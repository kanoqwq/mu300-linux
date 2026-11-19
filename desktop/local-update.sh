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
read -r need inodes < "$payload/rootfs.requirements"
free=$(df -Pk /mnt/mu300-disk | awk 'END {print $4}')
[ "$free" -gt "$((need + 196608))" ] || { echo 'not enough free space to retain the old system'; exit 1; }
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
# Older installs or manual cleanup may leave the shared boot-state directory absent.
mkdir -p "$DISK/.mu300"
dev=$(part_dev "boot_$linux")
[ -b "$dev" ] && [ "$(blockdev --getsize64 "$dev")" = 67108864 ]
backup=$DISK/boot/desktop-backup-$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$backup"
dd if="$dev" of="$backup/boot.img" bs=1048576 count=64
dd if="$misc" of="$backup/misc.bin" bs=1 skip=2048 count=32
[ "$(stat -c %s "$backup/boot.img")" = 67108864 ] && [ "$(stat -c %s "$backup/misc.bin")" = 32 ]
swapped=0 boot_changed=0 complete=0
finish() {
    rc=$?
    if [ "$complete" != 1 ]; then
        if [ "$boot_changed" = 1 ]; then
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
[ -s "$DISK/openwrt/lib/firmware/wcnmodem.bin" ] && [ -x "$DISK/openwrt/opt/mu300/android/vendor/bin/modem_control" ]
# Preserve the persistent log at 48 MiB and update the AVB footer separately.
head -c 50331648 "$boot" > "$payload/boot-head"
tail -c 64 "$boot" > "$payload/boot-footer"
boot_changed=1
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
