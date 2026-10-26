#!/system/bin/sh
# Device side of the web installer (site/web-installer): what the page asks a rooted Android to do over adb. The page
# pushes this file and the scripts it uses to /data/local/tmp/mu300-web and runs, for every step,
#   su -c 'sh /data/local/tmp/mu300-web/android-web.sh COMMAND [ARG...]'
# It installs nothing by itself: an installation is the Magisk zip's own installer (mu300/install.sh of the zip the
# page pushed, run the way Magisk's customize.sh runs it), told its answers with MU300_ANSWERS and asked for
# MU300_PROGRESS lines. The page never parses a sentence: every answer for it is a line that starts with MU300-WEB
# (below) or with the installer's MU300-STEP / MU300-PLAN.
#
#   status [deep]        MU300-WEB-STATUS {json}: the device, its slots, the free region, the SD card, what is on the
#                        Linux slot, a running job. deep also samples the free region and looks into an existing
#                        Linux filesystem (mounted read-only, no journal replay). Writes to no partition.
#   take NAME SHA256     the zip pushed as in/NAME (or downloaded by "run fetch"): moved to where only root can
#                        write, checked there against SHA256, its installer unpacked. MU300-WEB-ZIP {json}: its
#                        manifest, and whether its installer knows this caller (web=1).
#   extra vpn|panel FILE SHA256   an extra pushed as in/FILE, checked the same way, for the next install
#   net [NAME]           MU300-WEB-NET {json}: can the device reach GitHub (the release file NAME) right now, and over
#                        what (mobile data, Wi-Fi). Asks for headers only.
#   run fetch NAME SIZE  the device downloads a release's zip itself, as a job, into in/NAME (then "take NAME SHA256"
#                        as for a pushed one). The address is made here from NAME - this project's release of the
#                        tag in the name - and from nothing the page says. It uses the device's own internet
#                        connection (mobile data). A broken connection is continued where it broke, also by the next
#                        "run fetch" of the same name. The job's exit status says why it failed: 10 no connection,
#                        11 sent on to a host that is not GitHub's, 12 failed, 13 not the release's size.
#   answers              in/answers.conf becomes the answers file of the next run (root's, mode 600)
#   password             one line on stdin: the password of the next run, added to the answers file. It is never an
#                        argument (ps would show it) nor a file the adb user can read.
#   run plan|install NAME [more]   start the zip's installer as a job (plan: MU300_DRY_RUN=1). MU300-WEB-JOB started.
#                        more: the page means to install another system after this one; the job's record says so
#                        ("more":1 in status), for a page that finds the job without the one that started it
#   run uninstall WIPE SDWIPE   start the uninstaller as a job: WIPE secure|quick|keep, SDWIPE erase|keep
#   follow               the job's output from its start, as it comes; ends with MU300-WEB-EXIT <status>
#   cancel               end a job that has not started writing the systems yet
#   boot-linux [dry]     arm Linux for one boot and reboot (the Magisk module's switch.sh; dry: write nothing)
#   backup               copies of what an installation writes, in root's directory: MU300-WEB-FILE lines
#   cat NAME             one of those copies, as base64 on stdout, for the page to keep
#   clean [all]          remove the copies for a backup and what a killed run left (all: the zips and a download too)
#
# Where root writes. /data/local/tmp/mu300-web is the adb user's: the page pushes its files there, and whoever can
# write there could put a link where a file is expected. So root never creates, writes, truncates, chmods or
# deletes anything in it. A pushed file is an input: it is moved (a rename takes the name as it is and follows no
# link) into /data/adb/mu300-web, which only root can enter, and looked at there - a regular file, of a bounded
# size, with the checksum that was asked for - before anything reads it. Downloads, the copies for a backup, the
# answers, logs and the job's state live in root's directory only. What is left in the adb user's directory is
# removed by the page's own, unprivileged, adb session.
#
# How much root writes. /data is Android's own: filling it can stop Android from starting. A download is refused
# before it starts when it would leave less than 512 MiB free (the reserve the Magisk installer insists on, too),
# cannot write past the size the release names (the downloader's own file size limit, and a watch that ends it),
# and ends after an hour whatever the other end does. A zip is unpacked only when what it says its installer
# files hold fits a bound; a job's log stops growing at 8 MiB.
#
# A job runs in a session of its own (setsid) with its output in a file: closing the browser tab or pulling the cable
# ends the page's view of it, not the job. Nothing here is faster or slower for being watched, and "follow" after
# a reconnect shows the same log from its start.
#
# Never read here, whatever a caller passes: the NV, calibration and identity partitions (prodnv, nr_*nv*, persist,
# miscdata, ...). backup takes the Linux slot's boot image and boot chain, misc and the partition table - what an
# installation writes - and nothing else (BACKUP_PARTS, backup_allowed).
#
# It runs under Magisk's busybox with ASH_STANDALONE=1, the shell and tools Magisk gives a zip's customize.sh (the
# installer was written and tested for those: 64-bit arithmetic, busybox's unzip and tar); what must be Android's own
# tool (sm, losetup, mke2fs) is run through asw, as the installer does.
#
# MU300_LIB=1 defines the functions and runs nothing (tests/test_android_web.py; the MU300_* paths point at a fake
# device there, as for the Magisk installer).

# ---- under the right shell
if [ -z "${MU300_WEB_BB:-}" ]; then
    for _b in /data/adb/magisk/busybox /data/adb/ksu/bin/busybox /data/adb/ap/bin/busybox; do
        [ -x "$_b" ] || continue
        MU300_WEB_BB=$_b ASH_STANDALONE=1 exec "$_b" sh "$0" "$@"
    done
    # no busybox of a root solution: say so in the page's terms and stop
    echo 'MU300-WEB-ERROR no-busybox'
    exit 1
fi
set -u
umask 022
BB=$MU300_WEB_BB
S=${MU300_WEB_STAGE:-$(cd "$(dirname "$0")" && pwd)}     # /data/local/tmp/mu300-web: the adb user's
R=${MU300_WEB_ROOT:-/data/adb/mu300-web}                 # root's alone
BY=${MU300_BY_NAME:-/dev/block/by-name}
SYSFS=${MU300_SYSFS:-/sys}
MOUNTS=${MU300_MOUNTS:-/proc/mounts}
MAGISKBIN=${MAGISKBIN:-/data/adb/magisk}
MODULES=${MU300_MODULES:-/data/adb/modules}
WORK_PARENT=${MU300_WORK_PARENT:-/data/adb}
DEVSH=${MU300_DEVICE_SH:-/system/bin/sh}
ANDROID_SH=${MU300_ANDROID_SH:-/system/bin/sh}
APATH=${MU300_ANDROID_PATH:-/system/bin:/system/xbin:/vendor/bin}
DROP_CACHES=${MU300_DROP_CACHES:-/proc/sys/vm/drop_caches}
EMMC=${MU300_EMMC:-/dev/block/mmcblk0}
JOB=$R/job
LINUX_CMDLINE=6c6f676c6576656c3d3500      # "loglevel=5\0" at offset 44: the header of every Linux boot image

out() { echo "MU300-WEB-$*"; }
fail() { out "ERROR $*"; exit 1; }       # fail CODE [detail]: CODE is a word the page has a sentence for
say() { echo "- $*"; }
# what storage.sh and android-boot-image.sh expect of their caller
t() { printf '%s' "$1"; }
die() { echo "! $*"; exit 1; }
gib() { awk -v b="$1" 'BEGIN { printf "%.1f GiB", b / 1073741824 }'; }
fsize() { wc -c < "$1" | tr -d ' '; }
asw() { env -u ASH_STANDALONE PATH="$APATH" "$@"; }
su_do() { asw "$DEVSH" -c "$1"; }
. "$S/storage.sh"
. "$S/android-boot-image.sh"

