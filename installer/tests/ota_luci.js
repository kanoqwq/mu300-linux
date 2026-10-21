// Execute the shipped LuCI view with RPC/UI fixtures; never contacts a device.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../openwrt/luci-app-unisoc-ota/htdocs/luci-static/resources/view/system/unisoc-ota.js'), 'utf8');
let messages;
const idle = () => ({identity:{version:'test-r2'},transaction:{id:'a'.repeat(64),status:'complete'},job:{status:'complete',result:{}}});
function fixture(write=true) {
    const state={calls:[],notifications:[],ticks:new Set(),modal:[],writable:write,status:idle(),rejectStart:false,rejectRollback:false,rejectStatus:false,rejectReboot:null,reconnects:0,rollbackCheck:{available:true,reason:'ready',conflicts:[]}};
    const E=(tag,attrs={},children=[])=>({tag,...attrs,children,appendChild(node){this.children.push(node);}});
    const rpc={declare:({method})=>(...args)=>{
        state.calls.push(method);
        if(method==='status')return state.rejectStatus?Promise.reject(Error('offline')):Promise.resolve(state.status);
        if(method==='inspect')return Promise.resolve({id:'a'.repeat(64),applicable:true,manifest:{base_version:'r1',target_version:'r2',operations:[]}});
        if(method==='start'&&state.rejectStart)return Promise.reject(Error('start rejected'));
        if(method==='rollback_check')return Promise.resolve(state.rollbackCheck);
        if(method==='reboot'&&state.rejectReboot)return Promise.reject(Error(state.rejectReboot));
        if(method==='rollback'&&state.rejectRollback)return Promise.reject(Error('rollback rejected'));
        return Promise.resolve({status:'running'});
    }};
    const ui={uploadFile:()=>Promise.resolve(),showModal:(_title,nodes)=>{state.modal=nodes;},hideModal:()=>{},addNotification:(_a,node)=>state.notifications.push(node.children),awaitReconnect:()=>{state.reconnects++;}};
    const view=new Function('view','rpc','ui','poll','E','document','L',source)({extend:v=>v},rpc,ui,{add:fn=>state.ticks.add(fn),remove:fn=>state.ticks.delete(fn)},E,{documentElement:{lang:'en'}},{hasViewPermission:()=>state.writable});
    const all=nodes=>nodes.flatMap(n=>!n||typeof n!=='object'?[]:[n,...all(Array.isArray(n.children)?n.children:[])]);
    const root=view.render([messages,state.status]);
    state.button=name=>all([root,...state.modal]).find(n=>n.tag==='button'&&n.children===name);
    state.flush=()=>new Promise(resolve=>setImmediate(resolve));
    return state;
}
async function main(){
    const shared=fs.readFileSync(path.join(__dirname,'../../openwrt/luci-app-unisoc-ota/htdocs/luci-static/resources/unisoc-ota/errors.js'));
    messages=await import('data:text/javascript;base64,'+shared.toString('base64'));
    assert.match(messages.explainError('RuntimeError: {"error":"Device/base mismatch: version; device=r1; patch base=r2; patch target=r3"}').message,/设备当前为 r1/);
    assert.match(messages.explainError('Rollback conflict: usr/lib/unisoc-ota/rpc.py; changed=sha256,mode').message,/内容、权限/);
    assert.match(messages.explainError('unrecognized raw failure',true).message,/Technical details|technical details/);
    const f=fixture(false);
    for(const name of ['Import patch','Roll back last patch','Reboot'])assert(f.button(name).disabled);
    const g=fixture();g.status={identity:{version:'r2'},job:{status:'running'}};
    g.rejectStart=true;
    await g.button('Import patch').onclick();
    g.button('Apply patch').click();
    await g.flush();
    // A lost start reply must check the actual device state, not unlock a
    // possibly running transaction or leave the UI stuck after a rejection.
    assert(g.calls.includes('status'),'Start failure never rechecks device status');
    assert(g.button('Import patch').disabled,'Running device unlocked after lost start response');
    g.status=idle();
    for(const tick of [...g.ticks])await tick();
    assert(!g.button('Import patch').disabled,'Start error permanently disabled import');
    const h=fixture();h.rejectRollback=true;
    await h.button('Roll back last patch').onclick();
    h.button('Roll back').click();
    await h.flush();
    assert(h.calls.includes('status'),'Rollback failure never rechecks device status');
    assert(!h.button('Import patch').disabled);
    const p=fixture();
    await p.button('Import patch').onclick();p.button('Apply patch').click();await p.flush();
    p.rejectStatus=true;
    for(const tick of [...p.ticks])await tick();
    assert.equal(p.notifications.length,1,'Polling outage was not shown');
    for(const tick of [...p.ticks])await tick();
    assert.equal(p.notifications.length,1,'One outage spammed notifications');
    p.rejectStatus=false;
    for(const tick of [...p.ticks])await tick();
    assert(!p.button('Import patch').disabled,'Polling never recovered');
    const c=fixture();c.rollbackCheck={available:false,reason:'conflict',conflicts:[{path:'usr/lib/unisoc-ota/rpc.py',fields:['mode']}]};
    await c.button('Roll back last patch').onclick();
    assert(!c.calls.includes('rollback'),'Conflicting rollback started writes');
    assert(!c.button('Roll back'),'Conflicting rollback offered confirmation');
    for(const rejection of [null,'XHR request aborted by browser']) {
        const r=fixture();r.rejectReboot=rejection;
        await r.button('Reboot').onclick();await r.button('Reboot').onclick();
        assert.equal(r.reconnects,1,'Reboot did not enter reconnect flow');
        assert.equal(r.notifications.length,0,'Expected reboot disconnect produced an error');
        assert.equal(r.calls.filter(m=>m==='reboot').length,1,'Repeated reboot was accepted');
    }
    const b=fixture();b.rejectReboot='Update recovery is pending';
    await b.button('Reboot').onclick();
    assert.equal(b.reconnects,0,'A rejected reboot was treated as a restart');
    assert.equal(b.notifications.length,1,'Business rejection was hidden');
    console.log('PASS LuCI permission, rejected/lost start, rollback failure, polling outage and recovery');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
