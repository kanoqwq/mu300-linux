"""The hotspot never goes onto a DFS channel (FINDINGS 42: the SC2355 driver has no radar detection, hostapd's
start_dfs_cac() fails and the AP stays down).

* `lib/wifi-channels.sh`: the country table, a DFS or disallowed channel replaced with one line why, "auto", widths;
* `hotspot-start` (Ubuntu): the hostapd configuration it writes (MU300_HOTSPOT_DRYRUN), with the kernel's channel
  flags (a stub `iw list`) taking another channel of the country before 2.4 GHz;
* `openwrt-wifi-config` and `mu300-wifi-band` on OpenWrt (a stub `uci`): the channel, htmode, the channel list for
  auto, and a DFS channel set in LuCI rewritten at boot;
* `openwrt/patches/wifi-scripts-no-dfs.patch`: how the build applies it, and - with MU300_WRT_IMAGE naming an
  OpenWrt 25.12 docker image (e.g. mu300-openwrt-base:25.12.6, as openwrt/build-rootfs.sh imports it) - the hostapd
  configuration the patched wifi-scripts generate for a fake wiphy with and without radar detection.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

LIB = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'wifi-channels.sh'
PATCH = TOP / 'openwrt' / 'patches' / 'wifi-scripts-no-dfs.patch'
FIXTURES = TOP / 'tests' / 'fixtures' / 'wifi-scripts'
FIVE = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
        149, 153, 157, 161, 165]


class Lib(ShellTest):
    def call(self, shell, code):
        r = self.sh(shell, f'. "{LIB}"; {code}')
        return r.stdout.strip(), r.stderr.strip()

    def test_parses(self):
        for shell in self.each_shell():
            r = subprocess.run(shell + ['-n', str(LIB)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_country_table(self):
        cases = {
            'CN': '36 40 44 48 149 153 157 161 165', 'US': '36 40 44 48 149 153 157 161 165',
            'de': '36 40 44 48 149 153 157 161 165', 'TR': '36 40 44 48', 'JP': '36 40 44 48',
            'BD': '149 153 157 161 165', 'PK': '149 153 157 161 165', 'ID': '36 40 44 48 149 153 157 161',
            '00': '', 'AM': '', 'ZZ': '36 40 44 48 149 153 157 161 165', '': '36 40 44 48 149 153 157 161 165',
        }
        for shell in self.each_shell():
            for cc, want in cases.items():
                self.assertEqual(self.call(shell, f'wifi_ap5_channels "{cc}"')[0], want, cc)
            # never a DFS channel in any row
            for cc in ('CN', 'TR', 'BD', 'ID', 'US'):
                got = self.call(shell, f'wifi_ap5_channels {cc}')[0].split()
                self.assertFalse([c for c in got if 52 <= int(c) <= 144], cc)

    def test_dfs(self):
        for shell in self.each_shell():
            for c in FIVE:
                out = self.call(shell, f'wifi_ap5_dfs {c} && echo yes || echo no')[0]
                self.assertEqual(out, 'yes' if 52 <= c <= 144 else 'no', c)
            self.assertEqual(self.call(shell, 'wifi_ap5_dfs "" && echo yes || echo no')[0], 'no')

    def test_default(self):
        for shell in self.each_shell():
            for cc, want in (('CN', '36'), ('TR', '36'), ('BD', '149'), ('00', '')):
                self.assertEqual(self.call(shell, f'wifi_ap5_default {cc}')[0], want, cc)

    def test_resolve(self):
        for shell in self.each_shell():
            # the user's report: channel 60 in CN
            out, err = self.call(shell, 'wifi_ap5_resolve CN 60')
            self.assertEqual(out, '36')
            self.assertEqual(err, 'hotspot: 5 GHz channel 60 is a DFS channel (radar detection), which this Wi-Fi '
                                  'driver cannot do: using channel 36')
            for ch in (52, 64, 100, 120, 144):
                out, err = self.call(shell, f'wifi_ap5_resolve CN {ch}')
                self.assertEqual(out, '36', ch)
                self.assertIn('DFS', err)
            self.assertEqual(self.call(shell, 'wifi_ap5_resolve BD 100'), ('149', 'hotspot: 5 GHz channel 100 is a '
                             'DFS channel (radar detection), which this Wi-Fi driver cannot do: using channel 149'))
            # allowed: kept, nothing said
            for cc, ch in (('CN', 36), ('CN', 48), ('CN', 149), ('CN', 165), ('TR', 44), ('BD', 161)):
                self.assertEqual(self.call(shell, f'wifi_ap5_resolve {cc} {ch}'), (str(ch), ''), (cc, ch))
            # auto (or nothing) is the default, silently
            for v in ('auto', '', '0'):
                self.assertEqual(self.call(shell, f'wifi_ap5_resolve CN "{v}"'), ('36', ''), v)
            self.assertEqual(self.call(shell, 'wifi_ap5_resolve BD auto'), ('149', ''))
            # not DFS, but not this country's (or not 5 GHz at all)
            out, err = self.call(shell, 'wifi_ap5_resolve TR 149')
            self.assertEqual((out, err), ('36', 'hotspot: 5 GHz channel 149 is not available to the hotspot in '
                                                'country TR: using channel 36'))
            self.assertEqual(self.call(shell, 'wifi_ap5_resolve CN 6')[0], '36')
            # no 5 GHz channel at all: empty, and why
            out, err = self.call(shell, 'wifi_ap5_resolve 00 60')
            self.assertEqual(out, '')
            self.assertIn('country 00 has no 5 GHz channel without DFS', err)
            self.assertEqual(self.call(shell, 'wifi_ap5_resolve 00 auto'), ('', ''))

    def test_width(self):
        for shell in self.each_shell():
            for cc, ch, w in (('CN', 36, '80'), ('CN', 48, '80'), ('CN', 149, '80'), ('CN', 161, '80'),
                              ('CN', 165, '20'), ('TR', 40, '80'), ('ID', 157, '80'), ('BD', 153, '80'),
                              ('BD', 165, '20'), ('CN', 60, '20')):
                self.assertEqual(self.call(shell, f'wifi_ap5_width {cc} {ch}')[0], w, (cc, ch))
            # a block the country allows only partly: the pair, else 20 MHz (no such country today: a fake one)
            out = self.call(shell, 'wifi_ap5_channels() { echo "36 40 44"; }; '
                                   'for c in 36 40 44; do wifi_ap5_width XX $c; done')[0]
            self.assertEqual(out.split(), ['40', '40', '20'])


IW_LINE = '\t\t\t* {mhz}.0 MHz [{ch}] ({flags})\n'


class HotspotStart(ShellTest):
    """hotspot-start up to the hostapd configuration (bash: it uses ${x^^})."""

    def setUp(self):
        super().setUp()
        if not shutil.which('bash'):
            self.skipTest('no bash')
        self.conf = self.tmp / 'hotspot.conf'
        self.out = self.tmp / 'hostapd.conf'
        self.stub('sleep', 'exit 0')
        self.stub('iw', 'case "$*" in "reg get") echo "country ${FAKE_REG:-CN}: DFS-FCC" ;; '
                        'list) cat "$STUBLOG/iwlist" ;; esac; exit 0')

    def iw_list(self, flags=None):
        """iw list's 5 GHz channels as CN has them (DFS: radar), FLAGS {channel: text} overriding."""
        lines = ''
        for c in FIVE:
            f = '23.0 dBm' if c < 52 or c > 144 else '20.0 dBm) (no IR, radar detection'
            if 100 <= c <= 144:
                f = 'disabled'
            f = (flags or {}).get(c, f)
            lines += IW_LINE.format(mhz=5000 + 5 * c, ch=c, flags=f)
        (self.tmp / 'iwlist').write_text(lines)

    def run_start(self, conf, **env):
        self.conf.write_text(conf)
        r = subprocess.run(['bash', str(BIN / 'hotspot-start')], capture_output=True, text=True, timeout=60,
                           env=self.env(MU300_HOTSPOT_CONF=self.conf, MU300_HOSTAPD_CONF=self.out,
                                        MU300_HOTSPOT_DRYRUN=1, **env))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = dict(l.split('=', 1) for l in self.out.read_text().splitlines() if '=' in l)
        return cfg, r.stderr

    def test_dfs_channel_is_replaced(self):
        self.iw_list()
        cfg, err = self.run_start('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=60\nCOUNTRY=CN\n')
        self.assertEqual((cfg['hw_mode'], cfg['channel']), ('a', '36'))
        self.assertEqual(cfg['vht_oper_chwidth'], '1')
        self.assertEqual(cfg['vht_oper_centr_freq_seg0_idx'], '42')
        self.assertEqual(cfg['ht_capab'], '[HT40+][SHORT-GI-20][SHORT-GI-40]')
        self.assertEqual(cfg['country_code'], 'CN')
        self.assertIn('channel 60 is a DFS channel', err)

    def test_auto_and_kept_channels(self):
        self.iw_list()
        for ch, seg0, ht in (('auto', '42', '[HT40+]'), ('', '42', '[HT40+]'), ('48', '42', '[HT40-]'),
                             ('149', '155', '[HT40+]'), ('161', '155', '[HT40-]')):
            cfg, err = self.run_start(f'SSID=x\nPSK=12345678\nBAND=5\nCHANNEL={ch}\nCOUNTRY=CN\n')
            self.assertEqual(cfg['channel'], ch if ch not in ('auto', '') else '36', ch)
            self.assertEqual(cfg['vht_oper_centr_freq_seg0_idx'], seg0, ch)
            self.assertTrue(cfg['ht_capab'].startswith(ht), ch)
            self.assertEqual(err, '', ch)

    def test_a_hidden_name(self):
        # HIDDEN=1 (mu300-api wifi.set's switch): not broadcast; anything else, or no line: broadcast
        self.iw_list()
        for line, want in (('HIDDEN=1\n', '1'), ('HIDDEN=0\n', '0'), ('', '0'), ('HIDDEN=yes\n', '0')):
            cfg, _ = self.run_start(f'SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=36\nCOUNTRY=CN\n{line}')
            self.assertEqual(cfg['ignore_broadcast_ssid'], want, line)

    def test_165_is_20_mhz(self):
        self.iw_list()
        cfg, _ = self.run_start('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=165\nCOUNTRY=CN\n')
        self.assertEqual((cfg['channel'], cfg['ht_capab'], cfg['vht_oper_chwidth']), ('165', '[SHORT-GI-20]', '0'))
        self.assertNotIn('vht_oper_centr_freq_seg0_idx', cfg)

    def test_country_without_149(self):
        self.iw_list()
        cfg, err = self.run_start('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=149\nCOUNTRY=TR\n', FAKE_REG='TR')
        self.assertEqual(cfg['channel'], '36')
        self.assertIn('not available to the hotspot in country TR', err)

    def test_kernel_refuses_the_channel(self):
        # 36 still NO-IR (the country not live yet): another channel of the country, not 2.4 GHz
        self.iw_list({36: 'no IR'})
        cfg, err = self.run_start('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=auto\nCOUNTRY=CN\n')
        self.assertEqual((cfg['hw_mode'], cfg['channel'], cfg['vht_oper_centr_freq_seg0_idx']), ('a', '40', '42'))
        self.assertIn('using channel 40', err)
        # none of them: 2.4 GHz
        self.iw_list({c: 'no IR' for c in FIVE})
        cfg, err = self.run_start('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=auto\nCOUNTRY=CN\n')
        self.assertEqual((cfg['hw_mode'], cfg['channel']), ('g', '6'))
        self.assertIn('falling back to 2.4 GHz', err)

    def test_no_5ghz_country(self):
        self.iw_list()
        cfg, err = self.run_start('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=60\nCOUNTRY=00\n', FAKE_REG='00')
        self.assertEqual((cfg['hw_mode'], cfg['channel']), ('g', '6'))
        self.assertIn('no 5 GHz channel without DFS in country 00', err)

    def test_2g_untouched(self):
        self.iw_list()
        cfg, err = self.run_start('SSID=x\nPSK=12345678\nBAND=2.4\nCHANNEL=11\nCOUNTRY=CN\n')
        self.assertEqual((cfg['hw_mode'], cfg['channel'], cfg['ht_capab']), ('g', '11', '[SHORT-GI-20]'))
        self.assertEqual(err, '')


