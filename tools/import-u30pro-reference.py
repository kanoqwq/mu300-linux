#!/usr/bin/env python3
"""Import only reviewed U30 Pro hardware sources from an exact Git revision.

This does not copy a checkout, binaries, firmware, private inputs, old flashers,
the old LuCI application or its A/B updater. Existing local edits are not
overwritten. The manifest records the original blob and executable mode.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
DEST = TOP / 'devices/u30pro/native'
PREVIOUS_REVISION = '7bcb40ac769ebcdeae0fd550c7a637d3d6dae5fc'
REVISION = 'df3b44fce648105aa785204571f8ad6317a95959'
UPSTREAM = 'https://github.com/33333s/U30pro-openwrt.git'
KERNEL_TOOLS = {
    'apply_overlay.py', 'build_linux.py', 'build_stage0.py', 'wrap_image.py',
    'vendor-origins.json', 'upstream-adaptations.json',
}
NATIVE_SOURCES = {'align_image.S', 'stage0_linux.c', 'u30pro_watchdog_keeper.c'}
TOOLS = {
    'firmware.py',
    'build_static.py', 'build_native_panel.py', 'build_ui.py', 'ubus_sysroot.py',
    'gen_ui_font.py', 'gen_t2s.py', 'u30pro_ui.c', 'u30pro_panel.c',
    'u30pro_atc.c', 'u30pro_atcli.c', 'u30pro_atd.c', 'u30pro_celld.c',
    'u30pro_nvsyncd.c', 'u30pro_refnotifyd.c', 'u30pro_nfc.c',
}
SERVICES = {
    'u30pro-atd', 'u30pro-battlog', 'u30pro-cellular',
    'u30pro-charger-policy', 'u30pro-native', 'u30pro-panel', 'u30pro-safety',
}
HELPERS = {
    'u30pro-atc', 'u30pro-battlog', 'u30pro-cellular-start', 'u30pro-charger',
    'u30pro-cpu-apply', 'u30pro-cpuidle-limit', 'u30pro-modem-early',
    'u30pro-native-watchdog-start', 'u30pro-safety', 'u30pro-ui-feed',
    'u30pro-usb', 'u30pro-wifi-sdio', 'u30pro-wlan-tune', 'u30pro-zram',
}
LIBRARIES = {
    'battery-log.uc', 'cpu-apply.uc', 'cpu-rpc.uc', 'cpu.uc', 'dial.uc',
    'modem.uc', 'native-modemlog.uc', 'safety.uc', 'sms.uc', 'ui-feed.uc',
    'wireless-defaults.uc', 'wireless.uc', 'wallpaper.uc',
}
TESTS = {
    'test_atd.py', 'test_cellular_native.py', 'test_nvsyncd_writeback.py',
    'test_charger.py', 'test_cpu.uc', 'test_safety.uc', 'test_battery_log.uc',
}


def selected(name):
    p = PurePosixPath(name)
    if name in {
        'feed/packages/u30pro-feed/files/etc/apk/keys/u30pro-feed.pem',
        'feed/packages/u30pro-feed-immortalwrt/files/etc/apk/keys/immortalwrt-25.12.pem',
        'feed/packages/u30pro-feed-immortalwrt/files/etc/apk/repositories.d/immortalwrt.list',
        'ui/native-control/overlay/usr/libexec/rpcd/u30pro-wallpaper',
        'ui/native-control/overlay/usr/share/rpcd/acl.d/u30pro-wallpaper.json',
        'ui/native-control/overlay/www/luci-static/resources/view/system/u30pro-wallpaper.js',
        'ui/native-control/overlay/www/luci-static/resources/u30pro/kit.js',
        'ui/native-control/overlay/www/luci-static/resources/u30pro/kit.css',
    }:return True
    if name.startswith('modem-native-re/src/') and p.suffix in ('.c', '.h'):
        return True
    if name == 'LICENSE':
        return True
    if name.startswith('kernel/ums9632/'):
        tail = name.removeprefix('kernel/ums9632/')
        return (tail.startswith(('overlay/', 'patches/')) or
                '/' not in tail and (tail.endswith('.config') or tail in KERNEL_TOOLS) or
                tail.startswith('native/') and p.name in NATIVE_SOURCES)
    if name.startswith('tools/'):
        return name.startswith('tools/ui/') or name.removeprefix('tools/') in TOOLS
    if name.startswith('tests/'):
        return name.removeprefix('tests/') in TESTS or name.startswith('tests/ui/')
    prefix = 'ui/native-control/overlay/'
    if name.startswith(prefix):
        tail = name.removeprefix(prefix)
        return (tail in {'etc/config/u30pro_modem', 'etc/config/u30pro_native_control',
                         'etc/modules.d/60-aic8800', 'lib/netifd/proto/u30cell.sh'} or
                tail.startswith('etc/init.d/') and p.name in SERVICES or
                tail.startswith('usr/sbin/') and p.name in HELPERS or
                tail.startswith('usr/share/u30pro/') and p.name in LIBRARIES)
    return False


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])


def import_sources(source, destination=DEST, revision=REVISION, extend=False, update=False):
    # Read committed blobs, never uncommitted files from the reference checkout.
    commit = git(source, 'rev-parse', revision + '^{commit}').decode().strip()
    if commit != REVISION:
        raise ValueError('Reference revision is not the reviewed U30 Pro revision')
    adaptation_path = destination.parent / 'adaptations.json'
    adaptations = json.loads(adaptation_path.read_text(encoding='utf-8')) if adaptation_path.exists() else {}
    manifest_path = destination.parent / 'reference.json'
    previous = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else None
    if update:
        if not previous or previous.get('revision') != PREVIOUS_REVISION:
            raise ValueError('Source update requires the reviewed previous revision')
        subprocess.run(['git','-C',str(source),'merge-base','--is-ancestor',PREVIOUS_REVISION,commit],check=True)
    records, pending = {}, []
    for entry in git(source, 'ls-tree', '-rz', commit).split(b'\0'):
        if not entry:
            continue
        meta, raw_name = entry.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        if not selected(name):
            continue
        mode, kind, blob = meta.decode().split()
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('Unexpected non-regular reference source: ' + name)
        target = destination / name
        adapted = adaptations.get(name)
        old=previous['files'].get(name) if previous else None
        # The tree's blob identity plus our retained SHA receipt already proves
        # unchanged sources. Don't redownload hundreds of identical old blobs.
        if (update or extend) and old and blob==old['git_blob']:
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=(adapted or old)['sha256']:
                raise ValueError('Local source has unrecorded edits: '+name)
            records[name]=old
            continue
        data = git(source, 'cat-file', 'blob', blob)
        if update and target.exists():
            old = previous['files'].get(name)
            if not old or target.is_symlink():
                raise ValueError('Untracked local source cannot be overwritten: '+name)
            current=target.read_bytes()
            if hashlib.sha256(current).hexdigest() != (adapted or old)['sha256']:
                raise ValueError('Local source has unrecorded edits: '+name)
            base=git(source,'cat-file','blob',old['git_blob'])
            merged=data
            if current!=base:
                if data==base:merged=current
                else:
                    with tempfile.TemporaryDirectory() as folder:
                        files=[Path(folder)/n for n in ('current','base','upstream')]
                        for f,body in zip(files,[current,base,data]):f.write_bytes(body)
                        result=subprocess.run(['git','merge-file','-p',*map(str,files)],capture_output=True)
                        if result.returncode:raise ValueError('Review source merge conflict: '+name)
                        merged=result.stdout
            if merged!=data:
                adaptations[name]={'source_sha256':hashlib.sha256(data).hexdigest(),
                    'sha256':hashlib.sha256(merged).hexdigest(),'reason':adapted['reason']}
            else:adaptations.pop(name,None)
            pending.append((target,merged,mode))
            records[name]={'sha256':hashlib.sha256(data).hexdigest(),'git_blob':blob,'mode':mode}
            continue
        keep_local = (adapted and adapted.get('source_sha256') == hashlib.sha256(data).hexdigest()
                      and adapted.get('reason') and target.is_file()
                      and hashlib.sha256(target.read_bytes()).hexdigest() == adapted.get('sha256'))
        if target.is_symlink() or target.exists() and target.read_bytes() != data and not keep_local:
            raise ValueError('Refusing to overwrite a local adaptation: ' + name)
        pending.append((target, data, mode))
        records[name] = {'sha256': hashlib.sha256(data).hexdigest(),
                         'git_blob': blob, 'mode': mode}
    if not records:
        raise ValueError('No reviewed hardware sources found')
    manifest = {'format': 1, 'repository': UPSTREAM, 'revision': commit,
                'files': records}
    if manifest_path.exists():
        if previous != manifest and not update and not (extend and previous.get('revision') == commit and
                all(records.get(name) == record for name, record in previous['files'].items())):
            raise ValueError('Reference manifest differs; review the source update explicitly')
    for target, data, mode in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        if update or not target.exists():
            target.write_bytes(data)
            target.chmod(0o755 if mode == '100755' else 0o644)
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if update:adaptation_path.write_text(json.dumps(adaptations,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--extend', action='store_true', help='add newly reviewed source paths at the same pinned revision')
    parser.add_argument('--update',action='store_true',help='advance reviewed hardware sources and merge recorded local adaptations')
    args = parser.parse_args()
    result = import_sources(args.source, extend=args.extend,update=args.update)
    print(f"Imported {len(result['files'])} reviewed sources at {result['revision']}")
