#!/bin/sh
# Back up the partitions that cannot be replaced from anyone else's firmware. Run on a macOS/Linux host with the
# device in rooted Android (adb + su), ideally BEFORE installing anything.
#
#   tools/backup-device.sh [OUTDIR]        per-device data + bootloader chain (default: backup/<date>)
#   MU300_BACKUP_ALL=1 tools/backup-device.sh OUTDIR   also the stock boot images (a few hundred MB more)
#
# Why: `prodnv`, the modem NV partitions and the calibration areas hold this unit's IMEI, RF calibration and
# factory data. A generic firmware image cannot restore them - lose them and the modem side stays broken even
# after a full reflash. The bootloader chain (splloader lives in the eMMC boot area, uboot, sml, trustos, vbmeta)
# is what a BROM/SPD recovery needs to put the device back together.
#
# Nothing here belongs in a public repository: the dumps contain device identifiers.
set -eu

# Never stream a binary through `adb exec-out "su -c ..."`: on some devices su gives the command a pty and
# the tty layer rewrites every LF as CRLF, silently inflating the bytes by about one in 256. Write the file
# on the device and pull it instead - correct whether or not that device's su allocates a pty (issue #2).
dev_pull() {  # dev_pull DEVICE_PATH LOCAL_PATH
    adb shell "su -c 'cat $1 > /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
    adb pull /data/local/tmp/mu300-pull.bin "$2" >/dev/null 2>&1
    adb shell "su -c 'rm -f /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
}
OUT=${1:-backup/$(date +%Y%m%d-%H%M%S)}
T=/data/local/tmp

# per-device and security state: small, irreplaceable
CRITICAL="prodnv miscdata persist ztecfg ztepersist calinv isedata teecfg_a teecfg_b
          nr_fixnv1_a nr_fixnv1_b nr_fixnv2_a nr_fixnv2_b nr_runtimenv1 nr_runtimenv2
          nr_deltanv_a nr_deltanv_b nr_phy_a nr_phy_b"
# bootloader chain and verification data: needed to recover a device that no longer boots
BOOTCHAIN="sml_a sml_b uboot_a uboot_b trustos_a trustos_b vbmeta_a vbmeta_b misc"
# stock boot images (optional, large)
IMAGES="boot_a boot_b vendor_boot_a vendor_boot_b init_boot_a init_boot_b dtb_a dtb_b dtbo_a dtbo_b"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
su_do() { adb shell "su -c '$1'" </dev/null | tr -d '\r'; }

command -v adb >/dev/null || die "adb not found"
[ "$(adb get-state 2>/dev/null)" = device ] || die "no adb device (boot Android, enable USB debugging)"
# a device running MU300 Linux answers adb too (MU300LINUX-...), with a root shell and none of Android's partitions
case $(adb get-serialno 2>/dev/null) in MU300LINUX*) die "this adb device runs MU300 Linux, not Android: boot Android first (sudo mu300-next-boot android && sudo reboot)" ;; esac
[ "$(su_do 'id -u')" = 0 ] || die "su does not work on the device"

mkdir -p "$OUT"
list=$CRITICAL
list="$list $BOOTCHAIN"
[ "${MU300_BACKUP_ALL:-0}" = 1 ] && list="$list $IMAGES"

say "Backing up to $OUT"
total=0
: > "$OUT/sha256sums.txt"
for p in $list; do
    dev=$(su_do "readlink -f /dev/block/by-name/$p 2>/dev/null")
    [ -n "$dev" ] || { echo "  $p: not on this device, skipped"; continue; }
    dev_pull "$dev" "$OUT/$p.img"
    size=$(wc -c < "$OUT/$p.img" | tr -d ' ')
    [ "$size" -gt 0 ] || { rm -f "$OUT/$p.img"; echo "  $p: empty, skipped"; continue; }
    # verify against the device instead of trusting the transfer
    want=$(su_do "sha256sum $dev" | cut -d' ' -f1)
    have=$( (shasum -a 256 "$OUT/$p.img" 2>/dev/null || sha256sum "$OUT/$p.img") | cut -d' ' -f1)
    [ "$want" = "$have" ] || die "$p: checksum mismatch, the dump is not trustworthy"
    printf '%s  %s.img\n' "$have" "$p" >> "$OUT/sha256sums.txt"
    printf '  %-16s %6s MiB  ok\n' "$p" "$((size / 1048576))"
    total=$((total + size))
done
# the partition table itself, so a wiped GPT can be rebuilt
su_do 'for p in /sys/block/mmcblk0/mmcblk0p*; do echo "${p##*/} $(cat $p/start) $(cat $p/size)"; done' > "$OUT/gpt-layout.txt"
adb shell "su -c 'dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null > /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
adb pull /data/local/tmp/mu300-pull.bin "$OUT/gpt-header.bin" >/dev/null 2>&1
adb shell "su -c 'rm -f /data/local/tmp/mu300-pull.bin'" </dev/null >/dev/null
su_do 'ls -l /dev/block/by-name' > "$OUT/by-name.txt"

say "Done: $((total / 1048576)) MiB in $OUT"
echo "  Keep this off the device and out of any repository: it contains your IMEI and calibration data."
echo "  Restore a single partition later with:"
echo "    adb push $OUT/<name>.img $T/p.img"
echo "    adb shell \"su -c 'dd if=$T/p.img of=/dev/block/by-name/<name> && sync'\""
