"""Serial-bound ADB operations. Frontend never supplies shell text."""
import os
import ipaddress
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time

from .state import sha
from .diagnostics import traced
from .binary_io import pull_binary
from .windows_mtp import prepare_devices


def hidden_process():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def adb_endpoint(value):
    """Validate one TCP endpoint, never options or command text."""
    invalid = 'Enter an address such as 192.168.0.1:5555 / 请输入地址，例如 192.168.0.1:5555'
    if not isinstance(value, str) or len(value) > 270:
        raise ValueError(invalid)
    value = value.strip()
    port = '5555'
    if value.startswith('['):
        match = re.fullmatch(r'\[([0-9a-fA-F:.]+)\](?::([0-9]{1,5}))?', value)
        if not match: raise ValueError(invalid)
        try: host = '['+str(ipaddress.IPv6Address(match[1]))+']'
        except ValueError: raise ValueError(invalid) from None
        port = match[2] or port
    elif value.count(':') > 1:
        try: host = '['+str(ipaddress.IPv6Address(value))+']'
        except ValueError: raise ValueError(invalid) from None
    else:
        host, separator, supplied_port = value.partition(':')
        if separator: port = supplied_port
        if re.fullmatch(r'[0-9.]+', host or ''):
            try: host = str(ipaddress.IPv4Address(host))
            except ValueError: raise ValueError(invalid) from None
        elif (len(host) > 253 or not host or
              any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                  for label in host.split('.'))):
            raise ValueError(invalid)
        host = host.lower()
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise ValueError(invalid)
    return host+':'+str(int(port))


def wireless_serial(serial):
    if not isinstance(serial, str): return False
    if re.fullmatch(r'adb-[A-Za-z0-9_-]{1,200}\._adb-tls-connect\._tcp\.?', serial):
        return True
    if ':' not in serial: return False
    try:
        return adb_endpoint(serial) == serial.lower()
    except ValueError:
        return False


