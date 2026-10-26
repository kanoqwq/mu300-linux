"""The window's few dealings with the desktop around it: a web page in the browser, a folder in the file manager,
an SSH session in the system's terminal, the free space of a disk. Each command line is built from fixed words and
an address the window made itself - nothing a device or a log said is ever part of one.
"""
import os
import re
import shutil
import subprocess
import sys
import webbrowser

_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


def open_url(url):
    if not re.match(r'^https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]+$', url):
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:       # no browser to hand it to
        return False


def open_folder(path):
    if not os.path.isdir(path):
        return False
    try:
        if sys.platform == 'win32':
            os.startfile(path)      # noqa: S606 - a directory of the window's own choosing
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
        return True
    except OSError:
        return False


def ssh_command(user, ip):
    """The command of an SSH session to the device, as it is shown and as it is run; None for anything that is not
    a plain user name and an IPv4 address."""
    if not re.match(r'^[a-z][a-z0-9_-]{0,31}$', user) or not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', ip):
        return None
    return ['ssh', '%s@%s' % (user, ip)]


def terminal_argv(command, platform=None, which=shutil.which):
    """How this system opens a terminal window that runs COMMAND (a list of plain words); None when no terminal is
    known - the window then shows the command to type."""
    platform = sys.platform if platform is None else platform
    if any(not re.match(r'^[A-Za-z0-9@._:/=-]+$', w) for w in command):
        return None
    text = ' '.join(command)
    if platform == 'darwin':
        return ['osascript', '-e', 'tell application "Terminal" to do script "%s"' % text,
                '-e', 'tell application "Terminal" to activate']
    if platform == 'win32':
        return ['cmd.exe', '/c', 'start', 'MU300 Linux', 'cmd.exe', '/k'] + list(command)
    for term, flag in (('x-terminal-emulator', '-e'), ('gnome-terminal', '--'), ('konsole', '-e'), ('xfce4-terminal', '-x'),
                       ('kitty', None), ('alacritty', '-e'), ('xterm', '-e')):
        if which(term):
            return [term] + ([flag] if flag else []) + list(command)
    return None


def open_terminal(command):
    argv = terminal_argv(command)
    if not argv:
        return False
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def free_bytes(path):
    """The free space of the disk PATH is on (or will be on: the nearest directory that exists)."""
    p = os.path.abspath(path)
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def work_dir(top, environ=None):
    """Where the installers keep what they pull and download: MU300_WORK, or work/ next to the project."""
    environ = os.environ if environ is None else environ
    return environ.get('MU300_WORK') or os.path.join(top, 'work')
