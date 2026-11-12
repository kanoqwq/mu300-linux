// SPDX-License-Identifier: GPL-2.0-only
/*
 * Unisoc UMS9620 (qogirn6pro) USB 3.1 / USB 2.0 combo PHY for DWC3, device mode.
 * Ported from the Unisoc 5.4 kernel (drivers/usb/phy/phy-sprd-usb31-qogirn6pro.c).
 * Battery boards retain the PMIC BC1.2 -> USB data-line handoff. Boards without
 * the battery policy retain their existing direct USB path.
 *
 * Copyright (C) 2020 Spreadtrum Communications Inc.
 */
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/extcon.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/kernel.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/power/ump9620-battery.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/usb/otg.h>
#include <linux/usb/phy.h>
#include <linux/usb/ums9620-ssphy.h>
#include <linux/workqueue.h>
#include <dt-bindings/soc/sprd,qogirn6pro-mask.h>
#include <dt-bindings/soc/sprd,qogirn6pro-regs.h>

struct sprd_ssphy {
	bool orientation_error;
	bool role_sync_pending;
	struct device		*dev;
	struct usb_phy		phy;
	void __iomem		*base;
	struct regmap		*aon_apb;
	struct regmap		*ipa_apb;
	struct regmap		*ipa_dispc1_glb_apb;
	struct regmap		*ipa_usb31_dp;
	struct regmap		*ipa_usb31_dptx;
	struct regmap		*ana_g0l;
	struct regmap		*pmic;
	struct regulator	*vdd;
	u32			vdd_vol;
	u32			host_eye_pattern;
	u32			device_eye_pattern;
	u32			ssphy_ctl5_eye_pattern;
	u32			ssphy_ctl6_eye_pattern;
	atomic_t		reset;
	atomic_t		inited;
	bool			is_host;
	bool			bc12_enabled, bc12_ready, bc12_stopping;
	bool			vbus_present;
	atomic_t		bc12_generation;
	u32			bc12_seen;
	unsigned int		bc12_samples;
	bool			bc12_retry;
	struct mutex		dpdm_lock;
	spinlock_t		bc12_event_lock;
	struct delayed_work	bc12_work;
};

#define PHY_INIT_TIMEOUT 500

#define DISPC1_GLB_APB_EB		 (0x0)
#define DISPC1_GLB_APB_RST		 (0x4)

#define PHY0_SRAM_BYPASS		(0X48)
#define PHY_SRAM_INIT_CHECK_DONE	(0x74)
#define TYPEC_DISABLE_ACK		(0xc08)

#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVALID       0x02000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TESTCLK         0x01000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TESTDATAIN      0xff0000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TESTADDR        0xf000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TESTDATAOUTSEL  0x800
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TESTDATAOUT     0x380
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BIST_MODE       0x7c
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_T2RCOMP         0x2
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_LPBK_END        0x1
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DATABUS16_8     0x10000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_SUSPENDM        0x08000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_PORN            0x04000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_RESET           0x02000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_RXERROR         0x01000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BYPASS_DRV_DP   0x00800000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BYPASS_DRV_DM   0x00400000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BYPASS_FS       0x00200000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BYPASS_IN_DP    0x00100000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BYPASS_IN_DM    0x80000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BYPASS_OUT_DP   0x40000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BYPASS_OUT_DM   0x20000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVLDEXT      0x10000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_RESERVED        0x0000FFFF
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_REXTENABLE      0x4
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DMPULLUP        0x2
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_SAMPLER_SEL     0x1
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DPPULLDOWN      0x10
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DMPULLDOWN      0x8
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TXBITSTUFFENABLE 0x4
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TXBITSTUFFENABLEH 0x2
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_SLEEPM          0x1
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TUNEHSAMP       0x06000000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TFREGRES        0x01f80000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TFHSRES         0x7c000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TUNERISE        0x3000
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TUNEOTG         0xe00
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TUNEDSC         0x180
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TUNESQ          0x78
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TUNEEQ          0x7
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_TUNEPLLS        0x7800
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_PLL_PFD_DEADZONE 0x300
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_PLL_PFD_DELAY   0xc0
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_PLL_CP_IOFFSET_EN 0x20
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_PLL_CP_IOFFSET  0x1e
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_PLL_REF_DOUBLER_EN 0x1
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BISTRAM_EN      0x2
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_BIST_MODE_EN    0x1
#define BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_ISO_SW_EN       0x1
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_SUSPENDM 0x100
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_PORN    0x80
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_RESET   0x40
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_BYPASS_FS 0x20
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_BYPASS_IN_DM 0x10
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN 0x8
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN 0x4
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_SLEEPM 0x2
#define BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_ISO_SW_EN 0x1

#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_TEST_PIN        0x0000
#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1       0x0004
#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_BATTER_PLL      0x0008
#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL2       0x000C
#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_TRIMMING        0x0010
#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_PLL_CTRL        0x0014
#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_PHY_BIST_TEST   0x0018
#define REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_PHY             0x001C
#define REG_ANLG_PHY_G0L_ANALOG_USB20_REG_SEL_CFG_0         0x0020

#define DEFAULT_DEVICE_EYE_PATTERN			0x067bd1c0
#define DEFAULT_HOST_EYE_PATTERN			0x067bd1c0

