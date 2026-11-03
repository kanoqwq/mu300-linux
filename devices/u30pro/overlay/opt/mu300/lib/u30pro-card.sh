#!/bin/sh
# MU5358 / MU5358D / MU5358E BoardIdConfigEnum and VendorSubInfoUpdater.
# IDs identify hardware positions, never an operator account or an AT slot.
u30pro_board_id() {
    local root=${MU300_SYSROOT:-} value token path
    if [ -r "$root/proc/driver/board_id" ]; then
        value=$(cat "$root/proc/driver/board_id")
        case $value in 0|1|2|3|4|5|6) echo "$value"; return;; *) return 1;; esac
    fi
    for path in "$root/proc/cmdline" "$root/proc/device-tree/chosen/bootargs"; do
        [ -r "$path" ] || continue
        for token in $(tr '\000' ' ' < "$path"); do
            case $token in androidboot.zte_boardid=*|zteboot.zte_boardid=*)
                value=${token#*=}
                case $value in 0|1|2|3|4|5|6) echo "$value"; return;; *) return 1;; esac;;
            esac
        done
    done
    return 1
}
u30pro_cards() {
    case $1 in
        0|4) echo '0 1 2 3';;
        1) echo '0 1 2';;
        2) echo '0 1';;
        3) echo '0 1 3';;
        5) echo '3';;
        6) echo '0 2';;
        *) return 1;;
    esac
}
u30pro_card_map() {
    local card
    for card in $(u30pro_cards "$1"); do
        [ "$card" = "$2" ] || continue
        case $card in
            0) echo '0 psim_select 0';;
            1) echo '1 chip_select 0';;
            2) echo '1 chip_select 1';;
            3) echo '0 psim_select 1';;
        esac
        return
    done
    return 1
}
u30pro_active_card() {
    case $1:$2:$3 in
        0:*:0) echo 0;; 0:*:1) echo 3;;
        1:0:*) echo 1;; 1:1:*) echo 2;;
        *) return 1;;
    esac
}
u30pro_default_card() {
    u30pro_cards "$1" >/dev/null || return
    case $1 in 5) echo 3;; *) echo 0;; esac
}
u30pro_boot_cards() {
    local primary=0 secondary=1
    u30pro_card_map "$1" "$2" >/dev/null || return
    case $1:$2 in 5:*|*:3) primary=3;; esac
    case $1:$2 in 6:*|*:2) secondary=2;; esac
    echo "$primary"
    [ "$1" = 5 ] || echo "$secondary"
}
