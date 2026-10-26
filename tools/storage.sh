# Where the Linux filesystem goes: the free eMMC region or the SD card. Sourced by install.sh, uninstall.sh and
# tools/reset-password.sh (su_do, ask, t, die and gib are theirs).
#   region_probe     last_end / disk (sectors) and OFF / SIZE (bytes) of the free eMMC region; 1 when the device
#                    gave no answer
#   region_find_existing  existing=yes|no; an installed mu300root defines OFF / SIZE (IN_PART=1: in the GPT
#                    partition named mu300root that another installer made; region_probe already aims there)
#   region_in_bounds OFF SIZE  whether that place may hold the Linux filesystem (the free space or that partition)
#   region_part_refusal / region_image_note  another installer's layout this one cannot use, in words
#   region_dirty     DIRTY, how many of 16 samples across the region hold data
#   sd_probe         SD_DEV / SD_BYTES of the card in the slot (empty without one), SD_SMALL=1 when it is too small
#   sd_existing      yes when the card already holds a mu300sd filesystem, foreign for any other ext4, else no
#   choose_storage   SD_MODE=0|1; MU300_STORAGE=internal|sd answers without asking (internal while the card
#                    holds mu300sd still needs the typed word: that card would boot instead)
#   sd_kernel_ok     a card installation refuses a kernel bundle that cannot read the card
#   sd_erase         uninstall.sh: overwrite the start of the card's mu300sd filesystem (nothing else, ever)
#   write_install_env  install.sh: the text of mu300-install.env (install.ps1's InstallEnvText writes the same)
#   pw_states "OS..."  install.sh: "OS:SET|EMPTY|LOCKED|NOUSER" per system on the existing filesystem
#   pw_check OS PASSWORD  install.sh: MATCH when PASSWORD is the current one of OS's own account (android-pwcheck.sh)
#   adb_pubkey FILE  install.sh: the adb public key in FILE (adbkey.pub) as one safe line, "BASE64 name"
#   bootchain_line   install.sh and the Magisk installer: the plan's line from android-bootchain.sh check

SD_MIN=$((700 * 1024 * 1024))

# One line from the device: "<block device> <512-byte sectors> <type>" for the first mmc disk that is not the
# eMMC - its first partition when it has one, the whole card otherwise. The type keeps a second eMMC or an SDIO
# function out.
sd_probe() {
    SD_DEV=; SD_BYTES=0; SD_SMALL=0
    set -- $(su_do 'for b in /sys/block/mmcblk[1-9]; do [ -e $b/device/type ] || continue; n=${b##*/}; if [ -e $b/${n}p1 ]; then echo /dev/block/${n}p1 $(cat $b/${n}p1/size) $(cat $b/device/type); else echo /dev/block/$n $(cat $b/size) $(cat $b/device/type); fi; break; done')
    [ $# -eq 3 ] && [ "$3" = SD ] || return 0
    if [ $(( $2 / 2048 )) -lt $(( SD_MIN / 1048576 )) ]; then SD_SMALL=1; return 0; fi
    SD_DEV=$1; SD_BYTES=$(( $2 * 512 ))
}

sd_existing() {
    m=$(su_do "dd if=$SD_DEV bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1" | tr -d ' ')
    [ "$m" = 53ef ] || { echo no; return 0; }
    # LC_ALL=C: those bytes need not be text, and macOS's tr in a UTF-8 locale complains about them
    l=$(su_do "dd if=$SD_DEV bs=1 skip=1144 count=16 2>/dev/null" | LC_ALL=C tr -d '\000')
    # any other ext4, labelled or not, is foreign: android-install.sh's sd_prepare refuses to format it
    if [ "$l" = mu300sd ]; then echo yes; else echo foreign; fi
}

choose_storage() {
    SD_MODE=0
    _ex=no; [ -z "$SD_DEV" ] || _ex=$(sd_existing)
    case ${MU300_STORAGE:-} in
        internal) internal_over_card "$_ex"; return 0 ;;
        sd) [ -n "$SD_DEV" ] || die "$(t 'MU300_STORAGE=sd, but there is no usable SD card in the device')"
            SD_MODE=1; sd_not_foreign; return 0 ;;
        '') ;;
        *) die "$(t 'MU300_STORAGE must be internal or sd')" ;;
    esac
    [ -n "$SD_DEV" ] || return 0
    # too little room inside: the card is the way that needs no repartitioning; a card that holds an installation
    # already: that is what the device starts, so it is what an update or reinstall is about
    _d=internal; { [ "$SIZE" -lt "$SD_MIN" ] || [ "$_ex" = yes ]; } && _d=sd
    ask _st "$(t 'Where should the Linux filesystem go: internal storage or the SD card ({1}, {2})? (internal/sd)' "$SD_DEV" "$(gib "$SD_BYTES")")" $_d MU300_STORAGE
    case $_st in
        internal) internal_over_card "$_ex" ;;
        sd) SD_MODE=1; sd_not_foreign ;;
        *) die "$(t 'invalid choice')" ;;
    esac
}

