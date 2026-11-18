#!/bin/sh
# Run only in the disposable OpenWrt test container (no device mounts).
set -eu
test ! -e /usr/bin/python3
test ! -S /var/run/ubus/ubus.sock
test -f "${TRAFFIC_TEST_LIB:?}/traffic"
mkdir -p /tmp/policy-fixture/disk /tmp/policy-fixture/ram /tmp/policy-fixture/net /var/run/ubus
printf '{}\n' > /tmp/policy-fixture/disk/policy.json
cat > /usr/bin/python3 <<'SH'
#!/bin/sh
printf '%s\n' "$@" > /tmp/policy-fixture/worker-args
printf '%s\n' "${MU300_TRAFFIC_DIR:-not-inherited}" > /tmp/policy-fixture/worker-env
SH
chmod 755 /usr/bin/python3
ubusd >/tmp/policy-fixture/ubus.log 2>&1 &
bus_pid=$!
trap 'kill "${daemon_pid:-$bus_pid}" "$bus_pid" 2>/dev/null || true' EXIT
for i in $(seq 1 6); do [ ! -S /var/run/ubus/ubus.sock ] || break; sleep 1; done
MU300_TRAFFIC_DIR=/tmp/policy-fixture/disk MU300_TRAFFIC_RUN=/tmp/policy-fixture/ram \
MU300_TRAFFIC_SYS=/tmp/policy-fixture/net MU300_TRAFFIC_SIM_SLOTS=2 \
MU300_TRAFFIC_DEVICES='["test0","test1"]' \
ucode "$TRAFFIC_TEST_LIB/traffic" >/tmp/policy-fixture/daemon.log 2>&1 &
daemon_pid=$!
for i in $(seq 1 6); do [ ! -f /tmp/policy-fixture/worker-args ] || break; sleep 1; done
test -f /tmp/policy-fixture/worker-args || { cat /tmp/policy-fixture/daemon.log; exit 1; }
test "$(head -n 1 /tmp/policy-fixture/worker-args)" = /usr/libexec/unisoc-modem/traffic-policy
test "$(tail -n 1 /tmp/policy-fixture/worker-args)" = tick
test "$(cat /tmp/policy-fixture/worker-env)" = /tmp/policy-fixture/disk
ubus -t 3 call unisoc.traffic get '{}' >/dev/null
echo 'PASS actual uloop child dispatch, environment inheritance and responsive traffic RPC'
