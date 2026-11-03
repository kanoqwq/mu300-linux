"""Render the production roaming widget, without a modem or network calls."""
from pathlib import Path
import argparse
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--app',type=Path,default=TOP/'openwrt/luci-app-mu300');args=parser.parse_args()
RES=args.app/'htdocs/luci-static/resources'
common=(RES/'mu300/common.js').read_text(encoding='utf8')
widget=(RES/'mu300/roaming.js').read_text(encoding='utf8')
with sync_playwright() as engine:
    browser=engine.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    count=0
    for lang in ('zh-cn','en','tr'):
        for dark in (False,True):
            for width in (320,390,1280):
                page=browser.new_page(viewport={'width':width,'height':900});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content('<main id="root" class="mud"></main>')
                page.evaluate('''([common,widget,lang,dark])=>{
                 window.readonly=false;const L={env:{lang},hasViewPermission:()=>!window.readonly};
                 window.state={ok:1,slot:0,enabled:0,roaming:0,blocked:0};window.calls=[];window.waits=[];
                 const rpc={declare:o=>(...args)=>{
                  if(o.object!=='unisoc-roaming'||!o.nobatch)throw Error('Wrong RPC');
                  if(o.method==='get')return window.rejectGet?Promise.reject(Error('private raw transport')):Promise.resolve({...state});
                  calls.push(args);if(window.fail)return Promise.resolve({ok:0,error:fail});
                  if(window.hold)return new Promise(r=>waits.push(()=>{state={...state,enabled:+args[1]};r({...state});}));
                  state={...state,enabled:+args[1]};return Promise.resolve({...state});
                 }};
                 Object.defineProperty(window,'sessionStorage',{value:{getItem:()=>null,setItem(){}}});
                 window.M=new Function('rpc','baseclass','L',common)(rpc,{extend:o=>o},L);M.injectCss();
                 document.body.style.cssText='font-family:sans-serif;margin:12px;'+(dark?'--surface:#191d27;--text:#eee;--hairline:#454956;color:#eee;background:#11141a':'--surface:#fff;--text:#222;--hairline:#ddd;color:#222;background:#f3f4f6');
                 window.component=new Function('baseclass','rpc','M','L',widget)({extend:o=>o},rpc,M,L);
                 window.instance=component.mount(document.querySelector('#root'));
                }''',[common,widget,lang,dark])
                toggle=page.get_by_role('switch');card=page.locator('section')
                page.wait_for_function('!document.querySelector("#mud-roaming-switch").disabled')
                assert toggle.get_attribute('aria-checked')=='false'
                assert card.locator(':scope > h3').count()==1,'Use the same direct section heading as VoLTE and band locks'
                assert card.locator(':scope > h3').evaluate('e=>getComputedStyle(e).fontSize')=='11.2px'
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                if lang!='zh-cn':assert not any('\u4e00'<=c<='\u9fff' for c in card.inner_text())
                page.evaluate('hold=true');toggle.click()
                assert toggle.is_disabled() and card.get_attribute('aria-busy')=='true'
                page.evaluate('document.querySelector("#mud-roaming-switch").click()');assert page.evaluate('calls.length')==1
                page.evaluate('hold=false;waits.shift()()')
                page.wait_for_function('!document.querySelector("#mud-roaming-switch").disabled')
                assert toggle.get_attribute('aria-checked')=='true'
                assert toggle.evaluate('e=>getComputedStyle(e).backgroundColor')=='rgba(0, 0, 0, 0)'
                toggle.click();page.wait_for_function('state.enabled===0&&!document.querySelector("#mud-roaming-switch").disabled')
                assert page.evaluate('calls')==[['0','1'],['0','0']]
                page.evaluate('fail="roaming_notify_failed"');toggle.click();page.wait_for_timeout(30)
                assert toggle.is_disabled() and 'roaming_notify_failed' not in card.inner_text()
                page.evaluate('fail=null');card.locator('button').first.click()
                page.wait_for_function('!document.querySelector("#mud-roaming-switch").disabled')
                page.evaluate('rejectGet=true');card.locator('button').first.click();page.wait_for_timeout(30)
                assert toggle.is_disabled() and 'private raw transport' not in card.inner_text()
                page.evaluate('instance.dispose();readonly=true;rejectGet=false;document.querySelector("#root").replaceChildren();instance=component.mount(document.querySelector("#root"))')
                page.wait_for_function('document.querySelector("section").getAttribute("aria-busy")==="false"')
                assert toggle.is_disabled()
                assert not errors,errors
                page.close();count+=1
    browser.close()
print(f'PASS {count} roaming layouts/locales/themes/loading/double clicks/errors/persistence scope/permissions')
