"""Execute production ucode with private files/UCI and deterministic netlink/RPC."""
import json
import os
import subprocess
import unittest
from pathlib import Path
from helpers import TOP, ucode

APP=TOP/'openwrt/luci-app-mu300/root'
DASH=APP/'usr/share/ucode/mu300/dash.uc'

class Snapshot:
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.module=self.root/'modules'; self.module.mkdir(exist_ok=True)
        self.config=self.root/'config';self.config.mkdir(exist_ok=True)
        self.responses={};self.stations=[];self.addresses=[]
        for name,source in {
            'loop':"export function process(exe,args,env,cb) {writefile(getenv('DASH_FIXTURE')+'/spawn.json',sprintf('%J',{exe,args,env}));return {pid:999};};",
            'bus':"export function connect(path,timeout) {return {call:(o,m,a)=>{let p=json(readfile(getenv('DASH_FIXTURE')+'/rpc.json'));writefile(getenv('DASH_FIXTURE')+'/last-call.json',sprintf('%J',{o,m,a,timeout}));return p[o+'.'+m] ?? null;}};};",
            'nl':"export const constants={NL80211_CMD_GET_STATION:19,NLM_F_DUMP:768}; export function request(c,f,a) {return json(readfile(getenv('DASH_FIXTURE')+'/stations.json'));};",
            'rt':"export const constants={RTM_GETADDR:22,NLM_F_DUMP:768,AF_INET:2,AF_INET6:10}; export function request(c,f,a) {return filter(json(readfile(getenv('DASH_FIXTURE')+'/addresses.json')),r=>r.family==a.family);};"
        }.items():
            (self.module/(name+'.uc')).write_text("import {readfile,writefile} from 'fs';\n"+source)
        source=DASH.read_text().replace('nl.const.','nl.constants.').replace('rt.const.','rt.constants.')
        for old,new in [('ubus','bus'),('nl80211','nl'),('rtnl','rt'),('uloop','loop')]:
            source=source.replace("from '"+old+"'","from './"+new+".uc'")
        for name in ['hotspot','apn']:
            source=source.replace("'/usr/libexec/unisoc-modem/"+name+".uc'",json.dumps(str(APP/'usr/libexec/unisoc-modem'/(name+'.uc'))))
        (self.module/'dash.uc').write_text(source)
        (self.module/'main.uc').write_text("import * as dash from './dash.uc'; let action=ARGV[0]; print(sprintf('%J\\n',dash[action](ARGV[1] ?? null,ARGV[2]=='cells')));\n")
        self.write('proc/uptime','123.4 5\n')
        self.write('proc/stat','cpu 10 0 5 85 0 0 0 0\n')
        self.write('proc/cpuinfo','processor : 0\nprocessor : 1\n')
        self.write('proc/meminfo','MemTotal: 1024 kB\nMemAvailable: 512 kB\n')
        self.write('proc/sys/kernel/osrelease','7.2.8-fixture\n')
        self.write('run/mu300/device','u30air\n')
        self.write('tmp/sysinfo/model','Fixture board\n')
        self.write('etc/openwrt_release',"DISTRIB_DESCRIPTION='Fixture OpenWrt'\n")
        self.write('tmp/dhcp.leases','1000 E6:7A:77:59:7E:47 192.168.77.105 Xiaomi-17-Pro *\n1000 aa:bb:cc:dd:ee:ff 192.168.77.106 * *\n')
        for dev in ['sipa_eth0','sipa_eth3','br-lan','wlan0','usb0']:
            for path,value in [('statistics/rx_bytes','101'),('statistics/tx_bytes','202'),('operstate','up')]:
                self.write('sys/class/net/'+dev+'/'+path,value+'\n')
        self.setup_config()
        self.responses={
            'network.interface.wan.status':{'up':True,'l3_device':'sipa_eth3','proto':'mu300cell','uptime':42,'dns-server':['1.1.1.1']},
            'network.interface.wan6.status':{'up':True,'dns-server':['2001:4860:4860::8888','1.1.1.1']},
            'network.wireless.status':{'radio0':{'up':True,'interfaces':[{'section':'default_radio0','ifname':'wlan0'}]}},
            'hostapd.wlan0.get_status':{'status':'ENABLED'},
            'hostapd.wlan0.get_clients':{'clients':{}},
            'mu300.modem.collect':{'started':1}}
        self.addresses=[{'family':2,'dev':'br-lan','address':'192.168.1.1/24'},{'family':10,'dev':'sipa_eth3','address':'2001:db8::2/64'}]

    def setup_config(self, extra=''):
        (self.config/'unisoc_modem').write_text("config core 'main'\n option data_device 'sipa_eth0'\n option data_interface 'wan'\n option sim_slots '2'\n"+extra)
        (self.config/'network').write_text("config interface 'wan'\n option proto 'mu300cell'\n option apn 'CMIOT5G'\n")
        (self.config/'wireless').write_text("config wifi-device 'radio0'\n option channel '36'\n option band '5g'\n option htmode 'HE80'\n option country 'CN'\nconfig wifi-iface 'default_radio0'\n option mode 'ap'\n option device 'radio0'\n option ssid 'FixtureWiFi'\n option encryption 'psk2'\n")

    def write(self, name, text):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);return p

    def run(self, action='info', slot=None, cells=False):
        cmd=ucode()
        if not cmd:raise unittest.SkipTest('ucode runtime required; covered in disposable ARM64 container')
        for name,value in [('rpc',self.responses),('stations',self.stations),('addresses',self.addresses)]:
            (self.root/(name+'.json')).write_text(json.dumps(value))
        env=dict(os.environ,MU300_DASH_SYSROOT=str(self.root),MU300_DASH_CONFIG=str(self.config),DASH_FIXTURE=str(self.root))
        p=subprocess.run(cmd+[str(self.module/'main.uc'),action]+([] if slot is None else [slot])+(['cells'] if cells else []),env=env,capture_output=True,text=True,timeout=8)
        if p.returncode:raise AssertionError(p.stderr)
        return json.loads(p.stdout)
