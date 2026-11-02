/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Copyright (c) 2023, Spreadtrum Communications Inc. All rights reserved.
 *
 * Interface of the PMU sampling driver (drivers/unisoc_platform/pmu): every sample_ms it hands the per-CPU performance counter
 * deltas and the measured CPU frequencies to the registered clients (scufreq).  Based on the vendor header; sprd_pmu_sample_enable()
 * is new: the counters are only held while a client wants samples.
 */

#ifndef _SPRD_PMU_SAMPLE_H
#define _SPRD_PMU_SAMPLE_H

#include <linux/kernel.h>
#include <linux/notifier.h>

#define SPRD_PMU_MAX_EVS	8
#define MAX_CPU_NUM		8

enum ev_idx {
	INST_IDX,
	CYC_IDX,
	FE_STALL_IDX,
	BE_STALL_IDX,
	MISS_IDX,
	WB_IDX,
	ACC_IDX,
	NUM_EVS
};

struct cpu_ctrs {
	u64				ctrs[NUM_EVS];
};

struct sprd_pmu_sample_data {
	struct cpu_ctrs			delta[MAX_CPU_NUM];
	u32				freq_mhz[MAX_CPU_NUM];
	u32				ev_ids[NUM_EVS];
	bool				ctrs_released;
};

#if IS_ENABLED(CONFIG_U30PRO_SPRD_PMU_SAMPLE)
int sprd_pmu_sample_notify_register(struct notifier_block *nb);
int sprd_pmu_sample_notify_unregister(struct notifier_block *nb);
int sprd_pmu_sample_enable(bool enable);
#else
static inline int sprd_pmu_sample_notify_register(struct notifier_block *nb)
{
	return -ENODEV;
}
static inline int sprd_pmu_sample_notify_unregister(struct notifier_block *nb)
{
	return -ENODEV;
}
static inline int sprd_pmu_sample_enable(bool enable)
{
	return -ENODEV;
}
#endif

#endif /* _SPRD_PMU_SAMPLE_H */
