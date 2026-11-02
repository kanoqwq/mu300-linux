#!/bin/sh
# Runs under bundled BusyBox ash in Android init's mount namespace, not in
# Magisk's sourced customize.sh shell. Only successful stages permit the next.
set -eu
P=${MU300_PAYLOAD_DIR:?}; T=${MU300_INSTALL_TMP:?}; BB=${MU300_BUSYBOX:?}
# A private applet directory guarantees consistent stat/df/dd/sha256 behavior
# even when Android toybox or Magisk's smaller BusyBox shadows PATH. mke2fs
# remains an absolute Android e2fsprogs path.
if [ -z "${MU300_TF_BIN:-}" ]; then
    MU300_TF_BIN=$(mktemp -d "$T/bin.XXXXXX")
    "$BB" --install -s "$MU300_TF_BIN"
    export MU300_TF_BIN
fi
export PATH="$MU300_TF_BIN:$PATH"
. "$P/tf-storage.sh"
say_i18n() { if [ "${MU300_INSTALL_LANG:-en}" = zh ]; then echo "$1"; else echo "$2"; fi; }
case ${1:-} in
    --preflight)
        . "$P/mu300-install.env"
        cd "$P"
        "$BB" sha256sum -c payload.sha256
        [ -s boot-linux.sha256 ]
        [ "$(cat boot-linux.sha256)" = "$(sha256sum boot-linux.img | cut -d' ' -f1)" ]
        [ -x /system/bin/mke2fs ] || tf_die 'missing Android /system/bin/mke2fs'
        /system/bin/mke2fs -V
        [ -b "$SD_DEV" ]
        [ -b "$MU300_TF_BOOTDEV" ]
        tf_read_requirements rootfs.requirements || tf_die 'invalid rootfs.requirements: expected positive KiB and inode counts'
        sectors=$(blockdev --getsz "$SD_DEV")
        printf '[TF] capacity: target=%s sectors; required=%s KiB, %s inodes\n' "$sectors" "$required_kib" "$required_inodes"
        tf_capacity "$sectors" "$required_kib" "$required_inodes" || tf_die "TF capacity check failed: $tf_capacity_error"
        boot_sectors=$(blockdev --getsz "$MU300_TF_BOOTDEV")
        boot_bytes=$(stat -c %s boot-linux.img)
        [ "$boot_bytes" -gt 0 ] && [ $(( (boot_bytes + 4095) / 4096 * 8 )) -le "$boot_sectors" ] || tf_die 'boot image/aligned readback exceeds partition'
        [ -b /dev/block/by-name/misc ] && [ "$(blockdev --getsz /dev/block/by-name/misc)" -ge 8 ] || tf_die 'invalid misc partition'
        # Verify OTA slot compatibility before formatting or writing anything.
        if [ "${SYNC_BOOTCHAIN:-0}" = 1 ]; then
            "$BB" sh "$P/android-bootchain.sh" check || tf_die 'boot chain preflight refused'
        fi
        # Staging vendor files avoids discovering absent firmware AFTER format.
        free_kib=$(df -Pk "$T" | awk 'END {print $4}')
        [ "$free_kib" -ge 131072 ] || tf_die 'need 128 MiB free Android staging space'
        exit 0 ;;
    --write-boot)
        # fdatasync flushes this partition, not every Android filesystem.
        exec "$BB" dd if="$P/boot-linux.img" of="$MU300_TF_BOOTDEV" bs=4M conv=fsync ;;
    --staging-space)
        # Vendor staging has now completed. Its measured size/count must fit
        # the reserved budget; leave room for a full boot readback in /data.
        vendor_kib=$(du -sk "$MU300_PREPARED_VENDOR" | awk '{print $1}')
        vendor_inodes=$(find "$MU300_PREPARED_VENDOR" | wc -l)
        [ "$vendor_kib" -le 196608 ] && [ "$vendor_inodes" -le 8192 ] || tf_die 'vendor staging exceeds reserved space/inode budget'
        free_kib=$(df -Pk "$T" | awk 'END {print $4}')
        boot_bytes=$(stat -c %s "$P/boot-linux.img")
        [ "$free_kib" -ge $(( (boot_bytes + 1023) / 1024 + 32768 )) ] || tf_die 'not enough Android space for boot readback'
        (cd "$MU300_PREPARED_VENDOR" && find . -type f -exec sha256sum {} +) > "$MU300_TF_STATE/vendor.sha256"
        exit 0 ;;
    --verify-boot)
        bytes=$(stat -c %s "$P/boot-linux.img")
        # Read bounded aligned blocks to a file; no pipeline hides an I/O error.
        dd if="$MU300_TF_BOOTDEV" of="$T/boot-readback" bs=4096 count=$(( (bytes + 4095) / 4096 ))
        [ "$(stat -c %s "$T/boot-readback")" -ge "$bytes" ]
        truncate -s "$bytes" "$T/boot-readback"
        [ "$(sha256sum "$T/boot-readback" | cut -d' ' -f1)" = "$(cat "$P/boot-linux.sha256")" ]
        rm -f "$T/boot-readback"
        exit 0 ;;
esac

# A failed install remains locked until Android reboots. This also prevents
# retries accumulating unkillable requests after a storage-driver failure.
boot_id=$(cat /proc/sys/kernel/random/boot_id)
lock=$T/lock-$boot_id
mkdir "$lock" 2>/dev/null || {
    tf_die 'installer already ran/is running in this boot; reboot Android before retrying'; exit 1;
}
MU300_TF_STATE=$(mktemp -d "$T/log.XXXXXX")
export MU300_TF_STATE
say_i18n "TF 安装日志：$MU300_TF_STATE" "TF installation logs: $MU300_TF_STATE"
tf_step preflight 180 "$BB" sh "$0" --preflight
tf_step vendor-preflight 180 "$BB" sh "$P/mu300-vendor-from-device.sh" "$MU300_TF_STATE/vendor" openwrt
MU300_PREPARED_VENDOR=$MU300_TF_STATE/vendor
export MU300_PREPARED_VENDOR
tf_step staging-space 30 "$BB" sh "$0" --staging-space
tf_step rootfs 1200 "$BB" sh "$P/android-install.sh"
if [ "${MU300_TF_DEFER_BOOT:-0}" != 1 ]; then
    tf_step boot-write 60 "$BB" sh "$0" --write-boot
    tf_step boot-readback 60 "$BB" sh "$0" --verify-boot
    tf_step arm-slot 60 "$BB" sh "$P/switch.sh" --no-reboot
fi
rmdir "$lock"
# Logs remain in Android /data for diagnosis; only the private vendor staging
# directory is disposable. Avoid broad error-path cleanup or global sync.
rm -rf "$MU300_TF_STATE/vendor"
rm -rf "$MU300_TF_BIN"
