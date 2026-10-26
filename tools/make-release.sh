#!/bin/sh
# Maintainer: build the generic release assets used by ./install.sh (prebuilt mode) and optionally publish them.
#   tools/make-release.sh --next-tag      print the tag of the next release: vYYYY.MM.DD.N for today (UTC), N one
#                                          more than the highest N of today's releases and tags (1 for the first)
#   tools/make-release.sh --check-tag TAG  only check TAG as a new release's tag (below); prints it
#   tools/make-release.sh TAG             build into release/TAG and audit the contents
#   tools/make-release.sh TAG --publish   also create/update the GitHub release (gh)
#   tools/make-release.sh TAG --publish --prerelease   create it as a prerelease: never "latest", so mu300-update and
#                                          the installers do not offer it until it is promoted on GitHub
#   --any-date                             take a TAG whose day is not today (a build that went on past midnight, a
#                                          retry of a release made another day); its form is checked all the same
# The tag of a new release is vYYYY.MM.DD.N: the real day (UTC) it is built, and N = 1, 2, ... for the releases of
# that day (docs/FINDINGS.md 125; the three-field tags up to v2026.10.27 used the day as a counter and are not made
# any more). Every four-field tag is newer than every three-field one, for the updater and the installers alike.
# Needs docker and the kernel outputs in $MU300_KERNEL_OUT (default: out/, from kernel/build-all.sh).
# The assets must never contain proprietary files or anything from a particular device: the audit below fails the
# build if firmware, Android userspace, host keys or local settings end up in an image.
set -eu
USAGE='usage: tools/make-release.sh TAG [--publish [--prerelease]] [--any-date] | --next-tag | --check-tag TAG [--any-date]'
TOP=$(cd "$(dirname "$0")/.." && pwd)
REPO=${MU300_REPO:-dikeckaan/mu300-linux}
# tag_ok, tag_key, newer: the release tags (the same block as mu300-update's)
. "$TOP/tools/releases.sh"
# today: the day of a release built now, as its tag writes it (MU300_TODAY stands in for the clock in the tests)
today() { echo "${MU300_TODAY:-$(date -u +%Y.%m.%d)}"; }
# next_tag: vYYYY.MM.DD.N for today. What counts: GitHub's releases (drafts and prereleases too, as gh lists them to
# the maintainer) and the tags, here and on origin. Without GitHub's answer there is no next tag: a guess could
# take the name of a release that exists.
next_tag() {
    _day=v$(today)
    _rel=$(gh release list -R "$REPO" --limit 200 --json tagName -q '.[].tagName') ||
        { echo "cannot read the releases of $REPO (gh): no tag is guessed" >&2; return 1; }
    _n=0
    for _t in $_rel $(git -C "$TOP" tag -l "$_day.*") \
        $(git -C "$TOP" ls-remote --tags origin "refs/tags/$_day.*" 2>/dev/null | sed -n 's|.*refs/tags/\([^^]*\).*|\1|p'); do
        case $_t in "$_day".*) tag_ok "$_t" || continue; [ "${_t##*.}" -le "$_n" ] || _n=${_t##*.} ;; esac
    done
    [ "$_n" -lt 99 ] || { echo "$_day has 99 releases already" >&2; return 1; }
    echo "$_day.$((_n + 1))"
}
# check_tag TAG: a tag a new release may have - vYYYY.MM.DD.N, and of today unless --any-date
check_tag() {
    case $1 in
        *.*.*.*) tag_ok "$1" || { echo "$1 is not a release tag: vYYYY.MM.DD.N, N = 1..99 without a leading zero (tools/make-release.sh --next-tag)" >&2; return 1; } ;;
        *) echo "$1 is not a tag for a new release: those are vYYYY.MM.DD.N, the day and its N (tools/make-release.sh --next-tag; docs/FINDINGS.md 125)" >&2; return 1 ;;
    esac
    [ -n "$ANY_DATE" ] || [ "${1%.*}" = "v$(today)" ] ||
        { echo "$1 is not of today (v$(today)): a release is named after the day it is built (--any-date takes it anyway)" >&2; return 1; }
}
ARG= PUBLISH="" PRERELEASE="" ANY_DATE="" MODE=build
for a in "$@"; do
    case $a in
        --publish) PUBLISH=--publish ;;
        --prerelease) PRERELEASE=--prerelease ;;
        --any-date) ANY_DATE=1 ;;
        --next-tag) MODE=next ;;
        --check-tag) MODE=check ;;
        -*) echo "unknown option $a ($USAGE)" >&2; exit 1 ;;
        *) [ -z "$ARG" ] || { echo "$USAGE" >&2; exit 1; }; ARG=$a ;;
    esac
