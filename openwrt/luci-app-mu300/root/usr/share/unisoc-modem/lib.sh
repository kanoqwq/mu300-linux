# Shared by the rpcd backend (/usr/libexec/rpcd/mu300dash) and every adapter in
# /usr/libexec/unisoc-modem: sourced, never run. It holds the one JSON string
# escaper, and the checks on uci options that name a program, a path or an
# interface - the web UI can write those (uci unisoc_modem is in the panel's
# write grant), and each one reaches a root command, so a value outside the
# allow-list is ignored and the built-in default is used instead.

# --------------------------------------------------------------------- JSON
# JSON_ESC: an awk prologue defining jesc(s), which escapes the quote, the
# backslash and every control character (U+0001..U+001F, U+007F). Text above
# 0x7f passes through unchanged. Use as: awk "$JSON_ESC"' { ... jesc($1) ... }'
JSON_ESC='function jesc(s,   o, i, n, c) {
    o = ""; n = length(s)
    for (i = 1; i <= n; i++) { c = substr(s, i, 1); o = o ((c in JE) ? JE[c] : c) }
    return o
}
BEGIN {
    for (i = 1; i < 32; i++) JE[sprintf("%c", i)] = sprintf("\\u%04x", i)
    JE[sprintf("%c", 127)] = "\\u007f"
    JE["\\"] = "\\\\"; JE["\""] = "\\\""; JE["\n"] = "\\n"; JE["\t"] = "\\t"; JE["\r"] = "\\r"
}'
# json_str TEXT: TEXT as one JSON string, quotes included (newlines become \n)
json_str() {
    printf '%s\n' "$1" | awk "$JSON_ESC"'
        BEGIN { printf "\"" } { printf "%s%s", (NR > 1 ? "\\n" : ""), jesc($0) } END { printf "\"" }'
}
# json_str_or_null TEXT: json_str, or null for an empty TEXT
json_str_or_null() { if [ -n "$1" ]; then json_str "$1"; else echo null; fi; }
# json_num TEXT: TEXT if it is a JSON number (optional minus, no leading zero,
# optional fraction), else null
json_num() {
    case ${1#-} in
        ''|*[!0-9.]*|.*|*.|*.*.*|0[0-9]*) echo null ;;
        *) printf '%s\n' "$1" ;;
    esac
}

# jfilter [-i FILE] -e EXPR: jsonfilter where there is one (OpenWrt); elsewhere (Ubuntu, which runs the lock and
# forwarding adapters too) the few expressions the adapters use, through jq: @.a.b (a string as it is, anything else
# as compact JSON, nothing for null or a missing key) and @.a[*] (each element on its own line). Any other expression
# matches nothing. The input is stdin unless -i names a file.
jfilter() {
    if command -v jsonfilter >/dev/null 2>&1; then jsonfilter "$@"; return; fi
    _jf_i= _jf_e=
    while [ $# -gt 0 ]; do
        case $1 in -i) _jf_i=${2:-}; shift 2 ;; -e) _jf_e=${2:-}; shift 2 ;; *) shift ;; esac
    done
    case $_jf_e in @.?*) ;; *) return 1 ;; esac
    _jf_q=${_jf_e#@}
    case $_jf_q in *'[*]') _jf_q="${_jf_q%'[*]'}[]" ;; esac
    # names of letters, digits and _ only: nothing else of an expression reaches jq
    case $_jf_q in *[!A-Za-z0-9_.\[\]]*|*..*) return 1 ;; esac
    command -v jq >/dev/null 2>&1 || return 1
    if [ -n "$_jf_i" ]; then jq -rc "$_jf_q | select(. != null)" "$_jf_i" 2>/dev/null
    else jq -rc "$_jf_q | select(. != null)" 2>/dev/null; fi
}

# ---------------------------------------------------------- uci allow-lists
# uci_get OPTION: unisoc_modem.main.OPTION ('' when unset)
uci_get() { uci -q get "unisoc_modem.main.$1" 2>/dev/null || true; }

