# The installers without a person at the terminal: every question answered by a variable, and a progress stream a
# program can read. Sourced by install.sh and uninstall.sh once t() exists; say(), die() and ask() are defined here.
# docs/INSTALLER-AUTOMATION.md has the whole interface (the variables of every question, the events).
#
# Answers mode - MU300_NONINTERACTIVE=1, or an answers file (--answers FILE, MU300_ANSWERS=FILE):
#   nothing is read from the terminal. Each question has a variable (the last argument of its ask), taken from the
#   environment or the file. A question whose variable is not set stops the run with an error that names the
#   variable: nothing is ever defaulted silently. A variable set to nothing takes the question's default, which is
#   never the destructive answer. The value @stdin puts the question on the progress stream and reads one line from
#   standard input, for a program that wants to decide while the installer runs (the graphical installer shows the
#   installer's own summary before it sends INSTALL).
#   The file: one NAME=value per line, the value taken as it is to the end of the line (no quotes, no expansion),
#   # for comments. Names: MU300_* and ANDROID_SERIAL. It holds the password in plain text, so it must be yours
#   alone: a file that is a link, that another user owns or that anyone else can read or write is refused
#   (chmod 600). Delete it afterwards.
# Secrets - the variables in ANSWERS_SECRET (the password): their values are never printed, never part of an event
#   and never in an error message, and they are not handed to the programs the installer starts: one given in the
#   environment is taken out of it at once, one from the file is never put there. The password reaches one command
#   only, on its standard input: the one that hashes it.
# Progress - MU300_PROGRESS=1 (on standard output) or MU300_PROGRESS_FD=N (on that descriptor, 3..9):
#   lines "@@mu300 {json}", one event each, next to the text for people. Every value is a string.
#     {"event":"hello","script":"install.sh","version":"1","mode":"..."}
#     {"event":"step","id":"download","percent":"20","title":"Downloading release v2026.10.23"}
#     {"event":"info","key":"region.size","value":"34828075008"}
#     {"event":"question","id":"MU300_ANS_KERNEL","text":"Kernel","default":"1","answer":"2"}
#     {"event":"download","file":"mu300-kernel.tar.gz","bytes":"1048576","total":"26971198","state":"running"}
#     {"event":"verify","file":"mu300-kernel.tar.gz","result":"ok"}
#     {"event":"error","message":"..."}
#     {"event":"done","result":"ok"}
#   The last event of a run is done or error, whatever ends it.

ANSWERS_MODE=0; ANSWERS_ASKED=; PROGRESS_FD=; PROGRESS_STEP=; PROGRESS_PCT=; PROGRESS_ENDED=0
# the answers that are secrets, in one place
ANSWERS_SECRET="MU300_ANS_PASSWORD MU300_ANS_CURRENT_PASSWORD MU300_ANS_CURRENT_PASSWORD_UBUNTU MU300_ANS_CURRENT_PASSWORD_OPENWRT MU300_ANS_CURRENT_PASSWORD_OPENWRT_LUCI"
answers_secret() { case " $ANSWERS_SECRET " in *" $1 "*) return 0 ;; esac; return 1; }

# answers_private FILE: the file is this user's alone - no link, owned by this user, no permission for the group or
# for others. (Not judged where the file system has no such permissions: Git Bash, MSYS, Cygwin.)
answers_private() {
    case $(uname -s 2>/dev/null) in MINGW*|MSYS*|CYGWIN*) return 0 ;; esac
    [ ! -h "$1" ] || return 1
    _ap=$(ls -ln "$1" 2>/dev/null | awk '{ print $1 " " $3; exit }')
    case ${_ap%% *} in -???------*) ;; *) return 1 ;; esac
    [ "${_ap#* }" = "$(id -u)" ]
}

# answers_load FILE: the variables of an answers file. A name that is not one of ours is an error, not a variable.
# answers_fail MESSAGE: an error before the progress stream is set up (the answers file is read first). A program
# that asked for the stream in the environment still gets its last event (measured: a file with mode 644 ended the
# run with no event at all).
answers_fail() {
    printf 'ERROR: %s\n' "$1" >&2
    _afj="@@mu300 {\"event\":\"error\",\"message\":\"$(json_esc "$1")\"}"
    case ${MU300_PROGRESS_FD:-} in
        [3-9]) eval "printf '%s\n' \"\$_afj\" >&$MU300_PROGRESS_FD" 2>/dev/null || true ;;
        '') [ "${MU300_PROGRESS:-0}" != 1 ] || printf '%s\n' "$_afj" ;;
    esac
    exit 2
}

