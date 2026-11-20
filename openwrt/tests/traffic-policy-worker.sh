#!/bin/sh
# Disposable native OpenWrt container only, with no hardware/network mounts.
set -eu
test ! -e /usr/bin/python3
test ! -S /var/run/ubus/ubus.sock
base=/tmp/policy-fixture
mkdir -p "$base/disk" "$base/ram" "$base/net" /var/run/ubus /usr/libexec/unisoc-modem /etc/config
printf 'config main main\n option sim_slots 2\n option data_sim 0\n' > /etc/config/unisoc_modem
printf '{}\n' > "$base/disk/policy.json"
export MU300_TRAFFIC_DIR="$base/disk" MU300_TRAFFIC_RUN="$base/ram"
export MU300_DASH_BIN="$TRAFFIC_TEST_LIB" MU300_DASH_DIR="$base/dash" MU300_SMS_POOL_LIB="$base/pool.sh"
printf 'mu300_sms_pool() { echo /tmp/test-sms-pool; }\n' > "$MU300_SMS_POOL_LIB"
# Exercise the actual interpreter, imports, RPC metadata and config validation.
printf '{"slot":"1","ubus_rpc_session":"fixture"}' | ucode "$TRAFFIC_TEST_LIB/traffic-policy" get > "$base/get.json"
jsonfilter -i "$base/get.json" -e '@.ok' | grep -qx 1
printf '{"confirm":false}' | ucode "$TRAFFIC_TEST_LIB/traffic-policy" run > "$base/rejected.json" && exit 1
jsonfilter -i "$base/rejected.json" -e '@.error' | grep -qx confirmation_required
printf '{"slot":"0","ubus_rpc_session":"fixture"}' | sh /rpc/mu300dash call traffic_policy_get > "$base/rpc.json"
jsonfilter -i "$base/rpc.json" -e '@.ok' | grep -qx 1
# The child below is a harmless recorder. Accounting must select ucode itself.
cat > /usr/libexec/unisoc-modem/traffic-policy <<'UC'
import * as fs from 'fs';
fs.writefile('/tmp/policy-fixture/worker-args',join('\n',ARGV));
fs.writefile('/tmp/policy-fixture/worker-env',getenv('MU300_TRAFFIC_DIR'));
UC
ubusd > "$base/ubus.log" 2>&1 &
bus_pid=$!
trap 'kill "${daemon_pid:-$bus_pid}" "${wan_pid:-$bus_pid}" "$bus_pid" 2>/dev/null || true' EXIT
for i in $(seq 1 10); do [ ! -S /var/run/ubus/ubus.sock ] || break; sleep 1; done
# Use real libubus calls and ucode file locks, never a real network interface.
cat > "$base/wan.uc" <<'UC'
import * as fs from 'fs';
import * as uloop from 'uloop';
import * as ubus from 'ubus';
uloop.init();
let bus=ubus.connect(), state={up:true,autostart:true,pending:false};
let object=bus.publish('network.interface.wan',{
    status:{call:()=>state},
    down:{call:()=>{state={up:false,autostart:false,pending:false};return {};}},
    up:{call:()=>{state={up:true,autostart:true,pending:false};return {};}}
});
assert(object);fs.writefile('/tmp/policy-fixture/wan-ready','ready');uloop.run();
UC
ucode "$base/wan.uc" > "$base/wan.log" 2>&1 &
wan_pid=$!
for i in $(seq 1 10); do [ ! -f "$base/wan-ready" ] || break; sleep 1; done
test -f "$base/wan-ready"
printf '{"slots":{"0":{"enabled":true,"limit_gb":2}}}\n' > "$base/disk/policy.json"
printf '{"clock_ok":true,"cycle_used":2000000000}\n' > "$base/ram/view-sim0.json"
ucode "$TRAFFIC_TEST_LIB/traffic-policy" tick
ubus call network.interface.wan status | jsonfilter -e '@.up' | grep -qx false
# A disabled saved configuration still releases the WAN that this worker owns.
printf '{"payload":"{\"slot\":\"0\",\"config\":{\"quota\":{\"enabled\":false,\"limit_gb\":2}}}"}' | sh /rpc/mu300dash call traffic_policy_set > "$base/save.json"
jsonfilter -i "$base/save.json" -e '@.ok' | grep -qx 1
for i in $(seq 1 10); do
    ubus call network.interface.wan status | jsonfilter -e '@.up' | grep -qx true && break
    sleep 1
done
ubus call network.interface.wan status | jsonfilter -e '@.up' | grep -qx true
printf '{}\n' > "$base/disk/policy.json"
start() {
    MU300_TRAFFIC_SYS="$base/net" MU300_TRAFFIC_SIM_SLOTS=2 \
    MU300_TRAFFIC_DEVICES='["test0","test1"]' \
    ucode "$TRAFFIC_TEST_LIB/traffic" > "$base/daemon.log" 2>&1 &
    daemon_pid=$!
    for i in $(seq 1 20); do ubus -t 1 call unisoc.traffic get '{}' >/dev/null 2>&1 && return; sleep 1; done
    cat "$base/daemon.log"; exit 1
}
stop() { kill "$daemon_pid"; wait "$daemon_pid"; }
start
sleep 11
test ! -e "$base/worker-args"
# Enable policy at runtime; the next existing accounting sample dispatches it.
printf '{"slots":{"1":{"enabled":true,"limit_gb":2}}}\n' > "$base/disk/policy.json"
for i in $(seq 1 12); do [ ! -f "$base/worker-args" ] || break; sleep 1; done
test -f "$base/worker-args" || { cat "$base/daemon.log"; exit 1; }
test "$(cat "$base/worker-args")" = tick
test "$(cat "$base/worker-env")" = "$base/disk"
ubus -t 2 call unisoc.traffic get '{}' >/dev/null
stop
# All disabled still needs one worker for an owned cutoff or interrupted run.
printf '{}\n' > "$base/disk/policy.json"
for state in '{"stopped":{"boot":"one","iface":"wan"}}' '{"last":{"result":"running"}}'; do
    rm -f "$base/worker-args"
    printf '%s\n' "$state" > "$base/disk/policy-state.json"
    start
    for i in $(seq 1 20); do [ ! -f "$base/worker-args" ] || break; sleep 1; done
    test -f "$base/worker-args"
    stop
done
# Invalid optional policy files report through the worker, not a traffic crash.
rm -f "$base/worker-args"
printf 'invalid json\n' > "$base/disk/policy.json"
start
for i in $(seq 1 20); do [ ! -f "$base/worker-args" ] || break; sleep 1; done
test -f "$base/worker-args"
ubus -t 2 call unisoc.traffic get '{}' >/dev/null
stop
echo 'PASS real ucode RPC, disabled idle timer, runtime enable, recovery dispatch and responsive accounting'
