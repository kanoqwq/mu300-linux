"""upstream/all-modules.py (every tristate the boards can use, as a module) and check-dt-modules.py (none of them
may bind to the vendor device tree by itself)."""
import fnmatch
import importlib.util
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
UP = TOP / 'upstream'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, UP / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


allmod = load('all_modules', 'all-modules.py')


class AllModules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_write_set_replaces_the_line_that_names_an_option(self):
        cfg = self.d / '.config'
        cfg.write_text('CONFIG_A=y\n# CONFIG_B is not set\nCONFIG_BB=m\nCONFIG_C="x"\n')
        allmod.write_set(str(cfg), {'B': 'm', 'A': 'n'})
        self.assertEqual(allmod.read(str(cfg)), {'A': 'n', 'B': 'm', 'BB': 'm', 'C': '"x"'})
        self.assertEqual(cfg.read_text().count('CONFIG_B'), 2)   # B once, BB untouched

    def test_tristates_are_found_with_their_directory(self):
        k = self.d / 'drivers' / 'foo'
        k.mkdir(parents=True)
        (k / 'Kconfig').write_text('menuconfig FOO\n\ttristate "foo"\nconfig FOO_OPT\n\tbool "opt"\n'
                                   'config FOO_LIB\n\tdef_tristate m\n')
        self.assertEqual(allmod.tristates(str(self.d)), {'FOO': 'drivers/foo', 'FOO_LIB': 'drivers/foo'})

    def test_the_rules_keep_board_wiring_tests_and_the_soc_gpu_out(self):
        dirs, keep, pats = allmod.rules()
        w = lambda sym, where: allmod.wanted(sym, where, dirs, keep, pats)
        for sym, where in (('USB_SERIAL_CH341', 'drivers/usb/serial'), ('BTRFS_FS', 'fs/btrfs'),
                           ('MMC_VUB300', 'drivers/mmc/host'), ('DRM_UDL', 'drivers/gpu/drm/udl'),
                           ('LEDS_TRIGGER_PATTERN', 'drivers/leds/trigger'), ('NFT_CT', 'net/netfilter'),
                           ('TOUCHSCREEN_USB_COMPOSITE', 'drivers/input/touchscreen'),
                           ('BRIDGE_NF_EBTABLES', 'net/bridge/netfilter'), ('LEDS_CLASS_MULTICOLOR', 'drivers/leds')):
            self.assertTrue(w(sym, where), sym)
        for sym, where in (('SND_SOC_WM8960', 'sound/soc/codecs'), ('DRM_PANFROST', 'drivers/gpu/drm/panfrost'),
                           ('REGULATOR_FIXED_VOLTAGE', 'drivers/regulator'), ('CRC_KUNIT_TEST', 'lib'),
                           ('RPMB', 'drivers/misc'), ('EXT2_FS', 'fs/ext2'), ('CORESIGHT', 'drivers/hwtracing/coresight'),
                           ('USB_DUMMY_HCD', 'drivers/usb/gadget/udc'), ('USB_ETH', 'drivers/usb/gadget/legacy'),
                           ('VFIO', 'drivers/vfio'), ('MMC_SDHCI_OF_ARASAN', 'drivers/mmc/host'),
                           ('SERIAL_DEV_BUS', 'drivers/tty/serdev'), ('POWER_SEQUENCING', 'drivers/power/sequencing'),
                           ('INPUT_SC27XX_VIBRA', 'drivers/input/misc'), ('SPI_SPRD', 'drivers/spi')):
            self.assertFalse(w(sym, where), sym)

    def test_what_must_stay_off_is_checked(self):
        for k in ('PERF_EVENTS', 'KPROBES', 'IOMMU_DMA', 'OF_IOMMU', 'DMA_CMA', 'COMPILE_TEST'):
            self.assertIn(k, allmod.FORBIDDEN_Y)
        self.assertTrue(allmod.NEW_BOOL_OFF.search('RTLWIFI_DEBUG'))
        self.assertFalse(allmod.NEW_BOOL_OFF.search('IOMMU_DEFAULT_DMA_STRICT'))   # "FAULT" in DEFAULT is not one
        fam = (UP / 'all-modules.config').read_text()
        self.assertIn('CONFIG_NETFILTER_XTABLES_LEGACY=y', fam)     # Docker's check-config: iptable_nat & co.
        self.assertIn('CONFIG_DRM_FBDEV_EMULATION=n', fam)

    def test_the_build_runs_both(self):
        b = (UP / 'build.sh').read_text()
        self.assertLess(b.index('olddefconfig'), b.index('python3 /work/all-modules.py . $O'))
        self.assertLess(b.index('python3 /work/all-modules.py . $O'), b.index('config options not taken'))
        self.assertIn('cp $O/all-modules.report $OUT/', b)
        m = (UP / 'build-modules.sh').read_text()
        self.assertIn('python3 /work/check-dt-modules.py', m)
        self.assertIn("(out / 'all-modules.report')", (UP / 'audit-bundle.py').read_text())


