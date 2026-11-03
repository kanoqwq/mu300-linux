#!/bin/sh
# Runs inside an isolated target root, with synthetic registration and a real
# private ubus. The production UCI parser, policy and RPC implementation run.
set -eu
ucode /src/tests/roaming.uc
ucode /src/tests/network_recovery.uc
ucode -e "import * as checked from '/usr/share/ucode/mu300/registration.uc';"
for script in /opt/mu300/bin/mu300-modemd /opt/mu300/bin/mu300-network-recovery /usr/libexec/unisoc-modem/roaming; do
 ucode -c -o /dev/null "$script"
done
scratch=$(mktemp -d /tmp/roaming-test.XXXXXX)
buspid=
trap '[ -z "$buspid" ] || kill "$buspid"; rm -rf "$scratch"' EXIT HUP INT TERM
mkdir "$scratch/config" "$scratch/state"
printf "config core 'main'\n option sim_slots '2'\n option data_sim '0'\n" > "$scratch/config/unisoc_modem"
ubusd -s "$scratch/bus.sock" & buspid=$!
export MU300_ROAMING_SOCKET="$scratch/bus.sock"
for n in 1 2 3 4 5 6 7 8 9 10; do [ ! -S "$MU300_ROAMING_SOCKET" ] || break; sleep .1; done
cp /usr/libexec/unisoc-modem/roaming "$scratch/rpc"
export MU300_ROAMING_CONFIG="$scratch/config" MU300_REGISTRATION_DIR="$scratch/state"
rpc(){ printf '%s' "$2" | ucode "$scratch/rpc" "$1"; }
expect(){ [ "$1" = "$2" ] || { printf 'Expected %s, got %s\n' "$2" "$1";exit 1; }; }
result=$(rpc get '{"slot":"0"}');expect "$(printf '%s' "$result" | jsonfilter -e '@.enabled')" 0
printf '{"slot":0,"stat":5,"rat":7}\n' > "$scratch/state/slot0.json"
rc=0;ucode "$scratch/rpc" check 0 || rc=$?;expect "$rc" 8
expect "$(rpc get '{"slot":"0"}' | jsonfilter -e '@.blocked')" 1
expect "$(rpc set '{"slot":"0","enabled":"1"}' | jsonfilter -e '@.enabled')" 1
ucode "$scratch/rpc" check 0
expect "$(rpc get '{"slot":"1"}' | jsonfilter -e '@.enabled')" 0
expect "$(rpc set '{"slot":"0","enabled":"0"}' | jsonfilter -e '@.enabled')" 0
printf '{"slot":0,"stat":1,"rat":7}\n' > "$scratch/state/slot0.json"
ucode "$scratch/rpc" check 0
printf '{"slot":0,"stat":10,"rat":11}\n' > "$scratch/state/slot0-nr.json"
rc=0;ucode "$scratch/rpc" check 0 || rc=$?;expect "$rc" 8
printf '{"slot":1,"stat":1,"rat":11}\n' > "$scratch/state/slot0-nr.json"
ucode "$scratch/rpc" check 0
expect "$(rpc set '{"slot":"0","enabled":"invalid"}' | jsonfilter -e '@.error')" roaming_invalid
expect "$(rpc get '{"slot":"2"}' | jsonfilter -e '@.error')" roaming_invalid
printf 'PASS: target UCI defaults/persistence/SIM isolation/NR selection/invalid input and syntax\n'
ucode -e '
import {cache} from "/usr/share/ucode/mu300/registration.uc";
import * as fs from "fs";
let events=[], root=getenv("MU300_REGISTRATION_DIR"), c=cache(root,(n,d)=>push(events,{n,d}));
c.reply("AT+SPACTCARD=1;+CEREG?;+C5GREG?","+CEREG: 2,1,\"a\",\"b\",7\n+C5GREG: 2,5,\"a\",\"b\",11\nOK");
if(json(fs.readfile(root+"/slot1-nr.json")).stat!=5 || length(events)!=2)die("Scoped NR query was not cached\n");
c.reply("AT+C5GREG?","+C5GREG: 2,1\nOK");
if(length(events)!=2)die("Unscoped query contaminated cache\n");
c.reset(1);if(fs.stat(root+"/slot1-nr.json"))die("NR cache survived reset\n");
print("PASS: scoped EPS/5GS query cache, event isolation and CP reset\n");'
