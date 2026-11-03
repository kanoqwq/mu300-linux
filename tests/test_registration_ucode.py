"""Run the passive cache against real ucode; no device or AT channel is opened."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from helpers import ucode, ucode_paths

@unittest.skipUnless(ucode(), 'ucode required')
class RegistrationCache(unittest.TestCase):
    def setUp(self):
        if os.geteuid() != 0:
            self.skipTest('device cache is root-owned')
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-registration-'))
        self.root = self.tmp / 'cache'

    def tearDown(self):
        if hasattr(self, 'tmp'):
            shutil.rmtree(self.tmp)

    def run_cache(self, operations):
        source = ("import {cache} from 'mu300.registration'; "
                  "let events=[]; let c=cache(" + json.dumps(str(self.root)) +
                  ", (name,data)=>push(events,{name,data})); " + operations +
                  "; print(sprintf('%J', events));")
        p = subprocess.run(ucode() + ucode_paths()[2:] + ['-e', source], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_registration_changes_only_and_no_identity_fields(self):
        events = self.run_cache('''c.registration(0, '+CEREG: 1,"A","secret",7', false);
            c.registration(0, '+CEREG: 1,"B","other",7', false);
            c.registration(1, '+CEREG: 2,5,"A","secret",11', true);
            c.registration(0, '+CEREG: 11', false)''')
        self.assertEqual([e['data']['slot'] for e in events], [0, 1])
        self.assertEqual(json.loads((self.root/'slot0.json').read_text()), dict(slot=0, stat=1, rat=7))
        self.assertEqual(json.loads((self.root/'slot1.json').read_text()), dict(slot=1, stat=5, rat=11))
        self.assertEqual((self.root/'slot0.json').stat().st_mode & 0o777, 0o600)

    def test_nr_registration_query_and_reset(self):
        events=self.run_cache('''c.reply('AT+SPACTCARD=1;+CEREG?;+C5GREG?',
            '+CEREG: 2,1,"a","b",7\\n+C5GREG: 2,5,"a","b",11\\nOK');
            c.registration(0,'+C5GREG: 10,"a","b",11',false)''')
        self.assertEqual(len(events),3)
        self.assertEqual(json.loads((self.root/'slot1-nr.json').read_text())['stat'],5)
        self.assertEqual(json.loads((self.root/'slot1.json').read_text())['stat'],1)
        self.run_cache('c.reset(1)')
        self.assertFalse((self.root/'slot1-nr.json').exists())
        self.assertTrue((self.root/'slot0-nr.json').exists())

    def test_stock_sim_events_are_hints_and_reject_malformed(self):
        events = self.run_cache('''for (let line in ['+ECIND: 3,0,0,1', '+ECIND:3,1,34',
            '+ECIND: 3,100', '+ECIND: 3,2', '+ECIND: 3,4', '+ECIND: 3,1',
            '+ECIND: 3,1,99', '+ECIND: 3,0,', '+ECIND: 3,0,-1', '+ECIND: 3,4294967296',
            '+ECIND: 3,0,1garbage', '+ECIND: 2,0']) c.event(1,line)''')
        self.assertEqual(len(events), 5)
        self.assertTrue(all(e == dict(name='mu300.sim-change', data=dict(slot=1)) for e in events))
        self.assertFalse(self.root.exists(), 'a hint must not declare presence or absence')

    def test_power_off_is_not_absence_and_busy_retains_ready(self):
        events = self.run_cache('''c.reply('AT+SPACTCARD=0;+CPIN?', '+CME ERROR: 10');
            c.reply('AT+SPACTCARD=0;+SFUN=2', 'OK');
            c.reply('AT+SPACTCARD=0;+CPIN?', '+CPIN: READY\\nOK');
            c.reply('AT+SPACTCARD=0;+CPIN?', '+CME ERROR: 14');
            c.reply('AT+SPACTCARD=0;+CPIN?', '+CPIN: READY\\nOK')''')
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads((self.root/'sim0.json').read_text())['state'], 'ready')
        self.run_cache("c.reply('AT+SPACTCARD=0;+SFUN=3','OK'); c.reply('AT+SPACTCARD=0;+CPIN?', '+CME ERROR: 10')")
        self.assertFalse((self.root/'sim0.json').exists())
        self.assertFalse((self.root/'sim-power0').exists())

    def test_powered_absence_and_reset_are_scoped(self):
        self.run_cache('''c.reply('AT+SPACTCARD=1;+CFUN?', '+CFUN: 1\\nOK');
            c.reply('AT+SPACTCARD=1;+CPIN?', '+CME ERROR: 10');
            c.reply('AT+SPACTCARD=0;+CPIN?', '+CPIN: SIM PIN\\nOK'); c.reset(1)''')
        self.assertFalse((self.root/'sim1.json').exists())
        self.assertEqual(json.loads((self.root/'sim0.json').read_text())['state'], 'locked')

    def test_unscoped_or_incomplete_reply_does_not_change_state(self):
        events = self.run_cache('''c.reply('AT+CPIN?', '+CPIN: READY\\nOK');
            c.reply('AT+SPACTCARD=0;+CPIN?', '+CPIN: READY');
            c.reply('AT+SPACTCARD=0;+SFUN=2', 'ERROR')''')
        self.assertEqual(events, [])
        self.assertFalse(self.root.exists())
