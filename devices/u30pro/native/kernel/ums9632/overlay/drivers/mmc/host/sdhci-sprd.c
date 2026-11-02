// SPDX-License-Identifier: GPL-2.0
//
// Secure Digital Host Controller
//
// Copyright (C) 2018 Spreadtrum, Inc.
// Author: Chunyan Zhang <chunyan.zhang@unisoc.com>

#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/highmem.h>
#include <linux/iopoll.h>
#include <linux/mmc/host.h>
#include <linux/mmc/mmc.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/pinctrl/consumer.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/mfd/syscon.h>
#include <linux/regmap.h>
#include "../core/card.h"

#include "sdhci-pltfm.h"
#include "mmc_hsq.h"

/* SDHCI_ARGUMENT2 register high 16bit */
#define SDHCI_SPRD_ARG2_STUFF		GENMASK(31, 16)

#define SDHCI_SPRD_REG_32_DLL_CFG	0x200
#define  SDHCI_SPRD_DLL_ALL_CPST_EN	(BIT(18) | BIT(24) | BIT(25) | BIT(26) | BIT(27))
#define  SDHCI_SPRD_DLL_EN		BIT(21)
#define  SDHCI_SPRD_DLL_SEARCH_MODE	BIT(16)
#define  SDHCI_SPRD_DLL_INIT_COUNT	0xc00
#define  SDHCI_SPRD_DLL_PHASE_INTERNAL	0x3

#define SDHCI_SPRD_REG_32_DLL_DLY	0x204

#define SDHCI_SPRD_REG_32_DLL_DLY_OFFSET	0x208
#define  SDHCIBSPRD_IT_WR_DLY_INV		BIT(5)
#define  SDHCI_SPRD_BIT_CMD_DLY_INV		BIT(13)
#define  SDHCI_SPRD_BIT_POSRD_DLY_INV		BIT(21)
#define  SDHCI_SPRD_BIT_NEGRD_DLY_INV		BIT(29)

#define SDHCI_SPRD_REG_32_DLL_STS0	0x210
#define SDHCI_SPRD_DLL_LOCKED		BIT(18)

#define SDHCI_SPRD_REG_32_BUSY_POSI		0x250
#define  SDHCI_SPRD_BIT_OUTR_CLK_AUTO_EN	BIT(25)
#define  SDHCI_SPRD_BIT_INNR_CLK_AUTO_EN	BIT(24)

#define SDHCI_SPRD_REG_DEBOUNCE		0x28C
#define  SDHCI_SPRD_BIT_DLL_BAK		BIT(0)
#define  SDHCI_SPRD_BIT_DLL_VAL		BIT(1)

#define  SDHCI_SPRD_INT_SIGNAL_MASK	0x1B7F410B

/* SDHCI_HOST_CONTROL2 */
#define  SDHCI_SPRD_CTRL_HS200		0x0005
#define  SDHCI_SPRD_CTRL_HS400		0x0006
#define  SDHCI_SPRD_CTRL_HS400ES	0x0007

/*
 * According to the standard specification, BIT(3) of SDHCI_SOFTWARE_RESET is
 * reserved, and only used on Spreadtrum's design, the hardware cannot work
 * if this bit is cleared.
 * 1 : normal work
 * 0 : hardware reset
 */
#define  SDHCI_HW_RESET_CARD		BIT(3)

#define SDHCI_SPRD_MAX_CUR		0xFFFFFF
#define SDHCI_SPRD_CLK_MAX_DIV		1023

#define SDHCI_SPRD_CLK_DEF_RATE		26000000
#define SDHCI_SPRD_PHY_DLL_CLK		52000000

#define SDHCI_SPRD_MAX_RANGE		0xff
#define SDHCI_SPRD_CMD_DLY_MASK		GENMASK(15, 8)
#define SDHCI_SPRD_POSRD_DLY_MASK	GENMASK(23, 16)
#define SDHCI_SPRD_CPST_EN		GENMASK(27, 24)

struct sdhci_sprd_host {
	bool r11p3_trial;
	bool sdio_host;	/* R11P3 host that only carries SDIO (no-sd, no-mmc: the AIC8800 Wi-Fi), bus mode from sdio_mode */
	unsigned int sdio_f_uhs;	/* its highest clock in the UHS-I modes */
	struct mmc_card *trial_card;
	struct clk *clk_ap_axi;
	u32 version;
	struct clk *clk_sdio;
	struct clk *clk_enable;
	struct clk *clk_2x_enable;
	struct pinctrl *pinctrl;
	struct pinctrl_state *pins_uhs;
	struct pinctrl_state *pins_default;
	u32 base_rate;
	int flags; /* backup of host attribute */
	u32 phy_delay[MMC_TIMING_MMC_HS400 + 2];
};

enum sdhci_sprd_tuning_type {
	SDHCI_SPRD_TUNING_SD_HS_CMD,
	SDHCI_SPRD_TUNING_SD_HS_DATA,
};

struct sdhci_sprd_phy_cfg {
	const char *property;
	u8 timing;
};

static const struct sdhci_sprd_phy_cfg sdhci_sprd_phy_cfgs[] = {
	{ "sprd,phy-delay-legacy", MMC_TIMING_LEGACY, },
	{ "sprd,phy-delay-sd-highspeed", MMC_TIMING_SD_HS, },
	{ "sprd,phy-delay-sd-uhs-sdr50", MMC_TIMING_UHS_SDR50, },
	{ "sprd,phy-delay-sd-uhs-sdr104", MMC_TIMING_UHS_SDR104, },
	{ "sprd,phy-delay-mmc-highspeed", MMC_TIMING_MMC_HS, },
	{ "sprd,phy-delay-mmc-ddr52", MMC_TIMING_MMC_DDR52, },
	{ "sprd,phy-delay-mmc-hs200", MMC_TIMING_MMC_HS200, },
	{ "sprd,phy-delay-mmc-hs400", MMC_TIMING_MMC_HS400, },
	{ "sprd,phy-delay-mmc-hs400es", MMC_TIMING_MMC_HS400 + 1, },
};

#define TO_SPRD_HOST(host) sdhci_pltfm_priv(sdhci_priv(host))

static void sdhci_sprd_init_config(struct sdhci_host *host)
{
	u16 val;

	/* set dll backup mode */
	val = sdhci_readl(host, SDHCI_SPRD_REG_DEBOUNCE);
	val |= SDHCI_SPRD_BIT_DLL_BAK | SDHCI_SPRD_BIT_DLL_VAL;
	sdhci_writel(host, val, SDHCI_SPRD_REG_DEBOUNCE);
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

	ctrl &= ~SDHCI_CLOCK_CARD_EN;
	sdhci_writew(host, ctrl, SDHCI_CLOCK_CONTROL);
}

static inline void sdhci_sprd_sd_clk_on(struct sdhci_host *host)
{
	u16 ctrl;

	ctrl = sdhci_readw(host, SDHCI_CLOCK_CONTROL);
	ctrl |= SDHCI_CLOCK_CARD_EN;
	sdhci_writew(host, ctrl, SDHCI_CLOCK_CONTROL);
}

static inline void
sdhci_sprd_set_dll_invert(struct sdhci_host *host, u32 mask, bool en)
{
	u32 dll_dly_offset;

	dll_dly_offset = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_DLY_OFFSET);
	if (en)
		dll_dly_offset |= mask;
	else
		dll_dly_offset &= ~mask;
	sdhci_writel(host, dll_dly_offset, SDHCI_SPRD_REG_32_DLL_DLY_OFFSET);
}

