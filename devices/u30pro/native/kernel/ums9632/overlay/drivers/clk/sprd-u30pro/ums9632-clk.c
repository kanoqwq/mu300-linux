// SPDX-License-Identifier: GPL-2.0-only
/*
 * Unisoc UMS9632 clock driver
 *
 * Copyright (C) 2023 Unisoc, Inc.
 * Author: Zhifeng Tang <zhifeng.tang@unisoc.com>
 */

#include <linux/clk-provider.h>
#include <linux/err.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/slab.h>

#include <dt-bindings/clock/sprd,ums9632-clk.h>
#include <dt-bindings/reset/sprd,ums9632-reset.h>

#include "common.h"
#include "composite.h"
#include "div.h"
#include "gate.h"
#include "mux.h"
#include "pll.h"
#include "reset.h"

#define UMS9632_MUX_FLAG	\
	(CLK_GET_RATE_NOCACHE | CLK_SET_RATE_NO_REPARENT)

/* pll gate clock */
static CLK_FIXED_FACTOR_FW_NAME(clk_52m, "clk-52m", "ext-26m", 1, 2, 0);
static CLK_FIXED_FACTOR_FW_NAME(clk_13m, "clk-13m", "ext-26m", 2, 1, 0);
static CLK_FIXED_FACTOR_FW_NAME(clk_6m5, "clk-6m5", "ext-26m", 4, 1, 0);
static CLK_FIXED_FACTOR_FW_NAME(clk_4m33, "clk-4m33", "ext-26m", 6, 1, 0);
static CLK_FIXED_FACTOR_FW_NAME(clk_1m, "clk-1m", "ext-26m", 26, 1, 0);
static CLK_FIXED_FACTOR_FW_NAME(clk_250k, "clk-250k", "ext-26m", 104, 1, 0);
static CLK_FIXED_FACTOR_FW_NAME(rco_150m_75m, "rco-150m-75m", "rco-150m", 2, 1, 0);
static CLK_FIXED_FACTOR_FW_NAME(rco_150m_25m, "rco-150m-25m", "rco-150m", 6, 1, 0);
static CLK_FIXED_FACTOR_FW_NAME(rco_150m_4m05, "rco-150m-4m05", "rco-150m", 37, 1, 0);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(mplls_gate, "mplls-gate", "ext-26m", 0x9f4,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(mplll_gate, "mplll-gate", "ext-26m", 0x9f8,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(mpllm_gate, "mpllm-gate", "ext-26m", 0x9fc,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(rpll_gate, "rpll-gate", "ext-26m", 0xa0c,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(dpll0_gate, "dpll0-gate", "ext-26m", 0xa14,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(dpll1_gate, "dpll1-gate", "ext-26m", 0xa18,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(dpll2_gate, "dpll2-gate", "ext-26m", 0xa1c,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(gpll_gate, "gpll-gate", "ext-26m", 0xa20,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(phyr8pll_gate, "phyr8pll-gate", "ext-26m", 0xa30,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(tgpll_gate, "tgpll-gate", "ext-26m", 0xa40,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(v4nrpll_gate, "v4nrpll-gate", "ext-26m", 0xa44,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(audpll_gate, "audpll-gate", "ext-26m", 0xa48,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);
static SPRD_PLL_SC_GATE_CLK_FW_NAME(pixelpll_gate, "pixelpll-gate", "ext-26m", 0xa5c,
			    0x1000, BIT(2), CLK_IGNORE_UNUSED, 0, 240);

static struct sprd_clk_common *ums9632_pmu_gate_clks[] = {
	/* address base is 0x64910000 */
	&mplls_gate.common,
	&mplll_gate.common,
	&mpllm_gate.common,
	&rpll_gate.common,
	&dpll0_gate.common,
	&dpll1_gate.common,
	&dpll2_gate.common,
	&gpll_gate.common,
	&phyr8pll_gate.common,
	&tgpll_gate.common,
	&v4nrpll_gate.common,
	&audpll_gate.common,
	&pixelpll_gate.common,
};

static struct clk_hw_onecell_data ums9632_pmu_gate_hws = {
	.hws	= {
		[CLK_52M]		= &clk_52m.hw,
		[CLK_13M]		= &clk_13m.hw,
		[CLK_6M5]		= &clk_6m5.hw,
		[CLK_4M33]		= &clk_4m33.hw,
		[CLK_1M]		= &clk_1m.hw,
		[CLK_250K]		= &clk_250k.hw,
		[CLK_RCO_150M_75M]	= &rco_150m_75m.hw,
		[CLK_RCO_150M_25M]	= &rco_150m_25m.hw,
		[CLK_RCO_150M_4M05]	= &rco_150m_4m05.hw,
		[CLK_MPLLS_GATE]	= &mplls_gate.common.hw,
		[CLK_MPLLL_GATE]	= &mplll_gate.common.hw,
		[CLK_MPLLM_GATE]	= &mpllm_gate.common.hw,
		[CLK_RPLL_GATE]		= &rpll_gate.common.hw,
		[CLK_DPLL0_GATE]	= &dpll0_gate.common.hw,
		[CLK_DPLL1_GATE]	= &dpll1_gate.common.hw,
		[CLK_DPLL2_GATE]	= &dpll2_gate.common.hw,
		[CLK_GPLL_GATE]		= &gpll_gate.common.hw,
		[CLK_PHYR8PLL_GATE]	= &phyr8pll_gate.common.hw,
		[CLK_TGPLL_GATE]	= &tgpll_gate.common.hw,
		[CLK_V4NRPLL_GATE]	= &v4nrpll_gate.common.hw,
		[CLK_AUDPLL_GATE]	= &audpll_gate.common.hw,
		[CLK_PIXELPLL_GATE]	= &pixelpll_gate.common.hw,
	},
	.num = CLK_PMU_GATE_NUM,
};

static struct sprd_reset_map ums9632_pmu_apb_resets[] = {
	[RESET_PMU_APB_AP_SOFT_RST]		= { 0x0b98, BIT(0), true, 0x1000 },
	[RESET_PMU_APB_APCPU_TOP_SOFT_RST]	= { 0x0b98, BIT(1), true, 0x1000 },
	[RESET_PMU_APB_GPU_SOFT_RST]		= { 0x0b98, BIT(2), true, 0x1000 },
	[RESET_PMU_APB_CAMERA_SOFT_RST]		= { 0x0b98, BIT(3), true, 0x1000 },
	[RESET_PMU_APB_DPU_VSP_SOFT_RST]	= { 0x0b98, BIT(4), true, 0x1000 },
	[RESET_PMU_APB_PS_CP_SOFT_RST]		= { 0x0b98, BIT(11), true, 0x1000 },
	[RESET_PMU_APB_PHY_CP_SOFT_RST]		= { 0x0b98, BIT(14), true, 0x1000 },
	[RESET_PMU_APB_AUDIO_SOFT_RST]		= { 0x0b98, BIT(22), true, 0x1000 },
	[RESET_PMU_APB_CS_SOFT_RST]		= { 0x0b98, BIT(29), true, 0x1000 },
	[RESET_PMU_APB_AON_SOFT_RST]		= { 0x0b98, BIT(30), true, 0x1000 },
	[RESET_PMU_APB_SP_SOFT_RST]		= { 0x0b9c, BIT(0), true, 0x1000 },
	[RESET_PMU_APB_CH_SOFT_RST]		= { 0x0b9c, BIT(1), true, 0x1000 },
	[RESET_PMU_APB_PUB_SOFT_RST]		= { 0x0b9c, BIT(4), true, 0x1000 },
};

static struct sprd_clk_desc ums9632_pmu_gate_desc = {
	.clk_clks	= ums9632_pmu_gate_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_pmu_gate_clks),
	.hw_clks	= &ums9632_pmu_gate_hws,
	.resets		= ums9632_pmu_apb_resets,
	.num_resets	= ARRAY_SIZE(ums9632_pmu_apb_resets),
};

/* pll clock at g1 */
static struct freq_table rpll_ftable[] = {
	{ .ibias = 1, .max_freq = 2000000000ULL, .vco_sel = 0 },
	{ .ibias = 2, .max_freq = 2800000000ULL, .vco_sel = 0 },
	{ .ibias = 3, .max_freq = 3200000000ULL, .vco_sel = 0 },
	{ .ibias = INVALID_IBIAS, .max_freq = INVALID_MAX_FREQ, .vco_sel = INVALID_VCO_SEL },
};

static struct clk_bit_field f_rpll[PLL_FACT_MAX] = {
	{ .shift = 18,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 1 },	/* div_s	*/
	{ .shift = 1,	.width = 1 },	/* mod_en	*/
	{ .shift = 2,	.width = 1 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 3,	.width = 4 },	/* icp		*/
	{ .shift = 7,	.width = 11 },	/* n		*/
	{ .shift = 55,	.width = 8 },	/* nint		*/
	{ .shift = 32,	.width = 23 },	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 66,	.width = 2 },	/* postdiv	*/
	{ .shift = 0,	.width = 0 },	/* refdiv	*/
	{ .shift = 0,	.width = 0 },	/* vco_sel	*/
};

static SPRD_PLL_FW_NAME(rpll, "rpll", "ext-26m", 0x10,
				   3, rpll_ftable, f_rpll, 240,
				   1000, 1000, 1, 1560000000);
static CLK_FIXED_FACTOR_HW(rpll_26m, "rpll-26m", &rpll.common.hw, 30, 1, 0);
static CLK_FIXED_FACTOR_HW(rpll_390m, "rpll-390m", &rpll.common.hw, 2, 1, 0);

static struct sprd_clk_common *ums9632_g1_pll_clks[] = {
	/* address base is 0x64304000 */
	&rpll.common,
};

static struct clk_hw_onecell_data ums9632_g1_pll_hws = {
	.hws    = {
		[CLK_RPLL]	= &rpll.common.hw,
		[CLK_RPLL_26M]	= &rpll_26m.hw,
		[CLK_RPLL_390M]	= &rpll_390m.hw,
	},
	.num    = CLK_ANLG_PHY_G1_NUM,
};

static struct sprd_clk_desc ums9632_g1_pll_desc = {
	.clk_clks	= ums9632_g1_pll_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_g1_pll_clks),
	.hw_clks	= &ums9632_g1_pll_hws,
};

/* pll at g1l */
static struct freq_table dpll_ftable[] = {
	{ .ibias = 1, .max_freq = 2000000000ULL, .vco_sel = 0 },
	{ .ibias = 2, .max_freq = 2800000000ULL, .vco_sel = 0 },
	{ .ibias = 3, .max_freq = 3200000000ULL, .vco_sel = 0 },
	{ .ibias = INVALID_IBIAS, .max_freq = INVALID_MAX_FREQ, .vco_sel = INVALID_VCO_SEL },
};

static struct clk_bit_field f_dpll[PLL_FACT_MAX] = {
	{ .shift = 18,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 1 },	/* div_s	*/
	{ .shift = 1,	.width = 1 },	/* mod_en	*/
	{ .shift = 2,	.width = 1 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 3,	.width = 4 },	/* icp		*/
	{ .shift = 7,	.width = 11 },	/* n		*/
	{ .shift = 55,	.width = 8 },	/* nint		*/
	{ .shift = 32,	.width = 23 },	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 66,	.width = 4 },	/* postdiv	*/
	{ .shift = 0,	.width = 0 },	/* refdiv	*/
	{ .shift = 0,	.width = 0 },	/* vco_sel	*/
};

static SPRD_PLL_FW_NAME(dpll0, "dpll0", "ext-26m", 0x4,
				   3, dpll_ftable, f_dpll, 240,
				   1000, 1000, 1, 1500000000);
static SPRD_PLL_FW_NAME(dpll1, "dpll1", "ext-26m", 0x24,
				   3, dpll_ftable, f_dpll, 240,
				   1000, 1000, 1, 1500000000);
static SPRD_PLL_FW_NAME(dpll2, "dpll2", "ext-26m", 0x44,
				   3, dpll_ftable, f_dpll, 240,
				   1000, 1000, 1, 1500000000);

static struct sprd_clk_common *ums9632_g1l_pll_clks[] = {
	/* address base is 0x64308000 */
	&dpll0.common,
	&dpll1.common,
	&dpll2.common,
};

static struct clk_hw_onecell_data ums9632_g1l_pll_hws = {
	.hws    = {
		[CLK_DPLL0]	= &dpll0.common.hw,
		[CLK_DPLL1]	= &dpll1.common.hw,
		[CLK_DPLL2]	= &dpll2.common.hw,
	},
	.num    = CLK_ANLG_PHY_G1L_NUM,
};

static struct sprd_clk_desc ums9632_g1l_pll_desc = {
	.clk_clks	= ums9632_g1l_pll_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_g1l_pll_clks),
	.hw_clks	= &ums9632_g1l_pll_hws,
};

/* pll at g5l */
static struct freq_table tgpll_ftable[] = {
	{ .ibias = 1, .max_freq = 2000000000ULL, .vco_sel = 0 },
	{ .ibias = 2, .max_freq = 2800000000ULL, .vco_sel = 0 },
	{ .ibias = 3, .max_freq = 3200000000ULL, .vco_sel = 0 },
	{ .ibias = INVALID_IBIAS, .max_freq = INVALID_MAX_FREQ, .vco_sel = INVALID_VCO_SEL },
};

static struct clk_bit_field f_tgpll[PLL_FACT_MAX] = {
	{ .shift = 17,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 1 },	/* div_s	*/
	{ .shift = 1,	.width = 1 },	/* mod_en	*/
	{ .shift = 2,	.width = 1 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 3,	.width = 3 },	/* icp		*/
	{ .shift = 6,	.width = 11 },	/* n		*/
	{ .shift = 32,	.width = 7 },	/* nint		*/
	{ .shift = 39,	.width = 23 },	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 68,	.width = 1 },	/* postdiv	*/
	{ .shift = 0,	.width = 0 },	/* refdiv	*/
	{ .shift = 0,	.width = 0 },	/* vco_sel	*/
};

static SPRD_PLL_FW_NAME(tgpll, "tgpll", "ext-26m", 0x4,
				   3, tgpll_ftable, f_tgpll, 240,
				   1000, 1000, 1, 1600000000);
static CLK_FIXED_FACTOR_HW(tgpll_48m, "tgpll-48m", &tgpll.common.hw, 32, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_51m2, "tgpll-51m2", &tgpll.common.hw, 30, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_64m, "tgpll-64m", &tgpll.common.hw, 24, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_76m8, "tgpll-76m8", &tgpll.common.hw, 20, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_96m, "tgpll-96m", &tgpll.common.hw, 16, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_128m, "tgpll-128m", &tgpll.common.hw, 12, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_153m6, "tgpll-153m6", &tgpll.common.hw, 10, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_192m, "tgpll-192m", &tgpll.common.hw, 8, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_256m, "tgpll-256m", &tgpll.common.hw, 6, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_307m2, "tgpll-307m2", &tgpll.common.hw, 5, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_384m, "tgpll-384m", &tgpll.common.hw, 4, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_512m, "tgpll-512m", &tgpll.common.hw, 3, 1, 0);
static CLK_FIXED_FACTOR_HW(tgpll_768m, "tgpll-768m", &tgpll.common.hw, 2, 1, 0);

static struct clk_bit_field f_v4nrpll[PLL_FACT_MAX] = {
	{ .shift = 17,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 1 },	/* div_s	*/
	{ .shift = 1,	.width = 1 },	/* mod_en	*/
	{ .shift = 2,	.width = 1 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 3,	.width = 3 },	/* icp		*/
	{ .shift = 6,	.width = 11 },	/* n		*/
	{ .shift = 55,	.width = 7 },	/* nint		*/
	{ .shift = 32,	.width = 23 },	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 81,	.width = 1 },	/* postdiv	*/
	{ .shift = 0,	.width = 0 },	/* refdiv	*/
	{ .shift = 0,	.width = 0 },	/* vco_sel	*/
};

#define v4nrpll_ftable tgpll_ftable
static SPRD_PLL_FW_NAME(v4nrpll, "v4nrpll", "ext-26m", 0x28,
				   3, v4nrpll_ftable, f_v4nrpll, 240,
				   1000, 1000, 1, 1600000000);
static CLK_FIXED_FACTOR_HW(v4nrpll_409m6, "v4nrpll-409m6", &v4nrpll.common.hw, 6, 1, 0);
static CLK_FIXED_FACTOR_HW(v4nrpll_614m4, "v4nrpll-614m4", &v4nrpll.common.hw, 4, 1, 0);
static CLK_FIXED_FACTOR_HW(v4nrpll_1228m8, "v4nrpll-1228m8", &v4nrpll.common.hw, 2, 1, 0);

static struct clk_bit_field f_gpll[PLL_FACT_MAX] = {
	{ .shift = 14,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 0 },	/* div_s	*/
	{ .shift = 0,	.width = 0 },	/* mod_en	*/
	{ .shift = 0,	.width = 0 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 0,	.width = 3 },	/* icp		*/
	{ .shift = 3,	.width = 11 },	/* n		*/
	{ .shift = 0,	.width = 0 },	/* nint		*/
	{ .shift = 0,	.width = 0 },	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 35,	.width = 1 },	/* postdiv	*/
	{ .shift = 0,	.width = 0 },	/* refdiv	*/
	{ .shift = 0,	.width = 0 },	/* vco_sel	*/
};

#define gpll_ftable tgpll_ftable
static SPRD_PLL_FW_NAME(gpll, "gpll", "ext-26m", 0x44,
				   3, gpll_ftable, f_gpll, 240,
				   1000, 1000, 1, 1600000000);
static CLK_FIXED_FACTOR_HW(gpll_760m, "gpll-760m", &gpll.common.hw, 5, 2, 0);
static CLK_FIXED_FACTOR_HW(gpll_950m, "gpll-950m", &gpll.common.hw, 2, 1, 0);

static struct sprd_clk_common *ums9632_g5l_pll_clks[] = {
	/* address base is 0x64324000 */
	&tgpll.common,
	&v4nrpll.common,
	&gpll.common,
};

static struct clk_hw_onecell_data ums9632_g5l_pll_hws = {
	.hws    = {
		[CLK_TGPLL]		= &tgpll.common.hw,
		[CLK_TGPLL_48M]		= &tgpll_48m.hw,
		[CLK_TGPLL_51M2]	= &tgpll_51m2.hw,
		[CLK_TGPLL_64M]		= &tgpll_64m.hw,
		[CLK_TGPLL_76M8]	= &tgpll_76m8.hw,
		[CLK_TGPLL_96M]		= &tgpll_96m.hw,
		[CLK_TGPLL_128M]	= &tgpll_128m.hw,
		[CLK_TGPLL_153M6]	= &tgpll_153m6.hw,
		[CLK_TGPLL_192M]	= &tgpll_192m.hw,
		[CLK_TGPLL_256M]	= &tgpll_256m.hw,
		[CLK_TGPLL_307M2]	= &tgpll_307m2.hw,
		[CLK_TGPLL_384M]	= &tgpll_384m.hw,
		[CLK_TGPLL_512M]	= &tgpll_512m.hw,
		[CLK_TGPLL_768M]	= &tgpll_768m.hw,
		[CLK_V4NRPLL]		= &v4nrpll.common.hw,
		[CLK_V4NRPLL_409M6]	= &v4nrpll_409m6.hw,
		[CLK_V4NRPLL_614M4]	= &v4nrpll_614m4.hw,
		[CLK_V4NRPLL_1228M8]	= &v4nrpll_1228m8.hw,
		[CLK_GPLL]		= &gpll.common.hw,
		[CLK_GPLL_760M]		= &gpll_760m.hw,
		[CLK_GPLL_950M]		= &gpll_950m.hw,
	},
	.num    = CLK_ANLG_PHY_G5L_NUM,
};

static struct sprd_clk_desc ums9632_g5l_pll_desc = {
	.clk_clks	= ums9632_g5l_pll_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_g5l_pll_clks),
	.hw_clks	= &ums9632_g5l_pll_hws,
};

/* pll at g5r */
static struct freq_table audpll_ftable[] = {
	{ .ibias = 1, .max_freq = 2000000000ULL, .vco_sel = 0 },
	{ .ibias = 2, .max_freq = 2800000000ULL, .vco_sel = 0 },
	{ .ibias = 3, .max_freq = 3200000000ULL, .vco_sel = 0 },
	{ .ibias = INVALID_IBIAS, .max_freq = INVALID_MAX_FREQ, .vco_sel = INVALID_VCO_SEL },
};

static struct clk_bit_field f_audpll[PLL_FACT_MAX] = {
	{ .shift = 17,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 1 },	/* div_s	*/
	{ .shift = 1,	.width = 1 },	/* mod_en	*/
	{ .shift = 2,	.width = 1 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 3,	.width = 3 },	/* icp		*/
	{ .shift = 6,	.width = 11 },	/* n		*/
	{ .shift = 55,	.width = 7 },	/* nint		*/
	{ .shift = 32,	.width = 23 },	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 67,	.width = 1 },	/* postdiv	*/
	{ .shift = 0,	.width = 0 },	/* refdiv	*/
	{ .shift = 0,	.width = 0 },	/* vco_sel	*/
};

static SPRD_PLL_FW_NAME(audpll, "audpll", "ext-26m", 0x0,
				   3, audpll_ftable, f_audpll, 240,
				   1000, 1000, 1, 1600000000);

static struct clk_bit_field f_phyr8pll[PLL_FACT_MAX] = {
	{ .shift = 14,  .width = 1 },   /* lock_done	*/
	{ .shift = 0,   .width = 0 },   /* div_s	*/
	{ .shift = 0,   .width = 0 },   /* mod_en	*/
	{ .shift = 0,   .width = 0 },   /* sdm_en	*/
	{ .shift = 0,   .width = 0 },   /* refin	*/
	{ .shift = 0,   .width = 3 },   /* icp		*/
	{ .shift = 3,   .width = 11 },  /* n		*/
	{ .shift = 0,   .width = 0 },   /* nint		*/
	{ .shift = 0,   .width = 0 },   /* kint		*/
	{ .shift = 0,   .width = 0 },   /* prediv	*/
	{ .shift = 35,  .width = 1 },   /* postdiv	*/
	{ .shift = 0,   .width = 0 },   /* refdiv	*/
	{ .shift = 0,   .width = 0 },   /* vco_sel	*/
};

#define phyr8pll_ftable audpll_ftable
static SPRD_PLL_FW_NAME(phyr8pll, "phyr8pll", "ext-26m", 0x28,
				   3, phyr8pll_ftable, f_phyr8pll, 240,
				   1000, 1000, 1, 1600000000);

#define pixelpll_ftable audpll_ftable
#define f_pixelpll f_audpll
static SPRD_PLL_FW_NAME(pixelpll, "pixelpll", "ext-26m", 0x40,
				   3, pixelpll_ftable, f_pixelpll, 240,
				   1000, 1000, 1, 1600000000);
static CLK_FIXED_FACTOR_HW(pixelpll_200m, "pixelpll-200m", &pixelpll.common.hw, 16, 1, 0);
static CLK_FIXED_FACTOR_HW(pixelpll_400m, "pixelpll-400m", &pixelpll.common.hw, 8, 1, 0);
static CLK_FIXED_FACTOR_HW(pixelpll_420m, "pixelpll-420m", &pixelpll.common.hw, 4, 1, 0);

static struct sprd_clk_common *ums9632_g5r_pll_clks[] = {
	/* address base is 0x64320000 */
	&audpll.common,
	&phyr8pll.common,
	&pixelpll.common,
};

static struct clk_hw_onecell_data ums9632_g5r_pll_hws = {
	.hws    = {
		[CLK_AUDPLL]		= &audpll.common.hw,
		[CLK_PHYR8PLL]		= &phyr8pll.common.hw,
		[CLK_PIXELPLL]		= &pixelpll.common.hw,
		[CLK_PIXELPLL_200M]	= &pixelpll_200m.hw,
		[CLK_PIXELPLL_400M]	= &pixelpll_400m.hw,
		[CLK_PIXELPLL_420M]	= &pixelpll_420m.hw,
	},
	.num    = CLK_ANLG_PHY_G5R_NUM,
};

static struct sprd_clk_desc ums9632_g5r_pll_desc = {
	.clk_clks	= ums9632_g5r_pll_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_g5r_pll_clks),
	.hw_clks	= &ums9632_g5r_pll_hws,
};