class Adb:
    def __init__(self, package, serial=None):
        self.serial = serial
        names = ('tools/adb.exe', 'platform-tools/adb.exe') if sys.platform == 'win32' else (
            'tools/macos/adb', 'tools/adb', 'platform-tools/adb') if sys.platform == 'darwin' else (
            'tools/adb', 'platform-tools/adb')
        paths = [Path(package) / name for name in names]
        self.exe = next((str(p) for p in paths if p.is_file()), shutil.which('adb'))
        if not self.exe:
            raise ValueError('ADB is missing from tools / 安装包缺少 ADB 工具')
        self.su = None
        self.context = None
        self.backup_staging = None

    def call(self, *args, timeout=20, check=True):
        cmd = [self.exe]
        if self.serial and args[0] not in ('devices', 'connect', 'disconnect'):
            cmd += ['-s', self.serial]
        def perform():
            result = subprocess.run(cmd + list(args), capture_output=True, timeout=timeout, **hidden_process())
            if check and result.returncode:
                detail = (result.stderr or result.stdout).decode('utf-8', 'replace')[-4000:].strip()
                raise ConnectionError('ADB command failed / ADB 命令失败'+(': '+detail if detail else ''))
            return result.stdout.decode('utf-8', 'replace').replace('\r', '')
        return traced(self.context, 'ADB '+shlex.join(list(args)), perform, output=True)

    def devices(self):
        result = []
        for line in self.call('devices', '-l').splitlines()[1:]:
            values = line.split()
            if len(values) >= 2:
                result.append({'serial': values[0], 'state': values[1],
                               'wireless': wireless_serial(values[0]),
                               'label': next((v[6:] for v in values[2:] if v.startswith('model:')), values[0][-4:])})
        # Listing and wireless connection management stay read-only.
        if getattr(self, 'mtp_workaround', False):
            prepare_devices([d for d in result if d['serial'] == self.serial], getattr(self, 'notice', None))
        return result

    def connect(self, address):
        endpoint = adb_endpoint(address)
        try:
            self.call('connect', endpoint, timeout=15)
        except subprocess.TimeoutExpired:
            raise ConnectionError('Wireless ADB connection timed out; check the address, network and device debugging / 无线 ADB 连接超时，请检查地址、网络和设备调试设置') from None
        devices = self.devices()
        found = next((d for d in devices if d['serial'] == endpoint), None)
        if not found:
            raise ConnectionError('Wireless ADB did not connect; check the address/port and enable wireless ADB on the device / 无线 ADB 未连接，请核对地址和端口，并在设备上开启无线 ADB')
        return {'endpoint': endpoint, 'state': found['state'], 'devices': devices}

    def disconnect(self, serial):
        if not wireless_serial(serial) or not any(d['serial'] == serial for d in self.devices()):
            raise ValueError('Select a connected wireless ADB device / 请选择列表中已连接的无线 ADB 设备')
        try:
            self.call('disconnect', serial, timeout=10)
        except subprocess.TimeoutExpired:
            raise ConnectionError('Disconnect timed out; refresh the device list / 断开连接超时，请刷新设备列表') from None
        devices = self.devices()
        if any(d['serial'] == serial for d in devices):
            raise ConnectionError('Wireless ADB is still connected; refresh and retry / 无线 ADB 仍然连接，请刷新后重试')
        return {'endpoint': serial, 'devices': devices}

    def shell(self, script, root=False, timeout=20):
        if root and self.su is None:
            raise ValueError('Root has not been verified / 尚未验证 root')
        command = (self.su + ' -c ' if root and self.su else 'sh -c ') + shlex.quote(script)
        return self.call('shell', command, timeout=timeout)

    def root_status(self):
        if self.shell('id -u').strip() == '0':
            self.su = ''
            return 'ready'
        found = False
        for name in ('su', '/debug_ramdisk/su', '/sbin/su'):
            available = self.shell('command -v ' + name + ' >/dev/null 2>&1; echo $?').strip() == '0'
            if not available:
                continue
            found = True
            try:
                result = self.shell(name + ' -c id', timeout=8)
                if re.search(r'\buid=0\b', result):
                    self.su = name
                    return 'ready'
            except (ConnectionError, subprocess.TimeoutExpired):
                pass
        return 'root_permission' if found else 'magisk'

    def read(self, script):
        """Small metadata uses the same verified staging/pull transport as CLI."""
        if self.su is None:raise ValueError('Root has not been verified / 尚未验证 root')
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            local=Path(temp)/'metadata.bin'
            pull_binary(self.call,self.shell,script,local)
            return local.read_bytes()

    def _backup_directory(self, context, total):
        free=int(self.shell("df -Pk /data/local/tmp | tail -n1 | awk '{print $4}'").strip())*1024
        if free<total+(64<<20):
            raise ValueError('Insufficient Android staging space for backup / Android 备份临时空间不足')
        if not self.backup_staging:
            self.backup_staging='/data/local/tmp/openwrt-backup-'+secrets.token_hex(12)
            # A shell-owned private directory lets adb pull read root-created
            # images without making the directory accessible to other apps.
            context.record['backup_staging']=self.backup_staging
            context.store.save(context.record)
            self.shell('umask 077; mkdir '+self.backup_staging)
        return self.backup_staging

    def _pull_backup(self, remote, local, total, name, context):
        args=[self.exe]+(['-s',self.serial] if self.serial else [])+['pull',remote,str(local)]
        since=time.monotonic();last_change=since;previous=-1
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**hidden_process())
        try:
            while True:
                context.boundary()
                try:
                    out,err=process.communicate(timeout=.1)
                    finished=True
                except subprocess.TimeoutExpired:
                    finished=False
                received=local.stat().st_size if local.exists() else 0
                if received>total:raise ValueError('Backup is larger than the selected partition / 备份大小超出分区范围')
                if received!=previous:
                    last_change=time.monotonic();previous=received
                    context.transfer('backup/'+name,received,total,since)
                if finished:
                    if process.returncode:
                        raise ConnectionError('ADB pull failed / ADB 拉取失败: '+err.decode('utf-8','replace')[-2000:])
                    context.log(out.decode('utf-8','replace')+err.decode('utf-8','replace'),scope='adb-pull')
                    if received!=total:raise ValueError('Incomplete backup / 备份文件不完整: '+name)
                    return
                if time.monotonic()-last_change>45:
                    raise TimeoutError('ADB pull received no data for 45 seconds / ADB 拉取 45 秒未收到数据')
        finally:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)

    def backup_partition(self, name, destination, context):
        if not re.fullmatch(r'(misc|(?:boot|uboot|sml|teecfg|trustos|vbmeta)_[ab])',name):
            raise ValueError('Unsupported backup partition')
        source='/dev/block/by-name/'+name
        total=4096 if name=='misc' else int(self.shell('blockdev --getsize64 '+source,root=True).strip())
        if total<=0:raise ValueError('Invalid backup partition size: '+name)
        remote=self._backup_directory(context,total)+'/'+name+'.img'
        local=Path(str(destination)+'.partial')
        context.log(f'Backing up {name}: {total} bytes; device dd -> adb pull',scope='backup')
        try:
            command=f'dd if={source} of={remote} bs=4M'
            if name=='misc':command=f'dd if={source} of={remote} bs=4096 count=1'
            self.shell('set -e; umask 077; '+command+'; chmod 644 '+remote,root=True,timeout=120)
            expected=self.shell('sha256sum '+remote,root=True,timeout=120).split()[0]
            original=f'dd if={source} bs=4096 count=1 2>/dev/null' if name=='misc' else 'cat '+source
            actual=self.shell('(set -o pipefail; '+original+') | sha256sum',root=True,timeout=120).split()[0]
            if not re.fullmatch('[a-f0-9]{64}',expected) or expected!=actual:
                raise ValueError('Device backup checksum mismatch / 设备临时备份校验失败: '+name)
            self._pull_backup(remote,local,total,name,context)
            if sha(local)!=expected:raise ValueError('Computer backup checksum mismatch / 电脑备份校验失败: '+name)
            with local.open('r+b') as stream:os.fsync(stream.fileno())
            os.replace(local,destination)
            context.log(f'Backup verified: {name}, {total} bytes, SHA256 {expected}',scope='backup')
        finally:
            try:self.shell('rm -f '+remote,root=True)
            except Exception as error:context.log('Backup staging cleanup: '+str(error),level='warning',scope='backup')
            if local.exists():local.unlink()

    def cleanup_backup(self, context):
        if not self.backup_staging:return
        try:
            self.shell('rmdir '+self.backup_staging)
            context.record.pop('backup_staging',None)
            context.store.save(context.record)
            self.backup_staging=None
        except Exception as error:
            context.log('Backup staging directory cleanup: '+str(error),level='warning',scope='backup')

    def properties(self):
        names = ('ro.product.model', 'ro.product.device', 'ro.board.platform', 'ro.boot.slot_suffix',
                 'ro.boot.verifiedbootstate', 'ro.crypto.state', 'ro.crypto.type', 'sys.boot_completed')
        result = {}
        for name in names:
            result[name] = self.shell('getprop ' + name).strip()
        return result


def push_chunks(adb_call, shell, local, remote, context):
    """Actual acknowledged bytes, checksum at destination, no terminal parsing."""
    import tempfile
    total = Path(local).stat().st_size
    since = time.monotonic()
    shell('rm -f ' + shlex.quote(remote))
    done = 0
    with open(local, 'rb') as stream, tempfile.TemporaryDirectory() as temp:
        chunk_path = Path(temp) / 'chunk'
        while chunk := stream.read(8 << 20):
            context.boundary()
            chunk_path.write_bytes(chunk)
            part = remote + '.upload-chunk'
            adb_call('push', str(chunk_path), part)
            if shell('sha256sum ' + shlex.quote(part)).split()[0] != sha(chunk_path):
                raise ValueError('Transfer chunk checksum failed / 传输块校验失败')
            shell('cat ' + shlex.quote(part) + ' >> ' + shlex.quote(remote) + ' && rm -f ' + shlex.quote(part))
            done += len(chunk)
            context.transfer(Path(local).name, done, total, since)
    if shell('sha256sum ' + shlex.quote(remote)).split()[0] != sha(local):
        raise ValueError('Transfer checksum failed / 传输校验失败')
    return remote
