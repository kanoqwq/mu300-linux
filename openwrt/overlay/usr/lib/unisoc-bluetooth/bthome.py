# SPDX-License-Identifier: MIT
"""BTHome v2 service data, https://bthome.io/format/ and /encryption/.

Unrecognized object lengths cannot be guessed: retain raw advertisements instead
of reporting the following bytes as measurements. AES-CCM authenticates first.
"""
from collections import Counter
from core import Invalid

SERVICE = '0000fcd2-0000-1000-8000-00805f9b34fb'
# size, signed, multiplier, name, unit, HA device class
SENSORS = {
 0x00:(1,0,1,'packet_id','',''), 0x01:(1,0,1,'battery','%','battery'),
 0x02:(2,1,.01,'temperature','°C','temperature'),0x03:(2,0,.01,'humidity','%','humidity'),
 0x04:(3,0,.01,'pressure','hPa','atmospheric_pressure'),0x05:(3,0,.01,'illuminance','lx','illuminance'),
 0x06:(2,0,.01,'mass','kg','weight'),0x07:(2,0,.01,'mass','lb','weight'),
 0x08:(2,1,.01,'dewpoint','°C','temperature'),0x09:(1,0,1,'count','',''),
 0x0a:(3,0,.001,'energy','kWh','energy'),0x0b:(3,0,.01,'power','W','power'),
 0x0c:(2,0,.001,'voltage','V','voltage'),0x0d:(2,0,1,'pm25','µg/m³','pm25'),
 0x0e:(2,0,1,'pm10','µg/m³','pm10'),0x12:(2,0,1,'co2','ppm','carbon_dioxide'),
 0x13:(2,0,1,'tvoc','µg/m³','volatile_organic_compounds'),0x14:(2,0,.01,'moisture','%','moisture'),
 0x2e:(1,0,1,'humidity','%','humidity'),0x2f:(1,0,1,'moisture','%','moisture'),
 0x3d:(2,0,1,'count','',''),0x3e:(4,0,1,'count','',''),0x3f:(2,1,.1,'rotation','°',''),
 0x40:(2,0,1,'distance','mm','distance'),0x41:(2,0,.1,'distance','m','distance'),
 0x42:(3,0,.001,'duration','s','duration'),0x43:(2,0,.001,'current','A','current'),
 0x44:(2,0,.01,'speed','m/s','speed'),0x45:(2,1,.1,'temperature','°C','temperature'),
 0x46:(1,0,.1,'uv_index','',''),0x47:(2,0,.1,'volume','L','volume'),
 0x48:(2,0,1,'volume','mL','volume'),0x49:(2,0,.001,'volume_flow_rate','m³/h','volume_flow_rate'),
 0x4a:(2,0,.1,'voltage','V','voltage'),0x4b:(3,0,.001,'gas','m³','gas'),
 0x4c:(4,0,.001,'gas','m³','gas'),0x4d:(4,0,.001,'energy','kWh','energy'),
 0x4e:(4,0,.001,'volume','L','volume'),0x4f:(4,0,.001,'water','L','water'),
 0x50:(4,0,1,'timestamp','','timestamp'),0x51:(2,0,.001,'acceleration','m/s²',''),
 0x52:(2,0,.001,'gyroscope','°/s',''),0x55:(4,0,.001,'volume_storage','L','volume'),
 0x56:(2,0,1,'conductivity','µS/cm',''),0x57:(1,1,1,'temperature','°C','temperature'),
 0x58:(1,1,.35,'temperature','°C','temperature'),0x59:(1,1,1,'count','',''),
 0x5a:(2,1,1,'count','',''),0x5b:(4,1,1,'count','',''),0x5c:(4,1,.01,'power','W','power'),
 0x5d:(2,1,.001,'current','A','current'),0x5e:(2,0,.01,'direction','°',''),
 0x5f:(2,0,.1,'precipitation','mm','precipitation'),0x60:(1,0,1,'channel','',''),
 0x61:(2,0,1,'rotational_speed','rpm',''),0x62:(4,1,.000001,'speed','m/s','speed'),
 0x63:(4,1,.000001,'acceleration','m/s²',''),0x64:(1,0,1,'light_level','',''),
 0x65:(1,0,1,'settings_revision','',''),0xf0:(2,0,1,'device_type','',''),
 0xf1:(4,0,1,'firmware','',''),0xf2:(3,0,1,'firmware','',''),
}
BINARY = dict(zip([0x0f,0x10,0x11]+list(range(0x15,0x2e)),[
 'generic','power','opening','battery','battery_charging','carbon_monoxide','cold','connectivity',
 'door','garage_door','gas','heat','light','lock','moisture','motion','moving','occupancy',
 'plug','presence','problem','running','safety','smoke','sound','tamper','vibration','window']))