/* pll at g9 */
static struct freq_table mpllm_ftable[] = {
	{ .ibias = 7,  .max_freq = 2000000000ULL,  .vco_sel = 1 },
	{ .ibias = 10, .max_freq = 3000000000ULL,  .vco_sel = 0 },
	{ .ibias = 12, .max_freq = 3200000000ULL,  .vco_sel = 0 },
	{ .ibias = INVALID_IBIAS, .max_freq = INVALID_MAX_FREQ, .vco_sel = INVALID_VCO_SEL },
};

static struct clk_bit_field f_mpllm[PLL_FACT_MAX] = {
	{ .shift = 15,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 0 },	/* div_s	*/
	{ .shift = 0,	.width = 0 },	/* mod_en	*/
	{ .shift = 0,	.width = 0 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 0,	.width = 4 },	/* icp		*/
	{ .shift = 4,	.width = 11 },	/* n		*/
	{ .shift = 0,	.width = 0 },	/* nint		*/
	{ .shift = 0,	.width = 0 },	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 68,	.width = 4 },	/* postdiv	*/
	{ .shift = 67,	.width = 1 },	/* refdiv	*/
	{ .shift = 96,	.width = 1 },	/* vco_sel	*/
};

static SPRD_PLL_FW_NAME(mpllm, "mpllm", "ext-26m", 0x0,
				   4, mpllm_ftable, f_mpllm, 240,
				   1000, 1000, 1, 1200000000);

static struct sprd_clk_common *ums9632_g9_pll_clks[] = {
	/* address base is 0x64330000 */
	&mpllm.common,
};

static struct clk_hw_onecell_data ums9632_g9_pll_hws = {
	.hws    = {
		[CLK_MPLLM]	= &mpllm.common.hw,
	},
	.num    = CLK_ANLG_PHY_G9_NUM,
};

static struct sprd_clk_desc ums9632_g9_pll_desc = {
	.clk_clks	= ums9632_g9_pll_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_g9_pll_clks),
	.hw_clks	= &ums9632_g9_pll_hws,
};

/* pll at g10 */
#define mplll_ftable mpllm_ftable
#define f_mplll f_mpllm
static SPRD_PLL_FW_NAME(mplll, "mplll", "ext-26m", 0x0,
				   4, mplll_ftable, f_mplll, 240,
				   1000, 1000, 1, 1200000000);

static struct clk_bit_field f_mplls[PLL_FACT_MAX] = {
	{ .shift = 15,	.width = 1 },	/* lock_done	*/
	{ .shift = 0,	.width = 0 },	/* div_s	*/
	{ .shift = 0,	.width = 0 },	/* mod_en	*/
	{ .shift = 0,	.width = 0 },	/* sdm_en	*/
	{ .shift = 0,	.width = 0 },	/* refin	*/
	{ .shift = 0,	.width = 4 },	/* icp		*/
	{ .shift = 4,	.width = 11 },	/* n		*/
	{ .shift = 0,	.width = 0 },	/* nint		*/
	{ .shift = 0,	.width = 0},	/* kint		*/
	{ .shift = 0,	.width = 0 },	/* prediv	*/
	{ .shift = 67,	.width = 4 },	/* postdiv	*/
	{ .shift = 71,	.width = 1 },	/* refdiv	*/
	{ .shift = 96,	.width = 1 },	/* vco_sel	*/
};

#define mplls_ftable mpllm_ftable
static SPRD_PLL_FW_NAME(mplls, "mplls", "ext-26m", 0x20,
				   4, mplls_ftable, f_mplls, 240,
				   1000, 1000, 1, 1200000000);

static struct sprd_clk_common *ums9632_g10_pll_clks[] = {
	/* address base is 0x64334000 */
	&mplll.common,
	&mplls.common,
};

static struct clk_hw_onecell_data ums9632_g10_pll_hws = {
	.hws    = {
		[CLK_MPLLL]	= &mplll.common.hw,
		[CLK_MPLLS]	= &mplls.common.hw,
	},
	.num    = CLK_ANLG_PHY_G10_NUM,
};

static struct sprd_clk_desc ums9632_g10_pll_desc = {
	.clk_clks	= ums9632_g10_pll_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_g10_pll_clks),
	.hw_clks	= &ums9632_g10_pll_hws,
};

