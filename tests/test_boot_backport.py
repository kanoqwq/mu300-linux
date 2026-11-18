"""Boot ordering and readiness checks with private command/file fixtures only."""
import json
import os
import subprocess
import signal
import time
import tempfile
import unittest
from pathlib import Path
from helpers import TOP, BIN, ShellTest, ucode

class HardwareOrdering(ShellTest):
    def setUp(self):
        super().setUp()
        for name in ['led-status','extra-modules','zram-start','wifi-start','regdb-load','openwrt-wifi-config','mu300-nfc']:
            self.stub(name,'echo "'+name+'" >> "$STUBLOG/order"\nexit 0')
        self.stub('openwrt-board-check','echo board-check >> "$STUBLOG/order"; exit "${BOARD_FAIL:-0}"')
        self.stub('wifi-ready','exit "${READY_FAIL:-0}"')
        self.stub('uci','echo CN')
        self.stub('iw','echo "iw:$*" >> "$STUBLOG/order"; echo "country CN:"')
        self.stub('wifi','echo "wifi:$*" >> "$STUBLOG/order"')
        self.stub('sha256sum','echo "${CONFIG_HASH:-same}"')
        source=(TOP/'openwrt/overlay/etc/init.d/mu300-hw').read_text()
        source=source.replace('/tmp/mu300-',str(self.tmp)+'/mu300-').replace('/opt/mu300/bin/',str(self.stubs)+'/')
        source=source.replace('sha256sum ',str(self.stubs/'sha256sum')+' ')
        self.code=source+'\nhw_bringup\n'

    def test_rules_precede_phy_and_ready_ap_is_not_restarted(self):
        for shell in self.each_shell():
            (self.tmp/'order').unlink(missing_ok=True)
            r=self.sh(shell,self.code);self.assertEqual(r.returncode,0,r.stderr)
            events=(self.tmp/'order').read_text().splitlines()
            self.assertLess(events.index('board-check'),events.index('wifi-start'))
            self.assertLess(events.index('regdb-load'),events.index('wifi-start'))
            self.assertLess(events.index('wifi-start'),events.index('extra-modules'))
            self.assertNotIn('wifi:down',events);self.assertNotIn('wifi:up',events)

    def test_unready_ap_reconciles_once_and_invalid_board_stops_bringup(self):
        for shell in self.each_shell():
            for fail in [0,1]:
                (self.tmp/'order').unlink(missing_ok=True)
                r=self.sh(shell,self.code,READY_FAIL=2,BOARD_FAIL=fail)
                events=(self.tmp/'order').read_text().splitlines()
                if fail:self.assertNotEqual(r.returncode,0);self.assertNotIn('wifi-start',events)
                else:self.assertEqual(events.count('wifi:down'),1);self.assertEqual(events.count('wifi:up'),1)

    def test_pending_ap_is_not_restarted_and_changed_settings_still_apply(self):
        for shell in self.each_shell():
            (self.tmp/'order').unlink(missing_ok=True)
            result=self.sh(shell,self.code,READY_FAIL=1)
            self.assertEqual(result.returncode,0,result.stderr)
            events=(self.tmp/'order').read_text().splitlines()
            self.assertNotIn('wifi:down',events);self.assertNotIn('wifi:up',events)
            # A changed configuration still needs one real application.
            self.stub('sha256sum', 'if [ -e "$STUBLOG/hashed" ]; then echo changed; else touch "$STUBLOG/hashed"; echo old; fi')
            (self.tmp/'hashed').unlink(missing_ok=True)
            (self.tmp/'order').unlink(missing_ok=True)
            result=self.sh(shell,self.code,READY_FAIL=1)
            self.assertEqual(result.returncode,0,result.stderr)
            events=(self.tmp/'order').read_text().splitlines()
            self.assertEqual(events.count('wifi:down'),1);self.assertEqual(events.count('wifi:up'),1)
            self.stub('sha256sum','echo "${CONFIG_HASH:-same}"')

    def test_country_already_matches_without_duplicate_request_and_restores_after_phy_reset(self):
        self.stub('sleep','echo "sleep:$*" >> "$STUBLOG/order"')
        self.code=self.code.replace('sleep ',str(self.stubs/'sleep')+' ')
        self.stub('iw', '''echo "iw:$*" >> "$STUBLOG/order"
case "$*" in
 "reg set CN") touch "$STUBLOG/reg-cn" ;;
 "reg get") if [ "${REG_NEVER_READY:-0}" = 0 ] && [ -e "$STUBLOG/reg-cn" ]; then echo "country CN:"; else echo "country 00:"; fi ;;
esac''')
        self.stub('wifi-start', '''echo wifi-start >> "$STUBLOG/order"
[ "${PHY_RESETS_REG:-0}" != 1 ] || rm -f "$STUBLOG/reg-cn"
exit 0''')
        for shell in self.each_shell():
            for reset, never in ((0,0),(1,0),(1,1)):
                (self.tmp/'order').unlink(missing_ok=True)
                (self.tmp/'reg-cn').unlink(missing_ok=True)
                result=self.sh(shell,self.code,PHY_RESETS_REG=reset,REG_NEVER_READY=never,READY_FAIL=1)
                self.assertEqual(result.returncode,0,result.stderr)
                events=(self.tmp/'order').read_text().splitlines()
                self.assertEqual(events.count('iw:reg set CN'),1+reset)
                self.assertEqual(events.count('sleep:1'),10 if never else 0)
                self.assertEqual(events.count('wifi:up'),1 if never else 0)

    def test_optional_modules_do_not_block_ap_and_nfc_waits_for_them(self):
        for shell in self.each_shell():
            gate=self.tmp/'extra-gate';gate.unlink(missing_ok=True);os.mkfifo(gate)
            (self.tmp/'order').unlink(missing_ok=True)
            done=self.tmp/'main-returned';done.unlink(missing_ok=True)
            self.stub('extra-modules','echo extras-start >> "$STUBLOG/order"; read -r line < "$STUBLOG/extra-gate"; echo extras-done >> "$STUBLOG/order"')
            with (self.tmp/'process.log').open('w') as output:
                process=subprocess.Popen(shell+['-c',self.code+'\necho done > "$STUBLOG/main-returned"\n'],
                                         env=self.env(READY_FAIL=2),stdout=output,stderr=output,start_new_session=True)
                try:
                    deadline=time.monotonic()+4
                    while not done.exists() and time.monotonic()<deadline:time.sleep(.02)
                    self.assertTrue(done.exists(),'AP reconciliation waited for optional modules')
                    events=(self.tmp/'order').read_text().splitlines()
                    self.assertIn('wifi:up',events)
                    self.assertNotIn('mu300-nfc',events)
                    with gate.open('w') as writer:writer.write('continue\n')
                    deadline=time.monotonic()+4
                    while time.monotonic()<deadline:
                        events=(self.tmp/'order').read_text().splitlines()
                        if events.count('mu300-nfc')==2:break
                        time.sleep(.02)
                    self.assertLess(events.index('openwrt-wifi-config'),events.index('mu300-nfc'))
                    self.assertLess(events.index('extras-done'),events.index('zram-start'))
                    self.assertLess(events.index('zram-start'),events.index('mu300-nfc'))
                    self.assertEqual(process.wait(timeout=2),0)
                finally:
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    process.wait(timeout=2)

