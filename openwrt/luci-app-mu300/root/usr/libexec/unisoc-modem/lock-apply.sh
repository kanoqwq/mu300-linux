# Sourced by lock. Interactive writes only; the early boot replay deliberately
# keeps its established short AT path and MU300-specific encodings.
fail_apply() { printf '%s\n' "$1" >&2; return 1; }

validate_apply() {
    local kind=$1 val=$2 cap encoded pair fr pc
    case "$kind:$val" in volte:on|volte:off|vonr:on|vonr:off) return 0 ;; esac
    case "$kind:$val" in
        mode:auto|mode:4g|mode:sa|mode:nsa|endc:on|endc:off|auto_apply:on|auto_apply:off|reset:auto) return 0 ;;
        cell:auto|cell:off|cell:off-nr|cell:off-lte) return 0 ;;
    esac
    case "$kind" in
        lte|nr)
            [ -z "$val" ] && return 0
            printf '%s\n' "$val" | grep -Eq '^[1-9][0-9]*(,[1-9][0-9]*)*$' ||
                { fail_apply '无效的频段列表'; return 1; }
            [ "$kind" = lte ] && cap=$LTE_SUP || cap=$(nr_sup)
            [ "$(canonical_bands "$val")" = "$(canonical_bands "$(filter_bands "$val" "$cap")")" ] ||
                { fail_apply '所选频段不受此模组支持'; return 1; }
            if [ "$kind" = nr ]; then
                encoded=$(nr_bands "$(nr_masks "$(canonical_bands "$val")")")
                [ "$(canonical_bands "$encoded")" = "$(canonical_bands "$val")" ] ||
                    { fail_apply '所选频段无法用当前模组指令编码'; return 1; }
            fi
            ;;
        cell)
            printf '%s\n' "$val" | grep -Eq '^(nr|lte):[0-9]{1,7},[0-9]{1,4}$' ||
                { fail_apply '无效的小区参数'; return 1; }
            pair=${val#*:}; fr=${pair%,*}; pc=${pair#*,}
            # Decimal comparisons via awk also reject leading-zero surprises.
            printf '%s\n' "$fr,$pc" | awk -F, -v rat="${val%%:*}" '
                { exit !($1 >= (rat=="nr" ? 1 : 0) && $1 <= (rat=="nr" ? 3279165 : 262143) &&
                         $2 >= 0 && $2 <= (rat=="nr" ? 1007 : 503)) }' ||
                { fail_apply '无效的小区参数'; return 1; }
            ;;
        *) fail_apply '无效的网络设置'; return 1 ;;
    esac
}

# A number of vendor setters omit OK after applying. Silence is NOT success:
# allow a fresh readback to prove it, but never accept an explicit modem ERROR.
write_setting() {
    local reply
    reply=$(lock_at "$1" "${2:-6}") || true
    if printf '%s\n' "$reply" | tr -d '\r' | grep -Eq '^(ERROR|\+CME ERROR|\+CMS ERROR)(:|$)'; then
        fail_apply '模组拒绝了设置'; return 1
    fi
}

# Band masks may legitimately read back differently. Their own final OK is
# mandatory because equality readback is deliberately not an acceptance test.
write_band_setting() {
    local reply status=0
    reply=$(lock_at "$1" "${2:-6}") || status=$?
    reply=$(printf '%s\n' "$reply" | tr -d '\r')
    if printf '%s\n' "$reply" | grep -Eq '^(ERROR|\+CME ERROR|\+CMS ERROR)(:|$)'; then
        fail_apply '模组拒绝了设置'; return 1
    fi
    [ "$status" = 0 ] && [ "$(printf '%s\n' "$reply" | sed '/^[[:space:]]*$/d' | tail -n1)" = OK ] ||
        { fail_apply '频段设置未确认，请重试'; return 1; }
}

restart_write() {
    if [ "${INTERACTIVE_APPLY:-0}" = 1 ]; then
        write_setting "$1" "$2" || restart_failed=1
    else
        at "$1" "$2" >/dev/null
    fi
}

