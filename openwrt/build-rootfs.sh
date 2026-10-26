#!/bin/sh
# Build the MU300 OpenWrt (or ImmortalWrt) rootfs tarball (runs on the host; needs Docker with arm64 support).
#   openwrt/build-rootfs.sh OUT.tar.gz
#   MU300_FLAVOUR=immortalwrt openwrt/build-rootfs.sh OUT.tar.gz
#   MU300_SYSTEM=openwrt-luci openwrt/build-rootfs.sh OUT.tar.gz   (OpenWrt with the MU300 control panel and Aurora)
# OUT is written into openwrt/ under its base name.
# Inputs (same as rootfs/assemble.sh, all optional except modules):
#   out/modules/*.ko  out/modules.builtin*  firmware/  android-subset/  android-gpu-subset/
#   tools/logdw/logdw  tools/bt-init/mu300-bt-init  tools/keys/mu300-keys  tools/gpu/cltest  busybox (static, full)
#   tools/carry/mu300-carry (static: an update carries everything of the user's over with it)
#   upstream/out/modules/*.ko (optional: out-of-tree WCN modules for the mainline 6.18 kernel)
# openwrt-luci only: MU300_LUCI_THEME_APK, a local copy of the pinned Aurora .apk (offline builds; else downloaded)
# (MU300_SAE_APK_DIR, a hand-built hostapd for the U30 Air's firmware SAE offload, is gone: the offload is
# disabled, docs/U30AIR-SAE.md, FINDINGS 80. The build refuses it rather than ignore it.)
[ -z "${MU300_SAE_APK_DIR:-}" ] || {
    echo "MU300_SAE_APK_DIR: the U30 Air SAE offload is disabled and its hostapd is not built into images (docs/U30AIR-SAE.md)" >&2
    exit 1
}
set -eu
FLAVOUR=${MU300_FLAVOUR:-openwrt}
case $FLAVOUR in
    openwrt)     VER=${MU300_WRT_VER:-25.12.6}; BASEURL=https://downloads.openwrt.org/releases ;;
    # ImmortalWrt is an OpenWrt fork: same package manager, same layout, more drivers and LuCI apps
    immortalwrt) VER=${MU300_WRT_VER:-25.12.2}; BASEURL=https://downloads.immortalwrt.org/releases ;;
    *) echo "unknown flavour '$FLAVOUR' (openwrt or immortalwrt)" >&2; exit 1 ;;
esac
# The hash comes from the release's sha256sums, and that file counts only with a valid usign signature by the
# release's key, pinned in openwrt/keys/usign/ (OpenWrt's keyring, git.openwrt.org/keyring.git usign/<number>): the
# 25.12 releases are signed by b5043e70f9a75cde (OpenWrt) and 467a35c14d7fbc5c (ImmortalWrt). Another release
# series is signed by another key, which has to be pinned here first. Without the check, whoever could change the
# download (a mirror, a proxy) could change sha256sums with it, and the tarball would match.
case $FLAVOUR:$VER in
    openwrt:25.12.*)     USIGN_KEY=b5043e70f9a75cde ;;
    immortalwrt:25.12.*) USIGN_KEY=467a35c14d7fbc5c ;;
    *) echo "no pinned usign key for $FLAVOUR $VER (openwrt/keys/usign, openwrt/build-rootfs.sh)" >&2; exit 1 ;;
esac
SYSTEM=${MU300_SYSTEM:-openwrt}
case $SYSTEM in
    openwrt) ;;
    # the panel on ImmortalWrt was never tested by anyone
    openwrt-luci)
        [ "$FLAVOUR" = openwrt ] || { echo "MU300_SYSTEM=openwrt-luci is built on OpenWrt only, not $FLAVOUR" >&2; exit 1; } ;;
    *) echo "unknown system '$SYSTEM' (openwrt or openwrt-luci)" >&2; exit 1 ;;
esac
KREL=5.4.254-gb50db5b6224c
# the default name: mu300-<system>-<version>-rootfs.tar.gz; ImmortalWrt keeps its own (it has only the plain system)
if [ "$FLAVOUR" = immortalwrt ]; then OUT=${1:-mu300-immortalwrt-$VER-rootfs.tar.gz}; else OUT=${1:-mu300-$SYSTEM-$VER-rootfs.tar.gz}; fi
TOP=$(cd "$(dirname "$0")/.." && pwd)
# build inputs (out/, firmware/, android-subset/, tools binaries, busybox) may live outside the checkout
IN=${MU300_INPUTS:-$TOP}
# Without its cellular protocol netifd has no wan and the image boots without mobile data. openwrt-luci also needs
# the IPv6 relay monitor: its first boot selects relay mode (91-mu300-luci), and mu300cell.sh then runs it. Plain
# OpenWrt never selects relay, and builds without it.
CELL=$TOP/openwrt/overlay/lib/netifd/proto/mu300cell.sh
[ -s "$CELL" ] || { echo "required cellular protocol helper missing: $CELL" >&2; exit 1; }
if [ "$SYSTEM" = openwrt-luci ]; then
    [ -s "$TOP/openwrt/luci-overlay/lib/netifd/proto/mu300cell-v6.sh" ] || {
        echo "required cellular protocol helper missing: openwrt/luci-overlay/lib/netifd/proto/mu300cell-v6.sh" >&2; exit 1;
    }
