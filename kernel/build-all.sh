#!/bin/sh
# Build everything install.sh --build and tools/make-release.sh need from public sources, in one step:
#   kernel/build-all.sh            -> out/Image, out/modules/*.ko, out/modules.builtin*, out/modules-u30air/*.ko
# Sources (pinned commits, downloaded into the docker volume $MU300_KBUILD_VOLUME on first run):
#   ZTE UMS9620 MiFi 5.4.254 kernel (GPL release published by Enceka)
#   realme C51/C53 AndroidT kernel_modules: wlan_combo (Wi-Fi), bluetooth tty-pcie, Mali kbase (sparse checkout)
# Needs docker (arm64 host or emulation). Rebuilds incrementally when run again.
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
VOL=${MU300_KBUILD_VOLUME:-mu300-kernel}
OUT=${MU300_KERNEL_OUT:-$TOP/out}
KERNEL_REPO=https://github.com/Enceka/android_kernel_zte_ums9620_mifi_u30air.git
KERNEL_REV=b50db5b6224c11db42a246e8fcdaad1e1d59f478
MODULES_REPO=https://github.com/realme-kernel-opensource/realme_C51_C53_Narzo-N53-AndroidT-kernel-source.git
MODULES_REV=4381465ccaf87fcf3215b9cd42f4a685df40e5e0

