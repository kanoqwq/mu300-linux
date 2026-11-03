"""U30 Pro stock SKU routing over the shared, broker-owned card transaction."""
import json
from pathlib import Path
from helpers import TOP, ShellTest
import test_v50_sim as fixtures

LIB = TOP/'devices/u30pro/overlay/opt/mu300/lib/u30pro-card.sh'


class U30ProCards(fixtures.PhysicalCardsFixture):
    def setUp(self):
        super().setUp()
        self.args.write_bytes(b'zteboot.zte_boardid=0\0')
        self.initial.pop('v50_card')
        self.initial['u30pro_card']=0
        self.state.write_text(json.dumps(self.initial))
        self.extra.update(TEST_MODEL='u30pro',MU300_U30PRO_CARD_LIB=LIB,
                          MU300_U30PRO_RUN=self.run,MU300_U30PRO_MUX=self.hw)
        self.stub('mu300-u30pro-sim',f'exec /bin/sh "{self.controller}" "$@"')


    def test_all_stock_skus_do_not_reuse_v50_board_numbers(self):
        for shell in self.each_shell():
            for board,ids in [(0,[0,1,2,3]),(1,[0,1,2]),(2,[0,1]),(3,[0,1,3]),
                              (4,[0,1,2,3]),(5,[3]),(6,[0,2])]:
                self.args.write_bytes(f'zteboot.zte_boardid={board}\0'.encode())
                status=json.loads(self.call(shell,'status').stdout)
                self.assertEqual(status['cards'],ids)
                self.assertEqual(status['model'],'u30pro')
                for card in range(4):
                    r=self.call(shell,'validate',str(card))
                    self.assertEqual(r.returncode==0,card in ids,(board,card,r.stderr))
        self.assertFalse((self.tmp/'commands').exists())

    def test_cmdline_and_proc_precedence_and_unknown_id(self):
        for shell in self.each_shell():
            (self.root/'proc/cmdline').write_text('zteboot.zte_boardid=6 console=ttyS1')
            self.assertEqual(json.loads(self.call(shell,'status').stdout)['cards'],[0,2])
            (self.root/'proc/driver').mkdir(exist_ok=True)
            proc=self.root/'proc/driver/board_id';proc.write_text('5\n')
            self.assertEqual(json.loads(self.call(shell,'status').stdout)['cards'],[3])
            proc.write_text('99\n')
            self.assertFalse(json.loads(self.call(shell,'status').stdout)['supported'])
            self.assertNotEqual(self.call(shell,'apply','0').returncode,0)
            proc.unlink();(self.root/'proc/cmdline').unlink()
        self.assertFalse((self.tmp/'commands').exists())

    def test_physical_switch_reuses_rf_order_replay_and_rollback(self):
        for shell in self.each_shell():
            for target,attribute,stack in [(2,'chip_select',1),(3,'psim_select',0)]:
                self.state.write_text(json.dumps(self.initial))
                (self.tmp/'commands').unlink(missing_ok=True)
                for name in ('chip_select','psim_select'):(self.hw/name).write_text('0\n')
                r=self.call(shell,'apply',str(target));self.assertEqual(r.returncode,0,r.stderr)
                calls=(self.tmp/'commands').read_text()
                expected=[f'AT+SPACTCARD={stack};+SFUN=5',f'AT+SPACTCARD={stack};+SFUN=3',
                          'sleep 5',f'AT+SPACTCARD={stack};+SFUN=2',f'replay {stack} replay early',
                          f'AT+SPACTCARD={stack};+SFUN=4']
                positions=[calls.index(value) for value in expected]
                self.assertEqual(positions,sorted(positions))
                self.assertNotIn(f'AT+SPACTCARD={1-stack};+SFUN',calls)
                self.assertEqual((self.hw/attribute).read_text().strip(),'1')
                self.assertEqual(json.loads(self.state.read_text())['u30pro_card'],target)

    def test_chip_route_uses_second_logical_stack_and_keeps_saved_policy(self):
        for shell in self.each_shell():
            self.state.write_text(json.dumps(self.initial))
            (self.hw/'chip_select').write_text('0\n')
            r=self.call(shell,'apply','2');self.assertEqual(r.returncode,0,r.stderr)
            state=json.loads(self.state.read_text())
            self.assertEqual((state['data_sim'],state['u30pro_card']),(1,2))
            calls=(self.tmp/'commands').read_text()
            self.assertIn('AT+SPACTCARD=1;+SFUN=5',calls)
            self.assertNotIn('AT+SPACTCARD=2;',calls)
            self.assertFalse((self.run/'mu300/u30pro-switch-incomplete').exists())

    def test_factory_board6_boot_routes_chip3_and_never_raises_rf(self):
        for shell in self.each_shell():
            self.args.write_bytes(b'zteboot.zte_boardid=6\0')
            state=dict(self.initial);state.pop('u30pro_card')
            self.state.write_text(json.dumps(state));(self.hw/'chip_select').write_text('0\n')
            (self.tmp/'commands').unlink(missing_ok=True)
            r=self.sh(shell,'ln -s $$ "$MU300_U30PRO_RUN/mu300-radio-on.owner"\n"$MU300_BIN/mu300-u30pro-sim" boot\nrc=$?\nrm -f "$MU300_U30PRO_RUN/mu300-radio-on.owner"\nexit $rc',**self.extra)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((self.hw/'chip_select').read_text().strip(),'1')
            calls=(self.tmp/'commands').read_text()
            self.assertIn('AT+SPACTCARD=1;+SFUN=2',calls)
            self.assertNotIn('+SFUN=4',calls)
            self.assertNotIn('ifdown',calls)

    def test_matching_boot_route_adds_no_at_or_delay(self):
        for shell in self.each_shell():
            self.args.write_bytes(b'zteboot.zte_boardid=2\0')
            (self.hw/'chip_select').write_text('0\n');(self.hw/'psim_select').write_text('0\n')
            (self.tmp/'commands').unlink(missing_ok=True)
            r=self.sh(shell,'ln -s $$ "$MU300_U30PRO_RUN/mu300-radio-on.owner"\n"$MU300_BIN/mu300-u30pro-sim" boot\nrc=$?\nrm -f "$MU300_U30PRO_RUN/mu300-radio-on.owner"\nexit $rc',**self.extra)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertFalse((self.tmp/'commands').exists())


if __name__=='__main__':
    import unittest
    unittest.main(verbosity=2)
