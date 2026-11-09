"""Actual shared widget: theme/locale, saving, errors, permissions and disposal."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--app',type=Path,default=TOP/'openwrt/luci-app-mu300');args=p.parse_args()
res=args.app/'htdocs/luci-static/resources'
common=(res/'mu300/common.js').read_text(encoding='utf-8')
widget=(res/'mu300/network-performance.js').read_text(encoding='utf-8')
offload=(res/'mu300/flow-offload.js').read_text(encoding='utf-8')
device=(res/'view/mu300/device.js').read_text(encoding='utf-8')
style=device.split('<style>',1)[1].split('</style>',1)[0]
with sync_playwright() as engine:
    chrome=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    browser=engine.chromium.launch(headless=True,**({'executable_path':str(chrome)} if chrome.exists() else {}))
    cases=0
    for lang in ('zh-cn','en','tr'):
        for dark in (False,True):
            for width in (320,390,1280):
                page=browser.new_page(viewport={'width':width,'height':900});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/*',lambda route:route.abort())
                page.set_content('<html><body><main id="root" class="mud"></main></body></html>')
                bg,fg=('#202329','#eee') if dark else ('#fff','#222')
                page.add_style_tag(content=style+f'html{{--surface:{bg};--text:{fg};--hairline:#888;--text-muted:#888}}body{{margin:16px;background:{bg};color:{fg}}}')
                page.evaluate('''([common,widget,offload,lang])=>{
                    window.L={env:{lang},hasViewPermission:()=>!window.readonly};
                    window.requests=[];window.fail=null;
                    window.offloadRequests=[];window.offloadState={ok:true,supported:true,enabled:true};window.offloadFail=null;
                    window.state={ok:true,supported:true,available:['reno','cubic','bbr'],current:'cubic',qdisc:'pfifo_fast',selection:'default',defaults:{algorithm:'cubic',qdisc:'pfifo_fast'}};
                    const rpc={declare:o=>(algorithm)=>{
                        if(o.object!=='unisoc-network-performance')throw Error('Unrelated RPC');
                        if(o.method==='get_offload')return window.rejectOffload?Promise.reject(Error('RPC failed')):Promise.resolve(offloadState);
                        if(o.method==='apply_offload'){
                            offloadRequests.push(algorithm);
                            if(offloadFail)return Promise.resolve({ok:false,error:offloadFail,rollback:true});
                            if(window.deferOffload)return new Promise(resolve=>window.finishOffload=()=>{offloadState={...offloadState,enabled:algorithm};resolve(offloadState);});
                            offloadState={...offloadState,enabled:algorithm};return Promise.resolve(offloadState);
                        }
                        if(o.method==='get')return window.rejectGet?Promise.reject(Error('RPC transport failed')):Promise.resolve(state);
                        requests.push(algorithm);
                        if(fail)return Promise.resolve({ok:false,error:fail,rollback:true});
                        state={...state,current:algorithm==='default'?'cubic':algorithm,qdisc:algorithm==='bbr'?'fq':'pfifo_fast',selection:algorithm};
                        return Promise.resolve(state);
                    }};
                    window.M=new Function('rpc','baseclass','L',common)(rpc,{extend:o=>o},L);M.injectCss();
                    const FlowOffload=new Function('baseclass','rpc','M',offload)({extend:o=>o},rpc,M);
                    window.component=new Function('baseclass','rpc','M','FlowOffload',widget)({extend:o=>o},rpc,M,FlowOffload);
                    window.instance=component.mount(document.querySelector('#root'));
                }''',[common,widget,offload,lang])
                card=page.locator('.mud-network-performance');select=card.locator('select');save=card.locator('.mud-device-actions button')
                page.wait_for_function('!document.querySelector("#mud-network-algorithm").disabled')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(lang,dark,width,'overflow')
                if lang!='zh-cn':assert not any('\u4e00'<=c<='\u9fff' for c in card.inner_text()),card.inner_text()
                select.select_option('bbr');save.click();page.wait_for_function('state.current==="bbr" && !document.querySelector("#mud-network-algorithm").disabled')
                assert card.locator('.mud-v').all_text_contents()==['bbr','fq']
                select.select_option('default');save.click();page.wait_for_function('state.current==="cubic" && !document.querySelector("#mud-network-algorithm").disabled')
                assert page.evaluate('requests')==['bbr','default']
                toggle=card.locator('#mud-flow-offload-switch')
                assert toggle.get_attribute('aria-checked')=='true'
                page.evaluate('deferOffload=true');toggle.click()
                assert toggle.is_disabled()
                assert card.locator('.mud-flow-offload').get_attribute('aria-busy')=='true'
                page.evaluate('finishOffload();deferOffload=false')
                page.wait_for_function('!document.querySelector("#mud-flow-offload-switch").disabled')
                assert toggle.get_attribute('aria-checked')=='false'
                toggle.click();page.wait_for_function('!document.querySelector("#mud-flow-offload-switch").disabled')
                assert toggle.get_attribute('aria-checked')=='true'
                assert page.evaluate('offloadRequests')==[False,True]
                assert page.evaluate('requests')==['bbr','default']
                page.evaluate('offloadFail="offload_apply_failed"');toggle.click()
                page.wait_for_function('!document.querySelector("#mud-flow-offload-switch").disabled')
                assert toggle.get_attribute('aria-checked')=='true'
                assert 'offload_apply_failed' not in card.inner_text()
                page.evaluate('fail="network_save_failed"');select.select_option('bbr');save.click()
                page.wait_for_function('!document.querySelector("#mud-network-algorithm").disabled')
                assert 'network_save_failed' not in card.inner_text()
                assert select.input_value()=='default'
                page.evaluate('readonly=true');card.locator('.mud-device-head button').first.click()
                # Permissions are captured at mount, as LuCI grants do not change in-place.
                page.evaluate('instance.dispose();document.querySelector("#root").replaceChildren();instance=component.mount(document.querySelector("#root"))')
                page.wait_for_timeout(50);assert card.locator('select').is_disabled();assert toggle.is_disabled()
                page.evaluate('readonly=false;state={...state,available:["cubic"]};instance.dispose();document.querySelector("#root").replaceChildren();instance=component.mount(document.querySelector("#root"))')
                page.wait_for_function('!document.querySelector("#mud-network-algorithm").disabled')
                assert select.locator('option[value="bbr"]').count()==0
                page.evaluate('rejectGet=true');card.locator('.mud-device-head button').first.click();page.wait_for_timeout(50)
                assert select.is_disabled();assert 'RPC transport failed' not in card.inner_text()
                assert not toggle.is_disabled() # Independent of congestion-control availability.
                page.evaluate('rejectOffload=true');card.locator('.mud-flow-offload .mud-device-head button').click()
                page.wait_for_function('document.querySelector("#mud-flow-offload-switch").disabled')
                assert toggle.is_disabled()
                assert not errors,errors
                page.close();cases+=1
    browser.close()
print(f'PASS {cases} widget layouts: language, theme, mobile/desktop, save/default, failure, read-only and unsupported algorithms')
