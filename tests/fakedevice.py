"""A fake F50 for the Magisk installer: block devices are files, sysfs a directory tree, Android's tools stubs.
The device shell (MU300_DEVICE_SH) rewrites the absolute device paths in the commands storage.sh sends, the way
tests/test_installer.py's Region does; android-install.sh and the mount helper are replaced by MU300_ANDROID_SH,
which records what it was given and acts on a directory standing in for the Linux filesystem. A mount is a real
directory holding a copy of that filesystem and a line in a fake mount table (MU300_MOUNTS), so a cleanup that
deleted a mounted filesystem would delete files a test can look for."""
import os
import shutil

from test_boot_image import LIVE_A, fake_misc, fake_stock, with_slots

ANDROID_SH = r'''#!/bin/sh
# stand-in for /system/bin/sh running android-install.sh or android-mount-mu300root.sh
log=$FAKE/android-sh.log
echo "ASH_STANDALONE=${ASH_STANDALONE:-} PATH=$PATH $* MU300_RO=${MU300_RO:-} MU300_DEVICE_WORK=${MU300_DEVICE_WORK:-} MU300_LOCK_DIR=${MU300_LOCK_DIR:-} $(ls -ld "${MU300_DEVICE_WORK:-/nonexistent}" 2>/dev/null | cut -c1-10)" >> "$log"
case $1 in
    -c) shift; exec "$FAKE/devsh" -c "$@" ;;
    # the real one, on the fake by-name directory (MU300_BY_NAME), with Android's getprop
    */android-bootchain.sh) PATH="$FAKE/bin:$PATH" exec sh "$@" ;;
    # the real one, on the mounted copy; its chroot runs the command on this machine (the test's MU300_CHROOT)
    */android-pwcheck.sh) exec sh "$@" ;;
    */android-install.sh)
        cp "$MU300_DEVICE_WORK/mu300-install.env" "$FAKE/install.env"
        [ "${FAKE_INSTALL_FAILS:-0}" = 1 ] && { echo "[device] failing as asked"; exit 1; }
        . "$MU300_DEVICE_WORK/mu300-install.env"
        # what would be installed: the tarballs it is given, by checksum
        for os in $OSES; do sha256sum "$MU300_DEVICE_WORK/mu300-$os.tar.gz" >> "$FAKE/installed.sha256"; mkdir -p "$FAKE/fs/$os/etc"
            # an installed system has its accounts (android-install.sh: the image's, the hash put in or carried over)
            [ -f "$FAKE/fs/$os/etc/shadow" ] || echo "root:\$6\$fake:19000::::::" > "$FAKE/fs/$os/etc/shadow"; done
        # what a test wants to happen while the systems are installed (tests' fake_install_hook)
        [ ! -f "$FAKE/install-hook" ] || . "$FAKE/install-hook"
        echo MU300-INSTALL-OK ;;
    */android-mount-mu300root.sh)
        # a mount is a real directory holding a copy of $FAKE/fs, listed in $FAKE/mounts; unmounting a read-write
        # one writes it back to $FAKE/fs, then empties it
        if [ "$2" = -u ]; then
            [ "${FAKE_UMOUNT_FAILS:-0}" = 1 ] && { echo "umount: $3: Device or resource busy" >&2; exit 1; }
            grep -q " $3 ext4 ro " "$FAKE/mounts" || cp -R "$3/." "$FAKE/fs/"
            rm -rf "$3"/* "$3"/.[!.]*
            grep -v " $3 " "$FAKE/mounts" > "$FAKE/mounts.new"; mv "$FAKE/mounts.new" "$FAKE/mounts"
            echo UNMOUNTED
        else
            [ "${FAKE_MOUNT_FAILS:-0}" = 1 ] && { echo "ALREADY-MOUNTED loop7"; exit 1; }
            mkdir -p "$2" && cp -R "$FAKE/fs/." "$2/"
            echo "/dev/block/loop7 $2 ext4 $([ -n "${MU300_RO:-}" ] && echo ro || echo rw) 0 0" >> "$FAKE/mounts"
            echo "MOUNTED fake on $2"
        fi ;;
esac
'''
# A device path that comes back from the device (sd_probe's /dev/block/mmcblk1) is already rewritten when storage.sh
# sends it again (sd_existing's dd): the last expression undoes the second prefix.
DEVSH = r'''#!/bin/sh
# the device's shell for su_do: absolute device paths point into the fake device
[ "$1" = -c ] && shift
exec sh -c "$(printf '%s' "$1" | sed -e "s|/sys/|$FAKE/sys/|g" -e "s|/dev/block/|$FAKE/dev/block/|g" -e "s|$FAKE$FAKE/|$FAKE/|g")"
'''
GETPROP = r'''#!/bin/sh
sed -n "s/^$1=//p" "$FAKE/props"
'''
SM = r'''#!/bin/sh
case $1 in list-volumes) cat "$FAKE/volumes" ;; unmount) : ;; esac
'''
UBB = r'''#!/bin/sh
# the bundled busybox: only mkpasswd is used
[ "$1" = mkpasswd ] || exit 1
while [ $# -gt 0 ]; do case $1 in -S) salt=$2; shift ;; esac; shift; done
read -r pw
printf '$6$%s$%s\n' "$salt" "$(printf '%s' "$pw" | od -An -tx1 | tr -d ' \n' | cut -c1-20)"
'''


