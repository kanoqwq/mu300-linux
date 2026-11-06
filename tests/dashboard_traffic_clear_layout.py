"""Exercise real traffic page/common dialogs against isolated fake RPCs."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

RES = Path(__file__).resolve().parents[1] / 'openwrt/luci-app-mu300/htdocs/luci-static/resources'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser')
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'executable_path': args.browser} if args.browser else {}))
        cases = 0
        for lang in ('zh', 'en', 'tr'):
            for width in (390, 1280):
                page = browser.new_page(viewport={'width': width, 'height': 900})
                page.route('**/*', lambda route: route.abort())
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.set_content('<html><body><main id="app"></main></body></html>')
                page.evaluate('''([common,traffic,lang]) => {
                    const base={extend:x=>x}, L={env:{lang},resolveDefault:p=>p.catch(()=>({}))};
                    window.calls=[]; window.gets=[];
                    window.old={config:{plan_name:'My plan',monthly_gb:100,daily_gb:2,reset_day:5,count_mode:'rx',device:'sipa_eth0'},
                        status:{today_used:123456,month_used:123456,cycle_used:123456,available:true,clock_ok:true},
                        days:[{date:'2026-10-10',rx:123456,tx:0}],months:[{date:'2026-10',rx:123456,tx:0}]};
                    window.cleared={...old,status:{...old.status,today_used:0,month_used:0,cycle_used:0},days:[],months:[]};
                    const rpc={declare:o=>(...args)=>{
                        calls.push([o.method,args]);
                        if(o.method==='traffic_get') return new Promise(r=>gets.push(r));
                        if(o.method==='traffic_clear') return new Promise(r=>window.complete=r);
                        return Promise.resolve({});
                    }};
                    window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);
                    const poll={add:f=>window.poller=f,remove:f=>window.removed=f===poller};
                    window.v=new Function('view','M','L','poll',traffic)(base,M,L,poll);
                    document.querySelector('#app').appendChild(v.render(old));
                }''', [(RES/'mu300/common.js').read_text(encoding='utf-8'),
                       (RES/'view/mu300/traffic.js').read_text(encoding='utf-8'), lang])
                page.locator('#mud-tr-clear').click()
                page.wait_for_selector('.mud-dlg')
                if lang != 'zh':
                    assert not page.locator('.mud-dlg').evaluate("e=>/[\\u4e00-\\u9fff]/.test(e.textContent)")
                # Escape/cancel never sends a mutation.
                page.keyboard.press('Escape')
                page.wait_for_function('!v._confirming')
                assert page.evaluate("calls.filter(c=>c[0]==='traffic_clear').length") == 0
                # Begin an old poll, then clear; late pre-clear response must be ignored.
                page.evaluate('void v.refresh()')
                page.locator('#mud-tr-clear').click()
                page.locator('.mud-dlg .mud-btn').last.click()
                page.wait_for_function('!!window.complete')
                assert page.locator('#mud-tr-save').is_disabled()
                assert page.locator('#mud-tr-clear').is_disabled()
                assert page.locator('#mud-tr-clear .mud-spin').count() == 1
                assert page.evaluate("calls.filter(c=>c[0]==='traffic_clear')") == [['traffic_clear', [True, '0']]]
                page.evaluate('complete({ok:1,data:cleared})')
                page.wait_for_function('!v._mutating && !v._confirming')
                assert page.locator('#mud-tr-clear .mud-spin').count() == 0
                assert page.locator('#mud-tr-plan_name').input_value() == 'My plan'
                assert page.locator('#mud-tr-device').input_value() == 'sipa_eth0'
                page.evaluate('gets.shift()(old)')
                assert page.evaluate('v.data.status.today_used') == 0
                page.evaluate('gets.shift()(cleared)')
                # A failed transaction leaves the displayed ledger intact.
                page.evaluate('v.data=old;v.paint();window.complete=null')
                page.locator('#mud-tr-clear').click()
                page.locator('.mud-dlg .mud-btn').last.click()
                page.wait_for_function('!!window.complete')
                page.evaluate("complete({ok:0,error:'storage_failed'})")
                page.wait_for_function('!v._mutating && !v._confirming')
                assert page.evaluate('v.data.status.today_used') == 123456
                assert not page.locator('#mud-tr-save').is_disabled()
                if lang != 'zh':
                    assert not page.locator('#app').evaluate("e=>/[\\u4e00-\\u9fff]/.test(e.textContent)")
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (lang,width,'overflow')
                page.evaluate('v.unload();gets.shift()(cleared)')
                assert page.evaluate('removed && v.data.status.today_used===123456')
                assert not errors, errors
                cases += 1
                page.close()
        browser.close()
        print(f'traffic clear page: {cases} language/viewport cases passed')


if __name__ == '__main__':
    main()
