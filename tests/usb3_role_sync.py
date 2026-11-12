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
struct phy { void *otg; };
struct controller { struct device *dev; struct phy *usb2_phy, *usb3_phy; int current_dr_role; };
static int calls, failure, started, exited, initialized, init_failure, fail_on_call;
static bool battery, prepared;
static bool host;
static bool of_device_is_compatible(bool n, const char *s) { return n; }
static int otg_set_vbus(void *otg, bool on) {
 assert(!started); calls++; host=on; prepared=true;
 return fail_on_call && calls==fail_on_call ? -22 : failure;
}
static bool ums9620_ssphy_host_resume_needed(struct phy *p) {return battery;}
static void dwc3_core_exit(struct controller *dwc) {assert(!started);exited++;}
static int dwc3_core_init_for_resume(struct controller *dwc) {
 assert(!started && exited && host);initialized++;prepared=false;return init_failure;
}
#define dev_err(...) ((void)0)
static int transition(struct controller *dwc, int desired_dr_role) {
 int ret=0;
''' + prepare + r'''
 if (dwc->dev->of_node && dwc->usb2_phy==dwc->usb3_phy && desired_dr_role!=3)
  assert(prepared);
 started=1;
out:
 return ret;
}
int main(void) {
 struct device dev={true}; struct phy phy={0},other={0};
 struct controller c={&dev,&phy,&phy,0};
 for(int mode=1;mode<=2;mode++) {
  calls=0; started=0; failure=0;
  assert(transition(&c,mode)==0 && calls==1 && started);
  assert(host==(mode==1));
 }
 calls=0;started=0;failure=-5;c.current_dr_role=2;
 assert(transition(&c,1)==-5 && calls==1 && !started && !c.current_dr_role);
 failure=0;started=0;assert(transition(&c,1)==0 && started);
 /* Only battery-board device->host resumes; live host, device and other
  * platforms keep their existing lifecycle. Failure never starts xHCI. */
 battery=true;c.current_dr_role=2;exited=initialized=0;started=0;
 assert(!transition(&c,1) && exited==1 && initialized==1 && started);
 c.current_dr_role=1;exited=initialized=0;started=0;
 assert(!transition(&c,1) && !exited && !initialized && started);
 c.current_dr_role=2;exited=initialized=0;started=0;
 assert(!transition(&c,2) && !exited && !initialized && started);
 c.current_dr_role=2;exited=initialized=0;started=0;init_failure=-19;
 assert(transition(&c,1)==-19 && !started && !c.current_dr_role);
 init_failure=0;started=0;
 assert(!transition(&c,1) && started);
 c.current_dr_role=2;exited=initialized=0;started=0;failure=-5;
 assert(transition(&c,1)==-5 && exited==1 && !initialized && !started);
 assert(!c.current_dr_role);
 failure=0;started=0;
 assert(!transition(&c,1) && exited==1 && initialized==1 && started);
 c.current_dr_role=2;exited=initialized=calls=0;started=0;fail_on_call=2;
 assert(transition(&c,1)==-22 && exited==2 && initialized==1 && !started);
 assert(!c.current_dr_role);
 fail_on_call=0;started=0;
 assert(!transition(&c,1) && exited==2 && initialized==2 && started);
 battery=false;
 dev.of_node=false;calls=0;started=0;
 assert(!transition(&c,1) && !calls);
 dev.of_node=true;c.usb3_phy=&other;calls=0;started=0;
 assert(!transition(&c,1) && !calls);
 c.usb3_phy=&phy;calls=0;started=0;
 assert(!transition(&c,3) && !calls);
}
'''
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'test.c').write_text(source)
    subprocess.run(['cc','-Wall','-Werror','-Wno-unused-parameter',
                    str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('PASS: role preparation precedes controller start; failure prevents start; explicit retry and non-UMS9620 isolation')
