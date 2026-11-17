# SPDX-License-Identifier: MIT
"""Explicitly configured actions; neither MQTT nor templates can supply code."""
import asyncio
from core import Invalid,decode

def command_bytes(command, value=None):
    if command['domain']=='switch':
        if type(value) is not bool and value not in ('ON','OFF'):raise Invalid('invalid_command_value')
        # bool is accepted for direct RPC; strings match HA MQTT switch payloads.
        return command['on'] if value is True or value=='ON' else command['off']
    if command['domain']=='number':
        try:
            number=int(value)
            if isinstance(value,bool) or str(number)!=str(value).strip():raise ValueError()
            if not command['min']<=number<=command['max'] or (number-command['min'])%command['step']:raise ValueError()
            sizes={'u8':(1,False),'u16le':(2,False),'i16le':(2,True),'u32le':(4,False)}
            size,signed=sizes[command['codec']]
            return number.to_bytes(size,'little',signed=signed).hex()
        except (ValueError,TypeError,OverflowError) as error:raise Invalid('invalid_command_value') from error
    return command['hex']

async def execute(blue, serial, command, value=None):
    kind=command['kind'];mac=command['device']
    result={'command':command['id'],'device':mac,'acknowledged':True,'confirmed':False}
    if kind in ('connect','disconnect'):
        await blue.device_action(mac,kind)
    elif kind=='read':
        result.update(await blue.read(mac,command['service'],command['characteristic']))
        result['confirmed']=True
    elif kind=='serial_write':
        await serial.write(mac,command_bytes(command,value))
    elif kind=='write':
        data=command_bytes(command,value)
        await blue.write(mac,command['service'],command['characteristic'],data)
        # State is published only after a readable characteristic confirms the value.
        path=blue.resolve_characteristic(mac,command['service'],command['characteristic'])
        if 'read' in blue.objects[path].get('org.bluez.GattCharacteristic1',{}).get('Flags',[]):
            try:actual=(await blue.read(mac,command['service'],command['characteristic']))['hex']
            except (Invalid,OSError,asyncio.TimeoutError):
                result['verification_error']='bluetooth_command_readback_failed';return result
            result['hex']=actual;result['confirmed']=actual==data
            if command['domain']=='switch' and actual in (command['on'],command['off']):
                result['value']='ON' if actual==command['on'] else 'OFF'
                result['state_confirmed']=True
            elif command['domain']=='number':
                try:result['value']=decode(bytes.fromhex(actual),command['codec']);result['state_confirmed']=True
                except Invalid:result['verification_error']='bluetooth_command_readback_failed'
    else:raise Invalid('invalid_command')
    return result
