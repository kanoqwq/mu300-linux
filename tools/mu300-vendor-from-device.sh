#!/system/bin/sh
# Add this device's proprietary modem/WCN runtime to a generic rootfs.
set -eu
R=${1:?rootfs destination required}
OS=${2:-openwrt}
if [ "${MU300_PLATFORM:-$(cat "$R/etc/mu300/platform" 2>/dev/null)}" = u30pro ]; then
    # Hardware inputs only. U30 Pro uses the native CP loader, never the F50
    # Bionic chroot, property snapshot, WCN files or partition-number map.
    [ "$OS" = openwrt ] || exit 2
    V=$R/opt/mu300/vendor/u30pro
    mkdir -p "$V/modem" "$V/firmware/aic8800" "$R/etc/mu300"
    echo u30pro > "$R/etc/mu300/platform"
    for name in modem_cp_info.xml modem_sp_info.xml modem_ch_info.xml; do
        [ -s "/vendor/etc/$name" ] || { echo "Missing U30 Pro modem XML: $name" >&2; exit 1; }
        cp -p "/vendor/etc/$name" "$V/modem/$name"
    done
    map=$V/modem/map.tsv
    [ -s "$map" ] || cp "${MU300_PAYLOAD_DIR:?}/u30pro-map.tsv" "$map"
    [ -s "$map" ] || { echo 'Missing U30 Pro logical partition map' >&2; exit 1; }
    awk '{print $1, "/dev/block/by-name/" $1}' "$map" > "$map.new"
    mv "$map.new" "$map"
    found=0
    for path in /odm/firmware/aic8800 /vendor/firmware/aic8800; do
        [ -d "$path" ] || continue
        cp -p "$path/"* "$V/firmware/aic8800/"
        found=1
        break
    done
    [ "$found" = 1 ] || { echo 'Missing this device AIC8800 firmware' >&2; exit 1; }
    exit 0
fi
case $OS in openwrt) F=$R/lib/firmware ;; ubuntu) F=$R/usr/lib/firmware ;; *) exit 2 ;; esac
mkdir -p "$F"
for f in wcnmodem.bin gnssmodem.bin wifi_board_config.ini wifi_board_config_ab.ini \
         bt_configure_pskey.ini bt_configure_rf.ini; do
    found=
    for d in /odm/firmware /vendor/firmware /vendor/etc; do
        [ -s "$d/$f" ] && { cp -p "$d/$f" "$F/$f"; found=1; break; }
    done
    [ -n "$found" ] || {
        if [ "${MU300_INSTALL_LANG:-en}" = zh ]; then
            echo "设备缺少固件 $f" >&2
        else
            echo "device firmware is missing $f" >&2
        fi
        exit 1
    }
done
A=$R/opt/mu300/android
S=${TMPDIR:-/data/local/tmp}/mu300-vendor-subset.$$.tar
rm -f "$S"
tar -chf "$S" \
  /apex/com.android.runtime/bin/linker64 /apex/com.android.runtime/lib64/bionic \
  /system/lib64/libcutils.so /system/lib64/libexpat.so /system/lib64/liblog.so \
  /system/lib64/libhardware_legacy.so /system/lib64/libc++.so /system/lib64/libbase.so \
  /system/lib64/libbinder.so /system/lib64/libbinder_ndk.so /system/lib64/libhidlbase.so \
  /system/lib64/libutils.so /system/lib64/android.system.suspend-V1-ndk.so \
  /system/lib64/libtrusty.so /system/lib64/libandroid_runtime_lazy.so /system/lib64/libvndksupport.so \
  /system/lib64/libz.so /system/lib64/libcrypto.so /system/lib64/libselinux.so /system/lib64/libpcre2.so \
  /system/lib64/libpackagelistparser.so /system/lib64/libprocessgroup.so /system/lib64/libcgrouprc.so \
  /vendor/lib64/libkernelbootcp.trusty.so /vendor/lib64/lib_crypto.so \
  /vendor/bin/modem_control /vendor/bin/cp_diskserver /vendor/bin/refnotify \
  /vendor/bin/sh /vendor/bin/toybox_vendor /vendor/bin/getprop \
  /vendor/etc/modem_cp_info.xml /vendor/etc/modem_sp_info.xml /vendor/etc/modem_ch_info.xml \
  /vendor/etc/cp_dump_info.xml /vendor/etc/ueventd.rc /dev/__properties__
rm -rf "$A"; mkdir -p "$A"; tar -xf "$S" -C "$A"; rm -f "$S"
[ -d "$A/dev/__properties__" ] || {
    if [ "${MU300_INSTALL_LANG:-en}" = zh ]; then
        echo '缺少 Android 属性快照' >&2
    else
        echo 'Android property snapshot is missing' >&2
    fi
    exit 1
}
mv "$A/dev/__properties__" "$A/dev-properties"; rmdir "$A/dev" 2>/dev/null || true
mkdir -p "$A/system/bin" "$A/linkerconfig"
ln -sfn /apex/com.android.runtime/bin/linker64 "$A/system/bin/linker64" 2>/dev/null || \
    cp "$A/apex/com.android.runtime/bin/linker64" "$A/system/bin/linker64"
: > "$A/linkerconfig/ld.config.txt"
chmod 755 "$A/vendor/bin/modem_control"
