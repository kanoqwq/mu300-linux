#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Smart-home contracts using spec vectors, local sockets and fake radio state."""
import asyncio
import base64
import copy
import glob
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

sys.dont_write_bytecode=True
TOP=Path(__file__).resolve().parents[1]
BASE=TOP/'openwrt/overlay/usr/lib/unisoc-bluetooth'
sys.path[:0]=[str(BASE)]+glob.glob(str(BASE/'vendor/*.whl'))
from core import DEFAULT,Invalid,load_config,public_config,validate
from bthome import SERVICE as BTHOME,decode_payload
from catalog import Catalog,ReportPolicy
from commands import command_bytes,execute
from daemon import Gateway
from forward import Forwarder
from proxy import Proxy
from proxy_wire import field,message,one,parse,advertisement
from bluez import ADAPTER,DEVICE,SERVICE,CHAR,Bluez
from recovery import Recovery
from decoder import measurements,packets
import homeassistant as ha

MAC='54:48:E6:8F:80:A5';KEY='231d39c1d7cc1ab1aee224cd096db932'
AD='/org/bluez/hci0';DEV=AD+'/dev_'+MAC.replace(':','_')
SVC=DEV+'/service0001';CHR=SVC+'/char0002'
BATTERY='0000180f-0000-1000-8000-00805f9b34fb';LEVEL='00002a19-0000-1000-8000-00805f9b34fb'

def configured_command(**overrides):
    c={'id':'lamp','name':'Lamp','device':MAC,'kind':'write','domain':'switch',
       'service':BATTERY,'characteristic':LEVEL,'on':'01','off':'00'}
    c.update(overrides)
    return validate({'gateway':{'commands':[c]}})['gateway']['commands'][0]

