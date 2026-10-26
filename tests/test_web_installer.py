"""The web installer (site/web-installer, site/assets/web-installer): its JavaScript tests (tests/web, run with
Node), and what can be checked about the pages without a browser - the three languages say the same things, every
sentence the code asks for exists, the pages load nothing from anywhere else, and the staged site carries the device
scripts the page pushes."""
import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

WI = TOP / 'site/assets/web-installer'
PAGES = {'en': TOP / 'site/web-installer/index.html', 'tr': TOP / 'site/tr/web-installer/index.html',
         'zh': TOP / 'site/zh/web-installer/index.html'}
NODE = shutil.which('node')


def node_major():
    if not NODE:
        return 0
    try:
        return int(subprocess.run([NODE, '--version'], capture_output=True, text=True).stdout.strip().lstrip('v').split('.')[0])
    except ValueError:
        return 0


def strings():
    """{lang: {key: text}} out of strings.js (the file is three object literals of "key": "text" pairs)"""
    src = (WI / 'strings.js').read_text(encoding='utf-8')
    out = {}
    for lang in ('en', 'tr', 'zh'):
        body = src[src.index(f'const {lang} = {{'):]
        body = body[:body.index('\n};')]
        out[lang] = dict(re.findall(r'"([a-z0-9.-]+)":\s*"((?:[^"\\]|\\.)*)"', body))
    return out


