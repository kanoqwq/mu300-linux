"""Fixed-input power invariants and event-driven PD data-role ownership."""
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from helpers import ShellTest, TOP
from test_u30air_pd import DRIVER, function


class FixedInputPower(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'requires C compiler')
    def test_actual_callbacks_never_source_or_control_charger(self):
        text = DRIVER.read_text()
        regs = (DRIVER.parent/'sc27xx-ump9620-regs.h').read_text().split('enum sc27xx_state')[0]
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
typedef unsigned int u32;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define READ_ONCE(x) (x)
#define min(a,b) ((a)<(b)?(a):(b))
enum typec_role {TYPEC_SINK,TYPEC_SOURCE};
enum typec_data_role {TYPEC_DEVICE,TYPEC_HOST};
enum typec_cc_status {TYPEC_CC_OPEN,TYPEC_CC_RD,TYPEC_CC_RP_DEF,TYPEC_CC_RP_1_5,TYPEC_CC_RP_3_0};
enum typec_port_type {TYPEC_PORT_SNK,TYPEC_PORT_SRC,TYPEC_PORT_DRP};
enum usb_role {USB_ROLE_NONE,USB_ROLE_HOST,USB_ROLE_DEVICE};
enum {POWER_SUPPLY_USB_TYPE_PD,POWER_SUPPLY_USB_TYPE_UNKNOWN};
enum {POWER_SUPPLY_PROP_USB_TYPE,POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT};
union power_supply_propval {int intval;};
struct sc27xx_pd {bool sink_only,attached,pd_capable,vbus_on,stopping;
 enum typec_role power_role;void *vbus,*charger,*role_sw,*dev;int lock,cc_work;};
struct tcpc_dev {struct sc27xx_pd *pd;};
static int cc_writes,pd_writes,charger_writes,source_writes,events,usb_calls,usb_role,error;
static unsigned mode=0x20d4,last_reg,last_mask,last_value;
static struct sc27xx_pd *to_pd(struct tcpc_dev *t){return t->pd;}
static int cc_update(struct sc27xx_pd *p,u32 reg,u32 mask,u32 value){
 (void)p;cc_writes++;last_reg=reg;last_mask=mask;last_value=value;
 if(reg==4)mode=(mode&~mask)|(value&mask);return error;
}
static int regmap_write_bits(void *p,u32 reg,u32 mask,u32 v){(void)p;(void)reg;(void)mask;(void)v;assert(0);return 0;}
static int pd_update(struct sc27xx_pd *p,u32 reg,u32 mask,u32 v){(void)p;(void)reg;(void)mask;(void)v;pd_writes++;return error;}
static void mutex_lock(int *p){assert(!*p);*p=1;}
static void mutex_unlock(int *p){assert(*p);*p=0;}
static int regulator_enable(void *p){(void)p;source_writes++;return error;}
static int regulator_disable(void *p){(void)p;source_writes++;return error;}
static int power_supply_set_property(void *p,int prop,union power_supply_propval *v){(void)p;(void)prop;(void)v;charger_writes++;return error;}
static int usb_role_switch_set_role(void *p,enum usb_role role){(void)p;usb_calls++;usb_role=role;return error;}
static int mod_delayed_work(void *w,int *job,int delay){(void)w;(void)job;(void)delay;events++;return 0;}
static int msecs_to_jiffies(int v){return v;}
#define system_power_efficient_wq ((void *)0)
#define dev_info(...) ((void)0)
'''
        suffix = r'''
