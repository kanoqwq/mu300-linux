/* SPDX-License-Identifier: GPL-2.0 */
// Copyright (C) 2023 Unisoc, Inc.

#ifndef __SPRD_ASE_GENERIC_H__
#define __SPRD_ASE_GENERIC_H__

#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>

#define  CTRL_MASK  (0x1ff)

struct ase_core_data {
	struct device *dev;
	struct device_node *of_node;
	struct regmap *regmap;
	u32 reg[2];
	u32 ase_id;
	u32 work_volt;
	u32 deep_volt;
	u32 volt_threshold;
};

#endif
