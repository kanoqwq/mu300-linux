"""U30 Pro boot-container adapter. No partition planning or resize algorithms.

The maintained MU300 installer supplies the canonical kernel/ramdisk and A/B
record. Read this device's stock containers and construct its three boot images.
All transfers use the existing binary ADB transport, never SPD/fastboot/base64.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import struct


def enabled(root):
    path = Path(root) / 'desktop/manifest.json'
    return path.is_file() and json.loads(path.read_text(encoding='utf-8')).get('platform') == 'u30pro'


def codec(root):
    spec = importlib.util.spec_from_file_location('u30pro_boot_codec', Path(root) / 'desktop/boot-runtime.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def images(root, canonical, templates):
    return codec(root).native_images(canonical, templates)


def flash(android, canonical, bc, root, gui, *, dry_run=False, before_write='', before_misc='', preserve_bcb=False):
    root = Path(root)
    if android.slot not in ('a', 'b') or android.linux == android.slot:
        raise ValueError('Unconfirmed Android/Linux slot relation')
    if gui:
        destination = gui.ctx.directory / 'backup'
    else:
        destination = getattr(android, 'native_backup', None)
        if destination is None:
            raise ValueError('Verified computer backup is required before boot writes')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    record_path = destination / 'native-before.json'
    record = json.loads(record_path.read_text()) if record_path.is_file() else {
        'serial': android.serial, 'android_slot': android.slot, 'files': {}, 'planned': {}}
    if record.get('serial') != android.serial or record.get('android_slot') != android.slot:
        raise ValueError('Native boot backup belongs to another device or Android slot')
    canonical_sha = hashlib.sha256(canonical).hexdigest()
    worker = record.get('worker')
    if worker:
        if not worker.startswith('/data/local/tmp/mu300-desktop-') or not worker.endswith('/run.sh'):
            raise ValueError('Invalid native boot worker receipt')
        active = android.shell('for f in /proc/[0-9]*/cmdline; do '
            'tr "\\000" "\\n" < "$f" 2>/dev/null | grep -Fx '+shlex.quote(worker)+' && echo NATIVE_BUSY; done; true')
        if 'NATIVE_BUSY' in active.split():
            raise ValueError('Native boot write is still running / 原生启动写入仍在运行，请稍后重新检查')
    pending = record.get('write_started') and not record.get('committed')
    retrying = pending and (record.get('canonical_sha256') == canonical_sha or dry_run)
    templates, before = {}, {}
    sizes = {}
    for name in ('boot', 'vendor_boot', 'init_boot', 'dtbo'):
        for slot in (android.slot, android.linux):
            part = name + '_' + slot
            path = destination / (part + '.bin')
            size = int(android.shell('blockdev --getsize64 /dev/block/by-name/' + part).strip())
            sizes[part] = size
            # Bind an existing transaction backup to today's device bytes.
            remote_sha = android.shell('sha256sum /dev/block/by-name/' + part).split()[0]
            if path.exists():
                local_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.stat().st_size != size or record['files'].get(part, local_sha) != local_sha:
                    raise ValueError('Damaged native boot recovery backup: ' + part)
                allowed = [local_sha]
                if slot == android.linux:
                    allowed.append(record.get('planned', {}).get(name))
                if remote_sha not in allowed and not (slot == android.linux and retrying):
                    raise ValueError('Boot partition changed outside this transaction: ' + part)
            else:
                android.download('dd if=/dev/block/by-name/' + part + ' bs=1048576 2>/dev/null', path, expected_size=size)
                if hashlib.sha256(path.read_bytes()).hexdigest() != remote_sha:
                    raise ValueError('Boot backup checksum mismatch: ' + part)
            record['files'][part] = hashlib.sha256(path.read_bytes()).hexdigest()
            record_path.write_text(json.dumps(record, indent=2) + '\n')
            if slot == android.slot:
                if name != 'dtbo':
                    templates[name] = path.read_bytes()
                else:
                    dtbo_source = path
            else:
                before[name] = path
    outputs = images(root, canonical, templates)
    outputs['dtbo'] = dtbo_source.read_bytes()
    for name, data in outputs.items():
        if len(data) != sizes[name + '_' + android.linux]:
            raise ValueError('Native image/target size mismatch before writing: ' + name)
    if not (dry_run and pending):
        record['planned'] = {name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()}
        record['canonical_sha256'] = canonical_sha
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    checks_path = destination / 'checksums.json'
    checks = json.loads(checks_path.read_text()) if checks_path.is_file() else {}
    checks.update({part + '.bin': value for part, value in record['files'].items()})
    checks['native-before.json'] = hashlib.sha256(record_path.read_bytes()).hexdigest()
    checks_path.write_text(json.dumps(checks, indent=2) + '\n')
    def save_record():
        temporary = record_path.with_suffix('.new')
        temporary.write_text(json.dumps(record, indent=2)+'\n')
        temporary.replace(record_path)
        checks['native-before.json'] = hashlib.sha256(record_path.read_bytes()).hexdigest()
        temporary = checks_path.with_suffix('.new')
        temporary.write_text(json.dumps(checks, indent=2)+'\n')
        temporary.replace(checks_path)
    if dry_run:
        return {'verified': True, 'device_writes': False, 'backup': str(destination)}
    generated = destination.parent / 'native-boot'
    generated.mkdir(exist_ok=True)
    for name, data in outputs.items():
        path = generated / (name + '.img')
        path.write_bytes(data)
        android.push(path, 'native-' + name + '.img')
    for name, path in before.items():
        android.push(path, 'native-before-' + name + '.img')
    android.put(bc, 'misc-linux.bin')
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in outputs.items()}
    sums = ''.join(digest + '  native-' + name + '.img\n' for name, digest in manifest.items())
    android.put(sums, 'native-SHA256SUMS')
    record.update(write_started=True, committed=False,worker=android.p+'/run.sh')
    save_record()
    # Commit misc last. A failed write restores and verifies all affected Linux
    # containers; Android slot, GPT, userdata and NV are never written here.
    android.run('''set -e
[ "$(getprop ro.boot.slot_suffix)" = "_''' + android.slot + '''" ]
cd "$P"
sha256sum -c native-SHA256SUMS
''' + before_write + '''
# All containers must fit before the first partition write.
for name in boot vendor_boot init_boot dtbo; do
    [ "$(blockdev --getsize64 /dev/block/by-name/${name}_''' + android.linux + ''')" = "$("$BB" stat -c %s "$P/native-$name.img")" ]
done
dd if=/dev/block/by-name/misc of="$T/misc-before.bin" bs=1 skip=2048 count=32
[ "$("$BB" stat -c %s "$T/misc-before.bin")" = 32 ]
''' + ('dd if=/dev/block/by-name/misc of="$T/bcb-before.bin" bs=2048 count=1\n' if preserve_bcb else '') + '''
restore_native() {
    result=0
    for name in boot vendor_boot init_boot dtbo; do
        dev=/dev/block/by-name/${name}_''' + android.linux + '''
        dd if="$P/native-before-$name.img" of="$dev" bs=1048576 conv=fsync || result=1
        [ "$(sha256sum "$dev" | cut -d ' ' -f1)" = "$(sha256sum "$P/native-before-$name.img" | cut -d ' ' -f1)" ] || result=1
    done
    return "$result"
}
committed=0
restore_on_exit() {
    rc=$?
    trap - EXIT INT TERM
    if [ "$committed" != 1 ]; then
        set +e
        restore_native || echo 'BOOT RESTORE FAILED; do not switch slots'
''' + ('        dd if="$T/bcb-before.bin" of=/dev/block/by-name/misc bs=2048 count=1 conv=notrunc,fsync\n'
       '        dd if=/dev/block/by-name/misc of="$T/bcb-check.bin" bs=2048 count=1\n'
       '        cmp -s "$T/bcb-before.bin" "$T/bcb-check.bin" || echo "BCB RESTORE FAILED"\n' if preserve_bcb else '') + '''
        dd if="$T/misc-before.bin" of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc,fsync
        dd if=/dev/block/by-name/misc of="$T/misc-check.bin" bs=1 skip=2048 count=32
        cmp -s "$T/misc-before.bin" "$T/misc-check.bin" || echo 'MISC RESTORE FAILED'
    fi
    exit "$rc"
}
trap restore_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$BB" sh "$P/android-bootchain.sh" sync "$T/bootchain-backup"
for name in boot vendor_boot init_boot dtbo; do
    dev=/dev/block/by-name/${name}_''' + android.linux + '''
    if [ "$(blockdev --getsize64 "$dev")" != "$("$BB" stat -c %s "$P/native-$name.img")" ] ||
       ! dd if="$P/native-$name.img" of="$dev" bs=1048576 conv=fsync ||
       [ "$(sha256sum "$dev" | cut -d ' ' -f1)" != "$(sha256sum "$P/native-$name.img" | cut -d ' ' -f1)" ]; then
        exit 1
    fi
done
''' + before_misc + '''
dd if="$P/misc-linux.bin" of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc,fsync
dd if=/dev/block/by-name/misc of="$T/misc-check.bin" bs=1 skip=2048 count=32
if ! cmp -s "$P/misc-linux.bin" "$T/misc-check.bin"; then
    exit 1
fi
committed=1
echo 'BOOT-VERIFIED; OpenWrt selected'
# Only this writer's disposable uploads. Computer recovery backups remain.
for name in boot vendor_boot init_boot dtbo; do
    rm -f "$P/native-$name.img" "$P/native-before-$name.img" || :
done
rm -f "$P/native-SHA256SUMS" "$P/misc-linux.bin" "$T/misc-before.bin" "$T/misc-check.bin" "$T/bcb-before.bin" "$T/bcb-check.bin" || :
''')
    record['committed'] = True
    save_record()
    try:
        for name in outputs:
            (generated/(name+'.img')).unlink(missing_ok=True)
        generated.rmdir()
    except OSError as error:
        print('Boot verified; local generated-image cleanup can be retried / 启动已校验，本地生成文件可稍后清理: '+str(error))
