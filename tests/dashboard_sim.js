'use strict';
const fs=require('fs'),path=require('path'),assert=require('assert');
const dir=path.resolve(__dirname,'../openwrt/luci-app-mu300/htdocs/luci-static/resources');
const store=new Map(), session={getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)};
let called=[];
const common=new Function('rpc','baseclass','L','document','navigator','sessionStorage',fs.readFileSync(dir+'/mu300/common.js','utf8'))(
    {declare:o=>(...args)=>{called.push([o.method,args]); return Promise.resolve({});}},
    {extend:o=>o},{env:{lang:'en'}},{},{},session);
assert.equal(common.selectedSlot(),'0');
common.selectViewedSlot(1); assert.equal(common.selectedSlot(),'1');
assert.equal(common.selectViewedSlot(2),false); assert.equal(common.selectedSlot(),'1');
common.callAt('AT'); common.selectViewedSlot(0);
assert.deepEqual(called[0],['at',['AT','1']]);

function element(tag) {return {tag,children:[],style:{},isConnected:true,append(...n){this.children.push(...n);}};}
function find(root,tag){return root.tag===tag?root:root.children.map(n=>find(n,tag)).find(Boolean);}
const tick=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
(async()=>{
    for(const outcome of ['cancel','error','done']) {
        common.selectViewedSlot('0'); let requests=0;
        const M={injectCss(){},localizeMenu(){},translate:s=>s,selectViewedSlot:common.selectViewedSlot,
            confirmBox:()=>Promise.resolve(outcome!=='cancel'),busy:(b,v)=>b.busy=v,toast:()=>({close(){}})};
        const rpc={declare:o=>()=>{
            if(o.method==='data_sim_set'){requests++;return Promise.resolve({ok:1,id:'abcdefghij'});}
            if(o.method==='data_sim_status')return Promise.resolve({id:'abcdefghij',state:outcome,ok:outcome==='done',error:'rejected'});
            return Promise.resolve({ok:1,saved_sim:1,actual_sim:1,data_connected:false});
        }};
        const view=new Function('view','rpc','poll','M','document','SimRadio',fs.readFileSync(dir+'/view/mu300/sim.js','utf8'))(
            {extend:o=>o},rpc,{add(){},remove(){}},M,{createElement:element},{attach:()=>({refresh:()=>Promise.resolve(),dispose(){}})});
        const root=view.render([{slots:2},{ok:1,ready:true,saved_sim:0,actual_sim:0}]);
        const section=find(root,'section'), row=find(section,'select');
        assert(root.className.includes('mud-sim-page'));
        assert(find(section,'h3'), 'heading belongs inside the card');
        assert.equal(find(section,'label').htmlFor,row.id);
        assert.equal(find(section,'button').type,'button');
        assert(find(root,'style').textContent.includes('@media(max-width:480px)'));
        find(root,'select').value='1';find(root,'button').onclick();await tick();
        assert.equal(common.selectedSlot(),outcome==='done'?'1':'0',outcome);
        assert.equal(requests,outcome==='cancel'?0:1);
        assert(!view.busy && !find(root,'button').busy,outcome+' left loading active');
        view.unload();
    }
    {
        common.selectViewedSlot('0');
        const state={ok:1,ready:false,saved_sim:0,actual_sim:0,data_connected:true};
        const requests=[];
        const M={injectCss(){},localizeMenu(){},translate:s=>s,
            selectViewedSlot:common.selectViewedSlot,
            confirmBox:()=>Promise.resolve(true),busy:(b,v)=>b.busy=v,toast:()=>({close(){}})};
        const rpc={declare:o=>(target)=>{
            if(o.method==='data_sim_get')return Promise.resolve({...state});
            if(o.method==='data_sim_set') {
                requests.push(target);
                state.actual_sim=Number(target);state.saved_sim=Number(target);
                return Promise.resolve({ok:1,id:'abcdefghij'});
            }
            return Promise.resolve({id:'abcdefghij',state:'done',ok:1});
        }};
        const view=new Function('view','rpc','poll','M','document','SimRadio',fs.readFileSync(dir+'/view/mu300/sim.js','utf8'))(
            {extend:o=>o},rpc,{add(){},remove(){}},M,{createElement:element},{attach:()=>({refresh:()=>Promise.resolve(),dispose(){}})});
        const root=view.render([{slots:2},{...state}]);
        function all(n,tag){return (n.tag===tag?[n]:[]).concat(...n.children.map(c=>all(c,tag)));}
        const buttons=all(root,'button');
        assert.equal(buttons.length,1,'U30 has no separate hot-switch policy controls');
        const switchButton=buttons[0], select=find(root,'select');
        assert(switchButton.disabled,'uninitialized dual stacks require cold boot');
        assert(!select.disabled);
        state.ready=true;await view.refresh();
        assert(!switchButton.disabled,'ready state must enable the live control without a page reload');
        select.value='1';switchButton.onclick();await tick();
        assert.deepEqual(requests,['1']);
        assert.equal(common.selectedSlot(),'1');
        view.unload();
    }
    for(const [model,board,cards,target] of [['v50',0,[0,1,2],2],['v50',1,[0,1,2,3],3],['v50',2,[0,1,3],3],['v50',3,[0,1],1],['u30pro',0,[0,1,2,3],3],['u30pro',1,[0,1,2],2],['u30pro',2,[0,1],1],['u30pro',3,[0,1,3],3],['u30pro',4,[0,1,2,3],2],['u30pro',5,[3],3],['u30pro',6,[0,2],2]]) {
        common.selectViewedSlot('0');const requests=[];
        const physical={ok:1,supported:true,ready:true,model,board_id:board,cards,active_card:0,saved_card:0};
        const M={injectCss(){},localizeMenu(){},translate:s=>s,selectViewedSlot:common.selectViewedSlot,
            confirmBox:()=>Promise.resolve(true),busy(){},toast:()=>({close(){}})};
        const rpc={declare:o=>arg=>{
            if(o.method==='physical_sim_get')return Promise.resolve({...physical});
            if(o.method==='data_sim_set') {
                requests.push(arg);physical.active_card=physical.saved_card=target;
                return Promise.resolve({ok:1,id:'abcdefghij'});
            }
            if(o.method==='data_sim_status')return Promise.resolve({id:'abcdefghij',state:'done',ok:1});
            return Promise.resolve({ok:1,ready:true,actual_sim:0,saved_sim:0});
        }};
        const view=new Function('view','rpc','poll','M','document','SimRadio',fs.readFileSync(dir+'/view/mu300/sim.js','utf8'))(
            {extend:o=>o},rpc,{add(){},remove(){}},M,{createElement:element},{attach:()=>({refresh:()=>Promise.resolve(),dispose(){}})});
        const root=view.render([{slots:2},{ok:1,ready:true,actual_sim:0,saved_sim:0},{...physical}]);
        function all(n,tag){return (n.tag===tag?[n]:[]).concat(...n.children.map(c=>all(c,tag)));}
        const select=all(root,'select').find(n=>n.id==='mud-v50-card');
        assert.deepEqual(select.children.map(n=>Number(n.value)),cards,'board-specific physical choices');
        const button=all(root,'button')[1];select.value=String(target);button.onclick();await tick();
        assert.deepEqual(requests,[model+'-card-'+target]);
        assert.equal(common.selectedSlot(),target===0||target===3?'0':'1','physical IDs never become AT slot IDs');
        physical.ready=false;await view.refresh();assert(button.disabled);
        view.unload();
    }
    console.log('SIM UI: scoped RPC, shared controls, four V50 and seven U30 Pro hardware variants pass');
})().catch(e=>{console.error(e);process.exitCode=1;});
