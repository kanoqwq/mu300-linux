#!/bin/sh
# Build the Magisk installer zips of a release, one per system and kernel (design:
# docs/superpowers/specs/2026-10-05-magisk-installer-design.md):
#   tools/make-magisk-zips.sh RELEASE_DIR OUT_DIR [TAG]
# RELEASE_DIR holds the release's assets exactly as published; every one must match its SHA256SUMS. The zips contain
# the installer, the switch module, the 5.4 bundle's static busybox and the two assets of their combination - no file
# from any device: the installer reads those on the device it runs on. Every zip is checked against an exact list
# of names before it counts.
# A zip is also what a device without internet is updated from (mu300-update apply --from ZIP, the panel's System >
# Update): for that it carries the release's SHA256SUMS and, when the release is signed, SHA256SUMS.sig - the
# signature covers the SHA256SUMS, and the SHA256SUMS the two assets of the payload, so a zip can be checked against
# the project's release key wherever it came from (docs/FINDINGS.md 86). The installer does not read either.
# Unpacked on a computer, a zip is an installer for that computer too (docs/FINDINGS.md 111): computer/ holds this
# project's installers (android/magisk/computer/files.txt), with START-HERE.md and the launchers at the top, and they
# install the zip's own payload (tools/package.sh). None of that is a file the Magisk side reads: customize.sh unpacks
# only module.prop, mu300/, the switch module and system/, so a zip installs in Magisk exactly as it did without them.
set -eu
USAGE='usage: tools/make-magisk-zips.sh RELEASE_DIR OUT_DIR [TAG]'
TOP=$(cd "$(dirname "$0")/.." && pwd)
R=$(cd "${1:?$USAGE}" && pwd)
: "${2:?$USAGE}"
TAG=${3:-$(basename "$R")}
# The installer's manifest_load accepts only this tag, so a zip built with another one would fail on the device
# instead of here; the tag also goes into sed replacements and file names, so this comes before either.
tag_ok=1
case $TAG in v[0-9]*) ;; *) tag_ok= ;; esac
case $TAG in *[!0-9A-Za-z._-]*|*..*) tag_ok= ;; esac
[ -n "$tag_ok" ] || { echo "the tag '$TAG' is not one the installer accepts: v[0-9] and then letters, digits, . _ - (no ..)" >&2; exit 1; }
sha() { (sha256sum "$1" 2>/dev/null || shasum -a 256 "$1") | cut -d' ' -f1; }
for f in mu300-kernel.tar.gz mu300-kernel-6.18.tar.gz mu300-kernel-7.2.tar.gz mu300-kernel-7.3.tar.gz mu300-openwrt-rootfs.tar.gz \
         mu300-openwrt-luci-rootfs.tar.gz mu300-ubuntu-rootfs.tar.gz mu300-ubuntu-26.04-rootfs.tar.gz; do
    want=$(awk -v f="$f" '$2 == f || $2 == "*" f {print $1}' "$R/SHA256SUMS")
    [ -n "$want" ] && [ -f "$R/$f" ] && [ "$(sha "$R/$f")" = "$want" ] ||
        { echo "$f is missing from $R or does not match its SHA256SUMS" >&2; exit 1; }
done
mkdir -p "$2"; O=$(cd "$2" && pwd)
# Magisk wants an integer that grows: the date in the tag (v2026.10.06 -> 20261006); for a four-field tag the date and
# N (v2026.10.16.2 -> 2026101602: ten digits, above every three-field tag's eight, as the release is newer; docs/
# FINDINGS.md 125; below 2^31 until 2147). Any other tag: its digits, as before.
. "$TOP/tools/releases.sh"
code=$(tag_key "$TAG") || code=
case $code in
    2*) code=${code#2} ;;
    1*) code=${code#1}; code=${code%00} ;;
    *) code=$(printf '%s' "$TAG" | tr -cd 0-9 | cut -c1-9); [ -n "$code" ] || code=1 ;;
esac
W=$(mktemp -d "${TMPDIR:-/tmp}/mu300-magisk.XXXXXX"); trap 'rm -rf "$W"' EXIT
tar -xzOf "$R/mu300-kernel.tar.gz" ./busybox > "$W/busybox"
[ -s "$W/busybox" ] || { echo "mu300-kernel.tar.gz has no busybox" >&2; exit 1; }
# the installer's password checker (tools/pwcheck, FINDINGS 126), in the bundles of the releases that have it: without
# it a zip checks MD5- and SHA-crypt with the busybox only, and a yescrypt password cannot be checked
tar -xzOf "$R/mu300-kernel.tar.gz" ./mu300-pwcheck > "$W/mu300-pwcheck" 2>/dev/null || rm -f "$W/mu300-pwcheck"
[ -s "$W/mu300-pwcheck" ] || rm -f "$W/mu300-pwcheck"

