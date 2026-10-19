"""NDJSON command server and persistent coordinator. No generic exec command."""
from contextlib import ExitStack
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import threading
import time
import traceback
import zipfile

from .adapter import Adapter, installation_committed
from .package import inspect_package
from .state import Cancelled, Context, DeviceLock, Pending, RecoveryRequired, Store, compatible
from .transport import Adb, wireless_serial
from .uninstall import Uninstaller, prepare as prepare_uninstall, persist as persist_uninstall
from .uninstall_growth import PreservationUnavailable

METHODS = {'tf', 'part', 'reinstall', 'image', 'boot', 'update', 'uninstall'}


def plan_install(package, device, choices):
    method = choices.get('method')
    if method not in METHODS:
        raise ValueError('Unknown installation method')
    if device.get('readiness') != 'ready':
        raise ValueError('Device is not ready / 设备尚未就绪')
    if method == 'uninstall':
        raise ValueError('Uninstall requires device evidence planning / 卸载需要专用的设备证据检查与卸载计划')
    if method == 'update':
        return {'method': method, 'wipe': False, 'openwrt_bytes': device['capacity'], 'two_stage': False,
                'preserves_settings': True, 'host': choices['host'], 'grow': True}
    size = choices.get('size_gib', 4)
    if type(size) not in (float, int) or not math.isfinite(size) or size <= 0:
        raise ValueError('Size must be a positive number / 容量必须为正数')
    size_bytes = math.ceil(size*1024/4)*4*(1 << 20)
    plan = {'method': method, 'wipe': False, 'two_stage': False, 'openwrt_bytes': size_bytes,
            'android_bytes': device['userdata_bytes'],
            'grow': choices.get('grow', True) if method in ('part','reinstall') else True,
            'target': None, 'data_impact': method}
    storage = device['storage']
    if method == 'boot':
        plan.update(openwrt_bytes=0, target='misc', temporary=True)
    elif method == 'tf':
        if not device.get('tf'):
            raise ValueError('No TF card / 未检测到 TF 卡')
        plan.update(target=device['tf'], openwrt_bytes=device['tf_bytes'])
        plan['target_identity'] = device.get('tf_identity')
        if plan['openwrt_bytes'] < 2 << 30:
            raise ValueError('TF target is smaller than the system image / TF 分区小于系统镜像')
    elif method == 'reinstall':
        if not device.get('existing'):
            raise ValueError('No existing internal partition / 没有可重装的内置分区')
        plan.update(target=storage['ROOT'], openwrt_bytes=storage['ROOT_SIZE']*512)
    elif method == 'image':
        if device['encrypted']:
            raise ValueError('Encrypted userdata cannot boot an image / 加密 userdata 不支持镜像启动')
        uploads = sum((Path(package['path'])/name).stat().st_size for name in package['payloads'])
        maximum = device['free_bytes'] - (1536 << 20) - uploads - (2 << 30 if package['adapter'] == 'mu300' else 0)
        if size_bytes < 2 << 30 or size_bytes > maximum:
            raise ValueError(f'Image requires staging space and Android reserve; maximum {max(0,maximum)/(1<<30):.2f} GiB / 镜像大小超出含临时空间与 Android 预留的范围')
        plan.update(target='userdata-image', maximum_bytes=maximum, experimental=True)
    else:
        if device.get('existing'):
            raise ValueError('Internal partition exists; choose update or reinstall / 已存在内置分区，请选择更新或重装')
        if size_bytes < 2 << 30:
            raise ValueError('OpenWrt needs at least 2 GiB / OpenWrt 至少需要 2 GiB')
        candidates = [(n, a) for a, n in device['gpt']['safe_gaps'] if n*512 >= size_bytes]
        gap = max(candidates)[1] if candidates and device['gpt']['empty'] else None
        if gap is not None:
            plan.update(gap_start=gap, target='GPT unallocated space', two_stage=package['adapter'] == 'e5')
        else:
            wipe = device['encrypted'] or bool(choices.get('wipe'))
            android = device['userdata_bytes'] - size_bytes
            used = device['userdata_bytes'] - device['free_bytes']
            if android < (2 << 30) or (not wipe and android < used+(2 << 30)):
                raise ValueError('Insufficient Android retained space / Android 保留空间不足')
            plan.update(wipe=wipe, two_stage=wipe, android_bytes=android, target='userdata shrink')
            plan['offline'] = True
    return plan


