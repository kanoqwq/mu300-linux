# SPDX-License-Identifier: MIT
"""Configuration and data contracts shared by the web Bluetooth gateway."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
from smart_config import DEFAULTS, validate_features, public_features

MAC = re.compile(r'^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$')
UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
DEFAULT = {
    'autostart': False, 'adapter': '', 'alias': '',
    'gateway': {
        'enabled': False, 'transport': 'le', 'devices': [],
        'mqtt': {'enabled': False, 'host': '', 'port': 1883, 'tls': False,
                 'username': '', 'password': '', 'topic': 'unisoc/bluetooth',
                 'ha_discovery': False, 'ha_prefix': 'homeassistant'},
        'http': {'enabled': False, 'url': '', 'token': ''},
        'subscriptions': [], 'serial': [],
        **copy.deepcopy(DEFAULTS),
    },
}

class Invalid(ValueError):
    pass

def text(value, limit=256):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise Invalid('invalid_text')
    return value

def boolean(value):
    if type(value) is not bool:
        raise Invalid('invalid_boolean')
    return value

def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise Invalid('invalid_integer')
    return value

def address(value):
    value = text(value, 17).upper()
    if not MAC.fullmatch(value):
        raise Invalid('invalid_bluetooth_address')
    return value

def uuid(value):
    value = text(value, 36).lower()
    if len(value) in (4, 8):
        value = value.zfill(8) + '-0000-1000-8000-00805f9b34fb'
    if not UUID.fullmatch(value):
        raise Invalid('invalid_uuid')
    return value

def hex_bytes(value):
    value = text(value, 1536).replace(' ', '')
    if len(value) % 2 or len(value) > 1024 or re.search('[^0-9a-fA-F]', value):
        raise Invalid('invalid_hex')
    return bytes.fromhex(value)

def decode(data, codec):
    data = bytes(data)
    if codec == 'hex':
        return data.hex()
    if codec == 'text':
        return data.decode('utf-8', errors='replace')
    sizes = {'u8': (1, False), 'u16le': (2, False), 'i16le': (2, True), 'u32le': (4, False)}
    if codec not in sizes or len(data) != sizes[codec][0]:
        raise Invalid('invalid_value_size')
    return int.from_bytes(data, 'little', signed=sizes[codec][1])

def validate(candidate, previous=None):
    if not isinstance(candidate, dict):
        raise Invalid('invalid_configuration')
    old = copy.deepcopy(previous or DEFAULT)
    out = copy.deepcopy(DEFAULT)
    if set(candidate) - set(out):
        raise Invalid('unknown_setting')
    for key in ('autostart',):
        out[key] = boolean(candidate.get(key, old[key]))
    out['adapter'] = text(candidate.get('adapter', old['adapter']), 16)
    if out['adapter'] and not re.fullmatch(r'hci[0-9]{1,3}', out['adapter']):
        raise Invalid('invalid_adapter')
    out['alias'] = text(candidate.get('alias', old['alias']), 64)
    gateway = candidate.get('gateway', old['gateway'])
    if not isinstance(gateway, dict) or set(gateway) - set(out['gateway']):
        raise Invalid('invalid_gateway')
    target = out['gateway']
    target['enabled'] = boolean(gateway.get('enabled', old['gateway']['enabled']))
    target['transport'] = gateway.get('transport', old['gateway']['transport'])
    if target['transport'] not in ('auto', 'le', 'bredr'):
        raise Invalid('invalid_transport')
    devices = gateway.get('devices', old['gateway']['devices'])
    if not isinstance(devices, list) or len(devices) > 256:
        raise Invalid('invalid_device_list')
    target['devices'] = list(dict.fromkeys(address(d) for d in devices))
    for section in ('mqtt', 'http'):
        values = gateway.get(section, old['gateway'][section])
        if not isinstance(values, dict) or set(values) - (set(target[section]) | {'clear_secret'}):
            raise Invalid('invalid_' + section)
        dst, prev = target[section], old['gateway'][section]
        for key in dst:
            raw = values.get(key, prev[key])
            dst[key] = boolean(raw) if isinstance(dst[key], bool) else (
                integer(raw, 1, 65535) if key == 'port' else text(raw, 2048 if key == 'url' else 256))
        secret = 'password' if section == 'mqtt' else 'token'
        if not dst[secret]:
            dst[secret] = prev[secret]
        if boolean(values.get('clear_secret', False)):
            dst[secret] = ''
    mqtt, http = target['mqtt'], target['http']
    if mqtt['host'] and (re.search(r'[\s/@?#]', mqtt['host']) or ':' in mqtt['host'] and not _ipv6(mqtt['host'])):
        raise Invalid('invalid_mqtt_host')
    if any(not s or re.search(r'[\s+#]', s) for s in (mqtt['topic'], mqtt['ha_prefix'])):
        raise Invalid('invalid_mqtt_topic')
    if mqtt['enabled'] and not mqtt['host']:
        raise Invalid('mqtt_host_required')
    if http['url']:
        try:
            parsed = urlsplit(http['url'])
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise Invalid('invalid_webhook_url')
            parsed.port
        except ValueError as error:
            raise Invalid('invalid_webhook_url') from error
    if http['enabled'] and not http['url']:
        raise Invalid('webhook_url_required')
    subs = gateway.get('subscriptions', old['gateway']['subscriptions'])
    if not isinstance(subs, list) or len(subs) > 128:
        raise Invalid('invalid_subscriptions')
    for item in subs:
        if not isinstance(item, dict) or set(item) - {'device', 'service', 'characteristic', 'codec', 'name', 'unit'}:
            raise Invalid('invalid_subscription')
        entry = {'device': address(item.get('device')), 'service': uuid(item.get('service')),
                 'characteristic': uuid(item.get('characteristic')),
                 'codec': item.get('codec', 'hex'), 'name': text(item.get('name', ''), 64),
                 'unit': text(item.get('unit', ''), 16)}
        if entry['codec'] not in ('hex', 'text', 'u8', 'u16le', 'i16le', 'u32le'):
            raise Invalid('invalid_codec')
        target['subscriptions'].append(entry)
    serial = gateway.get('serial', old['gateway']['serial'])
    if not isinstance(serial, list) or len(serial) > 8:
        raise Invalid('invalid_serial_links')
    used_ports, used_devices = set(), set()
    for item in serial:
        if not isinstance(item, dict) or set(item) - {'device', 'channel', 'bind', 'port'}:
            raise Invalid('invalid_serial_link')
        entry = {'device': address(item.get('device')), 'channel': integer(item.get('channel', 1), 1, 30),
                 'bind': text(item.get('bind', '127.0.0.1'), 45), 'port': integer(item.get('port', 0), 0, 65535)}
        try:
            ip = ipaddress.ip_address(entry['bind'])
        except ValueError as error:
            raise Invalid('invalid_serial_bind') from error
        if ip.is_unspecified or ip.is_multicast or not (ip.is_private or ip.is_loopback):
            raise Invalid('serial_bind_must_be_local')
        if entry['device'] in used_devices or entry['port'] and (entry['bind'], entry['port']) in used_ports:
            raise Invalid('duplicate_serial_link')
        if entry['port'] and entry['port'] < 1024:
            raise Invalid('serial_port_must_be_unprivileged')
        used_devices.add(entry['device'])
        used_ports.add((entry['bind'], entry['port']))
        target['serial'].append(entry)
    validate_features(gateway, old['gateway'], target)
    return out

def _ipv6(host):
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        return False

def public_config(config):
    out = copy.deepcopy(config)
    public_features(out)
    for section, secret in (('mqtt', 'password'), ('http', 'token')):
        dst = out['gateway'][section]
        dst[secret + '_set'] = bool(dst[secret])
        dst[secret] = ''
    return out

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, separators=(',', ':'), ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def load_config(path):
    try:
        raw = Path(path).read_text()
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT)
    return validate(json.loads(raw))

def plain(value):
    if hasattr(value, 'signature') and hasattr(value, 'value'):
        return plain(value.value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value
