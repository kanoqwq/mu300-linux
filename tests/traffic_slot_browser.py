"""Run the real common RPC wrappers and traffic form; no device connections."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    here=Path(__file__).resolve()
    default=here.parents[1] if (here.parents[1]/'openwrt').is_dir() else here.parents[2]
    parser.add_argument('--repository',type=Path,default=default)
    parser.add_argument('--browser',default='C:/Program Files/Google/Chrome/Application/chrome.exe')
    args=parser.parse_args()
    app='luci-app-t551m' if (args.repository/'openwrt/luci-app-t551m').is_dir() else 'luci-app-mu300'
    res=args.repository/'openwrt'/app/'htdocs/luci-static/resources'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser)
        for width in (390,1280):
            page=browser.new_page(viewport={'width':width,'height':900})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('http://fixture/**',lambda r:r.fulfill(body='<html><body></body></html>',content_type='text/html'))
            page.goto('http://fixture/')
            page.evaluate('''async ([common,source,e5])=>{
                window.e5=e5;window.calls=[];window.pending=[];window.slow=false;window.fail=false;
                window.setSlot=s=>{sessionStorage.setItem('mu300-view-slot',s);sessionStorage.setItem('modem-view-slot',s);};
                setSlot('0');
                window.plans=[0,1].map(slot=>({slot,config:{plan_name:'Plan '+slot,monthly_gb:slot?22:11,daily_gb:1,reset_day:slot+1,count_mode:slot?'rx':'total',device:slot?'sipa_eth8':'sipa_eth0'},
                    status:{slot,available:true,clock_ok:true,today_used:(slot+1)*100,month_used:(slot+1)*1000,cycle_used:(slot+1)*1000,remaining:(slot+1)*10000},days:[],months:[]}));
                const clone=x=>JSON.parse(JSON.stringify(x)),rpc={declare:o=>(...args)=>{
                    calls.push({method:o.method,args:clone(args)});
                    const slot=String(args[o.params?.indexOf('slot')]);
                    if(o.method==='traffic_get')return fail?Promise.reject(new Error('offline')):slow?new Promise(resolve=>pending.push({slot,resolve})):Promise.resolve(clone(plans[+slot]));
                    if(o.method==='traffic_set'){plans[+slot].config=JSON.parse(args[0]);return Promise.resolve({ok:1,data:clone(plans[+slot])});}
                    if(o.method==='traffic_clear'){plans[+slot].status.today_used=0;return Promise.resolve({ok:1,data:clone(plans[+slot])});}
                    if(o.method==='sim_get')return Promise.resolve({slots:2,data_sim:0});
                    return Promise.resolve({});
                }},base={extend:v=>v},L={env:{lang:'zh'},resolveDefault:(p,d)=>p.catch(()=>d)};
                window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);
                for(const name of ['injectCss','localizeMenu','localize','simSelector'])M[name]=()=>{};
                M.toast=()=>({update(){},close(){}});M.busy=(b,on)=>b.disabled=on;
                M.confirmBox=()=>new Promise(resolve=>window.answer=resolve);
                const poll={add:f=>window.poller=f,remove(){}};
                window.v=new Function('view','M','L','poll',source)(base,M,L,poll);
                document.body.append(v.render(await v.load()));
            }''',[(res/'mu300/common.js').read_text(encoding='utf-8'),(res/'view/mu300/traffic.js').read_text(encoding='utf-8'),app=='luci-app-t551m'])
            assert page.locator('#mud-tr-plan_name').input_value()=='Plan 0'
            page.evaluate("setSlot('1');v.refresh()")
            assert page.locator('#mud-tr-plan_name').input_value()=='Plan 1'
            assert page.locator('#mud-tr-monthly_gb').input_value()=='22'
            assert page.locator('#mud-tr-count_mode').input_value()=='rx'
            assert page.locator('#mud-tr-device').input_value()=='sipa_eth8'
            assert page.evaluate('v.data.status.today_used')==200
            # Polling the same SIM preserves an unsaved edit; a different SIM
            # replaces the entire form and calibration input with its own data.
            page.locator('#mud-tr-plan_name').fill('unsaved')
            page.evaluate('v.refresh()')
            assert page.locator('#mud-tr-plan_name').input_value()=='unsaved'
            page.evaluate("setSlot('0');v.refresh()")
            assert page.locator('#mud-tr-plan_name').input_value()=='Plan 0'
            # A delayed response from SIM0 cannot overwrite a newer SIM1 view.
            page.evaluate("slow=true;void v.refresh();setSlot('1');void v.refresh()")
            page.wait_for_function('pending.length===2')
            page.evaluate('pending[1].resolve(structuredClone(plans[1]))')
            page.wait_for_function("document.querySelector('#mud-tr-plan_name').value==='Plan 1'")
            page.evaluate('pending[0].resolve(structuredClone(plans[0]))')
            assert page.locator('#mud-tr-plan_name').input_value()=='Plan 1'
            page.evaluate('slow=false')
            # Confirmation is tied to the form's original SIM, not the slot
            # which happens to be selected when the dialog is accepted.
            page.locator('#mud-tr-plan_name').fill('SIM1 edited')
            page.locator('#mud-tr-used_gb').fill('3')
            page.locator('#mud-tr-save').click()
            page.wait_for_function("typeof answer==='function'")
            page.evaluate("setSlot('0');answer(true)")
            page.wait_for_function("calls.some(c=>c.method==='traffic_set')")
            assert page.evaluate("calls.find(c=>c.method==='traffic_set').args[1]")=='1'
            assert page.evaluate('plans[0].config.plan_name')=='Plan 0'
            page.wait_for_function("document.querySelector('#mud-tr-plan_name').value==='Plan 0'")
            # Clearing likewise stays within the ledger explicitly confirmed.
            page.evaluate("setSlot('1');v.refresh()")
            page.locator('#mud-tr-clear').click()
            page.evaluate("setSlot('0');answer(true)")
            page.wait_for_function("calls.some(c=>c.method==='traffic_clear')")
            assert page.evaluate("calls.find(c=>c.method==='traffic_clear').args")==(['1',True] if app=='luci-app-t551m' else [True,'1'])
            assert page.evaluate('plans[0].status.today_used')==100
            page.evaluate("setSlot('0');v.refresh()")
            # Failure on the same card preserves known settings, while failure
            # after switching must not show/save the previous card's form.
            page.evaluate('fail=true;v.refresh()')
            assert page.locator('#mud-tr-plan_name').input_value()=='Plan 0'
            page.evaluate("setSlot('1');v.refresh()")
            assert page.locator('#mud-tr-plan_name').input_value()==''
            count=page.evaluate("calls.filter(c=>c.method==='traffic_set').length")
            page.locator('#mud-tr-save').click()
            assert page.evaluate("calls.filter(c=>c.method==='traffic_set').length")==count
            page.evaluate('fail=false;v.refresh()')
            assert page.locator('#mud-tr-plan_name').input_value()=='SIM1 edited'
            assert not errors,errors
            page.close()
        browser.close()
    print(f'PASS {app}: per-slot plans/counters, edit preservation, delayed replies, confirmed save/clear scope; mobile + desktop')


if __name__=='__main__':main()
