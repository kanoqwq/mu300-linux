#!/bin/sh
# Keep netifd's complete view of a cellular bearer current without polling.
# The event monitor of mu300cell's relay mode (option ipv6 'relay', openwrt-luci only; K36, spec D9), ported from
# kanoqwq/clean-tf-7.2 (1f5b82a). mu300cell.sh starts it with proto_run_command after its first update:
#   mu300cell-v6.sh CONFIG IFNAME IPV4 PREFIX4 DNS1 DNS2 PEERDNS [--once]
#
# IPv6 is supplied by RA, so address and route netlink notifications are the
# authoritative source (including lifetime refreshes). IPv4 and peer DNS are
# supplied by CGCONTRDP; nr0 announces PDP changes with +CGEV, at which point a
# single read refreshes them. This process is owned by netifd via
# proto_run_command and also implements the standard WAN renew signal (USR1).
# The bearer half follows the active card and believes only a well-formed
# answer (docs/FINDINGS.md 91, after kanoqwq's clean-tf-7.2 3cbf37fc).
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

config=$1
ifname=$2
ip4=$3
prefix4=$4
dns1=$5
dns2=$6
peerdns=$7
SIM_SLOT=0
if [ -x /opt/mu300/bin/mu300-sim ]; then
    SIM_SLOT=$(/opt/mu300/bin/mu300-sim active) || exit 1
fi
# The active card's channels and URC log, chosen as mobile-data and the panel's collector choose them (FINDINGS 74,
# 91). SIM1: the panel pool nr6/nr7, then nr1's daemon. SIM2 pinned at boot: /run/mu300-at is that card's own nr4.
# SIM2 in a dual-SIM boot (/run/mu300-radio-freeze): /run/mu300-at stays SIM1's nr1, SIM2's owner is /run/mu300-at4,
# and its URCs are logged there - the monitor used to read SIM1's context and wait for a log that never appears.
SIM_NATIVE=0
[ ! -e "${MU300_RADIO_FREEZE:-/run/mu300-radio-freeze}" ] || SIM_NATIVE=1
case "$SIM_SLOT:$SIM_NATIVE" in
	0:*) at_dirs='/run/mu300-at6 /run/mu300-at7 /run/mu300-at'; urc_default=/run/mu300-at/urc/stty_nr0.log ;;
	1:0) at_dirs=/run/mu300-at; urc_default=/run/mu300-at/urc/stty_nr3.log ;;
	1:1) at_dirs=/run/mu300-at4; urc_default=/run/mu300-at4/urc/stty_nr3.log ;;
	*) exit 1 ;;
esac
AT_DIRS=${MU300_CELL_AT_DIRS:-$at_dirs}
urclog=${MU300_URC_LOG:-$urc_default}
logtag=mu300cell-renew

# A netmask in dotted form to a prefix length; fails on anything that is not a contiguous mask (the old version
# turned garbage into /32 and an octet after a partial one into a wrong length).
mask2prefix() {
	local m=$1 bits=0 o n=0 partial=0 IFS=.
	for o in $m; do
		n=$((n + 1))
		[ "$partial" = 0 ] || [ "$o" = 0 ] || return 1
		case $o in
			255) bits=$((bits + 8)) ;;
			254) bits=$((bits + 7)); partial=1 ;; 252) bits=$((bits + 6)); partial=1 ;;
			248) bits=$((bits + 5)); partial=1 ;; 240) bits=$((bits + 4)); partial=1 ;;
			224) bits=$((bits + 3)); partial=1 ;; 192) bits=$((bits + 2)); partial=1 ;;
			128) bits=$((bits + 1)); partial=1 ;; 0) partial=1 ;;
			*) return 1 ;;
		esac
	done
	[ "$n" = 4 ] || return 1
	echo "$bits"
}

