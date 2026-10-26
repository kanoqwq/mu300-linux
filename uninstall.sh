#!/bin/sh
# Remove MU300 Linux and return the device to stock Android. Run on a macOS/Linux host with the device booted in
# rooted Android (adb + su), like install.sh.
#
#   ./uninstall.sh
#
# It
#   * makes the slot Android runs from the boot slot in misc (Linux is never started again),
#   * copies Android's boot image over the one of the Linux slot (boot_a to boot_b; boot_b to boot_a when Android
#     runs from slot b), so that slot holds a stock Android boot image instead of the Linux one,
#   * erases the Linux filesystem in the unpartitioned eMMC region (secure: overwrite everything and verify;
#     quick: only the filesystem headers, the data stays readable until the space is reused) - or in the GPT
#     partition named mu300root that another installer made (FINDINGS 179): its contents, never the partition,
#   * erases the Linux filesystem on the SD card (ext4 labelled mu300sd) when there is one and you say so: its
#     first 64 MiB are overwritten, and a card with any other filesystem is never touched,
#   * removes the installer leftovers in /data/local/tmp.
# Android's boot partition, the GPT, userdata and every other partition stay untouched. Needs adb and python3.
#
#   ./uninstall.sh --answers FILE   ask nothing: every question is answered by its variable, from FILE (NAME=value
#                                   lines) or the environment (MU300_NONINTERACTIVE=1); a question without an answer
#                                   is an error that names the variable. MU300_PROGRESS=1 adds the progress stream
#                                   (tools/answers.sh, docs/INSTALLER-AUTOMATION.md). The variables:
#     ANDROID_SERIAL=<serial>            the adb device, with more than one attached (or MU300_ANS_DEVICE=<number>)
#     MU300_ANS_REBOOT_ANDROID=yes|no    the device runs Linux: reboot it into Android over SSH
#     MU300_ANS_WIPE=secure|quick|keep   the Linux filesystem on the eMMC
#     MU300_ANS_SD_WIPE=erase|keep       the Linux filesystem on the SD card
#     MU300_ANS_CONFIRM=UNINSTALL        the summary's question; @stdin waits for it on standard input
#     MU300_ANS_GROW=yes|no              give the space behind userdata back to it (erases Android's data), and
#     MU300_ANS_ERASE_ANDROID=ERASE        the word that allows it
set -eu
T=/data/local/tmp
TOP=$(cd "$(dirname "$0")" && pwd)
ANSWERS_FILE=; _opt=
for a in "$@"; do
    if [ "$_opt" = answers ]; then ANSWERS_FILE=$a; _opt=; continue; fi
    case $a in
        --answers) _opt=answers ;;
        --answers=*) ANSWERS_FILE=${a#--answers=} ;;
        -h|--help) sed -n '2,/^set -eu/{/^set -eu/d;s/^# \{0,1\}//p;}' "$0"; exit 0 ;;
        *) echo "unknown option $a (see --help)" >&2; exit 2 ;;
    esac
done
[ -z "$_opt" ] || { echo "--answers needs a file (see --help)" >&2; exit 2; }
. "$TOP/tools/answers.sh"    # say, die, ask; the answers mode and the progress stream
answers_setup
su_do() { adb shell "su -c '$1'" </dev/null | tr -d '\r'; }
# tools/storage.sh finds the SD card; this script is English only: t() only fills in the {1} of its messages
. "$TOP/tools/i18n.sh"
MU300_LANG=en
progress hello script uninstall.sh version 1 mode uninstall
gib() { awk -v b="$1" 'BEGIN { printf "%.1f GiB", b / 1073741824 }'; }
. "$TOP/tools/storage.sh"
hex32() { su_do "dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v" | tr -d ' \n'; }

