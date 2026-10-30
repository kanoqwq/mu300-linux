"""Android-owned F2FS resize; the offline formatter remains a separate choice.

Geometry is in bytes/512-byte sectors at the host boundary, GPT logical blocks
only inside table edits. The kernel works through the mounted decrypted volume.
No userspace decryption, raw-offset Linux root, or forced BLKRRPART is used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import struct
import sys
import time

from . import gpt, storage
from .state import Pending, RecoveryRequired, atomic_json, sha
from .uninstall import android_volume, partition, table, with_table


class OnlineDecision(Exception):
    """No data was changed; an explicit new erase-data plan may be requested."""


def superblocks(raw):
    if len(raw) != 8192:
        raise ValueError('Incomplete F2FS superblocks / F2FS 超级块读取不完整')
    a, b = raw[1024:4096], raw[5120:8192]
    if a != b or a[:4] != bytes.fromhex('1020f5f2') or struct.unpack_from('<II', a, 16) != (12, 9):
        raise ValueError('F2FS superblocks disagree or are unsupported / F2FS 主备超级块不一致或格式不支持')
    sections = struct.unpack_from('<I', a, 24)[0]
    blocks = struct.unpack_from('<Q', a, 36)[0]
    uuid = a[108:124].hex()
    if not 0 < sections <= 65536 or not blocks or blocks % (sections*512) or uuid == '0'*32:
        raise ValueError('Invalid F2FS geometry / F2FS 几何无效')
    return dict(blocks=blocks, section_blocks=sections*512, uuid=uuid)


def inspect(adapter, device):
    """Read-only eligibility. A same-size ioctl is deferred until confirmation."""
    try:
        if device.get('encryption_type') == 'block':
            raise ValueError('Legacy block encryption is not verified for online resize / 旧式块加密尚未验证在线缩容')
        volume = android_volume(adapter.adb, device, allow_small=True)
        if volume['filesystem'] != 'f2fs' or volume['mapping_overhead']:
            raise ValueError('Online resize requires F2FS with a complete userdata mapping / 在线缩容要求 F2FS 且映射完整覆盖 userdata')
        node = volume['source'].rsplit('/',1)[-1]
        if node.startswith('dm-'):
            slaves = adapter.adb.shell(f'for p in /sys/class/block/{node}/slaves/*; do [ -e "$p" ] && echo "${{p##*/}}"; done; true',root=True).split()
            if slaves != [device['storage']['NODE']]:
                raise ValueError('Layered or offset encryption mapping is unsupported / 不支持多层或带偏移的加密映射')
        elif node != device['storage']['NODE']:
            raise ValueError('Android mount is not the selected userdata / Android 挂载源不是所选 userdata')
        geometry = superblocks(adapter.adb.read('dd if='+shlex.quote(volume['source'])+' bs=4096 count=2 2>/dev/null'))
        if geometry['uuid'] != volume['uuid']:
            raise ValueError('Filesystem identity changed while inspecting / 检查期间文件系统身份发生变化')
        return dict(available=True, volume=volume, **geometry)
    except (ValueError, RecoveryRequired) as error:
        return dict(available=False, reason=str(error))


def allocation(device, requested):
    info = device['online_shrink']
    if not info.get('available'):
        raise ValueError('Online resize is unavailable')
    s = device['storage']
    # Same MiB alignment as the existing offline/uninstall geometry contract.
    sectors = ((s['SIZE']-requested//512)//2048)*2048
    section = info['section_blocks']
    blocks = (sectors*512//4096//section)*section
    if (sectors*512 < 2 << 30 or blocks >= info['blocks'] or
            blocks*4096 <= device['userdata_bytes']-device['free_bytes']+(2 << 30)):
        raise ValueError('Insufficient free space for preserving Android / 保留 Android 数据的缩容空间不足')
    return dict(new_sectors=sectors, target_blocks=blocks,
                gap_start=s['START']+sectors, android_bytes=sectors*512)


def candidate(pair, sectors, node, old_size, new_size):
    info = gpt.gpt_space(*pair,sectors)
    data = partition(info,'userdata')
    disk = storage.disk_node(node)
    if (storage.part_node(disk,data['n']) != node or data['end']-data['start']+1 != old_size or
            new_size <= 0 or new_size >= old_size or new_size % 2048):
        raise RecoveryRequired('Userdata GPT geometry changed / userdata GPT 几何已变化')
    updated = bytearray(table(pair[0]))
    scale = info['sector_size']//512
    if (data['start']+new_size) % scale:
        raise ValueError('Target is not aligned to the logical block size')
    struct.pack_into('<Q',updated,(data['n']-1)*128+40,(data['start']+new_size)//scale-1)
    result = with_table(pair,updated,sectors)
    after = partition(gpt.gpt_space(*result,sectors),'userdata')
    if after['end']+1 != data['start']+new_size:
        raise ValueError('GPT candidate size differs')
    return result


def helper_files():
    if getattr(sys,'frozen',False):
        directory = Path(sys._MEIPASS)
        binary, metadata = directory/'f2fs-online-arm64', directory/'online-helper/build.json'
    else:
        directory = Path(__file__).resolve().parents[1]/'device'
        binary, metadata = directory/'f2fs-online-arm64', directory/'build.json'
    info = json.loads(metadata.read_text(encoding='utf-8'))
    if (info.get('protocol') != 1 or info.get('architecture') != 'aarch64-static-linux' or
            sha(binary) != info.get('binary') or (binary.read_bytes()[:7] != bytes.fromhex('7f454c46020101') or binary.read_bytes()[16:20] != bytes.fromhex('0200b700'))):
        raise ValueError('Online resize helper checksum/architecture differs / 在线缩容工具校验或架构不符')
    source = directory/'f2fs-online.c'
    if source.is_file() and hashlib.sha256(source.read_bytes().replace(b'\r\n',b'\n')).hexdigest() != info['source']:
        raise ValueError('Online resize helper is stale; rebuild installer/build-online-helper.py / 在线缩容工具已过期，需要重编')
    return binary, metadata


def values(text):
    return dict(line.split('=',1) for line in text.splitlines() if '=' in line)


class OnlineShrink:
    def __init__(self, adapter):
        self.a, self.ctx = adapter, adapter.ctx
        self.r = self.ctx.record
        self.directory = self.ctx.directory/'online-shrink'
        self.remote = '/dev/openwrt-online-'+self.r['id']
        self.tool = self.remote+'/f2fs-online'

    def pair(self):
        j = self.r['online_shrink']
        disk = '/dev/block/'+storage.disk_node(self.r['device']['storage']['NODE'])
        return (self.a.adb.read(f'dd if={disk} bs=512 count={j["front"]} 2>/dev/null'),
                self.a.adb.read(f'dd if={disk} bs=512 skip={j["tail"]} count={j["sectors"]-j["tail"]} 2>/dev/null'))

    def files(self):
        j = self.r['online_shrink']
        output = {}
        for name, digest in j['files'].items():
            path = self.directory/name
            if sha(path) != digest:
                raise RecoveryRequired('Online resize recovery file changed / 在线缩容恢复文件校验失败: '+name)
            if name != 'metadata.bin':
                output[name] = path.read_bytes()
        return output

    def installation_gpt(self, original):
        """Supply the verified post-resize baseline, retaining recovery originals."""
        j = self.r['online_shrink']
        if j.get('state') != 'verified':
            raise RecoveryRequired('Online resize is not verified / 在线缩容尚未校验完成')
        files = self.files()
        if original != (files['old-first'], files['old-last']):
            raise RecoveryRequired('Original GPT backup differs from the resize record / 原始 GPT 备份与缩容记录不符')
        s = self.r['device']['storage']
        data = partition(gpt.gpt_space(*original, s['DISK_SIZE']), 'userdata')
        updated = tuple(candidate(original, s['DISK_SIZE'], s['NODE'],
                                  data['end']-data['start']+1, s['SIZE']))
        if (j['sectors'] != s['DISK_SIZE'] or j['new_sectors'] != s['SIZE'] or
                self.r['plan'].get('shrink_strategy') != 'online-f2fs' or
                self.r['plan'].get('gap_start') != data['start']+s['SIZE'] or
                updated != (files['new-first'], files['new-last'])):
            raise RecoveryRequired('Installation GPT differs from the verified resize / 安装 GPT 与已校验缩容结果不符')
        return updated

    def prepare(self):
        d = self.r['device']
        actual = self.a.probe(d['serial'],'part')
        # Free bytes change normally; only identity and disk geometry bind the plan.
        if (any(actual.get(k) != d.get(k) for k in ('cid','slot','profile','boot_id','existing')) or
                any(actual['storage'].get(k) != d['storage'].get(k) for k in ('NODE','CID','START','SIZE','DISK_SIZE','SOURCE'))):
            raise RecoveryRequired('Device changed since online resize planning / 在线缩容计划后的设备身份或几何已变化')
        if any(n*512 >= self.r['plan']['openwrt_bytes'] for _,n in actual['gpt']['safe_gaps']):
            raise RecoveryRequired('Free-space layout changed; review a new plan before shrinking / 空闲区布局已变化，请重新生成计划，不再按旧计划缩容')
        actual['online_shrink'] = inspect(self.a,actual)
        if not actual['online_shrink']['available']:
            return self.decision(actual['online_shrink']['reason'], before_prepare=True)
        target = allocation(actual,self.r['plan']['openwrt_bytes'])
        if any(target.get(k) != self.r['plan'].get(k) for k in ('new_sectors','target_blocks','gap_start','android_bytes')):
            raise RecoveryRequired('Online resize target changed; review a new plan / 在线缩容目标发生变化，请重新生成计划')
        self.a.backup(d)
        self.ctx.boundary()
        self.directory.mkdir(parents=True,exist_ok=True)
        s = d['storage']; space = self.a.read_gpt(s)
        original = space['first'],space['last']
        updated = candidate(original,s['DISK_SIZE'],s['NODE'],s['SIZE'],target['new_sectors'])
        files = dict(zip(('old-first','old-last','new-first','new-last'),(*original,*updated)))
        for name,raw in files.items():
            with (self.directory/name).open('wb') as out:
                out.write(raw); out.flush(); os.fsync(out.fileno())
        # Metadata-encryption keys are a separate bounded recovery input. Never
        # back up userdata or a whole device for this operation.
        if actual['online_shrink']['volume']['source'].rsplit('/',1)[-1].startswith('dm-'):
            node = self.a.adb.shell('readlink -f /dev/block/by-name/metadata',root=True).strip().rsplit('/',1)[-1]
            if storage.disk_node(node) != storage.disk_node(s['NODE']):
                raise RecoveryRequired('Metadata partition is not on the selected disk / metadata 不在所选磁盘')
            try:
                self.a.adb.backup_partition('metadata',self.directory/'metadata.bin',self.ctx)
            finally:
                self.a.adb.cleanup_backup(self.ctx)
        self.r['online_shrink'] = dict(version=1,state='prepared',front=space['front'],tail=space['tail'],
            sectors=s['DISK_SIZE'],old_blocks=actual['online_shrink']['blocks'],
            uuid=actual['online_shrink']['uuid'],boot_id=d['boot_id'],original_plan=dict(self.r['plan']),
            files={p.name:sha(p) for p in self.directory.iterdir() if p.is_file()},**target)
        self.ctx.store.save(self.r)

    def stage(self):
        files = self.files()
        binary,_ = helper_files()
        self.a.adb.shell('umask 077; mkdir -p '+self.remote+'; chmod 700 '+self.remote,root=True)
        for name,local in [('f2fs-online',binary)]+[(n,self.directory/n) for n in files if n != 'metadata.bin']:
            if self.r['online_shrink']['state'] == 'prepared':
                self.ctx.boundary()
            remote = self.remote+'/'+name
            staged = '/data/local/tmp/online-upload-'+self.r['id']
            self.a.adb.call('push',str(local),staged,timeout=60)
            try:
                self.a.adb.shell(f'set -e; cp {staged} {remote}; chmod 700 {remote}; sync',root=True)
                if self.a.adb.shell('sha256sum '+remote,root=True).split()[0] != sha(local):
                    raise ValueError('Online resize transfer checksum failed / 在线缩容工具传输校验失败')
            finally:
                self.a.adb.shell('rm -f '+staged,root=True)

    def unchanged(self, actual=None):
        j = self.r['online_shrink']; files = self.files()
        actual = actual or self.a.probe(self.r['device']['serial'],'boot')
        info = inspect(self.a,actual)
        return (info.get('available') and info['uuid'] == j['uuid'] and info['blocks'] == j['old_blocks'] and
                self.pair() == (files['old-first'],files['old-last']))

    def decision(self, reason, before_prepare=False):
        if not before_prepare and not self.unchanged():
            raise RecoveryRequired('Resize changed disk state; erasing fallback is unavailable / 缩容已改变磁盘状态，不能直接转为清空流程: '+reason)
        self.r['unsafe'] = False
        self.r['online_fallback'] = dict(state='awaiting',nonce=secrets.token_urlsafe(24),reason=reason)
        self.ctx.stage('online_fallback','waiting_user')
        raise OnlineDecision()

    def prepare_reclaimed_gap(self, actual):
        """Erase stale headers only inside this transaction's proven F2FS tail.

        GPT-free does not imply blank: growing userdata over an old OpenWrt
        root and shrinking it again can leave its ext4 signature intact.
        Keep normal untracked-filesystem protection everywhere else.
        """
        j=self.r['online_shrink']; start=j['gap_start']
        end=start+self.r['plan']['openwrt_bytes']//512
        if any(actual.get(k)!=self.r['device'].get(k) for k in ('cid','slot','profile')):
            raise RecoveryRequired('Reclaimed range belongs to a different device / 回收区域设备身份不符')
        if not j.get('reclaimed_headers') and any(a<=start and end<=a+n for a,n in actual['gpt']['safe_gaps']):
            return actual
        files=self.files();s=self.r['device']['storage']
        before=(files['old-first'],files['old-last']);after=(files['new-first'],files['new-last'])
        if tuple(candidate(before,j['sectors'],s['NODE'],s['SIZE'],j['new_sectors']))!=after or self.pair()!=after:
            raise RecoveryRequired('Reclaimed range does not match the original userdata shrink / 回收范围与原 userdata 缩容记录不符')
        old=partition(gpt.gpt_space(*before,j['sectors']),'userdata')
        new=partition(gpt.gpt_space(*after,j['sectors']),'userdata')
        if (start!=new['end']+1 or end>old['end']+1 or
                actual['storage']['START']!=new['start'] or actual['storage']['SIZE']!=j['new_sectors'] or
                actual['boot_id']==j.get('commit_boot_id',j['boot_id'])):
            raise RecoveryRequired('Reclaimed range is not outside the rebooted Android mapping / 回收范围尚未脱离重启后的 Android 映射')
        gap=next(((a,n) for a,n in actual['gpt'].get('gaps',[]) if a<=start and end<=a+n),None)
        signatures={row['offset']:row for row in actual['gpt'].get('gap_signatures',[])
                    if gap and gap[0]<=row['offset']<gap[0]+gap[1]}
        for key,row in j.get('reclaimed_headers',{}).items():
            if not re.fullmatch('[0-9]+',key):raise RecoveryRequired('Invalid reclaimed header record')
            signatures.setdefault(int(key),{'offset':int(key),'kind':row['kind']})
        signatures=list(signatures.values())
        if not gap or not signatures or any(row['offset']%8 or not start<=row['offset'] or row['offset']+16>end for row in signatures):
            raise RecoveryRequired('Untracked filesystem lies outside the confirmed reclaimed range / 未登记文件系统不在已确认的缩容回收范围内，不会清理')
        folder=self.directory/'reclaimed-headers';folder.mkdir(exist_ok=True)
        disk='/dev/block/'+storage.disk_node(s['NODE'])
        ledger=j.setdefault('reclaimed_headers',{})
        empty=hashlib.sha256(bytes(8192)).hexdigest()
        for row in signatures:
            offset=row['offset'];key=str(offset);path=folder/(key+'.bin')
            raw=self.a.adb.read(f'dd if={disk} bs=512 skip={offset} count=16 2>/dev/null')
            if len(raw)!=8192:raise RecoveryRequired('Incomplete reclaimed header read / 回收区域文件系统头读取不完整')
            digest=hashlib.sha256(raw).hexdigest()
            if key in ledger:
                if not path.is_file() or sha(path)!=ledger[key]['sha256']:
                    raise RecoveryRequired('Reclaimed header backup changed / 回收区域文件系统头备份已变化')
                saved=path.read_bytes()
                # A disconnected zeroing write may have completed only some
                # sectors. Resume only bytes explained by this saved write.
                if len(saved)!=8192 or any(raw[n:n+512] not in (saved[n:n+512],bytes(512)) for n in range(0,8192,512)):
                    raise RecoveryRequired('Reclaimed header changed since backup / 回收区域文件系统头与备份不符')
            else:
                magic = raw[1080:1082] == b'\x53\xef' if row['kind']=='ext4' else raw[1024:1028] == bytes.fromhex('1020f5f2')
                if row['kind'] not in ('ext4','f2fs') or not magic:
                    raise RecoveryRequired('Reclaimed header changed after probing / 回收区域文件系统头在探测后已变化')
                with path.open('wb') as out:
                    out.write(raw);out.flush();os.fsync(out.fileno())
                ledger[key]={'sha256':digest,'kind':row['kind'],'bytes':8192}
                self.ctx.store.save(self.r)
            if digest==empty:continue
            checks=''.join('[ "$(dd if='+disk+' bs=512 skip='+str(at)+' count='+str(count)+
                          ' 2>/dev/null | sha256sum | cut -d\' \''+' -f1)" = '+hashlib.sha256(data).hexdigest()+' ]; '
                          for at,count,data in ((0,j['front'],after[0]),(j['tail'],j['sectors']-j['tail'],after[1])))
            script=('set -eu; set -o pipefail; [ "$(cat /proc/sys/kernel/random/boot_id)" = '+shlex.quote(actual['boot_id'])+' ]; '+
                    '[ "$(cat /sys/class/block/'+s['NODE']+'/size)" = '+str(j['new_sectors'])+' ]; '+checks+
                    '[ "$(dd if='+disk+' bs=512 skip='+key+' count=16 2>/dev/null | sha256sum | cut -d\' \''+' -f1)" = '+digest+' ]; '+
                    'dd if=/dev/zero of='+disk+' bs=4096 seek='+str(offset//8)+' count=2 conv=notrunc,fsync; '+
                    '[ "$(dd if='+disk+' bs=512 skip='+key+' count=16 2>/dev/null | sha256sum | cut -d\' \''+' -f1)" = '+empty+' ]')
            self.r['unsafe']=True;self.ctx.store.save(self.r)
            self.ctx.log('Preparing reclaimed '+row['kind']+' header at sector '+key+' / 清理已确认回收区的旧文件系统头',scope='online-shrink')
            self.a.adb.shell(script,root=True,timeout=None)
        verified=self.a.probe(self.r['device']['serial'],'part')
        if (any(verified.get(k)!=actual.get(k) for k in ('cid','slot','profile','boot_id')) or
                self.pair()!=after or not any(a<=start and end<=a+n for a,n in verified['gpt']['safe_gaps'])):
            raise RecoveryRequired('Reclaimed range did not pass final checks / 回收区域清理后的复核未通过')
        self.r['unsafe']=False;self.ctx.store.save(self.r)
        return verified

    def run(self):
        self.a.bind(self.r['device']['serial'])
        if 'online_shrink' not in self.r:
            self.prepare()
        j = self.r['online_shrink']
        if j['state'] == 'verified':
            return
        self.ctx.stage('online_shrink')
        # Identify hardware without parsing GPT first: a disconnected commit may
        # have written only one copy. Known old/new copy pairs are repaired by
        # the native worker; the general probe correctly rejects mixed GPT.
        actual = self.a.probe(self.r['device']['serial'],'boot')
        if any(actual.get(k) != self.r['device'].get(k) for k in ('cid','slot','profile')):
            raise RecoveryRequired('Online resize device identity changed / 在线缩容设备身份发生变化')
        original = self.r['device']['storage']; current = actual['storage']
        if (any(current.get(k) != original.get(k) for k in ('NODE','START','DISK_SIZE')) or
                current.get('SIZE') not in (original['SIZE'],j['new_sectors'])):
            raise RecoveryRequired('Online resize kernel partition geometry changed / 在线缩容的内核分区几何不符')
        if j['state'] == 'started':
            active = self.a.adb.shell(f'if [ -x {self.tool} ]; then {self.tool} lock-status; else echo busy=0; fi',root=True)
            if values(active).get('busy') == '1':
                return self.wait()
        files = self.files(); pair = self.pair()
        if any(raw not in (files['old-'+side],files['new-'+side]) for side,raw in zip(('first','last'),pair)):
            raise RecoveryRequired('GPT differs from the recorded resize / GPT 与已记录缩容不符')
        info = inspect(self.a,actual)
        if not info.get('available') or info['uuid'] != j['uuid']:
            raise RecoveryRequired('Online resize filesystem identity cannot be verified / 无法确认在线缩容文件系统身份')
        if pair == (files['new-first'],files['new-last']) and info['blocks'] == j['target_blocks']:
            if (actual['boot_id'] == j.get('commit_boot_id',j['boot_id']) or
                    actual['storage']['SIZE'] != j['new_sectors']):
                # A resumed commit may have happened in a later Android boot.
                # Wait against that boot, not the original planning boot UUID.
                j['commit_boot_id']=actual['boot_id']
                self.r['device']['boot_id']=actual['boot_id']
                j['state']='gpt-committed'; self.r['unsafe']=False; self.ctx.store.save(self.r)
                self.ctx.boundary()
                self.ctx.stage('android_boot','waiting_device')
                self.a.adb.call('reboot')
                raise Pending()
            if actual['storage']['SIZE'] != j['new_sectors'] or info['volume']['mapped_bytes'] != j['new_sectors']*512:
                raise RecoveryRequired('Android mapping did not adopt the resized partition / Android 未采用缩容后的映射容量')
            actual = self.a.probe(self.r['device']['serial'],'part')
            actual = self.prepare_reclaimed_gap(actual)
            if not any(start <= j['gap_start'] and start+n >= j['gap_start']+self.r['plan']['openwrt_bytes']//512
                       for start,n in actual['gpt']['safe_gaps']):
                raise RecoveryRequired('Reclaimed space is not free / 缩容回收范围不是可用空闲区')
            j['state']='verified'; self.r['device']=actual; self.r['unsafe']=False
            self.r['plan'].update(target='GPT unallocated space',two_stage=self.a.kind=='e5',offline=False)
            self.ctx.store.save(self.r)
            self.ctx.log('Online F2FS resize and Android reboot verified; continuing free-space installation',scope='online-shrink')
            self.ctx.boundary()
            return
        # If Android rebooted before GPT commit it may have grown F2FS back.
        # Only offer a new explicit plan after proving everything is unchanged.
        if actual['boot_id'] != j['boot_id'] and self.unchanged(actual):
            return self.decision('Android restarted before the resized GPT was committed / Android 在缩容 GPT 提交前重启，当前布局未改变')
        if info['blocks'] not in (j['old_blocks'],j['target_blocks']):
            raise RecoveryRequired('Unexpected intermediate F2FS capacity / F2FS 处于无法确认的中间容量')
        # A running detached worker must never have its inputs overwritten.
        busy = self.a.adb.shell(f'if [ -x {self.tool} ]; then {self.tool} lock-status; else echo busy=0; fi',root=True)
        if values(busy).get('busy') != '1':
            self.stage()
            if info['blocks'] == j['old_blocks']:
                try:
                    self.a.adb.shell(f'{self.tool} probe /data '+shlex.quote(info['volume']['source']),root=True,timeout=60)
                except ConnectionError as error:
                    return self.decision('Kernel online-resize probe failed / 内核在线缩容能力检查失败: '+str(error))
            if j['state'] == 'prepared':
                self.ctx.boundary()
            self.r['unsafe']=True; j['state']='started'; self.ctx.store.save(self.r); self.ctx.snapshot()
            disk = '/dev/block/'+storage.disk_node(actual['storage']['NODE'])
            command = [self.tool,'start','/data',info['volume']['source'],str(j['old_blocks']),str(j['target_blocks']),
                       disk,str(j['tail']*512),str(j['sectors']*512),j['uuid'],self.remote]
            try:
                self.a.adb.shell(shlex.join(command),root=True)
            except ConnectionError as error:
                try:
                    idle = values(self.a.adb.shell(self.tool+' lock-status',root=True)).get('busy') == '0'
                    unchanged = idle and self.unchanged()
                except (ConnectionError, OSError, ValueError):
                    unchanged = False
                if unchanged:
                    return self.decision('Online worker launch was rejected / 在线缩容任务未能启动: '+str(error))
                # ADB may have disconnected after fork. The saved recipe and
                # worker lock are authoritative; never start an erasing retry.
                raise RecoveryRequired('Online resize launch needs readback; reconnect and resume / 在线缩容启动结果待复核，请重连后继续') from None
        return self.wait()

    def wait(self):
        self.r['unsafe']=True; self.ctx.store.save(self.r)
        while True:
            result = values(self.a.adb.shell(f'cat {self.remote}/status 2>/dev/null; {self.tool} lock-status',root=True))
            phase = result.get('phase')
            self.ctx.log('Online F2FS phase: '+str(phase),scope='online-shrink')
            if phase == 'complete' and result.get('busy') == '0':
                # Readback in run() also checks both GPT copies and the UUID.
                self.r['unsafe']=False; self.ctx.store.save(self.r)
                return self.run()
            if result.get('busy') == '0':
                if phase in ('unchanged','failed'):
                    return self.decision('Online resize was rejected (errno '+result.get('errno','?')+') / 内核拒绝在线缩容，原容量已复核')
                raise RecoveryRequired('Online worker stopped; resume to verify filesystem/GPT / 在线缩容任务已停止，需继续复核文件系统及 GPT')
            # Cancellation is deferred until FS and GPT form a complete pair.
            time.sleep(2)


def fallback_plan(service, record, params):
    prompt = record.get('online_fallback',{})
    if (prompt.get('state') != 'awaiting' or params.get('confirmation') != 'yes' or
            not secrets.compare_digest(str(params.get('nonce','')),str(prompt.get('nonce','')))):
        raise ValueError('Confirm the erase-data fallback / 请确认清空 Android 的后备方案')
    adapter = service.adapter(record['package'],service.active if service.active and service.active.record['id']==record['id'] else None)
    actual = adapter.probe(record['device']['serial'],'part')
    if any(actual.get(k) != record['device'].get(k) for k in ('cid','slot','profile')):
        raise RecoveryRequired('Device changed before fallback / 后备方案前设备身份已变化')
    from .state import Context
    adapter.ctx = Context(service.store,record,service.emit)
    job = OnlineShrink(adapter)
    if 'online_shrink' in record and not job.unchanged(actual):
        raise RecoveryRequired('Cannot erase an unverified intermediate resize / 无法对未核验的缩容中间状态直接清空')
    choices = dict(record['choices'],wipe=True,shrink_strategy='format')
    from .service import plan_install
    record['plan'] = plan_install(record['package'],actual,choices)
    record['choices']=choices; record['device']=actual; record['unsafe']=False
    prompt['state']='confirmed'
    if 'online_shrink' in record: record['online_shrink']['state']='declined'
    record['error']=None
    adapter.ctx.stage('plan','ready')
    return service.public(record)
