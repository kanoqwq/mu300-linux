"""One-shot Android pre-mount F2FS maintenance; keys never leave Android.

The same Android slot temporarily boots its own verified maintenance ramdisk.
Magisk's documented overlay.d installs a wrapper before late-fs. Only the
verified decrypted mapper is resized, before it is mounted. The original slot
is restored from its verified backup before returning. No key, GPT or misc is written.
"""
import hashlib
import re
import shlex
import struct
import subprocess

from . import storage
from .state import RecoveryRequired, sha
from .transport import push_chunks
from .binary_io import pull_binary


MAGISKBOOT = '/data/adb/magisk/magiskboot'


def capability(adb):
    value = adb.shell('if [ -x '+MAGISKBOOT+' ]; then sha256sum '+MAGISKBOOT+
                      '; else echo MAGISKBOOT_UNAVAILABLE; fi',root=True).split()
    if not value or not re.fullmatch('[a-f0-9]{64}', value[0]):
        raise ValueError('Cannot prepare Android pre-mount growth: Magisk boot tools unavailable / 无法准备 Android 挂载前扩容：缺少 Magisk 启动工具')
    return value[0]


def identity_helper(record, adapter):
    if adapter.kind == 'e5' and storage.disk_node(record['device']['storage']['NODE']).startswith('sd'):
        path = adapter.root/'files/e5-ufs-id'
        data = path.read_bytes()
        if len(data)<20 or data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:
            raise ValueError('Package lacks the Android UFS identity executable / 安装包缺少 Android UFS 身份校验程序')
        return data
    return None


def verify_image(original, updated):
    """Permit a ramdisk edit, not a different kernel/command line/OS version."""
    if len(original) != 64 << 20 or len(updated) != len(original):
        raise ValueError('Android maintenance boot size differs')
    for data in (original, updated):
        if data[:8] != b'ANDROID!' or struct.unpack_from('<I', data, 40)[0] not in (3, 4):
            raise ValueError('Unsupported Android maintenance boot header')
    a, b = bytearray(original[:4096]), bytearray(updated[:4096])
    # Only the ramdisk length and v4 boot signature size may change.
    for header in (a, b):
        header[12:16] = bytes(4)
        if struct.unpack_from('<I', header, 40)[0] == 4:
            header[1580:1584] = bytes(4)
    size = struct.unpack_from('<I', original, 8)[0]
    end = 4096 + ((size + 4095) & ~4095)
    ramdisk = struct.unpack_from('<I', updated, 12)[0]
    if a != b or not size or not ramdisk or original[4096:end] != updated[4096:end] or end+ramdisk > len(updated)-4096:
        raise ValueError('Android maintenance boot changed outside its ramdisk')
    if original == updated:
        raise ValueError('Android maintenance hook was not added')


WRAPPER = r'''#!/system/bin/sh
set -eu
base=@BASE@
name=${0##*/}
case "$name" in fsck.f2fs|resize.f2fs) ;; *) exit 70 ;; esac
# Preserve stock invocation for other filesystems/devices. The real program
# lives under its original basename, including separate resize executables.
device=
for arg do device=$arg; done
case "$device" in /*) device=$(readlink -f "$device") ;; esac
node=${device##*/}
if ! [ -d "/sys/class/block/$node/dm" ] ||
   [ "$(ls /sys/class/block/$node/slaves)" != @NODE@ ]; then
    exec "$base/$name" "$@"
fi
exec >>"$base/worker.log" 2>&1
echo "Android pre-mount: $name $node"
fail() { echo "FAILED: $*" > "$base/result"; exit 71; }
[ "$(blockdev --getsize64 "$device")" = @TARGET@ ] || fail mapper-size
number=$(cat "/sys/class/block/$node/dev")
for mounts in /proc/self/mountinfo /proc/1/mountinfo; do
    ! awk -v n="$number" '$3==n {found=1} END {exit !found}' "$mounts" || fail mapper-mounted
done
measure() {
    dd if="$device" of="$base/super" bs=1024 skip=1 count=1 2>/dev/null
    [ "$(wc -c < "$base/super")" = 1024 ] || fail short-super
    [ "$(od -An -tx1 -N4 "$base/super" | tr -d ' \n')" = 1020f5f2 ] || fail magic
    [ "$(od -An -tx1 -j108 -N16 "$base/super" | tr -d ' \n')" = @UUID@ ] || fail filesystem-identity
    [ "$(od -An -tu4 -j16 -N4 "$base/super" | tr -d ' \n')" = 12 ] || fail block-size
    set -- $(od -An -tu4 -j36 -N8 "$base/super")
    bytes=$(( ($1 + ($2 << 32))*4096 ))
    [ "$bytes" -ge @BEFORE@ ] && [ "$bytes" -le @TARGET@ ] || fail filesystem-size
}
measure
if [ "$name" = fsck.f2fs ]; then
    "$base/fsck.f2fs" "$@" || { rc=$?; fail "stock-fsck-$rc"; }
fi
measure
if [ "$bytes" -lt @MINIMUM@ ]; then
    # Explicit size; never inherit a caller's safe-shrink or reserved-size flags.
    sector=$(blockdev --getss "$device")
    case "$sector" in 512|4096) ;; *) fail logical-sector-size ;; esac
    [ "$((@TARGET@ % sector))" = 0 ] || fail target-alignment
    "$base/resize.f2fs" -t "$((@TARGET@ / sector))" "$device" </dev/null || { rc=$?; fail "stock-resize-$rc"; }
fi
measure
[ "$bytes" -ge @MINIMUM@ ] || fail filesystem-not-grown
sync
echo GROWTH_VERIFIED > "$base/result"
'''