int main(void){
 struct sc27xx_pd pd={.sink_only=true};struct tcpc_dev t={&pd};
 assert(!sc27xx_set_cc(&t,TYPEC_CC_OPEN)&&!cc_writes);
 for(int c=TYPEC_CC_RP_DEF;c<=TYPEC_CC_RP_3_0;c++)
  assert(sc27xx_set_cc(&t,c)==-EOPNOTSUPP&&!cc_writes);
 assert(!sc27xx_set_cc(&t,TYPEC_CC_RD)&&cc_writes==1&&mode==0x20d4&&last_mask==3&&!last_value);
 int before=cc_writes;
 assert(sc27xx_start_toggling(&t,TYPEC_PORT_DRP,TYPEC_CC_RD)==-EOPNOTSUPP&&cc_writes==before&&!events);
 assert(sc27xx_start_toggling(&t,TYPEC_PORT_SRC,TYPEC_CC_RD)==-EOPNOTSUPP&&cc_writes==before);
 assert(!sc27xx_start_toggling(&t,TYPEC_PORT_SNK,TYPEC_CC_RD)&&events==1&&mode==0x20d4);
 before=cc_writes;
 assert(sc27xx_set_vconn(&t,true)==-EOPNOTSUPP&&cc_writes==before);
 assert(!sc27xx_set_vconn(&t,false)&&cc_writes==before);
 assert(sc27xx_set_vbus(&t,true,false)==-EOPNOTSUPP&&!source_writes);
 assert(!sc27xx_set_vbus(&t,false,true)&&!source_writes);
 assert(!sc27xx_set_vbus(&t,false,false)&&!source_writes);
 assert(!sc27xx_set_current_limit(&t,2000,5000)&&!charger_writes);
 assert(sc27xx_set_current_limit(&t,1000,9000)==-EINVAL&&!charger_writes);
 assert(sc27xx_set_roles(&t,true,TYPEC_SOURCE,TYPEC_HOST)==-EOPNOTSUPP&&!pd_writes&&!usb_calls);
 assert(!sc27xx_set_roles(&t,true,TYPEC_SINK,TYPEC_HOST)&&pd.attached&&usb_role==USB_ROLE_HOST);
 assert(!sc27xx_set_roles(&t,true,TYPEC_SINK,TYPEC_DEVICE)&&usb_role==USB_ROLE_DEVICE);
 assert(!sc27xx_set_roles(&t,false,TYPEC_SINK,TYPEC_HOST)&&!pd.attached&&usb_role==USB_ROLE_DEVICE);
 assert(!source_writes&&!charger_writes);
 /* Existing battery profile keeps source/charger ownership. */
 pd.sink_only=false;
 assert(!sc27xx_set_vbus(&t,true,false)&&source_writes==1&&pd.vbus_on);
 assert(!sc27xx_set_vbus(&t,false,false)&&source_writes==2&&!pd.vbus_on);
 assert(!sc27xx_set_current_limit(&t,500,5000)&&charger_writes==1);
 assert(!sc27xx_set_roles(&t,true,TYPEC_SOURCE,TYPEC_HOST)&&pd.power_role==TYPEC_SOURCE);
}
'''
        names = ('sc27xx_set_cc', 'sc27xx_start_toggling', 'sc27xx_set_vconn',
                 'sc27xx_set_vbus', 'sc27xx_set_current_limit', 'sc27xx_set_roles')
        # Include the field used only by the battery PR_SWAP branch.
        prefix = prefix.replace('void *vbus,*charger,*role_sw,*dev;',
                                'void *vbus,*charger,*role_sw,*dev,*regmap;u32 typec_base;')
        with tempfile.TemporaryDirectory() as d:
            c = Path(d)/'callbacks.c'; out = Path(d)/'callbacks'
            c.write_text(prefix+regs+''.join(function(text, n) for n in names)+suffix)
            subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-misleading-indentation',
                            str(c),'-o',str(out)],check=True)
            subprocess.run([str(out)],check=True)


class ProfileSelection(unittest.TestCase):
    def test_trial_profile_is_explicit_and_preserves_other_header_bytes(self):
        select = runpy.run_path(str(TOP/'tools/prepare-usb3-trial.py'))['select_batteryless_pd_profile']
        original = bytearray(b'Z'*4096)
        original[44:1580] = b'console=ttyS1'+bytes(1536-len(b'console=ttyS1'))
        header = bytearray(original); select(header, False)
        self.assertEqual(header, original)
        select(header, True)
        self.assertEqual(header[:44],original[:44]); self.assertEqual(header[1580:],original[1580:])
        expected = b'console=ttyS1 sc27xx_ump9620.batteryless_sink=1'
        self.assertEqual(bytes(header[44:1580]).split(b'\0')[0], expected)
        saved=bytes(header);select(header,True);self.assertEqual(header,saved)
        bad=bytearray(header);bad[44:1580]=expected.replace(b'=1',b'=0')+bytes(1536-len(expected))
        with self.assertRaises(ValueError):select(bad,True)
        big=bytearray(original);big[44:1580]=b'x'*1535+b'\0'
        with self.assertRaises(ValueError):select(big,True)


class SinkRoleAdapter(ShellTest):
    def setUp(self):
        super().setUp()
        self.root=self.tmp/'root'; self.port=self.root/'sys/class/typec/port0'
        (self.port/'device').mkdir(parents=True)
        driver=self.tmp/'drivers/ump9620-tcpm';driver.mkdir(parents=True)
        (self.port/'device/driver').symlink_to(driver,target_is_directory=True)
        self.profile=self.tmp/'profile';self.profile.write_text('Y\n')
        self.state=self.tmp/'request';self.state.write_text('new\nhost\npending\n')
        self.role=self.port/'data_role';self.role.write_text('device\n')
        (self.port/'power_role').write_text('sink\n')
        (self.port/'power_operation_mode').write_text('usb_power_delivery\n')

    def call(self,shell,desired,token='new'):
        return self.script(shell,TOP/'rootfs/overlay/opt/mu300/bin/mu300-usb-pd-sink',desired,
                           MU300_SYSROOT=self.root,MU300_USB_SINK_PROFILE=self.profile,
                           MU300_USB_ROLE_STATE=self.state,MU300_USB_ROLE_REQUEST=token,
                           MU300_USB_SINK_LOCK=self.tmp/'swap.lock')

    def test_pd_swap_changes_only_data_role(self):
        for shell in self.each_shell():
            self.role.write_text('device\n')
            self.assertEqual(self.call(shell,'host').returncode,0)
            self.assertEqual(self.role.read_text(),'host\n')
            self.assertEqual((self.port/'power_role').read_text(),'sink\n')
            self.assertFalse((self.port/'port_type').exists())

    def test_pending_contract_and_cancelled_request(self):
        for shell in self.each_shell():
            self.role.write_text('device\n')
            (self.port/'power_operation_mode').write_text('default\n')
            self.assertEqual(self.call(shell,'host').returncode,0)
            self.assertEqual(self.role.read_text(),'device\n')
            self.state.write_text('latest\ndevice\npending\n')
            (self.port/'power_operation_mode').write_text('usb_power_delivery\n')
            self.assertEqual(self.call(shell,'host').returncode,0)
            self.assertEqual(self.role.read_text(),'device\n')
            self.state.write_text('new\nhost\npending\n')

    def test_wrong_profile_or_power_role_fails_without_raw_fallback(self):
        for shell in self.each_shell():
            self.profile.write_text('N\n')
            self.assertNotEqual(self.call(shell,'host').returncode,0)
            self.profile.write_text('Y\n');(self.port/'power_role').write_text('source\n')
            self.assertNotEqual(self.call(shell,'host').returncode,0)
            self.assertEqual(self.role.read_text(),'device\n')
            (self.port/'power_role').write_text('sink\n')

    def test_non_pd_partner_rejection_keeps_power_and_device_mode(self):
        partner=self.port.with_name('port0-partner');partner.mkdir()
        (partner/'supports_usb_power_delivery').write_text('no\n')
        (self.port/'power_operation_mode').write_text('default\n')
        for shell in self.each_shell():
            self.assertNotEqual(self.call(shell,'host').returncode,0)
            self.assertEqual(self.role.read_text(),'device\n')
            self.assertEqual((self.port/'power_role').read_text(),'sink\n')
