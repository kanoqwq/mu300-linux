#!/bin/sh
# Maintainer: build the generic release assets used by ./install.sh (prebuilt mode) and optionally publish them.
#   tools/make-release.sh TAG             build into release/TAG and audit the contents
#   tools/make-release.sh TAG --publish   also create/update the GitHub release (gh)
# Needs docker and the kernel outputs in $MU300_KERNEL_OUT (default: out/, from kernel/build-all.sh).
# The assets must never contain proprietary files or anything from a particular device: the audit below fails the
# build if firmware, Android userspace, host keys or local settings end up in an image.
set -eu
TAG=${1:?usage: tools/make-release.sh TAG [--publish]}
PUBLISH=${2:-}
TOP=$(cd "$(dirname "$0")/.." && pwd)
KOUT=${MU300_KERNEL_OUT:-$TOP/out}
REPO=${MU300_REPO:-dikeckaan/mu300-linux}
D=$TOP/release/$TAG
IN=$D/inputs
[ -f "$KOUT/Image" ] && ls "$KOUT"/modules/*.ko >/dev/null 2>&1 || { echo "kernel outputs missing in $KOUT (kernel/build-all.sh)" >&2; exit 1; }
ls "$KOUT"/modules-u30air/*.ko >/dev/null 2>&1 || { echo "U30 Air modules missing in $KOUT/modules-u30air (kernel/build-all.sh)" >&2; exit 1; }
[ -z "$(git -C "$TOP" status --porcelain)" ] || { echo "commit your changes first: the release must match a commit" >&2; exit 1; }
rm -rf "$D" && mkdir -p "$IN/out" "$IN/tools/logdw" "$IN/tools/bt-init" "$IN/tools/gpu"

SML_CHECK=$(python3 "$TOP/tools/sml-voltage/build.py")
MODEM_LOADER_PATCH=$(python3 "$TOP/tools/modem-loader/build.py")
echo "==> helper binaries"
cp -R "$KOUT/modules" "$IN/out/modules"
cp "$KOUT/modules.builtin" "$KOUT/modules.builtin.modinfo" "$IN/out/"
docker build -q -t mu300-kbuild "$TOP/kernel" >/dev/null
docker build -q -t mu300-ubuntu:24.04 "$TOP/rootfs" >/dev/null
docker build -q --build-arg BASE=ubuntu:26.04 -t mu300-ubuntu:26.04 "$TOP/rootfs" >/dev/null
docker run --rm mu300-ubuntu:24.04 cat /bin/busybox > "$IN/busybox"; chmod +x "$IN/busybox"
docker run --rm -v "$TOP/tools":/src:ro -v "$IN/tools":/o mu300-kbuild sh -c '
  gcc -O2 -static -o /o/logdw/logdw /src/logdw/logdw.c &&
  gcc -O2 -static -o /o/bt-init/mu300-bt-init /src/bt-init/mu300-bt-init.c'
sh "$TOP/tools/keys/build.sh" "$IN/tools/keys"
sh "$TOP/tools/atd-io/build.sh" "$IN/tools/atd-io"
# cltest links against Android's libraries at build time only; use a local build when there is one
[ -f "$TOP/tools/gpu/cltest" ] && cp "$TOP/tools/gpu/cltest" "$IN/tools/gpu/cltest"

echo "==> kernel bundle"
K=$D/kernel && mkdir -p "$K"
cp -R "$IN/out/modules" "$K/modules"
cp -R "$KOUT/modules-u30air" "$K/modules-u30air"
# the devices this bundle runs on (mu300-update and the installers check it)
echo "f50 u30air" > "$K/devices"
cp "$KOUT/Image" "$KOUT/modules.builtin" "$KOUT/modules.builtin.modinfo" "$IN/busybox" "$IN/tools/logdw/logdw" "$K/"
# the device-independent part of the boot ramdisk, which mu300-update puts behind the device's own ramdisk to update
# the kernel and the boot image without a computer (same builder and file list as install.sh)
python3 "$TOP/boot/build-boot-image.py" --generic-ramdisk --modules "$IN/out/modules" --busybox "$IN/busybox" \
  --device-modules "u30air=$KOUT/modules-u30air" \
  --logdw "$IN/tools/logdw/logdw" --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" \
  --out "$K/ramdisk-generic.lz4" >/dev/null
tar -C "$K" -czf "$D/mu300-kernel.tar.gz" .
rm -rf "$K"

echo "==> mainline kernel bundles"
# "mu300-update kernel 6.18|7.2": built by upstream/build.sh + build-modules.sh at this commit - 6.18 into
# upstream/out, 7.2 into upstream/out-7.2 (OUTDIR=out-7.2 KV=7.2.x); each build must be the version its name says
for kv in 6.18:out 7.2:out-7.2; do
    v=${kv%%:*}; o=$TOP/upstream/${kv#*:}
    [ -f "$o/Image" ] || { echo "$o/Image missing (upstream/build.sh, build-modules.sh)" >&2; exit 1; }
    rel=$(strings "$o/Image" | sed -n 's/^Linux version \([^ ]*\) .*/\1/p' | head -1)
    case $rel in "$v".*) ;; *) echo "$o holds $rel, not $v" >&2; exit 1 ;; esac
    MU300_UPSTREAM_OUT=$o sh "$TOP/upstream/make-bundle.sh" "$D/mu300-kernel-$v.tar.gz" "$D/mu300-kernel.tar.gz"
