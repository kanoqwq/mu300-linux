"""The U30 Air's USB-C port controller (upstream/patches/0019, 0020; FINDINGS 150): off unless asked for, never on
another board, and its callbacks run against a fake PMIC: roles, the boost, the input current, the PD FIFOs."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'upstream/patches'
CHARGER = PATCHES / '0019-power-bq256xx-U30-Air-boost-as-otg-vbus-and-PD-input-current.patch'
TCPC = PATCHES / '0020-usb-typec-tcpm-UMP9620-port-controller-ZTE-U30-Air.patch'


def new_file(patch, name):
    """the text of a file the patch creates"""
    text = patch.read_text()
    start = text.index(f'+++ b/{name}\n')
    body = text[text.index('\n@@', start) + 1:]
    body = body[body.index('\n') + 1:]
    lines = []
    for line in body.splitlines():
        if line.startswith('diff ') or line.startswith('--- '):
            break
        lines.append(line[1:])
    return '\n'.join(lines) + '\n'


def function(text, name):
    start = re.search(r'^static [^\n]*\b' + name + r'\(', text, re.M).start()
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def block(text, head):
    start = text.index(head)
    return text[start:text.index('\n};\n', start) + 4]


class Static(unittest.TestCase):
    def setUp(self):
        self.drv = new_file(TCPC, 'drivers/usb/typec/tcpm/ump9620-tcpc.c')

    def test_patch_numbers_are_unique(self):
        nums = [p.name[:4] for p in PATCHES.glob('*.patch')] + [p.name[:4] for p in PATCHES.glob('*/*.patch')]
        self.assertEqual(len(nums), len(set(nums)), sorted(nums))

    def test_off_unless_enabled_and_only_on_the_u30_air(self):
        probe = function(self.drv, 'ump9620_probe')
        self.assertRegex(self.drv, r'\nstatic bool enable;\n')            # default off
        self.assertIn('module_param(enable, bool, 0644);', self.drv)
        # the first thing probe looks at is the switch, then the U30 Air's device tree
        self.assertLess(probe.index('if (!enable)'), probe.index('/charger_policy_service'))
        self.assertLess(probe.index('/charger_policy_service'), probe.index('use_pdhub_c2c'))
        self.assertLess(probe.index('use_pdhub_c2c'), probe.index('devm_kzalloc'))
        self.assertIn('{ .compatible = "sprd,ump9620-pd" }', self.drv)
        cfg = (ROOT / 'upstream/mu300-mainline.config').read_text()
        for opt in ('TYPEC', 'TYPEC_TCPM', 'TYPEC_UMP9620'):
            self.assertIn(f'\nCONFIG_{opt}=m\n', cfg)
        self.assertIn('blacklist ump9620_tcpc', (ROOT / 'rootfs/overlay/etc/modprobe.d/mu300-usb-pd.conf').read_text())

    def test_no_vconn_no_dummy_regulator_no_clock_off(self):
        self.assertIn('return on ? -EOPNOTSUPP : 0;', function(self.drv, 'ump9620_set_vconn'))
        self.assertIn('devm_regulator_get_optional(dev, "vbus")', self.drv)
        self.assertNotIn('devm_regulator_get(dev', self.drv)
        self.assertNotIn('TYPEC_PD_EN, 0)', self.drv)
        stop = function(self.drv, 'ump9620_stop')
        for what in ('regulator_disable', 'USB_ROLE_DEVICE', 'saved_mode', 'saved_en', 'tcpm_unregister_port'):
            self.assertIn(what, stop)
        # TCPM goes before anything it could still call into is put back
        self.assertLess(stop.index('tcpm_unregister_port'), stop.index('saved_mode'))

    def test_the_charger_refuses_the_boost_against_an_outside_supply(self):
        text = CHARGER.read_text()
        enable = text[text.index('static int bq2560x_otg_enable'):text.index('static int bq2560x_otg_disable')]
        self.assertIn('BQ256XX_PG_STAT_MASK', enable)
        self.assertIn('-EBUSY', enable)
        self.assertLess(enable.index('regmap_read_poll_timeout'), enable.index('BQ256XX_OTG_CONFIG |'))
        reg = text[text.index('static int bq2560x_otg_register'):]
        self.assertLess(reg.index('BQ256XX_OTG_CONFIG, 0)'), reg.index('devm_regulator_register'))


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32; typedef uint16_t u16; typedef uint8_t u8;
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((~0U << (l)) & (~0U >> (31 - (h))))
#define FIELD_GET(m, v) (((v) & (m)) >> __builtin_ctz(m))
#define FIELD_PREP(m, v) (((v) << __builtin_ctz(m)) & (m))
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#include <stddef.h>
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define cpu_to_le16(v) (v)
#define cpu_to_le32(v) (v)
#define le16_to_cpu(v) (v)
#define le32_to_cpu(v) (v)
#define pd_header_cnt_le(h) (((h) >> 12) & 7)
#define pd_header_type_le(h) ((h) & 31)
#define PD_CTRL_GOOD_CRC 1
#define PD_DATA_SOURCE_CAP 1
#define PD_REV30 2
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)
#define usb_role_string(r) ""
#define IRQ_HANDLED 1
#define IRQ_NONE 0
typedef int irqreturn_t;
enum usb_role { USB_ROLE_NONE, USB_ROLE_HOST, USB_ROLE_DEVICE };
enum typec_role { TYPEC_SINK, TYPEC_SOURCE };
enum typec_data_role { TYPEC_DEVICE, TYPEC_HOST };
enum typec_port_type { TYPEC_PORT_SRC, TYPEC_PORT_SNK, TYPEC_PORT_DRP };
enum typec_cc_status { TYPEC_CC_OPEN, TYPEC_CC_RA, TYPEC_CC_RD, TYPEC_CC_RP_DEF, TYPEC_CC_RP_1_5, TYPEC_CC_RP_3_0 };
enum tcpm_transmit_status { TCPC_TX_SUCCESS, TCPC_TX_DISCARDED, TCPC_TX_FAILED };
enum tcpm_transmit_type { TCPC_TX_SOP, TCPC_TX_HARD_RESET = 5 };
enum { POWER_SUPPLY_USB_TYPE_UNKNOWN, POWER_SUPPLY_USB_TYPE_PD = 7 };
enum { POWER_SUPPLY_PROP_USB_TYPE = 1, POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT };
union power_supply_propval { int intval; };
struct pd_message { u16 header; u32 payload[7]; };
struct tcpc_dev { int x; };
struct mutex { int held; };
struct device { int x; };
struct regmap { int x; };
struct delayed_work { int x; };
struct notifier_block { int x; };
struct fwnode_handle; struct tcpm_port { int x; }; struct usb_role_switch { int x; };
struct regulator { int on; }; struct power_supply { int x; }; struct extcon_dev { int x; };

static u32 regs[0x4000];		/* the PMIC: typec at 0x380, pd at 0xe00 */
static u32 fifo[32]; static int fifo_n, fifo_at, reads_fail;
static int strobes, receives, completes[3], hard_resets, held, role_sets, psy_type = -1, psy_iin = -1;
static int reg_enables, reg_disables, enable_error;
static enum usb_role dwc3 = USB_ROLE_DEVICE;
static struct pd_message got;
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; held = 0; }
static int regmap_read(struct regmap *m, u32 r, u32 *v)
{
	(void)m;
	if (r == 0xe00 + 0x04) { if (reads_fail) return -EIO; *v = fifo_at < fifo_n ? fifo[fifo_at++] : 0; return 0; }
	if (r == 0xe00 + 0x20) { *v = fifo_n - fifo_at; return 0; }
	*v = regs[r]; return 0;
}
static int regmap_write(struct regmap *m, u32 r, u32 v) { (void)m; regs[r] = v; return 0; }
static int regmap_update_bits(struct regmap *m, u32 r, u32 mask, u32 v) { (void)m; regs[r] = (regs[r] & ~mask) | (v & mask); return 0; }
static int regmap_write_bits(struct regmap *m, u32 r, u32 mask, u32 v)
{
	(void)m;
	if (r == 0xe00 + 0x0c) {	/* PD_CTRL strobes */
		strobes |= v & mask;
		if (v & mask & BIT(4)) fifo_n = fifo_at = 0;
		return 0;
	}
	regs[r] = (regs[r] & ~mask) | (v & mask); return 0;
}
static void tcpm_pd_receive(struct tcpm_port *p, const struct pd_message *msg, enum tcpm_transmit_type t)
{ (void)p; assert(t == TCPC_TX_SOP); got = *msg; receives++; }
static void tcpm_pd_transmit_complete(struct tcpm_port *p, enum tcpm_transmit_status s) { (void)p; completes[s]++; }
static void tcpm_pd_hard_reset(struct tcpm_port *p) { (void)p; hard_resets++; }
static enum usb_role usb_role_switch_get_role(struct usb_role_switch *s) { (void)s; return dwc3; }
static int usb_role_switch_set_role(struct usb_role_switch *s, enum usb_role r) { (void)s; dwc3 = r; role_sets++; return 0; }
static int regulator_enable(struct regulator *r) { if (enable_error) return enable_error; r->on = 1; reg_enables++; return 0; }
static int regulator_disable(struct regulator *r) { r->on = 0; reg_disables++; return 0; }
static int power_supply_set_property(struct power_supply *p, int prop, const union power_supply_propval *v)
{
	(void)p;
	if (prop == POWER_SUPPLY_PROP_USB_TYPE) psy_type = v->intval; else psy_iin = v->intval;
	return 0;
}
'''

MAIN = r'''
int main(void)
{
	static struct ump9620_tcpc u; struct regulator vbus = { 0 }; struct tcpm_port port;
	enum typec_cc_status cc1, cc2;
	u.base = 0xe00; u.typec = 0x380; u.vbus = &vbus; u.port = &port;
	u.regmap = (struct regmap *)&regs;

	/* a dock that charges us and has us as its host: data and power roles apart, the boost untouched */
	assert(!ump9620_set_roles(&u.tcpc, true, TYPEC_SINK, TYPEC_HOST));
	assert(dwc3 == USB_ROLE_HOST && !(regs[0xe00 + 0x10] & BIT(9)) && !vbus.on && !held);
	assert(!ump9620_set_roles(&u.tcpc, true, TYPEC_SOURCE, TYPEC_HOST));
	assert(regs[0xe00 + 0x10] & BIT(9));
	/* detached: device, the default */
	assert(!ump9620_set_roles(&u.tcpc, false, TYPEC_SINK, TYPEC_HOST));
	assert(dwc3 == USB_ROLE_DEVICE);
	/* the role dwc3 has counts, not a copy: mu300-usb may have written host itself */
	dwc3 = USB_ROLE_HOST; role_sets = 0;
	assert(!ump9620_set_roles(&u.tcpc, true, TYPEC_SINK, TYPEC_DEVICE));
	assert(dwc3 == USB_ROLE_DEVICE && role_sets == 1);

	/* the boost once per change; an error leaves it off and is passed on */
	assert(!ump9620_set_vbus(&u.tcpc, true, false) && vbus.on && reg_enables == 1);
	assert(!ump9620_set_vbus(&u.tcpc, true, false) && reg_enables == 1);
	assert(!ump9620_set_vbus(&u.tcpc, false, true) && !vbus.on && reg_disables == 1);
	enable_error = -EBUSY;
	assert(ump9620_set_vbus(&u.tcpc, true, false) == -EBUSY && !vbus.on && !u.vbus_on);
	enable_error = 0;

	/* CC: the pin the Type-C block chose; Rp as the source shows it; a cable's Ra is not reported */
	regs[0x380 + 0x1c] = 2 | BIT(5) | (2 << 6);
	assert(!ump9620_get_cc(&u.tcpc, &cc1, &cc2) && cc1 == TYPEC_CC_RP_3_0 && cc2 == TYPEC_CC_OPEN);
	regs[0x380 + 0x1c] = 6;
	assert(!ump9620_get_cc(&u.tcpc, &cc1, &cc2) && cc1 == TYPEC_CC_OPEN && cc2 == TYPEC_CC_RD);
	regs[0x380 + 0x1c] = 1;
	assert(!ump9620_get_cc(&u.tcpc, &cc1, &cc2) && cc1 == TYPEC_CC_OPEN && cc2 == TYPEC_CC_OPEN);

	/* the input current: without a contract the charger's own detection stays */
	assert(!ump9620_set_current_limit(&u.tcpc, 3000, 5000) && psy_type == POWER_SUPPLY_USB_TYPE_UNKNOWN && psy_iin == -1);
	/* Source_Capabilities as a sink: header 0x1161 (one object), its two halves, the CRC behind */
	u.power_role = TYPEC_SINK;
	regs[0xe00 + 0x28] = BIT(4);
	fifo[0] = 0x1161; fifo[1] = 0x912c; fifo[2] = 0x0801; fifo[3] = 0xc0d8; fifo[4] = 0xb104; fifo_n = 5; fifo_at = 0;
	assert(ump9620_pd_irq(0, &u) == IRQ_HANDLED && !held);
	assert(receives == 1 && got.header == 0x1161 && got.payload[0] == 0x0801912c && fifo_n == 0);
	assert(u.source_caps);
	assert(!ump9620_set_current_limit(&u.tcpc, 3000, 5000) && psy_type == POWER_SUPPLY_USB_TYPE_PD && psy_iin == 2000000);
	assert(ump9620_set_current_limit(&u.tcpc, 1500, 9000) == -EINVAL);

	/* one transmission completes once: TX_OK and the partner's GoodCRC both say so */
	struct pd_message req = { .header = 0x1082, .payload = { 0x12345678 } };
	assert(!ump9620_pd_transmit(&u.tcpc, TCPC_TX_SOP, &req, 2) && u.tx_pending && !held);
	assert(regs[0xe00 + 0x08] == 0x1082 && (strobes & BIT(0)));
	regs[0xe00 + 0x28] = BIT(4) | BIT(5);
	fifo[0] = PD_CTRL_GOOD_CRC; fifo_n = 1; fifo_at = 0;
	ump9620_pd_irq(0, &u);
	assert(completes[TCPC_TX_SUCCESS] == 1 && receives == 1 && (regs[0x380 + 0x08] & BIT(6)));
	/* a failure after nothing was sent completes nothing */
	regs[0xe00 + 0x28] = BIT(6);
	ump9620_pd_irq(0, &u);
	assert(completes[TCPC_TX_FAILED] == 0);
	/* a read error: nothing passed on, the FIFO emptied, the lock released */
	regs[0xe00 + 0x28] = BIT(4); reads_fail = 1; fifo_n = 3; fifo_at = 0; strobes = 0;
	ump9620_pd_irq(0, &u);
	assert(receives == 1 && (strobes & BIT(4)) && !held);
	reads_fail = 0;

	/* a hard reset: FIFOs and both message IDs back to zero, the contract gone, TCPM told */
	regs[0xe00 + 0x18] = 5; strobes = 0;
	regs[0xe00 + 0x28] = BIT(0);
	ump9620_pd_irq(0, &u);
	assert(hard_resets == 1 && !u.source_caps && regs[0xe00 + 0x18] == 0);
	assert((strobes & (BIT(3) | BIT(4) | BIT(8))) == (BIT(3) | BIT(4) | BIT(8)));
	/* a new PD session clears both IDs as well; switching receive off does not touch the TX ID */
	regs[0xe00 + 0x18] = 3;
	assert(!ump9620_set_pd_rx(&u.tcpc, false) && regs[0xe00 + 0x18] == 3 && !(regs[0xe00 + 0x10] & BIT(10)));
	assert(!ump9620_set_pd_rx(&u.tcpc, true) && regs[0xe00 + 0x18] == 0 && (regs[0xe00 + 0x10] & BIT(10)));

	/* VCONN never */
	assert(ump9620_set_vconn(&u.tcpc, true) == -EOPNOTSUPP && !ump9620_set_vconn(&u.tcpc, false));
	puts("ok");
	return 0;
}
'''


@unittest.skipUnless(shutil.which('cc'), 'needs a C compiler')
class Callbacks(unittest.TestCase):
    def test_callbacks_against_a_fake_pmic(self):
        drv = new_file(TCPC, 'drivers/usb/typec/tcpm/ump9620-tcpc.c')
        defines = ''.join(l + '\n' for l in drv.splitlines() if l.startswith('#define') and '\\' not in l)
        parts = [HARNESS, defines, block(drv, 'enum ump9620_typec_state {'), block(drv, 'struct ump9620_tcpc {')]
        for name in ('to_ump', 'pd_update', 'pd_strobe', 'typec_update', 'ump9620_pd_reset', 'ump9620_get_cc',
                     'ump9620_set_vconn', 'ump9620_set_vbus', 'ump9620_set_current_limit', 'ump9620_set_roles',
                     'ump9620_set_pd_rx', 'ump9620_pd_transmit', 'ump9620_tx_done', 'ump9620_receive',
                     'ump9620_pd_irq'):
            parts.append(function(drv, name))
        with tempfile.TemporaryDirectory() as d:
            src, exe = Path(d) / 't.c', Path(d) / 't'
            src.write_text(''.join(parts) + MAIN)
            r = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wno-unused-function', '-Wno-unused-parameter',
                                '-Wno-unused-variable', '-o', str(exe), str(src)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'ok'), r.stderr)


if __name__ == '__main__':
    unittest.main()
