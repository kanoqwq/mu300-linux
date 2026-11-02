// SPDX-License-Identifier: GPL-2.0-only
/*
 * Unisoc usb high speed phy driver
 *
 * Copyright (C) 2023 Unisoc, Inc.
 * Author: Pu Li <pu.li@unisoc.com>
 * Author: Xiongpeng Wu <xiongpeng.wu@unisoc.com>
 */

#include <linux/nvmem-consumer.h>
#include "usb-phy-sprd-hs.h"

static const struct sprd_hsphy_cfg *phy_cfg;

static void phy_reset_assert(struct sprd_hsphy *phy)
{
	int ret = 0;

	if (phy->phy_rst != NULL) {
		ret = reset_control_assert(phy->phy_rst);
		if (ret < 0)
			dev_warn(phy->dev, "phy soft set reset failed, ret = %d\n", ret);
	}
	if (phy->utmi_rst != NULL) {
		ret = reset_control_assert(phy->utmi_rst);
		if (ret < 0)
			dev_warn(phy->dev, "utmi soft set reset failed, ret = %d\n", ret);
	}
	if (phy->otg_rst != NULL) {
		ret = reset_control_assert(phy->otg_rst);
		if (ret < 0)
			dev_warn(phy->dev, "otg soft set reset failed, ret = %d\n", ret);
	}

}

static void phy_reset_deassert(struct sprd_hsphy *phy)
{
	int ret = 0;

	if (phy->phy_rst != NULL) {
		ret = reset_control_deassert(phy->phy_rst);
		if (ret < 0)
			dev_warn(phy->dev, "phy soft clear reset failed, ret = %d\n", ret);
	}
	if (phy->utmi_rst != NULL) {
		ret = reset_control_deassert(phy->utmi_rst);
		if (ret < 0)
			dev_warn(phy->dev, "utmi soft clear reset failed, ret = %d\n", ret);
	}
	if (phy->otg_rst != NULL) {
		ret = reset_control_deassert(phy->otg_rst);
		if (ret < 0)
			dev_warn(phy->dev, "otg soft clear reset failed, ret = %d\n", ret);
	}

}

static void phy_enable_ctrl(struct sprd_hsphy *phy, int on)
{
	if (on) {
		if (clk_prepare_enable(phy->otg_phy_eb))
			dev_warn(phy->dev, "otg_phy_eb enable error\n");
		if (clk_prepare_enable(phy->otg_ref_eb))
			dev_warn(phy->dev, "otg_ref_eb enable error\n");
		if (clk_prepare_enable(phy->dphy_ref_eb))
			dev_warn(phy->dev, "dphy_ref_eb enable error\n");
		} else {
			clk_disable_unprepare(phy->dphy_ref_eb);
			clk_disable_unprepare(phy->otg_ref_eb);
			clk_disable_unprepare(phy->otg_phy_eb);
		}
}

/* phy_v1 cfg ops */
static void phy_v1_usb_phy_power_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		reg |= 0x1;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);

		msk = phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_L] |
			phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_S];
		regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_PWR_CTRL], msk, 0);
	} else {
		reg = msk = phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_L] |
			phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_S];
		regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_PWR_CTRL], msk, reg);

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		reg &= ~0x1;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
	}
}

static void phy_v1_usb_vbus_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg;

	reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_TEST]);
	if (on) {
		reg |= (phy_cfg->masks[MASK_AP_AHB_OTG_VBUS_VALID_EXT] |
			phy_cfg->masks[MASK_AP_AHB_OTG_VBUS_VALID_PHYREG]);
	} else {
		reg &= ~(phy_cfg->masks[MASK_AP_AHB_OTG_VBUS_VALID_EXT] |
			phy_cfg->masks[MASK_AP_AHB_OTG_VBUS_VALID_PHYREG]);
	}
	writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_TEST]);
}

static void phy_v1_utmi_width_sel(struct sprd_hsphy *phy)
{
	u32 reg;

	if (phy->type == USBPHY_INTERFACE_MODE_UTMI) {

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
		reg &= ~(phy_cfg->masks[MASK_AP_AHB_UTMI_WIDTH_SEL] |
			phy_cfg->masks[MASK_AP_AHB_USB2_DATABUS16_8]);
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
	} else {
		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
		reg |= (phy_cfg->masks[MASK_AP_AHB_UTMI_WIDTH_SEL] |
			phy_cfg->masks[MASK_AP_AHB_USB2_DATABUS16_8]);
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
	}
	/* for SPRD phy sampler sel */
	reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL1]);
	reg |= phy_cfg->masks[MASK_AP_AHB_USB20_SAMPLER_SEL];
	writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL1]);
}

static int phy_v1_set_mode(struct sprd_hsphy *phy, int on)
{
	u32 reg;

	if (on) {
		reg = phy->host_otg_ctrl0;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		reg = phy->host_otg_ctrl1;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL1]);

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
		reg &= ~phy_cfg->masks[MASK_AP_AHB_USB2_PHY_IDDIG];
		reg |= phy_cfg->masks[MASK_AP_AHB_OTG_DPPULLDOWN] |
			phy_cfg->masks[MASK_AP_AHB_OTG_DMPULLDOWN];
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		reg |= 0x200;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		phy->is_host = true;
	} else {
		reg = phy->device_otg_ctrl0;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		reg = phy->device_otg_ctrl1;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL1]);

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
		reg |= phy_cfg->masks[MASK_AP_AHB_USB2_PHY_IDDIG];
		reg &= ~(phy_cfg->masks[MASK_AP_AHB_OTG_DPPULLDOWN] |
			phy_cfg->masks[MASK_AP_AHB_OTG_DMPULLDOWN]);
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		reg &= ~0x200;
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		phy->is_host = false;
	}
	return 0;
}

static const struct sprd_hsphy_cfg_ops phy_v1_cfg_ops = {
	.usb_enable_ctrl = phy_enable_ctrl,
	.usb_phy_power_ctrl = phy_v1_usb_phy_power_ctrl,
	.usb_vbus_ctrl = phy_v1_usb_vbus_ctrl,
	.utmi_width_sel = phy_v1_utmi_width_sel,
	.reset_assert = phy_reset_assert,
	.reset_deassert = phy_reset_deassert,
	.set_mode = phy_v1_set_mode,
};

/* phy_v2 cfg ops */

static void phy_v2_usb_phy_power_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		msk = phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_L] |
			phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_S] |
			phy_cfg->masks[MASK_AON_APB_USB_ISO_SW_EN];
		regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_PWR_CTRL], msk, 0);
	} else {
		reg = msk = phy_cfg->masks[MASK_AON_APB_USB_ISO_SW_EN];
		regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_PWR_CTRL], msk, reg);
		usleep_range(10000, 15000);
		reg = msk = phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_L] |
			phy_cfg->masks[MASK_AON_APB_USB_PHY_PD_S];
		regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_PWR_CTRL], msk, reg);
	}
}

static void phy_v2_usb_vbus_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_TEST]);
		reg |= phy_cfg->masks[MASK_AP_AHB_OTG_VBUS_VALID_PHYREG];
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_TEST]);

		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_VBUSVLDEXT];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);
	} else {
		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_TEST]);
		reg &= ~phy_cfg->masks[MASK_AP_AHB_OTG_VBUS_VALID_PHYREG];
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_TEST]);

		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_VBUSVLDEXT];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);
	}
}

