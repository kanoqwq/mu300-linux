"""Execute the actual callbacks against independent USB/power-role models."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from helpers import ShellTest, BIN

ROOT=Path(__file__).resolve().parents[1]
DRIVER=ROOT/'upstream/port/drivers/usb/typec/tcpm/sc27xx-ump9620.c'

def function(text,name):
    start=re.search(r'^static [^\n]*\b'+name+r'\(',text,re.M).start()
    end=text.index('{',start)+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]+'\n'

@unittest.skipUnless(shutil.which('cc'),'requires C compiler')
class PDRoles(unittest.TestCase):
    def test_non_pd_budget_does_not_overwrite_completed_bc12(self):
        text=DRIVER.read_text()
        before=r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
typedef unsigned int u32;
enum { POWER_SUPPLY_USB_TYPE_UNKNOWN, POWER_SUPPLY_USB_TYPE_PD };
enum { POWER_SUPPLY_PROP_USB_TYPE, POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT };
union power_supply_propval {int intval;};
struct tcpc_dev {void *pd;};
struct sc27xx_pd {void *charger;bool pd_capable;};
#define min(a,b) ((a)<(b)?(a):(b))
#define READ_ONCE(x) (x)
static int input=2000000, writes, error, type=-1;
static struct sc27xx_pd *flip_on_property;
static struct sc27xx_pd *to_pd(struct tcpc_dev *d){return d->pd;}
static int power_supply_set_property(void *psy,int prop,const union power_supply_propval *v){
 (void)psy;if(error)return error;
 if(prop==POWER_SUPPLY_PROP_USB_TYPE){type=v->intval;if(flip_on_property)flip_on_property->pd_capable=true;}
 else {assert(prop==POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT);input=v->intval;writes++;}
 return 0;
}
'''
        after=r'''
int main(void){
 struct sc27xx_pd pd={0};struct tcpc_dev tcpc={.pd=&pd};
 /* BC1.2 completed first: a later default-USB callback cannot undo DCP. */
 assert(!sc27xx_set_current_limit(&tcpc,500,5000));
 assert(input==2000000&&!writes&&type==POWER_SUPPLY_USB_TYPE_UNKNOWN);
 /* Rp is also handled by the charger; never create another non-PD owner. */
 assert(!sc27xx_set_current_limit(&tcpc,3000,5000)&&!writes);
 /* RX IRQ between ownership publication and IIN must not split the decision. */
 flip_on_property=&pd;
 assert(!sc27xx_set_current_limit(&tcpc,500,5000)&&!writes&&type==POWER_SUPPLY_USB_TYPE_UNKNOWN);
 flip_on_property=0;
 pd.pd_capable=true;
 assert(!sc27xx_set_current_limit(&tcpc,2000,5000));
 assert(input==2000000&&writes==1&&type==POWER_SUPPLY_USB_TYPE_PD);
 assert(!sc27xx_set_current_limit(&tcpc,1500,5000)&&input==1500000&&writes==2);
 assert(!sc27xx_set_current_limit(&tcpc,3000,5000)&&input==2000000&&writes==3);
 assert(!sc27xx_set_current_limit(&tcpc,0,0)&&writes==3&&type==POWER_SUPPLY_USB_TYPE_UNKNOWN);
 assert(sc27xx_set_current_limit(&tcpc,2000,9000)==-EINVAL&&writes==3);
 error=-EIO;assert(sc27xx_set_current_limit(&tcpc,2000,5000)==-EIO&&writes==3);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'current.c';binary=Path(d)/'current'
            source.write_text(before+function(text,'sc27xx_set_current_limit')+after)
            result=subprocess.run(['cc','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            subprocess.run([str(binary)],check=True)

    def test_power_sink_can_be_usb_host_without_enabling_boost(self):
        text=DRIVER.read_text()
        before=r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
enum usb_role {USB_ROLE_NONE,USB_ROLE_HOST,USB_ROLE_DEVICE};
enum typec_role {TYPEC_SINK,TYPEC_SOURCE};
enum typec_data_role {TYPEC_DEVICE,TYPEC_HOST};
enum tcpm_transmit_status {TCPC_TX_SUCCESS,TCPC_TX_FAILED};
struct tcpc_dev {void *pd;};
struct sc27xx_pd {void *role_sw,*dev,*port;bool attached,tx_pending,tx_sop,pd_capable,stopping;enum typec_role power_role;enum usb_role negotiated_role,data_override;int role_lock;};
#define READ_ONCE(x) (x)
static void mutex_lock(int *m){assert(!*m);*m=1;}
static void mutex_unlock(int *m){assert(*m);*m=0;}
#define SC27XX_PD_CFG0 16
#define SC27XX_PD_SRC_SINK_MODE 512
#define SC27XX_TYPEC_PD_CFG 8
#define SC27XX_TYPEC_PD_CONSTRACT 64
#define dev_warn_ratelimited(...) ((void)0)
#define dev_info(...) ((void)0)
static int writes,usb_calls,power_value,usb_value,write_error,usb_error,tx_completions,contract_writes,last_status;
static int cc_update(struct sc27xx_pd *pd,int reg,int mask,int val){(void)pd;assert(reg==8&&mask==64&&val==64);contract_writes++;return write_error;}
static struct sc27xx_pd *to_pd(struct tcpc_dev *dev){return dev->pd;}
static int pd_update(struct sc27xx_pd *pd,int reg,int mask,int val){(void)pd;assert(reg==16&&mask==512);writes++;power_value=val;return write_error;}
static int usb_role_switch_set_role(void *sw,enum usb_role role){(void)sw;usb_calls++;usb_value=role;return usb_error;}
static void tcpm_pd_transmit_complete(void *port,enum tcpm_transmit_status status){(void)port;last_status=status;tx_completions++;}
'''
        after=r'''
int main(void){
 struct sc27xx_pd pd={0};struct tcpc_dev tcpc={.pd=&pd};
 assert(!sc27xx_set_roles(&tcpc,true,TYPEC_SINK,TYPEC_HOST));
 assert(usb_value==USB_ROLE_HOST&&!power_value&&pd.attached&&pd.power_role==TYPEC_SINK);
 assert(!sc27xx_set_roles(&tcpc,true,TYPEC_SOURCE,TYPEC_HOST));
 assert(usb_value==USB_ROLE_HOST&&power_value==512&&pd.power_role==TYPEC_SOURCE);
 assert(!sc27xx_set_roles(&tcpc,true,TYPEC_SINK,TYPEC_DEVICE));
 assert(usb_value==USB_ROLE_DEVICE&&!power_value);
 assert(!sc27xx_set_roles(&tcpc,false,TYPEC_SINK,TYPEC_HOST));
 assert(usb_value==USB_ROLE_DEVICE&&!pd.attached);
 write_error=-EIO;int calls=usb_calls;
 assert(sc27xx_set_roles(&tcpc,true,TYPEC_SOURCE,TYPEC_HOST)==-EIO&&usb_calls==calls&&!pd.attached);
 write_error=0;usb_error=-EIO;
 assert(sc27xx_set_roles(&tcpc,true,TYPEC_SOURCE,TYPEC_HOST)==-EIO&&!pd.attached);
 usb_error=0;
 pd.tx_sop=true;pd.tx_pending=true;sc27xx_complete_tx(&pd,TCPC_TX_SUCCESS);sc27xx_complete_tx(&pd,TCPC_TX_SUCCESS);
 assert(tx_completions==1&&!pd.tx_pending&&contract_writes==1&&last_status==TCPC_TX_SUCCESS);
 pd.tx_sop=false;pd.tx_pending=true;sc27xx_complete_tx(&pd,TCPC_TX_SUCCESS);
 assert(tx_completions==2&&contract_writes==1);
 pd.tx_sop=true;pd.tx_pending=true;sc27xx_complete_tx(&pd,TCPC_TX_FAILED);
 assert(tx_completions==3&&contract_writes==1);
 write_error=-EIO;pd.tx_pending=true;sc27xx_complete_tx(&pd,TCPC_TX_SUCCESS);
 assert(tx_completions==4&&contract_writes==2&&last_status==TCPC_TX_FAILED);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'roles.c';binary=Path(d)/'roles'
            helpers=(DRIVER.parent/'sc27xx-ump9620-role.h').read_text()
            source.write_text(before+function(helpers,'sc27xx_apply_data_role')+function(text,'sc27xx_set_roles')+function(text,'sc27xx_complete_tx')+after)
            result=subprocess.run(['cc','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            subprocess.run([str(binary)],check=True)


    def test_receive_frame_trailer_is_not_a_second_pd_message(self):
        text=DRIVER.read_text()
        regs=(DRIVER.parent/'sc27xx-ump9620-regs.h').read_text().split('enum sc27xx_state')[0]
        before=r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
typedef uint32_t u32;typedef uint16_t u16;typedef int irqreturn_t;
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
#define cpu_to_le16(v) (v)
#define cpu_to_le32(v) (v)
#define le16_to_cpu(v) (v)
#define le32_to_cpu(v) (v)
#define PD_HEADER_EXT_HDR 0x8000
#define PD_DATA_SOURCE_CAP 1
#define PDO_TYPE_FIXED 0
#define pdo_type(v) (((v)>>30)&3)
#define pdo_fixed_voltage(v) ((((v)>>10)&1023)*50)
#define pd_header_cnt_le(v) (((v)>>12)&7)
#define pd_header_type_le(v) ((v)&31)
#define PD_CTRL_GOOD_CRC 1
#define TCPC_TX_SOP 0
#define IRQ_HANDLED 1
#define dev_warn_ratelimited(...) (++warnings)
"""
        model=r"""
enum tcpm_transmit_status {TCPC_TX_SUCCESS,TCPC_TX_FAILED};
struct pd_message {u16 header;u32 payload[7];};
struct sc27xx_pd {void *regmap,*port,*dev;u32 base,typec_base;int lock;bool tx_pending,tx_sop,pd_capable,rx_enabled;};
static u32 fifo[24],flags;static int at,held,flushes,messages,completions,hard_resets,read_error;
static int empty_reads,stay_empty,status_reads,fail_at=-1,warnings,contract_notifications;static struct pd_message received;
static void mutex_lock(int *p){(void)p;assert(!held);held=1;}
static void mutex_unlock(int *p){(void)p;assert(held);held=0;}
static int regmap_read(void *map,u32 reg,u32 *value){
 (void)map;assert(held);if(reg==SC27XX_INT_FLG){*value=flags;return 0;}
 if(read_error)return -EIO;
 if(reg==SC27XX_PD_STS1){status_reads++;if(stay_empty||empty_reads>0){if(empty_reads>0)empty_reads--;*value=SC27XX_PD_RX_EMPTY;}else *value=17;return 0;}
 assert(reg==SC27XX_PD_RX_BUF);assert(status_reads>0&&!stay_empty&&!empty_reads);
 if(at==fail_at)return -EIO;
 assert(at<24);
 *value=fifo[at++];
 return 0;
}
#define regmap_read_poll_timeout(map,reg,val,cond,interval,timeout) \
 ({int result=0,attempt=0;assert((interval)==50&&(timeout)==2000); \
 do {result=regmap_read((map),(reg),&(val));if(result||(cond))break; \
 if(++attempt>40){result=-ETIMEDOUT;break;}}while(1);result;})
static int regmap_write(void *map,u32 reg,u32 value){(void)map;(void)value;assert(held&&reg==SC27XX_INT_CLR);return 0;}
static int regmap_write_bits(void *map,u32 reg,u32 mask,u32 value){(void)map;assert(held&&reg==SC27XX_PD_CTRL);if(mask&SC27XX_PD_RX_FLASH){assert(value&SC27XX_PD_RX_FLASH);flushes++;}return 0;}
static int pd_update(struct sc27xx_pd *pd,u32 reg,u32 mask,u32 value){(void)pd;assert(held&&reg==SC27XX_PD_MESG_ID_CFG&&mask==SC27XX_PD_MESS_ID_MASK&&!value);return 0;}
static int cc_update(struct sc27xx_pd *pd,u32 reg,u32 mask,u32 value){(void)pd;assert(held&&reg==SC27XX_TYPEC_PD_CFG&&mask==SC27XX_TYPEC_PD_CONSTRACT&&value==mask);contract_notifications++;return 0;}
static void tcpm_pd_receive(void *port,const struct pd_message *msg,int sop){(void)port;assert(sop==TCPC_TX_SOP);received=*msg;messages++;}
static void tcpm_pd_transmit_complete(void *port,enum tcpm_transmit_status status){(void)port;(void)status;completions++;}
static void tcpm_pd_hard_reset(void *port){(void)port;hard_resets++;}
"""
        after=r"""
int main(void){
 struct sc27xx_pd pd={.rx_enabled=true};
 empty_reads=2;flags=SC27XX_PD_PKG_RV_FLAG;fifo[0]=0x1161;fifo[1]=0x90c8;fifo[2]=0x2601;fifo[3]=0xc0d8;fifo[4]=0xb104;
 sc27xx_pd_irq(1,&pd);assert(messages==1&&at==3&&flushes==1&&received.header==0x1161&&received.payload[0]==0x260190c8&&!held&&status_reads==4);
 at=0;fifo[0]=PD_CTRL_GOOD_CRC;flags|=SC27XX_PD_TX_OK_FLAG;pd.tx_pending=true;pd.tx_sop=true;
 sc27xx_pd_irq(1,&pd);assert(messages==1&&completions==1&&!pd.tx_pending&&at==1&&flushes==2&&!held);
 read_error=1;flags=SC27XX_PD_PKG_RV_FLAG;sc27xx_pd_irq(1,&pd);assert(messages==1&&completions==1&&flushes==3&&!held);
 flags=SC27XX_PD_HARD_RST_FLAG;sc27xx_pd_irq(1,&pd);assert(hard_resets==1&&!held);
 /* A receive IRQ with a still-empty FIFO must never pop stale data. */
 read_error=0;at=0;stay_empty=1;pd.pd_capable=false;flags=SC27XX_PD_PKG_RV_FLAG;
 sc27xx_pd_irq(1,&pd);assert(at==0&&messages==1&&flushes==5&&!pd.pd_capable&&!held);
 /* A failed/incomplete payload also cannot acquire PD current ownership. */
 stay_empty=0;fail_at=2;fifo[0]=0x1161;
 sc27xx_pd_irq(1,&pd);assert(at==2&&messages==1&&flushes==6&&!pd.pd_capable&&!held);
 fail_at=-1;at=0;fifo[0]=0x43a1;
 const u32 source[]={0x1912c,0x2d0c8,0x4b0c8,0x640c8};
 for(int i=0;i<4;i++){fifo[1+2*i]=source[i]&0xffff;fifo[2+2*i]=source[i]>>16;}
 fifo[9]=0xc0d8;fifo[10]=0xb104;
 sc27xx_pd_irq(1,&pd);assert(messages==2&&at==9&&flushes==7&&pd.pd_capable&&!held);
 for(int i=0;i<4;i++)assert(received.payload[i]==source[i]);
 /* Report, but do not rewrite/discard, the observed malformed SourceCaps. */
 at=0;fifo[1]=0x8f7d;fifo[2]=0x3800;int old_warnings=warnings;
 sc27xx_pd_irq(1,&pd);assert(messages==3&&at==9&&flushes==8&&warnings==old_warnings+1);
 /* Automatic GoodCRC may produce TX_OK without any RX FIFO frame. */
 int old_completions=completions,old_contracts=contract_notifications;
 pd.tx_pending=true;pd.tx_sop=true;flags=SC27XX_PD_TX_OK_FLAG;
 sc27xx_pd_irq(1,&pd);
 assert(completions==old_completions+1&&contract_notifications==old_contracts+1&&!pd.tx_pending&&!held);
 sc27xx_pd_irq(1,&pd);
 assert(completions==old_completions+1&&contract_notifications==old_contracts+1);
 /* RX-disabled and hard-reset trailer IRQs cannot reach TCPM as frames. */
 pd.rx_enabled=false;at=0;flags=SC27XX_PD_PKG_RV_FLAG;
 int old_messages=messages,old_flushes=flushes;
 sc27xx_pd_irq(1,&pd);assert(!at&&messages==old_messages&&flushes==old_flushes+1);
 pd.rx_enabled=true;flags|=SC27XX_PD_HARD_RST_FLAG;
 sc27xx_pd_irq(1,&pd);assert(!at&&messages==old_messages&&hard_resets==2);

 return 0;
}
"""
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'receive.c';binary=Path(d)/'receive'
            source.write_text(before+regs+model+function(text,'sc27xx_reset_fifos')+function(text,'sc27xx_complete_tx')+function(text,'sc27xx_receive')+function(text,'sc27xx_pd_irq')+after)
            result=subprocess.run(['cc','-Wall','-Wextra','-Werror','-Wno-unused-parameter',str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            subprocess.run([str(binary)],check=True)


class NativeRoleHelper(ShellTest):
    def test_pd_data_role_requests_never_force_charger_registers(self):
        root=self.tmp/'root'
        port=root/'sys/class/typec/port0'
        port.mkdir(parents=True)
        (port/'data_role').write_text('device\n')
        (port/'power_role').write_text('sink\n')
        (port/'power_operation_mode').write_text('usb_power_delivery\n')
        (port/'port_type').write_text('dual\n')
        control=root/'sys/bus/platform/drivers/ump9620-tcpm/controller/usb_role_override'
        control.parent.mkdir(parents=True);control.write_text('auto\n')
        self.stub('mu300-device','echo u30air')
        self.stub('id','echo 0')
        self.stub('i2cget','echo unexpected >> "$STUBLOG/i2c"; exit 1')
        self.stub('i2cset','echo unexpected >> "$STUBLOG/i2c"; exit 1')
        for shell in self.each_shell():
            for mode in ('boot','host','device','auto','status'):
                result=self.script(shell,BIN/'mu300-usb',mode,MU300_SYSROOT=root)
                self.assertEqual(result.returncode,0,result.stderr)
                if mode!='status':self.assertEqual(control.read_text().strip(),'auto' if mode=='boot' else mode)
            self.assertEqual((port/'power_role').read_text(),'sink\n')
            self.assertEqual((port/'data_role').read_text(),'device\n')
            self.assertFalse((self.tmp/'i2c').exists())

    def test_non_pd_and_unattached_force_only_data_with_automatic_power(self):
        root=self.tmp/'native-root';port=root/'sys/class/typec/port0';port.mkdir(parents=True)
        (port/'data_role').write_text('host [device]\n')
        (port/'power_role').write_text('source [sink]\n')
        (port/'port_type').write_text('[dual] source sink\n')
        control=root/'sys/bus/platform/drivers/ump9620-tcpm/controller/usb_role_override'
        control.parent.mkdir(parents=True);control.write_text('auto\n')
        self.stub('mu300-device','echo u30air');self.stub('id','echo 0')
        self.stub('i2cget','echo unexpected >> "$STUBLOG/i2c"; exit 1')
        self.stub('i2cset','echo unexpected >> "$STUBLOG/i2c"; exit 1')
        for shell in self.each_shell():
            for mode in ('default','1.5A','3.0A',''):
                (port/'power_operation_mode').write_text(mode+'\n')
                for role in ('host','device','auto','boot'):
                    result=self.script(shell,BIN/'mu300-usb',role,MU300_SYSROOT=root)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual((port/'port_type').read_text(),'dual\n')
                    self.assertEqual(control.read_text().strip(),'auto' if role=='boot' else role)
                    self.assertEqual((port/'data_role').read_text(),'host [device]\n')
                    self.assertEqual((port/'power_role').read_text(),'source [sink]\n')
            self.assertFalse((self.tmp/'i2c').exists())

    def test_missing_override_interface_fails_without_changing_power_policy(self):
        root=self.tmp/'pd-root';port=root/'sys/class/typec/port0';port.mkdir(parents=True)
        (port/'data_role').write_text('[host] device\n')
        (port/'port_type').write_text('sink\n')
        (port/'power_operation_mode').write_text('usb_power_delivery\n')
        self.stub('mu300-device','echo u30air');self.stub('id','echo 0')
        for shell in self.each_shell():
            result=self.script(shell,BIN/'mu300-usb','host',MU300_SYSROOT=root)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('matching native Type-C kernel interface',result.stderr)
            self.assertEqual((port/'port_type').read_text(),'sink\n')
