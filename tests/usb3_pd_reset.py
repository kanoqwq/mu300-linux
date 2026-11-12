"""Exercise production RX gating and hard-reset register order, including faults."""
from pathlib import Path
import subprocess
import tempfile
import re

TOP = Path(__file__).resolve().parents[1]
path = TOP / 'upstream/port/drivers/usb/typec/tcpm/sc27xx-ump9620.c'
source = path.read_text()
regs = (path.parent / 'sc27xx-ump9620-regs.h').read_text().split('enum sc27xx_state')[0]


def function(name):
    match = re.search(r'static int ' + name + r'\([^;]*\)\n\{', source)
    assert match
    start = match.start()
    return source[start:source.index('\n}', start) + 2]


code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#define BIT(n) (1U<<(n))
#define GENMASK(h,l) ((~0U<<(l))&(~0U>>(31-(h))))
typedef uint32_t u32;
#define le16_to_cpu(v) (v)
#define le32_to_cpu(v) (v)
#define pd_header_cnt_le(v) (((v)>>12)&7)
#define PD_REV30 2
enum tcpm_transmit_type {TCPC_TX_SOP,TCPC_TX_HARD_RESET};
struct pd_message {unsigned short header;u32 payload[7];};
struct sc27xx_pd {void *regmap;u32 base;int lock;bool rx_enabled,tx_pending,tx_sop;};
struct tcpc_dev {struct sc27xx_pd *pd;};
''' + regs + r'''
struct operation {u32 reg,mask,value;};
static struct operation ops[100];static int count,fault=-1;
static u32 registers[64];
static struct sc27xx_pd *to_pd(struct tcpc_dev *tcpc){return tcpc->pd;}
static void mutex_lock(int *lock){assert(!*lock);*lock=1;}
static void mutex_unlock(int *lock){assert(*lock);*lock=0;}
static int write_op(struct sc27xx_pd *pd,u32 reg,u32 mask,u32 value){
 assert(pd->lock);assert(reg<sizeof(registers));
 assert(count<100);ops[count]=(struct operation){reg,mask,value};
 if(count++==fault)return -EIO;
 registers[reg/4]=(registers[reg/4]&~mask)|(value&mask);return 0;
}
static int pd_update(struct sc27xx_pd *pd,u32 reg,u32 mask,u32 value){return write_op(pd,reg,mask,value);}
static int regmap_write_bits(void *map,u32 reg,u32 mask,u32 value){return write_op(map,reg,mask,value);}
static int regmap_write(void *map,u32 reg,u32 value){return write_op(map,reg,~0U,value);}
''' + function('sc27xx_reset_fifos') + function('sc27xx_set_rx') + function('sc27xx_transmit') + r'''
int main(void){
 struct sc27xx_pd pd={0};pd.regmap=&pd;
 struct tcpc_dev tcpc={&pd};
 /* Seed unrelated interrupt/config bits and a pending old transmission. */
 registers[SC27XX_INT_EN/4]=SC27XX_PD_TX_OK_FLAG;
 registers[SC27XX_PD_CFG0/4]=SC27XX_PD_BIST_MODE_EN;
 pd.tx_pending=true;
 assert(!sc27xx_set_rx(&tcpc,true)&&pd.rx_enabled&&!pd.tx_pending&&!pd.lock);
 assert(ops[0].reg==SC27XX_INT_EN&&!ops[0].value);
 assert(ops[3].reg==SC27XX_PD_CTRL&&ops[3].mask==SC27XX_PD_RX_FLASH);
 assert(ops[4].mask==SC27XX_PD_TX_FLASH&&ops[5].mask==SC27XX_PD_RX_ID_CLR);
 assert(ops[count-1].reg==SC27XX_INT_EN&&ops[count-1].value==SC27XX_PD_PKG_RV_FLAG);
 assert(registers[SC27XX_INT_EN/4]&SC27XX_PD_TX_OK_FLAG);
 assert(registers[SC27XX_PD_CFG0/4]&SC27XX_PD_BIST_MODE_EN);
 count=0;pd.tx_pending=true;
 assert(!sc27xx_set_rx(&tcpc,false)&&!pd.rx_enabled&&!pd.tx_pending&&!pd.lock);
 assert(!(registers[SC27XX_INT_EN/4]&SC27XX_PD_PKG_RV_FLAG));
 assert(!(registers[SC27XX_PD_CFG0/4]&(SC27XX_PD_CTL_EN|SC27XX_PD_EN_SOP)));
 assert(!(registers[SC27XX_PD_CFG1/4]&(SC27XX_PD_EN_SOP_RX|SC27XX_PD_RX_AUTO_GOOD_CRC)));
 /* RX activation failures never publish an enabled receive path. */
 for(int step=0;step<9;step++){
  count=0;fault=step;pd.rx_enabled=true;
  assert(sc27xx_set_rx(&tcpc,true)==-EIO&&!pd.rx_enabled&&!pd.lock);
 }
 fault=-1;count=0;
 registers[SC27XX_PD_MESG_ID_CFG/4]=7;
 assert(!sc27xx_transmit(&tcpc,TCPC_TX_HARD_RESET,0,PD_REV30));
 assert(pd.tx_pending&&!pd.tx_sop&&!pd.lock&&!pd.rx_enabled);
 assert(!(registers[SC27XX_INT_EN/4]&SC27XX_PD_PKG_RV_FLAG));
 int hr=-1,rx=-1,tx=-1,id=-1,txid=-1;
 for(int i=0;i<count;i++){
  if(ops[i].reg==SC27XX_PD_CTRL){
   if(ops[i].mask==SC27XX_PD_RX_FLASH)rx=i;
   if(ops[i].mask==SC27XX_PD_TX_FLASH)tx=i;
   if(ops[i].mask==SC27XX_PD_RX_ID_CLR)id=i;
   if(ops[i].mask==SC27XX_PD_HARD_RESET)hr=i;
  }
  if(ops[i].reg==SC27XX_PD_MESG_ID_CFG)txid=i;
 }
 assert(rx>=0&&rx<tx&&tx<id&&id<txid&&txid<hr);
 assert(!(registers[SC27XX_PD_MESG_ID_CFG/4]&SC27XX_PD_MESS_ID_MASK));
 /* Failure in the reset sequence cannot emit a hard-reset strobe. */
 for(int step=2;step<8;step++){
  count=0;fault=step;pd.tx_pending=false;
  assert(sc27xx_transmit(&tcpc,TCPC_TX_HARD_RESET,0,PD_REV30)==-EIO&&!pd.tx_pending&&!pd.lock);
  for(int i=0;i<count;i++)assert(ops[i].reg!=SC27XX_PD_CTRL||ops[i].mask!=SC27XX_PD_HARD_RESET);
 }
 /* Ordinary SOP TX retains the receive FIFO and message-ID state. */
 count=0;fault=-1;pd.rx_enabled=true;struct pd_message msg={.header=0x1082,.payload={0x12345678}};
 assert(!sc27xx_transmit(&tcpc,TCPC_TX_SOP,&msg,PD_REV30)&&pd.tx_sop&&pd.tx_pending);
 assert(pd.rx_enabled);
 for(int i=0;i<count;i++){
  assert(ops[i].reg!=SC27XX_PD_MESG_ID_CFG);
  if(ops[i].reg==SC27XX_PD_CTRL)assert(ops[i].mask!=SC27XX_PD_RX_FLASH&&ops[i].mask!=SC27XX_PD_RX_ID_CLR);
 }
}
'''
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'test.c').write_text(code)
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(root / 'test.c'), '-o', str(root / 'test')], check=True)
    subprocess.run([str(root / 'test')], check=True)
print('PASS: producer gated before reset; RX/TX IDs reset in stock order; faults block activation/reset; normal SOP preserves RX')
