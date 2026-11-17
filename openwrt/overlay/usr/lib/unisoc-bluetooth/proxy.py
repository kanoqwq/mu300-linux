# SPDX-License-Identifier: MIT
"""Noise-authenticated ESPHome-compatible BLE proxy sharing our BlueZ owner.

No separate scanner, HCI socket or automatic pairing. Active operations are
limited to explicitly allowed devices, and cleanup releases only owned links.
"""
import asyncio
import base64
import contextlib
import ipaddress
import struct
from collections import OrderedDict
from core import Invalid, address
from bluez import ADAPTER, DEVICE, SERVICE, CHAR, BLUEZ
from proxy_wire import field, message, one, parse, uuid_fields, handle, advertisement

DESC=BLUEZ+'.GattDescriptor1'
PROPERTIES={'broadcast':1,'read':2,'write-without-response':4,'write':8,'notify':16,'indicate':32,'authenticated-signed-writes':64,'extended-properties':128}

class Proxy:
    def __init__(self, gateway):
        self.gateway=gateway;self.config={};self.server=None;self.clients=set();self.owned={};self.mdns=None;self.info=None
        self.error='';self.rejected=0

    def snapshot(self):
        return {'enabled':bool(self.server),'clients':len(self.clients),'active_connections':len(self.owned),
                'slots':self.config.get('slots',0),'error':self.error,'rejected':self.rejected}

    async def configure(self, cfg):
        await self.close();self.config=cfg;self.error=''
        if not cfg['enabled']:return
        from noise.connection import NoiseConnection
        # Explicit bind must exist on this machine; configuration does not open firewall rules.
        self.server=await asyncio.start_server(self.accept,cfg['bind'],cfg['port'],limit=65536)
        if not ipaddress.ip_address(cfg['bind']).is_loopback:
            from zeroconf import ServiceInfo,IPVersion
            from zeroconf.asyncio import AsyncZeroconf
            mac=self.mac()
            self.mdns=AsyncZeroconf(interfaces=[cfg['bind']],ip_version=IPVersion.V6Only if ipaddress.ip_address(cfg['bind']).version==6 else IPVersion.V4Only)
            self.info=ServiceInfo('_esphomelib._tcp.local.',cfg['name']+'._esphomelib._tcp.local.',
                addresses=[ipaddress.ip_address(cfg['bind']).packed],port=cfg['port'],
                properties={'version':'2026.1.0','mac':mac.replace(':',''),'board':'unisoc-bluez',
                            'api_encryption':'Noise_NNpsk0_25519_ChaChaPoly_SHA256'},server=cfg['name']+'.local.')
            await self.mdns.async_register_service(self.info)

    def mac(self):
        blue=self.gateway.bluez
        return address(blue.objects.get(blue.adapter_path(),{}).get(ADAPTER,{}).get('Address',''))

    async def accept(self, reader, writer):
        if len(self.clients)>=4:
            writer.close();await writer.wait_closed();return
        session=Session(self,reader,writer);self.clients.add(session)
        try:await session.run()
        except (Invalid,ValueError,OSError,asyncio.TimeoutError,asyncio.IncompleteReadError):self.rejected+=1
        except Exception:
            # Noise failures must not include PSKs or plaintext handshake material in UI/logs.
            self.rejected+=1
        finally:
            await session.close();self.clients.discard(session)

    def event(self, event):
        for client in list(self.clients):client.event(event)

    async def free(self):
        allocated=[int(mac.replace(':',''),16) for mac in self.owned]
        payload=message((1,self.config['slots']-len(allocated)),(2,self.config['slots']))+b''.join(field(3,n) for n in allocated)
        for client in list(self.clients):
            if client.ready and client.free_subscribed:await client.send(81,payload)

    async def close(self):
        if self.server:self.server.close();await self.server.wait_closed();self.server=None
        for client in list(self.clients):await client.close()
        if self.mdns:
            if self.info:await self.mdns.async_unregister_service(self.info)
            await self.mdns.async_close();self.mdns=None;self.info=None

