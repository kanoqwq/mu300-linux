#!/bin/sh
# Keep netifd's complete view of a cellular bearer current without polling.
#
# IPv6 is supplied by RA, so address and route netlink notifications are the
# authoritative source (including lifetime refreshes). IPv4 and peer DNS are
# supplied by CGCONTRDP; nr0 announces PDP changes with +CGEV, at which point a
# state/address check refreshes them. This process is owned by netifd via
# proto_run_command and also implements the standard WAN renew signal (USR1).
#
# Every update is sent address-external (matching the setup in mu300cell.sh):
# the kernel state belongs to RA and to this monitor, netifd only tracks it. A
# report that flips the flag makes netifd delete the address it previously
# installed, so the monitor never manages what setup did not also send external.
#
# netifd scans every executable *.sh in this directory as a protocol provider
# and probes each one with an EMPTY proto name: argv is '<script> "" dump'
# (a direct '<script> dump' from a shell also happens). This helper is not a
# protocol, so every such probe must terminate at once - anything slower blocks
# netifd's scan, and falling through would start a monitor with no config that
# sits on a fifo forever. Real invocations pass 'wan sipa_eth0 ...'.
case "${1:-}-${2:-}" in
	*-dump|dump-*) exit 0 ;;
esac

. /lib/functions.sh
. /lib/netifd/netifd-proto.sh

export PATH="$PATH:/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/sbin"

. "${MU300_PDP_ADDRESS_LIB:-/opt/mu300/lib/pdp-address.sh}"

config=$1
ifname=$2
ip4=$3
prefix4=$4
dns1=$5
dns2=$6
peerdns=$7
urclog=${MU300_URC_LOG:-${10:-${MU300_SIM_URC:-/run/mu300-at/urc/stty_nr0.log}}}
sim_slot=${9:-${MU300_SIM_SLOT:-0}}
case "$sim_slot" in 0|1) ;; *) exit 1 ;; esac
logtag=mu300cell-renew

# One record per address: CIDR|preferred seconds|valid seconds. Including the
# lifetimes in the snapshot means an RA refresh of an unchanged address is
# still forwarded to netifd instead of being mistaken for a duplicate event.
# (The program ends right at the rule's closing brace: an extra one to "end"
# the program is a syntax error - the cause of empty v6 reports before.)
current_v6() {
	ip -6 addr show dev "$ifname" scope global 2>/dev/null |
		awk '
			/^[[:space:]]*inet6 / { cidr=$2; next }
			/^[[:space:]]*valid_lft / && cidr!="" {
				valid=$2; preferred=$4
				gsub(/sec$/, "", preferred); gsub(/sec$/, "", valid)
				if (preferred=="forever") preferred=""
				if (valid=="forever") valid=""
				print cidr "|" preferred "|" valid
				cidr=""
			}' | sort
}

# gateway|remaining lifetime. The route is refreshed from RTM_NEWROUTE just as
# the address is refreshed from RTM_NEWADDR, so netifd never turns an expiring
# RA default into a permanent route.
current_v6_route() {
	ip -6 route show dev "$ifname" default proto ra 2>/dev/null |
		awk 'NR==1 {
			gw=""; valid=""
			for (i=1; i<=NF; i++) {
				if ($i=="via") gw=$(i+1)
				if ($i=="expires") valid=$(i+1)
			}
			gsub(/sec$/, "", valid)
			print gw "|" valid
		}'
}

v6_snapshot() {
	printf '%s\n--route--\n%s\n' "$(current_v6)" "$(current_v6_route)"
}

