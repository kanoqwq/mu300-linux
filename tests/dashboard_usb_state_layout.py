"""Actual USB view/common UI with fake RPCs; never switches real hardware."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

RES=Path(__file__).resolve().parents[1]/'openwrt/luci-app-mu300/htdocs/luci-static/resources'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser');args=parser.parse_args()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'executable_path':args.browser} if args.browser else {}))
        for lang in ('zh','en','tr'):
            for width in (390,1280):
                page=browser.new_page(viewport={'width':width,'height':900})
                page.route('**/*',lambda r:r.abort())
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content('<html><body><main></main></body></html>')
                page.evaluate('''([common,device,lang])=>{
                    const base={extend:x=>x},L={env:{lang},resolveDefault:p=>p.catch(()=>({}))};
                    window.NetworkPerformance={mount:()=>({dispose(){}})};window.NFC={mount:()=>({dispose(){}})};window.calls=[];window.reply={ok:1};window.gets=[];window.scans=[];
                    window.state={ok:1,role:'device',role_auto:0,host_supported:1,net_mode:'ncm',net_scope:'permanent',net_auto:0,net_current:'ncm',net_next:'ncm'};
                    const rpc={declare:o=>(...args)=>{
                        calls.push([o.method,args]);
                        if(o.method==='usb_set')return Promise.resolve(window.reply);
                        if(o.method==='usb_get')return window.holdGet?new Promise(r=>gets.push(r)):Promise.resolve(state);
                        if(o.method==='usb_net_list')return window.holdScan?new Promise(r=>scans.push(r)):Promise.resolve(window.nicReply||{ok:1,devices:[]});
                        return Promise.resolve({});
                    }};
                    window.M=new Function('rpc','baseclass','L',common)(rpc,base,L);
                    window.v=new Function('view','M','L','Charge',device)(base,M,L,{mount(){}});
                    document.querySelector('main').appendChild(v.render(state));
                    // Only accelerate test timers, keeping production deadline logic.
                    const later=v.later;v.later=function(fn,ms){return later.call(this,fn,ms>=1500?120:Math.min(ms,30));};
                }''',[(RES/'mu300/common.js').read_text(encoding='utf-8'),
                      (RES/'view/mu300/device.js').read_text(encoding='utf-8'),lang])
                assert page.inner_text('#mud-usb-net-current')=='NCM'
                page.select_option('#mud-usb-net-mode','rndis')
                page.select_option('#mud-usb-net-scope','once')
                assert not page.is_checked('#mud-usb-net-auto')
                # Real dialog cancellation never writes a boot policy.
                page.click('#mud-usb-net-apply');page.wait_for_selector('.mud-dlg')
                page.keyboard.press('Escape');page.wait_for_function('!v._confirming')
                assert page.evaluate("calls.filter(c=>c[0]==='usb_set').length")==0
                page.evaluate('M.confirmBox=()=>Promise.resolve(true)')
                page.click('#mud-usb-net-apply')
                page.wait_for_function('!v._busy && !v._confirming')
                assert page.evaluate("calls.filter(c=>c[0]==='usb_set')[0][1]")==['net','rndis','once','0']
                assert page.inner_text('#mud-usb-net-current')=='NCM'
                # Current protocol is independent of next boot after one-shot use.
                page.evaluate("state={...state,net_mode:'rndis',net_current:'rndis',net_next:'ncm',net_auto:0};v.state=state;v.paint()")
                assert page.inner_text('#mud-usb-net-current')=='RNDIS'
                assert page.inner_text('#mud-usb-net-next')=='NCM'
                # A backend failure ends the role spinner and is not success.
                page.evaluate("reply={ok:1,pending:1};state={...state,role_target:'host',role_error:1}")
                page.select_option('#mud-usb-role','host');page.click('#mud-usb-role-apply')
                page.wait_for_function('!v._busy && !v._confirming')
                assert page.locator('#mud-usb-role-apply .mud-spin').count()==0
                assert page.evaluate("document.querySelector('.mud-toast.error')!==null")
                # A hung readback ends as unconfirmed, does not loop overlapping RPCs.
                page.evaluate('holdGet=true;gets=[]')
                page.select_option('#mud-usb-role','host');page.click('#mud-usb-role-apply')
                page.wait_for_function('!v._busy && !v._confirming')
                assert page.locator('#mud-usb-role-apply .mud-spin').count()==0
                assert page.evaluate('gets.length')<=2 # watch + one display refresh, never retry write
                page.evaluate("holdGet=false;gets.forEach(r=>r(state));gets=[]")
                # A real pending transition can complete without waiting for a display refresh.
                page.evaluate("state={...state,role:'host',role_error:0,role_auto:1,net_current:'none',net_next:'none'};holdScan=true")
                page.select_option('#mud-usb-role','host');page.click('#mud-usb-role-apply')
                page.wait_for_function('!v._busy && !v._confirming')
                assert page.is_disabled('#mud-usb-net-apply')
                assert page.locator('#mud-usb-role-apply .mud-spin').count()==0
                page.wait_for_function('scans.length>0')
                # Error is not presented as an empty successful scan.
                page.evaluate('scans.shift()({ok:0})')
                page.wait_for_function('!v._scan')
                expected=page.evaluate("M.translate('读取 USB 网卡失败，请刷新重试')")
                assert page.inner_text('#mud-usb-adapters')==expected
                # M10 policy defaults off; metadata and blocked ownership are
                # rendered without making unsafe add calls, in all locales.
                page.evaluate('''holdScan=false;reply={ok:1};
                    nicReply={ok:1,devices:[
                        {name:'eth0',driver:'r8152',mac:'02:11:22:33:44:55',eligible:1,owner:'',carrier:1},
                        {name:'eth1',driver:'ax88179_178a',mac:'02:11:22:33:44:66',eligible:0,owner:'network.wan',carrier:0},
                        {name:'eth2',driver:'r8152',mac:'02:11:22:33:44:77',eligible:1,owner:'br-lan',in_lan:1,attached:1,carrier:1}
                    ]};void v.refreshAdapters();''')
                page.wait_for_function('!v._scan')
                assert page.locator('.mud-device-item').count()==3
                assert 'r8152' in page.locator('.mud-device-item').nth(0).inner_text()
                assert '02:11:22:33:44:55' in page.locator('.mud-device-item').nth(0).inner_text()
                assert not page.locator('.mud-device-item button').nth(0).is_disabled()
                assert page.locator('.mud-device-item button').nth(1).is_disabled()
                assert page.locator('.mud-device-item button').nth(2).is_disabled()
                assert not page.is_checked('#mud-usb-lan-auto')
                page.check('#mud-usb-lan-auto');page.click('#mud-usb-lan-save')
                page.wait_for_function('!v._busy && !v._confirming && !v._stateReq')
                assert page.evaluate("calls.filter(c=>c[0]==='usb_set').at(-1)[1]")==['lan-auto','1','','']
                assert not page.locator('#mud-usb-lan-save .mud-spin').count()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(lang,width,'NIC overflow')
                if lang!='zh':
                    assert not page.locator('main').evaluate("e=>/[\\u4e00-\\u9fff]/.test(e.textContent)"),page.locator('main').inner_text()
                page.evaluate('holdScan=true')
                # One scan in flight, and late results after unload cannot repaint.
                page.evaluate('void v.refreshAdapters();void v.refreshAdapters()')
                page.wait_for_function('scans.length>0')
                assert page.evaluate('scans.length')==1
                page.evaluate('v.unload();scans.shift()({ok:1,devices:[{name:"late",carrier:1}]})')
                assert page.locator('.mud-device-item').count()==0
                assert page.evaluate('v._timers.size')==0
                if lang!='zh':
                    assert not page.locator('main').evaluate("e=>/[\\u4e00-\\u9fff]/.test(e.textContent)")
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(lang,width,'overflow')
                assert not errors,errors
                page.close()
        browser.close()
    print('USB UI: 6 language/viewport cases, policy, timeout, errors and lifecycle passed')


if __name__=='__main__':main()