/* ap apb gates */
static SPRD_SC_GATE_CLK_FW_NAME(iis0_eb, "iis0-eb", "ext-26m", 0x0,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(iis1_eb, "iis1-eb", "ext-26m", 0x0,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi0_eb, "spi0-eb", "ext-26m", 0x0,
			0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi1_eb, "spi1-eb", "ext-26m", 0x0,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi2_eb, "spi2-eb", "ext-26m", 0x0,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi3_eb, "spi3-eb", "ext-26m", 0x0,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(uart0_eb, "uart0-eb", "ext-26m", 0x0,
			0x1000, BIT(14), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(uart1_eb, "uart1-eb", "ext-26m", 0x0,
			0x1000, BIT(15), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(uart2_eb, "uart2-eb", "ext-26m", 0x0,
			0x1000, BIT(16), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(uart3_eb, "uart3-eb", "ext-26m", 0x0,
			0x1000, BIT(17), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi0_lfin_eb, "spi0-lfin-eb", "ext-26m", 0x0,
			0x1000, BIT(18), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi1_lfin_eb, "spi1-lfin-eb", "ext-26m", 0x0,
			0x1000, BIT(19), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi2_lfin_eb, "spi2-lfin-eb", "ext-26m", 0x0,
			0x1000, BIT(20), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(spi3_lfin_eb, "spi3-lfin-eb", "ext-26m", 0x0,
			0x1000, BIT(21), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ce_sec_eb, "ce-sec-eb", "ext-26m", 0x0,
			0x1000, BIT(30), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ce_pub_eb, "ce-pub-eb", "ext-26m", 0x0,
			0x1000, BIT(31), CLK_IGNORE_UNUSED, 0);

static struct sprd_clk_common *ums9632_apapb_gate_clks[] = {
	/* address base is 0x20100000 */
	&iis0_eb.common,
	&iis1_eb.common,
	&spi0_eb.common,
	&spi1_eb.common,
	&spi2_eb.common,
	&spi3_eb.common,
	&uart0_eb.common,
	&uart1_eb.common,
	&uart2_eb.common,
	&uart3_eb.common,
	&spi0_lfin_eb.common,
	&spi1_lfin_eb.common,
	&spi2_lfin_eb.common,
	&spi3_lfin_eb.common,
	&ce_sec_eb.common,
	&ce_pub_eb.common,
};

static struct clk_hw_onecell_data ums9632_apapb_gate_hws = {
	.hws	= {
		[CLK_IIS0_EB]		= &iis0_eb.common.hw,
		[CLK_IIS1_EB]		= &iis1_eb.common.hw,
		[CLK_SPI0_EB]		= &spi0_eb.common.hw,
		[CLK_SPI1_EB]		= &spi1_eb.common.hw,
		[CLK_SPI2_EB]		= &spi2_eb.common.hw,
		[CLK_SPI3_EB]		= &spi3_eb.common.hw,
		[CLK_UART0_EB]		= &uart0_eb.common.hw,
		[CLK_UART1_EB]		= &uart1_eb.common.hw,
		[CLK_UART2_EB]		= &uart2_eb.common.hw,
		[CLK_UART3_EB]		= &uart3_eb.common.hw,
		[CLK_SPI0_LFIN_EB]	= &spi0_lfin_eb.common.hw,
		[CLK_SPI1_LFIN_EB]	= &spi1_lfin_eb.common.hw,
		[CLK_SPI2_LFIN_EB]	= &spi2_lfin_eb.common.hw,
		[CLK_SPI3_LFIN_EB]	= &spi3_lfin_eb.common.hw,
		[CLK_CE_SEC_EB]		= &ce_sec_eb.common.hw,
		[CLK_CE_PUB_EB]		= &ce_pub_eb.common.hw,
	},
	.num	= CLK_AP_APB_GATE_NUM,
};

static struct sprd_reset_map ums9632_ap_apb_resets[] = {
	[RESET_AP_APB_IIS0_SOFT_RST]	= { 0x0004, BIT(1), true, 0x1000 },
	[RESET_AP_APB_IIS1_SOFT_RST]	= { 0x0004, BIT(2), true, 0x1000 },
	[RESET_AP_APB_SPI0_SOFT_RST]	= { 0x0004, BIT(4), true, 0x1000 },
	[RESET_AP_APB_SPI1_SOFT_RST]	= { 0x0004, BIT(5), true, 0x1000 },
	[RESET_AP_APB_SPI2_SOFT_RST]	= { 0x0004, BIT(6), true, 0x1000 },
	[RESET_AP_APB_SPI3_SOFT_RST]	= { 0x0004, BIT(7), true, 0x1000 },
	[RESET_AP_APB_UART0_SOFT_RST]	= { 0x0004, BIT(13), true, 0x1000 },
	[RESET_AP_APB_UART1_SOFT_RST]	= { 0x0004, BIT(14), true, 0x1000 },
	[RESET_AP_APB_UART2_SOFT_RST]	= { 0x0004, BIT(15), true, 0x1000 },
	[RESET_AP_APB_UART3_SOFT_RST]	= { 0x0004, BIT(16), true, 0x1000 },
	[RESET_AP_APB_CE_SEC_SOFT_RST]	= { 0x0004, BIT(20), true, 0x1000 },
	[RESET_AP_APB_CE_PUB_SOFT_RST]	= { 0x0004, BIT(21), true, 0x1000 },
	[RESET_AP_APB_AP_DVFS_SOFT_RST]	= { 0x0004, BIT(22), true, 0x1000 },
};

static struct sprd_clk_desc ums9632_apapb_gate_desc = {
	.clk_clks	= ums9632_apapb_gate_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_apapb_gate_clks),
	.hw_clks	= &ums9632_apapb_gate_hws,
	.resets		= ums9632_ap_apb_resets,
	.num_resets	= ARRAY_SIZE(ums9632_ap_apb_resets),
};

/* ap ahb gates */
static SPRD_SC_GATE_CLK_FW_NAME(sdio0_eb, "sdio0-eb", "ext-26m", 0x0,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(sdio1_eb, "sdio1-eb", "ext-26m", 0x0,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ice_eb, "ice-eb", "ext-26m", 0x0,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(emmc_eb, "emmc-eb", "ext-26m", 0x0,
			0x1000, BIT(3), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(dma_pub_eb, "dma-pub-eb", "ext-26m", 0x0,
			0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(dma_eb, "dma-eb", "ext-26m", 0x0,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_eb, "ufs-eb", "ext-26m", 0x0,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ckg_eb, "ckg-eb", "ext-26m", 0x0,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(busmon_clk_eb, "busmon-clk-eb", "ext-26m", 0x0,
			0x1000, BIT(8), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap2emc_eb, "ap2emc-eb", "ext-26m", 0x0,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c0_eb, "i2c0-eb", "ext-26m", 0x0,
			0x1000, BIT(10), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c1_eb, "i2c1-eb", "ext-26m", 0x0,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c2_eb, "i2c2-eb", "ext-26m", 0x0,
			0x1000, BIT(12), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c3_eb, "i2c3-eb", "ext-26m", 0x0,
			0x1000, BIT(13), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c4_eb, "i2c4-eb", "ext-26m", 0x0,
			0x1000, BIT(14), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c5_eb, "i2c5-eb", "ext-26m", 0x0,
			0x1000, BIT(15), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c6_eb, "i2c6-eb", "ext-26m", 0x0,
			0x1000, BIT(16), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c7_eb, "i2c7-eb", "ext-26m", 0x0,
			0x1000, BIT(17), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c8_eb, "i2c8-eb", "ext-26m", 0x0,
			0x1000, BIT(18), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(i2c9_eb, "i2c9-eb", "ext-26m", 0x0,
			0x1000, BIT(19), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(noc_ser_eb, "noc_ser-eb", "ext-26m", 0x0,
			0x1000, BIT(30), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_tx_eb, "ufs-tx-eb", "ext-26m", 0x1c,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_rx_0_eb, "ufs-rx-0-eb", "ext-26m", 0x1c,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_rx_1_eb, "ufs-rx-1-eb", "ext-26m", 0x1c,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_ao_apb_eb, "ufs-ao-apb-eb", "ext-26m", 0x1c,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_unipro_apb_eb, "ufs-unipro-apb-eb", "ext-26m", 0x1c,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_utp_apb_eb, "ufs-utp-apb-eb", "ext-26m", 0x1c,
			0x1000, BIT(8), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ufs_hci_apb_eb, "ufs-hci-apb-eb", "ext-26m", 0x1c,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);

static struct sprd_clk_common *ums9632_apahb_gate_clks[] = {
	/* address base is 0x20000000 */
	&sdio0_eb.common,
	&sdio1_eb.common,
	&ice_eb.common,
	&emmc_eb.common,
	&dma_pub_eb.common,
	&dma_eb.common,
	&ufs_eb.common,
	&ckg_eb.common,
	&busmon_clk_eb.common,
	&ap2emc_eb.common,
	&i2c0_eb.common,
	&i2c1_eb.common,
	&i2c2_eb.common,
	&i2c3_eb.common,
	&i2c4_eb.common,
	&i2c5_eb.common,
	&i2c6_eb.common,
	&i2c7_eb.common,
	&i2c8_eb.common,
	&i2c9_eb.common,
	&noc_ser_eb.common,
	&ufs_tx_eb.common,
	&ufs_rx_0_eb.common,
	&ufs_rx_1_eb.common,
	&ufs_ao_apb_eb.common,
	&ufs_unipro_apb_eb.common,
	&ufs_utp_apb_eb.common,
	&ufs_hci_apb_eb.common,
};

static struct clk_hw_onecell_data ums9632_apahb_gate_hws = {
	.hws	= {
		[CLK_SDIO0_EB]		= &sdio0_eb.common.hw,
		[CLK_SDIO1_EB]		= &sdio1_eb.common.hw,
		[CLK_ICE_EB]		= &ice_eb.common.hw,
		[CLK_EMMC_EB]		= &emmc_eb.common.hw,
		[CLK_DMA_PUB_EB]	= &dma_pub_eb.common.hw,
		[CLK_DMA_EB]		= &dma_eb.common.hw,
		[CLK_UFS_EB]		= &ufs_eb.common.hw,
		[CLK_CKG_EB]		= &ckg_eb.common.hw,
		[CLK_BUSMON_CLK_EB]	= &busmon_clk_eb.common.hw,
		[CLK_AP2EMC_EB]		= &ap2emc_eb.common.hw,
		[CLK_I2C0_EB]		= &i2c0_eb.common.hw,
		[CLK_I2C1_EB]		= &i2c1_eb.common.hw,
		[CLK_I2C2_EB]		= &i2c2_eb.common.hw,
		[CLK_I2C3_EB]		= &i2c3_eb.common.hw,
		[CLK_I2C4_EB]		= &i2c4_eb.common.hw,
		[CLK_I2C5_EB]		= &i2c5_eb.common.hw,
		[CLK_I2C6_EB]		= &i2c6_eb.common.hw,
		[CLK_I2C7_EB]		= &i2c7_eb.common.hw,
		[CLK_I2C8_EB]		= &i2c8_eb.common.hw,
		[CLK_I2C9_EB]		= &i2c9_eb.common.hw,
		[CLK_NOC_SER_EB]	= &noc_ser_eb.common.hw,
		[CLK_UFS_TX_EB]		= &ufs_tx_eb.common.hw,
		[CLK_UFS_RX_0_EB]	= &ufs_rx_0_eb.common.hw,
		[CLK_UFS_RX_1_EB]	= &ufs_rx_1_eb.common.hw,
		[CLK_UFS_AO_APB_EB]	= &ufs_ao_apb_eb.common.hw,
		[CLK_UFS_UNIPRO_APB_EB]	= &ufs_unipro_apb_eb.common.hw,
		[CLK_UFS_UTP_APB_EB]	= &ufs_utp_apb_eb.common.hw,
		[CLK_UFS_HCI_APB_EB]	= &ufs_hci_apb_eb.common.hw,
	},
	.num	= CLK_AP_AHB_GATE_NUM,
};

static struct sprd_reset_map ums9632_ap_ahb_resets[] = {
	[RESET_AP_AHB_SDIO0_SOFT_RST]		= { 0x0004, BIT(0), true, 0x1000 },
	[RESET_AP_AHB_SDIO1_SOFT_RST]		= { 0x0004, BIT(1), true, 0x1000 },
	[RESET_AP_AHB_EMMC_SOFT_RST]		= { 0x0004, BIT(2), true, 0x1000 },
	[RESET_AP_AHB_UFS_UNIPRO_SOFT_RST]	= { 0x0004, BIT(3), true, 0x1000 },
	[RESET_AP_AHB_UFS_UTP_SOFT_RST]		= { 0x0004, BIT(4), true, 0x1000 },
	[RESET_AP_AHB_UFS_AON_SOFT_RST]		= { 0x0004, BIT(5), true, 0x1000 },
	[RESET_AP_AHB_UFS_SOFT_RST]		= { 0x0004, BIT(6), true, 0x1000 },
	[RESET_AP_AHB_UFS_HCI_SOFT_RST]		= { 0x0004, BIT(7), true, 0x1000 },
	[RESET_AP_AHB_DMA_SOFT_RST]		= { 0x0004, BIT(8), true, 0x1000 },
	[RESET_AP_AHB_UFS_TX_SOFT_RST]		= { 0x0004, BIT(9), true, 0x1000 },
	[RESET_AP_AHB_I2C0_SOFT_RST]		= { 0x0004, BIT(16), true, 0x1000 },
	[RESET_AP_AHB_I2C1_SOFT_RST]		= { 0x0004, BIT(17), true, 0x1000 },
	[RESET_AP_AHB_I2C2_SOFT_RST]		= { 0x0004, BIT(18), true, 0x1000 },
	[RESET_AP_AHB_I2C3_SOFT_RST]		= { 0x0004, BIT(19), true, 0x1000 },
	[RESET_AP_AHB_I2C4_SOFT_RST]		= { 0x0004, BIT(20), true, 0x1000 },
	[RESET_AP_AHB_I2C5_SOFT_RST]		= { 0x0004, BIT(21), true, 0x1000 },
	[RESET_AP_AHB_I2C6_SOFT_RST]		= { 0x0004, BIT(22), true, 0x1000 },
	[RESET_AP_AHB_I2C7_SOFT_RST]		= { 0x0004, BIT(23), true, 0x1000 },
	[RESET_AP_AHB_I2C8_SOFT_RST]		= { 0x0004, BIT(24), true, 0x1000 },
	[RESET_AP_AHB_I2C9_SOFT_RST]		= { 0x0004, BIT(25), true, 0x1000 },
};

static struct sprd_clk_desc ums9632_apahb_gate_desc = {
	.clk_clks	= ums9632_apahb_gate_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_apahb_gate_clks),
	.hw_clks	= &ums9632_apahb_gate_hws,
	.resets		= ums9632_ap_ahb_resets,
	.num_resets	= ARRAY_SIZE(ums9632_ap_ahb_resets),
};

/* ap clks */
static const struct clk_parent_data ap_apb_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_64m.hw },
	{ .hw = &tgpll_96m.hw },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(ap_apb, "ap-apb", ap_apb_parents, 0x28,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data ap_axi_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_76m8.hw },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_256m.hw },
};
static SPRD_MUX_CLK_DATA(ap_axi, "ap-axi", ap_axi_parents, 0x34,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data ap2emc_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_76m8.hw },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_512m.hw },
};
static SPRD_MUX_CLK_DATA(ap2emc, "ap2emc", ap2emc_parents, 0x40,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data ap_uart_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_48m.hw },
	{ .hw = &tgpll_96m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(ap_uart0, "ap-uart0", ap_uart_parents,
			    0x4c, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_uart1, "ap-uart1", ap_uart_parents,
			    0x58, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_uart2, "ap-uart2", ap_uart_parents,
			    0x64, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_uart3, "ap-uart3", ap_uart_parents,
			    0x70, 0, 2, -4, 0, 3, 0);

static const struct clk_parent_data i2c_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_48m.hw },
	{ .hw = &tgpll_153m6.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c0, "ap-i2c0", i2c_parents,
			    0x7c, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c1, "ap-i2c1", i2c_parents,
			    0x88, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c2, "ap-i2c2", i2c_parents,
			    0x94, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c3, "ap-i2c3", i2c_parents,
			    0xa0, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c4, "ap-i2c4", i2c_parents,
			    0xac, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c5, "ap-i2c5", i2c_parents,
			    0xb8, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c6, "ap-i2c6", i2c_parents,
			    0xc4, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c7, "ap-i2c7", i2c_parents,
			    0xd0, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c8, "ap-i2c8", i2c_parents,
			    0xdc, 0, 2, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_i2c9, "ap-i2c9", i2c_parents,
			    0xe8, 0, 2, -4, 0, 3, 0);

static const struct clk_parent_data iis_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_512m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(ap_iis0, "ap-iis0", iis_parents,
			    0xf4, 0, 3, -4, 0, 6, 0);
static SPRD_COMP_CLK_DATA_OFFSET(ap_iis1, "ap-iis1", iis_parents,
			    0x100, 0, 3, -4, 0, 6, 0);

static const struct clk_parent_data ap_ce_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_96m.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
};
static SPRD_MUX_CLK_DATA(ap_ce, "ap-ce", ap_ce_parents, 0x10c,
		    0, 2, UMS9632_MUX_FLAG);
static SPRD_DIV_CLK_HW(ap_ce_x1, "ap-ce-x1", &ap_ce.common.hw, 0x114,
		    0, 1, 0);

static const struct clk_parent_data emmc_2x_parents[] = {
	{ .hw = &clk_1m.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &rpll_390m.hw },
	{ .hw = &v4nrpll_409m6.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(emmc_2x, "emmc-2x", emmc_2x_parents,
			    0x124, 0, 3, -4, 0, 11, 0);
static SPRD_DIV_CLK_HW(emmc_1x, "emmc-1x", &emmc_2x.common.hw, 0x12c,
		    0, 1, 0);

static const struct clk_parent_data ufs_tx_div2_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_153m6.hw },
};
static SPRD_MUX_CLK_DATA(ufs_tx_div2, "ufs-tx-div2", ufs_tx_div2_parents, 0x13c,
		    0, 1, UMS9632_MUX_FLAG);
static SPRD_GATE_CLK_FW_NAME(ufs_tx_div2_pad, "ufs-tx-div2-pad", "ext-26m",
			0x13c, BIT(16), CLK_IGNORE_UNUSED, 0);

static const struct clk_parent_data ufs_tx_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_384m.hw },
};
static SPRD_MUX_CLK_DATA(ufs_tx, "ufs-tx", ufs_tx_parents, 0x148,
			0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data ufs_ufs_rx_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_153m6.hw },
};
static SPRD_MUX_CLK_DATA(ufs_rx_0, "ufs-rx-0", ufs_ufs_rx_parents, 0x154,
			0, 1, UMS9632_MUX_FLAG);
static SPRD_GATE_CLK_FW_NAME(ufs_rx_0_pad, "ufs-rx-0-pad", "ext-26m",
			0x154, BIT(16), CLK_IGNORE_UNUSED, 0);

static SPRD_MUX_CLK_DATA(ufs_rx_1, "ufs-rx-1", ufs_ufs_rx_parents, 0x160,
			0, 1, UMS9632_MUX_FLAG);
static SPRD_GATE_CLK_FW_NAME(ufs_rx_1_pad, "ufs-rx-1-pad", "ext-26m",
			0x160, BIT(16), CLK_IGNORE_UNUSED, 0);

static const struct clk_parent_data ufs_axi_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_384m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(ufs_axi, "ufs-axi", ufs_axi_parents,
			    0x16c, 0, 2, -4, 0, 3, 0);

static const struct clk_parent_data ufs_apb_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(ufs_apb, "ufs-apb", ufs_apb_parents,
				0x178, 0, 1, -4, 0, 3, 0);

static const struct clk_parent_data ipa_axi_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &v4nrpll_409m6.hw },
};
static SPRD_MUX_CLK_DATA(ipa_axi, "ipa-axi", ipa_axi_parents, 0x184,
			0, 2, UMS9632_MUX_FLAG);
