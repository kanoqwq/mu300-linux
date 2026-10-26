#!/bin/sh
# One-shot trial boot of a Linux image on slot b, from rooted Android (adb + su).
# Writes ONLY boot_b and the 32-byte bootloader_control in misc; boot_a is never touched.
# usage: flash-trial.sh boot-linux-slotb.img   (expects .json and .misc-slot-b-trial.bin next to it)
set -eu
IMG=$1
BASE=${IMG%.img}
EXP=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['sha256'])" "$BASE.json")
BC_B=$BASE.misc-slot-b-trial.bin
A_HEX=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['misc_slot_a_hex'])" "$BASE.json")
su_do() { adb shell "su -c '$1'" | tr -d '\r'; }

[ "$(shasum -a 256 "$IMG" | cut -d' ' -f1)" = "$EXP" ] || { echo "local image hash mismatch" >&2; exit 1; }
[ "$(su_do 'getprop ro.boot.slot_suffix')" = "_a" ] || { echo "device is not running slot a" >&2; exit 1; }
live=$(su_do 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v' | tr -d ' \n')
[ "$live" = "$A_HEX" ] || { echo "misc bootloader_control is not the expected slot-a state: $live" >&2; exit 1; }

adb push "$IMG" /data/local/tmp/mu300-boot.img
adb push "$BC_B" /data/local/tmp/mu300-bc-b.bin
[ "$(su_do 'sha256sum /data/local/tmp/mu300-boot.img' | cut -d' ' -f1)" = "$EXP" ] || { echo "pushed image hash mismatch" >&2; exit 1; }
su_do 'dd if=/data/local/tmp/mu300-boot.img of=/dev/block/by-name/boot_b bs=4M && sync'
[ "$(su_do 'sha256sum /dev/block/by-name/boot_b' | cut -d' ' -f1)" = "$EXP" ] || { echo "boot_b verify failed; slot a is still active" >&2; exit 1; }
su_do 'dd if=/data/local/tmp/mu300-bc-b.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync'
new=$(su_do 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v' | tr -d ' \n')
[ "$new" = "$(xxd -p "$BC_B" | tr -d '\n')" ] || { echo "misc verify failed: $new" >&2; exit 1; }
echo "slot b armed (one-shot). Rebooting."
adb reboot
