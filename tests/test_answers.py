"""The installers without a person at the terminal (tools/answers.sh): every question answered by its variable, a
question without an answer an error that names the variable and never a default, and the progress stream. The
pieces first (ask, the answers file, the events), then install.sh and uninstall.sh as a whole against the fake adb
of tests/fakeadb.py, which answers what a rooted device would and writes nothing."""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

import fakeemmc  # noqa: E402
from helpers import TOP, ShellTest

FAKEADB = Path(__file__).resolve().parent / 'fakeadb.py'


def events(text):
    """The events of a run's output, in order."""
    out = []
    for line in text.splitlines():
        if line.startswith('@@mu300 '):
            out.append(json.loads(line[len('@@mu300 '):]))
    return out


class Ask(ShellTest):
    PRE = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; . "$TOP/tools/answers.sh"; ')

    def run_ask(self, shell, code, stdin='', **env):
        return self.sh(shell, self.PRE + code, stdin=stdin, **env)

    def test_interactive_asks_the_terminal_as_before(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask k "Kernel" 1 MU300_ANS_KERNEL; echo "k=$k"; '
                                    'ask v "VPN? (yes/no)" no MU300_ANS_VPN; echo "v=$v"', stdin='\nevet\n',
                             MU300_ANS_KERNEL='3')           # a variable alone does not turn the mode on
            self.assertIn('Kernel [1]: k=1', r.stdout)
            self.assertIn('v=yes', r.stdout)
            self.assertNotIn('@@mu300', r.stdout)

    def test_answered_by_the_variable(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask k "Kernel" 1 MU300_ANS_KERNEL; echo "k=$k"',
                             MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, MU300_ANS_KERNEL='6.18')
            self.assertIn('Kernel [1]: 6.18\n', r.stdout)
            self.assertIn('k=6.18', r.stdout)
            self.assertEqual(events(r.stdout), [{'event': 'question', 'id': 'MU300_ANS_KERNEL', 'text': 'Kernel',
                                                 'default': '1', 'answer': '6.18'}])

    def test_the_words_of_the_other_languages_count(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask v "VPN" no MU300_ANS_VPN; echo "v=$v"',
                             MU300_NONINTERACTIVE=1, MU300_ANS_VPN='evet')
            self.assertIn('v=yes', r.stdout)

    def test_unanswered_is_an_error_that_names_the_variable(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask c "Type INSTALL to continue" no MU300_ANS_CONFIRM; echo "c=$c"',
                             MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, stdin='INSTALL\n')
            self.assertEqual(r.returncode, 1)
            self.assertNotIn('c=', r.stdout)             # never the default, never what stdin happens to hold
            self.assertIn('no answer to the question "Type INSTALL to continue": set MU300_ANS_CONFIRM', r.stderr)
            ev = events(r.stdout)
            self.assertEqual([e['event'] for e in ev], ['error'])
            self.assertIn('MU300_ANS_CONFIRM', ev[0]['message'])

    def test_set_to_nothing_is_the_default(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask c "Type INSTALL to continue" no MU300_ANS_CONFIRM; echo "c=$c"',
                             MU300_NONINTERACTIVE=1, MU300_ANS_CONFIRM='')
            self.assertIn('c=no', r.stdout)

    def test_a_question_without_a_variable_cannot_be_answered(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask x "Something new" yes; echo "x=$x"', MU300_NONINTERACTIVE=1)
            self.assertEqual(r.returncode, 1)
            self.assertIn('this question has no variable', r.stderr)
            self.assertNotIn('x=', r.stdout)

    def test_a_question_that_comes_back_stops(self):
        # the kernel question repeats until it likes the answer: the same variable would answer for ever
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; n=0; while :; do n=$((n + 1)); [ $n -lt 9 ] || exit 7; '
                                    'ask k "Kernel" 1 MU300_ANS_KERNEL; case $k in 1|2|3) break ;; esac; done',
                             MU300_NONINTERACTIVE=1, MU300_ANS_KERNEL='9')
            self.assertEqual(r.returncode, 1)
            self.assertIn('the answer in MU300_ANS_KERNEL was not accepted: 9', r.stderr)

    def test_stdin_answers_while_it_runs(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask c "Type INSTALL to continue" no MU300_ANS_CONFIRM; echo "c=$c"; '
                                    'ask k "Kernel" 1 MU300_ANS_KERNEL; echo "k=$k"', stdin='INSTALL\n',
                             MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, MU300_ANS_CONFIRM='@stdin', MU300_ANS_KERNEL='2')
            self.assertIn('c=INSTALL', r.stdout)
            self.assertIn('k=2', r.stdout)
            ev = events(r.stdout)
            self.assertEqual(ev[0], {'event': 'question', 'id': 'MU300_ANS_CONFIRM', 'text': 'Type INSTALL to continue',
                                     'default': 'no', 'state': 'waiting'})
            self.assertEqual(ev[1]['answer'], 'INSTALL')
            # nothing on standard input: an error, not the default
            r = self.run_ask(shell, 'answers_setup; ask c "Type INSTALL to continue" no MU300_ANS_CONFIRM; echo "c=$c"',
                             stdin='', MU300_NONINTERACTIVE=1, MU300_ANS_CONFIRM='@stdin')
            self.assertEqual(r.returncode, 1)
            self.assertNotIn('c=', r.stdout)

    def test_the_password_is_never_shown(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask_password pw "Password" "Repeat"; echo "len=${#pw}"; '
                                    'sh -c \'echo "child: pw=[${MU300_ANS_PASSWORD-unset}] mode=$MU300_NONINTERACTIVE"\'; '
                                    'env | grep -c MU300_ANS_PASSWORD; true',
                             MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, MU300_ANS_PASSWORD='hunter2secret')
            self.assertIn('len=13', r.stdout)
            # given in the environment, it leaves the environment: what the installer starts does not inherit it
            self.assertIn('child: pw=[unset] mode=1\n0\n', r.stdout)
            self.assertIn('Password: ******', r.stdout)
            self.assertEqual(r.stdout.count('hunter2'), 0)
            self.assertEqual(r.stderr.count('hunter2'), 0)
            self.assertEqual([e.get('secret') for e in events(r.stdout)], ['1'])
            r = self.run_ask(shell, 'answers_setup; ask_password pw "Password" "Repeat"; echo "len=${#pw}"',
                             MU300_NONINTERACTIVE=1)
            self.assertEqual(r.returncode, 1)
            self.assertIn('set MU300_ANS_PASSWORD', r.stderr)

    def test_a_secret_is_never_shown_whichever_way_it_is_asked(self):
        # the table of secrets decides, not the function that happens to ask
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask pw "Password" "" MU300_ANS_PASSWORD; echo "len=${#pw}"; '
                                    'ask pw "Password" "" MU300_ANS_PASSWORD',
                             MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, MU300_ANS_PASSWORD='hunter2secret')
            self.assertIn('len=13', r.stdout)
            self.assertIn('Password []: ******', r.stdout)
            self.assertEqual((r.stdout + r.stderr).count('hunter2'), 0)
            self.assertIn('the answer in MU300_ANS_PASSWORD was not accepted: ******', r.stderr)
            self.assertEqual(events(r.stdout)[0], {'event': 'question', 'id': 'MU300_ANS_PASSWORD', 'text': 'Password',
                                                   'default': '', 'secret': '1'})

    def test_the_password_typed_twice(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; ask_password pw "Password" "Repeat"; echo "pw=$pw"', stdin='abcdef\nabcdef\n')
            self.assertIn('pw=abcdef', r.stdout)
            r = self.run_ask(shell, 'answers_setup; ask_password pw "Password" "Repeat"; echo "pw=[$pw]"', stdin='abcdef\nabcdeg\n')
            self.assertIn('pw=[]', r.stdout)

    def test_answers_file(self):
        f = self.tmp / 'answers'
        f.write_text('# what to install\n\nMU300_ANS_KERNEL=6.18\r\nMU300_ANS_PASSWORD=p=ss w\'rd $HOME `id` "q" \\n\n'
                     'ANDROID_SERIAL=324950664950\nMU300_ANS_GPU=\nMU300_LANG=tr')
        f.chmod(0o600)
        code = (f'ANSWERS_FILE="{f}"; answers_setup; echo "mode=$ANSWERS_MODE"; echo "k=$MU300_ANS_KERNEL"; '
                'printf "p=%s\\n" "$MU300_ANS_PASSWORD"; echo "g=[${MU300_ANS_GPU-unset}]"; echo "l=$MU300_LANG"; '
                'sh -c \'echo "child: serial=$ANDROID_SERIAL kernel=$MU300_ANS_KERNEL pw=[${MU300_ANS_PASSWORD-unset}]"\'')
        for shell in self.each_shell():
            out = self.run_ask(shell, code).stdout
            self.assertIn('mode=1\n', out)
            self.assertIn('k=6.18\n', out)                                   # the CR of a file from Windows is gone
            self.assertIn('p=p=ss w\'rd $HOME `id` "q" \\n\n', out)          # the value as it is, nothing expanded
            self.assertIn('g=[]\n', out)
            self.assertIn('l=tr\n', out)                                     # a last line without a line end counts
            # the programs the installer starts see the answers (adb reads ANDROID_SERIAL), never the password
            self.assertIn('child: serial=324950664950 kernel=6.18 pw=[unset]', out)
            # and the same through the environment variable
            out = self.sh(shell, self.PRE + 'answers_setup; echo "mode=$ANSWERS_MODE k=$MU300_ANS_KERNEL"',
                          MU300_ANSWERS=str(f)).stdout
            self.assertIn('mode=1 k=6.18', out)
            # a password that the environment exports too: the file's counts, and it is exported no more
            out = self.run_ask(shell, code, MU300_ANS_PASSWORD='exported').stdout
            self.assertIn('child: serial=324950664950 kernel=6.18 pw=[unset]', out)
            self.assertNotIn('exported', out)

    @unittest.skipIf(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0), 'needs file permissions that bind')
    def test_answers_file_must_be_the_users_alone(self):
        f = self.tmp / 'answers'
        f.write_text('MU300_ANS_KERNEL=6.18\nMU300_ANS_PASSWORD=hunter2secret\n')
        code = f'ANSWERS_FILE="{f}"; answers_setup; echo "reached k=$MU300_ANS_KERNEL"'
        for shell in self.each_shell():
            for mode in (0o644, 0o640, 0o604, 0o660, 0o606, 0o666, 0o620):
                f.chmod(mode)
                r = self.run_ask(shell, code)
                self.assertEqual(r.returncode, 2, oct(mode))
                self.assertIn('must be yours alone', r.stderr)
                self.assertNotIn('reached', r.stdout)
                self.assertNotIn('hunter2', r.stdout + r.stderr)
            for mode in (0o600, 0o400, 0o700):
                f.chmod(mode)
                self.assertIn('reached k=6.18', self.run_ask(shell, code).stdout, oct(mode))
            # a link is refused even to a private file: what it points to can be changed by its owner
            f.chmod(0o600)
            link = self.tmp / 'link'
            if not link.exists():
                link.symlink_to(f)
            r = self.run_ask(shell, f'ANSWERS_FILE="{link}"; answers_setup; echo reached')
            self.assertEqual(r.returncode, 2)
            self.assertNotIn('reached', r.stdout)

    def test_answers_file_takes_only_the_installers_variables(self):
        for bad, why in (('PATH=/evil\n', 'not a variable of the installer: PATH'),
                         ('LD_PRELOAD=/x.so\n', 'not a variable of the installer: LD_PRELOAD'),
                         ('mu300_ans_kernel=1\n', 'not a variable of the installer: mu300_ans_kernel'),
                         ('MU300_X;id=1\n', 'not a variable of the installer: MU300_X;id'),
                         ('hunter2secret\n', 'line 2 is not NAME=value'),
                         ('=x\n', 'not a variable of the installer: ')):
            f = self.tmp / 'answers'
            f.write_text('MU300_ANS_GPU=yes\n' + bad)
            f.chmod(0o600)
            for shell in self.each_shell():
                r = self.run_ask(shell, f'ANSWERS_FILE="{f}"; answers_setup; echo reached')
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn(why, r.stderr)
                self.assertNotIn('hunter2', r.stdout + r.stderr)
                self.assertNotIn('reached', r.stdout)
        for shell in self.each_shell():
            r = self.run_ask(shell, f'ANSWERS_FILE="{self.tmp}/none"; answers_setup; echo reached')
            self.assertEqual(r.returncode, 2)
            self.assertIn('cannot read the answers file', r.stderr)

    def test_a_refused_answers_file_still_ends_the_stream(self):
        # measured on the Mac: a file with mode 644 stopped install.sh with no event at all, before the stream existed
        f = self.tmp / 'answers'
        f.write_text('MU300_ANS_PASSWORD=hunter2secret\n')
        f.chmod(0o644)
        for shell in self.each_shell():
            r = self.run_ask(shell, f'ANSWERS_FILE="{f}"; answers_setup; echo reached', MU300_PROGRESS=1)
            self.assertEqual(r.returncode, 2)
            ev = events(r.stdout)
            self.assertEqual(len(ev), 1, r.stdout)
            self.assertEqual(ev[0]['event'], 'error')
            self.assertIn('must be yours alone', ev[0]['message'])
            r = self.run_ask(shell, f'{{ ANSWERS_FILE="{f}"; answers_setup; }} 3>&1 1>/dev/null', MU300_PROGRESS_FD=3)
            self.assertEqual([e['event'] for e in events(r.stdout)], ['error'])
            self.assertNotIn('hunter2', r.stdout + r.stderr)
            r = self.run_ask(shell, f'ANSWERS_FILE="{f}"; answers_setup')      # no stream asked for: none
            self.assertNotIn('@@mu300', r.stdout + r.stderr)

    def test_events_are_json_whatever_the_text_holds(self):
        tricky = ['plain', 'a "quoted" \\ back\\slash', 'line\nend\tand tab\r', 'Türkçe 中文 ✓', "it's $HOME `x` %s {1}",
                  'bell\a esc\033[31m', '']
        (self.tmp / 'vals').write_bytes(b'\0'.join(t.encode() for t in tricky))
        code = ('answers_setup; i=0; while [ $i -lt %d ]; do i=$((i + 1)); '
                'v=$(tr "\\000" "\\036" < "%s" | awk -v n=$i \'BEGIN { RS = "\\036" } NR == n { printf "%%s", $0 }\'); '
                'info key$i "$v"; done' % (len(tricky), self.tmp / 'vals'))
        for shell in self.each_shell():
            r = self.run_ask(shell, code, MU300_PROGRESS=1)
            ev = events(r.stdout)
            self.assertEqual(len(ev), len(tricky), r.stdout + r.stderr)
            self.assertEqual(ev[0], {'event': 'info', 'key': 'key1', 'value': 'plain'})
            self.assertEqual(ev[1]['value'], 'a "quoted" \\ back\\slash')
            self.assertEqual(ev[2]['value'], 'line end and tab ')
            self.assertEqual(ev[3]['value'], 'Türkçe 中文 ✓')
            self.assertEqual(ev[4]['value'], "it's $HOME `x` %s {1}")
            self.assertEqual(ev[5]['value'], 'bell esc[31m')
            self.assertEqual(ev[6]['value'], '')

    def test_steps_and_the_stream_on_another_descriptor(self):
        code = ('answers_setup; step download 20; say "Downloading release v1"; say "A heading"; echo text; '
                'progress done result ok')
        for shell in self.each_shell():
            r = self.run_ask(shell, code, MU300_PROGRESS=1)
            ev = events(r.stdout)
            self.assertEqual(ev[0], {'event': 'step', 'id': 'download', 'percent': '20', 'title': 'Downloading release v1'})
            self.assertEqual(ev[1], {'event': 'step', 'id': '', 'percent': '', 'title': 'A heading'})   # an id is used once
            self.assertEqual(ev[2], {'event': 'done', 'result': 'ok'})
            self.assertIn('==> Downloading release v1', r.stdout)
            # on descriptor 3 the text for people stays clean
            r = self.run_ask(shell, '{ ' + code + '; } 3>&1 1>/dev/null', MU300_PROGRESS_FD=3)
            self.assertEqual(len(events(r.stdout)), 3)
            self.assertNotIn('==>', r.stdout)
            r = self.run_ask(shell, code, MU300_PROGRESS_FD=3)       # a descriptor nobody opened: an error, not silence
            self.assertNotEqual(r.returncode, 0)
            r = self.run_ask(shell, 'answers_setup; echo reached', MU300_PROGRESS_FD='1; id')
            self.assertEqual(r.returncode, 2)
            self.assertNotIn('reached', r.stdout)
            # without a stream nothing is added
            r = self.run_ask(shell, code)
            self.assertNotIn('@@mu300', r.stdout)

    def test_die_is_an_error_event(self):
        for shell in self.each_shell():
            r = self.run_ask(shell, 'answers_setup; die "no adb device"; echo reached', MU300_PROGRESS=1)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(events(r.stdout), [{'event': 'error', 'message': 'no adb device'}])
            self.assertIn('ERROR: no adb device', r.stderr)


