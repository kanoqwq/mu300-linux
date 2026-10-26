#!/system/bin/sh
# The bootloader chain of the slot Android runs from, copied to the slot Linux boots from. Runs as root in Android,
# never in Linux.
#
#   android-bootchain.sh check             what would be copied; changes nothing
#   android-bootchain.sh sync [BACKUP_DIR] back up both slots' copies of every image that differs into BACKUP_DIR
#                                          (default /data/adb/mu300-bootchain/<date>), then copy Android's to the
#                                          Linux slot and read every one back
#
# Why (docs/FINDINGS.md, "The boot chain of the Linux slot"): LK boots the Linux slot with that slot's uboot, sml,
# teecfg, trustos and vbmeta, but Linux runs Android's modem through Android's slot (android-vendor-start). After an
# OTA the two slots hold different firmware builds, and the Linux slot's chain is the factory one (issue #86). Copying
# the five images of Android's slot over makes both slots one build (kano's advice: "it solves 99% of the problems").
# Nothing else is ever written: not the NV or calibration partitions, not boot, not the modem images, never a
# partition of Android's slot. Every check comes before the first write.
#
# Output: one machine line for the installers, then one line for people.
#   BOOTCHAIN-PLAN <android slot> <linux slot> [names that differ]
#   BOOTCHAIN-REFUSED: <reason>            exit 1, nothing written
#   BOOTCHAIN-OK <android slot> <linux slot> [names copied]       (sync)
#   BOOTCHAIN-FAILED: <reason>             exit 1: the Linux slot holds what it held before (restored and verified);
#                                          exit 2: it could not be restored - the backup is in BACKUP_DIR
#
# MU300_LIB=1 defines the functions and runs nothing (tests/test_bootchain.py, android/magisk/installer). The copy in
# android/magisk/mu300-linux-switch/bootchain.sh must stay identical (tests/test_bootchain.py).

# the chain LK and the SPL load per slot - and nothing else, whatever a caller passes
BOOTCHAIN_PARTS='uboot sml teecfg trustos vbmeta'
BC_BY=${MU300_BY_NAME:-/dev/block/by-name}
BC_DROP_CACHES=${MU300_DROP_CACHES:-/proc/sys/vm/drop_caches}
BC_LINUX_CMDLINE=6c6f676c6576656c3d3500    # "loglevel=5\0" at offset 44: the header of every Linux boot image

bc_refuse() { echo "BOOTCHAIN-REFUSED: $*"; return 1; }
bc_hex() { dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | od -An -tx1 -v | tr -d ' \n'; }
bc_sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
bc_size() {  # bytes of a partition (or, in tests, of a file); the string is compared, never computed with
    _bs=$(readlink -f "$1")
    if [ -b "$_bs" ]; then blockdev --getsize64 "$_bs" 2>/dev/null; else wc -c < "$_bs" | tr -d ' '; fi
}
# a name of the chain: the list above and nothing that could be NV, calibration or Android's own data
bc_allowed() {
    case " $BOOTCHAIN_PARTS " in *" $1 "*) ;; *) return 1 ;; esac
    case $1 in *nv*|prodnv|miscdata|persist|*cali*|isedata|ztecfg|ztepersist|boot|init_boot|vendor_boot|super|userdata|misc|metadata|*modem*|*phy*|pm_sys) return 1 ;; esac
}
bc_is_linux_image() { [ "$(bc_hex "$1" 44 11)" = "$BC_LINUX_CMDLINE" ]; }