@unittest.skipIf(node_major() < 20, 'Node.js 20 or newer is needed')
class JavaScript(unittest.TestCase):
    def test_node_tests(self):
        files = sorted(str(p) for p in (TOP / 'tests/web').glob('*.test.mjs'))
        self.assertGreaterEqual(len(files), 4)
        r = subprocess.run([NODE, '--test', '--test-timeout=120000'] + files, capture_output=True, text=True, timeout=900, cwd=TOP)
        # (the reporter depends on whether Node writes to a terminal: "not ok" lines of TAP, or the ✖ of spec)
        failed = '\n'.join(l for l in r.stdout.splitlines() if l.startswith(('not ok', '✖')) or 'Error' in l)[-6000:]
        self.assertEqual(r.returncode, 0, failed + r.stderr[-2000:])
        self.assertRegex(r.stdout, r'(?m)^(ℹ|#) fail 0$')
        self.assertRegex(r.stdout, r'(?m)^(ℹ|#) pass [1-9]\d')

    def test_every_module_parses(self):
        for f in sorted(WI.rglob('*.js')):
            r = subprocess.run([NODE, '--check', str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f'{f}: {r.stderr}')


    def test_a_built_zip_with_its_computer_side_is_read_as_before(self):
        # the zips of tools/make-magisk-zips.sh, computer/ and the launchers in them: zip.js finds the manifest and the
        # installer of every one (the fake release of test_magisk_zip)
        from test_magisk_zip import fake_release
        tmp = Path(tempfile.mkdtemp(prefix='mu300-web-zips-'))
        try:
            fake_release(tmp / 'v2026.10.06')
            r = subprocess.run(['sh', str(TOP / 'tools/make-magisk-zips.sh'), str(tmp / 'v2026.10.06'), str(tmp / 'out')],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            script = ('import { inspectMagiskZip } from ' + json.dumps((WI / 'zip.js').as_uri()) + ';\n'
                      'import { readFileSync } from "node:fs";\n'
                      'for (const f of process.argv.slice(2)) console.log(JSON.stringify(await inspectMagiskZip(new Blob([readFileSync(f)]))));\n')
            (tmp / 'inspect.mjs').write_text(script)
            zips = sorted((tmp / 'out').glob('*.zip'))
            self.assertEqual(len(zips), 15)
            r = subprocess.run([NODE, str(tmp / 'inspect.mjs')] + [str(z) for z in zips], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            seen = [json.loads(l) for l in r.stdout.splitlines()]
            self.assertEqual(len(seen), 15)
            for z, info in zip(zips, seen):
                self.assertEqual(f"mu300-magisk-{info['tag']}-{info['system']}-k{info['kernel']}.zip", z.name)
                self.assertTrue(info['web'], z.name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

class Strings(unittest.TestCase):
    def setUp(self):
        self.s = strings()

    def test_three_languages_same_keys_and_placeholders(self):
        en = self.s['en']
        self.assertGreater(len(en), 300)
        for lang in ('tr', 'zh'):
            self.assertEqual(sorted(set(en) - set(self.s[lang])), [], f'missing in {lang}')
            self.assertEqual(sorted(set(self.s[lang]) - set(en)), [], f'only in {lang}')
            for k, v in en.items():
                self.assertEqual(sorted(set(re.findall(r'\{\d\}', v))), sorted(set(re.findall(r'\{\d\}', self.s[lang][k]))), f'{lang} {k}')
                self.assertEqual(v.count('['), self.s[lang][k].count('['), f'{lang} {k}: the link brackets')
                if v:
                    self.assertTrue(self.s[lang][k].strip(), f'{lang} {k} is empty')

    def test_words_to_type_and_commands_are_not_translated(self):
        for lang, table in self.s.items():
            self.assertIn('sudo mu300-next-boot android && sudo reboot', table['done.back'], lang)
            self.assertIn('su -c mu300-linux', table['done.back'], lang)
            self.assertIn('{1}', table['erase.type'], lang)

    def test_every_sentence_the_code_asks_for_exists(self):
        en = self.s['en']
        app = (WI / 'app.js').read_text()
        manager = (WI / 'manager.js').read_text()
        asked = set()
        for src in (app, manager):
            asked |= set(re.findall(r'(?<![A-Za-z.])t\("([a-z0-9-]+\.[a-z0-9.-]+)"', src)) | set(re.findall(r'[?:] "([a-z]+\.[a-z0-9.-]+)"', src))
        # the manager's confirmations (consequence()) are keys of their own
        asked |= set(re.findall(r'key: (?:[^"]*\? )?"(mgr\.confirm\.[a-z-]+)"', manager)) | set(re.findall(r'"(mgr\.confirm\.[a-z-]+)"', manager))
        # a key that ends in a dot is a family, completed at run time: checked below
        # (an error's code is asked for as "err." + code)
        known = lambda k: k in en or 'err.' + k in en or any(e.startswith('err.' + k + '.') for e in en)
        self.assertEqual(sorted(k for k in asked if not k.endswith('.') and not known(k)), [])
        fam = {}
        flow = (WI / 'flow.js').read_text()
        device = (WI / 'device.js').read_text()
        web = (TOP / 'tools/android-web.sh').read_text()
        # the choices the wizard refuses, and what blocks an installation
        validate = flow[flow.index('export function validateChoices'):flow.index('export function dataNeeded')]
        fam['verr.'] = set(re.findall(r'code: "([a-z0-9-]+)"', validate)) | {'password-differs'}
        blockers = flow[flow.index('export function deviceBlockers'):flow.index('export function validPassword')]
        fam['blocker.'] = set(re.findall(r'"([a-z-]+)"', blockers)) - {'magisk'}
        # the device script's refusals and device.js's own
        fam['err.dev.'] = set(re.findall(r'\bfail ([a-z-]+)', web)) | set(re.findall(r'DeviceError\("([a-z-]+)"', device))
        # why the device's own download failed (the fetch job's exit status), and the mirror's refusals
        failures = re.search(r'FETCH_FAILURES = \{(.*?)\}', device).group(1)
        self.assertEqual(sorted(re.findall(r'(\d+):', failures)), sorted(re.findall(r'FETCH_[A-Z_]+=(\d+)', web)))
        fam['err.dev.'] |= set(re.findall(r'"([a-z-]+)"', failures))
        mirror = (WI / 'mirror.js').read_text()
        fam['err.mirror.'] = set(re.findall(r'MirrorError\("([a-z-]+)"', mirror))
        self.assertEqual(fam['err.mirror.'], {'none', 'network', 'truncated', 'mismatch', 'bad-manifest', 'aborted'})
        fam['files.uplink.'] = {'mobile', 'wifi', 'ethernet', 'other', 'none'}
        # every failure the files step offers another way around is one that has a sentence
        for c in re.findall(r'"((?:dev|mirror)\.[a-z-]+)"', app[app.index('const SOURCE_FAILURES'):app.index('function stepReview')]):
            self.assertIn('err.' + c, en, c)
        fam['err.adb.'] = set(re.findall(r'AdbError\("([a-z-]+)"', ''.join(p.read_text() for p in (WI / 'adb').glob('*.js')))) - {'access'}
        fam['err.gh.'] = set(re.findall(r'GithubError\("([a-z-]+)"', (WI / 'github.js').read_text()))
        fam['step.'] = set(re.search(r'INSTALL_STEPS = \[(.*?)\]', flow).group(1).replace('"', '').split(', '))
        fam['ustep.'] = set(re.search(r'UNINSTALL_STEPS = \[(.*?)\]', flow).group(1).replace('"', '').split(', '))
        fam['urisk.'] = fam['ustep.']
        fam['risk.'] = {'nothing', 'systems', 'boot', 'arm', 'done'}
        fam['storage.verdict.'] = set(re.findall(r'verdict = "([a-z-]+)"', flow))
        fam['storage.seg.'] = set(re.findall(r'kind: "([a-z-]+)"', flow)) - {'gap'}
        fam['err.adb.access.'] = {'windows', 'linux', 'mac', 'android', 'other'}
        fam['lock.'] = fam['explain.lock.'] = {'unlocked', 'locked', 'unknown'}
        fam['sys.about.'] = {'ubuntu-24.04', 'ubuntu-26.04', 'openwrt', 'openwrt-luci'}
        fam['kernel.'] = {'5.4', '6.18', '7.2', '7.3'}
        fam['job.kind.'] = {'plan', 'install', 'uninstall'}
        fam['review.pw.'] = {'given', 'kept', 'image'}
        fam['wizard.step.'] = set(re.search(r'WSTEPS = \[(.*?)\]', app).group(1).replace('"', '').split(', '))
        fam['storage.sd.'] = {'yes', 'no', 'foreign', 'none', 'small'}
        # the device manager (manager.js): its tabs, the words mu300-api answers with, its jobs and its refusals
        fam['mgr.tab.'] = set(re.search(r'TABS = \[(.*?)\]', manager).group(1).replace('"', '').split(', '))
        fam['mgr.reg.'] = {'home', 'roaming', 'searching', 'denied', 'none', 'emergency'}
        fam['mgr.sec.'] = {'wpa2', 'wpa2-wpa3', 'wpa3', 'open', 'other'}
        fam['mgr.data.'] = {'up', 'down', 'off'}
        fam['mgr.usb.role.'] = {'host', 'device'}
        fam['mgr.vpn.ipv6.'] = {'0', '1', 'auto'}
        api = (TOP / 'rootfs/overlay/opt/mu300/bin/mu300-api').read_text()
        fam['mgr.job.kind.'] = set(re.findall(r'job_start ([a-z-]+) ', api)) - {'KIND'}
        fam['err.dev.api-'] = set(re.findall(r'refuse ([a-z-]+)', api)) - {'CODE'}
        fam['err.dev.'].discard('api-')     # (device.js's DeviceError("api-" + code): the family above)
        self.assertIn('bad-value', fam['err.dev.api-'])
        self.assertIn('update-apply', fam['mgr.job.kind.'])
        for prefix, names in fam.items():
            self.assertTrue(names, prefix)
            for n in names:
                self.assertTrue(prefix + n in en, prefix + n)
        # the steps the device scripts name are the ones the page lists
        installer = (TOP / 'android/magisk/installer/mu300-install.sh').read_text()
        self.assertEqual(set(re.search(r"\nSTEPS='(.*?)'", installer).group(1).split()), fam['step.'])
        self.assertEqual(set(re.search(r"\nUNSTEPS='(.*?)'", web).group(1).split()), fam['ustep.'])


class Pages(unittest.TestCase):
    THEME = 'try{var t=localStorage.getItem("mu300-theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}'

    def test_three_pages_with_the_same_structure(self):
        ids = None
        for lang, p in PAGES.items():
            s = p.read_text(encoding='utf-8')
            self.assertIn(f'<html lang="{"zh-Hans" if lang == "zh" else lang}">', s)
            self.assertIn('id="wi-app" hidden', s)
            self.assertIn('<noscript>', s)
            # what it was tested on and what not, and the other ways named; no "beta" left anywhere on the page
            self.assertIn('class="callout wi-tested"', s)
            self.assertIn('2026-10-13', s)
            self.assertIn('v2026.10.25', s)
            self.assertNotRegex(s, r'(?i)\bbeta\b|experimental|deneysel|测试版|实验性')
            self.assertIn('../install/', s)
            self.assertIn('web-installer/app.js', s)
            self.assertIn('aria-current="page"', s)
            self.assertEqual(s.count('https://t.me/mu300project'), 1, f'{lang}: the Telegram line of the footer')
            found = re.findall(r'<h2 id="([a-z-]+)"', s)
            ids = ids or found
            self.assertEqual(found, ids, lang)
            for l2 in ('', 'tr/', 'zh/'):
                self.assertIn(f'href="https://kaandikec.com/mu300-linux/{l2}web-installer/"', s)

    def test_the_page_loads_nothing_from_elsewhere_and_says_so_in_a_policy(self):
        want = base64.b64encode(hashlib.sha256(self.THEME.encode()).digest()).decode()
        for lang, p in PAGES.items():
            s = p.read_text(encoding='utf-8')
            csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', s).group(1)
            self.assertIn("default-src 'none'", csp)
            self.assertIn(f"script-src 'self' 'sha256-{want}'", csp)
            self.assertIn("connect-src 'self' https://api.github.com", csp)
            self.assertNotIn('unsafe', csp)
            self.assertIn(f'<script>{self.THEME}</script>', s)
            # every script, stylesheet and image is this site's own (relative), no web fonts
            for tag, attr in (('script', 'src'), ('img', 'src')):
                for m in re.findall(rf'<{tag}\b[^>]*\b{attr}="([^"]+)"', s):
                    self.assertFalse(m.startswith(('http:', 'https:', '//')), m)
            for m in re.findall(r'<link\b[^>]*rel="(?:stylesheet|preconnect|preload)"[^>]*href="([^"]+)"', s):
                self.assertFalse(m.startswith(('http:', 'https:', '//')), m)
            self.assertNotIn('fonts.googleapis.com', s)
            self.assertNotIn(' style="', s.split('<main')[1].split('</main>')[0])   # the policy forbids inline styles

    def test_the_code_talks_to_github_and_this_site_only_and_never_builds_markup(self):
        hosts = set()
        for f in sorted(WI.rglob('*.js')):
            src = f.read_text(encoding='utf-8')
            for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'console.',
                              'XMLHttpRequest', 'WebSocket', 'sendBeacon', 'localStorage', 'sessionStorage', 'document.cookie'):
                self.assertNotIn(forbidden, src, f'{f.name}: {forbidden}')
            hosts |= set(re.findall(r'https://([a-z0-9.-]+)/', src))
        # raw.githubusercontent.com: where a mirror of the releases may be (config.js names it, or names none)
        self.assertEqual(hosts - {'api.github.com', 'github.com', 't.me', 'raw.githubusercontent.com'}, set(), hosts)
        # fetch() is called in three places only: the release list, this site's own device scripts, and the mirror
        fetches = [f.name for f in sorted(WI.rglob('*.js')) for _ in re.findall(r'\bfetch(?:Impl)?\(', f.read_text())]
        self.assertEqual(sorted(set(fetches)), ['app.js', 'github.js', 'mirror.js'])
        self.assertEqual(len(re.findall(r'\bfetch\(', (WI / 'app.js').read_text())), 1, 'app.js fetches the device scripts and nothing else')
        self.assertNotIn('getprop ro.serialno', ''.join(f.read_text() for f in WI.rglob('*.js')))

    def test_the_mirror_is_off_or_allowed_by_the_policy_of_all_three_pages(self):
        # config.js names where the browser may download a release itself. The pages' policy must allow exactly
        # that: no mirror, no extra host; a mirror, raw.githubusercontent.com and nothing wider.
        base = re.search(r'^export const MIRROR_BASE = "([^"]*)";$', (WI / 'config.js').read_text(), re.M).group(1)
        for lang, p in PAGES.items():
            csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', p.read_text(encoding='utf-8')).group(1)
            connect = re.search(r"connect-src ([^;]+)", csp).group(1).split()
            if base:
                self.assertRegex(base, r'^https://raw\.githubusercontent\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/$')
                self.assertEqual(connect, ["'self'", 'https://api.github.com', 'https://raw.githubusercontent.com'], lang)
            else:
                self.assertEqual(connect, ["'self'", 'https://api.github.com'], lang)

    def test_the_release_workflow_publishes_the_mirror_only_when_it_is_told_where(self):
        y = (TOP / '.github/workflows/magisk.yml').read_text()
        step = y[y.index('- name: Mirror for the web installer'):]
        self.assertIn('MIRROR_REPO: ${{ vars.MIRROR_REPO }}', step)
        # a deploy key of the mirror repository alone: no token, no API call with one, and the key never printed
        self.assertIn('MIRROR_DEPLOY_KEY: ${{ secrets.MIRROR_DEPLOY_KEY }}', step)
        self.assertNotIn('MIRROR_TOKEN', y)
        self.assertNotIn('x-access-token', step)
        self.assertIn('-o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$hosts', step)
        self.assertIn("gh api meta --jq '.ssh_keys[]'", step)          # GitHub's published host keys
        self.assertNotIn('ssh-keyscan', step)
        self.assertIn('umask 077', step)
        self.assertIn('chmod 600 "$key"', step)
        self.assertIn("trap 'rm -f \"$key\"' EXIT", step)
        used = [l.strip() for l in step.splitlines() if '$MIRROR_DEPLOY_KEY' in l]
        self.assertEqual(len(used), 2, used)                          # asked whether it is set; written to its file
        self.assertTrue(used[0].startswith('[ -n "$MIRROR_REPO" ] && [ -n "$MIRROR_DEPLOY_KEY" ] ||'), used)
        self.assertEqual(used[1], """( umask 077; printf '%s\\n' "$MIRROR_DEPLOY_KEY" > "$key" )""")
        self.assertNotIn('set -x', step)
        self.assertIn('git ls-remote --heads "$remote"', step)
        self.assertIn('git push -q "$remote" --delete "$b"', step)
        self.assertIn('no mirror"; exit 0; }', step)                # neither set: nothing happens, the job is green
        self.assertNotIn('\n        if:', step)                     # secrets are not available in a step's "if"
        self.assertIn('make-release-mirror.sh', step)
        self.assertLess(y.index('- name: Attach to the release'), y.index('- name: Mirror for the web installer'))

    def test_the_mirror_tool_cuts_a_release_into_parts_the_page_puts_together(self):
        if node_major() < 20:
            self.skipTest('Node.js 20 or newer is needed')
        tmp = Path(tempfile.mkdtemp(prefix='mu300-mirror-'))
        try:
            import os
            tag = 'v2026.11.01'
            (tmp / 'zips').mkdir()
            names = [f'mu300-magisk-{tag}-openwrt-k6.18.zip', f'mu300-magisk-{tag}-ubuntu-24.04-k7.2.zip']
            for n, size in zip(names, (300000, 2 * 1048576 + 17)):
                (tmp / 'zips' / n).write_bytes(os.urandom(size))
            (tmp / 'zips' / 'mu300-magisk-v2026.10.01-openwrt-k6.18.zip').write_bytes(b'another release')
            tool = TOP / 'tools/make-release-mirror.sh'
            r = subprocess.run(['sh', str(tool), str(tmp / 'zips'), str(tmp / 'out' / tag), tag], capture_output=True, text=True,
                               env={**os.environ, 'MU300_MIRROR_PART_MIB': '1'})
            self.assertEqual(r.returncode, 0, r.stderr)
            out = tmp / 'out' / tag
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ['manifest.json', names[0] + '.000', names[1] + '.000', names[1] + '.001', names[1] + '.002'])
            self.assertTrue(all(p.stat().st_size <= 1048576 for p in out.iterdir()))
            # the page's own code reads that mirror from a server that stands for raw.githubusercontent.com
            script = tmp / 'read.mjs'
            script.write_text(f"""
import fs from "node:fs";
import crypto from "node:crypto";
import {{ loadMirror, downloadZip }} from {json.dumps(str(WI / 'mirror.js'))};
const BASE = "https://raw.githubusercontent.com/someone/mirror/";
const fetchImpl = async (url) => {{ const f = {json.dumps(str(tmp / 'out'))} + "/" + url.slice(BASE.length); return fs.existsSync(f) ? new Response(fs.readFileSync(f)) : new Response("", {{ status: 404 }}); }};
const m = await loadMirror(BASE, {json.dumps(tag)}, fetchImpl);
for (const name of {json.dumps(names)}) {{
  const src = fs.readFileSync({json.dumps(str(tmp / 'zips'))} + "/" + name);
  const asset = {{ name, size: src.length, sha256: crypto.createHash("sha256").update(src).digest("hex") }};
  const blob = await downloadZip(BASE, m, asset, {{ fetchImpl }});
  if (Buffer.compare(Buffer.from(await blob.arrayBuffer()), src) !== 0) throw new Error("not the file: " + name);
}}
console.log("MIRROR-OK " + Object.keys(m.files).length);
""")
            r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=120)
            self.assertIn('MIRROR-OK 2', r.stdout, r.stderr)
            # a tag that is not one, parts larger than a repository may hold, a directory without that release
            for args, env in ((['zips', 'o2', 'main'], {}), (['zips', 'o2', '../v1'], {}), (['zips', 'o2', tag], {'MU300_MIRROR_PART_MIB': '90'}),
                              (['zips', 'o2', 'v2030.01.01'], {})):
                r = subprocess.run(['sh', str(tool)] + args, capture_output=True, text=True, cwd=tmp, env={**os.environ, **env})
                self.assertNotEqual(r.returncode, 0, args)
                self.assertFalse((tmp / 'o2' / 'manifest.json').exists(), args)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_locked_bootloader_is_a_question_in_the_warning_colour_and_stops_nothing(self):
        # with a patched TrustOS a locked device starts Linux: "locked" is never an error, a blocker or a refusal
        s = strings()
        for lang, word in (('en', 'Did you patch TrustOS?'), ('tr', "TrustOS'u yamaladınız mı?"), ('zh', 'TrustOS')):
            self.assertIn(word, s[lang]['lock.locked.title'], lang)
            self.assertIn('TrustOS', s[lang]['lock.locked.question'], lang)
            self.assertIn('Android', s[lang]['lock.locked.question'], lang)
            self.assertIn('TrustOS', s[lang]['explain.lock.locked'], lang)
        app = (WI / 'app.js').read_text()
        self.assertIn('lk === "unlocked" ? "ok" : lk === "locked" ? "warn" : ""', app)
        self.assertNotIn('"locked" ? "bad"', app)
        self.assertEqual(app.count('box.append(lockedNote())') + app.count('p.append(lockedNote())'), 2)   # the device panel, the plan
        self.assertIn('.wi-chip.warn', (WI / 'web-installer.css').read_text())
        flow = (WI / 'flow.js').read_text()
        for a, b in (('export function deviceBlockers', 'export function validPassword'), ('export function validateChoices', 'export function dataNeeded')):
            self.assertNotRegex(flow[flow.index(a):flow.index(b)], r'locked|bootloader|\.lock\b')
        # the page names the fact and links to no guide for unlocking or patching
        for lang in s:
            for k in ('lock.locked.title', 'lock.locked.question', 'explain.lock.locked'):
                self.assertNotIn('[', s[lang][k], k)

    def test_linked_from_the_navigation_the_install_page_and_the_sitemap(self):
        for p in sorted((TOP / 'site').rglob('index.html')):
            self.assertRegex(p.read_text(encoding='utf-8'), r'<a href="[^"]*web-installer/"', str(p))
        for pre in ('', 'tr/', 'zh/'):
            self.assertIn('<a href="../web-installer/">', (TOP / 'site' / pre / 'install/index.html').read_text(encoding='utf-8'))
        self.assertEqual((TOP / 'site/sitemap.xml').read_text().count('web-installer/'), 5)

    def test_the_staged_site_has_the_device_scripts_with_their_checksums(self):
        out = Path(tempfile.mkdtemp(prefix='mu300-site-'))
        try:
            r = subprocess.run(['sh', str(TOP / 'site/tools/stage.sh'), str(out)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            dev = out / 'assets/web-installer/device'
            names = re.search(r'DEVICE_FILES = \[(.*?)\]', (WI / 'device.js').read_text()).group(1).replace('"', '').split(', ')
            sums = dict(reversed(l.split()) for l in (dev / 'SHA256SUMS').read_text().splitlines())
            self.assertEqual(sorted(sums), sorted(names))
            for n in names:
                self.assertEqual(hashlib.sha256((dev / n).read_bytes()).hexdigest(), sums[n], n)
            self.assertEqual((dev / 'android-web.sh').read_bytes(), (TOP / 'tools/android-web.sh').read_bytes())
            self.assertFalse((out / 'tools').exists())
            self.assertTrue((out / 'web-installer/index.html').is_file())
            self.assertTrue((out / 'index.html').is_file())
            # staging into the site itself is refused
            r = subprocess.run(['sh', str(TOP / 'site/tools/stage.sh'), str(TOP / 'site/x')], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_the_pages_workflow_stages_with_that_script(self):
        y = (TOP / '.github/workflows/pages.yml').read_text()
        self.assertIn('sh site/tools/stage.sh _site', y)
        for path in ("'tools/android-web.sh'", "'tools/storage.sh'", "'android/magisk/mu300-linux-switch/switch.sh'"):
            self.assertIn(path, y)


if __name__ == '__main__':
    unittest.main()
