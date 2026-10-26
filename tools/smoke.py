#!/usr/bin/env python3
"""The release smoke test: update real boards to a release, reboot them, and check that nothing of theirs changed.

  tools/smoke.py [--config FILE] [--board NAME]... [--dry-run] [--report FILE] RELEASE
  tools/smoke.py --release-dir DIR [--tag TAG] ...         (the same, with a local release directory)

RELEASE is a release tag (vYYYY.MM.DD.N, or an old vYYYY.MM.DD: docs/FINDINGS.md 125) or a local release directory
(release/<tag> of tools/make-release.sh; the tag is its name unless --tag says it). FILE describes the boards
(default tools/smoke.json, which git ignores; tools/smoke.example.json is the example). Per board, in this order:

  1. identity, for every board before any board is touched: the SSH host key's SHA256 fingerprint and the device's
     hostname, the device type (mu300-device), the running system, and the adb serial when one is configured. A board
     that does not match stops the whole run before a single command that changes anything (exit 2).
  2. before: the release (/etc/mu300/image-version), the kernel, the boot image's tag, the mu300 services (systemd
     on Ubuntu, procd on OpenWrt), the hotspot (an AP interface), mobile data (an IPv4 address on sipa_eth*), the
     VPN's state (mu300-vpn status --json, a few fields) and a SHA-256 of every file under /etc/mu300/*vpn*, computed
     on the device. Only the hashes leave the device; the report says "unchanged" or names the files that changed.
  3. the update the config names: mu300-update apply online (MU300_RELEASE=TAG), from a mirror (MU300_RELEASE_URL),
     from a release directory on the device (--from; uploaded from --release-dir when given), or the release's Magisk
     zip installed with magisk --install-module from Android (only for a board with "allow_android": true).
  4. reboot (only when the default boot is Linux), wait for SSH to go and come back (bounded), the identity again,
     the state again, and the comparison: the release is TAG, the kernel as expected, and services, hotspot, mobile
     data and the VPN as before.

The report has one line per check and board, PASS, FAIL or SKIP; the exit status is 1 when any check failed.
--dry-run prints the commands without connecting to anything. Every remote command is a fixed text built from values
the config validation let through (strict patterns, no shell metacharacters); every subprocess has a timeout.
Standard library only (Python 3.8+); needs ssh, ssh-keyscan and scp, and adb for adb boards. See docs/RELEASING.md.
"""
import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = TOP / 'tools' / 'smoke.json'
UPDATE = '/opt/mu300/bin/mu300-update'