UCI = r'''
[ "$1" = -q ] && shift
case "$1" in
    show) grep -q '^wireless\.radio0\.' "$STUBLOG/uci.state" 2>/dev/null && echo "wireless.radio0=wifi-device" ;;
    get) v=$(sed -n "s/^$2=//p" "$STUBLOG/uci.state" 2>/dev/null | head -n1); [ -n "$v" ] || exit 1; echo "$v" ;;
    batch) cat >> "$STUBLOG/uci.batch" ;;
    *) echo "$*" >> "$STUBLOG/uci.batch" ;;
esac
exit 0
'''


class OpenWrt(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'etc' / 'mu300').mkdir(parents=True)
        (self.root / 'etc' / 'config').mkdir()
        (self.root / 'sys' / 'class' / 'ieee80211' / 'phy0').mkdir(parents=True)
        (self.root / 'etc' / 'openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
        self.stub('uci', UCI)
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/wifi.log"')
        self.stub('sleep', 'exit 0')
        self.stub('logger', 'exit 0')

    def state(self, text):
        (self.tmp / 'uci.state').write_text(text)
        for f in ('uci.batch',):
            (self.tmp / f).unlink(missing_ok=True)

    def batch(self):
        p = self.tmp / 'uci.batch'
        return p.read_text() if p.exists() else ''

    def config(self, shell, hotspot):
        (self.root / 'etc' / 'mu300' / 'wifi-configured').unlink(missing_ok=True)
        (self.root / 'etc' / 'mu300' / 'hotspot.conf').write_text(hotspot)
        r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_first_config(self):
        for shell in self.each_shell():
            self.state('')
            r = self.config(shell, 'SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=60\nCOUNTRY=CN\n')
            b = self.batch()
            self.assertIn("set wireless.radio0.channel='36'\n", b)
            self.assertIn("set wireless.radio0.htmode='VHT80'\n", b)
            self.assertIn("set wireless.radio0.channels='36 40 44 48 149 153 157 161 165'\n", b)
            self.assertIn("set wireless.radio0.acs_exclude_dfs='1'\n", b)
            self.assertIn("set wireless.radio0.country='CN'\n", b)
            self.assertIn('channel 60 is a DFS channel', r.stderr)
            self.assertIn('wireless configured: 5g channel 36', r.stdout)
            self.assertTrue((self.root / 'etc' / 'mu300' / 'wifi-configured').exists())
            # auto: the default, silently; 165: 20 MHz; a country without 149
            for conf, ch, ht, chans in (('CHANNEL=auto\nCOUNTRY=CN', '36', 'VHT80', None),
                                        ('CHANNEL=165\nCOUNTRY=CN', '165', 'VHT20', None),
                                        ('CHANNEL=149\nCOUNTRY=JP', '36', 'VHT80', '36 40 44 48'),
                                        ('CHANNEL=auto\nCOUNTRY=BD', '149', 'VHT80', '149 153 157 161 165')):
                self.state('')
                self.config(shell, f'SSID=x\nPSK=12345678\nBAND=5\n{conf}\n')
                b = self.batch()
                self.assertIn(f"set wireless.radio0.channel='{ch}'\n", b, conf)
                self.assertIn(f"set wireless.radio0.htmode='{ht}'\n", b, conf)
                if chans:
                    self.assertIn(f"set wireless.radio0.channels='{chans}'\n", b, conf)
            # 2.4 GHz: no 5 GHz channel list
            self.state('')
            self.config(shell, 'SSID=x\nPSK=12345678\nBAND=2.4\nCHANNEL=auto\nCOUNTRY=CN\n')
            b = self.batch()
            self.assertIn("set wireless.radio0.channel='6'\n", b)
            self.assertIn('delete wireless.radio0.channels\n', b)
            # a country with no 5 GHz channel without DFS: 2.4 GHz
            self.state('')
            r = self.config(shell, 'SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=auto\nCOUNTRY=00\n')
            self.assertIn("set wireless.radio0.band='2g'\n", self.batch())
            self.assertIn("set wireless.radio0.channel='6'\n", self.batch())

    def test_dfs_channel_from_luci_rewritten_at_boot(self):
        (self.root / 'etc' / 'mu300' / 'wifi-configured').write_text('')
        for shell in self.each_shell():
            for ch, ht, want_ch, want_ht in (('60', 'VHT80', '36', 'VHT80'), ('100', 'HE160', '36', 'HE80'),
                                             ('52', 'HT40', '36', 'HT40'), ('120', 'VHT80', '36', 'VHT80')):
                self.state(f'wireless.radio0.band=5g\nwireless.radio0.channel={ch}\nwireless.radio0.htmode={ht}\n'
                           'wireless.radio0.country=CN\n')
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual(r.returncode, 0, r.stderr)
                b = self.batch()
                self.assertIn(f"set wireless.radio0.channel='{want_ch}'\n", b, ch)
                self.assertIn(f"set wireless.radio0.htmode='{want_ht}'\n", b, ch)
                self.assertIn("set wireless.radio0.acs_exclude_dfs='1'\n", b, ch)
                self.assertIn('commit wireless', b)
                self.assertIn(f'channel {ch} is a DFS channel', r.stderr)
            # left alone: a non-DFS channel, auto, 2.4 GHz, no radio
            for st in ('wireless.radio0.band=5g\nwireless.radio0.channel=149\nwireless.radio0.country=CN\n',
                       'wireless.radio0.band=5g\nwireless.radio0.channel=auto\nwireless.radio0.country=CN\n',
                       'wireless.radio0.band=2g\nwireless.radio0.channel=11\n', ''):
                self.state(st)
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual((r.returncode, self.batch(), r.stderr), (0, '', ''), st)

    def test_wifi_band(self):
        for shell in self.each_shell():
            for cc, ch, ht, chans in (('CN', '36', 'VHT80', '36 40 44 48 149 153 157 161 165'),
                                      ('JP', '36', 'VHT80', '36 40 44 48'), ('BD', '149', 'VHT80', '149 153 157 161 165'),
                                      ('', '36', 'VHT80', '36 40 44 48')):
                self.state(f'wireless.radio0.band=2g\nwireless.radio0.country={cc}\n')
                r = self.script(shell, BIN / 'mu300-wifi-band', '5', MU300_SYSROOT=self.root)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, 'hotspot: 5 GHz'), r.stderr)
                b = self.batch()
                self.assertIn(f"set wireless.radio0.channel='{ch}'\n", b, cc)
                self.assertIn(f"set wireless.radio0.htmode='{ht}'\n", b, cc)
                self.assertIn(f"set wireless.radio0.channels='{chans}'\n", b, cc)
                self.assertIn("set wireless.radio0.acs_exclude_dfs='1'\n", b, cc)
            self.state('wireless.radio0.band=5g\nwireless.radio0.country=CN\n')
            r = self.script(shell, BIN / 'mu300-wifi-band', '2.4', MU300_SYSROOT=self.root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('delete wireless.radio0.channels\n', self.batch())
            self.assertIn("set wireless.radio0.channel='6'\n", self.batch())
            # no 5 GHz channel without DFS: refused, nothing changed
            self.state('wireless.radio0.band=2g\nwireless.radio0.country=00\n')
            r = self.script(shell, BIN / 'mu300-wifi-band', '5', MU300_SYSROOT=self.root)
            self.assertEqual(r.returncode, 1)
            self.assertIn('no 5 GHz channel without DFS in country 00', r.stderr)
            self.assertEqual(self.batch(), '')


class WifiScriptsPatch(unittest.TestCase):
    def test_build_applies_it(self):
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('-v "$NODFSPATCH":/in/wifi-scripts-no-dfs.patch:ro', text)
        apply = 'patch --batch --fuzz=0 -d $R -p1 -i /in/wifi-scripts-no-dfs.patch'
        self.assertIn(apply, text)
        self.assertIn('grep -q "device_no_dfs(config);" $R/usr/share/ucode/wifi/hostapd.uc || {', text)
        # in every OpenWrt system (outside the panel block), after the copy into $R, before the patch tool goes
        self.assertLess(text.index('cp -a /in/overlay/. $R/'), text.index(apply))
        self.assertLess(text.index(apply), text.index('if [ -d /in/luci-plugin ]; then\n    [ -d /in/luci-overlay ]'))
        self.assertLess(text.index(apply), text.index('apk del patch'))

    def test_patch(self):
        p = PATCH.read_text()
        self.assertTrue(p.startswith('--- a/usr/share/ucode/wifi/hostapd.uc\n+++ b/usr/share/ucode/wifi/hostapd.uc\n'))
        added = '\n'.join(l[1:] for l in p.splitlines() if l.startswith('+') and not l.startswith('+++'))
        # the capability is read from the driver, not assumed: radar_detect_widths in an interface combination
        self.assertIn('phy.interface_combinations', added)
        self.assertIn('c.radar_detect_widths', added)
        self.assertIn('config.acs_exclude_dfs = true;', added)
        self.assertIn('device_no_dfs(config);', added)


@unittest.skipUnless(os.environ.get('MU300_WRT_IMAGE') and shutil.which('docker'),
                     'MU300_WRT_IMAGE (an OpenWrt 25.12 arm64 docker image) not set')
class WifiScriptsInOpenWrt(unittest.TestCase):
    """The patched wifi-scripts of the OpenWrt feed, for a fake wiphy: the hostapd configuration they write."""

    @staticmethod
    def wiphy(radar, flags):
        comb = {'maxnum': 3, 'num_channels': 2}
        if radar:
            comb['radar_detect_widths'] = 63
        freqs = []
        for c in FIVE:
            e = {'freq': 5000 + 5 * c}
            e.update(flags.get(c, {'radar': True, 'no_ir': True} if 52 <= c <= 144 else {}))
            freqs.append(e)
        return {'extended_features': [0] * 16, 'cipher_suites': [], 'interface_combinations': [comb],
                'wiphy_bands': [{'freqs': [{'freq': 2412}]}, {'freqs': freqs, 'ht_capa': 0x1fe,
                                                               'vht_capa': 0x339071b2}]}

    @staticmethod
    def data(channel, htmode, extra=None):
        cfg = {'band': '5g', 'channel': str(channel), 'htmode': htmode, 'country': 'CN'}
        cfg.update(extra or {})
        return {'phy': 'phy0', 'phy_suffix': '', 'vif_phy_suffix': '', 'config': cfg,
                'interfaces': {'ap0': {'config': {'mode': 'ap', 'ssid': 'MU300-TEST', 'encryption': 'psk2',
                                                  'key': '12345678', 'ifname': 'wlan0', 'network': ['lan']},
                                       'bridge': 'br-lan', 'vlans': {}, 'stas': {}}}}

    def test_hostapd_config(self):
        no36 = {c: {'disabled': True} for c in (36, 40, 44, 48)}
        cases = {
            # name: (driver detects radar, channel flags, channel, htmode, extra) -> expected
            'cn60': (False, {}, 60, 'VHT80', None),
            'cn60-radar': (True, {}, 60, 'VHT80', None),
            'cn36': (False, {}, 36, 'VHT80', None),
            'cn149': (False, {}, 149, 'VHT80', None),
            'cn52-he160': (False, {}, 52, 'HE160', None),
            'cn120-vht40': (False, {}, 120, 'VHT40', None),
            'auto': (False, {}, 'auto', 'VHT80', None),
            'auto-radar': (True, {}, 'auto', 'VHT80', None),
            'auto-list': (False, {}, 'auto', 'VHT80', {'channels': '36 40 44 48 149 153 157 161 165'}),
            'no36-60': (False, no36, 60, 'VHT80', None),
            'no5g-60': (False, {c: {'disabled': True} for c in FIVE if not 52 <= c <= 144}, 60, 'VHT80', None),
        }
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'cases').mkdir()
            (d / 'out').mkdir()
            for n, (radar, flags, ch, ht, extra) in cases.items():
                (d / 'cases' / f'{n}.wiphy.json').write_text(json.dumps(self.wiphy(radar, flags)))
                (d / 'cases' / f'{n}.data.json').write_text(json.dumps(self.data(ch, ht, extra)))
            script = r'''
mkdir -p /var/lock /var/run /tmp /fake/wifi
apk update >/dev/null && apk add wifi-scripts patch >/dev/null 2>&1 || exit 1
patch --batch --fuzz=0 -p1 -d / -i /in/wifi-scripts-no-dfs.patch || exit 1
grep -q "device_no_dfs(config);" /usr/share/ucode/wifi/hostapd.uc || exit 1
sed -e "s/^export function wiphy_info(phy) {/function nl_wiphy_info(phy) {/" /usr/share/ucode/wifi/common.uc > /fake/wifi/common.uc
grep -q "^function nl_wiphy_info" /fake/wifi/common.uc || exit 1
echo "export function wiphy_info(phy) { return json(fs.readfile(getenv(\"FAKE_WIPHY\"))); };" >> /fake/wifi/common.uc
for f in /t/cases/*.data.json; do
    n=$(basename "$f" .data.json)
    FAKE_DATA=$f FAKE_WIPHY=/t/cases/$n.wiphy.json FAKE_CC=CN \
        ucode -L "/fake/*.uc" -L "/usr/share/ucode/*.uc" /fx/run.uc > /t/out/$n.conf 2>&1 || echo "rc=$?" >> /t/out/$n.conf
done
'''
            r = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
                                '-v', f'{PATCH.parent}:/in:ro', '-v', f'{FIXTURES}:/fx:ro', '-v', f'{d}:/t',
                                os.environ['MU300_WRT_IMAGE'], '/bin/sh', '-c', script],
                               capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            def conf(n):
                text = (d / 'out' / f'{n}.conf').read_text()
                self.assertNotIn('rc=', text, n)
                return text, dict(l.split('=', 1) for l in text.splitlines() if '=' in l and not l.startswith('#'))

            text, c = conf('cn60')
            self.assertIn('wifi-scripts: channel 60 is a DFS channel and this driver cannot detect radar', text)
            self.assertEqual((c['channel'], c['vht_oper_chwidth'], c['vht_oper_centr_freq_seg0_idx']), ('36', '1', '42'))
            # a driver that can detect radar keeps the channel (hostapd does the CAC)
            text, c = conf('cn60-radar')
            self.assertEqual((c['channel'], c['vht_oper_centr_freq_seg0_idx']), ('60', '58'))
            self.assertNotIn('DFS channel', text)
            for n, ch, seg0 in (('cn36', '36', '42'), ('cn149', '149', '155')):
                text, c = conf(n)
                self.assertEqual((c['channel'], c['vht_oper_centr_freq_seg0_idx']), (ch, seg0), n)
                self.assertNotIn('DFS channel', text)
            # 160 MHz cannot fit without DFS: 80
            _, c = conf('cn52-he160')
            self.assertEqual((c['channel'], c['he_oper_chwidth'], c['he_oper_centr_freq_seg0_idx']), ('36', '1', '42'))
            _, c = conf('cn120-vht40')
            self.assertEqual((c['channel'], c['vht_oper_chwidth'], c['ht_capab'][:7]), ('36', '0', '[HT40+]'))
            # auto: ACS without the DFS channels (and the channel list as given)
            _, c = conf('auto')
            self.assertEqual((c['channel'], c['acs_exclude_dfs']), ('0', '1'))
            _, c = conf('auto-radar')
            self.assertEqual((c['channel'], c['acs_exclude_dfs']), ('0', '0'))
            _, c = conf('auto-list')
            self.assertEqual((c['acs_exclude_dfs'], c['chanlist']), ('1', '36 40 44 48 149 153 157 161 165'))
            # 36-48 not allowed: 149
            _, c = conf('no36-60')
            self.assertEqual((c['channel'], c['vht_oper_centr_freq_seg0_idx']), ('149', '155'))
            # nothing to go to: left as it was, and said
            text, c = conf('no5g-60')
            self.assertEqual(c['channel'], '60')
            self.assertIn('no other 5 GHz channel is allowed', text)


