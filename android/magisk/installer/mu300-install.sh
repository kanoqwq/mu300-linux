#!/system/bin/sh
# MU300 Linux from a Magisk zip: the whole installation, on the device, with no computer. customize.sh runs this as
# a child of Magisk's busybox (nothing here can change Magisk's own shell) and turns its exit status into the
# result: 0 installed, 3 dry run (nothing written), anything else refused or failed - the messages say what was and
# was not written.
#
# Everything device-specific - the header and AVB data of Android's boot image, the misc block, the firmware and the
# Android files - is read from this device here; the zip carries none of it. Nothing is written to the eMMC or the
# card before the systems are installed, and misc is armed last.
#
# This runs as root. Everything it checks and then uses lives in one directory only root can write (W, made under
# /data/adb): /data/local/tmp belongs to the shell user and /sdcard to every app with storage access, so a file
# there can be replaced between the check and the use. A settings file on /sdcard is read, but it can only choose
# what destroys nothing (see conf_load).
#
# MU300_ZIP_CONF=FILE (customize.sh): the mu300-install.conf a user put at the top of the zip, read like the zip's own
# (see conf_read). Without it, an update keeps the accounts and passwords of the system it replaces.
#
# Another caller than Magisk's customize.sh (tools/android-web.sh, for the web installer, runs this as root over adb)
# sets two things in the environment:
#   MU300_ANSWERS=FILE  the settings of this run come from FILE alone - one only root can change, or the run is
#                       refused - and no mu300-install.conf on /sdcard or in /data/adb is read (conf_read)
#   MU300_PROGRESS=1    lines a program can follow, beside the messages for people (step, plan_machine):
#                         MU300-STEP <n>/<of> <name>     a stage begins; the names are STEPS below
#                         MU300-PLAN <key>=<value>       the plan, one value per line, after the plan for people
# and may put extras beside the scripts (extras_find): mu300/extras/mu300-extra-<name>.tar.gz.
#
# MU300_LIB=1 defines the functions and runs nothing (tests/, which also set MU300_DIR to a directory laid out like
# the zip's mu300/). The MU300_* paths below point at a fake device there.
set -eu
# what is built here ends up in the installed systems: with this umask, whatever the shell that flashes the zip has
umask 022
D=${MU300_DIR:-$(cd "$(dirname "$0")" && pwd)}
# mu300-update first: it defines a say and a die of its own, which the ones below replace. MU300_LIB=1 while it is
# read makes it define its functions only; the caller's value comes back afterwards.
_mu300_lib=${MU300_LIB:-}
MU300_LIB=1
. "$D/mu300-update"
. "$D/android-boot-image.sh"
MU300_LIB=$_mu300_lib
TOP=$D                                   # i18n.sh reads $TOP/i18n
BY=${MU300_BY_NAME:-/dev/block/by-name}
SDCARD=${MU300_SDCARD:-/sdcard}
WORK_PARENT=${MU300_WORK_PARENT:-/data/adb}
W=                                       # the work directory, made by work_setup
MOUNTS=${MU300_MOUNTS:-/proc/mounts}
# the settings file that may also erase and set the password: only root can write /data/adb
TRUSTED_CONF=${MU300_TRUSTED_CONF:-/data/adb/mu300-install.conf}
# where the password is written (mode 600): root's file, or the shared storage only when a trusted conf says
# MU300_PASSWORD_FILE=sdcard (any app with storage access can read it there)
PW_FILE_ROOT=${MU300_PW_DIR:-/data/adb}/mu300-linux-password.txt
PW_FILE_SDCARD=$SDCARD/mu300-linux-password.txt
MAGISKBIN=${MAGISKBIN:-/data/adb/magisk}
MAGISKBOOT=${MAGISKBOOT:-$MAGISKBIN/magiskboot}
UBB=${MU300_UBB:-$D/busybox}             # the release's static busybox: its mkpasswd
# the project's own password checker (tools/pwcheck; static, libxcrypt), in zips whose kernel bundle has it: what checks
# a current password (android-pwcheck.sh), never a program of the Linux system that is checked
PWBIN=${MU300_PWBIN:-$D/mu300-pwcheck}
DEVSH=${MU300_DEVICE_SH:-/system/bin/sh}
ANDROID_SH=${MU300_ANDROID_SH:-/system/bin/sh}
APATH=${MU300_ANDROID_PATH:-/system/bin:/system/xbin:/vendor/bin}
NEED_OPENWRT=$((800 * 1024 * 1024)); NEED_UBUNTU=$((1600 * 1024 * 1024)); NEED_BOTH=$((2400 * 1024 * 1024))
MU300_LANG=${MU300_LANG:-}
. "$D/i18n.sh"
. "$D/storage.sh"

say() { echo "- $*"; }
warn() { echo "! $*"; }
die() { echo "! $*"; exit 1; }
# The stages of a run, in order, for a caller that draws them (MU300_PROGRESS=1). Names, not messages: they are never
# translated and do not change with the wording. Nothing is written to the eMMC or the card before "systems"; the
# Linux slot's boot image is written in "boot", and "arm" is the 32 bytes of misc.
STEPS='check unpack plan vendor image systems boot arm done'
step() {  # step NAME
    [ "${MU300_PROGRESS:-}" = 1 ] || return 0
    _sn=0 _st=0
    for _s in $STEPS; do _st=$((_st + 1)); [ "$_s" != "$1" ] || _sn=$_st; done
    echo "MU300-STEP $_sn/$_st $1"
}
gib() { awk -v b="$1" 'BEGIN { printf "%.1f GiB", b / 1073741824 }'; }
# Android's own shell and toolbox, never Magisk's standalone busybox: its mke2fs makes ext2 only and its losetup has
# no -S. su -c sh gives install.sh exactly this environment. MU300_DEVICE_WORK tells android-install.sh where its files are.
# MU300_LOCK_DIR: the lock of a timed-out SD-card stage (android-install.sh, FINDINGS 50) outlives this run's W.
# MU300_UPDATE_KEEP: list keeps only the places an update kept before the first release with a four-field tag (android-install.sh).
asw() { env -u ASH_STANDALONE PATH="$APATH" MU300_DEVICE_WORK="${W:-}" MU300_LOCK_DIR="$WORK_PARENT" MU300_UPDATE_KEEP="${MU300_UPDATE_KEEP:-all}" "$@"; }
su_do() { asw "$DEVSH" -c "$1"; }            # storage.sh's way to run a command on the device: here it is local

android_lang() {  # the language of the Android locale: tr, zh or en
    _l=
    for _p in persist.sys.locale persist.sys.locales persist.sys.language ro.product.locale ro.product.locale.language; do
        _l=$(getprop "$_p" 2>/dev/null) && [ -n "$_l" ] && break
    done
    case ${_l%%,*} in tr|tr-*|tr_*) echo tr ;; zh|zh-*|zh_*) echo zh ;; *) echo en ;; esac
}

