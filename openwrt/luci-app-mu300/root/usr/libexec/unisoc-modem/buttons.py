#!/usr/bin/python3
"""Button settings and dispatch; rpcd exposes get/set, existing input owners run."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.environ.get('MU300_SYSROOT', '/'))
BIN = Path(os.environ.get('MU300_BIN', '/opt/mu300/bin'))
LIB = Path(os.environ.get('MU300_DASH_BIN', '/usr/libexec/unisoc-modem'))
RUN = Path(os.environ.get('MU300_RUN', '/run'))
ACTIONS = ('none', 'data-switch', 'wifi-band', 'nfc-toggle', 'custom')
LEGACY = ('page-next', 'toggle-wifi', 'reconnect-cellular', 'refresh-status')


def call(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], text=True, capture_output=True,
                          check=True, **kwargs).stdout.strip()


def model():
    marker = ROOT / 'etc/mu300/model'
    product = marker.read_text().strip() if marker.exists() else ''
    return product if product in ('u30pro', 'f50pro') else call([BIN / 'mu300-device'])


def keys(product):
    if product == 'u30pro':
        base = 'u30pro_native_control.settings.'
        return {name: base + suffix for name, suffix in {
            'short': 'home_short_action', 'long': 'home_long_action', 'double': 'home_double_action',
            'short_command': 'home_short_command', 'long_command': 'home_long_command', 'double_command': 'home_double_command',
            'power_short': 'power_short_action', 'power_command': 'power_short_command',
            'power_double': 'power_double_action', 'power_double_command': 'power_double_command'}.items()}
    base = 'unisoc_modem.main.' + product + '_button_'
    return {name: base + name for name in
            ('short', 'long', 'double', 'short_command', 'long_command', 'double_command',
             'power_short', 'power_command', 'power_double', 'power_double_command')}


def read_setting(key, default):
    result = subprocess.run(['uci', '-q', 'get', key], text=True, capture_output=True)
    return result.stdout.rstrip('\n') if result.returncode == 0 else default


def settings(product):
    defaults = dict(short='none' if product in ('f50pro', 'v50') else 'page-next' if product == 'u30pro' else 'default',
                    long='none' if product in ('f50pro', 'u30pro', 'v50') else 'default',
                    double='wifi-band' if product == 'u30air' else 'none', double_command='',
                    short_command='', long_command='', power_short='default', power_command='',
                    power_double='none', power_double_command='')
    return {name: read_setting(key, defaults[name]) for name, key in keys(product).items()}


def validate(product, values):
    for kind, command in (('short', 'short_command'), ('long', 'long_command'),
                          ('double', 'double_command'), ('power_short', 'power_command'),
                          ('power_double', 'power_double_command')):
        allowed = ACTIONS + ('default',)
        if product == 'u30pro' and kind in ('short', 'long'):
            allowed += LEGACY
        if values[kind] not in allowed:
            raise ValueError('invalid_button_action')
        text = values[command]
        if not isinstance(text, str) or len(text) > 4096 or any(c in text for c in '\r\n\0'):
            raise ValueError('invalid_button_command')
        if values[kind] == 'custom' and not text.strip():
            raise ValueError('empty_button_command')
    if product not in ('u30pro', 'u30air') and (values['power_short'] != 'default' or values['power_double'] not in ('none', 'default')):
        raise ValueError('power_button_unavailable')


def save(product, values, incoming):
    if not isinstance(incoming, dict):
        raise ValueError('invalid_button_settings')
    # rpcd adds the authenticated session to HTTP calls. It is transport
    # metadata, never a button setting or a substitute for rpcd's ACL checks.
    incoming = {key: value for key, value in incoming.items() if key != 'ubus_rpc_session'}
    if set(incoming) - set(values):
        raise ValueError('invalid_button_settings')  # also rejects power_long
    values.update(incoming)
    validate(product, values)
    mapping = keys(product)
    changed = []
    try:
        for name in incoming:
            call(['uci', '-q', 'set', mapping[name] + '=' + values[name]])
            changed.append(mapping[name])
        call(['uci', '-q', 'commit', mapping['short'].split('.')[0]])
    except subprocess.CalledProcessError:
        for key in changed:
            subprocess.run(['uci', '-q', 'revert', key], capture_output=True)
        raise ValueError('storage_failed') from None
    if product == 'u30pro':
        subprocess.run(['ubus', '-t', '3', 'call', 'u30pro.panel', 'reload'], capture_output=True)


def execute(product, action, command):
    if action == 'custom':
        argv = ['/bin/sh', '-c', command]
    elif action == 'wifi-band':
        argv = [BIN / 'mu300-wifi-band', 'toggle']
    elif action == 'nfc-toggle':
        state = json.loads(call([LIB / 'nfc', 'get']))
        if not state.get('ok') or not state.get('supported'):
            raise ValueError('nfc_unavailable')
        result = json.loads(call([LIB / 'nfc', 'set'], input=json.dumps({'enabled': 0 if state['enabled'] else 1})))
        if not result.get('ok'):
            raise ValueError(result.get('error', 'nfc_apply_failed'))
        return
    elif action == 'data-switch':
        slot = (RUN / 'mu300-data-sim-actual').read_text().strip()
        if slot not in ('0', '1'):
            raise ValueError('sim_read_failed')
        target = 1 - int(slot)
        if product in ('u30pro', 'f50pro'):
            state = json.loads(call([BIN / 'mu300-physical-sim', 'status']))
            card = state['logical_cards'][target]
            if type(card) is not int or not 0 <= card <= 4:
                raise ValueError('sim_unavailable')
            target = product + '-card-' + str(card)
        argv = [LIB / 'data-sim', 'apply', 'data', str(target)]
    elif action in LEGACY and action != 'page-next':
        argv = ['/usr/sbin/u30pro-panel-control', {'toggle-wifi': 'wifi-toggle',
                'reconnect-cellular': 'reconnect', 'refresh-status': 'key-refresh'}[action]]
    else:
        return
    call(argv)


def main():
    product = model()
    supported = product in ('u30pro', 'f50pro', 'u30air', 'v50')
    operation = sys.argv[1] if len(sys.argv) > 1 else 'get'
    if operation in ('run', 'worker'):
        if not supported or len(sys.argv) != 4:
            return 2
        button, kind = sys.argv[2:]
        if button not in ('function', 'power') or kind not in ('short', 'long', 'double'):
            return 2
        if button == 'power' and (kind == 'long' or product not in ('u30pro', 'u30air')):
            return 2  # held power stays with the existing shutdown owner
        values = settings(product)
        validate(product, values)
        action = values[kind if button == 'function' else 'power_' + kind]
        command = values[kind + '_command' if button == 'function' else
                         'power_command' if kind == 'short' else 'power_double_command']
        if action == 'default' and product == 'v50':
            action = 'none'
        if action == 'default' and kind == 'double':
            action = 'wifi-band' if product == 'u30air' and button == 'function' else 'none'
        if action in ('default', 'page-next'):
            return 3  # explicit default, never an error fallback
        if action == 'none':
            return 0
        if operation == 'run':
            # A slow command/SIM transaction must not hold up the power key.
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker', button, kind],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            execute(product, action, command)
        return 0
    if operation not in ('get', 'set'):
        raise ValueError('invalid_button_operation')
    if not supported:
        print(json.dumps(dict(ok=1, supported=False)))
        return 0
    values = settings(product)
    if operation == 'set':
        save(product, values, json.load(sys.stdin))
    print(json.dumps(dict(ok=1, supported=True, model=product,
                         power=product in ('u30pro', 'u30air'), **values)))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, IndexError) as error:
        if len(sys.argv) > 1 and sys.argv[1] in ('run', 'worker'):
            subprocess.run(['logger', '-t', 'mu300-buttons', 'Button action failed: ' + str(error)], check=False)
            sys.exit(2)
        print(json.dumps(dict(ok=0, error=str(error))))
