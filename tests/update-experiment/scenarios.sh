#!/bin/sh
# The updates that are not the plain one, each on a real image (lab.sh), inside the container:
#   scenarios.sh OLD_RELEASE_DIR NEW_RELEASE_DIR NEW_TAG OUT_DIR [NAME...]
#     link       apply --no-rollback: the files become the new system's, the old one is not kept
#     move       Ubuntu 24.04 moves to 26.04 (MU300_UBUNTU): the accounts are numbered differently there
#     unknown    an old system no manifest is known of (it says "dev"): by both paths
#     room       2.5 GB of the user's in a filesystem that has no room for a copy: stops with nothing changed, and
#                goes through with --no-rollback
#     again      OpenWrt updated twice (the second time to NEW_TAG+1, repacked here): the first update's own manifest
#                is what the second one reads, and what the first set aside is still there
#     list       MU300_UPDATE_KEEP=list: the update as it was before the first release with a four-field tag
# OUT_DIR gets NAME-*.tsv with their .log, .report, .sane; one line per run says how the planted items fared.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
old=$1 new=$2 tag=$3 out=$4
shift 4
[ $# -gt 0 ] || set -- link move unknown room again list
mkdir -p "$out"
run() {  # run NAME: the summary line of lab.sh
    name=$1; shift
    echo "== $name"
    sh "$HERE/lab.sh" "$@" 2>&1 | grep -v '^planted' || echo "FAILED"
}
for s in "$@"; do
    case $s in
    link)
        MU300_LAB_APPLY=--no-rollback run link device openwrt "$old/mu300-openwrt-rootfs.tar.gz" "$new" "$tag" "$out/link-device-openwrt.tsv" ;;
    move)
        MU300_LAB_ENV=MU300_UBUNTU=26.04 MU300_LAB_KERNEL=6.18 run move device ubuntu "$old/mu300-ubuntu-rootfs.tar.gz" "$new" "$tag" "$out/move-device-ubuntu.tsv" ;;
    unknown)
        MU300_LAB_OLD_VERSION=dev run "unknown, mu300-update" device openwrt "$old/mu300-openwrt-rootfs.tar.gz" "$new" "$tag" "$out/unknown-device-openwrt.tsv"
        MU300_LAB_OLD_VERSION=dev run "unknown, installers" android ubuntu "$old/mu300-ubuntu-rootfs.tar.gz" "$new/mu300-ubuntu-rootfs.tar.gz" "$HERE/../../tools/android-install.sh" "$out/unknown-android-ubuntu.tsv" ;;
    room)
        MU300_LAB_BIG=2500 MU300_LAB_FREE=2300 run "room, a copy does not fit" device openwrt "$old/mu300-openwrt-rootfs.tar.gz" "$new" "$tag" "$out/room-device-openwrt.tsv"
        MU300_LAB_BIG=2500 MU300_LAB_FREE=2300 MU300_LAB_APPLY=--no-rollback run "room, --no-rollback" device openwrt "$old/mu300-openwrt-rootfs.tar.gz" "$new" "$tag" "$out/room-link-device-openwrt.tsv" ;;
    again)
        next=${tag%.*}.$(( ${tag##*.} + 1 ))
        sh "$HERE/repack.sh" "$new" "${MU300_LAB_WORK:-/w}/rel-again" "$next" mu300-openwrt-rootfs >/dev/null 2>&1
        MU300_LAB_AGAIN="${MU300_LAB_WORK:-/w}/rel-again $next" run "again, to $next" device openwrt "$old/mu300-openwrt-rootfs.tar.gz" "$new" "$tag" "$out/again-device-openwrt.tsv"
        rm -rf "${MU300_LAB_WORK:-/w}/rel-again" ;;
    list)
        MU300_LAB_ENV=MU300_UPDATE_KEEP=list run list device ubuntu "$old/mu300-ubuntu-rootfs.tar.gz" "$new" "$tag" "$out/list-device-ubuntu.tsv" ;;
    *) echo "unknown scenario $s" >&2; exit 2 ;;
    esac
done
