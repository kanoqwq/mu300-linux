#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Private D-Bus and local sockets only. Never opens HCI, UART or AT devices."""
import asyncio
import copy
import glob
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

TOP = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
BASE = TOP/'openwrt/overlay/usr/lib/unisoc-bluetooth'
sys.path[:0] = [str(BASE)] + glob.glob(str(BASE/'vendor/*.whl'))
from core import DEFAULT, Invalid, address, atomic_json, decode, hex_bytes, load_config, public_config, uuid, validate
from bluez import ADAPTER, AGENT, BLUEZ, CHAR, DEVICE, MANAGER, PROPS, SERVICE, Bluez, BluezError
from daemon import Gateway
from forward import Forwarder
from serial_bridge import SerialBridge
from hci import H4
from transport import unblock_adapter
from stream_auth import session
from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus

MAC = '02:11:22:33:44:55'
AD = '/org/bluez/hci0'
DEV = AD+'/dev_'+MAC.replace(':','_')
SVC = DEV+'/service0001'
CHR = SVC+'/char0002'

class ConfigTests(unittest.TestCase):
    def test_defaults_and_secret_preservation(self):
        conf = copy.deepcopy(DEFAULT)
        conf['gateway']['mqtt']['password'] = 'secret'
        conf['gateway']['http']['token'] = 'bearer'
        public = public_config(conf)
        self.assertNotIn('secret', json.dumps(public))
        self.assertNotIn('bearer', json.dumps(public))
        self.assertTrue(public['gateway']['mqtt']['password_set'])
        new = validate({'gateway': {'mqtt': {'password':''}}}, conf)
        self.assertEqual(new['gateway']['mqtt']['password'], 'secret')
        new = validate({'gateway': {'mqtt': {'clear_secret':True}}}, conf)
        self.assertEqual(new['gateway']['mqtt']['password'], '')

    def test_atomic_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            atomic_json(path, DEFAULT)
            self.assertEqual(load_config(path), DEFAULT)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('{')
            with self.assertRaises(ValueError):
                load_config(path)

    def test_sdp_channel_discovery(self):
        raw=b'''Browsing device...\n<record><attribute id="0x0100"><text value="Serial port"/></attribute><attribute id="0x0004"><sequence><sequence><uuid value="0x0003"/><uint8 value="0x02"/></sequence></sequence></attribute></record>'''
        self.assertEqual(SerialBridge.parse_services(raw),[{'name':'Serial port','channel':2}])

    def test_only_selected_hci_soft_block_is_released(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for hci in ('hci0','hci1'):
                entry=root/hci/'rfkill2';entry.mkdir(parents=True)
                for key,value in (('type','bluetooth'),('soft','1'),('hard','0')):(entry/key).write_text(value+'\n')
            unblock_adapter('hci0',root)
            self.assertEqual((root/'hci0/rfkill2/soft').read_text(),'0\n')
            self.assertEqual((root/'hci1/rfkill2/soft').read_text(),'1\n')
            (root/'hci1/rfkill2/hard').write_text('1\n')
            with self.assertRaisesRegex(Invalid,'hard_blocked'):unblock_adapter('hci1',root)
            self.assertEqual((root/'hci1/rfkill2/soft').read_text(),'1\n')

    def test_h4_vendor_command_framing_and_rejection(self):
        import pty
        import threading
        master,slave=pty.openpty()
        received=[]
        def controller():
            for status in (0,0x0c):
                packet=bytearray()
                while len(packet)<4:packet.extend(os.read(master,4-len(packet)))
                while len(packet)<4+packet[3]:packet.extend(os.read(master,4+packet[3]-len(packet)))
                received.append(bytes(packet))
                os.write(master,b'\x04\x0e\x04\x01'+packet[1:3]+bytes([status]))
        transport=H4(os.ttyname(slave),1500000)
        worker=threading.Thread(target=controller,daemon=True);worker.start()
        try:
            transport.command(0xfc70,b'\x55\x44\x33\x22\x11\x02')
            with self.assertRaisesRegex(Invalid,'rejected'):transport.command(0xfc70,b'\x00'*6)
            worker.join(2)
            self.assertEqual(received[0],b'\x01\x70\xfc\x06\x55\x44\x33\x22\x11\x02')
            self.assertFalse(worker.is_alive())
        finally:transport.close();os.close(master);os.close(slave)

    def test_retired_cgi_returns_promptly_without_an_event_stream(self):
        path=TOP/'openwrt/overlay/www/cgi-bin/unisoc-bluetooth-events'
        for cookie in ('','sysauth_http=not_a_valid_session'):
            result=subprocess.run(['sh',str(path)],env=dict(os.environ,HTTP_COOKIE=cookie),capture_output=True,text=True,timeout=3,check=True)
            self.assertIn('410 Gone',result.stdout)
            self.assertNotIn('text/event-stream',result.stdout)

    def test_sse_accepts_session_body_without_out_of_path_cookie(self):
        import io
        sid='1'*32;data=json.dumps({'session':sid}).encode()
        env={'REQUEST_METHOD':'POST','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(data))}
        self.assertEqual(session(env,io.BytesIO(data)),sid)
        with self.assertRaises(ValueError):session(dict(env,CONTENT_TYPE='text/plain'),io.BytesIO(data))
        with self.assertRaises(ValueError):session(dict(env,CONTENT_LENGTH='2048'),io.BytesIO(data))
        with self.assertRaises(ValueError):session(dict(env,CONTENT_LENGTH='2'),io.BytesIO(b'{}'))

    def test_validation(self):
        for item in ({'adapter':'hci0; reboot'}, {'autostart':1}, {'gateway':{'enabled':'true'}},
                     {'gateway':{'http':{'url':'file:///etc/shadow'}}},
                     {'gateway':{'http':{'url':'https://user:pass@localhost/'}}},
                     {'gateway':{'mqtt':{'topic':'bad/+'}}},
                     {'gateway':{'mqtt':{'host':'broker\nreboot'}}},
                     {'gateway':{'serial':[{'device':MAC,'bind':'0.0.0.0','port':1025}]}},
                     {'gateway':{'serial':[{'device':MAC,'bind':'8.8.8.8','port':1025}]}},
                     {'gateway':{'subscriptions':[{'device':MAC,'service':'180f','characteristic':'2a19','codec':'eval'}]}}):
            with self.subTest(item=item), self.assertRaises(Invalid):
                validate(item)
        self.assertEqual(uuid('180f'), '0000180f-0000-1000-8000-00805f9b34fb')
        self.assertEqual(decode(bytes.fromhex('80ff'),'i16le'), -128)
        with self.assertRaises(Invalid): decode(b'','u8')
        with self.assertRaises(Invalid): hex_bytes('gg')
        with self.assertRaises(Invalid): address('00:11')

    def test_stage_and_permissions(self):
        spec=importlib.util.spec_from_file_location('stage_bt',TOP/'tools/stage-bluetooth.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('usr/bin/bluetoothd','usr/bin/rfcomm','usr/bin/btattach','usr/bin/sdptool','usr/bin/python3',
                         'usr/bin/ucode','usr/lib/ucode/socket.so','usr/lib/uhttpd_ubus.so'):
                path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.touch()
            for name,number in (('dbus',60),('bluetoothd',62)):
                path=root/'etc/init.d'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('START='+str(number)+'\n')
            path=root/'etc/bluetooth/main.conf';path.parent.mkdir(parents=True);path.write_text('[Policy]\nAutoEnable=true\n')
            import runpy,hashlib
            artifacts=root/'decoder-input';artifacts.mkdir()
            binary=b'\x7fELF\x02\x01'+b'\0'*12+(183).to_bytes(2,'little')
            (artifacts/'unisoc-ble-decode').write_bytes(binary);(artifacts/'SOURCE.tar.gz').write_bytes(b'test-source')
            fingerprint=runpy.run_path(str(TOP/'tools/ble-decoder/build.py'))['fingerprint']()
            (artifacts/'BUILD.json').write_text(json.dumps({'source_fingerprint':fingerprint,'sha256':hashlib.sha256(binary).hexdigest(),
                'source_archive_sha256':hashlib.sha256(b'test-source').hexdigest()}))
            module.stage(root,'mu300-pcie',decoder_artifacts=artifacts)
            module.stage(root,'mu300-pcie',decoder_artifacts=artifacts)
            self.assertEqual((root/'etc/rc.d/S65unisoc-bluetooth').readlink(),Path('../init.d/unisoc-bluetooth'))
            self.assertNotIn('true',path.read_text())
            self.assertEqual((root/'usr/libexec/rpcd/unisoc-bluetooth').stat().st_mode&0o777,0o755)
            self.assertIn('/etc/bluetooth/',(root/'lib/upgrade/keep.d/unisoc-bluetooth').read_text())
            self.assertTrue((root/'usr/lib/unisoc-bluetooth/events.uc').is_file())
            self.assertIn('events.uc',(root/'etc/init.d/unisoc-bluetooth').read_text())
            self.assertTrue((root/'usr/bin/unisoc-ble-decode').is_file())
            self.assertTrue((root/'usr/share/unisoc-bluetooth/decoder-source.tar.gz').is_file())
        acl=json.loads((TOP/'openwrt/overlay/usr/share/rpcd/acl.d/unisoc-bluetooth.json').read_text())['luci-app-unisoc-bluetooth']
        self.assertEqual(acl['read']['ubus']['unisoc-bluetooth'],['status','gatt_list','templates','advertising_list'])
        self.assertNotIn('backup',acl['read']['ubus']['unisoc-bluetooth'])
        self.assertIn('backup',acl['write']['ubus']['unisoc-bluetooth'])
        for method in ('advertising_save','advertising_delete','advertising_start','advertising_stop'):
            self.assertIn(method,acl['write']['ubus']['unisoc-bluetooth'])
            self.assertNotIn(method,acl['read']['ubus']['unisoc-bluetooth'])
        self.assertNotIn('file',acl['read'])

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.events=[]

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_power_status_tracks_initialization_and_actual_controller_state(self):
        gateway=Gateway(str(Path(self.tmp.name)/'conf'))
        self.assertEqual(gateway.status()['power_state'],'off')
        # A failed pairing attempt must not turn a disabled radio into a power failure.
        gateway.jobs={'1':{'id':'1','state':'failed','error':'org.bluez.Error.ConnectionAttemptFailed'}}
        self.assertEqual(gateway.status()['power_state'],'off')
        available=asyncio.Event()
        async def configure():
            await available.wait()
            gateway.bluez.objects={AD:{ADAPTER:{'Powered':True}}}
            gateway.bluez.select('hci0')
        gateway.transport=AsyncMock(side_effect=configure)
        gateway.activate=AsyncMock()
        task=asyncio.create_task(gateway.power_on())
        await asyncio.sleep(0)
        self.assertEqual(gateway.status()['power_state'],'starting')
        self.assertEqual(gateway.events[-1]['type'],'status')
        available.set();await task
        self.assertEqual(gateway.status()['power_state'],'on')
        gateway.bluez.objects={}
        self.assertEqual(gateway.status()['power_state'],'unavailable')
        gateway.transport=AsyncMock(side_effect=Invalid('bluetooth_transport_start_failed'))
        with self.assertRaisesRegex(Invalid,'bluetooth_transport_start_failed'):
            await gateway.power_on()
        self.assertEqual(gateway.status()['power_state'],'failed')
        # Explicit power-off clears the failed activation state, without enabling anything.
        gateway.bluez.power=AsyncMock()
        result=await gateway.dispatch({'action':'power','enabled':False})
        self.assertEqual(result['power_state'],'off')
        gateway.bluez.power.assert_awaited_once_with(False)
        self.assertFalse(gateway.desired_on)
        self.assertFalse(load_config(gateway.config_path)['autostart'])
        gateway.catalog.close()

    async def test_autostart_failure_is_reported_without_changing_the_preference(self):
        gateway=Gateway(str(Path(self.tmp.name)/'conf'))
        gateway.config['autostart']=True
        gateway.bluez.start=AsyncMock()
        gateway.transport=AsyncMock(side_effect=Invalid('bluetooth_transport_start_failed'))
        await gateway.initialize()
        self.assertEqual(gateway.status()['power_state'],'failed')
        self.assertEqual(gateway.status()['error'],'bluetooth_activation_failed')
        self.assertTrue(gateway.config['autostart'])
        # If only a gateway feature fails but the adapter is powered, report its real state.
        gateway.bluez.objects={AD:{ADAPTER:{'Powered':True}}};gateway.bluez.select('hci0')
        self.assertEqual(gateway.status()['power_state'],'on')
        gateway.catalog.close()

    async def test_advertisement_fields_refresh_without_commands_or_stale_timestamps(self):
        blue=Bluez(lambda k,d:self.events.append((k,d)))
        blue.objects={AD:{ADAPTER:{}},DEV:{DEVICE:{'Address':MAC,'AddressType':'random'}}};blue.select('hci0')
        blue.call=AsyncMock()
        change={'ManufacturerData':Variant('a{qv}',{911:Variant('ay',b'\x01\x02\xff')}),
                'ServiceData':Variant('a{sv}',{uuid('180f'):Variant('ay',b'\x64')}),
                'AdvertisingData':Variant('a{yv}',{0x26:Variant('ay',b'\x01')}),
                'AdvertisingFlags':Variant('ay',b'\x06'),'TxPower':Variant('n',0),'RSSI':Variant('n',-52)}
        blue.signal(Message.new_signal(DEV,PROPS,'PropertiesChanged','sa{sv}as',[DEVICE,change,[]]))
        d=blue.snapshot()['devices'][0]
        self.assertEqual(d['ManufacturerData'],{'911':'0102ff'});self.assertEqual(d['AdvertisingData'],{'38':'01'})
        self.assertEqual(d['AdvertisingFlags'],'06');self.assertEqual(d['TxPower'],0)
        self.assertGreater(d['LastAdvertisement'],0)
        stamp=d['LastAdvertisement'];blue._changed(DEV,{'Connected'})
        self.assertEqual(blue.snapshot()['devices'][0]['LastAdvertisement'],stamp)
        blue.call.assert_not_awaited()
        blue.signal(Message.new_signal('/',MANAGER,'InterfacesRemoved','oas',[DEV,[DEVICE]]))
        self.assertNotIn(DEV,blue.last_advertisement)

    async def test_event_cursor_restart_and_secret_filter(self):
        gateway=Gateway(str(Path(self.tmp.name)/'conf'))
        gateway.emit('device',{'Address':MAC,'RSSI':-30})
        reply=await gateway.dispatch({'action':'events','after':999,'event_id':'old','wait':0})
        self.assertEqual(len([e for e in reply['events'] if e['type']=='device']),1)
        self.assertEqual(reply['event_id'],gateway.event_id)
        cursor=gateway.sequence
        waiter=asyncio.create_task(gateway.dispatch({'action':'events','after':cursor,'event_id':gateway.event_id,'wait':1}))
        await asyncio.sleep(.01);gateway.emit('device',{'Address':MAC,'RSSI':-35})
        self.assertEqual((await waiter)['sequence'],cursor+1)
        gateway.catalog.close()
        gateway.config['gateway']['enabled']=True
        with self.assertRaises(Invalid): await gateway.dispatch({'action':'power','enabled':False})

    async def test_manual_mode_alias_save_does_not_enable_radio(self):
        gateway=Gateway(str(Path(self.tmp.name)/'conf'))
        gateway.bluez.objects={AD:{ADAPTER:{'Address':MAC,'Powered':False}}}
        gateway.bluez.select('hci0')
        gateway.bluez.set_property=AsyncMock()
        gateway.bluez.power=AsyncMock()
        result=await gateway.reconfigure({'alias':'Living room gateway'})
        self.assertTrue(result['ok'])
        gateway.bluez.set_property.assert_awaited_once_with(AD,ADAPTER,'Alias','s','Living room gateway')
        gateway.bluez.power.assert_not_awaited()
        self.assertFalse(load_config(gateway.config_path)['autostart'])

    async def test_existing_adapter_does_not_start_another_transport(self):
        gateway=Gateway(str(Path(self.tmp.name)/'conf'))
        gateway.bluez.objects={AD:{ADAPTER:{'Address':MAC,'Powered':False}}}
        with patch('asyncio.create_subprocess_exec',new_callable=AsyncMock) as process:
            await gateway.transport()
            process.assert_not_awaited()
        self.assertEqual(gateway.bluez.adapter,'hci0')

    async def test_power_busy_requires_actual_state_confirmation(self):
        blue=Bluez(lambda *_:None)
        blue.objects={AD:{ADAPTER:{'Powered':False}}};blue.select('hci0')
        blue.set_property=AsyncMock(side_effect=BluezError(BLUEZ+'.Error.Busy'))
        pending=asyncio.create_task(blue.power(True,timeout=.2))
        await asyncio.sleep(.01)
        self.assertFalse(pending.done())
        blue.signal(Message.new_signal(AD,PROPS,'PropertiesChanged','sa{sv}as',[ADAPTER,{'Powered':Variant('b',True)},[]]))
        await pending
        blue.set_property.assert_awaited_once()
        blue.objects[AD][ADAPTER]['Powered']=False
        with self.assertRaisesRegex(Invalid,'transition_timed_out'):
            await blue.power(True,timeout=.01)

    async def test_bounded_independent_forwarding(self):
        forward=Forwarder();forward.config=copy.deepcopy(DEFAULT['gateway']);forward.config['enabled']=True
        forward.config['policy'].update(min_interval=0,change_only=False)
        forward.queues={'http':asyncio.Queue(128),'mqtt':asyncio.Queue(128)}
        for i in range(150):forward.push({'type':'device','data':{'Address':MAC,'RSSI':-i}})
        self.assertEqual(forward.state['dropped'],44)
        self.assertEqual(forward.queues['http'].qsize(),128)
        forward.config['devices']=['02:00:00:00:00:01']
        forward.push({'type':'device','data':{'Address':MAC}})
        self.assertEqual(forward.state['dropped'],44)
        self.assertEqual(forward.sensor({'type':'device','data':{'Address':MAC,'RSSI':-42}})[2],-42)

    async def test_mqtt_and_http_real_local_delivery(self):
        received=[]
        async def broker(reader, writer):
            try:
                while True:
                    kind=(await reader.readexactly(1))[0]
                    size,shift=0,0
                    while True:
                        byte=(await reader.readexactly(1))[0];size+=(byte&127)<<shift;shift+=7
                        if not byte&128:break
                    payload=await reader.readexactly(size)
                    if kind>>4==1:writer.write(b'\x20\x02\x00\x00');await writer.drain()
                    elif kind>>4==8:writer.write(b'\x90\x03'+payload[:2]+b'\x00');await writer.drain()
                    elif kind>>4==3:
                        length=int.from_bytes(payload[:2],'big')
                        body=payload[2+length:].decode()
                        try:body=json.loads(body)
                        except ValueError:pass
                        received.append(('mqtt',payload[2:2+length].decode(),body))
                    elif kind>>4==12:writer.write(b'\xd0\x00');await writer.drain()
                    elif kind>>4==14:break
            except asyncio.IncompleteReadError:pass
            finally:writer.close();await writer.wait_closed()
        async def webhook(reader,writer):
            headers=await reader.readuntil(b'\r\n\r\n')
            length=int(next(x.split(b':',1)[1] for x in headers.split(b'\r\n') if x.lower().startswith(b'content-length:')))
            received.append(('http',headers,json.loads(await reader.readexactly(length))))
            writer.write(b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await writer.drain();writer.close();await writer.wait_closed()
        mqtt=await asyncio.start_server(broker,'127.0.0.1',0)
        http=await asyncio.start_server(webhook,'127.0.0.1',0)
        config=copy.deepcopy(DEFAULT['gateway']);config['enabled']=True
        config['mqtt'].update(enabled=True,host='127.0.0.1',port=mqtt.sockets[0].getsockname()[1],ha_discovery=True)
        config['http'].update(enabled=True,url='http://127.0.0.1:'+str(http.sockets[0].getsockname()[1])+'/',token='local-test-token')
        forward=Forwarder()
        try:
            await forward.configure(config)
            forward.push({'seq':1,'time':1,'type':'device','data':{'Address':MAC,'RSSI':-40}})
            async def deliveries():
                while len(received)<4:await asyncio.sleep(.01)
            await asyncio.wait_for(deliveries(),4)
            self.assertTrue(any(x[0]=='http' and b'Authorization: Bearer local-test-token' in x[1] for x in received))
            self.assertTrue(any(x[0]=='mqtt' and x[1].endswith('/events') and x[2]['data']['RSSI']==-40 for x in received))
            self.assertTrue(any(x[0]=='mqtt' and x[1].startswith('homeassistant/sensor/') for x in received))
            self.assertEqual(forward.state['errors'],0)
        finally:
            await forward.close();mqtt.close();http.close();await mqtt.wait_closed();await http.wait_closed()

    async def test_serial_raw_bytes_use_event_reader(self):
        import pty
        import tty
        master,slave=pty.openpty();tty.setraw(slave);os.set_blocking(slave,False);os.set_blocking(master,False)
        serial=SerialBridge(lambda k,v:self.events.append((k,v)))
        link={'state':'connected','fd':slave,'client':None,'write_lock':asyncio.Lock()}
        serial.links[MAC]=link
        loop=asyncio.get_running_loop();loop.add_reader(slave,serial.received,MAC,link)
        try:
            os.write(master,b'\x00\xffabc')
            for _ in range(20):
                if self.events:break
                await asyncio.sleep(.01)
            self.assertEqual(self.events[0],('serial',{'address':MAC,'hex':'00ff616263'}))
            await serial.write(MAC,'1234ff')
            await asyncio.sleep(.01)
            self.assertEqual(os.read(master,512),b'\x12\x34\xff')
            with self.assertRaises(Invalid):await serial.write('02:00:00:00:00:01','12')
        finally:
            loop.remove_reader(slave);os.close(master);os.close(slave)

    async def test_private_bluez_bus_pair_gatt_and_scan_lifetime(self):
        process=await asyncio.create_subprocess_exec('dbus-daemon','--session','--nofork','--print-address=1',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        bus_address=(await process.stdout.readline()).decode().strip()
        old=os.environ.get('DBUS_SYSTEM_BUS_ADDRESS');os.environ['DBUS_SYSTEM_BUS_ADDRESS']=bus_address
        fake=await MessageBus(bus_address=bus_address).connect()
        await fake.request_name(BLUEZ)
        objects={AD:{ADAPTER:{'Address':Variant('s',MAC),'Powered':Variant('b',True)}},
                 DEV:{DEVICE:{'Address':Variant('s',MAC),'Connected':Variant('b',True),'ServicesResolved':Variant('b',True)}},
                 SVC:{SERVICE:{'UUID':Variant('s',uuid('180f')),'Device':Variant('o',DEV)}},
                 CHR:{CHAR:{'UUID':Variant('s',uuid('2a19')),'Service':Variant('o',SVC),'Flags':Variant('as',['read','write','notify'])}}}
        calls=[];agent={}
        async def pair(message):
            answer=await fake.call(Message(destination=agent['sender'],path=AGENT,interface=BLUEZ+'.Agent1',member='RequestConfirmation',signature='ou',body=[DEV,123456]))
            fake.send(Message.new_method_return(message) if answer.message_type!=MessageType.ERROR else Message.new_error(message,answer.error_name,'rejected'))
        def handler(message):
            if message.message_type!=MessageType.METHOD_CALL:return
            calls.append((message.member,message.body))
            if message.member=='GetManagedObjects':return Message.new_method_return(message,'a{oa{sa{sv}}}',[objects])
            if message.member=='RegisterAgent':agent['sender']=message.sender
            if message.member=='Pair':asyncio.create_task(pair(message));return True
            if message.member=='ReadValue':return Message.new_method_return(message,'ay',[b'\x63'])
            return Message.new_method_return(message)
        fake.add_message_handler(handler)
        blue=Bluez(lambda k,v:self.events.append((k,v)))
        try:
            await blue.start();blue.select('hci0');await blue.refresh()
            self.assertEqual(sum(c[0]=='RegisterAgent' for c in calls),1)
            self.assertEqual((await blue.read(MAC,'180f','2a19'))['hex'],'63')
            await blue.write(MAC,'180f','2a19','01')
            await blue.notify(MAC,'180f','2a19',True);await blue.notify(MAC,'180f','2a19',True)
            self.assertEqual(sum(c[0]=='StartNotify' for c in calls),1)
            fake.send(Message.new_signal(CHR,PROPS,'PropertiesChanged','sa{sv}as',[CHAR,{'Value':Variant('ay',b'\x64')},[]]))
            await asyncio.sleep(.02)
            self.assertIn(('gatt',{'address':MAC,'path':CHR,'service':uuid('180f'),'characteristic':uuid('2a19'),'hex':'64'}),self.events)
            pairing=asyncio.create_task(blue.device_action(MAC,'pair'))
            for _ in range(100):
                if blue.pending:break
                await asyncio.sleep(.01)
            self.assertTrue(blue.pending)
            gateway=Gateway(str(Path(self.tmp.name)/'pair-conf'));gateway.bluez=blue
            token=next(iter(blue.pending))
            self.assertEqual(await gateway.dispatch({'action':'pair_reply','id':token,'accept':True}),{'ok':True})
            await pairing
            self.assertFalse(blue.pending)
            with self.assertRaisesRegex(Invalid,'pairing_request_expired'):
                await gateway.dispatch({'action':'pair_reply','id':token,'accept':True})
            await blue.scan('le',0)
            generation=blue.scan_generation
            await blue.scan('bredr',0)
            await blue._stop_scan_later(AD,0,generation)
            self.assertIn(AD,blue.discovery)
            await blue.stop_scan()
            count=len(calls);await asyncio.sleep(.05);self.assertEqual(len(calls),count)
        finally:
            if blue.bus:blue.bus.disconnect()
            fake.disconnect();process.terminate();await process.wait()
            if old is None:os.environ.pop('DBUS_SYSTEM_BUS_ADDRESS',None)
            else:os.environ['DBUS_SYSTEM_BUS_ADDRESS']=old

if __name__=='__main__':unittest.main(verbosity=2)