static SPRD_DIV_CLK_HW(ipa_apb, "ipa-apb", &ipa_axi.common.hw, 0x18c,
			0, 2, 0);

static struct sprd_clk_common *ums9632_ap_clks[] = {
	/* address base is 0x20010000 */
	&ap_apb.common,
	&ap_axi.common,
	&ap2emc.common,
	&ap_uart0.common,
	&ap_uart1.common,
	&ap_uart2.common,
	&ap_uart3.common,
	&ap_i2c0.common,
	&ap_i2c1.common,
	&ap_i2c2.common,
	&ap_i2c3.common,
	&ap_i2c4.common,
	&ap_i2c5.common,
	&ap_i2c6.common,
	&ap_i2c7.common,
	&ap_i2c8.common,
	&ap_i2c9.common,
	&ap_iis0.common,
	&ap_iis1.common,
	&ap_ce.common,
	&ap_ce_x1.common,
	&emmc_2x.common,
	&emmc_1x.common,
	&ufs_tx_div2.common,
	&ufs_tx_div2_pad.common,
	&ufs_tx.common,
	&ufs_rx_0.common,
	&ufs_rx_0_pad.common,
	&ufs_rx_1.common,
	&ufs_rx_1_pad.common,
	&ufs_axi.common,
	&ufs_apb.common,
	&ipa_axi.common,
	&ipa_apb.common,
};

static struct clk_hw_onecell_data ums9632_ap_clk_hws = {
	.hws	= {
		[CLK_AP_APB]		= &ap_apb.common.hw,
		[CLK_AP_AXI]		= &ap_axi.common.hw,
		[CLK_AP2EMC]		= &ap2emc.common.hw,
		[CLK_AP_UART0]		= &ap_uart0.common.hw,
		[CLK_AP_UART1]		= &ap_uart1.common.hw,
		[CLK_AP_UART2]		= &ap_uart2.common.hw,
		[CLK_AP_UART3]		= &ap_uart3.common.hw,
		[CLK_AP_I2C0]		= &ap_i2c0.common.hw,
		[CLK_AP_I2C1]		= &ap_i2c1.common.hw,
		[CLK_AP_I2C2]		= &ap_i2c2.common.hw,
		[CLK_AP_I2C3]		= &ap_i2c3.common.hw,
		[CLK_AP_I2C4]		= &ap_i2c4.common.hw,
		[CLK_AP_I2C5]		= &ap_i2c5.common.hw,
		[CLK_AP_I2C6]		= &ap_i2c6.common.hw,
		[CLK_AP_I2C7]		= &ap_i2c7.common.hw,
		[CLK_AP_I2C8]		= &ap_i2c8.common.hw,
		[CLK_AP_I2C9]		= &ap_i2c9.common.hw,
		[CLK_AP_IIS0]		= &ap_iis0.common.hw,
		[CLK_AP_IIS1]		= &ap_iis1.common.hw,
		[CLK_AP_CE]		= &ap_ce.common.hw,
		[CLK_AP_CE_X1]		= &ap_ce_x1.common.hw,
		[CLK_EMMC_2X]		= &emmc_2x.common.hw,
		[CLK_EMMC_1X]		= &emmc_1x.common.hw,
		[CLK_UFS_TX_DIV2]	= &ufs_tx_div2.common.hw,
		[CLK_UFS_TX_DIV2_PAD]	= &ufs_tx_div2_pad.common.hw,
		[CLK_UFS_TX]		= &ufs_tx.common.hw,
		[CLK_UFS_RX_0]		= &ufs_rx_0.common.hw,
		[CLK_UFS_RX_0_PAD]	= &ufs_rx_0_pad.common.hw,
		[CLK_UFS_RX_1]		= &ufs_rx_1.common.hw,
		[CLK_UFS_RX_1_PAD]	= &ufs_rx_1_pad.common.hw,
		[CLK_UFS_AXI]		= &ufs_axi.common.hw,
		[CLK_UFS_APB]		= &ufs_apb.common.hw,
		[CLK_IPA_AXI]		= &ipa_axi.common.hw,
		[CLK_IPA_APB]		= &ipa_apb.common.hw,
	},
	.num	= CLK_AP_CLK_NUM,
};

static struct sprd_clk_desc ums9632_ap_clk_desc = {
	.clk_clks	= ums9632_ap_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_ap_clks),
	.hw_clks	= &ums9632_ap_clk_hws,
};

