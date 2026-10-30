#!/usr/bin/env python3
"""Local build task logging and serial release assembly; never flash a device.

Run in Linux/WSL. Native installer compilation keeps installer/build.py.
plan/check are read-only; package reuses verified kernels/APKs and assembles rootfs.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

TOP = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
OWNER = 'mu300-build-release-v1'
PROFILES = ('mu300-7.2', 'mu300-6.18')
COMMON = ('inputs', 'desktop_tools', 'block_apk', 'wifi_apks')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    # Windows readers of a DrvFS status file can briefly deny its replacement.
    # Keep publication atomic; retry sharing/access contention for at most 1 s.
    for attempt in range(21):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 20:
                raise
            time.sleep(0.05)


def layout(config, workspace, profiles, repo=TOP):
    data = json.loads(config.read_text(encoding='utf-8'))
    if data.get('format') != 1:
        raise ValueError('Unsupported build layout')
    workspace = workspace.expanduser().resolve()
    if workspace == Path('/') or workspace == Path.home():
        raise ValueError('Choose a dedicated build workspace')
    if workspace == Path('/mnt/c') or workspace.is_relative_to('/mnt/c'):
        raise ValueError('Build workspace must not migrate to C:')
    values = {'repo': str(repo.resolve()), 'workspace': str(workspace)}
    def path(value):
        result = Path(value.format(**values)).expanduser()
        if not result.is_absolute():
            raise ValueError('Build input paths must be absolute after expansion')
        return result.resolve()
    common = {key: path(data[key]) for key in COMMON}
    rows = {}
    for model in profiles:
        if model not in PROFILES or model not in data['profiles']:
            raise ValueError('Unknown/unconfigured model: ' + model)
        rows[model] = {'kernel': path(data['profiles'][model]['kernel'])}
    return common, rows


@contextmanager
def workspace_lock(workspace):
    import fcntl
    directory = workspace / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    # A sudo packaging run must not make the next user's compiler task require
    # write access to a root-owned lock file. Linux flock works on a read FD.
    descriptor = os.open(directory / 'openwrt-release.lock', os.O_RDONLY | os.O_CREAT, 0o644)
    with os.fdopen(descriptor, 'rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another build/package task owns this workspace') from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def stage_path(workspace, model):
    if model not in PROFILES:
        raise ValueError('Unknown staging model')
    parent = workspace / 'package-work/mu300-release'
    target = parent / model
    if parent.is_symlink() or parent.resolve() != parent or target.is_symlink():
        raise ValueError('Staging path must not use symlinks')
    if not target.resolve().is_relative_to(workspace.resolve()):
        raise ValueError('Staging path escaped workspace')
    return target


def clean_stage(workspace, model):
    target = stage_path(workspace, model)
    if not target.exists():
        return
    marker = target / '.build-owner.json'
    if marker.is_symlink() or json.loads(marker.read_text()) != {'owner': OWNER, 'profile': model}:
        raise ValueError('Refusing to clean unowned staging: ' + str(target))
    # Refuse a live image/root mount anywhere below the staging tree.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
        if mount == target or mount.is_relative_to(target):
            raise ValueError('Unmount staging before cleanup: ' + str(mount))
    shutil.rmtree(target)


def installer_artifact(platform):
    directory, variable = {
        'windows-x64': ('windows', 'OPENWRT_GUI_ARTIFACT'),
        'macos-arm64': ('macos-arm64', 'OPENWRT_GUI_MACOS_ARTIFACT'),
    }[platform]
    return Path(os.environ.get(variable, str(TOP / 'installer/artifacts' / directory)))


def input_check(common, rows):
    if not (TOP/'installer/package_gui.py').is_file():
        raise ValueError('Initialize the shared installer submodule / 请先执行 git submodule update --init --recursive')
    missing = [str(p) for p in common.values() if not p.exists()]
    missing += [str(p) for row in rows.values() for p in row.values() if not p.exists()]
    if missing:
        raise ValueError('Missing registered inputs (nothing was built/copied):\n' + '\n'.join(missing))
    required = ['dumps/boot_a.img', 'dumps/misc-head.bin', 'busybox', 'tools/logdw/logdw',
                'firmware', 'android-subset', 'tools/bt-init/mu300-bt-init', 'tools/keys/mu300-keys', 'tools/atd-io/mu300-atd-io']
    required += ['out/modules/' + n for n in ('wcn_bsp.ko', 'sprd_wlan_combo.ko', 'sprdbt_tty.ko', 'mali_kbase.ko')]
    missing = [str(common['inputs'] / n) for n in required if not (common['inputs'] / n).exists()]
    if not (common['desktop_tools'] / 'lib/ld-musl-aarch64.so.1').is_file():
        missing.append(str(common['desktop_tools'] / 'lib/ld-musl-aarch64.so.1'))
    if missing: raise ValueError('Missing registered MU300 inputs:\n' + '\n'.join(missing))
    runpy.run_path(str(TOP / 'tools/atd-io/artifact.py'))['check'](common['inputs'] / 'tools/atd-io/mu300-atd-io')
    runpy.run_path(str(TOP / 'tools/atd-io/artifact.py'))['check'](common['inputs'] / 'tools/keys/mu300-keys', TOP / 'tools/keys/mu300-keys.c')
    runpy.run_path(str(TOP / 'openwrt/wifi-sae-state.py'))['inspect'](common['wifi_apks'])
    block = common['block_apk']
    block_sources = ('block-mount.Dockerfile', 'build-block-mount.sh', 'build-block-mount-inner.sh',
                     'block-probe-guard.h', 'patches/fstools-probe-device-types.patch')
    expected = hashlib.sha256(b''.join((TOP / 'openwrt' / n).read_bytes() for n in block_sources)).hexdigest()
    if Path(str(block) + '.build-id').read_text().strip() != expected:
        raise ValueError('block-mount input is stale')
    if Path(str(block) + '.sha256').read_text().split()[0] != digest(block):
        raise ValueError('block-mount input checksum mismatch')
    sys.path.insert(0, str(TOP / 'installer'))
    from package_gui import sources, validate_artifact
    source = sources()
    for platform in ('windows-x64', 'macos-arm64'):
        validate_artifact(installer_artifact(platform), platform, source)
    check = runpy.run_path(str(TOP / 'upstream/check-container-support.py'))['check']
    for model, row in rows.items():
        release = check(row['kernel'])
        if not release.startswith(model.removeprefix('mu300-') + '.'):
            raise ValueError('Wrong kernel model: ' + model + ': ' + release)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_archive(path, require_diff=False, require_android=False):
    with zipfile.ZipFile(path) as archive:
        if require_android:
            runpy.run_path(str(TOP/'installer/package_android.py'))['verify_archive'](archive)
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip():
            raise ValueError('ZIP duplicate entries or CRC failure: ' + str(path))
        manifest = json.loads(archive.read('desktop/manifest.json'))
        shared_root = manifest['magisk_layout']['mu300-openwrt.tar.gz']
        shared_boot = manifest['magisk_layout']['boot-linux.img']
        if (names.count(shared_root) != 1 or names.count(shared_boot) != 1
                or sum(n.endswith('boot-linux.img') for n in names) != 1
                or sum(n.endswith(('openwrt.ext4.gz', 'rootfs.tar.gz', 'mu300-openwrt.tar.gz'))
                       for n in names) != 1):
            raise ValueError('Expected one boot and one system image')
        expected = dict(manifest['magisk'])
        for name, wanted in manifest['desktop'].items():
            if name in expected and expected[name] != wanted:
                raise ValueError('Conflicting checksum entry')
            expected[name] = wanted
        if set(expected) != {n for n in names if not n.endswith('/') and n != 'desktop/manifest.json'}:
            raise ValueError('Incomplete ZIP checksums')
        for name, wanted in expected.items():
            with archive.open(name) as stream:
                h = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1 << 20), b''):
                    h.update(chunk)
            if h.hexdigest() != wanted:
                raise ValueError('ZIP checksum mismatch: ' + name)
        if require_diff:
            tool=json.loads(archive.read('diff-tool.json'))
            required={'OpenWrt-Diff-Studio.exe','OpenWrt-Diff-Studio.py',
                      'OpenWrt-Diff-Studio.app/Contents/MacOS/openwrt-installer',
                      'tools/backend/openwrt-backend.exe','tools/macos/backend/openwrt-backend'}
            if tool.get('protocol')!=1 or not required.issubset(tool.get('files',{})):
                raise ValueError('Diff Studio entries/runtime missing')
            for name,wanted in tool['files'].items():
                if (name not in required and not name.startswith(('backend/','tools/backend/',
                        'tools/macos/backend/','OpenWrt-Diff-Studio.app/'))) or expected.get(name)!=wanted:
                    raise ValueError('Diff Studio scope/checksum mismatch: '+name)


class Task:
    def __init__(self, workspace, label):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}', label):
            raise ValueError('Invalid task label')
        self.workspace = workspace
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        self.id = stamp + '-' + label + '-' + str(os.getpid())
        self.logs = TOP / 'out/build-logs' / self.id
        self.logs.mkdir(parents=True)
        self.data = {'format': 1, 'task': self.id, 'pid': os.getpid(), 'state': 'running',
                     'workspace': str(workspace), 'logs': str(self.logs), 'steps': []}
        self.save()

    def save(self, **fields):
        self.data.update(fields)
        write_json(self.logs / 'status.json', self.data)
        write_json(TOP / 'out/build-state/latest.json', self.data)

    def run(self, label, command, extra_env=None):
        log = self.logs / (label + '.log')
        step = {'stage': label, 'command': list(map(str, command)), 'log': str(log),
                'started': time.time(), 'state': 'running'}
        self.data['steps'].append(step); self.save(stage=label)
        temp_parent = self.workspace / 'tmp'
        temp_parent.mkdir(parents=True, exist_ok=True)
        print(label + ': ' + str(log), flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix='mu300-release-', dir=temp_parent) as temp:
                env = dict(os.environ, TMPDIR=temp, PYTHONDONTWRITEBYTECODE='1')
                env.update(extra_env or {})
                with log.open('wb') as stream:
                    subprocess.run(list(map(str, command)), cwd=TOP, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT, check=True)
        except BaseException:
            step.update(state='failed', finished=time.time()); self.save(state='failed')
            raise
        step.update(state='complete', finished=time.time()); self.save()


def package(task, common, rows):
    input_check(common, rows)  # All models and both GUI platforms before copying rootfs.
    for model in rows:
        if stage_path(task.workspace, model).exists():
            raise ValueError('Prior staging retained; inspect it then run clean-staging: ' + model)
    identify = runpy.run_path(str(TOP / 'tools/release-identity.py'))['identify']
    before = identify(TOP, 'mu300', '7.2.8')
    version = before['private_version']
    parent = TOP / 'release'
    pending = parent / ('.building-' + task.id)
    pending.mkdir(parents=True)
    task.save(pending_output=str(pending))
    assets = []
    for model, row in rows.items():
        stage = stage_path(task.workspace, model)
        stage.mkdir(parents=True)
        write_json(stage / '.build-owner.json', {'owner': OWNER, 'profile': model})
        assembly = stage / 'assembly'; assembly.mkdir()
        archive = assembly / 'package.zip'
        env = {'MU300_INPUTS': str(common['inputs']), 'MU300_DESKTOP_TOOLS': str(common['desktop_tools']),
               'MU300_UPSTREAM_OUT': str(row['kernel']), 'MU300_KERNEL': model.removeprefix('mu300-'),
               'MU300_PACKAGE_WORK': str(assembly), 'MU300_ROOTFS_OUT_DIR': str(assembly),
               'MU300_VERSION': version, 'MU300_DESKTOP_INSTALLER': '1', 'OPENWRT_GUI_OPTIONAL': '0'}
        task.run(model + '-package', ['sh', TOP / 'tools/build-openwrt-tf-magisk.sh', archive], extra_env=env)
        verify_archive(archive, require_diff=True,require_android=True)
        release = json.loads((assembly / ('release-' + env['MU300_KERNEL'] + '.json')).read_text())
        target = pending / (release['package_name'] + '.zip')
        shutil.copyfile(archive, target)
        checksum = digest(archive)
        if digest(target) != checksum: raise ValueError('Published copy differs from verified archive')
        (pending / (target.name + '.sha256')).write_text(checksum + '  ' + target.name + '\n')
        assets.append({'profile': model, 'file': target.name, 'bytes': target.stat().st_size, 'sha256': checksum})
        clean_stage(task.workspace, model)
    after = identify(TOP, 'mu300', '7.2.8')
    if after['source_fingerprint'] != before['source_fingerprint']:
        raise ValueError('Repository sources changed during release; output stays unpublished')
    write_json(pending / 'BUILD-RUN.json', {**task.data, 'state': 'verified',
               'source': before, 'assets': assets})
    destination = parent / task.id
    pending.rename(destination)
    task.save(state='complete', output=str(destination), assets=assets)
    print('Verified release: ' + str(destination))


def publish_completed(task, common, rows, source):
    """Publish only completed archives from an interrupted local batch."""
    if source is None:
        raise ValueError('Supply --source-release for the interrupted batch')
    if source.is_symlink(): raise ValueError('Symlink pending release directory')
    parent = (TOP / 'release').resolve()
    source = source.resolve()
    if (source.parent != parent or not source.name.startswith('.building-') or
            source.is_symlink()):
        raise ValueError('Use an owned pending release directory')
    old_id = source.name.removeprefix('.building-')
    status = json.loads((TOP / 'out/build-logs' / old_id / 'status.json').read_text())
    if (status.get('state') != 'failed' or status.get('task') != old_id or
            Path(status.get('pending_output', '')).resolve() != source or
            Path(status.get('workspace', '')).resolve() != task.workspace):
        raise ValueError('Pending release ownership/state differs')
    complete = {s['stage'] for s in status['steps'] if s.get('state') == 'complete'}
    assets, identity = [], None
    for path in sorted(source.glob('*.zip')):
        if path.is_symlink(): raise ValueError('Symlink pending archive')
        with zipfile.ZipFile(path) as archive:
            record = json.loads(archive.read('desktop/release.json'))
        profile = 'mu300-' + '.'.join(record['kernel_release'].split('.')[:2])
        if profile not in rows or profile + '-package' not in complete:
            raise ValueError('Archive has no completed selected build step: ' + profile)
        if path.name != record['package_name'] + '.zip' or record.get('source_dirty') is not False:
            raise ValueError('Archive name or clean source provenance differs')
        commit = record['source_commit']
        if not re.fullmatch('[a-f0-9]{40}', commit): raise ValueError('Invalid source commit')
        git = ['git', '-c', 'safe.directory='+str(TOP), '-C', str(TOP)]
        marker = TOP / '.git'
        if marker.is_file():
            directory = marker.read_text().strip().removeprefix('gitdir:').strip()
            if re.match(r'^[A-Za-z]:[/\\]', directory):
                directory = '/mnt/'+directory[0].lower()+'/'+directory[3:].replace('\\','/')
                git += ['--git-dir='+directory, '--work-tree='+str(TOP)]
        tree = subprocess.check_output([*git, 'rev-parse', commit+'^{tree}'])
        if hashlib.sha256(tree).hexdigest() != record['source_fingerprint']:
            raise ValueError('Recorded source fingerprint differs from its clean Git tree')
        key = (commit, record['source_fingerprint'], record['private_version'])
        if identity is not None and identity != key: raise ValueError('Mixed pending releases')
        identity = key
        wanted = path.with_name(path.name+'.sha256').read_text().split()
        checksum = digest(path)
        if wanted != [checksum, path.name]: raise ValueError('Pending archive checksum differs')
        verify_archive(path, require_diff=True)
        assets.append({'profile':profile,'file':path.name,'bytes':path.stat().st_size,'sha256':checksum})
        source_record = record
    if {a['profile'] for a in assets} != set(rows):
        raise ValueError('Not every selected profile has a completed archive')
    allowed = {a['file'] for a in assets} | {a['file']+'.sha256' for a in assets}
    if {p.name for p in source.iterdir()} != allowed:
        raise ValueError('Pending directory contains unverified extra files')
    input_check(common, rows)
    destination = parent / task.id
    if destination.exists(): raise ValueError('Publication destination already exists')
    write_json(source/'BUILD-RUN.json', {**task.data,'state':'verified','source':source_record,
               'resumed_from':old_id,'selected_profiles':list(rows),'assets':assets})
    source.rename(destination)
    task.save(state='complete', output=str(destination), resumed_from=old_id, assets=assets)
    print('Verified release: '+str(destination))


def add_diff(task,rows,source,repair_installer=False,refresh_native=False):
    if source is None:raise ValueError('Supply --source-release')
    source=source.resolve()
    if source.parent!=(TOP/'release').resolve() or source.name.startswith('.'):
        raise ValueError('Use a verified published local release')
    base=json.loads((source/'BUILD-RUN.json').read_text())
    if base.get('state')!='verified':raise ValueError('Source release is not verified')
    selected={}
    for profile in rows:
        asset=next(a for a in base['assets'] if a['profile']==profile)
        path=source/asset['file']
        if path.resolve().parent!=source or digest(path)!=asset['sha256']:
            raise ValueError('Source archive checksum/path mismatch')
        verify_archive(path);selected[profile]=path
        if stage_path(task.workspace,profile).exists():raise ValueError('Prior staging retained: '+profile)
    identify=runpy.run_path(str(TOP/'tools/release-identity.py'))['identify']
    before=identify(TOP,'mu300','7.2.8');parent=TOP/'release';pending=parent/('.building-'+task.id);pending.mkdir()
    task.save(pending_output=str(pending),build_type='native-installer-refresh' if refresh_native else 'installer-entry-repair' if repair_installer else 'diff-tools-only',base_release=str(source))
    repack=runpy.run_path(str(TOP/'tools/repack-with-diff.py'))['repack'];assets=[]
    for profile,path in selected.items():
        stage=stage_path(task.workspace,profile);stage.mkdir(parents=True)
        write_json(stage/'.build-owner.json',{'owner':OWNER,'profile':profile})
        studio=stage/'assembly'
        task.run(profile+'-diff',[sys.executable,TOP/'tools/package-diff-studio.py','--prepare','--package',studio])
        tag='-installer-g' if refresh_native else '-installer-fix-g' if repair_installer else '-tools-g'
        target=pending/(path.stem+tag+before['source_commit'][:8]+'.zip')
        repack(path,target,studio,before,repair_installer=repair_installer,refresh_native=refresh_native);verify_archive(target,require_diff=True,require_android=refresh_native)
        checksum=digest(target);(pending/(target.name+'.sha256')).write_text(checksum+'  '+target.name+'\n')
        assets.append({'profile':profile,'file':target.name,'bytes':target.stat().st_size,'sha256':checksum})
        clean_stage(task.workspace,profile);task.save(assets=assets)
    if identify(TOP,'mu300','7.2.8')['source_fingerprint']!=before['source_fingerprint']:
        raise ValueError('Sources changed during repack; output stays unpublished')
    write_json(pending/'BUILD-RUN.json',{**task.data,'state':'verified','source':before,'assets':assets})
    destination=parent/task.id;pending.rename(destination);task.save(state='complete',output=str(destination),assets=assets)
    print('Verified release: '+str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'check', 'package', 'publish-completed', 'add-diff', 'repair-installer', 'refresh-installers', 'run', 'status', 'clean-staging'))
    parser.add_argument('--profiles', nargs='+', choices=('all', *PROFILES), default=['all'])
    parser.add_argument('--workspace', type=Path, default=Path.home() / 'openwrt-build')
    parser.add_argument('--config', type=Path, default=TOP / 'tools/build-layout.json')
    parser.add_argument('--label', default='build')
    parser.add_argument('--source-release',type=Path)
    # Compiler arguments follow -- and are passed without a shell.
    args, command = parser.parse_known_args()
    if command and command[0] == '--': command = command[1:]
    if command and args.action != 'run': parser.error('Unexpected extra arguments')
    if os.name == 'nt': parser.error('Run in WSL; see docs/LOCAL-BUILD.zh-CN.md')
    workspace = args.workspace.expanduser().resolve()
    profiles = list(PROFILES) if 'all' in args.profiles else list(dict.fromkeys(args.profiles))
    common, rows = layout(args.config, workspace, profiles)
    if args.action == 'plan':
        print(json.dumps({'workspace': str(workspace), 'inputs': common, 'profiles': rows,
              'staging': workspace / 'package-work/mu300-release', 'output': TOP / 'release',
              'logs': TOP / 'out/build-logs'}, default=str, indent=2)); return
    if args.action == 'status':
        print((TOP / 'out/build-state/latest.json').read_text(encoding='utf-8')); return
    if args.action == 'check':
        input_check(common, rows); print('All selected inputs verified; nothing built/copied'); return
    with workspace_lock(workspace):
        if args.action == 'clean-staging':
            for model in profiles: clean_stage(workspace, model)
            return
        task = Task(workspace, args.label)
        try:
            if args.action == 'run':
                if not command: raise ValueError('Supply a real compiler/build command after --')
                task.run('command', command); task.save(state='complete')
            elif args.action == 'publish-completed':
                publish_completed(task,common,rows,args.source_release)
            elif args.action in ('add-diff','repair-installer','refresh-installers'):
                add_diff(task,rows,args.source_release,repair_installer=args.action=='repair-installer',refresh_native=args.action=='refresh-installers')
            else:
                package(task, common, rows)
        except BaseException as error:
            task.save(state='failed', error=str(error))
            raise


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit('Build stopped: ' + str(error))