read_field() {
    at "$1" 4 | sed -n "s/^+$2: *//p" | head -n1 | tr -d ' \t'
}

voice_receipt() {
    local bid bit source current
    current=$(cat /proc/sys/kernel/random/boot_id) || return 1
    [ -r "$RUNDIR/voice-$1" ] || return 1
    read -r bid bit source < "$RUNDIR/voice-$1" 2>/dev/null || return 1
    [ "$bid" = "$current" ] || return 1
    case "$bit:$source" in 0:modem|1:modem) printf '%s %s\n' "$bit" "$source" ;; *) return 1 ;; esac
}

voice_record() {
    local dest=$RUNDIR/voice-$1 bid
    bid=$(cat /proc/sys/kernel/random/boot_id) || return 1
    printf '%s %s %s\n' "$bid" "$2" "$3" > "$dest.part.$$" && mv "$dest.part.$$" "$dest"
}

voice_read() {
    local kind=$1 timeout=${2:-4} cmd prefix reply raw status=0
    case $kind in
        volte) cmd='AT+CAVIMS?'; prefix=CAVIMS ;;
        vonr) cmd='AT+SP5GCMDS="get nr synch_param",42'; prefix=SP5GCMDS ;;
        *) return 1 ;;
    esac
    reply=$(lock_at "$cmd" "$timeout") || status=$?
    reply=$(printf '%s\n' "$reply" | tr -d '\r')
    [ "$status" = 0 ] && [ "$(printf '%s\n' "$reply" | sed '/^[[:space:]]*$/d' | tail -n1)" = OK ] || return 1
    raw=$(printf '%s\n' "$reply" | sed -n "s/^+$prefix: *//p" | head -n1 | tr -d ' \t"')
    if [ "$kind" = vonr ]; then
        raw=$(printf '%s\n' "$raw" | awk -F, '$1=="getnrsynch_param" && $2==42 && ($3=="0" || $3=="1") {print $3;exit}')
    fi
    case $raw in 0|1) printf '%s\n' "$raw" ;; *) return 1 ;; esac
}

voice_set() {
    local kind=$1 val=$2 timeout=${3:-6} bit cmd reply status=0 got source
    case $val in on) bit=1 ;; off) bit=0 ;; *) return 1 ;; esac
    case $kind in
        volte) cmd="AT+CAVIMS=$bit" ;;
        vonr) cmd="AT+SP5GCMDS=\"set nr param\",45,$bit" ;;
        *) return 1 ;;
    esac
    reply=$(lock_at "$cmd" "$timeout") || status=$?
    reply=$(printf '%s\n' "$reply" | tr -d '\r')
    if printf '%s\n' "$reply" | grep -Eq '^(ERROR|\+CME ERROR|\+CMS ERROR)(:|$)'; then
        fail_apply '模组拒绝了设置'; return 1
    fi
    got=$(voice_read "$kind" "$timeout") && [ "$got" = "$bit" ] ||
        { fail_apply '模组回读与请求设置不一致，未保存'; return 1; }
    source=modem
    voice_record "$kind" "$bit" "$source"
}

voice_matches() {
    local kind=$1 val=$2 got
    case $val in on) val=1 ;; off) val=0 ;; *) return 1 ;; esac
    got=$(voice_read "$kind" 2) || return 1
    [ "$got" = "$val" ]
}

