#!/bin/sh
# Maintainer: sign a release's SHA256SUMS with the project's release key (docs/FINDINGS.md 86).
#   tools/sign-release.sh SHA256SUMS [PUBLIC_KEY]     writes SHA256SUMS.sig beside it
# The key is the secret key file that OpenWrt's usign wrote (usign -G -s release.sec -p release.pub; OpenBSD's
# signify -G -n makes the same file), given as its text in MU300_RELEASE_KEY (the repository secret of that name, in
# the Release workflow) or as a path in MU300_RELEASE_KEY_FILE. It is never printed, and never written anywhere but a
# directory of this run's own that is removed at its end.
# The signature is the file `usign -S` writes (a comment line, then base64 of "Ed", the key's number and the 64 bytes
# of an Ed25519 signature of the whole file), made with openssl 3 so that neither the runner nor a maintainer's
# computer needs usign. mu300-update checks it with usign on OpenWrt and with openssl on Ubuntu (sig_state).
# With PUBLIC_KEY the result is checked against it before it counts: signing with a key that is not the one in the
# images must stop the release here, not on every device that then refuses it.
set -eu
SUMS=${1:?usage: tools/sign-release.sh SHA256SUMS [PUBLIC_KEY]}
PUB=${2:-}
[ -s "$SUMS" ] || { echo "sign-release: $SUMS is missing or empty" >&2; exit 1; }
[ -n "${MU300_RELEASE_KEY:-}${MU300_RELEASE_KEY_FILE:-}" ] || { echo "sign-release: no key (MU300_RELEASE_KEY or MU300_RELEASE_KEY_FILE)" >&2; exit 1; }
# an openssl that signs a file with Ed25519 as it is (-rawin: 3.0 or newer; macOS's own is LibreSSL and does not)
SSL=${MU300_OPENSSL:-openssl}
"$SSL" pkeyutl -help 2>&1 | grep -q -- -rawin ||
    { echo "sign-release: $SSL cannot sign with Ed25519 (OpenSSL 3 is needed; MU300_OPENSSL names another one)" >&2; exit 1; }

W=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/mu300-sign.XXXXXX")
trap 'rm -rf "${W:?}"' EXIT
umask 077
if [ -n "${MU300_RELEASE_KEY:-}" ]; then printf '%s\n' "$MU300_RELEASE_KEY" > "$W/sec"
else cat "$MU300_RELEASE_KEY_FILE" > "$W/sec"; fi
hex() { od -An -tx1 | tr -d ' \n'; }
size() { wc -c < "$1" | tr -d ' '; }
# the line that is not the comment, as bytes
raw() { grep -v '^untrusted comment:' "$1" | tr -d ' \r\n' | "$SSL" base64 -d -A 2>/dev/null; }

# the secret key: "Ed", "BK", the rounds of its passphrase (0: none), a salt, a checksum, the key's number, then the
# 32-byte seed and the 32-byte public key
raw "$W/sec" > "$W/sec.raw"
[ "$(size "$W/sec.raw")" = 104 ] && [ "$(head -c 2 "$W/sec.raw")" = Ed ] ||
    { echo "sign-release: the key is not a usign secret key (usign -G -s FILE)" >&2; exit 1; }
[ "$(tail -c +5 "$W/sec.raw" | head -c 4 | hex)" = 00000000 ] ||
    { echo "sign-release: the key has a passphrase (signify without -n): make one without (usign -G, or signify -G -n)" >&2; exit 1; }
tail -c +33 "$W/sec.raw" | head -c 8 > "$W/num"
# openssl takes the seed as a PKCS#8 key: the 16 bytes every Ed25519 private key starts with, then the seed
{ printf '\060\056\002\001\000\060\005\006\003\053\145\160\004\042\004\040'; tail -c +41 "$W/sec.raw" | head -c 32; } > "$W/key.der"
"$SSL" pkeyutl -sign -inkey "$W/key.der" -keyform DER -rawin -in "$SUMS" -out "$W/sig.raw" 2>/dev/null &&
    [ "$(size "$W/sig.raw")" = 64 ] || { echo "sign-release: signing failed" >&2; exit 1; }
{
    printf 'untrusted comment: signed by key %s\n' "$(hex < "$W/num")"
    { printf 'Ed'; cat "$W/num" "$W/sig.raw"; } | "$SSL" base64 -A
    printf '\n'
} > "$W/sig"

if [ -n "$PUB" ]; then
    raw "$PUB" > "$W/pub.raw"
    [ "$(size "$W/pub.raw")" = 42 ] || { echo "sign-release: $PUB is not a usign public key" >&2; exit 1; }
    [ "$(tail -c +3 "$W/pub.raw" | head -c 8 | hex)" = "$(hex < "$W/num")" ] ||
        { echo "sign-release: the key is not the one $PUB belongs to (another key number): nothing was signed" >&2; exit 1; }
    { printf '\060\052\060\005\006\003\053\145\160\003\041\000'; tail -c 32 "$W/pub.raw"; } > "$W/pub.der"
    "$SSL" pkeyutl -verify -pubin -inkey "$W/pub.der" -keyform DER -rawin -in "$SUMS" -sigfile "$W/sig.raw" >/dev/null 2>&1 ||
        { echo "sign-release: the signature does not check against $PUB: nothing was signed" >&2; exit 1; }
fi
umask 022
cat "$W/sig" > "$SUMS.sig"
echo "signed $SUMS with key $(hex < "$W/num")${PUB:+ (checked against $PUB)}"
