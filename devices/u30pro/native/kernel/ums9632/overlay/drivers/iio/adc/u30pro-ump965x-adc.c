// SPDX-License-Identifier: GPL-2.0
// Copyright (C) 2018 Spreadtrum Communications Inc.

#include <linux/hwspinlock.h>
#include <linux/bitfield.h>
#include <linux/iio/iio.h>
#include <linux/math64.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/nvmem-consumer.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/sort.h>

/* ADC controller registers definition */
#define SPRD_ADC_CTL			0x0

#define SPRD_ADC_CH_CFG			0x4
#define SPRD_ADC_CHN_ID_MASK		GENMASK(4, 0)

#define SPRD_ADC_DATA			0x4c
#define SPRD_ADC_DATA_MASK		GENMASK(11, 0)

#define SPRD_ADC_INT_EN			0x50
#define SPRD_ADC_IRQ_EN			BIT(0)

#define SPRD_ADC_INT_CLR		0x54
#define SPRD_ADC_IRQ_CLR		BIT(0)

#define SPRD_ADC_INT_STS		0x58

#define SPRD_ADC_INT_RAW		0x5c
#define SPRD_ADC_IRQ_RAW		BIT(0)

/* Bits and mask definition for SPRD_ADC_CTL register */
#define SPRD_ADC_EN			BIT(0)
#define SPRD_ADC_CHN_RUN		BIT(1)
#define SPRD_ADC_12BIT_MODE		BIT(2)
#define SPRD_ADC_RUN_NUM_MASK		GENMASK(7, 4)
#define SPRD_ADC_RUN_NUM_SHIFT		4
#define SPRD_ADC_AVERAGE_SHIFT		8
#define SPRD_ADC_AVERAGE_MASK		GENMASK(10, 8)

/* ADC voltage ratio definition */
#define SPRD_RATIO_NUMERATOR_OFFSET	16
#define SPRD_RATIO_DENOMINATOR_MASK	GENMASK(15, 0)
#define SPRD_ADC_RATIO(n, d)		(((n) << SPRD_RATIO_NUMERATOR_OFFSET) | (d))
#define SPRD_ADC_R_INVALID		0xffffffff
#define SPRD_ADC_RATIO_DEF		SPRD_ADC_RATIO(1, 1)

#define SPRD_ADC_REFVOL_VDD35		3500000
#define SPRD_ADC_REFVOL_VDD28		2800000

#define SPRD_ADC_RDY_TIMEOUT_US		1000000
#define SPRD_ADC_RDY_POLL_DELAY_US	500

#define SPRD_ADC_HWLOCK_TIMEOUT_MS	5000
#define SPRD_ADC_CHANNEL_MAX		32
#define SPRD_ADC_CELL_MAX		2
#define SPRD_ADC_INVALID_DATA		0xffffffff
#define SPRD_ADC_INIT_MAGIC		0xa7a77a7a
#define SPRD_ADC_MESURE_NUM_SW		15
#define SPRD_ADC_MESURE_NUM_HW_DEF	3 /* 2 << 3 = 8 times */

#define SPRD_ADC_SCALE_MASK		GENMASK(7, 0)
#define SPRD_ADC_FILTER_MASK		GENMASK(15, 8)
#define SPRD_ADC_ISEN_MASK		GENMASK(23, 16)
#define SPRD_ADC_FILTER_SF_MASK		BIT(0)
#define SPRD_ADC_FILTER_HF_MASK		GENMASK(7, 1)
#define SPRD_ADC_ISEN_SUP_MASK		BIT(0)
#define SPRD_ADC_ISEN_VAL_MASK		GENMASK(7, 1)

#define SPRD_ADC_REG_DUMP_BUF_LEN	128
#define DATASHEET_NAME_LEN		32

enum SPRD_ADC_AUX_VER {
	AUX_RATIO_V0,
	AUX_RATIO_V1
};

#define SPRD_ADC_CHANNEL(index, mask) {				\
	.type = IIO_VOLTAGE,					\
	.channel = index,					\
	.info_mask_separate = mask | BIT(IIO_CHAN_INFO_SCALE),	\
	.datasheet_name = "CH##index",				\
	.indexed = 1,						\
}

#define CALIB_INFO_INIT_V0(ch, sl, mask0, offset0, mask1, offset1)	\
{					\
	.channel = ch,			\
	.scale = sl,			\
	.efuse_mask0 = mask0,		\
	.efuse_offset0 = offset0,	\
	.efuse_mask1 = mask1,		\
	.efuse_offset1 = offset1,	\
	.inited = SPRD_ADC_INIT_MAGIC,	\
}

#define CALIB_INFO_INIT_V1(ch, sl, aux_r0, aux_r1)		\
{								\
	.channel = ch,						\
	.scale = sl,						\
	.ch_aux_ratio = {aux_r0, aux_r1, SPRD_ADC_R_INVALID, SPRD_ADC_R_INVALID},\
	.inited = SPRD_ADC_INIT_MAGIC,				\
}

#define GRAPH_POINT_INIT(v0, a0, v1, a1)\
	.volt0 = v0,			\
	.adc0 = a0,			\
	.volt1 = v1,			\
	.adc1 = a1

#define REG_BIT_INIT(b_base, reg_address, b_mask, b_offset, set, clr, func, verse)\
{										\
	.base = b_base,								\
	.reg_addr = reg_address,						\
	.mask = b_mask,								\
	.offset = b_offset,							\
	.val_set = set,								\
	.val_clr = clr,								\
	.inited = SPRD_ADC_INIT_MAGIC,						\
	.get_val = func,							\
	.reverse = verse							\
}

enum SPRD_PMIC_TYPE {
	SC2720_ADC,
	SC2721_ADC,
	SC2730_ADC,
	SC2731_ADC,
	UIP8520_ADC,
	UMP518_ADC,
	UMP9620_ADC,
	UMP9650_ADC,
	UMP9651_ADC,
	UMP9360_ADC,
};

enum SPRD_ADC_GRAPH_TYPE {
	ONE_CELL_GRAPH0,
	ONE_CELL_GRAPH1,
	TWO_CELL_GRAPH,
	SPRD_ADC_GRAPH_TYPE_MAX
};

enum SPRD_ADC_GRAPH_OFFSET {
	GRAPH_BIG,
	GRAPH_SMALL,
	GRAPH_VBAT_DET,
	GRAPH_MAX
};

enum SPRD_ADC_SCALE {
	SCALE_00,
	SCALE_01,
	SCALE_10,
	SCALE_11,
	SPRD_ADC_SCALE_MAX
};

enum SPRD_ADC_REG_TYPE {
	REG_MODULE_EN,
	REG_CLK_EN,
	REG_SOFT_RST,
	REG_SCALE,
	REG_CLK_IN_ST = 5,
	REG_CLK_IN0,
	REG_CLK_IN1,
	REG_CLK_IN2,
	REG_CLK_IN_END,
	REG_CFG_FIXED_ST = 11,/* FIXED CFG START */
	REG_CAL_EN,
	REG_NTC_MUX,
	REG_CFG_FIXED_END,/* FIXED CFG END */
	REG_ISEN_ST = 21,/* CURRENT MODE START */
	REG_ISEN0,
	REG_ISEN1,
	REG_ISEN2,
	REG_ISEN3,
	REG_ISEN_END,/* CURRENT MODE END */
	SPRD_ADC_REG_TYPE_MAX
};

enum SPRD_ADC_REG_BASE {
	BASE_GLB,
	BASE_ANA
};

struct sprd_adc_pm_data {
	struct regmap *clk_regmap;
	struct regulator *volref;
	u32 clk_reg;/* adc clk26 vote reg */
	u32 clk_reg_mask;/* adc clk26 vote reg mask */
	bool dev_suspended;
};

struct sprd_adc_channel_data {
	int scale;
	int graph;
	int isen_info;
	int filter_info;
	int ip_ratio;
	int ch_aux_ratio[SPRD_ADC_SCALE_MAX];
	int aux_r_ver;
	int volreq; /* use volreq to customerize adc volref, def 2.8v */
	int inited;
};

struct sprd_adc_calib_info {
	int channel;
	int scale;
	u32 efuse_mask0;
	u32 efuse_offset0;
	u32 efuse_mask1;
	u32 efuse_offset1;
	int ch_aux_ratio[SPRD_ADC_SCALE_MAX];
	int inited;
};

struct  sprd_adc_reg_bit {
	u32 base;
	u32 reg_addr;
	u32 mask;
	u32 offset;
	u32 val_set;
	u32 val_clr;
	u32 inited;
	bool reverse;
	u32 (*get_val)(void *pri, int ch, bool set);
};

struct sprd_adc_data;
struct sprd_adc_variant_data {
	const int pmic_type;
	const struct  sprd_adc_reg_bit *const reg_list;
	const u32 glb_reg_base;
	const u32 adc_reg_base_offset;
	const u32 graph_index;
	const struct sprd_adc_calib_info calib_infos[GRAPH_MAX];
	const int aux_ratio_comm[SPRD_ADC_SCALE_MAX];
	void (*const ch_data_init)(struct sprd_adc_data *data);
};

struct sprd_adc_linear_graph {
	const char *cell_names[SPRD_ADC_CELL_MAX + 1];/* must end with NULL point */
	int cell_value[SPRD_ADC_CELL_MAX + 1];
	void (*const calibrate)(struct sprd_adc_data *data, int graph);
	int volt0;
	int adc0;
	int volt1;
	int adc1;
};

struct sprd_adc_graphs {
	struct sprd_adc_linear_graph linear_graphs[GRAPH_MAX];
};

