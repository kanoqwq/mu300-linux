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
typedef uintptr_t dma_addr_t;
typedef int atomic_t;
enum sipa_cmn_fifo_index { FIFO0 };
#define atomic_read(p) (*(p))
#define atomic_inc(p) (++*(p))
#define atomic_sub(n,p) (*(p)-=(n))
#define GFP_KERNEL 0
#define GFP_NOWAIT 0
#define DMA_FROM_DEVICE 1
#define DMA_TO_DEVICE 2
#define SIPA_RECV_BUF_LEN 2048
#define NET_SKB_PAD 32
#define SIPA_PER_PAGE_CAPACITY 32
#define SIPA_MAX_PROBE_DEPTH 128
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
#define smp_wmb() ((void)0)
struct page { int refs; };
struct sk_buff { void *head; struct page *page; bool freed; };
struct list_head { struct list_head *next,*prev; };
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry_safe(p,n,h,m) \
 for(p=container_of((h)->next,typeof(*p),m),n=container_of((p)->m.next,typeof(*p),m); \
     &(p)->m!=(h);p=n,n=container_of((n)->m.next,typeof(*n),m))
static void list_del(struct list_head *p) {p->next->prev=p->prev;p->prev->next=p->next;}
static void list_add_tail(struct list_head *p,struct list_head *h) {
 p->next=h;p->prev=h->prev;h->prev->next=p;h->prev=p;
}
struct sipa_recv_mem_list { void *virt; dma_addr_t dma_addr; struct page *page; struct list_head list; };
struct entry { struct sk_buff *skb; dma_addr_t dma_addr; bool need_unmap; };
struct sipa_skb_array { u32 rp,wp,depth; atomic_t need_fill_cnt; struct entry array[64]; struct list_head mem_list; };
struct sipa_node_desc_tag { dma_addr_t address; unsigned offset,length; };
struct sipa_endpoint { struct { int idx; } recv_fifo; };
struct sipa_skb_receiver { void *dev; struct sipa_endpoint *ep; struct sipa_skb_array *fill_array[1]; unsigned alloc_skb_cnt; };
enum fault { NONE, ALLOC, MAP, DESC, BUILD, FULL };
static enum fault fault;
static unsigned fail_after;
static struct sk_buff skbs[128];
static char storage[128][2048];
static struct sipa_node_desc_tag descriptors[64];
static struct page page;
static bool mapped[128];
static unsigned allocations,frees,mappings,unmappings,published;
static unsigned alloc_calls,map_calls,descriptor_calls,build_calls;
static bool reject(enum fault f,unsigned call) {return fault==f && call==fail_after;}
static struct sk_buff *__dev_alloc_skb(unsigned n,int flags) {
 if(reject(ALLOC,alloc_calls++))return NULL;
 assert(allocations<128);struct sk_buff *s=&skbs[allocations];
 s->head=storage[allocations++];return s;
}
static struct sk_buff *build_skb(void *p,unsigned n) {
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
 if(s->page){assert(s->page->refs>1);--s->page->refs;}
}
static struct sipa_node_desc_tag *sipa_hal_get_rx_node_wptr(void *dev,int id,unsigned i) {
 if(reject(DESC,descriptor_calls++))return NULL;
 assert(i<64);return &descriptors[i];
}
static void sipa_hal_sync_node_to_rx_fifo(void *d,int id,unsigned n) {}
static void sipa_hal_add_rx_fifo_wptr(void *d,int id,unsigned n) {published+=n;}
static int page_ref_count(struct page *p) {return p->refs;}
static void page_ref_inc(struct page *p) {++p->refs;}
static void dma_sync_single_range_for_device(void *d,dma_addr_t a,int o,unsigned n,int dir) {}

/* PRODUCTION_FUNCTIONS */

int main(int argc,char **argv) {
 assert(argc==4);int producer=atoi(argv[1]);fault=atoi(argv[2]);fail_after=atoi(argv[3]);
 struct sipa_skb_array a={.depth=64};
 struct sipa_endpoint ep={0};struct sipa_skb_receiver r={.ep=&ep,.fill_array={&a}};
 a.mem_list.next=a.mem_list.prev=&a.mem_list;page.refs=1;
 struct sipa_recv_mem_list mem={.virt=storage,.dma_addr=0x100000,.page=&page};
 list_add_tail(&mem.list,&a.mem_list);
 unsigned wanted=producer==0?33:producer==1?3:32;
 a.need_fill_cnt=wanted;
 if(fault==FULL)a.wp=a.depth;
 unsigned before=a.wp;
 if(producer==0)sipa_fill_free_node_by_alloc_skb(&r,0);
 else if(producer==1)sipa_fill_remaining_node(&r,wanted,0);
 else sipa_fill_free_fifo_node(&r,0,1);
 unsigned expected=fault==NONE?wanted:fault==FULL?0:fail_after;
 assert(published==expected && a.wp-before==expected);
 assert(a.need_fill_cnt==(int)(wanted-expected));
 assert(allocations-frees==published);
 if(producer==0)assert(mappings-unmappings==published);
 else assert(page.refs==1+(int)published);
 for(unsigned i=before;i<a.wp;i++) {
    struct entry *e=&a.array[i&(a.depth-1)];
    assert(e->skb && !e->skb->freed && e->need_unmap==(producer==0));
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
