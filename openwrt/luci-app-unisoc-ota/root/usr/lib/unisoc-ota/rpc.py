#!/usr/bin/python3
"""Fixed RPC surface. Detached worker survives browser/SSH disconnection."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import re
import shutil
import time

from engine import Engine, Patch, atomic_json, prepare_runtime

ROOT = os.environ.get('UNISOC_OTA_ROOT', '')
ENGINE = str(Path(__file__).with_name('engine.py'))
STATE = Path(ROOT+'/etc/unisoc-ota')
UPLOAD = STATE / 'upload.ota'
JOB = STATE / 'job.json'
METHODS = {'status': {}, 'inspect': {}, 'start': {'id':''}, 'rollback_check': {}, 'rollback': {}, 'reboot': {}}


def mounted(path, table='/proc/self/mountinfo'):
    """Bind mounts can share st_dev with their parent; read the kernel table."""
    wanted = str(Path(path).resolve())
    with open(table) as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 6: continue
            point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
            if point == wanted: return True
    return False


def activate_rpc():
    """Register newly installed plugins without restarting rpcd or its sessions."""
    root = Path(ROOT or '/')
    if not (root/'etc/init.d/rpcd').is_file():
        return {'status': 'deferred', 'error': 'rpcd service is unavailable'}
    prefix = ['/ota/busybox', 'chroot', str(root)] if os.environ.get('UNISOC_OTA_RAM') else []
    try:
        if prefix:
            # A bind of / does not include its tmpfs/proc submounts. rc.common
            # needs the real /var -> /tmp locks and ubus socket, not empty
            # directories from the disk root. PID 1 remains the host init in
            # this RAM chroot (no PID namespace is created).
            for name in ('tmp', 'proc'):
                destination = str(root/name)
                if not mounted(destination):
                    (root/name).mkdir(exist_ok=True)
                    subprocess.run(['/ota/busybox', 'mount', '--bind',
                                    '/proc/1/root/'+name, destination], check=True,
                                   capture_output=True, text=True, timeout=5)
        reload = subprocess.run(prefix+['/etc/init.d/rpcd', 'reload'], capture_output=True, text=True, timeout=15)
        if reload.returncode:
            raise ValueError((reload.stderr or reload.stdout)[-1000:])
        deadline = time.monotonic()+5
        while True:
            probe = subprocess.run(prefix+['/bin/sh', '-c', 'ubus call unisoc-ota status'], capture_output=True, text=True, timeout=3)
            if not probe.returncode:
                data = json.loads(probe.stdout)
                if 'identity' in data and not data.get('error'):
                    return {'status': 'ready'}
            if time.monotonic() >= deadline:
                raise ValueError('unisoc-ota RPC did not register after reload')
            time.sleep(.1)
    except Exception as error:
        # Files are already committed. A reload error must not be reported as
        # a failed filesystem transaction or trigger an unrelated rollback.
        return {'status': 'warning', 'error': str(error)}


def worker(command, path=None, expected_id=None):
    if path and not os.environ.get('UNISOC_OTA_RAM'):
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (STATE/'job.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with Patch(path) as patch:
            if patch.id != expected_id:
                raise ValueError('Upload differs from the reviewed patch')
            home = prepare_runtime(patch)
        result = subprocess.run([str(home/'busybox'), 'sh', str(home/'bootstrap.sh'),
                                 str(path), 'start', str(home)], capture_output=True, text=True)
        if result.returncode: raise ValueError(result.stderr or result.stdout)
        return json.loads(result.stdout)
    if command == 'rollback' and not os.environ.get('UNISOC_OTA_RAM'):
        import tempfile
        home = Path(tempfile.mkdtemp(prefix='unisoc-ota-rollback-'))
        journal = json.loads((STATE/'journal.json').read_text())
        runtime = journal.get('runtime', 'runtime')
        if runtime != 'runtime' and not re.fullmatch(r'backup-[0-9]+/runtime', runtime):
            raise ValueError('Invalid transaction recovery runtime')
        source = STATE/runtime
        if not (source/'ota/busybox').is_file():
            raise ValueError('Missing transaction recovery runtime')
        shutil.copytree(source, home/'root', symlinks=True)
        bb = str(home/'root/ota/busybox')
        for name, mounted in (('target', '/'), ('dev', '/dev'), ('target/dev', '/dev'), ('target/run', '/run'), ('sys', '/sys'), ('target/sys', '/sys')):
            dest = home/'root'/name; dest.mkdir(parents=True, exist_ok=True)
            subprocess.run([bb, 'mount', '--bind', mounted, str(dest)], check=True)
        proc = home/'root/proc'; proc.mkdir(exist_ok=True)
        subprocess.run([bb, 'mount', '-t', 'proc', 'proc', str(proc)], check=True)
        env = dict(os.environ, UNISOC_OTA_ROOT='/target', UNISOC_OTA_RAM='/', UNISOC_OTA_LIVE='1', PYTHONHOME='/usr')
        result = subprocess.run([bb, 'chroot', str(home/'root'), '/usr/bin/python3', '/ota/rpc.py', 'rollback'],
                                env=env, capture_output=True, text=True)
        if result.returncode: raise ValueError(result.stderr or result.stdout)
        return json.loads(result.stdout)
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / 'job.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        patch_id = None
        if path:
            candidate = STATE / ('candidate-' + str(time.time_ns()) + '.ota')
            source = Path(path)
            before = source.stat()
            if before.st_size > (2 << 30) or shutil.disk_usage(STATE).free < before.st_size + (16 << 20):
                raise ValueError('Insufficient staging space or excessive upload size')
            try:
                with source.open('rb') as src, candidate.open('xb') as dest:
                    remaining = before.st_size
                    while remaining:
                        block = src.read(min(1 << 20, remaining))
                        if not block:
                            raise ValueError('Upload changed during staging')
                        dest.write(block)
                        remaining -= len(block)
                    dest.flush()
                    os.fsync(dest.fileno())
                with Patch(candidate) as patch:
                    if patch.id != expected_id:
                        raise ValueError('Upload differs from the patch you reviewed; inspect again')
                    check = Engine().inspect(patch)
                    if not check['applicable']:
                        raise ValueError('File conflict or insufficient space')
                    frozen = STATE / ('reviewed-' + patch.id + '.ota')
                    patch_id = patch.id
                os.replace(candidate, frozen)
                after = source.stat()
                if (after.st_dev,after.st_ino,after.st_mtime_ns,after.st_size) == (before.st_dev,before.st_ino,before.st_mtime_ns,before.st_size):
                    source.unlink()
                path = str(frozen)
            finally:
                candidate.unlink(missing_ok=True)
        # Parent holds the lock until a durable running marker is written, then
        # detaches a process which inherits it. Its close releases the lock.
        atomic_json(JOB, {'status': 'running', 'command': command, 'patch_id': patch_id})
        subprocess.Popen([sys.executable, __file__, '_worker', command, path or ''],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         pass_fds=(lock.fileno(),))
    return {'status': 'running'}


def dispatch(method, path=None, review_id=None):
    STATE.mkdir(mode=0o700, exist_ok=True)
    if method == 'status':
        result = Engine().status()
        result['job'] = json.loads(JOB.read_text()) if JOB.exists() else {'status': 'idle'}
        if result['job']['status'] == 'complete':
            with (STATE/'job.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # The worker has published completion but still owns its
                    # final cleanup. Do not race it with an automatic reboot.
                    result['job'] = dict(result['job'], status='running')
                    return result
        if result['job']['status'] == 'running':
            with (STATE / 'job.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    transaction = result.get('transaction') or {}
                    complete = (transaction.get('status') == 'complete' and transaction.get('id') == result['job'].get('patch_id')) if result['job'].get('command') == 'apply' else transaction.get('status') == 'rolled_back'
                    result['job'] = dict(status='complete' if complete else 'failed',
                                         result={'error': 'Interrupted transaction; recovery status: ' + transaction.get('status', 'unknown')} if not complete else {})
                    atomic_json(JOB, result['job'])
        return result
    if method == 'inspect':
        with Patch(UPLOAD) as patch:
            return Engine().inspect(patch)
    if method == 'start':
        path = Path(path) if path else UPLOAD
        # CLI may select only a pre-reviewed, content-addressed upload on the
        # same persistent filesystem. rpcd callers cannot supply arbitrary paths.
        if path.parent != STATE or path.is_symlink() or path.suffix != '.ota':
            raise ValueError('Invalid OTA upload path')
        if review_id is None:
            matched = re.fullmatch(r'upload-([a-f0-9]{64})\.ota', path.name)
            review_id = matched[1] if matched else None
        if not isinstance(review_id,str) or not re.fullmatch(r'[a-f0-9]{64}',review_id):
            raise ValueError('Inspect this patch before applying')
        return worker('apply', str(path), review_id)
    if method == 'rollback':
        return worker('rollback')
    if method == 'rollback_check':
        return Engine().rollback_check()
    if method == 'reboot':
        with (STATE / 'job.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = STATE / 'journal.json'
            if journal.exists() and json.loads(journal.read_text())['status'] in ('applying', 'rolling_back'):
                raise ValueError('Update recovery is pending')
            reboot = ['/ota/busybox', 'reboot'] if os.environ.get('UNISOC_OTA_RAM') else ['/sbin/reboot']
            # Let rpcd deliver the acknowledgement before networking stops.
            # The child retains the job lock so another update cannot start
            # during this short acknowledgement window. It syncs before reboot.
            code = 'import os,sys,time; time.sleep(2); os.sync(); os.execv(sys.argv[1],sys.argv[1:])'
            subprocess.Popen([sys.executable, '-c', code, *reboot], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, pass_fds=(lock.fileno(),))
        return {'status': 'rebooting'}
    raise ValueError('Unknown OTA command')


def main():
    if sys.argv[1:2] == ['_worker']:
        command, path = sys.argv[2:4]
        args = [sys.executable, ENGINE, command] + ([path] if path else [])
        result = subprocess.run(args, capture_output=True, text=True)
        try:
            detail = json.loads(result.stdout)
        except ValueError:
            detail = {'error': (result.stderr or result.stdout)[-4000:]}
        if result.returncode == 0:
            detail['activation'] = activate_rpc()
        atomic_json(JOB, dict(status='failed' if result.returncode else 'complete', result=detail))
        if path and result.returncode == 0:
            uploaded = Path(path)
            if uploaded.parent == STATE and re.fullmatch(r'reviewed-[a-f0-9]{64}\.ota', uploaded.name):
                uploaded.unlink(missing_ok=True)
        return
    if sys.argv[1:] == ['list']:
        print(json.dumps(METHODS))
        return
    if sys.argv[1:2] == ['call']:
        # Standard rpcd plugins receive arguments via stdin. No dynamic command.
        args = json.loads(sys.stdin.read(4096) or '{}')
        if not isinstance(args, dict):
            raise ValueError('RPC arguments must be an object')
        # rpcd injects the authenticated session for LuCI/HTTP calls. It is
        # transport metadata, not a method argument. ACLs are enforced by rpcd.
        args.pop('ubus_rpc_session', None)
        method, path = sys.argv[2], None
        if method == 'start' and isinstance(args,dict) and set(args) == {'id'}:
            review_id = args['id']
        elif args:
            raise ValueError('Unexpected RPC arguments')
        else:
            review_id = None
    else:
        method = sys.argv[1]
        path = sys.argv[2] if len(sys.argv) == 3 else None
        review_id = None
    print(json.dumps(dispatch(method, path, review_id)))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)
