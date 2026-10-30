"""Repository adapters call existing installation code, never frontend shell."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import secrets
import shlex
import struct
import sys
import time
from types import SimpleNamespace

from . import gpt, storage as storage_io
from .state import Pending, RecoveryRequired, atomic_json, sha
from .transport import Adb, push_chunks
from .boot_image import boot_installed_e5, boot_installed_mu300
from .mu300_update import update_mu300
from .diagnostics import DebugStream, traced


def load(path, name):
    # /var and /private/var may name the same package on macOS.
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def debug_output(context):
    stream = DebugStream(context)
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            yield
    finally:
        stream.flush()


def args_for(record, directory):
    choices = record['choices']
    selected_gib = record['plan']['openwrt_bytes'] / (1 << 30) if choices['method'] in ('part','image') else choices.get('size_gib',4)
    return SimpleNamespace(y=True, apn='', ssid=None, wifi_key=record.get('secrets', {}).get('wifi_key', ''), grow_fs=choices.get('grow', True),
                           part_size=str(selected_gib), image_size=str(selected_gib),
                           size=str(selected_gib), format_userdata=record['plan'].get('wipe', False),
                           tf=choices['method'] == 'tf', data=choices['method'] == 'image',
                           part=choices['method'] == 'part', reinstall_part=choices['method'] == 'reinstall',
                           boot_openwrt=choices['method'] == 'boot', resume=False, resume_part=None,
                           transaction=None, backup_dir=str(directory / 'legacy-backups'), probe=False,
                           serial=record['device']['serial'], adb=None, host=choices.get('host'))


def installation_committed(record):
    """Only final, verified effects can bypass the initial Android plan.

    A stage-one boot selection is not a final installation. Legacy two-stage
    records must still use their geometry/token-aware resume implementation.
    """
    method = record['choices']['method']
    if record.get('legacy') or record['plan'].get('method') != method:
        return False
    done = {name for name, value in record['effects'].items() if value['state'] == 'done'}
    if method == 'update':
        return 'update-commit' in done
    if record['package']['adapter'] == 'e5':
        return method in ('tf', 'part', 'image', 'boot') and 'misc-trial' in done
    return record['package']['adapter'] == 'mu300' and (
            method == 'boot' and 'misc-trial' in done or method == 'tf' and 'root-tf-write' in done or
            method in ('part', 'reinstall', 'image') and any(name.startswith('boot-and-misc-') for name in done))


class Adapter:
    def __init__(self, package, context=None):
        self.package = package
        self.root = Path(package['path'])
        self.kind = package['adapter']
        self.ctx = context
        self.legacy = load(self.root / 'OpenWrt-Installer.py', '_installer_' + secrets.token_hex(4))
        self.legacy.GUI = self
        # Older package helpers may still implement a terminal confirmation.
        # Bind confirmation to the coordinator; never read its NDJSON stdin.
        self.legacy.confirm = self.confirm_legacy
        self.legacy.input = self.console_input_unavailable
        if self.kind == 'e5':
            self.legacy.B, self.legacy.F, self.legacy.SCRIPTS = str(self.root), str(self.root/'files'), str(self.root/'scripts')
            # Old compatible package scripts must not retain their exact-hash
            # gate when selecting the already installed system.
            self.legacy.boot_openwrt = lambda args:boot_installed_e5(vars(self.legacy), self.adb.read)
        else:
            self.legacy.package_root = lambda: self.root
            # Override older compatible packages too: no legacy binary codec
            # or second full device backup may run behind the GUI coordinator.
            self.legacy.Android.read = lambda android, command:self.read_legacy(android, command)
            self.legacy.Android.backup = lambda android, path, storage:self.backup_legacy(android, path, storage)
        self.adb = None
        self.password = ''
        self.expected_ssh_key = None
        if self.ctx:
            if self.kind == 'e5':
                original = self.legacy.adb
                def observed(*args, **kwargs):
                    return traced(self.ctx, 'legacy ADB '+shlex.join(map(str,args)),
                                  lambda:original(*args, **kwargs), output=True)
                self.legacy.adb = observed
            else:
                original = self.legacy.Android.call
                def observed(android, *args, **kwargs):
                    return traced(self.ctx, 'legacy ADB '+shlex.join(map(str,args)),
                                  lambda:original(android, *args, **kwargs), output=False)
                self.legacy.Android.call = observed

    def confirm_legacy(self, *_args, **_kwargs):
        if not self.ctx or self.legacy.GUI is not self:
            raise RecoveryRequired('GUI confirmation context is unavailable / 图形界面确认上下文不可用，已停止')
        self.ctx.boundary()
        return True

    def console_input_unavailable(self, *_args, **_kwargs):
        raise RecoveryRequired('A required GUI option is missing; console input is unavailable / 缺少必要的图形界面参数，已停止；请返回安装计划检查选项')

    def stage(self, stage, status='running', **details):
        if self.ctx:
            self.ctx.stage(stage, status, **details)

    def transfer(self, name, done, total, since):
        if self.ctx:
            self.ctx.transfer(name, done, total, since)

    def run_tool(self, script, *arguments):
        from .helpers import run_helper
        return run_helper(self.ctx, self.root, script, arguments)

    def action(self, name, operation, retry_safe=False):
        if not self.ctx:
            raise ValueError('Writes are unavailable during a probe')
        if self.ctx.record.get('legacy') and name in ('bootchain-sync', 'misc-trial'):
            state = json.loads(Path(self.ctx.record['legacy']).read_text(encoding='utf-8'))
            name += '-' + state.get('phase', state.get('state', 'unknown'))
        result = self.ctx.effect(name, operation, retry_safe=retry_safe)
        if name == 'update-commit':
            self.stage('openwrt_boot','waiting_device',reboot_manually=False,
                       host=self.ctx.record['choices'].get('host'))
        return result

    def saved(self, path, record):
        if self.ctx:
            self.ctx.legacy_record(path, record)

    def validate_plan(self, target, wipe=False, capacity=None, gap_start=None):
        plan = self.ctx.record['plan']
        if (target != plan['target'] or bool(wipe) != bool(plan['wipe']) or
                capacity is not None and capacity != plan['openwrt_bytes'] or
                gap_start is not None and gap_start != plan.get('gap_start')):
            raise ValueError('Actual installation path differs from the confirmed plan; nothing further written / 实际安装路径与已确认计划不一致，已停止后续写入')

    def backup_base(self):
        return str(self.ctx.directory)

    def read_legacy(self, android, command):
        if not self.adb or android.serial != self.adb.serial:
            raise ValueError('Binary read device differs from the selected device')
        return self.adb.read(command)

    def backup_legacy(self, android, directory, storage):
        import shutil
        if not self.ctx or not self.adb or android.serial != self.ctx.record['device']['serial']:
            raise ValueError('Backup device differs from the saved transaction')
        expected=self.ctx.record['device']['storage']
        if any(storage.get(k)!=expected.get(k) for k in ('CID','NODE','START','SIZE','DISK_SIZE')):
            raise ValueError('Storage changed since the verified backup / 分区与已校验备份不一致')
        directory=Path(directory).resolve()
        if not directory.is_relative_to(self.ctx.directory.resolve()):
            raise ValueError('Legacy backup directory is outside the transaction')
        self.backup(self.ctx.record['device'])
        source=self.ctx.directory/'backup'
        sums=json.loads((source/'checksums.json').read_text())
        mapping={'misc-head.bin':'misc.bin','gpt-first.bin':'gpt-first.bin','gpt-last.bin':'gpt-last.bin'}
        mapping.update({f'{name}_{slot}.img':f'{name}_{slot}.bin' for name in ('boot','uboot','sml','teecfg','trustos','vbmeta') for slot in ('a','b')})
        if any(name not in sums or sha(source/name)!=sums[name] for name in mapping.values()):
            raise ValueError('Verified backup is incomplete or damaged / 已校验备份缺失或损坏')
        snapshots = {}
        if self.ctx.record.get('online_shrink'):
            from .online_shrink import OnlineShrink
            names = ('gpt-first.bin', 'gpt-last.bin')
            original = tuple((source/name).read_bytes() for name in names)
            snapshots = dict(zip(names, OnlineShrink(self).installation_gpt(original)))
        directory.mkdir(parents=True,exist_ok=False)
        atomic_json(directory/'identity.json',{**storage,'serial':android.serial,'android_slot':android.slot})
        copied_sums = {}
        for target,name in mapping.items():
            self.ctx.boundary()
            if name in snapshots:
                # The legacy preflight needs today's approved GPT; the main
                # backup remains the pre-resize recovery/uninstall baseline.
                (directory/target).write_bytes(snapshots[name])
                expected_sum = hashlib.sha256(snapshots[name]).hexdigest()
            else:
                shutil.copyfile(source/name,directory/target)
                expected_sum = sums[name]
            if sha(directory/target)!=expected_sum:raise ValueError('Legacy backup copy checksum mismatch')
            copied_sums[target] = expected_sum
        (directory/'SHA256SUMS').write_text(''.join(h+'  '+target+'\n' for target,h in copied_sums.items()),encoding='utf-8')
        self.ctx.log('Reused verified computer backups for the compatible installer; no second device backup',scope='backup')

    def wait_android(self, *_):
        self.stage('android_boot', 'waiting_device')
        raise Pending()

    def reboot(self):
        # Persist expected loss of transport BEFORE sending reboot.
        self.stage('android_boot' if self.ctx.record['plan'].get('two_stage') else 'openwrt_boot',
                   'waiting_device', reboot_manually=False)

    def push(self, call, shell, local, remote):
        return push_chunks(call, shell, local, remote, self.ctx)

    def validate_ssh(self, client):
        fingerprint = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
        if self.expected_ssh_key and fingerprint != self.expected_ssh_key:
            raise ValueError('SSH device identity changed / SSH 设备身份改变')
        if self.ctx and self.ctx.record['choices']['method'] == 'update':
            _, out, _ = client.exec_command('cat /proc/sys/kernel/random/boot_id', timeout=15)
            boot_id = out.read().decode().strip()
            if out.channel.recv_exit_status() or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',boot_id):
                raise ValueError('Cannot establish pre-update boot identity / 无法确认更新前的启动标识')
            self.ctx.record['update_boot_id'] = boot_id
            self.ctx.store.save(self.ctx.record)
            self.stage('transfer_install')
        return fingerprint

    def bind(self, serial):
        self.adb = Adb(self.root, serial)
        self.adb.context = self.ctx
        self.adb.notice = getattr(self, 'notice', None)
        self.adb.mtp_workaround = self.kind == 'mu300'
        if self.kind == 'e5':
            self.legacy.ADB = self.adb.exe
            self.legacy.SERIAL = serial
            self.legacy.SU = None
            self.legacy.find_adb = lambda: None

    def probe(self, serial, method='part'):
        if method not in ('tf', 'part', 'reinstall', 'image', 'boot', 'uninstall'):
            raise ValueError('Unsupported Android installation method / 不支持的 Android 安装方式')
        self.bind(serial)
        devices = self.adb.devices()
        entry = next((d for d in devices if d['serial'] == serial), None)
        if not entry:
            return {'serial': serial, 'readiness': 'android_boot'}
        if entry['state'] != 'device':
            return {'serial': serial, 'readiness': 'debug_authorization' if entry['state'] == 'unauthorized' else 'android_boot'}
        properties = self.adb.properties()
        device = {'serial': serial, 'model': properties['ro.product.model'], 'system': 'Android',
                  'slot': properties['ro.boot.slot_suffix'], 'root': False, 'readiness': 'android_boot'}
        device['boot_id'] = self.adb.shell('cat /proc/sys/kernel/random/boot_id').strip()
        if properties['sys.boot_completed'] != '1':
            return device
        initialized = self.adb.shell('settings get secure user_setup_complete').strip()
        if initialized == '0':
            device['readiness'] = 'android_setup'
            return device
        status = self.adb.root_status()
        device.update(root=status == 'ready', readiness=status)
        if status != 'ready':
            return device
        if self.kind == 'e5':
            self.legacy.SU = self.adb.su
            profile = self.legacy.package_profile()
            if device['model'] not in profile['android_models']:
                raise ValueError('Package model differs / 安装包机型不匹配: ' + device['model'])
            device['profile'] = profile['profile']
            if device['slot'] != '_a':
                raise ValueError('E5 requires Android slot A / E5 要求当前 Android 在 A 槽')
        else:
            device['profile'] = self.legacy.android_profile(device['model'] + '\n' + properties['ro.product.device'], properties['ro.board.platform'])
        # Trial boot uses only the boot signature, boot-chain check and misc.
        # Do not access the root filesystem to detect an installation.
        text = self.adb.shell(storage_io.inventory_script(self.root), root=True)
        storage = storage_io.parse_inventory(text)
        # Removable MMC detection remains separate from internal UFS LUNs.
        cards = self.adb.shell('cards=""; for p in /sys/class/block/mmcblk*; do k=${p##*/}; '
            'case $k in *p[0-9]*|*boot*) continue;; esac; '
            '[ "$(cat "$p/device/type" 2>/dev/null)" = SD ] || continue; cards="$cards $k"; done; '
            'set -- $cards; if [ "$#" -eq 1 ]; then '
            'if [ -b /dev/block/"${1}p1" ]; then echo /dev/block/"${1}p1"; '
            'elif [ -b /dev/block/"$1" ]; then echo /dev/block/"$1"; fi; fi',root=True).strip()
        if cards: storage['TF'] = cards
        encrypted = properties['ro.crypto.state'] == 'encrypted' or properties['ro.crypto.type'] in ('file', 'block') or storage['SOURCE'].startswith(('/dev/block/dm-', '/dev/mapper/'))
        device.update(cid=storage['CID'], storage=storage, encrypted=encrypted,
                      encryption_state=properties['ro.crypto.state'], encryption_type=properties['ro.crypto.type'],
                      existing=bool(storage.get('ROOT')),
                      tf=storage.get('TF'), userdata_bytes=storage['SIZE']*512, free_bytes=storage['FREE']*1024)
        # Other modes need only a read-only availability hint for recommendations.
        # A removable card and its readable capacity are prerequisites only for TF.
        if method == 'tf' and self.kind != 'e5' and storage.get('TF'):
            node = storage['TF'].rsplit('/', 1)[-1]
            device['tf_bytes'] = int(self.adb.shell('cat /sys/class/block/' + node + '/size', root=True).strip())*512
            disk_node = re.sub(r'p\d+$', '', node)
            device['tf_identity'] = {'cid': self.adb.shell('cat /sys/class/block/'+disk_node+'/device/cid', root=True).strip(),
                                     'sectors': device['tf_bytes']//512,
                                     'start': self.adb.shell('cat /sys/class/block/'+node+'/start 2>/dev/null || echo 0', root=True).strip()}
        if storage.get('ROOT'):
            node = storage['ROOT']
            storage['ROOT_START'], storage['ROOT_SIZE'] = map(int, self.adb.shell('cat /sys/class/block/'+node+'/start /sys/class/block/'+node+'/size', root=True).split())
        if method != 'boot':
            space = self.read_gpt(storage)
            device['gpt'] = {k: v for k, v in space.items() if k not in ('first', 'last')}
        if self.kind == 'e5' and method == 'tf':
            # Reuse the existing internal-vs-removable selection policy.
            # Do not catch discovery failures: TF must stop on missing/ambiguous cards.
            disk = self.legacy.device_sd()
            node = disk.rsplit('/', 1)[-1]
            if self.adb.shell('test -b '+disk+'p1; echo $?', root=True).strip() == '0':
                target = disk+'p1'
            else:
                parts = self.adb.shell('ls /sys/class/block/'+node+'p* 2>/dev/null; true', root=True).strip()
                if parts: raise ValueError('TF has partitions but no p1; cannot select the whole card')
                target = disk
            device['tf'] = target
            device['tf_bytes'] = int(self.legacy.device_size(target))
            device['tf_identity'] = {'cid': self.adb.shell('cat /sys/class/block/'+node+'/device/cid', root=True).strip(),
                                     'sectors': device['tf_bytes']//512,
                                     'start': self.adb.shell('cat /sys/class/block/'+target.rsplit('/',1)[-1]+'/start 2>/dev/null || echo 0', root=True).strip()}
            storage['TF'] = target
        return device

    def read_gpt(self, storage):
        disk = '/dev/block/' + storage_io.disk_node(storage['NODE'])
        sectors = storage['DISK_SIZE']
        primary_cmd,backup_cmd=storage_io.gpt_header_commands(storage)
        primary = self.adb.read(primary_cmd)
        backup = self.adb.read(backup_cmd)
        front, tail = gpt.gpt_backup_layout(primary, backup, sectors)
        first = self.adb.read(f'dd if={disk} bs=512 count={front} 2>/dev/null')
        last = self.adb.read(f'dd if={disk} bs=512 skip={tail} count={sectors-tail} 2>/dev/null')
        space = gpt.gpt_space(first, last, sectors)
        # Retain unlisted old filesystems rather than declaring their gap free.
        safe = []; signatures = []
        for start, size in space['gaps']:
            candidates = {start}
            if self.kind == 'mu300':
                # Match Android.gpt exactly: old MU300 roots may sit at a raw
                # offset inside a GPT gap, rather than at its first sector.
                end = max(p['end'] for p in space['parts'])
                candidates.update(offset for offset in (27762098176//512, (end//4096+1)*4096)
                                  if start <= offset and offset+4 <= start+size)
            occupied = False
            for offset in sorted(candidates):
                raw = self.adb.read(f'dd if={disk} bs=512 skip={offset} count=4 2>/dev/null')
                if len(raw) != 2048:
                    raise ValueError('Incomplete free-space signature read')
                kind = 'ext4' if raw[1080:1082] == b'\x53\xef' else 'f2fs' if raw[1024:1028] == bytes.fromhex('1020f5f2') else None
                if kind:
                    signatures.append({'offset':offset,'kind':kind})
                    occupied = True
            if not occupied:
                safe.append((start, size))
        space.update(first=first, last=last, safe_gaps=safe, gap_signatures=signatures,
                     largest=max((size for _, size in safe), default=0) if space['empty'] else 0)
        return space

    def backup(self, device):
        destination = self.ctx.directory / 'backup'
        if 'backup' in self.ctx.record['completed']:
            sums = json.loads((destination/'checksums.json').read_text())
            if not sums or any(sha(destination/n) != h for n, h in sums.items()):
                raise ValueError('Computer backup is damaged / 电脑备份损坏')
            return
        self.stage('backup')
        destination.mkdir(parents=True, exist_ok=True)
        storage = device['storage']
        space = self.read_gpt(storage)
        (destination/'gpt-first.bin').write_bytes(space['first'])
        (destination/'gpt-last.bin').write_bytes(space['last'])
        atomic_json(destination/'identity.json', device)
        try:
            for name in ['misc'] + [f'{n}_{slot}' for n in ('boot', 'uboot', 'sml', 'teecfg', 'trustos', 'vbmeta') for slot in ('a', 'b')]:
                self.ctx.boundary()
                self.adb.backup_partition(name, destination/(name+'.bin'), self.ctx)
        finally:
            self.adb.cleanup_backup(self.ctx)
        atomic_json(destination/'checksums.json', {p.name: sha(p) for p in destination.iterdir() if p.is_file() and p.name != 'checksums.json'})
        self.ctx.record['completed'].append('backup')
        self.ctx.store.save(self.ctx.record)

    def execute(self):
        record = self.ctx.record
        choices = record['choices']
        method = choices['method']
        if method not in ('tf', 'part', 'reinstall', 'image', 'boot', 'update') or record['plan'].get('method') != method:
            raise RecoveryRequired('Installation method differs from the saved plan / 安装方式与已保存计划不一致，已停止')
        a = args_for(record, self.ctx.directory)
        if method != 'update':
            self.bind(record['device']['serial'])
            a.adb = self.adb.exe
        self.expected_ssh_key = record['device'].get('ssh_key')
        with debug_output(self.ctx):
            if method == 'update':
                self.stage('transfer_install')
                if self.kind == 'e5':
                    return self.update_e5(a)
                # Older compatible package entries reject the native hidden
                # eMMC layout. Use the shared update path for those entries too.
                return update_mu300(vars(self.legacy), a, self.root)
            self.probe(record['device']['serial'], method)
            if record.get('legacy'):
                self.stage('recheck')
                saved = json.loads(Path(record['legacy']).read_text(encoding='utf-8'))
                if self.kind == 'mu300' and saved.get('state') == 'installed':
                    # Host checkpoint follows root, boot and misc verification.
                    # A preparation token no longer occupies the formatted root.
                    # Recheck hardware/geometry, then wait for the installed OS;
                    # never pass an installed root back to a preparation writer.
                    actual = self.probe(record['device']['serial'], method)
                    expected = saved['storage']
                    current = actual['storage']
                    if (actual['cid'] != expected['CID'] or actual['slot'].removeprefix('_') != saved['android_slot'] or
                            current.get('ROOT_START') != expected['START']+saved['target'] or current.get('ROOT_SIZE') != saved['need']):
                        raise RecoveryRequired('Installed partition differs from saved geometry / 已安装分区与记录不一致')
                    return None
                if self.kind == 'e5':
                    a.resume_part = record['legacy']
                    return self.legacy.part_two_stage('resume', a)
                a.part = False
                a.resume = True
                a.transaction = record['legacy']
                return self.legacy.install(a, self.root, json.loads((self.root/'desktop/manifest.json').read_text()))
            if installation_committed(record):
                # Final selection is after image/root readback in the legacy
                # installers. A crash before the coordinator's next checkpoint
                # must only wait for boot, never replay the full entrypoint.
                return None
            # A whole initial run must never be replayed after a write started.
            # Explicit prepared records are the only entry into destructive resume.
            if any(not name.startswith('backup') for name in record['effects']) and not (self.kind == 'e5' and method == 'tf'):
                raise RecoveryRequired('Interrupted installation has no verified resume marker; export backups for recovery / 写入中断且无可验证续装标记，请导出备份进行恢复')
            self.backup(record['device'])
            self.stage('prepare')
            if self.kind == 'mu300':
                if method == 'boot':
                    return self.boot_mu300(a)
                return self.legacy.install(a, self.root, json.loads((self.root/'desktop/manifest.json').read_text()))
            if method == 'tf':
                return self.legacy.install_sd(a)
            if method == 'image':
                return self.legacy.install(a)
            if method == 'reinstall':
                return self.legacy.part_two_stage('reinstall', a)
            if method == 'boot':
                return self.legacy.boot_openwrt(a)
            if record['plan'].get('gap_start') is not None:
                return self.e5_free(a)
            return self.legacy.install_part(a)

    def boot_mu300(self,args):
        return boot_installed_mu300(vars(self.legacy),self.legacy.Android(args),self.root)

    def e5_free(self, args):
        """Add a GPT entry in verified free space, then use the existing writer.

        Android reboot refreshes block nodes. Only a token is staged outside GPT;
        the system payload remains on the PC until the existing writer runs.
        """
        m, record = self.legacy, self.ctx.record
        s, plan = record['device']['storage'], record['plan']
        space = self.read_gpt(s)
        first, last = gpt.gpt_add_root(space['first'], space['last'], s['DISK_SIZE'], plan['gap_start'], plan['openwrt_bytes']//512, 'e5root')
        directory = self.ctx.directory/'legacy-backups/free-space'
        directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(16)
        raw = 0
        with gzip.open(self.root/'files/openwrt.ext4.gz', 'rb') as stream:
            for data in iter(lambda: stream.read(1 << 20), b''):
                raw += len(data)
        data = dict(node=s['NODE'], cid=s['CID'], start=s['START'], size=s['SIZE'])
        state = dict(version=2, phase='awaiting-android', token=token, serial=record['device']['serial'],
                     model=record['device']['model'], data=data, target=s['SIZE'], need=plan['openwrt_bytes']//512,
                     android_slot=record['device']['slot'].removeprefix('_'),
                     linux_slot='b' if record['device']['slot']=='_a' else 'a',
                     root_start=plan['gap_start'], grow=int(args.grow_fs), raw_size=raw, apn='',
                     ssid=m.default_hotspot_name(), key=secrets.token_urlsafe(12),
                     boot_sha=sha(self.root/'files/boot.img'), image_sha=sha(self.root/'files/openwrt.ext4.gz'))
        path = directory/'transaction.json'
        atomic_json(path, state)
        self.saved(path, state)
        disk = '/dev/block/'+storage_io.disk_node(s['NODE'])
        for name, content in [('before-first', space['first']), ('before-last', space['last']), ('new-first', first), ('new-last', last),
                              ('marker', ('E5-PART-PREPARED-V2 '+token+'\n').encode().ljust(512, b'\0'))]:
            local = directory/(name+'.bin')
            local.write_bytes(content)
            m.push(str(local), '/data/local/tmp/gui-'+name, name)
        front, tail = space['front'], space['tail']
        def commit():
            # Compare using files: Android shells need not support process substitution.
            check = (f'dd if={disk} of=/data/local/tmp/gui-read-first bs=512 count={front}; '
                     f'dd if={disk} of=/data/local/tmp/gui-read-last bs=512 skip={tail} count={s["DISK_SIZE"]-tail}; '
                     'cmp -s /data/local/tmp/gui-before-first /data/local/tmp/gui-read-first; '
                     'cmp -s /data/local/tmp/gui-before-last /data/local/tmp/gui-read-last; ')
            write = (f'dd if=/data/local/tmp/gui-marker of={disk} bs=512 seek={plan["gap_start"]} conv=notrunc,fsync; '
                     f'dd if=/data/local/tmp/gui-new-last of={disk} bs=512 seek={tail} conv=notrunc,fsync; '
                     f'dd if=/data/local/tmp/gui-new-first of={disk} bs=512 conv=notrunc,fsync')
            m.su_checked('Creating root partition / 创建独立分区', check+write)
            if self.adb.read(f'dd if={disk} bs=512 count={front} 2>/dev/null') != first or self.adb.read(
                    f'dd if={disk} bs=512 skip={tail} count={s["DISK_SIZE"]-tail} 2>/dev/null') != last:
                raise RecoveryRequired('GPT readback differs; do not reboot / GPT 回读不一致，请勿重启')
        self.action('gpt-free-space', commit)
        self.reboot()
        self.adb.call('reboot')
        raise Pending()

    def update_e5(self, args):
        helper = load(self.root/'scripts/tools/e5-update.py', '_update')
        helper.GUI = self
        helper.input = self.console_input_unavailable
        raw_size, raw_hash = self.legacy.gz_metadata(str(self.root/'files/openwrt.ext4.gz'))
        previous = sys.argv
        try:
            sys.argv = ['e5-update', '--package', str(self.root), '--host', args.host,
                        '--raw-size', str(raw_size), '--raw-sha256', raw_hash]
            return helper.main()
        finally:
            sys.argv = previous
