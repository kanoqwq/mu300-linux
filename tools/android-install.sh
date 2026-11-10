#!/system/bin/sh
# Device side of install.sh (runs as root on Android). Settings come from /data/local/tmp/mu300-install.env:
#   SD_MODE=0          rootfs in the free eMMC region after the last GPT partition
#   SD_MODE=1 SD_DEV   rootfs on the full selected TF partition, ext4 mu300sd
#   OFF SIZE           free eMMC region (bytes) after the last GPT partition, as strings (SD_MODE=0)
#   OFF_S SIZE_S       the same in 512-byte sectors (Android's mksh has 32-bit arithmetic: never compute with bytes)
#   FORMAT=0|1         create ext4 mu300root internally or mu300sd on TF
#   OSES="ubuntu openwrt"  systems to (re)install from /data/local/tmp/mu300-<os>.tar.gz
#                      (plus mu300-vendor-<os>.tar.gz with the device's own vendor files for prebuilt images)
#   WIPE_LEGACY=0|1    remove a first-generation Ubuntu that lives directly in the filesystem root
#   UPDATE=0|1         keep the settings and user data of the systems being reinstalled
#   BOOT_OS            system started by the initramfs
#   DEFAULT_LINUX=0|1  keep booting Linux (otherwise every Linux boot is one-shot and returns to Android)
#   BOOT_ATTEMPTS=1-6  with DEFAULT_LINUX=1: failed boots in a row before Android (.mu300/boot-attempts)
#   PWHASH             SHA-512 crypt hash for the "ubuntu" (Ubuntu) and "root" (OpenWrt) accounts
#   IMPORT_HOTSPOT=0|1 copy Android's hotspot SSID/passphrase into each system
#   KERNEL=5.4|6.18|7.2  the kernel in the new boot image; mu300-update keeps installing that one (boot/kernel)
set -e
umask 022
T=${MU300_INSTALL_TMP:-/data/local/tmp}
P=${MU300_PAYLOAD_DIR:-$T}
. "$P/mu300-install.env"
M=$T/mu300root
say() { echo "[device] $*"; }
say_i18n() { if [ "${MU300_INSTALL_LANG:-en}" = zh ]; then say "$1"; else say "$2"; fi; }

# Refuse incompatible slots before formatting, including direct desktop installs.
if [ "${SYNC_BOOTCHAIN:-0}" = 1 ]; then
    sh "$P/android-bootchain.sh" check || exit 1
fi

