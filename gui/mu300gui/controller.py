"""What the window does, without the window: it watches the adb devices, runs the installers' check, backup,
installation and uninstallation, and keeps what they report. Everything slow happens in threads that only put
messages in a queue; pump(), called from the window's own thread every moment, takes them out, changes the state and
tells the listeners which part of it changed. So the tests drive this class with no display at all.
"""
import os
import platform
import queue
import shutil
import sys
import threading
import time

from . import TOP, adb as adbmod, package as packagemod, releases, runner
from .i18n import _, language
from .masking import Masker
from .model import (BACKUP_STEPS, CHECK_STEPS, INSTALL_STEPS, MAKEROOM_STEPS, UNINSTALL_STEPS, Choices, Facts, Progress,
                    answers, make_room_answers, problems, uninstall_answers)

# what the Manage page can ask a device in our Linux to do over adb (a root shell there): the project's own commands
LINUX_COMMANDS = {
    'to-android': '/opt/mu300/bin/mu300-next-boot android && sync && reboot',
    'reboot': 'sync && reboot',
    'update-check': '/opt/mu300/bin/mu300-update check',
    'update': '/opt/mu300/bin/mu300-update apply',
    'kernel-5.4': '/opt/mu300/bin/mu300-update kernel 5.4',
    'kernel-6.18': '/opt/mu300/bin/mu300-update kernel 6.18',
    'kernel-7.2': '/opt/mu300/bin/mu300-update kernel 7.2',
    'kernel-7.3': '/opt/mu300/bin/mu300-update kernel 7.3',
}
# ... and a device in Android: start Linux through the on-device switch the installer puts there (a Magisk module)
ANDROID_COMMANDS = {'to-linux': "su -c 'mu300-linux'", 'reboot': 'reboot'}
LOG_LIMIT = 20000