class WirelessInterfaceWait(ShellTest):
    def setUp(self):
        super().setUp()
        self.net=self.tmp/'net';self.net.mkdir()
        modules=self.tmp/'modules';modules.mkdir()
        for name in ('wcn_bsp','sprd_wlan_combo'):(modules/(name+'.ko')).touch()
        (self.tmp/'loaded').write_text('')
        self.stub('depmod','exit 0')
        self.stub('uname','echo test-kernel')
        self.stub('modprobe','exit 0')
        self.stub('insmod','echo insmod >> "$STUBLOG/loads"')
        self.stub('readiness-timer', '''[ "$1:$2" = usleep:100000 ] || exit 90
[ "${TIMER_FAIL:-0}" = 0 ] || exit 91
n=0; [ ! -f "$STUBLOG/ticks" ] || read -r n < "$STUBLOG/ticks"
n=$((n + 1)); echo "$n" > "$STUBLOG/ticks"
[ "$n" != "${READY_AFTER:-3}" ] || mkdir -p "$STUBLOG/net/wlan0"
exit 0''')
        source=(BIN/'wifi-start').read_text().replace('/opt/mu300/bin/',str(self.stubs)+'/')
        source=source.replace('/lib/modules/$(uname -r)',str(modules))
        source=source.replace(str(self.stubs/'busybox'),str(self.stubs/'readiness-timer'))
        source=source.replace('/proc/modules',str(self.tmp/'loaded'))
        source=source.replace('/sys/class/net',str(self.net))
        source=source.replace('/proc/mdbg/at_cmd',str(self.tmp/'no-at-cmd'))
        for command in ('depmod','modprobe','insmod'):
            source=source.replace(command+' ',str(self.stubs/command)+' ')
        self.code=source

    def test_stops_as_soon_as_interface_appears_and_preserves_timeout(self):
        for shell in self.each_shell():
            for ready_after,want in ((3,3),(0,300)):
                (self.net/'wlan0').rmdir() if (self.net/'wlan0').exists() else None
                (self.tmp/'ticks').unlink(missing_ok=True)
                result=self.sh(shell,self.code,READY_AFTER=ready_after)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(int((self.tmp/'ticks').read_text()),want)

    def test_loaded_driver_does_not_reload_and_timer_failure_is_reported(self):
        for shell in self.each_shell():
            (self.net/'wlan0').mkdir(exist_ok=True)
            (self.tmp/'loads').unlink(missing_ok=True)
            result=self.sh(shell,self.code)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((self.tmp/'loads').exists())
            (self.net/'wlan0').rmdir()
            result=self.sh(shell,self.code,TIMER_FAIL=1)
            self.assertEqual(result.returncode,91,result.stderr)


