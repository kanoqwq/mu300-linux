# SPDX-License-Identifier: MIT
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import signal
import time
import base64
import copy

from bluez import ADAPTER, BLUEZ, Bluez
from core import Invalid, atomic_json, boolean, integer, load_config, public_config, validate
from forward import Forwarder
from serial_bridge import SerialBridge
from transport import unblock_adapter
from catalog import Catalog
from recovery import Recovery
from commands import execute as execute_command
from decoder import Decoder
from bthome import SERVICE as BTHOME
from advertising import Advertising, validate_profiles

CONFIG = '/etc/unisoc-bluetooth/config.json'
SOCKET = '/run/unisoc-bluetooth/control.sock'

class Gateway:
    def __init__(self, config_path=CONFIG):
        self.config_path = config_path
        self.config = load_config(config_path)
        self.events = deque(maxlen=256)
        self.sequence = 0
        self.changed = asyncio.Event()
        self.bluez = Bluez(self.emit)
        self.bluez.on_ready = self.ready
        self.forward = Forwarder(self.execute_command,Path(config_path).parent/'discovery.json')
        self.serial = SerialBridge(self.emit)
        self.jobs = {}
        self.lock = asyncio.Lock()
        self.error = ''
        self.event_id = os.urandom(8).hex()
        self.catalog=Catalog(self.emit)
        self.catalog.configure(self.config['gateway']['known_devices'])
        self.forward.snapshot=self.catalog.snapshot
        self.recovery=Recovery(self.bluez,self.restore_subscriptions,self.emit)
        self.scan_task=None
        self.proxy=None
        self.decoder=Decoder(self.catalog.decoded,self.emit)
        self.starting = True
        self.desired_on = self.config['autostart']
        self.activation = 'starting' if self.desired_on else 'off'
        self.advertising = Advertising(self.bluez, Path(config_path).parent/'advertisements.json', self.emit)

    def emit(self, kind, data):
        if kind == 'status' and hasattr(self, 'advertising'):
            self.advertising.reconcile()
        self.sequence += 1
        event = {'seq': self.sequence, 'time': time.time(), 'type': kind, 'data': data}
        self.events.append(event)
        self.changed.set()
        self.changed = asyncio.Event()
        if kind in ('device', 'gatt', 'serial','measurement','availability'):
            self.forward.push(event)
        if kind=='gatt':
            path='/org/bluez/'+self.bluez.adapter+'/dev_'+data.get('address','').replace(':','_')
            connected=self.bluez.objects.get(path,{}).get(BLUEZ+'.Device1',{}).get('Connected',False)
            self.catalog.gatt(data,self.config['gateway']['subscriptions'],connected)
        if kind=='device':
            parsed=self.catalog.observe(data)
            if parsed:self.emit('measurement',parsed)
            mode=self.catalog.known.get(data.get('Address'),{}).get('decoder','auto')
            fresh='changed' not in data or bool(set(data['changed'])&{'ServiceData','ManufacturerData'})
            if fresh and mode in ('auto','theengs') and BTHOME not in data.get('ServiceData',{}) and (data.get('ServiceData') or data.get('ManufacturerData')):
                self.decoder.push(data)
            if not self.proxy or data.get('Address') not in self.proxy.owned:self.recovery.changed(data.get('Address'))
        if self.proxy:self.proxy.event(event)
        if kind in ('resolved', 'device') and self.config['gateway']['enabled']:
            mac = data.get('address', data.get('Address'))
            if any(s['device'] == mac for s in self.config['gateway']['subscriptions']):
                self.subscribe(mac)

    def status(self):
        snapshot = self.bluez.snapshot()
        selected = next((a for a in snapshot['adapters'] if a['id'] == snapshot['adapter']), None)
        state = self.activation
        if state != 'starting':
            if selected and selected.get('Powered'):
                state = 'on'
            elif state != 'failed':
                state = 'unavailable' if self.desired_on and not selected else 'off'
        return dict(snapshot, ok=True, power_state=state, sequence=self.sequence, event_id=self.event_id,
                    config=public_config(self.config), forwarding=dict(self.forward.state),
                    serial=self.serial.snapshot(), jobs=list(self.jobs.values()), error=self.error,
                    catalog=self.catalog.snapshot(),proxy=self.proxy.snapshot() if self.proxy else {'enabled':False},
                    advertising=self.advertising.snapshot())

    async def transport(self):
        adapters = self.bluez.snapshot()['adapters']
        if adapters and (not self.config['adapter'] or any(a['id'] == self.config['adapter'] for a in adapters)):
            self.bluez.select(self.config['adapter'])
            return
        # This helper has an explicit platform profile; it never probes and falls back to MU300.
        process = await asyncio.create_subprocess_exec('/etc/init.d/unisoc-bt-transport', 'start',
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        try:
            code = await asyncio.wait_for(process.wait(), 60)
        except asyncio.TimeoutError:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise Invalid('bluetooth_transport_start_timed_out')
        if code:
            raise Invalid('bluetooth_transport_start_failed')
        # HCI creation is asynchronous. Only wait while fulfilling this explicit power request.
        if not self.bluez.snapshot()['adapters']:
            pending = self.changed
            for _ in range(20):
                try:
                    await asyncio.wait_for(pending.wait(), .25)
                except asyncio.TimeoutError:
                    pass
                if self.bluez.snapshot()['adapters']:
                    break
                pending = self.changed
        self.bluez.select(self.config['adapter'])

    async def ready(self):
        self.bluez.select(self.config['adapter'])
        if self.starting:
            return
        if self.bluez.snapshot()['adapters']:
            try:
                if self.desired_on:
                    await self.start_configured()
                elif self.config['alias']:
                    await self.apply_alias()
            except (Invalid, OSError, asyncio.TimeoutError):
                self.error = 'bluetooth_activation_failed'
                self.emit('status', {'error': self.error})

    def powered(self):
        path = '/org/bluez/' + self.bluez.adapter
        return bool(self.bluez.objects.get(path, {}).get(ADAPTER, {}).get('Powered'))

    async def apply_alias(self):
        # Also retry a previously saved name if an earlier D-Bus application failed.
        if self.bluez.snapshot()['adapters']:
            await self.bluez.set_property(self.bluez.adapter_path(), ADAPTER, 'Alias', 's', self.config['alias'])

    async def activate(self):
        # The newly registered HCI rfkill can still inherit a blocked type policy
        # even after the separate board transport rfkill was enabled.
        unblock_adapter(self.bluez.adapter)
        await self.bluez.power(True)
        if self.config['alias']:
            await self.apply_alias()
        await self.forward.configure(self.config['gateway'])
        await self.recovery.configure(self.config['gateway'])
        if self.config['gateway']['enabled']:
            await self.start_scan_policy()
            for sub in self.config['gateway']['subscriptions']:
                self.subscribe(sub['device'])
            for d in self.bluez.snapshot()['devices']:self.recovery.changed(d.get('Address'))
        self.error = ''
        await self.configure_proxy()

    def subscribe(self, mac):
        self.recovery.changed(mac)

    async def restore_subscriptions(self, mac):
        subs=[s for s in self.config['gateway']['subscriptions'] if s['device']==mac]
        if not subs:return
        path=self.bluez.device_path(mac)
        if not self.bluez.objects[path][BLUEZ+'.Device1'].get('Connected'):
            await self.bluez.device_action(mac,'connect')
        await self.bluez.wait_services(mac)
        for sub in subs:
            char=self.bluez.resolve_characteristic(mac,sub['service'],sub['characteristic'])
            flags=self.bluez.objects[char]['org.bluez.GattCharacteristic1'].get('Flags',[])
            if 'notify' in flags or 'indicate' in flags:
                await self.bluez.notify(mac,sub['service'],sub['characteristic'],True)
            elif 'read' not in flags:raise Invalid('characteristic_not_readable_or_notifiable')
            if 'read' in flags:
                value=await self.bluez.read(mac,sub['service'],sub['characteristic'])
                self.emit('gatt',dict(address=mac,service=sub['service'],characteristic=sub['characteristic'],**value))

    async def start_scan_policy(self):
        await self.stop_scan_policy()
        cfg=self.config['gateway'];policy=cfg['policy']
        await self.bluez.scan(cfg['transport'],0)
        if policy['pause_seconds']:
            async def cycle():
                while True:
                    await asyncio.sleep(policy['scan_seconds'])
                    await self.bluez.stop_scan()
                    await asyncio.sleep(policy['pause_seconds'])
                    await self.bluez.scan(cfg['transport'],0)
            self.scan_task=asyncio.create_task(cycle())

    async def stop_scan_policy(self):
        if self.scan_task:
            self.scan_task.cancel();await asyncio.gather(self.scan_task,return_exceptions=True);self.scan_task=None
        await self.bluez.stop_scan()

    async def execute_command(self, command, value):
        async with self.lock:
            if any(j['state']=='running' for j in self.jobs.values()):raise Invalid('bluetooth_operation_in_progress')
            if command['kind']=='disconnect':
                self.recovery.suspend(command['device']);await self.serial.suspend(command['device'])
            elif command['kind']=='connect':self.recovery.suppressed.discard(command['device'])
            result=await execute_command(self.bluez,self.serial,command,value)
            self.emit('command',result)
            return result

    async def reconfigure(self, candidate, replace=False):
        if any(j['state'] == 'running' for j in self.jobs.values()):
            raise Invalid('bluetooth_operation_in_progress')
        new = validate(candidate, None if replace else self.config)
        if new['adapter'] != self.config['adapter']:
            await self.advertising.close()
        # Validate and persist before changing runtime. Preserve valid settings if activation fails.
        atomic_json(self.config_path, new)
        old = self.config
        self.config = new
        self.error = ''
        self.bluez.select(new['adapter'])
        await self.apply_alias()
        # Basic settings are independent of unfinished gateway forms. Changing
        # the startup preference must not power on a currently disabled radio.
        if new['gateway'] == old['gateway'] and new['adapter'] == old['adapter']:
            self.emit('config', {})
            return self.status()
        await self.stop_scan_policy()
        await self.recovery.close()
        self.catalog.configure(new['gateway']['known_devices'])
        for path in list(self.bluez.notifying):
            await self.bluez.call(path, BLUEZ + '.GattCharacteristic1', 'StopNotify')
            self.bluez.notifying.discard(path)
        await self.forward.configure(dict(new['gateway'], enabled=False))
        if not new['gateway']['proxy']['enabled'] and self.proxy:await self.proxy.close()
        await self.serial.close()
        if self.powered():
            self.job(self.start_configured())
        self.emit('config', {})
        return self.status()

    async def start_configured(self):
        self.activation = 'starting'
        self.emit('status', {})
        try:
            await self.transport()
            await self.activate()
            if self.config['gateway']['enabled']:
                await self.serial.configure(self.config['gateway']['serial'])
        except Exception:
            self.activation = 'failed'
            raise
        else:
            self.activation = 'on'
        finally:
            self.emit('status', {})

    async def configure_proxy(self):
        if not self.config['gateway']['proxy']['enabled']:
            if self.proxy:await self.proxy.close()
            return
        from proxy import Proxy
        if self.proxy is None:self.proxy=Proxy(self)
        try:await self.proxy.configure(self.config['gateway']['proxy'])
        except (Invalid,OSError,ImportError) as error:
            await self.proxy.close();self.proxy.error='bluetooth_proxy_start_failed'
            self.emit('proxy_error',{'error':'bluetooth_proxy_start_failed'})
            raise Invalid('bluetooth_proxy_start_failed') from error

    def templates(self):
        builtins=json.loads(Path('/usr/share/unisoc-bluetooth/templates.json').read_text())
        try:custom=json.loads((Path(self.config_path).parent/'templates.json').read_text())
        except FileNotFoundError:custom=[]
        return builtins+custom

    @staticmethod
    def validate_templates(record):
        import re
        from core import text
        if not isinstance(record,dict) or set(record)!={'format','kind','templates'} or record['format']!=1 or record['kind']!='unisoc-bluetooth-templates':
            raise Invalid('invalid_bluetooth_template')
        rows=record['templates']
        if not isinstance(rows,list) or len(rows)>32:raise Invalid('invalid_bluetooth_template')
        output=[];ids=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)-{'id','name','description','decoder','subscriptions','commands'}:raise Invalid('invalid_bluetooth_template')
            key=text(row.get('id',''),48)
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}',key) or key in ids or key in ('bthome-v2','gatt-battery','raw-bridge'):
                raise Invalid('invalid_bluetooth_template')
            ids.add(key)
            item={'id':key,'name':text(row.get('name',key),64),'description':text(row.get('description',''),512),'decoder':row.get('decoder','auto')}
            sample='02:00:00:00:00:01'
            checked=validate({'gateway':{'known_devices':[{'device':sample,'decoder':item['decoder']}],
                'subscriptions':[dict(s,device=sample) for s in row.get('subscriptions',[])],
                'commands':[dict(c,device=sample) for c in row.get('commands',[])]}})['gateway']
            for section in ('subscriptions','commands'):
                item[section]=[{k:v for k,v in d.items() if k!='device'} for d in checked[section]]
            output.append(item)
        return output

    async def apply_template(self, request):
        from core import address
        mac=address(request.get('device'))
        template=next((t for t in self.templates() if t['id']==request.get('id')),None)
        if template is None:raise Invalid('invalid_bluetooth_template')
        candidate=copy.deepcopy(self.config);g=candidate['gateway']
        known=next((d for d in g['known_devices'] if d['device']==mac),None)
        if known is None:
            known={'device':mac};g['known_devices'].append(known)
        known['template']=template['id'];known['decoder']=template.get('decoder','auto')
        for sub in template.get('subscriptions',[]):
            sub=dict(sub,device=mac)
            if not any((d['device'],d['service'],d['characteristic'])==(mac,sub['service'],sub['characteristic']) for d in g['subscriptions']):
                g['subscriptions'].append(sub)
        for command in template.get('commands',[]):
            command=dict(command,device=mac,id=command['id'][:30]+'_'+mac.replace(':','').lower())
            if not any(c['id']==command['id'] for c in g['commands']):g['commands'].append(command)
        return await self.reconfigure(candidate)

    def job(self, operation):
        if any(j['state'] == 'running' for j in self.jobs.values()):
            operation.close()
            raise Invalid('bluetooth_operation_in_progress')
        token = str(self.sequence + 1)
        self.jobs = {token: {'id': token, 'state': 'running'}}
        async def run():
            try:
                await operation
                self.jobs[token]['state'] = 'done'
            except Exception as error:
                self.jobs[token].update(state='failed', error=str(error)[:256])
            finally:
                self.emit('job', self.jobs[token])
        asyncio.create_task(run())
        return {'ok': True, 'job': token}

    async def dispatch(self, request):
        action = request.get('action')
        if action == 'status':
            return self.status()
        if action == 'advertising_list':
            return {'ok': True, 'advertising': self.advertising.snapshot()}
        if action == 'events':
            after = integer(request.get('after', 0), 0, 2**53)
            wait = integer(request.get('wait', 0), 0, 25)
            if request.get('event_id') != self.event_id:
                after = 0
            if after >= self.sequence and wait:
                try:
                    await asyncio.wait_for(self.changed.wait(), wait)
                except asyncio.TimeoutError:
                    pass
            return {'ok': True, 'sequence': self.sequence, 'event_id': self.event_id,
                    'events': [e for e in self.events if e['seq'] > after][-40:]}
        if action == 'pair_reply':
            self.bluez.reply(request.get('id'), request.get('accept'), request.get('value', ''))
            return {'ok': True}
        if action=='backup':
            try:templates=json.loads((Path(self.config_path).parent/'templates.json').read_text())
            except FileNotFoundError:templates=[]
            return {'ok':True,'backup':{'format':1,'kind':'unisoc-bluetooth-settings','config':copy.deepcopy(self.config),
                                      'templates':templates,'advertisements':copy.deepcopy(self.advertising.profiles)}}
        if action=='proxy_key':
            return {'ok':True,'key':base64.b64encode(os.urandom(32)).decode()}
        if action=='templates':
            return {'ok':True,'templates':self.templates()}
        if action=='command':
            key=request.get('id');command=next((c for c in self.config['gateway']['commands'] if c['id']==key),None)
            if command is None:raise Invalid('invalid_command_id')
            return dict(await self.execute_command(command,request.get('value')),ok=True)
        async with self.lock:
            if action == 'advertising_save':
                key = self.advertising.save(request.get('profile'))
                return {'ok': True, 'id': key, 'advertising': self.advertising.snapshot()}
            if action == 'advertising_delete':
                self.advertising.delete(request.get('id'))
                return {'ok': True, 'advertising': self.advertising.snapshot()}
            if action == 'advertising_start':
                await self.advertising.start(request.get('id'))
                return {'ok': True, 'advertising': self.advertising.snapshot()}
            if action == 'advertising_stop':
                if 'id' in request:
                    await self.advertising.stop(request['id'])
                else:
                    await self.advertising.close()
                return {'ok': True, 'advertising': self.advertising.snapshot()}
            if action=='import_templates':
                rows=self.validate_templates(request.get('templates'))
                existing={r['id']:r for r in self.templates() if r['id'] not in ('bthome-v2','gatt-battery','raw-bridge')}
                existing.update({r['id']:r for r in rows})
                if len(existing)>32:raise Invalid('invalid_bluetooth_template')
                atomic_json(Path(self.config_path).parent/'templates.json',list(existing.values()))
                return {'ok':True,'templates':self.templates()}
            if action=='restore':
                record=request.get('backup')
                if not isinstance(record,dict) or set(record)-{'format','kind','config','templates','advertisements'} or record.get('format')!=1 or record.get('kind')!='unisoc-bluetooth-settings' or 'config' not in record:
                    raise Invalid('invalid_bluetooth_backup')
                rows=self.validate_templates({'format':1,'kind':'unisoc-bluetooth-templates','templates':record.get('templates',[])})
                adverts=validate_profiles(record.get('advertisements',[]))
                # Validate the entire import before stopping current sessions.
                validate(record['config'])
                await self.advertising.close()
                await self.reconfigure(record['config'],replace=True)
                atomic_json(Path(self.config_path).parent/'templates.json',rows)
                self.advertising.replace(adverts)
                return self.status()
            if action=='apply_template':
                return await self.apply_template(request)
            if action == 'save':
                return await self.reconfigure(request.get('config'))
            if action == 'power':
                enabled = boolean(request.get('enabled'))
                if enabled:
                    return self.job(self.power_on())
                if not enabled:
                    await self.advertising.close()
                    self.desired_on = False
                    self.activation = 'off'
                    await self.stop_scan_policy()
                    await self.recovery.close()
                    await self.serial.close()
                    if self.proxy:await self.proxy.close()
                    await self.forward.configure(dict(self.config['gateway'], enabled=False))
                await self.bluez.power(enabled)
            elif action == 'visibility':
                enabled = boolean(request.get('enabled'))
                path = self.bluez.adapter_path()
                if enabled:
                    await self.bluez.call('/org/bluez', BLUEZ + '.AgentManager1', 'RequestDefaultAgent', 'o', ['/org/openwrt/UnisocBluetoothAgent'])
                # BlueZ owns the discoverability timer; no background shell sleep or permanent discoverability.
                await self.bluez.set_property(path, ADAPTER, 'DiscoverableTimeout', 'u', 120)
                await self.bluez.set_property(path, ADAPTER, 'Pairable', 'b', enabled)
                await self.bluez.set_property(path, ADAPTER, 'Discoverable', 'b', enabled)
            elif action == 'scan':
                if self.config['gateway']['enabled']:
                    raise Invalid('gateway_owns_discovery_change_gateway_settings')
                if boolean(request.get('enabled')):
                    await self.bluez.scan(request.get('transport', 'auto'), 30)
                else:
                    await self.bluez.stop_scan()
            elif action in ('pair', 'connect'):
                if action=='connect':self.recovery.suppressed.discard(request.get('device'))
                return self.job(self.bluez.device_action(request.get('device'), action))
            elif action in ('disconnect', 'forget', 'trust'):
                if action in ('disconnect','forget'):
                    self.recovery.suspend(request.get('device'))
                    await self.serial.suspend(request.get('device'))
                await self.bluez.device_action(request.get('device'), action, request.get('enabled'))
            elif action == 'gatt_list':
                return {'ok': True, 'characteristics': self.bluez.characteristics(request.get('device'))}
            elif action in ('read', 'write', 'notify'):
                args = [request.get(k) for k in ('device', 'service', 'characteristic')]
                if action != 'read':
                    args.append(boolean(request.get('enabled')) if action == 'notify' else request.get('hex'))
                return dict(await getattr(self.bluez, action)(*args), ok=True)
            elif action == 'serial_write':
                return dict(await self.serial.write(request.get('device'), request.get('hex')), ok=True)
            elif action == 'spp_services':
                return {'ok': True, 'services': await self.serial.services(request.get('device'))}
            else:
                raise Invalid('unknown_action')
        return self.status()

    async def power_on(self):
        await self.start_configured()
        self.desired_on = True

    async def client(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            request = json.loads(line)
            if not isinstance(request, dict):
                raise Invalid('invalid_request')
            result = await self.dispatch(request)
        except (Invalid, ValueError, TypeError, KeyError) as error:
            result = {'ok': False, 'error': str(error)[:256]}
        except (OSError, asyncio.TimeoutError):
            result = {'ok': False, 'error': 'bluetooth_operation_failed_or_timed_out'}
        try:
            writer.write(json.dumps(result).encode() + b'\n')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def initialize(self):
        await self.bluez.start()
        self.starting = False
        # BlueZ keeps manual power across a gateway-only restart. At boot its
        # AutoEnable=false policy leaves the radio off unless explicitly opted in.
        self.desired_on = self.config['autostart'] or self.powered()
        if self.desired_on:
            try:
                await self.start_configured()
            except (Invalid, OSError, asyncio.TimeoutError):
                self.error = 'bluetooth_activation_failed'
        elif self.config['alias']:
            try:
                await self.apply_alias()
            except Invalid:
                self.error = 'bluetooth_alias_apply_failed'

    async def run(self):
        Path(SOCKET).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        Path(SOCKET).unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.client, SOCKET, limit=262144)
        os.chmod(SOCKET, 0o600)
        await self.initialize()
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(name, done.set)
        try:
            await done.wait()
        finally:
            server.close()
            await server.wait_closed()
            await self.serial.close()
            await self.advertising.close()
            await self.stop_scan_policy()
            await self.recovery.close()
            self.catalog.close()
            await self.decoder.close()
            if self.proxy:await self.proxy.close()
            await self.forward.close()
            if self.bluez.bus:
                self.bluez.bus.disconnect()
            Path(SOCKET).unlink(missing_ok=True)

if __name__ == '__main__':
    asyncio.run(Gateway().run())