struct sprd_adc_data {
	struct iio_dev *indio_dev;
	struct device *dev;
	struct regmap *regmap;
	/* lock to protect against multiple access to the device */
	struct mutex mutex;
	/*
	 * One hardware spinlock to synchronize between the multiple
	 * subsystems which will access the unique ADC controller.
	 */
	struct hwspinlock *hwlock;
	u32 base;
	int irq;
	struct sprd_adc_channel_data ch_data[SPRD_ADC_CHANNEL_MAX];
	const struct sprd_adc_variant_data *var_data;
	struct sprd_adc_graphs adc_graphs;
	struct sprd_adc_pm_data pm_data;
	struct iio_chan_spec *iio_chan_spec;
	u32 iio_chan_spec_num;
	int data_cache[SPRD_ADC_CHANNEL_MAX];
};

static void sprd_adc_calib_with_one_cell(struct sprd_adc_data *data, int graph);
static void sprd_adc_calib_with_two_cell(struct sprd_adc_data *data, int graph);
static u32 sprd_adc_get_isen(void *pri, int ch, bool enable);
static int sprd_adc_soft_rst(struct sprd_adc_data *data);
static int sprd_adc_hw_enable(struct sprd_adc_data *data);

static void sprd_adc_ch_data_filling_v0(struct sprd_adc_channel_data *ch_data, int sl, int filter,
					int isen, int ip_r)
{
	memset(ch_data, 0, sizeof(struct sprd_adc_channel_data));
	ch_data->scale = sl;
	ch_data->isen_info = isen;
	ch_data->filter_info = filter;
	ch_data->ip_ratio = ip_r;
	ch_data->aux_r_ver = AUX_RATIO_V0;
	ch_data->inited = SPRD_ADC_INIT_MAGIC;
}

static void sprd_adc_ch_data_filling_v1(struct sprd_adc_channel_data *ch_data, int sl, int filter,
					int isen, int ip_r, int aux_r0, int aux_r1)
{
	memset(ch_data, 0, sizeof(struct sprd_adc_channel_data));
	ch_data->scale = sl;
	ch_data->isen_info = isen;
	ch_data->filter_info = filter;
	ch_data->ip_ratio = ip_r;
	memset(&ch_data->ch_aux_ratio[0], SPRD_ADC_R_INVALID,
	       sizeof(ch_data->ch_aux_ratio[0]) * SPRD_ADC_SCALE_MAX);
	ch_data->ch_aux_ratio[0] = aux_r0;
	ch_data->ch_aux_ratio[1] = aux_r1;
	ch_data->aux_r_ver = AUX_RATIO_V1;
	ch_data->inited = SPRD_ADC_INIT_MAGIC;
}

static int sprd_adc_chan_spec_init_once(struct sprd_adc_data *data, struct iio_chan_spec *chan,
					u32 ch, u32 iio_type)
{
	char name[DATASHEET_NAME_LEN] = {0};

	if (ch >= SPRD_ADC_CHANNEL_MAX ||
	    (iio_type != IIO_CHAN_INFO_RAW && iio_type != IIO_CHAN_INFO_PROCESSED))
		return -EINVAL;

	chan->type = IIO_VOLTAGE;
	chan->channel = ch;
	chan->info_mask_separate = (BIT(iio_type) | BIT(IIO_CHAN_INFO_SCALE));
	chan->indexed = 1;

	scnprintf(name, DATASHEET_NAME_LEN, "CH%u", ch);
	chan->datasheet_name = devm_kstrdup(data->dev, name, GFP_KERNEL);
	if (!chan->datasheet_name)
		return -ENOMEM;

	return 0;
}

static u32 sprd_adc_get_reg_addr(struct sprd_adc_data *data, int index)
{
	u32 base = ((data->var_data->reg_list[index].base == BASE_GLB)
		    ? (data->var_data->glb_reg_base)
		    : (data->base - data->var_data->adc_reg_base_offset));
	return (base + data->var_data->reg_list[index].reg_addr);
}

static inline void ratio_to_u32_fract(u32 ratio, struct u32_fract *fract)
{
	fract->numerator = ratio >> SPRD_RATIO_NUMERATOR_OFFSET;
	fract->denominator = ratio & SPRD_RATIO_DENOMINATOR_MASK;
}

static const struct sprd_adc_graphs sprd_adc_graphs_array[] = {
	[ONE_CELL_GRAPH0] = {/* SC2721_2731 */
		.linear_graphs = {
			[GRAPH_BIG] = {
				.cell_names = {"big_scale_calib", NULL},
				.calibrate = sprd_adc_calib_with_one_cell,
				GRAPH_POINT_INIT(4200, 850, 3600, 728),
			},
			[GRAPH_SMALL] = {
				.cell_names = {"small_scale_calib", NULL},
				.calibrate = sprd_adc_calib_with_one_cell,
				GRAPH_POINT_INIT(1000, 838, 100, 84),
			},
		},
	},
	[ONE_CELL_GRAPH1] = {/* SC2720_2730 */
		.linear_graphs = {
			[GRAPH_BIG] = {
				.cell_names = {"big_scale_calib", NULL},
				.calibrate = sprd_adc_calib_with_one_cell,
				GRAPH_POINT_INIT(4200, 856, 3600, 733),
			},
			[GRAPH_SMALL] = {
				.cell_names = {"small_scale_calib", NULL},
				.calibrate = sprd_adc_calib_with_one_cell,
				GRAPH_POINT_INIT(1000, 833, 100, 80),
			},
		},
	},
	[TWO_CELL_GRAPH] = {
		.linear_graphs = {
			[GRAPH_BIG] = {
				.cell_names = {"big_scale_calib1", "big_scale_calib2", NULL},
				.calibrate = sprd_adc_calib_with_two_cell,
				GRAPH_POINT_INIT(4200, 3310, 3600, 2832),
			},
			[GRAPH_SMALL] = {
				.cell_names = {"small_scale_calib1", "small_scale_calib2", NULL},
				.calibrate = sprd_adc_calib_with_two_cell,
				GRAPH_POINT_INIT(1000, 3413, 100, 341),
			},
			[GRAPH_VBAT_DET] = {
				.cell_names = {"vbat_det_cal1", "vbat_det_cal2", NULL},
				.calibrate = sprd_adc_calib_with_two_cell,
				GRAPH_POINT_INIT(1400, 3482, 200, 476),
			},
		},
	},
};

static const struct sprd_adc_reg_bit regs_sc2720[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x1e8, BIT(8), 8, 1, 0, NULL, false),
	[REG_SCALE]  = REG_BIT_INIT(BASE_ANA, -1, GENMASK(10, 9), 9, -1, -1, NULL, false),
	[REG_CAL_EN] = REG_BIT_INIT(BASE_ANA, 0x4, BIT(12), 12, 1, 0, NULL, false),
	[REG_ISEN0]  = REG_BIT_INIT(BASE_GLB, 0x1F0, BIT(0), 0, 0, 0, NULL, true),
	[REG_ISEN1]  = REG_BIT_INIT(BASE_GLB, 0x1F0, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2]  = REG_BIT_INIT(BASE_GLB, 0x1F0, GENMASK(11, 9), 9, -1, -1, sprd_adc_get_isen,
				    false),
	[REG_ISEN3]  = REG_BIT_INIT(BASE_ANA, 0xB0, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_sc2721[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x29c, BIT(8), 8, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, -1, BIT(5), 5, -1, -1, NULL, false),
	[REG_NTC_MUX] = REG_BIT_INIT(BASE_ANA, 0xB0, GENMASK(1, 0), 0, 0x1, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0x2A4, BIT(0), 0, 0, 0, NULL, true),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0x2A4, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0x2A4, GENMASK(12, 10), 10, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xAC, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_sc2730[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x378, BIT(8), 8, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, 0xffff, GENMASK(10, 9), 9, -1, -1, NULL, false),
	[REG_CAL_EN] = REG_BIT_INIT(BASE_ANA, 0x4, BIT(12), 12, 1, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0x384, BIT(0), 0, 0, 0, NULL, true),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0x384, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0x384, GENMASK(11, 9), 9, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xB0, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_sc2731[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x10, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x20, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x2b8, BIT(8), 8, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, 0xffff, BIT(5), 5, -1, -1, NULL, false),
	[REG_NTC_MUX] = REG_BIT_INIT(BASE_GLB, 0x2B4, GENMASK(12, 11), 11, 0x1, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0x324, BIT(4), 4, 0, 0, NULL, false),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0x2B4, BIT(6), 6, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0x324, GENMASK(3, 1), 1, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xAC, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_ump9620[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x378, BIT(8), 8, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, 0xffff, GENMASK(10, 9), 9, -1, -1, NULL, false),
	[REG_CAL_EN] = REG_BIT_INIT(BASE_ANA, 0x4, BIT(12), 12, 1, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0x384, BIT(0), 0, 0, 0, NULL, true),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0x384, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0x384, GENMASK(11, 9), 9, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xB0, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_ump9650[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x408, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN1] = REG_BIT_INIT(BASE_GLB, 0x410, BIT(2), 2, 1, 0, NULL, false),
	[REG_CLK_IN2] = REG_BIT_INIT(BASE_GLB, 0x410, BIT(10), 10, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, 0xffff, GENMASK(10, 9), 9, -1, -1, NULL, false),
	[REG_CAL_EN] = REG_BIT_INIT(BASE_ANA, 0x4, BIT(12), 12, 1, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0x1c0, BIT(0), 0, 0, 0, NULL, true),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0x1c0, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0x1c0, GENMASK(11, 9), 9, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xB0, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_ump9651[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x344, BIT(7), 7, 1, 0, NULL, false),
	[REG_CLK_IN1] = REG_BIT_INIT(BASE_GLB, 0x344, BIT(13), 13, 1, 0, NULL, false),
	[REG_CLK_IN2] = REG_BIT_INIT(BASE_GLB, 0x0c, BIT(7), 7, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, 0xffff, GENMASK(10, 9), 9, -1, -1, NULL, false),
	[REG_CAL_EN] = REG_BIT_INIT(BASE_ANA, 0x4, BIT(12), 12, 1, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0xfc, BIT(0), 0, 0, 0, NULL, true),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0xfc, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0xfc, GENMASK(11, 9), 9, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xB0, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_uip8520[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(6, 5), 5, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(6), 6, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x28c, BIT(8), 8, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, 0xffff, GENMASK(10, 9), 9, -1, -1, NULL, false),
	[REG_CAL_EN] = REG_BIT_INIT(BASE_ANA, 0x4, BIT(12), 12, 1, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0x298, BIT(0), 0, 0, 0, NULL, true),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0x298, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0x298, GENMASK(11, 9), 9, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xB0, BIT(0), 0, 1, 0, NULL, false),
};

