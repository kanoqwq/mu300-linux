#!/bin/bash
set -euo pipefail
KV=${KV:-7.2.8}
source_dir=/src/linux-$KV
objects=/src/out-$KV
module_dir=/src/mod-build/usb3-stock-speed
mkdir -p "$module_dir"
cp /work/debug/usb3-stock-speed/Makefile /work/debug/usb3-stock-speed/usb3_stock_speed.c "$module_dir/"
make -C "$source_dir" O="$objects" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- M="$module_dir" CONFIG_DEBUG_INFO_BTF_MODULES= modules
aarch64-linux-gnu-strip --strip-debug "$module_dir/usb3_stock_speed.ko"
cp "$module_dir/usb3_stock_speed.ko" /diag/usb3_stock_speed.ko
sha256sum /diag/usb3_stock_speed.ko
