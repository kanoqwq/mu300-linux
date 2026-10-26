"""The U30 Air's firmware SAE offload is disabled, in every layer (FINDINGS 80, docs/U30AIR-SAE.md): the hotspot is
WPA2-PSK only on every device (F50, U30 Air, V50: the SC2355).

* the driver (mainline tree and the 5.4 patch): the firmware's PMK record never reaches cfg80211 - the strip function
  compiled from the source (with UBSan when MU300_TEST_UBSAN=1) against records, malformed lists and random input;
  the vendor command that gave the firmware the SAE password is refused;
* no hostapd with the import is built or installed, and the build refuses MU300_SAE_APK_DIR;
* the wifi-scripts refuse an SAE access point (static, and in the OpenWrt feed's scripts with MU300_WRT_IMAGE);
* openwrt-wifi-wpa2-only: a hotspot left on SAE becomes WPA2-PSK (CCMP) with the same password at boot, or goes off
  (never open), with one log line and the notice for LuCI; the notice module itself under Node;
* mu300-api and Ubuntu's hotspot-start never put SAE on the hotspot."""
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

WLAN = TOP / 'upstream/modules/sprd_wlan_combo'
PATCH54 = TOP / 'kernel/patches/wlan_combo-softap-no-sae.patch'
WIFI_PATCH = TOP / 'openwrt/patches/wifi-scripts-wpa2-only.patch'
MIGRATE = BIN / 'openwrt-wifi-wpa2-only'
NOTICE_JS = TOP / 'openwrt/overlay/www/luci-static/resources/view/status/include/05_mu300_wpa2.js'
RECORD_PREFIX = bytes([0xdd, 0x34, 0x40, 0x45, 0xda, 0x04])


def c_function(source, name):
    """the text of C function NAME in SOURCE, from its 'static' line to its closing brace"""
    start = source.index('static u16 ' + name + '(')
    i = source.index('{', start)
    depth = 0
    while True:
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1


def added_lines(patch, path):
    """the '+' lines of the hunks of PATH in a unified diff"""
    out, on = [], False
    for line in patch.splitlines():
        if line.startswith('+++ '):
            on = line[4:].strip() == 'b/' + path
            continue
        if line.startswith('--- '):
            continue
        if on and line.startswith('+'):
            out.append(line[1:])
    return '\n'.join(out) + '\n'


def strip_model(ies):
    """what the driver must leave: the well-formed elements, in order, without any dd ..40:45:da:04 element"""
    out, i = b'', 0
    while i + 2 <= len(ies):
        n = ies[i + 1]
        if i + 2 + n > len(ies):
            break
        e = ies[i:i + 2 + n]
        if not (e[0] == 221 and n >= 4 and e[2:6] == RECORD_PREFIX[2:]):
            out += e
        i += 2 + n
    return out


