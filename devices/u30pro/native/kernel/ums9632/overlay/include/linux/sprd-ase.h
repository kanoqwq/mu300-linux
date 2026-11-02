/* SPDX-License-Identifier: GPL-2.0 */
// Copyright (C) 2023 Unisoc, Inc.

#ifndef __SPRD_ASE_H__
#define __SPRD_ASE_H__

#include <linux/alarmtimer.h>
#include <linux/delay.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/nvmem-consumer.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/delay.h>
#include <linux/slab.h>
#include <linux/time.h>
#include <linux/workqueue.h>
#include <linux/sprd_ase_ops.h>
#include <linux/pm_wakeup.h>
#include <linux/types.h>

#define ASE_TEST_TIMES			(5U) /* must greater than or equal to 3 */
#define ASE_MAX_VOLT			(50000)
#define ASE_MIN_TEST_VOLT		(550000U)
#define DCDC_STEP_VOL                   (3125U)   /* uV */

enum sprd_ase_work_mode {
	ASE_NORMAL_MODE = 1,
	ASE_ALL_SEQUENCE,
	ASE_ALL_CHAIN,
};

enum ase_current_state {
	ASE_IDLE = 2,
	INIT_FAIL,
};

struct ase_reg_info {
	u32 reg;
	u32 off;
	u32 msk;
	u32 val;
};

struct ase_sys_info {
	unsigned int id;
	bool bind_enable;
	bool tested;
	atomic_t ase_cnt;
	cpumask_t cpus;
	char *full_name;
	struct device_node *node;
	unsigned int sequence;
	unsigned int chain;
	unsigned int aging_volt;
	unsigned int test_volt;
	unsigned int ase_h;
	unsigned int age_calc[ASE_TEST_TIMES];
	unsigned int young_calc[ASE_TEST_TIMES];
	unsigned int young_src; /* delta_src */
	unsigned int age_src; /* slope */

	struct ase_reg_info age_calc_reg;
	struct ase_reg_info young_calc_reg;
	struct ase_reg_info init_fail;

	struct ase_ops *sys_ops;

	struct work_struct ase_work;
};

struct ase_private_data {
	struct ase_reg_info cfg_duration;
	struct ase_reg_info sel_x;
	struct ase_reg_info sel_y;
	struct ase_reg_info work_mode;
	struct ase_reg_info run_ctrl;
	struct ase_reg_info cur_state;
	struct ase_reg_info rd_valid;
	struct ase_reg_info init_fail;
	struct ase_reg_info idle_mode;
	struct ase_reg_info calc_base;
	int (*volt_calc)(struct ase_sys_info *info, u32 *volt);
	unsigned int ase_clk;
};

struct ase_device {
	void __iomem *base;
	struct regmap *aonmap;
	struct device *dev;
	u32 rst_reg;
	u32 rst_bit;
	struct device_node *of_node;
	struct mutex ase_lock;
	struct workqueue_struct *ase_wq_struct;
	struct wakeup_source *wake_lock;
	const struct ase_private_data *priv;

	struct ase_reg_info idle_mode;
	struct ase_reg_info config_fail;

	struct alarm ase_alarm;
	u64 cycle_time_ms;
	int sys_num;
	unsigned int sen; /* unused */
	unsigned int duration;
};

/* the vendor debugfs interface is not ported */
static inline int sprd_ase_debug_init(struct ase_device *pdata) { return 0; }
static inline int sprd_ase_debug_sys_init(struct ase_sys_info *info) { return 0; }
static inline int sprd_ase_debug_exit(void) { return 0; }

static inline int sort_ase_cmp(const void *a, const void *b)
{
	if (*(unsigned int *)a == *(unsigned int *)b)
		return 0;
	else
		return *(unsigned int *)a  <  *(unsigned int *)b ? -1 : 1;
}

static inline void sort_ase_swap(void *a, void *b, int size)
{
	*(unsigned int *)a = *(unsigned int *)a ^ *(unsigned int *)b;
	*(unsigned int *)b = *(unsigned int *)a ^ *(unsigned int *)b;
	*(unsigned int *)a = *(unsigned int *)a ^ *(unsigned int *)b;
}

#endif /* __SPRD_ASE_H__ */
