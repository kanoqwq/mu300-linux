"""Exercise production disconnect -> host handoff, including host with sink VBUS."""
from pathlib import Path
import re, subprocess, tempfile
TOP=Path(__file__).resolve().parents[1]
s=(TOP/'upstream/port/drivers/usb/phy/phy-sprd-ums9620-ssphy.c').read_text()
def function(name, signature="static int"):
 match=re.search(re.escape(signature)+r' '+re.escape(name)+r'\([^;]*\)\n\{',s)
 assert match, 'missing production function definition: '+name
 start=match.start()
 return s[start:s.index('\n}',start)+2]
constants='\n'.join(line for line in s.splitlines() if line.startswith(('#define BIT_ANLG_', '#define REG_ANLG_')))
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#define BIT(n) (1U<<(n))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define dev_info(...) ((void)0)
#include "regs.h"
#include "mask.h"
typedef unsigned int u32;
struct usb_phy {void *dev;};
struct sprd_ssphy {struct usb_phy phy; void *dev;u32 *aon_apb,*ana_g0l;bool is_host,bc12_enabled,data_ready,role_sync_pending;int dpdm_lock,inited;u32 host_eye_pattern,device_eye_pattern;};
enum usb_device_speed {USB_SPEED_UNKNOWN};
static int syncs,error,sync_error;static bool last_changed;
static int atomic_read(int *v){return *v;}
static void mutex_lock(int *l){assert(!*l);*l=1;}
static void mutex_unlock(int *l){assert(*l);*l=0;}
static int regmap_write(u32 *map,u32 reg,u32 v){map[reg/4]=v;return error;}
static int regmap_update_bits(u32 *map,u32 reg,u32 mask,u32 v){map[reg/4]=(map[reg/4]&~mask)|(v&mask);return error;}
static void sprd_ssphy_bc12_role(struct sprd_ssphy *p){assert(p->dpdm_lock);}
static int sprd_ssphy_sync_orientation(struct sprd_ssphy *p,bool changed){assert(!p->dpdm_lock);syncs++;last_changed=changed;return sync_error;}
'''+constants+'\n'+function('ums9620_ssphy_is_initialized', 'bool')+'\n'+function('sprd_ssphy_set_vbus')+'\n'+function('sprd_ssphy_notify_disconnect')+r'''
int main(void) {
 u32 aon[0x1000]={0},ana[0x1000]={0};
 struct sprd_ssphy p={.aon_apb=aon,.ana_g0l=ana,.bc12_enabled=true,.inited=1};
 for(int battery=0;battery<2;battery++){
  p.bc12_enabled=battery;
  p.inited=0;assert(!ums9620_ssphy_is_initialized(&p.phy));
  p.inited=1;assert(ums9620_ssphy_is_initialized(&p.phy));
 }
 p.bc12_enabled=true;
 for(int i=0;i<2;i++){
  p.is_host=false;
  mutex_lock(&p.dpdm_lock);assert(!sprd_ssphy_notify_disconnect(&p.phy,USB_SPEED_UNKNOWN));mutex_unlock(&p.dpdm_lock);
  assert(!(aon[REG_AON_APB_USB31DPCOMBPHY_CTRL/4]&MASK_AON_APB_SYS_VBUSVALID));
  assert(!sprd_ssphy_set_vbus(&p.phy,1) && p.is_host);
  assert(aon[REG_AON_APB_USB31DPCOMBPHY_CTRL/4]&MASK_AON_APB_SYS_VBUSVALID);
  assert(aon[REG_AON_APB_OTG_PHY_TEST/4]&MASK_AON_APB_OTG_VBUS_VALID_PHYREG);
  assert(ana[REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1/4]&BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVLDEXT);
  /* A stale disconnect callback cannot invalidate a host. */
  mutex_lock(&p.dpdm_lock);assert(!sprd_ssphy_notify_disconnect(&p.phy,USB_SPEED_UNKNOWN));mutex_unlock(&p.dpdm_lock);
  assert(aon[REG_AON_APB_USB31DPCOMBPHY_CTRL/4]&MASK_AON_APB_SYS_VBUSVALID);
 }
 error=-EIO;syncs=0;assert(sprd_ssphy_set_vbus(&p.phy,1)==-EIO && !syncs);
 /* DWC3 records host while the PHY is shut down, then initializes it.
  * The post-init host call must still synchronize a real role transition. */
 error=0;sync_error=0;syncs=0;p.is_host=false;p.inited=0;p.role_sync_pending=false;
 assert(!sprd_ssphy_set_vbus(&p.phy,1)&&p.is_host&&p.role_sync_pending&&!syncs);
 p.inited=1;
 assert(!sprd_ssphy_set_vbus(&p.phy,1)&&syncs==1&&last_changed&&!p.role_sync_pending);
 assert(!sprd_ssphy_set_vbus(&p.phy,1)&&syncs==2&&!last_changed);
 /* Failed synchronization retains the transition for an explicit retry. */
 p.inited=0;assert(!sprd_ssphy_set_vbus(&p.phy,0)&&p.role_sync_pending);
 p.inited=1;sync_error=-ETIMEDOUT;
 assert(sprd_ssphy_set_vbus(&p.phy,0)==-ETIMEDOUT&&p.role_sync_pending&&last_changed);
 sync_error=0;assert(!sprd_ssphy_set_vbus(&p.phy,0)&&last_changed&&!p.role_sync_pending);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp)
 for name,source in [('regs.h','sprd,qogirn6pro-regs.h'),('mask.h','sprd,qogirn6pro-mask.h')]:
  (root/name).write_bytes((TOP/'upstream/port/include/dt-bindings/soc'/source).read_bytes())
 (root/'test.c').write_text(code)
 subprocess.run(['cc','-Wall','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
 subprocess.run([str(root/'test')],check=True)
print('PASS: actual detach/host handoff restores all internal valid inputs, stale host disconnect ignored, register errors propagated')