class DriverStripsTheRecord(unittest.TestCase):
    """common/report.c: mu300_strip_sae_pmk_record before cfg80211_new_sta, compiled from the source."""

    def test_mainline_and_54_patch_carry_the_same_function(self):
        up = c_function((WLAN / 'common/report.c').read_text(), 'mu300_strip_sae_pmk_record')
        p54 = c_function(added_lines(PATCH54.read_text(), 'common/report.c'), 'mu300_strip_sae_pmk_record')
        self.assertEqual(up, p54)

    def test_softap_report_strips_before_cfg80211(self):
        src = (WLAN / 'common/report.c').read_text()
        body = src[src.index('void sprd_report_softap('):src.index('EXPORT_SYMBOL(sprd_report_softap)')]
        self.assertLess(body.index('mu300_strip_sae_pmk_record(req_ie, req_ie_len)'), body.index('sinfo.assoc_req_ies = req_ie'))
        self.assertLess(body.index('sinfo.assoc_req_ies = req_ie'), body.index('cfg80211_new_sta('))
        for text in (src, PATCH54.read_text()):
            # the bytes of the record are never printed
            self.assertNotIn('hex_dump', body)
            self.assertIn('0x40, 0x45, 0xda, 0x04', text)

    def test_firmware_never_gets_an_sae_password(self):
        for text in ((WLAN / 'sc2355/vendor.c').read_text(), added_lines(PATCH54.read_text(), 'sc2355/vendor.c')):
            fn = text[text.index('static int vendor_set_sae_password('):] if 'static int vendor_set_sae_password(' in text else text
            ret = fn.index('return -EOPNOTSUPP;')
            self.assertNotIn('nla_for_each_attr', fn[:ret])
            self.assertNotIn('send_cmd_recv_rsp', fn[:ret])
        # the 5.4 patch applies after wlan_combo-sae-private-logs.patch (the build's glob order), which edits vendor.c
        names = sorted(p.name for p in (TOP / 'kernel/patches').glob('wlan_combo-*.patch'))
        self.assertLess(names.index('wlan_combo-sae-private-logs.patch'), names.index(PATCH54.name))

    def compile(self):
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('no native C compiler')
        work = Path(tempfile.mkdtemp(prefix='mu300-strip-'))
        self.addCleanup(shutil.rmtree, work, True)
        fn = c_function((WLAN / 'common/report.c').read_text(), 'mu300_strip_sae_pmk_record')
        (work / 't.c').write_text('''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
typedef unsigned short u16;
''' + fn + '''
int main(int argc, char **argv)
{
	static u8 buf[65536];
	size_t n = fread(buf, 1, sizeof(buf), stdin);
	u16 len = (u16)n, out = mu300_strip_sae_pmk_record(buf, len);
	(void)argc; (void)argv;
	fwrite(buf, 1, out, stdout);
	return 0;
}
''')
        flags = ['-std=c99', '-Wall', '-Wextra', '-Werror', '-O1']
        if os.environ.get('MU300_TEST_UBSAN') == '1':
            flags += ['-fsanitize=undefined,address', '-fno-sanitize-recover=all']
        r = subprocess.run([cc] + flags + [str(work / 't.c'), '-o', str(work / 't')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return work / 't'

    def test_strip(self):
        exe = self.compile()

        def run(ies):
            r = subprocess.run([str(exe)], input=ies, capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

        ssid = bytes([0, 4]) + b'MU30'
        rsn = bytes([48, 20, 1, 0, 0, 0x0f, 0xac, 4, 1, 0, 0, 0x0f, 0xac, 4, 1, 0, 0, 0x0f, 0xac, 2, 0, 0])
        wmm = bytes([221, 7, 0, 0x50, 0xf2, 2, 0, 1, 0])
        record = RECORD_PREFIX + bytes(range(48))
        other_vendor = bytes([221, 6, 0x40, 0x45, 0xda, 0x05, 1, 2])
        cases = {
            'no record': (ssid + rsn + wmm, ssid + rsn + wmm),
            'empty': (b'', b''),
            'firmware record at the end': (ssid + rsn + record, ssid + rsn),
            'a client record in the middle': (ssid + record + rsn + wmm, ssid + rsn + wmm),
            'two records': (record + ssid + record, ssid),
            'prefix only': (ssid + bytes([221, 4, 0x40, 0x45, 0xda, 0x04]), ssid),
            'any length with the prefix': (bytes([221, 10, 0x40, 0x45, 0xda, 0x04]) + bytes(6) + rsn, rsn),
            'another vendor type stays': (ssid + other_vendor, ssid + other_vendor),
            'too short for the prefix stays': (bytes([221, 3, 0x40, 0x45, 0xda]), bytes([221, 3, 0x40, 0x45, 0xda])),
            'a truncated record': (ssid + record[:30], ssid),
            'a record behind a malformed element': (ssid + bytes([7, 200]) + record, ssid),
            'one stray byte': (ssid + b'\xdd', ssid),
        }
        for name, (ies, want) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(run(ies), want)
                self.assertEqual(strip_model(ies), want)
        rnd = random.Random(80)
        for _ in range(400):
            parts = []
            for _ in range(rnd.randrange(0, 12)):
                k = rnd.randrange(5)
                if k == 0:
                    parts.append(record)
                elif k == 1:
                    parts.append(bytes([221, 4 + rnd.randrange(60)]) + RECORD_PREFIX[2:] + bytes(rnd.randrange(256) for _ in range(60)))
                else:
                    n = rnd.randrange(40)
                    parts.append(bytes([rnd.randrange(256), n]) + bytes(rnd.randrange(256) for _ in range(n)))
            ies = b''.join(parts)
            if rnd.random() < 0.3:
                ies = ies[:rnd.randrange(len(ies) + 1)]
            out = run(ies)
            self.assertEqual(out, strip_model(ies))
            self.assertEqual(strip_model(out), out)       # well-formed, nothing left to strip


class DriverPrivacy(unittest.TestCase):
    """the driver never logs the passphrase, a station key or raw command/event payloads (kept from the offload work)"""

    def body(self, path, name):
        source = (TOP / 'upstream/modules/sprd_wlan_combo' / path).read_text()
        start = source.index('{', source.index(name + '('))
        depth = 1
        end = start + 1
        while depth:
            if source[end] == '{': depth += 1
            if source[end] == '}': depth -= 1
            end += 1
        return source[start:end]

    def test_transport_command_payloads_never_reach_hex_dump(self):
        for path, name in [('sc2355/pcie.c', 'pcie_tx_one'),
                           ('sc2355/sdio.c', 'sdio_tx_one'),
                           ('sc2355/cmdevt.c', 'cmdevt_send_cmd'),
                           ('sc2355/cmdevt.c', 'sc2355_rx_rsp_process')]:
            with self.subTest(path=path, name=name):
                self.assertNotIn('hex_dump', self.body(path, name))

    def test_transport_rx_cannot_dump_a_new_station_pmk(self):
        for path, name in [('sc2355/pcie.c', 'sc2355_pcie_rx_work_queue'),
                           ('sc2355/sdio.c', 'sc2355_rx_work_queue')]:
            with self.subTest(path=path):
                self.assertNotIn('hex_dump', self.body(path, name))

    def test_station_key_is_not_logged_as_an_unbounded_string(self):
        source = (TOP / 'upstream/modules/sprd_wlan_combo/common/cfg80211.c').read_text()
        self.assertNotIn('"PSK %s', source)
        self.assertIn('memcpy(con.psk, sme->key, sme->key_len)', source)


class NoImportIsBuilt(ShellTest):
    def test_the_hostapd_patch_and_its_recipe_are_gone(self):
        self.assertFalse((TOP / 'openwrt/patches/hostapd').exists())
        self.assertFalse((TOP / 'openwrt/build-u30-sae.sh').exists())
        self.assertFalse((BIN / 'openwrt-wifi-sae-guard').exists())
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        for gone in ('/in/u30-sae', 'wpad-basic-openssl', 'SAE_APKS'):
            self.assertNotIn(gone, build)
        self.assertIn('apk add wpad-basic-mbedtls wifi-scripts', build)

    def test_build_refuses_the_sae_apk_dir(self):
        r = self.script(['sh'], TOP / 'openwrt/build-rootfs.sh', 'x.tar.gz', MU300_SAE_APK_DIR=self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn('SAE offload is disabled', r.stderr)

    def test_no_code_turns_it_on(self):
        """u30_sae_offload is named only where it is refused or removed (and in the docs and tests)"""
        allowed = {'openwrt/patches/wifi-scripts-wpa2-only.patch', 'rootfs/overlay/opt/mu300/bin/openwrt-wifi-wpa2-only',
                   'rootfs/overlay/opt/mu300/bin/mu300-api'}
        out = subprocess.run(['git', '-C', str(TOP), 'grep', '-l', 'u30_sae_offload', '--', '.', ':!docs', ':!tests',
                              ':!wiki', ':!site', ':!README.md'], capture_output=True, text=True)
        if out.returncode not in (0, 1):
            self.skipTest('not a git checkout')
        self.assertEqual(set(out.stdout.split()) - allowed, set())


class WifiScriptsRefuseSae(unittest.TestCase):
    def test_patch(self):
        p = WIFI_PATCH.read_text()
        self.assertTrue(p.startswith('--- a/usr/share/ucode/wifi/hostapd.uc\n+++ b/usr/share/ucode/wifi/hostapd.uc\n'))
        self.assertIn("[ 'sae', 'psk3', 'sae-mixed', 'psk3-mixed', 'sae-compat' ]", p)
        self.assertIn("'u30_sae_offload*'", p)
        self.assertIn('+\t\tif (mu300_sae_refused(k, interface.config))\n+\t\t\tcontinue;', p)
        self.assertIn('WPA2-PSK only', p)
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('-v "$WPA2PATCH":/in/wifi-scripts-wpa2-only.patch:ro', build)
        # after the DFS patch, whose log import it uses, and checked like it
        self.assertLess(build.index('-i /in/wifi-scripts-no-dfs.patch'), build.index('-i /in/wifi-scripts-wpa2-only.patch'))
        self.assertIn('grep -q "mu300_sae_refused(k, interface.config)" $R/usr/share/ucode/wifi/hostapd.uc ||', build)

    @unittest.skipUnless(os.environ.get('MU300_WRT_IMAGE') and shutil.which('docker'),
                         'MU300_WRT_IMAGE (an OpenWrt 25.12 arm64 docker image) not set')
    def test_feed_scripts_start_wpa2_and_refuse_sae(self):
        import test_wifi_channels as channels
        fake = channels.WifiScriptsInOpenWrt
        cases = {'psk2': {}, 'psk2-ccmp': {'encryption': 'psk2+ccmp'}, 'sae': {'encryption': 'sae'},
                 'sae-mixed': {'encryption': 'sae-mixed'}, 'sae-compat': {'encryption': 'sae-compat'},
                 'psk3': {'encryption': 'psk3'},
                 'offload': {'encryption': 'psk2', 'hostapd_bss_options': ['u30_sae_offload=1']}}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'cases').mkdir()
            (d / 'out').mkdir()
            for name, iface in cases.items():
                data = fake.data(36, 'VHT80')
                data['interfaces']['ap0']['config'].update(iface)
                (d / 'cases' / f'{name}.wiphy.json').write_text(json.dumps(fake.wiphy(False, {})))
                (d / 'cases' / f'{name}.data.json').write_text(json.dumps(data))
            script = r"""
mkdir -p /var/lock /var/run /tmp /fake/wifi
apk update >/dev/null && apk add wifi-scripts patch >/dev/null 2>&1 || exit 1
patch --batch --fuzz=0 -p1 -d / -i /in/wifi-scripts-no-dfs.patch || exit 1
patch --batch --fuzz=0 -p1 -d / -i /in/wifi-scripts-wpa2-only.patch || exit 1
sed -e "s/^export function wiphy_info(phy) {/function nl_wiphy_info(phy) {/" /usr/share/ucode/wifi/common.uc > /fake/wifi/common.uc
echo "export function wiphy_info(phy) { return json(fs.readfile(getenv(\"FAKE_WIPHY\"))); };" >> /fake/wifi/common.uc
for f in /t/cases/*.data.json; do
    n=$(basename "$f" .data.json)
    rm -f /var/run/hostapd-phy0.conf
    FAKE_DATA=$f FAKE_WIPHY=/t/cases/$n.wiphy.json FAKE_CC=CN \
        ucode -L "/fake/*.uc" -L "/usr/share/ucode/*.uc" /fx/run.uc > /t/out/$n.conf 2>&1 || echo "rc=$?" >> /t/out/$n.conf
done
"""
            r = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
                                '-v', f'{WIFI_PATCH.parent}:/in:ro', '-v', f'{channels.FIXTURES}:/fx:ro', '-v', f'{d}:/t',
                                os.environ['MU300_WRT_IMAGE'], '/bin/sh', '-c', script],
                               capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name in cases:
                text = (d / 'out' / f'{name}.conf').read_text()
                with self.subTest(case=name):
                    self.assertNotIn('rc=', text)
                    conf = dict(l.split('=', 1) for l in text.splitlines() if '=' in l and not l.startswith('#'))
                    if name.startswith('psk2'):
                        self.assertEqual((conf['wpa_key_mgmt'], conf.get('wpa_pairwise', conf.get('rsn_pairwise'))), ('WPA-PSK', 'CCMP'), text)
                        self.assertNotIn('refused', text, text)
                        self.assertIn('ssid2="MU300-TEST"', text)
                    else:
                        self.assertIn("wifi-scripts: ap0: encryption '", text)
                        self.assertIn('WPA2-PSK only', text)
                        self.assertNotIn('interface=wlan0', text)
                        self.assertNotIn('wpa_passphrase', text)
                        self.assertNotIn('wpa_key_mgmt', text)
                        self.assertNotIn('12345678', text)


UCI = r"""
[ "$1" = -q ] && shift
f=$STUBLOG/uci.state
case "$1" in
    show) if [ -n "${2:-}" ]; then grep "^$2[.=]" "$f"; else cat "$f"; fi; exit 0 ;;
    get) v=$(sed -n "s/^$2='\(.*\)'$/\1/p" "$f" | head -n1); [ -n "$v" ] || exit 1; echo "$v"; exit 0 ;;
    set) k=${2%%=*}
         case "$2" in *.encryption=*) [ -z "${UCI_STUCK:-}" ] || { echo "$*" >> "$STUBLOG/uci.log"; exit 1; } ;; esac
         grep -v "^$k=" "$f" > "$f.n"; echo "$k='${2#*=}'" >> "$f.n"; mv "$f.n" "$f" ;;
    delete) grep -v "^$2=" "$f" > "$f.n"; mv "$f.n" "$f" ;;
    del_list) k=${2%%=*}; sed -e "/^$k=/s/ *'${2#*=}'//" -e "/^$k=\$/d" "$f" > "$f.n"; mv "$f.n" "$f" ;;
    commit) ;;
esac
echo "$*" >> "$STUBLOG/uci.log"
exit 0
"""
PASSWORD = 'synthetic-test-password'
# what `uci show wireless` printed for the documented opt-in, with a synthetic passphrase
SAE_STATE = f"""wireless.radio0=wifi-device
wireless.radio0.band='5g'
wireless.ap0=wifi-iface
wireless.ap0.device='radio0'
wireless.ap0.mode='ap'
wireless.ap0.ifname='wlan0'
wireless.ap0.ssid='MU300-TEST'
wireless.ap0.encryption='sae'
wireless.ap0.key='{PASSWORD}'
wireless.ap0.ieee80211w='2'
wireless.ap0.sae_pwe='0'
wireless.ap0.hostapd_bss_options='max_listen_interval=10' 'u30_sae_offload=1'
"""
WPA2_STATE = f"""wireless.radio0=wifi-device
wireless.ap0=wifi-iface
wireless.ap0.device='radio0'
wireless.ap0.mode='ap'
wireless.ap0.ifname='wlan0'
wireless.ap0.encryption='psk2'
wireless.ap0.key='{PASSWORD}'
"""


class Migration(ShellTest):
    """openwrt-wifi-wpa2-only migrate: SAE becomes WPA2-PSK (CCMP) with the same password, or off; never open."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        for d in ('etc/mu300', 'etc/config'):
            (self.root / d).mkdir(parents=True)
        (self.root / 'etc/openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
        (self.root / 'etc/config/wireless').write_text('the file as it was\n')
        self.stub('uci', UCI)
        self.stub('logger', 'echo "$*" >> "$STUBLOG/logger.log"')
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/wifi.log"')

    def migrate(self, shell, state, **env):
        (self.tmp / 'uci.state').write_text(state)
        for f in ('uci.log', 'logger.log', 'wifi.log'):
            (self.tmp / f).unlink(missing_ok=True)
        for f in ('wpa3-disabled', 'wireless.before-wpa2'):
            (self.root / 'etc/mu300' / f).unlink(missing_ok=True)
        r = self.script(shell, MIGRATE, 'migrate', MU300_SYSROOT=self.root, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def log(self, name):
        p = self.tmp / name
        return p.read_text().splitlines() if p.exists() else []

    def state(self):
        return (self.tmp / 'uci.state').read_text()

    def notice(self):
        p = self.root / 'etc/mu300/wpa3-disabled'
        return dict(l.split('=', 1) for l in p.read_text().splitlines()) if p.exists() else None

    def assertUntouched(self, r, state):
        self.assertEqual(r.stderr, '')
        self.assertEqual(self.state(), state)
        self.assertEqual([c for c in self.log('uci.log') if not c.startswith(('show', 'get '))], [])
        self.assertEqual(self.log('logger.log') + self.log('wifi.log'), [])
        self.assertEqual(sorted(p.name for p in (self.root / 'etc/mu300').iterdir()), [])

    def assertNoPassword(self, r):
        written = r.stdout + r.stderr + '\n'.join(self.log('logger.log')) + (self.root / 'etc/mu300/wpa3-disabled').read_text()
        self.assertNotIn(PASSWORD, written)

    def test_sae_hotspot_becomes_wpa2_with_the_same_password(self):
        for shell in self.each_shell():
            r = self.migrate(shell, SAE_STATE)
            s = self.state()
            self.assertIn("wireless.ap0.encryption='psk2+ccmp'\n", s)
            self.assertIn(f"wireless.ap0.key='{PASSWORD}'\n", s)
            self.assertIn("wireless.ap0.hostapd_bss_options='max_listen_interval=10'\n", s)
            for gone in ('u30_sae_offload', 'ieee80211w', 'sae_pwe', 'disabled'):
                self.assertNotIn(gone, s)
            self.assertEqual(self.log('uci.log')[-1], 'commit wireless')
            # one line, a warning: the hotspot is up and encrypted
            self.assertEqual(len(self.log('logger.log')), 1)
            self.assertIn('-p daemon.warn', self.log('logger.log')[0])
            self.assertIn('ap0 is now WPA2-PSK (CCMP) with the same password', r.stderr)
            n = self.notice()
            self.assertEqual((n['state'], n['migrated'], n['down']), ('migrated', 'ap0', ''))
            self.assertRegex(n['time'], r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
            self.assertEqual((self.root / 'etc/mu300/wpa3-disabled').stat().st_mode & 0o777, 0o644)
            saved = self.root / 'etc/mu300/wireless.before-wpa2'
            self.assertEqual(saved.read_text(), 'the file as it was\n')
            self.assertEqual(saved.stat().st_mode & 0o077, 0)
            self.assertNoPassword(r)
            # Wi-Fi is not restarted here: mu300-hw's "wifi down; wifi up" comes right after
            self.assertEqual(self.log('wifi.log'), [])

    def test_every_sae_mode_and_the_option_alone(self):
        for shell in self.each_shell():
            for enc in ('sae-mixed', 'sae-compat', 'psk3', 'psk3-mixed', 'sae+ccmp', 'psk2'):
                state = SAE_STATE.replace("encryption='sae'", f"encryption='{enc}'")
                with self.subTest(encryption=enc):
                    self.migrate(shell, state)
                    self.assertIn("wireless.ap0.encryption='psk2+ccmp'\n", self.state())
                    self.assertNotIn('u30_sae_offload', self.state())
                    self.assertEqual(self.notice()['state'], 'migrated')

    def test_without_a_usable_password_the_hotspot_goes_off_not_open(self):
        for shell in self.each_shell():
            for key in (None, 'short', 'x' * 64, 'caf\u00e9-password'):
                state = SAE_STATE.replace(f"wireless.ap0.key='{PASSWORD}'\n", '' if key is None else f"wireless.ap0.key='{key}'\n")
                with self.subTest(key=key):
                    r = self.migrate(shell, state)
                    s = self.state()
                    self.assertIn("wireless.ap0.disabled='1'\n", s)
                    self.assertIn("wireless.ap0.encryption='psk2+ccmp'\n", s)
                    self.assertNotIn("encryption='none'", s)
                    self.assertIn('-p daemon.err', self.log('logger.log')[0])
                    self.assertIn('off, not open', r.stderr)
                    n = self.notice()
                    self.assertEqual((n['state'], n['down'], n['migrated']), ('down', 'ap0', ''))
            # a raw key of 64 hex digits is a WPA2 key
            self.migrate(shell, SAE_STATE.replace(PASSWORD, 'a' * 64))
            self.assertEqual(self.notice()['state'], 'migrated')

    def test_a_change_that_did_not_stick_is_reported_down(self):
        for shell in self.each_shell():
            r = self.migrate(shell, SAE_STATE, UCI_STUCK=1)
            self.assertEqual(self.notice()['state'], 'down')
            self.assertIn('-p daemon.err', self.log('logger.log')[0])
            self.assertIn('the change was not saved', r.stderr)

    def test_nothing_to_do(self):
        for shell in self.each_shell():
            for state in (WPA2_STATE, SAE_STATE.replace("mode='ap'", "mode='sta'"),
                          WPA2_STATE.replace("encryption='psk2'", "encryption='none'")):
                self.assertUntouched(self.migrate(shell, state), state)
            # once migrated, the next boot does nothing (one log line, once)
            self.migrate(shell, SAE_STATE)
            after = self.state()
            (self.tmp / 'logger.log').unlink()
            r = self.script(shell, MIGRATE, 'migrate', MU300_SYSROOT=self.root)
            self.assertEqual((r.returncode, r.stderr, self.state()), (0, '', after))
            self.assertEqual(self.log('logger.log'), [])

    def test_not_openwrt(self):
        (self.root / 'etc/openwrt_release').unlink()
        for shell in self.each_shell():
            self.assertUntouched(self.migrate(shell, SAE_STATE), SAE_STATE)

    def test_boot_migrates_before_wifi_up_and_nothing_else_checks_the_offload(self):
        hw = (TOP / 'openwrt/overlay/etc/init.d/mu300-hw').read_text()
        self.assertLess(hw.index('openwrt-wifi-config'), hw.index('openwrt-wifi-wpa2-only migrate'))
        self.assertLess(hw.index('openwrt-wifi-wpa2-only migrate'), hw.index('\twifi down >>'))
        post = (TOP / 'openwrt/overlay/etc/init.d/mu300-post').read_text()
        self.assertNotIn('sae', post)


@unittest.skipUnless(shutil.which('node'), 'no node')
class NoticeModule(unittest.TestCase):
    """the LuCI notice (Status > Overview and the panel's home): nothing without the file, the message in the
    language of the page (English, Turkish, Chinese), the dismissal removes the file"""

    def render(self, content, lang):
        harness = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const content = process.argv[3] === '-' ? null : process.argv[3];
const removed = [];
function E(tag, attrs, children) {
  const kids = (Array.isArray(children) ? children : children == null ? [] : [children]);
  return { tag, attrs, style: {}, children: kids, appendChild(c) { kids.push(c); } };
}
global.E = E;
global.document = { documentElement: { getAttribute: () => process.argv[4] } };
global.L = { resolveDefault: (p, d) => Promise.resolve(p).catch(() => d) };
const baseclass = { extend: (o) => o };
const fsmod = { read: (p) => content == null ? Promise.reject(new Error('NotFound')) : Promise.resolve(content),
                remove: (p) => { removed.push(p); return Promise.resolve(0); } };
const ui = { createHandlerFn: (ctx, fn) => fn.bind(ctx) };
const body = src.split('\n').filter(l => !/^'require /.test(l) && l !== "'use strict';").join('\n');
const mod = new Function('baseclass', 'fs', 'ui', body)(baseclass, fsmod, ui);
function text(n) { return typeof n === 'string' ? n : (n.children || []).map(text).join(''); }
mod.load().then(async (n) => {
  const box = mod.render(n);
  const out = { title: mod.title, box: box ? text(box) : null, cls: box ? box.attrs['class'] : null };
  if (box) { await box.children[1].attrs.click(); out.removed = removed; out.hidden = box.style.display; }
  console.log(JSON.stringify(out));
});
"""
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h.js'
            h.write_text(harness)
            r = subprocess.run(['node', str(h), str(NOTICE_JS), content if content is not None else '-', lang],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

    def test_no_file_no_notice(self):
        self.assertIsNone(self.render(None, 'en')['box'])
        self.assertIsNone(self.render('garbage\n', 'en')['box'])

    def test_messages(self):
        migrated = 'state=migrated\ntime=2026-10-17 10:00:00\nmigrated=ap0\ndown=\n'
        down = 'state=down\ntime=2026-10-17 10:00:00\nmigrated=\ndown=ap0\n'
        o = self.render(migrated, 'en')
        self.assertIn('WPA2-PSK (CCMP) with the same password: ap0', o['box'])
        self.assertIn('2026-10-17 10:00:00', o['box'])
        self.assertIn('warning', o['cls'])
        self.assertEqual((o['removed'], o['hidden']), (['/etc/mu300/wpa3-disabled'], 'none'))
        o = self.render(down, 'en')
        self.assertIn('ap0 is off, not open', o['box'])
        self.assertIn('error', o['cls'])
        self.assertIn('WPA2-PSK\'ye (CCMP)', self.render(migrated, 'tr')['box'])
        self.assertIn('Kapat', self.render(migrated, 'tr')['box'])
        zh = self.render(migrated, 'zh-cn')
        self.assertIn('WPA2-PSK\uff08CCMP\uff09', zh['box'])
        self.assertIn('\u5b89\u5168', zh['title'])
        # any other language: English
        self.assertIn('Dismiss', self.render(migrated, 'de')['box'])

    def test_panel_home_loads_it_and_acl_allows_only_this_file(self):
        home = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/home.js').read_text()
        self.assertIn("L.require('view.status.include.05_mu300_wpa2')", home)
        acl = json.loads((TOP / 'openwrt/overlay/usr/share/rpcd/acl.d/mu300-wpa2-notice.json').read_text())
        g = acl['mu300-wpa2-notice']
        self.assertEqual(list(g['read']['file']), ['/etc/mu300/wpa3-disabled'])
        self.assertEqual(list(g['write']['file']), ['/etc/mu300/wpa3-disabled'])
        self.assertEqual(g['write']['ubus'], {'file': ['remove']})


class UbuntuHotspot(ShellTest):
    """hotspot-start: a hotspot.conf asking for WPA3/SAE is refused with a line, the hotspot is WPA2-PSK (CCMP)"""

    def test_refused(self):
        conf = self.tmp / 'hotspot.conf'
        out = self.tmp / 'hostapd.conf'
        self.stub('iw', 'exit 0')
        self.stub('logger', 'echo "$*" >> "$STUBLOG/logger.log"')
        self.stub('sleep', 'exit 0')
        for value in ('sae', 'SAE-mixed', 'wpa3', 'psk2'):
            with self.subTest(value=value):
                conf.write_text(f'SSID=MU300-T\nPSK={PASSWORD}\nBAND=2.4\nCHANNEL=6\nCOUNTRY=TR\nENCRYPTION={value}\n')
                (self.tmp / 'logger.log').unlink(missing_ok=True)
                r = self.script(['bash'], BIN / 'hotspot-start', MU300_HOTSPOT_CONF=conf, MU300_HOSTAPD_CONF=out,
                                MU300_HOTSPOT_DRYRUN=1)
                self.assertEqual(r.returncode, 0, r.stderr)
                c = out.read_text()
                self.assertIn('wpa_key_mgmt=WPA-PSK\n', c)
                self.assertIn('rsn_pairwise=CCMP\n', c)
                self.assertNotIn('SAE', c)
                if value == 'psk2':
                    self.assertNotIn('refused', r.stderr)
                else:
                    self.assertIn(f'ENCRYPTION={value} refused: WPA3/SAE is disabled on this Wi-Fi chip', r.stderr)
                    self.assertIn('WPA2-PSK only', r.stderr)
                    self.assertNotIn(PASSWORD, r.stderr)


if __name__ == '__main__':
    unittest.main()
