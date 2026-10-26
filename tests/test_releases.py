"""Which release the installers take: tools/release_pick.py (the decision, for install.sh, install.ps1 and the
graphical installer) against fake answers of GitHub's API, and tools/releases.sh's release_resolve - the beta is
asked about only when there is one newer than the newest full release, never with MU300_RELEASE set, and a GitHub
that does not answer the list leaves the newest release, as before."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest

from helpers import TOP, ShellTest

PICK = TOP / 'tools' / 'release_pick.py'
spec = importlib.util.spec_from_file_location('release_pick', PICK)
release_pick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_pick)


def rel(tag, when, prerelease=False, draft=False, files=None):
    return {'tag_name': tag, 'published_at': when, 'created_at': when, 'prerelease': prerelease, 'draft': draft,
            'assets': [{'name': n, 'size': s} for n, s in (files or {}).items()]}


FULL = rel('v2026.10.23', '2026-10-12T17:55:05Z', files={'mu300-kernel.tar.gz': 26971198, 'SHA256SUMS': 1011})
OLDER = rel('v2026.10.22', '2026-10-12T09:41:29Z')
OLD_BETA = rel('v2026.10.19', '2026-10-11T16:18:01Z', prerelease=True)
NEW_BETA = rel('v2026.10.25', '2026-10-14T08:00:00Z', prerelease=True, files={'mu300-kernel.tar.gz': 27000000})
NEWER_BETA = rel('v2026.10.26', '2026-10-15T08:00:00Z', prerelease=True)
RATE_LIMIT = {'message': "API rate limit exceeded for 203.0.113.7.", 'documentation_url': 'https://docs.github.com/'}


class Pick(unittest.TestCase):
    def test_no_prerelease(self):
        r = release_pick.pick([FULL, OLDER])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', None))

    def test_a_prerelease_the_full_release_has_overtaken_is_not_offered(self):
        r = release_pick.pick([FULL, OLDER, OLD_BETA])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', None))

    def test_a_newer_prerelease_is_the_beta(self):
        # in any order: the API's own order is not relied on
        for order in ([NEW_BETA, FULL, OLDER, OLD_BETA], [OLD_BETA, OLDER, FULL, NEW_BETA]):
            r = release_pick.pick(order)
            self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', 'v2026.10.25'))
        self.assertEqual(r['files']['v2026.10.25'], {'mu300-kernel.tar.gz': 27000000})
        self.assertEqual(r['files']['v2026.10.23']['SHA256SUMS'], 1011)

    def test_the_newest_of_two_betas(self):
        self.assertEqual(release_pick.pick([NEW_BETA, NEWER_BETA, FULL])['beta'], 'v2026.10.26')

    def test_drafts_are_nobodys_release(self):
        draft_full = rel('v2026.11.01', '2026-11-01T00:00:00Z', draft=True)
        draft_beta = rel('v2026.11.02', '2026-11-02T00:00:00Z', prerelease=True, draft=True)
        r = release_pick.pick([draft_full, draft_beta, FULL])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', None))

    def test_only_prereleases_is_no_release_and_no_beta(self):
        # "newer than the newest full release" cannot be said without one
        r = release_pick.pick([NEW_BETA])
        self.assertEqual((r['latest'], r['beta']), (None, None))

    def test_what_is_not_a_list_of_releases(self):
        for answer in (RATE_LIMIT, None, [], {}, 'x', 7, [7, 'x', None, {}], [{'tag_name': 5}], [{'tag_name': ''}]):
            r = release_pick.pick(answer)
            self.assertEqual((r['latest'], r['beta']), (None, None), answer)

    def test_text(self):
        self.assertEqual(release_pick.pick_text(json.dumps([FULL, NEW_BETA]))['beta'], 'v2026.10.25')
        for text in ('', '<html>blocked</html>', '{"message": "Not Found"}', '[{"tag_name": '):
            self.assertEqual(release_pick.pick_text(text)['latest'], None, text)

    def run_tool(self, text, *args):
        return subprocess.run([sys.executable, str(PICK)] + list(args), input=text.encode(), capture_output=True)

    def test_command_line(self):
        r = self.run_tool(json.dumps([FULL, NEW_BETA, OLD_BETA]))
        self.assertEqual(r.stdout.decode().split(), ['LATEST=v2026.10.23', 'BETA=v2026.10.25'])
        r = self.run_tool(json.dumps([FULL, OLD_BETA]))
        self.assertEqual(r.stdout.decode().split(), ['LATEST=v2026.10.23'])
        for text in (json.dumps(RATE_LIMIT), '', 'not json'):
            r = self.run_tool(text)
            self.assertEqual((r.returncode, r.stdout), (0, b''), text)
        r = self.run_tool(json.dumps([FULL, NEW_BETA]), '--json')
        self.assertEqual(json.loads(r.stdout.decode())['beta'], 'v2026.10.25')

    def test_a_tag_that_is_not_plain_is_not_passed_on(self):
        # the tag goes into a URL and a directory name of the installers
        evil = rel('v1; rm -rf ~', '2026-10-20T00:00:00Z', prerelease=True)
        r = self.run_tool(json.dumps([FULL, evil]))
        self.assertEqual(r.stdout.decode().split(), ['LATEST=v2026.10.23'])
        r = self.run_tool(json.dumps([rel('../../x', '2026-10-20T00:00:00Z'), NEW_BETA]))
        self.assertEqual(r.stdout, b'')
        r = self.run_tool(json.dumps([FULL, evil]), '--json')
        self.assertEqual(json.loads(r.stdout.decode()), {'latest': 'v2026.10.23', 'beta': None,
                                                         'files': {'v2026.10.23': release_pick._files(FULL)}})


TAGS = TOP / 'tests' / 'fixtures' / 'release-tags.tsv'
BIN_UPDATE = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-update'
# the regular expression of a release tag (ERE), where a device program checks one with grep: the panel's adapters
TAG_ERE = r'v[0-9]{4}\.[0-9]{2}\.[0-9]{2}|v[0-9]{4}\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])\.[1-9][0-9]?'


def tag_table():
    """(oks, bads, [(a, r, b)]) of tests/fixtures/release-tags.tsv: the table every language is held to"""
    oks, bads, cmps = [], [], []
    for line in TAGS.read_text().split('\n'):
        if not line or line.startswith('#'):
            continue
        f = line.split('\t')
        if f[0] == 'ok':
            oks.append(f[1])
        elif f[0] == 'bad':
            bads.append(f[1])
        else:
            cmps.append((f[1], f[2], f[3]))
    return oks, bads, cmps


def relation(newer, ok, a, b):
    if newer(a, b):
        return 'both' if newer(b, a) else '>'
    if newer(b, a):
        return '<'
    return '=' if ok(a) and ok(b) else '!'


class Tags(unittest.TestCase):
    """The release tags (docs/FINDINGS.md 125) in Python: tools/release_pick.py's tag_key, tag_ok and newer."""

    def test_the_table(self):
        oks, bads, cmps = tag_table()
        self.assertGreater(len(oks), 5)
        self.assertGreater(len(bads), 10)
        self.assertGreater(len(cmps), 20)
        for t in oks:
            self.assertTrue(release_pick.tag_ok(t), t)
        for t in bads:
            self.assertFalse(release_pick.tag_ok(t), t)
        for a, r, b in cmps:
            self.assertEqual(relation(release_pick.newer, release_pick.tag_ok, a, b), r, (a, b))
        for t in (None, 5, 'v2026.10.16.1\n', 'v2026.10.16.١', 'v٢026.10.16.1'):
            self.assertFalse(release_pick.tag_ok(t), repr(t))

    def test_the_regular_expression_of_the_device_programs(self):
        # the same expression in each: the panel's rpcd (the arguments of update_act), the update adapter (the tag
        # it starts an update to) and dashboard-info (the note of a newer release on the home page)
        oks, bads, _ = tag_table()
        rx = re.compile(TAG_ERE)
        for t in oks:
            self.assertTrue(rx.fullmatch(t), t)
        for t in bads:
            self.assertFalse(rx.fullmatch(t), t)
        luci = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec'
        self.assertIn(f"allowed \"$tag\" '({TAG_ERE})?'", (luci / 'rpcd' / 'mu300dash').read_text())
        for f in ('update', 'dashboard-info'):
            self.assertIn(f"LC_ALL=C grep -Eqx -- '{TAG_ERE}'", (luci / 'unisoc-modem' / f).read_text(), f)

    def test_a_beta_older_by_its_tag_is_no_beta(self):
        # published after the release, but an older release: what mu300-update would not offer either
        full = rel('v2026.10.16.2', '2026-10-16T10:00:00Z')
        r = release_pick.pick([full, rel('v2026.10.27', '2026-10-17T10:00:00Z', prerelease=True)])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.16.2', None))
        r = release_pick.pick([full, rel('v2026.10.16.10', '2026-10-17T10:00:00Z', prerelease=True)])
        self.assertEqual(r['beta'], 'v2026.10.16.10')
        # a four-field release after the last three-field one is the newest; a test build is what it always was
        r = release_pick.pick([rel('v2026.10.27', '2026-10-15T07:56:46Z'), rel('v2026.10.16.1', '2026-10-16T08:00:00Z'),
                               rel('test-issue-102', '2026-10-17T08:00:00Z', prerelease=True)])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.16.1', 'test-issue-102'))


