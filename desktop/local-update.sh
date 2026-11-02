#!/bin/sh
# Desktop updates use the same lock, config migration and swap as mu300-update.
set -eu
payload=${1:?upload directory}
case "$payload" in /mnt/mu300-disk/.mu300-desktop-*) ;; *) exit 1 ;; esac
[ "$(dirname "$payload")" = /mnt/mu300-disk ]
token=${payload##*/}; token=${token#.mu300-desktop-}
case "$token" in ''|*[!0-9a-f]*) exit 1 ;; esac
[ "${#token}" = 12 ]
[ ! -L "$payload" ] && [ -d "$payload" ] && [ "$(id -u)" = 0 ]
# Minimal OpenWrt lacks tools such as stat/blockdev. The installer uploads and
# checksums its complete static BusyBox before executing this script. Supply
# only missing commands in this private staging directory; existing tools and
# the running system's PATH configuration remain untouched.
BB=$payload/busybox
[ ! -L "$BB" ] && [ -x "$BB" ]
mkdir "$payload/compat-bin"
for tool in $("$BB" --list); do
    command -v "$tool" >/dev/null 2>&1 || ln -s "$BB" "$payload/compat-bin/$tool"
done
export PATH="$payload/compat-bin:$PATH"
for tool in stat blockdev dd cmp sha256sum mount umount; do
    command -v "$tool" >/dev/null 2>&1 || { echo "missing update command: $tool"; exit 1; }
done
linux=$(cat /run/mu300/linux-slot)
case "$linux" in a|b) ;; *) exit 1 ;; esac
misc=$(cat /run/mu300/misc-dev)
[ -b "$misc" ] && [ "$(stat -c %s "$payload/misc-linux.bin")" = 32 ]
boot=$payload/boot-desktop.img
[ "$(stat -c %s "$boot")" = 67108864 ]
native=0
if [ "$(cat /etc/mu300/platform 2>/dev/null)" = u30pro ]; then
    native=1
    [ -f "$payload/native-boot.tsv" ] && [ ! -L "$payload/native-boot.tsv" ]
    [ "$(wc -l < "$payload/native-boot.tsv")" = 3 ]
    read -r native_tag native_slot < "$payload/native-boot.tsv"
    [ "$native_tag" = U30PRO-BOOT-V1 ] && [ "$native_slot" = "$linux" ]
else
    [ ! -e "$payload/native-boot.tsv" ]
fi
read -r need inodes < "$payload/rootfs.requirements"
native_kib=0
if [ "$native" = 1 ]; then
    native_kib=$(awk 'NR>1 {if ($2 !~ /^[0-9]+$/ || $2<4096 || $2>268435456) exit 1; n+=$2} END {printf "%.0f", (n+1023)/1024}' "$payload/native-boot.tsv")
fi
free=$(df -Pk /mnt/mu300-disk | awk 'END {print $4}')
[ "$free" -gt "$((need + 196608 + native_kib))" ] || { echo 'not enough free space to retain the old system'; exit 1; }
# A plain bind does not replicate live vendor /dev or /proc mounts.
view=$payload/plain-disk
mkdir "$view"
mount --bind /mnt/mu300-disk "$view"
trap 'umount "$view" 2>/dev/null || :' EXIT
export MU300_LIB=1 MU300_DISK=$view
. "$payload/mu300-update-library.sh"
unset MU300_LIB
mounted_disk
lock_take
trap 'lock_drop; umount "$view" 2>/dev/null || :' EXIT
dev=$(part_dev "boot_$linux")
[ -b "$dev" ] && [ "$(blockdev --getsize64 "$dev")" = 67108864 ]
backup=$DISK/boot/desktop-backup-$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$backup"

native_row() {
    read -r nv_name nv_size nv_before nv_after nv_extra <<EOF
$(sed -n "${1}p" "$payload/native-boot.tsv")
EOF
    [ -z "$nv_extra" ]
    case "$1:$nv_name" in 2:vendor_boot|3:init_boot) ;; *) return 1 ;; esac
    case "$nv_size" in ''|*[!0-9]*) return 1 ;; esac
    case "$nv_before$nv_after" in *[!0-9a-f]*) return 1 ;; esac
    [ "${#nv_before}" = 64 ] && [ "${#nv_after}" = 64 ]
    [ "$nv_size" -ge 4096 ] && [ "$nv_size" -le 268435456 ]
    nv_dev=$(part_dev "${nv_name}_$linux")
    [ -b "$nv_dev" ] && [ "$(blockdev --getsize64 "$nv_dev")" = "$nv_size" ]
}
if [ "$native" = 1 ]; then
    for nv_index in 2 3; do
        native_row "$nv_index"
        [ "$(stat -c %s "$payload/native-$nv_name.img")" = "$nv_size" ]
        [ "$(sha256sum "$payload/native-$nv_name.img" | cut -d ' ' -f1)" = "$nv_after" ]
        [ "$(sha256sum "$nv_dev" | cut -d ' ' -f1)" = "$nv_before" ]
        # Backups and uploads stay on the persistent system disk, never tmpfs.
        dd if="$nv_dev" of="$backup/$nv_name.img" bs=1048576
        [ "$(sha256sum "$backup/$nv_name.img" | cut -d ' ' -f1)" = "$nv_before" ]
    done
