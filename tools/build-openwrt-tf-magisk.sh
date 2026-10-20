#!/bin/sh
# Build a Magisk-installable OpenWrt-on-TF package with Linux 6.18 or 7.2.
# Usage: MU300_KERNEL=6.18|7.2 MU300_INPUTS=work tools/build-openwrt-tf-magisk.sh [OUT.zip]
# The TF image includes the standalone LuCI package from openwrt/luci-app-mu300.
umask 022
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
IN=${MU300_INPUTS:-$TOP/work}
KERNEL=${MU300_KERNEL:-7.2}
case $KERNEL in
    6.18) DEFAULT_UO=$TOP/upstream/out ;;
    7.2) DEFAULT_UO=$TOP/upstream/out-7.2 ;;
    *) echo "unsupported kernel: $KERNEL (expected 6.18 or 7.2)" >&2; exit 1 ;;
esac
UO=${MU300_UPSTREAM_OUT:-$DEFAULT_UO}
PLUGIN=${MU300_LUCI_PLUGIN_SRC:-$TOP/openwrt/luci-app-mu300}
[ -s "$PLUGIN/Makefile" ] || { echo "standalone LuCI plugin missing: $PLUGIN (set MU300_LUCI_PLUGIN_SRC)" >&2; exit 1; }
OUT=${1:-}
PACKAGE_WORK=${MU300_PACKAGE_WORK:-$TOP/work}
ROOTFS_DIR=${MU300_ROOTFS_OUT_DIR:-$TOP/openwrt}
mkdir -p "$PACKAGE_WORK" "$ROOTFS_DIR"
PACKAGE_WORK=$(cd "$PACKAGE_WORK" && pwd)
ROOTFS_DIR=$(cd "$ROOTFS_DIR" && pwd)
ROOTFS=$ROOTFS_DIR/mu300-openwrt-tf-$KERNEL-rootfs.tar.gz
BOOT=$PACKAGE_WORK/boot-linux-tf-$KERNEL.img
GENERIC=$PACKAGE_WORK/ramdisk-$KERNEL-tf.lz4
WRAPPED=$PACKAGE_WORK/Image-$KERNEL.lk

# Hybrid layout relocates original deployment files behind a thin Magisk entry;
# desktop code is not added to the Magisk boot image.
DESKTOP=${MU300_DESKTOP_INSTALLER:-1}
DESKTOP_TOOLS=${MU300_DESKTOP_TOOLS:-$TOP/work/desktop-tools}
# Desktop releases require both native platforms. Resolve the default Mac
# artifact here; package_gui rejects missing/stale artifacts before attachment.
if [ -z "${OPENWRT_GUI_MACOS_ARTIFACT:-}" ] && [ -f "$TOP/installer/artifacts/macos-arm64/build.json" ]; then
    export OPENWRT_GUI_MACOS_ARTIFACT="$TOP/installer/artifacts/macos-arm64"
fi
if [ "$DESKTOP" = 1 ]; then
    [ -s "$DESKTOP_TOOLS/lib/ld-musl-aarch64.so.1" ] || {
        echo "desktop maintenance tools missing: $DESKTOP_TOOLS (set MU300_DESKTOP_TOOLS or MU300_DESKTOP_INSTALLER=0)" >&2; exit 1;
    }
fi

for f in dumps/boot_a.img dumps/misc-head.bin busybox tools/logdw/logdw; do
    [ -s "$IN/$f" ] || { echo "missing $IN/$f" >&2; exit 1; }
done
for f in firmware android-subset tools/bt-init/mu300-bt-init tools/keys/mu300-keys; do
    [ -e "$IN/$f" ] || { echo "required device input missing: $IN/$f" >&2; exit 1; }
