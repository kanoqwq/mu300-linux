"""Real SIM page: V50 board-specific sources, confirmations and theme/locale layout."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
JS=TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources'
COMMON=(JS/'mu300/common.js').read_text(encoding='utf-8')
SIM=(JS/'view/mu300/sim.js').read_text(encoding='utf-8')

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    count=0
    for theme in ('aurora','bootstrap'):
        for lang in ('zh-cn','en','tr'):
            for width in (360,1280):
                for board,cards in ((0,[0,1,2]),(1,[0,1,2,3]),(2,[0,1,3]),(3,[0,1])):
                    page=browser.new_page(viewport={'width':width,'height':1100})
                    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.set_content('<html><head></head><body><main id="page"></main></body></html>')
                    page.add_style_tag(content=('html{--surface:#20252d;--text:#eef2f6;--text-muted:#abb6c4;--hairline:#424955;--brand:#50c4b5}' if theme=='aurora' else
                        'html{--background-color-high:#fff;--background-color-low:#f0f2f4;--text-color-high:#20252d;--text-color-medium:#626b78;--border-color-low:#ddd;--primary-color-high:#3a80bd}')+
                        '*{box-sizing:border-box}body{margin:0;font:15px Arial,sans-serif}main{max-width:1100px;margin:0 auto;padding:12px}select{padding:9px}button{padding:10px}')
                    page.evaluate('''({common,source,lang,board,cards})=>{
                        window._=s=>s;window.L={env:{lang}};window.requests=[];window.confirmed=true;window.failJob=false;
                        window.physical={ok:1,supported:true,ready:true,board_id:board,cards,model:'v50',
                            active_card:0,saved_card:0,logical_cards:[0,1],saved_routes:[null,null]};
                        const rpc={declare:o=>(...args)=>{
                            if(o.method==='v50_sim_get')return Promise.resolve({...physical});
                            if(o.method==='data_sim_get')return Promise.resolve({ok:1,ready:true,actual_sim:0,saved_sim:0});
                            if(o.method==='data_sim_set'){
                                requests.push(args[0]);
                                if(!failJob&&args[0].startsWith('v50-pair-')){
                                    const pair=args[0].slice(9).split('-').map(Number);
                                    physical.saved_routes=pair;physical.logical_cards=pair;physical.active_card=pair[0];
                                }
                                return Promise.resolve({ok:1,id:'abcdefghij'});
                            }
                            if(o.method==='data_sim_status')return Promise.resolve({id:'abcdefghij',state:failJob?'error':'done',ok:!failJob,error:'v50_card_power_failed'});
                            return Promise.resolve({slots:2});
                        }};
                        window.M=new Function('rpc','baseclass','L',common)(rpc,{extend:x=>x},L);
                        M.confirmBox=()=>Promise.resolve(confirmed);M.toast=()=>({close(){}});M.selectedDuringTest='0';
                        M.selectViewedSlot=s=>{M.selectedDuringTest=s;};
                        window.view=new Function('view','rpc','poll','M','SimRadio',source)(
                            {extend:x=>x},rpc,{add(){},remove(){}},M,{attach:()=>({refresh:()=>Promise.resolve(),dispose(){}})});
                        document.querySelector('#page').append(view.render([{slots:2},{ok:1,ready:true,actual_sim:0,saved_sim:0},{...physical}]));
                    }''',dict(common=COMMON,source=SIM,lang=lang,board=board,cards=cards))
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(theme,lang,width,board)
                    if lang!='zh-cn':
                        import re
                        assert not re.search(r'[\u4e00-\u9fff]',page.locator('#page').inner_text()),(lang,board)
                    if board==3:
                        assert page.locator('#mud-v50-pair-apply').count()==0
                    else:
                        first=page.locator('#mud-v50-source0');second=page.locator('#mud-v50-source1');apply=page.locator('#mud-v50-pair-apply')
                        assert first.locator('option').evaluate_all('(a)=>a.map(o=>Number(o.value))')==[n for n in cards if n in (0,3)]
                        assert second.locator('option').evaluate_all('(a)=>a.map(o=>Number(o.value))')==[n for n in cards if n in (1,2)]
                        a=3 if 3 in cards else 0;b=2 if 2 in cards else 1
                        first.select_option(str(a));second.select_option(str(b))
                        # A background refresh must not overwrite an unsaved choice.
                        page.evaluate('view.refresh()');assert first.input_value()==str(a);assert second.input_value()==str(b)
                        page.evaluate('confirmed=false');apply.click();page.wait_for_function('!view.busy')
                        assert page.evaluate('requests.length')==0
                        page.evaluate('confirmed=true');first.select_option(str(a));second.select_option(str(b))
                        apply.click();page.wait_for_function('!view.busy&&!document.querySelector("#mud-v50-pair-apply").disabled')
                        assert page.evaluate('requests')==[f'v50-pair-{a}-{b}'],(theme,lang,width,board,page.evaluate('requests'))
                        assert page.evaluate('physical.saved_routes')==[a,b]
                        assert page.evaluate('M.selectedDuringTest')=='0','source-only change must not force the viewed/data SIM'
                        page.evaluate('failJob=true');apply.click();page.wait_for_function('!view.busy&&!document.querySelector("#mud-v50-pair-apply").disabled')
                        assert first.is_enabled() and second.is_enabled()
                        page.evaluate('physical.ready=false;view.refresh()');page.wait_for_function('document.querySelector("#mud-v50-pair-apply").disabled')
                    assert not errors,errors
                    page.evaluate('view.unload()');page.close();count+=1
    browser.close()
print(f'PASS {count} V50 source page cases: board maps, locales, themes, widths, cancel/error, refresh and readiness')
