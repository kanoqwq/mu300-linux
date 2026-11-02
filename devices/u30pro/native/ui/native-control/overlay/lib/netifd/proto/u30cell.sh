#!/bin/sh
# netifd protocol "u30cell": the cellular data sessions of the U30 Pro.  The sessions themselves (modem, PDP contexts) are run by
# u30pro-celld (ubus object u30pro.cell); when one comes up it brings its netifd interface up (wan for the internet context, cell_<name>
# for an additional one, option extra 1), and this handler asks u30pro-celld for the address and the DNS servers of that context
# (ubus call u30pro.cell status).  It hands them to netifd (/32, point to point, the default route unless option defaultroute 0 - the
# default of an additional context), so that LuCI, the firewall and the resolver see the link like any other WAN.
[ -n "$INCLUDE_ONLY" ] || {
	. /lib/functions.sh
	. ../netifd-proto.sh
	init_proto "$@"
}

proto_u30cell_init_config() {
	no_device=1
	available=1
	proto_config_add_string "device"
	proto_config_add_string "ifname"
	proto_config_add_int "metric"
	proto_config_add_int "mtu"
	proto_config_add_boolean "extra"
	proto_config_add_boolean "defaultroute"
}

proto_u30cell_setup() {
	local interface="$1"
	local device ifname metric mtu extra defaultroute ip dns status sel

	json_get_vars device ifname metric mtu extra defaultroute
	[ -n "$device" ] && ifname=$device
	[ -n "$ifname" ] || ifname=sipa_eth0
	[ -n "$metric" ] || metric=100
	status=$(ubus -t 5 call u30pro.cell status 2>/dev/null)
	if [ "$extra" = 1 ]; then
		sel="@.contexts[@.interface='$interface']"
		[ -n "$defaultroute" ] || defaultroute=0
	else
		sel="@"
		[ -n "$defaultroute" ] || defaultroute=1
	fi
	[ "$(echo "$status" | jsonfilter -e "$sel.connected" 2>/dev/null)" = true ] || status=
	ip=$(echo "$status" | jsonfilter -e "$sel.address" 2>/dev/null)
	dns=$(echo "$status" | jsonfilter -e "$sel.dns[*]" 2>/dev/null)
	if [ -z "$ip" ]; then
		proto_notify_error "$interface" NO_DATA_SESSION
		proto_block_restart "$interface"
		return 1
	fi

	ip link set dev "$ifname" up 2>/dev/null
	[ -n "$mtu" ] && ip link set dev "$ifname" mtu "$mtu" 2>/dev/null

	proto_init_update "$ifname" 1
	proto_add_ipv4_address "$ip" 32
	[ "$defaultroute" = 0 ] || proto_add_ipv4_route 0.0.0.0 0 "" "" "$metric"
	for dns in $dns; do
		proto_add_dns_server "$dns"
		# the carrier's resolvers stay reachable through the cellular link even when another uplink (Wi-Fi relay) holds the default route
		case "$dns" in *:*) ;; *) proto_add_ipv4_route "$dns" 32 "" "" "$metric" ;; esac
	done
	proto_send_update "$interface"
}

proto_u30cell_teardown() {
	local interface="$1"

	proto_init_update "*" 0
	proto_send_update "$interface"
}

add_protocol u30cell