static inline u32 sdhci_sprd_calc_div(u32 base_clk, u32 clk)
{
	u32 div;

	/* select 2x clock source */
	if (base_clk <= clk * 2)
		return 0;

	div = (u32) (base_clk / (clk * 2));

	if ((base_clk / div) > (clk * 2))
		div++;

	if (div % 2)
		div = (div + 1) / 2;
	else
		div = div / 2;

	if (div > SDHCI_SPRD_CLK_MAX_DIV)
		div = SDHCI_SPRD_CLK_MAX_DIV;

	return div;
}

static inline void _sdhci_sprd_set_clock(struct sdhci_host *host,
					unsigned int clk)
{
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	u32 div, val, mask;

	sdhci_writew(host, 0, SDHCI_CLOCK_CONTROL);

	div = sdhci_sprd_calc_div(sprd_host->base_rate, clk);
	div = ((div & 0x300) >> 2) | ((div & 0xFF) << 8);
	sdhci_enable_clk(host, div);

	val = sdhci_readl(host, SDHCI_SPRD_REG_32_BUSY_POSI);
	mask = SDHCI_SPRD_BIT_OUTR_CLK_AUTO_EN | SDHCI_SPRD_BIT_INNR_CLK_AUTO_EN;
	/* Enable CLK_AUTO when the clock is greater than 400K. */
	if (clk > 400000) {
		if (mask != (val & mask)) {
			val |= mask;
			sdhci_writel(host, val, SDHCI_SPRD_REG_32_BUSY_POSI);
		}
	} else {
		if (val & mask) {
			val &= ~mask;
			sdhci_writel(host, val, SDHCI_SPRD_REG_32_BUSY_POSI);
		}
	}
}

static void sdhci_sprd_enable_phy_dll(struct sdhci_host *host)
{
	u32 tmp;

	tmp = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG);
	tmp &= ~(SDHCI_SPRD_DLL_EN | SDHCI_SPRD_DLL_ALL_CPST_EN);
	sdhci_writel(host, tmp, SDHCI_SPRD_REG_32_DLL_CFG);
	/* wait 1ms */
	usleep_range(1000, 1250);

	tmp = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG);
	tmp |= SDHCI_SPRD_DLL_ALL_CPST_EN | SDHCI_SPRD_DLL_SEARCH_MODE |
		SDHCI_SPRD_DLL_INIT_COUNT | SDHCI_SPRD_DLL_PHASE_INTERNAL;
	sdhci_writel(host, tmp, SDHCI_SPRD_REG_32_DLL_CFG);
	/* wait 1ms */
	usleep_range(1000, 1250);

	tmp = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG);
	tmp |= SDHCI_SPRD_DLL_EN;
	sdhci_writel(host, tmp, SDHCI_SPRD_REG_32_DLL_CFG);
	/* wait 1ms */
	usleep_range(1000, 1250);

	if (read_poll_timeout(sdhci_readl, tmp, (tmp & SDHCI_SPRD_DLL_LOCKED),
		2000, USEC_PER_SEC, false, host, SDHCI_SPRD_REG_32_DLL_STS0)) {
		pr_err("%s: DLL locked fail!\n", mmc_hostname(host->mmc));
		pr_info("%s: DLL_STS0 : 0x%x, DLL_CFG : 0x%x\n",
			 mmc_hostname(host->mmc),
			 sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_STS0),
			 sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG));
	}
}

/* settings of the SDIO host's bus mode, see sdhci_sprd_sdio_apply() */
static unsigned int sdio_mode;
static unsigned int sdio_max_hz;
static struct sdhci_host *sdio_mode_host;

static void sdhci_sprd_set_clock(struct sdhci_host *host, unsigned int clock)
{
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	bool en = false, clk_changed = false;

	if (clock == 0) {
		sdhci_writew(host, 0, SDHCI_CLOCK_CONTROL);
	} else if (clock != host->clock) {
		sdhci_sprd_sd_clk_off(host);
		_sdhci_sprd_set_clock(host, clock);

		if (clock <= 400000)
			en = true;
		sdhci_sprd_set_dll_invert(host, SDHCI_SPRD_BIT_CMD_DLY_INV |
					  SDHCI_SPRD_BIT_POSRD_DLY_INV, en);
		clk_changed = true;
	} else {
		_sdhci_sprd_set_clock(host, clock);
	}

	/*
	 * According to the Spreadtrum SD host specification, when we changed
	 * the clock to be more than 52M, we should enable the PHY DLL which
	 * is used to track the clock frequency to make the clock work more
	 * stable. Otherwise deviation may occur of the higher clock.
	 */
	/* The R11P3 bootstrap is capped at 52 MHz, outside the DLL path; the SDIO host in a UHS-I mode takes it.  (In high speed
	 * the AIC8800 driver asks for 150 MHz itself; that stays as it was before sdio_mode.) */
	if (sprd_host->r11p3_trial && !(sprd_host->sdio_host && READ_ONCE(sdio_mode))) {
		u32 cfg = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG);

		cfg &= ~(SDHCI_SPRD_DLL_EN | SDHCI_SPRD_DLL_ALL_CPST_EN);
		sdhci_writel(host, cfg, SDHCI_SPRD_REG_32_DLL_CFG);
		return;
	}

	if (clk_changed && clock > SDHCI_SPRD_PHY_DLL_CLK)
		sdhci_sprd_enable_phy_dll(host);
}

static unsigned int sdhci_sprd_get_max_clock(struct sdhci_host *host)
{
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);

	return clk_round_rate(sprd_host->clk_sdio, ULONG_MAX);
}

static unsigned int sdhci_sprd_get_min_clock(struct sdhci_host *host)
{
	return 100000;
}

static void sdhci_sprd_set_uhs_signaling(struct sdhci_host *host,
					 unsigned int timing)
{
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	struct mmc_host *mmc = host->mmc;
	u32 *p = sprd_host->phy_delay;
	u16 ctrl_2;

	if (timing == host->timing)
		return;

	ctrl_2 = sdhci_readw(host, SDHCI_HOST_CONTROL2);
	/* Select Bus Speed Mode for host */
	ctrl_2 &= ~SDHCI_CTRL_UHS_MASK;
	switch (timing) {
	case MMC_TIMING_UHS_SDR12:
		ctrl_2 |= SDHCI_CTRL_UHS_SDR12;
		break;
	case MMC_TIMING_MMC_HS:
	case MMC_TIMING_SD_HS:
	case MMC_TIMING_UHS_SDR25:
		ctrl_2 |= SDHCI_CTRL_UHS_SDR25;
		break;
	case MMC_TIMING_UHS_SDR50:
		ctrl_2 |= SDHCI_CTRL_UHS_SDR50;
		break;
	case MMC_TIMING_UHS_SDR104:
		ctrl_2 |= SDHCI_CTRL_UHS_SDR104;
		break;
	case MMC_TIMING_UHS_DDR50:
	case MMC_TIMING_MMC_DDR52:
		ctrl_2 |= SDHCI_CTRL_UHS_DDR50;
		break;
	case MMC_TIMING_MMC_HS200:
		ctrl_2 |= SDHCI_SPRD_CTRL_HS200;
		break;
	case MMC_TIMING_MMC_HS400:
		ctrl_2 |= SDHCI_SPRD_CTRL_HS400;
		break;
	default:
		break;
	}

	sdhci_writew(host, ctrl_2, SDHCI_HOST_CONTROL2);

	if (!mmc->ios.enhanced_strobe)
		sdhci_writel(host, p[timing], SDHCI_SPRD_REG_32_DLL_DLY);
}

