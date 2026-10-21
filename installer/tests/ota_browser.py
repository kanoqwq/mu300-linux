"""Exercise real Tauri frontend views with an offline bridge (no device writes)."""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

ORIGIN=os.environ.get('OTA_UI_TEST_URL','http://127.0.0.1:1425')

BRIDGE = r'''
let picked=0;
let callbackId=0,eventId=0;
const callbacks=new Map(),listeners=new Map();
window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener:()=>{}};
window.testNativeEvent=(event,payload)=>{for(const [id,entry] of listeners)if(entry.event===event)callbacks.get(entry.handler)?.({event,id,payload});};
const manifest={family:'e5',profile:'t551m',base_version:'t551m-r20261019.170725',target_version:'t551m-r20261020.180000',kernel_release:'6.18.54-t551m-otg10',operations:[{path:'usr/libexec/unisoc-modem/action',before:{},after:{}}],preserved:['etc/config/wireless']};
window.__TAURI_INTERNALS__={metadata:{currentWindow:{label:'main'},currentWebview:{label:'main'}},transformCallback:handler=>{callbacks.set(++callbackId,handler);return callbackId;},unregisterCallback:id=>callbacks.delete(id),invoke:async(cmd,args)=>{
 if(cmd==='plugin:event|listen'){listeners.set(++eventId,args);return eventId;}
 if(cmd==='plugin:event|unlisten'){listeners.delete(args.eventId);return;}
 if(cmd==='pick_ota_input')return window.selectedFile||(++picked===1?'D:/Releases/old.zip':'D:/Releases/new.zip');
 if(cmd==='save_ota'){window.saveArgs=args;return window.cancelSave?null:(window.manualOutput||'D:/Releases/update.ota');}
 const command=args?.command;
 if(command==='ota_package'){if(window.badPackage)throw Error('Invalid selected patch');return {path:'D:/Releases/update.ota',manifest};}
 if(command==='ota_analyze'){window.analyzeArgs=args.params;return {manifest:window.testBlocked?{...manifest,operations:Array.from({length:180},(_,i)=>({path:`usr/lib/example/changed-file-${i}.js`,before:{},after:{}}))}:manifest,blockers:window.testBlocked?Array.from({length:12},(_,i)=>`Core runtime/module/updater changed; use the full installer: usr/lib/unisoc-ota/changed-file-${i}.py`):[],can_export:!window.testBlocked,payload_bytes:53720,files:window.testBlocked?180:1,added:0,removed:0};}
 if(command==='ota_export'){window.exportArgs=args.params;return {output:args.params.output};}
 if(command==='ota_probe')return {token:'reviewed',check:{applicable:!window.testConflict,conflicts:window.testConflict?['usr/libexec/unisoc-modem/action']:[],package_conflicts:[],required_bytes:20000000,free_bytes:1000000000}};
 if(command==='ota_apply'){window.autoRebootRequested=args.params.auto_reboot;return {status:'running'};}
 if(command==='ota_status'){
  window.statusCalls=(window.statusCalls||0)+1;
  if(window.deferStatus)return new Promise(resolve=>{window.statusResolve=resolve;});
  return window.statusResult||{identity:{version:manifest.target_version},job:{status:'complete'},transaction:{started:1,total:1,status:'complete'},reboot:window.autoRebootRequested?{status:'rebooting'}:undefined};
 }
 if(command==='ota_reboot'){window.rebootCalls=(window.rebootCalls||0)+1;return {status:'rebooting'};}
 return null;
}};
'''


async def check_footer_spacing(page):
    await page.locator('.ota-footer').wait_for()
    gap = await page.evaluate('''() => document.querySelector('.ota-footer').getBoundingClientRect().top
        - Math.max(...Array.from(document.querySelectorAll('.ota-card'), el => el.getBoundingClientRect().bottom))''')
    assert gap >= 10, f'Footer separator touches cards: {gap}px'