# safe_iface NAME DEFAULT: a network interface, device or uci section name (at
# most 15 characters, starting with a letter or digit: never an option, a path
# or a sed/regex metacharacter), else DEFAULT
safe_iface() {
    case $1 in
        ''|[!A-Za-z0-9]*|*[!A-Za-z0-9_.-]*) printf '%s\n' "$2" ;;
        *) if [ ${#1} -le 15 ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi ;;
    esac
}

# data_cid DEVICE SLOT: the PDP context behind a SIPA data device of SIM slot SLOT (each card has eight: sipa_eth0-7
# are slot 0's CID 1-8, sipa_eth8-15 slot 1's CID 1-8, as mobile-data names them); nothing, and status 1, for any
# other name or another card's device. Never guessed: the dashboard asks the modem about this CID's bearer.
data_cid() {
    case $2:$1 in
        0:sipa_eth[0-7]) echo $(( ${1#sipa_eth} + 1 )) ;;
        1:sipa_eth[89]|1:sipa_eth1[0-5]) echo $(( ${1#sipa_eth} - 7 )) ;;
        *) return 1 ;;
    esac
}

# safe_state_dir PATH DEFAULT: a directory under /etc/unisoc-modem or
# /etc/mu300 (no component starting with a dot, so no ".."), else DEFAULT
safe_state_dir() {
    case $1 in
        /etc/unisoc-modem|/etc/mu300|/etc/unisoc-modem/*|/etc/mu300/*)
            case $1 in
                */.*|*//*|*/|*[!A-Za-z0-9_./-]*) printf '%s\n' "$2" ;;
                *) printf '%s\n' "$1" ;;
            esac ;;
        *) printf '%s\n' "$2" ;;
    esac
}

# safe_tty PATH DEFAULT: an AT tty, /dev/stty* or /dev/tty<name>, else DEFAULT
safe_tty() {
    case $1 in
        /dev/stty[A-Za-z0-9_]*|/dev/tty[A-Za-z0-9_]*)
            case ${1#/dev/} in */*|*[!A-Za-z0-9_]*) printf '%s\n' "$2" ;; *) printf '%s\n' "$1" ;; esac ;;
        *) printf '%s\n' "$2" ;;
    esac
}

# PLATFORM_DIR: where a platform port installs the adapter programs the uci
# options at_command, sms_command and role_command may name (root-owned, part
# of the image); MU300_PLATFORM_DIR is for the tests
PLATFORM_DIR=${MU300_PLATFORM_DIR:-/usr/libexec/unisoc-modem/platform}
# safe_program PATH [KNOWN...]: PATH if it is one of the KNOWN paths or a
# program directly inside PLATFORM_DIR, else nothing
safe_program() {
    _p=$1; shift
    [ -n "$_p" ] || return 0
    for _k in "$@"; do [ "$_p" = "$_k" ] && { printf '%s\n' "$_p"; return 0; }; done
    case ${_p#"$PLATFORM_DIR"/} in
        "$_p"|''|.*|*/*|*[!A-Za-z0-9_.-]*) return 0 ;;
    esac
    printf '%s\n' "$_p"
}

# ------------------------------------------------------------------- files
# RUN_DIR: the package runtime directory (SMS text, the AT history, the method
# log). Its parent is root's (/var/run is /tmp/run, 0755 root), so no other
# user can make it first, as anyone could under /tmp.
RUN_DIR=/var/run/unisoc-modem
# private_dir DIR: create DIR readable by its owner only (root on the device);
# or fail (status 1, the reason on stderr) when DIR is a symlink, not a
# directory, not owned by the user running this, or writable by group or
# others: such a directory was made by someone else, who may have planted
# symlinks in it that root would then write through. Callers stop on failure.
private_dir() {
    (umask 077; mkdir -p "$1") 2>/dev/null
    if [ -L "$1" ] || [ ! -d "$1" ] || [ ! -O "$1" ]; then
        echo "unisoc-modem: refusing the runtime directory $1: a symlink, not a directory, or not ours" >&2
        return 1
    fi
    case $(ls -ld "$1" 2>/dev/null) in
        d????w*|d???????w*)
            echo "unisoc-modem: refusing the runtime directory $1: writable by group or others" >&2
            return 1 ;;
        d*) ;;
        *)
            echo "unisoc-modem: refusing the runtime directory $1: its mode cannot be read" >&2
            return 1 ;;
    esac
    chmod 700 "$1"
}

# -------------------------------------------------------------------- radio
# MOBILE_DATA: the platform dialer. Its radio lock keeps one radio state machine at a time (the dial, the watchdog,
# the boot warm-up: K57); the panel's own radio sequences take the same lock through it (mobile-data radio-locked),
# never a copy of its locking. A platform without it runs them unlocked, as before. MU300_MOBILE_DATA: the tests.
MOBILE_DATA=${MU300_MOBILE_DATA:-/opt/mu300/bin/mobile-data}
# SIM_RADIO: each SIM's radio on or off, kept across reboots (mu300-sim-radio, docs/FINDINGS.md 185). rf_allowed: the
# card in use's radio may be on (yes without the tool). A sequence that would switch the radio on asks it first.
SIM_RADIO=${MU300_SIM_RADIO_BIN:-/opt/mu300/bin/mu300-sim-radio}
rf_allowed() { [ ! -x "$SIM_RADIO" ] || "$SIM_RADIO" allowed 2>/dev/null; }
# radio_busy: a live process holds the radio lock (the panel answers "busy" instead of starting a sequence)
radio_busy() { [ -x "$MOBILE_DATA" ] && "$MOBILE_DATA" radio-busy >/dev/null 2>&1; }
# radio_locked CMD...: CMD under the radio lock, waiting MU300_RADIO_LOCK_WAIT seconds (0 by default) for a holder;
# status 75 when the lock stayed busy and CMD did not run. MU300_RADIO_LOCKED=1 in CMD's environment.
radio_locked() {
    if [ -x "$MOBILE_DATA" ]; then
        MU300_RADIO_LOCKED=1 "$MOBILE_DATA" radio-locked "$@"
    else
        MU300_RADIO_LOCKED=1 "$@"
    fi
}