docker build -q -t mu300-kbuild "$TOP/kernel" >/dev/null
docker volume create "$VOL" >/dev/null
W=$(mktemp -d)
# on a Linux docker the container writes as root: the outputs are handed to the user below, and a cleanup that
# still cannot remove something does not fail the build that has already copied them out. The config merge logs and
# the rejected fragment options go with the scratch copy, unless MU300_KBUILD_LOGS names a directory to keep them in
# (the release workflow does, for a failed build).
cleanup() {
    [ -z "${MU300_KBUILD_LOGS:-}" ] || { mkdir -p "$MU300_KBUILD_LOGS" && cp "$W"/*.log "$W"/fragment-rejected.txt "$MU300_KBUILD_LOGS/"; } 2>/dev/null || true
    rm -rf "$W" 2>/dev/null || true
}
trap cleanup EXIT
# build-linux.sh writes its logs next to the config: give it a scratch copy of kernel/
cp -R "$TOP/kernel/." "$W/"
cp "$TOP/kernel/f50-stock-B09.config" "$W/device.config"

docker run --rm -v "$VOL":/src -v "$W":/work \
  -e KERNEL_REPO=$KERNEL_REPO -e KERNEL_REV=$KERNEL_REV -e MODULES_REPO=$MODULES_REPO -e MODULES_REV=$MODULES_REV \
  mu300-kbuild bash -euc '
fetch() {  # fetch DIR REPO REV [sparse paths...]: shallow checkout of one commit
    d=$1 repo=$2 rev=$3; shift 3
    [ "$(git -C "$d" rev-parse HEAD 2>/dev/null)" = "$rev" ] && return 0
    rm -rf "$d"; git init -q "$d"; git -C "$d" remote add origin "$repo"
    if [ $# -gt 0 ]; then
        git -C "$d" sparse-checkout init --cone; git -C "$d" sparse-checkout set "$@"
        # partial clone (git 2.25 needs the promisor settings before the first filtered fetch)
        git -C "$d" config extensions.partialClone origin
        git -C "$d" config remote.origin.promisor true
        git -C "$d" config remote.origin.partialclonefilter blob:none
        git -C "$d" fetch -q --depth 1 --filter=blob:none origin "$rev"
    else
        git -C "$d" fetch -q --depth 1 origin "$rev"
    fi
    git -C "$d" checkout -q FETCH_HEAD
}
echo "==> sources"
fetch /src/zte-u30air "$KERNEL_REPO" "$KERNEL_REV"
cd /src/zte-u30air
# the tree is the pinned commit plus exactly these patches: reapply from a clean checkout whenever they change
KPATCHES="bluetooth-marlin3-link-policy of-reserved-mem-skip regdb-wens-certificate sipa-delegate-einprogress wcn-pcie-scan-timeout sipc-base-addr-attr sipa-delegate-pm-runtime sipc-debug-once sprdwcn-mbuf-pool-deinit-null logspam-quiet sipa-wcn-logspam-quiet wcn-pcie-rx-push-cursor xfrm-sysctl-fragment-per-netns"
sum=$(cd /work/patches && cat $(for p in $KPATCHES; do echo $p.patch; done) | sha256sum | cut -d" " -f1)
if [ "$(cat .mu300-patches 2>/dev/null)" != "$sum" ]; then
    git checkout -q -f "$KERNEL_REV" && git clean -q -fdx -e .mu300-patches
    for p in $KPATCHES; do patch -p1 -s -f < /work/patches/$p.patch; done
    echo "$sum" > .mu300-patches
fi
echo -gb50db5b6224c > .scmversion
M=kernel_modules/kernel5.4
fetch /src/realme "$MODULES_REPO" "$MODULES_REV" $M/wcn/wlan/wlan_combo $M/wcn/bluetooth/driver $M/gpu/natt/mali
# The driver trees are the pristine copy plus exactly their patches, like the kernel above: a copy patched in place
# cannot take a changed patch set (a second run found wlan_combo-pcie-post-init-retry neither applied nor reverted,
# because a later patch had moved its lines), so the copy is remade whenever the patch files change.
patched_copy() { # patched_copy DEST SOURCE PATCH-GLOB
    local sum; sum=$(cat /work/patches/$3 | sha256sum | cut -d" " -f1)
    [ "$(cat "$1/.mu300-patches" 2>/dev/null)" = "$sum" ] && return 0
    rm -rf "$1" && cp -r "$2" "$1"
    (cd "$1" && for p in /work/patches/$3; do patch -p1 -s -f < "$p"; done)
    echo "$sum" > "$1/.mu300-patches"
}
patched_copy /src/ext-wlan_combo /src/realme/$M/wcn/wlan/wlan_combo 'wlan_combo-*.patch'
patched_copy /src/ext-sprdbt /src/realme/$M/wcn/bluetooth/driver 'sprdbt-*.patch'
[ -d /src/ext-mali ] || cp -r /src/realme/$M/gpu/natt/mali /src/ext-mali

echo "==> kernel"
bash /work/build-linux.sh
echo "==> Wi-Fi, Bluetooth, GPU modules"
bash /work/build-wlan.sh >/dev/null
make O=/src/out-linux ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang LD=ld.lld -j"$(nproc)" M=/src/ext-sprdbt \
  BSP_BOARD_UNISOC_WCN_SOCKET=pcie modules >/dev/null
bash /work/build-mali.sh >/dev/null

echo "==> collecting"
rm -rf /work/out && mkdir -p /work/out/modules
cp /src/out-linux/arch/arm64/boot/Image /src/out-linux/modules.builtin /src/out-linux/modules.builtin.modinfo /work/out/
find /src/out-linux -name "*.ko" -exec cp {} /work/out/modules/ \;
cp /src/ext-wlan_combo/sprd_wlan_combo.ko /src/ext-sprdbt/sprdbt_tty.ko /work/out/modules/
llvm-strip --strip-debug /work/out/modules/*.ko
echo "$(ls /work/out/modules | wc -l) modules, $(strings /work/out/Image | grep -m1 "^Linux version 5" | cut -d" " -f1-3)"
echo "==> ZTE U30 Air modules"
bash /work/build-u30air.sh
'
docker run --rm -v "$W":/work mu300-kbuild chown -R "$(id -u):$(id -g)" /work/out
mkdir -p "$OUT"
rm -rf "$OUT/modules" "$OUT/modules-u30air"
cp -R "$W/out/." "$OUT/"
echo "kernel outputs in $OUT"