# ---- answers: mu300-install.conf, read line by line, never sourced (it is a text file, and this runs as root). Only
# these keys are taken. Two kinds of file:
#   trusted    /data/adb/mu300-install.conf when root owns it and nobody else can write it, and a
#              mu300-install.conf inside the zip (as trusted as the scripts it comes with): mu300/mu300-install.conf,
#              and one a user put at the top of the zip (MU300_ZIP_CONF, which wins over every other)
# The password of an update is the zip's business alone (docs/INSTALLER-AUTOMATION.md, "Magisk zip"):
# MU300_KEEP_PASSWORD and MU300_CURRENT_PASSWORD count only from a mu300-install.conf inside the zip (or MU300_ANSWERS),
# and an update takes MU300_PASSWORD and MU300_PASSWORD_RESET only from there too (plan_password).
#   untrusted  /sdcard/mu300-install.conf or /sdcard/Download/mu300-install.conf (first found): any app with
#              storage access can write these, so they choose only what destroys nothing and reveals nothing
# Trusted values win.
CONF_KEYS='MU300_STORAGE MU300_SD_ERASE MU300_REGION_OVERWRITE MU300_MODE MU300_DOWNGRADE MU300_UPDATE_KEEP MU300_BOOT_OS MU300_BOOT MU300_BOOT_ATTEMPTS MU300_HOTSPOT MU300_GPU MU300_PASSWORD MU300_PASSWORD_RESET MU300_KEEP_PASSWORD MU300_CURRENT_PASSWORD MU300_PASSWORD_FILE MU300_DEVICE MU300_LANG MU300_DRY_RUN MU300_SYNC_BOOTCHAIN MU300_ADB_KEY'
CONF_SET=
conf_find() { for _f in "$SDCARD/mu300-install.conf" "$SDCARD/Download/mu300-install.conf"; do [ -f "$_f" ] && { echo "$_f"; return 0; }; done; return 0; }
# erasing, formatting, overriding the model check and the password: only from a trusted file
conf_trusted_only() {  # conf_trusted_only KEY VALUE
    case $1 in
        MU300_SD_ERASE|MU300_REGION_OVERWRITE|MU300_PASSWORD|MU300_PASSWORD_RESET|MU300_PASSWORD_FILE|MU300_DEVICE) return 0 ;;
        MU300_KEEP_PASSWORD|MU300_CURRENT_PASSWORD) return 0 ;;
        MU300_ADB_KEY) return 0 ;;               # whoever holds that key gets a root shell in Linux
        MU300_MODE) [ "$2" = wipe ] ;;
        # an update that may not carry everything of the user's over: to an older release, or keeping only the list
        MU300_DOWNGRADE) return 0 ;;
        MU300_UPDATE_KEEP) [ "$2" = list ] ;;
        *) return 1 ;;
    esac
}
conf_trusted() {  # conf_trusted FILE: a regular file that root owns and nobody else can write
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    set -- $(ls -ln "$1")
    [ "${3:-}" = "${MU300_TRUSTED_UID:-0}" ] || return 1
    case $1 in ?????w*|????????w*) return 1 ;; esac
}
conf_load() {  # conf_load FILE [trusted|zip [NAME]]   (zip: a file inside the zip, or MU300_ANSWERS; trusted too.
    # NAME: what the messages, the plan and the password note call the file, when its path means nothing to the user)
    [ -n "$1" ] && [ -f "$1" ] || return 0
    _cf=${3:-$1}; _tr=${2:-}; _set=; _ign=
    _zip=; [ "$_tr" != zip ] || { _zip=1; _tr=trusted; }
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line=$(printf '%s' "$_line" | tr -d '\r' | sed 's/^[[:space:]]*//')
        case $_line in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
        _k=$(printf '%s' "${_line%%=*}" | tr -d ' \t'); _v=${_line#*=}
        _v=$(printf '%s' "$_v" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        case $_v in \"*\") _v=${_v#\"}; _v=${_v%\"} ;; \'*\') _v=${_v#\'}; _v=${_v%\'} ;; esac
        case " $CONF_KEYS " in
            *" $_k "*)
                # an empty value leaves the default; anything else must be exactly a value of that key before it is
                # assigned - MU300_LANG, for one, is part of a file name the moment it is set
                [ -n "$_v" ] || continue
                if ! conf_valid "$_k" "$_v"; then
                    [ "$_k" != MU300_PASSWORD ] || die "$(t 'mu300-install.conf: MU300_PASSWORD must have at least 6 characters')"
                    [ "$_k" != MU300_ADB_KEY ] || _v=$(printf '%s' "$_v" | cut -c1-16)...
                    die "$(t 'mu300-install.conf: {1}={2} is not valid' "$_k" "$_v")"
                fi
                if [ -z "$_zip" ] && { [ "$_k" = MU300_KEEP_PASSWORD ] || [ "$_k" = MU300_CURRENT_PASSWORD ]; }; then
                    # (the key only: the value is a password)
                    warn "$(t 'mu300-install.conf: {1} is taken only from a mu300-install.conf inside the zip; ignored in {2}' "$_k" "$_cf")"
                elif [ "$_tr" != trusted ] && conf_trusted_only "$_k" "$_v"; then
                    _kv=$_k; [ "$_k" != MU300_MODE ] || _kv=$_k=$_v        # never a password's value
                    warn "$(t 'mu300-install.conf: {1} is taken only from {2}, which only root can change; ignored in {3}' "$_kv" "$TRUSTED_CONF" "$_cf")"
                    _ign="$_ign $_k"
                else
                    # $_k is one of CONF_KEYS: the eval is safe, and the value is assigned, never evaluated
                    _t=; [ "$_tr" != trusted ] || _t=1
                    eval "$_k=\$_v; SRC_$_k=\$_cf; TRUST_$_k=\$_t; ZIP_$_k=\$_zip"; _set="$_set $_k"
                fi ;;
            *) warn "$(t 'mu300-install.conf: {1} is not a setting of this installer; ignored' "$_k")" ;;
        esac
    done < "$1"
    [ -z "$_set" ] || say "$(t 'settings from {1}:{2}' "$_cf" "$_set")"
    if [ -n "$_ign" ]; then
        if [ "$_cf" = "$TRUSTED_CONF" ]; then _c="su -c 'chown 0:0 $_cf && chmod 600 $_cf'"
        else _c="su -c 'cp $_cf $TRUSTED_CONF && chmod 600 $TRUSTED_CONF'"; fi
        say "$(t 'To use{1}, make the file one only root can change: {2}' "$_ign" "$_c")"
    fi
    CONF_SET="$CONF_SET$_set"
}
conf_read() {  # every settings file there is, the untrusted ones first so that trusted values win
    # MU300_ANSWERS (the environment of a caller that is root already): that file and no other. Somebody is watching
    # this run on another screen and has just confirmed its plan: a file left on /sdcard or in /data/adb must not
    # change it. The rule for a file that may erase is the same as for the one in /data/adb.
    if [ -n "${MU300_ANSWERS:-}" ]; then
        conf_trusted "$MU300_ANSWERS" ||
            die "$(t '{1} is not a file that only root can change; nothing was changed' "$MU300_ANSWERS")"
        conf_load "$MU300_ANSWERS" zip
        return 0
    fi
    conf_load "$(conf_find)"
    _adb=
    if [ -e "$TRUSTED_CONF" ]; then
        if conf_trusted "$TRUSTED_CONF"; then _adb=trusted
        else
            warn "$(t '{1} is not owned by root, or others can change it: it is read like a file on /sdcard' "$TRUSTED_CONF")"
            conf_load "$TRUSTED_CONF"
        fi
    fi
    conf_load "$D/mu300-install.conf" zip
    [ -z "$_adb" ] || conf_load "$TRUSTED_CONF" trusted
    # the one a user put into this zip, at its top: for this zip, so over every other
    # (customize.sh unpacked it into a directory of the run: it is named after the zip)
    [ -z "${MU300_ZIP_CONF:-}" ] || conf_load "$MU300_ZIP_CONF" zip "mu300-install.conf in ${ZIPFILE##*/}"
}
conf_valid() {  # conf_valid KEY VALUE: VALUE is exactly one this installer knows for KEY
    case $1 in
        MU300_STORAGE) case $2 in internal|sd) return 0 ;; esac ;;
        MU300_MODE) case $2 in update|wipe) return 0 ;; esac ;;
        MU300_BOOT_OS) case $2 in ubuntu|openwrt|openwrt-luci) return 0 ;; esac ;;
        MU300_BOOT) case $2 in linux|android) return 0 ;; esac ;;
        MU300_BOOT_ATTEMPTS) case $2 in [1-6]) return 0 ;; esac ;;
        MU300_SD_ERASE|MU300_REGION_OVERWRITE|MU300_PASSWORD_RESET|MU300_DOWNGRADE) [ "$2" = yes ] && return 0 ;;
        MU300_KEEP_PASSWORD) case $2 in yes|no) return 0 ;; esac ;;
        # the password the installed system has now, any characters: it only ever goes to android-pwcheck.sh on stdin
        MU300_CURRENT_PASSWORD) return 0 ;;
        MU300_UPDATE_KEEP) case $2 in all|list) return 0 ;; esac ;;
        MU300_HOTSPOT|MU300_GPU) case $2 in yes|no) return 0 ;; esac ;;
        MU300_PASSWORD_FILE) [ "$2" = sdcard ] && return 0 ;;
        MU300_DEVICE) case $2 in f50|u30air) return 0 ;; esac ;;
        MU300_LANG) case $2 in en|tr|zh) return 0 ;; esac ;;
        MU300_DRY_RUN) [ "$2" = 1 ] && return 0 ;;
        MU300_SYNC_BOOTCHAIN) case $2 in 0|1) return 0 ;; esac ;;
        # an adb public key as storage.sh's adb_pubkey takes it from adbkey.pub: 700 base64 characters ("QAAAA"
        # first, "=" last), one blank, a name of [A-Za-z0-9@._:-]. It goes into mu300-install.env in single quotes.
        MU300_ADB_KEY)
            _ak=${2%% *}; _an=${2#* }
            [ "$_ak $_an" = "$2" ] && [ ${#_ak} -eq 700 ] && [ "${_ak%=}" != "$_ak" ] || return 1
            case ${_ak%=} in QAAAA*[!A-Za-z0-9+/]*) return 1 ;; QAAAA*) ;; *) return 1 ;; esac
            case $_an in ''|*[!A-Za-z0-9@._:-]*) return 1 ;; esac
            [ ${#_an} -le 64 ] && return 0 ;;
        # any characters: it only ever goes to mkpasswd on stdin, never into a path or a command line
        MU300_PASSWORD) [ ${#2} -ge 6 ] && return 0 ;;
    esac
    return 1
}
conf_check() {  # every answer, from a file or the environment, has a value this installer knows
    for _k in $CONF_KEYS; do
        eval "_v=\${$_k:-}"
        [ -z "$_v" ] || conf_valid "$_k" "$_v" && continue
        [ "$_k" != MU300_PASSWORD ] || die "$(t 'mu300-install.conf: MU300_PASSWORD must have at least 6 characters')"
        [ "$_k" != MU300_ADB_KEY ] || _v=$(printf '%s' "$_v" | cut -c1-16)...
        die "$(t 'mu300-install.conf: {1}={2} is not valid' "$_k" "$_v")"
    done
}
src_of() { eval "_s=\${SRC_$1:-}"; if [ -n "$_s" ]; then echo "$_s"; else t 'default'; fi; }   # where a value came from

# ---- a run that timed out on the SD card in this Android boot (FINDINGS 50): android-install.sh left its lock, keyed
# by the boot id, in WORK_PARENT (its own work directory was that run's and went with it). The request it waited on is
# still stuck in the kernel, and another run would only add one: refused before anything is built or written. A lock
# of an earlier boot is stale and goes.
STALL_LOCK=$WORK_PARENT/mu300-install.stalled
stall_check() {
    [ -f "$STALL_LOCK" ] || return 0
    _bi=$(cat "${MU300_BOOT_ID:-/proc/sys/kernel/random/boot_id}" 2>/dev/null | tr -d ' \n')
    if [ "$(sed -n 1p "$STALL_LOCK" 2>/dev/null)" != "${_bi:-unknown}" ]; then rm -f "$STALL_LOCK"; return 0; fi
    die "$(t 'An earlier run of this installer timed out in this Android boot ({1}); the SD card request it waited on is still stuck in the kernel. Reboot Android, then install the zip again. Nothing was changed; the lock is {2}.' "$(sed -n 2p "$STALL_LOCK")" "$STALL_LOCK")"
}

# ---- the device, its slots and the zip
env_check() {
    [ -n "${MU300_SKIP_ROOT_CHECK:-}" ] || [ "$(id -u)" = 0 ] || die "$(t 'this installer needs root')"
    [ -x "$MAGISKBOOT" ] || die "$(t "Magisk's magiskboot was not found; install this zip with Magisk 26 or newer")"
    chmod 755 "$UBB" 2>/dev/null || true
    [ ! -f "$PWBIN" ] || chmod 755 "$PWBIN" 2>/dev/null || true
}
detect_device() {
    _m="$(getprop ro.product.model) / $(getprop ro.product.device)"
    say "$(t 'device: {1}' "$_m")"
    # the ZTE V50 (experimental, FINDINGS 95): exactly V50 / MU3351 on ums9620, and never under another name -
    # MU300_DEVICE (f50 or u30air only) cannot make it another device, and nothing makes another device a V50
    case $_m in
        "V50 / MU3351"|*" / MU3351"|"V50 / "*)
            [ "$_m" = "V50 / MU3351" ] && [ "$(getprop ro.board.platform)" = ums9620 ] ||
                die "$(t 'This looks like a ZTE V50 but is not one this installer knows (model V50, device MU3351, platform ums9620): {1}' "$_m")"
            [ -z "${MU300_DEVICE:-}" ] || die "$(t 'mu300-install.conf: {1}={2} is not valid' MU300_DEVICE "$MU300_DEVICE")"
            DEVICE=v50
            say "$(t 'ZTE V50: EXPERIMENTAL, tested by nobody in this project (kano ported it); mainline kernel only; no second SIM, no NFC; USB network 192.168.79.1')" ;;
        *U30Air*|*U30_Air*|*"U30 Air"*) DEVICE=u30air ;;
        *MU300*|*F50*|*mu300*) DEVICE=f50 ;;
        *) DEVICE= ;;
    esac
    MODEL_KNOWN=0; [ -z "$DEVICE" ] || MODEL_KNOWN=1
    [ -z "${MU300_DEVICE:-}" ] || DEVICE=$MU300_DEVICE
    [ -n "$DEVICE" ] || die "$(t 'This does not look like a ZTE F50/MU300 or U30 Air. If it is one, put MU300_DEVICE=f50 or u30air into {1}.' "$TRUSTED_CONF")"
}
hex_at() { dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | od -An -tx1 -v | tr -d ' \n'; }
slot_setup() {
    case $(getprop ro.boot.slot_suffix) in
        _a) ANDROID_SLOT=a; LINUX_SLOT=b ;;
        _b) ANDROID_SLOT=b; LINUX_SLOT=a ;;
        *) die "$(t 'cannot tell which slot Android runs from')" ;;
    esac
    MISC=$BY/misc; BOOT_ANDROID=$BY/boot_$ANDROID_SLOT; BOOT_LINUX=$BY/boot_$LINUX_SLOT
    [ -e "$MISC" ] && [ -e "$BOOT_ANDROID" ] && [ -e "$BOOT_LINUX" ] || die "$(t 'this is not an A/B device with misc, boot_a and boot_b')"
    # never the partition Android booted from, whatever the links say
    [ "$(readlink -f "$BOOT_ANDROID")" != "$(readlink -f "$BOOT_LINUX")" ] || die "$(t 'boot_a and boot_b are the same partition')"
    LIVE_BC=$(hex_at "$MISC" 2048 32)
    bc_valid "$LIVE_BC" || die "$(t 'misc holds no valid boot control block; nothing was changed')"
    say "$(t 'Android runs from slot {1}; Linux goes to slot {2} (boot_{2})' "$ANDROID_SLOT" "$LINUX_SLOT")"
}
# The manifest names files and is part of paths (the payload entries, $W/mu300-$OS.tar.gz): every field must be
# exactly one of the values a release has before anything is built from it - no "/", "..", leading "-" or blanks.
manifest_load() {  # the zip's manifest, read like the conf file: only these keys
    TAG= SYSTEM= OS= UBUNTU= KERNEL= KERNEL_ASSET= ROOTFS_ASSET= SHA256_KERNEL= SHA256_ROOTFS=
    while IFS='=' read -r _k _v; do
        case $_k in TAG|SYSTEM|OS|UBUNTU|KERNEL|KERNEL_ASSET|ROOTFS_ASSET|SHA256_KERNEL|SHA256_ROOTFS) eval "$_k=\$_v" ;; esac
    done < "$1"
    _ok=1
    case $TAG in v[0-9]*) ;; *) _ok= ;; esac
    case $TAG in *[!0-9A-Za-z._-]*|*..*) _ok= ;; esac
    case $SYSTEM:$OS:$UBUNTU:$ROOTFS_ASSET in
        openwrt:openwrt::mu300-openwrt-rootfs.tar.gz) ;;
        openwrt-luci:openwrt-luci::mu300-openwrt-luci-rootfs.tar.gz) ;;
        ubuntu-24.04:ubuntu:24.04:mu300-ubuntu-rootfs.tar.gz) ;;
        ubuntu-26.04:ubuntu:26.04:mu300-ubuntu-26.04-rootfs.tar.gz) ;;
        *) _ok= ;;
    esac
    case $KERNEL:$KERNEL_ASSET in
        5.4:mu300-kernel.tar.gz|6.18:mu300-kernel-6.18.tar.gz|7.2:mu300-kernel-7.2.tar.gz|7.3:mu300-kernel-7.3.tar.gz) ;;
        *) _ok= ;;
    esac
    for _h in "$SHA256_KERNEL" "$SHA256_ROOTFS"; do
        case $_h in *[!0-9a-f]*) _ok= ;; esac
        [ ${#_h} = 64 ] || _ok=
    done
    [ -n "$_ok" ] || die "$(t 'the zip is incomplete or damaged (its manifest is not valid)')"
    OSES=$OS
}
# A directory of this run only, which only root can enter: the payload, the scripts that run as root, the settings
# they get, the device's proprietary files and the mount points. A new name each run, so nothing an earlier run left
# behind (a filesystem still mounted after it was killed, say) is ever used or removed.
work_setup() {
    _um=$(umask); umask 077
    W=$(mktemp -d "$WORK_PARENT/mu300-magisk.XXXXXX" 2>/dev/null) || W=
    umask "$_um"
    [ -n "$W" ] && [ -d "$W" ] || { W=; die "$(t 'could not create a work directory in {1}' "$WORK_PARENT")"; }
    chmod 700 "$W"
    W=$(cd "$W" && pwd -P)                   # as /proc/mounts names it
}
# the misc block read at the start, kept as it was: arm_linux writes it back when the armed one does not verify
misc_save() {
    dd if="$MISC" of="$W/misc-bc-live.bin" bs=1 skip=2048 count=32 2>/dev/null &&
        [ "$(od -An -tx1 -v "$W/misc-bc-live.bin" | tr -d ' \n')" = "$LIVE_BC" ] ||
        die "$(t 'could not keep a copy of the misc block; nothing was changed')"
}
payload_unpack() {  # the two payload files copied out of the zip into W, and only those copies checked and used
    _need=$(( $(unzip -l "$ZIPFILE" "payload/*" | awk 'END { print $1 }') + 512 * 1048576 ))
    # -P: one line per filesystem (busybox puts a long device name on a line of its own)
    _free=$(( $(df -Pk "$W" | awk 'NR == 2 { print $4 }') * 1024 ))
    [ "$_free" -ge "$_need" ] || die "$(t 'not enough free space in {1}: {2} needed, {3} free' "$WORK_PARENT" "$(gib "$_need")" "$(gib "$_free")")"
    mkdir "$W/kernel"
    say "$(t 'Unpacking {1} and {2}' "$ROOTFS_ASSET" "$KERNEL_ASSET")"
    unzip -p "$ZIPFILE" "payload/$ROOTFS_ASSET" > "$W/mu300-$OS.tar.gz" &&
        unzip -p "$ZIPFILE" "payload/$KERNEL_ASSET" > "$W/kernel.tar.gz" || die "$(t 'could not unpack the zip')"
    [ "$(sha256sum "$W/mu300-$OS.tar.gz" | cut -d' ' -f1)" = "$SHA256_ROOTFS" ] &&
        [ "$(sha256sum "$W/kernel.tar.gz" | cut -d' ' -f1)" = "$SHA256_KERNEL" ] ||
        die "$(t 'the zip is damaged (checksum mismatch); download it again')"
    tar -xzf "$W/kernel.tar.gz" -C "$W/kernel" && rm -f "$W/kernel.tar.gz" || die "$(t 'could not unpack the kernel bundle')"
    KB=$W/kernel
    if [ "$DEVICE" = v50 ] && ! grep -qw v50 "$KB/devices" 2>/dev/null; then
        die "$(t 'The ZTE V50 runs only a mainline kernel of a release that knows it: take the zip for kernel 6.18, 7.2 or 7.3 (of a newer release if this one has none)')"
    fi
    grep -qw "$DEVICE" "$KB/devices" 2>/dev/null || die "$(t 'this kernel does not support the {1}' "$DEVICE")"
}

# ---- leaving: W goes, but never a filesystem mounted in it. rm -rf into a mounted mu300root or mu300sd would
# delete the installed systems and everyone's files.
MOUNT_POINTS='mnt mu300root mu300root-internal'    # ours and android-install.sh's: only ever rmdir'd
mounted_at_or_under() {  # PATH: something is mounted on PATH or below it (also when the mount table is unreadable)
    [ -r "$MOUNTS" ] || return 0
    awk -v p="$1" '$2 == p || index($2, p "/") == 1 { f = 1 } END { exit !f }' "$MOUNTS"
}
cleanup() {  # every exit
    set +e
    write_example
    [ -n "${W:-}" ] && [ -d "$W" ] || return 0
    [ -z "${MOUNTED:-}" ] || umount_target "$MOUNTED"
    _left=
    for _e in "$W"/* "$W"/.[!.]*; do
        [ -e "$_e" ] || [ -L "$_e" ] || continue
        case " $MOUNT_POINTS " in *" ${_e##*/} "*) rmdir "$_e" 2>/dev/null || _left=1; continue ;; esac
        if mounted_at_or_under "$_e"; then _left=1; else rm -rf "$_e"; fi
    done
    rmdir "$W" 2>/dev/null || _left=1
    [ -z "$_left" ] || warn "$(t 'A Linux filesystem is still mounted in {1}; it was left as it is. Restart the device before installing again.' "$W")"
}

