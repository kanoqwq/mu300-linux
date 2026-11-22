"""Execute the production BLTC callbacks with a private regmap; no hardware."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
SOURCE = TOP/'upstream/port/drivers/leds/leds-sc27xx-bltc.c'


def callback(text, name):
    start = re.search(r'^static [^\n]+\b'+name+r'\(', text, re.M).start()
    return text[start:text.index('\n}', start)+2]


@unittest.skipUnless(os.name == 'posix' and shutil.which('gcc'), 'native POSIX gcc required')
class BltcMode(unittest.TestCase):
    def test_steady_boot_to_breathing_full_off_and_register_failures(self):
        text = SOURCE.read_text()
        constants = '\n'.join(line for line in text.splitlines() if line.startswith('#define SC27XX_'))
        functions = '\n'.join(callback(text, name) for name in (
            'sc27xx_led_get_offset', 'sc27xx_led_enable', 'sc27xx_led_disable',
            'sc27xx_led_set', 'sc27xx_led_clamp_align_delta_t',
            'sc27xx_led_pattern_clear', 'sc27xx_led_pattern_set'))
        harness = r'''
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
typedef uint32_t u32;
typedef uint8_t u8;
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U >> (31-(h))) & (~0U << (l)))
#define clamp_t(t,v,lo,hi) ((v)<(lo)?(lo):((v)>(hi)?(hi):(v)))
enum led_brightness { LED_OFF=0, LED_FULL=255 };
struct led_classdev { unsigned brightness; };
struct led_pattern { unsigned brightness, delta_t; };
struct regmap { unsigned regs[64], calls, fail; };
struct sc27xx_led_priv { struct regmap *regmap; unsigned base; int lock; };
struct sc27xx_led { struct led_classdev ldev; struct sc27xx_led_priv *priv; u8 line; };
#define to_sc27xx_led(p) ((struct sc27xx_led *)((char *)(p)-offsetof(struct sc27xx_led,ldev)))
static void mutex_lock(int *lock) { assert(!*lock);*lock=1; }
static void mutex_unlock(int *lock) { assert(*lock);*lock=0; }
static int regmap_update_bits(struct regmap *map,unsigned address,unsigned mask,unsigned value) {
    if(++map->calls==map->fail)return -EIO;
    assert(address/4<64);
    map->regs[address/4]=(map->regs[address/4]&~mask)|(value&mask);return 0;
}
static int regmap_write(struct regmap *map,unsigned address,unsigned value) {
    return regmap_update_bits(map,address,~0U,value);
}
'''
        main = r'''
int main(void) {
    for(unsigned line=0;line<3;line++) for(unsigned peak=1;peak<=255;peak++) {
        struct regmap map={.regs={0xa5a5a5a5}};
        struct sc27xx_led_priv priv={.regmap=&map};
        struct sc27xx_led led={.priv=&priv,.line=line};
        unsigned shift=4*line,mask=3U<<shift;
        struct led_pattern pattern[]={{0,625},{peak,750},{0,625},{0,250}};
        /* A normal boot/power indication leaves RUN|TYPE set. */
        assert(sc27xx_led_set(&led.ldev,LED_FULL)==0);
        unsigned before=map.regs[0];
        assert((before&mask)==mask);
        assert(sc27xx_led_pattern_set(&led.ldev,pattern,4,-1)==0);
        assert(map.regs[0]==((before&~mask)|(1U<<shift)));
        assert(led.ldev.brightness==peak && !priv.lock);
        /* A colour or brightness refresh must retain breathing mode. */
        assert(sc27xx_led_pattern_set(&led.ldev,pattern,4,-1)==0);
        assert((map.regs[0]&mask)==(1U<<shift));
        assert(sc27xx_led_pattern_clear(&led.ldev)==0);
        assert(map.regs[0]==(before&~mask) && led.ldev.brightness==LED_OFF);
        assert(sc27xx_led_set(&led.ldev,LED_FULL)==0); /* Full uses steady. */
        assert((map.regs[0]&mask)==mask);
        assert(sc27xx_led_set(&led.ldev,LED_OFF)==0);
        assert(!(map.regs[0]&mask));
        assert(map.regs[0]==(before&~mask)); /* Other lamps remain untouched. */
    }
    for(unsigned failure=1;failure<=6;failure++) {
        struct regmap map={.regs={0xffffffff},.fail=failure};
        struct sc27xx_led_priv priv={.regmap=&map};
        struct sc27xx_led led={.priv=&priv,.ldev={.brightness=42}};
        struct led_pattern pattern[]={{0,625},{40,750},{0,625},{0,250}};
        assert(sc27xx_led_pattern_set(&led.ldev,pattern,4,-1)==-EIO);
        assert(map.regs[0]==0xffffffff && led.ldev.brightness==42 && !priv.lock);
        map.calls=0;
        assert(sc27xx_led_pattern_set(&led.ldev,pattern,3,-1)==-EINVAL);
        assert(map.calls==0 && !priv.lock);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='bltc-mode-') as temp:
            source=Path(temp)/'mode.c'; binary=Path(temp)/'mode'
            source.write_text(harness+'\n'+constants+'\n'+functions+'\n'+main)
            build=subprocess.run(['gcc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-O2',str(source),'-o',str(binary)],
                                 capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)


if __name__ == '__main__':
    unittest.main()