SETUP = r'''#!/system/bin/sh
set -eu
base=@BASE@
mkdir -p "$base"
chmod 700 "$base"
exec >>"$base/setup.log" 2>&1
@STORAGE@
@UFS_ENV@
[ "$(e5_storage_identity @DISK@)" = @CID@ ]
[ "$(getprop ro.boot.slot_suffix)" = _@SLOT@ ]
[ "$(cat /sys/class/block/@NODE@/size)" = @SECTORS@ ]
@GPT_CHECKS@
# Copy both native executables before binding either target. They can be the
# same symlink target; distinct inodes are handled too. Never modify /system.
cp /system/bin/fsck.f2fs "$base/fsck.f2fs"
cp /system/bin/resize.f2fs "$base/resize.f2fs"
[ "$(sha256sum "$base/fsck.f2fs" | cut -d' ' -f1)" = @FSCK_SHA@ ]
[ "$(sha256sum "$base/resize.f2fs" | cut -d' ' -f1)" = @RESIZE_SHA@ ]
cp "$1" "$base/wrapper"
chmod 755 "$base/fsck.f2fs" "$base/resize.f2fs" "$base/wrapper"
chcon u:object_r:magisk_file:s0 "$base/wrapper" "$base/fsck.f2fs" "$base/resize.f2fs"
fsck=$(readlink -f /system/bin/fsck.f2fs)
resizer=$(readlink -f /system/bin/resize.f2fs)
mount -o bind "$base/wrapper" "$fsck"
[ "$fsck" = "$resizer" ] || mount -o bind "$base/wrapper" "$resizer"
echo 'Wrapper installed; waiting for stock decrypted pre-mount check'
'''


FINISH = r'''#!/system/bin/sh
set -eu
base=@BASE@
[ "$(getprop ro.boot.slot_suffix)" = _@SLOT@ ]
# Preserve the actual pre-mount result across the return to original Android.
dir=/data/local/tmp/openwrt-uninstall-@ID@-android-growth
mkdir -p "$dir"
chmod 700 "$dir"
for file in setup.log worker.log result; do
    [ ! -f "$base/$file" ] || cp "$base/$file" "$dir/$file"
done
[ -f "$dir/result" ] || echo 'FAILED: stock pre-mount wrapper was not invoked' > "$dir/result"
# Restore the original Android image even when growth failed; keep the
# maintenance receipts. Only our exact installed image may be replaced.
[ "$(sha256sum "$dir/android.img" | cut -d' ' -f1)" = @ANDROID_SHA@ ]
current=$(sha256sum /dev/block/by-name/boot_@SLOT@ | cut -d' ' -f1)
wanted=$(sha256sum "$dir/maintenance.img" | cut -d' ' -f1)
[ "$current" = "$wanted" ] || [ "$current" = @ANDROID_SHA@ ]
if [ "$current" != @ANDROID_SHA@ ]; then
    dd if="$dir/android.img" of=/dev/block/by-name/boot_@SLOT@ bs=1048576 conv=fsync
fi
[ "$(sha256sum /dev/block/by-name/boot_@SLOT@ | cut -d' ' -f1)" = @ANDROID_SHA@ ]
sync
setprop sys.powerctl reboot
'''