# ---- what to do
mount_target() {  # mount_target DIR [ro|recover]: the Linux filesystem of the plan, with Android's mount helper
    # (recover: read only after the journal is replayed, android-mount-mu300root.sh)
    cp "$D/android-mount-mu300root.sh" "$W/" || return 1
    _ro=; [ "${2:-}" != ro ] || _ro=1; [ "${2:-}" != recover ] || _ro=recover
    if [ "$SD_MODE" = 1 ]; then
        asw MU300_RO="$_ro" MU300_SD_DEV="$SD_DEV" "$ANDROID_SH" "$W/android-mount-mu300root.sh" "$1"
    else
        asw MU300_RO="$_ro" MU300_OFF="$INT_OFF" MU300_SIZE="$INT_SIZE" "$ANDROID_SH" "$W/android-mount-mu300root.sh" "$1"
    fi | grep -q '^MOUNTED' && MOUNTED=$1
}
# MOUNTED is cleared only after the helper said it unmounted; under set -e a bare failing call would end the
# function before that line
umount_target() { asw "$ANDROID_SH" "$W/android-mount-mu300root.sh" -u "$1" >/dev/null 2>&1 || return 1; MOUNTED=; }
need_for() { case $1 in openwrt|openwrt-luci) echo $NEED_OPENWRT ;; ubuntu) echo $NEED_UBUNTU ;; *) echo $NEED_BOTH ;; esac; }
no_room_inside() {  # the refusal when Linux does not fit inside, with the way out this device has
    if [ -n "$SD_DEV" ] && [ "$sd_ex" = foreign ]; then
        die "$(t 'There is too little free space inside for Linux, and the SD card ({1}) holds another Linux (ext4) filesystem, which the installer never formats. Copy off what you need and format the card elsewhere, or use another card.' "$SD_DEV")"
    elif [ -n "$SD_DEV" ]; then
        die "$(t 'There is too little free space inside for Linux. The SD card ({1}, {2}) can hold it: put MU300_STORAGE=sd and MU300_SD_ERASE=yes into {3}, a file only root can change (everything on the card is erased).' "$SD_DEV" "$(gib "$SD_BYTES")" "$TRUSTED_CONF")"
    elif [ "${SD_SMALL:-0}" = 1 ]; then
        die "$(t 'There is too little free space inside for Linux, and the SD card is smaller than 700 MiB. Use a bigger card (MU300_STORAGE=sd and MU300_SD_ERASE=yes in {1}), or make room with the installer for computers.' "$TRUSTED_CONF")"
    else
        die "$(t 'There is too little free space inside for Linux and no SD card. Insert a card (MU300_STORAGE=sd and MU300_SD_ERASE=yes in {1}), or make room with the installer for computers.' "$TRUSTED_CONF")"
    fi
}
# the storage an overwrite may hit was chosen by a trusted file, or by the installer itself (no MU300_STORAGE at
# all): for the card that means it already holds this project's mu300sd
aimed_by_trust() {  # aimed_by_trust sd|internal
    if [ -n "${MU300_STORAGE:-}" ]; then
        [ "$MU300_STORAGE" = "$1" ] && [ -n "${TRUST_MU300_STORAGE:-}" ]
    else
        [ "$1" = internal ] || [ "$sd_ex" = yes ]
    fi
}
# The settings this run erases with (MU300_MODE=wipe, MU300_SD_ERASE, MU300_REGION_OVERWRITE): after a successful
# install each becomes a comment in the trusted file, so that a standing one never applies to a later flash (the
# second zip of two would wipe the first system, every update the home directories, a new card would be erased).
USED_KEYS=
plan_storage() {
    region_probe || die "$(t 'could not read the partition table')"
    region_find_existing
    INT_OFF=$OFF; INT_SIZE=$SIZE; int_existing=$existing
    # another installer's layouts (FINDINGS 179): its partition named mu300root is where this goes; its image file
    # in Android's data is not something this installer can update
    if _note=$(region_image_note); then say "$_note"; fi
    sd_probe; sd_ex=no
    [ -n "$SD_DEV" ] && sd_ex=$(sd_existing)
    case ${MU300_STORAGE:-} in
        sd) [ -n "$SD_DEV" ] || die "$(t 'MU300_STORAGE=sd, but there is no usable SD card in the device')"; SD_MODE=1 ;;
        internal)
            # boot/init starts a mu300sd card before anything on the eMMC, and nobody is here to type the word
            # storage.sh's internal_over_card asks for: an installation inside would never start
            [ "$sd_ex" != yes ] || die "$(t 'The SD card ({1}) holds a Linux installation (mu300sd), and the device always starts that one first: an installation to internal storage does not start while this card is in the slot.' "$SD_DEV")
