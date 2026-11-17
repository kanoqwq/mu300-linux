# SPDX-License-Identifier: MIT
"""Explicit, non-connectable BLE test advertisements on the existing BlueZ bus.

Definitions survive updates; active sessions never survive daemon/radio restarts.
BlueZ owns packet scheduling and controller resources. No address spoofing or HCI
writer is introduced here. API: BlueZ 5.83 org.bluez.LEAdvertisement(5).
"""
import asyncio
import json
from pathlib import Path
import re
import secrets
import time

from dbus_next import Variant
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method

from bluez import ADAPTER, BLUEZ, BluezError
from core import Invalid, atomic_json, hex_bytes, integer, text, uuid

MANAGER = BLUEZ + '.LEAdvertisingManager1'
INTERFACE = BLUEZ + '.LEAdvertisement1'


def uuid_size(value):
    if value.endswith('-0000-1000-8000-00805f9b34fb'):
        return 2 if value.startswith('0000') else 4
    return 16


def validate_profile(record):
    fields = {'id', 'label', 'name', 'uuids', 'manufacturer_data', 'service_data', 'interval_ms', 'timeout'}
    if not isinstance(record, dict) or set(record) - fields:
        raise Invalid('invalid_advertising_profile')
    key = text(record.get('id', ''), 32)
    if not re.fullmatch('[a-zA-Z0-9_]{1,32}', key):
        raise Invalid('invalid_advertising_id')
    label = text(record.get('label', ''), 64).strip()
    name = text(record.get('name', ''), 29)
    if not label or len(name.encode('utf-8')) > 29:
        raise Invalid('invalid_advertising_name')
    services = record.get('uuids', [])
    if not isinstance(services, list) or len(services) > 8:
        raise Invalid('invalid_advertising_data')
    try:
        interval = integer(record.get('interval_ms', 1000), 20, 10240)
    except Invalid as error:
        raise Invalid('invalid_advertising_interval') from error
    try:
        timeout = integer(record.get('timeout', 60), 0, 65535)
    except Invalid as error:
        raise Invalid('invalid_advertising_timeout') from error
    out = dict(id=key, label=label, name=name, uuids=list(dict.fromkeys(uuid(v) for v in services)),
               manufacturer_data=[], service_data=[], interval_ms=interval, timeout=timeout)
    for section in ('manufacturer_data', 'service_data'):
        rows = record.get(section, [])
        if not isinstance(rows, list) or len(rows) > 8:
            raise Invalid('invalid_advertising_data')
        used = set()
        for row in rows:
            field = 'company' if section == 'manufacturer_data' else 'uuid'
            if not isinstance(row, dict) or set(row) != {field, 'hex'}:
                raise Invalid('invalid_advertising_data')
            identifier = integer(row[field], 0, 65535) if field == 'company' else uuid(row[field])
            if identifier in used:
                raise Invalid('invalid_advertising_data')
            used.add(identifier)
            out[section].append({field: identifier, 'hex': hex_bytes(row['hex']).hex()})
    # Legacy advertising payload and scan response each have 31 bytes. BlueZ puts
    # LocalName in the scan response; do not silently trim developer test data.
    sizes = {size: sum(uuid_size(v) == size for v in out['uuids']) for size in (2, 4, 16)}
    length = sum(2 + size * count for size, count in sizes.items() if count)
    length += sum(4 + len(bytes.fromhex(v['hex'])) for v in out['manufacturer_data'])
    length += sum(2 + uuid_size(v['uuid']) + len(bytes.fromhex(v['hex'])) for v in out['service_data'])
    if length > 31:
        raise Invalid('advertising_payload_too_long')
    if not name and not length:
        raise Invalid('advertising_payload_empty')
    return out


def validate_profiles(rows):
    if not isinstance(rows, list) or len(rows) > 32:
        raise Invalid('invalid_advertising_profiles')
    output = [validate_profile(row) for row in rows]
    if len({row['id'] for row in output}) != len(output):
        raise Invalid('invalid_advertising_id')
    return output


class Advertisement(ServiceInterface):
    def __init__(self, owner, record):
        super().__init__(INTERFACE)
        self.owner, self.record = owner, record

    @method()
    def Release(self):
        self.owner.released(self, 'released')

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> 's':
        return 'broadcast'

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> 's':
        return self.record['name']

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> 'as':
        return self.record['uuids']

    @dbus_property(access=PropertyAccess.READ)
    def ManufacturerData(self) -> 'a{qv}':
        return {v['company']: Variant('ay', bytes.fromhex(v['hex'])) for v in self.record['manufacturer_data']}

    @dbus_property(access=PropertyAccess.READ)
    def ServiceData(self) -> 'a{sv}':
        return {v['uuid']: Variant('ay', bytes.fromhex(v['hex'])) for v in self.record['service_data']}

    @dbus_property(access=PropertyAccess.READ)
    def MinInterval(self) -> 'u':
        return self.record['interval_ms']

    @dbus_property(access=PropertyAccess.READ)
    def MaxInterval(self) -> 'u':
        return self.record['interval_ms']

    @dbus_property(access=PropertyAccess.READ)
    def Timeout(self) -> 'q':
        return self.record['timeout']


