# SPDX-License-Identifier: MIT
"""Owned RFCOMM processes, optional explicitly bound LAN TCP endpoints."""
import asyncio
import os
import tty
from pathlib import Path
from xml.etree import ElementTree

from core import Invalid, address, hex_bytes

class SerialBridge:
    def __init__(self, emit):
        self.emit = emit
        self.links = {}
        self.tasks = set()

    def snapshot(self):
        return [{'device': mac, 'state': link['state'], 'channel': link['config']['channel'],
                 'bind': link['config']['bind'], 'port': link['config']['port']}
                for mac, link in self.links.items()]

    async def services(self, mac):
        process = await asyncio.create_subprocess_exec('/usr/bin/sdptool', 'browse', '--xml', address(mac),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 12)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise Invalid('sdp_query_timed_out')
        if process.returncode:
            raise Invalid('sdp_query_failed')
        try:
            return self.parse_services(output)
        except (ElementTree.ParseError, ValueError):
            raise Invalid('sdp_invalid_response')

    @staticmethod
    def parse_services(output):
        # sdptool emits adjacent <record> documents; the prefix may contain a status line.
        import re
        result=[]
        for data in re.findall(rb'<record>.*?</record>', output, re.S):
            record=ElementTree.fromstring(data)
            name=record.find("attribute[@id='0x0100']/text")
            protocol=record.find("attribute[@id='0x0004']")
            if protocol is None:
                continue
            for sequence in protocol.iter('sequence'):
                items=list(sequence)
                if len(items)>=2 and items[0].tag=='uuid' and items[0].get('value')=='0x0003':
                    channel=int(items[1].get('value','0'),0)
                    if 1<=channel<=30:
                        result.append({'name':name.get('value','') if name is not None else '', 'channel':channel})
        return result

    async def configure(self, config):
        await self.close()
        for entry in config:
            # Never replace another application's existing rfcomm node.
            index = next((i for i in range(32) if not Path('/dev/rfcomm' + str(i)).exists()
                          and all(v['index'] != i for v in self.links.values())), None)
            if index is None:
                raise Invalid('no_free_rfcomm_device')
            mac = entry['device']
            link = {'config': entry, 'index': index, 'fd': None, 'process': None,
                    'server': None, 'client': None, 'state': 'connecting', 'write_lock': asyncio.Lock()}
            self.links[mac] = link
            task = asyncio.create_task(self.run(mac, link))
            link['task']=task
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def run(self, mac, link):
        delay=1
        while self.links.get(mac) is link:
            link.update(state='connecting',connected_once=False)
            await self.run_once(mac,link)
            delay=1 if link['connected_once'] else min(delay*2,60)
            self.emit('serial_status',{'address':mac,'state':'retrying','delay':delay})
            await asyncio.sleep(delay)

    async def run_once(self, mac, link):
        try:
            node = '/dev/rfcomm' + str(link['index'])
            link['process'] = await asyncio.create_subprocess_exec('/usr/bin/rfcomm', 'connect',
                node, mac, str(link['config']['channel']), stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            # Bounded wait only during connection establishment; no idle device polling.
            for _ in range(100):
                if link['process'].returncode is not None:
                    raise Invalid('rfcomm_connection_failed')
                try:
                    link['fd'] = os.open(node, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                    break
                except FileNotFoundError:
                    await asyncio.sleep(.1)
            if link['fd'] is None:
                raise Invalid('rfcomm_connect_timeout')
            tty.setraw(link['fd'])
            loop = asyncio.get_running_loop()
            loop.add_reader(link['fd'], self.received, mac, link)
            if link['config']['port']:
                link['server'] = await asyncio.start_server(lambda r, w: self.client(mac, link, r, w),
                    link['config']['bind'], link['config']['port'], limit=8192)
            link['state'] = 'connected'
            link['connected_once']=True
            self.emit('serial_status', {'address': mac, 'state': 'connected'})
            await link['process'].wait()
            link['state'] = 'disconnected'
        except asyncio.CancelledError:
            raise
        except (OSError, Invalid):
            link['state'] = 'connection_failed'
        finally:
            await self.cleanup(link)
            self.emit('serial_status', {'address': mac, 'state': link['state']})

    def received(self, mac, link):
        try:
            data = os.read(link['fd'], 2048)
            if not data:
                asyncio.get_running_loop().remove_reader(link['fd'])
                return
            self.emit('serial', {'address': mac, 'hex': data.hex()})
            writer = link['client']
            if writer:
                if writer.transport.get_write_buffer_size() > 65536:
                    writer.close()
                else:
                    writer.write(data)
        except BlockingIOError:
            pass
        except OSError:
            asyncio.get_running_loop().remove_reader(link['fd'])

    async def write_bytes(self, mac, data):
        link = self.links.get(address(mac))
        if not link or link['state'] != 'connected' or link['fd'] is None:
            raise Invalid('serial_not_connected')
        async with link['write_lock']:
            while data:
                if link['fd'] is None:
                    raise Invalid('serial_not_connected')
                try:
                    count = os.write(link['fd'], data)
                    data = data[count:]
                except BlockingIOError:
                    loop = asyncio.get_running_loop()
                    ready = loop.create_future()
                    descriptor = link['fd']
                    loop.add_writer(descriptor, lambda: ready.done() or ready.set_result(None))
                    try:
                        await asyncio.wait_for(ready, 5)
                    finally:
                        loop.remove_writer(descriptor)

    async def write(self, mac, value):
        await self.write_bytes(mac, hex_bytes(value))
        return {}

    async def client(self, mac, link, reader, writer):
        if link['client']:
            writer.close()
            return
        link['client'] = writer
        try:
            while data := await reader.read(512):
                await self.write_bytes(mac, data)
        except (OSError, Invalid, asyncio.TimeoutError):
            pass
        finally:
            if link['client'] is writer:
                link['client'] = None
            writer.close()
            await writer.wait_closed()

    async def cleanup(self, link):
        if link['server']:
            link['server'].close()
            await link['server'].wait_closed()
        if link['client']:
            link['client'].close()
        if link['fd'] is not None:
            asyncio.get_running_loop().remove_reader(link['fd'])
            os.close(link['fd'])
            link['fd'] = None
        process = link['process']
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        link['server']=None;link['client']=None;link['process']=None

    async def suspend(self, mac):
        link=self.links.get(mac)
        if link:
            task=link.get('task')
            if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.links.pop(mac,None)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.links.clear()