# boot/init starts a mu300sd card before anything on the eMMC, so an internal installation made while such a card
# is in the slot never starts. Say so, and go on only when the user types the word.
internal_over_card() {  # internal_over_card EXISTING   (sd_existing of the card in the slot)
    [ "$1" = yes ] || return 0
    echo "$(t 'The SD card ({1}) holds a Linux installation (mu300sd), and the device always starts that one first: an installation to internal storage does not start while this card is in the slot.' "$SD_DEV")"
    echo "$(t 'Take the card out before the device restarts, or erase its installation first with the uninstaller.')"
    ask _ok "$(t 'Type internal to install to internal storage anyway')" no MU300_ANS_INTERNAL_OVER_CARD
    [ "$_ok" = internal ] || die "$(t 'cancelled')"
}

# A card installation needs a kernel that reads the card. The mainline bundles that do say so in ./features (older
# ones gate the SD host off, FINDINGS 31j): with one of those the device would not find its card and land in Android.
sd_kernel_ok() {  # sd_kernel_ok DIR: the unpacked bundle DIR is fine for this installation
    [ "$SD_MODE" = 1 ] || return 0
    grep -qx sdcard "$1/features" 2>/dev/null || return 1
}

# Another Linux filesystem on the card may be someone's data, and the device refuses to format it. Say so now,
# not after the password, the download and the build.
sd_not_foreign() {
    [ "$(sd_existing)" = foreign ] || return 0
    die "$(t 'the SD card ({1}) holds another Linux (ext4) filesystem, and the installer never formats one that is not its own (mu300sd).' "$SD_DEV")
$(t 'Copy off what you need and format the card elsewhere, use another card, or install to internal storage (MU300_STORAGE=internal).')"
}