hex_at() { dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | od -An -tx1 -v | tr -d ' \n'; }
sha_of() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
# a string as a JSON string: control characters dropped, backslash and quote escaped
js() { printf '"%s"' "$(printf '%s' "$1" | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g')"; }
num() { case ${1:-} in ''|*[!0-9]*) echo "${2:-0}" ;; *) echo "$1" ;; esac; }
DATA_RESERVE=${MU300_DATA_RESERVE:-536870912}     # what a download or a copy must leave free on /data
ZIP_MAX=${MU300_ZIP_MAX:-314572800}               # no release zip is larger than 124 MB
EXTRA_MAX=${MU300_EXTRA_MAX:-104857600}           # the VPN module is 41 MB
UNPACK_MAX=${MU300_UNPACK_MAX:-67108864}          # the installer's own files in a zip are 4 MB
JOB_LOG_MAX=${MU300_JOB_LOG_MAX:-8388608}
root_dir() {
    # root's own, in a place only root can write (/data/adb): a real directory of mode 700 that the caller owns.
    # A name that is there as anything else - a link, someone else's directory - is not used.
    [ ! -L "$R" ] || fail no-root-dir
    [ -d "$R" ] || mkdir -p "$R" 2>/dev/null || fail no-root-dir
    chmod 700 "$R" 2>/dev/null
    [ -d "$R" ] && [ ! -L "$R" ] && [ "$(stat -c %u "$R" 2>/dev/null)" = "$(id -u)" ] && [ "$(stat -c %a "$R" 2>/dev/null)" = 700 ] || fail no-root-dir
}
data_free() { df -Pk "$1" 2>/dev/null | awk 'NR == 2 { printf "%.0f", $4 * 1024 }'; }
# bring NAME DEST MAX: the file the page pushed as in/NAME becomes DEST, in root's directory. Returns 1 when there
# is no such file, 2 when it is larger than MAX. It is moved, never opened or copied where the adb user can write:
# a rename takes the name as it is. What arrived is judged afterwards, where nobody else can change it.
bring() {
    [ ! -L "$S" ] && [ ! -L "$S/in" ] || return 1
    _b=$R/incoming
    rm -rf "$_b"
    mv "$S/in/$1" "$_b" 2>/dev/null || return 1
    if [ -L "$_b" ] || [ ! -f "$_b" ]; then rm -rf "$_b"; return 1; fi
    if [ "$(fsize "$_b")" -gt "$3" ]; then rm -f "$_b"; return 2; fi
    chown 0:0 "$_b" 2>/dev/null
    chmod 600 "$_b" && rm -rf "$2" && mv "$_b" "$2"
}
name_ok() {  # a file name of a release: letters, digits, . _ - and no ".."
    case $1 in ''|*[!0-9A-Za-z._-]*|*..*|.*|-*) return 1 ;; esac
}
sha_ok() { [ ${#1} = 64 ] && case $1 in *[!0-9a-f]*) return 1 ;; esac; }

# ---- the device
detect() {
    MODEL=$(getprop ro.product.model); PRODUCT=$(getprop ro.product.device)
    case "$MODEL / $PRODUCT" in
        # the ZTE V50 (experimental, FINDINGS 95): exactly V50 / MU3351 on ums9620; any other V50 is no device known
        "V50 / MU3351"|*" / MU3351"|"V50 / "*)
            DEVICE=
            [ "$MODEL / $PRODUCT" = "V50 / MU3351" ] && [ "$(getprop ro.board.platform)" = ums9620 ] && DEVICE=v50 ;;
        *U30Air*|*U30_Air*|*"U30 Air"*) DEVICE=u30air ;;
        *MU300*|*F50*|*mu300*) DEVICE=f50 ;;
        *) DEVICE= ;;
    esac
    case $(getprop ro.boot.slot_suffix) in
        _a) ANDROID_SLOT=a; LINUX_SLOT=b ;;
        _b) ANDROID_SLOT=b; LINUX_SLOT=a ;;
        *) ANDROID_SLOT=; LINUX_SLOT= ;;
    esac
    MISC=$BY/misc
    BOOT_ANDROID=$BY/boot_$ANDROID_SLOT; BOOT_LINUX=$BY/boot_$LINUX_SLOT
}
slots_ok() {  # an A/B device whose two boot partitions are two partitions
    [ -n "$ANDROID_SLOT" ] && [ -e "$MISC" ] && [ -e "$BOOT_ANDROID" ] && [ -e "$BOOT_LINUX" ] &&
        [ "$(readlink -f "$BOOT_ANDROID")" != "$(readlink -f "$BOOT_LINUX")" ]
}
# what the Linux slot's boot partition holds (the Magisk module's switch.sh decides the same way): linux, a copy of
# Android's image, another boot image (the Android from before an update), or nothing that is a boot image
linux_boot_kind() {
    slots_ok || { echo unknown; return 0; }
    [ "$(hex_at "$BOOT_LINUX" 0 8)" = 414e44524f494421 ] || { echo none; return 0; }
    if [ "$(hex_at "$BOOT_LINUX" 44 11)" = "$LINUX_CMDLINE" ]; then echo linux; return 0; fi
    if [ "$(dd if="$BOOT_ANDROID" bs=4096 count=16 2>/dev/null | md5sum | cut -d' ' -f1)" = \
         "$(dd if="$BOOT_LINUX" bs=4096 count=16 2>/dev/null | md5sum | cut -d' ' -f1)" ]; then echo android-copy
    else echo other; fi
}
root_kind() {
    if [ -x "$MAGISKBIN/magiskboot" ] || command -v magisk >/dev/null 2>&1; then echo magisk
    elif [ -d /data/adb/ksu ]; then echo kernelsu
    elif [ -d /data/adb/ap ]; then echo apatch
    else echo other; fi
}