fi
# sipa_eth0 into fw4's software flowtable (applied below, the build fails when it no longer applies)
FW4PATCH=$TOP/openwrt/patches/fw4-sipa-offload.patch
[ -s "$FW4PATCH" ] || { echo "missing $FW4PATCH" >&2; exit 1; }
# fw4 reload/restart on the 5.4 kernel: its old flowtable goes first, in a transaction of its own (#95, FINDINGS 38)
FW4FTPATCH=$TOP/openwrt/patches/fw4-old-kernel-flowtable.patch
[ -s "$FW4FTPATCH" ] || { echo "missing $FW4FTPATCH" >&2; exit 1; }
# never an access point on a DFS channel: the SC2355 driver has no radar detection (FINDINGS 42)
NODFSPATCH=$TOP/openwrt/patches/wifi-scripts-no-dfs.patch
[ -s "$NODFSPATCH" ] || { echo "missing $NODFSPATCH" >&2; exit 1; }
# Docker (FINDINGS 130): in the image (MU300_DOCKER=1) or ready to install with apk (0, the default: the image carries
# its settings and firewall zone only). In the image, its 190 MB are there twice while an update unpacks the new system
# beside the old one, and an update by zip or installer no longer fits the smallest region OpenWrt is installed in
# (800 MiB: "No space left on device", measured on an F50).
case ${MU300_DOCKER:-0} in
    0|1) DOCKER=${MU300_DOCKER:-0} ;;
    *) echo "MU300_DOCKER is 0 or 1" >&2; exit 1 ;;
esac
# an access point is WPA2-PSK only: SAE (WPA3-Personal, its mixed and compatibility modes) is refused on the SC2355
# (the U30 Air's firmware SAE offload is disabled, FINDINGS 80)
WPA2PATCH=$TOP/openwrt/patches/wifi-scripts-wpa2-only.patch
[ -s "$WPA2PATCH" ] || { echo "missing $WPA2PATCH" >&2; exit 1; }
# the docker mounts of the panel system, as the positional parameters (OUT is read above): each path stays one
# argument, spaces and all
set --
if [ "$SYSTEM" = openwrt-luci ]; then
    # Keep the upstream Aurora theme reproducible. Its APK is installed while
    # assembling the rootfs, so the theme is present on first boot without a
    # network-dependent uci-defaults install step. Bootstrap remains available.
    THEME_APK=${MU300_LUCI_THEME_APK:-$TOP/work/luci-theme-aurora-1.4.0-r20260920.apk}
    # pinned here, not overridable: MU300_LUCI_THEME_APK may swap the file, never the hash it is checked against
    THEME_SHA=05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e
    if [ ! -s "$THEME_APK" ]; then
        [ -z "${MU300_LUCI_THEME_APK:-}" ] || {
            echo "theme package missing: $THEME_APK" >&2; exit 1;
        }
        mkdir -p "$TOP/work"
        curl -fL -o "$THEME_APK.part" \
          https://github.com/eamonxg/luci-theme-aurora/releases/download/v1.4.0/luci-theme-aurora-1.4.0-r20260920.apk
        mv "$THEME_APK.part" "$THEME_APK"
    fi
    theme_hash=$(shasum -a 256 "$THEME_APK" 2>/dev/null || sha256sum "$THEME_APK")
    [ "${theme_hash%% *}" = "$THEME_SHA" ] || {
        echo "Aurora APK checksum mismatch: $THEME_APK" >&2; exit 1;
    }
    # The panel's catalogs in the image: Turkish and Simplified Chinese (the others are the lang extra,
    # tools/make-extra.sh). A catalog without translations (English: the msgids are the English text) is no file at
    # all. Named by LuCI's code for the language (openwrt/luci-languages.tsv: zh_Hans is zh-cn), or LuCI does not
    # load them.
    CAT=$(mktemp -d)
    for l in tr zh_Hans; do
        code=$(awk -F '\t' -v d="$l" '$1 == d {print $2}' "$TOP/openwrt/luci-languages.tsv")
        [ -n "$code" ] || { echo "no row for $l in openwrt/luci-languages.tsv" >&2; exit 1; }
        python3 "$TOP/tools/po2lmo.py" "$TOP/openwrt/luci-app-mu300/po/$l/mu300.po" "$CAT/mu300.$code.lmo"
    done
    [ -s "$CAT/mu300.tr.lmo" ] || { echo "no Turkish catalog built from openwrt/luci-app-mu300/po/tr" >&2; exit 1; }
    [ -s "$CAT/mu300.zh-cn.lmo" ] || { echo "no Chinese catalog built from openwrt/luci-app-mu300/po/zh_Hans" >&2; exit 1; }
    set -- -v "$THEME_APK:/in/luci-theme-aurora.apk:ro" -v "$TOP/openwrt/luci-app-mu300:/in/luci-plugin:ro" \
        -v "$TOP/openwrt/luci-overlay:/in/luci-overlay:ro" -v "$CAT:/in/catalogs:ro"
