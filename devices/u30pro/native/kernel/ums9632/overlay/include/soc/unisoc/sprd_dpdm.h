/* SPDX-License-Identifier: GPL-2.0+
 *
 * sprd_dpdm.h -- USB dpdm handling
 *
 * Copyright (C) 2022 Xinhu Wu <xinhu.wu@unisoc.com>
 */

#ifndef __SPRD_DPDM_H
#define __SPRD_DPDM_H

#include <linux/usb/phy.h>
#include <linux/types.h>

struct sprd_dpdm_variant_data {
	u8 pmic_name;
	u32 fs_enb;
	u32 bc_enb;
};

struct sprd_dpdm {
	struct device *dev;
	struct regmap *regmap;
	const struct sprd_dpdm_variant_data *data;
	struct iio_channel	*dp;
	struct iio_channel	*dm;
	bool			shutdown;
	bool			dpdm_detect;

	void (*dpdm_switch_to_phy)(struct sprd_dpdm *dpdm, bool enable);
	bool (*get_dpdm_from_phy)(struct sprd_dpdm *dpdm);
	enum usb_charger_type (*dpdm_charger_detect)(struct sprd_dpdm *dpdm,
			struct usb_phy *x);
	u32 base;
};

#endif
