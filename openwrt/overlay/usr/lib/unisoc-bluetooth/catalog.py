# SPDX-License-Identifier: MIT
"""Bounded live device catalogue and event-driven presence expiry."""
import asyncio
from collections import OrderedDict
import copy
import time
from bthome import SERVICE, decode_payload
from core import Invalid,decode

class Catalog:
    def __init__(self, emit):
        self.emit=emit;self.known={};self.live=OrderedDict();self.timers={};self.counters={}

    def configure(self, known):
        previous=self.known
        self.known={d['device']:copy.deepcopy(d) for d in known}
        for mac,entry in self.known.items():
            if previous.get(mac,{}).get('bindkey','')!=entry.get('bindkey',''):self.counters.pop(mac,None)
        for mac in self.live:self.expire_later(mac)

    def observe(self, data):
        mac=data.get('Address')
        if not mac:return None
        now=time.time();entry=self.live.setdefault(mac,{'address':mac,'measurements':[]})
        old_online=entry.get('online',False)
        self.live.move_to_end(mac)
        entry.update(last_seen=now,online=True,rssi=data.get('RSSI'),name=data.get('Name') or data.get('Alias') or '',
                     connected=data.get('Connected',False),paired=data.get('Paired',False))
        self.trim()
        self.expire_later(mac)
        if not old_online:self.emit('availability',{'address':mac,'online':True})
        cfg=self.known.get(mac,{})
        packet=data.get('ServiceData',{}).get(SERVICE)
        if 'changed' in data and 'ServiceData' not in data['changed']:return None
        if cfg.get('decoder') in ('raw','theengs') or packet is None:return None
        try:
            parsed=decode_payload(mac,packet,cfg.get('bindkey',''))
            entry['error']='';entry['protocol']=parsed['protocol'];entry['trigger_based']=parsed['trigger_based']
            previous=self.counters.get(mac)
            if 'counter' in parsed:
                token=('counter',parsed['counter'])
                if previous and previous[0]=='counter' and token[1]<=previous[1]:return None
            elif 'packet_id' in parsed:
                token=('packet',parsed['packet_id'])
                if previous==token:return None
            else:token=None
            if token:self.counters[mac]=token
            entry['measurements']=parsed['measurements']
            entry['measurement_time']=now
            entry['encrypted']=parsed['encrypted']
            self.expire_later(mac)
            return dict(parsed,address=mac)
        except (Invalid,ValueError,ImportError) as error:
            # Never report cached values as a freshly authenticated measurement.
            message=str(error) if isinstance(error,Invalid) else 'bluetooth_decoder_dependency_missing'
            if entry.get('error')!=message:self.emit('decoder_error',{'address':mac,'error':message})
            entry['error']=message
            return None

    def expire_later(self, mac):
        timer=self.timers.pop(mac,None)
        if timer:timer.cancel()
        entry=self.live[mac];delay=self.known.get(mac,{}).get('offline_after',300)
        if not delay or entry.get('trigger_based'):return
        remaining=max(0,entry['last_seen']+delay-time.time())
        self.timers[mac]=asyncio.get_running_loop().call_later(remaining,self.expire,mac)

    def expire(self, mac):
        self.timers.pop(mac,None)
        if mac in self.live:
            if self.live[mac].get('connected'):return
            self.live[mac]['online']=False
            self.emit('availability',{'address':mac,'online':False})

    def gatt(self, data, subscriptions, connected=False):
        mac=data.get('address')
        if not mac:return
        entry=self.live.setdefault(mac,{'address':mac,'measurements':[]})
        self.live.move_to_end(mac);self.trim()
        was_online=entry.get('online',False)
        entry.update(last_seen=time.time(),online=True,connected=connected)
        if not was_online:self.emit('availability',{'address':mac,'online':True})
        self.expire_later(mac)
        for sub in subscriptions:
            if (mac,data.get('service'),data.get('characteristic'))!=(sub['device'],sub['service'],sub['characteristic']):continue
            try:value=decode(bytes.fromhex(data['hex']),sub['codec'])
            except (Invalid,ValueError):entry['error']='invalid_value_size';return
            key=sub['service']+'_'+sub['characteristic']
            reading={'key':key,'name':sub['name'] or sub['characteristic'],'value':value,'unit':sub['unit'],'device_class':'','domain':'sensor'}
            entry['measurements']=[m for m in entry['measurements'] if m['key']!=key]+[reading]
            entry.update(measurement_time=time.time(),error='')
            break

    def trim(self):
        if len(self.live)<=512:return
        stale=next((m for m in self.live if m not in self.known),next(iter(self.live)))
        self.live.pop(stale);self.counters.pop(stale,None)
        timer=self.timers.pop(stale,None)
        if timer:timer.cancel()

    def snapshot(self):
        result=[]
        for mac in dict.fromkeys([*self.known,*self.live]):
            meta={k:v for k,v in self.known.get(mac,{}).items() if k!='bindkey'}
            meta.update(self.live.get(mac,{'address':mac,'online':False,'measurements':[]}))
            # User-chosen labels take precedence over advertising names.
            for k in ('name','room','model','manufacturer'):
                if self.known.get(mac,{}).get(k):meta[k]=self.known[mac][k]
            result.append(meta)
        return result

    def decoded(self, result):
        entry=self.live.get(result['address'])
        if entry is not None:
            entry.update(protocol=result['protocol'],model=result.get('model',''),manufacturer=result.get('manufacturer',''),
                         measurements=result['measurements'],measurement_time=time.time(),error='')
            self.emit('measurement',result)

    def close(self):
        for timer in self.timers.values():timer.cancel()
        self.timers.clear()

class ReportPolicy:
    def __init__(self):self.sent=OrderedDict()

    def accept(self, event, policy):
        # Discrete events and link transitions must never be suppressed as RSSI noise.
        if event['type'] not in ('device','measurement'):return True
        data=event['data'];mac=data.get('Address',data.get('address',''))
        if event['type']=='measurement' and any(x['domain']=='event' for x in data.get('measurements',[])):return True
        key=(event['type'],mac);now=time.monotonic();old=self.sent.get(key)
        comparison={k:v for k,v in data.items() if k not in ('RSSI','rssi','packet_id','counter','changed')}
        rssi=data.get('RSSI',data.get('rssi'))
        if old:
            before,stamp,signal=old
            transition=event['type']=='device' and any(before.get(k)!=comparison.get(k) for k in ('Connected','Paired','ServicesResolved'))
            if not transition and now-stamp<policy['max_interval']:
                if now-stamp<policy['min_interval']:return False
                if policy['change_only'] and comparison==before and (rssi is None or signal is None or rssi==signal or abs(rssi-signal)<policy['rssi_delta']):return False
        self.sent[key]=(comparison,now,rssi);self.sent.move_to_end(key)
        if len(self.sent)>1024:self.sent.popitem(last=False)
        return True
