'use strict';
// Execute the shipped view's stream lifecycle, without a device or live radio.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../openwrt/overlay/www/luci-static/resources/view/unisoc-bluetooth/settings.js'),'utf8');
const document={hidden:false,removeEventListener(){}};
let handler,requests=[],timers=[];
const V=Function('view','rpc','ui','I','F','S','A','B','E','L','document','fetch','AbortController','TextDecoder','setTimeout','clearTimeout','console',source)(
 {extend:x=>x},{declare:()=>()=>Promise.resolve({ok:true}),getBaseURL:()=>'/custom/ubus/',getSessionID:()=>'session-secret'},
 {},{t:k=>k},{},{},{},{},()=>({}),{},document,
 (url,options)=>{requests.push({url,options});return handler(options.signal);},AbortController,TextDecoder,
 (fn,delay)=>{const t={fn,delay};timers.push(t);return t;},t=>{timers=timers.filter(x=>x!==t);},{warn(){}});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fresh(){return Object.assign({},V,{streamState:{textContent:''},packets:[],updates:0,receiveEvents(v){this.packets.push(v);},update(){this.updates++;}});}
function stream(signal,done=false){
 const lines=new TextEncoder().encode('event: update\ndata: {"sequence":1,"event_id":"boot","events":[]}\n\n');
 let count=0;
 return Promise.resolve({ok:true,status:200,headers:{get:()=> 'text/event-stream'},body:{getReader:()=>({read(){
  if(!count++)return Promise.resolve({value:lines,done:false});
  if(done)return Promise.resolve({done:true});
  return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));
 }})}});
}
(async()=>{
 let v=fresh();handler=signal=>stream(signal);
 const first=v.openStream();await tick();
 assert.equal(requests.length,1);assert.equal(requests[0].url,'/custom/ubus/subscribe/unisoc-bluetooth-events');
 assert.equal(requests[0].options.headers.Authorization,'Bearer session-secret');
 assert(!requests[0].url.includes('session-secret'));assert.equal(v.packets.length,1);assert.equal(v.updates,1);
 await v.openStream();assert.equal(requests.length,1,'Duplicate subscription');
 const oldController=v.streamAbort;v.pauseStream();const second=v.openStream();await tick();
 assert(oldController.signal.aborted);assert(v.streamAbort&&!v.streamAbort.signal.aborted,'Old finally cleared the new subscription');
 v.unload();await Promise.all([first,second]);assert.equal(timers.length,0);
 document.hidden=true;v=fresh();await v.openStream();assert.equal(requests.length,2,'Hidden page opened an event stream');document.hidden=false;
 handler=async()=>({ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>({code:-13,message:'Permission denied'})});
 v=fresh();await v.openStream();assert.equal(v.streamState.textContent,'session_expired');assert.equal(timers.length,0);
 handler=signal=>stream(signal,true);v=fresh();await v.openStream();
 assert.equal(timers.length,1);assert.equal(timers[0].delay,0,'Lease renewal unnecessarily stalled the event stream');
 v.unload();assert.equal(timers.length,0);
 handler=async()=>({ok:false,status:503});v=fresh();await v.openStream();
 assert.equal(timers.length,1);assert(timers[0].delay>=1000);v.unload();assert.equal(timers.length,0);
 let malformedSignal;
 handler=async signal=>{malformedSignal=signal;return {ok:true,status:200,headers:{get:()=> 'text/event-stream'},body:{getReader:()=>({read:async()=>({value:new TextEncoder().encode('data: {bad json}\n\n'),done:false})})}};};
 v=fresh();await v.openStream();assert(malformedSignal.aborted,'Malformed event left its HTTP connection open');
 assert.equal(timers.length,1);v.unload();assert.equal(timers.length,0);
 console.log('PASS native SSE auth, custom URL, duplicate/race cleanup, hidden/unloaded page, revoked session and lease renewal');
})().catch(e=>{console.error(e);process.exitCode=1;});