fi
TARBALL=$FLAVOUR-$VER-armsr-armv8-rootfs.tar.gz
URL=$BASEURL/$VER/targets/armsr/armv8

cd "$TOP"
if [ ! -f "openwrt/$TARBALL" ]; then
    curl -fL -o "openwrt/$TARBALL" "$URL/$TARBALL"
fi
# sha256sums (below) counts only with a valid usign signature by the release's key
SUMS=$(mktemp -d)
curl -fsL -o "$SUMS/sha256sums" "$URL/sha256sums" && curl -fsL -o "$SUMS/sha256sums.sig" "$URL/sha256sums.sig" ||
    { echo "cannot download $URL/sha256sums and its signature" >&2; rm -rf "$SUMS"; exit 1; }
python3 "$TOP/tools/usign-verify.py" "$TOP/openwrt/keys/usign/$USIGN_KEY" "$SUMS/sha256sums.sig" "$SUMS/sha256sums" >/dev/null ||
    { echo "$URL/sha256sums: no valid signature by $FLAVOUR's key $USIGN_KEY" >&2; rm -rf "$SUMS"; exit 1; }
want=$(sed -n "s/^\([0-9a-f]\{64\}\) \*$TARBALL$/\1/p" "$SUMS/sha256sums")
rm -rf "$SUMS"
[ -n "$want" ] || { echo "$TARBALL is not in the signed sha256sums" >&2; exit 1; }
have=$(shasum -a 256 "openwrt/$TARBALL" 2>/dev/null || sha256sum "openwrt/$TARBALL")
[ "${have%% *}" = "$want" ] || { echo "checksum mismatch for $TARBALL" >&2; exit 1; }
docker import --platform linux/arm64 "openwrt/$TARBALL" mu300-$FLAVOUR-base:$VER >/dev/null
# OpenWrt ships an unsigned regulatory.db; this kernel requires the signed database (wens key), so take Debian/Ubuntu's
REGDB=$(mktemp -d)
docker run --rm --platform linux/arm64 -v "$REGDB":/o ubuntu:26.04 sh -c \
  "apt-get update -qq >/dev/null && apt-get install -y -qq wireless-regdb >/dev/null && cp /usr/lib/firmware/regulatory.db /usr/lib/firmware/regulatory.db.p7s /o/"

opt() { [ -e "$IN/$1" ] && echo "-v $IN/$1:/in/$2:ro" || true; }