# ---- jobs
job_state() {  # none | running | done | interrupted
    [ -f "$JOB/kind" ] || { echo none; return 0; }
    [ ! -f "$JOB/rc" ] || { echo done; return 0; }
    _p=$(cat "$JOB/pid" 2>/dev/null)
    # the pid is that job's only in the Android boot it was started in
    if [ -n "$_p" ] && [ "$(cat "$JOB/boot" 2>/dev/null)" = "$(boot_now)" ] && kill -0 "$_p" 2>/dev/null; then echo running
    # The job writes its status and then ends: one that ended between the look for the status above and the look
    # at its process here wrote it meanwhile, and is done. (Without this second look a job that finished well was
    # now and then "interrupted" for a watcher: tests/test_android_web.py EndToEnd, on a loaded CI machine.)
    elif [ -f "$JOB/rc" ]; then echo done
    else echo interrupted; fi
}
boot_now() { cat "${MU300_BOOT_ID:-/proc/sys/kernel/random/boot_id}" 2>/dev/null | tr -d ' \n'; }
job_step() { sed -n 's/^MU300-STEP \([0-9]*\)\/.*/\1/p' "$JOB/log" 2>/dev/null | tail -n1; }
job_json() {
    _js=$(job_state)
    printf '{"state":"%s","kind":%s,"arg":%s,"rc":%s,"step":%s,"more":%s}' "$_js" "$(js "$(cat "$JOB/kind" 2>/dev/null)")" \
        "$(js "$(cat "$JOB/arg" 2>/dev/null)")" "$(num "$(cat "$JOB/rc" 2>/dev/null)" -1)" "$(num "$(job_step)" 0)" \
        "$([ -f "$JOB/more" ] && echo 1 || echo 0)"
}
job_start() {  # job_start KIND ARG...: the job in a session of its own, its output in $JOB/log
    root_dir
    case $(job_state) in running) fail job-running "$(cat "$JOB/kind")" ;; esac
    rm -rf "$JOB"; mkdir -p "$JOB" || fail no-root-dir
    echo "$1" > "$JOB/kind"; echo "${2:-}" > "$JOB/arg"; boot_now > "$JOB/boot"; : > "$JOB/log"
    [ -z "${JOB_MORE:-}" ] || : > "$JOB/more"
    if command -v setsid >/dev/null 2>&1; then _ss=setsid; else _ss=; fi
    MU300_WEB_BB=$BB $_ss "$BB" sh "$0" _job "$@" </dev/null >/dev/null 2>&1 &
    # the job writes its own pid; wait for it so that "follow" and "status" see a running job at once
    _n=0
    while [ ! -s "$JOB/pid" ] && [ $_n -lt 50 ]; do sleep 0.1 2>/dev/null || sleep 1; _n=$((_n + 1)); done
    out "JOB started $1"
}
job_main() {  # _job KIND ARG...: runs inside the session; HUP never ends it (the page going away must not)
    trap '' HUP
    echo $$ > "$JOB/pid"
    _k=$1; shift
    # The log has an end: past JOB_LOG_MAX bytes the lines are read and dropped (never refused: a job whose output
    # nobody takes any more would be stopped by it in the middle of its work).
    {
        (
            case $_k in
                fetch) fetch_zip "$@" ;;
                plan) zip_run "$1" plan ;;
                install) zip_run "$1" install ;;
                uninstall) uninstall "$@" ;;
                *) echo "! unknown job $_k"; exit 2 ;;
            esac
        ) 2>&1
        echo $? > "$JOB/rc.tmp"
    } | {
        # line by line, in the shell itself: each line is in the file the moment it was said (a watcher follows it)
        _n=0
        while IFS= read -r _l || [ -n "$_l" ]; do
            [ "$_n" -lt "$JOB_LOG_MAX" ] || continue
            printf '%s\n' "$_l" >> "$JOB/log"
            _n=$((_n + ${#_l} + 1))
            [ "$_n" -lt "$JOB_LOG_MAX" ] || echo "! the log is full; the job goes on, its further lines are not kept" >> "$JOB/log"
        done
    }
    [ -s "$JOB/rc.tmp" ] || echo 1 > "$JOB/rc.tmp"
    mv "$JOB/rc.tmp" "$JOB/rc"
    # the answers held a password: they go with the run that used them
    rm -f "$R/answers.conf"
}
follow() {  # the job's log from its start, then as it grows, then the status
    [ -f "$JOB/log" ] || fail no-job
    _off=0
    while :; do
        _done=; [ ! -f "$JOB/rc" ] || _done=1
        _size=$(fsize "$JOB/log")
        if [ "$_size" -gt "$_off" ]; then
            tail -c +$((_off + 1)) "$JOB/log" | head -c $((_size - _off))
            _off=$_size
        fi
        if [ -n "$_done" ]; then
            [ "$(fsize "$JOB/log")" = "$_off" ] || continue
            break
        fi
        case $(job_state) in interrupted) echo; out "EXIT interrupted"; return 0 ;; esac
        sleep "${MU300_WEB_POLL:-1}"
    done
    echo
    out "EXIT $(cat "$JOB/rc")"
}
cancel() {
    [ "$(job_state)" = running ] || fail no-job
    # from "systems" on the installer is writing: it is left to finish (every stage after that is safe to finish,
    # none is safe to cut short for no reason)
    case $(cat "$JOB/kind") in
        plan|fetch) ;;
        install) [ "$(num "$(job_step)" 0)" -lt 6 ] || fail too-late ;;
        *) fail too-late ;;
    esac
    _p=$(cat "$JOB/pid")
    kill -TERM -- "-$_p" 2>/dev/null || kill -TERM "-$_p" 2>/dev/null || kill -TERM "$_p" 2>/dev/null
    sleep 1
    kill -KILL -- "-$_p" 2>/dev/null || kill -KILL "-$_p" 2>/dev/null || kill -KILL "$_p" 2>/dev/null
    echo 143 > "$JOB/rc"
    rm -f "$R/answers.conf"
    out "JOB cancelled"
}

# ---- status
parts_json() {  # [["name",start,size],...] of the eMMC, in sectors, from sysfs (the partition table is not read)
    _first=1
    printf '['
    for _p in "$SYSFS"/block/mmcblk0/mmcblk0p*; do
        [ -e "$_p/start" ] || continue
        _n=$(sed -n 's/^PARTNAME=//p' "$_p/uevent" 2>/dev/null | head -n1)
        [ -n "$_first" ] || printf ','
        _first=
        printf '[%s,%s,%s]' "$(js "${_n:-${_p##*/}}")" "$(num "$(cat "$_p/start" 2>/dev/null)")" "$(num "$(cat "$_p/size" 2>/dev/null)")"
    done
    printf ']'
}
mount_ro() {  # mount_ro DIR: the existing Linux filesystem (the card's first, as init looks there first), read-only
    mkdir -p "$1" || return 1
    if [ "$SD_EX" = yes ]; then
        asw MU300_RO=1 MU300_SD_DEV="$SD_DEV" MU300_MOUNTS="$MOUNTS" "$ANDROID_SH" "$S/android-mount-mu300root.sh" "$1"
    else
        asw MU300_RO=1 MU300_OFF="$OFF" MU300_SIZE="$SIZE" MU300_MOUNTS="$MOUNTS" "$ANDROID_SH" "$S/android-mount-mu300root.sh" "$1"
    fi 2>/dev/null | grep -q '^MOUNTED'
}
umount_ro() { asw MU300_MOUNTS="$MOUNTS" "$ANDROID_SH" "$S/android-mount-mu300root.sh" -u "$1" >/dev/null 2>&1; rmdir "$1" 2>/dev/null; true; }
systems_json() {  # what the Linux filesystem holds: {"where":"sd|internal","systems":{"ubuntu":"v..."},...}
    printf '{"where":"%s"' "$([ "$SD_EX" = yes ] && echo sd || echo internal)"
    _m=$R/mnt
    if [ "$(job_state)" != running ] && mount_ro "$_m"; then
        printf ',"mounted":1,"systems":{'
        _first=1
        for _o in ubuntu openwrt openwrt-luci; do
            [ -d "$_m/$_o/etc" ] || continue
            [ -n "$_first" ] || printf ','
            _first=
            printf '%s:%s' "$(js "$_o")" "$(js "$(head -n1 "$_m/$_o/etc/mu300/image-version" 2>/dev/null)")"
        done
        # the system that boots, and whether Linux is its default boot (each system has the setting; the installer
        # writes the same one into all of them)
        _bo=$(head -n1 "$_m/.mu300/boot-os" 2>/dev/null)
        _dl=0
        case $_bo in ubuntu|openwrt|openwrt-luci) [ "$(head -n1 "$_m/$_bo/etc/mu300/default-boot" 2>/dev/null)" != linux ] || _dl=1 ;; *) _bo= ;; esac
        printf '},"boot_os":%s,"kernel":%s,"ubuntu":%s,"default_linux":%s,"attempts":%s' \
            "$(js "$_bo")" "$(js "$(head -n1 "$_m/boot/kernel" 2>/dev/null)")" \
            "$(js "$(sed -n 's/^VERSION_ID="\(.*\)"/\1/p' "$_m/ubuntu/usr/lib/os-release" 2>/dev/null | head -n1)")" \
            "$_dl" "$(num "$(head -n1 "$_m/.mu300/boot-attempts" 2>/dev/null)" 5)"
        umount_ro "$_m"
    else
        printf ',"mounted":0'
    fi
    printf '}'
}
status() {
    detect
    _deep=${1:-}
    _bc=; _valid=0
    if [ -e "$MISC" ]; then _bc=$(hex_at "$MISC" 2048 32); bc_valid "$_bc" && _valid=1; fi
    disk=0; last_end=0; OFF=0; SIZE=0; existing=no; DIRTY=-1
    if region_probe; then
        region_find_existing
        if [ -n "$_deep" ] && [ "$existing" = no ] && [ "$SIZE" -ge 16777216 ]; then region_dirty; fi
    fi
    # how many of 16 samples of the free region hold data; -1: not looked at
    case $DIRTY in [0-9]|1[0-6]) _dirty=$DIRTY ;; *) _dirty=-1 ;; esac
    sd_probe; SD_EX=no
    [ -z "$SD_DEV" ] || SD_EX=$(sd_existing)
    _free=$(df -Pk "$WORK_PARENT" 2>/dev/null | awk 'NR == 2 { printf "%.0f", $4 * 1024 }')
    _stalled=0
    if [ -f "$WORK_PARENT/mu300-install.stalled" ] && [ "$(sed -n 1p "$WORK_PARENT/mu300-install.stalled" 2>/dev/null)" = "$(boot_now)" ]; then _stalled=1; fi
    printf 'MU300-WEB-STATUS {"v":1,"model":%s,"product":%s,"device":"%s","android":%s,"build":%s,"boot_completed":%s' \
        "$(js "$MODEL")" "$(js "$PRODUCT")" "$DEVICE" "$(js "$(getprop ro.build.version.release)")" \
        "$(js "$(getprop ro.build.display.id)")" "$(num "$(getprop sys.boot_completed)")"
    printf ',"android_slot":"%s","linux_slot":"%s","slots_ok":%s,"root_kind":"%s","magisk":%s,"magiskboot":%s,"busybox":%s' \
        "$ANDROID_SLOT" "$LINUX_SLOT" "$(slots_ok && echo 1 || echo 0)" "$(root_kind)" \
        "$(js "$(magisk -v 2>/dev/null | head -n1)")" "$([ -x "$MAGISKBIN/magiskboot" ] && echo 1 || echo 0)" "$(js "$BB")"
    printf ',"misc":{"valid":%s,"bc":"%s"},"linux_boot":"%s"' "$_valid" "$([ "$_valid" = 1 ] && echo "$_bc")" "$(linux_boot_kind)"
    # part: the region is the GPT partition named mu300root another installer made (part_fs: what it holds, mu300root,
    # ext4 or none; part_count: how many have that name); foreign_image: that installer's image file in Android's data
    # (tools/storage.sh, FINDINGS 179)
    printf ',"disk":%s,"last_end":%s,"region":{"off":%s,"size":%s,"existing":"%s","dirty":%s,"part":%s,"part_fs":"%s","part_count":%s,"foreign_image":%s},"parts":%s' \
        "$(num "$disk")" "$(num "$last_end")" "$(num "$OFF")" "$(num "$SIZE")" "$existing" "$_dirty" "$(num "${IN_PART:-0}")" \
        "${PART_FS:-none}" "$(num "${PART_COUNT:-0}")" "$(num "${FOREIGN_IMAGE:-0}")" "$(parts_json)"
    printf ',"sd":{"dev":%s,"bytes":%s,"small":%s,"existing":"%s"}' "$(js "$SD_DEV")" "$(num "$SD_BYTES")" "$(num "$SD_SMALL")" "$SD_EX"
    printf ',"data_free":%s,"stalled":%s,"module":%s,"job":%s' "$(num "$_free")" "$_stalled" \
        "$([ -f "$MODULES/mu300_linux_switch/switch.sh" ] && [ ! -e "$MODULES/mu300_linux_switch/remove" ] && echo 1 || echo 0)" "$(job_json)"
    if [ -n "$_deep" ] && { [ "$existing" = yes ] || [ "$SD_EX" = yes ]; }; then
        root_dir
        printf ',"linux":%s' "$(systems_json)"
    fi
    printf '}\n'
}

