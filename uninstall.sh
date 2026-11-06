#!/bin/sh
# Remove MU300 Linux and return the device to stock Android. Run on a macOS/Linux host with the device booted in
# rooted Android (adb + su), like install.sh.
#
#   ./uninstall.sh
#
# It
#   * makes slot a (Android) the boot slot in misc (Linux is never started again),
#   * copies boot_a to boot_b, so slot b holds a stock Android boot image instead of the Linux one,
#   * erases the Linux filesystem in the unpartitioned eMMC region (secure: overwrite everything and verify;
#     quick: only the filesystem headers, the data stays readable until the space is reused),
#   * removes the installer leftovers in /data/local/tmp.
# boot_a, the GPT, userdata and every other partition stay untouched. Needs adb and python3.
set -eu
T=/data/local/tmp
TOP=$(cd "$(dirname "$0")" && pwd)
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
ask() { printf '%s [%s]: ' "$2" "$3"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\$_a"; }
su_do() { adb shell "su -c '$1'" </dev/null | tr -d '\r'; }
hex32() { su_do "dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v" | tr -d ' \n'; }

say "Checking host tools and device"
for c in adb python3; do command -v $c >/dev/null || die "$c not found"; done
. "$TOP/tools/linux-mode.sh"
require_android
[ "$(su_do 'id -u')" = 0 ] || die "su does not work on the device"
if [ "$(su_do 'for u in /sys/class/block/mmcblk*p*/uevent; do grep -qx PARTNAME=mu300root "$u" && { echo internal-partition; break; }; done; true')" = internal-partition ]; then
    die 'Use OpenWrt-Installer.exe: Advanced options -> Uninstall and return space, with the original installation records. This legacy script cannot remove a GPT mu300root partition safely.'
fi
model="$(su_do 'getprop ro.product.model') / $(su_do 'getprop ro.product.device')"
echo "device: $model"
case "$model" in
    "MU5352 / MU5352") [ "$(su_do 'getprop ro.board.platform')" = ums9620 ] || die "M3 is not the UMS9620 platform" ;;
    *MU5352*) die "M3 requires MU5352 / MU5352 on ums9620" ;;
    *MU300*|*F50*|*mu300*|*U30Air*|*U30_Air*) ;; *) die "this does not look like a ZTE F50/MU300 or U30 Air" ;; esac
[ "$(su_do 'getprop ro.boot.slot_suffix')" = _a ] || die "Android must be running from slot a (boot Android first: mu300-next-boot android)"

say "Looking for the Linux installation"
set -- $(su_do 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; done; echo $e $(cat /sys/block/mmcblk0/size)')
[ $# -eq 2 ] || die "could not read the partition table from the device (is su granted? try again)"
last_end=$1; disk=$2
OFF=; SIZE=
start=$(( (last_end / 4096 + 1) * 4096 * 512 ))
for cand in $start 27762098176; do
    [ "$cand" -ge 0 ] && [ $((cand + 2048)) -le $((disk * 512)) ] || continue
    m=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1080)) count=2 2>/dev/null | od -An -tx1" | tr -d ' ')
    l=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1144)) count=16 2>/dev/null" | tr -d '\000')
    if [ "$m" = 53ef ] && [ "$l" = mu300root ]; then
        blocks=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1028)) count=4 2>/dev/null | od -An -tu4" | tr -d ' ')
        OFF=$cand; SIZE=$((blocks * 4096)); break
    fi
done
# the region must lie completely after the last partition and before the backup GPT
if [ -n "$OFF" ]; then
    [ $((OFF / 512)) -ge "$last_end" ] && [ $(( (OFF + SIZE) / 512 )) -le $((disk - 34)) ] || die "the mu300root filesystem overlaps a partition, refusing to touch it"
    [ $((OFF % 1048576)) -eq 0 ] || die "unexpected filesystem offset $OFF"
    echo "Linux filesystem: offset $OFF, $((SIZE / 1048576)) MiB"
