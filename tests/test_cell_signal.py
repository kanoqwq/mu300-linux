#!/usr/bin/env python3
"""Run the production signal parsers and query gate; no modem access."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'openwrt').is_dir())
APP='luci-app-t551m' if (ROOT/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
SOURCE=(ROOT/f'openwrt/{APP}/root/usr/libexec/unisoc-modem/cell').read_text()
FUNCTIONS=SOURCE[SOURCE.index('reg_fragment() {'):SOURCE.index('\nqos_fragment() {')]
GATE=SOURCE[SOURCE.index('R4=$(printf'):SOURCE.index('SP_LTE=; SP_LTEPHY=;',SOURCE.index('REGISTERED=0'))]
SHELLS=[shlex.split(s) for s in ('sh','bash','busybox sh') if shutil.which(shlex.split(s)[0])]


@unittest.skipUnless(os.name!='nt' and SHELLS, 'requires POSIX shells')
class Signal(unittest.TestCase):
    def run_code(self,code,**env):
        for shell in SHELLS:
            r=subprocess.run(shell+['-c',FUNCTIONS+'\n'+code],env=dict(os.environ,**env),capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,(shell,r.stderr))
            yield r.stdout.strip()

    def test_nr_cesq_and_unknown_values(self):
        cases=[('99,99,255,255,255,255,74,54,68',dict(rsrp=-102,rsrq=-6,sinr=11)),
               ('99,99,255,255,30,80',dict(rsrp=-60,rsrq=-4.5)),
               ('99,99,255,255,30,80,255,255,255',dict(rsrp=-60,rsrq=-4.5)),
               ('99,99,255,255,255,255,255,255,255',dict(rsrp=None,rsrq=None)),
               ('99,99,255,255,255,255,0,0,0',dict(rsrp=-156,rsrq=-43,sinr=-23)),
               ('99,99,255,255,255,255,126,126,127',dict(rsrp=-31,rsrq=20,sinr=40)),
               ('99,99,255,255,bad,-1',dict(rsrp=None,rsrq=None))]
        for raw,expected in cases:
            for text in self.run_code('cesq_fragment "$RAW"',RAW='+CESQ: '+raw):
                self.assertEqual(json.loads(text),expected)

    def test_c5greg_is_parsed_and_registration_not_forged(self):
        raw='+C5GREG: 0,8,"88B250","88A642085",11,9,01.040000'
        for text in self.run_code('reg_fragment "$RAW"',RAW=raw):
            self.assertEqual(json.loads(text),dict(stat=8,tac='88B250',cid='88A642085',act=11))
        for text in self.run_code(GATE+'\nprintf "%s:%s" "$REGISTERED" "$QUERY_SERVING"',
                                  CFUN='1',CEREG='',C5GREG=raw,MMREG='null'):
            self.assertEqual(text,'0:1')

    def test_no_service_legacy_emergency_and_radio_off_skip_queries(self):
        for cfun,reg,reg5 in [
            ('1','+CEREG: 2,2',''),('1','+CEREG: 2,8,"01","123",7',''),
            ('1','','+C5GREG: 0,8'),('0','','+C5GREG: 0,8,"01","123",11')]:
            for text in self.run_code(GATE+'\nprintf "%s:%s" "$REGISTERED" "$QUERY_SERVING"',
                                      CFUN=cfun,CEREG=reg,C5GREG=reg5,MMREG='null'):
                self.assertEqual(text,'0:0')

    def test_normal_registered_paths_are_unchanged(self):
        for stat in (1,5,10):
            for text in self.run_code(GATE+'\nprintf "%s:%s" "$REGISTERED" "$QUERY_SERVING"',
                                      CFUN='1',CEREG=f'+CEREG: 2,{stat},"01","123",7',C5GREG='',MMREG='null'):
                self.assertEqual(text,'1:1')


if __name__=='__main__': unittest.main()