class HotspotVerify(ShellTest):
    """hotspot-verify: a refused CMD_START_AP sends the hotspot to 2.4 GHz; a good start (the mainline driver prints no
    answer for it) ends as soon as iw shows the access point's SSID instead of after 20 s (it held every boot)."""

    def setUp(self):
        super().setUp()
        # Ubuntu's /bin/sh runs it (dash); busybox sh would run its own sleep and touch, not the stubs
        self.shells = [s for s in self.shells if 'busybox' not in ' '.join(s)]
        if not self.shells:
            self.skipTest('no dash or bash')
        self.stub('sleep', 'echo x >> "$STUBLOG/sleeps"')
        self.stub('systemctl', 'echo "$*" >> "$STUBLOG/systemctl"')
        self.stub('touch', 'exit 0')
        self.stub('dmesg', 'cat "$STUBLOG/dmesg" 2>/dev/null; exit 0')
        # iw: no SSID for the first FAKE_UP calls, then the access point's
        self.stub('iw', 'n=$(cat "$STUBLOG/iwn" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$STUBLOG/iwn"; '
                        'echo "Interface wlan0"; [ $n -le ${FAKE_UP:-0} ] || printf "\tssid F50_2\n\ttype AP\n"')

    def run_verify(self, shell, dmesg='', **env):
        for f in ('sleeps', 'systemctl', 'iwn'):
            (self.tmp / f).unlink(missing_ok=True)
        (self.tmp / 'dmesg').write_text(dmesg)
        env.setdefault('MAINPID', '')
        r = self.script(shell, BIN / 'hotspot-verify', **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        sleeps = len((self.tmp / 'sleeps').read_text().split()) if (self.tmp / 'sleeps').exists() else 0
        sc = (self.tmp / 'systemctl').read_text() if (self.tmp / 'systemctl').exists() else ''
        return sleeps, sc, r.stderr

    def test_a_good_start_ends_when_the_ssid_is_up(self):
        for shell in self.each_shell():
            sleeps, sc, _ = self.run_verify(shell, FAKE_UP=3)
            self.assertEqual(sleeps, 4)
            self.assertEqual(sc, '')

    def test_never_up_waits_its_20_seconds(self):
        for shell in self.each_shell():
            sleeps, sc, _ = self.run_verify(shell, FAKE_UP=1000)
            self.assertEqual(sleeps, 40)
            self.assertEqual(sc, '')

    REFUSED = '[{:12.6f}] sprd-wlan: sc2355_rx_rsp_process ctx_id 0 recv rsp[CMD_START_AP] status[SPRD_CMD_STATUS_NOT_SUPPORT]\n'

    @staticmethod
    def uptime():
        """The host's /proc/uptime (hotspot-verify reads only the dmesg lines from its own start on); None without it."""
        try:
            return float(Path('/proc/uptime').read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def test_a_refused_start_goes_to_2g(self):
        # printed after hotspot-verify began (a fixed early time, 27.5 s, was before it on any Linux host up longer than
        # that: the line was skipped; without /proc/uptime, as on macOS, the script keeps every line)
        now = self.uptime()
        line = self.REFUSED.format(27.5 if now is None else now + 5)
        for shell in self.each_shell():
            # the refusal is printed before the access point shows up, or in the same moment
            for up in (0, 1000):
                sleeps, sc, err = self.run_verify(shell, dmesg=line, FAKE_UP=up)
                self.assertIn('restart mu300-hotspot.service', sc)
                self.assertIn('falling back to 2.4 GHz', err)
                self.assertEqual(sleeps, 1)

    def test_a_refusal_from_before_this_start_is_not_this_one(self):
        now = self.uptime()
        if now is None or now < 3:
            self.skipTest('needs /proc/uptime, and a host up for more than 3 s')
        line = self.REFUSED.format(now - 2)
        for shell in self.each_shell():
            sleeps, sc, err = self.run_verify(shell, dmesg=line, FAKE_UP=3)
            self.assertEqual(sc, '')
            self.assertNotIn('falling back', err)
            self.assertEqual(sleeps, 4)

    @unittest.skipUnless(Path('/proc/self/comm').exists(), 'needs /proc')
    def test_an_ssid_before_hostapd_runs_is_not_this_start(self):
        # $MAINPID still hotspot-start (here: a process that is not hostapd): an SSID left from before does not count
        p = subprocess.Popen(['sleep', '30'])
        try:
            for shell in self.each_shell():
                sleeps, sc, _ = self.run_verify(shell, FAKE_UP=0, MAINPID=p.pid)
                self.assertEqual(sleeps, 40)
        finally:
            p.kill()
            p.wait()


if __name__ == '__main__':
    unittest.main()
