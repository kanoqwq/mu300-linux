#!/usr/bin/env python3
"""MU300 desktop installer. The ZIP's original Magisk deployment is independent."""
import argparse
from contextlib import contextmanager
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid
import zlib
GUI = None  # Optional structured observer for the shared Tauri backend.

class Outcome:
    def __init__(self,state,message,next_step=''):
        self.state=state;self.message=message;self.next_step=next_step

class InstallerCancelled(Exception):
    pass

def display_outcome(result,elapsed):
    labels={'success':'SUCCESS / 成功','pending':'NEXT STEP REQUIRED / 仍需下一步',
            'checked':'CHECK PASSED / 校验通过','failure':'FAILED / 失败','cancelled':'CANCELLED / 已取消'}
    print('\n'+'='*56)
    print('['+labels[result.state]+'] '+result.message)
    if result.next_step:print('Next / 下一步: '+result.next_step)
    print(f'Elapsed / 总耗时: {int(elapsed)} s',flush=True)

def run_cli(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    started=time.monotonic();code=0;result=None
    try:
        result=main(argv)
        if not isinstance(result,Outcome):raise RuntimeError('Operation returned without a completion status')
    except (InstallerCancelled,KeyboardInterrupt):
        result=Outcome('cancelled','Operation cancelled / 操作已取消。');code=130
    except Exception as exc:
        result=Outcome('failure',str(exc).strip() or type(exc).__name__);code=1
    except SystemExit as exc:
        code=exc.code if isinstance(exc.code,int) else 1
        if code:result=Outcome('failure','Invalid arguments / 命令行参数无效。')
    if result is not None:display_outcome(result,time.monotonic()-started)
    interactive=sys.stdin is not None and sys.stdin.isatty()
    if '--no-pause' not in argv and ('--pause' in argv or interactive):
        try:input('\nPress Enter to exit / 按回车退出… ')
        except (EOFError,KeyboardInterrupt):pass
    return code

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()

def payload_file(root,name):
    # Legacy previews had the Magisk payload at the top level.
    metadata=Path(root)/'desktop/manifest.json'
    layout=json.loads(metadata.read_text()).get('magisk_layout',{}) if metadata.is_file() else {}
    return Path(root)/layout.get(name,name)

def public_rootfs(source,target):
    # One shared rootfs in the ZIP. Desktop paths filter this on the computer,
    # so installation/update never uses the build machine's modem runtime.
    start=last=time.monotonic()
    with tarfile.open(source,'r:gz') as src, tarfile.open(target,'w:gz',compresslevel=6) as dst:
        for member in src:
            name=PurePosixPath(member.name.removeprefix('./'))
            if name.is_absolute() or '..' in name.parts:raise ValueError('unsafe rootfs path')
            n=str(name)
            if n=='opt/mu300/android' or n.startswith('opt/mu300/android/'):continue
            if n.startswith('lib/firmware/') and not (n.startswith('lib/firmware/rtl_nic/') or n in ('lib/firmware/regulatory.db','lib/firmware/regulatory.db.p7s')):continue
            dst.addfile(member,src.extractfile(member) if member.isfile() else None)
            now=time.monotonic()
            if now-last>=2:
                print(f'  Preparing shared rootfs / 正在准备共享系统包… {int(now-start)} s',flush=True)
                last=now

@contextmanager
def payload_rootfs(root,tf=False):
    source=payload_file(root,'mu300-openwrt.tar.gz')
    if tf:
        yield source
        return
    print('Preparing shared rootfs / 准备共享系统包，剔除构建机固件…',flush=True)
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='mu300 rootfs ') as temp:
        target=Path(temp)/'rootfs.tar.gz'
        public_rootfs(source,target)
        print(f'  Rootfs ready / 系统包准备完成: {time.monotonic()-start:.1f} s',flush=True)
        yield target

def package_root():
    here=Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve().parent
    return here.parent if here.name=='desktop' else here