# ---- validation: every value that reaches a command line matches one of these, whole (fullmatch, ASCII)
TAG_RE = re.compile(r'v[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[1-9][0-9]?)?', re.ASCII)
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', re.ASCII)
USER_RE = re.compile(r'[a-z_][a-z0-9_-]{0,31}', re.ASCII)
LABEL = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
HOST_RE = re.compile(rf'{LABEL}(?:\.{LABEL})*', re.ASCII)
SERIAL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}', re.ASCII)
FPR_RE = re.compile(r'SHA256:[A-Za-z0-9+/]{43}', re.ASCII)
URL_RE = re.compile(r'(?:https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?|file://)(?:/[A-Za-z0-9._~-]+)+', re.ASCII)
PATH_RE = re.compile(r'(?:/[A-Za-z0-9._-]+)+', re.ASCII)
KERNEL_RE = re.compile(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', re.ASCII)
ZIP_KIND_RE = re.compile(r'(?:openwrt|openwrt-luci|ubuntu-24\.04|ubuntu-26\.04)-k(?:5\.4|6\.18|7\.2)', re.ASCII)
FILE_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', re.ASCII)

DEVICES = ('f50', 'u30air', 'v50')
SYSTEMS = ('ubuntu', 'openwrt', 'openwrt-luci', 'android')
UPDATES = ('online', 'mirror', 'from', 'magisk')
APPLY_ARGS = ('--no-reinstall', '--no-rollback', '--unsigned', '--downgrade', 'ubuntu', 'openwrt', 'openwrt-luci')
TIMEOUTS = {'command': 120, 'apply': 3600, 'down': 300, 'boot': 900, 'settle': 60, 'push': 900, 'install': 1800,
            'poll': 10}
BOARD_KEYS = {'name', 'device', 'system', 'ssh', 'adb_serial', 'android_serial', 'allow_android', 'update',
              'mirror_url', 'from_path', 'magisk_zip', 'apply_args', 'expect_kernel', 'sudo'}
SSH_KEYS = {'host', 'user', 'port', 'fingerprint', 'hostname'}

# the checks of a board, in the order they are reported
CHECKS = ('identity', 'update', 'reboot', 'identity-after', 'release', 'boot-image', 'kernel', 'services', 'hotspot',
          'mobile-data', 'vpn-state', 'vpn-files')


class ConfigError(Exception):
    pass


class Refused(Exception):
    """A board's identity does not match, or a step the config does not allow: nothing (more) is run."""


def _full(rx, value):
    return isinstance(value, str) and rx.fullmatch(value) is not None


def valid_tag(tag):
    return _full(TAG_RE, tag)


def valid_host(host):
    if not isinstance(host, str) or not host or host.startswith('-'):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return _full(HOST_RE, host) and len(host) <= 253


def valid_path(p):
    return _full(PATH_RE, p) and not any(part in ('.', '..') for part in p.split('/'))


def valid_url(u):
    return _full(URL_RE, u) and '/../' not in u + '/' and '/./' not in u + '/'


class Board:
    """One board of the config, every field validated."""

    def __init__(self, d, where):
        def bad(msg):
            raise ConfigError(f'{where}: {msg}')
        if not isinstance(d, dict):
            bad('a board is a JSON object')
        unknown = {k for k in d if not k.startswith('_')} - BOARD_KEYS
        if unknown:
            bad(f'unknown field(s): {", ".join(sorted(unknown))}')
        self.name = d.get('name')
        if not _full(NAME_RE, self.name):
            bad('"name": letters, digits, _ and -, at most 32')
        where = f'board {self.name}'
        self.device = d.get('device')
        if self.device not in DEVICES:
            bad(f'"device" must be one of {", ".join(DEVICES)}')
        self.system = d.get('system')
        if self.system not in SYSTEMS:
            bad(f'"system" must be one of {", ".join(SYSTEMS)}')
        self.update = d.get('update', 'online')
        if self.update not in UPDATES:
            bad(f'"update" must be one of {", ".join(UPDATES)}')
        self.allow_android = d.get('allow_android', False)
        if not isinstance(self.allow_android, bool):
            bad('"allow_android" is true or false')
        ssh = d.get('ssh')
        if not isinstance(ssh, dict):
            bad('"ssh" is required: {"host", "user", "port", "fingerprint", "hostname"}')
        unknown = {k for k in ssh if not k.startswith('_')} - SSH_KEYS
        if unknown:
            bad(f'unknown ssh field(s): {", ".join(sorted(unknown))}')
        self.host = ssh.get('host')
        if not valid_host(self.host):
            bad('"ssh.host": an IP address or a host name')
        self.user = ssh.get('user', 'root')
        if not _full(USER_RE, self.user):
            bad('"ssh.user": a plain user name')
        self.port = ssh.get('port', 22)
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            bad('"ssh.port": a number from 1 to 65535')
        self.fingerprint = ssh.get('fingerprint')
        if not _full(FPR_RE, self.fingerprint):
            bad('"ssh.fingerprint" is required: SHA256:<43 characters>, as ssh-keygen -lf prints it')
        self.hostname = ssh.get('hostname')
        if self.hostname is not None and not _full(HOST_RE, self.hostname):
            bad('"ssh.hostname": the device\'s host name (/proc/sys/kernel/hostname)')
        self.sudo = d.get('sudo', self.user != 'root')
        if not isinstance(self.sudo, bool):
            bad('"sudo" is true or false')
        self.adb_serial = d.get('adb_serial')
        if self.adb_serial is not None and not _full(SERIAL_RE, self.adb_serial):
            bad('"adb_serial": the serial adb devices shows in Linux (MU300LINUX-...)')
        self.android_serial = d.get('android_serial')
        if self.android_serial is not None and not _full(SERIAL_RE, self.android_serial):
            bad('"android_serial": the serial adb devices shows in Android')
        self.mirror_url = d.get('mirror_url')
        if self.mirror_url is not None:
            self.mirror_url = self.mirror_url.rstrip('/') if isinstance(self.mirror_url, str) else self.mirror_url
            if not valid_url(self.mirror_url):
                bad('"mirror_url": http(s):// or file:// and a path of letters, digits and . _ ~ - (the tag is added)')
        self.from_path = d.get('from_path')
        if self.from_path is not None and not valid_path(self.from_path):
            bad('"from_path": an absolute path of letters, digits and . _ -')
        self.magisk_zip = d.get('magisk_zip')
        if self.magisk_zip is not None and not _full(ZIP_KIND_RE, self.magisk_zip):
            bad('"magisk_zip": the zip\'s system and kernel, e.g. openwrt-luci-k6.18 or ubuntu-24.04-k7.2')
        self.apply_args = d.get('apply_args', [])
        if not isinstance(self.apply_args, list) or any(a not in APPLY_ARGS for a in self.apply_args):
            bad(f'"apply_args": a list of {", ".join(APPLY_ARGS)}')
        self.expect_kernel = d.get('expect_kernel')
        if self.expect_kernel is not None and not _full(KERNEL_RE, self.expect_kernel):
            bad('"expect_kernel": a kernel version prefix such as 6.18 or 7.2.10')
        # what each update path needs, and the one rule that is never relaxed: Android only when allowed
        if self.update == 'mirror' and not self.mirror_url:
            bad('"update": "mirror" needs "mirror_url"')
        if self.update == 'from' and not self.from_path:
            bad('"update": "from" needs "from_path" (a directory on the device)')
        if self.update == 'magisk':
            if not self.allow_android:
                bad('the Magisk zip path goes through Android: it needs "allow_android": true')
            if not self.magisk_zip or not self.android_serial:
                bad('"update": "magisk" needs "magisk_zip" and "android_serial"')
        if self.system == 'android' and self.update != 'magisk':
            bad('a board in Android ("system": "android") can only take the Magisk zip')
        if self.update != 'magisk' and self.magisk_zip:
            bad('"magisk_zip" is for "update": "magisk" only')
        if self.magisk_zip and not self.in_android and self.system_after != self.system:
            bad(f'"magisk_zip" {self.magisk_zip} installs {self.system_after}, the board runs {self.system}')

    @property
    def system_after(self):
        """The system that runs after the update: the zip's for the Magisk path, else the board's."""
        if self.magisk_zip:
            z = self.magisk_zip.rsplit('-k', 1)[0]
            return 'ubuntu' if z.startswith('ubuntu-') else z
        return self.system

    @property
    def in_android(self):
        return self.system == 'android'


def _no_duplicates(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ConfigError(f'duplicate key "{k}"')
        d[k] = v
    return d


def load_config(path):
    """(boards, timeouts) from the JSON file at PATH; ConfigError says what is wrong."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError as e:
        raise ConfigError(f'cannot read {path}: {e.strerror}')
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates)
    except ValueError as e:
        raise ConfigError(f'{path}: not valid JSON ({e})')
    return parse_config(data, str(path))


def parse_config(data, where='config'):
    if not isinstance(data, dict):
        raise ConfigError(f'{where}: a JSON object with "boards"')
    unknown = {k for k in data if not k.startswith('_')} - {'boards', 'timeouts'}
    if unknown:
        raise ConfigError(f'{where}: unknown field(s): {", ".join(sorted(unknown))}')
    raw = data.get('boards')
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f'{where}: "boards" is a list with at least one board')
    boards = [Board(b, f'{where}: board {i + 1}') for i, b in enumerate(raw)]
    names = [b.name for b in boards]
    for n in set(names):
        if names.count(n) > 1:
            raise ConfigError(f'{where}: two boards are called {n}')
    timeouts = dict(TIMEOUTS)
    t = data.get('timeouts', {})
    if not isinstance(t, dict):
        raise ConfigError(f'{where}: "timeouts" is an object of seconds')
    for k, v in t.items():
        if k.startswith('_'):
            continue
        if k not in TIMEOUTS:
            raise ConfigError(f'{where}: unknown timeout "{k}" ({", ".join(TIMEOUTS)})')
        if type(v) is not int or not 0 <= v <= 86400:
            raise ConfigError(f'{where}: timeout "{k}" is a whole number of seconds up to 86400')
        timeouts[k] = v
    return boards, timeouts


# ---- what runs on the device. Fixed texts: no value of the config is ever part of them.
IDENTITY_SCRIPT = r'''echo "hostname=$(cat /proc/sys/kernel/hostname 2>/dev/null)"
echo "device=$(/opt/mu300/bin/mu300-device --json 2>/dev/null | head -n1)"
'''

STATE_SCRIPT = r'''PATH="$PATH:/opt/mu300/bin:/usr/sbin:/sbin"
echo "release=$(head -n1 /etc/mu300/image-version 2>/dev/null)"
echo "kernel=$(uname -r)"
echo "device=$(/opt/mu300/bin/mu300-device 2>/dev/null | head -n1)"
echo "hostname=$(cat /proc/sys/kernel/hostname 2>/dev/null)"
echo "default_boot=$(cat /etc/mu300/default-boot 2>/dev/null)"
echo "boot_tag=$(cat /mnt/mu300-disk/boot/installed.tag 2>/dev/null)"
if command -v systemctl >/dev/null 2>&1; then
    systemctl list-units --all --plain --no-legend --no-pager 'mu300-*' 2>/dev/null | awk '{print "svc", $1, $3}'
else
    for f in /etc/init.d/mu300-*; do
        [ -x "$f" ] || continue
        if "$f" enabled 2>/dev/null; then e=enabled; else e=disabled; fi
        if "$f" running >/dev/null 2>&1; then r=running; else r=stopped; fi
        echo "svc ${f##*/} $e/$r"
    done
fi
iw dev 2>/dev/null | awk '$1 == "Interface" {i = $2} $1 == "type" && $2 == "AP" {print "ap", i}'
ip -4 -o addr show 2>/dev/null | awk '$2 ~ /^sipa_eth/ {print "wan", $2}'
if command -v mu300-vpn >/dev/null 2>&1; then echo "vpn=$(mu300-vpn status --json 2>/dev/null | tr -d '\n')"
else echo "vpn="; fi
find /etc/mu300 -path '*vpn*' -type f -exec sha256sum {} \; 2>/dev/null | awk '{print "vpnhash", $0}'
echo "end=1"
'''

# the default boot is checked again right before: a reboot must never go to Android unless the config allows it
REBOOT_SCRIPT = r'''[ "$(cat /etc/mu300/default-boot 2>/dev/null)" = linux ] || { echo "the default boot is not linux: not rebooting" >&2; exit 3; }
sync
(sleep 2; reboot) >/dev/null 2>&1 </dev/null &
'''

TO_ANDROID_SCRIPT = r'''/opt/mu300/bin/mu300-next-boot android || exit 1
sync
(sleep 2; reboot) >/dev/null 2>&1 </dev/null &
'''


def root(board, cmd):
    return f'sudo -n {cmd}' if board.sudo else cmd


def zip_name(board, tag):
    return f'mu300-magisk-{tag}-{board.magisk_zip}.zip'


def apply_command(board, tag):
    """The mu300-update command of BOARD's update path (online, mirror, from)."""
    env = [f'MU300_RELEASE={tag}']
    if board.update == 'mirror':
        env.append(f'MU300_RELEASE_URL={board.mirror_url}/{tag}')
    args = ['apply']
    if board.update == 'from':
        args += ['--from', board.from_path]
    args += board.apply_args
    return root(board, ' '.join(['env'] + [shlex.quote(e) for e in env] + [UPDATE] + [shlex.quote(a) for a in args]))


def ssh_key_fingerprint(line):
    """'SHA256:...' of one known_hosts / ssh-keyscan line, as ssh-keygen -lf prints it; None for anything else."""
    parts = line.split()
    if len(parts) < 3 or line.startswith('#'):
        return None
    try:
        blob = base64.b64decode(parts[2], validate=True)
    except (binascii.Error, ValueError):
        return None
    return 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')


# ---- the state of a board, parsed. Only what is compared is kept: no file contents, the VPN's hashes by path.
VPN_FIELDS = (('enabled',), ('running',), ('kill_switch', 'setting'), ('profile', 'id'), ('engine', 'name'),
              ('tunnel', 'up'))


def vpn_summary(text):
    if not text:
        return None
    try:
        st = json.loads(text)
    except ValueError:
        return {'status': 'unreadable'}
    if not isinstance(st, dict):
        return {'status': 'unreadable'}
    out = {}
    for path in VPN_FIELDS:
        v = st
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        out['.'.join(path)] = v
    return out


def parse_state(text):
    s = {'release': '', 'kernel': '', 'device': '', 'hostname': '', 'default_boot': '', 'boot_tag': '',
         'services': {}, 'ap': [], 'wan': [], 'vpn': None, 'vpn_hashes': {}, 'complete': False}
    for line in text.splitlines():
        if line.startswith('svc '):
            p = line.split()
            if len(p) == 3:
                s['services'][p[1]] = p[2]
        elif line.startswith('ap '):
            s['ap'].append(line[3:].strip())
        elif line.startswith('wan '):
            s['wan'].append(line[4:].strip())
        elif line.startswith('vpnhash '):
            p = line[8:].split(None, 1)
            if len(p) == 2:
                s['vpn_hashes'][p[1].lstrip('*').strip()] = p[0]
        elif line.startswith('vpn='):
            s['vpn'] = vpn_summary(line[4:].strip())
        elif line == 'end=1':
            s['complete'] = True
        else:
            k, sep, v = line.partition('=')
            if sep and k in ('release', 'kernel', 'device', 'hostname', 'default_boot', 'boot_tag'):
                s[k] = v.strip()
    return s


def parse_identity(text):
    out = {'hostname': '', 'device': '', 'system': ''}
    for line in text.splitlines():
        k, sep, v = line.partition('=')
        if not sep:
            continue
        v = v.strip()
        if k == 'hostname':
            out['hostname'] = v
        elif k == 'device':
            try:
                j = json.loads(v)
            except ValueError:
                j = None
            if isinstance(j, dict):     # mu300-device --json (docs/ADB-INTERFACE.md)
                out['device'] = str(j.get('device', ''))
                out['system'] = str(j.get('system', ''))
            else:                       # a release before --json printed the device alone
                out['device'] = v.split()[0] if v else ''
    return out


# ---- transports: the real one runs ssh/scp/ssh-keyscan/adb; tests put a fake in its place
class Result:
    def __init__(self, rc, out='', err=''):
        self.rc, self.out, self.err = rc, out, err

    @property
    def ok(self):
        return self.rc == 0


class RealTransport:
    """ssh, scp, ssh-keyscan and adb with a timeout on every call. ssh runs only against a host whose key was pinned
    (pin) from the config's fingerprint: StrictHostKeyChecking=yes with a known_hosts file of that key alone."""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory(prefix='mu300-smoke-')
        self._known = {}

    def _exec(self, argv, timeout, input=None):
        try:
            r = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout,
                               stdin=None if input is not None else subprocess.DEVNULL)
            return Result(r.returncode, r.stdout, r.stderr)
        except subprocess.TimeoutExpired:
            return Result(124, '', f'timed out after {timeout} s')
        except OSError as e:
            return Result(127, '', str(e))

    def ssh_host_keys(self, board, timeout=30):
        r = self._exec(['ssh-keyscan', '-T', '10', '-p', str(board.port), board.host], timeout)
        return [l for l in r.out.splitlines() if l.strip() and not l.startswith('#')]

    def pin(self, board, lines):
        f = Path(self._dir.name) / f'known_hosts.{board.name}'
        f.write_text(''.join(l + '\n' for l in lines))
        self._known[board.name] = f

    def _ssh_opts(self, board):
        if board.name not in self._known:
            raise Refused(f'{board.name}: no pinned host key (the identity check runs first)')
        return ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={self._known[board.name]}',
                '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
                '-o', 'ServerAliveCountMax=4', '-o', 'LogLevel=ERROR']

    def ssh(self, board, command, stdin=None, timeout=120):
        return self._exec(['ssh'] + self._ssh_opts(board) + ['-p', str(board.port), '-l', board.user, board.host,
                                                              command], timeout, input=stdin if stdin is not None else '')

    def ssh_ok(self, board, timeout=30):
        return self.ssh(board, 'true', timeout=timeout).ok

    def scp_put(self, board, files, remote_dir, timeout):
        host = f'[{board.host}]' if ':' in board.host else board.host
        return self._exec(['scp', '-q'] + self._ssh_opts(board) + ['-P', str(board.port)] + [str(f) for f in files] +
                          [f'{board.user}@{host}:{remote_dir}/'], timeout)

    def adb_devices(self, timeout=30):
        r = self._exec(['adb', 'devices'], timeout)
        out = {}
        for line in r.out.splitlines()[1:]:
            p = line.split()
            if len(p) >= 2:
                out[p[0]] = p[1]
        return out

    def adb(self, serial, args, timeout=120):
        return self._exec(['adb', '-s', serial] + list(args), timeout)


