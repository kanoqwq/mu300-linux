"""SMS event filtering and real ubus waits; no modem/real network access."""
import json
import os
import signal
import subprocess
import time
import unittest
from pathlib import Path
from helpers import ShellTest, TOP, ucode
from test_modemd import Base, has_modules, wait_for, UBUS, UBUSD

LIB=TOP/'rootfs/overlay/opt/mu300/lib/sms-events.sh'

class SmsEventFilter(ShellTest):
    def test_only_own_physical_bank_triggers_spool_and_sim_archive(self):
        for shell in self.each_shell():
            for slot in [0,1]:
                for nr in range(8):
                    for line,expected in [('+CMT (PDU spooled)','spool'),('+CMTI: "SM",1','sync'),('+CEREG: 1',''),('+CMT: ,23','')]:
                        event=json.dumps({'mu300.modem.urc':{'channel':f'stty_nr{nr}','line':line}},separators=(',',':'))
                        own=(nr in [0,1,2,6,7]) if slot==0 else nr in [3,4,5]
                        r=self.sh(shell,'. '+str(LIB)+'\nsms_event_decode "$EVENT"; printf "%s" "$sms_event"',MU300_SIM_SLOT=slot,EVENT=event)
                        self.assertEqual(r.returncode,0,r.stderr)
                        self.assertEqual(r.stdout,('sync' if nr==0 else '') if line.startswith('+CMTI:') else expected if own else '',(slot,nr,line))

    def test_native_sim_state_and_object_removal(self):
        for shell in self.each_shell():
            for event,want in [({'mu300.sim-state':{'slot':1}},'sim'),({'mu300.sim-state':{'slot':0}},''),({'ubus.object.remove':{'path':'mu300.modem'}},'gone'),({'ubus.object.remove':{'path':'other'}},'')]:
                r=self.sh(shell,'. '+str(LIB)+'\nsms_event_decode "$EVENT"; printf "%s" "$sms_event"',MU300_SIM_SLOT=1,EVENT=json.dumps(event,separators=(',',':')))
                self.assertEqual(r.stdout,want,r.stderr)