class WirelessReadiness(unittest.TestCase):
    def test_all_enabled_interfaces_and_user_disabled_radio(self):
        from native_dashboard_fixture import Snapshot
        cmd=ucode()
        if not cmd:self.skipTest('ucode required')
        with tempfile.TemporaryDirectory() as td:
            s=Snapshot(td)
            source=(BIN/'wifi-ready').read_text().replace("from 'ubus'","from './bus.uc'")
            source=source.replace('cursor()','cursor(getenv("MU300_DASH_CONFIG"))')
            script=s.module/'wifi-ready';script.write_text(source)
            def run(*args):
                (s.root/'rpc.json').write_text(json.dumps(s.responses))
                return subprocess.run(cmd+[str(script),*args],env=dict(os.environ,MU300_DASH_CONFIG=str(s.config),DASH_FIXTURE=td),capture_output=True,text=True,timeout=5)
            result=run();self.assertEqual(result.returncode,0,result.stderr)
            radio=s.responses['network.wireless.status']['radio0']
            radio['config']={'ssid':'private-ssid','key':'private-wifi-key'}
            diagnostic=run('--state')
            self.assertEqual(json.loads(diagnostic.stdout)['result'],0)
            self.assertNotIn('private-ssid',diagnostic.stdout)
            self.assertNotIn('private-wifi-key',diagnostic.stdout)
            s.responses['hostapd.wlan0.get_status']={'status':'DISABLED'};self.assertEqual(run().returncode,2)
            s.responses['hostapd.wlan0.get_status']={'status':'ENABLED'}
            radio=s.responses['network.wireless.status']['radio0']
            radio['pending']=True;self.assertEqual(run().returncode,1)
            radio['pending']=False;radio['retry_setup_failed']=True;self.assertEqual(run().returncode,2)
            radio['retry_setup_failed']=False;radio['up']=False;self.assertEqual(run().returncode,2)
            radio['autostart']=False;self.assertEqual(run().returncode,0)
            radio['autostart']=True;radio['up']=True
            (s.config/'wireless').write_text("config wifi-device 'radio0'\n option disabled '1'\n")
            self.assertEqual(run().returncode,0,'a disabled radio must stay disabled without a global restart')


