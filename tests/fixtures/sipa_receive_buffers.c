/* Host MMIO/DMA fixture for the actual SIPA receive-buffer producers. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <stddef.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef uintptr_t dma_addr_t;
typedef int atomic_t;
enum sipa_cmn_fifo_index { FIFO0 };
#define atomic_read(p) (*(p))
#define atomic_inc(p) (++*(p))
#define atomic_sub(n,p) (*(p)-=(n))
#define atomic_dec(p) (--*(p))
#define GFP_KERNEL 0
#define GFP_NOWAIT 0
#define DMA_FROM_DEVICE 1
#define DMA_TO_DEVICE 2
#define DMA_BIDIRECTIONAL 3
#define SIPA_RECV_BUF_LEN 2048
#define NET_SKB_PAD 32
#define SIPA_PER_PAGE_CAPACITY 32
#define SIPA_MAX_PROBE_DEPTH 128
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define smp_wmb() ((void)0)
#define CHECKSUM_UNNECESSARY 1
#define CHECKSUM_NONE 0
#define time_after(a,b) 0
#define msecs_to_jiffies(n) (n)
#define pm_wakeup_dev_event(d,n,x) ((void)0)
#define smp_processor_id() 0
#define dev_warn(...) ((void)0)
static unsigned jiffies;
struct page { int refs; };
struct sk_buff { void *head; struct page *page; bool freed; unsigned len,pad,ip_summed; };
struct list_head { struct list_head *next,*prev; };
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry_safe(p,n,h,m) \
 for(p=container_of((h)->next,typeof(*p),m),n=container_of((p)->m.next,typeof(*p),m); \
     &(p)->m!=(h);p=n,n=container_of((n)->m.next,typeof(*n),m))
#define list_for_each_entry(p,h,m) \
 for(p=container_of((h)->next,typeof(*p),m);&(p)->m!=(h);p=container_of((p)->m.next,typeof(*p),m))
static void list_del(struct list_head *p) {p->next->prev=p->prev;p->prev->next=p->next;}
static void list_add_tail(struct list_head *p,struct list_head *h) {
 p->next=h;p->prev=h->prev;h->prev->next=p;h->prev=p;
}
struct sipa_recv_mem_list { void *virt; dma_addr_t dma_addr; struct page *page; struct list_head list; unsigned size; };
struct entry { struct sk_buff *skb; dma_addr_t dma_addr; bool need_unmap; };
struct sipa_skb_array { u32 rp,wp,depth; atomic_t need_fill_cnt; struct entry array[64]; struct list_head mem_list; };
struct sipa_node_desc_tag { dma_addr_t address; unsigned offset,length,checksum,net_id,src,dst; };
struct sipa_endpoint { struct { int idx; } recv_fifo; };
struct device { void *pm_domain; struct { struct { struct { unsigned expires; } timer; } *wakeup; } power; };
struct sipa_skb_receiver { struct device *dev; struct sipa_endpoint *ep; struct sipa_skb_array *fill_array[1]; unsigned alloc_skb_cnt; atomic_t check_flag,check_suspend; };
struct sipa_plat_drv_cfg { struct device *dev; };
static struct sipa_plat_drv_cfg ipa;
static struct sipa_plat_drv_cfg *sipa_get_ctrl_pointer(void) { return &ipa; }
static void skb_reserve(struct sk_buff *s,unsigned n) { s->pad+=n; }
static void skb_put(struct sk_buff *s,unsigned n) { s->len+=n; }
enum fault { NONE, ALLOC, MAP, DESC, BUILD, FULL };
static enum fault fault;
static unsigned fail_after;
static struct sk_buff skbs[128];
static char storage[128][2048];
static struct sipa_node_desc_tag descriptors[64];
static struct page page;
static bool mapped[128];
static bool device_owned[32];
static unsigned allocations,frees,mappings,unmappings,published;
static unsigned alloc_calls,map_calls,descriptor_calls,build_calls;
static bool reject(enum fault f,unsigned call) {return fault==f && call==fail_after;}
static struct sk_buff *__dev_alloc_skb(unsigned n,int flags) {
 if(reject(ALLOC,alloc_calls++))return NULL;
 assert(allocations<128);struct sk_buff *s=&skbs[allocations];
 s->head=storage[allocations++];return s;
}
static struct sk_buff *build_skb(void *p,unsigned n) {
 assert(n==SIPA_RECV_BUF_LEN);
 unsigned slot=((char *)p-(char *)storage)/SIPA_RECV_BUF_LEN;
 assert(slot<32 && !device_owned[slot]);
 if(reject(BUILD,build_calls++))return NULL;
 assert(allocations<128);struct sk_buff *s=&skbs[allocations++];
 s->head=p;s->page=&page;return s;
}
static dma_addr_t dma_map_single(void *dev,void *p,unsigned n,int direction) {
 assert(n==SIPA_RECV_BUF_LEN && direction==DMA_FROM_DEVICE);
 if(reject(MAP,map_calls++))return 0;
 unsigned i=allocations-1;assert(!mapped[i]);mapped[i]=true;++mappings;
 return (i+1)*SIPA_RECV_BUF_LEN;
}
static int dma_mapping_error(void *dev,dma_addr_t a) {return a==0;}
static void dma_unmap_single(void *dev,dma_addr_t a,unsigned n,int direction) {
 assert(n==SIPA_RECV_BUF_LEN && direction==DMA_FROM_DEVICE);
 unsigned i=a/SIPA_RECV_BUF_LEN-1;assert(i<128 && mapped[i]);mapped[i]=false;++unmappings;
}
static void dev_kfree_skb_any(struct sk_buff *s) {
 assert(s && !s->freed);s->freed=true;++frees;
 if(s->page){
    unsigned slot=((char *)s->head-(char *)storage)/SIPA_RECV_BUF_LEN;
    assert(!device_owned[slot]);assert(s->page->refs>1);--s->page->refs;
 }
}
static struct sipa_node_desc_tag *sipa_hal_get_rx_node_wptr(void *dev,int id,unsigned i) {
 if(reject(DESC,descriptor_calls++))return NULL;
 assert(i<64);return &descriptors[i];
}
static bool sipa_hal_get_tx_fifo_empty_status(void *dev,int id) { return false; }
static struct sipa_node_desc_tag *sipa_hal_get_tx_node_rptr(void *dev,int id,unsigned i) { assert(i==0);return descriptors; }
static void sipa_hal_add_tx_fifo_rptr(void *dev,int id,unsigned n) { assert(n==1); }
static bool sipa_check_recv_node_value(struct sipa_node_desc_tag *node,dma_addr_t addr,struct sipa_skb_receiver *receiver,int id,struct sk_buff *s) {
 if(s->page) {
    unsigned slot=((char *)s->head-(char *)storage)/SIPA_RECV_BUF_LEN;assert(!device_owned[slot]);
 } else {
    unsigned slot=addr/SIPA_RECV_BUF_LEN-1;assert(!mapped[slot]);
 }
 return fault==NONE;
}
static void sipa_hal_sync_node_to_rx_fifo(void *d,int id,unsigned n) {}
static void sipa_hal_add_rx_fifo_wptr(void *d,int id,unsigned n) {published+=n;}
static int page_ref_count(struct page *p) {return p->refs;}
static void page_ref_inc(struct page *p) {++p->refs;}
static void dma_sync_single_range_for_cpu(void *d,dma_addr_t a,int o,unsigned n,int dir) {
 assert(dir==DMA_BIDIRECTIONAL && n==SIPA_RECV_BUF_LEN);
 unsigned slot=(a-0x100000+o)/SIPA_RECV_BUF_LEN;assert(slot<32);device_owned[slot]=false;
}
static void dma_sync_single_range_for_device(void *d,dma_addr_t a,int o,unsigned n,int dir) {
 assert(dir==DMA_BIDIRECTIONAL && n==SIPA_RECV_BUF_LEN);
 unsigned slot=(a-0x100000+o)/SIPA_RECV_BUF_LEN;
 assert(slot<32 && !device_owned[slot]);device_owned[slot]=true;
}

/* PRODUCTION_FUNCTIONS */