@unittest.skipUnless(ucode() and has_modules(ucode()) and os.access(UBUS,os.X_OK) and os.access(UBUSD,os.X_OK),'real ucode/ubus required')
class SmsEventBus(Base):
    def setUp(self):
        super().setUp()
        self.ubus_sock=self.tmp/'ubus.sock'
        self.busd=subprocess.Popen([UBUSD,'-s',str(self.ubus_sock)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertTrue(wait_for(self.ubus_sock.exists))
        self.start()
        self.ready()
        self.assertTrue(wait_for(lambda: subprocess.run([UBUS,'-s',str(self.ubus_sock),'list','mu300.modem'],capture_output=True).returncode==0))
        self.stubdir=self.tmp/'stubs';self.stubdir.mkdir()
        stub=self.stubdir/'ubus'
        stub.write_text('#!/bin/sh\nexec '+UBUS+' -s "$MU300_UBUS_SOCKET" "$@"\n');stub.chmod(0o755)
        self.result=self.tmp/'result';self.started=self.tmp/'started'

    def tearDown(self):
        super().tearDown()
        self.busd.terminate();self.busd.wait(5)

    def receiver(self,slot=0,seconds=60):
        lock=self.tmp/'sms-lock';lock.mkdir()
        code='. '+str(LIB)+\
            '\ntrap sms_events_stop EXIT\n'+\
            'sms_events_start || exit 9\n'+\
            'printf "%s" "$sms_event_pid" > "$STARTED"\n'+\
            'sms_clock; printf \'%s\' \"$SMS_NOW\" > \"$RESULT.start\"\nsms_events_wait "$((SMS_NOW + SMS_TEST_WAIT))" || exit 1\n'+\
            'printf "%s" "$SMS_NOW" > "$RESULT.end"\n'+\
            'printf "%s|%s" "$sms_event" "$sms_events" > "$RESULT"\n'
        env=dict(self.env,PATH=str(self.stubdir)+':'+os.environ['PATH'],MU300_UBUS_SOCKET=str(self.ubus_sock),MU300_SIM_SLOT=str(slot),LOCK=str(lock),SMS_TEST_WAIT=str(seconds),STARTED=str(self.started),RESULT=str(self.result))
        p=subprocess.Popen(['/bin/sh','-c',code],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.procs.append(p)
        self.assertTrue(wait_for(self.started.exists))
        time.sleep(.15)  # listener subscription follows opening its FIFO
        return p

    def send(self,name,data):
        subprocess.run([UBUS,'-s',str(self.ubus_sock),'send',name,json.dumps(data)],check=True,capture_output=True)

    def finish(self,p,want):
        self.assertEqual(p.wait(5),0,p.stderr.read())
        self.assertEqual(self.result.read_text(),want)

    def daemon(self, initial_spool=False, fail_import=False, sync_interval=300, poll=60, events=True):
        lock=self.tmp/'receiver-lock'
        atdir=self.tmp/'receiver-at';atdir.mkdir();os.mkfifo(atdir/'cmd')
        pool=self.tmp/'sms-pool';(pool/'incoming').mkdir(parents=True)
        self.frame=pool/'incoming'/'.cmt.fixture.ready'
        if initial_spool: self.frame.write_text('fixture PDU, never printed')
        self.import_failure=self.tmp/'fail-import'
        if fail_import: self.import_failure.touch()
        self.at_log=self.tmp/'at-log';self.sms_log=self.tmp/'sms-log'
        at=self.stubdir/'mu300-at'
        at.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$AT_LOG"\ncase "$*" in *CPIN*) printf "+CPIN: READY\\nOK\\n";; *) echo OK;; esac\n');at.chmod(0o755)
        sms=self.stubdir/'sms'
        sms.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SMS_LOG"\n[ "$1" != import-pdu ] || [ ! -e "$IMPORT_FAILURE" ]\n');sms.chmod(0o755)
        env=dict(self.env,PATH=str(self.stubdir)+':'+os.environ['PATH'],MU300_UBUS_SOCKET=str(self.ubus_sock),
                 MU300_SMS_LOCK=str(lock),MU300_AT_DIR=str(atdir),MU300_SMS_POOL=str(pool),MU300_SIM_SLOT='0',
                 MU300_SMS_BIN=str(sms),MU300_SMS_DIRECT_FLAG=str(self.tmp/'direct'),MU300_SMS_POLL=str(poll),MU300_SMS_SYNC_INTERVAL=str(sync_interval),MU300_SMS_TRANSPORT='ubus' if events else 'urc-log',
                 MU300_REGISTRATION_LIB=str(self.tmp/'not-present'),AT_LOG=str(self.at_log),SMS_LOG=str(self.sms_log),IMPORT_FAILURE=str(self.import_failure))
        script=TOP/'rootfs/overlay/opt/mu300/bin/mu300-smsd'
        p=subprocess.Popen(['/bin/sh',str(script)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.procs.append(p)
        self.assertTrue(wait_for(lambda: (self.tmp/'direct').exists()))
        time.sleep(.2)
        return p

    def test_ubuntu_urc_log_transport_remains_functional(self):
        self.daemon(poll=1,events=False)
        self.frame.write_text('fixture PDU')
        self.assertTrue(wait_for(lambda: not self.frame.exists(),3))
        self.assertIn('CNMA=1',self.at_log.read_text())

    def test_existing_spool_is_imported_and_acknowledged_before_first_wait(self):
        p=self.daemon(initial_spool=True)
        self.assertTrue(wait_for(lambda: not self.frame.exists(),3))
        self.assertIn('import-pdu',self.sms_log.read_text())
        self.assertIn('AT+SPACTCARD=0;+CNMA=1',self.at_log.read_text())
        self.assertNotIn('fixture PDU',self.sms_log.read_text()+self.at_log.read_text())
        p.terminate();self.assertEqual(p.wait(5),0)
        self.assertFalse((self.tmp/'receiver-lock').exists())

    def test_failed_import_keeps_frame_and_sends_no_ack_then_retries(self):
        self.daemon(initial_spool=True,fail_import=True)
        self.assertTrue(wait_for(lambda: 'import-pdu' in self.sms_log.read_text(),3))
        self.assertTrue(self.frame.exists())
        self.assertNotIn('CNMA',self.at_log.read_text())
        self.import_failure.unlink()
        self.send('mu300.modem.urc',{'channel':'stty_nr1','line':'+CMT (PDU spooled)'})
        self.assertTrue(wait_for(lambda: not self.frame.exists(),3))
        self.assertIn('CNMA=1',self.at_log.read_text())

    def test_safety_sync_uses_real_time_even_without_events(self):
        self.daemon(sync_interval=2)
        self.assertTrue(wait_for(lambda: self.sms_log.read_text().count('archive-sim')>=2,4))

    def test_sms_event_wakes_without_waiting_for_poll(self):
        p=self.receiver(slot=1)
        self.send('mu300.modem.urc',{'channel':'stty_nr4','line':'+CMT (PDU spooled)'})
        self.finish(p,'spool|1')

    def test_other_slot_and_radio_events_do_not_move_deadline(self):
        p=self.receiver(slot=0,seconds=2)
        for i in range(4):
            self.send('mu300.modem.urc',{'channel':'stty_nr3','line':'+CMT (PDU spooled)'})
            self.send('mu300.modem.urc',{'channel':'stty_nr0','line':'+CEREG: 1'})
            time.sleep(.1)
        self.finish(p,'|1')
        elapsed=int(Path(str(self.result)+'.end').read_text())-int(Path(str(self.result)+'.start').read_text())
        self.assertIn(elapsed,(2,3))

    def test_listener_death_is_reported_without_polling(self):
        p=self.receiver()
        os.kill(int(self.started.read_text()),signal.SIGTERM)
        self.assertNotEqual(p.wait(5),0,p.stderr.read())

    def test_modemd_removal_is_an_error(self):
        p=self.receiver()
        self.procs[0].terminate()
        self.assertNotEqual(p.wait(5),0,p.stderr.read())

    def test_ready_cache_event_is_scoped_to_receiver(self):
        p=self.receiver(slot=1)
        self.send('mu300.sim-state',{'slot':0})
        time.sleep(.1);self.assertIsNone(p.poll())
        self.send('mu300.sim-state',{'slot':1})
        self.finish(p,'sim|1')
