#!/bin/sh
# A release to update TO, made without a release build: the images of a published release with this checkout's
# updater, the carry helper and the manifests put into them the way the image builds do it (rootfs/assemble.sh,
# openwrt/build-rootfs.sh). Inside the container:
#   repack.sh RELEASE_DIR OUT_DIR TAG [ASSET...]      (ASSET: mu300-openwrt-rootfs, ...; default: all four)
# OUT_DIR gets the four images as release TAG (etc/mu300/image-version, their own manifest), this checkout's
# mu300-update, the kernel bundles of RELEASE_DIR (links) and SHA256SUMS: what lab.sh serves to an installed updater.
# What it does not have is everything else a release build would change (other overlay files, packages).
set -eu
SRC=$(cd "$(dirname "$0")/../.." && pwd)
rel=$1 out=$2 tag=$3
shift 3
only=" $* "
W=${MU300_LAB_WORK:-/w/lab}/repack
mkdir -p "$out" "$W"
gcc -O2 -static -o "$W/mu300-carry" "$SRC/tools/carry/mu300-carry.c"
for asset in mu300-ubuntu-rootfs:ubuntu-24.04 mu300-ubuntu-26.04-rootfs:ubuntu-26.04 mu300-openwrt-rootfs:openwrt \
             mu300-openwrt-luci-rootfs:openwrt-luci; do
    image=${asset#*:}; asset=${asset%%:*}
    case $only in "  "|*" $asset "*) ;; *) continue ;; esac
    R=$W/root
    rm -rf "$R"; mkdir -p "$R"
    tar --numeric-owner -xzpf "$rel/$asset.tar.gz" -C "$R"
    install -m 755 "$SRC/rootfs/overlay/opt/mu300/bin/mu300-update" "$R/opt/mu300/bin/mu300-update"
    install -m 755 "$W/mu300-carry" "$R/opt/mu300/bin/mu300-carry"
    # the manifests of the releases before this one: an image has those of its own system
    mkdir -p "$R/opt/mu300/lib/image-manifests"
    case $image in
        ubuntu-*) for m in ubuntu-24.04 ubuntu-26.04; do install -m 644 "$SRC/rootfs/overlay/opt/mu300/lib/image-manifests/$m.gz" "$R/opt/mu300/lib/image-manifests/"; done ;;
        *) install -m 644 "$SRC/rootfs/overlay/opt/mu300/lib/image-manifests/$image.gz" "$R/opt/mu300/lib/image-manifests/"
           install -m 755 "$SRC/openwrt/overlay/etc/profile.d/mu300-update.sh" "$R/etc/profile.d/mu300-update.sh" ;;
    esac
    echo "$tag" > "$R/etc/mu300/image-version"
    "$W/mu300-carry" manifest "$R" "$image" "$tag" | gzip -9n > "$W/manifest.gz"
    install -m 644 "$W/manifest.gz" "$R/etc/mu300/image-manifest.gz"
    ( cd "$R" && tar --numeric-owner -czf "$out/$asset.tar.gz" . )
    python3 "$SRC/tools/image-manifest.py" check "$out/$asset.tar.gz"
    rm -rf "$R"
done
for f in "$rel"/mu300-kernel*.tar.gz; do ln -sf "$f" "$out/"; done
cp "$SRC/rootfs/overlay/opt/mu300/bin/mu300-update" "$out/mu300-update"
( cd "$out" && sha256sum *.tar.gz mu300-update > SHA256SUMS )
ls -la "$out"
