#!/usr/bin/env python3
"""Run a command on the MU300 over SSH with password auth (no sshpass needed).
usage: MU300_PASS=ubuntu mu300-ssh.py 'command'   (MU300_HOST: ubuntu@192.168.77.1; a U30 Air is ubuntu@192.168.78.1)"""
import os, pty, select, sys, time
host = os.environ.get('MU300_HOST', 'ubuntu@192.168.77.1')
pw = os.environ.get('MU300_PASS', 'ubuntu').encode()
cmd = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
       '-o', 'PubkeyAuthentication=no', host, ' '.join(sys.argv[1:]) + '; sleep 1']
pid, fd = pty.fork()
if pid == 0:
    os.execvp(cmd[0], cmd)
out = b''; sent = False
while True:
    r, _, _ = select.select([fd], [], [], 120)
    if not r:
        break
    try:
        d = os.read(fd, 65536)
    except OSError:
        break
    if not d:
        break
    out += d
    if not sent and b'assword:' in out:
        os.write(fd, pw + b'\n'); sent = True; out = b''
sys.stdout.write(out.decode(errors='replace').replace('\r', ''))