class Session:
    def __init__(self, proxy, reader, writer):
        self.proxy=proxy;self.reader=reader;self.writer=writer;self.noise=None;self.ready=False;self.closed=False
        self.subscribed=False;self.free_subscribed=False;self.queue=asyncio.Queue(128);self.write_lock=asyncio.Lock()
        self.tasks=set();self.sender=None;self.notifications=set();self.device_locks={};self.connections=set()

    async def frame(self):
        header=await asyncio.wait_for(self.reader.readexactly(3),90 if self.ready else 10)
        if header[0]!=1:raise Invalid('proxy_requires_encryption')
        size=int.from_bytes(header[1:],'big')
        if size>32768:raise Invalid('invalid_proxy_packet')
        return await asyncio.wait_for(self.reader.readexactly(size),10)

    async def raw(self,data):
        self.writer.write(b'\x01'+len(data).to_bytes(2,'big')+data)
        await asyncio.wait_for(self.writer.drain(),5)

    async def send(self, kind, data=b''):
        async with self.write_lock:
            if self.closed:return
            encrypted=self.noise.encrypt(struct.pack('>HH',kind,len(data))+data)
            await self.raw(encrypted)

    async def run(self):
        from noise.connection import NoiseConnection
        hello=await self.frame()
        if hello:raise Invalid('invalid_proxy_handshake')
        cfg=self.proxy.config
        await self.raw(b'\x01'+cfg['name'].encode()+b'\0'+self.proxy.mac().encode()+b'\0')
        self.noise=NoiseConnection.from_name(b'Noise_NNpsk0_25519_ChaChaPoly_SHA256')
        self.noise.set_as_responder();self.noise.set_psks(base64.b64decode(cfg['psk'],validate=True))
        self.noise.set_prologue(b'NoiseAPIInit\x00\x00');self.noise.start_handshake()
        handshake=await self.frame()
        if not handshake or handshake[0]!=0:raise Invalid('invalid_proxy_handshake')
        self.noise.read_message(handshake[1:]);await self.raw(b'\0'+self.noise.write_message())
        self.ready=True;self.sender=asyncio.create_task(self.send_events())
        self.sender.add_done_callback(lambda task:self.writer.close() if not task.cancelled() and task.exception() else None)
        while not self.closed:
            data=self.noise.decrypt(await self.frame())
            if len(data)<4:raise Invalid('invalid_proxy_packet')
            kind,length=struct.unpack('>HH',data[:4])
            if length!=len(data)-4:raise Invalid('invalid_proxy_packet')
            fields=parse(data[4:])
            if kind in (68,70,73,75,76,77,78):
                if len(self.tasks)>=8:raise Invalid('proxy_command_queue_full')
                task=asyncio.create_task(self.operation(kind,fields));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
            else:await self.request(kind,fields)

    async def request(self,kind,fields):
        cfg=self.proxy.config
        if kind==1:await self.send(2,message((1,1),(2,10),(3,'Unisoc BlueZ Gateway'),(4,cfg['name'])))
        elif kind==3:await self.send(4)  # Older native API clients; Noise already authenticated.
        elif kind==5:await self.send(6);self.closed=True
        elif kind==7:await self.send(8)
        elif kind==9:
            flags=33+(6 if cfg['active'] else 0)  # Advertisement streaming, raw AD, optional v3 connections/cache.
            await self.send(10,message((2,cfg['name']),(3,self.proxy.mac()),(4,'2026.1.0'),
                (6,'Unisoc BlueZ Gateway'),(8,'unisoc.bluetooth-gateway'),(9,'1'),
                (12,'Unisoc community'),(13,cfg['name']),(15,flags),(18,self.proxy.mac()),(19,True)))
        elif kind==11:await self.send(19)
        elif kind in (20,8,37):pass
        elif kind==66:
            self.subscribed=True
        elif kind==87:self.subscribed=False
        elif kind==80:self.free_subscribed=True;await self.proxy.free()
        # Unknown optional API requests are ignored, never mapped to arbitrary RPC.

    def event(self,event):
        if not self.ready or self.closed:return
        if event['type']=='device' and event['data'].get('Address') in self.connections and not event['data'].get('Connected'):
            mac=event['data']['Address'];self.connections.discard(mac);self.proxy.owned.pop(mac,None)
            prefix='/org/bluez/'+self.proxy.gateway.bluez.adapter+'/dev_'+mac.replace(':','_')+'/'
            self.notifications={p for p in self.notifications if not p.startswith(prefix)}
            event={'type':'link','data':{'address':mac}}
        elif event['type']=='device' and self.subscribed:
            d=event['data'];mac=d.get('Address')
            allowed=self.proxy.gateway.config['gateway']['devices']
            if allowed and mac not in allowed:return
            if not ('ServiceData' in d or 'ManufacturerData' in d):return
        elif event['type']=='gatt':
            d=event['data']
            path=d.get('path')
            if path is None:
                try:path=self.proxy.gateway.bluez.resolve_characteristic(d['address'],d['service'],d['characteristic'])
                except Invalid:return
            if path not in self.notifications:return
        else:return
        if self.queue.full():self.queue.get_nowait()
        self.queue.put_nowait(event)

    async def send_events(self):
        while True:
            event=await self.queue.get()
            if event['type']=='device':
                batch=OrderedDict();batch[event['data']['Address']]=event['data']
                await asyncio.sleep(.1)
                rest=[]
                while not self.queue.empty() and len(batch)<32:
                    item=self.queue.get_nowait()
                    if item['type']=='device':batch[item['data']['Address']]=item['data']
                    else:rest.append(item)
                payload=b''
                for d in batch.values():
                    rssi=int(d.get('RSSI',-127));zigzag=(rssi<<1)^(rssi>>31)
                    item=message((1,int(d['Address'].replace(':',''),16)),(2,zigzag),
                        (3,1 if d.get('AddressType')=='random' else 0),(4,advertisement(d)))
                    payload+=field(1,item)
                if self.subscribed:await self.send(93,payload)
                for item in rest:await self.notification(item['data'])
            elif event['type']=='link':
                await self.send(69,message((1,int(event['data']['address'].replace(':',''),16)),(2,False)))
                await self.proxy.free()
            else:await self.notification(event['data'])

    async def notification(self,data):
        blue=self.proxy.gateway.bluez
        try:
            path=data.get('path') or blue.resolve_characteristic(data['address'],data['service'],data['characteristic'])
            h=handle(path,blue.objects[path][CHAR])
            await self.send(79,message((1,int(data['address'].replace(':',''),16)),(2,h),(3,bytes.fromhex(data['hex']))))
        except Invalid:pass

    def target(self,number):
        if not isinstance(number,int) or not 0<=number<(1<<48):raise Invalid('invalid_bluetooth_address')
        mac=':'.join(('%012X'%number)[i:i+2] for i in range(0,12,2))
        mac=address(mac);cfg=self.proxy.config
        if not cfg['active'] or mac not in cfg['devices']:raise Invalid('proxy_device_not_allowed')
        allowed=self.proxy.gateway.config['gateway']['devices']
        if allowed and mac not in allowed:raise Invalid('proxy_device_not_allowed')
        return mac

    async def operation(self,kind,fields):
        number=one(fields,1);h=one(fields,2) if kind not in (68,70) else 0
        try:
            mac=self.target(number)
            async with self.device_locks.setdefault(mac,asyncio.Lock()):
                async with self.proxy.gateway.lock:
                    if kind==68:await self.device_request(mac,number,one(fields,2));return
                    if self.proxy.owned.get(mac) is not self:raise Invalid('proxy_connection_not_owned')
                    if kind==70:await self.services(mac,number);return
                    blue=self.proxy.gateway.bluez;path,interface,props=self.resolve(mac,h,kind in (76,77))
                    if kind in (73,76):
                        raw=(await blue.call(path,interface,'ReadValue','a{sv}',[{}]))[0]
                        await self.send(74,message((1,number),(2,h),(3,bytes(raw))))
                    elif kind in (75,77):
                        raw=one(fields,4 if kind==75 else 3,b'')
                        if not isinstance(raw,bytes) or len(raw)>512:raise Invalid('invalid_proxy_packet')
                        options={}
                        if interface==CHAR:
                            from dbus_next import Variant
                            options={'type':Variant('s','request' if one(fields,3) else 'command')}
                        await blue.call(path,interface,'WriteValue','aya{sv}',[raw,options])
                        await self.send(83,message((1,number),(2,h)))
                    elif kind==78:
                        enabled=bool(one(fields,3))
                        if enabled and path in blue.notifying and path not in self.notifications:raise Invalid('proxy_notification_in_use')
                        if enabled and path not in self.notifications:
                            await blue.call(path,CHAR,'StartNotify');blue.notifying.add(path);self.notifications.add(path)
                        elif not enabled and path in self.notifications:
                            await blue.call(path,CHAR,'StopNotify');blue.notifying.discard(path);self.notifications.discard(path)
                        await self.send(84,message((1,number),(2,h)))
        except (Invalid,OSError,asyncio.TimeoutError):
            if kind==68:await self.send(69,message((1,number),(2,False),(4,0x3e)))
            else:await self.send(82,message((1,number),(2,h),(3,0x0e)))

    async def device_request(self,mac,number,request):
        blue=self.proxy.gateway.bluez
        if request in (0,4,5):
            path=blue.device_path(mac);owner=self.proxy.owned.get(mac)
            if owner is not None and owner is not self:raise Invalid('proxy_connection_in_use')
            if owner is None and blue.objects[path][DEVICE].get('Connected'):raise Invalid('proxy_connection_in_use')
            if owner is None and len(self.proxy.owned)>=self.proxy.config['slots']:raise Invalid('proxy_connections_full')
            self.proxy.owned[mac]=self
            try:
                await blue.device_action(mac,'connect');await blue.wait_services(mac)
            except BaseException:
                self.proxy.owned.pop(mac,None)
                with contextlib.suppress(Invalid,OSError,asyncio.TimeoutError):await blue.device_action(mac,'disconnect')
                await self.proxy.free();raise
            mtus=[p[CHAR]['MTU'] for path,p in blue.objects.items() if path.startswith(blue.device_path(mac)+'/') and CHAR in p and isinstance(p[CHAR].get('MTU'),int) and 23<=p[CHAR]['MTU']<=517]
            await self.send(69,message((1,number),(2,True),(3,min(mtus) if mtus else 23)))
            self.connections.add(mac)
            await self.proxy.free()
        elif request==1:
            if self.proxy.owned.get(mac) is self:
                await blue.device_action(mac,'disconnect');self.proxy.owned.pop(mac,None)
                self.connections.discard(mac)
                prefix=blue.device_path(mac)+'/'
                self.notifications={x for x in self.notifications if not x.startswith(prefix)}
            await self.send(69,message((1,number),(2,False)));await self.proxy.free()
        else:raise Invalid('proxy_request_not_supported')

    def resolve(self,mac,h,descriptor=False):
        blue=self.proxy.gateway.bluez;prefix=blue.device_path(mac)+'/'
        interface=DESC if descriptor else CHAR
        for path,interfaces in blue.objects.items():
            if path.startswith(prefix) and interface in interfaces and handle(path,interfaces[interface])==h:
                return path,interface,interfaces[interface]
        raise Invalid('bluetooth_gatt_handle_missing')

    async def services(self,mac,number):
        blue=self.proxy.gateway.bluez;prefix=blue.device_path(mac)+'/'
        for path,interfaces in list(blue.objects.items()):
            if not path.startswith(prefix) or SERVICE not in interfaces:continue
            p=interfaces[SERVICE];item=uuid_fields(p['UUID'])+field(2,handle(path,p))
            for char,ci in list(blue.objects.items()):
                if CHAR not in ci or ci[CHAR].get('Service')!=path:continue
                cp=ci[CHAR];bits=sum(PROPERTIES.get(f,0) for f in cp.get('Flags',[]))
                encoded=uuid_fields(cp['UUID'])+message((2,handle(char,cp)),(3,bits))
                for desc,di in list(blue.objects.items()):
                    if DESC in di and di[DESC].get('Characteristic')==char:
                        dp=di[DESC];encoded+=field(4,uuid_fields(dp['UUID'])+field(2,handle(desc,dp)))
                item+=field(3,encoded)
            await self.send(71,message((1,number),(2,item)))
        await self.send(72,field(1,number))

    async def close(self):
        if self.closed and self.writer.is_closing():return
        self.closed=True
        if self.sender:self.sender.cancel()
        if self.sender:await asyncio.gather(self.sender,return_exceptions=True)
        for task in self.tasks:task.cancel()
        await asyncio.gather(*list(self.tasks),return_exceptions=True)
        blue=self.proxy.gateway.bluez
        for mac,owner in list(self.proxy.owned.items()):
            if owner is self:
                with contextlib.suppress(Invalid,OSError,asyncio.TimeoutError):await blue.device_action(mac,'disconnect')
                self.proxy.owned.pop(mac,None)
        self.writer.close()
        with contextlib.suppress(OSError):await self.writer.wait_closed()
