#!/bin/sh
# MU300 / ZTE F50 Linux installer. Run on a macOS/Linux host with the device booted in rooted Android (adb + su).
#
#   ./install.sh --check         only inspect the device: is the free eMMC region there and empty? (writes nothing)
#
# On the 32 GB variant there is no free region at all (userdata fills the disk). The installer then offers
# to shrink userdata to make one - EXPERIMENTAL, it rewrites the partition table and erases everything in
# Android, so take a full backup with tools/backup-device.sh first.
#   ./install.sh                 install Ubuntu, OpenWrt or both from the prebuilt release images
#   ./install.sh --build         build kernel outputs/root filesystems locally instead (see README "Build and run")
# It first brings this copy of the project up to date with GitHub (MU300_NO_SELF_UPDATE=1 skips that), and asks for
# the language (MU300_LANG=en|tr|zh skips that).
#
# Prebuilt: needs adb, python3, lz4, curl. Downloads the images of the newest release (or $MU300_RELEASE) and checks
# their sha256.
# Build:    needs adb, docker, python3, lz4 and the kernel outputs in $MU300_KERNEL_OUT (default: out/, kernel/build-all.sh).
# The published images contain no proprietary files: Wi-Fi/Bluetooth firmware and the Android modem/GPU userspace are
# pulled from *your* device into $MU300_WORK (default: work/), never leave the host except to your device.
set -eu
TOP=$(cd "$(dirname "$0")" && pwd)
KOUT=${MU300_KERNEL_OUT:-$TOP/out}
WORK=${MU300_WORK:-$TOP/work}
OWRT_VER=25.12.5
# empty: the newest published release, looked up when it is needed. It used to be a fixed tag that nobody
# remembered to move, so new installs kept getting an old release long after fixes were published.
RELEASE=${MU300_RELEASE:-}
REPO=${MU300_REPO:-dikeckaan/mu300-linux}
T=/data/local/tmp
MODE=prebuilt; CHECK_ONLY=0
for a in "$@"; do
    case $a in
        --check) CHECK_ONLY=1 ;;
        --build) MODE=build ;;
        --prebuilt) MODE=prebuilt ;;
        -h|--help) sed -n '2,13s/^# \{0,1\}//p' "$0"; exit 0 ;;
        *) echo "unknown option $a (see --help)" >&2; exit 2 ;;
    esac
done
. "$TOP/tools/i18n.sh"
choose_language
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\n%s %s\n' "$(t 'ERROR:' 2>/dev/null || echo ERROR:)" "$*" >&2; exit 1; }
ask() { # ask VAR "question" default   (the question already translated; yes/no in any language count)
    printf '%s [%s]: ' "$2" "$(t "$3")"; read -r _a; [ -n "$_a" ] || _a=$3; _a=$(normalize_answer "$_a"); eval "$1=\$_a"; }
