"""Physical SIM changes must confirm RF activation, not just an AT OK."""
import json
import unittest
import test_v50_sim as v50


class PhysicalSimRf(unittest.TestCase):
    def fixtures(self):
        return [(v50.V50Cards, "v50", 3, "psim_select", 0)]

    def exercise(self, check, **env):
        for cls, model, target, selector, initial_selector in self.fixtures():
            f = cls()
            f.setUp()
            try:
                for shell in f.shells:
                    with self.subTest(model=model, shell=shell):
                        f.state.write_text(json.dumps(f.initial))
                        (f.hw/selector).write_text(str(initial_selector))
                        (f.tmp/'commands').unlink(missing_ok=True)
                        (f.tmp/'rf-stuck-once').unlink(missing_ok=True)
                        reg = f.run/'mu300-registration';reg.mkdir(exist_ok=True)
                        for slot in (0, 1):
                            for suffix in ('', '-nr'):
                                (reg/f'slot{slot}{suffix}.json').write_text('old-card')
                        result = f.call(shell, 'apply', str(target), **env)
                        check(f, model, target, selector, initial_selector, result)
                        stack = 1 if model == 'f50pro' else 0
                        self.assertFalse((reg/f'slot{stack}-nr.json').exists())
                        self.assertEqual((reg/f'slot{1-stack}-nr.json').read_text(), 'old-card')
            finally:
                f.tearDown()

    def test_delayed_rf_is_confirmed_before_logical_selection_in_both_directions(self):
        def check(f, model, target, selector, old, result):
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (f.tmp/'commands').read_text()
            on = calls.index('+SFUN=4')
            self.assertGreaterEqual(calls[on:].count('+CFUN?'), 3)
            self.assertLess(calls.index('+CFUN?', on), calls.index('logical'))
            stack = 1 if model == 'f50pro' else 0
            self.assertEqual(json.loads(f.state.read_text())['cfun'][stack], 1)
            previous = 1 if model == 'f50pro' else 0
            result = f.call(f.shells[0], 'apply', str(previous), RF_DELAY='2')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(f.state.read_text())['cfun'][stack], 1)
            self.assertEqual((f.hw/selector).read_text().strip(), str(old))
        self.exercise(check, RF_DELAY='2')

    def test_ok_without_rf_activation_rolls_back_and_does_not_save_new_source(self):
        def check(f, model, target, selector, old, result):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('sim_switch_failed_restored', result.stderr)
            state = json.loads(f.state.read_text())
            self.assertEqual(state[model+'_card'], f.initial[model+'_card'])
            self.assertEqual((f.hw/selector).read_text().strip(), str(old))
            stack = 1 if model == 'f50pro' else 0
            self.assertEqual(state['cfun'][stack], 1)
            self.assertEqual(state['radio'], f.initial['radio'])
            self.assertTrue(state['up'])
            self.assertFalse((f.run/'mu300'/f'{model}-switch-incomplete').exists())
        self.exercise(check, RF_STUCK_ONCE='1')

    def test_disabled_peer_route_never_enables_rf_or_enters_activation_wait(self):
        for cls, model, _, _, _ in self.fixtures():
            if model == 'u30pro':
                continue  # No pair editor on MU5358; boot path has separate coverage.
            f = cls()
            f.setUp()
            try:
                for shell in f.shells:
                    with self.subTest(model=model, shell=shell):
                        f.state.write_text(json.dumps(dict(f.initial, radio=[1, 0], cfun=[1, 0])))
                        (f.hw/'chip_select').write_text('1' if model == 'f50pro' else '0')
                        (f.tmp/'commands').unlink(missing_ok=True)
                        target = 4 if model == 'f50pro' else 2
                        result = f.sh(shell, f'"$MU300_BIN/mobile-data" radio-locked /bin/sh "{f.controller}" --pair-route {target}', **f.extra)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        calls = (f.tmp/'commands').read_text()
                        self.assertIn('AT+SPACTCARD=1;+SFUN=2', calls)
                        self.assertNotIn('+SFUN=4', calls)
                        self.assertEqual(calls.count('+CFUN?'), 1)  # Only the original pre-switch query.
                        state = json.loads(f.state.read_text())
                        self.assertEqual(state['cfun'], [1, 0])
                        self.assertEqual(state['radio'], [1, 0])
                        self.assertNotIn('logical', calls)
            finally:
                f.tearDown()
