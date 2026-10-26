#!/bin/sh
# MU300 / ZTE F50 Linux installer. Run on a macOS/Linux host with the device booted in rooted Android (adb + su).
#
#   ./install.sh --check         only inspect the device: is the free eMMC region there and empty? (writes nothing)
#
# On the 32 GB variant there is no free region at all (userdata fills the disk). The installer then offers
# to shrink userdata to make one - EXPERIMENTAL, it rewrites the partition table and erases everything in
# Android, so take a full backup with tools/backup-device.sh first. Android's own recovery resets Android's data
# (that is what works with its encryption), and the installation resumes on the next run of this installer, once
# Android is set up again with USB debugging and root (tools/resume.sh; a transaction in ~/mu300-backups):
#   ./install.sh --resume        continue the prepared installation, or stop when there is none for this device
#   ./install.sh --no-resume     install as if nothing had been prepared (MU300_RESUME=0)
#   ./install.sh --transaction FILE   that transaction, not the newest one for the device (MU300_TRANSACTION)
#   ./install.sh                 install Ubuntu, OpenWrt or both from the prebuilt release images
#   ./install.sh --build         build kernel outputs/root filesystems locally instead (see README "Build and run")
#   ./install.sh --backup        only copy what an installation reads first to this computer: Android's boot image
#                                and the first block of misc, checked against the device (writes nothing to it)
#   ./install.sh --adb-path      print the adb this installer would use, and stop
#   ./install.sh --package DIR   install the system and the kernel of a release's Magisk zip unpacked at DIR, from its
#                                files and without a download (MU300_PACKAGE=DIR; the zip's own computer/install.sh,
#                                which its start.sh runs, finds them by itself: START-HERE.md in the zip, tools/package.sh)
#   ./install.sh --answers FILE  ask nothing: every question is answered by its variable, from FILE (NAME=value
#                                lines) or the environment; a question without an answer is an error that names the
#                                variable. MU300_PROGRESS=1 adds a machine-readable progress stream. For programs
#                                that drive the installer: tools/answers.sh, docs/INSTALLER-AUTOMATION.md
# The variable of each question, for --answers (a question is asked only where the installer asks it by hand too):
#   MU300_LANG=en|tr|zh                    the language
#   ANDROID_SERIAL=<serial>                the adb device, with more than one attached (or MU300_ANS_DEVICE=<its
#                                          number in the list)
#   MU300_ANS_REBOOT_ANDROID=yes|no        the device runs Linux: reboot it into Android over SSH
#   MU300_ANS_UNKNOWN_MODEL=yes|no         this is no F50 or U30 Air: continue anyway
#   MU300_STORAGE=internal|sd              with an SD card: where the Linux filesystem goes
#   MU300_ANS_INTERNAL_OVER_CARD=internal  internal storage although the card holds an installation
#   MU300_ANS_MAKE_ROOM=yes|no             no free space: shrink userdata (erases Android's data), then
#   MU300_ANS_LINUX_GIB=<number>             how many GiB for Linux,
#   MU300_ANS_ERASE_ANDROID=ERASE            the word that allows it, and
#   MU300_ANS_REBOOT=yes|no                  reboot afterwards
#   MU300_ANS_OVERWRITE=overwrite          the free space holds data: use it anyway
#   MU300_ANS_BETA=yes|no                  a beta is out: install it (MU300_RELEASE=<tag> names the release instead)
#   MU300_ANS_SYSTEMS=1|2|3                Ubuntu, OpenWrt, both
#   MU300_OPENWRT=plain|luci               which OpenWrt
#   MU300_ANS_BOOT_OS=<system>             with two systems: the one that boots
#   MU300_ANS_UBUNTU=24.04|26.04           which Ubuntu
#   MU300_ANS_DEFAULT_LINUX=yes|no         boot Linux by default
#   MU300_ANS_BOOT_ATTEMPTS=1..6           failed boots before Android (asked with default Linux)
#   MU300_ANS_HOTSPOT=yes|no               copy Android's hotspot name and password
#   MU300_ANS_GPU=yes|no                   the Mali GPU userspace
#   MU300_ANS_VPN=yes|no                   the VPN module
#   MU300_ANS_ADB=yes|no                   adb to Linux (on for every computer): this computer's key only
#   MU300_ANS_PANEL=yes|no                 the web panel for Ubuntu
#   MU300_ANS_KERNEL=5.4|6.18|7.2|7.3      the kernel
#   MU300_ANS_EXISTING=update|wipe         Linux is installed already
#   MU300_ANS_ERASE_SD=ERASE               a new filesystem on the SD card erases it
#   MU300_ANS_KEEP_PASSWORD=yes|no         an update of a system with a login password: keep it (its current password
#                                          is asked and checked on the device) or set the new one below
#   MU300_ANS_CURRENT_PASSWORD=<password>  with keep: the current password (empty: none, as OpenWrt's root ships);
#                                          for one system: MU300_ANS_CURRENT_PASSWORD_UBUNTU,
#                                          MU300_ANS_CURRENT_PASSWORD_OPENWRT, MU300_ANS_CURRENT_PASSWORD_OPENWRT_LUCI
#   MU300_ANS_PASSWORD=<password>          for "ubuntu" (Ubuntu) and "root" (OpenWrt), 6 characters or more (not
#                                          asked when an update keeps the passwords of every system it installs)
#   MU300_ANS_CONFIRM=INSTALL              the summary's question; @stdin waits for it on standard input
# The Linux slot gets the boot chain of Android's slot (uboot, sml, teecfg, trustos, vbmeta; backed up first):
# MU300_SYNC_BOOTCHAIN=0 skips that.
# An update never goes back to an older release by itself (the device refuses before it writes anything: the older
# release's updater may not carry over everything of the user's): MU300_DOWNGRADE=yes allows it.
# It first brings this copy of the project up to date with GitHub (MU300_NO_SELF_UPDATE=1 skips that), and asks for
# the language (MU300_LANG=en|tr|zh skips that). Started by a person at a terminal for a plain installation, it then
# offers the graphical installer of gui/, a window that drives this script (MU300_GUI=0: never ask, MU300_GUI=1:
# start it without asking; it needs a Python 3.8 or newer with tkinter, which tools/gui-launch.sh looks for).
#
# Prebuilt: needs adb, python3, lz4, curl. Downloads the images of the newest release (or $MU300_RELEASE) and checks
# their sha256. When GitHub has a prerelease newer than that release it asks whether to install the beta instead
# (default: no; tools/releases.sh). The VPN, when wanted, is the module of github.com/dikeckaan/mu300-linux-vpn: its latest release
# (MU300_VPN_URL: another place with its files and SHA256SUMS; MU300_VPN_MODULE=FILE: a copy on this computer).
# Build:    needs adb, docker, python3, lz4 and the kernel outputs in $MU300_KERNEL_OUT (default: out/, kernel/build-all.sh).
# The published images contain no proprietary files: Wi-Fi/Bluetooth firmware and the Android modem/GPU userspace are
# pulled from *your* device into $MU300_WORK (default: work/), never leave the host except to your device.
set -eu
TOP=$(cd "$(dirname "$0")" && pwd)
KOUT=${MU300_KERNEL_OUT:-$TOP/out}
WORK=${MU300_WORK:-$TOP/work}
OWRT_VER=25.12.6
# empty: the newest published release, looked up when it is needed. It used to be a fixed tag that nobody
# remembered to move, so new installs kept getting an old release long after fixes were published.
T=/data/local/tmp
MODE=prebuilt; CHECK_ONLY=0; BACKUP_ONLY=0; ADB_PATH_ONLY=0; ANSWERS_FILE=; _opt=
for a in "$@"; do
    if [ "$_opt" = answers ]; then ANSWERS_FILE=$a; _opt=; continue; fi
    if [ "$_opt" = transaction ]; then MU300_TRANSACTION=$a; export MU300_TRANSACTION; _opt=; continue; fi
    if [ "$_opt" = package ]; then MU300_PACKAGE=$a; export MU300_PACKAGE; _opt=; continue; fi
    case $a in
        --check) CHECK_ONLY=1 ;;
        --build) MODE=build ;;
        --prebuilt) MODE=prebuilt ;;
        --backup) BACKUP_ONLY=1 ;;
        --adb-path) ADB_PATH_ONLY=1 ;;
        --answers) _opt=answers ;;
        --answers=*) ANSWERS_FILE=${a#--answers=} ;;
        --resume) MU300_RESUME=1; export MU300_RESUME ;;
        --no-resume) MU300_RESUME=0; export MU300_RESUME ;;
        --transaction) _opt=transaction ;;
        --transaction=*) MU300_TRANSACTION=${a#--transaction=}; export MU300_TRANSACTION ;;
        --package) _opt=package ;;
        --package=*) MU300_PACKAGE=${a#--package=}; export MU300_PACKAGE ;;
        -h|--help) sed -n '2,/^set -eu/{/^set -eu/d;s/^# \{0,1\}//p;}' "$0"; exit 0 ;;
        *) echo "unknown option $a (see --help)" >&2; exit 2 ;;
    esac
done
[ -z "$_opt" ] || { echo "--$_opt needs a file or directory (see --help)" >&2; exit 2; }
. "$TOP/tools/answers.sh"    # say, die, ask; the answers mode and the progress stream
answers_setup                # (before anything reads a MU300_* variable: an answers file sets them)
RELEASE=${MU300_RELEASE:-}
REPO=${MU300_REPO:-dikeckaan/mu300-linux}
. "$TOP/tools/i18n.sh"
. "$TOP/tools/storage.sh"
. "$TOP/tools/releases.sh"
. "$TOP/tools/resume.sh"
. "$TOP/tools/package.sh"
# adb from platform-tools unzipped next to the project (or into the usual places) needs no PATH entry
find_adb() {
    command -v adb >/dev/null && return 0
    for d in "$TOP" "$TOP/platform-tools" "$PWD/platform-tools" "$HOME/platform-tools" "$HOME/Downloads/platform-tools" \
             "$HOME/Library/Android/sdk/platform-tools" "$HOME/Android/Sdk/platform-tools"; do
        [ -x "$d/adb" ] && { PATH="$d:$PATH"; export PATH; return 0; }
    done
    return 1
}
if [ $ADB_PATH_ONLY = 1 ]; then find_adb && command -v adb; exit; fi
if [ $ANSWERS_MODE = 1 ]; then
    case $MU300_LANG in en|tr|zh) ;; *) die "no answer to the language question: set MU300_LANG to en, tr or zh" ;; esac
fi
choose_language
progress hello script install.sh version 1 mode "$([ $CHECK_ONLY = 1 ] && echo check || { [ $BACKUP_ONLY = 1 ] && echo backup || echo $MODE; })"
# an unpacked Magisk zip (tools/package.sh): its system and kernel, its release, no download and no self-update -
# the installer that came with the files is the one that knows them
PKG=$(package_dir)
if [ -n "$PKG" ]; then
    package_load "$PKG"
    [ $MODE != build ] || die "$(t '--build builds the systems itself: run it from a copy of the project, not from an unpacked Magisk zip')"
    RELEASE=$PKG_TAG
    info package "$PKG"; info package.system "$PKG_SYSTEM"; info package.kernel "$PKG_KERNEL"; info package.release "$PKG_TAG"
    echo "$(t 'Package: {1} with kernel {2}, release {3} (unpacked Magisk zip at {4})' "$PKG_SYSTEM" "$PKG_KERNEL" "$PKG_TAG" "$PKG")"
fi
# first of all, make this the newest installer (it restarts itself when it changed) - not from a package, and not
# while a prepared installation waits: its transaction is bound to the tools that prepared it (tools/resume.sh)
. "$TOP/tools/self-update.sh"
if [ -n "$PKG" ]; then
    :
elif resume_pending; then
    echo "  $(t 'a prepared installation is waiting (tools/resume.sh): this installer does not update itself until it is done')"
else
    self_update "$0" "$@"
