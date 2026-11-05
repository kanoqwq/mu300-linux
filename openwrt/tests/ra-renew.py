#!/usr/bin/env python3
"""Carrier RA renewal with synthetic packets, netifd and monotonic time only."""
import errno
import ipaddress
import os
from pathlib import Path
import runpy
import socket
import struct
import tempfile
import time
import select
import types
import unittest
from unittest.mock import patch

TOP = Path(__file__).resolve().parents[2]
API = runpy.run_path(str(TOP/'openwrt/overlay/usr/sbin/unisoc-ra-renew'))
G = API['main'].__globals__
SOURCE = 'fe80::1'


def ra(lifetime=1800, options=b''):
    return struct.pack('!BBHBBHII', 134, 0, 0, 64, 0, lifetime, 0, 0) + options


def pio(valid=7200, preferred=3600, prefix='2001:db8::', flags=0xc0):
    return struct.pack('!BBBBIII16s', 3, 4, 64, flags, valid, preferred, 0, ipaddress.IPv6Address(prefix).packed)


def dns(ttl=600, addr='2001:db8::53'):
    return struct.pack('!BBHI16s', 25, 3, 0, ttl, ipaddress.IPv6Address(addr).packed)


def row(proto='mu300cell', iface='wan', dev='sipa_eth0', **extra):
    return dict(interface=iface, proto=proto, l3_device=dev, up=True,
                autostart=True, available=True, **extra)


