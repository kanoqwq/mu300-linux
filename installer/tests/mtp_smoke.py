"""Localized informational/warning toasts; simulated events only."""
from playwright.sync_api import sync_playwright
from ui_smoke import BRIDGE, fixture_context, settle


def main():
    bridge = BRIDGE.replace('window.__emitFixture=(change)=>{', '''
window.__noticeFixture=(code)=>callbacks.get(window.__fixture.event)?.({event:'backend',id:1,payload:{type:'notice',data:{code,label:'V50'}}});
window.__emitFixture=(change)=>{''')
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for lang, success, failure in [('zh','已禁用设备的 MTP 接口','未能禁用设备的 MTP 接口'),
                                       ('en','Device MTP interface disabled','Could not disable the device MTP interface')]:
            context = fixture_context(browser, viewport={'width':800,'height':650})
            context.add_init_script(bridge + "localStorage.setItem('language','"+lang+"');localStorage.setItem('onboarding-v1','seen');")
            page = context.new_page()
            page.goto('http://127.0.0.1:1420')
            settle(page)
            page.evaluate("window.__noticeFixture('mtp_disabled')")
            page.get_by_text(success, exact=True).wait_for()
            assert page.locator('[data-sonner-toast][data-type=info]').count() == 1
            page.evaluate("window.__noticeFixture('mtp_disable_failed')")
            page.get_by_text(failure, exact=True).wait_for()
            assert page.locator('[data-sonner-toast]').count() == 1
            assert page.locator('[data-sonner-toast][data-type=warning]').count() == 1
            context.close()
        browser.close()
    print('MTP toast: Chinese/English success/warning, one notification, no confirmation dialog')


if __name__ == '__main__':
    main()