done
file "$IN/tools/keys/mu300-keys" | grep -q 'ELF 64-bit.*ARM aarch64' || {
    echo "mu300-keys must be an AArch64 executable" >&2; exit 1;
}
grep -aq 'bq256xx-charger/online' "$IN/tools/keys/mu300-keys" || {
    echo 'mu300-keys is stale: rebuild tools/keys/mu300-keys.c with charger-event support' >&2; exit 1;
}
for f in wcn_bsp.ko sprd_wlan_combo.ko sprdbt_tty.ko mali_kbase.ko; do
    [ -s "$IN/out/modules/$f" ] || { echo "missing $IN/out/modules/$f" >&2; exit 1; }
done
[ -s "$UO/Image" ] || { echo "missing Linux $KERNEL Image in $UO" >&2; exit 1; }
python3 "$TOP/upstream/check-container-support.py" "$UO"
grep -qx 'CONFIG_LEDS_AW9523B=y' "$UO/kernel.config" || {
    echo 'Shared F50/V50 firmware requires built-in AW9523B support; rebuild the kernel before packaging' >&2
    exit 1
}
rel=$(strings "$UO/Image" | sed -n 's/^Linux version \([^ ]*\) .*/\1/p' | head -n1)
case $rel in "$KERNEL".*) ;; *) echo "$UO is kernel '$rel', expected $KERNEL.x" >&2; exit 1 ;; esac
RELEASE_JSON=$PACKAGE_WORK/release-$KERNEL.json
if [ -n "${MU300_VERSION:-}" ]; then
    VERSION=$(python3 "$TOP/tools/release-identity.py" --profile mu300 --kernel "$rel" --version "$MU300_VERSION" --output "$RELEASE_JSON" --field image_version)
else
    VERSION=$(python3 "$TOP/tools/release-identity.py" --profile mu300 --kernel "$rel" --output "$RELEASE_JSON" --field image_version)
fi
if [ -z "$OUT" ]; then
    OUT=$TOP/$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["package_name"])' "$RELEASE_JSON").zip
fi
case $OUT in /*) ;; *) OUT=$TOP/$OUT ;; esac

for m in $(cat "$TOP/upstream/module-order.txt"); do
    [ -s "$UO/modules/$m" ] || { echo "missing $KERNEL module $m" >&2; exit 1; }
done
if grep -qx 'CONFIG_ARM_SPRD_CPUFREQ_V2=m' "$UO/kernel.config"; then
    cpu_ko=$UO/modules/sprd-cpufreq-v2-driver.ko
    [ -s "$cpu_ko" ] || { echo 'CPU voltage module missing; run build-modules.sh' >&2; exit 1; }
    for feature in voltage_offset_uv scaling_voltage_table scaling_voltage_offset scaling_voltage_domain; do
        strings "$cpu_ko" | grep -F "$feature" >/dev/null || {
            echo "CPU module lacks $feature; rebuild modules before packaging" >&2; exit 1;
        }
    done
fi
for m in "$UO"/modules/*.ko; do
    v=$(strings "$m" | sed -n 's/^vermagic=\([^ ]*\) .*/\1/p' | head -n1)
    [ "$v" = "$rel" ] || { echo "$m is built for '$v', Image is '$rel'" >&2; exit 1; }
done
# The TF path packages modules directly rather than going through make-bundle.sh.
# Check both shared wireless modules: matching vermagic does not prove that
# the IPv6 checksum or TX-pressure logging fixes reached the built binaries.
for wireless_driver in sprd_wlan_combo wcn_bsp mu300_thermal; do
    wireless_module=$UO/modules/$wireless_driver.ko
    [ -s "$wireless_module" ] || { echo "missing $wireless_module" >&2; exit 1; }
    if [ -n "$(find "$TOP/upstream/modules/$wireless_driver" -type f -newer "$wireless_module" -print -quit)" ]; then
        echo "$wireless_driver source is newer than $wireless_module; rebuild it for $rel" >&2
        exit 1
    fi
done