static void sdhci_sprd_hw_reset(struct sdhci_host *host)
{
	int val;

	/*
	 * Note: don't use sdhci_writeb() API here since it is redirected to
	 * sdhci_sprd_writeb() in which we have a workaround for
	 * SDHCI_SOFTWARE_RESET which would make bit SDHCI_HW_RESET_CARD can
	 * not be cleared.
	 */
	val = readb_relaxed(host->ioaddr + SDHCI_SOFTWARE_RESET);
	val &= ~SDHCI_HW_RESET_CARD;
	writeb_relaxed(val, host->ioaddr + SDHCI_SOFTWARE_RESET);
	/* wait for 10 us */
	usleep_range(10, 20);

	val |= SDHCI_HW_RESET_CARD;
	writeb_relaxed(val, host->ioaddr + SDHCI_SOFTWARE_RESET);
	usleep_range(300, 500);
}

static unsigned int sdhci_sprd_get_max_timeout_count(struct sdhci_host *host)
{
	/* The Spredtrum controller actual maximum timeout count is 1 << 31 */
	return 1 << 31;
}

static unsigned int sdhci_sprd_get_ro(struct sdhci_host *host)
{
	return 0;
}

static void sdhci_sprd_request_done(struct sdhci_host *host,
				    struct mmc_request *mrq)
{
	/* Validate if the request was from software queue firstly. */
	if (mmc_hsq_finalize_request(host->mmc, mrq))
		return;

	mmc_request_done(host->mmc, mrq);
}

static void sdhci_sprd_set_power(struct sdhci_host *host, unsigned char mode,
				 unsigned short vdd)
{
	struct mmc_host *mmc = host->mmc;

	switch (mode) {
	case MMC_POWER_OFF:
		mmc_regulator_set_ocr(host->mmc, mmc->supply.vmmc, 0);

		mmc_regulator_disable_vqmmc(mmc);
		break;
	case MMC_POWER_ON:
		mmc_regulator_enable_vqmmc(mmc);
		break;
	case MMC_POWER_UP:
		mmc_regulator_set_ocr(host->mmc, mmc->supply.vmmc, vdd);
		break;
	}
}

static const struct sdhci_ops sdhci_sprd_ops = {
	.read_l = sdhci_sprd_readl,
	.write_l = sdhci_sprd_writel,
	.write_w = sdhci_sprd_writew,
	.write_b = sdhci_sprd_writeb,
	.set_clock = sdhci_sprd_set_clock,
	.set_power = sdhci_sprd_set_power,
	.get_max_clock = sdhci_sprd_get_max_clock,
	.get_min_clock = sdhci_sprd_get_min_clock,
	.set_bus_width = sdhci_set_bus_width,
	.reset = sdhci_reset,
	.set_uhs_signaling = sdhci_sprd_set_uhs_signaling,
	.hw_reset = sdhci_sprd_hw_reset,
	.get_max_timeout_count = sdhci_sprd_get_max_timeout_count,
	.get_ro = sdhci_sprd_get_ro,
	.request_done = sdhci_sprd_request_done,
};

static void sdhci_sprd_check_auto_cmd23(struct mmc_host *mmc,
					struct mmc_request *mrq)
{
	struct sdhci_host *host = mmc_priv(mmc);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);

	host->flags |= sprd_host->flags & SDHCI_AUTO_CMD23;

	/*
	 * From version 4.10 onward, ARGUMENT2 register is also as 32-bit
	 * block count register which doesn't support stuff bits of
	 * CMD23 argument on Spreadtrum's sd host controller.
	 */
	if (host->version >= SDHCI_SPEC_410 &&
	    mrq->sbc && (mrq->sbc->arg & SDHCI_SPRD_ARG2_STUFF) &&
	    (host->flags & SDHCI_AUTO_CMD23))
		host->flags &= ~SDHCI_AUTO_CMD23;
}

/*
 * Write protection of the eMMC, on unless switched off: sdhci_sprd.write_protect=0 on the kernel command line, or at run time
 * (root) echo 0 > /sys/module/sdhci_sprd/parameters/write_protect.  While it is on, only the write windows below can be written;
 * while it is off, every command reaches the card unchecked (any partition, boot areas, RPMB, erase, every EXT_CSD switch).
 */
static bool write_protect = true;

static int u30pro_write_protect_set(const char *val, const struct kernel_param *kp)
{
	bool was = READ_ONCE(write_protect);
	int ret = param_set_bool(val, kp);

	if (!ret && READ_ONCE(write_protect) != was)
		pr_warn("sdhci-sprd: eMMC write protection %s\n",
			write_protect ? "on: only the write windows can be written" : "OFF: every write and erase reaches the card");
	return ret;
}

static const struct kernel_param_ops u30pro_write_protect_ops = {
	.set = u30pro_write_protect_set,
	.get = param_get_bool,
};
module_param_cb(write_protect, &u30pro_write_protect_ops, &write_protect, 0644);
MODULE_PARM_DESC(write_protect, "1 (default): only the write windows of the kernel command line can be written; 0: no protection at all");

/* The R11P3 host that only carries SDIO (no-sd, no-mmc: the AIC8800 Wi-Fi) is not the disk, so the 52 MHz bootstrap clamp of the
 * eMMC does not have to hold for it.  At 50 MHz high speed the bus carries about 130 Mbit/s, below what the radio and the cellular
 * link do; SDR104 at 208 MHz (p538) enumerated and tuned but the AIC8800 firmware download then timed out.  So the mode is a
 * setting: sdio_mode 0 high speed at most 52 MHz (default), 1 SDR50, 2 SDR104, 3 DDR50; sdio_max_hz caps the clock of the UHS-I
 * modes (0: the host's maximum).  Both can be written at run time and count from the next initialization of the card, which on
 * this board is when aic8800_bsp powers the chip on (rcS sets them before kmodloader, /usr/sbin/u30pro-wifi-sdio). */

static void sdhci_sprd_sdio_apply(struct sdhci_host *host)
{
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	struct mmc_host *mmc = host->mmc;
	unsigned int mode = READ_ONCE(sdio_mode), max_hz = READ_ONCE(sdio_max_hz);

	mmc->caps &= ~(MMC_CAP_UHS | MMC_CAP_DDR);
	switch (mode) {
	case 1:
		mmc->caps |= MMC_CAP_UHS_SDR12 | MMC_CAP_UHS_SDR25 | MMC_CAP_UHS_SDR50;
		break;
	case 2:
		mmc->caps |= MMC_CAP_UHS_SDR12 | MMC_CAP_UHS_SDR25 | MMC_CAP_UHS_SDR50 | MMC_CAP_UHS_SDR104;
		break;
	case 3:
		mmc->caps |= MMC_CAP_UHS_SDR12 | MMC_CAP_UHS_SDR25 | MMC_CAP_UHS_DDR50;
		break;
	}
	mmc->f_max = mode ? sprd_host->sdio_f_uhs : 52000000;
	if (mode && max_hz && max_hz < mmc->f_max)
		mmc->f_max = max_hz;
	dev_info(mmc_dev(mmc), "SDIO bus: %s, up to %u Hz (from the next card initialization)\n",
		 mode == 1 ? "UHS-I SDR50" : mode == 2 ? "UHS-I SDR104" : mode == 3 ? "UHS-I DDR50" : "high speed", mmc->f_max);
}