report() {
	local v6 route cidr addr prefix preferred valid gw
	v6=$(current_v6)
	route=$(current_v6_route)
	# The kernel owns SLAAC/RA lifetimes, and this monitor owns bearer IPv4.
	# Mark the report external so netifd records it without replacing a finite
	# SLAAC lifetime with a permanent static address or route.
	proto_init_update "$ifname" 1 1
	# A complete external snapshot replaces old IP/DNS but leaves finite
	# SLAAC lifetimes and the kernel routes under their existing owners.
	proto_set_keep 0
	[ -n "$ip4" ] && {
		proto_add_ipv4_address "$ip4" "${prefix4:-32}"
		proto_add_ipv4_route "0.0.0.0" 0
	}
	if [ -n "$v6" ]; then
		while IFS='|' read -r cidr preferred valid; do
			[ -n "$cidr" ] || continue
			addr=${cidr%/*}; prefix=${cidr#*/}
			proto_add_ipv6_address "$addr" "${prefix:-64}" "$preferred" "$valid"
		done <<-EOF
		$v6
		EOF
	fi
	if [ -n "$route" ]; then
		gw=${route%%|*}; valid=${route#*|}
		proto_add_ipv6_route "::" 0 "$gw" 4096 "$valid"
	fi
	if [ "${peerdns:-1}" != 0 ]; then
		[ -n "$dns1" ] && proto_add_dns_server "$dns1"
		[ -n "$dns2" ] && proto_add_dns_server "$dns2"
	fi
	proto_send_update "$config"
}

read_bearer() {
	local d out
	local channels='/run/mu300-at6 /run/mu300-at7 /run/mu300-at'
	[ "$sim_slot" != 1 ] || channels=/run/mu300-at4
	for d in $channels; do
		[ -p "$d/cmd" ] || continue
		out=$(MU300_AT_LOCK_WAIT=0 MU300_AT_DIR="$d" mu300-at -t 3 "AT+SPACTCARD=$sim_slot;+CGCONTRDP=1" 2>/dev/null) || continue
		if pdp_address_parse "$out" 1; then
			BEARER_IP4=$PDP_IP4; BEARER_PREFIX4=$PDP_PREFIX4
			BEARER_DNS1=$PDP_DNS1; BEARER_DNS2=$PDP_DNS2
			return 0
		fi
	done
	return 1
}

# Only explicit inactivity is evidence to reconnect. AT owners can be busy
# while moving between cells; a timeout/empty response must keep WAN intact.
context_active() {
	local d out
	local channels='/run/mu300-at6 /run/mu300-at7 /run/mu300-at'
	[ "$sim_slot" != 1 ] || channels=/run/mu300-at4
	for d in $channels; do
		[ -p "$d/cmd" ] || continue
		out=$(MU300_AT_LOCK_WAIT=0 MU300_AT_DIR="$d" mu300-at -t 3 "AT+SPACTCARD=$sim_slot;+CGACT?" 2>/dev/null) || continue
		printf '%s\n' "$out" | tr -d '\r' |
			grep -Eq '\+CGACT:[[:space:]]*1,1([^0-9]|$)' && return 0
		printf '%s\n' "$out" | tr -d '\r' |
			grep -Eq '\+CGACT:[[:space:]]*1,0([^0-9]|$)' && return 1
	done
	return 2
}

refresh_bearer() {
	local reason=$1 old old_ip4 old_prefix4 old_dns1 old_dns2 state try kernel_addresses
	old="$ip4/$prefix4 $dns1 $dns2"
	old_ip4=$ip4; old_prefix4=$prefix4
	old_dns1=$dns1; old_dns2=$dns2
	context_active
	state=$?
	try=0
	while [ "$state" = 1 ] && [ "$try" -lt 3 ]; do
		sleep 2
		try=$((try + 1))
		context_active
		state=$?
	done
	if [ "$state" = 1 ]; then
		logger -t "$logtag" "$reason: PDP context 1 remains inactive; requesting netifd recovery"
		return 2
	fi
	# A failed state read or an active context with no fresh address does not
	# justify disrupting the connection, including stale CGCONTRDP replies.
	if [ "$state" != 0 ] || ! read_bearer; then
		logger -t "$logtag" "$reason: PDP active address is not available; keeping the last netifd state"
		return 1
	fi
	ip4=$BEARER_IP4; prefix4=$BEARER_PREFIX4
	dns1=$BEARER_DNS1; dns2=$BEARER_DNS2
	kernel_addresses=$(ip -4 -o addr show dev "$ifname" scope global 2>/dev/null | awk '{print $4}')
	if [ "$old" != "$ip4/$prefix4 $dns1 $dns2" ] ||
		{ [ -n "$ip4" ] && ! printf '%s\n' "$kernel_addresses" | grep -Fxq "$ip4/$prefix4"; } ||
		{ [ -z "$ip4" ] && [ -n "$kernel_addresses" ]; }; then
		# Add the replacement before deleting the old address. There is no
		# ifdown, CGACT cycle or USB event, so an address handover only loses
		# packets during the carrier's own transition.
		local applied=0
		if [ -z "$ip4" ] || { { ip -4 addr add "$ip4/$prefix4" dev "$ifname" 2>/dev/null ||
			ip -4 addr replace "$ip4/$prefix4" dev "$ifname"; } &&
			ip -4 route replace default dev "$ifname"; }; then
			report && applied=1
		fi
		if [ "$applied" != 1 ]; then
			ip4=$old_ip4; prefix4=$old_prefix4
			dns1=$old_dns1; dns2=$old_dns2
			logger -t "$logtag" "$reason: update failed; keeping previous snapshot for retry"
			return 1
		fi
		local a
		for a in $kernel_addresses; do
			[ -n "$ip4" ] && [ "$a" = "$ip4/$prefix4" ] ||
				ip -4 addr del "$a" dev "$ifname" 2>/dev/null || true
		done
		[ -n "$ip4" ] || ip -4 route del default dev "$ifname" 2>/dev/null || true
		logger -t "$logtag" "$reason: bearer updated to $ip4/$prefix4"
	else
		logger -t "$logtag" "$reason: bearer unchanged"
	fi
}

# Retain an unresolved event across busy/failed AT reads. This deadline exists
# only while that event is pending, never as an idle polling timer. Absolute
# uptime deadlines prevent unrelated IPv6 events from postponing the retry.
retry_due=0 retry_step=0 MONITOR_NOW=0
monitor_clock() {
	local up rest
	IFS=' ' read -r up rest < /proc/uptime || return 1
	MONITOR_NOW=${up%.*}
}
refresh_event() {
	local result=0 delay
	refresh_bearer "$1" || result=$?
	case $result in
		0) retry_due=0; retry_step=0 ;;
		1)
			case $retry_step in 0) delay=1;; 1) delay=3;; 2) delay=5;; *) delay=7;; esac
			retry_step=$(((retry_step + 1) % 4))
			monitor_clock || return 1
			retry_due=$((MONITOR_NOW + delay))
			;;
	esac
	return "$result"
}
read_event() {
	event=
	[ "$retry_due" != 0 ] || { IFS= read -r event <&3; return $?; }
	while :; do
		monitor_clock || return 1
		if [ "$MONITOR_NOW" -ge "$retry_due" ]; then event=pdp-retry; return 0; fi
		IFS= read -r -t "$((retry_due - MONITOR_NOW))" event <&3 && return 0
		# A renew signal interrupts read before the deadline. Let the main
		# loop handle it, retaining this pending event until a confirmed read.
		[ "$renew_pending" != 1 ] || return 1
	done
}

