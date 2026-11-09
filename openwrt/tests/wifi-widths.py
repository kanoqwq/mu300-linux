#!/usr/bin/env python3
"""Exercise the shipped ucode transform and real LuCI frequency widget offline."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[2]
POLICY=next((TOP/'openwrt').glob('luci-app-*/root/usr/libexec/unisoc-modem/limit-wifi-widths'))
SOURCE=Path(os.environ.get('UNISOC_WIFI_TEST_WIRELESS','/home/kano/openwrt-build/base/e5/www/luci-static/resources/view/network/wireless.js'))
IMAGE=os.environ.get('UNISOC_WIFI_TEST_IMAGE','mu300-openwrt-base:25.12.5')


class WidthChoices(unittest.TestCase):
    def apply(self,root,success=True):
        r=subprocess.run(['docker','run','--rm','--network','none','--platform','linux/arm64',
            '-v',str(root)+':/target','-v',str(POLICY)+':/policy:ro','--entrypoint','/usr/bin/ucode',
            IMAGE,'/policy','/target'],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode==0,success,r.stdout+r.stderr)

    def target(self,root,text):
        p=root/'www/luci-static/resources/view/network/wireless.js'
        p.parent.mkdir(parents=True);p.write_text(text);return p

    def test_real_widget_options_preserve_n_ac_ax_and_legacy_config(self):
        if not SOURCE.is_file():self.skipTest('UNISOC_WIFI_TEST_WIRELESS required')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.target(root,SOURCE.read_text())
            self.apply(root);first=p.read_bytes();stamp=p.stat().st_mtime_ns
            self.apply(root);self.assertEqual(first,p.read_bytes());self.assertEqual(stamp,p.stat().st_mtime_ns)
            subprocess.run(['node','--check',str(p)],check=True)
            text=p.read_text();start=text.index('CBIWifiFrequencyValue');start=text.rfind('var ',0,start) if 'var CBIWifiFrequencyValue' in text else text.rfind('const ',0,start)
            end=text.index('CBIWifiTxPowerValue',start);end=max(text.rfind('var ',start,end),text.rfind('const ',start,end))
            widget=text[start:end]
            test=r'''
const assert=require('assert'), source=JSON.parse(process.argv[1]);
String.prototype.format=function(...a){let n=0;return this.replace(/%[ds]/g,()=>String(a[n++]));};
let saved='HE80',writes=0;
const uci={get(p,s,k){return {htmode:saved,band:'5g',channel:'36'}[k]},set(){writes++}};
const L={env:{lang:'en'},toArray:x=>x||[],hasSystemFeature:()=>true,bind:(f,c,...a)=>f.bind(c,...a)};
const freqs=[{band:2,channel:1,mhz:2412},{band:5,channel:36,mhz:5180}];
const modes=['HT20','HT40','VHT20','VHT40','VHT80','VHT160','HE20','HE40','HE80','HE160','EHT20','EHT40','EHT80','EHT160'];
const network={getWifiDevice:async()=>({getHWModes:()=>['a','b','g','n','ac','ax','be'],getHTModes:()=>modes})};
const E=(tag,attrs,children)=>({value:attrs.value,disabled:!!attrs.disabled,hidden:!!attrs.hidden,children});
const widget=new Function('form','rpc','network','L','uci','E','dom','_',source+';return CBIWifiFrequencyValue;')(
 {Value:{extend:x=>x}},{declare:()=>async()=>freqs},network,L,uci,E,{},s=>s);
widget.map={checkDepends(){},readonly:false};
function select(){return {options:[],selectedIndex:-1,parentNode:{style:{}},add(o){this.options.push(o);if(this.selectedIndex<0&&!o.disabled)this.selectedIndex=this.options.length-1},get value(){return this.options[this.selectedIndex]?.value??''},set value(v){this.selectedIndex=this.options.findIndex(o=>o.value===String(v))}}}
function element(){const items={'.mode':select(),'.band':select(),'.channel':select(),'.htmode':select(),'.restricted_channel':{style:{}}};return {items,querySelector:k=>items[k]}}
(async()=>{
 await widget.load('radio0');
 for(const [family,keep] of [['n',['HT20','HT40']],['ac',['VHT20','VHT40','VHT80']],['ax',['HE20','HE40','HE80']],['be',['EHT20','EHT40','EHT80']]]){
  const options=widget.htmodes[family],available=[];
  for(let i=0;i<options.length;i+=3)if(options[i+2].available)available.push(options[i]);
  assert.deepStrictEqual(available,keep);
 }
 for(const [value,mode] of [['VHT160','ac'],['HE160','ax'],['EHT160','be']]){
  saved=value;const elem=element();widget.setInitialValues('radio0',elem);
  const width=elem.items['.htmode'];assert.equal(elem.items['.mode'].value,mode);
  assert.equal(width.value,value,'opening the view must not clear the saved width');
  assert(!width.options.some(o=>/160/.test(o.value)&&!o.hidden&&!o.disabled),'160 offered to user');
  assert.equal(typeof widget.validate('radio0',[value,'5g','36']),'string');
  width.value=value.replace('160','80');assert.equal(widget.validate('radio0',[width.value,'5g','36']),true);
 }
 for(const lang of ['zh-cn','en','tr']){L.env.lang=lang;assert.equal(typeof widget.validate('radio0',['HE160','5g','36']),'string')}
 assert.equal(widget.validate('radio0',['HT40','2g','1']),true);
 assert.equal(widget.validate('radio0',['','5g','36']),true);
 assert.equal(writes,0,'view policy must not rewrite UCI');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
            subprocess.run(['node','-e',test,json.dumps(widget)],check=True)

    def test_unknown_source_is_not_partially_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=self.target(root,"'use strict'; unknown();")
            before=p.read_bytes();self.apply(root,success=False);self.assertEqual(before,p.read_bytes())

    def test_missing_optional_wireless_view(self):
        with tempfile.TemporaryDirectory() as tmp:self.apply(Path(tmp))

    def test_all_build_install_and_boot_paths_include_policy(self):
        plugin=POLICY.parents[4]
        self.assertIn('limit-wifi-widths',(TOP/'openwrt/build-rootfs.sh').read_text())
        self.assertIn('limit-wifi-widths',(plugin/'Makefile').read_text())
        self.assertIn('limit-wifi-widths',(plugin/'root/etc/init.d/unisoc-modem-ui').read_text())
        self.assertIn('limit-wifi-widths',(plugin/'root/etc/uci-defaults/99-unisoc-wifi-widths').read_text())
        if (TOP/'tools/u30pro-package.py').is_file():
            self.assertIn('limit-wifi-widths',(TOP/'tools/u30pro-package.py').read_text())
        if (TOP/'openwrt/build-profile-tf.py').is_file():
            self.assertIn('limit-wifi-widths',(TOP/'openwrt/build-profile-tf.py').read_text())


if __name__=='__main__':unittest.main()
