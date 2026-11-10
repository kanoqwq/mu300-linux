#!/bin/sh
# Real OpenWrt jshn/configuration/transport dispatch; no network destinations.
set -e
source=$1
base=$(mktemp -d)
trap 'rm -rf "$base"' EXIT
export MU300_FORWARD_DIR=$base/config MU300_FORWARD_RESULT=$base/result MU300_CALL_RUNTIME=$base/run
export MU300_SMS_POOL=$base/pool MU300_SMS_BIN=/bin/false MU300_FORWARD_RUNTIME=$base
export MU300_FORWARD_RENDERER=${source%/*}/forward-template.uc
export MU300_FORWARD_ANSWER=${source%/*}/forward-answer.uc
export MU300_POWER_LIB=${source%/*}/power-supply.sh
mkdir -p "$base/config" "$base/pool/msg" "$base/pool/sim1/msg"
printf '0\n' > "$base/pool/next_id"
printf '0\n' > "$base/pool/sim1/next_id"
sed '/^case "${1:-}" in/,$d' "$source" > "$base/lib.sh"
. "$base/lib.sh"
sed -n '/^deliver() {/,/^}/p' "$source" | sed 's/^deliver()/transport_deliver()/' > "$base/transport.sh"
. "$base/transport.sh"
uci() { case "$*" in *unisoc_modem.main.sim_slots*) echo 2 ;; *) return 1 ;; esac; }
ubus() { :; }
deliver() { printf '%s|%s|%s|%s|%s\n' "$MU300_SIM_SLOT" "$method" "$webhook_url" "$1" "$3" >> "$base/sent"; DELIVER_RESULT=sent; }
fail() { echo "FAIL: $*" >&2; exit 1; }
config() {
    result=$(printf '%s' "$1" | set_config)
    [ "$(printf '%s' "$result" | jsonfilter -e '@.ok')" = 1 ] || fail "save config $result"
}
event() {
    printf '{"id":"%032d","card":%d,"number":"12345","labels":{"zh":"外置卡2","en":"External SIM 2","tr":"Harici SIM 2"}}' "$1" "$2" | process_call_event > "$base/reply"
    [ "$(jsonfilter -i "$base/reply" -e '@.ok')" = 1 ] || fail "call event $(cat "$base/reply")"
}
config '{"enabled":1,"method":"webhook","webhook_url":"https://shared.example.com/","template_language":"en"}'
[ "$(jsonfilter -i "$BASE/config.json" -e '@.call_forward_enabled')" = false ] || fail 'default not disabled'
event 1 0
[ ! -e "$base/sent" ] || fail 'default-off alert'
config '{"enabled":1,"call_forward_enabled":1,"method":"webhook","webhook_url":"https://shared.example.com/","template_language":"en"}'
event 2 1; event 2 1
[ "$(wc -l < "$base/sent")" = 1 ] || fail 'duplicate alert'
grep -q '^1|webhook|https://shared.example.com/|call|External SIM 2: incoming call from 12345$' "$base/sent" || fail 'slot/label/body mismatch'
config '{"enabled":1,"method":"webhook","webhook_url":"https://shared.example.com/"}'
[ "$(jsonfilter -i "$BASE/config.json" -e '@.call_forward_enabled')" = true ] || fail 'older client omitted field cleared saved choice'
config '{"enabled":1,"call_forward_enabled":0,"method":"webhook","webhook_url":"https://shared.example.com/"}'
event 3 1
[ "$(wc -l < "$base/sent")" = 1 ] || fail 'unchecked not saved'
config '{"profile":"sim0","mode":"per_sim","enabled":1,"call_forward_enabled":0,"method":"webhook","webhook_url":"https://first.example.com/"}'
config '{"profile":"sim1","mode":"per_sim","enabled":1,"call_forward_enabled":1,"method":"webhook","webhook_url":"https://second.example.com/","template_language":"zh","blacklist_phone":"12345","blacklist_keywords":"来电"}'
event 4 0; event 5 1
[ "$(wc -l < "$base/sent")" = 2 ] || fail 'independent profile routing'
grep -q '^1|webhook|https://second.example.com/|call|外置卡2：12345 来电了$' "$base/sent" || fail 'wrong independent slot'
get_view sim1 > "$base/view"
[ "$(jsonfilter -i "$base/view" -e '@.config.call_forward_enabled')" = true ] || fail 'readback lost enabled checkbox'
event 5 1
[ "$(wc -l < "$base/sent")" = 2 ] || fail 'persisted duplicate claim'
SMS=$base/fake-sms
export TEST_SMS_OUT=$base/transport
printf '#!/bin/sh\nprintf "%%s|%%s\\n" "$2" "$3" >> "$TEST_SMS_OUT"\n' > "$SMS"
chmod 700 "$SMS"
method=sms; enabled=1; sms_to_phone=12345; sms_forward_device_info=0; template_language=en
transport_deliver call 12345 'External SIM 2: incoming call from 12345' now || fail 'call alert to caller was incorrectly blocked as an SMS loop'
[ "$DELIVER_RESULT" = sent ] || fail 'call transport failed'
if transport_deliver sms 12345 'original SMS' now; then fail 'original SMS self-forward guard lost'; fi
[ "$DELIVER_RESULT" = self_forward_blocked ] || fail 'original SMS guard reason lost'
[ "$(wc -l < "$TEST_SMS_OUT")" = 1 ] || fail 'SMS loop guard was bypassed for original SMS'
echo 'PASS call alerts: default-off, save/reload/uncheck, both slots, profiles, caller/source label and duplicate claims'
