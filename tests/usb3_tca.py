"""Run the actual TCA helper against a bounded fake register block."""
from pathlib import Path
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define REG_AON_APB_BOOT_MODE 0x2c
#define PHY_INIT_TIMEOUT 500
struct sprd_ssphy { void *base, *aon_apb, *dev; bool orientation_error, is_host, bc12_enabled, data_ready; };
static u32 registers[32],boot;
static int writes,read_error,stuck;
#define dev_info(...) ((void)0)
static int regmap_read(void *map, int reg, u32 *value) {
 assert(reg==0x2c);*value=boot;return read_error;
}
static u32 readl(void *address) { return *(u32 *)address; }
static void writel(u32 value,void *address) {
 writes++;
 *(u32 *)address=value;
 /* Synopsys TCA system-configuration mode is 0, controller-sync is 1.
  * Reserved modes must not magically produce a matching physical output. */
 if(address==(void *)((char *)registers+0x18) && !stuck && !(registers[4]&3))
  registers[13]=value&12;
}
#define readl_poll_timeout(address,value,condition,delay,timeout) \
 ((value=readl(address)),(condition)?0:-ETIMEDOUT)
#include "ums9620-tca.h"
int main(void) {
 struct sprd_ssphy phy={.base=registers};
 /* The actual stock binary leaves a correctly initialized controller-sync
  * mode in place. Do not replace it merely because it is not system mode.
  * Its active input is TCPC, not the inactive system-mode register. */
 for(int host=0;host<2;host++) {
  phy.is_host=host;registers[4]=1|(host?0:16);
  registers[5]=0x17;registers[6]=0;registers[13]=0x107;boot=0;writes=0;
  assert(!sprd_ssphy_sync_orientation(&phy,false) && !writes);
 }
 phy.is_host=false;
 /* Cold boot: request matches cable but actual output has not followed.
  * It must still establish system mode and apply the output. */
 registers[4]=0x5601;registers[5]=0xa517;registers[6]=0x1284;registers[13]=0;boot=0;
 assert(sprd_ssphy_sync_orientation(&phy, false)==0);
 assert(registers[5]==0xa517 && registers[6]==0x1284 && !phy.orientation_error);
 assert(registers[4]==0x5610 && registers[13]==4);
 writes=0;assert(sprd_ssphy_sync_orientation(&phy, false)==0 && !writes);
 /* Change only the PHY role, with direction unchanged. */
 phy.is_host=true;writes=0;
 assert(sprd_ssphy_sync_orientation(&phy, false)==0 && writes>0);
 assert(registers[4]==0x5600);
 phy.is_host=false;writes=0;
 assert(sprd_ssphy_sync_orientation(&phy, false)==0 && writes>0);
 assert(registers[4]==0x5610);
 /* Initial device -> host transition must rearm the mux even when its
  * direction matches. The following duplicate role notification must not. */
 writes=0;assert(sprd_ssphy_sync_orientation(&phy, true)==0 && writes>0);
 writes=0;assert(sprd_ssphy_sync_orientation(&phy, false)==0 && !writes);
 boot=BIT(10);assert(sprd_ssphy_sync_orientation(&phy, false)==0);
 assert(registers[5]==0xa517 && registers[6]==0x1280);
 boot=0;stuck=1;assert(sprd_ssphy_sync_orientation(&phy, false)==-ETIMEDOUT);
 assert(phy.orientation_error);
 stuck=0;writes=0;assert(!sprd_ssphy_sync_orientation(&phy, false) && writes>0 && !phy.orientation_error);
 /* A stuck disable must stop before reenabling an unverified link. */
 stuck=1;boot=BIT(10);registers[13]=4;writes=0;
 assert(sprd_ssphy_sync_orientation(&phy, true)==-ETIMEDOUT);
 assert((registers[6]&8) && phy.orientation_error && writes==2);
 stuck=0;assert(!sprd_ssphy_sync_orientation(&phy, true));
 read_error=-EIO;writes=0;assert(sprd_ssphy_sync_orientation(&phy, false)==-EIO && !writes);
}
'''
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'ums9620-tca.h').write_bytes((TOP/'upstream/port/drivers/usb/phy/ums9620-tca.h').read_bytes())
    (root/'test.c').write_text(source)
    subprocess.run(['cc','-Wall','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('PASS: both orientations, unchanged state, unrelated bits, role bits, disabled output, actual output, timeout, explicit retry and regmap error')
