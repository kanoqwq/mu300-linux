/*
 *
 * (C) COPYRIGHT 2011-2018 ARM Limited. All rights reserved.
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
 * Statistics for power management
 */

#include <mali_kbase.h>
#include <mali_kbase_pm.h>
#include "mali_kbase_jm_rb.h"
#include "mali_kbase_pm_statistics.h"

int kbase_pm_statistics_init(struct kbase_device *kbdev)
{
	//set sub system name
	strcpy(kbdev->pm.backend.statistics.sleep_info.subsystem_name, "GPU");

	//init statistics
	kbdev->pm.backend.statistics.power_mode = 0;
	kbdev->pm.backend.statistics.gpu_active = false;
	kbdev->pm.backend.statistics.time_period_start = ktime_get();

	kbdev->pm.backend.statistics.values.time_busy = 0;
	kbdev->pm.backend.statistics.values.time_idle = 0;
	kbdev->pm.backend.statistics.values.time_sleep = 0;
	kbdev->pm.backend.statistics.values.time_total = 0;

	//init lock
	spin_lock_init(&kbdev->pm.backend.statistics.lock);

	return 0;
}

static void kbase_pm_get_statistics_calc(struct kbase_device *kbdev, ktime_t now)
{
#ifdef CONFIG_MALI_DEVFREQ
	int            i = 0;
#endif
	ktime_t        diff;
	unsigned long  current_freq = 0;

	lockdep_assert_held(&kbdev->pm.backend.statistics.lock);

	diff = ktime_sub(now, kbdev->pm.backend.statistics.time_period_start);
	if (ktime_to_ns(diff) < 0)
		return;

	//printk(KERN_ERR, "%s power_mode=%d gpu_active=%d \n", __func__, kbdev->pm.backend.statistics.power_mode, kbdev->pm.backend.statistics.gpu_active);
	switch (kbdev->pm.backend.statistics.power_mode)
	{
	case 0://power on
		//get current freq
		current_freq = kbdev->current_nominal_freq;

		if (kbdev->pm.backend.statistics.gpu_active) {
			//add busy time
			kbdev->pm.backend.statistics.values.time_busy = ktime_add(kbdev->pm.backend.statistics.values.time_busy, diff);

#ifdef CONFIG_MALI_DEVFREQ
			//record current freq statistics
			for(i = 0; i < kbdev->freq_num; i++)
			{
				if (current_freq == kbdev->pm.backend.statistics.freq_values[i].freq)
				{
					kbdev->pm.backend.statistics.freq_values[i].time_busy = ktime_add(kbdev->pm.backend.statistics.freq_values[i].time_busy, diff);
					kbdev->pm.backend.statistics.freq_values[i].time_total = ktime_add(kbdev->pm.backend.statistics.freq_values[i].time_total, diff);
					break;
				}
			}
#endif
		} else {
			//add idle time
			kbdev->pm.backend.statistics.values.time_idle = ktime_add(kbdev->pm.backend.statistics.values.time_idle, diff);

#ifdef CONFIG_MALI_DEVFREQ
			//record current freq statistics
			for(i = 0; i < kbdev->freq_num; i++)
			{
				if (current_freq == kbdev->pm.backend.statistics.freq_values[i].freq)
				{
					kbdev->pm.backend.statistics.freq_values[i].time_idle = ktime_add(kbdev->pm.backend.statistics.freq_values[i].time_idle, diff);
					kbdev->pm.backend.statistics.freq_values[i].time_total = ktime_add(kbdev->pm.backend.statistics.freq_values[i].time_total, diff);
					break;
				}
			}
#endif
		}
		break;

	case 1://light sleep
	case 2://deep sleep
		//add sleep time
		kbdev->pm.backend.statistics.values.time_sleep = ktime_add(kbdev->pm.backend.statistics.values.time_sleep, diff);
		break;

	default:
		break;
	}

	//add total time
	kbdev->pm.backend.statistics.values.time_total = ktime_add(kbdev->pm.backend.statistics.values.time_total, diff);

	kbdev->pm.backend.statistics.time_period_start = now;

	//convert ktime to ms
	kbdev->pm.backend.statistics.values_ms.time_busy = ktime_to_ms(kbdev->pm.backend.statistics.values.time_busy);
	kbdev->pm.backend.statistics.values_ms.time_idle = ktime_to_ms(kbdev->pm.backend.statistics.values.time_idle);
	kbdev->pm.backend.statistics.values_ms.time_sleep = ktime_to_ms(kbdev->pm.backend.statistics.values.time_sleep);
	kbdev->pm.backend.statistics.values_ms.time_total = ktime_to_ms(kbdev->pm.backend.statistics.values.time_total);

	//convert ms to jiffies
	kbdev->pm.backend.statistics.values_jiffies.time_busy = msecs_to_jiffies(kbdev->pm.backend.statistics.values_ms.time_busy);
	kbdev->pm.backend.statistics.values_jiffies.time_idle = msecs_to_jiffies(kbdev->pm.backend.statistics.values_ms.time_idle);
	kbdev->pm.backend.statistics.values_jiffies.time_sleep = msecs_to_jiffies(kbdev->pm.backend.statistics.values_ms.time_sleep);
	kbdev->pm.backend.statistics.values_jiffies.time_total = msecs_to_jiffies(kbdev->pm.backend.statistics.values_ms.time_total);
}

