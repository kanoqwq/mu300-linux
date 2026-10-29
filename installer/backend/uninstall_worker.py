"""Embedded BusyBox worker; imported as Python data by frozen backends."""
from .uninstall_growth import GROWTH

WORKER = r'''#!/bin/busybox sh
set -eu
set -o pipefail

snapshot_read() {
    dd if="$disk" of=check-first bs=512 count="$front" 2>/dev/null
    dd if="$disk" of=check-last bs=512 skip="$tail" count="$((total-tail))" 2>/dev/null
}
snapshot_known() {
    { cmp -s check-first before-first || cmp -s check-first after-first; } &&
    { cmp -s check-last before-last || cmp -s check-last after-last; }
}
snapshot_commit() {
    snapshot_read
    snapshot_known || { echo 'Unknown GPT; stopped without writing'; return 1; }
    # Flush and verify the backup copy before touching the primary copy.
    dd if=after-last of="$disk" bs=512 seek="$tail" conv=notrunc,fsync
    dd if="$disk" of=check-last bs=512 skip="$tail" count="$((total-tail))" 2>/dev/null
    cmp -s check-last after-last
    dd if=after-first of="$disk" bs=512 conv=notrunc,fsync
    snapshot_read
    cmp -s check-first after-first && cmp -s check-last after-last
}
idle_partition() {
    node=$1
    [ ! -d "/sys/class/block/$node" ] && return 0
    [ -z "$(ls -A "/sys/class/block/$node/holders")" ] || return 1
    number=$(cat "/sys/class/block/$node/dev")
    ! awk -v n="$number" '$3==n {found=1} END {exit !found}' /proc/self/mountinfo
}

# BEGIN DEVICE WORKER
. /openwrt-uninstall/storage.sh
mkdir -p /run/openwrt-uninstall
exec >>/run/openwrt-uninstall/debug.log 2>&1
fail() {
    rc=$?
    echo "FAILED: offline uninstall stopped (exit $rc). Keep power and computer backups."
    echo FAILED > /run/openwrt-uninstall/progress.txt
    while :; do sleep 10; done
}
trap fail EXIT HUP INT TERM
touch /run/stay
httpd -p 8089 -h /run/openwrt-uninstall || true
cd /openwrt-uninstall
sha256sum -c SHA256SUMS
IFS=: read -r cid disk_node data_node root_node misc_node android_node linux_node total front tail start root_size wipe boot_bytes boot_sha action data_start data_size fs_before fs_uuid extra < request
[ -z "$extra" ] && [ "${#cid}" = 32 ] && [ "${#boot_sha}" = 64 ]
case "$cid$boot_sha" in *[!0-9a-f]*) exit 2 ;; esac
case "$disk_node" in mmcblk[0-9]*|sd[a-z]*) ;; *) exit 2 ;; esac
for node in "$data_node" "$root_node" "$misc_node" "$android_node" "$linux_node"; do
    case "$node" in "$disk_node"p[0-9]*|"$disk_node"[0-9]*) ;; *) exit 2 ;; esac
    case "$node" in *[!a-z0-9]*) exit 2 ;; esac
done
for n in "$total" "$front" "$tail" "$start" "$root_size" "$boot_bytes"; do
    case "$n" in ''|0*|*[!0-9]*) exit 2 ;; esac
done
case "$wipe" in 0|1) ;; *) exit 2 ;; esac
case "$action" in
    '') [ "$wipe" = 1 ] && action=reset || action=gap ;;
    reset) [ "$wipe" = 1 ] ;;
    gap) [ "$wipe" = 0 ] ;;
    grow-ext4|grow-f2fs|grow-android-f2fs)
        [ "$wipe" = 0 ]
        for n in "$data_start" "$data_size" "$fs_before"; do case "$n" in ''|0*|*[!0-9]*) exit 2 ;; esac; done
        [ "${#fs_uuid}" = 32 ]; case "$fs_uuid" in *[!0-9a-f]*) exit 2 ;; esac
        [ "$data_start" -ge "$front" ] && [ "$((data_start+data_size))" -le "$tail" ]
        [ "$data_start" -lt "$start" ] && [ "$((data_start+data_size))" -ge "$((start+root_size))" ] ;;
    *) exit 2 ;;
esac
disk=/dev/$disk_node
misc=/dev/$misc_node
android=/dev/$android_node
linux=/dev/$linux_node
[ "$android_node" != "$linux_node" ]
[ "$(mu300_storage_identity "$disk_node")" = "$cid" ]
[ "$(cat /sys/class/block/$disk_node/size)" = "$total" ]
[ "$front" -ge 3 ] && [ "$front" -le 4096 ] && [ "$tail" -gt "$((total-4096))" ] && [ "$tail" -lt "$total" ]
[ "$start" -ge "$front" ] && [ "$((start%8))" = 0 ] && [ "$root_size" -ge 4194304 ] && [ "$((start+root_size))" -le "$tail" ]
grep -qx PARTNAME=userdata "/sys/class/block/$data_node/uevent"
grep -qx PARTNAME=misc "/sys/class/block/$misc_node/uevent"
idle_partition "$data_node"
idle_partition "$root_node"
idle_partition "$android_node"
idle_partition "$linux_node"
for loop in /sys/class/block/loop*/loop/backing_file; do
    [ -r "$loop" ] || continue
    backing=$(cat "$loop")
    case "$backing" in /dev/$disk_node|/dev/block/$disk_node|/dev/$data_node|/dev/block/$data_node|/dev/$root_node|/dev/block/$root_node) exit 1 ;; esac
done
[ "$(( $(cat /sys/class/block/$android_node/size)*512 ))" = "$boot_bytes" ]
[ "$(( $(cat /sys/class/block/$linux_node/size)*512 ))" = "$boot_bytes" ]
[ "$(sha256sum "$android" | cut -d' ' -f1)" = "$boot_sha" ]
[ "$(wc -c < android-bc)" = 32 ] && [ "$(wc -c < recovery-bcb)" = 2048 ]
for half in before after; do
    [ "$(wc -c < "$half-first")" = "$((front*512))" ]
    [ "$(wc -c < "$half-last")" = "$(((total-tail)*512))" ]
done
snapshot_read
snapshot_known
case "$action" in grow-*)
    # Both routes check Android's recovery request and userdata boundary before
    # any GPT write. Encrypted data must not reach the raw filesystem helpers.
    dd if="$misc" of=current-bcb bs=2048 count=1 2>/dev/null
    cmp -s current-bcb before-bcb
    [ "$(cat /sys/class/block/$data_node/start)" = "$data_start" ]
    if [ "$action" != grow-android-f2fs ]; then
    fs_measure /dev/$data_node
    if [ "$action" = grow-f2fs ]; then
        fs_tool /openwrt-uninstall/tools/sbin/fsck.f2fs -V
        fs_tool /openwrt-uninstall/tools/sbin/resize.f2fs -V
    else
        fs_tool /openwrt-uninstall/tools/usr/sbin/e2fsck -V
        fs_tool /openwrt-uninstall/tools/usr/sbin/resize2fs -V
    fi
    fs_check /dev/$data_node
    fs_measure /dev/$data_node
    fi ;;
esac
finished=0
if cmp -s check-first after-first && cmp -s check-last after-last; then finished=1; fi
if [ "$finished" = 0 ]; then
    echo '1/3 validating offline removal' > /run/openwrt-uninstall/progress.txt
    if [ "$wipe" = 1 ]; then
        # Recovery owns formatting and encryption. Arm its request before GPT
        # changes, but never re-arm it once both restored GPT copies exist.
        dd if="$misc" of=current-bcb bs=2048 count=1 2>/dev/null
        cmp -s current-bcb before-bcb || cmp -s current-bcb recovery-bcb
        dd if=recovery-bcb of="$misc" bs=2048 count=1 conv=notrunc,fsync
        dd if="$misc" of=current-bcb bs=2048 count=1 2>/dev/null
        cmp -s current-bcb recovery-bcb
    elif [ "$action" = gap ]; then
        # Only the proven OpenWrt range is invalidated; factory gaps stay gaps.
        dd if=/dev/zero of="$disk" bs=4096 seek="$((start/8))" count=1 conv=notrunc,fsync
    fi
    echo '2/3 restoring the verified partition layout' > /run/openwrt-uninstall/progress.txt
    snapshot_commit
fi
case "$action" in grow-ext4|grow-f2fs)
    echo 'Growing and verifying Android userdata without formatting' > /run/openwrt-uninstall/progress.txt
    # The packaged BusyBox supports BLKRRPART, not losetup --sizelimit.
    # Require the exact restored partition; never expose the remaining disk.
    blockdev --rereadpt "$disk"
    [ "$(cat /sys/class/block/$data_node/start)" = "$data_start" ]
    [ "$(cat /sys/class/block/$data_node/size)" = "$data_size" ]
    for node in "$data_node" "$misc_node" "$android_node" "$linux_node"; do
        kernel_node_matches "$node"
    done
    idle_partition "$data_node"
    grow_device=/dev/$data_node
    fs_grow ;;
    grow-android-f2fs)
    # Metadata encryption keys stay in Android. The raw userdata bytes are
    # ciphertext: no fsck, resize, mount, key export or wipe is allowed here.
    echo 'Returning to Android for stock decrypted F2FS growth; preserving data' > /run/openwrt-uninstall/progress.txt
    blockdev --rereadpt "$disk"
    [ "$(cat /sys/class/block/$data_node/start)" = "$data_start" ]
    [ "$(cat /sys/class/block/$data_node/size)" = "$data_size" ]
    kernel_node_matches "$data_node" ;;
esac
echo '3/3 restoring Android boot selection' > /run/openwrt-uninstall/progress.txt
# The running kernel/ramdisk is in RAM. The current Android boot is never written.
[ "$(sha256sum "$android" | cut -d' ' -f1)" = "$boot_sha" ]
dd if="$android" of="$linux" bs=1048576 conv=fsync
[ "$(sha256sum "$linux" | cut -d' ' -f1)" = "$boot_sha" ]
dd if=android-bc of="$misc" bs=1 seek=2048 count=32 conv=notrunc,fsync
dd if="$misc" of=current-bc bs=1 skip=2048 count=32 2>/dev/null
cmp -s current-bc android-bc
echo 'Offline uninstall verified; restarting Android' > /run/openwrt-uninstall/progress.txt
sync
trap - EXIT HUP INT TERM
reboot -f
while :; do sleep 10; done
'''

WORKER = WORKER.replace('# BEGIN DEVICE WORKER', GROWTH+'\n# BEGIN DEVICE WORKER',1)
