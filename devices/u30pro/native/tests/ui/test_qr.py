"""Encode strings with tools/ui/qr.h and decode them with OpenCV.  usage: python tests/ui/test_qr.py <qr_dump binary>"""
import subprocess
import sys

import cv2
import numpy as np

exe = sys.argv[1]
cases = [
    'a', 'hello', 'WIFI:T:WPA;S:U30Pro;P:12345678;;', 'WIFI:T:WPA;S:' + 'x' * 30 + ';P:' + 'y' * 20 + ';;',
    'https://example.com/' + 'a' * 60, 'z' * 100, 'Q' * 150, ''.join(chr(97 + (i * 7 + i // 5) % 26) for i in range(200)), '0123456789' * 25 + 'abcde', 'v' * 170, 'w' * 190, 'u' * 215, 'k' * 230, 'j' * 250,
    'WIFI:T:WPA;S:中文热点;P:密码密码1234;;',
]
fails = 0
det = cv2.QRCodeDetector()
for text in cases:
    out = subprocess.run(exe.split() + [text], capture_output=True, text=True, encoding='utf-8').stdout.split('\n')
    n = int(out[0])
    if n == 0:
        print('FAIL encode', len(text.encode()), 'bytes'); fails += 1; continue
    m = np.array([[int(c) for c in row] for row in out[1:1 + n]], dtype=np.uint8)
    img = np.pad(1 - m, 4, constant_values=1) * 255
    img = np.kron(img, np.ones((6, 6), dtype=np.uint8)).astype(np.uint8)
    got, pts, _ = det.detectAndDecode(img)
    ok = got == text
    print('PASS' if ok else 'FAIL', 'version-size', n, 'len', len(text.encode()), '' if ok else repr(got)[:60])
    fails += 0 if ok else 1
sys.exit(1 if fails else 0)




