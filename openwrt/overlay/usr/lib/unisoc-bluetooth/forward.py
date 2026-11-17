# SPDX-License-Identifier: MIT
"""Bounded, independent MQTT and webhook consumers. No radio polling."""
import asyncio
import json
import ssl
import urllib.request
from collections import OrderedDict
import time
from pathlib import Path

from core import decode, Invalid, text, atomic_json
from catalog import ReportPolicy
import homeassistant as ha

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Forwarder:
    def __init__(self, on_command=None, registry=None):
        self.tasks = []
        self.queues = {}
        self.config = {}
        self.state = {'mqtt': 'disabled', 'http': 'disabled', 'dropped': 0, 'errors': 0}
        self.on_command=on_command;self.registry=Path(registry) if registry else None
        self.discovery={};self.policy=ReportPolicy();self.requests=OrderedDict()
        self.command_tasks=set();self.client=None;self.snapshot=lambda:[]
        if self.registry:
            try:self.discovery=json.loads(self.registry.read_text())
            except (FileNotFoundError,ValueError):pass

    async def configure(self, config):
        await self.close()
        self.config = config
        for kind in ('mqtt', 'http'):
            self.state[kind] = 'disabled'
            if config['enabled'] and config[kind]['enabled']:
                self.queues[kind] = asyncio.Queue(128)
                self.tasks.append(asyncio.create_task(getattr(self, kind)()))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.queues.clear()
        for task in self.command_tasks:task.cancel()
        await asyncio.gather(*list(self.command_tasks),return_exceptions=True)
        self.command_tasks.clear();self.client=None

    def push(self, event):
        if not self.config.get('enabled'):
            return
        data = event['data']
        if self.config['devices'] and data.get('Address', data.get('address')) not in self.config['devices']:
            return
        if not self.policy.accept(event,self.config['policy']):return
        for queue in self.queues.values():
            if queue.full():
                queue.get_nowait()
                self.state['dropped'] += 1
            queue.put_nowait(event)

    def post(self, event):
        cfg = self.config['http']
        headers = {'Content-Type': 'application/json'}
        if cfg['token']:
            headers['Authorization'] = 'Bearer ' + cfg['token']
        request = urllib.request.Request(cfg['url'], json.dumps(event).encode(), headers, method='POST')
        # Do not leak the configured bearer token to a redirect target.
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=5) as response:
            response.read(4096)

    async def http(self):
        queue = self.queues['http']
        while True:
            event = await queue.get()
            try:
                await asyncio.to_thread(self.post, event)
                self.state['http'] = 'connected'
            except Exception:
                # Error text may contain a credential-bearing URL. Keep diagnostics bounded and secret-free.
                self.state['http'] = 'delivery_failed'
                self.state['errors'] += 1

    def sensor(self, event):
        data = event['data']
        mac = data.get('Address', data.get('address', ''))
        if event['type'] == 'device' and 'RSSI' in data:
            return mac, 'rssi', data['RSSI'], 'Signal strength', 'dBm'
        if event['type'] == 'gatt':
            for sub in self.config['subscriptions']:
                if (mac, data.get('service'), data.get('characteristic')) == (
                        sub['device'], sub['service'], sub['characteristic']):
                    value = decode(bytes.fromhex(data['hex']), sub['codec'])
                    return mac, sub['service'].split('-')[0] + '_' + sub['characteristic'].split('-')[0], value, sub['name'], sub['unit']
        return None

    async def mqtt(self):
        import aiomqtt
        cfg, queue = self.config['mqtt'], self.queues['mqtt']
        delay = 1
        while True:
            try:
                self.state['mqtt'] = 'connecting'
                async with aiomqtt.Client(hostname=cfg['host'], port=cfg['port'],
                        username=cfg['username'] or None, password=cfg['password'] or None,
                        tls_context=ssl.create_default_context() if cfg['tls'] else None,
                        timeout=8, keepalive=60,
                        will=aiomqtt.Will(cfg['topic']+'/availability','offline',retain=True)) as client:
                    self.client=client
                    self.state['mqtt'] = 'connected'
                    delay=1
                    await client.publish(cfg['topic']+'/availability','online',retain=True)
                    if cfg['ha_discovery']:await client.subscribe(cfg['ha_prefix']+'/status')
                    for command in self.config['commands']:
                        await client.subscribe(cfg['topic']+'/commands/'+command['id']+'/set',qos=1)
                    await self.announce(client)
                    sender=asyncio.create_task(self.send_events(client,queue))
                    receiver=asyncio.create_task(self.receive_commands(client))
                    try:
                        done,_=await asyncio.wait([sender,receiver],return_when=asyncio.FIRST_COMPLETED)
                        for task in done:task.result()
                    finally:
                        sender.cancel();receiver.cancel()
                        await asyncio.gather(sender,receiver,return_exceptions=True)
                        for task in list(self.command_tasks):task.cancel()
                        await asyncio.gather(*list(self.command_tasks),return_exceptions=True)
                        self.command_tasks.clear()
                        try:await client.publish(cfg['topic']+'/availability','offline',retain=True)
                        except Exception:pass
                        self.client=None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state['mqtt'] = 'connection_or_delivery_failed'
                self.state['errors'] += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def publish_discovery(self, client, topic, doc):
        encoded=json.dumps(doc,sort_keys=True,separators=(',',':'))
        if self.discovery.get(topic)!=encoded:
            await client.publish(topic,encoded,retain=True)
            self.discovery[topic]=encoded
            if self.registry:atomic_json(self.registry,self.discovery)

    async def announce(self, client):
        cfg=self.config;prefix=cfg['mqtt']['topic']
        valid_commands={ha.command_discovery(cfg,c)[0] for c in cfg['commands']} if cfg['mqtt']['ha_discovery'] else set()
        for topic in list(self.discovery):
            if ('_command_' in topic and topic not in valid_commands) or not cfg['mqtt']['ha_discovery']:
                await client.publish(topic,'',retain=True);self.discovery.pop(topic)
            elif cfg['mqtt']['ha_discovery']:
                await client.publish(topic,self.discovery[topic],retain=True)
        if self.registry:atomic_json(self.registry,self.discovery)
        if cfg['mqtt']['ha_discovery']:
            for command in cfg['commands']:
                topic,doc=ha.command_discovery(cfg,command)
                await self.publish_discovery(client,topic,doc)
        for d in self.snapshot():
            mac=d['address']
            if cfg['devices'] and mac not in cfg['devices']:continue
            await client.publish(prefix+'/devices/'+ha.device_id(mac)+'/availability','online' if d.get('online') else 'offline',retain=True)
            if cfg['mqtt']['ha_discovery']:
                for reading in d.get('measurements',[]):
                    if reading['domain']!='event':await self.publish_reading(client,mac,reading)

    async def publish_reading(self, client, mac, reading):
        if reading['domain']=='event' and reading['value']=='none':return
        topic,doc,state=ha.discovery(self.config,mac,reading)
        device=next((d for d in self.snapshot() if d['address']==mac),{})
        for key in ('manufacturer','model'):
            if device.get(key):doc['device'][key]=device[key]
        await self.publish_discovery(client,topic,doc)
        await client.publish(state,ha.state_payload(reading),retain=reading['domain']!='event')

    async def send_events(self, client, queue):
        cfg=self.config
        while True:
            event=await queue.get();d=event['data'];mac=d.get('Address',d.get('address','unknown'))
            await client.publish(cfg['mqtt']['topic']+'/'+ha.device_id(mac)+'/events',json.dumps(event),qos=0)
            if event['type']=='availability':
                await client.publish(cfg['mqtt']['topic']+'/devices/'+ha.device_id(mac)+'/availability','online' if d['online'] else 'offline',retain=True)
            if cfg['mqtt']['ha_discovery']:
                try:
                    mac,readings=ha.readings(event,cfg)
                    for reading in readings:await self.publish_reading(client,mac,reading)
                except (ValueError,TypeError):self.state['errors']+=1

    async def receive_commands(self, client):
        cfg=self.config
        async for message in client.messages:
            topic=str(message.topic)
            if topic==cfg['mqtt']['ha_prefix']+'/status':
                if bytes(message.payload)==b'online' and not message.retain:await self.announce(client)
                continue
            start=cfg['mqtt']['topic']+'/commands/'
            if message.retain or not topic.startswith(start) or not topic.endswith('/set'):continue
            key=topic[len(start):-4]
            command=next((c for c in cfg['commands'] if c['id']==key),None)
            if not command or len(message.payload)>2048:continue
            if len(self.command_tasks)>=16:
                self.state['errors']+=1
                await client.publish(start+key+'/result',json.dumps({'ok':False,'error':'bluetooth_command_queue_full'}))
                continue
            task=asyncio.create_task(self.command(client,command,bytes(message.payload)))
            self.command_tasks.add(task);task.add_done_callback(self.command_tasks.discard)

    async def command(self, client, command, payload):
        cfg=self.config;topic=cfg['mqtt']['topic']+'/commands/'+command['id'];request=None
        try:
            value=payload.decode('utf-8')
            if value.startswith('{'):
                body=json.loads(value)
                if set(body)-{'id','value'}:raise Invalid('invalid_command_request')
                request=text(body.get('id',''),64)
                if not request:raise Invalid('command_request_id_required')
                value=body.get('value')
                key=(command['id'],request)
                if key in self.requests:
                    previous=self.requests[key]
                    if previous is not None:await client.publish(topic+'/result',json.dumps(previous))
                    return
                self.requests[key]=None
                if len(self.requests)>256:self.requests.popitem(last=False)
            if cfg['devices'] and command['device'] not in cfg['devices']:raise Invalid('command_device_not_allowed')
            if self.on_command is None:raise Invalid('bluetooth_command_handler_unavailable')
            result=await self.on_command(command,value)
            result=dict(result,ok=True,id=request)
            if result.get('state_confirmed') and 'value' in result:await client.publish(topic+'/state',str(result['value']),retain=True)
        except (Invalid,ValueError,OSError,asyncio.TimeoutError) as error:
            result={'ok':False,'id':request,'error':str(error)[:256] if isinstance(error,Invalid) else 'bluetooth_command_failed'}
        if request:self.requests[(command['id'],request)]=result
        await client.publish(topic+'/result',json.dumps(result))