# The required input first, with a readable message: without it docker fails somewhere inside the build.
ls "$IN/out/modules"/*.ko >/dev/null 2>&1 || {
    echo "no kernel modules in $IN/out/modules - build them first (kernel/build-linux.sh), or point" >&2
    echo "MU300_INPUTS at the directory that has out/modules, firmware/ and android-subset/." >&2
    exit 1
}
# The optional ones decide whether the image can use the modem, Wi-Fi or the GPU at all. Missing ones
# used to be skipped silently, which produces an image that boots and then does nothing useful.
for o in firmware android-subset android-gpu-subset tools/logdw/logdw tools/bt-init/mu300-bt-init tools/keys/mu300-keys tools/adbd/adbd tools/carry/mu300-carry tools/gpu/cltest busybox upstream/out/modules; do
    [ -e "$IN/$o" ] && echo "  + $o" || echo "  - $o   (missing: the image is built without it)"
done
# shellcheck disable=SC2046
docker run --rm --platform linux/arm64 \
  -v "$TOP/rootfs/overlay/opt/mu300":/in/opt-mu300:ro -v "$TOP/openwrt/overlay":/in/overlay:ro \
  -v "$FW4PATCH":/in/fw4-sipa-offload.patch:ro \
  -v "$FW4FTPATCH":/in/fw4-old-kernel-flowtable.patch:ro \
  -v "$NODFSPATCH":/in/wifi-scripts-no-dfs.patch:ro \
  -v "$WPA2PATCH":/in/wifi-scripts-wpa2-only.patch:ro \
  -v "$TOP/boot/module-order.txt":/in/module-order.txt:ro -v "$IN/out/modules":/in/modules:ro \
  $(opt out/modules.builtin modules.builtin) $(opt out/modules.builtin.modinfo modules.builtin.modinfo) \
  $(opt firmware firmware) $(opt android-subset android-subset) $(opt android-gpu-subset android-gpu-subset) \
  $(opt tools/logdw/logdw logdw) $(opt tools/bt-init/mu300-bt-init bt-init) $(opt tools/keys/mu300-keys keys) $(opt tools/adbd/adbd adbd) $(opt tools/gpu/cltest cltest) \
  $(opt busybox busybox) $(opt upstream/out/modules mainline-modules) $(opt tools/carry/mu300-carry carry) -v "$TOP/openwrt":/out -v "$REGDB":/in/regdb:ro "$@" \
  -e KREL=$KREL -e OUT="$(basename "$OUT")" -e MU300_VERSION="${MU300_VERSION:-dev}" -e MU300_SYSTEM="$SYSTEM" -e MU300_DOCKER="$DOCKER" mu300-$FLAVOUR-base:$VER /bin/sh -eu -c '
mkdir -p /var/lock /var/run /tmp
# The packages come from the feed over the network while the image is built, and one connection cut in the middle of
# one package failed the whole image, and a release with it. update, upgrade and add are tried three times, ten
# seconds apart; what fails three times (a package the feed does not have) fails the build as before. Everything
# else (del) runs once. The idea is from kanoqwq/clean-tf-7.2 33640d01 (FINDINGS 66).
# --- apk-retry begin
apk() {
    case ${1:-} in
        update|upgrade|add) ;;
        *) command apk "$@"; return ;;
    esac
    _try=1
    until command apk "$@"; do
        [ "$_try" -lt 3 ] || return 1
        _try=$((_try + 1))
        echo "apk $1 failed; trying again ($_try of 3)" >&2
        sleep "${MU300_APK_RETRY_WAIT:-10}"
    done
}
# --- apk-retry end
apk update >/dev/null
# the release tarball carries the packages of the day the release was cut; the feed of the release branch moves on
# (LuCI, mbedtls, the CA bundle, iwinfo ...), and "apk add" below installs only what is missing, so without this
# an image shipped months later still has the old LuCI and the package manager shows every update on the first look
apk upgrade >/dev/null
# openssl-util: the VPN module (mu300-vpn, github.com/dikeckaan/mu300-linux-vpn) fetches the VPN server certificate
# with it to pin, for links that ask for allowInsecure;
# i2c-tools, gpiod-tools: mu300-usb (the charger of the U30 Air) and mu300-nfc (its NFC tag);
# jq: the VPN module reads vmess links and raw Xray/sing-box configs with it, and rewrites those configs at every start
# curl: mu300-update fetches with it where it is (and tells "the mirror cannot be reached" from "the source
# answered" by its exit codes, which the wget family does not distinguish); the Ubuntu image has it too, so both
# systems behave the same
apk add wpad-basic-mbedtls wifi-scripts iwinfo wireless-regdb iw bash flock ip-full coreutils-stty openssl-util \
    i2c-tools gpiod-tools jq curl >/dev/null
# the router protocols LuCI offers, with their tools: WireGuard, PPTP/L2TP (PPPoE is in the base), 6in4/6rd/DS-Lite,
# GRE and VXLAN, ipset, and SQM (cake). Their kmod-* dependencies install the 6.12 modules of the feed, removed below like
# every other kmod: the mainline kernels have them built in or as modules of their own (mu300-mainline.config), 5.4
# has some of them only
apk add wireguard-tools luci-proto-wireguard ppp-mod-pptp xl2tpd 6in4 6rd ds-lite gre luci-proto-gre vxlan \
    luci-proto-vxlan ipset sqm-scripts luci-app-sqm >/dev/null
# Docker (MU300_DOCKER=1, FINDINGS 130): the engine, its command and compose, from the feed (Docker 29). Their kmod-*
# dependencies are the 6.12 modules of the feed, removed below like every other kmod; what they add to /etc/modules.d
# and /etc/modules-boot.d goes here, or ubox would load br_netfilter, the legacy iptables tables, btrfs and raid6 at
# every boot of a mainline kernel that has them as modules (overlay/etc/sysctl.d/13-mu300-docker.conf keeps the LAN
# bridge out of the firewall when br_netfilter is loaded all the same: an apk add on the device). The nftables backend
# of Docker needs none of them. The stress tool and the command line of containerd (ctr) are not used by docker: 39 MB
# less. dockerd does not start at boot (the enable link apk made goes).
if [ "$MU300_DOCKER" = 1 ]; then
    ls /etc/modules.d /etc/modules-boot.d > /tmp/modules.before 2>/dev/null || true
    apk add dockerd docker docker-compose >/dev/null
    for d in /etc/modules.d /etc/modules-boot.d; do
        for m in $(ls $d 2>/dev/null); do grep -qx "$m" /tmp/modules.before || rm -f "$d/$m"; done
    done
    rm -f /usr/bin/containerd-stress /usr/bin/ctr /etc/rc.d/S*dockerd /etc/rc.d/K*dockerd
    # (the data of Docker is not in /opt/docker, the default of the package: etc/docker/daemon.json)
    rmdir /opt/docker 2>/dev/null || true
    [ -x /usr/bin/dockerd ] && [ -x /usr/bin/docker ] && [ -x /usr/lib/docker/cli-plugins/docker-compose ] ||
        { echo "Docker is not complete" >&2; exit 1; }
    # LuCI > Docker (panel system only): containers, images, networks and volumes (luci-app-dockerman, 200 KB)
    [ -d /in/luci-plugin ] && apk add luci-app-dockerman >/dev/null
fi
# the pinned Aurora theme
[ -d /in/luci-plugin ] && apk add --allow-untrusted /in/luci-theme-aurora.apk >/dev/null
# relay mode (panel system only): ra-deprecate sends its router advertisement through a raw ICMPv6 socket (#87)
[ -d /in/luci-plugin ] && apk add ucode-mod-socket >/dev/null
# System > Terminal (panel system only): ttyd, and luci-app-ttyd for its settings page and translations; the image
# configures it (luci-overlay/etc/config/ttyd: LAN address only, a root shell only for a token of a LuCI session)
[ -d /in/luci-plugin ] && apk add ttyd luci-app-ttyd >/dev/null
# ujail drops CAP_PERFMON (38), which this 5.4 kernel does not know: jailed services (dnsmasq, ntpd) crash-loop
apk del procd-ujail procd-seccomp >/dev/null 2>&1 || true
# online firmware upgrades flash whole-disk armsr images: that would overwrite the eMMC, so remove them
apk del luci-app-attendedsysupgrade attendedsysupgrade-common owut >/dev/null 2>&1 || true
# what those leave in /etc/config, and the .apk-new copies the upgrade above writes beside a changed config: an update
# adds every config file of the image the device does not have yet (mu300-update), and these belong to no package
rm -f /etc/config/attendedsysupgrade /etc/config/*.apk-new
# LuCI in Turkish and Simplified Chinese besides English, in both systems: the translation of luci-base and of
# every LuCI app the image has that the feed translates (apk checks each package against the signed index). Their
# own uci-defaults, run by apk here, register the languages in luci.languages; the other languages are the lang
# extra (mu300-extra install lang).
i18n=
for c in base $(apk list --installed "luci-app-*" | sed -n "s/^luci-app-\([^ ]*\)-[0-9][^ -]* .*/\1/p"); do
    for l in tr zh-cn; do
        apk search "luci-i18n-$c-$l" | grep -q "^luci-i18n-$c-$l-[0-9]" && i18n="$i18n luci-i18n-$c-$l"
    done
