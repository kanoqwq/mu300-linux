# The 5 GHz channels the hotspot may use (sourced by hotspot-start, openwrt-wifi-config and mu300-wifi-band; POSIX sh).
# The SC2355 firmware runs a 5 GHz access point on 36-48 and 149-165 only (FINDINGS 23), and the driver has no radar
# detection (no start_radar_detection, FINDINGS 42): hostapd cannot start an AP on a DFS channel (52-64, 100-144 and,
# in a few countries, also 36-48 or 149-165) - start_dfs_cac() fails and the AP stays down. So the hotspot never goes
# onto a DFS channel: what these functions offer is the firmware's channels a country allows without DFS and without
# NO-IR, from wireless-regdb (db.txt of 2026; the countries whose list differs from the full one are named below).
WIFI_AP5_ALL='36 40 44 48 149 153 157 161 165'

# wifi_ap5_channels CC: the channels for country CC (empty: none, the hotspot has to use 2.4 GHz)
wifi_ap5_channels() {
    case $(printf '%s' "${1:-}" | tr a-z A-Z) in
        # no 5 GHz access point without DFS at all (00 is the world domain: NO-IR everywhere)
        00|AM|BH|DZ|TG|YE) echo '' ;;
        # 149-165 not allowed, DFS or NO-IR
        AF|AI|AN|AW|AZ|BL|BT|BW|BY|EG|ET|FO|GE|GF|GI|GL|GP|IM|IR|JP|KE|KH|KW|LK|LS|MA|MF|MQ|MR|MW|NA|OM|PF|PM|QA|RE|\
        RS|SA|SM|SR|SX|SY|TD|TN|TR|TZ|UZ|VA|VC|WF|WS|YT|ZW) echo '36 40 44 48' ;;
        # 36-48 not allowed, DFS or NO-IR
        BD|BO|BZ|CU|EC|NG|PK) echo '149 153 157 161 165' ;;
        # no 165
        ID|KN|KP|LC) echo '36 40 44 48 149 153 157 161' ;;
        *) echo "$WIFI_AP5_ALL" ;;
    esac
}

# wifi_ap5_dfs CH: CH is a DFS channel in every country (52-64, 100-144)
wifi_ap5_dfs() {
    case ${1:-} in
        52|56|60|64|100|104|108|112|116|120|124|128|132|136|140|144) return 0 ;;
    esac
    return 1
}

# wifi_ap5_allowed CC CH: CH is one of CC's channels
wifi_ap5_allowed() {
    case " $(wifi_ap5_channels "$1") " in *" ${2:-x} "*) return 0 ;; esac
    return 1
}

# wifi_ap5_default CC: 36, else 149, else the first channel of CC (empty: none)
wifi_ap5_default() {
    _l=$(wifi_ap5_channels "$1")
    for _c in 36 149 $_l; do
        case " $_l " in *" $_c "*) echo "$_c"; return 0 ;; esac
    done
    echo ''
}

# wifi_ap5_resolve CC CH: the channel to use for CH ("auto", empty or a number). An explicit channel CC does not
# allow (a DFS one, or one outside the firmware's list) is replaced by the default; the reason goes to stderr in one
# line. Prints nothing when CC has no 5 GHz channel at all.
wifi_ap5_resolve() {
    _d=$(wifi_ap5_default "$1")
    case ${2:-} in
        ''|auto|0) echo "$_d"; return 0 ;;
    esac
    if wifi_ap5_allowed "$1" "$2"; then echo "$2"; return 0; fi
    if wifi_ap5_dfs "$2"; then
        _why="a DFS channel (radar detection), which this Wi-Fi driver cannot do"
    else
        _why="not available to the hotspot in country ${1:-?}"
    fi
    if [ -n "$_d" ]; then
        echo "hotspot: 5 GHz channel $2 is $_why: using channel $_d" >&2
    else
        echo "hotspot: 5 GHz channel $2 is $_why, and country ${1:-?} has no 5 GHz channel without DFS" >&2
    fi
    echo "$_d"
}

# wifi_ap5_width CC CH: the widest channel width in MHz (80, 40 or 20) whose channels CC all allows: 80 needs the
# block of four (36-48, 149-161), 40 the pair (36+40, 44+48, 149+153, 157+161); 165 is 20 MHz only.
wifi_ap5_width() {
    case $2 in
        36|40|44|48) _b='36 40 44 48' ;;
        149|153|157|161) _b='149 153 157 161' ;;
        *) echo 20; return 0 ;;
    esac
    case $2 in 36|40) _p='36 40' ;; 44|48) _p='44 48' ;; 149|153) _p='149 153' ;; *) _p='157 161' ;; esac
    _w=80
    for _c in $_b; do wifi_ap5_allowed "$1" "$_c" || _w=40; done
    if [ $_w = 40 ]; then
        for _c in $_p; do wifi_ap5_allowed "$1" "$_c" || _w=20; done
    fi
    echo $_w
}
