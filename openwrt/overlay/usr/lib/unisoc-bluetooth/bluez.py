# SPDX-License-Identifier: MIT
"""One BlueZ D-Bus owner for discovery, pairing and notification sessions."""
import asyncio
import re
import secrets
import time

from dbus_next import Message, MessageType, Variant, DBusError
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.service import ServiceInterface, method

from core import Invalid, address, boolean, hex_bytes, plain, text, uuid

BLUEZ = 'org.bluez'
ADAPTER = BLUEZ + '.Adapter1'
DEVICE = BLUEZ + '.Device1'
SERVICE = BLUEZ + '.GattService1'
CHAR = BLUEZ + '.GattCharacteristic1'
PROPS = 'org.freedesktop.DBus.Properties'
MANAGER = 'org.freedesktop.DBus.ObjectManager'
AGENT = '/org/openwrt/UnisocBluetoothAgent'

class BluezError(Invalid):
    def __init__(self, name, message=''):
        self.name = name
        super().__init__(name + ': ' + message)

class PairAgent(ServiceInterface):
    def __init__(self, owner):
        super().__init__(BLUEZ + '.Agent1')
        self.owner = owner

    @method()
    def Release(self):
        self.owner.cancel_pairing()

    @method()
    def Cancel(self):
        self.owner.cancel_pairing()

    @method()
    async def RequestPinCode(self, device: 'o') -> 's':
        return await self.owner.prompt(device, 'pin')

    @method()
    async def RequestPasskey(self, device: 'o') -> 'u':
        return await self.owner.prompt(device, 'passkey')

    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'):
        self.owner.display(device, 'display_pin', pincode)

    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
        self.owner.display(device, 'display_passkey', f'{passkey:06d}', entered)

    @method()
    async def RequestConfirmation(self, device: 'o', passkey: 'u'):
        await self.owner.prompt(device, 'confirm', f'{passkey:06d}')

    @method()
    async def RequestAuthorization(self, device: 'o'):
        await self.owner.prompt(device, 'authorize')

    @method()
    async def AuthorizeService(self, device: 'o', service: 's'):
        await self.owner.prompt(device, 'service', service)