step preflight 5
say "Checking host tools and device"
for c in adb python3; do command -v $c >/dev/null || die "$c not found"; done
. "$TOP/tools/linux-mode.sh"
require_android
[ "$(su_do 'id -u')" = 0 ] || die "su does not work on the device"
model="$(su_do 'getprop ro.product.model') / $(su_do 'getprop ro.product.device')"
echo "device: $model"
# (the ZTE V50, experimental: FINDINGS 95, as install.sh takes it)
case "$model" in *MU300*|*F50*|*mu300*|*U30Air*|*U30_Air*|"V50 / MU3351") ;; *) die "this does not look like a ZTE F50/MU300, U30 Air or V50" ;; esac
# Linux is on the slot Android is not on (install.sh): b next to an Android on a, a next to an Android on b
case $(su_do 'getprop ro.boot.slot_suffix') in
    _a) ANDROID_SLOT=a; LINUX_SLOT=b ;;
    _b) ANDROID_SLOT=b; LINUX_SLOT=a ;;
    *) die "cannot tell which slot Android runs from (boot Android first: mu300-next-boot android)" ;;
esac
[ "$(su_do "readlink -f /dev/block/by-name/boot_a")" != "$(su_do "readlink -f /dev/block/by-name/boot_b")" ] ||
    die "boot_a and boot_b are the same partition"
echo "Android runs from slot $ANDROID_SLOT; Linux is on slot $LINUX_SLOT (boot_$LINUX_SLOT)"
info device.model "$model"; info slot.android $ANDROID_SLOT; info slot.linux $LINUX_SLOT

step find 15
say "Looking for the Linux installation"
# where the installers look and boot/init boots (tools/storage.sh): the free region behind the last partition, a GPT
# partition named mu300root that another installer made (kanoqwq's desktop installer, FINDINGS 179), the old fixed
# offset
region_probe || die "could not read the partition table from the device (is su granted? try again)"
region_find_existing || die "no answer from the device while looking for the Linux filesystem; nothing was changed"
[ "${PART_COUNT:-0}" -le 1 ] || die "the eMMC has $PART_COUNT partitions named mu300root, and which one holds Linux cannot be told; nothing was changed"
if [ "$existing" = yes ]; then
    # the filesystem lies completely after the last partition and before the backup GPT, or wholly inside the GPT
    # partition named mu300root (region_in_bounds)
    region_in_bounds "$OFF" "$SIZE" || die "the mu300root filesystem overlaps a partition, refusing to touch it"
    [ $((OFF % 1048576)) -eq 0 ] || die "unexpected filesystem offset $OFF"
    [ $((SIZE / 1048576)) -gt 0 ] || die "the mu300root filesystem has no size, refusing to touch it"
    echo "Linux filesystem: offset $OFF, $((SIZE / 1048576)) MiB"
    [ "$IN_PART" = 0 ] || echo "  in the GPT partition named mu300root that another installer made: an erase empties the partition, the partition itself stays (remove it with the installer that made it)"
else
    OFF=; SIZE=; IN_PART=0
    echo "no mu300root filesystem found (already erased?)"
    [ "${PART_FS:-none}" != ext4 ] || echo "  the GPT partition named mu300root holds another Linux filesystem (not labelled mu300root): not this project's, left as it is"
fi
# the card's installation is a filesystem of its own (sd_existing: yes only for ext4 labelled mu300sd)
sd_probe; SD_HAS=no
[ -n "$SD_DEV" ] && SD_HAS=$(sd_existing)
[ "$SD_HAS" = yes ] && echo "Linux filesystem on the SD card: $SD_DEV, $(gib "$SD_BYTES")"
info region.existing "$([ -n "$OFF" ] && echo yes || echo no)"; info region.offset "$OFF"; info region.size "$SIZE"
info region.partition "$IN_PART"
info sd.device "$SD_DEV"; info sd.bytes "$SD_BYTES"; info sd.state "$SD_HAS"
BC=$(hex32)
echo "boot control in misc: slot $(python3 -c 'import sys; print(bytes.fromhex(sys.argv[1][:4]).decode(errors="replace"))' "$BC")"