# The device command that erases the mu300sd filesystem on DEV (uninstall.ps1 sends the same text, so no quotes of
# either kind). The host has checked all of this already; the device checks it again right before the write, so a
# wrong DEV can never reach the eMMC: not mmcblk0, an SD card in sysfs, still ext4 labelled mu300sd. Android lets go of
# the card first (vold mounts it as /dev/block/vold/public:179,N, which no grep for the card's name finds), then
# anything mounted from the card's own nodes is unmounted; a card mount of either kind that is still there stops it.
# Prints ERASED, or REFUSED/BUSY and writes nothing.
# The first 64 MiB hold the superblock, the group descriptors and the first inode tables: mount and blkid see no
# filesystem afterwards, and the rest of the card is free space for whatever the user formats it with.
sd_erase_cmd() {  # sd_erase_cmd DEV  (/dev/block/mmcblk1p1, or /dev/block/mmcblk1 for a card without partitions)
    _d=${1#/dev/block/}; _d=${_d%p[0-9]*}
    _s=${MU300_SYSFS:-/sys}; _m=${MU300_MOUNTS:-/proc/mounts}
    printf '%s' "case $1 in */mmcblk0|*/mmcblk0p*) echo REFUSED $1 is the eMMC; exit 1 ;; esac; " \
        "[ x\$(cat $_s/block/$_d/device/type 2>/dev/null) = xSD ] || { echo REFUSED $_d is not an SD card; exit 1; }; " \
        "set -- \$(dd if=$1 bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1); [ x\$1\$2 = x53ef ] || { echo REFUSED no ext4 on $1; exit 1; }; " \
        "[ x\$(dd if=$1 bs=1 skip=1144 count=16 2>/dev/null | tr -d \\\\000) = xmu300sd ] || { echo REFUSED no mu300sd on $1; exit 1; }; " \
        "for v in \$(sm list-volumes 2>/dev/null | grep -o ^public:179,[0-9]*); do sm unmount \$v >/dev/null 2>&1; done; " \
        "while read d m r; do case \$d in /dev/block/$_d|/dev/block/${_d}p*) umount \$m 2>/dev/null ;; esac; done < $_m; " \
        "grep -q -e ^/dev/block/$_d -e public:179, $_m && { echo BUSY; exit 1; }; " \
        "dd if=/dev/zero of=$1 bs=1048576 count=64 conv=notrunc 2>/dev/null; sync; echo ERASED"
}

# Only SD_DEV as sd_probe found it, and only while it holds mu300sd: any other ext4 on a card is someone's data.
# uninstall.sh asks no translated questions, so these messages stay English.
sd_erase() {
    [ -n "$SD_DEV" ] || die "no SD card to erase"
    case $SD_DEV in
        /dev/block/mmcblk0|/dev/block/mmcblk0p*) die "refusing $SD_DEV: that is the internal eMMC" ;;
        /dev/block/mmcblk[1-9]|/dev/block/mmcblk[1-9]p[0-9]|/dev/block/mmcblk[1-9]p[0-9][0-9]) ;;
        *) die "refusing $SD_DEV: not an SD card device" ;;
    esac
    [ "$(sd_existing)" = yes ] || die "the SD card ($SD_DEV) holds no mu300sd filesystem; nothing was erased"
    _o=$(su_do "$(sd_erase_cmd "$SD_DEV")")
    case $_o in *ERASED*) ;; *) die "the SD card was not erased: $_o" ;; esac
    [ "$(sd_existing)" = no ] || die "the SD card still shows a mu300sd filesystem"
}

# The first line of an adb public key file (~/.android/adbkey.pub: "BASE64 user@host", no newline at the end), as it
# goes into mu300-install.env inside single quotes: an Android RSA-2048 key (700 base64 characters, "QAAAA" first) and
# a name of [A-Za-z0-9@._:-] only. Status 1 when FILE holds no such key.
adb_pubkey() {
    _k= _c=
    IFS=' ' read -r _k _c < "$1" 2>/dev/null || [ -n "$_k" ] || return 1
    [ ${#_k} -eq 700 ] && [ "${_k%=}" != "$_k" ] || return 1
    case ${_k%=} in QAAAA*[!A-Za-z0-9+/]*) return 1 ;; QAAAA*) ;; *) return 1 ;; esac
    _c=$(printf '%s' "$_c" | tr -cd 'A-Za-z0-9@._:-' | cut -c1-64)
    echo "$_k ${_c:-installer}"
}

