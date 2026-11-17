#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""No HCI access: validate profiles and exercise exports over a private D-Bus."""
import asyncio
import copy
import glob
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

TOP = Path(__file__).resolve().parents[1]
BASE = TOP/'openwrt/overlay/usr/lib/unisoc-bluetooth'
sys.dont_write_bytecode = True
sys.path[:0] = [str(BASE)] + glob.glob(str(BASE/'vendor/*.whl'))
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus
from advertising import Advertising, INTERFACE, MANAGER, validate_profile, validate_profiles
from bluez import Bluez, BluezError, ADAPTER, BLUEZ, PROPS
from core import Invalid, uuid
from daemon import Gateway

AD = '/org/bluez/hci0'

def profile(**values):
    row = dict(id='test_one', label='Temperature lab', name='BLE Lab', uuids=['180f'],
                manufacturer_data=[{'company':65535,'hex':'0102'}], service_data=[],
                interval_ms=1000, timeout=60)
    row.update(values)
    return row


class ValidationTests(unittest.TestCase):
    def test_normalization_and_real_packet_budget(self):
        row=validate_profile(profile(uuids=['180f','180F']))
        self.assertEqual(row['uuids'],[uuid('180f')])
        self.assertEqual(validate_profile(profile(uuids=[],manufacturer_data=[{'company':1,'hex':'aa'*27}]))['manufacturer_data'][0]['hex'],'aa'*27)
        with self.assertRaisesRegex(Invalid,'payload_too_long'):
            validate_profile(profile(uuids=[],manufacturer_data=[{'company':1,'hex':'aa'*28}]))
        with self.assertRaisesRegex(Invalid,'payload_too_long'):
            validate_profile(profile(uuids=['12345678-1234-1234-1234-123456789012'],service_data=[{'uuid':'180f','hex':'aa'*12}]))

    def test_utf8_names_ids_and_unsafe_fields(self):
        self.assertEqual(validate_profile(profile(name='测'*9))['name'],'测'*9)
        for patch in ({'name':'测'*10},{'id':'../bad'},{'mac':'00:00:00:00:00:01'},
                      {'interval_ms':19},{'timeout':65536},{'connectable':True}):
            with self.assertRaises(Invalid):validate_profile(profile(**patch))
        with self.assertRaisesRegex(Invalid,'payload_empty'):
            validate_profile(profile(name='',uuids=[],manufacturer_data=[]))

    def test_duplicate_data_and_import_bounds(self):
        for rows in ([{'company':1,'hex':'00'}]*2,[{'company':-1,'hex':'00'}],[{'company':1,'hex':'oops'}]):
            with self.assertRaises(Invalid):validate_profile(profile(manufacturer_data=rows))
        with self.assertRaises(Invalid):validate_profiles([profile(),profile()])
        with self.assertRaises(Invalid):validate_profiles([profile(id='p'+str(i)) for i in range(33)])


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.events=[]
        self.blue=Bluez(lambda *_:None);self.blue.objects={AD:{ADAPTER:{'Powered':True},MANAGER:{'SupportedInstances':2,'ActiveInstances':0}}}
        self.blue.select('hci0');self.blue.bus=Mock();self.blue.call=AsyncMock(return_value=[])
        self.lab=Advertising(self.blue,Path(self.tmp.name)/'advertisements.json',lambda k,d:self.events.append((k,d)))
        self.lab.save(profile())

    async def asyncTearDown(self):
        await self.lab.close();self.tmp.cleanup()

    async def test_save_offline_never_activates_and_restart_does_not_replay(self):
        self.blue.objects[AD][ADAPTER]['Powered']=False
        self.lab.save(profile(name='New name'))
        self.blue.call.assert_not_awaited();self.blue.bus.export.assert_not_called()
        with self.assertRaisesRegex(Invalid,'requires_power'):await self.lab.start('test_one')
        fresh=Advertising(self.blue,self.lab.path,lambda *_:None)
        self.assertEqual(fresh.profiles[0]['name'],'New name');self.assertFalse(fresh.active)

    async def test_registration_lifetime_and_owned_cleanup(self):
        await self.lab.start('test_one');await self.lab.start('test_one')
        self.blue.call.assert_awaited_once()
        session=self.lab.active['test_one'];obj=session['object']
        self.assertEqual(obj.Type,'broadcast');self.assertEqual(obj.ManufacturerData[65535].value,b'\x01\x02')
        self.assertEqual(obj.MinInterval,1000);self.assertEqual(obj.Timeout,60)
        for action in (lambda:self.lab.save(profile()),lambda:self.lab.delete('test_one')):
            with self.assertRaisesRegex(Invalid,'stop_advertising'):action()
        obj.Release();self.assertFalse(self.lab.active)
        self.blue.bus.unexport.assert_called_once_with(session['path'],obj)
        await self.lab.start('test_one');obj.Release()
        self.assertTrue(self.lab.active)  # a stale Release cannot remove a newer session
        await self.lab.close();self.assertFalse(self.lab.active)
        self.assertEqual(self.blue.call.await_args.args[2],'UnregisterAdvertisement')

    async def test_registration_failure_cleans_exports_and_preserves_definition(self):
        self.blue.call.side_effect=BluezError(BLUEZ+'.Error.InvalidLength')
        with self.assertRaises(BluezError):await self.lab.start('test_one')
        self.assertFalse(self.lab.active);self.blue.bus.unexport.assert_called_once()
        self.assertEqual(len(self.lab.profiles),1)

    async def test_ambiguous_timeout_retains_a_stop_handle(self):
        self.blue.call.side_effect=asyncio.TimeoutError()
        with self.assertRaises(asyncio.TimeoutError):await self.lab.start('test_one')
        self.assertEqual(self.lab.snapshot()['profiles'][0]['state'],'uncertain')
        self.blue.bus.unexport.assert_not_called()
        self.blue.call.side_effect=None
        await self.lab.stop('test_one');self.assertFalse(self.lab.active)

    async def test_capability_limits_and_power_loss(self):
        self.blue.objects[AD][MANAGER]['ActiveInstances']=2
        with self.assertRaisesRegex(Invalid,'instances_full'):await self.lab.start('test_one')
        self.blue.objects[AD][MANAGER]['ActiveInstances']=0
        await self.lab.start('test_one')
        self.blue.objects[AD][ADAPTER]['Powered']=False;self.lab.reconcile()
        self.assertFalse(self.lab.active)
        self.blue.call.assert_awaited_once()  # loss does not trigger recovery or another writer
        self.blue.objects.clear();self.lab.reconcile()
        with self.assertRaisesRegex(Invalid,'not_supported'):await self.lab.start('test_one')

    async def test_stop_failure_does_not_claim_stopped(self):
        await self.lab.start('test_one')
        self.blue.call.side_effect=BluezError(BLUEZ+'.Error.Failed')
        with self.assertRaises(BluezError):await self.lab.stop('test_one')
        self.assertTrue(self.lab.active)
        self.blue.call.side_effect=BluezError(BLUEZ+'.Error.DoesNotExist')
        await self.lab.stop('test_one');self.assertFalse(self.lab.active)

    async def test_gateway_backup_restore_and_rpc_contract(self):
        g=Gateway(str(Path(self.tmp.name)/'gateway/config.json'))
        try:
            r=await g.dispatch({'action':'advertising_save','profile':dict(profile(),id='')})
            self.assertTrue(r['ok']);self.assertTrue(r['id']);self.assertFalse(r['advertising']['profiles'][0]['active'])
            backup=(await g.dispatch({'action':'backup'}))['backup']
            self.assertEqual(len(backup['advertisements']),1)
            await g.dispatch({'action':'advertising_delete','id':r['id']})
            await g.dispatch({'action':'restore','backup':backup})
            self.assertEqual(len(g.advertising.profiles),1);self.assertFalse(g.advertising.active)
            bad=copy.deepcopy(backup);bad['advertisements'][0]['interval_ms']=0
            with self.assertRaises(Invalid):await g.dispatch({'action':'restore','backup':bad})
            self.assertEqual(len(g.advertising.profiles),1)
        finally:
            g.catalog.close();await g.recovery.close();await g.forward.close();await g.decoder.close()

    async def test_real_private_bus_property_signatures_and_release(self):
        process=await asyncio.create_subprocess_exec('dbus-daemon','--session','--nofork','--print-address=1',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        address=(await process.stdout.readline()).decode().strip()
        server=await MessageBus(bus_address=address).connect();await server.request_name(BLUEZ)
        client=await MessageBus(bus_address=address).connect()
        self.blue.bus=client
        captured={};pending=[]
        async def register(message):
            try:
                response=await server.call(Message(destination=message.sender,path=message.body[0],interface=PROPS,member='GetAll',signature='s',body=[INTERFACE]))
                captured.update(response.body[0]);captured['path']=message.body[0];captured['owner']=message.sender
                server.send(Message.new_method_return(message))
            except Exception as error:server.send(Message.new_error(message,'org.bluez.Error.Failed',str(error)))
        def handler(message):
            if message.message_type!=MessageType.METHOD_CALL:return
            if message.member=='RegisterAdvertisement':pending.append(asyncio.create_task(register(message)));return True
            if message.member=='UnregisterAdvertisement':return Message.new_method_return(message)
        server.add_message_handler(handler)
        self.blue.call=Bluez.call.__get__(self.blue,Bluez)
        try:
            await self.lab.start('test_one')
            self.assertEqual(captured['Type'].value,'broadcast');self.assertEqual(captured['ManufacturerData'].signature,'a{qv}')
            self.assertEqual(captured['ServiceData'].signature,'a{sv}');self.assertEqual(captured['Timeout'].signature,'q')
            self.assertEqual(captured['ServiceUUIDs'].value,[uuid('180f')])
            reply=await server.call(Message(destination=captured['owner'],path=captured['path'],interface=INTERFACE,member='Release'))
            self.assertNotEqual(reply.message_type,MessageType.ERROR);self.assertFalse(self.lab.active)
            self.assertIn(('advertising',{'id':'test_one','state':'released'}),self.events)
        finally:
            await asyncio.gather(*pending);client.disconnect();server.disconnect();process.terminate();await process.wait()
            self.blue.bus=Mock();self.blue.call=AsyncMock(return_value=[])


if __name__=='__main__':unittest.main(verbosity=2)