class FakeDevice:
    # product: ro.product.device; the installer recognises the model from it too, so another model gets another one
    def __init__(self, tmp, stubs, model='ZTE MU300', slot='_a', emmc_gib=16, region=True, card=None, product=None,
                 platform=None):
        self.root = tmp / 'fake'
        r = self.root
        if product is None:
            product = 'mu300' if model == 'ZTE MU300' else model.lower().replace(' ', '')
        # tmp is /data/local/tmp, which the installer must never use; data/adb holds its work directory
        for d in ('dev/block/by-name', 'sys/block/mmcblk0/mmcblk0p1', 'sdcard', 'tmp', 'data/adb', 'fs', 'magisk',
                  'android'):
            (r / d).mkdir(parents=True, exist_ok=True)
        by = r / 'dev/block/by-name'
        fake_stock(by / 'boot_a')
        shutil.copy(by / 'boot_a', by / 'boot_b')
        # what misc holds while Android runs from that slot (a valid block: magic and CRC)
        fake_misc(by / 'misc', LIVE_A if slot == '_a' else with_slots(LIVE_A, b'_b\0\0', 0x1e, 0x9f))
        # the eMMC is as large as sysfs says (sparse): a read where nothing was written gives zeros, as on a device -
        # an empty file gave no answer at all, which the installers now take for a device that does not answer
        with open(r / 'dev/block/mmcblk0', 'wb') as f:
            f.truncate(emmc_gib << 30)
        # the boot chain, the same build on both slots (FakeDevice.bootchain changes one)
        for p in ('uboot', 'sml', 'teecfg', 'trustos', 'vbmeta'):
            for s in 'ab':
                (by / f'{p}_{s}').write_bytes(p.encode() * 1024)
        # partitions end at 8 GiB; a 16 GiB eMMC leaves a ~8 GiB region, a 9 GiB one too little
        sysb = r / 'sys/block/mmcblk0'
        (sysb / 'mmcblk0p1/start').write_text('2048\n')
        (sysb / 'mmcblk0p1/size').write_text(f'{(8 << 21) if region else (emmc_gib << 21) - 4096}\n')
        (sysb / 'size').write_text(f'{emmc_gib << 21}\n')
        if card is not None:
            c = r / 'sys/block/mmcblk1'
            (c / 'device').mkdir(parents=True)
            (c / 'device/type').write_text('SD\n')
            (c / 'size').write_text(f'{32 << 21}\n')
            (r / 'dev/block/mmcblk1').write_bytes(card)
        (r / 'props').write_text(f'ro.product.model={model}\nro.product.device={product}\nro.boot.slot_suffix={slot}\n'
                                 'persist.sys.locale=en-US\n' + (f'ro.board.platform={platform}\n' if platform else ''))
        (r / 'volumes').write_text('private mounted null\n')
        (r / 'mounts').write_text('')
        # /proc/sys/kernel/random/boot_id: the key of the lock a timed-out SD-card stage leaves (FINDINGS 50)
        (r / 'boot_id').write_text('11111111-2222-3333-4444-555555555555\n')
        for name, body in (('android-sh', ANDROID_SH), ('devsh', DEVSH), ('ubb', UBB)):
            (r / name).write_text(body); (r / name).chmod(0o755)
        for name, body in (('getprop', GETPROP), ('sm', SM)):
            (stubs / name).write_text(body); (stubs / name).chmod(0o755)
        (r / 'bin').mkdir(exist_ok=True)
        (r / 'bin/getprop').write_text(GETPROP); (r / 'bin/getprop').chmod(0o755)

    def env(self, mu300_dir, magiskboot):
        r = self.root
        return {'FAKE': r, 'MU300_DIR': mu300_dir, 'MU300_WORK_PARENT': r / 'data/adb',
                'MU300_TRUSTED_CONF': r / 'data/adb/mu300-install.conf', 'MU300_TRUSTED_UID': os.getuid(),
                'MU300_PW_DIR': r / 'data/adb', 'MU300_MOUNTS': r / 'mounts', 'MU300_BY_NAME': r / 'dev/block/by-name',
                'MU300_SDCARD': r / 'sdcard', 'MU300_DEVICE_SH': r / 'devsh', 'MU300_ANDROID_SH': r / 'android-sh',
                'MU300_ANDROID_PATH': os.environ['PATH'], 'MU300_ANDROID_ROOT': r / 'android',
                'MU300_FW_DIRS': f'{r}/android/vendor/firmware', 'MAGISKBIN': r / 'magisk',
                'MAGISKBOOT': magiskboot, 'MU300_UBB': r / 'ubb', 'MU300_SKIP_ROOT_CHECK': 1,
                'MU300_BOOT_ID': r / 'boot_id',
                # as under Magisk: what the installer starts on Android's side must not inherit it
                'ASH_STANDALONE': 1}
