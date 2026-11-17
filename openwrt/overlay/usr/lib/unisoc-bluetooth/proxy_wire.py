# SPDX-License-Identifier: MIT
"""ESPHome Native API protobuf subset; original implementation from api.proto.

Protocol references: esphome/aioesphomeapi api.proto and Noise frame helper.
Advertisement reconstruction follows ankon/btproxy's MIT-licensed approach;
its copyright notice is retained in vendor/licenses/btproxy-LICENSE.
"""
import re
from core import Invalid, uuid

def varint(n):
    n=int(n)&((1<<64)-1);out=bytearray()
    while n>127:out.append((n&127)|128);n>>=7
    out.append(n);return bytes(out)

def field(number, value):
    if isinstance(value,str):value=value.encode()
    if isinstance(value,(bytes,bytearray)):return varint(number<<3|2)+varint(len(value))+value
    return varint(number<<3)+varint(value)

def message(*fields):return b''.join(field(k,v) for k,v in fields)

def parse(raw):
    pos=0;out={}
    def integer():
        nonlocal pos
        n=0
        for shift in range(0,70,7):
            if pos>=len(raw):raise Invalid('invalid_proxy_packet')
            byte=raw[pos];pos+=1;n|=(byte&127)<<shift
            if not byte&128:return n
        raise Invalid('invalid_proxy_packet')
    while pos<len(raw):
        key=integer();number=key>>3;kind=key&7
        if not number:raise Invalid('invalid_proxy_packet')
        if kind==0:value=integer()
        elif kind==2:
            length=integer()
            if pos+length>len(raw):raise Invalid('invalid_proxy_packet')
            value=raw[pos:pos+length];pos+=length
        elif kind in (1,5):
            length=8 if kind==1 else 4
            if pos+length>len(raw):raise Invalid('invalid_proxy_packet')
            value=raw[pos:pos+length];pos+=length
        else:raise Invalid('invalid_proxy_packet')
        out.setdefault(number,[]).append(value)
    return out

def one(fields,key,default=0):return fields.get(key,[default])[0]

def uuid_fields(value):
    n=int(uuid(value).replace('-',''),16)
    return field(1,n>>64)+field(1,n&((1<<64)-1))

def handle(path, props):
    value=props.get('Handle')
    if isinstance(value,int) and 0<value<=65535:return value
    match=re.search(r'(?:service|char|desc)([0-9a-fA-F]{4})$',path)
    if not match:raise Invalid('bluetooth_gatt_handle_missing')
    return int(match[1],16)

def uuid_ad(value):
    raw=bytes.fromhex(uuid(value).replace('-',''))
    if raw[4:]==bytes.fromhex('00001000800000805f9b34fb'):
        raw=raw[2:4] if raw[:2]==b'\0\0' else raw[:4]
    return raw[::-1]

def advertisement(data):
    # Preserve complete service/manufacturer fields. Never clip encrypted payloads.
    out=bytearray(b'\x02\x01\x06')
    def add(kind, value):
        if len(value)<=254 and len(out)+len(value)+2<=62:out.extend(bytes([len(value)+1,kind])+value)
    for key,value in sorted(data.get('ManufacturerData',{}).items()):
        add(0xff,int(key).to_bytes(2,'little')+bytes.fromhex(value))
    for key,value in sorted(data.get('ServiceData',{}).items()):
        id=uuid_ad(key);add({2:0x16,4:0x20,16:0x21}[len(id)],id+bytes.fromhex(value))
    for key in data.get('UUIDs',[]):
        id=uuid_ad(key);add({2:0x03,4:0x05,16:0x07}[len(id)],id)
    name=(data.get('Name') or data.get('Alias') or '').encode()
    room=62-len(out)-2
    if name and room>0:add(0x09 if len(name)<=room else 0x08,name[:room])
    return bytes(out)