else
    echo "no mu300root filesystem found (already erased?)"
fi
BC=$(hex32)
echo "boot control in misc: slot $(python3 -c 'import sys; print(bytes.fromhex(sys.argv[1][:4]).decode(errors="replace"))' "$BC")"

say "What should be removed?"
wipe=keep
if [ -n "$OFF" ]; then
    echo "  secure  overwrite the whole $((SIZE / 1073741824)) GiB region and verify (recommended, takes a few"
    echo "          minutes; your files are really gone afterwards)"
    echo "  quick   only erase the filesystem headers (fast, but the files stay readable on the flash)"
    echo "  keep    leave the Linux filesystem in place (it just never boots again)"
    ask wipe "Erase the Linux filesystem: secure / quick / keep" secure
    case $wipe in secure|quick|keep) ;; full) wipe=secure ;; *) die "invalid choice" ;; esac
fi
echo
echo "  misc:     boot slot a (Android), Linux boot disabled"
echo "  boot_b:   replaced with a copy of boot_a (stock Android boot image)"
echo "  Linux:    $([ $wipe = keep ] && echo "kept on the eMMC (not bootable)" || echo "$wipe erase of $((SIZE / 1048576)) MiB at offset $OFF")"
echo "  untouched: boot_a, GPT, userdata and all other partitions"
ask confirm "Type UNINSTALL to continue" no
[ "$confirm" = UNINSTALL ] || die "cancelled"

say "Making slot a the boot slot"
MISC_TMP=$(mktemp)
adb shell "su -c 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null > /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
adb pull /data/local/tmp/mu300-pull.bin "$MISC_TMP" >/dev/null 2>&1
adb shell "su -c 'rm -f /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
NEW=$(python3 - "$MISC_TMP" <<'PY'
import struct, sys, zlib
head = open(sys.argv[1], 'rb').read()
bc = bytearray(head[0x800:0x820])
if len(bc) != 32 or bc[4:8] != b'BCAB' or zlib.crc32(bytes(bc[:28])) != struct.unpack('<I', bc[28:])[0]:
    sys.exit('misc has no valid bootloader_control block')
# same block install.sh's boot image restores on Android: a active (prio 15, successful), b inactive
bc[0:4] = b'_a\0\0'; bc[12] = 0x9f; bc[14] = 0x1e
bc[28:32] = struct.pack('<I', zlib.crc32(bytes(bc[:28])))
print(bc.hex())
PY
) || { rm -f "$MISC_TMP"; die "cannot build the slot a boot control block"; }
rm -f "$MISC_TMP"
if [ "$BC" != "$NEW" ]; then
    BIN=$(mktemp)
    python3 -c 'import sys; open(sys.argv[2], "wb").write(bytes.fromhex(sys.argv[1]))' "$NEW" "$BIN"
    adb push "$BIN" $T/mu300-bc-a.bin </dev/null >/dev/null; rm -f "$BIN"
    su_do "dd if=$T/mu300-bc-a.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc 2>/dev/null && sync && rm $T/mu300-bc-a.bin"
    [ "$(hex32)" = "$NEW" ] || die "misc verify failed"
    echo "slot a set"
else
    echo "already on slot a"
fi

say "Restoring boot_b from boot_a"
A=$(su_do 'sha256sum /dev/block/by-name/boot_a' | cut -d' ' -f1)
su_do "dd if=/dev/block/by-name/boot_a of=/dev/block/by-name/boot_b bs=4M 2>/dev/null && sync"
[ "$(su_do 'sha256sum /dev/block/by-name/boot_b' | cut -d' ' -f1)" = "$A" ] || die "boot_b verify failed (misc already points to slot a, Android keeps booting)"
echo "boot_b = boot_a"

