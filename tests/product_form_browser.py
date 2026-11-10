"""Exercise the real product-form component on mobile/desktop, without a modem."""
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
SOURCE=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/product-form.js').read_text(encoding='utf-8')
SETTINGS=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/settings.js').read_text(encoding='utf-8')
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
      // Keep RPC/control tests independent of a theme or full LuCI loader.
      // The component uses LuCI's real Dropdown API, not private select styling.
      const ui={Dropdown:class{
        constructor(value,choices,options){this.value=value;this.choices=choices;this.options=options;}
        render(){this.node=document.createElement('select');this.node.className='cbi-dropdown';
          this.options.sort.forEach(value=>{const option=document.createElement('option');option.value=value;option.textContent=this.choices[value];this.node.append(option);});
          this.node.value=this.value;this.node.addEventListener('change',()=>this.node.dispatchEvent(new CustomEvent('widget-change',{bubbles:true})));return this.node;}
        getValue(){return this.node.value;}
        setValue(value){this.node.value=value;}
      }};
      const component=new Function('baseclass','rpc','M','ui',source)({extend:x=>x},rpc,M,ui);
      window.productComponent=component;
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
          assert page.locator('.pf-meta + .mud-note + .pf-row').count()==1
          assert '手动形态仅覆盖' not in page.locator('.mud-product-form').inner_text()
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
    for model in (*LAYOUTS,'f50','u30air','e5','unknown','unavailable'):
        mount(page,'u30pro' if model not in LAYOUTS else model,'zh')
        page.evaluate('''async ([source,model])=>{
          if(!['u30pro','f50pro','v50'].includes(model))
            state=model==='unavailable'?{ok:0,error:'product_form_unavailable'}:{ok:0,supported:false};
          const form={NamedSection:'section',DummyValue:'dummy',ListValue:'list',Value:'value',Flag:'flag'};
          window.formOptions=[];
          form.Map=function(){this.sections=[];this.section=(type,id,kind,title)=>{
            const node=document.createElement('section');node.className='platform-section';
            const heading=document.createElement('h3');heading.textContent=title;node.append(heading);
            this.sections.push(node);
            return {option:(type,key,label)=>{const option={type,key,label,value(){},depends(){}};
              formOptions.push(option);return option;}};};
            this.render=async()=>{const root=document.createElement('main');root.append(...this.sections);
              for(const option of formOptions)if(option.renderWidget){
                const row=document.createElement('div');row.className='platform-option';
                const label=document.createElement('label');label.textContent=option.label;
                row.append(label,option.renderWidget());this.sections[0].append(row);
              }return root;};};
          const view={extend:x=>x},uci={load:async()=>{},get:()=>null};
          const M={localizeMenu(){},translate:x=>x},R={seconds:()=>2};
          const settings=new Function('form','uci','R','view','M','P',source)(form,uci,R,view,M,productComponent);
          const loaded=await settings.load();document.body.replaceChildren(await settings.render(loaded));
        }''',[SETTINGS,model])
        supported=model in LAYOUTS or model=='unavailable'
        assert page.locator('.platform-section .mud-product-form').count()==int(supported),model
        assert page.locator('.mud-product-form > h3').count()==0,model
        if model in LAYOUTS:
            assert page.locator('.platform-option label').inner_text()=='产品形态'
            assert page.locator('select option').count()==len(LAYOUTS[model])+1
            assert page.locator('select').input_value()=='auto'
            assert page.evaluate('formOptions.find(o=>o.key==="_product_form").write()') is None
        elif model=='unavailable':
            assert '无法读取' in page.locator('.pf-status').inner_text()
    browser.close()
print(f'PASS product form: {count} model/language/theme/viewport cases, settings-section integration, unsupported hiding, read errors, permissions and save failures')
