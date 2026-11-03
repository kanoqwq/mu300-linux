"""Actual NFC widget across locales/themes, permissions and async failures."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--app',type=Path,default=TOP/'openwrt/luci-app-mu300');args=p.parse_args()
res=args.app/'htdocs/luci-static/resources'
common=(res/'mu300/common.js').read_text(encoding='utf8')
widget=(res/'mu300/nfc.js').read_text(encoding='utf8')
device=(res/'view/mu300/device.js').read_text(encoding='utf8')
style=device.split('<style>',1)[1].split('</style>',1)[0]
with sync_playwright() as engine:
    chrome=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    browser=engine.chromium.launch(headless=True,**({'executable_path':str(chrome)} if chrome.exists() else {}))
    cases=0
    for lang in ('zh-cn','en','tr'):
        for dark in (False,True):
            for width in (320,390,1280):
                page=browser.new_page(viewport={'width':width,'height':900});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',lambda r:r.abort())
                page.set_content('<html><head><style>'+style+'</style></head><body><main id="root" class="mud"></main></body></html>')
                page.evaluate('''([common,widget,lang,dark])=>{
                    const L={env:{lang},hasViewPermission:()=>!window.readonly};window.readonly=false;
                    document.body.style.cssText='font-family:sans-serif;margin:12px;'+(dark?'--surface:#191d27;--text:#eee;--text-muted:#aaa;--hairline:#454956;color:#eee;background:#11141a':'--surface:#fff;--text:#222;--text-muted:#666;--hairline:#ddd;color:#222;background:#f3f4f6');
                    window.state={ok:1,supported:1,enabled:1};window.calls=[];window.waits=[];
                    const rpc={declare:o=>(...args)=>{
                        if(o.object!=='unisoc-nfc' || !o.nobatch)throw Error('Unexpected RPC');
                        if(o.method==='get')return window.holdGet?new Promise(r=>waits.push(r)):window.rejectGet?Promise.reject(Error('raw transport detail')):Promise.resolve({...state});
                        if(JSON.stringify(o.params)!=='["enabled"]')throw Error('Wrong parameters');
                        calls.push(args);if(window.fail)return Promise.resolve({ok:0,error:fail});
                        if(window.holdSet)return new Promise(r=>waits.push(()=>{state={...state,enabled:+args[0]};r({...state});}));
                        state={...state,enabled:+args[0]};return Promise.resolve({...state});
                    }};
                    window.M=new Function('rpc','baseclass','L',common)(rpc,{extend:o=>o},L);M.injectCss();
                    window.component=new Function('baseclass','rpc','M','L',widget)({extend:o=>o},rpc,M,L);
                    window.instance=component.mount(document.querySelector('#root'));
                }''',[common,widget,lang,dark])
                card=page.locator('.mud-nfc');toggle=page.get_by_role('switch',name='NFC');refresh=card.locator('.mud-device-head button')
                page.wait_for_function('!document.querySelector("#mud-nfc-switch").disabled')
                assert card.locator('h3').inner_text()=='NFC'
                assert toggle.get_attribute('aria-checked')=='true'
                assert toggle.evaluate('e=>getComputedStyle(e).backgroundColor')=='rgba(0, 0, 0, 0)'
                assert card.locator('.mud-spin').evaluate('e=>getComputedStyle(e).display')=='none'
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(lang,dark,width,'overflow')
                if lang!='zh-cn':assert not any('\u4e00'<=c<='\u9fff' for c in card.inner_text())
                page.evaluate('holdSet=true');toggle.click()
                assert toggle.is_disabled();assert refresh.is_disabled()
                assert card.get_attribute('aria-busy')=='true'
                assert card.locator('.mud-spin').evaluate('e=>getComputedStyle(e).display')!='none'
                page.evaluate('document.querySelector("#mud-nfc-switch").click()');assert page.evaluate('calls.length')==1
                page.evaluate('holdSet=false;waits.shift()()')
                page.wait_for_function('!document.querySelector("#mud-nfc-switch").disabled')
                assert toggle.get_attribute('aria-checked')=='false'
                toggle.click();page.wait_for_function('state.enabled===1 && !document.querySelector("#mud-nfc-switch").disabled')
                assert page.evaluate('calls')==[['0'],['1']]
                page.evaluate('fail="nfc_apply_failed"');toggle.click()
                page.wait_for_function('!document.querySelector("#mud-nfc-switch").disabled')
                assert toggle.get_attribute('aria-checked')=='true'
                assert 'nfc_apply_failed' not in card.inner_text()
                page.evaluate('rejectGet=true');refresh.click();page.wait_for_timeout(30)
                assert toggle.is_disabled();assert 'raw transport detail' not in card.inner_text()
                page.evaluate('rejectGet=false;fail=null');refresh.click()
                page.wait_for_function('!document.querySelector("#mud-nfc-switch").disabled')
                page.evaluate('instance.dispose();readonly=true;document.querySelector("#root").replaceChildren();instance=component.mount(document.querySelector("#root"))')
                page.wait_for_function('document.querySelector(".mud-nfc").getAttribute("aria-busy")==="false"')
                assert toggle.is_disabled()
                page.evaluate('instance.dispose();readonly=false;state={ok:1,supported:0};document.querySelector("#root").replaceChildren();instance=component.mount(document.querySelector("#root"))')
                page.wait_for_function('document.querySelector(".mud-nfc").hidden')
                assert not card.is_visible()
                page.evaluate('instance.dispose();holdGet=true;document.querySelector("#root").replaceChildren();instance=component.mount(document.querySelector("#root"))')
                before=card.inner_html();page.evaluate('instance.dispose();waits.shift()({ok:1,supported:1,enabled:0})');page.wait_for_timeout(20)
                assert card.inner_html()==before
                assert not errors,errors
                page.close();cases+=1
    browser.close()
print(f'PASS {cases} NFC layouts: locales, themes, switch, loading, errors, permissions, unsupported models and disposal')