voice_json() {
    local kind=$1 refresh=${2:-0} got source=unknown enabled=null saved query
    query=$refresh
    # Post-apply get is a child of the mutex owner. It must read both live
    # states: an untouched preference has no write receipt. Unrelated getters
    # still avoid querying halfway through another worker's radio cycle.
    if [ -d "$APPLY_DIR" ] && [ "$(cat "$APPLY_DIR/pid" 2>/dev/null)" != "$PPID" ]; then
        query=0
    fi
    if [ "$query" = 1 ]; then
        if got=$(voice_read "$kind"); then
            source=modem
            [ "$got" = 1 ] && enabled=true || enabled=false
        fi
    fi
    # Observations never overwrite a write receipt; only an accepted setter
    # can publish one. This also protects early replay from concurrent getters.
    if [ "$query" != 1 ]; then
        if got=$(voice_receipt "$kind"); then
            source=${got#* }; got=${got%% *}
            [ "$got" = 1 ] && enabled=true || enabled=false
        fi
    fi
    saved=$(cat "$STATE_DIR/$kind" 2>/dev/null)
    printf '{"enabled":%s,"source":"%s","saved":%s}' "$enabled" "$source" "$(qs "$saved")"
}

read_cells() {
    local rat=$1 p raw
    [ "$rat" = nr ] && p=16 || p=12
    raw=$(read_field "AT+SPFORCEFRQ=$p,3" SPFORCEFRQ)
    printf '%s\n' "$raw" | grep -Eq "^$p,3(,[0-9]+,[0-9]+)*$" || return 1
    printf '%s\n' "$raw" | awk -F, -v rat="$rat" '{for(i=3;i<NF;i+=2) print rat ":" $i "," $(i+1)}' | sort -u
}

verify_apply() {
    local raw got nr3
    case "$kind" in
        mode)
            raw=$(read_field 'AT+SPTESTMODE?' SPTESTMODE)
            [ "$(printf '%s' "$raw" | cut -d, -f1-3)" = "$m0,$m1,$prim" ] &&
                [ "$(read_field 'AT+SP5GRAN?' SP5GRAN)" = "$gran" ] ;;
        endc) endc_matches "$val" "$(read_field 'AT+SPENDC?' ENDC)" ;;
        lte|nr)
            # Readback remains available to the UI, but may be a normalized
            # subset and is not compared with the requested band list.
            return 0 ;;
        cell)
            got=$(read_cells lte) || return 1
            [ "$got" = "$want_lte" ] || return 1
            got=$(read_cells nr) || return 1
            [ "$got" = "$want_nr" ] ;;
    esac
}

# A committed reset is a tombstone for the old five keys. Never swap/remove
# STATE_DIR itself: SIM 0's directory may contain SIM 1 and identity data.
# Replay/apply finish interrupted cleanup before consuming another saved lock.
finish_reset_state() {
    [ -e "$STATE_DIR/reset_pending" ] || return 0
    local key
    for key in mode endc lte nr cell; do
        [ ! -d "$STATE_DIR/$key" ] || return 1
    done
    rm -f "$STATE_DIR/mode" "$STATE_DIR/endc" "$STATE_DIR/lte" \
        "$STATE_DIR/nr" "$STATE_DIR/cell" || return 1
    rm -f "$STATE_DIR/reset_pending"
}

verify_reset() {
    local kind val
    for kind in mode endc cell; do
        case "$kind" in mode) val=auto ;; endc) val=on ;; *) val= ;; esac
        verify_apply || return 1
    done
}

reset_modem_settings() {
    write_setting 'AT+SP5GRAN=1' &&
    write_setting "AT+SPTESTMODE=$m0,$m1,$prim" &&
    write_setting 'AT+SPENDC=1' &&
    write_band_setting 'AT+SPLBAND=1,0,0,0,0,0' &&
    write_band_setting 'AT+SPLBAND=2,0,0,0,0' &&
    write_setting 'AT+SPFORCEFRQ=12,4' &&
    write_setting 'AT+SPFORCEFRQ=16,4'
}

# F50 NR band masks are modem-wide even with atomic SPACTCARD addressing.
# Do not leave the other view showing stale shared fields for ten minutes.
# Saved settings remain per-SIM; never delete the other SIM's intent here.
invalidate_reset_cache() {
    rm -f "$LOCKCACHE"
    [ "$(cfg sim_slots)" = 2 ] || return 0
    case ${MU300_SIM_SLOT:-0}:$RUNDIR in
        0:*) rm -f "$RUNDIR/sim1/lock.json" ;;
        1:*/sim1) rm -f "${RUNDIR%/sim1}/lock.json" ;;
    esac
}

