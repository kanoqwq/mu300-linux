#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Explicit board transport setup, called only when Bluetooth is requested."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from core import Invalid, address, atomic_json
from hci import aic_address

def unblock_adapter(name, sysfs=Path('/sys/class/bluetooth')):
    if not name or not name.startswith('hci') or not name[3:].isdigit():
        raise Invalid('invalid_adapter')
    for entry in (Path(sysfs)/name).glob('rfkill*'):
        if (entry/'type').read_text().strip() != 'bluetooth':
            continue
        if (entry/'hard').read_text().strip() != '0':
            raise Invalid('bluetooth_hard_blocked')
        if (entry/'soft').read_text().strip() != '0':
            (entry/'soft').write_text('0\n')

def run(*args):
    subprocess.run(args, check=True, timeout=15)

def rfkill_unblock():
    candidates = [p for p in Path('/sys/class/rfkill').glob('rfkill*')
                  if (p/'type').read_text().strip() == 'bluetooth'
                  and (p/'name').read_text().strip() == 'bluetooth']
    if len(candidates) != 1:
        raise Invalid('board_bluetooth_rfkill_missing_or_ambiguous')
    entry = candidates[0]
    if (entry/'hard').read_text().strip() != '0':
        raise Invalid('bluetooth_hard_blocked')
    (entry/'soft').write_text('0\n')

def controller_address(profile):
    # Preserve existing addresses; do not derive a shared value from an absent machine-id.
    override = Path('/etc/mu300/bluetooth.conf')
    if override.exists():
        for line in override.read_text().splitlines():
            if line.startswith('BDADDR='):
                return address(line[7:].strip())
    legacy = Path('/var/lib/mu300/bdaddr')
    state = Path('/etc/unisoc-bluetooth/address.json')
    if state.exists():
        return address(json.loads(state.read_text())['address'])
    if profile == 'mu300-pcie' and legacy.exists():
        mac = address(legacy.read_text().strip())
    else:
        raw = bytearray(os.urandom(6))
        raw[0] = (raw[0] | 2) & 0xfe
        mac = ':'.join(f'{value:02X}' for value in raw)
    atomic_json(state, {'address': mac})
    return mac

def prepare(profile):
    if profile == 'mu300-pcie':
        run('/sbin/modprobe', 'wcn_bsp')
        run('/sbin/modprobe', 'sprdbt_tty')
        for _ in range(20):
            if Path('/dev/ttyBT0').exists():
                break
            time.sleep(.1)
        if not Path('/dev/ttyBT0').exists():
            raise Invalid('bluetooth_tty_missing')
        rfkill_unblock()
        run('/opt/mu300/bin/mu300-bt-init', '-d', '/dev/ttyBT0',
            '-p', '/usr/lib/firmware/bt_configure_pskey.ini',
            '-r', '/usr/lib/firmware/bt_configure_rf.ini', '-a', controller_address(profile))
    elif profile == 'u30pro-aic-uart':
        run('/sbin/modprobe', 'aic8800_bsp')
        run('/sbin/modprobe', 'aic_bt_rfkill')
        if not Path('/dev/ttyS0').exists():
            raise Invalid('bluetooth_uart_missing')
        rfkill_unblock()
        aic_address('/dev/ttyS0', controller_address(profile))
    else:
        raise Invalid('unknown_bluetooth_platform')

if __name__ == '__main__':
    try:
        prepare(Path('/etc/unisoc-bluetooth/platform').read_text().strip())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Bluetooth transport: ' + str(error), file=sys.stderr)
        sys.exit(1)