int main(int argc,char **argv) {
 assert(argc==4);int producer=atoi(argv[1]);fault=atoi(argv[2]);fail_after=atoi(argv[3]);
 struct sipa_skb_array a={.depth=64};
 struct sipa_endpoint ep={0};struct sipa_skb_receiver r={.ep=&ep,.fill_array={&a}};
 struct device dev={0};r.dev=&dev;ipa.dev=&dev;
 a.mem_list.next=a.mem_list.prev=&a.mem_list;page.refs=1;
 for(unsigned i=0;i<32;i++)device_owned[i]=true;
 struct sipa_recv_mem_list mem={.virt=storage,.dma_addr=0x100000,.page=&page,.size=32*SIPA_RECV_BUF_LEN};
 list_add_tail(&mem.list,&a.mem_list);
 if(producer>=4) {
    struct sk_buff *skb;dma_addr_t addr;bool heap=producer==5;
    if(heap) {skb=__dev_alloc_skb(SIPA_RECV_BUF_LEN,0);addr=dma_map_single(&dev,skb->head,SIPA_RECV_BUF_LEN,DMA_FROM_DEVICE);}
    else {skb=sipa_build_rx_skb(&r,&mem,0);addr=mem.dma_addr;dma_sync_single_range_for_device(&dev,addr,0,SIPA_RECV_BUF_LEN,DMA_BIDIRECTIONAL);}
    assert(sipa_put_recv_array_node(&a,skb,addr,heap)==0);
    descriptors[0]=(struct sipa_node_desc_tag){.address=addr,.length=64,.src=1,.dst=2,.net_id=3,.checksum=0xffff};
    int netid;u32 src;struct sk_buff *received=sipa_recv_skb(&r,&netid,&src,0,0);
    assert(r.check_flag==0 && unmappings==(heap?1:0));
    if(fault==NONE)assert(received==skb && !skb->freed && skb->len==64 && skb->ip_summed==CHECKSUM_UNNECESSARY && netid==3 && src==1);
    else assert(!received && skb->freed && frees==1 && a.need_fill_cnt==1);
    return 0;
 }
 unsigned wanted=producer==0?33:producer==1?3:32;
 a.need_fill_cnt=wanted;
 if(fault==FULL)a.wp=a.depth;
 unsigned before=a.wp;
 if(producer==0)sipa_fill_free_node_by_alloc_skb(&r,0);
 else if(producer==1)sipa_fill_remaining_node(&r,wanted,0);
 else if(producer==2)sipa_fill_free_fifo_node(&r,0,1);
 else {sipa_prepare_free_node(&r,0);published=a.wp-before;a.need_fill_cnt-=published;}
 unsigned expected=fault==NONE?wanted:fault==FULL?0:fail_after;
 if(producer==3 && fault==BUILD)expected=wanted-1;
 assert(published==expected && a.wp-before==expected);
 assert(a.need_fill_cnt==(int)(wanted-expected));
 assert(allocations-frees==published);
 if(producer==0)assert(mappings-unmappings==published);
 else assert(page.refs==1+(int)published);
 for(unsigned i=before;i<a.wp;i++) {
    struct entry *e=&a.array[i&(a.depth-1)];
    assert(e->skb && !e->skb->freed && e->need_unmap==(producer==0));
    if(producer!=0) {
        unsigned slot=((char *)e->skb->head-(char *)storage)/SIPA_RECV_BUF_LEN;
        assert(e->dma_addr==0x100000+slot*SIPA_RECV_BUF_LEN && device_owned[slot]);
    }
 }
 /* Failed heap replenishment remains retryable without clearing queues. */
 if(producer==0 && fault!=NONE && fault!=FULL) {
    fault=NONE;sipa_fill_free_node_by_alloc_skb(&r,0);
    assert(published==wanted && a.need_fill_cnt==0 && a.wp==wanted);
    assert(allocations-frees==published && mappings-unmappings==published);
 }
 printf("PASS producer=%d published=%u remaining=%d allocations=%u frees=%u maps=%u unmaps=%u\n",
        producer,published,a.need_fill_cnt,allocations,frees,mappings,unmappings);
 return 0;
}
