// SPDX-License-Identifier: GPL-2.0-only
/*
 * Software SCU (DSU, snoop control unit) frequency scaling ("sprd,scufreq").
 *
 * The stock design: the firmware normally runs the SCU in a hardware autotune mode.  scufreq takes it over: from the PMU samples of every
 * CPU (measured frequency, instructions per L2 refill = "refill ratio", back-end stall percentage) it works out which CPU frequency each
 * cluster effectively needs, maps that to an SCU index with the CPU frequency -> SCU index table the firmware gives for every cpufreq
 * entry and commits the highest index of all clusters.  While a cluster has a minimum frequency request above its lowest frequency (boost)
 * the SCU goes back to the hardware mode.
 *
 * Based on the vendor driver scufreq.c (GPL-2.0, Unisoc).  Differences: tracepoints are dropped; it only exists with u30pro_scufreq=1
 * (the cpufreq driver then provides the SCU data); it starts enabled in that case (the vendor driver starts disabled and waits for an
 * Android service to write "enable"); the PMU counters are only held while it is enabled; the SCU is put back to the hardware mode every
 * time it is disabled or a boost starts, and the next sample then commits again; an idle system delivers no samples.
 */
#define pr_fmt(fmt) "sprd-scufreq: " fmt

#include <linux/cpufreq.h>
#include <linux/cpumask.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/math.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/pm_qos.h>
#include <linux/platform_device.h>
#include <linux/soc/sprd/pmu_sample.h>
#include <linux/syscore_ops.h>
#include <soc/unisoc/sprd_dvfs.h>

#define SCUFREQ_HW_ENABLE	1
#define SCUFREQ_SOFT_ENABLE	0
#define SCU_IDX_UNKNOWN		U32_MAX

/* set up by the cpufreq driver: the SCU data exists only with u30pro_scufreq=1 */
extern int u30pro_scufreq;

struct cpu_stats {
	u32				freq_mhz;
	u32				stl_fe_pct;
	u32				stl_be_pct;
	u32				rfl_ratio;
	u32				wb_pct;
};

struct cpufreq_scuidx_map {
	unsigned int			cpufreq_mhz;
	unsigned int			scu_idx;
};

struct scufreq_clst {
	bool				inited;
	bool				is_boost;
	cpumask_t			cpus;
	struct cpufreq_scuidx_map	*freq_tbl;
	u32				freq_tbl_len;
	u32				rfl_ratio_ceil;
	u32				stl_fe_flr;
	u32				stl_be_flr;
	u32				freq_comp_pct;
	u32				freq_comp_ceil_mhz;
	u32				freq_comp_flr_mhz;
	u32				wb_pct_thres;
	u32				wb_filt_rfl;
	u32				min_idx;
	u32				max_idx;
	u32				clst_min_idx;
	u32				clst_max_idx;
	u32				cur_idx;
	struct kobject			kobj;
	u32				index;
	struct notifier_block		freq_limit_nb;
	struct cpufreq_policy		*policy;
};

struct scufreq_dev_data {
	struct device			*dev;
	struct kobject			kobj;
	struct scufreq_clst		*clsts;
	u32				num_clsts;
	bool				enable;
	bool				is_boost;
	struct mutex			ena_lock;
	u32				boost_idx;
	u32				cur_idx;
};

static struct scufreq_dev_data *scufreq_data;
static struct sprd_dvfs_scu_data *scu_data;
static struct sprd_dvfs_scu_ops *scu_ops;
static struct cpu_stats sampling_stats[MAX_CPU_NUM];

struct sprd_scufreq_attr {
	struct attribute		attr;
	ssize_t (*show)(struct kobject *kobj, struct attribute *attr, char *buf);
	ssize_t (*store)(struct kobject *kobj, struct attribute *attr, const char *buf, size_t count);
};

#define to_scufreq_attr(_attr)	container_of(_attr, struct sprd_scufreq_attr, attr)
#define to_scufreq_clst(k)	container_of(k, struct scufreq_clst, kobj)
#define to_scufreq_dev(k)	container_of(k, struct scufreq_dev_data, kobj)