static void phy_v2_utmi_width_sel(struct sprd_hsphy *phy)
{
	u32 reg, msk;
	if (phy->type == USBPHY_INTERFACE_MODE_UTMI) {
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DATABUS16_8];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
		reg &= ~phy_cfg->masks[MASK_AP_AHB_UTMI_WIDTH_SEL];
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
	} else {
		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DATABUS16_8];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);

		reg = readl_relaxed(phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
		reg |= phy_cfg->masks[MASK_AP_AHB_UTMI_WIDTH_SEL];
		writel_relaxed(reg, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_CTRL]);
	}
}

static int phy_v2_set_mode(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		reg = phy->host_eye_pattern;
		regmap_write(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING], reg);

		msk = phy_cfg->masks[MASK_ANALOG_USB20_UTMIOTG_IDDG];
		regmap_update_bits(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_IDDG], msk, 0);

		reg = msk = phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_REG_SEL_CFG_0], msk, reg);
		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL2], msk, reg);

		msk = 0x200;
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, msk);
		phy->is_host = true;
	} else {
		reg = phy->device_eye_pattern;
		regmap_write(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING], reg);

		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_UTMIOTG_IDDG];
		regmap_update_bits(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_IDDG], msk, reg);

		reg = msk = phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_REG_SEL_CFG_0], msk, reg);
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL2], msk, 0);

		msk = 0x200;
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);
		phy->is_host = false;
	}
	return 0;
}

static const struct sprd_hsphy_cfg_ops phy_v2_cfg_ops = {
	.usb_enable_ctrl = phy_enable_ctrl,
	.usb_phy_power_ctrl = phy_v2_usb_phy_power_ctrl,
	.usb_vbus_ctrl = phy_v2_usb_vbus_ctrl,
	.utmi_width_sel = phy_v2_utmi_width_sel,
	.reset_assert = phy_reset_assert,
	.reset_deassert = phy_reset_deassert,
	.set_mode = phy_v2_set_mode,
};

/* phy_v3 cfg ops */

static void phy_v3_usb_phy_power_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_ISO_SW_EN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_ISO_SW], msk, 0);
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_PS_PD_L] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_PS_PD_S];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_BATTER_PLL], msk, 0);
	} else {
		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_PS_PD_L] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_PS_PD_S];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_BATTER_PLL], msk, reg);
		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_ISO_SW_EN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_ISO_SW], msk, reg);
	}
}

static void phy_v3_usb_vbus_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	msk = phy_cfg->masks[MASK_AON_APB_OTG_VBUS_VALID_PHYREG];
	reg = on ? msk : 0;
	regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_OTG_PHY_TEST], msk, reg);

	msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_VBUSVLDEXT];
	reg = on ? msk : 0;
	regmap_update_bits(phy->analog,
		phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);
}

static void phy_v3_utmi_width_sel(struct sprd_hsphy *phy)
{
	u32 reg, msk;

	if (phy->type == USBPHY_INTERFACE_MODE_UTMI) {
		msk = phy_cfg->masks[MASK_AON_APB_UTMI_WIDTH_SEL];
		regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, 0);

		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DATABUS16_8];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);
	} else {
		reg = msk = phy_cfg->masks[MASK_AON_APB_UTMI_WIDTH_SEL];
		regmap_update_bits(phy->aon_apb, phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, reg);

		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DATABUS16_8];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);
	}
}

static int phy_v3_set_mode(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		reg = phy->host_eye_pattern;
		regmap_write(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING], reg);

		msk = phy_cfg->masks[MASK_AON_APB_USB2_PHY_IDDIG];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, 0);

		reg = msk = phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_REG_SEL_CFG_0], msk, reg);
		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL2], msk, reg);

		reg = 0x200;
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_RESERVED];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);
		phy->is_host = true;
	} else {
		reg = phy->device_eye_pattern;
		regmap_write(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING], reg);

		reg = msk = phy_cfg->masks[MASK_AON_APB_USB2_PHY_IDDIG];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, reg);

		reg = msk = phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_REG_SEL_CFG_0], msk, reg);
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL2], msk, 0);

		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_RESERVED];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);
		phy->is_host = false;
	}
	return 0;
}

static const struct sprd_hsphy_cfg_ops phy_v3_cfg_ops = {
	.usb_enable_ctrl = phy_enable_ctrl,
	.usb_phy_power_ctrl = phy_v3_usb_phy_power_ctrl,
	.usb_vbus_ctrl = phy_v3_usb_vbus_ctrl,
	.utmi_width_sel = phy_v3_utmi_width_sel,
	.reset_assert = phy_reset_assert,
	.reset_deassert = phy_reset_deassert,
	.set_mode = phy_v3_set_mode,
};

/* phy_v4 cfg ops */

static void phy_v4_usb_phy_power_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		msk = phy_cfg->masks[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_L] |
			phy_cfg->masks[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_S];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_MIPI_CSI_POWER_CTRL], msk, 0);

		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_ISO_SW_EN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_PHY], msk, 0);
		msk = phy_cfg->masks[MASK_AON_APB_USB20_ISO_SW_EN];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_AON_SOC_USB_CTRL], msk, 0);
	} else {
		reg = msk = phy_cfg->masks[MASK_AON_APB_USB20_ISO_SW_EN];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_AON_SOC_USB_CTRL], msk, reg);
		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_ISO_SW_EN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_PHY], msk, reg);

		reg = msk = phy_cfg->masks[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_L] |
			phy_cfg->masks[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_S];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_MIPI_CSI_POWER_CTRL], msk, reg);
	}
}

static void phy_v4_usb_vbus_ctrl(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	msk = phy_cfg->masks[MASK_AON_APB_OTG_VBUS_VALID_PHYREG];
	reg = on ? msk : 0;
	regmap_update_bits(phy->aon_apb,
		phy_cfg->regs[REG_AON_APB_OTG_PHY_TEST], msk, reg);

	msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_VBUSVLDEXT];
	reg = on ? msk : 0;
	regmap_update_bits(phy->analog,
		phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);
}

static void phy_v4_utmi_width_sel(struct sprd_hsphy *phy)
{
	u32 reg, msk;


	if (phy->type == USBPHY_INTERFACE_MODE_UTMI) {
		msk = phy_cfg->masks[MASK_AON_APB_UTMI_WIDTH_SEL];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, 0);

		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DATABUS16_8];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);
	} else {
		reg = msk = phy_cfg->masks[MASK_AON_APB_UTMI_WIDTH_SEL];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, reg);

		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DATABUS16_8];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);
	}
}

