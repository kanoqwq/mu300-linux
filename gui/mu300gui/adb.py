"""The adb devices as the window shows them. Which adb: the one the installers would use (install.sh --adb-path,
install.ps1 -AdbPath), so the window and the text installer never talk to different ones. Which device is which:
the installers' own rules - an F50/MU300 or U30 Air by what "adb devices -l" says, a device in our Linux by its
serial number (MU300LINUX-..., tools/linux-mode.sh).

Everything here only reads. The window never roots, unlocks or flashes anything itself: what writes to a device is
the installers, run by runner.py.
"""
import json
import os
import re
import socket
import subprocess
import sys

from . import TOP

# install.sh's select_device / install.ps1's SelectDevice
TARGET = re.compile(r'model:F50|product:MU300|device:MU300|device:U30Air|device:MU3351')
U30 = re.compile(r'U30Air|U30_Air|U30 Air')
USB_IPS = {'f50': '192.168.77.1', 'u30air': '192.168.78.1', 'v50': '192.168.79.1'}
_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0      # CREATE_NO_WINDOW: no console flashing up


class Device:
    """One line of `adb devices -l`."""

    def __init__(self, serial, state, attrs):
        self.serial = serial
        self.state = state                # device, unauthorized, offline, no permissions, ...
        self.attrs = attrs                # product, model, device, usb, transport_id
        self.line = '%s %s %s' % (serial, state, ' '.join('%s:%s' % kv for kv in sorted(attrs.items())))

    @property
    def linux(self):
        """Running our Linux (adb over USB there): never a device to install to."""
        return self.serial.upper().startswith('MU300LINUX')

    @property
    def target(self):
        """An F50/MU300 or U30 Air in Android, as far as adb's own list tells."""
        return not self.linux and self.state == 'device' and bool(TARGET.search(self.line))

    @property
    def kind(self):
        """f50, u30air, or '' when the list does not say (unauthorized devices say nothing about themselves)."""
        text = ' '.join(self.attrs.values())
        if U30.search(text):
            return 'u30air'
        if 'MU3351' in text:                # the ZTE V50 (experimental, FINDINGS 95)
            return 'v50'
        if re.search(r'MU300|F50|mu300', text):
            return 'f50'
        return ''

    @property
    def usable(self):
        return self.state == 'device'

    def __eq__(self, other):
        return isinstance(other, Device) and (self.serial, self.state, self.attrs) == (other.serial, other.state, other.attrs)

    def __repr__(self):
        return 'Device(%r, %r)' % (self.serial, self.state)


def parse_devices(text):
    """The devices of `adb devices -l`'s output."""
    out = []
    for line in text.splitlines()[1:] if text.lstrip().startswith('List of devices') else text.splitlines():
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('List of devices'):
            continue
        m = re.match(r'(\S+)\s+(no permissions(?: \([^)]*\))?|\S+)(.*)$', line)
        if not m:
            continue
        state = 'no permissions' if m.group(2).startswith('no permissions') else m.group(2)
        attrs = {}
        for part in m.group(3).split():
            if ':' in part:
                k, v = part.split(':', 1)
                attrs[k] = v
        out.append(Device(m.group(1), state, attrs))
    return out


def board(serial):
    """The board behind an adb serial number, whichever system answers: our Linux's is MU300LINUX-<Android's serial
    number>-<eMMC id> (boot/init), so a board keeps this across a restart into the other system."""
    s = serial or ''
    if s.upper().startswith('MU300LINUX-'):
        return s.split('-')[1]
    return s


def pick(devices, current=None, board_=None):
    """The device the window works on: the one already chosen while it is still there, else the only F50/U30 Air in
    Android, else the only device in our Linux, else none - never a phone or tablet by itself (the installers ask
    too whenever it is not the only one and one of ours). BOARD_: the board the window worked on (board()): that one
    again, in whichever system it comes back, and while it is away (restarting) none - never another in its place
    (measured: while the F50 restarted after an installation, a U30 Air in Linux was the only device left, and the
    window went on with it)."""
    if current:
        for d in devices:
            if d.serial == current:
                return d
    if board_:
        same = [d for d in devices if board(d.serial) == board_]
        return same[0] if len(same) == 1 else None
    targets = [d for d in devices if d.target]
    if len(targets) == 1:
        return targets[0]
    if not targets:
        linux = [d for d in devices if d.linux]
        if len(linux) == 1:
            return linux[0]
        waiting = [d for d in devices if not d.usable]
        if len(waiting) == 1 and len(devices) == 1:
            return waiting[0]
    return None


