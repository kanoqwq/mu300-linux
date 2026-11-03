"""NFC RPC contract with isolated owners; never touch real GPIO/I2C/NV."""
try:
    import fcntl
except ImportError:
    fcntl=None
import json
import unittest
from pathlib import Path
from helpers import ShellTest, TOP

APP=TOP/'openwrt/luci-app-mu300'
ADAPTER=APP/'root/usr/libexec/unisoc-modem/nfc'


class DashboardNFC(ShellTest):
    def setUp(self):
        super().setUp()
        self.state=self.tmp/'enabled';self.state.write_text('1\n')
        self.stub('device', 'echo "${NFC_MODEL-u30air}"')
        self.stub('jsonfilter', '''exec python3 -c 'import json,sys
try: print(json.load(sys.stdin)[sys.argv[-1][2:]])
except Exception: sys.exit(1)' "$@"''')
        for name in ('native','tag'):
            self.stub(name, '''printf '%s %s\n' "${0##*/}" "$*" >> "$STUBLOG/calls"
case $1 in
 supported) [ "${NFC_FAIL:-}" != supported ];;
 state) [ "${NFC_FAIL:-}" != read ] || exit 1; cat "$STUBLOG/enabled";;
 on|off)
  [ "${NFC_FAIL:-}" != apply ] || { echo 'private diagnostics' >&2; exit 1; }
  [ "${NFC_FAIL:-}" != mismatch ] || exit 0
  if [ "$1" = on ]; then echo 1; else echo 0; fi > "$STUBLOG/enabled";;
 *) exit 1;;
esac''')

    def call(self,shell,*args,payload=None,**env):
        r=self.script(shell,ADAPTER,*args,stdin=json.dumps(payload or {}),
                      UNISOC_NFC_DEVICE_COMMAND=self.stubs/'device',
                      UNISOC_NFC_TAG_COMMAND=self.stubs/'tag',
                      UNISOC_NFC_NATIVE_COMMAND=self.stubs/'native',
                      UNISOC_NFC_LOCK=self.tmp/'rpc.lock',**env)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn('private',r.stdout+r.stderr)
        return json.loads(r.stdout)

    def test_model_owners_readback_and_saved_switch(self):
        for shell in self.each_shell():
            for model,owner in [('u30pro','native'),('u30air','tag'),('v50','tag')]:
                with self.subTest(model=model):
                    (self.tmp/'calls').unlink(missing_ok=True)
                    self.state.write_text('1\n')
                    self.assertEqual(self.call(shell,'get',NFC_MODEL=model)['enabled'],1)
                    self.assertEqual(self.call(shell,'set',payload={'enabled':'0'},NFC_MODEL=model)['enabled'],0)
                    self.assertEqual(self.call(shell,'get',NFC_MODEL=model)['enabled'],0)
                    self.assertEqual(self.call(shell,'set',payload={'enabled':'1'},NFC_MODEL=model)['enabled'],1)
                    lines=(self.tmp/'calls').read_text().splitlines()
                    self.assertTrue(all(line.startswith(owner+' ') for line in lines),lines)
                    self.assertIn(owner+' off',lines);self.assertIn(owner+' on',lines)

    def test_unrelated_models_never_call_hardware(self):
        for shell in self.each_shell():
            for model in ('f50','mu300','e5','unknown',''):
                self.assertEqual(self.call(shell,'get',NFC_MODEL=model),{'ok':1,'supported':0})
                self.assertEqual(self.call(shell,'set',payload={'enabled':'1'},NFC_MODEL=model)['error'],'nfc_unsupported')
            self.assertFalse((self.tmp/'calls').exists())

    def test_validation_rejects_injection_and_invalid_json_values(self):
        for shell in self.each_shell():
            for value in ('',2,-1,'on','true','1; echo bad',True,[],None):
                self.assertEqual(self.call(shell,'set',payload={'enabled':value})['error'],'nfc_invalid')
            self.assertFalse((self.tmp/'calls').exists())

    def test_failures_never_report_false_success_or_select_another_owner(self):
        for shell in self.each_shell():
            self.state.write_text('1\n')
            for failure,error in [('supported','nfc_unavailable'),('apply','nfc_apply_failed'),
                                  ('read','nfc_read_failed'),('mismatch','nfc_apply_failed')]:
                self.state.write_text('1\n')
                self.assertEqual(self.call(shell,'set',payload={'enabled':'0'},NFC_MODEL='u30pro',NFC_FAIL=failure)['error'],error)
            self.assertNotIn('tag ',(self.tmp/'calls').read_text())

    @unittest.skipUnless(fcntl, 'POSIX advisory locks')
    def test_lock_contention_is_bounded_and_old_lock_file_is_not_stale(self):
        lock=self.tmp/'rpc.lock'
        with lock.open('w') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.call(['sh'],'get')['error'],'nfc_busy')
        # A prior process's lock file remains; only the kernel lock matters.
        self.assertTrue(self.call(['sh'],'get')['ok'])

    def test_rpc_schema_acl_and_install_permissions(self):
        shim=APP/'root/usr/libexec/rpcd/unisoc-nfc'
        r=self.script(['sh'],shim,'list')
        self.assertEqual(json.loads(r.stdout),{'get':{},'set':{'enabled':'String'}})
        acl=json.loads((APP/'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        self.assertEqual(acl['read']['ubus']['unisoc-nfc'],['get'])
        self.assertEqual(acl['write']['ubus']['unisoc-nfc'],['set'])
        self.assertIn('chmod 0755 $(1)/usr/libexec/rpcd/unisoc-nfc',(APP/'Makefile').read_text())
        self.assertIn('chmod 0755 $R/usr/libexec/rpcd/unisoc-nfc',(TOP/'openwrt/build-rootfs.sh').read_text())
