#!/bin/sh
# Isolated fake sysfs and firmware gate. Never loads a module or adjusts voltage.
set -eu
lib=$1
base=$(mktemp -d /tmp/mu300-voltage-test.XXXXXX)
trap 'rm -rf -- "$base"' EXIT HUP INT TERM
printf 'f50\n' > "$base/device"
export MU300_CPU_DEVICE_FILE="$base/device"
export MU300_CPU_VOLTAGE_DIR=$base/etc MU300_CPU_VOLTAGE_RUN=$base/run
export MU300_CPU_SYS=$base/sys MU300_CPU_BOOT_ID=$base/boot
export MU300_CPU_PLATFORM=$base/platform
mkdir -p "$base/etc" "$base/run" "$base/sys"
printf 'boot-one\n' > "$base/boot"
printf '#!/bin/sh\ntest ! -f "%s/unsupported"\n' "$base" > "$base/platform"
chmod 755 "$base/platform"
for d in 0 1 2; do
    dir=$base/sys/policy$d; mkdir -p "$dir"
    printf '%s\n' "$d" > "$dir/scaling_voltage_domain"
    printf '0\n' > "$dir/scaling_voltage_offset"
    printf '%s\n' "$d" > "$dir/affected_cpus"
    printf '1000000 750000\n1500000 850000\n' > "$dir/scaling_voltage_table"
done
call() { ucode "$lib" "$@"; }
check() { jsonfilter -s "$1" -e "$2"; }
out=$(call get)
test "$(check "$out" '@.supported')" = true
test "$(check "$out" '@.domains[0].table[0].uv')" = 750000
for bad in '[]' '[1,0,0]' '[-53125,0,0]' '[28125,0,0]' '[0,0]' '["0",0,0]' '[0,0,0,0]' '[true,0,0]' '[0.5,0,0]'; do
    if printf '{"offsets":%s}' "$bad" | call save > "$base/out"; then echo 'accepted invalid offsets'; exit 1; fi
