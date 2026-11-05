#!/bin/sh
# Adapt the cpu-load event watchdog to this branch's existing recovery loop.
# Keep its thirty-second safety deadline and two-confirmation retry budget.
watch_events=0 watch_event_pid= watch_event= WATCH_NOW=0
watch_clock() {
    local up rest
    IFS=' ' read -r up rest < /proc/uptime || return 1
    WATCH_NOW=${up%.*}
}
watch_events_stop() {
    if [ -n "$watch_event_pid" ]; then
        kill "$watch_event_pid" 2>/dev/null || true
        wait "$watch_event_pid" 2>/dev/null || true
    fi
    [ "$watch_events" = 0 ] || exec 8<&-
    watch_events=0 watch_event_pid=
}
watch_events_start() {
    local dir
    ubus -t 2 list mu300.modem >/dev/null 2>&1 || return 1
    dir=$(mktemp -d "${TMPDIR:-/tmp}/data-events.XXXXXX") || return 1
    if ! mkfifo -m 600 "$dir/events"; then rmdir "$dir"; return 1; fi
    ubus -S listen mu300.modem.urc mu300.sim-state network.interface ubus.object.remove > "$dir/events" 2>/dev/null </dev/null 8<&- &
    watch_event_pid=$!
    exec 8< "$dir/events"
    rm -f "$dir/events"; rmdir "$dir"
    watch_events=1
}
watch_event_decode() {
    watch_event=
    case "$1" in
        *'"ubus.object.remove"'*'"path":"mu300.modem"'*) watch_event=gone; return ;;
        *'"network.interface"'*)
            case "$1" in *'"interface":"'"${MU300_WAN:-wan}"'"'*) watch_event=network ;; esac
            return ;;
        *'"mu300.sim-state"'*)
            # Either slot can be selected by a completed data-SIM transaction;
            # the existing main loop re-resolves that choice before any AT.
            watch_event=sim; return ;;
        *'"mu300.modem.urc"'*) ;;
        *) return ;;
    esac
    # NR0 is the only unsolicited ingress, shared by both SIMs. Command
    # replies from other channels must not feed our own checks back as events.
    case "$1" in *'"channel":"stty_nr0"'*) ;; *) return ;; esac
    case "$1" in
        *'"line":"+CGEV:'*|*'"line":"NO CARRIER'*|\
        *'"line":"+CREG:'*|*'"line":"+CGREG:'*|\
        *'"line":"+CEREG:'*|*'"line":"+C5GREG:'*) watch_event=modem ;;
    esac
}
watch_next() {
    local interval=${MU300_WATCH_INTERVAL:-30} due line status idle_slack=
    { IFS= command read -r idle_slack < /proc/$$/timerslack_ns; } 2>/dev/null || idle_slack=
    watch_event=
    case "$interval" in ''|*[!0-9]*) echo 'mobile-data: watch interval must be whole seconds' >&2; return 1 ;; esac
    [ "$watch_events" = 1 ] || return 1
    watch_clock || return 1
    due=$((WATCH_NOW + interval))
    while [ "$WATCH_NOW" -lt "$due" ]; do
        status=0
        # Only the blocking wait gets slack; AT/route actions keep their defaults.
        [ -z "$idle_slack" ] || { printf '500000000\n' > /proc/$$/timerslack_ns; } 2>/dev/null || :
        IFS= read -r -t "$((due - WATCH_NOW))" line <&8 || status=$?
        [ -z "$idle_slack" ] || { printf '%s\n' "$idle_slack" > /proc/$$/timerslack_ns; } 2>/dev/null || :
        watch_clock || return 1
        if [ "$status" != 0 ]; then
            # Bash read reports its timer as 128 + SIGALRM. /proc/uptime
            # can still round below the deadline: this is not a dead FIFO.
            [ "$status" != 142 ] || continue
            [ "$WATCH_NOW" -ge "$due" ] || { echo "mobile-data: modem event listener disconnected" >&2; return 1; }
            return 0
        fi
        watch_event_decode "$line"
        if [ "$watch_event" = gone ]; then echo "mobile-data: modem service disconnected" >&2; return 1; fi
        # An event burst must not shorten the original interval between two
        # confirmed detach observations before recovery.
        if [ -n "$watch_event" ] && [ "${fails:-0}" = 0 ]; then return 0; fi
    done
    return 0
}
