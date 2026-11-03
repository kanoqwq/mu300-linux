"""Actual LuCI battery view: themes, languages, mobile, RPC races and controls."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=next(p for p in Path(__file__).resolve().parents if (p/'tools/build-release.py').is_file())
PLUGIN='luci-app-t551m' if (TOP/'openwrt/luci-app-t551m').is_dir() else 'luci-app-mu300'
JS=TOP/'openwrt'/PLUGIN/'htdocs/luci-static/resources'
sources={k:(JS/p).read_text(encoding='utf8') for k,p in {
    'common':'mu300/common.js','charge':'mu300/charge.js','battery':'view/mu300/battery.js'}.items()}
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    for theme in ('aurora','bootstrap'):
        for dark in (False,True):
            for lang in ('zh-cn','en','tr'):
                for width in (360,1280):
                    page=browser.new_page(viewport={'width':width,'height':1100})
                    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    page.set_content('<main></main>')
                    page.evaluate(r'''({sources,theme,dark,lang})=>{
                        window.L={env:{lang},resolveDefault:(p,d)=>p.catch(()=>d)};
                        document.documentElement.lang=lang;
                        const surface=dark?'#202329':'#fff',text=dark?'#eee':'#202329',muted=dark?'#a5a9b0':'#626874',border=dark?'#383c44':'#dce0e6';
                        const tokens=theme==='aurora'?{'surface':surface,'surface-sunken':dark?'#15171b':'#f4f5f7','text':text,'text-muted':muted,'hairline':border,'brand':'#22a474','on-brand':'#fff'}:{'background-color-high':surface,'background-color-low':dark?'#15171b':'#f4f5f7','text-color-high':text,'text-color-medium':muted,'text-color-low':muted,'border-color-low':border,'primary-color-high':'#22a474','on-primary-color':'#fff'};
                        document.documentElement.style.cssText=Object.entries(tokens).map(([k,v])=>'--'+k+':'+v).join(';');
                        document.body.style.cssText='font-family:Arial,sans-serif;margin:16px;background:'+(dark?'#15171b':'#f4f5f7')+';color:'+text;
                        window.calls=[];window.saves=[];window.fail=false;window.deferred=null;window.present=true;
                        window.charging={ok:true,supported:true,available:true,capacity:76,status:'Charging',online:true,start:70,stop:80,enabled:true,power_on:false,full_once:false};
                        window.sample=range=>({range,found:true,first:100,now:1793712600,t0:1793705400,t1:1793712600,step:60,
                            pct:Array.from({length:120},(_,i)=>i>45&&i<51?null:85-Math.floor(i/10)),
                            mw:Array.from({length:120},(_,i)=>i>45&&i<51?null:i<30?null:2500),
                            st:Array.from({length:120},(_,i)=>i>45&&i<51?null:i<30?'C':'D'),
                            current:{pct:76,mv:3940,ma:-650,mw:-2561,temp:300,online:false,st:'D'},
                            stats:{on_battery_s:4200},estimate_s:18000,present});
                        const rpc={declare:o=>(...a)=>{
                            calls.push([o.object,o.method,a]);
                            if(o.object==='unisoc-battery-history'){
                                if(fail)return Promise.reject(new Error('raw backend exception'));
                                if(deferred)return new Promise(resolve=>{window.release=()=>resolve(sample(a[0]));});
                                return Promise.resolve(sample(a[0]));
                            }
                            if((o.object==='mu300-charge'||o.object==='e5-charge')){
                                if(o.method==='configure'){saves.push(a);Object.assign(charging,{enabled:a[0]==='1',start:+a[1],stop:+a[2],power_on:a[3]==='1'});}
                                if(o.method==='pause')charging.paused=true;
                                if(o.method==='resume')charging.paused=false;
                                if(o.method==='full_once')charging.full_once=true;
                                return Promise.resolve({...charging});
                            }
                            return Promise.resolve({});
                        }};
                        const baseclass={extend:x=>x};const M=new Function('baseclass','rpc','L',sources.common)(baseclass,rpc,L);
                        const Charge=new Function('baseclass','rpc','M',sources.charge)(baseclass,rpc,M);
                        window.polls=[];window.removed=[];const poll={add:(fn)=>polls.push(fn),remove:fn=>removed.push(fn)};
                        window.app=new Function('view','rpc','poll','M','Charge',sources.battery)({extend:x=>x},rpc,poll,M,Charge);
                        document.querySelector('main').appendChild(app.render());
                    }''',dict(sources=sources,theme=theme,dark=dark,lang=lang))
                    page.wait_for_function("document.querySelector('.mud-battery-status').getAttribute('aria-busy')==='false'")
                    assert page.locator('.mud-battery-grid>.mud-card').count()==4
                    assert page.locator('.mud-battery h2').inner_text()=={'zh-cn':'电池与电量','en':'Battery & power','tr':'Pil ve güç'}[lang]
                    assert page.locator('.mud-charge-card').evaluate('e=>e===e.parentNode.lastElementChild')
                    assert page.locator('.charge-line').count()>=2, 'missing-data gap must split the line'
                    assert page.locator('.mud-battery-meter>span').evaluate('e=>e.style.width')=='76%'
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(theme,dark,lang,width)
                    assert page.locator('.mud-battery-chart svg text').first.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=10
                    for key in ('charge','24h','7d','full'):
                        page.locator('[data-range="'+key+'"]').click()
                        page.wait_for_function('app._pending===null')
                        assert page.evaluate('calls.filter(x=>x[0]==="unisoc-battery-history").at(-1)[2][0]')==key
                    page.locator('.mud-battery-chart svg').focus();page.keyboard.press('ArrowRight')
                    assert '%' in page.locator('.mud-battery-tip').inner_text()
                    page.locator('#mud-charge-stop').fill('90');page.locator('#mud-charge-start').fill('60');page.locator('[data-charge="save"]').click()
                    page.wait_for_function('saves.length===1');assert page.evaluate('saves[0]')==['1','60','90','0']
                    if (theme,dark,lang,width)==('aurora',False,'zh-cn',1280):
                        out=TOP/'out/battery-preview';out.mkdir(parents=True,exist_ok=True)
                        page.screenshot(path=str(out/'desktop.png'),full_page=True)
                    if (theme,dark,lang,width)==('aurora',True,'zh-cn',360):
                        page.screenshot(path=str(TOP/'out/battery-preview/mobile.png'),full_page=True)
                    # Failed refresh is semantic; range switches never paint an older response.
                    page.evaluate('fail=true;app.refresh()');page.wait_for_function('app._pending===null')
                    assert 'raw backend' not in page.locator('.mud-battery-status').inner_text()
                    page.evaluate('()=>{fail=false;deferred=true;app.refresh();}')
                    page.locator('[data-range="7d"]').click()
                    page.evaluate('deferred=null;release()');page.wait_for_function('app._pending===null')
                    assert page.evaluate('calls.filter(x=>x[0]==="unisoc-battery-history").at(-1)[2][0]')=='7d'
                    page.evaluate('present=false;app.refresh()');page.wait_for_function('app._pending===null')
                    assert page.locator('.mud-battery-chart svg').count()==0
                    page.evaluate('app.unload()');assert page.evaluate('removed.length===1')
                    assert not errors,errors
                    page.close()
    browser.close()
print('PASS: 24 actual battery page layouts, chart gaps/ranges, lifecycle, RPC races and charging controls')
