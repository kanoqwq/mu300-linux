"""Real LuCI traffic view + controls, with a fake RPC transport only."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, default=next(p for p in Path(__file__).resolve().parents if (p/'openwrt').is_dir()))
    parser.add_argument('--browser', default='C:/Program Files/Google/Chrome/Application/chrome.exe')
    args = parser.parse_args()
    app = 'luci-app-t551m' if (args.repository/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
    res = args.repository/'openwrt'/app/'htdocs/luci-static/resources'
    code = [(res/f).read_text(encoding='utf8') for f in ['mu300/common.js', 'view/mu300/traffic.js', 'mu300/traffic-csv.js', 'mu300/traffic-controls.js']]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.browser, headless=True)
        for lang in ('zh', 'en', 'tr'):
            for width in (390, 1280):
                page = browser.new_page(viewport={'width': width, 'height': 1100})
                errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
                page.route('http://fixture/**', lambda r: r.fulfill(body='<html><body></body></html>', content_type='text/html'))
                page.goto('http://fixture/')
                page.evaluate('''async ([common,view,csv,controls,lang])=>{
                    window.calls=[];window.quotas=[{enabled:false,limit_gb:0},{enabled:true,limit_gb:12}];
                    window.schedule={enabled:false,time:'07:00'};window.slot='0';window.delay=false;window.waiting=[];
                    window.setSlot=s=>{window.slot=s;sessionStorage.setItem('mu300-view-slot',s);sessionStorage.setItem('modem-view-slot',s);if(window.M?.selectViewedSlot)M.selectViewedSlot(s);};setSlot('0');
                    window.plans=[0,1].map(slot=>({slot,config:{plan_name:'Plan '+slot,reset_day:1,count_mode:'total'},status:{slot,clock_ok:true,available:true,cycle_used:2e9,cycle_end:'2026-12-01'},days:[],months:[]}));
                    const rpc={declare:o=>(...args)=>{
                        calls.push({method:o.method,args});
                        if(o.method==='traffic_get')return Promise.resolve(structuredClone(plans[+args[0]]));
                        if(o.method==='traffic_policy_get'){
                            const s=+args[0], r={ok:1,slot:s,quota:structuredClone(quotas[s]),schedule:structuredClone(schedule),local_time:'2026-11-18 16:45:00 CST',next_time:schedule.enabled?'2026-11-19 07:00:00 CST':null};
                            return delay?new Promise(resolve=>waiting.push({resolve,r})):Promise.resolve(r);
                        }
                        if(o.method==='traffic_policy_set'){
                            const p=JSON.parse(args[0]);if(p.config.quota)quotas[+p.slot]=p.config.quota;if(p.config.schedule)schedule=p.config.schedule;
                            return Promise.resolve({ok:1});
                        }
                        if(o.method==='traffic_policy_run')return Promise.resolve({ok:1});
                        return Promise.resolve({});
                    }}, base={extend:o=>o}, L={env:{lang},resolveDefault:(p,d)=>p.catch(()=>d)};
                    window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);
                    M.injectCss=M.localizeMenu=M.simSelector=()=>{};
                    M.toast=()=>({update(){},close(){}});M.busy=(b,on)=>b.disabled=on;
                    M.confirmBox=()=>new Promise(resolve=>window.answer=resolve);
                    window.v=new Function('view','M','poll','L','TrafficCsv','TrafficControls',view)(base,M,{add(){},remove(){}},L,new Function('baseclass',csv)(base),new Function('baseclass','rpc',controls)(base,rpc));
                    document.body.append(v.render(await v.load()));
                }''', code + [lang])
                page.wait_for_function("!document.querySelector('#mud-quota-save').disabled")
                self_box = page.locator('#mud-tr-controls')
                assert not page.locator('#mud-quota-enabled').is_checked()
                assert not page.locator('#mud-schedule-enabled').is_checked()
                assert page.locator('#mud-schedule-now').inner_text() == '2026-11-18 16:45:00 CST'
                if lang != 'zh':
                    assert not self_box.evaluate('(e)=>/[\\u3400-\\u9fff]/.test(e.innerText)'), (lang,self_box.inner_text())
                # Unsaved quota remains through background status updates.
                page.locator('#mud-quota-enabled').check();page.locator('#mud-quota-limit').fill('8.5')
                page.evaluate('v.refresh()');page.wait_for_timeout(100)
                assert page.locator('#mud-quota-limit').input_value() == '8.5'
                page.locator('#mud-quota-save').click()
                page.wait_for_function('quotas[0].limit_gb===8.5')
                assert page.evaluate('quotas[1].limit_gb') == 12
                assert not page.evaluate('schedule.enabled')
                # Card selection changes quota only; schedule is global.
                page.evaluate("setSlot('1');v.refresh()")
                page.wait_for_function("document.querySelector('#mud-quota-limit').value==='12'")
                page.locator('#mud-schedule-enabled').check();page.locator('#mud-schedule-time').fill('08:15')
                page.locator('#mud-schedule-save').click();page.wait_for_function("schedule.time==='08:15'")
                assert page.evaluate('quotas[1].limit_gb') == 12
                saved = page.evaluate("JSON.parse(calls.filter(c=>c.method==='traffic_policy_set').at(-1).args[0])")
                assert 'quota' not in saved['config']
                # Cancelling a manual restart never issues its RPC.
                page.locator('#mud-schedule-run').click();page.evaluate('answer(false)');page.wait_for_timeout(100)
                assert not page.evaluate("calls.some(c=>c.method==='traffic_policy_run')")
                page.locator('#mud-schedule-run').click();page.evaluate('answer(true)')
                page.wait_for_function("calls.some(c=>c.method==='traffic_policy_run'&&c.args[0]===true)")
                # Late responses for another SIM must not populate this form.
                page.evaluate("delay=true;void v.refresh()")
                page.wait_for_function('waiting.length===1')
                page.evaluate("setSlot('0');void v.refresh();waiting[0].resolve(waiting[0].r)")
                page.wait_for_timeout(100)
                assert page.evaluate('slot') == '0'
                page.evaluate('delay=false;void v.refresh()');page.wait_for_timeout(100)
                page.evaluate('v.unload()')
                assert not errors, errors
                assert self_box.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'), (lang,width,'horizontal overflow')
                page.close()
        browser.close()
    print('PASS traffic controls: per-SIM cutoff, global schedule, dirty edits, stale replies, confirmation, 3 languages, desktop/mobile')


if __name__ == '__main__': main()