static int sdhci_sprd_sdio_param_set(const char *val, const struct kernel_param *kp)
{
	unsigned int v;
	int ret = kstrtouint(val, 0, &v);

	if (ret)
		return ret;
	if (kp->arg == &sdio_mode && v > 3)
		return -EINVAL;
	*(unsigned int *)kp->arg = v;
	if (sdio_mode_host)
		sdhci_sprd_sdio_apply(sdio_mode_host);
	return 0;
}

static const struct kernel_param_ops sdhci_sprd_sdio_param_ops = {
	.set = sdhci_sprd_sdio_param_set,
	.get = param_get_uint,
};
module_param_cb(sdio_mode, &sdhci_sprd_sdio_param_ops, &sdio_mode, 0644);
MODULE_PARM_DESC(sdio_mode, "Bus mode of the R11P3 SDIO-only host (Wi-Fi): 0 high speed (default), 1 SDR50, 2 SDR104, 3 DDR50; from the next card initialization");
module_param_cb(sdio_max_hz, &sdhci_sprd_sdio_param_ops, &sdio_max_hz, 0644);
MODULE_PARM_DESC(sdio_max_hz, "Highest clock of the SDIO UHS-I modes (0: the host's maximum)");

/*
 * Optional write windows.  Everything stays read-only except at most two LBA ranges named on the kernel command line:
 *   u30pro_rw_lba=<first sector> u30pro_rw_sectors=<count>      persistent configuration partition
 *   u30pro_rw2_lba=<first sector> u30pro_rw2_sectors=<count>    (off unless given) the A/B control sector in misc
 * Only plain CMD24/CMD25 single/multiple block writes of 512-byte blocks that lie completely inside a window on a
 * block-addressed card are admitted.  The partition-switch rule below keeps the card in the user area, so boot,
 * RPMB and every other write, erase, sanitize, OTP or vendor command is still refused with -EROFS.
 */
static unsigned long long u30pro_rw_lba, u30pro_rw_sectors, u30pro_rw2_lba, u30pro_rw2_sectors;
core_param(u30pro_rw_lba, u30pro_rw_lba, ullong, 0444);
core_param(u30pro_rw_sectors, u30pro_rw_sectors, ullong, 0444);
core_param(u30pro_rw2_lba, u30pro_rw2_lba, ullong, 0444);
core_param(u30pro_rw2_sectors, u30pro_rw2_sectors, ullong, 0444);

/*
 * More windows for the A/B updater: u30pro_rwx=<lba>+<sectors>[,<lba>+<sectors>...] (at most U30PRO_RWX_MAX ranges).  The boot
 * image of one slot names the boot, init_boot and vendor_boot partitions of the OTHER slot here, so a running slot can rewrite the
 * slot it is not running from and nothing else.
 */
#define U30PRO_RWX_MAX 8
static char *u30pro_rwx;
core_param(u30pro_rwx, u30pro_rwx, charp, 0444);
static unsigned long long u30pro_rwx_lba[U30PRO_RWX_MAX], u30pro_rwx_sectors[U30PRO_RWX_MAX];
static unsigned int u30pro_rwx_n;

static void u30pro_rwx_parse(void)
{
	const char *p = u30pro_rwx;

	u30pro_rwx_n = 0;
	while (p && *p && u30pro_rwx_n < U30PRO_RWX_MAX) {
		char *end;
		unsigned long long lba = simple_strtoull(p, &end, 10), n;

		if (end == p || *end != '+')
			break;
		p = end + 1;
		n = simple_strtoull(p, &end, 10);
		if (end == p || !n)
			break;
		u30pro_rwx_lba[u30pro_rwx_n] = lba;
		u30pro_rwx_sectors[u30pro_rwx_n] = n;
		u30pro_rwx_n++;
		if (*end != ',')
			break;
		p = end + 1;
	}
}

static bool sdhci_sprd_write_window_ok(struct sdhci_sprd_host *sprd_host, struct mmc_request *mrq)
{
	u32 op = mrq->cmd->opcode;
	u64 start, end;
	unsigned int i;

	if (op != MMC_WRITE_BLOCK && op != MMC_WRITE_MULTIPLE_BLOCK)
		return false;
	if (!mrq->data || mrq->data->blksz != 512 || !mrq->data->blocks)
		return false;
	if (!sprd_host->trial_card || !mmc_card_blockaddr(sprd_host->trial_card))
		return false;
	start = mrq->cmd->arg;
	end = start + mrq->data->blocks;
	if (u30pro_rw_sectors && start >= u30pro_rw_lba && end <= u30pro_rw_lba + u30pro_rw_sectors)
		return true;
	if (u30pro_rw2_sectors && start >= u30pro_rw2_lba && end <= u30pro_rw2_lba + u30pro_rw2_sectors)
		return true;
	for (i = 0; i < u30pro_rwx_n; i++)
		if (start >= u30pro_rwx_lba[i] && end <= u30pro_rwx_lba[i] + u30pro_rwx_sectors[i])
			return true;
	return false;
}

/* A bounded bus-width test writes the controller/card test pattern, not media. */
static bool sdhci_sprd_trial_forbidden(struct sdhci_sprd_host *sprd_host,
				     struct mmc_host *mmc, struct mmc_request *mrq)
{
	u32 op = mrq->cmd->opcode;
	bool bus_test = op == MMC_BUS_TEST_W && mrq->data &&
		mrq->data->blocks == 1 &&
		(mrq->data->blksz == 4 || mrq->data->blksz == 8);

	if (!sprd_host->r11p3_trial || !(mmc->caps2 & MMC_CAP2_NO_SDIO))
		return false;
	if (!READ_ONCE(write_protect))
		return false;
	if (mrq->data && (mrq->data->flags & MMC_DATA_WRITE) && !bus_test)
		return !sdhci_sprd_write_window_ok(sprd_host, mrq);
	/* Only the native MMC initialization/read command set is admitted. */
	switch (op) {
	case MMC_GO_IDLE_STATE:
	case MMC_SEND_OP_COND:
	case MMC_ALL_SEND_CID:
	case MMC_SET_RELATIVE_ADDR:
	case MMC_SET_DSR:
	case MMC_SLEEP_AWAKE:
	case MMC_SELECT_CARD:
	case MMC_SEND_EXT_CSD:
	case MMC_SEND_CSD:
	case MMC_SEND_CID:
	case MMC_STOP_TRANSMISSION:
	case MMC_SEND_STATUS:
	case MMC_BUS_TEST_R:
	case MMC_GO_INACTIVE_STATE:
	case MMC_SET_BLOCKLEN:
	case MMC_READ_SINGLE_BLOCK:
	case MMC_READ_MULTIPLE_BLOCK:
	case MMC_SET_BLOCK_COUNT:
	case MMC_SEND_WRITE_PROT:
		return false;
	case MMC_BUS_TEST_W:
		return !bus_test;
	case MMC_SWITCH: {
		u8 index = (mrq->cmd->arg >> 16) & 0xff;

		if (((mrq->cmd->arg >> 24) & 3) != MMC_SWITCH_MODE_WRITE_BYTE)
			return true;
		/* Never OTP/boot-selection/FFU/sanitize/manufacturer commands. */
		switch (index) {
		case EXT_CSD_BUS_WIDTH:
		case EXT_CSD_HS_TIMING:
		case EXT_CSD_POWER_CLASS:
		case EXT_CSD_ERASE_GROUP_DEF:
		case EXT_CSD_CMDQ_MODE_EN:
		case EXT_CSD_CACHE_CTRL:
		case EXT_CSD_FLUSH_CACHE:
		case EXT_CSD_POWER_OFF_NOTIFICATION:
		case EXT_CSD_EXP_EVENTS_CTRL:
		case EXT_CSD_HPI_MGMT:
			return false;
		case EXT_CSD_PART_CONFIG:
			/* Core may return to user access, preserving boot/ACK bits. */
			return !sprd_host->trial_card ||
				((mrq->cmd->arg >> 8) & 0xff) !=
				(sprd_host->trial_card->ext_csd.part_config & ~7);
		default:
			return true;
		}
	}
	default:
		return true;
	}
}