$(t 'Take the card out and install the zip again, or install to the card (MU300_STORAGE=sd in /sdcard/mu300-install.conf).')"
            SD_MODE=0 ;;
        *)  # an installation where it is (the card first, as init looks there first), else inside when it fits
            if [ "$sd_ex" = yes ]; then SD_MODE=1
            elif [ "$int_existing" = yes ] || [ "$INT_SIZE" -ge "$(need_for "$OS")" ]; then SD_MODE=0
            elif _why=$(region_part_refusal); then die "$_why"
            else no_room_inside
            fi ;;
    esac
    if [ "$SD_MODE" = 1 ]; then
        sd_not_foreign
        existing=$([ "$sd_ex" = yes ] && echo yes || echo no); TARGET_SIZE=$SD_BYTES
    else
        existing=$int_existing; TARGET_SIZE=$INT_SIZE; DIRTY=0
        if _why=$(region_part_refusal); then die "$_why"; fi
        if [ "$IN_PART" = 1 ]; then say "$(t 'Linux goes into the GPT partition named mu300root ({1}), which another installer made; the partition table is not changed' "$(gib "$INT_SIZE")")"; fi
        [ "$INT_SIZE" -ge $((700 * 1024 * 1024)) ] || no_room_inside
        if [ "$existing" = no ]; then
            region_dirty
            if [ "$DIRTY" != 0 ] && ! { [ "${MU300_REGION_OVERWRITE:-}" = yes ] && aimed_by_trust internal; }; then
                [ "$IN_PART" = 1 ] && die "$(t 'The partition named mu300root is not empty ({1} of 16 samples hold data), and it holds no Linux filesystem of this project. To use it anyway, put MU300_STORAGE=internal and MU300_REGION_OVERWRITE=yes into {2}, a file only root can change.' "$DIRTY" "$TRUSTED_CONF")"
                die "$(t 'The free space behind the partitions is not empty ({1} of 16 samples hold data); it may be used by this firmware. To use it anyway, put MU300_STORAGE=internal and MU300_REGION_OVERWRITE=yes into {2}, a file only root can change.' "$DIRTY" "$TRUSTED_CONF")"
            fi
            [ "$DIRTY" = 0 ] || USED_KEYS="$USED_KEYS MU300_REGION_OVERWRITE"
        fi
    fi
    INTERNAL_EXISTS=0; [ "$int_existing" = yes ] && INTERNAL_EXISTS=1
    FORMAT=0; UPDATE=0
    if [ "$existing" = yes ]; then
        case ${MU300_MODE:-update} in update) UPDATE=1 ;; wipe) FORMAT=1 ;; esac
    else
        FORMAT=1
    fi
    # a wipe is used by the install that formats, also one onto a target with no filesystem yet (a blank region, a
    # new card): left standing, the next flash would wipe the system this one installs
    if [ "${MU300_MODE:-}" = wipe ] && [ "$FORMAT" = 1 ]; then USED_KEYS="$USED_KEYS MU300_MODE"; fi
    # A card is formatted only when a trusted file says MU300_SD_ERASE=yes, whatever the reason (a new card, or
    # MU300_MODE=wipe on a mu300sd one), and only when that same trust chose the card: a standing MU300_SD_ERASE=yes
    # in /data/adb must not be aimed at whatever card is in the slot by a MU300_STORAGE=sd any app can write.
    if [ "$SD_MODE" = 1 ] && [ "$FORMAT" = 1 ] && ! { [ "${MU300_SD_ERASE:-}" = yes ] && aimed_by_trust sd; }; then
        if [ "$existing" = yes ] && [ "${MU300_SD_ERASE:-}" != yes ]; then
            die "$(t 'MU300_MODE=wipe formats the SD card ({1}, {2}): the installation on it and everything else is erased. To allow that, put MU300_SD_ERASE=yes into {3} as well.' "$SD_DEV" "$(gib "$SD_BYTES")" "$TRUSTED_CONF")"
        fi
        die "$(t 'Installing to the SD card ({1}, {2}) erases everything on it. To allow that, put MU300_STORAGE=sd and MU300_SD_ERASE=yes into {3}, a file only root can change.' "$SD_DEV" "$(gib "$SD_BYTES")" "$TRUSTED_CONF")"
    fi
    if [ "$SD_MODE" = 1 ] && [ "$FORMAT" = 1 ]; then USED_KEYS="$USED_KEYS MU300_SD_ERASE"; fi
}
# HAVE_ACCOUNTS: the system of this zip is there with an account of its own: its user (ubuntu, OpenWrt's root) has a
# line in its /etc/shadow, with a password or none, and they are not the image's accounts (an older mu300-update leaves
# .mu300-accounts-from-image until they are carried over). An update keeps them as they are (plan_password): the
# password the user has, whatever it is, the images' well-known one or none included, as mu300-update does.
# PW_CHECK: with MU300_KEEP_PASSWORD=yes from the zip, android-pwcheck.sh's word on MU300_CURRENT_PASSWORD (MATCH,
# NOMATCH, LOCKED, UNVERIFIABLE), checked here while the filesystem is mounted read-only: never the password or the hash.
inspect_target() {  # which systems the existing filesystem holds, its Ubuntu release, and the release of the
    # system this zip replaces (HAVE_VERSION; empty when it is not there or does not say): mounted read-only
    HAVE_SYSTEMS=; HAVE_UBUNTU=; HAVE_ACCOUNTS=0; HAVE_VERSION=; PW_CHECK=
    [ "$existing" = yes ] && [ "$FORMAT" = 0 ] || return 0
    # with a password to check, the files as Linux left them: the journal is replayed first (a noload view can hold
    # the shadow from before the last passwd; android-mount-mu300root.sh). Else nothing is written, a dry run included.
    _how=ro
    if [ "${MU300_KEEP_PASSWORD:-}" = yes ] && [ -n "${ZIP_MU300_KEEP_PASSWORD:-}" ]; then _how=recover; fi
    mount_target "$W/mnt" "$_how" || die "$(t 'could not mount the existing Linux filesystem')"
    for _os in ubuntu openwrt openwrt-luci; do [ -d "$W/mnt/$_os" ] && HAVE_SYSTEMS="$HAVE_SYSTEMS $_os"; done
    _u=root; [ "$OS" != ubuntu ] || _u=ubuntu
    if grep -q "^$_u:" "$W/mnt/$OS/etc/shadow" 2>/dev/null && [ ! -e "$W/mnt/$OS/etc/.mu300-accounts-from-image" ]; then
        HAVE_ACCOUNTS=1
        if [ "${MU300_KEEP_PASSWORD:-}" = yes ] && [ -n "${ZIP_MU300_KEEP_PASSWORD:-}" ]; then
            PW_CHECK=$(printf '%s\n' "${MU300_CURRENT_PASSWORD:-}" |
                asw MU300_PWCHECK_BIN="$PWBIN" MU300_PWCHECK_BB="$UBB" "$ANDROID_SH" "$D/android-pwcheck.sh" "$W/mnt/$OS" "$_u" 2>/dev/null | tail -n1) || true
        fi
    fi
    HAVE_UBUNTU=$(sed -n 's/^VERSION_ID="\(.*\)"/\1/p' "$W/mnt/ubuntu/usr/lib/os-release" 2>/dev/null | head -n1)
    HAVE_VERSION=$(head -n1 "$W/mnt/$OS/etc/mu300/image-version" 2>/dev/null) || HAVE_VERSION=
    case $HAVE_VERSION in v[0-9]*) ;; *) HAVE_VERSION= ;; esac
    case $HAVE_VERSION in *[!0-9A-Za-z._-]*) HAVE_VERSION= ;; esac
    umount_target "$W/mnt" || die "$(t 'could not unmount the Linux filesystem from {1}' "$W/mnt")"
}
plan_choices() {
    _after=$(printf '%s\n' $HAVE_SYSTEMS "$OS" | sort -u | tr '\n' ' ')
    case $_after in *ubuntu*openwrt*|*openwrt*ubuntu*) _need=$NEED_BOTH ;; *) _need=$(need_for "$OS") ;; esac
    [ "$TARGET_SIZE" -ge "$_need" ] || die "$(t 'that choice needs about {1} MiB and this device has {2} MiB of free space' "$((_need / 1048576))" "$((TARGET_SIZE / 1048576))")"
    if [ "$KERNEL" = 5.4 ] && case " $HAVE_SYSTEMS " in *" ubuntu "*) [ "$HAVE_UBUNTU" = 26.04 ] ;; *) false ;; esac; then
        die "$(t 'Ubuntu 26.04 is installed, and it needs a mainline kernel (6.18, 7.2 or 7.3): use a zip with one of those kernels, or MU300_MODE=wipe.')"
    fi
    sd_kernel_ok "$KB" || die "$(t 'this kernel cannot read the SD card; use a newer zip')"
    # Never back to an older release by itself: the new image does the carrying over, and one from before the first
    # release with a four-field tag keeps only a list of places - what else was added to the system would be gone from
    # it. (newer, mu300-update's: the release tags, docs/FINDINGS.md 125 - every vYYYY.MM.DD.N is newer than every
    # vYYYY.MM.DD, which text order does not give; a system or a zip that does not say which release it is is not held
    # back.)
    DOWNGRADE=0
    if [ "$UPDATE" = 1 ] && [ -n "$HAVE_VERSION" ] && newer "$HAVE_VERSION" "$TAG"; then
        [ "${MU300_DOWNGRADE:-}" = yes ] ||
            die "$(t 'The installed {1} is {2}, and this zip is {3}, an older release. Going back to an older release is not done by itself: the older release may not carry over everything you added to the system.' "$OS" "$HAVE_VERSION" "$TAG")
