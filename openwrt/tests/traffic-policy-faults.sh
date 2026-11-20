#!/bin/sh
# Real ucode/uci/ubus/flock in a disposable container; modem adapter is a stub.
set -eux
test ! -S /var/run/ubus/ubus.sock
ucode -e 'let start=time(); assert(system(["/bin/sh","-c","sleep 5"],50)==-9); assert(time()-start<3);'
base=/tmp/policy-faults
mkdir -p "$base/lib" "$base/disk" "$base/ram" /var/run/ubus /etc/config
export MU300_TRAFFIC_DIR="$base/disk" MU300_TRAFFIC_RUN="$base/ram"
printf 'config main main\n option sim_slots 2\n option data_sim 1\n option data_interface wan\n' > /etc/config/unisoc_modem
cp "$TRAFFIC_TEST_LIB/traffic-policy" "$TRAFFIC_TEST_LIB/traffic-policy.uc" "$base/lib/"
if [ -f "$TRAFFIC_TEST_LIB/sim-scope.sh" ]; then cp "$TRAFFIC_TEST_LIB/sim-scope.sh" "$base/lib/"; fi
printf '{}\n' > "$base/disk/policy.json"
cat > "$base/lib/lock" <<'SH'
#!/bin/sh
printf '%s|%s|%s|%s\n' "$1" "${MU300_SIM_SLOT:-missing}" "${MU300_SCOPED_SLOT:-unset}" "${MU300_AT_DIR:-unset}" >> /tmp/policy-faults/restarts
sleep 2
mode=$(cat /tmp/policy-faults/mode 2>/dev/null || true)
case "$mode" in
    text) printf 'OK\n' ;;
    deferred) printf 'RECONNECT-DEFERRED\n' ;;
    error) printf 'adapter_failed\n' >&2; exit 1 ;;
    *) printf '{"ok":1,"reconnect":"done"}\n' ;;
