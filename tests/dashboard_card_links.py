"""Native dashboard links, battery gating and real browser navigation; offline."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, default=next(p for p in Path(__file__).resolve().parents if (p/'openwrt').is_dir()))
    args = parser.parse_args()
    app = 'luci-app-t551m' if (args.repository/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
    res = args.repository/'openwrt'/app/'htdocs/luci-static/resources'
    code = [(res/f).read_text(encoding='utf8') for f in ['mu300/common.js','view/mu300/home.js']]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
        for lang in ('zh','en','tr'):
            for width in (390,1280):
                page = browser.new_page(viewport={'width':width,'height':1000})
                errors = [];page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('http://dashboard.test/**',lambda r:r.fulfill(body='<main></main>',content_type='text/html'))

                def setup():
                    page.goto('http://dashboard.test/custom/luci/admin/home')
                    page.evaluate('''([common,home,lang])=>{
                        const base={extend:o=>o},L={env:{lang},url:(...p)=>'/custom/luci/'+p.join('/')};
                        window.rpcCalls=[];
                        const rpc={declare:o=>(...args)=>{rpcCalls.push(o.method);return Promise.resolve({});}};
                        window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);
                        window.app=new Function('view','uci','R','M','L','SimRadio',home)(base,{}, {},M,L,{});
                        const root=app._root=document.querySelector('main');root.className='mud';root.innerHTML=app.html();
                        document.documentElement.style.cssText='--surface:#141822;--text:#f9fafb;--brand:#00958e';
                        M.injectCss();app.wireCardLinks(root);
                        window.paint=power=>app.update({info:{power},cell:null});paint({present:0,usb:1});
                    }''',code+[lang])

                setup()
                assert page.locator('a[data-mud-page="traffic"][href]').count()==3
                assert page.locator('a[data-mud-page="cpu"][href]').count()==2
                # Unknown, absent and USB-only power must be inert; a subsequent
                # battery disappearance also removes the old navigation target.
                power=page.locator('#mud-power-card')
                for state in ({},{'present':False},{'present':0,'usb':1},{'present':'0','capacity':70}):
                    page.evaluate('paint',state)
                    assert power.get_attribute('href') is None
                    assert power.get_attribute('title') is None
                    assert power.evaluate('e=>e.tabIndex')==-1
                    assert power.evaluate('e=>getComputedStyle(e).cursor')=='default'
                    power.click()
                    assert page.url.endswith('/admin/home')
                for state in ({'present':1,'capacity':70},{'present':True,'capacity':None}):
                    page.evaluate('paint',state)
                    assert power.get_attribute('href')=='/custom/luci/admin/modem/battery'
                    assert power.evaluate('e=>e.tabIndex')==0
                    assert power.evaluate('e=>getComputedStyle(e).cursor')=='pointer'
                page.evaluate('paint({present:0,usb:1})')
                assert power.get_attribute('href') is None
                assert page.evaluate('rpcCalls')==[],'card wiring must not add modem RPCs'
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                # Verify native click/Enter navigation, including a nondefault
                # LuCI prefix, rather than only inspecting a click handler.
                for id,target in [('tr-day','traffic'),('tr-month','traffic'),('tr-left','traffic'),('cpu','cpu'),('ram','cpu')]:
                    setup()
                    card=page.locator('#mud-'+id).locator('..')
                    assert card.get_attribute('href')=='/custom/luci/admin/modem/'+target
                    if id=='ram':
                        card.focus();page.keyboard.press('Enter')
                    else:card.click()
                    page.wait_for_url('**/admin/modem/'+target)
                setup();page.evaluate('paint({present:1,capacity:70})')
                page.locator('#mud-power-card').click();page.wait_for_url('**/admin/modem/battery')
                assert not errors,errors
                page.close()
        browser.close()
    print('PASS dashboard links: traffic, CPU/memory, battery/USB-only transitions, native click/Enter, custom LuCI prefix, 3 languages, mobile/desktop')


if __name__=='__main__':main()