do_reset_apply() {
    local tm m0 gran=1 m1 prim want_lte= want_nr=
    m0=$(auto_work_mode)
    tm=$(read_field 'AT+SPTESTMODE?' SPTESTMODE)
    printf '%s\n' "$tm" | grep -Eq '^[0-9]+,[0-9]+,[01](,[0-9]+)*$' ||
        { fail_apply '无法读取当前 SIM 模式，未发送设置'; return 1; }
    m1=$(printf '%s' "$tm" | cut -d, -f2)
    prim=$(printf '%s' "$tm" | cut -d, -f3)
    # A partial modem write cannot be rolled back atomically. Invalidate the
    # old display, preserve saved intent on failure, and tell the caller.
    invalidate_reset_cache
    reset_modem_settings ||
        { fail_apply '清除未完成，部分设置可能已生效；已保存锁定未删除，请刷新后重试'; return 1; }
    verify_reset ||
        { fail_apply '清除回读不一致，部分设置可能已生效；已保存锁定未删除，请刷新后重试'; return 1; }
    sfun_restart || { fail_apply '协议栈重启被拒绝，请检查模组状态'; return 1; }
    [ "${restart_deferred:-0}" = 1 ] || at 'AT+CFUN?' 4 | grep -q '+CFUN: 1' ||
        { fail_apply '设置已写入，但射频未恢复，请检查模组状态'; return 1; }
    verify_reset || { fail_apply '协议栈重启后设置不一致，未保存'; return 1; }
    # Publish the replay barrier before deleting any file, so an interrupted
    # worker cannot leave a mixture of old locks for the next replay.
    save_state reset_pending 1 ||
        { fail_apply '模组已恢复自动，但清除结果保存失败；旧锁定仍可能在开机回放，请重试'; return 1; }
    finish_reset_state ||
        { fail_apply '模组已恢复自动，旧锁定回放已阻止，但保存文件清理失败，请重试'; return 1; }
    say "verified reset to automatic for SIM ${MU300_SIM_SLOT:-0}"
    invalidate_reset_cache
    "$0" get fresh >/dev/null 2>&1
    return 0
}