class Advertising:
    def __init__(self, blue, path, emit):
        self.blue, self.path, self.emit = blue, Path(path), emit
        try:
            self.profiles = validate_profiles(json.loads(self.path.read_text()))
        except FileNotFoundError:
            self.profiles = []
        self.active = {}

    def snapshot(self):
        path = '/org/bluez/' + self.blue.adapter
        props = self.blue.objects.get(path, {}).get(MANAGER)
        return {'supported': props is not None, 'powered': bool(self.blue.objects.get(path, {}).get(ADAPTER, {}).get('Powered')),
                'supported_instances': (props or {}).get('SupportedInstances', 0),
                'active_instances': (props or {}).get('ActiveInstances', 0),
                'profiles': [dict(v, active=v['id'] in self.active,
                                  state=self.active.get(v['id'], {}).get('state', 'stopped'),
                                  started=self.active.get(v['id'], {}).get('started')) for v in self.profiles]}

    def save(self, record):
        if not isinstance(record, dict):
            raise Invalid('invalid_advertising_profile')
        record = dict(record)
        record['id'] = record.get('id') or secrets.token_hex(8)
        row = validate_profile(record)
        if row['id'] in self.active:
            raise Invalid('stop_advertising_before_edit')
        rows = [v for v in self.profiles if v['id'] != row['id']] + [row]
        self.replace(rows)
        return row['id']

    def delete(self, key):
        if key in self.active:
            raise Invalid('stop_advertising_before_edit')
        if not any(v['id'] == key for v in self.profiles):
            raise Invalid('advertising_profile_not_found')
        self.replace([v for v in self.profiles if v['id'] != key])

    def replace(self, rows):
        rows = validate_profiles(rows)
        atomic_json(self.path, rows)
        self.profiles = rows
        self.emit('advertising', {'state': 'saved'})

    async def start(self, key):
        row = next((v for v in self.profiles if v['id'] == key), None)
        if row is None:
            raise Invalid('advertising_profile_not_found')
        if key in self.active:
            return
        cap = self.snapshot()
        if not cap['supported']:
            raise Invalid('advertising_not_supported')
        if not cap['powered']:
            raise Invalid('advertising_requires_power')
        if max(cap['active_instances'], len(self.active)) >= cap['supported_instances']:
            raise Invalid('advertising_instances_full')
        manager = self.blue.adapter_path()
        obj = Advertisement(self, row)
        path = '/org/openwrt/BluetoothLab/a' + secrets.token_hex(8)
        bus = self.blue.bus
        bus.export(path, obj)
        self.active[key] = {'object': obj, 'path': path, 'manager': manager, 'bus': bus, 'started': time.time(), 'state': 'starting'}
        try:
            await self.blue.call(manager, MANAGER, 'RegisterAdvertisement', 'oa{sv}', [path, {}])
            if key not in self.active:
                raise Invalid('advertising_session_ended')
            self.active[key]['state'] = 'active'
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # A timed-out registration can still complete at BlueZ. Keep its
            # export and stop handle instead of orphaning a possible broadcast.
            if key in self.active:
                self.active[key]['state'] = 'uncertain'
                self.emit('advertising', {'id': key, 'state': 'uncertain'})
            raise
        except BaseException:
            self.released(obj, 'failed')
            raise
        self.emit('advertising', {'id': key, 'state': 'started'})

    def released(self, obj, reason):
        key = obj.record['id']
        session = self.active.get(key)
        if session and session['object'] is obj:
            self.active.pop(key)
            session['bus'].unexport(session['path'], obj)
            self.emit('advertising', {'id': key, 'state': reason})

    def reconcile(self):
        # Called only after BlueZ change signals, never from a polling timer.
        for session in list(self.active.values()):
            props = self.blue.objects.get(session['manager'], {})
            if MANAGER not in props or not props.get(ADAPTER, {}).get('Powered'):
                self.released(session['object'], 'stopped')

    async def stop(self, key):
        session = self.active.get(key)
        if not session:
            return
        try:
            await self.blue.call(session['manager'], MANAGER, 'UnregisterAdvertisement', 'o', [session['path']])
        except BluezError as error:
            if error.name != BLUEZ + '.Error.DoesNotExist':
                raise
        self.released(session['object'], 'stopped')

    async def close(self):
        for key in list(self.active):
            await self.stop(key)
