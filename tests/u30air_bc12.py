"""Compile the production asynchronous BC1.2 owner with register/event fixtures.

No USB device or register is touched. Deterministic event interleavings are
covered; this does not replace real charger/OTG validation.
"""
from pathlib import Path
import os
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
PHY = TOP/'upstream/port/drivers/usb/phy'
source = (PHY/'phy-sprd-ums9620-ssphy.c').read_text()
header = (PHY/'ums9620-bc12.h').read_text()
assert 'phy->bc12_enabled = ump9620_has_battery_policy();' in source
assert 'was_host != phy->is_host' in source
assert 'cancel_delayed_work_sync(&phy->bc12_work)' in source
assert 'devm_extcon_unregister_notifier' in source
assert 'GENMASK(11, 8), 2 << 8' in source
start = source.index('static int sprd_ssphy_vbus_notify(')
notify = source[start:source.index('\n}', start)+2]
code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <errno.h>
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) (((1U<<((h)-(l)+1))-1)<<(l))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define NOTIFY_OK 1
#define USB_SPEED_UNKNOWN 0
#define CHGR_DET_FGU_CTRL 0x23a0
#define system_power_efficient_wq NULL
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
#define spin_lock_irqsave(l,f) ((void)(l),(f)=0)
#define spin_unlock_irqrestore(l,f) ((void)(l),(void)(f))
typedef unsigned int u32;
struct work_struct {int unused;};
struct delayed_work {struct work_struct work;};
struct notifier_block {int unused;};
enum usb_charger_type {UNKNOWN_TYPE,SDP_TYPE,DCP_TYPE,CDP_TYPE};
enum {USB_CHARGER_ABSENT,USB_CHARGER_PRESENT};
struct usb_phy {enum usb_charger_type chg_type;int chg_state;struct work_struct chg_work;struct notifier_block vbus_nb;void *dev;};
struct sprd_ssphy {
    struct usb_phy phy;void *pmic,*dev;int dpdm_lock,bc12_event_lock;
    bool bc12_enabled,bc12_ready,bc12_stopping,vbus_present,is_host;
    int bc12_generation;u32 bc12_seen;unsigned int bc12_samples;bool bc12_retry;
    struct delayed_work bc12_work;
};
static int atomic_read(int *p){return *p;}
static void atomic_inc(int *p){++*p;}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static unsigned long msecs_to_jiffies(unsigned long n){return n;}
static bool queued,usb_owner;
static u32 reg_status,reg_ctrl=0x2f0,reg_retry;
static int publications,connects,disconnects,attempts,updates,fail_update,fail_read;
static unsigned long wait_ms;
static struct sprd_ssphy *event_during_read;
static int mod_delayed_work(void *q,struct delayed_work *w,unsigned long delay){
    (void)q;(void)w;queued=true;wait_ms+=delay;return 0;
}
static void schedule_work(struct work_struct *w){(void)w;++publications;}
static int sc27xx_dpdm_switch_to_phy(void *map,bool enable){
    (void)map;if(fail_update && ++updates==fail_update)return -EIO;
    usb_owner=enable;return 0;
}
static int regmap_update_bits(void *map,u32 reg,u32 mask,u32 val){
    (void)map;
    if(fail_update && ++updates==fail_update)return -EIO;
    u32 *r=reg==CHGR_DET_FGU_CTRL?&reg_ctrl:&reg_retry;
    *r=(*r&~mask)|(val&mask);
    if(reg==0x243c && (val&1)){++attempts;reg_status&=~BIT(11);}
    return 0;
}
static int regmap_read(void *map,u32 reg,u32 *val){
    (void)map;assert(reg==0x239c);if(fail_read)return -EIO;
    *val=reg_status;
    if(event_during_read){
        event_during_read->vbus_present=false;
        ++event_during_read->bc12_generation;
        event_during_read=NULL;
    }
    return 0;
}
static int sprd_ssphy_notify_connect(struct usb_phy *p,int speed){(void)p;(void)speed;++connects;return 0;}
static int sprd_ssphy_notify_disconnect(struct usb_phy *p,int speed){(void)p;(void)speed;++disconnects;return 0;}
''' + header + notify + r'''
static struct sprd_ssphy phy;
static void tick(void){queued=false;sprd_ssphy_bc12_work(&phy.bc12_work.work);}
static void reset(void){
    phy=(struct sprd_ssphy){.bc12_enabled=true,.bc12_ready=true,.bc12_generation=1};
    queued=false;usb_owner=true;reg_ctrl=0x2f0;reg_retry=0;
    publications=connects=disconnects=attempts=updates=0;
    fail_update=fail_read=0;wait_ms=0;event_during_read=NULL;
}
static void attach(u32 status){
    reg_status=status;
    assert(sprd_ssphy_vbus_notify(&phy.phy.vbus_nb,1,NULL)==NOTIFY_OK);
    assert(queued);tick();
}
int main(void){
    /* Valid detection never redetects a real PC or upgrades SDP current. */
    const u32 bits[]={BIT(7),BIT(6),BIT(5)};
    const int types[]={SDP_TYPE,DCP_TYPE,CDP_TYPE};
    for(int i=0;i<3;i++){
        reset();attach(BIT(2)|BIT(11)|bits[i]);
        assert(phy.phy.chg_type==types[i]&&!queued&&!attempts);
        assert(usb_owner==(types[i]!=DCP_TYPE));
        assert(sprd_ssphy_charger_detect(&phy.phy)==types[i]);
        assert((reg_ctrl&~(BIT(14)|BIT(0)))==0x2f0);
    }
    /* Data-only HOST override must retain the same cable's completed
     * charging permission; a real VBUS edge invalidates it immediately. */
    for(int i=0;i<3;i++){
        reset();attach(BIT(2)|BIT(11)|bits[i]);
        phy.is_host=true;
        mutex_lock(&phy.dpdm_lock);sprd_ssphy_bc12_role(&phy);mutex_unlock(&phy.dpdm_lock);
        tick();assert(phy.phy.chg_type==types[i]&&usb_owner&&!attempts);
        sprd_ssphy_vbus_notify(&phy.phy.vbus_nb,0,NULL);
        assert(phy.phy.chg_type==UNKNOWN_TYPE);tick();
        assert(phy.phy.chg_state==USB_CHARGER_ABSENT);
        attach(BIT(2)|BIT(11)|bits[i]);
        assert(phy.phy.chg_type==UNKNOWN_TYPE&&!attempts);
    }
    /* Pending hardware completion: detector retains data lines until DONE. */
    reset();attach(BIT(2));assert(queued&&!usb_owner&&phy.phy.chg_type==UNKNOWN_TYPE);
    reg_status=BIT(2)|BIT(11)|BIT(5);tick();assert(!queued&&usb_owner&&phy.phy.chg_type==CDP_TYPE);
    /* One stock UNKNOWN retry, preserving delay and completion semantics. */
    reset();attach(BIT(2)|BIT(11));assert(attempts==1&&queued&&!usb_owner);
    assert((reg_ctrl&GENMASK(7,4))==(15<<4));
    reg_status=BIT(2)|BIT(11)|BIT(6);tick();assert(!queued&&attempts==1&&!usb_owner&&phy.phy.chg_type==DCP_TYPE);
    /* All ambiguous type bit combinations stay UNKNOWN, never invent DCP. */
    for(unsigned int bits=0;bits<8;bits++){
        if(bits==1||bits==2||bits==4)continue;
        reset();attach(BIT(2)|BIT(11)|(bits<<5));
        reg_status=BIT(2)|BIT(11)|(bits<<5);tick();
        assert(!queued&&!usb_owner&&phy.phy.chg_type==UNKNOWN_TYPE&&attempts==1);
    }
    /* Never-ready detector has a bounded lifetime, not permanent polling. */
    reset();attach(BIT(2));int ticks=1;
    while(queued){tick();assert(++ticks<200);}
    assert(attempts==1&&!usb_owner&&phy.phy.chg_type==UNKNOWN_TYPE);
    assert(wait_ms>1000&&wait_ms<4000);
    /* Read or mux failure never exposes unvalidated lines to USB. */
    reset();fail_read=1;attach(BIT(2));assert(!queued&&!usb_owner&&!connects);
    reset();fail_update=1;attach(BIT(2));assert(!queued&&!connects&&phy.phy.chg_type==UNKNOWN_TYPE);
    /* Physical disconnect while a completed status is being read. */
    reset();event_during_read=&phy;attach(BIT(2)|BIT(11)|BIT(6));
    assert(queued&&!usb_owner&&phy.phy.chg_type==UNKNOWN_TYPE);tick();
    assert(!queued&&phy.phy.chg_state==USB_CHARGER_ABSENT);
    /* A late old completion may not overwrite the next cable's cached state. */
    u32 old=phy.bc12_generation;
    sprd_ssphy_vbus_notify(&phy.phy.vbus_nb,1,NULL);
    assert(!sprd_ssphy_bc12_publish(&phy,DCP_TYPE,true,old));
    reg_status=BIT(2)|BIT(11)|BIT(7);tick();assert(usb_owner&&phy.phy.chg_type==SDP_TYPE);
    /* Host mode (including an externally powered dock) never detects on DPDM. */
    reset();phy.is_host=true;mutex_lock(&phy.dpdm_lock);sprd_ssphy_bc12_role(&phy);mutex_unlock(&phy.dpdm_lock);
    attach(BIT(2));assert(!queued&&usb_owner&&!attempts&&phy.phy.chg_type==UNKNOWN_TYPE);
    phy.is_host=false;mutex_lock(&phy.dpdm_lock);sprd_ssphy_bc12_role(&phy);mutex_unlock(&phy.dpdm_lock);
    reg_status=BIT(2)|BIT(11)|BIT(5);tick();assert(!queued&&usb_owner&&phy.phy.chg_type==CDP_TYPE);
    /* Removal and data-only boards do not start a detection polling loop. */
    reset();phy.bc12_stopping=true;tick();assert(!queued&&!publications);
    reset();phy.bc12_enabled=false;sprd_ssphy_vbus_notify(&phy.phy.vbus_nb,1,NULL);
    assert(connects==1&&!queued&&!attempts&&usb_owner);
    puts("BC1.2 owner: SDP/CDP/DCP, retry/timeout, stale events, host/dock, failure and non-battery isolation PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='u30-bc12-test-') as tmp:
    p=Path(tmp);(p/'test.c').write_text(code)
    flags=['-fsanitize=address,undefined','-fno-omit-frame-pointer'] if os.environ.get('BC12_SANITIZE') else []
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-Wno-sign-compare','-Wno-unused-parameter',*flags,str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)

# Compile the production BQ consumer against already-patched source as well.
# Passing a kernel path checks patch integration; no fallback parser/backend.
import sys
if len(sys.argv)>1:
    charger=(Path(sys.argv[1])/'drivers/power/supply/bq256xx_charger.c').read_text()
    def function(signature):
        a=charger.index(signature);return charger[a:charger.index('\n}',a)+2]
    assert 'bq2560x_chg_iindpm(' not in charger
    assert '"BC1.2 PHY owner\\n"' in charger
    detect=function('static void bq2560x_chg_detect(')
    assert 'BQ256XX_IINDET_EN' not in detect
    assert 'sprd_pmic_detect_charger_type(' not in function('static int bq2560x_chg_pmic_type(')
    pub=charger.index('ret = bq256xx_power_supply_init(bq, &psy_cfg, dev);')
    assert charger.index('ret = bq2560x_chg_phy(bq);')<pub
    assert charger.index('ret = devm_delayed_work_autocancel(dev, &bq->vbus_work')<pub
    code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define BQ256XX_CHARGER_CONTROL_0 1
#define BQ256XX_CHG_CONFIG_MASK 0x10
#define BQ256XX_OTG_CONFIG 0x20
#define BQ256XX_CHARGER_STATUS_0 8
#define BQ256XX_PG_STAT_MASK 4
#define BQ256XX_VBUS_STAT_MASK 0xe0
#define BQ256XX_VBUS_STAT_USB_OTG 0xe0
#define dev_info(d,...) ((void)(d),test_log(__VA_ARGS__))
static void test_log(const char *fmt,...){(void)fmt;}
#define dev_warn(...) ((void)0)
enum {UNKNOWN_TYPE,SDP_TYPE,DCP_TYPE,CDP_TYPE};
enum {POWER_SUPPLY_USB_TYPE_UNKNOWN,POWER_SUPPLY_USB_TYPE_SDP,POWER_SUPPLY_USB_TYPE_DCP,POWER_SUPPLY_USB_TYPE_CDP};
enum {USB_CHARGER_ABSENT,USB_CHARGER_PRESENT};
struct usb_phy {int chg_state,chg_type;};
struct device_node {int unused;};
static struct device_node *of_find_compatible_node(void*a,void*b,const char*c){(void)a;(void)b;(void)c;return NULL;}
static int spi_bus_type;
static void *bus_find_device_by_of_node(int*b,void*n){(void)b;(void)n;return NULL;}
static void of_node_put(void*p){(void)p;}
struct bq256xx_device;
struct chip_info {int (*bq256xx_set_iindpm)(struct bq256xx_device*,int);};
struct bq256xx_device {void *dev,*regmap,*charger,*pmic;int lock,usb_type;bool chg_off,pd_active,bc12_ready;struct chip_info *chip_info;struct usb_phy *bc12_phy;};
static int current,sets,errors,rp,notifications,reads,writes;
static unsigned int registers[16];
static void mutex_lock(int*p){assert(!*p);*p=1;}
static void mutex_unlock(int*p){assert(*p);*p=0;}
static int regmap_read(void*p,unsigned r,unsigned*v){(void)p;++reads;if(errors)return -EIO;*v=registers[r];return 0;}
static int regmap_update_bits(void*p,unsigned r,unsigned m,unsigned v){(void)p;++writes;assert(r==1);if(errors)return -EIO;registers[r]=(registers[r]&~m)|(v&m);return 0;}
static int set_current(struct bq256xx_device*b,int v){assert(b->lock);current=v;++sets;return 0;}
static int bq2560x_chg_typec_iindpm(struct bq256xx_device*b,unsigned int*v){assert(b->lock);*v=0;return rp;}
static void power_supply_changed(void*p){(void)p;++notifications;}
'''+function('static int bq2560x_chg_pmic_type(')+detect+r'''
int main(void){
 struct chip_info chip={set_current};struct usb_phy phy={USB_CHARGER_PRESENT,UNKNOWN_TYPE};
 struct bq256xx_device b={.bc12_ready=true,.chip_info=&chip,.bc12_phy=&phy};
 const int types[]={UNKNOWN_TYPE,SDP_TYPE,CDP_TYPE,DCP_TYPE};
 const int currents[]={500000,500000,1500000,2000000};
 registers[1]=0x8a;registers[8]=4|0x20;
 for(int i=0;i<4;i++){
  phy.chg_type=types[i];rp=0;bq2560x_chg_detect(&b);
  assert(current==currents[i]&&b.usb_type==types[i]);
  assert(registers[1]==0x9a);
 }
 /* Explicit current advertisement is separate from BC1.2 classification. */
 phy.chg_type=SDP_TYPE;rp=1500000;bq2560x_chg_detect(&b);assert(current==1500000);
 rp=3000000;bq2560x_chg_detect(&b);assert(current==3000000);
 /* An active PD contract must not be overwritten by a BC1.2 result. */
 int n=sets;b.pd_active=true;current=2000000;rp=3000000;
 bq2560x_chg_detect(&b);assert(sets==n&&current==2000000);b.pd_active=false;
 /* User off survives completion, and local OTG boost is never stopped. */
 b.chg_off=true;rp=0;bq2560x_chg_detect(&b);assert(!(registers[1]&0x10));
 registers[1]=0xaa;n=sets;bq2560x_chg_detect(&b);assert(registers[1]==0xaa&&sets==n);
 registers[1]=0x8a;registers[8]=0;n=sets;bq2560x_chg_detect(&b);assert(sets==n);
 registers[8]=4|0x20;errors=1;n=sets;bq2560x_chg_detect(&b);assert(sets==n);errors=0;
 /* A queued event cannot access an incompletely initialized/removed device. */
 b.bc12_ready=false;n=reads;bq2560x_chg_detect(&b);assert(reads==n);
 b.bc12_ready=true;phy.chg_state=USB_CHARGER_ABSENT;phy.chg_type=DCP_TYPE;
 bq2560x_chg_detect(&b);assert(current==500000&&b.usb_type==POWER_SUPPLY_USB_TYPE_UNKNOWN);
 return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='u30-bc12-consumer-') as tmp:
        p=Path(tmp);(p/'test.c').write_text(code)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(p/'test.c'),'-o',str(p/'test')],check=True)
        subprocess.run([str(p/'test')],check=True)
    print('BQ consumer: completed types, pending budget, PD/Rp priority, user off, OTG, probe/removal and I/O PASS')
