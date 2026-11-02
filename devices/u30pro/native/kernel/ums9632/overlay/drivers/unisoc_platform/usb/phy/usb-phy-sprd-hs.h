/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Unisoc usb high speed phy driver
 *
 * Copyright (C) 2023 Unisoc, Inc.
 * Author: Pu Li <pu.li@unisoc.com>
 * Author: Xiongpeng Wu <xiongpeng.wu@unisoc.com>
 */

#ifndef __USB_PHY_SPRD_HS_H
#define __USB_PHY_SPRD_HS_H

#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/io.h>
#include <linux/iio/consumer.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>
#include <linux/reset.h>
#include <linux/slab.h>
#include <linux/soc/sprd/sprd_usbpinmux.h>
#include <linux/timer.h>
#include <linux/usb/of.h>
#include <linux/usb/phy.h>
#include <linux/usb/sprd_usbm.h>
#include <linux/power/sprd-bc1p2.h>
#include <linux/usb/otg.h>
#include <uapi/linux/usb/charger.h>


/* dts efuse-offset-val elems offset*/
#define EFUSE_CALI_NUM			3
enum sprd_phy_efuse_offset {
	EFUSE_TFHSRES = 0,
	EFUSE_TFREGRES,
	EFUSE_TUNEOTG,
};

struct phy_reg_info {
	struct regmap           *regmap_ptr;
	u32                     args[2];
};

struct sprd_phy_context_registers {
	u32 power_ctrl;
	u32 sel_cfg0;
	u32 utmi_ctl1;
	u32 utmi_ctl2;
};

struct sprd_hsphy {
	struct device		*dev;
	struct usb_phy		phy;
	void __iomem		*base;
	struct regulator	*vdd;
	struct regmap           *aon_apb;
	struct regmap           *ap_ahb;
	struct regmap           *analog;
	struct regmap           *pmic;
	struct reset_control *phy_rst;
	struct reset_control *utmi_rst;
	struct reset_control *otg_rst;
	struct clk	*otg_ref_eb;
	struct clk	*dphy_ref_eb;
	struct clk	*otg_phy_eb;
	struct phy_reg_info     refclk_cfg;
	u32			vdd_vol;
	u32			phy_tune;
	u32			host_eye_pattern;
	u32			device_eye_pattern;
	u32			efuse_trim;
	u32			algorithm_val;
	u32			host_otg_ctrl0;
	u32			device_otg_ctrl0;
	u32			host_otg_ctrl1;
	u32			device_otg_ctrl1;
	u32			*efuse_offset;
	atomic_t		inited;
	bool			is_host;
	bool			avdd1v8_chipsleep_off;
	struct regulator	*avdd1v8;
	struct regulator	*avdd1v2;
	struct regulator	*avddmodem;
	struct regulator	*vddmm;
	enum usb_phy_interface type;
	struct sprd_phy_context_registers context;
	bool		is_dpdm_parallel;
	struct mutex	hsphy_mutex;
};

enum hsphy_parameters {
	TUNEHSAMP_SHIFT,
	TUNEEQ_SHIFT,
	TFREGRES_SHIFT,
	TUNEOTG_SHIFT,
	TUNEDEC_SHIFT,
	TFHSRES_SHIFT,
	FULLSPEED_USB33_TUNE,
};

enum sprd_hsphy_reg_layout {
	REG_AON_APB_OTG_PHY_TEST,
	REG_AON_APB_OTG_PHY_CTRL,
	REG_AON_APB_PWR_CTRL,
	REG_AON_APB_AON_SOC_USB_CTRL,
	REG_AON_APB_MIPI_CSI_POWER_CTRL,
	REG_AP_AHB_OTG_CTRL0,
	REG_AP_AHB_OTG_CTRL1,
	REG_AP_AHB_OTG_PHY_CTRL,
	REG_AP_AHB_OTG_PHY_TUNE,
	REG_AP_AHB_OTG_PHY_TEST,
	REG_ANALOG_USB20_USB20_ISO_SW,
	REG_ANALOG_USB20_USB20_BATTER_PLL,
	REG_ANALOG_USB20_USB20_UTMI_CTL1,
	REG_ANALOG_USB20_USB20_TRIMMING,
	REG_ANALOG_USB20_USB20_UTMI_CTL2,
	REG_ANALOG_USB20_REG_SEL_CFG_0,
	REG_ANALOG_USB20_IDDG,
	REG_ANALOG_USB20_USB20_PHY,
};