static bool sdhci_sprd_trial_reject(struct mmc_host *mmc, struct mmc_request *mrq)
{
	struct sdhci_host *host = mmc_priv(mmc);

	if (!sdhci_sprd_trial_forbidden(TO_SPRD_HOST(host), mmc, mrq))
		return false;
	mrq->cmd->error = -EROFS;
	if (mrq->data)
		mrq->data->error = -EROFS;
	sdhci_sprd_request_done(host, mrq);
	return true;
}

static void sdhci_sprd_trial_init_card(struct mmc_host *mmc, struct mmc_card *card)
{
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(mmc_priv(mmc));

	if (sprd_host->r11p3_trial) {
		sprd_host->trial_card = card;
		/* The disk is read-only unless a window was granted or the protection is off; the host filter still decides every
		 * single write.  (A disk marked read-only here stays so until the next boot, even if the protection is switched off.) */
		u30pro_rwx_parse();
		if (!write_protect)
			dev_warn(mmc_dev(mmc), "write protection is off: every write and erase reaches the card\n");
		else if (!u30pro_rw_sectors && !u30pro_rw2_sectors && !u30pro_rwx_n)
			mmc_card_set_readonly(card);
		else {
			unsigned int i;

			dev_info(mmc_dev(mmc), "write windows: [%llu,+%llu) [%llu,+%llu); everything else stays read-only\n",
				 u30pro_rw_lba, u30pro_rw_sectors, u30pro_rw2_lba, u30pro_rw2_sectors);
			for (i = 0; i < u30pro_rwx_n; i++)
				dev_info(mmc_dev(mmc), "write window x%u: [%llu,+%llu)\n", i, u30pro_rwx_lba[i], u30pro_rwx_sectors[i]);
		}
	}
}

static void sdhci_sprd_request(struct mmc_host *mmc, struct mmc_request *mrq)
{
	if (sdhci_sprd_trial_reject(mmc, mrq))
		return;
	sdhci_sprd_check_auto_cmd23(mmc, mrq);

	sdhci_request(mmc, mrq);
}

static int sdhci_sprd_request_atomic(struct mmc_host *mmc,
				     struct mmc_request *mrq)
{
	if (sdhci_sprd_trial_reject(mmc, mrq))
		return 0;
	sdhci_sprd_check_auto_cmd23(mmc, mrq);

	return sdhci_request_atomic(mmc, mrq);
}

static int sdhci_sprd_voltage_switch(struct mmc_host *mmc, struct mmc_ios *ios)
{
	struct sdhci_host *host = mmc_priv(mmc);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	int ret;

	if (!IS_ERR(mmc->supply.vqmmc)) {
		ret = mmc_regulator_set_vqmmc(mmc, ios);
		if (ret < 0) {
			pr_err("%s: Switching signalling voltage failed\n",
			       mmc_hostname(mmc));
			return ret;
		}
	}

	if (IS_ERR(sprd_host->pinctrl))
		goto reset;

	switch (ios->signal_voltage) {
	case MMC_SIGNAL_VOLTAGE_180:
		ret = pinctrl_select_state(sprd_host->pinctrl,
					   sprd_host->pins_uhs);
		if (ret) {
			pr_err("%s: failed to select uhs pin state\n",
			       mmc_hostname(mmc));
			return ret;
		}
		break;

	default:
		fallthrough;
	case MMC_SIGNAL_VOLTAGE_330:
		ret = pinctrl_select_state(sprd_host->pinctrl,
					   sprd_host->pins_default);
		if (ret) {
			pr_err("%s: failed to select default pin state\n",
			       mmc_hostname(mmc));
			return ret;
		}
		break;
	}

	/* Wait for 300 ~ 500 us for pin state stable */
	usleep_range(300, 500);

reset:
	sdhci_reset(host, SDHCI_RESET_CMD | SDHCI_RESET_DATA);

	return 0;
}

static void sdhci_sprd_hs400_enhanced_strobe(struct mmc_host *mmc,
					     struct mmc_ios *ios)
{
	struct sdhci_host *host = mmc_priv(mmc);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	u32 *p = sprd_host->phy_delay;
	u16 ctrl_2;

	if (!ios->enhanced_strobe)
		return;

	sdhci_sprd_sd_clk_off(host);

	/* Set HS400 enhanced strobe mode */
	ctrl_2 = sdhci_readw(host, SDHCI_HOST_CONTROL2);
	ctrl_2 &= ~SDHCI_CTRL_UHS_MASK;
	ctrl_2 |= SDHCI_SPRD_CTRL_HS400ES;
	sdhci_writew(host, ctrl_2, SDHCI_HOST_CONTROL2);

	sdhci_sprd_sd_clk_on(host);

	/* Set the PHY DLL delay value for HS400 enhanced strobe mode */
	sdhci_writel(host, p[MMC_TIMING_MMC_HS400 + 1],
		     SDHCI_SPRD_REG_32_DLL_DLY);
}

static int mmc_send_tuning_cmd(struct mmc_card *card)
{
	return mmc_send_status(card, NULL);
}

static int mmc_send_tuning_data(struct mmc_card *card)
{
	u8 *status;
	int ret;

	status = kmalloc(64, GFP_KERNEL);
	if (!status)
		return -ENOMEM;

	ret = mmc_sd_switch(card, 0, 0, 0, status);

	kfree(status);

	return ret;
}

static int sdhci_sprd_get_best_clk_sample(struct mmc_host *mmc, u8 *value)
{
	int range_end = SDHCI_SPRD_MAX_RANGE;
	int range_length = 0;
	int middle_range = 0;
	int count = 0;
	int i;

	for (i = 0; i <= SDHCI_SPRD_MAX_RANGE; i++) {
		if (value[i]) {
			pr_debug("%s: tuning ok: %d\n", mmc_hostname(mmc), i);
			count++;
		} else {
			pr_debug("%s: tuning fail: %d\n", mmc_hostname(mmc), i);
			if (range_length < count) {
				range_length = count;
				range_end = i - 1;
				count = 0;
			}
		}
	}

	if (!count)
		return -EIO;

	if (count > range_length) {
		range_length = count;
		range_end = i - 1;
	}

	middle_range = range_end - (range_length - 1) / 2;

	return middle_range;
}

