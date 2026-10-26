/*
 *
 * (C) COPYRIGHT 2010-2015, 2018 ARM Limited. All rights reserved.
 *
 * This program is free software and is provided to you under the terms of the
 * GNU General Public License version 2 as published by the Free Software
 * Foundation, and any use by you of this program is subject to the terms
 * of such GNU licence.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, you can access it online at
 * http://www.gnu.org/licenses/gpl-2.0.html.
 *
 * SPDX-License-Identifier: GPL-2.0
 *
 */

/*
 * Power statistics API definitions
 */

#ifndef _KBASE_PM_STATISTICS_H_
#define _KBASE_PM_STATISTICS_H_

//#include "mali_kbase.h"

//是一个很大的长整数，单位是 100 毫微秒。表示自 0001 年 1 月 1 日午夜 12:00:00 以来已经过的时间的以 100 毫微秒为间隔的间隔数.
//1 毫秒 = 10^-3 秒，
//1 微秒 = 10^-6 秒，
//1 毫微秒 = 10^-9 秒，
//100 毫微秒 = 10^-7 秒。
struct subsys_sleep_info {
	char    subsystem_name[8];     //subsystem name: GPU
	u64     total_duration;        //total ticks from start, 16 hex total=active+sleep+idle
	u64     idle_duration_total;   //idle ticks from start, 16 hex
	u64     sleep_duration_total;  //sleep ticks from start, 16 hex
	uint    current_status;        //1:active/idle 0: sleep
	uint    subsystem_reboot_count;
	uint    wakeup_reason;
	uint    last_wakeup_duration;  //last wakeup time
	uint    last_sleep_duration;   //last sleep time
	uint    active_core;
	uint    internal_irq_count;
	uint    irq_to_ap_count;
	uint    reseve[4];
};

/**
  * struct kbasep_pm_statistics - Statistics data collected for use by the power
  *                            management framework.
  *
  *  @time_busy: number of ktime_t the GPU was busy executing jobs since the
  *          @time_period_start timestamp.
  *  @time_idle: number of ktime_t since time_period_start the GPU was not executing
  *          jobs since the @time_period_start timestamp.
  *  @time_sleep: number of ktime_t the GPU was sleep since the @time_period_start timestamp.
  *  @time_total: number of ktime the GPU total time since the @time_period_start timestamp.
  */
struct kbasep_pm_statistics {
	ktime_t time_busy;
	ktime_t time_idle;
	ktime_t time_sleep;
	ktime_t time_total;
};

struct kbasep_pm_statistics_ms {
	s64 time_busy;
	s64 time_idle;
	s64 time_sleep;
	s64 time_total;
};

struct kbasep_pm_statistics_jiffies {
	u64 time_busy;
	u64 time_idle;
	u64 time_sleep;
	u64 time_total;
};

struct kbasep_pm_freq_statistics {
	u64     freq;
	ktime_t time_busy;
	ktime_t time_idle;
	ktime_t time_total;
};

struct kbasep_pm_statistics_state {
	//subsys sleep info
	struct subsys_sleep_info sleep_info;

	int     power_mode;
	bool    gpu_active;
	ktime_t time_period_start;

	spinlock_t lock;

	struct kbasep_pm_statistics            values;
	struct kbasep_pm_statistics_ms         values_ms;
	struct kbasep_pm_statistics_jiffies    values_jiffies;

	struct kbasep_pm_freq_statistics       *freq_values;
};

/**
 * kbase_pm_statistics_init - Initialize power statistics framework
 *
 * @kbdev: The kbase device structure for the device (must be a valid pointer)
 *
 * Return: 0 if the power statistics framework was successfully
 *         initialized, -errno otherwise.
 */
int kbase_pm_statistics_init(struct kbase_device *kbdev);

#ifdef CONFIG_MALI_DEVFREQ
void kbase_pm_statistics_FreqInit(struct kbase_device *kbdev);

void kbase_pm_statistics_FreqDeinit(struct kbase_device *kbdev);

void kbase_pm_set_freq_statistics(struct kbase_device *kbdev);
#endif

void kbase_pm_statistics_update(struct kbase_device *kbdev, ktime_t now);

void kbase_pm_set_statistics(struct kbase_device *kbdev, int power_mode);

void kbasep_gpu_statistics_debugfs_init(struct kbase_device *kbdev);
#endif /* _KBASE_PM_STATISTICS_H_ */