static const struct sprd_adc_reg_bit regs_ump9360[] = {
	[REG_MODULE_EN] = REG_BIT_INIT(BASE_GLB, 0x08, BIT(5), 5, 1, 0, NULL, false),
	[REG_CLK_EN] = REG_BIT_INIT(BASE_GLB, 0x0c, GENMASK(3, 2), 2, 0x3, 0, NULL, false),
	[REG_SOFT_RST] = REG_BIT_INIT(BASE_GLB, 0x14, BIT(4), 4, 1, 0, NULL, false),
	[REG_CLK_IN0] = REG_BIT_INIT(BASE_GLB, 0x2b0, BIT(8), 8, 1, 0, NULL, false),
	[REG_SCALE] = REG_BIT_INIT(BASE_ANA, 0xffff, GENMASK(10, 9), 9, -1, -1, NULL, false),
	[REG_CAL_EN] = REG_BIT_INIT(BASE_ANA, 0x4, BIT(12), 12, 0, 0, NULL, false),
	[REG_ISEN0] = REG_BIT_INIT(BASE_GLB, 0x2b4, BIT(0), 0, 1, 0, NULL, true),
	[REG_ISEN1] = REG_BIT_INIT(BASE_GLB, 0x2b4, BIT(13), 13, 1, 0, NULL, false),
	[REG_ISEN2] = REG_BIT_INIT(BASE_GLB, 0x2b4, GENMASK(11, 9), 9, -1, -1, sprd_adc_get_isen,
				   false),
	[REG_ISEN3] = REG_BIT_INIT(BASE_ANA, 0xB0, BIT(0), 0, 1, 0, NULL, false),
};

