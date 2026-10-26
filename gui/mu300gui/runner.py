"""One run of an installer for the window: started in answers mode with the progress stream on, its output read in
a thread of its own and handed over as messages, an answer sent on standard input when it asks for one, stopped
when the user says so. The window's thread never waits for any of it.

The answers go in a file, not on the command line and not in the environment: a private file (made with mode 0600
before anything is written, in a directory of the user's own - on Windows under the profile, never a shared
temporary folder) that is deleted as soon as the run ends, however it ends.
"""
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading

from . import TOP
from .adb import script_argv

PREFIX = '@@mu300 '
_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0
_NEW_GROUP = 0x00000200 if sys.platform == 'win32' else 0


def private_dir(environ=None):
    """A directory only this user can read, for the answers file: under the profile on Windows (LOCALAPPDATA), the
    user's runtime or cache directory elsewhere - never the shared temporary folder."""
    environ = os.environ if environ is None else environ
    if sys.platform == 'win32':
        base = environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        d = os.path.join(base, 'mu300-linux', 'run')
    else:
        base = environ.get('XDG_RUNTIME_DIR') or environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
        d = os.path.join(base, 'mu300-linux')
    os.makedirs(d, mode=0o700, exist_ok=True)
    if sys.platform != 'win32':
        os.chmod(d, 0o700)
    return d


def write_answers(answers, directory=None):
    """The answers as a file of NAME=value lines that only this user can read; returns its path. The file exists
    with mode 0600 before the first byte goes in, and a value with a line end is refused: it would become a second
    line."""
    for k, v in answers.items():
        if '\n' in str(v) or '\r' in str(v):
            raise ValueError('an answer cannot hold a line end: %s' % k)
    directory = private_dir() if directory is None else directory
    fd, path = tempfile.mkstemp(prefix='answers-', suffix='.txt', dir=directory)     # O_EXCL, mode 0600
    try:
        if sys.platform != 'win32':
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            for k, v in answers.items():
                f.write('%s=%s\n' % (k, v))
    except Exception:
        remove(path)
        raise
    return path


def remove(path):
    """Delete a file that held answers: overwritten first, so the password is not left in a freed block for the
    next file to find."""
    try:
        n = os.path.getsize(path)
        with open(path, 'r+b') as f:
            f.write(b'\0' * n)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        pass


def parse_line(line):
    """('event', {...}) for a line of the progress stream, ('line', text) for anything else."""
    text = line.rstrip('\r\n').lstrip('﻿')
    if text.startswith(PREFIX):
        try:
            ev = json.loads(text[len(PREFIX):])
        except ValueError:
            return 'line', text
        if isinstance(ev, dict) and isinstance(ev.get('event'), str):
            return 'event', {str(k): ('' if v is None else str(v)) for k, v in ev.items()}
    return 'line', text


def base_env(lang, serial='', adb_path='', environ=None):
    """The environment of a run: the progress stream on, no self-update under a running window, no question about
    the window itself, the language, the device, and the directory of the adb in front of PATH."""
    env = dict(os.environ if environ is None else environ)
    for k in [k for k in env if k.startswith('MU300_ANS_')]:
        del env[k]                 # the answers come from this run's file alone
    env.update(MU300_PROGRESS='1', MU300_FROM_GUI='1', MU300_GUI='0', MU300_NO_SELF_UPDATE='1', MU300_LANG=lang)
    env.pop('MU300_ANSWERS', None)
    env.pop('MU300_PROGRESS_FD', None)
    if serial:
        env['ANDROID_SERIAL'] = serial
    else:
        env.pop('ANDROID_SERIAL', None)
    if adb_path and os.path.dirname(adb_path):
        env['PATH'] = os.path.dirname(adb_path) + os.pathsep + env.get('PATH', '')
    return env


class Run:
    """A started script. Messages arrive in .messages (a queue): ('event', dict), ('line', text), and at the end
    ('exit', status). send() answers a question that waits on standard input; stop() ends the run."""

    def __init__(self, argv, env, answers=None, cwd=None, messages=None):
        self.messages = queue.Queue() if messages is None else messages
        self.answers_file = write_answers(answers) if answers is not None else None
        self.argv = list(argv)
        if self.answers_file:
            flag = '-AnswersFile' if sys.platform == 'win32' and self.argv[0].lower().startswith('powershell') else '--answers'
            self.argv += [flag, self.answers_file]
        elif env.get('MU300_NONINTERACTIVE') is None:
            env = dict(env, MU300_NONINTERACTIVE='1')
        self.status = None
        self._lock = threading.Lock()
        kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=cwd or TOP)
        if sys.platform == 'win32':
            kwargs['creationflags'] = _NO_WINDOW | _NEW_GROUP
        else:
            kwargs['start_new_session'] = True      # a group of its own: stop() ends the script and what it started
        try:
            self.proc = subprocess.Popen(self.argv, **kwargs)
        except OSError as e:
            self._cleanup()
            self.proc = None
            self.status = 127
            self.messages.put(('line', 'cannot start %s: %s' % (self.argv[0], e)))
            self.messages.put(('exit', 127))
            return
        self._thread = threading.Thread(target=self._read, name='mu300-run', daemon=True)
        self._thread.start()

    def _read(self):
        try:
            for raw in iter(self.proc.stdout.readline, b''):
                self.messages.put(parse_line(raw.decode('utf-8', 'replace')))
        except (OSError, ValueError):
            pass
        status = self.proc.wait()
        for pipe in (self.proc.stdout, self.proc.stdin):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        self._cleanup()
        self.status = status
        self.messages.put(('exit', status))

    def _cleanup(self):
        with self._lock:
            if self.answers_file:
                remove(self.answers_file)
                self.answers_file = None

    @property
    def running(self):
        return self.proc is not None and self.status is None

    def send(self, text):
        """One line on the run's standard input (the answer to a question that waits there)."""
        if not self.running:
            return False
        try:
            self.proc.stdin.write((text + '\n').encode('utf-8'))
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def stop(self):
        """End the run and everything it started. The answers file goes at once, not when the process is reaped."""
        self._cleanup()
        if not self.running:
            return
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'], capture_output=True,
                               creationflags=_NO_WINDOW, timeout=15)
            else:
                os.killpg(self.proc.pid, signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            try:
                self.proc.terminate()
            except OSError:
                pass

    def wait(self, timeout=None):
        if self.proc is not None:
            self._thread.join(timeout)
        return self.status


def start(top, name, args, lang, serial='', adb_path='', answers=None, extra=None, environ=None):
    """Start install or uninstall (NAME) with ARGS; MU300_GUI_INSTALLER names another program to run instead (the
    tests: a script that plays an installer's part)."""
    env = base_env(lang, serial, adb_path, environ)
    env.update(extra or {})
    fake = env.get('MU300_GUI_INSTALLER')
    argv = [sys.executable, fake, name] + list(args) if fake else script_argv(top, name, args)
    return Run(argv, env, answers=answers, cwd=top)