# A dotted IPv4 address, each part 0-255 without a sign or a leading zero, and not 0.0.0.0. What the modem says
# reaches `ip` and netifd only after this.
valid_ipv4() {
	case $1 in *[!0-9.]*|.*|*.|*..*) return 1 ;; esac
	local IFS=. o n=0
	for o in $1; do
		n=$((n + 1))
		case $o in 0) ;; 0*|????*) return 1 ;; esac
		[ "$o" -le 255 ] || return 1
	done
	[ "$n" = 4 ] && [ "$1" != 0.0.0.0 ]
}

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
	local d out line addrmask mask new_ip4 new_prefix new_dns1 new_dns2
	for d in $AT_DIRS; do
		[ -p "$d/cmd" ] || continue
		out=$(MU300_AT_LOCK_WAIT=0 MU300_AT_DIR="$d" mu300-at -t 3 'AT+CGCONTRDP=1' 2>/dev/null) || continue
		line=$(printf '%s\n' "$out" | awk -F, '/^\+CGCONTRDP:/ {
			a=$4; gsub(/"/, "", a); n=split(a, x, ".")
			if (n==8 && x[1]+0>0) { print; exit }
		}')
		[ -n "$line" ] && break
	done
	[ -n "$line" ] || return 1
	addrmask=$(printf '%s\n' "$line" | cut -d, -f4 | tr -d '"\r ')
	new_ip4=$(printf '%s\n' "$addrmask" | cut -d. -f1-4)
	mask=$(printf '%s\n' "$addrmask" | cut -d. -f5-8)
	new_prefix=$(mask2prefix "$mask") || return 1
	new_dns1=$(printf '%s\n' "$line" | cut -d, -f6 | tr -d '"\r ')
	new_dns2=$(printf '%s\n' "$line" | cut -d, -f7 | tr -d '"\r ')
	# a carrier without a second DNS server sends 0.0.0.0 (an IPv6 one is never on this line's v4 fields)
	[ "$new_dns1" != 0.0.0.0 ] || new_dns1=
	[ "$new_dns2" != 0.0.0.0 ] || new_dns2=
	valid_ipv4 "$new_ip4" || return 1
	[ -z "$new_dns1" ] || valid_ipv4 "$new_dns1" || new_dns1=
	[ -z "$new_dns2" ] || valid_ipv4 "$new_dns2" || new_dns2=
	BEARER_IP4=$new_ip4
	BEARER_PREFIX4=$new_prefix
	BEARER_DNS1=$new_dns1
	BEARER_DNS2=$new_dns2
}

refresh_bearer() {
	local reason=$1 old old_ip4 old_prefix4 old_dns1 old_dns2 have
	old="$ip4/$prefix4 $dns1 $dns2"
	old_ip4=$ip4; old_prefix4=$prefix4; old_dns1=$dns1; old_dns2=$dns2
	if ! read_bearer; then
		logger -t "$logtag" "$reason: PDP active address is not available; keeping the last netifd state"
		return 1
	fi
	ip4=$BEARER_IP4; prefix4=$BEARER_PREFIX4
	dns1=$BEARER_DNS1; dns2=$BEARER_DNS2
	# the kernel can lose the address while the bearer keeps it (after kanoqwq's 3cbf37fc): put it back as well
	have=$(ip -4 -o addr show dev "$ifname" scope global 2>/dev/null | awk '{print $4}')
	if [ "$old" != "$ip4/$prefix4 $dns1 $dns2" ] || ! printf '%s\n' "$have" | grep -Fxq "$ip4/$prefix4"; then
		# Add the replacement before deleting the old address. There is no
		# ifdown, CGACT cycle or USB event, so an address handover only loses
		# packets during the carrier's own transition. A step that fails
		# keeps the previous snapshot, so the next event or renew tries again
		# instead of finding "unchanged".
		if ! { ip -4 addr add "$ip4/$prefix4" dev "$ifname" 2>/dev/null ||
			ip -4 addr replace "$ip4/$prefix4" dev "$ifname"; } ||
			! ip -4 route replace default dev "$ifname"; then
			ip4=$old_ip4; prefix4=$old_prefix4; dns1=$old_dns1; dns2=$old_dns2
			logger -t "$logtag" "$reason: the new address could not be installed; keeping the previous state for a retry"
			return 1
		fi
		if [ -n "$old_ip4" ] && [ "$old_ip4/$old_prefix4" != "$ip4/$prefix4" ]; then
			ip -4 addr del "$old_ip4/${old_prefix4:-32}" dev "$ifname" 2>/dev/null || true
			# the old one was the primary of the new one's subnet: deleting it took the secondary along (no
			# promote_secondaries; the same as mu300cell.sh's setup), so once more, with the route
			ip -4 addr replace "$ip4/$prefix4" dev "$ifname" 2>/dev/null
			ip -4 route replace default dev "$ifname" 2>/dev/null
		fi
		report
		logger -t "$logtag" "$reason: bearer updated to $ip4/$prefix4"
	else
		logger -t "$logtag" "$reason: bearer unchanged"
	fi
}

# tests: the functions only (sourced with MU300CELL_V6_LIB=1)
[ -z "${MU300CELL_V6_LIB:-}" ] || return 0
last_v6=$(v6_snapshot)
report
[ "${8:-}" = --once ] && exit 0