class Fetch(ShellTest):
    """install.sh's fetch, cut out between its markers, with a curl that takes its time: the download is on the
    progress stream while it runs, under the file's name in the release."""
    SRC = re.search(r'# --- fetch begin\n(.*?)# --- fetch end', (TOP / 'install.sh').read_text(), re.S).group(1)

    def setUp(self):
        super().setUp()
        (self.tmp / 'fetch.sh').write_text(self.SRC)
        # curl -fsSLI URL: the length; -r A-B: that range, slowly; otherwise the whole file, slowly
        self.stub('curl', 'out=; range=; head=0; fail=0\n'
                          'while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift ;; -r) range=$2; shift ;; -fsSLI) head=1 ;; '
                          '*/missing) fail=1 ;; esac; shift; done\n'
                          '[ $fail = 0 ] || exit 22\n'
                          'if [ $head = 1 ]; then printf "HTTP/2 200\\r\\ncontent-length: %s\\r\\n\\r\\n" "$SIZE"; exit 0; fi\n'
                          'n=$SIZE; [ -z "$range" ] || n=$(( ${range#*-} - ${range%-*} + 1 ))\n'
                          ': > "$out"; h=$((n / 2))\n'
                          'head -c $h /dev/zero >> "$out"; sleep 2; head -c $((n - h)) /dev/zero >> "$out"')

    def fetch(self, shell, size, url='http://x/mu300-kernel.tar.gz', **env):
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; . "$TOP/tools/answers.sh"; answers_setup; '
                f'. "{self.tmp}/fetch.sh"; fetch {url} "{self.tmp}/mu300-kernel.tar.gz.part"; echo "rc=$?"')
        r = self.sh(shell, code, SIZE=size, **env)
        return r, [e for e in events(r.stdout) if e['event'] == 'download']

    def test_a_download_is_on_the_stream_while_it_runs(self):
        for shell in self.each_shell():
            for size in (1000000, 16000000):          # one stream, and parts in parallel
                r, ev = self.fetch(shell, size, MU300_PROGRESS=1)
                self.assertIn('rc=0', r.stdout, r.stderr)
                self.assertEqual((self.tmp / 'mu300-kernel.tar.gz.part').stat().st_size, size)
                self.assertEqual({e['file'] for e in ev}, {'mu300-kernel.tar.gz'})
                self.assertEqual({e['total'] for e in ev}, {str(size)})
                self.assertEqual((ev[0]['state'], ev[0]['bytes']), ('start', '0'))
                self.assertEqual((ev[-1]['state'], ev[-1]['bytes']), ('done', str(size)))
                running = [int(e['bytes']) for e in ev if e['state'] == 'running']
                self.assertTrue(running, ev)
                self.assertTrue(all(0 <= b <= size for b in running), running)
                self.assertTrue(any(0 < b < size for b in running), running)        # seen while it was half there
                self.assertEqual(running, sorted(running))

    def test_a_failed_download_says_so(self):
        for shell in self.each_shell():
            r, ev = self.fetch(shell, 1000000, url='http://x/missing', MU300_PROGRESS=1)
            self.assertNotIn('rc=0', r.stdout)
            self.assertEqual(ev[-1]['state'], 'failed')

    def test_without_the_stream_it_is_the_download_it_was(self):
        for shell in self.each_shell():
            r, ev = self.fetch(shell, 16000000)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(ev, [])
            self.assertEqual((self.tmp / 'mu300-kernel.tar.gz.part').stat().st_size, 16000000)


