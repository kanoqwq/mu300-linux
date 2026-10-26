#!/bin/sh
# Build e2fsck for the boot ramdisk: one static aarch64 binary (musl), which boot/init runs on the unmounted root
# filesystem only when its superblock records an error (FINDINGS 182; kanoqwq's desktop init did it first).
#   tools/e2fsck/build.sh OUT        (OUT: the binary to write; needs docker with arm64 support)
# e2fsprogs from kernel.org, checked against the SHA-256 of its signed sha256sums.asc, built in a pinned Alpine image
# with fixed paths and dates: the same inputs give the same binary. e2fsck alone (no NLS, no undo database, no
# other tools), stripped; about 1.4 MB, some 0.6 MB in the LZ4 ramdisk.
set -eu
OUT=${1:?usage: tools/e2fsck/build.sh OUT}
VER=1.47.4
URL=https://mirrors.edge.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v$VER/e2fsprogs-$VER.tar.xz
SHA=fd5bf388cbdbe006a3d3b318d983b2948382440acc85a87f1e7d108653e8db0b
IMG=alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
mkdir -p "$(dirname "$OUT")"
o=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
docker run --rm --platform linux/arm64 -v "$tmp":/out "$IMG" /bin/sh -eu -c "
apk add -q build-base linux-headers
cd /tmp
wget -q -O e2.tar.xz $URL
echo '$SHA  e2.tar.xz' | sha256sum -c - >/dev/null
tar -xJf e2.tar.xz
cd e2fsprogs-$VER
export SOURCE_DATE_EPOCH=0 TZ=UTC LC_ALL=C
./configure -q --disable-nls --disable-tdb --disable-debugfs --disable-imager --disable-resizer --disable-defrag \
    --disable-fuse2fs --disable-uuidd --disable-fsck --disable-e2initrd-helper --disable-elf-shlibs \
    --disable-bmap-stats --without-libarchive \
    CFLAGS='-Os -ffile-prefix-map=/tmp/e2fsprogs-$VER=. -ffunction-sections -fdata-sections' \
    LDFLAGS='-static -Wl,--gc-sections -Wl,--build-id=none' >/dev/null
make -s -j\$(nproc) libs >/dev/null
make -s -C e2fsck e2fsck >/dev/null
strip e2fsck/e2fsck
cp e2fsck/e2fsck /out/e2fsck
"
cp "$tmp/e2fsck" "$o"
chmod 755 "$o"
echo "$o: $(wc -c < "$o") bytes, sha256 $( (sha256sum "$o" 2>/dev/null || shasum -a 256 "$o") | cut -d' ' -f1)"
