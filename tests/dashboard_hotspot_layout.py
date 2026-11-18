"""Real homepage hotspot wiring; fake RPCs never touch a radio."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

RES=Path(__file__).resolve().parents[1]/'openwrt/luci-app-mu300/htdocs/luci-static/resources'


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--browser'); args=parser.parse_args()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':args.browser} if args.browser else {}))
        for lang in ('zh','en','tr'):
            page=browser.new_page(viewport={'width':390,'height':844})
            page.route('**/*',lambda route:route.abort())
            errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('http://dashboard.fixture/**',lambda route:route.fulfill(body='<html><body><main></main></body></html>',content_type='text/html'))
            page.goto('http://dashboard.fixture/')
            page.evaluate('''([common,home,lang])=>{
                const base={extend:x=>x},L={env:{lang},url:(...p)=>"/cgi-bin/luci/"+p.join("/"),resolveDefault:p=>p.catch(()=>({}))};
                window.calls=[];
                const rpc={declare:o=>(...args)=>{
                    calls.push([o.method,args]);
                    if(o.method==='sim_get')return Promise.resolve({ok:1,slots:2,actual_sim:0,saved_sim:0});
                    if(o.method==='act')return new Promise(r=>window.finish=r);
                    return Promise.resolve({});
                }};
                window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);M.injectCss();M.confirmBox=()=>Promise.resolve(true);
                window.v=new Function('view','M','L','R',home)(base,M,L,{});
                const root=v._root=document.querySelector('main');root.className='mud';root.innerHTML=v.html();v.wire(root);
                window.show=(wifi)=>v.update({info:{wifi},cell:null});
                show({available:1,up:0,pending:0});
            }''',[(RES/'mu300/common.js').read_text(encoding='utf-8'),
                  (RES/'view/mu300/home.js').read_text(encoding='utf-8'),lang])
            button=page.locator('#mud-btn-wifi')
            button.click()
            page.wait_for_function('!!window.finish')
            assert page.evaluate("calls.filter(c=>c[0]==='act')") == [['act',['wifi','on','0']]]
            assert button.is_disabled()
            page.evaluate('show({available:1,up:0,pending:0})')
            assert button.locator('.mud-spin').count()==1
            page.evaluate('finish({ok:1,pending:1})')
            page.wait_for_function('!v._wifiBusy')
            assert button.locator('.mud-spin').count()==0
            page.evaluate("show({available:0,up:0,error:'存在多个热点，请在适配设置选择目标 AP'})")
            assert button.is_disabled()
            if lang!='zh':assert not button.evaluate("e=>/[\\u4e00-\\u9fff]/.test(e.title)")
            page.evaluate('show({available:1,up:0,pending:1})')
            assert button.is_disabled()
            page.evaluate('show({available:1,up:1,pending:0})')
            button.click()
            page.wait_for_function("calls.filter(c=>c[0]==='act').length===2")
            assert page.evaluate("calls.filter(c=>c[0]==='act')[1][1]")==['wifi','off','0']
            page.evaluate("finish({ok:0,error:'热点配置保存失败'})")
            page.wait_for_function('!v._wifiBusy')
            assert button.locator('.mud-spin').count()==0
            assert not button.is_disabled()
            if lang!='zh':assert not page.locator('#mud-toasts').evaluate("e=>/[\\u4e00-\\u9fff]/.test(e.textContent)")
            assert not errors,errors
            page.close()
        browser.close()
    print('hotspot UI: 3 locales, correct on/off, pending/error/ambiguous state and spinner cleanup passed')


if __name__=='__main__':main()
