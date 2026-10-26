# Making room by a reset of Android, and the installation that resumes afterwards - EXPERIMENTAL (docs/FINDINGS.md
# 112). Ported from kano's desktop installer (kanoqwq/mu300-linux, branch clean-tf-7.2: desktop/installer.py and
# desktop/boot/part-prepare.sh, commits f6b0668d and 0e19fe32); the transaction itself is tools/resume.py's. Sourced by
# install.sh (su_do, dev_pull, adb_prop, sha256_of, ask, say, die, t, info, progress, gib are its own; needs $TOP,
# $WORK, $T).
#
# userdata is shrunk as before (only it: the last partition), but Android's data is not cleared by this installer:
# Android's own recovery is asked to format it (the bootloader message in misc, "--wipe_data"), which also starts
# its encryption anew - with file-based encryption a filesystem cleared from outside is one Android cannot mount, and
# recovery then waits for a person at a screen these devices do not have. Before anything is written, a transaction
# and a backup are saved on this computer; a token goes to the start of the region that is made, outside every
# partition, where the reset does not reach. After the reset - Android set up again, USB debugging on and root back -
# the installer finds the transaction and continues, when the device, its slot, its partition table (both copies,
# every CRC), the region, the token and this installer's tools are still the ones it was prepared with.
#
#   MU300_BACKUP_DIR=DIR    where the transactions and backups are kept (default ~/mu300-backups)
#   MU300_RESUME=0|1        0 (--no-resume): never resume; 1 (--resume): resume, or stop when there is nothing to
#   MU300_TRANSACTION=FILE  (--transaction FILE) this transaction, not the newest one for the device

RESUME=0; RESUME_FILE=
BACKUP_DIR=${MU300_BACKUP_DIR:-${HOME:-.}/mu300-backups}
resume_py() { python3 "$TOP/tools/resume.py" "$@"; }

# resume_pending: any transaction waiting in BACKUP_DIR (for any device). The installer does not update itself then:
# the transaction is bound to the tools that made it.
resume_pending() {
    [ "${MU300_RESUME:-}" != 0 ] || return 1
    [ -n "${MU300_TRANSACTION:-}" ] && return 0
    [ -d "$BACKUP_DIR" ] && command -v python3 >/dev/null 2>&1 && [ -n "$(resume_py find "$BACKUP_DIR" 2>/dev/null)" ]
}

# resume_facts: what identifies the device and its userdata, and how Android keeps its data (sysfs and properties,
# read only): SERIAL CID UD_NODE UD_START UD_SIZE CRYPTO (yes|no|unknown) CRYPTO_STATE CRYPTO_TYPE DATA_SOURCE
resume_facts() {
    # (no double quotes: install.ps1 sends the same text, and Windows PowerShell 5.1 drops them on the way to adb)
    _rf=$(su_do 'd=$(readlink -f /dev/block/by-name/userdata); n=${d##*/}; b=${n%p*}; echo NODE=$n; echo CID=$(cat /sys/class/block/$b/device/cid); echo START=$(cat /sys/class/block/$n/start); echo SIZE=$(cat /sys/class/block/$n/size); echo STATE=$(getprop ro.crypto.state); echo TYPE=$(getprop ro.crypto.type); while read s m r; do case $m in /data) echo SOURCE=$s; break ;; esac; done < /proc/mounts; echo MAGIC=$(dd if=$d bs=1 skip=1024 count=4 2>/dev/null | od -An -tx1 | tr -d \\040\\012); echo EXT=$(dd if=$d bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d \\040\\012)')
    _rv() { printf '%s\n' "$_rf" | sed -n "s/^$1=//p" | head -n1; }
    UD_NODE=$(_rv NODE); CID=$(_rv CID); UD_START=$(_rv START); UD_SIZE=$(_rv SIZE)
    CRYPTO_STATE=$(_rv STATE); CRYPTO_TYPE=$(_rv TYPE); DATA_SOURCE=$(_rv SOURCE)
    _m=$(_rv EXT); [ "$_m" = 53ef ] || _m=$(_rv MAGIC)
    SERIAL=$(adb_prop ro.serialno)
    # (kano's checks of the identity: an eMMC partition node and the eMMC's 128-bit CID)
    case $UD_NODE in mmcblk[0-9]p[0-9]*) ;; *) return 1 ;; esac
    case $UD_NODE in *[!a-z0-9]*) return 1 ;; esac
    [ ${#CID} = 32 ] || return 1
    case $CID in *[!0-9a-f]*) return 1 ;; esac
    case $UD_START$UD_SIZE in ''|*[!0-9]*) return 1 ;; esac
    [ -n "$SERIAL" ] || return 1
    CRYPTO=$(resume_py encrypted state="$CRYPTO_STATE" type="$CRYPTO_TYPE" source="$DATA_SOURCE" magic="$_m")
}

