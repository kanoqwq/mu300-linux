"""Boot promotion on private files; real boot helpers and optional target ucode."""
import argparse
from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import zlib

TOP=next(p for p in Path(__file__).resolve().parents if (p/'rootfs/overlay').is_dir())
REPOS=[TOP]
UCODE=shlex.split(os.environ.get('MU300_TEST_UCODE','')) or ([shutil.which('ucode')] if shutil.which('ucode') else [])

def block(slot,linux):
    b=bytearray(32);b[:4]=('_'+slot).encode()+b'\0\0';b[4:10]=b'BCAB\x01\x02'
    b[12],b[14]=((0x2f,0x9e) if slot=='a' else (0x9e,0x2f)) if linux else ((0x9f,0x1e) if slot=='a' else (0x1e,0x9f))
    struct.pack_into('<I',b,28,zlib.crc32(b[:28]));return bytes(b)

@contextmanager
def fixture(repo,slot='b'):
    with tempfile.TemporaryDirectory(prefix='boot-auto-') as d:
        root=Path(d);e5=(repo/'rootfs/overlay/opt/e5/e5-next-boot').exists()
        run=root/('run/e5linux' if e5 else 'run/mu300');run.mkdir(parents=True)
        config=root/('etc/e5linux/default-boot' if e5 else 'etc/mu300/default-boot')
        config.parent.mkdir(parents=True);config.write_text('android\n')
        misc=root/'misc';misc.write_bytes(b'P'*4096)
        (run/'misc-dev').write_text(str(misc));(run/'linux-slot').write_text(slot)
        for name,data in {'misc-bc-linux-trial.bin':block(slot,True),
            'misc-bc-android.bin':block('b' if slot=='a' else 'a',False),
            'misc-bc-slot-b-trial.bin':block(slot,True),
            'misc-bc-slot-a.bin':block('b' if slot=='a' else 'a',False)}.items():
            (run/name).write_bytes(data)
        (root/'disk/.mu300').mkdir(parents=True);(root/'cmdline').write_text('normal boot\n')
        (root/'proc').mkdir();(root/'proc/uptime').write_text('59.8 0\n');(root/'tmp').mkdir()
        source=repo/('rootfs/overlay/opt/e5/e5-next-boot' if e5 else 'rootfs/overlay/opt/mu300/bin/mu300-next-boot')
        text=source.read_text().replace('/run',str(root)+'/run')
        for prefix in ['/etc/e5linux/','/etc/mu300/']:
            text=text.replace(prefix,str(root)+prefix)
        text=text.replace('/mnt/mu300-disk',str(root/'disk')).replace('/proc/cmdline',str(root/'cmdline'))
        helper=root/'boot helper';helper.write_text(text);helper.chmod(0o755)
        bins=root/'bin';bins.mkdir();sync=bins/'sync';sync.write_text('#!/bin/sh\nexit 0\n');sync.chmod(0o755)
        env=dict(os.environ,PATH=str(bins)+':'+os.environ['PATH'])
        yield dict(root=root,run=run,config=config,misc=misc,helper=helper,env=env,e5=e5,bins=bins)

def invoke(f,*args):
    return subprocess.run(['bash',str(f['helper']),*args],env=f['env'],capture_output=True,text=True,timeout=10)