#define CHGR_DET_FGU_CTRL		0x23a0
#define DP_DM_FS_ENB			BIT(14)
#define DP_DM_BC_ENB			BIT(0)

#define DEFAULT_SSPHY_CTL5_EYE_PATTERN			0x1e40a000
#define DEFAULT_SSPHY_CTL6_EYE_PATTERN			0x5d000083

static int sc27xx_dpdm_switch_to_phy(struct regmap *regmap, bool enable)
{
	/* Do not overwrite the detector delay fields in this shared register. */
	return regmap_update_bits(regmap, CHGR_DET_FGU_CTRL,
		DP_DM_FS_ENB | DP_DM_BC_ENB,
		enable ? DP_DM_FS_ENB | DP_DM_BC_ENB : 0);
}

static void sprd_ssphy_bc12_role(struct sprd_ssphy *phy);
static int sprd_ssphy_notify_connect(struct usb_phy *x, enum usb_device_speed speed);
static int sprd_ssphy_notify_disconnect(struct usb_phy *x, enum usb_device_speed speed);

static int sprd_ssphy_restore_data_state(struct sprd_ssphy *phy)
{
	enum usb_charger_type type;
	int ret = 0;

	if (!phy->bc12_enabled)
		return 0;
	mutex_lock(&phy->dpdm_lock);
	/* The detector can finish before PHY init, when notify_* has no live
	 * registers to update. Apply its current decision before returning the
	 * initialized PHY to DWC3; do not let init unconditionally reopen data.
	 * Host + external sink power remains independent of charger detection.
	 */
	if (!phy->is_host) {
		type = READ_ONCE(phy->phy.chg_type);
		if (READ_ONCE(phy->vbus_present) &&
		    (type == SDP_TYPE || type == CDP_TYPE))
			ret = sprd_ssphy_notify_connect(&phy->phy, USB_SPEED_UNKNOWN);
		else
			ret = sprd_ssphy_notify_disconnect(&phy->phy, USB_SPEED_UNKNOWN);
	}
	mutex_unlock(&phy->dpdm_lock);
	return ret;
}


static inline void sprd_ssphy_reset_core(struct sprd_ssphy *phy)
{
	u32 reg, msk;
	int ret = 0;

	dev_dbg(phy->phy.dev, "%s ipa usb&phy rst!\n", __func__);

	/* Purpose: To soft-reset USB control */
	msk = MASK_IPA_APB_USB_SOFT_RST | MASK_IPA_APB_PAM_U3_SOFT_RST;
	reg = msk;
	regmap_update_bits(phy->ipa_apb, REG_IPA_APB_IPA_RST, msk, reg);

	/* Reset USB2 PHY */
	if (true) {
		msk = MASK_AON_APB_OTG_PHY_SOFT_RST | MASK_AON_APB_OTG_UTMI_SOFT_RST;
		reg = msk;
		regmap_update_bits(phy->aon_apb, REG_AON_APB_APB_RST1, msk, reg);
	}

	/* Reset USB31 PHY */
	ret |= regmap_read(phy->ipa_dispc1_glb_apb, DISPC1_GLB_APB_RST, &reg);
	reg |= BIT(4);
	ret |= regmap_write(phy->ipa_dispc1_glb_apb, DISPC1_GLB_APB_RST, reg);
	usleep_range(1, 10);
	/* ssphy power on @0x64900d14*/
	msk = MASK_AON_APB_PHY_TEST_POWERDOWN;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, 0);

	/*
	 *Reset signal should hold on for a while
	 *to issue resret process reliable.
	 */
	usleep_range(20000, 30000);
	msk = MASK_IPA_APB_USB_SOFT_RST | MASK_IPA_APB_PAM_U3_SOFT_RST;
	regmap_update_bits(phy->ipa_apb, REG_IPA_APB_IPA_RST, msk, 0);
	msk = MASK_AON_APB_OTG_PHY_SOFT_RST | MASK_AON_APB_OTG_UTMI_SOFT_RST;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_APB_RST1, msk, 0);
	ret |= regmap_read(phy->ipa_dispc1_glb_apb, DISPC1_GLB_APB_RST, &reg);
	reg &= ~BIT(4);
	ret |= regmap_write(phy->ipa_dispc1_glb_apb, DISPC1_GLB_APB_RST, reg);
}


#include "ums9620-tca.h"

bool ums9620_ssphy_orientation_changed(struct usb_phy *x)
{
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);
	u32 boot, flip, gcfg, direction, status, role;

	if (!atomic_read(&phy->inited))
		return false;
	if (regmap_read(phy->aon_apb, REG_AON_APB_BOOT_MODE, &boot))
		return false;
	flip = boot & BIT(10) ? 0 : BIT(2);
	gcfg = readl(phy->base + 0x10);
	direction = readl(phy->base +
		((gcfg & UMS9620_TCA_OP_MODE_MASK) == UMS9620_TCA_OP_MODE_SYSTEM ?
		 0x18 : 0x14));
	status = readl(phy->base + 0x34);
	role = phy->is_host ? 0 : UMS9620_TCA_DEVICE_ROLE;
	return phy->orientation_error || phy->role_sync_pending ||
		(gcfg & UMS9620_TCA_DEVICE_ROLE) != role ||
		(direction & (BIT(3) | BIT(2))) != flip ||
		(status & (BIT(3) | BIT(2))) != flip;
}
EXPORT_SYMBOL_GPL(ums9620_ssphy_orientation_changed);

