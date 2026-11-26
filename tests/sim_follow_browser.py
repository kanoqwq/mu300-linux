"""Real selector and scoped RPCs: external key switches must follow actual SIM."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
RES=TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources'
SOURCE=(RES/'mu300/common.js').read_text()
HOME=(RES/'view/mu300/home.js').read_text()
STATUS=HOME[HOME.index('var homeStatus=function()'):HOME.index('this._stopStatus =')]

with sync_playwright() as engine:
    browser=engine.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page=browser.new_page(viewport={'width':390,'height':844});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.route('http://follow.test/**',lambda r:r.fulfill(content_type='text/html',body='<main id="root" class="mud"><div class="mud-hero-meta"></div></main>'))
    page.goto('http://follow.test/')
    page.evaluate('''([source,status])=>{
      sessionStorage.setItem('mu300-view-slot','0'); // Legacy default is not a manual override.
      window.info={ok:1,slots:2,saved_sim:0,actual_sim:1,
        physical:{model:'v50',supported:true,cards:[0,1],logical_cards:[0,1]}};
      window.requests=[];window.lookups=0;window.pending=[];window.hold=false;window.fail=false;
      const rpc={declare:o=>(...args)=>{
        if(o.method==='sim_get'){
          lookups++;if(fail)return Promise.reject(Error('disconnected'));
          return hold?new Promise(r=>pending.push(r)):Promise.resolve(structuredClone(info));
        }
        requests.push([o.method,args]);
        return Promise.resolve({cell:{card:Number(args.at(-1))}});
      }};
      const L={env:{lang:'zh-cn'}};
      window.M=new Function('rpc','baseclass','L',source)(rpc,{extend:o=>o},L);
      window.homeStatus=new Function('M',status+'return homeStatus;')(M);
      M.simSelector(document.querySelector('#root'),'hero');
    }''',[SOURCE,STATUS])
    selector=page.locator('.mud-sim-selector');selector.wait_for()
    assert selector.locator('select').input_value()=='auto'
    assert selector.locator('option').all_text_contents()==['自动跟随上网卡','内置 SIM 1','内置 SIM 2']
    assert selector.locator('summary').inner_text()=='内置 SIM 2'
    assert page.evaluate('M.selectedSlot()')=='1'
    # The actual card wins over an unchanged next-boot preference.
    result=page.evaluate('async()=>{let n=lookups;let r=await homeStatus();return [r.cell.card,lookups-n];}')
    assert result==[1,1],result
    page.evaluate('info.actual_sim=0')
    assert page.evaluate('async()=>(await homeStatus()).cell.card')==0
    assert selector.locator('summary').inner_text()=='内置 SIM 1'
    # Explicit manual viewing survives subsequent external card switches.
    page.evaluate("M.selectViewedSlot('1');info.actual_sim=0")
    assert page.evaluate('async()=>(await homeStatus()).cell.card')==1
    assert selector.locator('select').input_value()=='1'
    page.evaluate("M.selectViewedSlot('auto');info.actual_sim=1")
    assert page.evaluate('async()=>(await homeStatus()).cell.card')==1
    assert selector.locator('select').input_value()=='auto'
    # Many simultaneous readers share one pending read and wait for the real slot.
    page.evaluate('''()=>{hold=true;requests=[];lookups=0;
      window.readers=Promise.all([homeStatus(),M.callSignal(),M.callCells()]);}''')
    assert page.evaluate('lookups')==1
    assert page.evaluate('requests.length')==0
    page.evaluate('''async()=>{hold=false;info.actual_sim=0;pending.shift()(structuredClone(info));await readers;}''')
    assert page.evaluate('requests.every(r=>r[1].at(-1)==="0")')
    # A failed lookup must not issue AT against the previous card; the next round recovers.
    page.evaluate('''async()=>{fail=true;requests=[];try{await M.callAt('AT+CFUN?');throw Error('unexpected success');}
      catch(e){if(e.message!=='disconnected')throw e;}}''')
    assert page.evaluate('requests.length')==0
    page.evaluate('fail=false;info.actual_sim=1')
    assert page.evaluate('async()=>(await homeStatus()).cell.card')==1
    # A write cannot silently move to a card selected after the form was shown.
    page.evaluate('info.actual_sim=0;requests=[]')
    assert page.evaluate("async()=>{try{await M.callLockSet('band','1');return false;}catch(e){return e.message.includes('查看卡槽已改变');}}")
    assert page.evaluate('requests.length')==0
    # F50/U30 Air use logical SIM names. V50 physical IDs never become SPACTCARD values.
    page.evaluate('info.actual_sim=1')
    for model in ('f50','u30air','mu300','v50'):
        page.evaluate('(model)=>{info.physical=model==="v50"?{model,supported:true,cards:[0,1,2,3],logical_cards:[3,2]}:{model,supported:false};}',model)
        assert page.evaluate('async()=>(await homeStatus()).cell.card')==1
        assert selector.locator('summary').inner_text()==('内置 SIM 3' if model=='v50' else 'SIM 2')
    # Repeated external switching uses the existing refresh lane, without navigation.
    assert page.evaluate('''async()=>{
      for(let n=0;n<250;n++){
        info.actual_sim=n%2;let r=await homeStatus();
        if(r.cell.card!==n%2||M.selectedSlot()!==String(n%2))return false;
      }return true;
    }''')
    assert page.url=='http://follow.test/'
    assert not errors,errors
    browser.close()
print('PASS auto/manual selection, legacy storage, actual vs saved, external key switches, concurrent first resolution, failure/recovery, model naming and 250 refresh rounds; no device access')
