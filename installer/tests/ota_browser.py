"""Exercise real Tauri frontend views with an offline bridge (no device writes)."""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

ORIGIN=os.environ.get('OTA_UI_TEST_URL','http://127.0.0.1:1425')

BRIDGE = r'''
let picked=0;
const manifest={family:'e5',profile:'t551m',base_version:'t551m-r20261019.170725',target_version:'t551m-r20261020.180000',kernel_release:'6.18.54-t551m-otg10',operations:[{path:'usr/libexec/unisoc-modem/action',before:{},after:{}}],preserved:['etc/config/wireless']};
window.__TAURI_INTERNALS__={transformCallback:()=>1,unregisterCallback:()=>{},invoke:async(cmd,args)=>{
 if(cmd==='plugin:event|listen')return 1;
 if(cmd==='pick_ota_input')return ++picked===1?'D:/Releases/old.zip':'D:/Releases/new.zip';
 if(cmd==='save_ota')return 'D:/Releases/update.ota';
 const command=args?.command;
 if(command==='ota_package')return {path:'D:/Releases/update.ota',manifest};
 if(command==='ota_analyze')return {manifest:window.testBlocked?{...manifest,operations:Array.from({length:180},(_,i)=>({path:`usr/lib/example/changed-file-${i}.js`,before:{},after:{}}))}:manifest,blockers:window.testBlocked?Array.from({length:12},(_,i)=>`Core runtime/module/updater changed; use the full installer: usr/lib/unisoc-ota/changed-file-${i}.py`):[],can_export:!window.testBlocked,payload_bytes:53720,files:window.testBlocked?180:1,added:0,removed:0};
 if(command==='ota_export')return {output:'D:/Releases/update.ota'};
 if(command==='ota_probe')return {token:'reviewed',check:{applicable:!window.testConflict,conflicts:window.testConflict?['usr/libexec/unisoc-modem/action']:[],package_conflicts:[],required_bytes:20000000,free_bytes:1000000000}};
 if(command==='ota_apply')return {status:'running'};
 if(command==='ota_status')return {identity:{version:manifest.target_version},job:{status:'complete'},transaction:{started:1,total:1}};
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


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1120,'height':860})
        await page.add_init_script(BRIDGE)
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
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
        await check_footer_spacing(page)
        await page.get_by_role('button',name='连接并检查').click()
        await page.get_by_role('button',name='确认应用此补丁').click()
        await page.get_by_text('更新完成，请在网页系统补丁页重启设备。').wait_for(timeout=10000)
        await page.evaluate('window.testConflict=true')
        await page.get_by_role('button',name='连接并检查').click()
        assert await page.get_by_role('button',name='确认应用此补丁').is_disabled()
        await page.set_viewport_size({'width':800,'height':650})
        await check_footer_spacing(page)
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        await browser.close()
        print('OTA browser workflows passed:',output)


if __name__=='__main__':asyncio.run(main())