#define SCUFREQ_ATTR_RW(_name)						\
static struct sprd_scufreq_attr _name =					\
__ATTR(_name, 0644, show_##_name, store_##_name)

#define SCUFREQ_ATTR_RO(_name)						\
static struct sprd_scufreq_attr _name =					\
__ATTR(_name, 0444, show_##_name, NULL)

#define show_attr(name)							\
static ssize_t show_##name(struct kobject *kobj, struct attribute *attr, char *buf)	\
{									\
	struct scufreq_clst *clst = to_scufreq_clst(kobj);		\
	return scnprintf(buf, PAGE_SIZE, "%u\n", clst->name);		\
}

#define store_attr(name, _min, _max)					\
static ssize_t store_##name(struct kobject *kobj, struct attribute *attr, const char *buf, size_t count)	\
{									\
	struct scufreq_clst *clst = to_scufreq_clst(kobj);		\
	unsigned int val;						\
	int ret = kstrtouint(buf, 10, &val);				\
	if (ret < 0)							\
		return ret;						\
	clst->name = clamp(val, (unsigned int)(_min), (unsigned int)(_max));	\
	return count;							\
}

static ssize_t store_min_idx(struct kobject *kobj, struct attribute *attr, const char *buf, size_t count)
{
	struct scufreq_clst *clst = to_scufreq_clst(kobj);
	unsigned int val;
	int ret;

	ret = kstrtouint(buf, 10, &val);
	if (ret < 0)
		return ret;
	clst->min_idx = clamp(val, clst->clst_min_idx, clst->max_idx);

	return count;
}

static ssize_t store_max_idx(struct kobject *kobj, struct attribute *attr, const char *buf, size_t count)
{
	struct scufreq_clst *clst = to_scufreq_clst(kobj);
	unsigned int val;
	int ret;

	ret = kstrtouint(buf, 10, &val);
	if (ret < 0)
		return ret;
	clst->max_idx = clamp(val, clst->min_idx, clst->clst_max_idx);

	return count;
}

static ssize_t show_freq_tbl(struct kobject *kobj, struct attribute *attr, char *buf)
{
	struct scufreq_clst *clst = to_scufreq_clst(kobj);
	struct cpufreq_scuidx_map *map = clst->freq_tbl;
	unsigned int cnt = 0;

	cnt += scnprintf(buf, PAGE_SIZE, "CPU freq (MHz)\tSCU index\n");
	while (map->cpufreq_mhz && cnt < PAGE_SIZE) {
		cnt += scnprintf(buf + cnt, PAGE_SIZE - cnt, "%14u\t%14u\n", map->cpufreq_mhz, map->scu_idx);
		map++;
	}

	return cnt;
}

/*
 * SYSFS interfaces:
 * /sys/devices/system/cpu/scufreq/enable			[rw]
 * /sys/devices/system/cpu/scufreq/boost_idx			[rw]	[debug]
 * /sys/devices/system/cpu/scufreq/hw_cur_idx			[ro]	the SCU index the firmware reports (error in autotune mode)
 * /sys/devices/system/cpu/scufreq/cur_idx			[ro]	the index scufreq committed last
 * /sys/devices/system/cpu/scufreq/clstX/{min_idx,max_idx,cur_idx,rfl_ratio_ceil,stl_fe_flr,stl_be_flr,freq_comp_pct,wb_pct_thres,
 *	wb_filt_rfl,freq_comp_ceil_mhz,freq_comp_flr_mhz,freq_tbl}
 */
show_attr(min_idx);
show_attr(max_idx);
show_attr(cur_idx);
show_attr(rfl_ratio_ceil);
store_attr(rfl_ratio_ceil, 1U, 50000U);
show_attr(stl_fe_flr);
store_attr(stl_fe_flr, 0U, 100U);
show_attr(stl_be_flr);
store_attr(stl_be_flr, 0U, 100U);
show_attr(freq_comp_pct);
store_attr(freq_comp_pct, 0U, 1000U);
show_attr(wb_pct_thres);
store_attr(wb_pct_thres, 0U, 100U);
show_attr(wb_filt_rfl);
store_attr(wb_filt_rfl, 0U, 50000U);
store_attr(freq_comp_ceil_mhz, 0U, 5000U);
show_attr(freq_comp_ceil_mhz);
store_attr(freq_comp_flr_mhz, 0U, 5000U);
show_attr(freq_comp_flr_mhz);

SCUFREQ_ATTR_RW(min_idx);
SCUFREQ_ATTR_RW(max_idx);
SCUFREQ_ATTR_RO(freq_tbl);
SCUFREQ_ATTR_RO(cur_idx);
SCUFREQ_ATTR_RW(rfl_ratio_ceil);
SCUFREQ_ATTR_RW(stl_fe_flr);
SCUFREQ_ATTR_RW(stl_be_flr);
SCUFREQ_ATTR_RW(freq_comp_pct);
SCUFREQ_ATTR_RW(wb_pct_thres);
SCUFREQ_ATTR_RW(wb_filt_rfl);
SCUFREQ_ATTR_RW(freq_comp_ceil_mhz);
SCUFREQ_ATTR_RW(freq_comp_flr_mhz);

static struct attribute *scufreq_clst_attrs[] = {
	&min_idx.attr,
	&max_idx.attr,
	&freq_tbl.attr,
	&cur_idx.attr,
	&rfl_ratio_ceil.attr,
	&stl_fe_flr.attr,
	&stl_be_flr.attr,
	&freq_comp_pct.attr,
	&wb_pct_thres.attr,
	&wb_filt_rfl.attr,
	&freq_comp_ceil_mhz.attr,
	&freq_comp_flr_mhz.attr,
	NULL,
};
ATTRIBUTE_GROUPS(scufreq_clst);

static ssize_t attr_show(struct kobject *kobj, struct attribute *attr, char *buf)
{
	struct sprd_scufreq_attr *scufreq_attr = to_scufreq_attr(attr);

	return scufreq_attr->show ? scufreq_attr->show(kobj, attr, buf) : -EIO;
}

static ssize_t attr_store(struct kobject *kobj, struct attribute *attr, const char *buf, size_t count)
{
	struct sprd_scufreq_attr *scufreq_attr = to_scufreq_attr(attr);

	return scufreq_attr->store ? scufreq_attr->store(kobj, attr, buf, count) : -EIO;
}

static const struct sysfs_ops scufreq_sysfs_ops = {
	.show	= attr_show,
	.store	= attr_store,
};

static const struct kobj_type scufreq_clst_ktype = {
	.sysfs_ops	= &scufreq_sysfs_ops,
	.default_groups	= scufreq_clst_groups,
};

static int sprd_scufreq_commit_scu_freq(u32 idx);

static ssize_t show_enable(struct kobject *kobj, struct attribute *attr, char *buf)
{
	struct scufreq_dev_data *dev = to_scufreq_dev(kobj);

	return scnprintf(buf, PAGE_SIZE, "%u\n", dev->enable);
}

static ssize_t store_enable(struct kobject *kobj, struct attribute *attr, const char *buf, size_t count)
{
	struct scufreq_dev_data *dev = to_scufreq_dev(kobj);
	bool val;
	int ret;

	ret = kstrtobool(buf, &val);
	if (ret < 0)
		return ret;

	mutex_lock(&dev->ena_lock);
	if (val == dev->enable) {
		mutex_unlock(&dev->ena_lock);
		return count;
	}
	dev->enable = val;
	if (!val) {
		/* back to the firmware's autotune; the next enable commits again whatever the index is */
		dev->cur_idx = SCU_IDX_UNKNOWN;
		ret = scu_ops->scu_set(SCUFREQ_HW_ENABLE, 0);
		if (ret)
			dev_err(dev->dev, "failed to set scu: %d\n", ret);
	}
	mutex_unlock(&dev->ena_lock);

	/* outside the lock: stopping the sampling waits for the work that takes it */
	ret = sprd_pmu_sample_enable(val);
	if (ret)
		dev_err(dev->dev, "cannot %s the PMU sampling: %d\n", val ? "start" : "stop", ret);

	return count;
}

static ssize_t show_boost_idx(struct kobject *kobj, struct attribute *attr, char *buf)
{
	struct scufreq_dev_data *dev = to_scufreq_dev(kobj);

	return scnprintf(buf, PAGE_SIZE, "%u\n", dev->boost_idx);
}

static ssize_t store_boost_idx(struct kobject *kobj, struct attribute *attr, const char *buf, size_t count)
{
	struct scufreq_dev_data *dev = to_scufreq_dev(kobj);
	unsigned int val;
	u32 new_idx;
	int ret;

	ret = kstrtouint(buf, 10, &val);
	if (ret < 0)
		return ret;

	dev->boost_idx = val;

	/* re-aggregate the votes after the boost update */
	new_idx = max(dev->boost_idx, dev->cur_idx == SCU_IDX_UNKNOWN ? 0 : dev->cur_idx);
	if (new_idx != dev->cur_idx) {
		ret = sprd_scufreq_commit_scu_freq(new_idx);
		if (ret < 0)
			dev_err(dev->dev, "Error setting boost freq: %d\n", ret);
	}

	return count;
}

static ssize_t show_hw_cur_idx(struct kobject *kobj, struct attribute *attr, char *buf)
{
	u32 idx = 0;
	int ret;

	ret = scu_ops->scu_get(&idx);
	if (ret)
		return scnprintf(buf, PAGE_SIZE, "autotune\n");

	return scnprintf(buf, PAGE_SIZE, "%u\n", idx);
}

static ssize_t show_dev_cur_idx(struct kobject *kobj, struct attribute *attr, char *buf)
{
	struct scufreq_dev_data *dev = to_scufreq_dev(kobj);

	if (dev->cur_idx == SCU_IDX_UNKNOWN)
		return scnprintf(buf, PAGE_SIZE, "none\n");

	return scnprintf(buf, PAGE_SIZE, "%u\n", dev->cur_idx);
}

SCUFREQ_ATTR_RW(enable);
SCUFREQ_ATTR_RW(boost_idx);
SCUFREQ_ATTR_RO(hw_cur_idx);
static struct sprd_scufreq_attr dev_cur_idx = __ATTR(cur_idx, 0444, show_dev_cur_idx, NULL);

static struct attribute *scufreq_settings_attrs[] = {
	&enable.attr,
	&boost_idx.attr,
	&hw_cur_idx.attr,
	&dev_cur_idx.attr,
	NULL,
};
ATTRIBUTE_GROUPS(scufreq_settings);

static const struct kobj_type scufreq_settings_ktype = {
	.sysfs_ops	= &scufreq_sysfs_ops,
	.default_groups	= scufreq_settings_groups,
};

static u32 sprd_scufreq_cpufreq_to_scuidx(struct scufreq_clst *clst, u32 cpu_mhz)
{
	struct cpufreq_scuidx_map *map = clst->freq_tbl;

	if (!map)
		return 0;

	while (map->cpufreq_mhz && map->cpufreq_mhz < cpu_mhz)
		map++;
	if (!map->cpufreq_mhz)
		map--;

	return map->scu_idx;
}

static void sprd_scufreq_calc_sampling_stats(struct sprd_pmu_sample_data *data)
{
	int cpu;

	for (cpu = 0; cpu < MAX_CPU_NUM; cpu++) {
		struct cpu_stats *stats = &sampling_stats[cpu];
		struct cpu_ctrs *delta = &data->delta[cpu];
		u64 cyc = delta->ctrs[CYC_IDX];

		stats->freq_mhz = data->freq_mhz[cpu];
		if (!data->ev_ids[FE_STALL_IDX] || !cyc)
			stats->stl_fe_pct = 0;
		else
			stats->stl_fe_pct = div64_u64(100 * delta->ctrs[FE_STALL_IDX], cyc);
		if (!data->ev_ids[BE_STALL_IDX] || !cyc)
			stats->stl_be_pct = 0;
		else
			stats->stl_be_pct = div64_u64(100 * delta->ctrs[BE_STALL_IDX], cyc);

		stats->rfl_ratio = delta->ctrs[INST_IDX];
		if (delta->ctrs[MISS_IDX])
			stats->rfl_ratio = div64_u64(delta->ctrs[INST_IDX], delta->ctrs[MISS_IDX]);

		if (!data->ev_ids[WB_IDX] || !data->ev_ids[ACC_IDX] || !delta->ctrs[ACC_IDX])
			stats->wb_pct = 0;
		else
			stats->wb_pct = div64_u64(100 * delta->ctrs[WB_IDX], delta->ctrs[ACC_IDX]);
	}
}

static inline void sprd_scufreq_set_higher_freq(int *max_cpu, int cpu, u32 *max_cpufreq, u32 cpufreq)
{
	if (cpufreq > *max_cpufreq) {
		*max_cpu = cpu;
		*max_cpufreq = cpufreq;
	}
}

static void sprd_scufreq_calc_clst_sampling_freq(struct scufreq_clst *clst)
{
	struct cpu_stats *stats;
	int cpu, max_cpu = cpumask_first(&clst->cpus);
	u32 max_scuidx, max_cpufreq = 0;
	u32 max_cpufreq_scaled = 0, rfl_ratio_diff;

	for_each_cpu(cpu, &clst->cpus) {
		if (cpu >= MAX_CPU_NUM)
			break;

		stats = &sampling_stats[cpu];
		if (stats->wb_pct >= clst->wb_pct_thres && stats->rfl_ratio <= clst->wb_filt_rfl) {
			sprd_scufreq_set_higher_freq(&max_cpu, cpu, &max_cpufreq, stats->freq_mhz);
		} else if (stats->rfl_ratio <= clst->rfl_ratio_ceil) {
			rfl_ratio_diff = clst->rfl_ratio_ceil - stats->rfl_ratio;
			max_cpufreq_scaled = stats->freq_mhz;
			if (clst->freq_comp_pct &&
			    (stats->freq_mhz > clst->freq_comp_flr_mhz && stats->freq_mhz < clst->freq_comp_ceil_mhz) &&
			    (stats->stl_fe_pct >= clst->stl_fe_flr || stats->stl_be_pct >= clst->stl_be_flr)) {
				max_cpufreq_scaled += (stats->freq_mhz * rfl_ratio_diff * clst->freq_comp_pct) /
						      (clst->rfl_ratio_ceil * 100);
				max_cpufreq_scaled = min(clst->freq_comp_ceil_mhz, max_cpufreq_scaled);
			}
			sprd_scufreq_set_higher_freq(&max_cpu, cpu, &max_cpufreq, max_cpufreq_scaled);
		}
	}

	max_scuidx = sprd_scufreq_cpufreq_to_scuidx(clst, max_cpufreq);
	max_scuidx = max(max_scuidx, clst->min_idx);
	max_scuidx = min(max_scuidx, clst->max_idx);

	clst->cur_idx = max_scuidx;
}

/* caller holds nothing; returns -EAGAIN when the firmware refuses */
static int sprd_scufreq_commit_scu_freq(u32 idx)
{
	int ret = 0;

	mutex_lock(&scufreq_data->ena_lock);
	if (!scufreq_data->enable || scufreq_data->is_boost)
		goto unlock_out;

	ret = scu_ops->scu_set(SCUFREQ_SOFT_ENABLE, idx);
	if (ret) {
		dev_err(scufreq_data->dev, "failed to set scu: %d\n", ret);
		ret = -EAGAIN;
		goto unlock_out;
	}
	scufreq_data->cur_idx = idx;

unlock_out:
	mutex_unlock(&scufreq_data->ena_lock);

	return ret;
}

static int sprd_scufreq_pmu_sample_cb(struct notifier_block *nb, unsigned long action, void *data)
{
	struct sprd_pmu_sample_data *sample_data = data;
	struct scufreq_clst *clst;
	u32 max_idx = 0, new_idx;
	bool skip;
	int i, ret;

	mutex_lock(&scufreq_data->ena_lock);
	skip = !scufreq_data->enable || scufreq_data->is_boost;
	mutex_unlock(&scufreq_data->ena_lock);
	if (skip)
		return NOTIFY_OK;

	sprd_scufreq_calc_sampling_stats(sample_data);

	for (i = 0; i < scufreq_data->num_clsts; i++) {
		clst = &scufreq_data->clsts[i];
		sprd_scufreq_calc_clst_sampling_freq(clst);
		max_idx = max(clst->cur_idx, max_idx);
	}

	new_idx = max(scufreq_data->boost_idx, max_idx);
	if (new_idx != scufreq_data->cur_idx) {
		ret = sprd_scufreq_commit_scu_freq(new_idx);
		if (ret < 0)
			dev_err(scufreq_data->dev, "Error committing scu freq: %d\n", ret);
	}

	return NOTIFY_OK;
}

static struct notifier_block pmu_sample_nb = {
	.notifier_call = sprd_scufreq_pmu_sample_cb,
};

static int sprd_scufreq_init_cpufreq_scuidx_map(struct device *dev, struct scufreq_clst *clst, struct sprd_dvfs_scu_info *scu_info)
{
	struct cpufreq_scuidx_map *tbl;
	int i;

	clst->freq_tbl_len = scu_info->dvfs_num;

	tbl = devm_kcalloc(dev, clst->freq_tbl_len + 1, sizeof(*tbl), GFP_KERNEL);
	if (!tbl)
		return -ENOMEM;

	for (i = 0; i < clst->freq_tbl_len; i++) {
		tbl[i].cpufreq_mhz = scu_info->scu_table[i].cpu_freq / 1000000;
		tbl[i].scu_idx = scu_info->scu_table[i].scu_idx;
		dev_dbg(dev, "Entry%d CPU:%u, SCU:%u\n", i, tbl[i].cpufreq_mhz, tbl[i].scu_idx);
	}
	tbl[i].cpufreq_mhz = 0;
	clst->freq_tbl = tbl;

	return 0;
}

/* a minimum frequency request above the lowest frequency is a boost: leave the SCU to the firmware meanwhile */
static int sprd_scufreq_boost_detect(struct notifier_block *nb, unsigned long freq_limit_min, void *ptr)
{
	struct scufreq_clst *clst = container_of(nb, struct scufreq_clst, freq_limit_nb);
	bool is_boost = false;
	int i, ret;

	mutex_lock(&scufreq_data->ena_lock);
	clst->is_boost = freq_limit_min > clst->policy->cpuinfo.min_freq;
	for (i = 0; i < scufreq_data->num_clsts; i++)
		is_boost = is_boost || scufreq_data->clsts[i].is_boost;

	if (scufreq_data->enable && scufreq_data->is_boost != is_boost && is_boost) {
		scufreq_data->cur_idx = SCU_IDX_UNKNOWN;
		ret = scu_ops->scu_set(SCUFREQ_HW_ENABLE, 0);
		if (ret)
			dev_err(scufreq_data->dev, "failed to set scu: %d\n", ret);
	}
	scufreq_data->is_boost = is_boost;
	mutex_unlock(&scufreq_data->ena_lock);

	return NOTIFY_OK;
}

static void sprd_scufreq_freq_limit_notif_register(void)
{
	int i, ret;

	for (i = 0; i < scufreq_data->num_clsts; i++) {
		struct scufreq_clst *clst = &scufreq_data->clsts[i];
		struct cpufreq_policy *policy = cpufreq_cpu_get(cpumask_first(&clst->cpus));

		if (!policy) {
			dev_warn(scufreq_data->dev, "clst%d has no cpufreq policy, boost is not detected\n", i);
			continue;
		}

		clst->policy = policy;
		clst->freq_limit_nb.notifier_call = sprd_scufreq_boost_detect;
		ret = freq_qos_add_notifier(&policy->constraints, FREQ_QOS_MIN, &clst->freq_limit_nb);
		if (ret)
			dev_err(scufreq_data->dev, "freq limit notifier register failed\n");
		cpufreq_cpu_put(policy);
	}
}

static int sprd_scufreq_suspend(void)
{
	int ret;

	mutex_lock(&scufreq_data->ena_lock);
	if (!scufreq_data->enable) {
		mutex_unlock(&scufreq_data->ena_lock);
		return 0;
	}

	scufreq_data->cur_idx = SCU_IDX_UNKNOWN;
	ret = scu_ops->scu_set(SCUFREQ_HW_ENABLE, 0);
	if (ret) {
		dev_err(scufreq_data->dev, "failed to set scu: %d\n", ret);
		ret = -EAGAIN;
	}
	mutex_unlock(&scufreq_data->ena_lock);

	return ret;
}

static struct syscore_ops scufreq_syscore_ops = {
	.suspend = sprd_scufreq_suspend,
};

static int sprd_scufreq_clst_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	int i, ret;

	for (i = 0; i < scufreq_data->num_clsts; i++) {
		struct scufreq_clst *clst = &scufreq_data->clsts[i];
		struct sprd_dvfs_scu_info *info = &scu_data->scu_info[i];

		clst->index = i;
		clst->cpus = info->cpus;
		if (!cpumask_weight(&clst->cpus) || !info->dvfs_num)
			return -EPROBE_DEFER;	/* the cpufreq policies are not up yet */

		clst->rfl_ratio_ceil = 400;
		clst->stl_fe_flr = 100;
		clst->stl_be_flr = 100;
		clst->freq_comp_pct = 0;
		clst->wb_pct_thres = 100;
		clst->wb_filt_rfl = 25000;

		ret = sprd_scufreq_init_cpufreq_scuidx_map(dev, clst, info);
		if (ret < 0) {
			dev_err(dev, "clst%d error importing cpufreq-scuidx table\n", clst->index);
			return ret;
		}

		clst->freq_comp_ceil_mhz = clst->freq_tbl[clst->freq_tbl_len - 1].cpufreq_mhz;
		clst->freq_comp_flr_mhz = 0;
		clst->clst_min_idx = sprd_scufreq_cpufreq_to_scuidx(clst, 0);
		clst->clst_max_idx = sprd_scufreq_cpufreq_to_scuidx(clst, U32_MAX);
		clst->min_idx = clst->clst_min_idx;
		clst->max_idx = clst->clst_max_idx;
		clst->cur_idx = clst->min_idx;
		clst->inited = true;
	}

	return 0;
}

static int sprd_scufreq_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct scufreq_dev_data *dev_data;
	struct device *dev_root;
	int i, ret;

	if (!u30pro_scufreq)
		return -ENODEV;

	scu_data = cpufreq_get_driver_data();
	if (!scu_data || !scu_data->scu_ops.scu_set)
		return -EPROBE_DEFER;	/* the cpufreq driver registers later */

	scu_ops = &scu_data->scu_ops;

	dev_data = devm_kzalloc(dev, sizeof(*dev_data), GFP_KERNEL);
	if (!dev_data)
		return -ENOMEM;

	dev_data->dev = dev;
	dev_data->enable = true;
	dev_data->cur_idx = SCU_IDX_UNKNOWN;
	mutex_init(&dev_data->ena_lock);
	dev_data->num_clsts = scu_data->cluster_num;
	if (!dev_data->num_clsts) {
		dev_err(dev, "No scufreq clsts provided\n");
		return -ENODEV;
	}

	dev_data->clsts = devm_kcalloc(dev, dev_data->num_clsts, sizeof(*dev_data->clsts), GFP_KERNEL);
	if (!dev_data->clsts)
		return -ENOMEM;

	scufreq_data = dev_data;
	ret = sprd_scufreq_clst_probe(pdev);
	if (ret < 0) {
		scufreq_data = NULL;
		if (ret != -EPROBE_DEFER)
			dev_err(dev, "failed to probe scufreq clsts: %d\n", ret);
		return ret;
	}

	dev_root = bus_get_dev_root(&cpu_subsys);
	if (!dev_root) {
		scufreq_data = NULL;
		return -ENODEV;
	}
	ret = kobject_init_and_add(&dev_data->kobj, &scufreq_settings_ktype, &dev_root->kobj, "scufreq");
	put_device(dev_root);
	if (ret < 0) {
		dev_err(dev, "failed to init scufreq settings kobj: %d\n", ret);
		kobject_put(&dev_data->kobj);
		scufreq_data = NULL;
		return ret;
	}

	for (i = 0; i < dev_data->num_clsts; i++) {
		struct scufreq_clst *clst = &dev_data->clsts[i];

		ret = kobject_init_and_add(&clst->kobj, &scufreq_clst_ktype, &dev_data->kobj, "clst%d", clst->index);
		if (ret < 0) {
			dev_err(dev, "failed to init scufreq clst%d kobj: %d\n", clst->index, ret);
			kobject_put(&clst->kobj);
			clst->inited = false;
		}
	}

	register_syscore_ops(&scufreq_syscore_ops);
	sprd_scufreq_freq_limit_notif_register();

	ret = sprd_pmu_sample_notify_register(&pmu_sample_nb);
	if (ret < 0) {
		dev_err(dev, "failed to register the PMU sampling notifier: %d\n", ret);
		return ret;
	}

	ret = sprd_pmu_sample_enable(true);
	if (ret < 0) {
		dev_err(dev, "failed to start the PMU sampling: %d\n", ret);
		dev_data->enable = false;
		return ret;
	}

	dev_info(dev, "software SCU frequency control on (%u clusters)\n", dev_data->num_clsts);

	return 0;
}

static const struct of_device_id scufreq_match_table[] = {
	{ .compatible = "sprd,scufreq" },
	{ /* sentinel */ },
};

static struct platform_driver sprd_scufreq_driver = {
	.probe = sprd_scufreq_probe,
	.driver = {
		.name = "sprd-scufreq",
		.of_match_table = scufreq_match_table,
		.suppress_bind_attrs = true,
	},
};
/* No module_exit: active PMU hooks must not outlive an unloaded module. */
static int __init sprd_scufreq_register(void)
{
    return platform_driver_register(&sprd_scufreq_driver);
}
module_init(sprd_scufreq_register);

MODULE_DESCRIPTION("Unisoc software SCU frequency scaling");
MODULE_LICENSE("GPL");