def script_argv(top, name, args):
    """How one of the project's scripts is started on this system: NAME is install or uninstall, ARGS its options in
    install.sh's spelling (--check, --backup, --adb-path, --answers FILE), translated for the PowerShell scripts."""
    if sys.platform == 'win32':
        ps = {'--check': '-Check', '--backup': '-Backup', '--adb-path': '-AdbPath', '--answers': '-AnswersFile'}
        return (['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(top, name + '.ps1')]
                + [ps.get(a, a) for a in args])
    return ['sh', os.path.join(top, name + '.sh')] + list(args)


def run(argv, timeout=20, env=None, stdin=None):
    """(status, output) of a short command; (None, '') when it cannot be started or does not come back in time."""
    kwargs = {'creationflags': _NO_WINDOW} if sys.platform == 'win32' else {}
    if stdin is None:
        kwargs['stdin'] = subprocess.DEVNULL
    else:
        kwargs['input'] = stdin
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None, ''
    return r.returncode, (r.stdout or b'').decode('utf-8', 'replace')


def find_adb(top=TOP, environ=None):
    """The adb the installers would use, or '' when there is none. MU300_ADB names one outright (the tests)."""
    environ = os.environ if environ is None else environ
    if environ.get('MU300_ADB'):
        return environ['MU300_ADB']
    env = dict(environ, MU300_NO_SELF_UPDATE='1', MU300_GUI='0', MU300_FROM_GUI='1', MU300_LANG='en')
    rc, out = run(script_argv(top, 'install', ['--adb-path']), timeout=30, env=env)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    if rc == 0 and lines and os.path.exists(lines[-1]):
        return lines[-1]
    return ''


class Adb:
    """The read-only questions the window asks through one adb."""

    def __init__(self, path):
        self.path = path

    def devices(self):
        """The attached devices; None when adb does not answer (no adb, a server that will not start)."""
        if not self.path:
            return None
        rc, out = run([self.path, 'devices', '-l'], timeout=15)
        if rc != 0:
            return None
        return parse_devices(out)

    def shell(self, serial, command, timeout=15):
        """(status, output) of a command on the device, without root."""
        rc, out = run([self.path, '-s', serial, 'shell', command], timeout=timeout, stdin=b'')
        return rc, out.replace('\r', '')

    def props(self, serial):
        """What a device in Android says about itself without root: {name: value} of getprop."""
        rc, out = self.shell(serial, 'getprop')
        props = {}
        if rc != 0:
            return props
        for m in re.finditer(r'^\[([^\]]+)\]: \[(.*)\]$', out, re.M):
            props[m.group(1)] = m.group(2)
        return props

    def linux_info(self, serial):
        """What a device in our Linux says about itself, through `mu300-device --json` (the device, the system that
        runs, its release, the kernel, what adb does there). Systems from before that command are asked the same
        things one by one. {} when it does not answer or is not ours; 'json': True when the command was there."""
        rc, out = self.shell(serial, '/opt/mu300/bin/mu300-device --json 2>/dev/null')
        info = parse_linux_json(out) if rc == 0 else {}
        if info:
            info['json'] = True
            return info
        rc, out = self.shell(serial, 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300; '
                                     '/opt/mu300/bin/mu300-device 2>/dev/null; cat /etc/mu300/image-version 2>/dev/null; '
                                     'echo; uname -r; test -e /etc/openwrt_release && echo openwrt || echo ubuntu')
        return parse_linux_plain(out) if rc == 0 else {}


def parse_linux_json(text):
    """The object of `mu300-device --json` (its fields are a contract: "mu300" is the format's version)."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and 'mu300' in obj:
            return {k: obj[k] for k in ('mu300', 'device', 'system', 'version_id', 'release', 'kernel', 'root', 'adb') if k in obj}
    return {}


def parse_linux_plain(text):
    lines = [l.strip() for l in text.splitlines()]
    if not lines or lines[0] != 'mu300':
        return {}
    rest = [l for l in lines[1:] if l]
    info = {'json': False}
    if rest and rest[0] in ('f50', 'u30air'):
        info['device'] = rest.pop(0)
    if len(rest) >= 3:
        info['release'] = rest[0]
    if len(rest) >= 2:
        info['kernel'] = rest[-2]
    if rest:
        info['system'] = rest[-1]
    return info


def ssh_answers(ip, timeout=1.5):
    """Whether something listens on the SSH port of the USB network (a device in Linux without adb to it)."""
    try:
        s = socket.create_connection((ip, 22), timeout=timeout)
    except OSError:
        return False
    s.close()
    return True


def linux_on_usb(ips=None):
    """The USB network address a device in Linux answers on, or ''. (MU300_IP: another address, as the installers.)"""
    for ip in (ips or (os.environ.get('MU300_IP', '').split() or sorted(USB_IPS.values()))):
        if ssh_answers(ip):
            return ip
    return ''