done
echo "LuCI translations:$i18n"
for l in tr zh-cn; do
    case " $i18n " in *" luci-i18n-base-$l "*) ;; *) echo "no LuCI base translation $l in the feed" >&2; exit 1 ;; esac
done
apk add $i18n >/dev/null
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
# mu300cell reports sipa_eth0 as l3_device only, so fw4 leaves it out of its software flowtable and the cellular
# downlink takes the slow forwarding path. --fuzz=0: a changed fw4 that no longer matches fails the build.
# (patch goes into the build container only: the image was copied above)
apk add patch >/dev/null
patch --batch --fuzz=0 -d $R -p1 -i /in/fw4-sipa-offload.patch
grep -q sipa_eth0 $R/usr/share/ucode/fw4.uc || { echo "fw4 patch not applied" >&2; exit 1; }
patch --batch --fuzz=0 -d $R -p1 -i /in/fw4-old-kernel-flowtable.patch
grep -q drop_old_flowtable $R/sbin/fw4 || { echo "fw4 flowtable patch not applied" >&2; exit 1; }
# wifi-scripts: a driver without radar detection (no radar_detect_widths, the SC2355) never gets an access point on a
# DFS channel - hostapd fails start_dfs_cac and the hotspot stays down otherwise (FINDINGS 42)
patch --batch --fuzz=0 -d $R -p1 -i /in/wifi-scripts-no-dfs.patch
grep -q "device_no_dfs(config);" $R/usr/share/ucode/wifi/hostapd.uc || { echo "wifi-scripts DFS patch not applied" >&2; exit 1; }
# Docker keeps its images and containers on the Linux partition beside the systems, a directory for each system
# (/mnt/mu300-disk/docker/openwrt or openwrt-luci); the package reads etc/docker/daemon.json (alt_config_file)
sed -i "s|\"/mnt/mu300-disk/docker/openwrt\"|\"/mnt/mu300-disk/docker/$MU300_SYSTEM\"|" $R/etc/docker/daemon.json
grep -q "\"data-root\": \"/mnt/mu300-disk/docker/$MU300_SYSTEM\"," $R/etc/docker/daemon.json || { echo "Docker data-root not set" >&2; exit 1; }
grep -q "^.option alt_config_file ./etc/docker/daemon.json.$" $R/etc/config/dockerd || { echo "dockerd does not read etc/docker/daemon.json" >&2; exit 1; }
! ls $R/etc/rc.d/*dockerd >/dev/null 2>&1 || { echo "dockerd starts at boot" >&2; exit 1; }
# wifi-scripts: an access point that asks for SAE (sae, sae-mixed, sae-compat, psk3) is not started, with a line that
# says WPA2-PSK only - the U30 Air firmware SAE offload is disabled (FINDINGS 80), and plain SAE never worked here
patch --batch --fuzz=0 -d $R -p1 -i /in/wifi-scripts-wpa2-only.patch
grep -q "mu300_sae_refused(k, interface.config)" $R/usr/share/ucode/wifi/hostapd.uc || { echo "wifi-scripts WPA2-only patch not applied" >&2; exit 1; }
apk del patch >/dev/null   # out of packages.txt below, which lists what the image has
if [ -d /in/luci-plugin ]; then
    [ -d /in/luci-overlay ] && cp -a /in/luci-overlay/. $R/
    cp -a /in/luci-plugin/root/. $R/
    cp -a /in/luci-plugin/htdocs/. $R/www/
    chmod 0755 $R/etc/init.d/unisoc-modem-ui $R/etc/hotplug.d/net/90-unisoc-usb-host $R/etc/hotplug.d/iface/90-unisoc-usb-host $R/usr/libexec/rpcd/mu300dash $R/usr/libexec/rpcd/mu300vpn $R/usr/libexec/unisoc-modem/*
    chmod 0755 $R/usr/libexec/rpcd/mu300term $R/usr/libexec/mu300-terminal-login $R/etc/hotplug.d/iface/95-mu300-terminal
    # the terminal is System > Terminal (the panel menu: the page, and the settings page of luci-app-ttyd); its own Services >
    # Terminal frames ttyd without a token, which mu300-terminal-login refuses
    rm -f $R/usr/share/luci/menu.d/luci-app-ttyd.json
    # ttyd as luci-overlay configures it, never the package default (/bin/login on every LAN instance, no origin check)
    grep -q "^	option command ./usr/libexec/mu300-terminal-login.$" $R/etc/config/ttyd &&
        ! grep -v "^#" $R/etc/config/ttyd | grep -q "/bin/login" || { echo "ttyd is not configured for the panel" >&2; exit 1; }
    # the panel catalogs, named as LuCI names the language (base.zh-cn.lmo: mu300.zh-cn.lmo)
    for f in /in/catalogs/mu300.*.lmo; do cp "$f" $R/usr/lib/lua/luci/i18n/; done
    [ -s $R/www/luci-static/aurora/main.css ] || { echo "Aurora theme assets missing" >&2; exit 1; }
    # the default theme is set on first boot (etc/uci-defaults/91-mu300-luci), after the themes pick their own
    grep -q "mediaurlbase=.*/luci-static/aurora" $R/etc/uci-defaults/91-mu300-luci || {
        echo "Aurora theme is not the LuCI default" >&2; exit 1;
    }