class ShellTags(ShellTest):
    """The same table in sh: tools/releases.sh (install.sh, make-release.sh) and its copies in mu300-update and
    tools/android-install.sh (the Magisk installer's and the computer installers' update on Android)."""

    def block(self, text):
        m = re.search(r'\n# --- release-tag begin\n.*?\n# --- release-tag end\n', '\n' + text, re.S)
        self.assertIsNotNone(m)
        return m.group(0)

    def test_the_copies_are_the_same(self):
        lib = self.block((TOP / 'tools/releases.sh').read_text())
        for f in (BIN_UPDATE, TOP / 'tools/android-install.sh'):
            self.assertEqual(self.block(f.read_text()), lib, f)

    @unittest.skipIf(os.name == 'nt', 'the device scripts run on Linux and Android; Windows has install.ps1 (installer.Tests.ps1)')
    def test_the_table(self):
        oks, bads, cmps = tag_table()
        # one run per shell and file: every line answered, then compared here
        lines = [f'tag_ok "{t}" && echo "ok[{t}]" || echo "no[{t}]"' for t in oks + bads]
        lines += [f'newer "{a}" "{b}" && echo "gt[{a}][{b}]"; newer "{b}" "{a}" && echo "gt[{b}][{a}]"' for a, _, b in cmps]
        code = '\n'.join(lines) + '\necho end'
        for shell in self.each_shell():
            for where, pre, env in (('releases.sh', f'. "{TOP}/tools/releases.sh"', {}),
                                    ('mu300-update', f'. "{BIN_UPDATE}"', {'MU300_LIB': '1'})):
                r = self.sh(shell, pre + '\n' + code, **env)
                out = set(r.stdout.split('\n'))
                self.assertIn('end', out, (where, r.stderr[-500:]))
                for t in oks:
                    self.assertIn(f'ok[{t}]', out, (where, t))
                for t in bads:
                    self.assertIn(f'no[{t}]', out, (where, t))
                for a, want, b in cmps:
                    got = relation(lambda x, y: f'gt[{x}][{y}]' in out, lambda x: f'ok[{x}]' in out, a, b)
                    self.assertEqual(got, want, (where, a, b))
                r = self.sh(shell, pre + '\ntag_key v2026.10.16.1; tag_key v2026.10.27; tag_key v2026.10.16.12; tag_key dev || echo none', **env)
                self.assertEqual(r.stdout.split(), ['22026101601', '12026102700', '22026101612', 'none'], where)