class Bluez:
    def __init__(self, emit):
        self.emit = emit
        self.bus = None
        self.objects = {}
        self.pending = {}
        self.discovery = set()
        self.notifying = set()
        self.adapter = ''
        self.agent_exported = False
        self.agent_registered = False
        self.scan_generation = 0
        self.state_changed = asyncio.Event()
        self.on_ready = None
        self.last_advertisement = {}

    async def call(self, path, interface, member, signature='', body=None):
        if self.bus is None:
            raise Invalid('bluetooth_bus_unavailable')
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination=BLUEZ, path=path, interface=interface, member=member,
            signature=signature, body=body or [])), 100 if member == 'Pair' else 15)
        if reply.message_type == MessageType.ERROR:
            raise BluezError(reply.error_name, str(reply.body[0]) if reply.body else '')
        return reply.body

    async def start(self):
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.bus.add_message_handler(self.signal)
        for rule in (
            "type='signal',sender='org.bluez'",
            "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'",
        ):
            response = await self.bus.call(Message(destination='org.freedesktop.DBus',
                path='/org/freedesktop/DBus', interface='org.freedesktop.DBus',
                member='AddMatch', signature='s', body=[rule]))
            if response.message_type == MessageType.ERROR:
                raise Invalid('bluetooth_signal_subscription_failed')
        self.bus.export(AGENT, PairAgent(self))
        self.agent_exported = True
        try:
            await self.refresh()
        except Invalid as error:
            self.emit('status', {'error': str(error)})

    async def refresh(self):
        self.objects = plain((await self.call('/', MANAGER, 'GetManagedObjects'))[0])
        if not self.agent_registered:
            await self.call('/org/bluez', BLUEZ + '.AgentManager1', 'RegisterAgent',
                            'os', [AGENT, 'KeyboardDisplay'])
            self.agent_registered = True
        # Register only for our own operations; do not steal another gateway's default agent.
        self.emit('status', {})
        if self.on_ready:
            await self.on_ready()

    def signal(self, message):
        if message.message_type != MessageType.SIGNAL:
            return
        changed = self.state_changed
        self.state_changed = asyncio.Event()
        changed.set()
        if (message.interface == 'org.freedesktop.DBus' and message.member == 'NameOwnerChanged'
                and message.body[0] == BLUEZ):
            self.objects.clear()
            self.last_advertisement.clear()
            self.agent_registered = False
            self.discovery.clear()
            self.notifying.clear()
            self.cancel_pairing()
            self.emit('status', {})
            if message.body[2]:
                asyncio.create_task(self._refresh_after_restart())
            return
        if message.interface == MANAGER:
            if message.member == 'InterfacesAdded':
                path, interfaces = message.body
                self.objects.setdefault(path, {}).update(plain(interfaces))
                self._changed(path, set(plain(interfaces).get(DEVICE, {})))
            elif message.member == 'InterfacesRemoved':
                path, interfaces = message.body
                if CHAR in interfaces:
                    self.notifying.discard(path)
                if DEVICE in interfaces:
                    self.last_advertisement.pop(path, None)
                for interface in interfaces:
                    self.objects.get(path, {}).pop(interface, None)
                if not self.objects.get(path):
                    self.objects.pop(path, None)
            self.emit('status', {})
        elif message.interface == PROPS and message.member == 'PropertiesChanged':
            interface, changed, invalidated = message.body
            props = self.objects.setdefault(message.path, {}).setdefault(interface, {})
            props.update(plain(changed))
            for key in invalidated:
                props.pop(key, None)
            if interface == DEVICE:
                if 'Connected' in changed and not plain(changed['Connected']):
                    self.notifying.difference_update(p for p in list(self.notifying) if p.startswith(message.path + '/'))
                self._changed(message.path, set(changed))
            elif interface == ADAPTER and 'Powered' in changed and not plain(changed['Powered']):
                self.discovery.discard(message.path)
            elif interface == CHAR and 'Value' in changed:
                owner = self.characteristic_owner(message.path)
                if owner:
                    self.emit('gatt', dict(owner, hex=props.get('Value', '')))
            self.emit('status', {})

    async def _refresh_after_restart(self):
        try:
            await self.refresh()
        except (Invalid, OSError) as error:
            self.emit('status', {'error': str(error)})

    def _changed(self, path, keys):
        props = self.objects.get(path, {}).get(DEVICE)
        if not props or not self.in_scope(path):
            return
        advertising = {'RSSI', 'ManufacturerData', 'ServiceData', 'AdvertisingData', 'AdvertisingFlags', 'TxPower'}
        if keys & advertising:
            self.last_advertisement[path] = time.time()
        if keys & (advertising | {'Connected', 'ServicesResolved'}):
            snapshot=self.device_snapshot(path);snapshot['changed']=sorted(keys)
            self.emit('device', snapshot)
        if 'ServicesResolved' in keys and props.get('ServicesResolved'):
            self.emit('resolved', {'address': props.get('Address', '')})

    def in_scope(self, path):
        return bool(self.adapter and path.startswith('/org/bluez/' + self.adapter + '/'))

    def select(self, name):
        if not name:
            adapters = [p.rsplit('/', 1)[1] for p, v in self.objects.items() if ADAPTER in v]
            name = sorted(adapters)[0] if adapters else ''
        if name and not re.fullmatch(r'hci[0-9]{1,3}', name):
            raise Invalid('invalid_adapter')
        self.adapter = name

    def adapter_path(self):
        path = '/org/bluez/' + self.adapter
        if not self.adapter or ADAPTER not in self.objects.get(path, {}):
            raise Invalid('no_bluetooth_adapter')
        return path

    def device_path(self, mac):
        mac = address(mac)
        path = self.adapter_path() + '/dev_' + mac.replace(':', '_')
        if DEVICE not in self.objects.get(path, {}):
            raise Invalid('device_not_found_scan_first')
        return path

    def device_snapshot(self, path):
        props = self.objects.get(path, {}).get(DEVICE, {})
        names = ('Address', 'AddressType', 'Name', 'Alias', 'RSSI', 'Paired', 'Trusted',
                 'Connected', 'Blocked', 'ServicesResolved', 'UUIDs', 'ManufacturerData', 'ServiceData',
                 'TxPower', 'Appearance', 'AdvertisingFlags', 'AdvertisingData')
        result = {k: props[k] for k in names if k in props}
        if path in self.last_advertisement:
            result['LastAdvertisement'] = self.last_advertisement[path]
        return result

    def snapshot(self):
        adapters = []
        for path, interfaces in self.objects.items():
            if ADAPTER in interfaces:
                props = interfaces[ADAPTER]
                adapters.append(dict(id=path.rsplit('/', 1)[1], **{
                    key: props[key] for key in ('Address', 'Alias', 'Powered', 'Discoverable',
                        'Pairable', 'Discovering', 'UUIDs', 'Roles') if key in props}))
        return {'adapters': adapters, 'adapter': self.adapter,
                'devices': [self.device_snapshot(p) for p, v in self.objects.items()
                            if DEVICE in v and self.in_scope(p)],
                'pairing': [dict(id=k, **v['display']) for k, v in self.pending.items()]}

    async def set_property(self, path, interface, key, signature, value):
        await self.call(path, PROPS, 'Set', 'ssv', [interface, key, Variant(signature, value)])

    async def power(self, enabled, timeout=10):
        enabled = boolean(enabled)
        path = self.adapter_path()
        if self.objects[path][ADAPTER].get('Powered') == enabled:
            return
        try:
            await self.set_property(path, ADAPTER, 'Powered', 'b', enabled)
        except BluezError as error:
            # Releasing HCI rfkill may already have started this same transition.
            # Busy alone is not success: require the actual Powered signal below.
            if error.name not in (BLUEZ + '.Error.Busy', BLUEZ + '.Error.InProgress'):
                raise
        deadline = asyncio.get_running_loop().time() + timeout
        while self.objects.get(path, {}).get(ADAPTER, {}).get('Powered') != enabled:
            if ADAPTER not in self.objects.get(path, {}):
                raise Invalid('bluetooth_adapter_removed')
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise Invalid('bluetooth_power_transition_timed_out')
            try:
                await asyncio.wait_for(self.state_changed.wait(), remaining)
            except asyncio.TimeoutError:
                raise Invalid('bluetooth_power_transition_timed_out')

    async def scan(self, transport, seconds=30):
        if transport not in ('le', 'bredr', 'auto'):
            raise Invalid('invalid_transport')
        path = self.adapter_path()
        # Stop only the session owned by this D-Bus connection, never another app's scan.
        await self.stop_scan()
        await self.call(path, ADAPTER, 'SetDiscoveryFilter', 'a{sv}',
                        [{'Transport': Variant('s', transport), 'DuplicateData': Variant('b', False)}])
        await self.call(path, ADAPTER, 'StartDiscovery')
        self.discovery.add(path)
        if seconds:
            asyncio.create_task(self._stop_scan_later(path, seconds, self.scan_generation))

    async def _stop_scan_later(self, path, seconds, generation):
        await asyncio.sleep(seconds)
        if path in self.discovery and generation == self.scan_generation:
            try:
                await self.stop_scan()
            except (Invalid, OSError, asyncio.TimeoutError):
                self.emit('status', {'error': 'stop_discovery_failed'})

    async def stop_scan(self):
        self.scan_generation += 1
        for path in list(self.discovery):
            try:
                await self.call(path, ADAPTER, 'StopDiscovery')
            finally:
                self.discovery.discard(path)

    async def device_action(self, mac, action, value=None):
        path = self.device_path(mac)
        if action == 'pair':
            try:
                await self.call(path, DEVICE, 'Pair')
            finally:
                self.cancel_pairing()
        elif action in ('connect', 'disconnect'):
            await self.call(path, DEVICE, 'Connect' if action == 'connect' else 'Disconnect')
        elif action == 'forget':
            await self.call(self.adapter_path(), ADAPTER, 'RemoveDevice', 'o', [path])
        elif action == 'trust':
            if type(value) is not bool:
                raise Invalid('invalid_boolean')
            await self.set_property(path, DEVICE, 'Trusted', 'b', value)
        else:
            raise Invalid('invalid_device_action')

    async def wait_services(self, mac, timeout=20):
        path=self.device_path(mac);deadline=asyncio.get_running_loop().time()+timeout
        while not self.objects.get(path,{}).get(DEVICE,{}).get('ServicesResolved'):
            if not self.objects.get(path,{}).get(DEVICE,{}).get('Connected'):raise Invalid('device_disconnected')
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:raise Invalid('bluetooth_services_timed_out')
            await asyncio.wait_for(self.state_changed.wait(),remaining)

    def characteristic_owner(self, path):
        props = self.objects.get(path, {}).get(CHAR)
        if not props:
            return None
        service = self.objects.get(props.get('Service'), {}).get(SERVICE, {})
        device = self.objects.get(service.get('Device'), {}).get(DEVICE, {})
        if not self.in_scope(service.get('Device', '')):
            return None
        return {'address': device.get('Address', ''), 'path':path,'service': service.get('UUID', ''),
                'characteristic': props.get('UUID', '')}

    def characteristics(self, mac):
        wanted = address(mac)
        self.device_path(wanted)
        result = []
        for path, interfaces in self.objects.items():
            owner = self.characteristic_owner(path)
            if owner and owner['address'] == wanted:
                props = interfaces[CHAR]
                result.append(dict(owner, path=path, flags=props.get('Flags', []),
                                   value=props.get('Value', ''), notifying=props.get('Notifying', False)))
        return result

    def resolve_characteristic(self, mac, service_id, characteristic_id):
        service_id, characteristic_id = uuid(service_id), uuid(characteristic_id)
        matches = [x['path'] for x in self.characteristics(mac)
                   if x['service'] == service_id and x['characteristic'] == characteristic_id]
        if len(matches) != 1:
            raise Invalid('characteristic_not_unique_or_not_found')
        return matches[0]

    async def read(self, mac, service_id, characteristic_id):
        path = self.resolve_characteristic(mac, service_id, characteristic_id)
        value = (await self.call(path, CHAR, 'ReadValue', 'a{sv}', [{}]))[0]
        return {'hex': bytes(value).hex()}

    async def write(self, mac, service_id, characteristic_id, value):
        path = self.resolve_characteristic(mac, service_id, characteristic_id)
        flags = self.objects[path][CHAR].get('Flags', [])
        if not set(flags) & {'write', 'write-without-response'}:
            raise Invalid('characteristic_not_writable')
        mode = 'request' if 'write' in flags else 'command'
        await self.call(path, CHAR, 'WriteValue', 'aya{sv}',
                        [hex_bytes(value), {'type': Variant('s', mode)}])
        return {}

    async def notify(self, mac, service_id, characteristic_id, enabled):
        path = self.resolve_characteristic(mac, service_id, characteristic_id)
        if enabled:
            if path not in self.notifying:
                await self.call(path, CHAR, 'StartNotify')
                self.notifying.add(path)
        elif path in self.notifying:
            await self.call(path, CHAR, 'StopNotify')
            self.notifying.discard(path)
        return {}

    def display(self, path, kind, value, entered=0):
        for key, pending in list(self.pending.items()):
            if pending['future'] is None:
                self.pending.pop(key)
        token = secrets.token_hex(12)
        self.pending[token] = {'display': {'device': path.rsplit('/', 1)[-1].replace('dev_', '').replace('_', ':'),
                                'kind': kind, 'value': value, 'entered': entered}, 'future': None}
        self.emit('pairing', {})

    async def prompt(self, path, kind, value=''):
        if not self.in_scope(path):
            raise DBusError(BLUEZ + '.Error.Rejected', 'Adapter not selected')
        token = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        self.pending[token] = {'display': {'device': path.rsplit('/', 1)[-1].replace('dev_', '').replace('_', ':'),
                                         'kind': kind, 'value': value}, 'future': future}
        self.emit('pairing', {})
        try:
            return await asyncio.wait_for(future, 80)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            raise DBusError(BLUEZ + '.Error.Canceled', 'Pairing canceled') from error
        finally:
            self.pending.pop(token, None)
            self.emit('pairing', {})

    def reply(self, token, accept, value=''):
        boolean(accept)
        request = self.pending.get(text(token, 32))
        if not request or request['future'] is None or request['future'].done():
            raise Invalid('pairing_request_expired')
        future, kind = request['future'], request['display']['kind']
        if not accept:
            future.set_exception(DBusError(BLUEZ + '.Error.Rejected', 'Rejected by user'))
        elif kind == 'pin':
            value = text(value, 16)
            if not value:
                raise Invalid('pin_required')
            future.set_result(value)
        elif kind == 'passkey':
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,6}', value):
                raise Invalid('invalid_passkey')
            future.set_result(int(value))
        else:
            future.set_result(None)
        return {}

    def cancel_pairing(self):
        for request in self.pending.values():
            future = request['future']
            if future and not future.done():
                future.cancel()
        self.pending.clear()
        self.emit('pairing', {})
