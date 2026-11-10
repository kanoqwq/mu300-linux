"""Real LuCI view/common module with synthetic RPC; no hardware or carrier traffic."""
import argparse
import io
from pathlib import Path
import qrcode
from playwright.sync_api import sync_playwright

TOP = Path(__file__).resolve().parents[2]


def mount(page, app, lang='zh', writable=True):
    common = (app/'htdocs/luci-static/resources/mu300/common.js').read_text(encoding='utf-8')
    source = (app/'htdocs/luci-static/resources/view/mu300/euicc.js').read_text(encoding='utf-8')
    page.evaluate('''async ([common,source,lang,writable])=>{
      window.calls=[];window.preview=false;window.hold=false;window.rejectStart=false;window.statusBroken=false;
      window.fixture={eid:'89049032000100123450001234567890',firmware:'Test',free_bytes:80000,ses:3,
        profiles:[{iccid:'8912345678901234567',profileState:'enabled',profileNickname:'<img src=x onerror=alert(1)>',serviceProviderName:'Example',profileName:'Data'}],notifications:[]};
      window.task=null;
      const rpc={declare:o=>async(...args)=>{
        if(o.method==='sim_get')return {slots:2,current:'0',physical:{supported:true,model:'f50pro',logical_cards:[3,4]}};
        if(o.object!=='unisoc-euicc')return {};
        calls.push({method:o.method,args});
        if(o.method==='start'){
          task=Object.fromEntries(o.params.map((k,i)=>[k,args[i]]));task.ok=1;task.state='running';
          if(rejectStart)throw Error('connection lost');return task;
        }
        if(o.method==='answer'){preview=false;if(!args[1]){task.state='failed';task.error='cancelled';}return {ok:1};}
        if(o.method==='cancel'){hold=false;task.state='failed';task.error='cancelled';return {ok:1};}
        if(preview&&task.action==='download')return {...task,preview:true,metadata:{profileName:'Example download',serviceProviderName:'Example',iccid:'8912345678901234567'}};
        if(statusBroken)throw Error('connection lost');
        if(hold)return {...task,stage:'reading_profiles'};
        if(task.state==='running'){
          const profile=fixture.profiles.find(p=>p.iccid===task.iccid);
          if(task.action==='disable')profile.profileState='disabled';
          if(task.action==='enable')profile.profileState='enabled';
          if(task.action==='nickname')profile.profileNickname=task.nickname;
          if(task.action==='delete')fixture.profiles=[];
          task.state='done';task.result=structuredClone(fixture);
        }return task;
      }};
      window.L={env:{lang},hasViewPermission:()=>writable,resource:p=>'http://euicc.test/'+p};
      window._=s=>s;
      const M=new Function('rpc','baseclass',common)(rpc,{extend:x=>x});
      window.component=new Function('view','rpc','ui','M',source)({extend:x=>x},rpc,{},M);
      document.body.replaceChildren(component.render(await component.load()));
    }''', [common, source, lang, writable])


def idle(page):
    page.wait_for_function('!component.busy && !component.mask')