done

# 24.04 keeps the name it always had (older installers and mu300-update ask for it); 26.04 has its own
for u in 24.04 26.04; do
    echo "==> Ubuntu $u root filesystem (generic)"
    B=$D/ubuntu-build && rm -rf "$B" && mkdir -p "$B"
    tar -C "$TOP/rootfs" --exclude ./base.tar --exclude './*.tar.gz' -cf - . | tar -xf - -C "$B"
    cid=$(docker create mu300-ubuntu:$u /bin/true); docker export "$cid" > "$B/base.tar"; docker rm "$cid" >/dev/null
    cltest=""; [ -f "$IN/tools/gpu/cltest" ] && cltest="-v $IN/tools/gpu/cltest:/cltest:ro"
    # shellcheck disable=SC2086
    docker run --rm -v "$SML_CHECK":/sml-voltage-check:ro -v "$MODEM_LOADER_PATCH":/modem-loader-patch:ro -v "$B":/w -v "$IN/out/modules":/kmods:ro -v "$IN/out":/kout:ro -v "$IN/tools/logdw/logdw":/logdw:ro \
      -v "$IN/tools/bt-init/mu300-bt-init":/bt-init:ro -v "$IN/tools/keys/mu300-keys":/keys:ro -v "$IN/tools/atd-io/mu300-atd-io":/atd-io:ro $cltest \
      -e MU300_VERSION="$TAG" mu300-ubuntu:$u bash /w/assemble.sh >/dev/null
    out=mu300-ubuntu-rootfs.tar.gz; [ $u = 24.04 ] || out=mu300-ubuntu-$u-rootfs.tar.gz
    mv "$B/mu300-ubuntu-$u-rootfs.tar.gz" "$D/$out"; rm -rf "$B"
done

echo "==> OpenWrt root filesystem (generic)"
MU300_INPUTS="$IN" MU300_LUCI_PLUGIN_SRC="$TOP/openwrt/luci-app-mu300" MU300_VERSION="$TAG" \
    sh "$TOP/openwrt/build-rootfs.sh" mu300-openwrt-release.tar.gz >/dev/null
