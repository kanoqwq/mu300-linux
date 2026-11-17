# SPDX-License-Identifier: MIT
"""HA MQTT entities and explicit command topics, independent from BlueZ."""
import json
from core import decode
from bthome import BUTTONS

def device_id(mac):return mac.replace(':','').lower()

def metadata(config, mac):
    d=next((d for d in config.get('known_devices',[]) if d['device']==mac),{})
    result={'identifiers':['bluetooth_'+mac],'name':d.get('name') or 'Bluetooth '+mac}
    for key in ('manufacturer','model'):
        if d.get(key):result[key]=d[key]
    if d.get('room'):result['suggested_area']=d['room']
    return result

def availability(config, mac):
    return [{'topic':config['mqtt']['topic']+'/availability'},
            {'topic':config['mqtt']['topic']+'/devices/'+device_id(mac)+'/availability'}]

def readings(event, config):
    d=event['data'];mac=d.get('Address',d.get('address',''));items=[]
    if event['type']=='device' and 'RSSI' in d:
        items=[{'key':'rssi','value':d['RSSI'],'name':'Signal strength','unit':'dBm','domain':'sensor','device_class':'signal_strength','diagnostic':True}]
    elif event['type']=='measurement':items=d['measurements']
    elif event['type']=='gatt':
        for sub in config['subscriptions']:
            if (mac,d.get('service'),d.get('characteristic'))==(sub['device'],sub['service'],sub['characteristic']):
                items=[{'key':sub['service']+'_'+sub['characteristic'],'value':decode(bytes.fromhex(d['hex']),sub['codec']),
                        'name':sub['name'] or sub['characteristic'],'unit':sub['unit'],'domain':'sensor','device_class':''}]
                break
    return mac,items

def discovery(config, mac, reading):
    identity=device_id(mac)+'_'+reading['key'];domain=reading['domain']
    state=config['mqtt']['topic']+'/'+identity+'/state'
    doc={'name':reading['name'].replace('_',' ').title(),'unique_id':'unisoc_bt_'+identity,
         'state_topic':state,'device':metadata(config,mac),'availability':availability(config,mac),'availability_mode':'all'}
    if reading.get('unit'):doc['unit_of_measurement']=reading['unit']
    if reading.get('device_class') and reading['device_class']!='generic':doc['device_class']=reading['device_class']
    if reading.get('diagnostic'):doc['entity_category']='diagnostic'
    if domain=='binary_sensor':doc.update(payload_on='true',payload_off='false')
    if domain=='event':
        doc.pop('device_class',None)
        doc['event_types']=list(BUTTONS.values()) if reading['name']=='button' else ['none','rotate_left','rotate_right','unknown']
        if reading['value'] not in doc['event_types']:doc['event_types'].append(reading['value'])
        doc['device_class']='button'
    return config['mqtt']['ha_prefix']+'/'+domain+'/unisoc_bt_'+identity+'/config',doc,state

def command_discovery(config, command):
    key='command_'+command['id'];mac=command['device'];base=config['mqtt']['topic']+'/commands/'+command['id']
    doc={'name':command['name'],'unique_id':'unisoc_bt_'+device_id(mac)+'_'+key,
         'command_topic':base+'/set','device':metadata(config,mac),'availability':availability(config,mac),'availability_mode':'all'}
    if command['domain']=='button':doc['payload_press']='PRESS'
    else:
        doc['state_topic']=base+'/state';doc['optimistic']=False
        if command['domain']=='number':doc.update(min=command['min'],max=command['max'],step=command['step'],mode='box')
    return config['mqtt']['ha_prefix']+'/'+command['domain']+'/unisoc_bt_'+device_id(mac)+'_'+key+'/config',doc

def state_payload(reading):
    if reading['domain']=='event':return json.dumps({'event_type':reading['value']})
    if isinstance(reading['value'],str):return reading['value']
    return json.dumps(reading['value'])
