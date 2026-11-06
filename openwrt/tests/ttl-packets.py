#!/usr/bin/env python3
"""Explicit root-only integration test, isolated network namespace, no real links.
Run: python3 ttl-packets.py /path/to/ttl-engine
Requires Linux netdev egress, nftables, ip-full and network namespaces.
"""
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time


def run(*args, **kw):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kw).stdout


def checksum(data):
    if len(data) % 2: data += b'\0'
    n = sum(struct.unpack('!%dH' % (len(data)//2), data))
    while n >> 16: n = (n & 65535) + (n >> 16)
    return (~n) & 65535


def address(value): return ipaddress.ip_address(value).packed

def mac(name):
    data = json.loads(run('ip', '-j', 'link', 'show', 'dev', name))[0]['address']
    return bytes.fromhex(data.replace(':', ''))


def packet(version, src, dst, sport, dport, payload, ttl=31):
    udp = struct.pack('!HHHH', sport, dport, 8+len(payload), 0) + payload
    if version == 6:
        pseudo = address(src)+address(dst)+struct.pack('!I3xB', len(udp), 17)
        crc = checksum(pseudo+udp) or 65535
        udp = udp[:6]+struct.pack('!H', crc)+udp[8:]
        return struct.pack('!IHBB', 6 << 28, len(udp), 17, ttl)+address(src)+address(dst)+udp
    header = struct.pack('!BBHHHBBH4s4s', 0x45,0,20+len(udp),0,0,ttl,17,0,address(src),address(dst))
    return header[:10]+struct.pack('!H', checksum(header))+header[12:]+udp


def recv(sock, token, timeout=2, verify_udp=True):
    end = time.monotonic()+timeout
    seen = []
    while time.monotonic() < end:
        if not select.select([sock], [], [], max(0,end-time.monotonic()))[0]: break
        data = sock.recv(65535)
        if token not in data:
            seen.append(data.hex()[:160]);continue
        if data[12:14] == b'\x08\x00':
            ip = data[14:]; ihl = (ip[0]&15)*4
            assert checksum(ip[:ihl]) == 0, 'IPv4 checksum broken'
            return ip[8]
        if data[12:14] == b'\x86\xdd':
            ip = data[14:]
            if ip[6] == 17 and verify_udp:
                pseudo = ip[8:40]+struct.pack('!I3xB', len(ip)-40, 17)
                assert checksum(pseudo+ip[40:]) == 0, 'IPv6 UDP checksum broken'
            return ip[7]
    raise AssertionError('Packet was not delivered: '+repr(token)+'; observed='+repr(seen))


def main():
    engine = str(Path(sys.argv[1]).resolve())
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(0x40000000):
        raise OSError(ctypes.get_errno(), 'Cannot create isolated network namespace')
    with tempfile.TemporaryDirectory(prefix='ttl-packets-') as directory:
        root = Path(directory)
        for name in ('bin', 'net/sipa_eth0', 'etc'): (root/name).mkdir(parents=True)
        # Firewall settings are private fixtures; nft/ip operate only in this namespace.
        uci = root/'bin/uci'
        uci.write_text('#!/bin/sh\ncase "$*" in *flow_offloading_hw) echo 0;; *flow_offloading) echo 1;; *) exit 1;; esac\n')
        uci.chmod(0o755)
        # SIPA's global notifier assumes any sipa_eth* device belongs to its
        # driver, even in another namespace. Never invent such a kernel link.
        # Only map the generated nft device binding to our isolated ttl_wan.
        nft = root/'bin/nft'
        nft.write_text("#!/bin/sh\nif [ \"$1\" = -f ]; then\n sed 's/device \"sipa_eth0\"/device \"ttl_wan\"/g' | "+shlex.quote(shutil.which('nft'))+" \"$@\"\nelse\n exec "+shlex.quote(shutil.which('nft'))+" \"$@\"\nfi\n")
        nft.chmod(0o755)
        env = dict(os.environ, PATH=str(root/'bin')+':'+os.environ['PATH'],
                   MU300_TTL_SYS_NET=str(root/'net'), MU300_TTL_CONF=str(root/'etc/ttl.conf'),
                   MU300_TTL_LOCK=str(root/'lock'))
        run('ip','link','set','lo','up')
        for router,peer in [('ttl_lan','ttl_client'),('ttl_wan','ttl_server')]:
            run('ip','link','add',router,'type','veth','peer','name',peer)
            for name in (router,peer): run('ip','link','set',name,'up')
        for dev,v4,v6 in [('ttl_lan','10.202.1.1/24','2001:db8:100::1/64'),
                          ('ttl_wan','10.202.2.1/24','2001:db8:200::1/64')]:
            run('ip','addr','add',v4,'dev',dev)
            run('ip','-6','addr','add',v6,'dev',dev,'nodad')
        Path('/proc/sys/net/ipv4/ip_forward').write_text('1')
        Path('/proc/sys/net/ipv6/conf/all/forwarding').write_text('1')
        for dev,peer,v4,v6 in [('ttl_lan','ttl_client','10.202.1.2','2001:db8:100::2'),
                               ('ttl_wan','ttl_server','10.202.2.2','2001:db8:200::2')]:
            peer_mac = ':'.join('%02x'%b for b in mac(peer))
            for ip in (v4,v6): run('ip','neigh','replace',ip,'lladdr',peer_mac,'nud','permanent','dev',dev)
        run('nft','-f','-',input="""table netdev ttl_peers {
 chain client { type filter hook ingress device \"ttl_client\" priority -500; policy drop; }
 chain server { type filter hook ingress device \"ttl_server\" priority -500; policy drop; }
}
table inet ttl_test {
 flowtable fast { hook ingress priority 0; devices = { ttl_lan, ttl_wan }; counter; }
 chain forward { type filter hook forward priority 0; policy accept; meta l4proto udp flow add @fast; }
 chain srcnat { type nat hook postrouting priority srcnat; policy accept; ip saddr 10.202.1.0/24 oifname "ttl_wan" masquerade; }
 chain post { type filter hook postrouting priority 0; policy accept;
  ip daddr 10.202.2.2 udp dport 42000 counter comment "post4"
  ip6 daddr 2001:db8:200::2 udp dport 42000 counter comment "post6"
 }
}
""")
        run('sh',engine,'set','65',env=env)
        results = {}
        client = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));client.bind(('ttl_client',0))
        server = socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));server.bind(('ttl_server',0))
        for version,src,dst in [(4,'10.202.1.2','10.202.2.2'),(6,'2001:db8:100::2','2001:db8:200::2')]:
            eth = b'\x08\x00' if version == 4 else b'\x86\xdd'
            forward = mac('ttl_lan')+mac('ttl_client')+eth
            reverse = mac('ttl_wan')+mac('ttl_server')+eth
            token = ('ttl%d-'%version).encode()
            client.send(forward+packet(version,src,dst,41000,42000,token+b'first'))
            assert recv(server,token+b'first') == 65
            server.send(reverse+packet(version,dst,'10.202.2.1' if version == 4 else src,42000,41000,token+b'reply',52))
            try: assert recv(client,token+b'reply') == 51
            except AssertionError:
                print(run('nft','list','ruleset'));print(run('ip','-4','route'));print(run('ip','neigh'));raise
            for n in range(40):
                payload = token+str(n).encode()+b'end'
                client.send(forward+packet(version,src,dst,41000,42000,payload))
                assert recv(server,payload) == 65
            # An existing offloaded flow also sees live TTL changes/removal.
            run('sh',engine,'set','128',env=env)
            client.send(forward+packet(version,src,dst,41000,42000,token+b'change'))
            assert recv(server,token+b'change') == 128
            run('sh',engine,'off',env=env)
            client.send(forward+packet(version,src,dst,41000,42000,token+b'off'))
            assert recv(server,token+b'off') == 30
            run('sh',engine,'set','65',env=env)
            rules = json.loads(run('nft','-j','list','chain','inet','ttl_test','post'))['nftables']
            post = [r['rule'] for r in rules if 'rule' in r and r['rule'].get('comment') == 'post%d'%version][0]
            count = [e['counter']['packets'] for e in post['expr'] if 'counter' in e][0]
            assert 0 < count < 40, 'Software flowtable did not bypass postrouting: '+str(count)
            results['ipv%d'%version] = {'nat':version == 4,'forwarded':43,'postrouting_packets':count,'ttl':65,'changed':128,'disabled':30}
        # Locally generated control traffic and non-cellular output keep their TTL.
        send = socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
        send.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_IF,socket.if_nametoindex('ttl_wan'))
        send.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_HOPS,1)
        send.sendto(b'ttl-multicast',('ff02::114',43000,0,socket.if_nametoindex('ttl_wan')))
        assert recv(server,b'ttl-multicast',verify_udp=False) == 1
        send4 = socket.socket(socket.AF_INET,socket.SOCK_DGRAM);send4.setsockopt(socket.IPPROTO_IP,socket.IP_TTL,50)
        send4.sendto(b'ttl-lan-unchanged',('10.202.1.2',43000))
        assert recv(client,b'ttl-lan-unchanged') == 50
        # Real NDP solicitation must keep RFC-required 255 hops.
        send.sendto(b'ndp-trigger',('2001:db8:200::99',43000))
        end=time.monotonic()+2;ndp=False
        while time.monotonic()<end:
            if not select.select([server],[],[],.2)[0]:continue
            data=server.recv(65535)
            if data[12:14]==b'\x86\xdd' and data[20]==58 and data[54]==135:
                assert data[21]==255;ndp=True;break
        assert ndp,'No neighbor solicitation captured'
        results['control']={'multicast_hops':1,'neighbor_discovery_hops':255,'lan_ttl':50}
        print(json.dumps(results,sort_keys=True))


if __name__ == '__main__': main()
