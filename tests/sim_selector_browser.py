"""Hero and original selector layout/interaction; RPC is mocked, no device writes."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
SOURCE=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text(encoding='utf-8')
HOME=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/home.js').read_text(encoding='utf-8')

def mount(page,lang='zh-cn',slots=2):
    page.evaluate('''([source,home,lang,slots])=>{
        window._=s=>s;window.L={env:{lang}};window.slots=slots;
        window.rpcCalls=[];
        const rpc={declare:options=>(...args)=>{
            rpcCalls.push([options.method,args]);
            sessionStorage.setItem('selector-rpc',JSON.stringify(rpcCalls));
            return Promise.resolve({slots:window.slots});
        }};
        window.M=new Function('rpc','baseclass','L',source)(rpc,{extend:x=>x},L);
        M.injectCss();
        const v=new Function('view','uci','R','M','SimRadio',home)({extend:x=>x},{},{},M,{});
        document.querySelector('.mud').innerHTML=v.html();
        document.querySelector('#mud-host').textContent='U30Pro';document.querySelector('#mud-uptime').textContent='Used 12 min';
        M.simSelector(document.querySelector('.mud'),'hero');
    }''',[SOURCE,HOME,lang,slots])

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    count=0
    for theme in ('aurora','bootstrap'):
        for dark in (False,True):
            for width in (320,390,768,1280,1920):
                for lang in ('zh-cn','en','tr'):
                    page=browser.new_page(viewport={'width':width,'height':720})
                    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.route('http://selector.test/**',lambda r:r.fulfill(content_type='text/html',body='''<!doctype html><html><head></head><body>
<header><button class="navigation-toggle">☰</button><a class="brand" href="#">U30Pro</a><nav>Navigation</nav><button class="search">⌕</button></header>
<main id="view"><section class="mud"><article class="hero"><h1>Dashboard</h1><p>Content stays in place.</p></article></section></main>
</body></html>'''))
                    page.goto('http://selector.test/')
                    surface,text,border=('#202329','#eee','#383c44') if dark else ('#fff','#202329','#dce0e6')
                    tokens={'surface':surface,'text':text,'hairline':border,'brand':'#447bee'} if theme=='aurora' else {
                        'background-color-high':surface,'background-color-low':surface,'text-color-high':text,
                        'text-color-medium':text,'border-color-low':border,'primary-color-high':'#447bee'}
                    page.add_style_tag(content='html{'+''.join(f'--{k}:{v};' for k,v in tokens.items())+'''}
*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;min-height:2000px}
header{height:56px;position:sticky;top:0;display:flex;align-items:center;padding:0 24px;gap:12px;background:''' +surface+'''}
.brand{font-size:24px;font-weight:700;color:inherit;text-decoration:none}nav{margin-left:110px;flex:1}.navigation-toggle{display:none}
main{margin:8px auto;max-width:1280px;padding:0 16px}.hero{height:180px;padding:16px;border:1px solid #888}h1{margin:0}
@media(max-width:600px){header{padding:0 16px}.navigation-toggle{display:block}.brand{flex:1}nav{display:none}}
''')
                    mount(page,lang)
                    pill=page.locator('.mud-sim-selector');pill.wait_for()
                    assert pill.locator('summary').inner_text()=='SIM 1'
                    assert pill.locator('summary').get_attribute('aria-label')
                    assert pill.evaluate('(e)=>e.parentElement.className')=='mud-hero-meta'
                    assert pill.evaluate('(e)=>getComputedStyle(e).position')!='fixed'
                    closed=pill.bounding_box();hero=page.locator('.mud-hero').bounding_box()
                    if width<=600:
                        assert abs(closed['x']+closed['width']-(hero['x']+hero['width']-15))<3
                        assert abs(closed['y']-(hero['y']+13))<3
                    else:
                        uptime=page.locator('#mud-uptime').bounding_box()
                        assert closed['x']>=uptime['x']+uptime['width']
                        assert abs(closed['y']-uptime['y'])<12
                    pill.locator('summary').click()
                    popup=pill.locator('.mud-sim-options').bounding_box()
                    assert popup['x']>=0 and popup['x']+popup['width']<=width,(width,popup)
                    page.keyboard.press('Escape');assert not pill.evaluate('(e)=>e.open')
                    assert pill.locator('summary').evaluate('(e)=>e===document.activeElement')
                    # Header/theme changes cannot move the control out of its own hero.
                    page.evaluate('document.querySelector("header").style.height="96px"')
                    assert pill.evaluate('(e)=>!!e.closest(".mud-hero")')
                    page.evaluate('scrollTo(0,300)');page.wait_for_timeout(30)
                    assert pill.bounding_box()['y']<0
                    page.evaluate('scrollTo(0,0)')
                    page.evaluate('M.simSelector(document.querySelector(".mud"),"hero");M.simSelector(document.querySelector(".mud"),"hero")')
                    page.wait_for_function('document.querySelectorAll(".mud-sim-selector").length===1')
                    # Other pages retain the original full card and caption.
                    page.evaluate('M.simSelector(document.querySelector(".mud"))')
                    page.locator('.mud-card.mud-sim-selector').wait_for()
                    assert not page.locator('.mud-sim-hero').count()
                    assert pill.evaluate('(e)=>e===e.parentElement.firstElementChild')
                    assert pill.evaluate('(e)=>getComputedStyle(e).position')=='static'
                    assert pill.locator('summary').inner_text()!='SIM 1'
                    page.evaluate('document.querySelector(".mud").remove()')
                    page.wait_for_function('!document.querySelector(".mud-sim-selector")')
                    page.evaluate('document.querySelector("main").innerHTML="<section class=mud></section>"')
                    page.evaluate('M.simSelector(document.querySelector(".mud"))')
                    pill.wait_for();pill.locator('summary').click()
                    with page.expect_navigation():pill.locator('select').select_option('1')
                    assert page.evaluate('sessionStorage.getItem("mu300-view-slot")')=='1'
                    assert page.evaluate('JSON.parse(sessionStorage.getItem("selector-rpc")).every(x=>x[0]==="sim_get")')
                    assert not errors,errors
                    page.close();count+=1
    browser.close()
print(f'PASS {count} hero/original SIM selector layouts: themes/light/dark/mobile/desktop/zh/en/tr, hero anchoring, theme-header independence, mobile top-right, original other pages, popup bounds, Escape, cleanup and view-only selection')