enum sprd_hsphy_mask_layout {
	MASK_AON_APB_USB_PHY_PD_S,
	MASK_AON_APB_USB_PHY_PD_L,
	MASK_AON_APB_USB_ISO_SW_EN,
	MASK_AON_APB_OTG_VBUS_VALID_PHYREG,
	MASK_AON_APB_USB2_PHY_IDDIG,
	MASK_AON_APB_UTMI_WIDTH_SEL,
	MASK_AON_APB_USB20_CTRL_MUX_REG,
	MASK_AON_APB_USB20_ISO_SW_EN,
	MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_S,
	MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_L,
	MASK_AP_AHB_USB2_PHY_IDDIG,
	MASK_AP_AHB_OTG_DPPULLDOWN,
	MASK_AP_AHB_OTG_DMPULLDOWN,
	MASK_AP_AHB_OTG_VBUS_VALID_EXT,
	MASK_AP_AHB_OTG_VBUS_VALID_PHYREG,
	MASK_AP_AHB_UTMI_WIDTH_SEL,
	MASK_AP_AHB_USB2_DATABUS16_8,
	MASK_AP_AHB_USB20_SAMPLER_SEL,
	MASK_AP_AHB_USB20_TUNEHSAMP,
	MASK_AP_AHB_USB20_TUNEEQ,
	MASK_AP_AHB_USB20_TFREGRES,
	MASK_AP_AHB_USB20_RESERVED,
	MASK_ANALOG_USB20_USB20_VBUSVLDEXT,
	MASK_ANALOG_USB20_USB20_DATABUS16_8,
	MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN,
	MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN,
	MASK_ANALOG_USB20_USB20_DMPULLDOWN,
	MASK_ANALOG_USB20_USB20_DPPULLDOWN,
	MASK_ANALOG_USB20_UTMIOTG_IDDG,
	MASK_ANALOG_USB20_USB20_PS_PD_S,
	MASK_ANALOG_USB20_USB20_PS_PD_L,
	MASK_ANALOG_USB20_USB20_RESERVED,
	MASK_ANALOG_USB20_USB20_ISO_SW_EN,
	MASK_ANALOG_USB20_USB20_TUNEHSAMP,
	MASK_ANALOG_USB20_USB20_TUNEEQ,
	MASK_ANALOG_USB20_USB20_TFREGRES,
	MASK_ANALOG_USB20_USB20_TUNEOTG,
	MASK_ANALOG_USB20_USB20_TFHSRES,
	MASK_ANALOG_USB20_USB20_TUNEDEC,
};

struct sprd_hsphy_cfg_ops {
	void (*usb_enable_ctrl)(struct sprd_hsphy *phy, int on);
	void (*usb_phy_power_ctrl)(struct sprd_hsphy *phy, int on);
	void (*usb_vbus_ctrl)(struct sprd_hsphy *phy, int on);
	void (*utmi_width_sel)(struct sprd_hsphy *phy);
	void (*reset_assert)(struct sprd_hsphy *phy);
	void (*reset_deassert)(struct sprd_hsphy *phy);
	int (*set_mode)(struct sprd_hsphy *phy, int on);
	void (*set_pmic_pd)(struct sprd_hsphy *phy, int on);
};

enum hsphy_ip_version {
	VERSION1,
	VERSION2,
	VERSION3,
	VERSION4,
};


struct sprd_hsphy_cfg {
	/* array of registers with different offsets */
	const unsigned int *regs;

