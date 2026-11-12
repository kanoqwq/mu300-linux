#!/bin/bash
# Build mainline for the MU300 inside mu300-mainline-build: Image + DTB. /work = this directory, /src = kernel tree volume
set -eo pipefail
KV=${KV:-6.18.54}
[ "$(uname -m)" = aarch64 ] || export CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
# where Image, DTB and modules.builtin go: /work/out for 6.18, OUTDIR=out-7.2 keeps another kernel next to it
# WSL builds need a case-sensitive output for xt_RATEEST/xt_rateest modules.
# MU300_OUT_DIR may point at a Linux-native bind mount instead of /work on NTFS.
OUT=${MU300_OUT_DIR:-/work/${OUTDIR:-out}}
# the kernel source, fetched once into the volume and checked against kernel.org's checksum list
if [ ! -d /src/linux-$KV ]; then
    base=https://cdn.kernel.org/pub/linux/kernel/v${KV%%.*}.x
    cd /src
    curl -fsSLO "$base/linux-$KV.tar.xz"
    curl -fsSL "$base/sha256sums.asc" | grep " linux-$KV.tar.xz\$" | sha256sum -c - || { rm -f linux-$KV.tar.xz; exit 1; }
    tar -xJf linux-$KV.tar.xz && rm linux-$KV.tar.xz
fi
cd /src/linux-$KV
# MU300 patches (idempotent); MU300_PROBE_STAGE=N adds the boot-stage reset probe (debug)
python3 /work/port/install.py .
[ -n "${MU300_PROBE_STAGE:-}" ] && python3 /work/debug/install-probe.py . "$MU300_PROBE_STAGE"
# a patch either applies or is applied already; anything else stops the build (it used to be skipped silently).
# patches/<major.minor>/ holds what only one kernel needs (what a newer one has upstream already)
# .mu300-patches lists the ones applied to this tree (by checksum): a patch that a later one builds on (0009 under
# 0012) can neither be applied again nor reversed alone, so a second run would stop at it
for p in /work/patches/*.patch /work/patches/"$(echo "$KV" | cut -d. -f1,2)"/*.patch; do
    [ -e "$p" ] || continue
    s=$(sha256sum < "$p" | cut -d' ' -f1)
    grep -qx "$s" .mu300-patches 2>/dev/null && continue
    if patch -p1 -N -s --dry-run < "$p" >/dev/null 2>&1; then patch -p1 -N -s < "$p"
    elif patch -p1 -R -s -f --dry-run < "$p" >/dev/null 2>&1; then :
    else echo "patch does not apply to $KV: $p" >&2; exit 1; fi
    echo "$s" >> .mu300-patches
done
O=/src/out-$KV
mkdir -p $O
make O=$O ARCH=arm64 allnoconfig >/dev/null
./scripts/kconfig/merge_config.sh -m -O $O $O/.config /work/mu300-mainline.config >/dev/null
# PR #228: NFSv4.1 is a separate prerequisite only on kernels exposing it.
if grep -q '^config NFS_V4_1$' fs/nfs/Kconfig; then
    scripts/config --file "$O/.config" --enable NFS_V4_1
fi
make O=$O ARCH=arm64 olddefconfig >/dev/null
# options Kconfig did not take; ones known not to exist in this kernel are listed in config-ignored.txt, any
# other one stops the build (a silently dropped option has cost a working feature before)
bad=
while IFS= read -r l; do
  case "$l" in CONFIG_*=*) k=${l%%=*}; v=${l#*=}; g=$(grep -E "^$k=" $O/.config | cut -d= -f2- || true);
    [ "$v" = n ] && { grep -q "^$k=y" $O/.config && bad="$bad\nNOT DISABLED: $k" || true; continue; }
    [ "$g" = "$v" ] || grep -qx "$k" /work/config-ignored.txt 2>/dev/null || bad="$bad\nNOT SET: $k want $v got ${g:-unset}";; esac
done < /work/mu300-mainline.config
[ -z "$bad" ] || { printf "config options not taken:$bad\n" >&2; exit 1; }
# on failure show the compiler's own messages: a plain grep for "error" also matches object names like
# fdt_strerror.o and used to fill the report with those
make O=$O ARCH=arm64 -j"$(nproc)" Image > $O/build.log 2>&1 || {
    grep -n -E ": (fatal )?error: |-Werror|treated as errors|undefined reference|No such file|Killed|internal compiler error|\*\*\*" -A3 $O/build.log | head -80 || true
    echo "--- end of build.log:"; tail -25 $O/build.log || true
    exit 1
}
if grep -qx 'CONFIG_DEBUG_INFO_BTF=y' "$O/.config"; then
    # Consume all output: grep -q can SIGPIPE readelf under pipefail.
    "${CROSS_COMPILE:-}readelf" -SW "$O/vmlinux" | grep -E '\.BTF[[:space:]]+PROGBITS' >/dev/null || {
        echo 'BTF missing from the linked kernel' >&2; exit 1;
    }
fi
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp -I include -I scripts/dtc/include-prefixes \
  /work/dts/ums9620-mu300.dts | dtc -I dts -O dtb -o $O/ums9620-mu300.dtb -
mkdir -p $OUT
cp $O/arch/arm64/boot/Image $O/ums9620-mu300.dtb $OUT/
# for depmod on the device: modprobe of a built-in driver fails without them
cp $O/modules.builtin $O/modules.builtin.modinfo $OUT/
# Tie feature checks to the Image actually built, not just the requested fragment.
cp "$O/.config" "$OUT/kernel.config"
(cd "$OUT" && sha256sum Image kernel.config > kernel.sha256)
ls -la $OUT
