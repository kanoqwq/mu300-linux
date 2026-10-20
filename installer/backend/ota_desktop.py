"""Diff generator and SSH updater shared by the portable Tauri frontend."""
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import tempfile
import time
import zipfile

from .ota_engine import Patch, digest
from .ota_package import analyze


def execute(client, command, timeout=1800):
    channel = client.get_transport().open_session(timeout=20)
    channel.exec_command(command)
    out, err = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
        if channel.recv_ready():
            out.extend(channel.recv(65536))
        if channel.recv_stderr_ready():
            err.extend(channel.recv_stderr(65536))
        if len(out) + len(err) > 32 << 20:
            channel.close()
            raise ValueError('Device response exceeded limit')
        if time.monotonic() > deadline:
            channel.close()
            raise TimeoutError('Device update is still running; reconnect and check status')
        time.sleep(.02)
    code = channel.recv_exit_status()
    if code:
        raise RuntimeError(out.decode('utf-8', 'replace') + err.decode('utf-8', 'replace'))
    return out.decode('utf-8', 'replace')


def connect(host, password):
    import paramiko
    if not isinstance(host, str) or not host or len(host) > 253 or any(c.isspace() for c in host):
        raise ValueError('Enter the device management address')
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username='root', password=password, look_for_keys=False, allow_agent=False,
                       timeout=15, banner_timeout=15, auth_timeout=15)
    except BaseException:
        client.close()
        raise
    return client


def remote_prepare(client, archive, emit=lambda *_: None):
    with Patch(archive) as patch:
        destination = '/etc/unisoc-ota/upload-' + patch.id + '.ota'
        total = Path(archive).stat().st_size
        free = int(execute(client, "df -Pk /etc | awk 'END {print $4}'").strip()) * 1024
        if total + (32 << 20) > free:
            raise ValueError('Insufficient device staging space')
        execute(client, 'umask 077; mkdir -p /etc/unisoc-ota')
        channel = client.get_transport().open_session(timeout=20)
        channel.exec_command('umask 077; cat > ' + shlex.quote(destination + '.new'))
        sent = 0
        with Path(archive).open('rb') as stream:
            expected = digest(stream)
            stream.seek(0)
            for block in iter(lambda: stream.read(128 << 10), b''):
                channel.sendall(block)
                sent += len(block)
                emit('ota_progress', dict(stage='upload', done=sent, total=total))
        channel.shutdown_write()
        if channel.recv_exit_status() != 0:
            raise ValueError('Device upload failed')
        got = execute(client, 'sha256sum ' + shlex.quote(destination + '.new')).split()[0]
        if got != expected:
            raise ValueError('Device upload checksum mismatch')
        execute(client, 'mv ' + shlex.quote(destination + '.new') + ' ' + shlex.quote(destination))
        # Bootstrap runs from the reviewed archive's own isolated RAM runtime;
        # the old image need not have Python or the OTA plugin installed.
        if not patch.manifest.get('runtime'):
            raise ValueError('This patch does not contain a portable updater runtime')
        # Exclusive creation gives us a new root-owned directory without
        # depending on stat (absent in reduced OpenWrt BusyBox builds). Never
        # reuse a predictable directory or execute a helper from an old one.
        runtime = '/tmp/unisoc-ota-'+secrets.token_hex(32)
        execute(client, 'umask 077; mkdir '+shlex.quote(runtime))
        for key, name in (('busybox', 'busybox'), ('bootstrap', 'bootstrap.sh')):
            desc = patch.manifest[key]
            content = patch.archive.read('blobs/'+desc['sha256'])
            channel = client.get_transport().open_session(timeout=20)
            channel.exec_command('umask 077; cat > '+shlex.quote(runtime+'/'+name))
            channel.sendall(content); channel.shutdown_write()
            if channel.recv_exit_status() != 0: raise ValueError('Runtime helper upload failed')
            got = execute(client, 'sha256sum '+shlex.quote(runtime+'/'+name)).split()[0]
            if got != desc['sha256']: raise ValueError('Runtime helper checksum mismatch')
        execute(client, 'chmod 700 '+shlex.quote(runtime+'/busybox'))
        return dict(path=destination, runtime=runtime)


def runtime_call(client, session, command, setup=False):
    home = session['runtime']
    if setup:
        shell = ' '.join(map(shlex.quote, [home+'/busybox', 'sh', home+'/bootstrap.sh', session['path'], command, home]))
    else:
        shell = 'UNISOC_OTA_ROOT=/target UNISOC_OTA_RAM=/ UNISOC_OTA_LIVE=1 PYTHONHOME=/usr '+shlex.quote(home+'/busybox')+' chroot '+shlex.quote(home+'/root')+' /usr/bin/python3 /ota/rpc.py '+shlex.quote(command)
    return json.loads(execute(client, shell))


def remote_inspect(client, archive, emit=lambda *_: None):
    session = remote_prepare(client, archive, emit)
    return dict(**session, check=runtime_call(client, session, 'inspect', setup=True))


