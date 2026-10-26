# Shared SIM preference storage. Runtime routing uses /run/mu300/sim-slot, never this preference.
# The caller sets SIM_CONFIG_U30=1 only after positive U30 Air/OpenWrt identification.
sim_config_root=${MU300_SIM_ROOT:-}
sim_cfg_valid() { case "$1" in 0|1) return 0;; *) echo 'SIM slot must be 0 or 1' >&2; return 1;; esac; }
sim_cfg_default() {
    local slot
    if command -v uci >/dev/null 2>&1; then
        if slot=$(uci -q get network.wan.sim_slot); then :
        elif [ "${SIM_CONFIG_U30:-0}" = 1 ]; then
            slot=$(cat "$sim_config_root/etc/mu300-sim-slot" 2>/dev/null || true)
        fi
    else
        slot=$(cat "$sim_config_root/etc/mu300/sim-slot" 2>/dev/null || true)
    fi
    slot=${slot:-0}
    sim_cfg_valid "$slot" || return 1
    printf '%s\n' "$slot"
}
sim_cfg_hot() {
    local hot
    [ "${SIM_CONFIG_U30:-0}" = 1 ] || { echo 0; return 0; }
    if hot=$(uci -q get network.wan.sim_hot); then :
    else hot=$(cat "$sim_config_root/etc/mu300-sim-hot" 2>/dev/null || true); fi
    hot=${hot:-0}
    case "$hot" in 0|1) printf '%s\n' "$hot";; *) echo 'Invalid SIM hot-switch policy' >&2; return 1;; esac
}
sim_cfg_write() {
    local option=$1 value=$2 old present=0
    case "$option" in sim_slot|sim_hot|v50_card) ;; *) return 1;; esac
    if old=$(uci -q get "network.wan.$option"); then present=1; fi
    uci set "network.wan.$option=$value" || return 1
    if uci commit network; then return 0; fi
    # A failed commit must not advertise the unsaved staged value as the next-boot preference.
    if [ "$present" = 1 ]; then uci set "network.wan.$option=$old" || true
    else uci -q delete "network.wan.$option" || true; fi
    return 1
}
sim_cfg_save_default() {
    local tmp
    sim_cfg_valid "$1" || return 1
    if command -v uci >/dev/null 2>&1; then
        sim_cfg_write sim_slot "$1"
    else
        mkdir -p "$sim_config_root/etc/mu300" || return 1
        tmp=$(mktemp "$sim_config_root/etc/mu300/sim-slot.XXXXXX") || return 1
        printf '%s\n' "$1" > "$tmp" && mv -f "$tmp" "$sim_config_root/etc/mu300/sim-slot"
    fi
}
# --- the ZTE V50's card (FINDINGS 204): ZTE's card ID 0-3, or nothing (the switches stay as the bootloader left
# them). OpenWrt: network.wan.v50_card, next to sim_slot; other systems: /etc/mu300/v50-card. mu300-sim validates it.
sim_cfg_card() {
    if command -v uci >/dev/null 2>&1; then
        uci -q get network.wan.v50_card 2>/dev/null || true
    else
        cat "$sim_config_root/etc/mu300/v50-card" 2>/dev/null || true
    fi
}
# sim_cfg_save_card CARD|'': saved, or (empty) forgotten
sim_cfg_save_card() {
    local tmp
    case "$1" in ''|0|1|2|3) ;; *) return 1;; esac
    if command -v uci >/dev/null 2>&1; then
        if [ -n "$1" ]; then sim_cfg_write v50_card "$1"; return; fi
        uci -q get network.wan.v50_card >/dev/null 2>&1 || return 0
        uci -q delete network.wan.v50_card && uci commit network
    elif [ -n "$1" ]; then
        mkdir -p "$sim_config_root/etc/mu300" || return 1
        tmp=$(mktemp "$sim_config_root/etc/mu300/v50-card.XXXXXX") || return 1
        printf '%s\n' "$1" > "$tmp" && mv -f "$tmp" "$sim_config_root/etc/mu300/v50-card"
    else
        rm -f "$sim_config_root/etc/mu300/v50-card"
    fi
}
sim_cfg_save_hot() {
    sim_cfg_valid "$1" || return 1
    [ "${SIM_CONFIG_U30:-0}" = 1 ] && command -v uci >/dev/null 2>&1 || return 1
    sim_cfg_write sim_hot "$1"
}
sim_cfg_unstage_added() {
    local key
    for key in "$@"; do
        case "$key" in sim_slot|sim_hot|apn_internal|pdptype_internal) uci -q delete "network.wan.$key" || true;; esac
    done
}
sim_cfg_migrate() {
    local slot hot saved_slot saved_hot slot_present=0 hot_present=0 touched= key value old destination
    [ "${SIM_CONFIG_U30:-0}" = 1 ] || return 0
    command -v uci >/dev/null 2>&1 || return 1
    if saved_slot=$(uci -q get network.wan.sim_slot); then slot_present=1; fi
    if saved_hot=$(uci -q get network.wan.sim_hot); then hot_present=1; fi
    [ "$slot_present" = 0 ] || sim_cfg_valid "${saved_slot:-0}" || return 1
    [ "$hot_present" = 0 ] || sim_cfg_valid "${saved_hot:-0}" || return 1
    slot=$(cat "$sim_config_root/etc/mu300-sim-slot" 2>/dev/null || true)
    hot=$(cat "$sim_config_root/etc/mu300-sim-hot" 2>/dev/null || true)
    # Validate every legacy value before making any staged change.
    [ "$slot_present" = 1 ] || [ -z "$slot" ] || sim_cfg_valid "$slot" || return 1
    [ "$hot_present" = 1 ] || [ -z "$hot" ] || sim_cfg_valid "$hot" || return 1
    if [ "$slot_present" = 0 ] && [ -n "$slot" ]; then
        uci set "network.wan.sim_slot=$slot" || { sim_cfg_unstage_added $touched; return 1; }
        touched="sim_slot"
    fi
    if [ "$hot_present" = 0 ] && [ -n "$hot" ]; then
        uci set "network.wan.sim_hot=$hot" || { sim_cfg_unstage_added $touched; return 1; }
        touched="$touched sim_hot"
    fi
    # An explicit empty APN is also a user choice; copy the old shared profile only into absent options.
    if [ "$slot" = 1 ]; then
        for key in apn pdptype; do
            if old=$(uci -q get "network.wan.${key}_internal"); then continue; fi
            value=$(uci -q get "network.wan.$key" || true)
            if [ -n "$value" ]; then
                uci set "network.wan.${key}_internal=$value" || { sim_cfg_unstage_added $touched; return 1; }
                touched="$touched ${key}_internal"
            fi
        done
    fi
    if [ -n "$touched" ] && ! uci commit network; then
        sim_cfg_unstage_added $touched
        return 1
    fi
    for old in "$sim_config_root/etc/mu300-sim-slot" "$sim_config_root/etc/mu300-sim-hot"; do
        [ -e "$old" ] || continue
        mkdir -p "$sim_config_root/etc/mu300/sim-migration" || return 1
        destination=$sim_config_root/etc/mu300/sim-migration/${old##*/}.migrated
        [ ! -e "$destination" ] || destination=$destination.$(date +%s).$$
        mv "$old" "$destination" || return 1
    done
}

# --- which cards have had mobile data under Linux (U30 Air dual SIM) ---
# /etc/mu300/sim-data-seen: one stored slot number (0 or 1) per line, root's alone; it holds no identity. A card is
# written here the first time netifd has an address on its bearer. The boot check (mu300-sim2-warmup) reads it: a
# second card that has never had data and gets none within the time is given up for SIM1; one that has had data
# before is never taken away for an outage.
sim_seen_file=$sim_config_root/etc/mu300/sim-data-seen
sim_seen() {
    case "$1" in 0|1) ;; *) return 1 ;; esac
    grep -qx "$1" "$sim_seen_file" 2>/dev/null
}
sim_seen_add() {
    local tmp
    case "$1" in 0|1) ;; *) return 1 ;; esac
    if sim_seen "$1"; then return 0; fi
    mkdir -p "${sim_seen_file%/*}" || return 1
    tmp=$(umask 077; mktemp "$sim_seen_file.XXXXXX") || return 1
    { grep -x '[01]' "$sim_seen_file" 2>/dev/null || true; printf '%s\n' "$1"; } > "$tmp" &&
        mv -f "$tmp" "$sim_seen_file" || { rm -f "$tmp"; return 1; }
}
# sim_cfg_data_wait: the seconds a second card chosen for the boot has to get its first data
# (network.wan.sim_data_wait, 300 by default; 0: never give it up). Anything else is the default.
sim_cfg_data_wait() {
    local wait
    wait=$(uci -q get network.wan.sim_data_wait 2>/dev/null || true)
    case $wait in ''|*[!0-9]*|??????*) wait=300 ;; esac
    printf '%s\n' "$wait"
}
# sim_cfg_auto_restart: 1 unless network.wan.sim_restart is '0'. The dual-SIM path restarts the device by itself in
# the two cases where nothing else brings the data back: a radio left in an unknown state, and a second card that
# was given up after the radio had been told to start it. With 0 those stay as they are until somebody reboots.
sim_cfg_auto_restart() {
    if [ "$(uci -q get network.wan.sim_restart 2>/dev/null || true)" = 0 ]; then echo 0; else echo 1; fi
}
# sim_wan_up BEARER: netifd has the wan up, on BEARER, with an address. Reads netifd's status; no AT command.
sim_wan_up() {
    local st
    st=$(ubus call network.interface.wan status 2>/dev/null | tr -d ' \n\t') || return 1
    case $st in *'"up":true'*) ;; *) return 1 ;; esac
    case $st in *\"l3_device\":\"$1\"*|*\"device\":\"$1\"*) ;; *) return 1 ;; esac
    case $st in *'"ipv4-address":[{'*|*'"ipv6-address":[{'*) return 0 ;; esac
    return 1
}
# sim_wan_held_down: somebody took the wan down and it stays down until somebody brings it up (ifdown, the page's
# button): netifd's "autostart" is false then. Not the marker mobile-data leaves (/run/mu300-mobile-data-down): every
# failed dial of netifd's own goes through a teardown that leaves it for a moment (measured: a boot check that
# looked in that moment took a card netifd was dialling every minute for one nobody was dialling).
sim_wan_held_down() {
    local st
    st=$(ubus call network.interface.wan status 2>/dev/null | tr -d ' \n\t') || return 1
    case $st in *'"autostart":false'*) return 0 ;; esac
    return 1
}
# The data route. AT+SPSWDATA tells the modem which card carries packet data, and whether the modem forgets that
# at a restart was never measured. So the dual-SIM path leaves this mark before it gives the route to the second
# card, and the SIM1 dial (mobile-data) asks the modem, puts the route back and removes the mark. Without the mark
# nothing asks and nothing is sent: a device that never used the second card sees none of it.
sim_route_mark=$sim_config_root/etc/mu300/sim-data-route-2
sim_route_mark_set() { mkdir -p "${sim_route_mark%/*}" && : > "$sim_route_mark"; }
