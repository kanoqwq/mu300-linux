#!/bin/sh
# OpenWrt native modem service and model-specific channel layouts.
modemd_require() {
    [ -x /opt/mu300/bin/mu300-atd-io ] && [ -r /opt/mu300/bin/mu300-modemd ] &&
        /usr/bin/ucode -e "import * as a from 'uloop'; import * as b from 'socket'; import * as c from 'ubus'; import * as d from 'mu300.registration'; import * as e from 'mu300.cellparse'; import * as f from 'uci';" || {
        echo 'mu300-atd: native modem runtime is incomplete' >&2; return 1;
    }
    # Refuse an unsafe in-place transition from an old physical owner. Repair
    # the image/reboot; never select a second implementation at runtime.
    local pidfile pid
    for pidfile in /run/mu300-at*/owner/pid; do
        [ -r "$pidfile" ] || continue
        IFS= read -r pid < "$pidfile"
        case "$pid" in ''|*[!0-9]*) continue ;; esac
        if [ -d "/proc/$pid" ]; then
            echo 'mu300-atd: a physical channel owner is already running; reboot before switching services' >&2
            return 1
        fi
    done
}

MODEMD_BROKERS=
modemd_broker() {
    # Metadata is fixed by this init script, never user input. One line per argv.
    MODEMD_BROKERS="${MODEMD_BROKERS}${MODEMD_BROKERS:+
}name=$1,dev=$2,dir=$3,urc=$4,live=$5,reopen=$6,ready=$7"
}
modemd_start() {
    procd_open_instance atd-io
    procd_set_param command /opt/mu300/bin/mu300-atd-io -s /run/mu300-modem/io.sock
    [ "$1" != 1 ] || procd_set_param respawn 3600 10 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
    procd_open_instance modemd
    procd_set_param command /usr/bin/ucode /opt/mu300/bin/mu300-modemd -s /run/mu300-modem/io.sock
    while IFS= read -r spec; do
        [ -z "$spec" ] || procd_append_param command "$spec"
    done <<EOF
$MODEMD_BROKERS
EOF
    procd_set_param env "MU300_SMS_POOL=$(mu300_sms_pool)"
    # The daemon owns no SIPC descriptor and may safely take over the C bridge.
    procd_set_param respawn 3600 5 0
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}

modemd_single_layout() {
    local reopen=1 ready= n urc live
    if [ "$(/opt/mu300/bin/mu300-device 2>/dev/null)" = u30pro ]; then
        # Native UMS9632 uses nr2 for general commands and nr0 for data.
        # Both belong to this one MU300 bridge; never add a second nr0 reader.
        MODEMD_BROKERS=
        live=/run/mu300-at2/urc/stty_nr0.log
        modemd_broker atd /dev/stty_nr2 /run/mu300-at '' "$live" 0 ''
        modemd_broker atd2 /dev/stty_nr0 /run/mu300-at2 '' "$live" 0 ''
        if [ "$(uci -q get unisoc_modem.main.sim_slots)" = 2 ]; then
            # Stock DSDS RIL owns nr0..2 and nr3..5. Keep our logical adapter
            # names, but mirror the native general/data roles in the second bank.
            live=/run/mu300-at5/urc/stty_nr3.log
            modemd_broker atd4 /dev/stty_nr5 /run/mu300-at4 '' "$live" 0 ''
            modemd_broker atd5 /dev/stty_nr3 /run/mu300-at5 '' "$live" 0 ''
        fi
        modemd_start 0
        return
    fi
    if [ "$(/opt/mu300/bin/mu300-device 2>/dev/null)" = u30air ]; then
        reopen=0 ready=sbuf:sbuf_5_006
    fi
    MODEMD_BROKERS=
    modemd_broker atd /dev/stty_nr1 /run/mu300-at 0 /run/mu300-at/urc/stty_nr0.log "$reopen" "$ready"
    modemd_broker atd2 /dev/stty_nr2 /run/mu300-at2 '' /run/mu300-at/urc/stty_nr0.log "$reopen" "$ready"
    for n in 3 4 5 6 7; do
        [ "$n" != 3 ] || [ "$(uci -q get unisoc_modem.main.sim_slots)" != 2 ] || continue
        urc= live=/run/mu300-at/urc/stty_nr0.log
        if [ "$n" = 4 ] && [ "$(uci -q get unisoc_modem.main.sim_slots)" = 2 ]; then
            urc=3 live=/run/mu300-at4/urc/stty_nr3.log
        fi
        modemd_broker atd$n /dev/stty_nr$n /run/mu300-at$n "$urc" "$live" "$reopen" "$ready"
    done
    modemd_start "$reopen"
}
modemd_sim2_layout() {
    MODEMD_BROKERS=
    modemd_broker atd /dev/stty_nr1 /run/mu300-at 0 /run/mu300-at/urc/stty_nr0.log 0 sbuf:sbuf_5_006
    modemd_broker atd2 /dev/stty_nr2 /run/mu300-at2 '' /run/mu300-at/urc/stty_nr0.log 0 sbuf:sbuf_5_006
    modemd_broker atd4 /dev/stty_nr4 /run/mu300-at4 3 /run/mu300-at4/urc/stty_nr3.log 0 sbuf:sbuf_5_006
    modemd_broker atd5 /dev/stty_nr5 /run/mu300-at5 '' /run/mu300-at4/urc/stty_nr3.log 0 sbuf:sbuf_5_006
    modemd_start 0
}