do_apply() {
    local kind=$1 val=$2 m0 m1 prim gran tm v rat p fr pc pair want_lte want_nr c INTERACTIVE_APPLY=1
    validate_apply "$kind" "$val" || return 1
    _t0=$(date +%s)
    if [ "$kind" = auto_apply ]; then
        save_state auto_apply "$val" && [ "$(cat "$STATE_DIR/auto_apply")" = "$val" ] ||
            { fail_apply '设置保存失败'; return 1; }
        return 0
    fi
    apply_lock || return 1
    trap 'rm -f "$APPLY_DIR/pid"; rmdir "$APPLY_DIR" 2>/dev/null' EXIT
    trap 'exit 1' INT TERM
    [ ! -d /run/unisoc-data-sim-switch ] || { fail_apply '另一项网络设置仍在执行，请稍后重试'; return 1; }
    finish_reset_state || { fail_apply '上次清除的保存文件尚未清理完成，请检查存储后重试'; return 1; }
    if [ "$kind" = reset ]; then
        do_reset_apply
        return $?
    fi
    case $kind in
        volte|vonr)
            voice_set "$kind" "$val" || return 1
            save_state "$kind" "$val" || { fail_apply '设置保存失败'; return 1; }
            say "applied $kind=$val for SIM ${MU300_SIM_SLOT:-0}"
            "$0" get fresh >/dev/null 2>&1
            return 0 ;;
    esac
    case "$kind" in
        mode)
            set -- $(mode_pair "$val"); m0=$1; gran=$2
            tm=$(read_field 'AT+SPTESTMODE?' SPTESTMODE)
            printf '%s\n' "$tm" | grep -Eq '^[0-9]+,[0-9]+,[01](,[0-9]+)*$' ||
                { fail_apply '无法读取当前 SIM 模式，未发送设置'; return 1; }
            m1=$(printf '%s' "$tm" | cut -d, -f2); prim=$(printf '%s' "$tm" | cut -d, -f3)
            write_setting "AT+SP5GRAN=$gran" && write_setting "AT+SPTESTMODE=$m0,$m1,$prim" || return 1
            ;;
        endc)
            [ "$val" = on ] && v=1 || v=2
            write_setting "AT+SPENDC=$v" || return 1 ;;
        lte)
            val=$(canonical_bands "$val")
            write_band_setting "AT+SPLBAND=1,$(lte_masks "$val")" || return 1 ;;
        nr)
            val=$(canonical_bands "$val")
            write_band_setting "AT+SPLBAND=2,$(nr_masks "$val" | awk -F, '{print $1",0,"$2","$3}')" || return 1 ;;
        cell)
            want_lte=$(read_cells lte) && want_nr=$(read_cells nr) ||
                { fail_apply '无法读取当前小区锁定，未发送设置'; return 1; }
            if [ "$val" = auto ]; then
                c=$RUNDIR/cell.json
                fr=$(jsonfilter -i "$c" -e '@.nr.arfcn' 2>/dev/null)
                pc=$(jsonfilter -i "$c" -e '@.nr.pci' 2>/dev/null)
                val="nr:$fr,$pc"
                if ! validate_apply cell "$val" 2>/dev/null; then
                    fr=$(jsonfilter -i "$c" -e '@.lte.earfcn' 2>/dev/null)
                    pc=$(jsonfilter -i "$c" -e '@.lte.pci' 2>/dev/null)
                    val="lte:$fr,$pc"
                fi
                validate_apply cell "$val" || return 1
            fi
            case "$val" in
                off|off-lte) write_setting 'AT+SPFORCEFRQ=12,4' || return 1; want_lte= ;;
            esac
            case "$val" in
                off|off-nr) write_setting 'AT+SPFORCEFRQ=16,4' || return 1; want_nr= ;;
                nr:*|lte:*)
                    rat=${val%%:*}; pair=${val#*:}
                    [ "$rat" = nr ] && p=16 || p=12
                    write_setting "AT+SPFORCEFRQ=$p,6,$pair" || return 1
                    if [ "$rat" = nr ]; then want_nr=$(printf '%s\n%s\n' "$want_nr" "$val" | sed '/^$/d' | sort -u)
                    else want_lte=$(printf '%s\n%s\n' "$want_lte" "$val" | sed '/^$/d' | sort -u); fi ;;
            esac
            ;;
    esac
    verify_apply || { fail_apply '模组回读与请求设置不一致，未保存'; return 1; }
    if [ "$kind" != endc ]; then
        sfun_restart || { fail_apply '协议栈重启被拒绝，请检查模组状态'; return 1; }
        [ "${restart_deferred:-0}" = 1 ] || at 'AT+CFUN?' 4 | grep -q '+CFUN: 1' ||
            { fail_apply '设置已写入，但射频未恢复，请检查模组状态'; return 1; }
        verify_apply || { fail_apply '协议栈重启后设置不一致，未保存'; return 1; }
    fi
    if [ "$kind" = cell ]; then
        val=$(printf '%s\n%s\n' "$want_lte" "$want_nr" | sed '/^$/d' | sort -u)
        # Replay consumes lines, including the final entry.
        [ -z "$val" ] || val="$val
"
    fi
    save_state "$kind" "$val" || { fail_apply '设置保存失败'; return 1; }
    say "verified apply $kind=$val"
    # One refresh, not the previous ten rounds that never terminated for auto.
    # Band outcome is based on its command acknowledgement; other fields verify.
    "$0" get fresh >/dev/null 2>&1
    return 0
}
