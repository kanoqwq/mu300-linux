"""Large eUICC inventories: real view/dialogs, synthetic RPC, no card access."""
import argparse
from pathlib import Path
import runpy
from playwright.sync_api import sync_playwright

TOP = Path(__file__).resolve().parents[2]
fixture = runpy.run_path(str(Path(__file__).with_name('euicc-browser.py')))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=TOP)
    args = parser.parse_args()
    app = next(args.repo/'openwrt'/name for name in ('luci-app-t551m','luci-app-mu300')
               if (args.repo/'openwrt'/name).is_dir())
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
        for lang in ('zh','en','tr'):
            for width in (320,768,1440):
                page = browser.new_page(viewport={'width':width,'height':950})
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('http://euicc.test/**',lambda r:r.fulfill(body='<!doctype html><html><body></body></html>',content_type='text/html'))
                page.goto('http://euicc.test/')
                page.add_style_tag(content='*{box-sizing:border-box}body{margin:0;padding:12px;font:15px Arial}html{--surface:#202329;--text:#eee;--hairline:#383c44;--background:#10141a;--brand:#50cac0}')
                fixture['mount'](page,app,lang)
                page.evaluate('''() => {
                  fixture.profiles=Array.from({length:125},(_,i)=>({
                    iccid:'89123456789012'+String(i).padStart(5,'0'),
                    profileNickname:i===0?'<img src=x onerror=alert(1)>':'Profile '+i,
                    profileName:'Travel '+i,serviceProviderName:i%2?'Carrier B':'Carrier A',
                    profileState:i===0?'enabled':'disabled'
                  }));
                  fixture.profiles[124].profileNickname='İstanbul 124';
                  component.run('scan');
                }''')
                fixture['idle'](page)
                assert page.locator('.eu-profile').count()==10
                assert page.locator('.eu-profile img').count()==0
                assert page.locator('.eu-profile-name').first.inner_text()=='<img src=x onerror=alert(1)>'
                assert page.locator('.eu-previous').is_disabled()
                assert not page.locator('.eu-next').is_disabled()
                assert page.locator('.eu-profile-count').inner_text()=={'zh':'共 125 个配置','en':'125 profiles','tr':'125 profil'}[lang]
                assert page.locator('.eu-manage').first.inner_text()=={'zh':'管理','en':'Manage','tr':'Yönet'}[lang]
                assert page.locator('.eu-profile').first.bounding_box()['height'] < (105 if width<=700 else 65)
                before=page.evaluate('calls.length')
                page.locator('.eu-next').click()
                assert page.locator('.eu-profile-name').first.inner_text()=='Profile 10'
                page.locator('.eu-manage').first.click()
                assert '8912345678901200010' in page.locator('.mud-dlg').inner_text()
                page.locator('.mud-dlg button[data-i="3"]').click() # close disabled profile
                assert page.evaluate('calls.length')==before
                page.locator('.eu-search').fill('Carrier B')
                assert page.locator('.eu-profile').count()==10
                assert page.locator('.eu-profile-name').first.inner_text()=='Profile 1'
                assert page.locator('.eu-previous').is_disabled()
                page.locator('.eu-search').fill('8912345678901200124')
                assert page.locator('.eu-profile').count()==1
                assert page.locator('.eu-profile-name').inner_text()=='İstanbul 124'
                assert page.locator('.eu-next').is_disabled()
                if lang=='tr':
                    page.locator('.eu-search').fill('istanbul')
                    assert page.locator('.eu-profile').count()==1
                page.locator('.eu-search').fill('no such profile')
                assert page.locator('.eu-profile').count()==0
                assert page.locator('.eu-next').is_disabled()
                page.locator('.eu-search').fill('')
                page.locator('.eu-filter').select_option('enabled')
                assert page.locator('.eu-profile').count()==1
                page.locator('.eu-manage').click()
                assert page.locator('.mud-dlg button.warn').count()==0 # enabled profile cannot be deleted
                page.keyboard.press('Escape')
                page.locator('.eu-filter').select_option('')
                page.locator('.eu-page-size select').select_option('50')
                assert page.locator('.eu-profile').count()==50
                page.locator('.eu-next').click();page.locator('.eu-next').click()
                assert page.locator('.eu-profile').count()==25
                assert page.locator('.eu-next').is_disabled()
                # Page position survives repaint; removing many results clamps it.
                page.evaluate('component.paint()')
                assert page.locator('.eu-profile-name').first.inner_text()=='Profile 100'
                page.evaluate('component.data.profiles=component.data.profiles.slice(0,2);component.paint()')
                assert page.locator('.eu-profile').count()==2
                assert page.locator('.eu-next').is_disabled()
                # Context changes while a dialog is open must not mutate a new card.
                before=page.evaluate('calls.filter(c=>c.method==="start").length')
                page.locator('.eu-manage').first.click()
                page.evaluate('component.slot.value="1"')
                page.locator('.mud-dlg button[data-i="0"]').click()
                page.wait_for_selector('.mud-toast.error')
                assert page.evaluate('calls.filter(c=>c.method==="start").length')==before
                assert page.locator('.mud-dlg').count()==0
                # Long names are truncated in the row, never widen the page.
                page.evaluate('component.data.profiles[0].profileNickname="A".repeat(256);component.paint()')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'),(lang,width)
                assert not errors,errors
                page.close()
        browser.close()
    print('eUICC list: 125 profiles, 9 language/width cases, search/filter/paging/dialog identity passed')


if __name__=='__main__':main()