void kbase_pm_statistics_update(struct kbase_device *kbdev, ktime_t now)
{
	int js;
	unsigned long flags;

	spin_lock_irqsave(&kbdev->pm.backend.statistics.lock, flags);

	//printk(KERN_ERR, "%s power_mode=%d gpu_active=%d \n", __func__, kbdev->pm.backend.statistics.power_mode, kbdev->pm.backend.statistics.gpu_active);
	kbase_pm_get_statistics_calc(kbdev, now);

	//set gpu active
	kbdev->pm.backend.statistics.gpu_active = false;
	for (js = 0; js < BASE_JM_MAX_NR_SLOTS; js++) {
		struct kbase_jd_atom *katom = kbase_gpu_inspect(kbdev, js, 0);

		/* Head atom may have just completed, so if it isn't running
		 * 		 * then try the next atom */
		if (katom && katom->gpu_rb_state != KBASE_ATOM_GPU_RB_SUBMITTED)
			katom = kbase_gpu_inspect(kbdev, js, 1);

		if (katom && katom->gpu_rb_state ==
				KBASE_ATOM_GPU_RB_SUBMITTED) {
			kbdev->pm.backend.statistics.gpu_active = true;
		}
	}

	spin_unlock_irqrestore(&kbdev->pm.backend.statistics.lock, flags);
}

void kbase_pm_set_statistics(struct kbase_device *kbdev, int power_mode)
{
	unsigned long flags;

	spin_lock_irqsave(&kbdev->pm.backend.statistics.lock, flags);

	//printk(KERN_ERR, "%s power_mode=%d gpu_active=%d \n", __func__, power_mode, kbdev->pm.backend.statistics.gpu_active);
	//set power mode
	kbdev->pm.backend.statistics.power_mode = power_mode;
	kbase_pm_get_statistics_calc(kbdev, ktime_get());

	spin_unlock_irqrestore(&kbdev->pm.backend.statistics.lock, flags);
}

#ifdef CONFIG_MALI_DEVFREQ
void kbase_pm_statistics_FreqInit(struct kbase_device *kbdev)
{
	int i = 0;

	kbdev->pm.backend.statistics.freq_values = vmalloc(sizeof(struct kbasep_pm_freq_statistics) * kbdev->freq_num);
	KBASE_DEBUG_ASSERT(kbdev->pm.backend.statistics.freq_values);

	for (i = 0; i < kbdev->freq_num; i++)
	{
		kbdev->pm.backend.statistics.freq_values[i].freq = kbdev->freq_stats[i].freq;
		kbdev->pm.backend.statistics.freq_values[i].time_busy = 0;
		kbdev->pm.backend.statistics.freq_values[i].time_idle = 0;
		kbdev->pm.backend.statistics.freq_values[i].time_total = 0;
	}
}