class Service:
    def __init__(self, package_root, store=None, output=None):
        self.package_root = str(Path(package_root).resolve())
        self.store = store or Store()
        self.output = output or sys.stdout
        self.write_lock = threading.Lock()
        self.active = None
        self.worker = None
        self.package = None
        self.uninstall_reset_prompt = None
        self.adapters = Adapter
        self.stop = threading.Event()
        self.verification_lock = threading.Lock()
        self.verification_request = None
        self.verification_wake = threading.Event()

    def emit(self, kind, data, request_id=None):
        event = {'protocol': 1, 'type': kind, 'data': data}
        if request_id is not None:
            event['id'] = request_id
        with self.write_lock:
            self.output.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.output.flush()

    def adapter(self, package=None, context=None):
        adapter = self.adapters(package or self.package, context)
        adapter.notice = lambda data:self.emit('notice', data)
        return adapter

    def adb(self):
        adb = Adb(self.package_root)
        adb.notice = lambda data:self.emit('notice', data)
        return adb

    def public(self, record):
        return Context(self.store, record, lambda *_: None).snapshot()

    def dispatch(self, request):
        if request.get('protocol') != 1:
            raise ValueError('Protocol version mismatch')
        command, p = request.get('command'), request.get('params', {})
        if not isinstance(p, dict):
            raise ValueError('Parameters must be an object')
        if command == 'hello':
            return {'protocol': 1, 'app_data': str(self.store.base), 'package_path': self.package_root,
                    'transactions': [self.public(r) for r in self.store.list()]}
        if command == 'snapshot':
            return self.active.snapshot() if self.active else None
        if command == 'list':
            return [self.public(r) for r in self.store.list()]
        if command == 'cancel':
            if self.active and self.worker and self.worker.is_alive():
                self.active.cancel = True
                self.active.record['cancel_requested'] = True
                self.store.save(self.active.record)
                return {'accepted': True, 'deferred': self.active.record['unsafe']}
            return {'accepted': False}
        if command == 'can_close':
            return not self.worker or not self.worker.is_alive()
        if command == 'verify_openwrt':
            context = self.active
            if (not context or not self.worker or not self.worker.is_alive() or
                    p.get('transaction') != context.record['id'] or context.record['unsafe'] or
                    context.record['stage'] != 'openwrt_boot' or
                    context.record['status'] not in ('waiting_user','waiting_device')):
                raise ValueError('OpenWrt verification is unavailable at this stage / 当前阶段不能确认 OpenWrt 启动')
            host = p.get('host', '')
            password = p.get('password', '')
            if not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}',host):
                raise ValueError('Enter an IP address or hostname / 请输入 IP 地址或主机名')
            if not isinstance(password,str) or len(password)>4096:
                raise ValueError('Invalid SSH password / SSH 密码无效')
            with self.verification_lock:
                self.verification_request = (context.record['id'], host, password)
                self.verification_wake.set()
            return {'accepted':True}
        if command == 'diagnostics':
            record = self.store.load(p['transaction'])
            ctx = Context(self.store, record, lambda *_:None)
            header = {key:record.get(key) for key in ('version','revision','stage','status','choices','plan','completed','error','capacity_check','android_capacity_check')}
            header['model'] = record['device'].get('model')
            header['package'] = {key:record['package'].get(key) for key in ('adapter','profile','kernel','layout')}
            parts = ['OpenWrt Installer diagnostics (redacted)\n'+ctx.sanitize(json.dumps(header,ensure_ascii=False,indent=2))]
            for name in ('debug.previous.ndjson','debug.ndjson'):
                path = ctx.directory/name
                if path.is_file():
                    with path.open('rb') as stream:
                        size = stream.seek(0, 2)
                        stream.seek(max(0, size-512*1024))
                        parts.append(name+'\n'+stream.read().decode('utf-8','replace'))
            if len(parts)==1:parts.append('This older transaction has no debug log. Retry with the current installer to record detailed diagnostics.')
            return {'text':'\n\n'.join(parts)}
        if command == 'adb_connect':
            if self.worker and self.worker.is_alive():
                record = self.active.record if self.active else {}
                if (record.get('unsafe') or record.get('status') not in ('waiting_user','waiting_device') or
                        record.get('stage') not in ('uninstall_offline','android_boot','android_setup','usb_debugging',
                                                    'debug_authorization','magisk','root_permission')):
                    raise ValueError('Pause before changing ADB connections / 请先暂停任务，再更改 ADB 连接')
            return self.adb().connect(p.get('address'))
        if command == 'adb_disconnect':
            if self.worker and self.worker.is_alive():
                raise ValueError('Pause before disconnecting a device / 请先暂停任务，再断开设备')
            serial = p.get('serial')
            if not wireless_serial(serial):
                raise ValueError('Select a wireless ADB device / 请选择无线 ADB 设备')
            with ExitStack() as locks:
                try:
                    locks.enter_context(DeviceLock(self.store.base, serial))
                except (OSError, RuntimeError):
                    raise ValueError('Another installer is using this device; pause it first / 另一个安装器正在操作此设备，请先暂停其任务') from None
                return self.adb().disconnect(serial)
        if self.worker and self.worker.is_alive() and command not in ('export',):
            raise ValueError('An operation is active / 任务正在执行')
        if command == 'package':
            self.package = inspect_package(p.get('path') or self.package_root)
            self.package_root = self.package['path']
            return self.package
        if command == 'devices':
            return self.adb().devices()
        if command == 'probe':
            if not self.package:
                raise ValueError('Verify a package first / 请先校验安装包')
            if not isinstance(p.get('serial'), str) or not p['serial']:
                raise ValueError('Select a device / 请选择设备')
            return self.adapter().probe(p['serial'], p.get('method', 'part'))
        if command == 'probe_update':
            return self.probe_update(p)
        if command == 'plan':
            if not self.package:
                raise ValueError('Verify package first')
            choices = p['choices']
            if choices.get('method') == 'update':
                device = self.probe_update(p)
            elif choices.get('method') == 'uninstall':
                uninstall_adapter = self.adapter()
                device = uninstall_adapter.probe(p['serial'], 'uninstall')
            else:
                plan_adapter = self.adapter()
                device = plan_adapter.probe(p['serial'], choices['method'])
            if choices.get('method') != 'update' and device.get('readiness') == 'ready':
                active_slot = device.get('slot')
                if active_slot not in ('_a','_b'):
                    raise ValueError('Cannot identify current Android slot')
                probe_adapter = uninstall_adapter if choices.get('method') == 'uninstall' else plan_adapter
                other = 'b' if active_slot == '_a' else 'a'
                header = probe_adapter.adb.read('dd if=/dev/block/by-name/boot_'+other+' bs=512 count=8 2>/dev/null')
                if len(header) != 4096:
                    raise ValueError('Incomplete opposite boot header / 另一启动槽读取不完整')
                pending = any(item['choices'].get('method') == 'uninstall' and item['device'].get('cid') == device.get('cid')
                              and item['status'] != 'completed' and item['effects'].get('uninstall-arm') for item in self.store.list())
                if pending or b'openwrt.uninstall=1' in header[44:1580].split(b'\0',1)[0].split():
                    raise RecoveryRequired('Uninstall is already pending. Resume/import its saved record; do not create another operation. / 已有未完成的卸载，请继续或导入卸载记录，不要创建新操作。')
            if choices.get('method') == 'uninstall':
                def authorize_reset(fingerprint):
                    expected = self.uninstall_reset_prompt
                    return bool(expected and p.get('reset_token') == expected['token'] and fingerprint == expected['fingerprint'])
                try:
                    plan, uninstall_metadata, uninstall_files = prepare_uninstall(uninstall_adapter,self.store,device,authorize_reset)
                except PreservationUnavailable as error:
                    token = secrets.token_urlsafe(24)
                    self.uninstall_reset_prompt = {'token':token,'fingerprint':error.fingerprint}
                    return {'confirmation_required':'format_android','reason':str(error),'reset_token':token,
                            'model':device['model'],'serial_hint':device['serial'][-4:]}
                self.uninstall_reset_prompt = None
            else:
                plan = plan_install(self.package, device, choices)
            record = self.store.create(self.package, device, choices, plan)
            record['data_directory'] = str(self.store.directory(record['id']))
            record['secrets'] = {'wifi_key': secrets.token_urlsafe(12)}
            self.store.save(record)
            if choices.get('method') == 'uninstall':
                persist_uninstall(Context(self.store, record, lambda *_:None), uninstall_metadata, uninstall_files)
            return self.public(record)
        if command in ('start', 'resume', 'retry'):
            record = self.store.load(p['transaction'])
            if record['status'] == 'completed':
                return self.public(record)
            if (record['choices'].get('method') == 'uninstall' and record['status'] == 'ready' and
                    record.get('uninstall',{}).get('version') == 1 and not record['effects'].get('uninstall-arm')):
                raise ValueError('Regenerate the old removal plan to check data preservation / 请重新生成旧版卸载计划，先检查能否保留 Android 数据')
            current = inspect_package(p.get('path') or self.package_root)
            compatible(record['package'], current)
            record['package'] = current
            # A final confirmation is bound to this already persisted plan.
            if command == 'start' and p.get('confirmed') is not True:
                raise ValueError('Confirm the displayed plan / 请确认安装计划')
            if command != 'start' and record['status'] == 'ready':
                raise ValueError('This plan has not been confirmed / 此计划尚未确认')
            if (command != 'start' and record['choices']['method'] == 'uninstall' and
                    p.get('serial') and p['serial'] != record['device']['serial']):
                self.rebind_uninstall(record, p['serial'], current)
            self.store.save(record)
            context = Context(self.store, record, self.emit)
            context.private.append(p.get('password', ''))
            adapter = self.adapter(current, context)
            adapter.password = p.get('password', '')
            context.cancel = False
            record['cancel_requested'] = False
            record['error'] = None
            self.active = context
            with self.verification_lock:
                self.verification_request = None
                self.verification_wake.clear()
            self.worker = threading.Thread(target=self.run, args=(context, adapter), daemon=False)
            self.worker.start()
            return self.public(record)
        if command == 'export':
            record = self.store.load(p['transaction'])
            directory = self.store.directory(record['id'])
            # Export path is selected by native dialog, archive paths are fixed.
            target = Path(p['path'])
            if target.exists():
                raise ValueError('Export destination exists / 导出文件已存在')
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in directory.rglob('*'):
                    if path.is_file():
                        archive.write(path, path.relative_to(directory).as_posix())
            return {'path': str(target)}
        if command == 'import':
            # Import full directories, never extract arbitrary ZIP paths here.
            directory = Path(p['path']).resolve()
            record = json.loads((directory/'transaction.json').read_text(encoding='utf-8'))
            if record.get('version') != 1:
                raise ValueError('Unsupported transaction version')
            target = self.store.directory(record['id'])
            if target.exists():
                raise ValueError('Transaction already exists')
            if record.get('legacy'):
                # macOS /var and /private/var can refer to the same directory.
                relative = Path(record['legacy']).resolve().relative_to(Path(record.get('data_directory', directory)).resolve())
                record['legacy'] = str(target/relative)
            record['data_directory'] = str(target)
            shutil.copytree(directory, target)
            self.store.save(record)
            return self.public(record)
        raise ValueError('Unsupported command / 不支持的命令')

    def rebind_uninstall(self, record, serial, package):
        """Explicit paused-resume transport selection, bound to the original hardware."""
        if not isinstance(serial,str) or not serial or len(serial)>512 or '\0' in serial:
            raise ValueError('Select a connected device / 请选择已连接的设备')
        with ExitStack() as locks:
            for identity in sorted({serial,record['device']['cid']}):
                locks.enter_context(DeviceLock(self.store.base,identity))
            actual = self.adapter(package).probe(serial,'uninstall')
            if actual.get('readiness') != 'ready':
                raise ValueError('Finish Android setup, debugging and root before selecting the new connection / 请先完成 Android 初始化、调试授权和 root，再选择新连接')
            if any(actual.get(key) != record['device'].get(key) for key in ('cid','profile','slot')):
                raise RecoveryRequired('Selected connection belongs to another device or Android slot / 所选连接并非原设备或 Android 槽位不符')
            record.setdefault('transport_history',[]).append({'serial':record['device']['serial'],'changed_at':time.time()})
            record['device']['serial'] = serial
            self.store.save(record)

    def probe_update(self, p):
        import paramiko
        if not self.package:
            raise ValueError('Verify package first')
        choices = p.get('choices', p)
        host = choices.get('host', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host):
            raise ValueError('Enter an IP address or hostname / 请输入 IP 地址或主机名')
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, username='root', password=p.get('password', ''), timeout=12,
                           auth_timeout=15, look_for_keys=False, allow_agent=False)
            key = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
            def read_bytes(script):
                _, out, err = client.exec_command(script, timeout=15)
                data = out.read()
                if out.channel.recv_exit_status():
                    raise ValueError('OpenWrt preflight failed / OpenWrt 预检失败')
                return data
            def read(script):return read_bytes(script).decode('utf-8','replace').strip()
            if read('id -u') != '0':
                raise ValueError('SSH root required')
            kind = self.package['adapter']
            profile = read('cat /etc/e5/device-profile' if kind == 'e5' else 'cat /run/mu300/device')
            if kind == 'e5' and profile != self.package['profile'] or kind == 'mu300' and profile not in ('f50', 'v50', 'u30air'):
                raise ValueError('Wrong OpenWrt profile / OpenWrt 机型不匹配')
            cid = read('cat /sys/class/block/mmcblk0/device/cid')
            if not re.fullmatch('[a-f0-9]{32}', cid):
                raise ValueError('Cannot establish OpenWrt eMMC identity')
            capacity = int(read("df -Pk / | awk 'END {print $2}'"))*1024
            root_dev = read('cat /run/mu300/disk-dev' if kind == 'mu300' else
                            "readlink -f $(awk '$2==\"/\" {print $1}' /proc/mounts | tail -1)")
            if re.fullmatch(r'/dev/(?:block/)?(?:mmcblk\d+(?:p\d+)?|loop\d+)', root_dev):
                raw = read_bytes(f'dd if={root_dev} bs=1024 skip=1 count=1 2>/dev/null')
                capacity = verify_ext4_capacity(raw, 0)
            return dict(serial='ssh:'+key, cid=cid, ssh_key=key, host=host, profile=profile,
                        model=profile.upper(), system='OpenWrt', slot=read('cat /run/mu300/linux-slot') if kind == 'mu300' else 'B',
                        root=True, readiness='ready', capacity=capacity)
        finally:
            client.close()

    def run(self, context, adapter):
        r = context.record
        try:
            with ExitStack() as locks:
                for identity in sorted({r['device']['serial'], r['device']['cid']}):
                    locks.enter_context(DeviceLock(self.store.base, identity))
                if r['choices']['method'] == 'uninstall':
                    return Uninstaller(self, context, adapter).run()
                if r['stage'] in ('openwrt_boot', 'verify_capacity') or installation_committed(r):
                    self.openwrt_wait_stage(context)
                    return self.wait_openwrt(context, adapter)
                if r.get('legacy'):
                    self.wait_android(context, adapter)
                else:
                    if r['choices']['method'] != 'update':
                        actual = adapter.probe(r['device']['serial'], r['choices']['method'])
                        if actual.get('cid') != r['device']['cid'] or actual.get('slot') != r['device']['slot']:
                            raise ValueError('Device identity or Android slot changed since plan / 设备或 Android 槽位与计划不一致')
                        # Recompute immediately before execution; do not accept stale TF geometry or GPT gaps.
                        fresh = plan_install(r['package'], actual, r['choices'])
                        for key in ('wipe', 'target', 'openwrt_bytes', 'android_bytes', 'gap_start', 'target_identity'):
                            if fresh.get(key) != r['plan'].get(key):
                                raise ValueError('Installation plan changed; inspect and confirm again / 安装计划发生变化，请重新检查并确认: '+key)
                context.stage('prepare')
                try:
                    result = adapter.execute()
                except Pending:
                    self.wait_android(context, adapter)
                    result = adapter.execute()
                if result is not None and getattr(result, 'state', '') == 'pending' and r.get('legacy'):
                    self.wait_android(context, adapter)
                    result = adapter.execute()
                self.openwrt_wait_stage(context)
                self.wait_openwrt(context, adapter)
        except Cancelled:
            context.stage(r['stage'], 'cancelled')
        except BaseException as error:
            context.private.append(adapter.password)
            context.log(''.join(traceback.format_exception(error)), level='error', scope='exception')
            message = context.sanitize(f'{type(error).__name__}: {error}')[:2000]
            for private in (r['device'].get('serial'), r['device'].get('cid'), adapter.password,
                            r.get('secrets', {}).get('wifi_key')):
                if private: message = message.replace(private, '[redacted]')
            r['error'] = {'code': ('recovery_required' if isinstance(error, RecoveryRequired) else
                                   'capacity_mismatch' if isinstance(error, FilesystemCapacityError) else 'operation_failed'),
                          'message': message,
                          'retryable': not r['unsafe'] or bool(r.get('legacy')) or r['choices']['method'] == 'uninstall'}
            context.stage(r['stage'], 'failed')

    def wait_android(self, context, adapter):
        r = context.record
        # No deadline: initialization, debugging and Magisk are user paced.
        while True:
            context.boundary()
            try:
                device = adapter.probe(r['device']['serial'], r['choices']['method'])
                readiness = device.get('readiness', 'android_boot')
                if readiness == 'ready':
                    if device.get('cid') != r['device']['cid'] or device.get('profile') != r['device']['profile']:
                        raise RecoveryRequired('Reconnected device differs / 重连设备身份不一致')
                    if r['plan'].get('two_stage') and r['device'].get('boot_id') and device.get('boot_id') == r['device']['boot_id']:
                        context.stage('android_boot', 'waiting_device')
                        if self.stop.wait(3): raise Cancelled()
                        continue
                    context.stage('root_ready')
                    return
                context.stage(readiness, 'waiting_device' if readiness == 'android_boot' else 'waiting_user')
            except (ConnectionError, OSError, TimeoutError, __import__('subprocess').TimeoutExpired):
                context.stage('android_boot', 'waiting_device')
            if self.stop.wait(3):
                raise Cancelled()

    def openwrt_wait_stage(self, context, error=None, connection_state=None):
        r = context.record
        # These entrypoints send their own reboot request. The other install
        # paths finish after verified writes and explicitly leave reboot to the user.
        manual = r['choices']['method'] != 'update' and not (
            r['package']['adapter'] == 'e5' and r['choices']['method'] == 'boot')
        manual = manual and not r.get('openwrt_confirmed',False)
        host = r['choices'].get('host') or ('192.168.9.1' if r['package']['adapter'] == 'e5' else
                '192.168.77.1')
        context.stage('openwrt_boot', 'waiting_user' if manual or error else 'waiting_device',
                      host=host, reboot_manually=manual, boot_confirmed=bool(r.get('openwrt_confirmed')),
                      verification_error=error, connection_state=connection_state)

    def wait_openwrt(self, context, adapter):
        import paramiko
        r = context.record
        host = r['choices'].get('host') or ('192.168.9.1' if r['package']['adapter'] == 'e5' else
                '192.168.77.1')
        # No network/interface changes and no automatic hotspot connection.
        while True:
            context.boundary()
            with self.verification_lock:
                pending = self.verification_request
                self.verification_request = None
                self.verification_wake.clear()
            if pending and pending[0] == r['id']:
                _, host, adapter.password = pending
                context.private.append(adapter.password)
                r['choices']['host'] = host
                r['openwrt_confirmed'] = True
                self.openwrt_wait_stage(context)
                context.log('User confirmed OpenWrt boot; checking the selected management address', scope='verification')
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(host, username='root', password=adapter.password or 'root', timeout=5,
                               auth_timeout=5, look_for_keys=False, allow_agent=False)
                def read_bytes(script):
                    _, out, _ = client.exec_command(script, timeout=15)
                    value = out.read()
                    status = out.channel.recv_exit_status()
                    if status < 0:
                        raise EOFError('SSH closed before a command exit status was received')
                    if status:
                        raise ValueError('Final verification command failed')
                    return value
                def read(script):return read_bytes(script).decode().strip()
                cid = read('cat /sys/class/block/mmcblk0/device/cid')
                if cid != r['device']['cid']:
                    raise RecoveryRequired('Management address belongs to another device / 管理地址对应另一台设备')
                if r['choices']['method'] == 'update':
                    before = r.get('update_boot_id')
                    if not before:
                        raise ValueError('Update record lacks pre-update boot evidence; automatic completion cannot be verified / 旧更新记录缺少重启前标识，无法自动确认更新完成，请保留记录人工复核')
                    boot_id = read('cat /proc/sys/kernel/random/boot_id')
                    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',boot_id):
                        raise ValueError('Cannot verify post-update boot identity / 无法校验更新后的启动标识')
                    if boot_id == before:
                        raise Pending()
                context.stage('verify_capacity')
                # SSH exec preserves raw stdout bytes; no encoding/od/SFTP.
                if r['package']['adapter'] == 'e5':
                    dev = read("readlink -f $(awk '$2==\"/\" {print $1}' /proc/mounts | tail -1)")
                else:
                    dev = read('cat /run/mu300/disk-dev')
                if not re.fullmatch(r'/dev/(?:block/)?(?:mmcblk\d+(?:p\d+)?|loop\d+)', dev):
                    raise ValueError('Cannot identify installed root block device')
                raw = read_bytes(f'dd if={dev} bs=1024 skip=1 count=1 2>/dev/null')
                # Older records may store the growth choice only in choices.
                # Never interpret an explicit no-grow installation as a failed resize.
                grow = (r['plan'].get('grow', r['choices'].get('grow', True))
                        if r['choices']['method'] in ('part', 'reinstall') else True)
                expected = r['plan']['openwrt_bytes'] if grow else 0
                capacity = verify_ext4_capacity(raw, 0)
                r['capacity_check'] = {'device': dev, 'filesystem_bytes': capacity,
                                       'allocated_bytes': r['plan']['openwrt_bytes'],
                                       'expected_bytes': expected, 'grow': grow,
                                       'adapter': r['package']['adapter'], 'model': r['device']['model'],
                                       'method': r['choices']['method'], 'status': 'checking'}
                try:
                    verify_ext4_capacity(raw, expected)
                except FilesystemCapacityError:
                    r['capacity_check']['status'] = 'mismatch'
                    raise
                else:
                    r['capacity_check']['status'] = 'verified'
                finally:
                    self.store.save(r)
                    context.log(json.dumps(r['capacity_check'], ensure_ascii=False), scope='capacity')
                # Mounted and usable, not just a larger partition entry.
                read('test -s /etc/openwrt_release && test -x /sbin/init')
                r['result'] = {'filesystem_bytes': capacity, 'url': 'http://'+host,
                               'backup_directory': str(context.directory/'backup'), 'model': r['device']['model'],
                               'temporary': r['choices']['method'] == 'boot'}
                context.stage('complete', 'completed')
                return
            except Pending:
                # The old runtime can still answer during the scheduled reboot
                # delay. It is not proof that the new system has started.
                self.openwrt_wait_stage(context)
            except RecoveryRequired as error:
                # The default address can belong to another router. Keep all
                # writes blocked, but let the user select the intended endpoint.
                self.openwrt_wait_stage(context, str(error))
            except ValueError:
                raise
            except paramiko.AuthenticationException:
                self.openwrt_wait_stage(context, 'SSH authentication failed; check the root password / SSH 认证失败，请核对 root 密码')
            except (OSError, EOFError, paramiko.SSHException) as error:
                # Transport loss is expected during restart, including refused
                # connections, missing interfaces, timeouts and SSH EOF. Keep
                # retrying without a user-facing warning or arbitrary deadline.
                context.log(f'Waiting for OpenWrt reconnection: {type(error).__name__}: {error}', scope='reconnect')
                self.openwrt_wait_stage(context, connection_state='reconnecting')
            finally:
                client.close()
            for _ in range(40):
                if self.verification_wake.is_set():break
                if self.stop.wait(.1):raise Cancelled()


