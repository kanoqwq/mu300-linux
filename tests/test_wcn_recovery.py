"""Run the actual refill/recovery state machines with stub kernel primitives.

No fault injection on a device, no real wireless changes.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP, ShellTest
from test_wifi_tx_logging import function

MOD = TOP / 'upstream/modules'
WLAN = MOD / 'sprd_wlan_combo'


class DriverRecovery(unittest.TestCase):
    def run_c(self, code):
        if not shutil.which('cc'):
            self.skipTest('requires a host C compiler')
        with tempfile.TemporaryDirectory(prefix='mu300-rx-recovery-') as tmp:
            src, exe = Path(tmp) / 'check.c', Path(tmp) / 'check'
            src.write_text(code)
            p = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', str(src), '-o', str(exe)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            subprocess.run([str(exe)], check=True, timeout=5)

    def test_rx_push_drains_completed_ring_before_refill(self):
        body = function((MOD / 'wcn_bsp/pcie/edma_engine.c').read_text(),
                        'edma_rx_push_isr')
        self.run_c(r'''
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
''' + body + r'''
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

    def test_refill_no_progress_backs_off_and_progress_resumes_immediately(self):
        text = (WLAN / 'sc2355/rx.c').read_text()
        body = function(text, 'sc2355_mm_fill_buffer')
        constants = '\n'.join(l for l in text.splitlines() if l.startswith('#define SPRD_REFILL_BACKOFF_'))
        self.run_c(r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#define READ_ONCE(x) (x)
#define SPRD_HW_SC2355_PCIE 3
#define spin_lock_irqsave(p,f) do { (void)(p); (f)=0; } while (0)
#define spin_unlock_irqrestore(p,f) do { (void)(p); (void)(f); } while (0)
#define SPRD_MAX_ADD_MH_BUF_ONCE 32
#define SPRD_PCIE_RX_ALLOC_BUF 1
#define clamp(x,a,b) ((x)<(a)?(a):((x)>(b)?(b):(x)))
#define msecs_to_jiffies(x) (x)
#define atomic_read(p) (*(p))
#define atomic_add_return(n,p) (*(p)+=(n))
static unsigned int atomic_xchg(unsigned int *p, unsigned int v) { unsigned int n=*p; *p=v; return n; }
struct mem_mgmt { unsigned int alloc_num; };
struct rx_mgmt { struct mem_mgmt mm_entry; void *addr_trans_head; bool stopping; int refill_retry; unsigned int refill_backoff_ms; struct sprd_hif *hif; int refill_lock; bool refill_bus_waiting,refill_bus_ready; };
struct ops { int (*tx_addr_trans)(struct sprd_hif *, void *, int, bool); };
struct sprd_hif { struct rx_mgmt *rx_mgmt; struct ops *ops; void *priv; int hw_type; };
static unsigned int allocated, immediate, delayed, delay_ms, attempts;
static bool sent;
static void *system_wq;
static int delayed_work_pending(int *p) { return *p; }
static void mod_delayed_work(void *q, int *p, unsigned int ms) { ++delayed; *p=1; delay_ms=ms; }
static void cancel_delayed_work(int *p) { *p=0; }
static void sc2355_queue_rx_buff_work(void *p, int id) { ++immediate; }
static unsigned int sc2355_mm_buffer_alloc(struct mem_mgmt *m, unsigned int n) { ++attempts; return n-allocated; }
static int transmit(struct sprd_hif *h, void *data, int len, bool flush) { if (sent) h->rx_mgmt->addr_trans_head=NULL; return 0; }
''' + constants + '\n' + body + r'''
int main(void) {
    struct rx_mgmt rx={ .mm_entry={100} }; struct ops ops={transmit}; struct sprd_hif h={&rx,&ops,NULL}; rx.hif=&h;
    unsigned int expected[]={20,40,80,160,320,640,1000,1000};
    for (unsigned int i=0; i<8; ++i) {
        rx.refill_retry=0;
        assert(sc2355_mm_fill_buffer(&h)==100);
        assert(delay_ms==expected[i] && immediate==0 && attempts==i+1);
        for (int j=0; j<1000; ++j) assert(sc2355_mm_fill_buffer(&h)==100);
        assert(attempts==i+1); /* RX events cannot bypass a timed retry */
    }
    rx.refill_retry=0; allocated=10;
    assert(sc2355_mm_fill_buffer(&h)==90 && immediate==1 && delayed==8 && rx.refill_backoff_ms==0);
    /* Sending pending addresses is progress even when no buffer was allocated. */
    allocated=0; rx.addr_trans_head=&h; sent=true;
    assert(sc2355_mm_fill_buffer(&h)==90 && immediate==2 && delayed==8);
    allocated=90;
    assert(sc2355_mm_fill_buffer(&h)==0 && immediate==2 && delayed==8);
    rx.stopping=true;
    unsigned int before=attempts;
    assert(sc2355_mm_fill_buffer(&h)==0 && attempts==before);
    return 0;
}
''')

    def test_watchdog_resets_once_and_does_not_reboot_by_default(self):
        text = (WLAN / 'common/iface.c').read_text()
        default = next(l for l in text.splitlines() if l.startswith('static bool recovery_reboot'))
        constants = '\n'.join(l for l in text.splitlines() if l.startswith('#define IFACE_WD_'))
        funcs = '\n'.join(function(text, n) for n in ('iface_wd_give_up', 'iface_wd_tick'))
        self.run_c(r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define HZ 100
#define READ_ONCE(x) (x)
#define atomic_read(p) (*(p))
#define time_after_eq(a,b) ((long)((a)-(b))>=0)
#define jiffies_to_msecs(x) ((x)*10)
/* Keep log arguments type-checked/used without printing. */
#define pr_err(...) ((void)0)
#define pr_emerg(...) ((void)0)
struct work_struct { int unused; };
struct sprd_hif { bool cp_asserted; int power_cnt; };
static struct { int tick; struct work_struct reset; struct sprd_hif *hif; unsigned long stuck_since; bool stuck,reset_used,reset_this_time,gave_up,stopping; } iface_wd;
static unsigned long jiffies;
static bool dump;
static int resets,reboots,ticks;
static void *system_wq,*system_unbound_wq;
static int sprdwcn_bus_get_carddump_status(void) { return dump; }
static void queue_work(void *w, struct work_struct *x) { ++resets; }
static void queue_delayed_work(void *w, int *x, int d) { ++ticks; }
static void orderly_reboot(void) { ++reboots; }
''' + constants + '\n' + default + '\n' + funcs + r'''
int main(void) {
    struct sprd_hif h={false,1}; iface_wd.hif=&h;
    iface_wd_tick(NULL); assert(!resets && !reboots);
    dump=true; iface_wd_tick(NULL);
    jiffies=9*HZ; iface_wd_tick(NULL); assert(!resets);
    jiffies=10*HZ; iface_wd_tick(NULL); assert(resets==1);
    jiffies=69*HZ; iface_wd_tick(NULL); assert(resets==1 && !reboots);
    jiffies=70*HZ; int before=ticks; iface_wd_tick(NULL);
    assert(iface_wd.gave_up && !reboots && ticks==before);
    /* Fresh state: successful recovery followed by a second failure still must not reset-loop. */
    iface_wd.gave_up=false; iface_wd.stuck=false; iface_wd.reset_used=false; iface_wd.reset_this_time=false;
    jiffies=100*HZ; dump=true; iface_wd_tick(NULL);
    jiffies=110*HZ; iface_wd_tick(NULL); assert(resets==2);
    dump=false; h.cp_asserted=false; iface_wd_tick(NULL); assert(!iface_wd.stuck);
    jiffies=200*HZ; dump=true; iface_wd_tick(NULL);
    jiffies=210*HZ; iface_wd_tick(NULL); assert(iface_wd.gave_up && resets==2 && !reboots);
    iface_wd.stopping=true; before=ticks; iface_wd_tick(NULL); assert(ticks==before);
    return 0;
}
''')

    def test_teardown_lock_and_existing_latency_fixes_survive(self):
        text = (MOD / 'wcn_bsp/platform/wcn_procfs.c').read_text()
        body = function(text, '__wcn_assert_interface')
        powerdown = body.split('} else if (!wcn_begin_dump()) {', 1)[1].split('\n\t}', 1)[0]
        self.assertIn('goto out;', powerdown)
        self.assertNotIn('return;', powerdown)
        self.assertIn('mutex_unlock(&mdbg_proc->mutex)', body)
        rx = (WLAN / 'sc2355/rx.c').read_text()
        deinit = function(rx, 'sc2355_rx_deinit')
        seq = ('WRITE_ONCE(rx_mgmt->stopping, true)', 'cancel_delayed_work_sync', 'sprd_clean_work', 'kfree(rx_mgmt)')
        self.assertEqual(sorted(deinit.index(x) for x in seq), [deinit.index(x) for x in seq])
        self.assertLess(deinit.index('destroy_workqueue(rx_mgmt->rx_queue)'), deinit.index('sprd_clean_work'))
        self.assertLess(deinit.index('sprd_clean_work'), deinit.rindex('cancel_delayed_work_sync'))
        fill = function(rx, 'sc2355_fill_skb_csum')
        self.assertIn('CHECKSUM_NONE', fill)
        self.assertNotIn('return -1', fill)
        self.assertIn('msecs_to_jiffies(20)', (WLAN / 'sc2355/reorder.h').read_text())
        iface = (WLAN / 'common/iface.c').read_text()
        init = function(iface, 'iface_notify_init')
        self.assertLess(init.index('misc_register'), init.index('atomic_notifier_chain_register'))
        self.assertLess(init.index('register_inet6addr_notifier'), init.index('atomic_notifier_chain_register'))
        self.assertIn('goto err_misc;', init)
        self.assertIn('goto err_inet;', init)
        deinit = function(iface, 'iface_notify_deinit')
        self.assertLess(deinit.index('atomic_notifier_chain_unregister'), deinit.index('cancel_work_sync'))
        self.assertLess(deinit.index('cancel_work_sync'), deinit.index('misc_deregister'))
        self.assertNotIn('kobject_get_path', function(iface, 'iface_host_reset'))


class ReopenScript(ShellTest):
    def test_only_matching_enabled_wcn_radio_is_reopened(self):
        root = self.tmp / 'sys'
        module = self.tmp / 'sprd_wlan_combo'
        module.mkdir()
        for phy, driver in [('phy0', module), ('phy1', self.tmp / 'external_driver')]:
            driver.mkdir(exist_ok=True)
            dev = root / phy / 'device' / 'driver'
            dev.mkdir(parents=True)
            (dev / 'module').symlink_to(driver)
        self.stub('wifi', 'echo "$*" >> "$STUBLOG/wifi"\n'
                  '[ "$1" != down ] || { [ "$DISABLE_DURING_DOWN" != 1 ] || '
                  'touch "$STUBLOG/disabled"; exit 1; }')  # still attempt up after failed down
        self.stub('logger', ':')
        script = (TOP / 'openwrt/overlay/opt/mu300/bin/wcn-reopen').read_text()
        # Minimal config API fixture; the production selection/lock/reopen code runs unchanged.
        fixture = r'''
config_load() { :; }
config_foreach() { "$1" radio0; "$1" radio1; }
config_get_bool() {
    if [ -f "$STUBLOG/disabled" ]; then eval "$1=1";
    else eval "$1=\"${DISABLED:-0}\""; fi
}
config_get() {
    case "${BINDING:-phy}:$2:$3" in
      phy:radio0:phy) eval "$1=phy0" ;;
      phy:radio1:phy) eval "$1=phy1" ;;
      path:radio0:path) eval "$1=phy0/device" ;;
      path:radio1:path) eval "$1=phy1/device" ;;
      *) eval "$1=" ;;
    esac
}
'''
        script = script.replace('. /lib/functions.sh', fixture).replace('/sys/class/ieee80211', str(root))
        script = script.replace('/run/mu300-wcn-reopen.lock', str(self.tmp / 'lock'))
        for shell in self.each_shell():
            for binding in ('phy', 'path', 'unbound'):
                for disabled in ('0', '1'):
                    (self.tmp / 'wifi').unlink(missing_ok=True)
                    r = self.sh(shell, script, DISABLED=disabled, BINDING=binding)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    if disabled == '0' and binding != 'unbound':
                        self.assertEqual((self.tmp / 'wifi').read_text().splitlines(), ['down radio0', 'up radio0'])
                    else:
                        self.assertFalse((self.tmp / 'wifi').exists())
                    self.assertFalse((self.tmp / 'lock').exists())
            (self.tmp / 'wifi').unlink(missing_ok=True)
            r = self.sh(shell, script, DISABLE_DURING_DOWN='1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.tmp / 'wifi').read_text().splitlines(), ['down radio0'])
            self.assertFalse((self.tmp / 'lock').exists())
            (self.tmp / 'disabled').unlink()
            (self.tmp / 'wifi').unlink()
            (self.tmp / 'lock').mkdir()
            r = self.sh(shell, script)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((self.tmp / 'wifi').exists())
            (self.tmp / 'lock').rmdir()


if __name__ == '__main__':
    unittest.main()