$(t 'Nothing was changed. To install it all the same, put MU300_DOWNGRADE=yes into {1} (a release before the first release with a four-field tag then keeps only settings, users and home directories), or MU300_MODE=wipe to start fresh.' "$TRUSTED_CONF")"
        DOWNGRADE=1; USED_KEYS="$USED_KEYS MU300_DOWNGRADE"
    fi
    if [ "$UPDATE" = 1 ] && [ "${MU300_UPDATE_KEEP:-all}" = list ]; then USED_KEYS="$USED_KEYS MU300_UPDATE_KEEP"; fi
    BOOT_OS=${MU300_BOOT_OS:-$OS}
    case " $_after " in *" $BOOT_OS "*) ;; *) die "$(t 'MU300_BOOT_OS={1}, but {1} is not installed' "$BOOT_OS")" ;; esac
    DEFAULT_LINUX=1; [ "${MU300_BOOT:-linux}" = android ] && DEFAULT_LINUX=0
    BOOT_ATTEMPTS=${MU300_BOOT_ATTEMPTS:-5}
    IMPORT_HOTSPOT=1; [ "${MU300_HOTSPOT:-yes}" = no ] && IMPORT_HOTSPOT=0
    GPU=${MU300_GPU:-yes}
    PW_FILE=$PW_FILE_ROOT; [ "${MU300_PASSWORD_FILE:-}" != sdcard ] || PW_FILE=$PW_FILE_SDCARD
    plan_password
    # adb over USB to Linux for one key (MU300_ADB_KEY, trusted only): android-install.sh turns it on and authorises
    # the key in the system it installs. Without it nothing about adb changes (an update keeps what was set).
    ADB=0 ADB_KEY=
    if [ -n "${MU300_ADB_KEY:-}" ]; then ADB=1; ADB_KEY=$MU300_ADB_KEY; fi
    # an if, not an && list: the last command's status is the function's, and set -e would stop main on it
    WIPE_LEGACY=0; if [ "$OS" = ubuntu ] && [ "$FORMAT" = 0 ]; then WIPE_LEGACY=1; fi
}
# The password. An update of a system with an account of its own (HAVE_ACCOUNTS) keeps its accounts and passwords as
# they are, as mu300-update does, unless a mu300-install.conf inside the zip says otherwise - the only place an update
# takes an answer about the password from (nothing can be typed while Magisk installs a zip):
#   nothing                        kept, not checked (PW_MODE=kept): the plan and the end say so
#   MU300_KEEP_PASSWORD=yes        kept once MU300_CURRENT_PASSWORD is the current password (no such line: the empty
#                                  one, OpenWrt's root as it ships), checked on the device by android-pwcheck.sh
#                                  (inspect_target; PW_MODE=checked). Anything else stops the run before anything is
#                                  written: a guess changes nothing, and nothing is set that was not asked for.
#   MU300_KEEP_PASSWORD=no, MU300_PASSWORD=<new>, MU300_PASSWORD_RESET=yes
#                                  the new one (PW_MODE=given), or without MU300_PASSWORD the images' own (image)
# MU300_PASSWORD or MU300_PASSWORD_RESET from /data/adb/mu300-install.conf is not used by an update (a line left
# there must not reset a password at the next update). A system with no account of its own yet (a new filesystem, a
# wipe, a system added beside another, one still with the images' accounts) gets the images' well-known password
# (build_password), or MU300_PASSWORD from a trusted file. KEEP_PW=1: android-install.sh leaves the accounts alone.
plan_password() {
    PW_MODE=image
    if [ "$UPDATE" = 1 ] && [ "${HAVE_ACCOUNTS:-0}" = 1 ]; then
        for _k in MU300_PASSWORD MU300_PASSWORD_RESET; do
            eval "_v=\${$_k:-}; _z=\${ZIP_$_k:-}"
            if [ -n "$_v" ] && [ -z "$_z" ]; then
                warn "$(t 'an update sets a password only from a mu300-install.conf inside the zip: {1} in {2} is not used' "$_k" "$(src_of "$_k")")"
                eval "$_k=; SRC_$_k="
            fi
        done
        _pwu=root; [ "$OS" != ubuntu ] || _pwu=ubuntu
        if [ "${MU300_KEEP_PASSWORD:-}" = yes ]; then
            [ -z "${MU300_PASSWORD:-}" ] && [ "${MU300_PASSWORD_RESET:-}" != yes ] ||
                die "$(t 'mu300-install.conf: MU300_KEEP_PASSWORD=yes and a new password (MU300_PASSWORD or MU300_PASSWORD_RESET) do not go together; nothing was changed')"
            case ${PW_CHECK:-} in
                MATCH) PW_MODE=checked ;;
                NOMATCH) die "$(t 'MU300_CURRENT_PASSWORD in {1} is not the current password of "{2}" in the installed {3}. Nothing was changed. Correct it (no such line: no password), or set a new one with MU300_KEEP_PASSWORD=no and MU300_PASSWORD, or remove MU300_KEEP_PASSWORD to keep the password without a check.' "$(src_of MU300_KEEP_PASSWORD)" "$_pwu" "$OS")" ;;
                LOCKED) die "$(t '"{1}" in the installed {2} has no password login, so there is none to confirm. Nothing was changed. Set a new one with MU300_KEEP_PASSWORD=no and MU300_PASSWORD, or remove MU300_KEEP_PASSWORD to keep the account as it is.' "$_pwu" "$OS")" ;;
                *) die "$(t 'the current password of "{1}" in the installed {2} could not be checked on the device ({3}). Nothing was changed. Set a new one with MU300_KEEP_PASSWORD=no and MU300_PASSWORD, or remove MU300_KEEP_PASSWORD to keep the password without a check.' "$_pwu" "$OS" "${PW_CHECK:-no answer}")" ;;
            esac
        elif [ "${MU300_KEEP_PASSWORD:-}" = no ] || [ -n "${MU300_PASSWORD:-}" ] || [ "${MU300_PASSWORD_RESET:-}" = yes ]; then
            [ -z "${MU300_PASSWORD:-}" ] || PW_MODE=given
        else
            PW_MODE=kept
        fi
    else
        [ -z "${MU300_PASSWORD:-}" ] || PW_MODE=given
        [ -z "${MU300_KEEP_PASSWORD:-}" ] ||
            warn "$(t 'MU300_KEEP_PASSWORD: the installed {1} has no password of its own to keep; the password below is used' "$OS")"
    fi
    PW_CHECK=
    KEEP_PW=0; case $PW_MODE in kept|checked) KEEP_PW=1 ;; esac
}
# The extras a caller put beside the scripts, mu300/extras/mu300-extra-<name>.tar.gz (the VPN module as vpn, the
# web panel as panel): android-install.sh installs every mu300-extra-<name>.tar.gz of its work directory onto the
# Linux partition (extra/<name>) and checks what it unpacks. Only these names, and only regular files: D is as
# trusted as the scripts in it. EXTRAS: the names found.
EXTRA_NAMES='vpn panel'
extras_find() {
    EXTRAS=
    for _n in $EXTRA_NAMES; do
        _f=$D/extras/mu300-extra-$_n.tar.gz
        if [ -f "$_f" ] && [ ! -L "$_f" ] && [ -s "$_f" ]; then EXTRAS="$EXTRAS $_n"; fi
    done
    EXTRAS=${EXTRAS# }
}
extras_take() {  # into W, for android-install.sh
    for _n in $EXTRAS; do
        cp "$D/extras/mu300-extra-$_n.tar.gz" "$W/mu300-extra-$_n.tar.gz" || die "$(t 'could not copy the installer files')"
    done
}
# The Linux slot gets the boot chain of Android's slot - uboot, sml, teecfg, trustos and vbmeta, one firmware build on
# both slots (issue #86): android-bootchain.sh decides and checks here, android-install.sh copies it before it installs
# anything, with a backup on the Linux filesystem (.mu300/bootchain). MU300_SYNC_BOOTCHAIN=0 skips that, for experts.
plan_bootchain() {
    SYNC_BOOTCHAIN=1; BC_LINE=; BC_REFUSED=; BC_NAMES=
    if [ "${MU300_SYNC_BOOTCHAIN:-1}" = 0 ]; then
        SYNC_BOOTCHAIN=0; BC_LINE=$(t 'boot chain:     not synced (MU300_SYNC_BOOTCHAIN=0)'); return 0
    fi
    # android-bootchain.sh writes only on a model it knows: one taken from MU300_DEVICE keeps its chain as it is
    if [ "${MODEL_KNOWN:-1}" = 0 ]; then
        SYNC_BOOTCHAIN=0; BC_LINE=$(t 'boot chain:     not synced (the model is not recognised)'); return 0
    fi
    _bco=$(asw MU300_BY_NAME="$BY" "$ANDROID_SH" "$D/android-bootchain.sh" check 2>&1) || true
    # the names that differ between the slots (BOOTCHAIN-PLAN <android slot> <linux slot> [names]), for plan_machine
    BC_NAMES=$(printf '%s\n' "$_bco" | sed -n 's/^BOOTCHAIN-PLAN [ab] [ab] *//p' | head -n1)
    bootchain_line "$_bco" ||
        die "$(t 'the boot chain cannot be copied to the Linux slot: {1}' "$BC_REFUSED")
$(t 'Nothing was changed. Experts can skip this step with MU300_SYNC_BOOTCHAIN=0.')"
}
plan_print() {
    echo
    say "$(t 'Plan')"
    echo "  $(t 'system:         {1} (zip {2}, kernel {3})' "$OS${UBUNTU:+ $UBUNTU}" "$TAG" "$KERNEL")"
    if [ "$SD_MODE" = 1 ]; then
        echo "  $(t 'storage:        SD card {1}, {2} ({3})' "$SD_DEV" "$(gib "$TARGET_SIZE")" "$(src_of MU300_STORAGE)")"
    else
        if [ "$IN_PART" = 1 ]; then
            echo "  $(t 'storage:        internal, the GPT partition mu300root at offset {1}, {2} ({3})' "$INT_OFF" "$(gib "$TARGET_SIZE")" "$(src_of MU300_STORAGE)")"
        else
            echo "  $(t 'storage:        internal, offset {1}, {2} ({3})' "$INT_OFF" "$(gib "$TARGET_SIZE")" "$(src_of MU300_STORAGE)")"
        fi
    fi
    if [ "$FORMAT" = 1 ]; then _fs=$(t 'CREATE new ext4 (erases what is there: every installed system, every file in them, the extras)')
    else _fs=$(t 'keep: nothing is erased'); fi
    echo "  $(t 'filesystem:     {1} ({2})' "$_fs" "$(src_of MU300_MODE)")"
    [ -z "$HAVE_SYSTEMS" ] || echo "  $(t 'already there:  {1}' "$HAVE_SYSTEMS")"
    # an update of the system of this zip: what is kept. The others and the extras are not this zip's to touch.
    case " $HAVE_SYSTEMS " in
        *" $OS "*)
            if [ "${MU300_UPDATE_KEEP:-all}" = list ]; then
                echo "  $(t 'update:         {1} {2} -> {3}: only settings, users and home directories are kept (MU300_UPDATE_KEEP=list)' "$OS" "${HAVE_VERSION:-?}" "$TAG")"
            else
                echo "  $(t 'update:         {1} {2} -> {3}: everything you added to it or changed in it is kept (files, settings, accounts and passwords);' "$OS" "${HAVE_VERSION:-?}" "$TAG")"
                echo "  $(t '                packages you installed are installed again at its first boot; what could not stay in place is listed in /etc/mu300/update-report')"
            fi
            [ "$DOWNGRADE" = 0 ] || echo "  $(t '                {1} is the OLDER release (MU300_DOWNGRADE=yes): one from before the first release with a four-field tag keeps only settings, users and home directories' "$TAG")" ;;
    esac
    _others=$(for _os in $HAVE_SYSTEMS; do [ "$_os" = "$OS" ] || printf '%s ' "$_os"; done)
    if [ "$FORMAT" = 0 ] && [ -n "$_others" ]; then
        echo "  $(t 'not touched:    {1}and the extras (the VPN module) stay as they are' "$_others")"
    fi
    echo "  $(t 'boots:          {1} ({2})' "$BOOT_OS" "$(src_of MU300_BOOT_OS)")"
    echo "  $(t 'default boot:   {1} ({2})' "$([ "$DEFAULT_LINUX" = 1 ] && t 'Linux, Android after failed boots' || t 'Android, Linux on demand')" "$(src_of MU300_BOOT)")"
    echo "  $(t 'boot attempts:  {1} failed boots in a row, then Android ({2})' "$BOOT_ATTEMPTS" "$(src_of MU300_BOOT_ATTEMPTS)")"
    echo "  $(t 'hotspot:        {1} ({2})' "$([ "$IMPORT_HOTSPOT" = 1 ] && t 'name and password copied from Android' || t 'not copied')" "$(src_of MU300_HOTSPOT)")"
    echo "  $(t 'GPU files:      {1} ({2})' "$([ "$GPU" = yes ] && t 'included' || t 'not included')" "$(src_of MU300_GPU)")"
    if [ "$PW_MODE" = checked ]; then
        echo "  $(t 'password:       kept: the accounts and passwords of the installed system are not changed; the current password was checked ({1})' "$(src_of MU300_KEEP_PASSWORD)")"
    elif [ "$KEEP_PW" = 1 ]; then
        echo "  $(t 'password:       kept, not checked: the accounts and passwords of the installed system are not changed (a mu300-install.conf inside the zip can check it or set a new one: {1})' "MU300_KEEP_PASSWORD, MU300_CURRENT_PASSWORD, MU300_PASSWORD")"
    elif [ -n "${MU300_PASSWORD:-}" ]; then
        echo "  $(t 'password:       the one from {1} (not shown)' "$(src_of MU300_PASSWORD)")"
    elif [ "$OS" = ubuntu ]; then
        echo "  $(t 'password:       ubuntu for user ubuntu, as in the images ({1} in {2} sets another)' "MU300_PASSWORD" "$TRUSTED_CONF")"
    else
        echo "  $(t 'password:       none: root logs in without one until one is set with passwd or in LuCI, System > Administration ({1} in {2} sets one now)' "MU300_PASSWORD" "$TRUSTED_CONF")"
    fi
    [ -z "$EXTRAS" ] || echo "  $(t 'extras:         {1}' "$EXTRAS")"
    [ "$ADB" = 0 ] || echo "  $(t 'adb to Linux:   {1}' "$(t 'on, for {1}' "${ADB_KEY#* }")")"
    echo "  $(t 'writes:         {1}, boot_{2}, 32 bytes of misc (boot_{3}, the partition table and userdata are not touched)' "$([ "$SD_MODE" = 1 ] && echo "$SD_DEV" || { [ "$IN_PART" = 1 ] && t 'the partition mu300root' || t 'the Linux region'; })" "$LINUX_SLOT" "$ANDROID_SLOT")"
    echo "  $BC_LINE"
    plan_machine
}
# The same plan for a program (MU300_PROGRESS=1): one "MU300-PLAN key=value" per line. Values are the installer's own
# words and numbers (no translated text, never a password or a key); a list is separated by blanks.
plan_machine() {
    [ "${MU300_PROGRESS:-}" = 1 ] || return 0
    _pw=image
    if [ "$KEEP_PW" = 1 ]; then _pw=kept; elif [ -n "${MU300_PASSWORD:-}" ]; then _pw=given; fi
    _pwc=0; [ "${PW_MODE:-}" != checked ] || _pwc=1
    for _l in "tag=$TAG" "system=$SYSTEM" "os=$OS" "ubuntu=$UBUNTU" "kernel=$KERNEL" "device=$DEVICE" \
        "storage=$([ "$SD_MODE" = 1 ] && echo sd || echo internal)" "sd_dev=$([ "$SD_MODE" != 1 ] || echo "$SD_DEV")" \
        "offset=$INT_OFF" "size=$TARGET_SIZE" "format=$FORMAT" "update=$UPDATE" "have=${HAVE_SYSTEMS# }" \
        "boot_os=$BOOT_OS" "default_linux=$DEFAULT_LINUX" "boot_attempts=$BOOT_ATTEMPTS" "hotspot=$IMPORT_HOTSPOT" \
        "gpu=$GPU" "password=$_pw" "password_checked=$_pwc" "extras=$EXTRAS" "adb=$ADB" "linux_slot=$LINUX_SLOT" "android_slot=$ANDROID_SLOT" \
        "sync_bootchain=$SYNC_BOOTCHAIN" "bootchain=$BC_NAMES" "dry_run=${MU300_DRY_RUN:-0}"; do
        echo "MU300-PLAN $_l"
    done
}
# Every setting, what it does and the value this run used (or would have: a refused run writes it too, with what
# was known by then), so nobody has to type it from a README. Any app can write $SDCARD, so it is never opened by
# its name: a new file with a random name is created exclusively and renamed over the target, and rename replaces
# whatever is there (a link included) instead of writing through it.
write_example() {
    _ex=$SDCARD/mu300-install.conf.example
    [ ! -d "$_ex" ] || [ -L "$_ex" ] || return 0
    _rnd=$(head -c 8 /dev/urandom 2>/dev/null | od -An -tx1 | tr -d ' \n')
    _new=$SDCARD/.mu300-install.conf.example.$$.$_rnd
    [ ! -e "$_new" ] && [ ! -L "$_new" ] || return 0
    ( set -C; {
        echo "# MU300 Linux Magisk installer settings. Copy to $SDCARD/mu300-install.conf, edit, install the zip again."
        echo "# Settings marked (root) are taken only from $TRUSTED_CONF, which only root can change: any app can write"
        echo "# to $SDCARD. Copy this file there with: su -c 'cp $SDCARD/mu300-install.conf $TRUSTED_CONF && chmod 600 $TRUSTED_CONF'"
        echo "# Values of the last run (${TAG:-}, $(date '+%Y-%m-%d %H:%M')) are shown; a line starting with # is not used."
        echo "# To use a setting, remove the # in front of its key (only there: the explanation stays a comment line)."
        echo "# MU300_MODE=wipe, MU300_SD_ERASE, MU300_REGION_OVERWRITE, MU300_DOWNGRADE, MU300_UPDATE_KEEP=list and MU300_PASSWORD count for one install: once a"
        echo "# successful install has used one, the installer turns its line in $TRUSTED_CONF into a comment."
        echo
        echo "# internal or sd"
        echo "#MU300_STORAGE=$([ "${SD_MODE:-0}" = 1 ] && echo sd || echo internal)"
        echo "# (root) allow formatting the SD card for Linux: everything on it is erased"
        echo "#MU300_SD_ERASE=yes"
        echo "# (root) use internal free space that holds data"
        echo "#MU300_REGION_OVERWRITE=yes"
        echo "# update: the system of this zip replaces the installed one of its kind, and everything you added to it or"
        echo "# changed in it is kept: files anywhere, settings, accounts and passwords. Packages you installed are installed"
        echo "# again at its first boot. What could not stay in its place is set aside and listed in /etc/mu300/update-report."
        echo "# The other installed systems and the extras (the VPN module) are not touched."
        echo "# wipe (root): the Linux filesystem is made anew. Every installed system, every file in them and the extras"
        echo "# are erased. (Android, its data and the boot fallback are never touched by either.)"
        echo "#MU300_MODE=update"
        echo "# (root) yes: install this zip over a NEWER release of the same system. A release before the first one"
        echo "# with a four-field tag (vYYYY.MM.DD.N) then keeps only settings, users and home directories."
        echo "#MU300_DOWNGRADE=yes"
        echo "# (root) list: keep only what an update kept before the first release with a four-field tag (settings, users, home directories), should"
        echo "# carrying everything over fail. all is the default."
        echo "#MU300_UPDATE_KEEP=all"
        echo "# ubuntu or openwrt: which system boots"
        echo "#MU300_BOOT_OS=${BOOT_OS:-${OS:-}}"
        echo "# linux (default boot, Android after failed boots) or android"
        echo "#MU300_BOOT=linux"
        echo "# 1-6 failed boots in a row before Android"
        echo "#MU300_BOOT_ATTEMPTS=${BOOT_ATTEMPTS:-5}"
        echo "# copy Android's hotspot name and password: yes or no"
        echo "#MU300_HOTSPOT=yes"
        echo "# Mali GPU (OpenCL) files: yes or no"
        echo "#MU300_GPU=yes"
        echo "# (root) 6+ characters; empty: the images' own (Ubuntu: ubuntu/ubuntu; OpenWrt: root without a password);"
        echo "# for an update only from a mu300-install.conf inside the zip"
        echo "#MU300_PASSWORD="
        echo "# (root) yes: the images' own password also when updating (an update keeps the existing one; for an"
        echo "# update only from a mu300-install.conf inside the zip)"
        echo "#MU300_PASSWORD_RESET=yes"
        echo "# An update keeps the password of the installed system. To check it, or to set a new one, put a"
        echo "# mu300-install.conf with these lines at the top of the zip (an update takes them, and MU300_PASSWORD and"
        echo "# MU300_PASSWORD_RESET, only from there): yes keeps it once MU300_CURRENT_PASSWORD is the current one"
        echo "# (no such line: no password, OpenWrt's root as it ships); no sets MU300_PASSWORD (without it: the images' own)"
        echo "#MU300_KEEP_PASSWORD=yes"
        echo "# with yes: the current password, checked against the installed system's before anything is written"
        echo "#MU300_CURRENT_PASSWORD="
        echo "# (root) write the password note to $PW_FILE_SDCARD instead of $PW_FILE_ROOT"
        echo "#MU300_PASSWORD_FILE=sdcard"
        echo "# (root) f50 or u30air, only when the model is not recognised"
        echo "#MU300_DEVICE=$([ "${DEVICE:-}" = u30air ] && echo u30air || echo f50)"
        echo "# en, tr or zh"
        echo "#MU300_LANG=${MU300_LANG:-en}"
        echo "# only show what would be done"
        echo "#MU300_DRY_RUN=1"
        echo "# 0: leave the Linux slot's boot chain (uboot, sml, teecfg, trustos, vbmeta) as it is; experts only"
        echo "#MU300_SYNC_BOOTCHAIN=1"
        echo "# (root) adb over USB to Linux for one computer: the line of its ~/.android/adbkey.pub (it gets a root shell)"
        echo "#MU300_ADB_KEY="
    } > "$_new" ) 2>/dev/null &&
        [ -f "$_new" ] && [ ! -L "$_new" ] && rm -f "$_ex" && mv -f "$_new" "$_ex" 2>/dev/null
    rm -f "$_new" 2>/dev/null
    return 0
}

