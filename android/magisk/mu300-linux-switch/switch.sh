#!/system/bin/sh
# Boot the Linux system from the slot Android is not running from (b, or a when Android runs from b).
#
#   mu300-linux            arm the one-shot trial and reboot
#   mu300-linux status     show what is on each slot, change nothing
#   mu300-linux --dry-run  do everything except writing and rebooting
#   mu300-linux bootchain  whether the boot chain (uboot, sml, teecfg, trustos, vbmeta) differs between the slots
#   mu300-linux bootchain sync   copy Android's slot's chain to the Linux slot, backed up first (bootchain.sh)
#
# The switch writes only the 32-byte AOSP bootloader_control block at offset 0x800 of the misc partition:
# the Linux slot gets the highest priority with tries_remaining = 2, Android's stays bootable and marked successful.
# LK then boots Linux once. Linux writes Android's block back as one of its first steps, so the reboot after that
# returns to Android, and a Linux that does not boot falls back to Android by itself.
#
# Android's boot partition, the GPT, userdata and every other partition are never touched. (bootchain sync writes
# the Linux slot's uboot, sml, teecfg, trustos and vbmeta, and nothing else: see bootchain.sh.)
BC_OFFSET=2048          # 0x800
MAGIC=42434142          # "BCAB", little endian in the struct
ANDROID_ARMED=158       # 0x9e: priority 14, tries 1, successful 1
LINUX_ARMED=47          # 0x2f: priority 15, tries 2, successful 0

DRY=0
case ${1:-} in
    status) ACTION=status ;;
    --dry-run|-n) DRY=1; ACTION=switch ;;
    ''|switch) ACTION=switch ;;
    bootchain)
        case ${2:-check} in
            check|status) exec sh "${0%/*}/bootchain.sh" check ;;
            sync) exec sh "${0%/*}/bootchain.sh" sync ${3:+"$3"} ;;
            *) echo "usage: mu300-linux bootchain [sync]" >&2; exit 2 ;;
        esac ;;
    -h|--help|help) sed -n '2,16s/^# \{0,1\}//p' "$0"; exit 0 ;;
    *) echo "usage: mu300-linux [status|--dry-run|bootchain [sync]]" >&2; exit 2 ;;
esac

die() { echo "mu300-linux: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (su -c mu300-linux)"

# busybox: Magisk's own copy is the one that is always there, and it has the awk this script needs
for b in ${MU300_BUSYBOX:+"$MU300_BUSYBOX"} "$MAGISKTMP/busybox" /data/adb/magisk/busybox /data/adb/busybox; do
    [ -x "$b" ] && { BB=$b; break; }
done
[ -n "${BB:-}" ] || die "Magisk's busybox was not found"
AWK="$BB awk"

by_name=${MU300_BY_NAME:-/dev/block/by-name}
[ -e "$by_name/misc" ] || by_name=$(dirname "$(ls -d /dev/block/platform/*/by-name/misc 2>/dev/null | head -n1)" 2>/dev/null)
MISC=$by_name/misc
BOOT_A=$by_name/boot_a
BOOT_B=$by_name/boot_b
[ -e "$MISC" ] || die "no misc partition at $MISC"
# misc is resolved once, to the block device itself, and that path is the one read, written and read back: the
# by-name link is followed here and never again. It has to be a block device under /dev/block whose kernel name
# says PARTNAME=misc (sysfs, which no other user writes). MU300_SYSFS (tests only) stands for /sys and lets a
# regular file stand for the device.
SYSFS=${MU300_SYSFS:-/sys}
misc_dev=$($BB readlink -f "$MISC") || die "cannot resolve $MISC"
if [ -n "${MU300_SYSFS:-}" ]; then
    [ -f "$misc_dev" ] && [ ! -L "$misc_dev" ] || die "misc ($misc_dev) is not a device"
else
    case $misc_dev in /dev/block/*) ;; *) die "misc resolves outside /dev/block ($misc_dev)" ;; esac
    [ -b "$misc_dev" ] || die "misc ($misc_dev) is not a block device"
fi
$BB grep -qx 'PARTNAME=misc' "$SYSFS/class/block/${misc_dev##*/}/uevent" 2>/dev/null ||
    die "$misc_dev is not the misc partition (sysfs PARTNAME)"
MISC=$misc_dev
[ -e "$BOOT_A" ] && [ -e "$BOOT_B" ] || die "both boot_a and boot_b are required at $by_name"

hex_of() {  # hex_of FILE OFFSET COUNT -> lowercase hex, no spaces (only ever used for a few bytes)
    dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | od -An -tx1 -v | tr -d ' \n'
}
head_md5() {  # head_md5 FILE -> md5 of the first 64 KiB, read in whole blocks (od on this device is very slow)
    dd if="$1" bs=4096 count=16 2>/dev/null | $BB md5sum | cut -d' ' -f1
}

live=$(hex_of "$MISC" "$BC_OFFSET" 32)
[ ${#live} -eq 64 ] || die "could not read the bootloader_control block"
[ "$(echo "$live" | cut -c9-16)" = "$MAGIC" ] || die "misc does not hold an AOSP bootloader_control block ($live)"

slot=${MU300_SLOT_SUFFIX:-$(getprop ro.boot.slot_suffix)}
linux_slot=
case $slot in
    _a) android_boot=$BOOT_A; linux_boot=$BOOT_B; linux_slot=b; a_arm=$ANDROID_ARMED; b_arm=$LINUX_ARMED ;;
    _b) android_boot=$BOOT_B; linux_boot=$BOOT_A; linux_slot=a; a_arm=$LINUX_ARMED; b_arm=$ANDROID_ARMED ;;
