# SPDX-License-Identifier: MIT
"""Validated smart-home configuration. Runtime radio state is never imported."""
import base64
import copy
import ipaddress
import re

DEFAULTS = {
    'known_devices': [], 'commands': [],
    'policy': {'scan_seconds': 30, 'pause_seconds': 0, 'min_interval': 2,
               'max_interval': 60, 'change_only': True, 'rssi_delta': 5},
    'proxy': {'enabled': False, 'bind': '127.0.0.1', 'port': 6053, 'psk': '',
              'name': 'unisoc-bluetooth', 'active': False, 'slots': 3, 'devices': []},
}

def validate_features(source, old, target):
    from core import Invalid, address, boolean, hex_bytes, integer, text, uuid
    known = source.get('known_devices', old.get('known_devices', []))
    if not isinstance(known, list) or len(known) > 256:
        raise Invalid('invalid_known_devices')
    previous = {d['device']: d for d in old.get('known_devices', [])}
    used = set()
    for d in known:
        if not isinstance(d, dict) or set(d) - {'device','name','room','manufacturer','model','bindkey','clear_bindkey','auto_connect','offline_after','decoder','template'}:
            raise Invalid('invalid_known_device')
        mac = address(d.get('device'))
        if mac in used:
            raise Invalid('duplicate_known_device')
        used.add(mac)
        entry = {k: text(d.get(k, ''), 64) for k in ('name','room','manufacturer','model')}
        entry.update(device=mac, auto_connect=boolean(d.get('auto_connect', False)),
                     offline_after=integer(d.get('offline_after', 300), 0, 86400),
                     decoder=text(d.get('decoder','auto'),16), template=text(d.get('template',''),64))
        if entry['decoder'] not in ('auto','bthome','theengs','raw'):
            raise Invalid('invalid_decoder')
        key = text(d.get('bindkey',''),32)
        if key and not re.fullmatch('[a-fA-F0-9]{32}',key):
            raise Invalid('invalid_bindkey')
        entry['bindkey'] = '' if boolean(d.get('clear_bindkey',False)) else key or previous.get(mac,{}).get('bindkey','')
        target['known_devices'].append(entry)
    policy = source.get('policy', old.get('policy', DEFAULTS['policy']))
    if not isinstance(policy,dict) or set(policy)-set(DEFAULTS['policy']):
        raise Invalid('invalid_scan_policy')
    bounds={'scan_seconds':(5,300),'pause_seconds':(0,3600),'min_interval':(0,3600),
            'max_interval':(1,86400),'rssi_delta':(0,50)}
    for k,default in DEFAULTS['policy'].items():
        value=policy.get(k,old.get('policy',{}).get(k,default))
        target['policy'][k]=boolean(value) if k=='change_only' else integer(value,*bounds[k])
    if target['policy']['max_interval'] < target['policy']['min_interval']:
        raise Invalid('invalid_report_intervals')
    commands=source.get('commands',old.get('commands',[]))
    if not isinstance(commands,list) or len(commands)>128:
        raise Invalid('invalid_commands')
    used=set()
    for d in commands:
        fields={'id','name','device','kind','domain','service','characteristic','on','off','hex','codec','min','max','step'}
        if not isinstance(d,dict) or set(d)-fields:
            raise Invalid('invalid_command')
        key=text(d.get('id',''),48)
        if not re.fullmatch('[a-zA-Z0-9_-]{1,48}',key) or key in used:
            raise Invalid('invalid_command_id')
        used.add(key)
        entry={'id':key,'name':text(d.get('name',key),64),'device':address(d.get('device')),
               'kind':d.get('kind','write'),'domain':d.get('domain','button')}
        if entry['kind'] not in ('write','read','serial_write','connect','disconnect') or entry['domain'] not in ('button','switch','number'):
            raise Invalid('invalid_command')
        if entry['kind'] in ('write','read'):
            entry.update(service=uuid(d.get('service')),characteristic=uuid(d.get('characteristic')))
        for k in ('hex','on','off'):
            entry[k]=hex_bytes(d.get(k,'')).hex()
        entry['codec']=d.get('codec','u8')
        if entry['codec'] not in ('u8','u16le','i16le','u32le'):
            raise Invalid('invalid_codec')
        entry['min']=integer(d.get('min',0),-2147483648,4294967295)
        entry['max']=integer(d.get('max',100),entry['min'],4294967295)
        entry['step']=integer(d.get('step',1),1,4294967295)
        if entry['domain']=='number':
            bounds={'u8':(0,255),'u16le':(0,65535),'i16le':(-32768,32767),'u32le':(0,4294967295)}
            low,high=bounds[entry['codec']]
            if entry['min']<low or entry['max']>high:raise Invalid('invalid_command_values')
        if entry['domain'] in ('switch','number') and entry['kind'] not in ('write','serial_write'):
            raise Invalid('invalid_command')
        if entry['domain']=='switch' and (not entry['on'] or not entry['off'] or entry['on']==entry['off']):
            raise Invalid('invalid_command_values')
        target['commands'].append(entry)
    proxy=source.get('proxy',old.get('proxy',DEFAULTS['proxy']))
    if not isinstance(proxy,dict) or set(proxy)-(set(DEFAULTS['proxy'])|{'clear_secret'}):
        raise Invalid('invalid_proxy')
    prev=old.get('proxy',DEFAULTS['proxy']);p=target['proxy']
    for k in ('enabled','active'):p[k]=boolean(proxy.get(k,prev[k]))
    p['port']=integer(proxy.get('port',prev['port']),1024,65535)
    p['slots']=integer(proxy.get('slots',prev['slots']),1,8)
    p['bind']=text(proxy.get('bind',prev['bind']),45)
    try:ip=ipaddress.ip_address(p['bind'])
    except ValueError as error:raise Invalid('invalid_proxy_bind') from error
    if ip.is_multicast or not (ip.is_private or ip.is_loopback) or ip.is_unspecified:
        raise Invalid('proxy_bind_must_be_local')
    p['name']=text(proxy.get('name',prev['name']),63)
    if not re.fullmatch('[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',p['name']):
        raise Invalid('invalid_proxy_name')
    p['psk']=text(proxy.get('psk',''),64) or prev['psk']
    if boolean(proxy.get('clear_secret',False)):p['psk']=''
    if p['psk']:
        try:
            if len(base64.b64decode(p['psk'],validate=True))!=32:raise ValueError()
        except ValueError as error:raise Invalid('invalid_proxy_key') from error
    allowed=proxy.get('devices',prev['devices'])
    if not isinstance(allowed,list) or len(allowed)>256:raise Invalid('invalid_device_list')
    p['devices']=list(dict.fromkeys(address(x) for x in allowed))
    if p['enabled'] and (not target['enabled'] or not p['psk'] or target['transport']=='bredr'):
        raise Invalid('proxy_requires_gateway_key_and_ble')
    if p['enabled'] and p['active'] and not p['devices']:
        raise Invalid('proxy_active_requires_allowlist')

def public_features(config):
    for d in config['gateway']['known_devices']:
        d['bindkey_set']=bool(d['bindkey']);d['bindkey']=''
    p=config['gateway']['proxy'];p['psk_set']=bool(p['psk']);p['psk']=''
