"""The web panel for Ubuntu, the panel extra (rootfs/panel): Cockpit and the MU300 page. mu300-update's checks of the
extra's tarball, mu300-extra install (online from the release, offline --from), remove and link against a fake
partition and a fake system (MU300_SYSROOT) with apt-get, systemctl and systemd-run stubbed; mu300-panel itself (its
configuration: the LAN address only, never waiting for br-lan; info's JSON; the hotspot, its password on stdin); the firewall that keeps port
9090 off the uplink; the page's manifest and translations; the page's JavaScript under Node against stub command
outputs (argument arrays, secrets on stdin, text never as HTML); and `sms json`."""
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest
from test_extra import ExtrasBase, sums
from test_sms_pool import AT_STUB, AWK, PDU_GSM, PDU_UCS2, Pool

PANEL = TOP / 'rootfs' / 'panel'
CMD = PANEL / 'bin' / 'mu300-panel'
PAGE = PANEL / 'cockpit' / 'mu300'
FIREWALL = TOP / 'rootfs' / 'overlay' / 'etc' / 'mu300' / 'firewall.nft'


def panel_tarball(path, release='v2026.10.20', debs=(), extra=(), name='panel'):
    """the panel extra as tools/make-extra.sh builds it (the page's own files, bin/mu300-panel), DEBS: (release, file)"""
    files = [('./name', name + '\n', 0o644), ('./release', release + '\n', 0o644),
             ('./components', 'mu300-panel\n', 0o644), ('./bin/mu300-panel', CMD.read_text(), 0o755)]
    files += [(f'./cockpit/mu300/{p.name}', p.read_bytes(), 0o644) for p in sorted(PAGE.iterdir())]
    files += [(f'./debs/{u}/{f}', b'!<arch>\n', 0o644) for u, f in debs]
    files += list(extra)
    with tarfile.open(path, 'w:gz') as t:
        for fn, data, mode in files:
            data = data.encode() if isinstance(data, str) else data
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
    return path


# apt-get: records its arguments; an install "installs" Cockpit (cockpit-ws appears in the fake system)
APT = ('echo "apt-get $*" >> "$STUBLOG/apt"; '
       # --download-only: one package into the Dir::Cache::archives given (unless APT_FAIL), nothing installed
       'case " $* " in *" --download-only "*) [ -z "$APT_FAIL" ] || exit 1; '
       'd=$(printf "%s\\n" "$@" | sed -n "s/^Dir::Cache::archives=//p"); : > "$d/cockpit-ws_314-1_arm64.deb"; exit 0 ;; esac; '
       'case " $* " in *" install "*) mkdir -p "$MU300_SYSROOT/usr/lib/cockpit" && : > "$MU300_SYSROOT/usr/lib/cockpit/cockpit-ws" '
       '&& chmod 755 "$MU300_SYSROOT/usr/lib/cockpit/cockpit-ws" ;; *" purge "*) rm -f "$MU300_SYSROOT/usr/lib/cockpit/cockpit-ws" ;; esac; '
       '[ -z "$APT_FAIL" ]')