# first of all, make this the newest installer (it restarts itself when it changed)
. "$TOP/tools/self-update.sh"
self_update "$0" "$@"
# fetch URL OUT: GitHub's release CDN throttles a single connection hard in some regions (measured 0.2 MB/s
# against a 180 Mbit/s line), so pull the file as parallel ranges and fall back to one stream when that fails.
fetch() {
    _u=$1; _o=$2
    _len=$(curl -fsSLI "$_u" | awk 'tolower($1) == "content-length:" {print $2}' | tr -d '\r' | tail -n1)
    _jobs=${MU300_FETCH_JOBS:-8}
    if [ -z "$_len" ] || [ "$_len" -lt 8000000 ] 2>/dev/null || [ "$_jobs" -le 1 ] 2>/dev/null; then
        curl -fL --retry 3 --progress-bar -o "$_o" "$_u"
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
    curl -fL --retry 3 --progress-bar -o "$_o" "$_u"
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

# ---------------------------------------------------------------- preflight
say "$(t 'Checking host tools and device')"
need="adb"
[ $CHECK_ONLY = 1 ] || { [ $MODE = build ] && need="adb docker python3 lz4" || need="adb python3 lz4 curl"; }
# adb from platform-tools unzipped next to the project (or into the usual places) needs no PATH entry
if ! command -v adb >/dev/null; then
    for d in "$TOP" "$TOP/platform-tools" "$PWD/platform-tools" "$HOME/platform-tools" "$HOME/Downloads/platform-tools" \
             "$HOME/Library/Android/sdk/platform-tools" "$HOME/Android/Sdk/platform-tools"; do
        [ -x "$d/adb" ] && { PATH="$d:$PATH"; export PATH; break; }
    done
fi
for c in $need; do command -v $c >/dev/null || die "$(t '{1} not found' "$c")"; done
if [ $CHECK_ONLY = 0 ] && [ $MODE = build ]; then
    [ -f "$KOUT/Image" ] && ls "$KOUT"/modules/*.ko >/dev/null 2>&1 || die "$(t 'kernel outputs missing in {1} (run kernel/build-all.sh)' "$KOUT")"
fi
. "$TOP/tools/linux-mode.sh"
require_android
[ "$(su_do 'id -u')" = 0 ] || die "$(t 'su does not work on the device')"
android_model=$(su_do 'getprop ro.product.model')
android_device=$(su_do 'getprop ro.product.device')
android_platform=$(su_do 'getprop ro.board.platform')
model="$android_model / $android_device"
echo "$(t 'device: {1}' "$model")"
. "$TOP/tools/android-profile.sh"
if DEVICE=$(mu300_android_profile "$android_model" "$android_device" "$android_platform"); then
    :
else
    profile_error=$?
    [ "$profile_error" != 2 ] || die "$(t 'V50 requires product V50 / MU3351 on ums9620; refusing installation.')"
    ask go "$(t 'This does not look like a ZTE F50/MU300, V50 or U30 Air. Continue anyway? (yes/no)')" no
    [ "$go" = yes ] || exit 1
    DEVICE=f50
fi
[ "$DEVICE" != v50 ] || [ "$MODE" != build ] || die "$(t 'V50 requires a compatible mainline kernel (6.18 or 7.2); the legacy 5.4 build is not supported.')"
[ "$(su_do 'getprop ro.boot.slot_suffix')" = _a ] || die "$(t 'Android must be running from slot a')"
# Existing desktop installer targets b; TF Magisk supports either Android slot.
SYNC_BOOTCHAIN=${MU300_SYNC_BOOTCHAIN:-1}
BC_LINE=$(t 'boot chain:     not synced (MU300_SYNC_BOOTCHAIN=0)')
if [ "$SYNC_BOOTCHAIN" = 1 ]; then
    adb push "$TOP/tools/android-bootchain.sh" $T/android-bootchain.sh >/dev/null
    BC_LINE=$(su_do "sh $T/android-bootchain.sh check")
    printf '%s\n' "$BC_LINE" | grep -q '^BOOTCHAIN-PLAN a b' ||
        die "Boot chain check refused; nothing installed: $BC_LINE"
fi
echo "$BC_LINE"


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
    [ $max_linux -ge $MIN_LINUX ] || die "$(t 'there is not enough room to split: {1} in total,' "$(gib $((total * 512)))")
$(t 'and Android needs at least {1} of it. Nothing is changed.' "$(gib $((MIN_ANDROID * 512)))")"

    echo
    echo "  eMMC:                 $(gib $((disk * 512)))"
    echo "  $(t 'userdata now:         {1} (sectors {2}..{3})' "$(gib $((LAST_END - LAST_FIRST + 1)))" "$LAST_FIRST" "$LAST_END")"
    echo "  $(t 'free behind it:       {1}' "$(gib $(( (USABLE_END - LAST_END) * 512 )))")"
    echo "  $(t 'to share:             {1}' "$(gib $((total * 512)))")"
    echo
    echo "  $(t 'Linux needs at least  {1} (OpenWrt) / 1.6 GiB (Ubuntu) / 2.4 GiB (both)' "$(gib $((MIN_LINUX * 512)))")"
    echo "  $(t 'Android keeps at least {1}' "$(gib $((MIN_ANDROID * 512)))")"
    echo
    echo "  $(t 'THIS IS EXPERIMENTAL. It rewrites the partition table and ERASES everything in Android -')"
    echo "  $(t 'apps, photos, accounts, settings. Take a full backup first:  sh tools/backup-device.sh')"
    echo "  $(t 'Only userdata changes size; it is the last partition, so nothing else moves.')"
    echo
    if [ $CHECK_ONLY = 1 ]; then
        echo "  $(t '--check writes nothing. Run the installer without it to make room.')"
        exit 0
    fi
    ask go "$(t 'Make room by shrinking userdata? (yes/no)')" no
    [ "$go" = yes ] || die "$(t 'nothing was changed.')"

    while :; do
        ask lin "$(t 'How many GiB for Linux? (the rest stays with Android)')" 4
        case "$lin" in ''|*[!0-9.]*) echo "  $(t 'give a number, like 4 or 2.5')"; continue ;; esac
        lin_s=$(awk -v g="$lin" 'BEGIN { printf "%d", (g * 1073741824) / 512 }')
        lin_s=$(( (lin_s / 4096) * 4096 ))   # keep the boundary on a 2 MiB alignment
        if [ "$lin_s" -lt $MIN_LINUX ]; then
            echo "  $(t 'too small: Linux needs at least {1}' "$(gib $((MIN_LINUX * 512)))")"
        elif [ "$lin_s" -gt $max_linux ]; then
            echo "  $(t 'too large: Android would be left with less than {1}' "$(gib $((MIN_ANDROID * 512)))")"
        else
            break
        fi
    done
    new_end=$((USABLE_END - lin_s))
    echo
    echo "  $(t 'userdata (Android):   {1}' "$(gib $(( (new_end - LAST_FIRST + 1) * 512 )))")"
    echo "  $(t 'Linux region:         {1}' "$(gib $((lin_s * 512)))")"
    echo
    ask sure "$(t "Type ERASE to rewrite the partition table and wipe Android's data")" ""
    [ "$sure" = ERASE ] || die "$(t 'nothing was changed.')"

    say "$(t 'Rewriting the partition table')"
    python3 "$TOP/tools/resize-last-partition.py" "$WORK/gpt/gpt.head,$WORK/gpt/gpt.tail" \
        --disk-sectors "$disk" --name userdata --end-sector "$new_end" --out "$WORK/gpt/new" || die "$(t 'the resize was refused; nothing is changed')"
    adb push "$WORK/gpt/new.head" /data/local/tmp/new.head >/dev/null 2>&1
    adb push "$WORK/gpt/new.tail" /data/local/tmp/new.tail >/dev/null 2>&1
    # the backup copy first: if power is lost between the two, the primary is still the old table and the
    # device boots, which is the recoverable order
    su_do "dd if=/data/local/tmp/new.tail of=/dev/block/mmcblk0 bs=512 seek=$((disk - 33)) conv=fsync 2>/dev/null" >/dev/null
    su_do "dd if=/data/local/tmp/new.head of=/dev/block/mmcblk0 bs=512 conv=fsync 2>/dev/null" >/dev/null
    su_do 'sync; rm -f /data/local/tmp/new.head /data/local/tmp/new.tail' >/dev/null

    say "$(t 'Verifying')"
    su_do "dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null > /data/local/tmp/gpt.head" >/dev/null
    su_do "dd if=/dev/block/mmcblk0 bs=512 skip=$((disk - 33)) count=33 2>/dev/null > /data/local/tmp/gpt.tail" >/dev/null
    adb pull /data/local/tmp/gpt.head "$WORK/gpt/after.head" >/dev/null 2>&1 || true
    adb pull /data/local/tmp/gpt.tail "$WORK/gpt/after.tail" >/dev/null 2>&1 || true
    su_do 'rm -f /data/local/tmp/gpt.head /data/local/tmp/gpt.tail' >/dev/null
    check=$(python3 "$TOP/tools/resize-last-partition.py" "$WORK/gpt/after.head,$WORK/gpt/after.tail" \
              --disk-sectors "$disk" --name userdata --show) || die "$(t 'the table on the device does not read back as valid GPT.')
$(t "The backup copy at the end of the disk was written first, so Android's own repair may still fix this. Do not power off.")"
    got=$(echo "$check" | sed -n 's/^LAST_END=//p')
    [ "$got" = "$new_end" ] || die "$(t 'the table did not take (userdata still ends at {1}). Nothing else was changed.' "$got")"
    echo "  $(t 'userdata now ends at sector {1}' "$got")"

    # Android cannot mount a filesystem that claims to be larger than its partition; clearing the superblocks
    # is what makes it format /data cleanly on the next boot instead of bootlooping on a repair attempt.
    say "$(t "Clearing Android's data filesystem so it is recreated on the next boot")"
    su_do "dd if=/dev/zero of=/dev/block/by-name/userdata bs=1M count=32 conv=fsync 2>/dev/null" >/dev/null

    say "$(t 'Done - the device has to reboot before the new layout is visible')"
    echo "  $(t 'Android will set up its data partition again on this boot, which takes a few minutes.')"
    echo "  $(t 'When it is back, run this installer again and it will find the free space.')"
    ask rb "$(t 'Reboot now? (yes/no)')" yes
    [ "$rb" = yes ] && adb reboot
    exit 0
}

# ---------------------------------------------------------------- free eMMC region
say "$(t 'Locating free eMMC space after the last partition')"
set -- $(su_do 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; done; echo $e $(cat /sys/block/mmcblk0/size)')
[ $# -eq 2 ] || die "$(t 'could not read the partition table from the device (is su granted? try again)')"
last_end=$1; disk=$2
start=$(( (last_end / 4096 + 1) * 4096 ))
end=$(( ((disk - 34) / 4096 - 1) * 4096 ))
# Upstream bde23d0: a full partition table leaves no region, not a negative one.
[ $end -gt $start ] || end=$start
OFF=$((start * 512)); SIZE=$(( (end - start) * 512 ))
gib() { awk -v b="$1" 'BEGIN { printf "%.1f GiB", b / 1073741824 }'; }
# What each choice needs: the installed systems measure ~320 MiB (OpenWrt) and ~580 MiB (Ubuntu), and an update
# keeps the previous one as <os>.old while the new one is unpacked, so allow for two of each plus working room.
NEED_OPENWRT=$((800 * 1024 * 1024)); NEED_UBUNTU=$((1600 * 1024 * 1024)); NEED_BOTH=$((2400 * 1024 * 1024))
echo "$(t 'eMMC: {1} ({2} sectors), partitions end at {3} (sector {4}), free after them: {5}' "$(gib $((disk * 512)))" "$disk" "$(gib $((last_end * 512)))" "$last_end" "$(gib $SIZE)")"
# Smaller eMMC variants leave less room behind userdata, and how much is needed depends on the choice further
# down - OpenWrt alone fits in a few hundred megabytes. So refuse only what cannot hold anything at all, and
# check the real requirement once the systems are known. There is nowhere else to put this region on these
# devices: userdata is metadata-encrypted (dm-default-key), so an image file inside it cannot be read from
# Linux, and the spare-looking blackbox and fulldumpdb partitions are written by the firmware itself.
if [ $SIZE -lt $((700 * 1024 * 1024)) ]; then
    offer_repartition   # exits, either by installing nothing or by rebooting for a second pass
fi
# an existing installation defines the region (it may have been created with a slightly different size)
existing=no
for cand in $OFF 27762098176; do
    # Never probe a superblock beyond the eMMC: the vendor block driver can hang.
    [ "$cand" -ge 0 ] && [ $((cand + 2048)) -le $((disk * 512)) ] || continue
    m=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1080)) count=2 2>/dev/null | od -An -tx1" | tr -d ' ')
    l=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1144)) count=16 2>/dev/null" | tr -d '\000')
    if [ "$m" = 53ef ] && [ "$l" = mu300root ]; then
        blocks=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1028)) count=4 2>/dev/null | od -An -tu4" | tr -d ' ')
        OFF=$cand; SIZE=$((blocks * 4096)); existing=yes; break
    fi
done
echo "$(t 'Linux region: offset {1}, {2}, existing mu300root filesystem: {3}' "$OFF" "$(gib $SIZE)" "$(t "$existing")")"
# unpartitioned space should be unused: sample 16 x 1 MiB across the region and count those with data. Empty is
# 0x00 or 0xFF: an eMMC reads back what its erase leaves (EXT_CSD ERASED_MEM_CONT), and on some F50s that is 0xFF -
# counted as data, a region that was never written stopped the install with "not empty".
DIRTY=0
if [ $existing = no ]; then
    step=$(( SIZE / 1048576 / 16 ))
    probe=""; i=0
    while [ $i -lt 16 ]; do probe="$probe $(( OFF / 1048576 + i * step ))"; i=$((i + 1)); done
    DIRTY=$(su_do "n=0; for s in $probe; do c=\$(dd if=/dev/block/mmcblk0 bs=1048576 skip=\$s count=1 2>/dev/null | tr -d \"\\000\\377\" | wc -c); [ \$c -gt 0 ] && n=\$((n + 1)); done; echo \$n")
    echo "$(t 'data check: {1} of 16 samples contain data' "$DIRTY")"
    # what is there, for a report: the first bytes of the region
    [ "$DIRTY" -gt 0 ] && echo "  $(t 'start of the region: {1}' "$(su_do "dd if=/dev/block/mmcblk0 bs=1048576 skip=$(( OFF / 1048576 )) count=1 2>/dev/null | od -An -tx1 -N32" | tr -s ' \n' ' ')")"
fi
if [ $existing = yes ]; then
    verdict=$(t 'OK: a MU300 Linux installation is already present (it can be kept or replaced)')
elif [ "$DIRTY" -gt 0 ]; then
    verdict=$(t 'WARNING: the unpartitioned space is not empty; it may be used by this firmware. Installing overwrites it')
elif [ $SIZE -ge $((20 * 1024 * 1024 * 1024)) ]; then
    verdict=$(t 'OK: free and empty, same layout as the tested device (~32 GiB after userdata on the 64 GB eMMC)')
elif [ $SIZE -ge $NEED_BOTH ]; then
    verdict=$(t 'OK: free and empty, smaller than on the tested device but enough for both systems')
elif [ $SIZE -ge $NEED_UBUNTU ]; then
    verdict=$(t 'OK: free and empty, but room for one system only (Ubuntu or OpenWrt, not both)')
else
    verdict=$(t 'OK: free and empty, but small: OpenWrt fits, Ubuntu does not')
fi
echo "$(t 'result: {1}' "$verdict")"
if [ $CHECK_ONLY = 1 ]; then
    echo; echo "$(t 'Nothing was written. Android version: {1}' "$(su_do 'getprop ro.build.display.id')")"
    exit 0
fi
if [ "$DIRTY" -gt 0 ]; then
    ask ow "$(t 'Type overwrite to use this region anyway')" no
    [ "$ow" = overwrite ] || die "$(t 'cancelled')"
fi

# ---------------------------------------------------------------- choices
say "$(t 'What should be installed?')"
echo "  $(t '1) Ubuntu LTS: 24.04 or 26.04, asked next (full distribution, apt, ~500 MiB RAM in use)')"
echo "  $(t '2) OpenWrt {1} (router, LuCI web UI, ~140 MiB RAM in use)' "$OWRT_VER")"
echo "  $(t '3) both (switch later with: mu300-os ubuntu|openwrt)')"
[ $SIZE -lt $NEED_BOTH ] && echo "  $(t '(this device has {1}: {2})' "$(gib $SIZE)" "$([ $SIZE -ge $NEED_UBUNTU ] && t 'one system fits, not both' || t 'only OpenWrt fits')")"
ask choice "$(t 'Choice')" 3
case $choice in 1) OSES=ubuntu ;; 2) OSES=openwrt ;; 3) OSES="ubuntu openwrt" ;; *) die "$(t 'invalid choice')" ;; esac
need=$NEED_OPENWRT; [ "$OSES" = ubuntu ] && need=$NEED_UBUNTU; [ "$OSES" = "ubuntu openwrt" ] && need=$NEED_BOTH
[ $SIZE -ge $need ] || die "$(t 'that choice needs about {1} MiB and this device has {2} MiB of free space' "$((need / 1048576))" "$((SIZE / 1048576))")"
BOOT_OS=${OSES%% *}
[ "$choice" = 3 ] && { ask BOOT_OS "$(t 'Which one should boot (ubuntu/openwrt)')" ubuntu; case $BOOT_OS in ubuntu|openwrt) ;; *) die "$(t 'invalid system')" ;; esac; }
UBUNTU=24.04
case " $OSES " in *" ubuntu "*) if [ $MODE = prebuilt ]; then
    say "$(t 'Which Ubuntu?')"
    echo "  $(t '1) 24.04 LTS  the longest tested, supported until 2029')"
    echo "  $(t '2) 26.04 LTS  BETA: the newest (systemd 259, newer packages), supported until 2031; tested less')"
    ask uv "$(t 'Ubuntu')" 1
    case $uv in 1|24.04) UBUNTU=24.04 ;; 2|26.04) UBUNTU=26.04 ;; *) die "$(t 'invalid choice')" ;; esac
fi ;; esac
# the release file of a system: Ubuntu 26.04 has its own, 24.04 keeps the name it always had
rootfs_file() { if [ "$1" = ubuntu ] && [ "$UBUNTU" = 26.04 ]; then echo mu300-ubuntu-26.04-rootfs.tar.gz; else echo "mu300-$1-rootfs.tar.gz"; fi; }
ask dl "$(t 'Boot Linux by default instead of Android (falls back to Android if Linux fails)? (yes/no)')" yes
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
    ask BOOT_ATTEMPTS "$(t 'Failed boots before Android (1-6)')" 5
    case $BOOT_ATTEMPTS in [1-6]) ;; *) die "$(t 'enter a number from 1 to 6')" ;; esac
fi
ask hs "$(t "Copy Android's hotspot name and password to Linux? (yes/no)")" yes  # kept as-is when updating
IMPORT_HOTSPOT=0; [ "$hs" = yes ] && IMPORT_HOTSPOT=1
ask gpu "$(t 'Include the Mali GPU (OpenCL) userspace (~90 MiB)? (yes/no)')" yes
KERNEL=5.4
if [ $MODE = prebuilt ]; then
    say "$(t 'Which kernel?')"
    echo "  $(t "1) 5.4   Unisoc's vendor kernel (Android 12 base): the longest tested, everything this project supports")"
    echo "  $(t '2) 6.18  mainline Linux, current long-term (LTS) release: newer drivers and security fixes, the same')"
    echo "  $(t '         functions (hotspot, mobile data, SMS, Bluetooth, VPN, GPU); no USB-C video output yet')"
    echo "  $(t '3) 7.2   the newest stable mainline Linux (7.2 for now): the newest drivers, the same functions')"
    echo "  $(t '         as 6.18; tested less than 6.18')"
    echo "  $(t 'This can be changed later on the device: sudo mu300-update kernel 5.4|6.18|7.2')"
    while :; do
        kernel_default=1; [ "$DEVICE" != v50 ] || kernel_default=2
        ask kchoice "$(t 'Kernel')" "$kernel_default"
        case $kchoice in
            1|5.4) if [ "$DEVICE" = v50 ]; then
                       echo "  $(t 'V50 requires a compatible mainline kernel (6.18 or 7.2); the legacy 5.4 build is not supported.')"
                       continue
                   fi
                   if [ "${UBUNTU:-}" = 26.04 ]; then
                       echo "  $(t 'Ubuntu 26.04 needs a mainline kernel (6.18 or 7.2): its programs use system calls that 5.4 does not have (tar, for one, cannot unpack folders there).')"
                       continue
                   fi
                   KERNEL=5.4; break ;;
            2|6.18) KERNEL=6.18; break ;;
            3|7.2) KERNEL=7.2; break ;;
            *) echo "  $(t 'enter 1, 2 or 3')" ;;
        esac
    done
fi
FORMAT=0; WIPE_LEGACY=0; UPDATE=0
if [ $existing = no ]; then
    FORMAT=1
else
    echo
    echo "  $(t 'A MU300 Linux installation is already on this device.')"
    echo "    $(t 'update  reinstall the systems and keep settings and data (/etc/mu300, users and home directories,')"
    echo "    $(t '        SSH host keys, OpenWrt UCI config; the hotspot settings are kept too)')"
    echo "    $(t 'wipe    erase the Linux filesystem and install from scratch')"
    ask mode "$(t 'update or wipe')" update
    case $mode in
        update) UPDATE=1 ;;
        wipe) FORMAT=1 ;;
        *) die "$(t 'invalid choice')" ;;
    esac
    # a new /ubuntu replaces an Ubuntu installed directly in the filesystem root (first-generation layout)
    case " $OSES " in *" ubuntu "*) [ $FORMAT = 0 ] && WIPE_LEGACY=1 ;; esac
fi
printf '%s: ' "$(t 'Password for the "ubuntu" user (Ubuntu) and "root" (OpenWrt)')"
[ -t 0 ] && stty -echo; read -r pw1; printf '\n%s: ' "$(t 'Repeat')"; read -r pw2; [ -t 0 ] && stty echo; echo
[ "$pw1" = "$pw2" ] && [ ${#pw1} -ge 6 ] || die "$(t 'passwords differ or are shorter than 6 characters')"

# ---------------------------------------------------------------- pull vendor data from the device
mkdir -p "$WORK/dumps" "$WORK/firmware"
say "$(t 'Pulling device data into {1} (stays on this computer)' "$WORK")"
dev_pull /dev/block/by-name/boot_a "$WORK/dumps/boot_a.img"
su_do 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null > /data/local/tmp/mu300-pull.bin' >/dev/null
adb pull /data/local/tmp/mu300-pull.bin "$WORK/dumps/misc-head.bin" >/dev/null 2>&1 || true
su_do 'rm -f /data/local/tmp/mu300-pull.bin' >/dev/null
[ -s "$WORK/dumps/misc-head.bin" ] || die "$(t 'could not read the misc header from the device')"
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
if [ -z "$RELEASE" ]; then
    if [ -n "${MU300_RELEASE_URL:-}" ]; then RELEASE=custom
    else
        RELEASE=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
            | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
        [ -n "$RELEASE" ] || die "$(t 'cannot find the newest release of {1} (set MU300_RELEASE=<tag> to pick one)' "$REPO")"
    fi
fi
REL=$WORK/release/$RELEASE
mkdir -p "$REL"
# MU300_RELEASE_URL: another location with the same files (e.g. a local test server)
base=${MU300_RELEASE_URL:-https://github.com/$REPO/releases/download/$RELEASE}
say "$(t 'Downloading release {1}' "$RELEASE")"
curl -fsSL -o "$REL/SHA256SUMS" "$base/SHA256SUMS" || die "$(t 'cannot download {1}' "$base/SHA256SUMS")"
files=mu300-kernel.tar.gz
[ "$KERNEL" = 5.4 ] || files="$files mu300-kernel-$KERNEL.tar.gz"
for os in $OSES; do files="$files $(rootfs_file $os)"; done
for f in $files; do
    want=$(awk -v f="$f" '$2 == f || $2 == "*" f {print $1}' "$REL/SHA256SUMS")
    [ -n "$want" ] || die "$(t '{1} is not part of release {2}' "$f" "$RELEASE")$([ "$f" = "mu300-kernel-$KERNEL.tar.gz" ] && [ "$KERNEL" != 5.4 ] && echo " $(t '(choose kernel 5.4, or a newer release)')")"
    have=$( (shasum -a 256 "$REL/$f" 2>/dev/null || sha256sum "$REL/$f" 2>/dev/null) | cut -d' ' -f1)
    if [ "$have" != "$want" ]; then
        echo "  $f"
        fetch "$base/$f" "$REL/$f.part" || die "$(t 'download of {1} failed' "$f")"
        have=$( (shasum -a 256 "$REL/$f.part" 2>/dev/null || sha256sum "$REL/$f.part") | cut -d' ' -f1)
        [ "$have" = "$want" ] || die "$(t 'checksum mismatch for {1}' "$f")"
        mv "$REL/$f.part" "$REL/$f"
    fi
done
rm -rf "$REL/kernel" && mkdir -p "$REL/kernel" && tar -xzf "$REL/mu300-kernel.tar.gz" -C "$REL/kernel"
KOUT=$REL/kernel
case $DEVICE in
    f50|v50) ;; # V50 shares the base modules; mainline support is checked below.
    *) [ -d "$KOUT/modules-$DEVICE" ] || die "$(t 'release {1} does not support this device yet; use a newer one' "$RELEASE")" ;;
esac
BUSYBOX=$KOUT/busybox; LOGDW=$KOUT/logdw
# a mainline kernel (6.18, 7.2): its bundle, unpacked
KMAIN=
if [ "$KERNEL" != 5.4 ]; then
    KMAIN=$REL/kernel-$KERNEL
    rm -rf "$KMAIN" && mkdir -p "$KMAIN" && tar -xzf "$REL/mu300-kernel-$KERNEL.tar.gz" -C "$KMAIN"
    [ -s "$KMAIN/Image" ] && [ -s "$KMAIN/ramdisk-generic.lz4" ] && [ -s "$KMAIN/kernel.release" ] || die "$(t '{1} is incomplete' "mu300-kernel-$KERNEL.tar.gz")"
    # a bundle names the devices it runs on; older mainline kernels do not bring up the U30 Air's USB (FINDINGS 33c)
    [ $DEVICE = f50 ] || grep -qw $DEVICE "$KMAIN/devices" 2>/dev/null || die "$(t 'release {1} does not support this device yet; use a newer one' "$RELEASE")"
fi
say "$(t 'Adding the vendor files from your device to the images')"
for os in $OSES; do
    python3 "$TOP/tools/vendor-overlay.py" --os $os --firmware "$WORK/firmware" --android-subset "$WORK/android-subset" \
      $([ -d "$WORK/android-gpu-subset" ] && [ "$gpu" = yes ] && echo --gpu-subset "$WORK/android-gpu-subset") \
      $([ -n "$KMAIN" ] && echo --kernel-bundle "$KMAIN") \
      --out "$WORK/mu300-vendor-$os.tar.gz"
done
PWHASH=$(printf '%s\n' "$pw1" | python3 "$TOP/tools/sha512crypt.py")
else
# ---------------------------------------------------------------- build
say "$(t 'Building helper binaries')"
mkdir -p "$WORK/out" "$WORK/tools/logdw" "$WORK/tools/bt-init" "$WORK/tools/gpu"
rm -rf "$WORK/out/modules" && cp -R "$KOUT/modules" "$WORK/out/modules"
cp "$KOUT/modules.builtin" "$KOUT/modules.builtin.modinfo" "$WORK/out/" 2>/dev/null || true
docker build -q -t mu300-ubuntu:24.04 "$TOP/rootfs" >/dev/null
docker run --rm mu300-ubuntu:24.04 cat /bin/busybox > "$WORK/busybox"; chmod +x "$WORK/busybox"
docker run --rm -v "$TOP/tools":/src:ro -v "$WORK/tools":/o mu300-kbuild sh -c '
  gcc -O2 -static -o /o/logdw/logdw /src/logdw/logdw.c &&
  gcc -O2 -static -o /o/bt-init/mu300-bt-init /src/bt-init/mu300-bt-init.c'
sh "$TOP/tools/keys/build.sh" "$WORK/tools/keys"
sh "$TOP/tools/atd-io/build.sh" "$WORK/tools/atd-io"
# the VPN engines, as tools/make-release.sh puts them into the published images (without them mu300-vpn only
# fails with "xray: not found")
[ -s "$WORK/sing-box" ] || sh "$TOP/tools/fetch-sing-box.sh" "$WORK/sing-box"
[ -s "$WORK/xray" ] && [ -s "$WORK/hev-socks5-tunnel" ] || sh "$TOP/tools/fetch-xray.sh" "$WORK"
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
    [ -d "$WORK/android-gpu-subset" ] && gpuargs="-v $WORK/android-gpu-subset:/android-gpu-subset:ro -v $WORK/tools/gpu/cltest:/cltest:ro"
    # shellcheck disable=SC2086
    SML_CHECK=$(python3 "$TOP/tools/sml-voltage/build.py")
    docker run --rm -v "$SML_CHECK":/sml-voltage-check:ro -v "$B":/w -v "$WORK/out/modules":/kmods:ro -v "$WORK/out":/kout:ro -v "$WORK/firmware":/firmware:ro \
      -v "$WORK/android-subset":/android-subset:ro -v "$WORK/tools/logdw/logdw":/logdw:ro \
      -v "$WORK/tools/bt-init/mu300-bt-init":/bt-init:ro -v "$WORK/tools/keys/mu300-keys":/keys:ro -v "$WORK/tools/atd-io/mu300-atd-io":/atd-io:ro -v "$WORK/sing-box":/sing-box:ro -v "$WORK/xray":/xray:ro \
      -v "$WORK/hev-socks5-tunnel":/hev-socks5-tunnel:ro $gpuargs mu300-ubuntu:24.04 bash /w/assemble.sh >/dev/null
    mv "$B/mu300-ubuntu-24.04-rootfs.tar.gz" "$WORK/mu300-ubuntu.tar.gz"; rm -rf "$B"; } ;;
esac
case " $OSES " in *" openwrt "*) reuse openwrt || {
    say "$(t 'Building the OpenWrt root filesystem')"
    MU300_INPUTS="$WORK" MU300_LUCI_PLUGIN_SRC="$TOP/openwrt/luci-app-mu300" \
      sh "$TOP/openwrt/build-rootfs.sh" mu300-openwrt-rootfs.tar.gz >/dev/null
    mv "$TOP/openwrt/mu300-openwrt-rootfs.tar.gz" "$WORK/mu300-openwrt.tar.gz"; } ;;
esac
BUSYBOX=$WORK/busybox; LOGDW=$WORK/tools/logdw/logdw
PWHASH=$(printf '%s' "$pw1" | docker run --rm -i mu300-ubuntu:24.04 openssl passwd -6 -stdin)
fi

say "$(t 'Building the boot image')"
sed "s/^ROOT_OFFSET=[0-9]*/ROOT_OFFSET=$OFF/" "$TOP/boot/init" > "$WORK/init"
# a mainline kernel: its kernel, and its generic ramdisk segment behind this one (its init and modules win) - the
# image that "mu300-update kernel 6.18" (or 7.2) writes on the device
python3 "$TOP/boot/build-boot-image.py" --stock-boot "$WORK/dumps/boot_a.img" --misc-head "$WORK/dumps/misc-head.bin" \
  --kernel "${KMAIN:-$KOUT}/Image" ${KMAIN:+--append-ramdisk "$KMAIN/ramdisk-generic.lz4"} \
  --modules "$KOUT/modules" --init "$WORK/init" --busybox "$BUSYBOX" --device "$DEVICE" \
  $([ -d "$KOUT/modules-u30air" ] && echo --device-modules "u30air=$KOUT/modules-u30air") \
  --logdw "$LOGDW" --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" \
  --android-subset "$WORK/android-subset" --out "$WORK/boot-linux-slotb.img" >/dev/null

# ---------------------------------------------------------------- confirm and install
say "$(t 'Ready to install')"
echo "  $(t 'source:         {1}' "$([ $MODE = prebuilt ] && t 'prebuilt release {1} + vendor files from this device' "$RELEASE" || t 'local build')")"
echo "  $(t 'systems:        {1} (boots: {2})' "$OSES$(case " $OSES " in (*" ubuntu "*) echo " (Ubuntu $UBUNTU)" ;; esac)" "$BOOT_OS")"
echo "  $(t 'kernel:         {1}' "$KERNEL$([ -n "$KMAIN" ] && echo " (mainline, $(cat "$KMAIN/kernel.release"))")")"
echo "  $(t 'default boot:   {1}' "$([ $DEFAULT_LINUX = 1 ] && t 'Linux (Android after {1} failed boots in a row)' "$BOOT_ATTEMPTS" || t 'Android, Linux on demand')")"
echo "  $(t 'filesystem:     {1}' "$([ $FORMAT = 1 ] && t 'CREATE new ext4 (erases the Linux region)' || t 'keep existing')")"
[ $UPDATE = 1 ] && echo "  $(t 'update:         settings and user data of the chosen systems are kept, everything else is replaced')"
[ $UPDATE = 0 ] && [ $FORMAT = 0 ] && echo "  $(t 'note:           the chosen systems are installed fresh; their previous files and settings are replaced')"
echo "  $(t 'writes:         Linux region at offset {1}, boot_b, 32 bytes of misc (boot_a, GPT and userdata are not touched)' "$OFF")"
echo "  $BC_LINE"
ask confirm "$(t 'Type INSTALL to continue')" no
[ "$confirm" = INSTALL ] || die "$(t 'cancelled')"

say "$(t 'Copying to the device')"
adb push "$TOP/tools/android-mount-mu300root.sh" "$TOP/tools/android-install.sh" "$TOP/tools/android-bootchain.sh" "$TOP/tools/tf-storage.sh" $T/ >/dev/null
for os in $OSES; do
    if [ $MODE = prebuilt ]; then
        adb push "$REL/$(rootfs_file $os)" $T/mu300-$os.tar.gz >/dev/null
        adb push "$WORK/mu300-vendor-$os.tar.gz" $T/mu300-vendor-$os.tar.gz >/dev/null
    else
        adb push "$WORK/mu300-$os.tar.gz" $T/mu300-$os.tar.gz >/dev/null
    fi
done
env=$(mktemp)
printf 'OFF=%s\nSIZE=%s\nOFF_S=%s\nSIZE_S=%s\nFORMAT=%s\nOSES="%s"\nWIPE_LEGACY=%s\nUPDATE=%s\nBOOT_OS=%s\nDEFAULT_LINUX=%s\nBOOT_ATTEMPTS=%s\nIMPORT_HOTSPOT=%s\nKERNEL=%s\nPWHASH='"'"'%s'"'"'\n' \
  "$OFF" "$SIZE" "$((OFF / 512))" "$((SIZE / 512))" "$FORMAT" "$OSES" "$WIPE_LEGACY" "$UPDATE" "$BOOT_OS" "$DEFAULT_LINUX" "$BOOT_ATTEMPTS" "$IMPORT_HOTSPOT" "$KERNEL" "$PWHASH" > "$env"
printf 'SYNC_BOOTCHAIN=%s\n' "$SYNC_BOOTCHAIN" >> "$env"
adb push "$env" $T/mu300-install.env >/dev/null; rm -f "$env"
su_do "sh $T/android-install.sh" | tee "$WORK/device-install.log"
grep -q MU300-INSTALL-OK "$WORK/device-install.log" || die "$(t 'installation on the device failed; boot_b and misc were not changed')"

# Keep an additional Android backup on device even if copying to the host fails.
if [ "$SYNC_BOOTCHAIN" = 1 ] && [ "$(su_do "[ -d $T/mu300-bootchain-backup ] && echo y")" = y ]; then
    mkdir -p "$WORK/bootchain"
    adb pull $T/mu300-bootchain-backup "$WORK/bootchain/$(date +%Y%m%d-%H%M%S)" ||
        echo "Boot chain backup stays on the TF/Linux filesystem and Android staging directory" >&2
fi
say "$(t 'Writing boot_b and arming slot b')"
EXP=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['sha256'])" "$WORK/boot-linux-slotb.json")
adb push "$WORK/boot-linux-slotb.img" $T/mu300-boot.img >/dev/null
adb push "$WORK/boot-linux-slotb.misc-slot-b-trial.bin" $T/mu300-bc-b.bin >/dev/null
[ "$(su_do "sha256sum $T/mu300-boot.img" | cut -d' ' -f1)" = "$EXP" ] || die "$(t 'pushed boot image hash mismatch')"
su_do "dd if=$T/mu300-boot.img of=/dev/block/by-name/boot_b bs=4M && sync"
[ "$(su_do 'sha256sum /dev/block/by-name/boot_b' | cut -d' ' -f1)" = "$EXP" ] || die "$(t 'boot_b verify failed (slot a still active, Android keeps booting)')"
su_do "dd if=$T/mu300-bc-b.bin of=/dev/block/by-name/misc bs=1 seek=2048 conv=notrunc && sync && rm $T/mu300-boot.img $T/mu300-bc-b.bin"

# on-device switch for later: one command in Android instead of plugging into a computer (needs Magisk)
say "$(t 'Installing the on-device switch (Magisk module)')"
sh "$TOP/tools/install-magisk-module.sh" || echo "  $(t '(skipped; the installer keeps working either way)')"

say "$(t 'Done. Rebooting into {1}' "$BOOT_OS")"
IP=192.168.77.1; [ $DEVICE = u30air ] && IP=192.168.78.1
echo "  $(t 'USB network: {1}   SSH: {2}' $IP "$([ "$BOOT_OS" = ubuntu ] && echo ubuntu@$IP || echo root@$IP, LuCI http://$IP)")"
echo "  $(t 'switch systems: mu300-os ubuntu|openwrt   back to Android: mu300-next-boot android')"
echo "  $(t 'back to Linux from Android (with Magisk): su -c mu300-linux')"
adb reboot </dev/null
