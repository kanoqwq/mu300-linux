"""APN list/UI/package contract and isolated dialer configuration tests."""
import json
import re
from helpers import TOP, ShellTest

APP=TOP/'openwrt/luci-app-mu300'
LIB=APP/'root/usr/libexec/unisoc-modem'

class APN(ShellTest):
    def test_alphanumeric_identity_adapter_preserves_key(self):
        import json
        source=(LIB/'apn-identity').read_text().replace('/run/unisoc-apn-sim',str(self.tmp/'sim'))
        path=self.tmp/'identity';path.write_text(source)
        adapter=self.tmp/'adapter';adapter.mkdir()
        at=adapter/'at'
        at.write_text('#!/bin/sh\nprintf \'%s\\r\\n\' "${TEST_REPLY}"\n')
        at.chmod(0o755)
        for shell in self.each_shell():
            for value in ('898604E112345678901F','898604Z11234567890af'):
                for reply in ('+CCID: '+value, '+CCID: "'+value+'"'):
                    r=self.script(shell,path,'1',MU300_DASH_BIN=adapter,TEST_REPLY=reply)
                    self.assertEqual(r.returncode,0,r.stderr)
                    self.assertEqual(json.loads(r.stdout)['iccid'],value)
                    self.assertEqual(json.loads((self.tmp/'sim1.json').read_text())['iccid'],value)
            for value in ('short','898604/112345678901F','x'*23,'卡'*20):
                r=self.script(shell,path,'1',MU300_DASH_BIN=adapter,TEST_REPLY='+CCID: '+value)
                self.assertNotEqual(r.returncode,0)
                self.assertEqual(r.stdout,'')

    def test_dialer_reads_letter_identity_without_losing_characters(self):
        source=(TOP/'rootfs/overlay/opt/mu300/bin/mobile-data').read_text()
        function=source[source.index('resolve_apn_selection() {'):source.index('\nconfigure_initial_apn() {')]
        helper=self.tmp/'apn'
        helper.write_text('''#!/bin/sh
if [ "$1" = needs-identity ]; then echo 1; exit; fi
cat > "$STUBLOG/resolved-identity"
echo '{"ok":1,"source":"sim","settings":{"apn":"letter.apn","pdptype":"IP","auth":"none","username":"","password":"","roaming":"IP","attach":"0"}}'
''')
        helper.chmod(0o755)
        filter_path=self.stubs/'jsonfilter'
        filter_path.write_text('''#!/usr/bin/env python3
import json,sys
value=json.load(sys.stdin)
for key in sys.argv[2].split('.')[1:]: value=value[key]
print(value)
''')
        filter_path.chmod(0o755)
        path=self.tmp/'dialer.sh'
        path.write_text('set -eu\n'+function.replace('/usr/libexec/unisoc-modem/apn',str(helper)).replace('/run/unisoc-apn-sim',str(self.tmp/'sim'))+'''
at() { printf '%s\\n' '+CCID: "898604Z11234567890af"'; }
resolve_apn_selection
[ "$RESOLVED_APN:$MU300_PDP_TYPE" = 'letter.apn:IP' ]
''')
        r=self.script(['bash'],path,MU300_SIM_SLOT='1')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads((self.tmp/'resolved-identity').read_text())['iccid'],'898604Z11234567890af')

    def test_hot_global_settings_without_identity_queries(self):
        source=(TOP/'rootfs/overlay/opt/mu300/bin/mobile-data').read_text()
        function=source[source.index('resolve_apn_selection() {'):source.index('\nconfigure_initial_apn() {')]
        helper=self.tmp/'apn'
        helper.write_text('#!/bin/sh\nif [ "$1" = needs-identity ]; then echo 0; else cat <<\'JSON\'\n'+json.dumps({'ok':1,'source':'network','settings':{'apn':'carrier.test','pdptype':'IPV4V6','auth':'chap','username':'fixture','password':'secret','roaming':'IP','attach':'0'}})+'\nJSON\nfi\n')
        helper.chmod(0o755)
        filter_path=self.stubs/'jsonfilter'
        filter_path.write_text('''#!/usr/bin/env python3
import json, sys
value=json.load(sys.stdin)
for key in sys.argv[2].split('.')[1:]: value=value[key]
print(value)
''')
        filter_path.chmod(0o755)
        path=self.tmp/'resolve.sh'
        path.write_text('set -eu\n'+function.replace('/usr/libexec/unisoc-modem/apn',str(helper))+'''
at() { echo UNEXPECTED-AT >&2; return 1; }
resolve_apn_selection
[ "$RESOLVED_APN:$MU300_PDP_TYPE:$MU300_AUTH:$MU300_USERNAME:$MU300_PASSWORD" = 'carrier.test:IPV4V6:chap:fixture:secret' ]
''')
        r=self.script(['bash'],path)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn('secret',r.stdout+r.stderr)
        self.assertNotIn('UNEXPECTED-AT',r.stderr)

    def test_authentication_and_attach_order(self):
        source=(TOP/'rootfs/overlay/opt/mu300/bin/mobile-data').read_text()
        functions=source[source.index('configure_pdp_context() {'):source.index('\nup_locked() {')]
        path=self.tmp/'order.sh'
        path.write_text('set -u\n'+functions+'''\nat() { printf '%s\\n' "$1" >> "$STUBLOG/at-calls"; printf '%s\\n' "${REPLY:-OK}"; }
configure_initial_apn internet && configure_pdp_context internet
''')
        r=self.script(['bash'],path,CID='1',MU300_NETIFD='1',MU300_AUTH='papchap',MU300_USERNAME='fixture',MU300_PASSWORD='fixture-secret',MU300_PDP_TYPE='IPV4V6',MU300_ROAMING_PDP_TYPE='IP',MU300_INITIAL_ATTACH='1')
        self.assertEqual(r.returncode,0,r.stderr)
        calls=(self.tmp/'at-calls').read_text().splitlines()
        self.assertEqual(calls,['AT+SPCGAPNCFG=1,"internet","IPV4V6","IP",0,"fixture","fixture-secret",3','AT+CGDCONT=1,"IPV4V6","internet"','AT+CGPCO=0,"fixture","fixture-secret",1,3'])
        self.assertNotIn('fixture-secret',r.stdout+r.stderr)
        (self.tmp/'at-calls').unlink()
        r=self.script(['bash'],path,CID='1',MU300_NETIFD='1',REPLY='ERROR',MU300_INITIAL_ATTACH='1')
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(len((self.tmp/'at-calls').read_text().splitlines()),1)

    def test_pdp_context(self):
        source=(TOP/'rootfs/overlay/opt/mu300/bin/mobile-data').read_text()
        function=source[source.index('configure_pdp_context() {'):source.index('\nup_locked() {')]
        harness=function+'''\nat() { printf '%s\\n' "$1" >&2; printf '%s\\n' "${REPLY:-OK}"; }
configure_pdp_context "$@"
'''
        path=self.tmp/'pdp.sh';path.write_text(harness)
        r=self.script(['bash'],path,'',MU300_NETIFD='1',MU300_APN='stale.apn',CID='1',MU300_PDP_TYPE='IPV4V6')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('AT+CGDCONT=1,"IPV4V6",""',r.stderr)
        r=self.script(['bash'],path,'internet',CID='1',REPLY='ERROR')
        self.assertNotEqual(r.returncode,0)
        for apn in ('bad"AT','a b','a..b','a\nAT'):
            r=self.script(['bash'],path,apn,CID='1')
            self.assertNotEqual(r.returncode,0)
            self.assertNotIn('AT+CGDCONT',r.stderr)
        r=self.script(['bash'],path,CID='1')
        self.assertEqual(r.returncode,0);self.assertNotIn('AT+CGDCONT',r.stderr)
        r=self.script(['bash','-u'],path,'internet',CID='1',MU300_NETIFD='1',MU300_AUTH='chap',MU300_USERNAME='testuser',MU300_PASSWORD='testpass')
        self.assertEqual(r.returncode,0,r.stderr)
        # Authentication command is deliberately suppressed from stderr.
        self.assertNotIn('testpass',r.stderr)
        for value in ('bad"command','bad\\command','bad\ncommand','非ASCII'):
            r=self.script(['bash','-u'],path,'internet',CID='1',MU300_NETIFD='1',MU300_PASSWORD=value)
            self.assertNotEqual(r.returncode,0)
        r=self.script(['bash','-u'],path,'internet',CID='1',MU300_NETIFD='1')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('mobile-data up "$apn"',(TOP/'openwrt/overlay/lib/netifd/proto/mu300cell.sh').read_text())

    def test_integration(self):
        menu=json.loads((APP/'root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
        self.assertEqual(menu['admin/modem/apn']['action']['path'],'mu300/apn')
        acl=json.loads((APP/'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())
        self.assertIn('apn_get',acl['luci-app-mu300']['read']['ubus']['mu300dash'])
        self.assertIn('/etc/config/unisoc_apn',(APP/'Makefile').read_text())
        for build in ('build-openwrt-tf-magisk.sh','make-release.sh'):
            text=(TOP/'tools'/build).read_text()
            for path in ('usr/libexec/unisoc-modem/apn','usr/libexec/unisoc-modem/apn.uc','etc/config/unisoc_apn','www/luci-static/resources/view/mu300/apn.js'):
                self.assertIn('./'+path+' ',text)
        text=(LIB/'apn').read_text()
        self.assertNotIn('fs.system',text)
        self.assertNotIn('wifi reload',text)

    def test_errors_have_ui_labels(self):
        ui=(APP/'htdocs/luci-static/resources/view/mu300/apn.js').read_text()
        for name in ('apn','apn.uc'):
            for error in re.findall(r"'(apn_[a-z]+)'",(LIB/name).read_text()):
                self.assertIn(error+':',ui)
