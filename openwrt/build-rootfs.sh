#!/bin/sh
# Build the MU300 OpenWrt (or ImmortalWrt) rootfs tarball (runs on the host; needs Docker with arm64 support).
#   openwrt/build-rootfs.sh OUT.tar.gz
#   MU300_FLAVOUR=immortalwrt openwrt/build-rootfs.sh OUT.tar.gz
# Inputs (same as rootfs/assemble.sh, all optional except modules):
#   out/modules/*.ko  out/modules.builtin*  firmware/  android-subset/  android-gpu-subset/
#   tools/logdw/logdw  tools/bt-init/mu300-bt-init  tools/keys/mu300-keys  tools/gpu/cltest  busybox (static, full)
# VPN/proxy engines are never preinstalled, even when present in MU300_INPUTS.
#   MU300_MAINLINE_OUT/modules/*.ko (optional: out-of-tree WCN modules for the selected mainline kernel)
#   The standalone LuCI plugin in openwrt/luci-app-mu300 is included by default.
set -eu
FLAVOUR=${MU300_FLAVOUR:-openwrt}
case $FLAVOUR in
    openwrt)     VER=${MU300_WRT_VER:-25.12.5}; BASEURL=https://downloads.openwrt.org/releases ;;
    # ImmortalWrt is an OpenWrt fork: same package manager, same layout, more drivers and LuCI apps
    immortalwrt) VER=${MU300_WRT_VER:-25.12.2}; BASEURL=https://downloads.immortalwrt.org/releases ;;
    *) echo "unknown flavour '$FLAVOUR' (openwrt or immortalwrt)" >&2; exit 1 ;;
esac
KREL=5.4.254-gb50db5b6224c
OUT=${1:-mu300-$FLAVOUR-$VER-rootfs.tar.gz}
TOP=$(cd "$(dirname "$0")/.." && pwd)
ROOTFS_DIR=${MU300_ROOTFS_OUT_DIR:-$TOP/openwrt}
mkdir -p "$ROOTFS_DIR"
ROOTFS_DIR=$(cd "$ROOTFS_DIR" && pwd)
# build inputs (out/, firmware/, android-subset/, tools binaries, busybox) may live outside the checkout
IN=${MU300_INPUTS:-$TOP}
MAINLINE=${MU300_MAINLINE_OUT:-$IN/upstream/out}
if [ -d "$MAINLINE/modules" ]; then
    python3 "$TOP/upstream/check-container-support.py" "$MAINLINE"
fi
for f in mu300cell.sh mu300cell-v6.sh; do
    [ -s "$TOP/openwrt/overlay/lib/netifd/proto/$f" ] || {
        echo "required cellular protocol helper missing: $f" >&2; exit 1;
    }
done
PLUGIN=${MU300_LUCI_PLUGIN_SRC:-$TOP/openwrt/luci-app-mu300}
if [ -n "$PLUGIN" ]; then
    PLUGIN=$(cd "$PLUGIN" && pwd)
    for f in Makefile root/usr/libexec/unisoc-modem/lock htdocs/luci-static/resources/view/mu300/home.js lmo/mu300.en.lmo lmo/mu300.tr.lmo; do
        [ -s "$PLUGIN/$f" ] || { echo "incomplete LuCI plugin: $PLUGIN/$f" >&2; exit 1; }
    done
fi
# Keep the upstream Aurora theme reproducible. Its APK is installed while
# assembling the rootfs, so the theme is present on first boot without a
# network-dependent uci-defaults install step. Bootstrap remains available.
THEME_APK=${MU300_LUCI_THEME_APK:-$TOP/work/luci-theme-aurora-1.4.0-r20260920.apk}
THEME_SHA=${MU300_LUCI_THEME_SHA256:-05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e}
if [ ! -s "$THEME_APK" ]; then
    [ -z "${MU300_LUCI_THEME_APK:-}" ] || {
        echo "theme package missing: $THEME_APK" >&2; exit 1;
    }
    mkdir -p "$TOP/work"
    curl -fL -o "$THEME_APK.part" \
      https://github.com/eamonxg/luci-theme-aurora/releases/download/v1.4.0/luci-theme-aurora-1.4.0-r20260920.apk
    mv "$THEME_APK.part" "$THEME_APK"
