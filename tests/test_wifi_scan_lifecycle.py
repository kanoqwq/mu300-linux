"""Run the actual SC2355 scan callback through accepted/rejected/racing replies."""
from pathlib import Path
import re
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import TOP

WLAN = TOP / 'upstream/modules/sprd_wlan_combo'


def function(text, name):
    start = re.search(r'(?m)^(?:static )?(?:int|void|bool) ' + name + r'\(', text).start()
    return text[start:text.index('\n}', start) + 2]


HEAD = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define ETH_ALEN 6
#define IEEE80211_MAX_SSID_LEN 32
#define SPRD_TOTAL_SSID_NR 9U
#define SPRD_MAX_SCAN_REQ_IE_LEN 512
#define SPRD_HW_SC2355_PCIE 1
#define SPRD_MODE_AP 2
#define SPRD_MODE_STATION 3
#define SPRD_MODE_STATION_SECOND 4
#define SPRD_ENABLE_SCAN_RANDOM_ADDR 1
#define SPRD_DISABLE_SCAN_RANDOM_ADDR 0
#define NL80211_SCAN_FLAG_RANDOM_ADDR 1
#define CMD_SCAN 0
#define SPRD_SCAN_TIMEOUT_MS 9000
#define HZ 100
#define jiffies 100
#define GFP_KERNEL 0
#define min(a,b) ((a)<(b)?(a):(b))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define container_of(p,t,f) ((t *)((char *)(p)-offsetof(t,f)))
#define netdev_info(...) ((void)0)
#define netdev_err(...) ((void)0)
#define wiphy_err(...) ((void)0)
#define pr_info(...) ((void)0)
#define kmalloc(n,f) malloc(n)
#define kzalloc(n,f) calloc(1,n)
#define kfree(p) free(p)
#define cpu_to_le16(v) (v)
struct list_head { int empty; };
#define INIT_LIST_HEAD(l) ((l)->empty=1)
static int list_empty(struct list_head *l) { return l->empty; }
static void list_add_tail(struct list_head *a, struct list_head *b) { b->empty=0; }
struct cfg80211_ssid { u8 ssid[32]; unsigned int ssid_len; };
struct ieee80211_channel { unsigned int hw_value; };
struct cfg80211_scan_request;
struct sprd_priv;
struct sprd_vif {
    struct sprd_priv *priv;
    struct { int unused; } wdev;
    void *ndev; int mode;
    struct list_head scan_head_ptr, survey_info_list;
};
struct sprd_api_version_t { u8 fw_version, drv_version; };
struct sprd_priv {
    struct cfg80211_scan_request *scan_request;
    struct sprd_vif *scan_vif;
    int scan_lock, scan_timer, rand_mac_flag;
    struct { int hw_type; } hif;
    struct { struct sprd_api_version_t api_array[1]; } sync_api;
};
struct wiphy { struct sprd_priv *priv; };
#define wiphy_priv(w) ((w)->priv)
struct cfg80211_scan_request {
    void *wdev; struct cfg80211_ssid *ssids;
    unsigned int n_ssids, n_channels, flags;
    const u8 *ie; size_t ie_len;
    struct ieee80211_channel **channels;
};
struct cfg80211_scan_info { bool aborted; uint64_t scan_start_tsf; u8 tsf_bssid[6]; };
struct sprd_scan_ssid { u8 len; char ssid[32]; };
struct survey_info_node {
    struct list_head bssid_list, survey_list;
    unsigned int chan, beacon_num; void *channel;
};
static unsigned int completions, submissions, random_calls, timers;
static int command_result, completion_race;
static void spin_lock_bh(int *p) { assert(!*p); *p=1; }
static void spin_unlock_bh(int *p) { assert(*p); *p=0; }
static int timer_pending(int *p) { return *p; }
static void del_timer_sync(int *p) { *p=0; }
static void mod_timer(int *p, int deadline) { *p=1; ++timers; }
static void scan_clean_list(struct sprd_vif *v) { v->scan_head_ptr.empty=1; }
static void scan_init_list(struct sprd_vif *v) { assert(v->scan_head_ptr.empty); }
static void transfer_survey_info(struct sprd_vif *v) { }
static void clean_survey_info_list(struct sprd_vif *v) { v->survey_info_list.empty=1; }
static void sc2355_random_mac_addr(u8 *a) { memset(a,0,6); }
static int sprd_set_random_mac(struct sprd_priv *p, struct sprd_vif *v, int flag, u8 *a) {
    ++random_calls; return 0;
}
static int sprd_set_probereq_ie(struct sprd_priv *p, struct sprd_vif *v, const u8 *s, size_t n) { return 0; }
static void cfg80211_scan_done(struct cfg80211_scan_request *r, struct cfg80211_scan_info *i) {
    assert(r); ++completions;
}
static int sc2355_cmd_scan(struct sprd_priv *p, struct sprd_vif *v,
                        u32 channels, int n, const u8 *ssids, u16 count, const u16 *cs) {
    ++submissions;
    assert(p->scan_request && p->scan_vif==v && p->scan_timer);
    if (completion_race) {
        struct cfg80211_scan_info info={0};
        cfg80211_scan_done(p->scan_request,&info);
        p->scan_request=NULL; p->scan_vif=NULL; p->scan_timer=0;
    }
    return command_result;
}
'''

CASES = r'''
int main(void) {
    for (int mode=SPRD_MODE_AP;mode<=SPRD_MODE_STATION_SECOND;++mode)
    for (int result=0;result<3;++result) {
        struct sprd_priv p={0};
        struct sprd_vif v={.priv=&p,.mode=mode,.scan_head_ptr={1},.survey_info_list={1}};
        struct wiphy w={.priv=&p};
        struct ieee80211_channel channel={.hw_value=1},*channels[]={&channel};
        struct cfg80211_scan_request r={.wdev=&v.wdev,.n_channels=1,.channels=channels};
        p.sync_api.api_array[0]=(struct sprd_api_version_t){2,2};
        completions=submissions=random_calls=timers=0;
        command_result=result ? -EIO : 0; completion_race=result==2;
        int ret=sc2355_scan(&w,&r);
        assert(submissions==1);
        if (!result) {
            assert(ret==0 && p.scan_request==&r && p.scan_vif==&v && !completions);
        } else if (result==1) {
            /* Rejection: cfg80211 reclaims it, with no completion queued. */
            assert(ret==-EIO && !p.scan_request && !p.scan_vif && !p.scan_timer && !completions);
        } else {
            /* An early completion owns it: never ask cfg80211 to free twice. */
            assert(ret==0 && completions==1 && !p.scan_request && !p.scan_vif);
        }
        /* New attempts must leave an in-flight owner's state untouched. */
        p.scan_request=&r; p.scan_vif=&v; p.scan_timer=1;
        struct cfg80211_scan_request other=r;
        unsigned int old_submits=submissions,old_random=random_calls,old_timers=timers;
        assert(sc2355_scan(&w,&other)==-EBUSY);
        assert(p.scan_request==&r && p.scan_vif==&v && p.scan_timer==1);
        assert(submissions==old_submits && random_calls==old_random && timers==old_timers);
        assert(!p.scan_lock);
    }
    return 0;
}
'''


@unittest.skipUnless(os.name == 'posix' and shutil.which('cc'), 'Requires Linux and a native C compiler')
class ScanLifecycle(unittest.TestCase):
    def test_actual_scan_callback_and_old_double_completion(self):
        path = WLAN / 'sc2355/scan.c'
        fixed = path.read_text()
        callback = function(fixed, 'sc2355_scan')
        # Replay the pre-fix rejected-start completion contract as a negative
        # control, independent of Git availability or the current HEAD.
        reject = '''\t\tif (!sc2355_cancel_scan(vif, request))