mv "$TOP/openwrt/mu300-openwrt-release.tar.gz" "$D/mu300-openwrt-rootfs.tar.gz"
for f in ./opt/mu300/bin/mu300-at ./opt/mu300/bin/mobile-data \
    ./opt/mu300/bin/mu300-data-sim-u30 ./opt/mu300/bin/mu300-sim-u30 \
    ./opt/mu300/bin/mu300-user-packages ./etc/init.d/mu300-user-packages ./etc/mu300/image-packages \
    ./opt/mu300/lib/wifi-channels.sh ./usr/share/ucode/wifi/hostapd.uc ./opt/mu300/bin/v50-led-status ./opt/mu300/bin/mu300-platform-config ./opt/mu300/bin/mu300-sim-env ./opt/mu300/bin/mu300-data-sim ./opt/mu300/bin/mu300-dual-radio ./usr/share/luci/menu.d/luci-app-mu300.json ./www/luci-static/resources/view/mu300/home.js \
    ./lib/netifd/hostapd-unisoc.sh ./usr/share/ucode/wifi/unisoc-sae.uc \
    ./etc/mu300/wifi-sae.build-id \
    ./opt/mu300/bin/mu300-smsd ./opt/mu300/bin/mu300-sms ./etc/init.d/mu300-smsd \
    ./www/luci-static/resources/mu300/refresh.js ./www/luci-static/resources/view/mu300/sms.css \
    ./www/luci-static/resources/view/mu300/settings.js ./www/luci-static/resources/mu300/product-form.js \
    ./usr/libexec/unisoc-modem/product-form ./usr/libexec/rpcd/unisoc-product-form \
    ./usr/lib/lpac ./usr/share/unisoc-modem/lpac.json ./usr/libexec/unisoc-modem/euicc ./usr/libexec/unisoc-modem/euicc-screen ./usr/libexec/unisoc-modem/euicc-progress.py ./usr/libexec/rpcd/unisoc-euicc ./usr/share/licenses/unisoc-euicc/e5-esim-MIT \
    ./usr/share/rpcd/acl.d/unisoc-euicc.json ./usr/share/luci/menu.d/unisoc-euicc.json \
    ./www/luci-static/resources/view/mu300/euicc.js ./www/luci-static/resources/mu300/vendor/jsQR.js \
    ./usr/share/rpcd/acl.d/unisoc-product-form.json ./etc/init.d/mu300-vendor ./opt/mu300/lib/v50-card.sh \
    ./usr/libexec/unisoc-modem/dashboard-rates ./usr/libexec/unisoc-modem/dashboard-network.sh ./usr/share/ucode/mu300/dash.uc ./usr/share/rpcd/ucode/mu300status.uc ./opt/mu300/bin/mu300-service-cpus ./usr/share/ucode/mu300/service-cpus.uc ./opt/mu300/bin/wifi-ready ./usr/libexec/unisoc-modem/operator.uc \
    ./usr/libexec/unisoc-modem/refresh-config \
    ./usr/libexec/unisoc-modem/lock ./usr/libexec/unisoc-modem/boot-replay ./usr/libexec/unisoc-modem/sim-scope.sh \
    ./usr/libexec/unisoc-modem/lock-apply.sh ./usr/libexec/unisoc-modem/lock-jobs.sh \
    ./usr/libexec/unisoc-modem/traffic ./usr/libexec/unisoc-modem/traffic-core.uc \
    ./usr/libexec/unisoc-modem/hotspot ./usr/libexec/unisoc-modem/hotspot.uc \
    ./sbin/block ./lib/libblkid-tiny.so ./etc/init.d/fstab ./etc/mu300/block-mount.build-id \
    ./usr/libexec/unisoc-modem/apn ./usr/libexec/unisoc-modem/apn.uc ./usr/libexec/unisoc-modem/apn-identity ./etc/config/unisoc_apn \
    ./www/luci-static/resources/view/mu300/apn.js \
    ./www/luci-static/resources/view/mu300/traffic.js ./etc/init.d/unisoc-traffic \
    ./www/luci-static/resources/view/mu300/cpu.js ./etc/init.d/unisoc-cpu \
    ./www/luci-static/resources/mu300/auto-mode.js \
    ./www/luci-static/resources/mu300/cpu-idle.js ./etc/init.d/unisoc-cpu-idle \
    ./usr/libexec/rpcd/unisoc-cpu-idle ./usr/libexec/unisoc-modem/cpu-idle \
    ./usr/libexec/unisoc-modem/cpu-idle.uc ./etc/rc.d/S99unisoc-cpu-idle \
    ./www/luci-static/resources/mu300/cpu-cores.js ./etc/init.d/unisoc-cpu-cores \
    ./usr/libexec/rpcd/unisoc-cpu-cores ./usr/libexec/unisoc-modem/cpu-cores \
    ./usr/libexec/unisoc-modem/cpu-cores.uc ./etc/rc.d/S99unisoc-cpu-cores \
    ./usr/libexec/unisoc-modem/cpu ./usr/libexec/unisoc-modem/cpu.uc \
    ./usr/libexec/unisoc-modem/cpu-thermal.uc \
    ./usr/libexec/unisoc-modem/cpu-voltage ./usr/libexec/unisoc-modem/cpu-voltage.uc \
    ./opt/mu300/bin/cpu-voltage-platform ./opt/mu300/bin/sml-voltage-check ./opt/mu300/bin/modem-loader-patch ./etc/init.d/mu300-cpu-driver \
    ./etc/rc.d/S08mu300-cpu-driver ./etc/rc.d/K89unisoc-cpu \
    ./usr/libexec/unisoc-modem/call-notify ./usr/libexec/unisoc-modem/call-notify-events.uc ./usr/libexec/unisoc-modem/sms-forward ./usr/libexec/unisoc-modem/power-supply.sh ./usr/libexec/unisoc-modem/forward-template.uc \
    ./www/luci-static/resources/view/mu300/forward.js ./etc/init.d/unisoc-sms-forward \
    ./usr/libexec/unisoc-modem/data-sim ./www/luci-static/resources/view/mu300/sim.js \
    ./www/luci-static/resources/view/mu300/device.js ./usr/libexec/unisoc-modem/device-usb \
    ./etc/hotplug.d/net/90-unisoc-usb-host ./etc/hotplug.d/iface/90-unisoc-usb-host \
    ./opt/mu300/bin/mu300-usb-pd-sink ./etc/hotplug.d/typec/50-mu300-usb-pd-sink \
    ./etc/init.d/unisoc-modem-ui ./usr/lib/lua/luci/i18n/mu300.en.lmo ./usr/lib/lua/luci/i18n/mu300.tr.lmo \
    ./usr/lib/lua/luci/i18n/base.zh-cn.lmo ./usr/lib/lua/luci/i18n/firewall.zh-cn.lmo ./usr/lib/lua/luci/i18n/package-manager.zh-cn.lmo \
    ./usr/libexec/unisoc-modem/network-performance ./usr/libexec/unisoc-modem/network-performance.uc \
    ./usr/libexec/rpcd/unisoc-network-performance ./etc/init.d/unisoc-network-performance ./etc/rc.d/S19unisoc-network-performance \
    ./www/luci-static/resources/mu300/network-performance.js ./usr/share/rpcd/acl.d/unisoc-network-performance.json; do
    tar -tzf "$D/mu300-openwrt-rootfs.tar.gz" "$f" >/dev/null || {
        echo "release OpenWrt rootfs missing $f" >&2; exit 1;
    }
