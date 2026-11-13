#!/bin/sh
# Exercise real jshn + ucode validation and per-SIM saves. No network or AT.
set -e
source=$1
FORWARD_ORIGINAL_DIR=${FORWARD_ORIGINAL_DIR:-${source%/*}}
base=$(mktemp -d)
trap 'rm -rf "$base"' EXIT
export MU300_FORWARD_DIR=$base/config MU300_FORWARD_RESULT=$base/result
export MU300_SMS_POOL=$base/pool MU300_SMS_BIN=/bin/false E5_FORWARD_NATIVE=/bin/false
export MU300_FORWARD_RENDERER=${FORWARD_ORIGINAL_DIR}/forward-template.uc
export MU300_POWER_LIB=${FORWARD_ORIGINAL_DIR}/power-supply.sh
export MU300_FORWARD_FILTER=${source%/*}/forward-filter.uc
mkdir -p "$base/config" "$base/pool/msg" "$base/pool/sim1/msg"
printf '0\n' > "$base/pool/next_id"; printf '0\n' > "$base/pool/sim1/next_id"
uci() { case "$*" in *unisoc_modem.main.sim_slots*) echo 2 ;; *) return 1 ;; esac; }
ubus() { :; }
sed '/^case "${1:-}" in/,$d' "$source" > "$base/library.sh"
. "$base/library.sh"
fail() { echo "FAIL: $*" >&2; exit 1; }
save() {
    response=$(printf '%s' "$1" | set_config)
    [ "$(printf '%s' "$response" | jsonfilter -e '@.ok')" = 1 ] || fail "save $response"
}
allowed() { if blocked "$1" "$2"; then fail 'unexpected filter rejection'; fi; }
rejected() { blocked "$1" "$2" || fail 'unexpected filter pass'; }
save '{"enabled":0,"method":"webhook","template_language":"en"}'
select_profile shared; load_config
[ "$number_filter_mode:$text_filter_mode:$filter_ignore_case" = off:off:0 ] || fail defaults
FILTER=$base/does-not-exist
allowed 106123 'default off'
FILTER=$MU300_FORWARD_FILTER
save '{"enabled":0,"method":"webhook","number_filter_mode":"whitelist","number_filter_regex":"^106[0-9]+$","text_filter_mode":"blacklist","text_filter_regex":"promo|广告","filter_ignore_case":1}'
select_profile shared; load_config
allowed 106123 '验证码 456789'
rejected 10086 '验证码 456789'
rejected 106123 'PROMO sale'
rejected 106123 '这是广告'
get_view shared > "$base/view"
[ "$(jsonfilter -i "$base/view" -e '@.config.number_filter_mode')" = whitelist ] || fail readback
[ "$(jsonfilter -i "$base/view" -e '@.config.filter_ignore_case')" = true ] || fail case_readback
save '{"enabled":0,"method":"webhook"}'
select_profile shared; load_config
allowed 106123 'verification code'
rejected 10086 'verification code'
[ "$filter_ignore_case" = 1 ] || fail old_client_cleared_case
before=$(sha256sum "$CONFIG" | cut -d' ' -f1)
response=$(printf '%s' '{"enabled":0,"method":"webhook","number_filter_regex":"ok\n["}' | set_config)
[ "$(printf '%s' "$response" | jsonfilter -e '@.error')" = invalid_number_filter_regex ] || fail invalid_regex
[ "$(printf '%s' "$response" | jsonfilter -e '@.line')" = 2 ] || fail invalid_line
[ "$(sha256sum "$CONFIG" | cut -d' ' -f1)" = "$before" ] || fail non_atomic_invalid_save
response=$(printf '%s' '{"enabled":0,"method":"webhook","text_filter_mode":"whitelist","text_filter_regex":""}' | set_config)
[ "$(printf '%s' "$response" | jsonfilter -e '@.error')" = text_filter_empty ] || fail empty_whitelist
[ "$(sha256sum "$CONFIG" | cut -d' ' -f1)" = "$before" ] || fail invalid_clear_changed_config
save '{"enabled":0,"method":"webhook","number_filter_mode":"off","number_filter_regex":"","text_filter_mode":"off","text_filter_regex":"","filter_ignore_case":0}'
select_profile shared; load_config
allowed 10086 'PROMO sale'
[ -z "$number_filter_regex$text_filter_regex" ] || fail explicit_clear_failed
save '{"profile":"sim0","mode":"per_sim","enabled":0,"method":"webhook","number_filter_mode":"whitelist","number_filter_regex":"^106[0-9]+$"}'
save '{"profile":"sim1","mode":"per_sim","enabled":0,"method":"webhook","text_filter_mode":"whitelist","text_filter_regex":"OTP|验证码"}'
select_profile sim0; load_config
allowed 106123 'normal'; rejected 10086 OTP
select_profile sim1; load_config
allowed 10086 OTP; rejected 106123 'normal'
save '{"profile":"sim1","mode":"per_sim","enabled":0,"method":"webhook","blacklist_phone":"10086","blacklist_keywords":"验证码"}'
select_profile sim1; load_config
allowed 10086 OTP; allowed 106123 '验证码'
! grep -q 'blacklist_phone\|blacklist_keywords' "$CONFIG" || fail 'deprecated fields saved'
# Existing on-disk legacy rules are reported, but never secretly applied.
json_load_file "$CONFIG"
json_add_string blacklist_phone '10086'
json_add_string blacklist_keywords '验证码'
json_dump > "$CONFIG"
load_config
allowed 10086 OTP; allowed 106123 '验证码'
[ "$legacy_blacklist_present" = 1 ] || fail 'legacy notice missing'
response=$(get_view sim1)
[ "$(printf '%s' "$response" | jsonfilter -e '@.config.legacy_blacklist_present')" = true ] || fail 'legacy notice not returned'
save '{"profile":"sim1","mode":"per_sim","enabled":0,"method":"webhook"}'
load_config
[ "$legacy_blacklist_present" = 0 ] || fail 'retired fields retained after save'
number_filter_mode=off; text_filter_mode=whitelist; text_filter_regex='['
rejected 106123 'must not leak on invalid configuration'
[ "$(cut -d'|' -f1 "$RESULT")" = invalid_forward_filter ] || fail invalid_runtime_result
printf 'PASS: defaults, filters, old clients, atomic invalid saves, clearing, per-SIM isolation, regex-only filtering, legacy retirement and invalid-runtime suppression\n'
# Exercise the event entry points as well as the pure matcher, without a modem.
export MU300_CALL_RUNTIME=$base/calls TEST_MESSAGES=$base/native TEST_SENT=$base/sent
mkdir -p "$TEST_MESSAGES"
deliver() { printf '%s|%s|%s\n' "$MU300_SIM_SLOT" "$1" "$2" >> "$TEST_SENT"; DELIVER_RESULT=sent; }
save '{"profile":"sim0","mode":"per_sim","enabled":1,"call_forward_enabled":1,"method":"webhook","webhook_url":"https://example.com/test","number_filter_mode":"whitelist","number_filter_regex":"^106[0-9]+$","text_filter_mode":"blacklist","text_filter_regex":"promo","blacklist_phone":"","blacklist_keywords":""}'
save '{"profile":"sim1","mode":"per_sim","enabled":1,"call_forward_enabled":1,"method":"webhook","webhook_url":"https://example.com/test","number_filter_mode":"whitelist","number_filter_regex":"^10086$","text_filter_mode":"whitelist","text_filter_regex":"OTP","blacklist_phone":"","blacklist_keywords":""}'
if command -v process_event >/dev/null; then
    NATIVE=$base/native-adapter
    cat > "$NATIVE" <<'EOF_NATIVE'
#!/bin/sh
case "$1" in
    message) cat "$TEST_MESSAGES/$2.json" ;;
    claim) [ ! -e "$TEST_MESSAGES/$2.claimed" ] || exit 1; : > "$TEST_MESSAGES/$2.claimed"; cat "$TEST_MESSAGES/$2.json" ;;
    *) exit 1 ;;
esac
EOF_NATIVE
    chmod 700 "$NATIVE"
    emit_sms() {
        json_init; json_add_int card "$2"; json_add_string number "$3"; json_add_string text "$4"; json_add_string time now
        json_dump > "$TEST_MESSAGES/$1.json"
        (process_event "$1") > "$base/event-reply"
        [ "$(jsonfilter -i "$base/event-reply" -e '@.ok')" = 1 ] || fail 'native event rejected'
    }
else
    emit_sms() {
        _ep=$ROOT_POOL
        [ "$2" = 0 ] || _ep=$ROOT_POOL/sim1
        _next=$(( $(cat "$_ep/next_id") + 1 ))
        printf 'dir: mt\nfrom: %s\nreceived: %s\nscts: old\nsource: direct\ncoding: pdu\n\n%s\n' "$3" "$(( $(date +%s) + 1 ))" "$4" > "$_ep/msg/$(printf '%06d' "$_next")"
        printf '%s\n' "$_next" > "$_ep/next_id"
        select_profile shared; load_config; read_mode
        if [ "$2" = 0 ]; then process_first_sim; else process_second_sim; fi
    }
fi
emit_sms 1 0 106123 'verification code'
emit_sms 2 0 106123 'promo sale'
emit_sms 3 0 10086 'verification code'
emit_sms 4 1 10086 'OTP 123456'
emit_sms 5 1 10086 'normal notice'
emit_sms 6 1 106123 'OTP 123456'
[ "$(wc -l < "$TEST_SENT")" = 2 ] || fail 'event filters/routing mismatch'
grep -q '^0|sms|106123$' "$TEST_SENT" || fail 'slot 0 event lost'
grep -q '^1|sms|10086$' "$TEST_SENT" || fail 'slot 1 event lost'
if command -v process_event >/dev/null; then
    (process_event 1) >/dev/null; (process_event 4) >/dev/null
else
    select_profile shared; load_config; read_mode
    process_first_sim; process_second_sim
fi
[ "$(wc -l < "$TEST_SENT")" = 2 ] || fail 'duplicate SMS forwarded'
# Clearing a filter does not replay previously blocked messages.
save '{"profile":"sim0","mode":"per_sim","enabled":1,"call_forward_enabled":1,"method":"webhook","webhook_url":"https://example.com/test","number_filter_mode":"off","text_filter_mode":"off"}'
if command -v process_event >/dev/null; then
    (process_event 2) >/dev/null
else
    select_profile shared; load_config; read_mode; process_first_sim
fi
[ "$(wc -l < "$TEST_SENT")" = 2 ] || fail 'blocked SMS replayed'
# Neither the number nor the body filter is applied to incoming-call alerts.
printf '{"id":"0123456789abcdef0123456789abcdef","card":1,"number":"106123","labels":{"zh":"外置卡2","en":"External SIM 2","tr":"Harici SIM 2"}}' | process_call_event > "$base/call-reply"
[ "$(jsonfilter -i "$base/call-reply" -e '@.ok')" = 1 ] || fail 'call event failed'
[ "$(wc -l < "$TEST_SENT")" = 3 ] || fail 'SMS whitelist interfered with call alert'
grep -q '^1|call|106123$' "$TEST_SENT" || fail 'call wrong target'
printf 'PASS: real SMS events, both slots, duplicate suppression, no blocked replay, calls unaffected\n'

# Shell-looking message contents stay data throughout native event handling.
_injection="OTP \$(touch $base/MUST_NOT_EXIST); \`id\`"
emit_sms 7 1 10086 "$_injection"
[ ! -e "$base/MUST_NOT_EXIST" ] || fail 'SMS was evaluated as shell commands'
[ "$(wc -l < "$TEST_SENT")" = 4 ] || fail 'literal shell-like SMS not delivered'
printf 'PASS: untrusted SMS remains literal data through event dispatch\n'