answers_load() {
    [ -r "$1" ] || answers_fail "cannot read the answers file $1"
    answers_private "$1" ||
        answers_fail "the answers file $1 can hold a password and must be yours alone (no link, owned by you, chmod 600)"
    _an=0
    while IFS= read -r _al || [ -n "$_al" ]; do
        _an=$((_an + 1))
        _al=$(printf '%s' "$_al" | tr -d '\r')
        case $_al in ''|'#'*) continue ;; esac
        # (the line itself is not shown: it may be a secret that lost its name)
        case $_al in *=*) ;; *) answers_fail "answers file: line $_an is not NAME=value" ;; esac
        _ak=${_al%%=*}; _av=${_al#*=}
        case $_ak in *[!A-Z0-9_]*|'') answers_fail "answers file: not a variable of the installer: $_ak" ;; esac
        case $_ak in MU300_*|ANDROID_SERIAL) ;; *) answers_fail "answers file: not a variable of the installer: $_ak" ;; esac
        # for the programs this one starts too (adb reads ANDROID_SERIAL) - but never a secret: a name that the
        # environment already exports is unset first, which takes it out of there
        if answers_secret "$_ak"; then unset "$_ak"; eval "$_ak=\$_av"; else eval "$_ak=\$_av"; export "$_ak"; fi
    done < "$1"
    _av=; _al=
    ANSWERS_MODE=1
}

# answers_setup: after the options are read. ANSWERS_FILE is --answers' file.
answers_setup() {
    [ -z "${ANSWERS_FILE:-}" ] || MU300_ANSWERS=$ANSWERS_FILE
    [ -z "${MU300_ANSWERS:-}" ] || answers_load "$MU300_ANSWERS"
    [ "${MU300_NONINTERACTIVE:-0}" != 1 ] || ANSWERS_MODE=1
    # a secret given in the environment stays in this shell and leaves the environment: adb, curl, python and the
    # other programs started from here do not inherit it
    for _ak in $ANSWERS_SECRET; do
        eval "_as=\${$_ak+set}"
        if [ -n "$_as" ]; then eval "_av=\$$_ak"; unset "$_ak"; eval "$_ak=\$_av"; _av=; fi
    done
    case ${MU300_PROGRESS_FD:-} in
        [3-9]) PROGRESS_FD=$MU300_PROGRESS_FD ;;
        '') [ "${MU300_PROGRESS:-0}" != 1 ] || PROGRESS_FD=1 ;;
        *) printf 'ERROR: MU300_PROGRESS_FD must be a descriptor from 3 to 9\n' >&2; exit 2 ;;
    esac
    [ -z "$PROGRESS_FD" ] || trap answers_exit EXIT
}
# A run that ends with an error nobody announced (a command that failed under set -e, an exit in the middle) still
# ends the stream with an event: whoever reads it must not wait for more.
answers_exit() {
    _ae=$?
    [ $_ae = 0 ] || [ $PROGRESS_ENDED = 1 ] || progress error message "the installer stopped (exit status $_ae)"
}

# a value inside a JSON string: \ and " escaped, a line end and a tab as a space, no other control character
json_esc() {
    case $1 in
        *[\\\"]*|*[[:cntrl:]]*)
            printf '%s' "$1" | LC_ALL=C tr '\n\t\r' '   ' | LC_ALL=C tr -d '\000-\037\177' | LC_ALL=C sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' ;;
        *) printf '%s' "$1" ;;
    esac
}

# progress EVENT [KEY VALUE]...: one event line; nothing without a progress stream
progress() {
    [ -n "$PROGRESS_FD" ] || return 0
    case $1 in error|done) PROGRESS_ENDED=1 ;; esac
    _pj="{\"event\":\"$1\""; shift
    while [ $# -ge 2 ]; do
        _pj="$_pj,\"$1\":\"$(json_esc "$2")\""; shift 2
    done
    printf '@@mu300 %s}\n' "$_pj" >&"$PROGRESS_FD"
}
info() { progress info key "$1" value "$2"; }
# step ID PERCENT: what the next say() starts, for the stream: its id and how far the whole run is by then
step() { PROGRESS_STEP=$1; PROGRESS_PCT=$2; }

say() {
    progress step id "$PROGRESS_STEP" percent "$PROGRESS_PCT" title "$*"
    PROGRESS_STEP=; PROGRESS_PCT=
    printf '\n==> %s\n' "$*"
}
die() {
    progress error message "$*"
    printf '\n%s %s\n' "$(t 'ERROR:' 2>/dev/null || echo ERROR:)" "$*" >&2
    exit 1
}

