/*
 *
 * (C) COPYRIGHT 2012-2015 ARM Limited. All rights reserved.
 *
 * This program is free software and is provided to you under the terms of the
 * GNU General Public License version 2 as published by the Free Software
 * Foundation, and any use by you of this program is subject to the terms
 * of such GNU licence.
 *
 * A copy of the licence is included with the program, and can also be obtained
 * from Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor,
 * Boston, MA  02110-1301, USA.
 *
 */



#include <mali_kbase_gpu_stats_debugfs.h>
#include <backend/gpu/mali_kbase_pm_internal.h>

#ifdef CONFIG_DEBUG_FS

#define MAX_ENABLE_STATS_LEN 5

/** Show callback for the @c gpu_stats debugfs file.
 *
 * This function is called to get the contents of the @c gpu_stats debugfs
 * file. This is a report of current gpu stats usage.
 *
 * @param sfile The debugfs entry
 * @param data Data associated with the entry
 *
 * @return 0 if successfully prints data in debugfs entry file
 *         -1 if it encountered an error
 */

static int kbasep_gpu_enable_stats_seq_show(struct seq_file *sfile, void *data)
{
	ssize_t ret = 0;

#ifdef CONFIG_MALI_DEVFREQ
	struct kbase_device *kbdev = sfile->private;

	seq_printf(sfile, "%5u\n", kbdev->enable_freq_stats);
#endif

	return ret;
}

/*
 *  File operations related to debugfs entry for enable_freq_stats
 */
static int kbasep_gpu_enable_stats_debugfs_open(struct inode *in, struct file *file)
{
	return single_open(file, kbasep_gpu_enable_stats_seq_show, in->i_private);
}

void kbase_gpu_set_freq_stats(struct kbase_device *kbdev)
{
	if (kbdev->enable_freq_stats)
	{
		int 			i = 0;
		unsigned long 	current_freq = 0;
		struct kbasep_pm_metrics diff;

		//Get statistics since its last request
		kbase_pm_get_dvfs_metrics(kbdev, &kbdev->last_freqstats_metrics, &diff);

		//get current freq
		current_freq = kbdev->current_nominal_freq;

		//record current freq statistics
		for(i = 0; i < kbdev->freq_num; i++)
		{
			if (current_freq == kbdev->freq_stats[i].freq)
			{
				kbdev->freq_stats[i].busy_time += diff.time_busy;
				kbdev->freq_stats[i].total_time += diff.time_busy + diff.time_idle;
				break;
			}
		}
}
}

static ssize_t kbasep_gpu_enable_stats_debugfs_write(struct file *file,
		const char __user *ubuf, size_t count, loff_t *ppos)
{
	struct seq_file *s = file->private_data;
	struct kbase_device *kbdev = s->private;
	char buf[MAX_ENABLE_STATS_LEN];
	u32 enable_freq_stats = 0;
	int i = 0;
	struct kbasep_pm_metrics diff;

	CSTD_UNUSED(ppos);

	count = min_t(size_t, sizeof(buf) - 1, count);
	if (copy_from_user(buf, ubuf, count))
		return -EFAULT;

	if (sscanf(buf, "%d", &enable_freq_stats) == 0)
		return -EFAULT;

	if (kbdev->enable_freq_stats != enable_freq_stats)
	{
		//set last freq statistics
		if (enable_freq_stats)
		{
			//reset freq statistics
			for(i=0; i<kbdev->freq_num; i++)
			{
				kbdev->freq_stats[i].busy_time = 0;
				kbdev->freq_stats[i].total_time = 0;
			}

			//clear last freq statistics
			kbase_pm_get_dvfs_metrics(kbdev, &kbdev->last_freqstats_metrics, &diff);
		}
		else
		{
			//set freq statistic
			kbase_gpu_set_freq_stats(kbdev);
		}

		kbdev->enable_freq_stats = enable_freq_stats;
	}

	return count;
}


static const struct file_operations kbasep_gpu_enable_stats_debugfs_fops = {
	.open = kbasep_gpu_enable_stats_debugfs_open,
	.read = seq_read,
	.write = kbasep_gpu_enable_stats_debugfs_write,
	.llseek = seq_lseek,
	.release = single_release,
};

static int kbasep_gpu_freq_stats_seq_show(struct seq_file *sfile, void *data)
{
	ssize_t ret = 0;

#ifdef CONFIG_MALI_DEVFREQ
	int i = 0;
	struct kbase_device *kbdev = sfile->private;

	//titles
	seq_printf(sfile, "   Freq        Busy time(us)     Total time(us)\n");

	//freq statistic
	kbase_gpu_set_freq_stats(kbdev);

	//freq-busy time-total time
	for(i = 0; i < kbdev->freq_num; i++)
	{
		seq_printf(sfile, "%10llu %16llu %16llu\n",
			kbdev->freq_stats[i].freq,
			kbdev->freq_stats[i].busy_time,
			kbdev->freq_stats[i].total_time);
	}
#endif

	return ret;
}

/*
 *  File operations related to debugfs entry for freq_stats
 */
static int kbasep_gpu_freq_stats_debugfs_open(struct inode *in, struct file *file)
{
	return single_open(file, kbasep_gpu_freq_stats_seq_show, in->i_private);
}

static const struct file_operations kbasep_gpu_freq_stats_debugfs_fops = {
	.open = kbasep_gpu_freq_stats_debugfs_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

/*
 *  Initialize debugfs entry for gpu_stats
 */
void kbasep_gpu_stats_debugfs_init(struct kbase_device *kbdev)
{
	debugfs_create_file("enable_freq_stats", S_IRUGO | S_IWUGO,
			kbdev->mali_debugfs_directory, kbdev,
			&kbasep_gpu_enable_stats_debugfs_fops);

	debugfs_create_file("freq_stats", S_IRUGO | S_IWUGO,
			kbdev->mali_debugfs_directory, kbdev,
			&kbasep_gpu_freq_stats_debugfs_fops);
	return;
}

#else
/*
 * Stub functions for when debugfs is disabled
 */
void kbase_gpu_set_freq_stats(struct kbase_device *kbdev)
{
	return;
}

void kbasep_gpu_stats_debugfs_init(struct kbase_device *kbdev)
{
	return;
}
#endif