# what every zip holds besides its payload, by name (the audit below compares against this)
COMMON='META-INF/com/google/android/update-binary META-INF/com/google/android/updater-script module.prop customize.sh
action.sh switch.sh bootchain.sh system/bin/mu300-linux mu300/install.sh mu300/android-boot-image.sh mu300/mu300-update
mu300/android-install.sh mu300/android-bootchain.sh mu300/android-mount-mu300root.sh mu300/android-pwcheck.sh mu300/storage.sh mu300/i18n.sh mu300/i18n/tr.tsv
mu300/i18n/zh.tsv mu300/subset-files.txt mu300/gpu-files.txt mu300/busybox mu300/manifest mu300/SHA256SUMS'
# ... and the computer's side: the launchers, the guide, and computer/ - the files of files.txt (a directory: every
# file in it, never a cache of Python's or a hidden file), and the mark that says "the payload is next to me"
C=$TOP/android/magisk/computer
LAUNCHERS='START-HERE.md start.sh start.cmd start-gui.sh start-gui.cmd uninstall-linux.sh uninstall-linux.cmd'
COMPUTER=$(sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$C/files.txt" | while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ -d "$TOP/$f" ]; then
        (cd "$TOP" && find "$f" -type f ! -name '.*' ! -name '*.pyc' ! -path '*/__pycache__/*')
    elif [ -f "$TOP/$f" ]; then
        echo "$f"
    else
        echo "android/magisk/computer/files.txt names $f, which is not there" >&2; echo '<missing>'
    fi
done | LC_ALL=C sort -u)
case $COMPUTER in *'<missing>'*) exit 1 ;; esac
COMMON="$COMMON $LAUNCHERS computer/.mu300-package $(printf '%s\n' $COMPUTER | sed 's|^|computer/|')"
# a .cmd for cmd.exe has CRLF line ends, whatever the checkout did
crlf() { tr -d '\r' < "$1" | awk '{ printf "%s\r\n", $0 }' > "$2"; }
# (the signature of a signed release)
[ ! -s "$R/SHA256SUMS.sig" ] || COMMON="$COMMON mu300/SHA256SUMS.sig"
[ ! -s "$W/mu300-pwcheck" ] || COMMON="$COMMON mu300/mu300-pwcheck"
stage() {  # stage DIR SYSTEM KERNEL ROOTFS_ASSET KERNEL_ASSET OS UBUNTU
    S=$1
    mkdir -p "$S/META-INF/com/google/android" "$S/system/bin" "$S/mu300/i18n" "$S/payload"
    I=$TOP/android/magisk/installer; M=$TOP/android/magisk/mu300-linux-switch
    cp "$I/update-binary" "$I/updater-script" "$S/META-INF/com/google/android/"
    sed -e "s/@TAG@/$TAG/" -e "s/@CODE@/$code/" -e "s/@SYSTEM@/$2/" -e "s/@KERNEL@/$3/" "$I/module.prop.in" > "$S/module.prop"
    cp "$I/customize.sh" "$M/action.sh" "$M/switch.sh" "$M/bootchain.sh" "$S/"
    cp "$M/system/bin/mu300-linux" "$S/system/bin/"
    cp "$I/mu300-install.sh" "$S/mu300/install.sh"
    cp "$TOP/tools/android-boot-image.sh" "$TOP/tools/android-install.sh" "$TOP/tools/android-bootchain.sh" "$TOP/tools/android-mount-mu300root.sh" \
       "$TOP/tools/android-pwcheck.sh" "$TOP/tools/storage.sh" "$TOP/tools/i18n.sh" "$S/mu300/"
    # the checkout's (in CI the tag's, so the release's own): the installer needs bootimg_from_stock, which an older
    # release's mu300-update does not have when zips are built locally from one
    cp "$TOP/rootfs/overlay/opt/mu300/bin/mu300-update" "$S/mu300/mu300-update"
    cp "$TOP/i18n/tr.tsv" "$TOP/i18n/zh.tsv" "$S/mu300/i18n/"
    cp "$TOP/android-vendor/subset-files.txt" "$TOP/android-vendor/gpu-files.txt" "$S/mu300/"
    cp "$W/busybox" "$S/mu300/busybox"
    [ ! -s "$W/mu300-pwcheck" ] || cp "$W/mu300-pwcheck" "$S/mu300/mu300-pwcheck"
    cp "$R/SHA256SUMS" "$S/mu300/SHA256SUMS"
    [ ! -s "$R/SHA256SUMS.sig" ] || cp "$R/SHA256SUMS.sig" "$S/mu300/SHA256SUMS.sig"
    cp "$R/$4" "$R/$5" "$S/payload/"
    printf 'TAG=%s\nSYSTEM=%s\nOS=%s\nUBUNTU=%s\nKERNEL=%s\nKERNEL_ASSET=%s\nROOTFS_ASSET=%s\nSHA256_KERNEL=%s\nSHA256_ROOTFS=%s\n' \
        "$TAG" "$2" "$6" "$7" "$3" "$5" "$4" "$(sha "$R/$5")" "$(sha "$R/$4")" > "$S/mu300/manifest"
    # the computer's side (tools/package.sh): the project's installers of this checkout (in CI the tag's), the guide
    # with this zip's system, kernel and release in it, the launchers, and the mark
    for f in $COMPUTER; do
        mkdir -p "$S/computer/$(dirname "$f")"
        case $f in *.cmd) crlf "$TOP/$f" "$S/computer/$f" ;; *) cp "$TOP/$f" "$S/computer/$f" ;; esac
    done
    printf '%s\n' 'This directory is the computer side of a Magisk zip of MU300 Linux: the payload is next to it (START-HERE.md).' \
        > "$S/computer/.mu300-package"
    sed -e "s/@TAG@/$TAG/g" -e "s/@SYSTEM@/$2/g" -e "s/@KERNEL@/$3/g" "$C/START-HERE.md" > "$S/START-HERE.md"
    for f in start.sh start-gui.sh uninstall-linux.sh; do cp "$C/$f" "$S/$f"; done
    for f in start.cmd start-gui.cmd uninstall-linux.cmd; do crlf "$C/$f" "$S/$f"; done
    chmod 755 "$S/start.sh" "$S/start-gui.sh" "$S/uninstall-linux.sh"     # (only these: the Magisk files keep their modes)
    find "$S/computer" -name '*.sh' -exec chmod 755 {} +
}
audit() {  # audit ZIP ROOTFS_ASSET KERNEL_ASSET: exactly the names it should hold, the payload stored
    printf '%s\n' $COMMON "payload/$2" "payload/$3" | LC_ALL=C sort > "$W/want"
    unzip -Z1 "$1" | LC_ALL=C sort > "$W/have"
    diff "$W/have" "$W/want" >&2 || { echo "$1 does not hold exactly the expected files (< extra, > missing)" >&2; return 1; }
    [ "$(unzip -Zv "$1" 'payload/*' | grep -c 'compression method: *none')" = 2 ] || { echo "$1: the payload is compressed" >&2; return 1; }
}
: > "$O/SHA256SUMS-magisk"
for s in openwrt:mu300-openwrt-rootfs.tar.gz:openwrt: \
         openwrt-luci:mu300-openwrt-luci-rootfs.tar.gz:openwrt-luci: \
         ubuntu-24.04:mu300-ubuntu-rootfs.tar.gz:ubuntu:24.04 \
         ubuntu-26.04:mu300-ubuntu-26.04-rootfs.tar.gz:ubuntu:26.04; do
    name=${s%%:*}; rest=${s#*:}; rootfs=${rest%%:*}; rest=${rest#*:}; os=${rest%%:*}; ubuntu=${rest#*:}
    for k in 5.4:mu300-kernel.tar.gz 6.18:mu300-kernel-6.18.tar.gz 7.2:mu300-kernel-7.2.tar.gz 7.3:mu300-kernel-7.3.tar.gz; do
        kv=${k%%:*}; kasset=${k#*:}
        # Ubuntu 26.04's programs need system calls 5.4 does not have (install.sh refuses the pair too)
        [ "$ubuntu" = 26.04 ] && [ "$kv" = 5.4 ] && continue
        zip=mu300-magisk-$TAG-$name-k$kv.zip
        S=$W/$name-$kv
        stage "$S" "$name" "$kv" "$rootfs" "$kasset" "$os" "$ubuntu"
        rm -f "$O/$zip"
        (cd "$S" && find . -type f ! -path './payload/*' | sed 's|^\./||' | LC_ALL=C sort | zip -X -q -9 "$O/$zip" -@ &&
            zip -X -q -0 "$O/$zip" "payload/$kasset" "payload/$rootfs")
        audit "$O/$zip" "$rootfs" "$kasset" || { rm -f "$O/$zip"; exit 1; }
        rm -rf "$S"
        printf '%s  %s\n' "$(sha "$O/$zip")" "$zip" >> "$O/SHA256SUMS-magisk"
        echo "$zip: $(wc -c < "$O/$zip" | tr -d ' ') bytes"
    done
done
