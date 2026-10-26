#!/bin/sh
# Back up EVERYTHING of a rooted Android device, before porting to it: every partition by name (userdata and super
# included), the eMMC boot areas (splloader lives there) and both GPT copies, each checked against a SHA-256 taken on
# the device. tools/backup-device.sh keeps only the irreplaceable parts; this is the "before touching anything" copy.
#
#   tools/backup-full.sh OUTDIR            (ANDROID_SERIAL=... picks the device when more than one is attached)
#
# Partitions are read in 512 MiB pieces, gzipped on the device, pulled and removed there, so a partition larger
# than the free space of /data is not a problem, and no binary ever goes through `adb exec-out "su -c ..."` (a
# pty there can rewrite LF as CRLF, issue #2). The pieces of one partition are gzip members of one file:
# OUTDIR/<name>.img.gz unpacks with plain gunzip. SHA256SUMS lists the raw images, as the device computed them.
# userdata is copied while Android runs, so it is only as consistent as a power cut; everything else is static.
# Nothing here belongs in a public repository: the dumps contain device identifiers and personal data.
set -eu
OUT=${1:?usage: tools/backup-full.sh OUTDIR}
CHUNK=512            # MiB per piece
T=/data/local/tmp/mu300-backup
command -v adb >/dev/null || { echo "adb not found" >&2; exit 1; }
su_do() { adb shell "su -c '$1'" </dev/null | tr -d '\r'; }
[ "$(su_do 'id -u')" = 0 ] || { echo "su does not work on the device" >&2; exit 1; }
mkdir -p "$OUT"
su_do "rm -rf $T; mkdir -p $T" >/dev/null

# dump NAME DEVICE: one block device into OUTDIR/NAME.img.gz, verified
dump() {
    name=$1 dev=$2
    size=$(su_do "blockdev --getsize64 $dev")
    pieces=$(( (size + CHUNK * 1048576 - 1) / (CHUNK * 1048576) ))
    printf '%-20s %6s MiB  ' "$name" $((size / 1048576))
    want=$(su_do "sha256sum $dev" | cut -d' ' -f1)
    : > "$OUT/$name.img.gz"
    i=0
    while [ $i -lt $pieces ]; do
        su_do "dd if=$dev bs=1048576 skip=$((i * CHUNK)) count=$CHUNK 2>/dev/null | gzip -1 > $T/piece.gz" >/dev/null
        adb pull $T/piece.gz "$OUT/.piece.gz" </dev/null >/dev/null 2>&1 || { echo "pull failed" >&2; exit 1; }
        cat "$OUT/.piece.gz" >> "$OUT/$name.img.gz"
        i=$((i + 1))
        printf '.'
    done
    rm -f "$OUT/.piece.gz"
    have=$(gunzip -c "$OUT/$name.img.gz" | { shasum -a 256 2>/dev/null || sha256sum; } | cut -d' ' -f1)
    if [ "$have" = "$want" ]; then
        echo "$want  $name.img" >> "$OUT/SHA256SUMS"
        echo " ok"
    else
        echo " CHECKSUM MISMATCH (device $want, copy $have)"
        echo "$name" >> "$OUT/FAILED"
    fi
}

: > "$OUT/SHA256SUMS"; rm -f "$OUT/FAILED"
su_do 'getprop' > "$OUT/getprop.txt"
su_do 'cat /proc/partitions; ls -l /dev/block/by-name/' > "$OUT/partitions.txt"
echo "==> partitions (by name), eMMC boot areas, GPT"
for n in $(su_do 'ls /dev/block/by-name/'); do
    dump "$n" "/dev/block/by-name/$n"
done
for b in mmcblk0boot0 mmcblk0boot1; do
    su_do "[ -e /dev/block/$b ] && echo y" | grep -q y && dump "$b" "/dev/block/$b"
done
disk=$(su_do 'cat /sys/block/mmcblk0/size')
su_do "dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null > $T/gpt.head; dd if=/dev/block/mmcblk0 bs=512 skip=$((disk - 33)) count=33 2>/dev/null > $T/gpt.tail" >/dev/null
adb pull $T/gpt.head "$OUT/gpt-primary.bin" </dev/null >/dev/null 2>&1
adb pull $T/gpt.tail "$OUT/gpt-backup.bin" </dev/null >/dev/null 2>&1
# the space after the last partition (where a Linux region goes): empty or not, 16 samples of 1 MiB
last=$(su_do 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; done; echo $e')
step=$(( (disk - last) / 2048 / 16 ))
nz=$(su_do "n=0; i=0; while [ \$i -lt 16 ]; do c=\$(dd if=/dev/block/mmcblk0 bs=1048576 skip=\$(( $last / 2048 + i * $step )) count=1 2>/dev/null | tr -d \"\\000\" | wc -c); [ \$c -gt 0 ] && n=\$((n + 1)); i=\$((i + 1)); done; echo \$n")
echo "unpartitioned space after sector $last: $(( (disk - last) / 2048 )) MiB, $nz of 16 samples non-zero" | tee "$OUT/unpartitioned.txt"
su_do "rm -rf $T" >/dev/null
echo
if [ -e "$OUT/FAILED" ]; then echo "FAILED: $(tr '\n' ' ' < "$OUT/FAILED")"; exit 1; fi
echo "all $(wc -l < "$OUT/SHA256SUMS" | tr -d ' ') images verified; $(du -sh "$OUT" | cut -f1) in $OUT"
