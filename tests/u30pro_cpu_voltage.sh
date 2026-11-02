#!/bin/sh
# Exercise the shared backend with isolated UMS9632 policies; no hardware writes.
set -eu
lib=$1
base=$(mktemp -d /tmp/u30pro-voltage-test.XXXXXX)
trap 'rm -rf -- "$base"' EXIT HUP INT TERM
mkdir -p "$base/etc" "$base/run" "$base/sys"
printf 'u30pro\n' > "$base/device"; printf 'first\n' > "$base/boot"
printf '#!/bin/sh\n[ ! -e "%s/unsupported" ]\n' "$base" > "$base/platform"
chmod 755 "$base/platform"
export MU300_CPU_DEVICE_FILE=$base/device MU300_CPU_SYS=$base/sys
export MU300_CPU_BOOT_ID=$base/boot MU300_CPU_PLATFORM=$base/platform
export MU300_CPU_VOLTAGE_DIR=$base/etc MU300_CPU_VOLTAGE_RUN=$base/run
for spec in '0 0' '6 1'; do
    set -- $spec; p=$base/sys/policy$1; mkdir "$p"
    printf '%s\n' "$2" > "$p/scaling_voltage_domain"
    printf '0\n' > "$p/scaling_voltage_offset"
    printf '%s\n' "$1" > "$p/related_cpus"
    printf '768000 750000\n2041000 800000\n' > "$p/scaling_voltage_table"
done
call() { ucode "$lib" "$@"; }
field() { jsonfilter -s "$1" -e "$2"; }
state=$(call get)
[ "$(field "$state" '@.supported')" = true ]
[ "$(field "$state" '@.domain_count')" = 2 ]
[ "$(field "$state" '@.domains[1].policy')" = policy6 ]
for offsets in '[]' '[0,0,0]' '[1,0]' '[0]' '[true,0]' '[0,-53125]' '[28125,0]'; do
    if printf '{"offsets":%s}' "$offsets" | call save >/dev/null; then echo 'accepted invalid UMS9632 offsets'; exit 1; fi
done
printf '{"offsets":[-3125,6250]}' | call save >/dev/null
[ "$(call boot-args)" = '-3125,6250' ]
[ "$(call boot-args)" = '0,0' ]
if call confirm >/dev/null; then echo 'confirmed mismatched offsets'; exit 1; fi
printf '%s\n' -3125 > "$base/sys/policy0/scaling_voltage_offset"
printf '6250\n' > "$base/sys/policy6/scaling_voltage_offset"
call confirm >/dev/null
[ "$(field "$(cat "$base/etc/cpu-voltage-armed.json")" '@.phase')" = confirmed ]
printf 'second\n' > "$base/boot"
[ "$(call boot-args)" = '-3125,6250' ]
# A new unfinished profile pauses after a further boot without losing user intent.
printf '{"offsets":[0,3125]}' | call save >/dev/null
printf 'third\n' > "$base/boot"
[ "$(call boot-args)" = '0,3125' ]
printf 'fourth\n' > "$base/boot"
printf '0\n' > "$base/sys/policy0/scaling_voltage_offset"
printf '0\n' > "$base/sys/policy6/scaling_voltage_offset"
[ "$(call boot-args)" = '0,0' ]
[ "$(field "$(call get)" '@.recovery')" = boot_unconfirmed ]
printf '{"offsets":[0,3125]}' | call save >/dev/null
[ "$(call boot-args)" = '0,3125' ]
printf '3125\n' > "$base/sys/policy6/scaling_voltage_offset"
call shutdown >/dev/null
printf '{"offsets":[0,0]}' | call save >/dev/null
printf 'defaults\n' > "$base/boot"
[ "$(call boot-args)" = '0,0' ]
[ ! -e "$base/etc/cpu-voltage-armed.json" ]
touch "$base/unsupported"
if printf '{"offsets":[0,3125]}' | call save >/dev/null; then echo 'accepted unsupported firmware'; exit 1; fi
printf '{"offsets":[0,0]}' | call save >/dev/null
printf 'PASS two native domains, real policy IDs, profile validation, boot confirmation, interrupted trial/retry, defaults and firmware gate\n'