static int phy_v4_set_mode(struct sprd_hsphy *phy, int on)
{
	u32 reg, msk;

	if (on) {
		reg = phy->host_eye_pattern;
		regmap_write(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING], reg);

		msk = phy_cfg->masks[MASK_AON_APB_USB2_PHY_IDDIG];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, 0);

		reg = msk = phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_REG_SEL_CFG_0], msk, reg);
		reg = msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL2], msk, reg);

		reg = 0x200;
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_RESERVED];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, reg);
		phy->is_host = true;
	} else {
		reg = phy->device_eye_pattern;
		regmap_write(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING], reg);

		reg = msk = phy_cfg->masks[MASK_AON_APB_USB2_PHY_IDDIG];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_OTG_PHY_CTRL], msk, reg);

		reg = msk = phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_REG_SEL_CFG_0], msk, reg);
		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DMPULLDOWN] |
			phy_cfg->masks[MASK_ANALOG_USB20_USB20_DPPULLDOWN];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL2], msk, 0);

		msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_RESERVED];
		regmap_update_bits(phy->analog,
			phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);
		phy->is_host = false;
	}
	return 0;
}


static void phy_v4_set_pmic_pd(struct sprd_hsphy *phy, int on)
{
	u32 ret = 0;

	if (on) {
		if (phy->avdd1v8) {
			ret = regulator_enable(phy->avdd1v8);
			if (ret)
				pr_err("phy->avdd1v8 regulator_enable failed!\n");
		}

		if (phy->avdd1v2) {
			ret = regulator_enable(phy->avdd1v2);
			if (ret)
				pr_err("phy->avdd1v2 regulator_enable failed!\n");
		}

		if (phy->avddmodem) {
			ret = regulator_enable(phy->avddmodem);
			if (ret)
				pr_err("phy->avddmodem regulator_enable failed!\n");
		}

		if (phy->vddmm) {
			ret = regulator_enable(phy->vddmm);
			if (ret)
				pr_err("phy->vddmm regulator_enable failed!\n");
		}
	} else {
		if (phy->avdd1v8)
			regulator_disable(phy->avdd1v8);

		if (phy->avdd1v2)
			regulator_disable(phy->avdd1v2);

		if (phy->avddmodem)
			regulator_disable(phy->avddmodem);

		if (phy->vddmm)
			regulator_disable(phy->vddmm);
	}
}

static const struct sprd_hsphy_cfg_ops phy_v4_cfg_ops = {
	.usb_enable_ctrl = phy_enable_ctrl,
	.usb_phy_power_ctrl = phy_v4_usb_phy_power_ctrl,
	.usb_vbus_ctrl = phy_v4_usb_vbus_ctrl,
	.utmi_width_sel = phy_v4_utmi_width_sel,
	.reset_assert = phy_reset_assert,
	.reset_deassert = phy_reset_deassert,
	.set_mode = phy_v4_set_mode,
	.set_pmic_pd = phy_v4_set_pmic_pd,
};

static const struct sprd_hsphy_cfg phy_cfg_1 = {
	.regs				= phy_regs_layout_1,
	.masks				= phy_masks_layout_1,
	.cfg_ops			= &phy_v1_cfg_ops,
	.parameters			= phy_parameters_1,
	.phy_version			= VERSION1,
};

static const struct sprd_hsphy_cfg phy_cfg_2 = {
	.regs				= phy_regs_layout_2,
	.masks				= phy_masks_layout_2,
	.cfg_ops			= &phy_v2_cfg_ops,
	.parameters			= phy_parameters_2,
	.phy_version			= VERSION2,
};

static const struct sprd_hsphy_cfg phy_cfg_3 = {
	.regs				= phy_regs_layout_3,
	.masks				= phy_masks_layout_3,
	.cfg_ops			= &phy_v3_cfg_ops,
	.parameters			= phy_parameters_3,
	.phy_version			= VERSION3,
};

static const struct sprd_hsphy_cfg phy_cfg_4 = {
	.regs				= phy_regs_layout_4,
	.masks				= phy_masks_layout_4,
	.cfg_ops			= &phy_v3_cfg_ops,
	.parameters			= phy_parameters_4,
	.phy_version			= VERSION3,
};

static const struct sprd_hsphy_cfg phy_cfg_5 = {
	.regs				= phy_regs_layout_5,
	.masks				= phy_masks_layout_5,
	.cfg_ops			= &phy_v4_cfg_ops,
	.parameters			= phy_parameters_5,
	.phy_version			= VERSION4,
};

static const struct sprd_hsphy_cfg phy_cfg_6 = {
	.regs				= phy_regs_layout_6,
	.masks				= phy_masks_layout_6,
	.cfg_ops			= &phy_v4_cfg_ops,
	.parameters			= phy_parameters_6,
	.phy_version			= VERSION4,
};

static const struct sprd_hsphy_cfg phy_cfg_7 = {
	.regs				= phy_regs_layout_7,
	.masks				= phy_masks_layout_7,
	.cfg_ops			= &phy_v4_cfg_ops,
	.parameters			= phy_parameters_7,
	.phy_version			= VERSION4,
};

static int sprd_hsphy_vbus_notify(struct notifier_block *nb,
				  unsigned long event, void *data)
{
	return 0;
}

static int sprd_hsphy_notify_connect(struct usb_phy *x,
		enum usb_device_speed speed)
{
	struct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
	const struct sprd_hsphy_cfg_ops *cfg_ops = phy_cfg->cfg_ops;

	/* usb vbus valid*/
	cfg_ops->usb_vbus_ctrl(phy, true);
	return 0;
}

static int sprd_hsphy_notify_disconnect(struct usb_phy *x,
		enum usb_device_speed speed)
{
	struct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
	const struct sprd_hsphy_cfg_ops *cfg_ops = phy_cfg->cfg_ops;

	/* usb vbus invalid*/
	cfg_ops->usb_vbus_ctrl(phy, false);
	return 0;
}

static int sprd_hostphy_set(struct usb_phy *x, int on)
{
	struct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
	const struct sprd_hsphy_cfg_ops *cfg_ops = phy_cfg->cfg_ops;
	int ret = 0;

	dev_info(x->dev, "[%s] enters!\n", __func__);
	mutex_lock(&phy->hsphy_mutex);
	ret = cfg_ops->set_mode(phy, on);
	mutex_unlock(&phy->hsphy_mutex);
	return ret;
}

static int sprd_set_pmic_pd(struct usb_phy *x, bool on)
{
	struct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
	const struct sprd_hsphy_cfg_ops *cfg_ops = phy_cfg->cfg_ops;
	int ret = 0;

	dev_info(x->dev, "[%s] enters!\n", __func__);
	if (!atomic_read(&phy->inited)) {
		dev_info(x->dev, "%s is already shut down\n", __func__);
		return 0;
	}

	if (!phy->pmic) {
		dev_err(x->dev, "pmic is null, cannot set pmic!\n");
		return 0;
	}

	mutex_lock(&phy->hsphy_mutex);
	cfg_ops->set_pmic_pd(phy, on);
	mutex_unlock(&phy->hsphy_mutex);
	return ret;
}

