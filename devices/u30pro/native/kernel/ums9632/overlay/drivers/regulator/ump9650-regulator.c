// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2023 Spreadtrum Communications Inc.
 */


#include <linux/debugfs.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mfd/ump965x/sprd,ump9650-pmu-ctrl.h>
#include <linux/mfd/ump965x/sprd,ump965x-mask.h>
#include <linux/mfd/ump965x/sprd,ump965x-regs.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/of_regulator.h>
#include <linux/seq_buf.h>
#include <linux/sched.h>
#include "ump9650-regulator.h"

#define UMP9650_WR_UNLOCK_VALUE			0x6e7f
#define SPRD_UMP9650_CA_VAL			0xC000

#define SPRD_SOCP_PRINT_BUF_LEN			8192
#define UMP9650_printf(m, x...)				\
	do {						\
		if (!m)					\
			pr_debug(x);			\
		else if (seq_buf_printf(m, x)) {	\
			seq_buf_clear(m);		\
			seq_buf_printf(m, x);		\
		}					\
	} while (0)

enum ump9650_regulator_id {
	UMP9650_LDO_VDDVMEMUFSX,
	UMP9650_LDO_VDDUSB33,
	UMP9650_LDO_VDDSDCORE,
	UMP9650_LDO_VDDSDIO,
	UMP9650_LDO_VDDSIM0,
	UMP9650_LDO_VDDSIM1,
	UMP9650_LDO_VDDRF1V8,
	UMP9650_LDO_VDDVRFFE12,
	UMP9650_LDO_VDDVRFFE18,
	UMP9650_LDO_VDDVRFFE28,
	UMP9650_LDO_VDDWCN,
	UMP9650_LDO_VDDRF1V1,
	UMP9650_LDO_AVDD12_1,
	UMP9650_LDO_AVDD12_2,
	UMP9650_LDO_VDDMEMQ,
	UMP9650_LDO_VDDVUFSQ12,
	UMP9650_LDO_AVDD18,
	UMP9650_LDO_VDDRF0V9,
	UMP9650_DCDC_WPA,
	UMP9650_LDO_VDD28_0,
	UMP9650_LDO_MEML,
	UMP9650_LDO_VDDWIFIPA1,
	UMP9650_LDO_VDDWIFIPA2,
	UMP9650_LDO_VDDVWCNXO,
	UMP9650_DCDC_MEM,
	UMP9650_LDO_VDDPUB,
	UMP9650_LDO_VDDVPST12,
	UMP9650_LDO_AVDD0V75,
	UMP9650_DCDC_GEN0,
	UMP9650_DCDC_CORE,
	UMP9650_DCDC_GEN1,
	UMP9650_DCDC_GEN2,
	UMP9650_DCDC_GEN3,
	UMP9650_DCDC_GEN4,
	UMP9650_LDO_VDDVPST06,
	UMP9650_LDO_VDDVPST15,
	UMP9650_LDO_VDD18_DCXO,
	UMP9650_LDO_AVDD12_2_SLP,
	UMP9650_LDO_AVDD18_SLP,
	UMP9650_DCDC_CORE_SLP,
	UMP9650_LDO_VDDWCN_SLP,
	UMP9650_LDO_VDDVWCNXO_SLP,
	UMP9650_LDO_VDDWIFIPA1_SLP,
	UMP9650_PWR_MAX,
};

static struct dentry *debugfs_root;

static u16 debugfs_locken = 0xFFFF;

/*
 * struct sprd_ump9650_eic_dbnc_desc_priv - Drv descriptor
 * @power: Identifying name for the regu-socp.
 * @ie: Regu-socp interrupt controller.
 * @scp: Regu-scp mode controller.
 * @type_power: Identifying the typec of regu-socp.
 *		BIT4: (0: off, 1: alive); BIT0~BIT3: (0: ldo ; 2: dcdc).
 * @eic_dbnc_ocp: The eic_dbnc id of regu-ocp signal. When the ocp occurs,
 *		  locating the source of the interrupt event. And you can
 *		  choose whether to mask the interrupt source or not.
 * @eic_dbnc_scp: The eic_dbnc id of regu-scp signal. When the ocp occurs,
 *		  locating the source of the interrupt event. And you can
 *		  choose whether to mask the interrupt source or not.
 * @ocp_ctrl: Control the ocp mode according to the system requirements.
 * @ocp_pd_clr_bit: Turn on the power that was shutdown due to ocp event.
 * @ocp_mode_bit: Choose whether to just record the event or shutdown the
 *		  power and record the event both.
 * @ocp_flag_clr_bit: Clear the power ocp flag after ocp event.
 * @scp_ctrl: Control the scp mode according to the system requirements.
 * @scp_pd_clr_bit: Turn on the power that was shutdown due to scp event.
 * @scp_mode_bit: Choose whether to just record the event or shutdown the
 *		  power and record the event both.
 * @scp_flag_clr_bit: Clear the power scp flag after scp event.
 * @ocp_flag: Register that record all of the ocp individual events.
 * @ocp_occur: Restore the ocp flag value.
 * @scp_flag: Register that record all of the scp individual events.
 * @scp_occur: Restore the scp flag value.
 */

struct sprd_ump9650_eic_dbnc_desc_priv {
	const char *power;
	u32 ie;
	u32 scp;
	int type_power;
	int eic_dbnc_ocp;
	int eic_dbnc_scp;
	unsigned long ocp_ctrl;
	u32 ocp_pd_clr_bit;
	u32 ocp_mode_bit;
	u32 ocp_flag_clr_bit;
	unsigned long scp_ctrl;
	u32 scp_pd_clr_bit;
	u32 scp_mode_bit;
	u32 scp_flag_clr_bit;
	unsigned long ocp_flag;
	u32 ocp_occur;
	unsigned long scp_flag;
	u32 scp_occur;
};

struct sprd_ump9650_eic_dbnc {
	struct device *dev;
	struct regmap *reg_map;
	int irq;
	u32 eic_dbnc_base;
	u32 glb_base;
	struct regulator_dev *rdev[UMP9650_PWR_MAX];
};

struct sprd_ump9650_eic_dbnc_info {
	u8 bit_bank;
	u8 bit_num;
};

static char *sprd_ump9650_buf;
static struct seq_buf *sprd_ump9650_seq_buf;
static struct debugfs_blob_wrapper ump9650_fault_blob;

