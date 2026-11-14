"""Real CSV module, traffic view, RPC declarations and dialogs; fake transport."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repository',type=Path,default=next(p for p in Path(__file__).resolve().parents if (p/'openwrt').is_dir()))
    parser.add_argument('--browser',default='C:/Program Files/Google/Chrome/Application/chrome.exe')
    args=parser.parse_args();app='luci-app-t551m' if (args.repository/'openwrt/luci-app-t551m').is_dir() else 'luci-app-mu300'
    res=args.repository/f'openwrt/{app}/htdocs/luci-static/resources'
    code=[(res/name).read_text(encoding='utf8') for name in ['mu300/common.js','view/mu300/traffic.js','mu300/traffic-csv.js']]
    cases=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=args.browser,headless=True)
        for lang in ('zh','en','tr'):
            for width in (390,1280):
                page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('http://fixture/**',lambda r:r.fulfill(body='<html><body></body></html>',content_type='text/html'))
                page.goto('http://fixture/')
                page.evaluate('''async ([common,traffic,csv,lang])=>{
                    window.calls=[];window.notes=[];
                    window.setSlot=s=>{sessionStorage.setItem('mu300-view-slot',s);sessionStorage.setItem('modem-view-slot',s);if(window.M&&M.selectViewedSlot)M.selectViewedSlot(s);};setSlot('0');
                    window.rows=[0,1].map(slot=>({slot,config:{plan_name:'Plan '+slot,reset_day:1,count_mode:'total'},status:{slot,clock_ok:true,available:true},days:[],months:[]}));
                    window.CSV='format_version,slot,record_type,date,rx_bytes,tx_bytes,total_bytes,exported_at\\r\\n1,1,meta,,0,0,0,1794657600\\r\\n';
                    const rpc={declare:o=>(...args)=>{
                        calls.push({method:o.method,args});
                        const slot=String(args[(o.params||[]).indexOf('slot')]);
                        if(o.method==='traffic_get')return Promise.resolve(structuredClone(rows[+slot]));
                        if(o.method==='traffic_export')return Promise.resolve({ok:1,slot:+slot,csv:CSV});
                        if(o.method==='traffic_import'){
                            if(args[3]){rows[+slot].csv_backup=true;return Promise.resolve({ok:1,slot:+slot});}
                            if(window.delayPreview)return new Promise(r=>window.finishPreview=r);
                            return Promise.resolve({ok:1,slot:+slot,preview:{source_slot:1,days:2,months:1,conflicts:1,first:'2026-10',last:'2026-11-14'}});
                        }
                        return Promise.resolve({});
                    }},base={extend:x=>x},L={env:{lang},resolveDefault:(p,d)=>p.catch(()=>d)};
                    window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);
                    M.simSelector=M.localizeMenu=()=>{};setSlot('0');
                    M.toast=(text)=>{notes.push(text);return{update(text){notes.push(text);},close(){}};};
                    const module=new Function('baseclass',csv)(base);
                    window.v=new Function('view','M','L','poll','TrafficCsv',traffic)(base,M,L,{add(){},remove(){}},module);
                    document.body.append(v.render(await v.load()));
                }''',code+[lang])
                box=page.locator('#mud-tr-csv')
                if lang!='zh':assert not box.evaluate('(e)=>/[\u3400-\u9fff]/.test(e.innerText)'),(lang,box.inner_text())
                assert page.locator('[data-csv=restore]').is_disabled()
                assert page.locator('[data-csv=backup]').is_disabled()
                with page.expect_download() as downloaded:page.locator('[data-csv=export]').click()
                assert Path(downloaded.value.path()).read_text(encoding='utf-8-sig').startswith('format_version,slot,record_type')
                assert page.evaluate("calls.find(c=>c.method==='traffic_export').args")==['0',False]
                file={'name':'traffic.csv','mimeType':'text/csv','buffer':page.evaluate('CSV').encode()}
                page.locator('[data-csv=file]').set_input_files(file)
                page.locator('[data-csv=restore]').click();page.wait_for_selector('.mud-dlg')
                assert 'IMEI2' in page.locator('.mud-dlg').inner_text()
                page.locator('.mud-dlg-btns [data-r="0"]').click()
                page.wait_for_function('!v._csv.busy')
                assert not page.evaluate("calls.some(c=>c.method==='traffic_import'&&c.args[3])")
                page.locator('[data-csv=mode]').select_option('replace')
                page.locator('[data-csv=restore]').click();page.wait_for_selector('.mud-dlg')
                page.locator('.mud-dlg-btns [data-r="1"]').click()
                page.wait_for_function('!v._csv.busy')
                writes=page.evaluate("calls.filter(c=>c.method==='traffic_import'&&c.args[3])")
                assert len(writes)==1 and writes[0]['args'][0]=='0' and writes[0]['args'][2]=='replace'
                assert page.locator('[data-csv=backup]').is_enabled()
                assert not page.evaluate('v._dirty'),'CSV controls must not mark the plan dirty'
                page.locator('[data-csv=file]').set_input_files(file)
                page.locator('[data-csv=restore]').click();page.wait_for_selector('.mud-dlg')
                page.evaluate("setSlot('1')")
                page.locator('.mud-dlg-btns [data-r="1"]').click();page.wait_for_function('!v._csv.busy')
                assert page.evaluate("calls.filter(c=>c.method==='traffic_import'&&c.args[3]).length")==1,'slot changed during confirmation'
                page.evaluate("setSlot('0');delayPreview=true")
                page.locator('[data-csv=restore]').click();page.wait_for_function('!!window.finishPreview')
                page.evaluate("v.unload();finishPreview({ok:1,preview:{source_slot:1,days:0,months:0}})")
                page.wait_for_function('!v._csv.busy')
                assert page.locator('.mud-dlg').count()==0,'dialog after unload'
                rect=box.bounding_box();assert rect['x']>=0 and rect['x']+rect['width']<=width+1
                assert not errors,errors
                cases+=1;page.close()
        browser.close()
    print('PASS CSV browser:',args.repository.name,cases,'language/viewport cases; export, preview, cancel, restore, slot change, unload, layout')

if __name__=='__main__':main()