bool ums9620_ssphy_host_resume_needed(struct usb_phy *x)
{
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);

	/* Stock battery-board glue sets host before resuming the combo PHY.
	 * Keep the existing direct path of boards without this policy.
	 */
	return phy->bc12_enabled;
}
EXPORT_SYMBOL_GPL(ums9620_ssphy_host_resume_needed);

static int sprd_ssphy_set_vbus(struct usb_phy *x, int on)
{
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);
	u32 reg, msk;
	int ret = 0;
	bool was_host;

	if (phy->bc12_enabled)
		mutex_lock(&phy->dpdm_lock);
	was_host = phy->is_host;
	if (on) {
		regmap_write(phy->ana_g0l, REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_TRIMMING,
					phy->host_eye_pattern);
		/* set USB connector type is A-type*/
		msk = MASK_AON_APB_USB2_PHY_IDDIG;
		ret |= regmap_update_bits(phy->aon_apb,
			REG_AON_APB_OTG_PHY_CTRL, msk, 0);

		msk = BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN |
			BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN;
		ret |= regmap_update_bits(phy->ana_g0l,
			REG_ANLG_PHY_G0L_ANALOG_USB20_REG_SEL_CFG_0,
			msk, msk);

		/* the pull down resistance on D-/D+ enable */
		msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DMPULLDOWN |
			BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DPPULLDOWN;
		ret |= regmap_update_bits(phy->ana_g0l,
			REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL2,
			msk, msk);

		reg = 0x200;
		msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_RESERVED;
		ret |= regmap_update_bits(phy->ana_g0l,
			REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1,
			msk, reg);
		/* BC1.2/detach may have invalidated both PHY inputs while we were
		 * a device. The stock host resume reruns phy_init(), restoring them.
		 * Mainline keeps the PHY initialized across DR swaps, so restore the
		 * data-path inputs here before xHCI starts (also for host + sink).
		 * These are internal valid signals, not the external VBUS regulator.
		 */
		ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_OTG_PHY_TEST,
				MASK_AON_APB_OTG_VBUS_VALID_PHYREG,
				MASK_AON_APB_OTG_VBUS_VALID_PHYREG);
		ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL,
				MASK_AON_APB_SYS_VBUSVALID, MASK_AON_APB_SYS_VBUSVALID);
		ret |= regmap_update_bits(phy->ana_g0l,
				REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1,
				BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVLDEXT,
				BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVLDEXT);
		phy->is_host = true;
	} else {
		regmap_write(phy->ana_g0l, REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_TRIMMING,
					phy->device_eye_pattern);

		if (true) {
			reg = msk = MASK_AON_APB_USB2_PHY_IDDIG;
			ret |= regmap_update_bits(phy->aon_apb,
				REG_AON_APB_OTG_PHY_CTRL, msk, reg);

			msk = BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_DMPULLDOWN |
				BIT_ANLG_PHY_G0L_DBG_SEL_ANALOG_USB20_USB20_DPPULLDOWN;
			ret |= regmap_update_bits(phy->ana_g0l,
				REG_ANLG_PHY_G0L_ANALOG_USB20_REG_SEL_CFG_0,
				msk, msk);

			/* the pull down resistance on D-/D+ enable */
			msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DMPULLDOWN |
				BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_DPPULLDOWN;
			ret |= regmap_update_bits(phy->ana_g0l,
				REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL2,
				msk, 0);

			msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_RESERVED;
			ret |= regmap_update_bits(phy->ana_g0l,
				REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1,
				msk, 0);
		}
		phy->is_host = false;
	}
	/* A role update can precede init. Preserve that transition until the
	 * live mux has been synchronized; phy_init setting matching register
	 * values is not evidence that the old data-role handoff took place.
	 */
	phy->role_sync_pending |= was_host != phy->is_host;

	if (phy->bc12_enabled) {
		if (was_host != phy->is_host)
			sprd_ssphy_bc12_role(phy);
		mutex_unlock(&phy->dpdm_lock);
	}
	/* DWC3 has stopped the previous link. Synchronize PHY data role and
	 * direction before the next link starts; never change VBUS policy here.
	 */
	if (!ret && atomic_read(&phy->inited)) {
		ret = sprd_ssphy_sync_orientation(phy, phy->role_sync_pending);
		if (!ret)
			phy->role_sync_pending = false;
	}
	return ret;
}