static int sprd_hsphy_init(struct usb_phy *x)
{
	struct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
	const struct sprd_hsphy_cfg_ops *cfg_ops = phy_cfg->cfg_ops;
	u32 reg = 0;
	int ret = 0;

	if (atomic_read(&phy->inited)) {
		dev_dbg(x->dev, "%s is already inited!\n", __func__);
		return 0;
	}
	dev_info(x->dev, "[%s] enters!\n", __func__);

	sprd_usbm_mutex_lock();
	mutex_lock(&phy->hsphy_mutex);

	/* Turn On VDD */
	regulator_set_voltage(phy->vdd, phy->vdd_vol, phy->vdd_vol);
	ret = regulator_enable(phy->vdd);
	if (ret) {
		mutex_unlock(&phy->hsphy_mutex);
		sprd_usbm_mutex_unlock();
		return ret;
	}

	sprd_usbm_hsphy_set_onoff(1);

	if (sprd_usbm_event_is_active()) {
		/* select the AON-SYS USB controller */
		reg = phy_cfg->masks[MASK_AON_APB_USB20_CTRL_MUX_REG];
		regmap_update_bits(phy->aon_apb,
			phy_cfg->regs[REG_AON_APB_AON_SOC_USB_CTRL], reg, 0);
	}

	/* refclk usb31pll pd bind with pub sys */
	if (phy->refclk_cfg.regmap_ptr) {
		reg = phy->refclk_cfg.args[1];
		ret |= regmap_update_bits(phy->refclk_cfg.regmap_ptr,
			phy->refclk_cfg.args[0], reg, reg);
	}
	/* usb enable */
	cfg_ops->usb_enable_ctrl(phy, true);

	/* usb phy power on */
	cfg_ops->usb_phy_power_ctrl(phy, true);

	/* for SPRD phy utmi_width sel */
	cfg_ops->utmi_width_sel(phy);

	/* set phy in normal mode */
	cfg_ops->reset_deassert(phy);

	atomic_set(&phy->inited, 1);

	mutex_unlock(&phy->hsphy_mutex);
	sprd_usbm_mutex_unlock();

	return 0;
}

static void sprd_hsphy_shutdown(struct usb_phy *x)
{
	u32 reg = 0;
	struct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
	const struct sprd_hsphy_cfg_ops *cfg_ops = phy_cfg->cfg_ops;

	if (!atomic_read(&phy->inited)) {
		dev_info(x->dev, "%s is already shut down\n", __func__);
		return;
	}
	dev_info(x->dev, "[%s] enters!\n", __func__);

	sprd_usbm_mutex_lock();
	mutex_lock(&phy->hsphy_mutex);
	dev_info(x->dev, "usbm_event_is_active(%d), usbm_ssphy_get_onoff(%d)\n",
				sprd_usbm_event_is_active(), sprd_usbm_ssphy_get_onoff());
	sprd_usbm_hsphy_set_onoff(0);

	if (sprd_usbm_ssphy_get_onoff())
		goto End;

	/* usb power down */
	if (sprd_usbmux_check_mode() != MUX_MODE) {
		/* set phy reset */
		cfg_ops->reset_assert(phy);
		/* set phy power down */
		cfg_ops->usb_phy_power_ctrl(phy, false);
	}

	cfg_ops->usb_enable_ctrl(phy, false);
	/* refclk usb31pll pd unbind with pub sys */
	if (phy->refclk_cfg.regmap_ptr) {
		reg = phy->refclk_cfg.args[1];
		regmap_update_bits(phy->refclk_cfg.regmap_ptr,
			phy->refclk_cfg.args[0], reg, 0);
	}

End:
	regulator_disable(phy->vdd);

	atomic_set(&phy->inited, 0);

	mutex_unlock(&phy->hsphy_mutex);
	sprd_usbm_mutex_unlock();

}

/* attributes */
static ssize_t phy_tune_show(struct device *dev, struct device_attribute *attr,
			      char *buf)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);

	if (!phy)
		return -EINVAL;

	return snprintf(buf, PAGE_SIZE, "0x%x\n", phy->phy_tune);
}

static ssize_t phy_tune_store(struct device *dev,
			       struct device_attribute *attr,
			       const char *buf, size_t size)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);

	if (!phy)
		return -EINVAL;

	if (kstrtouint(buf, 16, &phy->phy_tune) < 0)
		return -EINVAL;

	return size;
}
static DEVICE_ATTR_RW(phy_tune);

static ssize_t vdd_voltage_show(struct device *dev,
				struct device_attribute *attr,
				char *buf)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);

	if (!phy)
		return -EINVAL;

	return snprintf(buf, PAGE_SIZE, "%d\n", phy->vdd_vol);
}

static ssize_t vdd_voltage_store(struct device *dev,
				 struct device_attribute *attr,
				 const char *buf, size_t size)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);
	u32 vol;

	if (!phy)
		return -EINVAL;

	if (kstrtouint(buf, 10, &vol) < 0)
		return -EINVAL;

	if (vol < 1200000 || vol > 3750000) {
		dev_err(dev, "Invalid voltage value %d\n", vol);
		return -EINVAL;
	}
	phy->vdd_vol = vol;

	return size;
}
static DEVICE_ATTR_RW(vdd_voltage);

static ssize_t hsphy_device_eye_pattern_show(struct device *dev,
			struct device_attribute *attr,
			char *buf)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);

	if (!phy)
		return -EINVAL;

	return snprintf(buf, PAGE_SIZE, "0x%x\n", phy->device_eye_pattern);
}

static ssize_t hsphy_device_eye_pattern_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t size)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);

	if (!phy)
		return -EINVAL;

	if (kstrtouint(buf, 16, &phy->device_eye_pattern) < 0)
		return -EINVAL;

	return size;
}
static DEVICE_ATTR_RW(hsphy_device_eye_pattern);

static ssize_t hsphy_host_eye_pattern_show(struct device *dev,
			struct device_attribute *attr,
			char *buf)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);

	if (!phy)
		return -EINVAL;

	return snprintf(buf, PAGE_SIZE, "0x%x\n", phy->host_eye_pattern);
}

static ssize_t hsphy_host_eye_pattern_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t size)
{
	struct sprd_hsphy *phy = dev_get_drvdata(dev);

	if (!phy)
		return -EINVAL;

	if (kstrtouint(buf, 16, &phy->host_eye_pattern) < 0)
		return -EINVAL;

	return size;
}

static DEVICE_ATTR_RW(hsphy_host_eye_pattern);

static struct attribute *usb_hsphy_attrs[] = {
	&dev_attr_phy_tune.attr,
	&dev_attr_vdd_voltage.attr,
	&dev_attr_hsphy_device_eye_pattern.attr,
	&dev_attr_hsphy_host_eye_pattern.attr,
	NULL
};
ATTRIBUTE_GROUPS(usb_hsphy);

static int sprd_eyepatt_follow_efuse(struct sprd_hsphy *phy,
				struct device *dev,
				const char *cell_id)
{
	struct nvmem_cell *cell;
	void *buf;
	size_t len;
	u32 efuse_val = 0;

	cell = of_nvmem_cell_get(dev->of_node, cell_id);
	if (IS_ERR(cell))
		return PTR_ERR(cell);

	buf = nvmem_cell_read(cell, &len);
	if (IS_ERR(buf)) {
		nvmem_cell_put(cell);
		return PTR_ERR(buf);
	}

	memcpy(&efuse_val, buf, min(len, sizeof(u32)));

	kfree(buf);
	buf = NULL;

	return efuse_val;
}