fi
# the languages LuCI offers (System > System > Language and Style lists English and these); the base catalogs register
# their own, this covers one that did not
L=$R/usr/lib/lua/luci/i18n
for l in tr zh-cn; do
    [ -s $L/base.$l.lmo ] || { echo "LuCI catalog base.$l.lmo missing" >&2; exit 1; }
done
uci -c $R/etc/config -q get luci.languages.tr >/dev/null || uci -c $R/etc/config set luci.languages.tr="Türkçe (Turkish)"
uci -c $R/etc/config -q get luci.languages.zh_cn >/dev/null ||
    uci -c $R/etc/config set luci.languages.zh_cn="$(printf "\347\256\200\344\275\223\344\270\255\346\226\207 (Simplified Chinese)")"
uci -c $R/etc/config commit luci
uci -c $R/etc/config show luci.languages
# SQM (openwrt-luci): the default queue (disabled) shapes the generic eth1, which the MU300 does not have, so LuCI
# offered a device that is not there; it names the cellular uplink, sipa_eth0, instead
if [ -f $R/etc/config/sqm ]; then
    for q in $(uci -c $R/etc/config -q show sqm | sed -n "s/^sqm\.\([^.]*\)\.interface=.eth1.$/\1/p"); do
        uci -c $R/etc/config set sqm.$q.interface=sipa_eth0
    done
    uci -c $R/etc/config commit sqm
    uci -c $R/etc/config -q show sqm | grep -q "\.interface=.eth1.$" && { echo "SQM still names eth1" >&2; exit 1; }
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
# adb over USB (tools/adbd, static): on unless mu300-usb adb off
[ -e /in/adbd ] && { cp /in/adbd $R/opt/mu300/bin/adbd; chmod 755 $R/opt/mu300/bin/adbd; }
# an update carries everything of the user over with it (tools/carry, static; mu300-update, the carry block). Without
# it an update of this image keeps only the list of places, as before the first release with a four-field tag
[ -e /in/carry ] && { cp /in/carry $R/opt/mu300/bin/mu300-carry; chmod 755 $R/opt/mu300/bin/mu300-carry; }
# the manifests of the releases before images carried their own: of this system only (opt/mu300 has all four)
for m in $R/opt/mu300/lib/image-manifests/*.gz; do
    [ "$m" = "$R/opt/mu300/lib/image-manifests/$MU300_SYSTEM.gz" ] || rm -f "$m"
done
# the VPN engines are not part of the image: they are the vpn extra (mu300-extra install vpn)
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
# enable the services (rc.common "enable" needs ubus, which is not running in the build container)
# accounts still those of the image until an installer or mu300-update puts the device ones in place
: > $R/etc/.mu300-accounts-from-image
for s in mu300-accounts mu300-vendor mu300-hw mu300-post mu300-toolkit mu300-cpu mu300-cpu-voltage mu300-atd mu300-modem-log mu300-wifi-client mu300-buttons mu300-power mu300-traffic mu300-user-packages mu300-adbd; do
    n=$(sed -n "s/^START=//p" $R/etc/init.d/$s)
    ln -sf ../init.d/$s $R/etc/rc.d/S$n$s
done
# the CPU voltage offset (EXPERIMENTAL): its K link is the clean shutdown that keeps an offset for the next boot
n=$(sed -n "s/^STOP=//p" $R/etc/init.d/mu300-cpu-voltage)
ln -sf ../init.d/mu300-cpu-voltage $R/etc/rc.d/K${n}mu300-cpu-voltage
if [ -d /in/luci-plugin ]; then
    n=$(sed -n "s/^START=//p" $R/etc/init.d/unisoc-modem-ui)
    ln -sf ../init.d/unisoc-modem-ui $R/etc/rc.d/S${n}unisoc-modem-ui
    # stopped at shutdown: the operations log of the panel (mu300dash flush-ops) is kept for the next boot
    n=$(sed -n "s/^STOP=//p" $R/etc/init.d/unisoc-modem-ui)
    ln -sf ../init.d/unisoc-modem-ui $R/etc/rc.d/K${n}unisoc-modem-ui
    # the SMS pool behind the panel (K69, D11): openwrt-luci only, the sms command stays the SMS tool of every system
    chmod 0755 $R/opt/mu300/bin/mu300-sms $R/opt/mu300/bin/mu300-smsd $R/opt/mu300/bin/mu300-sms-forward $R/etc/init.d/mu300-smsd
    n=$(sed -n "s/^START=//p" $R/etc/init.d/mu300-smsd)
    ln -sf ../init.d/mu300-smsd $R/etc/rc.d/S${n}mu300-smsd
    # the dashboard AT channels, nr6 and nr7 (K19): the collector of the panel prefers them over nr1
    chmod 0755 $R/etc/init.d/mu300-atd-dash
    n=$(sed -n "s/^START=//p" $R/etc/init.d/mu300-atd-dash)
    ln -sf ../init.d/mu300-atd-dash $R/etc/rc.d/S${n}mu300-atd-dash
    # the routes of relay mode to the LAN (K37): it starts ndp-learn only when wan is in relay mode
    chmod 0755 $R/opt/mu300/bin/ndp-learn $R/lib/netifd/proto/mu300cell-v6.sh $R/etc/init.d/mu300-ndp
    n=$(sed -n "s/^START=//p" $R/etc/init.d/mu300-ndp)
    ln -sf ../init.d/mu300-ndp $R/etc/rc.d/S${n}mu300-ndp
    ln -sf /opt/mu300/bin/mu300-sms $R/usr/bin/mu300-sms
    # System > Terminal: apk enabled ttyd itself
    [ -e $R/etc/rc.d/S99ttyd ] || { echo "ttyd is not enabled" >&2; exit 1; }
fi
# the OpenWrt button handlers (power: a tap is poweroff; wps, rfkill) are replaced: mu300-buttons owns the keys
mkdir -p $R/etc/rc.button
for b in power wps rfkill; do
    printf "#!/bin/sh\n# mu300-buttons owns the keys (see /opt/mu300/bin/mu300-buttons)\nexit 0\n" > "$R/etc/rc.button/$b"
    chmod 0755 "$R/etc/rc.button/$b"
done
# no config file of a removed package and no .apk-new copy in the image: an update adds every etc/config file of the
# image the device lacks (mu300-update), so these would land on every device (v2026.10.18, built before the cleanup
# above, had both). Removed again here, in the image itself, and checked.
rm -f $R/etc/config/attendedsysupgrade $R/etc/config/*.apk-new
# no TLS key of the web server in the image: the uhttpd package makes the pair when apk installs it here, and every
# release up to v2026.10.16.1 shipped that one key to every device (FINDINGS 132). uhttpd makes the device its own
# at its first start, as it does whenever the pair is missing
rm -f $R/etc/uhttpd.key $R/etc/uhttpd.crt
[ ! -e $R/etc/uhttpd.key ] || { echo "the image holds a TLS key" >&2; exit 1; }
for f in $R/etc/config/attendedsysupgrade $R/etc/config/*.apk-new; do
    [ ! -e "$f" ] || { echo "stray config file in the image: $f" >&2; exit 1; }
done
# what apk installed, for comparing two builds (packages on the release feed are not pinned)
apk list --installed | sort > $R/etc/mu300/packages.txt
# the packages the image asks for (its world file): an update installs the others again, the ones added on the
# device (mu300-update, upk_record; read with its own function)
(MU300_LIB=1; . /in/opt-mu300/bin/mu300-update; upk_manual $R openwrt) > $R/etc/mu300/image-packages
[ -s $R/etc/mu300/image-packages ] || { echo "no package list for etc/mu300/image-packages" >&2; exit 1; }
# busybox PATH is /usr/sbin:/usr/bin:/sbin:/bin, so the commands go into /usr/bin (the same list as Ubuntu)
for c in $(cat /in/opt-mu300/lib/path-commands); do ln -sf /opt/mu300/bin/$c $R/usr/bin/$c; done
# no kernel of its own: OpenWrt kmods (6.12) and grub are unused on this device
rm -rf $R/lib/modules/6.* $R/boot
# the modules of the mainline kernel (upstream/out: the vendor ones and those of the kernel itself, about 20 MiB), so that
# a switch to it needs nothing more; mu300-update installs those of the bundle it boots over them
if ls /in/mainline-modules/*.ko >/dev/null 2>&1; then
    # the release the modules were built for (their vermagic), not a version written down here
    krel=$(for f in /in/mainline-modules/*.ko; do tr "\0" "\n" < $f | sed -n "s/^vermagic=\([^ ]*\) .*/\1/p"; break; done)
    [ -n "$krel" ] && mkdir -p $R/lib/modules/$krel && cp /in/mainline-modules/*.ko $R/lib/modules/$krel/
fi
# The manifest of the image: every file it holds, with type, mode, owner, size and sha256 (tools/image-manifest.py has
# the format and makes the same lines from the tarball; make-release.sh holds the two against each other). The next
# update reads it to tell the files of the image from those of the user. Last, when nothing in $R changes any more.
if [ -x $R/opt/mu300/bin/mu300-carry ]; then
    $R/opt/mu300/bin/mu300-carry manifest $R "$MU300_SYSTEM" "${MU300_VERSION:-dev}" | gzip -9n > /tmp/image-manifest.gz
    [ "$(gzip -dc /tmp/image-manifest.gz | wc -l)" -gt 1000 ] || { echo "the image manifest is not right" >&2; exit 1; }
    cp /tmp/image-manifest.gz $R/etc/mu300/image-manifest.gz; chmod 644 $R/etc/mu300/image-manifest.gz
fi
cd $R && tar -czf /out/$OUT .
ls -la /out/$OUT'
rm -rf "$REGDB" ${CAT:+"$CAT"}
