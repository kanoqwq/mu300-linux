// Execute the shipped LuCI view with RPC/UI fixtures; never contacts a device.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../openwrt/luci-app-unisoc-ota/htdocs/luci-static/resources/view/system/unisoc-ota.js'), 'utf8');
const idle = () => ({identity:{version:'test-r2'},transaction:{id:'a'.repeat(64),status:'complete'},job:{status:'complete',result:{}}});
function fixture(write=true) {
    const state={calls:[],notifications:[],ticks:new Set(),modal:[],writable:write,status:idle(),rejectStart:false,rejectRollback:false,rejectStatus:false};
    const E=(tag,attrs={},children=[])=>({tag,...attrs,children,appendChild(node){this.children.push(node);}});
    const rpc={declare:({method})=>(...args)=>{
        state.calls.push(method);
        if(method==='status')return state.rejectStatus?Promise.reject(Error('offline')):Promise.resolve(state.status);
        if(method==='inspect')return Promise.resolve({id:'a'.repeat(64),applicable:true,manifest:{base_version:'r1',target_version:'r2',operations:[]}});
        if(method==='start'&&state.rejectStart)return Promise.reject(Error('start rejected'));
        if(method==='rollback'&&state.rejectRollback)return Promise.reject(Error('rollback rejected'));
        return Promise.resolve({status:'running'});
    }};
    const ui={uploadFile:()=>Promise.resolve(),showModal:(_title,nodes)=>{state.modal=nodes;},hideModal:()=>{},addNotification:(_a,node)=>state.notifications.push(node.children),awaitReconnect:()=>{}};
    const view=new Function('view','rpc','ui','poll','E','document','L',source)({extend:v=>v},rpc,ui,{add:fn=>state.ticks.add(fn),remove:fn=>state.ticks.delete(fn)},E,{documentElement:{lang:'en'}},{hasViewPermission:()=>state.writable});
    const all=nodes=>nodes.flatMap(n=>!n||typeof n!=='object'?[]:[n,...all(Array.isArray(n.children)?n.children:[])]);
    const root=view.render(state.status);
    state.button=name=>all([root,...state.modal]).find(n=>n.tag==='button'&&n.children===name);
    state.flush=()=>new Promise(resolve=>setImmediate(resolve));
    return state;
}
async function main(){
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
    h.button('Roll back last patch').onclick();
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
    console.log('PASS LuCI permission, rejected/lost start, rollback failure, polling outage and recovery');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
