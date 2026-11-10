#!/bin/sh
set -eu
export FORCE_UNSAFE_CONFIGURE=1
cd /build
# Share the SDK lock with hostapd: both use this single prepared OpenWrt tree.
exec 9>/build/unisoc-hostapd.lock
flock 9
[ -d openwrt/.git ] || git clone -q --depth 1 --branch "v$VER" https://git.openwrt.org/openwrt/openwrt.git openwrt
cd openwrt
[ "$(git rev-parse HEAD)" = "$(git rev-parse "v$VER^{commit}")" ] || { echo 'Unexpected OpenWrt SDK source version' >&2; exit 1; }
[ -f feeds/packages/utils/lpac/Makefile ] || ./scripts/feeds update packages
grep -qx 'PKG_VERSION:=2.3.0' feeds/packages/utils/lpac/Makefile
grep -qx 'PKG_HASH:=661dffbd1e9e5732dab4a0bb0a9837d4906c8c66bd748bda262fe3e8d3e420f6' feeds/packages/utils/lpac/Makefile
./scripts/feeds install lpac
sed -i 's/^PKG_RELEASE:=.*/PKG_RELEASE:=1001/' feeds/packages/utils/lpac/Makefile
# OpenWrt may prepare the source again during compile. Apply through its
# package patch stack, so every prepare gets the reviewed transport fixes.
mkdir -p feeds/packages/utils/lpac/patches
cp /src/lpac-stdio.patch feeds/packages/utils/lpac/patches/900-unisoc-stdio.patch
if [ ! -s .config ]; then
    printf '%s\n' 'CONFIG_TARGET_armsr=y' 'CONFIG_TARGET_armsr_armv8=y' 'CONFIG_TARGET_armsr_armv8_DEVICE_generic=y' 'CONFIG_ALL_KMODS=n' 'CONFIG_ALL_NONSHARED=n' > .config
fi
sed -i -E '/^CONFIG_PACKAGE_lpac=|^CONFIG_LPAC_WITH_|^# CONFIG_LPAC_WITH_/d' .config
printf '%s\n' 'CONFIG_PACKAGE_lpac=m' '# CONFIG_LPAC_WITH_PCSC is not set' \
    '# CONFIG_LPAC_WITH_AT is not set' '# CONFIG_LPAC_WITH_UQMI is not set' \
    '# CONFIG_LPAC_WITH_MBIM is not set' 'CONFIG_SIGNED_PACKAGES=n' >> .config
make defconfig
grep -qx 'CONFIG_TARGET_armsr_armv8=y' .config || { echo 'Registered SDK cache is not armsr/armv8' >&2; exit 1; }
ls staging_dir/host/bin/openssl >/dev/null 2>&1 || make -j"$JOBS" tools/install
ls staging_dir/toolchain-*/bin/*-gcc >/dev/null 2>&1 || make -j"$JOBS" toolchain/install
P=package/feeds/packages/lpac
make "$P/clean"
make "$P/prepare" V=s
source_dir=$(find build_dir/target-* -mindepth 1 -maxdepth 2 -type d -name lpac-2.3.0)
[ -n "$source_dir" ] && [ "$(printf '%s\n' "$source_dir" | wc -l)" = 1 ] || { echo 'Expected one prepared lpac source' >&2; exit 1; }
python3 /src/patch-lpac.py --verify "$source_dir"
make -j"$JOBS" "$P/compile" V=s
python3 /src/patch-lpac.py --verify "$source_dir"
apk=$(find bin -type f -name 'lpac-2.3.0-r1001.apk')
[ -n "$apk" ] && [ "$(printf '%s\n' "$apk" | wc -l)" = 1 ] || { echo 'Expected one patched lpac APK' >&2; exit 1; }
stage=$(mktemp -d /out/.lpac.XXXXXX)
trap 'rm -rf "$stage"' EXIT
cp "$apk" "$stage/"
(cd "$stage" && sha256sum ./*.apk > SHA256SUMS)
printf '%s\n' "$BUILD_ID" > "$stage/build-id"
printf '%s\n' 'OpenWrt 25.12.5 / lpac 2.3.0-r1001' \
    'upstream 2.3.0 archive: 661dffbd1e9e5732dab4a0bb0a9837d4906c8c66bd748bda262fe3e8d3e420f6' \
    'stdio connect/open/transmit success tests and NULL error cleanup corrected' > "$stage/source-info"
cp "$stage/"*.apk "$stage/SHA256SUMS" "$stage/source-info" /out/
mv "$stage/build-id" /out/build-id
