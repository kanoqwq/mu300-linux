#!/usr/bin/env node
'use strict';
// Exercise presentation with real RPC snapshots, without a radio or browser session.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../openwrt/overlay/www/luci-static/resources');
function E(tag, attrs, children) {
 if (typeof attrs === 'string' || Array.isArray(attrs)) { children=attrs; attrs={}; }
 const node={tag,attrs:attrs || {},children:[],value:'',appendChild(child){this.children.push(child);return child;},
  replaceChildren(...children){this.children=children;},
  get textContent(){return this.children.map(x=>typeof x==='object' ? x.textContent : String(x || '')).join('');},
  set textContent(text){this.children=[text];}};
 node.children=children == null ? [] : Array.isArray(children) ? children : [children];
 node.classList={add:className=>{node.attrs.class=(node.attrs.class || '')+' '+className;}};
 node.value=node.attrs.value || (tag==='textarea' ? children || '' : '');
 node.focus=()=>{node.focused=true;};node.select=()=>{node.selected=true;};
 return node;
}
const L={env:{lang:'zh-cn'}}, baseclass={extend:x=>x};
function load(name, globals) {
 return Function(...Object.keys(globals), fs.readFileSync(path.join(root,name),'utf8'))(...Object.values(globals));
}
const languageDocument={documentElement:{lang:'en'}};
const I=load('unisoc-bluetooth/i18n.js',{baseclass,L,document:languageDocument});
const F=load('unisoc-bluetooth/feedback.js',{baseclass,I,E,L,document:{documentElement:{lang:'en'}}});
let modal;
const modalUI={showModal:(title,children)=>{modal={title,children};},hideModal:()=>{}};
const S=load('unisoc-bluetooth/smart.js',{baseclass,ui:modalUI,I,F,E,L});
const A=load('unisoc-bluetooth/advertising.js',{baseclass,ui:modalUI,I,F,E,L});
const clipboard={},browserWindow={isSecureContext:false};
const B=load('unisoc-bluetooth/broadcast.js',{baseclass,ui:modalUI,I,E,navigator:clipboard,window:browserWindow});
const replies={},declarations=[],stored=new Map();
const sessionStorage={getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)};
const V=load('view/unisoc-bluetooth/settings.js',{view:baseclass,rpc:{declare:options=>{
 declarations.push(options);return async()=>replies[options.method];
}},ui:{},I,F,S,A,B,E,L,sessionStorage});
function fresh(){
 const v=Object.assign({},V,{adapterIds:'hci0',adapter:E('select'),button:()=>'',seq:0,events:[],broadcast:B.create()});
 for(const key of ['info','forwardState','devices','pairing','jobs','feedback','eventLog'])v[key]=E('div');
 return v;
}
const snapshot={power_state:'off',adapters:[{id:'hci0',Address:'02:11:22:33:44:55',Powered:false}],adapter:'hci0',devices:[],pairing:[],
 jobs:[],forwarding:{mqtt:'disabled',http:'disabled',dropped:0,errors:0},config:{gateway:{enabled:false}}};