def refresh_rpc(client):
    execute(client, '/etc/init.d/rpcd reload', timeout=15)
    deadline = time.monotonic()+5
    while True:
        try:
            value = json.loads(execute(client, 'ubus call unisoc-ota status', timeout=3))
            if 'identity' in value and not value.get('error'):
                return {'status': 'ready'}
        except (RuntimeError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise ValueError('unisoc-ota RPC did not register after reload')
        time.sleep(.1)



class Desktop:
    def __init__(self, root, emit):
        self.root = Path(root)
        self.emit = emit
        self.analysis = None
        self.review = None
        self.runtime_session = None
        self.archive_path = None
        self.activated = set()
        self.pending_reboot = None
        self.last_event = 0

    def progress(self, stage, path):
        if time.monotonic() - self.last_event >= .4:
            self.last_event = time.monotonic()
            self.emit('ota_progress', dict(stage=stage, path=path))

    def dispatch(self, command, p):
        self.emit('ota_activity', {'busy': True})
        try:
            if command in ('ota_analyze', 'ota_export'):
                if command == 'ota_analyze':
                    self.analysis = dict(old=p['old'], new=p['new'])
                if not self.analysis:
                    raise ValueError('Analyze the old and new release first')
                if command == 'ota_export' and p.get('output', '').lower().endswith('.zip'):
                    with tempfile.TemporaryDirectory(prefix='openwrt-ota-') as directory:
                        patch = Path(directory) / 'update.ota'
                        result = analyze(self.analysis['old'], self.analysis['new'], patch, self.progress)
                        from .ota_bundle import export_bundle
                        export_bundle(patch, p['output'], self.root)
                        result['output'] = p['output']
                    return result
                result = analyze(self.analysis['old'], self.analysis['new'],
                                 p.get('output') if command == 'ota_export' else None, self.progress)
                return result
            if command == 'ota_package':
                archive = Path(p.get('path') or self.root / 'update.ota')
                with Patch(archive) as patch:
                    self.archive_path = archive
                    self.review = None
                    return dict(path=str(archive), id=patch.id, manifest=patch.manifest)
            if command == 'ota_probe':
                self.review = None
                archive = Path(p.get('path') or self.archive_path or self.root / 'update.ota')
                self.archive_path = archive
                with connect(p['host'], p['password']) as client:
                    result = remote_inspect(client, archive, self.emit)
                    key = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
                token = secrets.token_hex(24)
                self.review = dict(host=p['host'], key=key, token=token, **result)
                self.runtime_session = dict(host=p['host'], key=key, **result)
                return dict(token=token, **result)
            if command == 'ota_apply':
                r = self.review
                if not r or p.get('token') != r['token'] or p['host'] != r['host']:
                    raise ValueError('Review this device and patch before applying')
                auto_reboot = p.get('auto_reboot', False)
                if type(auto_reboot) is not bool:
                    raise ValueError('auto_reboot must be a boolean')
                with connect(p['host'], p['password']) as client:
                    key = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
                    if key != r['key']:
                        raise ValueError('Device SSH identity changed since review')
                    # Installed runner owns the detached transaction. Losing the
                    # PC connection does not kill an in-progress filesystem write.
                    result = runtime_call(client, r, 'start', setup=True)
                    self.runtime_session = r
                    self.pending_reboot = dict(host=r['host'], key=r['key'], id=r['check']['id']) if auto_reboot else None
                    self.review = None
                    return result
            if command == 'ota_reboot':
                with connect(p['host'], p['password']) as client:
                    key = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
                    session = self.runtime_session
                    if not session or session['host'] != p['host'] or session['key'] != key:
                        raise ValueError('Check this device before rebooting')
                    status = runtime_call(client, session, 'status')
                    if status.get('job', {}).get('status') == 'running' or (status.get('transaction') or {}).get('status') in ('applying', 'rolling_back'):
                        raise ValueError('The device is still updating; do not reboot')
                    self.pending_reboot = None
                    return runtime_call(client, session, 'reboot')
            if command == 'ota_status':
                with connect(p['host'], p['password']) as client:
                    key = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
                    session = self.runtime_session
                    setup = not session or session['host'] != p['host'] or session['key'] != key
                    if not setup:
                        home = session['runtime']
                        ready = execute(client, 'if test -x '+shlex.quote(home+'/busybox')+' && test -x '+shlex.quote(home+'/root/usr/bin/python3')+' && test -d '+shlex.quote(home+'/root/target/etc')+'; then echo ready; fi').strip()
                        setup = ready != 'ready'
                    if setup:
                        # Reopening the PC app, retrying after a failed probe,
                        # and a reboot all use the package's RAM interpreter.
                        # Status must also work after applying the patch, when
                        # an inspection against its old base would be rejected.
                        archive = Path(p.get('path') or self.archive_path or self.root/'update.ota')
                        if not archive.is_file():
                            raise ValueError('Select the OTA patch before checking this old device')
                        session = dict(host=p['host'], key=key, **remote_prepare(client, archive, self.emit))
                    result = runtime_call(client, session, 'status', setup=setup)
                    self.runtime_session = session
                    transaction = result.get('transaction') or {}
                    job = result.get('job') or {}
                    if job.get('status') == 'complete' and transaction.get('status') in ('complete', 'rolled_back'):
                        marker = (p['host'], key, transaction.get('id'), transaction['status'])
                        activation = (job.get('result') or {}).get('activation') or {}
                        if marker in self.activated:
                            activation = {'status': 'ready'}
                        if marker not in self.activated and activation.get('status') != 'ready':
                            try:
                                activation = refresh_rpc(client)
                            except Exception as error:
                                activation = {'status': 'warning', 'error': str(error)}
                        if activation.get('status') == 'ready': self.activated.add(marker)
                        result['activation'] = activation
                        pending = self.pending_reboot
                        if pending and (pending['host'], pending['key'], pending['id']) == (p['host'], key, transaction.get('id')):
                            self.pending_reboot = None
                            try:
                                result['reboot'] = runtime_call(client, session, 'reboot')
                            except Exception as error:
                                result['reboot'] = {'status': 'failed', 'error': str(error)}
                    elif job.get('status') == 'failed':
                        self.pending_reboot = None
                    return result
            raise ValueError('Unknown OTA command')
        finally:
            self.emit('ota_activity', {'busy': False})
