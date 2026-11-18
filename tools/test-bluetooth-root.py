#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Read-only ARM64 import smoke; no system D-Bus, HCI, UART or modem access."""
import argparse
from pathlib import Path
import subprocess

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--image',required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    for name in ('usr/bin/python3','usr/bin/bluetoothd','usr/bin/btattach','usr/bin/rfcomm','usr/bin/sdptool',
                 'usr/sbin/unisoc-bluetooth','usr/libexec/rpcd/unisoc-bluetooth',
                 'www/cgi-bin/unisoc-bluetooth-events','etc/rc.d/S65unisoc-bluetooth',
                 'usr/lib/unisoc-bluetooth/events.uc','usr/lib/ucode/socket.so','usr/lib/uhttpd_ubus.so',
                 'usr/bin/unisoc-ble-decode','usr/share/unisoc-bluetooth/decoder-source.tar.gz'):
        if not (root/name).exists():raise ValueError('Bluetooth root input missing: '+name)
    code="""import glob,sys
sys.path[:0]=['/usr/lib/unisoc-bluetooth']+glob.glob('/usr/lib/unisoc-bluetooth/vendor/*.whl')
import asyncio,ssl,termios,fcntl,dbus_next,aiomqtt,paho.mqtt.client
import core,bluez,forward,serial_bridge,hci,daemon,transport,stream_auth
import smart_config,bthome,catalog,commands,recovery,proxy,proxy_wire,homeassistant,decoder,advertising
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from noise.connection import NoiseConnection
from zeroconf.asyncio import AsyncZeroconf
assert not core.DEFAULT['autostart'] and not core.DEFAULT['gateway']['enabled']
print('BLUETOOTH_ARM64_IMPORT_OK')
"""
    subprocess.run(['docker','run','--rm','--network','none','--platform','linux/arm64',
                    '-v',str(root)+':/target:ro','--entrypoint','/bin/busybox',
                    args.image,'chroot','/target','/usr/bin/python3','-B','-c',code],check=True)

if __name__=='__main__':main()