# ---- the password: the images' well-known one, unless a trusted conf gives one. Ubuntu's user ubuntu has the
# password ubuntu; OpenWrt's root has none, as stock OpenWrt ships it (dropbear and LuCI let root in without one until
# the user sets one). Issue #167: a random password shown in nothing but a root-only file, which the next zip
# overwrote, left added systems nobody could log in to.
CRYPT_ITOA=./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz
random_chars() {  # random_chars ALPHABET N: bytes past the last whole multiple of the alphabet are skipped (no bias)
    head -c 256 /dev/urandom | od -An -tu1 -v | awk -v a="$1" -v n="$2" '
        BEGIN { l = length(a); lim = int(256 / l) * l }
        { for (i = 1; i <= NF && got < n; i++) if ($i < lim) { s = s substr(a, $i % l + 1, 1); got++ } }
        END { if (got < n) exit 1; print s }'
}
password_hash() {  # password_hash PASSWORD: SHA-512 crypt, password on stdin, as install.sh's sha512crypt.py makes it
    _salt=$(random_chars "$CRYPT_ITOA" 16) || return 1
    _h=$(printf '%s\n' "$1" | "$UBB" mkpasswd -m sha512 -S "$_salt" -P 0) || return 1
    case $_h in "\$6\$$_salt\$"?*) printf '%s' "$_h" ;; *) return 1 ;; esac
}
# FILE written with what CONTENT_CMD prints, mode 600, never through a link someone placed there: a new file with a
# random name is created exclusively in the same directory and renamed over FILE (rename replaces a link instead of
# writing through it; a link to a directory is removed first, or mv would move the file into that directory)
replace_file() {  # replace_file FILE CONTENT_CMD...
    _rf=$1; shift
    [ ! -d "$_rf" ] || [ -L "$_rf" ] || return 1
    _rnd=$(head -c 8 /dev/urandom 2>/dev/null | od -An -tx1 | tr -d ' \n')
    _new=${_rf%/*}/.${_rf##*/}.$$.$_rnd
    if ( umask 077; set -C; "$@" > "$_new" ) 2>/dev/null && [ -f "$_new" ] && [ ! -L "$_new" ] &&
        chmod 600 "$_new" && rm -f "$_rf" && mv -f "$_new" "$_rf" 2>/dev/null; then
        return 0
    fi
    rm -f "$_new" 2>/dev/null
    return 1
}

