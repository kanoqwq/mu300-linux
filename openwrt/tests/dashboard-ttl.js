'use strict';
const fs = require('fs'), path = require('path'), assert = require('assert');
const repo = path.resolve(__dirname, '../..');
const app = fs.existsSync(path.join(repo, 'devices/e5')) ? 'luci-app-t551m' : 'luci-app-mu300';
const dir = path.join(repo, 'openwrt', app, 'htdocs/luci-static/resources');
String.prototype.format = function(...args) { let i=0; return this.replace(/%[ds]/g,()=>String(args[i++])); };
function el(tag,attrs={},children=[]) {
    const node={tag,attrs,style:{},children:[],disabled:!!attrs.disabled,events:{},
        appendChild(n){this.children.push(n);},
        addEventListener(k,v){this.events[k]=v;},
        querySelectorAll(){return all(this).filter(n=>n.tag==='button'||n.tag==='input');}};
    for (const c of Array.isArray(children)?children:[children]) node.children.push(c);
    return node;
}
function all(n) {return typeof n==='object'&&n ? [n,...n.children.flatMap(all)] : [];}
const lookup=(root,id)=>all(root).find(n=>n.attrs.id===id);
const text=n=>typeof n==='string'?n:n.children.map(text).join(' ');
const commonSource=fs.readFileSync(path.join(dir,'mu300/common.js'),'utf8');
const pageSource=fs.readFileSync(path.join(dir,'view/mu300/ttl.js'),'utf8');
(async()=>{
    for(const lang of ['zh','en','tr']) {
        const options=[];
        const M=new Function('rpc','baseclass','L','document','navigator','sessionStorage',commonSource)(
            {declare:o=>{options.push(o);return ()=>Promise.resolve({});}}, {extend:o=>o},
            {env:{lang}}, {}, {}, {getItem(){return null;},setItem(){}});
        assert(options.find(o=>o.method==='ttl_set').nobatch);
        assert.equal(M.fmtBatteryCurrent(60000),'+60mA');
        assert.equal(M.fmtBatteryCurrent(-123456),'-123mA');
        assert.equal(M.fmtBatteryCurrent(null),'--');
        assert.equal(M.fmtBatteryCurrent(NaN),'--');
        const home=fs.readFileSync(path.join(dir,'view/mu300/home.js'),'utf8');
        const nodes={}, values={};
        const power=new Function('i','M',home.slice(home.indexOf('var p = i.power || {};'),home.indexOf("M.set('model'",home.indexOf('var p = i.power || {};'))));
        const powerM={...M,v:id=>nodes[id]||(nodes[id]={style:{}}),set:(id,value)=>values[id]=value};
        power({power:{present:1,capacity:44,status:'Charging',volt:3.7999999999999998,ua:12000,usb:1}},powerM);
        assert.equal(values.batt,'44%');assert(values['batt-l'].includes('3.80V'));
        assert(values['batt-l'].includes('0.01A'));assert.equal(nodes['batt-bar'].style.width,'44%');
        assert.equal(nodes['batt-meter'].hidden,false);
        power({power:{present:0,usb:1}},powerM);
        assert.equal(values.batt,'USB');assert.equal(nodes['batt-meter'].hidden,true);
        let writes=[],finish,toast=[];
        Object.assign(M,{injectCss(){},busy(){},toast:(msg,opts)=>toast.push([msg,opts]),
            callTtlGet:()=>Promise.resolve({ok:1,enabled:1,value:64,backend:'nft',offload:0}),
            callTtlSet:v=>{writes.push(v);return new Promise(resolve=>finish=resolve);}});
        const page=new Function('view','dom','M','E',pageSource)({extend:o=>o},
            {content:(n,c)=>{n.children=[c];}}, M, el);
        const root=page.render({ok:1,enabled:0,backend:'none',offload:1});
        assert(text(root).includes(lang==='zh'?'蜂窝网络 TTL':lang==='tr'?'Mobil veri TTL':'Cellular TTL'));
        assert(text(root).includes(lang==='zh'?'可与软件流量卸载同时使用':lang==='tr'?'birlikte çalışır':'works with software flow offloading'));
        page.paint({ok:1,enabled:1,value:65,backend:'nft',mode:'egress',iface:[],offload:1,hw_paused:1});
        assert(text(root).includes(M.translate('等待蜂窝接口，出现后自动应用。')));
        assert(text(root).includes(M.translate('固定 TTL 期间暂停')));
        lookup(root,'mud-ttl-value').value='1; reboot';
        lookup(root,'mud-ttl-custom').events.click();assert.equal(writes.length,0);
        const a=page.set(lookup(root,'mud-ttl-64'),64);
        assert(root.querySelectorAll().every(n=>n.disabled));
        await page.set(lookup(root,'mud-ttl-128'),128);assert.deepEqual(writes,['64']);
        finish({ok:0,error:'无法应用 TTL 设置'});await a;
        assert(toast.at(-1)[1].type==='error');assert(!lookup(root,'mud-ttl-64').disabled);
        const b=page.set(lookup(root,'mud-ttl-65'),65);finish({ok:1});await b;
        assert.deepEqual(writes,['64','65']);assert.equal(lookup(root,'mud-ttl-off').disabled,false);
        page.paint({ok:0,error:'无法读取 TTL 设置'});
        assert(!lookup(root,'mud-ttl-custom'));
    }
    console.log('TTL page zh/en/tr: validation, serialization, errors, save/refresh and battery current passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