static int sdhci_sprd_tuning(struct mmc_host *mmc, struct mmc_card *card,
			enum sdhci_sprd_tuning_type type)
{
	struct sdhci_host *host = mmc_priv(mmc);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	u32 *p = sprd_host->phy_delay;
	u32 dll_cfg, dll_dly;
	int best_clk_sample;
	int err = 0;
	u8 *value;
	int i;

	value = kmalloc(SDHCI_SPRD_MAX_RANGE + 1, GFP_KERNEL);
	if (!value)
		return -ENOMEM;

	sdhci_reset(host, SDHCI_RESET_CMD | SDHCI_RESET_DATA);

	dll_cfg = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG);
	dll_cfg &= ~SDHCI_SPRD_CPST_EN;
	sdhci_writel(host, dll_cfg, SDHCI_SPRD_REG_32_DLL_CFG);

	dll_dly = p[mmc->ios.timing];

	for (i = 0; i <= SDHCI_SPRD_MAX_RANGE; i++) {
		if (type == SDHCI_SPRD_TUNING_SD_HS_CMD) {
			dll_dly &= ~SDHCI_SPRD_CMD_DLY_MASK;
			dll_dly |= ((i << 8) & SDHCI_SPRD_CMD_DLY_MASK);
		} else {
			dll_dly &= ~SDHCI_SPRD_POSRD_DLY_MASK;
			dll_dly |= ((i << 16) & SDHCI_SPRD_POSRD_DLY_MASK);
		}

		sdhci_writel(host, dll_dly, SDHCI_SPRD_REG_32_DLL_DLY);

		if (type == SDHCI_SPRD_TUNING_SD_HS_CMD)
			value[i] = !mmc_send_tuning_cmd(card);
		else
			value[i] = !mmc_send_tuning_data(card);
	}

	best_clk_sample = sdhci_sprd_get_best_clk_sample(mmc, value);
	if (best_clk_sample < 0) {
		dev_err(mmc_dev(host->mmc), "all tuning phase fail!\n");
		err = best_clk_sample;
		goto out;
	}

	if (type == SDHCI_SPRD_TUNING_SD_HS_CMD) {
		p[mmc->ios.timing] &= ~SDHCI_SPRD_CMD_DLY_MASK;
		p[mmc->ios.timing] |= ((best_clk_sample << 8) & SDHCI_SPRD_CMD_DLY_MASK);
	} else {
		p[mmc->ios.timing] &= ~(SDHCI_SPRD_POSRD_DLY_MASK);
		p[mmc->ios.timing] |= ((best_clk_sample << 16) & SDHCI_SPRD_POSRD_DLY_MASK);
	}

	pr_debug("%s: the best clk sample %d, delay value 0x%08x\n",
			mmc_hostname(host->mmc), best_clk_sample, p[mmc->ios.timing]);

out:
	sdhci_writel(host, p[mmc->ios.timing], SDHCI_SPRD_REG_32_DLL_DLY);

	kfree(value);

	return err;
}

static int sdhci_sprd_prepare_sd_hs_cmd_tuning(struct mmc_host *mmc, struct mmc_card *card)
{
	return sdhci_sprd_tuning(mmc, card, SDHCI_SPRD_TUNING_SD_HS_CMD);
}

static int sdhci_sprd_execute_sd_hs_data_tuning(struct mmc_host *mmc, struct mmc_card *card)
{
	return sdhci_sprd_tuning(mmc, card, SDHCI_SPRD_TUNING_SD_HS_DATA);
}

/*
 * Tuning of the SDIO host in SDR104, as the vendor driver does it (MU5358 BSP, sdhci-sprd-tuning.c, the default type that SDIO
 * takes): the standard SDHCI tuning reports success on this controller, but the Wi-Fi then fails at the first transfers.  With
 * ADMA off, every step of the PHY DLL (command and read-sample delay together) sends CMD19; the steps from dll_cnt on repeat the
 * first ones, so a step counts when both pass.  The middle of the longest passing run, as a phase of 256 with compensation on,
 * becomes the delay of SDR104.  Other timings keep the standard path (SDR50 works without tuning).
 */
static int sdhci_sprd_sdio_execute_tuning(struct mmc_host *mmc, u32 opcode)
{
	struct sdhci_host *host = mmc_priv(mmc);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	u32 *p = sprd_host->phy_delay;
	u32 mask = SDHCI_SPRD_CMD_DLY_MASK | SDHCI_SPRD_POSRD_DLY_MASK;
	u32 dll_cfg, dll_cnt, dly, tmp;
	unsigned int len, i, run = 0, start = 0, best = 0, best_start = 0, mid, phase;
	bool adma = false, sdma;
	u8 *ok;

	if (mmc->ios.timing != MMC_TIMING_UHS_SDR104)
		return sdhci_execute_tuning(mmc, opcode);

	sdhci_reset(host, SDHCI_RESET_CMD | SDHCI_RESET_DATA);
	dll_cfg = sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_CFG) & ~SDHCI_SPRD_CPST_EN;
	sdhci_writel(host, dll_cfg, SDHCI_SPRD_REG_32_DLL_CFG);
	/* the DLL locks in half mode on R11 */
	dll_cnt = (sdhci_readl(host, SDHCI_SPRD_REG_32_DLL_STS0) & 0xff) << 1;
	if (!dll_cnt) {
		dev_warn(mmc_dev(mmc), "SDIO tuning: DLL count %u\n", dll_cnt);
		return -EIO;
	}
	len = dll_cnt * 3 / 2;
	ok = kcalloc(len, 1, GFP_KERNEL);
	if (!ok)
		return -ENOMEM;

	sdma = host->flags & SDHCI_USE_SDMA;
	if (host->flags & SDHCI_USE_ADMA) {
		adma = true;
		host->flags &= ~SDHCI_USE_ADMA;
		host->flags |= SDHCI_USE_SDMA;
	}

	dly = p[mmc->ios.timing];
	for (i = 0; i < len; i++) {
		dly = (dly & ~mask) | ((i << 8) & SDHCI_SPRD_CMD_DLY_MASK) | ((i << 16) & SDHCI_SPRD_POSRD_DLY_MASK);
		sdhci_writel(host, dly, SDHCI_SPRD_REG_32_DLL_DLY);
		ok[i] = !mmc_send_tuning(mmc, opcode, NULL);
		if (!ok[i]) {
			/* a failed read can leave the controller busy: wait for it before the next step */
			read_poll_timeout(sdhci_readl, tmp, !(tmp & SDHCI_DOING_READ), USEC_PER_MSEC,
					  100 * USEC_PER_MSEC, false, host, SDHCI_PRESENT_STATE);
			sdhci_reset(host, SDHCI_RESET_CMD | SDHCI_RESET_DATA);
		}
	}

	if (adma)
		host->flags |= SDHCI_USE_ADMA;
	if (!sdma)
		host->flags &= ~SDHCI_USE_SDMA;

	for (i = 0; i + dll_cnt < len; i++)
		ok[i] = ok[i] && ok[i + dll_cnt];
	/* the longest run of passing steps, around the end of the range as well */
	for (i = 0; i < 2 * dll_cnt && best < dll_cnt; i++) {
		if (!ok[i % dll_cnt]) {
			run = 0;
			continue;
		}
		if (!run++)
			start = i;
		if (run > best) {
			best = run;
			best_start = start;
		}
	}
	kfree(ok);
	if (!best) {
		sdhci_writel(host, p[mmc->ios.timing], SDHCI_SPRD_REG_32_DLL_DLY);
		dev_warn(mmc_dev(mmc), "SDIO tuning: no step of %u passes\n", dll_cnt);
		return -EIO;
	}

	mid = (best_start + best / 2) % dll_cnt;
	phase = mid * 256 / dll_cnt;
	sdhci_writel(host, dll_cfg | SDHCI_SPRD_CPST_EN, SDHCI_SPRD_REG_32_DLL_CFG);
	p[mmc->ios.timing] = (p[mmc->ios.timing] & ~mask) | ((phase << 8) & SDHCI_SPRD_CMD_DLY_MASK) |
			     ((phase << 16) & SDHCI_SPRD_POSRD_DLY_MASK);
	sdhci_writel(host, p[mmc->ios.timing], SDHCI_SPRD_REG_32_DLL_DLY);
	dev_info(mmc_dev(mmc), "SDIO tuning at %u Hz: %u of %u steps pass from %u, step %u, phase 0x%02x, delay 0x%08x\n",
		 mmc->ios.clock, best, dll_cnt, best_start % dll_cnt, mid, phase, p[mmc->ios.timing]);
	return 0;
}