# mu300-install.env, what android-install.sh is told. OFF/SIZE (and their sectors) are always the internal region:
# in SD mode SIZE is the card's by now and INT_SIZE keeps the region's, which the root-on-sd marker needs; the card's
# own size is read on the device.
bootchain_line() {  # bootchain_line OUTPUT: BC_LINE for the plan from the device's BOOTCHAIN-PLAN line, else 1 and
    # BC_REFUSED, the reason
    _p=$(printf '%s\n' "$1" | sed -n 's/^BOOTCHAIN-PLAN //p' | head -n1)
    if [ -z "$_p" ]; then
        BC_REFUSED=$(printf '%s\n' "$1" | sed -n 's/^BOOTCHAIN-REFUSED: //p' | head -n1)
        [ -n "$BC_REFUSED" ] || BC_REFUSED=$(t 'no answer from the device')
        return 1
    fi
    set -- $_p
    _a=$1; _l=$2; shift 2
    if [ $# = 0 ]; then BC_LINE=$(t 'boot chain:     identical on both slots')
    else BC_LINE=$(t "boot chain:     {1} differ between the slots; Android's slot {2} is copied to slot {3} (backed up first)" "$(echo "$*" | sed 's/ /, /g')" "$_a" "$_l")
    fi
}

write_install_env() {
    _rs=$SIZE; [ "$SD_MODE" = 1 ] && _rs=$INT_SIZE
    printf 'OFF=%s
SIZE=%s
OFF_S=%s
SIZE_S=%s
FORMAT=%s
OSES="%s"
WIPE_LEGACY=%s
UPDATE=%s
BOOT_OS=%s
DEFAULT_LINUX=%s
BOOT_ATTEMPTS=%s
IMPORT_HOTSPOT=%s
KERNEL=%s
SD_MODE=%s
SD_DEV=%s
INTERNAL_EXISTS=%s
ADB=%s
ADB_KEY='"'"'%s'"'"'
SYNC_BOOTCHAIN=%s
PWHASH='"'"'%s'"'"'
KEEP_PW_OSES="%s"
' \
      "$OFF" "$_rs" "$((OFF / 512))" "$((_rs / 512))" "$FORMAT" "$OSES" "$WIPE_LEGACY" "$UPDATE" "$BOOT_OS" "$DEFAULT_LINUX" "$BOOT_ATTEMPTS" "$IMPORT_HOTSPOT" "$KERNEL" "$SD_MODE" "$SD_DEV" "$INTERNAL_EXISTS" "${ADB:-0}" "${ADB_KEY:-}" "${SYNC_BOOTCHAIN:-0}" "$PWHASH" "${KEEP_PW_OSES:-}"
}

# The passwords of the systems on the Linux filesystem already on the device, for an update that keeps them
# (install.sh asks; android-pwcheck.sh, pushed along, looks). Read through a read-only mount taken down again at once,
# like vpn_on_systems. Neither prints nor hands anywhere a password or a hash: one word per system.
pw_mount_env() { if [ "$SD_MODE" = 1 ]; then echo "MU300_SD_DEV=$SD_DEV"; else echo "MU300_OFF=$OFF MU300_SIZE=$SIZE"; fi; }
# pw_states "OS...": "OS:SET", "OS:EMPTY" (no password), "OS:LOCKED" or "OS:NOUSER" (no such system or account), one
# per line; nothing at all when the filesystem cannot be mounted
pw_states() {
    [ "$existing" = yes ] || return 0
    adb push "$TOP/tools/android-mount-mu300root.sh" "$TOP/tools/android-pwcheck.sh" $T/ >/dev/null 2>&1 || return 0
    su_do "$(pw_mount_env) MU300_RO=1 sh $T/android-mount-mu300root.sh $T/mu300pw >/dev/null 2>&1 && { for o in $1; do case \$o in ubuntu) u=ubuntu ;; *) u=root ;; esac; echo \$o:\$(sh $T/android-pwcheck.sh -s $T/mu300pw/\$o \$u); done; sh $T/android-mount-mu300root.sh -u $T/mu300pw >/dev/null 2>&1 && rmdir $T/mu300pw 2>/dev/null; }; true"
}
# pw_tools_push: the project's own tools that compute the hash - never a program of the Linux system being checked,
# which would run with Android's root (FINDINGS 126) - pushed once: PW_BIN (mu300-pwcheck, every method) and PW_BB (a
# busybox with mkpasswd: MD5- and SHA-crypt), files on this computer the caller found (install.sh's pw_tools); PW_ENV
# the environment android-pwcheck.sh gets for them. Without either it can confirm only an empty password.
pw_tools_push() {
    [ -z "${PW_ENV+set}" ] || return 0
    PW_ENV=
    if [ -n "${PW_BIN:-}" ] && adb push "$PW_BIN" $T/mu300-pwcheck >/dev/null 2>&1; then
        PW_ENV="MU300_PWCHECK_BIN=$T/mu300-pwcheck"
    fi
    if [ -n "${PW_BB:-}" ] && adb push "$PW_BB" $T/mu300-pwbb >/dev/null 2>&1; then
        PW_ENV="$PW_ENV MU300_PWCHECK_BB=$T/mu300-pwbb"
    fi
    [ -z "$PW_ENV" ] || su_do "chmod 755 $T/mu300-pwcheck $T/mu300-pwbb; true" >/dev/null
}
# pw_check OS PASSWORD: MATCH, NOMATCH, LOCKED, NOUSER or UNVERIFIABLE (nothing: the filesystem did not mount). The
# password goes to the device in a file of this user's alone, which android-pwcheck.sh reads on its standard input and
# the same command removes; never on a command line.
pw_check() {
    case $1 in ubuntu) _pu=ubuntu ;; *) _pu=root ;; esac
    pw_tools_push
    _pf=$(umask 077; mktemp "${TMPDIR:-/tmp}/mu300-pw.XXXXXX") || return 0
    printf '%s\n' "$2" > "$_pf"
    if ! adb push "$_pf" $T/mu300-pwcheck.in >/dev/null 2>&1; then rm -f "$_pf"; return 0; fi
    rm -f "$_pf"
    su_do "chmod 600 $T/mu300-pwcheck.in; $(pw_mount_env) MU300_RO=recover sh $T/android-mount-mu300root.sh $T/mu300pw >/dev/null 2>&1 && { $PW_ENV sh $T/android-pwcheck.sh $T/mu300pw/$1 $_pu < $T/mu300-pwcheck.in; sh $T/android-mount-mu300root.sh -u $T/mu300pw >/dev/null 2>&1 && rmdir $T/mu300pw 2>/dev/null; }; rm -f $T/mu300-pwcheck.in; true"
}

