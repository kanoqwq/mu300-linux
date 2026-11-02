"""Check the real service's emitted procd argv and boot-time owner selection."""
import json
import os
import shlex
from helpers import ShellTest, TOP

class ModemdService(ShellTest):
    def setUp(self):
        super().setUp()
        self.lib = self.tmp / 'service.sh'
        text = (TOP/'rootfs/overlay/opt/mu300/lib/modemd-service.sh').read_text()
        for a,b in [('/opt/mu300/bin/mu300-device','mu300-device'),
                    ('/usr/bin/ucode','ucode'),('/opt/mu300',str(self.tmp/'opt')),
                    ('/etc/mu300',str(self.tmp/'etc')),('/run/',str(self.tmp/'run')+'/')]:
            text = text.replace(a,b)
        self.lib.write_text(text)
        self.stub('mu300-device', 'printf "%s" "$DEVICE"')
        self.stub('uci', 'printf "%s" "$SLOTS"')
        self.stub('ucode', 'exit "${RUNTIME_FAIL:-0}"')
        for name in ['mu300-atd-io','mu300-modemd']:
            f = self.tmp/'opt/bin'/name
            f.parent.mkdir(parents=True,exist_ok=True)
            f.write_text('#!/bin/sh\n')
            f.chmod(0o755)
        self.code = '. '+shlex.quote(str(self.lib))+\
            "\nmu300_sms_pool() { printf '%s' '/custom sms/pool'; }\n"+\
            'procd_open_instance() { echo "instance:$1"; }\n'+\
            'procd_close_instance() { :; }\n'+\
            'procd_set_param() { printf "set:"; printf "<%s>" "$@"; echo; }\n'+\
            'procd_append_param() { printf "append:"; printf "<%s>" "$@"; echo; }\n'

    def test_layout_preserves_all_native_channels_and_single_urc_owner(self):
        for shell in self.each_shell():
            for device,slots,channels in [('mu300','1',[1,2,3,4,5,6,7]),('v50','2',[1,2,4,5,6,7]),('u30air','2',[1,2,4,5,6,7])]:
                with self.subTest(device=device):
                    p=self.sh(shell,self.code+'modemd_single_layout',DEVICE=device,SLOTS=slots)
                    self.assertEqual(p.returncode,0,p.stderr)
                    specs=[line for line in p.stdout.splitlines() if line.startswith('append:')]
                    self.assertEqual(len(specs),len(channels),p.stdout)
                    for line,n in zip(specs,channels):
                        self.assertIn(f'dev=/dev/stty_nr{n},',line)
                        self.assertIn('reopen='+('0' if device=='u30air' else '1'),line)
                    self.assertEqual(sum('urc=0,' in line for line in specs),1)
                    self.assertEqual(sum('urc=3,' in line for line in specs),int(slots=='2'))
                    self.assertIn('<MU300_SMS_POOL=/custom sms/pool>',p.stdout)
                    before_daemon=p.stdout.split('instance:modemd')[0]
                    self.assertEqual('set:<respawn>' in before_daemon,device!='u30air')

    def test_dual_sim_never_respawns_or_reopens_descriptor_owner(self):
        for shell in self.each_shell():
            p=self.sh(shell,self.code+'modemd_sim2_layout',DEVICE='u30air',SLOTS='2')
            self.assertEqual(p.returncode,0,p.stderr)
            specs=[line for line in p.stdout.splitlines() if line.startswith('append:')]
            self.assertEqual(len(specs),4)
            self.assertTrue(all('reopen=0,ready=sbuf:sbuf_5_006' in s for s in specs))
            self.assertNotIn('set:<respawn>',p.stdout.split('instance:modemd')[0])
            self.assertIn('set:<respawn>',p.stdout.split('instance:modemd')[1])

    def test_u30pro_has_one_owner_for_nr2_and_nr0(self):
        for shell in self.each_shell():
            p=self.sh(shell,self.code+'modemd_single_layout',DEVICE='u30pro',SLOTS='1')
            self.assertEqual(p.returncode,0,p.stderr)
            specs=[line for line in p.stdout.splitlines() if line.startswith('append:')]
            self.assertEqual(len(specs),2,p.stdout)
            self.assertIn('dev=/dev/stty_nr2,',specs[0])
            self.assertIn('dev=/dev/stty_nr0,',specs[1])
            self.assertTrue(all('urc=,live=' in row and 'reopen=0,ready=' in row for row in specs))
            self.assertNotIn('set:<respawn>',p.stdout.split('instance:modemd')[0])

    def test_missing_native_runtime_fails_instead_of_selecting_an_old_owner(self):
        for shell in self.each_shell():
            p=self.sh(shell,self.code+'modemd_require',RUNTIME_FAIL=1)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('runtime is incomplete',p.stderr)

    def test_u30pro_dual_banks_preserve_physical_ownership(self):
        for shell in self.each_shell():
            p=self.sh(shell,self.code+'modemd_single_layout',DEVICE='u30pro',SLOTS='2')
            self.assertEqual(p.returncode,0,p.stderr)
            specs=[line for line in p.stdout.splitlines() if line.startswith('append:')]
            self.assertEqual(len(specs),4,p.stdout)
            for row,n in zip(specs,[2,0,5,3]):self.assertIn(f'dev=/dev/stty_nr{n},',row)
            self.assertIn('dir='+str(self.tmp/'run/mu300-at4'),specs[2])
            self.assertIn('live='+str(self.tmp/'run/mu300-at5/urc/stty_nr3.log'),specs[2])

    def test_existing_physical_owner_requires_reboot(self):
        owner=self.tmp/'run/mu300-at/owner/pid'
        owner.parent.mkdir(parents=True)
        owner.write_text(str(os.getpid())+'\n')
        for shell in self.each_shell():
            p=self.sh(shell,self.code+'modemd_require')
            self.assertNotEqual(p.returncode,0)
            self.assertIn('already running',p.stderr)
