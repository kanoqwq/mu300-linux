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



#include <mali_kbase_gpu_utilisation_debugfs.h>
#include <backend/gpu/mali_kbase_pm_internal.h>

#ifdef CONFIG_DEBUG_FS
/** Show callback for the @c gpu_utilisation debugfs file.
 *
 * This function is called to get the contents of the @c gpu_utilisation debugfs
 * file. This is a report of current gpu utilisation usage.
 *
 * @param sfile The debugfs entry
 * @param data Data associated with the entry
 *
 * @return 0 if successfully prints data in debugfs entry file
 *         -1 if it encountered an error
 */

static int kbasep_gpu_utilisation_seq_show(struct seq_file *sfile, void *data)
{

	ssize_t ret = 0;
	struct kbase_device *kbdev = sfile->private;
	unsigned long total_time = 0, busy_time;

	//get total time and busy time
#if defined(CONFIG_PM_DEVFREQ) || defined(CONFIG_MALI_MIDGARD_DVFS)
        struct kbasep_pm_metrics diff;
	kbase_pm_get_dvfs_metrics(kbdev, &kbdev->last_frequtil_metrics, &diff);
        busy_time = diff.time_busy;
        total_time = diff.time_busy + diff.time_idle;
#else
	// Fixed compiling error for  unused parameter
	kbdev = kbdev;
	busy_time=0;
	total_time=100;
#endif

	seq_printf(sfile, "%5lu\n", busy_time*100/total_time);
	return ret;

}

/*
 *  File operations related to debugfs entry for gpu_memory
 */
static int kbasep_gpu_utilisation_debugfs_open(struct inode *in, struct file *file)
{
	return single_open(file, kbasep_gpu_utilisation_seq_show, in->i_private);
}

static const struct file_operations kbasep_gpu_utilisation_debugfs_fops = {
	.open = kbasep_gpu_utilisation_debugfs_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

/*
 *  Initialize debugfs entry for gpu_utilisation
 */
void kbasep_gpu_utilisation_debugfs_init(struct kbase_device *kbdev)
{
	debugfs_create_file("gpu_utilisation", S_IRUGO,
			kbdev->mali_debugfs_directory, kbdev,
			&kbasep_gpu_utilisation_debugfs_fops);
	return;
}

#else
/*
 * Stub functions for when debugfs is disabled
 */
void kbasep_gpu_utilisation_debugfs_init(struct kbase_device *kbdev)
{
	return;
}
#endif