/* Requirement from usb analog compliance tests */
static void usb_config_4_cts(struct sprd_ssphy *phy)
{
	u32	reg;

	dev_dbg(phy->phy.dev, "%s enter\n", __func__);

	regmap_read(phy->ipa_usb31_dp, 0x20, &reg);
	/*enable cr apb*/
	reg |= BIT(4);
	regmap_write(phy->ipa_usb31_dp, 0x20, reg);
	/*enable cr clk*/
	reg |= BIT(3);
	regmap_write(phy->ipa_usb31_dp, 0x20, reg);

	/*phy reg addr 0x0021 SUP_DIG_LVL_OVER_IN*/
	reg = 0x10021;
	regmap_write(phy->ipa_usb31_dp, 0x8, reg);
	regmap_read(phy->ipa_usb31_dp, 0xc, &reg);
	/*bit7: TX_VBOOST_LVL_EN; bit6L4=5 TX_TXVBOOST_LVL*/
	reg &= ~(BIT(6) | BIT(5) | BIT(4));
	reg |= BIT(7) | BIT(6) | BIT(4);
	regmap_write(phy->ipa_usb31_dp, 0xc, reg);

	/*cfg for enter gen2*/
	reg = 0x10063;
	regmap_write(phy->ipa_usb31_dp, 0x8, reg);
	regmap_read(phy->ipa_usb31_dp, 0xc, &reg);
	reg |= BIT(8) | BIT(7) | BIT(1) | BIT(0);
	regmap_write(phy->ipa_usb31_dp, 0xc, reg);

	/* Configure usb31 phy eye-pattern */
	regmap_write(phy->ipa_usb31_dp, 0x44, phy->ssphy_ctl5_eye_pattern);
	regmap_write(phy->ipa_usb31_dp, 0x48, phy->ssphy_ctl6_eye_pattern);
}


static int sprd_ssphy_init(struct usb_phy *x)
{
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);
	u32	reg, msk;
	int	ret = 0;
	int timeout;

	if (atomic_read(&phy->inited)) {
		dev_info(x->dev, "%s is already inited!\n", __func__);
		return 0;
	}

	
	/*
	 * Due to chip design, some chips may turn on vddusb by default,
	 * We MUST avoid turning it on twice.
	 */
	if (phy->vdd) {
		ret = regulator_enable(phy->vdd);
		if (ret) {
			dev_err(x->dev,
				"Failed to enable phy->vdd: %d\n", ret);
			return ret;
		}
	}

	
	/* select the IPA_SYS USB controller */
	msk = MASK_AON_APB_USB20_CTRL_MUX_REG;
	reg = msk;
	ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_AON_SOC_USB_CTRL,
			 msk, reg);

	/* enable analog */
	msk = MASK_AON_APB_CGM_OTG_REF_EN | MASK_AON_APB_CGM_DPHY_REF_EN;
	reg = msk;
	ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_CGM_REG1, msk, reg);

	/*enable analog:0x64900004*/
	reg = MASK_AON_APB_AON_USB2_TOP_EB | MASK_AON_APB_OTG_PHY_EB |
						MASK_AON_APB_ANA_EB;
	msk = reg;
	ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_APB_EB1, msk, reg);

	/* utmisrp_bvalid  sys vbus valid:0x64900D14*/
	reg = MASK_AON_APB_SYS_VBUSVALID;
	msk = reg;
	ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, reg);

	/*  usb eb and usb ref eb :0x25000004*/
	ret |= regmap_read(phy->ipa_apb, REG_IPA_APB_IPA_EB, &reg);
	reg |= MASK_IPA_APB_USB_EB | MASK_IPA_APB_USB_REF_EB;
	ret |= regmap_write(phy->ipa_apb, REG_IPA_APB_IPA_EB, reg);

	/* usb suspend eb :0x64900138*/
	reg = MASK_AON_APB_CGM_USB_SUSPEND_EN;
	msk = reg;
	ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_CGM_REG1, msk, reg);

	/*
	 * USB2 PHY power on: set pd_l/pd_s firstly, then set iso_sw.
	 * poweroff sequence is opposite
	 */
	msk = MASK_AON_APB_LVDSRF_PD_PD_L | MASK_AON_APB_LVDSRF_PS_PD_S;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_MIPI_CSI_POWER_CTRL, msk, 0);

	ret |= regmap_update_bits(phy->ana_g0l,
		REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_PHY,
		BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_ISO_SW_EN, 0);

	/* enable usb20 ISO AVDD1V8_USB*/
	msk = MASK_AON_APB_USB20_ISO_SW_EN;
	ret |= regmap_update_bits(phy->aon_apb, REG_AON_APB_AON_SOC_USB_CTRL,
			 msk, 0);

	/* ssphy power on @0x64900d14*/
	msk = MASK_AON_APB_PHY_TEST_POWERDOWN;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, 0);

	/*hsphy vbus valid */
	msk = MASK_AON_APB_OTG_VBUS_VALID_PHYREG;
	reg = msk;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_OTG_PHY_TEST, msk, reg);
	/*ssphy vbus valid */
	msk = MASK_AON_APB_SYS_VBUSVALID;
	reg = msk;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, reg);

	msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVLDEXT;
	reg = msk;
	ret |= regmap_update_bits(phy->ana_g0l,
			REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1,	msk, reg);

	regmap_write(phy->ana_g0l, REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_TRIMMING,
					phy->device_eye_pattern);

	/* Reset PHY */
	sprd_ssphy_reset_core(phy);

	/* wait phy_sram init down check bit2=1 */
	timeout = PHY_INIT_TIMEOUT;
	while (1) {
		ret |= regmap_read(phy->ipa_usb31_dp, PHY_SRAM_INIT_CHECK_DONE, &reg);
		if ((reg & BIT(2)) == BIT(2)) {
			break;
		} else {
			msleep(1);
			if (!--timeout) {
				dev_err(x->dev, "%s phy sram init failure\n", __func__);
				break;
			}
		}
	}

	usb_config_4_cts(phy);

	/* REG_TYPEC_CTRL: TYPEC_DISABLE_ACK */
	ret |= regmap_read(phy->ipa_usb31_dptx, TYPEC_DISABLE_ACK, &reg);
	reg |= BIT(0);
	ret |= regmap_write(phy->ipa_usb31_dptx, TYPEC_DISABLE_ACK, reg);

	/* TCA GCFG
	bit1:0 => 2b01 op_mode = controller synced mode
	bit3:2 => 2b00 reserved
	bit4 = 1 => USB device mode
	Bit31:5 => 0x0
	*/
	reg = readl_relaxed(phy->base + 0x010);
	reg &= ~(0xffffffc0);
	writel_relaxed(reg, phy->base + 0x010);
	reg &= ~(BIT(0) | BIT(4));
	if (phy->is_host)
		reg |= BIT(0);
	else
		reg |= BIT(0) | BIT(4);
	writel_relaxed(reg, phy->base + 0x010);

	/* TCA CTRLSYNCMODE CFG0 */
	reg = readl_relaxed(phy->base + 0x020);
	reg |= BIT(1) | BIT(2);
	writel_relaxed(reg, phy->base + 0x020);

	/*  TCA CTRLSYNCMODE CFG1 */
	reg = readl_relaxed(phy->base + 0x024);
	reg = 0x3d090;
	writel_relaxed(reg, phy->base + 0x024);

	/* clear TCA int status */
	reg = readl_relaxed(phy->base + 0x08);
	reg = 0xffff;
	writel_relaxed(reg, phy->base + 0x08);

	/* TCA INT EN
	xa_ack_event_en =1
	xa_timeout_event_en=1
	*/
	reg = readl_relaxed(phy->base + 0x04);
	reg |= BIT(0) | BIT(1);
	writel_relaxed(reg, phy->base + 0x04);

	/* usb3 switch port */
	/* remove usbonly,add combophy for dp/usb */
	ret |= regmap_read(phy->aon_apb, REG_AON_APB_BOOT_MODE, &reg);
	msk = readl_relaxed(phy->base + 0x14);
	if ((reg & BIT(10))) {
		msk &= ~(BIT(2) | BIT(3));
		msk |= BIT(0) | BIT(1) | BIT(4);
	} else {
		msk &= ~BIT(3);
		msk |= BIT(0) | BIT(1) | BIT(2) | BIT(4);
	}
	writel_relaxed(msk, phy->base + 0x14);

	msk = readl_relaxed(phy->base + 0x18);
	if (reg & BIT(10))
		msk &= ~BIT(2);
	else
		msk |= BIT(2);
	writel_relaxed(msk, phy->base + 0x18);

	msleep(10);
	/* wait tca interrupt */
	timeout = PHY_INIT_TIMEOUT;
	while (1) {
		reg = readl_relaxed(phy->base + 0x08);
		if ((reg & BIT(0)) == BIT(0) || (reg & BIT(1)) == BIT(1)) {
			msk = 0xffff;
			writel_relaxed(msk, phy->base + 0x08);
			break;
		} else {
			msleep(1);
			if (!--timeout) {
				dev_err(x->dev, "%s tca interrupt not ready\n", __func__);
				break;
			}
		}
	}

	if (!phy->pmic) {
		/*
		 *In FPGA platform, Disable low power will take some time
		 *before the DWC3 Core register is accessible.
		 */
		usleep_range(1000, 2000);
	}

	atomic_set(&phy->inited, 1);
	ret |= sprd_ssphy_restore_data_state(phy);

	return ret;
}