echo "==> OpenWrt rootfs ($rel modules included)"
MU300_GPU=0 MU300_INPUTS="$IN" MU300_MAINLINE_OUT="$UO" MU300_LUCI_PLUGIN_SRC="$PLUGIN" \
  MU300_VERSION="$VERSION" MU300_RELEASE_JSON="$RELEASE_JSON" MU300_ROOTFS_OUT_DIR="$ROOTFS_DIR" \
  sh "$TOP/openwrt/build-rootfs.sh" "$(basename "$ROOTFS")"
for f in ./lib/netifd/proto/mu300cell.sh ./lib/netifd/proto/mu300cell-v6.sh \
    ./sbin/block ./lib/libblkid-tiny.so ./etc/init.d/fstab ./etc/mu300/block-mount.build-id \
    ./opt/mu300/bin/mu300-keys ./opt/mu300/bin/mu300-bt-init ./opt/mu300/bin/mu300-atd ./opt/mu300/bin/mu300-at ./opt/mu300/bin/mobile-data \
    ./opt/mu300/bin/u30air-led-status ./etc/hotplug.d/power_supply/70-mu300-led \
    ./opt/mu300/bin/mu300-data-sim-u30 ./opt/mu300/bin/mu300-sim-u30 ./opt/mu300/bin/mu300-sim2-start ./opt/mu300/bin/mu300-sim2-warmup ./opt/mu300/lib/sim-config.sh \
    ./opt/mu300/bin/mu300-v50-sim ./opt/mu300/lib/v50-card.sh ./opt/mu300/lib/network-led.sh \
    ./opt/mu300/bin/mu300-charge-mode ./opt/mu300/bin/mu300-modem-coldboot ./etc/rc.d/S09mu300-charge-mode \
    ./opt/mu300/bin/mu300-user-packages ./etc/init.d/mu300-user-packages ./etc/mu300/image-packages \
    ./opt/mu300/lib/wifi-channels.sh ./usr/share/ucode/wifi/hostapd.uc \
    ./lib/netifd/hostapd-unisoc.sh ./usr/share/ucode/wifi/unisoc-sae.uc \
    ./etc/mu300/wifi-sae.build-id \
    ./opt/mu300/bin/mu300-usb ./opt/mu300/bin/v50-led-status ./opt/mu300/bin/mu300-platform-config ./opt/mu300/bin/openwrt-board-check ./opt/mu300/bin/mu300-sim-env ./opt/mu300/bin/mu300-data-sim ./opt/mu300/bin/mu300-dual-radio ./etc/init.d/mu300-post ./etc/init.d/mu300-atd \
    ./etc/init.d/mu300-smsd ./opt/mu300/bin/mu300-smsd ./opt/mu300/bin/mu300-sms ./etc/sysctl.d/99-mu300-console.conf \
    ./usr/share/luci/menu.d/luci-app-mu300.json \
    ./www/luci-static/resources/view/mu300/home.js \
    ./www/luci-static/resources/mu300/refresh.js \
    ./www/luci-static/resources/view/mu300/sms.css \
    ./usr/libexec/unisoc-modem/dashboard-rates ./usr/libexec/unisoc-modem/dashboard-network.sh \
    ./usr/libexec/unisoc-modem/apn ./usr/libexec/unisoc-modem/apn.uc ./usr/libexec/unisoc-modem/apn-identity ./etc/config/unisoc_apn \
    ./www/luci-static/resources/view/mu300/apn.js \
    ./usr/libexec/unisoc-modem/lock ./usr/libexec/unisoc-modem/boot-replay ./usr/libexec/unisoc-modem/sim-scope.sh \
    ./usr/libexec/unisoc-modem/lock-apply.sh ./usr/libexec/unisoc-modem/lock-jobs.sh \
    ./usr/libexec/unisoc-modem/traffic ./usr/libexec/unisoc-modem/traffic-core.uc \
    ./usr/libexec/unisoc-modem/hotspot ./usr/libexec/unisoc-modem/hotspot.uc \
    ./www/luci-static/resources/view/mu300/traffic.js ./etc/init.d/unisoc-traffic \
    ./www/luci-static/resources/view/mu300/cpu.js ./etc/init.d/unisoc-cpu \
    ./www/luci-static/resources/mu300/auto-mode.js \
    ./www/luci-static/resources/mu300/cpu-cores.js ./etc/init.d/unisoc-cpu-cores \
    ./usr/libexec/rpcd/unisoc-cpu-cores ./usr/libexec/unisoc-modem/cpu-cores \
    ./usr/libexec/unisoc-modem/cpu-cores.uc ./etc/rc.d/S99unisoc-cpu-cores \
    ./usr/libexec/unisoc-modem/cpu ./usr/libexec/unisoc-modem/cpu.uc \
    ./usr/libexec/unisoc-modem/cpu-thermal.uc \
    ./usr/libexec/unisoc-modem/cpu-voltage ./usr/libexec/unisoc-modem/cpu-voltage.uc \
    ./opt/mu300/bin/cpu-voltage-platform ./etc/init.d/mu300-cpu-driver \
    ./etc/rc.d/S08mu300-cpu-driver ./etc/rc.d/K89unisoc-cpu \
    ./usr/libexec/unisoc-modem/sms-forward ./usr/libexec/unisoc-modem/power-supply.sh ./usr/libexec/unisoc-modem/forward-template.uc \
    ./www/luci-static/resources/view/mu300/forward.js ./etc/init.d/unisoc-sms-forward \
    ./usr/libexec/unisoc-modem/data-sim ./www/luci-static/resources/view/mu300/sim.js \
    ./usr/libexec/unisoc-modem/operator.uc ./usr/libexec/unisoc-modem/refresh-config \
    ./www/luci-static/resources/view/mu300/device.js \
    ./usr/libexec/unisoc-modem/device-usb \
    ./etc/hotplug.d/net/90-unisoc-usb-host \
    ./etc/hotplug.d/iface/90-unisoc-usb-host \
    ./usr/lib/lua/luci/i18n/mu300.en.lmo ./usr/lib/lua/luci/i18n/mu300.tr.lmo \
    ./etc/init.d/unisoc-modem-ui ./etc/rc.d/S95unisoc-modem-ui \
    ./www/luci-static/aurora/main.css \
    ./www/luci-static/resources/view/aurora/studio.js \
    ./www/luci-static/resources/view/aurora/marketplace.js \
    ./usr/share/luci/menu.d/luci-app-aurora.json ./usr/share/rpcd/acl.d/luci-app-aurora.json \
    ./usr/lib/lua/luci/i18n/aurora-config.zh-cn.lmo ./usr/lib/lua/luci/i18n/aurora-config.tr.lmo \
    ./usr/share/ucode/luci/template/themes/aurora/header.ut; do
    tar -tzf "$ROOTFS" "$f" >/dev/null || { echo "built rootfs missing required file: $f" >&2; exit 1; }