# ---- a zip from the page
zip_dir() { echo "$R/x/${1%.zip}"; }
take() {  # take NAME SHA256
    name_ok "${1:-}" && case $1 in mu300-magisk-*.zip) ;; *) false ;; esac || fail bad-name
    sha_ok "${2:-}" || fail bad-sha
    root_dir
    case $(job_state) in running) fail job-running ;; esac
    mkdir -p "$R/zips" "$R/x" || fail no-root-dir
    # moved first, checked after: what is checked is the file only root can change. A pushed file counts before one
    # the device downloaded (which is in root's directory already).
    rm -rf "$R/zips/$1"
    bring "$1" "$R/zips/$1" "$ZIP_MAX"
    case $? in
        0) rm -f "$R/dl/$1" ;;
        2) fail too-large ;;
        *) [ -f "$R/dl/$1" ] && [ ! -L "$R/dl/$1" ] && mv "$R/dl/$1" "$R/zips/$1" && chmod 600 "$R/zips/$1" || fail no-file ;;
    esac
    _h=$(sha_of "$R/zips/$1")
    if [ "$_h" != "$2" ]; then rm -f "$R/zips/$1"; fail sha-mismatch "$_h"; fi
    # what is unpacked is the installer and the switch module, a few megabytes. A zip that says they hold more than
    # that, or more than /data can take with its reserve, is not unpacked at all.
    _u=$(unzip -l "$R/zips/$1" 'mu300/*' module.prop action.sh switch.sh bootchain.sh 'system/*' 2>/dev/null |
        awk 'NF >= 4 && $1 ~ /^[0-9]+$/ { s += $1 } END { printf "%.0f", s }')
    _u=$(num "$_u" 0)
    if [ "$_u" -gt "$UNPACK_MAX" ]; then rm -f "$R/zips/$1"; fail too-large "$_u"; fi
    [ $(( $(num "$(data_free "$R")" 0) - _u )) -ge "$DATA_RESERVE" ] || fail no-space
    _x=$(zip_dir "$1")
    rm -rf "$_x"; mkdir -p "$_x" || fail no-root-dir
    unzip -o -q "$R/zips/$1" 'mu300/*' module.prop action.sh switch.sh bootchain.sh 'system/*' -d "$_x" >/dev/null 2>&1 ||
        { rm -rf "$_x"; fail not-a-zip; }
    [ -f "$_x/mu300/install.sh" ] && [ -f "$_x/mu300/manifest" ] || { rm -rf "$_x"; fail not-a-zip; }
    _web=0; grep -q 'MU300_ANSWERS' "$_x/mu300/install.sh" && grep -q 'MU300-STEP' "$_x/mu300/install.sh" && _web=1
    _mf() { sed -n "s/^$1=//p" "$_x/mu300/manifest" | head -n1; }
    out "ZIP {\"name\":$(js "$1"),\"web\":$_web,\"tag\":$(js "$(_mf TAG)"),\"system\":$(js "$(_mf SYSTEM)"),\"os\":$(js "$(_mf OS)"),\"ubuntu\":$(js "$(_mf UBUNTU)"),\"kernel\":$(js "$(_mf KERNEL)")}"
}
# ---- the device downloads a release's Magisk zip itself (run fetch NAME SIZE, net [NAME])
# The tag is the one in the name (mu300-magisk-<tag>-<system>-k<kernel>.zip), the address this project's release of
# that tag: nothing else can be asked for. Whatever arrives is only a file in in/ until "take" has checked it
# against the release's checksum, which the page read from GitHub's API over the browser's own (verified) TLS.
#
# That check is what the download rests on, because the downloader cannot be trusted with it: Magisk's busybox wget
# (1.36.1) does TLS and follows GitHub's redirect, but says "TLS certificate validation not implemented" and takes a
# self-signed or a wrong-host certificate without a word (FINDINGS 72), and Android on these devices has no curl,
# no openssl and no wget of its own. So: the file must have exactly the size the release names, it is accepted only
# with the release's SHA-256, and wget's own redirects are watched - a connection to anything but github.com and
# its object hosts (*.githubusercontent.com), on port 443, ends the download.
REPO_URL=${MU300_REPO_URL:-https://github.com/dikeckaan/mu300-linux/releases/download}
FETCH_TRIES=${MU300_FETCH_TRIES:-5}
# the job's exit status says why a download failed, for the page (device.js: FETCH_FAILURES)
FETCH_NO_NETWORK=10; FETCH_BAD_HOST=11; FETCH_FAILED=12; FETCH_BAD_SIZE=13; FETCH_NO_SPACE=14
zip_tag() {  # the tag in a release zip's name, or nothing
    # in the shell's own patterns: the sed of a busybox built for Android (bionic's regex) knows no \| in a basic
    # expression, so the sed that did this on a computer said "not a release zip" to every name on a device
    case $1 in mu300-magisk-v[0-9]*.zip) ;; *) return 0 ;; esac
    _zt=${1#mu300-magisk-}; _zt=${_zt%.zip}
    case $_zt in *-k5.4|*-k6.18|*-k7.2|*-k7.3) _zt=${_zt%-k*} ;; *) return 0 ;; esac
    case $_zt in
        *-openwrt-luci) _zt=${_zt%-openwrt-luci} ;;
        *-openwrt) _zt=${_zt%-openwrt} ;;
        *-ubuntu-24.04|*-ubuntu-26.04) _zt=${_zt%-ubuntu-2?.04} ;;
        *) return 0 ;;
    esac
    case $_zt in ''|*[!0-9A-Za-z._]*|*..*) return 0 ;; esac
    echo "$_zt"
}
# wget_strangers LOG: the hosts busybox wget said it connected to ("Connecting to HOST (ADDRESS:PORT)") that are
# not GitHub's, or not on port 443. Nothing printed: every connection so far was one of the expected ones.
wget_strangers() {
    sed -n 's/^Connecting to \([^ ]*\) (.*:\([0-9]*\))[[:space:]]*$/\1 \2/p' "$1" 2>/dev/null | while read -r _h _p; do
        case $_h in github.com|*.githubusercontent.com) [ "$_p" = 443 ] || echo "$_h:$_p" ;; *) echo "$_h" ;; esac
    done
}
wget_connected() { grep -q '^Connecting to ' "$1" 2>/dev/null; }
# why a wget that brought nothing failed: there is no way out (no address for the name, no route, nobody answers)
wget_no_network() { grep -q -i -E 'bad address|unreachable|can.t connect|timed out|Connection re(fused|set)|closed prematurely|Temporary failure|No address' "$1" 2>/dev/null; }
wget_last() { tr '\r' '\n' < "$1" 2>/dev/null | grep -i '^wget:' | grep -v 'validation not implemented' | tail -n1 | tr -d '\000-\037' | cut -c1-160; }
uplink_kind() {  # Android's default network: mobile, wifi, ethernet, other, or none
    _dc=$(asw dumpsys connectivity 2>/dev/null)
    _dn=$(printf '%s\n' "$_dc" | sed -n 's/^Active default network: *//p' | head -n1 | tr -d ' \r')
    case $_dn in ''|none|*[!0-9]*) echo none; return 0 ;; esac
    case $(printf '%s\n' "$_dc" | grep "NetworkAgentInfo{network{$_dn}" | head -n1) in
        *CELLULAR*|*MOBILE*) echo mobile ;; *WIFI*) echo wifi ;; *ETHERNET*) echo ethernet ;; *) echo other ;;
    esac
}
# net [NAME]: can this device download from GitHub right now? A request for the headers of the release file NAME
# (or of github.com), with the same watch on where it connects. MU300-WEB-NET {json}; writes nothing but a log.
net() {
    _url=https://github.com/
    if [ -n "${1:-}" ]; then
        _tag=$(zip_tag "$1"); name_ok "$1" && [ -n "$_tag" ] || fail bad-name
        _url=$REPO_URL/$_tag/$1
    fi
    root_dir
    _l=$R/fetch.log; : > "$_l"
    _ok=0; _why=
    # the timeout of wget is for a connection that says nothing; one that says a little for ever is ended here
    ${MU300_WGET:-wget} -T "${MU300_NET_TIMEOUT:-10}" --spider "$_url" > "$_l" 2>&1 &
    _wp=$!; _n=0
    while kill -0 "$_wp" 2>/dev/null; do
        if [ "$_n" -ge "${MU300_NET_MAX_SECONDS:-30}" ]; then kill "$_wp" 2>/dev/null; echo "wget: no answer in time" >> "$_l"; break; fi
        sleep 1; _n=$((_n + 1))
    done
    if wait "$_wp"; then
        if [ -n "$(wget_strangers "$_l")" ]; then _why=host
        elif ! wget_connected "$_l"; then _why=tool
        else _ok=1; fi
    elif wget_no_network "$_l" || ! wget_connected "$_l"; then _why=no-network
    else _why=failed; fi
    out "NET {\"ok\":$_ok,\"why\":\"$_why\",\"uplink\":\"$(uplink_kind)\",\"detail\":$(js "$(wget_last "$_l")")}"
    rm -f "$_l"
}
fetch_zip() {  # fetch_zip NAME SIZE
    _tag=$(zip_tag "$1")
    [ -n "$_tag" ] || { echo "! not the name of a release zip: $1"; return $FETCH_FAILED; }
    _size=$(num "${2:-}" 0)
    [ "$_size" -gt 0 ] || { echo "! no size for $1"; return $FETCH_FAILED; }
    [ "$_size" -le "$ZIP_MAX" ] || { echo "! $_size bytes is more than any release zip has"; return $FETCH_BAD_SIZE; }
    # into root's own directory, never the adb user's (and a directory root made there would refuse the page's next
    # push: "couldn't create file: Permission denied")
    mkdir -p "$R/dl" || return $FETCH_FAILED
    _f=$R/dl/$1; _l=$R/fetch.log
    [ ! -L "$_f" ] || rm -f "$_f"
    # the start of this very file from an earlier try is continued (wget -c); anything else starts again
    _have=0; [ ! -f "$_f" ] || _have=$(fsize "$_f")
    if [ "$_have" -ge "$_size" ]; then rm -f "$_f"; _have=0; fi
    # /data is Android's own: what is still to come must leave the reserve free, or nothing is started
    _free=$(num "$(data_free "$R")" 0)
    if [ $((_free - (_size - _have))) -lt "$DATA_RESERVE" ]; then
        echo "! Android's storage has $((_free / 1048576)) MiB free; this download needs $(( (_size - _have) / 1048576 )) MiB and must leave $((DATA_RESERVE / 1048576)) MiB"
        return $FETCH_NO_SPACE
    fi
    say "the device downloads $1 itself, over its own internet connection"
    [ "$_have" = 0 ] || say "continuing after the $_have bytes that are here already"
    _try=0; _t0=$(date +%s)
    while :; do
        _try=$((_try + 1))
        : > "$_l"
        # The file cannot grow past the size the release names: the downloader's own limit (in blocks of 512 bytes;
        # the kernel refuses the write that would cross it), and the watch below for a shell without one.
        (
            ulimit -f $(( (_size + 511) / 512 )) 2>/dev/null
            exec ${MU300_WGET:-wget} -c -T "${MU300_FETCH_TIMEOUT:-30}" -O "$_f" "$REPO_URL/$_tag/$1"
        ) > "$_l" 2>&1 &
        _wp=$!
        while kill -0 "$_wp" 2>/dev/null; do
            sleep "${MU300_WEB_POLL:-1}"
            _bad=$(wget_strangers "$_l")
            if [ -n "$_bad" ]; then
                kill "$_wp" 2>/dev/null; wait "$_wp" 2>/dev/null
                rm -f "$_f" "$_l"
                echo "! the download was sent on to $(echo $_bad | cut -c1-120), which is not GitHub: stopped, nothing kept"
                return $FETCH_BAD_HOST
            fi
            _got=0; [ ! -f "$_f" ] || _got=$(fsize "$_f")
            if [ "$_got" -gt "$_size" ]; then
                kill "$_wp" 2>/dev/null; wait "$_wp" 2>/dev/null
                rm -f "$_f" "$_l"
                echo "! more arrived than the release's file holds ($_size bytes): stopped, nothing kept"
                return $FETCH_BAD_SIZE
            fi
            # an end to it whatever the other end does: a connection that sends a little for ever, a log that grows
            if [ $(( $(date +%s) - _t0 )) -gt "${MU300_FETCH_MAX_SECONDS:-3600}" ] || [ "$(fsize "$_l")" -gt 1048576 ]; then
                kill "$_wp" 2>/dev/null; wait "$_wp" 2>/dev/null
                rm -f "$_l"
                echo "! the download did not finish in time; $_got of $_size bytes are here and the next try continues from there"
                return $FETCH_FAILED
            fi
            [ ! -f "$_f" ] || echo "MU300-PROGRESS $_got $_size"
        done
        wait "$_wp"; _rc=$?
        _bad=$(wget_strangers "$_l")
        if [ -n "$_bad" ]; then
            rm -f "$_f" "$_l"
            echo "! the download was sent on to $(echo $_bad | cut -c1-120), which is not GitHub: nothing kept"
            return $FETCH_BAD_HOST
        fi
        _got=0; [ ! -f "$_f" ] || _got=$(fsize "$_f")
        echo "MU300-PROGRESS $_got $_size"
        if [ "$_got" -gt "$_size" ]; then
            rm -f "$_f" "$_l"
            echo "! the file that arrived is larger than the release's ($_got bytes, not $_size): nothing kept"
            return $FETCH_BAD_SIZE
        fi
        if [ "$_got" = "$_size" ]; then
            # where it came from must have been said: a wget that names no host cannot be watched
            if ! wget_connected "$_l"; then
                rm -f "$_f" "$_l"
                echo "! this wget does not say where it connects; the download cannot be watched and was not kept"
                return $FETCH_FAILED
            fi
            break
        fi
        _last=$(wget_last "$_l")
        if [ "$_got" -le "$_have" ]; then
            # this try brought nothing: there is no connection (or the release has no such file)
            if wget_no_network "$_l" || ! wget_connected "$_l"; then
                echo "! the device has no connection to GitHub${_last:+ ($_last)}"
                [ "$_got" -gt 0 ] || rm -f "$_f"
                rm -f "$_l"
                return $FETCH_NO_NETWORK
            fi
            echo "! the download failed${_last:+: $_last}"
            [ "$_got" -gt 0 ] || rm -f "$_f"
            rm -f "$_l"
            return $FETCH_FAILED
        fi
        if [ "$_try" -ge "$FETCH_TRIES" ]; then
            echo "! the connection broke $FETCH_TRIES times; $_got of $_size bytes are here and the next try continues from there"
            rm -f "$_l"
            return $FETCH_FAILED
        fi
        say "the connection broke at $_got of $_size bytes${_last:+ ($_last)}; continuing"
        _have=$_got
    done
    rm -f "$_l"
    chmod 600 "$_f"
    say "downloaded $_size bytes; not checked yet"
}
extra() {  # extra NAME FILE SHA256
    case ${1:-} in vpn|panel) ;; *) fail bad-name ;; esac
    name_ok "${2:-}" || fail bad-name
    sha_ok "${3:-}" || fail bad-sha
    root_dir
    mkdir -p "$R/extras" || fail no-root-dir
    _e=$R/extras/mu300-extra-$1.tar.gz
    bring "$2" "$_e" "$EXTRA_MAX"
    case $? in 0) ;; 2) fail too-large ;; *) fail no-file ;; esac
    _h=$(sha_of "$_e")
    if [ "$_h" != "$3" ]; then rm -f "$_e"; fail sha-mismatch "$_h"; fi
    out "EXTRA $1"
}
answers() {
    root_dir
    rm -f "$R/answers.conf"
    bring answers.conf "$R/answers.in" 65536
    case $? in 0) ;; 2) fail too-large ;; *) fail no-file ;; esac
    # a copy made by root under umask 077 from the file in root's directory, no password in it yet
    ( umask 077; grep -v '^[[:space:]]*MU300_PASSWORD[[:space:]]*=' "$R/answers.in" > "$R/answers.conf" ) || true
    rm -f "$R/answers.in"
    [ -f "$R/answers.conf" ] && chmod 600 "$R/answers.conf" || fail no-root-dir
    out "ANSWERS ok"
}
password() {  # one line on stdin
    [ -f "$R/answers.conf" ] || fail no-answers
    IFS= read -r _pw || [ -n "${_pw:-}" ] || fail no-stdin
    _pw=$(printf '%s' "$_pw" | tr -d '\r')
    [ ${#_pw} -ge 6 ] || fail short-password
    ( umask 077; printf 'MU300_PASSWORD=%s\n' "$_pw" >> "$R/answers.conf" ) || fail no-root-dir
    _pw=
    out "PASSWORD ok"
}
# The zip's installer, as Magisk's customize.sh starts it: under Magisk's busybox with ASH_STANDALONE=1, MU300_DIR the
# unpacked mu300/, ZIPFILE the zip. After an installation the zip's switch module goes into Magisk's modules, as
# customize.sh's last lines and tools/install-magisk-module.sh do.
zip_run() {  # zip_run NAME plan|install
    _x=$(zip_dir "$1")
    [ -f "$_x/mu300/install.sh" ] && [ -f "$R/zips/$1" ] || { echo "! the zip $1 is not on the device any more"; return 1; }
    [ -f "$R/answers.conf" ] || { echo "! no answers for this run"; return 1; }
    rm -rf "$_x/mu300/extras"
    if [ "$2" = install ] && [ -d "$R/extras" ]; then
        mkdir -p "$_x/mu300/extras" && cp "$R"/extras/mu300-extra-*.tar.gz "$_x/mu300/extras/" 2>/dev/null
    fi
    _dry=; [ "$2" != plan ] || _dry=1
    MU300_DIR=$_x/mu300 ZIPFILE=$R/zips/$1 MAGISKBIN=$MAGISKBIN MU300_ANSWERS=$R/answers.conf MU300_PROGRESS=1 \
        MU300_DRY_RUN=$_dry ASH_STANDALONE=1 "${MU300_INSTALL_BB:-$MAGISKBIN/busybox}" sh "$_x/mu300/install.sh"
    _rc=$?
    rm -rf "$_x/mu300/extras"
    if [ "$2" = install ] && [ $_rc = 0 ]; then
        rm -rf "$R/extras"
        switch_module "$_x" || echo "! the on-device switch (Magisk module) could not be installed; Linux is installed all the same"
    fi
    return $_rc
}
switch_module() {  # switch_module DIR: the zip's switch module, straight into Magisk's modules directory
    [ -d "$MODULES" ] || return 0
    _m=$MODULES/mu300_linux_switch
    [ -f "$1/module.prop" ] && [ -f "$1/switch.sh" ] || return 1
    rm -rf "$_m" && mkdir -p "$_m" &&
        cp -R "$1/module.prop" "$1/action.sh" "$1/switch.sh" "$1/bootchain.sh" "$1/system" "$_m/" || return 1
    chown -R 0:0 "$_m" 2>/dev/null
    find "$_m" -type d -exec chmod 755 {} + && find "$_m" -type f -exec chmod 644 {} + &&
        chmod 755 "$_m/switch.sh" "$_m/action.sh" "$_m/bootchain.sh" "$_m/system/bin/mu300-linux" || return 1
    echo "- the on-device switch is installed (after the next Android boot: su -c mu300-linux)"
}

# ---- uninstall: tools/uninstall.sh, on the device. Android's slot becomes the boot slot in misc (verified), then
# Android's boot image is copied over the Linux slot's (verified), then the Linux filesystem is erased as asked. In
# that order a run that stops anywhere leaves a device that boots Android. Android's boot partition, the partition
# table and userdata are never written; giving back space that was made by shrinking userdata stays with the
# uninstaller for computers.
UNSTEPS='check misc boot erase card cleanup done'
ustep() {
    _sn=0 _st=0
    for _s in $UNSTEPS; do _st=$((_st + 1)); [ "$_s" != "$1" ] || _sn=$_st; done
    echo "MU300-STEP $_sn/$_st $1"
}
# un_check: the device, the slots, where Linux is, the block misc gets. Sets existing/OFF/SIZE, SD_DEV/SD_EX, UN_LIVE
# and UN_NEW; dies before anything is written.
un_check() {
    detect
    mkdir -p "$R" && chmod 700 "$R" || die "cannot use $R; nothing was changed"
    [ -n "$DEVICE" ] || die "this does not look like a ZTE F50/MU300, U30 Air or V50; nothing was changed"
    slots_ok || die "cannot tell which slot Android runs from, or boot_a and boot_b are the same partition; nothing was changed"
    say "Android runs from slot $ANDROID_SLOT; Linux is on slot $LINUX_SLOT (boot_$LINUX_SLOT)"
    region_probe || die "could not read the partition table; nothing was changed"
    region_find_existing
    if [ "$existing" = yes ]; then
        # the region lies completely after the last partition and before the backup GPT (or inside the GPT partition
        # named mu300root that another installer made: region_in_bounds), on a 1 MiB boundary
        region_in_bounds "$OFF" "$SIZE" || die "the mu300root filesystem overlaps a partition; nothing was changed"
        [ $((OFF % 1048576)) -eq 0 ] || die "unexpected filesystem offset $OFF; nothing was changed"
        [ $((SIZE / 1048576)) -gt 0 ] || die "the mu300root filesystem has no size; nothing was changed"
        say "Linux filesystem: offset $OFF, $((SIZE / 1048576)) MiB$([ "${IN_PART:-0}" = 1 ] && echo ", in the GPT partition named mu300root (the partition stays; remove it with the installer that made it)")"
    else
        say "no mu300root filesystem on the eMMC"
    fi
    sd_probe; SD_EX=no
    [ -z "$SD_DEV" ] || SD_EX=$(sd_existing)
    [ "$SD_EX" != yes ] || say "Linux filesystem on the SD card: $SD_DEV, $(gib "$SD_BYTES")"
    UN_LIVE=$(hex_at "$MISC" 2048 32)
    bc_valid "$UN_LIVE" || die "misc holds no valid boot control block; nothing was changed"
    # the blocks the Linux boot image restores on Android (bc_files): Android's slot active and successful, the
    # other one not bootable
    case $ANDROID_SLOT in a) UN_NEW=$(bc_block "$UN_LIVE" a 9f 1e) ;; b) UN_NEW=$(bc_block "$UN_LIVE" b 1e 9f) ;; esac
    bc_valid "$UN_NEW" || die "could not build the boot control block of slot $ANDROID_SLOT; nothing was changed"
    # Android still runs from the slot it ran from: its partition is never the one written below
    [ "$(getprop ro.boot.slot_suffix)" = "_$ANDROID_SLOT" ] || die "Android no longer runs from slot $ANDROID_SLOT; nothing was changed"
}
un_misc() {  # Android's slot becomes the boot slot: 32 bytes, read back; a write that does not verify is undone
    if [ "$UN_LIVE" = "$UN_NEW" ]; then say "slot $ANDROID_SLOT is the boot slot already"; return 0; fi
    _t=$R/bc.bin
    hex_to_bytes "$UN_NEW" > "$_t" && [ "$(fsize "$_t")" = 32 ] || { rm -f "$_t"; die "could not build the block file; nothing was changed"; }
    hex_to_bytes "$UN_LIVE" > "$_t.live" && [ "$(fsize "$_t.live")" = 32 ] || { rm -f "$_t" "$_t.live"; die "could not keep a copy of the misc block; nothing was changed"; }
    if dd if="$_t" of="$MISC" bs=1 seek=2048 conv=notrunc 2>/dev/null && sync && [ "$(hex_at "$MISC" 2048 32)" = "$UN_NEW" ]; then
        rm -f "$_t" "$_t.live"
        say "slot $ANDROID_SLOT is the boot slot"
        return 0
    fi
    # misc may hold anything now: the block it held goes back, as the installer's arm_linux does
    dd if="$_t.live" of="$MISC" bs=1 seek=2048 conv=notrunc 2>/dev/null; sync
    if [ "$(hex_at "$MISC" 2048 32)" = "$UN_LIVE" ]; then
        rm -f "$_t" "$_t.live"
        die "misc did not verify after writing; the block it held before was written back and verified. Nothing else was changed."
    fi
    die "misc did not verify after writing, and the block it held before could not be written back. Do not reboot. Write it back as root: dd if=$_t.live of=$MISC bs=1 seek=2048 conv=notrunc"
}
un_boot() {  # Android's boot image over the Linux slot's, compared by sha256 after the page cache is dropped
    _a=$(sha_of "$BOOT_ANDROID")
    [ -n "$_a" ] || die "could not read boot_$ANDROID_SLOT (misc already points to slot $ANDROID_SLOT: Android keeps booting)"
    if [ "$(sha_of "$BOOT_LINUX")" = "$_a" ]; then say "boot_$LINUX_SLOT is a copy of boot_$ANDROID_SLOT already"; return 0; fi
    dd if="$BOOT_ANDROID" of="$BOOT_LINUX" bs=4194304 conv=notrunc 2>/dev/null && sync ||
        die "writing boot_$LINUX_SLOT failed (misc already points to slot $ANDROID_SLOT: Android keeps booting)"
    echo 3 > "$DROP_CACHES" 2>/dev/null || true
    [ "$(sha_of "$BOOT_LINUX")" = "$_a" ] ||
        die "boot_$LINUX_SLOT verify failed (misc already points to slot $ANDROID_SLOT: Android keeps booting)"
    say "boot_$LINUX_SLOT = boot_$ANDROID_SLOT"
}
un_erase() {  # un_erase secure|quick|keep: the Linux filesystem in the free eMMC region
    [ "$existing" = yes ] || return 0
    if [ "$1" = keep ]; then say "the Linux filesystem on the eMMC is kept (it does not boot any more)"; return 0; fi
    # nothing may still use the region (a loop device an installer left attached)
    for _o in "$SYSFS"/block/loop*/loop/offset; do
        [ "$(cat "$_o" 2>/dev/null)" != "$OFF" ] || die "the Linux region is still attached (${_o%/loop/offset}); restart Android and uninstall again"
    done
    _skip=$((OFF / 1048576)); _mib=$((SIZE / 1048576)); _end=$((_skip + _mib))
    # once more, in the units the writes use: every megabyte written lies behind the last partition and before the
    # backup partition table, or inside the GPT partition named mu300root
    region_in_bounds $((_skip * 1048576)) $((_mib * 1048576)) ||
        die "the region does not lie in the free space; the filesystem was not erased"
    if [ "$1" = quick ]; then
        # superblock, group descriptors and the first inode tables: no filesystem for mount or blkid any more
        _n=64; [ $_mib -ge 64 ] || _n=$_mib
        dd if=/dev/zero of="$EMMC" bs=1048576 seek=$_skip count=$_n conv=notrunc 2>/dev/null; sync
    else
        say "overwriting $_mib MiB; this takes a few minutes"
        # discard first where the eMMC can (fast, and it clears blocks the controller has remapped), then overwrite
        ! command -v blkdiscard >/dev/null 2>&1 || blkdiscard -o "$OFF" -l "$SIZE" "$EMMC" 2>/dev/null || true
        _at=$_skip
        while [ $_at -lt $_end ]; do
            _n=256; [ $((_at + _n)) -le $_end ] || _n=$((_end - _at))
            dd if=/dev/zero of="$EMMC" bs=1048576 seek=$_at count=$_n conv=notrunc 2>/dev/null ||
                die "writing failed at $_at MiB; boot_$LINUX_SLOT is Android already, uninstall again to finish the erase"
            _at=$((_at + _n))
            echo "MU300-PROGRESS $((_at - _skip)) $_mib"
        done
        sync
    fi
    [ "$(hex_at "$EMMC" $((OFF + 1080)) 2)" != 53ef ] || die "the filesystem signature is still there"
    if [ "$1" = secure ]; then
        # sample the region: no success while anything is still readable
        _left=0; _at=$_skip; _stepm=$((_mib / 32 + 1))
        while [ $_at -lt $_end ]; do
            _c=$(dd if="$EMMC" bs=1048576 skip=$_at count=1 2>/dev/null | tr -d '\000' | wc -c | tr -d ' ')
            [ "$_c" = 0 ] || _left=$((_left + 1))
            _at=$((_at + _stepm))
        done
        [ "$_left" = 0 ] || die "$_left of 32 samples still contain data; run the secure erase again"
        say "erased and verified (32 samples across the region are empty)"
    else
        say "erased (headers only: the files stay readable on the flash until the space is reused)"
    fi
}
un_card() {  # un_card WIPE erase|keep: the card's mu300sd, and the marker a kept internal installation may hold
    if [ "$SD_EX" = yes ] && [ "$2" = erase ]; then
        sd_erase         # checks the card again on the device before it writes (storage.sh)
        say "erased the Linux filesystem on the SD card ($SD_DEV)"
    elif [ "$SD_EX" = yes ]; then
        say "the SD card is kept as it is"
    fi
    # a kept internal installation may hold the marker of a card installation (init then waits for the card)
    if [ "$existing" = yes ] && [ "$1" = keep ] && { [ "$2" = erase ] || [ "$SD_EX" != yes ]; }; then
        _m=$R/mnt
        if mkdir -p "$_m" && asw MU300_OFF="$OFF" MU300_SIZE="$SIZE" MU300_MOUNTS="$MOUNTS" "$ANDROID_SH" "$S/android-mount-mu300root.sh" "$_m" 2>/dev/null | grep -q '^MOUNTED'; then
            rm -f "$_m/.mu300/root-on-sd"; sync
            umount_ro "$_m"
        fi
    fi
}
un_cleanup() {
    # what the installer for computers left in /data/local/tmp; never through a Linux filesystem still mounted there
    _t=${MU300_LOCAL_TMP:-/data/local/tmp}
    # (the adb user can write there: single names only, a link goes as a link, and no tree is ever walked)
    mounted_under "$_t/mu300root" || { [ -L "$_t/mu300root" ] && rm -f -- "$_t/mu300root"; rmdir "$_t/mu300root" 2>/dev/null; }
    for _f in "$_t"/mu300-*; do
        if [ -L "$_f" ] || [ -f "$_f" ]; then rm -f -- "$_f"; fi
    done
    rm -f -- "$_t/android-install.sh" "$_t/android-mount-mu300root.sh" "$_t/android-bootchain.sh"
    # the on-device switch would point at a slot that holds Android again: Magisk removes a module with this marker
    if [ -d "$MODULES/mu300_linux_switch" ]; then
        : > "$MODULES/mu300_linux_switch/remove"
        say "the on-device switch (Magisk module) goes with the next restart"
    fi
}
uninstall() {  # uninstall secure|quick|keep erase|keep
    case ${1:-} in secure|quick|keep) ;; *) die "uninstall: secure, quick or keep" ;; esac
    case ${2:-} in erase|keep) ;; *) die "uninstall: erase or keep for the SD card" ;; esac
    ustep check;   un_check
    ustep misc;    un_misc
    ustep boot;    un_boot
    ustep erase;   un_erase "$1"
    ustep card;    un_card "$1" "$2"
    ustep cleanup; un_cleanup
    ustep done
    say "Done. The device boots Android; restart it once to check."
}

