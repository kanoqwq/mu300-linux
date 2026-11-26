"""The startup mode page refreshes once after automatic promotion, without writes."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

def check(repo,browser):
    app='luci-app-t551m' if (repo/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
    resources=repo/'openwrt'/app/'htdocs/luci-static/resources'
    sources=[(resources/p).read_text(encoding='utf-8') for p in ['mu300/common.js','mu300/boot-controls.js','view/mu300/boot.js']]
    for unload in [False,True]:
        page=browser.new_page();page.clock.install()
        page.set_content('<main id="app"></main>')
        page.evaluate('''async sources=>{
            window.calls=[];window.state={boot:{temporary:true,can_set_default:true},capabilities:{dualboot:1}};
            const rpc={declare:o=>(...args)=>{
                calls.push(o.method);if(o.method!=='sysinfo')throw Error('No writes allowed');
                return Promise.resolve(structuredClone(state));
            }},base={extend:x=>x},L={env:{lang:'zh'},url:(...x)=>'/'+x.join('/')};
            const M=new Function('rpc','baseclass','L',sources[0])(rpc,base,L);
            const Boot=new Function('M','baseclass',sources[1])(M,base);
            window.view=new Function('view','M','Boot',sources[2])(base,M,Boot);
            document.querySelector('#app').appendChild(await view.render());
        }''',sources)
        assert not page.locator('#mud-boot-permanent').is_checked()
        if unload:page.evaluate('view.unload()')
        page.evaluate('state.boot.temporary=false')
        page.clock.fast_forward(65000)
        assert page.locator('#mud-boot-permanent').is_checked()!=unload
        assert page.evaluate('calls.length')==(1 if unload else 2)
        page.clock.fast_forward(65000)
        assert page.evaluate('calls.length')==(1 if unload else 2),'Must be one deadline, not recurring polling'
        page.close()
    print(repo.name+': startup-mode automatic refresh, no writes and unload passed')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--repo',action='append',required=True,type=Path)
    args=parser.parse_args()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
        for repo in args.repo:check(repo,browser)
        browser.close()