async def check_long_analysis_scroll(page):
    await page.evaluate('window.testBlocked=true')
    await page.get_by_role('button',name='分析差异').click()
    export = page.get_by_role('button',name='导出差异包')
    assert await export.is_disabled()
    for width,height in ((1920,1008),(1120,860),(800,650)):
        await page.set_viewport_size({'width':width,'height':height})
        await page.get_by_role('button',name='Theme',exact=True).click()
        main=page.locator('.ota-main')
        result=page.locator('.ota-result-body')
        errors=page.locator('.ota-blockers')
        await result.evaluate('el=>el.scrollTop=0')
        box=await errors.bounding_box()
        assert box and box['height']<=181,box
        await errors.hover()
        await page.mouse.wheel(0,500)
        await page.wait_for_function("document.querySelector('.ota-blockers').scrollTop>0")
        if await result.evaluate('el=>el.scrollHeight>el.clientHeight'):
            await result.focus()
            await page.keyboard.press('Control+End')
            await page.wait_for_function("document.querySelector('.ota-result-body').scrollTop>0",timeout=2500)
        await export.scroll_into_view_if_needed()
        bounds=await export.bounding_box()
        assert bounds and bounds['y']>=76 and bounds['y']+bounds['height']<=height,bounds
        await page.get_by_text('查看文件清单',exact=True).click()
        files=page.locator('.ota-files')
        await files.hover()
        await page.mouse.wheel(0,500)
        await page.wait_for_function("document.querySelector('.ota-files').scrollTop>0")
        await page.get_by_text('查看文件清单',exact=True).click()
        assert await main.evaluate('el=>el.scrollTop')==0
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert await page.evaluate('document.documentElement.scrollHeight<=innerHeight')
        footer=await page.locator('.ota-footer').bounding_box()
        assert footer and footer['y']>=76 and footer['y']+footer['height']<=height+1,footer
        header=await page.locator('header').bounding_box()
        assert header and header['y']==0,header
        if width==1120:
            await result.evaluate('el=>el.scrollTop=0')
            await page.screenshot(path=str(Path(__file__).resolve().parents[2]/'work/ota-ui-long-results.png'),animations='disabled')


async def drop_file(page, target, paths, event='tauri://drag-drop'):
    box=await page.locator('[data-ota-drop="'+target+'"]').bounding_box()
    assert box
    await page.evaluate('''({box,paths,event})=>window.testNativeEvent(event,{paths,position:{x:(box.x+box.width/2)*devicePixelRatio,y:(box.y+box.height/2)*devicePixelRatio}})''',dict(box=box,paths=paths,event=event))


