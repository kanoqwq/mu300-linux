"""Execute the SIPA ndo_start_xmit path under repeated qdisc retries."""
import unittest

from helpers import TOP
import test_wifi_tx_retry as retry


class SipaRetry(unittest.TestCase):
    run_c = retry.TxRetry.run_c

    def test_busy_retains_packet_and_checksum_until_accepted(self):
        src = TOP / 'upstream/modules/sprd_modem/sipa_v3/sipa_eth.c'
        text = src.read_text()
        start = text.index('static netdev_tx_t sipa_eth_start_xmit(')
        body = text[start:text.index('\n}', start) + 2]
        self.run_c(r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <arpa/inet.h>
typedef uint16_t __sum16;
typedef int netdev_tx_t;
#define DEV_ON 1
#define NETDEV_TX_OK 0
#define NETDEV_TX_BUSY 16
#define CHECKSUM_PARTIAL 1
#define ETH_P_IP 0x800
#define ETH_P_IPV6 0x86dd
#define ETH_HLEN 14
#define NEXTHDR_TCP IPPROTO_TCP
#define NEXTHDR_UDP IPPROTO_UDP
#define likely(x) (x)
#define unlikely(x) (x)
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)
#define pr_err_ratelimited(...) ((void)0)
#define spin_lock_irqsave(lock, flags) ((void)(lock), (flags)=0x55)
#define spin_unlock_irqrestore(lock, flags) ((void)(lock), assert((flags)==0x55))
static int queue_lock;
/* Allows the same harness to demonstrate failure of the original function. */
static unsigned long queue_lock_flags __attribute__((unused));
struct iphdr { unsigned char protocol; };
struct ipv6hdr { unsigned char nexthdr; };
struct tcphdr { __sum16 check; };
struct udphdr { __sum16 check; };
struct sk_buff {
    unsigned char storage[256], *head, *data;
    unsigned int len, mac_header, network_header, transport_header;
    unsigned short protocol;
    int ip_summed;
};
#define ip_hdr(s) ((struct iphdr *)((s)->head+(s)->network_header))
#define ipv6_hdr(s) ((struct ipv6hdr *)((s)->head+(s)->network_header))
#define tcp_hdr(s) ((struct tcphdr *)((s)->head+(s)->transport_header))
#define udp_hdr(s) ((struct udphdr *)((s)->head+(s)->transport_header))
struct net_device_stats {
    unsigned long tx_dropped,tx_errors,tx_packets,tx_bytes;
};
struct sipa_eth_init_data { int src_id,netid; };
struct sipa_eth {
    struct sipa_eth_init_data *pdata;
    struct net_device_stats *stats;
    int state,nic_id;
};
struct net_device { struct sipa_eth *priv; };
static void *netdev_priv(struct net_device *d) { return d->priv; }
static int linear_fail, cow_fail, frees, result, pushed, flow_requests, queue_stops;
static unsigned char *original_data;
static unsigned int original_len;
static __sum16 *check_to_zero;
static int skb_linearize(struct sk_buff *s) { return linear_fail; }
static void dev_kfree_skb_any(struct sk_buff *s) { ++frees; }
static unsigned int skb_headroom(struct sk_buff *s) { return s->data-s->head; }
static int skb_cow_head(struct sk_buff *s,unsigned int n) {
    if(cow_fail)return cow_fail;
    unsigned room=skb_headroom(s);
    if(room<n) {
        unsigned delta=n-room;memmove(s->data+delta,s->data,s->len);
        s->data+=delta;s->mac_header+=delta;s->network_header+=delta;s->transport_header+=delta;
    }
    return 0;
}
static void skb_push(struct sk_buff *s,unsigned int n) {
    assert(skb_headroom(s)>=n);s->data-=n;s->len+=n;
}
static __attribute__((unused)) void skb_pull_inline(struct sk_buff *s,unsigned int n) {
    assert(s->len>=n);s->data+=n;s->len-=n;
}
static int sipa_nic_tx(int nic,int src,int netid,struct sk_buff *s) {
    assert(s->data==original_data-(pushed?ETH_HLEN:0));
    assert(s->len==original_len+(pushed?ETH_HLEN:0));
    if (check_to_zero) assert(*check_to_zero==0);
    return result;
}
static int sipa_nic_check_flow_ctrl(int nic) { return 1; }
static void netif_stop_queue(struct net_device *d) { ++queue_stops; }
static void sipa_nic_trigger_flow_ctrl_work(int nic,int error) {
    assert(error<0);++flow_requests;
}
''' + body + r'''
int main(void) {
    for (int raw=1;raw>=0;raw--)
    for (int v6=0;v6<2;v6++)
    for (int udp=0;udp<2;udp++)
    for (int partial=0;partial<2;partial++) {
        struct net_device_stats stats={0};
        struct sipa_eth_init_data pdata={6,0};
        struct sipa_eth eth={&pdata,&stats,DEV_ON,0};
        struct net_device dev={&eth};
        struct sk_buff skb;
        memset(&skb,0,sizeof(skb));memset(skb.storage,0xa5,sizeof(skb.storage));
        skb.head=skb.storage;skb.data=skb.storage+(raw?64:50);
        skb.len=raw?64:78;skb.mac_header=raw?64:50;skb.network_header=64;
        skb.transport_header=v6?104:84;
        skb.protocol=htons(v6?ETH_P_IPV6:ETH_P_IP);
        skb.ip_summed=partial;
        ip_hdr(&skb)->protocol=udp?IPPROTO_UDP:IPPROTO_TCP;
        tcp_hdr(&skb)->check=0x1234;
        original_data=skb.data;original_len=skb.len;pushed=raw;
        check_to_zero=partial?&tcp_hdr(&skb)->check:NULL;
        unsigned char original[256];memcpy(original,skb.storage,sizeof(original));
        int free_before=frees;
        for (int retry=0;retry<50;retry++) {
            result=retry%2 ? -EAGAIN:-EINPROGRESS;
            assert(sipa_eth_start_xmit(&skb,&dev)==NETDEV_TX_BUSY);
            assert(skb.data==original_data && skb.len==original_len);
            assert(!memcmp(original,skb.storage,sizeof(original)));
            assert(frees==free_before && stats.tx_packets==0);
        }
        result=0;
        assert(sipa_eth_start_xmit(&skb,&dev)==NETDEV_TX_OK);
        assert(stats.tx_packets==1 && stats.tx_bytes==original_len);
        assert(stats.tx_errors==0 && frees==free_before);
        assert(skb.data==original_data-(raw?ETH_HLEN:0));

        /* A consumed skb must not be offered to qdisc again. */
        linear_fail=-ENOMEM;
        assert(sipa_eth_start_xmit(&skb,&dev)==NETDEV_TX_OK);
        assert(frees==free_before+1 && stats.tx_dropped==1);
        linear_fail=0;eth.state=0;
        assert(sipa_eth_start_xmit(&skb,&dev)==NETDEV_TX_OK);
        assert(frees==free_before+2 && stats.tx_dropped==2);
    }
    assert(flow_requests==16*50 && queue_stops==16*50);
    for(unsigned room=0;room<64;room++) {
        struct net_device_stats stats={0};struct sipa_eth_init_data pd={0};
        struct sipa_eth eth={&pd,&stats,DEV_ON,0};struct net_device dev={&eth};
        struct sk_buff s={0};s.head=s.storage;s.data=s.head+room;s.len=64;
        s.mac_header=s.network_header=room;s.transport_header=room+20;
        memset(s.data,0x5a,64);check_to_zero=NULL;pushed=1;
        original_data=s.head+(room<14?14:room);original_len=64;
        for(int retry=0;retry<16;retry++) {
            result=retry&1?-EAGAIN:-EINPROGRESS;
            assert(sipa_eth_start_xmit(&s,&dev)==NETDEV_TX_BUSY);
            assert(s.data==original_data && s.len==64);
            for(unsigned i=0;i<64;i++)assert(s.data[i]==0x5a);
        }
        int before=frees;result=-EIO;
        assert(sipa_eth_start_xmit(&s,&dev)==NETDEV_TX_OK);
        assert(frees==before+1 && stats.tx_dropped==1 && stats.tx_errors==1);
        cow_fail=-ENOMEM;before=frees;
        assert(sipa_eth_start_xmit(&s,&dev)==NETDEV_TX_OK && frees==before+1);
        cow_fail=0;
    }
    return 0;
}
''')
