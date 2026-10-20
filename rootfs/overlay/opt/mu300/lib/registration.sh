#!/bin/sh
# Passive cache of PS registration already received by the existing AT owner.
# No tty opens, AT commands, SIM identity, cell location or persistent writes.

# Stock onSimStatusChanged() treats these ECIND values as a request to read
# CPIN. They do NOT prove removal: SIM errors and PIN changes also use them.
mu300_sim_changed() {
    local slot=$1 line=$2 type value cause extra field
    case "$slot:$line" in 0:+ECIND:*|1:+ECIND:*) ;; *) return 0 ;; esac
    case "$line" in '+ECIND: 3,'*|'+ECIND:3,'*) ;; *) return 0 ;; esac
    line=${line#*:}
    line=$(printf '%s' "$line" | tr -d ' \r\t')
    # Ready can be 3,0,0,1 after a transient SIM-busy indication. The stock
    # RIL ignores numeric metadata after cause; CPIN confirms our own slot.
    case "$line" in ''|,*|*,|*,,*|*[!0-9,]*) return 0 ;; esac
    for field in $(printf '%s' "$line" | tr ',' ' '); do
        [ "${#field}" -le 10 ] && [ "$field" -le 4294967295 ] || return 0
    done
    IFS=, read -r type value cause extra <<EOF
$line
EOF
    [ "$type" = 3 ] || return 0
    case "$value:$cause" in
        0:|2:|100:|4:|0:[0-9]*|2:[0-9]*|100:[0-9]*|4:[0-9]*|1:2|1:34|1:25|1:1|1:7) ;;
        *) return 0 ;;
    esac
    case "$cause" in *[!0-9]*) return 0 ;; esac
    ( ubus -t 1 send mu300.sim-change "{\"slot\":$slot}" ) \
        3<&- 4<&- </dev/null >/dev/null 2>&1 &
}

mu300_sim_save() {
    local slot=$1 state=$2 old= value tmp
    local root=${MU300_REGISTRATION_DIR:-/run/mu300-registration}
    case "$slot:$state" in 0:absent|1:absent|0:ready|1:ready|0:locked|1:locked) ;; *) return 0 ;; esac
    value="{\"slot\":$slot,\"state\":\"$state\"}"
    [ ! -f "$root/sim$slot.json" ] || IFS= read -r old < "$root/sim$slot.json" || true
    [ "$value" != "$old" ] || return 0
    mkdir -p "$root" || return 0
    tmp="$root/.sim$slot.$$"
    (umask 077; printf '%s\n' "$value" > "$tmp") &&
        mv -f "$tmp" "$root/sim$slot.json" || { rm -f "$tmp"; return 0; }
    ( ubus -t 1 send mu300.sim-state "{\"slot\":$slot}" ) \
        3<&- 4<&- </dev/null >/dev/null 2>&1 &
    return 0
}

# Observe existing scoped SIM power/CPIN transactions, including the cold
# plan and dashboard reads. No additional normal-boot queries are introduced.
mu300_sim_reply() {
    local command=$1 reply=$2 slot line state= root=${MU300_REGISTRATION_DIR:-/run/mu300-registration}
    case "$command" in
        'AT+SPACTCARD=0;'*) slot=0; command=${command#*;} ;;
        'AT+SPACTCARD=1;'*) slot=1; command=${command#*;} ;;
        *) return 0 ;;
    esac
    case "$command" in +CPIN\?|+SFUN=2|+SFUN=3|+SFUN=4|+CFUN=0|+CFUN\?) ;; *) return 0 ;; esac
    while IFS= read -r line; do
        line=${line%"$(printf '\r')"}
        case "$line" in
            '+CFUN: 1') state=powered ;;
            '+CPIN: READY') state=ready ;;
            '+CPIN: SIM PIN'|'+CPIN: SIM PUK'|'+CPIN: SIM PIN2'|'+CPIN: SIM PUK2'|'+CPIN: PH-'*) state=locked ;;
            '+CPIN: NOT INSERTED'|'+CPIN: SIM NOT INSERTED') state=absent ;;
            '+CME ERROR: 10'|'+CME ERROR: SIM not inserted')
                # U30 returns CME 10 for a physically inserted but powered-off
                # SIM too. Never use that to prevent its normal RF startup.
                [ "$command" != '+CPIN?' ] || [ ! -f "$root/sim-power$slot" ] || mu300_sim_save "$slot" absent
                return 0 ;;
            OK)
                case "$command:$state" in
                    +SFUN=2:*|+SFUN=4:*|+CFUN\?:powered)
                        mkdir -p "$root" && : > "$root/sim-power$slot" ;;
                    +SFUN=3:*|+CFUN=0:*)
                        rm -f "$root/sim-power$slot" "$root/sim$slot.json" ;;
                    +CPIN\?:ready|+CPIN\?:locked|+CPIN\?:absent)
                        [ "$state" != absent ] || [ -f "$root/sim-power$slot" ] || return 0
                        mkdir -p "$root" && : > "$root/sim-power$slot"
                        mu300_sim_save "$slot" "$state" ;;
                esac
                return 0 ;;
        esac
    done <<EOF
