"""Execute the prepared driver's charge-switch case; private state, no hardware."""
from pathlib import Path
import subprocess
import sys
import tempfile

kernel = Path(sys.argv[1])
source = (kernel/'drivers/power/supply/bq256xx_charger.c').read_text()
start = source.index('static int bq256xx_set_charger_property(')
start = source.index('\tcase POWER_SUPPLY_PROP_CHARGE_TYPE:', start)
case = source[start:source.index('\n\tdefault:', start)]

stub = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#define WRITE_ONCE(x,v) ((x)=(v))
enum { POWER_SUPPLY_PROP_CHARGE_TYPE=1, POWER_SUPPLY_CHARGE_TYPE_NONE=0,
       POWER_SUPPLY_CHARGE_TYPE_TRICKLE=1, POWER_SUPPLY_CHARGE_TYPE_FAST=2 };
struct bq256xx_device;
struct chip_info { int (*bq256xx_set_charge_type)(struct bq256xx_device *,int); };
struct bq256xx_device { struct chip_info *chip_info; bool chg_off; };
struct power_supply { struct bq256xx_device *data; };
union power_supply_propval { int intval; };
static int error, notifications, writes, requested;
static int set_type(struct bq256xx_device *bq,int value) {
    (void)bq;writes++;requested=value;return error;
}
static void __attribute__((unused)) power_supply_changed(struct power_supply *psy) {
    assert(psy->data->chg_off==(requested==POWER_SUPPLY_CHARGE_TYPE_NONE));
    notifications++;
}
static int apply(struct power_supply *psy,int value) {
    struct bq256xx_device *bq=psy->data;
    union power_supply_propval input={.intval=value},*val=&input;
    int ret=-EINVAL;
    switch (POWER_SUPPLY_PROP_CHARGE_TYPE) {
'''
tail = r'''
    }
    return ret;
}
int main(void) {
    struct chip_info info={.bq256xx_set_charge_type=set_type};
    struct bq256xx_device bq={.chip_info=&info};
    struct power_supply psy={.data=&bq};
    for(int round=0;round<100;round++) {
        for(int type=0;type<3;type++) {
            int before=notifications;
            error=0;assert(apply(&psy,type)==0);
            assert(notifications==before+1);
            bool saved=bq.chg_off;
            error=-EIO;assert(apply(&psy,!type)==-EIO);
            assert(notifications==before+1 && bq.chg_off==saved);
        }
    }
    assert(writes==600 && notifications==300);
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='bq-charge-events-') as directory:
    root=Path(directory);c=root/'events.c';binary=root/'events'
    c.write_text(stub+case+tail)
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',str(c),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
print('PASS actual BQ charge switch: pause/resume/trickle notifications, updated ownership before event, failed writes and 100 rounds; no hardware')