fi
theme_hash=$(sha256sum "$THEME_APK" | cut -d' ' -f1)
[ "$theme_hash" = "$THEME_SHA" ] || {
    echo "Aurora APK checksum mismatch: $THEME_APK" >&2; exit 1;
}
# Install the matching official configuration UI and translations at build
# time, not from a first-boot download. Unique temporary names allow concurrent
# 6.x/7.x builds to share this verified download cache safely.
AURORA_CONFIG_BASE=https://github.com/eamonxg/luci-app-aurora-config/releases/download/v1.2.5
mkdir -p "$TOP/work"
while read -r expected package; do
    [ -n "$package" ] || continue
    cached="$TOP/work/$package"
    if [ ! -s "$cached" ]; then
        partial=$(mktemp "$cached.part.XXXXXX")
        if ! curl -fL -o "$partial" "$AURORA_CONFIG_BASE/$package"; then
            rm -f "$partial"; exit 1
        fi
        actual=$(sha256sum "$partial" | cut -d' ' -f1)
        [ "$actual" = "$expected" ] || {
            rm -f "$partial"; echo "Aurora config APK checksum mismatch: $package" >&2; exit 1;
        }
        mv "$partial" "$cached"
    fi
    actual=$(sha256sum "$cached" | cut -d' ' -f1)
    [ "$actual" = "$expected" ] || {
        echo "Aurora config APK checksum mismatch: $package" >&2; exit 1;
    }
done < "$TOP/openwrt/aurora-config-packages.sha256"
TARBALL=$FLAVOUR-$VER-armsr-armv8-rootfs.tar.gz
URL=$BASEURL/$VER/targets/armsr/armv8

# Stock block-mount probes /dev/sd* (including Unisoc diagnostic channels).
# Build/install the patched APK before exposing LuCI's Mount Points page.
BLOCK_APK=$TOP/work/block-mount-mu300.apk
sh "$TOP/openwrt/build-block-mount.sh" "$BLOCK_APK"
WIFI_APKS="$TOP/work/wifi-sae"
UNISOC_WRT_VER="$VER" sh "$TOP/openwrt/build-hostapd.sh" "$WIFI_APKS"

cd "$TOP"
if [ ! -f "openwrt/$TARBALL" ]; then
    curl -fL -o "openwrt/$TARBALL" "$URL/$TARBALL"
fi
want=$(curl -fsL "$URL/sha256sums" | sed -n "s/^\([0-9a-f]*\) \*$TARBALL$/\1/p")
have=$(shasum -a 256 "openwrt/$TARBALL" 2>/dev/null || sha256sum "openwrt/$TARBALL")
[ "${have%% *}" = "$want" ] || { echo "checksum mismatch for $TARBALL" >&2; exit 1; }
# Import each upstream rootfs once. Re-importing identical bytes creates another
# dangling image on every packaging run, even though the base has not changed.
base_image=mu300-$FLAVOUR-base:$VER
base_digest=$(docker image inspect --format '{{ index .Config.Labels "org.openwrt.rootfs.sha256" }}' "$base_image" 2>/dev/null || true)
if [ "$base_digest" != "$want" ]; then
    docker import --platform linux/arm64 --change "LABEL org.openwrt.rootfs.sha256=$want" \
        "openwrt/$TARBALL" "$base_image" >/dev/null
fi
# OpenWrt ships an unsigned regulatory.db; this kernel requires the signed database (wens key), so take Debian/Ubuntu's
REGDB=$(mktemp -d)
docker run --rm --platform linux/arm64 -v "$REGDB":/o ubuntu:26.04 sh -c \
  "apt-get update -qq >/dev/null && apt-get install -y -qq wireless-regdb >/dev/null && cp /usr/lib/firmware/regulatory.db /usr/lib/firmware/regulatory.db.p7s /o/"

