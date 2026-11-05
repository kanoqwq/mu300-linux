#!/bin/sh
# Event wait adapted from liuwang97/cpu-load ccf89bb6; local per-SIM semantics.
# Transport is ubus' compact JSON, never modem text or message contents.
sms_events=0 sms_event= sms_event_pid=
sms_clock() {
    local up rest
    IFS=' ' read -r up rest < /proc/uptime || return 1
    SMS_NOW=${up%.*}
}
sms_events_stop() {
    if [ -n "$sms_event_pid" ]; then
        kill "$sms_event_pid" 2>/dev/null || true
        wait "$sms_event_pid" 2>/dev/null || true
    fi
    [ "$sms_events" = 0 ] || exec 8<&-
    sms_event_pid= sms_events=0
}
sms_events_start() {
    ubus -t 2 list mu300.modem >/dev/null 2>&1 || return 1
    local fifo=$LOCK/events
    rm -f "$fifo"
    mkfifo -m 600 "$fifo" || return 1
    ubus -S listen mu300.modem.urc mu300.sim-state ubus.object.remove > "$fifo" 2>/dev/null </dev/null 8<&- 9<&- &
    sms_event_pid=$!
    exec 8< "$fifo"
    rm -f "$fifo"
    sms_events=1
}
# Only the physical bank owned by this receiver may trigger its import/ACK.
# NR6/7 are additional SIM0 command channels, not a third SIM.
sms_event_decode() {
    sms_event=
    case "$1" in
        *'"ubus.object.remove"'*'"path":"mu300.modem"'*) sms_event=gone; return ;;
        *'"mu300.sim-state"'*)
            case "$1" in *'"slot":'"${MU300_SIM_SLOT:-0}"'}'*) sms_event=sim ;; esac
            return ;;
        *'"mu300.modem.urc"'*) ;;
        *) return ;;
    esac
    # NR0 can announce a stored message for either card. Wake a scoped list
    # query on both receivers; direct PDU spool/ACK ownership stays unchanged.
    case "$1" in
        *'"channel":"stty_nr0"'*'"line":"+CMTI:'*) sms_event=sync; return ;;
    esac
    # Spool completions are internal notifications, not another URC ingress.
    # Preserve the pool owner so the same frame cannot be ACKed twice.
    case "${MU300_SIM_SLOT:-0}:$1" in
        0:*'"channel":"stty_nr0"'*|0:*'"channel":"stty_nr1"'*|0:*'"channel":"stty_nr2"'*|0:*'"channel":"stty_nr6"'*|0:*'"channel":"stty_nr7"'*|1:*'"channel":"stty_nr3"'*|1:*'"channel":"stty_nr4"'*|1:*'"channel":"stty_nr5"'*) ;;
        *) return ;;
    esac
    case "$1" in
        *'"line":"+CMT (PDU spooled)"'*|*'"line":"+CMT (not spooled)"'*) sms_event=spool ;;

    esac
}
# Wait until a relevant event or the real monotonic deadline. Unrelated radio
# traffic does not start processes, scan logs or move the safety-sync deadline.
sms_events_wait() {
    local due=$1 line status idle_slack=
    { IFS= command read -r idle_slack < /proc/$$/timerslack_ns; } 2>/dev/null || idle_slack=
    sms_event=
    while :; do
        sms_clock || return 1
        [ "$SMS_NOW" -lt "$due" ] || return 0
        status=0
        # Only the blocking wait gets slack; AT/route actions keep their defaults.
        [ -z "$idle_slack" ] || { printf '500000000\n' > /proc/$$/timerslack_ns; } 2>/dev/null || :
        IFS= read -r -t "$((due - SMS_NOW))" line <&8 || status=$?
        [ -z "$idle_slack" ] || { printf '%s\n' "$idle_slack" > /proc/$$/timerslack_ns; } 2>/dev/null || :
        if [ "$status" != 0 ]; then
            sms_clock || return 1
            # Bash's timer can fire just before /proc/uptime rounds up.
            # Preserve the deadline; a real EOF still fails below.
            [ "$status" != 142 ] || continue
            [ "$SMS_NOW" -ge "$due" ] || { echo "mu300-smsd: modem event listener disconnected" >&2; return 1; }
            return 0
        fi
        sms_event_decode "$line"
        if [ "$sms_event" = gone ]; then echo "mu300-smsd: modem service disconnected" >&2; return 1; fi
        [ -z "$sms_event" ] || return 0
    done
}
