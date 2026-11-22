"""Real target ucode; independently parse every NDEF/TLV, no device access."""
import os
from pathlib import Path
import subprocess
import unittest
import uuid

TOP=Path(__file__).resolve().parents[1]
ENCODER=TOP/'rootfs/overlay/usr/share/ucode/mu300/nfc-wifi.uc'

def attributes(data):
    result={}
    while data:
        assert len(data)>=4
        kind=int.from_bytes(data[:2],'big');size=int.from_bytes(data[2:4],'big')
        assert len(data)>=4+size
        result[kind]=data[4:4+size];data=data[4+size:]
    return result

def records(raw):
    assert raw[0]==3
    offset=2;size=raw[1]
    if size==255:offset=4;size=int.from_bytes(raw[2:4],'big')
    assert len(raw)==offset+size+1 and raw[-1]==254
    data=raw[offset:-1];result=[]
    while data:
        flags,tl=data[:2];p=2
        if flags&16:pl=data[p];p+=1
        else:pl=int.from_bytes(data[p:p+4],'big');p+=4
        assert not flags&40  # no chunking or IDs in generated tokens
        assert len(data)>=p+tl+pl
        result.append((flags,data[p:p+tl],data[p+tl:p+tl+pl]))
        data=data[p+tl+pl:]
    assert result[0][0]&128 and result[-1][0]&64
    return result

@unittest.skipUnless(os.environ.get('MU300_TEST_RUNTIME_IMAGE'),'select target ucode image')
class Encoding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container='nfc-encoding-'+uuid.uuid4().hex[:8]
        subprocess.run(['docker','run','-d','--pull=never','--platform','linux/arm64','--network','none',
            '--name',cls.container,'-v',str(ENCODER)+':/encode.uc:ro',
            os.environ['MU300_TEST_RUNTIME_IMAGE'],'sleep','3600'],check=True,stdout=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['docker','rm','-f',cls.container],check=True,stdout=subprocess.DEVNULL)

    def call(self,mode,key='private pass',ssid='Test AP',hidden='0'):
        return subprocess.run(['docker','exec','-i',self.container,'ucode','/encode.uc',ssid,mode,hidden],
            input=key.encode(),capture_output=True,timeout=15)

    def token(self,mode,**kwargs):
        result=self.call(mode,**kwargs)
        self.assertEqual(result.returncode,0,result.stderr)
        return records(bytes.fromhex(result.stdout.decode()))

    def test_all_personal_families_and_cipher_suffixes(self):
        families={'none':(1,1,False),'owe':(1,1,False),'wep-open':(1,2,True),
            'wep-shared':(4,2,True),'psk':(2,8,True),'psk2':(32,8,True),
            'psk-mixed':(34,8,True),'sae':(32,8,True),'sae-mixed':(32,8,True),'sae-ext-key':(32,8,True)}
        for mode,(auth,cipher,secret) in families.items():
            for suffix in ('','+ccmp','+aes','+gcmp','+ccmp-256','+gcmp-256','+tkip','+tkip+ccmp','+tkip+aes'):
                if mode in ('none','owe','wep-open','wep-shared') and suffix:continue
                with self.subTest(mode=mode+suffix):
                    token=self.token(mode+suffix)
                    cred=attributes(attributes(token[0][2])[0x100e])
                    self.assertEqual(cred[0x1045],b'Test AP')
                    self.assertEqual(int.from_bytes(cred[0x1003],'big'),auth)
                    self.assertEqual(int.from_bytes(cred[0x100f],'big'),12 if suffix in ('+tkip+ccmp','+tkip+aes') else 4 if suffix=='+tkip' else cipher)
                    self.assertEqual(cred[0x1027],b'private pass' if secret else b'')
                    self.assertEqual(token[1][1],b'T')
                    self.assertIn(('X:'+mode+suffix+';').encode(),token[1][2])

    def test_sae_short_password_unicode_escaping_and_hidden(self):
        token=self.token('sae',key='a',ssid='测试;AP:"\\',hidden='1')
        cred=attributes(attributes(token[0][2])[0x100e])
        self.assertEqual(cred[0x1027],b'a')
        self.assertEqual(cred[0x1045],'测试;AP:"\\'.encode())
        self.assertIn(b'T:SAE;',token[1][2]);self.assertIn(b'H:true;',token[1][2])
        self.assertIn(b'\\;',token[1][2]);self.assertIn(b'\\:',token[1][2])

    def test_unrecognized_mode_has_content_without_server_secret(self):
        for mode in ('new-security-mode','wpa2','wpa3'):
            token=self.token(mode,key='RADIUS-SECRET')
            self.assertEqual(len(token),1)
            self.assertIn(mode.encode(),token[0][2]);self.assertNotIn(b'RADIUS-SECRET',token[0][2])

    def test_encoding_failure_has_no_partial_token_or_password_diagnostic(self):
        for mode,key,ssid in [('sae','a\0b','AP'),('psk2','private pass','x'*33),('psk2','x'*600,'AP')]:
            r=self.call(mode,key=key,ssid=ssid)
            self.assertNotEqual(r.returncode,0)
            self.assertFalse(r.stdout);self.assertNotIn(b'private pass',r.stderr)

    def test_sustained_alternating_updates_have_no_cached_password(self):
        for n in range(40):
            token=self.token(('sae','psk2','sae-mixed')[n%3],key=f'new-key-{n}')
            cred=attributes(attributes(token[0][2])[0x100e])
            self.assertEqual(cred[0x1027],f'new-key-{n}'.encode())