# BC_A (Android's slot), BC_L (the other one), or a refusal
bc_setup() {
    BC_A=; BC_L=
    [ -n "${MU300_SKIP_ROOT_CHECK:-}" ] || [ "$(id -u)" = 0 ] || { bc_refuse "this needs root"; return 1; }
    # Linux has no getprop: from there LK's slot is Linux's own, and nothing here may run
    command -v getprop >/dev/null 2>&1 || { bc_refuse "this is not Android: the boot chain is only ever copied from Android"; return 1; }
    _m="$(getprop ro.product.model) / $(getprop ro.product.device)"
    case $_m in
        *U30Air*|*U30_Air*|*"U30 Air"*|*MU300*|*F50*|*mu300*) ;;
        # the ZTE V50 (experimental, FINDINGS 95): exactly V50 / MU3351 on ums9620 (kano read its chain, both slots alike)
        "V50 / MU3351")
            [ "$(getprop ro.board.platform)" = ums9620 ] || { bc_refuse "a V50 that is not on the UMS9620 platform"; return 1; } ;;
        *) bc_refuse "this is not a ZTE F50/MU300, U30 Air or V50 ($_m)"; return 1 ;;
    esac
    case $(getprop ro.boot.slot_suffix) in
        _a) BC_A=a; BC_L=b ;;
        _b) BC_A=b; BC_L=a ;;
        *) bc_refuse "cannot tell which slot Android runs from"; return 1 ;;
    esac
    [ -e "$BC_BY/misc" ] && [ -e "$BC_BY/boot_a" ] && [ -e "$BC_BY/boot_b" ] ||
        { bc_refuse "no misc, boot_a and boot_b in $BC_BY"; return 1; }
    # the running slot must be Android's, not a Linux image's (the slot suffix of a booted Linux names its own slot)
    ! bc_is_linux_image "$BC_BY/boot_$BC_A" || { bc_refuse "boot_$BC_A, the slot that is running, holds Linux"; return 1; }
    # misc: the AOSP bootloader_control block at 0x800 - slot a's metadata in byte 12, slot b's in byte 14
    # (priority bits 0-3, tries 4-6, successful 7)
    _bc=$(bc_hex "$BC_BY/misc" 2048 32)
    [ ${#_bc} = 64 ] && [ "$(printf %s "$_bc" | cut -c9-16)" = 42434142 ] ||
        { bc_refuse "misc holds no bootloader_control block"; return 1; }
    _ma=$(printf %s "$_bc" | cut -c25-26); _mb=$(printf %s "$_bc" | cut -c29-30)
    if [ "$BC_A" = a ]; then _am=$_ma; _lm=$_mb; else _am=$_mb; _lm=$_ma; fi
    _am=$((0x$_am)); _lm=$((0x$_lm))
    # Android's slot booted and was marked good: right after an update it is not yet, and the other slot is then the
    # one to fall back to
    [ $((_am & 128)) = 128 ] && [ $((_am & 15)) -gt 0 ] ||
        { bc_refuse "slot $BC_A (Android) is not marked as booted successfully: restart Android once and try again"; return 1; }
    # the other slot set to boot next: fine when that is Linux (an armed trial), never when it holds something else -
    # an Android update waiting for its restart, say
    if [ $((_lm & 15)) -gt $((_am & 15)) ] && ! bc_is_linux_image "$BC_BY/boot_$BC_L"; then
        bc_refuse "slot $BC_L is set to boot next and holds no Linux (an Android update waiting for a restart?)"; return 1
    fi
    return 0
}

# every image of the chain, on both slots: present, two different partitions of one size; BC_DIFF the names that
# differ, BC_HA_<name> and BC_HL_<name> the two hashes
bc_scan() {
    BC_DIFF=
    for _p in $BOOTCHAIN_PARTS; do
        bc_allowed "$_p" || { bc_refuse "$_p is not part of the boot chain"; return 1; }
        _a=$BC_BY/${_p}_$BC_A; _l=$BC_BY/${_p}_$BC_L
        [ -e "$_a" ] && [ -e "$_l" ] || { bc_refuse "${_p}_a or ${_p}_b is missing in $BC_BY"; return 1; }
        _ra=$(readlink -f "$_a"); _rl=$(readlink -f "$_l")
        [ -n "$_ra" ] && [ "$_ra" != "$_rl" ] || { bc_refuse "${_p}_a and ${_p}_b are the same partition"; return 1; }
        _sa=$(bc_size "$_a"); _sl=$(bc_size "$_l")
        [ -n "$_sa" ] && [ "$_sa" != 0 ] && [ "$_sa" = "$_sl" ] ||
            { bc_refuse "${_p}_a and ${_p}_b differ in size (${_sa:-?} and ${_sl:-?} bytes)"; return 1; }
        _ha=$(bc_sha "$_a"); _hl=$(bc_sha "$_l")
        [ ${#_ha} = 64 ] && [ ${#_hl} = 64 ] || { bc_refuse "cannot read ${_p}_a or ${_p}_b"; return 1; }
        eval "BC_HA_$_p=\$_ha; BC_HL_$_p=\$_hl"
        [ "$_ha" = "$_hl" ] || BC_DIFF="$BC_DIFF $_p"
    done
    return 0
}

bc_list() { echo $1 | sed 's/ /, /g'; }    # " trustos sml" -> "trustos, sml"
bc_plan_text() {  # the line for people
    if [ -z "$BC_DIFF" ]; then echo "boot chain: identical on both slots"
    else echo "boot chain: $(bc_list "$BC_DIFF") differ between the slots; Android's slot $BC_A is copied to slot $BC_L (backed up first)"
    fi
}

bc_check() {
    bc_setup && bc_scan || return 1
    echo "BOOTCHAIN-PLAN $BC_A $BC_L$BC_DIFF"
    bc_plan_text
}

bc_dd_opts() {  # conv=fsync where this dd has it (toybox and busybox do)
    if dd if=/dev/null of=/dev/null conv=fsync 2>/dev/null; then echo conv=fsync; fi
}
bc_settle() {  # bc_settle DEV: written through to the eMMC, and the next read comes from there, not from the cache
    sync
    blockdev --flushbufs "$(readlink -f "$1")" 2>/dev/null || true
    [ ! -w "$BC_DROP_CACHES" ] || echo 3 > "$BC_DROP_CACHES" 2>/dev/null || true
}
# bc_put NAME IMAGE: IMAGE onto the Linux slot's NAME - only ever a name of the chain and only ever the Linux slot
bc_put() {
    bc_allowed "$1" || return 1
    case $BC_L in a|b) ;; *) return 1 ;; esac
    [ "$BC_L" != "$BC_A" ] || return 1
    _t=$BC_BY/${1}_$BC_L
    [ "$(readlink -f "$_t")" != "$(readlink -f "$BC_BY/${1}_$BC_A")" ] || return 1
    dd if="$2" of="$_t" bs=1048576 $(bc_dd_opts) 2>/dev/null
    _rc=$?
    bc_settle "$_t"
    return $_rc
}

bc_sync() {  # bc_sync [BACKUP_DIR]
    bc_setup && bc_scan || return 1
    _dir=${1:-/data/adb/mu300-bootchain/$(date +%Y%m%d-%H%M%S)}
    if [ -z "$BC_DIFF" ]; then
        echo "BOOTCHAIN-OK $BC_A $BC_L"
        echo "boot chain: identical on both slots, nothing written"
        return 0
    fi
    # the backup, complete and checked, before anything is written
    mkdir -p "$_dir" || { bc_refuse "cannot create $_dir"; return 1; }
    : > "$_dir/SHA256SUMS" || { bc_refuse "cannot write $_dir"; return 1; }
    for _p in $BC_DIFF; do
        eval "_ha=\$BC_HA_$_p; _hl=\$BC_HL_$_p"
        for _s in "$BC_L:$_hl" "$BC_A:$_ha"; do
            _sl=${_s%%:*}; _h=${_s#*:}
            dd if="$BC_BY/${_p}_$_sl" of="$_dir/${_p}_$_sl.img" bs=1048576 2>/dev/null
            [ "$(bc_sha "$_dir/${_p}_$_sl.img")" = "$_h" ] ||
                { bc_refuse "the backup of ${_p}_$_sl in $_dir does not match the partition"; return 1; }
            echo "$_h  ${_p}_$_sl.img" >> "$_dir/SHA256SUMS"
        done
    done
    sync
    # still Android on the slot it was on at the start
    [ "$(getprop ro.boot.slot_suffix)" = "_$BC_A" ] || { bc_refuse "the running slot changed"; return 1; }
    echo "boot chain: backup of$BC_DIFF (both slots) in $_dir"
    _done=
    for _p in $BC_DIFF; do
        eval "_ha=\$BC_HA_$_p; _hl=\$BC_HL_$_p"
        _done="$_done $_p"
        if bc_put "$_p" "$_dir/${_p}_$BC_A.img" && [ "$(bc_sha "$BC_BY/${_p}_$BC_L")" = "$_ha" ]; then
            echo "boot chain: ${_p}_$BC_L <- ${_p}_$BC_A, verified"
            continue
        fi
        # the Linux slot gets back everything it held, each image read back again
        _bad=
        for _q in $_done; do
            eval "_hq=\$BC_HL_$_q"
            bc_put "$_q" "$_dir/${_q}_$BC_L.img" && [ "$(bc_sha "$BC_BY/${_q}_$BC_L")" = "$_hq" ] || _bad="$_bad $_q"
        done
        if [ -z "$_bad" ]; then
            echo "BOOTCHAIN-FAILED: ${_p}_$BC_L did not verify after writing; slot $BC_L was restored from $_dir and verified"
            return 1
        fi
        echo "BOOTCHAIN-FAILED: ${_p}_$BC_L did not verify after writing, and$_bad could not be restored. Do not boot slot $BC_L. The images it held are in $_dir (<name>_$BC_L.img, SHA256SUMS): dd if=$_dir/<name>_$BC_L.img of=$BC_BY/<name>_$BC_L"
        return 2
    done
    echo "BOOTCHAIN-OK $BC_A $BC_L$BC_DIFF"
    echo "boot chain: slot $BC_L now holds the chain of slot $BC_A; the images it held are in $_dir"
    return 0
}

[ -z "${MU300_LIB:-}" ] || return 0
case ${1:-} in
    check) bc_check ;;
    sync) shift; bc_sync "$@" ;;
    -h|--help|help) sed -n '2,8s/^# \{0,1\}//p' "$0"; exit 0 ;;
    *) echo "usage: android-bootchain.sh check | sync [BACKUP_DIR]" >&2; exit 2 ;;
esac
