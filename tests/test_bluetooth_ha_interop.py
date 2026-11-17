#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Independent aioesphomeapi client against local fake-radio proxy, no hardware.

Install aioesphomeapi into a development directory and pass --client-path.
This dependency is a test client only, never shipped in the firmware.
"""
import argparse
import asyncio
import base64
import copy
import glob
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--client-path',type=Path)
    args=parser.parse_args()
    sys.dont_write_bytecode=True
    if args.client_path:sys.path.insert(0,str(args.client_path))
    # Import the independent client first, without altering its protocol/framing.
    from aioesphomeapi import APIClient
    import aioesphomeapi
    base=Path(__file__).resolve().parents[1]/'openwrt/overlay/usr/lib/unisoc-bluetooth'
    sys.path[:0]=[str(base)]+glob.glob(str(base/'vendor/*.whl'))
    from core import DEFAULT
    from bluez import Bluez,ADAPTER,DEVICE,SERVICE,CHAR
    from proxy import Proxy
    async def run():
        mac='54:48:E6:8F:80:A5';number=int(mac.replace(':',''),16)
        ad='/org/bluez/hci0';dev=ad+'/dev_'+mac.replace(':','_');svc=dev+'/service0001';char=svc+'/char0002'
        su='0000180f-0000-1000-8000-00805f9b34fb';cu='00002a19-0000-1000-8000-00805f9b34fb'
        blue=Bluez(lambda *_:None);blue.objects={ad:{ADAPTER:{'Address':mac,'Powered':True}},
            dev:{DEVICE:{'Address':mac,'Connected':False,'ServicesResolved':False}},
            svc:{SERVICE:{'UUID':su,'Device':dev}},char:{CHAR:{'UUID':cu,'Service':svc,'Flags':['read','write','notify']}}};blue.select('hci0')
        async def action(target,kind):
            blue.objects[dev][DEVICE].update(Connected=kind=='connect',ServicesResolved=kind=='connect')
        blue.device_action=AsyncMock(side_effect=action)
        async def call(path,interface,member,signature='',body=None):
            return [b'\x63'] if member=='ReadValue' else []
        blue.call=AsyncMock(side_effect=call)
        g=SimpleNamespace(bluez=blue,config=copy.deepcopy(DEFAULT),lock=asyncio.Lock())
        p=Proxy(g);cfg=copy.deepcopy(DEFAULT['gateway']['proxy'])
        cfg.update(enabled=True,active=True,devices=[mac],port=0,psk=base64.b64encode(b'\x19'*32).decode())
        await p.configure(cfg);port=p.server.sockets[0].getsockname()[1]
        client=APIClient('127.0.0.1',port,noise_psk=cfg['psk'],expected_name=cfg['name'],expected_mac=mac)
        try:
            await client.connect(login=True)
            info=await client.device_info()
            assert info.bluetooth_proxy_feature_flags==39,info
            packets=[];unsub=client.subscribe_bluetooth_le_raw_advertisements(packets.append)
            await asyncio.sleep(.03)
            p.event({'type':'device','data':{'Address':mac,'RSSI':-42,'ServiceData':{'0000fcd2-0000-1000-8000-00805f9b34fb':'4002ca09'}}})
            await asyncio.sleep(.2)
            assert packets and packets[0].advertisements[0].rssi==-42
            states=[]
            remove=await client.bluetooth_device_connect(number,lambda *s:states.append(s),timeout=3,feature_flags=39,address_type=0)
            services=await client.bluetooth_gatt_get_services(number)
            assert services.services[0].uuid==su,services
            assert services.services[0].characteristics[0].uuid==cu,services
            assert await client.bluetooth_gatt_read(number,2)==bytearray(b'\x63')
            await client.bluetooth_gatt_write(number,2,b'\x01',response=True)
            notifications=[]
            stop,remove_notify=await client.bluetooth_gatt_start_notify(number,2,lambda h,value:notifications.append((h,value)))
            p.event({'type':'gatt','data':{'address':mac,'service':su,'characteristic':cu,'hex':'64'}})
            await asyncio.sleep(.05)
            assert notifications==[(2,bytearray(b'\x64'))],notifications
            await stop();remove_notify();remove();unsub()
            print('Official aioesphomeapi: Noise handshake, device info, advertisement, v3 connection, service UUIDs, GATT read/write and notifications passed.')
        finally:await client.disconnect();await p.close()
    asyncio.run(run())

if __name__=='__main__':main()
