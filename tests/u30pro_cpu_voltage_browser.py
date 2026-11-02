"""CPU voltage UI submits the hardware's domain count; no device RPC."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import re
TOP=Path(__file__).resolve().parents[1]
common=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text(encoding='utf8')
source=(TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/cpu.js').read_text(encoding='utf8')
with sync_playwright() as p:
    chrome=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
    browser=p.chromium.launch(headless=True,**({'executable_path':str(chrome)} if chrome.exists() else {}))
    for count in (2,3):
      for lang in ('zh-cn','en','tr'):
        for width in (360,1280):
            page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('http://u30pro.test/**',lambda r:r.fulfill(body='<html><body></body></html>',content_type='text/html'))
            page.goto('http://u30pro.test/')
            page.add_style_tag(content='html{--surface:#202329;--surface-sunken:#15171b;--text:#eee;--text-muted:#aaa;--hairline:#383c44;--brand:#447bee}body{margin:16px;background:#15171b;color:#eee;font-family:Arial}*{box-sizing:border-box}')
            page.evaluate(r"""([common,source,lang,count])=>{
                window.L={env:{lang}};window.requests=[];
                const data={policies:[],zones:[],voltage:{supported:true,domain_count:count,offsets:Array(count).fill(0),domains:Array.from({length:count},(_,id)=>({id,policy:'policy'+(id?6:0),cpus:id?'6 7':'0 1 2 3 4 5',offset:0,table:[{khz:768000,uv:750000},{khz:2041000,uv:800000}]}))}};
                const rpc={declare:o=>payload=>{
                    if(o.method==='cpu_voltage_save'){const input=JSON.parse(payload);requests.push(input);return Promise.resolve({...data.voltage,ok:1,offsets:input.offsets,pending:true});}
                    return Promise.resolve(data);
                }};
                const M=new Function('rpc','baseclass','L',common)(rpc,{extend:o=>o},L);
                window.view=new Function('view','rpc','poll','M','Cores','Idle',source)({extend:o=>o},rpc,{add(){},remove(){}},M,{render:()=>document.createElement('div')},{render:()=>document.createElement('div')});
                document.body.append(view.render(data));
            }""",[common,source,lang,count])
            section=page.locator('[data-scope=voltage]');inputs=section.locator('input[type=number]')
            assert inputs.count()==count
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            if lang!='zh-cn':assert not re.search(r'[\u4e00-\u9fff]',section.inner_text())
            # Presets remain drafts; values are scoped by firmware domain IDs.
            section.locator('.mud-cpu-presets').first.locator('button').nth(1).click()
            assert not page.evaluate('requests.length')
            inputs.nth(count-1).fill('6.25')
            section.locator('.mud-cpu-actions .mud-btn.on').click();page.locator('.mud-dlg').wait_for()
            page.keyboard.press('Escape');assert not page.evaluate('requests.length')
            section.locator('.mud-cpu-actions .mud-btn.on').click();page.locator('.mud-dlg [data-r="1"]').click()
            page.wait_for_function('requests.length===1 && !view.busy')
            expected=[0]*count;expected[0]=-3125;expected[-1]=6250
            assert page.evaluate('requests[0]')=={'offsets':expected}
            # Restoring defaults must send two values on U30 Pro, three on UMS9620.
            section.locator('.mud-cpu-actions .mud-btn:not(.on)').click();page.locator('.mud-dlg [data-r="1"]').click()
            page.wait_for_function('requests.length===2 && !view.busy')
            assert page.evaluate('requests[1]')=={'offsets':[0]*count}
            section.locator('.mud-cpu-table button').first.click()
            assert page.locator('.mud-cpu-voltage-table tbody tr').count()==2
            page.keyboard.press('Escape')
            assert not errors,errors;page.close()
    browser.close()
print('PASS 12 CPU voltage UI scenarios: two/three domains, zh/en/tr, mobile/desktop, presets, cancel, save, defaults and tables; no hardware RPC')
