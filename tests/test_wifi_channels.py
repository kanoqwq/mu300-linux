"""The hotspot never goes onto a DFS channel (FINDINGS 40: the SC2355 driver has no radar detection, hostapd's
start_dfs_cac() fails and the AP stays down).

* `lib/wifi-channels.sh`: the country table, a DFS or disallowed channel replaced with one line why, "auto", widths;
* `hotspot-start` (Ubuntu): the hostapd configuration it writes (MU300_HOTSPOT_DRYRUN), with the kernel's channel
  flags (a stub `iw list`) taking another channel of the country before 2.4 GHz;
* `openwrt-wifi-config` and `mu300-wifi-band` on OpenWrt (a stub `uci`): the channel, htmode, the channel list for
  auto, and a DFS channel set in LuCI rewritten at boot;
* `openwrt/patches/wifi-scripts-no-dfs.patch`: how the build applies it, and - with MU300_WRT_IMAGE naming an
  OpenWrt 25.12 docker image (e.g. mu300-openwrt-base:25.12.5, as openwrt/build-rootfs.sh imports it) - the hostapd
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
            out, err = self.call(shell, 'wifi_ap5_resolve BD 100; echo rc=$?')
            self.assertEqual(out, 'rc=1')
            self.assertIn('channel 36 / 80 MHz is not permitted', err)
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
            self.assertIn('channel 36 / 80 MHz is not permitted in country 00', err)
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
        self.conf.write_text('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=60\nCOUNTRY=00\n')
        r = self.script(['bash'], BIN / 'hotspot-start', MU300_HOTSPOT_CONF=self.conf,
                        MU300_HOSTAPD_CONF=self.out, MU300_HOTSPOT_DRYRUN=1, FAKE_REG='00')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('channel 36 / 80 MHz is not permitted', r.stderr)
        self.assertFalse(self.out.exists())

    def test_dfs_does_not_fall_back_to_another_width_or_channel(self):
        self.iw_list({48: 'disabled'})
        self.conf.write_text('SSID=x\nPSK=12345678\nBAND=5\nCHANNEL=60\nCOUNTRY=CN\n')
        r = self.script(['bash'], BIN / 'hotspot-start', MU300_HOTSPOT_CONF=self.conf,
                        MU300_HOSTAPD_CONF=self.out, MU300_HOTSPOT_DRYRUN=1)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('no narrower fallback', r.stderr)
        self.assertFalse(self.out.exists())

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
            for ch, ht, want_ch in (('60', 'VHT80', '36'), ('100', 'VHT20', '36'),
                                    ('52', 'HT40', '36'), ('120', 'VHT40', '36'), ('100', 'HE80', '36')):
                self.state(f'wireless.radio0.band=5g\nwireless.radio0.channel={ch}\nwireless.radio0.htmode={ht}\n'
                           'wireless.radio0.country=CN\n')
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual(r.returncode, 0, r.stderr)
                b = self.batch()
                self.assertIn(f"set wireless.radio0.channel='{want_ch}'\n", b, ch)
                self.assertNotIn('.htmode=', b, ch)
                self.assertIn("set wireless.radio0.acs_exclude_dfs='1'\n", b, ch)
                self.assertIn('commit wireless', b)
                self.assertIn(f'channel {ch} is a DFS channel', r.stderr)
            for cc in ('00', 'BD'):
                self.state('wireless.radio0.band=5g\nwireless.radio0.channel=100\nwireless.radio0.htmode=VHT80\n'
                           f'wireless.radio0.country={cc}\n')
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(self.batch(), '')
                self.assertIn('bandwidth left unchanged', r.stderr)
            for ht in ('VHT160', 'HE160', 'EHT320', 'VHT80+80'):
                self.state('wireless.radio0.band=5g\nwireless.radio0.channel=60\nwireless.radio0.country=CN\n'
                           f'wireless.radio0.htmode={ht}\n')
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(self.batch(), '')
                self.assertIn('bandwidth left unchanged', r.stderr)
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
        self.assertIn('-v "$TOP/openwrt/patches/wifi-scripts-no-dfs.patch":/in/wifi-scripts-no-dfs.patch:ro', text)
        apply = 'patch --batch --fuzz=0 -d $R -p1 -i /in/wifi-scripts-no-dfs.patch'
        self.assertIn(apply, text)
        self.assertIn('grep -q "device_no_dfs(config);" $R/usr/share/ucode/wifi/hostapd.uc || {', text)
        # in every OpenWrt system (outside the panel block), after the copy into $R, before the patch tool goes
        self.assertLess(text.index('cp -a /in/overlay/. $R/'), text.index(apply))
        self.assertLess(text.index(apply), text.index('if [ -d /in/luci-plugin ]; then\n    cp -a /in/luci-plugin/root/.'))
        self.assertLess(text.index(apply), text.index('cd $R && tar -czf'))

    def test_patch(self):
        p = PATCH.read_text()
        self.assertTrue(p.startswith('--- a/usr/share/ucode/wifi/hostapd.uc\n+++ b/usr/share/ucode/wifi/hostapd.uc\n'))
        added = '\n'.join(l[1:] for l in p.splitlines() if l.startswith('+') and not l.startswith('+++'))
        # the capability is read from the driver, not assumed: radar_detect_widths in an interface combination
        self.assertIn('phy.interface_combinations', added)
        self.assertIn('c.radar_detect_widths', added)
        self.assertIn('config.acs_exclude_dfs = true;', added)
        self.assertIn('device_no_dfs(config);', added)
        self.assertIn('device_chanlist_band(config);', added)
        self.assertNotRegex(added, r'config\.htmode\s*=(?!=)')


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
                'wiphy_bands': [{'freqs': [{'freq': 2407 + 5 * c} for c in range(1, 14)], 'ht_capa': 0x1fe}, {'freqs': freqs, 'ht_capa': 0x1fe,
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
            'cn60-vht20': (False, {}, 60, 'VHT20', None),
            'auto': (False, {}, 'auto', 'VHT80', None),
            'auto-radar': (True, {}, 'auto', 'VHT80', None),
            'auto-list': (False, {}, 'auto', 'VHT80', {'channels': '36 40 44 48 149 153 157 161 165'}),
            '2g-auto-5glist': (False, {}, 'auto', 'HT20', {'band':'2g','channels':'36 40 44 48'}),
            '2g-auto-mixed': (False, {}, 'auto', 'HT40', {'band':'2g','channels':['1 6', '36', '1-11', '1-36']}),
            '2g-auto-list': (False, {}, 'auto', 'HT20', {'band':'2g','channels':'1 6 11'}),
            '5g-auto-2glist': (False, {}, 'auto', 'VHT80', {'channels':'1 6 11'}),
            '2g-auto-invalid': (False, {}, 'auto', 'HT20', {'band':'2g','channels':['1-6-11', '11-1', '0x6', '6']}),
            'no36-60': (False, no36, 60, 'VHT80', None),
            'partial36-60': (False, {48: {'disabled': True}}, 60, 'VHT80', None),
            'only40-60': (False, {c: {'disabled': True} for c in FIVE if c not in (36, 40, 60)}, 60, 'VHT80', None),
            'only40-60-vht40': (False, {c: {'disabled': True} for c in FIVE if c not in (36, 40, 60)}, 60, 'VHT40', None),
            'no-ir-60': (False, {c: {'no_ir': True} for c in FIVE if not 52 <= c <= 144}, 60, 'VHT80', None),
            'no80-low-60': (False, {c: {'no_80mhz': True} for c in (36, 40, 44, 48)}, 60, 'VHT80', None),
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
            # Fixed channel 36, but htmode is never reduced or widened.
            def refused(n):
                text = (d / 'out' / f'{n}.conf').read_text()
                self.assertIn('rc=', text, n)
                self.assertIn('bandwidth left unchanged', text, n)
                self.assertNotIn('vht_oper_chwidth=', text, n)
            refused('cn52-he160')
            _, c = conf('cn120-vht40')
            self.assertEqual((c['channel'], c['vht_oper_chwidth'], c['ht_capab'][:7]), ('36', '0', '[HT40+]'))
            _, c = conf('cn60-vht20')
            self.assertEqual((c['channel'], c['vht_oper_chwidth']), ('36', '0'))
            self.assertNotIn('HT40', c['ht_capab'])
            # auto: ACS without the DFS channels (and the channel list as given)
            _, c = conf('auto')
            self.assertEqual((c['channel'], c['acs_exclude_dfs']), ('0', '1'))
            _, c = conf('auto-radar')
            self.assertEqual((c['channel'], c['acs_exclude_dfs']), ('0', '0'))
            _, c = conf('auto-list')
            self.assertEqual((c['acs_exclude_dfs'], c['chanlist']), ('1', '36 40 44 48 149 153 157 161 165'))
            text,c=conf('2g-auto-5glist')
            self.assertEqual((c['hw_mode'],c['channel']),('g','0'))
            self.assertNotIn('chanlist',c)
            self.assertIn('channel list: 36 40 44 48 not on the 2g band or invalid, left out',text)
            _,c=conf('2g-auto-mixed')
            self.assertEqual(c['chanlist'],'1 6 1-11')
            self.assertIn('HT40',c['ht_capab'])
            text,c=conf('2g-auto-list')
            self.assertEqual(c['chanlist'],'1 6 11')
            self.assertNotIn('channel list:',text)
            _,c=conf('5g-auto-2glist')
            self.assertEqual((c['channel'],c['acs_exclude_dfs']),('0','1'))
            self.assertNotIn('chanlist',c)
            _,c=conf('2g-auto-invalid')
            self.assertEqual(c['chanlist'],'6')
            _, c = conf('only40-60-vht40')
            self.assertEqual((c['channel'], c['vht_oper_chwidth']), ('36', '0'))
            # Cannot fit the requested width on 36: error, not 149 or narrower.
            for n in ('no36-60', 'partial36-60', 'no80-low-60',
                      'no5g-60', 'only40-60', 'no-ir-60'):
                refused(n)


if __name__ == '__main__':
    unittest.main()
