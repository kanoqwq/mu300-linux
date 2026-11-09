"""Execute production TX completion/submit paths with delayed reclamation."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

WLAN = Path(os.environ.get('MU300_TXC_SOURCE', TOP / 'upstream/modules/sprd_wlan_combo/sc2355'))


def function(text, name):
    m = re.search(r'(?m)^(?:(?:static|inline)\s+)*(?:int|void)\s+' + name + r'\(', text)
    if not m:
        raise AssertionError('missing production function: ' + name)
    return text[m.start():text.index('\n}', m.start()) + 2]


class TxcReclaim(unittest.TestCase):
    def run_c(self, code):
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('host C compiler required')
        with tempfile.TemporaryDirectory(prefix='mu300-txc-') as tmp:
            src, binary = Path(tmp) / 'test.c', Path(tmp) / 'test'
            src.write_text(code)
            p = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function',
                                '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                                str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            p = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 0, p.stderr)

    def test_rx_returns_credit_only_after_actual_reclaim_without_work_allocation(self):
        text = (WLAN / 'rx.c').read_text()
        old = 'void sc2355_tx_free_data_num(' in text
        early = function(text, 'sc2355_tx_free_data_num') if old else ''
        dispatch = function(text, 'sc2355_rx_mh_addr_process')
        self.run_c(r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#define pr_debug(...) ((void)0)
#define pr_err(...) ((void)0)
#define SPRD_PCIE_TX_FREE_BUF 23
#define atomic_sub(n,p) (*(p)-=(n))
#define atomic_read(p) (*(p))
struct tx_mgmt { struct { int free_num; } xmit_msg_list; };
struct sprd_hif { int hif_offset; void *priv, *tx_mgmt; };
struct rx_mgmt { struct sprd_hif *hif; int mm_entry; };
struct sprd_common_hdr { unsigned char reserv; };
struct txc_addr_buff { unsigned char reserv; unsigned short number; };
struct sprd_work { int id,len; unsigned char data[32]; };
static struct tx_mgmt tx;
static int released,wakeups,disposed,allocations,rx_events;
static void sc2355_tx_up(struct tx_mgmt *t) {
    assert(released==4); assert(t->xmit_msg_list.free_num==0); wakeups++;
}
static struct sprd_work *sprd_alloc_work(int len) { allocations++; return NULL; }
static void sprd_queue_work(void *priv,struct sprd_work *w) { assert(0); }
static void sc2355_free_data(void *p,int type) { assert(type==7); disposed++; }
static void sc2355_mm_mh_data_event_process(int *mm,void *p,int len,int type) { rx_events++; }
static int sc2355_tx_free_pcie_data(unsigned char *data,int len) {
    assert(len==sizeof(struct txc_addr_buff)); assert(tx.xmit_msg_list.free_num==4);
    released=4; tx.xmit_msg_list.free_num-=4; sc2355_tx_up(&tx); return 0;
}
''' + early + '\n' + dispatch + r'''
int main(void) {
    struct sprd_hif hif={0}; struct rx_mgmt rx={.hif=&hif};
    struct txc_addr_buff event={.number=4}; hif.tx_mgmt=&tx; tx.xmit_msg_list.free_num=4;
    sc2355_rx_mh_addr_process(&rx,&event,sizeof(event),7);
    assert(released==4 && disposed==1 && wakeups==1 && allocations==0);
    event.reserv=1;
    sc2355_rx_mh_addr_process(&rx,&event,sizeof(event),7);
    assert(rx_events==1 && disposed==2 && wakeups==1);
    return 0;
}
''')

    def test_completion_accounting_counts_reclaimed_messages_and_wakes_after_free(self):
        text = (WLAN / 'pcie.c').read_text()
        body = function(text, 'sc2355_tx_free_pcie_data')
        has_len = re.search(r'sc2355_tx_free_pcie_data\([^)]*\bint\s+len', body) is not None
        call = 'sc2355_tx_free_pcie_data((unsigned char *)&event, sizeof(event))' if has_len else 'sc2355_tx_free_pcie_data((unsigned char *)&event)'
        self.run_c(r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdbool.h>
#define smp_load_acquire(p) (*(p))
static void debug(const char *format,...) {}
#define pr_debug(...) debug(__VA_ARGS__)
#define pr_info(...) ((void)0)
#define pr_err_ratelimited(...) ((void)0)
#define SPRD_MODE_NONE 0
#define SPRD_PHYS_LEN 5
#define SPRD_MAX_DATA_TXLEN 2048
#define DMA_TO_DEVICE 1
#define atomic_sub(n,p) (*(p)-=(n))
#define atomic_read(p) (*(p))
struct sprd_priv { int unused; };
struct sprd_msg { int id,len; unsigned long pcie_addr; };
struct tx_mgmt { int net_stopped; unsigned long txc_num,tx_num; struct { int free_num; } xmit_msg_list; };
struct sprd_hif { struct tx_mgmt *tx_mgmt; struct sprd_priv *priv; struct {int dev;} *pdev; };
struct txc_addr_buff { unsigned char flags[3]; unsigned short number,rsvd; } __attribute__((packed));
static struct sprd_msg messages[3];
static struct tx_mgmt tx;
static struct sprd_hif hif;
static int reclaimed, wakes, net_wakes, attempts;
static unsigned int claimed;
static unsigned char eventbuf[64];
static struct sprd_hif *sc2355_pcie_get_hif(void){return &hif;}
static int printk_timed_ratelimit(unsigned long *p,int ms){return 0;}
static void *sc2355_mm_phys_to_virt(void *d,unsigned long addr,int n,int direction,bool mh){
    assert(addr>=0x1000 && addr<=0x1002); return &messages[addr-0x1000];
}
#define RESTORE_ADDR(out,p,n) ((out)=(struct sprd_msg *)(p))
static int free_message(struct tx_mgmt *t,struct sprd_msg *m){
    attempts++;
    assert(t->xmit_msg_list.free_num==(int)claimed); /* no early credit */
    if(m->id<0)return -1;
    m->id=-1; reclaimed++;return 0;
}
static int sprd_tx_free_txc_msg(struct tx_mgmt *t,unsigned long addr,unsigned long *start){
    assert(addr>=0x1000 && addr<=0x1002); return free_message(t,&messages[addr-0x1000]);
}
static void sc2355_tx_up(struct tx_mgmt *t){assert(reclaimed==2 && t->xmit_msg_list.free_num==1);wakes++;}
static void sprd_net_flowcontrl(struct sprd_priv *p,int mode,bool on){assert(reclaimed==2);net_wakes++;}
''' + ('' if has_len else '#define sprd_tx_free_txc_msg(t,m) free_message((t),(m))\n') + body + '\n' + r'''
int main(void){
    struct { struct txc_addr_buff head; unsigned char addr[3][5]; } __attribute__((packed)) event={0};
    struct {int dev;} device={0}; struct sprd_priv priv={0};
    hif.tx_mgmt=&tx;hif.pdev=(void *)&device;hif.priv=&priv;
    tx.net_stopped=1;tx.xmit_msg_list.free_num=3;claimed=3;
    for(int i=0;i<3;i++)messages[i].id=i;
    event.head.number=3;
    for(int i=0;i<3;i++){unsigned long addr=0x1010+(i==2?0:i);memcpy(event.addr[i],&addr,5);}
    int result=CALL;
    assert(result==0 && reclaimed==2 && attempts==3 && wakes==1 && net_wakes==1);
    assert(tx.xmit_msg_list.free_num==1 && tx.txc_num==2 && !tx.net_stopped);
''' .replace('CALL',call) + (r'''
    /* A truncated/overlong receipt must not manufacture credits or read past its buffer. */
    event.head.number=4;
    assert(sc2355_tx_free_pcie_data((unsigned char *)&event,sizeof(event))<0);
    assert(sc2355_tx_free_pcie_data((unsigned char *)&event,1)<0);
    assert(reclaimed==2 && wakes==1 && tx.xmit_msg_list.free_num==1);
    hif.tx_mgmt=NULL;
    assert(sc2355_tx_free_pcie_data((unsigned char *)&event,sizeof(event))<0);
''' if has_len else '') + r'''
    (void)eventbuf;
    return 0;
}
''')

    def test_only_owned_dma_mappings_are_unmapped_with_their_original_length(self):
        body = function((WLAN / 'pcie.c').read_text(), 'sprd_tx_free_txc_msg')
        self.run_c(r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define MORE_DEBUG 1
#define DMA_TO_DEVICE 1
#define SPRD_PHYS_MASK ((1UL<<40)-1)
#define SPRD_MH_ADDRESS_BIT (1UL<<39)
#define pr_debug_ratelimited(...) ((void)0)
#define spin_lock_irqsave(p,f) ((void)(p),(void)(f))
#define spin_unlock_irqrestore(p,f) ((void)(p),(void)(f))
struct list_head {int active;};
struct sprd_msg {struct list_head list;unsigned long pcie_addr,tx_start_time;int len;void *node,*skb,*data,*tran_data,*msglist;};
struct sprd_hif {struct {int dev;} *pdev;};
struct tx_mgmt {struct sprd_hif *hif;struct {int free_lock;struct list_head to_free_list;} xmit_msg_list;};
static struct sprd_msg messages[2];
#define list_for_each_entry(pos,head,member) for(pos=((void)(head),messages);pos<messages+2;pos++) if(pos->list.active)
static void list_del(struct list_head *p){assert(p->active);p->active=0;}
static int unmaps,returned,freed_skb,freed_msg;
static void *sc2355_mm_phys_to_virt(void *dev,unsigned long addr,int len,int direction,bool mh){
    assert(direction==DMA_TO_DEVICE && !mh);
    assert((addr==(SPRD_MH_ADDRESS_BIT|0x1000) && len==89) || (addr==0x2000 && len==1500));unmaps++;return NULL;
}
static void pcie_free_tx_buf(void *node){assert(node);returned++;}
static void dev_kfree_skb(void *p){assert(p);freed_skb++;}
static void sprd_free_msg(struct sprd_msg *msg,void *list){assert(!msg->node && !msg->skb && !msg->pcie_addr && !msg->len);freed_msg++;}
''' + body + r'''
int main(void){
    struct {int dev;} dev={0};struct sprd_hif hif={.pdev=(void *)&dev};struct tx_mgmt tx={.hif=&hif};
    messages[0]=(struct sprd_msg){.list={1},.pcie_addr=SPRD_MH_ADDRESS_BIT|0x1000,.len=89,.node=&dev,.tx_start_time=42};
    messages[1]=(struct sprd_msg){.list={1},.pcie_addr=0x2000,.len=1500,.skb=&dev};
    unsigned long start=0;
    assert(sprd_tx_free_txc_msg(&tx,0x1000,&start)==0 && start==42);
    assert(sprd_tx_free_txc_msg(&tx,SPRD_MH_ADDRESS_BIT|0x1000,&start)<0); /* duplicate */
    assert(sprd_tx_free_txc_msg(&tx,0x9999,&start)<0); /* unknown */
    assert(unmaps==1 && returned==1 && freed_msg==1 && !freed_skb);
    assert(sprd_tx_free_txc_msg(&tx,SPRD_MH_ADDRESS_BIT|0x2000,&start)==0);
    assert(unmaps==2 && freed_msg==2 && freed_skb==1 && start==42);
    return 0;
}
''')

    def test_allocation_failure_cannot_be_submitted_as_a_stale_dma_address(self):
        body = function((WLAN / 'pcie.c').read_text(), 'sc2355_pcie_hif_tx_list')
        start = body.index('\t\t\tif (msg_pos->skb)')
        end = body.index('\n\t\t\tpr_debug("debug pcie addr:', start)
        prepare = body[start:end]
        self.run_c(r'''
#include <assert.h>
#include <errno.h>
struct msg {void *skb;int prepared;} item,*msg_pos=&item;
static int result,freed;
static int pcie_skb_to_tx_buf(void *h,struct msg *m){if(!result){m->prepared=1;m->skb=0;}return result;}
static void sprdwl_mbuf_list_free(void *h,void *head,void *tail,int n){freed++;}
static int submit(void){void *hif=0,*head=0,*tail=0;int pcie_count=1,ret=0;
    (void)hif;(void)head;(void)tail;(void)pcie_count;(void)ret;
''' + prepare + r'''
    assert(msg_pos->prepared); /* address must never reach the bus after a failed conversion */
    return 0;
}
int main(void){
    item.skb=&item;result=-ENOMEM;
    assert(submit()==-ENOMEM && item.skb==&item && !item.prepared && freed==1);
    result=0;assert(submit()==0 && item.prepared && !item.skb);
    return 0;
}
''')


if __name__ == '__main__':
    unittest.main()