# resume_region NEW_LAST_END: RG_START and RG_END (sectors) of the free region behind a userdata that ends before
# sector NEW_LAST_END - tools/storage.sh's region_probe arithmetic
resume_region() {
    RG_START=$(( ($1 / 4096 + 1) * 4096 ))
    RG_END=$(( ((disk - 34) / 4096 - 1) * 4096 ))
    [ $RG_END -gt $RG_START ] || RG_END=$RG_START
}

# resume_backup DIR: what an installation reads first and what the prepare writes over, copied to DIR on this
# computer and checked against the device: the partition table (as read for the plan), the first block of misc, both
# slots' boot and boot chain partitions. Never the NV or calibration partitions. A SHA256SUMS lists them.
resume_backup() {
    _bd=$1
    mkdir -p "$_bd" || die "$(t 'could not create {1}' "$_bd")"
    cp "$WORK/gpt/gpt.head" "$_bd/gpt.head" && cp "$WORK/gpt/gpt.tail" "$_bd/gpt.tail" ||
        die "$(t 'could not copy the partition table into the backup')"
    su_do 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null > /data/local/tmp/mu300-pull.bin' >/dev/null
    adb pull /data/local/tmp/mu300-pull.bin "$_bd/misc-head.bin" >/dev/null 2>&1 || true
    su_do 'rm -f /data/local/tmp/mu300-pull.bin' >/dev/null
    [ -s "$_bd/misc-head.bin" ] || die "$(t 'could not read the misc header from the device')"
    for _bp in boot uboot sml teecfg trustos vbmeta; do
        for _bs in a b; do
            say "$(t 'Backing up {1}' "${_bp}_$_bs")"
            dev_pull "/dev/block/by-name/${_bp}_$_bs" "$_bd/${_bp}_$_bs.img"
            _bw=$(su_do "sha256sum /dev/block/by-name/${_bp}_$_bs" | cut -d' ' -f1)
            [ -n "$_bw" ] && [ "$_bw" = "$(sha256_of "$_bd/${_bp}_$_bs.img")" ] ||
                die "$(t 'the copy of {1} is not what the device holds; nothing was changed' "${_bp}_$_bs")"
        done
    done
    (cd "$_bd" && for _f in gpt.head gpt.tail misc-head.bin *.img; do printf '%s  %s\n' "$(sha256_of "$_f")" "$_f"; done) > "$_bd/SHA256SUMS"
}

# resume_push LOCAL REMOTE: LOCAL to the device, and there with the checksum it has here (a push that stopped
# halfway leaves a short file, which dd would write)
resume_push() {
    adb push "$1" "$2" >/dev/null 2>&1 || true
    [ "$(su_do "sha256sum $2" | cut -d' ' -f1)" = "$(sha256_of "$1")" ]
}

