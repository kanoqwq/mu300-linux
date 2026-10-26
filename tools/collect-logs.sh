#!/bin/sh
# After a trial falls back to Android: collect LK log, pstore and the init log persisted in the Linux boot partition.
set -eu
OUT=${1:-logs-$(date +%Y%m%d-%H%M%S)}
mkdir -p "$OUT"
adb shell "su -c 'getprop ro.boot.slot_suffix; ls -la /sys/fs/pstore'" | tee "$OUT/state.txt"
# the persistent log is in the slot Android is not on: boot_b, or boot_a when Android runs from b
slot=$(adb shell "su -c 'getprop ro.boot.slot_suffix'" </dev/null | tr -d '\r')
case $slot in _b) LS=a ;; *) LS=b ;; esac
# via a file on the device, never streamed: su may run on a pty that rewrites LF as CRLF (issue #2)
dev_pull() {
    adb shell "su -c 'cat $1 > /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
    adb pull /data/local/tmp/mu300-pull.bin "$2" >/dev/null 2>&1
    adb shell "su -c 'rm -f /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
}
for f in $(adb shell "su -c 'ls /sys/fs/pstore'" | tr -d '\r'); do dev_pull "/sys/fs/pstore/$f" "$OUT/$f"; done
dev_pull /dev/block/by-name/uboot_log "$OUT/uboot_log.raw"
strings -n 6 "$OUT/uboot_log.raw" > "$OUT/uboot_log.txt"
# init writes stages + dmesg to boot_$LS at 48 MiB (8 MiB)
adb shell "su -c 'dd if=/dev/block/by-name/boot_$LS bs=4096 skip=12288 count=2048 2>/dev/null > /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
adb pull /data/local/tmp/mu300-pull.bin "$OUT/linux-persist.raw" >/dev/null 2>&1
adb shell "su -c 'rm -f /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
tr -d '\000' < "$OUT/linux-persist.raw" > "$OUT/linux-persist.txt" && rm -f "$OUT/linux-persist.raw"
sed -n '/MU300-PERSIST-BEGIN/,/--- lsmod/p' "$OUT/linux-persist.txt" || true
echo "saved: $OUT"