esac
SH
chmod 755 "$base/lib/lock"
ubusd > "$base/bus.log" 2>&1 &
bus_pid=$!
cleanup() {
    code=$?
    if [ "$code" -ne 0 ]; then
        for file in "$base"/*.json "$base/disk/policy-state.json"; do
            [ ! -f "$file" ] || { printf '%s\n' "$file"; cat "$file"; }
        done
    fi
    kill "${worker_pid:-$bus_pid}" "${wan_pid:-$bus_pid}" "$bus_pid" 2>/dev/null || true
}
trap cleanup EXIT
for i in $(seq 1 10); do [ ! -S /var/run/ubus/ubus.sock ] || break; sleep 1; done
cat > "$base/wan.uc" <<'UC'
import * as fs from 'fs'; import * as uloop from 'uloop'; import * as ubus from 'ubus';
uloop.init(); let bus=ubus.connect(), state={up:true,autostart:true,pending:false};
let object=bus.publish('network.interface.wan',{
    status:{call:()=>state},
    down:{call:()=>{state={up:false,autostart:false,pending:false};return {};}},
    up:{call:()=>{state={up:true,autostart:true,pending:false};return {};}}
});
assert(object);fs.writefile('/tmp/policy-faults/ready','ready');uloop.run();
UC
start_wan() {
    rm -f "$base/ready"
    ucode "$base/wan.uc" > "$base/wan.log" 2>&1 & wan_pid=$!
    for i in $(seq 1 10); do [ ! -f "$base/ready" ] || return 0; sleep 1; done
    cat "$base/wan.log"; exit 1
}
run() { ucode "$base/lib/traffic-policy" "$@"; }
start_wan
# Existing adapter receives slot 1 after inherited scope/AT paths are removed.
MU300_SCOPED_SLOT=0 MU300_AT_DIR=/wrong MU300_DASH_DIR=/wrong ucode "$base/lib/traffic-policy" tick force > "$base/first.json" & worker_pid=$!
for i in $(seq 1 10); do [ ! -s "$base/restarts" ] || break; sleep 1; done
run tick force > "$base/busy.json" && exit 1
jsonfilter -i "$base/busy.json" -e '@.error' | grep -qx operation_busy
printf '{"confirm":true}' | run run > "$base/manual-busy.json" && exit 1
jsonfilter -i "$base/manual-busy.json" -e '@.error' | grep -qx operation_busy
wait "$worker_pid"
test "$(wc -l < "$base/restarts")" -eq 1
if [ -f "$base/lib/sim-scope.sh" ]; then
    grep -qx 'restart|1|1|/run/mu300-at4' "$base/restarts"
else
    grep -qx 'restart|1|unset|unset' "$base/restarts"
fi
jsonfilter -i "$base/disk/policy-state.json" -e '@.last.result' | grep -qx done
# Concurrent partial saves retain both SIMs and the existing schema.
printf '{"slot":"0","config":{"quota":{"enabled":false,"limit_gb":2}}}' | run set > "$base/save0.json" & save0=$!
printf '{"slot":"1","config":{"quota":{"enabled":false,"limit_gb":8.5}}}' | run set > "$base/save1.json" & save1=$!
wait "$save0"; wait "$save1"
jsonfilter -i "$base/save0.json" -e '@.ok' | grep -qx 1
jsonfilter -i "$base/save1.json" -e '@.ok' | grep -qx 1
jsonfilter -i "$base/disk/policy.json" -e '@.slots["0"].limit_gb' | grep -qx 2
jsonfilter -i "$base/disk/policy.json" -e '@.slots["1"].limit_gb' | grep -qx 8.5
sleep 1
# Kill an in-flight worker after the durable running marker, then recover.
ucode "$base/lib/traffic-policy" tick force > "$base/interrupted.json" & worker_pid=$!
for i in $(seq 1 10); do [ "$(wc -l < "$base/restarts")" -lt 2 ] || break; sleep 1; done
kill -KILL "$worker_pid"; wait "$worker_pid" 2>/dev/null || true
sleep 3
run tick
jsonfilter -i "$base/disk/policy-state.json" -e '@.last.detail' | grep -qx interrupted
test "$(wc -l < "$base/restarts")" -eq 2
# A failed ownership write must not stop the WAN.
printf '{}\n' > "$base/disk/policy-state.json"
printf '{"slots":{"1":{"enabled":true,"limit_gb":1}}}\n' > "$base/disk/policy.json"
printf '{"clock_ok":true,"cycle_used":2000000000}\n' > "$base/ram/view-sim1.json"
mkdir "$base/disk/policy-state.json.new"
run tick > "$base/storage.json" && exit 1
jsonfilter -i "$base/storage.json" -e '@.error' | grep -qx storage_failed
ubus call network.interface.wan status | jsonfilter -e '@.up' | grep -qx true
rmdir "$base/disk/policy-state.json.new"
# Disappearing/recreated netifd object reports an error and recovers on retry.
printf '{}\n' > "$base/disk/policy.json"
kill "$wan_pid"; wait "$wan_pid" 2>/dev/null || true
run tick > "$base/offline.json" && exit 1
jsonfilter -i "$base/offline.json" -e '@.error' | grep -qx operation_failed
start_wan
run tick
test -z "$(jsonfilter -i "$base/ram/policy-status.json" -e '@.error')"
printf 'invalid json\n' > "$base/disk/policy.json"
run tick > "$base/corrupt.json" && exit 1
ubus call network.interface.wan status | jsonfilter -e '@.up' | grep -qx true
printf '{}\n' > "$base/disk/policy.json"
for mode in text deferred error; do
    printf '%s\n' "$mode" > "$base/mode"
    run tick force
    case "$mode" in text) expected=done;; deferred) expected=radio_off;; error) expected=failed;; esac
    jsonfilter -i "$base/disk/policy-state.json" -e '@.last.result' | grep -qx "$expected"
    test ! -e "$base/ram/policy-restart-output"
done
echo 'PASS native locks, concurrent saves, slot owner, interruption recovery, storage failure and ubus recovery'