class StartupDelivery(ShellTest):
    """The actual generator/applier replaces image code and keeps user settings."""
    @unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'OTA ownership requires POSIX root')
    def test_actual_diff_updates_startup_scripts_and_preserves_disabled_wifi(self):
        import sys
        sys.path.insert(0, str(TOP/'installer/tests'))
        from test_ota import mu_release, analyze, Engine, Patch
        files = {'etc/init.d/mu300-hw': TOP/'openwrt/overlay/etc/init.d/mu300-hw',
                 'opt/mu300/bin/wifi-ready': BIN/'wifi-ready',
                 'opt/mu300/bin/wifi-start': BIN/'wifi-start'}
        current = {name: path.read_bytes() for name, path in files.items()}
        old = {name: b'#!/bin/sh\n# previous image-owned program\n' for name in files}
        user = {'etc/config/wireless': b"config wifi-device 'radio0'\n option disabled '1'\n",
                'etc/config/network': b'user LAN and WAN configuration\n',
                'etc/mu300/user-settings': b'preserve user preferences\n'}
        live = self.tmp/'live'
        for name, data in dict(old, **user).items():
            path = live/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(data)
            if name in old:path.chmod(0o755)
        (live/'etc/mu300/release.json').write_text(json.dumps(dict(profile='mu300', image_version='mu300-r1', kernel_release='7.2.8')))
        for version, data in ((1,old),(2,current),(3,{k:v+b'\n# next image revision\n' for k,v in current.items()})):
            root = self.tmp/str(version)
            mu_release(root, 'mu300-r'+str(version), data)
            # Use the same executable modes as build-rootfs, not the fixture default.
            import io, tarfile, hashlib
            archive = root/'payload/mu300-openwrt.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                parents = {str(p) for name in data for p in Path(name).parents if str(p) != '.'}
                for name in sorted(parents, key=lambda n:(n.count('/'),n)):
                    item=tarfile.TarInfo(name);item.type=tarfile.DIRTYPE;item.mode=0o755;tar.addfile(item)
                for name, content in data.items():
                    item=tarfile.TarInfo(name);item.mode=0o755;item.size=len(content);tar.addfile(item,io.BytesIO(content))
            manifest=root/'desktop/manifest.json';meta=json.loads(manifest.read_text())
            meta['magisk']['payload/mu300-openwrt.tar.gz']=hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(meta))
        for start in (1,2):
            output=self.tmp/('startup-%d.ota'%start)
            result=analyze(self.tmp/str(start),self.tmp/str(start+1),output)
            self.assertTrue(result['can_export'],result['blockers'])
            with Patch(output) as patch:Engine(live).apply(patch)
            for name, data in current.items():
                self.assertEqual((live/name).read_bytes(),data+(b'\n# next image revision\n' if start==2 else b''))
                self.assertTrue((live/name).stat().st_mode & 0o111)
            for name, data in user.items():self.assertEqual((live/name).read_bytes(),data)

    def test_ordinary_update_replaces_startup_code_without_enabling_wifi(self):
        import io, tarfile
        from test_update import Update
        # Reuse the real ordinary updater fixture, not a model of its copy rules.
        fixture=Update();fixture.setUp()
        try:
            for shell in fixture.each_shell():
                old, user=fixture.vendor_update_fixture()
                programs={'etc/init.d/mu300-hw':TOP/'openwrt/overlay/etc/init.d/mu300-hw',
                          'opt/mu300/bin/wifi-ready':BIN/'wifi-ready',
                          'opt/mu300/bin/wifi-start':BIN/'wifi-start'}
                for name in programs:
                    path=old/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'old startup code');path.chmod(0o755)
                disabled=b"config wifi-device 'radio0'\n option disabled '1'\n"
                (old/'etc/config/wireless').write_bytes(disabled)
                archive=fixture.disk/'.mu300-update/mu300-openwrt-rootfs.tar.gz'
                entries={'sbin/init':b'#!/bin/sh\n','etc/mu300/image-version':b'test-new\n'}
                entries.update({name:path.read_bytes() for name,path in programs.items()})
                with tarfile.open(archive,'w:gz') as tar:
                    for name,data in entries.items():
                        item=tarfile.TarInfo(name);item.mode=0o755;item.size=len(data);tar.addfile(item,io.BytesIO(data))
                result=fixture.up(shell,'sync() { :; }; is_root() { false; }; apply_one openwrt test-new')
                self.assertEqual(result.returncode,0,result.stderr)
                for name,path in programs.items():
                    self.assertEqual((old/name).read_bytes(),path.read_bytes())
                    self.assertTrue((old/name).stat().st_mode & 0o111)
                self.assertEqual((old/'etc/config/wireless').read_bytes(),disabled)
                import shutil
                shutil.rmtree(old);shutil.rmtree(fixture.disk/'openwrt.old')
        finally:fixture.tearDown()