done
tar -xOzf "$ROOTFS" ./etc/config/luci | grep -q "mediaurlbase .*/luci-static/aurora" || {
    echo "built rootfs does not enable Aurora by default" >&2; exit 1;
}
tar -tzf "$ROOTFS" "./lib/modules/$rel/sprd_wlan_combo.ko" >/dev/null || {
    echo "built rootfs missing $rel Wi-Fi module" >&2; exit 1;
}

echo "==> Linux $KERNEL boot image (installer selects the opposite slot)"
python3 "$TOP/upstream/wrap-image.py" "$UO/Image" "$WRAPPED"
python3 "$TOP/boot/build-boot-image.py" --generic-ramdisk --modules "$UO/modules" \
  --module-order "$TOP/upstream/module-order.txt" --busybox "$IN/busybox" \
  --logdw "$IN/tools/logdw/logdw" --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" \
  --out "$GENERIC" >/dev/null
python3 "$TOP/boot/build-boot-image.py" \
  --stock-boot "$IN/dumps/boot_a.img" --misc-head "$IN/dumps/misc-head.bin" \
  --kernel "$WRAPPED" --append-ramdisk "$GENERIC" --modules "$IN/out/modules" \
  --init "$TOP/boot/init" --busybox "$IN/busybox" --logdw "$IN/tools/logdw/logdw" \
  --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" \
  --android-subset "$IN/android-subset" --root-target sd --out "$BOOT" >/dev/null