static void sdhci_sprd_phy_param_parse(struct sdhci_sprd_host *sprd_host,
				       struct device_node *np)
{
	u32 *p = sprd_host->phy_delay;
	int ret, i, index;
	u32 val[4];

	for (i = 0; i < ARRAY_SIZE(sdhci_sprd_phy_cfgs); i++) {
		ret = of_property_read_u32_array(np,
				sdhci_sprd_phy_cfgs[i].property, val, 4);
		if (ret)
			continue;

		index = sdhci_sprd_phy_cfgs[i].timing;
		p[index] = val[0] | (val[1] << 8) | (val[2] << 16) | (val[3] << 24);
	}
}

static const struct sdhci_pltfm_data sdhci_sprd_pdata = {
	.quirks = SDHCI_QUIRK_BROKEN_CARD_DETECTION |
		  SDHCI_QUIRK_DATA_TIMEOUT_USES_SDCLK,
	.quirks2 = SDHCI_QUIRK2_BROKEN_HS200 |
		   SDHCI_QUIRK2_USE_32BIT_BLK_CNT |
		   SDHCI_QUIRK2_PRESET_VALUE_BROKEN,
	.ops = &sdhci_sprd_ops,
};

static int sdhci_sprd_probe(struct platform_device *pdev)
{
	struct sdhci_host *host;
	struct sdhci_sprd_host *sprd_host;
	struct mmc_hsq *hsq;
	struct clk *clk;
	int ret = 0;

	host = sdhci_pltfm_init(pdev, &sdhci_sprd_pdata, sizeof(*sprd_host));
	if (IS_ERR(host))
		return PTR_ERR(host);

	host->dma_mask = DMA_BIT_MASK(64);
	pdev->dev.dma_mask = &host->dma_mask;
	host->mmc_host_ops.request = sdhci_sprd_request;
	host->mmc_host_ops.hs400_enhanced_strobe =
		sdhci_sprd_hs400_enhanced_strobe;
	host->mmc_host_ops.prepare_sd_hs_tuning =
		sdhci_sprd_prepare_sd_hs_cmd_tuning;
	host->mmc_host_ops.execute_sd_hs_tuning =
		sdhci_sprd_execute_sd_hs_data_tuning;

	/*
	 * We can not use the standard ops to change and detect the voltage
	 * signal for Spreadtrum SD host controller, since our voltage regulator
	 * for I/O is fixed in hardware, that means we do not need control
	 * the standard SD host controller to change the I/O voltage.
	 */
	host->mmc_host_ops.start_signal_voltage_switch =
		sdhci_sprd_voltage_switch;

	host->mmc->caps = MMC_CAP_SD_HIGHSPEED | MMC_CAP_MMC_HIGHSPEED |
		MMC_CAP_WAIT_WHILE_BUSY;

	ret = mmc_of_parse(host->mmc);
	if (ret)
		goto pltfm_free;

	if (!mmc_card_is_removable(host->mmc))
		host->mmc_host_ops.request_atomic = sdhci_sprd_request_atomic;
	else
		host->always_defer_done = true;

	sprd_host = TO_SPRD_HOST(host);
	sprd_host->r11p3_trial = of_device_is_compatible(pdev->dev.of_node,
						      "sprd,sdhci-r11p3");
	if (sprd_host->r11p3_trial) {
		host->mmc_host_ops.init_card = sdhci_sprd_trial_init_card;
		sprd_host->sdio_host = (host->mmc->caps2 & MMC_CAP2_NO_SD) && (host->mmc->caps2 & MMC_CAP2_NO_MMC);
		/* the SDIO host is set up with the UHS-I modes of its device tree; sdio_mode picks the one used */
		if (!sprd_host->sdio_host) {
			host->mmc->f_max = 52000000;
			host->mmc->caps &= ~(MMC_CAP_UHS | MMC_CAP_DDR);
		}
		host->mmc->caps2 &= ~(MMC_CAP2_HS200 | MMC_CAP2_HS400 |
					  MMC_CAP2_HS400_ES | MMC_CAP2_CQE |
					  MMC_CAP2_CQE_DCMD);
		/* Keep the AXI provider active until runtime PM is validated. */
		sprd_host->clk_ap_axi = devm_clk_get_optional_enabled(&pdev->dev, "ap_axi");
		if (IS_ERR(sprd_host->clk_ap_axi)) {
			ret = PTR_ERR(sprd_host->clk_ap_axi);
			goto pltfm_free;
		}
	}
	sdhci_sprd_phy_param_parse(sprd_host, pdev->dev.of_node);

	sprd_host->pinctrl = devm_pinctrl_get(&pdev->dev);
	if (!IS_ERR(sprd_host->pinctrl)) {
		sprd_host->pins_uhs =
			pinctrl_lookup_state(sprd_host->pinctrl, "state_uhs");
		if (IS_ERR(sprd_host->pins_uhs)) {
			ret = PTR_ERR(sprd_host->pins_uhs);
			goto pltfm_free;
		}

		sprd_host->pins_default =
			pinctrl_lookup_state(sprd_host->pinctrl, "default");
		if (IS_ERR(sprd_host->pins_default)) {
			ret = PTR_ERR(sprd_host->pins_default);
			goto pltfm_free;
		}
	}

	clk = devm_clk_get(&pdev->dev, "sdio");
	if (IS_ERR(clk)) {
		ret = PTR_ERR(clk);
		goto pltfm_free;
	}
	sprd_host->clk_sdio = clk;
	sprd_host->base_rate = clk_get_rate(sprd_host->clk_sdio);
	if (!sprd_host->base_rate)
		sprd_host->base_rate = SDHCI_SPRD_CLK_DEF_RATE;

	clk = devm_clk_get(&pdev->dev, "enable");
	if (IS_ERR(clk)) {
		ret = PTR_ERR(clk);
		goto pltfm_free;
	}
	sprd_host->clk_enable = clk;

	clk = devm_clk_get(&pdev->dev, "2x_enable");
	if (!IS_ERR(clk))
		sprd_host->clk_2x_enable = clk;

	ret = clk_prepare_enable(sprd_host->clk_sdio);
	if (ret)
		goto pltfm_free;

	ret = clk_prepare_enable(sprd_host->clk_enable);
	if (ret)
		goto clk_disable;

	ret = clk_prepare_enable(sprd_host->clk_2x_enable);
	if (ret)
		goto clk_disable2;

	if (sprd_host->r11p3_trial &&
	    of_property_read_bool(pdev->dev.of_node, "need_cache_convert")) {
		u32 args[2];
		struct regmap *map = syscon_regmap_lookup_by_phandle_args(
			pdev->dev.of_node, "sprd-cache-convert", 2, args);

		if (IS_ERR(map)) {
			ret = PTR_ERR(map);
			goto clk_2x_disable_probe;
		}
		ret = regmap_update_bits(map, args[0], args[1], args[1]);
		if (ret)
			goto clk_2x_disable_probe;
	}

	sdhci_sprd_init_config(host);
	host->version = sdhci_readw(host, SDHCI_HOST_VERSION);
	sprd_host->version = ((host->version & SDHCI_VENDOR_VER_MASK) >>
			       SDHCI_VENDOR_VER_SHIFT);

	pm_runtime_get_noresume(&pdev->dev);
	pm_runtime_set_active(&pdev->dev);
	pm_runtime_enable(&pdev->dev);
	pm_runtime_set_autosuspend_delay(&pdev->dev, 50);
	pm_runtime_use_autosuspend(&pdev->dev);
	pm_suspend_ignore_children(&pdev->dev, 1);

	sdhci_enable_v4_mode(host);

	/*
	 * Supply the existing CAPS, but clear the UHS-I modes. This
	 * will allow these modes to be specified only by device
	 * tree properties through mmc_of_parse().
	 */
	sdhci_read_caps(host);
	host->caps1 &= ~(SDHCI_SUPPORT_SDR50 | SDHCI_SUPPORT_SDR104 |
			 SDHCI_SUPPORT_DDR50);

	ret = mmc_regulator_get_supply(host->mmc);
	if (ret)
		goto pm_runtime_disable;

	ret = sdhci_setup_host(host);
	if (ret)
		goto pm_runtime_disable;

	sprd_host->flags = host->flags;

	hsq = devm_kzalloc(&pdev->dev, sizeof(*hsq), GFP_KERNEL);
	if (!hsq) {
		ret = -ENOMEM;
		goto err_cleanup_host;
	}

	ret = mmc_hsq_init(hsq, host->mmc);
	if (ret)
		goto err_cleanup_host;

	if (sprd_host->sdio_host && !sdio_mode_host) {
		host->mmc_host_ops.execute_tuning = sdhci_sprd_sdio_execute_tuning;
		sprd_host->sdio_f_uhs = sdhci_sprd_get_max_clock(host);
		sdio_mode_host = host;
		sdhci_sprd_sdio_apply(host);
	}

	ret = __sdhci_add_host(host);
	if (ret)
		goto err_cleanup_host;

	if (sprd_host->sdio_host) {
		dev_info(&pdev->dev, "R11P3 SDIO host: bus mode from sdhci_sprd.sdio_mode, UHS-I up to %u Hz\n", sprd_host->sdio_f_uhs);
	} else if (sprd_host->r11p3_trial) {
		dev_info(&pdev->dev, "R11P3 read-only bootstrap, max 52 MHz; CQE/ICE/HS200/400 disabled\n");
	} else {
		pm_runtime_mark_last_busy(&pdev->dev);
		pm_runtime_put_autosuspend(&pdev->dev);
	}

	return 0;

err_cleanup_host:
	if (sdio_mode_host == host)
		sdio_mode_host = NULL;
	sdhci_cleanup_host(host);

pm_runtime_disable:
	pm_runtime_put_noidle(&pdev->dev);
	pm_runtime_disable(&pdev->dev);
	pm_runtime_set_suspended(&pdev->dev);

clk_2x_disable_probe:
	clk_disable_unprepare(sprd_host->clk_2x_enable);

clk_disable2:
	clk_disable_unprepare(sprd_host->clk_enable);

clk_disable:
	clk_disable_unprepare(sprd_host->clk_sdio);

pltfm_free:
	sdhci_pltfm_free(pdev);
	return ret;
}