class Device(ShellTest):
    """A scratch HOME, the fake adb first on PATH, and no self-update: nothing here can reach a real device or GitHub."""

    BASE = dict(MU300_NO_SELF_UPDATE=1, MU300_LANG='en', MU300_IP='127.0.0.1', MU300_RELEASE='v2026.10.23',
                MU300_RELEASE_URL='http://127.0.0.1:9/no-release')

    def setUp(self):
        super().setUp()
        self.state = self.tmp / 'device.json'
        (self.tmp / 'home').mkdir()
        # the host tools an installation asks for before anything else: none of these runs gets as far as using them
        for tool in ('lz4', 'curl'):
            if not shutil.which(tool):
                self.stub(tool, 'exit 1')

    def device(self, **keys):
        self.state.write_text(json.dumps(keys))
        log = Path(str(self.state) + '.log')
        if log.exists():
            log.unlink()
        subprocess.run([sys.executable, str(FAKEADB), '--stub', str(self.stubs), str(self.state)], check=True)

    def run_script(self, shell, script, *args, stdin='', **env):
        e = dict(self.BASE, HOME=str(self.tmp / 'home'), FAKEADB=str(self.state), MU300_WORK=str(self.tmp / 'work'))
        e.update(env)
        full = self.env(**e)
        if 'ANDROID_SERIAL' not in e:
            full.pop('ANDROID_SERIAL', None)
        for k in [k for k in full if k.startswith('MU300_') and k not in e]:
            del full[k]
        return subprocess.run(shell + [str(TOP / script)] + list(args), input=stdin, capture_output=True, text=True,
                              env=full, timeout=120, cwd=str(self.tmp))

    def calls(self, kind=None):
        log = Path(str(self.state) + '.log')
        if not log.exists():
            return []
        rows = [json.loads(l) for l in log.read_text().splitlines()]
        return [r for r in rows if kind is None or r['kind'] == kind]

    def assert_no_secret_anywhere(self, secret):
        # adb was started with the answers in its environment, and never with the password; and the value is in no
        # file the run left behind (the work directory, the fake's log)
        calls = self.calls()
        self.assertTrue(any('MU300_ANS_KERNEL' in c['answers'] for c in calls))
        self.assertEqual([c['argv'] for c in calls if 'MU300_ANS_PASSWORD' in c['answers']], [])
        for p in [Path(str(self.state) + '.log')] + [q for q in (self.tmp / 'work').rglob('*') if q.is_file()]:
            self.assertNotIn(secret.encode(), p.read_bytes(), str(p))

    def assert_nothing_written(self):
        self.assertEqual([c['cmd'] for c in self.calls('write')], [])
        self.assertEqual([c['cmd'] or c['argv'] for c in self.calls('unknown')], [])
        self.assertEqual(self.calls('reboot'), [])


