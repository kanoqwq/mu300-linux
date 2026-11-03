#!/bin/sh
# netifd protocol for the MU300 modem: attach with AT commands (mobile-data) and configure sipa_eth0.
# /etc/config/network:  config interface 'wan' / option proto 'mu300cell' / option apn 'internet'
# LuCI edits the same options through www/luci-static/resources/protocol/mu300cell.js.
#
# The bearer's kernel state is owned here, not by netifd: every update is sent
# address-external, so netifd tracks addresses, routes and DNS for display and
# firewall purposes but never installs or removes them. A mixed mode (netifd
# installing the v4 during setup, the monitor later reporting external) makes
# netifd delete the address it installed the moment the first external report
# arrives - the mobile-data watchdog then saw an address-less WAN and redialled
# every 60 s. mobile-data's netifd path leaves the interface alone; this script
# and mu300cell-v6.sh are the only writers.
[ -n "$INCLUDE_ONLY" ] || {
	. /lib/functions.sh
	. ../netifd-proto.sh
	init_proto "$@"
}

proto_mu300cell_init_config() {
	available=1
	no_device=1
	renew_handler=1
	proto_config_add_string "apn"
	proto_config_add_string "pdptype"
	proto_config_add_string "auth"
	proto_config_add_string "username"
	proto_config_add_string "password"
	proto_config_add_string "roaming"
	proto_config_add_boolean "attach"
	proto_config_add_boolean "peerdns"
	proto_config_add_array "dns:list(ipaddr)"
}

proto_mu300cell_setup() {
	local config="$1"
	local apn pdptype peerdns out ifname ip prefix dns1 dns2 iid zone slot rc auth username password roaming attach
	rm -f "/run/mu300cell-$config.registration-wait"
	json_get_vars apn pdptype peerdns auth username password roaming attach
	. /opt/mu300/bin/mu300-sim-env
	mu300_sim_env || { proto_notify_error "$config" INVALID_SIM; return 1; }
	slot=$MU300_SIM_SLOT
	# Teardown must address the old bearer even if a switch has already saved
	# the next SIM. Keep this runtime ownership separate from persistent intent.
	printf '%s\n%s\n' "$slot" "$MU300_SIM_DEVICE" > "/run/mu300cell-$config.sim"

	printf '{"phase":"connecting"}\n' > "/run/unisoc-apn-dial-$slot.json"
	out=$(MU300_NETIFD=1 MU300_PDP_TYPE="${pdptype:-IP}" MU300_AUTH="${auth:-none}" MU300_USERNAME="$username" MU300_PASSWORD="$password" MU300_ROAMING_PDP_TYPE="${roaming:-${pdptype:-IP}}" MU300_INITIAL_ATTACH="${attach:-0}" /opt/mu300/bin/mobile-data up "$apn" 2>/tmp/mu300cell.err)
	rc=$?
	[ "$rc" = 0 ] || printf '{"phase":"failed"}\n' > "/run/unisoc-apn-dial-$slot.json"
	if [ "$rc" = 7 ]; then
		proto_notify_error "$config" SIM_RADIO_DISABLED
		proto_set_available "$config" 0
		return 1
	fi
	if [ "$rc" = 4 ]; then
		logger -t mu300cell "$(cat /tmp/mu300cell.err)"
		proto_notify_error "$config" DUAL_RADIO_REBOOT_REQUIRED
		# Repeating SFUN against a mixed boot state cannot fix it. Keep LAN
		# available and stop the retry loop until an explicit restart/reboot.
		proto_block_restart "$config"
		return 1
	fi
	if [ "$rc" = 3 ]; then
		logger -t mu300cell "$(cat /tmp/mu300cell.err)"
		proto_notify_error "$config" NO_MODEM
		proto_block_restart "$config"
		return 1
	fi
	if [ "$rc" = 5 ] || [ "$rc" = 6 ] || [ "$rc" = 8 ]; then
		# Out of PS coverage is transient. Keep autostart and wait for the
		# existing owner's registration event, rather than blocking restart.
		printf '%s\n' "$slot" > "/run/mu300cell-$config.registration-wait"
		if [ "$rc" = 8 ]; then proto_notify_error "$config" ROAMING_DISABLED
		elif [ "$rc" = 6 ]; then proto_notify_error "$config" SIM_MISSING
		else proto_notify_error "$config" REGISTRATION_WAIT; fi
		proto_set_available "$config" 0
		return 1
	fi
	ip=$(echo "$out" | sed -n 's/^IP=//p')
	if [ -z "$ip" ]; then
		printf '{"phase":"failed"}\n' > "/run/unisoc-apn-dial-$slot.json"
		logger -t mu300cell "attach failed: $(cat /tmp/mu300cell.err)"
		proto_notify_error "$config" ATTACH_FAILED
		proto_setup_failed "$config"
		return 1
	fi
	ifname=$(echo "$out" | sed -n 's/^IFACE=//p')
	# Per-SIM profiles can override the global protocol passed by netifd.
	local effective_type
	effective_type=$(echo "$out" | sed -n 's/^PDP_TYPE=//p')
	case "$effective_type" in IP|IPV4V6) pdptype=$effective_type ;; esac
	prefix=$(echo "$out" | sed -n 's/^PREFIX=//p')
	dns1=$(echo "$out" | sed -n 's/^DNS1=//p')
	dns2=$(echo "$out" | sed -n 's/^DNS2=//p')
	iid=$(echo "$out" | sed -n 's/^IID6=//p')

	ip link set "$ifname" up
	# This carrier's RAs give INFINITE address lifetimes: after a redial on a
	# new prefix the old SLAAC address never expires and prefixes stack up
	# (same for the default route a previous dial left behind). Clear the last
	# dial's v6 state; RS/RA re-establishes it within ~1 s. Also fine for
	# v4-only bearers - there is nothing to keep either way.
	ip -6 addr flush dev "$ifname" scope global 2>/dev/null
	ip -6 route flush dev "$ifname" 2>/dev/null
	# netifd treats this as a v4 protocol, so forwarding leaves accept_ra at 0. Enable
	# IPv6 and accept RAs before waiting for the carrier address. The monitor below
	# reports the initial RA and all later address/route lifetime refreshes.
	if [ "${pdptype:-IP}" != IP ]; then
		# The 1->0 cycle restarts addrconf entirely: the link-local on-link
		# route is rebuilt (a bare route flush leaves it missing and stops RA
		# processing) and a fresh router solicitation goes out, so the global
		# address returns about a second after the flush above.
		[ -w "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6" ] && {
			echo 1 > "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6"
			echo 0 > "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6"
		}
		[ -w "/proc/sys/net/ipv6/conf/$ifname/accept_ra" ] && {
			echo 0 > "/proc/sys/net/ipv6/conf/$ifname/accept_ra"
			echo 2 > "/proc/sys/net/ipv6/conf/$ifname/accept_ra"
		}
	fi
	# Install the bearer address ourselves (external updates are never applied by
	# netifd). Replace-first keeps an unchanged address continuous across
	# re-setups; anything else the carrier left behind is removed after it.
	ip -4 addr replace "$ip/${prefix:-32}" dev "$ifname" 2>/dev/null ||
		ip -4 addr add "$ip/${prefix:-32}" dev "$ifname"
	local a
	for a in $(ip -4 -o addr show dev "$ifname" scope global | awk '{print $4}'); do
		[ "$a" = "$ip/${prefix:-32}" ] || ip -4 addr del "$a" dev "$ifname" 2>/dev/null
	done
	ip -4 route replace default dev "$ifname"

	proto_init_update "$ifname" 1 1
	proto_add_ipv4_address "$ip" "${prefix:-32}"
	proto_add_ipv4_route "0.0.0.0" 0
	if [ "${peerdns:-1}" != 0 ]; then
		[ -n "$dns1" ] && proto_add_dns_server "$dns1"
		[ -n "$dns2" ] && proto_add_dns_server "$dns2"
	fi
	proto_send_update "$config"
	printf '{"phase":"connected"}\n' > "/run/unisoc-apn-dial-$slot.json"
	# The event-driven child is the protocol task netifd owns. It reports IPv6
	# netlink changes and CGEV-triggered IPv4/DNS changes without holding setup.
	# IPv4-only contexts change address/DNS during mobility too. Keep the
	# same event owner for both PDP families; IPv6 monitoring blocks while idle.
	proto_run_command "$config" \
		/lib/netifd/proto/mu300cell-v6.sh "$config" "$ifname" "$ip" \
		"${prefix:-32}" "$dns1" "$dns2" "${peerdns:-1}" "" "$slot"
	[ "${pdptype:-IP}" = IP ] && [ -w "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6" ] &&
		echo 1 > "/proc/sys/net/ipv6/conf/$ifname/disable_ipv6"
	logger -t mu300cell "connected: $ip/${prefix:-32} on $ifname"
}