# ---- build everything in $W before anything is written
build_vendor() {
    _miss=$(collect_firmware "$W/fw") || die "$(t 'these firmware files could not be read from the device:{1}' "$_miss")
$(t 'Wi-Fi and Bluetooth need them; without them the system installs and boots but has no hotspot.')"
    collect_subset "$D/subset-files.txt" "$W/subset" || die "$(t 'could not read the Android files the modem needs from this device')"
    _gpu=
    if [ "$GPU" = yes ]; then
        if collect_gpu "$D/gpu-files.txt" "$W/gpu"; then _gpu=$W/gpu
        else warn "$(t 'the Mali GPU files are incomplete on this device; installing without them')"; fi
    fi
    vendor_overlay "$OS" "$W/fw" "$W/subset" "$_gpu" "$W/mu300-vendor-$OS.tar.gz" || die "$(t 'could not pack the vendor files')"
}
build_boot() {
    mkdir -p "$W/ramdisk/etc" "$W/boot"
    echo "$DEVICE" > "$W/ramdisk/etc/mu300-device"
    echo "$LINUX_SLOT" > "$W/ramdisk/etc/mu300-linux-slot"
    bc_files "$LIVE_BC" "$W/ramdisk/etc" || die "$(t 'could not build the boot control blocks')"
    cp -a "$W/subset" "$W/ramdisk/android" || die "$(t 'could not copy the Android files into the boot ramdisk')"
    # Linux on slot a needs an init that knows it (Tasks 1-3); the init comes from the bundle's generic segment
    if [ "$LINUX_SLOT" = a ]; then
        "$MAGISKBOOT" decompress "$KB/ramdisk-generic.lz4" "$W/generic.cpio" >/dev/null 2>&1 &&
            grep -q 'mu300-linux-slot' "$W/generic.cpio" ||
            die "$(t 'Android runs from slot b, and the kernel of this zip cannot boot Linux from slot a yet; use a newer zip')"
        rm -f "$W/generic.cpio"
    fi
    device_segment "$W/ramdisk" "$W/device.lz4" || die "$(t 'could not build the boot ramdisk (magiskboot)')"
    linux_boot_image "$BOOT_ANDROID" "$KB" "$W/device.lz4" "$W/boot" || die "$(t 'could not build the boot image')"
    # the Android-side switch arms a slot only when its image says loglevel=5 (Android's own say nothing)
    [ "$(dd if="$W/boot/new" bs=1 skip=44 count=11 2>/dev/null | od -An -tx1 | tr -d ' \n')" = 6c6f676c6576656c3d3500 ] ||
        die "$(t 'could not build the boot image')"
    say "$(t 'boot image for slot {1}: kernel {2}, {3} bytes' "$LINUX_SLOT" "$(cat "$KB/kernel.release" 2>/dev/null || echo 5.4)" "$(( $(fsize "$W/boot/new") + $(fsize "$W/boot/vbmeta") ))")"
}
# PWHASH goes into the system's own account (ubuntu, OpenWrt's root) when PW_SET=1; empty with PW_SET=1 it is OpenWrt's
# root without a password. PW_SET=0: android-install.sh carries the accounts over (KEEP_PW)
build_password() {
    PWHASH=; PW_SET=0; KEEP_PW_OSES=
    if [ "$KEEP_PW" = 1 ]; then KEEP_PW_OSES=$OS; return 0; fi
    PW_SET=1
    if [ -n "${MU300_PASSWORD:-}" ]; then PWHASH=$(password_hash "$MU300_PASSWORD") || die "$(t 'could not hash the password')"
    elif [ "$OS" = ubuntu ]; then PWHASH=$(password_hash ubuntu) || die "$(t 'could not hash the password')"
    fi
}

