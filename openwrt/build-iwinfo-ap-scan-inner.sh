#!/bin/sh
set -eu
export FORCE_UNSAFE_CONFIGURE=1
cd /build/openwrt
[ "$(git rev-parse HEAD)" = "$(git rev-parse 'v25.12.5^{commit}')" ]
P=package/network/utils/iwinfo
grep -q 'PKG_SOURCE_VERSION:=f5dd57a84cc31a403a1383dd14944fa2e2b5824a' "$P/Makefile"
grep -q 'PKG_MIRROR_HASH:=a9ed29ea9f222d222fc554238e9c79675e1784293685198f759785da9940571f' "$P/Makefile"
sed -i 's/^PKG_RELEASE:=.*/PKG_RELEASE:=1001/' "$P/Makefile"
grep -q '^CONFIG_PACKAGE_libiwinfo=m$' .config || printf '\nCONFIG_PACKAGE_libiwinfo=m\n' >> .config
make defconfig
make "$P/prepare" V=s
source_dir=$(find build_dir/target-* -mindepth 1 -maxdepth 1 -type d -name 'libiwinfo-2026.01.14~f5dd57a8')
[ -n "$source_dir" ] && [ "$(printf '%s\n' "$source_dir" | wc -l)" = 1 ]
python3 /src/patch-iwinfo-ap-scan.py "$source_dir"
# A new backend revision edits the prepared C file. Invalidate the package's
# build dependency through its own prepare stamp, retaining source/tool caches.
for stamp in "$source_dir"/.prepared_*; do
    [ -f "$stamp" ] && touch "$stamp"
done
make -j"$JOBS" "$P/compile" V=s
apk=$(find bin -type f -name 'libiwinfo20230701-2026.01.14~f5dd57a8-r1001.apk')
[ -n "$apk" ] && [ "$(printf '%s\n' "$apk" | wc -l)" = 1 ]
cp "$apk" /out/
(cd /out && sha256sum libiwinfo20230701-2026.01.14~f5dd57a8-r1001.apk > SHA256SUMS)
printf '%s\n' "$BUILD_ID" > /out/build-id
