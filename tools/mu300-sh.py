#!/usr/bin/env python3
"""Run a command in the already logged-in shell on the MU300 USB serial console (ttyGS0).
usage: mu300-sh.py [--timeout N] 'command'   -- prints the command output (ANSI/OSC stripped)."""
import glob, os, re, select, sys, time, tty

def main():
    timeout = 60.0
    args = sys.argv[1:]
    if args[:1] == ['--timeout']:
        timeout = float(args[1]); args = args[2:]
    cmd = ' '.join(args)
    fd = os.open(sorted(glob.glob('/dev/cu.usbmodem*'))[-1], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    tag = 'MU300_%d' % int(time.time() * 1000)
    os.write(fd, ("stty -echo 2>/dev/null; { %s ; } 2>&1; echo %s_END\r" % (cmd, tag)).encode())
    buf = b''
    end = time.time() + timeout
    while time.time() < end and (tag + '_END').encode() not in buf.split(b'echo ' + tag.encode())[-1]:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                buf += os.read(fd, 65536)
            except BlockingIOError:
                pass
    text = buf.decode(errors='replace')
    text = re.sub(r'\x1b\][^\x1b\x07]*(\x1b\\|\x07)', '', text)   # OSC (shell integration)
    text = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', text).replace('\r', '')
    text = text.split(tag + '_END')[-2] if text.count(tag + '_END') >= 2 else text.split(tag + '_END')[0]
    lines = [l for l in text.split('\n') if 'stty -echo' not in l and not l.endswith('$ ')]
    sys.stdout.write('\n'.join(lines).strip() + '\n')

main()
