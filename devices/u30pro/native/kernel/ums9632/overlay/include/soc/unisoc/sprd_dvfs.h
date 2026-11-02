/* SPDX-License-Identifier: GPL-2.0 */
//Copyright (C) 2024 Unisoc Inc.

#ifndef __SPRD_DVFS_H__
#define __SPRD_DVFS_H__

#include <linux/cpumask.h>
#include <linux/types.h>

#define MAX_DVFS_NUM		(16)

struct sprd_dvfs_scu_ops {
	int (*scu_set)(u32 enable, u32 index);
	int (*scu_get)(u32 *index);
};

struct dvfs_scu_map {
	uint32_t cpu_freq;
	uint32_t scu_idx;
};

struct sprd_dvfs_scu_info {
	struct dvfs_scu_map scu_table[MAX_DVFS_NUM];
	uint32_t dvfs_num;
	struct cpumask cpus;
};

struct sprd_dvfs_scu_data {
	struct sprd_dvfs_scu_ops scu_ops;
	struct sprd_dvfs_scu_info *scu_info;
	uint32_t cluster_num;
};

#endif
