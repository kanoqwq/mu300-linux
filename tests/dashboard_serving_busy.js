// Real page methods, no AT or network. Both repos must distinguish busy/error/data.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const repos=[['Local',path.resolve(__dirname,'../openwrt/luci-app-mu300')]];
(async()=>{
 for(const [label,app] of repos){
  const source=fs.readFileSync(path.join(app,'htdocs/luci-static/resources/view/mu300/locks.js'),'utf8');
  let response,calls=0,feedback=[],paint=[];
  const M={translate:x=>x,callSignal:()=>{calls++;return typeof response==='function'?response():Promise.resolve(response);}};
  const page=new Function('view','rpc','poll','R','M','L','AutoMode','document',source)(
   {extend:o=>o},{},{},{},M,{}, {},{hidden:false});
  page._root={isConnected:true};page._disposed=false;page.Q=()=>({});
  page.loading=(...x)=>feedback.push(x);page.paintServing=x=>paint.push(x);
  const old={ts:10,lte:{band:8}};page.liveSignal=page.lastSignal=old;
  response={busy:1,error:'busy'};await page.loadServing();
  assert.equal(feedback.at(-1)[1],true,label);assert(!feedback.at(-1)[2],label);assert.equal(paint.length,0);
  assert.strictEqual(page.liveSignal,old);assert.strictEqual(page.lastSignal,old);
  response={ts:20,lte:{band:3}};await page.loadServing();
  assert.equal(feedback.at(-1)[1],false);assert(!feedback.at(-1)[2]);assert.equal(paint.at(-1).ts,20);
  page.servingMeta={ts:1,error:'AT backend did not answer'};
  response={ts:21,sig:{rsrp:-90}};await page.loadServing();
  assert(!paint.at(-1).error,'successful live result must remove cached metadata error');
  assert.equal(paint.at(-1).sig.rsrp,-90);
  assert(page.servingSnapshot({ts:50,error:'new failure'},{ts:21,sig:{rsrp:-90}}).error,'old signal must not hide a newer error');
  const before=paint.length;response={error:'hardware unavailable'};await page.loadServing();
  assert(feedback.at(-1)[2],label);assert.equal(paint.length,before+1);assert.equal(paint.at(-1),null);
  response={};await page.loadServing();assert(feedback.at(-1)[2],label+' invalid empty payload');
  response=()=>Promise.reject(Error('connection lost'));await page.loadServing();assert(feedback.at(-1)[2]);
  let finish;response=()=>new Promise(resolve=>{finish=resolve;});const count=calls;
  const first=page.loadServing(),second=page.loadServing();assert.strictEqual(first,second);assert.equal(calls,count+1);
  finish({ts:30});await first;assert.equal(paint.at(-1).ts,30);assert(!feedback.at(-1)[2]);
  const n=paint.length;const pending=page.loadServing();page._disposed=true;finish({ts:40});await pending;
  assert.equal(paint.length,n,label+' disposed');assert.equal(page._signalRequest,null);
  console.log(label+': busy, data recovery, actual failure, invalid response, single flight and disposal PASS');
 }
})().catch(e=>{console.error(e);process.exit(1);});