static int sprd_eyepatt_get_tfregres(struct sprd_hsphy *phy, struct device *dev)
{
	u32 val = 0, i;
	struct device_node *algo_node;
	int algo_count = 0, offset_count = 0, ret = 0;
	u32 *algo_array, *offset_array;

	algo_node = of_parse_phandle(dev->of_node, "sprd,hsphy-algorithm-val", 0);
	if (!algo_node) {
		dev_err(dev, "get sprd,hsphy-algorithm-val fail\n");
		return -ENODEV;
	}

	algo_count = of_property_count_u32_elems(algo_node, "sprd,algorithm");
	if (algo_count < 0) {
		dev_err(dev, "get sprd,algorithm fail\n");
		return -EINVAL;
	}

	offset_count = of_property_count_u32_elems(algo_node, "sprd,offset");
	if (offset_count < 0) {
		dev_err(dev, "get sprd,offset fail\n");
		return -EINVAL;
	}

	algo_array = devm_kcalloc(dev, algo_count, sizeof(u32), GFP_KERNEL);
	if (!algo_array)
		return -ENOMEM;

	offset_array = devm_kcalloc(dev, offset_count, sizeof(u32), GFP_KERNEL);
	if (!offset_array)
		goto no_algo_out;

	ret = of_property_read_u32_array(algo_node, "sprd,algorithm", algo_array, algo_count);
	if (ret < 0)
		goto no_offset_out;

	ret = of_property_read_u32_array(algo_node, "sprd,offset", offset_array, offset_count);
	if (ret < 0)
		goto no_offset_out;

	val = (phy->efuse_trim >> phy->efuse_offset[EFUSE_TFREGRES]) & 0x3F;
	if ((algo_count > 1) && (val >= algo_array[0] && val < algo_array[1])) {
		val = offset_array[0];
		return val;
	}

	for (i = 1; i < algo_count - 1; i++) {
		if (val >= algo_array[i] && val < algo_array[i+1]) {
			val -= offset_array[i];
			break;
		}
	}
	return val;

no_offset_out:
	kfree(offset_array);
	offset_array = NULL;
no_algo_out:
	kfree(algo_array);
	algo_array = NULL;
	return -ENOMEM;
}

static int sprd_eyepatt_tuneotg_set(struct sprd_hsphy *phy, struct device *dev)
{
	u32 val;

	if (phy->efuse_trim & BIT(30)) {
		val = (phy->efuse_trim >> phy->efuse_offset[EFUSE_TUNEOTG]) & 0x7;
		/* device setting */
		phy->device_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEOTG];
		phy->device_eye_pattern |= val << phy_cfg->parameters[TUNEOTG_SHIFT];

		/* host setting */
		phy->host_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEOTG];
		phy->host_eye_pattern |= val << phy_cfg->parameters[TUNEOTG_SHIFT];
	}

	return 0;
}

static int sprd_eyepatt_tfhsres_set(struct sprd_hsphy *phy, struct device *dev)
{
	u32 val;

	if (phy->efuse_trim & BIT(30)) {
		val = (phy->efuse_trim >> phy->efuse_offset[EFUSE_TFHSRES]) & 0x1F;
		/* device setting */
		phy->device_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TFHSRES];
		phy->device_eye_pattern |= val << phy_cfg->parameters[TFHSRES_SHIFT];

		/* host setting */
		phy->host_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TFHSRES];
		phy->host_eye_pattern |= val << phy_cfg->parameters[TFHSRES_SHIFT];
	}
	return 0;
}

/* prepare eye pattern */
static int sprd_eyepatt_tunehsamp_set(struct sprd_hsphy *phy, struct device *dev)
{
	int ret = 0;
	u8 val[2];

	ret = of_property_read_u8_array(dev->of_node, "sprd,hsphy-tunehsamp", val, 2);

	if (ret < 0) {
		dev_err(dev, "unable to get hsphy-device-tunehsamp\n");
		return ret;
	}

	if (phy_cfg->phy_version == VERSION1) {
		/* device setting */
		phy->device_otg_ctrl0 &= ~phy_cfg->masks[MASK_AP_AHB_USB20_TUNEHSAMP];
		phy->device_otg_ctrl0 |= (u32)val[0] << phy_cfg->parameters[TUNEHSAMP_SHIFT];

		/* host setting */
		phy->host_otg_ctrl0 &= ~phy_cfg->masks[MASK_AP_AHB_USB20_TUNEHSAMP];
		phy->host_otg_ctrl0 |= (u32)val[1] << phy_cfg->parameters[TUNEHSAMP_SHIFT];

	} else {
		/* device setting */
		phy->device_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEHSAMP];
		phy->device_eye_pattern |= (u32)val[0] << phy_cfg->parameters[TUNEHSAMP_SHIFT];

		/* host setting */
		phy->host_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEHSAMP];
		phy->host_eye_pattern |= (u32)val[1] << phy_cfg->parameters[TUNEHSAMP_SHIFT];
	}

	return 0;
}

/* prepare eye pattern */
static int sprd_eyepatt_tunedec_set(struct sprd_hsphy *phy, struct device *dev)
{
	int ret = 0;
	u8 val;

	ret = of_property_read_u8(dev->of_node, "sprd,hsphy-tunedec", &val);

	if (ret < 0) {
		dev_err(dev, "unable to get hsphy-device-tunehsamp\n");
		return ret;
	}

	/* device setting */
	phy->device_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEDEC];
	phy->device_eye_pattern |= (u32)val << phy_cfg->parameters[TUNEDEC_SHIFT];

	/* host setting */
	phy->host_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEDEC];
	phy->host_eye_pattern |= (u32)val << phy_cfg->parameters[TUNEDEC_SHIFT];

	return 0;
}

static int sprd_eyepatt_tuneeq_set(struct sprd_hsphy *phy, struct device *dev)
{
	int ret = 0;
	u8 val[2];

	ret = of_property_read_u8_array(dev->of_node, "sprd,hsphy-tuneeq", val, 2);

	if (ret < 0) {
		dev_err(dev, "unable to get hsphy-tuneeq\n");
		return ret;
	}

	if (phy_cfg->phy_version == VERSION1) {
		/* device setting */
		phy->device_otg_ctrl1 &= ~phy_cfg->masks[MASK_AP_AHB_USB20_TUNEEQ];
		phy->device_otg_ctrl1 |= (u32)val[0] << phy_cfg->parameters[TUNEEQ_SHIFT];

		/* host setting */
		phy->host_otg_ctrl1 &= ~phy_cfg->masks[MASK_AP_AHB_USB20_TUNEEQ];
		phy->host_otg_ctrl1 |= (u32)val[1] << phy_cfg->parameters[TUNEEQ_SHIFT];

	} else {
		/* device setting */
		phy->device_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEEQ];
		phy->device_eye_pattern |= (u32)val[0] << phy_cfg->parameters[TUNEEQ_SHIFT];

		/* host setting */
		phy->host_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TUNEEQ];
		phy->host_eye_pattern |= (u32)val[1] << phy_cfg->parameters[TUNEEQ_SHIFT];
	}

	return 0;
}