async def check_drag_and_names(page):
    await page.goto(ORIGIN+'/diff.html')
    await page.locator('[data-ota-drop="old"]').wait_for()
    await drop_file(page,'old',['D:/发行包/old.ZIP'],'tauri://drag-enter')
    await page.locator('[data-ota-drop="old"].is-dragover').wait_for()
    await drop_file(page,'old',['D:/发行包/old.ZIP'])
    await drop_file(page,'new',['D:/Releases/t551m-openwrt-r123-k6.18.54.zip'])
    await page.locator('[data-ota-drop="new"]').filter(has_text='t551m-openwrt-r123-k6.18.54.zip').wait_for()
    assert not await page.locator('.is-dragover').count()
    await page.get_by_role('button',name='分析差异').click()
    assert await page.evaluate('window.analyzeArgs.old')=='D:/发行包/old.ZIP'
    await page.evaluate("window.manualOutput='D:/My patches/custom-name.zip'")
    await page.get_by_role('button',name='导出差异包').click()
    await page.get_by_role('status').filter(has_text='custom-name.zip').wait_for()
    assert await page.evaluate('window.saveArgs.releasePath')=='D:/Releases/t551m-openwrt-r123-k6.18.54.zip'
    assert await page.evaluate('window.saveArgs.baseVersion')=='t551m-r20261019.170725'
    assert await page.evaluate('window.saveArgs.targetVersion')=='t551m-r20261020.180000'
    assert await page.evaluate('window.exportArgs.output')=='D:/My patches/custom-name.zip'
    await page.evaluate('window.cancelSave=true;window.exportArgs=null')
    await page.get_by_role('button',name='导出差异包').click()
    assert await page.evaluate('window.exportArgs') is None
    await drop_file(page,'new',['D:/wrong.txt'])
    await page.get_by_role('alert').filter(has_text='完整 ZIP').wait_for()
    assert 't551m-openwrt-r123-k6.18.54.zip' in await page.locator('[data-ota-drop="new"]').inner_text()
    await drop_file(page,'old',['D:/1.zip','D:/2.zip'])
    await page.get_by_role('alert').filter(has_text='一次只接收一个文件').wait_for()
    await page.evaluate('window.testNativeEvent("tauri://drag-drop",{paths:["D:/outside.zip"],position:{x:1,y:1}})')
    await page.get_by_role('alert').filter(has_text='对应的选择框').wait_for()
    await drop_file(page,'new',['D:/Releases/replacement.zip'])
    assert await page.get_by_role('button',name='导出差异包').count()==0,'Replacing a release kept stale analysis'


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1120,'height':860})
        await page.add_init_script(BRIDGE)
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await check_drag_and_names(page)
        await page.goto(ORIGIN+'/diff.html')
        await check_footer_spacing(page)
        choices=page.get_by_role('button',name='选择 ZIP 文件')
        await choices.nth(0).click();await choices.nth(0).click()
        await page.get_by_role('button',name='分析差异').click()
        await page.get_by_role('button',name='导出差异包').wait_for()
        await page.get_by_role('button',name='导出差异包').click()
        await page.get_by_role('status').wait_for()
        await check_long_analysis_scroll(page)
        output=Path(__file__).resolve().parents[2]/'work/ota-ui-preview.png'
        await page.screenshot(path=str(output),full_page=True)
        await page.goto(ORIGIN+'/ota.html')
        await drop_file(page,'patch',['D:/Releases/dropped.ota'])
        await page.locator('[data-ota-drop="patch"]').filter(has_text='dropped.ota').wait_for()
        await check_footer_spacing(page)
        await page.get_by_role('button',name='连接并检查').click()
        assert await page.get_by_role('checkbox',name='更新完成后自动重启').is_checked()
        await page.get_by_role('button',name='确认应用此补丁').click()
        await page.get_by_text('更新完成，设备正在重启。稍后可重新检查设备状态。').wait_for(timeout=10000)
        await page.evaluate('window.autoRebootRequested=false')
        await page.get_by_role('button',name='重新检查设备状态').click()
        await page.get_by_role('button',name='重启设备',exact=True).click()
        await page.get_by_role('dialog').wait_for()
        await page.get_by_role('button',name='取消',exact=True).click()
        assert await page.evaluate('window.rebootCalls||0')==0
        await page.get_by_role('button',name='重启设备',exact=True).click()
        await page.get_by_role('button',name='确认重启',exact=True).click()
        await page.get_by_text('重启命令已发送，设备正在重新启动。').wait_for()
        assert await page.evaluate('window.rebootCalls')==1
        await page.evaluate('window.testConflict=true')
        await page.get_by_role('button',name='连接并检查').click()
        assert await page.get_by_role('button',name='确认应用此补丁').is_disabled()
        await page.set_viewport_size({'width':800,'height':650})
        await check_footer_spacing(page)
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        await page.goto(ORIGIN+'/ota.html')
        await page.evaluate("window.statusResult={identity:{version:'test'},job:{status:'failed',result:{error:'Exploratory write failure'}},transaction:{status:'rolled_back'}}")
        await page.get_by_role('button',name='重新检查设备状态').click()
        await page.get_by_role('alert').filter(has_text='Exploratory write failure').wait_for(timeout=2500)
        assert not await page.get_by_role('status').count(), 'Failed transaction displayed as success'
        await page.evaluate("window.statusResult={identity:{version:'test'},job:{status:'complete'},transaction:{status:'rolled_back'}}")
        await page.get_by_role('button',name='重新检查设备状态').click()
        assert await page.get_by_role('button',name='重启设备',exact=True).is_enabled(), 'Rollback has no direct reboot'
        await page.goto(ORIGIN+'/ota.html')
        await page.get_by_role('button',name='连接并检查').click()
        await page.evaluate('window.deferStatus=true')
        await page.get_by_role('button',name='确认应用此补丁').click()
        await page.wait_for_function('window.statusCalls===1')
        current_patch=await page.locator('[data-ota-drop="patch"]').inner_text()
        await drop_file(page,'patch',['D:/must-not-replace.ota'])
        assert await page.locator('[data-ota-drop="patch"]').inner_text()==current_patch,'A drop replaced the active update'
        await page.wait_for_timeout(4500)
        assert await page.evaluate('window.statusCalls')==1, 'Slow status calls overlap and queue'
        await page.evaluate("window.deferStatus=false;window.statusResolve({identity:{version:'test'},job:{status:'complete'},transaction:{status:'complete'}})")
        await page.get_by_role('button',name='重启设备',exact=True).wait_for()
        await page.locator('.ota-card input').first.fill('192.168.77.2')
        assert await page.get_by_role('button',name='重启设备',exact=True).count()==0, 'Device change retained old reboot control'
        await page.evaluate("window.badPackage=true;window.selectedFile='D:/invalid.ota'")
        await page.locator('.ota-picker').click()
        await page.get_by_role('alert').filter(has_text='Invalid selected patch').wait_for()
        assert await page.locator('.ota-release').count()==0, 'Invalid patch retained the previous manifest'
        await page.goto(ORIGIN+'/ota.html')
        await page.get_by_role('button',name='English',exact=True).click()
        await page.get_by_role('button',name='Connect and review',exact=True).click()
        assert await page.get_by_role('checkbox',name='Automatically reboot after updating').is_checked()
        assert not errors,errors
        scaled=await browser.new_page(viewport={'width':1120,'height':860},device_scale_factor=2)
        await scaled.add_init_script(BRIDGE)
        scaled.on('pageerror',lambda e:errors.append(str(e)))
        await check_drag_and_names(scaled)
        await scaled.close()
        assert not errors,errors
        await browser.close()
        print('OTA browser workflows passed:',output)


if __name__=='__main__':asyncio.run(main())