/* Turn off PHY and core */
static void sprd_ssphy_shutdown(struct usb_phy *x)
{
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);
	u32 msk = 0, reg = 0;

	if (!atomic_read(&phy->inited)) {
		dev_dbg(x->dev, "%s is already shut down\n", __func__);
		return;
	}


		if (true) {
		msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_ISO_SW_EN;
		reg = msk;
		regmap_update_bits(phy->ana_g0l, REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_PHY,
			msk, reg);

		/*hsphy vbus invalid */
		msk = MASK_AON_APB_OTG_VBUS_VALID_PHYREG;
		regmap_update_bits(phy->aon_apb, REG_AON_APB_OTG_PHY_TEST, msk, 0);

		/* disable usb20 ISO*/
		msk = MASK_AON_APB_USB20_ISO_SW_EN;
		reg = msk;
		regmap_update_bits(phy->aon_apb, REG_AON_APB_AON_SOC_USB_CTRL,
				 msk, reg);

		/* hsphy power off */
		msk = MASK_AON_APB_LVDSRF_PD_PD_L | MASK_AON_APB_LVDSRF_PS_PD_S;
		reg = msk;
		regmap_update_bits(phy->aon_apb, REG_AON_APB_MIPI_CSI_POWER_CTRL, msk, reg);

		/* disable usb cgm ref */
		msk = MASK_AON_APB_CGM_OTG_REF_EN | MASK_AON_APB_CGM_DPHY_REF_EN;
		regmap_update_bits(phy->aon_apb, REG_AON_APB_CGM_REG1, msk, 0);

		/*disable analog:0x64900004*/
		msk = MASK_AON_APB_AON_USB2_TOP_EB | MASK_AON_APB_OTG_PHY_EB;
		regmap_update_bits(phy->aon_apb, REG_AON_APB_APB_EB1, msk, 0);
	}

	/*ssphy vbus invalid */
	msk = MASK_AON_APB_SYS_VBUSVALID;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, 0);

	/* Reset USB31 PHY */
	regmap_read(phy->ipa_dispc1_glb_apb, DISPC1_GLB_APB_RST, &reg);
	reg |= BIT(4);
	regmap_write(phy->ipa_dispc1_glb_apb, DISPC1_GLB_APB_RST, reg);

	usleep_range(1, 10);
	/* ssphy power off @0x64900d14*/
	msk = MASK_AON_APB_PHY_TEST_POWERDOWN;
	reg = msk;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, reg);

	/*
	 * Due to chip design, some chips may turn on vddusb by default,
	 * we MUST avoid turning it off twice.
	 */

	if (phy->vdd && regulator_is_enabled(phy->vdd))
		regulator_disable(phy->vdd);

	
	atomic_set(&phy->inited, 0);
	atomic_set(&phy->reset, 0);
}


