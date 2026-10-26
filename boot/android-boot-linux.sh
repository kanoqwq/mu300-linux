#!/bin/sh
# From rooted Android: boot the Linux image already on boot_b once more (no reflash).
# usage: android-boot-linux.sh boot-linux-slotb.img   (uses its .json/.misc-slot-b-trial.bin for verification)
# Inside Linux, `sudo mu300-next-boot linux` then keeps Linux as the default.
set -eu
BASE=${1%.img}
# boot_b's tail (48 MiB+) holds the persistent init log and changes on every boot; compare the image part only
EXP=$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()[:48<<20]).hexdigest())" "$1")
su_do() { adb shell "su -c '$1'" | tr -d '\r'; }
[ "$(su_do 'dd if=/dev/block/by-name/boot_b bs=1048576 count=48 2>/dev/null | sha256sum' | cut -d' ' -f1)" = "$EXP" ] || { echo "boot_b does not contain $1; use flash-trial.sh" >&2; exit 1; }
adb push "$BASE.misc-slot-b-trial.bin" /data/local/tmp/mu300-bc-b.bin
su_do 'dd if=/data/local/tmp/mu300-bc-b.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync'
[ "$(su_do 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v' | tr -d ' \n')" = "$(xxd -p "$BASE.misc-slot-b-trial.bin" | tr -d '\n')" ] || { echo "misc verify failed" >&2; exit 1; }
adb reboot