static int sprd_eyepatt_tfregres_set(struct sprd_hsphy *phy, struct device *dev)
{
	int ret = 0;
	u8 val[2];
	int val_efuse = 0;

	ret = of_property_read_u8_array(dev->of_node, "sprd,hsphy-tfregres", val, 2);
	if (ret < 0) {
		dev_err(dev, "unable to get hsphy-tfregres\n");
		return ret;
	}

	if (phy_cfg->phy_version == VERSION1) {
		/* device setting */
		phy->device_otg_ctrl1 &= ~phy_cfg->masks[MASK_AP_AHB_USB20_TFREGRES];
		phy->device_otg_ctrl1 |= (u32)val[0] << phy_cfg->parameters[TFREGRES_SHIFT];

		/* host setting */
		phy->host_otg_ctrl1 &= ~phy_cfg->masks[MASK_AP_AHB_USB20_TFREGRES];
		phy->host_otg_ctrl1 |= (u32)val[1] << phy_cfg->parameters[TFREGRES_SHIFT];
	} else {
		if (phy->efuse_trim & BIT(30)) {
			val_efuse = sprd_eyepatt_get_tfregres(phy, dev);
			if (val_efuse < 0)
				val_efuse = (phy->efuse_trim >>
					phy->efuse_offset[EFUSE_TFREGRES]) & 0x3F;
			/* device setting */
			phy->device_eye_pattern &=
						~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TFREGRES];
			phy->device_eye_pattern |= val_efuse << phy_cfg->parameters[TFREGRES_SHIFT];

			/* host setting */
			phy->host_eye_pattern &= ~phy_cfg->masks[MASK_ANALOG_USB20_USB20_TFREGRES];
			phy->host_eye_pattern |= val_efuse << phy_cfg->parameters[TFREGRES_SHIFT];
		}
	}

	return 0;
}

static int sprd_eye_pattern_prepared(struct sprd_hsphy *phy, struct device *dev)
{
	int ret = 0;

	if (phy_cfg->phy_version == VERSION1) {
		writel_relaxed(phy->phy_tune, phy->base + phy_cfg->regs[REG_AP_AHB_OTG_PHY_TUNE]);
		/* USB PHY write register need to delay 2ms~3ms */
		usleep_range(2000, 3000);
		/* set default OTG_CTRL */
		phy->device_otg_ctrl0 = readl_relaxed(phy->base +
				phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		phy->device_otg_ctrl1 = readl_relaxed(phy->base +
				phy_cfg->regs[REG_AP_AHB_OTG_CTRL1]);
		dev_info(dev, "%s: default device REG_AP_AHB_OTG_CTRL0: 0x%x\n",
			__func__, phy->device_otg_ctrl0);
		dev_info(dev, "%s: default device REG_AP_AHB_OTG_CTRL1: 0x%x\n",
			__func__, phy->device_otg_ctrl1);

		phy->host_otg_ctrl0 = readl_relaxed(phy->base +
				phy_cfg->regs[REG_AP_AHB_OTG_CTRL0]);
		phy->host_otg_ctrl1 = readl_relaxed(phy->base +
				phy_cfg->regs[REG_AP_AHB_OTG_CTRL1]);
		dev_info(dev, "%s: default host REG_AP_AHB_OTG_CTRL0: 0x%x\n", __func__,
			phy->host_otg_ctrl0);
		dev_info(dev, "%s: default host REG_AP_AHB_OTG_CTRL1: 0x%x\n", __func__,
			phy->host_otg_ctrl1);
	} else {
		/* set default eyepatt */
		regmap_read(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING],
					&phy->device_eye_pattern);
		dev_info(dev, "%s: default device eye_pattern: 0x%x\n", __func__,
					phy->device_eye_pattern);

		regmap_read(phy->analog, phy_cfg->regs[REG_ANALOG_USB20_USB20_TRIMMING],
					&phy->host_eye_pattern);
		dev_info(dev, "%s: default host eye_pattern: 0x%x\n", __func__,
					phy->host_eye_pattern);
	}

	phy->efuse_offset = devm_kcalloc(dev, EFUSE_CALI_NUM, sizeof(u32), GFP_KERNEL);
	if (!phy->efuse_offset)
		return -ENOMEM;

	ret = of_property_read_u32_array(dev->of_node, "sprd,efuse-offset-val",
					phy->efuse_offset, EFUSE_CALI_NUM);
	if (ret < 0) {
		dev_err(dev, "unable to get sprd,efuse-offset-val\n");
		return ret;
	}

	/* set eye pattern accord to the efuse value */
	phy->efuse_trim = sprd_eyepatt_follow_efuse(phy, dev, "usb20_eyepattern");
	if (!(phy->efuse_trim & BIT(30))) {
		dev_err(dev, "%s efuse trim BIT30 miss\n", __func__);
		return -EINVAL;
	}

	/* set eyepatt tuneotg */
	ret |= sprd_eyepatt_tuneotg_set(phy, dev);

	/* set eyepatt tunedsc */
	ret |= sprd_eyepatt_tunedec_set(phy, dev);

	/* set eyepatt tfhsres */
	ret |= sprd_eyepatt_tfhsres_set(phy, dev);

	/* set eyepatt tunehsamp */
	ret |= sprd_eyepatt_tunehsamp_set(phy, dev);

	/* set eyepatt tuneeq */
	ret |= sprd_eyepatt_tuneeq_set(phy, dev);

	/* set eyepatt tfregres */
	ret |= sprd_eyepatt_tfregres_set(phy, dev);

	return ret;
}

static int sprd_hsphy_set_dpdm_high_resistance(struct sprd_hsphy *phy)
{
	u32 msk;
	int ret = 0;

	dev_info(phy->dev, "[%s] entry!\n", __func__);

	/* dp high resistance is not affected by iso, pd_s, pd_l
	 * dm high resistance is affected by iso, pd_s, pd_l, all 0.
	 */
	msk = phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN] |
				phy_cfg->masks[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN];
	ret |= regmap_update_bits(phy->analog,
				phy_cfg->regs[REG_ANALOG_USB20_REG_SEL_CFG_0], msk, msk);

	msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_VBUSVLDEXT];
	ret = regmap_update_bits(phy->analog,
				phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL1], msk, 0);

	msk = phy_cfg->masks[MASK_ANALOG_USB20_USB20_DMPULLDOWN] |
				phy_cfg->masks[MASK_ANALOG_USB20_USB20_DPPULLDOWN];
	ret |= regmap_update_bits(phy->analog,
				phy_cfg->regs[REG_ANALOG_USB20_USB20_UTMI_CTL2], msk, 0);

	if (ret)
		dev_err(phy->dev, "%s error, ret = %d\n", __func__, ret);

	return ret;
}

/* charegr detect */
static enum usb_charger_type sprd_hsphy_charger_detect(struct usb_phy *x)
{
	enum usb_charger_type type = UNKNOWN_TYPE;
	struct sprd_hsphy *phy = container_of(x, struct sprd_hsphy, phy);
	int ret = 0;

	if (phy->is_dpdm_parallel) {
		ret = regulator_enable(phy->vdd);
		if (ret)
			dev_WARN(x->dev, "phy_vdd regulator_enable failed!\n");
		mutex_lock(&phy->hsphy_mutex);
		/* need open USBVDD33 and set dp/dm in high-impedance state */
		sprd_hsphy_set_dpdm_high_resistance(phy);
	}

	type = sprd_bc1p2_charger_detect(x);

	if (phy->is_dpdm_parallel) {
		mutex_unlock(&phy->hsphy_mutex);
		if (!ret)
			regulator_disable(phy->vdd);
	}

	return type;
}

