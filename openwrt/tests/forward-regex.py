#!/usr/bin/env python3
"""Run the actual target ucode filter; never deliver SMS or access a modem."""
import json,os,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
PREFIX=json.loads(os.environ.get('FORWARD_UCODE_COMMAND','["ucode"]'))
if os.environ.get('FORWARD_FILTER_LIBRARIES'):
    APPS=[Path(p) for p in json.loads(os.environ['FORWARD_FILTER_LIBRARIES'])]
else:
    APPS=[p for p in (ROOT/'openwrt/luci-app-mu300',ROOT/'openwrt/luci-app-t551m') if p.is_dir()]
if not APPS: raise RuntimeError('No SMS forwarding source found')

class RegexTests(unittest.TestCase):
    def invoke(self,data,action='check',app=None):
        helper=(app or APPS[0])/'root/usr/libexec/unisoc-modem/forward-filter.uc'
        r=subprocess.run(PREFIX+[str(helper),action],input=json.dumps(data,ensure_ascii=False),capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stderr,'')
        return json.loads(r.stdout) if action=='validate' else r.stdout.strip()
    def check(self,data,expected):self.assertEqual(self.invoke(dict(number='106123',text='code 123456',**data)),expected)
    def test_default_and_all_platform_helpers(self):
        self.assertEqual(len({(p/'root/usr/libexec/unisoc-modem/forward-filter.uc').read_bytes() for p in APPS}),1)
        for p in APPS:self.assertEqual(self.invoke({'number':'anything','text':'any message'},app=p),'allow')
    def test_number_blacklist(self):
        for number,want in [('106123','block'),('10086','allow'),('+86106123','allow')]:
            self.assertEqual(self.invoke({'number':number,'text':'hello','number_filter_mode':'blacklist','number_filter_regex':'^106[0-9]+$'}),want)
    def test_number_whitelist_and_optional_country(self):
        for number,want in [('106123','allow'),('+86106123','allow'),('10086','block')]:
            self.assertEqual(self.invoke({'number':number,'text':'hello','number_filter_mode':'whitelist','number_filter_regex':r'^(\+86)?106[0-9]+$'}),want)
    def test_text_blacklist_unicode_and_emoji(self):
        for text,want in [('广告通知','block'),('你的取件码1234','allow'),('🚚已送达','block')]:
            self.assertEqual(self.invoke({'number':'10086','text':text,'text_filter_mode':'blacklist','text_filter_regex':'广告|🚚'}),want)
    def test_case_flag(self):
        for flag,want in [(0,'allow'),(1,'block'),(False,'allow'),(True,'block')]:
            self.assertEqual(self.invoke({'number':'1','text':'PROMO','text_filter_mode':'blacklist','text_filter_regex':'promo','filter_ignore_case':flag}),want)
    def test_groups_intersect_and_blacklist_wins(self):
        for number,text,want in [('106123','code','allow'),('106123','promo','block'),('10086','code','block')]:
            self.assertEqual(self.invoke({'number':number,'text':text,'number_filter_mode':'whitelist','number_filter_regex':'^106','text_filter_mode':'blacklist','text_filter_regex':'promo'}),want)
    def test_dotall_and_full_message_anchors(self):
        data={'number':'1','text':'通知\n取件码1234','text_filter_mode':'whitelist','text_filter_regex':'^通知.*取件码[0-9]{4}$'}
        self.assertEqual(self.invoke(data),'allow');data['text']='前缀\n通知\n取件码1234';self.assertEqual(self.invoke(data),'block')
    def test_spaces_preserved_and_blank_lines_ignored(self):
        data={'number':'1','text':' leading ','text_filter_mode':'whitelist','text_filter_regex':'\n^ leading $\r\n\n'}
        self.assertEqual(self.invoke(data),'allow');data['text']='leading';self.assertEqual(self.invoke(data),'block')
    def test_empty_match_is_a_match(self):
        self.assertEqual(self.invoke({'number':'1','text':'','text_filter_mode':'blacklist','text_filter_regex':'^$'}),'block')
    def test_invalid_regex_line_and_platforms(self):
        for app in APPS:
            result=self.invoke({'number_filter_mode':'blacklist','number_filter_regex':'ok\n['},'validate',app)
            self.assertEqual(result,{'ok':0,'error':'invalid_number_filter_regex','line':2})
    def test_invalid_types_modes_limits_and_whitelist(self):
        for value in [[],1,None]:self.assertEqual(self.invoke({'number_filter_regex':value},'validate')['ok'],0)
        for data in [{'number_filter_mode':'unknown'},{'filter_ignore_case':'1'},{'text_filter_mode':'whitelist'},{'text_filter_regex':'a'*513},{'text_filter_regex':'\n'.join('x' for _ in range(33))},{'text_filter_regex':'a\x00b'}]:
            with self.subTest(data=data): self.assertEqual(self.invoke(data,'validate')['ok'],0)
    def test_sms_cannot_execute_commands(self):
        self.assertEqual(self.invoke({'number':'106123','text':'$(touch /tmp/forward-regex-must-not-exist); `id`','text_filter_mode':'whitelist','text_filter_regex':'.*'}),'allow')
    def test_invalid_saved_config_does_not_allow_sms(self):
        self.assertEqual(self.invoke({'number':'123','text':'sensitive','text_filter_mode':'whitelist','text_filter_regex':'['}),'invalid')
    def test_number_and_text_whitelists_both_required(self):
        for number,text,want in [('106123','OTP','allow'),('106123','hello','block'),('10086','OTP','block')]:
            self.assertEqual(self.invoke({'number':number,'text':text,'number_filter_mode':'whitelist','number_filter_regex':'^106','text_filter_mode':'whitelist','text_filter_regex':'OTP'}),want)

if __name__=='__main__':unittest.main(verbosity=2)