"""SIM capacity is reported without AT on page refresh; SIM deletes must be honest."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP


SMS = TOP / 'rootfs/overlay/opt/mu300/bin/mu300-sms'
RPC = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash'
UI = TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/sms.js'
I18N = TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js'
SPOOL = TOP / 'rootfs/overlay/opt/mu300/bin/mu300-cmt-spool'
PDU_AWK = TOP / 'rootfs/overlay/opt/mu300/lib/sms-pdu.awk'


class SmsCapacity(unittest.TestCase):
    def test_direct_pdu_is_persisted_and_imported_idempotently(self):
        # SMS-DELIVER from 1234, GSM-7 "hello"; direct delivery must survive
        # duplicate network attempts without producing duplicate inbox rows.
        pdu = '00040481214300006201702143650005E8329BFD06'
        with tempfile.TemporaryDirectory() as tmp:
            pool = Path(tmp) / 'pool'
            env = dict(os.environ, MU300_SMS_POOL=str(pool), MU300_SMS_PDU_AWK=str(PDU_AWK))
            spooled = subprocess.run([str(SPOOL)], input='+CMT: ,20\n' + pdu + '\n',
                                     env=env, capture_output=True, text=True)
            self.assertEqual(spooled.returncode, 0, spooled.stderr)
            ready = list((pool / 'incoming').glob('*.ready'))
            self.assertEqual(len(ready), 1)
            self.assertEqual(ready[0].read_text().strip(), pdu)
            self.assertEqual(ready[0].stat().st_mode & 0o077, 0)
            for _ in range(2):
                result = subprocess.run([str(SMS), 'import-pdu', str(ready[0])], env=env,
                                        capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stderr)
            messages = list((pool / 'msg').glob('[0-9]*'))
            self.assertEqual(len(messages), 1)
            headers, body = messages[0].read_text().split('\n\n', 1)
            self.assertEqual(body, 'hello\n')
            self.assertIn('source: direct\n', headers + '\n')
            self.assertNotIn('multipart_ref:', headers)
            self.assertIn('multipart_total: 1\n', headers + '\n')
            self.assertIn('multipart_part: 1\n', headers + '\n')


    def test_import_preserves_body_with_empty_ref_and_multipart_ref(self):
        # UCS-2 avoids relying on a mock decoder and includes Chinese plus
        # punctuation that must not be interpreted by the shell.
        text = '中文 $(id) \\"quoted"'
        payload = text.encode('utf-16-be').hex().upper()
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            for multipart in (False, True):
                ud = ('0500032A0201' if multipart else '') + payload
                pdu = ('0044' if multipart else '0004') + '04812143000862017021436500'
                pdu += f'{len(ud) // 2:02X}' + ud
                raw = temp / 'input.pdu'
                raw.write_text(pdu + '\n')
                pool = temp / ('multi' if multipart else 'single')
                env = dict(os.environ, MU300_SMS_POOL=str(pool), MU300_SMS_PDU_AWK=str(PDU_AWK),
                           LC_ALL='C.UTF-8')
                result = subprocess.run([str(SMS), 'import-pdu', str(raw)], env=env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                if multipart:
                    self.assertEqual(list((pool / 'msg').glob('[0-9]*')), [])
                    record = next((pool / 'multipart').glob('*/1'))
                else:
                    record = pool / 'msg/000001'
                headers, body = record.read_text().split('\n\n', 1)
                self.assertEqual(body, text + '\n')
                if not multipart:
                    self.assertNotIn('multipart_ref:', headers)
                    self.assertIn('multipart_part: 1\n', headers + '\n')

    def test_unsupported_pdu_keeps_recoverable_local_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            raw = temp / 'unknown.pdu'
            raw.write_text('00\n')
            pool = temp / 'pool'
            env = dict(os.environ, MU300_SMS_POOL=str(pool), MU300_SMS_PDU_AWK=str(PDU_AWK))
            result = subprocess.run([str(SMS), 'import-pdu', str(raw)], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            messages = list((pool / 'msg').glob('[0-9]*'))
            self.assertEqual(len(messages), 1)
            self.assertIn('Undecoded SMS PDU: 00', messages[0].read_text())

    def test_direct_mode_never_switches_to_text_during_manual_sync_or_send(self):
        sms = SMS.read_text(encoding='utf-8')
        daemon = (TOP / 'rootfs/overlay/opt/mu300/bin/mu300-smsd').read_text(encoding='utf-8')
        self.assertIn('if [ -n "${MU300_SIM_SLOT:-}" ] || [ -f "$DIRECT_FLAG" ]; then\n        cmd_archive_sim', sms)
        send = sms.split('cmd_send() {', 1)[1].split('cmd_delete() {', 1)[0]
        self.assertNotIn("at 'AT+CMGF=1'", send)
        fallback = daemon.split('sync_fallback() {', 1)[1].split('signature() {', 1)[0]
        self.assertNotIn('AT+CMGF=1', fallback)
        self.assertNotIn('AT+CNMI=', fallback)

    def test_sim_original_is_deleted_only_after_verified_local_archive(self):
        pdu = '00040481214300006201702143650005E8329BFD06'
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            modem = temp / 'mu300-at'
            modem.write_text('''#!/bin/sh
case "$3" in
  'AT+CPMS="SM"') printf '+CPMS: 1,50,0,0,0,0\\nOK\\n' ;;
  'AT+CMGF=0') printf 'OK\\n' ;;
  'AT+CMGL=4') printf '+CMGL: 3,0,,20\\n''' + pdu + '''\\nOK\\n' ;;
  'AT+CMGR=3')
    if [ "$SMS_MOCK_BAD_READ" = 1 ]; then printf '+CMGR: 0,,20\\n00\\nOK\\n';
    else printf '+CMGR: 0,,20\\n''' + pdu + '''\\nOK\\n'; fi ;;
  'AT+CMGD=3')
    printf '%s\\n' "$3" >> "$SMS_MOCK_LOG"
    if [ "$SMS_MOCK_FAIL_DELETE" = 1 ]; then printf '+CMS ERROR: 500\\n'; else printf 'OK\\n'; fi ;;
  *) printf 'ERROR\\n' ;;
esac
''', encoding='utf-8')
            modem.chmod(0o755)
            pool = temp / 'pool'
            env = dict(os.environ, PATH=f'{tmp}:{os.environ["PATH"]}',
                       MU300_SMS_POOL=str(pool), MU300_SMS_PDU_AWK=str(PDU_AWK),
                       SMS_MOCK_LOG=str(temp / 'delete.log'),
                       SMS_MOCK_BAD_READ='1', SMS_MOCK_FAIL_DELETE='0')
            def archive():
                return subprocess.run([str(SMS), 'archive-sim'], env=env,
                                      capture_output=True, text=True, encoding='utf-8')

            self.assertNotEqual(archive().returncode, 0)
            self.assertFalse((temp / 'delete.log').exists(), 'mismatched slot must not be deleted')
            env['SMS_MOCK_BAD_READ'] = '0'
            env['SMS_MOCK_FAIL_DELETE'] = '1'
            self.assertNotEqual(archive().returncode, 0)
            self.assertEqual(len(list((pool / 'archive').glob('*.pdu'))), 1)
            self.assertEqual(len(list((pool / 'msg').glob('[0-9]*'))), 1)
            self.assertIn('source: sim\n', (pool / 'msg/000001').read_text())
            env['SMS_MOCK_FAIL_DELETE'] = '0'
            self.assertEqual(archive().returncode, 0)
            self.assertEqual((temp / 'delete.log').read_text().splitlines(),
                             ['AT+CMGD=3', 'AT+CMGD=3'])

    def test_incomplete_sim_listing_never_deletes_any_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            modem = temp / 'mu300-at'
            modem.write_text('''#!/bin/sh
case "$3" in
  'AT+CPMS="SM"') printf '+CPMS: 2,50,0,0,0,0\\nOK\\n' ;;
  'AT+CMGF=0') printf 'OK\\n' ;;
  'AT+CMGL=4') printf '+CMGL: 3,0,,20\\n00040481214300006201702143650005E8329BFD06\\nOK\\n' ;;
  'AT+CMGD='*) printf '%s\\n' "$3" >> "$SMS_MOCK_LOG"; printf 'OK\\n' ;;
  *) printf 'ERROR\\n' ;;
esac
''', encoding='utf-8')
            modem.chmod(0o755)
            log = temp / 'delete.log'
            env = dict(os.environ, PATH=f'{tmp}:{os.environ["PATH"]}',
                       MU300_SMS_POOL=str(temp / 'pool'), SMS_MOCK_LOG=str(log))
            result = subprocess.run([str(SMS), 'archive-sim'], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(log.exists())

    def test_manual_sim_delete_retires_legacy_direct_marker_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            pool = temp / 'pool'
            raw = temp / 'direct.pdu'
            raw.write_text('00040481214300006201702143650005E8329BFD06\n')
            flag = temp / 'direct.flag'
            flag.touch()
            modem = temp / 'mu300-at'
            modem.write_text('''#!/bin/sh
printf '%s\\n' "$3" >> "$SMS_MOCK_LOG"
case "$3" in
  'AT+CPMS="SM"') printf '+CPMS: 0,50,0,0,0,0\\nOK\\n' ;;
  *) printf 'OK\\n' ;;
esac
''', encoding='utf-8')
            modem.chmod(0o755)
            log = temp / 'at.log'
            env = dict(os.environ, PATH=f'{tmp}:{os.environ["PATH"]}',
                       MU300_SMS_POOL=str(pool), MU300_SMS_PDU_AWK=str(PDU_AWK),
                       MU300_SMS_DIRECT_FLAG=str(flag), SMS_MOCK_LOG=str(log))
            saved = subprocess.run([str(SMS), 'import-pdu', str(raw)], env=env,
                                   capture_output=True, text=True)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            result = subprocess.run([str(SMS), 'delete', '1', '--sim'], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((pool / 'msg/000001').exists())
            self.assertFalse(flag.exists())
            commands = log.read_text().splitlines()
            self.assertEqual(commands[0], 'AT+CNMI=2,1,0,0,0')
            self.assertEqual(commands[-2:], ['AT+CMGF=0', 'AT+CNMI=2,1,0,0,0'])

    def test_sim_delete_failure_is_not_downgraded_to_local_delete(self):
        rpc = RPC.read_text(encoding='utf-8')
        self.assertIn('if [ "$sim" = 1 ]; then\n        out=$("$SMS" delete "$id" --sim 2>&1)', rpc)
        self.assertIn('printf \'{"ok":0,"error":"%s"}', rpc)

    def test_capacity_warning_is_localized(self):
        ui = UI.read_text(encoding='utf-8')
        catalog = I18N.read_text(encoding='utf-8')
        self.assertIn('st.sim_used >= st.sim_total', ui)
        self.assertIn("'SIM 存储已满；自动归档尚未释放空间，请检查短信服务。':", catalog)

    def test_full_sim_keeps_local_copy_when_modem_refuses_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            phone = '17674544422'
            hex_phone = phone.encode().hex().upper()
            modem = temp / 'mu300-at'
            modem.write_text('''#!/bin/sh
case "$3" in
  'AT+CPMS="SM"') printf '+CPMS: 50,50,0,0,0,0\\nOK\\n' ;;
  'AT+CMGF=1') printf 'OK\\n' ;;
  'AT+CMGL="ALL"') printf '+CMGL: 3,"REC READ","''' + hex_phone + '''",,"26/10/07,12:00:00+08"\\nhello\\nOK\\n' ;;
  'AT+CMGD=3')
    printf '%s\\n' "$3" >> "$SMS_MOCK_LOG"
    if [ "$SMS_MOCK_FAIL_DELETE" = 1 ]; then printf '+CMS ERROR: 500\\n'; else printf 'OK\\n'; fi ;;
  *) printf 'ERROR\\n' ;;
esac
''', encoding='utf-8')
            modem.chmod(0o755)
            env = dict(os.environ, PATH=f'{tmp}:{os.environ["PATH"]}',
                       MU300_SMS_POOL=str(temp / 'pool'),
                       MU300_SMS_STORE_CACHE=str(temp / 'store-cache'),
                       SMS_MOCK_LOG=str(temp / 'delete.log'),
                       SMS_MOCK_FAIL_DELETE='1')
            def sms(*args):
                return subprocess.run([str(SMS), *args], env=env, capture_output=True,
                                      text=True, encoding='utf-8')

            synced = sms('sync')
            self.assertEqual(synced.returncode, 0, synced.stderr)
            self.assertEqual((temp / 'pool/storage').read_text().split()[:3], ['SM', '50', '50'])
            msg = temp / 'pool/msg/000001'
            self.assertTrue(msg.exists())
            self.assertIn('from: ' + phone, msg.read_text())
            self.assertIn('source: sim\n', msg.read_text())

            refused = sms('delete', '1', '--sim')
            self.assertNotEqual(refused.returncode, 0)
            self.assertTrue(msg.exists(), 'a failed SIM delete must retain the local copy')
            self.assertFalse((temp / 'pool/deleted').exists())

            env['SMS_MOCK_FAIL_DELETE'] = '0'
            deleted = sms('delete', '1', '--sim')
            self.assertEqual(deleted.returncode, 0, deleted.stderr)
            self.assertFalse(msg.exists())
            self.assertTrue((temp / 'pool/deleted').exists())
            self.assertEqual((temp / 'delete.log').read_text().splitlines(),
                             ['AT+CMGD=3', 'AT+CMGD=3'])


if __name__ == '__main__':
    unittest.main()