static int sprd_hsphy_cali_mode(void)
{
	struct device_node *cmdline_node;
	const char *cmdline;
	int ret;
	bool mode;

	cmdline_node = of_find_node_by_path("/chosen");
	ret = of_property_read_string(cmdline_node, "bootargs", &cmdline);

	if (ret) {
		pr_err("Can't not parse bootargs\n");
		return 0;
	}

	mode = (strstr(cmdline, "androidboot.mode=cali") != NULL) ||
		(strstr(cmdline, "androidboot.mode=autotest") != NULL) ||
		(strstr(cmdline, "sprdboot.mode=cali") != NULL) ||
		(strstr(cmdline, "sprdboot.mode=autotest") != NULL);
	if (mode)
		return 1;

	return 0;
}

static int sprd_hsphy_clk_init(struct platform_device *pdev, struct sprd_hsphy *phy)
{
	struct clk *clk_otg, *clk_parent;
	int ret = 0;

	phy->otg_ref_eb = devm_clk_get(&pdev->dev, "otg_ref_eb");
	if (IS_ERR(phy->otg_ref_eb)) {
		dev_warn(&pdev->dev, "can't get the otg_ref_eb clock\n");
		phy->otg_ref_eb = NULL;
	}

	phy->dphy_ref_eb = devm_clk_get(&pdev->dev, "dphy_ref_eb");
	if (IS_ERR(phy->dphy_ref_eb)) {
		dev_warn(&pdev->dev, "can't get the dphy_ref_eb clock\n");
		phy->dphy_ref_eb = NULL;
	}

	phy->otg_phy_eb = devm_clk_get(&pdev->dev, "otg_phy_eb");
	if (IS_ERR(phy->otg_phy_eb)) {
		dev_warn(&pdev->dev, "can't get the otg_phy_eb clock\n");
		phy->otg_phy_eb = NULL;
	}

	clk_otg = devm_clk_get(&pdev->dev, "otg-ref");
	if (IS_ERR(clk_otg)) {
		dev_warn(&pdev->dev, "can't get the hsphy clock\n");
		clk_otg = NULL;
		return -EINVAL;
	}
	clk_parent = devm_clk_get(&pdev->dev, "source");
	if (IS_ERR(clk_parent)) {
		dev_warn(&pdev->dev, "can't get the source clock\n");
		clk_parent = NULL;
		return -EINVAL;
	}

	ret = clk_set_parent(clk_otg, clk_parent);
	if (ret)
		dev_warn(&pdev->dev, "set clock source error\n");
	return ret;
}

static void sprd_hsphy_lowpower_pd_init(struct platform_device *pdev, struct sprd_hsphy *phy)
{
	phy->avdd1v8 = devm_regulator_get(&pdev->dev, "avdd1v8");
	if (IS_ERR(phy->avdd1v8)) {
		dev_err(&pdev->dev, "can't get avdd1v8\n");
		phy->avdd1v8 = NULL;
	}

	phy->avdd1v2 = devm_regulator_get(&pdev->dev, "avdd1v2");
	if (IS_ERR(phy->avdd1v2)) {
		dev_err(&pdev->dev, "can't get avdd1v2\n");
		phy->avdd1v2 = NULL;
	}

	phy->avddmodem = devm_regulator_get(&pdev->dev, "avddmodem");
	if (IS_ERR(phy->avddmodem)) {
		dev_err(&pdev->dev, "can't get avddmodem\n");
		phy->avddmodem = NULL;
	}

	phy->vddmm = devm_regulator_get(&pdev->dev, "vddmm");
	if (IS_ERR(phy->vddmm)) {
		dev_err(&pdev->dev, "can't get vddmm\n");
		phy->vddmm = NULL;
	}
}

