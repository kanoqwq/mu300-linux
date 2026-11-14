#!/bin/sh
# Private container bus only. No device sockets, sysfs or production state.
set -eu
if [ "${TRAFFIC_TEST_STRING_SLOTS:-0}" = 0 ]; then
    # MU300/Pro's shared RPC bootstrap needs its ordinary SIM/SMS library.
    # This is only the disposable container's UCI directory.
    mkdir -p /etc/config
    printf "config modem 'main'\n option sim_slots '2'\n" > /etc/config/unisoc_modem
    export MU300_SMS_POOL_LIB=/repo/rootfs/overlay/opt/mu300/lib/sms-pool.sh
fi
mkdir -p /var/run/ubus
ubusd >/tmp/traffic-test-ubusd.log 2>&1 &
bus_pid=$!
trap 'kill "$bus_pid" 2>/dev/null || true' EXIT HUP INT TERM
for attempt in 1 2 3 4 5; do
    [ ! -S /var/run/ubus/ubus.sock ] || break
    sleep 1
done
ucode "$TRAFFIC_TEST_SCRIPT"
