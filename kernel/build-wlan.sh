#!/bin/bash
# Build sprd_wlan_combo (SC2355 Wi-Fi) as an external module against out-linux.
# Source: realme C51/C53 AndroidT kernel_modules/kernel5.4/wcn/wlan/wlan_combo (GPL), patched for the MU300.
set -e
WSRC=${WSRC:-/src/ext-wlan_combo}
# build-all.sh hands over a tree that already carries the patches (.mu300-patches); a tree prepared by hand (BUILD.md)
# gets them here, in order, and a patch that does not apply stops the build (as upstream/build.sh)
cd "$WSRC"
if [ ! -f .mu300-patches ]; then
    for p in /work/patches/wlan_combo-*.patch; do patch -p1 -s -f < "$p"; done
    cat /work/patches/wlan_combo-*.patch | sha256sum | cut -d" " -f1 > .mu300-patches
fi
cd /src/zte-u30air
make O=/src/out-linux ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang LD=ld.lld -j"$(nproc)" M="$WSRC" modules
llvm-strip --strip-debug -o /src/out-linux/sprd_wlan_combo.ko "$WSRC/sprd_wlan_combo.ko"
