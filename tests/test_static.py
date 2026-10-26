"""Checks over every script without running it: syntax under each shell that runs it, executable bits, and rules
that past bugs taught (see each test)."""
import json
import os
import gzip
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, shells

OPENWRT = TOP / 'openwrt' / 'overlay'
# the PowerShell the installers are made of: the rules of Windows PowerShell 5.1 hold for every file of it
POWERSHELL = ('install.ps1', 'uninstall.ps1', 'tools/answers.ps1', 'tools/gui-launch.ps1')
LUCI_OVERLAY = TOP / 'openwrt' / 'luci-overlay'


def shebang(p):
    try:
        with open(p, 'rb') as f:
            first = f.readline()
    except OSError:
        return ''
    return first.decode(errors='replace').strip() if first.startswith(b'#!') else ''


def shell_scripts():
    """(path, 'sh'|'bash') of every shell script the project ships or runs."""
    cands = list(BIN.iterdir()) + list((TOP / 'tools').glob('*.sh')) + [
        TOP / 'install.sh', TOP / 'uninstall.sh', TOP / 'boot' / 'init', TOP / 'rootfs' / 'assemble.sh',
        TOP / 'kernel' / 'build-all.sh', TOP / 'tools' / 'i18n.sh', TOP / 'tools' / 'self-update.sh',
        TOP / 'tools' / 'linux-mode.sh', TOP / 'tools' / 'storage.sh', TOP / 'android-vendor' / 'ueventd-perms.sh']
    cands += list((TOP / 'android' / 'magisk' / 'installer').glob('*.sh'))
    cands += [TOP / 'android' / 'magisk' / 'installer' / 'update-binary']
    cands += list((TOP / 'android' / 'magisk' / 'mu300-linux-switch').glob('*.sh'))
    cands += [p for p in OPENWRT.rglob('*') if p.is_file()]
    cands += [p for p in LUCI_OVERLAY.rglob('*') if p.is_file()]
    out = []
    for p in sorted(set(cands)):
        if not p.is_file():
            continue
        sb = shebang(p)
        if 'bash' in sb:
            out.append((p, 'bash'))
        elif sb.endswith('sh') or 'rc.common' in sb or (not sb and p.suffix == '.sh'):
            out.append((p, 'sh'))
    return out


class Syntax(unittest.TestCase):
    def test_every_script_parses(self):
        sh_shells = [s for s in shells() if s[0] != 'bash'] or shells()
        scripts = shell_scripts()
        self.assertGreater(len(scripts), 40)
        for p, kind in scripts:
            if kind == 'bash' and not shutil.which('bash'):
                continue
            for s in ([['bash']] if kind == 'bash' else sh_shells):
                with self.subTest(script=str(p.relative_to(TOP)), shell=' '.join(s)):
                    r = subprocess.run(s + ['-n', str(p)], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr)

    def test_device_programs_are_executable(self):
        for p in BIN.iterdir():
            if p.is_file() and shebang(p):
                with self.subTest(p=p.name):
                    self.assertTrue(p.stat().st_mode & 0o111, 'not executable')