# The free eMMC region: from the first 2 MiB boundary after the last partition to the last one before the backup
# GPT. Byte counts are computed here, never in the device's shell (Android's mksh has 32-bit arithmetic).
# Also what another installer may have left (kanoqwq's desktop installer, FINDINGS 178/179): a GPT partition named
# mu300root (PART_OFF / PART_SIZE in bytes, empty without one) and an image file in Android's data (FOREIGN_IMAGE=1).
# With such a partition the Linux filesystem goes there - it was made for it, and boot/init boots it (stage=root-part)
# - so OFF / SIZE are the partition and IN_PART=1; the free region behind the partitions stays REG_OFF / REG_SIZE.
region_probe() {
    # (the same command as install.ps1's: no double quotes, which Windows PowerShell 5.1 drops on the way to adb) the
    # last line is the end of the last partition and the eMMC's size
    set -- $(su_do 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; grep -qx PARTNAME=mu300root $p/uevent 2>/dev/null && echo part:$(cat $p/start):$(cat $p/size); done; [ -f /data/mu300linux/openwrt.ext4 ] && echo image; echo $e $(cat /sys/block/mmcblk0/size)')
    PART_OFF=; PART_SIZE=; PART_COUNT=0; FOREIGN_IMAGE=0; IN_PART=0; last_end=; disk=
    for _w; do
        case $_w in
            part:*:*) _ps=${_w#part:}; _pz=${_ps#*:}; _ps=${_ps%%:*}
                      case $_ps$_pz in ''|*[!0-9]*) return 1 ;; esac
                      PART_COUNT=$((PART_COUNT + 1)); PART_OFF=$((_ps * 512)); PART_SIZE=$((_pz * 512)) ;;
            image) FOREIGN_IMAGE=1 ;;
            *[!0-9]*|'') return 1 ;;
            *) if [ -z "$last_end" ]; then last_end=$_w; elif [ -z "$disk" ]; then disk=$_w; else return 1; fi ;;
        esac
    done
    [ -n "$disk" ] || return 1
    start=$(( (last_end / 4096 + 1) * 4096 ))
    end=$(( ((disk - 34) / 4096 - 1) * 4096 ))
    # A partition table that ends at the end of the eMMC (one alignment block or less behind the last partition,
    # issue #65): the two boundaries cross, and a negative size is not a region. It is an empty one - the callers
    # then offer the card or make room - and its start is not read (region_find_existing).
    [ $end -gt $start ] || end=$start
    REG_OFF=$((start * 512)); REG_SIZE=$(( (end - start) * 512 ))
    OFF=$REG_OFF; SIZE=$REG_SIZE
    # two partitions of that name: which one is the system cannot be told, and neither is used (region_part_refusal)
    if [ "$PART_COUNT" = 1 ]; then OFF=$PART_OFF; SIZE=$PART_SIZE; IN_PART=1; else PART_OFF=; PART_SIZE=; fi
}