done

echo "==> audit"
fail=0
for a in mu300-kernel mu300-kernel-6.18 mu300-kernel-7.2 mu300-ubuntu-rootfs mu300-ubuntu-26.04-rootfs mu300-openwrt-rootfs; do
    bad=$(tar -tzf "$D/$a.tar.gz" | sed 's|^\./||' | grep -E \
        -e '(^|/)lib/firmware/(wcnmodem|gnssmodem|wifi_board_config|bt_configure)' \
        -e '^opt/mu300/android/.+' -e '__properties__|dev-properties' \
        -e '(^|/)(libmali|libOpenCL|libGLES|libEGL|libvulkan)[^/]*\.so' \
        -e '^etc/uhttpd\.key$' -e '^etc/ssh/ssh_host_' -e '^etc/mu300/(hotspot|toolkit)\.conf$' -e '^etc/dropbear/dropbear_.*_host_key' \
        | grep -vE '^opt/mu300/android/system/?$|^opt/mu300/android/system/bin/?$|^opt/mu300/android/system/bin/cltest$' || true)
    if [ -n "$bad" ]; then echo "$a contains files that must not be published:"; echo "$bad" | head -20; fail=1; fi
    mid=$(tar -xzOf "$D/$a.tar.gz" ./etc/machine-id 2>/dev/null || true)
    [ -z "$mid" ] || { echo "$a has a machine-id"; fail=1; }