class AutoloadGuard(unittest.TestCase):
    """The kernel's on-demand loads go through mu300-modprobe-guard on both systems (review of #256)."""
    GUARD = TOP / 'rootfs/overlay/opt/mu300/bin/mu300-modprobe-guard'
    LIST = TOP / 'rootfs/overlay/opt/mu300/lib/autoload-blocked'

    SAMPLE = TOP / 'tests/data/autoload-7.2.11'   # a subset of the 7.2.11 build's modules.dep and modules.alias

    def run_guard(self, name, allow=False, shell=('sh',), moddir=None):
        with tempfile.TemporaryDirectory() as d:
            log, kmsg = Path(d) / 'log', Path(d) / 'kmsg'
            fake = Path(d) / 'modprobe'
            fake.write_text('#!/bin/sh\necho "$@" > "%s"\n' % log)
            fake.chmod(0o755)
            env = {'PATH': '/usr/bin:/bin', 'MU300_MODPROBE': str(fake), 'MU300_AUTOLOAD_BLOCKED': str(self.LIST),
                   'MU300_KMSG': str(kmsg), 'MU300_AUTOLOAD_ALLOW': str(Path(d) / 'allow'), 'MU300_KREL': 't',
                   'MU300_MODDIR': str(moddir or Path(d) / 'none'), 'MU300_GUARD_CACHE': str(Path(d) / 'cache')}
            if allow:
                (Path(d) / 'allow').write_text('')
            r = subprocess.run([*shell, str(self.GUARD), '-q', '--', name], env=env, capture_output=True, text=True)
            return r.returncode, log.read_text() if log.exists() else None, kmsg.read_text() if kmsg.exists() else ''

    def test_the_list_holds_module_names(self):
        names = [l.split('#')[0].strip() for l in self.LIST.read_text().splitlines()]
        names = [n for n in names if n]
        self.assertEqual(len(names), len(set(names)))
        for n in names:
            self.assertRegex(n, r'^[a-z0-9_]+$')
        for n in ('tipc', 'sctp', 'can', 'can_raw', 'can_bcm', 'nfc', 'ieee802154', 'slcan', 'hfs', 'jfs', 'nilfs2'):
            self.assertIn(n, names)
        for n in ('l2tp_core', 'l2tp_ppp', 'can_dev', 'hfsplus', 'wireguard', 'vhost_net'):
            self.assertNotIn(n, names)

    def test_every_spelling_of_a_blocked_module_in_the_build_sample(self):
        # every alias of every blocked module of the 7.2.11 build, and its name either way, refused; the closure of
        # their dependants checked; the daily ones (nft, wireguard, overlay, usb-storage, vfat, tun, bridge, veth,
        # br_netfilter, xt_*, vhost_net, kvm, l2tp...) handed on - under every shell the systems run it with
        check = load('autoload_check', 'check-autoload-blocked.py')
        for shell in (['sh'], ['busybox', 'sh'], ['dash']):
            if not shutil.which(shell[0]):
                continue
            with self.subTest(shell=shell[0]):
                check.check(self.SAMPLE, self.LIST, self.GUARD, shell=shell)

    def test_a_refusal_is_logged_and_never_reaches_modprobe(self):
        with tempfile.TemporaryDirectory() as d:
            moddir = Path(d)
            (moddir / 'tipc.ko').write_text('')
            rc, called, kmsg = self.run_guard('tipc', moddir=moddir)
            self.assertEqual((rc, called), (1, None))
            self.assertIn('on-demand loading of tipc (tipc) refused (root: modprobe tipc', kmsg)
            # without a table (no modinfo answer) the names still hold, in either spelling
            self.assertEqual(self.run_guard('can-raw')[:2], (1, None))
            self.assertEqual(self.run_guard('wireguard')[:2], (0, '-q -- wireguard\n'))

    def sample_moddir(self, d):
        """A module directory made from the build sample: every module file carries its aliases in its .modinfo."""
        aliases = {}
        for line in (self.SAMPLE / 'modules.alias').read_text().splitlines():
            if line.startswith('alias '):
                _, a, m = line.split(None, 2)
                aliases.setdefault(m, []).append(a)
        names = {os.path.basename(l.split(':')[0]).split('.ko')[0].replace('-', '_')
                 for l in (self.SAMPLE / 'modules.dep').read_text().splitlines()}
        for m in names:
            (d / (m + '.ko')).write_bytes(b'\x7fELF\0' + '\0'.join('alias=' + a for a in aliases.get(m, [])).encode() + b'\0')
        return aliases

    # the requests the first (request-string) list refused, made concrete
    OLD_REQUESTS = ['net-pf-29', 'net-pf-29-proto-1', 'net-pf-30', 'net-pf-30-proto-0', 'net-pf-36', 'net-pf-39',
                    'net-pf-39-proto-1', 'net-pf-40', 'net-pf-2-proto-132', 'net-pf-2-proto-132-type-1',
                    'net-pf-10-proto-132', 'net-pf-10-proto-132-type-5', 'net-pf-16-proto-16-family-TIPCv2',
                    'net-pf-16-proto-16-family-nfc', 'net-pf-16-proto-16-family-nl802154',
                    'net-pf-16-proto-16-family-802.15.4 MAC', 'net-pf-16-proto-16-family-j1939', 'rtnl-family-29'] + [
                    'fs-' + f for f in ('adfs affs befs bfs coda cramfs efs gfs2 gfs2meta hfs hpfs jfs minix nilfs2 ocfs2 '
                                        'ocfs2_dlmfs omfs orangefs qnx4 qnx6 romfs sysv v7 ufs vxfs ceph').split()]

    def test_what_the_old_list_refused_is_still_refused_or_not_built(self):
        with tempfile.TemporaryDirectory() as d:
            aliases = self.sample_moddir(Path(d))
            pairs = [(a.replace('-', '_'), m) for m, al in aliases.items() for a in al]
            for req in self.OLD_REQUESTS:
                answering = {m for a, m in pairs if fnmatch.fnmatchcase(req.replace('-', '_'), a)}
                rc, called, _ = self.run_guard(req, moddir=Path(d))
                if answering:
                    self.assertEqual((rc, called), (1, None), (req, answering))
                else:
                    # nothing in the build answers it (sysv went upstream in 6.15; no genetlink family by that name):
                    # the guard hands it on, and modprobe finds nothing either
                    self.assertEqual(rc, 0, req)

    def test_the_families_the_review_named_are_not_built(self):
        gone = ('rds rds_tcp rds_rdma atm clip br2684 lec mpoa pppoatm ax25 netrom rose mkiss 6pack bpqether x25 lapb '
                'lapbether appletalk phonet cdc_phonet af_rxrpc rxkad kafs kcm smc smc_diag caif caif_socket chnl_net '
                'mISDN_core mISDN_dsp capi n_gsm n_hdlc dccp dccp_ipv4 dccp_ipv6 dccp_diag qrtr psnap p8022 llc2').split()
        listed = {l.split('#')[0].strip() for l in self.LIST.read_text().splitlines()}
        for kv in ('7.2.11', '6.18.57'):
            names = set((self.SAMPLE / ('modules.names-' + kv)).read_text().split())
            self.assertGreater(len(names), 2500)
            self.assertEqual(sorted(set(gone) & names), [], kv)
            # L2TP stays, on purpose (L2TP VPNs), and is not listed
            self.assertIn('l2tp_ppp', names)
        self.assertNotIn('l2tp_ppp', listed)
        # every line discipline that is built is listed
        ldisc = {l.split()[2] for l in (self.SAMPLE / 'modules.alias').read_text().splitlines()
                 if l.startswith('alias tty-ldisc-')}
        self.assertTrue(ldisc)
        self.assertLessEqual(ldisc, listed)

    def test_a_module_that_depends_on_a_listed_one_is_refused_too(self):
        # Ubuntu's modules.dep: the table takes in every module whose dependencies reach a listed one, whatever the list
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'tipc.ko').write_bytes(b'\0alias=net-pf-30\0')
            (d / 'evil-helper.ko').write_bytes(b'\0alias=net-pf-99\0alias=rtnl-link-evil\0')
            (d / 'other.ko').write_bytes(b'\0alias=net-pf-98\0')
            (d / 'modules.dep').write_text('kernel/net/tipc/tipc.ko:\nkernel/x/evil-helper.ko: kernel/net/tipc/tipc.ko\n'
                                           'kernel/x/other.ko:\n')
            for req in ('net-pf-99', 'rtnl-link-evil', 'evil_helper', 'evil-helper'):
                self.assertEqual(self.run_guard(req, moddir=d)[:2], (1, None), req)
            self.assertEqual(self.run_guard('net-pf-98', moddir=d)[:2], (0, '-q -- net-pf-98\n'))

    def test_without_a_table_it_fails_closed_for_the_families(self):
        with tempfile.TemporaryDirectory() as d:
            env_cache = Path(d) / 'elsewhere'
            env_cache.mkdir()
            link = Path(d) / 'cache-link'
            link.symlink_to(env_cache)   # not a directory of root's own making: no table
            log, kmsg = Path(d) / 'log', Path(d) / 'kmsg'
            fake = Path(d) / 'modprobe'
            fake.write_text('#!/bin/sh\necho "$@" > "%s"\n' % log)
            fake.chmod(0o755)
            env = {'PATH': '/usr/bin:/bin', 'MU300_MODPROBE': str(fake), 'MU300_AUTOLOAD_BLOCKED': str(self.LIST),
                   'MU300_KMSG': str(kmsg), 'MU300_AUTOLOAD_ALLOW': str(Path(d) / 'allow'), 'MU300_KREL': 't',
                   'MU300_MODDIR': str(Path(d)), 'MU300_GUARD_CACHE': str(link)}
            run = lambda req: subprocess.run(['sh', str(self.GUARD), '-q', '--', req], env=env).returncode
            for req in ('net-pf-99', 'net-pf-16-proto-16-family-x', 'fs-foo', 'tty-ldisc-21', 'rtnl-link-x'):
                self.assertEqual(run(req), 1, req)
                # the hint names what root would load (on F50-B it said "root: modprobe no alias table")
                self.assertIn('on-demand loading of %s (no alias table) refused (root: modprobe %s;' % (req, req),
                              kmsg.read_text())
            self.assertEqual(run('can-raw'), 1)          # a listed name, still known without a table
            self.assertFalse(log.exists())
            for req in ('crypto-gcm(aes)', 'nft-chain-2-nat', 'char-major-10-200', 'wireguard'):
                self.assertEqual(run(req), 0, req)

    def test_the_switch_turns_it_off(self):
        self.assertEqual(self.run_guard('tipc', allow=True)[:2], (0, '-q -- tipc\n'))

    def test_prepare_makes_the_table_then_takes_over(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            helper = d / 'helper'
            helper.write_text('/sbin/modprobe\n')
            (d / 'mods').mkdir()
            (d / 'mods' / 'tipc.ko').write_bytes(b'\x7fELF\0name=tipc\0alias=net-pf-30\0alias=net-pf-16-proto-16-family-TIPCv2\0')
            env = {'PATH': '/usr/bin:/bin', 'MU300_AUTOLOAD_BLOCKED': str(self.LIST), 'MU300_KREL': '7.2.11',
                   'MU300_MODDIR': str(d / 'mods'), 'MU300_GUARD_CACHE': str(d / 'cache'),
                   'MU300_HELPER_SYSCTL': str(helper), 'MU300_AUTOLOAD_ALLOW': str(d / 'allow')}
            r = subprocess.run(['sh', str(self.GUARD), '--prepare'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(helper.read_text(), '/opt/mu300/bin/mu300-modprobe-guard\n')
            table = (d / 'cache' / '7.2.11').read_text().splitlines()
            self.assertIn('net_pf_30 tipc', table)
            self.assertIn('net_pf_16_proto_16_family_TIPCv2 tipc', table)
            self.assertEqual((d / 'cache').stat().st_mode & 0o777, 0o700)
            # with the switch file the helper stays the system's
            helper.write_text('/sbin/modprobe\n')
            (d / 'allow').write_text('')
            subprocess.run(['sh', str(self.GUARD), '--prepare'], env=env, capture_output=True, text=True)
            self.assertEqual(helper.read_text(), '/sbin/modprobe\n')

    def test_both_systems_install_it(self):
        for system in ('rootfs', 'openwrt'):
            conf = (TOP / system / 'overlay/etc/sysctl.d/98-mu300-modules.conf').read_text()
            self.assertIn('dev.tty.ldisc_autoload = 0', conf)
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-modprobe-guard.service').read_text()
        self.assertIn('ExecStart=/opt/mu300/bin/mu300-modprobe-guard --prepare', unit)
        self.assertIn('mu300-modprobe-guard.service:sysinit.target', (TOP / 'rootfs/assemble.sh').read_text())
        hw = (TOP / 'openwrt/overlay/etc/init.d/mu300-hw').read_text()
        self.assertIn('/opt/mu300/bin/mu300-modprobe-guard --prepare', hw)
        # OpenWrt's image takes /opt/mu300 from the Ubuntu overlay, and both copy their overlay whole
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('rootfs/overlay/opt/mu300":/in/opt-mu300', build)
        self.assertIn('cp -a /in/overlay/. $R/', build)
        self.assertTrue(self.GUARD.stat().st_mode & 0o111)

    def test_the_dead_families_are_not_built(self):
        dirs, keep, pats = allmod.rules()
        for sym, where in (('N_GSM', 'drivers/tty'), ('N_HDLC', 'drivers/tty'), ('ATM', 'net/atm'),
                           ('USB_ATM', 'drivers/usb/atm'), ('AX25', 'net/ax25'), ('X25', 'net/x25'),
                           ('RDS', 'net/rds'), ('AF_RXRPC', 'net/rxrpc'), ('AFS_FS', 'fs/afs'), ('PHONET', 'net/phonet'),
                           ('ISDN', 'drivers/isdn'), ('PARPORT', 'drivers/parport'), ('AF_KCM', 'net/kcm')):
            self.assertFalse(allmod.wanted(sym, where, dirs, keep, pats), sym)
        self.assertNotIn('CONFIG_HAMRADIO=y', (UP / 'all-modules.config').read_text())


class DtModules(unittest.TestCase):
    def run_check(self, aliases, *allowed):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / 'modules.alias'
            a.write_text(aliases)
            c = Path(d) / 'compat'
            c.write_text('# boards\nsprd,ums9620-dwc3\nregulator-fixed\nst,st21nfc\n')
            return subprocess.run([sys.executable, str(UP / 'check-dt-modules.py'), str(a), str(c), *allowed],
                                  capture_output=True, text=True)

    def test_a_module_that_matches_a_node_fails_the_build(self):
        r = self.run_check('alias of:N*T*Cregulator-fixed reg_fixed_voltage\n'
                           'alias of:N*T*Cregulator-fixedC* reg_fixed_voltage\nalias usb:v1234p* foo\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('reg_fixed_voltage binds to regulator-fixed', r.stderr)

    def test_unrelated_and_allowed_modules_pass(self):
        self.assertEqual(self.run_check('alias of:N*T*Cvendor,otherC* other\nalias usb:v1234p* foo\n'
                                        'alias of:NGAISLER_APBPS2T(null)C* apbps2\n').returncode, 0)
        self.assertEqual(self.run_check('alias of:N*T*Cst,st21nfc nfc-thing\n', 'nfc_thing').returncode, 0)

    def test_the_compatibles_of_both_boards_are_listed(self):
        compat = (UP / 'dt-compatibles.txt').read_text().split()
        for c in ('sprd,ums9620', 'zte-ldo-leds', 'arm,gic-v3', 'sprd,sc9860-spi'):
            self.assertIn(c, compat)


if __name__ == '__main__':
    unittest.main()
