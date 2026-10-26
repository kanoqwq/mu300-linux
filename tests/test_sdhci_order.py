"""upstream/port/sdhci_order.py: sdhci-sprd's accessors get the generic SDHCI ordering (FINDINGS 93).

The text below is the accessor block of mainline drivers/mmc/host/sdhci-sprd.c (6.18 and 7.x), the shape the edit
is anchored on; the real tree is checked by the Mainline kernel build workflow, where install.py stops on a changed
anchor."""
import sys
import unittest

from helpers import TOP

sys.path.insert(0, str(TOP / 'upstream' / 'port'))
sys.dont_write_bytecode = True
import sdhci_order  # noqa: E402

MAINLINE = '''static void sdhci_sprd_init_config(struct sdhci_host *host)
{
	u32 val = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG);
	val = readl_relaxed(host->ioaddr + SDHCI_SPRD_REG_32_DLL_CFG);
}

static inline u32 sdhci_sprd_readl(struct sdhci_host *host, int reg)
{
	if (unlikely(reg == SDHCI_MAX_CURRENT))
		return SDHCI_SPRD_MAX_CUR;

	return readl_relaxed(host->ioaddr + reg);
}

static inline void sdhci_sprd_writel(struct sdhci_host *host, u32 val, int reg)
{
	/* SDHCI_MAX_CURRENT is reserved on Spreadtrum's platform */
	if (unlikely(reg == SDHCI_MAX_CURRENT))
		return;

	if (unlikely(reg == SDHCI_SIGNAL_ENABLE || reg == SDHCI_INT_ENABLE))
		val = val & SDHCI_SPRD_INT_SIGNAL_MASK;

	writel_relaxed(val, host->ioaddr + reg);
}

static inline void sdhci_sprd_writew(struct sdhci_host *host, u16 val, int reg)
{
	/* SDHCI_BLOCK_COUNT is Read Only on Spreadtrum's platform */
	if (unlikely(reg == SDHCI_BLOCK_COUNT))
		return;

	writew_relaxed(val, host->ioaddr + reg);
}

static inline void sdhci_sprd_writeb(struct sdhci_host *host, u8 val, int reg)
{
	/*
	 * Since BIT(3) of SDHCI_SOFTWARE_RESET is reserved according to the
	 * standard specification, sdhci_reset() write this register directly
	 * without checking other reserved bits, that will clear BIT(3) which
	 * is defined as hardware reset on Spreadtrum's platform and clearing
	 * it by mistake will lead the card not work. So here we need to work
	 * around it.
	 */
	if (unlikely(reg == SDHCI_SOFTWARE_RESET)) {
		if (readb_relaxed(host->ioaddr + reg) & SDHCI_HW_RESET_CARD)
			val |= SDHCI_HW_RESET_CARD;
	}

	writeb_relaxed(val, host->ioaddr + reg);
}

static inline void sdhci_sprd_sd_clk_off(struct sdhci_host *host)
{
	u16 ctrl = sdhci_readw(host, SDHCI_CLOCK_CONTROL);
}
'''


class SdhciOrder(unittest.TestCase):
    def test_the_four_accessors_are_ordered_and_the_special_cases_kept(self):
        out = sdhci_order.ordered(MAINLINE)
        block = out[out.index(sdhci_order.START):out.index(sdhci_order.END)]
        for line in ('\treturn readl(host->ioaddr + reg);', '\twritel(val, host->ioaddr + reg);',
                     '\twritew(val, host->ioaddr + reg);', '\twriteb(val, host->ioaddr + reg);'):
            self.assertEqual(block.count(line), 1, line)
        for kept in ('return SDHCI_SPRD_MAX_CUR;', 'val = val & SDHCI_SPRD_INT_SIGNAL_MASK;',
                     'if (unlikely(reg == SDHCI_BLOCK_COUNT))', 'val |= SDHCI_HW_RESET_CARD;'):
            self.assertIn(kept, block)
        self.assertNotIn('writel_relaxed', block)
        # the reset bit's read-modify-write keeps its relaxed read: it orders nothing against DMA memory, and the
        # write that follows it is the ordered one
        self.assertIn('if (readb_relaxed(host->ioaddr + reg) & SDHCI_HW_RESET_CARD)', block)
        # nothing outside the accessors changes
        self.assertEqual(out[:out.index(sdhci_order.START)], MAINLINE[:MAINLINE.index(sdhci_order.START)])
        self.assertEqual(out[out.index(sdhci_order.END):], MAINLINE[MAINLINE.index(sdhci_order.END):])

    def test_idempotent(self):
        once = sdhci_order.ordered(MAINLINE)
        self.assertEqual(sdhci_order.ordered(once), once)

    def test_a_changed_driver_stops_the_port(self):
        for broken in (MAINLINE.replace('\twritew_relaxed(val, host->ioaddr + reg);', '\tsprd_writew(val, reg);'),
                       MAINLINE.replace(sdhci_order.END, 'static void sdhci_sprd_other('),
                       MAINLINE.replace('\treturn readl_relaxed(host->ioaddr + reg);',
                                        '\treturn readl_relaxed(host->ioaddr + reg);\n\treturn readl_relaxed(host->ioaddr + reg);')):
            with self.assertRaises(ValueError):
                sdhci_order.ordered(broken)

    def test_install_uses_it_and_stops_on_a_changed_anchor(self):
        port = (TOP / 'upstream' / 'port' / 'install.py').read_text()
        self.assertIn('t = sdhci_order.ordered(t)', port)
        self.assertIn("sys.exit('port: ' + str(e))", port)
        self.assertIn('sys.dont_write_bytecode = True', port)
        self.assertLess(port.index('sdhci_order.ordered(t)'), port.index("open(sp, 'w').write(t)"))


if __name__ == '__main__':
    unittest.main()