\t\t\treturn 0; /* Completion or timeout won the reply race. */'''
        assert callback.count(reject) == 1
        old = callback.replace(reject, '''\t\tstruct cfg80211_scan_info info = { 0 };
\t\tcfg80211_scan_done(request, &info);
\t\tsc2355_cancel_scan(vif, request);''')
        def no_core():
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        with tempfile.TemporaryDirectory() as d:
            for name, source in [('fixed',callback), ('old',old)]:
                code = HEAD + function(fixed,'sc2355_cancel_scan') + '\n' + source + CASES
                c = Path(d)/(name+'.c'); c.write_text(code)
                exe = Path(d)/name
                subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',
                    '-Wno-unused-parameter','-Wno-unused-function','-Wno-pointer-sign',str(c),'-o',str(exe)],check=True)
                run = subprocess.run([str(exe)],capture_output=True,preexec_fn=no_core)
                if name=='fixed':
                    self.assertEqual(run.returncode,0,run.stderr.decode())
                else:
                    self.assertNotEqual(run.returncode,0,'Old failure path must reproduce duplicate ownership')

    def test_scan_metadata_is_initialized_for_all_completion_paths(self):
        for path in (WLAN/'sc2355/scan.c',WLAN/'common/report.c'):
            text=path.read_text()
            self.assertNotIn('struct cfg80211_scan_info info;',text)
            self.assertIn('struct cfg80211_scan_info info = { 0 };',text)


if __name__=='__main__':
    unittest.main()
