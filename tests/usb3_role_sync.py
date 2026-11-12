"""Exercise the actual platform role hook with fake controller/PHY operations."""
import ast
from pathlib import Path
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
tree = ast.parse((TOP/'upstream/port/install.py').read_text())
prepare = next(ast.literal_eval(node.value) for node in ast.walk(tree)
               if isinstance(node, ast.Assign) and
               any(isinstance(t, ast.Name) and t.id == 'prepare' for t in node.targets))
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#define DWC3_GCTL_PRTCAP_HOST 1
#define DWC3_GCTL_PRTCAP_DEVICE 2
#define DWC3_GCTL_PRTCAP_OTG 3
struct device { bool of_node; };
struct phy { void *otg; bool inited, battery; };
struct controller { struct device *dev; struct phy *usb2_phy, *usb3_phy; int current_dr_role; };
static int calls, failure, started, exited, initialized, init_failure, fail_on_call;
static bool prepared, host;
static bool of_device_is_compatible(bool n, const char *s) { return n; }
static bool ums9620_ssphy_is_initialized(struct phy *p) { return p->inited; }
static int otg_set_vbus(void *otg, bool on) {
 struct phy *p=otg;
 assert(!started); calls++; host=on; prepared=p->inited;
 return fail_on_call && calls==fail_on_call ? -22 : failure;
}
static void dwc3_core_exit(struct controller *dwc) {
 assert(!started && dwc->usb2_phy->inited);
 dwc->usb2_phy->inited=false; prepared=false; exited++;
}
static int dwc3_core_init_for_resume(struct controller *dwc) {
 assert(!started && !dwc->usb2_phy->inited);
 initialized++;prepared=false;
 if (!init_failure) dwc->usb2_phy->inited=true;
 return init_failure;
}
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
static int transition(struct controller *dwc, int desired_dr_role) {
 int ret=0;
''' + prepare + r'''
 if (dwc->dev->of_node && dwc->usb2_phy==dwc->usb3_phy && desired_dr_role!=3)
  assert(prepared && dwc->usb2_phy->inited);
 dwc->current_dr_role=desired_dr_role;
 started=1;
out:
 return ret;
}
static void reset(struct controller *c, int role, bool ready) {
 calls=failure=started=exited=initialized=init_failure=fail_on_call=0;
 prepared=host=false;c->current_dr_role=role;c->usb2_phy->inited=ready;
}
int main(void) {
 struct device dev={true}; struct phy phy={0},other={0};
 struct controller c={&dev,&phy,&phy,0};phy.otg=&phy;
 /* Both battery and battery-less hosts require the same full lifecycle.
  * role=0 can still have an initialized core (first selection, or a prior
  * error with no shutdown); never enable its clocks twice. */
 for(int battery=0;battery<=1;battery++) {
  phy.battery=battery;
  for(int prior=0;prior<=2;prior+=2) {
   reset(&c,prior,true);
   assert(!transition(&c,1) && exited==1 && initialized==1 && calls==2 && host && started);
  }
 }
 /* Same-role direction changes and a healthy device retain their lifecycle. */
 reset(&c,1,true);assert(!transition(&c,1)&&!exited&&!initialized&&calls==1&&host);
 reset(&c,2,true);assert(!transition(&c,2)&&!exited&&!initialized&&calls==1&&!host);
 /* Initial role with core already off restores either role exactly once. */
 for(int mode=1;mode<=2;mode++) {
  reset(&c,0,false);
  assert(!transition(&c,mode)&&!exited&&initialized==1&&calls==2&&started);
  assert(host==(mode==1));
 }
 /* A preparation failure after shutdown must recover even when the next
  * explicit request is DEVICE, rather than another HOST retry. */
 for(int next=1;next<=2;next++) {
  reset(&c,2,true);failure=-5;
  assert(transition(&c,1)==-5&&exited==1&&!initialized&&!started&&!c.current_dr_role&&!phy.inited);
  failure=0;assert(!transition(&c,next)&&exited==1&&initialized==1&&started&&phy.inited);
  assert(host==(next==1));
  reset(&c,2,true);init_failure=-19;
  assert(transition(&c,1)==-19&&exited==1&&initialized==1&&!started&&!c.current_dr_role&&!phy.inited);
  init_failure=0;assert(!transition(&c,next)&&exited==1&&initialized==2&&started&&phy.inited);
  assert(host==(next==1));
  reset(&c,2,true);fail_on_call=2;
  assert(transition(&c,1)==-22&&exited==2&&initialized==1&&!started&&!c.current_dr_role&&!phy.inited);
  fail_on_call=0;assert(!transition(&c,next)&&exited==2&&initialized==2&&started&&phy.inited);
  assert(host==(next==1));
 }
 /* A device preparation error with the core still on needs no extra init. */
 reset(&c,2,true);failure=-5;
 assert(transition(&c,2)==-5&&!exited&&!initialized&&!started&&!c.current_dr_role&&phy.inited);
 failure=0;assert(!transition(&c,2)&&!exited&&!initialized&&started);
 reset(&c,2,true);dev.of_node=false;
 assert(!transition(&c,1)&&!calls&&!exited&&!initialized);
 dev.of_node=true;c.usb3_phy=&other;reset(&c,2,true);
 assert(!transition(&c,1)&&!calls&&!exited&&!initialized);
 c.usb3_phy=&phy;reset(&c,2,true);
 assert(!transition(&c,3)&&!calls&&!exited&&!initialized);
}
'''
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'test.c').write_text(source)
    subprocess.run(['cc','-Wall','-Werror','-Wno-unused-parameter',
                    str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('PASS: battery/battery-less lifecycle, first role without duplicate init, host/device recovery after errors, same-role and non-UMS9620 isolation')