BUTTONS={0:'none',1:'press',2:'double_press',3:'triple_press',4:'long_press',
         5:'long_double_press',6:'long_triple_press',128:'hold_press',129:'hold_release'}

def decode_payload(mac, raw, bindkey=''):
    raw=bytes.fromhex(raw) if isinstance(raw,str) else bytes(raw)
    if not raw or raw[0] >> 5 != 2:raise Invalid('unsupported_bthome_version')
    result={'protocol':'bthome_v2','encrypted':bool(raw[0]&1),'trigger_based':bool(raw[0]&4),'measurements':[]}
    payload=raw[1:]
    if raw[0]&1:
        if not bindkey:raise Invalid('bthome_key_required')
        if len(payload)<9:raise Invalid('invalid_bthome_packet')
        from cryptography.hazmat.primitives.ciphers.aead import AESCCM
        from cryptography.exceptions import InvalidTag
        counter=payload[-8:-4]
        nonce=bytes.fromhex(mac.replace(':',''))+b'\xd2\xfc'+raw[:1]+counter
        try:payload=AESCCM(bytes.fromhex(bindkey),tag_length=4).decrypt(nonce,payload[:-8]+payload[-4:],None)
        except InvalidTag as error:raise Invalid('bthome_authentication_failed') from error
        result['counter']=int.from_bytes(counter,'little')
    counts=Counter();pos=0
    while pos<len(payload):
        obj=payload[pos];pos+=1
        if obj in (0x53,0x54,0x3b):
            if pos>=len(payload):raise Invalid('invalid_bthome_packet')
            length=payload[pos];pos+=1
            if obj==0x3b:length=(length&31)+1
            size,signed,scale,name,unit,cls=length,False,1,{0x53:'text',0x54:'raw',0x3b:'command'}[obj],'',''
        elif obj in (0x3a,0x3c):
            size,signed,scale,name,unit,cls=(1 if obj==0x3a else 2),False,1,('button' if obj==0x3a else 'dimmer'),'',''
        elif obj in BINARY:
            size,signed,scale,name,unit,cls=1,False,1,BINARY[obj],'',BINARY[obj]
        elif obj in SENSORS:
            size,signed,scale,name,unit,cls=SENSORS[obj]
        else:raise Invalid('unsupported_bthome_object_%02x'%obj)
        if pos+size>len(payload):raise Invalid('invalid_bthome_packet')
        data=payload[pos:pos+size];pos+=size
        number=int.from_bytes(data,'little',signed=bool(signed))
        if obj==0:result['packet_id']=number;continue
        if obj==0x53:value=data.decode('utf-8',errors='replace')
        elif obj in (0x54,0x3b):value=data.hex()
        elif obj==0x3a:value=BUTTONS.get(number,'unknown_%d'%number)
        elif obj==0x3c:value=['none','rotate_left','rotate_right'][data[0]] if data[0]<3 else 'unknown'
        elif obj in BINARY:
            if number not in (0,1):raise Invalid('invalid_bthome_boolean')
            value=bool(number)
        else:value=round(number*scale,6) if scale!=1 else number
        counts[name]+=1
        key='%s_%d'%(name,counts[name])
        domain='binary_sensor' if obj in BINARY else 'event' if obj in (0x3a,0x3c) else 'sensor'
        result['measurements'].append({'key':key,'name':name,'value':value,'unit':unit,'device_class':cls,
                                       'domain':domain,'object_id':obj})
    return result
