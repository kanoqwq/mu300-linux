#!/usr/bin/env python3
"""Actual RS/RA expiry in a private Docker netns, using an existing target root.

No device, host interface, Internet access, extra rootfs or package downloads.
Run through the repository build-release.py runner with --root and --image.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

TOP=Path(__file__).resolve().parents[2]

PEER=r'''
import json,socket,struct,time
from pathlib import Path
root=Path(__file__).parent
index=socket.if_nametoindex('router0')
s=socket.socket(socket.AF_INET6,socket.SOCK_RAW,socket.IPPROTO_ICMPV6)
s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_RECVPKTINFO,1)
s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_HOPS,255)
s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_JOIN_GROUP,socket.inet_pton(socket.AF_INET6,'ff02::2')+struct.pack('=I',index))
pio=struct.pack('!BBBBIII16s',3,4,64,0xc0,60,60,0,socket.inet_pton(socket.AF_INET6,'2001:db8:55::'))
advert=struct.pack('!BBHBBHII',134,0,0,64,0,12,0,0)+pio
(root/'peer-ready').touch()
while True:
    packet,controls,flags,source=s.recvmsg(65535,256)
    received=None
    for level,kind,data in controls:
        if level==socket.IPPROTO_IPV6 and kind==socket.IPV6_PKTINFO:received=struct.unpack_from('=I',data,16)[0]
    if received!=index or len(packet)<8 or packet[:2]!=b'\x85\0':continue
    with (root/'requests').open('a') as log:log.write(str(time.monotonic())+'\n')
    info=struct.pack('=16sI',b'\0'*16,index)
    s.sendmsg([advert],[(socket.IPPROTO_IPV6,socket.IPV6_PKTINFO,info)],0,('ff02::1',0,0,index))
'''

SCENARIO=r'''
import json,os,runpy,socket,subprocess,sys,time
from pathlib import Path
root=Path(__file__).parent
api=runpy.run_path('/target/usr/sbin/unisoc-ra-renew')
ip='/target/sbin/ip'
def run(*args):return subprocess.check_output([ip,*args],text=True)
def default():
    # An expired FIB entry can remain in a route dump until lazy GC. Check
    # the usable route with an off-prefix lookup, without sending a packet.
    found=subprocess.run([ip,'-6','route','get','2001:db8:99::123'],capture_output=True,text=True)
    return found.returncode==0 and 'dev sipa_eth0' in found.stdout
def wait_for(fn,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if fn():return True
        time.sleep(.05)
    return False
run('link','add','sipa_eth0','type','veth','peer','name','router0')
run('link','set','sipa_eth0','up');run('link','set','router0','up')
run('-6','addr','add','fe80::2/64','dev','sipa_eth0','nodad')
run('-6','addr','add','fe80::1/64','dev','router0','nodad')
peer=daemon=None
with (root/'peer.log').open('w') as log, (root/'daemon.log').open('w') as daemonlog:
    try:
        peer=subprocess.Popen([sys.executable,str(root/'peer.py')],stdout=log,stderr=log)
        assert wait_for(lambda:(root/'peer-ready').exists(),5),'RA peer did not start'
        with api['raw_socket']() as wire:api['send_rs'](wire,socket.if_nametoindex('sipa_eth0'))
        assert wait_for(default,5),'initial real RA was not accepted'
        print('CONTROL: initial solicited RA accepted with a 12-second router lifetime',flush=True)
        time.sleep(13)
        assert not default(),('control route did not expire',run('-6','route','show','default'),(root/'requests').read_text())
        print('CONTROL: without renewal the default route expired',flush=True)
        before=len((root/'requests').read_text().splitlines())
        env=dict(os.environ,PATH=str(root/'bin')+':'+os.environ['PATH'])
        daemon=subprocess.Popen([sys.executable,'-B','/target/usr/sbin/unisoc-ra-renew'],env=env,stdout=daemonlog,stderr=daemonlog)
        assert wait_for(default,5),'renewal service did not acquire a real RA'
        start=time.monotonic();time.sleep(14)
        assert daemon.poll() is None,(root/'daemon.log').read_text()
        assert default(),'default route expired while RS renewal was active'
        count=len((root/'requests').read_text().splitlines())-before
        assert count>=3,('missing lifetime refresh solicitations',count)
        print('PASS: real RS/RA kept the route alive beyond its original lifetime; RS count='+str(count),flush=True)
        # Confirm clean stop, then re-observe expiry with the same RA peer.
        daemon.terminate();daemon.wait(5)
        time.sleep(13)
        assert not default(),'route incorrectly became permanent after the service stopped'
        print('PASS: shutdown releases sockets; the real finite route still expires normally',flush=True)
    finally:
        for child in (daemon,peer):
            if child is not None:
                if child.poll() is None:child.terminate()
                child.wait(5)
        print((root/'daemon.log').read_text(),flush=True)
'''

UBUS=r'''
import json,signal,sys
if sys.argv[1:]==['-S','call','network.interface','dump']:
    print(json.dumps({'interface':[{'interface':'wan','proto':'mu300cell','l3_device':'sipa_eth0','up':True,'autostart':True,'available':True}]}))
elif sys.argv[1:]==['-S','listen','network.interface']:
    signal.pause()
else:raise SystemExit(2)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--image',required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    if root==Path('/') or not (root/'etc/openwrt_release').is_file():
        raise ValueError('Use the registered prepared OpenWrt root')
    source=(TOP/'openwrt/overlay/usr/sbin/unisoc-ra-renew').read_bytes().replace(b'\r\n',b'\n')
    if (root/'usr/sbin/unisoc-ra-renew').read_bytes()!=source:
        raise ValueError('Stage current RA source before testing')
    with tempfile.TemporaryDirectory(prefix='ra-renew-netns-',dir=root/'tmp') as name:
        directory=Path(name);inside='/target/tmp/'+directory.name
        for filename,code in [('peer.py',PEER),('scenario.py',SCENARIO),('ubus.py',UBUS)]:
            (directory/filename).write_text(code)
        (directory/'bin').mkdir()
        (directory/'bin/ubus').write_text('#!/bin/sh\nexec /target/usr/bin/python3 '+inside+'/ubus.py "$@"\n')
        (directory/'bin/ubus').chmod(0o755)
        command=['docker','run','--rm','--platform','linux/arm64','--network','none',
            '--cap-add','NET_ADMIN','--cap-add','NET_RAW',
            '--sysctl','net.ipv6.conf.all.disable_ipv6=0','--sysctl','net.ipv6.conf.default.disable_ipv6=0',
            '--sysctl','net.ipv6.conf.default.accept_ra=2','--sysctl','net.ipv6.conf.all.forwarding=1',
            '--sysctl','net.ipv6.conf.default.router_solicitations=0',
            '-v',str(root)+':/target','-e','PYTHONHOME=/target/usr','-e','PYTHONDONTWRITEBYTECODE=1',
            '-e','LD_LIBRARY_PATH=/target/usr/lib:/target/lib','--entrypoint','/target/usr/bin/python3',
            args.image,'-B',inside+'/scenario.py']
        subprocess.run(command,check=True,timeout=90)


if __name__=='__main__':main()