# Kill processes a previous monitor left behind (never in --once test mode:
# that must not disturb the running instance). netifd's teardown signal does
# not reach pipeline members, so every re-setup that missed the kill left an
# event farm behind. The ps snapshot is taken whole before awk starts (in a
# variable, never a file: no fixed path a root process writes through), so
# awk's own line, which carries the pattern in -v, is not in it.
reap() {
	local pat=$1 snap pids pid
	snap=$(ps w)
	pids=$(printf '%s\n' "$snap" | awk -v pat="$pat" 'NR>1 && index($0, pat) { print $1 }')
	for pid in $pids; do
		[ "$pid" != "$$" ] && kill "$pid" 2>/dev/null
	done
}
reap "mu300cell-v6.sh $config $ifname"
reap "ip -6 monitor address dev $ifname"
reap "ip -6 monitor route dev $ifname"
reap "tail -n 0 -F $urclog"

events=/run/mu300cell-${config}.events
# One queued read covers a whole burst of hints: the producer sends "pdp" only when it creates this directory
# (mkdir is atomic and never follows a symlink), the reader removes it right before its AT read.
pending=$events.pdp
rm -rf "$events" "$pending"
mkfifo -m 600 "$events" || exit 1
exec 3<>"$events"

# Each producer blocks while idle. There is no periodic AT or netlink polling.
( ip -6 monitor address dev "$ifname" 2>/dev/null |
	while IFS= read -r _; do printf 'v6\n'; done > "$events" ) & addrmon=$!
( ip -6 monitor route dev "$ifname" 2>/dev/null |
	while IFS= read -r _; do printf 'v6\n'; done > "$events" ) & routemon=$!
( while [ ! -f "$urclog" ]; do sleep 1; done
	tail -n 0 -F "$urclog" 2>/dev/null |
	while IFS= read -r line; do
		case $line in
			# Besides PDN and MODIFY: the short "NW ACT"/"NW DEACT" forms, a
			# detach, and Unisoc's +SPPCODATA (protocol options, where a DNS
			# change arrives). Each is only a hint to read the bearer once
			# (after kanoqwq's clean-tf-7.2 3cbf37fc).
			*+CGEV:*PDN*|*+CGEV:*ACT*|*+CGEV:*DETACH*|*+CGEV:*MODIFY*|*+SPPCODATA:*)
				mkdir -m 700 "$pending" 2>/dev/null && printf 'pdp\n' ;;
		esac
	done > "$events" ) & urcmon=$!

renew_pending=0
cleanup() {
	trap - INT TERM EXIT USR1
	kill "$addrmon" "$routemon" "$urcmon" 2>/dev/null
	# Killing a subshell does not kill the pipeline it feeds; reap the
	# monitor and tail processes by command line so nothing survives us.
	reap "ip -6 monitor address dev $ifname"
	reap "ip -6 monitor route dev $ifname"
	reap "tail -n 0 -F $urclog"
	exec 3>&-
	rm -rf "$events" "$pending"
	exit 0
}
trap cleanup INT TERM EXIT
trap 'renew_pending=1' USR1

last_pdp=0
while :; do
	if [ "$renew_pending" = 1 ]; then
		renew_pending=0
		refresh_bearer manual-renew || true
		last_pdp=$(date +%s)
	fi
	event=
	# A USR1 (renew) interrupts the read builtin: it returns an error without
	# consuming fifo data. Continue, not break - leaving the loop would kill
	# the protocol task and netifd would tear the interface down. This fd is
	# an O_RDWR fifo, so a real EOF cannot occur.
	IFS= read -r event <&3 || continue
	case $event in
		v6)
			now=$(v6_snapshot)
			[ "$now" = "$last_v6" ] && continue
			last_v6=$now
			report
			logger -t "$logtag" "IPv6 RA address/route state refreshed"
			;;
		pdp)
			# A hint inside the 2 s after the last read waits for the rest of
			# that time instead of being dropped: the dropped one could be the
			# last of a burst, the one after which the new address is readable.
			# A clock stepped back never makes this a long wait.
			gap=$(( $(date +%s) - last_pdp ))
			[ "$gap" -ge 0 ] || gap=2
			[ "$gap" -ge 2 ] || sleep $((2 - gap))
			# Networks commonly emit DEACT plus several ACT notifications as one
			# transition. Let the burst settle, then perform exactly one AT read.
			sleep 1
			# hints from here on queue one more read (the producer's flag)
			rmdir "$pending" 2>/dev/null
			refresh_bearer cgev || true
			last_pdp=$(date +%s)
			;;
	esac
done