@unittest.skipIf(os.name == 'nt', 'make-release.sh runs on a Unix host')
class MakeReleaseTag(ShellTest):
    """tools/make-release.sh --next-tag and the tag it takes for a new release (docs/FINDINGS.md 125), with a stub gh
    ($STUBLOG/gh: its list of releases; $STUBLOG/gh.rc: its status) and a stub for the repository's own tags
    ($STUBLOG/tags, $STUBLOG/remote)."""

    def setUp(self):
        super().setUp()
        self.stub('gh', 'cat "$STUBLOG/gh" 2>/dev/null; exit "$(cat "$STUBLOG/gh.rc" 2>/dev/null || echo 0)"')
        self.stub('git', 'case "$*" in *" tag -l "*) cat "$STUBLOG/tags" 2>/dev/null ;; *ls-remote*) cat "$STUBLOG/remote" 2>/dev/null ;; '
                         '*) echo "unexpected: $*" >&2; exit 9 ;; esac')

    def run_mr(self, shell, *args, gh='', tags='', remote='', rc=0):
        (self.tmp / 'gh').write_text(gh)
        (self.tmp / 'gh.rc').write_text(str(rc))
        (self.tmp / 'tags').write_text(tags)
        (self.tmp / 'remote').write_text(remote)
        return self.script(shell, TOP / 'tools/make-release.sh', *args, MU300_TODAY='2026.10.16')

    def test_next_tag(self):
        for shell in self.each_shell():
            for gh, tags, remote, want in (
                    ('v2026.10.27\nv2026.10.26\ntest-issue-102\n', '', '', 'v2026.10.16.1'),
                    ('v2026.10.16.1\nv2026.10.27\n', '', '', 'v2026.10.16.2'),
                    # .9 then .10, not .10 then .2 (as numbers); a tag without a release counts; another day does not
                    ('v2026.10.16.9\nv2026.10.15.30\n', 'v2026.10.16.3\n', '', 'v2026.10.16.10'),
                    ('v2026.10.16.10\n', '', 'abc\trefs/tags/v2026.10.16.12\nabc\trefs/tags/v2026.10.16.12^{}\n', 'v2026.10.16.13'),
                    # not release tags: never counted
                    ('v2026.10.16.01\nv2026.10.16.0\nv2026.10.16.1x\n', '', '', 'v2026.10.16.1')):
                r = self.run_mr(shell, '--next-tag', gh=gh, tags=tags, remote=remote)
                self.assertEqual((r.returncode, r.stdout), (0, want + '\n'), (gh, tags, remote, r.stderr))
            # GitHub does not answer: no tag at all
            r = self.run_mr(shell, '--next-tag', rc=1)
            self.assertEqual((r.returncode, r.stdout), (1, ''))
            self.assertIn('no tag is guessed', r.stderr)
            r = self.run_mr(shell, '--next-tag', gh='v2026.10.16.99\n')
            self.assertEqual(r.returncode, 1)

    def test_the_tag_of_a_new_release(self):
        for shell in self.each_shell():
            r = self.run_mr(shell, '--check-tag', 'v2026.10.16.3')
            self.assertEqual((r.returncode, r.stdout), (0, 'v2026.10.16.3\n'), r.stderr)
            for tag, why in (('v2026.10.28', 'not a tag for a new release'), ('v2026.10.16', 'not a tag for a new release'),
                             ('v2026.10.16.0', 'not a release tag'), ('v2026.10.16.01', 'not a release tag'),
                             ('v2026.13.16.1', 'not a release tag'), ('v2026.10.15.1', 'not of today'),
                             ('v2026.10.17.1', 'not of today'), ('test-issue-102', 'not a tag for a new release')):
                r = self.run_mr(shell, '--check-tag', tag)
                self.assertEqual((r.returncode, r.stdout), (1, ''), tag)
                self.assertIn(why, r.stderr, tag)
            # another day only when said so; the form never
            r = self.run_mr(shell, '--check-tag', 'v2026.10.15.1', '--any-date')
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.run_mr(shell, '--check-tag', 'v2026.10.28', '--any-date')
            self.assertEqual(r.returncode, 1)
            # a build is refused before anything is looked at
            r = self.run_mr(shell, 'v2026.10.28', '--publish')
            self.assertEqual(r.returncode, 1)
            self.assertIn('not a tag for a new release', r.stderr)
            self.assertNotIn('kernel outputs', r.stderr)

    def test_the_release_workflow_names_the_release_by_it(self):
        wf = (TOP / '.github/workflows/release.yml').read_text()
        self.assertIn('sh tools/make-release.sh --next-tag', wf)
        self.assertIn('sh tools/make-release.sh --check-tag "$TAG"', wf)
        self.assertIn('sudo -E sh tools/make-release.sh "$TAG" --publish --any-date $pre', wf)
        self.assertLess(wf.index('--check-tag'), wf.index('Not yet released'))


