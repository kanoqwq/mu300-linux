# SPDX-License-Identifier: MIT
"""Reconnect only configured devices after disconnect/discovery events."""
import asyncio
from core import Invalid

class Recovery:
    def __init__(self, blue, restore, emit):
        self.blue=blue;self.restore=restore;self.emit=emit;self.devices=set();self.tasks={};self.suppressed=set();self.enabled=False

    async def configure(self, gateway):
        await self.close();self.enabled=gateway['enabled'];self.suppressed.clear()
        serial={d['device'] for d in gateway['serial']}
        self.devices={d['device'] for d in gateway['known_devices'] if d['auto_connect'] and d['device'] not in serial}
        self.devices.update(d['device'] for d in gateway['subscriptions'])

    def changed(self, mac):
        if not self.enabled or mac not in self.devices or mac in self.suppressed or mac in self.tasks:return
        self.tasks[mac]=asyncio.create_task(self.run(mac))
        self.tasks[mac].add_done_callback(lambda _:self.tasks.pop(mac,None))

    async def run(self, mac):
        delay=1
        while self.enabled and mac not in self.suppressed:
            try:
                path=self.blue.device_path(mac)
                if not self.blue.objects.get(self.blue.adapter_path(),{}).get('org.bluez.Adapter1',{}).get('Powered'):return
                if not self.blue.objects[path]['org.bluez.Device1'].get('Connected'):
                    await self.blue.device_action(mac,'connect')
                await self.restore(mac)
                self.emit('recovery',{'address':mac,'state':'connected'})
                return
            except (Invalid,OSError,asyncio.TimeoutError) as error:
                self.emit('recovery',{'address':mac,'state':'retrying','delay':delay,
                                      'error':str(error)[:256] if isinstance(error,Invalid) else 'bluetooth_connection_failed'})
                await asyncio.sleep(delay);delay=min(60,delay*2)

    def suspend(self, mac):
        self.suppressed.add(mac)
        task=self.tasks.get(mac)
        if task:task.cancel()

    async def close(self):
        self.enabled=False
        for task in self.tasks.values():task.cancel()
        await asyncio.gather(*list(self.tasks.values()),return_exceptions=True)
        self.tasks.clear()
