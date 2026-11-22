// NFC encoding only: no GPIO/I2C, Wi-Fi writes, polling or network access.
// WSC has legacy authentication IDs. Android adds SAE/OWE security parameters
// when importing PSK/open credentials; never change the AP to match the token.
import { readfile } from 'fs';

function bytes(s) { let a=[]; for(let i=0;i<length(s);i++)push(a,ord(s,i)); return a; }
function be16(n) { return [(n>>8)&255,n&255]; }
function attr(id,data) { return [...be16(id),...be16(length(data)),...data]; }
function record(tnf,type,payload,first,last) {
    let flags=(first?128:0)|(last?64:0)|tnf;
    return length(payload)<256 ? [flags|16,length(type),length(payload),...bytes(type),...payload] :
        [flags,length(type),0,0,...be16(length(payload)),...bytes(type),...payload];
}
function escape(s) { return replace(s,/[\\;,:\"]/g,c=>'\\'+c); }
let ssid=ARGV[0], encryption=ARGV[1] ?? '', hidden=ARGV[2]=='1';
let key=readfile('/dev/stdin') ?? '';
if(!ssid || length(ssid)>32 || index(key,'\x00')>=0)
    die('Invalid NFC Wi-Fi credentials\n');
let parts=split(encryption,'+'), mode=parts[0], auth=null, cipher=8, type=mode, personal=false;
// These are protocol families, not a list of permitted firmware/config values.
// Unrecognized modes still get a text record containing their exact mode.
if(mode=='' || mode=='none' || mode=='open' || mode=='owe') {auth=1;cipher=1;type=mode=='owe'?'OWE':'nopass';}
else if(match(mode,/^wep/)) {auth=mode=='wep-shared'?4:1;cipher=2;type='WEP';personal=true;}
else if(match(mode,/^(psk|sae)/)) {
    auth=mode=='psk'?2:mode=='psk-mixed'?34:32;
    type=match(mode,/^sae/) && mode!='sae-mixed'?'SAE':'WPA';personal=true;
}
if(index(parts,'tkip')>=0) cipher=4;
if(index(parts,'tkip')>=0 && (index(parts,'ccmp')>=0 || index(parts,'aes')>=0 || index(parts,'gcmp')>=0))cipher=12;
if(!personal)key=''; // AP/RADIUS server secrets are not client credentials.
let wifi='WIFI:T:'+escape(type)+';S:'+escape(ssid)+';X:'+escape(encryption)+';';
if(personal)wifi+='P:'+escape(key)+';';
if(hidden)wifi+='H:true;';
wifi+=';';
let payload=[], text=[2,...bytes('en'),...bytes(wifi)];
if(auth!=null) {
    let cred=[...attr(0x1026,[1]),...attr(0x1045,bytes(ssid)),...attr(0x1003,be16(auth)),
        ...attr(0x100f,be16(cipher)),...attr(0x1027,bytes(key)),...attr(0x1020,[255,255,255,255,255,255])];
    payload=record(2,'application/vnd.wfa.wsc',[...attr(0x104a,[16]),...attr(0x100e,cred),
        ...attr(0x1049,[0,55,42,0,1,32])],true,false);
}
// The additional text retains the real SAE/OWE mode and hidden flag for NFC
// readers. Enterprise APs carry no fabricated client account or shared secret.
payload=[...payload,...record(1,'T',text,auth==null,true)];
let tlv=length(payload)<255?[3,length(payload),...payload,254]:[3,255,...be16(length(payload)),...payload,254];
if(length(tlv)>512)die('NFC Wi-Fi data exceeds tag writer capacity\n');
printf('%s\n',join(' ',map(tlv,n=>sprintf('%02x',n))));