static int sprd_ssphy_notify_connect(struct usb_phy *x,
				enum usb_device_speed speed)
{
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);
	u32 msk = 0, reg = 0;

	if (!atomic_read(&phy->inited)) {
		dev_info(x->dev, "%s phy is not inited!\n", __func__);
		return 0;
	}

	if (phy->is_host)
		return 0;

	/*hsphy vbus valid */
	msk = MASK_AON_APB_OTG_VBUS_VALID_PHYREG;
	reg = msk;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_OTG_PHY_TEST, msk, reg);
	/*ssphy vbus valid */
	msk = MASK_AON_APB_SYS_VBUSVALID;
	reg = msk;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, reg);

	msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVLDEXT;
	reg = msk;
	regmap_update_bits(phy->ana_g0l,
			REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1,	msk, reg);
	dev_info(x->dev, "ssphy set vbus valid!\n");
	return 0;
}

static int sprd_ssphy_notify_disconnect(struct usb_phy *x,
				enum usb_device_speed speed)
{
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);
	u32 msk = 0;

	if (!atomic_read(&phy->inited)) {
		dev_info(x->dev, "%s phy is not inited!\n", __func__);
		return 0;
	}

	if (phy->is_host)
		return 0;

	/*hsphy vbus invalid */
	msk = MASK_AON_APB_OTG_VBUS_VALID_PHYREG;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_OTG_PHY_TEST, msk, 0);
	/*ssphy vbus invalid */
	msk = MASK_AON_APB_SYS_VBUSVALID;
	regmap_update_bits(phy->aon_apb, REG_AON_APB_USB31DPCOMBPHY_CTRL, msk, 0);

	msk = BIT_ANLG_PHY_G0L_ANALOG_USB20_USB20_VBUSVLDEXT;
	regmap_update_bits(phy->ana_g0l,
			REG_ANLG_PHY_G0L_ANALOG_USB20_USB20_UTMI_CTL1,	msk, 0);
	dev_info(x->dev, "ssphy set vbus invalid!\n");
	return 0;
}


#include "ums9620-bc12.h"

/* Battery boards detect before data-line handoff, without sleeping in extcon. */
static int sprd_ssphy_vbus_notify(struct notifier_block *nb, unsigned long event, void *data)
{
	struct usb_phy *x = container_of(nb, struct usb_phy, vbus_nb);
	struct sprd_ssphy *phy = container_of(x, struct sprd_ssphy, phy);

	if (phy->bc12_enabled) {
		unsigned long flags;

		spin_lock_irqsave(&phy->bc12_event_lock, flags);
		WRITE_ONCE(phy->vbus_present, !!event);
		WRITE_ONCE(x->chg_type, UNKNOWN_TYPE);
		atomic_inc(&phy->bc12_generation);
		if (READ_ONCE(phy->bc12_ready) && !READ_ONCE(phy->bc12_stopping))
			mod_delayed_work(system_power_efficient_wq, &phy->bc12_work, 0);
		spin_unlock_irqrestore(&phy->bc12_event_lock, flags);
		return NOTIFY_OK;
	}

	dev_info(x->dev, "vbus %s\n", event ? "on" : "off");
	if (event)
		sprd_ssphy_notify_connect(x, USB_SPEED_UNKNOWN);
	else
		sprd_ssphy_notify_disconnect(x, USB_SPEED_UNKNOWN);
	return NOTIFY_OK;
}

static struct regmap *sprd_ssphy_pmic_regmap(struct device *dev)
{
	struct device_node *np = of_find_compatible_node(NULL, NULL, "sprd,ump962x-syscon");
	struct platform_device *pdev;
	struct regmap *map = NULL;

	if (!np)
		return NULL;
	pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (pdev && pdev->dev.parent)
		map = dev_get_regmap(pdev->dev.parent, NULL);
	if (pdev)
		put_device(&pdev->dev);
	return map;
}