void kbase_pm_statistics_FreqDeinit(struct kbase_device *kbdev)
{
	if (NULL != kbdev->pm.backend.statistics.freq_values)
	{
		vfree(kbdev->pm.backend.statistics.freq_values);
		kbdev->pm.backend.statistics.freq_values = NULL;
	}
}

void kbase_pm_set_freq_statistics(struct kbase_device *kbdev)
{
	unsigned long flags;

	spin_lock_irqsave(&kbdev->pm.backend.statistics.lock, flags);

	//printk(KERN_ERR, "%s power_mode=%d gpu_active=%d \n", __func__, kbdev->pm.backend.statistics.power_mode, kbdev->pm.backend.statistics.gpu_active);
	kbase_pm_get_statistics_calc(kbdev, ktime_get());

	spin_unlock_irqrestore(&kbdev->pm.backend.statistics.lock, flags);
}
#endif

#ifdef CONFIG_DEBUG_FS
static int kbasep_gpu_freq_time_seq_show(struct seq_file *sfile, void *data)
{
	ssize_t ret = 0;
	struct kbase_device *kbdev = sfile->private;
#ifdef CONFIG_MALI_DEVFREQ
	int i = 0;
	s64 ms_busy = 0, ms_idle = 0, ms_total = 0;
#endif

	kbase_pm_set_statistics(kbdev, kbdev->pm.backend.statistics.power_mode);

	//print sleep info
	seq_printf(sfile, "\n System name: %s\n\n", kbdev->pm.backend.statistics.sleep_info.subsystem_name);
	seq_printf(sfile, "           Duration(ms)   Duration(jiffies)\n");
	seq_printf(sfile, "Busy:  %16lld %16llu\n", kbdev->pm.backend.statistics.values_ms.time_busy, kbdev->pm.backend.statistics.values_jiffies.time_busy);
	seq_printf(sfile, "Idle:  %16lld %16llu\n", kbdev->pm.backend.statistics.values_ms.time_idle, kbdev->pm.backend.statistics.values_jiffies.time_idle);
	seq_printf(sfile, "Sleep: %16lld %16llu\n", kbdev->pm.backend.statistics.values_ms.time_sleep, kbdev->pm.backend.statistics.values_jiffies.time_sleep);
	seq_printf(sfile, "Total: %16lld %16llu\n", kbdev->pm.backend.statistics.values_ms.time_total, kbdev->pm.backend.statistics.values_jiffies.time_total);

#ifdef CONFIG_MALI_DEVFREQ
	//titles
	seq_printf(sfile, "\n\n   Freq        Busy time(ms)     Idle time(ms)  Total time(ms) \n");

	//freq statistic
	kbase_pm_set_freq_statistics(kbdev);

	//freq-busy time-total time
	for(i = 0; i < kbdev->freq_num; i++)
	{
		//convert ktime to ms
		ms_busy = ktime_to_ms(kbdev->pm.backend.statistics.freq_values[i].time_busy);
		ms_idle = ktime_to_ms(kbdev->pm.backend.statistics.freq_values[i].time_idle);
		ms_total = ktime_to_ms(kbdev->pm.backend.statistics.freq_values[i].time_total);

		seq_printf(sfile, "%10llu %16llu %16llu %16llu\n",
				kbdev->pm.backend.statistics.freq_values[i].freq,
				ms_busy,
				ms_idle,
				ms_total);
	}
#endif

	return ret;
}

static int kbasep_gpu_freq_time_debugfs_open(struct inode *in, struct file *file)
{
	return single_open(file, kbasep_gpu_freq_time_seq_show, in->i_private);
}

static const struct file_operations kbasep_gpu_freq_time_debugfs_fops = {
	.open = kbasep_gpu_freq_time_debugfs_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

void kbasep_gpu_statistics_debugfs_init(struct kbase_device *kbdev)
{
	debugfs_create_file("gpu_freq_time", S_IRUGO | S_IWUGO,
			kbdev->mali_debugfs_directory, kbdev,
			&kbasep_gpu_freq_time_debugfs_fops);
}
#else
void kbasep_gpu_statistics_debugfs_init(struct kbase_device *kbdev)
{
}
#endif
