#!/bin/sh
# Stock V50 B15 ElfDeviceManager constants, not modem logical slot numbers.
v50_detected_board_id() {
    local root=${MU300_SYSROOT:-} value token path
    if [ -r "$root/proc/driver/board_id" ]; then
        value=$(cat "$root/proc/driver/board_id")
        case "$value" in 0|1|2|3) printf '%s\n' "$value"; return;; esac
        return 1
    fi
    for path in "$root/proc/cmdline" "$root/proc/device-tree/chosen/bootargs" \
                "$root/sys/firmware/devicetree/base/chosen/bootargs"; do
        [ -r "$path" ] || continue
        for token in $(tr '\000' ' ' < "$path"); do
            case "$token" in androidboot.zte_boardid=*)
                value=${token#*=}
                case "$value" in 0|1|2|3) printf '%s\n' "$value"; return;; esac
                return 1;;
            esac
        done
    done
    return 1
}
v50_board_id() {
    local value marker=${MU300_V50_RUN:-/run}/mu300-product-form-v50
    if [ -r "$marker" ]; then value=$(cat "$marker");
    else value=$(uci -q get unisoc_modem.main.v50_board_override 2>/dev/null || true); fi
    case $value in
        ''|auto) v50_detected_board_id;;
        0|1|2|3) printf '%s\n' "$value";;
        *) return 1;;
    esac
}

v50_cards() {
    case "$1" in
        0) echo '0 1 2';; # three chip cards
        1) echo '0 1 2 3';; # three chip cards and a physical card
        2) echo '0 1 3';; # two chip cards and a physical card
        3) echo '0 1';; # two chip cards
        *) return 1;;
    esac
}

v50_card_map() {
    local card
    for card in $(v50_cards "$1"); do
        [ "$card" = "$2" ] || continue
        # Output: logical modem slot, selector attribute, selector value.
        case "$card" in
            0) echo '0 psim_select 0';;
            1) echo '1 chip_select 0';;
            2) echo '1 chip_select 1';;
            3) echo '0 psim_select 1';;
        esac
        return 0
    done
    return 1
}

v50_active_card() {
    case "$1:$2:$3" in
        0:*:0) echo 0;; 0:*:1) echo 3;;
        1:0:*) echo 1;; 1:1:*) echo 2;;
        *) return 1;;
    esac
}