async function main(){
 for(const lang of ['zh-cn','en','tr']){
  L.env.lang=lang;
  assert.notEqual(F.error('disable_gateway_before_power_off'),'disable_gateway_before_power_off');
  assert.notEqual(I.t('pair_display_passkey'),'pair_display_passkey');
  assert.notEqual(F.state('running'),'running');
  for(const [state,label,hint] of [['off','powered_off','controller_hint'],['starting','power_starting','power_starting_hint'],['failed','power_failed','power_failed_hint'],['unavailable','none','controller_missing_hint']]){
   const stateView=fresh();stateView.status({...snapshot,adapters:[],power_state:state});
   assert.ok(stateView.info.textContent.includes(I.t(label)));
   assert.ok(stateView.info.textContent.includes(I.t(hint)));
   if(state!=='unavailable')assert.ok(!stateView.info.textContent.includes(I.t('none')));
   assert.notEqual(I.t(label),label);assert.notEqual(I.t(hint),hint);
  }
  const enabledView=fresh();enabledView.status({...snapshot,power_state:'on',adapters:[{...snapshot.adapters[0],Powered:true}]});
  assert.ok(enabledView.info.textContent.includes(I.t('on')));
 }
 L.env.lang='zh-cn';
 // Raw scan data is useful without pairing; RSSI updates retain opened details
 // and unchanged payload DOM, including byte selection and copy controls.
 const broadcast=B.create(),packet={Address:'02:11:22:33:44:55',AddressType:'random',Name:'Lab <script>',RSSI:-52,TxPower:0,
  ManufacturerData:{911:'0102ff'},ServiceData:{'0000180f-0000-1000-8000-00805f9b34fb':'64'},
  AdvertisingData:{38:'0102'},AdvertisingFlags:'06',UUIDs:['0000180f-0000-1000-8000-00805f9b34fb']};
 const detail=broadcast.render(packet,'hci0');detail.open=true;
 assert.match(detail.textContent,/0x038F \(911\) · Xiaomi Inc\./);assert.match(detail.textContent,/01 02 FF/);
 assert.match(detail.textContent,/3 字节/);assert.match(detail.textContent,/0x26/);assert.match(detail.textContent,/随机地址/);
 const body=detail.children[1],manufacturer=body.children[2],manufacturerBlock=manufacturer.children[1].children[0];
 assert.equal(broadcast.render({...packet,RSSI:-65},'hci0'),detail);assert.equal(detail.open,true);
 assert.equal(manufacturer.children[1].children[0],manufacturerBlock);
 let copied;clipboard.clipboard={writeText:async text=>{copied=text;}};browserWindow.isSecureContext=true;
 await manufacturerBlock.children[3].children[0].attrs.click();assert.equal(copied,'0x038F=0102FF');
 browserWindow.isSecureContext=false;await manufacturerBlock.children[3].children[0].attrs.click();
 assert.equal(modal.title,'请选择并复制下方内容');
 const copyArea=modal.children[0].children[0].children[0];assert.equal(copyArea.value,'0x038F=0102FF');assert.equal(copyArea.selected,true);
 broadcast.prune([], 'hci0');assert.equal(broadcast.entries.size,0);
 for(const lang of ['zh-cn','en','tr']){L.env.lang=lang;assert.notEqual(B.t('manufacturer'),'manufacturer');}
 L.env.lang='zh-cn';
 assert.ok(declarations.every(x=>x.reject===true));
 replies.pair_reply={ok:true};
 assert.equal((await V.call('pair_reply',{})).ok,true);
 replies.pair_reply={};
 await assert.rejects(V.call('pair_reply',{}),/bluetooth_invalid_response: \{\}/);
 replies.pair_reply={ok:false,error:'pairing_request_expired'};
 await assert.rejects(V.call('pair_reply',{}),/pairing_request_expired/);
 let v=fresh();v.status(snapshot);
 assert.match(v.info.textContent,/蓝牙已关闭/);
 assert.doesNotMatch(v.info.textContent,/开启蓝牙/);
 assert.match(v.devices.textContent,/开启蓝牙后/);
 assert.doesNotMatch(v.forwardState.textContent,/[{}]|disabled/);
 const problem='unknown_driver_error <script>alert(1)</script>';
 const message=F.message(F.error(problem),problem,'error');
 assert.equal(message.children[0].textContent,'操作失败');
 assert.equal(message.children[1].tag,'details');
 assert.equal(message.children[1].children[1].textContent,problem);
 assert.match(F.error('org.bluez.Error.NotReady: Resource Not Ready'),/请先开启蓝牙/);
 // An accepted asynchronous command is not a completed operation.
 v=fresh();v.call=async()=>({ok:true,job:'7'});v.update=async()=>{};
 await v.action('power',{enabled:true});
 assert.match(v.feedback.textContent,/请求已受理/);
 assert.doesNotMatch(v.feedback.textContent,/已完成/);
 v.status({...snapshot,jobs:[{id:'7',state:'failed',error:'bluetooth_power_transition_timed_out'}]});
 assert.equal(v.feedback.textContent,'');
 assert.match(v.jobs.textContent,/上次操作未完成/);
 // Closing a terminal result survives SSE refreshes and page reloads in this tab.
 const failed={...snapshot,event_id:'daemon-one',jobs:[{id:'8',state:'failed',error:'org.bluez.Error.ConnectionAttemptFailed: Page Timeout'}]};
 v=fresh();v.button=(label,fn)=>E('button',{click:fn},label);v.status(failed);
 assert.match(v.jobs.textContent,/对方未响应/);
 v.jobs.children[0].children.at(-1).attrs.click();
 assert.equal(v.jobs.textContent,'');v.status(failed);assert.equal(v.jobs.textContent,'');
 let reloaded=fresh();reloaded.status(failed);assert.equal(reloaded.jobs.textContent,'');
 reloaded.status({...failed,event_id:'daemon-two'});assert.match(reloaded.jobs.textContent,/上次操作未完成/);
 // Running operations cannot be dismissed; a new request clears the old result.
 v.status({...failed,jobs:[{id:'9',state:'running'}]});v.dismissCompletedJobs();
 assert.match(v.jobs.textContent,/操作进行中/);
 v.status({...failed,jobs:[{id:'10',state:'failed',error:'old_error'}]});
 replies.visibility={ok:true};await v.call('visibility',{});v.status(v.latest);
 assert.equal(v.jobs.textContent,'');
 // A transport failure replaces the previous result instead of stacking alerts.
 v.call=async()=>{throw new Error('org.bluez.Error.NotReady: Resource Not Ready');};
 await v.action('scan',{});await v.action('scan',{});
 assert.equal(v.feedback.children.length,1);
 assert.match(v.feedback.textContent,/蓝牙尚未就绪/);
 assert.match(F.event({type:'job',data:{state:'done'}}),/已完成/);
 assert.match(F.event({type:'serial',data:{address:'02:11:22:33:44:55'}}),/收到串口数据/);
 // Basic settings do not serialize or validate unrelated unfinished gateway rows.
 v=fresh();v.autostart={querySelector:()=>({checked:false})};v.alias={value:'我的蓝牙'};
 v.status=()=>{};let saved;
 v.call=async(method,args)=>{saved={method,args};return snapshot;};
 await v.saveBasic();
 assert.deepEqual(saved,{method:'save',args:{config:{autostart:false,adapter:'',alias:'我的蓝牙'}}});
 assert.match(v.feedback.textContent,/名称在蓝牙关闭时也能保存/);
 // LuCI may expose language only on <html>, and mounts modals outside the view.
 delete L.env.lang;languageDocument.documentElement.lang='zh-CN';
 const smart=Object.assign(Object.create(S),{known:[],view:{button:(label)=>E('button',{},label)}});
 smart.edit('02:11:22:33:44:55');
 assert.equal(modal.title,'管理设备');
 assert.equal(modal.children[0].attrs.class,'unisoc-bt bt-modal');
 assert.equal(modal.children[0].children[0].attrs.class,'bt-modal-body');
 assert.match(modal.children[0].textContent,/房间／分组/);
 assert.doesNotMatch(modal.children[0].textContent,/Room|Manufacturer|Keep;/);
 languageDocument.documentElement.lang='tr';assert.equal(S.t('manage'),'Cihazı yönet');
 L.env.lang='en';assert.equal(S.t('manage'),'Manage device');L.env.lang='zh-cn';
 // Editing a lab definition saves only lab data and never enables Bluetooth.
 let labRequest;
 const labView={button:(label,fn)=>E('button',{click:fn},label),update:async()=>{},showError:e=>{throw e;},
  call:async(method,args)=>{labRequest={method,args};return {ok:true,advertising:{profiles:[]}};}};
 const lab=A.create(labView);assert.equal(labRequest,undefined);
 lab.edit();assert.equal(modal.title,'新建实验广播');
 function descend(node){return [node].concat((node.children || []).filter(c=>typeof c==='object').flatMap(descend));}
 const inputs=descend(modal.children[0]).filter(n=>n.tag==='input'||n.tag==='textarea');
 inputs[5].value='0xFFFF=01 02\n123=FF';inputs[6].value='180F=64';
 const labSave=modal.children[0].children[1].children[1];await labSave.attrs.click();
 assert.equal(labRequest.method,'advertising_save');
 assert.deepEqual(labRequest.args.profile.manufacturer_data,[{company:65535,hex:'01 02'},{company:123,hex:'FF'}]);
 assert.deepEqual(labRequest.args.profile.service_data,[{uuid:'180F',hex:'64'}]);
 assert.equal(labRequest.args.profile.interval_ms,1000);
 lab.update({supported:true,powered:false,profiles:[{id:'demo',label:'Test',name:'Lab',active:false,interval_ms:1000,timeout:60}]});
 assert.equal(lab.list.children[0].children[1].children[0].disabled,true);
 for(const lang of ['zh-cn','en','tr']){L.env.lang=lang;assert.notEqual(A.t('title'),'title');}
 L.env.lang='zh-cn';
 console.log('Bluetooth UI: power states, localization, safe error details, async results, replacement feedback and snapshots passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
