"""Apply a real release delta with its ARM64 RAM interpreter under QEMU.

Only changed base files are staged, not another full rootfs. Fake boot partitions
are regular files. All staging is temporary; no device or network is accessed.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.ota_package import Release
from backend.ota_engine import Patch, package_records


def run(patch_path, old_path, workspace, bootstrap_probe=False):
    scratch = Path(workspace)/'tmp'; scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ota-migration-', dir=scratch) as directory, Patch(patch_path) as patch:
        root = Path(directory); live = root/'target'; live.mkdir()
        m = patch.manifest
        for name, desc in m['runtime'].items():
            dest = root/name; dest.parent.mkdir(parents=True, exist_ok=True)
            if desc['kind'] == 'file':
                dest.write_bytes(patch.archive.read('blobs/'+desc['sha256'])); dest.chmod(desc['mode'])
            else:
                dest.symlink_to(desc['target'])
        wanted = {op['path'] for op in m['operations'] if op['before'] is not None}
        wanted.update(('lib/apk/db/installed', 'etc/shadow', 'etc/config/wireless'))
        with Release(old_path) as old:
            if old.family != 'mu300': raise ValueError('Use the tar-root MU300 fixture for this test')
            for name, desc in old.entries.items():
                if desc['kind'] == 'dir':
                    dest = live/name; dest.mkdir(parents=True, exist_ok=True); dest.chmod(desc['mode'])
            with old.open(old.rootfs_name) as compressed, tarfile.open(fileobj=compressed, mode='r|gz') as archive:
                for node in archive:
                    name = node.name.removeprefix('./').rstrip('/')
                    if name not in wanted: continue
                    dest = live/name; dest.parent.mkdir(parents=True, exist_ok=True)
                    if node.isdir(): dest.mkdir(exist_ok=True)
                    elif node.issym(): dest.symlink_to(node.linkname)
                    elif node.isfile():
                        with archive.extractfile(node) as src, dest.open('wb') as out: shutil.copyfileobj(src, out)
                        dest.chmod(node.mode)
            identity = live/'etc/mu300/release.json'; identity.parent.mkdir(parents=True, exist_ok=True)
            identity.write_text(json.dumps(old.meta))
            linux = live/'etc/mu300/linux-slot'; linux.write_text('a\n')
            device = live/'dev/block/by-name'; device.mkdir(parents=True)
            with old.open(old.boot_name) as src, (device/'boot_a').open('wb') as out: shutil.copyfileobj(src, out)
            (device/'boot_b').write_bytes(b'ANDROID-SENTINEL')
        db = live/'lib/apk/db/installed'; db.parent.mkdir(parents=True, exist_ok=True)
        original_db = db.read_bytes()
        db.write_bytes(original_db+b'\nP:user-extra-plugin\nV:99\nA:aarch64\nF:usr/lib\nR:user-extra-plugin\n\n')
        extra = live/'usr/lib/user-extra-plugin'; extra.parent.mkdir(parents=True, exist_ok=True); extra.write_bytes(b'user-plugin-binary')
        config = live/'etc/config/wireless'; config.parent.mkdir(parents=True, exist_ok=True); config.write_bytes(b'USER-SSID-PASSWORD')
        data = live/'root/user-data.db'; data.parent.mkdir(exist_ok=True); data.write_bytes(b'USER-DATA')
        firmware = live/'lib/firmware/nv.bin'; firmware.parent.mkdir(parents=True, exist_ok=True); firmware.write_bytes(b'DEVICE-NV')
        source = live/'update.ota'; shutil.copyfile(patch_path, source)
        if bootstrap_probe:
            print('Bootstrap: old root has no Python or OTA engine', flush=True)
            assert not (live/'usr/bin/python3').exists(), 'The old fixture unexpectedly has Python'
            home = live/'tmp'/('unisoc-ota-'+patch.id); home.mkdir(parents=True, mode=0o700)
            for key, name in (('busybox', 'busybox'), ('bootstrap', 'bootstrap.sh')):
                (home/name).write_bytes(patch.archive.read('blobs/'+m[key]['sha256']))
            (home/'busybox').chmod(0o700)
            for name in ('run', 'sys', 'proc', 'etc/unisoc-ota'):
                (live/name).mkdir(parents=True, exist_ok=True)
            remote = '/tmp/unisoc-ota-'+patch.id
            command = ['unshare', '--mount', '--propagation', 'private', 'chroot', str(live),
                       remote+'/busybox', 'sh', remote+'/bootstrap.sh', '/update.ota', 'inspect', remote]
            probe = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if probe.returncode: raise AssertionError(probe.stdout+probe.stderr)
            assert json.loads(probe.stdout)['applicable']
            # Start through the actual detached RPC worker too, rather than
            # merely proving that direct engine invocation works.
            (live/'dev/null').touch()
            staged = live/'etc/unisoc-ota'/('upload-'+patch.id+'.ota')
            shutil.copyfile(patch_path, staged)
            command[-3:] = ['/etc/unisoc-ota/'+staged.name, 'start', remote]
            started = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if started.returncode: raise AssertionError(started.stdout+started.stderr)
            assert json.loads(started.stdout)['status'] == 'running'
            print('Detached ARM64 RPC update worker is running', flush=True)
            deadline = time.monotonic()+240
            while time.monotonic() < deadline:
                job_path = live/'etc/unisoc-ota/job.json'
                if job_path.is_file():
                    job = json.loads(job_path.read_text())
                    if job['status'] != 'running':
                        assert job['status'] == 'complete', job
                        break
                time.sleep(.1)
            else: raise AssertionError('Detached ARM RPC worker did not finish')
            import fcntl
            with (live/'etc/unisoc-ota/job.lock').open() as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
            # All private bind mounts belong to the now-exited worker namespace.
            time.sleep(.2)
        env = dict(os.environ, PYTHONHOME='/usr', UNISOC_OTA_ROOT='/target', UNISOC_OTA_RAM='/')
        command = ['chroot', str(root), '/usr/bin/python3', '/ota/engine.py']
        if not bootstrap_probe:
            result = subprocess.run(command+['inspect', '/target/update.ota'], env=env, capture_output=True, text=True, timeout=180)
            if result.returncode: raise AssertionError(result.stdout+result.stderr)
            check = json.loads(result.stdout); assert check['applicable'], check
            result = subprocess.run(command+['apply', '/target/update.ota'], env=env, capture_output=True, text=True, timeout=240)
            if result.returncode: raise AssertionError(result.stdout+result.stderr)
            assert json.loads(result.stdout)['status'] == 'complete'
        assert config.read_bytes() == b'USER-SSID-PASSWORD'
        assert data.read_bytes() == b'USER-DATA'
        assert extra.read_bytes() == b'user-plugin-binary'
        assert firmware.read_bytes() == b'DEVICE-NV'
        assert (live/'dev/block/by-name/boot_b').read_bytes() == b'ANDROID-SENTINEL'
        installed = package_records(db.read_bytes())
        assert 'user-extra-plugin' in installed
        assert set(m['target_packages']).issubset(installed)
        assert (live/'usr/lib/unisoc-ota/engine.py').read_bytes() == (root/'ota/engine.py').read_bytes()
        print('Update verified: plugin, data, config, firmware and Android slot retained; testing rollback', flush=True)
        result = subprocess.run(command+['rollback'], env=env, capture_output=True, text=True, timeout=240)
        if result.returncode: raise AssertionError(result.stdout+result.stderr)
        assert json.loads(result.stdout)['status'] == 'rolled_back'
        assert extra.read_bytes() == b'user-plugin-binary' and data.read_bytes() == b'USER-DATA'
        assert config.read_bytes() == b'USER-SSID-PASSWORD'
        return dict(ok=True, operations=len(m['operations']), packages=len(installed), arm64=True,
                    bootstrap_without_installed_python=bootstrap_probe, detached_rpc=bootstrap_probe,
                    rollback=True, android_slot_untouched=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--patch', required=True); p.add_argument('--old', required=True)
    p.add_argument('--workspace', default='/home/kano/openwrt-build')
    p.add_argument('--bootstrap-probe', action='store_true')
    args = p.parse_args()
    with zipfile.ZipFile(args.patch) as archive:
        bundled = 'update.ota' in archive.namelist()
        if bundled:
            with tempfile.TemporaryDirectory(prefix='ota-input-', dir=Path(args.workspace)/'tmp') as temporary:
                raw = Path(temporary)/'update.ota'
                with archive.open('update.ota') as src, raw.open('wb') as dest: shutil.copyfileobj(src, dest)
                result = run(raw, args.old, args.workspace, args.bootstrap_probe)
        else:
            result = run(args.patch, args.old, args.workspace, args.bootstrap_probe)
    print(json.dumps(result))