def module(root):
    spec=importlib.util.spec_from_file_location('desktop_boot_image',root/'desktop/boot_image.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def verify(root):
    m=json.loads((root/'desktop/manifest.json').read_text())
    for n,h in {**m['magisk'],**m['desktop']}.items():
        p=(root/n).resolve()
        if not p.is_relative_to(root.resolve()) or not p.is_file() or digest(p)!=h:
            raise ValueError('Package checksum failed: '+n)
    return m

def confirm(message):
    if GUI:
        GUI.ctx.boundary()
        return
    print('\n'+message)
    if input('Type yes to continue / 输入 yes 继续: ').strip()!='yes': raise InstallerCancelled()

def image_size(text, maximum):
    try: size=float(text)
    except ValueError: raise ValueError('Image size must be a number in GiB') from None
    if not (2<=size<=maximum/1024): raise ValueError(f'Choose 2–{maximum/1024:.2f} GiB')
    return int(size*1024)//4*4

def allocation(before, part_mib):
    need=part_mib*2048
    target=(before-need)//2048*2048
    if target<4194304 or need<4194304: raise ValueError('At least 2 GiB is required for each system')
    return target,need

def gpt_backup_layout(primary, backup, sectors):
    def header(raw,at):
        if len(raw)!=512 or raw[:8]!=b'EFI PART':raise ValueError('Invalid GPT header')
        size=struct.unpack_from('<I',raw,12)[0]
        if not 92<=size<=512:raise ValueError('Invalid GPT header size')
        check=bytearray(raw[:size]);expected=struct.unpack_from('<I',check,16)[0];check[16:20]=bytes(4)
        if zlib.crc32(check)!=expected:raise ValueError('GPT header checksum failed')
        current,alternate=struct.unpack_from('<QQ',raw,24)
        table,count,entrysize=struct.unpack_from('<QII',raw,72)
        if current!=at or count<1 or count>1024 or entrysize!=128:raise ValueError('Unsupported GPT geometry')
        return alternate,table,(count*entrysize+511)//512,raw[56:72],raw[80:92]
    a=header(primary,1);b=header(backup,sectors-1)
    if a[0]!=sectors-1 or b[0]!=1 or a[3:]!=b[3:]:raise ValueError('GPT copies disagree')
    front=a[1]+a[2];tail=b[1]
    if not (2<=a[1]<front<=4096 and sectors-4096<=tail and tail+b[2]==sectors-1):raise ValueError('GPT backup range is invalid')
    return front,tail

def gpt_space(first,last,sectors):
    """Validate both on-disk GPT copies and return aligned, genuinely unallocated ranges."""
    primary=first[512:1024];backup=last[-512:]
    front,tail=gpt_backup_layout(primary,backup,sectors)
    if len(first)!=front*512 or len(last)!=(sectors-tail)*512:
        raise ValueError('Incomplete GPT snapshot')
    start,end=struct.unpack_from('<QQ',primary,40)
    if (start,end)!=struct.unpack_from('<QQ',backup,40) or not front<=start<=end<tail:
        raise ValueError('GPT usable bounds disagree or overlap metadata')
    table,count,entry=struct.unpack_from('<QII',primary,72)
    data=first[table*512:table*512+count*entry]
    if data!=last[:count*entry] or zlib.crc32(data)!=struct.unpack_from('<I',primary,88)[0]:
        raise ValueError('GPT table checksum/copy mismatch')
    parts=[];empty=[]
    for i in range(count):
        raw=data[i*entry:(i+1)*entry]
        if raw[:16]==bytes(16):empty.append(i);continue
        a,b=struct.unpack_from('<QQ',raw,32)
        name=raw[56:128].decode('utf-16-le').split('\0',1)[0]
        if not start<=a<=b<=end:raise ValueError('GPT partition outside usable bounds: '+name)
        parts.append({'n':i+1,'start':a,'end':b,'name':name})
    at=start;gaps=[]
    for part in sorted(parts,key=lambda p:p['start'])+[{'start':end+1,'end':end}]:
        if part['start']<at:raise ValueError('Overlapping GPT partitions')
        aligned=(at+2047)//2048*2048
        limit=(part['start']//2048)*2048
        if limit>aligned:gaps.append((aligned,limit-aligned))
        at=part['end']+1
    return {'front':front,'tail':tail,'parts':parts,'empty':empty,'gaps':gaps,
            'largest':max((size for _,size in gaps),default=0) if empty else 0}

def gpt_add_root(first,last,sectors,offset,need):
    info=gpt_space(first,last,sectors)
    if any(p['name']=='mu300root' for p in info['parts']):raise ValueError('mu300root already exists')
    if not info['empty'] or need<4194304 or offset%2048 or need%2048 or not any(
            offset>=a and offset+need<=a+n for a,n in info['gaps']):
        raise ValueError('Requested root is not in an aligned GPT free range')
    idx=info['empty'][0];entry=bytearray(128)
    entry[:16]=uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le
    entry[16:32]=uuid.uuid4().bytes_le
    struct.pack_into('<QQ',entry,32,offset,offset+need-1)
    name='mu300root'.encode('utf-16-le');entry[56:56+len(name)]=name
    copies=[]
    for raw,header_offset,table_offset in ((first,512,struct.unpack_from('<Q',first,584)[0]*512),(last,len(last)-512,0)):
        new=bytearray(raw);new[table_offset+idx*128:table_offset+(idx+1)*128]=entry
        count=struct.unpack_from('<I',new,header_offset+80)[0]
        struct.pack_into('<I',new,header_offset+88,zlib.crc32(new[table_offset:table_offset+count*128]))
        struct.pack_into('<I',new,header_offset+16,0)
        hsize=struct.unpack_from('<I',new,header_offset+12)[0]
        struct.pack_into('<I',new,header_offset+16,zlib.crc32(new[header_offset:header_offset+hsize]))
        copies.append(bytes(new))
    gpt_space(*copies,sectors)
    return copies

def free_root_range(info,need):
    if not info['empty']:return None
    # Old MU300 installs may live outside GPT at this fixed raw offset. Such
    # an area is retained even though a partition table calls it unallocated.
    gaps=info.get('safe_gaps',info['gaps'])
    candidates=[(n,a) for a,n in gaps if n>=need]
    return max(candidates)[1] if candidates else None

def encrypted(properties, source, raw_magic):
    # FBE can have a perfectly readable filesystem superblock. A filesystem
    # signature alone never grants permission to preserve encrypted files.
    if properties.get('state')=='encrypted' or properties.get('type') in ('file','block') or '/dm-' in source or '/mapper/' in source:
        return True
    if properties.get('state')=='unencrypted' and raw_magic in ('1020f5f2','53ef'): return False
    raise ValueError('Encryption state is unknown; refusing a preserving shrink')

def android_profile(model,platform=''):
    names=[s.strip().upper() for s in model.splitlines()]
    if 'MU3351' in names or 'V50' in names:
        if 'MU3351' not in names or 'V50' not in names or platform.strip()!='ums9620':
            raise ValueError('V50 requires product MU3351 and the UMS9620 platform')
        return 'v50'
    if re.search(r'U30.?Air',model,re.I):return 'u30air'
    if re.search(r'MU300|F50',model,re.I):return 'f50'
    raise ValueError('Not a supported MU300/F50/V50/U30Air: '+model)

def env(values):
    return ''.join(k+'='+shlex.quote(str(v))+'\n' for k,v in values.items())

def settings(args, image_mib, mode, kernel):
    ssid='' if GUI else input('Hotspot name / 热点名称 [import Android]: ').strip()
    key=input('Hotspot key / 热点密码 (visible / 明文输入): ') if ssid else ''
    if ssid and (len(ssid.encode())>32 or '\n' in ssid or '\r' in ssid): raise ValueError('SSID must be 1–32 UTF-8 bytes')
    if ssid and (not 8<=len(key)<=63 or any(ord(c)<32 or ord(c)>126 for c in key)): raise ValueError('Wi-Fi key must be 8–63 ASCII characters')
    apn='' if GUI else input('APN [SIM default / SIM 默认]: ').strip()
    if apn and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,99}',apn):raise ValueError('Invalid APN')
    return env({'IMAGE_MIB':image_mib,'DESKTOP_MODE':mode,'KERNEL':kernel,'SSID':ssid,'WIFI_KEY':key,'APN':apn,'PWHASH':'','GROW_FS':int(getattr(args,'grow_fs',True))})

class Android:
    def __init__(self,args):
        self.args=args
        bundled=package_root()/('tools/adb.exe' if sys.platform=='win32' else
                                'tools/macos/adb' if sys.platform=='darwin' else 'tools/adb')
        self.adb=args.adb or (str(bundled) if bundled.is_file() else shutil.which('adb')) or str(bundled)
        listing=self.call('devices','-l').decode(errors='replace')
        serials=[l.split()[0] for l in listing.splitlines() if len(l.split())>=2 and l.split()[1]=='device']
        if args.serial: self.serial=args.serial
        elif len(serials)==1: self.serial=serials[0]
        elif len(serials)>1 and sys.stdin.isatty() and not GUI:
            for index,serial in enumerate(serials,1):print(f'  {index}. Device / 设备 ••••{serial[-4:]}')
            selected=input('Select device number / 选择设备编号: ').strip()
            if not selected.isdigit() or not 1<=int(selected)<=len(serials):raise ValueError('Invalid device number; use --serial SERIAL')
            self.serial=serials[int(selected)-1]
        else: raise ValueError('Specify --serial; expected one online Android device: '+repr(serials))
        if self.serial not in serials: raise ValueError('Android is not online: '+self.serial)
        if sys.platform=='win32' and not GUI:
            helper=package_root()/'desktop/windows_mtp.py'
            if helper.is_file():
                spec=importlib.util.spec_from_file_location('_mu300_windows_mtp',helper)
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                entries=[]
                for line in listing.splitlines():
                    values=line.split()
                    if len(values)>=2 and values[0]==self.serial:
                        entries.append({'serial':self.serial,'wireless':':' in self.serial,
                                        'label':next((v[6:] for v in values[2:] if v.startswith('model:')),'')})
                module.prepare_devices(entries)
        if self.shell('id -u',root=False).strip()=='0': self.su=False
        else:
            self.su=True
            if self.shell('id -u').strip()!='0': raise ValueError('Grant the Android su permission and retry')
        model=self.shell('getprop ro.product.model; getprop ro.product.device')
        self.profile=android_profile(model,self.shell('getprop ro.board.platform').strip())
        self.model=model.strip();self.slot=self.shell('getprop ro.boot.slot_suffix').strip().removeprefix('_')
        if self.slot not in ('a','b'): raise ValueError('Cannot identify Android slot')
        self.linux='b' if self.slot=='a' else 'a'
        self.p='/data/local/tmp/mu300-desktop-'+secrets.token_hex(6)
        self.t=self.p+'/work';self.bb=self.p+'/busybox'
        if not getattr(args,'probe',False):
            self.shell('mkdir -p '+self.t+'; chown 2000:2000 '+self.p+' '+self.t+'; chmod 700 '+self.p)

    def call(self,*args,timeout=1800,serial=None):
        cmd=[self.adb]
        selected=serial if serial is not None else getattr(self,'serial',None)
        if selected:cmd+=['-s',selected]
        proc=subprocess.Popen(cmd+list(args),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        start=time.monotonic()
        while True:
            try: out,err=proc.communicate(timeout=min(10,timeout));break
            except subprocess.TimeoutExpired:
                if time.monotonic()-start>timeout:
                    proc.kill();proc.communicate();raise TimeoutError('ADB operation timed out; do not retry a storage write blindly')
                print(f'  Working / 正在执行… {int(time.monotonic()-start)} s',flush=True)
        if proc.returncode:
            message=(err.decode(errors='replace')+'\n'+out.decode(errors='replace')).strip()
            raise RuntimeError(message or f'ADB {args[0]} failed, exit {proc.returncode}; check the device connection')
        return out

    def reboot(self):
        if GUI:GUI.reboot()
        # The ADB reboot service acknowledges the request before shutting down;
        # a root shell may disconnect before it can return an exit status.
        try:self.call('reboot',timeout=30)
        except (RuntimeError,ConnectionError,EOFError):
            if not GUI:raise
        print('Reboot request sent / 已发送重启请求。')

    def shell(self,script,root=True,timeout=1800):
        # adb shell has an additional remote shell layer; quote its entire
        # command. No host shell is used and user input never becomes code.
        cmd='su -c '+shlex.quote(script) if root and self.su else 'sh -c '+shlex.quote(script)
        text=self.call('shell',cmd,timeout=timeout).decode('utf-8',errors='replace').replace('\r','')
        return text

    def push(self,path,name):
        remote=self.p+'/'+name
        parent=shlex.quote(str(Path(remote).parent).replace('\\','/'))
        # adb push runs as Android shell even when su is available. Private
        # staging belongs to shell; root remains able to consume it.
        self.shell('mkdir -p '+parent+'; chown 2000:2000 '+parent+'; chmod 700 '+parent)
        if GUI:
            GUI.push(self.call,self.shell,path,remote)
            return remote
        self.call('push',str(path),remote)
        want=digest(path)
        got=self.shell('sha256sum '+shlex.quote(remote)).split()[0]
        if got!=want:raise ValueError('Upload checksum failed: '+name)
        return remote

    def put(self,data,name):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'upload';p.write_bytes(data.encode() if isinstance(data,str) else data)
            return self.push(p,name)

    def download(self,command,destination,expected_size=None):
        helper=package_root()/'desktop/binary_io.py'
        spec=importlib.util.spec_from_file_location('desktop_binary_io',helper)
        binary=importlib.util.module_from_spec(spec);spec.loader.exec_module(binary)
        return binary.pull_binary(self.call,self.shell,command,destination,expected_size)

    def read(self,command):
        with tempfile.TemporaryDirectory() as temp:
            local=Path(temp)/'read.bin'
            self.download(command,local)
            return local.read_bytes()

    def run(self,body,effect=None):
        if GUI and effect:
            GUI.stage('transfer_install')
            return GUI.action(effect,lambda:self.run(body))
        script='#!/system/bin/sh\nset -eu\nP='+self.p+'\nT='+self.t+'\nBB='+self.bb+'\nexport MU300_PAYLOAD_DIR=$P MU300_INSTALL_TMP=$T MU300_BUSYBOX=$BB MU300_KEEP_PAYLOAD=1\nmkdir -p "$T/bin"\n"$BB" --install -s "$T/bin"\nexport PATH="$T/bin:$PATH"\n'+body+'\n'
        p=self.put(script,'run.sh')
        result=self.shell(self.bb+' nsenter -t 1 -m '+self.bb+' sh '+p)
        print(result,end='');return result

    def storage(self):
        out=self.shell('set -e; d=$(readlink -f /dev/block/by-name/userdata); n=${d##*/}; b=${n%p*}; '+
            'echo NODE=$n; echo CID=$(cat /sys/class/block/$b/device/cid); echo START=$(cat /sys/class/block/$n/start); '+
            'echo SIZE=$(cat /sys/class/block/$n/size); echo DISK_SIZE=$(cat /sys/class/block/$b/size); '+
            'echo FREE=$(df -Pk /data | tail -n1 | awk \'{print $4}\'); '+
            'echo STATE=$(getprop ro.crypto.state); echo TYPE=$(getprop ro.crypto.type); '+
            'echo SOURCE=$(awk \'$2=="/data" {print $1;exit}\' /proc/mounts); '+
            'echo MAGIC=$(dd if="$d" bs=1 skip=1024 count=4 2>/dev/null | od -An -tx1 | tr -d " \\n"); '+
            'echo EXT=$(dd if="$d" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d " \\n"); '+
            'for u in /sys/class/block/${b}p*/uevent; do if grep -qx PARTNAME=mu300root "$u"; then n=${u%/uevent}; echo ROOT=${n##*/}; fi; done')
        result=dict(l.split('=',1) for l in out.splitlines() if '=' in l)
        if not re.fullmatch(r'mmcblk\d+p\d+',result['NODE']) or not re.fullmatch('[a-f0-9]{32}',result['CID']):raise ValueError('Invalid eMMC identity')
        for n in ('START','SIZE','DISK_SIZE','FREE'):result[n]=int(result[n])
        return result

    def gpt(self,s):
        disk='/dev/block/'+s['NODE'].split('p')[0]
        primary=self.read(f'dd if={disk} bs=512 skip=1 count=1 2>/dev/null')
        backup=self.read(f'dd if={disk} bs=512 skip={s["DISK_SIZE"]-1} count=1 2>/dev/null')
        front,tail=gpt_backup_layout(primary,backup,s['DISK_SIZE'])
        first=self.read(f'dd if={disk} bs=512 count={front} 2>/dev/null')
        last=self.read(f'dd if={disk} bs=512 skip={tail} count={s["DISK_SIZE"]-tail} 2>/dev/null')
        info=gpt_space(first,last,s['DISK_SIZE'])
        userdata=[p for p in info['parts'] if p['name']=='userdata']
        if len(userdata)!=1 or (userdata[0]['start'],userdata[0]['end']-userdata[0]['start']+1)!=(s['START'],s['SIZE']):
            raise ValueError('GPT userdata differs from the running kernel; reboot Android first')
        end=max(p['end'] for p in info['parts'])
        candidates={27762098176//512,(end//4096+1)*4096}
        candidates.update(a for a,_ in info['gaps'])
        blocked=[]
        for offset in sorted(candidates):
            for a,n in info['gaps']:
                if a<=offset and offset+4<=a+n:
                    raw=self.read(f'dd if={disk} bs=512 skip={offset} count=4 2>/dev/null')
                    if len(raw)!=2048:raise ValueError('Incomplete free-space signature read')
                    if raw[1080:1082]==b'\x53\xef' or raw[1024:1028]==bytes.fromhex('1020f5f2'):
                        blocked.append((a,n))
        info['safe_gaps']=[gap for gap in info['gaps'] if gap not in blocked]
        info['largest']=max((n for _,n in info['safe_gaps']),default=0) if info['empty'] else 0
        info['first']=first;info['last']=last
        return info

    def prepare_payload(self,root,tf=False,with_rootfs=True):
        names=['busybox','android-bootchain.sh','bootchain.sh','switch.sh','mu300-vendor-from-device.sh','rootfs-critical.sha256','rootfs.requirements']
        if tf:names+=['android-install.sh','tf-install-worker.sh','tf-storage.sh','tf-stage.sh','payload.sha256']
        for n in names:
            source=root/'desktop'/n if n in ('android-bootchain.sh','bootchain.sh') else payload_file(root,n)
            self.push(source,n)
        if with_rootfs:
            with payload_rootfs(root,tf=tf) as archive:
                self.push(archive,'mu300-openwrt.tar.gz')
        self.shell('chmod 755 '+self.bb+' '+self.p+'/*.sh')
        if not tf:
            for n in ('android-image.sh','android-write-part.sh','android-write-free.sh','android-loop.sh'):self.push(root/'desktop'/n,n)
            self.push(root/'desktop/files/boot/ext4-geometry.sh','ext4-geometry.sh')
            for p in (root/'desktop/files/boot/repair').rglob('*'):
                if p.is_file():self.push(p,p.relative_to(root).as_posix())
            self.shell('chmod -R u+rx '+self.p+'/desktop/files/boot/repair')
        self.run('"$BB" sh "$P/android-bootchain.sh" check')

    def live(self):
        data=self.read('dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null')
        if len(data)!=32:raise ValueError('Incomplete misc read')
        return data

    def backup(self,path,s):
        path.mkdir(parents=True,exist_ok=False)
        path.joinpath('identity.json').write_text(json.dumps({**s,'serial':self.serial,'android_slot':self.slot},indent=2)+'\n')
        disk='/dev/block/'+s['NODE'].split('p')[0]
        primary=self.read(f'dd if={disk} bs=512 skip=1 count=1 2>/dev/null')
        alternate=self.read(f'dd if={disk} bs=512 skip={s["DISK_SIZE"]-1} count=1 2>/dev/null')
        front,tail=gpt_backup_layout(primary,alternate,s['DISK_SIZE'])
        items={'misc-head.bin':'dd if=/dev/block/by-name/misc bs=4096 count=1',
               'gpt-first.bin':f'dd if={disk} bs=512 count={front}',
               'gpt-last.bin':f'dd if={disk} bs=512 skip={tail} count={s["DISK_SIZE"]-tail}'}
        for n in ('boot','uboot','sml','teecfg','trustos','vbmeta'):
            for slot in ('a','b'):items[f'{n}_{slot}.img']=f'cat /dev/block/by-name/{n}_{slot}'
        for n,cmd in items.items():
            print('Backing up / 备份:',n)
            if n=='misc-head.bin':size=4096
            elif n=='gpt-first.bin':size=front*512
            elif n=='gpt-last.bin':size=(s['DISK_SIZE']-tail)*512
            else:size=int(self.shell('blockdev --getsize64 /dev/block/by-name/'+n.removesuffix('.img')).strip())
            self.download(cmd+' 2>/dev/null',path/n,expected_size=size)
        first=path.joinpath('gpt-first.bin').read_bytes();last=path.joinpath('gpt-last.bin').read_bytes()
        if len(first)!=front*512 or len(last)!=(s['DISK_SIZE']-tail)*512:raise ValueError('Incomplete GPT backup')
        table,count,entry=struct.unpack_from('<QII',primary,72)
        primary_table=first[table*512:table*512+count*entry]
        backup_table=last[:count*entry]
        if primary_table!=backup_table or zlib.crc32(primary_table)!=struct.unpack_from('<I',primary,88)[0]:raise ValueError('GPT table checksum/copy mismatch')
        path.joinpath('SHA256SUMS').write_text(''.join(digest(path/n)+'  '+n+'\n' for n in items))
        path.joinpath('gpt-layout.json').write_text(json.dumps({'front_sectors':front,'tail_start':tail,'disk_sectors':s['DISK_SIZE']},indent=2)+'\n')

    def flash(self,image,bc):
        if GUI:return GUI.action('boot-and-misc-'+hashlib.sha256(image).hexdigest()[:16],lambda:self._flash(image,bc))
        return self._flash(image,bc)

    def _flash(self,image,bc):
        self.put(image,'boot-desktop.img');self.put(bc,'misc-linux.bin')
        self.run('''[ "$(getprop ro.boot.slot_suffix)" = "_'''+self.slot+'''" ]
"$BB" sh "$P/android-bootchain.sh" sync "$T/bootchain-backup"
dev=/dev/block/by-name/boot_'''+self.linux+'''
[ "$(blockdev --getsize64 "$dev")" -eq "$(stat -c %s "$P/boot-desktop.img")" ]
dd if="$dev" of="$T/boot-before.img" bs=1048576
dd if="$P/boot-desktop.img" of="$dev" bs=1048576 conv=fsync
dd if="$dev" of="$T/boot-check.img" bs=1048576
if ! cmp -s "$P/boot-desktop.img" "$T/boot-check.img"; then
    dd if="$T/boot-before.img" of="$dev" bs=1048576 conv=fsync
    dd if="$dev" of="$T/boot-check.img" bs=1048576
    cmp -s "$T/boot-before.img" "$T/boot-check.img" || echo 'BOOT RESTORE FAILED; do not switch slots'
    exit 1
fi
dd if=/dev/block/by-name/misc of="$T/misc-before.bin" bs=1 skip=2048 count=32
dd if="$P/misc-linux.bin" of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc,fsync
dd if=/dev/block/by-name/misc of="$T/misc-check.bin" bs=1 skip=2048 count=32
if ! cmp -s "$P/misc-linux.bin" "$T/misc-check.bin"; then
    dd if="$T/misc-before.bin" of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc,fsync
    exit 1
fi
echo 'BOOT-VERIFIED; OpenWrt selected'
''')

def transaction(path,record):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(record,indent=2)+'\n');tmp.replace(path)
    if GUI:GUI.saved(path,record)

def validate_resume(s,r,slot,serial,root_geometry,marker,root_verified=False):
    if s['CID']!=r['storage']['CID'] or s['NODE']!=r['storage']['NODE'] or s['START']!=r['storage']['START'] or slot!=r['android_slot'] or serial!=r['serial']:
        raise ValueError('Resume device/slot differs from the saved transaction')
    if s['SIZE']!=r['target'] or root_geometry!=(s['START']+r['target'],r['need']):raise ValueError('Prepared partition geometry differs')
    if not root_verified and marker.strip()!=('MU300-PART-PREPARED-V2 '+r['token']).encode():raise ValueError('Preparation token mismatch; refusing to overwrite a root')

def resume_payload_id(manifest):
    # Paths, EXE, prompts and upload scripts can change in a repair release.
    # Bind the transaction to actual system/boot content and filesystem tools.
    layout=manifest.get('magisk_layout',{})
    files={}
    for name in ('boot-linux.img','mu300-openwrt.tar.gz','rootfs-critical.sha256','rootfs.requirements','busybox'):
        h=manifest['magisk'].get(layout.get(name,name),'')
        if not re.fullmatch('[a-f0-9]{64}',h):raise ValueError('Incomplete resume payload metadata: '+name)
        files[name]=h
    for name,h in manifest['desktop'].items():
        if name.startswith('desktop/files/boot/repair/'):
            files[name]=h
    if not any(n.startswith('desktop/files/boot/repair/') for n in files):
        raise ValueError('Missing resume filesystem tool checksums')
    canonical={'format':1,'layout':'mu300root-container','kernel':manifest['kernel'],'files':files}
    return hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate_resume_package(root,manifest,r,record):
    current=resume_payload_id(manifest)
    if r.get('payload_identity'):
        if r['payload_identity']!=current:raise ValueError('Resume boot/rootfs/tools differ from the prepared installation')
        return current
    # Old records bound the entire manifest. Recover that exact metadata first;
    # never accept an arbitrary sibling package just because its kernel matches.
    candidates=[Path(record).parent/'package-manifest.json',Path(root)/'desktop/manifest.json']
    olddir=r.get('package_dir')
    if olddir:candidates.append(Path(olddir)/'desktop/manifest.json')
    candidates.extend(p/'desktop/manifest.json' for p in Path(root).parent.iterdir() if p.is_dir())
    for path in candidates:
        if not path.is_file() or digest(path)!=r.get('package'):continue
        if resume_payload_id(json.loads(path.read_text()))!=current:
            raise ValueError('Resume boot/rootfs/tools differ from the recorded package')
        print('Compatible repair package verified / 修复版关键安装载荷一致，可继续续装。')
        return current
    raise ValueError('Original package metadata is missing; keep its folder or restore package-manifest.json beside the transaction')

def bind_resume_package(root,manifest,r,record):
    r['payload_identity']=resume_payload_id(manifest)
    r['package']=digest(Path(root)/'desktop/manifest.json')
    r['package_dir']=str(Path(root).resolve())
    snapshot=Path(record).parent/'package-manifest.json'
    temp=snapshot.with_suffix('.tmp')
    temp.write_bytes((Path(root)/'desktop/manifest.json').read_bytes());temp.replace(snapshot)
    transaction(Path(record),r)

def load_resume(args,root,manifest,a,s):
    base=Path(args.backup_dir).expanduser()
    record=Path(args.transaction).expanduser() if args.transaction else next(iter(sorted(base.glob('*/transaction.json'),key=lambda p:p.stat().st_mtime,reverse=True)),None)
    if not record:raise ValueError('No saved transaction; specify --transaction FILE')
    r=json.loads(record.read_text());rootnode=s.get('ROOT')
    if not rootnode:raise ValueError('No prepared mu300root found')
    geometry=tuple(int(x) for x in a.shell('cat /sys/class/block/'+rootnode+'/start /sys/class/block/'+rootnode+'/size').split())
    marker=a.read('dd if=/dev/block/'+rootnode+' bs=512 count=1 2>/dev/null').split(b'\0',1)[0]
    verified=r.get('state')=='installed' or bool(GUI and GUI.ctx.record['effects'].get('root-part-write',{}).get('state')=='done')
    validate_resume(s,r,a.slot,a.serial,geometry,marker,root_verified=verified)
    validate_resume_package(root,manifest,r,record)
    return record,r,geometry

def boot(root,m,mode,live,slot,**kwargs):
    return m.build(payload_file(root,'boot-linux.img').read_bytes(),root/'desktop/files/boot',mode,live,slot,**kwargs)

def check_boot(root):
    # Exercise the real packaged repacker, including in a frozen Windows EXE.
    # Synthetic A/B data stays entirely on the computer; no ADB/SSH is used.
    m=module(root);source=payload_file(root,'boot-linux.img').read_bytes()
    kernel_size=struct.unpack_from('<I',source,8)[0]
    for slot,mode in (('a','sd'),('b','install-part')):
        bc=bytearray(32);bc[:4]=('_'+slot).encode()+b'\0\0';bc[4:8]=b'BCAB';bc[8]=1;bc[9]=2
        bc[12],bc[14]=(0x9f,0x1e) if slot=='a' else (0x1e,0x9f)
        bc[28:]=struct.pack('<I',zlib.crc32(bc[:28]))
        image=boot(root,m,mode,bytes(bc),slot)
        if image[4096:4096+kernel_size]!=source[4096:4096+kernel_size] or image[44:55]!=b'loglevel=5\0':
            raise ValueError('Desktop boot changed kernel or Linux sentinel')
        print('Desktop boot build verified:',mode,'Android slot',slot,'(no device writes)')

def install(args,root,manifest):
    a=Android(args);m=module(root)
    print('Device / 设备:',a.model,'Android',a.slot,'Linux',a.linux)
    if args.boot_openwrt:
        helper=importlib.util.spec_from_file_location('desktop_boot_control',root/'desktop/boot_control.py')
        control=importlib.util.module_from_spec(helper);helper.loader.exec_module(control)
        return control.boot_installed_mu300(globals(),a,root)
    s=a.storage()
    if args.tf:
        sd=a.shell('if [ -b /dev/block/mmcblk1p1 ]; then echo /dev/block/mmcblk1p1; elif [ -b /dev/block/mmcblk1 ]; then echo /dev/block/mmcblk1; fi').strip()
        if not sd:raise ValueError('No TF card found; choose an internal-storage method instead')
        if GUI:GUI.validate_plan(sd,capacity=int(a.shell('cat /sys/class/block/'+sd.rsplit('/',1)[-1]+'/size').strip())*512)
        confirm('This formats '+sd+'; its existing data will be erased / 会清空所选 TF 分区。')
        a.prepare_payload(root,tf=True)
        custom=settings(args,2048,'sd',manifest['kernel'])
        a.put(custom,'desktop-settings.env')
        a.push(root/'desktop/configure.sh','desktop-configure.sh')
        # Custom desktop choices are a private uploaded copy. Neither the
        # original Magisk scripts nor their ZIP checksums are changed.
        original=payload_file(root,'android-install.sh').read_text(encoding='utf-8')
        hook='    . "$P/desktop-configure.sh"\n    desktop_configure "$R"\n'
        needle='    rm -rf "$M/$os" && mv "$M/$os.new" "$M/$os"'
        if original.count(needle)!=1:raise ValueError('Unsupported Android installer version')
        private=original.replace(needle,hook+needle)
        a.put(private,'android-install.sh')
        live=a.live()
        private_boot=boot(root,m,'sd',live,a.slot)
        a.put(private_boot,'boot-linux.img')
        a.put(hashlib.sha256(private_boot).hexdigest()+'\n','boot-linux.sha256')
        replacements={'android-install.sh':hashlib.sha256(private.encode()).hexdigest(),
                      'boot-linux.img':hashlib.sha256(private_boot).hexdigest(),
                      'android-bootchain.sh':digest(root/'desktop/android-bootchain.sh'),
                      'bootchain.sh':digest(root/'desktop/bootchain.sh')}
        payload=payload_file(root,'payload.sha256').read_text()
        for name,checksum in replacements.items():
            payload=re.sub(r'^[a-f0-9]{64}(\s+\*?'+re.escape(name)+r')$',lambda match:checksum+match[1],payload,flags=re.M)
        a.put(payload,'payload.sha256')
        a.put(env({'SD_MODE':1,'SD_DEV':sd,'FORMAT':1,'SYNC_BOOTCHAIN':1,'OSES':'openwrt','WIPE_LEGACY':0,'UPDATE':0,'BOOT_OS':'openwrt','DEFAULT_LINUX':1,'BOOT_ATTEMPTS':5,'IMPORT_HOTSPOT':1,'KERNEL':manifest['kernel'],'PWHASH':''}),'mu300-install.env')
        # The original TF worker handles release, formatting and readback.
        # Only its desktop upload has custom settings and fresh A/B blocks.
        a.run('export MU300_TF_BOOTDEV=/dev/block/by-name/boot_'+a.linux+'\n"$BB" sh "$P/tf-install-worker.sh"',**({'effect':'root-tf-write'} if GUI else {}))
        return Outcome('success','TF installation verified / TF 安装和写入校验已完成。','Reboot the device manually / 请手动重启设备。')
    base=Path(args.backup_dir).expanduser()
    if args.resume:
        record,r,geom=load_resume(args,root,manifest,a,s)
        confirm('Resume installation into the prepared OpenWrt partition / 继续安装到已准备的 OpenWrt 分区。')
        bind_resume_package(root,manifest,r,record)
        a.prepare_payload(root)
        return direct_write(a,root,m,manifest,s,geom[1],record,r)
    if args.reinstall_part:
        node=s.get('ROOT')
        if not node:raise ValueError('No existing mu300root to reinstall')
        capacity=int(a.shell('cat /sys/class/block/'+node+'/size').strip())
        if GUI:GUI.validate_plan(node,capacity=capacity*512)
        confirm('Reinstall erases only the existing OpenWrt partition, including its settings / 会清空原 OpenWrt 分区及设置。')
        a.prepare_payload(root)
        return direct_write(a,root,m,manifest,s,capacity)
    # A new partition in a GPT gap never needs to access the encrypted raw
    # userdata. Decide this path before requesting a shrink/reset.
    if not args.data:
        if s.get('ROOT'):raise ValueError('mu300root exists; choose reinstall or update')
        space=a.gpt(s)
        if any(p['name']=='mu300root' for p in space['parts']):
            raise ValueError('GPT already has mu300root; reboot Android to refresh partition nodes, then choose reinstall/update')
        if not space['empty']:raise ValueError('GPT has no unused partition entry; nothing was changed')
        free_max=space['largest']//2048//4*4
        shrink_max=(s['SIZE']//2048-2048)//4*4
        maximum=max(free_max,shrink_max)
        print(f'eMMC / 闪存: {s["DISK_SIZE"]/2097152:.2f} GiB; GPT unused / 未分配可用: {free_max/1024:.2f} GiB')
        size=image_size(args.size or input(f'OpenWrt partition GiB / 分区大小 [4], max {maximum/1024:.2f}: ') or '4',maximum)
        offset=free_root_range(space,size*2048)
        if offset is not None:
            if GUI:GUI.validate_plan('GPT unallocated space',capacity=size*1048576,gap_start=offset)
            if args.format_userdata:raise ValueError('Free-space installation preserves userdata; omit --format-userdata')
            confirm(f'Use {size/1024:.2f} GiB of unallocated eMMC space; Android userdata is preserved / 使用未分配空间，不缩容、不格式化 Android。Selected raw range will be overwritten / 所选空闲区域原始内容将被覆盖。')
            backup=base/(time.strftime('%Y%m%d-%H%M%S')+'-'+s['CID'][-8:]);a.backup(backup,s)
            if (backup/'gpt-first.bin').read_bytes()!=space['first'] or (backup/'gpt-last.bin').read_bytes()!=space['last']:
                raise ValueError('GPT changed during preflight; nothing was installed')
            a.prepare_payload(root)
            return free_write(a,root,m,manifest,s,space,offset,size*2048)
    is_encrypted=encrypted({'state':s['STATE'],'type':s['TYPE']},s['SOURCE'],s['EXT'] if s['EXT']=='53ef' else s['MAGIC'])
    if args.data and is_encrypted:raise ValueError('Encrypted userdata cannot host a bootable image. Choose independent-partition installation.')
    if args.data:
        print("EXPERIMENTAL / 实验性功能：userdata 镜像模式可能破坏 Android 的 F2FS 文件系统。\n"
              "This mode may damage Android's F2FS filesystem. Prefer TF or an independent internal partition.")
        maximum=(s['FREE']//1024-1024-256)
        print(f'Available / 可用: {s["FREE"]/1048576:.2f} GiB; image maximum {maximum/1024:.2f} GiB')
        size=image_size(args.size or input('Image size GiB / 镜像大小 [4]: ') or '4',maximum)
        if GUI:GUI.validate_plan('userdata-image',capacity=size*1048576)
        confirm('Create a userdata image; an existing image will be retained as a backup / 新建镜像，保留已有镜像备份。')
        a.prepare_payload(root)
        a.put(settings(args,size,'data-image',manifest['kernel']),'desktop-settings.env')
        a.run('"$BB" sh "$P/android-image.sh"',**({'effect':'root-image-write'} if GUI else {}))
        live=a.live();a.flash(boot(root,m,'data-image',live,a.slot),m.boot_control(live,a.slot))
        return Outcome('success','Userdata image and boot verified / userdata 镜像和启动镜像已安装并校验。','Reboot the device manually / 请手动重启设备。')
    if s['START']%2048:raise ValueError('userdata starts at an unsupported unaligned sector')
    target,need=allocation(s['SIZE'],size)
    destructive=is_encrypted or args.format_userdata
    if GUI:GUI.validate_plan('userdata shrink',wipe=destructive,capacity=size*1048576)
    if not destructive and not GUI:
        destructive=input('Format Android userdata instead of preserving it? / 是否格式化 Android 数据？[no]: ').strip().lower()=='yes'
    if not destructive and (target//2 < (s['SIZE']//2-s['FREE']+1048576)):
        raise ValueError('Android data + 1 GiB reserve does not fit the selected smaller userdata')
    confirm('Userdata will be formatted and Android reset / userdata 将格式化，Android 数据全部清空。' if destructive else 'Shrink userdata offline while preserving Android data / 离线缩小 userdata，保留 Android 数据。')
    backup=base/(time.strftime('%Y%m%d-%H%M%S')+'-'+s['CID'][-8:]);a.backup(backup,s)
    live=a.live()
    if destructive:
        token=secrets.token_hex(16)
        aa=bytearray(live);aa[:4]=('_'+a.slot).encode()+b'\0\0';aa[12],aa[14]=(0x9f,0x1e) if a.slot=='a' else (0x1e,0x9f);aa[28:]=struct.pack('<I',zlib.crc32(aa[:28]))
        request=':'.join(map(str,(token,s['CID'],s['NODE'],s['START'],s['SIZE'],target,need,bytes(aa).hex())))
        r={'format':1,'state':'prepared-request','serial':a.serial,'android_slot':a.slot,'storage':s,'target':target,'need':need,'token':token,'package':digest(root/'desktop/manifest.json')}
        bind_resume_package(root,manifest,r,backup/'transaction.json')
        a.prepare_payload(root,with_rootfs=False)
        a.flash(boot(root,m,'prepare',live,a.slot,prepare=request),m.boot_control(live,a.slot))
        print('Saved resume transaction / 续装记录:',backup/'transaction.json')
        print('Rebooting to offline partition preparation and stock recovery. Complete Android setup, USB debugging and su; then run resume-internal-install / 重置后重新启用 USB 调试和 root，运行续装入口。')
        print('First boot resizes offline and can take several minutes. Keep power connected; it reboots after resizing. / 首次开机离线缩容通常需数分钟，请勿断电；缩容完成后自动重启，原厂恢复出厂可能另有重启。')
        a.reboot()
        return Outcome('pending','Preparation written; offline resize/reset is starting / 准备阶段已写入，设备将离线缩容并重置 Android。',
                       'Allow several minutes for offline resizing and its automatic reboot. Finish Android setup, enable USB debugging and root, then choose 5 to resume / 等待数分钟离线缩容及自动重启，完成 Android 初始化，启用 USB 调试和 root，再选 5 续装。')
    # Use a small, fully verified initial image; offline resize fills ALL space.
    a.prepare_payload(root)
    a.put(settings(args,2048,'part-stage',manifest['kernel']),'desktop-settings.env')
    a.run('"$BB" sh "$P/android-image.sh"')
    a.run('printf "PART_MIB='+str(size)+'\\nDATA_MIB='+str(target//2048)+'\\nGROW_FS='+str(int(getattr(args,'grow_fs',True)))+'\\nFORMAT_DATA=0\\n" > /data/mu300linux/part-install.conf\nsync')
    identity=':'.join(map(str,(s['CID'],s['NODE'],s['START'],s['SIZE'],target)))
    a.flash(boot(root,m,'install-part',live,a.slot,identity=identity),m.boot_control(live,a.slot))
    print('Offline shrink/install starts after reboot; progress at http://192.168.77.1:8089/progress.txt. / 重启后开始离线安装。')
    print('First boot may take several minutes or longer; do not interrupt power. After installation it reboots once into OpenWrt. / 首次开机缩容安装通常需数分钟，数据多或闪存慢时更久；完成后自动重启一次进入 OpenWrt，请勿中断。')
    a.reboot()
    return Outcome('pending','Payload verified; offline shrink/install is starting / 载荷校验通过，设备将离线缩容并安装。',
                   'Allow several minutes or longer; it automatically reboots once into OpenWrt. Keep power connected / 首次缩容安装通常需数分钟或更久，完成后自动重启一次进入 OpenWrt，请保持供电。')

def direct_write(a,root,m,manifest,s,sectors,record=None,r=None):
    node=s['ROOT']
    verified=bool(GUI and GUI.ctx.record['effects'].get('root-part-write',{}).get('state')=='done')
    if not verified:
        a.put(settings(a.args,2048,'part',manifest['kernel']),'desktop-settings.env')
        a.run('"$BB" sh "$P/android-image.sh"')
        checksum=a.shell('sha256sum '+a.t+'/openwrt.ext4.new').split()[0]
        a.run('"$BB" sh "$P/android-write-part.sh" /dev/block/'+node+' '+s['CID']+' '+str(sectors)+' '+checksum,**({'effect':'root-part-write'} if GUI else {}))
    live=a.live();a.flash(boot(root,m,'part',live,a.slot),m.boot_control(live,a.slot))
    if record:r['state']='installed';transaction(record,r)
    return Outcome('success',f'Independent partition installed and verified: {sectors/2097152:.2f} GiB / 独立分区已安装、扩容并校验。',
                   'Reboot the device manually / 请手动重启设备。')

def free_write(a,root,m,manifest,s,space,offset,sectors):
    first,last=gpt_add_root(space['first'],space['last'],s['DISK_SIZE'],offset,sectors)
    for name,data in (('gpt-before-first.bin',space['first']),('gpt-before-last.bin',space['last']),
                      ('gpt-new-first.bin',first),('gpt-new-last.bin',last)):
        a.put(data,name)
    a.put(settings(a.args,2048,'part',manifest['kernel']),'desktop-settings.env')
    a.run('"$BB" sh "$P/android-image.sh"')
    checksum=a.shell('sha256sum '+a.t+'/openwrt.ext4.new').split()[0]
    if not re.fullmatch('[a-f0-9]{64}',checksum):raise ValueError('Invalid image checksum')
    disk='/dev/block/'+s['NODE'].split('p')[0]
    a.run('"$BB" sh "$P/android-write-free.sh" '+env_args([
        disk,s['CID'],s['DISK_SIZE'],offset,sectors,checksum,space['front'],space['tail']]),**({'effect':'root-free-write'} if GUI else {}))
    # Independent verification after the device-side comparison, before
    # writing boot or selecting the alternate slot.
    if a.read(f'dd if={disk} bs=512 count={space["front"]} 2>/dev/null')!=first or a.read(
            f'dd if={disk} bs=512 skip={space["tail"]} count={s["DISK_SIZE"]-space["tail"]} 2>/dev/null')!=last:
        raise ValueError('GPT readback failed; boot selection was not changed')
    live=a.live();a.flash(boot(root,m,'part',live,a.slot),m.boot_control(live,a.slot))
    return Outcome('success',f'Installed in unallocated eMMC: {sectors/2097152:.2f} GiB; Android preserved / 未分配空间安装完成，Android 数据保留。',
                   'Reboot the device manually; the new partition appears after reboot / 手动重启后新分区生效。')

def env_args(values):
    return ' '.join(shlex.quote(str(v)) for v in values)

def ssh_exec(c,command,timeout=1800):
    _,out,_=c.exec_command(command,timeout=timeout)
    channel=out.channel;channel.set_combine_stderr(True);start=time.monotonic();last=start
    parts=[]
    while True:
        if channel.recv_ready():
            b=channel.recv(65536);parts.append(b);print(b.decode(errors='replace'),end='',flush=True)
        elif channel.exit_status_ready():break
        else:
            time.sleep(.1)
            if time.monotonic()-last>=10:print(f'  Working… {int(time.monotonic()-start)} s');last=time.monotonic()
        if time.monotonic()-start>timeout:raise TimeoutError('SSH timed out; check device status before retrying')
    rc=channel.recv_exit_status()
    if rc:raise RuntimeError('Remote operation failed, exit '+str(rc))
    return b''.join(parts).decode(errors='replace')

def ssh_read_exact(c,command,size):
    # SSH transports binary data directly. Keep diagnostic stderr separate;
    # minimal OpenWrt images need not provide od/hexdump or another encoder.
    _,out,err=c.exec_command(command,timeout=30)
    data=out.read();detail=err.read().decode(errors='replace').strip()
    rc=out.channel.recv_exit_status()
    if rc:raise RuntimeError('Cannot read boot control / 无法读取启动控制信息: '+(detail or 'exit '+str(rc)))
    if len(data)!=size:
        raise ValueError(f'Incomplete boot control / 启动控制信息读取不完整: expected {size} bytes, got {len(data)}')
    return data


def ssh_upload(c,path,destination):
    # Dropbear installations often have no SFTP subsystem. Use the same SSH
    # exec transport as commands; the caller verifies the staged file hash.
    channel=c.get_transport().open_session()
    try:
        channel.settimeout(120)
        channel.exec_command('umask 077; cat > '+shlex.quote(destination))
        size=path.stat().st_size;sent=0;last=0
        started=time.monotonic()
        with path.open('rb') as stream:
            while data:=stream.read(262144):
                channel.sendall(data);sent+=len(data)
                if GUI:GUI.transfer(path.name,sent,size,started)
                if time.monotonic()-last>=1:
                    print(f'\r  {path.name}: {sent>>20}/{size>>20} MiB',end='',flush=True)
                    last=time.monotonic()
        channel.shutdown_write()
        detail=channel.makefile_stderr().read().decode(errors='replace').strip()
        rc=channel.recv_exit_status()
        print()
        if rc:raise RuntimeError('SSH upload failed / SSH 上传失败: '+(detail or 'exit '+str(rc)))
    finally:channel.close()


def update(args,root):
    # Same implementation as the GUI backend, including older hidden-area roots.
    helper=Path(__file__).resolve().parent/'update_control.py'
    if not helper.is_file():helper=Path(root)/'desktop/update_control.py'
    spec=importlib.util.spec_from_file_location('mu300_update_control',helper)
    control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
    return control.update_mu300(globals(),args,root)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    actions=p.add_mutually_exclusive_group()
    for n in ('tf','data','part','reinstall-part','resume','update','boot-openwrt','check','check-boot','check-rootfs','check-resume','probe'):
        help_text = 'EXPERIMENTAL userdata image: may damage Android F2FS; unencrypted data only' if n=='data' else None
        actions.add_argument('--'+n,action='store_true',help=help_text)
    pause=p.add_mutually_exclusive_group()
    pause.add_argument('--pause',action='store_true',help='wait for Enter after the result, including redirected input')
    pause.add_argument('--no-pause',action='store_true',help='exit immediately after the result (automation)')
    p.add_argument('--adb');p.add_argument('--serial');p.add_argument('--host');p.add_argument('--size')
    p.add_argument('--format-userdata',action='store_true');p.add_argument('--transaction')
    p.add_argument('--backup-dir',default=str(Path.home()/'mu300-backups'))
    args=p.parse_args(argv);root=package_root();manifest=verify(root)
    print('MU300 desktop package verified; kernel',manifest['kernel'])
    if args.check:return Outcome('checked','Package checksums passed / 安装包校验通过。')
    if args.check_rootfs:
        before=digest(payload_file(root,'mu300-openwrt.tar.gz'))
        with payload_rootfs(root) as archive:
            with tarfile.open(archive) as tar:
                print('Desktop rootfs prepared and readable:',len(tar.getmembers()),'members (no device access)')
        if digest(payload_file(root,'mu300-openwrt.tar.gz'))!=before:raise ValueError('Shared rootfs changed during preparation')
        return Outcome('checked','Shared rootfs preparation passed / 共享 rootfs 准备检查通过。')
    if args.check_boot:
        check_boot(root);return Outcome('checked','Desktop boot construction passed / 桌面启动镜像构建检查通过。')
    if args.check_resume:
        args.probe=True
        a=Android(args);_,_,geometry=load_resume(args,root,manifest,a,a.storage())
        print(f'Resume verified / 续装校验通过: {geometry[1]/2097152:.2f} GiB (no device writes)')
        return Outcome('checked',f'Resume record verified: {geometry[1]/2097152:.2f} GiB / 续装记录校验通过。')
    if args.probe:
        a=Android(args);storage=a.storage();bc=a.live();space=a.gpt(storage)
        module(root).boot_control(bc,a.slot)  # validate only, never write misc
        dt=a.read('cat /sys/firmware/fdt')
        if len(dt)<8 or dt[:4]!=bytes.fromhex('d00dfeed') or struct.unpack_from('>I',dt,4)[0]!=len(dt):
            raise ValueError('Device tree binary transport verification failed')
        print(json.dumps({'profile':a.profile,'model':a.model,'android_slot':a.slot,
                          'encryption':storage['STATE'],'encryption_type':storage['TYPE'],
                          'userdata_free_gib':round(storage['FREE']/1048576,2),
                          'emmc_gib':round(storage['DISK_SIZE']/2097152,2),
                          'gpt_unused_largest_gib':round(space['largest']/2097152,2),
                          'gpt_unused_ranges':space['safe_gaps'],
                          'dtb_bytes':len(dt),'dtb_sha256':hashlib.sha256(dt).hexdigest(),
                          'device_writes':False},ensure_ascii=False,indent=2))
        return Outcome('checked','Read-only device preflight passed / 设备只读预检通过。')
    if not any(getattr(args,n) for n in ('tf','data','part','reinstall_part','resume','update','boot_openwrt')):
        choices=('tf','data','part','reinstall_part','resume','update','boot_openwrt')
        print('1 TF card / TF 卡\n2 Userdata image [EXPERIMENTAL] / userdata 镜像【实验性：可能损坏 Android F2FS】\n3 Independent internal partition / 独立内置分区\n4 Reinstall internal system / 重装独立分区\n5 Resume after Android reset / 重置后续装\n6 Update OpenWrt / 更新\n7 Boot OpenWrt once / 临时启动 OpenWrt（仅修改 misc）')
        selected=input('Choose / 选择 [1]: ').strip() or '1'
        if selected not in tuple(map(str,range(1,8))):raise ValueError('Invalid menu choice')
        setattr(args,choices[int(selected)-1],True)
    if args.update:return update(args,root)
    return install(args,root,manifest)

if __name__=='__main__':
    sys.exit(run_cli())
