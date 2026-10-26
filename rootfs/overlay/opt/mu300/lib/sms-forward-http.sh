#!/bin/sh
# One HTTP(S) POST for mu300-sms-forward with curl: Ubuntu's client, where OpenWrt uses sms-forward-http.uc (ucode).
# The same contract as that helper:
#   stdin   URL, then "Header: value" lines (Content-Type among them), an empty line, the body (to the end)
#   stdout  one line, "status N" (the HTTP status) or "error WHAT" (no status came); with MU300_FWD_ANSWER=N in the
#           environment (the caller's, never the request's), the first N bytes (65536 at most) of a 2xx answer's body
#           after it. Nothing else: not the URL, not a header, not any other answer.
# The URL and the headers can hold a secret (a Telegram bot token, an Authorization header): they reach curl in its
# configuration on stdin (-K -), and the body in a file; nothing of the request is ever in a command line. The files
# are 0600 in a directory of their own under MU300_FWD_TMP (the forwarding's RAM directory), removed at the end.
# HTTPS checks the server's certificate against the system's CA bundle; redirects are not followed (a 3xx is the
# status); 15 s for the whole request, as the ucode helper.
set -u
umask 077
export LC_ALL=C
keep=${MU300_FWD_ANSWER:-0}
case $keep in ''|*[!0-9]*) keep=0 ;; esac
[ "$keep" -le 65536 ] || keep=65536
base=${MU300_FWD_TMP:-/run/mu300-sms-forward}
[ -d "$base" ] || base=${TMPDIR:-/tmp}
D=$(mktemp -d "$base/http.XXXXXX" 2>/dev/null) || { echo 'error no temporary directory'; exit 0; }
trap 'rm -rf "$D"' EXIT
trap 'exit 1' INT TERM

cat > "$D/req"
url=$(sed -n 1p "$D/req")
# the URL as the ucode helper takes it, and one line of printable ASCII (it goes into curl's configuration)
printf '%s\n' "$url" | grep -Eqx 'https?://[^/[:space:]]+[ -~]*' || { echo 'error bad url'; exit 0; }
# the head: the lines up to the first empty one; the body: every byte after that empty line
off=$(awk '{ n += length($0) + 1 } $0 == "" { print n; f = 1; exit } END { if (!f) print -1 }' "$D/req")
if [ "$off" -ge 0 ] 2>/dev/null; then
    tail -c +"$((off + 1))" "$D/req" > "$D/body"
    head -c "$off" "$D/req" | sed '1d;$d' > "$D/head"
else
    : > "$D/body"
    sed '1d' "$D/req" > "$D/head"
fi
rm -f "$D/req"

# cq TEXT: TEXT as a double-quoted value of curl's configuration (a backslash and a quote escaped)
cq() { printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }
{
    printf 'url = %s\n' "$(cq "$url")"
    # "Name: value" lines only, as the ucode helper reads them; printable ASCII (a tab allowed after the colon)
    grep -E '^[A-Za-z0-9-]+:[ 	]*[ -~	]*$' "$D/head" | while IFS= read -r h; do
        printf 'header = %s\n' "$(cq "$h")"
    done
    printf 'data-binary = %s\n' "$(cq "@$D/body")"
    printf 'output = %s\n' "$(cq "$D/answer")"
    printf 'write-out = "%%{http_code}"\n'
    printf 'max-time = 15\nconnect-timeout = 10\nsilent\nproto = "=http,https"\n'
} > "$D/conf"
rm -f "$D/head"

code=$(curl -K - < "$D/conf" 2>/dev/null); rc=$?
rm -f "$D/conf"
case $code in
    [1-9][0-9][0-9])
        echo "status $code"
        case $code in 2[0-9][0-9]) [ "$keep" -gt 0 ] && [ -f "$D/answer" ] && head -c "$keep" "$D/answer" ;; esac
        exit 0 ;;
esac
case $rc in
    6) echo 'error the address could not be resolved' ;;
    7) echo 'error the server could not be reached' ;;
    28) echo 'error no answer in time' ;;
    35|51|53|54|58|59|60|66|77|80|82|83|90|91) echo 'error TLS failed' ;;
    *) echo "error curl $rc" ;;
esac
exit 0
