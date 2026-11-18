'use strict';
'require view';
'require rpc';
'require ui';
'require unisoc-bluetooth.i18n as I';
'require unisoc-bluetooth.feedback as F';
'require unisoc-bluetooth.smart as S';
'require unisoc-bluetooth.advertising as A';
'require unisoc-bluetooth.broadcast as B';

var T = function(k) { return I.t(k); };
var names = ['status','save','power','visibility','scan','pair','pair_reply','connect','disconnect','forget','trust','gatt_list','read','write','notify','serial_write','spp_services','templates','backup','restore','proxy_key','apply_template','import_templates','command','advertising_list','advertising_save','advertising_delete','advertising_start','advertising_stop'];
var calls = {};
names.forEach(function(n) { calls[n] = rpc.declare({ object: 'unisoc-bluetooth', method: n, params: ['request'], expect: {}, reject: true }); });
function field(label, input) { return E('label', { 'class': 'bt-field' }, [E('span', {}, label), input]); }
function input(value, type) { return E('input', { type: type || 'text', value: value == null ? '' : value }); }
function check(value, label) { return E('label', { 'class': 'bt-check' }, [E('input', { type: 'checkbox', checked: value || null }), E('span', {}, label)]); }
function checked(node) { return node.querySelector('input').checked; }
function select(items, value) { return E('select', {}, items.map(function(x) { return E('option', { value: x[0], selected: x[0] === value || null }, x[1]); })); }

