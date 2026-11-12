"""Run the actual TCA helper against a bounded fake register block."""
from pathlib import Path
import re
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
driver = (TOP/'upstream/port/drivers/usb/phy/phy-sprd-ums9620-ssphy.c').read_text()
init_gate = re.search(r'\tif \(!ret && sram_ready && tca_ready\)\n'
                     r'\t\tret = sprd_ssphy_finish_tca_init\(phy\);', driver)[0]
selector_start = driver.index('static int sprd_ssphy_set_flip_override(')
selector = driver[selector_start:driver.index('\n}', selector_start)+2]
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdlib.h>
#include <limits.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define READ_ONCE(value) (value)
#define WRITE_ONCE(target,value) ((target)=(value))
static int tca_flip_override=-1;
struct kernel_param { void *arg; };
static int kstrtoint(const char *s, unsigned int base, int *result) {
 char *end;long n;errno=0;n=strtol(s,&end,base);
 if(errno||n<INT_MIN||n>INT_MAX)return -ERANGE;
 if(end==s||(*end&&(*end!='\n'||end[1])))return -EINVAL;
 *result=n;return 0;
}
__SELECTOR__
#define REG_AON_APB_BOOT_MODE 0x2c
#define PHY_INIT_TIMEOUT 500
struct sprd_ssphy { void *base, *aon_apb, *dev; bool orientation_error, is_host, bc12_enabled, data_ready, role_sync_pending; };
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
static int complete_init(struct sprd_ssphy *phy, int ret, bool sram_ready, bool tca_ready) {
__INIT_GATE__
 return ret;
}
int main(void) {
 struct sprd_ssphy phy={.base=registers};
 struct kernel_param param={.arg=&tca_flip_override};
 const char *invalid[]={"-2","2","9","auto","0garbage","999999999999999999999999"};
 for(unsigned int i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++) {
  tca_flip_override=-1;writes=0;
  assert(sprd_ssphy_set_flip_override(invalid[i],&param)<0 && tca_flip_override==-1 && !writes);
 }
 assert(!sprd_ssphy_set_flip_override("0",&param) && tca_flip_override==0 && !writes);
 assert(!sprd_ssphy_set_flip_override("1\n",&param) && tca_flip_override==1 && !writes);
 assert(!sprd_ssphy_set_flip_override("-1",&param) && tca_flip_override==-1 && !writes);
 for(int choice=-1;choice<=1;choice++) for(int pin=0;pin<2;pin++) {
  tca_flip_override=choice;boot=pin?BIT(10):0;
  assert(sprd_ssphy_tca_flip(boot)==(choice<0?(pin?0:BIT(2)):(choice?BIT(2):0)));
 }
 tca_flip_override=-1;
 for(int sram=0;sram<2;sram++) for(int ack=0;ack<2;ack++) {
  phy.is_host=true;phy.role_sync_pending=true;phy.orientation_error=true;
  boot=0;registers[4]=1;registers[5]=0x17;registers[13]=0x107;writes=0;
  assert(!complete_init(&phy,0,sram,ack) && !writes);
  assert(phy.role_sync_pending==!(sram&&ack));
  assert(phy.orientation_error==!(sram&&ack));
 }
 phy.role_sync_pending=true;phy.orientation_error=true;writes=0;
 assert(complete_init(&phy,-EIO,true,true)==-EIO && !writes && phy.role_sync_pending);
 phy.orientation_error=false;
 /* The actual stock binary leaves a correctly initialized controller-sync
  * mode in place. Do not replace it merely because it is not system mode.
  * Its active input is TCPC, not the inactive system-mode register. */
 for(int host=0;host<2;host++) {
  phy.is_host=host;registers[4]=1|(host?0:16);
  registers[5]=0x17;registers[6]=0;registers[13]=0x107;boot=0;writes=0;
  assert(!sprd_ssphy_sync_orientation(&phy,false) && !writes);
 }
 /* A real reset/init already applied both data role and orientation. The
  * stale pending flag from device -> host must not pulse the new link.
  * Exercise both cable directions and both roles, including an old error. */
 for(int host=0;host<2;host++) for(int flipped=0;flipped<2;flipped++) {
  phy.is_host=host;phy.role_sync_pending=true;phy.orientation_error=true;
  boot=flipped?0:BIT(10);registers[4]=1|(host?0:16);
  registers[5]=flipped?0x17:0x13;registers[6]=0;
  registers[13]=flipped?0x107:0x103;writes=0;
  assert(!sprd_ssphy_finish_tca_init(&phy) && !writes);
  assert(!phy.role_sync_pending && !phy.orientation_error);
  assert(!sprd_ssphy_sync_orientation(&phy,phy.role_sync_pending) && !writes);
 }
 /* Init ACK alone is insufficient: a mismatched physical output must be
  * repaired, and a failed repair/read must retain pending state. */
 phy.is_host=true;boot=0;registers[4]=1;registers[5]=0x17;
 registers[6]=0;registers[13]=0x103;phy.role_sync_pending=true;writes=0;
 assert(!sprd_ssphy_finish_tca_init(&phy) && writes>0 && !phy.role_sync_pending);
 registers[13]=0;phy.role_sync_pending=true;stuck=1;
 assert(sprd_ssphy_finish_tca_init(&phy)==-ETIMEDOUT && phy.role_sync_pending && phy.orientation_error);
 stuck=0;read_error=-EIO;writes=0;
 assert(sprd_ssphy_finish_tca_init(&phy)==-EIO && phy.role_sync_pending && phy.orientation_error && !writes);
 read_error=0;assert(!sprd_ssphy_finish_tca_init(&phy));
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
 /* The failed F50 cable orientation retains the same boot/CC indication.
  * An explicit staged override must select the opposite output, and auto
  * must restore the original choice, through the same bounded safe handoff. */
 read_error=0;stuck=0;boot=0;phy.is_host=true;
 registers[4]=1;registers[5]=7;registers[6]=7;registers[13]=0x107;
 tca_flip_override=0;writes=0;
 assert(!sprd_ssphy_sync_orientation(&phy,false) && writes>0 && !(registers[13]&4));
 tca_flip_override=-1;writes=0;
 assert(!sprd_ssphy_sync_orientation(&phy,false) && writes>0 && (registers[13]&4));
}
'''.replace('__INIT_GATE__', init_gate).replace('__SELECTOR__', selector)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'ums9620-tca.h').write_bytes((TOP/'upstream/port/drivers/usb/phy/ums9620-tca.h').read_bytes())
    (root/'test.c').write_text(source)
    subprocess.run(['cc','-Wall','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('PASS: fresh-init ACK/SRAM/error gates, controller-sync retained for both roles/orientations, live role handoff, output mismatch, timeout and explicit retry')
