#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stddef.h>
#include <errno.h>
#include <pthread.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef uintptr_t dma_addr_t;
#define DMA_TO_DEVICE 1
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define SIPA_LEAVE_FLOWCTRL 1
struct skb_shared_info { void *frag_list; unsigned nr_frags; int dataref; };
static struct skb_shared_info shared_info;
#define skb_shinfo(s) (&shared_info)
#define NIC_REQUEUE_SKB 1
#define dev_err(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define atomic_add(n,p) (*(p)+=(n))
#define atomic_read(p) (*(p))
#define spin_lock_irqsave(lock,flags) ((flags)=0,assert(pthread_mutex_lock(lock)==0))
#define spin_unlock_irqrestore(lock,flags) assert(pthread_mutex_unlock(lock)==0)
struct list_head { struct list_head *next,*prev; };
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry_safe(p,n,h,m) \
 for(p=container_of((h)->next,typeof(*p),m),n=container_of((p)->m.next,typeof(*p),m); \
     &(p)->m!=(h);p=n,n=container_of((n)->m.next,typeof(*n),m))
static void init_list(struct list_head *h) { h->next=h->prev=h; }
static bool list_empty(struct list_head *h) { return h->next==h; }
static void list_del(struct list_head *p) { p->next->prev=p->prev;p->prev->next=p->next; }
static void list_add_tail(struct list_head *p,struct list_head *h) { p->next=h;p->prev=h->prev;h->prev->next=p;h->prev=p; }
struct sk_buff { unsigned len;bool freed,mapped,requeued; };
struct sipa_skb_dma_addr_pair { struct list_head list;dma_addr_t dma_addr;struct sk_buff *skb;bool need_unmap; };
struct sipa_node_desc_tag { dma_addr_t address;unsigned err_code,flow_ctrl,net_id,dst; };
struct sipa_endpoint { struct { int idx; struct { int fifo_depth; } rx_fifo; } send_fifo; };
struct sipa_skb_sender { void *dev;pthread_mutex_t send_lock;struct list_head sending_list,pair_free_list;struct sipa_endpoint *ep;int left_cnt; bool free_notify_net; };
struct sipa_plat_drv_cfg { struct sipa_skb_sender *sender; };
static struct sipa_plat_drv_cfg ipa;
static struct sk_buff skbs[4];
static struct sipa_node_desc_tag nodes[8];
static unsigned total,rd,syncs,unmaps,frees,requeues;
static int fault=-1,test;
static struct sipa_plat_drv_cfg *sipa_get_ctrl_pointer(void) { return &ipa; }
static int net_ratelimit(void) { return 1; }
static unsigned skb_headroom(struct sk_buff *s) { return 32; }
static void assert_unlocked(void) { assert(pthread_mutex_trylock(&ipa.sender->send_lock)==0);pthread_mutex_unlock(&ipa.sender->send_lock); }
static void dma_unmap_single(void *d,dma_addr_t addr,unsigned len,int dir) {
    unsigned i=addr/64-1;assert(i<4 && dir==DMA_TO_DEVICE && skbs[i].mapped);skbs[i].mapped=false;++unmaps;
}
static void dev_kfree_skb_any(struct sk_buff *s) { assert(!s->freed && !s->mapped);s->freed=true;++frees; }
static void sipa_inform_evt_to_nics(struct sipa_skb_sender *sender,int evt) {
    assert_unlocked();sender->free_notify_net=false;
}
static void sipa_hal_cmn_fifo_get_filled_depth(void *d,int id,void *rx,u32 *num) { *num=total-rd; }
static void sipa_hal_sync_node_from_tx_fifo(void *d,int id,unsigned n) {
    assert_unlocked();++syncs;
    if(test==3 && syncs==2)nodes[rd].address=64;
}
static struct sipa_node_desc_tag *sipa_hal_get_tx_node_rptr(void *d,int id,unsigned i) {
    if((int)(rd+i)==fault)return NULL;
    assert(rd+i<total);return &nodes[rd+i];
}
static void sipa_hal_add_tx_fifo_rptr(void *d,int id,unsigned n) { assert(n<=total-rd);rd+=n; }
/* PRODUCTION_FUNCTIONS */
static void *complete_same_packet(void *node) {
    return (void *)(uintptr_t)sipa_do_free_skb(node);
}
int main(int argc,char **argv) {
    assert(argc==2);test=atoi(argv[1]);
    struct sipa_endpoint ep={0};struct sipa_skb_sender sender={.ep=&ep};ipa.sender=&sender;
    pthread_mutex_init(&sender.send_lock,NULL);init_list(&sender.sending_list);init_list(&sender.pair_free_list);
    struct sipa_skb_dma_addr_pair pairs[4]={0};
    if(test==8) {
        for(unsigned round=0;round<2000;round++) {
            init_list(&sender.sending_list);init_list(&sender.pair_free_list);
            skbs[0]=(struct sk_buff){.len=100,.mapped=true};
            pairs[0]=(struct sipa_skb_dma_addr_pair){.dma_addr=64,.skb=&skbs[0],.need_unmap=true};
            list_add_tail(&pairs[0].list,&sender.sending_list);
            nodes[0]=(struct sipa_node_desc_tag){.address=64};
            pthread_t a,b;void *ra,*rb;
            assert(pthread_create(&a,NULL,complete_same_packet,nodes)==0);
            assert(pthread_create(&b,NULL,complete_same_packet,nodes)==0);
            pthread_join(a,&ra);pthread_join(b,&rb);
            assert((uintptr_t)ra+(uintptr_t)rb==1);
            assert(skbs[0].freed && !skbs[0].mapped && list_empty(&sender.sending_list));
        }
        assert(frees==2000 && unmaps==2000);pthread_mutex_destroy(&sender.send_lock);return 0;
    }
    unsigned owned=test==6?0:test==7?2:3;
    for(unsigned i=0;i<owned;i++) {
        skbs[i].len=100;skbs[i].mapped=!(test==5 && i==0);
        pairs[i].dma_addr=(i+1)*64;pairs[i].skb=&skbs[i];pairs[i].need_unmap=skbs[i].mapped;
        list_add_tail(&pairs[i].list,&sender.sending_list);
    }
    total=owned;
    for(unsigned i=0;i<owned;i++)nodes[i].address=(i+1)*64;
    if(test==1){nodes[3]=nodes[2];nodes[2]=nodes[1];nodes[1]=nodes[0];total=4;}
    if(test==2){nodes[3]=nodes[2];nodes[2]=nodes[1];nodes[1].address=0xdead;total=4;}
    if(test==3)nodes[0].address=0xdead;
    if(test==4)fault=1;
    if(test==9)nodes[0].err_code=1;
    if(test==10){nodes[3]=nodes[2];nodes[2]=nodes[1];nodes[1].address=0;total=4;}
    if(test==11) {
        fault=0;sipa_free_sent_items();
        assert(!rd && !sender.left_cnt && !unmaps && !frees);
        fault=-1;
    }
    if(test==6){total=1;nodes[0].address=0xdead;}
    if(test==7){nodes[0].address=0xdead;nodes[1].address=128;}
    sipa_free_sent_items();
    if(test==4){assert(rd==1 && sender.left_cnt==1);fault=-1;sipa_free_sent_items();}
    assert(rd==total);
    unsigned released=test==7?1:owned;
    assert(sender.left_cnt==(int)released);
    assert(frees+requeues==released && unmaps==released-(test==5?1:0));
    if(test==7)assert(!skbs[0].freed && skbs[0].mapped && !list_empty(&sender.sending_list));
    else assert(list_empty(&sender.sending_list));
    if(test==5)assert(!requeues && skbs[0].freed);
    unsigned old_syncs=syncs;sipa_free_sent_items();assert(syncs==old_syncs);
    pthread_mutex_destroy(&sender.send_lock);return 0;
}
