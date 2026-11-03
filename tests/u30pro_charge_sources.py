"""Run production SGM policy with synthetic PHY/PD/temperature/register inputs."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
DRIVER = TOP/'devices/u30pro/native/kernel/ums9632/overlay/drivers/power/supply'
SOURCE = (DRIVER/'u30pro-sgm41513.c').read_text()


def declaration(pattern):
    match = re.search(pattern, SOURCE, re.M)
    assert match, pattern
    start = SOURCE.index('{', match.start())
    pos, depth = start + 1, 1
    while depth:
        depth += (SOURCE[pos] == '{') - (SOURCE[pos] == '}')
        pos += 1
    return SOURCE[match.start():pos] + '\n'


@unittest.skipUnless(os.name == 'posix', 'host C compiler')
class ChargeSources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        base = Path(cls.temp.name)
        model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <stdlib.h>
#include "u30pro-sgm41513-values.h"
typedef uint32_t u32; typedef uint8_t u8;
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define BIT(x) (1U<<(x))
#define SGM_STATUS 8
#define SGM_CONTROL 1
#define SGM_OTG BIT(5)
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
enum {UNKNOWN_TYPE,SDP_TYPE,DCP_TYPE,CDP_TYPE,ACA_TYPE};
enum {USB_CHARGER_DEFAULT,USB_CHARGER_PRESENT,USB_CHARGER_ABSENT};
enum {POWER_SUPPLY_USB_TYPE_UNKNOWN,POWER_SUPPLY_USB_TYPE_SDP,
 POWER_SUPPLY_USB_TYPE_DCP,POWER_SUPPLY_USB_TYPE_CDP};
enum {POWER_SUPPLY_PROP_ONLINE,POWER_SUPPLY_PROP_VOLTAGE_NOW,
 POWER_SUPPLY_PROP_CURRENT_MAX,POWER_SUPPLY_PROP_TEMP};
enum {POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO,POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE};
enum {NOTIFY_DONE,NOTIFY_OK};
struct mutex {int unused;};struct work_struct {int unused;};
struct delayed_work {int unused;};struct notifier_block {int unused;};
struct power_supply_desc {int unused;};struct regulator_desc {int unused;};
struct usb_phy {int chg_state,chg_type;};
struct power_supply {int kind;};union power_supply_propval {int intval;};
STRUCTS
static unsigned regs[16],reads,writes,queued,notifications,locks,cancelled;
static int pd_online,pd_uv=5000000,pd_ua=3000000,temp=320,have_temp=1;
static struct power_supply pd={1},battery={2};
static int sgm_read(struct u30_sgm *s,int reg){(void)s;reads++;return regs[reg];}
static int sgm_update_reg(struct u30_sgm *s,int reg,int mask,int value){
 (void)s;writes++;regs[reg]=(regs[reg]&~mask)|value;return 0;
}
static struct power_supply *power_supply_get_by_name(const char *n){
 return strstr(n,"tcpm")?&pd:(have_temp?&battery:NULL);
}
static int power_supply_get_property(struct power_supply *s,int p,union power_supply_propval *v){
 if(s->kind==2)v->intval=temp;
 else if(p==POWER_SUPPLY_PROP_ONLINE)v->intval=pd_online;
 else if(p==POWER_SUPPLY_PROP_VOLTAGE_NOW)v->intval=pd_uv;
 else v->intval=pd_ua;
 return 0;
}
static void power_supply_put(struct power_supply *s){(void)s;}
static void power_supply_changed(struct power_supply *s){(void)s;notifications++;}
static void mutex_lock(struct mutex *m){(void)m;locks++;}
static void mutex_unlock(struct mutex *m){(void)m;locks--;}
static void schedule_work(struct work_struct *w){(void)w;queued++;}
static void cancel_work_sync(struct work_struct *w){(void)w;cancelled++;}
static void cancel_delayed_work_sync(struct delayed_work *w){(void)w;cancelled++;}
FUNCTIONS
int main(int argc,char **argv){
 assert(argc==2);struct usb_phy phy={USB_CHARGER_PRESENT,SDP_TYPE};
 struct u30_sgm s={.usb_phy=&phy,.psy=&battery,.policy_on=true,.boot_vreg=0x88,
  .jeita_n=1,.jeita_idx=-1,.tbl_iin={500000,3000000,1500000,1000000},
  .tbl_ichg={700000,2000000,1500000,1000000}};
 s.jeita[0]=(struct sgm_jeita){.t_up=1000,.t_rec=970,.ichg=3000000,.cv=4400000};
 regs[SGM_STATUS]=0x34; // Chip PSEL still says SDP even when PMIC finds DCP.
 switch(atoi(argv[1])){
 case 1:
  sgm_policy(&s);assert(s.pol_iin==500000 && s.pol_ichg==700000 && s.pol_type==0);
  phy.chg_type=DCP_TYPE;sgm_policy(&s);
  assert(s.pol_iin==3000000 && s.pol_ichg==2000000 && sgm_usb_type(&s)==POWER_SUPPLY_USB_TYPE_DCP);
  assert(sgm41513_iin_ua(regs[0])==3000000);
  phy.chg_type=CDP_TYPE;sgm_policy(&s);assert(s.pol_iin==1500000 && s.pol_ichg==1500000);
  assert(sgm_usb_type(&s)==POWER_SUPPLY_USB_TYPE_CDP);
  phy.chg_type=SDP_TYPE;sgm_policy(&s);assert(s.pol_iin==500000);
  assert(sgm_usb_type(&s)==POWER_SUPPLY_USB_TYPE_SDP);break;
 case 2:
  phy.chg_type=UNKNOWN_TYPE;sgm_policy(&s);assert(s.pol_type==3 && s.pol_iin==1000000);
  phy.chg_type=DCP_TYPE;phy.chg_state=USB_CHARGER_ABSENT;
  assert(sgm_usb_type(&s)==POWER_SUPPLY_USB_TYPE_UNKNOWN);
  regs[SGM_STATUS]=0;sgm_policy(&s);assert(writes==4);
  regs[SGM_STATUS]=0x34;regs[SGM_CONTROL]=SGM_OTG;sgm_policy(&s);assert(writes==4);break;
 case 3:
  pd_online=1;sgm_policy(&s);assert(s.pol_pd_ma==3000 && s.pol_iin==3000000 && s.pol_ichg==3000000);
  s.user_iin=1200000;s.user_ichg=900000;sgm_policy(&s);
  assert(s.pol_iin==1200000 && s.pol_ichg==900000);
  pd_online=0;s.user_iin=s.user_ichg=0;sgm_policy(&s);assert(s.pol_iin==500000);break;
 case 4:
  phy.chg_type=DCP_TYPE;s.behaviour=POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE;
  sgm_policy(&s);assert(!s.pol_charge && !(regs[1]&BIT(4)) && s.pol_cv==4400000);
  s.behaviour=POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO;have_temp=0;
  sgm_policy(&s);assert(s.pol_ichg==1000000);
  have_temp=1;temp=1100;sgm_policy(&s);assert(!s.pol_charge && !s.pol_ichg);break;
 case 5:
  phy.chg_type=DCP_TYPE;
  assert(sgm_source_notify(&s.usb_nb,3000,&phy)==NOTIFY_OK);
  assert(queued==1 && !reads && !writes && !locks);
  sgm_source_work(&s.source_work);assert(s.pol_iin==3000000 && notifications==1 && !locks);
  assert(sgm_source_notify(&s.usb_nb,0,NULL)==NOTIFY_DONE && queued==1);
  s.stopping=true;assert(sgm_source_notify(&s.usb_nb,0,&phy)==NOTIFY_DONE && queued==1);
  sgm_source_work(&s.source_work);assert(notifications==1);break;
 case 6:
  sgm_cancel_poll(&s);assert(s.stopping && cancelled==2);
  assert(sgm_source_notify(&s.usb_nb,0,&phy)==NOTIFY_DONE && !queued);break;
 default:assert(0);
 }
 return 0;
}
'''
        structs = '\n'.join(declaration(r'^struct '+name+r'\s*\{')+';'
                            for name in ('sgm_jeita','u30_sgm'))
        functions = '\n'.join(declaration(r'^static (?:int|void) '+name+r'\([^;{}]*\)\s*\{')
            for name in ('sgm_ichg_code','sgm_cv_code','sgm_band','sgm_source_type',
                         'sgm_usb_type','sgm_policy','sgm_source_work','sgm_source_notify','sgm_cancel_poll'))
        (base/'test.c').write_text(model.replace('STRUCTS',structs).replace('FUNCTIONS',functions))
        cls.binary=base/'sources'
        r=subprocess.run(['gcc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-I',str(DRIVER),
                          str(base/'test.c'),'-o',str(cls.binary)],capture_output=True,text=True)
        if r.returncode:raise RuntimeError(r.stderr)

    def case(self,n):
        r=subprocess.run([str(self.binary),str(n)],capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_bc12_dcp_and_cdp_override_chip_psel_but_sdp_stays_sdp(self):self.case(1)
    def test_unknown_absent_and_otg_never_become_dcp(self):self.case(2)
    def test_pd_and_explicit_user_limits_are_preserved(self):self.case(3)
    def test_bypass_jeita_and_factory_voltage_remain_in_force(self):self.case(4)
    def test_atomic_notification_only_queues_process_context_policy(self):self.case(5)
    def test_shutdown_cancels_both_workers_and_blocks_further_events(self):self.case(6)


if __name__=='__main__':unittest.main(verbosity=2)
