"""Compile real charger initialization and CV encoding against register mocks.

Usage: python3 tests/bq256xx_stock_cv.py PREPARED_KERNEL
No hardware or battery calibration writes are performed.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

driver = Path(sys.argv[1])
source = (driver if driver.is_file() else driver / 'drivers/power/supply/bq256xx_charger.c').read_text()


def function(name):
    start = re.search(r'^static int ' + name + r'\(', source, re.M).start()
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


model = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <string.h>
typedef uint32_t u32;
#define clamp(v,l,h) ((v)<(l)?(l):((v)>(h)?(h):(v)))
#define min_t(t,a,b) ((t)(a)<(t)(b)?(t)(a):(t)(b))
#define dev_warn(...) ((void)0)
#define BQ256XX_NUM_WD_VAL 4
#define BQ256XX_WATCHDOG_DIS 0
#define BQ256XX_WATCHDOG_MASK 0x30
#define BQ256XX_WDT_BIT_SHIFT 4
#define BQ256XX_CHARGER_CONTROL_1 5
#define BQ256XX_EN_TERM 0x80
#define BQ2560X_CHG_EN_TIMER 8
#define BQ2560X_CHG_ICHG_MAX_uA 3000000
#define BQ2560X_CHG_ICHG_FALLBACK_uA 1980000
#define BQ256XX_ICHG_MIN_uA 0
#define BQ256XX_ICHG_STEP_uA 60000
#define BQ256XX_CHARGE_CURRENT_LIMIT 2
#define BQ256XX_ICHG_MASK 0x3f
#define BQ2560X_VBATREG_MIN_uV 3856000
#define BQ2560X_VBATREG_OFFSET_uV 3856000
#define BQ2560X_VBATREG_STEP_uV 32000
#define BQ256XX_VBATREG_MASK 0xf8
#define BQ256XX_VBATREG_BIT_SHIFT 3
#define BQ256XX_BATTERY_VOLTAGE_LIMIT 4
static const int bq256xx_watchdog_time[]={0,40,80,160};
struct bq256xx_device;
struct power_supply_battery_info {
 int constant_charge_current_max_ua,constant_charge_voltage_max_uv;
 int precharge_current_ua,charge_term_current_ua;
};
struct bq256xx_chip_info {
 int bq256xx_def_ichg,bq256xx_def_vbatreg,bq256xx_def_iprechg,bq256xx_def_iterm;
 int bq256xx_max_ichg,bq256xx_max_vbatreg;
 int (*bq256xx_set_vindpm)(struct bq256xx_device*,int);
 int (*bq256xx_set_iindpm)(struct bq256xx_device*,int);
 int (*bq256xx_set_ichg)(struct bq256xx_device*,int);
 int (*bq256xx_set_iprechg)(struct bq256xx_device*,int);
 int (*bq256xx_set_vbatreg)(struct bq256xx_device*,int);
 int (*bq256xx_set_iterm)(struct bq256xx_device*,int);
 int (*bq256xx_set_ts_ignore)(struct bq256xx_device*,int);
};
struct bq256xx_device {
 int dev,regmap,charger,watchdog_timer;bool bq2560x_chg;
 struct { int ichg_max,vbatreg_max,vindpm,iindpm,ts_ignore; } init_data;
 struct bq256xx_chip_info *chip_info;
};
static struct power_supply_battery_info battery;
static unsigned regs[12];
static int io_error,info_error,voltage_requests,info_gets,info_puts,fail_reg=-1;
static int regmap_update_bits(int map,int reg,unsigned mask,unsigned value) {
 (void)map;if(io_error)return io_error;if(reg==fail_reg)return -EIO;regs[reg]=(regs[reg]&~mask)|(value&mask);return 0;
}
static int regmap_read(int map,int reg,unsigned *value){(void)map;if(io_error)return io_error;*value=regs[reg];return 0;}
static int power_supply_get_battery_info(int chg,struct power_supply_battery_info **p) {
 (void)chg;if(info_error)return info_error;*p=&battery;info_gets++;return 0;
}
static void power_supply_put_battery_info(int chg,struct power_supply_battery_info*p){(void)chg;assert(p==&battery);info_puts++;}
static int unchanged(struct bq256xx_device*b,int val){(void)b;(void)val;return 0;}
'''
tests = r'''
static int set_voltage(struct bq256xx_device*b,int val){voltage_requests++;return bq2560x_set_chrg_volt(b,val);}
static struct bq256xx_chip_info chip = {
 .bq256xx_def_ichg=2040000,.bq256xx_def_vbatreg=4208000,.bq256xx_def_iprechg=180000,
 .bq256xx_def_iterm=180000,.bq256xx_max_ichg=3000000,.bq256xx_max_vbatreg=4624000,
 .bq256xx_set_vindpm=unchanged,.bq256xx_set_iindpm=unchanged,.bq256xx_set_ichg=bq256xx_set_ichg_curr,
 .bq256xx_set_iprechg=unchanged,.bq256xx_set_vbatreg=set_voltage,.bq256xx_set_iterm=unchanged,
};
static int actual_voltage(void){unsigned code=(regs[4]&0xf8)>>3;return code==15?4352000:code*32000+3856000;}
static void reset(void){
 memset(regs,0,sizeof(regs));regs[4]=0x5b;regs[5]=0xcf;regs[1]=0x1a;regs[0]=0x04;regs[2]=0xa1;
 battery=(struct power_supply_battery_info){3000000,4300000,180000,300000};
 io_error=info_error=voltage_requests=info_gets=info_puts=0;fail_reg=-1;
}
int main(void){
 struct bq256xx_device b={.chip_info=&chip,.bq2560x_chg=true};
 reset();assert(!bq256xx_hw_init(&b));
 assert(b.init_data.vbatreg_max==4304000&&actual_voltage()==4304000);
 assert(info_gets==1&&info_puts==1);
 assert(b.init_data.ichg_max==3000000&&regs[2]==0xb2);
 assert(regs[5]==0xc7); /* TERM on, timer off, TS bit and other bits retained. */
 assert((regs[4]&7)==3&&regs[1]==0x1a&&regs[0]==0x04);
 assert(!bq256xx_set_ichg_curr(&b,9000000)&&regs[2]==0xb2);
 reset();battery.constant_charge_current_max_ua=1500000;
 assert(!bq256xx_hw_init(&b));assert(b.init_data.ichg_max==1500000&&regs[2]==0x99);
 reset();battery.constant_charge_current_max_ua=9000000;
 assert(!bq256xx_hw_init(&b));assert(b.init_data.ichg_max==3000000&&regs[2]==0xb2);
 reset();chip.bq256xx_max_ichg=2400000;
 assert(!bq256xx_hw_init(&b));assert(b.init_data.ichg_max==2400000&&regs[2]==0xa8);
 chip.bq256xx_max_ichg=3000000;
 reset();battery.constant_charge_current_max_ua=-EINVAL;
 assert(!bq256xx_hw_init(&b));assert(b.init_data.ichg_max==1980000&&regs[2]==0xa1);
 /* Independent oracle: stock sqc_mp_set_cv takes mV and writes this
  * register code. Check the full stock-supported mV range, including exact
  * steps, chip saturation and 0xf's special value. Generic setters still floor.
  */
 for(int mv=3800;mv<=4700;mv++) {
  reset();battery.constant_charge_voltage_max_uv=mv*1000;
  assert(!bq256xx_hw_init(&b));
  unsigned code=mv<3856?0:mv>=4624?24:(mv-3856)/32+1;
  assert((regs[4]&0xf8)==(code<<3));
  assert((regs[4]&7)==3);
  assert(bq2560x_get_chrg_volt(&b)==actual_voltage());
 }
 /* A lower-voltage cell is not raised to this board's 4.3 V setting. */
 reset();battery.constant_charge_voltage_max_uv=4100000;
 assert(!bq256xx_hw_init(&b));assert(actual_voltage()==4112000);
 reset();battery.constant_charge_voltage_max_uv=9000000;
 assert(!bq256xx_hw_init(&b));assert(actual_voltage()==4624000);
 reset();battery.constant_charge_voltage_max_uv=-EINVAL;
 assert(!bq256xx_hw_init(&b));assert(actual_voltage()==4208000);
 /* Missing battery information keeps the default rather than chip maximum. */
 reset();info_error=-ENODEV;assert(!bq256xx_hw_init(&b));assert(actual_voltage()==4208000);
 assert(info_gets==0&&info_puts==0);
 assert(b.init_data.ichg_max==1980000&&regs[2]==0xa1);
 reset();info_error=-ENOMEM;assert(bq256xx_hw_init(&b)==-ENOMEM&&!voltage_requests);
 assert(!info_gets&&!info_puts);
 /* Success allocates battery metadata; later write failures must release it. */
 reset();fail_reg=4;assert(bq256xx_hw_init(&b)==-EIO);
 assert(info_gets==1&&info_puts==1);
 reset();io_error=-EIO;assert(bq256xx_hw_init(&b)==-EIO&&!voltage_requests);
 /* Generic bq users do not inherit the U30 policy or battery CV request. */
 reset();b.bq2560x_chg=false;assert(!bq256xx_hw_init(&b));
 assert(actual_voltage()==4208000&&regs[5]==0xcf&&b.init_data.ichg_max==3000000);
 assert(regs[2]==0xa2);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='bq-stock-cv-') as directory:
    root = Path(directory)
    code = model + function('bq256xx_set_ichg_curr') + function('bq2560x_get_chrg_volt') + function('bq2560x_set_chrg_volt') + function('bq2560x_stock_cv') + function('bq256xx_hw_init') + tests
    (root / 'test.c').write_text(code)
    subprocess.run(['cc','-Wall','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('PASS: stock CV, register quantization/preservation, timer policy, lower-voltage battery, missing info, I/O errors and generic-board isolation')