class Controller:
    def __init__(self, top=TOP, environ=None, poll_seconds=2.0, linux_probe=None):
        self.top = top
        self.environ = dict(os.environ if environ is None else environ)
        # where a device in Linux without adb is looked for: the SSH port of the USB network (the tests: nowhere)
        self.linux_probe = linux_probe or (lambda: adbmod.linux_on_usb(self.environ.get('MU300_IP', '').split() or None))
        self.poll_seconds = poll_seconds
        self.inbox = queue.Queue()
        self.listeners = []
        self.masker = Masker()
        self.log = []                   # the lines of every run, masked as they came in
        self.adb_path = None            # None: not looked for yet; '': there is none
        self.adb = None
        self.devices = []
        self.devices_seen = False       # adb has answered at least once
        self.serial = ''                # the device the window works on
        self.board = ''                 # ... and its board (adb.board): the same one again after a restart, no other
        self.props = {}                 # what that device says without root (Android)
        self.linux = {}                 # what it says when it runs our Linux
        self.linux_ip = ''              # a device in Linux that only SSH reaches
        self.facts = Facts()
        self.facts_serial = ''
        self.check_state = 'idle'       # idle | running | ok | failed
        self.check_error = ''
        self.releases = {'latest': None, 'beta': None, 'files': {}}
        self.releases_state = 'idle'    # idle | running | ok | failed
        self.choices = Choices()
        # an unpacked Magisk zip (package.py): its system and kernel, and nothing to ask GitHub
        self.package = packagemod.load(top, self.environ)
        if self.package and not self.package.get('error'):
            self.choices.apply_package(self.package)
        self.run = None
        self.run_kind = ''              # check | backup | install | makeroom | uninstall | command
        self.progress = None
        self.last = {}                  # run kind -> its Progress when it ended
        self.backup = {}                # the facts of the last backup
        self.installed = None           # the `done` event of an installation that succeeded, with the choices
        self._device_key = None
        self._stop = threading.Event()
        self._threads = []

    # ---- listeners, threads
    def listen(self, fn):
        self.listeners.append(fn)

    def _tell(self, *topics):
        for fn in list(self.listeners):
            fn(set(topics))

    def _thread(self, target, *args):
        t = threading.Thread(target=target, args=args, daemon=True)
        t.start()
        self._threads.append(t)
        return t

    def start(self):
        """Begin watching: find adb, list the devices again and again, ask GitHub for the releases once."""
        self._thread(self._watch)
        if not self.package:
            self.fetch_releases()

    def close(self):
        self._stop.set()
        if self.run is not None:
            self.run.stop()

    def _watch(self):
        path = adbmod.find_adb(self.top, self.environ)
        self.inbox.put(('adb', path))
        if not path:
            return
        a = adbmod.Adb(path)
        last = None
        while not self._stop.is_set():
            found = a.devices()
            key = None if found is None else [(d.serial, d.state, sorted(d.attrs.items())) for d in found]
            if key != last or found is None:
                last = key
                self.inbox.put(('devices', found))
            if not found or not any(d.usable for d in found):
                # no adb device: one in Linux without adb to it still answers SSH on the USB network
                self.inbox.put(('linux-ip', self.linux_probe()))
            self._stop.wait(self.poll_seconds)

    def fetch_releases(self):
        if self.releases_state == 'running':
            return
        self.releases_state = 'running'
        self._thread(lambda: self.inbox.put(('releases', releases.fetch(top=self.top))))

    # ---- the state changes, in the window's thread
    def pump(self):
        """Take in what the threads and the run have reported; returns whether anything changed."""
        topics = set()
        while True:
            try:
                kind, payload = self.inbox.get_nowait()
            except queue.Empty:
                break
            topics |= self._take(kind, payload)
        if self.run is not None:
            while self.run is not None:         # (the run's last message takes it away)
                try:
                    kind, payload = self.run.messages.get_nowait()
                except queue.Empty:
                    break
                topics |= self._run_message(kind, payload)
        if topics:
            self._tell(*topics)
        return bool(topics)

    def _take(self, kind, payload):
        if kind == 'adb':
            self.adb_path = payload
            self.adb = adbmod.Adb(payload) if payload else None
            return {'devices'}
        if kind == 'devices':
            self.devices_seen = payload is not None
            self.devices = payload or []
            for d in self.devices:
                self.masker.add_serial(d.serial)
            chosen = adbmod.pick(self.devices, self.serial, self.board)
            self.serial = chosen.serial if chosen else ''
            if chosen:
                self.board = adbmod.board(chosen.serial)
            key = (chosen.serial, chosen.state) if chosen else None
            if key != self._device_key:         # another device, or the same one in another state
                self._device_key = key
                self._device_changed()
            if any(d.usable for d in self.devices):
                self.linux_ip = ''
            return {'devices', 'check'}
        if kind == 'props':
            serial, props = payload
            if serial == self.serial:
                self.props = props
            return {'devices'}
        if kind == 'linux':
            serial, info = payload
            if serial == self.serial:
                self.linux = info
            return {'devices', 'linux'}
        if kind == 'linux-ip':
            if payload != self.linux_ip:
                self.linux_ip = payload
                return {'devices', 'linux'}
            return set()
        if kind == 'releases':
            self.releases = payload
            self.releases_state = 'ok' if payload.get('latest') else 'failed'
            if self.choices.release and self.choices.release not in (payload.get('latest'), payload.get('beta')) and not self.choices.release_url:
                pass        # a tag typed by hand stays what it is
            return {'releases'}
        return set()

    @property
    def device(self):
        for d in self.devices:
            if d.serial == self.serial:
                return d
        return None

    def select(self, serial):
        """The user chose a device."""
        chosen = [d for d in self.devices if d.serial == serial]
        if serial != self.serial and chosen:
            self.serial = serial
            self.board = adbmod.board(serial)
            self._device_key = (serial, chosen[0].state)
            self._device_changed()
            self._tell('devices', 'check')

    def _device_changed(self):
        """Another device, or the same one in another state: what was known about the old one is gone."""
        self.props = {}
        self.linux = {}
        if self.facts_serial != self.serial or not self.serial:
            self.facts = Facts()
            self.facts_serial = ''
            if self.check_state != 'running':
                self.check_state = 'idle'
                self.check_error = ''
        d = self.device
        if d is None or not d.usable or self.adb is None:
            return
        serial, a = d.serial, self.adb
        if d.linux:
            self._thread(lambda: self.inbox.put(('linux', (serial, a.linux_info(serial)))))
        else:
            self._thread(lambda: self.inbox.put(('props', (serial, a.props(serial)))))

    # ---- what is known about the chosen device
    def device_state(self):
        """none, no-adb, unauthorized, offline, linux, linux-ssh, other, android."""
        if self.adb_path == '':
            return 'no-adb'
        d = self.device
        if d is None:
            if self.linux_ip:
                return 'linux-ssh'
            return 'choose' if self.devices else 'none'
        if d.state == 'unauthorized':
            return 'unauthorized'
        if not d.usable:
            return 'offline'
        if d.linux:
            return 'linux'
        return 'android' if d.target else 'other'

    def kind(self):
        """f50 or u30air, from whatever has said so: the check, the Linux on the device, adb's list."""
        d = self.device
        return (self.facts.kind if self.facts.kind in ('f50', 'u30air', 'v50') else '') or self.linux.get('device', '') or (d.kind if d else '')

    def problems(self):
        return problems(self.choices, self.facts)

    def busy(self):
        return self.run is not None and self.run.running

    # ---- runs
    def _begin(self, kind, name, args, steps, answers_=None, serial=None, extra=None):
        if self.busy():
            return False
        serial = self.serial if serial is None else serial
        self.run_kind = kind
        self.progress = Progress(steps, kind=kind)
        self._log('')
        self._log('--- %s %s' % (name, ' '.join(args)))
        self.run = runner.start(self.top, name, args, language(), serial=serial, adb_path=self.adb_path or '',
                                answers=answers_, extra=extra, environ=self.environ)
        return True

    def check(self):
        """Look at the chosen device: the installer's own --check, which writes nothing."""
        d = self.device
        if d is None or not d.usable or d.linux or self.busy():
            return False
        self.check_state = 'running'
        self.check_error = ''
        # the one question a check can ask: a device that is no F50/U30 Air. "no" unless the user said to go on.
        extra = {'MU300_ANS_UNKNOWN_MODEL': 'yes' if self.choices.unknown_model else 'no'}
        ok = self._begin('check', 'install', ['--check'], CHECK_STEPS, extra=extra)
        self._tell('check', 'run')
        return ok

    def backup_now(self):
        """Copy what an installation reads first to this computer (the installer's own --backup)."""
        if not self._ready_android():
            return False
        ok = self._begin('backup', 'install', ['--backup'], BACKUP_STEPS)
        self._tell('run')
        return ok

    def install(self):
        if not self._ready_android() or self.problems():
            return False
        a = answers(self.choices, self.facts, language(), self.serial)
        self.masker.add(self.choices.password, '<password>')
        self.masker.add(self.choices.current_password, '<password>')
        ok = self._begin('install', 'install', [], INSTALL_STEPS, answers_=a)
        self._tell('run')
        return ok

    def make_room(self, gib, word, reboot=True):
        if not self._ready_android():
            return False
        a = make_room_answers(language(), self.serial, gib, word, reboot)
        ok = self._begin('makeroom', 'install', [], MAKEROOM_STEPS, answers_=a)
        self._tell('run')
        return ok

    def uninstall(self, wipe, sd_wipe, grow=False, erase_word=''):
        if not self._ready_android():
            return False
        a = uninstall_answers(language(), self.serial, wipe, sd_wipe, grow, erase_word)
        if sys.platform == 'win32':
            for k in ('MU300_ANS_GROW', 'MU300_ANS_ERASE_ANDROID'):      # uninstall.ps1 has no such question
                a.pop(k, None)
        ok = self._begin('uninstall', 'uninstall', [], UNINSTALL_STEPS, answers_=a)
        self._tell('run')
        return ok

    def command(self, name):
        """One of the project's own commands on the chosen device, over adb: LINUX_COMMANDS for a device in our
        Linux, ANDROID_COMMANDS for one in Android. Its output goes to the log."""
        d = self.device
        if d is None or not d.usable or self.busy() or self.adb is None:
            return False
        table = LINUX_COMMANDS if d.linux else ANDROID_COMMANDS
        if name not in table:
            return False
        self.run_kind = 'command'
        self.progress = Progress((), kind='command')
        self.progress.title = name
        self._log('')
        self._log('--- adb shell %s' % table[name])
        env = runner.base_env(language(), d.serial, self.adb_path, self.environ)
        self.run = runner.Run([self.adb_path, '-s', d.serial, 'shell', table[name]], dict(env, MU300_NONINTERACTIVE='1'), cwd=self.top)
        self._tell('run')
        return True

    def _ready_android(self):
        d = self.device
        return d is not None and d.usable and not d.linux and not self.busy()

    def answer(self, text):
        """Answer the question the run waits on (INSTALL, UNINSTALL, or anything else to cancel)."""
        if self.run is None or self.progress is None or self.progress.waiting is None:
            return False
        self.progress.waiting = None
        ok = self.run.send(text)
        self._tell('run')
        return ok

    def cancel(self):
        """Stop the run. Only while that is safe: before anything is written to the device (Progress.can_cancel)."""
        if not self.busy() or self.progress is None or not self.progress.can_cancel():
            return False
        self._log(_('Stopped by the user.'))
        self.run.stop()
        return True

    def _run_message(self, kind, payload):
        p = self.progress
        if kind == 'line':
            text = self.masker.mask(payload)
            self._log(text)
            p.line(text)
            return {'log'}
        if kind == 'event':
            # an event's text can name the device too
            ev = {k: (self.masker.mask(v) if k in ('message', 'title', 'text', 'answer') else v) for k, v in payload.items()}
            p.event(ev)
            if ev.get('event') == 'error':
                self._log('ERROR: ' + ev.get('message', ''))
            return {'run'} | ({'confirm'} if ev.get('event') == 'question' and ev.get('state') == 'waiting' else set())
        if kind == 'exit':
            p.end(payload)
            topics = {'run', 'log'}
            run_kind = self.run_kind
            self.last[run_kind] = p
            self.run = None
            if run_kind == 'check':
                self.facts = Facts(p.facts)
                self.facts_serial = self.serial
                self.check_state = 'ok' if p.ok else 'failed'
                self.check_error = p.error
                self._after_check()
                topics |= {'check'}
            elif run_kind == 'backup' and p.ok:
                self.backup = dict(p.facts)
            elif run_kind == 'install' and p.ok:
                self.installed = {'done': dict(p.result), 'kind': self.kind(), 'boot': self.choices.boot_system(),
                                  'systems': self.choices.systems(), 'panel': self.choices.panel and self.choices.ubuntu,
                                  'attempts': self.choices.boot_attempts, 'default_linux': self.choices.default_linux}
            if run_kind in ('install', 'makeroom', 'uninstall'):
                # the device is another one now: what the last check said no longer holds
                self.facts = Facts()
                self.facts_serial = ''
                self.check_state = 'idle'
                topics |= {'check'}
            return topics
        return set()

    def _after_check(self):
        """Fit the choices to the device that was checked: only what the installer itself would default."""
        c, f = self.choices, self.facts
        c.device = f.kind
        sd = f.sd
        if not sd:
            c.storage = 'internal'
        elif c.storage == 'internal' and (f.num('region.size') < 700 * (1 << 20) or sd[2] == 'yes'):
            c.storage = 'sd'           # the installers' own default: the card when there is no room inside, or when it holds an installation
        room = c.place_bytes(f)
        if room and c.ubuntu and c.openwrt and room < f.need('both'):
            # both do not fit: the one that does (the installers refuse the choice; here it is not offered)
            c.ubuntu, c.openwrt = (room >= f.need('ubuntu')), (room < f.need('ubuntu'))
        if not c.kernel_ok() and not c.package:
            c.kernel = '6.18'

    # ---- the log
    def _log(self, text):
        self.log.append(text)
        if len(self.log) > LOG_LIMIT:
            del self.log[:len(self.log) - LOG_LIMIT]

    def log_text(self):
        """The log as it may leave the computer: every line was masked when it came in, and is masked once more with
        what has been learned since (a serial number that appeared before its device was listed)."""
        return '\n'.join(self.masker.mask(l) for l in self.log) + '\n'

    def save_log(self, path):
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(self.log_text())

    def diagnostics(self):
        """A report for an issue: this computer, the tools, the devices adb lists, what the last check found, how the
        last runs ended, and the log - all of it masked."""
        lines = ['MU300 Linux graphical installer - diagnostics', time.strftime('%Y-%m-%d %H:%M:%S %z'), '']
        lines += ['system:   %s %s (%s)' % (platform.system(), platform.release(), platform.machine()),
                  'python:   %s' % sys.version.split()[0]]
        try:
            import tkinter
            lines.append('tk:       %s' % tkinter.TkVersion)
        except Exception as e:      # the report is written from the tests too, where there may be no tkinter
            lines.append('tk:       %s' % e)
        lines += ['language: %s' % language(), 'project:  %s' % self._revision(), 'adb:      %s' % (self.adb_path or 'none'),
                  'tools:    ' + ', '.join('%s=%s' % (t, 'yes' if shutil.which(t) else 'no') for t in ('curl', 'lz4', 'tar', 'ssh', 'python3')),
                  '']
        lines.append('devices (%d):' % len(self.devices))
        for d in self.devices:
            lines.append('  %s  %s  %s' % (d.serial, d.state, ' '.join('%s:%s' % kv for kv in sorted(d.attrs.items()))))
        lines += ['', 'device state: %s' % self.device_state()]
        if self.props:
            for k in ('ro.product.model', 'ro.product.device', 'ro.build.display.id', 'ro.boot.flash.locked', 'ro.boot.verifiedbootstate', 'ro.boot.slot_suffix'):
                lines.append('  %s = %s' % (k, self.props.get(k, '')))
        if self.linux:
            lines.append('  linux: %s' % ', '.join('%s=%s' % kv for kv in sorted(self.linux.items())))
        lines += ['', 'last check: %s %s' % (self.check_state, self.check_error)]
        for k in sorted(self.facts):
            lines.append('  %s = %s' % (k, self.facts[k]))
        lines.append('')
        for kind in sorted(self.last):
            p = self.last[kind]
            lines.append('last %s: exit %s%s' % (kind, p.exit, (', error: ' + p.error) if p.error else ''))
        lines += ['', 'log:'] + self.log[-400:]
        return '\n'.join(self.masker.mask(l) for l in lines) + '\n'

    def _revision(self):
        for name in ('.mu300-source',):
            try:
                with open(os.path.join(self.top, name), encoding='utf-8') as f:
                    return f.read().strip()[:40]
            except OSError:
                pass
        try:
            head = os.path.join(self.top, '.git')
            if os.path.isfile(head):            # a worktree: .git is a file that names the directory
                with open(head, encoding='utf-8') as f:
                    head = f.read().split(':', 1)[1].strip()
            with open(os.path.join(head, 'HEAD'), encoding='utf-8') as f:
                ref = f.read().strip()
            return ref[5:] if ref.startswith('ref: ') else ref[:40]
        except (OSError, IndexError):
            return 'unknown'