step choices 25
say "What should be removed?"
wipe=keep
if [ -n "$OFF" ]; then
    # (in MiB: a region below 1 GiB, as a make-room run of 0.79 GiB leaves, read "the whole 0 GiB region")
    echo "  secure  overwrite the whole $((SIZE / 1048576)) MiB region and verify (recommended, takes a few"
    echo "          minutes; your files are really gone afterwards)"
    echo "  quick   only erase the filesystem headers (fast, but the files stay readable on the flash; an"
    echo "          installation into this space later finds it not empty and asks for the word overwrite)"
    echo "  keep    leave the Linux filesystem in place (it just never boots again)"
    ask wipe "Erase the Linux filesystem: secure / quick / keep" secure MU300_ANS_WIPE
    case $wipe in secure|quick|keep) ;; full) wipe=secure ;; *) die "invalid choice" ;; esac
fi
sdwipe=keep
if [ "$SD_HAS" = yes ]; then
    echo "  The SD card holds a Linux installation (ext4 labelled mu300sd):"
    echo "  erase   remove the filesystem from the card (its first 64 MiB are overwritten: fast, but the files"
    echo "          stay readable on the card until the space is reused)"
    echo "  keep    leave the card as it is (it does not boot once boot_$LINUX_SLOT is restored)"
    ask sdwipe "Linux filesystem on the SD card: erase / keep" erase MU300_ANS_SD_WIPE
    case $sdwipe in erase|keep) ;; *) die "invalid choice" ;; esac
fi
progress step id confirm percent 30 title "What will be done"
echo
echo "  misc:     boot slot $ANDROID_SLOT (Android), Linux boot disabled"
echo "  boot_$LINUX_SLOT:   replaced with a copy of boot_$ANDROID_SLOT (stock Android boot image)"
if [ -z "$OFF" ]; then
    echo "  Linux:    no installation on the eMMC"
else
    echo "  Linux:    $([ $wipe = keep ] && echo "kept on the eMMC (not bootable)" || echo "$wipe erase of $((SIZE / 1048576)) MiB at offset $OFF")"
    [ "$IN_PART" = 0 ] || echo "            (inside the GPT partition named mu300root: the partition stays, only its contents are erased)"
fi
if [ $sdwipe = erase ]; then
    echo "  SD card:  mu300sd filesystem erased ($SD_DEV, first 64 MiB)"
elif [ "$SD_HAS" = yes ]; then
    echo "  SD card:  kept ($SD_DEV, not bootable)"
else
    echo "  SD card:  no installation"
fi
echo "  untouched: boot_$ANDROID_SLOT, GPT, userdata and all other partitions$([ "$IN_PART" = 0 ] || echo " (the partition named mu300root too: it stays)")"
ask confirm "Type UNINSTALL to continue" no MU300_ANS_CONFIRM
[ "$confirm" = UNINSTALL ] || die "cancelled"