if [ $wipe != keep ]; then
    say "Erasing the Linux filesystem ($wipe)"
    # nothing may still use the region (a leftover loop device from the installer)
    busy=$(su_do "for o in /sys/block/loop*/loop/offset; do [ \"\$(cat \$o 2>/dev/null)\" = $OFF ] && echo \${o%/loop/offset}; done")
    [ -z "$busy" ] || die "the Linux region is still attached ($busy); reboot Android and run again"
    skip=$((OFF / 1048576)); mib=$((SIZE / 1048576))
    if [ $wipe = quick ]; then
        # superblock, group descriptors and inode tables of the first groups: the filesystem is gone for mount/blkid
        # (not a secure erase: backup superblocks and file data stay until the space is reused or fully erased)
        su_do "dd if=/dev/zero of=/dev/block/mmcblk0 bs=1048576 seek=$skip count=64 conv=notrunc 2>/dev/null; sync"
    else
        echo "overwriting $((mib / 1024)) GiB, this takes a few minutes"
        # discard first when the eMMC supports it (fast and it also clears blocks the controller has remapped),
        # then overwrite everything, so nothing readable is left behind
        su_do "command -v blkdiscard >/dev/null && blkdiscard -o $OFF -l $SIZE /dev/block/mmcblk0 2>/dev/null; dd if=/dev/zero of=/dev/block/mmcblk0 bs=1048576 seek=$skip count=$mib conv=notrunc 2>/dev/null; sync"
    fi
    m=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((OFF + 1080)) count=2 2>/dev/null | od -An -tx1" | tr -d ' ')
    [ "$m" != 53ef ] || die "the filesystem signature is still there"
    if [ $wipe = secure ]; then
        # verify: sample the region and refuse to report success while anything is still readable
        left=$(su_do "n=0; s=$skip; e=$((skip + mib)); step=$(( mib / 32 + 1 )); while [ \$s -lt \$e ]; do c=\$(dd if=/dev/block/mmcblk0 bs=1048576 skip=\$s count=1 2>/dev/null | tr -d \"\\000\" | wc -c); [ \$c -gt 0 ] && n=\$((n + 1)); s=\$((s + step)); done; echo \$n")
        [ "$left" = 0 ] || die "$left of 32 samples still contain data; run the secure erase again"
        echo "erased and verified (32 samples across the region are empty)"
    else
        echo "erased (headers only)"
    fi
fi

# never delete through a still mounted Linux filesystem
su_do "grep -q \" $T/mu300root \" /proc/mounts || rm -rf $T/mu300root; rm -f $T/mu300-* $T/android-install.sh $T/android-mount-mu300root.sh" >/dev/null

