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


def remote_inspect(client, archive, emit=lambda *_: None):
    with Patch(archive) as patch:
        # Only the installed engine runs with root privileges. The package may
        # contain data, never its own update program. Bootstrap older releases
        # through a full installer rather than executing uploaded arbitrary code.
        execute(client, 'test -f /usr/lib/unisoc-ota/engine.py && command -v python3 >/dev/null')
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
        result = json.loads(execute(client, 'python3 /usr/lib/unisoc-ota/engine.py inspect ' + shlex.quote(destination)))
        return dict(path=destination, check=result)


class Desktop:
    def __init__(self, root, emit):
        self.root = Path(root)
        self.emit = emit
        self.analysis = None
        self.review = None
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
                    return dict(path=str(archive), id=patch.id, manifest=patch.manifest)
            if command == 'ota_probe':
                archive = Path(p.get('path') or self.root / 'update.ota')
                with connect(p['host'], p['password']) as client:
                    result = remote_inspect(client, archive, self.emit)
                    key = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
                token = secrets.token_hex(24)
                self.review = dict(host=p['host'], key=key, token=token, **result)
                return dict(token=token, **result)
            if command == 'ota_apply':
                r = self.review
                if not r or p.get('token') != r['token'] or p['host'] != r['host']:
                    raise ValueError('Review this device and patch before applying')
                with connect(p['host'], p['password']) as client:
                    key = hashlib.sha256(client.get_transport().get_remote_server_key().asbytes()).hexdigest()
                    if key != r['key']:
                        raise ValueError('Device SSH identity changed since review')
                    # Installed runner owns the detached transaction. Losing the
                    # PC connection does not kill an in-progress filesystem write.
                    result = execute(client, 'python3 /usr/lib/unisoc-ota/rpc.py start ' + shlex.quote(r['path']))
                    self.review = None
                    return json.loads(result)
            if command == 'ota_status':
                with connect(p['host'], p['password']) as client:
                    return json.loads(execute(client, 'python3 /usr/lib/unisoc-ota/rpc.py status'))
            raise ValueError('Unknown OTA command')
        finally:
            self.emit('ota_activity', {'busy': False})