class Check(Device):
    """install.sh --check with the progress stream: what the graphical installer reads the device with."""

    def check(self, shell, **env):
        r = self.run_script(shell, 'install.sh', '--check', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, **env)
        facts = {e['key']: e['value'] for e in events(r.stdout) if e['event'] == 'info'}
        return r, events(r.stdout), facts

    def test_the_usual_device(self):
        for shell in self.each_shell():
            self.device()
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(ev[0], {'event': 'hello', 'script': 'install.sh', 'version': '1', 'mode': 'check'})
            self.assertEqual(ev[-1], {'event': 'done', 'result': 'ok'})
            self.assertEqual([e['id'] for e in ev if e['event'] == 'step'], ['preflight', 'storage'])
            self.assertEqual(facts['device.model'], 'F50 / MU300')
            self.assertEqual(facts['device.kind'], 'f50')
            self.assertEqual(facts['android.build'], 'MU300_ZYV1.0.0B09')
            self.assertEqual((facts['bootloader.locked'], facts['bootloader.verified']), ('0', 'orange'))
            self.assertEqual((facts['bootloader.device_state'], facts['bootloader.state']), ('unlocked', 'unlocked'))
            self.assertNotIn('The bootloader is locked', r.stdout)
            self.assertEqual((facts['root'], facts['root.su']), ('yes', '27.0:MAGISKSU'))
            self.assertEqual((facts['slot.android'], facts['slot.linux']), ('a', 'b'))
            self.assertEqual(facts['emmc.sectors'], '122159104')
            self.assertEqual(facts['emmc.last_end'], '54218752')
            self.assertEqual(facts['region.offset'], str(54222848 * 512))
            self.assertEqual(facts['region.size'], str((122150912 - 54222848) * 512))
            self.assertEqual((facts['region.existing'], facts['region.dirty']), ('no', '0'))
            self.assertEqual(facts['verdict.code'], 'tested')
            self.assertEqual(facts['sd.device'], '')
            parts = facts['partitions'].split()
            self.assertEqual(parts[-1], 'userdata:12275712:41943040')
            self.assertIn('boot_a:', facts['partitions'])
            # the text for people is all still there
            self.assertIn('result: OK: free and empty, same layout as the tested device', r.stdout)
            self.assertIn('Nothing was written.', r.stdout)
            self.assert_nothing_written()

    def test_the_other_device_and_slot(self):
        for shell in self.each_shell():
            self.device(model='U30_Air', device='U30Air', slot='b', locked='1', verified='green')
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(facts['device.kind'], 'u30air')
            self.assertEqual((facts['slot.android'], facts['slot.linux']), ('b', 'a'))
            self.assertEqual((facts['bootloader.locked'], facts['bootloader.verified']), ('1', 'green'))

    def test_a_locked_bootloader_is_a_warning_and_never_a_refusal(self):
        # the owner's decision of 2026-10-13: a locked bootloader with a patched TrustOS boots Linux
        warning = 'WARNING: The bootloader is locked. Did you patch TrustOS?'
        for shell in self.each_shell():
            for keys, want in ((dict(locked='1', device_state='locked', verified='green'), 'locked'),
                               (dict(locked='', device_state='locked'), 'locked'), (dict(locked='1', device_state=''), 'locked'),
                               (dict(locked='', device_state='unlocked'), 'unlocked'), (dict(locked='', device_state='', verified=''), 'unknown')):
                self.device(**keys)
                r, ev, facts = self.check(shell)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(facts['bootloader.state'], want, keys)
                self.assertEqual(warning in r.stdout, want == 'locked', keys)
                self.assertEqual(ev[-1], {'event': 'done', 'result': 'ok'})
            self.device(locked='1')
            r = self.run_script(shell, 'install.sh', '--check', MU300_NONINTERACTIVE=1, MU300_LANG='tr')
            self.assertIn("UYARI: Bootloader kilitli. TrustOS'u yamaladınız mı?", r.stdout)

    def test_no_root_is_said_before_it_stops(self):
        for shell in self.each_shell():
            self.device(root=False)
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(facts['root'], 'no')
            self.assertEqual(facts['device.model'], 'F50 / MU300')       # what needs no root is there all the same
            self.assertEqual(facts['bootloader.locked'], '0')
            self.assertEqual(ev[-1], {'event': 'error', 'message': 'su does not work on the device'})
            self.assert_nothing_written()

    def test_nothing_attached(self):
        for shell in self.each_shell():
            self.device(adb_state='none')
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(ev[-1]['event'], 'error')
            self.assertIn('no adb device', ev[-1]['message'])
            self.device(adb_state='unauthorized')
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 1)
            self.assertIn('no adb device', ev[-1]['message'])

    def test_an_unknown_model_is_a_question_with_a_variable(self):
        for shell in self.each_shell():
            self.device(model='Pixel 7', device='panther')
            r, ev, facts = self.check(shell)                    # the only adb device, and not ours: which one? is asked
            self.assertEqual(r.returncode, 1)
            self.assertIn('set MU300_ANS_DEVICE', ev[-1]['message'])
            r, ev, facts = self.check(shell, ANDROID_SERIAL='324950664950')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(facts['device.kind'], 'unknown')
            self.assertIn('set MU300_ANS_UNKNOWN_MODEL', ev[-1]['message'])
            r, ev, facts = self.check(shell, ANDROID_SERIAL='324950664950', MU300_ANS_UNKNOWN_MODEL='no')
            self.assertEqual(r.returncode, 1)
            self.assertNotIn('emmc.sectors', facts)
            r, ev, facts = self.check(shell, ANDROID_SERIAL='324950664950', MU300_ANS_UNKNOWN_MODEL='yes')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('not synced (the model is not recognised)', facts['bootchain'])
            self.assert_nothing_written()

    def test_an_installation_that_is_there(self):
        for shell in self.each_shell():
            self.device(existing=True, existing_blocks=1048576)
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(facts['region.existing'], 'yes')
            self.assertEqual(facts['region.size'], str(1048576 * 4096))
            self.assertEqual(facts['verdict.code'], 'existing')
            self.assertNotIn('region.dirty', facts)

    def test_data_in_the_free_space(self):
        for shell in self.each_shell():
            self.device(dirty=3)
            r, ev, facts = self.check(shell)
            self.assertEqual((facts['region.dirty'], facts['verdict.code']), ('3', 'dirty'))

    def test_the_sd_card(self):
        card = {'dev': 'mmcblk1', 'sectors': 124735488, 'partition': True, 'type': 'SD', 'state': 'yes'}
        for shell in self.each_shell():
            self.device(sd=card)
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((facts['sd.device'], facts['sd.bytes'], facts['sd.state']),
                             ('/dev/block/mmcblk1p1', str(124735488 * 512), 'yes'))
            # no room inside, a card in the slot: the card is the way, no repartitioning
            self.device(sd=dict(card, state='no'), layout='vendor64')
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((facts['region.size'], facts['verdict.code'], facts['sd.state']), ('0', 'sd-only', 'no'))
            self.assertNotIn('makeroom.first', facts)
            self.assert_nothing_written()

    def test_userdata_fills_the_disk(self):
        # issue #32: the figures of the make-room path, and --check writes nothing
        for shell in self.each_shell():
            self.device(layout='vendor64')
            r, ev, facts = self.check(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(facts['region.size'], '0')
            self.assertEqual((facts['makeroom.first'], facts['makeroom.end'], facts['makeroom.usable_end']),
                             ('12275712', '122155007', '122155007'))
            self.assertEqual((facts['makeroom.min_android'], facts['makeroom.min_linux'], facts['makeroom.max_linux']),
                             ('8388608', '1638400', '101490688'))
            self.assertIn('--check writes nothing', r.stdout)
            self.assertEqual(ev[-1], {'event': 'done', 'result': 'ok'})
            self.assertNotIn('question', [e['event'] for e in ev])
            self.assert_nothing_written()

    def test_adb_path(self):
        for shell in self.each_shell():
            self.device()
            r = self.run_script(shell, 'install.sh', '--adb-path')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, str(self.stubs / 'adb')))
            self.assertEqual(self.calls(), [])                  # it asks the device nothing
            # platform-tools unzipped next to where the installer is started from, not on PATH
            (self.stubs / 'adb').unlink()
            pt = self.tmp / 'platform-tools'
            pt.mkdir(exist_ok=True)
            (pt / 'adb').write_text('#!/bin/sh\nexit 0\n')
            (pt / 'adb').chmod(0o755)
            r = self.run_script(shell, 'install.sh', '--adb-path', PATH='/usr/bin:/bin')
            self.assertEqual((r.returncode, Path(r.stdout.strip()).resolve()), (0, (pt / 'adb').resolve()))
            (pt / 'adb').unlink()
            r = self.run_script(shell, 'install.sh', '--adb-path', PATH='/usr/bin:/bin')
            self.assertEqual((r.returncode, r.stdout), (1, ''))

    def test_the_language_is_a_question_too(self):
        for shell in self.each_shell():
            self.device()
            r = self.run_script(shell, 'install.sh', '--check', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, MU300_LANG='')
            self.assertEqual(r.returncode, 1)
            self.assertIn('set MU300_LANG', events(r.stdout)[-1]['message'])
            self.assertEqual(self.calls(), [])
            r = self.run_script(shell, 'install.sh', '--check', MU300_NONINTERACTIVE=1, MU300_LANG='tr')
            self.assertIn('Hiçbir şey yazılmadı.', r.stdout)

    def test_backup(self):
        for shell in self.each_shell():
            self.device(boot_image_bytes=70000)
            r = self.run_script(shell, 'install.sh', '--backup', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            facts = {e['key']: e['value'] for e in events(r.stdout) if e['event'] == 'info'}
            boot = Path(facts['backup.boot'])
            self.assertEqual(boot, self.tmp / 'work' / 'dumps' / 'boot_a.img')
            self.assertEqual(boot.stat().st_size, 70000)
            import hashlib
            self.assertEqual(hashlib.sha256(boot.read_bytes()).hexdigest(), facts['backup.sha256'])
            self.assertEqual(Path(facts['backup.misc']).stat().st_size, 4096)
            self.assertIn('Nothing was written to the device.', r.stdout)
            self.assertEqual(events(r.stdout)[-1], {'event': 'done', 'result': 'ok'})
            # only those two were read: nothing of prodnv or the NV partitions is ever named
            cmds = ' '.join(c['cmd'] for c in self.calls())
            self.assertNotRegex(cmds, r'prodnv|fixnv|runtimenv|deltanv|calinv|nr_phy|miscdata|persist')
            self.assertEqual(len(self.calls('pull')), 2)
            self.assert_nothing_written()
            # a pull that fails is not a backup
            self.device(pull=False)
            r = self.run_script(shell, 'install.sh', '--backup', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1,
                                MU300_WORK=str(self.tmp / 'work2'))
            self.assertEqual(r.returncode, 1)
            self.assertEqual(events(r.stdout)[-1]['event'], 'error')


ANSWERS = dict(MU300_ANS_SYSTEMS='3', MU300_OPENWRT='luci', MU300_ANS_BOOT_OS='ubuntu', MU300_ANS_UBUNTU='26.04',
               MU300_ANS_DEFAULT_LINUX='yes', MU300_ANS_BOOT_ATTEMPTS='4', MU300_ANS_HOTSPOT='no', MU300_ANS_GPU='no',
               MU300_ANS_VPN='no', MU300_ANS_ADB='no', MU300_ANS_PANEL='yes', MU300_ANS_KERNEL='6.18',
               MU300_ANS_PASSWORD='hunter2secret', MU300_ANS_CONFIRM='INSTALL')


class Install(Device):
    """install.sh with every answer in variables, as far as the first pull from the device - which the fake device
    refuses (pull=false), so the run ends there: after every question, before anything is downloaded or written."""

    def install(self, shell, answers, stdin='', **env):
        f = self.tmp / 'answers.txt'
        f.write_text(''.join(f'{k}={v}\n' for k, v in answers.items()))
        f.chmod(0o600)
        r = self.run_script(shell, 'install.sh', '--answers', str(f), stdin=stdin, MU300_PROGRESS=1, **env)
        ev = events(r.stdout)
        asked = [(e['id'], e.get('answer')) for e in ev if e['event'] == 'question' and 'state' not in e]
        return r, ev, asked

    def test_every_question_from_its_variable(self):
        for shell in self.each_shell():
            self.device(pull=False)
            r, ev, asked = self.install(shell, ANSWERS)
            self.assertEqual(asked, [('MU300_ANS_SYSTEMS', '3'), ('MU300_ANS_BOOT_OS', 'ubuntu'), ('MU300_ANS_UBUNTU', '26.04'),
                                     ('MU300_ANS_DEFAULT_LINUX', 'yes'), ('MU300_ANS_BOOT_ATTEMPTS', '4'),
                                     ('MU300_ANS_HOTSPOT', 'no'), ('MU300_ANS_GPU', 'no'), ('MU300_ANS_VPN', 'no'),
                                     ('MU300_ANS_ADB', 'no'), ('MU300_ANS_PANEL', 'yes'), ('MU300_ANS_KERNEL', '6.18'),
                                     ('MU300_ANS_PASSWORD', None)], r.stdout + r.stderr)
            self.assertEqual([e['id'] for e in ev if e['event'] == 'step' and e['id']], ['preflight', 'storage', 'choices', 'pull'])
            self.assertEqual(ev[-1]['event'], 'error')
            self.assertIn('could not read /dev/block/by-name/boot_a from the device', ev[-1]['message'])
            self.assertEqual(r.returncode, 1)
            self.assertEqual((r.stdout + r.stderr).count('hunter2'), 0)
            self.assertIn({'event': 'info', 'key': 'release', 'value': 'v2026.10.23'}, ev)
            self.assert_nothing_written()
            self.assert_no_secret_anywhere('hunter2')
            # the same with the answers in the environment, where the password would be inherited by every program
            self.device(pull=False)
            r = self.run_script(shell, 'install.sh', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, **ANSWERS)
            self.assertIn('could not read /dev/block/by-name/boot_a', events(r.stdout)[-1]['message'])
            self.assertEqual((r.stdout + r.stderr).count('hunter2'), 0)
            self.assert_no_secret_anywhere('hunter2')

    def test_a_locked_bootloader_does_not_stop_an_installation(self):
        for shell in self.each_shell():
            self.device(pull=False, locked='1', device_state='locked')
            r, ev, asked = self.install(shell, ANSWERS)
            self.assertIn('WARNING: The bootloader is locked. Did you patch TrustOS?', r.stdout)
            self.assertEqual(len(asked), 12)                                # every question was still asked
            self.assertIn('could not read /dev/block/by-name/boot_a from the device', ev[-1]['message'])
        # ... and the plan says it again, right before the question
        src = (TOP / 'install.sh').read_text()
        plan = src[src.index("say \"$(t 'Ready to install')\""):src.index('ask confirm')]
        self.assertIn('[ $BL_STATE != locked ] || echo "  $(t \'WARNING: {1}\' "$BL_WARN")"', plan)
        self.assertNotRegex(src, r'BL_STATE[^\n]*\bdie\b')

    def test_a_missing_answer_stops_before_anything_is_read_or_written(self):
        for missing in ('MU300_ANS_SYSTEMS', 'MU300_ANS_KERNEL', 'MU300_ANS_PASSWORD', 'MU300_ANS_DEFAULT_LINUX',
                        'MU300_OPENWRT', 'MU300_ANS_VPN'):
            answers = {k: v for k, v in ANSWERS.items() if k != missing}
            for shell in self.each_shell():
                self.device(pull=False)
                r, ev, asked = self.install(shell, answers)
                self.assertEqual(r.returncode, 1, missing)
                self.assertEqual(ev[-1]['event'], 'error')
                self.assertIn('set ' + missing, ev[-1]['message'])
                self.assertNotIn(missing, [a[0] for a in asked])
                self.assertEqual(self.calls('pull'), [], missing)
                self.assert_nothing_written()

    def test_an_answer_the_installer_refuses(self):
        for shell in self.each_shell():
            self.device(pull=False)
            # Ubuntu 26.04 needs a mainline kernel: the kernel question comes back, and stops
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_KERNEL='5.4'))
            self.assertEqual(r.returncode, 1)
            self.assertIn('the answer in MU300_ANS_KERNEL was not accepted: 5.4', ev[-1]['message'])
            self.assertIn('Ubuntu 26.04 needs a mainline kernel', r.stdout)
            for bad in (dict(MU300_ANS_SYSTEMS='7'), dict(MU300_ANS_BOOT_OS='android'), dict(MU300_ANS_BOOT_ATTEMPTS='9'),
                        dict(MU300_ANS_UBUNTU='22.04'), dict(MU300_ANS_PASSWORD='short'), dict(MU300_OPENWRT='both')):
                r, ev, asked = self.install(shell, dict(ANSWERS, **bad))
                self.assertEqual(r.returncode, 1, bad)
                self.assertEqual(ev[-1]['event'], 'error', bad)
                self.assertEqual(self.calls('pull'), [], bad)
            self.assert_nothing_written()

    def test_an_installation_that_is_there_asks_update_or_wipe(self):
        for shell in self.each_shell():
            self.device(pull=False, existing=True, vpn_on='ubuntu')
            r, ev, asked = self.install(shell, ANSWERS)
            self.assertIn('set MU300_ANS_EXISTING', ev[-1]['message'])
            # the VPN is on in the system being replaced: the question says so, and its answer is still the variable's
            self.assertIn('The VPN is on in the system being replaced (ubuntu)', r.stdout)
            self.assertIn(('MU300_ANS_VPN', 'no'), asked)
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_EXISTING='update'))
            self.assertIn(('MU300_ANS_EXISTING', 'update'), asked)
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])
            self.assert_nothing_written()

    def test_data_in_the_free_space_needs_its_word(self):
        for shell in self.each_shell():
            self.device(pull=False, dirty=2)
            r, ev, asked = self.install(shell, ANSWERS)
            self.assertIn('set MU300_ANS_OVERWRITE', ev[-1]['message'])
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_OVERWRITE=''))        # set to nothing: the default
            self.assertEqual(ev[-1], {'event': 'error', 'message': 'cancelled'})
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_OVERWRITE='overwrite'))
            self.assertEqual(asked[0], ('MU300_ANS_OVERWRITE', 'overwrite'))
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])

    def test_the_sd_card(self):
        card = {'dev': 'mmcblk1', 'sectors': 124735488, 'partition': True, 'type': 'SD', 'state': 'no'}
        for shell in self.each_shell():
            self.device(pull=False, sd=card)
            r, ev, asked = self.install(shell, ANSWERS)
            self.assertIn('set MU300_STORAGE', ev[-1]['message'])
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_STORAGE='sd'))
            self.assertIn({'event': 'info', 'key': 'storage', 'value': 'sd'}, ev)
            self.assertIn('set MU300_ANS_ERASE_SD', ev[-1]['message'])
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_STORAGE='sd', MU300_ANS_ERASE_SD='ERASE'))
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])
            # a card that holds an installation, and internal storage wanted: the typed word has its variable too
            self.device(pull=False, sd=dict(card, state='yes'))
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_STORAGE='internal'))
            self.assertIn('set MU300_ANS_INTERNAL_OVER_CARD', ev[-1]['message'])
            # another filesystem on the card is never formatted, whatever the answers say
            self.device(pull=False, sd=dict(card, state='foreign'))
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_STORAGE='sd', MU300_ANS_ERASE_SD='ERASE'))
            self.assertIn('never formats one that is not its own', ev[-1]['message'])
            self.assert_nothing_written()

    def test_making_room_is_never_assumed(self):
        for shell in self.each_shell():
            self.device(pull='gpt', layout='vendor64')
            r, ev, asked = self.install(shell, ANSWERS)
            self.assertIn('set MU300_ANS_MAKE_ROOM', ev[-1]['message'])
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_MAKE_ROOM='no'))
            self.assertEqual(ev[-1], {'event': 'error', 'message': 'nothing was changed.'})
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_MAKE_ROOM='yes', MU300_ANS_LINUX_GIB='4'))
            self.assertIn('set MU300_ANS_ERASE_ANDROID', ev[-1]['message'])
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_MAKE_ROOM='yes', MU300_ANS_LINUX_GIB='4',
                                                    MU300_ANS_ERASE_ANDROID='erase'))        # not the word
            self.assertEqual(ev[-1], {'event': 'error', 'message': 'nothing was changed.'})
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_MAKE_ROOM='yes', MU300_ANS_LINUX_GIB='900',
                                                    MU300_ANS_ERASE_ANDROID='ERASE'))
            self.assertIn('the answer in MU300_ANS_LINUX_GIB was not accepted: 900', ev[-1]['message'])
            self.assert_nothing_written()

    def test_an_answer_on_standard_input(self):
        for shell in self.each_shell():
            self.device(pull=False)
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_SYSTEMS='@stdin'), stdin='2\n')
            waiting = [e for e in ev if e['event'] == 'question' and e.get('state') == 'waiting']
            self.assertEqual([e['id'] for e in waiting], ['MU300_ANS_SYSTEMS'])
            self.assertEqual(asked[0], ('MU300_ANS_SYSTEMS', '2'))
            self.assertNotIn('MU300_ANS_BOOT_OS', [a[0] for a in asked])       # one system: nothing to choose

    # An update asks whether to keep the passwords of the systems it replaces; keeping one takes its current password,
    # checked on the device (android-pwcheck.sh, which the fake answers from "passwords"). Nothing past the first pull
    # is reached by a run that stops, and no password is anywhere.
    UPDATE = dict(ANSWERS, MU300_ANS_EXISTING='update')

    def pw_questions(self, asked):
        return [a for a in asked if 'PASSWORD' in a[0]]

    def test_keep_with_the_right_current_password(self):
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'ubuntu': 'old secret 1', 'openwrt-luci': 'oldroot99'})
            r, ev, asked = self.install(shell, dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD_UBUNTU='old secret 1',
                                                    MU300_ANS_CURRENT_PASSWORD='oldroot99'))
            self.assertEqual(self.pw_questions(asked), [('MU300_ANS_KEEP_PASSWORD', 'yes'),
                                                        ('MU300_ANS_CURRENT_PASSWORD_UBUNTU', None),
                                                        ('MU300_ANS_CURRENT_PASSWORD', None)], r.stdout + r.stderr)
            self.assertIn('confirmed: the password of ubuntu stays as it is', r.stdout)
            self.assertIn('confirmed: the password of openwrt-luci stays as it is', r.stdout)
            self.assertIn({'event': 'info', 'key': 'password.ubuntu', 'value': 'MATCH'}, ev)
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])     # on to the first pull
            self.assert_nothing_written()
            self.assert_no_secret_anywhere('old secret')
            self.assert_no_secret_anywhere('oldroot99')
            self.assertEqual((r.stdout + r.stderr).count('oldroot99'), 0)

    def test_the_check_uses_the_releases_own_tools(self):
        # android-pwcheck.sh runs nothing of the Linux system it checks: the release's 5.4 bundle (its mu300-pwcheck and
        # busybox) is fetched, checked against SHA256SUMS, pushed, and handed to it; the installation reuses the file
        import hashlib, io, tarfile
        rel = self.tmp / 'rel'
        rel.mkdir()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as t:
            for name, data in (('./busybox', b'#!bb\n'), ('./mu300-pwcheck', b'\x7fELF pwcheck\n')):
                i = tarfile.TarInfo(name); i.size = len(data); t.addfile(i, io.BytesIO(data))
        (rel / 'mu300-kernel.tar.gz').write_bytes(buf.getvalue())
        (rel / 'SHA256SUMS').write_text(hashlib.sha256(buf.getvalue()).hexdigest() + '  mu300-kernel.tar.gz\n')
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'ubuntu': 'old secret 1'})
            r, ev, asked = self.install(shell, dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD='old secret 1'),
                                        MU300_RELEASE_URL=rel.as_uri())
            self.assertIn('confirmed: the password of ubuntu stays as it is', r.stdout, r.stdout + r.stderr)
            pushed = [c['argv'][-1] for c in self.calls('push')]
            self.assertIn('/data/local/tmp/mu300-pwcheck', pushed)
            self.assertIn('/data/local/tmp/mu300-pwbb', pushed)
            check = [c['cmd'] for c in self.calls() if '< /data/local/tmp/mu300-pwcheck.in' in (c['cmd'] or '')]
            self.assertTrue(check and 'MU300_PWCHECK_BIN=/data/local/tmp/mu300-pwcheck MU300_PWCHECK_BB=/data/local/tmp/mu300-pwbb sh '
                            in check[0], check)
            self.assertNotIn('chroot', ' '.join(check))
            self.assertTrue((self.tmp / 'work/release/v2026.10.23/mu300-kernel.tar.gz').exists())
            self.assert_nothing_written()

    def test_keep_with_a_wrong_current_password_stops(self):
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'ubuntu': 'old secret 1'})
            r, ev, asked = self.install(shell, dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD='guess123'))
            self.assertEqual(r.returncode, 1)
            self.assertIn('that is not the current password of "ubuntu" in ubuntu', r.stdout)
            self.assertIn('was not confirmed. Nothing was changed: run the installer again and answer no', ev[-1]['message'])
            self.assertEqual(self.calls('pull'), [])
            self.assert_nothing_written()
            self.assertEqual((r.stdout + r.stderr).count('guess123'), 0)

    def test_keep_an_empty_password_with_an_empty_entry(self):
        # OpenWrt's root as it ships: no password, confirmed by an empty answer; no new password is asked for
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'openwrt-luci': ''})
            ans = {k: v for k, v in self.UPDATE.items() if k != 'MU300_ANS_PASSWORD'}
            r, ev, asked = self.install(shell, dict(ans, MU300_ANS_SYSTEMS='2', MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD=''))
            self.assertEqual(self.pw_questions(asked), [('MU300_ANS_KEEP_PASSWORD', 'yes'), ('MU300_ANS_CURRENT_PASSWORD', None)])
            self.assertIn('confirmed: the password of openwrt-luci stays as it is', r.stdout)
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])
            # a non-empty answer is not the empty password
            r, ev, asked = self.install(shell, dict(ans, MU300_ANS_SYSTEMS='2', MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD='something'))
            self.assertIn('was not confirmed', ev[-1]['message'])
            self.assert_nothing_written()

    def test_no_sets_the_new_password(self):
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'ubuntu': 'old secret 1', 'openwrt-luci': ''})
            r, ev, asked = self.install(shell, dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='no'))
            self.assertEqual(self.pw_questions(asked), [('MU300_ANS_KEEP_PASSWORD', 'no'), ('MU300_ANS_PASSWORD', None)])
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])
            # the new one is still typed twice: two that differ stop the run
            r = self.run_script(shell, 'install.sh', stdin='', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1,
                                **dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='no', MU300_ANS_PASSWORD='short'))
            self.assertIn('passwords differ or are shorter than 6 characters', events(r.stdout)[-1]['message'])
            self.assert_nothing_written()

    def test_a_system_installed_new_still_gets_the_new_password(self):
        # Ubuntu is updated and kept; OpenWrt has no account there yet (installed new): the new password is asked for
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'ubuntu': 'old secret 1'})
            r, ev, asked = self.install(shell, dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD='old secret 1'))
            self.assertEqual(self.pw_questions(asked), [('MU300_ANS_KEEP_PASSWORD', 'yes'), ('MU300_ANS_CURRENT_PASSWORD', None),
                                                        ('MU300_ANS_PASSWORD', None)])
            self.assertIn('could not read /dev/block/by-name/boot_a', ev[-1]['message'])

    def test_a_password_that_cannot_be_checked_stops(self):
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'ubuntu': 'old secret 1'}, pwcheck='UNVERIFIABLE')
            r, ev, asked = self.install(shell, dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD='old secret 1'))
            self.assertIn('could not be checked on the device (UNVERIFIABLE)', ev[-1]['message'])
            self.device(pull=False, existing=True, passwords={'ubuntu': '!'})
            r, ev, asked = self.install(shell, dict(self.UPDATE, MU300_ANS_KEEP_PASSWORD='yes',
                                                    MU300_ANS_CURRENT_PASSWORD='old secret 1'))
            self.assertIn('has no password login', ev[-1]['message'])
            self.assertEqual(self.calls('pull'), [])
            self.assert_nothing_written()

    def test_no_question_without_a_password_to_keep(self):
        # a fresh installation, a wipe, and an update of a filesystem whose systems have no account of theirs
        for shell in self.each_shell():
            self.device(pull=False)
            r, ev, asked = self.install(shell, ANSWERS)
            self.assertEqual(self.pw_questions(asked), [('MU300_ANS_PASSWORD', None)])
            self.device(pull=False, existing=True)
            r, ev, asked = self.install(shell, self.UPDATE)
            self.assertEqual(self.pw_questions(asked), [('MU300_ANS_PASSWORD', None)])
            self.device(pull=False, existing=True, passwords={'ubuntu': 'old secret 1'})
            r, ev, asked = self.install(shell, dict(ANSWERS, MU300_ANS_EXISTING='wipe'))
            self.assertEqual(self.pw_questions(asked), [('MU300_ANS_PASSWORD', None)])

    def test_keep_asks_three_times_at_the_terminal(self):
        # interactive: a wrong entry asks again, three in all, then the run stops before anything is pulled
        for shell in self.each_shell():
            self.device(pull=False, existing=True, passwords={'openwrt-luci': 'oldroot99'})
            env = dict(MU300_OPENWRT='luci', MU300_STORAGE='internal')
            # systems, default linux, attempts, hotspot, gpu, vpn, adb, kernel, update/wipe, keep, then the passwords
            answers = ['2', 'yes', '5', 'no', 'no', 'no', 'no', '2', 'update', 'yes', 'a1', 'b2', 'c3']
            r = self.run_script(shell, 'install.sh', stdin='\n'.join(answers) + '\n', **env)
            out = r.stdout + r.stderr
            self.assertEqual(out.count('that is not the current password of "root" in openwrt-luci'), 3, out)
            self.assertIn('was not confirmed. Nothing was changed', out)
            self.assertEqual(self.calls('pull'), [])
            self.assert_nothing_written()
            # the right one on the second try
            self.device(pull=False, existing=True, passwords={'openwrt-luci': 'oldroot99'})
            answers[-2:] = ['oldroot99']
            r = self.run_script(shell, 'install.sh', stdin='\n'.join(answers) + '\n', **env)
            out = r.stdout + r.stderr
            self.assertEqual(out.count('that is not the current password'), 1, out)
            self.assertIn('confirmed: the password of openwrt-luci stays as it is', out)
            self.assertIn('could not read /dev/block/by-name/boot_a', out)

    def test_every_question_has_a_variable(self):
        # an ask without one cannot be answered in answers mode: none may be added that way
        for name in ('install.sh', 'uninstall.sh', 'tools/storage.sh', 'tools/linux-mode.sh', 'tools/releases.sh'):
            src = (TOP / name).read_text()
            for m in re.finditer(r'^[^#\n]*\bask [a-zA-Z_][^\n]*$', src, re.M):
                line = m.group(0)
                if 'ask VAR' in line or line.lstrip().startswith('ask()'):
                    continue
                self.assertRegex(line, r' MU300_[A-Z_]+(;| ;;|\s+#.*)?($|; )', f'{name}: {line.strip()}')
        header = (TOP / 'install.sh').read_text().split('\nset -eu\n')[0]
        for var in set(re.findall(r'\bMU300_ANS_[A-Z_]+|\bMU300_OPENWRT\b|\bMU300_STORAGE\b',
                                  ''.join((TOP / n).read_text().split('\nset -eu\n')[-1]
                                          for n in ('install.sh', 'tools/storage.sh', 'tools/linux-mode.sh',
                                                    'tools/releases.sh', 'tools/answers.sh')))):
            self.assertIn(var, header, f'{var} is not in the header of install.sh')