class BootContracts(unittest.TestCase):
    def test_device_entrypoints_have_unix_line_endings(self):
        for name in ['boot/init','openwrt/build-rootfs.sh',
                     'openwrt/overlay/lib/preinit/04_mu300_early_leds',
                     'openwrt/overlay/etc/init.d/mu300-hw',
                     'openwrt/overlay/etc/init.d/mu300-service-cpus',
                     'rootfs/overlay/opt/mu300/bin/mu300-led',
                     'rootfs/overlay/opt/mu300/bin/mu300-modemd',
                     'rootfs/overlay/opt/mu300/bin/mu300-service-cpus',
                     'rootfs/overlay/opt/mu300/bin/wifi-ready',
                     'rootfs/overlay/opt/mu300/bin/sipa-dele-start']:
            self.assertNotIn(b'\r\n',(TOP/name).read_bytes(),name)

    def test_recovery_and_full_resume_wait_are_retained(self):
        init=(TOP/'boot/init').read_text()
        self.assertNotIn('.kmodloader-once',init)
        self.assertIn('head -c 1 /dev/random',init)
        self.assertIn('echo 4 > /proc/sys/kernel/printk',init)
        driver=(TOP/'upstream/port/drivers/pci/controller/dwc/pcie-sprd.c').read_text()
        probe=driver[driver.index('static int sprd_pcie_probe('):]
        self.assertIn('sprd_pcie_link_up_soon(pci)',probe)
        self.assertIn('dw_pcie_wait_for_link(pci)',driver[:driver.index('static int sprd_pcie_probe(')])
        self.assertIn('sprd_pcie_clear_unhandled_msi',driver)

class EarlyLeds(ShellTest):
    def test_preinit_lights_before_usb_or_recorder_without_a_usb_interface(self):
        hooks=TOP/'openwrt/overlay/lib/preinit'
        self.assertLess('04_mu300_early_leds','05_mu300_early_recorder')
        for name in ('led-status','bootmark'):
            self.stub(name,'echo "'+name+' $*" >> "$STUBLOG/calls"')
        source=(hooks/'04_mu300_early_leds').read_text().replace('/opt/mu300/bin/',str(self.stubs)+'/')
        code='''boot_hook_add() { printf '%s %s' "$1" "$2"; }
'''+source+'\nmu300_early_leds\n'
        for shell in self.each_shell():
            (self.tmp/'calls').write_text('')
            result=self.sh(shell,code)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,'preinit_essential mu300_early_leds')
            self.assertEqual((self.tmp/'calls').read_text().splitlines(),
                             ['led-status boot','bootmark early-leds'])
        self.assertNotIn('led-status boot',(hooks/'06_mu300_early_usb').read_text())

