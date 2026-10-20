"""Exercise the real manual cell form with isolated RPC; never sends modem commands."""
from pathlib import Path
import argparse
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('--repo',type=Path)
a=parser.parse_args()
repo=a.repo or next(p for p in Path(__file__).resolve().parents if (p/'openwrt').is_dir())
app='luci-app-t551m' if (repo/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
res=repo/'openwrt'/app/'htdocs/luci-static/resources'
common=(res/'mu300/common.js').read_text(encoding='utf8')
locks=(res/'view/mu300/locks.js').read_text(encoding='utf8')
cases=0
with sync_playwright() as p:
 browser=p.chromium.launch()
 for width in (320,1280):
  for lang in ('zh','en','tr'):
   page=browser.new_page(viewport={'width':width,'height':900})
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.set_content('<html><body><main id="app"></main></body></html>')
   page.evaluate('''([common,locks,lang])=>{
    const L={env:{lang},resolveDefault:p=>Promise.resolve(p).catch(()=>null)},base={extend:x=>x};
    window.M=new Function('rpc','baseclass','L',common)({declare:()=>()=>Promise.resolve({})},base,L);
    window.slot='0';window.requests=[];window.notes=[];
    M.simSlot=M.selectedSlot=()=>slot;M.simSelector=M.watchSms=()=>{};
    M.callLockSet=(kind,value)=>{requests.push({kind,value,slot});return new Promise(r=>window.finish=r);};
    window.view=new Function('view','poll','R','M','L','AutoMode',locks)(base,{add(){},remove(){}},{poll:()=>()=>{}},M,L,{attach:()=>({update(){},reload(){}})});
    view.refresh=view.loadServing=view.loadServingMeta=()=>Promise.resolve();
    view.note=(text,type)=>notes.push({text,type});
    view.readback=(r,btn)=>{M.busy(btn,false);view._applying=false;};
    document.querySelector('#app').append(view.render());
   }''',[common,locks,lang])
   page.locator('.mud-manual-cell summary').click()
   form=page.locator('#mud-lock-manual-form')
   if lang!='zh':assert not any('\u3400'<=c<='\u9fff' for c in form.inner_text())
   for selector in ('#mud-lock-manual-rat','#mud-lock-manual-frequency','#mud-lock-manual-pci','#mud-lock-manual-apply'):
    b=page.locator(selector).bounding_box();assert b and b['x']>=0 and b['x']+b['width']<=width+1,(width,selector,b)
   def fill(rat,freq,pci):
    page.locator('#mud-lock-manual-rat').select_option(rat)
    page.locator('#mud-lock-manual-frequency').fill(freq)
    page.locator('#mud-lock-manual-pci').fill(pci)
   for rat,freq,pci in [('lte','','1'),('lte','1',''),('lte','-1','0'),('lte','1e3','1'),('lte','1.5','0'),('lte','262144','0'),('lte','123','504'),('nr','0','0'),('nr','3279166','0'),('nr','1','1008')]:
    fill(rat,freq,pci);page.locator('#mud-lock-manual-apply').click()
    assert page.locator('.mud-dlg-wrap').count()==0
    assert page.evaluate('requests.length')==0
    assert page.evaluate("notes.at(-1).type")== 'error'
    cases+=1
   for rat,freq,pci in [('lte','0','0'),('lte','262143','503'),('nr','1','0'),('nr','3279165','1007'),('nr',' 0627264 ',' 0393 ')]:
    fill(rat,freq,pci);page.locator('#mud-lock-manual-apply').click()
    assert page.locator('.mud-dlg-wrap').count()==1
    assert page.evaluate('requests.length')==0
    page.evaluate("document.querySelector('#mud-lock-manual-form').dispatchEvent(new Event('submit',{cancelable:true}))")
    assert page.locator('.mud-dlg-wrap').count()==1
    page.keyboard.press('Escape');page.wait_for_function('!view._manualConfirm')
    assert page.evaluate('requests.length')==0
    page.locator('#mud-lock-manual-apply').click();page.locator('.mud-dlg-btns [data-r="1"]').click()
    page.wait_for_function('requests.length===1')
    assert page.evaluate('requests[0]')=={'kind':'cell','value':rat+':'+str(int(freq))+','+str(int(pci)),'slot':'0'}
    page.evaluate("document.querySelector('#mud-lock-manual-form').dispatchEvent(new Event('submit',{cancelable:true}))");assert page.evaluate('requests.length')==1
    page.evaluate("finish({ok:1});requests=[]");page.wait_for_function('!view._applying')
    cases+=1
   fill('nr','627264','393')
   page.locator('#mud-lock-manual-apply').click();page.evaluate("slot='1'")
   page.locator('.mud-dlg-btns [data-r="1"]').click();page.wait_for_function('!view._manualConfirm')
   assert page.evaluate('requests.length')==0,'confirmation crossed SIM slots'
   page.locator('#mud-lock-manual-apply').click();page.locator('.mud-dlg-btns [data-r="1"]').click()
   page.wait_for_function('requests.length===1');assert page.evaluate('requests[0].slot')=='1'
   page.evaluate("finish({ok:1});requests=[]");page.wait_for_function('!view._applying')
   page.locator('#mud-lock-manual-apply').click();page.evaluate('view.unload()')
   page.locator('.mud-dlg-btns [data-r="1"]').click();page.wait_for_function('!view._manualConfirm')
   assert page.evaluate('requests.length')==0,'request after unload'
   assert not errors,errors
   page.close()
 browser.close()
print('PASS',repo.name,cases,'manual-cell validation/confirmation cases; both SIM slots, cancellation, duplicates, unload, translations and phone/desktop layout')
