#!/bin/sh
# Package the mainline build (build.sh + build-modules.sh) as a kernel bundle, laid out like the release's
# mu300-kernel.tar.gz so that mu300-update can install it:
#   upstream/make-bundle.sh OUT.tar.gz [KERNEL_BUNDLE_5.4]
#     ./Image                LK-loadable kernel (wrap-image.py: LK copies it to 0x80080000)
#     ./ramdisk-generic.lz4  boot/init, busybox, logdw, e2fsck and the modules of upstream/module-order.txt
#     ./modules/*.ko         every module - the kernel's own (=m) and the vendor ones - flat, for /lib/modules/<release>
#                            on the root filesystem (Ubuntu: extra/, indexed by depmod; OpenWrt: flat, for kmodloader)
#     ./kernel.release       the kernel's release string (uname -r)
#     ./devices              the devices it runs on (f50 u30air, and v50 when the kernel has its LED driver)
#     ./features             what it can do that older bundles could not (sdcard, linux-slot, gpt-root)
#     ./modules.builtin*     what the kernel has built in, for depmod/modprobe
# busybox and logdw are the static helpers of the 5.4 bundle (default: the newest one under release/).
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
U=$TOP/upstream
# the build to package: upstream/out (6.18), or MU300_UPSTREAM_OUT=upstream/out-7.2 (out-7.3) for another kernel
UO=${MU300_UPSTREAM_OUT:-$U/out}
OUT=${1:?usage: upstream/make-bundle.sh OUT.tar.gz [mu300-kernel.tar.gz]}
REF=${2:-$(ls -t "$TOP"/release/*/mu300-kernel.tar.gz 2>/dev/null | head -1)}
[ -f "$UO/Image" ] || { echo "no $UO/Image (run build.sh)" >&2; exit 1; }
[ -f "$REF" ] || { echo "no 5.4 kernel bundle for busybox/logdw (give one as the second argument)" >&2; exit 1; }
# the build's module tree and the staging directory must keep xt_HL.ko and xt_hl.ko apart (FINDINGS 212); a tree built
# on a file system that ignores case has lost one of each pair already (audit-bundle.py also finds that)
. "$U/case-sensitive.sh"
case_sensitive "$UO/modules" || exit 1

python3 "$U/audit-bundle.py" "$UO" "$U/mu300-mainline.config"
# the modules the devices' guard keeps from on-demand loading, against this build (FINDINGS 163)
python3 "$U/check-autoload-blocked.py" "$UO" "$TOP/rootfs/overlay/opt/mu300/lib/autoload-blocked" \
    "$TOP/rootfs/overlay/opt/mu300/bin/mu300-modprobe-guard"

krel=$(strings "$UO/Image" | sed -n 's/^Linux version \([^ ]*\) .*/\1/p' | head -1)
[ -n "$krel" ] || { echo "cannot read the kernel release from upstream/out/Image" >&2; exit 1; }
# a module left over from another kernel release would only fail on the device, at insmod
for m in "$UO"/modules/*.ko; do
    v=$(strings "$m" | sed -n 's/^vermagic=\([^ ]*\) .*/\1/p' | head -1)
    [ "$v" = "$krel" ] || { echo "$m is built for '$v', the kernel is '$krel'" >&2; exit 1; }
done
for m in $(cat "$U/module-order.txt"); do
    [ -f "$UO/modules/$m" ] || { echo "module-order.txt names $m, which was not built" >&2; exit 1; }
done

W=$(mktemp -d "${TMPDIR:-/tmp}/mu300-bundle.XXXXXX")
trap 'rm -rf "$W"' EXIT
case_sensitive "$W" || exit 1
tar -xzf "$REF" -C "$W" ./busybox ./logdw
# init's e2fsck (FINDINGS 182), from 5.4 bundles since it came; an older one: the ramdisk boots without it (the
# release audit refuses such a bundle)
tar -xzf "$REF" -C "$W" ./e2fsck 2>/dev/null || echo "warning: $REF has no e2fsck; this ramdisk will not check its root filesystem" >&2
mkdir "$W/b" "$W/b/modules"
python3 "$U/wrap-image.py" "$UO/Image" "$W/b/Image"
python3 "$TOP/boot/build-boot-image.py" --generic-ramdisk --modules "$UO/modules" \
    --module-order "$U/module-order.txt" --busybox "$W/busybox" --logdw "$W/logdw" \
    $([ -f "$W/e2fsck" ] && echo --e2fsck "$W/e2fsck") \
    --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" --out "$W/b/ramdisk-generic.lz4" >/dev/null
cp "$UO"/modules/*.ko "$W/b/modules/"
for f in modules.builtin modules.builtin.modinfo; do [ ! -f "$UO/$f" ] || cp "$UO/$f" "$W/b/"; done
cp "$UO/kernel.config" "$UO/modules.in-tree" "$W/b/"
( cd "$W/b/modules" && printf '%s\n' *.ko ) > "$W/b/modules.list"
echo "$krel" > "$W/b/kernel.release"
# the devices this kernel runs on; mu300-update and the installers check it (FINDINGS 33c: mainline kernels from
# before this file do not bring up the U30 Air's USB). The ZTE V50 (experimental, FINDINGS 95) only with a kernel
# that has its LED driver and its eMMC host kept awake (patch 0017): both came with it, the driver's option tells.
if grep -qx 'CONFIG_LEDS_AW9523B=y' "$UO/kernel.config"; then
    echo "f50 u30air v50" > "$W/b/devices"
else
    echo "f50 u30air" > "$W/b/devices"
fi
# what this kernel can do that an older bundle could not; mu300-update reads it before it replaces a kernel
# (sdcard: the card slot's host is probed, so a system on the SD card can boot with it; linux-slot: its init runs
# from slot a as well, when Android is on b; gpt-root: its init also boots a system in a GPT partition named mu300root,
# another installer's layout, FINDINGS 178)
printf 'sdcard\nlinux-slot\ngpt-root\n' > "$W/b/features"
tar -C "$W/b" -czf "$OUT" .
echo "$OUT: kernel $krel, $(ls "$W/b/modules" | wc -l | tr -d ' ') modules ($(du -sk "$W/b/modules" | cut -f1) KiB), ramdisk segment $(wc -c < "$W/b/ramdisk-generic.lz4" | tr -d ' ') bytes"
