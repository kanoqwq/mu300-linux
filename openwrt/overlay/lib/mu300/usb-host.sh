#!/bin/sh
# MU300: the USB host's address on the LAN, sourced by preinit (06_mu300_early_usb) and uci-defaults (90-mu300) so
# both read the LAN alike and give the host the same one. .200 of the router's /24 when that is inside the LAN subnet and not the router
# itself (.199 then); in a smaller subnet (a /25 without .200), the last host address that is not the router.
mu300_ip2int() {  # mu300_ip2int A.B.C.D
	set -- $(echo "$1" | tr . ' ')
	echo $(( ($1 << 24) | ($2 << 16) | ($3 << 8) | $4 ))
}
mu300_int2ip() {
	echo "$(( ($1 >> 24) & 255 )).$(( ($1 >> 16) & 255 )).$(( ($1 >> 8) & 255 )).$(( $1 & 255 ))"
}
mu300_prefix2mask() {  # mu300_prefix2mask 8..30
	mu300_int2ip $(( (0xffffffff << (32 - $1)) & 0xffffffff ))
}
mu300_usb_host_ip() {  # mu300_usb_host_ip ROUTER NETMASK
	_r=$(mu300_ip2int "$1"); _m=$(mu300_ip2int "$2")
	_net=$(( _r & _m )); _bc=$(( _net | (~_m & 0xffffffff) ))
	_h=$(( (_r & 0xffffff00) | 200 ))
	[ $_h -eq $_r ] && _h=$(( _h - 1 ))
	if [ $_h -le $_net ] || [ $_h -ge $_bc ]; then
		_h=$(( _bc - 1 ))
		[ $_h -eq $_r ] && _h=$(( _bc - 2 ))
	fi
	mu300_int2ip $_h
}
# mu300_lan IPADDR NETMASK: the LAN as netifd will set it from network.lan's ipaddr (A.B.C.D or A.B.C.D/N) and
# netmask, in mu300_lan_addr and mu300_lan_mask; mu300_lan_addr is empty when IPADDR is no address (the caller takes
# the device's default then), and the mask a /24 when neither gives one
mu300_lan() {
	_c=${1%% *}
	mu300_lan_addr=${_c%/*}
	mu300_lan_mask=255.255.255.0
	case $mu300_lan_addr in
		*.*.*.*)
			case $_c in
				*/*) _p=${_c#*/}
				     case $_p in 8|9|[12][0-9]|30) mu300_lan_mask=$(mu300_prefix2mask "$_p") ;; esac ;;
				*) case $2 in *.*.*.*) mu300_lan_mask=$2 ;; esac ;;
			esac ;;
		*) mu300_lan_addr= ;;
	esac
}