class DelegateReadiness(ShellTest):
    def setUp(self):
        super().setUp()
        (self.tmp/'modules').mkdir();(self.tmp/'modules/sipa-dele.ko').write_text('test file only')
        (self.tmp/'loaded-modules').write_text('');(self.tmp/'tty').touch()
        node=self.tmp/'sysfs'/'cp';node.mkdir(parents=True)
        (node/'sipa_dele_wait').write_text('1\n')
        (node/'sipa_dele_ready').write_text('1\n')
        self.stub('dmesg','echo unexpected >> "$STUBLOG/dmesg-calls"; exit 99')
        self.stub('uci',"case \"$*\" in *sim_slots) echo 2;; *data_sim) echo 0;; *) exit 1;; esac")
        self.stub('mu300-at',"""[ "$MU300_AT_DAEMON_ONLY" = 1 ] || exit 99
printf '%s %s\n' "$MU300_AT_DIR" "$*" >> "$STUBLOG/at-calls"
case "$MU300_AT_DIR:$*" in
 /run/mu300-at:*'AT+SPACTCARD=0;+CGATT?'|/run/mu300-at4:*'AT+SPACTCARD=1;+CGATT?') ;;
 *) exit 98;;
esac
printf '+CGATT: %s\\n' "${ATTACHED:-1}"
""")
        self.stub('cat',"""case "$1" in
 */sipa_dele_wait)
  echo wait >> "$STUBLOG/gate-calls"
  [ "${GATE_FAIL:-0}" = 0 ] || exit 1
  ;;
esac
exec /bin/cat "$@"
""")
        self.stub('insmod','echo load >> "$STUBLOG/loads"; exit "${LOAD_FAIL:-0}"')
        self.stub('sleep','echo "$1" >> "$STUBLOG/sleeps"; echo "1.0 0" > "$STUBLOG/uptime"')
        source=(BIN/'sipa-dele-start').read_text()
        source=source.replace('/lib/modules/$(uname -r)',str(self.tmp/'modules'))
        source=source.replace('/proc/modules',str(self.tmp/'loaded-modules')).replace('/proc/uptime',str(self.tmp/'uptime'))
        source=source.replace('/dev/stty_nr1',str(self.tmp/'tty')).replace('[ -c ','[ -f ')
        for command in ['mu300-at','insmod','dmesg','sleep','cat']:
            source=source.replace(command+' ',str(self.stubs/command)+' ')
        source=source.replace('${MU300_SIM_ENV_LIB:-/opt/mu300/bin/mu300-sim-env}',str(BIN/'mu300-sim-env'))
        source=source.replace('${MU300_DELE_SYSFS:-/sys/bus/platform/drivers/sipa_delegate}',str(self.tmp/'sysfs'))
        self.code=source

    def run_delegate(self,shell,**env):
        for name in ['loads','sleeps','at-calls','gate-calls','dmesg-calls']:(self.tmp/name).unlink(missing_ok=True)
        (self.tmp/'uptime').write_text('0.0 0')
        return self.sh(shell,self.code,MU300_DELE_WAIT=1,**env)

    def test_packet_attach_and_handshake_finish_without_fixed_delay(self):
        for shell in self.each_shell():
            p=self.run_delegate(shell)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual((self.tmp/'loads').read_text().splitlines(),['load'])
            self.assertFalse((self.tmp/'sleeps').exists())
            self.assertFalse((self.tmp/'dmesg-calls').exists())
            self.assertEqual((self.tmp/'gate-calls').read_text().splitlines(),['wait'])

    def test_no_packet_attach_never_loads_and_load_failure_is_not_success(self):
        for shell in self.each_shell():
            p=self.run_delegate(shell,ATTACHED=0)
            self.assertNotEqual(p.returncode,0);self.assertFalse((self.tmp/'loads').exists())
            p=self.run_delegate(shell,LOAD_FAIL=1)
            self.assertNotEqual(p.returncode,0);self.assertNotIn('channel and resource callbacks ready',p.stdout)

    def test_sim2_attach_uses_sim2_owner_and_explicit_scope(self):
        for shell in self.each_shell():
            p=self.run_delegate(shell,MU300_SIM_SLOT=1)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn('/run/mu300-at4', (self.tmp/'at-calls').read_text())
            self.assertIn('AT+SPACTCARD=1;+CGATT?',(self.tmp/'at-calls').read_text())
            self.assertNotIn('SPACTCARD=0',(self.tmp/'at-calls').read_text())

    def test_failed_or_missing_handshake_is_not_success(self):
        for shell in self.each_shell():
            p=self.run_delegate(shell,GATE_FAIL=1)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('handshake not ready',p.stdout)
            node=self.tmp/'sysfs/cp/sipa_dele_wait'
            node.rename(node.with_suffix('.saved'))
            p=self.run_delegate(shell)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('matching delegate module required',p.stdout)
            node.with_suffix('.saved').rename(node)

    def test_loaded_module_still_requires_handshake_but_no_more_at(self):
        for shell in self.each_shell():
            (self.tmp/'loaded-modules').write_text('sipa_dele 123 0\n')
            for fail in (0,1):
                p=self.run_delegate(shell,GATE_FAIL=fail)
                self.assertEqual(p.returncode==0,fail==0,p.stderr)
                self.assertFalse((self.tmp/'loads').exists())
                self.assertFalse((self.tmp/'at-calls').exists())
            (self.tmp/'loaded-modules').write_text('')

    def test_invalid_handshake_value_is_not_success(self):
        for shell in self.each_shell():
            node=self.tmp/'sysfs/cp/sipa_dele_wait'
            for value in ('0\n','2\n','-19\n',''):
                node.write_text(value)
                self.assertNotEqual(self.run_delegate(shell).returncode,0)
            node.write_text('1\n')