def scripts(record, adapter, volume, android_sha):
    slot = record['device']['slot'].removeprefix('_')
    other = 'b' if slot == 'a' else 'a'
    identity = record['id']
    if not re.fullmatch('[a-zA-Z0-9-]{1,80}', identity):
        raise ValueError('Invalid maintenance transaction ID')
    node = record['device']['storage']['NODE']
    disk = storage.disk_node(node)
    target = record['plan']['android_bytes']
    before = record['uninstall']['volume']['filesystem_bytes']
    uuid = record['uninstall']['volume']['uuid']
    if not re.fullmatch('[a-f0-9]{32}', uuid) or target % 512 or before <= 0 or target <= before:
        raise ValueError('Invalid Android growth geometry')
    proof = volume['android_growth']
    # Both are copied from the live stock firmware, not package maintenance
    # binaries with potentially different vendor feature support.
    fsck_sha = adapter.adb.shell('sha256sum /system/bin/fsck.f2fs', root=True).split()[0]
    if not re.fullmatch('[a-f0-9]{64}', fsck_sha):
        raise ValueError('Cannot verify stock F2FS checker')
    meta = record['uninstall']
    checks = []
    for name, offset, count in [('after-first', 0, meta['front']), ('after-last', meta['tail'], meta['total']-meta['tail'])]:
        checks.append('[ "$(dd if=/dev/block/'+disk+' bs=512 skip='+str(offset)+' count='+str(count)+
                      ' 2>/dev/null | sha256sum | cut -d\' \''+' -f1)" = '+meta['checksums'][name]+' ]')
    library = storage.library(adapter.root)
    identity_function = 'mu300_storage_identity' if adapter.kind == 'mu300' else 'e5_storage_identity'
    values = {'BASE':'/dev/openwrt-grow-'+identity, 'NODE':node, 'DISK':disk,
              'ID':identity, 'SLOT':slot, 'OTHER':other, 'TARGET':str(target),
              'SECTORS':str(target//512), 'BEFORE':str(before), 'MINIMUM':str(target-(4 << 20)),
              'UUID':uuid, 'CID':record['device']['cid'], 'ANDROID_SHA':android_sha,
              'FSCK_SHA':fsck_sha, 'RESIZE_SHA':proof['resizer_sha256'],
              'GPT_CHECKS':'\n'.join(checks),
              'STORAGE':library, 'UFS_ENV':'export E5_UFS_ID_HELPER="$2"' if identity_helper(record,adapter) else ':'}
    def render(source):
        for key, value in values.items():
            source = source.replace('@'+key+'@', value)
        if re.search(r'@[A-Z_]+@', source):
            raise ValueError('Unexpanded Android growth template')
        return source.replace('e5_storage_identity', identity_function).encode()
    prefix = 'openwrt-grow-'+identity
    helper = identity_helper(record,adapter)
    rc = ('on post-fs\n    setprop sys.openwrt.growth '+identity+'\n'+
          '    exec u:r:magisk:s0 root root -- /system/bin/sh ${MAGISKTMP}/'+prefix+
          '-setup.sh ${MAGISKTMP}/'+prefix+'-wrapper.sh'+(' ${MAGISKTMP}/'+prefix+'-ufs-id' if helper else '')+'\n\non post-fs-data\n'+
          '    exec u:r:magisk:s0 root root -- /system/bin/sh ${MAGISKTMP}/'+prefix+'-finish.sh\n')
    files = {prefix+'.rc':rc.encode(), prefix+'-setup.sh':render(SETUP),
            prefix+'-wrapper.sh':render(WRAPPER), prefix+'-finish.sh':render(FINISH)}
    if helper:
        files[prefix+'-ufs-id'] = helper
    return files


def prepare_image(adb, ctx, remote, local, source, files, tool_sha):
    """Only ordinary staging files are written; this never arms or boots it."""
    adb.shell('umask 077; mkdir -p '+remote+'; chmod 700 '+remote+'; chown 2000:2000 '+remote, root=True)
    for name, content in files.items():
        path = local/name; path.write_bytes(content)
        push_chunks(adb.call, lambda s:adb.shell(s, root=True), path, remote+'/'+name, ctx)
    current = adb.shell('if [ -f '+remote+'/android.img ]; then sha256sum '+remote+'/android.img; fi',root=True).split()
    if not current or current[0] != sha(source):
        push_chunks(adb.call, lambda s:adb.shell(s, root=True), source, remote+'/android.img', ctx)
    build = ('set -eu; cd '+remote+'; [ "$(sha256sum '+MAGISKBOOT+
             ' | cut -d\' \''+' -f1)" = '+tool_sha+' ]; '+MAGISKBOOT+' unpack android.img; ')
    build += 'if '+MAGISKBOOT+' cpio ramdisk.cpio test; then rc=0; else rc=$?; fi; [ "$rc" = 1 ]; '
    build += MAGISKBOOT+' cpio ramdisk.cpio "mkdir 0750 overlay.d" "mkdir 0750 overlay.d/sbin"; '
    for name in files:
        dest = 'overlay.d/'+('' if name.endswith('.rc') else 'sbin/')+name
        build += MAGISKBOOT+' cpio ramdisk.cpio '+shlex.quote('add '+('0644' if name.endswith('.rc') else '0755')+' '+dest+' '+name)+'; '
    build += MAGISKBOOT+' repack android.img maintenance.img; chmod 644 maintenance.img'
    adb.shell(build, root=True, timeout=None)
    output = local/'maintenance.img'
    pull_binary(adb.call,adb.shell,'cat '+remote+'/maintenance.img',output,expected_size=64 << 20)
    updated = output.read_bytes()
    verify_image(source.read_bytes(), updated)
    return updated


def stage(worker, device, volume):
    """Build/verify the temporary Android image; arm it within a journal effect."""
    adb, ctx, record = worker.adapter.adb, worker.ctx, worker.r
    ctx.log('正在准备 Android 挂载前扩容，完成后恢复原启动镜像 / Preparing stock Android pre-mount growth with boot restoration',scope='android-growth')
    if worker.layout_state() != 'after' or device['userdata_bytes'] != record['plan']['android_bytes']:
        raise RecoveryRequired('Android growth requires the verified restored partition layout')
    tool_sha = capability(adb)
    slot = device['slot'].removeprefix('_')
    remote = '/data/local/tmp/openwrt-uninstall-'+record['id']+'-android-growth'
    local = ctx.directory/'uninstall/android-growth'
    local.mkdir(parents=True, exist_ok=True)
    boot_path = '/dev/block/by-name/boot_'+slot
    source = local/'android.img'
    pull_binary(adb.call,adb.shell,'dd if='+boot_path+' bs=1048576 count=64 2>/dev/null',source,expected_size=64 << 20)
    boot = source.read_bytes()
    android_sha = hashlib.sha256(boot).hexdigest()
    # Keep the current working Android image, independently of any earlier
    # pre-reset backup. Only its ramdisk changes temporarily; the stock kernel
    # and the actual Android slot (including logical system/vendor) are kept.
    files = scripts(record, worker.adapter, volume, android_sha)
    updated = prepare_image(adb,ctx,remote,local,source,files,tool_sha)
    patched = local/'maintenance.img'; patched.write_bytes(updated)
    patched_sha = sha(patched)
    record.update(uninstall_android_growth_sha256=patched_sha,
                  uninstall_android_growth_source_sha256=android_sha,
                  uninstall_android_growth_attempted=True,
                  uninstall_growth_pending=True,
                  uninstall_wait_boot_id=device['boot_id'])
    ctx.store.save(record)
    if worker.layout_state() != 'after':
        raise RecoveryRequired('Layout changed during Android growth preparation')
    script = ('set -eu; set -o pipefail; [ "$(getprop ro.boot.slot_suffix)" = _'+slot+' ]; '+
              '[ "$(sha256sum '+boot_path+' | cut -d\' \''+' -f1)" = '+android_sha+' ]; '+
              '[ "$(sha256sum '+remote+'/android.img | cut -d\' \''+' -f1)" = '+android_sha+' ]; '+
              '[ "$(sha256sum '+remote+'/maintenance.img | cut -d\' \''+' -f1)" = '+patched_sha+' ]; '+
              'chown -R 0:0 '+remote+'; chmod 700 '+remote+'; '+
              'if dd if='+remote+'/maintenance.img of='+boot_path+' bs=1048576 conv=fsync && '+
              '[ "$(sha256sum '+boot_path+' | cut -d\' \''+' -f1)" = '+patched_sha+' ]; then :; else '+
              'dd if='+remote+'/android.img of='+boot_path+' bs=1048576 conv=fsync; '+
              '[ "$(sha256sum '+boot_path+' | cut -d\' \''+' -f1)" = '+android_sha+' ]; '+
              'echo ANDROID_GROWTH_BOOT_RESTORED; exit 1; fi')
    ctx.stage('uninstall_prepare')
    key = 'uninstall-android-growth-arm'
    if record['effects'].get(key):
        record['effects'].pop(key)
        ctx.store.save(record)
    ctx.effect(key, lambda:adb.shell(script, root=True, timeout=None))
    ctx.stage('uninstall_offline', 'waiting_device', reboot_manually=False)
    try:
        adb.call('reboot')
    except (ConnectionError, OSError, TimeoutError, subprocess.TimeoutExpired):
        ctx.log('Expected ADB loss during one-shot Android pre-mount growth', scope='uninstall')


def report(worker):
    remote = '/data/local/tmp/openwrt-uninstall-'+worker.r['id']+'-android-growth'
    text = worker.adapter.adb.shell('for f in result setup.log worker.log; do '
                                   '[ ! -f '+remote+'/$f ] || tail -c 12000 '+remote+'/$f; done', root=True)
    worker.ctx.log(text, scope='android-growth')
    return text


def restore(worker, device):
    """Recover an interrupted hook/restore by exact boot readback, never GPT."""
    record, adb, ctx = worker.r, worker.adapter.adb, worker.ctx
    if not record.get('uninstall_android_growth_attempted'):
        return
    expected = record.get('uninstall_android_growth_source_sha256')
    patched = record.get('uninstall_android_growth_sha256')
    source = ctx.directory/'uninstall/android-growth/android.img'
    if (not re.fullmatch('[a-f0-9]{64}',expected or '') or
            not re.fullmatch('[a-f0-9]{64}',patched or '') or
            not source.is_file() or source.stat().st_size != 64 << 20 or sha(source) != expected):
        raise RecoveryRequired('Android growth boot backup is damaged / Android 扩容启动备份损坏，请保留备份')
    if device['slot'] != record['device']['slot'] or worker.layout_state() != 'after':
        raise RecoveryRequired('Android growth recovery identity/layout changed')
    slot = device['slot'].removeprefix('_'); target='/dev/block/by-name/boot_'+slot
    current = adb.shell('sha256sum '+target,root=True).split()[0]
    if current == expected:
        # ADB may have disconnected after the device-side commit. Exact boot
        # readback proves this step independently of its host-side receipt.
        entry=record['effects'].get('uninstall-android-growth-arm')
        if entry:
            entry.update(state='done',result='original Android restored by readback')
            record['unsafe']=False;ctx.store.save(record)
        return adb.shell('getprop sys.openwrt.growth',root=True).strip() == record['id']
    if current != patched:
        raise RecoveryRequired('Android growth boot is neither the backup nor our maintenance image / Android 启动镜像与扩容备份不符，不会覆盖')
    remote='/data/local/tmp/openwrt-uninstall-'+record['id']+'-growth-restore'
    adb.shell('umask 077; mkdir -p '+remote+'; chown 2000:2000 '+remote+'; chmod 700 '+remote,root=True)
    push_chunks(adb.call,lambda s:adb.shell(s,root=True),source,remote+'/android.img',ctx)
    script=('set -eu; set -o pipefail; [ "$(getprop ro.boot.slot_suffix)" = _'+slot+' ]; '+
            '[ "$(sha256sum '+target+' | cut -d\' \''+' -f1)" = '+patched+' ]; '+
            '[ "$(sha256sum '+remote+'/android.img | cut -d\' \''+' -f1)" = '+expected+' ]; '+
            'dd if='+remote+'/android.img of='+target+' bs=1048576 conv=fsync; '+
            '[ "$(sha256sum '+target+' | cut -d\' \''+' -f1)" = '+expected+' ]')
    key='uninstall-android-growth-restore'
    if record['effects'].get(key):
        record['effects'].pop(key);ctx.store.save(record)
    ctx.effect(key,lambda:adb.shell(script,root=True,timeout=None))
    return True


def cleanup(worker):
    """Remove only this successful transaction's ordinary staging files."""
    record=worker.r
    if not record.get('uninstall_android_growth_attempted'):
        return
    report(worker)
    prefix='openwrt-grow-'+record['id']
    remote='/data/local/tmp/openwrt-uninstall-'+record['id']+'-android-growth'
    names=['android.img','maintenance.img','kernel','ramdisk.cpio','header','dtb',
           'kernel_dtb','extra','second','recovery_dtbo','result','setup.log','worker.log',
           prefix+'.rc',prefix+'-setup.sh',prefix+'-wrapper.sh',prefix+'-finish.sh',prefix+'-ufs-id']
    restore_dir='/data/local/tmp/openwrt-uninstall-'+record['id']+'-growth-restore'
    worker.adapter.adb.shell('rm -f '+' '.join(remote+'/'+name for name in names)+' '+
                             restore_dir+'/android.img; rmdir '+remote+' '+restore_dir+' 2>/dev/null || true',root=True)
