#!/bin/sh
# Build adbd for MU300 Linux: one static aarch64 binary (musl) for Ubuntu and OpenWrt alike.
#   tools/adbd/build.sh OUT        (OUT: the binary to write; needs docker with arm64 support)
# The daemon of AOSP's adb, platform-tools 34.0.5, from the release tarball of github.com/nmeum/android-tools (the
# AOSP sources with BoringSSL vendored; checked against its SHA-256), built with CMakeLists.txt here - adbd only,
# the tarball's own build makes the host tools - and mu300-adbd.patch:
#   - USB (FunctionFS at /dev/usb-ffs/adb) on Linux, and nothing else: no TCP or vsock listener, ever
#   - authentication by one switch, the environment variable mu300-adbd starts it with: MU300_ADB_AUTH=open accepts
#     every computer (ro.adb.secure=0; the default of "mu300-usb adb auth"), anything else is public-key
#     authentication (ro.adb.secure=1): keys in /etc/mu300/adb_keys and /root/.android/adb_keys, no Android
#     framework socket that could approve one
#   - the shell: the user's login shell, TMPDIR=/tmp, umask 022; no property monitor (Android's)
# stubs.cpp leaves out JDWP, adb over Wi-Fi (pairing) and mDNS, and with them protobuf. Debian's and Ubuntu's adbd
# package (26.04 only) is no option: it has no authorisation to turn on and listens on TCP 5555.
set -eu
OUT=${1:?usage: tools/adbd/build.sh OUT}
HERE=$(cd "$(dirname "$0")" && pwd)
VER=34.0.5
URL=https://github.com/nmeum/android-tools/releases/download/$VER/android-tools-$VER.tar.xz
SHA=fb09cff12cfb82acf42a8ebebbc0342671bfcd02117716368bdc73fdda60304a
mkdir -p "$(dirname "$OUT")"
o=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
docker run --rm --platform linux/arm64 -v "$HERE":/adbd:ro -v "$tmp":/out alpine:3.20 /bin/sh -eu -c "
apk add -q build-base cmake samurai perl go linux-headers libcap-dev pkgconf \
    brotli-dev brotli-static lz4-dev lz4-static zstd-dev zstd-static
cd /tmp
wget -q -O at.tar.xz $URL
echo '$SHA  at.tar.xz' | sha256sum -c -
tar -xJf at.tar.xz
cd android-tools-$VER/vendor
patch -p1 < /adbd/mu300-adbd.patch
cmake -S /adbd -B /tmp/b -G Ninja -DCMAKE_BUILD_TYPE=Release -DANDROID_TOOLS=\$PWD >/dev/null
ninja -C /tmp/b adbd >/dev/null
cp /tmp/b/adbd /out/adbd
"
cp "$tmp/adbd" "$o"
chmod 755 "$o"
echo "$o: $(wc -c < "$o") bytes"
