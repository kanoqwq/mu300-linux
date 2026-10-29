"""Installation reboot button: bound request, loading, failure recovery and i18n."""
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
        page.get_by_role('button',name='继续未完成的安装',exact=False).click();settle(page)
        page.get_by_role('button',name='确认安装计划',exact=True).click();settle(page)
        page.get_by_role('checkbox').check()
        page.get_by_role('button',name='确认并开始').click();settle(page)
        assert page.get_by_role('button',name='重启设备',exact=True).count()==0
        page.evaluate("window.__emitFixture({stage:'openwrt_boot',status:'waiting_user',revision:10,details:{host:'192.168.9.1',reboot_manually:true}})")
        settle(page);layout(page)
        restart=page.get_by_role('button',name='重启设备',exact=True)
        assert restart.is_enabled()
        restart.click();settle(page)
        assert page.get_by_role('button',name='正在请求重启…',exact=True).is_disabled()
        assert page.evaluate("window.__fixture.calls.filter(x=>x.command==='reboot_device').map(x=>x.params)")==[{'transaction':'a'*32}]
        page.evaluate("window.__emitFixture({stage:'openwrt_boot',status:'waiting_device',revision:11,details:{host:'192.168.9.1',reboot_manually:false}})")
        settle(page);layout(page)
        assert page.get_by_role('button',name='重启设备',exact=True).count()==0
        page.evaluate("window.__emitFixture({stage:'openwrt_boot',status:'waiting_user',revision:12,details:{host:'192.168.9.1',reboot_manually:true,verification_error:'ADB unavailable / ADB 不可用'}})")
        settle(page)
        assert page.get_by_role('button',name='重启设备',exact=True).is_enabled()
        page.evaluate("window.__fixture.fail='reboot_device'")
        page.get_by_role('button',name='重启设备',exact=True).click();settle(page)
        assert page.get_by_role('button',name='重启设备',exact=True).is_enabled()
        page.get_by_role('button',name='关闭',exact=True).click() if page.get_by_role('button',name='关闭',exact=True).count() else None
        page.get_by_label('语言').select_option('en');page.get_by_label('Theme').select_option('dark')
        settle(page);layout(page)
        assert page.get_by_role('button',name='Restart device',exact=True).is_enabled()
        page.evaluate("window.__emitFixture({stage:'transfer_install',status:'running',revision:13,details:{}})")
        settle(page)
        assert page.get_by_role('button',name='Restart device',exact=True).count()==0
        assert not errors,errors
        browser.close()
        print('Device reboot UI passed: transaction binding, loading, no automatic request, expected disconnect view, retry and bilingual layout')


if __name__=='__main__':main()