last_v6=$(v6_snapshot)
report
[ "${8:-}" = --once ] && exit 0

# Kill processes a previous monitor left behind (never in --once test mode:
# that must not disturb the running instance). netifd's teardown signal does
# not reach pipeline members, so every re-setup that missed the kill left an
# event farm behind. The ps snapshot goes through a file: an environment
# prefix does not cross a pipe, and awk -v would otherwise match the awk line
# in a piped ps.
reap() {
	local pat=$1 pids pid
	ps w > /tmp/mu300cell-reap.ps
	pids=$(awk -v pat="$pat" 'NR>1 && index($0, pat) { print $1 }' /tmp/mu300cell-reap.ps)
	rm -f /tmp/mu300cell-reap.ps
	for pid in $pids; do
		[ "$pid" != "$$" ] && kill "$pid" 2>/dev/null
	done
}
reap "mu300cell-v6.sh $config $ifname"
reap "ip -6 monitor address dev $ifname"
reap "ip -6 monitor route dev $ifname"
reap "tail -n 0 -F $urclog"
reap "ubus -S listen mu300.modem.urc mu300cell-$config"

events=/run/mu300cell-${config}.events
pdp_pending=$events.pdp
rm -f "$events" "$pdp_pending"
mkfifo -m 600 "$events" || exit 1
exec 3<>"$events"