	const unsigned int *masks;

	/* private ops for each SOC */
	const struct sprd_hsphy_cfg_ops *cfg_ops;

	const unsigned int *parameters;

	enum hsphy_ip_version phy_version;

};

/*sc9832e&sc7731e*/
static const unsigned int phy_regs_layout_1[] = {
	[REG_AON_APB_PWR_CTRL]				= 0x0024,
	[REG_AP_AHB_OTG_CTRL0]				= 0x302c,
	[REG_AP_AHB_OTG_CTRL1]				= 0x3030,
	[REG_AP_AHB_OTG_PHY_CTRL]			= 0x3028,
	[REG_AP_AHB_OTG_PHY_TUNE]			= 0x3020,
	[REG_AP_AHB_OTG_PHY_TEST]			= 0x3024,
};

/*sc9863a*/
static const unsigned int phy_regs_layout_2[] = {
	[REG_AON_APB_PWR_CTRL]				= 0x0024,
	[REG_AP_AHB_OTG_PHY_CTRL]			= 0x3028,
	[REG_AP_AHB_OTG_PHY_TEST]			= 0x3024,
	[REG_ANALOG_USB20_USB20_UTMI_CTL1]		= 0x009c,
	[REG_ANALOG_USB20_USB20_TRIMMING]		= 0x00a8,
	[REG_ANALOG_USB20_USB20_UTMI_CTL2]		= 0x00a4,
	[REG_ANALOG_USB20_REG_SEL_CFG_0]		= 0x00b0,
	[REG_ANALOG_USB20_IDDG]				= 0x00bc,
};

/*ums312&ums512*/
static const unsigned int phy_regs_layout_3[] = {
	[REG_AON_APB_OTG_PHY_TEST]			= 0x0204,
	[REG_AON_APB_OTG_PHY_CTRL]			= 0x0208,
	[REG_ANALOG_USB20_USB20_ISO_SW]			= 0x0070,
	[REG_ANALOG_USB20_USB20_BATTER_PLL]		= 0x005c,
	[REG_ANALOG_USB20_USB20_UTMI_CTL1]		= 0x0058,
	[REG_ANALOG_USB20_USB20_TRIMMING]		= 0x0064,
	[REG_ANALOG_USB20_USB20_UTMI_CTL2]		= 0x0060,
	[REG_ANALOG_USB20_REG_SEL_CFG_0]		= 0x0074,
};

/*ums9230*/
static const unsigned int phy_regs_layout_4[] = {
	[REG_AON_APB_OTG_PHY_TEST]			= 0x0204,
	[REG_AON_APB_OTG_PHY_CTRL]			= 0x0208,
	[REG_ANALOG_USB20_USB20_ISO_SW]			= 0x001c,
	[REG_ANALOG_USB20_USB20_BATTER_PLL]		= 0x0008,
	[REG_ANALOG_USB20_USB20_UTMI_CTL1]		= 0x0004,
	[REG_ANALOG_USB20_USB20_TRIMMING]		= 0x0010,
	[REG_ANALOG_USB20_USB20_UTMI_CTL2]		= 0x000c,
	[REG_ANALOG_USB20_REG_SEL_CFG_0]		= 0x0020,
};

/*ums9620&ums9621*/
static const unsigned int phy_regs_layout_5[] = {
	[REG_AON_APB_OTG_PHY_TEST]			= 0x0204,
	[REG_AON_APB_OTG_PHY_CTRL]			= 0x0208,
	[REG_AON_APB_AON_SOC_USB_CTRL]			= 0x0190,
	[REG_AON_APB_MIPI_CSI_POWER_CTRL]		= 0x0350,
	[REG_ANALOG_USB20_USB20_UTMI_CTL1]		= 0x0004,
	[REG_ANALOG_USB20_USB20_TRIMMING]		= 0x0010,
	[REG_ANALOG_USB20_USB20_UTMI_CTL2]		= 0x000c,
	[REG_ANALOG_USB20_REG_SEL_CFG_0]		= 0x0020,
	[REG_ANALOG_USB20_USB20_PHY]			= 0x001C,
};