/*
 * Where VBUS comes from. The F50's device tree gives the PHY the extcon-usb-gpio device; the U30 Air's gives it the
 * PMIC's Type-C block (sprd,sc27xx-typec), which has no driver here, so usb_add_phy_dev() waited for it for ever
 * and there was no USB at all (no network, no serial console). Both boards have the VBUS GPIO node: point the
 * property at it when it names the Type-C block. The property stays with the node, so it is never freed.
 */
static void sprd_ssphy_vbus_source(struct device *dev)
{
	struct device_node *np = dev->of_node, *ext, *gpio;
	struct property *prop;
	bool typec;

	ext = of_parse_phandle(np, "extcon", 0);
	if (!ext)
		return;
	typec = of_device_is_compatible(ext, "sprd,sc27xx-typec");
	of_node_put(ext);
	if (!typec)
		return;

	gpio = of_find_compatible_node(NULL, NULL, "linux,extcon-usb-gpio");
	if (!gpio || !of_device_is_available(gpio) || !gpio->phandle) {
		of_node_put(gpio);
		dev_warn(dev, "extcon is the Type-C block and there is no VBUS GPIO: USB will not come up\n");
		return;
	}
	prop = kzalloc(sizeof(*prop) + sizeof(__be32), GFP_KERNEL);
	if (prop) {
		prop->name = kstrdup("extcon", GFP_KERNEL);
		prop->length = sizeof(__be32);
		prop->value = prop + 1;
		*(__be32 *)prop->value = cpu_to_be32(gpio->phandle);
		if (prop->name && !of_update_property(np, prop))
			dev_info(dev, "VBUS from %pOF (no driver for the Type-C block)\n", gpio);
		else
			dev_warn(dev, "could not point extcon at %pOF\n", gpio);
	}
	of_node_put(gpio);
}

/*
 * dwc3 tells the PHY about a role change through otg_set_vbus() - host: the host eye pattern, an A-type ID and the
 * D+/D- pull-downs; device: back. Without an otg structure it never did, and a host-mode port kept the device
 * settings (a failed first descriptor read, "error -71", and slow transfers).
 */
static int sprd_ssphy_otg_set_vbus(struct usb_otg *otg, bool on)
{
	return sprd_ssphy_set_vbus(otg->usb_phy, on);
}

static int sprd_ssphy_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *np = dev->of_node;
	struct sprd_ssphy *phy;
	int ret;

	phy = devm_kzalloc(dev, sizeof(*phy), GFP_KERNEL);
	if (!phy)
		return -ENOMEM;

	phy->pmic = sprd_ssphy_pmic_regmap(dev);
	if (!phy->pmic)
		return dev_err_probe(dev, -EPROBE_DEFER, "PMIC regmap not ready\n");
	phy->bc12_enabled = ump9620_has_battery_policy();
	mutex_init(&phy->dpdm_lock);
	spin_lock_init(&phy->bc12_event_lock);
	INIT_DELAYED_WORK(&phy->bc12_work, sprd_ssphy_bc12_work);
	atomic_set(&phy->bc12_generation, 1);

	phy->base = devm_platform_ioremap_resource_byname(pdev, "phy_glb_regs");
	if (IS_ERR(phy->base))
		return PTR_ERR(phy->base);

#define SYSCON(field, prop)							\
	do {									\
		phy->field = syscon_regmap_lookup_by_phandle(np, prop);		\
		if (IS_ERR(phy->field))						\
			return dev_err_probe(dev, PTR_ERR(phy->field), prop "\n");	\
	} while (0)
	SYSCON(aon_apb, "sprd,syscon-aon-apb");
	SYSCON(ana_g0l, "sprd,syscon-ana-g0l");
	SYSCON(ipa_apb, "sprd,syscon-ipa-apb");
	SYSCON(ipa_dispc1_glb_apb, "sprd,syscon-ipa-dispc1-glb-apb");
	SYSCON(ipa_usb31_dp, "sprd,syscon-ipa-usb31-dp");
	SYSCON(ipa_usb31_dptx, "sprd,syscon-ipa-usb31-dptx");
