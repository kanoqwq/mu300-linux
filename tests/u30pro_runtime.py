"""Exercise hardware-service and screen routing with regular-file mocks only."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
OVERLAY = TOP/'devices/u30pro/overlay'


@unittest.skipUnless(os.name == 'posix', 'POSIX runtime scripts')
class Runtime(unittest.TestCase):
    def test_screen_and_addon_reconnect_use_netifd_owner_and_report_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binpath=root/'bin';binpath.mkdir()
            scripts={
                'uci':'case "$*" in *data_interface) echo customwan;; *data_sim) echo 1;; esac',
                'ifup':'echo "UP $*" >> "$LOG"; exit "${UP_ERROR:-0}"',
                'ifdown':'echo "DOWN $*" >> "$LOG"; exit "${DOWN_ERROR:-0}"',
                'allowed':'echo "RADIO $*" >> "$LOG"; exit "${RADIO_ERROR:-0}"'}
            for name,body in scripts.items():
                p=binpath/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
            action=TOP/'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/action'
            (root/'action').write_text(action.read_text());(root/'action').chmod(0o755)
            compat=(OVERLAY/'usr/sbin/u30pro-celld').read_text().replace('/usr/libexec/unisoc-modem/action',str(root/'action'))
            self.assertNotIn('mobile-data',compat)
            (root/'celld').write_text(compat);(root/'celld').chmod(0o755)
            panel=(OVERLAY/'usr/sbin/u30pro-panel-control').read_text().replace('/usr/sbin/u30pro-celld',str(root/'celld'))
            (root/'panel').write_text(panel);(root/'panel').chmod(0o755)
            env=dict(os.environ,PATH=str(binpath)+':'+os.environ['PATH'],LOG=str(root/'log'),MU300_RADIO_ALLOWED=str(binpath/'allowed'))
            for command,events in (([str(root/'celld'),'--ctl','reconnect'],['DOWN customwan','RADIO 1','UP customwan']),
                                   ([str(root/'celld'),'--ctl','disconnect'],['DOWN customwan']),
                                   ([str(root/'panel'),'reconnect'],['DOWN customwan','RADIO 1','UP customwan'])):
                (root/'log').write_text('')
                result=subprocess.run(command,env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual((root/'log').read_text().splitlines(),events)
            for failure,events in (('DOWN_ERROR',['DOWN customwan']),('UP_ERROR',['DOWN customwan','RADIO 1','UP customwan']),('RADIO_ERROR',['DOWN customwan','RADIO 1'])):
                (root/'log').write_text('')
                result=subprocess.run([str(root/'celld'),'--ctl','reconnect'],env={**env,failure:'1'},capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0,failure)
                self.assertIn('"ok":0',result.stdout)
                self.assertEqual((root/'log').read_text().splitlines(),events)

    def test_sdio_defaults_and_explicit_policy_do_not_touch_storage(self):
        text=(OVERLAY/'usr/sbin/u30pro-wifi-sdio').read_text()
        self.assertNotIn('/mnt/data',text)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('sdio_mode','sdio_max_hz'):(root/name).touch()
            for mode,hz,want in (('', '',True),('1','100000000',True),('9','208000000',False),('2','bad',False)):
                script='uci() { case "$*" in *sdio_mode) echo "'+mode+'";; *) echo "'+hz+'";; esac; }\n'+text
                result=subprocess.run(['sh','-c',script],env=dict(os.environ,U30PRO_SDIO_PARAMS=str(root)),capture_output=True,text=True)
                self.assertEqual(result.returncode==0,want,result.stderr)
                if want:
                    self.assertEqual((root/'sdio_mode').read_text().strip(),mode or '2')
                    self.assertEqual((root/'sdio_max_hz').read_text().strip(),hz or '208000000')
        factory=(OVERLAY/'etc/init.d/u30pro-factory').read_text()
        self.assertIn('START=08\n',factory, 'rc.common must generate S08, not late-sorted S8')
        self.assertLess(factory.index('u30pro-wifi-sdio'),factory.index('u30pro-factory-wifi'))

    def test_vendor_uses_prepared_nodes_verified_disk_and_android_slot(self):
        for slot in ('a', 'b'):
            with self.subTest(slot=slot), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root/'slot').write_text(slot+'\n')
                for name in ('nr_runtimenv1','nr_runtimenv2'):
                    (root/name).touch()
                (root/'storage').write_text('mu300_storage_disk() { echo mmcblk0; }\nmu300_storage_parent() { echo mmcblk0; }\n')
                text = (OVERLAY/'etc/init.d/mu300-vendor').read_text()
                self.assertIn('START=13', text)
                text = text.replace('/run/mu300/android-slot',str(root/'slot'))
                text = text.replace('/usr/sbin/u30pro-nodes --populate-nodes','populate')
                text = text.replace('/opt/mu300/lib/storage.sh',str(root/'storage'))
                text = text.replace('/dev/block/by-name/',str(root)+'/').replace('[ -b ', '[ -f ')
                script='''populate() { echo POPULATE; }
uci() { echo off; }
mkdir() { :; }
procd_open_instance() { echo "INSTANCE $*"; }
procd_close_instance() { :; }
procd_set_param() { echo "PARAM $*"; }
logger() { echo "ERROR $*"; }
'''+text+'\nstart_service\n'
                result = subprocess.run(['sh','-c',script],capture_output=True,text=True,check=True)
                self.assertLess(result.stdout.index('POPULATE'),result.stdout.index('INSTANCE nv'))
                self.assertIn('--writeback=off',result.stdout)
                self.assertIn('--slot _'+slot,result.stdout)
                self.assertEqual(result.stdout.count('INSTANCE '),3)
                (root/'storage').write_text('mu300_storage_disk() { echo mmcblk0; }\nmu300_storage_parent() { echo sda; }\n')
                result = subprocess.run(['sh','-c',script],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('INSTANCE ',result.stdout)

    def test_regdb_reload_precedes_wifi_and_failure_does_not_start_ap(self):
        for failure in (False,True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                log=Path(folder)/'log'
                text=(OVERLAY/'etc/init.d/mu300-hw').read_text()
                text=text.replace('/usr/sbin/u30pro-nodes --populate-nodes','populate')
                text=text.replace('/usr/sbin/u30pro-usb-boot','usb_boot')
                text=text.replace('/opt/mu300/bin/zram-start','zram')
                text=text.replace('/opt/mu300/bin/openwrt-wifi-config','configure_wifi').replace('/sbin/wifi up','start_wifi')
                script='''populate() { :; }
zram() { :; }
usb_boot() { :; }
iw() { echo "REG $*" >> "$LOG"; return '''+('1' if failure else '0')+'''; }
configure_wifi() { echo CONFIG >> "$LOG"; }
start_wifi() { echo AP >> "$LOG"; }
logger() { echo ERROR >> "$LOG"; }
'''+text+'\nboot\nwait\n'
                subprocess.run(['sh','-c',script],env=dict(os.environ,LOG=str(log)),check=True)
                self.assertEqual(log.read_text().splitlines(),['REG reg reload','ERROR'] if failure else ['REG reg reload','CONFIG','AP'])

    def test_screen_toggle_uses_selected_ap_runtime_not_fixed_section(self):
        for available,up,want in ((1,1,'off'),(1,0,'on'),(0,0,None)):
            with self.subTest(available=available,up=up),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);helper=root/'hotspot'
                helper.write_text('#!/bin/sh\nif [ "$1" = fields ]; then printf "available\\n'+str(available)+'\\nup\\n'+str(up)+'\\nsection\\nrenamed_ap\\n"; else echo "AP $1"; fi\n')
                helper.chmod(0o755)
                text=(OVERLAY/'usr/sbin/u30pro-panel-control').read_text().replace('/usr/libexec/unisoc-modem/hotspot',str(helper))
                self.assertNotIn('wireless.default_radio0',text)
                result=subprocess.run(['sh','-c','set -- wifi-toggle\n'+text],capture_output=True,text=True)
                if want:
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(result.stdout.strip(),'AP '+want)
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertNotIn('AP ',result.stdout)

    def test_factory_import_never_mounts_writable_or_changes_factory_files(self):
        for bad_mac in (False,True):
            with self.subTest(bad_mac=bad_mac),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'run').mkdir();(root/'source').mkdir();(root/'prodnv').touch()
                (root/'source/wifimac.txt').write_text('ff:ff:ff:ff:ff:ff\n' if bad_mac else '02:11:22:33:44:55\n')
                (root/'source/aic_userconfig_8800d80x2.txt').write_text('factory-tuning\n')
                before={p.name:p.read_bytes() for p in (root/'source').iterdir()}
                (root/'storage').write_text('mu300_storage_disk() { echo mmcblk0; }\nmu300_storage_part() { echo prodnv; }\nmu300_storage_parent() { echo mmcblk0; }\n')
                text=(OVERLAY/'usr/sbin/u30pro-factory-wifi').read_text()
                text=text.replace('/opt/mu300/lib/storage.sh',str(root/'storage')).replace('/usr/sbin/u30pro-nodes --populate-nodes','populate')
                text=text.replace('dev=/dev/$node','dev='+str(root/'prodnv')).replace('[ -b ', '[ -f ')
                text=text.replace('/run/u30pro-factory-wifi.',str(root/'run/factory.')).replace('/lib/firmware',str(root/'firmware'))
                script='''populate() { :; }
blkid() { echo ext4; }
logger() { :; }
mount() {
    [ "$*" = "-t ext4 -o ro,noload,nodev,nosuid,noexec '''+str(root/'prodnv')+''' $6" ] || exit 41
    cp "'''+str(root)+'''/source/"* "$6/"
}
umount() { rm "$1/wifimac.txt" "$1/aic_userconfig_8800d80x2.txt"; }
'''+text
                result=subprocess.run(['sh','-c',script],capture_output=True,text=True)
                self.assertEqual(result.returncode!=0,bad_mac,result.stderr)
                self.assertEqual({p.name:p.read_bytes() for p in (root/'source').iterdir()},before)
                self.assertEqual(list((root/'run').iterdir()),[])
                if not bad_mac:self.assertEqual((root/'firmware/wifimac.txt').read_bytes(),before['wifimac.txt'])

    def test_usb_boot_honors_common_modes_scopes_and_rejects_corruption(self):
        for mode,scope in [('ncm','once'),('ecm','permanent'),('rndis','once'),('garbage','once')]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'policy').write_text('mode='+mode+'\nscope='+scope+'\n')
                text=(OVERLAY/'usr/sbin/u30pro-usb-boot').read_text()
                text=text.replace('/etc/unisoc-modem/usb-boot.conf',str(root/'policy'))
                text=text.replace('/run/u30pro-usb-profile',str(root/'profile')).replace('/run/unisoc-usb-net-applied',str(root/'applied'))
                text=text.replace('/usr/sbin/u30pro-usb','gadget').replace('/usr/libexec/unisoc-modem/device-usb','consume_policy')
                script='''logger() { :; }
gadget() { echo "GADGET $*"; }
consume_policy() { echo "POLICY $*"; }
'''+text
                result=subprocess.run(['sh','-c',script],capture_output=True,text=True)
                self.assertEqual(result.returncode==0,mode!='garbage',result.stderr)
                self.assertEqual((root/'applied').exists(),mode!='garbage')
                if mode!='garbage':self.assertEqual(result.stdout.splitlines(),['GADGET start '+mode,'POLICY boot'])


if __name__=='__main__':unittest.main()
