"""Exercise the shipped dashboard and theme/i18n with both equipment rows."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--resources', type=Path, default=Path(__file__).resolve().parents[1] / 'openwrt/luci-app-mu300/htdocs/luci-static/resources')
parser.add_argument('--screenshots', type=Path)
args = parser.parse_args()
if args.screenshots: args.screenshots.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    for language, yes, no, active in [('zh', '是', '否', '使用中'), ('en', 'Yes', 'No', 'In use'), ('tr', 'Evet', 'Hayır', 'Kullanımda')]:
        for dark in [False, True]:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('about:blank')
            # A normal origin is required by the production sessionStorage helpers.
            page.route('http://dashboard.test/**', lambda route: route.fulfill(content_type='text/html', body='<main></main>'))
            page.goto('http://dashboard.test/')
            page.evaluate('''([common,home,language,dark])=>{
                window.L={env:{lang:language}};
                const M=new Function('rpc','baseclass','L',common)(
                    {declare:()=>()=>Promise.resolve({})},{extend:o=>o},L);
                window.M=M;
                // View SIM2 while SIM1 carries data, then reverse only the data slot.
                sessionStorage.setItem('modem-view-slot','1');
                sessionStorage.setItem('mu300-view-slot','1');
                window.app=new Function('view','uci','M','SimRadio',home)({extend:o=>o},{},M,{});
                const root=document.querySelector('main');root.innerHTML=app.html();app._root=root;
                document.documentElement.style.cssText=dark?'--surface:#171b24;--text:#eee;--success:#57c98a':'--surface:#fff;--text:#111;--success:#16814b';
                document.body.style.color=dark?'#eee':'#111';
                M.injectCss();
                window.snapshot={info:{modem:{alive:1,atd:1,data_sim:0,imeis:['866100000000408','866100000000409']}},
                    cell:{card:1,ts:1,cfun:1,reg:{stat:1},ident:{imei:'866100000000409'}}};
                window.paint=()=>app.update(JSON.parse(JSON.stringify(snapshot)));
                paint();
            }''', [(args.resources/'mu300/common.js').read_text(encoding='utf-8'),
                   (args.resources/'view/mu300/home.js').read_text(encoding='utf-8'), language, dark])
            assert page.locator('#mud-imei1').inner_text() == '8661****408'
            assert page.locator('#mud-imei2').inner_text() == '8661****409'
            assert page.locator('#mud-imei-active1').is_visible()
            assert not page.locator('#mud-imei-active2').is_visible()
            assert active in page.locator('#mud-imei-active1').inner_text()
            assert page.locator('#mud-roaming').inner_text() == no
            if args.screenshots and language == 'zh':
                page.screenshot(path=str(args.screenshots/('dark.png' if dark else 'light.png')), full_page=True)
            # NR-only roaming must win over the nonregistered LTE snapshot.
            page.evaluate("snapshot.cell.reg={stat:2};snapshot.cell.reg5g={stat:5};paint()")
            assert page.locator('#mud-roaming').inner_text() == yes
            # No slow-tier timestamp change: equipment and active-slot repaint anyway.
            page.evaluate('snapshot.info.modem.data_sim=1;app.identShown=true;paint()')
            assert page.locator('#mud-imei2').inner_text() == '866100000000409'
            assert not page.locator('#mud-imei-active1').is_visible()
            assert page.locator('#mud-imei-active2').is_visible()
            page.evaluate('snapshot.cell.reg=null;snapshot.cell.reg5g=null;snapshot.info.modem.data_sim=null;paint()')
            assert page.locator('#mud-roaming').inner_text() == '--'
            assert not page.locator('#mud-imei-active2').is_visible()
            # A missing SIM must not erase hardware identity or retain roaming.
            page.evaluate("snapshot.info.modem.sims=[{slot:0,state:'ready'},{slot:1,state:'absent'}];paint()")
            assert page.locator('#mud-roaming').inner_text() == no
            assert page.locator('#mud-imei2').inner_text() == '866100000000409'
            # Chip identity may complete while the other card is being viewed.
            page.evaluate('snapshot.info.modem.imeis=[null,null];app.identShown=false;paint()')
            assert page.locator('#mud-imei1').inner_text() == '--'
            page.evaluate("snapshot.info.modem.imeis=['866100000000408','866100000000409'];paint()")
            assert page.locator('#mud-imei1').inner_text() == '8661****408'
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'mobile overflow'
            assert not errors, errors
            page.close()
    browser.close()
print('PASS: dual IMEI, masking, actual data SIM, roaming, missing SIM, three languages and both themes')