$reply
EOF
    # Busy, timeout, ERROR and an incomplete response retain the known state.
    return 0
}

mu300_sim_is() {
    local state= root=${MU300_REGISTRATION_DIR:-/run/mu300-registration}
    case "$1" in 0|1) ;; *) return 1 ;; esac
    [ -f "$root/sim$1.json" ] && IFS= read -r state < "$root/sim$1.json" || return 1
    [ "$state" = "{\"slot\":$1,\"state\":\"$2\"}" ]
}
mu300_sim_absent() {
    [ -f "${MU300_REGISTRATION_DIR:-/run/mu300-registration}/sim-power$1" ] && mu300_sim_is "$1" absent
}

mu300_registration_save() {
    local slot=$1 line=$2 kind=${3:-urc} body stat rat a b c d e rest
    local root=${MU300_REGISTRATION_DIR:-/run/mu300-registration} old= value tmp
    case "$slot" in 0|1) ;; *) return 0 ;; esac
    case "$line" in +CEREG:*) body=${line#*:} ;; *) return 0 ;; esac
    IFS=, read -r a b c d e rest <<EOF
$body
EOF
    if [ "$kind" = query ]; then stat=$b; rat=$e; else stat=$a; rat=$d; fi
    stat=${stat#"${stat%%[![:space:]]*}"}
    stat=${stat%"${stat##*[![:space:]]}"}
    rat=${rat#"${rat%%[![:space:]]*}"}
    rat=${rat%"${rat##*[![:space:]]}"}
    # Only the numeric fields are exported; never export the raw URC.
    case "$stat" in 0|1|2|3|4|5|6|7|8|9|10) ;; *) return 0 ;; esac
    case "$rat" in 0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]) ;; *) rat=null ;; esac
    value="{\"slot\":$slot,\"stat\":$stat,\"rat\":$rat}"
    [ ! -f "$root/slot$slot.json" ] || IFS= read -r old < "$root/slot$slot.json" || true
    [ "$value" != "$old" ] || return 0
    mkdir -p "$root" || return 0
    # The URC owner is the only writer for its physical group; the selected
    # dialer's scoped query may also provide a newer state atomically.
    tmp="$root/.slot$slot.$$"
    (umask 077; printf '%s\n' "$value" > "$tmp") &&
        mv -f "$tmp" "$root/slot$slot.json" || { rm -f "$tmp"; return 0; }
    # A slow or absent ubus cannot stall an AT drainer. Only state/RAT
    # transitions fork this bounded notifier, not repeated signal/cell URCs.
    ( ubus -t 1 send mu300.registration "{\"slot\":$slot}" ) \
        3<&- 4<&- </dev/null >/dev/null 2>&1 &
    return 0
}

mu300_registration_reply() {
    local slot=$1 reply=$2 line
    while IFS= read -r line; do
        case "$line" in +CEREG:*) mu300_registration_save "$slot" "$line" query ;; esac
    done <<EOF
$reply
EOF
}
