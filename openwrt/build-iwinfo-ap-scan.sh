#!/bin/sh
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$TOP/work/iwinfo-ap-scan}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
BUILD_ID=$(python3 -c 'import runpy,sys; print(runpy.run_path(sys.argv[1])["build_id"]())' "$TOP/openwrt/iwinfo-ap-scan-state.py")
if [ -s "$OUT/build-id" ] && python3 "$TOP/openwrt/iwinfo-ap-scan-state.py" "$OUT" >/dev/null; then
    echo 'SC2355 AP scan: verified library APK cache'
    exit 0
fi
# Reuse the existing hostapd/OpenWrt buildroot and cross toolchain.
docker build -t unisoc-hostapd-build -f "$TOP/openwrt/hostapd.Dockerfile" "$TOP/openwrt"
docker run --rm -v "${UNISOC_WRT_CACHE:-e5-openwrt-src}:/build" \
    -v "$TOP/openwrt:/src:ro" -v "$OUT:/out" \
    -e "BUILD_ID=$BUILD_ID" -e "JOBS=${UNISOC_JOBS:-4}" \
    --entrypoint /bin/sh unisoc-hostapd-build /src/build-iwinfo-ap-scan-inner.sh
python3 "$TOP/openwrt/iwinfo-ap-scan-state.py" "$OUT" >/dev/null
