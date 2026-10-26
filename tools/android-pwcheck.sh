#!/system/bin/sh
# The password of a system's own account (Ubuntu's ubuntu, OpenWrt's root) on the Linux filesystem, looked at on the
# device: for "keep the current password" of an update (install.sh, install.ps1 and the Magisk zip). It prints one
# word, and never a password or a hash:
#   android-pwcheck.sh -s ROOT USER   SET, EMPTY (no password: root logs in without one), LOCKED (! or *), NOUSER
#   android-pwcheck.sh ROOT USER      is the first line of standard input (an empty line: the empty password) USER's
#                                     password? MATCH, NOMATCH, LOCKED, NOUSER or UNVERIFIABLE (none of our tools
#                                     computes that hash's method). Exit status 0 only with MATCH.
# ROOT is one system's directory on the mounted Linux filesystem (.../ubuntu, .../openwrt).
#
# This runs as Android's root, and the Linux system is not trusted here: nothing of it is ever run (no chroot into it,
# no perl or openssl of its own - a replaced one, or a library or loader it brings, would run with Android's root),
# and what is read from it is data. Only the project's own tools compute the hash:
#   MU300_PWCHECK_BIN  mu300-pwcheck (tools/pwcheck, static, libxcrypt): every method - yescrypt (Ubuntu's passwd),
#                      SHA-crypt with or without rounds=, MD5-crypt (OpenWrt's passwd and LuCI), bcrypt. The password
#                      and the hash reach it on standard input, never on a command line.
#   MU300_PWCHECK_BB   a busybox with mkpasswd (the zips' and kernel bundles' own): MD5- and SHA-crypt without rounds=
#                      only, for releases whose bundle has no mu300-pwcheck yet
# A method neither computes is UNVERIFIABLE. /etc/shadow, /etc and ROOT must not be links: a link is resolved on
# Android's side, where it could name any of Android's files.
set -u
STATE=0
if [ "${1:-}" = -s ]; then STATE=1; shift; fi
[ $# = 2 ] || { echo "usage: android-pwcheck.sh [-s] ROOT USER" >&2; exit 2; }
ROOT=$1 U=$2
case $U in ''|*[!a-z0-9_-]*) echo NOUSER; exit 1 ;; esac
for p in "$ROOT" "$ROOT/etc" "$ROOT/etc/shadow"; do [ ! -L "$p" ] || { echo NOUSER; exit 1; }; done
[ -f "$ROOT/etc/shadow" ] && grep -q "^$U:" "$ROOT/etc/shadow" 2>/dev/null || { echo NOUSER; exit 1; }
h=$(sed -n "s/^$U:\([^:]*\):.*/\1/p" "$ROOT/etc/shadow" | head -n1)
if [ $STATE = 1 ]; then
    case $h in '') echo EMPTY ;; '!'*|'*'*) echo LOCKED ;; *) echo SET ;; esac
    exit 0
fi
IFS= read -r pw || [ -n "${pw:-}" ] || pw=
case $h in
    '') if [ -z "$pw" ]; then echo MATCH; exit 0; fi; echo NOMATCH; exit 1 ;;
    '!'*|'*'*) echo LOCKED; exit 1 ;;
esac
# no password typed, and the account has one: passwd and LuCI never write a hash of the empty password (no password
# is an empty field, above)
if [ -z "$pw" ]; then h=; echo NOMATCH; exit 1; fi
res=
if [ -n "${MU300_PWCHECK_BIN:-}" ] && [ -f "$MU300_PWCHECK_BIN" ]; then
    res=$(printf '%s\n%s\n' "$pw" "$h" | "$MU300_PWCHECK_BIN" 2>/dev/null) || true
    case $res in MATCH|NOMATCH) ;; *) res= ;; esac
fi
if [ -z "$res" ] && [ -n "${MU300_PWCHECK_BB:-}" ] && [ -f "$MU300_PWCHECK_BB" ]; then
    case $h in
        '$1$'*|'$5$'*|'$6$'*)
            case $h in '$1$'*) m=md5 ;; '$5$'*) m=sha256 ;; *) m=sha512 ;; esac
            s=${h#\$?\$}; s=${s%%\$*}
            # a salt of crypt's alphabet only (rounds= is not one busybox takes), handed over as an argument's value
            case $s in ''|*[!./0-9A-Za-z]*) ;;
                *)  got=$(printf '%s\n' "$pw" | "$MU300_PWCHECK_BB" mkpasswd -m "$m" -S "$s" -P 0 2>/dev/null) || got=
                    case $got in '$'?'$'"$s"'$'?*) if [ "$got" = "$h" ]; then res=MATCH; else res=NOMATCH; fi ;; esac
                    got= ;;
            esac ;;
    esac
fi
pw=; h=
echo "${res:-UNVERIFIABLE}"
[ "$res" = MATCH ]