class Rules(unittest.TestCase):
    def test_power_page_is_wired(self):
        menu = json.loads((TOP / 'openwrt/luci-app-mu300/root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
        self.assertEqual(menu['admin/system/power']['action']['path'], 'mu300/power')
        common = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("method: 'power_get'", common)
        self.assertIn("method: 'power_set', params: [ 'op', 'key', 'value' ]", common)
        view = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/power.js').read_text()
        for s in ('WIFI_IDLE', 'RADIO_IDLE', 'LEDS_IDLE', 'CPU', 'SAVER_BELOW', 'CHARGE_TO', 'callPowerSet'):
            self.assertIn(s, view)
        acl = json.loads((TOP / 'openwrt/luci-app-mu300/root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())
        self.assertIn('power_get', acl['luci-app-mu300']['read']['ubus']['mu300dash'])
        self.assertIn('power_set', acl['luci-app-mu300']['write']['ubus']['mu300dash'])

    def test_power_profiles_are_wired_in(self):
        # the daemon runs on both systems, the keys wake it, the Ubuntu units skip the radios in a charging boot,
        # the command is on PATH, OpenWrt's own power-key handler (a tap = poweroff) is neutralised, and power.conf is kept
        self.assertIn('mu300-power', (TOP / 'rootfs/overlay/opt/mu300/lib/path-commands').read_text().split())
        buttons = (BIN / 'mu300-buttons').read_text()
        self.assertIn('mu300-power wake', buttons)
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-power.service').read_text()
        self.assertIn('ExecStart=/opt/mu300/bin/mu300-power daemon', unit)
        self.assertIn('After=mu300-hotspot.service mu300-mobile-data.service', unit)
        # R10: stopped while idle, the TERM trap runs the whole wake (up to ~120 s for the radio lock)
        self.assertIn('TimeoutStopSec=150', unit)
        self.assertIn('mu300-power.service:multi-user.target', (TOP / 'rootfs/assemble.sh').read_text())
        init = (TOP / 'openwrt/overlay/etc/init.d/mu300-power').read_text()
        self.assertIn('procd_set_param command /opt/mu300/bin/mu300-power daemon', init)
        self.assertIn('procd_set_param term_timeout 150', init)
        self.assertRegex(init, r'START=9[6-9]')
        for u in ('mu300-hotspot.service', 'mu300-mobile-data.service'):
            self.assertIn('ConditionPathExists=!/run/mu300/charging-boot', (TOP / 'rootfs/overlay/etc/systemd/system' / u).read_text())
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('for b in power wps rfkill', build)
        self.assertIn('mkdir -p $R/etc/rc.button', build)
        self.assertIn('rc.button/$b', build)
        self.assertLess(build.index('cp -a /in/overlay/. $R/'), build.index('for b in power wps rfkill'))
        self.assertIn('mu300-power', build.split('for s in mu300-accounts')[1].split('; do')[0])
        # etc/mu300 is kept whole on both systems, and power.conf lives in it
        update = (TOP / 'rootfs/overlay/opt/mu300/bin/mu300-update').read_text()
        self.assertRegex(update, r'ubuntu\) echo "etc/mu300 ')
        self.assertRegex(update, r'openwrt\) echo "etc/config etc/mu300 ')
        self.assertIn('/etc/mu300/power.conf', (BIN / 'mu300-power').read_text())

    def test_customize_leaves_magisks_shell_alone(self):
        # Magisk sources customize.sh: errexit or nounset there would end Magisk's own installer before its cleanup
        c = (TOP / 'android' / 'magisk' / 'installer' / 'customize.sh').read_text()
        code = '\n'.join(l for l in c.splitlines() if not l.lstrip().startswith('#'))
        self.assertNotRegex(code, r'\bset\s+-[a-z]*[eu]')
        self.assertNotRegex(code, r'(^|\s)exit\b')
        self.assertIn('SKIPUNZIP=1', code)

    def test_installer_never_writes_androids_boot_partition(self):
        s = (TOP / 'android' / 'magisk' / 'installer' / 'mu300-install.sh').read_text()
        self.assertNotRegex(s, r'of="?\$BOOT_ANDROID')
        self.assertNotRegex(s, r'write_boot "?\$BOOT_ANDROID')

    def test_powershell_device_commands_have_no_double_quotes(self):
        # Windows PowerShell 5.1 drops the double quotes inside an argument to a native program: `tr -d "\000"`
        # reached the device as tr -d \000 ("delete the character 0"), and every empty region was "not empty".
        # A command for the device (SuDo, adb shell) may therefore contain no `" and no "".
        for name in POWERSHELL:
            for n, line in enumerate((TOP / name).read_text().splitlines(), 1):
                code = line.split('#', 1)[0] if not line.lstrip().startswith('#') else ''
                if re.search(r'\bSuDo(ToFile)?\s+"|adb shell\s+"', code) and ('`"' in code or '""' in code):
                    self.fail(f'{name}:{n}: double quote inside a device command: {line.strip()}')

    def test_init_restores_androids_slot(self):
        # every restore goes through restore_android, so Linux on slot a returns to Android on b
        init = (TOP / 'boot' / 'init').read_text()
        self.assertNotIn('restore_slot_a', init)
        self.assertNotIn('slot_suffix=_b/androidboot.slot_suffix=_a', init)
        self.assertIn('sleep 300', init)

    def test_powershell_scripts_are_ascii(self):
        # Windows PowerShell 5.1 reads a file without a BOM as ANSI: non-ASCII text in the script is garbled
        for name in POWERSHELL + ('tests/installer.Tests.ps1', 'tests/answers.Tests.ps1'):
            data = (TOP / name).read_bytes()
            bad = [i for i, b in enumerate(data) if b > 127]
            self.assertFalse(bad, f'{name}: non-ASCII byte at offset {bad[:1]}')

    def test_windows_pushes_text_with_lf(self):
        # a CRLF clone pushed its scripts as they were and Android's sh ran none of them (issue #7): the Windows
        # installers send text files through PushUnix, never a plain adb push
        for name in POWERSHELL:
            for n, line in enumerate((TOP / name).read_text().splitlines(), 1):
                if 'adb push' in line:
                    self.assertNotRegex(line, r'\.(sh|prop)\b|mu300-linux"|\$f"', f'{name}:{n}')
        self.assertIn('eol=lf', (TOP / '.gitattributes').read_text())

    def test_single_quoted_scripts_have_no_apostrophes(self):
        # A script handed to `sh -c '...'` (docker run) ends at its first apostrophe: "the U30 Air's charger" in a
        # comment there broke OpenWrt's build at release time, while every parser still saw balanced quotes.
        for name in ('openwrt/build-rootfs.sh', 'tools/make-release.sh'):
            lines = (TOP / name).read_text().splitlines()
            for n, line in enumerate(lines):
                if not line.rstrip().endswith("-c '"):
                    continue
                for m in range(n + 1, len(lines)):
                    if "'" in lines[m]:
                        self.assertTrue(lines[m].rstrip().endswith("'") and lines[m].count("'") == 1,
                                        f'{name}:{m + 1}: an apostrophe inside the script of line {n + 1}')
                        break

    def test_ubuntu_has_the_panels_lock_and_forwarding_programs(self):
        # Ubuntu runs openwrt-luci's lock and forwarding adapters as they are (FINDINGS 189): the same bytes, executable
        for f in ('usr/libexec/unisoc-modem/lock', 'usr/libexec/unisoc-modem/at', 'usr/libexec/unisoc-modem/boot-replay',
                  'usr/libexec/unisoc-modem/forward', 'usr/share/unisoc-modem/lib.sh'):
            with self.subTest(file=f):
                ours, luci = TOP / 'rootfs/overlay' / f, TOP / 'openwrt/luci-app-mu300/root' / f
                self.assertEqual(ours.read_bytes(), luci.read_bytes(), f'{f}: copy openwrt/luci-app-mu300/root/{f} over it')
                if f.startswith('usr/libexec'):
                    self.assertTrue(os.access(ours, os.X_OK), f)
        units = (TOP / 'rootfs/assemble.sh').read_text()
        for u in ('mu300-lock-replay.service:multi-user.target', 'mu300-sms-forward.service:multi-user.target'):
            self.assertIn(u, units)

    def test_every_tool_that_mounts_the_filesystem_knows_the_card(self):
        for f in ('uninstall.sh', 'uninstall.ps1', 'tools/reset-password.sh'):
            self.assertIn('mu300sd', (TOP / f).read_text(), f)

    def test_every_system_list_has_openwrt_luci(self):
        # every place that names the systems knows the third one, by its name or by the OpenWrt kind pattern
        files = ('boot/init', 'rootfs/overlay/opt/mu300/bin/mu300-update', 'rootfs/overlay/opt/mu300/bin/mu300-os',
                 'tools/android-install.sh', 'tools/reset-password.sh', 'tools/vendor-overlay.py', 'install.sh',
                 'install.ps1', 'tools/make-release.sh', 'rootfs/overlay/opt/mu300/bin/mu300-extra',
                 'android/magisk/installer/mu300-install.sh')
        for f in files:
            with self.subTest(file=f):
                text = (TOP / f).read_text()
                self.assertTrue('openwrt-luci' in text or 'openwrt-*' in text or 'openwrt|openwrt-' in text, f)

    def test_openwrt_luci_build_wiring(self):
        # MU300_SYSTEM=openwrt-luci: the same build as plain OpenWrt plus the panel, its catalogs compiled from po/
        # and Aurora pinned by hash (D3, D5); ImmortalWrt with the panel was never tested by anyone, so refused
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        for s in ('MU300_SYSTEM', '05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e', 'po2lmo.py',
                  'luci-overlay', 'packages.txt', 'luci-i18n-$c-$l', 'for l in tr zh-cn', 'base.$l.lmo'):
            self.assertIn(s, text)
        arm = re.search(r'^\s*openwrt-luci\)(.*?);;', text, re.M | re.S)
        self.assertIsNotNone(arm, 'no openwrt-luci arm in the MU300_SYSTEM case')
        self.assertRegex(arm.group(1), r'"\$FLAVOUR" = openwrt \]', 'openwrt-luci does not refuse immortalwrt')
        self.assertIn('mu300-$SYSTEM-$VER-rootfs.tar.gz', text)
        # ImmortalWrt keeps the name it had; the Aurora hash is pinned in the script, only the file may be overridden
        self.assertIn('mu300-immortalwrt-$VER-rootfs.tar.gz', text)
        self.assertNotIn('MU300_LUCI_THEME_SHA256', text)
        self.assertRegex(text, r'(?m)^\s*THEME_SHA=05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e$')
        mk = (TOP / 'openwrt' / 'luci-app-mu300' / 'Makefile').read_text()
        self.assertRegex(mk, r'set -e; \$\(foreach', 'a failing catalog must fail the compile')
        f = LUCI_OVERLAY / 'etc' / 'uci-defaults' / '91-mu300-luci'
        self.assertTrue(f.is_file() and f.stat().st_mode & 0o111, f'{f} missing or not executable')
        self.assertIn('/luci-static/aurora', f.read_text())
        self.assertIn(f, [p for p, _ in shell_scripts()])   # so test_every_script_parses parses it

    def test_uninstallers_have_no_per_system_list(self):
        # they remove the whole Linux filesystem; a per-system case arm added later would forget the third name
        for f in ('uninstall.sh', 'uninstall.ps1'):
            text = (TOP / f).read_text()
            self.assertNotRegex(text, r'(^|\s)(ubuntu|openwrt)\)', f)

    def test_the_commands_on_path_are_the_same_everywhere(self):
        # the Ubuntu image, the OpenWrt image and the boot-time links (mu300-extra link, for systems installed before a
        # command had one) link the same commands. They used to be three copies of the list: the fixups' lagged behind
        # (mu300-led, mu300-device were "command not found" on an installed U30 Air), and all three left mu300-ussd out.
        # Now there is one file, and every place reads it.
        lst = (TOP / 'rootfs/overlay/opt/mu300/lib/path-commands').read_text().split()
        self.assertIn('$R/opt/mu300/lib/path-commands', (TOP / 'rootfs/assemble.sh').read_text())
        self.assertIn('/in/opt-mu300/lib/path-commands', (TOP / 'openwrt/build-rootfs.sh').read_text())
        self.assertIn('/lib/path-commands', (BIN / 'mu300-extra').read_text())
        self.assertIn('mu300-extra link', (BIN / 'rootfs-fixups').read_text())
        self.assertIn('mu300-extra link', (OPENWRT / 'etc/init.d/mu300-post').read_text())
        for c in ('mu300-device', 'mu300-sim', 'mu300-led', 'mobile-data', 'mu300-update', 'mu300-ussd', 'sms', 'mu300-extra'):
            self.assertIn(c, lst)
        self.assertEqual(len(lst), len(set(lst)))
        for c in lst:
            self.assertTrue((BIN / c).is_file(), c)

    def test_reply_deadlines_are_at_least_t_whole_seconds(self):
        # date +%s counts whole seconds: "now + T" ends between T - 1 and T seconds away, so a command written late in
        # a second had almost none of its budget (mu300-atd lost replies with T=1). Every file-clock deadline is now + T + 1.
        for name, pat in (('mu300-at', r'\$\(date \+%s\) \+ T \+ 1'),
                          ('mu300-ussd', r'\$\(date \+%s\) \+ T \+ 1'),
                          ('mu300-atd', r'\$\(now\) \+ \$1 \+ 1')):
            src = (BIN / name).read_text()
            self.assertRegex(src, pat, name)
            self.assertNotRegex(src, r'date \+%s\) \+ T \)', name)

    def test_images_carry_no_vpn(self):
        # the VPN is the module of dikeckaan/mu300-linux-vpn: no mu300-vpn, no drivers, no service, no engines in
        # any image or in this repository, and no release of this repository builds or publishes it
        for f in ('rootfs/overlay/opt/mu300/bin/mu300-vpn', 'rootfs/overlay/opt/mu300/lib/vpn',
                  'rootfs/overlay/etc/systemd/system/mu300-vpn.service', 'openwrt/overlay/etc/init.d/mu300-vpn',
                  'rootfs/overlay/etc/mu300/vpn.conf.example', 'tools/fetch-xray.sh', 'tools/fetch-sing-box.sh',
                  'tools/fetch-mihomo.sh'):
            self.assertFalse((TOP / f).exists(), f)
        self.assertNotIn('mu300-vpn', (TOP / 'rootfs/overlay/opt/mu300/lib/path-commands').read_text().split())
        for f in ('rootfs/assemble.sh', 'rootfs/Dockerfile', 'openwrt/build-rootfs.sh', 'arch/build-rootfs.sh'):
            src = (TOP / f).read_text()
            for e in ('xray', 'sing-box', 'hev-socks5-tunnel', 'mihomo', 'mu300-vpn'):
                self.assertNotRegex(src, rf'opt/mu300/bin/{e}\b', (f, e))
            self.assertNotIn('vpn.conf.example', src, f)
            self.assertNotIn('lib/vpn', src, f)
        self.assertNotRegex((TOP / 'arch/build-rootfs.sh').read_text(), r'for c in [^;]*\bmu300-vpn\b')
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertNotRegex(rel, r'make-extra\.sh" vpn')
        self.assertNotIn('mu300-extra-vpn', rel)
        self.assertNotIn('fetch-', rel)
        self.assertRegex(rel, r'(?m)^for x in lang; do')
        self.assertIn('https://github.com/dikeckaan/mu300-linux-vpn', rel)
        # the audit refuses an image that still has any of it
        self.assertIn('opt/mu300/bin/(xray|sing-box|hev-socks5-tunnel|mihomo|mu300-vpn)', rel)
        for pat in ('^opt/mu300/lib/vpn(/|$)', 'etc/systemd/system/mu300-vpn', 'etc/init\\.d/mu300-vpn',
                    'etc/mu300/vpn\\.conf\\.example'):
            self.assertIn(pat, rel)
        extra = (TOP / 'tools/make-extra.sh').read_text()
        self.assertNotRegex(extra, r'(?m)^    vpn\)')
        self.assertNotRegex(extra, r'(?m)^    vpn-mihomo\)')
        self.assertNotIn('fetch-', extra)

    def test_the_release_source_is_wired(self):
        # a published image must not carry a configured release source, and both image trees must have the example
        # (openwrt/overlay is the only overlay an OpenWrt image gets, so the Ubuntu one alone would miss them)
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn("^etc/mu300/(hotspot|vpn|toolkit|mobile-data)\\.conf$", rel)
        self.assertRegex(rel, r"\^etc/mu300/update-source\$")
        self.assertNotIn('update-source.example', rel)
        for f in ('rootfs/overlay/etc/mu300/update-source.example', 'openwrt/overlay/etc/mu300/update-source.example'):
            text = (TOP / f).read_text()
            for s in ('MU300_DOWNLOAD_URL=http://mirror.lan/', 'MU300_API_URL=https://', '403', 'MU300_MIRROR_FALLBACK=0'):
                self.assertIn(s, text, f)
        # the panel's field, the op behind it and the status field that feeds it name the same thing
        js = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/update.js').read_text()
        self.assertIn("op: 'source'", js)
        self.assertIn("confirm: 'source'", js)
        self.assertIn('st.source || {}', js)
        common = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("'flags', 'confirm', 'url'", common)
        dash = (TOP / 'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash').read_text()
        self.assertIn('|source|dismiss)', dash)
        self.assertIn('allowed "$url"', dash)
        adapter = (TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/update').read_text()
        self.assertIn('"$CMD" source', adapter)
        # the fallback tells an unreachable mirror from an answering one by curl's exit codes, so both images carry
        # curl (the OpenWrt one had only the wget family, where every failure looks the same)
        self.assertRegex((TOP / 'openwrt/build-rootfs.sh').read_text(), r'apk add[^>]*\bcurl\b')
        updater = (TOP / 'rootfs/overlay/opt/mu300/bin/mu300-update').read_text()
        self.assertIn('source) shift; source_cmd "$@"', updater)
        self.assertIn('"source":%s}', updater)

    def test_the_vpn_module_wiring(self):
        # mu300-update knows the module (fixed asset names, the repository's latest release, MU300_VPN_URL), and
        # mu300-extra lists vpn and its engine extras from it (module v2: the core, and the bundle as the fallback)
        up = (BIN / 'mu300-update').read_text()
        self.assertRegex(up, r'(?m)^EXTRAS="vpn lang vpn-mihomo panel vpn-xray vpn-singbox vpn-dae"$')
        self.assertRegex(up, r'(?m)^VPN_ENGINE_EXTRAS="vpn-xray vpn-singbox vpn-mihomo vpn-dae"$')
        self.assertRegex(up, r'(?m)^VPN_REPO=dikeckaan/mu300-linux-vpn$')
        self.assertIn('VPN_URL=${MU300_VPN_URL:-https://github.com/$VPN_REPO/releases/latest/download}', up)
        self.assertIn('vpn) echo mu300-linux-vpn-core.tar.gz ;; vpn-bundle) echo mu300-linux-vpn.tar.gz ;;', up)
        self.assertIn('vpn-mihomo|vpn-xray|vpn-singbox|vpn-dae) echo "mu300-linux-$1.tar.gz" ;;', up)
        self.assertNotIn('extra_adopt', up)
        body = up[up.index('\napply() {'):up.index('\nclean_broken() {')]
        # the module is had before anything changes, and put in place before the systems are switched
        self.assertLess(body.index('modules_fetch'), body.index('all files are here and verified'))
        self.assertLess(body.index('modules_install'), body.index('apply_one'))
        self.assertLess(body.index('apply_one'), body.index('modules_link'))
        ex = (BIN / 'mu300-extra').read_text()
        self.assertIn('vpn-mihomo', ex.split('BIN=')[0])
        self.assertIn('--from FILE|DIR', ex.split('BIN=')[0])
        # the installers take the module from its repository when the release has no vpn extra of its own
        for f in ('install.sh', 'install.ps1'):
            src = (TOP / f).read_text()
            self.assertIn('https://github.com/dikeckaan/mu300-linux-vpn/releases/latest/download', src, f)
            self.assertIn('MU300_VPN_URL', src, f)
            self.assertIn('MU300_VPN_MODULE', src, f)
            self.assertNotIn('make-extra.sh" vpn', src, f)

    def test_images_carry_jq(self):
        # the VPN module's mu300-vpn reads vmess links and raw Xray/sing-box configs with jq, so every image has it; Ubuntu
        # and Arch also get wireguard-tools (OpenWrt has it already). The CI runner needs jq for the same tests.
        def words(block):
            return block.replace('\\\n', ' ').split()
        docker = (TOP / 'rootfs/Dockerfile').read_text()
        apt = re.search(r'apt-get install -y --no-install-recommends(.*?)&&', docker, re.S).group(1)
        self.assertIn('jq', words(apt))
        self.assertIn('wireguard-tools', words(apt))
        owrt = (TOP / 'openwrt/build-rootfs.sh').read_text()
        first = re.search(r'\napk add (.*?)>/dev/null', owrt, re.S).group(1)
        self.assertIn('jq', words(first))
        arch = (TOP / 'arch/build-rootfs.sh').read_text()
        pac = re.search(r'pacman -Syu --noconfirm --needed(.*?)>/dev/null', arch, re.S).group(1)
        self.assertIn('jq', words(pac))
        self.assertIn('wireguard-tools', words(pac))
        ci = (TOP / '.github/workflows/tests.yml').read_text()
        self.assertIn('jq', re.search(r'sudo apt-get install -y -qq ([^>]*)>', ci).group(1).split())

    def test_images_carry_docker(self):
        # FINDINGS 130: Docker in every image - Ubuntu's own packages, OpenWrt's feed; neither starts dockerd at boot,
        # both keep its data on the Linux partition beside the systems (an update never copies it) and use Docker's
        # nftables backend (no iptables-nft translation, no xt_addrtype that the 5.4 kernel lacks)
        def words(block):
            return block.replace('\\\n', ' ').split()
        docker = (TOP / 'rootfs/Dockerfile').read_text()
        apt = words(re.search(r'apt-get install -y --no-install-recommends(.*?)&&', docker, re.S).group(1))
        self.assertIn('docker.io', apt)
        self.assertIn('docker-compose-v2', apt)
        self.assertNotIn('docker-ce', apt)
        self.assertIn('usermod -aG sudo,docker ubuntu', docker)
        # by its socket, not at boot
        self.assertIn('systemctl disable docker.service containerd.service', docker)
        self.assertIn('systemctl is-enabled docker.socket', docker)
        for f in ('/usr/bin/containerd-stress', '/usr/bin/ctr', '/usr/libexec/docker/cli-plugins/docker-trust'):
            self.assertIn('path-exclude=' + f, docker)
        conf = json.loads((TOP / 'rootfs/overlay/etc/docker/daemon.json').read_text())
        self.assertEqual(conf['data-root'], '/mnt/mu300-disk/docker/ubuntu')
        self.assertEqual(conf['storage-driver'], 'overlay2')
        self.assertEqual(conf['firewall-backend'], 'nftables')
        self.assertIs(conf['features']['containerd-snapshotter'], False)
        # Docker must not refuse to start while the system forwards nothing (no uplink yet)
        self.assertIs(conf['ip-forward'], False)

        owrt = (TOP / 'openwrt/build-rootfs.sh').read_text()
        # OpenWrt: in the image with MU300_DOCKER=1, ready to install (its settings and zone only) by default - 190 MB,
        # twice over during an update, do not fit OpenWrt's smallest region (800 MiB)
        self.assertIn('case ${MU300_DOCKER:-0} in', owrt)
        self.assertIn('-e MU300_DOCKER="$DOCKER"', owrt)
        block = owrt[owrt.index('if [ "$MU300_DOCKER" = 1 ]; then'):]
        block = block[:block.index('\nfi\n')]
        self.assertIn('\n    apk add dockerd docker docker-compose >/dev/null\n', block)
        # the panel's page for it only on openwrt-luci, before the loop that adds the translations of the luci-apps
        self.assertIn('[ -d /in/luci-plugin ] && apk add luci-app-dockerman >/dev/null', block)
        self.assertLess(owrt.index('apk add luci-app-dockerman'), owrt.index('for c in base $(apk list --installed'))
        # what its kmods put in /etc/modules.d goes; it does not start at boot
        self.assertLess(block.index('ls /etc/modules.d /etc/modules-boot.d > /tmp/modules.before'),
                        block.index('apk add dockerd'))
        self.assertIn('/etc/rc.d/S*dockerd', block)
        self.assertIn('echo "dockerd starts at boot"', owrt)
        self.assertIn('rm -f $R/etc/uhttpd.key $R/etc/uhttpd.crt', owrt)
        # the build script is one single-quoted sh -c argument: no apostrophe may be in it
        start = "mu300-$FLAVOUR-base:$VER /bin/sh -eu -c '"
        body = owrt[owrt.index(start) + len(start):owrt.rindex("ls -la /out/$OUT'")]
        self.assertNotIn("'", body)
        # the package reads Docker's own file (alt_config_file), which an apk add on the device leaves as it is
        cfg = (OPENWRT / 'etc/config/dockerd').read_text()
        self.assertIn("\toption alt_config_file '/etc/docker/daemon.json'\n", cfg)
        conf = json.loads((OPENWRT / 'etc/docker/daemon.json').read_text())
        self.assertEqual(conf['data-root'], '/mnt/mu300-disk/docker/openwrt')
        self.assertIn('/mnt/mu300-disk/docker/$MU300_SYSTEM', owrt)
        self.assertEqual(conf['firewall-backend'], 'nftables')
        self.assertEqual(conf['storage-driver'], 'overlay2')
        self.assertIs(conf['features']['containerd-snapshotter'], False)
        self.assertEqual(conf['default-address-pools'], [{'base': '172.16.0.0/12', 'size': 20}])
        sysctl = (OPENWRT / 'etc/sysctl.d/13-mu300-docker.conf').read_text()
        self.assertIn('net.bridge.bridge-nf-call-iptables=0', sysctl)
        # the firewall: the docker zone covers only Docker's range, never br-lan; out to wan, in from the LAN only
        zone = (OPENWRT / 'etc/uci-defaults/95-mu300-docker').read_text()
        self.assertTrue(os.access(OPENWRT / 'etc/uci-defaults/95-mu300-docker', os.X_OK))
        for line in ("set network.docker.device='docker0'", "set network.@device[-1].name='docker0'",
                     "add_list firewall.docker.network='docker'", "add_list firewall.docker.device='br-+'", "add_list firewall.docker.device='!br-lan'",
                     "add_list firewall.docker.subnet='172.16.0.0/12'", "set firewall.docker_wan.dest='wan'",
                     "set firewall.lan_docker.src='lan'"):
            self.assertIn(line, zone)
        self.assertNotRegex(zone, r"src='wan'")
        # containers never reach the router itself, nor the LAN, nor one Docker network another (security review)
        self.assertIn("set firewall.docker.input='REJECT'", zone)
        self.assertIn("set firewall.docker.forward='REJECT'", zone)
        self.assertNotIn("firewall.docker.input='ACCEPT'", zone)
        self.assertNotRegex(zone, r"src='docker'\nset firewall\.[a-z_]*\.dest='lan'")
        # Ubuntu: a container opens nothing on the device (the host firewall drops Docker's bridges, never br-lan)
        fw = (TOP / 'rootfs/overlay/etc/mu300/firewall.nft').read_text()
        self.assertIn('iifname "docker0" counter drop', fw)
        self.assertIn('iifname "br-*" iifname != "br-lan" counter drop', fw)
        # the marker is a file of the device, so a zone the user deleted is not made again after an update
        self.assertIn('[ -e /etc/mu300/docker-zone ] && exit 0', zone)

    def test_release_audit_refuses_the_audio_stack(self):
        # make-release.sh: no kernel bundle carries a driver of the removed call-audio stack, and no image its programs or
        # services. The audit's own lines, run against bundles made here.
        rel = (TOP / 'tools' / 'make-release.sh').read_text()
        audit = rel[rel.index('AUDIO_KO="'):rel.index('[ $fail = 0 ] || { echo "audit failed')]
        self.assertEqual(23, len(re.search(r'AUDIO_KO="([^"]*)"', audit).group(1).split()))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)

            def bundle(name, *mods):
                src = d / 'src' / name
                shutil.rmtree(src, ignore_errors=True)
                (src / 'modules').mkdir(parents=True)
                for m in ('sipc-core.ko',) + mods:
                    (src / 'modules' / m).write_text('')
                subprocess.run(['tar', '-czf', str(d / f'{name}.tar.gz'), '-C', str(src), '.'], check=True)

            def manifest(name, *paths):
                with gzip.open(d / f'{name}.manifest.gz', 'wt') as f:
                    for p in ('etc/mu300/image-version',) + paths:
                        f.write(f'f 755 0 0 1 {"0" * 64} {p}\n')

            def run():
                r = subprocess.run(['sh', '-c', 'fail=0\n' + audit + 'echo "fail=$fail"'], env={'D': str(d), 'PATH': os.environ['PATH']},
                                   capture_output=True, text=True)
                return r.stdout

            bundle('mu300-kernel', 'sprd-dma.ko', 'virt-dma.ko')
            bundle('mu300-kernel-6.18')
            bundle('mu300-kernel-7.2')
            bundle('mu300-kernel-7.3')
            manifest('mu300-ubuntu-rootfs', 'opt/mu300/bin/mu300-atd', 'etc/systemd/system/mu300-atd.service')
            manifest('mu300-openwrt-rootfs', 'etc/init.d/mu300-atd')
            self.assertIn('fail=0', run())
            for name, mods in (('mu300-kernel', ('audio_sipc.ko',)), ('mu300-kernel-6.18', ('sprd-dma.ko',)),
                               ('mu300-kernel-7.2', ('snd-soc-sprd-card.ko',)), ('mu300-kernel-7.3', ('audio_mem.ko',))):
                bundle(name, *mods)
                out = run()
                self.assertIn('fail=1', out, name)
                self.assertIn(f'{name} carries {mods[0]}', out)
                bundle(name)
            for name, path in (('mu300-ubuntu-rootfs', 'opt/mu300/bin/mu300-voice'),
                               ('mu300-ubuntu-rootfs', 'etc/systemd/system/mu300-audio.service'),
                               ('mu300-openwrt-rootfs', 'etc/init.d/mu300-audio'),
                               ('mu300-ubuntu-rootfs', 'etc/modprobe.d/mu300-audio.conf')):
                manifest(name, path)
                self.assertIn('fail=1', run(), path)
                manifest(name)
            self.assertIn('fail=0', run())

    def test_e2fsck_is_pinned_and_everywhere(self):
        # tools/e2fsck/build.sh: a pinned e2fsprogs (its signed SHA-256) in a pinned image (FINDINGS 182), and every
        # path that builds a boot ramdisk hands it over
        text = (TOP / 'tools' / 'e2fsck' / 'build.sh').read_text()
        self.assertRegex(text, r'(?m)^VER=1\.47\.4$')
        self.assertRegex(text, r'(?m)^SHA=[0-9a-f]{64}$')
        self.assertRegex(text, r'(?m)^IMG=alpine:[0-9.]+@sha256:[0-9a-f]{64}$')
        self.assertIn('sha256sum -c', text)
        for f, s in (('tools/make-release.sh', '--e2fsck "$IN/tools/e2fsck/e2fsck"'),
                     ('tools/make-release.sh', 'has no sbin/e2fsck'),
                     ('upstream/make-bundle.sh', '--e2fsck "$W/e2fsck"'),
                     ('install.sh', '${E2FSCK:+--e2fsck "$E2FSCK"}'),
                     ('install.ps1', "'--e2fsck', \"$REL\\kernel\\e2fsck\"")):
            self.assertIn(s, (TOP / f).read_text(), f)
        # init: before every mount of the root, and never a gate (root_fsck always returns 0)
        init = (TOP / 'boot' / 'init').read_text()
        self.assertEqual(3, init.count('root_fsck "$'))
        block = re.search(r'# --- fsck begin\n(.*?)# --- fsck end', init, re.S).group(1)
        self.assertNotRegex(block, r'return [1-9]')

    def test_init_finds_partitions_after_the_modules(self):
        # the eMMC driver is one of the vendor modules: misc and boot_b cannot be found before they are loaded
        init = (TOP / 'boot' / 'init').read_text()
        calls = [l.strip() for l in init.splitlines() if l.strip() in ('load_vendor_modules', 'find_partitions')]
        self.assertEqual(calls, ['load_vendor_modules', 'find_partitions'])

    def test_every_device_has_its_files(self):
        # a device the installers know needs its module order; its modules come from kernel/build-<device>.sh
        for dev in ('u30air',):
            self.assertTrue((TOP / 'boot' / f'module-order-{dev}.txt').is_file())
            self.assertTrue((TOP / 'kernel' / f'{dev}.fragment').is_file())
            self.assertIn(dev, (TOP / 'install.sh').read_text())
            self.assertIn(dev, (TOP / 'install.ps1').read_text())

    def test_mainline_keeps_the_sd_host(self):
        # the SD card can hold the Linux filesystem: the port lets the card slot's host probe next to the eMMC, and
        # still keeps any other sdhci host (the stock DT's sdio_wifi; Wi-Fi is on PCIe) out
        port = (TOP / 'upstream' / 'port' / 'install.py').read_text()
        self.assertIn('MU300: only the eMMC and the card slot', port)
        self.assertIn('if (!of_property_read_bool(pdev->dev.of_node, "non-removable") &&', port)
        self.assertIn('of_property_match_string(pdev->dev.of_node, "sprd,name", "sdio_sd") < 0)', port)
        self.assertIn('MU300: CD GPIO deferred', port)
        # a deferral that is only masked leaves the rest of mmc_of_parse() undone (UHS modes, no-sdio, no-mmc): the
        # unresolvable cd-gpios is dropped and the parse run again
        self.assertIn('of_remove_property(pdev->dev.of_node, cd)', port)
        self.assertIn("'MU300: only the eMMC and the card slot', 'MU300: CD GPIO deferred', "
                      "'of_remove_property(pdev->dev.of_node, cd)'", port)
        self.assertRegex((TOP / 'upstream' / 'make-bundle.sh').read_text(),
                         r"printf 'sdcard\\nlinux-slot\\ngpt-root\\n' > \"\$W/b/features\"")

    def test_mainline_keeps_the_emmc_at_mmc0(self):
        # Both hosts probe asynchronously and the stock DT has no mmc aliases, so the card slot could take mmc0 and
        # leave mmcblk1 to the eMMC: init then never found the card and rebooted at 300 s (FINDINGS 31l). The card
        # slot's host waits until the eMMC's host is added.
        port = (TOP / 'upstream' / 'port' / 'install.py').read_text()
        self.assertIn('MU300: the eMMC is mmc0', port)
        self.assertIn('static bool sdhci_sprd_emmc_added;', port)
        self.assertIn('for_each_compatible_node(np, NULL, "sprd,sdhci-r11")', port)
        # the deferral sits right after the host filter, before sdhci_pltfm_init() allocates the host index
        self.assertIn('\\t    sdhci_sprd_emmc_pending(pdev->dev.of_node))\n\\t\\treturn -EPROBE_DEFER;\n', port)
        # and says what that costs: without a working eMMC host the card slot never binds
        self.assertIn("no SD root without a working eMMC host", port)
        self.assertLess(port.index("t.replace(filt, filt + '''"), port.index('add_old = '))
        # the flag is set only once the eMMC's host is added
        self.assertIn("add_old = '\\tret = __sdhci_add_host(host);\\n\\tif (ret)\\n\\t\\tgoto err_cleanup_host;\\n'",
                      port)
        self.assertIn('\\t\\tWRITE_ONCE(sdhci_sprd_emmc_added, true);', port)
        # and the build stops when the edit did not apply
        self.assertIn("'MU300: the eMMC is mmc0', 'WRITE_ONCE(sdhci_sprd_emmc_added, true);'", port)

    def test_mainline_v50_bits_bind_only_on_a_v50(self):
        """The ZTE V50 (FINDINGS 95, kano's port): its LED driver binds only ZTE's AW9523B node, its eMMC host is kept
        awake only where that node is, and a kernel bundle names the V50 only when it has the driver. The F50's and
        the U30 Air's device trees have no such node: there the kernel does what it did."""
        cfg = (TOP / 'upstream' / 'mu300-mainline.config').read_text()
        self.assertRegex(cfg, r'(?m)^CONFIG_LEDS_AW9523B=y$')
        port = (TOP / 'upstream' / 'port' / 'install.py').read_text()
        self.assertIn("('LEDS_AW9523B', 'leds-aw9523b',", port)
        drv = (TOP / 'upstream' / 'port' / 'drivers' / 'leds' / 'leds-aw9523b.c').read_text()
        self.assertEqual(re.findall(r'\.compatible = "([^"]+)"', drv), ['awinic,aw9523b'])
        for s in ('"aw9523b,led_enable", &enabled) || enabled != 1', '"aw9523b,gpio_enable", &gpio_enabled) || gpio_enabled',
                  'if (ret != AW_ID_VALUE) {', '"v50:net1:white"', '"v50:wifi:blue"', 'devm_add_action_or_reset(dev, aw9523b_off, chip)'):
            self.assertIn(s, drv)
        p17 = (TOP / 'upstream' / 'patches' / '0017-mmc-sdhci-sprd-V50-eMMC-host-kept-awake.patch').read_text()
        for s in ('+\tif (mmc_card_is_removable(host->mmc))\n+\t\treturn false;',
                  '+\tnp = of_find_compatible_node(NULL, NULL, "awinic,aw9523b");',
                  '+\tif (sdhci_sprd_keep_awake(host)) {\n+\t\tpm_runtime_forbid(&pdev->dev);',
                  '+\tif (sdhci_sprd_keep_awake(host))\n+\t\tpm_runtime_allow(&pdev->dev);\n \tpm_runtime_put_noidle(&pdev->dev);'):
            self.assertIn(s, p17)
        # the patch touches nothing else
        self.assertEqual(re.findall(r'(?m)^\+\+\+ (\S+)', p17), ['b/drivers/mmc/host/sdhci-sprd.c'])
        bundle = (TOP / 'upstream' / 'make-bundle.sh').read_text()
        self.assertIn("if grep -qx 'CONFIG_LEDS_AW9523B=y' \"$UO/kernel.config\"; then\n    echo \"f50 u30air v50\"", bundle)
        self.assertIn('else\n    echo "f50 u30air" > "$W/b/devices"', bundle)
        # the 5.4 bundle does not name it
        self.assertIn('echo "f50 u30air" > "$K/devices"', (TOP / 'tools' / 'make-release.sh').read_text())

    def test_mainline_emmc_timing_falls_back(self):
        """FINDINGS 160 (issue #226): an eMMC whose HS400ES selection fails at a cold boot is initialised again at
        HS200, then HS/DDR52, then HS, instead of being given up after mmc_rescan's attempts at HS400ES; a boot whose
        HS400ES works drops nothing. The test hook in sdhci-sprd is off by default and touches only CMD13 while a new
        card is set up at HS400ES; a test build sets it through build.sh's MU300_CMDLINE_EXTRA, never a release."""
        p = (TOP / 'upstream' / 'patches' / '0021-mmc-HS400ES-failure-falls-back-to-HS200.patch').read_text()
        self.assertEqual(re.findall(r'(?m)^\+\+\+ (\S+)', p), ['b/drivers/mmc/core/mmc.c', 'b/drivers/mmc/host/sdhci-sprd.c'])
        core = p[p.index('+++ b/drivers/mmc/core/mmc.c'):p.index('--- a/drivers/mmc/host/sdhci-sprd.c')]
        # the ladder: HS400ES drops HS400 with it, then HS200, then DDR52; a removed card drops nothing
        for s in ('+\tif (err == -ENOMEDIUM)\n+\t\treturn;',
                  '+\tif (card->mmc_avail_type & EXT_CSD_CARD_TYPE_HS400ES) {\n'
                  '+\t\thost->caps2 &= ~(MMC_CAP2_HS400_ES | MMC_CAP2_HS400);',
                  '+\t\thost->caps2 &= ~(MMC_CAP2_HS200 | MMC_CAP2_HS400);',
                  '+\t} else if (card->mmc_avail_type & EXT_CSD_CARD_TYPE_DDR_52) {\n+\t\thost->caps &= ~MMC_CAP_DDR;',
                  '+\tpr_warn("%s: %s failed (%d), the next initialisation uses %s\\n",',
                  '+timing_failed:\n+\tmmc_timing_fallback(card, err);\n free_card:'):
            self.assertIn(s, core)
        # exactly the four failures of the timing selection go through it, nothing before or after
        self.assertEqual(len(re.findall(r'(?m)^-\t+goto free_card;$', core)), 4)
        self.assertEqual(len(re.findall(r'(?m)^\+\t+goto timing_failed;$', core)), 4)
        self.assertEqual(core.count('goto timing_failed;'), 4)
        # the card type is chosen again from the host caps at every initialisation, also of a known card: what the
        # caps drop relies on (kept in the context of the patch would be too far; the header says it)
        self.assertIn('"since host caps could have been changed when debugging"', p)
        hook = p[p.index('+++ b/drivers/mmc/host/sdhci-sprd.c'):]
        for s in ('+static unsigned int fail_hs400es;\n+module_param(fail_hs400es, uint, 0644);',
                  '+\tif (unlikely(sprd_host->fail_hs400es) && mmc->ios.enhanced_strobe &&\n'
                  '+\t    !mmc->card && mrq->cmd->opcode == MMC_SEND_STATUS) {\n+\t\tmrq->cmd->error = -EILSEQ;',
                  '+\tsprd_host->fail_hs400es = fail_hs400es > 0;'):
            self.assertIn(s, hook)
        self.assertNotRegex(hook, r'(?m)^\+static unsigned int fail_hs400es\s*=')
        build = (TOP / 'upstream' / 'build.sh').read_text()
        self.assertIn('--set-str CMDLINE "$c $MU300_CMDLINE_EXTRA"', build)
        # after the check of the config file (which would refuse a changed CMDLINE), before the build
        self.assertLess(build.index('config options not taken'), build.index('MU300_CMDLINE_EXTRA'))
        self.assertLess(build.index('MU300_CMDLINE_EXTRA'), build.index('make O=$O ARCH=arm64 -j"$(nproc)" Image'))
        for wf in ('release.yml', 'mainline.yml'):
            self.assertNotIn('MU300_CMDLINE_EXTRA', (TOP / '.github' / 'workflows' / wf).read_text())
        self.assertNotIn('fail_hs400es', (TOP / 'upstream' / 'mu300-mainline.config').read_text())

    def test_mainline_drives_the_u30_air_charger(self):
        # Without a driver the charger kept what Android left in it: charging off (CHG_CONFIG 0), 500 mA input, and
        # the battery drained while plugged in. bq256xx drives the vendor node "ti,bq2560x_chg" now.
        cfg = (TOP / 'upstream' / 'mu300-mainline.config').read_text()
        for k in ('CONFIG_CHARGER_BQ256XX=y', 'CONFIG_REGMAP_I2C=y', 'CONFIG_I2C_SPRD=y', 'CONFIG_EXTCON_USB_GPIO=y'):
            self.assertRegex(cfg, rf'(?m)^{k}$')
        port = (TOP / 'upstream' / 'port' / 'install.py').read_text()
        # the edit itself, on the two forms of the I2C table (6.18 positional, 7.x designated), twice (idempotent)
        code = port[port.index("bp = os.path.join(tree, 'drivers/power/supply/bq256xx_charger.c')"):
                    port.index('# Every edit above is a text substitution')]
        of = ('static const struct of_device_id bq256xx_of_match[] = {\n'
              '\t{ .compatible = "ti,bq25601", .data = &bq256xx_chip_info_tbl[BQ25601] },\n\t{}\n};\n')
        acpi = ('static const struct acpi_device_id bq256xx_acpi_match[] = {\n'
                '\t{ "bq25601", (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] },\n\t{}\n};\n')
        for entry, want in (('{ "bq25601", (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] }',
                             '\t{ "bq2560x_chg", (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] },\n'),
                            ('{ .name = "bq25601", .driver_data = (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] }',
                             '\t{ .name = "bq2560x_chg", .driver_data = (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] },\n')):
            with tempfile.TemporaryDirectory() as tree:
                f = Path(tree, 'drivers/power/supply/bq256xx_charger.c')
                f.parent.mkdir(parents=True)
                f.write_text('static const struct i2c_device_id bq256xx_i2c_ids[] = {\n\t' + entry + ',\n\t{}\n};\n'
                             'MODULE_DEVICE_TABLE(i2c, bq256xx_i2c_ids);\n' + of + acpi)
                for _ in range(2):
                    exec(code, {'os': os, 'tree': tree})
                t = f.read_text()
                self.assertEqual(t.count(want), 1, t)
                self.assertEqual(t.count('"ti,bq2560x_chg", .data = &bq256xx_chip_info_tbl[BQ25601]'), 1, t)
                self.assertNotIn('bq2560x_chg', t[t.index('acpi_device_id'):])   # the ACPI table stays as it was
        # and the build stops when it did not apply
        self.assertIn("('drivers/power/supply/bq256xx_charger.c', ['\"ti,bq2560x_chg\"", port)
        # the driver's handling of that node: no stale cache, Android's 1.98 A and 4.208 V at most, the chip's own
        # input current from the PMIC's charger detection on every VBUS change from the extcon, the chip's own only as
        # a fallback, charging on unless userspace said "N/A"
        p9 = (TOP / 'upstream' / 'patches' / '0009-power-bq256xx-drive-the-U30-Air-charger.patch').read_text()
        for s in ('.cache_type = REGCACHE_NONE', 'BQ2560X_CHG_ICHG_MAX_uA\t\t1980000', 'BQ2560X_CHG_VBATREG_MAX_uV\t4208000',
                  'BQ256XX_IINDET_EN', 'devm_extcon_register_notifier(dev, bq->vbus_edev, EXTCON_USB',
                  '"linux,extcon-usb-gpio"', 'bq->init_data.iindpm = 500000;', 'WRITE_ONCE(bq->chg_off',
                  'off ? 0 : BQ256XX_CHG_CONFIG_MASK', 'case BQ256XX_VBUS_STAT_USB_CDP:\n+\t\treturn 1500000;',
                  'case BQ256XX_VBUS_STAT_USB_DCP:\n+\t\treturn 2000000;', 'case BQ256XX_VBUS_STAT_NONSTD:\n+\t\treturn 1000000;',
                  'static char *supplied_to[] = { "sc27xx-fgu" };',
                  # the PMIC's charger detection first: the charger's own (IINDET_EN) drives D+/D-, the gadget's lines
                  'sprd_pmic_detect_charger_type(bq->pmic)', 'of_find_compatible_node(NULL, NULL, "sprd,ump9620")',
                  'bus_find_device_by_of_node(&spi_bus_type, np)', 'no charger type from the PMIC'):
            self.assertIn(s, p9)
        # the fuel gauge asks the charger by the name the driver registers
        p10 = (TOP / 'upstream' / 'patches' / '0010-power-sc27xx-fuel-gauge-status-from-bq256xx.patch').read_text()
        self.assertIn('+\t"bq256xx-charger",', p10)
        # the U30 Air's other charger (issue #102): when nothing answers at the node's address, ZTE's driver goes on
        # at 0x1a (an SGM41513), and so does this one, with that chip's charge and termination current steps; a
        # charger that answers at the node's address takes none of it
        p15 = (TOP / 'upstream' / 'patches' / '0015-power-bq256xx-SGM41513-at-0x1a.patch').read_text()
        for s in ('#define SGM41513_I2C_ADDR\t\t0x1a', 'if (part < 0 && client->addr != SGM41513_I2C_ADDR) {',
                  'devm_i2c_new_dummy_device(dev, client->adapter, SGM41513_I2C_ADDR)',
                  'bq->regmap = devm_regmap_init_i2c(chip,', 'no charger: %d (and %d at 0x%02x)',
                  '{ 0, 40000, 130000, 300000, 540000, 1020000, 1500000, 2460000 }',
                  '{ 5000, 10000, 20000, 30000, 60000, 60000, 120000, 120000 }',
                  '80000, 100000, 120000, 140000, 160000, 180000, 200000, 240000,',
                  'info->bq256xx_set_ichg = sgm41513_set_ichg_curr;', 'info->bq256xx_set_iterm = sgm41513_set_term_curr;',
                  'info->bq256xx_set_iprechg = sgm41513_set_prechrg_curr;'):
            self.assertIn(s, p15)
        # ... but only on a board with a battery (the U30 Air's charger policy service): the F50 has a charger at
        # 0x1a too, and bound and told to charge it reset the board in a loop on a router's port (issue #225)
        p16 = (TOP / 'upstream' / 'patches' / '0016-power-bq256xx-SGM41513-only-with-a-battery.patch').read_text()
        for s in ('of_find_node_by_path("/charger_policy_service")', 'of_node_put(np);',
                  '+\t\tif (part < 0 && client->addr != SGM41513_I2C_ADDR && battery) {'):
            self.assertIn(s, p16)
        # the PMIC's charger detection reads the UMP9620's own register (0x239c), not the SC2730's (0x1b9c), and a
        # tree prepared with the old one is corrected
        self.assertIn(r"#define SPRD_UMP9620_CHG_DET\t\t0x239c", port)
        self.assertIn(r"'\t.charger_det = SPRD_SC2730_CHG_DET,\n};\n', UMP9620_DATA)", port)
        self.assertIn("'.charger_det = SPRD_UMP9620_CHG_DET,'", port)
        # Android's last capacity in the FGU's user area is whole percent in bits 7:0 and tenths in bits 11:8 (575 =
        # 0x23f = 63.2 %), not 0.1 %; mainline writes whole percent; a valid value is taken as it is, only an invalid
        # one goes to the OCV (FINDINGS 181: no migration guess); the charger's Full (from a charger driver) means 100 %
        # with the battery current within the node's fullbatt-current, no OCV percentage or voltage (the 4.146 V of a
        # real Full is below fullbatt-voltage); the capacity is saved as it changes and stays 0-100
        patches = TOP / 'upstream' / 'patches'
        self.assertFalse((patches / '0011-power-sc27xx-fuel-gauge-UMP9620-capacity-in-tenths.patch').exists())
        p11 = (patches / '0011-power-sc27xx-fuel-gauge-UMP9620-saved-capacity-and-full.patch').read_text()
        for s in ('+#define UMP9620_FGU_CAP_INTEGER_MASK\tGENMASK(7, 0)', '+#define UMP9620_FGU_CAP_DECIMAL_MASK\tGENMASK(11, 8)',
                  '+\t\tcap = clamp(cap, 0, 100);', '+\tvalid = whole <= 100 && tenths <= 9 && !(whole == 100 && tenths);',
                  '+\tif (valid) {',
                  '+\t*cap = power_supply_ocv2cap_simple(data->cap_table, data->table_len, ocv);',
                  '+\tif (!is_first_poweron && data->var == &ump9620_info)',
                  '+\tif (data->var == &ump9620_info && chg_sts == POWER_SUPPLY_STATUS_FULL && cap != 100 &&',
                  '+\t    sc27xx_fgu_has_charger() && ump9620_fgu_full_current(data)) {',
                  '+\treturn abs(cur) <= (int)(data->full_current_ua / 1000);',
                  '+\t\t\tof_property_read_u32(bat, "fullbatt-current", &data->full_current_ua);',
                  '+\t\tsc27xx_fgu_adjust_cap(data, 100);',
                  '+\t\tif (*cap != data->saved_cap && sc27xx_fgu_save_last_cap(data, *cap))',
                  '+\t\tval->intval = clamp(value, 0, 100);'):
            self.assertIn(s, p11)
        self.assertNotIn('* 10;', p11)
        for s in ('ambiguous', 'UMP9620_FGU_RELAXED_MA', 'UMP9620_FGU_FULL_MIN_CAP', 'fullbatt-voltage"'):
            self.assertNotIn(s, ''.join(l for l in p11.splitlines(True) if l.startswith('+')))
        # (FINDINGS 105, after kanoqwq's 2af101f8) the capacity is read and kept once more at a power-off, on UMP9620
        # with a battery only; the user area's steps stay regmap_update_bits() as in Unisoc's driver and the U30 Air's
        # stock module (FINDINGS 181), with its 200 us wait after the release too
        p18 = (patches / '0018-power-sc27xx-fuel-gauge-UMP9620-capacity-kept-at-power-off.patch').read_text()
        for s in ('+static void sc27xx_fgu_shutdown(struct platform_device *pdev)',
                  '+\tif (!data || data->var != &ump9620_info || !data->bat_present)',
                  '+\t\tret = sc27xx_fgu_save_last_cap(data, clamp(cap, 0, 100));',
                  '+\t\tret = sc27xx_fgu_save_boot_mode(data, SC27XX_FGU_NORMAIL_POWERTON);',
                  '+\t.shutdown = sc27xx_fgu_shutdown,'):
            self.assertIn(s, p18)
        self.assertNotIn('regmap_write_bits(data', p18)
        self.assertEqual(p18.count('+\tif (!ret && data->var == &ump9620_info)\n+\t\tudelay(200);'), 2)
        # mu300-usb reaches the chip past the driver and leaves its watchdog off then
        usb = (BIN / 'mu300-usb').read_text()
        self.assertIn('[ -e "$R/sys/bus/i2c/devices/$BUS-006b/driver" ] && FORCE=-f', usb)

    def test_mainline_can_suspend(self):
        # System suspend (PSCI SYSTEM_SUSPEND) and a tickless idle: under the periodic tick a CPU brought back online
        # was left out of the timer broadcast and locked up, which every suspend (and `cpu7 offline/online`) hit
        cfg = (TOP / 'upstream' / 'mu300-mainline.config').read_text()
        for k in ('CONFIG_SUSPEND', 'CONFIG_PM_SLEEP', 'CONFIG_NO_HZ_IDLE', 'CONFIG_HIGH_RES_TIMERS'):
            self.assertRegex(cfg, rf'(?m)^{k}=y$')
        drv = TOP / 'upstream' / 'port' / 'drivers'
        # the PMIC watchdog keeps counting while the AP sleeps: stopped for the sleep, armed again after it
        wdt = (drv / 'watchdog' / 'ump9620-pmic-wdt.c').read_text()
        self.assertIn('NOIRQ_SYSTEM_SLEEP_PM_OPS(ump9620_wdt_suspend_noirq, ump9620_wdt_resume_noirq)', wdt)
        self.assertIn('.pm = pm_sleep_ptr(&ump9620_wdt_pm_ops)', wdt)
        self.assertIn('platform_set_drvdata(pdev, w);', wdt)
        # PCIe WAKE# is a wakeup only when power/wakeup says so, and is masked (unlazily) for the sleep otherwise
        pcie = (drv / 'pci' / 'controller' / 'dwc' / 'pcie-sprd.c').read_text()
        self.assertIn('if (device_may_wakeup(pci->dev))\n\t\tpm_wakeup_hard_event(pci->dev);', pcie)
        self.assertNotIn('IRQF_TRIGGER_FALLING | IRQF_NO_SUSPEND', pcie)
        self.assertIn('irq_set_status_flags(ctrl->wakeup_irq, IRQ_DISABLE_UNLAZY);', pcie)
        self.assertNotIn('device_init_wakeup(dev, true)', pcie)
        self.assertIn('SET_SYSTEM_SLEEP_PM_OPS(sprd_pcie_pm_suspend, sprd_pcie_pm_resume)', pcie)
        # the firmware's debug log pulled WAKE# right after every L2 entry: off by default, and a resume leaves it be
        mods = TOP / 'upstream' / 'modules'
        sysfs = (mods / 'wcn_bsp' / 'platform' / 'sysfs.c').read_text()
        self.assertRegex(sysfs, r'#endif\n(\t.*\n)*\tsysfs_info\.armlog_status = 0;\n')
        ep = (mods / 'wcn_bsp' / 'pcie' / 'pcie.c').read_text()
        resume = ep[ep.index('static int sprd_ep_resume('):ep.index('const struct dev_pm_ops sprd_ep_pm_ops')]
        self.assertNotIn('wcn_set_armlog(true)', resume)
        # without a WoWLAN configuration cfg80211 closed the interfaces while the bus was going down, and the
        # firmware asserted at the next Wi-Fi open: WoWLAN "any" from the wiphy's registration on
        iface = (mods / 'sprd_wlan_combo' / 'common' / 'iface.c').read_text()
        self.assertIn('wowlan->any = true;\n\t\t\twiphy->wowlan_config = wowlan;', iface)
        self.assertLess(iface.index('ret = wiphy_register(wiphy);'), iface.index('wiphy->wowlan_config = wowlan;'))

    def test_mainline_config_has_kvm_and_the_module_set(self):
        # /dev/kvm (the CPUs start at EL2) and the router/container modules; the kernel's own modules reach the bundle
        cfg = (TOP / 'upstream' / 'mu300-mainline.config').read_text()
        opts = {}
        for line in cfg.splitlines():
            if line.startswith('CONFIG_'):
                k, v = line.split('=', 1)
                # an option given twice must say the same thing both times: merge_config takes the last one silently
                self.assertEqual(opts.setdefault(k, v), v, k)
        for k in ('CONFIG_VIRTUALIZATION', 'CONFIG_KVM', 'CONFIG_MODULES', 'CONFIG_WIREGUARD', 'CONFIG_NET_SCH_CAKE',
                  'CONFIG_TUN', 'CONFIG_NF_FLOW_TABLE', 'CONFIG_IKCONFIG_PROC', 'CONFIG_CHECKPOINT_RESTORE',
                  'CONFIG_MPTCP', 'CONFIG_IP_MROUTE', 'CONFIG_NFSD_V4', 'CONFIG_FS_ENCRYPTION'):
            self.assertEqual(opts.get(k), 'y', k)
        for k in ('CONFIG_VHOST_NET', 'CONFIG_NTFS3_FS', 'CONFIG_DM_CRYPT', 'CONFIG_NET_SCH_HTB', 'CONFIG_USB_NET_CDC_MBIM',
                  'CONFIG_XFRM_USER', 'CONFIG_BRIDGE_NETFILTER', 'CONFIG_BINFMT_MISC', 'CONFIG_SND_USB_AUDIO',
                  'CONFIG_VIRTIO_FS', 'CONFIG_VHOST_SCSI', 'CONFIG_9P_FS', 'CONFIG_DM_INTEGRITY',
                  'CONFIG_MPLS_ROUTING', 'CONFIG_USBIP_HOST', 'CONFIG_USB_VIDEO_CLASS'):
            self.assertEqual(opts.get(k), 'm', k)
        mods = (TOP / 'upstream' / 'build-modules.sh').read_text()
        self.assertIn('INSTALL_MOD_STRIP=1 DEPMOD=true modules_install', mods)
        self.assertIn('has the name of an in-tree module', mods)

    def test_ttl_page_is_wired(self):
        # Cellular > TTL: the menu entry right after the AT terminal, its view, the rpc declarations in common.js,
        # ttl_get readable and ttl_set only with write access, the adapter executable
        import json
        app = TOP / 'openwrt' / 'luci-app-mu300'
        menu = json.loads((app / 'root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
        ttl = menu['admin/modem/ttl']
        self.assertEqual(ttl['title'], 'TTL')
        self.assertEqual(ttl['action'], {'type': 'view', 'path': 'mu300/ttl'})
        cellular = sorted((v['order'], k) for k, v in menu.items() if k.startswith('admin/modem/'))
        keys = [k for _, k in cellular]
        self.assertEqual(keys[keys.index('admin/modem/at') + 1], 'admin/modem/ttl')
        self.assertEqual(len({o for o, _ in cellular}), len(cellular), 'two Cellular pages with the same order')
        self.assertTrue((app / 'htdocs/luci-static/resources/view/mu300/ttl.js').is_file())
        common = (app / 'htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("method: 'ttl_get'", common)
        self.assertIn("method: 'ttl_set', params: [ 'value' ]", common)
        self.assertIn('callTtlGet: callTtlGet, callTtlSet: callTtlSet', common)
        acl = json.loads((app / 'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        self.assertIn('ttl_get', acl['read']['ubus']['mu300dash'])
        self.assertNotIn('ttl_set', acl['read']['ubus']['mu300dash'])
        self.assertIn('ttl_set', acl['write']['ubus']['mu300dash'])
        self.assertTrue((app / 'root/usr/libexec/unisoc-modem/ttl').stat().st_mode & 0o111)
        # the fast-path note belongs to the nft backend only
        view = (app / 'htdocs/luci-static/resources/view/mu300/ttl.js').read_text()
        self.assertRegex(view, r"if \(st\.backend === 'nft'\)\s*card\.appendChild\([^;]*firewall fast-path is off")

    def test_mainline_rewrites_the_ttl_in_tc(self):
        # mu300-ttl's tc backend (clsact egress, matchall, pedit, csum): the flowtable transmits through egress, so
        # flow offloading stays on. The 5.4 kernel has no pedit: mu300-ttl falls back to nftables there.
        cfg = (TOP / 'upstream' / 'mu300-mainline.config').read_text()
        for k in ('CONFIG_NET_SCH_INGRESS', 'CONFIG_NET_CLS_ACT'):
            self.assertRegex(cfg, rf'(?m)^{k}=y$')
        for k in ('CONFIG_NET_CLS_MATCHALL', 'CONFIG_NET_ACT_PEDIT', 'CONFIG_NET_ACT_CSUM'):
            self.assertRegex(cfg, rf'(?m)^{k}=[ym]$')
        # tc itself: tc-tiny comes with sqm-scripts on OpenWrt
        self.assertIn('sqm-scripts', (TOP / 'openwrt' / 'build-rootfs.sh').read_text())

    def test_every_release_kernel_bundle_has_the_sd_host(self):
        # 5.4 reads the card as well (FINDINGS 31j): its bundle says so, and the release audit fails when any of the
        # four bundles does not (mu300-update refuses such a bundle for a system on the card)
        mr = (TOP / 'tools' / 'make-release.sh').read_text()
        self.assertIn("printf 'sdcard\\nlinux-slot\\ngpt-root\\n' > \"$K/features\"", mr)
        self.assertLess(mr.index('$K/features'), mr.index('tar -C "$K" -czf "$D/mu300-kernel.tar.gz" .'))
        self.assertIn('for a in mu300-kernel mu300-kernel-6.18 mu300-kernel-7.2 mu300-kernel-7.3; do\n'
                      '    tar -xzOf "$D/$a.tar.gz" ./features 2>/dev/null | grep -qx sdcard', mr)
        # and that its init works with Linux on either slot (mu300-update refuses one without it on slot a)
        self.assertIn('    tar -xzOf "$D/$a.tar.gz" ./features 2>/dev/null | grep -qx linux-slot', mr)
        # and that its init finds a system in a GPT partition named mu300root (FINDINGS 178; mu300-update asks)
        self.assertIn('    tar -xzOf "$D/$a.tar.gz" ./features 2>/dev/null | grep -qx gpt-root', mr)

    def test_release_can_be_a_prerelease(self):
        # a release is published as a prerelease first and promoted to "latest" only after a board ran it: the
        # option reaches `gh release create`, and an unknown option stops the script instead of being ignored
        mr = (TOP / 'tools' / 'make-release.sh').read_text()
        self.assertIn('--prerelease) PRERELEASE=--prerelease ;;', mr)
        self.assertIn('--publish) PUBLISH=--publish ;;', mr)
        self.assertRegex(mr, r'\*\) echo "unknown option \$a')
        self.assertRegex(mr, r'gh release create "\$TAG" [^\n]*\$PRERELEASE')
        self.assertNotIn('--latest', mr)

    def test_proc_reads_are_braced(self):
        # `tr < /proc/$pid/cmdline 2>/dev/null` reports a failed redirection (the process just went) before its own
        # 2>/dev/null applies, so "can't open /proc/..." reaches the log: the read is braced, `{ tr < ...; } 2>/dev/null`
        files = [p for p, _ in shell_scripts()]
        files += [p for p in (TOP / 'openwrt' / 'luci-app-mu300' / 'root').rglob('*')
                  if p.is_file() and shebang(p).endswith('sh')]
        bad = re.compile(r'<\s*"?/proc/\$[^\s;|]*\s+2>\s*/dev/null')
        found = []
        for p in sorted(set(files)):
            for n, line in enumerate(p.read_text(errors='replace').splitlines(), 1):
                if bad.search(line):
                    found.append(f'{p.relative_to(TOP)}:{n}')
        self.assertEqual(found, [])

    def test_quiet_console_sysctl_on_both_systems(self):
        # K24: both images carry the same drop-in (systemd-sysctl on Ubuntu, procd's /etc/init.d/sysctl on OpenWrt)
        a = (TOP / 'rootfs' / 'overlay' / 'etc' / 'sysctl.d' / '99-mu300-console.conf').read_text()
        b = (TOP / 'openwrt' / 'overlay' / 'etc' / 'sysctl.d' / '99-mu300-console.conf').read_text()
        self.assertEqual(a, b)
        self.assertRegex(a, r'(?m)^kernel\.printk = 1$')

    def test_modem_control_is_released_early_and_without_a_fixed_wait(self):
        # K22, K23: S09 (leading zero kept, rc.common embeds START verbatim: S9 would sort after S19) and no
        # unconditional sleep before android-vendor-start, which waits for the modem nodes itself
        f = (TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-vendor').read_text()
        self.assertRegex(f, r'(?m)^START=09$')
        self.assertIn('leading zero', f)
        self.assertIn('procd_set_param command /opt/mu300/bin/android-vendor-start\n', f)
        self.assertNotRegex(f, r'sleep 5')
        # nothing else may order itself in front of it
        for s in (TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d').iterdir():
            m = re.search(r'(?m)^START=(\d+)', s.read_text())
            if s.name != 'mu300-vendor' and m:
                self.assertGreater(int(m.group(1)), 9, s.name)

    def test_atd_before_the_network_with_a_second_daemon_on_nr2(self):
        # K16, K17, K18, K21: S19 (the dial at S20 asks the daemon), nr1 and nr2 only, nr2 without a URC channel
        f = (TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-atd').read_text()
        self.assertRegex(f, r'(?m)^START=19$')
        # K20: radio-warmup is the third instance, and it opens no channel: it waits for nr1's daemon and asks it
        # These assertions describe the unchanged default SIM1 path. SIM2 is exercised by test_sim2.
        f = f[:f.index('\nbroker() {')]
        self.assertEqual(re.findall(r'procd_open_instance (\S+)', f), ['atd', 'atd2', 'radio-warmup'])
        warm = f[f.index('procd_open_instance radio-warmup'):]
        warm = warm[:warm.index('procd_close_instance')]
        self.assertIn('until [ -p /run/mu300-at/cmd ]', warm)
        self.assertIn('exec /opt/mu300/bin/mobile-data radio-on', warm)
        # a charging boot (init's marker) keeps the radio off until the Wi-Fi key
        self.assertLess(warm.index('[ -e /run/mu300/charging-boot ] && exit 0'), warm.index('mobile-data radio-on'))
        self.assertNotIn('stty_nr', warm)
        self.assertNotIn('respawn', warm)   # one round per start; netifd's dial and watch retry
        self.assertIn('wait_and_exec "$cmddev" /opt/mu300/bin/mu300-atd', f)
        self.assertIn('wait_and_exec "$datadev" /opt/mu300/bin/mu300-atd', f)
        self.assertIn('procd_set_param env MU300_AT_DEV="$datadev" MU300_AT_DIR=/run/mu300-at2 '
                      'MU300_AT_URC_CHANNELS=\n', f)
        self.assertNotRegex(f, r'stty_nr[3-7]')
        self.assertIn('[ ! -x /usr/libexec/unisoc-modem/lock ] || : > /run/unisoc-modem-early-hook-pending', f)
        u = (TOP / 'rootfs' / 'overlay' / 'etc' / 'systemd' / 'system' / 'mu300-atd2.service').read_text()
        for line in ('Environment=MU300_AT_ROLE=data', 'Environment=MU300_AT_DIR=/run/mu300-at2',
                     'Environment=MU300_AT_URC_CHANNELS=\n', 'ConditionPathExists=|/dev/stty_nr2'):
            self.assertIn(line, u)
        self.assertIn('mu300-atd2.service:multi-user.target', (TOP / 'rootfs' / 'assemble.sh').read_text())
        self.assertIn('mu300-atd2.service:multi-user.target', (TOP / 'arch' / 'build-rootfs.sh').read_text())

    def test_cellular_downlink_in_the_software_flowtable(self):
        # K28, K29: mu300cell reports sipa_eth0 as l3_device only, so fw4 leaves it out of its flowtable and the
        # downlink takes the slow forwarding path. The patch puts it in; it is applied with --fuzz=0 to every OpenWrt
        # system (outside the panel block), so a changed fw4 fails the build instead of shipping without it.
        patch = (TOP / 'openwrt' / 'patches' / 'fw4-sipa-offload.patch').read_text()
        self.assertIn('+++ b/usr/share/ucode/fw4.uc', patch)
        self.assertIn("+\t\t\tif (fs.access('/sys/class/net/sipa_eth0'))", patch)
        self.assertIn("+\t\t\t\tpush(devices, 'sipa_eth0');", patch)
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('-v "$FW4PATCH":/in/fw4-sipa-offload.patch:ro', text)
        apply = 'patch --batch --fuzz=0 -d $R -p1 -i /in/fw4-sipa-offload.patch'
        self.assertIn(apply, text)
        self.assertLess(text.index('cp -a /in/overlay/. $R/'), text.index(apply))
        self.assertLess(text.index(apply), text.index('if [ -d /in/luci-plugin ]; then\n    [ -d /in/luci-overlay ]'))
        # the patch tool is installed after the copy into $R: the build container has it, the image does not
        self.assertLess(text.index('for e in /*; do'), text.index('apk add patch'))
        self.assertLess(text.index(apply), text.index('apk del patch'))
        self.assertLess(text.index('apk del patch'), text.index('apk list --installed | sort'))
        # #95: fw4 reload/restart on 5.4 drops the old flowtable first; applied the same way, after the first patch
        ft = (TOP / 'openwrt' / 'patches' / 'fw4-old-kernel-flowtable.patch').read_text()
        self.assertIn('+++ b/sbin/fw4', ft)
        self.assertIn('-v "$FW4FTPATCH":/in/fw4-old-kernel-flowtable.patch:ro', text)
        apply_ft = 'patch --batch --fuzz=0 -d $R -p1 -i /in/fw4-old-kernel-flowtable.patch'
        self.assertLess(text.index(apply), text.index(apply_ft))
        self.assertLess(text.index(apply_ft), text.index('apk del patch'))
        self.assertIn('grep -q drop_old_flowtable $R/sbin/fw4', text)
        # the function as patched in: only a kernel before 5.13 deletes - the rules that use the flowtable first (it
        # cannot be deleted while in use), then the flowtable - and it never fails the reload
        added = [l[1:] for l in ft.splitlines() if l.startswith('+') and not l.startswith('+++')]
        func = '\n'.join(added[added.index('drop_old_flowtable() {'):added.index('}') + 1])
        nft = r'''nft() {
    echo "nft $*" >> "$LOG"
    case "$*" in
        "list flowtables") printf 'table inet fw4 {\n\tflowtable ft {\n' ;;
        "-a list table inet fw4") printf 'table inet fw4 { # handle 1\n\tchain forward { # handle 2\n\t\tmeta l4proto { tcp, udp } flow add @ft # handle 56\n\t}\n\tchain other { # handle 3\n\t\tct state established accept # handle 7\n\t\tflow offload @ft # handle 9\n\t}\n}\n' ;;
    esac
    return 0
}'''
        for rel, deletes in (('5.4.254', True), ('5.12.19-x', True), ('4.19.1', True), ('5.13.0', False),
                             ('6.18.56', False), ('7.2.10', False)):
            with tempfile.TemporaryDirectory() as d:
                log = Path(d) / 'log'
                log.write_text('')
                r = subprocess.run(['sh', '-c', f'uname() {{ echo {rel}; }}\n{nft}\n{func}\ndrop_old_flowtable; echo rc=$?'],
                                   capture_output=True, text=True, env=dict(os.environ, LOG=str(log)))
                self.assertIn('rc=0', r.stdout, (rel, r.stderr))
                calls = log.read_text().splitlines()
                if not deletes:
                    self.assertEqual(calls, [], rel)
                    continue
                self.assertEqual(calls[-3:], ['nft delete rule inet fw4 forward handle 56',
                                              'nft delete rule inet fw4 other handle 9',
                                              'nft delete flowtable inet fw4 ft'], (rel, calls))
        # software offloading on, hardware off: the SIPA and SC2355 drivers have no nftables hardware offload
        uci = (OPENWRT / 'etc' / 'uci-defaults' / '90-mu300').read_text()
        self.assertIn("uci -q set firewall.@defaults[0].flow_offloading='1'", uci)
        self.assertIn("uci -q set firewall.@defaults[0].flow_offloading_hw='0'", uci)
        self.assertIn('uci commit firewall', uci)

    def test_build_stops_without_the_cellular_protocol(self):
        # K74: without mu300cell.sh netifd has no wan, and the image would boot without mobile data; openwrt-luci's
        # first boot selects relay mode (K30), which runs mu300cell-v6.sh: its overlay must have it, whatever
        # mu300cell.sh says. Checked before any download.
        def run(files, system, extra_env=None):
            with tempfile.TemporaryDirectory() as d:
                top = Path(d)
                (top / 'openwrt').mkdir()
                shutil.copy(TOP / 'openwrt' / 'build-rootfs.sh', top / 'openwrt' / 'build-rootfs.sh')
                for rel, body in files.items():
                    (top / rel).parent.mkdir(parents=True, exist_ok=True)
                    (top / rel).write_text(body)
                env = dict(os.environ, MU300_SYSTEM=system, MU300_INPUTS=d, PATH='/usr/bin:/bin',
                           MU300_LUCI_THEME_APK=str(top / 'no-theme.apk'))
                env.update(extra_env or {})
                return subprocess.run(['sh', str(top / 'openwrt' / 'build-rootfs.sh'), 'x.tar.gz'], env=env,
                                      capture_output=True, text=True, timeout=30)
        cell = 'openwrt/overlay/lib/netifd/proto/mu300cell.sh'
        v6 = 'openwrt/luci-overlay/lib/netifd/proto/mu300cell-v6.sh'
        patch = {'openwrt/patches/fw4-sipa-offload.patch': 'x\n',
                 'openwrt/patches/fw4-old-kernel-flowtable.patch': 'x\n',
                 'openwrt/patches/wifi-scripts-no-dfs.patch': 'x\n',
                 'openwrt/patches/wifi-scripts-wpa2-only.patch': 'x\n'}
        for system in ('openwrt', 'openwrt-luci'):
            with self.subTest(system=system, missing='the WPA2-only patch'):
                r = run({cell: 'x\n', v6: 'x\n', **{k: v for k, v in patch.items() if 'wpa2' not in k}}, system)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn('wifi-scripts-wpa2-only.patch', r.stderr)
            with self.subTest(system=system, missing='mu300cell.sh'):
                r = run(patch, system)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn('mu300cell.sh', r.stderr)
            with self.subTest(system=system, missing='the patch'):
                r = run({cell: 'x\n', v6: 'x\n'}, system)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn('fw4-sipa-offload.patch', r.stderr)
            with self.subTest(system=system, missing='the wifi-scripts patch'):
                r = run({cell: 'x\n', v6: 'x\n', **{k: v for k, v in patch.items() if 'wifi-scripts' not in k}}, system)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn('wifi-scripts-no-dfs.patch', r.stderr)
        # openwrt-luci needs the monitor even when mu300cell.sh does not name it; plain OpenWrt (never relay) does not
        r = run(dict(patch, **{cell: 'x\n'}), 'openwrt-luci')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('mu300cell-v6.sh', r.stderr)
        # with every file present neither check stops the build (it stops later: openwrt-luci at the theme this tree
        # lacks, plain OpenWrt at its download, which a curl stub refuses)
        with tempfile.TemporaryDirectory() as stubs:
            curl = Path(stubs) / 'curl'
            curl.write_text('#!/bin/sh\necho "curl: stub refuses" >&2\nexit 22\n')
            curl.chmod(0o755)
            for system, files in (('openwrt', dict(patch, **{cell: 'x\n'})),
                                  ('openwrt-luci', dict(patch, **{cell: 'x\n', v6: 'x\n'}))):
                r = run(files, system, {'PATH': f'{stubs}:/usr/bin:/bin'})
                self.assertNotEqual(r.returncode, 0, r.stderr)
                self.assertIn('stub refuses' if system == 'openwrt' else 'theme package missing', r.stderr)
                self.assertNotIn('required cellular protocol helper missing', r.stderr)
                self.assertNotIn('fw4-sipa-offload.patch', r.stderr)

    def test_panel_mounts_survive_spaces_in_paths(self):
        # final review minor 5: the panel system's docker mounts were one word-split string; a checkout or a
        # MU300_LUCI_THEME_APK path with a space broke the build. They are the positional parameters now, each path
        # one argument: the build's own `set --` statement is run with spaces in every path
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertNotIn('$LUCI', text)
        m = re.search(r'\n    (set -- -v "\$THEME_APK:.*?:/in/catalogs:ro")\n', text, re.S)
        self.assertTrue(m, 'the luci mounts are not one set -- statement')
        self.assertRegex(text, r'-v "\$REGDB":/in/regdb:ro "\$@" \\\n')
        for sh in shells():
            r = subprocess.run(sh + ['-c', m.group(1) + '\nprintf "%s\\n" "$@"'], capture_output=True, text=True,
                               env=dict(os.environ, THEME_APK='/a b/theme.apk', TOP='/my repo', CAT='/t m/cat'))
            self.assertEqual(r.stdout.splitlines(), [
                '-v', '/a b/theme.apk:/in/luci-theme-aurora.apk:ro', '-v', '/my repo/openwrt/luci-app-mu300:/in/luci-plugin:ro',
                '-v', '/my repo/openwrt/luci-overlay:/in/luci-overlay:ro', '-v', '/t m/cat:/in/catalogs:ro'], sh)

    def test_openwrt_luci_starts_ndp_learn(self):
        # K37: init.d/mu300-ndp is enabled in openwrt-luci's image (it does nothing unless wan is in relay mode) and
        # is executable there, beside the SMS service
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        block = text[text.index('ln -sf ../init.d/unisoc-modem-ui $R/etc/rc.d/'):text.index('apk list --installed | sort')]
        self.assertIn('$R/etc/init.d/mu300-ndp', block)
        self.assertIn('ln -sf ../init.d/mu300-ndp $R/etc/rc.d/S${n}mu300-ndp', block)
        for f in ('etc/init.d/mu300-ndp', 'opt/mu300/bin/ndp-learn', 'lib/netifd/proto/mu300cell-v6.sh'):
            self.assertTrue((LUCI_OVERLAY / f).stat().st_mode & 0o111, f)

    def test_board_json_names_the_usb_lan_port(self):
        # panel page audit: armsr's 99-default_network gave board.json a LAN on eth0, which the MU300 does not have;
        # LuCI's Status > Overview showed an "eth0, no link" port and its ubus status call failed. board.d/05 names
        # usb0 first (board_detect runs board.d in name order, and 99-default_network leaves a network it finds)
        d = OPENWRT / 'etc' / 'board.d'
        self.assertEqual(sorted(p.name for p in d.iterdir()), ['05_mu300_network'])
        self.assertLess('05_mu300_network', '99-default_network')
        f = d / '05_mu300_network'
        self.assertTrue(f.stat().st_mode & 0o111)
        stub = ('board_config_update() { echo update; }\nboard_config_flush() { echo flush; }\n'
                'ucidef_set_interface_lan() { echo "lan $*"; }\n')
        body = f.read_text().replace('. /lib/functions/uci-defaults.sh', stub)
        self.assertNotEqual(body, f.read_text())
        for s in shells():
            r = subprocess.run(s + ['-c', body], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, 'update\nlan usb0\nflush\n'), (s, r.stderr))

    def test_sqm_default_queue_names_the_cellular_uplink(self):
        # panel page audit: the image's /etc/config/sqm (sqm-scripts' default, disabled) named eth1, which the
        # MU300 does not have; the build names sipa_eth0 instead, and stops when a queue still names eth1
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        start = text.index('if [ -f $R/etc/config/sqm ]; then')
        block = text[start:text.index('\nfi\n', start) + 4]
        # a uci stand-in over one file of "sqm.SECTION.OPTION='VALUE'" lines (what uci show prints)
        stub = ('uci() { while [ "${1#-}" != "$1" ]; do [ "$1" = -c ] && shift; shift; done\n'
                '  case $1 in show) cat "$CFG" ;; commit) echo "commit $2" >> "$LOG" ;;\n'
                '  set) k=${2%%=*}; v=${2#*=}; grep -v "^$k=" "$CFG" > "$CFG.n"; echo "$k=\'$v\'" >> "$CFG.n"; '
                'mv "$CFG.n" "$CFG" ;; esac; }\n')
        for s in shells():
            for queues, want in (
                    (["sqm.eth1=queue", "sqm.eth1.enabled='0'", "sqm.eth1.interface='eth1'"],
                     ["sqm.eth1.interface='sipa_eth0'"]),
                    (["sqm.mine=queue", "sqm.mine.interface='br-lan'"], ["sqm.mine.interface='br-lan'"])):
                with tempfile.TemporaryDirectory() as d, self.subTest(shell=s, queues=queues):
                    (Path(d) / 'etc' / 'config').mkdir(parents=True)
                    (Path(d) / 'etc' / 'config' / 'sqm').write_text('x\n')
                    cfg = Path(d) / 'show'
                    cfg.write_text('\n'.join(queues) + '\n')
                    r = subprocess.run(s + ['-c', f'R={d}\n{stub}{block}echo done'], capture_output=True, text=True,
                                       env=dict(os.environ, CFG=str(cfg), LOG=str(Path(d) / 'log')))
                    self.assertEqual((r.returncode, r.stdout), (0, 'done\n'), r.stderr)
                    lines = cfg.read_text().splitlines()
                    for w in want:
                        self.assertIn(w, lines)
                    self.assertNotIn("sqm.eth1.interface='eth1'", lines)
                    self.assertEqual((Path(d) / 'log').read_text(), 'commit sqm\n')

    def test_wifi_power_transitions_are_serialised(self):
        # FINDINGS 31n: the probe's power-off (outside RTNL) ran post_deinit while hostapd's open ran post_init;
        # post_deinit cleared the context post_init had just set, and the next RX interrupt read it as NULL in hard
        # IRQ. Both kernels' drivers hold a mutex across the power transition and drop RX with no context
        wlan = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo'
        patch = (TOP / 'kernel' / 'patches' / 'wlan_combo-wcn-power-serialise.patch').read_text()
        hif = (wlan / 'common' / 'hif.h').read_text()
        iface = (wlan / 'common' / 'iface.c').read_text()
        pcie = (wlan / 'sc2355' / 'pcie.c').read_text()
        self.assertIn('\tstruct mutex power_lock;', hif)
        self.assertIn('+\tstruct mutex power_lock;', patch)
        for text, pre in ((iface, ''), (patch, '+')):
            self.assertIn(pre + '\tmutex_init(&hif->power_lock);', text)
            self.assertIn(pre + '\tmutex_lock(&hif->power_lock);', text)
            self.assertIn(pre + '\tmutex_unlock(&hif->power_lock);', text)
        body = iface[iface.index('int sprd_iface_set_power('):]
        body = body[:body.index('\n}\n')]
        # no return between the lock and the unlock
        self.assertNotIn('return ret;', body[:body.index('mutex_unlock')])
        rx = pcie[pcie.index('static int pcie_rx_handle('):]
        rx = rx[:rx.index('\n}\n')]
        guard = 'rx_mgmt = hif ? (struct rx_mgmt *)READ_ONCE(hif->rx_mgmt) : NULL;'
        self.assertLess(rx.index(guard), rx.index('if (unlikely(!rx_mgmt))'))
        self.assertLess(rx.index('if (unlikely(!rx_mgmt))'), rx.index('rx_mgmt->rx_list'))
        self.assertIn('+\t' + guard, patch)
        # remove powers off before sprd_core_free frees priv (and hif, and the lock in it)
        rm = iface[iface.index('int sprd_iface_remove('):]
        self.assertLess(rm.index('sprd_iface_set_power(hif, false);'), rm.index('sprd_core_free(priv);'))
        for text, pre in ((pcie, ''), (patch, '+')):
            self.assertIn(pre + '\tsmp_store_release(&sc2355_hif.hif, (void *)hif);', text)
            self.assertIn(pre + '\tWRITE_ONCE(sc2355_hif.hif, NULL);', text)

    def test_wifi_per_frame_logs_are_bounded(self):
        # FINDINGS 92 (after kanoqwq's clean-tf-7.2 3cbf37fc): three lines the TX path wrote for every frame - a
        # group frame's LUT lookup (pr_info), a frame for a client that has left (pr_err) and a send that failed
        # (netdev_err) - are debug or rate-limited; the frames are still dropped and freed as before
        wlan = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo'
        for name in ('pcie.c', 'sdio.c'):
            text = (wlan / 'sc2355' / name).read_text()
            self.assertNotIn('pr_info("%s, %d, group lut_index=%d\\n"', text, name)
            self.assertNotIn('pr_err("%s, %d, sta disconn, no data tx!"', text, name)
            self.assertIn('pr_debug_ratelimited("%s, %d, group lut_index=%d\\n"', text, name)
            self.assertIn('\t\tpr_debug_ratelimited("%s, %d, sta disconn, no data tx!\\n", __func__, __LINE__);\n'
                          '\t\treturn -EPERM;', text, name)
        iface = (wlan / 'common' / 'iface.c').read_text()
        body = iface[iface.index('static netdev_tx_t iface_start_xmit('):]
        body = body[:body.index('\n}\n')]
        self.assertIn('\t\tif (net_ratelimit())\n\t\t\tnetdev_err(ndev, "%s drop msg due to TX Err\\n", __func__);', body)
        after = body[body.index('drop msg due to TX Err'):]
        self.assertLess(after.index('dev_kfree_skb(skb);'), after.index('return NETDEV_TX_OK;'))
        self.assertLess(after.index('sprd_chip_free_msg(&vif->priv->chip, msg);'), after.index('return NETDEV_TX_OK;'))

    def test_wifi_notify_init_unwinds_and_registers_the_reset_notifier_last(self):
        # kanoqwq/mu300-linux@23361ce: a failed notify_init left the WCN reset notifier registered on a freed priv
        iface = (TOP / 'upstream' / 'modules' / 'sprd_wlan_combo' / 'common' / 'iface.c').read_text()
        body = iface[iface.index('static int iface_notify_init('):]
        body = body[:body.index('\n}\n')]
        reg = body.index('atomic_notifier_chain_register(&wcn_reset_notifier_list,')
        for step in ('misc_register(&wlan_misc_device);', 'register_inetaddr_notifier(&iface_inetaddr_cb);',
                     'register_inet6addr_notifier(&iface_inet6addr_cb);'):
            self.assertLess(body.index(step), reg, step)
        # nothing returns between the first registration and the end without undoing what it registered
        self.assertEqual(body.count('return ret;'), 2)
        self.assertIn('\t\treturn ret;\n\t}\n\n\tret = register_inetaddr_notifier', body)
        for label in ('goto err_misc;', 'goto err_inet;', 'err_inet:\n\tunregister_inetaddr_notifier',
                      'err_misc:\n\tmisc_deregister(&wlan_misc_device);\n\treturn ret;'):
            self.assertIn(label, body)

    def test_wifi_rx_teardown_stops_the_refill_producers_first(self):
        # kanoqwq/mu300-linux@23361ce: the RX work queues refills on sprd_work and a refill arms the timed retry;
        # rx_deinit drained them in an order that let one re-arm the other over a freed rx_mgmt
        wlan = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo' / 'sc2355'
        self.assertIn('\tbool stopping;\n};', (wlan / 'rx.h').read_text())
        rx = (wlan / 'rx.c').read_text()

        def body(sig):
            b = rx[rx.index(sig):]
            return b[:b.index('\n}\n')]
        q = body('void sc2355_queue_rx_buff_work(')
        self.assertLess(q.index('READ_ONCE(rx_mgmt->stopping)'), q.index('sprd_alloc_work(0)'))
        self.assertIn('if (!READ_ONCE(rx_mgmt->stopping))\n\t\tsc2355_queue_rx_buff_work(', body('static void rx_refill_retry('))
        f = body('int sc2355_mm_fill_buffer(')
        # read under refill_lock since the bus-completion resume (FINDINGS 195)
        self.assertLess(f.index('if (rx_mgmt->stopping) {'), f.index('atomic_xchg(&mm_entry->alloc_num, 0)'))
        d = body('int sc2355_rx_deinit(')
        order = ['WRITE_ONCE(rx_mgmt->stopping, true);', 'cancel_delayed_work_sync(&rx_mgmt->refill_retry);',
                 'flush_workqueue(rx_mgmt->rx_queue);', 'flush_workqueue(rx_mgmt->rx_net_workq);',
                 'sprd_clean_work(hif->priv);']
        pos = [d.index(s) for s in order]
        self.assertEqual(pos, sorted(pos))
        self.assertEqual(d.count('cancel_delayed_work_sync(&rx_mgmt->refill_retry);'), 2)
        self.assertLess(d.index('sprd_clean_work(hif->priv);'), d.rindex('cancel_delayed_work_sync(&rx_mgmt->refill_retry);'))
        self.assertLess(d.rindex('cancel_delayed_work_sync'), d.index('kfree(rx_mgmt);'))

    def test_wifi_remove_tears_the_driver_down(self):
        # issue #146: the port's platform remove only logged (the vendor's calls sprd_iface_remove), so rmmod freed
        # the module with the recovery watchdog's delayed work still armed: __run_timers panicked within a second
        wlan = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo'
        drv = (wlan / 'common' / 'sprd_wlan.c').read_text()
        rm = drv[drv.index('static void sprd_wlan_remove('):]
        rm = rm[:rm.index('\n}\n')]
        self.assertIn('\tif (p_match_data->hw_type == SPRD_HW_SC2355_PCIE)\n\t\tsprd_iface_remove(pdev);', rm)
        iface = (wlan / 'common' / 'iface.c').read_text()
        body = iface[iface.index('int sprd_iface_remove('):]
        body = body[:body.index('\n}\n')]
        # the watchdog stops first, and a failed power-on does not leave the teardown undone
        self.assertLess(body.index('iface_wd_stop();'), body.index('sprd_iface_set_power(hif, true);'))
        self.assertEqual(body.count('\n\treturn '), 1)
        self.assertIn('\tif (!ret)\n\t\tsprd_iface_set_power(hif, false);', body)
        wd = iface[iface.index('static void iface_wd_stop(void)'):]
        wd = wd[:wd.index('\n}\n')]
        self.assertIn('cancel_delayed_work_sync(&iface_wd.tick);', wd)
        # the timers that live in priv are shut down where priv is freed
        core_free = (wlan / 'common' / 'cfg80211.c').read_text()
        core_free = core_free[core_free.index('void sprd_core_free('):]
        core_free = core_free[:core_free.index('\n}\n')]
        self.assertLess(core_free.index('timer_shutdown_sync(&priv->scan_timer);'), core_free.index('wiphy_free(wiphy);'))
        ack = (wlan / 'sc2355' / 'tcp_ack.c').read_text()
        ack = ack[ack.index('void sc2355_tcp_ack_deinit('):]
        ack = ack[:ack.index('\n}\n')]
        self.assertLess(ack.index('sprd_chip_drop_tcp_msg('), ack.index('timer_shutdown_sync(&ack_m->ack_info[i].timer);'))

    def test_wifi_tx_backpressure_is_not_logged_as_an_error(self):
        # kanoqwq/mu300-linux@3386928: a full TX ring is flow control, hit on every scheduling pass under load;
        # the vendor printed two pr_err lines for it each time
        wlan = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo' / 'sc2355'
        pcie = (wlan / 'pcie.c').read_text()
        fc = pcie[pcie.index('int sc2355_pcie_fc_test_send_num('):]
        fc = fc[:fc.index('\n}\n')]
        self.assertNotIn('pr_err(', fc)
        self.assertIn('pr_debug_ratelimited("%s,%d free_num=%d, data_num=%d\\n"', fc)
        tx = (wlan / 'tx.c').read_text()
        self.assertNotIn('pr_err("%s, %d: _fc_ no credit!\\n"', tx)
        self.assertIn('pr_debug_ratelimited("%s, %d: _fc_ no credit!\\n"', tx)

    def test_wifi_ap_get_station_answers_for_its_stations_only(self):
        # FINDINGS 180 (after kanoqwq's f59a296): on an access point get_station handed out the interface's own
        # counters and the station side's GET_STATION for any address; now -ENOENT unless the address is one of this
        # interface's peers, and nothing filled in for one (the firmware has no per-client figures); dump_station
        # refuses a negative index; get_channel answers for link 0 only. The 5.4 patch adds the same code
        mainline = (TOP / 'upstream' / 'modules' / 'sprd_wlan_combo' / 'common' / 'cfg80211.c').read_text()
        patch = (TOP / 'kernel' / 'patches' / 'wlan_combo-ap-stats-scope.patch').read_text()
        lines = ('static bool sprd_ap_has_peer(struct sprd_vif *vif, const u8 *mac)',
                 '\tif (!is_valid_ether_addr(mac) || ether_addr_equal(mac, vif->ndev->dev_addr))',
                 '\t\tif (READ_ONCE(hif->peer_entry[i].ctx_id) == vif->ctx_id &&',
                 '\tif (vif->wdev.iftype == NL80211_IFTYPE_AP || vif->wdev.iftype == NL80211_IFTYPE_P2P_GO) {',
                 '\t\tif (!sprd_ap_has_peer(vif, mac))\n\t\t\treturn -ENOENT;\n\t\tsinfo->filled = 0;\n\t\treturn 0;',
                 '\tif (idx < 0 || (vif->wdev.iftype != NL80211_IFTYPE_AP && vif->wdev.iftype != NL80211_IFTYPE_P2P_GO))',
                 '\tif (link_id || (wdev->iftype != NL80211_IFTYPE_AP && wdev->iftype != NL80211_IFTYPE_P2P_GO) ||',
                 '\t*chandef = wdev->links[0].ap.chandef;')
        added = '\n'.join(l[1:] if l[:1] in '+ ' else l for l in patch.splitlines())
        for s in lines:
            self.assertIn(s, mainline)
            self.assertIn(s, added)
        self.assertNotIn('links[link_id]', mainline)
        # the AP branch comes before anything of the station side's is filled in
        f = mainline[mainline.index('int sprd_cfg80211_get_station('):]
        self.assertLess(f.index('sprd_ap_has_peer(vif, mac)'), f.index('sinfo->tx_bytes = ndev->stats.tx_bytes;'))

    def test_wifi_routine_events_are_not_logged_as_errors(self):
        # issue #140: the vendor printed per-poll survey data, the multicast default of the peer lookup, the
        # short-frame copy path, reordering drops, dup-ACK skips, EAPOL sends and DELBA for an unknown session with
        # pr_err/netdev_err. Both drivers trace them at debug level, the 5.4 one through its patch; the real errors
        # next to them ("too short", "alloc skb fail", BAR for an unknown session) keep their level
        wlan = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo' / 'sc2355'
        patch = (TOP / 'kernel' / 'patches' / 'wlan_combo-routine-not-err.patch').read_text()
        src = {f: (wlan / f).read_text() for f in ('acs.c', 'pcie.c', 'sdio.c', 'mm.c', 'defrag.c', 'tx.c', 'reorder.c',
                                                   'tcp_ack.c')}
        # all of acs.c's error lines were dump_survey's trace
        self.assertNotIn('netdev_err(', src['acs.c'])
        self.assertIn('-\t\t\tnetdev_err(vif->ndev, "Not AP mode, exit %s!\\n",', patch)
        self.assertEqual(patch.count('+\t\t\t\t\tnetdev_dbg(vif->ndev,'), 2)
        lowered = (('pcie.c', '\t\tpr_debug("%s,%d,bssid not found, multicast?\\n"', 3),
                   ('sdio.c', '\t\tpr_debug("%s,%d,bssid not found, multicast?\\n"', 3),
                   ('mm.c', '\t\t\tpr_debug_ratelimited("%s: data len is %d, skb need %d\\n",', 1),
                   ('defrag.c', '\t\tpr_debug("%s:msdu len %d\\n", __func__, node->msdu_len);', 1),
                   ('tx.c', '\t\tpr_debug("send %s frame by CMD_TX_DATA\\n",', 1),
                   ('reorder.c', '\t\tpr_debug_ratelimited("%s: seq_num: %d is less than win_start: %d\\n",', 1),
                   ('reorder.c', '\t\tpr_debug_ratelimited("%s: kfree skb %d", __func__, ret);', 1),
                   ('reorder.c', '\t\tpr_debug("%s: NOT FOUND sta_lut_index: %d, tid: %d\\n",', 1),
                   ('tcp_ack.c', '\t\t\tpr_debug_ratelimited("%s before abnormal ack: %d, %d\\n",', 1))
        def hunks(f):  # the patch's section for one file
            s = patch[patch.index('--- a/sc2355/' + f + '\n'):]
            end = s.find('\n--- a/', 1)
            return s if end < 0 else s[:end]
        for f, line, n in lowered:
            with self.subTest(file=f, line=line):
                self.assertEqual(src[f].count(line), n)
                self.assertEqual(hunks(f).count('+' + line), n)
                self.assertEqual(hunks(f).count('-' + line.replace('pr_debug_ratelimited(', 'pr_err(')
                                                .replace('pr_debug(', 'pr_err(')), n)
        # the errors next to them stay
        self.assertIn('pr_err_ratelimited("%s: data len is %d, too short\\n"', src['mm.c'])
        self.assertIn('pr_err_ratelimited("%s: alloc skb fail\\n"', src['mm.c'])
        bar = src['reorder.c'][src['reorder.c'].index('static void reorder_wlan_bar_event('):]
        self.assertIn('pr_err("%s: NOT FOUND sta_lut_index: %d, tid: %d\\n"', bar[:bar.index('\n}\n')])
        for kept in ('too short', 'alloc skb fail'):
            self.assertNotIn(kept, patch[patch.index('--- a/'):])



class ApkRetry(unittest.TestCase):
    """openwrt/build-rootfs.sh: the feed's packages are fetched while the image is built, and a download that broke
    off failed the image; update, upgrade and add are tried three times (FINDINGS 66)."""

    def setUp(self):
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.block = re.search(r'# --- apk-retry begin\n(.*?)# --- apk-retry end\n', text, re.S).group(1)
        # the function stands before the first use, inside the one single-quoted script of the container
        self.assertLess(text.index('# --- apk-retry end'), text.index('\napk update >/dev/null'))
        self.assertNotIn("'", self.block)
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-test-'))
        stub = self.tmp / 'apk'
        # fails FAIL times (a counter in a file), then succeeds; records every call
        stub.write_text('#!/bin/sh\necho "$*" >> "$LOG"\nn=$(cat "$COUNT" 2>/dev/null || echo 0)\n'
                        '[ "$n" -lt "$FAIL" ] || { echo "ok $1"; exit 0; }\necho $((n + 1)) > "$COUNT"\nexit 1\n')
        stub.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_apk(self, shell, args, fail):
        for f in ('log', 'count'):
            (self.tmp / f).unlink(missing_ok=True)
        env = dict(os.environ, PATH=f'{self.tmp}{os.pathsep}{os.environ["PATH"]}', LOG=str(self.tmp / 'log'),
                   COUNT=str(self.tmp / 'count'), FAIL=str(fail), MU300_APK_RETRY_WAIT='0')
        # as the build runs it: sh -eu, the output thrown away
        r = subprocess.run(shell + ['-eu', '-c', self.block + f'apk {args} >/dev/null\necho done'], env=env,
                           capture_output=True, text=True, timeout=30)
        log = (self.tmp / 'log').read_text().splitlines() if (self.tmp / 'log').exists() else []
        return r, log

    def test_a_download_that_breaks_off_is_tried_again(self):
        for shell in shells():
            for cmd in ('update', 'upgrade', 'add wpad-basic-mbedtls iw', 'add --allow-untrusted /in/x.apk'):
                for fail, tries, ok in ((0, 1, True), (1, 2, True), (2, 3, True), (3, 3, False)):
                    r, log = self.run_apk(shell, cmd, fail)
                    self.assertEqual(log, [cmd] * tries, (shell, cmd, fail))     # the same arguments every time
                    self.assertEqual((r.returncode == 0, 'done' in r.stdout), (ok, ok), (shell, cmd, fail, r.stderr))
                    self.assertEqual(r.stderr.count('trying again'), tries - 1)

    def test_anything_else_runs_once(self):
        for shell in shells():
            r, log = self.run_apk(shell, 'del procd-ujail', 1)
            self.assertEqual((log, r.returncode), (['del procd-ujail'], 1), shell)
            self.assertNotIn('done', r.stdout)
            # "apk del ... || true", as the build has it
            env = dict(os.environ, PATH=f'{self.tmp}{os.pathsep}{os.environ["PATH"]}', LOG=str(self.tmp / 'log'),
                       COUNT=str(self.tmp / 'count'), FAIL='9')
            r = subprocess.run(shell + ['-eu', '-c', self.block + 'apk del x >/dev/null 2>&1 || true\necho done'],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'done'), r.stderr)



class SelfUpdateCopies(unittest.TestCase):
    def test_a_git_worktree_is_a_git_copy(self):
        # in a worktree .git is a file: taken for a downloaded copy, the uncommitted work in it was overwritten with
        # GitHub's main by the installer's self-update (2026-10-17, FINDINGS 179); install.ps1's Test-Path sees both
        su = (TOP / 'tools' / 'self-update.sh').read_text()
        self.assertIn('if [ -e "$TOP/.git" ] && command -v git >/dev/null; then', su)
        self.assertNotIn('-d "$TOP/.git"', su)
        self.assertIn("(Test-Path (Join-Path $Top '.git'))", (TOP / 'install.ps1').read_text())


class Ps1FunctionOrder(unittest.TestCase):
    """PowerShell knows a function only once the script has run past its definition: a top-level line of
    install.ps1 that calls one defined further down (or that calls one whose body calls one) stops the installer
    with 'The term ... is not recognized'. The make-room resume path did that on Windows with ResumeFacts and
    GptDump (#357)."""

    @staticmethod
    def code(line):
        line = re.sub(r"'[^']*'", "''", line)
        line = re.sub(r'"[^"]*"', '""', line)
        return line.split('#', 1)[0]

    def test_top_level_calls_come_after_the_definitions(self):
        text = (TOP / 'install.ps1').read_text()
        lines = re.sub(r'<#.*?#>', lambda m: '\n' * m.group(0).count('\n'), text, flags=re.S).splitlines()
        defs, bodies, top, i = {}, {}, [], 0
        while i < len(lines):
            m = re.match(r'function\s+([A-Za-z][\w-]*)', lines[i])
            if not m:
                top.append((i + 1, lines[i]))
                i += 1
                continue
            name, start = m.group(1), i
            while i < len(lines) and not re.match(r'}\s*$', lines[i]):
                i += 1
            defs[name] = start + 1
            bodies[name] = '\n'.join(self.code(x) for x in lines[start + 1:i])
            i += 1
        self.assertIn('ResumeFacts', defs)
        word = {n: re.compile(r'(?<![\w$.-])' + re.escape(n) + r'(?![\w-])') for n in defs}
        calls = {n: {c for c in defs if c != n and word[c].search(bodies[n])} for n in defs}
        late = []
        for no, line in top:
            for n in defs:
                if not word[n].search(self.code(line)):
                    continue
                seen, todo = set(), [n]
                while todo:
                    f = todo.pop()
                    if f in seen:
                        continue
                    seen.add(f)
                    if defs[f] > no:
                        late.append('line %d calls %s, defined at line %d' % (no, f, defs[f]))
                    todo.extend(calls[f])
        self.assertEqual(late, [])


if __name__ == '__main__':
    unittest.main()
