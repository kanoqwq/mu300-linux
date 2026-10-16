"""Browser checks with simulated transport. --source uses Vite without a build."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright
from ui_smoke import fixture_context, BRIDGE, ROOT, settle, layout

UNINSTALL = r'''
localStorage.setItem('onboarding-v1','seen');
window.__fixture.probed={existing:true};
const originalInvoke=window.__TAURI_INTERNALS__.invoke;
window.__TAURI_INTERNALS__.invoke=async(command,args={})=>{
 if(command==='backend_request' && args.command==='plan' && args.params.choices.method==='uninstall' && window.__fixture.removeWipe && !args.params.reset_token){
   return {confirmation_required:'format_android',reason:'Android data is encrypted / Android data 已加密',reset_token:'fixture-reset-token',model:'E5',serial_hint:'1234'};
 }
 const result=await originalInvoke(command,args);
 if(command==='backend_request' && args.command==='plan' && args.params.choices.method==='uninstall'){
   const wipe=window.__fixture.removeWipe;
   const preserve=window.__fixture.removePreserve;
   const plan={...result.plan,method:'uninstall',wipe,data_action:preserve?'grow-ext4':wipe?'reset':'gap',two_stage:true,offline:true,grow:false,
     target:'mmcblk0p5',previous_android_bytes:28*1073741824,android_bytes:((wipe||preserve)?32:28)*1073741824,
     openwrt_bytes:4*1073741824,reclaimed_bytes:(wipe||preserve)?4*1073741824:0,
     layout_source:window.__fixture.layoutSource,released_unallocated_bytes:(wipe||preserve)?0:4*1073741824};
   window.__fixture.tx.plan=plan;result.plan=plan;
 }
 if(command==='backend_request' && args.command==='start'){
   Object.assign(result,{stage:'uninstall_offline',status:'waiting_device',uninstall_armed:true});
   Object.assign(window.__fixture.tx,result);
 }
 return result;
};
'''


def main(url='http://127.0.0.1:1420'):
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for language,wipe,theme,preserve,basis in (('zh',True,'dark',False,'original_record'),('en',False,'light',False,'original_record'),('zh',False,'light',True,'original_record'),('en',True,'light',False,'original_record'),('zh',False,'light',True,'device'),('en',False,'dark',False,'device')):
            context=fixture_context(browser,viewport={'width':1120,'height':900},device_scale_factor=1)
            context.add_init_script(BRIDGE+UNINSTALL+
                'localStorage.setItem("language",'+json.dumps(language)+');'+
                'localStorage.setItem("theme",'+json.dumps(theme)+');'+
                'window.__fixture.removeWipe='+json.dumps(wipe)+';window.__fixture.removePreserve='+json.dumps(preserve)+';window.__fixture.layoutSource='+json.dumps(basis)+';')
            page=context.new_page();errors=[]
            page.set_default_timeout(10000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('console',lambda message:errors.append(message.text) if message.type=='error' and 'net::ERR_FAILED' not in message.text else None)
            page.on('requestfailed',lambda request:errors.append(request.url) if not request.url.startswith('https://api.kanokano.cn/pay/zsm.png') else None)
            page.on('response',lambda response:errors.append(str(response.status)+' '+response.url) if response.status>=400 else None)
            page.goto(url)
            zh=language=='zh'
            try:
                page.get_by_role('button',name='高级选项' if zh else 'Advanced options',exact=False).click();settle(page)
            except Exception as error:
                raise AssertionError({'errors':errors,'html':page.content()[:4000]}) from error
            page.get_by_role('button',name='卸载并归还空间' if zh else 'Uninstall and return space',exact=True).click()
            page.get_by_role('button',name='校验安装包' if zh else 'Verify package',exact=True).click()
            page.get_by_label('请选择设备' if zh else 'Select a device').select_option('fixture-1234')
            page.get_by_role('button',name='检查设备' if zh else 'Inspect device',exact=True).click();settle(page)
            calls=page.evaluate('window.__fixture.calls')
            assert [c for c in calls if c['command']=='probe'][-1]['params']['method']=='uninstall'
            page.get_by_role('button',name='查看卸载计划' if zh else 'Review removal plan',exact=True).click();settle(page)
            if wipe:
                dialog=page.get_by_role('dialog')
                dialog.get_by_role('heading',name='无法保留 Android 数据卸载，是否格式化？' if zh else 'Cannot preserve Android data. Format instead?').wait_for()
                assert not page.get_by_role('button',name='确认卸载' if zh else 'Confirm uninstall',exact=True).count()
                page.set_viewport_size({'width':800,'height':650});settle(page)
                bounds=dialog.bounding_box();assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=650
                output=ROOT/'screenshots';output.mkdir(exist_ok=True)
                if zh:page.screenshot(path=str(output/'uninstall-reset-choice-zh.png'))
                page.set_viewport_size({'width':1120,'height':900});settle(page)
                dialog.get_by_role('button',name='取消卸载' if zh else 'Cancel removal',exact=True).click();settle(page)
                assert not [c for c in page.evaluate('window.__fixture.calls') if c['command']=='start']
                page.get_by_role('button',name='查看卸载计划' if zh else 'Review removal plan',exact=True).click();settle(page)
                dialog.get_by_role('button',name='选择格式化并查看计划' if zh else 'Choose format and review plan',exact=True).click();settle(page)
                assert [c for c in page.evaluate('window.__fixture.calls') if c['command']=='plan'][-1]['params']['reset_token']=='fixture-reset-token'
            button=page.get_by_role('button',name='确认卸载' if zh else 'Confirm uninstall',exact=True)
            assert button.is_disabled()
            assert page.locator('.plan-list').get_by_text('4.00 GiB',exact=True).count()>=1
            assert page.locator('.confirmation').count()==1
            if basis=='device':
                page.get_by_text('未找到可用的原安装备份' if zh else 'No usable original installation backup',exact=False).wait_for()
            if not wipe and not preserve:
                page.locator('.plan-list').get_by_text('释放为空闲的空间' if zh else 'Space released as unallocated',exact=True).wait_for()
            assert page.get_by_text('保留 Android 数据' if preserve else '全部清空' if zh else 'All Android apps' if wipe else 'Android userdata is not enlarged',exact=False).count()>=1
            layout(page)
            page.set_viewport_size({'width':800,'height':650});settle(page)
            layout(page)
            impact=page.locator('.page > .alert').bounding_box()
            assert impact and impact['y']+impact['height']<650
            page.set_viewport_size({'width':1120,'height':900});settle(page)
            output=ROOT/'screenshots';output.mkdir(exist_ok=True)
            page.screenshot(path=str(output/(('uninstall-plan-device-zh.png' if zh else 'uninstall-plan-device-gap-en.png') if basis=='device' else 'uninstall-plan-preserve-zh.png' if preserve else 'uninstall-plan-reset-zh.png' if zh else 'uninstall-plan-reset-en.png' if wipe else 'uninstall-plan-gap-en.png')))
            page.locator('.confirmation input').check();button.click();settle(page)
            page.get_by_text('不会撤回设备上的卸载或已确认的重置' if zh else 'does not withdraw device removal or an approved reset',exact=False).wait_for()
            assert 'verify_openwrt' not in page.evaluate('window.__fixture.requests')
            page.evaluate('''() => window.__emitFixture({stage:'complete',status:'completed',
              result:{uninstalled:true,filesystem_bytes:32*1073741824,url:''}})''');settle(page)
            page.get_by_role('heading',name='OpenWrt 已卸载并完成容量验证' if zh else 'OpenWrt removed and capacity verified').wait_for()
            assert page.locator('.login-hint').count()==0
            assert not page.locator('.success-panel').get_by_text('管理地址' if zh else 'Management address',exact=True).count()
            layout(page)
            if zh:page.screenshot(path=str(output/'uninstall-complete-zh.png'))
            page.evaluate("() => window.__emitFixture({stage:'uninstall_offline',status:'cancelled'})");settle(page)
            page.locator('.nav-home').click();settle(page)
            assert ('卸载' if zh else 'removal') not in page.locator('.stepper').inner_text()
            page.get_by_role('button',name='继续未完成的安装' if zh else 'Continue an unfinished installation',exact=False).click();settle(page)
            label='更换卸载设备的连接（可选）' if zh else 'Change removal device connection (optional)'
            page.locator('summary').filter(has_text=label).click()
            page.evaluate("window.__fixture.devices=[{serial:'192.168.0.1:5555',model:'Same device',state:'device',wireless:true}]")
            page.get_by_role('button',name='刷新设备' if zh else 'Refresh devices',exact=True).click();settle(page)
            selector=page.get_by_label(label)
            assert selector.input_value()==''
            selector.select_option('192.168.0.1:5555')
            page.get_by_role('button',name='继续卸载' if zh else 'Continue removal',exact=True).click();settle(page)
            resume=page.evaluate("window.__fixture.calls.filter(call=>call.command==='resume').at(-1)")
            assert resume['params']['serial']=='192.168.0.1:5555'
            assert not errors,errors
            context.close()
        browser.close()
    print('PASS: reset consent/cancel, original-record and device-evidence plans, preservation/release/reset impacts, bilingual UI, Android completion, bounded layout; simulated backend only')


def source():
    # Keep Vite cache outside the shared checkout and never write ui/dist.
    with tempfile.TemporaryDirectory(prefix='openwrt-uninstall-ui-') as cache, tempfile.TemporaryFile() as log:
        assert Path(cache).resolve().parent==Path(tempfile.gettempdir()).resolve()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        module=(ROOT/'ui/node_modules/vite/dist/node/index.js').as_uri()
        script='import {createServer} from '+json.dumps(module)+';const server=await createServer('+json.dumps(
            {'root':str(ROOT/'ui'),'cacheDir':cache,'server':{'host':'127.0.0.1','port':port,'strictPort':True}})+');await server.listen();'
        server=subprocess.Popen(['node','--input-type=module','-e',script],cwd=ROOT/'ui',stdout=log,stderr=log)
        try:
            url='http://127.0.0.1:'+str(port)
            deadline=time.monotonic()+20
            while True:
                if server.poll() is not None:
                    log.seek(0);raise RuntimeError(log.read().decode(errors='replace'))
                try:
                    with urlopen(url,timeout=1) as response:
                        if response.status==200:break
                except OSError:
                    if time.monotonic()>deadline:raise RuntimeError('Temporary source server did not start')
                    time.sleep(.1)
            main(url)
        finally:
            server.terminate();server.wait(timeout=10)


if __name__=='__main__':
    source() if '--source' in sys.argv else main(os.environ.get('OPENWRT_UI_TEST_URL','http://127.0.0.1:1420'))