/* aon apb gates */
static SPRD_SC_GATE_CLK_FW_NAME(djtag_eb, "djtag-eb", "ext-26m", 0x0,
			0x1000, BIT(3), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aux0_eb, "aux0-eb", "ext-26m", 0x0,
			0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aux1_eb, "aux1-eb", "ext-26m", 0x0,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aux2_eb, "aux2-eb", "ext-26m", 0x0,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(probe_eb, "probe-eb", "ext-26m", 0x0,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(mm_eb, "mm-eb", "ext-26m", 0x0,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(gpu_eb, "gpu-eb", "ext-26m", 0x0,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(apcpu_dap_eb, "apcpu-dap-eb", "ext-26m", 0x0,
			0x1000, BIT(14), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_cssys_eb, "aon-cssys-eb", "ext-26m", 0x0,
			0x1000, BIT(15), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(cssys_apb_eb, "cssys-apb-eb", "ext-26m", 0x0,
			0x1000, BIT(16), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(dpu_vsp_eb, "dpu-vsp-eb", "ext-26m", 0x0,
			0x1000, BIT(21), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(dsi_cfg_eb, "dsi-cfg-eb", "ext-26m", 0x0,
			0x1000, BIT(31), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(efuse_eb, "efuse-eb", "ext-26m", 0x4,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(gpio_eb, "gpio-eb", "ext-26m", 0x4,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(mbox_eb, "mbox-eb", "ext-26m", 0x4,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(kpd_eb, "kpd-eb", "ext-26m", 0x4,
			0x1000, BIT(3), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_syst_eb, "aon-syst-eb", "ext-26m", 0x4,
			0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_syst_eb, "ap-syst-eb", "ext-26m", 0x4,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_tmr_eb, "aon-tmr-eb", "ext-26m", 0x4,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_dvfs_top_eb, "aon-dvfs-top-eb", "ext-26m", 0x4,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_usb2_top_eb, "aon-usb2-top-eb", "ext-26m", 0x4,
			0x1000, BIT(8), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_vlt_sensor_eb, "aon-vlt-sensor-eb", "ext-26m", 0x4,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(splk_eb, "splk-eb", "ext-26m", 0x4,
			0x1000, BIT(10), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(pin_eb, "pin-eb", "ext-26m", 0x4,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ana_eb, "ana-eb", "ext-26m", 0x4,
			0x1000, BIT(12), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(apcpu_busmon_eb,  "apcpu-busmon-eb", "ext-26m", 0x4,
			0x1000, BIT(14), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(apcpu_ts_eb, "apcpu-ts-eb", "ext-26m", 0x4,
			0x1000, BIT(17), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(apb_busmon_eb,  "apb-busmon-eb", "ext-26m", 0x4,
			0x1000, BIT(18), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_ase_eb, "aon-ase-eb", "ext-26m", 0x4,
			0x1000, BIT(19), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_scc_eb, "aon-scc-eb", "ext-26m", 0x4,
			0x1000, BIT(20), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aux3_eb, "aux3-eb", "ext-26m", 0x4,
			0x1000, BIT(30), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(bisr_eb, "bisr-eb", "ext-26m", 0x4,
			0x1000, BIT(31), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(thm0_eb, "thm0-eb", "ext-26m", 0x8,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(thm1_eb, "thm1-eb", "ext-26m", 0x8,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(adi_eb, "adi-eb", "ext-26m", 0x8,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(eic_eb, "eic-eb", "ext-26m", 0x8,
			0x1000, BIT(10), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_tmr0_eb, "ap-tmr0-eb", "ext-26m", 0x8,
			0x1000, BIT(22), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_tmr1_eb, "ap-tmr1-eb", "ext-26m", 0x8,
			0x1000, BIT(23), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_tmr2_eb, "ap-tmr2-eb", "ext-26m", 0x8,
			0x1000, BIT(24), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(pwm0_eb, "pwm0-eb", "ext-26m", 0x8,
			0x1000, BIT(25), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(pwm1_eb, "pwm1-eb", "ext-26m", 0x8,
			0x1000, BIT(26), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(pwm2_eb, "pwm2-eb", "ext-26m", 0x8,
			0x1000, BIT(27), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(pwm3_eb, "pwm3-eb", "ext-26m", 0x8,
			0x1000, BIT(28), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_wdg_eb, "ap-wdg-eb", "ext-26m", 0x8,
			0x1000, BIT(29), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(apcpu_wdg_eb, "apcpu-wdg-eb", "ext-26m", 0x8,
			0x1000, BIT(30), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(arch_rtc_eb, "arch-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(kpd_rtc_eb, "kpd-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_syst_rtc_eb, "aon-syst-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_syst_rtc_eb, "ap-syst-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(3), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(aon_tmr_rtc_eb, "aon-tmr-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(eic_rtc_eb, "eic-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(eic_rtcdv5_eb, "eic-rtcdv5-eb", "ext-26m", 0x18,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_wdg_rtc_eb, "ap-wdg-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ac_wdg_rtc_eb, "ac-wdg-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(8), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_tmr0_rtc_eb, "ap-tmr0-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_tmr1_rtc_eb, "ap-tmr1-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(10), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(ap_tmr2_rtc_eb, "ap-tmr2-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(dcxo_lc_rtc_eb, "dcxo-lc-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(12), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(bb_cal_rtc_eb, "bb-cal-rtc-eb", "ext-26m", 0x18,
			0x1000, BIT(13), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(djtag_tck_en, "djtag-tck-en", "ext-26m", 0x138,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(dmc_ref_eb, "dmc-ref-eb", "ext-26m", 0x138,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(otg_ref_eb, "otg-ref-eb", "ext-26m", 0x138,
			0x1000, BIT(12), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(debounce_eb, "debounce-eb", "ext-26m", 0x138,
			0x1000, BIT(17), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(det_32k_eb, "det-32k-eb", "ext-26m", 0x138,
			0x1000, BIT(18), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(debug_ts_en, "debug-ts-en", "ext-26m", 0x13c,
			0x1000, BIT(18), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_FW_NAME(access_aud_en, "access-aud-en", "ext-26m", 0x14c,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);

static const struct clk_parent_data aux_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .hw = &clk_52m.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_COMP_CLK_DATA(aux0_clk, "aux0-clk", aux_parents, 0x240,
		    6, 6, 0, 6, 0);
static SPRD_COMP_CLK_DATA(aux1_clk, "aux1-clk", aux_parents, 0x244,
		    6, 6, 0, 6, 0);
static SPRD_COMP_CLK_DATA(aux2_clk, "aux2-clk", aux_parents, 0x248,
		    6, 6, 0, 6, 0);
static SPRD_COMP_CLK_DATA(probe_clk, "probe-clk", aux_parents, 0x24c,
		    6, 6, 0, 6, 0);
static SPRD_COMP_CLK_DATA(aux3_clk, "aux3-clk", aux_parents, 0xd20,
		    6, 6, 0, 6, 0);

static struct sprd_clk_common *ums9632_aon_gate_clks[] = {
	/* address base is 0x64900000 */
	&djtag_eb.common,
	&aux0_eb.common,
	&aux1_eb.common,
	&aux2_eb.common,
	&probe_eb.common,
	&mm_eb.common,
	&gpu_eb.common,
	&apcpu_dap_eb.common,
	&aon_cssys_eb.common,
	&cssys_apb_eb.common,
	&dpu_vsp_eb.common,
	&dsi_cfg_eb.common,
	&efuse_eb.common,
	&gpio_eb.common,
	&mbox_eb.common,
	&kpd_eb.common,
	&aon_syst_eb.common,
	&ap_syst_eb.common,
	&aon_tmr_eb.common,
	&aon_dvfs_top_eb.common,
	&aon_usb2_top_eb.common,
	&aon_vlt_sensor_eb.common,
	&splk_eb.common,
	&pin_eb.common,
	&ana_eb.common,
	&apcpu_busmon_eb.common,
	&apcpu_ts_eb.common,
	&apb_busmon_eb.common,
	&aon_ase_eb.common,
	&aon_scc_eb.common,
	&aux3_eb.common,
	&bisr_eb.common,
	&thm0_eb.common,
	&thm1_eb.common,
	&adi_eb.common,
	&eic_eb.common,
	&ap_tmr0_eb.common,
	&ap_tmr1_eb.common,
	&ap_tmr2_eb.common,
	&pwm0_eb.common,
	&pwm1_eb.common,
	&pwm2_eb.common,
	&pwm3_eb.common,
	&ap_wdg_eb.common,
	&apcpu_wdg_eb.common,
	&arch_rtc_eb.common,
	&kpd_rtc_eb.common,
	&aon_syst_rtc_eb.common,
	&ap_syst_rtc_eb.common,
	&aon_tmr_rtc_eb.common,
	&eic_rtc_eb.common,
	&eic_rtcdv5_eb.common,
	&ap_wdg_rtc_eb.common,
	&ac_wdg_rtc_eb.common,
	&ap_tmr0_rtc_eb.common,
	&ap_tmr1_rtc_eb.common,
	&ap_tmr2_rtc_eb.common,
	&dcxo_lc_rtc_eb.common,
	&bb_cal_rtc_eb.common,
	&djtag_tck_en.common,
	&dmc_ref_eb.common,
	&otg_ref_eb.common,
	&debounce_eb.common,
	&det_32k_eb.common,
	&debug_ts_en.common,
	&access_aud_en.common,
	&aux0_clk.common,
	&aux1_clk.common,
	&aux2_clk.common,
	&probe_clk.common,
	&aux3_clk.common,
};

static struct clk_hw_onecell_data ums9632_aon_gate_hws = {
	.hws	= {
		[CLK_DJTAG_EB]		= &djtag_eb.common.hw,
		[CLK_AUX0_EB]		= &aux0_eb.common.hw,
		[CLK_AUX1_EB]		= &aux1_eb.common.hw,
		[CLK_AUX2_EB]		= &aux2_eb.common.hw,
		[CLK_PROBE_EB]		= &probe_eb.common.hw,
		[CLK_MM_EB]		= &mm_eb.common.hw,
		[CLK_GPU_EB]		= &gpu_eb.common.hw,
		[CLK_APCPU_DAP_EB]	= &apcpu_dap_eb.common.hw,
		[CLK_AON_CSSYS_EB]	= &aon_cssys_eb.common.hw,
		[CLK_CSSYS_APB_EB]	= &cssys_apb_eb.common.hw,
		[CLK_DPU_VSP_EB]	= &dpu_vsp_eb.common.hw,
		[CLK_DSI_CFG_EB]	= &dsi_cfg_eb.common.hw,
		[CLK_EFUSE_EB]		= &efuse_eb.common.hw,
		[CLK_GPIO_EB]		= &gpio_eb.common.hw,
		[CLK_MBOX_EB]		= &mbox_eb.common.hw,
		[CLK_KPD_EB]		= &kpd_eb.common.hw,
		[CLK_AON_SYST_EB]	= &aon_syst_eb.common.hw,
		[CLK_AP_SYST_EB]	= &ap_syst_eb.common.hw,
		[CLK_AON_TMR_EB]	= &aon_tmr_eb.common.hw,
		[CLK_AON_DVFS_TOP_EB]	= &aon_dvfs_top_eb.common.hw,
		[CLK_AON_USB2_TOP_EB]	= &aon_usb2_top_eb.common.hw,
		[CLK_AON_VLT_SENSOR_EB]	= &aon_vlt_sensor_eb.common.hw,
		[CLK_SPLK_EB]		= &splk_eb.common.hw,
		[CLK_PIN_EB]		= &pin_eb.common.hw,
		[CLK_ANA_EB]		= &ana_eb.common.hw,
		[CLK_APCPU_BUSMON_EB]	= &apcpu_busmon_eb.common.hw,
		[CLK_APCPU_TS_EB]	= &apcpu_ts_eb.common.hw,
		[CLK_APB_BUSMON_EB]	= &apb_busmon_eb.common.hw,
		[CLK_AON_ASE_EB]	= &aon_ase_eb.common.hw,
		[CLK_AON_SCC_EB]	= &aon_scc_eb.common.hw,
		[CLK_AUX3_EB]		= &aux3_eb.common.hw,
		[CLK_BISR_EB]		= &bisr_eb.common.hw,
		[CLK_THM0_EB]		= &thm0_eb.common.hw,
		[CLK_THM1_EB]		= &thm1_eb.common.hw,
		[CLK_ADI_EB]		= &adi_eb.common.hw,
		[CLK_EIC_EB]		= &eic_eb.common.hw,
		[CLK_AP_TMR0_EB]	= &ap_tmr0_eb.common.hw,
		[CLK_AP_TMR1_EB]	= &ap_tmr1_eb.common.hw,
		[CLK_AP_TMR2_EB]	= &ap_tmr2_eb.common.hw,
		[CLK_PWM0_EB]		= &pwm0_eb.common.hw,
		[CLK_PWM1_EB]		= &pwm1_eb.common.hw,
		[CLK_PWM2_EB]		= &pwm2_eb.common.hw,
		[CLK_PWM3_EB]		= &pwm3_eb.common.hw,
		[CLK_AP_WDG_EB]		= &ap_wdg_eb.common.hw,
		[CLK_APCPU_WDG_EB]	= &apcpu_wdg_eb.common.hw,
		[CLK_ARCH_RTC_EB]	= &arch_rtc_eb.common.hw,
		[CLK_KPD_RTC_EB]	= &kpd_rtc_eb.common.hw,
		[CLK_AON_SYST_RTC_EB]	= &aon_syst_rtc_eb.common.hw,
		[CLK_AP_SYST_RTC_EB]	= &ap_syst_rtc_eb.common.hw,
		[CLK_AON_TMR_RTC_EB]	= &aon_tmr_rtc_eb.common.hw,
		[CLK_EIC_RTC_EB]	= &eic_rtc_eb.common.hw,
		[CLK_EIC_RTCDV5_EB]	= &eic_rtcdv5_eb.common.hw,
		[CLK_AP_WDG_RTC_EB]	= &ap_wdg_rtc_eb.common.hw,
		[CLK_AC_WDG_RTC_EB]	= &ac_wdg_rtc_eb.common.hw,
		[CLK_AP_TMR0_RTC_EB]	= &ap_tmr0_rtc_eb.common.hw,
		[CLK_AP_TMR1_RTC_EB]	= &ap_tmr1_rtc_eb.common.hw,
		[CLK_AP_TMR2_RTC_EB]	= &ap_tmr2_rtc_eb.common.hw,
		[CLK_DCXO_LC_RTC_EB]	= &dcxo_lc_rtc_eb.common.hw,
		[CLK_BB_CAL_RTC_EB]	= &bb_cal_rtc_eb.common.hw,
		[CLK_DJTAG_TCK_EN]	= &djtag_tck_en.common.hw,
		[CLK_DMC_REF_EB]	= &dmc_ref_eb.common.hw,
		[CLK_OTG_REF_EB]	= &otg_ref_eb.common.hw,
		[CLK_DEBOUNCE_EB]	= &debounce_eb.common.hw,
		[CLK_DET_32K_EB]	= &det_32k_eb.common.hw,
		[CLK_DEBUG_TS_EN]	= &debug_ts_en.common.hw,
		[CLK_ACCESS_AUD_EN]	= &access_aud_en.common.hw,
		[CLK_AUX0]		= &aux0_clk.common.hw,
		[CLK_AUX1]		= &aux1_clk.common.hw,
		[CLK_AUX2]		= &aux2_clk.common.hw,
		[CLK_PROBE]		= &probe_clk.common.hw,
		[CLK_AUX3]		= &aux3_clk.common.hw,
	},
	.num	= CLK_AON_APB_GATE_NUM,
};

static struct sprd_reset_map ums9632_aon_apb_resets[] = {
	[RESET_AON_APB_DAP_MTX_SOFT_RST]	= { 0x000c, BIT(6), true, 0x1000 },
	[RESET_AON_APB_EFUSE_SOFT_RST]		= { 0x0010, BIT(0), true, 0x1000 },
	[RESET_AON_APB_GPIO_SOFT_RST]		= { 0x0010, BIT(1), true, 0x1000 },
	[RESET_AON_APB_MBOX_SOFT_RST]		= { 0x0010, BIT(2), true, 0x1000 },
	[RESET_AON_APB_KPD_SOFT_RST]		= { 0x0010, BIT(3), true, 0x1000 },
	[RESET_AON_APB_AON_SYST_SOFT_RST]	= { 0x0010, BIT(4), true, 0x1000 },
	[RESET_AON_APB_AP_SYST_SOFT_RST]	= { 0x0010, BIT(5), true, 0x1000 },
	[RESET_AON_APB_DVFS_TOP_SOFT_RST]	= { 0x0010, BIT(7), true, 0x1000 },
	[RESET_AON_APB_OTG_UTMI_SOFT_RST]	= { 0x0010, BIT(8), true, 0x1000 },
	[RESET_AON_APB_OTG_PHY_SOFT_RST]	= { 0x0010, BIT(9), true, 0x1000 },
	[RESET_AON_APB_SPLK_SOFT_RST]		= { 0x0010, BIT(10), true, 0x1000 },
	[RESET_AON_APB_ANA_SOFT_RST]		= { 0x0010, BIT(12), true, 0x1000 },
	[RESET_AON_APB_CKG_SOFT_RST]		= { 0x0010, BIT(13), true, 0x1000 },
	[RESET_AON_APB_AON_VLT_SENSOR_SOFT_RST]	= { 0x0010, BIT(14), true, 0x1000 },
	[RESET_AON_APB_APCPU_TS0_SOFT_RST]	= { 0x0010, BIT(17), true, 0x1000 },
	[RESET_AON_APB_DEBUG_FILTER_SOFT_RST]	= { 0x0010, BIT(18), true, 0x1000 },
	[RESET_AON_APB_AON_ASE_SOFT_RST]	= { 0x0010, BIT(19), true, 0x1000 },
	[RESET_AON_APB_SCC_SOFT_RST]		= { 0x0010, BIT(20), true, 0x1000 },
	[RESET_AON_APB_SERDES1_SOFT_RST]	= { 0x0010, BIT(21), true, 0x1000 },
	[RESET_AON_APB_SERDES0_SOFT_RST]	= { 0x0010, BIT(22), true, 0x1000 },
	[RESET_AON_APB_THM0_OVERHEAT_SOFT_RST]	= { 0x0010, BIT(23), true, 0x1000 },
	[RESET_AON_APB_THM1_OVERHEAT_SOFT_RST]	= { 0x0010, BIT(24), true, 0x1000 },
	[RESET_AON_APB_THM0_SOFT_RST]		= { 0x0014, BIT(0), true, 0x1000 },
	[RESET_AON_APB_THM1_SOFT_RST]		= { 0x0014, BIT(1), true, 0x1000 },
	[RESET_AON_APB_PMU_SOFT_RST]		= { 0x0014, BIT(8), true, 0x1000 },
	[RESET_AON_APB_ADI_SOFT_RST]		= { 0x0014, BIT(9), true, 0x1000 },
	[RESET_AON_APB_EIC_SOFT_RST]		= { 0x0014, BIT(10), true, 0x1000 },
	[RESET_AON_APB_PWM0_SOFT_RST]		= { 0x0014, BIT(25), true, 0x1000 },
	[RESET_AON_APB_PWM1_SOFT_RST]		= { 0x0014, BIT(26), true, 0x1000 },
	[RESET_AON_APB_PWM2_SOFT_RST]		= { 0x0014, BIT(27), true, 0x1000 },
	[RESET_AON_APB_PWM3_SOFT_RST]		= { 0x0014, BIT(28), true, 0x1000 },
	[RESET_AON_APB_AP_WDG_SOFT_RST]		= { 0x0014, BIT(29), true, 0x1000 },
	[RESET_AON_APB_APCPU_WDG_SOFT_RST]	= { 0x0014, BIT(30), true, 0x1000 },
	[RESET_AON_APB_DJTAG_SOFT_RST]		= { 0x0130, BIT(15), true, 0x1000 },
	[RESET_AON_APB_MPHY_PHY_RESET_SOFT_RST]	= { 0x0130, BIT(17), true, 0x1000 },
	[RESET_AON_APB_UFSDEV_SOFT_RST]		= { 0x0ce8, BIT(0), true, 0x1000 },
};

static struct sprd_clk_desc ums9632_aon_gate_desc = {
	.clk_clks	= ums9632_aon_gate_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_aon_gate_clks),
	.hw_clks	= &ums9632_aon_gate_hws,
	.resets		= ums9632_aon_apb_resets,
	.num_resets	= ARRAY_SIZE(ums9632_aon_apb_resets),
};

/* aon apb clks */
static const struct clk_parent_data aon_apb_parents[] = {
	{ .hw = &rco_150m_4m05.hw },
	{ .hw = &clk_13m.hw },
	{ .fw_name = "ext-26m" },
	{ .fw_name = "rco-150m" },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_192m.hw },
};
static SPRD_MUX_CLK_DATA(aon_apb, "aon-apb", aon_apb_parents, 0x28,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data adi_parents[] = {
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_51m2.hw },
};
static SPRD_MUX_CLK_DATA(adi, "adi", adi_parents, 0x34,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data pwm_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(pwm0, "pwm0", pwm_parents, 0x40,
		    0, 2, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(pwm1, "pwm1", pwm_parents, 0x4c,
		    0, 2, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(pwm2, "pwm2", pwm_parents, 0x58,
		    0, 2, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(pwm3, "pwm3", pwm_parents, 0x64,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data efuse_parents[] = {
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_MUX_CLK_DATA(efuse, "efuse", efuse_parents, 0x70,
		    0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data thm_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .hw = &clk_250k.hw },
};
static SPRD_MUX_CLK_DATA(thm0, "thm0", thm_parents, 0xa0,
		    0, 1, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(thm1, "thm1", thm_parents, 0xac,
		    0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data pri_sbi_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &clk_52m.hw },
	{ .hw = &tgpll_96m.hw },
};
static SPRD_MUX_CLK_DATA(pri_sbi, "pri-sbi", pri_sbi_parents, 0xdc,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data aon_tmr_parents[] = {
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_MUX_CLK_DATA(aon_tmr, "aon-tmr", aon_tmr_parents, 0x148,
		    0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data tzpc_parents[] = {
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(tzpc, "tzpc", tzpc_parents, 0x160,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data aon_pmu_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .hw = &rco_150m_4m05.hw },
	{ .hw = &clk_4m33.hw },
};
static SPRD_MUX_CLK_DATA(aon_pmu, "aon_pmu", aon_pmu_parents, 0x16c,
			0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data debounce_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_MUX_CLK_DATA(debounce, "debounce", debounce_parents, 0x178,
			0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data apcpu_pmu_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &rco_150m_75m.hw },
	{ .hw = &tgpll_76m8.hw },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(apcpu_pmu, "apcpu-pmu", apcpu_pmu_parents, 0x184,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data top_dvfs_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &rco_150m_75m.hw },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(top_dvfs, "top-dvfs", top_dvfs_parents, 0x190,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data uart_parents[] = {
	{ .hw = &rco_150m_4m05.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &rco_150m_75m.hw },
	{ .hw = &tgpll_96m.hw },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(uart0, "uart0", uart_parents, 0x19c,
			0, 3, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(uart1, "uart1", uart_parents, 0x1a8,
			0, 3, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(uart2, "uart2", uart_parents, 0x1b4,
			0, 3, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(uart3, "uart3", uart_parents, 0x1c0,
			0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data apcpu_dap_parents[] = {
	{ .hw = &rco_150m_4m05.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &rco_150m_75m.hw },
	{ .hw = &tgpll_76m8.hw },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_153m6.hw },
};
static SPRD_MUX_CLK_DATA(apcpu_dap, "apcpu-dap", apcpu_dap_parents, 0x208,
			0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data apcpu_ts_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_153m6.hw },
};
static SPRD_MUX_CLK_DATA(apcpu_ts, "apcpu-ts", apcpu_ts_parents, 0x214,
			0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data debug_ts_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_76m8.hw },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_192m.hw },
};
static SPRD_MUX_CLK_DATA(debug_ts, "debug-ts", debug_ts_parents, 0x220,
			0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data djtag_tck_parents[] = {
	{ .hw = &rco_150m_4m05.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_MUX_CLK_DATA(djtag_tck, "djtag-tck", djtag_tck_parents, 0x22c,
			0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data det_ref_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .fw_name = "ext-26m" },
};
static SPRD_MUX_CLK_DATA(det_ref, "det-ref", det_ref_parents, 0x238,
			0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data cssys_parents[] = {
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &rco_150m_75m.hw },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_384m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(cssys, "cssys", cssys_parents, 0x244,
			    0, 3, -4, 0, 2, 0);
static SPRD_DIV_CLK_HW(cssys_apb, "cssys-apb", &cssys.common.hw, 0x24c,
		    0, 2, 0);

static const struct clk_parent_data sdio_2x_parents[] = {
	{ .hw = &clk_1m.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &rpll_390m.hw },
	{ .hw = &v4nrpll_409m6.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(sdio0_2x, "sdio0-2x", sdio_2x_parents, 0x268,
		    0, 3, -4, 0, 11, 0);
static SPRD_DIV_CLK_HW(sdio0_1x, "sdio0-1x", &sdio0_2x.common.hw, 0x270,
		    0, 1, 0);

static SPRD_COMP_CLK_DATA_OFFSET(sdio1_2x, "sdio1-2x", sdio_2x_parents, 0x280,
			    0, 3, -4, 0, 11, 0);
static SPRD_DIV_CLK_HW(sdio1_1x, "sdio1-1x", &sdio1_2x.common.hw, 0x288,
		    0, 1, 0);

static const struct clk_parent_data spi_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_192m.hw },
};
static SPRD_MUX_CLK_DATA(spi0, "spi0", spi_parents, 0x298,
		    0, 1, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(spi1, "spi1", spi_parents, 0x2a4,
		    0, 1, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(spi2, "spi2", spi_parents, 0x2b0,
		    0, 1, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(spi3, "spi3", spi_parents, 0x2bc,
		    0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data analog_io_apb_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_48m.hw },
};
static SPRD_MUX_CLK_DATA(analog_io_apb, "analog-io-apb", analog_io_apb_parents, 0x2d4,
		    0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data dmc_ref_parents[] = {
	{ .hw = &clk_6m5.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_MUX_CLK_DATA(dmc_ref, "dmc-ref", dmc_ref_parents, 0x2e0,
		    0, 1, UMS9632_MUX_FLAG);

static const struct clk_parent_data usb_parents[] = {
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
	{ .hw = &rco_150m_75m.hw },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(usb, "usb", usb_parents, 0x2ec,
			    0, 2, -4, 0, 2, 0);

static const struct clk_parent_data noc_ref_parents[] = {
	{ .fw_name = "ext-32k" },
	{ .hw = &rco_150m_4m05.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_COMP_CLK_DATA_OFFSET(noc_ref, "noc-ref", noc_ref_parents, 0x358,
			    0, 2, -4, 0, 2, 0);

static const struct clk_parent_data bisr_26m_parents[] = {
	{ .hw = &rco_150m_25m.hw },
	{ .fw_name = "ext-26m" },
};
static SPRD_MUX_CLK_DATA(bisr_26m, "bisr-26m", bisr_26m_parents, 0x364,
		    0, 1, UMS9632_MUX_FLAG);

static struct sprd_clk_common *ums9632_aonapb_clks[] = {
	/* address base is 0x64920000 */
	&aon_apb.common,
	&adi.common,
	&pwm0.common,
	&pwm1.common,
	&pwm2.common,
	&pwm3.common,
	&efuse.common,
	&thm0.common,
	&thm1.common,
	&pri_sbi.common,
	&aon_tmr.common,
	&tzpc.common,
	&aon_pmu.common,
	&debounce.common,
	&apcpu_pmu.common,
	&top_dvfs.common,
	&uart0.common,
	&uart1.common,
	&uart2.common,
	&uart3.common,
	&apcpu_dap.common,
	&apcpu_ts.common,
	&debug_ts.common,
	&djtag_tck.common,
	&det_ref.common,
	&cssys.common,
	&cssys_apb.common,
	&sdio0_2x.common,
	&sdio0_1x.common,
	&sdio1_2x.common,
	&sdio1_1x.common,
	&spi0.common,
	&spi1.common,
	&spi2.common,
	&spi3.common,
	&analog_io_apb.common,
	&dmc_ref.common,
	&usb.common,
	&noc_ref.common,
	&bisr_26m.common,
};

static struct clk_hw_onecell_data ums9632_aonapb_clk_hws = {
	.hws	= {
		[CLK_AON_APB]		= &aon_apb.common.hw,
		[CLK_ADI]		= &adi.common.hw,
		[CLK_PWM0]		= &pwm0.common.hw,
		[CLK_PWM1]		= &pwm1.common.hw,
		[CLK_PWM2]		= &pwm2.common.hw,
		[CLK_PWM3]		= &pwm3.common.hw,
		[CLK_EFUSE]		= &efuse.common.hw,
		[CLK_THM0]		= &thm0.common.hw,
		[CLK_THM1]		= &thm1.common.hw,
		[CLK_PRI_SBI]		= &pri_sbi.common.hw,
		[CLK_AON_TMR]		= &aon_tmr.common.hw,
		[CLK_TZPC]		= &tzpc.common.hw,
		[CLK_AON_PMU]		= &aon_pmu.common.hw,
		[CLK_DEBOUNCE]		= &debounce.common.hw,
		[CLK_APCPU_PMU]		= &apcpu_pmu.common.hw,
		[CLK_TOP_DVFS]		= &top_dvfs.common.hw,
		[CLK_UART0]		= &uart0.common.hw,
		[CLK_UART1]		= &uart1.common.hw,
		[CLK_UART2]		= &uart2.common.hw,
		[CLK_UART3]		= &uart3.common.hw,
		[CLK_APCPU_DAP]		= &apcpu_dap.common.hw,
		[CLK_APCPU_TS]		= &apcpu_ts.common.hw,
		[CLK_DEBUG_TS]		= &debug_ts.common.hw,
		[CLK_DJTAG_TCK]		= &djtag_tck.common.hw,
		[CLK_DET_REF]		= &det_ref.common.hw,
		[CLK_CSSYS]		= &cssys.common.hw,
		[CLK_CSSYS_APB]		= &cssys_apb.common.hw,
		[CLK_SDIO0_2X]		= &sdio0_2x.common.hw,
		[CLK_SDIO0_1X]		= &sdio0_1x.common.hw,
		[CLK_SDIO1_2X]		= &sdio1_2x.common.hw,
		[CLK_SDIO1_1X]		= &sdio1_1x.common.hw,
		[CLK_SPI0]		= &spi0.common.hw,
		[CLK_SPI1]		= &spi1.common.hw,
		[CLK_SPI2]		= &spi2.common.hw,
		[CLK_SPI3]		= &spi3.common.hw,
		[CLK_ANALOG_IO_APB]	= &analog_io_apb.common.hw,
		[CLK_DMC_REF]		= &dmc_ref.common.hw,
		[CLK_USB]		= &usb.common.hw,
		[CLK_NOC_REF]		= &noc_ref.common.hw,
		[CLK_BISR_26M]		= &bisr_26m.common.hw,
	},
	.num	= CLK_AON_APB_NUM,
};

static struct sprd_clk_desc ums9632_aonapb_clk_desc = {
	.clk_clks	= ums9632_aonapb_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_aonapb_clks),
	.hw_clks	= &ums9632_aonapb_clk_hws,
};

/* top dvfs clk */
static const struct clk_parent_data lit_core_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &v4nrpll_614m4.hw },
	{ .hw = &tgpll_768m.hw },
	{ .hw = &v4nrpll_1228m8.hw },
	{ .hw = &mplll.common.hw },
};
static SPRD_COMP_CLK_DATA(core0, "core0", lit_core_parents, 0xe08,
		     0, 3, 3, 1, 0);
static SPRD_COMP_CLK_DATA(core1, "core1", lit_core_parents, 0xe08,
		     4, 3, 7, 1, 0);
static SPRD_COMP_CLK_DATA(core2, "core2", lit_core_parents, 0xe08,
		     8, 3, 11, 1, 0);
static SPRD_COMP_CLK_DATA(core3, "core3", lit_core_parents, 0xe08,
		     12, 3, 15, 1, 0);
static SPRD_COMP_CLK_DATA(core4, "core4", lit_core_parents, 0xe08,
		     16, 3, 19, 1, 0);
static SPRD_COMP_CLK_DATA(core5, "core5", lit_core_parents, 0xe08,
		     20, 3, 23, 1, 0);

static const struct clk_parent_data mid_core_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_768m.hw },
	{ .hw = &v4nrpll_1228m8.hw },
	{ .hw = &tgpll.common.hw },
	{ .hw = &mpllm.common.hw },
};
static SPRD_COMP_CLK_DATA(core6, "core6", mid_core_parents, 0xe08,
		     24, 3, 27, 1, 0);
static SPRD_COMP_CLK_DATA(core7, "core7", mid_core_parents, 0xe08,
		     28, 3, 31, 1, 0);

static const struct clk_parent_data scu_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &v4nrpll_409m6.hw },
	{ .hw = &tgpll_768m.hw },
	{ .hw = &v4nrpll_1228m8.hw },
	{ .hw = &mplls.common.hw },
};
static SPRD_COMP_CLK_DATA(scu, "scu", scu_parents, 0xe0c,
		     0, 3, 3, 1, 0);
static SPRD_DIV_CLK_HW(ace, "ace", &scu.common.hw, 0xe0c,
		    24, 1, 0);

static const struct clk_parent_data atb_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &tgpll_768m.hw },
};
static SPRD_COMP_CLK_DATA(atb, "atb", atb_parents, 0xe0c,
		     4, 2, 6, 3, 0);
static SPRD_DIV_CLK_HW(debug_apb, "debug_apb", &atb.common.hw, 0xe0c,
		    25, 2, 0);

static const struct clk_parent_data gic_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &tgpll_768m.hw },
};
static SPRD_COMP_CLK_DATA(gic, "gic", gic_parents, 0xe0c,
		     14, 2, 16, 3, 0);

static const struct clk_parent_data periph_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &tgpll_768m.hw },
};
static SPRD_COMP_CLK_DATA(periph, "periph", periph_parents, 0xe0c,
		     19, 2, 21, 3, 0);

static struct sprd_clk_common *ums9632_topdvfs_clks[] = {
	/* address base is 0x64940000 */
	&core0.common,
	&core1.common,
	&core2.common,
	&core3.common,
	&core4.common,
	&core5.common,
	&core6.common,
	&core7.common,
	&scu.common,
	&ace.common,
	&atb.common,
	&debug_apb.common,
	&gic.common,
	&periph.common,
};

static struct clk_hw_onecell_data ums9632_topdvfs_clk_hws = {
	.hws    = {
		[CLK_CORE0]	= &core0.common.hw,
		[CLK_CORE1]	= &core1.common.hw,
		[CLK_CORE2]	= &core2.common.hw,
		[CLK_CORE3]	= &core3.common.hw,
		[CLK_CORE4]	= &core4.common.hw,
		[CLK_CORE5]	= &core5.common.hw,
		[CLK_CORE6]	= &core6.common.hw,
		[CLK_CORE7]	= &core7.common.hw,
		[CLK_SCU]	= &scu.common.hw,
		[CLK_ACE]	= &ace.common.hw,
		[CLK_ATB]	= &atb.common.hw,
		[CLK_DEBUG_APB]	= &debug_apb.common.hw,
		[CLK_GIC]	= &gic.common.hw,
		[CLK_PERIPH]	= &periph.common.hw,
	},
	.num    = CLK_TOPDVFS_CLK_NUM,
};

static struct sprd_clk_desc ums9632_topdvfs_clk_desc = {
	.clk_clks	= ums9632_topdvfs_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_topdvfs_clks),
	.hw_clks	= &ums9632_topdvfs_clk_hws,
};

/* mm apb gate clocks */
static SPRD_SC_GATE_CLK_HW(ckg_en, "ckg-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dvfs_en, "dvfs-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sys_mtx_cfg_en, "sys_mtx-cfg-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sys_mtx_axi_mon_en, "sys-mtx-axi-mon-en", &mm_eb.common.hw,
			0x0, 0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sys_tck_en, "sys-tck-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(mm_mtx_data_en, "mm-mtx-data-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(snr_en, "snr-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(mm_sys_cfg_noc_ll_en, "mm-sys-cfg_noc-ll-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(10), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(mm_sys_cfg_noc_hb_en, "mm-sys-cfg-noc-hb-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(mm_s_t_mainexten, "mm-s-t-mainexten", &mm_eb.common.hw, 0x0,
			0x1000, BIT(12), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(noc_axi_trans_mon_en, "noc-axi-trans-mon-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(13), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(noc_axi2apb_auto_en, "noc-axi2apb-auto-en", &mm_eb.common.hw, 0x0,
			0x1000, BIT(14), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(isp_en, "isp-en", &mm_eb.common.hw, 0x4,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(cpp_en, "cpp-en", &mm_eb.common.hw, 0x4,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(isp_mtx_en, "isp-mtx-en", &mm_eb.common.hw, 0x4,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(isp_blk_cfg_en, "isp-blk-cfg-en", &mm_eb.common.hw, 0x4,
			0x1000, BIT(3), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(isp_blk_mtx_axi_mon_en, "isp-blk-mtx-axi-mon-en", &mm_eb.common.hw,
			0x4, 0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(isp_tck_en, "isp-tck-en", &mm_eb.common.hw, 0x4,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dcam_if_en, "dcam-if-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dcam_if_lite_en, "dcam-if-lite-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(phy_cfg_en, "phy-cfg-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dcam_blk_cfg_en, "dcam-blk-cfg-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(3), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sensor0_en, "sensor0-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sensor1_en, "sensor1-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sensor2_en, "sensor2-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sensor3_en, "sensor3-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(sensor4_en, "sensor4-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(8), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(csi_sw_en, "csi-sw-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dcam_tck_en, "dcam-tck-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(10), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(csi0_en, "csi0-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(csi1_en, "csi1-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(12), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(csi2_en, "csi2-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(13), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(ipa_en, "ipa-en", &mm_eb.common.hw, 0x8,
			0x1000, BIT(14), CLK_IGNORE_UNUSED, 0);

static struct sprd_clk_common *ums9632_mm_gate_clks[] = {
	/* address base is 0x33000000 */
	&ckg_en.common,
	&dvfs_en.common,
	&sys_mtx_cfg_en.common,
	&sys_mtx_axi_mon_en.common,
	&sys_tck_en.common,
	&mm_mtx_data_en.common,
	&snr_en.common,
	&mm_sys_cfg_noc_ll_en.common,
	&mm_sys_cfg_noc_hb_en.common,
	&mm_s_t_mainexten.common,
	&noc_axi_trans_mon_en.common,
	&noc_axi2apb_auto_en.common,
	&isp_en.common,
	&cpp_en.common,
	&isp_mtx_en.common,
	&isp_blk_cfg_en.common,
	&isp_blk_mtx_axi_mon_en.common,
	&isp_tck_en.common,
	&dcam_if_en.common,
	&dcam_if_lite_en.common,
	&phy_cfg_en.common,
	&dcam_blk_cfg_en.common,
	&sensor0_en.common,
	&sensor1_en.common,
	&sensor2_en.common,
	&sensor3_en.common,
	&sensor4_en.common,
	&csi_sw_en.common,
	&dcam_tck_en.common,
	&csi0_en.common,
	&csi1_en.common,
	&csi2_en.common,
	&ipa_en.common,
};

static struct clk_hw_onecell_data ums9632_mm_gate_hws = {
	.hws	= {
		[CLK_CKG_EN]			= &ckg_en.common.hw,
		[CLK_DVFS_EN]			= &dvfs_en.common.hw,
		[CLK_SYS_MTX_CFG_EN]		= &sys_mtx_cfg_en.common.hw,
		[CLK_SYS_MTX_AXI_MON_EN]	= &sys_mtx_axi_mon_en.common.hw,
		[CLK_SYS_TCK_EN]		= &sys_tck_en.common.hw,
		[CLK_MM_MTX_DATA_EN]		= &mm_mtx_data_en.common.hw,
		[CLK_SNR_EN]			= &snr_en.common.hw,
		[CLK_MM_SYS_CFG_NOC_LL_EN]	= &mm_sys_cfg_noc_ll_en.common.hw,
		[CLK_MM_SYS_CFG_NOC_HB_EN]	= &mm_sys_cfg_noc_hb_en.common.hw,
		[CLK_MM_S_T_MAINEXTEN]		= &mm_s_t_mainexten.common.hw,
		[CLK_NOC_AXI_TRANS_MON_EN]	= &noc_axi_trans_mon_en.common.hw,
		[CLK_NOC_AXI2APB_AUTO_EN]	= &noc_axi2apb_auto_en.common.hw,
		[CLK_ISP_EN]			= &isp_en.common.hw,
		[CLK_CPP_EN]			= &cpp_en.common.hw,
		[CLK_ISP_MTX_EN]		= &isp_mtx_en.common.hw,
		[CLK_ISP_BLK_CFG_EN]		= &isp_blk_cfg_en.common.hw,
		[CLK_ISP_BLK_MTX_AXI_MON_EN]	= &isp_blk_mtx_axi_mon_en.common.hw,
		[CLK_ISP_TCK_EN]		= &isp_tck_en.common.hw,
		[CLK_DCAM_IF_EN]		= &dcam_if_en.common.hw,
		[CLK_DCAM_IF_LITE_EN]		= &dcam_if_lite_en.common.hw,
		[CLK_PHY_CFG_EN]		= &phy_cfg_en.common.hw,
		[CLK_DCAM_BLK_CFG_EN]		= &dcam_blk_cfg_en.common.hw,
		[CLK_SENSOR0_EN]		= &sensor0_en.common.hw,
		[CLK_SENSOR1_EN]		= &sensor1_en.common.hw,
		[CLK_SENSOR2_EN]		= &sensor2_en.common.hw,
		[CLK_SENSOR3_EN]		= &sensor3_en.common.hw,
		[CLK_SENSOR4_EN]		= &sensor4_en.common.hw,
		[CLK_CSI_SW_EN]			= &csi_sw_en.common.hw,
		[CLK_DCAM_TCK_EN]		= &dcam_tck_en.common.hw,
		[CLK_CSI0_EN]			= &csi0_en.common.hw,
		[CLK_CSI1_EN]			= &csi1_en.common.hw,
		[CLK_CSI2_EN]			= &csi2_en.common.hw,
		[CLK_IPA_EN]			= &ipa_en.common.hw,
	},
	.num	= CLK_MM_GATE_NUM,
};

static struct sprd_reset_map ums9632_mm_apb_resets[] = {
	[RESET_MM_APB_REGU_SOFT_RST]		= { 0x0050, BIT(0), true, 0x1000 },
	[RESET_MM_APB_DCAM0_1_ALL_SOFT_RST]	= { 0x0050, BIT(1), true, 0x1000 },
	[RESET_MM_APB_DCAM2_3_ALL_SOFT_RST]	= { 0x0050, BIT(2), true, 0x1000 },
	[RESET_MM_APB_DCAM0_1_AXI_SOFT_RST]	= { 0x0050, BIT(3), true, 0x1000 },
	[RESET_MM_APB_DCAM3_SOFT_RST]		= { 0x0050, BIT(4), true, 0x1000 },
	[RESET_MM_APB_DCAM2_SOFT_RST]		= { 0x0050, BIT(5), true, 0x1000 },
	[RESET_MM_APB_DCAM1_SOFT_RST]		= { 0x0050, BIT(6), true, 0x1000 },
	[RESET_MM_APB_DCAM0_SOFT_RST]		= { 0x0050, BIT(7), true, 0x1000 },
	[RESET_MM_APB_DCAM3_MIPI_CAP_SOFT_RST]	= { 0x0050, BIT(8), true, 0x1000 },
	[RESET_MM_APB_DCAM2_MIPI_CAP_SOFT_RST]	= { 0x0050, BIT(9), true, 0x1000 },
	[RESET_MM_APB_DCAM1_MIPI_CAP_SOFT_RST]	= { 0x0050, BIT(10), true, 0x1000 },
	[RESET_MM_APB_DCAM0_MIPI_CAP_SOFT_RST]	= { 0x0050, BIT(11), true, 0x1000 },
	[RESET_MM_APB_DCAM2_3_AXI_SOFT_RST]	= { 0x0050, BIT(12), true, 0x1000 },
	[RESET_MM_APB_DCAM2_3_VAU_SOFT_RST]	= { 0x0050, BIT(13), true, 0x1000 },
	[RESET_MM_APB_DCAM0_1_VAU_SOFT_RST]	= { 0x0050, BIT(14), true, 0x1000 },
	[RESET_MM_APB_DCAM0_1_FMCU_SOFT_RST]	= { 0x0050, BIT(15), true, 0x1000 },
	[RESET_MM_APB_CSI_SWITCH_SOFT_RST]	= { 0x0050, BIT(16), true, 0x1000 },
	[RESET_MM_APB_DCAM0_CFG_SOFT_RST]	= { 0x0050, BIT(17), true, 0x1000 },
	[RESET_MM_APB_DCAM1_CFG_SOFT_RST]	= { 0x0050, BIT(18), true, 0x1000 },
	[RESET_MM_APB_CPP_DMA_SOFT_RST]		= { 0x0054, BIT(0), true, 0x1000 },
	[RESET_MM_APB_CPP_PATH1_SOFT_RST]	= { 0x0054, BIT(1), true, 0x1000 },
	[RESET_MM_APB_CPP_PATH0_SOFT_RST]	= { 0x0054, BIT(2), true, 0x1000 },
	[RESET_MM_APB_CPP_VAU_SOFT_RST]		= { 0x0054, BIT(3), true, 0x1000 },
	[RESET_MM_APB_CPP_SOFT_RST]		= { 0x0054, BIT(4), true, 0x1000 },
	[RESET_MM_APB_CPP_ALL_SOFT_RST]		= { 0x0054, BIT(5), true, 0x1000 },
	[RESET_MM_APB_ISP_VAU_SOFT_RST]		= { 0x0054, BIT(6), true, 0x1000 },
	[RESET_MM_APB_ISP_ALL_SOFT_RST]		= { 0x0054, BIT(7), true, 0x1000 },
	[RESET_MM_APB_ISP_SOFT_RST]		= { 0x0054, BIT(8), true, 0x1000 },
	[RESET_MM_APB_ISP_AHB_SOFT_RST]		= { 0x0054, BIT(9), true, 0x1000 },
	[RESET_MM_APB_CKG_SOFT_RST]		= { 0x0058, BIT(0), true, 0x1000 },
	[RESET_MM_APB_DVFS_SOFT_RST]		= { 0x0058, BIT(1), true, 0x1000 },
};

static struct sprd_clk_desc ums9632_mm_gate_desc = {
	.clk_clks	= ums9632_mm_gate_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_mm_gate_clks),
	.hw_clks	= &ums9632_mm_gate_hws,
	.resets		= ums9632_mm_apb_resets,
	.num_resets	= ARRAY_SIZE(ums9632_mm_apb_resets),
};

/* mm clocks */
static const struct clk_parent_data mipi_csi0_parents[] = {
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &v4nrpll_409m6.hw },
	{ .hw = &v4nrpll_614m4.hw },
	{ .hw = &tgpll_768m.hw },
};
static SPRD_MUX_CLK_DATA(mipi_csi0, "mipi-csi0", mipi_csi0_parents, 0x28,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data mipi_csi1_1_parents[] = {
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
};
static SPRD_MUX_CLK_DATA(mipi_csi1_1, "mipi-csi1_1",  mipi_csi1_1_parents,
			0x34, 0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data mipi_csi1_2_parents[] = {
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
};
static SPRD_MUX_CLK_DATA(mipi_csi1_2, "mipi-csi1_2",  mipi_csi1_2_parents,
			0x40, 0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data mipi_csi2_1_parents[] = {
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
};
static SPRD_MUX_CLK_DATA(mipi_csi2_1, "mipi-csi2_1",  mipi_csi2_1_parents,
		    0x4c, 0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data mipi_csi2_2_parents[] = {
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
};
static SPRD_MUX_CLK_DATA(mipi_csi2_2, "mipi-csi2_2",  mipi_csi2_2_parents,
		    0x58, 0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data dcam_blk_cfg_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_48m.hw },
	{ .hw = &tgpll_64m.hw },
	{ .hw = &tgpll_96m.hw },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(dcam_blk_cfg, "dcam-blk-cfg", dcam_blk_cfg_parents,
		    0x70, 0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data mm_sys_cfg_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_48m.hw },
	{ .hw = &tgpll_64m.hw },
	{ .hw = &tgpll_96m.hw },
	{ .hw = &tgpll_128m.hw },
};
static SPRD_MUX_CLK_DATA(mm_sys_cfg, "mm-sys-cfg", mm_sys_cfg_parents,
		    0x7c, 0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data sensor_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_48m.hw },
	{ .hw = &tgpll_51m2.hw },
	{ .hw = &tgpll_64m.hw },
	{ .hw = &tgpll_96m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(sensor0, "sensor0", sensor_parents, 0x88,
		     0, 3, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(sensor1, "sensor1", sensor_parents, 0x94,
		     0, 3, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(sensor2, "sensor2", sensor_parents, 0xa0,
		     0, 3, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(sensor3, "sensor3", sensor_parents, 0xac,
		     0, 3, -4, 0, 3, 0);
static SPRD_COMP_CLK_DATA_OFFSET(sensor4, "sensor4", sensor_parents, 0xb8,
		     0, 3, -4, 0, 3, 0);

static struct sprd_clk_common *ums9632_mm_clks[] = {
	/* address base is 0x3301fc00 */
	&mipi_csi0.common,
	&mipi_csi1_1.common,
	&mipi_csi1_2.common,
	&mipi_csi2_1.common,
	&mipi_csi2_2.common,
	&dcam_blk_cfg.common,
	&mm_sys_cfg.common,
	&sensor0.common,
	&sensor1.common,
	&sensor2.common,
	&sensor3.common,
	&sensor4.common,
};

static struct clk_hw_onecell_data ums9632_mm_clk_hws = {
	.hws	= {
		[CLK_MIPI_CSI0]		= &mipi_csi0.common.hw,
		[CLK_MIPI_CSI1_1]	= &mipi_csi1_1.common.hw,
		[CLK_MIPI_CSI1_2]	= &mipi_csi1_2.common.hw,
		[CLK_MIPI_CSI2_1]	= &mipi_csi2_1.common.hw,
		[CLK_MIPI_CSI2_2]	= &mipi_csi2_2.common.hw,
		[CLK_DCAM_BLK_CFG]	= &dcam_blk_cfg.common.hw,
		[CLK_MM_SYS_CFG]	= &mm_sys_cfg.common.hw,
		[CLK_SENSOR0]		= &sensor0.common.hw,
		[CLK_SENSOR1]		= &sensor1.common.hw,
		[CLK_SENSOR2]		= &sensor2.common.hw,
		[CLK_SENSOR3]		= &sensor3.common.hw,
		[CLK_SENSOR4]		= &sensor4.common.hw,
	},
	.num	= CLK_MM_CLK_NUM,
};

static struct sprd_clk_desc ums9632_mm_clk_desc = {
	.clk_clks	= ums9632_mm_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_mm_clks),
	.hw_clks	= &ums9632_mm_clk_hws,
};

/* mm dvfs clocks */
static const struct clk_parent_data isp_parents[] = {
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &v4nrpll_409m6.hw },
	{ .hw = &tgpll_512m.hw },
};
static SPRD_MUX_CLK_DATA(isp, "isp", isp_parents, 0x100,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data cpp_parents[] = {
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
};
static SPRD_MUX_CLK_DATA(cpp, "cpp", cpp_parents, 0x104,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data dcam0_1_parents[] = {
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &v4nrpll_409m6.hw },
	{ .hw = &tgpll_512m.hw },
};
static SPRD_MUX_CLK_DATA(dcam0_1, "dcam0-1", dcam0_1_parents, 0x108,
		    0, 3, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(dcam0_1_axi, "dcam0-1-axi", dcam0_1_parents, 0x10c,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data dcam2_3_parents[] = {
	{ .hw = &tgpll_96m.hw },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_192m.hw },
	{ .hw = &tgpll_256m.hw },
};
static SPRD_MUX_CLK_DATA(dcam2_3, "dcam2-3", dcam2_3_parents, 0x110,
		    0, 3, UMS9632_MUX_FLAG);
static SPRD_MUX_CLK_DATA(dcam2_3_axi, "dcam2-3-axi", dcam2_3_parents, 0x114,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data mm_mtx_data_parents[] = {
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &v4nrpll_409m6.hw },
	{ .hw = &tgpll_512m.hw },
};
static SPRD_MUX_CLK_DATA(mm_mtx_data, "mm-mtx-data", mm_mtx_data_parents,
		    0x118, 0, 3, UMS9632_MUX_FLAG);

static struct sprd_clk_common *ums9632_mm_dvfs_clks[] = {
	/* address base is 0x33014000 */
	&isp.common,
	&cpp.common,
	&dcam0_1.common,
	&dcam0_1_axi.common,
	&dcam2_3.common,
	&dcam2_3_axi.common,
	&mm_mtx_data.common,
};

static struct clk_hw_onecell_data ums9632_mm_dvfs_clk_hws = {
	.hws	= {
		[CLK_ISP]		= &isp.common.hw,
		[CLK_CPP]		= &cpp.common.hw,
		[CLK_DCAM0_1]		= &dcam0_1.common.hw,
		[CLK_DCAM0_1_AXI]	= &dcam0_1_axi.common.hw,
		[CLK_DCAM2_3]		= &dcam2_3.common.hw,
		[CLK_DCAM2_3_AXI]	= &dcam2_3_axi.common.hw,
		[CLK_MM_MTX_DATA]	= &mm_mtx_data.common.hw,
	},
	.num	= CLK_MM_DVFS_CLK_NUM,
};

static struct sprd_clk_desc ums9632_mm_dvfs_clk_desc = {
	.clk_clks	= ums9632_mm_dvfs_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_mm_dvfs_clks),
	.hw_clks	= &ums9632_mm_dvfs_clk_hws,
};

/* dpu vsp apb clock gates */
static SPRD_SC_GATE_CLK_HW(dpu_eb, "dpu-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dsc0_eb, "dsc0-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(1), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dsi0_eb, "dsi0-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(2), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(vpu_enc0_eb, "vpu-enc0-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(3), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(vpu_dec_eb, "dpu-dec-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(4), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(gsp0_eb, "gsp0-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(5), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(jpg_eb, "jpg-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(6), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dpu_dvfs_eb, "dpu-dvfs-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(7), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dpu_ckg_eb, "dpu-ckg-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(8), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(dpu_busmon_eb, "dpu-busmon-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(9), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(noc_disp_eb, "noc-disp-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(10), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(noc_vpu_eb, "noc-vpu-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(11), CLK_IGNORE_UNUSED, 0);
static SPRD_SC_GATE_CLK_HW(vol_snr_eb, "vol-snr-eb", &dpu_vsp_eb.common.hw, 0x0,
			0x1000, BIT(12), CLK_IGNORE_UNUSED, 0);
static SPRD_GATE_CLK_HW(mipi_dphy_m_enable, "mipi-dphy-m-enable", &dpu_vsp_eb.common.hw,
		     0x60, BIT(0), CLK_IGNORE_UNUSED, 0);
static SPRD_GATE_CLK_HW(m_div6clk_gate_en, "m-div6clk-gate-en", &dpu_vsp_eb.common.hw,
		     0x60, BIT(1), CLK_IGNORE_UNUSED, 0);

static struct sprd_clk_common *ums9632_dpu_vsp_gate_clks[] = {
	/* address base is 0x30100000 */
	&dpu_eb.common,
	&dsc0_eb.common,
	&dsi0_eb.common,
	&vpu_enc0_eb.common,
	&vpu_dec_eb.common,
	&gsp0_eb.common,
	&jpg_eb.common,
	&dpu_dvfs_eb.common,
	&dpu_ckg_eb.common,
	&dpu_busmon_eb.common,
	&noc_disp_eb.common,
	&noc_vpu_eb.common,
	&vol_snr_eb.common,
	&mipi_dphy_m_enable.common,
	&m_div6clk_gate_en.common,
};

static struct clk_hw_onecell_data ums9632_dpu_vsp_gate_hws = {
	.hws    = {
		[CLK_DPU_EB]			= &dpu_eb.common.hw,
		[CLK_DSC0_EB]			= &dsc0_eb.common.hw,
		[CLK_DSI0_EB]			= &dsi0_eb.common.hw,
		[CLK_VPU_ENC0_EB]		= &vpu_enc0_eb.common.hw,
		[CLK_VPU_DEC_EB]		= &vpu_dec_eb.common.hw,
		[CLK_GSP0_EB]			= &gsp0_eb.common.hw,
		[CLK_JPG_EB]			= &jpg_eb.common.hw,
		[CLK_DPU_DVFS_EB]		= &dpu_dvfs_eb.common.hw,
		[CLK_DPU_CKG_EB]		= &dpu_ckg_eb.common.hw,
		[CLK_DPU_BUSMON_EB]		= &dpu_busmon_eb.common.hw,
		[CLK_NOC_DISP_EB]		= &noc_disp_eb.common.hw,
		[CLK_NOC_VPU_EB]		= &noc_vpu_eb.common.hw,
		[CLK_VOL_SNR_EB]		= &vol_snr_eb.common.hw,
		[CLK_MIPI_DPHY_M_ENABLE]	= &mipi_dphy_m_enable.common.hw,
		[CLK_M_DIV6CLK_GATE_EN]		= &m_div6clk_gate_en.common.hw,
	},
	.num    = CLK_DPU_VSP_GATE_NUM,
};

static struct sprd_reset_map ums9632_dpu_vsp_resets[] = {
	[RESET_DPU_VSP_APB_DPU_SOFT_RST]		= { 0x0004, BIT(0), true, 0x1000 },
	[RESET_DPU_VSP_APB_DPU_VAU_SOFT_RST]		= { 0x0004, BIT(1), true, 0x1000 },
	[RESET_DPU_VSP_APB_DSI0_SOFT_RST]		= { 0x0004, BIT(2), true, 0x1000 },
	[RESET_DPU_VSP_APB_VPU_ENC0_SOFT_RST]		= { 0x0004, BIT(3), true, 0x1000 },
	[RESET_DPU_VSP_APB_VPU_ENC0_VSP_SOFT_RST]	= { 0x0004, BIT(4), true, 0x1000 },
	[RESET_DPU_VSP_APB_VPU_ENC0_VAU_SOFT_RST]	= { 0x0004, BIT(5), true, 0x1000 },
	[RESET_DPU_VSP_APB_VPU_DEC_SOFT_RST]		= { 0x0004, BIT(6), true, 0x1000 },
	[RESET_DPU_VSP_APB_VPU_DEC_VSP_SOFT_RST]	= { 0x0004, BIT(7), true, 0x1000 },
	[RESET_DPU_VSP_APB_VPU_DEC_VPP_SOFT_RST]	= { 0x0004, BIT(8), true, 0x1000 },
	[RESET_DPU_VSP_APB_VPU_DEC_VAU_SOFT_RST]	= { 0x0004, BIT(9), true, 0x1000 },
	[RESET_DPU_VSP_APB_GSP0_SOFT_RST]		= { 0x0004, BIT(10), true, 0x1000 },
	[RESET_DPU_VSP_APB_GSP0_VAU_SOFT_RST]		= { 0x0004, BIT(11), true, 0x1000 },
	[RESET_DPU_VSP_APB_JPG_SOFT_RST]		= { 0x0004, BIT(12), true, 0x1000 },
	[RESET_DPU_VSP_APB_JPG_VAU_SOFT_RST]		= { 0x0004, BIT(13), true, 0x1000 },
	[RESET_DPU_VSP_APB_DVFS_SOFT_RST]		= { 0x0004, BIT(14), true, 0x1000 },
	[RESET_DPU_VSP_APB_SYS_SOFT_RST_REQ_DISP]	= { 0x0054, BIT(0), true, 0x1000 },
	[RESET_DPU_VSP_APB_SYS_SOFT_RST_REQ_VPU_ENC0]	= { 0x0054, BIT(1), true, 0x1000 },
	[RESET_DPU_VSP_APB_SYS_SOFT_RST_REQ_VPU_DEC]	= { 0x0054, BIT(2), true, 0x1000 },
	[RESET_DPU_VSP_APB_SYS_SOFT_RST_REQ_GSP0]	= { 0x0054, BIT(3), true, 0x1000 },
	[RESET_DPU_VSP_APB_SYS_SOFT_RST_REQ_JPG]	= { 0x0054, BIT(4), true, 0x1000 },
};

static struct sprd_clk_desc ums9632_dpu_vsp_gate_desc = {
	.clk_clks	= ums9632_dpu_vsp_gate_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_dpu_vsp_gate_clks),
	.hw_clks	= &ums9632_dpu_vsp_gate_hws,
	.resets		= ums9632_dpu_vsp_resets,
	.num_resets	= ARRAY_SIZE(ums9632_dpu_vsp_resets),
};

/* dpu_vsp clocks */
static const struct clk_parent_data dpu_cfg_parents[] = {
	{ .fw_name = "ext-26m" },
	{ .hw = &tgpll_128m.hw },
	{ .hw = &tgpll_153m6.hw },
};
static SPRD_MUX_CLK_DATA(dpu_cfg, "dpu-cfg", dpu_cfg_parents, 0x28,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data dispc0_dpi_parents[] = {
	{ .hw = &pixelpll_200m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .fw_name = "dphy-312m5" },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &pixelpll_400m.hw },
	{ .fw_name = "dphy-416m7" },
	{ .hw = &pixelpll_420m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(dispc0_dpi, "dispc0-dpi", dispc0_dpi_parents, 0x34,
			    0, 3, -4, 0, 3, 0);

static const struct clk_parent_data dispc0_dsc_parents[] = {
	{ .hw = &pixelpll_200m.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .fw_name = "dphy-312m5" },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &pixelpll_400m.hw },
	{ .fw_name = "dphy-416m7" },
	{ .hw = &pixelpll_420m.hw },
};
static SPRD_COMP_CLK_DATA_OFFSET(dispc0_dsc, "dispc0-dsc", dispc0_dsc_parents, 0x40,
				0, 3, -4, 0, 4, 0);

static struct sprd_clk_common *ums9632_dpu_vsp_clks[] = {
	/* address base is 0x30110000 */
	&dpu_cfg.common,
	&dispc0_dpi.common,
	&dispc0_dsc.common,
};

static struct clk_hw_onecell_data ums9632_dpu_vsp_clk_hws = {
	.hws    = {
		[CLK_DPU_CFG]		= &dpu_cfg.common.hw,
		[CLK_DISPC0_DPI]	= &dispc0_dpi.common.hw,
		[CLK_DISPC0_DSC]	= &dispc0_dsc.common.hw,
	},
	.num    = CLK_DPU_VSP_CLK_NUM,
};

static struct sprd_clk_desc ums9632_dpu_vsp_clk_desc = {
	.clk_clks	= ums9632_dpu_vsp_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_dpu_vsp_clks),
	.hw_clks	= &ums9632_dpu_vsp_clk_hws,
};

/* dpu_vsp dvfs clocks */
static const struct clk_parent_data vpu_mtx_parents[] = {
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &v4nrpll_614m4.hw },
};
static SPRD_MUX_CLK_DATA(vpu_mtx, "vpu-mtx", vpu_mtx_parents, 0x100,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data vpu_enc_parents[] = {
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &tgpll_512m.hw },
};
static SPRD_MUX_CLK_DATA(vpu_enc, "vpu-enc", vpu_enc_parents, 0x104,
		    0, 2, UMS9632_MUX_FLAG);

static const struct clk_parent_data vpu_dec_parents[] = {
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &v4nrpll_614m4.hw },
};
static SPRD_MUX_CLK_DATA(vpu_dec, "vpu-dec", vpu_dec_parents, 0x108,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data gsp_parents[] = {
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &v4nrpll_614m4.hw },
	{ .hw = &tgpll_768m.hw },
};
static SPRD_MUX_CLK_DATA(gsp0, "gsp0", gsp_parents, 0x10C,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data dispc0_parents[] = {
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &tgpll_384m.hw },
	{ .hw = &v4nrpll_409m6.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &v4nrpll_614m4.hw },
};
static SPRD_MUX_CLK_DATA(dispc0, "dispc0", dispc0_parents, 0x110,
		    0, 3, UMS9632_MUX_FLAG);

static const struct clk_parent_data jpg_parents[] = {
	{ .hw = &tgpll_153m6.hw },
	{ .hw = &tgpll_256m.hw },
	{ .hw = &tgpll_307m2.hw },
	{ .hw = &v4nrpll_409m6.hw },
	{ .hw = &tgpll_512m.hw },
	{ .hw = &v4nrpll_614m4.hw },
	{ .hw = &tgpll_768m.hw },
};
static SPRD_MUX_CLK_DATA(jpg, "jpg", jpg_parents, 0x114,
			0, 3, UMS9632_MUX_FLAG);

static struct sprd_clk_common *ums9632_dpu_vsp_dvfs_clks[] = {
	/* address base is 0x30114000 */
	&vpu_mtx.common,
	&vpu_enc.common,
	&vpu_dec.common,
	&gsp0.common,
	&dispc0.common,
	&jpg.common,
};

static struct clk_hw_onecell_data ums9632_dpu_vsp_dvfs_clk_hws = {
	.hws    = {
		[CLK_VPU_MTX]		= &vpu_mtx.common.hw,
		[CLK_VPU_ENC]		= &vpu_enc.common.hw,
		[CLK_VPU_DEC]		= &vpu_dec.common.hw,
		[CLK_GSP0]		= &gsp0.common.hw,
		[CLK_DISPC0]		= &dispc0.common.hw,
		[CLK_JPG]		= &jpg.common.hw,
	},
	.num    = CLK_DPU_VSP_DVFS_CLK_NUM,
};

static struct sprd_clk_desc ums9632_dpu_vsp_dvfs_clk_desc = {
	.clk_clks	= ums9632_dpu_vsp_dvfs_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_dpu_vsp_dvfs_clks),
	.hw_clks	= &ums9632_dpu_vsp_dvfs_clk_hws,
};

/* audcp global clock gates, Note: not support set/clear */
static SPRD_GATE_CLK_HW(audcp_uart_eb, "audcp-uart-eb", &access_aud_en.common.hw,
			0x0, BIT(0), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_tmr0_eb, "audcp-tmr0-eb", &access_aud_en.common.hw,
			0x0, BIT(1), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_tmr1_eb, "audcp-tmr1-eb", &access_aud_en.common.hw,
			0x0, BIT(2), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_wdg_eb, "audcp-wdg-eb", &access_aud_en.common.hw,
			0x0, BIT(3), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_intc_eb, "audcp-intc-eb", &access_aud_en.common.hw,
			0x0, BIT(4), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_mcdt_eb, "audcp-mcdt-eb", &access_aud_en.common.hw,
			0x0, BIT(5), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_hac_eb, "audcp-hac-eb", &access_aud_en.common.hw,
			0x0, BIT(6), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_vts_eb, "audcp-vts-eb", &access_aud_en.common.hw,
			0x0, BIT(7), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_dma_ap_eb, "audcp-dma-ap-eb", &access_aud_en.common.hw,
			0x0, BIT(8), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_dma_cp_eb, "audcp-dma-cp-eb", &access_aud_en.common.hw,
			0x0, BIT(9), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_splk_eb, "audcp-splk-eb", &access_aud_en.common.hw,
			0x0, BIT(10), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_ahbfifo_vt_eb, "audcp-ahbfifo-vt-eb", &access_aud_en.common.hw,
			0x0, BIT(11), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_ahbfifo_pdm_eb, "audcp-ahbfifo-pdm-eb", &access_aud_en.common.hw,
			0x0, BIT(12), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_mcdt_cp_eb, "audcp-mcdt-cp-eb", &access_aud_en.common.hw,
			0x0, BIT(13), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_ahbfifo_ap_eb, "audcp-ahbfifo-ap-eb", &access_aud_en.common.hw,
			0x0, BIT(14), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_mcdt_ap_eb, "audcp-mcdt-ap-eb", &access_aud_en.common.hw,
			0x0, BIT(15), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);
static SPRD_GATE_CLK_HW(audcp_sensor_eb, "audcp-sensor-eb", &access_aud_en.common.hw,
			0x0, BIT(16), CLK_IGNORE_UNUSED, SPRD_GATE_NON_AON);

static struct sprd_clk_common *ums9632_audcpglb_gate_clks[] = {
	/* address base is 0x56200000 */
	&audcp_uart_eb.common,
	&audcp_tmr0_eb.common,
	&audcp_tmr1_eb.common,
	&audcp_wdg_eb.common,
	&audcp_intc_eb.common,
	&audcp_mcdt_eb.common,
	&audcp_hac_eb.common,
	&audcp_vts_eb.common,
	&audcp_dma_ap_eb.common,
	&audcp_dma_cp_eb.common,
	&audcp_splk_eb.common,
	&audcp_ahbfifo_vt_eb.common,
	&audcp_ahbfifo_pdm_eb.common,
	&audcp_mcdt_cp_eb.common,
	&audcp_ahbfifo_ap_eb.common,
	&audcp_mcdt_ap_eb.common,
	&audcp_sensor_eb.common,
};

static struct clk_hw_onecell_data ums9632_audcpglb_gate_hws = {
	.hws	= {
		[CLK_AUDCP_UART_EB]		= &audcp_uart_eb.common.hw,
		[CLK_AUDCP_TMR0_EB]		= &audcp_tmr0_eb.common.hw,
		[CLK_AUDCP_TMR1_EB]		= &audcp_tmr1_eb.common.hw,
		[CLK_AUDCP_WDG_EB]		= &audcp_wdg_eb.common.hw,
		[CLK_AUDCP_INTC_EB]		= &audcp_intc_eb.common.hw,
		[CLK_AUDCP_MCDT_EB]		= &audcp_mcdt_eb.common.hw,
		[CLK_AUDCP_HAC_EB]		= &audcp_hac_eb.common.hw,
		[CLK_AUDCP_VTS_EB]		= &audcp_vts_eb.common.hw,
		[CLK_AUDCP_DMA_AP_EB]		= &audcp_dma_ap_eb.common.hw,
		[CLK_AUDCP_DMA_CP_EB]		= &audcp_dma_cp_eb.common.hw,
		[CLK_AUDCP_SPLK_EB]		= &audcp_splk_eb.common.hw,
		[CLK_AUDCP_AHBFIFO_VT_EB]	= &audcp_ahbfifo_vt_eb.common.hw,
		[CLK_AUDCP_AHBFIFO_PDM_EB]	= &audcp_ahbfifo_pdm_eb.common.hw,
		[CLK_AUDCP_MCDT_CP_EB]		= &audcp_mcdt_cp_eb.common.hw,
		[CLK_AUDCP_AHBFIFO_AP_EB]	= &audcp_ahbfifo_ap_eb.common.hw,
		[CLK_AUDCP_MCDT_AP_EB]		= &audcp_mcdt_ap_eb.common.hw,
		[CLK_AUDCP_SENSOR_EB]		= &audcp_sensor_eb.common.hw,
	},
	.num	= CLK_AUDCP_GLB_GATE_NUM,
};

static const struct sprd_clk_desc ums9632_audcpglb_gate_desc = {
	.clk_clks	= ums9632_audcpglb_gate_clks,
	.num_clk_clks	= ARRAY_SIZE(ums9632_audcpglb_gate_clks),
	.hw_clks	= &ums9632_audcpglb_gate_hws,
};

static const struct of_device_id sprd_ums9632_clk_ids[] = {
	{ .compatible = "sprd,ums9632-pmu-gate",		/* 0x64910000 */
	  .data = &ums9632_pmu_gate_desc },
	{ .compatible = "sprd,ums9632-g1-pll",			/* 0x64304000 */
	  .data = &ums9632_g1_pll_desc },
	{ .compatible = "sprd,ums9632-g1l-pll",			/* 0x64308000 */
	  .data = &ums9632_g1l_pll_desc },
	{ .compatible = "sprd,ums9632-g5l-pll",			/* 0x64324000 */
	  .data = &ums9632_g5l_pll_desc },
	{ .compatible = "sprd,ums9632-g5r-pll",			/* 0x64320000 */
	  .data = &ums9632_g5r_pll_desc },
	{ .compatible = "sprd,ums9632-g9-pll",			/* 0x64330000 */
	  .data = &ums9632_g9_pll_desc },
	{ .compatible = "sprd,ums9632-g10-pll",			/* 0x64334000 */
	  .data = &ums9632_g10_pll_desc },
	{ .compatible = "sprd,ums9632-apapb-gate",		/* 0x20100000 */
	  .data = &ums9632_apapb_gate_desc },
	{ .compatible = "sprd,ums9632-apahb-gate",		/* 0x20000000 */
	  .data = &ums9632_apahb_gate_desc },
	{ .compatible = "sprd,ums9632-ap-clk",			/* 0x20010000 */
	  .data = &ums9632_ap_clk_desc },
	{ .compatible = "sprd,ums9632-aon-gate",		/* 0x64900000 */
	  .data = &ums9632_aon_gate_desc },
	{ .compatible = "sprd,ums9632-aonapb-clk",		/* 0x64920000 */
	  .data = &ums9632_aonapb_clk_desc },
	{ .compatible = "sprd,ums9632-topdvfs-clk",		/* 0x64940000 */
	  .data = &ums9632_topdvfs_clk_desc },
	{ .compatible = "sprd,ums9632-mm-gate",			/* 0x33000000 */
	  .data = &ums9632_mm_gate_desc },
	{ .compatible = "sprd,ums9632-mm-clk",			/* 0x3301fc00 */
	  .data = &ums9632_mm_clk_desc },
	{ .compatible = "sprd,ums9632-mm-dvfs-clk",		/* 0x33014000 */
	  .data = &ums9632_mm_dvfs_clk_desc },
	{ .compatible = "sprd,ums9632-dpu-vsp-gate",		/* 0x30100000 */
	  .data = &ums9632_dpu_vsp_gate_desc },
	{ .compatible = "sprd,ums9632-dpu-vsp-clk",		/* 0x30110000 */
	  .data = &ums9632_dpu_vsp_clk_desc },
	{ .compatible = "sprd,ums9632-dpu-vsp-dvfs-clk",	/* 0x30114000 */
	  .data = &ums9632_dpu_vsp_dvfs_clk_desc },
	{ .compatible = "sprd,ums9632-audcpglb-gate",		/* 0x56200000 */
	  .data = &ums9632_audcpglb_gate_desc },
	{ }
};
MODULE_DEVICE_TABLE(of, sprd_ums9632_clk_ids);

static int ums9632_clk_probe(struct platform_device *pdev)
{
	const struct sprd_clk_desc *desc;
	struct sprd_reset *reset;
	int ret;

	dev_info(&pdev->dev, " clock probe\n");
	desc = device_get_match_data(&pdev->dev);
	if (!desc)
		return -ENODEV;

	ret = sprd_clk_regmap_init(pdev, desc);
	if (ret)
		return ret;

	if (desc->num_resets > 0) {
		reset = devm_kzalloc(&pdev->dev, sizeof(*reset), GFP_KERNEL);
		if (!reset)
			return -ENOMEM;

		spin_lock_init(&reset->lock);
		reset->rcdev.of_node = pdev->dev.of_node;
		reset->rcdev.ops = &sprd_reset_ops;
		reset->rcdev.nr_resets = desc->num_resets;
		reset->reset_map = desc->resets;
		reset->regmap = platform_get_drvdata(pdev);

		ret = devm_reset_controller_register(&pdev->dev, &reset->rcdev);
		if (ret) {
			dev_err(&pdev->dev, "Failed to register reset controller\n");
			return ret;
		}
	}

	return sprd_clk_probe(&pdev->dev, desc->hw_clks);
}

static struct platform_driver ums9632_clk_driver = {
	.probe	= ums9632_clk_probe,
	.driver	= {
		.name	= "ums9632-clk",
		.of_match_table	= sprd_ums9632_clk_ids,
	},
};
module_platform_driver(ums9632_clk_driver);

MODULE_DESCRIPTION("Unisoc UMS9632 Clock Driver");
MODULE_LICENSE("GPL");
