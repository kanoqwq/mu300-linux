# Device side of the Magisk installer: what only the device can make for its Linux boot image and its systems - the
# misc blocks, the Android files modem_control needs, the firmware, the device ramdisk segment - and the image
# itself, built from Android's own boot image the way mu300-update rebuilds ours. Sourced after
# rootfs/overlay/opt/mu300/bin/mu300-update (MU300_LIB=1), whose bootimg_from_stock and byte helpers it uses.
# Writes only into the directories it is given. The files it collects are proprietary: they stay on this device.
LZ4_MAGIC=02214c18
FIRMWARE='wcnmodem.bin gnssmodem.bin wifi_board_config.ini wifi_board_config_ab.ini bt_configure_pskey.ini bt_configure_rf.ini'

hex_to_bytes() {  # hex_to_bytes HEX: the bytes (octal escapes: dash's printf has no \x)
    _h=$1
    while [ -n "$_h" ]; do
        _p=${_h%"${_h#??}"}; _h=${_h#??}
        printf "\\$(printf %o "0x$_p")"
    done
}
# CRC-32 (zlib) of the bytes of HEX, little endian as the block stores it: gzip ends every stream with exactly that
# value, which saves an implementation (mu300-next-boot does the same)
crc32_hex() { hex_to_bytes "$1" | gzip -c | tail -c 8 | head -c 4 | od -An -tx1 -v | tr -d ' \n'; }

# bc_block LIVE a|b A_META B_META: the live bootloader_control (64 hex digits) with that slot suffix, slot a's and
# slot b's first byte (priority | tries << 4 | successful << 7) and a new CRC - boot/build-boot-image.py's with_slots
bc_block() {
    case $2 in a) _s=5f610000 ;; b) _s=5f620000 ;; *) return 1 ;; esac
    _b="$_s$(printf %s "$1" | cut -c9-24)$3$(printf %s "$1" | cut -c27-28)$4$(printf %s "$1" | cut -c31-56)"
    printf '%s%s' "$_b" "$(crc32_hex "$_b")"
}
bc_valid() {  # bc_valid LIVE: 64 hex digits, magic BCAB, a CRC that matches
    [ ${#1} = 64 ] && [ "$(printf %s "$1" | cut -c9-16)" = 42434142 ] &&
        [ "$(crc32_hex "$(printf %s "$1" | cut -c1-56)")" = "$(printf %s "$1" | cut -c57-64)" ]
}
bc_files() {  # bc_files LIVE DIR: the four blocks of Task 1, byte for byte
    _l=$1 _d=$2
    for _x in 'slot-a a 9f 1e' 'slot-b-trial b 9e 2f' 'slot-b b 1e 9f' 'slot-a-trial a 2f 9e'; do
        set -- $_x
        hex_to_bytes "$(bc_block "$_l" "$2" "$3" "$4")" > "$_d/misc-bc-$1.bin" || return 1
        [ "$(fsize "$_d/misc-bc-$1.bin")" = 32 ] || return 1
    done
}

# collect_firmware DIR: the Wi-Fi/Bluetooth/GNSS firmware of this device; prints what is missing. The collect_*
# functions and vendor_overlay run under umask 022 whatever the caller has (see collect_subset).
collect_firmware() { ( umask 022; _collect_firmware "$@" ); }
_collect_firmware() {
    mkdir -p "$1"; _miss=
    for _f in $FIRMWARE; do
        _ok=
        for _d in ${MU300_FW_DIRS:-/odm/firmware /vendor/firmware /vendor/etc}; do
            if [ -s "$_d/$_f" ] && cp "$_d/$_f" "$1/$_f"; then _ok=1; break; fi
        done
        [ -n "$_ok" ] || _miss="$_miss $_f"
    done
    [ -z "$_miss" ] || { echo "$_miss"; return 1; }
}
# collect_subset LIST DIR: the files of LIST (android-vendor/subset-files.txt) from this device's /, links followed
# as extract-subset.sh follows them over adb, and the two things it adds. A file this firmware lacks is skipped; the
# two the modem cannot start without are checked. LIST is read before the cd into Android's root, so a relative
# path works too. Under umask 022 whatever the caller has: only the files are named, so tar makes vendor/etc and
# the other directories itself, and under a root shell with 077 they come out 0700: modem_control (uid 1000 by
# then) cannot reach its XML files in them ("can't get modem img!", and the CP is never loaded).
collect_subset() { ( umask 022; _collect_subset "$@" ); }
_collect_subset() {
    _list=$(grep -v '^#' "$1") || return 1
    rm -rf "$2"; mkdir -p "$2"
    (cd "${MU300_ANDROID_ROOT:-/}" && tar -chf - $_list 2>/dev/null) | tar -xf - -C "$2" 2>/dev/null
    mkdir -p "$2/system/bin" "$2/linkerconfig"
    ln -sfn /apex/com.android.runtime/bin/linker64 "$2/system/bin/linker64"
    : > "$2/linkerconfig/ld.config.txt"
    [ -s "$2/vendor/bin/modem_control" ] && [ -d "$2/dev/__properties__" ]
}
# collect_gpu LIST DIR: the Mali userspace and its library closure, all of it or nothing (a partial closure only
# fails later, inside OpenCL). LIST is read before the cd, as in collect_subset.
collect_gpu() { ( umask 022; _collect_gpu "$@" ); }
_collect_gpu() {
    _list=$(grep -v '^#' "$1") || return 1
    rm -rf "$2"; mkdir -p "$2"
    for _p in $_list; do
        [ -e "${MU300_ANDROID_ROOT:-}/$_p" ] || { rm -rf "$2"; return 1; }
    done
    (cd "${MU300_ANDROID_ROOT:-/}" && tar -chf - $_list) | tar -xf - -C "$2" || { rm -rf "$2"; return 1; }
}
# vendor_overlay OS FW SUBSET GPU OUT: what tools/vendor-overlay.py makes on a computer, for android-install.sh to
# unpack over the system: the firmware, the subset under opt/mu300/android with its property area moved out of
# dev/ (a devtmpfs is mounted there), the GPU files where the subset has none. Only leaf directories are named, so
# no entry replaces a directory the system has (Ubuntu's lib is a link to usr/lib). The copies it works on are
# proprietary: they are removed whether it succeeds or not.
vendor_overlay() {
    _o=$5.d
    rm -rf "$_o" "$5.gpu"
    ( umask 022; _vendor_overlay "$@" ) || { rm -rf "$_o" "$5" "$5.gpu"; return 1; }
    rm -rf "$_o" "$5.gpu"
}
_vendor_overlay() {
    case $1 in ubuntu) _fw=usr/lib/firmware ;; openwrt|openwrt-luci) _fw=lib/firmware ;; *) return 1 ;; esac
    mkdir -p "$_o/$_fw" "$_o/opt/mu300/android" || return 1
    cp "$2"/* "$_o/$_fw/" && cp -a "$3"/. "$_o/opt/mu300/android/" || return 1
    mv "$_o/opt/mu300/android/dev/__properties__" "$_o/opt/mu300/android/dev-properties" &&
        rm -rf "$_o/opt/mu300/android/dev" || return 1
    if [ -n "$4" ]; then
        # the list goes through a file, not a pipe: a loop at the end of a pipe runs in a subshell, and a copy
        # that failed there would not fail this function
        (cd "$4" && find . ! -type d) > "$5.gpu" || return 1
        while read -r _f; do
            _f=${_f#./}
            [ -e "$_o/opt/mu300/android/$_f" ] || [ -L "$_o/opt/mu300/android/$_f" ] && continue
            mkdir -p "$_o/opt/mu300/android/$(dirname "$_f")" && cp -a "$4/$_f" "$_o/opt/mu300/android/$_f" || return 1
        done < "$5.gpu"
    fi
    # Root's, all of it, as tools/vendor-overlay.py stores it: tar keeps the owner each copy has, and on Android
    # bionic belongs to system (uid 1000) and the programs to group shell (2000). On the installed system uid 1000
    # is the first login account, which could then rewrite the libc root's cp_diskserver loads. -h: the linker64
    # link itself, never what it points at on this device. (Not root: a test; the installer always is.)
    [ "$(id -u)" != 0 ] || chown -hR 0:0 "$_o" || return 1
    tar -czf "$5" -C "$_o" "$_fw" opt/mu300/android
}

# device_segment DIR OUT: DIR as a newc cpio owned by root, compressed to LZ4 legacy by magiskboot - the format the
# 5.4 kernel takes for every segment (gzip makes it panic) and that no shell tool on the device writes. The cpio is
# a copy of proprietary files: it goes whether this succeeds or not.
device_segment() {
    rm -f "$2" "$2.cpio" "$2.err" "$2.chk" "$2.t"
    _device_segment "$@" || { rm -f "$2" "$2.cpio" "$2.err" "$2.chk" "$2.t"; return 1; }
    rm -f "$2.cpio" "$2.err" "$2.chk"
}
_device_segment() {
    # cpio reports its block count on stderr: shown only when it fails (a cpio without -R, say)
    (cd "$1" && find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C sort | cpio -o -H newc -R 0:0) > "$2.cpio" 2> "$2.err" ||
        { cat "$2.err" >&2; return 1; }
    # magiskboot's reason, kept as cpio's is: on the device it is the only one there is
    "${MAGISKBOOT:-magiskboot}" compress=lz4_legacy "$2.cpio" "$2" >/dev/null 2> "$2.err" || { cat "$2.err" >&2; return 1; }
    [ "$(od -An -tx1 -N4 "$2" | tr -d ' \n')" = "$LZ4_MAGIC" ] || return 1
    # Only the magic and whole chunks. Magisk's lz4_lg variant ends with the uncompressed size (v30.7's lz4_legacy
    # does not), and in front of the generic segment the kernel would take that word for the size of a chunk: the
    # generic segment - init, busybox, the modules - would never unpack.
    _n=$(fsize "$2") _i=4
    while [ $((_i + 4)) -le "$_n" ]; do
        _c=$(u32 "$2" "$_i")
        [ "$_c" != 407642370 ] && [ $((_i + 4 + _c)) -le "$_n" ] || break  # 407642370: the magic, a next frame
        _i=$((_i + 4 + _c))
    done
    [ "$_i" -gt 4 ] || { echo "boot: magiskboot gave no whole LZ4 chunk" >&2; return 1; }
    if [ "$_i" != "$_n" ]; then head -c "$_i" "$2" > "$2.t" && mv "$2.t" "$2" || return 1; fi
    # and it must give back exactly the cpio
    "${MAGISKBOOT:-magiskboot}" decompress "$2" "$2.chk" >/dev/null 2>&1 && cmp -s "$2.chk" "$2.cpio" ||
        { echo "boot: the device ramdisk segment does not decompress to its cpio" >&2; return 1; }
}
# linux_boot_image STOCK BUNDLE DEVSEG OUT: the device segment first, then the bundle's generic one (init, busybox,
# modules); nothing in a generic segment has the name of a device-segment file, and mu300-update wants an LZ4 frame
# first. Then the header and AVB data of Android's own image around it. boot/build-boot-image.py with
# --append-ramdisk puts the checkout's init behind as a third segment (a release is usually older than the checkout
# that installs from it); here the bundle's generic segment carries the init, which is why the bundle and the
# installer come from the same release.
linux_boot_image() {
    [ "$(od -An -tx1 -N4 "$2/ramdisk-generic.lz4" | tr -d ' \n')" = "$LZ4_MAGIC" ] || return 1
    cat "$3" "$2/ramdisk-generic.lz4" > "$4/ramdisk" || return 1
    bootimg_from_stock "$1" "$2/Image" "$4/ramdisk" "$4"
}