# ---- backup: what an installation writes, copied into root's directory; the page reads each copy with "cat"
BACKUP_PARTS='boot uboot sml teecfg trustos vbmeta'      # of the Linux slot; plus misc and the partition table
backup_allowed() {  # never a partition with the unit's identity or calibration, whatever the list says
    case $1 in *nv*|prodnv|miscdata|persist|*cali*|isedata|ztecfg|ztepersist|super|userdata|metadata|*modem*|*phy*|pm_sys) return 1 ;; esac
    case " $BACKUP_PARTS misc " in *" ${1%_[ab]} "*) return 0 ;; esac
    return 1
}
backup() {
    detect
    slots_ok || fail no-slots
    region_probe || fail no-partition-table
    root_dir
    _o=$R/out
    rm -rf "$_o"; mkdir -p "$_o" || fail no-space
    # the copies are about 80 MB on an F50 (the boot partition is 64); with the reserve of /data
    [ "$(num "$(data_free "$R")" 0)" -ge $((DATA_RESERVE + 134217728)) ] || { rm -rf "$_o"; fail no-space; }
    _emit() {  # _emit FILE: one line for the page
        out "FILE $1 $(fsize "$_o/$1") $(sha_of "$_o/$1")"
    }
    for _p in $BACKUP_PARTS; do
        _n=${_p}_$LINUX_SLOT
        backup_allowed "$_n" || continue
        [ -e "$BY/$_n" ] || continue
        # the copy is checked against the partition: a read error gives no file instead of a short one
        dd if="$BY/$_n" of="$_o/$_n.img" bs=1048576 2>/dev/null && [ "$(sha_of "$_o/$_n.img")" = "$(sha_of "$BY/$_n")" ] ||
            { rm -rf "$_o"; fail read-failed "$_n"; }
        _emit "$_n.img"
    done
    dd if="$MISC" of="$_o/misc-head.bin" bs=4096 count=1 2>/dev/null || { rm -rf "$_o"; fail read-failed misc; }
    _emit misc-head.bin
    dd if="$EMMC" of="$_o/gpt-head.bin" bs=512 count=34 2>/dev/null &&
        dd if="$EMMC" of="$_o/gpt-tail.bin" bs=512 skip=$((disk - 33)) count=33 2>/dev/null || { rm -rf "$_o"; fail read-failed gpt; }
    _emit gpt-head.bin; _emit gpt-tail.bin
    for _p in "$SYSFS"/block/mmcblk0/mmcblk0p*; do
        [ -e "$_p/start" ] || continue
        echo "${_p##*/} $(sed -n 's/^PARTNAME=//p' "$_p/uevent" 2>/dev/null | head -n1) $(cat "$_p/start") $(cat "$_p/size")"
    done > "$_o/partitions.txt"
    _emit partitions.txt
    out "BACKUP android_slot=$ANDROID_SLOT linux_slot=$LINUX_SLOT"
}
backup_file() {  # the names backup gives its copies, and no other
    case $1 in
        boot_[ab].img|uboot_[ab].img|sml_[ab].img|teecfg_[ab].img|trustos_[ab].img|vbmeta_[ab].img) return 0 ;;
        misc-head.bin|gpt-head.bin|gpt-tail.bin|partitions.txt) return 0 ;;
    esac
    return 1
}
# cat NAME: one copy of the backup, as base64 (text survives a shell that puts the command on a terminal; the adb
# user cannot read root's directory, and root writes nothing where the adb user can)
backup_cat() {
    backup_file "${1:-}" || fail bad-name
    [ -f "$R/out/$1" ] && [ ! -L "$R/out/$1" ] || fail no-file
    base64 "$R/out/$1" || fail read-failed "$1"
}