def main():
    p=argparse.ArgumentParser();p.add_argument('--repo', type=Path, default=TOP)
    a=p.parse_args();app=next(a.repo/'openwrt'/n for n in ('luci-app-t551m','luci-app-mu300') if (a.repo/'openwrt'/n).exists())
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
        count=0
        for lang in ('zh','en','tr'):
          for theme in ('bootstrap','aurora-light','aurora-dark'):
            for width in (320,768,1440):
              page=browser.new_page(viewport={'width':width,'height':900})
              errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
              page.route('http://euicc.test/**',lambda route:route.fulfill(body='<!doctype html><html><body></body></html>',content_type='text/html'))
              page.goto('http://euicc.test/')
              colors={'bootstrap':'--background-color:#fff;--background-color-high:#f3f3f3;--text-color:#222;--border-color:#ddd',
                      'aurora-light':'--surface:#fff;--text:#222;--hairline:#ddd;--background:#f6f7f8;--brand:#168e86',
                      'aurora-dark':'--surface:#202329;--text:#eee;--hairline:#383c44;--background:#10141a;--brand:#50cac0'}[theme]
              page.add_style_tag(content='*{box-sizing:border-box}body{margin:0;padding:12px;font:15px Arial}html{'+colors+'}')
              mount(page,app,lang)
              assert page.locator('h2').inner_text()=={'zh':'eSIM 管理','en':'eSIM management','tr':'eSIM yönetimi'}[lang]
              assert not page.evaluate('calls.length')
              assert page.locator('.eu-heading').evaluate("n=>n.tagName")=='DIV'
              page.evaluate('hold=true');page.locator('.eu-top button').click()
              page.wait_for_selector('.eu-modal')
              assert page.locator('.eu-top .eu-slot').is_disabled()
              assert page.evaluate('getComputedStyle(document.querySelector(".eu-modal .mud-spin")).animationName')=='euiccspin'
              page.evaluate('hold=false');idle(page)
              assert page.locator('.eu-profile').count()==1
              assert page.locator('.eu-profile img').count()==0
              assert page.locator('.eu-profile button').count()==1
              assert page.locator('.eu-profile .eu-manage').is_visible()
              # Assert the rendered gutters, including stacked mobile cards.
              gaps=page.evaluate('''() => {
                const rect=n=>n.getBoundingClientRect();
                const a=rect(component.info),b=rect(component.profiles);
                const body=rect(component.root.querySelector('.eu-body'));
                const details=rect(component.root.querySelector('details'));
                const d=rect(component.download),n=rect(component.notes);
                return [b.top-a.bottom,body.top-b.bottom,details.top-body.bottom,
                        innerWidth<=700?n.top-d.bottom:n.left-d.right];
              }''')
              assert all(gap>=15.5 for gap in gaps),(lang,theme,width,gaps)
              toolbar=page.evaluate('''() => {
                const box=s=>component.root.querySelector(s).getBoundingClientRect();
                const label=box('.eu-field-label'),select=box('.eu-slot'),fields=box('.eu-top-fields'),button=box('.eu-read');
                return {labelBottom:label.bottom,selectTop:select.top,selectWidth:select.width,
                        fieldsWidth:fields.width,fieldsBottom:fields.bottom,fieldsRight:fields.right,
                        buttonTop:button.top,buttonRight:button.right,buttonHeight:button.height};
              }''')
              assert toolbar['selectTop']>=toolbar['labelBottom']+6
              assert toolbar['buttonHeight']>=39.5
              if width<=700:
                assert abs(toolbar['selectWidth']-toolbar['fieldsWidth'])<1
                assert toolbar['buttonTop']>=toolbar['fieldsBottom']+11
                assert abs(toolbar['buttonRight']-toolbar['fieldsRight'])<1


              assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'),(lang,theme,width)
              assert not errors,errors
              page.close();count+=1
        # Typed confirmation, source context, reconnect and QR behavior use the real dialogs.
        page=browser.new_page(viewport={'width':390,'height':850})
        page.route('http://euicc.test/**',lambda r:r.fulfill(body='<!doctype html><html><body></body></html>',content_type='text/html'))
        page.route('http://euicc.test/mu300/vendor/jsQR.js',lambda r:r.fulfill(body=(app/'htdocs/luci-static/resources/mu300/vendor/jsQR.js').read_bytes(),content_type='application/javascript'))
        page.goto('http://euicc.test/');mount(page,app)
        page.locator('.eu-top button').click();idle(page)
        page.locator('.eu-profile .eu-manage').click();page.locator('.mud-dlg button[data-i="0"]').click()
        page.locator('.mud-dlg input').fill('YES');page.locator('.mud-dlg button[data-r="1"]').click()
        assert page.evaluate('calls.filter(c=>c.method==="start").length')==1
        page.locator('.eu-profile .eu-manage').click();page.locator('.mud-dlg button[data-i="0"]').click()
        page.locator('.mud-dlg input').fill('yes');page.locator('.mud-dlg button[data-r="1"]').click();idle(page)
        assert page.evaluate('fixture.profiles[0].profileState')=='disabled'
        page.locator('.eu-profile .eu-manage').click();page.locator('.mud-dlg button[data-i="1"]').click();page.locator('.mud-dlg input').fill('Renamed')
        page.locator('.mud-dlg button[data-r="1"]').click();idle(page)
        assert page.locator('.eu-profile-name').inner_text()=='Renamed'
        qr=io.BytesIO();qrcode.make('LPA:1$rsp.example$test').save(qr,format='PNG')
        page.locator('input[type=file]').set_input_files({'name':'test.png','mimeType':'image/png','buffer':qr.getvalue()})
        page.wait_for_function('component.activation.value==="LPA:1$rsp.example$test"')
        page.locator('.eu-top .eu-slot').select_option('1')
        assert page.evaluate('component.data') is None
        page.evaluate('rejectStart=true;statusBroken=true');page.locator('.eu-top button').click()
        page.wait_for_function('!component.busy && component.job')
        assert page.locator('.eu-top button').is_disabled()
        saved=page.evaluate('sessionStorage.getItem("unisoc-euicc-job")');assert saved
        before=page.evaluate('calls.filter(c=>c.method==="start").length')
        page.evaluate('statusBroken=false;component.watch()');idle(page)
        assert page.evaluate('calls.filter(c=>c.method==="start").length')==before
        assert page.evaluate('sessionStorage.getItem("unisoc-euicc-job")') is None
        # Select a detected security element, and confirm/decline real metadata dialogs.
        page.evaluate('rejectStart=false')
        assert page.locator('.eu-top select').nth(1).is_visible()
        page.locator('.eu-top select').nth(1).select_option('A06573746B6D65FFFF4953442D522031');idle(page)
        assert page.evaluate('calls.filter(c=>c.method==="start").at(-1).args[10]')=='A06573746B6D65FFFF4953442D522031'
        for accept in (True,False):
            page.evaluate('() => {preview=true;component.run("download",{activation:"LPA:1$rsp.example$example",confirm:"yes"});}')
            page.wait_for_selector('.mud-dlg')
            assert 'Example download' in page.locator('.mud-dlg').inner_text()
            page.locator('.mud-dlg button[data-r="%s"]' % ('1' if accept else '0')).click();idle(page)
            assert page.evaluate('calls.filter(c=>c.method==="answer").at(-1).args[1]')==accept
            if not accept:
                page.locator('.eu-top button').click();idle(page)
        page.evaluate('hold=true;component.run("download",{activation:"LPA:1$rsp.example$example",confirm:"yes"})')
        page.wait_for_selector('.eu-modal button');page.locator('.eu-modal button').click();idle(page)
        assert page.evaluate('calls.filter(c=>c.method==="cancel").length')==1
        # A completed TLS failure must be visible even when the status row is
        # scrolled out of view. It is not a lost RPC and must never replay.
        page.locator('.eu-top button').click();idle(page)
        before=page.evaluate('calls.filter(c=>c.method==="start").length')
        page.evaluate('''() => {
          task.state='failed';task.error='server_tls';component.job=task.job;
          component.watch();
        }''');idle(page)
        assert page.locator('.mud-toast.error').last.is_visible()
        assert '服务器证书校验失败' in page.locator('.mud-toast.error').last.inner_text()
        assert page.evaluate('component.job') is None
        assert page.evaluate('calls.filter(c=>c.method==="start").length')==before
        assert page.evaluate('sessionStorage.getItem("unisoc-euicc-job")') is None
        # Aurora styles every header as a sticky navigation bar. View titles
        # must stay in normal flow and cannot cover the real navbar on scroll.
        page.add_style_tag(content='header{position:sticky;top:0;z-index:40;background:#18212b;height:56px;padding:16px}')
        page.evaluate('''() => {
          document.querySelectorAll('.mud-toast').forEach(n=>n.remove());
          const nav=document.createElement('header');nav.id='theme-nav';nav.textContent='Navigation';
          document.body.prepend(nav);component.root.style.marginBottom='1200px';window.scrollTo(0,220);
        }''')
        assert page.locator('.eu-heading').evaluate('n=>getComputedStyle(n).position')=='static'
        assert page.evaluate("!!document.elementFromPoint(30,20)?.closest('#theme-nav')")
        page.evaluate('window.scrollTo(0,0)')
        mount(page,app,writable=False)
        assert page.locator('.eu-top button').is_disabled()
        page.close();browser.close()
    print(f'eUICC browser: {count} layout/language/theme cases plus confirmation/QR/timeout/ACL/SE/download-preview/cancel passed')


if __name__=='__main__':main()
