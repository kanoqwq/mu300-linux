#!/bin/bash
# Build mainline for the MU300 inside mu300-mainline-build: Image + DTB. /work = this directory, /src = kernel tree volume
set -eo pipefail
KV=${KV:-6.18.58}
# where Image, DTB and modules.builtin go: /work/out for 6.18, OUTDIR=out-7.2 keeps another kernel next to it
OUT=/work/${OUTDIR:-out}
# the kernel source, fetched once into the volume and unpacked only after verify-kernel-src.sh passed: the signed
# sha256sums.asc (kernel.org autosigner) and the releaser's tar.sign, against the keyrings in keys/ (FINDINGS 161).
# It unpacks beside the tree and is moved into place last, so an interrupted run never leaves a tree that counts
if [ ! -d /src/linux-$KV ]; then
    base=https://cdn.kernel.org/pub/linux/kernel/v${KV%%.*}.x
    dl=/src/.download-$KV
    rm -rf "$dl" && mkdir -p "$dl"
    # the signed checksum list from kernel.org itself: the CDN's copy lagged a new release by hours (7.3: the CDN
    # still had the list of 7.2.11's day, without 7.3's line, FINDINGS 206); it is signed, so where it comes from
    # does not change what is checked
    for f in linux-$KV.tar.xz linux-$KV.tar.sign sha256sums.asc; do
        u=$base/$f; [ "$f" != sha256sums.asc ] || u=https://www.kernel.org/pub/linux/kernel/v${KV%%.*}.x/$f
        curl -fsSL -o "$dl/$f" "$u" || { rm -rf "$dl"; echo "cannot fetch $u" >&2; exit 1; }
    done
    bash /work/verify-kernel-src.sh "$dl" "$KV" || { rm -rf "$dl"; exit 1; }
    tar -xJf "$dl/linux-$KV.tar.xz" -C "$dl" && mv "$dl/linux-$KV" /src/linux-$KV
    rm -rf "$dl"
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
# NFSv4.2 needs the separate v4.1 switch in 6.18; 7.2 folded v4.1 into NFS_V4.
if grep -q '^config NFS_V4_1$' fs/nfs/Kconfig; then
    ./scripts/config --file "$O/.config" --enable NFS_V4_1
fi
make O=$O ARCH=arm64 olddefconfig >/dev/null
# every other tristate option this board can use becomes a module (all-modules.py: not allmodconfig - what is built
# in stays built in, bools only where all-modules.config opens a family, no PCI cards, no debug/test code, nothing
# that would bind to the vendor device tree); MU300_ALL_MODULES=0 builds the fragment alone
rm -f $O/all-modules.report
[ "${MU300_ALL_MODULES:-1}" = 0 ] || python3 /work/all-modules.py . $O
# options Kconfig did not take stop the build (a silently dropped option has cost a working feature before); one
# listed in config-ignored.txt passes only when this kernel has no such symbol at all (7.3 has no PROC_SYSCTL)
bad=
while IFS= read -r l; do
  case "$l" in CONFIG_*=*) k=${l%%=*}; v=${l#*=}; g=$(grep -E "^$k=" $O/.config | cut -d= -f2- || true);
    [ "$v" = n ] && { grep -q "^$k=y" $O/.config && bad="$bad\nNOT DISABLED: $k" || true; continue; }
    [ "$g" = "$v" ] || { grep -qx "$k" /work/config-ignored.txt 2>/dev/null && ! grep -qE "(^| )$k[= ]" $O/.config; } ||
        bad="$bad\nNOT SET: $k want $v got ${g:-unset}";; esac
done < /work/mu300-mainline.config
[ -z "$bad" ] || { printf "config options not taken:$bad\n" >&2; exit 1; }
# a test build's extra kernel arguments: mainline forces its own command line (CONFIG_CMDLINE_FORCE), the
# bootloader's is not read. E.g. MU300_CMDLINE_EXTRA=sdhci_sprd.fail_hs400es=1 (FINDINGS 160). Never for a release.
if [ -n "${MU300_CMDLINE_EXTRA:-}" ]; then
    c=$(sed -n 's/^CONFIG_CMDLINE="\(.*\)"$/\1/p' $O/.config)
    ./scripts/config --file $O/.config --set-str CMDLINE "$c $MU300_CMDLINE_EXTRA"
    make O=$O ARCH=arm64 olddefconfig >/dev/null
    grep '^CONFIG_CMDLINE=' $O/.config
fi
# on failure show the compiler's own messages: a plain grep for "error" also matches object names like
# fdt_strerror.o and used to fill the report with those
make O=$O ARCH=arm64 -j"$(nproc)" Image > $O/build.log 2>&1 || {
    grep -n -E ": (fatal )?error: |-Werror|treated as errors|undefined reference|No such file|Killed|internal compiler error|\*\*\*" -A3 $O/build.log | head -80 || true
    echo "--- end of build.log:"; tail -25 $O/build.log || true
    exit 1
}
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp -I include -I scripts/dtc/include-prefixes \
  /work/dts/ums9620-mu300.dts | dtc -I dts -O dtb -o $O/ums9620-mu300.dtb -
mkdir -p $OUT
cp $O/arch/arm64/boot/Image $O/ums9620-mu300.dtb $OUT/
# for depmod on the device: modprobe of a built-in driver fails without them
cp $O/modules.builtin $O/modules.builtin.modinfo $OUT/
# what all-modules.py added: make-bundle.sh checks the Image against it as against the fragment
rm -f $OUT/all-modules.report
[ ! -f $O/all-modules.report ] || cp $O/all-modules.report $OUT/
ls -la $OUT
