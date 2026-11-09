"""Real shell/PDU importer regression; never sends AT or accesses a real pool."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
SMS=TOP/'rootfs/overlay/opt/mu300/bin/mu300-sms'
PDU=TOP/'rootfs/overlay/opt/mu300/lib/sms-pdu.awk'
BASH='C:/Program Files/Git/bin/bash.exe' if os.name=='nt' else '/bin/bash'


def pdu(text, ref=42, part=1, total=2, wide=False, stamp='62017021436500'):
    header=('060804'+f'{ref:04X}' if wide else '050003'+f'{ref:02X}')+f'{total:02X}{part:02X}'
    payload=header+text.encode('utf-16-be').hex().upper()
    return '0044048121430008'+stamp+f'{len(payload)//2:02X}'+payload


class Multipart(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.pool=self.root/'pool'
        self.env=dict(os.environ,MU300_SMS_POOL=self.pool.as_posix(),MU300_SMS_PDU_AWK=PDU.as_posix(),LC_ALL='C')

    def command(self,*args):
        return subprocess.run([BASH,'-c','sync() { :; }; script="$1"; shift; . "$script"','isolated-test',SMS.as_posix(),*args],env=self.env,capture_output=True,text=True,encoding='utf-8',timeout=15)

    def receive(self,text,ref=42,part=1,total=2,wide=False,stamp='62017021436500',source='direct'):
        raw=self.root/'input.pdu';raw.write_text(pdu(text,ref,part,total,wide,stamp)+'\n',newline='\n')
        result=self.command('import-pdu',raw.as_posix(),source)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)

    def messages(self):
        return sorted((self.pool/'msg').glob('[0-9]*'))

    def body(self,file):
        return file.read_text(encoding='utf-8').split('\n\n',1)[1].rstrip('\n')

    def test_reverse_order_complete_only(self):
        self.receive('后半',part=2)
        self.assertEqual(self.messages(),[])
        self.assertEqual((self.pool/'next_id').read_text().strip(),'0')
        self.receive('前半',part=1)
        self.assertEqual([self.body(f) for f in self.messages()],['前半后半'])
        self.assertEqual(list((self.pool/'multipart').iterdir()),[])

    def test_interleaved_three_messages_same_sender_same_second(self):
        for text,ref,part in [('B2',2,2),('A3',1,3),('C1',3,1),('A1',1,1),('B1',2,1),('C3',3,3),('A2',1,2),('B3',2,3),('C2',3,2)]:
            self.receive(text,ref,part,3)
        self.assertEqual(sorted(self.body(f) for f in self.messages()),['A1A2A3','B1B2B3','C1C2C3'])

    def test_duplicate_pending_and_completed(self):
        self.receive('B',part=2);self.receive('B',part=2)
        self.receive('A',part=1);self.receive('A',part=1);self.receive('B',part=2)
        self.assertEqual(len(self.messages()),1)
        self.assertEqual(self.body(self.messages()[0]),'AB')

    def test_reference_widths_do_not_collide(self):
        self.receive('eight1');self.receive('sixteen2',part=2,wide=True)
        self.receive('eight2',part=2);self.receive('sixteen1',wide=True)
        self.assertEqual(sorted(self.body(f) for f in self.messages()),['eight1eight2','sixteen1sixteen2'])

    def test_timestamps_can_differ_and_cross_minute(self):
        self.receive('B',part=2,stamp='62017022530000')
        self.receive('A',part=1,stamp='62017022439500')
        self.assertEqual([self.body(f) for f in self.messages()],['AB'])

    def test_reference_reused_on_another_day(self):
        self.receive('old1',stamp='62017021436500')
        self.receive('new2',part=2,stamp='62018021436500')
        self.receive('old2',part=2,stamp='62017021436500')
        self.receive('new1',stamp='62018021436500')
        self.assertEqual(sorted(self.body(f) for f in self.messages()),['new1new2','old1old2'])

    def test_missing_part_never_visible_or_forwardable(self):
        self.receive('one',part=1,total=3);self.receive('three',part=3,total=3)
        self.assertEqual(self.messages(),[])
        self.assertEqual((self.pool/'next_id').read_text().strip(),'0')

    def test_archive_and_direct_parts_do_not_forward_history(self):
        self.receive('A',source='sim');self.receive('B',part=2)
        self.assertIn('source: sim\n',self.messages()[0].read_text())

    def test_stored_live_multipart_and_mixed_history(self):
        # Arrival order must not promote any historical part to a live event.
        for first, second, expected in [('stored-live','stored-live','stored-live'),
                                        ('direct','stored-live','stored-live'),
                                        ('sim','stored-live','sim'),
                                        ('stored-live','sim','sim')]:
            ref=50+len(self.messages())
            self.receive('head',ref=ref,source=first)
            self.receive('tail',ref=ref,part=2,source=second)
            self.assertIn('source: '+expected+'\n',self.messages()[-1].read_text())

    def test_deleted_message_retry_does_not_resurrect(self):
        self.receive('A');self.receive('B',part=2)
        result=self.command('delete','000001');self.assertEqual(result.returncode,0,result.stderr)
        self.receive('B',part=2);self.receive('A')
        self.assertEqual(self.messages(),[])

    def test_legacy_sync_no_timestamp_heuristic(self):
        source=SMS.read_text()
        block=source[source.index('cmd_sync() {'):source.index('sms_timestamp() {')]
        self.assertNotIn('key = $3 "|" $4',block)

    def test_ambiguous_reference_collision_keeps_original(self):
        self.receive('first A');self.receive('first B')
        raw=self.root/'input.pdu';raw.write_text(pdu('ambiguous tail',part=2)+'\n',newline='\n')
        result=self.command('import-pdu',raw.as_posix())
        self.assertNotEqual(result.returncode,0)
        self.assertIn('safely assemble',result.stderr)
        self.assertTrue(raw.is_file())
        self.assertEqual(self.messages(),[])
        self.assertFalse((self.pool/'.lock').exists())

    def test_restart_resumes_missing_part(self):
        # Every receive is a new process; persistent state alone must suffice.
        self.receive('tail',part=2)
        self.assertIn('pdu:',next((self.pool/'multipart').glob('*/2')).read_text())
        self.receive('head',part=1)
        self.assertEqual([self.body(f) for f in self.messages()],['headtail'])

    def test_single_part_unchanged(self):
        raw=self.root/'input.pdu';raw.write_text('00040481214300006201702143650005E8329BFD06\n',newline='\n')
        for _ in range(2):
            result=self.command('import-pdu',raw.as_posix())
            self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual([self.body(f) for f in self.messages()],['hello'])


if __name__=='__main__':unittest.main()