esac
a_meta=$(echo "$live" | cut -c25-26)
b_meta=$(echo "$live" | cut -c29-30)

# what is on the Linux slot. Every boot image starts with "ANDROID!", ours too, and after an OTA the other slot holds
# the previous Android - not a copy of this one, and not Linux either. Ours carry loglevel=5 as the header's command
# line (offset 44; boot/build-boot-image.py, and mu300-update keeps the header), Android's boot images do not.
if [ -n "$linux_slot" ]; then
    head_target=$(hex_of "$linux_boot" 0 8)
    cmdline_target=$(hex_of "$linux_boot" 44 11)
    same=no
    [ "$(head_md5 "$android_boot")" = "$(head_md5 "$linux_boot")" ] && same=yes
fi
LINUX_CMDLINE=6c6f676c6576656c3d3500    # "loglevel=5\0"

if [ "$ACTION" = status ]; then
    echo "running slot   ${slot:-unknown}"
    echo "slot a         priority $((0x$a_meta & 15)), tries $(( (0x$a_meta >> 4) & 7 )), successful $(( (0x$a_meta >> 7) & 1 ))"
    echo "slot b         priority $((0x$b_meta & 15)), tries $(( (0x$b_meta >> 4) & 7 )), successful $(( (0x$b_meta >> 7) & 1 ))"
    if [ -z "$linux_slot" ]; then
        echo "Linux slot     unknown (Android's slot is not _a or _b)"
    elif [ "$head_target" != "414e44524f494421" ]; then
        echo "boot_$linux_slot         does not start with ANDROID! - not a boot image"
    elif [ "$same" = yes ]; then
        echo "boot_$linux_slot         a copy of Android's boot image (no Linux installed)"
    elif [ "$cmdline_target" = "$LINUX_CMDLINE" ]; then
        echo "boot_$linux_slot         the Linux boot image"
    else
        echo "boot_$linux_slot         a boot image that is not Linux (the Android from before an update?)"
    fi
    exit 0
fi

[ -n "$linux_slot" ] || die "Android boot slot '$slot' is unknown"
[ "$head_target" = "414e44524f494421" ] || die "boot_$linux_slot is not a boot image - install Linux first"
[ "$same" = no ] || die "boot_$linux_slot is the same image as Android's boot partition: no Linux is installed"
[ "$cmdline_target" = "$LINUX_CMDLINE" ] ||
    die "no Linux on boot_$linux_slot: it holds another boot image (the Android from before an update?)"

# the new block: same as the live one, but with the slot suffix and the two metadata bytes of an armed trial,
# and a fresh CRC32 (zlib, over the first 28 bytes, stored little endian at offset 28)
new=$($AWK -v live="$live" -v am="$a_arm" -v bm="$b_arm" -v suffix="_$linux_slot" '
function xor32(a, b,   i, r, p, x, y) {
    r = 0; p = 1
    for (i = 0; i < 32; i++) {
        x = a % 2; y = b % 2; a = int(a / 2); b = int(b / 2)
        if (x != y) r += p
        p *= 2
    }
    return r
}
function h2i(s,   i, v) {   # busybox awk has no strtonum
    v = 0
    for (i = 1; i <= length(s); i++) v = v * 16 + index("0123456789abcdef", tolower(substr(s, i, 1))) - 1
    return v
}
function crc32(bytes, n,   c, i, k) {
    c = 4294967295
    for (i = 1; i <= n; i++) {
        c = xor32(c, bytes[i])
        for (k = 0; k < 8; k++) c = (c % 2) ? xor32(int(c / 2), 3988292384) : int(c / 2)
    }
    return xor32(c, 4294967295)
}
BEGIN {
    for (i = 0; i < 32; i++) b[i + 1] = h2i(substr(live, i * 2 + 1, 2))
    b[2] = (suffix == "_a" ? 97 : 98)   # "_a" or "_b"
    b[13] = am           # slot a metadata
    b[15] = bm           # slot b metadata
    c = crc32(b, 28)
    for (i = 0; i < 4; i++) { b[29 + i] = c % 256; c = int(c / 256) }
    out = ""
    for (i = 1; i <= 32; i++) out = out sprintf("%02x", b[i])
    print out
}')
[ ${#new} -eq 64 ] || die "could not build the new bootloader_control block (awk gave '$new')"

echo "misc  now: $live"
echo "misc  new: $new"
if [ "$DRY" = 1 ]; then
    echo "dry run: nothing was written"
    exit 0
fi

# No file on the way: the block goes from printf straight into dd. It used to be written to a fixed name in
# ${TMPDIR:-/data/local/tmp}, the adb shell user's directory (0771, no sticky bit), where that user could plant a
# symlink (root's write follows it) or swap the file - or a whole directory made there - between the size check
# and dd, and so choose what goes into misc. The 64 hex digits are checked, the 32 bytes counted, dd copies 32.
case $new in *[!0-9a-f]*) die "the new block is not hex ($new)" ;; esac
esc=$(echo "$new" | sed 's/../\\x&/g')
[ "$($BB printf "$esc" | $BB wc -c | tr -d ' ')" = 32 ] || die "the new block is not 32 bytes"
$BB printf "$esc" | dd of="$MISC" bs=1 count=32 seek="$BC_OFFSET" conv=notrunc 2>/dev/null ||
    die "writing misc failed"
sync

back=$(hex_of "$MISC" "$BC_OFFSET" 32)
[ "$back" = "$new" ] || die "misc verify failed ($back) - reboot normally, Android is unaffected"

echo
echo "slot $linux_slot armed for one boot. Rebooting into Linux."
echo "If it does not boot, the device returns to Android by itself."
sleep 3
reboot
