#!/bin/bash
# Build the Mali kbase driver (Mali-G57, DDK r40p0, matching Android's userspace) as an external module.
# Source: realme C51/C53 AndroidT kernel_modules, kernel5.4/gpu/natt/mali (GPL), copied to /src/ext-mali.
set -e
MSRC=${MSRC:-/src/ext-mali}
cd /src/zte-u30air
make O=/src/out-linux ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang LD=ld.lld -j"$(nproc)" M="$MSRC" src="$MSRC" \
  CONFIG_MALI_MIDGARD=m CONFIG_MALI_PLATFORM_NAME=qogirn6pro CONFIG_MALI_DEBUG=n CONFIG_MALI_FENCE_DEBUG=n BUILD=no modules
llvm-strip --strip-debug -o /src/out-linux/mali_kbase.ko "$MSRC/mali_kbase.ko"
