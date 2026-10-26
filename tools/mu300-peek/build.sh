#!/bin/sh
# Build mu300_peek.ko against the same kernel kernel/build-all.sh builds.
#   tools/mu300-peek/build.sh [OUT]      default: work/k54/mu300_peek.ko
set -eu
TOP=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:-$TOP/work/k54}
docker build -q -t mu300-kbuild "$TOP/kernel" >/dev/null
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
cp "$TOP/tools/mu300-peek/mu300_peek.c" "$TOP/tools/mu300-peek/Kbuild" "$W/"
docker run --rm -v mu300-kernel:/src -v "$W":/work mu300-kbuild bash -euc '
cd /src/zte-u30air
make O=/src/out-linux ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang LD=ld.lld -j"$(nproc)" M=/work modules >/tmp/b.log 2>&1 ||
    { tail -20 /tmp/b.log; exit 1; }'
mkdir -p "$OUT"; cp "$W/mu300_peek.ko" "$OUT/"
echo "$OUT/mu300_peek.ko"
