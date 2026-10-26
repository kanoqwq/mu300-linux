#!/usr/bin/env python3
"""Run shell commands on the MU300 over its USB CDC-ACM console (ttyGS0 getty).
usage: mu300-serial.py [--user U --password P] 'command; command'   (stdlib only)"""
import argparse, glob, os, select, sys, termios, time, tty

def open_port():
    ports = sorted(glob.glob('/dev/cu.usbmodem*'))
    if not ports:
        sys.exit('no /dev/cu.usbmodem* port (is the MU300 ACM function up?)')
    fd = os.open(ports[-1], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd

def read_until(fd, needles, timeout):
    buf = b''
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            buf += chunk
            if any(n in buf for n in needles):
                return buf
    return buf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--user', default='ubuntu')
    ap.add_argument('--password', default='ubuntu')
    ap.add_argument('--timeout', type=float, default=60)
    ap.add_argument('command')
    a = ap.parse_args()
    fd = open_port()
    os.write(fd, b'\r')
    out = read_until(fd, [b'login:', b'$ ', b'# '], 10)
    if b'login:' in out:
        os.write(fd, a.user.encode() + b'\r')
        read_until(fd, [b'assword:'], 10)
        os.write(fd, a.password.encode() + b'\r')
        read_until(fd, [b'$ ', b'# '], 20)
    marker = b'__MU300_DONE__'
    os.write(fd, b"stty -echo; " + a.command.encode() + b"; echo __MU300_DONE__\r")
    out = read_until(fd, [marker + b'\r\n', marker + b'\n'], a.timeout)
    text = out.decode(errors='replace').replace('\r', '')
    sys.stdout.write(text.split('__MU300_DONE__')[0])

if __name__ == '__main__':
    main()