class ContractTests(unittest.TestCase):
    def test_bthome_plain_and_duplicate_channels(self):
        d=decode_payload(MAC,'4002c40903bf1301011a01')
        values={m['key']:m['value'] for m in d['measurements']}
        self.assertEqual(values,{'temperature_1':25.0,'humidity_1':50.55,'battery_1':1,'door_1':True})
        d=decode_payload(MAC,'400201000202003a013a02')
        self.assertEqual([m['key'] for m in d['measurements']],['temperature_1','temperature_2','button_1','button_2'])

    def test_bthome_official_encrypted_vector(self):
        d=decode_payload(MAC,'41e445f3c9962b332211006c7c4519',KEY)
        self.assertEqual(d['counter'],1122867)
        self.assertEqual([m['value'] for m in d['measurements']],[25.06,50.55])
        with self.assertRaisesRegex(Invalid,'authentication_failed'):decode_payload(MAC,'41e445f3c9962b332211006c7c4518',KEY)
        with self.assertRaisesRegex(Invalid,'key_required'):decode_payload(MAC,'41e445f3c9962b332211006c7c4519')

    def test_bthome_rejects_truncated_and_unknown_objects(self):
        for packet in ('','4002','40ff0101','401a02'):
            with self.subTest(packet=packet),self.assertRaises(Invalid):decode_payload(MAC,packet)

    def test_config_roundtrip_and_secret_visibility(self):
        cfg=validate({'gateway':{'known_devices':[{'device':MAC,'name':'Desk','room':'Office','bindkey':KEY}]}})
        self.assertEqual(validate(cfg),cfg)
        public=public_config(cfg)
        self.assertTrue(public['gateway']['known_devices'][0]['bindkey_set'])
        self.assertNotIn(KEY,json.dumps(public))
        updated=validate({'gateway':{'known_devices':[{'device':MAC,'name':'New','bindkey':''}]}},cfg)
        self.assertEqual(updated['gateway']['known_devices'][0]['bindkey'],KEY)
        updated=validate({'gateway':{'known_devices':[{'device':MAC,'clear_bindkey':True}]}},cfg)
        self.assertEqual(updated['gateway']['known_devices'][0]['bindkey'],'')
        with self.assertRaises(Invalid):validate({'gateway':{'known_devices':[{'device':MAC,'bindkey':'bad'}]}})

    def test_proxy_is_opt_in_encrypted_and_scoped(self):
        for p in ({'enabled':True},{'enabled':True,'psk':'bad'},
                  {'enabled':True,'psk':base64.b64encode(b'x'*32).decode(),'active':True}):
            with self.assertRaises(Invalid):validate({'gateway':{'enabled':True,'proxy':p}})
        cfg=validate({'gateway':{'enabled':True,'proxy':{'enabled':True,'psk':base64.b64encode(b'x'*32).decode(),'active':True,'devices':[MAC]}}})
        self.assertTrue(public_config(cfg)['gateway']['proxy']['psk_set'])
        self.assertEqual(public_config(cfg)['gateway']['proxy']['psk'],'')
        with self.assertRaises(Invalid):validate({'gateway':{'proxy':{'bind':'0.0.0.0'}}})

    def test_template_has_no_executable_content(self):
        templates=json.loads((TOP/'openwrt/overlay/usr/share/unisoc-bluetooth/templates.json').read_text())
        for t in templates:
            cfg=validate({'gateway':{'subscriptions':[dict(s,device=MAC) for s in t['subscriptions']]}})
            self.assertEqual(len(cfg['gateway']['subscriptions']),len(t['subscriptions']))
        with self.assertRaises(Invalid):validate({'gateway':{'commands':[{'id':'shell','device':MAC,'kind':'shell'}]}})

    def test_commands_validate_encodings_and_ranges(self):
        c=configured_command()
        self.assertEqual(command_bytes(c,'ON'),'01')
        self.assertEqual(command_bytes(c,'OFF'),'00')
        with self.assertRaises(Invalid):command_bytes(c,'maybe')
        n=configured_command(domain='number',codec='u8',min=0,max=100,step=5)
        self.assertEqual(command_bytes(n,'25'),'19')
        for value in ('101','26','1.0','-1','NaN'):
            with self.assertRaises(Invalid):command_bytes(n,value)

    def test_reporting_preserves_buttons_and_connection_transitions(self):
        policy=ReportPolicy();cfg=DEFAULT['gateway']['policy']
        event={'type':'device','data':{'Address':MAC,'RSSI':-50,'Connected':False}}
        self.assertTrue(policy.accept(event,cfg));event['data']['RSSI']=-49
        self.assertFalse(policy.accept(event,cfg))
        event['data']['Connected']=True;self.assertTrue(policy.accept(event,cfg))
        press={'type':'measurement','data':{'address':MAC,'measurements':[{'domain':'event'}]}}
        self.assertTrue(policy.accept(press,cfg));self.assertTrue(policy.accept(press,cfg))

    def test_ha_discovery_sensor_binary_event_and_controls(self):
        cfg=copy.deepcopy(DEFAULT['gateway']);cfg['known_devices']=[{'device':MAC,'name':'Office','room':'Study'}]
        parsed=decode_payload(MAC,'4002ca091a013a01')
        for reading in parsed['measurements']:
            topic,doc,state=ha.discovery(cfg,MAC,reading)
            self.assertIn('/'+reading['domain']+'/',topic)
            self.assertEqual(doc['device']['name'],'Office')
            self.assertEqual(len(doc['availability']),2)
            if reading['domain']=='event':self.assertIn('press',doc['event_types'])
        topic,doc=ha.command_discovery(cfg,configured_command())
        self.assertTrue(topic.startswith('homeassistant/switch/'))
        self.assertFalse(doc['optimistic'])

    def test_proxy_wire_and_ad_payload_not_truncated(self):
        raw=message((1,0x5448e68f80a5),(2,True),(3,b'\0\1'))
        self.assertEqual(one(parse(raw),1),0x5448e68f80a5)
        for bad in (b'\x0a\x05ab',b'\x80'*11,b'\0'):
            with self.assertRaises(Invalid):parse(bad)
        data={'ServiceData':{BTHOME:'4002ca09'},'Name':'Long device name','ManufacturerData':{}}
        ad=advertisement(data)
        self.assertIn(bytes.fromhex('d2fc4002ca09'),ad)
        self.assertLessEqual(len(ad),62)

    def test_theengs_measurements_and_raw_packet_mapping(self):
        data={'Address':MAC,'ServiceData':{'0000181a-0000-1000-8000-00805f9b34fb':'abcd'},'ManufacturerData':{'76':'01'}}
        frames=list(packets(data));self.assertEqual(frames[0]['servicedatauuid'],'181a');self.assertEqual(frames[1]['manufacturerdata'],'4c0001')
        d=measurements({'data':{'model_id':'Test','brand':'Test','tempc':25,'hum':50,'batt':97,'power':True}},MAC)
        self.assertEqual(d['measurements'][-1]['domain'],'binary_sensor');self.assertEqual(d['measurements'][-1]['unit'],'')
        self.assertIsNone(measurements({'data':{'servicedata':'0011'}},MAC))

    def test_import_templates_is_data_only_and_normalized(self):
        records={'format':1,'kind':'unisoc-bluetooth-templates','templates':[{'id':'my-lamp','name':'Lamp',
            'commands':[{'id':'switch','kind':'write','domain':'switch','service':'180f','characteristic':'2a19','on':'01','off':'00'}]}]}
        result=Gateway.validate_templates(records)
        self.assertEqual(result[0]['commands'][0]['service'],BATTERY)
        self.assertNotIn('device',result[0]['commands'][0])
        for extra in ({'script':'echo unsafe'},{'decoder':'../bad'},{'id':'bthome-v2'}):
            bad=copy.deepcopy(records);bad['templates'][0].update(extra)
            with self.assertRaises(Invalid):Gateway.validate_templates(bad)

