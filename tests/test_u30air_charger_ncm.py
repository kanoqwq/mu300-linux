"""kanoqwq's charger and NCM kernel patches (upstream/patches/0024-0026; FINDINGS 207, 208): the U30 Air's battery
CV and stock safety-timer policy on, its 3 A and the NCM busy-flush retry off unless asked for."""
import re
import unittest
from pathlib import Path

PATCHES = Path(__file__).resolve().parents[1] / 'upstream/patches'


def patch(prefix):
    found = sorted(PATCHES.glob(prefix + '-*.patch'))
    assert len(found) == 1, found
    return found[0].read_text()


def added(text):
    return '\n'.join(l[1:] for l in text.splitlines() if l.startswith('+') and not l.startswith('+++'))


def after(text):
    """the hunks as they read once applied: context and added lines"""
    return '\n'.join(l[1:] for l in text.splitlines()
                     if (l.startswith('+') and not l.startswith('+++')) or l.startswith(' '))


def removed(text):
    return '\n'.join(l[1:] for l in text.splitlines() if l.startswith('-') and not l.startswith('---'))


class ChargerCV(unittest.TestCase):
    def setUp(self):
        self.text = patch('0024')

    def test_kano_is_the_author(self):
        self.assertTrue(self.text.startswith('From: kanoqwq <kanoqwq@qq.com>\n'))
        self.assertIn('c031a58a', self.text)

    def test_the_battery_cv_bounded_by_the_chip_and_only_on_the_vendor_node(self):
        self.assertIn('BQ2560X_CHG_VBATREG_MAX_uV', removed(self.text))          # the 4.208 V snapshot cap goes
        plus = added(self.text)
        self.assertNotIn('4208000', plus)
        self.assertIn('min_t(u32, bat_info->constant_charge_voltage_max_uv,\n'
                      '\t\t\t      bq->chip_info->bq256xx_max_vbatreg)', plus)
        self.assertIn('bq->chip_info->bq256xx_def_vbatreg;', plus)                 # no valid node: the chip default
        # what probe writes: the vendor node its bounded CV, every other board the chip default as before
        self.assertIn('bq->bq2560x_chg ? bq->init_data.vbatreg_max :\n\t\t\tbq->chip_info->bq256xx_def_vbatreg);',
                      plus)

    def test_the_safety_timer_off_and_termination_kept(self):
        plus = added(self.text)
        self.assertIn('#define BQ2560X_CHG_EN_TIMER\t\tBIT(3)', plus)
        # mask both bits, set only EN_TERM: the timer bit cleared, termination on, nothing else touched
        self.assertIn('BQ256XX_EN_TERM | BQ2560X_CHG_EN_TIMER,\n\t\t\t\t\t BQ256XX_EN_TERM);', after(self.text))

    def test_the_current_budget_is_not_this_patch(self):
        self.assertNotIn('ICHG', added(self.text).replace('ICHG_MAX_uA', ''))


class Charger3A(unittest.TestCase):
    def setUp(self):
        self.text = patch('0025')
        self.plus = added(self.text)

    def test_off_by_default(self):
        self.assertIn('\nstatic bool u30air_3a;\n', self.plus)
        self.assertIn('module_param(u30air_3a, bool, 0444);', self.plus)

    def test_every_change_behind_the_switch(self):
        self.assertIn('return u30air_3a ? 3000000 : 2000000;', self.plus)          # a 3 A Rp: 2 A as before
        self.assertIn('return 2000000;', removed(self.text))
        self.assertIn('bq->bq2560x_chg && u30air_3a ? bq->init_data.ichg_max :\n'
                      '\t\t\t\tbq->chip_info->bq256xx_def_ichg);', after(self.text))       # the initial ICHG
        m = re.search(r'if \(u30air_3a\)\n(.*?)\n\t\telse\n\t\t\tbq->init_data.ichg_max = '
                      r'min_t\(u32, bq->init_data.ichg_max, BQ2560X_CHG_ICHG_MAX_uA\);', self.plus, re.S)
        self.assertTrue(m, 'the old 1.98 A cap stays the default')
        # on: the battery node's current, at most 3 A and the chip's; 1.98 A without a valid node
        self.assertIn('BQ2560X_CHG_ICHG_MAX_uA :', m.group(1))
        self.assertIn('min_t(u32, BQ2560X_CHG_ICHG_3A_uA, bq->chip_info->bq256xx_max_ichg)', m.group(1))
        self.assertIn('#define BQ2560X_CHG_ICHG_3A_uA\t\t3000000', self.plus)

    def test_nothing_else_is_raised(self):
        # the 1.98 A default is not edited; only the lines above change
        self.assertNotIn('BQ2560X_CHG_ICHG_MAX_uA\t\t', removed(self.text))


class NcmRetry(unittest.TestCase):
    def setUp(self):
        self.text = patch('0026')
        self.plus = added(self.text)

    def test_off_by_default_and_switchable(self):
        self.assertIn('\nstatic bool tx_busy_retry;\n', self.plus)
        self.assertIn('module_param(tx_busy_retry, bool, 0644);', self.plus)

    def test_retry_only_when_busy_and_asked_for_with_the_deadline_moved_first(self):
        body = self.plus[self.plus.index('/* A timer flush has no qdisc'):]
        self.assertIn('== NETDEV_TX_BUSY &&\n\t\t    READ_ONCE(tx_busy_retry)) {', body)
        self.assertLess(body.index('hrtimer_forward_now(data, TX_TIMEOUT_NSECS);'), body.index('return HRTIMER_RESTART;'))
        self.assertIn('return HRTIMER_NORESTART;', self.text)                     # every other path as before

    def test_the_source(self):
        self.assertIn('20260817192638.437798-1-cosmin-gabriel.tanislav.xa@renesas.com', self.text)


if __name__ == '__main__':
    unittest.main()
