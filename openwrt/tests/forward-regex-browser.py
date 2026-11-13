"""Forwarding UI: SIM profiles, drafts, localization, bounded history; mocked RPC."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
APP='luci-app-mu300' if (ROOT/'openwrt/luci-app-mu300').is_dir() else 'luci-app-t551m'
WEB=Path(os.environ.get('FORWARD_WEB_ROOT', ROOT/'openwrt'/APP/'htdocs/luci-static/resources'))


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':390,'height':844})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content('<html lang="zh"><style>*{box-sizing:border-box}body{margin:12px}</style></html>')
        page.evaluate('''([common,source])=>{
            window.L={env:{lang:'zh'},resolveDefault:(p,d)=>Promise.resolve(p).catch(()=>d)};
            window.calls=[];window.configs={};window.mode='shared';window.historyCleared=false;
            for(const profile of ['shared','sim0','sim1']) configs[profile]={enabled:1,method:'webhook',template_language:'en',webhook_url:'https://example.com/'+profile,webhook_method:'POST',webhook_content_type:'application/json',webhook_timeout:12,smtp:{port:465,password_configured:true}};
            window.state=(profile='shared')=>({profile,mode,sim_slots:2,config:configs[profile],status:{enabled:true,method:'webhook',power_supported:true,history:historyCleared?[]:Array.from({length:30},(_,i)=>({time:1700000000+i,kind:'sms',slot:i%2,method:'webhook',result:'sent',http_code:200}))}});
            const rpc={declare:s=>(...args)=>{
                calls.push({method:s.method,args});
                if(s.method==='forward_log_clear')historyCleared=true;
                if(s.method==='forward_get'||s.method==='forward_status')return Promise.resolve(state(args[0]));
                if(s.method==='forward_set'){const value=JSON.parse(args[0]);configs[value.profile]={...configs[value.profile],...value};mode=value.mode;}
                return Promise.resolve({ok:1});
            }};
            const M=new Function('rpc','baseclass',common)(rpc,{extend:x=>x});M.localizeMenu=()=>{};
            window.fwd=new Function('view','poll','M',source)({extend:x=>x},{add:f=>window.tick=f},M);
            document.body.append(fwd.render(state()));
        }''',[(WEB/'mu300/common.js').read_text(encoding='utf-8'),(WEB/'view/mu300/forward.js').read_text(encoding='utf-8')])
        assert page.locator('#mud-fwd-history').evaluate('(el)=>el.scrollHeight>el.clientHeight')
        assert page.locator('#mud-fwd-history .mud-fwd-event').count()==30
        page.locator('#mud-fwd-clear').click()
        page.get_by_role('dialog').get_by_role('button',name='取消',exact=True).click()
        assert not page.evaluate('calls.some(c=>c.method==="forward_log_clear")')
        page.locator('#mud-fwd-clear').click()
        page.get_by_role('dialog').get_by_role('button',name='确定',exact=True).click()
        page.wait_for_function('historyCleared')
        page.wait_for_function("!document.querySelector('#mud-fwd-history .mud-fwd-event')")
        assert not page.evaluate('calls.some(c=>c.method==="forward_set")')
        page.locator('#mud-fwd-webhook_url').fill('https://example.com/draft')
        page.evaluate('()=>tick()')
        assert page.locator('#mud-fwd-webhook_url').input_value().endswith('/draft')
        page.locator('#mud-fwd-test').click()
        assert not page.evaluate('calls.some(c=>c.method==="forward_test")')
        page.locator('#mud-fwd-profile').select_option('sim1')
        page.get_by_role('dialog').get_by_role('button',name='确定',exact=True).click()
        page.wait_for_function("fwd.profile==='sim1'")
        assert page.locator('#mud-fwd-webhook_url').input_value().endswith('/sim1')
        page.locator('#mud-fwd-mode').select_option('per_sim')
        page.locator('#mud-fwd-webhook_expect').fill('ok=true')
        page.locator('#mud-fwd-save').click()
        page.get_by_role('dialog').get_by_role('button',name='保存',exact=True).click()
        page.wait_for_function("calls.some(c=>c.method==='forward_set')&&!fwd.q('save').disabled")
        saved=page.evaluate("JSON.parse(calls.find(c=>c.method==='forward_set').args[0])")
        assert saved['profile']=='sim1' and saved['mode']=='per_sim'
        assert saved['webhook_expect']=='ok=true'
        # Filter controls preserve patterns literally, including meaningful spaces.
        assert page.locator('#mud-fwd-regex-details').evaluate('(el)=>el.tagName')=='DIV'
        assert page.locator('#mud-fwd-number_filter_mode').is_visible()
        assert page.locator('#mud-fwd-blacklist_phone, #mud-fwd-blacklist_keywords').count()==0
        page.locator('#mud-fwd-number_filter_mode').select_option('whitelist')
        page.locator('#mud-fwd-number_filter_regex').fill('^(\\+86)?106[0-9]+$')
        page.locator('#mud-fwd-text_filter_mode').select_option('blacklist')
        page.locator('#mud-fwd-text_filter_regex').fill('^ leading $\nPROMO|广告')
        page.locator('#mud-fwd-filter_ignore_case').check()
        page.evaluate('()=>tick()')
        assert page.locator('#mud-fwd-text_filter_regex').input_value()=='^ leading $\nPROMO|广告'
        page.locator('#mud-fwd-save').click()
        page.get_by_role('dialog').get_by_role('button',name='保存',exact=True).click()
        page.wait_for_function("calls.filter(c=>c.method==='forward_set').length===2&&!fwd.q('save').disabled")
        saved=page.evaluate("JSON.parse(calls.filter(c=>c.method==='forward_set').at(-1).args[0])")
        assert saved['number_filter_mode']=='whitelist' and saved['text_filter_mode']=='blacklist'
        assert saved['number_filter_regex']==r'^(\+86)?106[0-9]+$'
        assert saved['text_filter_regex']=='^ leading $\nPROMO|广告' and saved['filter_ignore_case']==1
        assert page.locator('#mud-fwd-number_filter_mode').is_visible()
        assert 'blacklist_phone' not in saved and 'blacklist_keywords' not in saved
        p=page.evaluate('()=>fwd.payload()');p['number_filter_regex']=''
        assert page.evaluate('(p)=>fwd.validate(p)',p)=='号码白名单至少填写一条正则'
        p['number_filter_regex']='x'*513
        assert page.evaluate('(p)=>fwd.validate(p)',p)=='正则条数或长度超过上限'
        p['number_filter_regex']='['
        assert page.evaluate('(p)=>fwd.validate(p)',p)==''  # Target ucode owns syntax validation.
        page.evaluate("()=>{configs.sim1.legacy_blacklist_present=1}")
        for language in ['en','tr']:
            page.evaluate("lang=>{L.env.lang=lang;document.documentElement.lang=lang;fwd.root.remove();document.body.append(fwd.render(state('sim1')))}",language)
            assert page.locator('#mud-fwd-legacy-filter-note').is_visible()
            assert page.locator('#mud-fwd-number_filter_mode').is_visible()
            untranslated=page.evaluate(r"fwd.root.innerText.split('\n').filter(s=>/[\u3400-\u9fff]/.test(s))")
            assert not untranslated,(language,untranslated)
        for width in [390,1280]:
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        browser.close()
    print('PASS: profile switching and saves, unsaved test guard, polling preserves drafts, history scroll, en/tr, mobile and desktop')


if __name__=='__main__':main()