if [ "${SD_MODE:-0}" = 1 ]; then
    . "$P/tf-storage.sh"
    mkdir -p "$T"
    if [ -n "${MU300_TF_STATE:-}" ]; then
        tf_read_requirements "$P/rootfs.requirements" || { tf_die 'invalid rootfs.requirements'; exit 1; }
    fi
    R=${SD_DEV:?SD_DEV is not set}
    [ -b "$R" ] || { say_i18n "找不到块设备 $R（TF 卡是否已插入？）" \
                                    "no block device $R (is the TF card inserted?)"; exit 1; }
    # Never touch an eMMC free-region candidate from the SD branch.
    tf_release "$R"
    tf_read_super "$R" || { tf_die 'failed/short TF superblock read'; exit 1; }
    magic=$(dd if="$T/tf-super" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')
    label=$(dd if="$T/tf-super" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000')
    if [ "$FORMAT" = 1 ]; then
        [ "$magic" != 53ef ] || [ "$label" = mu300sd ] || {
            say_i18n "拒绝格式化非 mu300sd 的 ext4 文件系统 '$label'" \
                     "refusing to format foreign ext4 filesystem '$label'"; exit 1; }
        say_i18n "正在 $R 创建 ext4 mu300sd（使用所选分区全部容量）" \
                 "creating ext4 mu300sd on $R (full selected partition capacity)"
        # make_ext4fs creates large cards very slowly and can fail on 128 GiB
        # media. Use e2fsprogs' lazy metadata initialization and one inode per
        # MiB (still plenty for OpenWrt) so formatting touches only metadata,
        # not the whole card. nodiscard avoids a full-card erase on slow TFs.
        # Magisk runs customize.sh in BusyBox standalone mode: bare mke2fs
        # resolves to BusyBox's ext2-only applet, even when Android's real
        # e2fsprogs is installed. An absolute path bypasses that interception.
        if [ -x /system/bin/mke2fs ]; then
            # Explicit minimum inode count for small TFs; large cards retain
            # the sparse 1 MiB/inode layout. No filesystem-size argument/cap.
            inode_args=
            if [ -n "${MU300_TF_STATE:-}" ]; then
                tf_read_requirements "$P/rootfs.requirements" || { tf_die 'invalid rootfs.requirements'; exit 1; }
                [ $((tf_sectors / 2048)) -ge $((required_inodes + 1024)) ] || inode_args="-N $((required_inodes + 1024))"
            fi
            /system/bin/mke2fs -t ext4 -F -b 4096 -m 0 -i 1048576 \
                $inode_args \
                -E lazy_itable_init=1,lazy_journal_init=1,nodiscard \
                -L mu300sd "$R" || { say_i18n '快速创建 ext4 文件系统失败' \
                                                         'optimized ext4 format failed'; exit 1; }
        else
            say_i18n '缺少 Android 的 /system/bin/mke2fs，无法可靠地格式化 TF 卡' \
                     'Android /system/bin/mke2fs is required for reliable TF formatting'
            exit 1
        fi
    elif [ "$magic" != 53ef ] || [ "$label" != mu300sd ]; then
        say_i18n "$R 上没有 mu300sd 文件系统（需要 FORMAT=1）" \
                 "no mu300sd filesystem on $R (FORMAT=1 is required)"; exit 1
    fi
    mkdir -p "$M"
    mount -t ext4 -o noatime "$R" "$M"
    # On failure do not global-sync or launch a second I/O request into a
    # possibly wedged card. The bounded Magisk worker leaves a reboot lock.
    if [ -n "${MU300_TF_STATE:-}" ]; then
        free_kib=$(df -Pk "$M" | awk 'END {print $4}')
        free_inodes=$(df -Pi "$M" | awk 'END {print $4}')
        [ "$free_kib" -ge "$required_kib" ] && [ "$free_inodes" -ge "$required_inodes" ] || {
            tf_die 'insufficient usable space/inodes after format'; exit 1;
        }
    fi
else
# --- the region must not overlap any partition (checked again here, on the device itself)
end=0
for p in /sys/block/mmcblk0/mmcblk0p*; do
    e=$(( $(cat $p/start) + $(cat $p/size) ))
    [ $e -gt $end ] && end=$e
done
disk=$(cat /sys/block/mmcblk0/size)
[ "$SIZE_S" -ge 8 ] && [ "$OFF_S" -ge $end ] && [ "$OFF_S" -lt "$disk" ] && \
    [ "$SIZE_S" -le $((disk - 34 - OFF_S)) ] || { say "region overlaps partitions or the backup GPT"; exit 1; }

attach() {
    for o in /sys/block/loop*/loop/offset; do
        [ "$(cat $o 2>/dev/null)" = "$OFF" ] && { say "region already attached (${o%/loop/offset})"; exit 1; }
    done
    f=$(losetup -f 2>&1 | grep -o '/dev/block/loop[0-9]*' | head -1); n=${f##*loop}
    L=/dev/block/loop$n
    [ -b "$L" ] || mknod "$L" b $(cut -d: -f1 /sys/block/loop$n/dev) $(cut -d: -f2 /sys/block/loop$n/dev) 2>/dev/null || [ -b "$L" ]
    losetup -o $OFF -S $SIZE "$L" /dev/block/mmcblk0
    [ "$(cat /sys/block/loop$n/loop/offset)" = "$OFF" ] && [ "$(blockdev --getsize64 $L)" = "$SIZE" ] || { losetup -d $L; say "loop setup mismatch"; exit 1; }
}

sb() { dd if=/dev/block/mmcblk0 bs=512 skip=$OFF_S count=4 2>/dev/null | dd bs=1 skip=$1 count=$2 2>/dev/null; }
magic=$(sb 1080 2 | od -An -tx1 | tr -d ' \n')
label=$(sb 1144 16 | tr -d '\000')
if [ "$FORMAT" = 1 ]; then
    if [ "$magic" = 53ef ] && [ "$label" != mu300root ]; then say "refusing to format: foreign ext4 ($label) in the region"; exit 1; fi
    attach
    say "creating ext4 mu300root on $L ($((SIZE_S / 2048)) MiB)"
    mke2fs -t ext4 -L mu300root -F "$L" >/dev/null
    losetup -d "$L"
elif [ "$magic" != 53ef ] || [ "$label" != mu300root ]; then
    say "no mu300root filesystem in the region (run with FORMAT=1)"; exit 1
fi

MU300_OFF=$OFF MU300_SIZE=$SIZE sh $T/android-mount-mu300root.sh $M
trap 'sync; sh $T/android-mount-mu300root.sh -u $M >/dev/null 2>&1; true' EXIT
fi

# --- bootchain begin
# The Linux slot's uboot, sml, teecfg, trustos and vbmeta become the ones of Android's slot (android-bootchain.sh),
# before anything else is installed: a failure stops here, and the installers then write no boot image. The images
# the Linux slot held go onto the Linux filesystem first (.mu300/bootchain/<date>), and for install.sh/install.ps1
# also to $T/mu300-bootchain-backup, which they pull into their work directory.
if [ "${SYNC_BOOTCHAIN:-0}" = 1 ]; then
    bcdir=$M/.mu300/bootchain/$(date +%Y%m%d-%H%M%S)
    rm -rf $T/mu300-bootchain-backup
    bcout=$(sh "${P:-$T}/android-bootchain.sh" sync "$bcdir") && bcrc=0 || bcrc=$?
    echo "$bcout" | grep -v '^BOOTCHAIN-' | sed 's/^/[device] /'
    if [ $bcrc != 0 ]; then
        say "$(echo "$bcout" | sed -n 's/^BOOTCHAIN-[A-Z]*: //p')"
        say "the boot chain was not synced; no system was installed (MU300_SYNC_BOOTCHAIN=0 skips this step)"
        exit 1
    fi
    if [ -z "${MU300_DEVICE_WORK:-}" ] && [ -d "$bcdir" ]; then
        cp -R "$bcdir" $T/mu300-bootchain-backup && chmod -R a+rX $T/mu300-bootchain-backup || true
    fi
fi
# --- bootchain end

if [ "$WIPE_LEGACY" = 1 ] && { [ -x $M/lib/systemd/systemd ] || [ -L $M/lib ]; }; then
    say "removing the root-level Ubuntu"
    for e in $M/* $M/.[!.]*; do
        case "${e##*/}" in lost+found|.mu300|ubuntu|openwrt) ;; *) rm -rf "$e" ;; esac
    done
fi

ssid=; psk=
if [ "$IMPORT_HOTSPOT" = 1 ]; then
    X=/data/misc/apexdata/com.android.wifi/WifiConfigStoreSoftAp.xml
    ssid=$(sed -n 's/.*<string name="WifiSsid">&quot;\(.*\)&quot;<\/string>.*/\1/p; s/.*<string name="WifiSsid">\([^&<]*\)<\/string>.*/\1/p' $X 2>/dev/null | head -1)
    psk=$(sed -n 's/.*<string name="Passphrase">\(.*\)<\/string>.*/\1/p' $X 2>/dev/null | head -1 | sed "s/&amp;/\&/g; s/&lt;/</g; s/&gt;/>/g; s/&quot;/\"/g; s/&apos;/'/g")
    [ -n "$ssid" ] && [ ${#psk} -ge 8 ] || { say_i18n "无法读取可用的 Android 热点配置，将生成随机密码" \
                                                    "no usable Android hotspot config, a random password will be generated"; ssid=; psk=; }
fi

# --- user-packages begin
# Packages installed on the device are not in the release's image, and an update puts the image in place while it
# keeps /etc/config (OpenWrt) and /etc/mu300: their programs were gone and their settings stayed - an absent DNS extension left
# the device without DNS (#116). So before the switch the update writes down what was added - the packages asked for
# by hand (OpenWrt: /etc/apk/world; Ubuntu: dpkg's packages that apt did not pull in by itself) less the image's
# own set (etc/mu300/image-packages, written when the image is built) - into etc/mu300/user-packages/ of the new
# system, and sets the UCI files of those packages aside (etc/mu300/orphaned-config/): a package that is not there
# leaves no settings behind. At its first boot the new system installs them again (mu300-user-packages, a service)
# and puts their settings back. tools/android-install.sh (the installers' update) has a copy of this block; tests
# hold the two to one text. It runs on Android too: sh, awk, sed, grep, sort - nothing else, and safe under set -e.
UPK_DIR=etc/mu300/user-packages
UPK_ORPH=etc/mu300/orphaned-config
# what is never installed again: kernel modules (the feed's are for another kernel), and what the images take out on
# purpose (sysupgrade would flash the eMMC; ujail crash-loops services on 5.4)
upk_skip() {
    case $1 in
        kmod-*|kernel|procd-ujail|procd-seccomp|luci-app-attendedsysupgrade|attendedsysupgrade-common|owut) return 0 ;;
        linux-image-*|linux-modules-*|linux-headers-*|linux-generic*|linux-firmware) return 0 ;;
    esac
    return 1
}
upk_kind() { case $1 in ubuntu) echo ubuntu ;; *) echo openwrt ;; esac; }
# upk_dpkg ROOT [manual]: dpkg's installed packages in ROOT; with manual, less those apt installed as dependencies
# (what apt-mark showmanual says, read from the files: ROOT need not be the running system)
upk_dpkg() {
    [ -f "$1/var/lib/dpkg/status" ] || return 0
    { [ "${2:-}" != manual ] || cat "$1/var/lib/apt/extended_states" 2>/dev/null || :; echo '@dpkg'; cat "$1/var/lib/dpkg/status"; } |
        awk '$0 == "@dpkg" { s = 1; next }
             $1 == "Package:" { p = $2; next }
             !s && $1 == "Auto-Installed:" && $2 == 1 { auto[p] = 1; next }
             s && $1 == "Status:" && $3 == "ok" && $4 == "installed" { inst[p] = 1 }
             END { for (p in inst) if (!(p in auto)) print p }' | sort
}
# upk_manual ROOT KIND: the packages asked for by name (OpenWrt: the world file, versions and pins taken off)
upk_manual() {
    if [ "$2" = ubuntu ]; then upk_dpkg "$1" manual; return 0; fi
    [ -f "$1/etc/apk/world" ] || return 0
    sed 's/[<>=~@ ].*//' "$1/etc/apk/world" | awk 'NF' | sort -u
}
# upk_installed ROOT KIND: every installed package
upk_installed() {
    if [ "$2" = ubuntu ]; then upk_dpkg "$1"; return 0; fi
    [ -f "$1/lib/apk/db/installed" ] || return 0
    sed -n 's/^P://p' "$1/lib/apk/db/installed" | sort -u
}
# upk_txt_names FILE: the names in etc/mu300/packages.txt ("apk list --installed": name-version-rN arch ...)
upk_txt_names() {
    [ -f "$1" ] || return 0
    awk '{print $1}' "$1" | sed 's/-r[0-9][0-9]*$//; s/-[^-]*$//'
}
# upk_pending DIR: the packages of a record not installed again yet (nor given up on)
upk_pending() {
    [ -f "$1/wanted" ] || return 0
    { for _uf in done failed; do [ ! -f "$1/$_uf" ] || sed 's/^/x /' "$1/$_uf"; done; sed 's/^/w /' "$1/wanted"; } |
        awk '$1 == "x" { d[$2] = 1; next } NF > 1 && !($2 in d) { print $2 }'
}
# What belongs to the new image, not to the device, in the directories an update keeps: its version and package
# lists in etc/mu300 (an update carried the old ones over: every update then reported the version it replaced), and
# the names of its own files in etc/config (never set aside). image_own_save NEW before the settings are copied,
# image_own_restore NEW after; upk_record removes what they keep in NEW/.mu300-image.
image_own_save() {
    rm -rf "$1/.mu300-image"; mkdir -p "$1/.mu300-image" || return 1
    for _uf in image-version image-packages packages.txt release.json model; do
        [ ! -f "$1/etc/mu300/$_uf" ] || cp "$1/etc/mu300/$_uf" "$1/.mu300-image/$_uf" || return 1
    done
    for _uf in "$1"/etc/config/*; do [ ! -e "$_uf" ] || echo "${_uf##*/}"; done > "$1/.mu300-image/config"
}
image_own_restore() {
    [ -d "$1/.mu300-image" ] || return 0
    mkdir -p "$1/etc/mu300" || return 1
    for _uf in image-version image-packages packages.txt release.json; do
        if [ -f "$1/.mu300-image/$_uf" ]; then cp "$1/.mu300-image/$_uf" "$1/etc/mu300/$_uf" || return 1
        # a list of the old image would describe the wrong one (the version stays: "unknown" says less)
        elif [ "$_uf" != image-version ]; then rm -f "$1/etc/mu300/$_uf"; fi
    done
    # The kept directory may predate product identity. Introduce the image's
    # missing default, but preserve any existing file or user-managed symlink.
    if [ ! -e "$1/etc/mu300/model" ] && [ ! -L "$1/etc/mu300/model" ] &&
       [ -f "$1/.mu300-image/model" ]; then
        cp "$1/.mu300-image/model" "$1/etc/mu300/model" || return 1
    fi
}
# upk_os_codename ROOT: Ubuntu's release name (noble, ...)
upk_os_codename() {
    sed -n 's/^VERSION_CODENAME=//p' "$1/usr/lib/os-release" 2>/dev/null | tr -d '"' | head -n 1
}
# upk_apt_keep OLD NEW [checked]: Ubuntu keeps only parts of /etc, and apt's sources were not among them: a PPA's
# packages could not come back. The source files added in etc/apt/sources.list.d are copied where the new image
# has no file of that name (the image's own are never replaced), with the keys they need: one in etc/apt/keyrings
# or usr/share/keyrings that a kept source names, and etc/apt/trusted.gpg.d's - each only where the new image has
# no file of that name. checked: the first boot tries apt-get update and disables a kept source that makes it fail
# (mu300-user-packages); without it (nothing to install again) a source of another Ubuntu release (24.04 -> 26.04)
# is copied disabled (NAME.disabled, which apt skips) and said so. NEW/.mu300-image/apt-kept: the sources kept on.
upk_apt_keep() {
    _ua=$2/.mu300-image/apt-kept _ul=
    : > "$_ua" || return 1
    _uoc=$(upk_os_codename "$1") _unc=$(upk_os_codename "$2")
    for _uf in "$1"/etc/apt/sources.list.d/*.list "$1"/etc/apt/sources.list.d/*.sources; do
        [ -f "$_uf" ] || continue
        _ur=etc/apt/sources.list.d/${_uf##*/}
        { [ ! -e "$2/$_ur" ] && [ ! -L "$2/$_ur" ]; } || continue
        mkdir -p "$2/etc/apt/sources.list.d" || return 1
        if [ -z "${3:-}" ] && [ "$_uoc" != "$_unc" ]; then
            cp -p "$_uf" "$2/$_ur.disabled" || return 1
            echo "  apt: /$_ur is for Ubuntu $_uoc, this is $_unc: kept disabled (/$_ur.disabled)"
        else
            cp -p "$_uf" "$2/$_ur" || return 1
            echo "$_ur" >> "$_ua"
            echo "  apt: /$_ur kept"
        fi
        _ul="$_ul $_uf"
    done
    [ -n "$_ul" ] || return 0
    # their pinning (etc/apt/preferences.d), where the new image has no file of that name; a pin whose source is
    # turned off later does nothing on its own (mu300-user-packages status names it). NEW/.mu300-image/apt-prefs.
    : > "$2/.mu300-image/apt-prefs" || return 1
    for _uf in "$1"/etc/apt/preferences.d/*; do
        [ -f "$_uf" ] || continue
        _ur=etc/apt/preferences.d/${_uf##*/}
        { [ ! -e "$2/$_ur" ] && [ ! -L "$2/$_ur" ]; } || continue
        mkdir -p "$2/etc/apt/preferences.d" && cp -p "$_uf" "$2/$_ur" || return 1
        echo "$_ur" >> "$2/.mu300-image/apt-prefs"
        echo "  apt: /$_ur kept"
    done
    # shellcheck disable=SC2086
    _us=$(cat $_ul 2>/dev/null)
    for _uf in "$1"/etc/apt/keyrings/* "$1"/usr/share/keyrings/* "$1"/etc/apt/trusted.gpg.d/*; do
        [ -f "$_uf" ] || continue
        _ur=${_uf#"$1"/}
        { [ ! -e "$2/$_ur" ] && [ ! -L "$2/$_ur" ]; } || continue
        case $_ur in
            etc/apt/trusted.gpg.d/*) ;;
            *) printf '%s\n' "$_us" | grep -qF "/$_ur" || continue ;;
        esac
        mkdir -p "$2/${_ur%/*}" && cp -p "$_uf" "$2/$_ur" || return 1
    done
}
# upk_units OLD: "PACKAGE UNIT enabled|disabled|masked" for the systemd units the packages named on stdin have in
# OLD (dpkg's file lists), as OLD had them - mu300-user-packages puts that back after it installs them again
upk_units() {
    while read -r _up; do
        for _ul in "$1/var/lib/dpkg/info/$_up.list" "$1/var/lib/dpkg/info/$_up":*.list; do
            [ -f "$_ul" ] || continue
            grep -E '^/(usr/)?lib/systemd/system/[A-Za-z0-9_.:-]+\.(service|socket|timer|path)$' "$_ul" |
                while read -r _uu; do
                    # an alias (a link to the unit, smartd.service) follows its unit
                    [ ! -L "$1$_uu" ] || continue
                    _uu=${_uu##*/}
                    case $_uu in *@*) continue ;; esac
                    _us=disabled
                    for _uw in "$1"/etc/systemd/system/*.wants/"$_uu" "$1"/etc/systemd/system/*.requires/"$_uu"; do
                        [ ! -L "$_uw" ] || _us=enabled
                    done
                    [ "$(readlink "$1/etc/systemd/system/$_uu" 2>/dev/null)" != /dev/null ] || _us=masked
                    echo "$_up $_uu $_us"
                done
        done
    done | sort -u
}
# upk_record OLD NEW OS: after the settings are in NEW, before the switch. Writes NEW/etc/mu300/user-packages/
# (wanted: the names; files: "package path" of their files in etc/; rc.d: the services enabled in OLD; from; state)
# and moves their etc/config files out of the way. MU300_KEEP_PACKAGES=0: they are not installed again (state
# skipped; mu300-user-packages retry does it later), their settings are set aside all the same.
upk_record() {
    _uo=$1 _un=$2 _uk=$(upk_kind "$3")
    _ut=$2/.mu300-image _ud=$2/$UPK_DIR
    mkdir -p "$_ut" || return 1
    # the old image's own set; a system from before that list: the new image's and the one packages.txt names
    if [ -s "$_uo/etc/mu300/image-packages" ]; then cat "$_uo/etc/mu300/image-packages"
    else
        cat "$_ut/image-packages" 2>/dev/null || :
        upk_manual "$_un" "$_uk"
        upk_txt_names "$_uo/etc/mu300/packages.txt"
    fi > "$_ut/not"
    upk_installed "$_un" "$_uk" >> "$_ut/not"
    # what an earlier update recorded and the device never got back (offline since, or skipped) stays wanted
    { upk_manual "$_uo" "$_uk"; upk_pending "$_uo/$UPK_DIR"; } > "$_ut/had"
    { sed 's/^/x /' "$_ut/not"; sed 's/^/w /' "$_ut/had"; } |
        awk '$1 == "x" { n[$2] = 1; next } NF > 1 && !($2 in n) { print $2 }' | sort -u > "$_ut/added"
    rm -rf "$_ud"
    : > "$_ut/wanted"
    while read -r _up; do upk_skip "$_up" || echo "$_up" >> "$_ut/wanted"; done < "$_ut/added"
    # Ubuntu: the apt sources added on the device (a PPA's packages cannot come back without it), see upk_apt_keep
    if [ "$_uk" = ubuntu ]; then
        upk_apt_keep "$_uo" "$_un" "$([ -s "$_ut/wanted" ] && echo checked)" || return 1
    fi
    if [ ! -s "$_ut/wanted" ]; then
        echo "  none: everything installed is part of the new image"
        rm -rf "$_ut"
        return 0
    fi
    mkdir -p "$_ud" || return 1
    mv "$_ut/wanted" "$_ud/wanted"
    [ ! -s "$_ut/apt-kept" ] || mv "$_ut/apt-kept" "$_ud/apt-sources"
    [ ! -s "$_ut/apt-prefs" ] || mv "$_ut/apt-prefs" "$_ud/apt-prefs"
    : > "$_ud/done"; : > "$_ud/failed"
    cat "$_uo/etc/mu300/image-version" > "$_ud/from" 2>/dev/null || echo unknown > "$_ud/from"
    # their files in etc/, from the package database of OLD (and of the record a pending one came from)
    if [ "$_uk" = openwrt ]; then
        { cat "$_uo/$UPK_DIR/files" 2>/dev/null || :
          [ ! -f "$_uo/lib/apk/db/installed" ] || awk '
            $0 ~ /^P:/ { p = substr($0, 3); next }
            $0 ~ /^F:/ { d = substr($0, 3); next }
            $0 ~ /^R:/ && (d == "etc" || d ~ /^etc\//) { print p " " d "/" substr($0, 3) }' "$_uo/lib/apk/db/installed"
        } | { sed 's/^/w /' "$_ud/wanted"; sed 's/^/f /'; } |
            awk '$1 == "w" { w[$2] = 1; next } ($2 in w) && NF > 2 { print substr($0, 3) }' | sort -u > "$_ud/files"
        for _uf in "$_uo"/etc/rc.d/*; do { [ -L "$_uf" ] || [ -e "$_uf" ]; } && echo "${_uf##*/}"; done > "$_ud/rc.d" || :
    else
        : > "$_ud/files"; : > "$_ud/rc.d"
        upk_units "$_uo" < "$_ud/wanted" > "$_ud/units"
    fi
    if [ "${MU300_KEEP_PACKAGES:-1}" = 0 ]; then echo skipped > "$_ud/state"; else echo pending > "$_ud/state"; fi
    echo "  $(tr '\n' ' ' < "$_ud/wanted")"
    if [ "$(cat "$_ud/state")" = skipped ]; then
        echo "  The new system does not have them, and they are not installed again (MU300_KEEP_PACKAGES=0 or"
        echo "  --no-reinstall; later: mu300-user-packages retry)."
    else
        echo "  The new system does not have them: its first boot installs them again once the device is online"
        echo "  (the result: mu300-user-packages status)."
    fi
    # their UCI files: a package that is not there must leave nothing behind that acts on the network. Only a file
    # its package owns, in etc/config, that the new image does not have itself (dhcp, firewall, luci: never).
    _um=
    while read -r _up _uf; do
        case $_uf in etc/config/?*) ;; *) continue ;; esac
        { [ -f "$_un/$_uf" ] && [ ! -L "$_un/$_uf" ]; } || continue
        ! grep -qxF "${_uf#etc/config/}" "$_ut/config" 2>/dev/null || continue
        mkdir -p "$_un/$UPK_ORPH/etc/config" && mv -f "$_un/$_uf" "$_un/$UPK_ORPH/$_uf" || return 1
        [ -n "$_um" ] || echo "  Their settings are set aside until they are back, so that they cannot act without them:"
        echo "    /$_uf -> /$UPK_ORPH/$_uf ($_up)"
        _um=1
    done < "$_ud/files"
    rm -rf "$_ut"
}
# stale_config_clean NEW: what the images of v2026.10.18 left in etc/config and an update then copied onto every
# device (the config files of the image are added to the kept ones): attendedsysupgrade, of a package the images
# remove on purpose, and the .apk-new copies apk leaves beside a changed config. Gone from NEW's etc/config unless
# the package is installed there (attendedsysupgrade stays with attendedsysupgrade-common or its LuCI app).
stale_config_clean() {
    for _uf in "$1"/etc/config/*.apk-new; do [ ! -e "$_uf" ] || rm -f "$_uf"; done
    [ -e "$1/etc/config/attendedsysupgrade" ] || return 0
    upk_installed "$1" openwrt | grep -qx 'attendedsysupgrade-common\|luci-app-attendedsysupgrade' && return 0
    rm -f "$1/etc/config/attendedsysupgrade"
}
# --- user-packages end

ACCOUNTS_MARK=/etc/.mu300-accounts-from-image   # in the images; removed once the accounts are the device's own
merge_accounts() {  # merge_accounts OLD_ROOT NEW_ROOT   ("" is the running system)
    o=$1 n=$2
    [ -f "$o/etc/shadow" ] && [ -f "$n/etc/shadow" ] || return 0
    for f in passwd group shadow gshadow; do
        [ -f "$o/etc/$f" ] && [ -f "$n/etc/$f" ] || continue
        awk -F: -v OFS=: -v f="$f" -v op="$o/etc/passwd" -v og="$o/etc/group" '
            BEGIN {
                while ((getline l < op) > 0) { split(l, a, ":"); if (a[3] >= 1000 && a[3] < 60000) lu[a[1]] = 1 }
                while ((getline l < og) > 0) { split(l, a, ":"); if (a[3] >= 1000 && a[3] < 60000) lg[a[1]] = 1 }
            }
            NR == FNR { old[$1] = $0; next }
            {
                seen[$1] = 1
                if ((f == "shadow" || f == "gshadow") && ($1 in old)) { print old[$1]; next }
                if (f == "group" && ($1 in old)) {
                    split(old[$1], og4, ":"); m = $4; k = split(og4[4], om, ",")
                    for (i = 1; i <= k; i++)
                        if (om[i] != "" && index("," m ",", "," om[i] ",") == 0) m = (m == "" ? om[i] : m "," om[i])
                    $4 = m
                }
                print
            }
            END {
                for (x in old) if (!(x in seen)) {
                    if ((f == "passwd" || f == "shadow") && (x in lu)) print old[x]
                    if ((f == "group" || f == "gshadow") && (x in lg)) print old[x]
                }
            }' "$o/etc/$f" "$n/etc/$f" > "$n/etc/$f.mu300" || { rm -f "$n/etc/$f.mu300"; return 1; }
        # renamed into place, never rewritten: a crash (or a reader at boot) never sees a half-written passwd.
        # The copy made with cp -p keeps the file's owner and mode (shadow is root:shadow 0640; no stat on OpenWrt).
        if [ -s "$n/etc/$f.mu300" ]; then
            cp -p "$n/etc/$f" "$n/etc/$f.mu300-new" && cat "$n/etc/$f.mu300" > "$n/etc/$f.mu300-new" && sync &&
                mv -f "$n/etc/$f.mu300-new" "$n/etc/$f" || { rm -f "$n/etc/$f.mu300" "$n/etc/$f.mu300-new"; return 1; }
        fi
        rm -f "$n/etc/$f.mu300"
    done
    rm -f "$n$ACCOUNTS_MARK"
}

# --- install-os begin
for os in $OSES; do
    tarball=$P/mu300-$os.tar.gz
    [ -f $tarball ] || { say_i18n "缺少 $tarball" "missing $tarball"; exit 1; }
    say_i18n "正在安装 $os" "installing $os"
    rm -rf $M/$os.new && mkdir $M/$os.new
    tar -xzpf $tarball -C $M/$os.new
    if [ -n "${MU300_TF_STATE:-}" ]; then
        (cd "$M/$os.new" && sha256sum -c "$P/rootfs-critical.sha256")
        [ -L "$M/$os.new/sbin/init" ] || [ -x "$M/$os.new/sbin/init" ]
    fi
    # prebuilt images: firmware and Android userspace pulled from this device by install.sh (tools/vendor-overlay.py)
    if [ -f $P/mu300-vendor-$os.tar.gz ]; then
        tar -xzpf $P/mu300-vendor-$os.tar.gz -C $M/$os.new
        [ "${MU300_KEEP_PAYLOAD:-0}" = 1 ] || rm -f $P/mu300-vendor-$os.tar.gz
    elif [ -n "${MU300_PREPARED_VENDOR:-}" ]; then
        # Do not merge stale property snapshots/libraries from the build device
        # with this device's vendor tree.
        rm -rf "$M/$os.new/opt/mu300/android"
        cp -a "$MU300_PREPARED_VENDOR/." "$M/$os.new/"
        (cd "$M/$os.new" && sha256sum -c "$MU300_TF_STATE/vendor.sha256" >/dev/null)
    elif [ "${MU300_VENDOR_FROM_DEVICE:-0}" = 1 ]; then
        sh "$P/mu300-vendor-from-device.sh" "$M/$os.new" "$os"
    fi
    # update: carry the settings and user data of the previous installation over to the new system
    if [ "${UPDATE:-0}" = 1 ] && [ -d $M/$os ]; then
        image_own_save "$M/$os.new"
        case $os in
            ubuntu) keep="etc/mu300 etc/ssh etc/hostname etc/localtime etc/timezone etc/fstab home root srv usr/local var/lib/bluetooth" ;;
openwrt) keep="etc/config etc/mu300 etc/unisoc-modem etc/dropbear etc/rc.local etc/crontabs root" ;;
        esac
        kept=
        for k in $keep; do
            [ -e "$M/$os/$k" ] || continue
            mkdir -p "$M/$os.new/$(dirname $k)"
            rm -rf "$M/$os.new/$k.image"
            [ "$k" = etc/config ] && [ -d "$M/$os.new/$k" ] && mv "$M/$os.new/$k" "$M/$os.new/$k.image"
            rm -rf "$M/$os.new/$k"
            cp -a "$M/$os/$k" "$M/$os.new/$k" && kept="$kept $k"
            # OpenWrt: the old settings, plus the config files of the image's packages the old system did not have
            # (as mu300-update does; the old etc/config alone dropped e.g. /etc/config/sqm)
            if [ -d "$M/$os.new/$k.image" ]; then
                for f in "$M/$os.new/$k.image"/* "$M/$os.new/$k.image"/.[!.]*; do
                    [ -e "$f" ] || [ -L "$f" ] || continue
                    [ -e "$M/$os.new/$k/${f##*/}" ] || [ -L "$M/$os.new/$k/${f##*/}" ] || cp -a "$f" "$M/$os.new/$k/"
                done
                rm -rf "$M/$os.new/$k.image"
            fi
        done
        # services the user enabled or disabled themselves: copy the extra symlinks over, but only when the unit
        # they point at exists in the new system (stale units from an older release must not come back)
        extra=
        case $os in
            ubuntu)
                for w in $M/$os/etc/systemd/system/*.wants; do
                    [ -d "$w" ] || continue
                    t=${w##*/}
                    for l in "$w"/*; do
                        # -L, not -e: the links point at absolute paths inside the Linux root, so from Android
                        # they all look broken
                        [ -L "$l" ] || [ -e "$l" ] || continue
                        u=${l##*/}
                        if [ -L "$M/$os.new/etc/systemd/system/$t/$u" ]; then continue; fi
                        [ -e "$M/$os.new/etc/systemd/system/$u" ] || [ -e "$M/$os.new/usr/lib/systemd/system/$u" ] || continue
                        mkdir -p "$M/$os.new/etc/systemd/system/$t"
                        cp -a "$l" "$M/$os.new/etc/systemd/system/$t/$u" && extra="$extra $u"
                    done
                done ;;
            openwrt)
                for l in $M/$os/etc/rc.d/*; do
                    [ -L "$l" ] || [ -e "$l" ] || continue
                    u=${l##*/}
                    if [ -L "$M/$os.new/etc/rc.d/$u" ]; then continue; fi
                    [ -x "$M/$os.new/etc/init.d/${u#S??}" ] || [ -x "$M/$os.new/etc/init.d/${u#K??}" ] || continue
                    cp -a "$l" "$M/$os.new/etc/rc.d/$u" && extra="$extra $u"
                done ;;
        esac
        say_i18n "已保留先前 $os 的配置：$kept" "kept from the previous $os:$kept"
        [ -n "$extra" ] && say_i18n "已保留启用的服务：$extra" "kept enabled services:$extra"
        merge_accounts "$M/$os" "$M/$os.new" || {
            say_i18n "保留原系统账号失败，未替换系统" "could not preserve accounts; old system not replaced"
            exit 1
        }
        image_own_restore "$M/$os.new"
        upk_record "$M/$os" "$M/$os.new" "$os" || {
            say_i18n "无法记录用户安装的软件包，未替换原系统" "could not record user packages; old system not replaced"
            exit 1
        }
        stale_config_clean "$M/$os.new"
    fi
    # Configure and validate the staged tree before publishing its name.
    R=$M/$os.new
    [ "${SD_MODE:-0}" = 1 ] && [ -f $R/etc/fstab ] && \
        sed -i 's|LABEL=mu300root|LABEL=mu300sd|' $R/etc/fstab
    mkdir -p $R/etc/mu300
    if [ -n "$ssid" ] && ! { [ "${UPDATE:-0}" = 1 ] && [ -s $R/etc/mu300/hotspot.conf ]; }; then
        umask 077
        printf 'SSID=%s\nPSK=%s\nBAND=5\nCHANNEL=auto\nCOUNTRY=TR\n' "$ssid" "$psk" > $R/etc/mu300/hotspot.conf
        chmod 600 $R/etc/mu300/hotspot.conf
        umask 022
    fi
    if [ "$DEFAULT_LINUX" = 1 ]; then echo linux > $R/etc/mu300/default-boot; else rm -f $R/etc/mu300/default-boot; fi
    if [ -n "$PWHASH" ]; then
        rm -f $R/etc/.mu300-accounts-from-image   # the password is the one just chosen, not one to carry over
        case $os in
            ubuntu) sed -i "s|^ubuntu:[^:]*:|ubuntu:$PWHASH:|" $R/etc/shadow ;;
            openwrt) sed -i "s|^root:[^:]*:|root:$PWHASH:|" $R/etc/shadow ;;
        esac
    fi
    rm -rf "$M/$os" && mv "$M/$os.new" "$M/$os"
    [ "${MU300_KEEP_PAYLOAD:-0}" = 1 ] || rm -f $tarball