class PanelExtra(ExtrasBase):
    def setUp(self):
        super().setUp()
        (self.root / 'etc').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        self.stub('apt-get', APT)
        self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/systemctl"')
        self.stub('systemd-run', 'echo "systemd-run $*" >> "$STUBLOG/systemd-run"')
        self.stub('dpkg-query', 'exit 1')

    def log(self, name):
        f = self.tmp / name
        out = f.read_text() if f.exists() else ''
        f.unlink(missing_ok=True)
        return out

    def panel(self):
        return self.disk / 'extra' / 'panel'

    def reset(self, shell):
        self.up(shell, f'rm -rf "{self.disk}/extra"')
        for p in ('usr', 'etc/cockpit', 'etc/systemd'):
            shutil.rmtree(self.root / p, ignore_errors=True)
        for f in ('apt', 'systemctl', 'systemd-run'):
            self.log(f)

    # ---- the tarball ---------------------------------------------------------------------------------------
    def test_unpack_takes_the_panel_and_refuses_anything_else(self):
        good = panel_tarball(self.tmp / 'p.tar.gz', debs=[('24.04', 'cockpit-ws_314-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            r = self.up(shell, f'extra_unpack panel "{good}" && extra_release panel && extra_installed')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ['v2026.10.20', 'panel'])
            self.assertTrue(os.access(self.panel() / 'bin/mu300-panel', os.X_OK))
            self.assertTrue((self.panel() / 'cockpit/mu300/manifest.json').is_file())
            for bad in (panel_tarball(self.tmp / 'b1.tar.gz', extra=[('./etc/sudoers', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b2.tar.gz', extra=[('./bin/other', '#!/bin/sh\n', 0o755)]),
                        panel_tarball(self.tmp / 'b3.tar.gz', extra=[('./debs/24.04/x.sh', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b4.tar.gz', extra=[('./cockpit/other/index.html', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b5.tar.gz', extra=[('./debs/x/y.deb', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b6.tar.gz', name='lang')):
                r = self.up(shell, f'extra_unpack panel "{bad}"; echo "rc=$?"')
                self.assertIn('rc=1', r.stdout, bad.name)
                self.assertTrue((self.panel() / 'cockpit/mu300/manifest.json').is_file(), bad.name)

    # ---- install, remove, link --------------------------------------------------------------------------------
    def installed_files(self):
        page = self.root / 'usr/share/cockpit/mu300'
        conf = (self.root / 'etc/cockpit/cockpit.conf')
        drop = (self.root / 'etc/systemd/system/cockpit.socket.d/mu300-listen.conf')
        return page, conf, drop

    def test_offline_install_from_the_extra_packages(self):
        (self.tmp / 'off').mkdir()
        off = panel_tarball(self.tmp / 'off' / 'mu300-extra-panel-offline.tar.gz',
                            debs=[('24.04', 'cockpit-ws_314-1_arm64.deb'), ('24.04', 'python3_3.12_arm64.deb'),
                                  ('26.04', 'cockpit-ws_360-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            self.offline()
            r = self.ex(shell, 'install', 'panel', '--from', off)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            apt = self.log('apt')
            # only this release's packages, by path, and nothing from the archive
            self.assertIn('install -y -q --no-install-recommends', apt)
            self.assertIn(f'{self.panel()}/debs/24.04/cockpit-ws_314-1_arm64.deb', apt)
            self.assertIn('python3_3.12_arm64.deb', apt)
            self.assertNotIn('26.04', apt)
            self.assertNotIn('update', apt)
            page, conf, drop = self.installed_files()
            # a copy every user can read (the extras on the disk are root's alone): never a link into the disk
            self.assertFalse(page.is_symlink())
            src = self.panel() / 'cockpit/mu300'
            self.assertEqual(sorted(x.name for x in page.iterdir()), sorted(x.name for x in src.iterdir()))
            for f in page.iterdir():
                self.assertEqual(f.read_bytes(), (src / f.name).read_bytes())
                self.assertEqual(f.stat().st_mode & 0o777, 0o644, f)
            self.assertEqual(page.stat().st_mode & 0o777, 0o755)
            # a newer page in the extra is copied over at the next link; a link of an older panel is replaced
            (src / 'mu300.js').write_text('// new\n')
            self.assertEqual(self.ex(shell, 'link').returncode, 0)
            self.assertEqual((page / 'mu300.js').read_text(), '// new\n')
            shutil.rmtree(page)
            page.symlink_to(src)
            self.assertEqual(self.ex(shell, 'link').returncode, 0)
            self.assertTrue(page.is_dir() and not page.is_symlink())
            self.assertIn('LoginTo = false', conf.read_text())
            self.assertIn('IdleTimeout = 15', conf.read_text())
            self.assertEqual([l for l in drop.read_text().splitlines() if not l.startswith('#')],
                             ['[Socket]', 'ListenStream=', 'ListenStream=192.168.77.1:9090', 'FreeBind=yes',
                              'IPAddressDeny=any', 'IPAddressAllow=localhost 192.168.77.0/24'])
            self.assertTrue((self.links / 'mu300-panel').is_symlink())
            self.assertIn('--no-block restart cockpit.socket', self.log('systemctl'))

    def test_offline_copies_are_checked(self):
        d = self.tmp / 'dir'
        d.mkdir()
        panel_tarball(d / 'mu300-extra-panel-offline.tar.gz')
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ex(shell, 'install', 'panel', '--from', d)          # a directory needs its SHA256SUMS
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('SHA256SUMS', r.stderr)
            (d / 'SHA256SUMS').write_text('0' * 64 + '  mu300-extra-panel-offline.tar.gz\n')
            r = self.ex(shell, 'install', 'panel', '--from', d)
            self.assertIn('does not match', r.stderr)
            sums(d, 'mu300-extra-panel-offline.tar.gz')
            r = self.ex(shell, 'install', 'panel', '--from', d)
            self.assertEqual(r.returncode, 0, r.stderr)
            (d / 'SHA256SUMS').unlink()
            self.assertFalse((self.disk / 'extra/.panel.new').exists())

    def test_online_install_takes_cockpit_from_the_archive(self):
        self.serve('v2026.10.20', assets={'mu300-extra-panel.tar.gz': panel_tarball(self.tmp / 'on.tar.gz')})
        self.versions('v2026.10.20')
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ex(shell, 'install', 'panel')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            apt = self.log('apt').splitlines()
            # bounded: offline, an apt-get update without limits waited more than 10 minutes
            self.assertTrue(apt[0].startswith('apt-get update '), apt)
            for o in ('Acquire::http::Timeout=10', 'Acquire::https::Timeout=10', 'Acquire::Retries=0'):
                self.assertIn(o, apt[0])
            # downloaded into the extra, installed from there, and kept for the restore after an update
            self.assertIn('--download-only', apt[1])
            self.assertIn('Dir::Cache::archives=', apt[1])
            self.assertIn('/disk/extra/panel/.debs-new -o', apt[1])
            self.assertIn('Acquire::http::Timeout=10', apt[1])
            self.assertTrue(apt[2].endswith('/disk/extra/panel/.debs-new/cockpit-ws_314-1_arm64.deb'), apt)
            self.assertTrue((self.panel() / 'debs/24.04/cockpit-ws_314-1_arm64.deb').is_file())
            self.assertFalse((self.panel() / '.debs-new').exists())
            # the configuration was in place before the packages (their postinst starts the socket)
            self.assertTrue(self.installed_files()[2].is_file())

    def test_offline_without_packages_says_how(self):
        self.serve('v2026.10.20', assets={'mu300-extra-panel.tar.gz': panel_tarball(self.tmp / 'on.tar.gz')})
        self.versions('v2026.10.20')
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ex(shell, 'install', 'panel', APT_FAIL=1)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('mu300-extra-panel-offline.tar.gz', r.stderr)

    def test_openwrt_refuses(self):
        (self.root / 'etc/openwrt_release').write_text('x\n')
        off = panel_tarball(self.tmp / 'p.tar.gz')
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'panel', '--from', off)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('for Ubuntu', r.stderr)
            self.assertFalse(self.panel().exists())
            self.assertIn('for Ubuntu only', self.ex(shell, 'list').stdout)

    def test_remove_takes_it_all_out(self):
        off = panel_tarball(self.tmp / 'p.tar.gz', debs=[('24.04', 'cockpit-ws_314-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            self.ex(shell, 'install', 'panel', '--from', off)
            self.log('apt')
            r = self.ex(shell, 'remove', 'panel')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('purge -y -q cockpit-ws cockpit-system cockpit-bridge', self.log('apt'))
            self.assertIn('disable --now cockpit.socket', self.log('systemctl'))
            for p in self.installed_files():
                self.assertFalse(p.exists() or p.is_symlink(), p)
            self.assertFalse(self.panel().exists())
            self.assertFalse((self.links / 'mu300-panel').is_symlink())

    def test_link_after_an_update_puts_cockpit_back_in_the_background(self):
        off = panel_tarball(self.tmp / 'p.tar.gz', debs=[('24.04', 'cockpit-ws_314-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            self.ex(shell, 'install', 'panel', '--from', off)
            self.log('systemctl')
            # nothing to do while Cockpit and its configuration are there: no reload at boot
            self.assertEqual(self.ex(shell, 'link').returncode, 0)
            self.assertEqual(self.log('systemctl'), '')
            self.assertEqual(self.log('systemd-run'), '')
            # an update: a new system without Cockpit, its configuration or the page
            for p in ('usr', 'etc/cockpit', 'etc/systemd'):
                shutil.rmtree(self.root / p, ignore_errors=True)
            self.log('apt')
            self.assertEqual(self.ex(shell, 'link').returncode, 0)
            run = self.log('systemd-run')
            self.assertIn('--no-block', run)
            self.assertIn('After=network-online.target', run)
            self.assertIn(f'{self.panel()}/bin/mu300-panel restore', run)
            self.assertEqual(self.log('apt'), '')                  # the boot never runs apt itself
            page, conf, drop = self.installed_files()
            self.assertTrue(page.is_dir() and conf.is_file() and drop.is_file())

    def test_link_moves_an_old_device_bound_socket_to_the_lan_address(self):
        # the panel of v2026.10.17.3 bound the socket to br-lan (BindToDevice): systemd made it wait for the device,
        # which mu300-lan creates only after basic.target - 90 s at every boot, then the socket failed (F50, 7.2.11)
        off = panel_tarball(self.tmp / 'p.tar.gz', debs=[('24.04', 'cockpit-ws_314-1_arm64.deb')])
        old = '# mu300-panel (the panel extra)\n[Socket]\nListenStream=\nListenStream=9090\nBindToDevice=br-lan\n'
        for shell in self.each_shell():
            self.reset(shell)
            self.ex(shell, 'install', 'panel', '--from', off)
            drop = self.installed_files()[2]
            drop.write_text(old)
            (self.root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP="192.168.90.1"\n')
            self.log('systemctl')
            self.assertEqual(self.ex(shell, 'link').returncode, 0)
            body = [l for l in drop.read_text().splitlines() if not l.startswith('#')]
            self.assertEqual(body, ['[Socket]', 'ListenStream=', 'ListenStream=192.168.90.1:9090', 'FreeBind=yes',
                                   'IPAddressDeny=any', 'IPAddressAllow=localhost 192.168.90.0/24'])
            self.assertNotIn('BindToDevice', drop.read_text())
            sc = self.log('systemctl').splitlines()
            self.assertEqual(sc[0], 'systemctl daemon-reload')
            # never waits for the socket: link runs in mu300-fixups, before sysinit.target, and the socket after it
            self.assertIn('systemctl --no-block restart cockpit.socket', sc)
            # a LAN_IP that is no address: the device's default, as lan-start would refuse it
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=$(reboot)\n')
            self.ex(shell, 'link')
            self.assertIn('ListenStream=192.168.77.1:9090', drop.read_text())
            # never a wildcard: a lan.conf without LAN_IP, or none at all, is the default too
            for conf in ('LAN_IP=\n', None):
                if conf is None:
                    (self.root / 'etc/mu300/lan.conf').unlink()
                else:
                    (self.root / 'etc/mu300/lan.conf').write_text(conf)
                self.ex(shell, 'link')
                listen = [l for l in drop.read_text().splitlines() if l.startswith('ListenStream=')]
                self.assertEqual(listen, ['ListenStream=', 'ListenStream=192.168.77.1:9090'])
            (self.root / 'etc/mu300/lan.conf').write_text('LAN_IP=192.168.77.1\n')
            (self.root / 'etc/mu300/lan.conf').unlink()

    def test_update_rewrites_the_drop_in_of_the_new_system(self):
        # mu300-update apply, after the systems and extras are in place: the carried drop-in of an older panel is
        # rewritten in the new Ubuntu, so its first boot does not wait for br-lan; a system without the panel is left
        off = panel_tarball(self.tmp / 'p.tar.gz')
        ub = self.disk / 'ubuntu'
        (ub / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        (ub / 'opt/mu300/bin').mkdir(parents=True)
        shutil.copy(BIN / 'mu300-lan-ip', ub / 'opt/mu300/bin/mu300-lan-ip')
        (ub / 'opt/mu300/bin/mu300-device').write_text('#!/bin/sh\necho u30air\n')
        (ub / 'opt/mu300/bin/mu300-device').chmod(0o755)
        drop = ub / 'etc/systemd/system/cockpit.socket.d/mu300-listen.conf'
        for shell in self.each_shell():
            self.reset(shell)
            self.up(shell, f'extra_unpack panel "{off}"')
            drop.parent.mkdir(parents=True, exist_ok=True)
            drop.write_text('[Socket]\nListenStream=\nListenStream=9090\nBindToDevice=br-lan\n')
            r = self.up(shell, 'panel_configure "ubuntu openwrt"; echo "rc=$?"')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('ListenStream=192.168.78.1:9090\nFreeBind=yes\nIPAddressDeny=any\n'
                          'IPAddressAllow=localhost 192.168.78.0/24', drop.read_text())
            self.assertNotIn('BindToDevice', drop.read_text())
            self.assertFalse((self.disk / 'openwrt/etc/systemd').exists())
            self.assertEqual(self.log('systemctl'), '')             # files only: the running system is not touched
            self.assertFalse((self.root / 'etc/systemd').exists())

    def test_after_an_update_offline_cockpit_comes_from_the_kept_packages(self):
        # online install (packages kept), then an update: the release's online extra (no debs) replaces the extra and
        # a new system has no Cockpit; offline, the restore installs it from the kept packages without the archive
        self.serve('v2026.10.20', assets={'mu300-extra-panel.tar.gz': panel_tarball(self.tmp / 'on.tar.gz')})
        self.versions('v2026.10.20')
        new = panel_tarball(self.tmp / 'new.tar.gz', release='v2026.10.21')
        for shell in self.each_shell():
            self.reset(shell)
            self.assertEqual(self.ex(shell, 'install', 'panel').returncode, 0)
            self.up(shell, f'extra_unpack panel "{new}"')
            self.assertEqual((self.panel() / 'release').read_text().strip(), 'v2026.10.21')
            self.assertTrue((self.panel() / 'debs/24.04/cockpit-ws_314-1_arm64.deb').is_file())
            self.assertFalse((self.disk / 'extra/.panel.old').exists())
            shutil.rmtree(self.root / 'usr', ignore_errors=True)
            self.log('apt')
            env = dict(MU300_SYSROOT=self.root, MU300_PANEL_DIR=self.panel(), MU300_RUN=self.tmp / 'run',
                       MU300_PANEL_RETRY=0, MU300_PANEL_TRIES=2)
            r = self.script(shell, CMD, 'restore', **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            apt = self.log('apt')
            self.assertIn('/disk/extra/panel/debs/24.04/cockpit-ws_314-1_arm64.deb', apt)
            self.assertNotIn('update', apt)
            # an offline extra brings its own packages: those win over the kept ones
            off = panel_tarball(self.tmp / 'off.tar.gz', debs=[('24.04', 'cockpit-ws_360-1_arm64.deb')])
            self.up(shell, f'extra_unpack panel "{off}"')
            self.assertEqual(sorted(x.name for x in (self.panel() / 'debs/24.04').iterdir()), ['cockpit-ws_360-1_arm64.deb'])

    def test_restore_installs_and_stops_trying(self):
        off = panel_tarball(self.tmp / 'p.tar.gz')
        for shell in self.each_shell():
            self.reset(shell)
            self.up(shell, f'extra_unpack panel "{off}"')
            env = dict(MU300_SYSROOT=self.root, MU300_PANEL_DIR=self.panel(), MU300_RUN=self.tmp / 'run',
                       MU300_PANEL_RETRY=0, MU300_PANEL_TRIES=3)
            r = self.script(shell, CMD, 'restore', APT_FAIL=1, **env)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.log('apt').count('update'), 3)
            # what to do instead, said plainly
            self.assertIn('mu300-extra install panel --from FILE', r.stderr)
            self.assertIn('offline', r.stderr)
            r = self.script(shell, CMD, 'restore', **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((self.root / 'usr/lib/cockpit/cockpit-ws').exists())


class Command(ShellTest):
    """mu300-panel info and hotspot-set"""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'etc/mu300').mkdir(parents=True)
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        (self.root / 'etc/mu300/hotspot.conf').write_text('SSID=MU300-1\nPSK=oldpassword\nBAND=5\nCHANNEL=auto\n')
        z = self.root / 'sys/class/thermal/thermal_zone0'
        z.mkdir(parents=True)
        (z / 'type').write_text('soc-thmzone\n')
        (z / 'temp').write_text('48250\n')
        b = self.root / 'sys/class/power_supply/battery'
        b.mkdir(parents=True)
        for k, v in (('type', 'Battery'), ('capacity', '81'), ('status', 'Discharging'), ('voltage_now', '3900000')):
            (b / k).write_text(v + '\n')
        self.stub('id', 'echo 0')
        self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/systemctl"; [ "$1" = is-active ] && echo active; exit 0')
        self.stub('mu300-device', 'cat "$STUBLOG/device"')
        (self.tmp / 'device').write_text('u30air\n')
        self.stub('mu300-lan-ip', 'echo 192.168.77.1')
        self.stub('mu300-cpu', 'echo \'{"ok":1,"profile":"saving"}\'')
        self.stub('ip', 'echo "5: sipa_eth0    inet 10.0.0.2/30 scope global sipa_eth0"')
        # an operator name with a backslash and markup, as a hostile network could name itself
        (self.tmp / 'cops').write_text('+COPS: 0,0,"Evil\\x <b>",7\r\nOK\r\n')
        self.stub('mu300-at', 'echo "$*" >> "$STUBLOG/at"; case "$*" in '
                  '*COPS*) cat "$STUBLOG/cops" ;; '
                  '*CEREG*) printf \'+CEREG: 0,5\\r\\nOK\\r\\n\' ;; *CESQ*) printf \'+CESQ: 99,99,255,255,30,60\\r\\nOK\\r\\n\' ;; esac')

    def env(self, **extra):
        e = super().env(**extra)
        e.update(MU300_SYSROOT=str(self.root), MU300_RUN=str(self.tmp / 'run'))
        return e

    def test_info_is_json(self):
        for shell in self.each_shell():
            shutil.rmtree(self.tmp / 'run', ignore_errors=True)
            r = self.script(shell, CMD, 'info')
            self.assertEqual(r.returncode, 0, r.stderr)
            i = json.loads(r.stdout)
            self.assertEqual(i['device'], 'u30air')
            self.assertEqual(i['mobile_data'], {'service': 'active', 'address': '10.0.0.2/30', 'roaming': None})
            self.assertEqual(i['signal']['rat'], 'LTE')
            self.assertEqual(i['signal']['registration'], 'roaming')
            self.assertEqual((i['signal']['rsrp'], i['signal']['rsrq']), (-80, -4.5))
            self.assertEqual(i['signal']['operator'], 'Evil\\x <b>')
            self.assertEqual(i['battery']['capacity'], 81)
            self.assertEqual(i['temperatures'], [{'name': 'soc-thmzone', 'millicelsius': 48250}])
            self.assertEqual(i['cpu']['profile'], 'saving')
            self.assertEqual(i['hotspot'], {'ssid': 'MU300-1'})
            self.assertNotIn('oldpassword', r.stdout)
            # the radio readings are cached: a second call within 15 s sends no AT command
            (self.tmp / 'at').unlink()
            self.script(shell, CMD, 'info')
            self.assertFalse((self.tmp / 'at').exists())
            # the F50 has no battery, whatever its charger driver lists
            (self.tmp / 'device').write_text('f50\n')
            self.assertIsNone(json.loads(self.script(shell, CMD, 'info').stdout)['battery'])
            (self.tmp / 'device').write_text('u30air\n')

    def test_info_data_roaming(self):
        """FINDINGS 120: mobile-data roaming --json as it is; anything else (an older mobile-data's usage text) null."""
        for shell in self.each_shell():
            self.stub('mobile-data', '[ "$*" = "roaming --json" ] || exit 2\n'
                      'echo \'{"allowed":false,"held":true,"slot":0,"per_sim":false,"slots":[false,false]}\'')
            i = json.loads(self.script(shell, CMD, 'info').stdout)
            self.assertEqual(i['mobile_data']['roaming'], {'allowed': False, 'held': True, 'slot': 0, 'per_sim': False,
                                                           'slots': [False, False]})
            self.stub('mobile-data', 'echo "usage: mobile-data up" >&2; echo \'"x"\'; exit 2')
            self.assertIsNone(json.loads(self.script(shell, CMD, 'info').stdout)['mobile_data']['roaming'])
            (self.stubs / 'mobile-data').unlink()

    def test_hotspot_set(self):
        conf = self.root / 'etc/mu300/hotspot.conf'
        for shell in self.each_shell():
            conf.write_text('SSID=MU300-1\nPSK=oldpassword\nBAND=5\nCHANNEL=auto\n')
            for ssid, psk in (('', 'longenough'), ('x' * 33, 'longenough'), ('ok', 'short'), ('ok', 'x' * 64),
                              ('a\tb', 'longenough'), (' lead', 'longenough'), ('ok', 'pässword1')):
                r = self.script(shell, CMD, 'hotspot-set', ssid, stdin=psk + '\n')
                self.assertNotEqual(r.returncode, 0, (ssid, psk))
                self.assertIn('PSK=oldpassword', conf.read_text())
            r = self.script(shell, CMD, 'hotspot-set', 'Café $(id) "x"', stdin='p@ss $(reboot)\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(conf.read_text().splitlines(),
                             ['BAND=5', 'CHANNEL=auto', 'SSID=Café $(id) "x"', 'PSK=p@ss $(reboot)'])
            self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
            self.assertIn('restart mu300-hotspot', (self.tmp / 'systemctl').read_text())

    def test_not_on_openwrt(self):
        (self.root / 'etc/openwrt_release').write_text('x\n')
        for shell in self.each_shell():
            r = self.script(shell, CMD, 'install')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('for Ubuntu', r.stderr)


class Static(unittest.TestCase):
    def test_the_uplink_never_reaches_9090(self):
        # the host firewall: nothing new from the cellular side gets in, Cockpit's port included; nothing opens it
        fw = [l.strip() for l in FIREWALL.read_text().splitlines()]
        chain = fw[fw.index('chain input {'):]
        chain = chain[:chain.index('}')]
        self.assertEqual(chain[-1], 'iifname "sipa_eth*" counter drop')
        for line in chain:
            if 'accept' in line and 'iifname' in line:
                self.assertRegex(line, r'ct state established,related accept|icmp|icmpv6')
        self.assertNotIn('9090', FIREWALL.read_text())
        # the Wi-Fi uplink is closed the same way
        self.assertIn('iifname "%s" counter drop', (BIN / 'wifi-client').read_text())
        # and Cockpit listens on the LAN address only - from the start of the boot (FreeBind), never bound to br-lan
        # (BindToDevice makes the socket wait for the device, which comes after basic.target: a 90 s stall)
        cmd = CMD.read_text()
        self.assertIn("printf '[Socket]\\nListenStream=\\nListenStream=%s:9090\\nFreeBind=yes\\n' \"$_ip\"", cmd)
        self.assertIn("printf 'IPAddressDeny=any\\nIPAddressAllow=localhost %s.0/24\\n'", cmd)
        self.assertFalse([l for l in cmd.splitlines() if 'BindToDevice=' in l and not l.lstrip().startswith('#')])
        # a LAN address changed while the device runs moves the socket along
        lan = (BIN / 'lan-start').read_text()
        self.assertIn('grep -qx "ListenStream=$LAN_IP:9090" "$CP"', lan)
        self.assertIn('mu300-panel link', lan)
        # an update rewrites it in the new systems
        self.assertIn('panel_configure "$todo"', (BIN / 'mu300-update').read_text())

    def test_manifest_and_page(self):
        m = json.loads((PAGE / 'manifest.json').read_text())
        self.assertEqual(m['menu']['index']['label'], 'MU300')
        html = (PAGE / 'index.html').read_text()
        for s in ('../base1/cockpit.js', 'po.js', 'mu300.js', 'mu300.css'):
            self.assertIn(s, html)
        self.assertNotRegex(html, r'<script>[^<]')            # Cockpit's policy: no inline script
        js = (PAGE / 'mu300.js').read_text()
        for bad in ('innerHTML', 'eval(', "'sh'", '"sh"', "'-c'", 'bash'):
            self.assertNotIn(bad, js)
        self.assertIn("superuser: 'try'", js)

    def test_translations(self):
        r = subprocess.run(['python3', str(TOP / 'tools/cockpit-i18n.py'), 'check'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        d = Path(tempfile.mkdtemp(prefix='mu300-cockpit-po-'))
        try:
            subprocess.run(['python3', str(TOP / 'tools/cockpit-i18n.py'), 'build', str(d)], check=True)
            for lang in ('tr', 'zh_CN'):
                text = (d / f'po.{lang}.js').read_text(encoding='utf-8')
                self.assertTrue(text.startswith('cockpit.locale({'))
                self.assertIn('"Overview": [', text)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_release_and_installers(self):
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('make-extra.sh" panel "$D/mu300-extra-panel.tar.gz"', rel)
        self.assertIn('make-extra.sh" panel-offline "$D/mu300-extra-panel-offline.tar.gz"', rel)
        self.assertIn('EXTRAS="vpn lang vpn-mihomo panel vpn-xray vpn-singbox vpn-dae"', (BIN / 'mu300-update').read_text())
        sh = (TOP / 'install.sh').read_text()
        self.assertIn('adb push "$EXTRA_PANEL" $T/mu300-extra-panel.tar.gz', sh)
        self.assertIn("Install the web panel for Ubuntu (about 24 MB more to download, 50 MB on the device)? (yes/no)')\" no", sh)
        ps = (TOP / 'install.ps1').read_text()
        self.assertIn('& adb push $EXTRA_PANEL "$T/mu300-extra-panel.tar.gz"', ps)


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the page tests')
class Page(unittest.TestCase):
    """mu300.js under Node: a small DOM, and a cockpit object whose spawn records the argument arrays and the stdin,
    and answers as mu300-api would from a table of answers per method (a method not in it is unknown-method)."""

    HARNESS = r'''
const fs = require('fs');
const calls = [];
class Node_ { constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.on = {}; this._text = '';
    this.classList = { toggle: () => {}, add: () => {}, remove: () => {} }; this.disabled = false; this.value = ''; this.checked = false; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  insertBefore(c) { this.children.unshift(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  get firstChild() { return this.children[0] || null; }
  get parentNode() { return this.parent || null; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') ids[v] = this; } getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.on[t] = this.on[t] || []).push(f); }
  click() { (this.on.click || []).forEach((f) => f({ target: this })); }
  set textContent(t) { this.children = []; this._text = String(t); } get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set id(v) { this.setAttribute('id', v); } set className(v) { this.attrs.class = v; }
}
const ids = {};
const text = (t) => { const n = new Node_('#text'); n._text = t; return n; };
const document = { readyState: 'complete', createElement: (t) => new Node_(t), createTextNode: text,
  getElementById: (i) => ids[i] || null, querySelectorAll: () => [], addEventListener: () => {}, body: new Node_('body') };
ids.app = new Node_('div');
const find = (n, pred) => { if (pred(n)) return n; for (const c of n.children || []) { const r = find(c, pred); if (r) return r; } return null; };
const all = (n, pred, out = []) => { if (pred(n)) out.push(n); (n.children || []).forEach((c) => all(c, pred, out)); return out; };
const btn = (root, label) => find(root, (n) => n.tag === 'button' && n.textContent === label);
const OUT = __OUT__;
function spawn(argv, opts) {
  const call = { argv: argv, opts: opts, input: undefined };
  calls.push(call);
  let cb = null;
  const p = new Promise((res, rej) => setTimeout(() => {
    let req = {};
    try { req = JSON.parse(call.input || '{}'); } catch (e) { req = 'bad'; }
    call.req = req;
    let a = OUT[argv[1]];
    if (typeof a === 'function') a = a(req);
    if (a === undefined) a = { ok: 0, error: 'unknown-method' };
    const s = JSON.stringify(a) + '\n';
    if (cb) cb(s);
    if (a.ok === 1) res(cb ? '' : s); else rej({ problem: null, exit_status: 1, message: '' });
  }, 0));
  p.input = (d) => { call.input = d; return p; };
  p.stream = (f) => { cb = f; return p; };
  return p;
}
const cockpit = { gettext: (s) => s, spawn: spawn, format: (f, ...a) => f.replace(/\$(\d)/g, (m, i) => a[i]),
  file: () => ({ read: () => Promise.resolve('Inter-|\n eth0: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0\n') }),
  location: { path: [], go: (p) => { cockpit.location.path = p; } }, translate: () => {}, addEventListener: () => {},
  permission: () => ({ allowed: __ADMIN__, addEventListener: () => {} }) };
const window = { confirm: () => true, prompt: () => 'New name' };
global.TextEncoder = TextEncoder;
new Function('cockpit', 'document', 'window', 'setInterval', 'clearInterval', 'setTimeout', fs.readFileSync(process.argv[2], 'utf8'))(
  cockpit, document, window, () => 0, () => {}, (f, ms) => (ms > 100 ? 0 : setTimeout(f, ms)));
const flush = async (n = 4) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 2)); };
(async function() { await flush(); __BODY__ })().then((r) => process.stdout.write(JSON.stringify({ result: r,
  calls: calls.map((c) => ({ argv: c.argv, opts: c.opts, req: c.req })) })), (e) => { console.error(e); process.exit(1); });
'''

    METHODS = ['hello', 'overview', 'data.get', 'sms.list', 'at.send', 'traffic.get', 'wifi.get', 'vpn.status', 'cpu.get',
               'system.get', 'extra.list', 'backup.export']
    OUT = {
        'hello': {'ok': 1, 'methods': METHODS, 'features': {'locks': False}, 'job': {'kind': '', 'state': 'none'}},
        'overview': {'ok': 1, 'device': 'f50', 'release': 'v1', 'data': {'state': 'up', 'iface': 'sipa_eth0'},
                     'signal': {'operator': '<img src=x onerror=alert(1)>', 'rat': 'LTE'}, 'temperatures': [],
                     'hotspot': {'ssid': 'MU300', 'enabled': True}, 'traffic': {'today': {'rx': 1, 'tx': 1}}},
        'vpn.status': {'ok': 1, 'installed': True, 'status': {'enabled': False, 'running': False, 'kill_switch': {'setting': True}}},
        'vpn.profiles': {'ok': 1, 'profiles': [{'id': 'ok-1', 'name': 'A', 'type': 'xray'}, {'id': '../x', 'name': 'B', 'type': 'xray'}]},
        'vpn.subs': {'ok': 1, 'subscriptions': []}, 'vpn.settings': {'ok': 1, 'settings': {'IPV6': 'auto'}},
        'vpn.use': {'ok': 1}, 'vpn.add': {'ok': 1, 'id': 'p-2'}, 'vpn.install': {'ok': 1, 'job': 'vpn-install'},
        'job.status': {'ok': 1, 'job': {'kind': 'vpn-install', 'state': 'done', 'rc': 0, 'text': 'installed', 'next': 1}},
        'sms.list': {'ok': 1, 'data': {'messages': [{'index': [1, 2], 'sender': '<b>x</b>', 'time': 't', 'parts': 2, 'text': 'hello'},
                                                    {'index': ['3;CFUN=0'], 'sender': 'y', 'time': 't', 'parts': 1, 'text': 'z'}]}},
        'sms.send': {'ok': 1, 'text': 'sent'}, 'sms.delete': {'ok': 1},
        'wifi.get': {'ok': 1, 'enabled': True, 'ssid': 'Net', 'band': '5', 'channel': 'auto', 'channels': {'5': [36], '2.4': [1]},
                     'password_set': True, 'hidden': False, 'clients': []},
        'wifi.set': {'ok': 1, 'restart': True}, 'wificlient.status': {'ok': 1, 'active': False, 'text': ''},
        'wificlient.join': {'ok': 1, 'job': 'wifi-join'},
        'at.send': {'ok': 1, 'rc': 0, 'text': '+CSQ: 20,99\nOK'},
        'data.get': {'ok': 1, 'state': 'up', 'apn': 'internet', 'pdp': 'IP', 'auth': '', 'user': '', 'password_set': True,
                     'sim': None, 'roaming': None, 'roaming_switch': False},
        'data.set': {'ok': 1, 'restart': True},
    }

    def run_page(self, body, out=None, admin='true'):
        h = (self.HARNESS.replace('__OUT__', json.dumps(self.OUT if out is None else out)).replace('__BODY__', body)
             .replace('__ADMIN__', admin))
        r = subprocess.run(['node', '-', str(PAGE / 'mu300.js')], input=h, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def api_calls(self, out, method=None):
        return [(c['argv'][1], c['req']) for c in out['calls'] if method is None or c['argv'][1] == method]

    def test_every_call_is_mu300_api_with_the_request_on_stdin(self):
        out = self.run_page("await flush(); return document.getElementById('app').textContent;")
        # values from the device are text, never markup
        self.assertIn('<img src=x onerror=alert(1)>', out['result'])
        for c in out['calls']:
            self.assertEqual(c['argv'][0], '/opt/mu300/bin/mu300-api')
            self.assertEqual(len(c['argv']), 2)
            self.assertEqual(c['opts'], {'superuser': 'try', 'err': 'message'})
        self.assertEqual(self.api_calls(out)[0], ('hello', {}))
        self.assertIn('overview', [m for m, _ in self.api_calls(out)])

    def test_tabs_follow_the_devices_methods(self):
        body = r"""await flush();
return all(ids.app, (n) => n.tag === 'button' && n.attrs['data-tab']).map((n) => n.attrs['data-tab']);"""
        self.assertEqual(self.run_page(body)['result'], ['overview', 'mobile', 'sms', 'at', 'usage', 'wifi', 'vpn', 'power',
                                                         'system', 'updates', 'backup'])
        # an older mu300-api: only what it has (the VPN tab is there whenever vpn.status is: installed or not)
        old = dict(self.OUT, hello=dict(self.OUT['hello'], methods=['hello', 'overview', 'vpn.status', 'sms.list']))
        self.assertEqual(self.run_page(body, old)['result'], ['overview', 'sms', 'vpn'])

    def test_without_administrative_access_the_page_says_so(self):
        out = self.run_page("await flush(); return document.getElementById('mu-main').textContent;", admin='false')
        self.assertIn('Administrative access is off', out['result'])
        self.assertEqual(self.api_calls(out), [])
        # the access is on but mu300-api still says not-root: the same
        nr = dict(self.OUT, hello={'ok': 0, 'error': 'not-root'})
        out = self.run_page("await flush(); return document.getElementById('mu-main').textContent;", nr, admin='null')
        self.assertIn('Administrative access is off', out['result'])

    def test_sms_send_and_delete(self):
        out = self.run_page(r"""
window.MU300.show('sms'); await flush();
ids['mu-sms-number'].value = '+905551112233'; ids['mu-sms-text'].value = 'hi $(reboot); `id`';
btn(ids['mu-sms-send'], 'Send').click(); await flush();
ids['mu-sms-number'].value = '123; reboot'; btn(ids['mu-sms-send'], 'Send').click(); await flush();
all(ids['mu-sms-list'], (n) => n.tag === 'button' && n.textContent === 'Delete')[0].click(); await flush(8);
return document.getElementById('app').textContent;""")
        sends = [r for m, r in self.api_calls(out, 'sms.send')]
        self.assertEqual(sends, [{'number': '+905551112233', 'text': 'hi $(reboot); `id`'}])
        # the newest first: the message with the bad index is the first Delete; its index is never sent
        deletes = [r for m, r in self.api_calls(out, 'sms.delete')]
        self.assertTrue(all(set(d) == {'id'} and d['id'].isdigit() for d in deletes), deletes)
        self.assertIn('<b>x</b>', out['result'])

    def test_secrets_are_in_the_request_only(self):
        out = self.run_page(r"""
window.MU300.show('wifi'); await flush();
ids['mu-hs-ssid'].value = 'Net2'; ids['mu-hs-psk'].value = 'secret123';
btn(ids['mu-hotspot'], 'Save and restart the hotspot').click(); await flush();
ids['mu-wc-ssid'].value = 'Cafe'; ids['mu-wc-psk'].value = 'cafepass';
btn(ids['mu-wifi-client'], 'Join').click(); await flush();
window.MU300.show('mobile'); await flush();
ids['mu-apn-pw'].value = 'apnpass1'; btn(ids['mu-apn'], 'Save').click(); await flush();
return null;""")
        self.assertEqual(self.api_calls(out, 'wifi.set'), [('wifi.set', {'ssid': 'Net2', 'password': 'secret123'})])
        self.assertEqual(self.api_calls(out, 'wificlient.join'), [('wificlient.join', {'ssid': 'Cafe', 'password': 'cafepass'})])
        self.assertEqual(self.api_calls(out, 'data.set')[0][1]['password'], 'apnpass1')
        for c in out['calls']:
            self.assertFalse(any(s in ' '.join(c['argv']) for s in ('secret123', 'cafepass', 'apnpass1')))

    def test_vpn_profile_ids_are_checked_and_a_link_is_a_link(self):
        out = self.run_page(r"""
window.MU300.show('vpn'); await flush(8);
all(ids['mu-vpn-profiles'], (n) => n.tag === 'button' && n.textContent === 'Use').forEach((b) => b.click()); await flush();
ids['mu-vpn-data'].value = ' vless://abc@host:443?x=y#n '; btn(ids['mu-vpn-profiles'], 'Add').click(); await flush();
return null;""")
        self.assertEqual(self.api_calls(out, 'vpn.use'), [('vpn.use', {'id': 'ok-1'})])
        self.assertEqual(self.api_calls(out, 'vpn.add'), [('vpn.add', {'source': 'link', 'data': 'vless://abc@host:443?x=y#n'})])

    def test_vpn_not_installed_offers_the_module_and_follows_the_job(self):
        out = dict(self.OUT)
        out['vpn.status'] = {'ok': 1, 'installed': False, 'extra': True}
        r = self.run_page(r"""
window.MU300.show('vpn'); await flush();
const t = ids['mu-vpn'].textContent;
btn(ids['mu-vpn'], 'Install the VPN module').click(); await flush(8);
return [t, document.getElementById('mu-job').textContent];""", out)
        self.assertIn('The VPN module is not installed', r['result'][0])
        self.assertIn(('vpn.install', {}), self.api_calls(r))
        self.assertIn(('job.status', {'from': 0}), self.api_calls(r))
        self.assertIn('installed', r['result'][1])

    def test_sim_radio_and_voice(self):
        out = dict(self.OUT, hello=dict(self.OUT['hello'], methods=self.METHODS + ['radio.get', 'radio.set', 'voice.get', 'voice.set']))
        out['radio.get'] = {'ok': 1, 'radio': {'enabled': [True, False], 'all_off': False, 'active': 0, 'slot1_supported': 1}}
        out['radio.set'] = {'ok': 1, 'text': ''}
        out['voice.get'] = {'ok': 1, 'voice': {'slot': 0, 'slots': [{'volte': True, 'vonr': None}, {'volte': False, 'vonr': True}], 'live': None}}
        out['voice.set'] = {'ok': 1, 'text': ''}
        r = self.run_page(r"""
window.MU300.show('mobile'); await flush(8);
btn(ids['mu-radio'], 'All radios off (airplane mode)').click(); await flush();
all(ids['mu-radio'], (n) => n.tag === 'button' && n.textContent === 'Turn on')[0].click(); await flush();
all(ids['mu-voice'], (n) => n.tag === 'button' && n.textContent === 'Turn on')[0].click(); await flush();
return null;""", out)
        self.assertEqual([q for m, q in self.api_calls(r, 'radio.set')], [{'slot': 'all', 'state': 'off'}, {'slot': 2, 'state': 'on'}])
        self.assertEqual([q for m, q in self.api_calls(r, 'voice.set')], [{'slot': 1, 'kind': 'vonr', 'state': 'on'}])

    LOCKS = {'ok': 1, 'data': {'auto_apply': 1, 'ts': 1700000000, 'mode': {'label': 'auto'}, 'endc': '1',
                               'lte': {'locked': ''}, 'nr': {'locked': '78'}, 'cell': '', 'cells': ['nr:627264,393'],
                               'caps': {'nr': '1,5,6,8,28,41,78', 'lte': '1,3,5,8,34,38,39,40,41'}}}

    def parity(self):
        out = dict(self.OUT, hello=dict(self.OUT['hello'], methods=self.METHODS + [
            'lock.get', 'lock.set', 'sim.state', 'sms.forward', 'sms.forward_set', 'sms.forward_test', 'sms.forward_log_clear',
            'at.history', 'usb.net', 'lan.get'], features={'locks': True, 'forward': True, 'usb_net': True}))
        out['lock.get'] = self.LOCKS
        out['lock.set'] = {'ok': 1, 'data': {'ok': 1, 'started': 1, 'queued': 0}}
        out['sim.state'] = {'ok': 1, 'state': 'absent', 'slot': 2}
        out['sms.forward'] = {'ok': 1, 'data': {'ok': 1, 'enabled': 1, 'template': '{text}', 'webhook': 1, 'webhook_url_set': 1,
                                                'webhook_url_hint': 'https://hook.example.com/...', 'webhook_format': 'json',
                                                'telegram_token_set': 1, 'telegram_token_hint': '123:...', 'email_available': 0,
                                                'queued': 2, 'log': [{'time': 1, 'id': '7', 'target': 'webhook', 'result': 'sent',
                                                                      'detail': '<i>HTTP 200</i>'}]}}
        out['sms.forward_set'] = {'ok': 1}
        out['sms.forward_test'] = {'ok': 1, 'started': True}
        out['sms.forward_log_clear'] = {'ok': 1}
        out['at.history'] = {'ok': 1, 'history': [{'time': '10-18 01:00:00', 'command': 'AT+COPS?'}]}
        out['usb.net'] = {'ok': 1, 'mode': 'ecm', 'once': True, 'now': 'ncm'}
        out['lan.get'] = {'ok': 1, 'ip': '192.168.79.1', 'usb_role_switch': False, 'adb': {'state': 'on', 'auth': 'open'}}
        out['system.get'] = {'ok': 1, 'release': 'v1'}
        out['data.get'] = dict(self.OUT['data.get'], sim={'active': 2, 'default': 2, 'slot1_supported': 1})
        return out

    def test_locks(self):
        r = self.run_page(r"""
window.MU300.show('mobile'); await flush(10);
const t = ids['mu-locks'].textContent;
ids['mu-lock-mode'].value = '4g'; btn(ids['mu-locks'], 'Apply').click(); await flush();
ids['mu-lock-nr-41'].checked = true; btn(ids['mu-locks'], 'Apply NR bands').click(); await flush();
btn(ids['mu-locks'], 'Apply LTE bands').click(); await flush();
ids['mu-lock-arfcn'].value = '627264'; ids['mu-lock-pci'].value = '393'; btn(ids['mu-locks'], 'Lock this cell').click(); await flush();
ids['mu-lock-arfcn'].value = '62x'; btn(ids['mu-locks'], 'Lock this cell').click(); await flush();
btn(ids['mu-locks'], 'Turn EN-DC off').click(); await flush();
btn(ids['mu-locks'], 'Do not apply at startup').click(); await flush();
btn(ids['mu-locks'], 'Reset all to automatic').click(); await flush();
return t;""", self.parity())
        self.assertIn('n78', r['result'])
        self.assertIn('NR ARFCN 627264, PCI 393', r['result'])
        self.assertIn(('lock.get', {}), self.api_calls(r))
        self.assertEqual([q for m, q in self.api_calls(r, 'lock.set')], [
            {'kind': 'mode', 'val': '4g'}, {'kind': 'nr', 'val': '41,78'}, {'kind': 'lte', 'val': ''},
            {'kind': 'cell', 'val': 'nr:627264,393'}, {'kind': 'endc', 'val': 'off'}, {'kind': 'auto_apply', 'val': 'off'},
            {'kind': 'reset', 'val': 'auto'}])

    def test_sim_card_state(self):
        r = self.run_page(r"""
window.MU300.show('mobile'); await flush(10);
const t = ids['mu-sim'].textContent;
btn(ids['mu-sim'], 'Ask the card').click(); await flush(6);
return t;""", self.parity())
        self.assertIn('Card (SIM 2)No card in this slot', r['result'])
        self.assertEqual([q for m, q in self.api_calls(r, 'sim.state')], [{}, {'fresh': True}])

    def test_forwarding(self):
        r = self.run_page(r"""
window.MU300.show('sms'); await flush(10);
const t = ids['mu-sms-forward'].textContent + ids['mu-sms-forward-log'].textContent;
ids['mu-fwd-body-preset'].value = 'wecom'; (ids['mu-fwd-body-preset'].on.change || []).forEach((f) => f({}));
ids['mu-fwd-telegram_token'].value = '987:' + 'B'.repeat(30); ids['mu-fwd-webhook_url-clear'].checked = true;
btn(ids['mu-sms-forward'], 'Save').click(); await flush(8);
btn(ids['mu-sms-forward'], 'Send a test').click(); await flush();
btn(ids['mu-sms-forward-log'], 'Clear the log').click(); await flush();
return t;""", self.parity())
        self.assertIn('Saved: https://hook.example.com/...', r['result'])
        self.assertIn('<i>HTTP 200</i>', r['result'])
        sets = [q for m, q in self.api_calls(r, 'sms.forward_set')]
        self.assertEqual(len(sets), 1)
        q = sets[0]
        self.assertEqual((q['enabled'], q['webhook'], q['telegram']), (True, True, False))
        self.assertEqual(q['webhook_body'], '{"msgtype":"text","text":{"content":"{message}"}}')
        self.assertEqual((q['webhook_expect'], q['webhook_format']), ('errcode=0', 'json'))
        self.assertEqual(q['telegram_token'], '987:' + 'B' * 30)
        self.assertEqual(q['clear'], 'webhook_url')
        self.assertEqual(q['webhook_url'], '')
        self.assertIn(('sms.forward_test', {}), self.api_calls(r))
        self.assertIn(('sms.forward_log_clear', {}), self.api_calls(r))
        for c in r['calls']:
            self.assertNotIn('BBBBBBBB', ' '.join(c['argv']))

    def test_at_history_and_usb_network(self):
        r = self.run_page(r"""
window.MU300.show('at'); await flush(8);
const h = ids['mu-at-history'].textContent;
btn(ids['mu-at-history'], 'AT+COPS?').click();
const v = ids['mu-at-cmd'].value;
window.MU300.show('system'); await flush(10);
const u = ids['mu-usbnet'].textContent;
ids['mu-usbnet-mode'].value = 'rndis'; ids['mu-usbnet-scope'].value = 'once';
btn(ids['mu-usbnet'], 'Save (used at the next boot)').click(); await flush();
ids['mu-usbnet-mode'].value = 'default';
btn(ids['mu-usbnet'], 'Save (used at the next boot)').click(); await flush();
return [h, v, u];""", self.parity())
        self.assertIn('10-18 01:00:00', r['result'][0])
        self.assertEqual(r['result'][1], 'AT+COPS?')
        self.assertIn('ECM (that boot only)', r['result'][2])
        self.assertEqual([q for m, q in self.api_calls(r, 'usb.net') if q], [{'mode': 'rndis', 'scope': 'once'}, {'mode': 'default'}])

    def test_errors_are_worded(self):
        out = self.run_page(r"""
const M = window.MU300;
return [M.errText({ code: 'not-root' }), M.errText({ code: 'bad-value', detail: 'ssid' }), M.errText({ code: 'failed', detail: 'no SIM' }),
        M.errText({ problem: 'not-found' })];""")
        self.assertIn('administrative access', out['result'][0])
        self.assertEqual(out['result'][1:], ['Not a valid value: ssid', 'no SIM', 'The command is not on this system.'])

    def test_helpers(self):
        out = self.run_page(r"""const M = window.MU300;
return [M.netDev('Inter-|\n  lo: 1 0 0 0 0 0 0 0 2 0\n sipa_eth0: 300 5 0 0 0 0 0 0 400 6 0 0 0 0 0 0\n'),
        M.fmtBytes(1536), M.fmtRate(125000), M.mask('8612345678901234')];""")
        self.assertEqual(out['result'], [{'lo': {'rx': 1, 'tx': 2}, 'sipa_eth0': {'rx': 300, 'tx': 400}}, '1.5 KiB',
                                         '1.0 Mbit/s', '8612****234'])

    def test_every_page_renders(self):
        """each tab against answers of the right shape, and against a device that refuses everything"""
        for out in (None, {'hello': self.OUT['hello']}):
            r = self.run_page(r"""const res = {};
for (const t of ['overview', 'mobile', 'sms', 'at', 'usage', 'wifi', 'vpn', 'power', 'system', 'updates', 'backup']) {
  window.MU300.show(t); await flush(8); res[t] = document.getElementById('mu-main').textContent.length; }
return res;""", out)
            self.assertTrue(all(v > 0 for v in r['result'].values()), r['result'])


class SmsJson(ShellTest):
    """`sms json`: the SIM's messages, long ones joined, as one JSON object (the Ubuntu panel's SMS page)"""

    def setUp(self):
        super().setUp()
        self.stub('mu300-at', AT_STUB)
        (self.tmp / 'at').mkdir()
        (self.tmp / 'at' / 'CPMS').write_text('+CPMS: 2,50,2,50,2,50\nOK\n')

    def run_json(self, shell, cmgl):
        (self.tmp / 'at' / 'CMGL4').write_text(cmgl)
        r = self.script(shell, BIN / 'sms', 'json', MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_messages(self):
        hostile = Pool.part(0x45, 1, 1, 'a "quote" \\ back\tslash ü')
        for shell in self.each_shell():
            j = self.run_json(shell, f'+CMGL: 3,0,,40\n{PDU_GSM}\n+CMGL: 7,1,,56\n{PDU_UCS2}\n+CMGL: 9,0,,30\n{hostile}\nOK\n')
            self.assertEqual(j['store'], 'SM')
            m = {x['index'][0]: x for x in j['messages']}
            self.assertEqual(m[3]['sender'], '+31641600986')
            self.assertEqual(m[7]['sender'], 'ADANA BLD')
            self.assertEqual(m[9]['text'], 'a "quote" \\ back slash ü')
            self.assertEqual(self.run_json(shell, 'OK\n'), {'store': 'SM', 'messages': []})


if __name__ == '__main__':
    unittest.main()