return view.extend({
 load: function() { return calls.status('{}'); },
 call: function(name, args) {
  var self = this;
  if(['status','gatt_list','spp_services','templates','backup','proxy_key','advertising_list'].indexOf(name)<0)
   this.dismissCompletedJobs();
  return calls[name](JSON.stringify(args || {})).then(function(r) {
   if (!r || r.ok !== true) throw new Error(r && r.error || 'bluetooth_invalid_response: '+JSON.stringify(r));
   return r;
  });
 },
 action: function(name, args) {
  var self = this;
  this.feedback.replaceChildren(F.message(T('working')));
  return this.call(name, args).then(function(r) {
   var label=r.job ? 'queued' : r.acknowledged ? (r.confirmed ? 'command_confirmed' : 'command_sent') : 'completed';
   self.feedback.replaceChildren(F.message(T(label), null, r.job || r.acknowledged && !r.confirmed ? 'info' : 'success'));
   if(r.job) self.waitingJob=r.job;
   return self.update();
  }).catch(function(e) { self.showError(e); });
 },
 showError: function(error) { this.feedback.replaceChildren(F.message(F.error(error.message),error.message,'error')); },
 jobKey: function(s,j) { return (s.event_id || '')+':'+j.id; },
 dismissedJob: function() {
  if(this.hiddenJob===undefined){try{this.hiddenJob=sessionStorage.getItem('unisoc-bluetooth-dismissed-job') || '';}catch(e){this.hiddenJob='';}}
  return this.hiddenJob;
 },
 dismissJob: function(j) {
  if(j.state==='running')return;
  this.hiddenJob=this.jobKey(this.latest,j);
  try{sessionStorage.setItem('unisoc-bluetooth-dismissed-job',this.hiddenJob);}catch(e){}
  this.status(this.latest);
 },
 dismissCompletedJobs: function() {
  (this.latest && this.latest.jobs || []).filter(function(j){return j.state!=='running';}).forEach(this.dismissJob,this);
 },
 button: function(label, fn, primary) { return E('button', { 'class': 'cbi-button ' + (primary ? 'cbi-button-action' : ''), click: ui.createHandlerFn(this, fn) }, label); },
 update: function() {
  var self = this;
  if (this.loading) { this.pendingRefresh = true; return this.loading; }
  this.loading = this.call('status').then(function(s) { self.status(s); }).catch(function(e) {
   self.info.replaceChildren(F.message(T('unavailable'),e.message,'error'));
  }).finally(function() {
   self.loading = null;
   if (self.pendingRefresh) { self.pendingRefresh = false; self.update(); }
  });
  return this.loading;
 },
 status: function(s) {
  this.latest = s;
  if(this.smart)this.smart.update(s);
  if(this.lab)this.lab.update(s.advertising);
  var adapterValue=this.adapter.value, adapterIds=(s.adapters || []).map(function(a){return a.id;}).join(',');
  if(adapterIds!==this.adapterIds){this.adapterIds=adapterIds;this.adapter.replaceChildren(E('option',{value:''},T('automatic')));(s.adapters || []).forEach(function(a){this.adapter.appendChild(E('option',{value:a.id},a.id+' · '+a.Address));},this);this.adapter.value=adapterValue;}
  var self = this;
  var selected=(s.adapters || []).find(function(a){return a.id===s.adapter;});
  var powerLabels={off:'powered_off',on:'on',starting:'power_starting',failed:'power_failed',unavailable:'none'};
  var powerHints={off:'controller_hint',starting:'power_starting_hint',failed:'power_failed_hint',unavailable:'controller_missing_hint'};
  this.info.replaceChildren();
  if(s.error) this.info.appendChild(F.message(F.error(s.error),s.error,'error'));
  this.info.appendChild(E('div',{'class':'bt-status-line'},[
   E('strong',{},T(powerLabels[s.power_state] || 'none')),
   selected && selected.Powered ? E('span',{'class':'bt-badge'+(selected.Discovering ? ' bt-active' : '')},T(selected.Discovering ? 'scanning' : 'idle')) : '',
   selected && selected.Discoverable ? E('span',{'class':'bt-badge'},T('discoverable')) : ''
  ]));
  var hint=powerHints[s.power_state];
  this.info.appendChild(E('p',{'class':'bt-muted'},selected && (s.power_state==='on' || s.power_state==='off') ? (selected.Alias || selected.id)+' · '+selected.Address : T(hint || 'controller_missing_hint')));
  var forwarding=s.forwarding || {};
  this.forwardState.replaceChildren(E('dl',{'class':'bt-forward-status'},['mqtt','http'].map(function(k){
   var state=forwarding[k] || 'disabled';
   var label=state==='connected' ? (k==='http' ? 'delivered' : 'connected_state') : state;
   if(state==='disabled' && s.config && s.config.gateway.enabled && s.config.gateway[k].enabled) label='awaiting_delivery';
   return E('div',{},[E('dt',{},k==='mqtt' ? 'MQTT' : 'HTTP'),E('dd',{},T(label))]);
  }).concat(['dropped','errors'].map(function(k){return E('div',{},[E('dt',{},T(k)),E('dd',{},String(forwarding[k] || 0))]);}))));
  this.devices.replaceChildren();
  if(!(s.devices || []).length) this.devices.appendChild(E('p',{'class':'bt-muted'},T(selected && selected.Powered ? 'empty_scan' : 'empty_off')));
  (s.devices || []).sort(function(a,b) { return Number(b.Connected) - Number(a.Connected) || Number(b.RSSI || -127) - Number(a.RSSI || -127); }).forEach(function(d) {
   var actions = [self.button(d.Connected ? T('disconnect') : T('connect'), function() { return self.action(d.Connected ? 'disconnect' : 'connect', { device:d.Address }); })];
   if (!d.Paired) actions.push(self.button(T('pair'), function() { return self.action('pair', {device:d.Address}); }));
   actions.push(self.button(d.Trusted ? T('untrust') : T('trust'), function() { return self.action('trust', {device:d.Address,enabled:!d.Trusted}); }));
   actions.push(self.button(T('forget'), function() { return self.action('forget', {device:d.Address}); }));
   if(self.smart)actions.push(self.button(S.t('manage'),function(){self.smart.edit(d.Address);}));
   actions.push(self.button('SPP',function(){return self.call('spp_services',{device:d.Address}).then(function(r){ui.showModal('SPP · '+d.Address,[r.services.length ? E('div',{},r.services.map(function(x){return self.button((x.name || 'SPP')+' · '+x.channel,function(){self.addSerial({device:d.Address,channel:x.channel});ui.hideModal();});})) : E('p',{},T('no_spp')),self.button(T('close'),ui.hideModal)]);}).catch(function(e){self.showError(e);});}));
   if (d.Connected) actions.push(self.button(T('inspect'), function() { return self.gatt(d.Address); }));
   self.devices.appendChild(E('article', { 'class':'bt-device bt-scan-device' }, [
    E('div', {}, [E('strong', {}, d.Name || d.Alias || T('unnamed')), E('div',{'class':'bt-device-state'},[
     E('span',{'class':'bt-badge'+(d.Connected ? ' bt-active' : '')},T(d.Connected ? 'connected' : 'disconnected')),
     E('span',{'class':'bt-badge'},T(d.Paired ? 'paired' : 'unpaired'))]),
     E('div', {'class':'bt-muted'}, d.Address + (d.RSSI != null ? ' · ' + d.RSSI + ' dBm' : ''))]),
    E('div', {'class':'bt-actions'}, actions),self.broadcast.render(d,s.adapter)
   ]));
  });
  if(this.broadcast)this.broadcast.prune(s.devices || [],s.adapter);
  this.pairing.replaceChildren();
  (s.pairing || []).forEach(function(p) {
   var value = input('', p.kind === 'pin' ? 'text' : 'number');
   self.pairing.appendChild(E('div', {'class':'bt-pair'}, [E('strong',{},T('pairing')+' · '+p.device), E('p',{},T('pair_'+p.kind)),p.value ? E('code',{'class':'bt-passkey'},p.value) : '',
    ['pin','passkey'].indexOf(p.kind) >= 0 ? field(T('pin'),value) : '',
    p.kind.indexOf('display_') === 0 ? '' : E('div',{'class':'bt-actions'},[
     self.button(T('accept'),function(){return self.action('pair_reply',{id:p.id,accept:true,value:value.value});},true),
     self.button(T('reject'),function(){return self.action('pair_reply',{id:p.id,accept:false});})])
   ]));
  });
  this.jobs.replaceChildren();
  (s.jobs || []).forEach(function(j){
   if(self.waitingJob===j.id && j.state!=='running'){self.waitingJob=null;self.feedback.replaceChildren();}
   if(j.state!=='running' && self.jobKey(s,j)===self.dismissedJob())return;
   var notice=F.message(F.state(j.state)+(j.error ? ' · '+F.error(j.error) : ''),j.error,j.state==='failed' ? 'error' : j.state==='done' ? 'success' : 'info');
   if(j.state!=='running'){
    notice.classList.add('bt-job-result');
    notice.appendChild(self.button(T('close'),function(){self.dismissJob(j);}));
   }
   self.jobs.appendChild(notice);
  });
 },
 gatt: function(mac) {
  var self = this;
  return this.call('gatt_list',{device:mac}).then(function(result) {
   var items = result.characteristics.map(function(c) {
    var args={device:mac,service:c.service,characteristic:c.characteristic}, value=input(c.value), message=E('code',{},c.value);
    var buttons=[];
    if (c.flags.indexOf('read')>=0) buttons.push(self.button(T('read'),function(){return self.call('read',args).then(function(r){message.textContent=r.hex;}).catch(function(e){self.showError(e);});}));
    if (c.flags.indexOf('write')>=0 || c.flags.indexOf('write-without-response')>=0) buttons.push(self.button(T('write'),function(){return self.action('write',Object.assign({},args,{hex:value.value}));}));
    if (c.flags.indexOf('notify')>=0 || c.flags.indexOf('indicate')>=0) buttons.push(self.button(c.notifying ? T('unsubscribe') : T('notify'),function(){return self.action('notify',Object.assign({},args,{enabled:!c.notifying})).then(function(){ui.hideModal();return self.gatt(mac);});}));
    return E('div',{'class':'bt-gatt'},[E('small',{},c.service),E('div',{},c.characteristic),E('small',{},c.flags.join(', ')),message,value,E('div',{'class':'bt-actions'},buttons)]);
   });
   ui.showModal('GATT · '+mac,[items.length ? E('div',{'class':'bt-gatt-list'},items) : E('p',{},T('no_gatt')),self.button(T('close'),ui.hideModal)]);
  }).catch(function(e){self.showError(e);});
 },
 render: function(initial) {
  var self=this, cfg=initial.config || {autostart:false,adapter:'',alias:'',gateway:{enabled:false,transport:'le',devices:[],mqtt:{},http:{},subscriptions:[],serial:[]}}, g=cfg.gateway, m=g.mqtt, h=g.http;
  this.info=E('div'); this.jobs=E('div'); this.pairing=E('div'); this.devices=E('div'); this.forwardState=E('div');
  this.feedback=E('div',{'class':'bt-feedback','aria-live':'polite'}); this.streamState=E('p',{'class':'bt-muted'},T('stream_connecting'));
  this.autostart=check(cfg.autostart,T('auto')); this.adapter=select([['',T('automatic')]].concat((initial.adapters || []).map(function(a){return [a.id,a.id+' · '+a.Address];})),cfg.adapter); this.alias=input(cfg.alias);
  var selected=(initial.adapters || []).find(function(a){return a.id===initial.adapter;});
  this.alias.placeholder=selected && selected.Alias || '';
  this.transport=select([['auto',T('all')],['le','BLE'],['bredr','Classic']],g.transport);
  var scanMode=select([['auto',T('all')],['le','BLE'],['bredr','Classic']],'auto');
  this.gate=check(g.enabled,T('enable')); this.allow=input(g.devices.join(', '));
  this.mqtt=check(m.enabled,T('enable')); this.host=input(m.host); this.port=input(m.port || 1883,'number'); this.tls=check(m.tls,'TLS'); this.user=input(m.username); this.password=input('','password'); this.clearM=check(false,T('clear')); this.topic=input(m.topic || 'unisoc/bluetooth'); this.ha=check(m.ha_discovery,T('ha')); this.haprefix=input(m.ha_prefix || 'homeassistant');
  this.http=check(h.enabled,T('enable')); this.url=input(h.url,'url'); this.token=input('','password'); this.clearH=check(false,T('clear'));
  this.subs=E('div',{'class':'bt-rows'}); this.serial=E('div',{'class':'bt-rows'}); this.subRows=[]; this.serialRows=[];
  (g.subscriptions || []).forEach(function(s){self.addSub(s);}); (g.serial || []).forEach(function(s){self.addSerial(s);});
  this.eventLog=E('div',{'class':'bt-log'},T('waiting'));
  this.smart=S.create(this,cfg);
  this.lab=A.create(this);
  this.broadcast=B.create();
  var serialMac=input(''), serialHex=input('');
  var root=E('div',{'class':'unisoc-bt'},[
   E('link',{rel:'stylesheet',href:L.resource('unisoc-bluetooth/style.css')}),
   E('h2',{},T('title')),E('p',{'class':'bt-muted'},T('intro')),
   E('section',{'class':'bt-card'},[this.info,this.jobs,this.feedback,E('div',{'class':'bt-grid'},[field(T('adapter'),this.adapter),field(T('alias'),this.alias)]),this.autostart,
    E('p',{'class':'bt-muted'},T('startup_hint')),this.button(T('save_basic'),this.saveBasic,true),
    E('div',{'class':'bt-actions'},[this.button(T('power'),function(){return this.action('power',{enabled:true});},true),this.button(T('off'),function(){return this.action('power',{enabled:false});}),this.button(T('visible'),function(){return this.action('visibility',{enabled:true});}),this.button(T('refresh'),this.update)]),
    E('div',{'class':'bt-actions'},[scanMode,this.button(T('scan'),function(){return this.action('scan',{enabled:true,transport:scanMode.value});}),this.button(T('stop'),function(){return this.action('scan',{enabled:false});})]),this.pairing]),
   E('section',{'class':'bt-card'},[E('h3',{},T('devices')),E('p',{'class':'bt-muted'},T('devices_hint')),this.devices]),
   E('section',{'class':'bt-card'},[E('h3',{},T('gateway')),this.gate,E('p',{'class':'bt-muted'},T('gateway_hint')),E('div',{'class':'bt-grid'},[field(T('scan_transport'),this.transport),field(T('allowlist'),this.allow)]),
    E('div',{'class':'bt-columns'},[
     E('div',{},[E('h4',{},'MQTT'),this.mqtt,E('div',{'class':'bt-grid'},[field(T('host'),this.host),field(T('port'),this.port),field(T('username'),this.user),field(T('password'),this.password),field(T('topic'),this.topic),field(T('haprefix'),this.haprefix)]),this.tls,this.ha,this.clearM]),
     E('div',{},[E('h4',{},'HTTP webhook'),this.http,field('URL',this.url),field(T('token'),this.token),this.clearH])]),this.forwardState]),
   E('section',{'class':'bt-card'},[E('h3',{},T('subscriptions')),this.subs,this.button(T('add'),function(){this.addSub({});})]),
   E('section',{'class':'bt-card'},[E('h3',{},T('serial')),E('p',{'class':'bt-muted'},T('serial_hint')),this.serial,this.button(T('add'),function(){this.addSerial({});}),
    E('div',{'class':'bt-grid'},[field(T('address'),serialMac),field(T('hex'),serialHex)]),this.button(T('send'),function(){return this.action('serial_write',{device:serialMac.value,hex:serialHex.value});})]),
   this.smart.node,E('div',{'class':'bt-actions'},[this.button(T('save'),this.save,true)]),
   this.lab.node,
   E('section',{'class':'bt-card'},[E('h3',{},T('events')),this.streamState,this.eventLog]),
   E('section',{'class':'bt-card'},[E('h3',{},T('documentation')),E('p',{},T('docs'))])
  ]);
  this.status(initial); this.events=[]; this.seq=0;
  this.streamDisposed=false;
  this.onVisibility=function(){
   if(document.hidden)self.pauseStream();
   else if(root.isConnected&&!self.streamDisposed){self.openStream();self.update();}
  };
  document.addEventListener('visibilitychange',this.onVisibility);
  this.openStream();
  this.observer=new MutationObserver(function(){if(!root.isConnected)self.unload();});
  this.observer.observe(document.body,{childList:true,subtree:true});
  return root;
 },
 receiveEvents: function(result) {
  if(result.event_id!==this.eventId){this.seq=0;this.eventId=result.event_id;}
  (result.events || []).forEach(function(x){if(x.seq>this.seq){this.seq=x.seq;if(x.type!=='status')this.events.push(x);}},this);
  this.events=this.events.slice(-60);
  this.eventLog.replaceChildren();
  this.events.slice().reverse().forEach(function(x){this.eventLog.appendChild(E('div',{'class':'bt-event'},[
   E('time',{},new Date(x.time*1000).toLocaleTimeString()),E('div',{},[E('span',{},F.event(x)),F.details(JSON.stringify(x.data,null,2))])
  ]));},this);
  if(!this.events.length) this.eventLog.textContent=T('waiting');
  if((result.events.length||result.resync) && !this.refreshTimer)this.refreshTimer=setTimeout(function(){this.refreshTimer=null;this.update();}.bind(this),250);
 },
 pauseStream: function() {
  if(this.streamAbort)this.streamAbort.abort();
  this.streamAbort=null;
  if(this.streamTimer)clearTimeout(this.streamTimer);
  this.streamTimer=null;
 },
 unload: function() {
  this.streamDisposed=true;this.pauseStream();
  if(this.onVisibility)document.removeEventListener('visibilitychange',this.onVisibility);
  if(this.observer)this.observer.disconnect();
  if(this.refreshTimer)clearTimeout(this.refreshTimer);
 },
 openStream: async function() {
  if(this.streamDisposed||document.hidden||this.streamAbort)return;
  var controller=new AbortController();this.streamAbort=controller;
  var retry=true,cleanEnd=false,received=false;
  try {
   // Native subscriptions do not occupy CGI/script slots. Credentials stay in
   // the header, never in the URL or access log.
   var response=await fetch(rpc.getBaseURL().replace(/\/$/,'')+'/subscribe/unisoc-bluetooth-events',{
    headers:{Authorization:'Bearer '+rpc.getSessionID()},credentials:'same-origin',
    cache:'no-store',signal:controller.signal
   });
   if(response.status===401||response.status===403){retry=false;this.streamState.textContent=T('session_expired');throw new Error('Bluetooth event session expired');}
   if(!response.ok)throw new Error('Bluetooth event stream: '+response.status);
   if((response.headers.get('Content-Type')||'').indexOf('text/event-stream')<0){
    var failure=await response.json();
    if(failure.code===-13||failure.code===6){retry=false;this.streamState.textContent=T('session_expired');}
    throw new Error(failure.message||'Bluetooth event subscription failed');
   }
   var reader=response.body.getReader(),decoder=new TextDecoder(),buffer='';
   this.streamDelay=1000;
   this.streamState.textContent=T('stream_live');
   this.update();
   while(true){
    var chunk=await reader.read();if(chunk.done){cleanEnd=received;break;}
    buffer+=decoder.decode(chunk.value,{stream:true});
    var lines=buffer.split('\n');buffer=lines.pop();
    lines.forEach(function(line){if(line.indexOf('data: ')===0){this.receiveEvents(JSON.parse(line.slice(6)));received=true;}},this);
   }
  } catch(error) {
   if(!controller.signal.aborted){if(retry)this.streamState.textContent=T('stream_retry');console.warn(error.message);}
  } finally {
   var aborted=controller.signal.aborted;
   controller.abort();
   if(this.streamAbort!==controller)return;
   this.streamAbort=null;
   if(retry && !aborted&&!this.streamDisposed&&!document.hidden){
    if(!cleanEnd)this.streamState.textContent=T('stream_retry');
    this.streamTimer=setTimeout(this.openStream.bind(this),cleanEnd?0:this.streamDelay || 1000);
    this.streamDelay=Math.min((this.streamDelay || 1000)*2,15000);
   }
  }
 },
 addRow: function(target, rows, fields) {
  var row={fields:fields}; row.element=E('div',{'class':'bt-row'},Object.keys(fields).map(function(key){return field(T(key),fields[key]);}));
  row.element.appendChild(this.button(T('remove'),function(){row.element.remove();rows.splice(rows.indexOf(row),1);})); target.appendChild(row.element); rows.push(row);
 },
 addSub: function(s){this.addRow(this.subs,this.subRows,{address:input(s.device),service:input(s.service),characteristic:input(s.characteristic),codec:select(['hex','text','u8','u16le','i16le','u32le'].map(function(c){return[c,c];}),s.codec || 'hex'),name:input(s.name),unit:input(s.unit)});},
 addSerial: function(s){this.addRow(this.serial,this.serialRows,{address:input(s.device),channel:input(s.channel || 1,'number'),bind:input(s.bind || '127.0.0.1'),port:input(s.port || 0,'number')});},
 saveBasic: function(){
  var self=this;
  this.feedback.replaceChildren(F.message(T('working')));
  return this.call('save',{config:{autostart:checked(this.autostart),adapter:this.adapter.value,alias:this.alias.value}}).then(function(s){
   self.status(s);self.feedback.replaceChildren(F.message(T('basic_saved'),null,'success'));
  }).catch(function(e){self.showError(e);});
 },
 save: function(){
  var config={autostart:checked(this.autostart),adapter:this.adapter.value,alias:this.alias.value,gateway:{enabled:checked(this.gate),transport:this.transport.value,devices:this.allow.value.split(/[,\s]+/).filter(Boolean),
   mqtt:{enabled:checked(this.mqtt),host:this.host.value,port:Number(this.port.value),tls:checked(this.tls),username:this.user.value,password:this.password.value,clear_secret:checked(this.clearM),topic:this.topic.value,ha_discovery:checked(this.ha),ha_prefix:this.haprefix.value},
   http:{enabled:checked(this.http),url:this.url.value,token:this.token.value,clear_secret:checked(this.clearH)},
   subscriptions:this.subRows.map(function(row){var f=row.fields;return{device:f.address.value,service:f.service.value,characteristic:f.characteristic.value,codec:f.codec.value,name:f.name.value,unit:f.unit.value};}),
   serial:this.serialRows.map(function(row){var f=row.fields;return{device:f.address.value,channel:Number(f.channel.value),bind:f.bind.value,port:Number(f.port.value)};})}};
  var self=this; this.feedback.replaceChildren(F.message(T('working')));
  Object.assign(config.gateway,this.smart.configuration());
  return this.call('save',{config:config}).then(function(s){self.password.value='';self.token.value='';self.clearM.querySelector('input').checked=false;self.clearH.querySelector('input').checked=false;self.smart.saved();self.status(s);self.feedback.replaceChildren(F.message(T('saved'),null,'success'));}).catch(function(e){self.showError(e);});
 },
 handleSaveApply:null,handleSave:null,handleReset:null
});
