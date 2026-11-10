#!/bin/sh
# One shared aarch64 OpenWrt 25.12.5 APK, built in the existing SDK cache.
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:?Pass the registered lpac artifact directory}
VER=${UNISOC_WRT_VER:-25.12.5}
[ "$VER" = 25.12.5 ] || { echo 'Update the pinned lpac build checks for this OpenWrt version' >&2; exit 1; }
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
if python3 "$TOP/openwrt/lpac-state.py" "$OUT" >/dev/null 2>&1; then
    echo 'eUICC lpac: verified patched APK cache'
    exit 0
fi
stamp=$(python3 -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["fingerprint"]())' "$TOP/openwrt/lpac-state.py")
case "$(uname -m)" in x86_64) PLAT=linux/amd64;; aarch64|arm64) PLAT=linux/arm64;; *) exit 1;; esac
docker build --platform "$PLAT" -t unisoc-hostapd-build -f "$TOP/openwrt/hostapd.Dockerfile" "$TOP/openwrt"
docker run --rm --platform "$PLAT" -v "${UNISOC_WRT_CACHE:-e5-openwrt-src}:/build" \
    -v "$TOP/openwrt:/src:ro" -v "$OUT:/out" -e VER="$VER" -e BUILD_ID="$stamp" \
    -e JOBS="${UNISOC_JOBS:-4}" unisoc-hostapd-build sh /src/build-lpac-inner.sh
python3 "$TOP/openwrt/lpac-state.py" "$OUT" >/dev/null
