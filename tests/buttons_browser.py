"""Exercise the actual LuCI button widget on desktop/mobile in zh/en/tr."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
JS=TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources'
sources={n:(JS/('mu300/'+n+'.js')).read_text(encoding='utf-8') for n in ('common','buttons')}
device=(JS/'view/mu300/device.js').read_text(encoding='utf-8')
style=device.split('<style>',1)[1].split('</style>',1)[0]
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    for model in ('f50pro','u30pro','u30air','v50'):
        for lang in ('zh','en','tr'):
            for width in (320,1280):
                page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content('<style>'+style+'</style><main class="mud"></main>')
                page.evaluate("""({sources,lang,model})=>{
                    window.calls=[];window.fail=false;
                    window.state={ok:1,supported:true,model,power:['u30air','u30pro'].includes(model),
                        short:model==='u30pro'?'page-next':model==='f50pro'?'none':'default',
                        long:['f50pro','u30pro'].includes(model)?'none':'default',
                        double:model==='u30air'?'wifi-band':'none',double_command:'',power_double:'none',power_double_command:'',
                        short_command:'',long_command:'',power_short:'default',power_command:''};
                    window.L={env:{lang},hasViewPermission:()=>true};
                    const rpc={declare:o=>(...args)=>{
                        if(o.method==='button_get')return Promise.resolve({...state});
                        if(o.method==='button_set'){
                            calls.push(args);if(fail)return Promise.reject(Error('transport'));
                            o.params.forEach((key,i)=>state[key]=args[i]);return Promise.resolve({...state});
                        }
                        throw Error(o.method);
                    }};
                    window.M=new Function('rpc','baseclass','L',sources.common)(rpc,{extend:x=>x},L);M.injectCss();
                    window.widget=new Function('rpc','baseclass','M','L',sources.buttons)(rpc,{extend:x=>x},M,L);
                    window.instance=widget.mount(document.querySelector('main'));
                }""",dict(sources=sources,lang=lang,model=model))
                save=page.locator('.mud-buttons button')
                page.wait_for_function('!document.querySelector(".mud-buttons button").disabled')
                assert page.evaluate('calls.length')==0
                assert page.locator('#mud-button-2').is_visible()==(model in ('u30pro','u30air'))
                assert not page.locator('[id*=power-long]').count()
                assert page.locator('#mud-button-4').is_visible()==(model in ('u30pro','u30air'))
                page.locator('#mud-button-3').select_option('custom')
                page.locator('#mud-button-3-command').fill('logger double')
                page.locator('#mud-button-0').select_option('custom')
                command=page.locator('#mud-button-0-command')
                assert command.is_visible()
                save.click()
                assert page.evaluate('calls.length')==0  # empty custom command cannot save
                command.fill("logger 'hello $world; test'")
                page.locator('#mud-button-1').select_option('wifi-band')
                save.click()
                page.wait_for_function('calls.length===1 && !document.querySelector(".mud-buttons button").disabled')
                assert page.evaluate('calls[0].slice(0,4)')==['custom','wifi-band',"logger 'hello $world; test'",'']
                assert page.evaluate('calls[0].slice(6,8)')==['custom','logger double']
                assert command.input_value()=="logger 'hello $world; test'"
                page.evaluate('fail=true');save.click()
                page.wait_for_function('calls.length===2 && !document.querySelector(".mud-buttons button").disabled')
                assert page.locator('[role=status]').inner_text()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                if lang!='zh':
                    assert not page.locator('main').evaluate('e=>/[\\u4e00-\\u9fff]/.test(e.textContent)')
                # No unsafe rendering of shell text, no execution, and read-only access has no save.
                page.evaluate("instance.dispose();document.querySelector('main').innerHTML='';L.hasViewPermission=()=>false;widget.mount(document.querySelector('main'))")
                page.wait_for_timeout(50)
                assert page.locator('.mud-buttons button').is_disabled()
                page.evaluate("document.querySelector('main').innerHTML='';state.supported=false;widget.mount(document.querySelector('main'))")
                page.wait_for_timeout(50)
                assert page.locator('.mud-buttons').is_hidden()
                assert not errors,errors
                page.close()
    browser.close()
print('PASS: four models, zh/en/tr, desktop/mobile, custom command fields, power restrictions, read-only and errors')