/*uis8520*/
static const unsigned int phy_regs_layout_6[] = {
	[REG_AON_APB_OTG_PHY_TEST]			= 0x0204,
	[REG_AON_APB_OTG_PHY_CTRL]			= 0x0208,
	[REG_AON_APB_AON_SOC_USB_CTRL]			= 0x0190,
	[REG_AON_APB_MIPI_CSI_POWER_CTRL]		= 0x0350,
	[REG_ANALOG_USB20_USB20_UTMI_CTL1]		= 0x0008,
	[REG_ANALOG_USB20_USB20_TRIMMING]		= 0x0018,
	[REG_ANALOG_USB20_USB20_UTMI_CTL2]		= 0x0010,
	[REG_ANALOG_USB20_REG_SEL_CFG_0]		= 0x0028,
	[REG_ANALOG_USB20_USB20_PHY]			= 0x0024,
};

/*ums9632 & ums9360*/
/*REG_ANA_SLP_AVDD18_LDO_PD_CTRL:REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL19,avddav8*/
/*REG_ANA_SLP_AVDD12_LDO_PD_CTRL:REG_UMP9650_PMU_CTRL_SLP_SEQ_CTRL16,avdd1v2_2*/
/*REG_ANA_SLP_AVDDMODEM_LDO_PD_CTRL:REG_UMP9651_PMU_CTRL_SLP_SEQ_CTRL4,avddmodem2*/
static const unsigned int phy_regs_layout_7[] = {
	[REG_AON_APB_OTG_PHY_TEST]			= 0x0204,
	[REG_AON_APB_OTG_PHY_CTRL]			= 0x0208,
	[REG_AON_APB_AON_SOC_USB_CTRL]			= 0x0190,
	[REG_AON_APB_MIPI_CSI_POWER_CTRL]		= 0x0350,
	[REG_ANALOG_USB20_USB20_UTMI_CTL1]		= 0x0004,
	[REG_ANALOG_USB20_USB20_TRIMMING]		= 0x0010,
	[REG_ANALOG_USB20_USB20_UTMI_CTL2]		= 0x000c,
	[REG_ANALOG_USB20_REG_SEL_CFG_0]		= 0x0020,
	[REG_ANALOG_USB20_USB20_PHY]			= 0x0018,
};


/*sc9832e&sc7731e*/
static const unsigned int phy_masks_layout_1[] = {
	[MASK_AON_APB_USB_PHY_PD_S]			= 0x20000,
	[MASK_AON_APB_USB_PHY_PD_L]			= 0x10000,
	[MASK_AP_AHB_USB2_PHY_IDDIG]			= 0x8,
	[MASK_AP_AHB_OTG_DPPULLDOWN]			= 0x100000,
	[MASK_AP_AHB_OTG_DMPULLDOWN]			= 0x200000,
	[MASK_AP_AHB_OTG_VBUS_VALID_EXT]		= 0x400000,
	[MASK_AP_AHB_OTG_VBUS_VALID_PHYREG]		= 0x1000000,
	[MASK_AP_AHB_UTMI_WIDTH_SEL]			= 0x40000000,
	[MASK_AP_AHB_USB2_DATABUS16_8]			= 0x20000000,
	[MASK_AP_AHB_USB20_SAMPLER_SEL]			= 0x100000,
	[MASK_AP_AHB_USB20_TUNEHSAMP]			= 0xc0000000,
	[MASK_AP_AHB_USB20_TUNEEQ]			= 0x7,
	[MASK_AP_AHB_USB20_TFREGRES]			= 0x3f00,
};

