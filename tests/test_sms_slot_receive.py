"""Execute both native receivers/importers with one NR0 hint and scoped stores."""
import json
import os
from pathlib import Path
from helpers import ShellTest, BIN


class ScopedStoredReceive(ShellTest):
    def test_unassigned_direct_retries_preserve_one_raw_copy_without_inbox_or_ack(self):
        for shell in self.each_shell():
            pool=self.tmp/('unassigned-'+shell[0].split('/')[-1])
            pdu='00040481214300006201702143650005E8329BFD06'
            # Class-0 direct delivery also lacks a recipient slot. Preserve
            # evidence without routing it to an arbitrary SIM or sending ACK.
            flash=pdu.replace('21430000','214300F0')
            for pdu in (pdu,flash):
                for _ in range(3):
                    result=self.script(shell,BIN/'mu300-cmt-spool',
                        MU300_SMS_POOL=pool,MU300_CMT_UNASSIGNED='1',
                        stdin='+CMT: ,20\n'+pdu+'\n')
                    self.assertEqual(result.returncode,0,result.stderr)
            frames=list((pool/'incoming').glob('*.ready'))
            self.assertEqual(len(frames),2)
            self.assertEqual({f.read_text() for f in frames},{flash+'\n','00040481214300006201702143650005E8329BFD06\n'})
            self.assertFalse((pool/'msg').exists())

    def test_one_nr0_hint_cannot_assign_either_slot_by_port(self):
        # Same sender/PDU/index on either slot: none can be origin evidence.
        pdu='00040481214300006201702143650005E8329BFD06'
        for shell in self.each_shell():
            for target in (0,1,2):
                import tempfile
                with tempfile.TemporaryDirectory(dir=self.tmp) as tmp:
                    base=Path(tmp);pool=base/'pool';at=base/'at';at.mkdir();os.mkfifo(at/'cmd')
                    events=base/'events.sh'
                    events.write_text('''
. "$REAL_EVENTS"
sms_events_start() { touch "$TEST_BASE/listening-$MU300_SIM_SLOT"; }
sms_events_stop() { :; }
WAIT=0
sms_events_wait() {
    WAIT=$((WAIT+1))
    [ "$WAIT" = 1 ] || exit 0
    touch "$TEST_BASE/arrived"
    sms_event_decode '{"mu300.modem.urc":{"channel":"stty_nr0","line":"+CMTI: \\"SM\\",7"}}'
}
''')
                    modem=base/'mu300-at'
                    modem.write_text('''#!/usr/bin/env python3
import os,sys,pathlib,json
base=pathlib.Path(os.environ['TEST_BASE']);slot=int(os.environ['MU300_SIM_SLOT'])
cmd=sys.argv[-1];prefix='AT+SPACTCARD='+str(slot)+';'
assert cmd.startswith(prefix), 'unscoped native operation'
cmd=cmd[len(prefix):]
if cmd.startswith('+CNMI='):assert (base/('listening-'+str(slot))).exists(), 'receiver subscribed too late'
with (base/'commands').open('a') as f:f.write(json.dumps([slot,cmd])+'\\n')
active=(base/'arrived').exists() and not (base/('deleted-'+str(slot))).exists() and (slot==int(os.environ['TARGET_SLOT']) or int(os.environ['TARGET_SLOT'])==2)
pdu=os.environ['TEST_PDU']
if cmd=='+CSMS=1' and int(os.environ['TARGET_SLOT'])==1:
    print('ERROR');raise SystemExit(0)
elif cmd=='+CPIN?':print('+CPIN: READY')
elif cmd=='+CPMS="SM"':print('+CPMS: %d,50,0,50,0,50'%int(active))
elif cmd=='+CMGL=4' and active:print('+CMGL: 7,0,,20\\n'+pdu)
elif cmd=='+CMGR=7' and active:print('+CMGR: 0,,20\\n'+pdu)
elif cmd=='+CMGD=7':
    assert active
    pool=pathlib.Path(os.environ['MU300_SMS_POOL'])
    assert any((pool/'archive').glob('*.pdu')) and any((pool/'msg').glob('*'))
    (base/('deleted-'+str(slot))).touch()
elif cmd.startswith('+CNMA') or ',2,2,1,1' in cmd:raise AssertionError('unscoped direct receive was enabled/ACKed')
print('OK')
''');modem.chmod(0o755)
                    # Leave a legacy direct PDU in the wrong inbox's incoming
                    # directory. It must survive without being imported/ACKed.
                    legacy=pool/('sim1' if target==0 else '')/'incoming/.cmt.legacy.ready'
                    legacy.parent.mkdir(parents=True);legacy.write_text(pdu+'\n')
                    for slot in (0,1):
                        scoped=pool/('sim1' if slot else '')
                        result=self.script(shell,BIN/'mu300-smsd',
                            PATH=str(base)+':'+os.environ['PATH'],TEST_BASE=base,TARGET_SLOT=target,TEST_PDU=pdu,
                            MU300_SMS_POOL=scoped,MU300_AT_DIR=at,MU300_SIM_SLOT=slot,
                            MU300_SMS_LOCK=base/('receiver-'+str(slot)),MU300_SMS_OP_LOCK=base/('operation-'+str(slot)),
                            MU300_SMS_BIN=BIN/'mu300-sms',MU300_SMS_PDU_AWK=BIN.parent/'lib/sms-pdu.awk',
                            MU300_SMS_DIRECT_FLAG=base/('direct-'+str(slot)),MU300_SMS_EVENTS_LIB=events,
                            REAL_EVENTS=BIN.parent/'lib/sms-events.sh',MU300_REGISTRATION_LIB=base/'absent',
                            MU300_SMS_TRANSPORT='ubus')
                        self.assertEqual(result.returncode,0,result.stderr)
                    for slot in (0,1):
                        inbox=pool/('sim1' if slot else '')/'msg'
                        messages=list(inbox.glob('*'));receives=target==slot or target==2
                        self.assertEqual(len(messages),int(receives))
                        self.assertEqual((base/('deleted-'+str(slot))).exists(),receives)
                        if receives:
                            self.assertIn('source: '+('stored-live' if slot==0 else 'sim'),messages[0].read_text())
                    self.assertTrue(legacy.exists())
                    queries=[json.loads(line) for line in (base/'commands').read_text().splitlines()]
                    for slot in (0,1):
                        setup=[cmd for owner,cmd in queries if owner==slot]
                        self.assertIn('+CPMS="SM","SM","SM"',setup)
                        self.assertLess(setup.index('+CPMS="SM","SM","SM"'),setup.index('+CMGL=4') if '+CMGL=4' in setup else len(setup))
                    self.assertTrue(all(slot==target or target==2 for slot,cmd in queries if cmd.startswith(('+CMGR=','+CMGD='))))
                    # The two pools retain identical SMS content independently.
                    target=0 if target==2 else target
                    expected=pool/('sim1' if target else '')/'msg'
                    before=len(queries)
                    result=self.script(shell,BIN/'mu300-sms','sync',
                        PATH=str(base)+':'+os.environ['PATH'],TEST_BASE=base,TARGET_SLOT=target,TEST_PDU=pdu,
                        MU300_SMS_POOL=expected.parent,MU300_AT_DIR=at,MU300_SIM_SLOT=target,
                        MU300_SMS_OP_LOCK=base/'manual-operation',MU300_SMS_DIRECT_FLAG=base/'no-marker')
                    self.assertEqual(result.returncode,0,result.stderr)
                    manual=[json.loads(line)[1] for line in (base/'commands').read_text().splitlines()[before:]]
                    self.assertIn('+CPMS="SM"',manual)
                    self.assertNotIn('+CMGF=1',manual)
