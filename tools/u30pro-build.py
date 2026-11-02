#!/usr/bin/env python3
"""U30 Pro source/input checks and the registered native kernel build.

No device operations, cache downloads or package publication. Invoke kernel
compilation through build-release.py run to obtain the shared workspace lock.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TOP = Path(__file__).resolve().parents[1]
PROFILE = TOP / 'devices/u30pro'
NATIVE = PROFILE / 'native'
PIN = 'df3b44fce648105aa785204571f8ad6317a95959'
FRAGMENTS = ('native-wifi', 'native-display', 'native-thermal', 'native-router',
             'native-idle', 'native-cpufreq', 'native-router2', 'native-kmods',
             'native-containers')
REQUIRED = (
    'CONFIG_UNISOC_AIC8800=m', 'CONFIG_UNISOC_NV3002C_PANEL=y',
    'CONFIG_UNISOC_TLSC_TOUCH=y', 'CONFIG_U30PRO_SPRD_CPUFREQ_V2=m',
    'CONFIG_CPU_FREQ=y', 'CONFIG_U30PRO_SPRD_ASE=y',
    'CONFIG_U30PRO_SPRD_SCUFREQ=m', 'CONFIG_CPU_IDLE=y',
    'CONFIG_ARM_PSCI_CPUIDLE=y', 'CONFIG_TCP_CONG_BBR=y',
    'CONFIG_EXT4_FS=y', 'CONFIG_F2FS_FS=y', 'CONFIG_OVERLAY_FS=y', 'CONFIG_ZRAM=y',
    'CONFIG_USER_NS=y', 'CONFIG_MEMCG=y', 'CONFIG_CGROUP_PIDS=y',
    'CONFIG_CPUSETS=y', 'CONFIG_POSIX_MQUEUE=y',
)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def check_sources(profile=PROFILE):
    manifest = read_json(profile / 'reference.json')
    if manifest.get('format') != 1 or manifest.get('revision') != PIN:
        raise ValueError('Unreviewed U30 Pro reference revision')
    adaptations = read_json(profile / 'adaptations.json') if (profile / 'adaptations.json').exists() else {}
    native = (profile / 'native').resolve()
    for name, record in manifest['files'].items():
        path = native / name
        if path.is_symlink() or not path.resolve().is_relative_to(native) or not path.is_file():
            raise ValueError('Missing or non-regular imported source: ' + name)
        expected = record['sha256']
        if name in adaptations:
            local = adaptations[name]
            if local.get('source_sha256') != expected or not local.get('reason'):
                raise ValueError('Invalid local adaptation provenance: ' + name)
            expected = local['sha256']
        if sha(path) != expected:
            raise ValueError('Imported/adapted source differs from its reviewed manifest: ' + name)
    if set(adaptations) - set(manifest['files']):
        raise ValueError('Adaptation does not name an imported source')
    profile_data = read_json(profile / 'profile.json')
    if (profile_data['software_base']['branch'] != 'minikano-openwrt-feats' or
            profile_data['hardware_reference']['commit'] != PIN or
            profile_data['boot']['images'] != ['boot', 'vendor_boot', 'init_boot'] or
            profile_data['boot']['slot_policy'] != 'opposite-verified-android-slot' or
            profile_data['hardware']['wifi'] != 'aic8800-sdio'):
        raise ValueError('U30 Pro platform contract differs')
    return {'files': len(manifest['files']), 'reference': PIN,
            'local_adaptations': sorted(adaptations), 'hardware_verified': False}


def load_layout(config, workspace=None):
    data = read_json(config)
    if data.get('format') != 1 or data.get('family') != 'u30pro':
        raise ValueError('Expected an explicit U30 Pro build layout')
    workspace = Path(workspace or data['workspace']).expanduser()
    if not workspace.is_absolute() or workspace.resolve() in {Path('/'), Path.home().resolve()}:
        raise ValueError('Use a dedicated absolute build workspace')
    if str(workspace).lower().startswith(('/mnt/c/', 'c:')):
        raise ValueError('Do not migrate builds to C:')
    values = {'workspace': str(workspace), 'repo': str(TOP)}
    paths = {}
    for key in ('kernel_source', 'kernel_objects', 'input_archive', 'input_manifest',
                'stock_capture', 'partition_reference', 'busybox', 'at_transport',
                'maintenance', 'theme', 'installer_artifacts'):
        path = Path(data[key].format(**values)).expanduser()
        if not path.is_absolute():
            raise ValueError('Registered path must be absolute: ' + key)
        paths[key] = path
    for key in ('kernel_source', 'kernel_objects'):
        if not paths[key].resolve().is_relative_to(workspace.resolve()) or paths[key].resolve() == workspace.resolve():
            raise ValueError('Kernel cache escaped workspace: ' + key)
    source, objects = (paths[k].resolve() for k in ('kernel_source', 'kernel_objects'))
    if source == objects or source.is_relative_to(objects) or objects.is_relative_to(source):
        raise ValueError('Kernel source and objects must be separate')
    return workspace, paths


def check_inputs(paths):
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise ValueError('Missing registered inputs (nothing downloaded/copied):\n' + '\n'.join(missing))
    source = paths['kernel_source']
    makefile = (source / 'Makefile').read_text(encoding='utf-8')
    version = [re.search(r'^' + key + r'\s*=\s*(\d+)\s*$', makefile, re.M) for key in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL')]
    if not all(version) or '.'.join(m[1] for m in version) != '6.12.94':
        raise ValueError('Registered source is not U30 Pro Linux 6.12.94')
    for name in ('boot.img', 'vendor_boot.img', 'init_boot.img', 'stock-images.json'):
        if not (paths['stock_capture'] / name).is_file():
            raise ValueError('Missing model-specific stock input: ' + name)
    stock = read_json(paths['stock_capture'] / 'stock-images.json')
    if stock.get('profile') != 'u30pro' or stock.get('device_writes') is not False:
        raise ValueError('Stock boot templates have no read-only U30 Pro provenance')
    android_slot = stock.get('android_slot')
    if android_slot not in ('a', 'b', '_a', '_b'):
        raise ValueError('Stock boot templates have no verified Android slot')
    android_slot = android_slot.removeprefix('_')
    for name in ('boot', 'vendor_boot', 'init_boot'):
        path = paths['stock_capture'] / (name + '.img')
        record = stock.get('files', {}).get(path.name, {})
        if (record.get('source') != '/dev/block/by-name/' + name + '_' + android_slot or
                record.get('size') != path.stat().st_size or record.get('sha256') != sha(path)):
            raise ValueError('Stock boot template source/hash differs: ' + name)
    capture = read_json(paths['partition_reference'])
    if capture.get('device_writes') is not False or '/MU5358/' not in capture.get('fingerprint', ''):
        raise ValueError('Stock reference is not the read-only MU5358 capture')
    # Presence checks here are not an artifact-reuse receipt: changed kernel
    # sources still require Image plus complete matching modules to be rebuilt.
    # Availability is not a reuse receipt; package() still validates Image,
    # config, all selected modules, runtime and portable-installer identities.
    return {'registered_inputs_present': True, 'kernel_source_version': '6.12.94',
            'kernel_artifacts_reusable': False, 'preview_assembler_available': True,
            'hardware_verified': False}


def kernel_command(paths, jobs, configure_only=False):
    if jobs < 1 or jobs > 256:
        raise ValueError('Kernel jobs must be between 1 and 256')
    command = [sys.executable, str(NATIVE / 'kernel/ums9632/build_linux.py'),
               '--profile', 'hardware-native-kexec-modem',
               '--out', paths['kernel_objects'].name, '--jobs', str(jobs)]
    for fragment in FRAGMENTS:
        command += ['--extra-config', fragment + '.config']
    command += ['--extra-config', str(PROFILE / 'port.config')]
    for setting in REQUIRED:
        command += ['--require', setting]
    if configure_only:
        command += ['--configure-only']
    return command


def dispatch_release(args, command, workspace, profiles, *, workspace_lock, task_type):
    if profiles != ['u30pro-6.12']:
        raise ValueError('This independent repository builds U30 Pro only')
    _, paths = load_layout(args.config, workspace)
    if args.action == 'status':
        status = TOP / 'out/build-state/latest.json'
        print(status.read_text(encoding='utf-8') if status.exists() else 'No U30 Pro build task has run')
        return
    sources = check_sources()
    if args.action == 'plan':
        print(json.dumps({'profile': profiles[0], 'sources': sources, 'inputs': paths,
                         'workspace': workspace, 'output': TOP / 'release',
                         'logs': TOP / 'out/build-logs', 'package_ready': False}, indent=2, default=str))
        return
    if args.action == 'check':
        print(json.dumps({**sources, **check_inputs(paths)}, indent=2))
        return
    if args.action == 'package':
        check_inputs(paths)
        command=['taskset','-c','14,15','nice','-n','19','ionice','-c','3',
                 sys.executable,str(TOP/'tools/u30pro-package.py'),'package']
    elif args.action != 'run':
        raise ValueError('Unsupported U30 Pro build action')
    if not command:
        raise ValueError('Supply the real compiler/test command after --')
    with workspace_lock(workspace):
        task = task_type(workspace, args.label)
        try:
            task.run('command', command, {'U30PRO_BUILD_LOCKED': '1'})
            task.save(state='complete', profile=profiles[0])
        except BaseException as error:
            task.save(state='failed', error=str(error))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'check', 'kernel'))
    parser.add_argument('--config', type=Path, default=TOP / 'tools/build-layout.json')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--sources-only', action='store_true')
    parser.add_argument('--configure-only', action='store_true')
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    workspace, paths = load_layout(args.config, args.workspace)
    sources = check_sources()
    if args.action == 'plan':
        print(json.dumps({'profile': 'u30pro-6.12', 'sources': sources,
                         'workspace': workspace, 'inputs': paths,
                         'output': TOP / 'release',
                         'kernel_command': kernel_command(paths, args.jobs, args.configure_only),
                         'device_actions': [], 'package_ready': False}, indent=2, default=str))
        return
    if args.sources_only:
        if args.action != 'check':
            parser.error('--sources-only applies to check')
        print(json.dumps(sources, indent=2))
        return
    inputs = check_inputs(paths)
    if args.action == 'check':
        print(json.dumps({**sources, **inputs}, indent=2))
        return
    if not os.environ.get('U30PRO_BUILD_LOCKED'):
        raise ValueError('Run compilation through tools/build-release.py run for locking and logs')
    env = dict(os.environ, U30PRO_WORKSPACE=str(workspace),
               U30PRO_KERNEL_SOURCE=str(paths['kernel_source']),
               U30PRO_KERNEL_OBJECTS=str(paths['kernel_objects']))
    subprocess.run(kernel_command(paths, args.jobs, args.configure_only), env=env, check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        sys.exit('U30 Pro build stopped: ' + str(error))
