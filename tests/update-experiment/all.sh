#!/bin/sh
# Every image of a release updated by every path (lab.sh), inside the container:
#   all.sh OLD_RELEASE_DIR NEW_RELEASE_DIR NEW_TAG OUT_DIR LABEL [ANDROID_INSTALL_SH] [IMAGES] [ZIP_DIR]
# OUT_DIR gets LABEL-device-<image>.tsv and LABEL-android-<image>.tsv (and with ZIP_DIR, the release's Magisk zips,
# LABEL-from-<image>.tsv: apply --from the zip), and one table per image, LABEL-<image>.md.
# IMAGES: of "ubuntu ubuntu-26.04 openwrt openwrt-luci" the ones to run (default: all four).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
old=$1 new=$2 tag=$3 out=$4 label=$5
script=${6:-$HERE/../../tools/android-install.sh}
images=${7:-ubuntu ubuntu-26.04 openwrt openwrt-luci}
zips=${8:-}
mkdir -p "$out"
for image in $images; do
    os=$image; ubuntu=
    case $image in ubuntu-26.04) os=ubuntu; ubuntu=26.04 ;; esac
    asset=mu300-$image-rootfs.tar.gz
    echo "== $image: the device's updater"
    sh "$HERE/lab.sh" device "$os" "$old/$asset" "$new" "$tag" "$out/$label-device-$image.tsv" || echo "FAILED"
    echo "== $image: the installers' update"
    sh "$HERE/lab.sh" android "$os" "$old/$asset" "$new/$asset" "$script" "$out/$label-android-$image.tsv" || echo "FAILED"
    from=
    if [ -n "$zips" ]; then
        # the zip of this system for the kernel the device is set to (Ubuntu 26.04 has none for 5.4)
        k=5.4; [ "$image" != ubuntu-26.04 ] || k=6.18
        zs=$image; [ "$image" != ubuntu ] || zs=ubuntu-24.04
        echo "== $image: apply --from the Magisk zip"
        MU300_LAB_KERNEL=$k sh "$HERE/lab.sh" from "$os" "$old/$asset" "$zips/mu300-magisk-$tag-$zs-k$k.zip" "$new" \
            "$out/$label-from-$image.tsv" || echo "FAILED"
        [ ! -f "$out/$label-from-$image.tsv" ] || from="apply --from the zip=$out/$label-from-$image.tsv"
    fi
    python3 "$HERE/experiment.py" table "mu300-update apply=$out/$label-device-$image.tsv" \
        "installer, Magisk zip=$out/$label-android-$image.tsv" ${from:+"$from"} > "$out/$label-$image.md"
    tail -n 2 "$out/$label-$image.md"
done
