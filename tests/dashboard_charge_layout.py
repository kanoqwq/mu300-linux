"""Render the real device page and charging card on mobile and desktop."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT/'openwrt/luci-app-mu300/htdocs/luci-static/resources'
sources = {name: (JS/path).read_text(encoding='utf-8') for name, path in {
    'common':'mu300/common.js', 'network':'mu300/network-performance.js', 'charge':'mu300/charge.js', 'device':'view/mu300/device.js', 'battery':'view/mu300/battery.js'}.items()}
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    for lang in ('zh-cn', 'en', 'tr'):
        for dark in (False, True):
            page = browser.new_page(viewport={'width':375 if dark else 1100, 'height':900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content('<main></main>')
            page.evaluate('''({sources,lang,dark})=>{
                window.L={env:{lang},resolveDefault:(p,d)=>Promise.resolve(p).catch(()=>d)};
                document.documentElement.lang=lang;
                document.documentElement.style.cssText=dark
                    ? '--surface:#20232b;--text:#eee;--text-muted:#aaa;--hairline:#454956;color:#eee;background:#11141a'
                    : '--surface:#fff;--text:#222;--text-muted:#666;--hairline:#ddd;color:#222;background:#f3f4f6';
                document.body.style.cssText='font-family:sans-serif;margin:16px';
                window.saved=[];
                window.chargeState={ok:true,available:true,supported:true,capacity:76,status:'Charging',online:true,
                    start:70,stop:80,enabled:true,power_on:true,full_once:false};
                const rpc={declare:info=>(...args)=>{
                    if(info.object==='unisoc-network-performance')return Promise.resolve({ok:true,supported:true,available:['cubic','bbr'],current:'cubic',qdisc:'pfifo_fast',selection:'default',defaults:{algorithm:'cubic'}});
                    if(info.object==='unisoc-battery-history')return Promise.resolve({present:false});
                    if(info.object==='mu300-charge') {
                        if(info.method==='configure'){
                            saved.push(args);Object.assign(chargeState,{enabled:args[0]==='1',start:+args[1],stop:+args[2],power_on:args[3]==='1'});
                        }
                        if(info.method==='full_once')chargeState.full_once=true;
                        if(info.method==='pause')chargeState.paused=true;
                        if(info.method==='resume')chargeState.paused=false;
                        return Promise.resolve({...chargeState});
                    }
                    return Promise.resolve({ok:1,devices:[],net_current:'ncm',role:'device'});
                }};
                const baseclass={extend:x=>x};
                const M=new Function('baseclass','rpc','L',sources.common)(baseclass,rpc,L);
                const Charge=new Function('baseclass','rpc','M',sources.charge)(baseclass,rpc,M);
                const NetworkPerformance=new Function('baseclass','rpc','M',sources.network)(baseclass,rpc,M);
                const app=new Function('view','rpc','poll','M','Charge',sources.battery)({extend:x=>x},rpc,{add(){},remove(){}},M,Charge);
                document.querySelector('main').appendChild(app.render({ok:1,role:'device',net_current:'ncm'}));
            }''', dict(sources=sources, lang=lang, dark=dark))
            save = page.locator('[data-charge="save"]')
            save.wait_for()
            assert page.locator('.mud-charge-card').evaluate('el => el === el.parentNode.lastElementChild')
            page.wait_for_function('!document.querySelector(\'[data-charge="save"]\').disabled')
            page.locator('#mud-charge-stop').fill('90')
            page.locator('#mud-charge-start').fill('60')
            save.click()
            page.wait_for_function('saved.length===1')
            assert page.evaluate('saved') == [['1','60','90','1']]
            page.locator('[data-charge="once"]').click()
            page.wait_for_function('document.querySelector(\'[data-charge="once"]\').disabled')
            assert page.locator('[data-charge="capacity"]').inner_text() == '76%'
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (lang, dark)
            if lang == 'en':
                assert page.locator('[data-charge="once"]').inner_text() == 'Charging to full once…'
            page.locator('[data-charge="pause"]').click()
            page.wait_for_function('chargeState.paused===true')
            page.locator('[data-charge="pause"]').click()
            page.wait_for_function('chargeState.paused===false')
            assert not errors, errors
            page.close()
    browser.close()
print('PASS: actual battery page, charge RPCs, mobile/desktop layout, light/dark colors, Chinese/English/Turkish')
