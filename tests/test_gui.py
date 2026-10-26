"""The graphical installer (gui/) without its window: the catalogs, the masking of logs, the adb devices, the
choices and the answers they become, a run's progress, and the controller driving the real install.sh --check
against tests/fakeadb.py and a whole installation against tests/fakeinstaller.py. At the end the window itself is
built once, page by page, where there is a display (or an Xvfb to make one) - and skipped cleanly where there is
none."""
import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from helpers import TOP

sys.path.insert(0, str(TOP / 'gui'))
from mu300gui import adb, desktop, i18n, masking, model, releases, runner  # noqa: E402
from mu300gui.controller import Controller  # noqa: E402

TESTS = Path(__file__).resolve().parent
POSIX = os.name != 'nt'


class Catalogs(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('en')

    def test_fill(self):
        self.assertEqual(i18n.fill('{1} of {2}', ['a', 'b']), 'a of b')
        self.assertEqual(i18n.fill('{2} then {1}', ['a', 'b']), 'b then a')
        self.assertEqual(i18n.fill('{1} {3} {x} {} {0}', ['a']), 'a {3} {x} {} {0}')
        self.assertEqual(i18n.fill('{1}', ['{2}', 'b']), '{2}')          # an argument is never filled in again
        self.assertEqual(i18n.fill('no braces', []), 'no braces')
        self.assertEqual(i18n.fill('{', []), '{')

    def test_languages(self):
        self.assertEqual(i18n._('Continue'), 'Continue')
        for lang in ('tr', 'zh'):
            i18n.set_language(lang)
            self.assertNotEqual(i18n._('Continue'), 'Continue', lang)
            self.assertEqual(i18n._('a message nobody translated {1}', 7), 'a message nobody translated 7')
        self.assertEqual(i18n.set_language('xx'), 'en')

    def test_the_language_of_the_system(self):
        for env, want in (({'MU300_LANG': 'tr'}, 'tr'), ({'LANG': 'zh_CN.UTF-8'}, 'zh'), ({'LANG': 'tr_TR.UTF-8'}, 'tr'),
                          ({'LC_ALL': 'en_US.UTF-8', 'LANG': 'tr_TR'}, 'en'), ({'LANG': 'de_DE.UTF-8'}, 'en'), ({'LANG': 'C'}, 'en'),
                          ({}, 'en')):
            self.assertEqual(i18n.os_language(env, getlocale=lambda: ''), want, env)
        self.assertEqual(i18n.os_language({}, getlocale=lambda: 'zh-Hans'), 'zh')

    def messages(self):
        """Every message of the window: the first argument of _() and N_() in gui/, where it is a plain string."""
        found = []
        for path in sorted((TOP / 'gui').rglob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('_', 'N_') and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.append((arg.value, len(node.args) - 1, f'{path.name}:{node.lineno}'))
                    else:
                        # _(label) of a table: its entries are marked with N_() where they are written
                        self.assertEqual(node.func.id, '_', f'{path.name}:{node.lineno}: N_() of something that is no string')
        return found

    def test_every_message_is_in_the_catalogs_the_checker_reads(self):
        found = self.messages()
        self.assertGreater(len(found), 150)
        r = subprocess.run([sys.executable, str(TOP / 'tools' / 'check-i18n.py'), '--keys'], capture_output=True, text=True)
        keys = set(r.stdout.split('\n'))
        for msg, _n, where in found:
            self.assertIn(msg, keys, where)
        r = subprocess.run([sys.executable, str(TOP / 'tools' / 'check-i18n.py')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])

    def test_messages_and_their_arguments(self):
        for msg, nargs, where in self.messages():
            holes = sorted(set(int(n) for n in re.findall(r'\{(\d)\}', msg)))
            self.assertEqual(holes, list(range(1, len(holes) + 1)), f'{where}: {msg}')
            self.assertLessEqual(len(holes), 6, where)                 # tools/i18n.sh fills {1}..{6}
            self.assertNotIn('\t', msg, where)                         # a tab ends the message in the catalogs
            self.assertNotIn('\n', msg, where)


class Masking(unittest.TestCase):
    def setUp(self):
        self.m = masking.Masker(home='/Users/kaan')
        self.m.add_serial('324950664950')
        self.m.add_serial('MU300LINUX-320260377386')
        self.m.add('correct horse battery', '<password>')

    def test_what_is_known_by_value(self):
        out = self.m.mask('adb -s 324950664950 shell; device 324950664950 again, and MU300LINUX-320260377386 / 320260377386')
        self.assertNotIn('324950664950', out)
        self.assertNotIn('320260377386', out)
        self.assertIn('<serial>', out)
        self.assertNotIn('battery', self.m.mask('the password was correct horse battery, twice: correct horse battery'))

    def test_identities(self):
        for text, secret in (('IMEI: 867530912345678', '867530912345678'), ('imei=867530912345678', '867530912345678'),
                             ('+CGSN: 867530912345678', '867530912345678'), ('IMSI 286011234567890', '286011234567890'),
                             ('ICCID: 8990011234567890123', '8990011234567890123'), ('+CCID: 89900112345678901234', '89900112345678901234'),
                             ('iccid 8990011234567890123F', '8990011234567890123'), ('[ro.serialno]: [A1B2C3D4E5F6]', 'A1B2C3D4E5F6'),
                             ('serial number: ZTE0123456789', 'ZTE0123456789'), ('ANDROID_SERIAL=abcdef123456', 'abcdef123456'),
                             ('adb -s 99887766 shell id', '99887766'), ('bssid 3c:22:fb:01:02:03', '3c:22:fb:01:02:03')):
            out = self.m.mask(text)
            self.assertNotIn(secret, out, text)

    def test_secrets(self):
        for text, secret in (('MU300_ANS_PASSWORD=hunter2secret', 'hunter2secret'), ("PWHASH='$6$abcdefgh$" + 'x' * 86 + "'", 'xxxxxxxx'),
                             ('password: s3cret-thing', 's3cret-thing'), ('wpa_passphrase=myhotspotpass', 'myhotspotpass'),
                             ('psk="my wifi pass"', 'my wifi pass'), ('hash $6$rounds=5000$saltsalt$' + 'A' * 40, 'AAAAAAAA'),
                             ('ADB_KEY=\'QAAAA' + 'B' * 694 + '= me@host\'', 'BBBBBBBB'), ('token=ghp_abcdefghijklmnop', 'ghp_abcdefghijklmnop')):
            out = self.m.mask(text)
            self.assertNotIn(secret, out, text)

    def test_the_home_directory(self):
        self.assertEqual(self.m.mask('saved to /Users/kaan/mu300/work/dumps'), 'saved to ~/mu300/work/dumps')

    def test_what_is_not_a_secret_stays(self):
        for text in ('eMMC: 58.2 GiB (122159104 sectors), partitions end at 25.9 GiB (sector 54218752), free after them: 32.4 GiB',
                     'Linux region: offset 27762098176, 32.4 GiB, existing mu300root filesystem: no',
                     'mu300-kernel.tar.gz  sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
                     'release v2026.10.23, kernel 6.18.21', 'Type INSTALL to continue [no]: INSTALL', 'USB network: 192.168.77.1   SSH: ubuntu@192.168.77.1',
                     'a serial console is there too', 'the device passed the check'):
            self.assertEqual(self.m.mask(text), text)

    def test_short_serial(self):
        self.assertEqual(masking.short_serial('324950664950'), '•' * 9 + '950')
        self.assertEqual(masking.short_serial('MU300LINUX-320260377386'), 'MU300LINUX-' + '•' * 9 + '386')
        self.assertEqual(masking.short_serial('abc'), '•••')
        self.assertEqual(masking.short_serial(''), '')


LIST = '''List of devices attached
324950664950           device usb:1 product:MU300 model:F50 device:MU300 transport_id:1
323960377386           device usb:2 product:U30Air model:U30_Air device:U30Air transport_id:2
d16d93c5               device usb:3 product:nabu_global model:21051182G device:nabu transport_id:3
192.168.31.1:55555     offline product:MU5358 model:MU5358 device:MU5358 transport_id:4
0123456789ABCDEF       unauthorized usb:5 transport_id:5
MU300LINUX-320260377386 device usb:6 product:mu300 model:MU300_Linux device:mu300 transport_id:6
ABCDEF                 no permissions (user in plugdev group; are your udev rules wrong?); see [http://developer.android.com/tools/device.html] usb:7

'''


class Devices(unittest.TestCase):
    def test_parse(self):
        ds = adb.parse_devices(LIST)
        self.assertEqual([d.serial for d in ds], ['324950664950', '323960377386', 'd16d93c5', '192.168.31.1:55555', '0123456789ABCDEF',
                                                  'MU300LINUX-320260377386', 'ABCDEF'])
        self.assertEqual([d.state for d in ds], ['device', 'device', 'device', 'offline', 'unauthorized', 'device', 'no permissions'])
        self.assertEqual([d.target for d in ds], [True, True, False, False, False, False, False])
        self.assertEqual([d.linux for d in ds], [False, False, False, False, False, True, False])
        self.assertEqual([d.kind for d in ds][:3], ['f50', 'u30air', ''])
        self.assertEqual(ds[0].attrs['model'], 'F50')
        self.assertEqual(adb.parse_devices('* daemon not running; starting now at tcp:5037\n* daemon started successfully\nList of devices attached\n\n'), [])
        self.assertEqual(adb.parse_devices(''), [])

    def test_the_installers_rule_for_a_target(self):
        # the same expression as tools/linux-mode.sh's select_device
        src = (TOP / 'tools' / 'linux-mode.sh').read_text()
        self.assertIn("grep -E '" + adb.TARGET.pattern + "'", src)

    def test_pick_never_takes_a_phone_by_itself(self):
        ds = adb.parse_devices(LIST)
        f50, u30, tablet, offline, unauth, linux = ds[:6]
        self.assertIs(adb.pick([f50]), f50)
        self.assertIs(adb.pick([tablet, f50, offline]), f50)           # one of ours next to a tablet: ours
        self.assertIsNone(adb.pick([f50, u30]))                         # two of ours: the user says
        self.assertIsNone(adb.pick([tablet]))                           # only a tablet: never by itself
        self.assertIsNone(adb.pick([tablet, offline]))
        self.assertIs(adb.pick([linux]), linux)
        self.assertIs(adb.pick([linux, tablet]), linux)
        self.assertIs(adb.pick([f50, linux]), f50)
        self.assertIs(adb.pick([unauth]), unauth)                       # so that the window can say what is wrong with it
        self.assertIsNone(adb.pick([]))
        self.assertIs(adb.pick([f50, u30], current='323960377386'), u30)  # what the user chose stays chosen
        self.assertIs(adb.pick([f50], current='gone'), f50)

    def test_the_v50_is_one_of_ours_with_a_mainline_kernel_only(self):
        # the installers' rule since the ZTE V50 (FINDINGS 95): device MU3351, USB network 192.168.79.1, no 5.4
        v50 = adb.parse_devices('List of devices attached\n3249506649 device usb:1 product:MU3351 model:V50 device:MU3351 transport_id:1\n')[0]
        self.assertTrue(v50.target)
        self.assertEqual(v50.kind, 'v50')
        self.assertEqual(model.connect('v50', 'ubuntu')['ip'], '192.168.79.1')
        c = model.Choices()
        c.device = 'v50'
        self.assertFalse(c.kernel_ok('5.4'))
        self.assertTrue(c.kernel_ok('6.18'))
        c.device = 'f50'
        self.assertTrue(c.kernel_ok('5.4'))

    def test_the_board_worked_on_comes_back_and_no_other_takes_its_place(self):
        # measured: while the F50 restarted into Linux after an installation, a U30 Air in Linux was the only device,
        # and the window went on with it (its Manage page would have restarted that one)
        f50 = adb.Device('324950664950', 'device', {'model': 'F50', 'product': 'MU300', 'device': 'MU300'})
        f50_linux = adb.Device('MU300LINUX-324950664950-203d8329', 'device', {})
        u30_linux = adb.Device('MU300LINUX-323960377386-203edc81', 'device', {})
        self.assertEqual(adb.board(f50_linux.serial), '324950664950')
        self.assertEqual(adb.board('MU300LINUX-320260377386'), '320260377386')
        self.assertEqual(adb.board(f50.serial), '324950664950')
        self.assertIsNone(adb.pick([u30_linux], current=f50.serial, board_='324950664950'))
        self.assertIs(adb.pick([u30_linux, f50_linux], current=f50.serial, board_='324950664950'), f50_linux)
        self.assertIs(adb.pick([f50, u30_linux], current=f50_linux.serial, board_='324950664950'), f50)
        self.assertIs(adb.pick([u30_linux]), u30_linux)                # nothing chosen before: as it was
        ctl = Controller(top=str(TOP), environ={'MU300_ADB': '/nonexistent/adb', 'PATH': ''})
        ctl._take('devices', [f50, u30_linux])
        self.assertEqual(ctl.serial, f50.serial)
        ctl._take('devices', [u30_linux])                               # the F50 restarts
        self.assertEqual(ctl.serial, '')
        ctl._take('devices', [u30_linux])
        self.assertEqual(ctl.serial, '')
        ctl._take('devices', [u30_linux, f50_linux])                    # back, in Linux
        self.assertEqual(ctl.serial, f50_linux.serial)
        ctl.select(u30_linux.serial)                                    # the user's choice is another board
        ctl._take('devices', [f50_linux])
        self.assertEqual(ctl.serial, '')

    def test_script_command(self):
        argv = adb.script_argv('/p', 'install', ['--check'])
        if POSIX:
            self.assertEqual(argv, ['sh', '/p/install.sh', '--check'])
        else:
            self.assertEqual(argv[-2:], [os.path.join('/p', 'install.ps1'), '-Check'])
            self.assertEqual(adb.script_argv('/p', 'install', ['--answers', 'f'])[-2:], ['-AnswersFile', 'f'])

    def test_linux_json(self):
        line = '{"mu300":1,"device":"u30air","system":"openwrt-luci","version_id":"25.12.5","release":"v2026.10.23","kernel":"6.18.21","root":true,"adb":"open"}'
        info = adb.parse_linux_json('some banner\n' + line + '\n')
        self.assertEqual((info['device'], info['system'], info['release'], info['kernel'], info['adb']),
                         ('u30air', 'openwrt-luci', 'v2026.10.23', '6.18.21', 'open'))
        for text in ('', 'f50\n', '{"device":"f50"}', '{broken', '[1,2]', 'sh: mu300-device: not found'):
            self.assertEqual(adb.parse_linux_json(text), {}, text)
        # a system from before --json: asked one thing after another
        info = adb.parse_linux_plain('mu300\nf50\nv2026.10.12\n\n5.4.254\nubuntu\n')
        self.assertEqual((info['device'], info['release'], info['kernel'], info['system'], info['json']), ('f50', 'v2026.10.12', '5.4.254', 'ubuntu', False))
        self.assertEqual(adb.parse_linux_plain('f50\n'), {})            # no marker: not ours


class Releases(unittest.TestCase):
    def test_decided_by_the_installers_own_file(self):
        full = {'tag_name': 'v2026.10.23', 'published_at': '2026-10-12T17:55:05Z', 'prerelease': False, 'draft': False,
                'assets': [{'name': 'mu300-kernel.tar.gz', 'size': 26971198}]}
        beta = dict(full, tag_name='v2026.10.25', published_at='2026-10-14T08:00:00Z', prerelease=True)
        old = dict(full, tag_name='v2026.10.19', published_at='2026-10-11T08:00:00Z', prerelease=True)
        r = releases.decide(json.dumps([full, beta, old]))
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', 'v2026.10.25'))
        self.assertEqual(r['files']['v2026.10.23'], {'mu300-kernel.tar.gz': 26971198})
        self.assertEqual(releases.decide(json.dumps([full, old]))['beta'], None)
        for text in ('', '{"message": "API rate limit exceeded"}', '<html>'):
            self.assertEqual(releases.decide(text), {'latest': None, 'beta': None, 'files': {}})
        evil = dict(full, tag_name='v1; rm -rf ~', published_at='2026-10-20T00:00:00Z', prerelease=True)
        self.assertEqual(releases.decide(json.dumps([full, evil]))['beta'], None)

    def test_no_answer_from_github(self):
        def gone(_req, timeout=0):
            raise OSError('no network')
        self.assertEqual(releases.fetch(opener=gone), {'latest': None, 'beta': None, 'files': {}})


FACTS = {'device.model': 'F50 / MU300', 'device.kind': 'f50', 'android.build': 'MU300_ZYV1.0.0B09', 'bootloader.locked': '0',
         'bootloader.verified': 'orange', 'root': 'yes', 'root.su': '27.0:MAGISKSU', 'slot.android': 'a', 'slot.linux': 'b',
         'emmc.sectors': '122159104', 'emmc.last_end': '54218752', 'region.offset': str(54222848 * 512),
         'region.size': str((122150912 - 54222848) * 512), 'region.existing': 'no', 'region.dirty': '0',
         'partitions': 'boot_a:2048:131072 boot_b:133120:131072 super:264192:12011520 userdata:12275712:41943040',
         'sd.device': '', 'sd.bytes': '0', 'verdict.code': 'tested', 'need.openwrt': str(800 << 20), 'need.ubuntu': str(1600 << 20),
         'need.both': str(2400 << 20)}


def facts(**more):
    f = model.Facts(FACTS)
    f.update({k.replace('__', '.'): v for k, v in more.items()})
    return f


def good_choices():
    c = model.Choices()
    c.password = c.password2 = 'hunter2secret'
    return c


class Model(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('en')

    def test_sizes_and_times(self):
        self.assertEqual(model.size(34828075008), '32.4 GiB')
        self.assertEqual(model.size(800 << 20), '800 MiB')
        self.assertEqual(model.size(2048), '2 KiB')
        self.assertEqual(model.size(0), '0 B')
        self.assertEqual((model.duration(45), model.duration(200), model.duration(4800)), ('45 s', '3 min', '1 h 20 min'))

    def test_facts(self):
        f = facts()
        self.assertEqual((f.root, f.bootloader, f.existing, f.sd, f.checked, f.kind), (True, 'unlocked', False, None, True, 'f50'))
        self.assertEqual(model.Facts().root, None)
        self.assertEqual(facts(root='no').root, False)
        self.assertEqual(facts(bootloader__locked='1').bootloader, 'locked')
        # the two properties that say it; the verified-boot colour is shown, not interpreted
        self.assertEqual(facts(bootloader__locked='', bootloader__device_state='locked').bootloader, 'locked')
        self.assertEqual(facts(bootloader__locked='', bootloader__device_state='unlocked').bootloader, 'unlocked')
        self.assertEqual(facts(bootloader__locked='0', bootloader__device_state='locked').bootloader, 'locked')
        self.assertEqual(facts(bootloader__locked='', bootloader__verified='green').bootloader, 'unknown')
        self.assertEqual(facts(bootloader__locked='', bootloader__verified='').bootloader, 'unknown')
        # what the installer's check concluded counts first
        self.assertEqual(facts(bootloader__locked='0', bootloader__state='locked').bootloader, 'locked')

    def test_a_locked_bootloader_blocks_nothing(self):
        # a locked bootloader with a patched TrustOS boots Linux, and no property tells: only missing root blocks
        c = good_choices()
        for f in (facts(bootloader__locked='1'), facts(bootloader__state='locked', bootloader__verified='green'),
                  facts(bootloader__locked='', bootloader__device_state='locked')):
            self.assertEqual(f.bootloader, 'locked')
            self.assertEqual(model.problems(c, f), [])
        self.assertEqual([p[0] for p in model.problems(c, facts(bootloader__locked='1', root='no'))], ['device'])
        self.assertEqual(facts(sd__device='/dev/block/mmcblk1p1', sd__bytes='63864569856', sd__state='yes').sd,
                         ('/dev/block/mmcblk1p1', 63864569856, 'yes'))
        self.assertEqual(f.partitions()[-1], ('userdata', 12275712, 41943040))
        self.assertEqual(model.Facts({'partitions': 'junk a:b:c x:1:2'}).partitions(), [('x', 1, 2)])
        self.assertEqual(model.Facts({'region.size': 'many'}).num('region.size'), 0)

    def test_the_picture_of_the_disk_adds_up(self):
        for f in (facts(), facts(region__existing='yes', region__size=str(8388608 * 4096)), facts(region__size='0'),
                  model.Facts({'emmc.sectors': '122159104', 'emmc.last_end': '54218752'})):
            segs = model.layout(f)
            self.assertEqual(sum(s['bytes'] for s in segs), 122159104 * 512)
            self.assertTrue(all(s['bytes'] > 0 for s in segs))
        kinds = [s['kind'] for s in model.layout(facts())]
        self.assertEqual(kinds[:3], ['android', 'userdata', 'other'])
        self.assertIn('free', kinds)
        self.assertIn('linux', [s['kind'] for s in model.layout(facts(region__existing='yes'))])
        self.assertEqual(model.layout(model.Facts()), [])

    def test_make_room(self):
        self.assertIsNone(model.make_room(facts()))
        r = model.make_room(facts(makeroom__first='12275712', makeroom__end='122155007', makeroom__usable_end='122155007',
                                  makeroom__min_android='8388608', makeroom__min_linux='1638400', makeroom__max_linux='101490688'))
        self.assertEqual((r['total'], r['min'], r['max'], r['possible']), (109879296 * 512, 800 << 20, 101490688 * 512, True))
        self.assertEqual(r['suggest'], 4 << 30)
        small = model.make_room(facts(makeroom__first='113766400', makeroom__end='122155007', makeroom__usable_end='122155007',
                                      makeroom__min_android='8388608', makeroom__min_linux='1638400', makeroom__max_linux='0'))
        self.assertFalse(small['possible'])

    def test_the_defaults_are_the_installers(self):
        c = model.Choices()
        src = (TOP / 'install.sh').read_text()
        # each default below is the default of the question in install.sh
        # (both where both fit, systems_default; the window narrows it after the check the same way: controller.py)
        self.assertIn('if [ $SIZE -ge $NEED_BOTH ]; then echo 3; elif [ $SIZE -ge $NEED_UBUNTU ]; then echo 1; else echo 2; fi', src)
        self.assertEqual((c.ubuntu, c.openwrt), (True, True))
        self.assertIn('ask _w "$(t \'Choice\')" 1 MU300_OPENWRT', src)
        self.assertEqual(c.openwrt_flavor, 'plain')
        self.assertIn('ask uv "$(t \'Ubuntu\')" 1 MU300_ANS_UBUNTU', src)
        self.assertEqual(c.ubuntu_version, '24.04')
        # (1, 5.4; on the ZTE V50 2, 6.18, which the window takes after the check: its kernel_ok refuses 5.4 there)
        self.assertIn('kdefault=1; [ "$DEVICE" != v50 ] || kdefault=2', src)
        self.assertIn('ask kchoice "$(t \'Kernel\')" $kdefault MU300_ANS_KERNEL', src)
        self.assertEqual(c.kernel, '5.4')
        self.assertIn("? (yes/no)')\" yes MU300_ANS_DEFAULT_LINUX", src)
        self.assertIn("(1-6)')\" 5 MU300_ANS_BOOT_ATTEMPTS", src)
        self.assertEqual((c.default_linux, c.boot_attempts), (True, 5))
        self.assertIn('yes MU300_ANS_HOTSPOT', src)
        self.assertIn('yes MU300_ANS_GPU', src)
        self.assertIn('no MU300_ANS_ADB', src)
        self.assertIn('no MU300_ANS_PANEL', src)
        self.assertIn('update MU300_ANS_EXISTING', src)
        self.assertEqual((c.hotspot, c.gpu, c.adb, c.panel, c.vpn, c.existing), (True, True, False, False, False, 'update'))
        self.assertEqual((c.overwrite_word, c.erase_sd_word, c.internal_word, c.password), ('', '', '', ''))   # no consent by default

    def test_problems(self):
        def fields(c, f):
            return sorted(set(p[0] for p in model.problems(c, f)))
        c = good_choices()
        self.assertEqual(model.problems(c, facts()), [])
        self.assertEqual(fields(model.Choices(), facts()), ['password'])
        self.assertEqual(fields(c, model.Facts()), ['device'])
        self.assertEqual(fields(c, facts(root='no')), ['device'])
        self.assertEqual(fields(c, facts(device__kind='unknown')), ['device'])
        c.unknown_model = True
        self.assertEqual(model.problems(c, facts(device__kind='unknown')), [])
        c = good_choices(); c.ubuntu = c.openwrt = False
        self.assertEqual(fields(c, facts()), ['systems'])
        c = good_choices(); c.ubuntu_version = '26.04'
        self.assertEqual(fields(c, facts()), ['kernel'])                    # 26.04 needs a mainline kernel
        c.kernel = '6.18'
        self.assertEqual(model.problems(c, facts()), [])
        c = good_choices(); c.password2 = 'different'
        self.assertEqual(fields(c, facts()), ['password'])
        c = good_choices(); c.password = c.password2 = 'short'
        self.assertEqual(fields(c, facts()), ['password'])
        c = good_choices(); c.boot_attempts = 9
        self.assertEqual(fields(c, facts()), ['boot'])
        c.default_linux = False
        self.assertEqual(model.problems(c, facts()), [])                    # not asked then
        # room for one system only
        c = good_choices()
        self.assertEqual(fields(c, facts(region__size=str(2000 << 20))), ['systems'])
        c.openwrt = False
        self.assertEqual(model.problems(c, facts(region__size=str(2000 << 20))), [])
        # data in the free space needs its word
        c = good_choices()
        self.assertEqual(fields(c, facts(region__dirty='2')), ['storage'])
        c.overwrite_word = 'Overwrite'
        self.assertEqual(fields(c, facts(region__dirty='2')), ['storage'])
        c.overwrite_word = 'overwrite'
        self.assertEqual(model.problems(c, facts(region__dirty='2')), [])
        # no room inside
        self.assertEqual(fields(good_choices(), facts(region__size='0')), ['storage'])
        # the card
        card = dict(sd__device='/dev/block/mmcblk1p1', sd__bytes='63864569856', sd__state='no')
        c = good_choices(); c.storage = 'sd'
        self.assertEqual(fields(c, facts()), ['storage'])                   # no card
        self.assertEqual(fields(c, facts(**card)), ['storage'])             # formatting it needs ERASE
        c.erase_sd_word = 'ERASE'
        self.assertEqual(model.problems(c, facts(**card)), [])
        self.assertEqual(fields(c, facts(**dict(card, sd__state='foreign'))), ['storage'])   # never formatted, word or not
        c = good_choices(); c.storage = 'sd'
        self.assertEqual(model.problems(c, facts(**dict(card, sd__state='yes'))), [])        # an update of the card's own: no word
        c.existing = 'wipe'
        self.assertEqual(fields(c, facts(**dict(card, sd__state='yes'))), ['storage'])
        c = good_choices()
        self.assertEqual(fields(c, facts(**dict(card, sd__state='yes'))), ['storage'])       # internal over a card that boots first
        c.internal_word = 'internal'
        self.assertEqual(model.problems(c, facts(**dict(card, sd__state='yes'))), [])

    def test_an_update_keeps_the_password(self):
        """With an installation there and update chosen, the current password is kept by default: the installer
        checks the one typed here on the device, and the new one is then needed only for a system installed new."""
        def fields(c, f):
            return sorted(set(p[0] for p in model.problems(c, f)))
        there = facts(region__existing='yes')
        c = model.Choices()
        self.assertEqual((c.keep_password, c.current_password), (True, ''))
        self.assertTrue(c.keeps_password(there))
        self.assertFalse(c.keeps_password(facts()))                         # nothing there to keep
        self.assertEqual(model.problems(c, there), [])                      # no new password needed
        a = model.answers(c, there, 'en', '')
        self.assertEqual((a['MU300_ANS_EXISTING'], a['MU300_ANS_KEEP_PASSWORD'], a['MU300_ANS_CURRENT_PASSWORD']), ('update', 'yes', ''))
        self.assertNotIn('MU300_ANS_PASSWORD', a)
        self.assertLess(list(a).index('MU300_ANS_KEEP_PASSWORD'), list(a).index('MU300_ANS_CONFIRM'))
        c.current_password = 'oldsecret1'
        self.assertEqual(model.answers(c, there, 'en', '')['MU300_ANS_CURRENT_PASSWORD'], 'oldsecret1')
        c.password = 'newsecret22'                                          # one for a system installed new: checked
        self.assertEqual(fields(c, there), ['password'])
        c.password2 = 'newsecret22'
        self.assertEqual(model.answers(c, there, 'en', '')['MU300_ANS_PASSWORD'], 'newsecret22')
        c = model.Choices(); c.current_password = 'two\nlines'
        self.assertEqual(fields(c, there), ['password'])
        # no: the new password, as always, and nothing about the current one
        c = model.Choices(); c.keep_password = False
        self.assertEqual(fields(c, there), ['password'])
        c.password = c.password2 = 'newsecret22'
        a = model.answers(c, there, 'en', '')
        self.assertEqual((a['MU300_ANS_KEEP_PASSWORD'], a['MU300_ANS_PASSWORD']), ('no', 'newsecret22'))
        self.assertNotIn('MU300_ANS_CURRENT_PASSWORD', a)
        # wipe: nothing is kept
        c = good_choices(); c.existing = 'wipe'
        a = model.answers(c, there, 'en', '')
        self.assertFalse(c.keeps_password(there))
        self.assertNotIn('MU300_ANS_KEEP_PASSWORD', a)
        self.assertNotIn('MU300_ANS_CURRENT_PASSWORD', a)
        rows = dict(model.summary(good_choices(), there, ''))
        self.assertEqual(rows['Password'], 'kept: the current one is checked on the device')
        # in the log too
        m = masking.Masker(home='')
        self.assertNotIn('oldsecret1', m.mask('MU300_ANS_CURRENT_PASSWORD_UBUNTU=oldsecret1 MU300_ANS_CURRENT_PASSWORD=oldsecret1'))

    def header_variables(self):
        head = (TOP / 'install.sh').read_text().split('\nset -eu\n')[0]
        return set(re.findall(r'\b(?:MU300_[A-Z0-9_]+|ANDROID_SERIAL)\b', head))

    def test_answers_are_the_installers_variables(self):
        c = good_choices()
        a = model.answers(c, facts(), 'tr', '324950664950')
        self.assertEqual(list(a.items()), [
            ('MU300_LANG', 'tr'), ('ANDROID_SERIAL', '324950664950'), ('MU300_ANS_BETA', 'no'), ('MU300_ANS_SYSTEMS', '3'),
            ('MU300_OPENWRT', 'plain'), ('MU300_ANS_BOOT_OS', 'ubuntu'), ('MU300_ANS_UBUNTU', '24.04'), ('MU300_ANS_DEFAULT_LINUX', 'yes'),
            ('MU300_ANS_BOOT_ATTEMPTS', '5'), ('MU300_ANS_HOTSPOT', 'yes'), ('MU300_ANS_GPU', 'yes'), ('MU300_ANS_VPN', 'no'),
            ('MU300_ANS_ADB', 'no'), ('MU300_ANS_PANEL', 'no'), ('MU300_ANS_KERNEL', '5.4'), ('MU300_ANS_PASSWORD', 'hunter2secret'),
            ('MU300_ANS_CONFIRM', '@stdin')])
        known = self.header_variables() | {'MU300_RELEASE', 'MU300_RELEASE_URL', 'MU300_SYNC_BOOTCHAIN', 'MU300_FETCH_JOBS',
                                            'MU300_VPN_URL', 'MU300_VPN_MODULE'}
        every = model.Choices()
        every.__dict__.update(password='hunter2secret', password2='hunter2secret', openwrt_flavor='luci', boot_os='openwrt', ubuntu_version='26.04',
                              kernel='7.2', release='v2026.10.25', release_url='http://127.0.0.1:8000', vpn=True, vpn_url='http://x/vpn',
                              vpn_module='/tmp/m.tar.gz', adb=True, panel=True, storage='sd', existing='wipe', erase_sd_word='ERASE',
                              internal_word='internal', overwrite_word='overwrite', unknown_model=True, sync_bootchain=False, fetch_jobs=2)
        f = facts(device__kind='unknown', sd__device='/dev/block/mmcblk1p1', sd__bytes='63864569856', sd__state='yes', region__dirty='3')
        a = model.answers(every, f, 'en', 'S')
        for name in a:
            self.assertIn(name, known, f'{name} is no variable of install.sh')
        self.assertEqual((a['MU300_ANS_SYSTEMS'], a['MU300_OPENWRT'], a['MU300_ANS_BOOT_OS'], a['MU300_ANS_UBUNTU'], a['MU300_ANS_KERNEL']),
                         ('3', 'luci', 'openwrt-luci', '26.04', '7.2'))
        self.assertEqual((a['MU300_RELEASE'], a['MU300_STORAGE'], a['MU300_ANS_EXISTING'], a['MU300_ANS_ERASE_SD'], a['MU300_ANS_UNKNOWN_MODEL']),
                         ('v2026.10.25', 'sd', 'wipe', 'ERASE', 'yes'))
        self.assertNotIn('MU300_ANS_BETA', a)                 # a release was named: that question is not asked
        self.assertNotIn('MU300_ANS_OVERWRITE', a)            # the card, not the free space
        self.assertNotIn('MU300_ANS_INTERNAL_OVER_CARD', a)
        every.storage = 'internal'
        a = model.answers(every, f, 'en', 'S')
        self.assertEqual((a['MU300_ANS_INTERNAL_OVER_CARD'], a['MU300_ANS_OVERWRITE']), ('internal', 'overwrite'))
        self.assertNotIn('MU300_ANS_ERASE_SD', a)

    def test_no_destructive_word_that_was_not_typed(self):
        # whatever the device looks like, the words that allow something destructive are only ever the user's own
        c = good_choices()
        for f in (facts(), facts(region__dirty='5'), facts(sd__device='/dev/block/mmcblk1p1', sd__bytes='63864569856', sd__state='yes'),
                  facts(region__existing='yes'), facts(device__kind='unknown')):
            for storage in ('internal', 'sd'):
                c.storage = storage
                a = model.answers(c, f, 'en', 'S')
                for word in ('MU300_ANS_OVERWRITE', 'MU300_ANS_ERASE_SD', 'MU300_ANS_INTERNAL_OVER_CARD', 'MU300_ANS_ERASE_ANDROID',
                             'MU300_ANS_MAKE_ROOM'):
                    self.assertNotIn(word, a, (storage, dict(a)))
                self.assertNotIn('INSTALL', a.values())
                self.assertEqual(a.get('MU300_ANS_EXISTING', 'update'), 'update')
                self.assertEqual(a['MU300_ANS_CONFIRM'], '@stdin')
                self.assertEqual(a.get('MU300_ANS_UNKNOWN_MODEL', 'no'), 'no')
        a = model.make_room_answers('en', 'S', 4.0, '')
        self.assertNotIn('MU300_ANS_ERASE_ANDROID', a)
        self.assertEqual(a['MU300_ANS_LINUX_GIB'], '4')
        self.assertEqual(model.make_room_answers('en', 'S', 2.5, 'ERASE')['MU300_ANS_LINUX_GIB'], '2.5')
        a = model.uninstall_answers('en', 'S', 'secure', 'keep')
        self.assertEqual((a['MU300_ANS_CONFIRM'], a['MU300_ANS_GROW']), ('@stdin', 'no'))
        self.assertNotIn('MU300_ANS_ERASE_ANDROID', a)
        self.assertNotIn('MU300_ANS_ERASE_ANDROID', model.uninstall_answers('en', 'S', 'keep', 'keep', grow=True))

    def test_downloads(self):
        files = {'mu300-kernel.tar.gz': 26, 'mu300-kernel-6.18.tar.gz': 18, 'mu300-ubuntu-rootfs.tar.gz': 97, 'mu300-ubuntu-26.04-rootfs.tar.gz': 109,
                 'mu300-openwrt-rootfs.tar.gz': 21, 'mu300-openwrt-luci-rootfs.tar.gz': 22, 'mu300-extra-panel-offline.tar.gz': 24}
        c = model.Choices()
        self.assertEqual(model.download_plan(c, files), ([('mu300-kernel.tar.gz', 26), ('mu300-ubuntu-rootfs.tar.gz', 97), ('mu300-openwrt-rootfs.tar.gz', 21)], 144))
        c.kernel, c.ubuntu_version, c.openwrt_flavor, c.panel, c.vpn = '6.18', '26.04', 'luci', True, True
        names = [n for n, _b in model.download_plan(c, files)[0]]
        self.assertEqual(names, ['mu300-kernel.tar.gz', 'mu300-kernel-6.18.tar.gz', 'mu300-ubuntu-26.04-rootfs.tar.gz',
                                 'mu300-openwrt-luci-rootfs.tar.gz', 'mu300-extra-panel-offline.tar.gz'])   # the VPN is a module of its own
        # the names are the ones install.sh asks the release for
        src = (TOP / 'install.sh').read_text()
        for name in ('mu300-kernel.tar.gz', 'mu300-kernel-$KERNEL.tar.gz', 'mu300-ubuntu-26.04-rootfs.tar.gz', 'mu300-$1-rootfs.tar.gz',
                     'mu300-extra-panel-offline.tar.gz', 'mu300-extra-vpn.tar.gz'):
            self.assertIn(name, src)

    def test_how_to_connect(self):
        self.assertEqual(model.connect('f50', 'ubuntu')['ssh'], 'ubuntu@192.168.77.1')
        self.assertEqual(model.connect('u30air', 'ubuntu')['ssh'], 'ubuntu@192.168.78.1')
        o = model.connect('u30air', 'openwrt-luci')
        self.assertEqual((o['ssh'], o['web']), ('root@192.168.78.1', [('LuCI', 'http://192.168.78.1')]))
        self.assertEqual(model.connect('f50', 'ubuntu', panel=True)['web'], [('Cockpit', 'https://192.168.77.1:9090')])
        # the addresses the installer prints
        src = (TOP / 'install.sh').read_text()
        self.assertIn('IP=192.168.77.1; [ $DEVICE = u30air ] && IP=192.168.78.1', src)

    def test_summary_in_every_language(self):
        for lang in ('en', 'tr', 'zh'):
            i18n.set_language(lang)
            rows = model.summary(good_choices(), facts(), 'v2026.10.23')
            self.assertEqual(len(rows), 6)
            self.assertIn('Ubuntu 24.04', rows[0][1])


class Run(unittest.TestCase):
    """Progress, fed with events."""

    def progress(self, kind='install'):
        self.now = [100.0]
        return model.Progress(model.INSTALL_STEPS if kind == 'install' else model.UNINSTALL_STEPS, kind=kind, clock=lambda: self.now[0])

    def test_stages(self):
        p = self.progress()
        self.assertEqual(p.state('preflight'), 'pending')
        p.event({'event': 'step', 'id': 'preflight', 'percent': '2', 'title': 'Checking'})
        self.assertEqual((p.state('preflight'), p.current, p.percent, p.title), ('running', 'preflight', 2, 'Checking'))
        p.event({'event': 'step', 'id': '', 'percent': '', 'title': 'Which Ubuntu?'})          # a heading, no stage
        self.assertEqual((p.current, p.title), ('preflight', 'Which Ubuntu?'))
        p.event({'event': 'step', 'id': 'pull', 'percent': '15', 'title': 'Pulling'})
        self.assertEqual([p.state(s) for s in ('preflight', 'storage', 'choices', 'pull', 'download')],
                         ['done', 'skipped', 'skipped', 'running', 'pending'])
        p.event({'event': 'step', 'id': 'done', 'percent': '100', 'title': 'Done'})
        self.assertEqual((p.state('pull'), p.state('done'), p.percent), ('done', 'done', 100))
        p.event({'event': 'done', 'result': 'ok', 'boot_os': 'ubuntu'})
        p.end(0)
        self.assertTrue(p.ok)

    def test_a_download_has_a_speed_and_a_time_left(self):
        p = self.progress()
        p.event({'event': 'step', 'id': 'download', 'percent': '20', 'title': 'Downloading'})
        p.event({'event': 'download', 'file': 'k.tar.gz', 'bytes': '0', 'total': '1000000', 'state': 'start'})
        for i in range(1, 5):
            self.now[0] += 1.0
            p.event({'event': 'download', 'file': 'k.tar.gz', 'bytes': str(i * 100000), 'total': '1000000', 'state': 'running'})
        f = p.files['k.tar.gz']
        self.assertEqual((p.file, f['bytes']), ('k.tar.gz', 400000))
        self.assertAlmostEqual(f['speed'], 100000.0, delta=1)
        self.assertAlmostEqual(f['eta'], 6.0, delta=0.1)
        self.assertEqual(p.overall(), 20 + int(40 * 0.4))
        p.plan_total = 4000000                              # the window knows there are more files to come
        self.assertEqual(p.overall(), 24)
        p.event({'event': 'download', 'file': 'k.tar.gz', 'bytes': '1000000', 'total': '1000000', 'state': 'done'})
        p.event({'event': 'verify', 'file': 'k.tar.gz', 'result': 'ok'})
        self.assertEqual((p.file, f['verify'], f['eta']), ('', 'ok', None))
        p.event({'event': 'verify', 'file': 'cached.tar.gz', 'result': 'cached'})
        self.assertEqual(p.files['cached.tar.gz']['verify'], 'cached')
        p.event({'event': 'download', 'file': 'x', 'bytes': 'many', 'total': '1', 'state': 'running'})      # nonsense is ignored
        self.assertNotIn('x', p.files)

    def test_stopping_is_only_offered_while_it_is_safe(self):
        p = self.progress()
        for sid in ('preflight', 'storage', 'choices', 'pull', 'download', 'vendor', 'bootimage', 'confirm', 'copy'):
            p.event({'event': 'step', 'id': sid, 'percent': '1', 'title': sid})
            self.assertTrue(p.can_cancel(), sid)
        for sid in ('device', 'boot', 'switch', 'done'):
            p.event({'event': 'step', 'id': sid, 'percent': '1', 'title': sid})
            self.assertFalse(p.can_cancel(), sid)               # the installer is writing to the device
        u = self.progress('uninstall')
        for sid in ('preflight', 'find', 'choices', 'confirm'):
            u.event({'event': 'step', 'id': sid, 'percent': '1', 'title': sid})
            self.assertTrue(u.can_cancel(), sid)
        u.event({'event': 'step', 'id': 'misc', 'percent': '40', 'title': 'misc'})
        self.assertFalse(u.can_cancel())
        m = model.Progress(model.MAKEROOM_STEPS, kind='makeroom')
        m.event({'event': 'step', 'id': 'storage', 'percent': '6', 'title': 'x'})
        self.assertTrue(m.can_cancel())
        m.event({'event': 'step', 'id': 'repartition', 'percent': '50', 'title': 'x'})
        self.assertFalse(m.can_cancel())                        # the partition table is being rewritten

    def test_questions_and_the_end(self):
        p = self.progress()
        p.event({'event': 'question', 'id': 'MU300_ANS_KERNEL', 'text': 'Kernel', 'default': '1', 'answer': '2'})
        self.assertEqual((p.waiting, len(p.questions)), (None, 1))
        p.event({'event': 'step', 'id': 'confirm', 'percent': '72', 'title': 'Ready to install'})
        p.line('  systems: ubuntu')
        p.event({'event': 'question', 'id': 'MU300_ANS_CONFIRM', 'text': 'Type INSTALL', 'default': 'no', 'state': 'waiting'})
        self.assertEqual(p.waiting['id'], 'MU300_ANS_CONFIRM')
        self.assertEqual(p.heading_lines, ['  systems: ubuntu'])
        p.event({'event': 'question', 'id': 'MU300_ANS_CONFIRM', 'text': 'Type INSTALL', 'default': 'no', 'answer': 'no'})
        self.assertIsNone(p.waiting)
        p.event({'event': 'error', 'message': 'cancelled'})
        p.end(1)
        self.assertEqual((p.ok, p.error, p.state('confirm'), p.finished), (False, 'cancelled', 'failed', True))
        # a run that ends without saying how has failed
        q = self.progress()
        q.event({'event': 'step', 'id': 'pull', 'percent': '15', 'title': 'x'})
        q.end(137)
        self.assertFalse(q.ok)
        self.assertIn('137', q.error)
        self.assertEqual(q.state('pull'), 'failed')
        # ... and so has one that exits 0 without ever saying done
        r = self.progress()
        r.end(0)
        self.assertFalse(r.ok)


class Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_event_lines(self):
        self.assertEqual(runner.parse_line('@@mu300 {"event":"step","id":"pull","percent":"15","title":"x"}\n'),
                         ('event', {'event': 'step', 'id': 'pull', 'percent': '15', 'title': 'x'}))
        self.assertEqual(runner.parse_line('﻿@@mu300 {"event":"hello","version":1}\r\n'), ('event', {'event': 'hello', 'version': '1'}))
        self.assertEqual(runner.parse_line('@@mu300 {"event":"info","key":"m","value":"T\\u00fcrk\\u00e7e"}'),
                         ('event', {'event': 'info', 'key': 'm', 'value': 'Türkçe'}))
        for text in ('plain text', '@@mu300 not json', '@@mu300 [1]', '@@mu300 {"no":"event"}', '  @@mu300 {"event":"x"}', ''):
            self.assertEqual(runner.parse_line(text + '\n')[0], 'line', text)

    @unittest.skipUnless(POSIX, 'file permissions')
    def test_the_answers_file_is_private_from_its_first_byte(self):
        d = runner.private_dir({'XDG_RUNTIME_DIR': str(self.tmp / 'run')})
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        old = os.umask(0)                               # whatever the umask
        try:
            path = runner.write_answers({'MU300_ANS_KERNEL': '6.18', 'MU300_ANS_PASSWORD': 'hunter2 = secret # x'}, d)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(Path(path).read_text(), 'MU300_ANS_KERNEL=6.18\nMU300_ANS_PASSWORD=hunter2 = secret # x\n')
        self.assertEqual(os.path.dirname(path), d)
        # install.sh takes it: its own check of the file's owner and permissions passes
        code = f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; . "$TOP/tools/answers.sh"; ANSWERS_FILE="{path}"; answers_setup; echo "k=$MU300_ANS_KERNEL"'
        r = subprocess.run(['sh', '-c', code], capture_output=True, text=True)
        self.assertEqual(r.stdout, 'k=6.18\n', r.stderr)
        runner.remove(path)
        self.assertFalse(os.path.exists(path))
        runner.remove(path)                             # twice is fine
        with self.assertRaises(ValueError):
            runner.write_answers({'MU300_ANS_PASSWORD': 'two\nMU300_ANS_CONFIRM=INSTALL'}, d)      # never a second line
        self.assertEqual(os.listdir(d), [])

    def test_the_environment_of_a_run(self):
        env = runner.base_env('tr', 'SERIAL', '/opt/pt/adb', {'PATH': '/usr/bin', 'MU300_ANS_PASSWORD': 'leak', 'MU300_ANS_KERNEL': '1',
                                                              'MU300_ANSWERS': '/x', 'ANDROID_SERIAL': 'other', 'HOME': '/h'})
        self.assertEqual((env['MU300_LANG'], env['ANDROID_SERIAL'], env['MU300_PROGRESS'], env['MU300_FROM_GUI'], env['MU300_GUI'],
                          env['MU300_NO_SELF_UPDATE']), ('tr', 'SERIAL', '1', '1', '0', '1'))
        self.assertEqual(env['PATH'], '/opt/pt' + os.pathsep + '/usr/bin')
        self.assertFalse([k for k in env if k.startswith('MU300_ANS_')])     # the answers come from the file alone
        self.assertNotIn('MU300_ANSWERS', env)
        self.assertNotIn('ANDROID_SERIAL', runner.base_env('en', '', '', {'ANDROID_SERIAL': 'other'}))

    def collect(self, run, answer=None, timeout=30):
        events, lines, status = [], [], None
        end = time.time() + timeout
        while time.time() < end:
            try:
                kind, payload = run.messages.get(timeout=0.2)
            except Exception:
                continue
            if kind == 'event':
                events.append(payload)
                if payload.get('state') == 'waiting' and answer is not None:
                    run.send(answer)
            elif kind == 'line':
                lines.append(payload)
            else:
                status = payload
                break
        return events, lines, status

    def start(self, answers, **env):
        e = dict(os.environ, MU300_GUI_INSTALLER=str(TESTS / 'fakeinstaller.py'), FAKEINSTALL_RECORD=str(self.tmp / 'record.json'),
                 XDG_RUNTIME_DIR=str(self.tmp / 'run'), LOCALAPPDATA=str(self.tmp / 'local'), FAKEINSTALL_SPEED='0.002')
        e.update(env)
        old = dict(os.environ)
        os.environ.update({k: e[k] for k in ('XDG_RUNTIME_DIR', 'LOCALAPPDATA')})
        try:
            return runner.start(str(TOP), 'install', [], 'en', serial='324950664950', answers=answers, environ=e)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_a_whole_run(self):
        a = model.answers(good_choices(), facts(), 'en', '324950664950')
        run = self.start(a)
        path = run.answers_file
        self.assertTrue(os.path.exists(path))
        events, lines, status = self.collect(run, answer='INSTALL')
        self.assertEqual(status, 0, lines[-5:])
        self.assertEqual(events[0]['event'], 'hello')
        self.assertEqual(events[-1]['event'], 'done')
        self.assertEqual([e['id'] for e in events if e['event'] == 'step' and e['id']], [s for s, _l in model.INSTALL_STEPS])
        self.assertFalse(os.path.exists(path))              # the answers file is gone as soon as the run is
        self.assertEqual(os.listdir(os.path.dirname(path)), [])
        rec = json.loads((self.tmp / 'record.json').read_text())
        self.assertEqual(rec['answers']['MU300_ANS_PASSWORD'], 'hunter2secret')
        self.assertEqual(rec['env_answers'], [])            # nothing of the answers in the environment
        self.assertEqual((rec['env']['MU300_PROGRESS'], rec['env']['MU300_FROM_GUI'], rec['env']['MU300_GUI'], rec['env']['ANDROID_SERIAL']),
                         ('1', '1', '0', '324950664950'))
        if POSIX:
            self.assertEqual(rec['answers_mode'], 0o600)
        self.assertNotIn('hunter2secret', ' '.join(rec['argv']))        # and nothing of them on the command line

    def test_no_to_the_summary_writes_nothing(self):
        run = self.start(model.answers(good_choices(), facts(), 'en', 'S'))
        events, lines, status = self.collect(run, answer='no')
        self.assertEqual(status, 1)
        self.assertEqual(events[-1], {'event': 'error', 'message': 'cancelled'})
        self.assertNotIn('copy', [e.get('id') for e in events if e['event'] == 'step'])

    def test_stop(self):
        run = self.start(model.answers(good_choices(), facts(), 'en', 'S'), FAKEINSTALL_SPEED='0.2')
        path = run.answers_file
        deadline = time.time() + 20
        while time.time() < deadline:
            kind, payload = run.messages.get(timeout=20)
            if kind == 'event' and payload.get('id') == 'download':
                break
        run.stop()
        self.assertFalse(os.path.exists(path))              # at once, not when the process has gone
        events, lines, status = self.collect(run)
        self.assertNotEqual(status, 0)
        self.assertFalse(run.running)
        self.assertFalse(run.send('INSTALL'))

    def test_a_program_that_is_not_there(self):
        r = runner.Run(['/nonexistent/installer'], dict(os.environ))
        events, lines, status = self.collect(r)
        self.assertEqual(status, 127)
        self.assertIn('cannot start', lines[0])


class Desktop(unittest.TestCase):
    def test_ssh_command_is_made_of_plain_words(self):
        self.assertEqual(desktop.ssh_command('ubuntu', '192.168.77.1'), ['ssh', 'ubuntu@192.168.77.1'])
        for user, ip in (('root; rm -rf ~', '192.168.77.1'), ('ubuntu', '192.168.77.1; id'), ('ubuntu', 'evil.example'), ('-oProxyCommand=x', '1.2.3.4'),
                         ('', '1.2.3.4'), ('ubuntu', '')):
            self.assertIsNone(desktop.ssh_command(user, ip), (user, ip))

    def test_terminals(self):
        cmd = ['ssh', 'ubuntu@192.168.77.1']
        self.assertEqual(desktop.terminal_argv(cmd, 'darwin')[:3], ['osascript', '-e', 'tell application "Terminal" to do script "ssh ubuntu@192.168.77.1"'])
        self.assertEqual(desktop.terminal_argv(cmd, 'win32'), ['cmd.exe', '/c', 'start', 'MU300 Linux', 'cmd.exe', '/k'] + cmd)
        self.assertEqual(desktop.terminal_argv(cmd, 'linux', which=lambda t: t == 'konsole'), ['konsole', '-e'] + cmd)
        self.assertEqual(desktop.terminal_argv(cmd, 'linux', which=lambda t: t == 'gnome-terminal'), ['gnome-terminal', '--'] + cmd)
        self.assertIsNone(desktop.terminal_argv(cmd, 'linux', which=lambda t: None))
        for bad in (['ssh', 'a"; do shell script "id'], ['ssh', 'x y'], ['ssh', '$(id)'], ['ssh', 'a&b']):
            for plat in ('darwin', 'win32', 'linux'):
                self.assertIsNone(desktop.terminal_argv(bad, plat, which=lambda t: '/usr/bin/' + t), bad)

    def test_links(self):
        self.assertFalse(desktop.open_url('file:///etc/passwd'))
        self.assertFalse(desktop.open_url('javascript:alert(1)'))
        self.assertFalse(desktop.open_url('https://x.example/ " && calc'))

    def test_free_space_of_a_folder_that_is_not_there_yet(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertGreater(desktop.free_bytes(os.path.join(d, 'work', 'release', 'v1')), 0)
        self.assertEqual(desktop.work_dir('/p', {}), os.path.join('/p', 'work'))
        self.assertEqual(desktop.work_dir('/p', {'MU300_WORK': '/elsewhere'}), '/elsewhere')


@unittest.skipUnless(POSIX and shutil.which('sh'), 'the controller is driven through install.sh here; tests/answers.Tests.ps1 covers install.ps1')
class Control(unittest.TestCase):
    """The controller with the fake adb in front of the real install.sh (the check, the backup) and the fake
    installer for the rest. A scratch HOME; nothing can reach a real device or GitHub."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / 'device.json'
        (self.tmp / 'home').mkdir()
        self.ctl = None
        self._fetch = releases.fetch
        releases.fetch = lambda **kw: {'latest': 'v2026.10.23', 'beta': None, 'files': {}}
        self._env = dict(os.environ)
        i18n.set_language('en')

    def tearDown(self):
        if self.ctl is not None:
            self.ctl.close()
        releases.fetch = self._fetch
        os.environ.clear()
        os.environ.update(self._env)

    def device(self, **keys):
        self.state.write_text(json.dumps(keys))
        subprocess.run([sys.executable, str(TESTS / 'fakeadb.py'), '--stub', str(self.tmp / 'bin'), str(self.state)], check=True)
        for tool in ('lz4', 'curl'):
            if not shutil.which(tool):
                p = self.tmp / 'bin' / tool
                p.write_text('#!/bin/sh\nexit 1\n')
                p.chmod(0o755)

    def controller(self, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith('MU300_') and k != 'ANDROID_SERIAL'}
        e.update(MU300_ADB=str(self.tmp / 'bin' / 'adb'), FAKEADB=str(self.state), MU300_WORK=str(self.tmp / 'work'),
                 MU300_GUI_INSTALLER=str(TESTS / 'fakeinstaller.py'), FAKEINSTALL_SPEED='0.002', FAKEINSTALL_RECORD=str(self.tmp / 'record.json'),
                 HOME=str(self.tmp / 'home'), XDG_RUNTIME_DIR=str(self.tmp / 'run'), MU300_IP='127.0.0.1',
                 PATH=str(self.tmp / 'bin') + os.pathsep + e.get('PATH', ''))
        e.update(env)
        os.environ['XDG_RUNTIME_DIR'] = e['XDG_RUNTIME_DIR']
        self.topics = []
        self.ctl = Controller(top=str(TOP), environ=e, poll_seconds=0.2, linux_probe=lambda: '')
        self.ctl.listen(lambda t: self.topics.append(t))
        self.ctl.start()
        return self.ctl

    def until(self, cond, timeout=60, what=''):
        end = time.time() + timeout
        while time.time() < end:
            self.ctl.pump()
            if cond():
                return
            time.sleep(0.02)
        self.fail('timed out waiting for %s; log:\n%s' % (what or cond, '\n'.join(self.ctl.log[-30:])))

    def checked(self, **keys):
        self.device(**keys)
        c = self.controller()
        self.until(lambda: c.device is not None, what='the device')
        self.assertTrue(c.check())
        self.until(lambda: c.check_state in ('ok', 'failed'), what='the check')
        return c

    def test_the_device_is_found_and_checked(self):
        c = self.checked()
        self.assertEqual((c.device_state(), c.serial, c.kind(), c.check_state), ('android', '324950664950', 'f50', 'ok'))
        f = c.facts
        self.assertEqual((f.root, f.bootloader, f.existing, f['verdict.code'], f.checked), (True, 'unlocked', False, 'tested', True))
        self.assertEqual(f.num('region.size'), (122150912 - 54222848) * 512)
        self.assertEqual(f.need('both'), 2400 << 20)
        self.assertEqual(model.layout(f)[-2]['kind'], 'free')
        self.until(lambda: c.props.get('ro.product.model') == 'F50', what='the properties')
        self.assertEqual(c.problems(), [('password', 'The password needs 6 characters or more.')])
        # the fake's log: the check only read
        rows = [json.loads(l) for l in Path(str(self.state) + '.log').read_text().splitlines()]
        self.assertEqual([r['cmd'] for r in rows if r['kind'] in ('write', 'unknown', 'reboot')], [])

    def test_no_root(self):
        c = self.checked(root=False)
        self.assertEqual((c.check_state, c.facts.root, c.check_error), ('failed', False, 'su does not work on the device'))
        self.assertEqual(c.facts.get('device.model'), 'F50 / MU300')           # what needs no root is still known
        self.assertEqual([p[0] for p in c.problems()][:1], ['device'])
        self.assertFalse(c.install())

    def test_states_that_are_no_device_to_install_to(self):
        self.device(adb_state='unauthorized')
        c = self.controller()
        self.until(lambda: c.devices_seen and c.device is not None)
        self.assertEqual(c.device_state(), 'unauthorized')
        self.assertFalse(c.check())
        self.device(adb_state='none')
        self.until(lambda: c.device is None)
        self.assertEqual(c.device_state(), 'none')
        # a tablet next to nothing of ours: listed, never chosen by itself
        self.device(adb_state='none', extra_devices=['d16d93c5               device usb:3 product:nabu_global model:21051182G device:nabu transport_id:3'])
        self.until(lambda: len(c.devices) == 1)
        self.assertEqual((c.device, c.device_state()), (None, 'choose'))
        # a device in our Linux
        self.device(serial='MU300LINUX-324950664950', model='MU300_Linux', device='mu300')
        self.until(lambda: c.device is not None and c.device.linux)
        self.assertEqual(c.device_state(), 'linux')
        self.assertFalse(c.check())
        self.assertFalse(c.install())

    def test_a_device_that_is_not_ours_is_checked_only_on_request(self):
        self.device(model='Pixel 7', device='panther')
        c = self.controller()
        self.until(lambda: len(c.devices) == 1)
        self.assertIsNone(c.device)
        c.select('324950664950')
        self.assertEqual(c.device_state(), 'other')
        self.assertTrue(c.check())
        self.until(lambda: c.check_state in ('ok', 'failed'))
        self.assertEqual((c.check_state, c.facts.kind), ('failed', 'unknown'))     # the installer's question was answered no
        c.choices.unknown_model = True
        self.assertTrue(c.check())
        self.until(lambda: c.check_state in ('ok', 'failed') and not c.busy())
        self.assertEqual(c.check_state, 'ok')

    def install(self, c, answer='INSTALL'):
        c.choices.password = c.choices.password2 = 'hunter2secret'
        self.assertEqual(c.problems(), [])
        self.assertTrue(c.install())
        self.until(lambda: c.progress.waiting is not None or not c.busy(), what='the summary')
        return c.progress

    def test_an_installation_from_the_first_click_to_the_last_page(self):
        c = self.checked()
        c.choices.kernel = '6.18'
        p = self.install(c)
        self.assertEqual(p.waiting['id'], 'MU300_ANS_CONFIRM')
        self.assertIn({'confirm'}, [t & {'confirm'} for t in self.topics])
        self.assertTrue(any('systems:' in l for l in p.heading_lines))             # the installer's own summary, to show
        self.assertTrue(p.can_cancel())
        self.assertEqual(p.state('download'), 'done')
        self.assertEqual([f['verify'] for f in p.files.values()], ['ok'] * 4)
        self.assertTrue(c.answer('INSTALL'))
        self.until(lambda: not c.busy(), what='the end')
        self.assertTrue(p.ok, p.error)
        self.assertEqual([s[2] for s in p.steps], ['done'] * len(model.INSTALL_STEPS))
        self.assertEqual(p.overall(), 100)
        self.assertEqual((c.installed['kind'], c.installed['boot'], c.installed['done']['ip']), ('f50', 'ubuntu', '192.168.77.1'))
        self.assertEqual(c.check_state, 'idle')                                    # the device is another one now
        rec = json.loads((self.tmp / 'record.json').read_text())
        self.assertEqual((rec['answers']['MU300_ANS_KERNEL'], rec['answers']['MU300_ANS_CONFIRM'], rec['answers']['ANDROID_SERIAL']),
                         ('6.18', '@stdin', '324950664950'))
        self.assertFalse(os.path.exists(rec['answers_file']))
        # the fake installer printed the serial number, an IMEI, an IMSI, an ICCID and the password: none of it is
        # in the log, in what is saved, in what is copied, or in a diagnostics report
        out = self.tmp / 'log.txt'
        c.save_log(str(out))
        for text in ('\n'.join(c.log), out.read_text(), c.log_text(), c.diagnostics()):
            for secret in ('324950664950', '867530912345678', '8990011234567890123', '286011234567890', 'hunter2secret', str(self.tmp / 'home')):
                self.assertNotIn(secret, text)
            self.assertIn('Ready to install', text)
        self.assertIn('<serial>', c.log_text())
        self.assertIn('device state:', c.diagnostics())

    def test_no_to_the_summary(self):
        c = self.checked()
        p = self.install(c)
        self.assertTrue(c.answer('no'))
        self.until(lambda: not c.busy())
        self.assertEqual((p.ok, p.error, p.state('copy'), p.state('device')), (False, 'cancelled', 'pending', 'pending'))
        self.assertIsNone(c.installed)

    def test_stop_before_anything_is_written(self):
        c = self.checked()
        c.environ['FAKEINSTALL_SPEED'] = '0.1'
        c.choices.password = c.choices.password2 = 'hunter2secret'
        self.assertTrue(c.install())
        self.until(lambda: c.progress.current == 'download')
        self.assertTrue(c.cancel())
        self.until(lambda: not c.busy())
        self.assertFalse(c.progress.ok)
        self.assertEqual(os.listdir(self.tmp / 'run' / 'mu300-linux'), [])        # no answers file left behind

    def test_no_stop_while_the_device_is_written(self):
        c = self.checked()
        c.environ['FAKEINSTALL_SPEED'] = '0.05'
        p = self.install(c)
        c.answer('INSTALL')
        self.until(lambda: p.current in ('device', 'boot') or not c.busy())
        if c.busy():
            self.assertFalse(c.cancel())                    # refused: the run goes on to its end
            self.until(lambda: not c.busy())
        self.assertTrue(p.ok, p.error)

    def test_a_failure_is_shown_as_what_the_installer_said(self):
        c = self.checked()
        c.environ['FAKEINSTALL'] = 'fail-download'
        p = self.install(c)
        self.until(lambda: not c.busy())
        self.assertEqual((p.ok, p.state('download'), p.state('copy')), (False, 'failed', 'pending'))
        self.assertIn('download of', p.error)
        c.environ['FAKEINSTALL'] = 'fail-device'
        c.check(); self.until(lambda: c.check_state == 'ok' and not c.busy())
        p = self.install(c)
        c.answer('INSTALL')
        self.until(lambda: not c.busy())
        self.assertEqual((p.ok, p.state('device'), p.state('boot')), (False, 'failed', 'pending'))
        self.assertIn('boot_b and misc were not changed', p.error)

    def test_one_run_at_a_time(self):
        c = self.checked()
        c.environ['FAKEINSTALL_SPEED'] = '0.05'
        self.install(c)
        self.assertTrue(c.busy())
        self.assertFalse(c.check())
        self.assertFalse(c.backup_now())
        self.assertFalse(c.uninstall('keep', 'keep'))
        c.answer('no')
        self.until(lambda: not c.busy())

    def test_backup(self):
        c = self.checked(boot_image_bytes=70000)
        self.assertTrue(c.backup_now())
        self.until(lambda: not c.busy())
        self.assertTrue(c.last['backup'].ok, c.last['backup'].error)
        self.assertEqual(os.path.getsize(c.backup['backup.boot']), 70000)
        self.assertEqual(len(c.backup['backup.sha256']), 64)
        rows = [json.loads(l) for l in Path(str(self.state) + '.log').read_text().splitlines()]
        self.assertEqual([r['cmd'] for r in rows if r['kind'] in ('write', 'unknown', 'reboot')], [])
        self.assertNotRegex(' '.join(r['cmd'] for r in rows), r'prodnv|fixnv|runtimenv|calinv|nr_phy|miscdata')

    def test_uninstall(self):
        c = self.checked(existing=True)
        self.assertTrue(c.uninstall('quick', 'keep'))
        self.until(lambda: c.progress.waiting is not None or not c.busy())
        p = c.progress
        self.assertEqual(p.waiting['id'], 'MU300_ANS_CONFIRM')
        self.assertTrue(any('boot_b' in l for l in p.heading_lines))
        c.answer('UNINSTALL')
        self.until(lambda: not c.busy())
        self.assertTrue(p.ok, p.error)
        rec = json.loads((self.tmp / 'record.json').read_text())
        self.assertEqual((rec['answers']['MU300_ANS_WIPE'], rec['answers']['MU300_ANS_SD_WIPE'], rec['answers']['MU300_ANS_GROW']), ('quick', 'keep', 'no'))
        self.assertNotIn('MU300_ANS_ERASE_ANDROID', rec['answers'])

    def test_what_fits_decides_the_first_choices(self):
        c = self.checked(sd={'dev': 'mmcblk1', 'sectors': 124735488, 'partition': True, 'type': 'SD', 'state': 'yes'})
        self.assertEqual(c.choices.storage, 'sd')          # a card that holds an installation is what the device starts
        self.assertTrue(c.choices.place_existing(c.facts))
        self.assertFalse(c.choices.formats(c.facts))

    def test_commands_on_the_device(self):
        self.device(serial='MU300LINUX-324950664950', model='MU300_Linux', device='mu300')
        c = self.controller()
        self.until(lambda: c.device is not None and c.device.linux)
        self.assertFalse(c.command('rm -rf /'))             # only the commands of the table
        self.assertFalse(c.command('to-linux'))             # that one is for a device in Android
        self.assertTrue(c.command('update-check'))
        self.until(lambda: not c.busy())
        rows = [json.loads(l) for l in Path(str(self.state) + '.log').read_text().splitlines()]
        self.assertIn('/opt/mu300/bin/mu300-update check', [r['cmd'] for r in rows])

    def test_a_device_in_linux_says_what_it_runs(self):
        line = '{"mu300":1,"device":"f50","system":"ubuntu","version_id":"24.04","release":"v2026.10.23","kernel":"6.18.21","root":true,"adb":"open"}'
        self.device(serial='MU300LINUX-324950664950', model='MU300_Linux', device='mu300', linux_json=line)
        c = self.controller()
        self.until(lambda: c.linux.get('release') == 'v2026.10.23', what='mu300-device --json')
        self.assertEqual((c.linux['system'], c.linux['kernel'], c.linux['json'], c.kind()), ('ubuntu', '6.18.21', True, 'f50'))
        # a system from before that command: the window degrades to what it can ask (here: nothing answers), and
        # the device is still known to be in Linux
        self.device(serial='MU300LINUX-324950664950', model='MU300_Linux', device='mu300', adb_state='unauthorized')
        self.until(lambda: c.device is not None and c.device.state == 'unauthorized')
        self.assertEqual(c.linux, {})
        # no adb at all, but SSH answers on the USB network
        c.linux_probe = lambda: '192.168.77.1'
        self.device(adb_state='none')
        self.until(lambda: c.device_state() == 'linux-ssh')
        self.assertEqual(c.linux_ip, '192.168.77.1')


def display():
    """A display for the smoke test: the one there is, or an Xvfb started for it. (None, None) where there is none."""
    if sys.platform in ('darwin', 'win32') or os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        return os.environ.get('DISPLAY', ''), None
    xvfb = shutil.which('Xvfb')
    if not xvfb:
        return None, None
    num = ':%d' % (90 + os.getpid() % 9)
    proc = subprocess.Popen([xvfb, num, '-screen', '0', '1280x900x24', '-nolisten', 'tcp'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    if proc.poll() is not None:
        return None, None
    return num, proc


class Window(unittest.TestCase):
    """The widgets themselves: every page is built, in every language and in both appearances, from a controller
    that holds what a check and a run reported. Skipped where there is no tkinter, no Tk 8.6 or no display."""

    def test_every_page_builds(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest('no tkinter in this Python')
        if tk.TkVersion < 8.6:
            self.skipTest('Tk %s: the window needs 8.6' % tk.TkVersion)
        if os.environ.get('MU300_TEST_NO_WINDOW'):
            self.skipTest('MU300_TEST_NO_WINDOW')
        disp, xvfb = display()
        if disp is None:
            self.skipTest('no display and no Xvfb')
        old = os.environ.get('DISPLAY')
        if xvfb is not None:
            os.environ['DISPLAY'] = disp
        try:
            try:
                root = tk.Tk()
            except tk.TclError as e:
                self.skipTest('no window can be opened here: %s' % e)
            self.pages(root, tk)
        finally:
            i18n.set_language('en')
            if xvfb is not None:
                xvfb.terminate()
                if old is None:
                    os.environ.pop('DISPLAY', None)
                else:
                    os.environ['DISPLAY'] = old

    def pages(self, root, tk):
        from mu300gui import app as appmod
        from mu300gui.theme import Theme
        errors = []
        root.report_callback_exception = lambda *exc: errors.append(exc)
        theme = Theme(root, dark=False)
        ctl = Controller(top=str(TOP), environ={'MU300_ADB': '/nonexistent/adb', 'PATH': os.environ.get('PATH', '')})
        ctl.adb_path = '/nonexistent/adb'
        a = appmod.App(root, ctl, theme)
        root.after_cancel(a._after)

        def texts(widget):
            out = []
            for child in widget.winfo_children():
                try:
                    out.append(str(child.cget('text')))
                except tk.TclError:
                    pass
                out += texts(child)
            return out

        def all_widgets(widget):
            out = []
            for child in widget.winfo_children():
                try:
                    child.cget('text')
                    out.append(child)
                except tk.TclError:
                    pass
                out += all_widgets(child)
            return out

        def show(section, step=None):
            a.goto(section, step)
            root.update_idletasks()
            return ' | '.join(texts(root))

        f50 = adb.parse_devices('List of devices attached\n324950664950 device usb:1 product:MU300 model:F50 device:MU300 transport_id:1\n')
        # nothing attached, then states that are no device to install to
        self.assertIn('No device answers on adb yet.', show('install', 0))
        ctl.adb_path = ''
        self.assertIn('adb was not found', show('install', 0))
        ctl.adb_path = '/nonexistent/adb'
        ctl.devices = adb.parse_devices('List of devices attached\n0123456789ABCDEF unauthorized usb:5 transport_id:5\n')
        ctl.serial = '0123456789ABCDEF'
        self.assertIn('has not authorised this computer', show('install', 0))
        ctl.devices = adb.parse_devices('List of devices attached\nMU300LINUX-324950664950 device usb:1 product:mu300\n')
        ctl.serial = 'MU300LINUX-324950664950'
        ctl.linux = {'mu300': 1, 'device': 'f50', 'system': 'ubuntu', 'version_id': '24.04', 'release': 'v2026.10.23', 'kernel': '6.18.21', 'adb': 'open', 'json': True}
        self.assertIn('is running MU300 Linux', show('install', 0))
        page = show('manage')
        self.assertIn('v2026.10.23', page)
        self.assertIn('Back to Android', page)
        ctl.linux = {}
        self.assertIn('Use SSH below', show('manage'))
        # a checked device, every page of the wizard
        ctl.devices, ctl.serial, ctl.linux = f50, '324950664950', {}
        ctl.facts, ctl.facts_serial, ctl.check_state = facts(verdict='OK: free and empty'), '324950664950', 'ok'
        ctl.releases = {'latest': 'v2026.10.23', 'beta': 'v2026.10.25', 'files': {'v2026.10.23': {'mu300-kernel.tar.gz': 26971198}}}
        page = show('install', 0)
        self.assertIn('ZTE F50 / MU300', page)
        self.assertIn('27.0:MAGISKSU', page)
        self.assertIn('does not root, unlock or patch devices', page)
        self.assertNotIn('The bootloader is locked.', page)
        self.assertNotIn('324950664950', page)              # the serial number is masked until asked for
        a.show_serial = True
        self.assertIn('324950664950', show('install', 0))
        a.show_serial = False
        self.assertIn('32.4 GiB', show('install', 1))
        page = show('install', 2)
        self.assertIn('The beta: v2026.10.25', page)
        self.assertIn('Ubuntu LTS', page)
        self.assertIn('disabled', a.w['next'].state())                        # no password yet
        a.v['password'].set('hunter2secret'); a.v['password2'].set('hunter2secret')
        root.update()
        self.assertNotIn('disabled', a.w['next'].state())       # (the pointer may be over it: active, hover)
        self.assertEqual(ctl.choices.password, 'hunter2secret')
        a.v['ubuntu_version'].set('26.04')
        root.update()
        self.assertIn('Ubuntu 26.04 needs a mainline kernel', str(a.w['problems'].cget('text')))
        a.v['kernel'].set('6.18')
        a.v['release_mode'].set('beta')
        root.update()
        self.assertEqual((ctl.choices.kernel, ctl.choices.release, ctl.problems()), ('6.18', 'v2026.10.25', []))
        a.advanced = True
        self.assertIn('Parallel connections per download', show('install', 2))
        self.assertIn('Never copied by the installer', show('install', 3))
        self.assertIn('Ubuntu 26.04', show('install', 4))
        # started from an unpacked Magisk zip: its system and kernel, nothing to choose about them, nothing to download
        import copy
        saved = ctl.choices
        pkg = {'dir': '/somewhere/mu300-magisk-v2026.10.23-openwrt-luci-k7.2', 'tag': 'v2026.10.23', 'system': 'openwrt-luci',
               'os': 'openwrt-luci', 'ubuntu': '', 'kernel': '7.2', 'files': {}, 'bytes': 45 << 20}
        ctl.choices = copy.copy(saved)
        ctl.choices.apply_package(pkg)
        ctl.package = pkg
        a._sync()
        page = show('install', 2)
        self.assertIn('This package', page)
        self.assertIn('OpenWrt with the MU300 control panel', page)
        for gone in ('Ubuntu LTS', 'The beta: v2026.10.25', 'The web panel for Ubuntu'):
            self.assertNotIn(gone, page)
        self.assertEqual(ctl.problems(), [])
        self.assertIn('this unpacked Magisk zip, nothing downloaded', show('install', 4))
        ctl.package = {'dir': '/somewhere/else', 'error': 'manifest'}
        self.assertIn('is not an unpacked Magisk zip of a release that this installer can read', show('install', 2))
        ctl.choices, ctl.package = saved, None
        a._sync()
        # other devices: no root, a locked bootloader, data in the free space, a card, no room
        ctl.facts = facts(root='no'); ctl.check_state = 'failed'; ctl.check_error = 'su does not work on the device'
        self.assertIn('Without root nothing can be installed', show('install', 0))
        # a locked bootloader: the question about TrustOS, in the warning colour, and nothing is blocked
        ctl.facts = facts(bootloader__locked='1')
        ctl.check_state = 'ok'
        page = show('install', 0)
        self.assertIn('The bootloader is locked. Did you patch TrustOS?', page)
        self.assertNotIn('disabled', a.w['next'].state())
        locked = [w for w in all_widgets(root) if str(w.cget('text')) == 'locked']
        self.assertEqual([str(w.cget('style')) for w in locked], ['Card.WarnText.TLabel'])
        self.assertEqual(ctl.problems(), [])
        self.assertIn('The bootloader is locked. Did you patch TrustOS?', show('install', 4))     # again before the start
        ctl.facts = facts(bootloader__locked='', bootloader__device_state='')
        self.assertIn('not reported', show('install', 0))
        ctl.check_state = 'ok'
        ctl.facts = facts(region__dirty='3', sd__device='/dev/block/mmcblk1p1', sd__bytes='63864569856', sd__state='yes')
        page = show('install', 1)
        self.assertIn('Type overwrite to use it anyway', page)
        self.assertIn('holds a MU300 Linux installation', page)
        ctl.facts = facts(region__size='0', makeroom__first='12275712', makeroom__end='122155007', makeroom__usable_end='122155007',
                          makeroom__min_android='8388608', makeroom__min_linux='1638400', makeroom__max_linux='101490688')
        page = show('install', 1)
        self.assertIn('Make room now', page)
        self.assertIn("restarts into Android's own recovery", page)          # the reset, and how it goes on (tools/resume.sh)
        # a prepared installation waiting (tools/resume.sh): what its checks said
        ctl.facts = facts(resume__transaction='/home/u/mu300-backups/x/transaction.json', resume__check='RESUME-OK', verdict__code='resume')
        self.assertIn('A prepared installation waits for this device (EXPERIMENTAL', show('install', 1))
        ctl.facts = facts(resume__transaction='/home/u/mu300-backups/x/transaction.json', resume__check='RESUME-REFUSED: preparation token mismatch')
        self.assertIn('it cannot be continued: preparation token mismatch', show('install', 1))
        # a check that fitted the choices to the device: the widgets follow (a card that holds an installation)
        ctl.choices.storage = 'sd'
        a.on_change({'check'})
        root.update()
        self.assertEqual(a.v['storage'].get(), 'sd')
        ctl.choices.storage = 'internal'
        a.on_change({'check'})
        root.update()
        ctl.facts = facts(region__existing='yes')
        page = show('install', 2)
        self.assertIn('Linux is already installed there', page)
        self.assertIn('Keep the current password', page)                    # an update keeps it, checked
        self.assertIn('Current password', page)
        a.v['keep_password'].set(False)
        root.update()
        self.assertNotIn('Current password', show('install', 2))
        a.v['keep_password'].set(True)
        a.v['existing'].set('wipe')
        root.update()
        self.assertNotIn('Keep the current password', show('install', 2))  # nothing is kept by a wipe
        a.v['existing'].set('update')
        root.update()
        self.assertIn('Uninstall', show('manage'))
        # a run, as its events come in
        p = model.Progress(model.INSTALL_STEPS, kind='install')
        ctl.progress, ctl.run_kind = p, 'install'
        a._show_result = True
        for ev in ({'event': 'step', 'id': 'download', 'percent': '20', 'title': 'Downloading release v2026.10.23'},
                   {'event': 'download', 'file': 'mu300-kernel.tar.gz', 'bytes': '1000', 'total': '26971198', 'state': 'start'},
                   {'event': 'download', 'file': 'mu300-kernel.tar.gz', 'bytes': '13000000', 'total': '26971198', 'state': 'running'},
                   {'event': 'verify', 'file': 'mu300-ubuntu-rootfs.tar.gz', 'result': 'ok'}):
            p.event(ev)
        ctl.log += ['==> Downloading release v2026.10.23', '  mu300-kernel.tar.gz']
        a.log_open = True

        class Busy:
            running = True
            messages = None
        ctl.run = Busy()
        ctl.pump = lambda: False
        page = show('install', 4)
        self.assertIn('Downloading release v2026.10.23', page)
        self.assertIn('mu300-kernel.tar.gz', page)
        self.assertIn('SHA256 verified: mu300-ubuntu-rootfs.tar.gz', page)
        self.assertIn('Stopping is safe now', page)
        self.assertIn('mu300-kernel.tar.gz', a.w['log'].get('1.0', 'end'))
        # the installer's summary and its question
        p.event({'event': 'step', 'id': 'confirm', 'percent': '72', 'title': 'Ready to install'})
        p.line('  systems:        ubuntu (boots: ubuntu)')
        p.event({'event': 'question', 'id': 'MU300_ANS_CONFIRM', 'text': 'Type INSTALL to continue', 'default': 'no', 'state': 'waiting'})
        sent = []
        ctl.answer = lambda text: sent.append(text) or True
        a._confirm()
        self.assertIsNotNone(a.dialog)
        self.assertIn('systems:        ubuntu', ' '.join(str(w.get('1.0', 'end')) for w in a.dialog.winfo_children()[0].winfo_children() if isinstance(w, tk.Text)))
        root.tk.eval(a.dialog.protocol('WM_DELETE_WINDOW'))      # the window's close button
        root.update()
        self.assertIsNone(a.dialog)
        self.assertEqual(sent, ['no'])                       # closing it is a no, never a yes
        # it stopped before anything was written
        ctl.run = None
        p.event({'event': 'error', 'message': 'cancelled'})
        p.end(1)
        ctl.last['install'] = p
        page = show('install', 4)
        self.assertIn('It stopped: cancelled', page)
        self.assertIn('Nothing was written to the device.', page)
        self.assertIn('Try again', page)
        # a run that is writing to the device cannot be stopped, and one that failed there is not simply tried again
        p = model.Progress(model.INSTALL_STEPS, kind='install')
        ctl.progress, ctl.run = p, Busy()
        p.event({'event': 'step', 'id': 'device', 'percent': '80', 'title': 'Installing on the device'})
        show('install', 4)
        self.assertIn('cannot be stopped', str(a.w['cancel-note'].cget('text')))
        self.assertIn('disabled', a.w['cancel'].state())
        ctl.run = None
        p.event({'event': 'error', 'message': 'installation on the device failed; boot_b and misc were not changed'})
        p.end(1)
        page = show('install', 4)
        self.assertIn('the device still starts Android', page)
        self.assertNotIn('Try again', page)
        ctl.installed = {'done': {'boot_os': 'ubuntu', 'ip': '192.168.77.1'}, 'kind': 'f50', 'boot': 'ubuntu', 'systems': ['ubuntu'],
                         'panel': True, 'attempts': 5, 'default_linux': True}
        page = show('install', 5)
        self.assertIn('ssh ubuntu@192.168.77.1', page)
        self.assertIn('https://192.168.77.1:9090', page)
        self.assertIn('goes back to Android by itself', page)
        self.assertIn('Follow on Telegram', page)
        page = show('help')
        self.assertIn('Follow on Telegram', page)
        self.assertIn('https://t.me/mu300project', page)
        self.assertIn('Save a diagnostics report', page)
        self.assertNotIn('magisk.me', page.lower())          # no rooting guide is linked anywhere
        # every page again in the other languages and in the dark, and nothing may have raised
        for lang, dark in (('tr', False), ('zh', True), ('en', True)):
            i18n.set_language(lang)
            theme.apply(dark=dark)
            a.build()
            for section, step in (('install', 0), ('install', 1), ('install', 2), ('install', 3), ('install', 4), ('install', 5), ('manage', None), ('help', None)):
                show(section, step)
            self.assertEqual(root.title(), i18n._('MU300 Linux installer'))
        i18n.set_language('tr')
        a.build()
        self.assertIn('Telegram', show('help'))
        self.assertNotIn('Follow on Telegram', show('help'))  # translated
        root.update()
        root.destroy()
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