# Android still runs from the slot it ran from at the start: its partition is never the one written below
[ "$(su_do 'getprop ro.boot.slot_suffix')" = "_$ANDROID_SLOT" ] || die "Android no longer runs from slot $ANDROID_SLOT; nothing was changed"
step misc 40
say "Making slot $ANDROID_SLOT the boot slot"
MISC_TMP=$(mktemp)
adb shell "su -c 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null > /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
adb pull /data/local/tmp/mu300-pull.bin "$MISC_TMP" >/dev/null 2>&1
adb shell "su -c 'rm -f /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
NEW=$(python3 - "$MISC_TMP" "$ANDROID_SLOT" <<'PY'
import struct, sys, zlib
head = open(sys.argv[1], 'rb').read()
bc = bytearray(head[0x800:0x820])
if len(bc) != 32 or bc[4:8] != b'BCAB' or zlib.crc32(bytes(bc[:28])) != struct.unpack('<I', bc[28:])[0]:
    sys.exit('misc has no valid bootloader_control block')
# same blocks install.sh's boot image restores on Android: Android's slot active (prio 15, successful), the other
# inactive (build-boot-image.py's misc-bc-slot-a.bin / misc-bc-slot-b.bin)
if sys.argv[2] == 'a':
    bc[0:4] = b'_a\0\0'; bc[12] = 0x9f; bc[14] = 0x1e
else:
    bc[0:4] = b'_b\0\0'; bc[12] = 0x1e; bc[14] = 0x9f
bc[28:32] = struct.pack('<I', zlib.crc32(bytes(bc[:28])))
print(bc.hex())
PY
) || { rm -f "$MISC_TMP"; die "cannot build the slot $ANDROID_SLOT boot control block"; }
rm -f "$MISC_TMP"
if [ "$BC" != "$NEW" ]; then
    BIN=$(mktemp)
    python3 -c 'import sys; open(sys.argv[2], "wb").write(bytes.fromhex(sys.argv[1]))' "$NEW" "$BIN"
    adb push "$BIN" $T/mu300-bc-a.bin </dev/null >/dev/null; rm -f "$BIN"
    su_do "dd if=$T/mu300-bc-a.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc 2>/dev/null && sync && rm $T/mu300-bc-a.bin"
    [ "$(hex32)" = "$NEW" ] || die "misc verify failed"
    echo "slot $ANDROID_SLOT set"
else
    echo "already on slot $ANDROID_SLOT"
fi

step boot 55
say "Restoring boot_$LINUX_SLOT from boot_$ANDROID_SLOT"
A=$(su_do "sha256sum /dev/block/by-name/boot_$ANDROID_SLOT" | cut -d' ' -f1)
su_do "dd if=/dev/block/by-name/boot_$ANDROID_SLOT of=/dev/block/by-name/boot_$LINUX_SLOT bs=4M 2>/dev/null && sync"
[ "$(su_do "sha256sum /dev/block/by-name/boot_$LINUX_SLOT" | cut -d' ' -f1)" = "$A" ] || die "boot_$LINUX_SLOT verify failed (misc already points to slot $ANDROID_SLOT, Android keeps booting)"
echo "boot_$LINUX_SLOT = boot_$ANDROID_SLOT"

if [ $wipe != keep ]; then
    step erase 65
    say "Erasing the Linux filesystem ($wipe)"
    # nothing may still use the region (a leftover loop device from the installer)
    busy=$(su_do "for o in /sys/block/loop*/loop/offset; do [ \"\$(cat \$o 2>/dev/null)\" = $OFF ] && echo \${o%/loop/offset}; done")
    [ -z "$busy" ] || die "the Linux region is still attached ($busy); reboot Android and run again"
    skip=$((OFF / 1048576)); mib=$((SIZE / 1048576))
    # once more, in the units the writes use: every megabyte written lies in the free space or in the partition
    region_in_bounds $((skip * 1048576)) $((mib * 1048576)) || die "the region does not lie in the free space; the filesystem was not erased"
    if [ $wipe = quick ]; then
        # superblock, group descriptors and inode tables of the first groups: the filesystem is gone for mount/blkid
        # (not a secure erase: backup superblocks and file data stay until the space is reused or fully erased)
        # (never more than the filesystem: in a partition named mu300root that is the partition's end)
        n=64; [ "$mib" -ge 64 ] || n=$mib
        su_do "dd if=/dev/zero of=/dev/block/mmcblk0 bs=1048576 seek=$skip count=$n conv=notrunc 2>/dev/null; sync"
    else
        echo "overwriting $mib MiB, this takes a few minutes"
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

if [ $sdwipe = erase ]; then
    step sd 80
    say "Erasing the Linux filesystem on the SD card"
    # sd_erase checks the card again on the device (never mmcblk0, an SD card, still mu300sd) before it writes
    sd_erase
    echo "erased ($SD_DEV)"
