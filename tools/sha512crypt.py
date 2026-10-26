#!/usr/bin/env python3
"""SHA-512 crypt ($6$) password hash for /etc/shadow, without openssl or the removed crypt module.
Reads the password from stdin (first line), prints the hash. Implements Ulrich Drepper's SHA-crypt specification."""
import hashlib
import secrets
import sys

ITOA = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'


def sha512_crypt(password, salt=None, rounds=5000):
    pw = password.encode()
    salt = (salt if salt is not None else ''.join(secrets.choice(ITOA) for _ in range(16)))[:16]
    s = salt.encode()
    b = hashlib.sha512(pw + s + pw).digest()
    a = hashlib.sha512(pw + s)
    n = len(pw)
    while n > 64:
        a.update(b)
        n -= 64
    a.update(b[:n])
    n = len(pw)
    while n:
        a.update(b if n & 1 else pw)
        n >>= 1
    c = a.digest()
    dp = hashlib.sha512(pw * len(pw)).digest()
    p = (dp * (len(pw) // 64 + 1))[:len(pw)]
    ds = hashlib.sha512(s * (16 + c[0])).digest()
    sb = (ds * (len(s) // 64 + 1))[:len(s)]
    for r in range(rounds):
        h = hashlib.sha512(p if r & 1 else c)
        if r % 3:
            h.update(sb)
        if r % 7:
            h.update(p)
        h.update(c if r & 1 else p)
        c = h.digest()
    out = []

    def enc(b2, b1, b0, count):
        w = (b2 << 16) | (b1 << 8) | b0
        for _ in range(count):
            out.append(ITOA[w & 0x3f])
            w >>= 6
    order = [(0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48),
             (28, 49, 7), (50, 8, 29), (9, 30, 51), (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13),
             (56, 14, 35), (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41)]
    for i, j, k in order:
        enc(c[i], c[j], c[k], 4)
    enc(0, 0, c[63], 2)
    return f'$6${salt}${"".join(out)}'


if __name__ == '__main__':
    salt = sys.argv[1] if len(sys.argv) > 1 else None
    print(sha512_crypt(sys.stdin.readline().rstrip('\n'), salt))
