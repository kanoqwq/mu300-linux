#!/bin/sh
# Disposable native OpenWrt container ONLY: no host network, NET_ADMIN required.
# Mount the repository read-only at /repo. No physical interfaces or modem.
set -eu
[ "${MU300_RA_TEST:-}" = isolated-container ] || exit 2
script=/repo/openwrt/overlay/opt/mu300/bin/ndp-learn
eval "$(sed '/^case ${1:-} in/,$d' "$script")"
# Real kernel routes/neighbours; record only the RA sends for state assertions.
uci() { echo relay; }
logger() { :; }
ra_calls=0
record_ra() {
    [ "$*" = 'br-lan 1800 2001:db8:0:0::/64' ] || return 1
    ra_calls=$((ra_calls + 1))
}
RA_DEPRECATE=record_ra
ip link add sipa_eth0 type dummy
ip link add br-lan type dummy
ip link set sipa_eth0 up
ip link set br-lan up
ip -6 addr add 2001:db8::1/64 dev sipa_eth0 nodad noprefixroute
ip -6 addr add 2408:1:2:4::1/64 dev sipa_eth0 nodad noprefixroute
ip -6 route add default dev sipa_eth0 metric 100
ip -6 route add 2001:db8::99/128 dev sipa_eth0
ip -6 route add 2408:1:2:4::99/128 dev sipa_eth0
ip -6 neigh add 2001:db8::99 lladdr 02:00:00:00:00:01 nud permanent dev br-lan
ip -6 neigh add 2408:1:2:4::99 lladdr 02:00:00:00:00:02 nud permanent dev br-lan

learn_once
[ "$ra_calls" = 0 ]
ip -6 route get 2001:db8::123 | grep -q 'dev br-lan'
ip -6 route get 2408:1:2:4::123 | grep -q 'dev br-lan'
ip -6 route get 2001:db8::99 | grep -q 'dev br-lan'
ip -6 route get 2408:1:2:4::99 | grep -q 'dev br-lan'

# Missing route is repaired next round, without reannouncing or losing the other.
ip -6 route del 2408:1:2:4::/64 dev br-lan metric 128
learn_once
ip -6 route get 2408:1:2:4::123 | grep -q 'dev br-lan'
[ "$ra_calls" = 0 ]

# The old prefix and pinned host route must go even with its neighbour retained.
ip -6 addr del 2001:db8::1/64 dev sipa_eth0
learn_once
[ "$ra_calls" = 1 ]
! ip -6 route show dev br-lan | grep -q '2001:db8:'
ip -6 route get 2408:1:2:4::99 | grep -q 'dev br-lan'
learn_once
learn_once
learn_once
[ "$ra_calls" = 3 ]

# Transient loss and return of the same /64 never sends a withdrawal.
ip -6 addr del 2408:1:2:4::1/64 dev sipa_eth0
learn_once
! ip -6 route show dev br-lan | grep -q '2408:1:2:4:'
ip -6 addr add 2408:1:2:4::1/64 dev sipa_eth0 nodad noprefixroute
learn_once
[ "$ra_calls" = 3 ]
ip -6 route get 2408:1:2:4::123 | grep -q 'dev br-lan'
echo 'IPv6 multi-prefix runtime checks passed (isolated dummy interfaces only)'