# ---- clean
mounted_under() {  # something is mounted on DIR or below it (also when the mount table cannot be read)
    [ -r "$MOUNTS" ] || return 0
    awk -v p="$1" '$2 == p || index($2, p "/") == 1 { f = 1 } END { exit !f }' "$MOUNTS"
}
clean() {
    # root's directory only. What the page pushed into the adb user's directory, the page removes itself.
    [ -d "$R" ] && [ ! -L "$R" ] || { out "CLEAN ok"; return 0; }
    rm -rf "$R/out" "$R/incoming"
    case $(job_state) in running) out "CLEAN job-running"; return 0 ;; esac
    rm -f "$R/answers.conf" "$R/answers.in" "$R/bc.bin" "$R/fetch.log"
    rm -rf "$R/extras"
    # what a killed run of the installer left in /data/adb: its work directories, never one with a filesystem
    # still mounted in it (rm -rf into a mounted mu300root would delete the installed systems)
    for _w in "$WORK_PARENT"/mu300-magisk.*; do
        [ -d "$_w" ] && [ ! -L "$_w" ] || continue
        mounted_under "$_w" || rm -rf "$_w"
    done
    if [ "${1:-}" = all ]; then
        mounted_under "$R" || rm -rf "$R/zips" "$R/x" "$R/job" "$R/dl"
    fi
    out "CLEAN ok"
}

