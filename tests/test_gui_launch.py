"""tools/gui-launch.sh: the question that offers the graphical installer, and the search for a Python that can run
it - against fake directory trees and stub interpreters, never this machine's own Pythons. The stubs answer the two
probes (the version, `import tkinter`) and record anything else they are run with."""
import os
import shutil
import unittest

from helpers import TOP, ShellTest

# what the script itself needs; python is not among them, so only the stubs of a test are ever found
TOOLS = ['sh', 'awk', 'uname', 'cat', 'sed', 'tr', 'grep', 'env', 'ls', 'rm', 'touch', 'printf', 'true', 'false', 'test', '[']


class GuiLaunch(ShellTest):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / 'toolbin'
        self.bin.mkdir()
        for t in TOOLS:
            p = shutil.which(t)
            if p:
                os.symlink(p, self.bin / t)
        self.root = self.tmp / 'root'
        self.root.mkdir()

    def python(self, path, version, tk=True, gui_exit=0):
        """A stub interpreter at PATH (under the fake root when it is absolute): VERSION is what it prints for the
        version probe ('' prints nothing and fails, like the Microsoft Store's stub), TK whether it has tkinter -
        or 'later': once $STUBLOG/tk-installed exists."""
        p = self.root / path.lstrip('/') if path.startswith('/') else self.stubs / path
        p.parent.mkdir(parents=True, exist_ok=True)
        tkrc = {True: 'exit 0', False: 'exit 1', 'old': 'exit 4', 'later': '[ -e "$STUBLOG/tk-installed" ]; exit $?'}[tk]
        ver = f'echo "{version}"; exit 0' if version else 'exit 9'
        p.write_text('#!/bin/sh\ncase "$2" in\n'
                     f'  *tkinter*) {tkrc} ;;\n  *version_info*) {ver} ;;\nesac\n'
                     f'echo "$0 $* lang=$MU300_LANG" >> "$STUBLOG/ran"\nexit {gui_exit}\n')
        p.chmod(0o755)
        return p

    def run_gui(self, shell, code, os_name='Linux', stdin='', **env):
        pre = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; ANSWERS_MODE=0; '
               'say() { echo "==> $*"; }; '
               'ask() { printf "ASKED[%s|%s] " "$2" "$3"; read -r _a || _a=; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
               '. "$TOP/tools/gui-launch.sh"; ')
        e = self.env(MU300_GUI_OS=os_name, MU300_GUI_ROOT=str(self.root), **env)
        e['PATH'] = f'{self.stubs}{os.pathsep}{self.bin}'
        for k in ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY', 'DISPLAY', 'WAYLAND_DISPLAY', 'MU300_GUI', 'MU300_FROM_GUI'):
            if k not in env:
                e.pop(k, None)
        import subprocess
        return subprocess.run(shell + ['-c', pre + code], input=stdin, capture_output=True, text=True, env=e, timeout=60)

    FIND = ('gui_py_find; echo "rc=$? py=[$GUI_PY] v=[$GUI_PY_VERSION] notk=[$GUI_PY_NOTK] nv=[$GUI_PY_NOTK_VERSION] '
            'old=[$GUI_PY_OLD] ov=[$GUI_PY_OLD_VERSION]"')

    def find(self, shell, **kw):
        return self.run_gui(shell, self.FIND, **kw).stdout.strip()

    # ---- the search
    def test_python_on_path_with_tkinter(self):
        for shell in self.each_shell():
            p = self.python('python3', '3.12')
            self.assertEqual(self.find(shell), f'rc=0 py=[{p}] v=[3.12] notk=[] nv=[] old=[] ov=[]')

    def test_the_first_with_tkinter_wins_not_the_first_python(self):
        for shell in self.each_shell():
            self.python('python3', '3.13', tk=False)
            good = self.python('/usr/bin/python3.11', '3.11')
            self.assertEqual(self.find(shell), f'rc=0 py=[{good}] v=[3.11] notk=[] nv=[] old=[] ov=[]')

    def test_python_without_tkinter_is_named(self):
        for shell in self.each_shell():
            p = self.python('python3', '3.10', tk=False)
            self.python('/usr/bin/python3', '3.9', tk=False)
            self.assertEqual(self.find(shell), f'rc=1 py=[] v=[] notk=[{p}] nv=[3.10] old=[] ov=[]')

    def test_a_tk_older_than_8_6_is_no_tkinter(self):
        # macOS's own Python (the Command Line Tools') has Tk 8.5, whose window stays black on current macOS
        for shell in self.each_shell():
            clt = self.python('/usr/bin/python3', '3.9', tk='old')
            self.stub('xcode-select', 'exit 0')
            out = self.run_gui(shell, self.FIND + '; echo "oldtk=$GUI_PY_NOTK_OLDTK"', os_name='Darwin').stdout
            self.assertIn(f'rc=1 py=[] v=[] notk=[{clt}] nv=[3.9]', out)
            self.assertIn('oldtk=1', out)
            top = self.gui_tree()
            out, ran = self.offer(shell, top=top, MU300_GUI='1', os_name='Darwin')
            self.assertIn('its Tk is older than 8.6 and cannot draw the window', out)
            self.assertIn('python.org', out)
            self.assertEqual(ran, '')
            # a Homebrew Python without tkinter beside it is the one a command is known for: that one is offered
            brew = self.python('/opt/homebrew/bin/python3.13', '3.13', tk=False)
            self.stub('brew', 'exit 0')
            out = self.run_gui(shell, self.FIND + '; echo "oldtk=$GUI_PY_NOTK_OLDTK"', os_name='Darwin').stdout
            self.assertIn(f'notk=[{brew}] nv=[3.13]', out)
            self.assertIn('oldtk=0', out)
            out, ran = self.offer(shell, top=top, MU300_GUI='1', os_name='Darwin', stdin='\n')
            self.assertIn('    brew install python-tk@3.13\n', out)
            # ... and one with a Tk that can is taken, whatever stands in front of it
            good = self.python('/Library/Frameworks/Python.framework/Versions/3.12/bin/python3', '3.12')
            self.assertIn(f'rc=0 py=[{good}] v=[3.12]', self.find(shell, os_name='Darwin'))
            for p in (clt, brew, good, self.stubs / 'brew', self.stubs / 'xcode-select'):
                p.unlink()

    def test_too_old_is_named(self):
        for shell in self.each_shell():
            self.python('python', '2.7')
            p = self.python('python3', '3.6')
            self.assertEqual(self.find(shell), f'rc=1 py=[] v=[] notk=[] nv=[] old=[{p}] ov=[3.6]')
            self.python('python3', '3.7')
            self.assertIn('ov=[3.7]', self.find(shell))
            self.python('python3', '3.8')
            self.assertIn('v=[3.8]', self.find(shell))

    def test_what_does_not_run_is_no_python(self):
        for shell in self.each_shell():
            self.python('python3', '')                               # prints nothing and fails
            (self.stubs / 'python').write_text('#!/bin/sh\necho "Python was not found; run without arguments to install"\n')
            (self.stubs / 'python').chmod(0o755)                     # prints something that is no version
            (self.stubs / 'python3.12').write_text('not a program')  # not executable
            (self.root / 'usr/bin').mkdir(parents=True, exist_ok=True)
            (self.root / 'usr/bin/python3.9').mkdir(exist_ok=True)   # a directory of that name
            self.assertEqual(self.find(shell), 'rc=1 py=[] v=[] notk=[] nv=[] old=[] ov=[]')

    def test_nothing_at_all(self):
        for shell in self.each_shell():
            self.assertEqual(self.find(shell), 'rc=1 py=[] v=[] notk=[] nv=[] old=[] ov=[]')
            self.assertEqual(self.find(shell, os_name='Darwin'), 'rc=1 py=[] v=[] notk=[] nv=[] old=[] ov=[]')

    def test_versioned_names_and_usr_local(self):
        for shell in self.each_shell():
            p = self.python('/usr/local/bin/python3.14', '3.14')
            self.assertIn(f'py=[{p}] v=[3.14]', self.find(shell))

    def test_macos_places_in_order(self):
        for shell in self.each_shell():
            clt = self.python('/usr/bin/python3', '3.9')
            self.stub('xcode-select', 'exit 0')
            self.assertIn(f'py=[{clt}] v=[3.9]', self.find(shell, os_name='Darwin'))
            org = self.python('/Library/Frameworks/Python.framework/Versions/3.12/bin/python3', '3.12')
            self.assertIn(f'py=[{org}] v=[3.12]', self.find(shell, os_name='Darwin'))
            intel = self.python('/usr/local/bin/python3', '3.11')
            self.assertIn(f'py=[{intel}] v=[3.11]', self.find(shell, os_name='Darwin'))
            brew = self.python('/opt/homebrew/bin/python3', '3.13', tk=False)   # Homebrew's Python without python-tk
            out = self.find(shell, os_name='Darwin')
            self.assertIn(f'py=[{intel}] v=[3.11]', out)
            # Linux never looks in Homebrew's or python.org's places
            self.assertNotIn('homebrew', self.find(shell))
            self.assertNotIn('Frameworks', self.find(shell))
            for p in (clt, org, intel, brew):
                p.unlink()

    def test_macos_never_runs_the_command_line_tools_stub_without_the_tools(self):
        # /usr/bin/python3 without the Command Line Tools only opens the dialog that offers to install them
        for shell in self.each_shell():
            stub = self.python('/usr/bin/python3', '3.9')
            stub.write_text('#!/bin/sh\necho "$*" >> "$STUBLOG/dialog"\necho 3.9\n')
            self.stub('xcode-select', 'exit 2')
            self.assertEqual(self.find(shell, os_name='Darwin'), 'rc=1 py=[] v=[] notk=[] nv=[] old=[] ov=[]')
            self.assertFalse((self.tmp / 'dialog').exists())
            (self.stubs / 'xcode-select').unlink()                   # no xcode-select at all: the same
            self.find(shell, os_name='Darwin')
            self.assertFalse((self.tmp / 'dialog').exists())
            self.stub('xcode-select', 'echo /Library/Developer/CommandLineTools')
            self.assertIn('v=[3.9]', self.find(shell, os_name='Darwin'))
            self.assertTrue((self.tmp / 'dialog').exists())
            (self.tmp / 'dialog').unlink()

    # ---- the command that adds tkinter
    def test_tk_command_per_system(self):
        for tool, want in (('apt-get', 'sudo apt-get install -y python3-tk'), ('dnf', 'sudo dnf install -y python3-tkinter'),
                           ('pacman', 'sudo pacman -S --noconfirm tk'), ('zypper', 'sudo zypper install -y python3-tk')):
            self.stub(tool, 'exit 0')
            for shell in self.each_shell():
                out = self.run_gui(shell, 'gui_tk_command /usr/bin/python3 3.12').stdout.strip()
                self.assertEqual(out, want)
            (self.stubs / tool).unlink()
        for shell in self.each_shell():
            self.assertEqual(self.run_gui(shell, 'gui_tk_command /usr/bin/python3 3.12').stdout.strip(), '')
            # Homebrew's Python gets Homebrew's python-tk of its version; python.org's needs none
            self.stub('brew', 'exit 0')
            out = self.run_gui(shell, f'gui_tk_command "{self.root}/opt/homebrew/bin/python3" 3.13', os_name='Darwin').stdout.strip()
            self.assertEqual(out, 'brew install python-tk@3.13')
            out = self.run_gui(shell, f'gui_tk_command "{self.root}/usr/local/bin/python3.12" 3.12', os_name='Darwin').stdout.strip()
            self.assertEqual(out, 'brew install python-tk@3.12')
            out = self.run_gui(shell, f'gui_tk_command "{self.root}/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" 3.12',
                               os_name='Darwin').stdout.strip()
            self.assertEqual(out, '')
            (self.stubs / 'brew').unlink()
            out = self.run_gui(shell, f'gui_tk_command "{self.root}/opt/homebrew/bin/python3" 3.13', os_name='Darwin').stdout.strip()
            self.assertEqual(out, '')

    # ---- whether it is asked, and the default
    def test_asked_only_of_a_person(self):
        asked = 'gui_asked; echo "asked=$?"'
        for shell in self.each_shell():
            # the tests have no terminal: never asked by itself
            self.assertIn('asked=1', self.run_gui(shell, asked).stdout)
            self.assertIn('asked=0', self.run_gui(shell, asked, MU300_GUI='1').stdout)
            self.assertIn('asked=1', self.run_gui(shell, asked, MU300_GUI='0').stdout)
            self.assertIn('asked=1', self.run_gui(shell, asked, MU300_GUI='1', MU300_FROM_GUI='1').stdout)
            self.assertIn('asked=1', self.run_gui(shell, 'ANSWERS_MODE=1; ' + asked, MU300_GUI='1').stdout)

    def test_the_default_follows_the_display(self):
        disp = 'gui_display; echo "display=$?"'
        for shell in self.each_shell():
            self.assertIn('display=1', self.run_gui(shell, disp).stdout)                         # Linux, no X11, no Wayland
            self.assertIn('display=0', self.run_gui(shell, disp, DISPLAY=':0').stdout)
            self.assertIn('display=0', self.run_gui(shell, disp, WAYLAND_DISPLAY='wayland-0').stdout)
            self.assertIn('display=0', self.run_gui(shell, disp, os_name='Darwin').stdout)
            for var in ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY'):
                self.assertIn('display=1', self.run_gui(shell, disp, os_name='Darwin', **{var: 'x'}).stdout)
                self.assertIn('display=1', self.run_gui(shell, disp, DISPLAY=':10', **{var: 'x'}).stdout)

    # ---- the whole offer
    OFFER = 'gui_offer; echo "text installer goes on"'

    def offer(self, shell, top=None, **kw):
        code = self.OFFER if top is None else f'TOP="{top}"; ' + self.OFFER
        r = self.run_gui(shell, code, **kw)
        ran = (self.tmp / 'ran').read_text() if (self.tmp / 'ran').exists() else ''
        if (self.tmp / 'ran').exists():
            (self.tmp / 'ran').unlink()
        return r.stdout, ran

    def gui_tree(self):
        top = self.tmp / 'top'
        (top / 'gui').mkdir(parents=True, exist_ok=True)
        (top / 'gui' / 'mu300-gui.py').write_text('# stands for the window\n')
        return top

    def test_offer_starts_the_window_and_hands_over(self):
        top = self.gui_tree()
        for shell in self.each_shell():
            p = self.python('python3', '3.12')
            out, ran = self.offer(shell, top=top, MU300_GUI='1')
            self.assertIn('==> Starting the graphical installer (Python 3.12)', out)
            self.assertEqual(ran.strip(), f'{p} {top}/gui/mu300-gui.py lang=en')
            self.assertNotIn('text installer goes on', out)          # the window ran and ended: so does the script
            self.assertNotIn('ASKED', out)

    def test_a_window_that_cannot_start_falls_back(self):
        top = self.gui_tree()
        for shell in self.each_shell():
            self.python('python3', '3.12', gui_exit=3)
            out, ran = self.offer(shell, top=top, MU300_GUI='1')
            self.assertIn('could not start or stopped with an error (3)', out)
            self.assertIn('Continuing with the text installer.', out)
            self.assertIn('text installer goes on', out)

    def test_no_python_falls_back_and_says_how_to_get_it(self):
        top = self.gui_tree()
        for shell in self.each_shell():
            out, ran = self.offer(shell, top=top, MU300_GUI='1')
            self.assertIn('No Python 3 was found for the window.', out)
            self.assertIn('python3-tk', out)
            self.assertIn('text installer goes on', out)
            out, ran = self.offer(shell, top=top, MU300_GUI='1', os_name='Darwin')
            self.assertIn('python.org', out)
            self.assertEqual(ran, '')
            self.python('python3', '3.6')
            out, ran = self.offer(shell, top=top, MU300_GUI='1')
            self.assertIn('Python 3.6 is there', out)
            self.assertIn('needs Python 3.8 or newer', out)
            self.assertIn('text installer goes on', out)
            (self.stubs / 'python3').unlink()

    def test_tkinter_is_installed_only_with_a_yes_to_the_shown_command(self):
        top = self.gui_tree()
        self.stub('apt-get', 'exit 0')
        self.stub('sudo', 'echo "sudo $*" >> "$STUBLOG/sudo"; touch "$STUBLOG/tk-installed"')
        for shell in self.each_shell():
            p = self.python('python3', '3.12', tk='later')
            # the default is no: the command is shown and not run
            out, ran = self.offer(shell, top=top, MU300_GUI='1', stdin='\n')
            self.assertIn(f'Python 3.12 is there ({p}), but without tkinter', out)
            self.assertIn('    sudo apt-get install -y python3-tk\n', out)
            self.assertIn('ASKED[Run that command now? (yes/no)|no]', out)
            self.assertFalse((self.tmp / 'sudo').exists())
            self.assertIn('text installer goes on', out)
            self.assertEqual(ran, '')
            # yes: it runs, the Python is looked at again, and the window starts with it
            out, ran = self.offer(shell, top=top, MU300_GUI='1', stdin='yes\n')
            self.assertEqual((self.tmp / 'sudo').read_text(), 'sudo apt-get install -y python3-tk\n')
            self.assertIn('Starting the graphical installer (Python 3.12)', out)
            self.assertNotIn('text installer goes on', out)
            self.assertIn('mu300-gui.py', ran)
            (self.tmp / 'sudo').unlink()
            (self.tmp / 'tk-installed').unlink()

    def test_a_failed_command_falls_back(self):
        top = self.gui_tree()
        self.stub('apt-get', 'exit 0')
        self.stub('sudo', 'exit 100')
        for shell in self.each_shell():
            self.python('python3', '3.12', tk=False)
            out, ran = self.offer(shell, top=top, MU300_GUI='1', stdin='yes\n')
            self.assertIn('the command failed', out)
            self.assertIn('text installer goes on', out)

    def test_no_package_tool_known(self):
        top = self.gui_tree()
        for shell in self.each_shell():
            self.python('python3', '3.12', tk=False)
            out, ran = self.offer(shell, top=top, MU300_GUI='1')
            self.assertIn('without tkinter', out)
            self.assertNotIn('ASKED', out)
            self.assertIn('the package is usually called python3-tk', out)
            self.assertIn('text installer goes on', out)

    def test_not_asked_means_nothing_happens(self):
        top = self.gui_tree()
        for shell in self.each_shell():
            self.python('python3', '3.12')
            for env in (dict(), dict(MU300_GUI='0'), dict(MU300_GUI='1', MU300_FROM_GUI='1')):
                out, ran = self.offer(shell, top=top, **env)
                self.assertEqual(out, 'text installer goes on\n', env)
                self.assertEqual(ran, '')

    def test_install_sh_offers_it_for_a_plain_installation_only(self):
        src = (TOP / 'install.sh').read_text()
        self.assertIn('[ $CHECK_ONLY = 1 ] || [ $BACKUP_ONLY = 1 ] || [ $MODE != prebuilt ] || gui_offer', src)
        self.assertLess(src.index('self_update "$0" "$@"'), src.index('|| gui_offer'))        # the newest window
        self.assertLess(src.index('|| gui_offer'), src.index("say \"$(t 'Checking host tools and device')\""))


if __name__ == '__main__':
    unittest.main()