# Each producer blocks while idle. There is no periodic AT or netlink polling.
( ip -6 monitor address dev "$ifname" 2>/dev/null |
	while IFS= read -r _; do printf 'v6\n'; done > "$events" ) & addrmon=$!
( ip -6 monitor route dev "$ifname" 2>/dev/null |
	while IFS= read -r _; do printf 'v6\n'; done > "$events" ) & routemon=$!
( # NR0 is the shared unsolicited source. Physical bank identity does not
    # identify the event's SIM. The existing owner publishes the hint; only
    # this monitor's SPACTCARD-scoped CID check can decide to reconnect.
    ubus -S listen mu300.modem.urc "mu300cell-$config" |
	while IFS= read -r line; do
		case $line in
			# Besides standard PDN/MODIFY reports, Unisoc emits abbreviated
			# "NW ACT <p_cid>,<cid>" and +SPPCODATA (PCO updates).
			# All are hints to perform one coalesced CGCONTRDP refresh.
			*+CGEV:*PDN*|*+CGEV:*ACT*|*+CGEV:*DETACH*|*+CGEV:*MODIFY*|*+SPPCODATA:*|*NO\ CARRIER*)
				# One queued hint covers the entire burst. A hint arriving during
				# an AT read queues another refresh, so cooldown never loses it.
				[ -e "$pdp_pending" ] || { : >"$pdp_pending"; printf 'pdp\n'; }
				;;
		esac
	done > "$events" ) & urcmon=$!

renew_pending=0
cleanup() {
	local rc=$1
	trap - INT TERM EXIT USR1
	kill "$addrmon" "$routemon" "$urcmon" 2>/dev/null
	# Killing a subshell does not kill the pipeline it feeds; reap the
	# monitor and tail processes by command line so nothing survives us.
	reap "ip -6 monitor address dev $ifname"
	reap "ip -6 monitor route dev $ifname"
	reap "tail -n 0 -F $urclog"
	reap "ubus -S listen mu300.modem.urc mu300cell-$config"
	exec 3>&-
	rm -f "$events" "$pdp_pending"
	exit "$rc"
}
trap 'cleanup 0' INT TERM
trap 'cleanup $?' EXIT
trap 'renew_pending=1' USR1

# Close the setup/subscription gap with one scoped state read. There is no
# timer: all subsequent refreshes are driven by modem/netlink/renew events.
: >"$pdp_pending"; printf 'pdp\n' >&3
last_pdp=0
while :; do
	if [ "$renew_pending" = 1 ]; then
		renew_pending=0
		refresh_event manual-renew
		[ "$?" != 2 ] || exit 2
		last_pdp=$(date +%s)
	fi
	event=
	# A USR1 (renew) interrupts the read builtin: it returns an error without
	# consuming fifo data. Continue, not break - leaving the loop would kill
	# the protocol task and netifd would tear the interface down. This fd is
	# an O_RDWR fifo, so a real EOF cannot occur.
	read_event || continue
	case $event in
		v6)
			now=$(v6_snapshot)
			[ "$now" = "$last_v6" ] && continue
			report || continue
			last_v6=$now
			logger -t "$logtag" "IPv6 RA address/route state refreshed"
			;;
		pdp|pdp-retry)
			now=$(date +%s)
			gap=$((now - last_pdp))
			# A backward NTP correction must not turn cooldown into a long stall.
			[ "$gap" -ge 0 ] || gap=0
			[ "$gap" -ge 2 ] || sleep "$((2 - gap))"
			# Networks commonly emit DEACT plus several ACT notifications as one
			# transition. Let the burst settle, then perform exactly one AT read.
			sleep 1
			rm -f "$pdp_pending"
			refresh_event cgev
			[ "$?" != 2 ] || exit 2
			last_pdp=$(date +%s)
			;;
	esac
done