# region_in_bounds OFF SIZE (bytes): the place a Linux filesystem may be written to - wholly in the free space behind
# the last partition and before the backup GPT, or wholly inside the partition named mu300root (region_probe first)
region_in_bounds() {
    { [ $(($1 / 512)) -ge "$last_end" ] && [ $(( ($1 + $2) / 512 )) -le $((disk - 34)) ]; } ||
    { [ -n "${PART_OFF:-}" ] && [ "$1" -ge "$PART_OFF" ] && [ $(($1 + $2)) -le $((PART_OFF + PART_SIZE)) ]; }
}

# region_on_disk BYTES: whether the ext4 superblock of a region at BYTES (its second KiB) lies on the eMMC. A read
# past the end of the disk never returns on this device: the dd spins on one core, survives kill -9 and heats the
# SoC until a reboot (issues #43, #52, #65 - a 32 GB eMMC whose table ends at its end puts the region's start at the
# disk's end, and the fixed offset of the first releases lies beyond a 32 GB eMMC altogether).
region_on_disk() { [ $(( ($1 + 2048) / 512 )) -le "${disk:?region_probe first}" ]; }

# an existing installation defines the region (it may have been created with a slightly different size, or at the
# fixed offset of the first releases). Two bytes on the disk always read as four hex digits: no answer (adb lost
# for a moment, 2026-10-16 on F50-C) is status 2, never "no installation" - that skipped the update-or-wipe
# question and the data check, and the region was "free and empty".
# The places in the order boot/init looks: the free region, the partition named mu300root, the old fixed offset (not
# when it lies inside that partition: that is the partition's own data). A filesystem in the partition is as large
# as its superblock says, never more than the partition. PART_FS: what the partition holds - mu300root, ext4 (another
# ext4: not ours, never formatted) or none.
region_find_existing() {
    existing=no; PART_FS=none
    for cand in ${REG_OFF:-$OFF} ${PART_OFF:-} 27762098176; do
        region_on_disk "$cand" || continue
        if [ "$cand" = 27762098176 ] && [ -n "${PART_OFF:-}" ] && [ "$cand" -ge "$PART_OFF" ] &&
           [ "$cand" -lt $((PART_OFF + PART_SIZE)) ]; then continue; fi
        m=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1080)) count=2 2>/dev/null | od -An -tx1" | tr -d ' \n')
        case $m in [0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) existing=; return 2 ;; esac
        l=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1144)) count=16 2>/dev/null" | LC_ALL=C tr -d '\000')
        if [ "$cand" = "${PART_OFF:-}" ] && [ "$m" = 53ef ]; then PART_FS=ext4; [ "$l" = mu300root ] && PART_FS=mu300root; fi
        if [ "$m" = 53ef ] && [ "$l" = mu300root ]; then
            blocks=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1028)) count=4 2>/dev/null | od -An -tu4" | tr -d ' ')
            OFF=$cand; SIZE=$((blocks * 4096)); existing=yes; IN_PART=0
            if [ "$cand" = "${PART_OFF:-}" ]; then
                IN_PART=1; [ "$SIZE" -le "$PART_SIZE" ] || SIZE=$PART_SIZE
            fi
            return 0
        fi
    done
}