done
[ $fail = 0 ] || { echo "audit failed, nothing published" >&2; exit 1; }
rm -rf "$IN"
# the updater itself: an older mu300-update fetches this one and continues with it
cp "$TOP/rootfs/overlay/opt/mu300/bin/mu300-update" "$D/mu300-update"
(cd "$D" && { shasum -a 256 *.tar.gz mu300-update 2>/dev/null || sha256sum *.tar.gz mu300-update; } > SHA256SUMS)
ls -la "$D"

[ "$PUBLISH" = --publish ] || { echo "built release/$TAG (run again with --publish to upload)"; exit 0; }
commit=$(git -C "$TOP" rev-parse HEAD)
kernel_rev=$(sed -n 's/^KERNEL_REV=//p' "$TOP/kernel/build-all.sh")
modules_rev=$(sed -n 's/^MODULES_REV=//p' "$TOP/kernel/build-all.sh")
notes=$(mktemp)
cat > "$notes" <<EOF
Prebuilt images for \`./install.sh\` (ZTE F50 / MU300 and ZTE U30 Air; the installer recognises which). Check your device
first with \`./install.sh --check\`.

| file | contents |
|---|---|
| mu300-kernel.tar.gz | Linux 5.4.254 \`Image\` and modules (with the U30 Air's own in \`modules-u30air/\`), static busybox and logdw for the boot image, and the generic boot ramdisk segment \`mu300-update\` uses |
| mu300-kernel-6.18.tar.gz | mainline Linux 6.18 (longterm) for \`mu300-update kernel 6.18\`: \`Image\`, modules, generic boot ramdisk segment |
| mu300-kernel-7.2.tar.gz | mainline Linux 7.2 (newest stable) for \`mu300-update kernel 7.2\`: the same parts |
| mu300-ubuntu-rootfs.tar.gz | Ubuntu 24.04 LTS root filesystem |
| mu300-ubuntu-26.04-rootfs.tar.gz | Ubuntu 26.04 LTS root filesystem |
| mu300-openwrt-rootfs.tar.gz | OpenWrt 25.12.5 root filesystem |
| mu300-update | the on-device updater of this release (\`mu300-update apply\` switches to it before it changes anything) |

The images contain **no proprietary files**: the installer pulls the Wi-Fi/Bluetooth firmware and the Android
modem/GPU userspace from your own device and adds them during installation.

Built from $REPO@$commit with \`kernel/build-all.sh\` and \`tools/make-release.sh\`.
Corresponding source (GPL): kernel https://github.com/Enceka/android_kernel_zte_ums9620_mifi_u30air/tree/$kernel_rev ,
Wi-Fi/Bluetooth/Mali modules https://github.com/realme-kernel-opensource/realme_C51_C53_Narzo-N53-AndroidT-kernel-source/tree/$modules_rev ,
patches in \`kernel/patches\`. Ubuntu, OpenWrt and busybox packages come from their distributions' archives.
EOF
if gh release view "$TAG" -R "$REPO" >/dev/null 2>&1; then
    gh release upload "$TAG" -R "$REPO" --clobber "$D"/*.tar.gz "$D/mu300-update" "$D/SHA256SUMS"
else
    gh release create "$TAG" -R "$REPO" --target "$commit" --title "MU300 Linux $TAG" --notes-file "$notes" \
      "$D"/*.tar.gz "$D/mu300-update" "$D/SHA256SUMS"
fi
rm -f "$notes"