class Clock:
    def now(self):
        return time.monotonic()

    def sleep(self, s):
        time.sleep(s)


# ---- the run
class Check:
    def __init__(self, board, name, status, detail=''):
        self.board, self.name, self.status, self.detail = board, name, status, detail

    def line(self):
        return f'- {self.status} {self.board} {self.name}' + (f': {self.detail}' if self.detail else '')


def _active(state):
    return state in ('active', 'activating', 'reloading') or state.endswith('/running')


def _kernel_mm(k):
    m = re.match(r'([0-9]+\.[0-9]+)', k or '')
    return m.group(1) if m else ''


def _kernel_matches(kernel, want):
    return kernel == want or kernel.startswith(want + '.') or kernel.startswith(want + '-')


def compare(board, tag, before, after):
    """The checks after the update: [Check]. BEFORE is None for a board that started in Android."""
    c = []
    n = board.name
    was = f' (was {before["release"] or "unknown"})' if before else ''
    if after['release'] == tag:
        c.append(Check(n, 'release', 'PASS', tag + was))
    else:
        c.append(Check(n, 'release', 'FAIL', f'{after["release"] or "unknown"}, expected {tag}{was}'))
    if after['boot_tag'] == tag:
        c.append(Check(n, 'boot-image', 'PASS', tag))
    elif board.update == 'magisk':
        c.append(Check(n, 'boot-image', 'SKIP', f'{after["boot_tag"] or "none"} (the zip writes the boot image itself)'))
    else:
        c.append(Check(n, 'boot-image', 'FAIL', f'{after["boot_tag"] or "none"}, expected {tag}'))
    want = board.expect_kernel or (board.magisk_zip.rsplit('-k', 1)[1] if board.magisk_zip else None)
    if want:
        ok = _kernel_matches(after['kernel'], want)
        c.append(Check(n, 'kernel', 'PASS' if ok else 'FAIL', f'{after["kernel"]}' + ('' if ok else f', expected {want}')))
    elif before:
        ok = _kernel_mm(after['kernel']) == _kernel_mm(before['kernel'])
        c.append(Check(n, 'kernel', 'PASS' if ok else 'FAIL', f'{after["kernel"]} (was {before["kernel"]})'))
    else:
        c.append(Check(n, 'kernel', 'SKIP', f'{after["kernel"]} (nothing to compare with)'))
    failed = sorted(k for k, v in after['services'].items() if v == 'failed')
    lost = sorted(k for k, v in (before or {}).get('services', {}).items() if _active(v)
                  and not _active(after['services'].get(k, 'gone')))
    if failed or lost:
        parts = ([f'failed: {", ".join(failed)}'] if failed else []) + ([f'no longer running: {", ".join(lost)}'] if lost else [])
        c.append(Check(n, 'services', 'FAIL', '; '.join(parts)))
    else:
        up = sum(1 for v in after['services'].values() if _active(v))
        c.append(Check(n, 'services', 'PASS', f'{up} running, none failed' + (', none lost' if before else '')))
    for name, key, down_without_before in (('hotspot', 'ap', 'FAIL'), ('mobile-data', 'wan', 'SKIP')):
        now = after[key]
        if now:
            c.append(Check(n, name, 'PASS', 'up (' + ', '.join(now) + ')'))
        elif before is None:
            c.append(Check(n, name, down_without_before, 'down (nothing to compare with)'))
        elif before[key]:
            c.append(Check(n, name, 'FAIL', 'down, was up (' + ', '.join(before[key]) + ')'))
        else:
            c.append(Check(n, name, 'SKIP', 'down, and was down before'))
    if before is None:
        c.append(Check(n, 'vpn-state', 'SKIP', 'nothing to compare with'))
        c.append(Check(n, 'vpn-files', 'SKIP', 'nothing to compare with'))
        return c
    bv, av = before['vpn'], after['vpn']
    if bv is None and av is None:
        c.append(Check(n, 'vpn-state', 'SKIP', 'no VPN module'))
    elif bv == av:
        c.append(Check(n, 'vpn-state', 'PASS', 'unchanged (' + ('running' if av.get('running') else 'not running') + ')'))
    elif av is None:
        c.append(Check(n, 'vpn-state', 'FAIL', 'the VPN module is gone'))
    else:
        diff = sorted(k for k in set(bv or {}) | set(av) if (bv or {}).get(k) != av.get(k))
        c.append(Check(n, 'vpn-state', 'FAIL', 'changed: ' + ', '.join(diff)))
    bh, ah = before['vpn_hashes'], after['vpn_hashes']
    changed = sorted(p for p in bh if p in ah and ah[p] != bh[p])
    gone = sorted(p for p in bh if p not in ah)
    new = sorted(p for p in ah if p not in bh)
    if not (changed or gone or new):
        c.append(Check(n, 'vpn-files', 'PASS' if bh else 'SKIP', f'unchanged ({len(bh)} files)' if bh else 'no VPN files'))
    else:
        parts = []
        for label, ps in (('changed', changed), ('gone', gone), ('new', new)):
            if ps:
                parts.append(f'{label}: {len(ps)} files: {", ".join(ps)}')
        c.append(Check(n, 'vpn-files', 'FAIL', '; '.join(parts)))
    return c


