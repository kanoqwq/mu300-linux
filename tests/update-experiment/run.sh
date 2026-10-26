#!/bin/sh
# What an update keeps, measured on real release images in a container (docs/FINDINGS.md 85):
#   tests/update-experiment/run.sh LABEL OLD_RELEASE_DIR NEW_RELEASE_DIR NEW_TAG OUT_DIR
# OLD_RELEASE_DIR and NEW_RELEASE_DIR hold the assets of two releases as published (mu300-*-rootfs.tar.gz,
# mu300-kernel*.tar.gz, mu300-update, SHA256SUMS). For each of the four images the old one is installed into an ext4
# filesystem, some sixty kinds of user state are planted in it (experiment.py: files in every place, odd names,
# owners, extended attributes, hard links, a sparse file, changed and deleted image files, accounts, a package), and
# it is updated to NEW_TAG twice over: by the installed system's own mu300-update, and by the installers' update
# (tools/android-install.sh under mksh and toybox). OUT_DIR gets LABEL-<image>.md, the table of what was kept,
# changed, set aside or lost by each path, and the files behind it.
#   MU300_LAB_REPACK=1               NEW_RELEASE_DIR is a published release, and what is measured is this checkout: its
#                                    images are repacked with this checkout's updater, helper and manifests as NEW_TAG
#                                    (repack.sh: what a release build would do to them, without one)
#   MU300_LAB_ANDROID_INSTALL=FILE   the android-install.sh to run (default: this checkout's; for a published release
#                                    its own: git show TAG:tools/android-install.sh > FILE)
#   MU300_LAB_SCENARIOS="link move"  also the updates that are not the plain one (scenarios.sh; "all" for every one)
#   MU300_LAB_IMAGES="openwrt"       of ubuntu ubuntu-26.04 openwrt openwrt-luci, the ones to run
#   MU300_LAB_ZIPS=DIR               the Magisk zips of NEW_TAG (tools/make-magisk-zips.sh): a third path, the old system
#                                    updated offline by "mu300-update apply --from" the zip of its system
#   MU300_LAB_HOST_WORK=DIR          the container's working directory (the filesystems in files, some GB) on the
#                                    computer instead of in docker's own disk
# Needs docker; the container is privileged (it mounts a filesystem and changes root into the installed system) and
# is removed at the end. Nothing is written to the release directories.
set -eu
[ $# = 5 ] || { sed -n '2,24s/^# \{0,1\}//p' "$0" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/../.." && pwd)
label=$1 old=$(cd "$2" && pwd) new=$(cd "$3" && pwd) tag=$4
mkdir -p "$5"; out=$(cd "$5" && pwd)
docker build -q -t mu300-update-lab "$HERE" >/dev/null
c=mu300-update-lab-$$
script=/src/tools/android-install.sh
set -- -v "$TOP":/src:ro -v "$old":/old:ro -v "$new":/new:ro -v "$out":/out
zips=
if [ -n "${MU300_LAB_ZIPS:-}" ]; then set -- "$@" -v "$(cd "$MU300_LAB_ZIPS" && pwd)":/zips:ro; zips=/zips; fi
if [ -n "${MU300_LAB_HOST_WORK:-}" ]; then mkdir -p "$MU300_LAB_HOST_WORK"; set -- "$@" -v "$(cd "$MU300_LAB_HOST_WORK" && pwd)":/w; fi
if [ -n "${MU300_LAB_ANDROID_INSTALL:-}" ]; then
    set -- "$@" -v "$(cd "$(dirname "$MU300_LAB_ANDROID_INSTALL")" && pwd)/$(basename "$MU300_LAB_ANDROID_INSTALL")":/android-install.sh:ro
    script=/android-install.sh
fi
docker run -d --name "$c" --privileged "$@" mu300-update-lab sleep infinity >/dev/null
trap 'docker rm -f "$c" >/dev/null 2>&1 || true' EXIT
to=/new
if [ "${MU300_LAB_REPACK:-0}" = 1 ]; then
    docker exec "$c" sh /src/tests/update-experiment/repack.sh /new /w/release "$tag" >/dev/null
    to=/w/release
fi
docker exec "$c" sh /src/tests/update-experiment/all.sh /old "$to" "$tag" /out "$label" "$script" "${MU300_LAB_IMAGES:-ubuntu ubuntu-26.04 openwrt openwrt-luci}" "$zips"
case ${MU300_LAB_SCENARIOS:-} in
    '') ;;
    all) docker exec "$c" sh /src/tests/update-experiment/scenarios.sh /old "$to" "$tag" /out ;;
    *) # shellcheck disable=SC2086
       docker exec "$c" sh /src/tests/update-experiment/scenarios.sh /old "$to" "$tag" /out $MU300_LAB_SCENARIOS ;;
esac
echo "the tables: $out/$label-<image>.md"