static struct sprd_ump9650_eic_dbnc_desc_priv sprd_ump9650_eic_dbnc_desc[] = {
	{"vddvmemufsx_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC5_VMEMUFSX_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, MASK_ANA_UMP9650_LDO_VMEMUFSX_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VMEMUFSX_OCP_MODE, MASK_ANA_UMP9650_LDO_VMEMUFSX_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VMEMUFSX_OCP_FLAG, 0, 0},
	{"vddusb33_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC7_VDDUSB33_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, 0,
		0, MASK_ANA_UMP9650_LDO_VDDUSB33_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VDDUSB33_OCP_FLAG, 0, 0},
	{"vddsdcore_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC3_VDDSDCORE_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, MASK_ANA_UMP9650_LDO_VDDSDCORE_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDSDCORE_OCP_MODE,
		MASK_ANA_UMP9650_LDO_VDDSDCORE_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VDDSDCORE_OCP_FLAG, 0, 0},
	{"vddsdio_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC4_VDDSDIO_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, 0,
		0, MASK_ANA_UMP9650_LDO_VDDSDIO_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VDDSDIO_OCP_FLAG, 0, 0},
	{"vddsim0_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC0_VDDSIM0_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, 0,
		0, MASK_ANA_UMP9650_LDO_VDDSIM0_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDSIM0_OCP_FLAG, 0, 0},
	{"vddsim1_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC1_VDDSIM1_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, 0,
		0, MASK_ANA_UMP9650_LDO_VDDSIM1_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDSIM1_OCP_FLAG, 0, 0},
	{"vddrf18_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC14_VDDRF18_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL1, MASK_ANA_UMP9650_LDO_VDDRF18_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDRF18_OCP_MODE, MASK_ANA_UMP9650_LDO_VDDRF18_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDRF18_OCP_FLAG, 0, 0},
	{"vddvrffe12_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC22_VRFFE12_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, 0,
		0, MASK_ANA_UMP9650_LDO_VRFFE12_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VRFFE12_OCP_FLAG, 0, 0},
	{"vddvrffe18_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC13_VRFFE18_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL1, MASK_ANA_UMP9650_LDO_VRFFE18_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VRFFE18_OCP_MODE, MASK_ANA_UMP9650_LDO_VRFFE18_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VRFFE18_OCP_FLAG, 0, 0},
	{"vddvrffe28_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC6_VRFFE28_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL1, 0,
		0, MASK_ANA_UMP9650_LDO_VRFFE28_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VRFFE28_OCP_FLAG, 0, 0},
	{"vddwcn_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC18_VDDWCN_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL2, MASK_ANA_UMP9650_LDO_VDDWCN_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDWCN_OCP_MODE, MASK_ANA_UMP9650_LDO_VDDWCN_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDWCN_OCP_FLAG, 0, 0},
	{"vddrf1v1_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC16_VDDRF1V1_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL2, MASK_ANA_UMP9650_LDO_VDDRF1V1_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDRF1V1_OCP_MODE, MASK_ANA_UMP9650_LDO_VDDRF1V1_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDRF1V1_OCP_FLAG, 0, 0},
	{"avdd12_1_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC17_AVDD12_1_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL2, MASK_ANA_UMP9650_LDO_AVDD12_1_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_AVDD12_1_OCP_MODE, MASK_ANA_UMP9650_LDO_AVDD12_1_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_AVDD12_1_OCP_FLAG, 0, 0},
	{"avdd12_2_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC21_AVDD12_2_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, MASK_ANA_UMP9650_LDO_AVDD12_2_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_AVDD12_2_OCP_MODE, MASK_ANA_UMP9650_LDO_AVDD12_2_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_AVDD12_2_OCP_FLAG, 0, 0},
	{"vddmemq_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC27_VDDMEMQ_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, MASK_ANA_UMP9650_LDO_VDDMEMQ_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDMEMQ_OCP_MODE, MASK_ANA_UMP9650_LDO_VDDMEMQ_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDMEMQ_OCP_FLAG, 0, 0},
	{"vddvufsq12_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC15_VUFSQ1V2_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL2, MASK_ANA_UMP9650_LDO_VUFSQ1V2_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VUFSQ1V2_OCP_MODE, MASK_ANA_UMP9650_LDO_VUFSQ1V2_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VUFSQ1V2_OCP_FLAG, 0, 0},
	{"avdd18_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC12_AVDD18_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL1, 0,
		0, MASK_ANA_UMP9650_LDO_AVDD18_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_AVDD18_OCP_FLAG, 0, 0},
	{"vddrf0v9_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC23_VDDRF0V9_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, MASK_ANA_UMP9650_LDO_VDDRF0V9_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDRF0V9_OCP_MODE, MASK_ANA_UMP9650_LDO_VDDRF0V9_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDRF0V9_OCP_FLAG, 0, 0},
	{"vddwpa_socp", 0, 0, 0x12, UMP9650_EIC_DBNC33_DCDC_WPA_OCP,
		UMP9650_EIC_DBNC41_DCDC_WPA_SCP,
		REG_ANA_UMP9650_DCDC_OCP_CTRL1, MASK_ANA_UMP9650_DCDC_WPA_OCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_WPA_OCP_MODE, MASK_ANA_UMP9650_DCDC_WPA_OCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_SCP_CTRL1, MASK_ANA_UMP9650_DCDC_WPA_SCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_WPA_SCP_MODE, MASK_ANA_UMP9650_DCDC_WPA_SCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_WPA_OCP_FLAG,
		REG_ANA_UMP9650_DCDC_SCP_FLAG0, MASK_ANA_UMP9650_DCDC_WPA_SCP_FLAG},
	{"vdd28_0_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC8_VDD28_0_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, 0,
		0, MASK_ANA_UMP9650_LDO_VDD28_0_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VDD28_0_OCP_FLAG, 0, 0},
	{"vddmeml_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC25_VDDMEML_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, MASK_ANA_UMP9650_LDO_VDDMEML_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDMEML_OCP_MODE, MASK_ANA_UMP9650_LDO_VDDMEML_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDMEML_OCP_FLAG, 0, 0},
	{"vddwifipa1_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC9_VDDWIFIPA1_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL1, MASK_ANA_UMP9650_LDO_VDDWIFIPA1_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDWIFIPA1_OCP_MODE,
		MASK_ANA_UMP9650_LDO_VDDWIFIPA1_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VDDWIFIPA1_OCP_FLAG, 0, 0},
	{"vddwifipa2_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC10_VDDWIFIPA2_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL1, MASK_ANA_UMP9650_LDO_VDDWIFIPA2_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDWIFIPA2_OCP_MODE,
		MASK_ANA_UMP9650_LDO_VDDWIFIPA2_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VDDWIFIPA2_OCP_FLAG, 0, 0},
	{"vddvwcnxo_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC2_VWCNXO_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL0, 0,
		0, MASK_ANA_UMP9650_LDO_VWCNXO_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VWCNXO_OCP_FLAG, 0, 0},
	{"vddmem_socp", 0, 0, 0x12, UMP9650_EIC_DBNC30_DCDC_MEM_OCP,
		UMP9650_EIC_DBNC38_DCDC_MEM_SCP,
		REG_ANA_UMP9650_DCDC_OCP_CTRL1, MASK_ANA_UMP9650_DCDC_MEM_OCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_MEM_OCP_MODE, MASK_ANA_UMP9650_DCDC_MEM_OCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_SCP_CTRL1, MASK_ANA_UMP9650_DCDC_MEM_SCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_MEM_SCP_MODE, MASK_ANA_UMP9650_DCDC_MEM_SCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_MEM_OCP_FLAG,
		REG_ANA_UMP9650_DCDC_SCP_FLAG0, MASK_ANA_UMP9650_DCDC_MEM_SCP_FLAG},
	{"vddpub_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC24_VDDPUB_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, MASK_ANA_UMP9650_LDO_VDDPUB_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_VDDPUB_OCP_MODE, MASK_ANA_UMP9650_LDO_VDDPUB_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_VDDPUB_OCP_FLAG, 0, 0},
	{"vddvpst12_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC20_VPST12_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, 0,
		0, MASK_ANA_UMP9650_LDO_VPST12_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VPST12_OCP_FLAG, 0, 0},
	{"avdd0v75_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC26_AVDD0V75_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL4, MASK_ANA_UMP9650_LDO_AVDD0V75_OCP_PD_CLR,
		MASK_ANA_UMP9650_LDO_AVDD0V75_OCP_MODE, MASK_ANA_UMP9650_LDO_AVDD0V75_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG1, MASK_ANA_UMP9650_LDO_AVDD0V75_OCP_FLAG, 0, 0},
	{"vddgen0_socp", 0, 0, 0x12, UMP9650_EIC_DBNC31_DCDC_GEN0_OCP,
		UMP9650_EIC_DBNC39_DCDC_GEN0_SCP,
		REG_ANA_UMP9650_DCDC_OCP_CTRL0, MASK_ANA_UMP9650_DCDC_GEN0_OCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_GEN0_OCP_MODE, MASK_ANA_UMP9650_DCDC_GEN0_OCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_SCP_CTRL0, MASK_ANA_UMP9650_DCDC_GEN0_SCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_GEN0_SCP_MODE, MASK_ANA_UMP9650_DCDC_GEN0_SCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN0_OCP_FLAG,
		REG_ANA_UMP9650_DCDC_SCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN0_SCP_FLAG},
	{"vddcore_socp", 0, 0, 0x12, UMP9650_EIC_DBNC29_DCDC_CORE_OCP,
		UMP9650_EIC_DBNC37_DCDC_CORE_SCP,
		REG_ANA_UMP9650_DCDC_OCP_CTRL1, MASK_ANA_UMP9650_DCDC_CORE_OCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_CORE_OCP_MODE, MASK_ANA_UMP9650_DCDC_CORE_OCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_SCP_CTRL1, MASK_ANA_UMP9650_DCDC_CORE_SCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_CORE_SCP_MODE, MASK_ANA_UMP9650_DCDC_CORE_SCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_CORE_OCP_FLAG,
		REG_ANA_UMP9650_DCDC_SCP_FLAG0, MASK_ANA_UMP9650_DCDC_CORE_SCP_FLAG},
	{"vddgen1_socp", 0, 0, 0x12, UMP9650_EIC_DBNC32_DCDC_GEN1_OCP,
		UMP9650_EIC_DBNC40_DCDC_GEN1_SCP,
		REG_ANA_UMP9650_DCDC_OCP_CTRL0, MASK_ANA_UMP9650_DCDC_GEN1_OCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_GEN1_OCP_MODE, MASK_ANA_UMP9650_DCDC_GEN1_OCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_SCP_CTRL0, 0,
		MASK_ANA_UMP9650_DCDC_GEN1_SCP_MODE,
		MASK_ANA_UMP9650_DCDC_GEN1_SCP_CHIP_PD_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN1_OCP_FLAG,
		REG_ANA_UMP9650_DCDC_SCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN1_SCP_CHIP_PD_FLAG},
	{"vddgen2_socp", 0, 0, 0x12, UMP9650_EIC_DBNC34_DCDC_GEN2_OCP, 0,
		REG_ANA_UMP9650_DCDC_OCP_CTRL0, 0,
		MASK_ANA_UMP9650_DCDC_GEN2_OCP_MODE,
		MASK_ANA_UMP9650_DCDC_GEN2_OCP_CHIP_PD_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN2_OCP_CHIP_PD_FLAG, 0, 0},
	{"vddgen3_socp", 0, 0, 0x12, UMP9650_EIC_DBNC35_DCDC_GEN3_OCP,
		UMP9650_EIC_DBNC42_DCDC_GEN3_SCP,
		REG_ANA_UMP9650_DCDC_OCP_CTRL0, MASK_ANA_UMP9650_DCDC_GEN3_OCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_GEN3_OCP_MODE, MASK_ANA_UMP9650_DCDC_GEN3_OCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_SCP_CTRL0, MASK_ANA_UMP9650_DCDC_GEN3_SCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_GEN3_SCP_MODE, MASK_ANA_UMP9650_DCDC_GEN3_SCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN3_OCP_FLAG,
		REG_ANA_UMP9650_DCDC_SCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN3_SCP_FLAG},
	{"vddgen4_socp", 0, 0, 0x12, UMP9650_EIC_DBNC36_DCDC_GEN4_OCP,
		UMP9650_EIC_DBNC43_DCDC_GEN4_SCP,
		REG_ANA_UMP9650_DCDC_OCP_CTRL0, MASK_ANA_UMP9650_DCDC_GEN4_OCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_GEN4_OCP_MODE, MASK_ANA_UMP9650_DCDC_GEN4_OCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_SCP_CTRL0, MASK_ANA_UMP9650_DCDC_GEN4_SCP_PD_CLR,
		MASK_ANA_UMP9650_DCDC_GEN4_SCP_MODE, MASK_ANA_UMP9650_DCDC_GEN4_SCP_FLAG_CLR,
		REG_ANA_UMP9650_DCDC_OCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN4_OCP_FLAG,
		REG_ANA_UMP9650_DCDC_SCP_FLAG0, MASK_ANA_UMP9650_DCDC_GEN4_SCP_FLAG},
	{"vddvpst06_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC28_VPST06_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, 0,
		0, MASK_ANA_UMP9650_LDO_VPST06_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VPST06_OCP_FLAG, 0, 0},
	{"vddvpst15_ocp", 0, 0, 0x10, UMP9650_EIC_DBNC19_VPST15_OCP, 0,
		REG_ANA_UMP9650_LDO_OCP_CTRL3, 0,
		0, MASK_ANA_UMP9650_LDO_VPST15_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9650_LDO_OCP_FLAG0, MASK_ANA_UMP9650_LDO_VPST15_OCP_FLAG, 0, 0},
	{"vdd18_dcxo_ocp", 0, 0, 0x10, 0, 0,
		0, 0,
		0, 0,
		0, 0, 0, 0,
		0, 0, 0, 0},
	{"otp_warn_out", 0, 0, 0x10, UMP9650_EIC_DBNC11_OTP_WARN_OCP, 0,
		0, 0,
		0, 0,
		0, 0, 0, 0,
		0, 0, 0, 0},
};

static inline unsigned long sprd_socp_ffs(u64 word)
{
	int num = 0;

	if ((word & 0xffffffff) == 0) {
		num += 32;
		word >>= 32;
	}

	if ((word & 0xffff) == 0) {
		num += 16;
		word >>= 16;
	}

	if ((word & 0xff) == 0) {
		num += 8;
		word >>= 8;
	}

	if ((word & 0xf) == 0) {
		num += 4;
		word >>= 4;
	}

	if ((word & 0x3) == 0) {
		num += 2;
		word >>= 2;
	}

	if ((word & 0x1) == 0)
		num += 1;

	return num;
}

static int sprd_ump9650_dbnc_id_get(int eic_dbnc_id)
{
	int i, count;

	count = ARRAY_SIZE(sprd_ump9650_eic_dbnc_desc);
	for (i = 0; i < count; i++) {
		if ((eic_dbnc_id == sprd_ump9650_eic_dbnc_desc[i].eic_dbnc_ocp) ||
			(eic_dbnc_id == sprd_ump9650_eic_dbnc_desc[i].eic_dbnc_scp))

			return i;
	}

	return -1;
}

static struct sprd_ump9650_eic_dbnc_desc_priv *sprd_ump9650_dbnc_get(const char *dbnc_id)
{
	int i, count;

	count = ARRAY_SIZE(sprd_ump9650_eic_dbnc_desc);
	for (i = 0; i < count; i++) {
		if (!strcmp(dbnc_id, sprd_ump9650_eic_dbnc_desc[i].power))
			return &sprd_ump9650_eic_dbnc_desc[i];
	}

	return NULL;
}

static int sprd_ump9650_power_ocp_mode_ctrl(struct sprd_ump9650_eic_dbnc *eic_dbnc,
					    const char dbnc_id[], bool ocp_mode)
{
	int ret;
	struct sprd_ump9650_eic_dbnc_desc_priv *desc = sprd_ump9650_dbnc_get(dbnc_id);

	if (!desc) {
		pr_err("Sprd Ocp Mode Ctrl Passed err Id [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	if (!desc->ocp_mode_bit) {
		pr_err("Sprd Ocp Mode Ctrl Passed err Power [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	ret = regmap_update_bits(eic_dbnc->reg_map, desc->ocp_ctrl,
				 desc->ocp_mode_bit,
				 ocp_mode ? desc->ocp_mode_bit : 0);
	if (ret) {
		pr_err("Sprd Ocp Mode Ctrl failed Id [%s] [err %d]!\n", dbnc_id, ret);
		return ret;
	}

	return 0;
}

static int sprd_ump9650_power_scp_mode_ctrl(struct sprd_ump9650_eic_dbnc *eic_dbnc,
					    const char dbnc_id[], bool scp_mode)
{
	int ret, type;
	struct sprd_ump9650_eic_dbnc_desc_priv *desc = sprd_ump9650_dbnc_get(dbnc_id);

	if (!desc) {
		pr_err("Sprd Scp Mode Ctrl Passed err Id [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	type = desc->type_power & (BIT(4) - 1);
	if (type == 2) {
		ret = regmap_update_bits(eic_dbnc->reg_map, desc->scp_ctrl,
					 desc->scp_mode_bit,
					 scp_mode ? desc->scp_mode_bit : 0);
		if (ret) {
			pr_err("Sprd Scp Mode Ctrl failed Id [%s] [err %d]!\n", dbnc_id, ret);
			return ret;
		}
	} else {
		pr_err("Sprd Scp Mode Ctrl Passed err Power [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	return 0;
}

static int sprd_ump9650_get_eic_dbnc_base_info(u32 eic_dbnc_id,
					       struct sprd_ump9650_eic_dbnc_info *info)
{
	info->bit_num  = eic_dbnc_id & 0xF;

	if (eic_dbnc_id >= SPRD_UMP9650_EIC_DBNC_BANK0_START &&
			eic_dbnc_id <= SPRD_UMP9650_EIC_DBNC_BANK0_END)
		info->bit_bank = 0;
	else if (eic_dbnc_id >= SPRD_UMP9650_EIC_DBNC_BANK1_START &&
			eic_dbnc_id <= SPRD_UMP9650_EIC_DBNC_BANK1_END)
		info->bit_bank = 1;
	else if (eic_dbnc_id >= SPRD_UMP9650_EIC_DBNC_BANK2_START &&
			eic_dbnc_id <= SPRD_UMP9650_EIC_DBNC_BANK2_END)
		info->bit_bank = 2;
	else {
		pr_err("Sprd Eic Dbnc Base Info Passed err Id [%d]!\n", eic_dbnc_id);
		return -EINVAL;
	}

	pr_info("Sprd Eic Dbnc Base Info Get Id:%d -> Bank:%d", eic_dbnc_id, info->bit_bank);

	return 0;
}

static int sprd_ump9650_eic_dbnc_irq_mask(struct sprd_ump9650_eic_dbnc *eic_dbnc,
					    unsigned int offset, bool mask_val)
{
	unsigned int eic_dbnc_id = offset;
	struct sprd_ump9650_eic_dbnc_info eic_dbnc_info;
	unsigned long reg_addr;
	int ret;

	ret = sprd_ump9650_get_eic_dbnc_base_info(eic_dbnc_id, &eic_dbnc_info);
	if (ret) {
		pr_err("Sprd Eic Dbnc Irq Mask Get Base Info err Id [%d]!\n", eic_dbnc_id);
		return -EINVAL;
	}

	if (eic_dbnc_info.bit_bank == 0)
		reg_addr = SPRD_UMP9650_EIC_DBNC_IE0;
	else if (eic_dbnc_info.bit_bank == 1)
		reg_addr = SPRD_UMP9650_EIC_DBNC_IE1;
	else
		reg_addr = SPRD_UMP9650_EIC_DBNC_IE2;

	ret = regmap_update_bits(eic_dbnc->reg_map,
				 eic_dbnc->eic_dbnc_base + reg_addr,
				 SPRD_UMP9650_BIT(eic_dbnc_info.bit_num),
				 mask_val ? 0 : SPRD_UMP9650_BIT(eic_dbnc_info.bit_num));

	if (ret) {
		pr_err("Sprd Eic Dbnc Irq MaskVal-%d failed Id [%d]!\n",
			mask_val, eic_dbnc_id);
		return ret;
	}

	return 0;
}

static int sprd_ump9650_eic_dbnc_module_enable(struct sprd_ump9650_eic_dbnc *eic_dbnc, bool enable)
{
	int ret;
	u32 reg_val;

	reg_val = enable ? MASK_ANA_UMP9650_EIC_DBNC_EN : 0;

	ret = regmap_update_bits(eic_dbnc->reg_map, REG_ANA_UMP9650_MODULE_EN0,
				 MASK_ANA_UMP9650_EIC_DBNC_EN, reg_val);
	if (ret) {
		pr_err("Sprd Eic Dbnc Module En failed!\n");
		return ret;
	}

	reg_val = enable ? MASK_ANA_UMP9650_RTC_EIC_DBNC_EN : 0;

	ret = regmap_update_bits(eic_dbnc->reg_map, REG_ANA_UMP9650_RTC_CLK_EN0,
				 MASK_ANA_UMP9650_RTC_EIC_DBNC_EN, reg_val);

	if (ret) {
		pr_err("Sprd Eic Dbnc Rtc Clk En failed!\n");
		return ret;
	}

	return 0;
}

static int sprd_ump9650_eic_dbnc_mode_ctrl_manul(struct sprd_ump9650_eic_dbnc *eic_dbnc)
{
	int ret, i, count;

	/*
	 * Before enable this Module, check what the pwr you care is choosed the
	 * right mode (ocp/scp). Generally for the system exception protection,
	 * this driver is mainly for maintenance record.
	 */

	count = ARRAY_SIZE(sprd_ump9650_eic_dbnc_desc);
	for (i = 0; i < count; i++) {
		if (!sprd_ump9650_eic_dbnc_desc[i].scp)
			continue;

		ret = sprd_ump9650_power_scp_mode_ctrl(eic_dbnc,
				sprd_ump9650_eic_dbnc_desc[i].power, 1);
		if (ret)
			pr_err("Sprd Scp Ctrl [%s] failed!\n",
				sprd_ump9650_eic_dbnc_desc[i].power);
	}

	/*
	 * Default IE, Mask/UnMask EIC_DBNC_NUM Here.
	 * < eic_dbnc_num: 208 ~ 253 >
	 * ret = sprd_eic_dbnc_irq_mask(eic_dbnc, eic_dbnc_num);
	 */

	/* EIC_DBNC 11 - Over Tmp Warn */
	ret = sprd_ump9650_eic_dbnc_irq_mask(eic_dbnc, UMP9650_EIC_DBNC11_OTP_WARN_OCP, 0);
	if (ret)
		pr_err("Sprd Irq Unmask [OTP WARN] failed!\n");

	for (i = 0; i < count - 1; i++) {
		if (!sprd_ump9650_eic_dbnc_desc[i].ie)
			continue;

		ret = sprd_ump9650_eic_dbnc_irq_mask(eic_dbnc,
				sprd_ump9650_eic_dbnc_desc[i].eic_dbnc_ocp, 0);

		if (sprd_ump9650_eic_dbnc_desc[i].eic_dbnc_scp)
			ret |= sprd_ump9650_eic_dbnc_irq_mask(eic_dbnc,
				sprd_ump9650_eic_dbnc_desc[i].eic_dbnc_scp, 0);

		if (ret)
			pr_err("Sprd Irq Unmask [%s] failed!\n",
				sprd_ump9650_eic_dbnc_desc[i].power);
	}

	return ret;
}

static int sprd_ump9650_eic_dbnc_irq_ack(struct sprd_ump9650_eic_dbnc *eic_dbnc,
					 unsigned int offset)
{
	unsigned int eic_dbnc_id = offset;
	struct sprd_ump9650_eic_dbnc_info eic_dbnc_info;
	unsigned long reg_addr;
	int ret;

	ret = sprd_ump9650_get_eic_dbnc_base_info(eic_dbnc_id, &eic_dbnc_info);
	if (ret) {
		pr_err("Sprd Eic Dbnc Irq Ack Get Base Info err Id [%d]!\n", eic_dbnc_id);
		return -EINVAL;
	}

	if (eic_dbnc_info.bit_bank == 0)
		reg_addr = SPRD_UMP9650_EIC_DBNC_IC0;
	else if (eic_dbnc_info.bit_bank == 1)
		reg_addr = SPRD_UMP9650_EIC_DBNC_IC1;
	else
		reg_addr = SPRD_UMP9650_EIC_DBNC_IC2;

	ret = regmap_update_bits(eic_dbnc->reg_map, eic_dbnc->eic_dbnc_base + reg_addr,
				 BIT(eic_dbnc_info.bit_num),
				 BIT(eic_dbnc_info.bit_num));
	if (ret) {
		pr_err("Sprd Eic Dbnc Irq Ack failed Id [%d]!\n", eic_dbnc_id);
		return ret;
	}

	return 0;
}

static irqreturn_t sprd_ump9650_eic_dbnc_handler(int irq, void *data)
{
	struct sprd_ump9650_eic_dbnc *eic_dbnc = data;
	int ret = IRQ_NONE, n, eic_dbnc_id;
	u64 value;
	u32 tmp = 0;

	ret = regmap_read(eic_dbnc->reg_map,
			  eic_dbnc->eic_dbnc_base + SPRD_UMP9650_EIC_DBNC_MIS2, &tmp);
	if (ret)
		return IRQ_RETVAL(ret);

	value = tmp;
	value = value << 32;

	ret = regmap_read(eic_dbnc->reg_map,
			  eic_dbnc->eic_dbnc_base + SPRD_UMP9650_EIC_DBNC_MIS1, &tmp);
	if (ret)
		return IRQ_RETVAL(ret);

	value |= tmp << 16;

	ret = regmap_read(eic_dbnc->reg_map,
			  eic_dbnc->eic_dbnc_base + SPRD_UMP9650_EIC_DBNC_MIS0, &tmp);
	if (ret)
		return IRQ_RETVAL(ret);

	value |= tmp;

	while (value) {
		n = sprd_socp_ffs(value);
		value &= ~(1UL << n);
		eic_dbnc_id = SPRD_UMP9650_EIC_DBNC_BANK0_START + n;

		ret = sprd_ump9650_eic_dbnc_irq_mask(eic_dbnc, eic_dbnc_id, 1);
		if (ret)
			return IRQ_RETVAL(ret);

		ret = sprd_ump9650_eic_dbnc_irq_ack(eic_dbnc, eic_dbnc_id);
		if (ret)
			return IRQ_RETVAL(ret);

		ret = sprd_ump9650_dbnc_id_get(eic_dbnc_id);
		if ((ret >= 0) && (eic_dbnc->rdev[ret] != NULL))
			regulator_notifier_call_chain(eic_dbnc->rdev[ret], eic_dbnc_id, NULL);
		else
			pr_err("Sprd Ump9650 Irq Ack X NOTIFIER [%d/%d]!\n", eic_dbnc_id, ret);

		UMP9650_printf(sprd_ump9650_seq_buf,
				"Sprd Ump9650 [%d] Occurs Ocp/Scp OoOs!\n", eic_dbnc_id);

		pr_err("Sprd Ump9650 [%d] Occurs Ocp/Scp OoOs!\n", eic_dbnc_id);

		//ret = sprd_ump9650_eic_dbnc_irq_mask(eic_dbnc, eic_dbnc_id, 0);
		//if (ret)
		//	return IRQ_RETVAL(ret);
	}

	return IRQ_HANDLED;
}

static int sprd_clear_sim_int_status(struct regulator_dev *rdev, unsigned int MASK_SIM_INT0)
{
	int rint = 0, ret;

	ret = regmap_read(rdev->regmap, REG_ANA_UMP9650_SIM_INT_CTRL2, &rint);
	if (ret)
		return ret;

	if ((rint & MASK_SIM_INT0) && (rint & MASK_ANA_UMP9650_SIM_INT0_STATUS))
		ret = regmap_update_bits(rdev->regmap, REG_ANA_UMP9650_SIM_INT_CTRL2,
					 MASK_ANA_UMP9650_SIM_INT0_CLR,
					 MASK_ANA_UMP9650_SIM_INT0_CLR);
	return ret;
}

static int sprd_resolve_slp_state_relationship(struct regulator_dev *rdev, int pwr, bool bound)
{
	int rnum = pwr, ret;

	if (!bound) {
		switch (rnum) {
		case UMP9650_LDO_AVDD12_2_SLP:
			ret = regmap_update_bits(rdev->regmap, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL37,
						 BIT_UMP9650_PMU_CTRL_SLP_DCDC_GEN1_LP_EN, 0);
			break;
		case UMP9650_LDO_AVDD18_SLP:
			ret = regmap_update_bits(rdev->regmap, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL35,
						 BIT_UMP9650_PMU_CTRL_SLP_DCDC_GEN0_LP_EN, 0);
			break;
		default:
			ret = -1;
			break;
		}
	} else {
		switch (rnum) {
		case UMP9650_LDO_AVDD12_2_SLP:
			ret = regmap_update_bits(rdev->regmap, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL37,
						 BIT_UMP9650_PMU_CTRL_SLP_DCDC_GEN1_LP_EN,
						 BIT_UMP9650_PMU_CTRL_SLP_DCDC_GEN1_LP_EN);
			break;
		case UMP9650_LDO_AVDD18_SLP:
			ret = regmap_update_bits(rdev->regmap, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL35,
						 BIT_UMP9650_PMU_CTRL_SLP_DCDC_GEN0_LP_EN,
						 BIT_UMP9650_PMU_CTRL_SLP_DCDC_GEN0_LP_EN);
			break;
		default:
			ret = -1;
			break;
		}
	}

	return ret;
}

static int sprd_regulator_enable_regmap(struct regulator_dev *rdev)
{
	int rid = rdev_get_id(rdev), ret;

	switch (rid) {
	case UMP9650_LDO_AVDD12_2_SLP:
		ret = sprd_resolve_slp_state_relationship(rdev, UMP9650_LDO_AVDD12_2_SLP, 0);
		break;
	case UMP9650_LDO_AVDD18_SLP:
		ret = sprd_resolve_slp_state_relationship(rdev, UMP9650_LDO_AVDD18_SLP, 0);
		break;
	case UMP9650_LDO_VDDSIM0:
		ret = sprd_clear_sim_int_status(rdev, MASK_ANA_UMP9650_VDDSIM0_SIM_INT0_EN);
		break;
	case UMP9650_LDO_VDDSIM1:
		ret = sprd_clear_sim_int_status(rdev, MASK_ANA_UMP9650_VDDSIM1_SIM_INT0_EN);
		break;
	case UMP9650_LDO_VDDSDCORE:
		ret = sprd_clear_sim_int_status(rdev, MASK_ANA_UMP9650_VDDSDCORE_SIM_INT0_EN);
		break;
	case UMP9650_LDO_VDDSDIO:
		ret = sprd_clear_sim_int_status(rdev, MASK_ANA_UMP9650_VDDSDIO_SIM_INT0_EN);
		break;
	default:
		ret = 0;
		break;
	}

	if (ret)
		return ret;

	return regulator_enable_regmap(rdev);
}

static int sprd_regulator_disable_regmap(struct regulator_dev *rdev)
{
	int rid = rdev_get_id(rdev), ret;

	ret = regulator_disable_regmap(rdev);

	switch (rid) {
	case UMP9650_LDO_AVDD12_2_SLP:
		ret |= sprd_resolve_slp_state_relationship(rdev, UMP9650_LDO_AVDD12_2_SLP, 1);
		break;
	case UMP9650_LDO_AVDD18_SLP:
		ret |= sprd_resolve_slp_state_relationship(rdev, UMP9650_LDO_AVDD18_SLP, 1);
		break;
	default:
		break;
	}

	return ret;
}

static int sprd_regulator_set_socp_regmap(struct regulator_dev *rdev, int lim_uA,
					  int severity, bool scop_en)
{
	int ret, val, socp_rid = rdev_get_id(rdev);
	struct sprd_ump9650_eic_dbnc *sprd_set_socp_dev = rdev_get_drvdata(rdev);

	if (severity != REGULATOR_SEVERITY_PROT) {
		dev_err(&rdev->dev, "OC Protection [%s]:%d Passed err severity!\n",
			sprd_ump9650_eic_dbnc_desc[socp_rid].power, socp_rid);
		return -EINVAL;
	}

	val = lim_uA;

	if (!scop_en) {
		/*
		 * there is no option to disable socp,
		 * notification_limit is severity - PROT
		 */
		dev_info(&rdev->dev, "OC Protection [%s]:%d Passed Zero.\n",
			 sprd_ump9650_eic_dbnc_desc[socp_rid].power, socp_rid);

		return 0;
	}

	if (val == REGU_50_NOTIF_LIMIT_ENABLE) {
		ret = sprd_ump9650_power_ocp_mode_ctrl(sprd_set_socp_dev,
				sprd_ump9650_eic_dbnc_desc[socp_rid].power, !scop_en);
		if (ret)
			return ret;
	} else if (val > 0) {
		ret = sprd_ump9650_power_ocp_mode_ctrl(sprd_set_socp_dev,
				sprd_ump9650_eic_dbnc_desc[socp_rid].power, scop_en);
		if (ret)
			return ret;
	} else {
		dev_err(&rdev->dev, "OC Protection [%s]:%d Passed err limit!\n",
			sprd_ump9650_eic_dbnc_desc[socp_rid].power, socp_rid);
		return -EINVAL;
	}

	return 0;
}

static const struct regulator_ops ump9650_regu_linear_ops = {
	.enable = sprd_regulator_enable_regmap,
	.disable = sprd_regulator_disable_regmap,
	.is_enabled = regulator_is_enabled_regmap,
	.list_voltage = regulator_list_voltage_linear,
	.get_voltage_sel = regulator_get_voltage_sel_regmap,
	.set_voltage_sel = regulator_set_voltage_sel_regmap,
	.set_over_current_protection = sprd_regulator_set_socp_regmap,
};

#define UMP9650_REGU_LINEAR(_id, en_reg, en_mask, vreg, vmask,	\
			  vstep, vmin, vmax, min_sel, delay_off_on) {		\
	.name			= #_id,				\
	.of_match		= of_match_ptr(#_id),		\
	.ops			= &ump9650_regu_linear_ops,	\
	.type			= REGULATOR_VOLTAGE,		\
	.id			= UMP9650_##_id,		\
	.owner			= THIS_MODULE,			\
	.min_uV			= vmin,				\
	.n_voltages		= ((vmax) - (vmin)) / (vstep) + 1,	\
	.uV_step		= vstep,			\
	.enable_is_inverted	= true,				\
	.enable_val		= 0,				\
	.enable_reg		= en_reg,			\
	.enable_mask		= en_mask,			\
	.vsel_reg		= vreg,				\
	.vsel_mask		= vmask,			\
	.linear_min_sel		= min_sel,			\
	.off_on_delay		= delay_off_on,			\
}

static struct regulator_desc regulators[] = {
	UMP9650_REGU_LINEAR(LDO_VDDVMEMUFSX, REG_ANA_UMP9650_LDO_VBAT_TOP_REG2,
			MASK_ANA_UMP9650_LDO_VMEMUFSX_PD, REG_ANA_UMP9650_LDO_VBAT_TOP_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDUSB33, REG_ANA_UMP9650_LDO_VDDUSB33_REG0,
			MASK_ANA_UMP9650_LDO_VDDUSB33_PD, REG_ANA_UMP9650_LDO_VDDUSB33_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDSDCORE, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_VDDSDCORE_PD, REG_ANA_UMP9650_LDO_VDDSDCORE_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 3500),
	UMP9650_REGU_LINEAR(LDO_VDDSDIO, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_VDDSDIO_PD, REG_ANA_UMP9650_LDO_VDDSDIO_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 3500),
	UMP9650_REGU_LINEAR(LDO_VDDSIM0, REG_ANA_UMP9650_LDO_VDDSIM0_REG0,
			MASK_ANA_UMP9650_LDO_VDDSIM0_PD, REG_ANA_UMP9650_LDO_VDDSIM0_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDSIM1, REG_ANA_UMP9650_LDO_VDDSIM1_REG0,
			MASK_ANA_UMP9650_LDO_VDDSIM1_PD, REG_ANA_UMP9650_LDO_VDDSIM1_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDRF1V8, REG_ANA_UMP9650_LDO_VDDRF1V8_REG0,
			MASK_ANA_UMP9650_LDO_VDDRF18_PD, REG_ANA_UMP9650_LDO_VDDRF1V8_REG1,
			GENMASK(5, 0), 10000, 1200000, 1830000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVRFFE12, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG8,
			MASK_ANA_UMP9650_LDO_VRFFE12_PD, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG8,
			GENMASK(5, 0), 10000, 900000, 1530000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVRFFE18, REG_ANA_UMP9650_LDO_DCDC_GEN0_REG2,
			MASK_ANA_UMP9650_LDO_VRFFE18_PD, REG_ANA_UMP9650_LDO_DCDC_GEN0_REG2,
			GENMASK(6, 1), 10000, 1200000, 1830000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVRFFE28, REG_ANA_UMP9650_LDO_VBAT_TOP_REG7,
			MASK_ANA_UMP9650_LDO_VRFFE28_PD, REG_ANA_UMP9650_LDO_VBAT_TOP_REG7,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDWCN, REG_ANA_UMP9650_LDO_DCDC_WCN_REG1,
			MASK_ANA_UMP9650_LDO_VDDWCN_PD, REG_ANA_UMP9650_LDO_DCDC_WCN_REG1,
			GENMASK(5, 0), 10000, 900000, 1845000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDRF1V1, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG3,
			MASK_ANA_UMP9650_LDO_VDDRF1V1_PD, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG3,
			GENMASK(5, 0), 10000, 900000, 1530000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_AVDD12_1, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_AVDD12_1_PD, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG5,
			GENMASK(5, 0), 10000, 900000, 1530000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_AVDD12_2, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG6,
			MASK_ANA_UMP9650_LDO_AVDD12_2_PD, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG6,
			GENMASK(5, 0), 10000, 900000, 1530000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDMEMQ, REG_ANA_UMP9650_POWER_LDO_PD_SW,
			MASK_ANA_UMP9650_LDO_VDDMEMQ_PD, REG_ANA_UMP9650_LDO_VDDMEMQ_DVFS1,
			GENMASK(8, 0), 3125, 0, 1596875, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVUFSQ12, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_VUFSQ1V2_PD, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG1,
			GENMASK(5, 0), 10000, 900000, 1530000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_AVDD18, REG_ANA_UMP9650_LDO_DCDC_GEN0_REG1,
			MASK_ANA_UMP9650_LDO_AVDD18_PD, REG_ANA_UMP9650_LDO_DCDC_GEN0_REG1,
			GENMASK(6, 1), 10000, 1200000, 1830000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDRF0V9, REG_ANA_UMP9650_LDO_VDDRF0V9_REG0,
			MASK_ANA_UMP9650_LDO_VDDRF0V9_PD, REG_ANA_UMP9650_LDO_VDDRF0V9_REG1,
			GENMASK(4, 0), 15000, 705000, 1170000, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_WPA, REG_ANA_UMP9650_DCDC_WPA_REG2,
			MASK_ANA_UMP9650_DCDC_WPA_PD, REG_ANA_UMP9650_DCDC_WPA_VOL,
			GENMASK(7, 0), 14100, 0, 3595500, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDD28_0, REG_ANA_UMP9650_POWER_PD_SW,
			MASK_ANA_UMP9650_LDO_VDD28_0_PD, REG_ANA_UMP9650_LDO_VDD28_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_MEML, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_VDDMEML_PD, REG_ANA_UMP9650_LDO_DCDC_GEN3_REG2,
			GENMASK(9, 5), 15000, 705000, 1170000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDWIFIPA1, REG_ANA_UMP9650_LDO_VBAT_TOP_REG11,
			MASK_ANA_UMP9650_LDO_VDDWIFIPA1_PD, REG_ANA_UMP9650_LDO_VBAT_TOP_REG11,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDWIFIPA2, REG_ANA_UMP9650_LDO_VBAT_TOP_REG13,
			MASK_ANA_UMP9650_LDO_VDDWIFIPA2_PD, REG_ANA_UMP9650_LDO_VBAT_TOP_REG13,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVWCNXO, REG_ANA_UMP9650_LDO_VBAT_TOP_REG3,
			MASK_ANA_UMP9650_LDO_VWCNXO_PD, REG_ANA_UMP9650_LDO_VBAT_TOP_REG3,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_MEM, REG_ANA_UMP9650_BUCK_TOP_REG5,
			MASK_ANA_UMP9650_DCDC_MEM_PD, REG_ANA_UMP9650_BUCK_TOP_REG3,
			GENMASK(8, 0), 3125, 0, 1596875, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDPUB, REG_ANA_UMP9650_POWER_LDO_PD_SW,
			MASK_ANA_UMP9650_LDO_VDDPUB_PD, REG_ANA_UMP9650_LDO_VDDPUB_DVFS1,
			GENMASK(8, 0), 3125, 0, 1596875, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVPST12, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_VPST12_PD, REG_ANA_UMP9650_LDO_DCDC_GEN1_REG11,
			GENMASK(5, 0), 10000, 900000, 1530000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_AVDD0V75, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_AVDD0V75_PD, REG_ANA_UMP9650_LDO_DCDC_GEN3_REG4,
			GENMASK(14, 9), 15000, 510000, 1455000, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_GEN0, REG_ANA_UMP9650_POWER_PD_SW,
			MASK_ANA_UMP9650_DCDC_GEN0_PD, REG_ANA_UMP9650_DCDC_GEN0_VOL,
			GENMASK(7, 0), 9375, 0, 2390625, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_CORE, REG_ANA_UMP9650_POWER_PD_SW,
			MASK_ANA_UMP9650_DCDC_CORE_PD, REG_ANA_UMP9650_DCDC_CORE_DVFS1,
			GENMASK(8, 0), 3125, 0, 1596875, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_GEN1, REG_ANA_UMP9650_POWER_PD_SW,
			MASK_ANA_UMP9650_DCDC_GEN1_PD, REG_ANA_UMP9650_DCDC_GEN1_VOL,
			GENMASK(7, 0), 6250, 0, 1593750, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_GEN2, REG_ANA_UMP9650_POWER_PD_SW,
			MASK_ANA_UMP9650_DCDC_GEN2_PD, REG_ANA_UMP9650_DCDC_GEN2_VOL,
			GENMASK(7, 0), 9375, 0, 2390625, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_GEN3, REG_ANA_UMP9650_BUCK_TOP_REG8,
			MASK_ANA_UMP9650_DCDC_GEN3_PD, REG_ANA_UMP9650_BUCK_TOP_REG6,
			GENMASK(8, 0), 3125, 0, 1596875, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_GEN4, REG_ANA_UMP9650_BUCK_TOP_REG11,
			MASK_ANA_UMP9650_DCDC_GEN4_PD, REG_ANA_UMP9650_BUCK_TOP_REG9,
			GENMASK(7, 0), 6250, 0, 1593750, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVPST06, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_VPST06_PD, REG_ANA_UMP9650_LDO_DCDC_GEN3_REG5,
			GENMASK(6, 1), 10000, 560000, 1505000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVPST15, REG_ANA_UMP9650_POWER_LDO_PD_REG1,
			MASK_ANA_UMP9650_LDO_VPST15_PD, REG_ANA_UMP9650_LDO_VBAT_TOP_REG9,
			GENMASK(5, 0), 10000, 1200000, 1830000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDD18_DCXO, REG_ANA_UMP9650_POWER_PD_SW,
			MASK_ANA_UMP9650_LDO_VDD18_DCXO_PD, REG_ANA_UMP9650_LDO_VDD18_DCXO_REG1,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_AVDD12_2_SLP, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL16,
			BIT_UMP9650_PMU_CTRL_SLP_LDO_AVDD12_2_PD_EN,
			REG_ANA_UMP9650_LDO_DCDC_GEN1_REG6,
			GENMASK(5, 0), 10000, 900000, 1530000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_AVDD18_SLP, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL19,
			BIT_UMP9650_PMU_CTRL_SLP_LDO_AVDD18_PD_EN,
			REG_ANA_UMP9650_LDO_DCDC_GEN0_REG1,
			GENMASK(6, 1), 10000, 1200000, 1830000, 0, 0),
	UMP9650_REGU_LINEAR(DCDC_CORE_SLP, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL36,
			BIT_UMP9650_PMU_CTRL_SLP_DCDC_CORE_PD_EN, REG_ANA_UMP9650_DCDC_CORE_DVFS1,
			GENMASK(8, 0), 3125, 0, 1596875, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDWCN_SLP, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL13,
			BIT_UMP9650_PMU_CTRL_SLP_LDO_VDDWCN_PD_EN,
			REG_ANA_UMP9650_LDO_DCDC_WCN_REG1,
			GENMASK(5, 0), 10000, 900000, 1845000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDVWCNXO_SLP, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL29,
			BIT_UMP9650_PMU_CTRL_SLP_LDO_VWCNXO_PD_EN,
			REG_ANA_UMP9650_LDO_VBAT_TOP_REG3,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
	UMP9650_REGU_LINEAR(LDO_VDDWIFIPA1_SLP, REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL26,
			BIT_UMP9650_PMU_CTRL_SLP_LDO_VDDWIFIPA1_PD_EN,
			REG_ANA_UMP9650_LDO_VBAT_TOP_REG11,
			GENMASK(7, 0), 10000, 1200000, 3750000, 0, 0),
};

static int debugfs_enable_get(void *data, u64 *val)
{
	struct regulator_dev *rdev = data;

	if (rdev && rdev->desc->ops->is_enabled)
		*val = rdev->desc->ops->is_enabled(rdev);
	else
		*val = ~0ULL;

	return 0;
}

static int debugfs_enable_set(void *data, u64 val)
{
	struct regulator_dev *rdev = data;

	if (debugfs_locken != UMP9650_WR_UNLOCK_VALUE)
		return -EINVAL;

	if (rdev && rdev->desc->ops->enable && rdev->desc->ops->disable) {
		if (val)
			rdev->desc->ops->enable(rdev);
		else
			rdev->desc->ops->disable(rdev);
	}

	return 0;
}

static int debugfs_voltage_get(void *data, u64 *val)
{
	int sel, ret;
	struct regulator_dev *rdev = data;

	sel = rdev->desc->ops->get_voltage_sel(rdev);
	if (sel < 0)
		return sel;

	ret = rdev->desc->ops->list_voltage(rdev, sel);
	*val = ret / 1000;

	return 0;
}

static int debugfs_voltage_set(void *data, u64 val)
{
	int selector;
	struct regulator_dev *rdev = data;

	if (debugfs_locken != UMP9650_WR_UNLOCK_VALUE)
		return -EINVAL;

	val = val * 1000;

	selector = regulator_map_voltage_linear(rdev,
						val - rdev->desc->uV_step / 2,
						val + rdev->desc->uV_step / 2);

	return rdev->desc->ops->set_voltage_sel(rdev, selector);
}

DEFINE_SIMPLE_ATTRIBUTE(fops_enable, debugfs_enable_get, debugfs_enable_set, "%llu\n");
DEFINE_SIMPLE_ATTRIBUTE(fops_ldo, debugfs_voltage_get, debugfs_voltage_set, "%llu\n");

static void ump9650_regulator_debugfs_init(struct regulator_dev *rdev)
{
	struct dentry *debugfs;

	debugfs = debugfs_create_dir(rdev->desc->name, debugfs_root);
	if (IS_ERR_OR_NULL(debugfs)) {
		dev_warn(&rdev->dev, "Failed to create %s directory\n", rdev->desc->name);
		return;
	}

	debugfs_create_file("enable", 0644, debugfs, rdev, &fops_enable);
	debugfs_create_file("voltage", 0644, debugfs, rdev, &fops_ldo);

	return;
}

static int ump9650_regulator_unlock(struct regmap *regmap)
{
	return regmap_write(regmap, REG_ANA_UMP9650_PWR_WR_PROT_VALUE, UMP9650_WR_UNLOCK_VALUE);
}

static void sprd_ump9650_seq_buf_init(void)
{
 sprd_ump9650_buf = kzalloc(SPRD_SOCP_PRINT_BUF_LEN, GFP_KERNEL);
 if (!sprd_ump9650_buf)
  return;
 sprd_ump9650_seq_buf = kzalloc(sizeof(*sprd_ump9650_seq_buf), GFP_KERNEL);
 if (!sprd_ump9650_seq_buf) {
  kfree(sprd_ump9650_buf);
  sprd_ump9650_buf = NULL;
  return;
 }
 seq_buf_init(sprd_ump9650_seq_buf, sprd_ump9650_buf, SPRD_SOCP_PRINT_BUF_LEN);
 /* Keep fault diagnostics using standard debugfs, without Android minidump. */
 ump9650_fault_blob.data = sprd_ump9650_buf;
 ump9650_fault_blob.size = SPRD_SOCP_PRINT_BUF_LEN;
 if (!IS_ERR_OR_NULL(debugfs_root))
  debugfs_create_blob("fault_log", 0444, debugfs_root, &ump9650_fault_blob);
}

static int ump9650_regulator_set_dsc(struct regmap *regmap)
{
	int ret;
	u32 ver = 0;

	ret = regmap_read(regmap, REG_ANA_UMP9650_CHIP_ID_LOW, &ver);
	if (ret)
		return ret;

	if (ver == SPRD_UMP9650_CA_VAL) {
		regulators[UMP9650_LDO_VDDSDCORE].enable_reg = REG_ANA_UMP9650_LDO_VDDSDCORE_REG0;
		regulators[UMP9650_LDO_VDDSDCORE].enable_mask =
							MASK_ANA_UMP9650_LDO_VDDSDCORE_CA_PD;
		regulators[UMP9650_LDO_VDDSDIO].enable_reg = REG_ANA_UMP9650_LDO_VDDSDIO_REG0;
		regulators[UMP9650_LDO_VDDSDIO].enable_mask = MASK_ANA_UMP9650_LDO_VDDSDIO_CA_PD;
		regulators[UMP9650_LDO_VDDPUB].enable_reg = REG_ANA_UMP9650_POWER_LDO_PD_REG1;
		regulators[UMP9650_LDO_VDDPUB].enable_mask = MASK_ANA_UMP9650_LDO_VDDPUB_CA_PD;
		regulators[UMP9650_DCDC_GEN4].enable_reg = REG_ANA_UMP9650_POWER_LDO_PD_REG1;
		regulators[UMP9650_DCDC_GEN4].enable_mask = MASK_ANA_UMP9650_DCDC_GEN4_CA_PD;
		regulators[UMP9650_DCDC_GEN4].uV_step = 3125;
		regulators[UMP9650_DCDC_GEN4].vsel_mask = GENMASK(8, 0);
	}

	return 0;
}

static int ump9650_regulator_probe(struct platform_device *pdev)
{
	int i, ret, err, count;
	struct regulator_config config = { };
	struct sprd_ump9650_eic_dbnc *sprd_eic_dbnc_dev;
	u32 socp_on = 0;

	sprd_eic_dbnc_dev = devm_kzalloc(&pdev->dev,
					 sizeof(struct sprd_ump9650_eic_dbnc), GFP_KERNEL);
	if (!sprd_eic_dbnc_dev)
		return -ENOMEM;

	sprd_eic_dbnc_dev->dev = &pdev->dev;
	sprd_eic_dbnc_dev->reg_map = dev_get_regmap(pdev->dev.parent, NULL);
	if (IS_ERR_OR_NULL(sprd_eic_dbnc_dev->reg_map)) {
		err = sprd_eic_dbnc_dev->reg_map ? PTR_ERR(sprd_eic_dbnc_dev->reg_map) : -EPROBE_DEFER;
		dev_err(&pdev->dev, "failed to get regmap:%d!\n", err);
		return err;
	}

	ret = of_property_read_u32_index(pdev->dev.of_node, "eic_db_reg", 0,
					 &sprd_eic_dbnc_dev->eic_dbnc_base);
	if (ret) {
		dev_err(sprd_eic_dbnc_dev->dev, "failed to get eic_dbnc_base addr!\n");
		return ret;
	}

	ret = of_property_read_u32_index(pdev->dev.of_node, "eic_db_reg", 1,
					 &sprd_eic_dbnc_dev->glb_base);
	if (ret) {
		dev_err(sprd_eic_dbnc_dev->dev, "failed to get glb_base addr!\n");
		return ret;
	}

	ret = ump9650_regulator_set_dsc(sprd_eic_dbnc_dev->reg_map);
	if (ret) {
		dev_err(sprd_eic_dbnc_dev->dev, "failed to set regulator ca-desc\n");
		return ret;
	}

	ret = ump9650_regulator_unlock(sprd_eic_dbnc_dev->reg_map);
	if (ret) {
		dev_err(sprd_eic_dbnc_dev->dev, "failed to release regulator lock\n");
		return ret;
	}

	debugfs_root = debugfs_create_dir(pdev->dev.driver->name, NULL);
	if (IS_ERR_OR_NULL(debugfs_root))
		dev_warn(sprd_eic_dbnc_dev->dev,
			 "Failed to recreate debugfs_root directory\n");
	else
		debugfs_create_x16("locken", 0644, debugfs_root, &debugfs_locken);

	config.dev = sprd_eic_dbnc_dev->dev;
	config.regmap = sprd_eic_dbnc_dev->reg_map;
	config.driver_data = (void *)sprd_eic_dbnc_dev;
	count = ARRAY_SIZE(regulators);
	for (i = 0; i < count; i++) {
		sprd_eic_dbnc_dev->rdev[i] = devm_regulator_register(sprd_eic_dbnc_dev->dev,
								     &regulators[i], &config);
		if (IS_ERR(sprd_eic_dbnc_dev->rdev[i])) {
			dev_err(sprd_eic_dbnc_dev->dev, "failed to register regulator %s\n",
				regulators[i].name);
			continue;
		}

		ump9650_regulator_debugfs_init(sprd_eic_dbnc_dev->rdev[i]);

		ret = of_property_read_u32(sprd_eic_dbnc_dev->rdev[i]->dev.of_node,
					   "regulator-socp-on", &socp_on);
		if (ret)
			dev_info(sprd_eic_dbnc_dev->dev,
				 "[%s] not support socp!\n", regulators[i].name);
		else
			sprd_ump9650_eic_dbnc_desc[i].ie = socp_on;

		ret = of_property_read_bool(sprd_eic_dbnc_dev->rdev[i]->dev.of_node,
					    "regulator-short-current-protection");
		if (!ret)
			dev_info(sprd_eic_dbnc_dev->dev,
				 "[%s] not support scp!\n", regulators[i].name);
		else
			sprd_ump9650_eic_dbnc_desc[i].scp = 1;
	}

	ret = sprd_ump9650_eic_dbnc_module_enable(sprd_eic_dbnc_dev, true);
	if (ret)
		return ret;

	ret = sprd_ump9650_eic_dbnc_mode_ctrl_manul(sprd_eic_dbnc_dev);
	if (ret)
		return ret;

	sprd_eic_dbnc_dev->irq = platform_get_irq(pdev, 0);
	if (sprd_eic_dbnc_dev->irq < 0) {
		dev_err(sprd_eic_dbnc_dev->dev, "Sprd Eic no irq resource specified\n");
		return sprd_eic_dbnc_dev->irq;
	}

	ret = devm_request_threaded_irq(&pdev->dev, sprd_eic_dbnc_dev->irq, NULL,
					sprd_ump9650_eic_dbnc_handler,
					IRQF_ONESHOT, /* Bootstrap system sleep is disabled or guarded before device PM. */
					pdev->name, sprd_eic_dbnc_dev);
	if (ret < 0) {
		dev_err(sprd_eic_dbnc_dev->dev, "failed to request ump9650 eic_dbnc irq\n");
		return ret;
	}

	sprd_ump9650_seq_buf_init();

	return 0;
}

static void ump9650_regulator_remove(struct platform_device *pdev)
{
	debugfs_remove_recursive(debugfs_root);

	kfree(sprd_ump9650_seq_buf);
	sprd_ump9650_seq_buf = NULL;
	kfree(sprd_ump9650_buf);
	sprd_ump9650_buf = NULL;

}

static const struct of_device_id ump9650_regulator_match[] = {
	{ .compatible = "sprd,ump9650-regulator" },
	{},
};

MODULE_DEVICE_TABLE(of, ump9650_regulator_match);

static struct platform_driver ump9650_regulator_driver = {
	.driver = {
		   .name = "ump9650-regulator",
		   .of_match_table = ump9650_regulator_match,
		   },
	.probe = ump9650_regulator_probe,
	.remove = ump9650_regulator_remove,
};

module_platform_driver(ump9650_regulator_driver);

MODULE_AUTHOR("yixin.lin <yixin.lin@unisoc.com>");
MODULE_DESCRIPTION("Unisoc ump9650 regulator driver");
MODULE_LICENSE("GPL");
