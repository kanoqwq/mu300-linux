#!/bin/bash
set -euo pipefail
KV=${KV:-7.2.8}
module_dir=/src/mod-build/usb3-cc-snapshot
mkdir -p "$module_dir"
cp /work/debug/usb3-cc-snapshot/Makefile /work/debug/usb3-cc-snapshot/usb3_cc_snapshot.c "$module_dir/"
make -C /src/linux-$KV O=/src/out-$KV ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- M="$module_dir" CONFIG_DEBUG_INFO_BTF_MODULES= modules
aarch64-linux-gnu-strip --strip-debug "$module_dir/usb3_cc_snapshot.ko"
cp "$module_dir/usb3_cc_snapshot.ko" /diag/usb3_cc_snapshot.ko
sha256sum /diag/usb3_cc_snapshot.ko