[ -z "${MU300_LIB:-}" ] || return 0
[ "$(id -u)" = 0 ] || [ -n "${MU300_SKIP_ROOT_CHECK:-}" ] || fail not-root
case ${1:-} in
    status) status "${2:-}" ;;
    take) take "${2:-}" "${3:-}" ;;
    extra) extra "${2:-}" "${3:-}" "${4:-}" ;;
    answers) answers ;;
    password) password ;;
    net) net "${2:-}" ;;
    run)
        case ${2:-} in
            plan|install)
                name_ok "${3:-}" || fail bad-name
                [ -f "$(zip_dir "$3")/mu300/install.sh" ] || fail no-zip
                [ -f "$R/answers.conf" ] || fail no-answers
                case ${4:-} in '') JOB_MORE= ;; more) JOB_MORE=1 ;; *) fail bad-argument ;; esac
                job_start "$2" "$3" ;;
            fetch)
                name_ok "${3:-}" && [ -n "$(zip_tag "$3")" ] || fail bad-name
                case ${4:-} in ''|*[!0-9]*) fail bad-argument ;; esac
                job_start fetch "$3" "$4" ;;
            uninstall)
                case ${3:-} in secure|quick|keep) ;; *) fail bad-argument ;; esac
                case ${4:-} in erase|keep) ;; *) fail bad-argument ;; esac
                job_start uninstall "$3" "$4" ;;
            *) fail bad-argument ;;
        esac ;;
    _job) shift; job_main "$@" ;;
    follow) follow ;;
    cancel) cancel ;;
    boot-linux)
        detect
        [ "$(linux_boot_kind)" = linux ] || fail no-linux
        # "dry": everything except writing misc and restarting
        if [ "${2:-}" = dry ]; then exec "$BB" sh "$S/switch.sh" --dry-run; fi
        exec "$BB" sh "$S/switch.sh" ;;
    backup) backup ;;
    cat) backup_cat "${2:-}" ;;
    clean) clean "${2:-}" ;;
    *) fail bad-command ;;
esac