class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.events=[]
        self.gateway=Gateway(str(Path(self.tmp.name)/'config.json'))
    async def asyncTearDown(self):
        self.gateway.catalog.close();await self.gateway.recovery.close();await self.gateway.forward.close()
        await self.gateway.decoder.close()
        if self.gateway.proxy:await self.gateway.proxy.close()
        self.tmp.cleanup()

    async def test_boot_requires_autostart_even_with_saved_gateway(self):
        g=self.gateway;g.config=validate({'gateway':{'enabled':True}})
        g.bluez.objects={AD:{ADAPTER:{'Powered':False}}};g.bluez.select('hci0')
        # BlueZ reports readiness from start; it must not race a second activation.
        g.bluez.start=AsyncMock(side_effect=g.ready);g.start_configured=AsyncMock()
        await g.initialize()
        g.start_configured.assert_not_awaited();self.assertFalse(g.desired_on)
        self.assertFalse(g.recovery.enabled);self.assertEqual(g.forward.tasks,[])
        g.config['autostart']=True;g.starting=True
        await g.initialize()
        g.start_configured.assert_awaited_once();self.assertTrue(g.desired_on)

    async def test_service_restart_preserves_manual_power(self):
        g=self.gateway;g.bluez.objects={AD:{ADAPTER:{'Powered':True}}};g.bluez.select('hci0')
        g.bluez.start=AsyncMock(side_effect=g.ready);g.start_configured=AsyncMock()
        await g.initialize()
        g.start_configured.assert_awaited_once();self.assertTrue(g.desired_on)

    async def test_alias_can_be_saved_offline_and_retried_without_power(self):
        g=self.gateway;g.start_configured=AsyncMock();g.bluez.power=AsyncMock()
        await g.reconfigure({'alias':'My home 蓝牙','autostart':True})
        self.assertEqual(load_config(g.config_path)['alias'],'My home 蓝牙')
        g.start_configured.assert_not_awaited();g.bluez.power.assert_not_awaited()
        g.bluez.objects={AD:{ADAPTER:{'Powered':False}}};g.bluez.select('hci0')
        g.bluez.set_property=AsyncMock(side_effect=Invalid('temporary_name_error'))
        with self.assertRaisesRegex(Invalid,'temporary_name_error'):
            await g.reconfigure({'alias':'New name'})
        self.assertEqual(load_config(g.config_path)['alias'],'New name')
        g.bluez.set_property=AsyncMock()
        await g.reconfigure({'alias':'New name'})
        g.bluez.set_property.assert_awaited_once_with(AD,ADAPTER,'Alias','s','New name')
        g.bluez.power.assert_not_awaited();g.start_configured.assert_not_awaited()
        await g.reconfigure({'alias':''})
        self.assertEqual(g.bluez.set_property.await_args.args[-1],'')

    async def test_power_off_suspends_gateway_without_erasing_saved_config(self):
        g=self.gateway;g.config=validate({'gateway':{'enabled':True}});g.desired_on=True
        g.bluez.power=AsyncMock();g.stop_scan_policy=AsyncMock();g.serial.close=AsyncMock()
        await g.dispatch({'action':'power','enabled':False})
        g.bluez.power.assert_awaited_once_with(False);self.assertFalse(g.desired_on)
        self.assertTrue(g.config['gateway']['enabled']);self.assertFalse(g.recovery.enabled)
        self.assertFalse(g.forward.config['enabled'])

    async def test_presence_expiry_and_encrypted_replay(self):
        c=Catalog(lambda k,d:self.events.append((k,d)))
        c.configure([{'device':MAC,'bindkey':KEY,'offline_after':1}])
        d={'Address':MAC,'ServiceData':{BTHOME:'41e445f3c9962b332211006c7c4519'}}
        self.assertIsNotNone(c.observe(d));self.assertIsNone(c.observe(d))
        self.assertNotIn(KEY,json.dumps(c.snapshot()))
        c.expire(MAC);self.assertFalse(c.snapshot()[0]['online']);c.close()
        c=Catalog(lambda *_:None)
        c.observe({'Address':MAC,'ServiceData':{BTHOME:'443a01'}})
        self.assertNotIn(MAC,c.timers);c.close()

    async def test_gatt_only_device_is_online_and_survives_idle_connection(self):
        c=Catalog(lambda k,d:self.events.append((k,d)))
        sub={'device':MAC,'service':BATTERY,'characteristic':LEVEL,'codec':'u8','name':'Battery','unit':'%'}
        c.gatt({'address':MAC,'service':BATTERY,'characteristic':LEVEL,'hex':'63'},[sub],connected=True)
        snapshot=c.snapshot()[0];self.assertTrue(snapshot['online']);self.assertEqual(snapshot['measurements'][0]['value'],99)
        c.expire(MAC);self.assertTrue(c.snapshot()[0]['online'])
        c.close()

    async def test_rssi_changes_do_not_replay_cached_button_advertisement(self):
        c=Catalog(lambda *_:None)
        data={'Address':MAC,'ServiceData':{BTHOME:'443a01'},'changed':['ServiceData']}
        self.assertEqual(c.observe(data)['measurements'][0]['value'],'press')
        data['changed']=['RSSI'];data['RSSI']=-51
        self.assertIsNone(c.observe(data));c.close()

    async def test_backup_restore_and_no_radio_action(self):
        self.gateway.config=validate({'gateway':{'known_devices':[{'device':MAC,'bindkey':KEY}]}})
        backup=(await self.gateway.dispatch({'action':'backup'}))['backup']
        self.assertIn(KEY,json.dumps(backup))
        self.gateway.bluez.set_property=AsyncMock()
        await self.gateway.dispatch({'action':'restore','backup':backup})
        self.gateway.bluez.set_property.assert_not_awaited()
        self.assertEqual(self.gateway.config['gateway']['known_devices'][0]['bindkey'],KEY)
        backup['config']['gateway']['known_devices'][0]['bindkey']=''
        await self.gateway.dispatch({'action':'restore','backup':backup})
        self.assertEqual(self.gateway.config['gateway']['known_devices'][0]['bindkey'],'')
        with self.assertRaises(Invalid):await self.gateway.dispatch({'action':'restore','backup':{'format':0}})

    async def test_mqtt_commands_idempotent_no_implicit_device_access(self):
        execute=AsyncMock(return_value={'acknowledged':True,'confirmed':False})
        f=Forwarder(execute);f.config=copy.deepcopy(DEFAULT['gateway']);cmd=configured_command()
        client=type('Client',(),{'publish':AsyncMock()})()
        packet=json.dumps({'id':'one','value':'ON'}).encode()
        await f.command(client,cmd,packet);await f.command(client,cmd,packet)
        execute.assert_awaited_once()
        self.assertEqual(client.publish.await_count,2)
        self.assertFalse(any(c.args[0].endswith('/state') for c in client.publish.await_args_list))
        f.config['devices']=['02:00:00:00:00:01']
        await f.command(client,cmd,b'{"id":"two","value":"ON"}')
        execute.assert_awaited_once()
        self.assertIn('command_device_not_allowed',client.publish.await_args.args[1])

    async def test_retained_mqtt_commands_never_run_and_birth_reannounces(self):
        cfg=copy.deepcopy(DEFAULT['gateway']);cfg['commands']=[configured_command()]
        f=Forwarder(AsyncMock());f.config=cfg;f.announce=AsyncMock()
        class Messages:
            def __init__(self):self.rows=iter([
                type('M',(),{'topic':'unisoc/bluetooth/commands/lamp/set','payload':b'ON','retain':True})(),
                type('M',(),{'topic':'homeassistant/status','payload':b'online','retain':False})()])
            def __aiter__(self):return self
            async def __anext__(self):
                try:return next(self.rows)
                except StopIteration:raise StopAsyncIteration
        client=type('C',(),{'messages':Messages()})()
        await f.receive_commands(client)
        f.on_command.assert_not_awaited();f.announce.assert_awaited_once_with(client)

    async def test_command_readback_confirms_actual_state(self):
        blue=Bluez(lambda *_:None);blue.objects={AD:{ADAPTER:{}},DEV:{DEVICE:{'Address':MAC}},SVC:{SERVICE:{'UUID':BATTERY,'Device':DEV}},CHR:{CHAR:{'UUID':LEVEL,'Service':SVC,'Flags':['read','write']}}};blue.select('hci0')
        blue.write=AsyncMock();blue.read=AsyncMock(return_value={'hex':'00'})
        result=await execute(blue,None,configured_command(),'ON')
        self.assertFalse(result['confirmed']);self.assertEqual(result['value'],'OFF')
        blue.read.return_value={'hex':'01'}
        self.assertTrue((await execute(blue,None,configured_command(),'ON'))['confirmed'])

    async def test_recovery_stops_after_explicit_user_disconnect(self):
        blue=Bluez(lambda *_:None);blue.objects={AD:{ADAPTER:{'Powered':True}},DEV:{DEVICE:{'Address':MAC,'Connected':False}}};blue.select('hci0');blue.device_action=AsyncMock()
        restore=AsyncMock();r=Recovery(blue,restore,lambda *_:None)
        cfg=copy.deepcopy(DEFAULT['gateway']);cfg.update(enabled=True,known_devices=[{'device':MAC,'auto_connect':True}])
        await r.configure(cfg);r.changed(MAC);await asyncio.sleep(.01)
        blue.device_action.assert_awaited_once_with(MAC,'connect');restore.assert_awaited_once()
        r.suspend(MAC);r.changed(MAC);await asyncio.sleep(.01)
        blue.device_action.assert_awaited_once();await r.close()

    async def test_encrypted_native_proxy_handshake_and_readonly_advertisements(self):
        from noise.connection import NoiseConnection
        g=self.gateway;g.bluez.objects={AD:{ADAPTER:{'Address':MAC,'Powered':True}},DEV:{DEVICE:{'Address':MAC,'Connected':False}}};g.bluez.select('hci0')
        g.bluez.device_action=AsyncMock();p=Proxy(g);g.proxy=p
        cfg=copy.deepcopy(DEFAULT['gateway']['proxy']);cfg.update(enabled=True,port=0,psk=base64.b64encode(b'z'*32).decode())
        await p.configure(cfg);port=p.server.sockets[0].getsockname()[1]
        reader,writer=await asyncio.open_connection('127.0.0.1',port)
        async def raw(data):writer.write(b'\1'+len(data).to_bytes(2,'big')+data);await writer.drain()
        async def frame():
            h=await asyncio.wait_for(reader.readexactly(3),2);return await reader.readexactly(int.from_bytes(h[1:],'big'))
        n=NoiseConnection.from_name(b'Noise_NNpsk0_25519_ChaChaPoly_SHA256');n.set_as_initiator();n.set_psks(b'z'*32);n.set_prologue(b'NoiseAPIInit\0\0');n.start_handshake()
        await raw(b'');self.assertTrue((await frame()).startswith(b'\1unisoc-bluetooth\0'))
        await raw(b'\0'+n.write_message());reply=await frame();n.read_message(reply[1:])
        async def send(kind,data=b''):await raw(n.encrypt(struct.pack('>HH',kind,len(data))+data))
        async def receive():
            result=n.decrypt(await frame());return int.from_bytes(result[:2],'big'),parse(result[4:])
        await send(1);kind,result=await receive();self.assertEqual(kind,2);self.assertEqual(one(result,1),1)
        await send(9);kind,result=await receive();self.assertEqual(kind,10);self.assertEqual(one(result,15),33)
        await send(66,field(1,1));await asyncio.sleep(.01)
        p.event({'type':'device','data':{'Address':MAC,'RSSI':-42,'ServiceData':{BTHOME:'4002ca09'}}})
        kind,result=await receive();self.assertEqual(kind,93);self.assertIn(bytes.fromhex('d2fc4002ca09'),one(parse(one(result,1)),4))
        await send(68,message((1,int(MAC.replace(':',''),16)),(2,4)))
        kind,result=await receive();self.assertEqual(kind,69);self.assertFalse(one(result,2));g.bluez.device_action.assert_not_awaited()
        writer.close();await writer.wait_closed();await p.close()

    async def test_proxy_rejects_plaintext_and_wrong_key(self):
        g=self.gateway;g.bluez.objects={AD:{ADAPTER:{'Address':MAC,'Powered':True}}};g.bluez.select('hci0')
        p=Proxy(g);g.proxy=p;cfg=copy.deepcopy(DEFAULT['gateway']['proxy']);cfg.update(enabled=True,port=0,psk=base64.b64encode(b'z'*32).decode())
        await p.configure(cfg);port=p.server.sockets[0].getsockname()[1]
        r,w=await asyncio.open_connection('127.0.0.1',port);w.write(b'\0\0\0');await w.drain()
        self.assertEqual(await asyncio.wait_for(r.read(),2),b'');self.assertEqual(p.rejected,1)
        w.close();await w.wait_closed()
        from noise.connection import NoiseConnection
        r,w=await asyncio.open_connection('127.0.0.1',port)
        w.write(b'\1\0\0');await w.drain();h=await r.readexactly(3);await r.readexactly(int.from_bytes(h[1:],'big'))
        n=NoiseConnection.from_name(b'Noise_NNpsk0_25519_ChaChaPoly_SHA256');n.set_as_initiator();n.set_psks(b'y'*32);n.set_prologue(b'NoiseAPIInit\0\0');n.start_handshake()
        handshake=b'\0'+n.write_message();w.write(b'\1'+len(handshake).to_bytes(2,'big')+handshake);await w.drain()
        self.assertEqual(await asyncio.wait_for(r.read(),2),b'');self.assertEqual(p.rejected,2)
        w.close();await w.wait_closed();await p.close()

if __name__=='__main__':unittest.main(verbosity=2)