done
mkdir -p $M/.mu300
echo "$BOOT_OS" > $M/.mu300/boot-os
case ${BOOT_ATTEMPTS:-} in [1-6]) echo "$BOOT_ATTEMPTS" > $M/.mu300/boot-attempts ;; esac
case ${KERNEL:-5.4} in
    5.4|6.18|7.2) mkdir -p $M/boot; echo "${KERNEL:-5.4}" > $M/boot/kernel; echo "${KERNEL:-5.4}" > $M/boot/installed.kernel ;;
esac
# the boot image is the installer's now: a release tag left by an earlier mu300-update would describe another one
rm -f $M/boot/installed.tag
[ -n "$ssid" ] && say_i18n "热点：已导入 SSID $ssid（密码 ${#psk} 位）" \
                             "hotspot: SSID $ssid imported (passphrase ${#psk} chars)"
installed=$(ls -d $M/ubuntu $M/openwrt 2>/dev/null | sed "s|$M/||g" | tr '\n' ' ')
say_i18n "已安装：${installed}启动系统=$BOOT_OS 默认启动 Linux=$DEFAULT_LINUX" \
         "installed: ${installed}boot-os=$BOOT_OS default-linux=$DEFAULT_LINUX"
[ "${MU300_KEEP_PAYLOAD:-0}" = 1 ] || rm -f $P/mu300-install.env
if [ "${SD_MODE:-0}" = 1 ]; then
    say_i18n '正在刷盘、卸载并回读 TF 文件系统' 'flushing, unmounting and reading back TF filesystem'
    # umount performs filesystem writeback and reports failure. A read-only
    # remount checks the published tree before any boot partition is written.
    umount "$M"
    if [ -n "${MU300_TF_STATE:-}" ]; then
        mount -t ext4 -o ro,noload "$SD_DEV" "$M"
        (cd "$M/openwrt" && sha256sum -c "$P/rootfs-critical.sha256")
        if [ -n "${MU300_PREPARED_VENDOR:-}" ]; then
            (cd "$M/openwrt" && sha256sum -c "$MU300_TF_STATE/vendor.sha256" >/dev/null)
        fi
        [ "$(cat "$M/.mu300/boot-os")" = "$BOOT_OS" ]
        [ "$(cat "$M/openwrt/etc/mu300/default-boot")" = linux ]
        umount "$M"
    fi
else
    # Also make the legacy path's success marker mean unmount has finished.
    sh "$T/android-mount-mu300root.sh" -u "$M"
    trap - EXIT
fi
echo MU300-INSTALL-OK   # install.sh checks for this line (set -e stops before it on any failure)