class Harness:
    def __init__(self, boards, timeouts, tag, transport, clock=None, release_dir=None, log=None):
        self.boards, self.t, self.tag = boards, timeouts, tag
        self.tr = transport
        self.clock = clock or Clock()
        self.release_dir = Path(release_dir) if release_dir else None
        self.log = log or (lambda msg: print(msg, file=sys.stderr))

    # -- local checks that need no device: refuse early
    def local_problems(self):
        out = []
        for b in self.boards:
            if b.update == 'magisk':
                if not self.release_dir:
                    out.append(f'{b.name}: the Magisk path needs the zip: --release-dir DIR')
                elif not (self.release_dir / zip_name(b, self.tag)).is_file():
                    out.append(f'{b.name}: {zip_name(b, self.tag)} is not in {self.release_dir}')
        return out

    # -- identity
    def identity_problems(self, b, after=False):
        """[] when BOARD is the board the config describes; otherwise what differs. Pins the host key."""
        problems = []
        if b.in_android and not after:
            devs = self.tr.adb_devices()
            if devs.get(b.android_serial) != 'device':
                return [f'adb serial {b.android_serial} is not connected (adb devices: {devs.get(b.android_serial, "absent")})']
            r = self.tr.adb(b.android_serial, ['shell', 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300'],
                            timeout=self.t['command'])
            if 'mu300' in r.out:
                problems.append(f'{b.android_serial} runs MU300 Linux, the config says Android')
            return problems
        keys = self.tr.ssh_host_keys(b)
        match = [k for k in keys if ssh_key_fingerprint(k) == b.fingerprint]
        if not match:
            return [f'ssh host key of {b.host}:{b.port} is not {b.fingerprint} (' +
                    (f'it offers {len(keys)} other key(s)' if keys else 'no key: not reachable') + ')']
        self.tr.pin(b, match)
        r = self.tr.ssh(b, 'sh -s', stdin=IDENTITY_SCRIPT, timeout=self.t['command'])
        if not r.ok:
            return [f'ssh to {b.user}@{b.host}:{b.port} failed (exit {r.rc})']
        ident = parse_identity(r.out)
        if b.hostname and ident['hostname'] != b.hostname:
            problems.append(f'hostname is {ident["hostname"] or "empty"}, expected {b.hostname}')
        if ident['device'] != b.device:
            problems.append(f'device is {ident["device"] or "unknown"}, expected {b.device}')
        want_system = b.system_after if after else b.system
        if ident['system'] and ident['system'] != want_system:
            problems.append(f'running system is {ident["system"]}, expected {want_system}')
        if b.adb_serial:
            st = self.tr.adb_devices().get(b.adb_serial)
            if st != 'device':
                problems.append(f'adb serial {b.adb_serial} is not connected (adb devices: {st or "absent"})')
        return problems

    def preflight(self):
        """{board name: [problem]} for every board that does not match; empty when all of them do."""
        bad = {}
        for b in self.boards:
            p = self.identity_problems(b)
            if p:
                bad[b.name] = p
        return bad

    # -- state
    def state(self, b):
        r = self.tr.ssh(b, root(b, 'sh -s'), stdin=STATE_SCRIPT, timeout=self.t['command'])
        s = parse_state(r.out) if r.ok else None
        return s if s and s['complete'] else None

    # -- waiting
    def wait(self, pred, timeout):
        start = self.clock.now()
        while True:
            if pred():
                return True
            if self.clock.now() - start >= timeout:
                return False
            self.clock.sleep(self.t['poll'])

    def wait_ssh(self, b, up, timeout):
        return self.wait(lambda: self.tr.ssh_ok(b) == up, timeout)

    def wait_android(self, b):
        def booted():
            if self.tr.adb_devices().get(b.android_serial) != 'device':
                return False
            return self.tr.adb(b.android_serial, ['shell', 'getprop sys.boot_completed'], timeout=30).out.strip() == '1'
        return self.wait(booted, self.t['boot'])

    def may_send_to_android(self, b):
        # the config validation refuses this already; here again, at the one place that does it
        if not b.allow_android:
            raise Refused(f'{b.name}: not sent to Android ("allow_android" is not true)')

    # -- one board
    def run_board(self, b):
        checks = []
        n = b.name

        def stop(name, detail):
            checks.append(Check(n, name, 'FAIL', detail))
            done = {c.name for c in checks}
            checks.extend(Check(n, x, 'SKIP', 'not reached') for x in CHECKS if x not in done)
            return checks

        checks.append(Check(n, 'identity', 'PASS', f'{b.fingerprint[:15]}..., {b.device}' +
                            (f', {b.hostname}' if b.hostname else '')))
        before = None
        if not b.in_android:
            before = self.state(b)
            if before is None:
                return stop('update', 'could not read the state before the update')
            if before['default_boot'] != 'linux' and b.update != 'magisk':
                return stop('update', f'the default boot is {before["default_boot"] or "unknown"}, not linux: '
                                      'a reboot would go to Android (mu300-next-boot linux first)')
        if b.update == 'magisk':
            r = self.flash_zip(b)
        else:
            r = self.apply(b)
        if r:
            return stop('update', r)
        checks.append(Check(n, 'update', 'PASS', b.update + (f' ({zip_name(b, self.tag)})' if b.update == 'magisk' else '')))
        if b.update != 'magisk':
            rr = self.tr.ssh(b, root(b, 'sh -s'), stdin=REBOOT_SCRIPT, timeout=self.t['command'])
            if rr.rc == 3:
                return stop('reboot', 'the default boot is not linux after the update: not rebooted')
            if not self.wait_ssh(b, False, self.t['down']):
                return stop('reboot', f'ssh still answers {self.t["down"]} s after the reboot command')
        if not self.wait_ssh(b, True, self.t['boot']):
            keys = self.tr.ssh_host_keys(b)
            why = ('the host key changed' if keys and not any(ssh_key_fingerprint(k) == b.fingerprint for k in keys)
                   else 'no answer')
            return stop('reboot', f'ssh did not come back within {self.t["boot"]} s ({why})')
        checks.append(Check(n, 'reboot', 'PASS', 'ssh is back'))
        p = self.identity_problems(b, after=True)
        if p:
            return stop('identity-after', '; '.join(p))
        checks.append(Check(n, 'identity-after', 'PASS', 'the same board'))
        if self.t['settle']:
            self.clock.sleep(self.t['settle'])
        after = self.state(b)
        if after is None:
            return stop('release', 'could not read the state after the update')
        checks.extend(compare(b, self.tag, before, after))
        return checks

    def apply(self, b):
        if b.update == 'from' and self.release_dir:
            files = sorted(f for f in self.release_dir.iterdir() if f.is_file() and _full(FILE_RE, f.name))
            if not files:
                return f'{self.release_dir} has no files to upload'
            r = self.tr.ssh(b, f'mkdir -p {shlex.quote(b.from_path)}', timeout=self.t['command'])
            if not r.ok:
                return f'could not make {b.from_path} on the device (exit {r.rc})'
            r = self.tr.scp_put(b, files, b.from_path, timeout=self.t['push'])
            if not r.ok:
                return f'upload to {b.from_path} failed (exit {r.rc})'
        r = self.tr.ssh(b, apply_command(b, self.tag), timeout=self.t['apply'])
        if not r.ok:
            last = (r.err.strip() or r.out.strip()).splitlines()[-1:] or ['']
            return f'mu300-update apply failed (exit {r.rc}): {last[0][:200]}'
        return None

    def flash_zip(self, b):
        self.may_send_to_android(b)
        if not b.in_android:
            r = self.tr.ssh(b, root(b, 'sh -s'), stdin=TO_ANDROID_SCRIPT, timeout=self.t['command'])
            if not r.ok:
                return f'mu300-next-boot android failed (exit {r.rc})'
            if not self.wait_ssh(b, False, self.t['down']):
                return f'ssh still answers {self.t["down"]} s after the switch to Android'
        if not self.wait_android(b):
            return f'Android ({b.android_serial}) did not come up within {self.t["boot"]} s'
        r = self.tr.adb(b.android_serial, ['shell', 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300'],
                        timeout=self.t['command'])
        if 'mu300' in r.out:
            return f'{b.android_serial} is not in Android'
        name = zip_name(b, self.tag)
        dest = f'/data/local/tmp/{name}'
        r = self.tr.adb(b.android_serial, ['push', str(self.release_dir / name), dest], timeout=self.t['push'])
        if not r.ok:
            return f'adb push failed (exit {r.rc})'
        r = self.tr.adb(b.android_serial, ['shell', f"su -c 'magisk --install-module {dest}'"], timeout=self.t['install'])
        if not r.ok:
            last = (r.out.strip() or r.err.strip()).splitlines()[-1:] or ['']
            return f'magisk --install-module failed (exit {r.rc}): {last[0][:200]}'
        self.tr.adb(b.android_serial, ['shell', f'rm -f {dest}'], timeout=self.t['command'])
        self.tr.adb(b.android_serial, ['reboot'], timeout=self.t['command'])
        return None

    def run(self):
        """All boards: (checks, refused). REFUSED is {board: [problem]} when an identity did not match; then nothing
        was run on any board."""
        bad = self.preflight()
        if bad:
            return [], bad
        checks = []
        for b in self.boards:
            self.log(f'{b.name}: {b.update} update to {self.tag}')
            checks.extend(self.run_board(b))
        return checks, {}

    # -- the plan (--dry-run): the same commands, printed, nothing connected
    def plan(self):
        lines = []
        for b in self.boards:
            ssh = f'ssh -p {b.port} {b.user}@{b.host}'
            lines.append(f'{b.name} ({b.device}, {b.system}, update: {b.update})')
            if b.in_android:
                lines.append(f'  identity: adb devices shows {b.android_serial}; it is in Android (no /opt/mu300)')
            else:
                lines.append(f'  identity: ssh-keyscan -p {b.port} {b.host} offers {b.fingerprint}; then '
                             f'{ssh} sh -s < identity script (hostname {b.hostname or "not checked"}, device {b.device}, '
                             f'system {b.system})' + (f'; adb devices shows {b.adb_serial}' if b.adb_serial else ''))
                lines.append(f'  before: {ssh} {root(b, "sh -s")} < state script (release, kernel, services, hotspot, '
                             'mobile data, VPN state and file hashes)')
            if b.update == 'magisk':
                name = zip_name(b, self.tag)
                if not b.in_android:
                    lines.append(f'  to Android: {ssh} {root(b, "sh -s")} < mu300-next-boot android; reboot')
                lines.append(f'  wait for Android: adb -s {b.android_serial} shell getprop sys.boot_completed')
                lines.append(f'  adb -s {b.android_serial} push {self.release_dir / name if self.release_dir else name} /data/local/tmp/{name}')
                lines.append(f"  adb -s {b.android_serial} shell \"su -c 'magisk --install-module /data/local/tmp/{name}'\"")
                lines.append(f'  adb -s {b.android_serial} reboot')
            else:
                if b.update == 'from' and self.release_dir:
                    lines.append(f'  upload: {ssh} mkdir -p {b.from_path}; scp -P {b.port} {self.release_dir}/* '
                                 f'{b.user}@{b.host}:{b.from_path}/')
                lines.append(f'  update: {ssh} {apply_command(b, self.tag)}')
                lines.append(f'  reboot: {ssh} {root(b, "sh -s")} < reboot script (only when the default boot is linux)')
                lines.append(f'  wait: ssh gone within {self.t["down"]} s')
            lines.append(f'  wait: ssh back within {self.t["boot"]} s; identity again; {self.t["settle"]} s to settle')
            lines.append(f'  after: {ssh} {root(b, "sh -s")} < state script; compare with before')
        return lines


def report(tag, checks, refused=None):
    lines = [f'# Release smoke test: {tag}', '']
    if refused:
        for name, problems in refused.items():
            for p in problems:
                lines.append(f'- REFUSED {name} identity: {p}')
        lines += ['', 'Nothing was run: a board is not the one the config describes.']
        return '\n'.join(lines) + '\n'
    lines += [c.line() for c in checks]
    counts = {s: sum(1 for c in checks if c.status == s) for s in ('PASS', 'FAIL', 'SKIP')}
    lines += ['', f'{counts["PASS"]} passed, {counts["FAIL"]} failed, {counts["SKIP"]} skipped']
    return '\n'.join(lines) + '\n'


def exit_code(checks, refused=None):
    if refused:
        return 2
    return 1 if any(c.status == 'FAIL' for c in checks) else 0


def resolve_release(release, tag=None, release_dir=None):
    """(tag, release_dir) from the RELEASE argument (a tag or a directory), --tag and --release-dir."""
    if release and Path(release).is_dir():
        release_dir = release_dir or release
        tag = tag or Path(release).resolve().name
    elif release:
        if tag and tag != release:
            raise ConfigError(f'two tags: {release} and --tag {tag}')
        tag = release
    elif release_dir:
        tag = tag or Path(release_dir).resolve().name
    if not valid_tag(tag):
        raise ConfigError(f'not a release tag: {tag!r} (vYYYY.MM.DD.N, docs/FINDINGS.md 125)')
    if release_dir and not Path(release_dir).is_dir():
        raise ConfigError(f'{release_dir} is not a directory')
    return tag, release_dir


def main(argv=None, transport=None, clock=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog='tools/smoke.py', description='Update boards to a release and check them.')
    ap.add_argument('release', nargs='?', help='a release tag or a local release directory')
    ap.add_argument('--tag', help='the tag, when the release directory is not named after it')
    ap.add_argument('--release-dir', help='a local release directory (the Magisk zips, or the files for "from")')
    ap.add_argument('--config', default=str(DEFAULT_CONFIG), help=f'the boards (default {DEFAULT_CONFIG.relative_to(TOP)})')
    ap.add_argument('--board', action='append', help='only this board (more than once for several)')
    ap.add_argument('--dry-run', action='store_true', help='print the plan; connect to nothing')
    ap.add_argument('--report', help='also write the report to this file')
    a = ap.parse_args(argv)
    try:
        if not a.release and not a.release_dir:
            raise ConfigError('which release? a tag, or a release directory')
        tag, release_dir = resolve_release(a.release, a.tag, a.release_dir)
        boards, timeouts = load_config(a.config)
        if a.board:
            unknown = [x for x in a.board if x not in {b.name for b in boards}]
            if unknown:
                raise ConfigError(f'no such board in {a.config}: {", ".join(unknown)}')
            boards = [b for b in boards if b.name in a.board]
        h = Harness(boards, timeouts, tag, transport, clock=clock, release_dir=release_dir)
        local = h.local_problems()
        if local:
            raise ConfigError('; '.join(local))
    except ConfigError as e:
        print(f'smoke: {e}', file=sys.stderr)
        return 2
    if a.dry_run:
        out.write(f'# Plan: smoke test of {tag} (dry run: nothing is connected)\n\n' + '\n'.join(h.plan()) + '\n')
        return 0
    if h.tr is None:
        h.tr = RealTransport()
    try:
        checks, refused = h.run()
    except Refused as e:
        print(f'smoke: refused: {e}', file=sys.stderr)
        return 2
    text = report(tag, checks, refused)
    out.write(text)
    if a.report:
        Path(a.report).write_text(text)
    return exit_code(checks, refused)


if __name__ == '__main__':
    sys.exit(main())
