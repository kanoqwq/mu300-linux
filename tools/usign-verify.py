#!/usr/bin/env python3
"""Check a usign signature: tools/usign-verify.py PUBLIC_KEY SIGNATURE FILE

Exit 0 and "valid" only when SIGNATURE (the file `usign -S` writes) is a signature of FILE by PUBLIC_KEY (a usign
public key file) - the same check as `usign -V -m FILE -p PUBLIC_KEY -x SIGNATURE`. Anything else exits 1 and says
why: a file that is not a key or a signature, a signature by another key (its number is not the key's), or one that
does not verify.

openwrt/build-rootfs.sh checks OpenWrt's sha256sums with it before it takes a hash from that file. Plain Python
(RFC 8032's Ed25519 verification, written out): no usign on the build host, and macOS's own openssl (LibreSSL)
cannot verify Ed25519 over a file as it is. Each file: a comment line, then base64 of "Ed", the key's 8-byte
number and the 32-byte public key (a key) or the 64-byte Ed25519 signature of the whole file (a signature).
"""
import base64
import hashlib
import sys

P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493   # the group order
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _add(a, b):
    # extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, x*y = T/Z (RFC 8032 5.1.4)
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * D % P
    Dd = 2 * a[2] * b[2] % P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)


def _mul(s, pt):
    r = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        s >>= 1
    return r


def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


_GY = 4 * pow(5, P - 2, P) % P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % P)


def ed25519_verify(public, msg, sig):
    """RFC 8032 5.1.7: True when sig (64 bytes) is public's (32 bytes) signature of msg."""
    if len(public) != 32 or len(sig) != 64:
        return False
    A = _decompress(public)
    R = _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], 'little')
    if s >= Q:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + public + msg).digest(), 'little') % Q
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))


def usign_blob(path, size):
    """The bytes of a usign key or signature file (its second line, base64), when they are size long and say Ed."""
    with open(path, 'rb') as f:
        lines = f.read().splitlines()
    if len(lines) < 2 or not lines[0].startswith(b'untrusted comment:'):
        raise ValueError('%s: not a usign file (no comment line)' % path)
    try:
        raw = base64.b64decode(lines[1].strip(), validate=True)
    except ValueError:
        raise ValueError('%s: not base64' % path) from None
    if len(raw) != size or raw[:2] != b'Ed':
        raise ValueError('%s: not an Ed25519 usign %s' % (path, 'key' if size == 42 else 'signature'))
    return raw


def verify(pub_path, sig_path, file_path):
    """None when valid, else why not."""
    try:
        pub = usign_blob(pub_path, 42)
        sig = usign_blob(sig_path, 74)
    except (OSError, ValueError) as e:
        return str(e)
    if pub[2:10] != sig[2:10]:
        return 'signed by key %s, not by %s' % (sig[2:10].hex(), pub[2:10].hex())
    try:
        with open(file_path, 'rb') as f:
            msg = f.read()
    except OSError as e:
        return str(e)
    if not ed25519_verify(pub[10:], msg, sig[10:]):
        return 'the signature does not verify: %s is not what key %s signed' % (file_path, pub[2:10].hex())
    return None


def main(argv):
    if len(argv) != 4:
        print('usage: usign-verify.py PUBLIC_KEY SIGNATURE FILE', file=sys.stderr)
        return 2
    why = verify(argv[1], argv[2], argv[3])
    if why:
        print('usign-verify: ' + why, file=sys.stderr)
        return 1
    print('valid')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