fi

native_write() {
    [ "$native" = 1 ] || return 0
    for nv_index in 2 3; do
        native_row "$nv_index"
        nv_actual=$(sha256sum "$nv_dev" | cut -d ' ' -f1)
        [ "$nv_actual" = "$nv_before" ]
        [ "$nv_before" != "$nv_after" ] || continue
        dd if="$payload/native-$nv_name.img" of="$nv_dev" bs=1048576 conv=fsync
        [ "$(sha256sum "$nv_dev" | cut -d ' ' -f1)" = "$nv_after" ]
    done
}
native_restore() {
    [ "$native" = 1 ] || return 0
    nv_failed=0
    for nv_index in 2 3; do
        native_row "$nv_index" || return 1
        [ "$(sha256sum "$backup/$nv_name.img" | cut -d ' ' -f1)" = "$nv_before" ] || return 1
        dd if="$backup/$nv_name.img" of="$nv_dev" bs=1048576 conv=fsync || nv_failed=1
        [ "$(sha256sum "$nv_dev" | cut -d ' ' -f1)" = "$nv_before" ] || nv_failed=1
    done
    [ "$nv_failed" = 0 ]
}
dd if="$dev" of="$backup/boot.img" bs=1048576 count=64
dd if="$misc" of="$backup/misc.bin" bs=1 skip=2048 count=32
[ "$(stat -c %s "$backup/boot.img")" = 67108864 ] && [ "$(stat -c %s "$backup/misc.bin")" = 32 ]
swapped=0 boot_changed=0 complete=0
finish() {
    rc=$?
    if [ "$complete" != 1 ]; then
        if [ "$boot_changed" = 1 ]; then
            native_restore || echo 'NATIVE BOOT RESTORE FAILED; do not reboot'
            dd if="$backup/boot.img" of="$dev" bs=1048576 conv=fsync || echo 'BOOT RESTORE FAILED'
            dd if="$dev" of="$payload/restored-boot.img" bs=1048576 count=64
            cmp -s "$backup/boot.img" "$payload/restored-boot.img" || echo 'BOOT RESTORE READBACK FAILED'
            dd if="$backup/misc.bin" of="$misc" bs=1 seek=2048 conv=notrunc,fsync || echo 'MISC RESTORE FAILED'
        fi
        if [ "$swapped" = 1 ]; then
            mv "$DISK/openwrt" "$DISK/openwrt.failed.$$" && mv "$DISK/openwrt.old" "$DISK/openwrt" || echo 'ROOT RESTORE FAILED'
        fi
    fi
    lock_drop
    # Never delete a directory that still holds the plain bind view: that
    # would descend into the entire Linux disk. Failed runs retain their logs.
    if umount "$view"; then
        [ "$complete" != 1 ] || rm -rf "$payload"
    else
        echo 'plain update view still mounted; uploaded files retained'
    fi
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT TERM HUP
STAGE=$payload
if ! apply_one openwrt desktop-local; then exit 1; fi
swapped=1
(cd "$DISK/openwrt" && sha256sum -c "$payload/rootfs-critical.sha256")
if [ "$(cat "$DISK/openwrt/etc/mu300/platform" 2>/dev/null)" = u30pro ]; then
    [ -s "$DISK/openwrt/opt/mu300/vendor/u30pro/modem/map.tsv" ] &&
        [ -s "$DISK/openwrt/opt/mu300/vendor/u30pro/modem/modem_cp_info.xml" ] &&
        [ -x "$DISK/openwrt/usr/sbin/u30-modemd" ]
else
    [ -s "$DISK/openwrt/lib/firmware/wcnmodem.bin" ] && [ -x "$DISK/openwrt/opt/mu300/android/vendor/bin/modem_control" ]
fi
# Preserve the persistent log at 48 MiB and update the AVB footer separately.
head -c 50331648 "$boot" > "$payload/boot-head"
tail -c 64 "$boot" > "$payload/boot-footer"
boot_changed=1
native_write
dd if="$payload/boot-head" of="$dev" bs=1048576 conv=fsync
dd if="$payload/boot-footer" of="$dev" bs=1 seek=67108800 conv=notrunc,fsync
dd if="$dev" of="$payload/boot-readback" bs=1048576 count=48
dd if="$dev" of="$payload/footer-readback" bs=1 skip=67108800 count=64
cmp -s "$payload/boot-head" "$payload/boot-readback" && cmp -s "$payload/boot-footer" "$payload/footer-readback"
dd if="$payload/misc-linux.bin" of="$misc" bs=1 seek=2048 conv=notrunc,fsync
dd if="$misc" of="$payload/misc-check" bs=1 skip=2048 count=32
cmp -s "$payload/misc-check" "$payload/misc-linux.bin"
echo linux > "$DISK/openwrt/etc/mu300/default-boot"
echo 0 > "$DISK/.mu300/boot-tries"
echo openwrt > "$DISK/.mu300/boot-os"
sync
complete=1
echo 'UPDATE-COMPLETE; old system: openwrt.old; rebooting'
(sleep 3; reboot) </dev/null >/dev/null 2>&1 &