done
case $MODE in
    next) [ -z "$ARG" ] || { echo "$USAGE" >&2; exit 1; }; next_tag; exit ;;
    check) [ -n "$ARG" ] || { echo "$USAGE" >&2; exit 1; }; check_tag "$ARG" || exit 1; echo "$ARG"; exit 0 ;;
esac
TAG=$ARG
[ -n "$TAG" ] || { echo "$USAGE" >&2; exit 1; }
check_tag "$TAG" || exit 1
KOUT=${MU300_KERNEL_OUT:-$TOP/out}
D=$TOP/release/$TAG
IN=$D/inputs
[ -f "$KOUT/Image" ] && ls "$KOUT"/modules/*.ko >/dev/null 2>&1 || { echo "kernel outputs missing in $KOUT (kernel/build-all.sh)" >&2; exit 1; }
ls "$KOUT"/modules-u30air/*.ko >/dev/null 2>&1 || { echo "U30 Air modules missing in $KOUT/modules-u30air (kernel/build-all.sh)" >&2; exit 1; }
[ -z "$(git -C "$TOP" status --porcelain)" ] || { echo "commit your changes first: the release must match a commit" >&2; exit 1; }
rm -rf "$D" && mkdir -p "$IN/out" "$IN/tools/logdw" "$IN/tools/bt-init" "$IN/tools/gpu"
# the module trees are staged here and in TMPDIR (upstream/make-bundle.sh): both must keep xt_HL.ko and xt_hl.ko
# apart, which macOS's default file system does not (FINDINGS 212)
. "$TOP/upstream/case-sensitive.sh"
case_sensitive "$D" || exit 1
case_sensitive "${TMPDIR:-/tmp}" || exit 1

echo "==> helper binaries"
cp -R "$KOUT/modules" "$IN/out/modules"
cp "$KOUT/modules.builtin" "$KOUT/modules.builtin.modinfo" "$IN/out/"
docker build -q -t mu300-kbuild "$TOP/kernel" >/dev/null
docker build -q -t mu300-ubuntu:24.04 "$TOP/rootfs" >/dev/null
docker build -q --build-arg BASE=ubuntu:26.04 -t mu300-ubuntu:26.04 "$TOP/rootfs" >/dev/null
docker run --rm mu300-ubuntu:24.04 cat /bin/busybox > "$IN/busybox"; chmod +x "$IN/busybox"
docker run --rm -v "$TOP/tools":/src:ro -v "$IN/tools":/o mu300-kbuild sh -c '
  gcc -O2 -static -o /o/logdw/logdw /src/logdw/logdw.c &&
  gcc -O2 -static -o /o/bt-init/mu300-bt-init /src/bt-init/mu300-bt-init.c &&
  mkdir -p /o/keys && gcc -O2 -static -o /o/keys/mu300-keys /src/keys/mu300-keys.c &&
  mkdir -p /o/carry && gcc -O2 -static -o /o/carry/mu300-carry /src/carry/mu300-carry.c &&
  mkdir -p /o/pwcheck && gcc -O2 -static -o /o/pwcheck/mu300-pwcheck /src/pwcheck/mu300-pwcheck.c -lcrypt'
# the password checker of the installers (tools/android-pwcheck.sh: never a program of the Linux system it checks):
# it must know yescrypt, what Ubuntu's passwd writes (FINDINGS 126)
H='$y$j9T$yozuPAQ4fDSQH5fJzaFQd/$Z6UKqnHQMcZqZNJ18QIV5mTrJ7raj2y7MDipRg8bUj.'
[ "$(printf '%s\n%s\n' 'old secret 1' "$H" | docker run --rm -i -v "$IN/tools/pwcheck":/p:ro mu300-kbuild /p/mu300-pwcheck)" = MATCH ] ||
    { echo "mu300-pwcheck does not check a yescrypt hash" >&2; exit 1; }
# e2fsck for the boot ramdisk: init runs it on a root filesystem whose superblock records an error (FINDINGS 182)
sh "$TOP/tools/e2fsck/build.sh" "$IN/tools/e2fsck/e2fsck" >/dev/null
# adbd (adb over USB to Linux): static aarch64, from AOSP's sources (tools/adbd/build.sh)
sh "$TOP/tools/adbd/build.sh" "$IN/tools/adbd/adbd" >/dev/null
# cltest links against Android's libraries at build time only; use a local build when there is one
[ -f "$TOP/tools/gpu/cltest" ] && cp "$TOP/tools/gpu/cltest" "$IN/tools/gpu/cltest"

echo "==> kernel bundle"
K=$D/kernel && mkdir -p "$K"
cp -R "$IN/out/modules" "$K/modules"
cp -R "$KOUT/modules-u30air" "$K/modules-u30air"
# the devices this bundle runs on (mu300-update and the installers check it); not the V50: the only V50 known to run
# this project (kano's) ran mainline, and its LED driver is mainline only (upstream/make-bundle.sh, FINDINGS 95)
echo "f50 u30air" > "$K/devices"
# what it can do that older bundles could not: 5.4 reads the SD card (FINDINGS 31j), so a system on the card may get
# it; its init runs from either slot, so a Linux on slot a may get it (mu300-update checks both); the same init finds a
# system in a GPT partition named mu300root (gpt-root, FINDINGS 178)
printf 'sdcard\nlinux-slot\ngpt-root\n' > "$K/features"
cp "$KOUT/Image" "$KOUT/modules.builtin" "$KOUT/modules.builtin.modinfo" "$IN/busybox" "$IN/tools/logdw/logdw" "$K/"
# beside busybox for the installers' password check (the Magisk zips take both from here; FINDINGS 126)
cp "$IN/tools/pwcheck/mu300-pwcheck" "$K/"
# beside busybox for every boot ramdisk built from this bundle (install.sh, install.ps1, upstream/make-bundle.sh)
cp "$IN/tools/e2fsck/e2fsck" "$K/"
# the device-independent part of the boot ramdisk, which mu300-update puts behind the device's own ramdisk to update
# the kernel and the boot image without a computer (same builder and file list as install.sh)
python3 "$TOP/boot/build-boot-image.py" --generic-ramdisk --modules "$IN/out/modules" --busybox "$IN/busybox" \
  --device-modules "u30air=$KOUT/modules-u30air" \
  --logdw "$IN/tools/logdw/logdw" --e2fsck "$IN/tools/e2fsck/e2fsck" --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" \
  --out "$K/ramdisk-generic.lz4" >/dev/null
tar -C "$K" -czf "$D/mu300-kernel.tar.gz" .
rm -rf "$K"

echo "==> mainline kernel bundles"
# "mu300-update kernel 6.18|7.2|7.3": built by upstream/build.sh + build-modules.sh at this commit - 6.18 into
# upstream/out, 7.2 into upstream/out-7.2 (OUTDIR=out-7.2 KV=7.2.x), 7.3 into upstream/out-7.3 (OUTDIR=out-7.3 KV=7.3
# or 7.3.x; its first release reports 7.3.0); each build must be the version its name says
for kv in 6.18:out 7.2:out-7.2 7.3:out-7.3; do
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
    docker run --rm -v "$B":/w -v "$IN/out/modules":/kmods:ro -v "$IN/out":/kout:ro -v "$IN/tools/logdw/logdw":/logdw:ro \
      -v "$IN/tools/bt-init/mu300-bt-init":/bt-init:ro -v "$IN/tools/keys/mu300-keys":/keys:ro -v "$IN/tools/adbd/adbd":/adbd:ro \
      -v "$IN/tools/carry/mu300-carry":/carry:ro $cltest \
      -e MU300_VERSION="$TAG" mu300-ubuntu:$u bash /w/assemble.sh >/dev/null
    out=mu300-ubuntu-rootfs.tar.gz; [ $u = 24.04 ] || out=mu300-ubuntu-$u-rootfs.tar.gz
    mv "$B/mu300-ubuntu-$u-rootfs.tar.gz" "$D/$out"; rm -rf "$B"
done

echo "==> OpenWrt root filesystem (generic)"
MU300_INPUTS="$IN" MU300_VERSION="$TAG" sh "$TOP/openwrt/build-rootfs.sh" mu300-openwrt-release.tar.gz >/dev/null
mv "$TOP/openwrt/mu300-openwrt-release.tar.gz" "$D/mu300-openwrt-rootfs.tar.gz"

echo "==> OpenWrt with the MU300 control panel"
MU300_SYSTEM=openwrt-luci MU300_INPUTS="$IN" MU300_VERSION="$TAG" sh "$TOP/openwrt/build-rootfs.sh" mu300-openwrt-luci-release.tar.gz >/dev/null
mv "$TOP/openwrt/mu300-openwrt-luci-release.tar.gz" "$D/mu300-openwrt-luci-rootfs.tar.gz"

echo "==> lang extra"
# after the OpenWrt builds: it is built in the OpenWrt base image they import, from the same feed
sh "$TOP/tools/make-extra.sh" lang "$D/mu300-extra-lang.tar.gz" "$TAG"

echo "==> panel extra (Ubuntu's web panel: Cockpit and the MU300 page), online and offline"
# the offline one carries Cockpit's packages for both Ubuntu releases, from the mu300-ubuntu images built above
sh "$TOP/tools/make-extra.sh" panel "$D/mu300-extra-panel.tar.gz" "$TAG"
sh "$TOP/tools/make-extra.sh" panel-offline "$D/mu300-extra-panel-offline.tar.gz" "$TAG"

echo "==> audit"
fail=0
list_of_image() { tar -tzf "$D/$1.tar.gz" | sed 's|^\./||'; }
for a in mu300-kernel mu300-kernel-6.18 mu300-kernel-7.2 mu300-kernel-7.3 mu300-ubuntu-rootfs mu300-ubuntu-26.04-rootfs mu300-openwrt-rootfs \
         mu300-openwrt-luci-rootfs; do
    bad=$(tar -tzf "$D/$a.tar.gz" | sed 's|^\./||' | grep -E \
        -e '(^|/)lib/firmware/(wcnmodem|gnssmodem|wifi_board_config|bt_configure)' \
        -e '^opt/mu300/android/.+' -e '__properties__|dev-properties' \
        -e '(^|/)(libmali|libOpenCL|libGLES|libEGL|libvulkan)[^/]*\.so' \
        -e '^etc/ssh/ssh_host_' -e '^etc/uhttpd\.key$' -e '^etc/mu300/(hotspot|vpn|toolkit|mobile-data)\.conf$' -e '^etc/mu300/update-source$' -e '^etc/dropbear/dropbear_.*_host_key' \
        -e '^opt/mu300/bin/(xray|sing-box|hev-socks5-tunnel|mihomo|mu300-vpn)$' -e '^opt/mu300/lib/vpn(/|$)' \
        -e '^etc/systemd/system/mu300-vpn\.service$' -e '^etc/init\.d/mu300-vpn$' -e '^etc/mu300/vpn\.conf\.example$' \
        | grep -vE '^opt/mu300/android/system/?$|^opt/mu300/android/system/bin/?$|^opt/mu300/android/system/bin/cltest$' || true)
    if [ -n "$bad" ]; then echo "$a contains files that must not be published:"; echo "$bad" | head -20; fail=1; fi
    mid=$(tar -xzOf "$D/$a.tar.gz" ./etc/machine-id 2>/dev/null || true)
    [ -z "$mid" ] || { echo "$a has a machine-id"; fail=1; }
done
# An update carries everything of the user's over with what the NEW image brings (mu300-update's carry block): each
# image must have mu300-carry, a static busybox, the manifests of the releases before the first release with a four-field tag for its own system,
# and the manifest of its own files - which must be the one this tool makes from the tarball (two implementations,
# held against each other at every release). Without them an update would quietly keep only the list of places.
# The manifest is published beside the image, too (mu300-<image>-rootfs.manifest.gz, in SHA256SUMS).
for a in mu300-ubuntu-rootfs:usr/bin/busybox:ubuntu-24.04,ubuntu-26.04 mu300-ubuntu-26.04-rootfs:usr/bin/busybox:ubuntu-24.04,ubuntu-26.04 \
         mu300-openwrt-rootfs:opt/mu300/bin/busybox:openwrt mu300-openwrt-luci-rootfs:opt/mu300/bin/busybox:openwrt-luci; do
    old=${a##*:}; a=${a%:*}; bb=${a#*:}; a=${a%%:*}
    l=$(list_of_image "$a")
    for f in opt/mu300/bin/mu300-carry "$bb" etc/mu300/image-manifest.gz $(echo "$old" | tr ',' '\n' | sed 's|.*|opt/mu300/lib/image-manifests/&.gz|'); do
        printf '%s\n' "$l" | grep -qx "$f" || { echo "$a lacks $f: an update to it would not carry everything over"; fail=1; }
    done
    [ "$(printf '%s\n' "$l" | grep -c '^opt/mu300/lib/image-manifests/.')" = "$(echo "$old" | tr ',' '\n' | wc -l | tr -d ' ')" ] ||
        { echo "$a has manifests of another system in opt/mu300/lib/image-manifests"; fail=1; }
    python3 "$TOP/tools/image-manifest.py" check "$D/$a.tar.gz" || fail=1
    tar -xzOf "$D/$a.tar.gz" ./etc/mu300/image-manifest.gz > "$D/$a.manifest.gz" 2>/dev/null || { echo "$a: no manifest to publish"; fail=1; }
done
# the panel: everything of it is in the luci asset, and nothing of it (nor Aurora) in the plain OpenWrt one
list_of() { tar -tzf "$D/$1.tar.gz" | sed 's|^\./||'; }
panel=$(list_of mu300-openwrt-luci-rootfs)
for f in usr/share/luci/menu.d/luci-app-mu300.json www/luci-static/resources/view/mu300/home.js \
         usr/libexec/rpcd/mu300dash usr/lib/lua/luci/i18n/mu300.tr.lmo usr/lib/lua/luci/i18n/mu300.zh-cn.lmo \
         www/luci-static/aurora/main.css etc/mu300/packages.txt usr/lib/lua/luci/i18n/base.tr.lmo \
         usr/lib/lua/luci/i18n/base.zh-cn.lmo; do
    printf '%s\n' "$panel" | grep -qx "$f" || { echo "mu300-openwrt-luci-rootfs lacks $f"; fail=1; }
done
plain=$(list_of mu300-openwrt-rootfs)
for f in usr/libexec/rpcd/mu300dash www/luci-static/aurora/main.css; do
    ! printf '%s\n' "$plain" | grep -qx "$f" || { echo "mu300-openwrt-rootfs has $f, which is only for openwrt-luci"; fail=1; }
done
for f in etc/mu300/packages.txt usr/lib/lua/luci/i18n/base.tr.lmo usr/lib/lua/luci/i18n/base.zh-cn.lmo; do
    printf '%s\n' "$plain" | grep -qx "$f" || { echo "mu300-openwrt-rootfs lacks $f"; fail=1; }
done
# the lang extra: catalogs only (and its manifest), and the panel's in the languages it was translated to
lx=$(tar -tzf "$D/mu300-extra-lang.tar.gz" | sed 's|^\./||')
! printf '%s\n' "$lx" | grep . | grep -Evx 'name|release|components|languages|manifest|i18n/?|i18n/[a-z0-9-]+\.[a-z]{2,3}(-[a-z]{2})?\.lmo' ||
    { echo "mu300-extra-lang.tar.gz has files that are not catalogs"; fail=1; }
for f in i18n/base.de.lmo i18n/mu300.de.lmo i18n/mu300.ja.lmo; do
    printf '%s\n' "$lx" | grep -qx "$f" || { echo "mu300-extra-lang.tar.gz lacks $f"; fail=1; }
done
# what the package list changed since the last release (MU300_PREV_RELEASE: its directory, with the assets)
if [ -n "${MU300_PREV_RELEASE:-}" ]; then
    for a in mu300-openwrt-rootfs mu300-openwrt-luci-rootfs; do
        [ -f "$MU300_PREV_RELEASE/$a.tar.gz" ] || { echo "$a: no previous asset in $MU300_PREV_RELEASE"; continue; }
        echo "==> $a: etc/mu300/packages.txt against the previous release"
        tar -xzOf "$MU300_PREV_RELEASE/$a.tar.gz" ./etc/mu300/packages.txt > "$D/packages.prev" 2>/dev/null || : > "$D/packages.prev"
        tar -xzOf "$D/$a.tar.gz" ./etc/mu300/packages.txt > "$D/packages.new" 2>/dev/null || : > "$D/packages.new"
        diff -u "$D/packages.prev" "$D/packages.new" || true
        rm -f "$D/packages.prev" "$D/packages.new"
    done
fi
# an extra holds what its name says, for the release it is published with (mu300-update compares ./release). The VPN
# (vpn, vpn-mihomo) is not one of this release's: it is the module of github.com/dikeckaan/mu300-linux-vpn
# the panel: its page and command; the offline one the same with Cockpit's packages for both Ubuntu releases
for a in mu300-extra-panel mu300-extra-panel-offline; do
    px=$(tar -tzf "$D/$a.tar.gz" | sed 's|^\./||')
    ! printf '%s\n' "$px" | grep . | grep -Evx 'name|release|components|manifest|bin/?|bin/mu300-panel|cockpit/?|cockpit/mu300/?|cockpit/mu300/[A-Za-z0-9_.-]+|debs/?|debs/[0-9.]+/?|debs/[0-9.]+/[A-Za-z0-9._+~-]+\.deb' ||
        { echo "$a.tar.gz has unexpected files"; fail=1; }
    for f in bin/mu300-panel cockpit/mu300/manifest.json cockpit/mu300/mu300.js cockpit/mu300/po.tr.js cockpit/mu300/po.zh_CN.js; do
        printf '%s\n' "$px" | grep -qx "$f" || { echo "$a.tar.gz lacks $f"; fail=1; }
    done
    [ "$(tar -xzOf "$D/$a.tar.gz" ./name)" = panel ] && [ "$(tar -xzOf "$D/$a.tar.gz" ./release)" = "$TAG" ] ||
        { echo "$a.tar.gz is not the panel extra of $TAG"; fail=1; }
done
for u in 24.04 26.04; do
    tar -tzf "$D/mu300-extra-panel-offline.tar.gz" | grep -q "^\./debs/$u/cockpit-ws_" || { echo "mu300-extra-panel-offline.tar.gz has no Cockpit for Ubuntu $u"; fail=1; }
done
for x in lang; do
    [ "$(tar -xzOf "$D/mu300-extra-$x.tar.gz" ./name)" = $x ] && [ "$(tar -xzOf "$D/mu300-extra-$x.tar.gz" ./release)" = "$TAG" ] ||
        { echo "mu300-extra-$x.tar.gz is not the $x extra of $TAG"; fail=1; }
done
# mu300-update refuses a kernel bundle without the SD host for a system on the card, and one whose init runs only from
# slot b for a Linux on slot a: every bundle must say it has both
for a in mu300-kernel mu300-kernel-6.18 mu300-kernel-7.2 mu300-kernel-7.3; do
    tar -xzOf "$D/$a.tar.gz" ./features 2>/dev/null | grep -qx sdcard || { echo "$a does not list sdcard in ./features"; fail=1; }
    tar -xzOf "$D/$a.tar.gz" ./features 2>/dev/null | grep -qx linux-slot || { echo "$a does not list linux-slot in ./features"; fail=1; }
    tar -xzOf "$D/$a.tar.gz" ./features 2>/dev/null | grep -qx gpt-root || { echo "$a does not list gpt-root in ./features"; fail=1; }
done
# every generic ramdisk segment brings init's e2fsck: a root filesystem with recorded errors is checked before it is
# mounted (FINDINGS 182)
for a in mu300-kernel mu300-kernel-6.18 mu300-kernel-7.2 mu300-kernel-7.3; do
    tar -xzOf "$D/$a.tar.gz" ./ramdisk-generic.lz4 > "$D/ramdisk.check" 2>/dev/null &&
    python3 -c 'import importlib.util as u, sys
s = u.spec_from_file_location("b", sys.argv[1]); b = u.module_from_spec(s); s.loader.exec_module(b)
sys.exit("sbin/e2fsck" not in b.cpio_names(b.unlz4_legacy(open(sys.argv[2], "rb").read())))' \
        "$TOP/boot/build-boot-image.py" "$D/ramdisk.check" || { echo "$a: its ramdisk-generic.lz4 has no sbin/e2fsck"; fail=1; }
    rm -f "$D/ramdisk.check"
done
# a mainline bundle carries the kernel's own modules next to the vendor ones (upstream/build-modules.sh): one of each
# kind - qdisc, filesystem, device mapper, USB modem, vhost - and the vendor Wi-Fi
for a in mu300-kernel-6.18 mu300-kernel-7.2 mu300-kernel-7.3; do
    l=$(tar -tzf "$D/$a.tar.gz")
    for m in sch_htb.ko ntfs3.ko dm-crypt.ko cdc_mbim.ko vhost_net.ko sprd_wlan_combo.ko; do
        printf '%s\n' "$l" | grep -qx "./modules/$m" || { echo "$a lacks modules/$m"; fail=1; }
    done
done
# every module a mainline build made (its modules.in-tree) is in the bundle, by its exact name: on a file system that
# ignores case xt_HL.ko and xt_hl.ko, xt_TCPMSS.ko and xt_tcpmss.ko, xt_DSCP.ko and xt_dscp.ko, xt_RATEEST.ko and
# xt_rateest.ko became one file each - in every mainline bundle before v2026.10.23.1 (FINDINGS 212). The TTL and
# MSS pairs are named as well, so a modules.in-tree that lost them too does not pass.
for a in mu300-kernel-6.18 mu300-kernel-7.2 mu300-kernel-7.3; do
    tar -tzf "$D/$a.tar.gz" | sed -n 's|^\./modules/||p' > "$D/modules.check"
    want=$(tar -xzOf "$D/$a.tar.gz" ./modules.in-tree 2>/dev/null) || { echo "$a has no modules.in-tree"; fail=1; }
    for m in $(printf '%s\n' $want xt_HL.ko xt_hl.ko xt_TCPMSS.ko xt_tcpmss.ko | sort -u | grep -vxF -f "$D/modules.check"); do
        echo "$a lacks modules/$m"; fail=1
    done
    rm -f "$D/modules.check"
done
# Call audio is not supported: reject leftover drivers, programs and services in release inputs.
# The vendor kernel keeps its native DMA driver; mainline has no call-audio DMA module.
AUDIO_KO="audio_mem agdsp_access audio_sipc sprd_audcp_boot sprd_audcp_dvfs audio-dsp-dump audio-pipe sprd_apipe
mcdt_hw_r2p0 saudio sprd-dmaengine-pcm sprd-platform-pcm-routing sprd-compr-2stage-dma snd-soc-sprd-dai snd-soc-sprd-vaudio
snd-soc-sprd-vbc-v4 snd-soc-sprd-vbc-fe snd-soc-sprd-codec-ump9620-power snd-soc-sprd-codec-ump9620-power-dev
snd-soc-sprd-codec-ump9620 snd-soc-sprd-pa-ucp1301 snd-soc-sprd-dummy-codec snd-soc-sprd-card"
for a in mu300-kernel mu300-kernel-6.18 mu300-kernel-7.2 mu300-kernel-7.3; do
    l=$(tar -tzf "$D/$a.tar.gz")
    extra=; [ "$a" = mu300-kernel ] || extra=sprd-dma
    for m in $AUDIO_KO $extra; do
        ! printf '%s\n' "$l" | grep -Eq "/$m\.ko\$" || { echo "$a carries $m.ko, which belongs to the removed call-audio stack"; fail=1; }
    done
done
for m in mu300-ubuntu-rootfs mu300-ubuntu-26.04-rootfs mu300-openwrt-rootfs mu300-openwrt-luci-rootfs; do
    [ -f "$D/$m.manifest.gz" ] || continue
    ! gzip -dc "$D/$m.manifest.gz" | grep -Eq '(opt/mu300/bin/(mu300-audio-dsp|mu300-voice|pcmhost|ctlset|mu300-hfp)|systemd/system/mu300-(audio|voice)\.service|init\.d/mu300-(audio|voice)|modprobe\.d/mu300-audio\.conf)( |$)' ||
        { echo "$m carries removed call-audio programs or services"; fail=1; }
done
[ $fail = 0 ] || { echo "audit failed, nothing published" >&2; exit 1; }
rm -rf "$IN"
# the updater itself: an older mu300-update fetches this one and continues with it
cp "$TOP/rootfs/overlay/opt/mu300/bin/mu300-update" "$D/mu300-update"
# mu300-release: the tag, as a file, so that SHA256SUMS - and with it the signature - says which release these
# checksums are of (mu300-update's tag_sha: a signed SHA256SUMS cannot be handed out as another release's)
echo "$TAG" > "$D/mu300-release"
(cd "$D" && { shasum -a 256 *.tar.gz *.manifest.gz mu300-update mu300-release 2>/dev/null || sha256sum *.tar.gz *.manifest.gz mu300-update mu300-release; } > SHA256SUMS)
# The signature of SHA256SUMS (tools/sign-release.sh; mu300-update checks it where the system has the public key,
# docs/FINDINGS.md 86): made when the release key is here - MU300_RELEASE_KEY, which the Release workflow takes from the
# repository secret of that name, or MU300_RELEASE_KEY_FILE. The images of this release carry the public key when it
# is in the tree; devices with it refuse a newer release that is not signed, so such a release is never published
# without its signature.
PUB=$TOP/rootfs/overlay/opt/mu300/lib/release.pub
SIG=
if [ -n "${MU300_RELEASE_KEY:-}${MU300_RELEASE_KEY_FILE:-}" ]; then
    if [ -s "$PUB" ]; then sh "$TOP/tools/sign-release.sh" "$D/SHA256SUMS" "$PUB"
    else
        echo "signing without $PUB in the tree: no device checks this signature until the public key is committed" >&2
        sh "$TOP/tools/sign-release.sh" "$D/SHA256SUMS"
    fi
    SIG=$D/SHA256SUMS.sig
elif [ -s "$PUB" ]; then
    echo "the images carry the release key ($PUB) and there is no MU300_RELEASE_KEY to sign with:" >&2
    echo "devices would refuse this release. Nothing is published without its signature." >&2
    [ "$PUBLISH" != --publish ] || exit 1
fi
ls -la "$D"

[ "$PUBLISH" = --publish ] || { echo "built release/$TAG (run again with --publish to upload)"; exit 0; }
commit=$(git -C "$TOP" rev-parse HEAD)
kernel_rev=$(sed -n 's/^KERNEL_REV=//p' "$TOP/kernel/build-all.sh")
modules_rev=$(sed -n 's/^MODULES_REV=//p' "$TOP/kernel/build-all.sh")
notes=$(mktemp "${TMPDIR:-/tmp}/mu300-notes.XXXXXX")
cat > "$notes" <<EOF
Prebuilt images for \`./install.sh\` (ZTE F50 / MU300 and ZTE U30 Air; the installer recognises which). Check your device
first with \`./install.sh --check\`.

| file | contents |
|---|---|
| mu300-kernel.tar.gz | Linux 5.4.254 \`Image\` and modules (with the U30 Air's own in \`modules-u30air/\`), with the patches of \`kernel/patches\` (among them: no panic when a network namespace goes away, #103, and the Wi-Fi driver's routine lines no longer at error level, #140), static busybox, logdw and e2fsck for the boot image, the installers' static password checker \`mu300-pwcheck\`, and the generic boot ramdisk segment \`mu300-update\` uses |
| mu300-kernel-6.18.tar.gz | mainline Linux 6.18 (longterm) for \`mu300-update kernel 6.18\`: \`Image\`, modules, generic boot ramdisk segment; with BTF and the BPF JIT (so the VPN module's dae engine runs), FunctionFS (for adb) and card detect for the F50's SD slot (an empty slot is no longer polled, #181) |
| mu300-kernel-7.2.tar.gz | mainline Linux 7.2 (stable) for \`mu300-update kernel 7.2\`: the same parts |
| mu300-kernel-7.3.tar.gz | mainline Linux 7.3 (the newest mainline release) for \`mu300-update kernel 7.3\`: the same parts |
| mu300-ubuntu-rootfs.tar.gz | Ubuntu 24.04 LTS root filesystem |
| mu300-ubuntu-26.04-rootfs.tar.gz | Ubuntu 26.04 LTS root filesystem |
| mu300-openwrt-rootfs.tar.gz | OpenWrt 25.12.6 root filesystem |
| mu300-openwrt-luci-rootfs.tar.gz | OpenWrt 25.12.6 with the MU300 control panel (luci-app-mu300 by kanoqwq, Aurora theme by eamonxg) |
| mu300-extra-lang.tar.gz | LuCI and the MU300 panel in more languages (OpenWrt only; English, Turkish and Chinese are in the images): \`mu300-extra install lang\` on the device, or System > Languages in the panel; LuCI's catalogs from the OpenWrt 25.12.6 feed, the panel's AI-translated |
| mu300-extra-panel.tar.gz | the web panel for Ubuntu: Cockpit (Ubuntu's web console: terminal, logs, services, networking, storage) and the MU300 page (signal, mobile data, data usage, SMS, VPN, power and CPU, Wi-Fi, updates), on the LAN only: \`sudo mu300-extra install panel\` on the device (Cockpit from Ubuntu's archive), or the installer's question |
| mu300-extra-panel-offline.tar.gz | the same with Cockpit's packages for Ubuntu 24.04 and 26.04, for a device that is not online: \`sudo mu300-extra install panel --from mu300-extra-panel-offline.tar.gz\` |
| mu300-update | the on-device updater of this release (\`mu300-update apply\` switches to it before it changes anything) |
| mu300-*-rootfs.manifest.gz | the list of each image's files (type, mode, owner, size, sha256), which the image also carries: an update reads it to tell the image's files from yours, and carries yours over |
| mu300-magisk-*.zip | attached a few minutes later by the Magisk installers workflow, one per system and kernel. Two ways to use one: install it in **Magisk** on the device (no computer), or **unpack it on a computer** and run \`start.cmd\` / \`sh start.sh\` (\`start-gui\` for the window): the project's installer, with that system and kernel, offline (\`START-HERE.md\` in the zip; adb from Google's platform-tools is not included) |
| (the VPN) | not part of this release: the VPN toolkit (\`mu300-vpn\`, module v2: the core with hev-socks5-tunnel, and the engines as extras: \`vpn-xray\`, \`vpn-singbox\`, \`vpn-mihomo\`, \`vpn-dae\`) is the module of https://github.com/dikeckaan/mu300-linux-vpn - \`mu300-extra install vpn\` on the device, or the installer's question, takes its latest release with the engines its profiles need; on OpenWrt with the panel the VPN menu drives it |

The images contain **no proprietary files**: the installer pulls the Wi-Fi/Bluetooth firmware and the Android
modem/GPU userspace from your own device and adds them during installation. They carry a static adbd (AOSP
platform-tools, built by \`tools/adbd/build.sh\`) for adb to Linux over USB, which is **on by default and lets every
computer in**: whoever can plug a cable into the device gets a root shell without a password. To close it:
\`sudo mu300-usb adb auth keys\` (only the keys authorised with \`sudo mu300-usb adb key FILE\`) or
\`sudo mu300-usb adb off\`. A device where adb was turned off, or set up with a key, before this release stays as it was.

Built from $REPO@$commit with \`kernel/build-all.sh\` and \`tools/make-release.sh\`.
Corresponding source (GPL): kernel https://github.com/Enceka/android_kernel_zte_ums9620_mifi_u30air/tree/$kernel_rev ,
Wi-Fi/Bluetooth/Mali modules https://github.com/realme-kernel-opensource/realme_C51_C53_Narzo-N53-AndroidT-kernel-source/tree/$modules_rev ,
patches in \`kernel/patches\`. Ubuntu, OpenWrt and busybox packages come from their distributions' archives;
Aurora (luci-theme-aurora 1.4.0) from https://github.com/eamonxg/luci-theme-aurora/releases/tag/v1.4.0.
News and releases: the Telegram channel https://t.me/mu300project
EOF
if gh release view "$TAG" -R "$REPO" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    gh release upload "$TAG" -R "$REPO" --clobber "$D"/*.tar.gz "$D"/*.manifest.gz "$D/mu300-update" "$D/mu300-release" "$D/SHA256SUMS" $SIG
else
    # shellcheck disable=SC2086
    gh release create "$TAG" -R "$REPO" --target "$commit" --title "MU300 Linux $TAG" --notes-file "$notes" $PRERELEASE \
      "$D"/*.tar.gz "$D"/*.manifest.gz "$D/mu300-update" "$D/mu300-release" "$D/SHA256SUMS" $SIG
fi
rm -f "$notes"