# ask VAR "question" DEFAULT [ANSWER-VARIABLE]   (the question already translated; yes/no in any language count)
ask() {
    if [ $ANSWERS_MODE = 1 ]; then
        [ -n "${4:-}" ] || die "$(t 'this question has no variable and cannot be answered without a terminal: {1}' "$2")"
        eval "_as=\${$4+set}; _a=\${$4-}"
        [ -n "$_as" ] || die "$(t 'no answer to the question "{1}": set {2} (nothing is asked and nothing is assumed in answers mode)' "$2" "$4")"
        if [ "$_a" = @stdin ]; then
            progress question id "$4" text "$2" default "$3" state waiting
            printf '%s [%s]: ' "$2" "$(t "$3")"
            IFS= read -r _a || die "$(t 'no answer to the question "{1}" on standard input ({2}=@stdin)' "$2" "$4")"
            printf '\n'
        else
            # a question that comes back was not happy with its answer, and the answer would be the same again
            _ashow=$_a; ! answers_secret "$4" || _ashow='******'
            case " $ANSWERS_ASKED " in *" $4 "*) die "$(t 'the answer in {1} was not accepted: {2}' "$4" "$_ashow")" ;; esac
            ANSWERS_ASKED="$ANSWERS_ASKED $4"
            printf '%s [%s]: %s\n' "$2" "$(t "$3")" "$_ashow"
        fi
    else
        printf '%s [%s]: ' "$2" "$(t "$3")"; read -r _a
    fi
    [ -n "$_a" ] || _a=$3
    if command -v normalize_answer >/dev/null 2>&1; then _a=$(normalize_answer "$_a"); fi
    if answers_secret "${4:-}"; then progress question id "$4" text "$2" default "$3" secret 1
    else progress question id "${4:-}" text "$2" default "$3" answer "$_a"; fi
    eval "$1=\$_a"
}

# ask_password VAR "question" "repeat": typed twice without echo; in answers mode MU300_ANS_PASSWORD, which no
# event and no line of output ever shows. Empty when the two differ.
ask_password() {
    if [ $ANSWERS_MODE = 1 ]; then
        [ -n "${MU300_ANS_PASSWORD+set}" ] || die "$(t 'no answer to the question "{1}": set {2} (nothing is asked and nothing is assumed in answers mode)' "$2" MU300_ANS_PASSWORD)"
        _p1=$MU300_ANS_PASSWORD
        if [ "$_p1" = @stdin ]; then
            progress question id MU300_ANS_PASSWORD text "$2" default "" state waiting secret 1
            IFS= read -r _p1 || die "$(t 'no answer to the question "{1}" on standard input ({2}=@stdin)' "$2" MU300_ANS_PASSWORD)"
        fi
        _p2=$_p1
        printf '%s: ******\n' "$2"
    else
        printf '%s: ' "$2"
        [ -t 0 ] && stty -echo; read -r _p1; printf '\n%s: ' "$3"; read -r _p2; [ -t 0 ] && stty echo; echo
    fi
    progress question id MU300_ANS_PASSWORD text "$2" default "" secret 1
    [ "$_p1" = "$_p2" ] || _p1=
    eval "$1=\$_p1"; _p1=; _p2=
}
# ask_secret VAR "question" ANSWER-VARIABLE...: typed once, without echo (the current password, which is checked, not
# set). In answers mode the first of the variables that is set - an empty value is the empty password - or @stdin.
ask_secret() {
    _sv=$1 _sq=$2; shift 2
    _sid=$1
    if [ $ANSWERS_MODE = 1 ]; then
        _sset=
        for _sn in "$@"; do eval "_as=\${$_sn+set}"; if [ -n "$_as" ]; then _sid=$_sn; _sset=1; break; fi; done
        [ -n "$_sset" ] || die "$(t 'no answer to the question "{1}": set {2} (nothing is asked and nothing is assumed in answers mode)' "$_sq" "$1")"
        eval "_p1=\$$_sid"
        if [ "$_p1" = @stdin ]; then
            progress question id "$_sid" text "$_sq" default "" state waiting secret 1
            IFS= read -r _p1 || die "$(t 'no answer to the question "{1}" on standard input ({2}=@stdin)' "$_sq" "$_sid")"
        fi
        printf '%s: ******\n' "$_sq"
    else
        printf '%s: ' "$_sq"
        [ -t 0 ] && stty -echo; IFS= read -r _p1 || _p1=; [ -t 0 ] && stty echo; echo
    fi
    progress question id "$_sid" text "$_sq" default "" secret 1
    eval "$_sv=\$_p1"; _p1=
}