class Renewal(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.sent = []
        self.index = {'sipa_eth0': 3, 'sipa_eth8': 7}
        self.engine = API['Renewer'](clock=lambda: self.now,
            sender=lambda sock, index: self.sent.append((self.now, index)))
        self.knobs = {}
        self.sysctl = lambda name, key: self.knobs.get((name, key), '0' if key=='disable_ipv6' else '2')

    def use(self, rows=None):
        self.engine.reconcile({'interface': rows or [row()]}, self.sysctl, self.index.get, jitter=0)

    def tick(self, when):
        self.now = when
        self.engine.tick(None, self.index.get)

    def receive(self, packet, index=3, source=SOURCE):
        entries = API['ra_lifetimes'](packet, source, 255)
        self.assertIsNotNone(entries)
        self.engine.packet(index, entries)

    def test_65535_second_router_renews_before_expiry_without_an_event(self):
        self.use(); self.tick(100)
        self.receive(ra(65535))
        self.assertEqual(self.engine.timeout(), 32767.5)
        self.tick(32867); self.assertEqual(len(self.sent), 1)
        self.tick(32867.5); self.assertEqual(len(self.sent), 2)
        self.receive(ra(65535))
        self.assertEqual(self.engine.timeout(), 32767.5)

    def test_short_carrier_lifetime_is_dynamic_not_a_fixed_hourly_job(self):
        for lifetime in (30, 600, 1800, 7200):
            self.now=100; self.sent.clear(); self.engine.states.clear()
            self.use(); self.receive(ra(lifetime))
            self.tick(100 + lifetime / 2 - .1); self.assertFalse(self.sent)
            self.tick(100 + lifetime / 2); self.assertEqual(len(self.sent), 1)

    def test_prefix_preferred_dns_and_rio_can_expire_before_the_router(self):
        self.use(); self.receive(ra(65535, pio(7200, 900)+dns(120)))
        self.assertEqual(self.engine.timeout(), 60)
        self.now=160; self.receive(ra(65535, dns(1800)))
        self.assertEqual(self.engine.timeout(), 390)  # preferred prefix due at 550.
        rio=struct.pack('!BBBBI', 24, 1, 0, 0, 20)
        self.receive(ra(65535, rio)); self.assertEqual(self.engine.timeout(), 10)

    def test_partial_ra_cannot_forget_an_older_prefix_deadline(self):
        self.use(); self.receive(ra(1800, pio(600, 300)))
        self.now=200; self.receive(ra(1800))
        self.assertEqual(self.engine.timeout(), 50)
        self.tick(250); self.now=251; self.receive(ra(1800))
        self.assertEqual(self.engine.timeout(), 3)  # Pending retry, no postponement.

    def test_zero_and_infinite_lifetimes_do_not_create_permanent_routes(self):
        self.use(); self.receive(ra(1800, pio()))
        self.receive(ra(0, pio(0, 0)))
        self.assertFalse(self.engine.states[3].leases)
        self.assertEqual(self.engine.timeout(), 300)
        self.receive(ra(100, pio(0xffffffff, 0xffffffff)))
        self.assertEqual(len(self.engine.states[3].leases), 1)
        self.assertEqual(self.engine.timeout(), 50)

    def test_withdrawal_is_scoped_to_its_router_and_option(self):
        self.use(); self.receive(ra(600))
        self.receive(ra(120), source='fe80::2')
        self.receive(ra(0), source='fe80::2')
        self.assertEqual(len(self.engine.states[3].leases), 1)
        self.assertEqual(self.engine.timeout(), 300)

    def test_lost_ra_and_socket_errors_have_bounded_backoff(self):
        self.use()
        for now in (100,104,108,138,142,146,206): self.tick(now)
        self.assertEqual([x[0] for x in self.sent], [100,104,108,138,142,146,206])
        self.assertGreater(self.engine.timeout(), 0)
        self.engine.sender=lambda *_: (_ for _ in ()).throw(OSError(errno.ENETUNREACH,'offline'))
        self.tick(210); self.assertEqual(self.engine.timeout(), 4)

    def test_short_lifetimes_and_duplicate_packets_never_busy_loop(self):
        self.use(); self.tick(100); self.receive(ra(1))
        self.tick(100.5); self.assertEqual(len(self.sent), 1)
        for when in (101,102,103):
            self.now=when; self.receive(ra(1)); self.tick(when)
        self.assertEqual(len(self.sent), 1)
        self.tick(104); self.assertEqual(len(self.sent), 2)

    def test_periodic_ra_refresh_suppresses_unnecessary_rs(self):
        self.use(); self.receive(ra(600))
        for when in range(200,1001,100):
            self.now=when; self.receive(ra(600)); self.tick(when)
        self.assertFalse(self.sent)

    def test_manual_ifdown_ipv4_only_or_ipv6_disable_leave_no_timer(self):
        for key,value in (('up',False),('autostart',False),('available',False)):
            self.use(); item=row(); item[key]=value; self.use([item])
            self.assertIsNone(self.engine.timeout())
        self.use(); self.knobs[('sipa_eth0','disable_ipv6')]='1'; self.use()
        self.assertIsNone(self.engine.timeout()); self.tick(10000); self.assertFalse(self.sent)

    def test_mm_dhcpv6_owner_accept_ra_zero_and_ipv6_only_pending(self):
        parent=row('modemmanager'); child=row('dhcpv6','wan_6')
        self.knobs[('sipa_eth0','accept_ra')]='0'
        self.use([parent,child]); self.assertEqual(set(self.engine.states), {3})
        parent['up']=False; parent['pending']=True; child['up']=False; child['pending']=True
        child.pop('l3_device'); child['device']='@wan'
        self.use([parent,child]); self.assertEqual(set(self.engine.states), {3})
        parent['autostart']=False; self.use([parent,child]); self.assertFalse(self.engine.states)
        parent['autostart']=True; self.use([parent]); self.assertFalse(self.engine.states)
        self.use([row('dhcpv6')]); self.assertFalse(self.engine.states)

    def test_switching_sim_removes_the_old_interface_and_keeps_one_owner(self):
        self.use(); self.tick(100)
        self.use([row(dev='sipa_eth8')]); self.tick(100)
        self.assertEqual(set(self.engine.states), {7})
        self.assertEqual([x[1] for x in self.sent], [3,7])
        self.use([row(dev='sipa_eth8')]); self.assertEqual(self.engine.timeout(),4)

    def test_foreign_lan_usb_wifi_and_ra_interfaces_are_ignored(self):
        self.use([row(dev='br-lan'),row(iface='x',dev='usb0'),row(iface='y',dev='wlan0')])
        self.assertFalse(self.engine.states)
        self.use(); self.receive(ra(10),index=999); self.assertEqual(self.engine.timeout(),0)

    def test_kernel_index_reuse_or_down_drops_the_old_deadline(self):
        self.use(); self.index['sipa_eth0']=9; self.tick(100)
        self.assertFalse(self.sent); self.assertIsNone(self.engine.timeout())

    def test_ntp_wallclock_is_not_used(self):
        self.use(); self.receive(ra(100))
        with patch.object(G['time'],'time',side_effect=AssertionError('wallclock used')):
            self.tick(150)
        self.assertEqual(self.sent,[(150,3)])

    def test_ra_validation_and_malformed_options(self):
        parse=API['ra_lifetimes']
        for packet in (b'', ra()[:15], b'\x86\x01'+ra()[2:], ra()+b'\x03\0',ra()+b'\x03\x04'+b'\0'*6,
                       ra()+struct.pack('!BBBBI',24,1,64,0,30), ra()+b'\x19\x02'+b'\0'*14):
            self.assertIsNone(parse(packet,SOURCE,255),packet)
        self.assertIsNone(parse(ra(),'2001:db8::1',255))
        self.assertIsNone(parse(ra(),SOURCE,64))
        self.assertIsNone(parse(ra(),'not an IP',255))
        self.assertEqual(len(parse(ra(100,pio(10,20)),SOURCE,255)),1)

    def test_rs_packet_interface_scope_and_no_route_or_address_commands(self):
        class Wire:
            def sendmsg(self,*args): self.args=args
        wire=Wire(); API['send_rs'](wire,7)
        parts,control,flags,target=wire.args
        self.assertEqual(parts,[b'\x85\0\0\0\0\0\0\0'])
        self.assertEqual(target,('ff02::2',0,0,7))
        self.assertEqual(struct.unpack('=16sI',control[0][2]),(b'\0'*16,7))

    def test_received_packet_requires_pktinfo_hoplimit_and_complete_control(self):
        class Wire:
            def recvmsg(self,*_): return self.reply
        wire=Wire(); wire.reply=(ra(),[(socket.IPPROTO_IPV6,socket.IPV6_PKTINFO,struct.pack('=16sI',b'\0'*16,3)),
            (socket.IPPROTO_IPV6,socket.IPV6_HOPLIMIT,struct.pack('=i',255))],0,(SOURCE,0,0,3))
        self.assertEqual(API['receive_ra'](wire)[0],3)
        for flags,control in [(socket.MSG_TRUNC,wire.reply[1]),(socket.MSG_CTRUNC,wire.reply[1]),(0,[]),(0,wire.reply[1][:1])]:
            wire.reply=(ra(),control,flags,(SOURCE,0,0,3))
            self.assertIsNone(API['receive_ra'](wire))

    def test_native_netlink_link_address_and_default_expiry_messages(self):
        def message(kind,body):return struct.pack('=IHHII',16+len(body),kind,0,0,0)+body
        link=message(17,struct.pack('=BBHiII',0,0,0,3,0,0))
        address=message(20,struct.pack('=BBBBI',socket.AF_INET6,64,0,0,7))
        route=message(25,struct.pack('=BBBBBBBBI',socket.AF_INET6,0,0,0,254,9,0,1,0)+struct.pack('=HHI',8,4,3))
        self.assertEqual(list(API['netlink_changes'](link+address+route)),[(3,True,False),(7,False,False),(3,False,True)])
        self.assertEqual(list(API['netlink_changes'](b'\0'*16)),[])

    @unittest.skipIf(os.name=='nt','Linux socket/timerfd ABI is tested in WSL/target')
    def test_real_main_waits_on_boottime_timerfd_and_cleans_all_owners(self):
        # Complete event-loop execution, including long idle wait and bus loss.
        # Every FD/socket/process here is simulated; nothing touches the host.
        for active in (True,False):
            now=[100.0]; armed=[]; sent=[]; closed=[]; keys={}; steps=[0]
            class Wire:
                def __init__(self,name): self.name=name
                def bind(self,*_): pass
                def setblocking(self,*_): pass
                def close(self): closed.append(self.name)
                def sendmsg(self,parts,control,flags,target): sent.append((now[0],target))
                def recvmsg(self,*_):
                    return (ra(100),[(socket.IPPROTO_IPV6,socket.IPV6_PKTINFO,struct.pack('=16sI',b'\0'*16,3)),
                        (socket.IPPROTO_IPV6,socket.IPV6_HOPLIMIT,struct.pack('=i',255))],0,(SOURCE,0,0,3))
            class Pipe:
                def fileno(self): return 77
                def close(self): closed.append('pipe')
            class Bus:
                stdout=Pipe()
                def poll(self): return None
                def terminate(self): closed.append('bus')
                def wait(self,**_): return 0
            class Select:
                def register(self,obj,event,data): keys[data]=types.SimpleNamespace(fileobj=obj,data=data)
                def select(self):
                    step=steps[0];steps[0]+=1
                    if active and step==0:return [(keys['ra'],1)]
                    if active and step==1:
                        now[0]=150.0;return [(keys['timer'],1)]
                    return [(keys['bus'],1)]
                def close(self): closed.append('selector')
            sockets=dict(vars(socket))
            sockets.update(AF_NETLINK=16,NETLINK_ROUTE=0,socket=lambda family,*_:Wire('singleton' if family==socket.AF_UNIX else 'netlink'))
            fakeos=types.SimpleNamespace(set_blocking=lambda *_:None,TFD_NONBLOCK=2048,TFD_TIMER_ABSTIME=1,
                timerfd_create=lambda *_,**__:88,timerfd_settime=lambda fd,**kw:armed.append(kw['initial']),
                read=lambda fd,n:b'' if fd==77 else struct.pack('=Q',1),close=lambda fd:closed.append('timer'))
            fakeprocess=types.SimpleNamespace(Popen=lambda *_a,**_kw:Bus(),
                PIPE=-1,DEVNULL=-3,SubprocessError=G['subprocess'].SubprocessError,TimeoutExpired=G['subprocess'].TimeoutExpired)
            faketime=types.SimpleNamespace(CLOCK_BOOTTIME=7)
            replacements=dict(socket=types.SimpleNamespace(**sockets),os=fakeos,subprocess=fakeprocess,
                selectors=types.SimpleNamespace(DefaultSelector=Select,EVENT_READ=1),signal=types.SimpleNamespace(SIGTERM=15,SIGINT=2,signal=lambda *_:None),
                time=faketime,boottime=lambda:now[0],raw_socket=lambda:Wire('raw'),random=types.SimpleNamespace(uniform=lambda *_:0),
                snapshot=lambda:{'interface':[row()] if active else []},knob=self.sysctl,interface_index=self.index.get)
            with patch.dict(G,replacements):self.assertEqual(API['main'](),1)
            self.assertEqual(set(closed),{'raw','netlink','singleton','timer','pipe','bus','selector'})
            if active:
                self.assertIn(150.0,armed)
                self.assertEqual([x[0] for x in sent],[100.0,150.0])
            else:
                self.assertEqual(armed,[0]);self.assertEqual(sent,[])

    @unittest.skipIf(os.name=='nt','Linux socket ABI is tested in WSL/target')
    def test_raw_socket_sets_receive_validation_and_rs_hoplimit(self):
        calls=[]
        class Wire:
            def setsockopt(self,*args): calls.append(args)
            def setblocking(self,value): self.blocking=value
        wire=Wire(); sockets=dict(vars(socket));sockets['socket']=lambda *_:wire
        with patch.dict(G,socket=types.SimpleNamespace(**sockets)):
            self.assertIs(API['raw_socket'](),wire)
        self.assertIn((socket.IPPROTO_IPV6,socket.IPV6_RECVHOPLIMIT,1),calls)
        self.assertIn((socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_HOPS,255),calls)
        self.assertFalse(wire.blocking)

    @unittest.skipIf(os.name=='nt','Linux service-link permissions are tested in WSL/target')
    def test_stager_is_idempotent_and_refuses_foreign_links_before_writes(self):
        stage=runpy.run_path(str(TOP/'tools/stage-ra-renew.py'))['stage']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);stage(root);stage(root)
            for name in ('usr/sbin/unisoc-ra-renew','etc/init.d/unisoc-ra-renew'):
                self.assertEqual((root/name).read_bytes(),(TOP/'openwrt/overlay'/name).read_bytes().replace(b'\r\n',b'\n'))
                self.assertTrue(os.access(root/name,os.X_OK))
            link=root/'etc/rc.d/S98unisoc-ra-renew';link.unlink();link.symlink_to('../init.d/foreign')
            before=(root/'usr/sbin/unisoc-ra-renew').read_bytes()
            with self.assertRaises(ValueError):stage(root)
            self.assertEqual((root/'usr/sbin/unisoc-ra-renew').read_bytes(),before)

    def test_repository_build_paths_include_the_service(self):
        if (TOP/'devices/m8/prepare-root.py').exists():
            paths=('devices/m8/prepare-root.py','openwrt/build-rootfs.sh','openwrt/build-profile-tf.py')
            for name in paths:self.assertIn('ra-renew',(TOP/name).read_text())
        elif (TOP/'tools/u30pro-package.py').exists():
            self.assertIn('stage-ra-renew.py',(TOP/'tools/u30pro-package.py').read_text())
            self.assertIn("'unisoc-ra-renew'",(TOP/'tools/u30pro-package.py').read_text())
        else:
            self.assertIn('stage-ra-renew.py',(TOP/'openwrt/build-rootfs.sh').read_text())
            self.assertIn('--overlay /in/overlay',(TOP/'openwrt/build-rootfs.sh').read_text())

    @unittest.skipUnless(hasattr(os,'timerfd_create'),'actual timerfd requires the Python 3.13 Linux target')
    def test_actual_boottime_timerfd_expires_and_disarms(self):
        fd=os.timerfd_create(time.CLOCK_BOOTTIME,flags=os.TFD_NONBLOCK)
        try:
            os.timerfd_settime(fd,flags=os.TFD_TIMER_ABSTIME,initial=API['boottime']()+.01)
            self.assertEqual(select.select([fd],[],[],2)[0],[fd])
            self.assertGreater(struct.unpack('=Q',os.read(fd,8))[0],0)
            os.timerfd_settime(fd,initial=0)
            self.assertEqual(select.select([fd],[],[],0)[0],[])
        finally:os.close(fd)


if __name__=='__main__':
    unittest.main(verbosity=2)