# ---------------------------------------------------------------- giving the space back (EXPERIMENTAL)
# Only for a device where the installer shrank userdata to make room (the 32 GB variant). Growing it back is
# the same one-entry edit in reverse: userdata is the last partition, so its end moves and nothing else does.
# Off by default, because on a device that was never shrunk this would hand the factory gap to Android - the
# numbers below are printed so the choice is made on what is actually there.
say "Checking whether userdata was shrunk to make room"
GPTW=$(mktemp -d)
su_do "dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null > /data/local/tmp/gpt.head" >/dev/null
su_do "dd if=/dev/block/mmcblk0 bs=512 skip=$((disk - 33)) count=33 2>/dev/null > /data/local/tmp/gpt.tail" >/dev/null
adb pull /data/local/tmp/gpt.head "$GPTW/gpt.head" >/dev/null 2>&1
adb pull /data/local/tmp/gpt.tail "$GPTW/gpt.tail" >/dev/null 2>&1
su_do 'rm -f /data/local/tmp/gpt.head /data/local/tmp/gpt.tail' >/dev/null
if [ -s "$GPTW/gpt.head" ] && [ -s "$GPTW/gpt.tail" ] &&
   info=$(python3 "$TOP/tools/resize-last-partition.py" "$GPTW/gpt.head,$GPTW/gpt.tail" \
            --disk-sectors "$disk" --name userdata --show 2>/dev/null); then
    u_first=$(echo "$info" | sed -n 's/^LAST_FIRST=//p')
    u_end=$(echo "$info" | sed -n 's/^LAST_END=//p')
    u_usable=$(echo "$info" | sed -n 's/^USABLE_END=//p')
    free_gib=$(awk -v s=$((u_usable - u_end)) 'BEGIN { printf "%.1f", s * 512 / 1073741824 }')
    echo "  userdata: sectors $u_first..$u_end ($(awk -v s=$((u_end - u_first + 1)) 'BEGIN { printf "%.1f GiB", s * 512 / 1073741824 }'))"
    echo "  unused after it: $free_gib GiB"
    echo
    echo "  On the 64 GB variant about 32 GiB behind userdata is free from the factory - growing userdata"
    echo "  there changes the device away from its original layout. Say yes only if this installer shrank it."
    echo "  EXPERIMENTAL, and it erases Android's data again (the filesystem has to be recreated)."
    ask grow "Grow userdata back over the freed space? (yes/no)" no
    if [ "$grow" = yes ]; then
        ask sure2 "Type ERASE to rewrite the partition table and wipe Android's data" ""
        [ "$sure2" = ERASE ] || die "nothing was changed."
        python3 "$TOP/tools/resize-last-partition.py" "$GPTW/gpt.head,$GPTW/gpt.tail" \
            --disk-sectors "$disk" --name userdata --fill --out "$GPTW/new" || die "the resize was refused; nothing is changed"
        adb push "$GPTW/new.head" /data/local/tmp/new.head >/dev/null 2>&1
        adb push "$GPTW/new.tail" /data/local/tmp/new.tail >/dev/null 2>&1
        # backup copy first, so a power cut between the two leaves the old primary table and a bootable device
        su_do "dd if=/data/local/tmp/new.tail of=/dev/block/mmcblk0 bs=512 seek=$((disk - 33)) conv=fsync 2>/dev/null" >/dev/null
        su_do "dd if=/data/local/tmp/new.head of=/dev/block/mmcblk0 bs=512 conv=fsync 2>/dev/null" >/dev/null
        su_do 'sync; rm -f /data/local/tmp/new.head /data/local/tmp/new.tail' >/dev/null
        su_do "dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null > /data/local/tmp/gpt.head" >/dev/null
        su_do "dd if=/dev/block/mmcblk0 bs=512 skip=$((disk - 33)) count=33 2>/dev/null > /data/local/tmp/gpt.tail" >/dev/null
        adb pull /data/local/tmp/gpt.head "$GPTW/after.head" >/dev/null 2>&1
        adb pull /data/local/tmp/gpt.tail "$GPTW/after.tail" >/dev/null 2>&1
        su_do 'rm -f /data/local/tmp/gpt.head /data/local/tmp/gpt.tail' >/dev/null
        chk=$(python3 "$TOP/tools/resize-last-partition.py" "$GPTW/after.head,$GPTW/after.tail" \
                --disk-sectors "$disk" --name userdata --show) || die "the table on the device does not read back as valid GPT.
The backup copy was written first, so Android's own repair may still fix this. Do not power off."
        [ "$(echo "$chk" | sed -n 's/^LAST_END=//p')" = "$u_usable" ] || die "the table did not take; nothing else was changed."
        echo "  userdata now ends at sector $u_usable"
        say "Clearing Android's data filesystem so it is recreated on the next boot"
        su_do "dd if=/dev/zero of=/dev/block/by-name/userdata bs=1M count=32 conv=fsync 2>/dev/null" >/dev/null
        echo "  Android will set its data partition up again on the next boot; that takes a few minutes."
    fi
else
    echo "  could not read the partition table; leaving it alone"
fi
rm -rf "$GPTW"

# the on-device switch would point at a boot_b that is Android again
say "Removing the on-device switch (Magisk module)"
sh "$TOP/tools/install-magisk-module.sh" --remove || true

say "Done. The device boots stock Android; reboot it once to check."
