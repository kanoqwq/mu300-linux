#!/bin/bash
set -euo pipefail
KV=${KV:-7.2.8}
source_dir=/src/linux-$KV
objects=/src/out-$KV
module_dir=/src/mod-build/usb3-snapshot
mkdir -p "$module_dir"
cp /work/debug/usb3-snapshot/Makefile /work/debug/usb3-snapshot/usb3_snapshot.c "$module_dir/"
make -C "$source_dir" O="$objects" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- M="$module_dir" CONFIG_DEBUG_INFO_BTF_MODULES= modules
aarch64-linux-gnu-strip --strip-debug "$module_dir/usb3_snapshot.ko"
cp "$module_dir/usb3_snapshot.ko" /diag/usb3_snapshot.ko
sha256sum /diag/usb3_snapshot.ko