fi
# the graphical installer, offered once to a person who starts a plain installation
. "$TOP/tools/gui-launch.sh"
[ $CHECK_ONLY = 1 ] || [ $BACKUP_ONLY = 1 ] || [ $MODE != prebuilt ] || gui_offer
# --- fetch begin
# fetch URL OUT: GitHub's release CDN throttles a single connection hard in some regions (measured 0.2 MB/s
# against a 180 Mbit/s line), so pull the file as parallel ranges and fall back to one stream when that fails.
fetch() {
    _u=$1; _o=$2
    _len=$(curl -fsSLI "$_u" | awk 'tolower($1) == "content-length:" {print $2}' | tr -d '\r' | tail -n1)
    fetch_watch "$_o" "$_len"
    _fr=0; fetch_get "$_u" "$_o" "$_len" || _fr=$?
    fetch_unwatch "$_o" "$_len" $_fr
    return $_fr
}
# For the progress stream: while a download runs, how many of its bytes are there, once a second (the file and the
# parts of a parallel download together). Whoever reads the stream works out the speed and the time left.
FETCH_WATCH=
fetch_watch() {  # fetch_watch OUT LENGTH
    [ -n "$PROGRESS_FD" ] || return 0
    _fw=${1##*/}; _fw=${_fw%.part}      # the file's name in the release, not the name it has while it comes in
    progress download file "$_fw" bytes 0 total "${2:-0}" state start
    ( trap 'exit 0' TERM
      while :; do
          sleep 1
          # (while the parts are being joined both they and the whole file are there: never more than the length)
          progress download file "$_fw" bytes "$(wc -c "$1" "$1".part[0-9]* 2>/dev/null | awk -v t="${2:-0}" 'END { n = $1 + 0; if (t > 0 && n > t) n = t; print n }')" total "${2:-0}" state running
      done ) &
    FETCH_WATCH=$!
}
fetch_unwatch() {  # fetch_unwatch OUT LENGTH STATUS
    [ -n "$FETCH_WATCH" ] || return 0
    kill "$FETCH_WATCH" 2>/dev/null || true
    wait "$FETCH_WATCH" 2>/dev/null || true
    FETCH_WATCH=
    progress download file "$_fw" bytes "$(wc -c < "$1" 2>/dev/null | tr -d ' ')" total "${2:-0}" state "$([ "$3" = 0 ] && echo done || echo failed)"
}
fetch_get() {  # fetch_get URL OUT LENGTH
    _u=$1; _o=$2; _len=$3
    # curl's own bar is for a terminal; a program that reads the progress stream gets the bytes from there
    _bar=--progress-bar; [ -z "$PROGRESS_FD" ] || _bar=-sS
    _jobs=${MU300_FETCH_JOBS:-8}
    if [ -z "$_len" ] || [ "$_len" -lt 8000000 ] 2>/dev/null || [ "$_jobs" -le 1 ] 2>/dev/null; then
        curl -fL --retry 3 $_bar -o "$_o" "$_u"
        return
    fi
    _part=$((_len / _jobs + 1)); _i=0; _pids=
    rm -f "$_o".part*
    while [ $_i -lt "$_jobs" ]; do
        _s=$((_i * _part)); _e=$((_s + _part - 1))
        [ $_e -ge "$_len" ] && _e=$((_len - 1))
        curl -fsSL --retry 3 -r "$_s-$_e" -o "$_o.part$_i" "$_u" &
        _pids="$_pids $!"; _i=$((_i + 1))
    done
    _ok=1
    for _p in $_pids; do wait "$_p" || _ok=0; done
    if [ $_ok = 1 ]; then
        cat "$_o".part* > "$_o"; rm -f "$_o".part*
        [ "$(wc -c < "$_o" | tr -d ' ')" = "$_len" ] && return
    fi
    rm -f "$_o".part*
    echo "  $(t 'parallel download failed, retrying as a single stream')"
    curl -fL --retry 3 $_bar -o "$_o" "$_u"
}
# --- fetch end

# fetch_vpn_module: the VPN module's latest release (or MU300_VPN_MODULE, a copy on this computer) into $WORK/vpn-module,
# checked against that release's SHA256SUMS; sets EXTRA_VPN. SHA256SUMS is fetched every time: under releases/latest
# the same name is a new file with every release of the module.
fetch_vpn_module() {
    _vd=$WORK/vpn-module
    mkdir -p "$_vd"
    if [ -n "${MU300_VPN_MODULE:-}" ]; then
        [ -s "$MU300_VPN_MODULE" ] || die "$(t '{1} not found' "$MU300_VPN_MODULE")"
        _w=$(awk -v f="${MU300_VPN_MODULE##*/}" '$2 == f || $2 == "*" f {print $1; exit}' "$(dirname "$MU300_VPN_MODULE")/SHA256SUMS" 2>/dev/null)
        _h=$( (shasum -a 256 "$MU300_VPN_MODULE" 2>/dev/null || sha256sum "$MU300_VPN_MODULE") | cut -d' ' -f1)
        [ -z "$_w" ] || [ "$_h" = "$_w" ] || die "$(t 'checksum mismatch for {1}' "$MU300_VPN_MODULE")"
        EXTRA_VPN=$MU300_VPN_MODULE
        return
    fi
    _vb=${MU300_VPN_URL:-https://github.com/dikeckaan/mu300-linux-vpn/releases/latest/download}
    _f=mu300-linux-vpn.tar.gz
    say "$(t 'Downloading the VPN module')"
    curl -fsSL -o "$_vd/SHA256SUMS" "$_vb/SHA256SUMS" || die "$(t 'cannot download {1}' "$_vb/SHA256SUMS")"
    _w=$(awk -v f="$_f" '$2 == f || $2 == "*" f {print $1; exit}' "$_vd/SHA256SUMS")
    [ -n "$_w" ] || die "$(t '{1} is not part of release {2}' "$_f" "$_vb")"
    _h=$( (shasum -a 256 "$_vd/$_f" 2>/dev/null || sha256sum "$_vd/$_f" 2>/dev/null) | cut -d' ' -f1)
    if [ "$_h" != "$_w" ]; then
        echo "  $_f"
        fetch "$_vb/$_f" "$_vd/$_f.part" || die "$(t 'download of {1} failed' "$_f")"
        _h=$( (shasum -a 256 "$_vd/$_f.part" 2>/dev/null || sha256sum "$_vd/$_f.part") | cut -d' ' -f1)
        [ "$_h" = "$_w" ] || die "$(t 'checksum mismatch for {1}' "$_f")"
        mv "$_vd/$_f.part" "$_vd/$_f"
    fi
    EXTRA_VPN=$_vd/$_f
}

# adb shell/exec-out read stdin; never let them eat the answers typed (or piped) into this script
su_do() { adb shell "su -c '$1'" </dev/null | tr -d '\r'; }

# Never stream a binary through `adb exec-out "su -c ..."`. On some devices su gives the command a pty, and
# the tty layer's ONLCR rewrites every LF as CRLF: the bytes arrive inflated by about one in 256 and nothing
# reports an error. It is silent and it is ruinous - a boot image built from such a dump is the wrong size,
# and Wi-Fi firmware mangled this way loads without complaint and simply never brings up a phy.
#
# Writing the file on the device and pulling it keeps the bytes off any pty. It costs one round trip and is
# correct whether or not that device's su allocates one. Reported with proof in issue #2, on a device whose
# su does; the maintainer's does not, which is why this survived so long.
dev_pull() {  # dev_pull DEVICE_PATH LOCAL_PATH   (DEVICE_PATH may be a block device)
    su_do "cat $1 > /data/local/tmp/mu300-pull.bin" >/dev/null
    # (|| true: under set -e a failed pull ended the installer here, without the message below)
    adb pull /data/local/tmp/mu300-pull.bin "$2" >/dev/null 2>&1 || true
    su_do 'rm -f /data/local/tmp/mu300-pull.bin' >/dev/null
    [ -s "$2" ] || die "$(t 'could not read {1} from the device' "$1")"
}

sha256_of() { (shasum -a 256 "$1" 2>/dev/null || sha256sum "$1" 2>/dev/null) | cut -d' ' -f1; }
# What an installation reads from the device before it writes anything, into $WORK/dumps: Android's own boot image
# (the base of the Linux one: its header, its AVB footer) and the first block of misc (the boot control block).
pull_boot_and_misc() {
    dev_pull /dev/block/by-name/boot_$ANDROID_SLOT "$WORK/dumps/boot_$ANDROID_SLOT.img"
    su_do 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null > /data/local/tmp/mu300-pull.bin' >/dev/null
    adb pull /data/local/tmp/mu300-pull.bin "$WORK/dumps/misc-head.bin" >/dev/null 2>&1 || true
    su_do 'rm -f /data/local/tmp/mu300-pull.bin' >/dev/null
    [ -s "$WORK/dumps/misc-head.bin" ] || die "$(t 'could not read the misc header from the device')"
}
# --backup: those two files and nothing else, the boot image compared with the device's. The partitions that hold
# the unit's own identity and calibration (prodnv, the NV partitions) are not read here or anywhere in this script.
backup_only() {
    mkdir -p "$WORK/dumps"
    step backup 30
    say "$(t 'Copying the boot image of Android (boot_{1}) and the first block of misc to this computer' "$ANDROID_SLOT")"
    pull_boot_and_misc
    _bw=$(su_do "sha256sum /dev/block/by-name/boot_$ANDROID_SLOT" | cut -d' ' -f1)
    _bh=$(sha256_of "$WORK/dumps/boot_$ANDROID_SLOT.img")
    [ -n "$_bw" ] && [ "$_bw" = "$_bh" ] || die "$(t 'the copy of boot_{1} is not what the device holds; do not rely on it' "$ANDROID_SLOT")"
    echo "  $WORK/dumps/boot_$ANDROID_SLOT.img  sha256 $_bh"
    echo "  $WORK/dumps/misc-head.bin"
    echo "  $(t 'Not copied, here or during an installation: the NV and calibration partitions (IMEI, radio calibration). The installer never reads or writes them.')"
    echo "  $(t 'Nothing was written to the device.')"
    info backup.boot "$WORK/dumps/boot_$ANDROID_SLOT.img"; info backup.misc "$WORK/dumps/misc-head.bin"; info backup.sha256 "$_bh"
    progress done result ok
    exit 0
}

# ---------------------------------------------------------------- preflight
step preflight 2
say "$(t 'Checking host tools and device')"
need="adb"
[ $CHECK_ONLY = 1 ] || [ $BACKUP_ONLY = 1 ] || { [ $MODE = build ] && need="adb docker python3 lz4" || need="adb python3 lz4 curl"; }
# (a package downloads nothing: curl only for the VPN module, when it is wanted)
[ -z "$PKG" ] || [ $CHECK_ONLY = 1 ] || [ $BACKUP_ONLY = 1 ] || need="adb python3 lz4"
find_adb || true
for c in $need; do command -v $c >/dev/null || die "$(t '{1} not found' "$c")"; done
if [ $CHECK_ONLY = 0 ] && [ $MODE = build ]; then
    [ -f "$KOUT/Image" ] && ls "$KOUT"/modules/*.ko >/dev/null 2>&1 || die "$(t 'kernel outputs missing in {1} (run kernel/build-all.sh)' "$KOUT")"
fi
. "$TOP/tools/linux-mode.sh"
require_android
adb_prop() { adb shell getprop "$1" </dev/null 2>/dev/null | tr -d '\r' | head -n1; }
# The bootloader's lock is a warning and never a refusal (the owner's decision of 2026-10-13, FINDINGS 71): Linux
# needs a device that boots a modified boot image, and that is an unlocked bootloader or a locked one with a patched
# TrustOS - which no property tells. Without either the device goes back to Android by itself after the install.
# BL_STATE: locked (ro.boot.flash.locked=1 or ro.boot.vbmeta.device_state=locked), unlocked, or unknown.
BL_WARN="$(t 'The bootloader is locked. Did you patch TrustOS? With a patched TrustOS Linux boots on a locked bootloader; without it the device goes back to Android by itself after the install.')"
BL_LOCKED=$(adb_prop ro.boot.flash.locked); BL_DEVICE_STATE=$(adb_prop ro.boot.vbmeta.device_state)
BL_STATE=unknown
case "$BL_LOCKED:$BL_DEVICE_STATE" in 1:*|*:locked) BL_STATE=locked ;; 0:*|*:unlocked) BL_STATE=unlocked ;; esac
# For a program that reads the progress stream: what the device says about itself before anything needs root (the
# model, Android's build, the bootloader's lock and verified-boot state), then whether su works and whose it is.
# Read only, and only for the stream.
if [ -n "$PROGRESS_FD" ]; then
    info device.model "$(adb_prop ro.product.model) / $(adb_prop ro.product.device)"
    info android.build "$(adb_prop ro.build.display.id)"
    info bootloader.locked "$BL_LOCKED"
    info bootloader.device_state "$BL_DEVICE_STATE"
    info bootloader.state "$BL_STATE"
    info bootloader.verified "$(adb_prop ro.boot.verifiedbootstate)"
    if [ "$(su_do 'id -u')" = 0 ]; then
        info root yes; info root.su "$(adb shell su -v </dev/null 2>/dev/null | tr -d '\r' | head -n1)"
    else
        info root no
    fi
fi
[ "$(su_do 'id -u')" = 0 ] || die "$(t 'su does not work on the device')"
model="$(su_do 'getprop ro.product.model') / $(su_do 'getprop ro.product.device')"
echo "$(t 'device: {1}' "$model")"
[ $BL_STATE != locked ] || echo "$(t 'WARNING: {1}' "$BL_WARN")"
# The U30 Air is the F50's board with a battery: the same kernel and images, a few modules of its own (init
# loads them; the boot image says which device it is for)
# The ZTE V50 (model V50, device MU3351, platform ums9620) is experimental (FINDINGS 95): only exactly that, never
# taken for an F50 (a V50 that is not all three is refused, not asked about), and only with a mainline kernel.
# --- model begin
case "$model" in
    "V50 / MU3351"|*" / MU3351"|"V50 / "*)
        [ "$model" = "V50 / MU3351" ] && [ "$(su_do 'getprop ro.board.platform')" = ums9620 ] ||
            die "$(t 'This looks like a ZTE V50 but is not one this installer knows (model V50, device MU3351, platform ums9620): {1}' "$model")"
        DEVICE=v50
        echo "$(t 'ZTE V50: EXPERIMENTAL, tested by nobody in this project (kano ported it); mainline kernel only; no second SIM, no NFC; USB network 192.168.79.1')" ;;
    *U30Air*|*U30_Air*|*"U30 Air"*) DEVICE=u30air ;;
    *MU300*|*F50*|*mu300*) DEVICE=f50 ;;
    *) info device.kind unknown
       ask go "$(t 'This does not look like a ZTE F50/MU300 or U30 Air. Continue anyway? (yes/no)')" no MU300_ANS_UNKNOWN_MODEL; [ "$go" = yes ] || exit 1
       DEVICE=f50; MODEL_KNOWN=0 ;;
esac
[ "$DEVICE" != v50 ] || [ "$MODE" != build ] || die "$(t 'The ZTE V50 runs a mainline kernel only: install a prebuilt release (without --build)')"
# --- model end
[ "${MODEL_KNOWN:-1}" = 0 ] || info device.kind $DEVICE
# --- slot begin
# Linux goes on the slot Android is not on: b next to an Android on a, a next to an Android on b (after an OTA the
# device often runs from b, issue #86) - the rule of the Magisk installer's slot_setup. Android's own boot partition
# is never written: it is the base the Linux image is built from, and the slot the bootloader falls back to.
slot_setup() {
    _ss=$(su_do 'getprop ro.boot.slot_suffix')
    case $_ss in
        _a) ANDROID_SLOT=a; LINUX_SLOT=b ;;
        _b) ANDROID_SLOT=b; LINUX_SLOT=a ;;
        *) die "$(t 'cannot tell which slot Android runs from')" ;;
    esac
    [ "$(su_do "readlink -f /dev/block/by-name/boot_a")" != "$(su_do "readlink -f /dev/block/by-name/boot_b")" ] ||
        die "$(t 'boot_a and boot_b are the same partition')"
    echo "$(t 'Android runs from slot {1}; Linux goes to slot {2} (boot_{2})' "$ANDROID_SLOT" "$LINUX_SLOT")"
}
# Linux on a needs systems that know it (release v2026.10.10 on: mu300-next-boot, mu300-update and the recorder
# follow the slot the initramfs names); the init is this checkout's either way, build-boot-image.py puts it behind a
# mainline kernel's generic segment. A tag that is not a release tag (MU300_RELEASE_URL's "custom") is not judged;
# newer and tag_ok: tools/releases.sh (vYYYY.MM.DD.N is newer than every vYYYY.MM.DD, docs/FINDINGS.md 125).
slot_release_ok() {  # slot_release_ok RELEASE
    [ "$LINUX_SLOT" = a ] || return 0
    tag_ok "$1" || return 0
    ! newer v2026.10.10 "$1" ||
        die "$(t 'Android runs from slot b, and release {1} cannot boot Linux from slot a; use {2} or newer' "$1" v2026.10.10)"
}
# write_linux_boot IMAGE: IMAGE to boot_$LINUX_SLOT, verified, then the block beside it (IMAGE without .img +
# .misc-slot-$LINUX_SLOT-trial.bin) to misc, which arms Linux for one trial boot. Before anything is written it
# checks again that Android still runs from the slot it ran from when the image was built, and that the image was
# built for the other one.
write_linux_boot() {
    _wb=${1%.img}
    case $ANDROID_SLOT$LINUX_SLOT in ab|ba) ;; *) die "$(t 'cannot tell which slot Android runs from')" ;; esac
    [ "$(su_do 'getprop ro.boot.slot_suffix')" = "_$ANDROID_SLOT" ] ||
        die "$(t 'Android no longer runs from slot {1}; boot_{2} and misc were not changed' "$ANDROID_SLOT" "$LINUX_SLOT")"
    [ "$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('linux_slot'))" "$_wb.json")" = "$LINUX_SLOT" ] &&
        [ -s "$_wb.misc-slot-$LINUX_SLOT-trial.bin" ] ||
        die "$(t 'the boot image was not built for slot {1}; boot_{1} and misc were not changed' "$LINUX_SLOT")"
    say "$(t 'Writing boot_{1} and arming slot {1}' "$LINUX_SLOT")"
    EXP=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['sha256'])" "$_wb.json")
    adb push "$1" $T/mu300-boot.img >/dev/null
    adb push "$_wb.misc-slot-$LINUX_SLOT-trial.bin" $T/mu300-bc.bin >/dev/null
    [ "$(su_do "sha256sum $T/mu300-boot.img" | cut -d' ' -f1)" = "$EXP" ] || die "$(t 'pushed boot image hash mismatch')"
    su_do "dd if=$T/mu300-boot.img of=/dev/block/by-name/boot_$LINUX_SLOT bs=4M && sync"
    # read back from the medium (the page cache dropped first): exactly the image's length in whole 4096-byte blocks,
    # into a file, by one dd whose status counts - a read error leaves no hash to compare instead of a wrong one that
    # looks like anything (FINDINGS 50). A mismatch stops here: misc is not written, Android keeps booting.
    _len=$(wc -c < "$1" | tr -d ' ')
    [ "$(su_do "echo 3 > /proc/sys/vm/drop_caches; dd if=/dev/block/by-name/boot_$LINUX_SLOT of=$T/mu300-boot.rb bs=4096 count=$(( (_len + 4095) / 4096 )) 2>/dev/null && head -c $_len $T/mu300-boot.rb | sha256sum" | cut -d' ' -f1)" = "$EXP" ] ||
        die "$(t 'boot_{1} verify failed (slot {2} still active, Android keeps booting)' "$LINUX_SLOT" "$ANDROID_SLOT")"
    su_do "dd if=$T/mu300-bc.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync && rm $T/mu300-boot.img $T/mu300-bc.bin $T/mu300-boot.rb"
}
# --- slot end
slot_setup
info slot.android $ANDROID_SLOT; info slot.linux $LINUX_SLOT; info work "$WORK"
[ $BACKUP_ONLY = 0 ] || backup_only
# --- bootchain begin
# The Linux slot gets the boot chain of Android's slot - uboot, sml, teecfg, trustos and vbmeta, one firmware build on
# both slots (issue #86; tools/android-bootchain.sh decides and checks, android-install.sh copies it before it installs
# anything, with a backup). MU300_SYNC_BOOTCHAIN=0 skips that, for experts.
bootchain_plan() {
    SYNC_BOOTCHAIN=1; BC_LINE=; BC_REFUSED=
    if [ "${MU300_SYNC_BOOTCHAIN:-1}" = 0 ]; then
        SYNC_BOOTCHAIN=0; BC_LINE=$(t 'boot chain:     not synced (MU300_SYNC_BOOTCHAIN=0)'); return 0
    fi
    # android-bootchain.sh writes only on a model it knows: a device taken as an F50 anyway keeps its chain as it is
    if [ "${MODEL_KNOWN:-1}" = 0 ]; then
        SYNC_BOOTCHAIN=0; BC_LINE=$(t 'boot chain:     not synced (the model is not recognised)'); return 0
    fi
    adb push "$TOP/tools/android-bootchain.sh" $T/android-bootchain.sh >/dev/null 2>&1 || BC_REFUSED=$(t 'no answer from the device')
    bootchain_line "$(su_do "sh $T/android-bootchain.sh check")" && return 0
    if [ $CHECK_ONLY = 1 ]; then BC_LINE="$(t 'boot chain:     cannot be synced: {1}' "$BC_REFUSED")"; return 0; fi
    die "$(t 'the boot chain cannot be copied to the Linux slot: {1}' "$BC_REFUSED")
$(t 'Nothing was changed. Experts can skip this step with MU300_SYNC_BOOTCHAIN=0.')"
}
bootchain_pull() {  # the backup android-install.sh made, into the work directory
    [ "$SYNC_BOOTCHAIN" = 1 ] || return 0
    [ "$(su_do "[ -d $T/mu300-bootchain-backup ] && echo y")" = y ] || return 0
    _d=$WORK/bootchain/$(date +%Y%m%d-%H%M%S)
    mkdir -p "$WORK/bootchain"
    adb pull $T/mu300-bootchain-backup "$_d" >/dev/null 2>&1 || true
    su_do "rm -rf $T/mu300-bootchain-backup" >/dev/null
    if [ -s "$_d/SHA256SUMS" ] && (cd "$_d" && { shasum -a 256 -c SHA256SUMS >/dev/null 2>&1 || sha256sum -c SHA256SUMS >/dev/null 2>&1; }); then
        echo "  $(t 'boot chain backup: {1}' "$_d")"
    else
        echo "  $(t 'the boot chain backup could not be copied to this computer; it is on the Linux filesystem (.mu300/bootchain)')"
    fi
}
# --- bootchain end
bootchain_plan
echo "$BC_LINE"
info bootchain "$BC_LINE"


# ---------------------------------------------------------------- making room on a small eMMC (EXPERIMENTAL)
# The 32 GB variant ships with userdata filling the disk, so there is no gap behind it and nothing to install
# into. Space can be made by shrinking userdata, and only userdata: it is the **last** partition, so its end
# moves and nothing else does - every other partition keeps its offset, and the AVB descriptors and the boot
# chain stay valid. That is the entire safety argument, and it is why this refuses to touch anything else.
#
# It is still the most dangerous thing this installer can do. It rewrites the partition table and it erases
# everything in Android. Reported and first done by hand in issue #2; treat it as experimental.
offer_repartition() {
    say "$(t 'Not enough free space - this looks like the 32 GB variant')"
    mkdir -p "$WORK/gpt"
    su_do "dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null > /data/local/tmp/gpt.head" >/dev/null
    su_do "dd if=/dev/block/mmcblk0 bs=512 skip=$((disk - 33)) count=33 2>/dev/null > /data/local/tmp/gpt.tail" >/dev/null
    adb pull /data/local/tmp/gpt.head "$WORK/gpt/gpt.head" >/dev/null 2>&1 || true
    adb pull /data/local/tmp/gpt.tail "$WORK/gpt/gpt.tail" >/dev/null 2>&1 || true
    su_do 'rm -f /data/local/tmp/gpt.head /data/local/tmp/gpt.tail' >/dev/null
    [ -s "$WORK/gpt/gpt.head" ] && [ -s "$WORK/gpt/gpt.tail" ] || die "$(t 'could not read the partition table')"

    # resize-last-partition.py validates both GPT copies and every CRC before it says anything
    info=$(python3 "$TOP/tools/resize-last-partition.py" "$WORK/gpt/gpt.head,$WORK/gpt/gpt.tail" \
             --disk-sectors "$disk" --name userdata --show) || die "$info"
    LAST_FIRST=$(echo "$info" | sed -n 's/^LAST_FIRST=//p')
    LAST_END=$(echo "$info" | sed -n 's/^LAST_END=//p')
    USABLE_END=$(echo "$info" | sed -n 's/^USABLE_END=//p')
    [ -n "$LAST_FIRST" ] && [ -n "$USABLE_END" ] || die "$(t 'could not read the userdata entry from the partition table')"

    total=$((USABLE_END - LAST_FIRST + 1))         # sectors that userdata and Linux have to share
    MIN_ANDROID=$((4 * 1024 * 1024 * 1024 / 512))  # below this Android has nowhere to put apps or updates
    MIN_LINUX=$((800 * 1024 * 1024 / 512))         # OpenWrt alone, with room for one update
    max_linux=$((total - MIN_ANDROID))
    info makeroom.first "$LAST_FIRST"; info makeroom.end "$LAST_END"; info makeroom.usable_end "$USABLE_END"
    info makeroom.min_android $MIN_ANDROID; info makeroom.min_linux $MIN_LINUX; info makeroom.max_linux $max_linux
    [ $max_linux -ge $MIN_LINUX ] || die "$(t 'there is not enough room to split: {1} in total,' "$(gib $((total * 512)))")
$(t 'and Android needs at least {1} of it. Nothing is changed.' "$(gib $((MIN_ANDROID * 512)))")"
    # the device's identity and how Android keeps its data (read only); kano's rule: encrypted, plain, or unknown
    resume_facts || die "$(t 'could not read what identifies this device (serial, eMMC CID, userdata)')"
    [ "$UD_START" = "$LAST_FIRST" ] && [ $((UD_START + UD_SIZE - 1)) = "$LAST_END" ] ||
        die "$(t 'the partition table and the running kernel disagree about userdata; reboot Android and try again')"
    info makeroom.crypto "$CRYPTO"; info makeroom.crypto_state "$CRYPTO_STATE"; info makeroom.crypto_type "$CRYPTO_TYPE"

    echo
    echo "  eMMC:                 $(gib $((disk * 512)))"
    echo "  $(t 'userdata now:         {1} (sectors {2}..{3})' "$(gib $(( (LAST_END - LAST_FIRST + 1) * 512 )))" "$LAST_FIRST" "$LAST_END")"
    echo "  $(t 'free behind it:       {1}' "$(gib $(( (USABLE_END - LAST_END) * 512 )))")"
    echo "  $(t 'to share:             {1}' "$(gib $((total * 512)))")"
    # (not a case inside $(...): macOS's bash 3.2 takes its first ")" for the end of the substitution)
    crypto_text=$(t 'unknown'); [ "$CRYPTO" = yes ] && crypto_text=$(t 'encrypted'); [ "$CRYPTO" = no ] && crypto_text=$(t 'not encrypted')
    echo "  $(t "Android's data:       {1} ({2})" "$crypto_text" "${CRYPTO_STATE:-?}${CRYPTO_TYPE:+/$CRYPTO_TYPE}")"
    echo
    echo "  $(t 'Linux needs at least  {1} (OpenWrt) / 1.6 GiB (Ubuntu) / 2.4 GiB (both)' "$(gib $((MIN_LINUX * 512)))")"
    echo "  $(t 'Android keeps at least {1}' "$(gib $((MIN_ANDROID * 512)))")"
    echo
    echo "  $(t 'THIS IS EXPERIMENTAL. It rewrites the partition table and ERASES everything in Android -')"
    echo "  $(t 'apps, photos, accounts, settings. Take a full backup first:  sh tools/backup-device.sh')"
    echo "  $(t 'Only userdata changes size; it is the last partition, so nothing else moves.')"
    echo "  $(t "Android's own recovery erases Android's data: the device restarts into it, resets Android (a factory")"
    echo "  $(t 'reset) and starts Android again, which takes a few minutes and more than one restart. Then set Android up')"
    echo "  $(t 'again, turn USB debugging back on and bring root back (Magisk: install the Magisk app again and let')"
    echo "  $(t 'Shell have root), and run this installer again: it finds what was prepared and continues.')"
    echo "  $(t 'What was prepared, and a backup of the partition table, misc, boot and the boot chain, are kept on this')"
    echo "  $(t 'computer in {1} - the reset erases everything of it on the device.' "$BACKUP_DIR")"
    echo
    if [ $CHECK_ONLY = 1 ]; then
        echo "  $(t '--check writes nothing. Run the installer without it to make room.')"
        progress done result ok
        exit 0
    fi
    # kano's rule: an encryption state that cannot be told is no ground to stand on
    [ "$CRYPTO" != unknown ] || die "$(t "Android's encryption state is unknown ({1}); nothing is changed." "${CRYPTO_STATE:-?}/${CRYPTO_TYPE:-?}")"
    ask go "$(t 'Make room by shrinking userdata? (yes/no)')" no MU300_ANS_MAKE_ROOM
    [ "$go" = yes ] || die "$(t 'nothing was changed.')"

    while :; do
        ask lin "$(t 'How many GiB for Linux? (the rest stays with Android)')" 4 MU300_ANS_LINUX_GIB
        case "$lin" in ''|*[!0-9.]*) echo "  $(t 'give a number, like 4 or 2.5')"; continue ;; esac
        lin_s=$(awk -v g="$lin" 'BEGIN { printf "%d", (g * 1073741824) / 512 }')
        lin_s=$(( (lin_s / 4096) * 4096 ))   # keep the boundary on a 2 MiB alignment
        # userdata ends where the region the installers find behind it (region_probe, resume_region) is lin_s
        # sectors: that region starts one 2 MiB block after userdata's end and stops one block short of the
        # disk's end, so a userdata that ended lin_s before the usable end left a region 4 MiB or more smaller
        # than asked - 0.79 GiB for OpenWrt's 0.78 was 804 MiB, 0.78125 would have been 796 and the resumed run
        # would have refused it after the reset (FINDINGS 112, the first device run)
        new_end=$(( ((disk - 34) / 4096 - 1) * 4096 - lin_s - 4096 - 1 ))
        [ $new_end -le "$USABLE_END" ] || new_end=$((USABLE_END - lin_s))
        resume_region $((new_end + 1))
        if [ $((RG_END - RG_START)) -lt $MIN_LINUX ]; then
            echo "  $(t 'too small: Linux needs at least {1}' "$(gib $((MIN_LINUX * 512)))")"
        elif [ "$lin_s" -gt $max_linux ] || [ $((new_end - LAST_FIRST + 1)) -lt $MIN_ANDROID ]; then
            echo "  $(t 'too large: Android would be left with less than {1}' "$(gib $((MIN_ANDROID * 512)))")"
        else
            break
        fi
    done
    echo
    echo "  $(t 'userdata (Android):   {1}' "$(gib $(( (new_end - LAST_FIRST + 1) * 512 )))")"
    echo "  $(t 'Linux region:         {1}' "$(gib $(( (RG_END - RG_START) * 512 )))")"
    echo
    ask sure "$(t "Type ERASE to rewrite the partition table and wipe Android's data")" "" MU300_ANS_ERASE_ANDROID
    [ "$sure" = ERASE ] || die "$(t 'nothing was changed.')"

    # a backup on this computer, before anything is written (kano's backup: the table, misc, boot and the boot chain
    # of both slots, checked against the device)
    step backup 30
    RESUME_BD=$BACKUP_DIR/$(date +%Y%m%d-%H%M%S)-$(printf '%s' "$CID" | cut -c25-32)
    say "$(t 'Backing up to {1}' "$RESUME_BD")"
    resume_backup "$RESUME_BD"
    info resume.backup "$RESUME_BD"
    resume_prepare "$new_end"

    say "$(t 'Done - the device restarts into the reset now')"
    echo "  $(t "Android's recovery formats Android's data on the next start; then Android starts as new. Set it up,")"
    echo "  $(t 'turn USB debugging on, bring root back (the Magisk app, Shell allowed), and run this installer again.')"
    echo "  $(t 'transaction: {1}' "$RESUME_FILE")"
    ask rb "$(t 'Reboot now? (yes/no)')" yes MU300_ANS_REBOOT
    progress done result ok repartitioned yes resume pending transaction "$RESUME_FILE"
    [ "$rb" = yes ] && adb reboot
    exit 0
}

# ---------------------------------------------------------------- free eMMC region
step storage 6
say "$(t 'Locating free eMMC space after the last partition')"
region_probe || die "$(t 'could not read the partition table from the device (is su granted? try again)')"
info emmc.sectors "$disk"; info emmc.last_end "$last_end"; info region.offset "$OFF"; info region.size "$SIZE"
# a prepared installation waiting for this device (the make-room path below, on an earlier run): its checks first
resume_lookup
if [ $RESUME = 1 ]; then
    if [ $CHECK_ONLY = 1 ]; then resume_validate soft || RESUME=0; else resume_validate; fi
fi
# the partitions, for a picture of the eMMC: "name:first sector:sectors" of each (the stream only; sysfs, read only)
[ -z "$PROGRESS_FD" ] || info partitions "$(su_do 'for p in /sys/block/mmcblk0/mmcblk0p*; do echo $(sed -n s/^PARTNAME=//p $p/uevent):$(cat $p/start):$(cat $p/size); done' | tr '\n' ' ')"
gib() { awk -v b="$1" 'BEGIN { printf "%.1f GiB", b / 1073741824 }'; }
# What each choice needs: the installed systems measure ~320 MiB (OpenWrt) and ~580 MiB (Ubuntu), and an update
# keeps the previous one as <os>.old while the new one is unpacked, so allow for two of each plus working room.
NEED_OPENWRT=$((800 * 1024 * 1024)); NEED_UBUNTU=$((1600 * 1024 * 1024)); NEED_BOTH=$((2400 * 1024 * 1024))
info need.openwrt $NEED_OPENWRT; info need.ubuntu $NEED_UBUNTU; info need.both $NEED_BOTH
echo "$(t 'eMMC: {1} ({2} sectors), partitions end at {3} (sector {4}), free after them: {5}' "$(gib $((disk * 512)))" "$disk" "$(gib $((last_end * 512)))" "$last_end" "$(gib $REG_SIZE)")"
# another installer's layouts (FINDINGS 179): a GPT partition named mu300root is where Linux goes (boot/init boots it),
# an image file in Android's data is one this installer cannot update
info region.partition "$IN_PART"
[ "$IN_PART" = 0 ] || echo "$(t 'Linux partition: the GPT partition named mu300root, made by another installer, {1} at offset {2}; Linux goes there, the partition table is not changed' "$(gib $SIZE)" "$OFF")"
if _note=$(region_image_note); then echo "$_note"; info region.foreign_image 1; fi
# the SD card as the other place for it (boot/init looks there first)
sd_probe
[ "$SD_SMALL" = 1 ] && echo "$(t 'SD card present but smaller than 700 MiB; not used')"
info sd.device "$SD_DEV"; info sd.bytes "$SD_BYTES"; info sd.small "$SD_SMALL"
if [ $CHECK_ONLY = 1 ]; then
    SD_MODE=0; sd_ex=no
    if [ -n "$SD_DEV" ]; then
        sd_ex=$(sd_existing)
        info sd.state "$sd_ex"
        echo "$(t 'SD card: {1}, {2}, existing mu300sd filesystem: {3}' "$SD_DEV" "$(gib "$SD_BYTES")" "$(t "$([ $sd_ex = yes ] && echo yes || echo no)")")"
        [ $sd_ex = foreign ] && echo "  $(t 'the SD card holds another Linux (ext4) filesystem; the installer will not format it')"
    fi
elif [ $RESUME = 1 ]; then
    SD_MODE=0                    # the region that was prepared for it
    info storage internal
else
    choose_storage
    info storage "$([ $SD_MODE = 1 ] && echo sd || echo internal)"
fi
# Smaller eMMC variants leave less room behind userdata, and how much is needed depends on the choice further
# down - OpenWrt alone fits in a few hundred megabytes. So refuse only what cannot hold anything at all, and
# check the real requirement once the systems are known. There is nowhere else to put this region on these
# devices: userdata is metadata-encrypted (dm-default-key), so an image file inside it cannot be read from
# Linux, and the spare-looking blackbox and fulldumpdb partitions are written by the firmware itself.
if [ $SD_MODE = 0 ] && _why=$(region_part_refusal); then die "$_why"; fi
if [ $SD_MODE = 0 ] && [ $SIZE -lt $((700 * 1024 * 1024)) ]; then
    # --check with a card: a real run offers the card (as the default) and needs no repartitioning
    if [ $CHECK_ONLY = 1 ] && [ -n "$SD_DEV" ] && [ "$sd_ex" != foreign ]; then
        echo "$(t 'result: {1}' "$(t 'too little free eMMC space for Linux; the installer will offer the SD card instead (no repartitioning needed)')")"
        echo; echo "$(t 'Nothing was written. Android version: {1}' "$(su_do 'getprop ro.build.display.id')")"
        info verdict.code sd-only; progress done result ok
        exit 0
    fi
    offer_repartition   # exits, either by installing nothing or by rebooting for a second pass
fi
region_find_existing || die "$(t 'no answer from the device')"
# what the partition holds is known now: another ext4 in it is not this installer's to format
if [ $SD_MODE = 0 ] && _why=$(region_part_refusal); then die "$_why"; fi
echo "$(t 'Linux region: offset {1}, {2}, existing mu300root filesystem: {3}' "$OFF" "$(gib $SIZE)" "$(t "$existing")")"
INTERNAL_EXISTS=0; [ $existing = yes ] && INTERNAL_EXISTS=1; INT_SIZE=
info region.existing "$existing"; info region.offset "$OFF"; info region.size "$SIZE"
if [ $SD_MODE = 1 ]; then
    # from here on SIZE and existing describe the card; OFF and INT_SIZE keep the internal region for the marker
    INT_SIZE=$SIZE; SIZE=$SD_BYTES; existing=$(sd_existing); DIRTY=0
    echo "$(t 'Linux filesystem: SD card {1}, {2}, existing mu300sd filesystem: {3}' "$SD_DEV" "$(gib $SIZE)" "$(t "$existing")")"
fi
# unpartitioned space should be unused: sample 16 x 1 MiB across the region and count those with data. Empty is
# 0x00 or 0xFF: an eMMC reads back what its erase leaves (EXT_CSD ERASED_MEM_CONT), and on some F50s that is 0xFF -
# counted as data, a region that was never written stopped the install with "not empty".
DIRTY=0
if [ $SD_MODE = 0 ] && [ $existing = no ] && [ $RESUME = 0 ]; then
    region_dirty || die "$(t 'no answer from the device')"
    echo "$(t 'data check: {1} of 16 samples contain data' "$DIRTY")"
    info region.dirty "$DIRTY"
    # what is there, for a report: the first bytes of the region
    [ "$DIRTY" -gt 0 ] && echo "  $(t 'start of the region: {1}' "$(su_do "dd if=/dev/block/mmcblk0 bs=1048576 skip=$(( OFF / 1048576 )) count=1 2>/dev/null | od -An -tx1 -N32" | tr -s ' \n' ' ')")"
fi
if [ $RESUME = 1 ]; then
    # (what the region holds is what was userdata, erased with the word ERASE when it was prepared, and the token)
    vcode=resume; verdict=$(t 'OK: the region prepared by making room, checked against its transaction')
elif [ $existing = yes ]; then
    vcode=existing; verdict=$(t 'OK: a MU300 Linux installation is already present (it can be kept or replaced)')
elif [ $SD_MODE = 1 ]; then
    vcode=sd; verdict=$(t 'OK: the SD card will be formatted; everything on it is erased')
elif [ "$DIRTY" -gt 0 ] && [ "$IN_PART" = 1 ]; then
    vcode=dirty; verdict=$(t 'WARNING: the partition named mu300root is not empty, and it holds no Linux filesystem of this project. Installing overwrites it')
elif [ "$DIRTY" -gt 0 ]; then
    vcode=dirty; verdict=$(t 'WARNING: the unpartitioned space is not empty; it may be used by this firmware. Installing overwrites it')
elif [ $SIZE -ge $((20 * 1024 * 1024 * 1024)) ]; then
    vcode=tested; verdict=$(t 'OK: free and empty, same layout as the tested device (~32 GiB after userdata on the 64 GB eMMC)')
elif [ $SIZE -ge $NEED_BOTH ]; then
    vcode=both; verdict=$(t 'OK: free and empty, smaller than on the tested device but enough for both systems')
elif [ $SIZE -ge $NEED_UBUNTU ]; then
    vcode=one; verdict=$(t 'OK: free and empty, but room for one system only (Ubuntu or OpenWrt, not both)')
else
    vcode=openwrt; verdict=$(t 'OK: free and empty, but small: OpenWrt fits, Ubuntu does not')
fi
echo "$(t 'result: {1}' "$verdict")"
info verdict "$verdict"; info verdict.code $vcode
if [ $CHECK_ONLY = 1 ]; then
    echo; echo "$(t 'Nothing was written. Android version: {1}' "$(su_do 'getprop ro.build.display.id')")"
    progress done result ok
    exit 0
fi
if [ "$DIRTY" -gt 0 ]; then
    ask ow "$(t 'Type overwrite to use this region anyway')" no MU300_ANS_OVERWRITE
    [ "$ow" = overwrite ] || die "$(t 'cancelled')"
fi

# ---------------------------------------------------------------- choices
# the release first: it is asked about only when there is a beta, and a GitHub that does not answer stops the run
# here, before the password and the pulls from the device
[ $MODE != prebuilt ] || release_resolve
info release "$RELEASE"
step choices 10
say "$(t 'What should be installed?')"
# --- package-choices begin
# package_choices: a package's system, Ubuntu and kernel are its own - nothing to ask, only whether they fit here
package_choices() {
    echo "  $(t 'This package installs {1} with kernel {2} (release {3}); nothing is downloaded.' "$PKG_SYSTEM" "$PKG_KERNEL" "$PKG_TAG")"
    echo "  $(t 'Another system or kernel: the zip of that combination, or ./install.sh of the project.')"
    OSES=$PKG_OS; BOOT_OS=$PKG_OS; UBUNTU=${PKG_UBUNTU:-24.04}; KERNEL=$PKG_KERNEL
    [ "$DEVICE" != v50 ] || [ "$KERNEL" != 5.4 ] ||
        die "$(t 'The ZTE V50 runs a mainline kernel only: take the zip for kernel 6.18, 7.2 or 7.3 of this release')"
    need=$NEED_OPENWRT; [ "$OSES" = ubuntu ] && need=$NEED_UBUNTU
    _fit=; [ $SIZE -lt $NEED_OPENWRT ] || _fit=$(t "An OpenWrt zip of this release fits here.")
    [ $SIZE -ge $need ] || die "$(t 'that choice needs about {1} MiB and this device has {2} MiB of free space' "$((need / 1048576))" "$((SIZE / 1048576))")${_fit:+
$_fit}"
}
# --- package-choices end
if [ -n "$PKG" ]; then
    package_choices
else
echo "  $(t '1) Ubuntu LTS: 24.04 or 26.04, asked next (full distribution, apt, ~500 MiB RAM in use)')"
echo "  $(t '2) OpenWrt {1} (router, LuCI web UI, ~140 MiB RAM in use)' "$OWRT_VER")"
echo "  $(t '3) both (switch later with: mu300-os ubuntu|openwrt)')"
[ $SIZE -lt $NEED_BOTH ] && echo "  $(t '(this device has {1}: {2})' "$(gib $SIZE)" "$([ $SIZE -ge $NEED_UBUNTU ] && t 'one system fits, not both' || t 'only OpenWrt fits')")"
# --- choose-systems begin
# systems_default: what Enter chooses - both when both fit, else Ubuntu when it fits, else OpenWrt (the window chooses
# so too). It was always 3: on the 804 MiB a make-room run of 0.79 GiB leaves, Enter stopped with "that choice needs
# about 2400 MiB", which reads as "make room again" on a reinstall (FINDINGS 153).
systems_default() {
    if [ $SIZE -ge $NEED_BOTH ]; then echo 3; elif [ $SIZE -ge $NEED_UBUNTU ]; then echo 1; else echo 2; fi
}
# too_small_for_choice NEED: the refusal of a choice that does not fit here, with the choice that does
too_small_for_choice() {
    _fit=$(t 'Choose 2 (OpenWrt) instead: it fits here.')
    [ $SIZE -lt $NEED_UBUNTU ] || _fit=$(t 'Choose 1 (Ubuntu) or 2 (OpenWrt) instead: one system fits here.')
    [ $SIZE -ge $NEED_OPENWRT ] || _fit=
    die "$(t 'that choice needs about {1} MiB and this device has {2} MiB of free space' "$(($1 / 1048576))" "$((SIZE / 1048576))")${_fit:+
$_fit}"
}
# choose_systems: OSES from $choice (1 Ubuntu, 2 OpenWrt, 3 both); OpenWrt is asked which one (MU300_OPENWRT=plain|luci
# answers without asking), "openwrt-luci" then stands in OSES instead of "openwrt"; BOOT_OS is the system that boots.
# A choice that does not fit stops before the question of which OpenWrt.
choose_systems() {
    case $choice in 1) OSES=ubuntu; need=$NEED_UBUNTU ;; 2) OSES=openwrt; need=$NEED_OPENWRT ;; 3) OSES="ubuntu openwrt"; need=$NEED_BOTH ;;
        *) die "$(t 'invalid choice')" ;; esac
    [ $SIZE -ge $need ] || too_small_for_choice $need
    case " $OSES " in *" openwrt "*)
        case ${MU300_OPENWRT:-} in
            plain) _w=1 ;;
            luci) _w=2 ;;
            '')
                say "$(t 'Which OpenWrt?')"
                echo "  $(t '1) OpenWrt {1}: the standard LuCI web interface' "$OWRT_VER")"
                echo "  $(t '2) OpenWrt {1} with the MU300 control panel: dashboard, cellular locks, SMS, AT terminal, USB modes (by kanoqwq)' "$OWRT_VER")"
                ask _w "$(t 'Choice')" 1 MU300_OPENWRT ;;
            *) die "$(t 'MU300_OPENWRT must be plain or luci')" ;;
        esac
        case $_w in 1) ;; 2) OSES=$(echo "$OSES" | sed 's/openwrt/openwrt-luci/') ;; *) die "$(t 'invalid choice')" ;; esac ;;
    esac
    BOOT_OS=${OSES%% *}
    case $OSES in *" "*)
        _names=$(echo "$OSES" | tr ' ' '/')
        ask BOOT_OS "$(t 'Which one should boot ({1})' "$_names")" "$BOOT_OS" MU300_ANS_BOOT_OS
        case " $OSES " in *" $BOOT_OS "*) ;; *) die "$(t 'invalid system')" ;; esac ;;
    esac
}
# --- choose-systems end
ask choice "$(t 'Choice')" "$(systems_default)" MU300_ANS_SYSTEMS
choose_systems
UBUNTU=24.04
case " $OSES " in *" ubuntu "*) if [ $MODE = prebuilt ]; then
    say "$(t 'Which Ubuntu?')"
    echo "  $(t '1) 24.04 LTS  the longest tested, supported until 2029')"
    echo "  $(t '2) 26.04 LTS  the newest (systemd 259, newer packages), supported until 2031; kernel 6.18, 7.2 or 7.3')"
    ask uv "$(t 'Ubuntu')" 1 MU300_ANS_UBUNTU
    case $uv in 1|24.04) UBUNTU=24.04 ;; 2|26.04) UBUNTU=26.04 ;; *) die "$(t 'invalid choice')" ;; esac
fi ;; esac
fi
# the release file of a system: Ubuntu 26.04 has its own, 24.04 keeps the name it always had
rootfs_file() { if [ "$1" = ubuntu ] && [ "$UBUNTU" = 26.04 ]; then echo mu300-ubuntu-26.04-rootfs.tar.gz; else echo "mu300-$1-rootfs.tar.gz"; fi; }
ask dl "$(t 'Boot Linux by default instead of Android (falls back to Android if Linux fails)? (yes/no)')" yes MU300_ANS_DEFAULT_LINUX
DEFAULT_LINUX=0; [ "$dl" = yes ] && DEFAULT_LINUX=1
BOOT_ATTEMPTS=5
if [ $DEFAULT_LINUX = 1 ]; then
    echo
    echo "  $(t 'How many failed Linux boots in a row before the device goes back to Android by itself?')"
    echo
    echo "  $(t 'A boot counts as failed when it never finishes starting up - the power goes, the battery runs out, or')"
    echo "  $(t 'Linux hangs - before about a minute after power-on. One boot that does finish resets the count.')"
    echo
    echo "  $(t '* Higher is more forgiving: a flaky cable or a couple of power cuts while it is starting will not throw you')"
    echo "  $(t '  back into Android.')"
    echo "  $(t '* Lower gets you to Android sooner if Linux is really broken.')"
    echo "  $(t '* It is also your way back to Android with no computer at hand: cut the power while it is starting this')"
    echo "  $(t '  many times in a row.')"
    echo
    echo "  $(t "1 is how this project used to behave (a single interrupted boot returns to Android). 1-6; the device's")"
    echo "  $(t 'boot counter has no room for more. It can be changed later with: mu300-next-boot attempts N')"
    ask BOOT_ATTEMPTS "$(t 'Failed boots before Android (1-6)')" 5 MU300_ANS_BOOT_ATTEMPTS
    case $BOOT_ATTEMPTS in [1-6]) ;; *) die "$(t 'enter a number from 1 to 6')" ;; esac
fi
ask hs "$(t "Copy Android's hotspot name and password to Linux? (yes/no)")" yes MU300_ANS_HOTSPOT  # kept as-is when updating
IMPORT_HOTSPOT=0; [ "$hs" = yes ] && IMPORT_HOTSPOT=1
ask gpu "$(t 'Include the Mali GPU (OpenCL) userspace (~90 MiB)? (yes/no)')" yes MU300_ANS_GPU
# the VPN is not part of the systems: a module of its own repository, which goes onto the Linux partition (as the vpn
# extra) only when wanted
echo "  $(t 'The VPN (mu300-vpn, with Xray and sing-box) is a module of its own: {1}. It can also be added later on the device:' https://github.com/dikeckaan/mu300-linux-vpn)"
echo "    sudo mu300-extra install vpn"
# a system being replaced whose VPN is on keeps it only with the module: then it is the default
vx_default=no
vpn_on=$(vpn_on_systems "$OSES" | tr -d '\r' | tr '\n' ' ')
vpn_ks=$(for v in $vpn_on; do case $v in (*:ks) printf '%s ' "${v%:ks}" ;; esac; done)
vpn_on=$(echo $vpn_on | sed 's/:ks//g')
if [ -n "$vpn_on" ]; then
    echo "  $(t 'The VPN is on in the system being replaced ({1}): without the module it stays off after the installation.' "$vpn_on")"
    vx_default=yes
fi
ask vx "$(t 'Install the VPN module (about 40 MB more to download, 120 MB on the device)? (yes/no)')" $vx_default MU300_ANS_VPN
# its kill switch on too: without the module nothing would enforce it (android-install.sh refuses the same, later)
[ "$vx" = yes ] || [ -z "$vpn_ks" ] || die "$(t 'The VPN is on with its kill switch in {1}: an update without the VPN module would leave it to nothing. Answer yes to install the module, or turn the VPN off there first (ENABLE=0 in /etc/mu300/vpn.conf).' "${vpn_ks% }")"
# adb over USB to Linux is on in every system and lets every computer in, with a root shell and no password (the
# owner's decision of 2026-10-13, FINDINGS 70; on the device: mu300-usb adb auth open|keys, mu300-usb adb off). The
# one thing to choose here is to close it to all but this computer from the first boot on: its adb key, which the
# installer runs with anyway, is then the one authorised, and adb takes authorised keys only.
ADB=0 ADB_KEY=
ask ab "$(t "adb to Linux over USB is on and lets every computer in: a root shell without a password for anyone who plugs in a cable. Let in only this computer's adb key (~/.android/adbkey.pub) instead? (yes/no)")" no MU300_ANS_ADB
if [ "$ab" = yes ]; then
    ADB_KEY=$(adb_pubkey "${ANDROID_USER_HOME:-$HOME/.android}/adbkey.pub") ||
        die "$(t 'no adb key of this computer to authorise ({1})' "$HOME/.android/adbkey.pub")"
    ADB=1
fi
EXTRA_VPN=
# the web panel for Ubuntu (Cockpit and the MU300 page): the panel extra, optional; offline from the release (Cockpit's
# packages in it), so the first boot needs no network for it
px=no
EXTRA_PANEL=
case " $OSES " in *" ubuntu "*)
    echo "  $(t 'The web panel for Ubuntu (Cockpit with an MU300 page: signal, SMS, VPN, Wi-Fi, updates, a terminal; on the LAN only) is optional. It can also be added later on the device:')"
    echo "    sudo mu300-extra install panel"
    # (not part of a package: it is a file of the release, not of the zip)
    if [ -n "$PKG" ]; then echo "  $(t 'It is not part of this package.')"
    else ask px "$(t 'Install the web panel for Ubuntu (about 24 MB more to download, 50 MB on the device)? (yes/no)')" no MU300_ANS_PANEL; fi ;;
esac
[ -n "$PKG" ] || KERNEL=5.4
if [ $MODE = prebuilt ] && [ -z "$PKG" ]; then
    say "$(t 'Which kernel?')"
    echo "  $(t "1) 5.4   Unisoc's vendor kernel (Android 12 base): the longest tested, everything this project supports")"
    echo "  $(t '2) 6.18  mainline Linux, current long-term (LTS) release: newer drivers and security fixes, the same')"
    echo "  $(t '         functions (hotspot, mobile data, SMS, Bluetooth, VPN, GPU); no USB-C video output yet')"
    echo "  $(t '3) 7.2   mainline Linux, stable release: newer drivers, the same functions')"
    echo "  $(t '         as 6.18; tested less than 6.18')"
    echo "  $(t '4) 7.3   the newest mainline Linux (7.3 for now): the newest drivers, the same functions')"
    echo "  $(t '         as 6.18; tested less than 7.2')"
    echo "  $(t 'This can be changed later on the device: sudo mu300-update kernel 5.4|6.18|7.2|7.3')"
    while :; do
        kdefault=1; [ "$DEVICE" != v50 ] || kdefault=2
        ask kchoice "$(t 'Kernel')" $kdefault MU300_ANS_KERNEL
        case $kchoice in
            1|5.4) if [ "$DEVICE" = v50 ]; then
                       echo "  $(t 'The ZTE V50 runs a mainline kernel only (6.18, 7.2 or 7.3).')"
                       continue
                   fi
                   if [ "${UBUNTU:-}" = 26.04 ]; then
                       echo "  $(t 'Ubuntu 26.04 needs a mainline kernel (6.18, 7.2 or 7.3): its programs use system calls that 5.4 does not have (tar, for one, cannot unpack folders there).')"
                       continue
                   fi
                   KERNEL=5.4; break ;;
            2|6.18) KERNEL=6.18; break ;;
            3|7.2) KERNEL=7.2; break ;;
            4|7.3) KERNEL=7.3; break ;;
            *) echo "  $(t 'enter 1, 2, 3 or 4')" ;;
        esac
    done
fi
FORMAT=0; WIPE_LEGACY=0; UPDATE=0
if [ $existing = no ]; then
    FORMAT=1
else
    echo
    echo "  $(t 'A MU300 Linux installation is already on this device.')"
    echo "    $(t 'update  install the new release over the systems and keep everything you added or changed in them')"
    echo "    $(t '        (files, settings, accounts; packages you installed come back at the first boot)')"
    echo "    $(t 'wipe    erase the Linux filesystem and install from scratch')"
    ask mode "$(t 'update or wipe')" update MU300_ANS_EXISTING
    case $mode in
        update) UPDATE=1 ;;
        wipe) FORMAT=1 ;;
        *) die "$(t 'invalid choice')" ;;
    esac
    # a new /ubuntu replaces an Ubuntu installed directly in the filesystem root (first-generation layout)
    case " $OSES " in *" ubuntu "*) [ $FORMAT = 0 ] && WIPE_LEGACY=1 ;; esac
fi
if [ $SD_MODE = 1 ] && [ $FORMAT = 1 ]; then
    ask erase "$(t 'Everything on the SD card ({1}, {2}) will be erased. Type ERASE to continue' "$SD_DEV" "$(gib $SIZE)")" no MU300_ANS_ERASE_SD
    [ "$erase" = ERASE ] || die "$(t 'cancelled')"
fi
# The password. An update keeps the accounts and passwords of the systems it replaces when the user says so and shows
# that they know the current one (checked on the device by android-pwcheck.sh, with the hash's own method: yescrypt
# on Ubuntu, MD5- or SHA-crypt on OpenWrt; an empty one, OpenWrt's root as it ships, is confirmed by an empty entry).
# Three wrong entries stop the run before anything is written: nothing is changed by a guess, and nothing is set that
# the user did not ask for (run again and answer no to set a new one). The systems installed new, and an update whose
# user says no, get the new password asked for below.
KEEP_PW_OSES=; NEW_PW_OSES=$OSES; pw1=
# pw_tools: what computes the hash on the device (tools/storage.sh, pw_tools_push): the project's own tools, never a
# program of the Linux system being checked (it would run with Android's root). From an unpacked zip its mu300/
# (mu300-pwcheck when its release has one, and busybox); for prebuilt images the release's 5.4 kernel bundle, fetched
# and checked against SHA256SUMS now (the installation takes it from there later: it is downloaded once). --build has
# neither: then only an empty password can be confirmed (answer no to set one).
pw_tools() {
    PW_BIN= PW_BB=
    if [ -n "$PKG" ]; then
        [ ! -s "$PKG/mu300/mu300-pwcheck" ] || PW_BIN=$PKG/mu300/mu300-pwcheck
        [ ! -s "$PKG/mu300/busybox" ] || PW_BB=$PKG/mu300/busybox
        return 0
    fi
    [ $MODE = prebuilt ] && [ -n "${RELEASE:-}" ] || return 0
    _rel=$WORK/release/$RELEASE _base=${MU300_RELEASE_URL:-https://github.com/$REPO/releases/download/$RELEASE}
    mkdir -p "$_rel" "$WORK/pwtools" || return 0
    curl -fsSL -o "$_rel/SHA256SUMS" "$_base/SHA256SUMS" 2>/dev/null || return 0
    _want=$(awk '$2 == "mu300-kernel.tar.gz" || $2 == "*mu300-kernel.tar.gz" {print $1}' "$_rel/SHA256SUMS")
    [ -n "$_want" ] || return 0
    _have=$( (shasum -a 256 "$_rel/mu300-kernel.tar.gz" 2>/dev/null || sha256sum "$_rel/mu300-kernel.tar.gz" 2>/dev/null) | cut -d' ' -f1)
    if [ "$_have" != "$_want" ]; then
        fetch "$_base/mu300-kernel.tar.gz" "$_rel/mu300-kernel.tar.gz.part" || { rm -f "$_rel/mu300-kernel.tar.gz.part"; return 0; }
        _have=$( (shasum -a 256 "$_rel/mu300-kernel.tar.gz.part" 2>/dev/null || sha256sum "$_rel/mu300-kernel.tar.gz.part") | cut -d' ' -f1)
        [ "$_have" = "$_want" ] || { rm -f "$_rel/mu300-kernel.tar.gz.part"; return 0; }
        mv "$_rel/mu300-kernel.tar.gz.part" "$_rel/mu300-kernel.tar.gz"
    fi
    tar -xzOf "$_rel/mu300-kernel.tar.gz" ./mu300-pwcheck > "$WORK/pwtools/mu300-pwcheck" 2>/dev/null &&
        [ -s "$WORK/pwtools/mu300-pwcheck" ] && PW_BIN=$WORK/pwtools/mu300-pwcheck
    tar -xzOf "$_rel/mu300-kernel.tar.gz" ./busybox > "$WORK/pwtools/busybox" 2>/dev/null &&
        [ -s "$WORK/pwtools/busybox" ] && PW_BB=$WORK/pwtools/busybox
    return 0
}
if [ $UPDATE = 1 ]; then
    pw_have=
    for l in $(pw_states "$OSES" | tr -d '\r'); do
        case ${l#*:} in SET|EMPTY|LOCKED) pw_have="$pw_have ${l%%:*}" ;; esac
    done
    pw_have=${pw_have# }
    if [ -n "$pw_have" ]; then
        echo
        echo "  $(t 'The installed {1} has a login password of its own.' "$pw_have")"
        echo "    $(t 'yes  keep it: the current password is asked once and checked on the device')"
        echo "    $(t 'no   set a new password')"
        ask keep_pw "$(t 'Keep the current password? (yes/no)')" yes MU300_ANS_KEEP_PASSWORD
        case $keep_pw in
            yes)
                pw_tools
                for os in $pw_have; do
                    case $os in ubuntu) pwu=ubuntu ;; *) pwu=root ;; esac
                    osv=$(echo "$os" | tr 'a-z-' 'A-Z_')
                    tries=0
                    while :; do
                        tries=$((tries + 1))
                        ask_secret cur "$(t 'Current password of "{1}" in the installed {2} (only Enter if it has none)' "$pwu" "$os")" \
                            "MU300_ANS_CURRENT_PASSWORD_$osv" MU300_ANS_CURRENT_PASSWORD
                        res=$(pw_check "$os" "$cur" | tr -d '\r' | tail -n1); cur=
                        progress info key "password.$os" value "$res"
                        case $res in
                            MATCH) echo "  $(t 'confirmed: the password of {1} stays as it is' "$os")"; KEEP_PW_OSES="$KEEP_PW_OSES $os"; break ;;
                            NOMATCH) echo "  $(t 'that is not the current password of "{1}" in {2}' "$pwu" "$os")" ;;
                            LOCKED) die "$(t '"{1}" in the installed {2} has no password login, so there is none to confirm. Nothing was changed: run the installer again and answer no to set a new password.' "$pwu" "$os")" ;;
                            *) die "$(t 'the current password of "{1}" in the installed {2} could not be checked on the device ({3}). Nothing was changed: run the installer again and answer no to set a new password.' "$pwu" "$os" "${res:-no answer}")" ;;
                        esac
                        [ $ANSWERS_MODE = 0 ] && [ $tries -lt 3 ] ||
                            die "$(t 'the current password of "{1}" in the installed {2} was not confirmed. Nothing was changed: run the installer again and answer no to set a new password.' "$pwu" "$os")"
                    done
                done ;;
            no) ;;
            *) die "$(t 'invalid choice')" ;;
        esac
    fi
    KEEP_PW_OSES=${KEEP_PW_OSES# }
    NEW_PW_OSES=$(for os in $OSES; do case " $KEEP_PW_OSES " in (*" $os "*) ;; (*) printf '%s ' "$os" ;; esac; done)
    NEW_PW_OSES=${NEW_PW_OSES% }
fi
if [ -n "$NEW_PW_OSES" ]; then
    ask_password pw1 "$(t 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)')" "$(t 'Repeat')"
    [ ${#pw1} -ge 6 ] || die "$(t 'passwords differ or are shorter than 6 characters')"
fi

# ---------------------------------------------------------------- pull vendor data from the device
mkdir -p "$WORK/dumps" "$WORK/firmware"
step pull 15
say "$(t 'Pulling device data into {1} (stays on this computer)' "$WORK")"
pull_boot_and_misc
# what a complete subset must hold, not only the directory: an interrupted run left a partial one behind that the
# old check then accepted for good (the same guard as install.ps1, issue #6)
[ -e "$WORK/android-subset/vendor/bin/modem_control" ] && [ -e "$WORK/android-subset/linkerconfig/ld.config.txt" ] ||
    sh "$TOP/android-vendor/extract-subset.sh" "$WORK/android-subset"
for f in wcnmodem.bin gnssmodem.bin wifi_board_config.ini wifi_board_config_ab.ini bt_configure_pskey.ini bt_configure_rf.ini; do
    for d in /odm/firmware /vendor/firmware /vendor/etc; do
        if [ "$(su_do "[ -f $d/$f ] && echo y")" = y ]; then dev_pull "$d/$f" "$WORK/firmware/$f"; break; fi
    done
done
# Nothing downstream notices a firmware file that was never found: the system installs, boots, brings up 5G
# and has no hotspot, with nothing in any log pointing at it. Say so here instead.
missing=
for f in wcnmodem.bin gnssmodem.bin wifi_board_config.ini wifi_board_config_ab.ini bt_configure_pskey.ini bt_configure_rf.ini; do
    [ -s "$WORK/firmware/$f" ] || missing="$missing $f"
done
[ -z "$missing" ] || die "$(t 'these firmware files could not be read from the device:{1}' "$missing")
$(t 'Wi-Fi and Bluetooth need them; without them the system installs and boots but has no hotspot.')"
if [ "$gpu" = yes ] && [ ! -d "$WORK/android-gpu-subset" ]; then
    sh "$TOP/android-vendor/extract-gpu-subset.sh" "$WORK/android-gpu-subset"
fi

if [ $MODE = prebuilt ]; then
# ---------------------------------------------------------------- prebuilt images
slot_release_ok "$RELEASE"   # (RELEASE: release_resolve, before the questions)
REL=$WORK/release/$RELEASE
mkdir -p "$REL"
# ASSETS: where the release's files are - downloaded into REL, or a package's payload/ (the kernels are unpacked into
# REL either way)
ASSETS=$REL
VPN_FROM_MODULE=
if [ -n "$PKG" ]; then
step download 20
say "$(t 'Checking the files of this package')"
ASSETS=$PKG/payload
package_verify
# the VPN is not in a zip: the module of its own repository, which is a download
[ "$vx" != yes ] || VPN_FROM_MODULE=1
else
# MU300_RELEASE_URL: another location with the same files (e.g. a local test server)
base=${MU300_RELEASE_URL:-https://github.com/$REPO/releases/download/$RELEASE}
step download 20
say "$(t 'Downloading release {1}' "$RELEASE")"
curl -fsSL -o "$REL/SHA256SUMS" "$base/SHA256SUMS" || die "$(t 'cannot download {1}' "$base/SHA256SUMS")"
files=mu300-kernel.tar.gz
[ "$KERNEL" = 5.4 ] || files="$files mu300-kernel-$KERNEL.tar.gz"
for os in $OSES; do files="$files $(rootfs_file $os)"; done
# the VPN: a release from before the module (its systems carry mu300-vpn) has the engines as its own vpn extra, from the
# same SHA256SUMS; a newer one has neither, and the module comes from its own repository (below)
if [ "$vx" = yes ]; then
    if awk '$2 == "mu300-extra-vpn.tar.gz" || $2 == "*mu300-extra-vpn.tar.gz" {f = 1} END {exit !f}' "$REL/SHA256SUMS"; then
        files="$files mu300-extra-vpn.tar.gz"; EXTRA_VPN=$REL/mu300-extra-vpn.tar.gz
    else
        VPN_FROM_MODULE=1
    fi
fi
if [ "$px" = yes ]; then
    if awk '$2 == "mu300-extra-panel-offline.tar.gz" || $2 == "*mu300-extra-panel-offline.tar.gz" {f = 1} END {exit !f}' "$REL/SHA256SUMS"; then
        files="$files mu300-extra-panel-offline.tar.gz"; EXTRA_PANEL=$REL/mu300-extra-panel-offline.tar.gz
    else
        echo "  $(t 'release {1} has no web panel for Ubuntu; it can be added on the device later: sudo mu300-extra install panel' "$RELEASE")"
    fi
fi
for f in $files; do
    want=$(awk -v f="$f" '$2 == f || $2 == "*" f {print $1}' "$REL/SHA256SUMS")
    [ -n "$want" ] || die "$(t '{1} is not part of release {2}' "$f" "$RELEASE")$([ "$f" = "mu300-kernel-$KERNEL.tar.gz" ] && [ "$KERNEL" != 5.4 ] && echo " $(t '(choose kernel 5.4, or a newer release)')")"
    have=$( (shasum -a 256 "$REL/$f" 2>/dev/null || sha256sum "$REL/$f" 2>/dev/null) | cut -d' ' -f1)
    if [ "$have" != "$want" ]; then
        echo "  $f"
        fetch "$base/$f" "$REL/$f.part" || die "$(t 'download of {1} failed' "$f")"
        have=$( (shasum -a 256 "$REL/$f.part" 2>/dev/null || sha256sum "$REL/$f.part") | cut -d' ' -f1)
        [ "$have" = "$want" ] || { progress verify file "$f" result mismatch; die "$(t 'checksum mismatch for {1}' "$f")"; }
        mv "$REL/$f.part" "$REL/$f"
        progress verify file "$f" result ok
    else
        progress verify file "$f" result cached
    fi
done
fi
[ -z "$VPN_FROM_MODULE" ] || fetch_vpn_module
# the 5.4 bundle: its kernel, and for every kernel the busybox, logdw and modules of the boot image's first segment.
# A package with a mainline kernel has no 5.4 bundle: its boot image is that kernel with its own generic segment,
# which brings init's busybox, logdw and modules (build-boot-image.py checks that it does).
KOUT=; BUSYBOX=; LOGDW=; E2FSCK=
if [ -z "$PKG" ] || [ "$KERNEL" = 5.4 ]; then
    rm -rf "$REL/kernel" && mkdir -p "$REL/kernel" && tar -xzf "$ASSETS/mu300-kernel.tar.gz" -C "$REL/kernel"
    KOUT=$REL/kernel
    # the 5.4 modules of a device of its own (u30air); the V50 has none, it runs as the F50 does (its mainline kernel
    # is checked below)
    case $DEVICE in f50|v50) ;; *) [ -d "$KOUT/modules-$DEVICE" ] || die "$(t 'release {1} does not support this device yet; use a newer one' "$RELEASE")" ;; esac
    BUSYBOX=$KOUT/busybox; LOGDW=$KOUT/logdw
    # init's e2fsck (FINDINGS 182): in the bundles of the releases since it came
    [ ! -f "$KOUT/e2fsck" ] || E2FSCK=$KOUT/e2fsck
fi
# a mainline kernel (6.18, 7.2, 7.3): its bundle, unpacked
KMAIN=
if [ "$KERNEL" != 5.4 ]; then
    KMAIN=$REL/kernel-$KERNEL
    rm -rf "$KMAIN" && mkdir -p "$KMAIN" && tar -xzf "$ASSETS/mu300-kernel-$KERNEL.tar.gz" -C "$KMAIN"
    [ -s "$KMAIN/Image" ] && [ -s "$KMAIN/ramdisk-generic.lz4" ] && [ -s "$KMAIN/kernel.release" ] || die "$(t '{1} is incomplete' "mu300-kernel-$KERNEL.tar.gz")"
    # a bundle names the devices it runs on; older mainline kernels do not bring up the U30 Air's USB (FINDINGS 33c)
    [ $DEVICE = f50 ] || grep -qw $DEVICE "$KMAIN/devices" 2>/dev/null || die "$(t 'release {1} does not support this device yet; use a newer one' "$RELEASE")"
    sd_kernel_ok "$KMAIN" || die "$(t 'the {1} kernel of release {2} cannot read the SD card: choose kernel 5.4, a newer release, or internal storage (MU300_STORAGE=internal)' "$KERNEL" "$RELEASE")"
fi
step vendor 60
say "$(t 'Adding the vendor files from your device to the images')"
# a mainline kernel's modules come from its tarball, not from $KMAIN: names that differ only in case (xt_HL.ko and
# xt_hl.ko) collapse into one file in a tree on macOS's default file system (FINDINGS 212)
for os in $OSES; do
    python3 "$TOP/tools/vendor-overlay.py" --os $os --firmware "$WORK/firmware" --android-subset "$WORK/android-subset" \
      $([ -d "$WORK/android-gpu-subset" ] && [ "$gpu" = yes ] && echo --gpu-subset "$WORK/android-gpu-subset") \
      $([ -n "$KMAIN" ] && echo --kernel-bundle "$ASSETS/mu300-kernel-$KERNEL.tar.gz") \
      --out "$WORK/mu300-vendor-$os.tar.gz"
done
PWHASH=; [ -z "$pw1" ] || PWHASH=$(printf '%s\n' "$pw1" | python3 "$TOP/tools/sha512crypt.py")
else
# ---------------------------------------------------------------- build
say "$(t 'Building helper binaries')"
mkdir -p "$WORK/out" "$WORK/tools/logdw" "$WORK/tools/bt-init" "$WORK/tools/gpu"
rm -rf "$WORK/out/modules" && cp -R "$KOUT/modules" "$WORK/out/modules"
cp "$KOUT/modules.builtin" "$KOUT/modules.builtin.modinfo" "$WORK/out/" 2>/dev/null || true
docker build -q -t mu300-ubuntu:24.04 "$TOP/rootfs" >/dev/null
docker run --rm mu300-ubuntu:24.04 cat /bin/busybox > "$WORK/busybox"; chmod +x "$WORK/busybox"
# init's e2fsck for a root filesystem with recorded errors (FINDINGS 182)
sh "$TOP/tools/e2fsck/build.sh" "$WORK/tools/e2fsck/e2fsck" >/dev/null && E2FSCK=$WORK/tools/e2fsck/e2fsck
# adbd (adb to Linux is on by default; a build here takes a few minutes)
sh "$TOP/tools/adbd/build.sh" "$WORK/tools/adbd/adbd" >/dev/null
docker run --rm -v "$TOP/tools":/src:ro -v "$WORK/tools":/o mu300-kbuild sh -c '
  gcc -O2 -static -o /o/logdw/logdw /src/logdw/logdw.c &&
  gcc -O2 -static -o /o/bt-init/mu300-bt-init /src/bt-init/mu300-bt-init.c &&
  mkdir -p /o/keys && gcc -O2 -static -o /o/keys/mu300-keys /src/keys/mu300-keys.c &&
  mkdir -p /o/carry && gcc -O2 -static -o /o/carry/mu300-carry /src/carry/mu300-carry.c'
# the VPN: the module of its own repository, as for prebuilt images (not part of the systems)
[ "$vx" != yes ] || fetch_vpn_module
# the web panel: built here without Cockpit's packages (its first boot installs them from Ubuntu's archive once online)
if [ "$px" = yes ]; then
    say "$(t 'Building the web panel extra')"
    sh "$TOP/tools/make-extra.sh" panel "$WORK/mu300-extra-panel.tar.gz" dev >/dev/null
    EXTRA_PANEL=$WORK/mu300-extra-panel.tar.gz
fi
if [ -d "$WORK/android-gpu-subset" ]; then
    L=$(mktemp -d "$WORK/cllibs.XXXX")
    cp "$WORK/android-gpu-subset/vendor/lib64/libOpenCL.so" "$WORK/android-subset/apex/com.android.runtime/lib64/bionic/libc.so" \
       "$WORK/android-subset/apex/com.android.runtime/lib64/bionic/libdl.so" "$L/"
    docker run --rm -v "$TOP/tools/gpu":/w -v "$L":/l:ro -v "$WORK/tools/gpu":/o mu300-kbuild sh -c \
      'ln -sf /usr/bin/clang-12 /usr/local/bin/clang; cp -r /w /tmp/gpu && sh /tmp/gpu/build.sh /l && cp /tmp/gpu/cltest /o/'
    rm -rf "$L"
fi

# MU300_REUSE_BUILD=1 keeps rootfs tarballs from an earlier run of this script
reuse() { [ "${MU300_REUSE_BUILD:-0}" = 1 ] && [ -s "$WORK/mu300-$1.tar.gz" ] && echo "reusing $WORK/mu300-$1.tar.gz"; }
case " $OSES " in *" ubuntu "*) reuse ubuntu || {
    say "$(t 'Building the Ubuntu root filesystem')"
    B=$(mktemp -d "$WORK/ubuntu-build.XXXX")
    tar -C "$TOP/rootfs" --exclude ./base.tar --exclude './*.tar.gz' -cf - . | tar -xf - -C "$B"
    cid=$(docker create mu300-ubuntu:24.04 /bin/true); docker export "$cid" > "$B/base.tar"; docker rm "$cid" >/dev/null
    gpuargs=""
    [ -f "$WORK/tools/adbd/adbd" ] && gpuargs="-v $WORK/tools/adbd/adbd:/adbd:ro"
    [ -d "$WORK/android-gpu-subset" ] && gpuargs="$gpuargs -v $WORK/android-gpu-subset:/android-gpu-subset:ro -v $WORK/tools/gpu/cltest:/cltest:ro"
    # shellcheck disable=SC2086
    docker run --rm -v "$B":/w -v "$WORK/out/modules":/kmods:ro -v "$WORK/out":/kout:ro -v "$WORK/firmware":/firmware:ro \
      -v "$WORK/android-subset":/android-subset:ro -v "$WORK/tools/logdw/logdw":/logdw:ro \
      -v "$WORK/tools/bt-init/mu300-bt-init":/bt-init:ro -v "$WORK/tools/keys/mu300-keys":/keys:ro \
      -v "$WORK/tools/carry/mu300-carry":/carry:ro \
      $gpuargs mu300-ubuntu:24.04 bash /w/assemble.sh >/dev/null
    mv "$B/mu300-ubuntu-24.04-rootfs.tar.gz" "$WORK/mu300-ubuntu.tar.gz"; rm -rf "$B"; } ;;
esac
for os in $OSES; do case $os in openwrt|openwrt-*) reuse $os || {
    say "$(t 'Building the OpenWrt root filesystem')"
    MU300_SYSTEM=$os MU300_INPUTS="$WORK" sh "$TOP/openwrt/build-rootfs.sh" mu300-$os-rootfs.tar.gz >/dev/null
    mv "$TOP/openwrt/mu300-$os-rootfs.tar.gz" "$WORK/mu300-$os.tar.gz"; } ;; esac; done
BUSYBOX=$WORK/busybox; LOGDW=$WORK/tools/logdw/logdw
PWHASH=; [ -z "$pw1" ] || PWHASH=$(printf '%s' "$pw1" | docker run --rm -i mu300-ubuntu:24.04 openssl passwd -6 -stdin)
fi

step bootimage 68
say "$(t 'Building the boot image')"
sed "s/^ROOT_OFFSET=[0-9]*/ROOT_OFFSET=$OFF/" "$TOP/boot/init" > "$WORK/init"
# a mainline kernel: its kernel, and its generic ramdisk segment behind this one (its init and modules win) - the
# image that "mu300-update kernel 6.18" (or 7.2, 7.3) writes on the device
python3 "$TOP/boot/build-boot-image.py" --stock-boot "$WORK/dumps/boot_$ANDROID_SLOT.img" --misc-head "$WORK/dumps/misc-head.bin" \
  --kernel "${KMAIN:-$KOUT}/Image" ${KMAIN:+--append-ramdisk "$KMAIN/ramdisk-generic.lz4"} \
  ${KOUT:+--modules "$KOUT/modules"} --init "$WORK/init" ${BUSYBOX:+--busybox "$BUSYBOX"} --device "$DEVICE" \
  $([ -n "$KOUT" ] && [ -d "$KOUT/modules-u30air" ] && echo --device-modules "u30air=$KOUT/modules-u30air") \
  ${LOGDW:+--logdw "$LOGDW"} ${E2FSCK:+--e2fsck "$E2FSCK"} --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" \
  --android-subset "$WORK/android-subset" --linux-slot "$LINUX_SLOT" --out "$WORK/boot-linux-slot$LINUX_SLOT.img" >/dev/null

# ---------------------------------------------------------------- confirm and install
step confirm 72
say "$(t 'Ready to install')"
echo "  $(t 'source:         {1}' "$([ $MODE = prebuilt ] && t 'prebuilt release {1} + vendor files from this device' "$RELEASE" || t 'local build')")"
echo "  $(t 'systems:        {1} (boots: {2})' "$OSES$(case " $OSES " in (*" ubuntu "*) echo " (Ubuntu $UBUNTU)" ;; esac)" "$BOOT_OS")"
echo "  $(t 'kernel:         {1}' "$KERNEL$([ -n "$KMAIN" ] && echo " (mainline, $(cat "$KMAIN/kernel.release"))")")"
echo "  $(t 'extras:         {1}' "$(x=; [ -z "$EXTRA_VPN" ] || x=vpn; [ -z "$EXTRA_PANEL" ] || x="${x:+$x }panel"; [ -n "$x" ] && echo "$x" || t 'none')")"
echo "  $(t 'adb to Linux:   {1}' "$([ "$ADB" = 1 ] && t 'on, only for the adb key of this computer' || t 'on, for every computer: a root shell without a password (on the device: mu300-usb adb auth keys, or mu300-usb adb off)')")"
echo "  $(t 'default boot:   {1}' "$([ $DEFAULT_LINUX = 1 ] && t 'Linux (Android after {1} failed boots in a row)' "$BOOT_ATTEMPTS" || t 'Android, Linux on demand')")"
echo "  $(t 'filesystem:     {1}' "$([ $FORMAT = 0 ] && t 'keep existing' || { [ $SD_MODE = 1 ] && t 'CREATE new ext4 (erases the SD card)' || t 'CREATE new ext4 (erases the Linux region)'; })")"
[ $UPDATE = 1 ] && echo "  $(t 'update:         everything you added to the chosen systems or changed in them is kept (/etc/mu300/update-report)')"
[ -z "$KEEP_PW_OSES" ] || echo "  $(t 'password:       kept as it is in {1} (confirmed){2}' "$KEEP_PW_OSES" "$([ -z "$NEW_PW_OSES" ] || echo "; $(t 'the new one in {1}' "$NEW_PW_OSES")")")"
[ $UPDATE = 0 ] && [ $FORMAT = 0 ] && echo "  $(t 'note:           the chosen systems are installed fresh; their previous files and settings are replaced')"
if [ $SD_MODE = 1 ]; then
    echo "  $(t 'writes:         SD card {1}, boot_{2}, 32 bytes of misc (the eMMC region, boot_{3}, GPT and userdata are not touched)' "$SD_DEV" "$LINUX_SLOT" "$ANDROID_SLOT")"
else
    echo "  $(t 'writes:         Linux region at offset {1}, boot_{2}, 32 bytes of misc (boot_{3}, GPT and userdata are not touched)' "$OFF" "$LINUX_SLOT" "$ANDROID_SLOT")"
fi
echo "  $BC_LINE"
[ $BL_STATE != locked ] || echo "  $(t 'WARNING: {1}' "$BL_WARN")"
ask confirm "$(t 'Type INSTALL to continue')" no MU300_ANS_CONFIRM
[ "$confirm" = INSTALL ] || die "$(t 'cancelled')"

step copy 75
say "$(t 'Copying to the device')"
adb push "$TOP/tools/android-mount-mu300root.sh" "$TOP/tools/android-install.sh" "$TOP/tools/android-bootchain.sh" $T/ >/dev/null
for os in $OSES; do
    if [ $MODE = prebuilt ]; then
        adb push "$ASSETS/$(rootfs_file $os)" $T/mu300-$os.tar.gz >/dev/null
        adb push "$WORK/mu300-vendor-$os.tar.gz" $T/mu300-vendor-$os.tar.gz >/dev/null
    else
        adb push "$WORK/mu300-$os.tar.gz" $T/mu300-$os.tar.gz >/dev/null
    fi
done
# android-install.sh puts every pushed mu300-extra-<name>.tar.gz onto the Linux partition (extra/<name>); the VPN
# module goes as the vpn extra, and each system links it into itself at its first boot (mu300-extra link)
[ -z "$EXTRA_VPN" ] || adb push "$EXTRA_VPN" $T/mu300-extra-vpn.tar.gz >/dev/null
# the web panel goes as the panel extra; Ubuntu's first boot installs Cockpit from it in the background (mu300-extra link)
[ -z "$EXTRA_PANEL" ] || adb push "$EXTRA_PANEL" $T/mu300-extra-panel.tar.gz >/dev/null
env=$(mktemp)
write_install_env > "$env"
# an update to an older release: the device refuses it unless told (tools/android-install.sh, the downgrade block)
[ "${MU300_DOWNGRADE:-}" != yes ] || echo DOWNGRADE=1 >> "$env"
adb push "$env" $T/mu300-install.env >/dev/null; rm -f "$env"
step device 80
say "$(t 'Installing on the device')"
su_do "sh $T/android-install.sh" | tee "$WORK/device-install.log"
# a timed-out SD-card stage (FINDINGS 50) names itself in that log, with the way out (reboot Android first)
grep -q MU300-INSTALL-OK "$WORK/device-install.log" ||
    die "$(t 'installation on the device failed; boot_{1} and misc were not changed' "$LINUX_SLOT")
$(t 'the device log was kept at {1}' "$WORK/device-install.log")"
bootchain_pull

step boot 92
write_linux_boot "$WORK/boot-linux-slot$LINUX_SLOT.img"
# the prepared installation is done: its transaction no longer waits (kano: state installed)
[ $RESUME = 0 ] || resume_py set "$RESUME_FILE" state=installed || echo "  $(t 'could not mark {1} as done' "$RESUME_FILE")"

# on-device switch for later: one command in Android instead of plugging into a computer (needs Magisk)
step switch 96
say "$(t 'Installing the on-device switch (Magisk module)')"
# (the module of this release, as its Magisk zip installs it: not the generic one, FINDINGS 131)
MU300_MODULE_TAG="${RELEASE:-}" MU300_MODULE_KERNEL="$KERNEL" \
MU300_MODULE_SYSTEM="$(for o in $OSES; do if [ "$o" = ubuntu ]; then printf 'ubuntu-%s ' "${UBUNTU:-24.04}"; else printf '%s ' "$o"; fi; done)" \
    sh "$TOP/tools/install-magisk-module.sh" || echo "  $(t '(skipped; the installer keeps working either way)')"

step done 100
say "$(t 'Done. Rebooting into {1}' "$BOOT_OS")"
IP=192.168.77.1; [ $DEVICE = u30air ] && IP=192.168.78.1; [ $DEVICE = v50 ] && IP=192.168.79.1
progress done result ok boot_os "$BOOT_OS" systems "$OSES" ip "$IP" release "${RELEASE:-}" kernel "$KERNEL"
echo "  $(t 'USB network: {1}   SSH: {2}' $IP "$([ "$BOOT_OS" = ubuntu ] && echo ubuntu@$IP || echo root@$IP, LuCI http://$IP)")"
echo "  $(t 'switch systems: mu300-os {1}   back to Android: mu300-next-boot android' "$(echo "$OSES" | tr ' ' '|')")"
echo "  $(t 'back to Linux from Android (with Magisk): su -c mu300-linux')"
echo "  $(t 'News and new releases are on the Telegram channel of the project, you can follow it: {1}' https://t.me/mu300project)"
adb reboot </dev/null
