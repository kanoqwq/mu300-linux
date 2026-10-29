"""UI-only test bridge. Never packaged and never opens ADB or SSH."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BRIDGE=r"""
(() => {
 localStorage.setItem('support-notice-v1','seen');
 const callbacks = new Map(); let seq = 1;
 const device = {serial:'fixture-1234',model:'E5',system:'Android',slot:'_a',root:true,readiness:'ready',encrypted:true,profile:'e5'};
 const pkg = {path:'D:/OpenWrt-Package',name:'e5-openwrt-release',adapter:'e5',profile:'e5',kernel:'v2'};
 const tx = {id:'a'.repeat(32),status:'ready',stage:'plan',device,package:pkg,choices:{method:'part'},plan:{wipe:true,two_stage:true,offline:true,openwrt_bytes:10737418240,android_bytes:21474836480,target:'userdata shrink',grow:true},completed:[],unsafe:false,updated:Date.now()/1000};
 window.__fixture={tx,callbacks,records:[tx],fail:null,requests:[],calls:[],probed:{},autoDismissSupport:true};
 // Other suites dismiss the startup notice as a user would. The support suite
 // disables this automation and verifies the actual dialog and every launch.
 new MutationObserver(()=>{
  if(window.__fixture.autoDismissSupport)
   document.querySelector('button[aria-label="关闭赞赏提示"],button[aria-label="Close support notice"]')?.click();
 }).observe(document,{childList:true,subtree:true});
 window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener(){}};
 window.__TAURI_INTERNALS__={
  transformCallback(fn,once){const id=seq++;callbacks.set(id,fn);return id;},
  unregisterCallback(id){callbacks.delete(id);},
  invoke:async(cmd,args={})=>{
   if(window.__fixture.fail===cmd)throw new Error('Fixture: native operation rejected');
   if(cmd==='save_log')return window.__fixture.cancelSave ? null : 'D:/fixture/log.txt';
   if(cmd==='plugin:event|listen'){window.__fixture.event=args.handler;return 1;}
   if(cmd==='plugin:event|unlisten')return;
   if(cmd==='backend_request'){
    window.__fixture.requests.push(args.command);
    window.__fixture.calls.push({command:args.command,params:args.params});
    if(window.__fixture.fail===args.command)throw new Error('Fixture: operation rejected');
    switch(args.command){
     case 'hello':return {package_path:pkg.path,transactions:window.__fixture.saved || []};
     case 'package':return pkg;
     case 'devices':return window.__fixture.devices ?? [{...device,label:'E5',state:'device'},{serial:'fixture-5678',label:'Other device',state:'unauthorized'}];
     case 'probe':return {...device,...window.__fixture.probed,serial:args.params.serial};
     case 'plan':Object.assign(tx,{choices:{...args.params.choices},device:{...device,...window.__fixture.probed,serial:args.params.serial},plan:{...tx.plan,method:args.params.choices.method,wipe:args.params.choices.method==='part',two_stage:args.params.choices.method==='part',offline:args.params.choices.method==='part',target:args.params.choices.method==='reinstall'?'mmcblk1p20':'userdata shrink'}});return {...tx};
     case 'list':return window.__fixture.records;
     case 'start':if(window.__fixture.startRace){const older={...tx,stage:'android_setup',status:'waiting_user',revision:1};window.__emitFixture({stage:'complete',status:'completed',revision:2,result:{filesystem_bytes:10737418240,url:'http://192.168.9.1'}});return older;}tx.stage='android_setup';tx.status='waiting_user';return {...tx};
     case 'cancel':return {accepted:window.__fixture.cancelAccepted ?? true,deferred:false};
     case 'verify_openwrt':return {accepted:true};
     case 'reboot_device':return {accepted:true};
     case 'diagnostics':return {text:'DEBUG fixture diagnostics: commands, traceback and verified bytes (redacted)'};
     default:return null;
    }
   }
   return null;
  }
 };
 window.__emitFixture=(change)=>{
  Object.assign(tx,change);
  callbacks.get(window.__fixture.event)?.({event:'backend',id:1,payload:{type:'transaction',data:JSON.parse(JSON.stringify(tx))}});
 };
 window.__logFixture=()=>{for(let i=0;i<700;i++)callbacks.get(window.__fixture.event)?.({event:'backend',id:1,payload:{type:'log',data:{message:'Test log line '+i}}});};
})();
"""


def settle(page):
    page.wait_for_timeout(450)


def fixture_context(browser,**options):
    context=browser.new_context(**options)
    context.route('https://api.kanokano.cn/pay/zsm.png',lambda route:route.abort())
    return context


def layout(page):
    result=page.evaluate('''() => {
      const footer=document.querySelector('footer').getBoundingClientRect();
      const main=document.querySelector('main').getBoundingClientRect();
      const hint=document.querySelector('.sidebar-bottom').getBoundingClientRect();
      const aside=document.querySelector('aside').getBoundingClientRect();
      return {root:document.documentElement.scrollHeight,height:innerHeight,width:innerWidth,
        scroll:scrollY,footer:footer.bottom,main:main.bottom,hint:hint.bottom,aside:aside.bottom,
        horizontal:document.documentElement.scrollWidth};
    }''')
    assert result['root']==result['height'] and result['horizontal']==result['width'],result
    assert result['scroll']==0 and result['footer']<=result['height']+.5,result
    assert result['aside']-result['hint']<30 and result['hint']<=result['aside'],result


def main():
    out=ROOT/'screenshots';out.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch()
        context=fixture_context(browser,viewport={'width':1120,'height':900},device_scale_factor=1)
        context.add_init_script(BRIDGE+"localStorage.setItem('onboarding-v1','seen');")
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:1420')
        page.get_by_role('heading',name='你想如何使用 OpenWrt？').wait_for()
        page.get_by_label('主题').select_option('light')
        settle(page);layout(page)
        page.screenshot(path=str(out/'home-light-zh.png'),full_page=True)
        page.get_by_role('button',name='安装 OpenWrt',exact=False).click()
        page.get_by_role('button',name='校验安装包',exact=True).click()
        page.get_by_role('button',name='刷新设备',exact=True).click()
        assert page.get_by_label('请选择设备').input_value()==''
        page.get_by_label('请选择设备').select_option('fixture-1234')
        page.get_by_role('button',name='检查设备',exact=True).click()
        page.get_by_role('button',name='查看安装计划').click()
        assert page.get_by_role('button',name='确认并开始').is_disabled()
        settle(page);layout(page)
        page.screenshot(path=str(out/'plan-light-zh.png'),full_page=True)
        page.get_by_role('checkbox').check()
        page.get_by_role('button',name='确认并开始').click()
        page.get_by_role('heading',name='请完成 Android 初始设置',exact=True).first.wait_for()
        settle(page);layout(page)
        page.screenshot(path=str(out/'wait-setup-light-zh.png'),full_page=True)
        page.get_by_label('主题').select_option('dark')
        page.get_by_label('语言').select_option('en')
        page.wait_for_timeout(300)
        page.evaluate("window.__emitFixture({stage:'root_permission',status:'waiting_user'})")
        page.get_by_role('heading',name='Allow the root request',exact=True).first.wait_for()
        settle(page)
        assert page.locator('[data-sonner-toast][data-type=info]').filter(has_text='Allow the root request').count()==1
        for _ in range(8):page.evaluate("window.__emitFixture({stage:'root_permission',status:'waiting_user'})")
        assert page.locator('[data-sonner-toast][data-type=info]').filter(has_text='Allow the root request').count()==1
        page.screenshot(path=str(out/'wait-root-dark-en.png'),full_page=True)
        page.evaluate("window.__emitFixture({stage:'complete',status:'completed',result:{filesystem_bytes:10737418240,url:'http://192.168.9.1',backup_directory:'fixture'}})")
        page.get_by_role('heading',name='OpenWrt installed and verified').wait_for()
        assert 'username root, default password root' in page.locator('.success-panel .login-hint').inner_text()
        settle(page);layout(page)
        page.screenshot(path=str(out/'complete-dark-en.png'),full_page=True)
        # Smallest supported native window: large list and long log scroll independently.
        page.set_viewport_size({'width':800,'height':650})
        page.get_by_label('Language').select_option('zh')
        page.get_by_label('主题').select_option('light')
        page.get_by_role('button',name='开始',exact=True).click()
        page.evaluate("window.__fixture.records=Array.from({length:120},(_,i)=>({...window.__fixture.tx,id:String(i).padStart(32,'0'),status:'cancelled',stage:'backup'}))")
        page.get_by_role('button',name='继续未完成的安装',exact=False).click()
        page.get_by_role('heading',name='继续未完成的安装').wait_for();settle(page)
        assert page.evaluate('document.activeElement.tagName')=='H1'
        assert page.locator('.record').count()==120
        layout(page)
        before=page.locator('h1').bounding_box()
        page.locator('.records-list').evaluate('(el)=>el.scrollTop=el.scrollHeight')
        assert page.locator('h1').bounding_box()==before
        assert page.locator('.records-list').evaluate('(el)=>el.scrollTop>0')
        page.evaluate('window.__logFixture()')
        if page.get_by_role('button',name='详细日志',exact=False).get_attribute('aria-expanded')=='false':page.get_by_role('button',name='详细日志',exact=False).click()
        settle(page);layout(page)
        assert page.locator('.records-list').evaluate('(el)=>el.clientHeight>=100')
        assert page.locator('.log-panel pre').evaluate('(el)=>el.scrollHeight>el.clientHeight')
        assert page.locator('.log-toggle span').inner_text()=='500'
        page.screenshot(path=str(out/'resume-small-light-zh.png'),full_page=True)
        page.evaluate('window.__fixture.cancelSave=true')
        page.get_by_role('button',name='导出日志',exact=True).click();settle(page)
        assert page.get_by_text('日志已导出',exact=True).count()==0
        page.evaluate('window.__fixture.cancelSave=false')
        page.get_by_role('button',name='导出日志',exact=True).click()
        page.get_by_text('日志已导出',exact=True).wait_for()
        page.evaluate("window.__fixture.fail='write_log'")
        page.get_by_role('button',name='导出日志',exact=True).click()
        page.get_by_text('Error: Fixture: native operation rejected',exact=True).wait_for()
        page.evaluate("window.__fixture.fail='pick_package'")
        page.get_by_role('button',name='选择安装包目录',exact=True).click()
        assert page.locator('[data-sonner-toast][data-type=error]').count()==1
        page.evaluate('window.__fixture.fail=null')
        page.evaluate("Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:()=>Promise.reject(new Error('Fixture: clipboard denied'))})")
        page.get_by_role('button',name='复制',exact=True).click()
        page.get_by_text('Error: Fixture: clipboard denied',exact=True).wait_for()
        page.get_by_role('button',name='详细日志',exact=False).click();settle(page)
        page.get_by_role('button',name='开始',exact=True).click();settle(page)
        page.get_by_role('button',name='安装 OpenWrt',exact=False).click();settle(page)
        # One notification surface catches backend errors, native dialog rejection and clipboard failure.
        page.evaluate("window.__fixture.fail='package'")
        page.get_by_role('button',name='校验安装包',exact=True).click()
        page.get_by_text('Error: Fixture: operation rejected',exact=True).wait_for()
        assert page.locator('[data-sonner-toast][data-type=error]').count()==1
        page.evaluate('window.__fixture.fail=null')
        page.get_by_role('button',name='校验安装包',exact=True).click()
        page.get_by_role('button',name='刷新设备',exact=True).click()
        assert page.get_by_label('请选择设备').input_value()=='fixture-1234'
        page.get_by_label('请选择设备').select_option('fixture-1234')
        page.get_by_role('button',name='检查设备',exact=True).click();settle(page);layout(page)
        assert page.locator('.page-scroll').evaluate('(el)=>el.scrollHeight>el.clientHeight')
        action=page.get_by_role('button',name='查看安装计划').bounding_box()
        page.locator('.page-scroll').evaluate('(el)=>el.scrollTop=el.scrollHeight')
        assert page.get_by_role('button',name='查看安装计划').bounding_box()==action
        page.screenshot(path=str(out/'install-small-light-zh.png'),full_page=True)
        # System theme and reduced motion remain usable after view changes.
        page.emulate_media(color_scheme='dark',reduced_motion='reduce')
        page.get_by_label('主题').select_option('system')
        assert page.locator('html').get_attribute('class')=='dark'
        page.get_by_role('button',name='返回',exact=True).click();settle(page);layout(page)
        assert page.locator('.purpose').first.evaluate('(el)=>getComputedStyle(el).transform') in ('none','matrix(1, 0, 0, 1, 0, 0)')
        page.get_by_label('语言').select_option('en')
        page.get_by_role('button',name='Install OpenWrt',exact=False).click();settle(page)
        page.get_by_role('button',name='Inspect device',exact=True).click()
        page.evaluate("Object.assign(window.__fixture.tx,{status:'ready',stage:'plan'})")
        page.get_by_role('button',name='Review installation plan').click();settle(page);layout(page)
        confirm=page.get_by_role('button',name='Confirm and start')
        assert confirm.is_visible() and confirm.is_disabled()
        assert confirm.bounding_box()['y']<page.locator('.log-panel').bounding_box()['y']
        page.screenshot(path=str(out/'plan-small-dark-en.png'),full_page=True)
        assert not errors, errors
        print('UI smoke passed: fixed 800x650/1120x900 layout, 120 records, 500 log cap, independent scrolling, toast errors/status deduplication, multi-device selection, confirmation, waits/completion, themes/languages/reduced motion; no device transport')
        browser.close()


if __name__=='__main__':main()