# ---- install: the systems, then the boot image, then misc
# mu300-install.env as install.sh writes it (storage.sh): OFF and INT_SIZE are the internal region, SIZE the region
# or, in SD mode, the card; plus PW_SET (build_password) and DOWNGRADE (plan_choices), which install.sh does not write
install_env() {
    OFF=$INT_OFF; SIZE=$INT_SIZE
    if [ "$SD_MODE" = 1 ]; then SIZE=$SD_BYTES; fi
    # DOWNGRADE: plan_choices let an older release through (MU300_DOWNGRADE=yes); android-install.sh checks again
    ( umask 077; { write_install_env && echo "PW_SET=$PW_SET" && echo "DOWNGRADE=${DOWNGRADE:-0}"; } > "$W/mu300-install.env" ) ||
        die "$(t 'could not write {1}' "$W/mu300-install.env")"
}
install_systems() {
    say "$(t 'Installing {1} (this takes a few minutes)' "$OS")"
    cp "$D/android-install.sh" "$D/android-mount-mu300root.sh" "$D/android-bootchain.sh" "$W/" || die "$(t 'could not copy the installer files')"
    extras_take
    install_env
    asw "$ANDROID_SH" "$W/android-install.sh" 2>&1 | tee "$W/device-install.log"
    _keep=$WORK_PARENT/mu300-device-install.log
    if ! grep -q MU300-INSTALL-OK "$W/device-install.log"; then
        # W goes with this run: the device script's output is kept where root can read it (a timed-out SD-card stage
        # names itself there, FINDINGS 50)
        _msg=$(t 'installation on the device failed; boot_{1} and misc were not changed' "$LINUX_SLOT")
        if ( umask 077; cp "$W/device-install.log" "$_keep" ) 2>/dev/null && chmod 600 "$_keep" 2>/dev/null; then
            _msg="$_msg
$(t 'the device log was kept at {1}' "$_keep")"
        fi
        die "$_msg"
    fi
    rm -f "$_keep"
    if [ -s "$KB/kernel.release" ]; then        # a mainline bundle: its modules into every system on the filesystem
        mount_target "$W/mnt" || die "$(t 'could not mount the Linux filesystem for the kernel modules')"
        DISK=$W/mnt MU300_NO_DEPMOD=1 kernel_modules_into_systems "$KB" ||
            { umount_target "$W/mnt"; die "$(t 'could not install the kernel modules')"; }
        sync
        umount_target "$W/mnt" || die "$(t 'could not unmount the Linux filesystem from {1}' "$W/mnt")"
    fi
}
# boot_readback DEV: what write_boot put on DEV - the image with its vbmeta from offset 0, the AVB footer in the
# partition's last 64 bytes - read back from the medium into files of their own, in whole 4096-byte blocks, by one dd
# each whose status counts (a pipeline would turn a read error into a short hash and say no more), after the page
# cache is dropped, and compared by sha256 with what was written. write_boot has its own check (mu300-update's,
# through a pipeline); this is the installer's last word before misc is armed (FINDINGS 50). Prints why it failed.
boot_readback() {
    _bl=$(fsize "$W/boot/written"); _bn=$(( (_bl + 4095) / 4096 )); _ps=$(part_size "$1")
    rm -f "$W/boot/readback" "$W/boot/readback.tail"
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
    dd if="$1" of="$W/boot/readback" bs=4096 count="$_bn" 2>/dev/null || { echo "dd failed"; return 1; }
    [ "$(fsize "$W/boot/readback")" = $((_bn * 4096)) ] || { echo "short read"; return 1; }
    [ "$(head -c "$_bl" "$W/boot/readback" | sha256sum | cut -d' ' -f1)" = "$(sha256sum < "$W/boot/written" | cut -d' ' -f1)" ] ||
        { echo "sha256 differs"; return 1; }
    dd if="$1" of="$W/boot/readback.tail" bs=4096 skip=$((_ps / 4096 - 1)) count=1 2>/dev/null || { echo "dd failed (footer)"; return 1; }
    [ "$(tail -c 64 "$W/boot/readback.tail" | sha256sum | cut -d' ' -f1)" = "$(sha256sum < "$W/boot/newfooter" | cut -d' ' -f1)" ] ||
        { echo "sha256 differs (footer)"; return 1; }
    rm -f "$W/boot/readback" "$W/boot/readback.tail"
}
install_boot() {
    say "$(t 'Writing and verifying boot_{1}' "$LINUX_SLOT")"
    cat "$W/boot/new" "$W/boot/vbmeta" > "$W/boot/written" || die "$(t 'could not build the boot image')"
    write_boot "$BOOT_LINUX" "$(part_size "$BOOT_LINUX")" "$W/boot/new" "$W/boot/vbmeta" "$W/boot/newfooter" ||
        die "$(t 'boot_{1} did not verify after writing; misc was not changed, so Android keeps booting' "$LINUX_SLOT")"
    _why=$(boot_readback "$BOOT_LINUX") ||
        die "$(t 'boot_{1} read back from the eMMC is not the image that was written ({2}); Linux was not armed: misc was not changed, so Android keeps booting' "$LINUX_SLOT" "$_why")"
}
arm_linux() {  # the Linux slot's trial block, built from the misc block read at the start; misc must still hold that
    _blk=$W/ramdisk/etc/misc-bc-slot-$LINUX_SLOT-trial.bin
    [ "$(hex_at "$MISC" 2048 32)" = "$LIVE_BC" ] || die "$(t 'misc changed during the installation; Linux was not armed. Install the zip again.')"
    if dd if="$_blk" of="$MISC" bs=1 seek=2048 conv=notrunc 2>/dev/null && sync &&
        [ "$(hex_at "$MISC" 2048 32)" = "$(od -An -tx1 -v "$_blk" | tr -d ' \n')" ]; then
        return 0
    fi
    # misc may now hold anything: the block it held before goes back, and is read back too
    dd if="$W/misc-bc-live.bin" of="$MISC" bs=1 seek=2048 conv=notrunc 2>/dev/null || true
    sync || true
    [ "$(hex_at "$MISC" 2048 32)" != "$LIVE_BC" ] ||
        die "$(t 'misc did not verify after writing; the block it held before was written back and verified, so Android boots as before. Linux was not armed.')"
    # W goes when this exits: the block is kept where root can still reach it
    _keep=$WORK_PARENT/mu300-misc-bc-before.bin
    cp "$W/misc-bc-live.bin" "$_keep" 2>/dev/null || true
    die "$(t 'misc did not verify after writing, and the block it held before could not be written back: misc holds unknown bytes, and the device may not start Android. Do not reboot. Write the saved block back as root: {1}' "dd if=$_keep of=$MISC bs=1 seek=2048 conv=notrunc")"
}
# the note on the password, in the file: never the password itself
pw_note() {
    if [ -n "${MU300_PASSWORD:-}" ]; then echo "the one given as MU300_PASSWORD in $(src_of MU300_PASSWORD)"
    elif [ "$OS" = ubuntu ]; then echo "ubuntu (as in the images; change it after the first login: passwd)"
    else echo "none yet: set one with passwd, or in LuCI (System > Administration)"
    fi
}
pw_text() {
    printf 'MU300 Linux %s, %s\nuser: %s\npassword: %s\n' "$TAG" "$(date '+%Y-%m-%d %H:%M')" "$_users" "$(pw_note)"
    # The other system, installed beside this one by an earlier zip, keeps its password: so does the file (its block
    # from the file this one replaces), or the first zip's note would be lost with its output. Not after a wipe
    # (HAVE_SYSTEMS is empty then), and never a block of this run's user.
    case $OS in ubuntu) _ou=root _os=openwrt ;; *) _ou=ubuntu _os=ubuntu ;; esac
    case " $HAVE_SYSTEMS " in *" $_os "*|*" $_os-luci "*) ;; *) return 0 ;; esac
    [ -f "$PW_FILE" ] && [ ! -L "$PW_FILE" ] || return 0
    awk -v u="user: $_ou" '
        /^MU300 Linux / { if (keep) printf "\n%s", b; b = ""; keep = 0 }
        $0 == "" { next }
        { b = b $0 "\n" }
        $0 == u { keep = 1 }
        END { if (keep) printf "\n%s", b }' "$PW_FILE"
}
# never the password of the conf file: it is not shown anywhere
pw_show() {
    if [ "$KEEP_PW" = 1 ]; then echo "  $(t 'password for {1}: unchanged (the one this system had)' "$_users")"
        [ "$PW_MODE" = checked ] || echo "  $(t '(not checked: a mu300-install.conf inside the zip can check it or set a new one, see {1})' "$SDCARD/mu300-install.conf.example")"
    elif [ -n "${MU300_PASSWORD:-}" ]; then echo "  $(t 'password for {1}: the one from {2}' "$_users" "$(src_of MU300_PASSWORD)")"
    elif [ "$OS" = ubuntu ]; then echo "  $(t 'password for ubuntu: "ubuntu"   (as in the images; change it after the first login: passwd)')"
    else echo "  $(t 'password for root: none yet - set one with passwd, or in LuCI (System > Administration)')"
    fi
}
# Shown and noted before anything is written: android-install.sh puts the password into the systems, and a run that
# fails or is killed after that must not leave a Linux whose password nobody was told. No file, no installation.
save_password() {
    _users=root; [ "$OS" != ubuntu ] || _users=ubuntu
    [ "$KEEP_PW" = 0 ] || return 0                # nothing new to show or note; the file stays as it is
    replace_file "$PW_FILE" pw_text || die "$(t 'could not write {1}; nothing was installed' "$PW_FILE")"
    pw_show
}
# A MU300_PASSWORD or MU300_PASSWORD_RESET line in the trusted conf, and an erasing setting this run used
# (USED_KEYS), have done their job: each becomes a comment (the file stays root's, mode 600). A key is matched as
# conf_load reads it (blanks around and inside it do not count); a conf inside the zip is never edited.
conf_drop_used() {
    awk -v d="$_drop " '{ l = $0; sub(/^[ \t]*/, "", l); k = l; sub(/=.*/, "", k); gsub(/[ \t]/, "", k)
           if (index(l, "=") && k != "" && index(d, " " k " ")) print "# " k " was used by the installer and removed"; else print }' "$TRUSTED_CONF"
}
# Linux is armed by now: nothing here may fail the run (main ignores its status), or customize.sh would call an
# installed Linux "not installed"
report() {
    _ip=192.168.77.1; [ "$DEVICE" != u30air ] || _ip=192.168.78.1
    [ "$DEVICE" != v50 ] || _ip=192.168.79.1
    _drop=
    for _k in $USED_KEYS MU300_PASSWORD MU300_PASSWORD_RESET; do
        eval "_s=\${SRC_$_k:-}"
        [ "$_s" != "$TRUSTED_CONF" ] || _drop="$_drop $_k"
    done
    if [ -n "$_drop" ] && conf_trusted "$TRUSTED_CONF"; then
        if replace_file "$TRUSTED_CONF" conf_drop_used; then
            say "$(t 'used by this installation and removed from {1}:{2}' "$TRUSTED_CONF" "$_drop")"
        else
            warn "$(t 'could not remove{1} from {2}; remove it by hand' "$_drop" "$TRUSTED_CONF")"
        fi
    fi
    echo
    say "$(t 'Done. Reboot to start {1}.' "$BOOT_OS")"
    pw_show
    echo "  $(t 'USB network: {1}   SSH: {2}' "$_ip" "$_users@$_ip")"
    echo "  $(t 'switch systems: mu300-os {1}   back to Android: mu300-next-boot android' "$(echo $_after | tr ' ' '|')")"
    echo "  $(t 'If Linux does not start, the device returns to Android by itself.')"
    # the zip holds the release's systems and kernel only: the VPN is the module of its own repository, which
    # mu300-extra downloads on the device (one already on the Linux partition stays, and each system links it at boot)
    echo "  $(t 'The VPN (mu300-vpn, with Xray and sing-box) is a module of its own: {1}. It can also be added later on the device:' https://github.com/dikeckaan/mu300-linux-vpn)"
    echo "    sudo mu300-extra install vpn"
}

main() {
    trap cleanup EXIT
    [ -n "$MU300_LANG" ] || MU300_LANG=$(android_lang)
    conf_valid MU300_LANG "$MU300_LANG" || MU300_LANG=en      # it is part of a file name in t()
    manifest_load "$D/manifest"
    echo "MU300 Linux $TAG"
    step check
    conf_read
    conf_check
    env_check
    stall_check
    detect_device
    slot_setup
    work_setup
    misc_save
    step unpack
    payload_unpack
    step plan
    plan_storage
    inspect_target
    plan_choices
    extras_find
    plan_bootchain
    plan_print
    if [ "${MU300_DRY_RUN:-}" = 1 ]; then say "$(t 'Dry run: nothing was written.')"; exit 3; fi
    step vendor
    build_vendor
    step image
    build_boot
    build_password
    save_password
    step systems
    install_systems
    step boot
    install_boot
    step arm
    arm_linux
    step done
    report || true
}
[ -z "${MU300_LIB:-}" ] || return 0
main "$@"
