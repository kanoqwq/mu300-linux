# SPDX-License-Identifier: MIT
"""One bounded, persistent decoder subprocess; no extra radio scanner."""
import asyncio
import json

ALIASES={
 'tempc':('temperature','°C','temperature'),'hum':('humidity','%','humidity'),
 'batt':('battery','%','battery'),'volt':('voltage','V','voltage'),
 'lux':('illuminance','lx','illuminance'),'co2':('co2','ppm','carbon_dioxide'),
 'pm25':('pm25','µg/m³','pm25'),'pm10':('pm10','µg/m³','pm10'),
 'pres':('pressure','hPa','atmospheric_pressure'),'pressure':('pressure','hPa','atmospheric_pressure'),
 'moist':('moisture','%','moisture'),'power':('power','W','power'),
 'open':('opening','','opening'),'motion':('motion','','motion'),
 'presence':('presence','','presence'),'water':('moisture','','moisture'),
}

def packets(data):
    base={'id':data['Address'],'name':data.get('Name',data.get('Alias',''))}
    for uuid,raw in data.get('ServiceData',{}).items():
        if len(raw)<=1024:yield dict(base,servicedata=raw,servicedatauuid=uuid[4:8] if uuid.startswith('0000') else uuid)
    for key,raw in data.get('ManufacturerData',{}).items():
        if len(raw)<=1024:yield dict(base,manufacturerdata=int(key).to_bytes(2,'little').hex()+raw)

def measurements(result, mac):
    data=result.get('data',{})
    if not data.get('model_id'):return None
    items=[]
    for key,(name,unit,cls) in ALIASES.items():
        if key not in data or not isinstance(data[key],(int,float,bool)):continue
        boolean=key in ('open','motion','presence','water')
        if key=='power' and isinstance(data[key],bool):boolean=True;unit=''
        items.append({'key':name+'_1','name':name,'value':bool(data[key]) if boolean else data[key],
                      'domain':'binary_sensor' if boolean else 'sensor','unit':unit,'device_class':cls})
    if not items:return None
    return {'address':mac,'protocol':'theengs','model':data['model_id'],'manufacturer':data.get('brand',''),
            'measurements':items}

class Decoder:
    def __init__(self, callback, emit):
        self.callback=callback;self.emit=emit;self.pending={};self.queue=asyncio.Queue(128);self.task=None;self.process=None

    def push(self, data):
        mac=data['Address']
        if mac not in self.pending:
            if self.queue.full():return
            self.queue.put_nowait(mac)
        self.pending[mac]=data
        if self.task is None or self.task.done():self.task=asyncio.create_task(self.run())

    async def run(self):
        try:
            self.process=await asyncio.create_subprocess_exec('/usr/bin/unisoc-ble-decode',
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=32768)
            while True:
                mac=await self.queue.get();data=self.pending.pop(mac)
                for packet in packets(data):
                    self.process.stdin.write(json.dumps(packet).encode()+b'\n');await self.process.stdin.drain()
                    raw=await asyncio.wait_for(self.process.stdout.readline(),2)
                    if not raw:raise ValueError('decoder exited')
                    parsed=measurements(json.loads(raw),mac)
                    if parsed:self.callback(parsed);break
        except asyncio.CancelledError:raise
        except (OSError,ValueError,asyncio.TimeoutError):self.emit('decoder_error',{'error':'bluetooth_decoder_unavailable'})
        finally:
            if self.process:
                if self.process.returncode is None:self.process.terminate()
                await self.process.wait();self.process=None

    async def close(self):
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True);self.task=None
        self.pending.clear()
        while not self.queue.empty():self.queue.get_nowait()