/*sc9863a*/
static const unsigned int phy_masks_layout_2[] = {
	[MASK_AON_APB_USB_PHY_PD_S]			= 0x20000,
	[MASK_AON_APB_USB_PHY_PD_L]			= 0x10000,
	[MASK_AON_APB_USB_ISO_SW_EN]			= 0x10000000,
	[MASK_AP_AHB_OTG_VBUS_VALID_PHYREG]		= 0x10000000,
	[MASK_AP_AHB_UTMI_WIDTH_SEL]			= 0x40000000,
	[MASK_ANALOG_USB20_USB20_VBUSVLDEXT]		= 0x10000,
	[MASK_ANALOG_USB20_USB20_DATABUS16_8]		= 0x10000000,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN]	= 0x1,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN]	= 0x2,
	[MASK_ANALOG_USB20_USB20_DMPULLDOWN]		= 0x4,
	[MASK_ANALOG_USB20_USB20_DPPULLDOWN]		= 0x8,
	[MASK_ANALOG_USB20_UTMIOTG_IDDG]		= 0x1,
	[MASK_ANALOG_USB20_USB20_TUNEHSAMP]		= 0x18000000,
	[MASK_ANALOG_USB20_USB20_TUNEEQ]		= 0x1c,
	[MASK_ANALOG_USB20_USB20_TFREGRES]		= 0x7e00000,
};

/*ums312&ums512*/
static const unsigned int phy_masks_layout_3[] = {
	[MASK_AON_APB_OTG_VBUS_VALID_PHYREG]		= 0x1000000,
	[MASK_AON_APB_UTMI_WIDTH_SEL]			= 0x40000000,
	[MASK_AON_APB_USB2_PHY_IDDIG]			= 0x8,
	[MASK_ANALOG_USB20_USB20_VBUSVLDEXT]		= 0x10000,
	[MASK_ANALOG_USB20_USB20_DATABUS16_8]		= 0x10000000,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN]	= 0x2,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN]	= 0x4,
	[MASK_ANALOG_USB20_USB20_DMPULLDOWN]		= 0x8,
	[MASK_ANALOG_USB20_USB20_DPPULLDOWN]		= 0x10,
	[MASK_ANALOG_USB20_USB20_PS_PD_S]		= 0x10,
	[MASK_ANALOG_USB20_USB20_PS_PD_L]		= 0x8,
	[MASK_ANALOG_USB20_USB20_RESERVED]		= 0xffff,
	[MASK_ANALOG_USB20_USB20_ISO_SW_EN]		= 0x1,
	[MASK_ANALOG_USB20_USB20_TUNEHSAMP]		= 0x06000000,
	[MASK_ANALOG_USB20_USB20_TUNEEQ]		= 0x7,
	[MASK_ANALOG_USB20_USB20_TFREGRES]		= 0x01f80000,
};

/*ums9230*/
static const unsigned int phy_masks_layout_4[] = {
	[MASK_AON_APB_OTG_VBUS_VALID_PHYREG]		= 0x1000000,
	[MASK_AON_APB_UTMI_WIDTH_SEL]			= 0x40000000,
	[MASK_AON_APB_USB2_PHY_IDDIG]			= 0x8,
	[MASK_ANALOG_USB20_USB20_VBUSVLDEXT]		= 0x10000,
	[MASK_ANALOG_USB20_USB20_DATABUS16_8]		= 0x10000000,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN]	= 0x2,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN]	= 0x4,
	[MASK_ANALOG_USB20_USB20_DMPULLDOWN]		= 0x8,
	[MASK_ANALOG_USB20_USB20_DPPULLDOWN]		= 0x10,
	[MASK_ANALOG_USB20_USB20_PS_PD_S]		= 0x10,
	[MASK_ANALOG_USB20_USB20_PS_PD_L]		= 0x8,
	[MASK_ANALOG_USB20_USB20_RESERVED]		= 0xffff,
	[MASK_ANALOG_USB20_USB20_ISO_SW_EN]		= 0x1,
	[MASK_ANALOG_USB20_USB20_TUNEHSAMP]		= 0x06000000,
	[MASK_ANALOG_USB20_USB20_TUNEEQ]		= 0x7,
	[MASK_ANALOG_USB20_USB20_TFREGRES]		= 0x01f80000,
};