@unittest.skipIf(os.name == 'nt', 'the stub curl is a shell script; install.ps1 has its own lookup, tested in installer.Tests.ps1')
class Resolve(ShellTest):
    """release_resolve with a stub curl: $STUBLOG/list is the answer to the list of releases, $STUBLOG/latest the
    one to releases/latest; a missing file is a failed request (curl -f)."""

    def setUp(self):
        super().setUp()
        self.stub('curl', 'for a in "$@"; do u=$a; done; echo "$u" >> "$STUBLOG/curl.log"\n'
                          'case $u in */releases\\?per_page=*) f=list ;; */releases/latest) f=latest ;; *) exit 22 ;; esac\n'
                          '[ -f "$STUBLOG/$f" ] || exit 22\ncat "$STUBLOG/$f"')

    def resolve(self, shell, listing=None, latest=None, answer='', pre=''):
        for name, value in (('list', listing), ('latest', latest)):
            p = self.tmp / name
            if value is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(value if isinstance(value, str) else json.dumps(value))
        (self.tmp / 'curl.log').write_text('')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; REPO=dikeckaan/mu300-linux; RELEASE=; '
                'die() { echo "DIE $*"; exit 1; }; '
                'ask() { printf "ASKED[%s|%s|%s] " "$2" "$3" "${4:-}"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
                f'{pre} . "$TOP/tools/releases.sh"; release_resolve; echo "release=$RELEASE offered=$BETA_OFFERED"')
        r = self.sh(shell, code, stdin=answer + '\n')
        return r.stdout, (self.tmp / 'curl.log').read_text()

    def test_no_beta_asks_nothing(self):
        for shell in self.each_shell():
            out, _ = self.resolve(shell, [FULL, OLDER, OLD_BETA])
            self.assertNotIn('ASKED', out)
            self.assertIn('release=v2026.10.23 offered=\n', out)

    def test_a_beta_is_asked_about_and_the_default_is_no(self):
        for shell in self.each_shell():
            out, _ = self.resolve(shell, [NEW_BETA, FULL])
            self.assertIn('ASKED[Install the beta v2026.10.25 instead of v2026.10.23? (yes/no)|no|MU300_ANS_BETA]', out)
            self.assertIn('A beta is out: v2026.10.25,', out)
            self.assertIn('release=v2026.10.23 offered=v2026.10.25', out)
            out, _ = self.resolve(shell, [NEW_BETA, FULL], answer='yes')
            self.assertIn('release=v2026.10.25 offered=v2026.10.25', out)
            out, _ = self.resolve(shell, [NEW_BETA, FULL], answer='no')
            self.assertIn('release=v2026.10.23', out)

    def test_a_release_given_asks_nothing_and_asks_github_nothing(self):
        for shell in self.each_shell():
            out, log = self.resolve(shell, [NEW_BETA, FULL], pre='RELEASE=v2026.10.20;')
            self.assertNotIn('ASKED', out)
            self.assertIn('release=v2026.10.20 offered=\n', out)
            self.assertEqual(log, '')
            out, log = self.resolve(shell, [NEW_BETA, FULL], pre='MU300_RELEASE_URL=http://127.0.0.1:8000;')
            self.assertNotIn('ASKED', out)
            self.assertIn('release=custom', out)
            self.assertEqual(log, '')

    def test_without_the_list_the_newest_release_and_no_question(self):
        latest = '{\n  "url": "x",\n  "tag_name": "v2026.10.23",\n  "prerelease": false\n}\n'
        for shell in self.each_shell():
            # the request failed (offline on that path, HTTP 403 of the rate limit: curl -f prints nothing)
            out, log = self.resolve(shell, None, latest)
            self.assertNotIn('ASKED', out)
            self.assertIn('release=v2026.10.23 offered=\n', out)
            self.assertEqual(len(log.splitlines()), 2)
            # the rate limit's error object, a proxy's page
            for listing in (RATE_LIMIT, '<html>captive portal</html>', ''):
                out, _ = self.resolve(shell, listing, latest)
                self.assertNotIn('ASKED', out)
                self.assertIn('release=v2026.10.23', out)

    def test_github_gone_altogether_stops_as_before(self):
        for shell in self.each_shell():
            out, _ = self.resolve(shell, None, None)
            self.assertIn('DIE cannot find the newest release of dikeckaan/mu300-linux (set MU300_RELEASE=<tag>', out)
            self.assertNotIn('release=', out)

    def test_install_sh_resolves_before_the_questions(self):
        src = (TOP / 'install.sh').read_text()
        self.assertLess(src.index('release_resolve'), src.index("say \"$(t 'What should be installed?')\""))
        self.assertLess(src.index("say \"$(t 'What should be installed?')\""), src.index('slot_release_ok "$RELEASE"'))
        self.assertNotIn('/releases/latest"', src)      # one place looks it up: tools/releases.sh


if __name__ == '__main__':
    unittest.main()
