"""Exercise the real product-form component on mobile/desktop, without a modem."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
SOURCE=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/product-form.js').read_text(encoding='utf-8')
LAYOUTS={'u30pro':[[0,1,2,3],[0,1,2],[0,1],[0,1,3],[0,1,2,3],[3],[0,2]],
         'f50pro':[[0,1,2],[0,1],[0,2],[0,2,3,4],[0,1,3,4],[3,4]],
         'v50':[[0,1,2],[0,1,2,3],[0,1,3],[0,1]]}


def mount(page,model,lang,writable=True):
    options=[{'value':str(i),'cards':cards} for i,cards in enumerate(LAYOUTS[model])]
    page.evaluate('''([source,model,options,lang,writable])=>{
      window.calls=[];window.confirmed=true;window.notices=[];window.failSet=false;
      window.state={ok:1,supported:true,model,detected_board_id:1,mode:'auto',active_mode:'auto',effective_board_id:1,pending_reboot:false,options};
      const rpc={declare:o=>async(...args)=>{calls.push([o.method,args]);if(o.method==='get')return {...state};
        if(failSet)return {ok:0,error:'product_form_unsaved'};
        state={...state,mode:args[1],pending_reboot:args[1]!==state.active_mode};return {...state};}};
      window.L={hasViewPermission:()=>writable};
      const M={uiLanguage:()=>lang,injectCss:()=>{},confirmBox:async()=>confirmed,toast:(...x)=>notices.push(x)};
      const component=new Function('baseclass','rpc','M',source)({extend:x=>x},rpc,M);
      document.body.replaceChildren(component.render(state));
    }''',[SOURCE,model,options,lang,writable])


with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    count=0
    for model in LAYOUTS:
      for lang in ('zh','en','tr'):
       for dark in (False,True):
        for width in (320,390,768,1440):
          page=browser.new_page(viewport={'width':width,'height':850})
          errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
          page.route('http://product-form.test/**',lambda route:route.fulfill(body='<!doctype html><html><body></body></html>',content_type='text/html'))
          page.goto('http://product-form.test/')
          page.add_style_tag(content='''*{box-sizing:border-box}body{margin:0;padding:12px;font:15px Arial,sans-serif}
            .mud-card{border:1px solid var(--hairline);border-radius:12px}.mud-btn{padding:10px 14px;line-height:1.4;font:inherit;border:1px solid var(--hairline);border-radius:8px}
            .mud-note{font-size:14px}'''+('html{--surface:#202329;--text:#eee;--hairline:#383c44}' if dark else 'html{--surface:#fff;--text:#202329;--hairline:#dce0e6}'))
          mount(page,model,lang)
          assert page.locator('select option').count()==len(LAYOUTS[model])+1
          assert page.locator('select').input_value()=='auto'
          assert page.locator('button').nth(0).is_disabled()
          assert page.locator('button').nth(1).is_disabled()
          page.locator('select').select_option('2')
          page.locator('button').nth(0).click()
          page.wait_for_function('state.mode==="2" && !document.querySelector("select").disabled')
          assert page.evaluate('calls.filter(c=>c[0]==="set")[0][1]')==[model,'2']
          assert page.locator('.pf-status').inner_text()
          page.locator('button').nth(1).click()
          page.wait_for_function('state.mode==="auto" && !document.querySelector("select").disabled')
          assert page.locator('button').nth(1).is_disabled()
          assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(model,lang,dark,width)
          for button in page.locator('button').all():
            assert button.evaluate('(b)=>b.scrollWidth<=b.clientWidth+1'),(model,lang,width,button.inner_text())
          assert not errors,errors
          page.close();count+=1
    page=browser.new_page();page.goto('about:blank')
    mount(page,'f50pro','en',False)
    assert page.locator('select').is_disabled()
    assert page.locator('button').nth(0).is_disabled()
    assert page.locator('button').nth(1).is_disabled()
    assert not page.locator('button').nth(2).is_disabled()
    mount(page,'v50','zh');page.evaluate('confirmed=false')
    page.locator('select').select_option('2');page.locator('button').nth(0).click()
    page.wait_for_function('!document.querySelector("select").disabled')
    assert not page.evaluate('calls.some(c=>c[0]==="set")')
    page.evaluate('confirmed=true;failSet=true');page.locator('button').nth(0).click()
    page.wait_for_function('notices.length>0')
    assert '其他待提交' in page.locator('.pf-status').inner_text()
    assert page.evaluate('state.mode')=='auto'
    browser.close()
print(f'PASS product form: {count} model/language/theme/viewport cases, permissions, confirmation cancellation and failed save')
