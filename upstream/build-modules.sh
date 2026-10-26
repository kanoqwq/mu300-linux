#!/bin/bash
# Build the out-of-tree vendor modules (WCN, modem, Mali) against the mainline tree built by build.sh, and collect
# the kernel's own modules next to them (upstream/out/modules, flat).
# Run inside the mu300-mainline-build container: bash /work/build-modules.sh [module-dir...]
set -eo pipefail
KV=${KV:-6.18.58}
K=/src/linux-$KV
O=/src/out-$KV
OUT=/work/${OUTDIR:-out}   # as in build.sh
# every out-of-tree module, in dependency order (wlan/bt use wcn_bsp, the PMIC watchdog and Mali the modem's headers;
# mu300_thermal, the stock virtual thermal zone, and ums9620_lpintc need only the kernel.
mods=${*:-wcn_bsp sprd_wlan_combo sprdbt_tty sprd_modem sprd_pmic_wdt mali mu300_thermal ums9620_lpintc}
mkdir -p $OUT/modules
# xt_HL.ko and xt_hl.ko (and three more pairs) must stay two files: not on a host directory that ignores case
. /work/case-sensitive.sh
case_sensitive $OUT/modules || exit 1
# a full build starts clean, so no module of another kernel release ends up next to the new ones
[ $# -gt 0 ] || rm -f $OUT/modules/*.ko $OUT/modules/*.log
# Module.symvers for the built-in exports (pcie-sprd etc.)
make -C $K O=$O ARCH=arm64 -j"$(nproc)" modules > $O/modules.log 2>&1 || { tail -20 $O/modules.log; exit 1; }
# the kernel's own modules (the =m options of mu300-mainline.config), stripped, flat next to the vendor ones: the
# bundle and mu300-update keep one flat directory, which depmod (Ubuntu) and kmodloader (OpenWrt) both index
rm -rf "$O/mod-install"
make -C $K O=$O ARCH=arm64 INSTALL_MOD_PATH="$O/mod-install" INSTALL_MOD_STRIP=1 DEPMOD=true modules_install >> $O/modules.log 2>&1 ||
    { tail -20 $O/modules.log; exit 1; }
# the set of the previous run goes first: an option no longer =m leaves no module behind on a partial run
if [ -f $O/modules.in-tree ]; then while read -r ko; do rm -f "$OUT/modules/$ko"; done < $O/modules.in-tree; fi
find "$O/mod-install/lib/modules" -name '*.ko' -exec basename {} \; | sort > $O/modules.in-tree
# one flat directory holds them all, and modprobe takes - and _ for the same: two modules of one name would shadow
# each other
tr - _ < $O/modules.in-tree | sort > $O/modules.in-tree.names
[ -z "$(uniq -d $O/modules.in-tree.names)" ] || { echo "in-tree modules share a name: $(uniq -d $O/modules.in-tree.names)" >&2; exit 1; }
# none of them may load by itself on the boards: udev loads what matches a node of the vendor device tree
krel=$(cat $O/include/config/kernel.release)
depmod -b "$O/mod-install" "$krel"
python3 /work/check-dt-modules.py "$O/mod-install/lib/modules/$krel/modules.alias" /work/dt-compatibles.txt \
    $(sed 's/#.*//' /work/dt-modules-allowed.txt)
# depmod's index of them, for make-bundle.sh's check of the on-demand guard (check-autoload-blocked.py)
cp "$O/mod-install/lib/modules/$krel/modules.dep" "$O/mod-install/lib/modules/$krel/modules.alias" $OUT/
find "$O/mod-install/lib/modules" -name '*.ko' -exec cp {} $OUT/modules/ \;
extra=
for m in $mods; do
    rm -rf /src/mod-build/$m && mkdir -p /src/mod-build && cp -r /work/modules/$m /src/mod-build/$m
    # wlan/bt use wcn_bsp's exports and its vendor headers (../wcn_bsp/kinclude)
    [ -d /src/mod-build/wcn_bsp ] || cp -r /work/modules/wcn_bsp /src/mod-build/wcn_bsp
    # the PMIC watchdog talks to pm_sys over SIPC, so it needs the modem stack's headers next to it
    [ -d /src/mod-build/sprd_modem ] || cp -r /work/modules/sprd_modem /src/mod-build/sprd_modem
    # the Mali DDK needs its own configuration switches (same ones the 5.4 build uses)
    margs=
    kcflags=
    [ "$m" = mali ] && kcflags="-I/src/mod-build/mali/kinclude"
    # the Mali driver calls Trusty for protected mode, so it needs the vendor trusty headers that ship with
    # the modem modules
    [ "$m" = mali ] && [ ! -d /src/mod-build/mali/kinclude ] && cp -r /work/modules/sprd_modem/kinclude /src/mod-build/mali/kinclude
    [ "$m" = mali ] && margs="src=/src/mod-build/mali CONFIG_MALI_MIDGARD=m CONFIG_MALI_PLATFORM_NAME=qogirn6pro CONFIG_MALI_DEVFREQ=y CONFIG_DEVFREQ_THERMAL=y CONFIG_MALI_DEBUG=n CONFIG_MALI_FENCE_DEBUG=n BUILD=no"
    make -C $O ARCH=arm64 M=/src/mod-build/$m KBUILD_EXTRA_SYMBOLS="$extra" KCFLAGS="$kcflags" $margs -j"$(nproc)" modules 2>&1 | tee $OUT/modules/$m.log
    [ -f /src/mod-build/$m/Module.symvers ] && extra="$extra /src/mod-build/$m/Module.symvers"
    for ko in $(find /src/mod-build/$m -name '*.ko'); do
        ! echo "${ko##*/}" | tr - _ | grep -Fqx -f - $O/modules.in-tree.names || { echo "$m: ${ko##*/} has the name of an in-tree module" >&2; exit 1; }
        cp "$ko" $OUT/modules/
        # the kernel's own modules are stripped by modules_install; these carry the DWARF of CONFIG_DEBUG_INFO
        # (several MiB each) - --strip-debug keeps their .BTF
        strip --strip-debug "$OUT/modules/${ko##*/}"
    done
done
cp "$O/modules.in-tree" "$OUT/modules.in-tree"
ls -la $OUT/modules/*.ko
