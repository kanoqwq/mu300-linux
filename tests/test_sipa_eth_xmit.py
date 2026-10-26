"""sipa_eth_start_xmit (upstream/modules/sprd_modem/sipa_v3/sipa_eth.c), the mobile data TX path, compiled from the
source with stand-ins for the kernel calls it makes: a full IPA FIFO (NETDEV_TX_BUSY) hands the skb back to the qdisc
exactly as it came - no Ethernet header pushed again at every retry, the checksum seed back - and a failed linearize,
which frees the skb, says NETDEV_TX_OK (kanoqwq's clean-tf-7.2 149c9d2a, FINDINGS 102)."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from helpers import TOP

SRC = TOP / 'upstream/modules/sprd_modem/sipa_v3/sipa_eth.c'

HARNESS = r'''
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint16_t __sum16;
typedef uint16_t __be16;
typedef int netdev_tx_t;
enum { NETDEV_TX_OK = 0, NETDEV_TX_BUSY = 0x10 };
enum { DEV_ON = 1 };
enum { CHECKSUM_NONE = 0, CHECKSUM_PARTIAL = 3 };
#define ETH_HLEN 14
#define ETH_P_IP 0x0800
#define ETH_P_IPV6 0x86DD
#define IPPROTO_TCP 6
#define IPPROTO_UDP 17
#define NEXTHDR_TCP 6
#define NEXTHDR_UDP 17
#undef htons
#define htons(x) ((uint16_t)((((x) & 0xff) << 8) | (((x) >> 8) & 0xff)))
#define unlikely(x) (x)
#define likely(x) (x)
#define pr_err(...) ((void)0)
#define pr_err_ratelimited(...) ((void)0)
#define pr_info(...) ((void)0)
#define pr_debug(...) ((void)0)

struct sk_buff { unsigned char *head, *data; unsigned int len; int mac_header, network_header, transport_header;
                 int ip_summed; __be16 protocol; int freed; };
struct iphdr { uint8_t ihl_version, tos; uint16_t tot_len, id, frag; uint8_t ttl, protocol; uint16_t check;
               uint32_t saddr, daddr; };
struct ipv6hdr { uint32_t vtc; uint16_t payload_len; uint8_t nexthdr, hop_limit; uint8_t saddr[16], daddr[16]; };
struct tcphdr { uint16_t source, dest; uint32_t seq, ack; uint16_t flags, window, check, urg; };
struct udphdr { uint16_t source, dest, len, check; };
struct net_device_stats { unsigned long tx_dropped, tx_errors, tx_packets, tx_bytes; };
struct net_device { const char *name; int stopped; };
struct sipa_eth_init_data { int netid; int src_id; };
struct sipa_eth { int state; int nic_id; struct net_device *netdev; struct net_device_stats *stats;
                  struct sipa_eth_init_data *pdata; };
typedef struct { int locked; } spinlock_t;
static spinlock_t queue_lock;
#define spin_lock_irqsave(l, f) do { (f) = 1; (l)->locked++; } while (0)
#define spin_unlock_irqrestore(l, f) do { (void)(f); (l)->locked--; } while (0)

static struct sipa_eth the_eth;
static void *netdev_priv(struct net_device *d) { (void)d; return &the_eth; }
static int linearize_fails;
static int skb_linearize(struct sk_buff *s) { (void)s; return linearize_fails ? -12 : 0; }
static void dev_kfree_skb_any(struct sk_buff *s) { s->freed = 1; }
static unsigned int skb_headroom(struct sk_buff *s) { return (unsigned int)(s->data - s->head); }
static unsigned char *skb_push(struct sk_buff *s, unsigned int n) { s->data -= n; s->len += n; return s->data; }
static unsigned char *__skb_pull(struct sk_buff *s, unsigned int n) { s->data += n; s->len -= n; return s->data; }
static struct iphdr *ip_hdr(struct sk_buff *s) { return (struct iphdr *)(s->head + s->network_header); }
static struct ipv6hdr *ipv6_hdr(struct sk_buff *s) { return (struct ipv6hdr *)(s->head + s->network_header); }
static struct tcphdr *tcp_hdr(struct sk_buff *s) { return (struct tcphdr *)(s->head + s->transport_header); }
static struct udphdr *udp_hdr(struct sk_buff *s) { return (struct udphdr *)(s->head + s->transport_header); }
static void netif_stop_queue(struct net_device *d) { d->stopped = 1; }
static int busy_left;            /* sipa_nic_tx answers -EAGAIN this many times, then takes the skb */
static unsigned int sent_len; static unsigned char sent[256]; static uint16_t sent_check;
static int sipa_nic_tx(int nic, int dst, int netid, struct sk_buff *s) {
    (void)nic; (void)dst; (void)netid;
    if (busy_left > 0) { busy_left--; return -11; }
    sent_len = s->len; memcpy(sent, s->data, s->len); sent_check = tcp_hdr(s)->check; return 0;
}
static int sipa_nic_check_flow_ctrl(int nic) { (void)nic; return 1; }
static void sipa_nic_trigger_flow_ctrl_work(int nic, int ret) { (void)nic; (void)ret; }

FUNCTION

int main(int argc, char **argv) {
    static unsigned char buf[256];
    struct net_device dev = { "sipa_eth0", 0 };
    struct net_device_stats st = { 0 };
    struct sipa_eth_init_data pd = { 1, 2 };
    struct sk_buff skb = { 0 };
    int retries = argc > 1 ? atoi(argv[1]) : 0, rc, n = 0;
    the_eth.state = DEV_ON; the_eth.netdev = &dev; the_eth.stats = &st; the_eth.pdata = &pd;
    linearize_fails = argc > 2;
    /* a raw IPv4/TCP packet, as the rawip device gets it: mac_header == network_header, 64 bytes of headroom */
    skb.head = buf; skb.data = buf + 64; skb.len = 40;
    skb.mac_header = skb.network_header = 64; skb.transport_header = 64 + 20;
    skb.ip_summed = CHECKSUM_PARTIAL; skb.protocol = htons(ETH_P_IP);
    ip_hdr(&skb)->protocol = IPPROTO_TCP;
    tcp_hdr(&skb)->check = 0x1234;           /* the pseudo-header seed of CHECKSUM_PARTIAL */
    busy_left = retries;
    unsigned char *data0 = skb.data;
    while ((rc = sipa_eth_start_xmit(&skb, &dev)) == NETDEV_TX_BUSY) {
        n++;
        if (skb.data != data0 || skb.len != 40 || tcp_hdr(&skb)->check != 0x1234) {
            printf("changed after busy %d: off=%ld len=%u check=%x\n", n, (long)(skb.data - data0), skb.len,
                   tcp_hdr(&skb)->check);
            return 1;
        }
    }
    printf("rc=%d busy=%d freed=%d sent_len=%u sent_check=%x locked=%d dropped=%lu\n", rc, n, skb.freed,
           sent_len, sent_check, queue_lock.locked, st.tx_dropped);
    return 0;
}
'''


class SipaEthXmit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('no native C compiler')
        src = SRC.read_text()
        m = re.search(r'^static netdev_tx_t sipa_eth_start_xmit\(.*?^}\n', src, re.S | re.M)
        assert m, 'sipa_eth_start_xmit not found'
        cls.work = tempfile.TemporaryDirectory(prefix='sipa-xmit-')
        c = os.path.join(cls.work.name, 'x.c')
        with open(c, 'w') as f:
            f.write(HARNESS.replace('FUNCTION', m.group(0)))
        cls.exe = os.path.join(cls.work.name, 'x')
        probe = subprocess.run([cc, '-fsanitize=address,undefined', '-x', 'c', '-', '-o',
                                os.path.join(cls.work.name, 'p')], input='int main(void){return 0;}',
                               capture_output=True, text=True)
        san = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all'] if not probe.returncode else []
        r = subprocess.run([cc, '-std=gnu99', '-Wall', '-Wno-unused-function', '-Werror'] + san + [c, '-o', cls.exe],
                           capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_x(self, *args):
        r = subprocess.run([self.exe, *map(str, args)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return dict(kv.split('=') for kv in r.stdout.split())

    def test_sent_once_with_one_header(self):
        out = self.run_x(0)
        self.assertEqual((out['rc'], out['busy'], out['sent_len'], out['sent_check']), ('0', '0', '54', '0'))

    def test_busy_retries_give_the_skb_back_unchanged(self):
        for retries in (1, 3, 50):
            out = self.run_x(retries)
            # one Ethernet header in front of the 40 bytes, whatever the number of retries; the lock released
            self.assertEqual((out['rc'], out['busy'], out['sent_len'], out['locked']), ('0', str(retries), '54', '0'))

    def test_a_freed_skb_is_reported_sent(self):
        out = self.run_x(0, 'linearize-fails')
        self.assertEqual((out['rc'], out['freed'], out['dropped'], out['sent_len']), ('0', '1', '1', '0'))


if __name__ == '__main__':
    unittest.main()