fi
# A kept internal installation may hold the marker of a card installation next to it (boot/init then waits for
# the card). With the card's installation gone it would only make a later boot wait for nothing.
if [ -n "$OFF" ] && [ $wipe = keep ] && { [ $sdwipe = erase ] || [ "$SD_HAS" != yes ]; }; then
    adb push "$TOP/tools/android-mount-mu300root.sh" $T/ </dev/null >/dev/null
    r=$(su_do "MU300_OFF=$OFF MU300_SIZE=$SIZE sh $T/android-mount-mu300root.sh $T/mu300root >/dev/null && { rm -f $T/mu300root/.mu300/root-on-sd; sync; sh $T/android-mount-mu300root.sh -u $T/mu300root >/dev/null; echo CLEARED; }" 2>/dev/null) || true
    case $r in *CLEARED*) ;; *) echo "note: could not open the kept Linux filesystem to clear its SD card marker (harmless: boot_$LINUX_SLOT is Android)" ;; esac
fi

# never delete through a still mounted Linux filesystem
# (files only: /data/local/tmp/mu300-web is the web installer's directory, and "rm -f mu300-*" said "Is a directory")
su_do "grep -q \" $T/mu300root \" /proc/mounts || rm -rf $T/mu300root; for f in $T/mu300-*; do [ -d \$f ] || rm -f \$f; done; rm -f $T/android-install.sh $T/android-mount-mu300root.sh" >/dev/null

# ---------------------------------------------------------------- giving the space back (EXPERIMENTAL)
# Only for a device where the installer shrank userdata to make room (the 32 GB variant). Growing it back is
# the same one-entry edit in reverse: userdata is the last partition, so its end moves and nothing else does.
# Off by default, because on a device that was never shrunk this would hand the factory gap to Android - the
# numbers below are printed so the choice is made on what is actually there.
step userdata 88
say "Checking whether userdata was shrunk to make room"
GPTW=$(mktemp -d)
if [ "${PART_COUNT:-0}" -gt 0 ]; then
    # userdata is not the last partition then, and only the last one can grow (resize-last-partition.py)
    echo "  a GPT partition named mu300root (another installer's) lies behind userdata: the partition table is left alone"
else
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
        info userdata.first "$u_first"; info userdata.end "$u_end"; info userdata.usable_end "$u_usable"
        echo
        echo "  On the 64 GB variant about 32 GiB behind userdata is free from the factory - growing userdata"
        echo "  there changes the device away from its original layout. Say yes only if this installer shrank it."
        echo "  EXPERIMENTAL, and it erases Android's data again (the filesystem has to be recreated)."
        # (FINDINGS 153: a reinstall on a device given its space back has to make room again - from the outside that is
        # "the installer asks to repartition again")
        echo "  Say no if Linux may be installed again: the installer then finds this space and installs into it"
        echo "  without touching Android. After yes, a new installation has to make room again, which resets"
        echo "  Android once more."
        ask grow "Grow userdata back over the freed space? (yes/no)" no MU300_ANS_GROW
        if [ "$grow" = yes ]; then
            ask sure2 "Type ERASE to rewrite the partition table and wipe Android's data" "" MU300_ANS_ERASE_ANDROID
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
            # Android's kernel keeps the table it booted with: until a restart it still shows userdata ending where it
            # did, and an installer run now would take the space just given to userdata for free space
            echo "  Restart the device before anything else: until then Android still sees the old partition table."
        fi
    else
        echo "  could not read the partition table; leaving it alone"
    fi
fi
rm -rf "$GPTW"

# the on-device switch would point at a Linux slot that holds Android again
step switch 95
say "Removing the on-device switch (Magisk module)"
sh "$TOP/tools/install-magisk-module.sh" --remove || true

step done 100
say "Done. The device boots stock Android; reboot it once to check."
progress done result ok
