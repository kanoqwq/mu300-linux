"""Real LuCI modules, dialogs and DOM; all RPC replies are isolated fixtures."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def run(repo, browser, out, luci):
    app = 'luci-app-mu300' if (repo / 'openwrt/luci-app-mu300').exists() else 'luci-app-t551m'
    package = repo / 'openwrt' / app
    res = package / 'htdocs/luci-static/resources'
    sources = [(res / f).read_text(encoding='utf-8') for f in (
        'mu300/common.js', 'mu300/boot-controls.js', 'view/mu300/boot.js', 'view/mu300/home.js')]
    menu = json.loads((package / 'root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
    assert menu['admin/system/android']['action']['path'] == 'mu300/boot'
    if luci:
        page = browser.new_page()
        page.set_content('<html><body><main id="maincontent"></main></body></html>')
        page.add_script_tag(content=(luci.parent / 'cbi.js').read_text(encoding='utf-8'))
        page.add_script_tag(content=luci.read_text(encoding='utf-8'))
        page.evaluate('''async ([common,controls])=>{
            window.L=Object.create(LuCI.prototype);
            const modules={'mu300/common':common,'mu300/boot-controls':controls,
                rpc:"'use strict';'require baseclass';return baseclass.extend({declare:function(){return function(){return Promise.resolve({});};}});"};
            L.Request.get=url=>{const name=url.match(/\\/(mu300\\/(?:common|boot-controls)|rpc)\\.js/)[1];
                return Promise.resolve({ok:true,url,text:()=>modules[name]});};
            const boot=await L.require('mu300.boot-controls');
            if(typeof boot.bind!=='function'||typeof boot.temporary!=='function')throw Error('Invalid LuCI boot module');
        }''', sources[:2])
        page.close()
    cases = 0
    for width in (360, 1280):
        for lang in ('zh', 'en', 'tr'):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', lambda route: route.abort())
            page.set_content('<html><body><main id="app"></main></body></html>')
            page.add_style_tag(content='''html{--surface:#151922;--text:#eef2f7;--text-muted:#93a0b2;
                --hairline:#303641;--brand:#009e98;--radius-base:.5rem;background:#080b10;color:var(--text)}
                body{margin:0;padding:18px;font:14px sans-serif}main{max-width:1050px;margin:0 auto}''')
            page.evaluate('''async ([sources,lang])=>{
                const [common,controls,boot,home]=sources, base={extend:x=>x}, L={env:{lang}};
                window.calls=[]; window.state={boot:{temporary:true,can_set_default:true},capabilities:{dualboot:1}};
                const rpc={declare:spec=>(...args)=>{
                    calls.push({method:spec.method,args});
                    if(spec.method==='sysinfo')return Promise.resolve(structuredClone(state));
                    if(spec.method==='act')return new Promise((resolve,reject)=>{
                        window.finish=reply=>{if(reply.ok && args[0]==='os' && args[1]!=='android')
                            state.boot.temporary=args[1]==='linux-temporary';resolve(reply);};
                        window.fail=reject;
                    });
                    throw Error('Unexpected RPC: '+spec.method);
                }};
                window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);
                window.Boot=new Function('M','baseclass',controls)(M,base);
                window.view=new Function('view','M','Boot',boot)(base,M,Boot);
                window.home=new Function('view','uci','R','M','SimRadio','Boot','L',home)(base,{}, {},M,{},Boot,{url:(...x)=>'/'+x.join('/')});
                document.querySelector('#app').appendChild(await view.render());
            }''', [sources, lang])
            toggle = page.locator('#mud-boot-permanent')
            assert not toggle.is_checked() and not toggle.is_disabled()
            assert page.evaluate('calls.every(c=>c.method==="sysinfo")'), 'Boot page must not depend on a SIM/AT read'
            if out and lang == 'zh' and width == 1280:
                page.locator('#app').screenshot(path=str(out / (repo.name + '-boot.png')))
            # Round 1: actual shared dialogs and cancellation, including duplicate clicks.
            page.locator('#mud-btn-android').click()
            android_text = page.locator('.mud-dlg-msg').inner_text()
            assert android_text
            page.evaluate('document.querySelector("#mud-btn-reboot").onclick()')
            assert page.locator('.mud-dlg-wrap').count() == 1
            page.keyboard.press('Escape')
            page.wait_for_function('!view._quickPending')
            assert page.evaluate('calls.every(c=>c.method!=="act")')
            page.locator('#mud-btn-reboot').click()
            assert page.locator('.mud-dlg-wrap').count() == 1
            page.keyboard.press('Escape')
            page.wait_for_function('!view._quickPending')
            # Round 2: permanent and temporary round trips, with real readback after RPC.
            for wanted, arg in ((True, 'linux-default'), (False, 'linux-temporary')):
                toggle.evaluate('(e,v)=>{e.checked=v;e.dispatchEvent(new Event("change"));}', wanted)
                assert toggle.is_checked() != wanted, 'Do not show a new boot mode before acceptance'
                page.locator('.mud-dlg [data-r="1"]').click()
                page.wait_for_function('!!window.finish')
                assert toggle.is_disabled()
                assert page.evaluate('calls.filter(c=>c.method==="act").at(-1).args') == ['os', arg]
                page.evaluate('finish({ok:1}); window.finish=null')
                page.wait_for_function('!view._quickPending && !document.querySelector("#mud-boot-permanent").disabled')
                assert toggle.is_checked() == wanted
            # Round 3: backend error, transport error, then a working retry. No wedged controls.
            for failure in ('backend', 'transport'):
                toggle.evaluate('e=>{e.checked=true;e.dispatchEvent(new Event("change"));}')
                page.locator('.mud-dlg [data-r="1"]').click()
                page.wait_for_function('!!window.finish')
                page.evaluate('finish({ok:0,error:"boot control busy"})' if failure == 'backend' else 'fail(new Error("offline"))')
                page.evaluate('window.finish=null')
                page.wait_for_function('!view._quickPending && !document.querySelector("#mud-boot-permanent").disabled')
                assert not toggle.is_checked()
            # Round 4: capability loss, unknown state, return/focus readback, and unload.
            page.evaluate('state.boot.temporary=null;window.dispatchEvent(new Event("focus"))')
            page.wait_for_function('document.querySelector("#mud-boot-permanent").disabled')
            page.evaluate('state.boot.temporary=false;window.dispatchEvent(new Event("focus"))')
            page.wait_for_function('document.querySelector("#mud-boot-permanent").checked')
            assert not toggle.is_disabled()
            page.locator('#mud-btn-android').click()
            count = page.evaluate('calls.filter(c=>c.method==="act").length')
            page.evaluate('view.unload()')
            page.locator('.mud-dlg [data-r="1"]').click()
            page.wait_for_function('!view._quickPending')
            assert page.evaluate('calls.filter(c=>c.method==="act").length') == count
            assert page.evaluate('calls.every(c=>c.method==="sysinfo" || c.method==="act")')
            # Round 5: heading geometry across loading/idle/error; no added table row.
            page.evaluate('''()=>{document.querySelector('#app').innerHTML='<div class="mud">'+home.html()+'</div>';
                M.localize(document.querySelector('#app'));}''')
            if page.locator('#mud-slow-progress').count():
                positions = []
                for text in ('', '正在更新…', '读取失败，稍后重试', ''):
                    positions.append(page.evaluate('''text=>{
                        const p=document.querySelector('#mud-slow-progress');
                        p.textContent=M.translate(text);p.classList.toggle('is-loading',text==='正在更新…');
                        const h=p.parentElement.getBoundingClientRect(), r=p.getBoundingClientRect();
                        if(r.top<h.top || r.bottom>h.bottom+1)throw Error('Progress not inside heading');
                        const t=document.querySelector('#mud-neigh').getBoundingClientRect();return [t.y,t.height];
                    }''', text))
                assert all(p == positions[0] for p in positions), positions
            # Round 6: the two pages use the same Android confirmation and power choices.
            page.evaluate('''()=>{home.refreshLock=()=>{};home.wire(document.querySelector('#app>.mud'));
                document.querySelector('#mud-btn-android').onclick();}''')
            assert page.locator('.mud-dlg-msg').inner_text() == android_text
            page.keyboard.press('Escape')
            assert not errors, errors
            page.close()
            cases += 1
    print(f'{repo.name}: {cases} responsive/locale cases; six behavior rounds passed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', action='append', type=Path)
    parser.add_argument('--browser', required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--luci', type=Path, help='Delivered luci.js, for its actual module constructor checks')
    args = parser.parse_args()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.browser)
        for repo in args.repo or [next(p for p in Path(__file__).resolve().parents if (p / 'openwrt').is_dir())]:
            run(repo, browser, args.out, args.luci)
        browser.close()


if __name__ == '__main__':
    main()