class FilesystemCapacityError(ValueError):
    def __init__(self, actual, expected):
        self.actual, self.expected = actual, expected
        super().__init__(
            f'Filesystem capacity differs from allocation / 文件系统容量与分配不符: {actual} != {expected}. '
            f'Actual / 实际 {actual/(1 << 30):.2f} GiB; expected / 预期 {expected/(1 << 30):.2f} GiB. '
            'OpenWrt has started, but capacity verification is incomplete. Keep the system and backups; '
            'copy diagnostics for review. Retrying only rechecks capacity. / '
            'OpenWrt 已启动，但容量校验未通过。请保留当前系统和备份，复制诊断日志；重试只复核容量。')


def verify_ext4_capacity(superblock, expected):
    import struct
    if len(superblock) < 340 or superblock[56:58] != b'\x53\xef':
        raise ValueError('Installed filesystem is not ext4 / 已安装文件系统不是 ext4')
    blocks = struct.unpack_from('<I', superblock, 4)[0] | (struct.unpack_from('<I', superblock, 336)[0] << 32)
    shift = struct.unpack_from('<I', superblock, 24)[0]
    if shift > 6:
        raise ValueError('Invalid ext4 block size')
    capacity = blocks*(1024 << shift)
    if capacity <= 0:
        raise ValueError('Invalid ext4 filesystem capacity / ext4 文件系统容量无效')
    if expected and not expected-(4 << 20) <= capacity <= expected:
        raise FilesystemCapacityError(capacity, expected)
    return capacity


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--package-root', required=True)
    p.add_argument('--data-dir')
    a = p.parse_args()
    output = sys.stdout
    service = Service(a.package_root, Store(a.data_dir), output)
    # Legacy prints become sanitized diagnostic events; protocol replies use
    # this captured stream even while legacy code redirects stdout.
    for line in sys.stdin:
        if len(line) > 1024*1024:
            continue
        request = {}
        try:
            request = json.loads(line)
            value = service.dispatch(request)
            service.emit('response', {'ok': True, 'result': value}, request.get('id'))
        except Exception as error:
            from .diagnostics import redact
            params = request.get('params', {})
            private = [params.get(key) for key in ('serial','password')] if isinstance(params,dict) else []
            message = redact(f'{type(error).__name__}: {error}',private)[:2000]
            service.emit('response', {'ok': False, 'error': message}, request.get('id'))
    # EOF requests safe cancellation; never kill a storage operation.
    if service.active:
        service.active.cancel = True
    service.stop.set()
    if service.worker:
        service.worker.join()


if __name__ == '__main__':
    main()
