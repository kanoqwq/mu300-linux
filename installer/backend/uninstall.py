"""Journaled removal of a verified internal OpenWrt partition.

Planning never changes GPT, boot or userdata. An owned, idle OpenWrt filesystem
may replay its journal before evidence is accepted. GPT changes run offline;
stock Android owns encrypted F2FS growth, and recovery owns explicit resets.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import subprocess
import time
import zlib
import uuid

from . import gpt, storage as storage_io
from .boot_image import inspect_linux_boot, trial_control
from .state import Cancelled, RecoveryRequired, atomic_json, sha
from .transport import push_chunks
from .uninstall_boot import build
from .uninstall_growth import (PreservationUnavailable, eligible, growth_tools, base_boot,
                               inspect_android_growth, preservation_eligible)
from .install_evidence import inspect_installation
from . import android_growth_boot


def partition(info, name):
    rows = [p for p in info['parts'] if p['name'] == name]
    if len(rows) != 1:
        raise RecoveryRequired('Ambiguous or missing partition / 分区缺失或存在重名: '+name)
    return rows[0]


def table(first):
    block = gpt.sector_size(first)
    at, count = struct.unpack_from('<QI', first, block+72)
    return first[at*block:at*block+count*128]


def with_table(current, updated, sectors):
    block = gpt.sector_size(current[0])
    output = []
    for raw, header, offset in ((current[0], block, struct.unpack_from('<Q', current[0], block+72)[0]*block),
                                (current[1], len(current[1])-block, 0)):
        raw = bytearray(raw); raw[offset:offset+len(updated)] = updated
        struct.pack_into('<I', raw, header+88, zlib.crc32(updated))
        struct.pack_into('<I', raw, header+16, 0)
        length = struct.unpack_from('<I', raw, header+12)[0]
        struct.pack_into('<I', raw, header+16, zlib.crc32(raw[header:header+length]))
        output.append(bytes(raw))
    gpt.gpt_space(*output,sectors)
    return output


def device_layout(current, sectors, name):
    """Remove only this root; extend userdata only into its contiguous extent.

    This is a new, bounded allocation, never a reconstruction of historical GPT.
    Unlisted space and any partition before/after the root remain untouched.
    """
    info = gpt.gpt_space(*current,sectors)
    data, root = partition(info,'userdata'), partition(info,name)
    updated = bytearray(table(current[0]))
    entry = updated[(root['n']-1)*128:root['n']*128]
    if (entry[:16] != uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le or
            entry[16:32] == bytes(16) or entry[48:56] != bytes(8) or
            root['start'] % 2048 or (root['end']+1) % 2048 or
            root['end']-root['start']+1 < 4194304):
        raise RecoveryRequired('Unsupported OpenWrt partition type, identity or alignment / OpenWrt 分区类型、身份或对齐方式不受支持')
    if any(updated[(p['n']-1)*128+16:(p['n']-1)*128+32] == entry[16:32]
           for p in info['parts'] if p['n'] != root['n']):
        raise RecoveryRequired('OpenWrt partition GUID is duplicated / OpenWrt 分区 GUID 存在重复')
    expands = root['start'] == data['end']+1
    target = {**data,'end':root['end']} if expands else data
    updated[(root['n']-1)*128:root['n']*128] = bytes(128)
    struct.pack_into('<Q',updated,(data['n']-1)*128+40,(target['end']+1)//(info['sector_size']//512)-1)
    return with_table(current,updated,sectors), target, root, expands


def restored_layout(original, current, sectors, name):
    """Reverse proven entry edits. The final bool means growth, not reset consent."""
    old = gpt.gpt_space(*original, sectors)
    now = gpt.gpt_space(*current, sectors)
    data, root = partition(now, 'userdata'), partition(now, name)
    prior = partition(old, 'userdata')
    oldtable, newtable = table(original[0]), table(current[0])
    if (original[0][old['sector_size']+56:old['sector_size']+72] != current[0][now['sector_size']+56:now['sector_size']+72] or old['front'] != now['front'] or
            old['tail'] != now['tail'] or len(oldtable) != len(newtable) or data['n'] != prior['n'] or
            data['start'] != prior['start'] or root['n']-1 not in old['empty']):
        raise RecoveryRequired('Original GPT identity differs / 原安装备份与当前 GPT 身份不符')
    for index in range(len(newtable)//128):
        a, b = oldtable[index*128:(index+1)*128], newtable[index*128:(index+1)*128]
        if index == data['n']-1:
            if a[:40]+a[48:] != b[:40]+b[48:]:
                raise RecoveryRequired('Userdata identity changed / userdata 身份发生变化')
        elif index != root['n']-1 and a != b:
            raise RecoveryRequired('Another partition changed; refusing rollback / 其他分区已变化，不能恢复原布局')
    wipe = prior['end'] > data['end']
    if wipe:
        expected_size = ((prior['end']-prior['start']+1-(root['end']-root['start']+1))//2048)*2048
        if (root['start'] != data['end']+1 or root['end'] > prior['end'] or
                data['end']-data['start']+1 != expected_size):
            raise RecoveryRequired('OpenWrt is not inside the proven reclaimed userdata range / OpenWrt 不在原缩容范围内')
    elif prior['end'] != data['end'] or not any(
            a <= root['start'] and root['end'] < a+n for a, n in old['gaps']):
        raise RecoveryRequired('Unallocated-space origin cannot be proven / 无法确认原未分配空间范围')
    updated = bytearray(newtable)
    updated[(root['n']-1)*128:root['n']*128] = oldtable[(root['n']-1)*128:root['n']*128]
    struct.pack_into('<Q', updated, (data['n']-1)*128+40, (prior['end']+1)//(now['sector_size']//512)-1)
    output = with_table(current,updated,sectors)
    verified = gpt.gpt_space(*output, sectors)
    if any(p['name'] == name for p in verified['parts']):
        raise RecoveryRequired('Root entry was not removed')
    return output, prior, root, wipe


def android_volume(adb, device, *, allow_small=False):
    source = adb.shell('readlink -f '+shlex.quote(device['storage']['SOURCE']), root=True).strip()
    if not re.fullmatch(r'/dev/(?:block/)?(?:mmcblk\d+p\d+|sd[a-z]+[1-9][0-9]*|dm-\d+)', source):
        raise RecoveryRequired('Cannot identify Android data mapping / 无法确认 Android data 映射设备')
    mapped = int(adb.shell('cat /sys/class/block/'+source.rsplit('/',1)[-1]+'/size', root=True).strip())*512
    raw = adb.read('dd if='+source+' bs=1024 skip=1 count=1 2>/dev/null')
    if len(raw) != 1024:
        raise RecoveryRequired('Incomplete Android filesystem superblock / Android 文件系统超级块读取不完整')
    if raw[56:58] == b'\x53\xef':
        from .service import verify_ext4_capacity
        size, kind = verify_ext4_capacity(raw, 0), 'ext4'
    elif raw[:4] == bytes.fromhex('1020f5f2'):
        shift = struct.unpack_from('<I', raw, 16)[0]
        if shift != 12:
            raise RecoveryRequired('Unsupported Android F2FS block size')
        size, kind = struct.unpack_from('<Q', raw, 36)[0]*(1 << shift), 'f2fs'
    else:
        raise RecoveryRequired('Android data is not a readable ext4/F2FS filesystem / 无法读取 Android data 的 ext4/F2FS 文件系统')
    if not 0 < size <= mapped <= device['userdata_bytes'] or not allow_small and mapped-size > 4 << 20:
        raise RecoveryRequired('Android filesystem/mapping capacity differs / Android 文件系统或映射容量不符: '
                               f'filesystem={size}, mapped={mapped}, userdata={device["userdata_bytes"]} bytes')
    uuid_at = 104 if kind == 'ext4' else 108
    return {'source': source, 'mapped_bytes': mapped, 'filesystem_bytes': size, 'filesystem': kind,
            'uuid':raw[uuid_at:uuid_at+16].hex(),
            'mapping_overhead': device['userdata_bytes']-mapped}


def uninstall_bcb(raw):
    """Keep BCB contents, except a superseded one-shot bootloader command.

    The user has confirmed an uninstall boot, not entry into the bootloader.
    Recovery/OTA requests remain protected. These are C strings: bytes after
    the terminating NUL are not a pending command.
    """
    if len(raw) != 2048:
        raise RecoveryRequired('Incomplete Android boot message / Android 启动消息读取不完整')
    def text(field):
        return b'' if field == b'\xff'*len(field) else field.split(b'\0',1)[0]
    command = text(raw[:32])
    if not command:
        return raw
    if command == b'bootonce-bootloader' and not text(raw[64:832]) and not text(raw[832:864]):
        return bytes(32)+raw[32:]
    label = command.decode('ascii','backslashreplace')
    raise RecoveryRequired('Another Android boot operation is pending / Android 另有待执行的启动操作: '+label)


def bcb_handoff(before, after):
    """Compare all 2048 bytes; at most clear the 32-byte command field."""
    if len(before) != 2048 or after != uninstall_bcb(before):
        raise RecoveryRequired('Invalid boot message handoff / 启动消息交接内容无效')
    old, new = (hashlib.sha256(value).hexdigest() for value in (before,after))
    read = 'dd if=/dev/block/by-name/misc bs=2048 count=1 2>/dev/null | sha256sum | cut -d" " -f1'
    guard = 'bcb_hash=$('+read+'); [ "$bcb_hash" = '+old+' ] || [ "$bcb_hash" = '+new+' ]; '
    update = guard
    if before != after:
        update += ('if [ "$bcb_hash" != '+new+' ]; then '
                   'dd if=/dev/zero of=/dev/block/by-name/misc bs=1 count=32 conv=notrunc,fsync; fi; ')
    update += '[ "$('+read+')" = '+new+' ]; '
    return guard, update


def prepare(adapter, store, device, authorize_reset=None):
    if device.get('readiness') != 'ready' or not device.get('existing'):
        raise RecoveryRequired('Boot rooted Android with an installed internal partition / 请先启动有 root 的 Android，并确认存在内置 OpenWrt 分区')
    storage = device['storage']; total = storage['DISK_SIZE']
    space = adapter.read_gpt(storage)
    current = (space['first'], space['last'])
    name = 'e5root' if adapter.kind == 'e5' else 'mu300root'
    matches = []
    for source in store.list():
        if (source['choices'].get('method') != 'part' or source['device'].get('cid') != device['cid'] or
                source['package'].get('adapter') != adapter.kind or source['device'].get('profile') != device['profile']):
            continue
        try:
            folder = store.directory(source['id'])/'backup'
            sums = json.loads((folder/'checksums.json').read_text())
            for filename in ('gpt-first.bin','gpt-last.bin','identity.json'):
                if not 0 < (folder/filename).stat().st_size <= 2 << 20 or sha(folder/filename) != sums[filename]:
                    raise ValueError('Damaged original backup')
            identity = json.loads((folder/'identity.json').read_text())
            if identity['cid'] != device['cid'] or identity['storage']['DISK_SIZE'] != total:
                raise ValueError('Original backup identity differs')
            original = ((folder/'gpt-first.bin').read_bytes(), (folder/'gpt-last.bin').read_bytes())
            after, data, root, wipe = restored_layout(original, current, total, name)
            if source['plan']['openwrt_bytes'] != (root['end']-root['start']+1)*512:
                raise ValueError('Installed root allocation differs')
            if not wipe and source['plan'].get('gap_start') != root['start']:
                raise ValueError('Recorded unallocated-space location differs')
            matches.append((source, after, data, root, wipe))
        except (OSError, ValueError, KeyError, RecoveryRequired):
            continue
    # Conflicting historical layouts must be reviewed rather than guessed.
    if matches and any(item[1] != matches[0][1] for item in matches[1:]):
        raise RecoveryRequired('Original records disagree about Android capacity / 原安装记录对 Android 容量的记载冲突')
    proof = None
    if matches:
        source, after, data, root, expands = matches[0]
    else:
        after, data, root, expands = device_layout(current,total,name)
        proof = inspect_installation(adapter,device,root,recovery_store=store)
        source = None
    node = storage_io.disk_node(storage['NODE'])
    if (storage['ROOT'] != storage_io.part_node(node,root['n']) or storage['START'] != data['start'] or
            storage['SIZE']*512 != device['userdata_bytes']):
        raise RecoveryRequired('Kernel partition nodes differ from GPT; reboot Android / 内核分区节点与 GPT 不符，请重启 Android')
    for bootname in ('boot_a','boot_b','misc'):
        partition(space, bootname)
    tools = {}; reason = ''; action = 'gap'
    try:
        volume = android_volume(adapter.adb, device)
    except RecoveryRequired as error:
        if not expands:
            raise
        volume = {'filesystem':'unknown','mapping_overhead':0,'filesystem_bytes':0}
        reason = str(error)
    if expands:
        try:
            if reason:
                raise ValueError(reason)
            if device.get('encrypted') is True and volume['filesystem'] == 'f2fs':
                android_growth_boot.capability(adapter.adb)
                android_growth_boot.identity_helper({'device':device},adapter)
                volume = inspect_android_growth(adapter,device,volume)
                action = 'grow-android-f2fs'
            else:
                eligible(device,volume)
                tools = growth_tools(adapter,volume['filesystem'])
                action = 'grow-'+volume['filesystem']
            build(base_boot(adapter),adapter.kind,tools)
        except (ConnectionError,TimeoutError,subprocess.TimeoutExpired):
            # A lost ADB connection cannot establish that preservation is impossible.
            raise
        except (ValueError,OSError,KeyError) as error:
            reason = str(error)
            evidence = {'cid':device['cid'],'slot':device['slot'],'profile':device['profile'],
                        'layout':[hashlib.sha256(raw).hexdigest() for raw in current],
                        'installation':proof,
                        'encryption':[device.get(k) for k in ('encrypted','encryption_state','encryption_type')],
                        'volume':volume,'reason':reason}
            fingerprint = hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
            if not authorize_reset or not authorize_reset(fingerprint):
                raise PreservationUnavailable(reason,fingerprint) from error
            action = 'reset'; tools = {}
    wipe = action == 'reset'
    plan = {'method':'uninstall','target':storage['ROOT'],'wipe':wipe,'two_stage':True,'offline':True,
            'data_action':action,'preservation_error':reason,
            'grow':False, 'openwrt_bytes':(root['end']-root['start']+1)*512,
            'android_bytes':(data['end']-data['start']+1)*512,
            'previous_android_bytes':device['userdata_bytes'],
            'reclaimed_bytes':(data['end']-data['start']+1)*512-device['userdata_bytes'],
            'source_transaction':source['id'] if source else None,
            'layout_source':'original_record' if source else 'device',
            'released_unallocated_bytes':0 if expands else (root['end']-root['start']+1)*512}
    metadata = {'version':2 if source else 3,'action':action,'reset_authorized':wipe,'total':total,'front':space['front'],'tail':space['tail'],
                'tool_hashes':{name:hashlib.sha256(content).hexdigest() for name,content in tools.items()},
                'root_start':root['start'],'root_size':root['end']-root['start']+1,
                'nodes':{p['name']:storage_io.part_node(node,p['n']) for p in space['parts']
                         if p['name'] in ('userdata',name,'misc','boot_a','boot_b')}, 'volume':volume}
    if proof:
        metadata['installation_evidence'] = proof
    files = dict(zip(('before-first','before-last','after-first','after-last'), (*current,*after)))
    return plan, metadata, files


def persist(context, metadata, files):
    folder = context.directory/'uninstall'; folder.mkdir(parents=True,exist_ok=True)
    for name, data in files.items():
        (folder/name).write_bytes(data)
    metadata['checksums'] = {name:sha(folder/name) for name in files}
    context.record['uninstall'] = metadata
    context.store.save(context.record)


class Uninstaller:
    def __init__(self, service, context, adapter):
        self.service, self.ctx, self.adapter = service, context, adapter
        self.r = context.record
        self.resume_growth = self.r.get('status') in ('failed','cancelled') and bool(self.r.get('uninstall_growth_pending'))
        self.meta = self.r['uninstall']
        self.folder = context.directory/'uninstall'
        self.files = {}
        for name, expected in self.meta['checksums'].items():
            if (name not in ('before-first','before-last','after-first','after-last') or
                    not 0 < (self.folder/name).stat().st_size <= 2 << 20 or sha(self.folder/name) != expected):
                raise RecoveryRequired('Uninstall snapshot is damaged / 卸载分区快照损坏')
            self.files[name] = (self.folder/name).read_bytes()
        self.validate_snapshots()

    def validate_snapshots(self):
        """Imported journals must prove the same limited edits, not just hashes."""
        required = {'before-first','before-last','after-first','after-last'}
        version = self.meta.get('version')
        if set(self.files) != required or version not in (1,2,3) or self.r['plan'].get('method') != 'uninstall':
            raise RecoveryRequired('Incomplete uninstall journal / 卸载记录不完整')
        storage = self.r['device']['storage']
        if (self.r['package'].get('adapter') not in ('e5','mu300') or
                not re.fullmatch(r'(?:mmcblk[0-9]+p|sd[a-z]+)[1-9][0-9]*',storage.get('NODE',''))):
            raise RecoveryRequired('Invalid uninstall disk node / 卸载记录中的磁盘节点无效')
        total = storage['DISK_SIZE']
        before = (self.files['before-first'],self.files['before-last'])
        after = (self.files['after-first'],self.files['after-last'])
        family = self.r['package']['adapter']
        name = 'e5root' if family == 'e5' else 'mu300root'
        if version == 3:
            calculated, prior, root, expands = device_layout(before,total,name)
            proof = self.meta.get('installation_evidence',{})
            paths = {'etc/e5/part-root','etc/openwrt_release'} if family == 'e5' else {
                'openwrt/etc/mu300/desktop-mode','.mu300/boot-os','openwrt/etc/openwrt_release'}
            hashes = [proof.get(key,'') for key in ('boot_header_sha256','ramdisk_sha256','superblock_sha256')]
            hashes += list(proof.get('markers',{}).values())
            if (self.r['plan'].get('layout_source') != 'device' or self.r['plan'].get('source_transaction') is not None or
                    proof.get('version') != 1 or set(proof.get('markers',{})) != paths or
                    any(not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value) for value in hashes) or
                    self.r['plan'].get('released_unallocated_bytes') != (0 if expands else (root['end']-root['start']+1)*512)):
                raise RecoveryRequired('Device installation evidence is incomplete / 设备端安装证据不完整')
        else:
            calculated, prior, root, expands = restored_layout(after,before,total,name)
        wipe = expands
        if version >= 2:
            action = self.meta.get('action')
            if action != self.r['plan'].get('data_action') or action not in ('gap','reset','grow-ext4','grow-f2fs','grow-android-f2fs'):
                raise RecoveryRequired('Uninstall data action differs / 卸载数据处理方式不一致')
            wipe = action == 'reset'
            if (action == 'gap') != (not expands) or self.meta.get('reset_authorized') is not wipe:
                raise RecoveryRequired('Uninstall reset was not explicitly authorized / 卸载重置未获得明确授权')
            if action.startswith('grow-'):
                preservation_eligible(self.r['device'],self.meta['volume'],action)
                if ((action != 'grow-android-f2fs' and
                     (action != 'grow-'+self.meta['volume']['filesystem'] or not self.meta.get('tool_hashes'))) or
                        (action == 'grow-android-f2fs' and self.meta.get('tool_hashes'))):
                    raise RecoveryRequired('Uninstall preservation evidence is incomplete / 卸载保数据证据不完整')
        info = gpt.gpt_space(*before,total)
        data = partition(info,'userdata')
        disk = storage_io.disk_node(storage['NODE'])
        expected_nodes = {label:storage_io.part_node(disk,partition(info,label)['n'])
                          for label in ('userdata',name,'misc','boot_a','boot_b')}
        if (calculated != list(after) or self.meta['total'] != total or self.meta['front'] != info['front'] or
                self.meta['tail'] != info['tail'] or self.meta['nodes'] != expected_nodes or
                self.meta['root_start'] != root['start'] or self.meta['root_size'] != root['end']-root['start']+1 or
                storage['NODE'] != expected_nodes['userdata'] or storage['START'] != data['start'] or
                storage['SIZE'] != data['end']-data['start']+1 or
                self.r['device']['userdata_bytes'] != storage['SIZE']*512 or
                self.r['plan']['previous_android_bytes'] != storage['SIZE']*512 or
                self.r['plan']['target'] != expected_nodes[name] or self.r['plan']['wipe'] is not wipe or
                self.r['plan']['android_bytes'] != (prior['end']-prior['start']+1)*512 or
                self.r['plan']['reclaimed_bytes'] != self.r['plan']['android_bytes']-storage['SIZE']*512 or
                self.r['plan']['openwrt_bytes'] != (root['end']-root['start']+1)*512):
            raise RecoveryRequired('Uninstall plan/snapshot geometry differs / 卸载计划与分区快照范围不符')

    def current(self):
        adb = self.adapter.adb
        disk = '/dev/block/'+storage_io.disk_node(self.r['device']['storage']['NODE'])
        front, tail, total = (self.meta[k] for k in ('front','tail','total'))
        return (adb.read(f'dd if={disk} bs=512 count={front} 2>/dev/null'),
                adb.read(f'dd if={disk} bs=512 skip={tail} count={total-tail} 2>/dev/null'))

    def layout_state(self):
        first, last = self.current()
        if (first,last) == (self.files['after-first'],self.files['after-last']):
            return 'after'
        if (first,last) == (self.files['before-first'],self.files['before-last']):
            return 'before'
        raise RecoveryRequired('GPT is changed or partially written. Keep backups; offline recovery is required. / GPT 已变化或写入不完整，请保留备份并进行离线恢复；不会自动重跑卸载。')

    def ready(self):
        device = self.adapter.probe(self.r['device']['serial'], 'uninstall')
        if device.get('readiness') == 'ready' and any(
                device.get(k) != self.r['device'].get(k) for k in ('cid','profile','slot')):
            raise RecoveryRequired('Uninstall device or Android slot changed / 卸载设备身份或 Android 槽位发生变化')
        return device

    def arm(self, device):
        from .adapter import debug_output
        adb = self.adapter.adb
        if self.meta['version'] == 3:
            info = gpt.gpt_space(self.files['before-first'],self.files['before-last'],self.meta['total'])
            root = partition(info,'e5root' if self.adapter.kind == 'e5' else 'mu300root')
            original_boot = None
            if self.r.get('uninstall_retry_linux_sha256'):
                other = 'b' if device['slot'] == '_a' else 'a'
                path = self.ctx.directory/'backup'/('boot_'+other+'.bin')
                sums = json.loads((path.parent/'checksums.json').read_text())
                if path.stat().st_size != 64 << 20 or sha(path) != sums.get(path.name):
                    raise RecoveryRequired('Original Linux boot backup is damaged / 原 Linux 启动备份已损坏')
                original_boot = path.read_bytes()
            if inspect_installation(self.adapter,device,root,boot_backup=original_boot) != self.meta['installation_evidence']:
                raise RecoveryRequired('Installed OpenWrt changed; create a new removal plan / 设备上的 OpenWrt 已变化，请重新生成卸载计划')
        tools = {}
        if self.meta.get('action','').startswith('grow-'):
            volume = android_volume(adb,device)
            if self.meta['action'] == 'grow-android-f2fs':
                volume = inspect_android_growth(self.adapter,device,volume)
            preservation_eligible(device,volume,self.meta['action'])
            if any(volume.get(k) != self.meta['volume'].get(k) for k in ('uuid','source','filesystem','filesystem_bytes')):
                raise RecoveryRequired('Android data changed; create a new removal plan / Android data 已变化，请重新生成卸载计划')
            if self.meta['action'] == 'grow-android-f2fs':
                if volume['android_growth'] != self.meta['volume']['android_growth']:
                    raise RecoveryRequired('Stock Android resize tools changed; review removal plan / 原厂 Android 扩容程序已变化，请重新核对卸载计划')
            else:
                tools = growth_tools(self.adapter,volume['filesystem'])
            if {name:hashlib.sha256(content).hexdigest() for name,content in tools.items()} != self.meta['tool_hashes']:
                raise RecoveryRequired('Preservation tools changed; review removal plan / 保数据工具已变化，需重新核对卸载计划')
        slot = device['slot'].removeprefix('_'); other = 'b' if slot == 'a' else 'a'
        with debug_output(self.ctx):
            if not self.r.get('uninstall_retry_linux_sha256'):
                inspect_linux_boot(adb.read, int(adb.shell('blockdev --getsize64 /dev/block/by-name/boot_'+other,root=True)), self.adapter.kind, other)
            self.adapter.backup(device)
        self.ctx.stage('uninstall_prepare')
        if self.layout_state() != 'before':
            raise RecoveryRequired('Layout changed during backup')
        backup = self.ctx.directory/'backup'
        before = (backup/'misc.bin').read_bytes()[:4096]
        if len(before) != 4096:
            raise RecoveryRequired('Incomplete misc backup / misc 备份不完整')
        before_bcb = uninstall_bcb(before[:2048])
        bcb_guard, bcb_update = bcb_handoff(before[:2048],before_bcb)
        control = before[2048:2080]
        trial = trial_control(control, slot)
        android = bytearray(control); android[:4] = ('_'+slot).encode()+b'\0\0'
        android[12], android[14] = (0x9f,0x1e) if slot == 'a' else (0x1e,0x9f)
        struct.pack_into('<I',android,28,zlib.crc32(android[:28]))
        bcb = bytearray(2048); bcb[:13] = b'boot-recovery'
        message = b'recovery\n--wipe_data\n--reason=openwrt-uninstall\n'
        bcb[64:64+len(message)] = message
        boot_bytes = (backup/('boot_'+slot+'.bin')).stat().st_size
        android_sha = sha(backup/('boot_'+slot+'.bin'))
        if boot_bytes != 64 << 20 or (backup/('boot_'+other+'.bin')).stat().st_size != boot_bytes:
            raise RecoveryRequired('Unsupported boot partition sizes')
        with (backup/('boot_'+slot+'.bin')).open('rb') as stream:
            android_header = stream.read(4096)
        with (backup/('boot_'+other+'.bin')).open('rb') as stream:
            linux_header = stream.read(4096)
        if android_header[:8] != b'ANDROID!' or android_header == linux_header:
            raise RecoveryRequired('Current Android boot cannot be distinguished from Linux / 无法区分当前 Android 与 Linux 启动镜像')
        nodes = self.meta['nodes']; rootname = 'e5root' if self.adapter.kind == 'e5' else 'mu300root'
        request = [device['cid'],storage_io.disk_node(device['storage']['NODE']),nodes['userdata'],nodes[rootname],
                   nodes['misc'],nodes['boot_'+slot],nodes['boot_'+other]]
        request += [self.meta[k] for k in ('total','front','tail','root_start','root_size')]
        request += [int(self.r['plan']['wipe']),boot_bytes,android_sha]
        if self.meta['version'] >= 2:
            volume = self.meta['volume']
            request += [self.meta['action'],device['storage']['START'],self.r['plan']['android_bytes']//512,
                        volume['filesystem_bytes'],volume.get('uuid','0'*32)]
        files = {'openwrt-uninstall/'+name:content for name,content in self.files.items()}
        files.update(tools)
        files['openwrt-uninstall/storage.sh'] = storage_io.library(self.adapter.root).encode()
        files.update({'openwrt-uninstall/request':(':'.join(map(str,request))+'\n').encode(),
                      'openwrt-uninstall/before-bcb':before_bcb,
                      'openwrt-uninstall/recovery-bcb':bytes(bcb) if self.r['plan']['wipe'] else bytes(2048),
                      'openwrt-uninstall/android-bc':bytes(android)})
        files['openwrt-uninstall/SHA256SUMS'] = ''.join(hashlib.sha256(content).hexdigest()+'  '+name.removeprefix('openwrt-uninstall/')+'\n'
            for name,content in files.items()).encode()
        if self.adapter.kind == 'e5':
            base = (self.adapter.root/'files/boot.img').read_bytes()
        else:
            base = self.adapter.legacy.payload_file(self.adapter.root,'boot-linux.img').read_bytes()
        image = build(base,self.adapter.kind,files)
        path = self.folder/'boot-uninstall.img'; path.write_bytes(image)
        (self.folder/'trial.bin').write_bytes(trial)
        remote = '/data/local/tmp/openwrt-uninstall-'+self.r['id']
        self.r['uninstall_remote'] = remote
        self.r['uninstall_boot_sha256'] = sha(path)
        self.r['uninstall_android_sha256'] = android_sha
        self.r['uninstall_trial_sha256'] = hashlib.sha256(trial).hexdigest()
        self.ctx.store.save(self.r)
        adb.shell('umask 077; mkdir -p '+remote+'; chown 2000:2000 '+remote+'; chmod 700 '+remote,root=True)
        for local, name in ((path,'boot.img'),(self.folder/'trial.bin','trial.bin')):
            push_chunks(adb.call,lambda script:adb.shell(script,root=True),local,remote+'/'+name,self.ctx)
        bootdev = '/dev/block/by-name/boot_'+other
        disk = '/dev/block/'+storage_io.disk_node(device['storage']['NODE'])
        layout_checks = (
            f'[ "$(dd if={disk} bs=512 count={self.meta["front"]} | sha256sum | cut -d" " -f1)" = {hashlib.sha256(self.files["before-first"]).hexdigest()} ]; '
            f'[ "$(dd if={disk} bs=512 skip={self.meta["tail"]} count={self.meta["total"]-self.meta["tail"]} | sha256sum | cut -d" " -f1)" = {hashlib.sha256(self.files["before-last"]).hexdigest()} ]; ')
        script = ('set -eu; set -o pipefail; '
                  '[ "$(getprop ro.boot.slot_suffix)" = "_'+slot+'" ]; '
                  + layout_checks +
                  bcb_guard +
                  '[ "$(sha256sum /dev/block/by-name/boot_'+slot+' | cut -d" " -f1)" = '+android_sha+' ]; '
                  '[ "$(sha256sum '+bootdev+' | cut -d" " -f1)" = '+self.r.get('uninstall_retry_linux_sha256',sha(backup/('boot_'+other+'.bin')))+' ]; '
                  'dd if='+bootdev+' of='+remote+'/before-boot.img bs=1048576; '
                  'dd if=/dev/block/by-name/misc of='+remote+'/before-bc bs=1 skip=2048 count=32; '
                  '[ "$(sha256sum '+remote+'/before-bc | cut -d" " -f1)" = '+hashlib.sha256(control).hexdigest()+' ]; '
                  'dd if='+remote+'/boot.img of='+bootdev+' bs=1048576 conv=fsync; '
                  '[ "$(sha256sum '+bootdev+' | cut -d" " -f1)" = '+sha(path)+' ]; '
                  + bcb_update +
                  'if dd if='+remote+'/trial.bin of=/dev/block/by-name/misc bs=1 seek=2048 count=32 conv=notrunc,fsync && '
                  '[ "$(dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 | sha256sum | cut -d" " -f1)" = '+hashlib.sha256(trial).hexdigest()+' ]; then :; '
                  'else dd if='+remote+'/before-bc of=/dev/block/by-name/misc bs=1 seek=2048 count=32 conv=notrunc,fsync; exit 1; fi')
        self.ctx.stage('uninstall_prepare')
        def commit_boot():
            # Cancellation is deferred by Context.effect. Do not terminate a
            # boot write because a host-side command deadline expired.
            adb.shell(script,root=True,timeout=None)
            self.r['uninstall_armed'] = True
            self.ctx.store.save(self.r)
        self.ctx.effect('uninstall-arm',commit_boot)
        self.ctx.stage('uninstall_offline','waiting_device',reboot_manually=False)
        try:
            adb.call('reboot')
        except (ConnectionError,OSError,subprocess.TimeoutExpired):
            self.ctx.log('Expected ADB loss after the verified offline uninstall request',scope='uninstall')

    def run(self):
        armed = self.r['effects'].get('uninstall-arm')
        if armed and armed['state'] != 'done':
            armed = self.recover_arm()
        if not armed:
            device = self.ready()
            if device.get('readiness') != 'ready':
                raise RecoveryRequired('Android/root is not ready; no uninstall request written / Android 或 root 尚未就绪，未写入卸载请求')
            if self.layout_state() != 'before':
                raise RecoveryRequired('Unconfirmed layout changed; create a new plan')
            self.arm(device)
        else:
            self.r['uninstall_armed'] = True
        while True:
            self.ctx.boundary()
            try:
                device = self.ready()
                readiness = device.get('readiness','android_boot')
                if readiness != 'ready':
                    self.ctx.stage(readiness,'waiting_device' if readiness == 'android_boot' else 'waiting_user')
                elif device.get('boot_id') == self.r.get('uninstall_wait_boot_id',self.r['device'].get('boot_id')):
                    self.ctx.stage('uninstall_offline','waiting_user',reboot_manually=True)
                elif self.layout_state() == 'before':
                    raise RecoveryRequired('Android returned before offline removal completed. Keep backups and export diagnostics. / Android 已返回但离线卸载未完成，请保留备份并导出诊断。')
                else:
                    break
            except (ConnectionError,OSError,TimeoutError,subprocess.TimeoutExpired):
                self.ctx.stage('uninstall_offline','waiting_device',reboot_manually=False)
            if self.service.stop.wait(3):
                raise Cancelled()
        # No reboot is expected during final checks/boot cleanup. A command or
        # write failure here must be reported, rather than hidden in a wait loop.
        if self.r.get('uninstall_android_growth_attempted'):
            if android_growth_boot.restore(self,device):
                self.r['uninstall_wait_boot_id']=device['boot_id'];self.ctx.store.save(self.r)
                self.ctx.stage('uninstall_offline','waiting_device',reboot_manually=False)
                try:
                    self.adapter.adb.call('reboot')
                except (ConnectionError,OSError,TimeoutError,subprocess.TimeoutExpired):
                    self.ctx.log('Expected ADB loss returning from Android growth maintenance',scope='uninstall')
                return self.run()
        if self.resume_growth:
            self.resume_growth = False
            if self.rearm_growth(device):
                return self.run()
        if (self.meta.get('action') == 'grow-android-f2fs' and
                not self.r.get('uninstall_android_growth_attempted')):
            volume = android_volume(self.adapter.adb,device,allow_small=True)
            if volume['mapped_bytes']-volume['filesystem_bytes'] > 4 << 20:
                volume = inspect_android_growth(self.adapter,device,volume)
                preservation_eligible(device,volume,self.meta['action'])
                if volume['uuid'] != self.meta['volume']['uuid']:
                    raise RecoveryRequired('Android filesystem identity changed / Android 文件系统身份已变化')
                android_growth_boot.stage(self,device,volume)
                return self.run()
        return self.finish(device)

    def rearm_growth(self, device):
        """Explicit retry re-arms only the same pending worker through misc."""
        if not self.meta.get('action','').startswith('grow-') or self.layout_state() != 'after':
            raise RecoveryRequired('No verified pending growth / 没有可复核的待完成扩容')
        adb = self.adapter.adb
        volume = android_volume(adb,device,allow_small=True)
        if self.meta['action'] == 'grow-android-f2fs':
            volume = inspect_android_growth(self.adapter,device,volume)
        preservation_eligible(device,volume,self.meta['action'])
        if volume['uuid'] != self.meta['volume']['uuid']:
            raise RecoveryRequired('Android filesystem identity changed / Android 文件系统身份已变化')
        if volume['filesystem_bytes'] >= self.r['plan']['android_bytes']-(4 << 20):
            self.r['uninstall_growth_pending']=False;self.ctx.store.save(self.r)
            return False
        if self.meta['action'] == 'grow-android-f2fs':
            android_growth_boot.stage(self,device,volume)
            return True
        slot = device['slot'].removeprefix('_'); other = 'b' if slot == 'a' else 'a'
        values = adb.shell('sha256sum /dev/block/by-name/boot_'+slot+' /dev/block/by-name/boot_'+other,root=True).splitlines()
        expected = (self.r.get('uninstall_android_sha256'),self.r.get('uninstall_boot_sha256'))
        if len(values) != 2 or tuple(row.split()[0] for row in values) != expected:
            raise RecoveryRequired('Pending growth boot identity differs / 待完成扩容的启动镜像身份不符')
        current = adb.read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')
        effect = self.r['effects'].get('uninstall-grow-rearm')
        saved_trial = bytes.fromhex(self.r.get('uninstall_growth_trial',''))
        if effect and (effect['state'] != 'done' or current == saved_trial):
            before = bytes.fromhex(self.r['uninstall_growth_before_bc'])
            trial = bytes.fromhex(self.r['uninstall_growth_trial'])
            if current == trial:
                effect.update(state='done',result='verified by readback');self.r['unsafe']=False
            elif current != before:
                raise RecoveryRequired('Unconfirmed growth boot control; keep backups / 扩容启动控制写入尚未确认，请保留备份恢复')
            else:
                self.r['effects'].pop('uninstall-grow-rearm');self.r['unsafe']=False
                self.ctx.store.save(self.r)
        else:
            before = current;trial = trial_control(before,slot)
            self.r['uninstall_growth_before_bc']=before.hex();self.r['uninstall_growth_trial']=trial.hex()
            self.r['effects'].pop('uninstall-grow-rearm',None)
            self.ctx.store.save(self.r)
        if current != trial:
            remote = '/data/local/tmp/openwrt-uninstall-'+self.r['id']+'-grow'
            adb.shell('umask 077; mkdir -p '+remote+'; chown 2000:2000 '+remote+'; chmod 700 '+remote,root=True)
            for filename,content in (('before-bc',before),('trial-bc',trial)):
                local=self.folder/('grow-'+filename);local.write_bytes(content)
                push_chunks(adb.call,lambda script:adb.shell(script,root=True),local,remote+'/'+filename,self.ctx)
            wanted=hashlib.sha256(trial).hexdigest();original=hashlib.sha256(before).hexdigest()
            script = ('set -eu; set -o pipefail; [ "$(getprop ro.boot.slot_suffix)" = _'+slot+' ]; '
                      '[ "$(sha256sum /dev/block/by-name/boot_'+slot+' | cut -d" " -f1)" = '+expected[0]+' ]; '
                      '[ "$(sha256sum /dev/block/by-name/boot_'+other+' | cut -d" " -f1)" = '+expected[1]+' ]; '
                      '[ "$(dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 | sha256sum | cut -d" " -f1)" = '+original+' ]; '
                      'if dd if='+remote+'/trial-bc of=/dev/block/by-name/misc bs=1 seek=2048 count=32 conv=notrunc,fsync && '
                      '[ "$(dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 | sha256sum | cut -d" " -f1)" = '+wanted+' ]; then :; '
                      'else dd if='+remote+'/before-bc of=/dev/block/by-name/misc bs=1 seek=2048 count=32 conv=notrunc,fsync; exit 1; fi')
            self.ctx.effect('uninstall-grow-rearm',lambda:adb.shell(script,root=True,timeout=None))
        self.r['uninstall_wait_boot_id']=device['boot_id']
        self.ctx.stage('uninstall_offline','waiting_device',reboot_manually=False)
        try:
            adb.call('reboot')
        except (ConnectionError,OSError,subprocess.TimeoutExpired):
            self.ctx.log('Expected reconnect after resuming verified offline growth',scope='uninstall')
        return True

    def recover_arm(self):
        """Recover boot staging by exact readback; never replay a completed GPT edit."""
        if (not re.fullmatch('[a-f0-9]{64}',self.r.get('uninstall_boot_sha256','')) or
                not (self.folder/'trial.bin').is_file() or not (self.ctx.directory/'backup/misc.bin').is_file()):
            raise RecoveryRequired('Uninstall staging was interrupted without sufficient readback evidence / 卸载准备中断且回读证据不足，请保留备份恢复')
        while True:
            self.ctx.boundary()
            try:
                device = self.ready()
                readiness = device.get('readiness','android_boot')
                if readiness == 'ready':
                    break
                self.ctx.stage(readiness,'waiting_device' if readiness == 'android_boot' else 'waiting_user')
            except (ConnectionError,OSError,TimeoutError,subprocess.TimeoutExpired):
                self.ctx.stage('android_boot','waiting_device')
            if self.service is None:
                raise RecoveryRequired('Reconnect rooted Android to inspect the interrupted boot write / 请连接有 root 的 Android，复核中断的启动写入')
            if self.service.stop.wait(3):
                raise Cancelled()
        state = self.layout_state()
        if state == 'before':
            adb = self.adapter.adb
            slot = device['slot'].removeprefix('_'); other = 'b' if slot == 'a' else 'a'
            original = (self.ctx.directory/'backup/misc.bin').read_bytes()[2048:2080]
            trial = (self.folder/'trial.bin').read_bytes()
            checksums = json.loads((self.ctx.directory/'backup/checksums.json').read_text())
            if (sha(self.ctx.directory/'backup/misc.bin') != checksums.get('misc.bin') or
                    hashlib.sha256(trial).hexdigest() != self.r.get('uninstall_trial_sha256')):
                raise RecoveryRequired('Interrupted uninstall backups are damaged / 中断卸载的备份损坏')
            bc = adb.read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')
            active = adb.shell('sha256sum /dev/block/by-name/boot_'+slot,root=True).split()[0]
            target = adb.shell('sha256sum /dev/block/by-name/boot_'+other,root=True).split()[0]
            if active != self.r.get('uninstall_android_sha256') or not re.fullmatch('[a-f0-9]{64}',target):
                raise RecoveryRequired('Android boot identity changed during interrupted uninstall / 中断卸载期间 Android 启动身份已变化')
            if bc == original:
                # Explicit resume, unchanged GPT/misc/Android: only the inactive
                # boot write is retried. No partition or recovery request ran.
                self.r['uninstall_retry_linux_sha256'] = target
                self.r['effects'].pop('uninstall-arm')
                self.r['unsafe'] = False
                self.ctx.store.save(self.r)
                self.ctx.log('Verified original GPT, misc and Android boot; retrying inactive boot staging only',scope='recovery')
                return None
            if bc != trial or target != self.r.get('uninstall_boot_sha256'):
                raise RecoveryRequired('Interrupted boot/misc readback is not recognized / 中断的 boot/misc 回读不符合已保存记录，请保留备份恢复')
        self.r['effects']['uninstall-arm'] = {'state':'done','time':time.time(),'result':'verified by recovery readback'}
        if 'uninstall-arm' not in self.r['completed']:
            self.r['completed'].append('uninstall-arm')
        self.r['unsafe'] = False
        self.r['uninstall_armed'] = True
        self.ctx.store.save(self.r)
        return self.r['effects']['uninstall-arm']

    def finish(self, device):
        self.ctx.stage('verify_android')
        if device.get('existing') or device['userdata_bytes'] != self.r['plan']['android_bytes']:
            raise RecoveryRequired('Android partition capacity was not restored / Android 分区容量未恢复')
        preserving = self.meta.get('action','').startswith('grow-')
        volume = android_volume(self.adapter.adb,device,allow_small=preserving)
        expected = device['userdata_bytes']-self.meta['volume']['mapping_overhead']
        self.r['android_capacity_check'] = {key:value for key,value in volume.items() if key != 'uuid'}
        self.r['android_capacity_check']['expected_mapped_bytes'] = expected
        self.ctx.log(json.dumps(self.r['android_capacity_check']),scope='capacity')
        if preserving:
            if self.meta['action'] == 'grow-android-f2fs':
                volume = inspect_android_growth(self.adapter,device,volume)
            preservation_eligible(device,volume,self.meta['action'])
            if volume['uuid'] != self.meta['volume']['uuid']:
                raise RecoveryRequired('Preserved Android filesystem identity differs / 保留的 Android 文件系统身份不符')
            if volume['mapped_bytes']-volume['filesystem_bytes'] > 4 << 20:
                self.r['uninstall_growth_pending']=True;self.ctx.store.save(self.r)
                if self.meta['action'] == 'grow-android-f2fs' and self.r.get('uninstall_android_growth_attempted'):
                    details = android_growth_boot.report(self)
                    raise RecoveryRequired('Android pre-mount F2FS growth did not complete; data was not formatted. / Android 挂载前 F2FS 扩容未完成，未格式化数据。请查看扩容日志: '+details[-3000:])
                raise RecoveryRequired('Offline data growth is incomplete. Retry resumes checks/growth only; it never formats. / 离线 data 扩容尚未完成。重试仅继续检查与扩容，不会格式化。')
        if abs(volume['mapped_bytes']-expected) > 4 << 20 or not expected-(4 << 20) <= volume['filesystem_bytes'] <= expected:
            raise RecoveryRequired('Android filesystem has not reached the restored allocation / Android 文件系统尚未达到恢复后的分配容量: '
                                   f'{volume["filesystem_bytes"]} != {expected} bytes')
        # The request must have been consumed by stock recovery, not just a GPT
        # enlargement followed by a boot of the previous smaller filesystem.
        adb = self.adapter.adb
        bcb = adb.read('dd if=/dev/block/by-name/misc bs=2048 count=1 2>/dev/null')
        uninstall_bcb(bcb)
        self.finish_boot(device,bcb=bcb)
        self.r['uninstall_growth_pending']=False
        remote = '/data/local/tmp/openwrt-uninstall-'+self.r['id']
        try:
            android_growth_boot.cleanup(self)
            # Fixed filenames in this transaction's own staging directories.
            # Never recursively remove userdata paths or other installations.
            adb.shell('rm -f '+remote+'/boot.img '+remote+'/trial.bin '+remote+'/before-boot.img '+remote+'/before-bc '+
                      remote+'-finish/android-bc '+remote+'-grow/before-bc '+remote+'-grow/trial-bc; rmdir '+
                      remote+' '+remote+'-finish '+remote+'-grow 2>/dev/null || true',root=True)
        except (OSError,ConnectionError,TimeoutError) as error:
            self.ctx.log('Uninstall verified; temporary staging cleanup can be retried: '+str(error),level='warning',scope='cleanup')
        self.r['result'] = {'uninstalled':True,'filesystem_bytes':volume['filesystem_bytes'],
                            'android_bytes':device['userdata_bytes'],'reclaimed_bytes':self.r['plan']['reclaimed_bytes'],
                            'released_bytes':self.r['plan']['openwrt_bytes'],
                            'backup_directory':str(self.ctx.directory/'backup'),'url':'','temporary':False}
        self.ctx.stage('complete','completed')

    def finish_boot(self, device, *, bcb=None):
        """Verify/finish only inactive boot and default selection after Android is back."""
        adb = self.adapter.adb
        slot = device['slot'].removeprefix('_'); other = 'b' if slot == 'a' else 'a'
        bc = adb.read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')
        if (len(bc) != 32 or bc[4:8] != b'BCAB' or bc[8] != 1 or bc[9]&7 != 2 or
                zlib.crc32(bc[:28]) != struct.unpack_from('<I',bc,28)[0] or
                not bc[12 if slot == 'a' else 14]&0x80 or bc[13]&1 or bc[15]&1):
            raise RecoveryRequired('Android boot control is not healthy / Android 启动控制尚未就绪')
        desired = bytearray(bc); desired[:4] = ('_'+slot).encode()+b'\0\0'
        current_index,other_index = (12,14) if slot == 'a' else (14,12)
        if (bc[other_index]&15 > bc[current_index]&15 and
                ((bc[other_index]>>4)&7 > 1 or bc[other_index]&0x80)):
            raise RecoveryRequired('Another Android slot update is pending / 另一 Android 槽位存在待执行更新，请先处理')
        desired[12],desired[14] = (0x9f,0x1e) if slot == 'a' else (0x1e,0x9f)
        struct.pack_into('<I',desired,28,zlib.crc32(desired[:28]))
        values = adb.shell('sha256sum /dev/block/by-name/boot_'+slot+' /dev/block/by-name/boot_'+other,root=True).splitlines()
        if len(values) != 2 or any(not re.fullmatch('[a-f0-9]{64}',row.split()[0]) for row in values):
            raise RecoveryRequired('Cannot verify restored Android boot')
        source_hash, target_hash = (row.split()[0] for row in values)
        if source_hash != self.r.get('uninstall_android_sha256'):
            # Restoring Magisk after a reset can legitimately change Android's
            # boot. Back up that working image before copying it to the other slot.
            post_reset = self.ctx.directory/'backup/boot-android-after-reset.bin'
            adb.backup_partition('boot_'+slot,post_reset,self.ctx)
            checksums = json.loads((self.ctx.directory/'backup/checksums.json').read_text())
            checksums[post_reset.name] = sha(post_reset)
            atomic_json(self.ctx.directory/'backup/checksums.json',checksums)
        normalized_bcb = uninstall_bcb(bcb) if bcb is not None else None
        if source_hash == target_hash and bc == desired and bcb == normalized_bcb:
            entry = self.r['effects'].get('uninstall-final-boot')
            if entry:
                entry.update(state='done',result='verified by readback')
                self.r['unsafe'] = False
                self.ctx.store.save(self.r)
            return
        self.ctx.stage('uninstall_restore_boot')
        remote = '/data/local/tmp/openwrt-uninstall-'+self.r['id']+'-finish'
        adb.shell('umask 077; mkdir -p '+remote+'; chown 2000:2000 '+remote+'; chmod 700 '+remote,root=True)
        local = self.folder/'android-final-bc'; local.write_bytes(desired)
        push_chunks(adb.call,lambda script:adb.shell(script,root=True),local,remote+'/android-bc',self.ctx)
        if self.r['effects'].get('uninstall-final-boot'):
            # The restored GPT/FS and current Android were verified above.
            # Repeating a copy to its inactive slot is safe; no reset is armed.
            self.r['effects'].pop('uninstall-final-boot')
            self.ctx.log('Retrying verified inactive Android boot restoration; no partition/reset replay',scope='recovery')
        guard, update = bcb_handoff(bcb,normalized_bcb) if bcb is not None else ('','')
        script = ('set -eu; set -o pipefail; [ "$(getprop ro.boot.slot_suffix)" = "_'+slot+'" ]; '
                  + guard +
                  '[ "$(sha256sum /dev/block/by-name/boot_'+slot+' | cut -d" " -f1)" = '+source_hash+' ]; '
                  'dd if=/dev/block/by-name/boot_'+slot+' of=/dev/block/by-name/boot_'+other+' bs=1048576 conv=fsync; '
                  '[ "$(sha256sum /dev/block/by-name/boot_'+other+' | cut -d" " -f1)" = '+source_hash+' ]; '
                  + update +
                  'dd if='+remote+'/android-bc of=/dev/block/by-name/misc bs=1 seek=2048 count=32 conv=notrunc,fsync; '
                  '[ "$(dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 | sha256sum | cut -d" " -f1)" = '+hashlib.sha256(desired).hexdigest()+' ]')
        self.ctx.effect('uninstall-final-boot',lambda:adb.shell(script,root=True,timeout=None))
