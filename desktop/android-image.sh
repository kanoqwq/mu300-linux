#!/system/bin/sh
# Desktop-only image builder. Never changes GPT, boot or misc.
set -eu
P=${MU300_PAYLOAD_DIR:?}; BB=${MU300_BUSYBOX:?}; T=${MU300_INSTALL_TMP:?}
. "$P/android-loop.sh"
. "$P/desktop-settings.env"
case "$IMAGE_MIB" in ''|*[!0-9]*) exit 1 ;; esac
[ "$IMAGE_MIB" -ge 2048 ]
[ -x /system/bin/mke2fs ] || { echo 'Android /system/bin/mke2fs is required' >&2; exit 1; }
mkdir -p /data/mu300linux "$T/image-root"
[ ! -e /data/mu300linux/part-install.conf ] || { echo 'an internal installation is already pending' >&2; exit 1; }
image=$T/openwrt.ext4.new
[ ! -e "$image" ] || { echo 'unfinished image exists; inspect it before retrying' >&2; exit 1; }
[ "$DESKTOP_MODE" != data-image ] || [ ! -e /data/mu300linux/openwrt.ext4.backup ] || { echo 'previous image backup exists; move it before reinstalling' >&2; exit 1; }
free=$(df -Pk /data | awk 'END {print $4}')
[ "$free" -gt "$((IMAGE_MIB * 1024 + 524288))" ] || { echo 'insufficient userdata staging space' >&2; exit 1; }
truncate -s "$((IMAGE_MIB * 1048576))" "$image"
/system/bin/mke2fs -t ext4 -F -b 4096 -m 1 -E lazy_itable_init=1,lazy_journal_init=1,nodiscard -L mu300root "$image"
loop=; loop_attached=0; mounted=0
cleanup() {
    rc=$?
    trap - EXIT HUP INT TERM
    if [ "$mounted" = 1 ]; then
        umount "$T/image-root" || { echo 'image still mounted; loop retained' >&2; exit 1; }
    fi
    [ "$loop_attached" = 0 ] || losetup -d "$loop" || exit 1
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
loop=$(desktop_free_loop)
losetup "$loop" "$image"
loop_attached=1
mount -t ext4 "$loop" "$T/image-root"
mounted=1
M=$T/image-root; R=$M/openwrt
mkdir -p "$R"
tar -xzpf "$P/mu300-openwrt.tar.gz" -C "$R"
(cd "$R" && sha256sum -c "$P/rootfs-critical.sha256")
# Use this device's libraries, firmware and Android property snapshot.
rm -rf "$R/opt/mu300/android"
sh "$P/mu300-vendor-from-device.sh" "$R" openwrt
mkdir -p "$M/.mu300" "$M/boot" "$R/etc/mu300"
echo openwrt > "$M/.mu300/boot-os"; echo 5 > "$M/.mu300/boot-attempts"; echo 0 > "$M/.mu300/boot-tries"
echo "$KERNEL" > "$M/boot/kernel"; echo "$KERNEL" > "$M/boot/installed.kernel"
echo linux > "$R/etc/mu300/default-boot"
echo "$DESKTOP_MODE" > "$R/etc/mu300/desktop-mode"
if [ -z "${SSID:-}" ]; then
    X=/data/misc/apexdata/com.android.wifi/WifiConfigStoreSoftAp.xml
    SSID=$(sed -n 's/.*<string name="WifiSsid">&quot;\(.*\)&quot;<\/string>.*/\1/p; s/.*<string name="WifiSsid">\([^&<]*\)<\/string>.*/\1/p' "$X" 2>/dev/null | head -1)
    WIFI_KEY=$(sed -n 's/.*<string name="Passphrase">\(.*\)<\/string>.*/\1/p' "$X" 2>/dev/null | head -1 | sed "s/&amp;/\&/g; s/&lt;/</g; s/&gt;/>/g; s/&quot;/\"/g; s/&apos;/'/g")
    [ "${#WIFI_KEY}" -ge 8 ] || SSID=
fi
if [ -n "${SSID:-}" ]; then
    printf 'SSID=%s\nPSK=%s\nBAND=5\nCHANNEL=auto\nCOUNTRY=TR\n' "$SSID" "$WIFI_KEY" > "$R/etc/mu300/hotspot.conf"
    chmod 600 "$R/etc/mu300/hotspot.conf"
fi
if [ -n "${APN:-}" ]; then
    # First-boot defaults are UCI, shared by LuCI, netifd and APN selection.
    mkdir -p "$R/etc/uci-defaults"
    printf '#!/bin/sh\nuci set network.wan.apn=%s\nuci commit network\n' "$APN" > "$R/etc/uci-defaults/99-desktop-apn"
    chmod 755 "$R/etc/uci-defaults/99-desktop-apn"
fi
if [ -n "${PWHASH:-}" ]; then
    sed -i "s|^root:[^:]*:|root:$PWHASH:|" "$R/etc/shadow"
    rm -f "$R/etc/.mu300-accounts-from-image"
fi
[ -x "$R/sbin/init" ] || [ -L "$R/sbin/init" ]
if [ "$(cat "$R/etc/mu300/platform" 2>/dev/null)" = u30pro ]; then
    [ -s "$R/opt/mu300/vendor/u30pro/modem/map.tsv" ] &&
        [ -s "$R/opt/mu300/vendor/u30pro/modem/modem_cp_info.xml" ] &&
        [ -x "$R/usr/sbin/u30-modemd" ]
else
    [ -s "$R/lib/firmware/wcnmodem.bin" ] && [ -x "$R/opt/mu300/android/vendor/bin/modem_control" ]
fi
(cd "$M" && find . -type f -exec sha256sum {} +) > "$T/image.sha256"
umount "$M"; mounted=0
losetup -d "$loop"; loop_attached=0
# The old loop may now belong to another process. Allocate again and only
# mark ownership after a successful attach, so a failed bind never detaches it.
loop=$(desktop_free_loop)
losetup -r "$loop" "$image"
loop_attached=1
mount -t ext4 -o ro,noload "$loop" "$M"
mounted=1
(cd "$M" && sha256sum -c "$T/image.sha256" >/dev/null)
umount "$M"; mounted=0
losetup -d "$loop"; loop_attached=0
trap - EXIT HUP INT TERM
if [ "$DESKTOP_MODE" = data-image ]; then
    [ ! -e /data/mu300linux/openwrt.ext4 ] || mv /data/mu300linux/openwrt.ext4 /data/mu300linux/openwrt.ext4.backup
    mv "$image" /data/mu300linux/openwrt.ext4
    echo 'IMAGE-INSTALLED data-image'
elif [ "$DESKTOP_MODE" = part-stage ]; then
    gzip -1 -c "$image" > /data/mu300linux/part-install.img.gz.tmp
    gzip -t /data/mu300linux/part-install.img.gz.tmp
    mv /data/mu300linux/part-install.img.gz.tmp /data/mu300linux/part-install.img.gz
    rm -f "$image"
    # install-part's firmware archive is metadata only: the complete device
    # runtime is already inside the checked container image.
    mkdir -p "$T/metadata/.mu300"
    echo openwrt > "$T/metadata/.mu300/boot-os"
    tar -cf /data/mu300linux/device-files.tar -C "$T/metadata" .
    echo 'IMAGE-INSTALLED part-stage'
else
    echo 'IMAGE-INSTALLED partition-write'
fi
