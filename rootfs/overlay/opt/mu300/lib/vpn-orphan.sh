# Sourced (POSIX sh, busybox ash, bash): is the VPN's kill switch orphaned - wanted on, with nothing left to enforce it?
# The VPN is a module of its own (github.com/dikeckaan/mu300-linux-vpn) and the images carry no mu300-vpn. A device
# whose VPN is on with its kill switch, and which has lost the module (a reinstall without it, a link that failed,
# the extra removed with --force), would otherwise bring its uplinks up in the clear: mobile-data and wifi-client
# refuse instead (fail closed), and the toolkit and the panel say why. mu300-vpn is not there to ask, so its files are
# read directly, the way it reads them itself - with sed, never sourced:
#   ENABLE       /etc/mu300/vpn.conf, the last ENABLE= line, quotes, blanks and a trailing comment dropped (read_enable)
#   KILL_SWITCH  the store's settings (/etc/mu300/vpn/settings, KEY='VALUE') when the store is in use (a profiles
#                directory and a readable settings file), else vpn.conf's legacy line; only 0 turns it off, so a
#                missing line or a value that is not understood counts as on (mu300-vpn's own default)

# vpn_conf_value FILE KEY: the last KEY= line of FILE, unquoted ('...' or "..."), blanks and a trailing comment dropped
vpn_conf_value() {
    [ -r "$1" ] || return 0
    sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n1 | sed "s/[[:space:]]#.*//; s/[\"' ]//g"
}
# vpn_killswitch_wanted [ROOT]: ENABLE=1 and the kill switch on, in the system at ROOT ("" the running one)
vpn_killswitch_wanted() {
    [ "$(vpn_conf_value "${1:-}/etc/mu300/vpn.conf" ENABLE)" = 1 ] || return 1
    if [ -d "${1:-}/etc/mu300/vpn/profiles" ] && [ -r "${1:-}/etc/mu300/vpn/settings" ]; then
        _vks=$(vpn_conf_value "${1:-}/etc/mu300/vpn/settings" KILL_SWITCH)
    else
        _vks=$(vpn_conf_value "${1:-}/etc/mu300/vpn.conf" KILL_SWITCH)
    fi
    [ "$_vks" != 0 ]
}
# vpn_killswitch_orphaned CMD [ROOT]: the VPN is wanted with its kill switch, and CMD (mu300-vpn) is not executable
vpn_killswitch_orphaned() {
    [ ! -x "$1" ] && vpn_killswitch_wanted "${2:-}"
}
VPN_ORPHANED_MSG="the VPN's kill switch is on but the VPN module is missing"

# vpn_engine_missing [ROOT]: the VPN is on in the system at ROOT and its active profile's engine is not installed -
# prints the extra that carries it (vpn-xray, vpn-singbox, vpn-mihomo, vpn-dae), else nothing and 1. The engines are
# extras of their own (MU300_DISK/extra/NAME), or in the vpn extra itself (module v1, the bundle). Unlike a missing
# module this does not keep the uplinks down: mu300-vpn holds the kill switch up and fetches the engine through its
# download window, which needs an uplink. The toolkit and the panel show it as the reason the VPN is not up.
vpn_engine_missing() {
    [ "$(vpn_conf_value "${1:-}/etc/mu300/vpn.conf" ENABLE)" = 1 ] || return 1
    _vea=$(head -n1 "${1:-}/etc/mu300/vpn/active" 2>/dev/null | tr -cd 'a-z0-9-')
    [ -n "$_vea" ] || return 1
    case $(vpn_conf_value "${1:-}/etc/mu300/vpn/profiles/$_vea/meta" TYPE) in
        xray) _vex=vpn-xray _veb=xray ;; sing-box) _vex=vpn-singbox _veb=sing-box ;;
        mihomo) _vex=vpn-mihomo _veb=mihomo ;; dae) _vex=vpn-dae _veb=dae ;; *) return 1 ;;
    esac
    _ved=${MU300_DISK:-/mnt/mu300-disk}/extra
    [ -x "$_ved/$_vex/bin/$_veb" ] || [ -x "$_ved/vpn/bin/$_veb" ] || [ -x "${1:-}/opt/mu300/bin/$_veb" ] && return 1
    echo "$_vex"
}