static void sdhci_sprd_remove(struct platform_device *pdev)
{
	struct sdhci_host *host = platform_get_drvdata(pdev);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);

	if (sdio_mode_host == host)
		sdio_mode_host = NULL;
	sdhci_remove_host(host, 0);

	clk_disable_unprepare(sprd_host->clk_sdio);
	clk_disable_unprepare(sprd_host->clk_enable);
	clk_disable_unprepare(sprd_host->clk_2x_enable);

	sdhci_pltfm_free(pdev);
}

static const struct of_device_id sdhci_sprd_of_match[] = {
	{ .compatible = "sprd,sdhci-r11", },
#ifdef CONFIG_U30PRO_SDHCI_R11P3_BOOTSTRAP
	{ .compatible = "sprd,sdhci-r11p3", },
#endif
	{ }
};
MODULE_DEVICE_TABLE(of, sdhci_sprd_of_match);

#ifdef CONFIG_PM
static int sdhci_sprd_runtime_suspend(struct device *dev)
{
	struct sdhci_host *host = dev_get_drvdata(dev);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);

	mmc_hsq_suspend(host->mmc);
	sdhci_runtime_suspend_host(host);

	clk_disable_unprepare(sprd_host->clk_sdio);
	clk_disable_unprepare(sprd_host->clk_enable);
	clk_disable_unprepare(sprd_host->clk_2x_enable);

	return 0;
}

static int sdhci_sprd_runtime_resume(struct device *dev)
{
	struct sdhci_host *host = dev_get_drvdata(dev);
	struct sdhci_sprd_host *sprd_host = TO_SPRD_HOST(host);
	int ret;

	ret = clk_prepare_enable(sprd_host->clk_2x_enable);
	if (ret)
		return ret;

	ret = clk_prepare_enable(sprd_host->clk_enable);
	if (ret)
		goto clk_2x_disable;

	ret = clk_prepare_enable(sprd_host->clk_sdio);
	if (ret)
		goto clk_disable;

	sdhci_runtime_resume_host(host, 1);
	mmc_hsq_resume(host->mmc);

	return 0;

clk_disable:
	clk_disable_unprepare(sprd_host->clk_enable);

clk_2x_disable:
	clk_disable_unprepare(sprd_host->clk_2x_enable);

	return ret;
}
#endif

static const struct dev_pm_ops sdhci_sprd_pm_ops = {
	SET_SYSTEM_SLEEP_PM_OPS(pm_runtime_force_suspend,
				pm_runtime_force_resume)
	SET_RUNTIME_PM_OPS(sdhci_sprd_runtime_suspend,
			   sdhci_sprd_runtime_resume, NULL)
};

static struct platform_driver sdhci_sprd_driver = {
	.probe = sdhci_sprd_probe,
	.remove_new = sdhci_sprd_remove,
	.driver = {
		.name = "sdhci_sprd_r11",
		.probe_type = PROBE_PREFER_ASYNCHRONOUS,
		.of_match_table = sdhci_sprd_of_match,
		.pm = &sdhci_sprd_pm_ops,
	},
};
module_platform_driver(sdhci_sprd_driver);

MODULE_DESCRIPTION("Spreadtrum sdio host controller r11 driver");
MODULE_LICENSE("GPL v2");
MODULE_ALIAS("platform:sdhci-sprd-r11");

#if IS_ENABLED(CONFIG_U30PRO_SDHCI_TRIAL_KUNIT_TEST)
#include "sdhci-sprd-trial-test.c"
#endif
