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
METHODS = {'status': {}, 'inspect': {}, 'start': {'id':''}, 'rollback': {}, 'reboot': {}}


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
        source = STATE/'runtime'
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
    if method == 'reboot':
        with (STATE / 'job.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal = STATE / 'journal.json'
            if journal.exists() and json.loads(journal.read_text())['status'] in ('applying', 'rolling_back'):
                raise ValueError('Update recovery is pending')
            os.sync()
            reboot = ['/ota/busybox', 'reboot'] if os.environ.get('UNISOC_OTA_RAM') else ['/sbin/reboot']
            subprocess.Popen(reboot, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
