/* SPDX-License-Identifier: GPL-2.0 */
//Copyright (C) 2022 Unisoc Inc.

#ifndef __SPRD_ASE_OPS_H__
#define __SPRD_ASE_OPS_H__
#include <linux/sprd-ase.h>

#include <linux/types.h>

struct ase_ops {
	int (*sys_prepare)(u32 sys_id, u32 test_volt);
	int (*sys_update)(u32 sys_id, u32 volt_margin);
};

struct sprd_ase_ops {
	int (*ops_init)(u32 sys_id, struct ase_ops *ops, bool is_test);
	struct ase_device  *ase_device;
};

#endif