#undef SYSCON

	if (of_property_read_u32(np, "sprd,vdd-voltage", &phy->vdd_vol))
		phy->vdd_vol = 3300000;
	phy->vdd = devm_regulator_get_optional(dev, "vdd");
	if (IS_ERR(phy->vdd)) {
		if (PTR_ERR(phy->vdd) == -EPROBE_DEFER)
			return -EPROBE_DEFER;
		dev_warn(dev, "no vdd supply\n");
		phy->vdd = NULL;
	} else if (regulator_set_voltage(phy->vdd, phy->vdd_vol, phy->vdd_vol)) {
		dev_warn(dev, "cannot set vdd to %u uV\n", phy->vdd_vol);
	}

	if (of_property_read_u32(np, "sprd,hsphy-device-eye-pattern", &phy->device_eye_pattern))
		phy->device_eye_pattern = DEFAULT_DEVICE_EYE_PATTERN;
	if (of_property_read_u32(np, "sprd,hsphy-host-eye-pattern", &phy->host_eye_pattern))
		phy->host_eye_pattern = DEFAULT_HOST_EYE_PATTERN;
	if (of_property_read_u32(np, "sprd,ssphy-ctl5-eye-pattern", &phy->ssphy_ctl5_eye_pattern))
		phy->ssphy_ctl5_eye_pattern = DEFAULT_SSPHY_CTL5_EYE_PATTERN;
	if (of_property_read_u32(np, "sprd,ssphy-ctl6-eye-pattern", &phy->ssphy_ctl6_eye_pattern))
		phy->ssphy_ctl6_eye_pattern = DEFAULT_SSPHY_CTL6_EYE_PATTERN;

	/* select the IPA_SYS USB controller */
	regmap_update_bits(phy->aon_apb, REG_AON_APB_AON_SOC_USB_CTRL,
			   MASK_AON_APB_USB20_CTRL_MUX_REG, MASK_AON_APB_USB20_CTRL_MUX_REG);

	platform_set_drvdata(pdev, phy);
	phy->dev = dev;
	phy->phy.dev = dev;
	phy->phy.label = "sprd-ssphy";
	phy->phy.init = sprd_ssphy_init;
	phy->phy.shutdown = sprd_ssphy_shutdown;
	phy->phy.set_vbus = sprd_ssphy_set_vbus;
	phy->phy.type = USB_PHY_TYPE_USB3;
	phy->phy.notify_connect = sprd_ssphy_notify_connect;
	phy->phy.notify_disconnect = sprd_ssphy_notify_disconnect;
	phy->phy.vbus_nb.notifier_call = sprd_ssphy_vbus_notify;
	phy->phy.otg = devm_kzalloc(dev, sizeof(*phy->phy.otg), GFP_KERNEL);
	if (!phy->phy.otg)
		return -ENOMEM;
	phy->phy.otg->usb_phy = &phy->phy;
	phy->phy.otg->set_vbus = sprd_ssphy_otg_set_vbus;

	sprd_ssphy_vbus_source(dev);
	if (phy->bc12_enabled) {
		phy->phy.charger_detect = sprd_ssphy_charger_detect;
		ret = sc27xx_dpdm_switch_to_phy(phy->pmic, false);
		if (!ret)
			ret = regmap_update_bits(phy->pmic, CHGR_DET_FGU_CTRL,
						 GENMASK(11, 8), 2 << 8);
		if (ret)
			return dev_err_probe(dev, ret, "BC1.2 detector setup\n");
	}
	ret = usb_add_phy_dev(&phy->phy);
	if (ret)
		return dev_err_probe(dev, ret, "cannot add phy\n");

	if (phy->bc12_enabled) {
		if (IS_ERR_OR_NULL(phy->phy.edev)) {
			usb_remove_phy(&phy->phy);
			return dev_err_probe(dev, -ENODEV, "BC1.2 requires VBUS extcon\n");
		}
		WRITE_ONCE(phy->bc12_ready, true);
		sprd_ssphy_vbus_notify(&phy->phy.vbus_nb,
			extcon_get_state(phy->phy.edev, EXTCON_USB) > 0, NULL);
	} else {
		/* Unchanged data-only path on F50/V50 and other battery-less boards. */
		sc27xx_dpdm_switch_to_phy(phy->pmic, true);
	}
	dev_info(dev, "UMS9620 USB 3.1 PHY registered\n");
	return 0;
}

static void sprd_ssphy_bc12_stop(struct sprd_ssphy *phy)
{
	unsigned long flags;

	if (phy->bc12_enabled) {
		spin_lock_irqsave(&phy->bc12_event_lock, flags);
		WRITE_ONCE(phy->bc12_stopping, true);
		spin_unlock_irqrestore(&phy->bc12_event_lock, flags);
		devm_extcon_unregister_notifier(phy->dev, phy->phy.edev,
					       EXTCON_USB, &phy->phy.vbus_nb);
		cancel_delayed_work_sync(&phy->bc12_work);
		cancel_work_sync(&phy->phy.chg_work);
		regmap_update_bits(phy->pmic, UMP9620_BC12_CTRL2, BIT(0), 0);
		sc27xx_dpdm_switch_to_phy(phy->pmic, false);
	}
}

static void sprd_ssphy_shutdown_device(struct platform_device *pdev)
{
	sprd_ssphy_bc12_stop(platform_get_drvdata(pdev));
}

static void sprd_ssphy_remove(struct platform_device *pdev)
{
	struct sprd_ssphy *phy = platform_get_drvdata(pdev);

	sprd_ssphy_bc12_stop(phy);
	usb_remove_phy(&phy->phy);
}

static const struct of_device_id sprd_ssphy_match[] = {
	{ .compatible = "sprd,qogirn6pro-ssphy" },
	{ }
};
MODULE_DEVICE_TABLE(of, sprd_ssphy_match);

static struct platform_driver sprd_ssphy_driver = {
	.probe = sprd_ssphy_probe,
	.remove = sprd_ssphy_remove,
	.shutdown = sprd_ssphy_shutdown_device,
	.driver = { .name = "sprd-ssphy", .of_match_table = sprd_ssphy_match },
};
module_platform_driver(sprd_ssphy_driver);

MODULE_DESCRIPTION("Unisoc UMS9620 USB 3.1 PHY driver");
MODULE_LICENSE("GPL");
