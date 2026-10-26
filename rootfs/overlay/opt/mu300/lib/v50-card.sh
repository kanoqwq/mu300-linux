# The ZTE V50's SIM cards (docs/FINDINGS.md 204; sourced by mu300-sim). From kanoqwq's v50-card.sh (f0ed60ff), who
# read the four board variants and the card IDs from ZTE's B15 firmware (ElfDeviceManager, the telephony framework).
#
# A card here is ZTE's physical card ID, not one of the modem's two SIM interfaces (slots):
#   card 0  built-in SIM 1   slot 0   psim_select 0
#   card 1  built-in SIM 2   slot 1   chip_select 0
#   card 2  built-in SIM 3   slot 1   chip_select 1
#   card 3  external SIM     slot 0   psim_select 1
# Which cards a V50 has is its board_id (ZTE's /proc/driver/board_id, androidboot.zte_boardid); an unknown one is no
# V50 we know, and nothing is guessed for it.

# v50_board_id: 0-3 on stdout, or fail
v50_board_id() {
    local root=${MU300_SYSROOT:-} value token path
    if [ -r "$root/proc/driver/board_id" ]; then
        value=$(cat "$root/proc/driver/board_id")
        case "$value" in 0|1|2|3) printf '%s\n' "$value"; return 0;; esac
        return 1
    fi
    # the mainline kernel's own command line has none of androidboot.*; the bootloader's is in the device tree
    for path in "$root/proc/cmdline" "$root/proc/device-tree/chosen/bootargs" \
                "$root/sys/firmware/devicetree/base/chosen/bootargs"; do
        [ -r "$path" ] || continue
        for token in $(tr '\000' ' ' < "$path"); do
            case "$token" in androidboot.zte_boardid=*)
                value=${token#*=}
                case "$value" in 0|1|2|3) printf '%s\n' "$value"; return 0;; esac
                return 1;;
            esac
        done
    done
    return 1
}

# v50_cards BOARD: the card IDs that board has
v50_cards() {
    case "$1" in
        0) echo '0 1 2';;       # three built-in cards
        1) echo '0 1 2 3';;     # three built-in cards and the external card
        2) echo '0 1 3';;       # two built-in cards and the external card
        3) echo '0 1';;         # two built-in cards
        *) return 1;;
    esac
}

# v50_card_map BOARD CARD: "SLOT ATTRIBUTE VALUE" (the modem's SIM interface, the switch, its level), or fail
v50_card_map() {
    local card
    for card in $(v50_cards "$1"); do
        [ "$card" = "$2" ] || continue
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

# v50_active_card SLOT CHIP PSIM: the card that slot reaches with the switches at those levels
v50_active_card() {
    case "$1:$2:$3" in
        0:*:0) echo 0;; 0:*:1) echo 3;;
        1:0:*) echo 1;; 1:1:*) echo 2;;
        *) return 1;;
    esac
}

# v50_mux: the driver's directory (zte-v50-sim.c), or fail
v50_mux() {
    local d
    if [ -n "${MU300_V50_MUX:-}" ]; then
        [ -r "$MU300_V50_MUX/chip_select" ] && [ -r "$MU300_V50_MUX/psim_select" ] || return 1
        printf '%s\n' "$MU300_V50_MUX"; return 0
    fi
    for d in "${MU300_SYSROOT:-}"/sys/bus/platform/drivers/zte-v50-sim/*/; do
        [ -r "${d}chip_select" ] && [ -r "${d}psim_select" ] || continue
        printf '%s\n' "${d%/}"; return 0
    done
    return 1
}