done
test ! -e "$base/etc/cpu-voltage.json"
out=$(printf '{"offsets":[-3125,0,6250]}' | call save)
test "$(check "$out" '@.pending')" = true
test "$(call boot-args)" = '-3125,0,6250'
test -f "$base/etc/cpu-voltage-armed.json"
# Repeating startup cannot apply an offset twice.
test "$(call boot-args)" = '0,0,0'
# Readback must match all domains before startup is confirmed.
if call confirm > "$base/out"; then echo 'confirmed wrong voltage'; exit 1; fi
test "$(check "$(cat "$base/etc/cpu-voltage-armed.json")" '@.phase')" = pending
printf '%s\n' -3125 > "$base/sys/policy0/scaling_voltage_offset"
printf '%s\n' 6250 > "$base/sys/policy2/scaling_voltage_offset"
call confirm > "$base/out"
test "$(check "$(cat "$base/etc/cpu-voltage-armed.json")" '@.phase')" = confirmed
test "$(call boot-args)" = '0,0,0'
# F50/V50 unplug: no shutdown action is invoked, and settings survive.
printf 'boot-two\n' > "$base/boot"
test "$(call boot-args)" = '-3125,0,6250'
# Even a subsequent power loss before S99 keeps this already confirmed profile.
printf 'boot-three\n' > "$base/boot"
test "$(call boot-args)" = '-3125,0,6250'
# A new, unconfirmed profile is paused after an interrupted first boot.
printf '{"offsets":[0,3125,0]}' | call save > "$base/out"
printf 'boot-four\n' > "$base/boot"
test "$(call boot-args)" = '0,3125,0'
printf 'boot-five\n' > "$base/boot"
for d in 0 1 2; do printf '0\n' > "$base/sys/policy$d/scaling_voltage_offset"; done
test "$(call boot-args)" = '0,0,0'
test -f "$base/etc/cpu-voltage.json"
test ! -e "$base/etc/cpu-voltage.json.rejected"
out=$(call get)
test "$(check "$out" '@.recovery')" = boot_unconfirmed
# Further power loss cannot retry the paused profile without explicit saving.
printf 'boot-six\n' > "$base/boot"
test "$(call boot-args)" = '0,0,0'
call shutdown > "$base/out"
printf 'boot-seven\n' > "$base/boot"
test "$(call boot-args)" = '0,0,0'
# Explicit retry is accepted only after observed default-voltage boot.
printf '%s\n' -3125 > "$base/sys/policy0/scaling_voltage_offset"
printf '{"offsets":[0,3125,0]}' | call save > "$base/out"
test "$(check "$(cat "$base/etc/cpu-voltage-armed.json")" '@.phase')" = paused
printf '0\n' > "$base/sys/policy0/scaling_voltage_offset"
printf '{"offsets":[0,3125,0]}' | call save > "$base/out"
test ! -e "$base/etc/cpu-voltage-armed.json"
test "$(call boot-args)" = '0,3125,0'
printf '3125\n' > "$base/sys/policy1/scaling_voltage_offset"
call shutdown > "$base/out"
test "$(check "$(cat "$base/etc/cpu-voltage-armed.json")" '@.phase')" = confirmed
printf 'boot-eight\n' > "$base/boot"
test "$(call boot-args)" = '0,3125,0'
# Pending save of different offsets is not discarded with an older trial.
printf '{"offsets":[3125,0,0]}' | call save > "$base/out"
printf 'boot-nine\n' > "$base/boot"
test "$(call boot-args)" = '3125,0,0'
printf '{"offsets":[6250,0,0]}' | call save > "$base/out"
printf 'boot-ten\n' > "$base/boot"
test "$(call boot-args)" = '6250,0,0'
# Legacy lifetime guard migrates without declaring an unplug to be a crash.
printf '{"boot":"old-version","offsets":[6250,0,0]}' > "$base/etc/cpu-voltage-armed.json"
printf 'boot-eleven\n' > "$base/boot"
test "$(call boot-args)" = '6250,0,0'
printf '6250\n' > "$base/sys/policy0/scaling_voltage_offset"
printf '0\n' > "$base/sys/policy1/scaling_voltage_offset"
call confirm > "$base/out"
# Saving factory values while an offset runs preserves its evidence until boot.
printf '{"offsets":[0,0,0]}' | call save > "$base/out"
test -f "$base/etc/cpu-voltage-armed.json"
printf 'boot-defaults\n' > "$base/boot"
test "$(call boot-args)" = '0,0,0'
test ! -e "$base/etc/cpu-voltage-armed.json"
printf '{"offsets":[6250,0,0]}' | call save > "$base/out"
test "$(call boot-args)" = '6250,0,0'
# Corrupt records fail closed without erasing the saved user profile.
printf '{bad' > "$base/etc/cpu-voltage-armed.json"
printf 'boot-twelve\n' > "$base/boot"
test "$(call boot-args)" = '0,0,0'
test "$(check "$(call get)" '@.recovery')" = invalid_boot_record
test -f "$base/etc/cpu-voltage.json"
for d in 0 1 2; do printf '0\n' > "$base/sys/policy$d/scaling_voltage_offset"; done
printf '{"offsets":[0,0,0]}' | call save > "$base/out"
test ! -e "$base/etc/cpu-voltage-armed.json"
# Unknown firmware may reset to defaults, but may not save/apply an offset.
touch "$base/unsupported"
if printf '{"offsets":[-3125,0,0]}' | call save > "$base/out"; then exit 1; fi
test "$(call boot-args)" = '0,0,0'
printf '{"offsets":[0,0,0]}' | call save > "$base/out"
# Missing/duplicate domain interfaces are unsupported, never fabricate controls.
rm "$base/unsupported" "$base/sys/policy2/scaling_voltage_domain"
if printf '{"offsets":[0,0,3125]}' | call save > "$base/out"; then exit 1; fi
test ! -e "$base/etc/cpu-voltage-armed.json"
echo "PASS voltage validation/gate/confirmed unplug/unfinished trial/manual retry/legacy recovery: $base"
