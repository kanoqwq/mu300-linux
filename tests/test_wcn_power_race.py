"""Exercise real dump/reset/stop code with a BAR lifetime and pthread locks."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

PLATFORM = TOP / 'upstream/modules/wcn_bsp/platform'


def function(text, name):
    start = re.search(r'(?m)^(?:static )?(?:int|bool|void) ' + name + r'\(', text).start()
    return text[start:text.index('\n}', start) + 2]


def command_branch(text, command):
    start = text.rindex(f'\t\tif (strncmp(mdbg_proc->write_buf, "{command}",')
    opening = text.index('{', start)
    at, depth = opening + 1, 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[opening + 1:at - 1]


@unittest.skipUnless(shutil.which('cc'), 'native C compiler required')
class PowerRace(unittest.TestCase):
    def test_real_assert_reset_and_stop_preserve_bar_lifetime(self):
        boot = (PLATFORM / 'wcn_boot.c').read_text()
        proc = (PLATFORM / 'wcn_procfs.c').read_text()
        code = r'''
#include <assert.h>
#include <stdbool.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define EXPORT_SYMBOL_GPL(x)
#define WCN_INFO(...) ((void)0)
#define WCN_ERR(...) ((void)0)
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
#define unlikely(x) (x)
#define FALSE false
#define TRUE true
#define MDBG_LOCK_WAIT_MS 30000
#define WCN_ASSERT_ONLY_DUMP 0
#define WCN_ASSERT_ONLY_RESET 1
#define WCN_ASSERT_BOTH_RESET_DUMP 2
enum wcn_sub_sys { MARLIN_WIFI=0, MARLIN_ALL=7, MARLIN_MDBG=8 };
enum wcn_source_type { WCN_SOURCE_BTWF, WCN_SOURCE_CP2_ALIVE };
struct wcn_match_data {bool unisoc_wcn_integrated,unisoc_wcn_pcie;};
static struct wcn_match_data match={false,true};
static struct {pthread_mutex_t power_lock;unsigned long power_state;bool keep_power_on;} state={PTHREAD_MUTEX_INITIALIZER,0,false};
static typeof(state) *marlin_dev=&state;
static struct {pthread_mutex_t mutex;bool marlin_powerdown_flag;int assert_notify_flag,fail_count;} debug={PTHREAD_MUTEX_INITIALIZER,false,0,0};
static typeof(debug) *mdbg_proc=&debug;
static bool carddump,bar_live,claimed;
static int io_reads,removes,dumps,notifies,reset_prop,flag_reset,dump_cnt;
static int g_dumpmem_switch=1;
static _Thread_local bool power_owned;
static void mutex_lock(pthread_mutex_t *p){assert(!pthread_mutex_lock(p));if(p==&state.power_lock){assert(!power_owned);power_owned=true;}}
static void mutex_unlock(pthread_mutex_t *p){if(p==&state.power_lock){assert(power_owned);power_owned=false;}assert(!pthread_mutex_unlock(p));}
static bool mutex_trylock(pthread_mutex_t *p){return !pthread_mutex_trylock(p);}
static bool mutex_is_locked(pthread_mutex_t *p){(void)p;return false;}
static struct wcn_match_data *get_wcn_match_config(void){return &match;}
static const char *strno(enum wcn_sub_sys s){(void)s;return "wifi";}
static int marlin_get_power(void){return state.power_state;}
static bool sprdwcn_bus_get_carddump_status(void){return carddump;}
static void sprdwcn_bus_set_carddump_status(bool v){if(v)assert(power_owned||carddump);carddump=v;}
static void wcn_set_powerdown_flag(bool v){assert(power_owned);debug.marlin_powerdown_flag=v;}
static void bar_read(void){assert(bar_live);io_reads++;}
static void sprdwcn_bus_runtime_put(void){assert(power_owned);bar_read();}
static int chip_power_off(int s){(void)s;assert(power_owned&&bar_live);bar_live=false;state.power_state=0;removes++;return 0;}
static void integ_wcn_chip_power_off(void){assert(0);}
static int get_loopcheck_status(void){return 0;}
static void mem_pd_mgr(int s,bool v){(void)s;(void)v;assert(0);}
static void stop_loopcheck(void){}
static void marlin_set_download_status(int v){assert(!v);}
static void marlin_chip_en(bool on,bool reset){assert(!on&&reset&&carddump&&bar_live);}
static int marlin_set_sleep(int s,bool v){(void)s;(void)v;assert(carddump&&bar_live);return 0;}
static int marlin_set_wakeup(int s){(void)s;assert(carddump&&bar_live);return 0;}
static void wcn_notify_fw_error(int t,char *s){(void)t;(void)s;notifies++;}
static void sprdwcn_bus_debug_point_show(void){assert(carddump);bar_read();}
static void wcn_pm_qos_reset(void){assert(carddump);}
static void wcnlog_clear_log(void){assert(carddump);}
static void msleep(int n){assert(n==2000);}
static int wcn_sysfs_get_reset_prop(void){return reset_prop;}
static int stop_integ_marlin(int s){(void)s;assert(0);return 0;}
static int stop_marlin(enum wcn_sub_sys s);
static void wcn_reset_cp2(void);
static bool mdbg_lock_bounded(unsigned ms){assert(ms==30000);return !pthread_mutex_lock(&debug.mutex);}
static void mdbg_dump_mem(int t){(void)t;assert(carddump);bar_read();dumps++;}
static void wcn_dump_process(enum wcn_source_type t){
 assert(stop_marlin(MARLIN_WIFI)==-1);mdbg_dump_mem(t);dump_cnt++;
}
static pthread_mutex_t sync_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond=PTHREAD_COND_INITIALIZER;
static bool pause_stop,stop_enter,release_stop;
static int marlin_set_power(enum wcn_sub_sys s,bool on){
 (void)s;assert(!on&&power_owned&&!carddump);
 pthread_mutex_lock(&sync_lock);
 if(pause_stop){stop_enter=true;pthread_cond_broadcast(&cond);while(!release_stop)pthread_cond_wait(&cond,&sync_lock);}
 pthread_mutex_unlock(&sync_lock);
 sprdwcn_bus_runtime_put();return chip_power_off(0);
}
static void set_bit(unsigned i,unsigned long *p){*p|=1UL<<i;}
'''
        code += '\n' + function(boot, 'wcn_mu300_reserve_reset_dump')
        code += '\n' + function(boot, 'wcn_chip_power_off')
        code += '\n' + function(boot, 'stop_marlin').replace('int stop_marlin(', 'static int stop_marlin(')
        code += '\n' + function(boot, 'marlin_power_off')
        code += '\nstatic void wcn_reset_cp2(void){wcn_chip_power_off();}\n'
        code += '\n' + function(proc, 'wcn_reset_process')
        code += '\n' + function(proc, '__wcn_assert_interface')
        code += '\n' + function(proc, 'wcn_mu300_recover')
        code += '\nstatic int user_reboot(void){int count=9;\n' + command_branch(proc, 'rebootwcn') + '\n}\n'
        code += '\nstatic int user_dump(void){int count=7;\n' + command_branch(proc, 'dumpmem') + '\n}\n'
        code += r'''
static void fresh(void){
 assert(!power_owned);state.power_state=1;bar_live=true;carddump=false;
 debug.marlin_powerdown_flag=false;debug.assert_notify_flag=0;debug.fail_count=0;
 io_reads=removes=dumps=notifies=dump_cnt=flag_reset=0;
 reset_prop=0;g_dumpmem_switch=1;pause_stop=stop_enter=release_stop=false;
}
static void *stop_thread(void *p){(void)p;stop_marlin(MARLIN_WIFI);return NULL;}
static void *dump_thread(void *p){(void)p;claimed=wcn_mu300_reserve_reset_dump();if(claimed)bar_read();return NULL;}
int main(void){
 pthread_t a,b;
 /* Stop owns power_lock first. Assert must observe OFF after waiting. */
 fresh();pause_stop=true;assert(!pthread_create(&a,NULL,stop_thread,NULL));
 pthread_mutex_lock(&sync_lock);while(!stop_enter)pthread_cond_wait(&cond,&sync_lock);
 assert(!pthread_create(&b,NULL,dump_thread,NULL));
 release_stop=true;pthread_cond_broadcast(&cond);pthread_mutex_unlock(&sync_lock);
 pthread_join(a,NULL);pthread_join(b,NULL);assert(!claimed&&!carddump&&!bar_live&&removes==1);
 /* Claim wins. A normal stop or explicit debug poweroff cannot remove BAR. */
 fresh();assert(wcn_mu300_reserve_reset_dump());assert(!pthread_create(&a,NULL,stop_thread,NULL));pthread_join(a,NULL);
 assert(carddump&&bar_live&&!removes);marlin_power_off(MARLIN_ALL);assert(bar_live&&!removes);
 wcn_chip_power_off();assert(!bar_live&&removes==1);int before=io_reads;
 wcn_chip_power_off();assert(io_reads==before&&removes==1);carddump=false;
 /* Exercise both schedules repeatedly with genuine mutex contention. */
 for(int n=0;n<200;n++){
  fresh();assert(!pthread_create(&a,NULL,(n&1)?dump_thread:stop_thread,NULL));
  assert(!pthread_create(&b,NULL,(n&1)?stop_thread:dump_thread,NULL));
  pthread_join(a,NULL);pthread_join(b,NULL);
  assert(claimed?(bar_live&&carddump&&!removes):(!bar_live&&!carddump&&removes==1));
 }
 for(int mode=0;mode<3;mode++){
  fresh();reset_prop=mode;__wcn_assert_interface(WCN_SOURCE_BTWF,"test");
  assert(!pthread_mutex_trylock(&debug.mutex));pthread_mutex_unlock(&debug.mutex);
  if(mode==0){assert(carddump&&bar_live&&dumps==1);assert(wcn_mu300_recover());}
  else assert(!bar_live&&!carddump&&removes==1);
  assert(!carddump&&!bar_live&&!dump_cnt);before=io_reads;
  __wcn_assert_interface(WCN_SOURCE_BTWF,"after off");assert(io_reads==before);
 }
 fresh();g_dumpmem_switch=0;__wcn_assert_interface(WCN_SOURCE_BTWF,"disabled");assert(!carddump&&!io_reads);
 /* A previous stop flag cannot veto a live bus claimed under power_lock. */
 fresh();debug.marlin_powerdown_flag=true;
 assert(wcn_mu300_reserve_reset_dump()&&carddump&&bar_live);
 wcn_reset_process();assert(!bar_live&&!carddump);
 fresh();pthread_mutex_lock(&debug.mutex);__wcn_assert_interface(WCN_SOURCE_BTWF,"busy");pthread_mutex_unlock(&debug.mutex);assert(!carddump&&!io_reads);
 fresh();assert(user_reboot()==9&&!bar_live&&!carddump&&removes==1&&!flag_reset);
 before=io_reads;assert(user_reboot()==-ENODEV&&io_reads==before);assert(user_dump()==-ENODEV&&io_reads==before);
 assert(!pthread_mutex_trylock(&debug.mutex));pthread_mutex_unlock(&debug.mutex);
 fresh();assert(user_dump()==7&&bar_live&&carddump&&dumps==1);assert(stop_marlin(MARLIN_WIFI)==-1);
 assert(wcn_mu300_recover()&&!bar_live&&!carddump&&removes==1);
 fresh();state.keep_power_on=true;assert(wcn_mu300_reserve_reset_dump());marlin_power_off(MARLIN_ALL);
 assert(bar_live&&state.keep_power_on&&!io_reads);wcn_reset_process();assert(!bar_live);
 fresh();marlin_power_off(MARLIN_ALL);assert(!bar_live&&removes==1);
 before=io_reads;marlin_power_off(MARLIN_ALL);assert(io_reads==before&&removes==1);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='wcn-power-race-') as folder:
            src, exe = Path(folder) / 'test.c', Path(folder) / 'test'
            src.write_text(code)
            result = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                     '-Wno-unused-parameter', '-Wno-unused-function',
                                     '-fsanitize=address,undefined', '-fno-pie', '-no-pie',
                                     '-pthread', str(src), '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(exe)], check=True, timeout=15)


if __name__ == '__main__':
    unittest.main()