static struct iio_chan_spec sprd_channels[] = {
	SPRD_ADC_CHANNEL(0, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(1, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(2, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(3, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(4, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(5, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(6, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(7, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(8, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(9, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(10, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(11, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(12, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(13, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(14, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(15, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(16, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(17, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(18, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(19, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(20, BIT(IIO_CHAN_INFO_RAW)),
	SPRD_ADC_CHANNEL(21, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(22, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(23, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(24, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(25, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(26, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(27, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(28, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(29, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(30, BIT(IIO_CHAN_INFO_PROCESSED)),
	SPRD_ADC_CHANNEL(31, BIT(IIO_CHAN_INFO_PROCESSED)),
};

static void sprd_adc_calib_with_one_cell(struct sprd_adc_data *data, int graph)
{
	struct sprd_adc_linear_graph *linear_graph = &data->adc_graphs.linear_graphs[graph];
	int calib_data = linear_graph->cell_value[0], adc0_calib, adc1_calib;

	pr_debug("calib before: adc0: %d:, adc1:%d, calib_data: %d\n",
		 linear_graph->adc0, linear_graph->adc1, calib_data);

	adc0_calib = (((calib_data & 0xff) + linear_graph->adc0 - 128) * 4);
	adc1_calib = ((((calib_data >> 8) & 0xff) + linear_graph->adc1 - 128) * 4);

	if (adc0_calib > 0 && adc1_calib > 0) {
		linear_graph->adc0 = adc0_calib;
		linear_graph->adc1 = adc1_calib;
	}

	pr_debug("calib aft: adc0: %d:, adc1:%d, calib_data: %d\n",
		 linear_graph->adc0, linear_graph->adc1, calib_data);
}

static void sprd_adc_calib_with_two_cell(struct sprd_adc_data *data, int graph)
{
	struct sprd_adc_linear_graph *linear_graph = &data->adc_graphs.linear_graphs[graph];
	const struct sprd_adc_calib_info *calib_info = &data->var_data->calib_infos[graph];

	int adc_calib_data0 = linear_graph->cell_value[0];
	int adc_calib_data1 = linear_graph->cell_value[1];
	int adc0_calib, adc1_calib;

	pr_debug("calib before: adc0: %d:, adc1:%d, calib_data0: 0x%x, calib_data1: 0x%x\n",
		 linear_graph->adc0, linear_graph->adc1, adc_calib_data0, adc_calib_data1);

	adc0_calib = ((adc_calib_data0 >> calib_info->efuse_offset0) & calib_info->efuse_mask0);
	adc1_calib = ((adc_calib_data1 >> calib_info->efuse_offset1) & calib_info->efuse_mask1);

	if (adc0_calib > 0 && adc1_calib > 0) {
		linear_graph->adc0 = adc0_calib;
		linear_graph->adc1 = adc1_calib;
	}

	pr_debug("calib aft: adc0: %d:, adc1:%d, calib_data0: 0x%x, calib_data1: 0x%x\n",
		 linear_graph->adc0, linear_graph->adc1, adc_calib_data0, adc_calib_data1);
}

static int sprd_adc_calib_graph_get(struct sprd_adc_data *data, int ch)
{
	int calib_graph;
	const struct sprd_adc_calib_info *calib_info;

	for (calib_graph = 0; calib_graph < GRAPH_MAX; calib_graph++) {

		calib_info = &data->var_data->calib_infos[calib_graph];
		if (calib_info->inited != SPRD_ADC_INIT_MAGIC)
			continue;

		if (calib_info->channel == ch)
			return calib_graph;
	}

	return GRAPH_SMALL;
}

static void sprd_adc_aux_ratio_init(struct sprd_adc_data *data, int ch)
{
	u64 final_ratio;
	int scale, *aux_ratio_ch, aux_ratio_tmp[SPRD_ADC_SCALE_MAX];
	const int *aux_ratio_calib;
	struct u32_fract c_fract, aux_fract;
	int calib_graph = sprd_adc_calib_graph_get(data, ch);
	const struct sprd_adc_calib_info *calib_match = &data->var_data->calib_infos[calib_graph];

	aux_ratio_ch = data->ch_data[ch].ch_aux_ratio;
	if (calib_match->inited != SPRD_ADC_INIT_MAGIC) {
		for (scale = 0; scale < SPRD_ADC_SCALE_MAX; scale++)
			aux_ratio_ch[scale] = SPRD_ADC_RATIO_DEF;
		return;
	}

	data->ch_data[ch].graph = calib_graph;

	if (data->ch_data[ch].aux_r_ver == AUX_RATIO_V0) {
		aux_ratio_calib = &data->var_data->aux_ratio_comm[0];
		memcpy(&aux_ratio_tmp[0], aux_ratio_calib, sizeof(int) * SPRD_ADC_SCALE_MAX);
	} else {
		aux_ratio_calib = &calib_match->ch_aux_ratio[0];
		memcpy(&aux_ratio_tmp[0], &aux_ratio_ch[0], sizeof(int) * SPRD_ADC_SCALE_MAX);
	}

	ratio_to_u32_fract(aux_ratio_calib[calib_match->scale], &c_fract);
	for (scale = 0; scale < SPRD_ADC_SCALE_MAX; scale++) {

		if (ch == calib_match->channel && scale == calib_match->scale) {
			aux_ratio_ch[scale] = SPRD_ADC_RATIO_DEF;
			continue;
		}

		if (aux_ratio_tmp[scale] == SPRD_ADC_R_INVALID ||
		    aux_ratio_calib[scale] == SPRD_ADC_R_INVALID) {
			aux_ratio_ch[scale] = SPRD_ADC_RATIO_DEF;
			continue;
		}

		ratio_to_u32_fract(aux_ratio_tmp[scale], &aux_fract);
		if (scale > calib_match->scale) {
			final_ratio =
				div_u64((u64)aux_fract.denominator * c_fract.numerator * 1000,
					c_fract.denominator * aux_fract.numerator);
			aux_ratio_ch[scale] = SPRD_ADC_RATIO(1000, (int)final_ratio);
		} else {
			final_ratio =
				div_u64((u64)c_fract.denominator * aux_fract.numerator * 1000,
					aux_fract.denominator * c_fract.numerator);
			aux_ratio_ch[scale] = SPRD_ADC_RATIO((int)final_ratio, 1000);
		}
	}
}

static void sprd_adc_ch_data_merge(struct sprd_adc_data *data,
				   struct sprd_adc_channel_data *d_def)
{
	struct sprd_adc_channel_data *ch_data;
	int ch;

	for (ch = 0; ch < SPRD_ADC_CHANNEL_MAX; ch++) {
		ch_data = &data->ch_data[ch];
		if (ch_data->inited == SPRD_ADC_INIT_MAGIC)
			continue;
		*ch_data = *d_def;
	}

	for (ch = 0; ch < SPRD_ADC_CHANNEL_MAX; ch++)
		sprd_adc_aux_ratio_init(data, ch);
}


static int sprd_adc_graphs_calibrate(struct sprd_adc_data *data)
{
	int i, j, ret;
	const struct sprd_adc_graphs *graphs = &sprd_adc_graphs_array[data->var_data->graph_index];
	struct sprd_adc_linear_graph *linear_graph;
	u16 cell_value = 0;

	if (data->var_data->graph_index >= SPRD_ADC_GRAPH_TYPE_MAX)
		return -EINVAL;

	memcpy(&data->adc_graphs, graphs, sizeof(struct sprd_adc_graphs));

	if (device_property_read_bool(data->dev, "sprd,adc-no-calib"))
		return dev_err_probe(data->dev, -EINVAL, "factory ADC calibration required\n");

	for (i = 0; i < GRAPH_MAX; i++) {

		if (data->var_data->calib_infos[i].inited != SPRD_ADC_INIT_MAGIC)
			continue;

		linear_graph = &data->adc_graphs.linear_graphs[i];
		for (j = 0; linear_graph->cell_names[j] != NULL; j++) {
			ret = nvmem_cell_read_u16(data->dev, linear_graph->cell_names[j],
						  &cell_value);
			if (ret) {
				dev_err(data->dev, "calibrate err! name: %s: ret %d\n",
					linear_graph->cell_names[j], ret);
				return ret;
			}
			linear_graph->cell_value[j] = cell_value;
		}
	}

	for (i = 0; i < GRAPH_MAX; i++) {

		if (data->var_data->calib_infos[i].inited != SPRD_ADC_INIT_MAGIC)
			continue;

		linear_graph = &data->adc_graphs.linear_graphs[i];
		linear_graph->calibrate(data, i);
		if (linear_graph->adc0 <= linear_graph->adc1 ||
		    linear_graph->adc0 > 4095 || linear_graph->adc1 < 0)
			return dev_err_probe(data->dev, -EINVAL, "invalid factory ADC graph\n");
	}

	return 0;
}

static void sc2720_ch_data_init(struct sprd_adc_data *data)
{
	struct sprd_adc_channel_data ch_data_def;
	struct sprd_adc_channel_data *ch_data = data->ch_data;

	sprd_adc_ch_data_filling_v0(&ch_data_def, SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[3], SCALE_00, 0x1, 0x9, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[5], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[7], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[9], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[13], SCALE_01, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[14], SCALE_00, 0, 0, SPRD_ADC_RATIO(68, 900));
	sprd_adc_ch_data_filling_v0(&ch_data[16], SCALE_00, 0, 0, SPRD_ADC_RATIO(48, 100));
	sprd_adc_ch_data_filling_v0(&ch_data[19], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[21], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[22], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[23], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[30], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[31], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);

	sprd_adc_ch_data_merge(data, &ch_data_def);
}

static void sc2721_ch_data_init(struct sprd_adc_data *data)
{
	struct sprd_adc_channel_data ch_data_def;
	struct sprd_adc_channel_data *ch_data = data->ch_data;

	sprd_adc_ch_data_filling_v1(&ch_data_def, SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v1(&ch_data[1], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[2], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[3], SCALE_00, 0x1, 0x9, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[4], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[5], SCALE_01, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO(7, 29), SPRD_ADC_RATIO(7, 29));
	sprd_adc_ch_data_filling_v1(&ch_data[7], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 125));
	sprd_adc_ch_data_filling_v1(&ch_data[9], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 125));
	sprd_adc_ch_data_filling_v1(&ch_data[14], SCALE_00, 0, 0, SPRD_ADC_RATIO(68, 900),
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v1(&ch_data[16], SCALE_00, 0, 0, SPRD_ADC_RATIO(48, 100),
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v1(&ch_data[19], SCALE_00, 0, 0, SPRD_ADC_RATIO(1, 3),
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO_DEF);

	sprd_adc_ch_data_merge(data, &ch_data_def);

	data->ch_data[30].volreq = data->ch_data[31].volreq = SPRD_ADC_REFVOL_VDD35;
}

static void sc2730_ch_data_init(struct sprd_adc_data *data)
{
	struct sprd_adc_channel_data ch_data_def;
	struct sprd_adc_channel_data *ch_data = data->ch_data;

	sprd_adc_ch_data_filling_v0(&ch_data_def, SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[3], SCALE_00, 1, 0x9, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[5], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[7], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[9], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[10], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[13], SCALE_01, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[14], SCALE_00, 0, 0, SPRD_ADC_RATIO(68, 900));
	sprd_adc_ch_data_filling_v0(&ch_data[15], SCALE_00, 0, 0, SPRD_ADC_RATIO(1, 3));
	sprd_adc_ch_data_filling_v0(&ch_data[16], SCALE_00, 0, 0, SPRD_ADC_RATIO(48, 100));
	sprd_adc_ch_data_filling_v0(&ch_data[19], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[21], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[22], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[23], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[30], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[31], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);

	sprd_adc_ch_data_merge(data, &ch_data_def);
}

static void sc2731_ch_data_init(struct sprd_adc_data *data)
{
	struct sprd_adc_channel_data ch_data_def;
	struct sprd_adc_channel_data *ch_data = data->ch_data;

	sprd_adc_ch_data_filling_v1(&ch_data_def, SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v1(&ch_data[1], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[2], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[3], SCALE_00, 0x1, 0x9, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[4], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(400, 1025));
	sprd_adc_ch_data_filling_v1(&ch_data[5], SCALE_01, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO(7, 29), SPRD_ADC_RATIO(7, 29));
	sprd_adc_ch_data_filling_v1(&ch_data[6], SCALE_00, 0, 0, SPRD_ADC_RATIO(375, 9000),
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v1(&ch_data[7], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 125));
	sprd_adc_ch_data_filling_v1(&ch_data[8], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF,
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 125));
	sprd_adc_ch_data_filling_v1(&ch_data[19], SCALE_00, 0, 0, SPRD_ADC_RATIO(1, 3),
				    SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO_DEF);

	sprd_adc_ch_data_merge(data, &ch_data_def);
}

static void ump9620_ch_data_init(struct sprd_adc_data *data)
{
	struct sprd_adc_channel_data ch_data_def;
	struct sprd_adc_channel_data *ch_data = data->ch_data;

	sprd_adc_ch_data_filling_v0(&ch_data_def, SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[0], SCALE_01, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[5], SCALE_00, 0x1, 0x9, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[6], SCALE_00, 0x1, 0x9, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[7], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[9], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[10], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[11], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[13], SCALE_01, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[14], SCALE_00, 0, 0, SPRD_ADC_RATIO(68, 900));
	sprd_adc_ch_data_filling_v0(&ch_data[15], SCALE_00, 0, 0, SPRD_ADC_RATIO(1, 3));
	sprd_adc_ch_data_filling_v0(&ch_data[19], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[21], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[22], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[23], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[30], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[31], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);

	sprd_adc_ch_data_merge(data, &ch_data_def);
}

static void ump9360_ch_data_init(struct sprd_adc_data *data)
{
	struct sprd_adc_channel_data ch_data_def;
	struct sprd_adc_channel_data *ch_data = data->ch_data;

	sprd_adc_ch_data_filling_v0(&ch_data_def, SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[0], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[5], SCALE_00, 0x1, 0x9, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[6], SCALE_00, 0x1, 0x9, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[7], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[8], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[9], SCALE_10, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[10], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[11], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[13], SCALE_01, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[14], SCALE_00, 0, 0, SPRD_ADC_RATIO(68, 900));
	sprd_adc_ch_data_filling_v0(&ch_data[15], SCALE_00, 0, 0, SPRD_ADC_RATIO(1, 3));
	sprd_adc_ch_data_filling_v0(&ch_data[19], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[21], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[22], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[23], SCALE_00, 0, 0, SPRD_ADC_RATIO(3, 8));
	sprd_adc_ch_data_filling_v0(&ch_data[29], SCALE_00, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[30], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);
	sprd_adc_ch_data_filling_v0(&ch_data[31], SCALE_11, 0, 0, SPRD_ADC_RATIO_DEF);

	sprd_adc_ch_data_merge(data, &ch_data_def);
}

static void sprd_adc_regs_dump(struct sprd_adc_data *data, int ch, int scale, const char *tag)
{
	char buf[SPRD_ADC_REG_DUMP_BUF_LEN];
	u32 cnt = 0, reg_val, err_magic = 0x6776;
	int ret;

	cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "[%s]: ch %d, sl %d, ",
			 tag, ch, scale);

	ret = regmap_read(data->regmap, sprd_adc_get_reg_addr(data, REG_CLK_EN), &reg_val);
	cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "clk_en 0x%x, ",
			 (ret ? err_magic : reg_val));

	ret = regmap_read(data->regmap, sprd_adc_get_reg_addr(data, REG_CLK_IN0), &reg_val);
	cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "xtl_ctl 0x%x, ",
			 (ret ? err_magic : reg_val));

	ret = regmap_read(data->regmap, sprd_adc_get_reg_addr(data, REG_MODULE_EN), &reg_val);
	cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "mod_en 0x%x, ",
			 (ret ? err_magic : reg_val));

	if (data->pm_data.clk_regmap) {
		ret = regmap_read(data->pm_data.clk_regmap,  data->pm_data.clk_reg, &reg_val);
		cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "pm_clk 0x%x, ",
				 (ret ? err_magic : reg_val));
	}

	ret = regmap_read(data->regmap, data->base + SPRD_ADC_INT_CLR, &reg_val);
	cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "int_ctl 0x%x, ",
			 (ret ? err_magic : reg_val));

	ret = regmap_read(data->regmap, data->base + SPRD_ADC_INT_RAW, &reg_val);
	cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "int_raw 0x%x, ",
			 (ret ? err_magic : reg_val));

	ret = regmap_read(data->regmap, data->base + SPRD_ADC_CTL, &reg_val);
	cnt += scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "adc_ctl 0x%x, ",
			 (ret ? err_magic : reg_val));

	ret = regmap_read(data->regmap, data->base + SPRD_ADC_CH_CFG, &reg_val);
	scnprintf(buf + cnt, SPRD_ADC_REG_DUMP_BUF_LEN - cnt, "ch_cfg 0x%x",
		 (ret ? err_magic : reg_val));

	dev_err(data->dev, "%s\n", buf);
}

static u32 sprd_adc_get_isen(void *pri, int ch, bool enable)
{
	struct sprd_adc_data *data = (struct sprd_adc_data *)pri;

	if (!enable)
		return 0;

	return FIELD_GET(SPRD_ADC_ISEN_VAL_MASK, data->ch_data[ch].isen_info);
}

static int sprd_adc_reg_cfg(struct sprd_adc_data *data, int ch, int index, bool set)
{
	int ret;
	u32 reg_addr, val, read_val = 0;
	const struct sprd_adc_reg_bit *const reg_inf = &data->var_data->reg_list[index];
	bool set_act = ((set && !reg_inf->reverse) || (!set && reg_inf->reverse));

	if (data->var_data->reg_list[index].inited != SPRD_ADC_INIT_MAGIC)
		return 0;

	reg_addr = sprd_adc_get_reg_addr(data, index);
	if (!reg_inf->get_val)
		val = (set_act ? reg_inf->val_set : reg_inf->val_clr);
	else
		val = reg_inf->get_val(data, ch, set_act);
	val = ((val << reg_inf->offset) & reg_inf->mask);
	ret = regmap_update_bits(data->regmap, reg_addr, reg_inf->mask, val);
	if (ret) {
		dev_err(data->dev, "reg_cfg cfg err: reg[%d], ret %d\n", reg_addr, ret);
		return ret;
	}

	ret = regmap_read(data->regmap, reg_addr, &read_val);
	if (ret) {
		dev_err(data->dev, "reg_cfg read err: reg[%d], ret %d\n", reg_addr, ret);
		return ret;
	}

	dev_dbg(data->dev, "reg_cfg[%d %d]: reg 0x%x, mask: 0x%x, val: 0x%x, read_val: 0x%x\n",
		set, reg_inf->reverse, reg_addr, reg_inf->mask, val, read_val);

	return 0;
}

static int sprd_adc_regs_set_order(struct sprd_adc_data *data, int ch, int start, int end)
{
	int i, ret;

	for (i = start + 1; i < end; i++) {
		ret = sprd_adc_reg_cfg(data, ch, i, true);
		if (ret)
			return ret;
	}

	return 0;
}

static int sprd_adc_regs_clr_order_reverse(struct sprd_adc_data *data, int ch, int start, int end)
{
	int i, ret;

	for (i = end - 1; i > start; i--) {
		ret = sprd_adc_reg_cfg(data, ch, i, false);
		if (ret)
			return ret;
	}

	return 0;
}

static int sprd_adc_isen_enable(struct sprd_adc_data *data, int channel)
{
	int ret;

	if (!FIELD_GET(SPRD_ADC_ISEN_SUP_MASK, data->ch_data[channel].isen_info))
		return 0;

	ret = sprd_adc_regs_set_order(data, channel, REG_ISEN_ST, REG_ISEN_END);
	usleep_range(100, 200);

	return ret;
}

static int sprd_adc_isen_disable(struct sprd_adc_data *data, int channel)
{
	int ret;

	if (!FIELD_GET(SPRD_ADC_ISEN_SUP_MASK, data->ch_data[channel].isen_info))
		return 0;

	ret = sprd_adc_regs_clr_order_reverse(data, channel, REG_ISEN_ST, REG_ISEN_END);
	usleep_range(100, 200);

	return ret;
}

static int sprd_adc_config_fixed(struct sprd_adc_data *data)
{
	return sprd_adc_regs_set_order(data, -1, REG_CFG_FIXED_ST, REG_CFG_FIXED_END);
}

static int sprd_adc_data_average(int *vals, int len)
{
	int i, sum = 0;

	for (i = 0; i < len; i++)
		sum += vals[i];
	return DIV_ROUND_CLOSEST(sum, len);
}

static int compare_val(const void *a, const void *b)
{
	return *(int *)a - *(int *)b;
}

static int sprd_adc_get_val_with_sw_filter(struct sprd_adc_data *data, int ch, int *val)
{
	int data_buf[SPRD_ADC_MESURE_NUM_SW] = { 0 }, num = SPRD_ADC_MESURE_NUM_SW, count;
	unsigned int rawdata;

	for (count = 0; count < SPRD_ADC_MESURE_NUM_SW; count++) {
		if (regmap_read(data->regmap, data->base + SPRD_ADC_DATA, &rawdata)) {
			dev_err(data->dev, "adc read with sw filter failed.\n");
			return -EINVAL;
		}
		rawdata &= SPRD_ADC_DATA_MASK;
		data_buf[count] = rawdata;
		usleep_range(10, 20);
	}

	sort(data_buf, SPRD_ADC_MESURE_NUM_SW, sizeof(int), compare_val, NULL);
	*val = sprd_adc_data_average(&data_buf[num / 5], (num - num * 2 / 5));
	dev_dbg(data->dev, "avg_raw = %d\n", *val);

	return 0;
}

static int sprd_adc_enable(struct sprd_adc_data *data, int channel)
{
	int ret = 0, volreq = data->ch_data[channel].volreq;

	/*
	 * According to the sc2721 chip data sheet, the reference voltage of
	 * specific channel 30 and channel 31 in ADC module needs to be set from
	 * the default 2.8v to 3.5v.
	 */
	if (data->pm_data.volref && volreq > 0) {
		volreq = ((volreq > SPRD_ADC_REFVOL_VDD28) ? volreq : SPRD_ADC_REFVOL_VDD28);
		ret = regulator_set_voltage(data->pm_data.volref, volreq, volreq);
		dev_dbg(data->dev, "set volref %d to ch%d\n", volreq, channel);
		if (ret) {
			dev_err(data->dev, "failed to set the volref for ch%d, volreq %d\n",
				channel, volreq);
			return ret;
		}
	}

	if (data->pm_data.clk_regmap) {
		ret = regmap_update_bits(data->pm_data.clk_regmap, data->pm_data.clk_reg,
					 data->pm_data.clk_reg_mask,
					 data->pm_data.clk_reg_mask);
		if (ret) {
			dev_err(data->dev, "failed to enable clk26m, channel %d\n", channel);
			return ret;
		}
	}

	ret = sprd_adc_isen_enable(data, channel);
	if (ret) {
		dev_err(data->dev, "failed to enable isen\n");
		return ret;
	}

	ret = regmap_update_bits(data->regmap, data->base + SPRD_ADC_CTL,
				 SPRD_ADC_EN, SPRD_ADC_EN);
	if (ret) {
		dev_err(data->dev, "failed to set SPRD_ADC_EN\n");
		return ret;
	}

	return 0;
}

static int sprd_adc_disable(struct sprd_adc_data *data, int channel)
{
	int ret = 0, volreq = data->ch_data[channel].volreq;

	ret = regmap_update_bits(data->regmap, data->base + SPRD_ADC_CTL,
				 SPRD_ADC_CHN_RUN, 0);
	if (ret) {
		dev_err(data->dev, "failed to disable SPRD_ADC_CHN_RUN\n");
		return ret;
	}

	ret = regmap_update_bits(data->regmap, data->base + SPRD_ADC_CTL, SPRD_ADC_EN, 0);
	if (ret) {
		dev_err(data->dev, "failed to reset SPRD_ADC_EN\n");
		return ret;
	}

	ret = sprd_adc_isen_disable(data, channel);
	if (ret) {
		dev_err(data->dev, "failed to disable isen\n");
		return ret;
	}

	if (data->pm_data.clk_regmap) {
		ret = regmap_update_bits(data->pm_data.clk_regmap, data->pm_data.clk_reg,
					 data->pm_data.clk_reg_mask, 0);
		if (ret) {
			dev_err(data->dev, "failed to disable clk26m, channel %d\n", channel);
			return ret;
		}
	}

	/*
	 * According to the sc2721 chip data sheet, the reference voltage of
	 * specific channel 30 and channel 31 in ADC module needs to be set from
	 * the default 2.8v to 3.5v.
	 */
	if (data->pm_data.volref && volreq > 0) {
		volreq = SPRD_ADC_REFVOL_VDD28;
		ret = regulator_set_voltage(data->pm_data.volref, volreq, volreq);
		dev_dbg(data->dev, "reset volref %d to ch%d\n", volreq, channel);
		if (ret) {
			dev_err(data->dev, "failed to reset the volref for ch%d, volreq %d\n",
				channel, volreq);
			return ret;
		}
	}

	return 0;
}

static int sprd_adc_hwspin_lock(struct sprd_adc_data *data)
{
	if (!data->hwlock)
		return 0;

	return hwspin_lock_timeout_raw(data->hwlock, SPRD_ADC_HWLOCK_TIMEOUT_MS);
}

static void sprd_adc_hwspin_unlock(struct sprd_adc_data *data)
{
	if (!data->hwlock)
		return;

	hwspin_unlock_raw(data->hwlock);
}

static int sprd_adc_read(struct sprd_adc_data *data, int channel, int scale, int *val)
{
	int ret = 0, sample_num_sw;
	u32 rawdata = 0, tmp, status, scale_shift, scale_mask;
	bool filter_sw = FIELD_GET(SPRD_ADC_FILTER_SF_MASK, data->ch_data[channel].filter_info);
	int sample_num_hw = FIELD_GET(SPRD_ADC_FILTER_HF_MASK, data->ch_data[channel].filter_info);

	if (data->pm_data.dev_suspended) {
		dev_err(data->dev, "adc_exp: adc has been suspended, ignore.\n");
		return -EBUSY;
	}

	dev_dbg(data->dev, "ch_data[%d]: scale %d, graph %d, filter_info 0x%x, isen_info 0x%x\n",
		channel, data->ch_data[channel].scale, data->ch_data[channel].graph,
		data->ch_data[channel].filter_info, data->ch_data[channel].isen_info);

	ret = sprd_adc_hwspin_lock(data);
	if (ret) {
		dev_err(data->dev, "timeout getting the hwspinlock\n");
		return ret;
	}

	ret = sprd_adc_enable(data, channel);
	if (ret)
		goto disable_adc;

	ret = regmap_update_bits(data->regmap, data->base + SPRD_ADC_INT_CLR,
				 SPRD_ADC_IRQ_CLR, SPRD_ADC_IRQ_CLR);
	if (ret)
		goto disable_adc;

	/* Configure the channel id and scale */
	scale_shift = data->var_data->reg_list[REG_SCALE].offset;
	scale_mask = data->var_data->reg_list[REG_SCALE].mask;
	tmp = (scale << scale_shift) & scale_mask;
	tmp |= channel & SPRD_ADC_CHN_ID_MASK;
	ret = regmap_update_bits(data->regmap, data->base + SPRD_ADC_CH_CFG,
				 SPRD_ADC_CHN_ID_MASK | scale_mask, tmp);
	if (ret)
		goto disable_adc;

	/* Select 12bit conversion mode, and only sample 1 time */
	tmp = SPRD_ADC_12BIT_MODE;
	sample_num_sw = (filter_sw ? SPRD_ADC_MESURE_NUM_SW - 1 : 0);
	sample_num_hw = ((sample_num_hw > 0) ? sample_num_hw : SPRD_ADC_MESURE_NUM_HW_DEF);
	tmp |= ((unsigned long)sample_num_sw << SPRD_ADC_RUN_NUM_SHIFT) & SPRD_ADC_RUN_NUM_MASK;
	tmp |= ((unsigned long)sample_num_hw << SPRD_ADC_AVERAGE_SHIFT) & SPRD_ADC_AVERAGE_MASK;
	ret = regmap_update_bits(data->regmap, data->base + SPRD_ADC_CTL,
				 SPRD_ADC_RUN_NUM_MASK | SPRD_ADC_12BIT_MODE |
				 SPRD_ADC_AVERAGE_MASK, tmp);
	if (ret)
		goto disable_adc;

	ret = regmap_update_bits(data->regmap, data->base + SPRD_ADC_CTL,
				 SPRD_ADC_CHN_RUN, SPRD_ADC_CHN_RUN);
	if (ret)
		goto disable_adc;

	ret = regmap_read_poll_timeout(data->regmap, data->base + SPRD_ADC_INT_RAW, status,
				       (status & SPRD_ADC_IRQ_RAW), SPRD_ADC_RDY_POLL_DELAY_US,
				       SPRD_ADC_RDY_TIMEOUT_US);
	if (ret) {
		dev_err(data->dev, "read adc timeout, return %d\n", data->data_cache[channel]);
		sprd_adc_regs_dump(data, channel, scale, "t_bef");
		sprd_adc_disable(data, channel);
		sprd_adc_hw_enable(data);
		sprd_adc_soft_rst(data);
		sprd_adc_regs_dump(data, channel, scale, "t_aft");
		goto disable_adc;
	}

	if (filter_sw)
		ret = sprd_adc_get_val_with_sw_filter(data, channel, &rawdata);
	else
		ret = regmap_read(data->regmap, data->base + SPRD_ADC_DATA, &rawdata);

disable_adc:
	{
		int disable_ret = sprd_adc_disable(data, channel);
		if (disable_ret) {
			dev_err(data->dev, "adc disable failed\n");
			if (!ret) ret = disable_ret;
		}
	}

	*val = (rawdata & SPRD_ADC_DATA_MASK);

	sprd_adc_hwspin_unlock(data);

	return ret;
}

static int sprd_adc_calculate_volt_by_graph(struct sprd_adc_data *data, int channel,
					    int scale, int raw_adc)
{
	int tmp;
	struct sprd_adc_graphs *adc_graphs = &data->adc_graphs;
	int graph = data->ch_data[channel].graph;
	struct sprd_adc_linear_graph *linear_graph = &adc_graphs->linear_graphs[graph];

	tmp = (linear_graph->volt0 - linear_graph->volt1) * (raw_adc - linear_graph->adc1);
	tmp /= (linear_graph->adc0 - linear_graph->adc1);
	tmp += linear_graph->volt1;
	tmp = max(0, tmp);

	dev_dbg(data->dev, "by_graph_c%d: v0 %d a0 %d, v1 %d a1 %d, raw_adc 0x%x, vol_graph %d\n",
		channel, linear_graph->volt0, linear_graph->adc0, linear_graph->volt1,
		linear_graph->adc1, raw_adc, tmp);

	return tmp;
}

static int sprd_adc_calculate_volt_by_ratio(struct sprd_adc_data *data, int channel,
					    int scale, int vol_graph)
{
	struct u32_fract ip_fract, aux_fract;
	int pmic_type = data->var_data->pmic_type;
	u32 vol_final = vol_graph;

	ratio_to_u32_fract(data->ch_data[channel].ip_ratio, &ip_fract);
	vol_final = DIV_ROUND_CLOSEST(vol_final * ip_fract.denominator, ip_fract.numerator);

	ratio_to_u32_fract(data->ch_data[channel].ch_aux_ratio[scale], &aux_fract);
	vol_final = DIV_ROUND_CLOSEST(vol_final * aux_fract.denominator, aux_fract.numerator);

	dev_dbg(data->dev, "by_ratio_c%d: id %d, scale %d, ip_r[%d/%d], aux_r[%d/%d], vol_f %d\n",
		channel, pmic_type, scale, ip_fract.numerator, ip_fract.denominator,
		aux_fract.numerator, aux_fract.denominator, vol_final);

	return vol_final;
}

static int sprd_adc_read_processed(struct sprd_adc_data *data, int channel, int scale, int *val)
{
	int ret, raw_adc, vol_graph;

	ret = sprd_adc_read(data, channel, scale, &raw_adc);
	if (ret)
		return ret;

	vol_graph = sprd_adc_calculate_volt_by_graph(data, channel, scale, raw_adc);
	*val = sprd_adc_calculate_volt_by_ratio(data, channel, scale, vol_graph);
	data->data_cache[channel] = *val;
	return 0;
}

static int sprd_adc_ch_data_encode(struct sprd_adc_data *data, int ch)
{
	int scale = FIELD_PREP(SPRD_ADC_SCALE_MASK, data->ch_data[ch].scale);
	int isen_info = FIELD_PREP(SPRD_ADC_ISEN_MASK, data->ch_data[ch].isen_info);
	int filter_info = FIELD_PREP(SPRD_ADC_FILTER_MASK, data->ch_data[ch].filter_info);

	return (scale | isen_info | filter_info);
}

/*
 *val info:
 *bit[0-7]: scale
 *bit[8-15]: filter_info(bit8: sw filter support, bit[9-15]: hw filter val(2<<n))
 *bit[16-23]: isen_info (bit16: isen support, bit[17-23]: isen val)
 */
static void sprd_adc_ch_data_decode(struct sprd_adc_data *data, int ch, int val)
{
	int scale_override, filter_override, isen_override;

	scale_override = FIELD_GET(SPRD_ADC_SCALE_MASK, val);
	filter_override = FIELD_GET(SPRD_ADC_FILTER_MASK, val);
	isen_override = FIELD_GET(SPRD_ADC_ISEN_MASK, val);

	if (scale_override < SPRD_ADC_SCALE_MAX && scale_override != data->ch_data[ch].scale)
		data->ch_data[ch].scale = scale_override;

	if (data->ch_data[ch].filter_info != filter_override)
		data->ch_data[ch].filter_info = filter_override;

	if (data->ch_data[ch].isen_info != isen_override)
		data->ch_data[ch].isen_info = isen_override;
}

static int sprd_adc_iio_xlate(struct iio_dev *indio_dev,
			      const struct fwnode_reference_args *iiospec)
{
	struct sprd_adc_data *data = iio_priv(indio_dev);
	struct iio_chan_spec *chan_spec = data->iio_chan_spec;
	u32 chan_spec_num = data->iio_chan_spec_num, index;

	if (!iiospec->nargs)
		return 0;

	for (index = 0; index < chan_spec_num; index++)
		if (chan_spec[index].channel == iiospec->args[0])
			return index;

	dev_err(&indio_dev->dev, "invalid channel %llu\n", iiospec->args[0]);

	return -EINVAL;
}

static int sprd_adc_read_raw(struct iio_dev *indio_dev, struct iio_chan_spec const *chan,
			     int *val, int *val2, long mask)
{
	struct sprd_adc_data *data = iio_priv(indio_dev);
	int ret;
	int scale = data->ch_data[chan->channel].scale;

	switch (mask) {
	case IIO_CHAN_INFO_RAW:
		mutex_lock(&data->mutex);
		ret = sprd_adc_read(data, chan->channel, scale, val);
		mutex_unlock(&data->mutex);
		return ret ? ret : IIO_VAL_INT;
	case IIO_CHAN_INFO_PROCESSED:
		mutex_lock(&data->mutex);
		ret = sprd_adc_read_processed(data, chan->channel, scale, val);
		mutex_unlock(&data->mutex);
		return ret ? ret : IIO_VAL_INT;
	case IIO_CHAN_INFO_SCALE:
		*val = sprd_adc_ch_data_encode(data, chan->channel);
		return IIO_VAL_INT;

	default:
		return -EINVAL;
	}
}

static int sprd_adc_write_raw(struct iio_dev *indio_dev, struct iio_chan_spec const *chan,
			      int val, int val2, long mask)
{
	struct sprd_adc_data *data = iio_priv(indio_dev);

	switch (mask) {
	case IIO_CHAN_INFO_SCALE:
		mutex_lock(&data->mutex);
		sprd_adc_ch_data_decode(data, chan->channel, val);
		mutex_unlock(&data->mutex);
		return 0;

	default:
		return -EINVAL;
	}
}

static int sprd_adc_soft_rst(struct sprd_adc_data *data)
{
	int ret;
	const struct sprd_adc_reg_bit *const reg_list = data->var_data->reg_list;

	ret = regmap_update_bits(data->regmap, sprd_adc_get_reg_addr(data, REG_SOFT_RST),
				 reg_list[REG_SOFT_RST].mask, reg_list[REG_SOFT_RST].mask);
	if (ret)
		return ret;

	usleep_range(100, 200);

	ret = regmap_update_bits(data->regmap, sprd_adc_get_reg_addr(data, REG_SOFT_RST),
				 reg_list[REG_SOFT_RST].mask, 0);
	if (ret)
		return ret;

	return sprd_adc_config_fixed(data);
}

static int sprd_adc_clkin_cfg(struct sprd_adc_data *data)
{
	int ret;

	ret = sprd_adc_regs_set_order(data, -1, REG_CLK_IN_ST, REG_CLK_IN_END);
	usleep_range(100, 200);

	return ret;
}

static int sprd_adc_hw_enable(struct sprd_adc_data *data)
{
	int ret;
	const struct  sprd_adc_reg_bit *const reg_list = data->var_data->reg_list;

	ret = sprd_adc_clkin_cfg(data);
	if (ret)
		return ret;

	ret = regmap_update_bits(data->regmap, sprd_adc_get_reg_addr(data, REG_MODULE_EN),
				 reg_list[REG_MODULE_EN].mask, reg_list[REG_MODULE_EN].mask);
	if (ret)
		return ret;

	return regmap_update_bits(data->regmap, sprd_adc_get_reg_addr(data, REG_CLK_EN),
				  reg_list[REG_CLK_EN].mask, reg_list[REG_CLK_EN].mask);
}

static void sprd_adc_hw_disable(void *_data)
{
	struct sprd_adc_data *data = _data;
	int ret;
	const struct  sprd_adc_reg_bit *const reg_list = data->var_data->reg_list;

	ret = regmap_update_bits(data->regmap, sprd_adc_get_reg_addr(data, REG_CLK_EN),
				 reg_list[REG_CLK_EN].mask, 0);
	if (ret)
		dev_err(data->dev, "hw disable, REG_CLK_EN update failed\n");

	ret = regmap_update_bits(data->regmap, sprd_adc_get_reg_addr(data, REG_MODULE_EN),
				 reg_list[REG_MODULE_EN].mask, 0);

	if (ret)
		dev_err(data->dev, "hw disable, REG_MODULE_EN update failed\n");
}

static void sprd_adc_free_lock(void *_data)
{
	struct sprd_adc_data *sprd_data = _data;

	if (sprd_data->hwlock)
		hwspin_lock_free(sprd_data->hwlock);

	mutex_destroy(&sprd_data->mutex);
}

static int sprd_adc_ch_data_init(struct sprd_adc_data *data)
{
	int size, ret, ch, ch_data_val, i;
	u32 *ch_data_override;

	data->var_data->ch_data_init(data);

	size = device_property_count_u32(data->dev, "ch_data_override");
	if (size <= 0)
		return 0;

	if (size % 2) {
		dev_err(data->dev, "Pair of ch data err!\n");
		return -EINVAL;
	}

	ch_data_override = kcalloc(size, sizeof(u32), GFP_KERNEL);
	if (!ch_data_override)
		return -ENOMEM;

	ret = device_property_read_u32_array(data->dev, "ch_data_override", ch_data_override, size);
	if (ret) {
		dev_err(data->dev, "Failed to read ch data from dt: %d\n", ret);
		goto ch_data_init_clean;
	}

	for (i = 0; i < size; i += 2) {
		ch = ch_data_override[i];
		if (ch >= SPRD_ADC_CHANNEL_MAX) { ret = -EINVAL; goto ch_data_init_clean; }
		ch_data_val = ch_data_override[i + 1];
		sprd_adc_ch_data_decode(data, ch, ch_data_val);
	}

ch_data_init_clean:
	kfree(ch_data_override);

	return ret;
}

static int sprd_adc_iio_chan_spec_init(struct sprd_adc_data *data)
{
	int size, ret = 0, ch, iio_type, i;
	u32 *iio_chan_spec;
	struct iio_chan_spec *chan;

	size = device_property_count_u32(data->dev, "sprd,iio-chan-spec");
	if (size <= 0) {
		data->iio_chan_spec = sprd_channels;
		data->iio_chan_spec_num = ARRAY_SIZE(sprd_channels);
		return 0;
	}

	if (size % 2) {
		dev_err(data->dev, "Pair of iio_chan_spec err!\n");
		return -EINVAL;
	}

	iio_chan_spec = kcalloc(size, sizeof(u32), GFP_KERNEL);
	if (!iio_chan_spec)
		return -ENOMEM;

	data->iio_chan_spec_num = (size / 2);
	data->iio_chan_spec = devm_kcalloc(data->dev, data->iio_chan_spec_num,
					   sizeof(struct iio_chan_spec), GFP_KERNEL);
	if (!data->iio_chan_spec) {
		ret = -ENOMEM;
		goto iio_chan_spec_init_clean;
	}

	ret = device_property_read_u32_array(data->dev, "sprd,iio-chan-spec", iio_chan_spec, size);
	if (ret) {
		dev_err(data->dev, "Failed to read iio_chan_spec from dt: %d\n", ret);
		goto iio_chan_spec_init_clean;
	}

	for (i = 0; i < size; i += 2) {
		ch = iio_chan_spec[i];
		iio_type = iio_chan_spec[i + 1];
		chan = &data->iio_chan_spec[i / 2];
		ret = sprd_adc_chan_spec_init_once(data, chan, ch, iio_type);
		if (ret) {
			dev_err(data->dev, "Failed to init chan spec %u\n", ch);
			goto iio_chan_spec_init_clean;
		}
	}

iio_chan_spec_init_clean:
	kfree(iio_chan_spec);

	return ret;
}

static int sprd_adc_pm_init(struct sprd_adc_data *data)
{
	unsigned int pm_args[2];
	u32 reg, mask;
	struct device_node *np = data->dev->of_node;

	data->pm_data.volref = devm_regulator_get_optional(data->dev, "vref");
	if (IS_ERR(data->pm_data.volref)) {
		int error = PTR_ERR(data->pm_data.volref);
		if (error != -ENODEV) return error;
		data->pm_data.volref = NULL;
	}

	data->pm_data.clk_regmap =
		syscon_regmap_lookup_by_phandle_args(np, "sprd_adc_pm_reg", 2, pm_args);
	if (!IS_ERR_OR_NULL(data->pm_data.clk_regmap)) {
		data->pm_data.clk_reg = reg = pm_args[0];
		data->pm_data.clk_reg_mask = mask = pm_args[1];
	} else {
		data->pm_data.clk_regmap = NULL;
	}

	data->pm_data.dev_suspended = false;

	return 0;
}

static const struct iio_info sprd_info = {
	.read_raw = &sprd_adc_read_raw,
	.write_raw = &sprd_adc_write_raw,
	.fwnode_xlate = sprd_adc_iio_xlate,
};

static int sprd_adc_probe(struct platform_device *pdev)
{
	struct device_node *np = pdev->dev.of_node;
	struct sprd_adc_data *sprd_data;
	const struct sprd_adc_variant_data *pdata;
	struct iio_dev *indio_dev;
	int ret;

	pdata = device_get_match_data(&pdev->dev);
	if (!pdata)
		return dev_err_probe(&pdev->dev, -EINVAL, "No matching driver data found\n");

	indio_dev = devm_iio_device_alloc(&pdev->dev, sizeof(*sprd_data));
	if (!indio_dev)
		return dev_err_probe(&pdev->dev, -ENOMEM, "iio device alloc failed\n");

	sprd_data = iio_priv(indio_dev);

	sprd_data->regmap = dev_get_regmap(pdev->dev.parent, NULL);
	if (!sprd_data->regmap)
		return dev_err_probe(&pdev->dev, -ENODEV, "failed to get ADC regmap\n");

	ret = device_property_read_u32(&pdev->dev, "reg", &sprd_data->base);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to get ADC base address\n");

	sprd_data->irq = platform_get_irq(pdev, 0);
	if (sprd_data->irq < 0)
		return dev_err_probe(&pdev->dev, sprd_data->irq, "failed to get ADC irq number\n");

	mutex_init(&sprd_data->mutex);
	/* ADC0 has no declared lock. ADC1 must honor its real shared lock. */
	if (of_find_property(np, "hwlocks", NULL)) {
		ret = of_hwspin_lock_get_id(np, 0);
		if (ret < 0)
			return dev_err_probe(&pdev->dev, ret, "ADC hardware lock unavailable\n");
		sprd_data->hwlock = hwspin_lock_request_specific(ret);
		if (!sprd_data->hwlock)
			return dev_err_probe(&pdev->dev, -EBUSY, "ADC hardware lock already owned\n");
	}
	ret = devm_add_action_or_reset(&pdev->dev, sprd_adc_free_lock, sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to add free lock action\n");

	sprd_data->dev = &pdev->dev;
	sprd_data->var_data = pdata;
	sprd_data->indio_dev = indio_dev;

	/* ADC channel scales calibration from nvmem device */
	ret = sprd_adc_graphs_calibrate(sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to calib graphs from nvmem\n");

	ret = sprd_adc_pm_init(sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "adc pm init err.\n");

	ret = sprd_adc_ch_data_init(sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "ch data init err.\n");

	ret = sprd_adc_iio_chan_spec_init(sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "iio chan spec init err.\n");

	ret = sprd_adc_hw_enable(sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to enable ADC module\n");

	ret = devm_add_action_or_reset(&pdev->dev, sprd_adc_hw_disable, sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to add ADC disable action\n");

	ret = sprd_adc_hwspin_lock(sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "ADC reset arbitration failed\n");
	ret = sprd_adc_soft_rst(sprd_data);
	sprd_adc_hwspin_unlock(sprd_data);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "adc soft rst failed\n");

	indio_dev->dev.parent = &pdev->dev;
	indio_dev->name = dev_name(&pdev->dev);
	indio_dev->modes = INDIO_DIRECT_MODE;
	indio_dev->info = &sprd_info;
	indio_dev->channels = sprd_data->iio_chan_spec;
	indio_dev->num_channels = sprd_data->iio_chan_spec_num;
	ret = devm_iio_device_register(&pdev->dev, indio_dev);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "could not register iio (ADC)\n");

	platform_set_drvdata(pdev, indio_dev);

	return 0;
}

#ifdef CONFIG_PM_SLEEP
static int sprd_adc_pm_suspend(struct device *dev)
{
	struct sprd_adc_data *sprd_data = iio_priv(dev_get_drvdata(dev));

	mutex_lock(&sprd_data->mutex);
	sprd_data->pm_data.dev_suspended = true;
	mutex_unlock(&sprd_data->mutex);

	return 0;
}

static int sprd_adc_pm_resume(struct device *dev)
{
	struct sprd_adc_data *sprd_data = iio_priv(dev_get_drvdata(dev));

	mutex_lock(&sprd_data->mutex);
	sprd_data->pm_data.dev_suspended = false;
	mutex_unlock(&sprd_data->mutex);

	return 0;
}
#endif

static const struct sprd_adc_variant_data sc2720_data = {
	.pmic_type = SC2720_ADC,
	.glb_reg_base = 0xc00,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_sc2720,
	.graph_index = ONE_CELL_GRAPH1,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V0(5, SCALE_11, GENMASK(15, 0), 0, GENMASK(15, 0), 0),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(15, 0), 0, GENMASK(15, 0), 0)
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(1000, 1955),
			   SPRD_ADC_RATIO(1000, 2586), SPRD_ADC_RATIO(100, 406)},
	.ch_data_init = sc2720_ch_data_init,
};

static const struct sprd_adc_variant_data sc2721_data = {
	.pmic_type = SC2721_ADC,
	.glb_reg_base = 0xc00,
	.adc_reg_base_offset = 0x0,
	.reg_list = regs_sc2721,
	.graph_index = ONE_CELL_GRAPH0,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V1(5, SCALE_01, SPRD_ADC_RATIO(7, 29),
					   SPRD_ADC_RATIO(7, 29)),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V1(1, SCALE_00, SPRD_ADC_RATIO_DEF,
					   SPRD_ADC_RATIO(400, 1025))
	},
	.ch_data_init = sc2721_ch_data_init,
};

static const struct sprd_adc_variant_data sc2730_data = {
	.pmic_type = SC2730_ADC,
	.glb_reg_base = 0x1800,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_sc2730,
	.graph_index = ONE_CELL_GRAPH1,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V0(5, SCALE_11, GENMASK(15, 0), 0, GENMASK(15, 0), 0),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(15, 0), 0, GENMASK(15, 0), 0)
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(1000, 1955),
			   SPRD_ADC_RATIO(1000, 2586), SPRD_ADC_RATIO(1000, 4060)},
	.ch_data_init = sc2730_ch_data_init,
};

static const struct sprd_adc_variant_data sc2731_data = {
	.pmic_type = SC2731_ADC,
	.glb_reg_base = 0xc00,
	.adc_reg_base_offset = 0x0,
	.reg_list = regs_sc2731,
	.graph_index = ONE_CELL_GRAPH0,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V1(5, SCALE_01, SPRD_ADC_RATIO(7, 29),
					   SPRD_ADC_RATIO(7, 29)),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V1(1, SCALE_00, SPRD_ADC_RATIO_DEF,
					   SPRD_ADC_RATIO(400, 1025))
	},
	.ch_data_init = sc2731_ch_data_init,
};

static const struct sprd_adc_variant_data ump9620_data = {
	.pmic_type = UMP9620_ADC,
	.glb_reg_base = 0x2000,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_ump9620,
	.graph_index = TWO_CELL_GRAPH,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V0(11, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_VBAT_DET] =
			CALIB_INFO_INIT_V0(0, SCALE_01, GENMASK(11, 0), 4, GENMASK(11, 4), 4),
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 133),
			   SPRD_ADC_RATIO(1000, 2600), SPRD_ADC_RATIO(1000, 4060)},
	.ch_data_init = ump9620_ch_data_init,
};

static const struct sprd_adc_variant_data ump518_data = {
	.pmic_type = UMP518_ADC,
	.glb_reg_base = 0x1800,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_sc2730,
	.graph_index = TWO_CELL_GRAPH,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V0(11, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_VBAT_DET] =
			CALIB_INFO_INIT_V0(0, SCALE_01, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 133),
			   SPRD_ADC_RATIO(1000, 2600), SPRD_ADC_RATIO(1000, 4060)},
	.ch_data_init = ump9620_ch_data_init,
};

static const struct sprd_adc_variant_data ump9650_data = {
	.pmic_type = UMP9650_ADC,
	.glb_reg_base = 0x2000,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_ump9650,
	.graph_index = TWO_CELL_GRAPH,
	.calib_infos = {
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(11, 0), 0, GENMASK(11, 0), 0),
		[GRAPH_VBAT_DET] =
			CALIB_INFO_INIT_V0(0, SCALE_01, GENMASK(11, 0), 0, GENMASK(11, 0), 0),
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 133),
			   SPRD_ADC_RATIO(1000, 2600), SPRD_ADC_RATIO(1000, 4060)},
	.ch_data_init = ump9620_ch_data_init,
};

static const struct sprd_adc_variant_data ump9651_data = {
	.pmic_type = UMP9651_ADC,
	.glb_reg_base = 0x2000,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_ump9651,
	.graph_index = TWO_CELL_GRAPH,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V0(11, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_VBAT_DET] =
			CALIB_INFO_INIT_V0(0, SCALE_01, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 133),
			   SPRD_ADC_RATIO(1000, 2600), SPRD_ADC_RATIO(1000, 4060)},
	.ch_data_init = ump9620_ch_data_init,
};

static const struct sprd_adc_variant_data uip8520_data = {
	.pmic_type = UIP8520_ADC,
	.glb_reg_base = 0x2000,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_uip8520,
	.graph_index = TWO_CELL_GRAPH,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V0(11, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_VBAT_DET] =
			CALIB_INFO_INIT_V0(0, SCALE_01, GENMASK(11, 0), 4, GENMASK(11, 4), 4),
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 133),
			   SPRD_ADC_RATIO(1000, 2600), SPRD_ADC_RATIO(1000, 4060)},
	.ch_data_init = ump9620_ch_data_init,
};

static const struct sprd_adc_variant_data ump9360_data = {
	.pmic_type = UMP9360_ADC,
	.glb_reg_base = 0x2000,
	.adc_reg_base_offset = 0x4,
	.reg_list = regs_ump9360,
	.graph_index = TWO_CELL_GRAPH,
	.calib_infos = {
		[GRAPH_BIG] =
			CALIB_INFO_INIT_V0(11, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_SMALL] =
			CALIB_INFO_INIT_V0(1, SCALE_00, GENMASK(11, 0), 4, GENMASK(11, 0), 4),
		[GRAPH_VBAT_DET] =
			CALIB_INFO_INIT_V0(0, SCALE_01, GENMASK(11, 0), 4, GENMASK(11, 4), 4),
	},
	.aux_ratio_comm = {SPRD_ADC_RATIO_DEF, SPRD_ADC_RATIO(100, 133),
			   SPRD_ADC_RATIO(1000, 2600), SPRD_ADC_RATIO(1000, 4060)},
	.ch_data_init = ump9360_ch_data_init,
};

static const struct of_device_id sprd_adc_of_match[] = {
	{ .compatible = "sprd,ump9650-adc", .data = &ump9650_data },
	{ .compatible = "sprd,ump9651-adc", .data = &ump9651_data },
	{ }
};
MODULE_DEVICE_TABLE(of, sprd_adc_of_match);

static const struct dev_pm_ops sprd_adc_pm_ops = {
	SET_NOIRQ_SYSTEM_SLEEP_PM_OPS(sprd_adc_pm_suspend, sprd_adc_pm_resume)
};

static struct platform_driver sprd_adc_driver = {
	.probe = sprd_adc_probe,
	.driver = {
		.name = "u30pro-ump965x-adc",
		.of_match_table = sprd_adc_of_match,
		.pm = &sprd_adc_pm_ops,
	},
};

module_platform_driver(sprd_adc_driver);
MODULE_DESCRIPTION("SPRD PMIC ADC Driver");
MODULE_LICENSE("GPL");
