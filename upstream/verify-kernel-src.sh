#!/bin/bash
# Verify a kernel.org source tarball against two independent OpenPGP signatures before anything unpacks it
# (FINDINGS 161):
#   1. sha256sums.asc, kernel.org's checksum list signed by its off-the-network autosigner: the tarball's SHA-256
#      must be on a line of the *signed* text (gpgv --output; text outside the clearsigned block is never read);
#   2. linux-$KV.tar.sign, the releaser's own signature over the uncompressed tar (Linus Torvalds, Greg
#      Kroah-Hartman or Sasha Levin), which kernel.org says the checksums do not replace.
# Each is checked with gpgv against a keyring committed in the repo (keys/, README there), and the signing key's
# primary fingerprint must be one listed in keys/*.fpr. Any failure - no gpgv, a bad, expired or revoked signature,
# an unknown key, no line for the tarball, a checksum mismatch - exits non-zero: the caller deletes the download.
#   verify-kernel-src.sh DIR KV   (DIR holds linux-KV.tar.xz, linux-KV.tar.sign and sha256sums.asc)
# MU300_KERNEL_KEYS overrides the keys directory (the tests use throwaway keys).
set -euo pipefail
DIR=${1:?usage: verify-kernel-src.sh DIR KV}
KV=${2:?usage: verify-kernel-src.sh DIR KV}
KEYS=${MU300_KERNEL_KEYS:-$(cd "$(dirname "$0")" && pwd)/keys}
TAR=linux-$KV.tar.xz
fail() { echo "kernel source $TAR: $*" >&2; exit 1; }

command -v gpgv >/dev/null 2>&1 || fail "gpgv not found (the signatures cannot be checked)"
command -v xz >/dev/null 2>&1 || fail "xz not found"
for f in "$DIR/$TAR" "$DIR/linux-$KV.tar.sign" "$DIR/sha256sums.asc" \
         "$KEYS/kernel-autosigner.gpg" "$KEYS/kernel-autosigner.fpr" "$KEYS/kernel-releasers.gpg" "$KEYS/kernel-releasers.fpr"; do
    [ -s "$f" ] || fail "missing or empty: $f"
done

W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT

# sigcheck KEYRING FPRLIST GPGV-ARGS...: gpgv's verdict plus a VALIDSIG whose primary key fingerprint is pinned.
# gpgv exits 0 only for a good signature; the status lines tell which key made it (VALIDSIG: field 3 is the signing
# (sub)key, field 12 the primary key).
sigcheck() {
    local keyring=$1 fprs=$2 rc=0 primary p
    shift 2
    rm -f "$W/status"
    gpgv --status-fd 3 --keyring "$keyring" "$@" 3>"$W/status" >"$W/gpgv.log" 2>&1 || rc=$?
    if [ "$rc" -ne 0 ] || ! grep -q '^\[GNUPG:\] GOODSIG ' "$W/status" ||
       grep -qE '^\[GNUPG:\] (BADSIG|ERRSIG|EXPSIG|EXPKEYSIG|REVKEYSIG|NO_PUBKEY) ' "$W/status"; then
        cat "$W/gpgv.log" >&2
        return 1
    fi
    primary=$(awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" { print ($12 != "" ? $12 : $3) }' "$W/status")
    [ -n "$primary" ] || { echo "gpgv reported no VALIDSIG" >&2; return 1; }
    for p in $primary; do
        grep -qixF "$p" "$fprs" || { echo "signed by $p, which is not pinned in $fprs" >&2; return 1; }
    done
}

# 1. the autosigner's checksum list; only the signed text (--output) is read
sigcheck "$KEYS/kernel-autosigner.gpg" "$KEYS/kernel-autosigner.fpr" --output "$W/sums" "$DIR/sha256sums.asc" ||
    fail "sha256sums.asc: no good signature from the pinned kernel.org autosigner"
want=$(awk -v f="$TAR" '$2 == f || $2 == "*" f { print $1 }' "$W/sums")
[ -n "$want" ] || fail "no line for it in the signed sha256sums.asc"
[ "$(printf '%s\n' "$want" | wc -l)" -eq 1 ] || fail "more than one line for it in the signed sha256sums.asc"
have=$( (sha256sum "$DIR/$TAR" 2>/dev/null || shasum -a 256 "$DIR/$TAR") | cut -d' ' -f1)
[ "$have" = "$want" ] || fail "SHA-256 $have, the signed sha256sums.asc says $want"

# 2. the releaser's signature over the uncompressed tar (gpgv reads the data from stdin: "-"); a broken xz stream
# fails the pipeline (pipefail) or the signature
xz -dc "$DIR/$TAR" | sigcheck "$KEYS/kernel-releasers.gpg" "$KEYS/kernel-releasers.fpr" "$DIR/linux-$KV.tar.sign" - ||
    fail "linux-$KV.tar.sign: no good signature from a pinned kernel releaser over the uncompressed tar"
echo "kernel source $TAR: SHA-256 in the autosigner's signed list, tar signed by a pinned releaser"