STAGE=$(mktemp -d "$PACKAGE_WORK/tf-magisk-stage.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
cp "$TOP/android/magisk/mu300-openwrt-tf/module.prop" \
   "$TOP/android/magisk/mu300-openwrt-tf/customize.sh" \
   "$TOP/android/magisk/mu300-openwrt-tf/locale.sh" \
   "$TOP/android/magisk/mu300-linux-switch/switch.sh" \
   "$TOP/tools/android-install.sh" "$TOP/tools/android-bootchain.sh" "$TOP/tools/mu300-vendor-from-device.sh" \
   "$TOP/tools/tf-storage.sh" "$TOP/tools/tf-stage.sh" "$TOP/tools/tf-install-worker.sh" "$STAGE/"
cp "$TOP/android/magisk/mu300-linux-switch/bootchain.sh" "$STAGE/bootchain.sh"
sed -i "s/Linux 7\.2/Linux $KERNEL/g; s/KERNEL=7\.2/KERNEL=$KERNEL/g" "$STAGE/customize.sh"
VERSION_CODE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["build_epoch"])' "$RELEASE_JSON")
sed -i "s/7\.2/$KERNEL/g; s/^version=.*/version=$VERSION/; s/^versionCode=.*/versionCode=$VERSION_CODE/" "$STAGE/module.prop"
cp "$IN/busybox" "$STAGE/busybox"
cp "$ROOTFS" "$STAGE/mu300-openwrt.tar.gz"
cp "$BOOT" "$STAGE/boot-linux.img"
mkdir -p "$STAGE/kernel-evidence"
cp "$UO/kernel.config" "$UO/modules.in-tree" "$UO/kernel.sha256" "$UO/modules.sha256" "$STAGE/kernel-evidence/"
(cd "$STAGE" && sha256sum boot-linux.img | cut -d' ' -f1 > boot-linux.sha256)
python3 "$TOP/tools/tf-payload-manifest.py" "$STAGE"
chmod 755 "$STAGE"/*.sh "$STAGE/busybox"
if [ "$DESKTOP" = 1 ]; then
    if [ -n "${MU300_DESKTOP_EXE:-}" ]; then
        python3 "$TOP/desktop/package.py" --stage "$STAGE" --tools "$DESKTOP_TOOLS" --kernel "$KERNEL" --exe "$MU300_DESKTOP_EXE"
    else
        python3 "$TOP/desktop/package.py" --stage "$STAGE" --tools "$DESKTOP_TOOLS" --kernel "$KERNEL"
    fi
    cp "$RELEASE_JSON" "$STAGE/desktop/release.json"
    # Include release metadata in the desktop checksum envelope.
    python3 - "$STAGE" <<'PY_RELEASE'
import hashlib, json, sys
from pathlib import Path
stage = Path(sys.argv[1])
path = stage/'desktop/manifest.json'
manifest = json.loads(path.read_text())
release = stage/'desktop/release.json'
manifest['desktop']['desktop/release.json'] = hashlib.sha256(release.read_bytes()).hexdigest()
path.write_text(json.dumps(manifest, indent=2)+'\n')
PY_RELEASE
fi
rm -f "$OUT"
python3 - "$TOP" "$STAGE" "$OUT" <<'PY'
import os, sys, zipfile
sys.path.insert(0, os.path.join(sys.argv.pop(1), 'installer'))
from archive_tools import write_member
src, out = map(os.path.abspath, sys.argv[1:])
with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(root, name)
            write_member(z, p, os.path.relpath(p, src).replace(os.sep, '/'))
PY
echo "built $OUT (kernel $rel)"
