#!/bin/sh
# Build cltest for bionic with plain clang (no NDK). $1 = directory with Android's libc.so, libdl.so, libOpenCL.so
set -e
L=$1
cd "$(dirname "$0")"
clang --target=aarch64-linux-android29 -O2 -fPIE -nostdlib -ffreestanding -fuse-ld=lld \
  -Wl,-pie -Wl,--dynamic-linker=/system/bin/linker64 -Wl,--allow-shlib-undefined -Wl,-z,max-page-size=4096 \
  crt_android.c cltest.c "$L/libOpenCL.so" "$L/libc.so" "$L/libdl.so" -o cltest
