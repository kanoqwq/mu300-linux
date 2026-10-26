"""The SC2355 PCIe RX path of the mainline kernels, run as C on the host (FINDINGS 195, after kanoqwq's clean-tf-7.2
2d3f7438 and cf4dcd61): the vendor functions are cut out of upstream/modules and compiled against stub kernel
primitives, so the tests exercise the real code, not a copy of it.

- edma_rx_push_isr() (wcn_bsp): hands up the descriptors the chip completed, then refills. The vendor's
  dscr_ring_empty() called every valid cursor "empty", so this path only ever refilled.
- sc2355_mm_fill_buffer() (sprd_wlan_combo): an RX refill refused by a full EDMA pending ring is released by the bus
  completion on the data channel, not after the 20 ms - 1 s back-off; any other failure keeps the back-off.

The 5.4 kernel gets the RX push change as kernel/patches/wcn-pcie-rx-push-cursor.patch (its driver has no refill
back-off). Needs a host C compiler; skipped without one."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

MOD = TOP / 'upstream' / 'modules'
WLAN = MOD / 'sprd_wlan_combo' / 'sc2355'


def function(text, name):
    start = re.search(r'(?m)^(?:static )?(?:inline )?(?:int|void) ' + re.escape(name) + r'\(', text).start()
    return text[start:text.index('\n}', start) + 2]


def run_c(test, code, sanitize=False):
    cc = shutil.which('cc')
    if not cc:
        test.skipTest('needs a host C compiler')
    with tempfile.TemporaryDirectory(prefix='mu300-rx-') as tmp:
        src, exe = Path(tmp) / 'check.c', Path(tmp) / 'check'
        src.write_text(code)
        flags = ['-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function']
        if sanitize:
            flags.append('-fsanitize=address,undefined')
        p = subprocess.run([cc, *flags, str(src), '-o', str(exe)], capture_output=True, text=True)
        test.assertEqual(p.returncode, 0, p.stderr)
        return subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)


class RxPushCursor(unittest.TestCase):
    def test_rx_push_delivers_completed_descriptors_before_the_refill(self):
        text = (MOD / 'wcn_bsp' / 'pcie' / 'edma_engine.c').read_text()
        self.assertNotIn('dscr_ring_empty(chn)', text)
        r = run_c(self, r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p) >= (uintptr_t)-4095)
struct desc { int index; };
struct ring { struct desc *head; int free; };
struct channel { int inout; struct ring dscr_ring; };
struct edma_info { struct channel chn_sw[16]; };
static struct edma_info state;
static struct desc ring[4] = {{0},{1},{2},{3}};
static struct desc *hw;
static int reads, maps, pops, delivered, refills, step, pop_step, refill_step;
static struct edma_info *edma_info(void) { return &state; }
static void edma_hw_next_dscr(int ch, int dir, struct desc **end) {
    assert(ch==11 && dir==0); ++reads; *end=hw;
}
static struct desc *mpool_phy_to_vir(struct desc *p) {
    assert(!IS_ERR_OR_NULL(p)); ++maps; return p;
}
static void edma_pop_link(int ch, struct desc *start, struct desc *end,
                          void **head, void **tail, int *n) {
    ++pops; pop_step=++step;
    *n=(end->index-start->index+4)%4;
    assert(*n>0); *head=start; *tail=end;
    state.chn_sw[ch].dscr_ring.head=end;
    state.chn_sw[ch].dscr_ring.free+=*n;
}
static void edma_rx_list_pop_dp(int ch, void *h, void *t, int n) {
    assert(ch==11 && h && t && n>0);
}
static void mchn_hw_pop_link(int ch, void *h, void *t, int n) {
    assert(ch==11 && h && t); delivered+=n; ++step;
}
static void mchn_hw_req_push_link(int ch, int n) {
    assert(ch==11 && n==state.chn_sw[ch].dscr_ring.free);
    ++refills; refill_step=++step;
}
''' + function(text, 'edma_rx_push_isr') + r'''
int main(void) {
    state.chn_sw[11].dscr_ring.head=&ring[0]; hw=&ring[2];
    edma_rx_push_isr(11);
    assert(reads==1 && maps==1 && pops==1 && delivered==2 && refills==1);
    assert(pop_step<refill_step);
    /* An empty push interrupt still replenishes available descriptors. */
    edma_rx_push_isr(11);
    assert(reads==2 && pops==1 && delivered==2 && refills==2);
    /* Wrapped completion belongs to the same ring and is delivered once. */
    state.chn_sw[11].dscr_ring.head=&ring[3]; hw=&ring[1];
    edma_rx_push_isr(11);
    assert(reads==3 && pops==2 && delivered==4 && refills==3);
    hw=NULL; edma_rx_push_isr(11);
    hw=(void *)(intptr_t)-5; edma_rx_push_isr(11);
    assert(reads==5 && maps==3 && pops==2 && delivered==4 && refills==5);
    state.chn_sw[11].dscr_ring.free=0;
    edma_rx_push_isr(11);
    assert(refills==5);
}
''')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_vendor_kernel_gets_the_same_change(self):
        patch = (TOP / 'kernel' / 'patches' / 'wcn-pcie-rx-push-cursor.patch').read_text()
        self.assertIn('-static bool dscr_ring_empty(int chn)', patch)
        self.assertIn('+	if (IS_ERR_OR_NULL(end))', patch)
        build = (TOP / 'kernel' / 'build-all.sh').read_text()
        self.assertIn('sipa-wcn-logspam-quiet wcn-pcie-rx-push-cursor ', build)


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
typedef unsigned int atomic_t;
#define READ_ONCE(x) (x)
#define atomic_read(p) (*(p))
static unsigned int atomic_xchg(atomic_t *p, unsigned int v) {
    unsigned int old=*p; *p=v; return old;
}
#define atomic_add_return(v,p) (*(p)+=(v))
#define smp_load_acquire(p) (*(p))
#define SPRD_HW_SC2355_PCIE 3
#define SPRD_MAX_ADD_MH_BUF_ONCE 100U
#define SPRD_PCIE_RX_ALLOC_BUF 20
#define clamp(v,lo,hi) ((v)<(lo)?(lo):((v)>(hi)?(hi):(v)))
#define msecs_to_jiffies(x) (x)
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; bool pending; unsigned int delay; };
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define to_delayed_work(p) container_of(p, struct delayed_work, work)
struct mem_mgmt { atomic_t alloc_num; };
struct sprd_hif;
struct rx_mgmt {
    struct sprd_hif *hif;
    struct mem_mgmt mm_entry;
    bool stopping;
    struct delayed_work refill_retry;
    void *addr_trans_head;
    unsigned int refill_backoff_ms;
    bool refill_lock, refill_bus_waiting, refill_bus_ready;
};
struct tx_mgmt { atomic_t pcie_tx_waiting; unsigned int wakes; };
struct ops { int (*tx_addr_trans)(struct sprd_hif *, void *, int, bool); };
struct sprd_hif {
    struct rx_mgmt *rx_mgmt; struct tx_mgmt *tx_mgmt;
    struct ops *ops; void *priv; int hw_type, tx_data_port;
};
static struct sprd_hif *current_hif;
static unsigned int rcu_depth, submissions, rx_queued, allocated;
static unsigned int scheduled, cancelled, alloc_calls, retry_expired;
static int bus_result, completion_in_submit;
static bool stop_in_submit;
static void *system_wq;
#define rcu_read_lock() (++rcu_depth)
#define rcu_read_unlock() (--rcu_depth)
#define spin_lock_irqsave(p,f) do { assert(!*(p)); *(p)=true; (f)=0; } while (0)
#define spin_unlock_irqrestore(p,f) do { assert(*(p)); *(p)=false; (void)(f); } while (0)
static bool delayed_work_pending(struct delayed_work *w) { return w->pending; }
static void mod_delayed_work(void *q, struct delayed_work *w, unsigned int delay) {
    ++scheduled; w->pending=true; w->delay=delay;
}
static void queue_delayed_work(void *q, struct delayed_work *w, unsigned int delay) {
    assert(!w->pending); mod_delayed_work(q,w,delay);
}
static void cancel_delayed_work(struct delayed_work *w) { ++cancelled; w->pending=false; }
static unsigned int sc2355_mm_buffer_alloc(struct mem_mgmt *m, unsigned int n) {
    ++alloc_calls; assert(allocated<=n); return n-allocated;
}
static void sc2355_queue_rx_buff_work(void *priv, int id) {
    assert(id==SPRD_PCIE_RX_ALLOC_BUF);
    assert(!current_hif->rx_mgmt->refill_lock);
    ++rx_queued;
}
static struct sprd_hif *sc2355_pcie_get_hif(void) { return current_hif; }
static void sc2355_tx_up(struct tx_mgmt *t) { ++t->wakes; }
static int sc2355_pcie_tx_data_complete(int channel,int timeout);
static int submit(struct sprd_hif *h, void *p, int n, bool now) {
    assert(now && !h->rx_mgmt->refill_lock); ++submissions;
    if (completion_in_submit) {
        completion_in_submit=0;
        sc2355_pcie_tx_data_complete(h->tx_data_port,0);
    }
    if (stop_in_submit) h->rx_mgmt->stopping=true;
    if (!bus_result) h->rx_mgmt->addr_trans_head=NULL;
    return bus_result;
}
static void expire(struct rx_mgmt *rx);
'''

CASES = r'''
static void expire(struct rx_mgmt *rx) {
    assert(rx->refill_retry.pending);
    rx->refill_retry.pending=false;
    ++retry_expired;
    rx_refill_retry(&rx->refill_retry.work);
}
static void reset(struct rx_mgmt *rx,struct tx_mgmt *tx,struct sprd_hif *h,struct ops *ops) {
    memset(rx,0,sizeof(*rx)); memset(tx,0,sizeof(*tx));
    *h=(struct sprd_hif){.rx_mgmt=rx,.tx_mgmt=tx,.ops=ops,.hw_type=SPRD_HW_SC2355_PCIE,.tx_data_port=10};
    current_hif=h; rx->hif=h;
    submissions=rx_queued=allocated=scheduled=cancelled=alloc_calls=retry_expired=0;
    completion_in_submit=0; stop_in_submit=false; bus_result=-EAGAIN;
}
int main(void) {
    struct rx_mgmt rx; struct tx_mgmt tx; struct sprd_hif h;
    struct ops ops={.tx_addr_trans=submit};
    unsigned int previous[]={0,20,40,80,160,320,640,1000};
    unsigned int cases=0;
    for (unsigned int i=0;i<sizeof(previous)/sizeof(previous[0]);++i)
    for (unsigned int waiting=0;waiting<2;++waiting)
    for (unsigned int early=0;early<2;++early) {
        reset(&rx,&tx,&h,&ops);
        rx.addr_trans_head=&rx; rx.refill_backoff_ms=previous[i];
        tx.pcie_tx_waiting=waiting;
        completion_in_submit=early;
        assert(sc2355_mm_fill_buffer(&h)==0 && submissions==1);
        assert(rx.refill_retry.pending);
        if (!early) {
            assert(rx.refill_retry.delay>=20);
            sc2355_pcie_tx_data_complete(10,0);
        }
        /* Completion before timer arming and after timer arming both win. */
        assert(rx.refill_retry.delay==0);
        assert(tx.wakes==waiting && rx_queued==0);
        unsigned int before=scheduled;
        for (int duplicate=0;duplicate<100;++duplicate)
            sc2355_pcie_tx_data_complete(10,0);
        assert(scheduled==before); /* one pending RX retry, no IRQ storm */
        bus_result=0;
        expire(&rx);
        assert(rx_queued==1);
        sc2355_mm_fill_buffer(&h);
        assert(submissions==2 && rx.addr_trans_head==NULL);
        assert(!rx.refill_retry.pending && rx.refill_backoff_ms==0);
        before=scheduled;
        sc2355_pcie_tx_data_complete(10,0);
        assert(scheduled==before && !rx.refill_bus_waiting);
        assert(!rcu_depth); ++cases;
    }
    /* An RX work item already queued before the zero timer expires must also
     * be allowed through; a later timer must not restore the long delay. */
    reset(&rx,&tx,&h,&ops); rx.addr_trans_head=&rx;
    sc2355_mm_fill_buffer(&h); sc2355_pcie_tx_data_complete(10,0);
    assert(rx.refill_retry.delay==0); bus_result=0;
    sc2355_mm_fill_buffer(&h);
    assert(submissions==2 && cancelled==1 && !rx.refill_retry.pending); ++cases;

    /* A completion is only an opportunity, not proof of submission success.
     * Losing capacity again re-arms a bounded timer instead of spinning. */
    reset(&rx,&tx,&h,&ops); rx.addr_trans_head=&rx;
    sc2355_mm_fill_buffer(&h); sc2355_pcie_tx_data_complete(10,0);
    expire(&rx); sc2355_mm_fill_buffer(&h);
    assert(rx.refill_retry.pending && rx.refill_retry.delay==40);
    assert(submissions==2); ++cases;

    /* Lack of completions retains the original timer fallback. */
    reset(&rx,&tx,&h,&ops); rx.addr_trans_head=&rx;
    unsigned int expected[]={20,40,80,160,320,640,1000,1000};
    for (unsigned int i=0;i<sizeof(expected)/sizeof(expected[0]);++i) {
        sc2355_mm_fill_buffer(&h);
        assert(rx.refill_retry.delay==expected[i]);
        unsigned int n=submissions;
        for (int event=0;event<8;++event) sc2355_mm_fill_buffer(&h);
        assert(submissions==n);
        expire(&rx);
    }
    ++cases;

    /* Allocation failure and hard submission failures must not be released by
     * unrelated bus completions. Neither is evidence that memory is available. */
    for (int error=0;error<2;++error) {
        reset(&rx,&tx,&h,&ops);
        if (error) { rx.addr_trans_head=&rx; bus_result=-EIO; }
        else { rx.mm_entry.alloc_num=101; bus_result=0; }
        sc2355_mm_fill_buffer(&h);
        assert(rx.refill_retry.delay==20 && !rx.refill_bus_waiting);
        unsigned int n=scheduled;
        sc2355_pcie_tx_data_complete(10,0);
        assert(rx.refill_retry.delay==20 && scheduled==n);
        ++cases;
    }
    /* Wrong channel, a timeout, and an SDIO context cannot provide RX progress. */
    reset(&rx,&tx,&h,&ops); rx.addr_trans_head=&rx;
    sc2355_mm_fill_buffer(&h);
    sc2355_pcie_tx_data_complete(5,0);
    sc2355_pcie_tx_data_complete(10,1);
    assert(rx.refill_retry.delay==20); ++cases;
    reset(&rx,&tx,&h,&ops); rx.addr_trans_head=&rx; h.hw_type=1;
    sc2355_mm_fill_buffer(&h); sc2355_pcie_tx_data_complete(10,0);
    assert(rx.refill_retry.delay==20 && !rx.refill_bus_waiting); ++cases;

    /* Allocation progress keeps the existing immediate serial refill path. */
    reset(&rx,&tx,&h,&ops); rx.mm_entry.alloc_num=201; allocated=1;
    sc2355_mm_fill_buffer(&h);
    assert(rx_queued==1 && scheduled==0 && rx.refill_backoff_ms==0); ++cases;

    /* Teardown wins against completions and an already-running submission. */
    reset(&rx,&tx,&h,&ops); rx.addr_trans_head=&rx;
    sc2355_mm_fill_buffer(&h); rx.stopping=true;
    unsigned int n=scheduled;
    sc2355_pcie_tx_data_complete(10,0);
    assert(scheduled==n);
    expire(&rx); assert(rx_queued==0);
    sc2355_mm_fill_buffer(&h); assert(submissions==1); ++cases;
    reset(&rx,&tx,&h,&ops); rx.addr_trans_head=&rx; stop_in_submit=true;
    sc2355_mm_fill_buffer(&h);
    assert(!rx.refill_retry.pending && !rx.refill_bus_waiting); ++cases;

    /* Early init and late completion: published RX/TX contexts may be absent. */
    reset(&rx,&tx,&h,&ops); h.rx_mgmt=NULL;
    sc2355_pcie_tx_data_complete(10,0);
    h.tx_mgmt=NULL; sc2355_pcie_tx_data_complete(10,0);
    current_hif=NULL; sc2355_pcie_tx_data_complete(10,0);
    assert(!rcu_depth); ++cases;
    printf("%u RX completion/refill scenarios passed\n",cases);
    return 0;
}
'''



class RxRefillResume(unittest.TestCase):
    def code(self, baseline=False, off=False):
        rx, pcie = (WLAN / 'rx.c').read_text(), (WLAN / 'pcie.c').read_text()
        if baseline:
            # the completion hook taken out again: the long back-off comes back (the regression this catches)
            self.assertEqual(pcie.count('sc2355_rx_bus_complete(hif);'), 1)
            pcie = pcie.replace('sc2355_rx_bus_complete(hif);', '(void)hif;')
        constants = '\n'.join(s for s in rx.splitlines() if s.startswith('#define SPRD_REFILL_BACKOFF_'))
        return (HARNESS + '\n' + constants + '\nstatic bool refill_bus_resume = ' + ('false' if off else 'true') +
                ';\n' + function(rx, 'sc2355_rx_bus_complete') + '\n' +
                function(rx, 'rx_refill_retry') + '\n' + function(rx, 'sc2355_mm_fill_buffer') + '\n' +
                function(pcie, 'sc2355_pcie_tx_data_complete') + '\n' + CASES)

    def test_completion_resumes_rx_and_keeps_the_fault_backoff(self):
        r = run_c(self, self.code(), sanitize=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('RX completion/refill scenarios passed', r.stdout)

    def test_without_the_completion_hook_the_old_delay_returns(self):
        r = run_c(self, self.code(baseline=True))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('rx.refill_retry.delay==0', r.stderr)

    def test_off_by_default_the_back_off_stays(self):
        # experimental until measured on a board: the module parameter is off, and off is the old timer
        rx = (WLAN / 'rx.c').read_text()
        self.assertIn('static bool refill_bus_resume;\nmodule_param(refill_bus_resume, bool, 0644);', rx)
        r = run_c(self, self.code(off=True))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('rx.refill_retry.delay==0', r.stderr)

    def test_lifetime_and_teardown_order(self):
        rx, pcie = (WLAN / 'rx.c').read_text(), (WLAN / 'pcie.c').read_text()
        cb = function(pcie, 'sc2355_pcie_tx_data_complete')
        self.assertLess(cb.index('rcu_read_lock()'), cb.index('sc2355_pcie_get_hif()'))
        self.assertLess(cb.index('sc2355_rx_bus_complete'), cb.index('rcu_read_unlock()'))
        init = function(rx, 'sc2355_rx_init')
        self.assertLess(init.index('spin_lock_init(&rx_mgmt->refill_lock)'), init.index('smp_store_release'))
        self.assertLess(init.index('rx_mgmt->hif = hif'), init.index('smp_store_release'))
        close = function(rx, 'sc2355_rx_deinit')
        order = ['spin_lock_irqsave', 'WRITE_ONCE(rx_mgmt->stopping, true)', 'spin_unlock_irqrestore',
                 'cancel_delayed_work_sync', 'sprd_clean_work', 'smp_store_release(&hif->rx_mgmt, NULL)',
                 'synchronize_rcu()', 'kfree(rx_mgmt)']
        offsets = [close.index(s) for s in order]
        self.assertEqual(offsets, sorted(offsets))
        self.assertLess(close.index('sprd_clean_work'), close.rindex('cancel_delayed_work_sync'))


class VendorEventBounds(unittest.TestCase):
    def test_the_5_4_driver_gets_mainline_s_event_bounds_and_mgmt_channel(self):
        # mainline has them since PR #282 (FINDINGS 80); the 5.4 copy must say the same
        patch = (TOP / 'kernel' / 'patches' / 'wlan_combo-station-event-bounds.patch').read_text()
        main_evt = function((WLAN / 'cmdevt.c').read_text(), 'cmdevt_report_new_station_evt')
        for line in main_evt.splitlines()[4:-1]:
            if line.strip():
                self.assertIn('+' + line + '\n', patch)
        self.assertIn('+\t\tchan = wdev->chandef.chan;\n', patch)
        self.assertIn('+\t\tif (params->offchan ||\n', patch)


if __name__ == '__main__':
    unittest.main()
