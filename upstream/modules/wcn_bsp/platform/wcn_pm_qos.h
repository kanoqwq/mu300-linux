/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright (C) 2022 Spreadtrum Communications Inc.
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 */
#ifndef __WCN_PM_QOS_H__
#define __WCN_PM_QOS_H__

#include <linux/pm_qos.h>

struct wcn_pm_qos_condition {
	bool blank_cond;
	bool cpu_pd_forbid;
	bool cpu_freq_change;
	bool ddr_freq_change;
};

/* Number of CPU to be updated */
#define CPU_CORE_NUM				6
#define CPU_CORE_NUM_OFFSET		0
#define CPU_FREQ_TABLE_NUM_MAX	20
/* freq index, max level: struct wcn_pm_qos:cpu_freq_table_num[cpu_idx] */
#define CPU_FREQ_LEVEL_INDEX		4

struct wcn_pm_qos {
	unsigned long active_modules;
	unsigned long active_modules_pending;
	struct mutex mutex;
	struct pm_qos_request pm_qos_req;
	struct freq_qos_request wcn_freq_qos_req[CPU_CORE_NUM];
	struct cpufreq_policy *policy;
	struct device *dev;
	struct wcn_pm_qos_condition cond;
	bool cond_set;
	unsigned int min_freq[CPU_CORE_NUM], max_freq[CPU_CORE_NUM];
	unsigned int cpu_freq_table[CPU_CORE_NUM][CPU_FREQ_TABLE_NUM_MAX];
	unsigned int cpu_freq_table_num[CPU_CORE_NUM];
	bool freq_set_flag;
	bool cpu_pd_set_flag;
	bool ddr_freq_set_flag;
	bool pm_qos_disable;
};

#define CPU_FREQ_BIG_CORE_INDEX	6
#define CPU_DMA_FORBID_POWER_DOWN_LATENCY	100

int wcn_pm_qos_config_common(unsigned int mode, bool set);
int wcn_pm_qos_init(void);
void wcn_pm_qos_exit(void);

#endif