# resume_prepare NEW_END: the destructive stage. Everything has been asked and the backup made; the transaction is
# saved before the first write. Then: the partition table (the backup copy first), read back and checked; the token
# at the start of the region, read back; the bootloader message that has recovery format userdata, read back, with
# the A/B boot control block behind it as it was. The transaction then says "prepared". Android keeps running from
# its slot; nothing of boot_a, boot_b or the boot chain is written.
resume_prepare() {
    _ne=$1
    resume_region $((_ne + 1))
    _tok=$(resume_py token) || die "$(t 'could not make a token')"
    RESUME_FILE=$RESUME_BD/transaction.json
    _id=$(resume_py identity "$TOP" "${PKG:+$PKG/mu300/manifest}") || die "$(t 'could not compute the installer identity')"
    resume_py new "$RESUME_FILE" serial="$SERIAL" slot="$ANDROID_SLOT" cid="$CID" node="$UD_NODE" start="$UD_START" \
        size="$UD_SIZE" disk="$disk" target=$((_ne - UD_START + 1)) need=$((RG_END - RG_START)) usable_end="$USABLE_END" \
        region_start="$RG_START" region_end="$RG_END" token="$_tok" identity="$_id" crypto="$CRYPTO" \
        crypto_state="$CRYPTO_STATE" crypto_type="$CRYPTO_TYPE" backup="$RESUME_BD" ||
        die "$(t 'could not save the transaction; nothing was changed')"
    info resume.transaction "$RESUME_FILE"
    echo "  $(t 'transaction saved: {1}' "$RESUME_FILE")"
    resume_py marker "$_tok" "$WORK/gpt/marker.bin" && resume_py bcb "$WORK/gpt/bcb.bin" ||
        die "$(t 'could not prepare the files to write; nothing was changed')"

    step repartition 50
    say "$(t 'Rewriting the partition table')"
    python3 "$TOP/tools/resize-last-partition.py" "$WORK/gpt/gpt.head,$WORK/gpt/gpt.tail" \
        --disk-sectors "$disk" --name userdata --end-sector "$_ne" --out "$WORK/gpt/new" || die "$(t 'the resize was refused; nothing is changed')"
    for _f in new.head new.tail marker.bin bcb.bin; do
        resume_push "$WORK/gpt/$_f" "$T/mu300-$_f" ||
            { su_do "rm -f $T/mu300-new.head $T/mu300-new.tail $T/mu300-marker.bin $T/mu300-bcb.bin" >/dev/null
              die "$(t 'the new partition table did not arrive on the device as it was made; nothing is changed')"; }
    done
    _bc=$(su_do 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v' | tr -d ' \n')
    # the backup copy first: if power is lost between the two, the primary is still the old table and the device
    # boots, which is the recoverable order
    su_do "dd if=$T/mu300-new.tail of=/dev/block/mmcblk0 bs=512 seek=$((disk - 33)) conv=fsync 2>/dev/null" >/dev/null
    su_do "dd if=$T/mu300-new.head of=/dev/block/mmcblk0 bs=512 conv=fsync 2>/dev/null" >/dev/null

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
    [ "$got" = "$_ne" ] || die "$(t 'the table did not take (userdata still ends at {1}). Nothing else was changed.' "$got")"
    echo "  $(t 'userdata now ends at sector {1}' "$got")"

    # the token: the first 4 KiB of the region, outside every partition from now on
    su_do "dd if=$T/mu300-marker.bin of=/dev/block/mmcblk0 bs=512 seek=$RG_START count=8 conv=notrunc,fsync 2>/dev/null" >/dev/null
    [ "$(su_do "dd if=/dev/block/mmcblk0 bs=512 skip=$RG_START count=8 2>/dev/null | sha256sum" | cut -d' ' -f1)" = "$(sha256_of "$WORK/gpt/marker.bin")" ] ||
        die "$(t 'the token did not read back from the device; misc was not changed, Android keeps its data. Run the installer again.')"

    # the reset, by Android's own recovery: the bootloader message in the first 2 KiB of misc
    say "$(t "Asking Android's recovery to reset Android's data on the next boot")"
    su_do "dd if=$T/mu300-bcb.bin of=/dev/block/by-name/misc bs=2048 count=1 conv=notrunc,fsync 2>/dev/null" >/dev/null
    [ "$(su_do 'dd if=/dev/block/by-name/misc bs=2048 count=1 2>/dev/null | sha256sum' | cut -d' ' -f1)" = "$(sha256_of "$WORK/gpt/bcb.bin")" ] &&
        [ "$(su_do 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v' | tr -d ' \n')" = "$_bc" ] ||
        die "$(t 'the request for the reset did not read back from misc. The partition table is changed already: run the installer again, it continues from here.')"
    su_do "sync; rm -f $T/mu300-new.head $T/mu300-new.tail $T/mu300-marker.bin $T/mu300-bcb.bin" >/dev/null
    resume_py set "$RESUME_FILE" state=prepared || die "$(t 'could not save the transaction')"
}

# resume_lookup: RESUME=1 and RESUME_FILE when a transaction waits for this device (or MU300_TRANSACTION names one);
# dies when --resume was asked for and there is none. Needs region_probe (disk) and slot_setup.
resume_lookup() {
    [ "${MU300_RESUME:-}" != 0 ] || return 0
    # nothing saved on this computer, and not asked for: nothing more is read from the device
    [ "${MU300_RESUME:-}" = 1 ] || resume_pending || return 0
    command -v python3 >/dev/null 2>&1 || die "$(t '{1} not found' python3)"
    if ! resume_facts; then
        [ "${MU300_RESUME:-}" != 1 ] || die "$(t 'could not read what identifies this device (serial, eMMC CID, userdata)')"
        return 0
    fi
    RESUME_FILE=${MU300_TRANSACTION:-}
    [ -n "$RESUME_FILE" ] || RESUME_FILE=$(resume_py find "$BACKUP_DIR" "$SERIAL" "$CID" 2>/dev/null)
    if [ -z "$RESUME_FILE" ]; then
        [ "${MU300_RESUME:-}" != 1 ] || die "$(t 'no saved transaction for this device in {1} (--transaction FILE names one)' "$BACKUP_DIR")"
        return 0
    fi
    [ -f "$RESUME_FILE" ] || die "$(t '{1} not found' "$RESUME_FILE")"
    RESUME=1
    info resume.transaction "$RESUME_FILE"
    say "$(t 'A prepared installation is waiting: {1}' "$RESUME_FILE")"
    echo "  $(t 'EXPERIMENTAL: it continues the installation that made room by resetting Android. --no-resume ignores it.')"
}

# resume_validate [soft]: every check of the transaction against the device, before anything is written
# (resume.py check, read only); dies with the reason - with soft (--check) says it and returns 1.
resume_validate() {
    mkdir -p "$WORK/gpt"
    rm -f "$WORK/gpt/resume.head" "$WORK/gpt/resume.tail" "$WORK/gpt/resume-marker.bin" "$WORK/gpt/resume-bcb.bin"
    su_do "dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null > /data/local/tmp/gpt.head" >/dev/null
    su_do "dd if=/dev/block/mmcblk0 bs=512 skip=$((disk - 33)) count=33 2>/dev/null > /data/local/tmp/gpt.tail" >/dev/null
    adb pull /data/local/tmp/gpt.head "$WORK/gpt/resume.head" >/dev/null 2>&1 || true
    adb pull /data/local/tmp/gpt.tail "$WORK/gpt/resume.tail" >/dev/null 2>&1 || true
    su_do 'rm -f /data/local/tmp/gpt.head /data/local/tmp/gpt.tail' >/dev/null
    _rs=$(python3 "$TOP/tools/resize-last-partition.py" "$WORK/gpt/resume.head,$WORK/gpt/resume.tail" \
            --disk-sectors "$disk" --name userdata --show 2>&1) || _rs=
    [ -n "$_rs" ] || die "$(t 'could not read the partition table')"
    _rg() { printf '%s\n' "$_rs" | sed -n "s/^$1=//p"; }
    _rr=$(resume_py get "$RESUME_FILE" region_start) || die "$(t 'could not read the transaction {1}' "$RESUME_FILE")"
    _rr=${_rr#region_start=}
    case $_rr in ''|*[!0-9]*) die "$(t 'could not read the transaction {1}' "$RESUME_FILE")" ;; esac
    su_do "dd if=/dev/block/mmcblk0 bs=512 skip=$_rr count=8 2>/dev/null > /data/local/tmp/mu300-pull.bin" >/dev/null
    adb pull /data/local/tmp/mu300-pull.bin "$WORK/gpt/resume-marker.bin" >/dev/null 2>&1 || true
    su_do 'dd if=/dev/block/by-name/misc bs=2048 count=1 2>/dev/null > /data/local/tmp/mu300-pull.bin' >/dev/null
    adb pull /data/local/tmp/mu300-pull.bin "$WORK/gpt/resume-bcb.bin" >/dev/null 2>&1 || true
    su_do 'rm -f /data/local/tmp/mu300-pull.bin' >/dev/null
    _id=$(resume_py identity "$TOP" "${PKG:+$PKG/mu300/manifest}")
    _ok=0
    _out=$(resume_py check "$RESUME_FILE" serial="$SERIAL" slot="$ANDROID_SLOT" cid="$CID" node="$UD_NODE" \
        start="$UD_START" size="$UD_SIZE" disk="$disk" gpt_first="$(_rg LAST_FIRST)" gpt_end="$(_rg LAST_END)" \
        usable_end="$(_rg USABLE_END)" region_start=$((OFF / 512)) region_end=$(( (OFF + SIZE) / 512 )) \
        marker="$WORK/gpt/resume-marker.bin" bcb="$WORK/gpt/resume-bcb.bin" identity="$_id") || _ok=$?
    info resume.check "$_out"
    if [ $_ok != 0 ]; then
        if [ "${1:-}" = soft ]; then
            echo "  $(t 'The prepared installation cannot be continued: {1}' "${_out#RESUME-REFUSED: }")"
            return 1
        fi
        die "$(t 'The prepared installation cannot be continued: {1}' "${_out#RESUME-REFUSED: }")
$(t 'Nothing was written. Keep the device as it is and keep {1}.' "$RESUME_FILE")
$(t 'Another transaction: --transaction FILE. To install as if nothing had been prepared: --no-resume (the region is then treated as space that holds data).')"
    fi
    echo "  $(t 'the device, its slot, its partition table, the region, the token and this installer are the ones the transaction was made with')"
}