class PowerShellNames(unittest.TestCase):
    def test_the_answers_variables_cannot_collide_with_the_installers(self):
        # PowerShell's variables ignore case and a dot-sourced file shares its caller's scope: install.ps1's own
        # "$ans = Ask ..." once replaced the dictionary of answers ($script:Ans then) with a string
        mine = set()
        for name in ('tools/answers.ps1', 'tools/gui-launch.ps1'):
            mine |= {m.lower() for m in re.findall(r'\$script:([A-Za-z_][A-Za-z0-9_]*)', (TOP / name).read_text())}
        self.assertIn('muanswers', mine)
        for name in ('install.ps1', 'uninstall.ps1'):
            src = (TOP / name).read_text()
            for var in sorted(mine):
                if var == 'answerqueue':        # install.ps1's own (-Answers), which gui-launch.ps1 only reads
                    continue
                self.assertTrue(var.startswith('mu') or var.startswith('gui'), f'{var}: no prefix of its own')
                bare = re.findall(r'(?i)\$(?!script:)' + re.escape(var) + r'\b', src)
                self.assertEqual(bare, [], f'{name} has a variable of its own called ${var}')

class UninstallInPartition(Device):
    """uninstall.sh on a fake eMMC (tests/fakeemmc.py) whose system is in a GPT partition named mu300root, the layout
    of kanoqwq's desktop installer (FINDINGS 179): it erased the filesystem inside the partition, never the partition
    or anything around it. Before, it refused: "the mu300root filesystem overlaps a partition"."""
    PSTART, PSECT = 54218752, 128 << 11

    def setUp(self):
        super().setUp()
        self.emmc = fakeemmc.make(self.tmp / 'emmc', layout='part64')

    def ext4(self, label=b'mu300root', blocks=(128 << 20) // 4096):
        at = self.PSTART * 512
        with open(self.emmc / 'dev/block/mmcblk0', 'r+b') as f:
            f.seek(at + 1024 + 4); f.write(struct.pack('<I', blocks))
            f.seek(at + 1080); f.write(b'\x53\xef')
            f.seek(at + 1144); f.write(label.ljust(16, b'\0'))
            # data in the filesystem, and right in front of and behind the partition
            f.seek(at + (100 << 20)); f.write(b'files' * 100)
            f.seek(at - 4096); f.write(b'in front' * 10)
            f.seek(at + self.PSECT * 512); f.write(b'behind' * 10)

    def around(self):
        at = self.PSTART * 512
        with open(self.emmc / 'dev/block/mmcblk0', 'rb') as f:
            f.seek(at - 4096); front = f.read(80)
            f.seek(at + self.PSECT * 512); back = f.read(60)
            f.seek(at + 1080); magic = f.read(2)
            f.seek(at + (100 << 20)); files = f.read(5)
        return front, back, magic, files

    def uninstall(self, shell, **env):
        self.device(layout='part64', emmc=str(self.emmc), magisk='')
        r = self.run_script(shell, 'uninstall.sh', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1,
                            MU300_ANS_CONFIRM='UNINSTALL', **env)
        return r, events(r.stdout)

    def test_the_filesystem_in_the_partition_is_erased_the_partition_stays(self):
        for shell in self.each_shell():
            for wipe in ('quick', 'secure'):
                shutil.rmtree(self.emmc); self.emmc = fakeemmc.make(self.tmp / 'emmc', layout='part64')
                self.ext4()
                before = fakeemmc.table(self.emmc)
                self.assertEqual(before[-1], ('mu300root', self.PSTART, self.PSTART + self.PSECT - 1))
                r, ev = self.uninstall(shell, MU300_ANS_WIPE=wipe)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
                self.assertNotIn('overlaps a partition', r.stdout + r.stderr)
                facts = {e['key']: e['value'] for e in ev if e['event'] == 'info'}
                self.assertEqual((facts['region.existing'], facts['region.offset'], facts['region.size'],
                                  facts['region.partition']), ('yes', str(self.PSTART * 512), str(128 << 20), '1'))
                self.assertIn('the partition itself stays', r.stdout)
                self.assertIn('the partition named mu300root too: it stays', r.stdout)
                self.assertIn('the partition table is left alone', r.stdout)
                front, back, magic, files = self.around()
                self.assertEqual((front, back), (b'in front' * 10, b'behind' * 10))
                self.assertEqual(magic, b'\0\0')
                # quick: the headers only (the files stay until the space is reused); secure: everything
                self.assertEqual(files, b'files' if wipe == 'quick' else bytes(5))
                self.assertEqual(fakeemmc.table(self.emmc), before)
                self.assertNotIn('resize-last-partition', ' '.join(c['cmd'] for c in self.calls()))

    def test_keep_another_filesystem_and_two_partitions(self):
        for shell in self.each_shell():
            self.ext4()
            r, ev = self.uninstall(shell, MU300_ANS_WIPE='keep')
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
            self.assertEqual(self.around()[2], b'\x53\xef')
            self.assertIn('kept on the eMMC', r.stdout)
            # another installer's ext4 that is not labelled mu300root: not this project's, nothing asked about it
            self.ext4(label=b'rootfs')
            r, ev = self.uninstall(shell)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
            self.assertIn('no mu300root filesystem found', r.stdout)
            self.assertIn('holds another Linux filesystem', r.stdout)
            self.assertEqual(self.around()[2], b'\x53\xef')
        # two partitions of that name: which one is the system cannot be told, nothing is changed
        fakeemmc.sysfs(self.emmc, fakeemmc.table(self.emmc) + [('mu300root', self.PSTART + self.PSECT, self.PSTART + self.PSECT + 2047)])
        for shell in self.each_shell():
            r, ev = self.uninstall(shell, MU300_ANS_WIPE='quick')
            self.assertEqual(r.returncode, 1)
            self.assertIn('2 partitions named mu300root', ev[-1]['message'])
            self.assertEqual(self.calls('write'), [])

    def test_reset_password_mounts_inside_the_partition(self):
        self.ext4()
        for shell in self.each_shell():
            self.device(layout='part64', emmc=str(self.emmc))
            r = self.run_script(shell, 'tools/reset-password.sh', 'openwrt')
            self.assertIn(f'internal storage: offset {self.PSTART * 512}, 128 MiB, in the GPT partition named mu300root',
                          r.stdout)
            # the mount itself needs a device (losetup): what it was asked for is enough here
            mount = [c['cmd'] for c in self.calls() if 'android-mount-mu300root.sh /data/local/tmp/mu300root' in c['cmd']]
            self.assertEqual(mount[:1], [f'MU300_OFF={self.PSTART * 512} MU300_SIZE={128 << 20} sh '
                                         '/data/local/tmp/android-mount-mu300root.sh /data/local/tmp/mu300root'])


class Uninstall(Device):
    def uninstall(self, shell, **env):
        r = self.run_script(shell, 'uninstall.sh', MU300_NONINTERACTIVE=1, MU300_PROGRESS=1, **env)
        return r, events(r.stdout)

    def test_nothing_is_removed_without_the_word(self):
        for shell in self.each_shell():
            self.device(existing=True)
            r, ev = self.uninstall(shell)
            self.assertEqual(r.returncode, 1)
            self.assertIn('set MU300_ANS_WIPE', ev[-1]['message'])
            r, ev = self.uninstall(shell, MU300_ANS_WIPE='keep')
            self.assertIn('set MU300_ANS_CONFIRM', ev[-1]['message'])
            r, ev = self.uninstall(shell, MU300_ANS_WIPE='keep', MU300_ANS_CONFIRM='')
            self.assertEqual(ev[-1], {'event': 'error', 'message': 'cancelled'})
            r, ev = self.uninstall(shell, MU300_ANS_WIPE='keep', MU300_ANS_CONFIRM='uninstall')
            self.assertEqual(ev[-1], {'event': 'error', 'message': 'cancelled'})
            self.assertEqual(ev[0], {'event': 'hello', 'script': 'uninstall.sh', 'version': '1', 'mode': 'uninstall'})
            facts = {e['key']: e['value'] for e in ev if e['event'] == 'info'}
            self.assertEqual((facts['region.existing'], facts['slot.linux']), ('yes', 'b'))
            self.assert_nothing_written()

    def test_help(self):
        for name in ('install.sh', 'uninstall.sh'):
            for shell in self.each_shell():
                self.device()
                r = self.run_script(shell, name, '--help')
                self.assertEqual(r.returncode, 0)
                self.assertIn('--answers FILE', r.stdout)
                self.assertIn('MU300_ANS_CONFIRM=', r.stdout)
                self.assertNotIn('set -eu', r.stdout)
                self.assertEqual(self.calls(), [])
                r = self.run_script(shell, name, '--answers')
                self.assertEqual(r.returncode, 2)
                r = self.run_script(shell, name, '--frobnicate')
                self.assertEqual(r.returncode, 2)


if __name__ == '__main__':
    unittest.main()