proto_mu300cell_renew() {
	# Standard netifd renew: ask the existing event monitor to re-read the
	# bearer. It updates in place; it does not cycle CGACT or restart the link.
	local sigusr1="$(kill -l SIGUSR1)"
	[ -n "$sigusr1" ] && proto_kill_command "$1" "$sigusr1"
}

proto_mu300cell_teardown() {
	local config="$1" slot ifname
	proto_kill_command "$config"
	slot=$(sed -n '1p' "/run/mu300cell-$config.sim" 2>/dev/null)
	ifname=$(sed -n '2p' "/run/mu300cell-$config.sim" 2>/dev/null)
	. /opt/mu300/bin/mu300-sim-env
	mu300_sim_env "$slot" || return 1
	[ -n "$ifname" ] || ifname=$MU300_SIM_DEVICE
	case "$ifname" in *[!a-zA-Z0-9_.:-]*) return 1 ;; esac
	MU300_NETIFD=1 MU300_SIM_SLOT=$MU300_SIM_SLOT /opt/mu300/bin/mobile-data down >/dev/null 2>&1
	# External state is not removed by netifd on ifdown, so clean the bearer
	# ourselves - both families. v6 must go too: this carrier's RAs carry
	# INFINITE lifetimes, so an unflushed SLAAC address (and the default route
	# an earlier report installed) would survive every redial and stack up.
	ip -4 addr flush dev "$ifname" scope global 2>/dev/null
	ip -4 route del default dev "$ifname" 2>/dev/null
	ip -6 addr flush dev "$ifname" scope global 2>/dev/null
	ip -6 route flush dev "$ifname" 2>/dev/null
	rm -f "/run/mu300cell-$config.sim"
}

[ -n "$INCLUDE_ONLY" ] || add_protocol mu300cell