static int sprd_hsphy_probe(struct platform_device *pdev)
{
	struct device_node *regmap_np = NULL;
	struct platform_device *regmap_pdev;
	struct sprd_hsphy *phy;
	struct device *dev = &pdev->dev;
	int ret = 0, calimode = 0;
	struct usb_otg *otg;

	phy = devm_kzalloc(dev, sizeof(*phy), GFP_KERNEL);
	if (!phy)
		return -ENOMEM;

	otg = devm_kzalloc(dev, sizeof(*otg), GFP_KERNEL);
	if (!otg)
		return -ENOMEM;

	/* phy cfg data */
	phy_cfg = of_device_get_match_data(dev);
	if (!phy_cfg) {
		dev_err(dev, "no matching driver data found\n");
		return -EINVAL;
	}

	phy->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(phy->base))
		return PTR_ERR(phy->base);

	/* pmic regmap */
	regmap_np = of_find_compatible_node(NULL, NULL, "sprd,ump962x-syscon");
	if (!regmap_np) {
		regmap_np = of_find_compatible_node(NULL, NULL, "sprd,sc27xx-syscon");
		if (!regmap_np) {
			regmap_np = of_find_compatible_node(NULL, NULL, "sprd,ump965x-syscon");
			if (!regmap_np)
				dev_err(dev, "unable to get syscon node\n");
		}
	}
	regmap_pdev = of_find_device_by_node(regmap_np);
	if (!regmap_pdev) {
		dev_err(dev, "unable to get syscon platform device\n");
		ret = -ENODEV;
		goto device_node_err;
	}

	phy->pmic = dev_get_regmap(regmap_pdev->dev.parent, NULL);
	if (!phy->pmic) {
		dev_err(dev, "unable to get pmic regmap device\n");
		ret = -ENODEV;
		goto platform_device_err;
	}

	phy->phy_rst = devm_reset_control_get_shared(dev, "phy_rst");
	if (IS_ERR(phy->phy_rst)) {
		dev_err(dev, "can't get phy_rst node\n");
		phy->phy_rst = NULL;
	}
	phy->utmi_rst = devm_reset_control_get_shared(dev, "utmi_rst");
	if (IS_ERR(phy->utmi_rst)) {
		dev_err(dev, "can't get utmi_rst node\n");
		phy->utmi_rst = NULL;
	}
	phy->otg_rst = devm_reset_control_get_shared(dev, "otg_rst");
	if (IS_ERR(phy->otg_rst)) {
		dev_err(dev, "can't get otg_rst node\n");
		phy->otg_rst = NULL;
	}
	/* set vdd */
	ret = of_property_read_u32(dev->of_node, "sprd,vdd-voltage",
				   &phy->vdd_vol);
	if (ret < 0) {
		dev_err(dev, "unable to read hsphy vdd voltage\n");
		goto platform_device_err;
	}

	calimode = sprd_hsphy_cali_mode();
	if (calimode) {
		phy->vdd_vol = phy_cfg->parameters[FULLSPEED_USB33_TUNE];
		dev_info(dev, "calimode vdd_vol:%d\n", phy->vdd_vol);
	}

	phy->vdd = devm_regulator_get(dev, "vdd");
	if (IS_ERR(phy->vdd)) {
		dev_err(dev, "unable to get hsphy vdd supply\n");
		ret = PTR_ERR(phy->vdd);
		goto platform_device_err;
	}

	/* get lowpower electricity */
	sprd_hsphy_lowpower_pd_init(pdev, phy);

	ret = regulator_set_voltage(phy->vdd, phy->vdd_vol, phy->vdd_vol);
	if (ret < 0) {
		dev_err(dev, "fail to set hsphy vdd voltage at %dmV\n",
			phy->vdd_vol);
		goto platform_device_err;
	}

	/* phy tune */
	ret = of_property_read_u32(dev->of_node, "sprd,tune-value",
				&phy->phy_tune);
	if (ret < 0)
		dev_warn(dev, "unable to read hsphy usb phy tune\n");

	/* analog & aoapb & apahb regmap */
	phy->aon_apb = syscon_regmap_lookup_by_phandle(dev->of_node,
				 "sprd,syscon-aonapb");
	if (IS_ERR(phy->aon_apb)) {
		dev_warn(dev, "USB aon apb syscon failed!\n");
		phy->aon_apb = NULL;
	}

	phy->ap_ahb = syscon_regmap_lookup_by_phandle(dev->of_node,
				"sprd,syscon-apahb");
	if (IS_ERR(phy->ap_ahb)) {
		dev_warn(dev, "USB apahb syscon failed!\n");
		phy->ap_ahb = NULL;
	}

	phy->analog = syscon_regmap_lookup_by_phandle(dev->of_node,
				"sprd,syscon-ana");
	if (IS_ERR(phy->analog)) {
		dev_warn(dev, "USB analog syscon failed!\n");
		phy->analog = NULL;
	}

	/*in order to make sure that aon phy and ipaphy is independent 8520 need
	 *  modify aon phy clk source to 26M and config analog reserved bit0-1
	 */
	ret = sprd_hsphy_clk_init(pdev, phy);
	if (ret)
		dev_warn(dev, "aon hsphy uses default clk source\n");

	/* prepare eye pattern */
	ret = sprd_eye_pattern_prepared(phy, dev);
	if (ret < 0)
		dev_warn(dev, "sprd_eye_pattern_prepared failed, ret = %d\n", ret);

	/* usb vbus invalid */
	phy_cfg->cfg_ops->usb_vbus_ctrl(phy, false);

	/* usb phy power down */
	if (sprd_usbmux_check_mode() != MUX_MODE) {
		/* set phy reset */
		phy_cfg->cfg_ops->reset_deassert(phy);
		udelay(2);
		phy_cfg->cfg_ops->reset_assert(phy);
		/* set phy power down */
		phy_cfg->cfg_ops->usb_phy_power_ctrl(phy, 0);
	}

	phy->type = of_usb_get_phy_mode(dev->of_node);
	if (phy->type == USBPHY_INTERFACE_MODE_UTMI)
	/* musb_set_utmi_60m_flag() is used to control the must i2s clk
	 * configuration, pass parameter "true" if we use 60MHz utmi phy
	 */
		musb_set_utmi_60m_flag(true);
	else
		phy->type = USBPHY_INTERFACE_MODE_UTMIW;

	phy->refclk_cfg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,
			"refclk_cfg", 2, phy->refclk_cfg.args);
	if (IS_ERR(phy->refclk_cfg.regmap_ptr)) {
		dev_warn(&pdev->dev, "failed to get refclk_cfg regmap!\n");
		phy->refclk_cfg.regmap_ptr = NULL;
	}

	if (of_property_read_bool(dev->of_node, "dpdm-parallel"))
		phy->is_dpdm_parallel = true;
	else
		phy->is_dpdm_parallel = false;
	dev_info(dev, "dp/dm mode: %s\n", phy->is_dpdm_parallel ? "parallel" : "series");

	mutex_init(&phy->hsphy_mutex);

	phy->dev = dev;
	phy->phy.dev = dev;
	phy->phy.label = "sprd-hsphy";
	phy->phy.otg = otg;
	phy->phy.init = sprd_hsphy_init;
	phy->phy.shutdown = sprd_hsphy_shutdown;
	phy->phy.set_vbus = sprd_hostphy_set;
	phy->phy.set_wakeup = sprd_set_pmic_pd;
	phy->phy.type = USB_PHY_TYPE_USB2;
	phy->phy.vbus_nb.notifier_call = sprd_hsphy_vbus_notify;
	phy->phy.charger_detect = sprd_hsphy_charger_detect;
	/*
	 * notify_connect is used to set vbusvalid
	 * notify_disconnect is used to set vbusinvalid
	 */
	phy->phy.notify_connect = sprd_hsphy_notify_connect;
	phy->phy.notify_disconnect = sprd_hsphy_notify_disconnect;
	otg->usb_phy = &phy->phy;

	device_init_wakeup(phy->dev, true);

	platform_set_drvdata(pdev, phy);

	ret = usb_add_phy_dev(&phy->phy);
	if (ret) {
		dev_err(dev, "fail to add phy\n");
		goto  platform_device_err;
	}

	ret = usb_add_bc1p2_init(&phy->phy, phy->is_dpdm_parallel);
	if (ret) {
		dev_err(dev, "fail to add bc1p2\n");
		goto  platform_device_err;
	}

	ret = sysfs_create_groups(&dev->kobj, usb_hsphy_groups);
	if (ret)
		dev_warn(dev, "failed to create usb hsphy attributes\n");

	dev_info(dev, "sprd usb phy probe ok !\n");

platform_device_err:
//	of_dev_put(regmap_pdev);
device_node_err:
	of_node_put(regmap_np);

	return ret;
}

static void sprd_hsphy_remove(struct platform_device *pdev)
{
	struct sprd_hsphy *phy = platform_get_drvdata(pdev);

	sysfs_remove_groups(&pdev->dev.kobj, usb_hsphy_groups);
	usb_remove_phy(&phy->phy);
	if (regulator_is_enabled(phy->vdd))
		regulator_disable(phy->vdd);

}

static void sprd_hsphy_drshutdown(struct platform_device *pdev)
{
}

static const struct of_device_id sprd_hsphy_match[] = {
	{ .compatible = "sprd,sc9832e-phy", .data = &phy_cfg_1,},
	{ .compatible = "sprd,sc9863a-phy", .data = &phy_cfg_2,},
	{ .compatible = "sprd,ums512-phy", .data = &phy_cfg_3,},
	{ .compatible = "sprd,ums9230-phy", .data = &phy_cfg_4,},
	{ .compatible = "sprd,ums9621-phy", .data = &phy_cfg_5,},
	{ .compatible = "sprd,uis8520-phy", .data = &phy_cfg_6,},
	{ .compatible = "sprd,ums9632-phy", .data = &phy_cfg_7,},
	{ .compatible = "sprd,ums9360-phy", .data = &phy_cfg_7,},
	{},
};
MODULE_DEVICE_TABLE(of, sprd_hsphy_match);

static struct platform_driver sprd_hsphy_driver = {
	.probe = sprd_hsphy_probe,
	.remove = sprd_hsphy_remove,
	.shutdown = sprd_hsphy_drshutdown,
	.driver = {
		.name = "sprd-hsphy",
		.of_match_table = sprd_hsphy_match,
	},
};

static int __init sprd_hsphy_driver_init(void)
{
	return platform_driver_register(&sprd_hsphy_driver);
}

static void __exit sprd_hsphy_driver_exit(void)
{
	platform_driver_unregister(&sprd_hsphy_driver);
}

late_initcall(sprd_hsphy_driver_init);
module_exit(sprd_hsphy_driver_exit);

MODULE_DESCRIPTION("UNISOC USB High Speed PHY Driver");
MODULE_LICENSE("GPL");