opt() { [ -e "$IN/$1" ] && echo "-v $IN/$1:/in/$2:ro" || true; }
mainline_opt() { [ -e "$MAINLINE/$1" ] && echo "-v $MAINLINE/$1:/in/$2:ro" || true; }
plugin_opt() { [ -n "$PLUGIN" ] && echo "-v $PLUGIN:/in/luci-plugin:ro" || true; }

# The required input first, with a readable message: without it docker fails somewhere inside the build.
ls "$IN/out/modules"/*.ko >/dev/null 2>&1 || {
    echo "no kernel modules in $IN/out/modules - build them first (kernel/build-linux.sh), or point" >&2
    echo "MU300_INPUTS at the directory that has out/modules, firmware/ and android-subset/." >&2
    exit 1
}
# The optional ones decide whether the image can use the modem, Wi-Fi, the GPU or the VPN at all. Missing ones
# used to be skipped silently, which produces an image that boots and then does nothing useful.
for o in firmware android-subset android-gpu-subset tools/logdw/logdw tools/bt-init/mu300-bt-init tools/keys/mu300-keys tools/atd-io/mu300-atd-io tools/gpu/cltest busybox upstream/out/modules; do
    [ -e "$IN/$o" ] && echo "  + $o" || echo "  - $o   (missing: the image is built without it)"
done
# shellcheck disable=SC2046
docker run --rm --platform linux/arm64 \
  -v "${MU300_RELEASE_JSON:-$TOP/work}":/in/release.json:ro \
  -v "$TOP/rootfs/overlay/opt/mu300":/in/opt-mu300:ro -v "$TOP/rootfs/overlay/usr/share/ucode/mu300":/in/ucode-mu300:ro -v "$TOP/openwrt/overlay":/in/overlay:ro \
  -v "$TOP/openwrt/patches/fw4-sipa-offload.patch":/in/fw4-sipa-offload.patch:ro \
  -v "$TOP/openwrt/patches/wifi-scripts-no-dfs.patch":/in/wifi-scripts-no-dfs.patch:ro \
  -v "$TOP/boot/module-order.txt":/in/module-order.txt:ro -v "$IN/out/modules":/in/modules:ro \
  $(opt out/modules.builtin modules.builtin) $(opt out/modules.builtin.modinfo modules.builtin.modinfo) \
  $(opt firmware firmware) $(opt android-subset android-subset) $(opt android-gpu-subset android-gpu-subset) \
  $(opt tools/logdw/logdw logdw) $(opt tools/bt-init/mu300-bt-init bt-init) $(opt tools/keys/mu300-keys keys) $(opt tools/atd-io/mu300-atd-io atd-io) $(opt tools/gpu/cltest cltest) \
  $(opt busybox busybox) $(mainline_opt modules mainline-modules) $(plugin_opt) -v "$ROOTFS_DIR":/out -v "$REGDB":/in/regdb:ro \
  $(mainline_opt modules.builtin mainline-modules.builtin) $(mainline_opt modules.builtin.modinfo mainline-modules.builtin.modinfo) \
  -v "$THEME_APK":/in/luci-theme-aurora.apk:ro \
  -v "$BLOCK_APK":/in/block-mount-mu300.apk:ro \
  -v "$BLOCK_APK.build-id":/in/block-mount.build-id:ro \
  -v "$WIFI_APKS":/in/wifi-sae:ro \
  -v "$TOP/openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/disable-stock-upgrade":/in/stock-upgrade-policy:ro \
  -v "$TOP/installer":/in/ota-source/installer:ro \
  -v "$TOP/work/luci-app-aurora-config-1.2.5-r20260920.apk":/in/luci-app-aurora-config.apk:ro \
  -v "$TOP/work/luci-i18n-aurora-config-zh-cn-26.262.58276.362887a.apk":/in/aurora-config-zh-cn.apk:ro \
  -v "$TOP/work/luci-i18n-aurora-config-tr-26.262.58276.362887a.apk":/in/aurora-config-tr.apk:ro \
  -e KREL=$KREL -e OUT="$(basename "$OUT")" -e MU300_VERSION="${MU300_VERSION:-dev}" mu300-$FLAVOUR-base:$VER /bin/sh -eu -c '
mkdir -p /var/lock /var/run /tmp
apk_retry() {
    apk_attempt=1
    while ! apk "$@"; do
        [ "$apk_attempt" -lt 3 ] || return 1
        apk_attempt=$((apk_attempt + 1))
        echo "Retrying apk download (attempt $apk_attempt/3)" >&2
    done
}
apk_retry update >/dev/null
# openssl-util: general TLS certificate inspection and maintenance tools;
# i2c-tools, gpiod-tools: mu300-usb (the charger of the U30 Air) and mu300-nfc (its NFC tag)
apk_retry add --allow-untrusted /in/wifi-sae/*.apk >/dev/null
# The cipher capability helper needs the section omitted by reduced iw builds.
apk_retry add iwinfo wireless-regdb iw-full bash ip-full coreutils-stty openssl-util curl ca-bundle msmtp luci-base \
    luci-i18n-base-zh-cn luci-i18n-firewall-zh-cn luci-i18n-package-manager-zh-cn \
    rpcd-mod-ucode ucode ucode-mod-fs ucode-mod-ubus ucode-mod-uci ucode-mod-uloop ucode-mod-nl80211 ucode-mod-rtnl ucode-mod-socket \
    i2c-tools gpiod-tools flock ttyd luci-app-ttyd luci-i18n-ttyd-zh-cn luci-i18n-ttyd-tr python3 >/dev/null
apk_retry add --allow-untrusted /in/block-mount-mu300.apk >/dev/null
apk_retry add --allow-untrusted /in/luci-theme-aurora.apk >/dev/null
apk_retry add --allow-untrusted /in/luci-app-aurora-config.apk /in/aurora-config-zh-cn.apk /in/aurora-config-tr.apk >/dev/null
# ujail drops CAP_PERFMON (38), which this 5.4 kernel does not know: jailed services (dnsmasq, ntpd) crash-loop
apk del procd-ujail procd-seccomp >/dev/null 2>&1 || true
# online firmware upgrades flash whole-disk armsr images: that would overwrite the eMMC, so remove them
apk del luci-app-attendedsysupgrade attendedsysupgrade-common owut >/dev/null 2>&1 || true
R=/build/root; mkdir -p $R
# copy the live filesystem of this container (the OpenWrt rootfs plus packages), without runtime mounts
for e in /*; do
    case "$e" in /proc|/sys|/dev|/build|/in|/out|/tmp) continue ;; esac
    cp -a "$e" $R/
done
mkdir -p $R/proc $R/sys $R/dev $R/tmp $R/run $R/opt
# Docker bind-mounts these three into the container, so the copy above picks up the build host versions:
# a resolv.conf pointing at the internal Docker DNS (which broke every lookup the device itself made), a hosts
# file with the container id, and a hostname that was the container id. Put the OpenWrt ones back.
# (no apostrophes in here: this whole block is one single-quoted argument to sh -c)
ln -sf /tmp/resolv.conf $R/etc/resolv.conf
printf "127.0.0.1\tlocalhost\n\n::1\tlocalhost ip6-localhost ip6-loopback\nff02::1\tip6-allnodes\nff02::2\tip6-allrouters\n" > $R/etc/hosts
printf "mu300\n" > $R/etc/hostname   # the real one comes from uci (etc/uci-defaults/90-mu300)
cp -a /in/opt-mu300 $R/opt/mu300
cp -a /in/overlay/. $R/
# Do not retain the retired call helpers from an older base image.
rm -f $R/opt/mu300/bin/mu300-voice $R/opt/mu300/bin/mu300-audio-dsp
mkdir -p $R/usr/share/ucode/mu300
cp -a /in/ucode-mu300/. $R/usr/share/ucode/mu300/
chmod 0755 $R/etc/uci-defaults/95-disable-ap-uapsd
chmod 0755 $R/etc/init.d/unisoc-battery $R/etc/hotplug.d/power_supply/80-unisoc-battery
mkdir -p $R/etc/mu300
cp /in/block-mount.build-id $R/etc/mu300/block-mount.build-id
cp /in/wifi-sae/build-id $R/etc/mu300/wifi-sae.build-id
cp /in/wifi-sae/source-info $R/etc/mu300/wifi-sae.source-info
for f in sbin/block lib/libblkid-tiny.so etc/init.d/fstab; do
    [ -s "$R/$f" ] || { echo "patched block-mount required file missing: $f" >&2; exit 1; }
done
chmod 0755 $R/opt/mu300/bin/cpu-voltage-platform $R/etc/init.d/mu300-cpu-driver
chmod 0755 $R/opt/mu300/bin/mu300-lowlatency $R/opt/mu300/bin/wifi-ready $R/opt/mu300/bin/mu300-service-cpus $R/etc/init.d/mu300-service-cpus
chmod 0755 $R/etc/init.d/mu300-sysd
chmod 0755 $R/opt/mu300/bin/mu300-modemd $R/opt/mu300/bin/mu300-sysd
for helper in mu300-data-sim-u30 mu300-sim-u30 u30air-led-status; do
    [ -s "$R/opt/mu300/bin/$helper" ] || { echo "missing U30 helper: $helper" >&2; exit 1; }
    chmod 0755 "$R/opt/mu300/bin/$helper"
done
chroot "$R" /bin/sh -c "command -v flock >/dev/null" || { echo "missing native SIM flock dependency" >&2; exit 1; }
[ -s "$R/opt/mu300/lib/sms-pool.sh" ] || { echo "missing shared SMS pool library" >&2; exit 1; }
chmod 0755 $R/etc/hotplug.d/power_supply/70-mu300-led
chmod 0755 $R/opt/mu300/bin/mu300-platform-config
chmod 0755 $R/opt/mu300/bin/mu300-network-recovery $R/etc/init.d/mu300-network-recovery
chmod 0755 $R/etc/uci-defaults/92-mu300-uhttpd-key
chmod 0755 $R/opt/mu300/bin/v50-led-status
chmod 0755 $R/opt/mu300/bin/mu300-v50-sim
chmod 0755 $R/opt/mu300/bin/mu300-charge-mode $R/opt/mu300/bin/mu300-modem-coldboot $R/etc/init.d/mu300-charge-mode
chmod 0755 $R/opt/mu300/bin/openwrt-board-check
chmod 0755 $R/opt/mu300/bin/ra-deprecate $R/opt/mu300/bin/wcn-reopen
chmod 0755 $R/opt/mu300/bin/mu300-user-packages $R/etc/init.d/mu300-user-packages
[ -s $R/www/luci-static/aurora/main.css ] || { echo "Aurora theme assets missing" >&2; exit 1; }
for f in www/luci-static/resources/view/aurora/studio.js \
    www/luci-static/resources/view/aurora/marketplace.js \
    usr/share/luci/menu.d/luci-app-aurora.json usr/share/rpcd/acl.d/luci-app-aurora.json \
    usr/lib/lua/luci/i18n/aurora-config.zh-cn.lmo usr/lib/lua/luci/i18n/aurora-config.tr.lmo; do
    [ -s "$R/$f" ] || { echo "Aurora config required file missing: $f" >&2; exit 1; }
done
grep -q "mediaurlbase .*/luci-static/aurora" $R/etc/config/luci || {
    echo "Aurora theme is not the LuCI default" >&2; exit 1;
}
# mu300cell reports sipa_eth0 as l3_device only. fw4 otherwise omits it from
# its software flowtable, leaving cellular downlink on the slow forwarding path.
# Fail the build if a changed fw4 version no longer matches this targeted patch.
apk_retry add patch >/dev/null
patch --batch --fuzz=0 -d $R -p1 -i /in/fw4-sipa-offload.patch
patch --batch --fuzz=0 -d $R -p1 -i /in/wifi-scripts-no-dfs.patch
grep -q "device_no_dfs(config);" $R/usr/share/ucode/wifi/hostapd.uc || { echo "wifi-scripts DFS patch not applied" >&2; exit 1; }
if [ -d /in/luci-plugin ]; then
    cp -a /in/luci-plugin/root/. $R/
    cp -a /in/luci-plugin/htdocs/. $R/www/
    chmod 0600 $R/etc/config/mu300-charge
    chmod 0755 $R/etc/init.d/mu300-charge $R/etc/hotplug.d/power_supply/75-mu300-charge $R/usr/libexec/rpcd/mu300-charge
    ucode $R/usr/libexec/unisoc-modem/disable-channel-analysis $R
    ucode $R/usr/libexec/unisoc-modem/filter-wifi-ciphers $R
    chmod 0755 $R/etc/init.d/unisoc-cpu $R/etc/init.d/unisoc-cpu-cores $R/usr/libexec/rpcd/unisoc-cpu-cores
    chmod 0755 $R/etc/init.d/unisoc-cpu-idle $R/usr/libexec/rpcd/unisoc-cpu-idle
    chmod 0755 $R/etc/init.d/unisoc-network-performance $R/usr/libexec/rpcd/unisoc-network-performance
    chmod 0600 $R/etc/config/unisoc_apn
    chmod 0755 $R/etc/init.d/unisoc-ttl $R/etc/hotplug.d/iface/92-unisoc-ttl $R/etc/uci-defaults/98-unisoc-ttl
    chmod 0755 $R/etc/uci-defaults/97-unisoc-upgrade-policy
    chmod 0755 $R/etc/init.d/unisoc-modem-ui $R/etc/init.d/unisoc-sms-forward $R/etc/init.d/unisoc-traffic $R/etc/hotplug.d/net/90-unisoc-usb-host $R/etc/hotplug.d/iface/90-unisoc-usb-host $R/usr/libexec/rpcd/mu300dash $R/usr/libexec/unisoc-modem/*
    # rpcd refuses world-writable plugins from a Windows bind mount.
    chmod 0644 $R/usr/share/rpcd/ucode/*.uc $R/usr/share/ucode/mu300/*.uc
    chroot "$R" /usr/bin/ucode -e "import * as dash from \"mu300.dash\"; import * as rates from \"mu300.rates\"; import * as placement from \"mu300.service-cpus\";"
    mkdir -p $R/usr/lib/lua/luci/i18n
    cp -a /in/luci-plugin/lmo/. $R/usr/lib/lua/luci/i18n/
    # Official Chinese language packages register zh_cn. The dashboard uses
    # Chinese source strings; source-tree staging still needs Turkish registration.
    uci -c $R/etc/config -q get luci.languages.tr >/dev/null || uci -c $R/etc/config set luci.languages.tr="Türkçe"
    uci -c $R/etc/config commit luci
fi
mv $R/sbin/sysupgrade $R/sbin/sysupgrade.openwrt && mv $R/usr/libexec/mu300-sysupgrade $R/sbin/sysupgrade
M=$R/lib/modules/$KREL; mkdir -p $M
cp /in/modules/*.ko $M/          # ubox kmodloader expects the modules flat in /lib/modules/<release>/
for f in modules.builtin modules.builtin.modinfo; do [ -e /in/$f ] && cp /in/$f $M/; done
[ -d /in/firmware ] && { mkdir -p $R/lib/firmware; cp -a /in/firmware/. $R/lib/firmware/; }
# tools shared with the Ubuntu image look under /usr/lib/firmware (mu300-bt-init, for one); OpenWrt keeps
# firmware in /lib/firmware
mkdir -p $R/usr/lib && ln -sfn ../../lib/firmware $R/usr/lib/firmware
cp /in/regdb/regulatory.db /in/regdb/regulatory.db.p7s $R/lib/firmware/
if [ -d /in/android-subset ]; then
    mkdir -p $R/opt/mu300/android && cp -a /in/android-subset/. $R/opt/mu300/android/
    mv $R/opt/mu300/android/dev/__properties__ $R/opt/mu300/android/dev-properties && rmdir $R/opt/mu300/android/dev
fi
[ -d /in/android-gpu-subset ] && cp -a /in/android-gpu-subset/. $R/opt/mu300/android/
[ -e /in/cltest ] && { mkdir -p $R/opt/mu300/android/system/bin; cp /in/cltest $R/opt/mu300/android/system/bin/cltest; chmod 755 $R/opt/mu300/android/system/bin/cltest; }
[ -e /in/logdw ] && { cp /in/logdw $R/opt/mu300/bin/logdw; chmod 755 $R/opt/mu300/bin/logdw; }
[ -e /in/bt-init ] && { cp /in/bt-init $R/opt/mu300/bin/mu300-bt-init; chmod 755 $R/opt/mu300/bin/mu300-bt-init; }
[ -e /in/keys ] && { cp /in/keys $R/opt/mu300/bin/mu300-keys; chmod 755 $R/opt/mu300/bin/mu300-keys; }
[ -e /in/atd-io ] && { cp /in/atd-io $R/opt/mu300/bin/mu300-atd-io; chmod 755 $R/opt/mu300/bin/mu300-atd-io; }
# full static busybox for the tools OpenWrt busybox leaves out (od, timeout, mountpoint, losetup, rfkill, telnetd)
if [ -e /in/busybox ]; then
    cp /in/busybox $R/opt/mu300/bin/busybox; chmod 755 $R/opt/mu300/bin/busybox
    mkdir -p $R/opt/mu300/busybox-bin
    for a in od timeout losetup telnetd getty; do
        chroot $R /bin/sh -c "command -v $a" >/dev/null 2>&1 && continue
        ln -sf ../bin/busybox $R/opt/mu300/busybox-bin/$a
    done
fi
mkdir -p $R/etc/mu300
printf "%s\n" "${MU300_VERSION:-dev}" > $R/etc/mu300/image-version
[ ! -f /in/release.json ] || cp /in/release.json $R/etc/mu300/release.json
# Record image packages so upgrades can restore only user additions.
(MU300_LIB=1; . $R/opt/mu300/bin/mu300-update; upk_manual $R openwrt) > $R/etc/mu300/image-packages
[ -s $R/etc/mu300/image-packages ] || { echo "missing image-packages" >&2; exit 1; }
# --- default-root-password begin ---
# PR #264: uhttpd creates a pair on the device, never in a shared image.
rm -f $R/etc/uhttpd.key $R/etc/uhttpd.crt
[ ! -e $R/etc/uhttpd.key ] || { echo "the image holds a TLS key" >&2; exit 1; }
# Fresh installs use root/root; account merging preserves passwords on updates.
root_hash="\$6\$mu300root\$O/wH3K.NhG6ZZOMSOTLlNNHv08B1RRcRsec0Xc54SuqhFhgPwmQUnxo782srY03f5mYk.Gv0qJ8JGfcrJvYKs."
grep -q "^root:" "$R/etc/shadow" || { echo "missing root account" >&2; exit 1; }
sed -i "s|^root:[^:]*:|root:$root_hash:|" "$R/etc/shadow"
unset root_hash
# --- default-root-password end ---
# enable the services (rc.common "enable" needs ubus, which is not running in the build container)
# accounts still those of the image until an installer or mu300-update puts the device ones in place
: > $R/etc/.mu300-accounts-from-image
for s in mu300-accounts mu300-cpu-driver mu300-service-cpus mu300-vendor mu300-charge-mode mu300-hw mu300-post mu300-toolkit mu300-atd mu300-network-recovery mu300-modem-log mu300-wifi-client mu300-sysd mu300-ndp mu300-smsd mu300-user-packages unisoc-battery ttyd; do
    n=$(sed -n "s/^START=//p" $R/etc/init.d/$s)
    ln -sf ../init.d/$s $R/etc/rc.d/S$n$s
done
ln -sf ../init.d/unisoc-battery $R/etc/rc.d/K08unisoc-battery
if [ -d /in/luci-plugin ]; then
    n=$(sed -n "s/^START=//p" $R/etc/init.d/unisoc-modem-ui)
    ln -sf ../init.d/unisoc-modem-ui $R/etc/rc.d/S$n"unisoc-modem-ui"
    n=$(sed -n "s/^START=//p" $R/etc/init.d/unisoc-sms-forward)
    ln -sf ../init.d/unisoc-sms-forward $R/etc/rc.d/S$n"unisoc-sms-forward"
    n=$(sed -n "s/^START=//p" $R/etc/init.d/unisoc-traffic)
    ln -sf ../init.d/unisoc-traffic $R/etc/rc.d/S$n"unisoc-traffic"
    ln -sf ../init.d/unisoc-ttl $R/etc/rc.d/S97unisoc-ttl
    ln -sf ../init.d/unisoc-traffic $R/etc/rc.d/K01unisoc-traffic
    ln -sf ../init.d/unisoc-cpu $R/etc/rc.d/S99unisoc-cpu
    ln -sf ../init.d/unisoc-cpu-cores $R/etc/rc.d/S99unisoc-cpu-cores
    ln -sf ../init.d/unisoc-cpu-idle $R/etc/rc.d/S99unisoc-cpu-idle
    ln -sf ../init.d/unisoc-network-performance $R/etc/rc.d/S19unisoc-network-performance
    ln -sf ../init.d/unisoc-cpu $R/etc/rc.d/K89unisoc-cpu
    ln -sf ../init.d/mu300-charge $R/etc/rc.d/S97mu300-charge
    ln -sf ../init.d/mu300-charge $R/etc/rc.d/K20mu300-charge
fi
# busybox PATH is /usr/sbin:/usr/bin:/sbin:/bin, so the commands go into /usr/bin
for c in mu300-toolkit mu300-next-boot mu300-os mu300-update mobile-data mu300-at mu300-sms led-status wifi-client mu300-ttl mu300-wifi-band mu300-led mu300-usb mu300-nfc mu300-user-packages; do ln -sf /opt/mu300/bin/$c $R/usr/bin/$c; done
# Config remnants of packages deliberately removed from our images must not return on upgrade.
rm -f $R/etc/config/attendedsysupgrade $R/etc/config/*.apk-new
# no kernel of its own: OpenWrt kmods (6.12) and grub are unused on this device
rm -rf $R/lib/modules/6.* $R/boot
# In-tree and vendor modules for the selected mainline kernel (upstream/).
if ls /in/mainline-modules/*.ko >/dev/null 2>&1; then
    # the release the modules were built for (their vermagic), not a version written down here
    krel=$(for f in /in/mainline-modules/*.ko; do tr "\0" "\n" < $f | sed -n "s/^vermagic=\([^ ]*\) .*/\1/p"; break; done)
    [ -n "$krel" ] && mkdir -p $R/lib/modules/$krel && cp /in/mainline-modules/*.ko $R/lib/modules/$krel/
    for f in modules.builtin modules.builtin.modinfo; do
        [ ! -f /in/mainline-$f ] || cp /in/mainline-$f $R/lib/modules/$krel/$f
    done
fi
mkdir -p $R/usr/libexec/unisoc-modem
cp /in/stock-upgrade-policy $R/usr/libexec/unisoc-modem/disable-stock-upgrade
chmod 0755 $R/usr/libexec/unisoc-modem/disable-stock-upgrade
ucode $R/usr/libexec/unisoc-modem/disable-stock-upgrade $R
python3 /in/ota-source/installer/stage-ota.py --root $R
cd $R && tar -czf /out/$OUT .
ls -la /out/$OUT'
# Remove optional VPN helpers and reject accidentally inherited VPN packages.
python3 "$TOP/openwrt/no-vpn-payload.py" "$ROOTFS_DIR/$(basename "$OUT")"
if [ -d "$MAINLINE/modules" ]; then
    python3 "$TOP/upstream/check-container-support.py" "$MAINLINE" --rootfs "$ROOTFS_DIR/$(basename "$OUT")"
fi
rm -rf "$REGDB"