class Transactions(unittest.TestCase):
    def test_both_slot_roles_missing_default_and_permanent_idempotence(self):
        for repo in REPOS:
            for slot in ['a','b']:
                for missing in [False,True]:
                    with self.subTest(repo=repo.name,slot=slot,missing=missing),fixture(repo,slot) as f:
                        if missing:f['config'].unlink()
                        before=f['misc'].read_bytes();p=invoke(f,'--auto-default')
                        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                        self.assertEqual(f['config'].read_text(),'linux\n')
                        after=f['misc'].read_bytes()
                        self.assertEqual(before[:2048],after[:2048]);self.assertEqual(before[2080:],after[2080:])
                        self.assertEqual(zlib.crc32(after[2048:2076]),struct.unpack_from('<I',after,2076)[0])
                        self.assertEqual(invoke(f,'--auto-default').returncode,0)
                        self.assertEqual(f['misc'].read_bytes(),after)

    def test_explicit_choice_wins_before_and_after_auto(self):
        for repo in REPOS:
            with self.subTest(repo=repo.name),fixture(repo) as f:
                self.assertEqual(invoke(f,'android').returncode,0)
                before=f['misc'].read_bytes()
                self.assertEqual(invoke(f,'--auto-default').returncode,0)
                self.assertEqual(f['config'].read_text(),'android\n');self.assertEqual(f['misc'].read_bytes(),before)
                (f['root']/'run/openwrt-boot-manual').unlink()
                self.assertEqual(invoke(f,'--auto-default').returncode,0)
                self.assertEqual(invoke(f,'android').returncode,0)
                self.assertEqual(f['config'].read_text(),'android\n')
                # A rearm finishing afterwards cannot undo the user's Android choice.
                self.assertEqual(invoke(f,'--rearm').returncode,0)
                self.assertEqual(f['config'].read_text(),'android\n')

    def test_contention_failed_persistence_and_retry(self):
        for repo in REPOS:
            with self.subTest(repo=repo.name),fixture(repo) as f:
                before=f['misc'].read_bytes()
                with (f['root']/'run/openwrt-default-boot.lock').open('a') as fd:
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    self.assertNotEqual(invoke(f,'--auto-default').returncode,0)
                self.assertEqual(f['misc'].read_bytes(),before)
                mv=f['bins']/'mv'
                mv.write_text('#!/bin/sh\ncase "$2" in *.backup.*) ;; *) exit 1;; esac\nexec /bin/mv "$@"\n');mv.chmod(0o755)
                self.assertNotEqual(invoke(f,'--auto-default').returncode,0)
                self.assertEqual(f['misc'].read_bytes(),before);self.assertEqual(f['config'].read_text(),'android\n')
                mv.unlink();self.assertEqual(invoke(f,'--auto-default').returncode,0)

    def test_unknown_intent_and_e5_ram_trial_are_not_promoted(self):
        for repo in REPOS:
            with self.subTest(repo=repo.name),fixture(repo) as f:
                f['config'].write_text('broken\n');before=f['misc'].read_bytes()
                self.assertEqual(invoke(f,'--auto-default').returncode,0)
                self.assertEqual(f['config'].read_text(),'broken\n');self.assertEqual(f['misc'].read_bytes(),before)
                if f['e5']:
                    f['config'].write_text('android\n');(f['root']/'cmdline').write_text('e5.openwrt=trial.ext4\n')
                    self.assertEqual(invoke(f,'--auto-default').returncode,0)
                    self.assertEqual(f['config'].read_text(),'android\n');self.assertEqual(f['misc'].read_bytes(),before)

    def test_existing_startup_entry_registers_one_shot_and_keeps_trial_cleanup(self):
        for repo in REPOS:
            with self.subTest(repo=repo.name),fixture(repo) as f:
                source=repo/('openwrt/overlay/etc/init.d/e5-boot-ok' if f['e5'] else 'openwrt/overlay/etc/init.d/mu300-post')
                native=repo/'devices/u30pro/overlay/etc/init.d/mu300-post'
                if native.exists():source=native
                self.assertIn('START=99',source.read_text());self.assertIn('USE_PROCD=1',source.read_text())
                alias=f['root']/'helper';alias.symlink_to(f['helper'])
                text=source.read_text().replace('/opt/e5/e5-next-boot',str(alias)).replace('/opt/mu300/bin/mu300-next-boot',str(alias))
                for path in ['/run','/etc','/sys','/mnt/e5-data','/opt/mu300']:
                    text=text.replace(path,str(f['root'])+path)
                role=f['root']/'sys/class/usb_role/25100000.dwc3-role-switch/role'
                role.parent.mkdir(parents=True);role.write_text('host\n')
                led=f['root']/'opt/mu300/bin/led-status';led.parent.mkdir(parents=True)
                led.write_text('#!/bin/sh\necho offline\n');led.chmod(0o755)
                logger=f['bins']/'logger';logger.write_text('#!/bin/sh\nexit 0\n');logger.chmod(0o755)
                for name in ['sd-trial','part-trial']:
                    p=f['root']/'etc/e5'/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
                record=f['root']/'instances'
                stub='''procd_open_instance() { printf 'open %s\\n' "$*" >> "$RECORD"; }
procd_set_param() { printf 'param %s\\n' "$*" >> "$RECORD"; }
procd_close_instance() { echo close >> "$RECORD"; }
'''
                p=subprocess.run(['sh','-c',stub+text+'\nstart_service\n'],env=dict(f['env'],RECORD=str(record)),capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                rows=record.read_text();self.assertEqual(rows.count('open auto-default'),1)
                auto=rows.split('open auto-default\n',1)[1].split('close',1)[0]
                self.assertIn('/usr/bin/ucode /usr/libexec/unisoc-boot-default',auto)
                self.assertNotIn('respawn',auto)
                if f['e5']:
                    self.assertFalse((f['root']/'etc/e5/sd-trial').exists())
                    self.assertFalse((f['root']/'etc/e5/part-trial').exists())

@unittest.skipUnless(UCODE,'target ucode required')
class Deadline(unittest.TestCase):
    def test_exact_normal_boot_deadline_and_charging_handoff(self):
        for repo in REPOS:
            program="""import {delay_ms} from 'unisoc.boot-default';
for(let row in [[0,false,60000],[20,false,40000],[59.5,false,500],[60,false,0],[90,false,0],
 [900,true,60000],[null,false,60000],[-1,false,60000]])
 if(delay_ms(row[0],row[1])!=row[2])die(sprintf('%J',row));print('ok');"""
            p=subprocess.run(UCODE+['-L',str(repo/'openwrt/overlay/usr/share/ucode/*.uc'),'-e',program],capture_output=True,text=True)
            self.assertEqual((p.returncode,p.stdout),(0,'ok'),p.stderr)

    def worker(self,repo,f):
        text=(repo/'openwrt/overlay/usr/libexec/unisoc-boot-default').read_text()
        for path in ['/tmp/.failsafe','/run/openwrt-boot-manual','/proc/uptime','/run/mu300/charging-boot.json']:
            text=text.replace("'"+path+"'",json.dumps(str(f['root'])+path))
        script=f['root']/'worker.uc';script.write_text(text)
        return subprocess.Popen(UCODE+['-L',str(repo/'openwrt/overlay/usr/share/ucode/*.uc'),str(script),
            str(f['helper']),str(f['config'])],env=f['env'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

    def test_real_timer_process_and_transaction(self):
        for repo in REPOS:
            with self.subTest(repo=repo.name),fixture(repo) as f:
                began=time.monotonic();p=self.worker(repo,f);out,err=p.communicate(timeout=12)
                self.assertEqual(p.returncode,0,out+err)
                self.assertGreaterEqual(time.monotonic()-began,.15)
                self.assertEqual(f['config'].read_text(),'linux\n')
                self.assertEqual(out.count('normal boot reached one minute'),1)

    def test_full_minute_then_permanent_worker_exits(self):
        with ExitStack() as stack:
            running=[]
            for repo in REPOS:
                f=stack.enter_context(fixture(repo))
                (f['root']/'proc/uptime').write_text('0 0\n')
                began=time.monotonic();p=self.worker(repo,f)
                running.append((repo,f,began,p))
            for repo,f,began,p in running:
                out,err=p.communicate(timeout=75)
                self.assertGreaterEqual(time.monotonic()-began,59)
                self.assertEqual(p.returncode,0,out+err)
                self.assertEqual(f['config'].read_text(),'linux\n')
                self.assertEqual(out.count('normal boot reached one minute'),1)
                out,err=self.worker(repo,f).communicate(timeout=5)
                self.assertEqual(out,'',err)

    def test_timer_cancellation_charging_failsafe_and_manual_choice(self):
        for repo in REPOS:
            for case in ['poweroff','charging','failsafe','manual','permanent']:
                with self.subTest(repo=repo.name,case=case),fixture(repo) as f:
                    before=f['misc'].read_bytes()
                    if case=='poweroff':(f['root']/'proc/uptime').write_text('0 0\n')
                    if case=='charging':
                        (f['root']/'proc/uptime').write_text('900 0\n')
                        path=f['root']/'run/mu300/charging-boot.json';path.parent.mkdir(exist_ok=True);path.write_text('{}')
                    if case=='failsafe':(f['root']/'tmp/.failsafe').touch()
                    if case=='permanent':f['config'].write_text('linux\n')
                    if case=='manual':(f['root']/'proc/uptime').write_text('59 0\n')
                    p=self.worker(repo,f)
                    if case in ['poweroff','charging']:
                        time.sleep(.2);self.assertIsNone(p.poll());p.terminate()
                    if case=='manual':
                        time.sleep(.1);self.assertEqual(invoke(f,'android').returncode,0);before=f['misc'].read_bytes()
                    out,err=p.communicate(timeout=10)
                    self.assertEqual(f['config'].read_text(),'linux\n' if case=='permanent' else 'android\n',out+err)
                    self.assertEqual(f['misc'].read_bytes(),before)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--repo',action='append',type=Path)
    args,rest=parser.parse_known_args();REPOS=args.repo or [TOP]
    unittest.main(argv=[__file__,*rest],verbosity=2)
