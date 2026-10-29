"""Render the shipped dashboard with failed/recovered cache snapshots, no modem."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

TOP=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--source',type=Path,default=TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/home.js')
ap.add_argument('--common',type=Path,default=TOP/'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js')
args=ap.parse_args();SOURCE=args.source

with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    page=browser.new_page(viewport={'width':390,'height':844})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content('<main></main>')
    page.evaluate('''([source,sharedSource])=>{
        window.neighborPaints=0;
        const shared=new Function('rpc','baseclass','L',sharedSource)(
            {declare:()=>()=>Promise.resolve({})},{extend:o=>o},{env:{lang:'zh'}});
        const M={...shared,
            v:id=>document.getElementById('mud-'+id),
            set:(id,text)=>{M.v(id).textContent=text ?? '--';},
            translate:s=>s,localize(){},
            esc:s=>String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
            qLabel:rsrp=>rsrp==null?'未知':'良好',qCol:()=> '#123456',
            qScore:s=>s.rsrp==null?null:8,
            carrierName:op=>op?.name || '--',PLMN_CN:{},
            neighborRows:c=>{neighborPaints++;return '<tr><td>'+M.esc(c.neigh?.[0]?.pci ?? '--')+'</td></tr>';}
        };
        window.app=new Function('view','uci','M','SimRadio',source)({extend:o=>o},{},M,{});
        const root=document.querySelector('main');root.innerHTML=app.html();app._root=root;
        app.identShown=true;
        window.show=st=>app.update({info:{modem:{alive:1,atd:1}},...st});
        window.text=id=>M.v(id).textContent;
        window.freeze=value=>{
            if(value && typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}
            return value;
        };
    }''',[SOURCE.read_text(encoding='utf-8'),args.common.read_text(encoding='utf-8')])
    result=page.evaluate('''()=>{
        const check=(ok,message)=>{if(!ok)throw Error(message);};
        const slow=freeze({ts:100,error:'AT backend did not answer'});
        const signal=freeze({ts:101,card:0,cfun:1,reg:{stat:1,act:7},reg5g:null,
            sig_src:'lte',sig:{rsrp:-88,rsrq:-10},lte:{band:41,pci:123,earfcn:40000},nr:null});

        show({cell:slow,sig:{ts:100,error:'AT backend did not answer'}});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='无应答','real failures must be visible');
        show({cell:slow,sig:signal});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='4G','successful signal must clear old failure');
        check(text('rsrp').includes('-88.0'),'signal must repaint immediately');
        check(text('reg')==='已注册','registration must recover');
        check(!app.lastCell.error && slow.error,'must not mutate RPC snapshots');
        const paints=neighborPaints;
        show({cell:slow,sig:{...signal,ts:102}});
        check(neighborPaints===paints,'fast-only polls must not repaint unchanged slow sections');

        // Partial recovery has useful CESQ data even though engineering reads are pending.
        show({cell:slow,sig:{...signal,ts:103,partial:1,sig_src:'cesq',sig:{rsrp:-95},lte:null,nr:null}});
        check(!app.lastCell.error && text('rsrp').includes('-95.0'),'partial recovery must use its fresh fallback signal');
        check(document.querySelector('#mud-rat').firstChild.nodeValue!=='无应答','partial recovery must clear error');

        show({cell:{ts:110,error:'new failure'},sig:signal});
        check(app.lastCell.error==='new failure','older good signal must not erase newer failure');
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='无应答','newer failure must remain visible');

        const full=freeze({...signal,ts:200,ident:{imei:'fixture-zero',model:'fixture',fw:'fixture-fw',
            imsi:'fixture-imsi',iccid:'fixture-iccid',msisdn:'fixture-number'},
            operator:{name:'fixture carrier',act:7},qos:{qci:9},neigh:[{pci:456}],
            sig_src:'nr',sig:{rsrp:-80},lte:null,nr:{band:41,pci:321,arfcn:500000}});
        show({cell:full,sig:null});
        const staged=freeze({...full,ts:201,partial:1,neigh_pending:1,ident:null,qos:null,neigh:null});
        const partial=freeze({ts:202,card:0,partial:1,cfun:1,reg:{stat:1},reg5g:null,
            sig_src:'cesq',sig:null,lte:null,nr:null});
        show({cell:staged,sig:partial});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='5G SA','healthy partial read must preserve engineering signal');
        check(text('rsrp').includes('-80.0'),'partial read must not blank healthy engineering metrics');
        check(text('imei1')==='fixture-zero' && text('qci')==='9' && text('neigh').includes('456'),'same SIM must keep completed slow tiers');

        // A different card at the same slow timestamp must repaint and never inherit identity.
        const switched=freeze({...staged,card:1});
        show({cell:switched,sig:{...partial,card:1}});
        check(text('imei1')==='--' && text('qci')==='--' && !text('neigh').includes('456'),'SIM change must clear previous card details');
        check(app.lastCell.card===1,'selected card must follow fresh snapshot');
        show({cell:full,sig:{...signal,card:1,ts:203}});
        check(app.lastCell.card===1 && !app.lastCell.ident && text('imei1')==='--','mismatched slow/fast cards must not merge');

        show({cell:freeze({ts:204,error:'both failed'}),sig:freeze({ts:205,error:'both failed'})});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='无应答','continued failure must not reuse last successful signal');

        const simInfo=state=>({modem:{alive:1,atd:1,sims:[{slot:0,state},{slot:1,state:'ready'}]}});
        const off=freeze({...signal,ts:220,cfun:0,lte:null,nr:null});
        show({info:simInfo('absent'),cell:{...full,ts:220,cfun:0},sig:null});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='未插卡','confirmed absence must override RF-off and stale RAT');
        check(text('reg')==='未插卡' && text('rsrp')==='--','absence must clear stale network and signal');
        check(text('imei1')==='fixture-zero' && text('fwmodel')==='fixture' && text('fw')==='fixture-fw','equipment identity must remain available without a SIM');
        check(text('imsi')==='--' && text('iccid')==='--' && text('msisdn')==='--','removed SIM identifiers must not remain visible');
        check(!app.lastCell.ident.imsi && !app.lastCell.ident.iccid && !app.lastCell.ident.msisdn,'reveal must not restore identifiers from a removed SIM');
        check(full.ident.imsi==='fixture-imsi','absence filtering must not mutate the source cache');
        show({info:simInfo('unknown'),cell:off,sig:null});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='无线电已关','unknown SIM must not be guessed absent');
        show({info:simInfo('ready'),cell:off,sig:null});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='无线电已关','inserted SIM with RF off keeps its real status');
        show({info:simInfo('absent'),cell:{...off,card:1},sig:null});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='无线电已关','other SIM absence must not cross slots');
        show({info:simInfo('absent'),cell:{ts:221,card:0,error:'AT unavailable'},sig:null});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='未插卡','known absence must not become an engineering read error');
        show({info:simInfo('ready'),cell:{...full,ts:222},sig:null});
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='5G SA','fresh insertion/registration must restore RAT');
        app._slowCell=null; app.lastCell=null; app.lastFullTs=null;
        show({info:simInfo('absent'),cell:{...full,ts:223,cfun:0,ident:{imei:'cold-imei',model:'cold-model',fw:'cold-fw'}},sig:null});
        check(text('imei1')==='cold-imei' && text('fw')==='cold-fw','a fresh no-SIM page must render newly read equipment identity');
        check(document.querySelector('#mud-rat').firstChild.nodeValue==='未插卡','equipment identity must not fabricate registration');
        return {recovery:true,partial_recovery:true,older_signal_rejected:true,
            same_sim_details_preserved:true,sim_isolation:true,real_failures_visible:true,input_immutable:true};
    }''')
    assert not errors,errors
    browser.close()
print('PASS:',result)
