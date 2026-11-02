"""Exercise the production screen payload and native feed decoder, offline."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
ROOT=Path('/home/kano/openwrt-build/package-work/u30pro-release/root')
NATIVE=TOP/'devices/u30pro/native/tools'


@unittest.skipUnless(os.name=='posix','native/target test runner')
class Bandwidth(unittest.TestCase):
    def test_native_decoder_preserves_ca_and_clears_unknown_bandwidth(self):
        source=r'''#define _GNU_SOURCE
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#include "ui/data.h"
int main(void) {
 char ca[]="20+20 MHz", empty[]="", nr[]="100", zero[]="0", unknown[]="-1", legacy[]="5";
 feed_set("lte_bw",ca); feed_set("nr_bw",nr);
 assert(!strcmp(D.lte_bw,"20+20 MHz") && D.nr_bw==100);
 feed_set("lte_bw",empty); feed_set("nr_bw",zero); feed_set("bw_idx",unknown);
 assert(!D.lte_bw[0] && D.nr_bw==0 && D.bw_idx==-1);
 feed_set("bw_idx",legacy); assert(D.bw_idx==5);
 char long_text[1024]; memset(long_text,'X',sizeof long_text-1);long_text[sizeof long_text-1]=0;
 feed_set("lte_bw",long_text); assert(strlen(D.lte_bw)==sizeof(D.lte_bw)-1);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);(base/'test.c').write_text(source)
            subprocess.run(['gcc','-std=gnu11','-O2','-Wno-unused-function','-I',str(NATIVE),str(base/'test.c'),'-lm','-o',str(base/'test')],check=True,capture_output=True)
            subprocess.run([str(base/'test')],check=True,timeout=5)

    @unittest.skipUnless(ROOT.exists(),'prepared target root')
    def test_actual_payload_lte_nr_ca_missing_and_rat_switch(self):
        source=(TOP/'devices/u30pro/overlay/usr/sbin/u30pro-ui-feed').read_text()
        body=source[source.index('    let data={'):source.index('    for(let i=0;i<min(6,length(view.wifi.clients')]
        program='''const cases=[
 {lte:{bw:'1.4M'},nr:{},want:'1.4 MHz',nr_bw:0},
 {lte:{bw:'20M'},nr:{},want:'20 MHz',nr_bw:0},
 {lte:{bw:'20M+20M'},nr:{},want:'20+20 MHz',nr_bw:0},
 {lte:{bw:'10M+20M'},nr:{band:78,bw_mhz:100},want:'10+20 MHz',nr_bw:100},
 {lte:{},nr:{band:28,bw_mhz:20},want:'',nr_bw:20},
 {lte:{},nr:{band:28,bw_mhz:null},want:'',nr_bw:0},
 {lte:{},nr:{band:28,bw_mhz:0},want:'',nr_bw:0},
 {lte:{},nr:{},want:'',nr_bw:0}
];
const fs={readfile:()=>''}, config={get:()=>''},carrier_name=x=>x;
for(let c in cases){
 let lte=c.lte,nr=c.nr,relay={},cell={},slot='0';
 let view={wan:{},wifi:{},net:{},kernel:'test'};
 BODY
 if(data.lte_bw!==c.want || data.nr_bw!==c.nr_bw || data.bw_idx!==-1)die(sprintf('%J',data));
}
print('PASS 8 bandwidth payloads: fractional LTE, CA, NR, NSA, unknown and RAT change\\n');
'''.replace('BODY',body)
        with tempfile.TemporaryDirectory(prefix='screen-bw-',dir=ROOT/'tmp') as directory:
            base=Path(directory);(base/'test.uc').write_text(program)
            result=subprocess.run(['docker','run','--rm','--platform','linux/arm64','-v',str(ROOT)+':/target','--entrypoint','/bin/sh','mu300-openwrt-base:25.12.5','-ec','chroot /target /usr/bin/ucode /tmp/'+base.name+'/test.uc'],text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS 8 bandwidth',result.stdout)


if __name__=='__main__':unittest.main(verbosity=2)