# region_part_refusal: why the partition named mu300root cannot be used, translated (status 0), else status 1 - one
# that holds another ext4 is someone else's (the device refuses to format it too, android-install.sh), and of two such
# partitions the one with the system cannot be told
region_part_refusal() {
    if [ "${PART_COUNT:-0}" -gt 1 ]; then
        t 'The eMMC has {1} partitions named mu300root, and which one holds Linux cannot be told. Nothing was changed: remove the one that is not used (with the installer that made it) and try again.' "$PART_COUNT"
    elif [ "${IN_PART:-0}" = 1 ] && [ "${existing:-}" != yes ] && [ "${PART_FS:-none}" = ext4 ]; then
        t 'The partition named mu300root holds another Linux (ext4) filesystem, not labelled mu300root, and the installer never formats one that is not its own. Nothing was changed.'
    else
        return 1
    fi
}

# region_image_note: kanoqwq's installer can also keep the system as an image file in Android's data
# (/data/mu300linux/openwrt.ext4, loop-mounted by its own boot image). This installer cannot update that layout, and the
# boot image it writes does not start it: said once, translated; status 1 when there is none
region_image_note() {
    [ "${FOREIGN_IMAGE:-0}" = 1 ] || return 1
    t 'Note: the Android data partition holds a Linux image file from another installer (/data/mu300linux/openwrt.ext4). This installer cannot update that layout, and after this installation the device starts the new system, not that one. The file is left as it is: delete it in Android to free its space.'
}

# unpartitioned space should be unused: sample 16 x 1 MiB across the region and count those with data. Empty is
# 0x00 or 0xFF: an eMMC reads back what its erase leaves (EXT_CSD ERASED_MEM_CONT), and on some F50s that is 0xFF -
# counted as data, a region that was never written stopped the install with "not empty".
region_dirty() {
    step=$(( SIZE / 1048576 / 16 ))
    probe=""; i=0
    while [ $i -lt 16 ]; do probe="$probe $(( OFF / 1048576 + i * step ))"; i=$((i + 1)); done
    DIRTY=$(su_do "n=0; for s in $probe; do c=\$(dd if=/dev/block/mmcblk0 bs=1048576 skip=\$s count=1 2>/dev/null | tr -d \"\\000\\377\" | wc -c); [ \$c -gt 0 ] && n=\$((n + 1)); done; echo \$n")
    # a count, or no answer (status 2): an empty DIRTY compared as "0" and the region passed as empty
    case $DIRTY in ''|*[!0-9]*) DIRTY=; return 2 ;; esac
}

# vpn_on_systems "OS...": the systems among OS... of the Linux filesystem already on the device whose VPN is on
# (ENABLE=1 in etc/mu300/vpn.conf), one per line, as "OS:ks" when its kill switch is on too (the images'
# lib/vpn-orphan.sh decides, pushed along and sourced on the device - it reads with sed, never sources a setting).
# Read through a read-only mount (android-mount-mu300root.sh with MU300_RO=1: not even the journal is replayed),
# taken down again at once. Nothing there, or nothing that mounts: nothing printed. The VPN is a module the new
# systems do not carry: the installer then offers it by default, and with the kill switch on it is needed.
vpn_on_systems() {
    [ "$existing" = yes ] || return 0
    _env="MU300_OFF=$OFF MU300_SIZE=$SIZE"
    [ "$SD_MODE" = 1 ] && _env="MU300_SD_DEV=$SD_DEV"
    adb push "$TOP/tools/android-mount-mu300root.sh" "$TOP/rootfs/overlay/opt/mu300/lib/vpn-orphan.sh" $T/ >/dev/null 2>&1 || return 0
    su_do "$_env MU300_RO=1 sh $T/android-mount-mu300root.sh $T/mu300probe >/dev/null 2>&1 && { . $T/vpn-orphan.sh; for o in $1; do if vpn_killswitch_wanted $T/mu300probe/\$o; then echo \$o:ks; elif [ \"\$(vpn_conf_value $T/mu300probe/\$o/etc/mu300/vpn.conf ENABLE)\" = 1 ]; then echo \$o; fi; done; sh $T/android-mount-mu300root.sh -u $T/mu300probe >/dev/null 2>&1; }; true"
}