/*ums9620&ums9621*/
static const unsigned int phy_masks_layout_5[] = {
	[MASK_AON_APB_OTG_VBUS_VALID_PHYREG]		= 0x1000000,
	[MASK_AON_APB_UTMI_WIDTH_SEL]			= 0x40000000,
	[MASK_AON_APB_USB2_PHY_IDDIG]			= 0x8,
	[MASK_AON_APB_USB20_CTRL_MUX_REG]		= 0x0008,
	[MASK_AON_APB_USB20_ISO_SW_EN]			= 0x0010,
	[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_S]	= 0x0002,
	[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_L]	= 0x0001,
	[MASK_ANALOG_USB20_USB20_VBUSVLDEXT]		= 0x10000,
	[MASK_ANALOG_USB20_USB20_DATABUS16_8]		= 0x10000000,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN]	= 0x4,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN]	= 0x8,
	[MASK_ANALOG_USB20_USB20_DMPULLDOWN]		= 0x8,
	[MASK_ANALOG_USB20_USB20_DPPULLDOWN]		= 0x10,
	[MASK_ANALOG_USB20_USB20_RESERVED]		= 0x0000FFFF,
	[MASK_ANALOG_USB20_USB20_ISO_SW_EN]		= 0x1,
	[MASK_ANALOG_USB20_USB20_TUNEHSAMP]		= 0x06000000,
	[MASK_ANALOG_USB20_USB20_TUNEEQ]		= 0x7,
	[MASK_ANALOG_USB20_USB20_TFREGRES]		= 0x01f80000,
};

/*uis8520*/
static const unsigned int phy_masks_layout_6[] = {
	[MASK_AON_APB_OTG_VBUS_VALID_PHYREG]		= 0x1000000,
	[MASK_AON_APB_UTMI_WIDTH_SEL]			= 0x40000000,
	[MASK_AON_APB_USB2_PHY_IDDIG]			= 0x8,
	[MASK_AON_APB_USB20_CTRL_MUX_REG]		= 0x0008,
	[MASK_AON_APB_USB20_ISO_SW_EN]			= 0x0010,
	[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_S]	= 0x0002,
	[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_L]	= 0x0001,
	[MASK_ANALOG_USB20_USB20_VBUSVLDEXT]		= 0x10000,
	[MASK_ANALOG_USB20_USB20_DATABUS16_8]		= 0x10000000,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN]	= 0x4,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN]	= 0x8,
	[MASK_ANALOG_USB20_USB20_DMPULLDOWN]		= 0x8,
	[MASK_ANALOG_USB20_USB20_DPPULLDOWN]		= 0x10,
	[MASK_ANALOG_USB20_USB20_RESERVED]		= 0x0000FFFF,
	[MASK_ANALOG_USB20_USB20_ISO_SW_EN]		= 0x1,
	[MASK_ANALOG_USB20_USB20_TUNEHSAMP]		= 0xC0,
	[MASK_ANALOG_USB20_USB20_TUNEEQ]		= 0x7,
	[MASK_ANALOG_USB20_USB20_TFREGRES]		= 0x1F,
};

