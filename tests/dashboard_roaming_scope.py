"""Exercise the real common/widget async SIM binding; all RPCs are mocked."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP = Path(__file__).resolve().parents[1]
RES = TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources'
COMMON = (RES/'mu300/common.js').read_text(encoding='utf8')
WIDGET = (RES/'mu300/roaming.js').read_text(encoding='utf8')

def mount(page, *, hold=True, snapshot=None, choice=None, prefetch=False):
    page.set_content('<main class="mud" id="root"></main>')
    page.evaluate('''([common,widget,hold,snapshot,choice,prefetch])=>{
      const L={env:{lang:'zh-cn'},hasViewPermission:()=>true};
      const storage=new Map();if(choice!==null)storage.set('mu300-view-choice',choice);
      Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
        getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v))}});
      window.snapshot=snapshot;window.holdSim=hold;window.holdGet=false;window.holdSet=false;
      window.pendingSim=[];window.pendingGet=[];window.pendingSet=[];
      window.reads=[];window.writes=[];window.simReads=0;window.toasts=[];
      window.enabled={'0':0,'1':0};
      const rpc={declare:o=>(...args)=>{
        if(o.object==='mu300dash'&&o.method==='sim_get'){
          simReads++;return holdSim?new Promise(r=>pendingSim.push(r)):Promise.resolve({...snapshot});
        }
        if(o.object!=='unisoc-roaming')throw Error('Unexpected RPC '+o.object);
        const slot=String(args[0]);
        const reply=()=>({ok:1,slot:Number(slot),enabled:enabled[slot],roaming:0,blocked:0});
        if(o.method==='get'){
          reads.push(slot);return holdGet?new Promise(r=>pendingGet.push(()=>r(reply()))):Promise.resolve(reply());
        }
        if(o.method==='set'){
          writes.push(args);
          const apply=()=>{enabled[slot]=Number(args[1]);return reply();};
          return holdSet?new Promise(r=>pendingSet.push(()=>r(apply()))):Promise.resolve(apply());
        }
        throw Error('Unexpected method '+o.method);
      }};
      window.M=new Function('rpc','baseclass','L',common)(rpc,{extend:o=>o},L);
      M.toast=(...args)=>toasts.push(args);
      if(prefetch)M.callSimGet();
      const component=new Function('baseclass','rpc','M','L',widget)({extend:o=>o},rpc,M,L);
      window.instance=component.mount(document.querySelector('#root'));
    }''', [COMMON,WIDGET,hold,snapshot or {'slots':2,'actual_sim':1,'saved_sim':1},choice,prefetch])

def ready(page):
    page.wait_for_function('!document.querySelector("#mud-roaming-switch").disabled')

with sync_playwright() as engine:
    browser=engine.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    mount(page,prefetch=True)
    assert page.evaluate('reads.length')==0, 'Do not read the temporary SIM1 fallback'
    assert page.evaluate('simReads')==1, 'Share the in-flight automatic SIM resolution'
    assert page.get_by_role('switch').is_disabled()
    page.evaluate('holdSim=false;pendingSim.shift()({...snapshot})')
    ready(page)
    assert page.evaluate('reads')==['1']
    assert '查看卡槽已改变' not in page.locator('section').inner_text()
    page.get_by_role('switch').click()
    page.wait_for_function('writes.length===1&&!document.querySelector("#mud-roaming-switch").disabled')
    assert page.evaluate('writes')==[['1','1']]

    # A deliberate view change must still block a save of the old card's state.
    page.evaluate("M.selectViewedSlot('0')")
    page.get_by_role('switch').click()
    page.wait_for_function('document.querySelector("#mud-roaming-switch").disabled')
    assert page.evaluate('writes.length')==1
    assert '查看卡槽已改变' in page.locator('section').inner_text()
    page.locator('section button').first.click();ready(page)
    assert page.evaluate('reads.at(-1)')=='0'

    # Do not repaint a new card with a response requested for the previous one.
    page.evaluate('holdGet=true')
    page.locator('section button').first.click()
    page.wait_for_function('pendingGet.length===1')
    page.evaluate("M.selectViewedSlot('1');holdGet=false;pendingGet.shift()()")
    page.wait_for_function('document.querySelector("section").getAttribute("aria-busy")==="false"')
    assert page.get_by_role('switch').is_disabled()
    page.locator('section button').first.click();ready(page)
    assert page.get_by_role('switch').get_attribute('aria-checked')=='true'

    # An already-issued save stays explicitly addressed to its original card.
    page.evaluate('holdSet=true');page.get_by_role('switch').click()
    page.wait_for_function('pendingSet.length===1')
    page.evaluate("M.selectViewedSlot('0');holdSet=false;pendingSet.shift()()")
    page.wait_for_function('document.querySelector("section").getAttribute("aria-busy")==="false"')
    assert page.evaluate('writes')==[['1','1'],['1','0']]
    assert page.get_by_role('switch').is_disabled()
    page.locator('section button').first.click();ready(page)
    assert page.evaluate('reads.at(-1)')=='0'
    page.evaluate("M.selectViewedSlot('auto');snapshot={slots:2,actual_sim:1,saved_sim:1}")
    page.get_by_role('switch').click()
    page.wait_for_function('document.querySelector("section").getAttribute("aria-busy")==="false"')
    assert page.evaluate('writes.length')==2, 'Changed data SIM cannot inherit the displayed card preference'
    assert page.get_by_role('switch').is_disabled()
    assert not errors,errors
    page.close()

    for snapshot,choice,dispose in [({'slots':2,'actual_sim':None,'saved_sim':None},None,False),
                                    ({'slots':2,'actual_sim':1,'saved_sim':1},None,True),
                                    ({'slots':2,'actual_sim':0,'saved_sim':0},'1',False)]:
        page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        mount(page,hold=dispose,snapshot=snapshot,choice=choice)
        if dispose:
            page.evaluate('instance.dispose();holdSim=false;pendingSim.shift()({...snapshot})')
            page.wait_for_timeout(30)
            assert page.evaluate('reads.length+writes.length')==0
        elif choice is not None:
            ready(page);assert page.evaluate('reads')==['1']
        else:
            page.wait_for_function('document.querySelector("section").getAttribute("aria-busy")==="false"')
            assert page.evaluate('reads.length+writes.length')==0
            assert page.get_by_role('switch').is_disabled()
        assert not errors,errors
        page.close()
    browser.close()
print('PASS automatic F50 SIM resolution, shared pending lookup, correct write scope, real card-change guards, stale get/set replies, unknown SIM, explicit selection and disposal; no device access')
