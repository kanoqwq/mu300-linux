"""Debug-log controls, diagnostic copying and honest progress presentation."""
from playwright.sync_api import sync_playwright
from ui_smoke import fixture_context,BRIDGE,ROOT,layout,settle


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        context=fixture_context(browser,viewport={'width':800,'height':650})
        context.add_init_script(BRIDGE+"localStorage.setItem('onboarding-v1','seen');")
        page=context.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:1420')
        page.evaluate("Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async(text)=>{window.__copied=text}})")
        page.get_by_role('button',name='继续未完成的安装',exact=False).click();settle(page)
        page.get_by_role('button',name='确认安装计划',exact=True).click();settle(page)
        page.get_by_role('checkbox').check()
        page.get_by_role('button',name='确认并开始').click();settle(page)
        toggle=page.get_by_role('button',name='详细日志',exact=False)
        assert toggle.get_attribute('aria-expanded')=='true'
        def event(kind,data):
            page.evaluate('''({kind,data})=>window.__fixture.callbacks.get(window.__fixture.event)({event:'backend',id:1,payload:{type:kind,data}})''',{'kind':kind,'data':data})
        page.evaluate("window.__emitFixture({stage:'backup',status:'running',revision:10})")
        event('log',{'level':'debug','scope':'backup','message':'Backing up boot_a: 67108864 bytes; 1 MiB chunks'})
        event('transfer',{'name':'backup/boot_a','bytes':33554432,'total':67108864,'bytes_per_second':1048576})
        settle(page);layout(page)
        progress=page.locator('progress')
        assert progress.get_attribute('value')=='33554432' and progress.get_attribute('max')=='67108864'
        assert page.locator('.transfer').get_by_text('50%',exact=True).count()==1
        assert '[DEBUG] [backup] Backing up boot_a' in page.locator('.log-panel pre').inner_text()
        event('log',{'level':'debug','scope':'order','message':'OLDER-BLOCK\nolder detail'})
        event('log',{'level':'debug','scope':'order','message':'NEWER-BLOCK\nnewer detail'})
        settle(page)
        text=page.locator('.log-panel pre').inner_text()
        assert text.index('NEWER-BLOCK')<text.index('newer detail')<text.index('OLDER-BLOCK')<text.index('older detail')
        assert page.locator('.log-panel pre').evaluate('(el)=>el.scrollTop')==0
        page.screenshot(path=str(ROOT/'screenshots/backup-progress-debug-zh.png'))
        event('transfer',{'name':'android-bootchain.sh','bytes':2048,'total':2048,'bytes_per_second':1024})
        settle(page)
        assert page.locator('.transfer').get_by_text('100%',exact=True).count()==1
        event('activity',{'name':'checking firmware'})
        settle(page)
        assert page.locator('progress').count()==0
        page.evaluate("window.__emitFixture({stage:'firmware_convert',status:'running',revision:11,details:{tool:'vbc-profile.py'}})")
        settle(page);layout(page)
        assert page.locator('h1').inner_text()=='转换本机音频与蓝牙固件'
        assert page.locator('.activity').get_by_text('m8-openwrt-gui-1.0.0',exact=True).count()==0
        assert page.locator('.activity').get_by_text('e5-openwrt-release · vbc-profile.py',exact=True).count()==1
        page.screenshot(path=str(ROOT/'screenshots/firmware-convert-debug-zh.png'))
        toggle.click();settle(page)
        assert page.locator('.log-panel pre').count()==0
        toggle.click();settle(page)
        assert '[DEBUG] [backup]' in page.locator('.log-panel pre').inner_text()
        page.evaluate("window.__emitFixture({stage:'transfer_install',status:'running',revision:12,details:{}})")
        settle(page)
        bar=page.get_by_role('progressbar',name='当前阶段进度')
        assert bar.get_attribute('aria-valuenow') is None
        assert page.locator('progress').count()==0
        assert page.get_by_text('正在处理，请保持设备供电。',exact=True).count()==1
        page.screenshot(path=str(ROOT/'screenshots/install-activity-debug-zh.png'))
        page.evaluate("window.__emitFixture({stage:'openwrt_boot',status:'waiting_user',revision:13,details:{host:'192.168.9.1',reboot_manually:true}})")
        settle(page);layout(page)
        assert page.locator('h1').inner_text()=='准备进入 OpenWrt'
        assert '用户名 root，默认密码 root' in page.locator('.login-hint').inner_text()
        guidance=page.locator('.boot-guidance')
        assert '已开始重启或已进入 OpenWrt' in guidance.inner_text()
        box=guidance.bounding_box()
        assert box['y']>=page.locator('header').bounding_box()['height'] and box['y']+box['height']<page.locator('.log-panel').bounding_box()['y']
        assert page.get_by_role('button',name='在安全边界暂停',exact=True).bounding_box()['y']<page.locator('.log-panel').bounding_box()['y']
        confirm=page.get_by_role('button',name='我已进入 OpenWrt，开始校验',exact=True)
        assert confirm.bounding_box()['y']+confirm.bounding_box()['height']<page.locator('.log-panel').bounding_box()['y']
        page.get_by_label('设备管理 IP / 主机名').fill('192.168.9.22')
        page.get_by_label('SSH root 密码（不保存）').fill('fixture-private')
        page.screenshot(path=str(ROOT/'screenshots/manual-restart-log-zh.png'))
        confirm.click();settle(page)
        assert page.evaluate("window.__fixture.calls.filter(c=>c.command==='verify_openwrt').at(-1).params")=={'transaction':'a'*32,'host':'192.168.9.22','password':'fixture-private'}
        assert page.locator('h1').inner_text()=='准备进入 OpenWrt'
        page.evaluate("window.__emitFixture({stage:'openwrt_boot',status:'waiting_user',revision:14,details:{host:'192.168.9.22',boot_confirmed:true,reboot_manually:false,verification_error:'Management address belongs to another device / 管理地址对应另一台设备'}})")
        settle(page);layout(page)
        assert '另一台设备' in page.locator('.verification-error').inner_text()
        assert page.get_by_label('设备管理 IP / 主机名').input_value()=='192.168.9.22'
        assert page.get_by_role('button',name='我已进入 OpenWrt，开始校验',exact=True).is_enabled()
        before=guidance.bounding_box()
        page.locator('.page-scroll').evaluate('(el)=>el.scrollTop=el.scrollHeight')
        assert guidance.bounding_box()==before
        page.screenshot(path=str(ROOT/'screenshots/openwrt-endpoint-mismatch-zh.png'))
        page.get_by_label('语言').select_option('en');page.get_by_label('Theme').select_option('dark')
        page.evaluate("window.__emitFixture({stage:'openwrt_boot',status:'waiting_device',revision:15,details:{host:'192.168.9.1',reboot_manually:false}})")
        settle(page);layout(page)
        assert page.locator('h1').inner_text()=='Waiting for OpenWrt to start'
        assert 'automatic restart was requested' in guidance.inner_text()
        assert page.get_by_text('Newest entries first',exact=True).count()==1
        page.screenshot(path=str(ROOT/'screenshots/automatic-restart-log-en.png'))
        page.get_by_label('Language').select_option('zh');page.get_by_label('主题').select_option('light')
        page.evaluate("window.__emitFixture({stage:'backup',status:'failed',revision:16,error:{message:'TimeoutExpired: backup boot_a timed out after 120 seconds',retryable:true,code:'operation_failed'}})")
        event('log',{'level':'error','scope':'exception','message':'Traceback: TimeoutExpired in backup_partition'})
        settle(page);layout(page)
        page.get_by_role('button',name='复制诊断信息给开发者',exact=True).click();settle(page)
        assert 'DEBUG fixture diagnostics' in page.evaluate('window.__copied')
        assert page.evaluate("window.__fixture.requests.includes('diagnostics')")
        assert page.get_by_text('日志已复制',exact=True).count()==1
        page.screenshot(path=str(ROOT/'screenshots/failure-copy-diagnostics-zh.png'))
        page.get_by_label('语言').select_option('en');page.get_by_label('Theme').select_option('dark');settle(page);layout(page)
        assert page.get_by_role('button',name='Copy diagnostics for the developer').count()==1
        assert not errors,errors
        browser.close()
        print('Diagnostics smoke passed: newest-first multiline logs, pinned manual/automatic restart guidance with logs expanded at 800x650, persistent collapse/reopen, real byte progress, honest activity, traceback copying and bounded bilingual layout')


if __name__=='__main__':main()