/*ums9632 & ums9360*/
static const unsigned int phy_masks_layout_7[] = {
	[MASK_AON_APB_OTG_VBUS_VALID_PHYREG]		= 0x1000000,
	[MASK_AON_APB_UTMI_WIDTH_SEL]			= 0x40000000,
	[MASK_AON_APB_USB2_PHY_IDDIG]			= 0x8,
	[MASK_AON_APB_USB20_CTRL_MUX_REG]		= 0x0008,
	[MASK_AON_APB_USB20_ISO_SW_EN]			= 0x0010,
	[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_S]	= 0x0002,
	[MASK_AON_APB_C2G_ANALOG_USB20_USB20_PS_PD_L]	= 0x0001,
	[MASK_ANALOG_USB20_USB20_VBUSVLDEXT]		= 0x10000,
	[MASK_ANALOG_USB20_USB20_DATABUS16_8]		= 0x10000000,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN]	= 0x80,
	[MASK_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN]	= 0x100,
	[MASK_ANALOG_USB20_USB20_DMPULLDOWN]		= 0x8,
	[MASK_ANALOG_USB20_USB20_DPPULLDOWN]		= 0x10,
	[MASK_ANALOG_USB20_USB20_RESERVED]		= 0x0000FFFF,
	[MASK_ANALOG_USB20_USB20_ISO_SW_EN]		= 0x1,
	[MASK_ANALOG_USB20_USB20_TUNEHSAMP]		= 0x06000000,
	[MASK_ANALOG_USB20_USB20_TUNEEQ]		= 0x7,
	[MASK_ANALOG_USB20_USB20_TFREGRES]		= 0x01f80000,
	[MASK_ANALOG_USB20_USB20_TUNEOTG]		= 0xE00,
	[MASK_ANALOG_USB20_USB20_TUNEDEC]		= 0x180,
	[MASK_ANALOG_USB20_USB20_TFHSRES]		= 0x7C000,
};

/*sc9832e&sc7731e*/
static const unsigned int phy_parameters_1[] = {
	[TUNEHSAMP_SHIFT]		= 30,
	[TUNEEQ_SHIFT]			= 0,
	[TFREGRES_SHIFT]		= 8,
	[FULLSPEED_USB33_TUNE]		= 2700000,
};

/*sc9863a*/
static const unsigned int phy_parameters_2[] = {
	[TUNEHSAMP_SHIFT]		= 27,
	[TUNEEQ_SHIFT]			= 2,
	[TFREGRES_SHIFT]		= 21,
	[FULLSPEED_USB33_TUNE]		= 2700000,
};

/*ums312&ums512*/
static const unsigned int phy_parameters_3[] = {
	[TUNEHSAMP_SHIFT]		= 25,
	[TUNEEQ_SHIFT]			= 0,
	[TFREGRES_SHIFT]		= 19,
	[FULLSPEED_USB33_TUNE]		= 2700000,
};

/*ums9230*/
static const unsigned int phy_parameters_4[] = {
	[TUNEHSAMP_SHIFT]		= 25,
	[TUNEEQ_SHIFT]			= 0,
	[TFREGRES_SHIFT]		= 19,
	[FULLSPEED_USB33_TUNE]		= 2700000,
};

/*ums9620&ums9621*/
static const unsigned int phy_parameters_5[] = {
	[TUNEHSAMP_SHIFT]		= 25,
	[TUNEEQ_SHIFT]			= 0,
	[TFREGRES_SHIFT]		= 19,
	[FULLSPEED_USB33_TUNE]		= 3300000,
};

/*uis8520*/
static const unsigned int phy_parameters_6[] = {
	[TUNEHSAMP_SHIFT]		= 6,
	[TUNEEQ_SHIFT]			= 0,
	[TFREGRES_SHIFT]		= 0,
	[FULLSPEED_USB33_TUNE]		= 3300000,
};

/*ums9632 & ums9360*/
static const unsigned int phy_parameters_7[] = {
	[TUNEHSAMP_SHIFT]		= 25,
	[TUNEEQ_SHIFT]			= 0,
	[TFREGRES_SHIFT]		= 19,
	[TUNEOTG_SHIFT]			= 9,
	[TUNEDEC_SHIFT]			= 7,
	[TFHSRES_SHIFT]			= 14,
	#ifdef CONFIG_ZTE_T780_USB_CTL_VDDUSB33_V
	[FULLSPEED_USB33_TUNE]		= 3300000,
	#else
	[FULLSPEED_USB33_TUNE]		= 2700000,
	#endif
	
};


#endif

