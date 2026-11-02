// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2018 Spreadtrum Communications Inc.
 */


#include <linux/debugfs.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mfd/ump965x/sprd,ump9651-pmu-ctrl.h>
#include <linux/mfd/ump965x/sprd,ump965x-mask.h>
#include <linux/mfd/ump965x/sprd,ump965x-regs.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/of_regulator.h>
#include <linux/regulator/machine.h>
#include <linux/seq_buf.h>
#include <linux/sched.h>
#include "ump9651-regulator.h"
#include "internal.h"

#define UMP9651_WR_UNLOCK_VALUE			0x6e7f

#define SPRD_SOCP_PRINT_BUF_LEN			8192
#define UMP9651_printf(m, x...)				\
	do {						\
		if (!m)					\
			pr_debug(x);			\
		else if (seq_buf_printf(m, x)) {	\
			seq_buf_clear(m);		\
			seq_buf_printf(m, x);		\
		}					\
	} while (0)

enum ump9651_regulator_id {
	UMP9651_LDO_VDD28_1,
	UMP9651_LDO_VDDSRAM1,
	UMP9651_LDO_VDDSRAM2,
	UMP9651_DCDC_MODEM1,
	UMP9651_DCDC_MODEM2,
	UMP9651_DCDC_SRAM0,
	UMP9651_LDO_VDDVUSBPD,
	UMP9651_LDO_VDDCAMMOT,
	UMP9651_LDO_VDDVIB,
	UMP9651_LDO_VDDCAMIO,
	UMP9651_LDO_VDDCAMD,
	UMP9651_LDO_VDDFP,
	UMP9651_DCDC_CPU1,
	UMP9651_DCDC_CPU2,
	UMP9651_DCDC_CPU0,
	UMP9651_DCDC_MODEM2_SLP,
	UMP9651_PWR_MAX,
};

static struct dentry *debugfs_root;

static u16 debugfs_locken = 0xFFFF;

/*
 * struct sprd_ump9651_eic_dbnc_desc_priv - Drv descriptor
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
 *
 */

