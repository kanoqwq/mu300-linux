"""Replay detector-before-init ordering against the actual PHY decision helper."""
from pathlib import Path
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
s = (TOP/'upstream/port/drivers/usb/phy/phy-sprd-ums9620-ssphy.c').read_text()
start = s.index('static int sprd_ssphy_restore_data_state(')
helper = s[start:s.index('\n}', start)+2]
init = s[s.index('static int sprd_ssphy_init('):s.index('static void sprd_ssphy_shutdown(')]
assert init.index('atomic_set(&phy->inited, 1)') < init.index('sprd_ssphy_restore_data_state(phy)')
code = r'''
#include <stdbool.h>
#include <assert.h>
#define READ_ONCE(x) (x)
#define USB_SPEED_UNKNOWN 0
enum usb_charger_type {UNKNOWN_TYPE, SDP_TYPE, DCP_TYPE, CDP_TYPE};
struct usb_phy {enum usb_charger_type chg_type;};
struct sprd_ssphy {bool bc12_enabled,is_host,vbus_present; int dpdm_lock;struct usb_phy phy;};
static bool inited,valid;
static int connects,disconnects,locks;
static void mutex_lock(int *m){assert(!*m);*m=1;locks++;}
static void mutex_unlock(int *m){assert(*m);*m=0;}
static int sprd_ssphy_notify_connect(struct usb_phy *p,int speed){connects++;if(inited)valid=true;return 0;}
static int sprd_ssphy_notify_disconnect(struct usb_phy *p,int speed){disconnects++;if(inited)valid=false;return 0;}
''' + helper + r'''
int main(void) {
 struct sprd_ssphy p={.bc12_enabled=true,.vbus_present=true};
 for (int type=UNKNOWN_TYPE; type<=CDP_TYPE;type++) {
  p.phy.chg_type=type;inited=false;valid=false;
  /* Early detector callback cannot write a not-yet-initialized PHY. */
  if(type==SDP_TYPE||type==CDP_TYPE)sprd_ssphy_notify_connect(&p.phy,0);
  else sprd_ssphy_notify_disconnect(&p.phy,0);
  inited=true;valid=true; /* Hardware init's unconditional valid write. */
  assert(!sprd_ssphy_restore_data_state(&p));
  assert(valid==(type==SDP_TYPE||type==CDP_TYPE));
 }
 p.vbus_present=false;p.phy.chg_type=SDP_TYPE;valid=true;
 assert(!sprd_ssphy_restore_data_state(&p)&&!valid);
 p.is_host=true;valid=true;connects=disconnects=0;
 assert(!sprd_ssphy_restore_data_state(&p)&&valid&&!connects&&!disconnects);
 p.bc12_enabled=false;p.is_host=false;locks=0;valid=true;
 assert(!sprd_ssphy_restore_data_state(&p)&&valid&&!locks);
}
'''
with tempfile.TemporaryDirectory() as directory:
    p=Path(directory)
    (p/'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS: detector-before-init reapplied; SDP/CDP, charger/unknown, detach, host/sink and non-battery isolation')
