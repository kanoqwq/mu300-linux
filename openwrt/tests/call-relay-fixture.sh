#!/bin/sh
# Disposable container only: real ubus registration and event framing.
set -e
base=$(mktemp -d)
mkdir -p /var/run/ubus
mkfifo "$base/events"
ubusd -s /var/run/ubus/ubus.sock &
daemon=$!
trap 'kill "$relay" "$daemon" 2>/dev/null || :; rm -rf "$base"' EXIT
# Only this disposable fixture waits for its freshly spawned daemon's socket.
# Production subscription readiness follows the real ubus registration ACKs.
n=0
while [ ! -S /var/run/ubus/ubus.sock ]; do
    n=$((n+1)); [ "$n" -lt 5 ] || exit 1
    sleep 1
done
ucode "$1" > "$base/events" &
relay=$!
exec 3< "$base/events"
read -r -t 5 line <&3
[ "$(printf '%s' "$line" | jsonfilter -e '@.ready')" = true ]
ubus send mu300.modem.urc '{"channel":"stty_nr0","line":"RING"}'
read -r -t 5 line <&3
[ "$(printf '%s' "$line" | jsonfilter -e '@["mu300.modem.urc"].channel')" = stty_nr0 ]
[ "$(printf '%s' "$line" | jsonfilter -e '@["mu300.modem.urc"].line')" = RING ]
ubus send u30pro.panel.request '{"request":"call_refresh"}'
read -r -t 5 line <&3
[ "$(printf '%s' "$line" | jsonfilter -e '@["u30pro.panel.request"].request')" = call_refresh ]
ubus send unisoc.forward.changed '{}'
read -r -t 5 line <&3
printf '%s' "$line" | jsonfilter -e '@["unisoc.forward.changed"]' >/dev/null
echo 'PASS registered ubus subscriptions, initial readiness, first incoming event and control/config events'