struct sprd_ump9651_eic_dbnc_desc_priv {
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

struct sprd_ump9651_eic_dbnc {
	struct device *dev;
	struct regmap *reg_map;
	int irq;
	u32 eic_dbnc_base;
	u32 glb_base;
	struct regulator_dev *rdev[UMP9651_PWR_MAX];
};

struct sprd_ump9651_eic_dbnc_info {
	u8 bit_bank;
	u8 bit_num;
};

static char *sprd_ump9651_buf;
static struct seq_buf *sprd_ump9651_seq_buf;
static struct debugfs_blob_wrapper ump9651_fault_blob;

static struct sprd_ump9651_eic_dbnc_desc_priv sprd_ump9651_eic_dbnc_desc[] = {
	{"vdd28_1_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC3_VDD28_1_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL3, 0,
		0, MASK_ANA_UMP9651_LDO_VDD28_1_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VDD28_1_OCP_FLAG_CLR,
		0, 0},
	{"vddsram1_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC1_VDDSRAM1_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL3, MASK_ANA_UMP9651_LDO_VDDSRAM1_OCP_PD_CLR,
		MASK_ANA_UMP9651_LDO_VDDSRAM1_OCP_MODE, MASK_ANA_UMP9651_LDO_VDDSRAM1_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VDDSRAM1_OCP_FLAG, 0, 0},
	{"vddsram2_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC2_VDDSRAM2_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL3, MASK_ANA_UMP9651_LDO_VDDSRAM2_OCP_PD_CLR,
		MASK_ANA_UMP9651_LDO_VDDSRAM2_OCP_MODE, MASK_ANA_UMP9651_LDO_VDDSRAM2_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VDDSRAM2_OCP_FLAG, 0, 0},
	{"vddmodem1_socp", 0, 0, 0x12, UMP9651_EIC_DBNC15_DCDC_MODEM1_P_OCP,
		UMP9651_EIC_DBNC24_DCDC_MODEM1_SCP,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL1, MASK_ANA_UMP9651_DCDC_MODEM1_POCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_MODEM1_POCP_MODE, MASK_ANA_UMP9651_DCDC_MODEM1_POCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_SCP_CTRL0, MASK_ANA_UMP9651_DCDC_MODEM1_SCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_MODEM1_SCP_MODE, MASK_ANA_UMP9651_DCDC_MODEM1_SCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG0, MASK_ANA_UMP9651_DCDC_MODEM1_POCP_FLAG,
		REG_ANA_UMP9651_DCDC_SCP_FLAG0, MASK_ANA_UMP9651_DCDC_MODEM1_SCP_FLAG},
	{"vddmodem2_socp", 0, 0, 0x12, UMP9651_EIC_DBNC17_DCDC_MODEM2_P_OCP,
		UMP9651_EIC_DBNC25_DCDC_MODEM2_SCP,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL1, MASK_ANA_UMP9651_DCDC_MODEM2_POCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_MODEM2_POCP_MODE, MASK_ANA_UMP9651_DCDC_MODEM2_POCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_SCP_CTRL0, MASK_ANA_UMP9651_DCDC_MODEM2_SCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_MODEM2_SCP_MODE, MASK_ANA_UMP9651_DCDC_MODEM2_SCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG0, MASK_ANA_UMP9651_DCDC_MODEM2_POCP_FLAG,
		REG_ANA_UMP9651_DCDC_SCP_FLAG0, MASK_ANA_UMP9651_DCDC_MODEM2_SCP_FLAG},
	{"vddsram0_socp", 0, 0, 0x12, UMP9651_EIC_DBNC21_DCDC_SRAM0_OCP,
		UMP9651_EIC_DBNC28_DCDC_SRAM0_SCP,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL2, MASK_ANA_UMP9651_DCDC_SRAM0_OCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_SRAM0_OCP_MODE, MASK_ANA_UMP9651_DCDC_SRAM0_OCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_SCP_CTRL1, MASK_ANA_UMP9651_DCDC_SRAM0_SCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_SRAM0_SCP_MODE, MASK_ANA_UMP9651_DCDC_SRAM0_SCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG0, MASK_ANA_UMP9651_DCDC_SRAM0_OCP_FLAG,
		REG_ANA_UMP9651_DCDC_SCP_FLAG0, MASK_ANA_UMP9651_DCDC_SRAM0_SCP_FLAG},
	{"vddvusbpd_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC4_VUSBPD_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL3, MASK_ANA_UMP9651_LDO_VUSBPD_OCP_PD_CLR,
		MASK_ANA_UMP9651_LDO_VUSBPD_OCP_MODE, MASK_ANA_UMP9651_LDO_VUSBPD_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VUSBPD_OCP_FLAG, 0, 0},
	{"vddcammot_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC8_VDDCAMMOT_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL4, MASK_ANA_UMP9651_LDO_VDDCAMMOT_OCP_PD_CLR,
		MASK_ANA_UMP9651_LDO_VDDCAMMOT_OCP_MODE,
		MASK_ANA_UMP9651_LDO_VDDCAMMOT_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VDDCAMMOT_OCP_FLAG, 0, 0},
	{"vddvib_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC7_VDDVIB_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL4, 0,
		0, MASK_ANA_UMP9651_LDO_VDDVIB_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VDDVIB_OCP_FLAG, 0, 0},
	{"vddcamio_ocp", 0, 0, 0x10, 0, 0,
		0, 0, 0, 0,
		0, 0, 0, 0,
		0, 0, 0, 0},
	{"vddcamd_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC5_VDDCAMD_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL4, MASK_ANA_UMP9651_LDO_VDDCAMD_OCP_PD_CLR,
		MASK_ANA_UMP9651_LDO_VDDCAMD_OCP_MODE, MASK_ANA_UMP9651_LDO_VDDCAMD_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VDDCAMD_OCP_FLAG, 0, 0},
	{"vddfp_ocp", 0, 0, 0x10, UMP9651_EIC_DBNC6_VDDFP_OCP, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL4, 0,
		0, MASK_ANA_UMP9651_LDO_VDDFP_OCP_FLAG_CLR,
		0, 0, 0, 0,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG1, MASK_ANA_UMP9651_LDO_VDDFP_OCP_FLAG, 0, 0},
	{"vddcpu1_socp", 0, 0, 0x12, UMP9651_EIC_DBNC11_DCDC_CPU1P0_P_OCP,
		UMP9651_EIC_DBNC22_DCDC_CPU1P0_SCP,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL0, MASK_ANA_UMP9651_DCDC_CPU1PH0_POCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_CPU1PH0_POCP_MODE,
		MASK_ANA_UMP9651_DCDC_CPU1PH0_POCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_SCP_CTRL0, MASK_ANA_UMP9651_DCDC_CPU1PH0_SCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_CPU1PH0_SCP_MODE, MASK_ANA_UMP9651_DCDC_CPU1PH0_SCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG0, MASK_ANA_UMP9651_DCDC_CPU1PH0_POCP_FLAG,
		REG_ANA_UMP9651_DCDC_SCP_FLAG0, MASK_ANA_UMP9651_DCDC_CPU1PH0_SCP_FLAG},
	{"vddcpu2_socp", 0, 0, 0x12, UMP9651_EIC_DBNC20_DCDC_CPU2_OCP,
		UMP9651_EIC_DBNC27_DCDC_CPU2_SCP,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL2, MASK_ANA_UMP9651_DCDC_CPU2_OCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_CPU2_OCP_MODE, MASK_ANA_UMP9651_DCDC_CPU2_OCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_SCP_CTRL1, MASK_ANA_UMP9651_DCDC_CPU2_SCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_CPU2_SCP_MODE, MASK_ANA_UMP9651_DCDC_CPU2_SCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG0, MASK_ANA_UMP9651_DCDC_CPU2_OCP_FLAG,
		REG_ANA_UMP9651_DCDC_SCP_FLAG0, MASK_ANA_UMP9651_DCDC_CPU2_SCP_FLAG},
	{"vddcpu0_socp", 0, 0, 0x12, UMP9651_EIC_DBNC19_DCDC_CPU0_OCP,
		UMP9651_EIC_DBNC26_DCDC_CPU0_SCP,
		REG_ANA_UMP9651_DCDC_LDO_OCP_CTRL2, MASK_ANA_UMP9651_DCDC_CPU0_OCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_CPU0_OCP_MODE, MASK_ANA_UMP9651_DCDC_CPU0_OCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_SCP_CTRL1, MASK_ANA_UMP9651_DCDC_CPU0_SCP_PD_CLR,
		MASK_ANA_UMP9651_DCDC_CPU0_SCP_MODE, MASK_ANA_UMP9651_DCDC_CPU0_SCP_FLAG_CLR,
		REG_ANA_UMP9651_DCDC_LDO_OCP_FLAG0, MASK_ANA_UMP9651_DCDC_CPU0_OCP_FLAG,
		REG_ANA_UMP9651_DCDC_SCP_FLAG0, MASK_ANA_UMP9651_DCDC_CPU0_SCP_FLAG},
	{"otp_warn_out51", 0, 0, 0x10, UMP9651_EIC_DBNC0_OTP_WARN_OCP, 0,
		0, 0, 0, 0,
		0, 0, 0, 0,
		0, 0, 0, 0},
};

static inline unsigned long sprd_socp_ffs(u32 word)
{
	int num = 0;

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

static int sprd_ump9651_dbnc_id_get(int eic_dbnc_id)
{
	int i, count;

	count = ARRAY_SIZE(sprd_ump9651_eic_dbnc_desc);
	for (i = 0; i < count; i++) {
		if ((eic_dbnc_id == sprd_ump9651_eic_dbnc_desc[i].eic_dbnc_ocp) ||
			(eic_dbnc_id == sprd_ump9651_eic_dbnc_desc[i].eic_dbnc_scp))

			return i;
	}

	return -1;
}

static struct sprd_ump9651_eic_dbnc_desc_priv *sprd_ump9651_dbnc_get(const char *dbnc_id)
{
	int i, count;

	count = ARRAY_SIZE(sprd_ump9651_eic_dbnc_desc);
	for (i = 0; i < count; i++) {
		if (!strcmp(dbnc_id, sprd_ump9651_eic_dbnc_desc[i].power))
			return &sprd_ump9651_eic_dbnc_desc[i];
	}

	return NULL;
}

static int sprd_ump9651_power_ocp_mode_ctrl(struct sprd_ump9651_eic_dbnc *eic_dbnc,
					    const char dbnc_id[], bool ocp_mode)
{
	int ret;
	struct sprd_ump9651_eic_dbnc_desc_priv *desc = sprd_ump9651_dbnc_get(dbnc_id);

	if (!desc) {
		pr_err("Sprd 51 Ocp Mode Ctrl Passed err Id [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	if (!desc->ocp_mode_bit) {
		pr_err("Sprd 51 Ocp Mode Ctrl Passed err Power [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	ret = regmap_update_bits(eic_dbnc->reg_map, desc->ocp_ctrl,
				 desc->ocp_mode_bit,
				 ocp_mode ? desc->ocp_mode_bit : 0);
	if (ret) {
		pr_err("Sprd 51 Ocp Mode Ctrl failed Id [%s] [err %d]!\n", dbnc_id, ret);
		return ret;
	}

	return 0;
}

static int sprd_ump9651_power_scp_mode_ctrl(struct sprd_ump9651_eic_dbnc *eic_dbnc,
					    const char dbnc_id[], bool scp_mode)
{
	int ret, type;
	struct sprd_ump9651_eic_dbnc_desc_priv *desc = sprd_ump9651_dbnc_get(dbnc_id);

	if (!desc) {
		pr_err("Sprd 51 Scp Mode Ctrl Passed err Id [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	type = desc->type_power & (BIT(4) - 1);
	if (type == 2) {
		ret = regmap_update_bits(eic_dbnc->reg_map, desc->scp_ctrl,
					 desc->scp_mode_bit,
					 scp_mode ? desc->scp_mode_bit : 0);
		if (ret) {
			pr_err("Sprd 51 Scp Mode Ctrl failed Id [%s] [err %d]!\n", dbnc_id, ret);
			return ret;
		}
	} else {
		pr_err("Sprd 51 Scp Mode Ctrl Passed err Power [%s]!\n", dbnc_id);
		return -EINVAL;
	}

	return 0;
}

static int sprd_ump9651_get_eic_dbnc_base_info(u32 eic_dbnc_id,
					       struct sprd_ump9651_eic_dbnc_info *info)
{
	info->bit_num  = eic_dbnc_id & 0xF;

	if (eic_dbnc_id >= SPRD_UMP9651_EIC_DBNC_BANK0_START &&
			eic_dbnc_id <= SPRD_UMP9651_EIC_DBNC_BANK0_END)
		info->bit_bank = 0;
	else if (eic_dbnc_id >= SPRD_UMP9651_EIC_DBNC_BANK1_START &&
			eic_dbnc_id <= SPRD_UMP9651_EIC_DBNC_BANK1_END)
		info->bit_bank = 1;
	else {
		pr_err("Sprd 51 Eic Dbnc Base Info Passed err Id [%d]!\n", eic_dbnc_id);
		return -EINVAL;
	}

	pr_info("Sprd Eic Dbnc Base Info Get Id:%d -> Bank:%d", eic_dbnc_id, info->bit_bank);

	return 0;
}

static int sprd_ump9651_eic_dbnc_irq_mask(struct sprd_ump9651_eic_dbnc *eic_dbnc,
					    unsigned int offset, bool mask_val)
{
	unsigned int eic_dbnc_id = offset;
	struct sprd_ump9651_eic_dbnc_info eic_dbnc_info;
	unsigned long reg_addr;
	int ret;

	ret = sprd_ump9651_get_eic_dbnc_base_info(eic_dbnc_id, &eic_dbnc_info);
	if (ret) {
		pr_err("Sprd 51 Eic Dbnc Irq Mask Get Base Info err Id [%d]!\n", eic_dbnc_id);
		return -EINVAL;
	}

	if (eic_dbnc_info.bit_bank == 0)
		reg_addr = SPRD_UMP9651_EIC_DBNC_IE0;
	else
		reg_addr = SPRD_UMP9651_EIC_DBNC_IE1;

	ret = regmap_update_bits(eic_dbnc->reg_map,
				 eic_dbnc->eic_dbnc_base + reg_addr,
				 SPRD_UMP9651_BIT(eic_dbnc_info.bit_num),
				 mask_val ? 0 : SPRD_UMP9651_BIT(eic_dbnc_info.bit_num));

	if (ret) {
		pr_err("Sprd 51 Eic Dbnc Irq MaskVal-%d failed Id [%d]!\n",
			mask_val, eic_dbnc_id);
		return ret;
	}

	return 0;
}

static int sprd_ump9651_eic_dbnc_module_enable(struct sprd_ump9651_eic_dbnc *eic_dbnc, bool enable)
{
	int ret;
	u32 reg_val;

	reg_val = enable ? MASK_ANA_UMP9651_EIC_DBNC_EN : 0;

	ret = regmap_update_bits(eic_dbnc->reg_map, REG_ANA_UMP9651_MODULE_EN0,
				 MASK_ANA_UMP9651_EIC_DBNC_EN, reg_val);
	if (ret) {
		pr_err("Sprd 51 Eic Dbnc Module En failed!\n");
		return ret;
	}

	reg_val = enable ? MASK_ANA_UMP9651_RTC_EIC_DBNC_EN : 0;

	ret = regmap_update_bits(eic_dbnc->reg_map, REG_ANA_UMP9651_RTC_CLK_EN0,
				 MASK_ANA_UMP9651_RTC_EIC_DBNC_EN, reg_val);

	if (ret) {
		pr_err("Sprd 51 Eic Dbnc Rtc Clk En failed!\n");
		return ret;
	}

	return 0;
}

static int sprd_ump9651_eic_dbnc_mode_ctrl_manul(struct sprd_ump9651_eic_dbnc *eic_dbnc)
{
	int ret, i, count;

	/*
	 * Before enable this Module, check what the pwr you care is choosed the
	 * right mode (ocp/scp). Generally for the system exception protection,
	 * this driver is mainly for maintenance record.
	 */

	count = ARRAY_SIZE(sprd_ump9651_eic_dbnc_desc);
	for (i = 0; i < count; i++) {
		if (!sprd_ump9651_eic_dbnc_desc[i].scp)
			continue;

		ret = sprd_ump9651_power_scp_mode_ctrl(eic_dbnc,
				sprd_ump9651_eic_dbnc_desc[i].power, 1);
		if (ret)
			pr_err("Sprd Scp Ctrl [%s] failed!\n",
				sprd_ump9651_eic_dbnc_desc[i].power);
	}

	/*
	 * Default IE, Mask/UnMask EIC_DBNC_NUM Here.
	 * < eic_dbnc_num: 208 ~ 253 >
	 * ret = sprd_eic_dbnc_irq_mask(eic_dbnc, eic_dbnc_num);
	 */

	/* EIC_DBNC 0 - Over Tmp Warn */
	ret = sprd_ump9651_eic_dbnc_irq_mask(eic_dbnc, UMP9651_EIC_DBNC0_OTP_WARN_OCP, 0);
	if (ret)
		pr_err("Sprd 51 Irq Unmask [OTP WARN] failed!\n");

	for (i = 0; i < count - 1; i++) {
		if (!sprd_ump9651_eic_dbnc_desc[i].ie)
			continue;

		ret = sprd_ump9651_eic_dbnc_irq_mask(eic_dbnc,
				sprd_ump9651_eic_dbnc_desc[i].eic_dbnc_ocp, 0);

		if (sprd_ump9651_eic_dbnc_desc[i].eic_dbnc_scp)
			ret |= sprd_ump9651_eic_dbnc_irq_mask(eic_dbnc,
				sprd_ump9651_eic_dbnc_desc[i].eic_dbnc_scp, 0);

		if (ret)
			pr_err("Sprd 51 Irq Unmask [%s] failed!\n",
				sprd_ump9651_eic_dbnc_desc[i].power);
	}

	return ret;
}

static int sprd_ump9651_eic_dbnc_irq_ack(struct sprd_ump9651_eic_dbnc *eic_dbnc,
					 unsigned int offset)
{
	unsigned int eic_dbnc_id = offset;
	struct sprd_ump9651_eic_dbnc_info eic_dbnc_info;
	unsigned long reg_addr;
	int ret;

	ret = sprd_ump9651_get_eic_dbnc_base_info(eic_dbnc_id, &eic_dbnc_info);
	if (ret) {
		pr_err("Sprd 51 Eic Dbnc Irq Ack Get Base Info err Id [%d]!\n", eic_dbnc_id);
		return -EINVAL;
	}

	if (eic_dbnc_info.bit_bank == 0)
		reg_addr = SPRD_UMP9651_EIC_DBNC_IC0;
	else
		reg_addr = SPRD_UMP9651_EIC_DBNC_IC1;

	ret = regmap_update_bits(eic_dbnc->reg_map, eic_dbnc->eic_dbnc_base + reg_addr,
				 BIT(eic_dbnc_info.bit_num),
				 BIT(eic_dbnc_info.bit_num));

	if (ret) {
		pr_err("Sprd 51 Eic Dbnc Irq Ack failed Id [%d]!\n", eic_dbnc_id);
		return ret;
	}

	return 0;
}

static irqreturn_t sprd_ump9651_eic_dbnc_handler(int irq, void *data)
{
	struct sprd_ump9651_eic_dbnc *eic_dbnc = data;
	int ret = IRQ_NONE, n, eic_dbnc_id;
	u32 tmp = 0, value;

	ret = regmap_read(eic_dbnc->reg_map, eic_dbnc->eic_dbnc_base + SPRD_UMP9651_EIC_DBNC_MIS1,
			  &tmp);
	if (ret)
		return IRQ_RETVAL(ret);

	value = tmp << 16;

	ret = regmap_read(eic_dbnc->reg_map, eic_dbnc->eic_dbnc_base + SPRD_UMP9651_EIC_DBNC_MIS0,
			  &tmp);
	if (ret)
		return IRQ_RETVAL(ret);

	value |= tmp;

	while (value) {
		n = sprd_socp_ffs(value);
		value &= ~(1UL << n);
		eic_dbnc_id = SPRD_UMP9651_EIC_DBNC_BANK0_START + n;

		ret = sprd_ump9651_eic_dbnc_irq_mask(eic_dbnc, eic_dbnc_id, 1);
		if (ret)
			return IRQ_RETVAL(ret);

		ret = sprd_ump9651_eic_dbnc_irq_ack(eic_dbnc, eic_dbnc_id);
		if (ret)
			return IRQ_RETVAL(ret);

		ret = sprd_ump9651_dbnc_id_get(eic_dbnc_id);
		if ((ret >= 0) && (eic_dbnc->rdev[ret] != NULL))
			regulator_notifier_call_chain(eic_dbnc->rdev[ret], eic_dbnc_id, NULL);
		else
			pr_err("Sprd Ump9651 Irq Ack X NOTIFIER [%d/%d]!\n", eic_dbnc_id, ret);

		UMP9651_printf(sprd_ump9651_seq_buf,
				"Sprd Ump9651 [%d] Occurs Ocp/Scp OoOs!\n", eic_dbnc_id);

		pr_err("Sprd Ump9651 [%d] Occurs Ocp/Scp OoOs!\n", eic_dbnc_id);

		//ret = sprd_ump9651_eic_dbnc_irq_mask(eic_dbnc, eic_dbnc_id, 0);
		//if (ret)
		//	return IRQ_RETVAL(ret);
	}

	return IRQ_HANDLED;
}

static int sprd_regulator_set_socp_regmap(struct regulator_dev *rdev, int lim_uA,
					  int severity, bool scop_en)
{
	int ret, val, socp_rid = rdev_get_id(rdev);
	struct sprd_ump9651_eic_dbnc *sprd_set_socp_dev = rdev_get_drvdata(rdev);

	if (severity != REGULATOR_SEVERITY_PROT) {
		dev_err(&rdev->dev, "OC Protection [%s]:%d Passed err severity!\n",
			sprd_ump9651_eic_dbnc_desc[socp_rid].power, socp_rid);
		return -EINVAL;
	}

	val = lim_uA;

	if (!scop_en) {
		/*
		 * there is no option to disable socp,
		 * notification_limit is severity - PROT
		 */
		dev_warn(&rdev->dev, "OC Protection [%s]:%d Passed Zero.\n",
			 sprd_ump9651_eic_dbnc_desc[socp_rid].power, socp_rid);

		return 0;
	}

	if (val == REGU_51_NOTIF_LIMIT_ENABLE) {
		ret = sprd_ump9651_power_ocp_mode_ctrl(sprd_set_socp_dev,
				sprd_ump9651_eic_dbnc_desc[socp_rid].power, !scop_en);
		if (ret)
			return ret;
	} else if (val > 0) {
		ret = sprd_ump9651_power_ocp_mode_ctrl(sprd_set_socp_dev,
				sprd_ump9651_eic_dbnc_desc[socp_rid].power, scop_en);
		if (ret)
			return ret;
	} else {
		dev_err(&rdev->dev, "OC Protection [%s]:%d Passed err limit!\n",
			sprd_ump9651_eic_dbnc_desc[socp_rid].power, socp_rid);
		return -EINVAL;
	}

	return 0;
}

static const struct regulator_ops ump9651_regu_linear_ops = {
	.enable = regulator_enable_regmap,
	.disable = regulator_disable_regmap,
	.is_enabled = regulator_is_enabled_regmap,
	.list_voltage = regulator_list_voltage_linear,
	.get_voltage_sel = regulator_get_voltage_sel_regmap,
	.set_voltage_sel = regulator_set_voltage_sel_regmap,
	.set_over_current_protection = sprd_regulator_set_socp_regmap,
};

#define UMP9651_REGU_LINEAR(_id, en_reg, en_mask, vreg, vmask,	\
			  vstep, vmin, vmax, min_sel) {		\
	.name			= #_id,				\
	.of_match		= of_match_ptr(#_id),		\
	.ops			= &ump9651_regu_linear_ops,	\
	.type			= REGULATOR_VOLTAGE,		\
	.id			= UMP9651_##_id,		\
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
}

static struct regulator_desc regulators[] = {
	UMP9651_REGU_LINEAR(LDO_VDD28_1, REG_ANA_UMP9651_POWER_PD_SW2,
			MASK_ANA_UMP9651_LDO_VDD28_1_PD, REG_ANA_UMP9651_LDO_VDD28_1_REG1,
			GENMASK(12, 5), 10000, 1200000, 3750000, 0),
	UMP9651_REGU_LINEAR(LDO_VDDSRAM1, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_LDO_VDDSRAM1_PD, REG_ANA_UMP9651_LDO_VDDSRAM1_VOL,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(LDO_VDDSRAM2, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_LDO_VDDSRAM2_PD, REG_ANA_UMP9651_LDO_DCDC_TOP_REG2,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(DCDC_MODEM1, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_DCDC_MODEM1_PD, REG_ANA_UMP9651_BUCK_TOP_REG20,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(DCDC_MODEM2, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_DCDC_MODEM2_PD, REG_ANA_UMP9651_BUCK_TOP_REG28,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(DCDC_SRAM0, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_DCDC_SRAM0_PD, REG_ANA_UMP9651_BUCK_TOP_REG42,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(LDO_VDDVUSBPD, REG_ANA_UMP9651_POWER_PD_SW2,
			MASK_ANA_UMP9651_LDO_VUSBPD_PD, REG_ANA_UMP9651_LDO_VUSBPD_REG1,
			GENMASK(12, 5), 10000, 1200000, 3750000, 0),
	UMP9651_REGU_LINEAR(LDO_VDDCAMMOT, REG_ANA_UMP9651_POWER_PD_SW2,
			MASK_ANA_UMP9651_LDO_VDDCAMMOT_PD, REG_ANA_UMP9651_LDO_VDDCAMMOT_REG1,
			GENMASK(12, 5), 10000, 1200000, 3750000, 0),
	UMP9651_REGU_LINEAR(LDO_VDDVIB, REG_ANA_UMP9651_POWER_PD_SW2,
			MASK_ANA_UMP9651_LDO_VDDVIB_PD, REG_ANA_UMP9651_LDO_VDDVIB_REG1,
			GENMASK(12, 5), 10000, 1200000, 3750000, 0),
	UMP9651_REGU_LINEAR(LDO_VDDCAMIO, REG_ANA_UMP9651_POWER_PD_SW2,
			MASK_ANA_UMP9651_LDO_VDDCAMIO_PD, REG_ANA_UMP9651_LDO_VDDCAMD_REG1,
			GENMASK(10, 5), 10000, 100000, 1830000, 0),
	UMP9651_REGU_LINEAR(LDO_VDDCAMD, REG_ANA_UMP9651_POWER_PD_SW2,
			MASK_ANA_UMP9651_LDO_VDDCAMD_PD, REG_ANA_UMP9651_LDO_VDDCAMD_REG1,
			GENMASK(10, 5), 10000, 900000, 1530000, 0),
	UMP9651_REGU_LINEAR(LDO_VDDFP, REG_ANA_UMP9651_POWER_PD_SW2,
			MASK_ANA_UMP9651_LDO_VDDFP_PD, REG_ANA_UMP9651_LDO_VDDFP_REG1,
			GENMASK(12, 5), 10000, 1200000, 3750000, 0),
	UMP9651_REGU_LINEAR(DCDC_CPU1, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_DCDC_CPU1PH0_PD, REG_ANA_UMP9651_BUCK_TOP_REG4,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(DCDC_CPU2, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_DCDC_CPU2_PD, REG_ANA_UMP9651_BUCK_TOP_REG39,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(DCDC_CPU0, REG_ANA_UMP9651_POWER_PD_SW1,
			MASK_ANA_UMP9651_DCDC_CPU0_PD, REG_ANA_UMP9651_BUCK_TOP_REG36,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
	UMP9651_REGU_LINEAR(DCDC_MODEM2_SLP, REG_UMP9651_PMU_CTRL_SLP_SEQ_CTRL4,
			BIT_UMP9651_PMU_CTRL_SLP_DCDC_MODEM2_PD_EN, REG_ANA_UMP9651_BUCK_TOP_REG28,
			GENMASK(8, 0), 3125, 0, 1596875, 0),
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

	if (debugfs_locken != UMP9651_WR_UNLOCK_VALUE)
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

	if (debugfs_locken != UMP9651_WR_UNLOCK_VALUE)
		return -EINVAL;

	val = val * 1000;
	selector = regulator_map_voltage_linear(rdev,
						val - rdev->desc->uV_step / 2,
						val + rdev->desc->uV_step / 2);

	return rdev->desc->ops->set_voltage_sel(rdev, selector);
}

DEFINE_SIMPLE_ATTRIBUTE(fops_enable, debugfs_enable_get, debugfs_enable_set, "%llu\n");
DEFINE_SIMPLE_ATTRIBUTE(fops_ldo, debugfs_voltage_get, debugfs_voltage_set, "%llu\n");

static void ump9651_regulator_debugfs_init(struct regulator_dev *rdev)
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

static int ump9651_regulator_unlock(struct regmap *regmap)
{
	return regmap_write(regmap, REG_ANA_UMP9651_PWR_WR_PROT_VALUE, UMP9651_WR_UNLOCK_VALUE);
}

static void sprd_ump9651_seq_buf_init(void)
{
 sprd_ump9651_buf = kzalloc(SPRD_SOCP_PRINT_BUF_LEN, GFP_KERNEL);
 if (!sprd_ump9651_buf)
  return;
 sprd_ump9651_seq_buf = kzalloc(sizeof(*sprd_ump9651_seq_buf), GFP_KERNEL);
 if (!sprd_ump9651_seq_buf) {
  kfree(sprd_ump9651_buf);
  sprd_ump9651_buf = NULL;
  return;
 }
 seq_buf_init(sprd_ump9651_seq_buf, sprd_ump9651_buf, SPRD_SOCP_PRINT_BUF_LEN);
 /* Keep fault diagnostics using standard debugfs, without Android minidump. */
 ump9651_fault_blob.data = sprd_ump9651_buf;
 ump9651_fault_blob.size = SPRD_SOCP_PRINT_BUF_LEN;
 if (!IS_ERR_OR_NULL(debugfs_root))
  debugfs_create_blob("fault_log", 0444, debugfs_root, &ump9651_fault_blob);
}

int regulator_check_slp_pd(struct regulator *regulator)
{
	int ret;
	struct regulator_dev *sprd_rdev = regulator->rdev;
	struct regulation_constraints *regu_constr = sprd_rdev->constraints;

	ret = regulator_is_enabled(regulator);

	dev_info(&sprd_rdev->dev, "Regulator PM Check SLP_PD %s = %d!\n",
		 regu_constr->name, ret);

	return ret;
}
EXPORT_SYMBOL_GPL(regulator_check_slp_pd);

int regulator_disable_slp_pd(struct regulator *regulator)
{
	struct regulator_dev *sprd_rdev = regulator->rdev;
	struct regulation_constraints *regu_constr = sprd_rdev->constraints;
	int ret;

	dev_info(&sprd_rdev->dev, "Regulator PM SLP_PD %s DIS!\n",
		 regu_constr->name);

	ret = regulator_enable(regulator);
	if (ret)
		pr_debug("Regulator PM SLP_PD %s DIS Failed!\n", regu_constr->name);

	return ret;
}
EXPORT_SYMBOL_GPL(regulator_disable_slp_pd);

int regulator_enable_slp_pd(struct regulator *regulator)
{
	struct regulator_dev *sprd_rdev = regulator->rdev;
	struct regulation_constraints *regu_constr = sprd_rdev->constraints;
	int ret;

	dev_info(&sprd_rdev->dev, "Regulator PM SLP_PD %s EN!\n",
		 regu_constr->name);

	ret = regulator_disable(regulator);
	if (ret)
		pr_debug("Regulator PM SLP_PD %s EN Failed!\n", regu_constr->name);

	return ret;
}
EXPORT_SYMBOL_GPL(regulator_enable_slp_pd);

static int ump9651_regulator_probe(struct platform_device *pdev)
{
	int i, ret, err, count;
	struct regulator_config config = { };
	struct sprd_ump9651_eic_dbnc *sprd_eic_dbnc_dev;
	u32 socp_on = 0;

	sprd_eic_dbnc_dev = devm_kzalloc(&pdev->dev,
					 sizeof(struct sprd_ump9651_eic_dbnc), GFP_KERNEL);
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

	ret = ump9651_regulator_unlock(sprd_eic_dbnc_dev->reg_map);
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

		ump9651_regulator_debugfs_init(sprd_eic_dbnc_dev->rdev[i]);

		ret = of_property_read_u32(sprd_eic_dbnc_dev->rdev[i]->dev.of_node,
					   "regulator-socp-on", &socp_on);

		if (ret)
			dev_info(sprd_eic_dbnc_dev->dev,
				 "[%s] not support socp!!!\n", regulators[i].name);
		else
			sprd_ump9651_eic_dbnc_desc[i].ie = socp_on;

		ret = of_property_read_bool(sprd_eic_dbnc_dev->rdev[i]->dev.of_node,
					    "regulator-short-current-protection");
		if (!ret)
			dev_info(sprd_eic_dbnc_dev->dev,
				 "[%s] not support scp!\n", regulators[i].name);
		else
			sprd_ump9651_eic_dbnc_desc[i].scp = 1;
	}

	ret = sprd_ump9651_eic_dbnc_module_enable(sprd_eic_dbnc_dev, true);
	if (ret)
		return ret;

	ret = sprd_ump9651_eic_dbnc_mode_ctrl_manul(sprd_eic_dbnc_dev);
	if (ret)
		return ret;

	sprd_eic_dbnc_dev->irq = platform_get_irq(pdev, 0);
	if (sprd_eic_dbnc_dev->irq < 0) {
		dev_err(sprd_eic_dbnc_dev->dev, "Sprd Eic 51 Dbnc Mode Manu Ctrl failed\n");
		return sprd_eic_dbnc_dev->irq;
	}

	ret = devm_request_threaded_irq(&pdev->dev, sprd_eic_dbnc_dev->irq, NULL,
					sprd_ump9651_eic_dbnc_handler,
					IRQF_ONESHOT, /* Bootstrap system sleep is disabled or guarded before device PM. */
					pdev->name, sprd_eic_dbnc_dev);
	if (ret < 0) {
		dev_err(sprd_eic_dbnc_dev->dev, "failed to request ump9651 eic_dbnc irq\n");
		return ret;
	}

	sprd_ump9651_seq_buf_init();

	return 0;
}

static void ump9651_regulator_remove(struct platform_device *pdev)
{
	debugfs_remove_recursive(debugfs_root);

	kfree(sprd_ump9651_seq_buf);
	sprd_ump9651_seq_buf = NULL;
	kfree(sprd_ump9651_buf);
	sprd_ump9651_buf = NULL;

}

static const struct of_device_id ump9651_regulator_match[] = {
	{ .compatible = "sprd,ump9651-regulator" },
	{},
};

MODULE_DEVICE_TABLE(of, ump9651_regulator_match);

static struct platform_driver ump9651_regulator_driver = {
	.driver = {
		   .name = "ump9651-regulator",
		   .of_match_table = ump9651_regulator_match,
		   },
	.probe = ump9651_regulator_probe,
	.remove = ump9651_regulator_remove,
};

module_platform_driver(ump9651_regulator_driver);

MODULE_AUTHOR("yixin.lin <yixin.lin@unisoc.com>");
MODULE_DESCRIPTION("Unisoc ump9651 regulator driver");
MODULE_LICENSE("GPL");
