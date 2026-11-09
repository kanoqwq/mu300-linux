"""Mainline MU300 must not spam errors or hide another valid hot sensor."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]


class ThermalLogging(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'requires host C compiler')
    def test_repeated_wcn_read_failures_keep_valid_hot_readings_without_printk(self):
        source=(TOP/'upstream/modules/mu300_thermal/mu300_thermal.c').read_text()
        start=source.index('static int mu300_virtual_temp(')
        callback=source[start:source.index('\nstatic const',start)]
        prelude=r'''
#include <errno.h>
#include <limits.h>
static int logs;
#define pr_err(...) (++logs)
#define pr_warn(...) (++logs)
#define dev_err(...) (++logs)
#define dev_warn(...) (++logs)
#define max(a,b) ((a)>(b)?(a):(b))
struct thermal_zone_device { void *priv; int value,error; };
struct mu300_virtual { unsigned count; struct thermal_zone_device *sensor[2]; };
#define thermal_zone_device_priv(tz) ((tz)->priv)
static int thermal_zone_get_temp(struct thermal_zone_device *tz,int *value) {
 if(tz->error)return tz->error;
 *value=tz->value;return 0;
}
'''
        checks=r'''
int main(void) {
 struct thermal_zone_device wcn={0,0,-EIO},soc={0,94000,0};
 struct mu300_virtual data={2,{&wcn,&soc}};
 struct thermal_zone_device zone={&data,0,0}; int value=0;
 for(int i=0;i<100;i++)if(mu300_virtual_temp(&zone,&value)||value!=94000)return 1;
 if(logs)return 2;
 soc.error=-EIO;value=123;
 if(mu300_virtual_temp(&zone,&value)!=-EIO||value!=123)return 3;
 wcn.error=0;wcn.value=65000;
 if(mu300_virtual_temp(&zone,&value)||value!=65000)return 4;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'thermal.c';binary=root/'thermal'
            path.write_text(prelude+callback+checks)
            subprocess.run(['cc','-std=c99','-Wall','-Werror',str(path),'-o',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary)],check=True)


if __name__ == '__main__': unittest.main()
