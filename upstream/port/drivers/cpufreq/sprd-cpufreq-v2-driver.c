// SPDX-License-Identifier: GPL-2.0
// Copyright (C) 2021 Unisoc, Inc.
#define pr_fmt(fmt) "sprd-apcpu-dvfs: " fmt

#include "sprd-cpufreq-v2.h"
#include "sprd-cpufreq-v2-voltage.h"
#include <linux/capability.h>
#include <linux/jiffies.h>
#include <linux/moduleparam.h>
#include <linux/nvmem-consumer.h>

#define low_check(temp)		((int)(temp) < (int)DVFS_TEMP_LOW_LIMIT)
#define upper_check(temp)	((int)(temp) >= (int)DVFS_TEMP_UPPER_LIMIT)
#define temp_check(temp)	(low_check(temp) || upper_check(temp))

#define ON_BOOST		0
#define OUT_BOOST		1
#define SPRD_CPUFREQ_BOOST_DURATION	(60ul * HZ)
#define SPRD_DVFS_DEBUG_MAGIC		(0x5A)

struct cluster_prop {
	char *name;
	u32 *value;
	void **ops;
};

static struct device *dev;
static struct cluster_info *pclusters;
static unsigned long boot_done_timestamp;

/*
 * MU300: the CPU voltage offset (EXPERIMENTAL; docs/FINDINGS.md 133, based on kanoqwq/mu300-linux@3386928).
 *
 * The firmware applies an offset only inside DVFS_INIT, once per boot, so it cannot be changed at run time and has to
 * be known before DVFS starts. Persistent settings live on the Linux filesystem, which only the initramfs can read,
 * so the driver (built in, as before) probes as before and then holds DVFS_INIT and the cpufreq registration until
 * the initramfs writes voltage_offset_uv - "0,0,0" for the stock call, which is every boot without the setting.
 * Nothing written within SPRD_VOLT_WAIT_S seconds (an initramfs without this step, a boot without a filesystem): DVFS
 * starts at the stock voltages by itself. One value per boot, checked whole before anything is applied: inside
 * -25..+12.5 mV, whole 3.125 mV steps, UMS9620 only, a raise only with a known DCDC maximum (and an OPP whose raised
 * voltage would go above that maximum is left out of the table). The command line can never set it (the parameter
 * refuses anything before userspace runs), and only CAP_SYS_ADMIN can write it. Whether an offset is ever written
 * at all (the boot counter that takes it back after a crash) is the initramfs' decision (boot/init, cpu-voltage).
 */
enum sprd_volt_state {
	SV_PROBING,	/* not probed (yet), or the probe failed */
	SV_WAITING,	/* probed: DVFS_INIT waits for voltage_offset_uv or the timer */
	SV_QUEUED,	/* a value was accepted, DVFS_INIT is about to run */
	SV_APPLIED,	/* DVFS runs with the value written */
	SV_TIMEOUT,	/* DVFS runs at the stock voltages: nothing was written in time */
	SV_FAILED,	/* DVFS_INIT or the cpufreq registration failed */
	SV_DISABLED,	/* androidboot.dvfs_set debug magic: no DVFS at all (vendor behaviour) */
};
static const char * const sprd_volt_state_names[] = {
	"probing", "waiting", "queued", "applied", "timeout", "failed", "disabled",
};
static DEFINE_MUTEX(sprd_volt_lock);
static enum sprd_volt_state sprd_volt_state;
static int sprd_volt_req[SPRD_VOLT_DOMAINS];
static int sprd_volt_applied[SPRD_VOLT_DOMAINS];
static u32 sprd_dvfs_base_flag;
static int sprd_volt_dcdc_max_uv;	/* lowest regulator-max-microvolt of the CPU DCDCs; 0: unknown, no raise */

static void sprd_volt_start_work(struct work_struct *work);
static void sprd_volt_dcdc_limit(void);
static DECLARE_DELAYED_WORK(sprd_volt_work, sprd_volt_start_work);

/* a raised domain: an OPP above the DCDC maximum is never offered to cpufreq */
static bool sprd_volt_over_limit(struct cluster_info *cluster, u64 vol)
{
	return cluster->id < SPRD_VOLT_DOMAINS && READ_ONCE(sprd_volt_applied[cluster->id]) > 0 &&
	       sprd_volt_dcdc_max_uv > 0 && vol > (u64)sprd_volt_dcdc_max_uv;
}

/* cluster common interface */
static struct cluster_info *sprd_cluster_info(u32 cpu_idx)
{
	u32 index;

	if (!pclusters || cpu_idx >= nr_cpu_ids)
		return NULL;

	index = topology_cluster_id(cpu_idx);

	return pclusters + index;
}

static int sprd_cpufreq_boost_judge(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;

	if (time_after(jiffies, boot_done_timestamp)) {
		cluster->boost_enable = false;
		pr_info("Disables boost it is %lu seconds after boot up\n",
			SPRD_CPUFREQ_BOOST_DURATION / HZ);
	}

	if (cluster->boost_enable) {
		if (policy->max >= policy->cpuinfo.max_freq)
			return ON_BOOST;
		cluster->boost_enable = false;
		pr_info("Disables boost due to policy max(%d<%d)\n",
			policy->max, policy->cpuinfo.max_freq);
	}

	return OUT_BOOST;
}

static int sprd_nvmem_info_read(struct device_node *node, const char *name, u32 *value)
{
	struct nvmem_cell *cell;
	void *buf;
	size_t len = 0;
	int ret = 0;

	cell = of_nvmem_cell_get(node, name);
	if (IS_ERR(cell)) {
		ret = PTR_ERR(cell);
		if (ret == -EPROBE_DEFER)
			pr_warn("cell for cpufreq not ready, retry\n");
		else
			pr_err("failed to get cell for cpufreq\n");

		return ret;
	}

	buf = nvmem_cell_read(cell, &len);
	if (IS_ERR(buf)) {
		nvmem_cell_put(cell);
		return PTR_ERR(buf);
	}

	if (len > sizeof(u32))
		len = sizeof(u32);

	memcpy(value, buf, len);

	kfree(buf);
	nvmem_cell_put(cell);

	return 0;
}

static int sprd_temp_list_init(struct list_head *head)
{
	struct temp_node *node;

	if (!list_empty_careful(head)) {
		dev_warn(dev, "%s: temp list is also init\n", __func__);
		return 0;
	}

	/* add upper temp node */
	node = devm_kzalloc(dev, sizeof(*node), GFP_KERNEL);
	if (!node) {
		dev_err(dev, "%s: alloc for upper temp node error\n", __func__);
		return -ENOMEM;
	}

	node->temp = DVFS_TEMP_UPPER_LIMIT;

	list_add(&node->list, head);

	/* add low temp node */
	node = devm_kzalloc(dev, sizeof(*node), GFP_KERNEL);
	if (!node) {
		dev_err(dev, "%s: alloc for low temp node error\n", __func__);
		return -ENOMEM;
	}

	node->temp = DVFS_TEMP_LOW_LIMIT;

	list_add(&node->list, head);

	return 0;
}

static int sprd_temp_list_add(struct list_head *head, int temp)
{
	struct temp_node *node, *pos;

	if (temp_check(temp) || list_empty_careful(head)) {
		dev_err(dev, "%s: temp %d or list is error\n", __func__, temp);
		return -EINVAL;
	}

	list_for_each_entry(pos, head, list) {
		if (temp == pos->temp)
			return 0;
		if (temp < pos->temp)
			break;
	}

	node = devm_kzalloc(dev, sizeof(*node), GFP_KERNEL);
	if (!node) {
		dev_err(dev, "%s: alloc for temp node error\n", __func__);
		return -ENOMEM;
	}

	node->temp = temp;

	list_add_tail(&node->list, &pos->list);

	return 0;
}

static struct temp_node *sprd_temp_list_find(struct list_head *head, int temp)
{
	struct temp_node *pos, *next;

	if (temp_check(temp)) {
		dev_err(dev, "%s: temp %d is out of range\n", __func__, temp);
		return NULL;
	}

	list_for_each_entry(pos, head, list) {
		next = list_entry(pos->list.next, typeof(*pos), list);
		if ((temp >= pos->temp) && (temp < next->temp))
			break;
	}

	return pos;
}

static u32 sprd_cpufreq_get_debug_flag(void)
{
	struct device_node *cmdline_node;
	const char *cmd_line, *dvfs_set;
	u32 value = 0;
	int ret;

	cmdline_node = of_find_node_by_path("/chosen");
	ret = of_property_read_string(cmdline_node, "bootargs", &cmd_line);

	if (ret) {
		pr_err("Fail to find cmdline bootargs property\n");
		return 0;
	}

	dvfs_set = strstr(cmd_line, "androidboot.dvfs_set=0x");
	if (!dvfs_set) {
		pr_err("no property sprdboot.dvfs_set found\n");
		return 0;
	}

	sscanf(dvfs_set, "androidboot.dvfs_set=0x%x", &value);

	return value;
}

static void sprd_cluster_get_supply_mode(char *dcdc_supply)
{
	struct device_node *cmdline_node;
	const char *cmd_line, *dcdc_type, *ver_str = "-v2";
	int value = -1, ret;

	cmdline_node = of_find_node_by_path("/chosen");
	ret = of_property_read_string(cmdline_node, "bootargs", &cmd_line);

	if (ret) {
		pr_err("Fail to find cmdline bootargs property\n");
		return;
	}

	dcdc_type = strstr(cmd_line, "power.from.extern=");
	if (!dcdc_type) {
		pr_info("no property power.from.extern found\n");
		return;
	}

	sscanf(dcdc_type, "power.from.extern=%d", &value);
	if (value)
		return;

	strcat(dcdc_supply, ver_str);
}

static int sprd_policy_table_update(struct cpufreq_policy *policy, struct temp_node *node)
{
	struct cpufreq_frequency_table *new_table __maybe_unused;
	struct cluster_info *cluster;
	struct device *cpu;
	u64 freq, vol;
	int i, ret;

	cpu = get_cpu_device(policy->cpu);
	if (!cpu) {
		dev_err(dev, "%s: get cpu %u dev error\n", __func__, policy->cpu);
		return -EINVAL;
	}

	cluster = (struct cluster_info *)policy->driver_data;
	if (!cluster || !cluster->table_update || !cluster->pair_get) {
		dev_err(dev, "%s: get cpu %u cluster info error\n", __func__, policy->cpu);
		return -EINVAL;
	}

	dev_info(dev, "%s: update cluster %u temp %d dvfs table\n", __func__, cluster->id, node->temp);

	ret = cluster->table_update(cluster->id, node->temp, &cluster->table_entry_num);
	if (ret) {
		dev_err(dev, "%s: update cluster %u temp %d table error\n", __func__, cluster->id, node->temp);
		return -EINVAL;
	}

	dev_dbg(dev, "%s: cluster %u dvfs table entry num is %u\n", __func__, cluster->id, cluster->table_entry_num);

	for (i = 0; i < cluster->table_entry_num; ++i) {
		ret = cluster->pair_get(cluster->id, i, &freq, &vol);
		if (ret) {
			dev_err(dev, "%s: get cluster %u index %u pair error\n", __func__, cluster->id, i);
			return -EINVAL;
		}

		/*
		 * MU300: with a raised voltage, the first OPP above the DCDC maximum ends the table (the firmware's index
		 * is the table's, so only the top can be cut); a stale OPP of an earlier table goes too.
		 */
		if (sprd_volt_over_limit(cluster, vol)) {
			u32 j, kept = i;

			dev_warn(dev, "%s: cluster %u: %lluHz needs %lluuV with the +%duV offset, above the DCDC maximum %duV: not used from here on\n",
				 __func__, cluster->id, freq, vol, sprd_volt_applied[cluster->id], sprd_volt_dcdc_max_uv);
			for (j = i; j < cluster->table_entry_num; j++) {
				struct dev_pm_opp *opp;

				if (cluster->pair_get(cluster->id, j, &freq, NULL))
					break;
				opp = dev_pm_opp_find_freq_exact(cpu, freq, true);
				if (!IS_ERR(opp)) {
					dev_pm_opp_put(opp);
					dev_pm_opp_remove(cpu, freq);
				}
			}
			if (!kept) {
				dev_err(dev, "%s: cluster %u: no OPP left under the DCDC maximum\n", __func__, cluster->id);
				return -EINVAL;
			}
			cluster->table_entry_num = kept;
			node->temp_table = NULL;	/* built again from the OPPs that are left */
			break;
		}

		dev_info(dev, "%s: add %lluHz/%lluuV to opp\n", __func__, freq, vol);

		if (policy->freq_table)
			dev_pm_opp_remove(cpu, freq);

		ret = dev_pm_opp_add(cpu, freq, vol);
		if (ret) {
			dev_err(dev, "%s: add %lluHz/%lluuV pair to opp error(%d)\n", __func__, freq, vol, ret);
			return -EINVAL;
		}
	}

	if (node->temp_table) {
		policy->freq_table = node->temp_table;
	} else {
		ret = dev_pm_opp_init_cpufreq_table(cpu, &new_table);
		if (ret) {
			dev_err(dev, "%s: init cluster %u freq table error(%d)\n", __func__, cluster->id, ret);
			return -EINVAL;
		}

		policy->freq_table = new_table;
		node->temp_table = new_table;
	}

	policy->suspend_freq = policy->freq_table[0].frequency;

	return 0;
}

/* sprd_cpufreq_driver interface */
static int sprd_cpufreq_init(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster;
	unsigned int cpumask;
	cpumask_t cluster_cpumask;
	u64 freq = 0;
	int ret;

	cluster = sprd_cluster_info(policy->cpu);
	if (!cluster || !cluster->freq_get || !cluster->dvfs_enable) {
		dev_err(dev, "%s: get cpu %u cluster info error\n", __func__, policy->cpu);
		return -EINVAL;
	}

	mutex_lock(&cluster->mutex);

	/* CPUs in the same cluster share same clock and power domain */
	ret = of_property_read_u32(cluster->node, "sprd,cluster-cpumask",
				   &cpumask);
	if (ret) {
		dev_err(dev, "%s: cpufreq cluster cpumask read fail", __func__);
		goto unlock_ret;
	}

	cluster_cpumask.bits[0] = (unsigned long)cpumask;
	cpumask_or(policy->cpus, policy->cpus, &cluster_cpumask);

	/* init other cpu policy link in same cluster */
	policy->dvfs_possible_from_any_cpu = true;

	policy->transition_delay_us = cluster->transition_delay;
	policy->driver_data = cluster;

	/* init dvfs table use current temp */
	ret = sprd_policy_table_update(policy, cluster->temp_currt_node);
	if (ret) {
		dev_err(dev, "%s: update cluster %u table error\n", __func__, cluster->id);
		goto unlock_ret;
	}

	/* get current freq for policy */
	ret = cluster->freq_get(cluster->id, &freq);
	if (ret) {
		dev_err(dev, "%s: get cluster %u current freq error\n", __func__, cluster->id);
		goto unlock_ret;
	}

	do_div(freq, 1000); /* from Hz to KHz */
	policy->cur = (u32)freq;


	/* enable dvfs phy */
	ret = cluster->dvfs_enable(cluster->id);
	if (ret) {
		dev_err(dev, "%s: enable cluster %u dvfs error\n", __func__, cluster->id);
		goto unlock_ret;
	}

	/* init debugfs interface to debug dvfs */
	ret = sprd_debug_cluster_init(policy);
	if (ret) {
		dev_err(dev, "%s: init cluster %u debug error\n", __func__, cluster->id);
		goto unlock_ret;
	}

	mutex_unlock(&cluster->mutex);

	return 0;

unlock_ret:
	mutex_unlock(&cluster->mutex);

	return -EINVAL;
}

static void sprd_cpufreq_exit(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster;
	struct device *cpu;
	int ret;

	cluster = (struct cluster_info *)policy->driver_data;
	if (!cluster) {
		dev_err(dev, "%s: policy is not init\n", __func__);
		return;
	}

	cpu = get_cpu_device(policy->cpu);
	if (!cpu) {
		dev_err(dev, "%s: get cpu %u device error\n", __func__, policy->cpu);
		return;
	}

	mutex_lock(&cluster->mutex);

	dev_pm_opp_free_cpufreq_table(cpu, &policy->freq_table);
	dev_pm_opp_of_remove_table(cpu);

	ret = sprd_debug_cluster_exit(policy);
	if (ret)
		dev_warn(dev, "%s: cluster %u debug exit error\n", __func__, cluster->id);

	policy->driver_data = NULL;

	mutex_unlock(&cluster->mutex);
}

static int sprd_cpufreq_table_verify(struct cpufreq_policy_data *policy_data)
{
	return cpufreq_generic_frequency_table_verify(policy_data);
}

static int sprd_cpufreq_set_target_index(struct cpufreq_policy *policy, u32 index)
{
	struct cluster_info *cluster;
	unsigned int freq;
	int ret;

	cluster = (struct cluster_info *)policy->driver_data;
	if (!cluster || !cluster->freq_set) {
		dev_err(dev, "%s: policy is not init\n", __func__);
		return -EINVAL;
	}

	if (cluster->boost_enable) {
		ret = sprd_cpufreq_boost_judge(policy);
		if (ret == ON_BOOST)
			return 0;
	}

	mutex_lock(&cluster->mutex);

	if (index >= cluster->table_entry_num) {
		dev_err(dev, "%s: cluster %u index %u is error\n", __func__, cluster->id, index);
		mutex_unlock(&cluster->mutex);
		return -EINVAL;
	}

	ret = cluster->freq_set(cluster->id, index);
	if (ret) {
		dev_err(dev, "%s: set cluster %u index %u error(%d)\n", __func__, cluster->id, index, ret);
		mutex_unlock(&cluster->mutex);
		return -EINVAL;
	}

	freq = policy->freq_table[index].frequency;

	arch_set_freq_scale(policy->related_cpus, freq, policy->cpuinfo.max_freq);

	mutex_unlock(&cluster->mutex);

	return 0;
}

static u32 sprd_cpufreq_get(u32 cpu)
{
	struct cluster_info *cluster;
	u64 freq;
	int ret;

	cluster = sprd_cluster_info(cpu);
	if (!cluster || !cluster->freq_get) {
		dev_err(dev, "%s: get cpu %u cluster info error\n", __func__, cpu);
		return 0;
	}

	mutex_lock(&cluster->mutex);

	ret = cluster->freq_get(cluster->id, &freq);
	if (ret) {
		dev_err(dev, "%s: get cluster %u current freq error\n", __func__, cluster->id);
		mutex_unlock(&cluster->mutex);
		return 0;
	}

	mutex_unlock(&cluster->mutex);

	do_div(freq, 1000); /* from Hz to KHz */
	return (u32)freq;
}

static int sprd_cpufreq_suspend(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;

	if (!strcmp(policy->governor->name, "userspace")) {
		dev_info(dev, "%s: do nothing for governor-%s\n", __func__, policy->governor->name);
		return 0;
	}

	if (cluster->boost_enable) {
		cluster->boost_enable = false;
		sprd_cpufreq_set_target_index(policy, 0);
	}

	return cpufreq_generic_suspend(policy);
}

static int sprd_cpufreq_resume(struct cpufreq_policy *policy)
{
	if (!strcmp(policy->governor->name, "userspace")) {
		dev_info(dev, "%s: do nothing for governor-%s\n", __func__, policy->governor->name);
		return 0;
	}

	return cpufreq_generic_suspend(policy);
}

static int sprd_cpufreq_online(struct cpufreq_policy *policy)
{
	return 0;
}

static int sprd_cpufreq_offline(struct cpufreq_policy *policy)
{
	return 0;
}

/* MU300: read-only views of the firmware's table and of the offset (kanoqwq/mu300-linux@3386928) */
static ssize_t show_scaling_voltage_table(struct cpufreq_policy *policy, char *buf)
{
	struct cluster_info *cluster = policy->driver_data;
	u64 freq, volt;
	int i, ret, len = 0;

	if (!cluster || !cluster->pair_get)
		return -ENODEV;
	mutex_lock(&cluster->mutex);
	for (i = 0; i < cluster->table_entry_num && len < PAGE_SIZE - 64; i++) {
		ret = cluster->pair_get(cluster->id, i, &freq, &volt);
		if (ret) {
			len = -EIO;
			break;
		}
		len += sysfs_emit_at(buf, len, "%llu %llu\n", freq / 1000, volt);
	}
	mutex_unlock(&cluster->mutex);
	return len;
}
cpufreq_freq_attr_ro(scaling_voltage_table);

static ssize_t show_scaling_voltage_offset(struct cpufreq_policy *policy, char *buf)
{
	struct cluster_info *cluster = policy->driver_data;

	if (!cluster)
		return -ENODEV;
	return sysfs_emit(buf, "%d\n", cluster->id < SPRD_VOLT_DOMAINS ? READ_ONCE(sprd_volt_applied[cluster->id]) : 0);
}
cpufreq_freq_attr_ro(scaling_voltage_offset);

static ssize_t show_scaling_voltage_domain(struct cpufreq_policy *policy, char *buf)
{
	struct cluster_info *cluster = policy->driver_data;

	if (!cluster)
		return -ENODEV;
	return sysfs_emit(buf, "%u\n", cluster->id);
}
cpufreq_freq_attr_ro(scaling_voltage_domain);

static struct freq_attr *sprd_cpufreq_attr[] = {
	&scaling_voltage_table, &scaling_voltage_offset, &scaling_voltage_domain, NULL,
};

static struct cpufreq_driver sprd_cpufreq_driver = {
	.name = "sprd-cpufreq-v2",
	.flags = CPUFREQ_NEED_INITIAL_FREQ_CHECK |
		 CPUFREQ_HAVE_GOVERNOR_PER_POLICY |
		 CPUFREQ_IS_COOLING_DEV,
	.init = sprd_cpufreq_init,
	.exit = sprd_cpufreq_exit,
	.verify = sprd_cpufreq_table_verify,
	.target_index = sprd_cpufreq_set_target_index,
	.get = sprd_cpufreq_get,
	.suspend = sprd_cpufreq_suspend,
	.resume = sprd_cpufreq_resume,
	.online = sprd_cpufreq_online,
	.offline = sprd_cpufreq_offline,
	.register_em = cpufreq_register_em_with_opp,
	.attr = sprd_cpufreq_attr,
};

/* init inerface */
static int sprd_cluster_temp_init(struct cluster_info *cluster)
{
	const char *name = "sprd,temp-threshold";
	struct property *prop;
	u32 num, val;
	int i, ret;

	INIT_LIST_HEAD(&cluster->temp_list_head);

	ret = sprd_temp_list_init(&cluster->temp_list_head);
	if (ret) {
		dev_err(dev, "%s: init cluster %u temp limit error\n", __func__, cluster->id);
		return -EINVAL;
	}

	cluster->temp_level_node = sprd_temp_list_find(&cluster->temp_list_head, DVFS_TEMP_LOW_LIMIT);
	cluster->temp_currt_node = cluster->temp_level_node;

	cluster->temp_tick = 0U;

	prop = of_find_property(cluster->node, name, &num);
	if (!prop || !num) {
		dev_warn(dev, "%s: find cluster %u temp property error\n", __func__, cluster->id);
		return 0;
	}

	for (i = 0; i < num / sizeof(u32); i++) {
		ret = of_property_read_u32_index(cluster->node, name, i, &val);
		if (ret) {
			dev_err(dev, "%s: get cluster %u temp error\n", __func__, cluster->id);
			return -EINVAL;
		}

		ret = sprd_temp_list_add(&cluster->temp_list_head, (int)val);
		if (ret) {
			dev_err(dev, "%s: add cluster %u temp error\n", __func__, cluster->id);
			return -EINVAL;
		}
	}

	return 0;
}

static int sprd_cluster_props_init(struct cluster_info *cluster)
{
	int (*ops)(u32 id, u32 val);
	struct cluster_prop *p;
	struct device_node *hwf;
	int i, ret;
	char dcdc_supply[32] = "sprd,pmic-type";
	struct cluster_prop props[] = {
		{
			.name = "sprd,voltage-step",
			.value = &cluster->voltage_step,
			.ops = (void **)&cluster->step_set,
		}, {
			.name = "sprd,voltage-margin",
			.value = &cluster->voltage_margin,
			.ops = (void **)&cluster->margin_set,
		}, {
			.name = "sprd,transition-delay",
			.value = &cluster->transition_delay,
			.ops = NULL,
		}
	};

	for (i = 0; i < ARRAY_SIZE(props); ++i) {
		p = props + i;
		ops = p->ops ? *p->ops : NULL;

		ret = of_property_read_u32(cluster->node, p->name, p->value);
		if (ret) {
			dev_warn(dev, "%s: get cluster %u '%s' value error\n", __func__, cluster->id, p->name);
			*p->value = 0U;
			continue;
		}

		ret = ops ? ops(cluster->id, *p->value) : 0;
		if (ret) {
			dev_err(dev, "%s: set cluster %u '%s' value error\n", __func__, cluster->id, p->name);
			return -EINVAL;
		}
	}

	if (of_property_read_bool(cluster->node, "sprd,multi-supply"))
		sprd_cluster_get_supply_mode(dcdc_supply);

	dev_info(dev, "%s: cluster %u dcdc supply[%s]\n", __func__, cluster->id, dcdc_supply);

	ret = of_property_read_u32(cluster->node, dcdc_supply, &cluster->pmic_type);
	if (!ret) {
		ret = cluster->pmic_set(cluster->id, cluster->pmic_type);
		if (ret) {
			dev_err(dev, "%s: set cluster %u 'pmic-type' value error\n", __func__, cluster->id);
			return -EINVAL;
		}
	}

	if (cluster->id == 0) {
		ret = cluster->dvfs_debug_init();
		if (ret) {
			pr_err("dvfs debug init error\n");
			return ret;
		}
	}

	ret = of_property_match_string(cluster->node, "nvmem-cell-names", "dvfs_bin");
	if (ret == -EINVAL) { /* No definition is allowed */
		dev_warn(dev, "%s: Warning: no 'dvfs_bin' appointed\n", __func__);
		cluster->bin = 0U;
	} else {
		ret = sprd_nvmem_info_read(cluster->node, "dvfs_bin", &cluster->bin);
		if (ret) {
			dev_err(dev, "%s: error in reading dvfs bin value\n", __func__);
			return ret;
		}

		ret = cluster->bin_set(cluster->id, cluster->bin);
		if (ret) {
			dev_err(dev, "%s: set cluster %u 'binning' value error\n", __func__, cluster->id);
			return -EINVAL;
		}
	}

	if (of_property_read_bool(cluster->node, "sprd,multi-version")) {
		hwf = of_find_node_by_path("/hwfeature/auto");
		if (IS_ERR_OR_NULL(hwf)) {
			dev_err(dev, "%s: no hwfeature/auto node found\n", __func__);
			return PTR_ERR(hwf);
		}

		cluster->version = (u64 *)of_get_property(hwf, "efuse", NULL);
		ret = cluster->version_set(cluster->id, cluster->version);
		if (ret) {
			dev_err(dev, "%s: set cluster %u 'version' value error\n", __func__, cluster->id);
			return -EINVAL;
		}
	}

	if (of_property_read_bool(cluster->node, "sprd,cpufreq-boost"))
		cluster->boost_enable = true;

	return 0;
}

static int sprd_cluster_ops_init(struct cluster_info *cluster)
{
	struct sprd_sip_svc_dvfs_ops *ops;
	struct sprd_sip_svc_handle *sip;

	sip = sprd_sip_svc_get_handle();
	if (!sip) {
		dev_err(dev, "%s: get sip error\n", __func__);
		return -EINVAL;
	}

	ops = &sip->dvfs_ops;

	cluster->dvfs_enable = ops->dvfs_enable;
	cluster->table_update = ops->table_update;
	cluster->step_set = ops->step_set;
	cluster->margin_set = ops->margin_set;
	cluster->freq_set = ops->freq_set;
	cluster->freq_get = ops->freq_get;
	cluster->pair_get = ops->pair_get;
	cluster->pmic_set = ops->pmic_set;
	cluster->bin_set = ops->bin_set;
	cluster->version_set = ops->version_set;
	cluster->dvfs_init = ops->dvfs_init;
	cluster->dvfs_debug_init = ops->dvfs_debug_init;

	return 0;
}

static struct device_node *sprd_cluster_node_init(u32 cpu_idx)
{
	struct device *cpu_dev;

	cpu_dev = get_cpu_device(cpu_idx);
	if (!cpu_dev)
		return NULL;

	return of_parse_phandle(cpu_dev->of_node, "cpufreq-data-v1", 0);
}

static int sprd_cluster_info_init(struct cluster_info *clusters)
{
	struct cluster_info *cluster;
	int cpu, ret;

	for_each_possible_cpu(cpu) {
		cluster = clusters + topology_cluster_id(cpu);
		if (cluster->node) {
			dev_dbg(dev, "%s: cluster %u info is also init\n", __func__, cluster->id);
			continue;
		}

		cluster->node = sprd_cluster_node_init(cpu);
		if (!cluster->node) {
			dev_err(dev, "%s: init cluster %u node error\n", __func__, cluster->id);
			return -EINVAL;
		}

		cluster->id = topology_cluster_id(cpu);

		mutex_init(&cluster->mutex);

		ret = sprd_cluster_ops_init(cluster);
		if (ret) {
			dev_err(dev, "%s: init cluster %u ops error\n", __func__, cluster->id);
			return -EINVAL;
		}

		ret = sprd_cluster_props_init(cluster);
		if (ret) {
			dev_err(dev, "%s: init cluster %u props error\n", __func__, cluster->id);
			return ret;
		}

		ret = sprd_cluster_temp_init(cluster);
		if (ret) {
			dev_err(dev, "%s: init cluster %u temp error\n", __func__, cluster->id);
			return -EINVAL;
		}

		cluster->update_opp = sprd_cpufreq_update_opp_v2;
	}

	return 0;
}

static int sprd_cpufreq_probe(struct platform_device *pdev)
{
	int ret;
	u32 flag;

	boot_done_timestamp = jiffies + SPRD_CPUFREQ_BOOST_DURATION;

	dev = &pdev->dev;

	dev_info(dev, "%s: probe sprd cpufreq v2 driver\n", __func__);

	/* MU300: DVFS_INIT runs once per boot (see above) */
	if (READ_ONCE(sprd_volt_state) != SV_PROBING)
		return -EBUSY;

	flag = sprd_cpufreq_get_debug_flag();
	if ((flag & 0xFF) == SPRD_DVFS_DEBUG_MAGIC) {
		dev_info(dev, "%s: disable apcpu dvfs for debug!\n", __func__);
		WRITE_ONCE(sprd_volt_state, SV_DISABLED);
		return 0;
	}

	pclusters = devm_kzalloc(dev, sizeof(*pclusters) * sprd_cluster_num(), GFP_KERNEL);
	if (!pclusters) {
		dev_err(dev, "%s: alloc memory for cluster info error\n", __func__);
		return -ENOMEM;
	}

	ret = sprd_cluster_info_init(pclusters);
	if (ret) {
		dev_err(dev, "%s: init cluster info error\n", __func__);
		return ret;
	}

	/* MU300: DVFS_INIT and the registration wait for the initramfs' word, or the timer */
	sprd_dvfs_base_flag = flag;
	sprd_volt_dcdc_limit();
	mutex_lock(&sprd_volt_lock);
	sprd_volt_state = SV_WAITING;
	mutex_unlock(&sprd_volt_lock);
	schedule_delayed_work(&sprd_volt_work, SPRD_VOLT_WAIT_S * HZ);
	dev_info(dev, "%s: DVFS waits for voltage_offset_uv (stock voltages after %ds)\n", __func__, SPRD_VOLT_WAIT_S);

	return 0;
}

/* the vendor probe's tail: DVFS_INIT with FLAG, then the cpufreq driver */
static int sprd_dvfs_start(u32 flag)
{
	int ret;

	ret = pclusters->dvfs_init(flag);
	if (ret) {
		dev_err(dev, "%s: init dvfs device error\n", __func__);
		return -EINVAL;
	}

	ret = sprd_debug_init(dev);
	if (ret) {
		dev_err(dev, "%s: init dvfs debug error\n", __func__);
		return -EINVAL;
	}

	ret = cpufreq_register_driver(&sprd_cpufreq_driver);
	if (ret)
		dev_err(dev, "%s: register cpufreq driver error\n", __func__);
	else
		dev_info(dev, "%s: register cpufreq driver success\n", __func__);

	return ret;
}

static void sprd_volt_start_work(struct work_struct *work)
{
	bool timeout = false;
	u32 flag;
	int ret;

	mutex_lock(&sprd_volt_lock);
	if (sprd_volt_state == SV_WAITING) {
		memset(sprd_volt_req, 0, sizeof(sprd_volt_req));
		timeout = true;
	} else if (sprd_volt_state != SV_QUEUED) {
		mutex_unlock(&sprd_volt_lock);
		return;
	}
	ret = sprd_volt_flag(sprd_dvfs_base_flag, sprd_volt_req, &flag);
	if (ret) {	/* cannot happen: checked when written; the stock call then */
		memset(sprd_volt_req, 0, sizeof(sprd_volt_req));
		flag = sprd_dvfs_base_flag;
	}
	memcpy(sprd_volt_applied, sprd_volt_req, sizeof(sprd_volt_applied));
	if (timeout)
		dev_warn(dev, "no voltage_offset_uv within %ds: DVFS starts at the stock voltages\n", SPRD_VOLT_WAIT_S);
	else
		dev_info(dev, "DVFS_INIT flag 0x%08x, voltage offsets %d,%d,%d uV%s\n", flag, sprd_volt_req[0],
			 sprd_volt_req[1], sprd_volt_req[2],
			 (sprd_volt_req[0] | sprd_volt_req[1] | sprd_volt_req[2]) ? " (EXPERIMENTAL)" : "");
	ret = sprd_dvfs_start(flag);
	if (ret)
		sprd_volt_state = SV_FAILED;
	else
		sprd_volt_state = timeout ? SV_TIMEOUT : SV_APPLIED;
	mutex_unlock(&sprd_volt_lock);
}

/* the lowest regulator-max-microvolt of the UMP9620 CPU DCDCs in the device tree */
static void sprd_volt_dcdc_limit(void)
{
	static const char * const rails[] = { "DCDC_CPU1", "DCDC_CPU2" };
	struct device_node *pmic, *np;
	int i, lim = 0;
	u32 v;

	pmic = of_find_compatible_node(NULL, NULL, "sprd,ump962x-regulator");
	for (i = 0; pmic && i < ARRAY_SIZE(rails); i++) {
		np = of_get_child_by_name(pmic, rails[i]);
		if (!np)
			continue;
		if (!of_property_read_u32(np, "regulator-max-microvolt", &v) && v > 0 && v <= INT_MAX &&
		    (!lim || (int)v < lim))
			lim = v;
		of_node_put(np);
	}
	of_node_put(pmic);
	sprd_volt_dcdc_max_uv = lim;
	dev_info(dev, "CPU DCDC maximum %duV%s\n", lim, lim ? "" : " (unknown: no voltage raise)");
}

/*
 * voltage_offset_uv: "A,B,C" in uV for domains 0..2, once per boot, while DVFS waits. Read: what DVFS runs with.
 */
static int sprd_volt_param_set(const char *val, const struct kernel_param *kp)
{
	int uv[SPRD_VOLT_DOMAINS], ret, i;
	bool any = false;
	u32 flag;

	/* the command line is parsed before userspace runs: never from there, and never without CAP_SYS_ADMIN */
	if (system_state != SYSTEM_RUNNING || !capable(CAP_SYS_ADMIN))
		return -EPERM;
	ret = sprd_volt_parse(val, uv);
	if (ret)
		return ret;

	mutex_lock(&sprd_volt_lock);
	if (sprd_volt_state != SV_WAITING) {
		ret = -EBUSY;
		goto out;
	}
	ret = sprd_volt_flag(sprd_dvfs_base_flag, uv, &flag);
	if (ret)
		goto out;
	for (i = 0; i < SPRD_VOLT_DOMAINS; i++) {
		any |= uv[i] != 0;
		if (uv[i] > 0 && !sprd_volt_dcdc_max_uv)
			ret = -EOPNOTSUPP;
	}
	if (any && !of_machine_is_compatible("sprd,ums9620"))
		ret = -EOPNOTSUPP;
	if (ret)
		goto out;
	memcpy(sprd_volt_req, uv, sizeof(sprd_volt_req));
	sprd_volt_state = SV_QUEUED;
	mod_delayed_work(system_wq, &sprd_volt_work, 0);
out:
	mutex_unlock(&sprd_volt_lock);
	if (ret)
		pr_warn("voltage_offset_uv %d,%d,%d refused (%d)\n", uv[0], uv[1], uv[2], ret);
	return ret;
}

static int sprd_volt_param_get(char *buf, const struct kernel_param *kp)
{
	return sysfs_emit(buf, "%d,%d,%d\n", READ_ONCE(sprd_volt_applied[0]), READ_ONCE(sprd_volt_applied[1]),
			  READ_ONCE(sprd_volt_applied[2]));
}

static int sprd_volt_param_ro(const char *val, const struct kernel_param *kp)
{
	return -EPERM;
}

static int sprd_volt_state_get(char *buf, const struct kernel_param *kp)
{
	return sysfs_emit(buf, "%s\n", sprd_volt_state_names[READ_ONCE(sprd_volt_state)]);
}

static int sprd_volt_limits_get(char *buf, const struct kernel_param *kp)
{
	return sysfs_emit(buf, "min=%d max=%d step=%d dcdc_max=%d wait=%d\n", SPRD_VOLT_MIN_UV, SPRD_VOLT_MAX_UV,
			  SPRD_VOLT_STEP_UV, sprd_volt_dcdc_max_uv, SPRD_VOLT_WAIT_S);
}

static const struct kernel_param_ops sprd_volt_offset_ops = {
	.set = sprd_volt_param_set,
	.get = sprd_volt_param_get,
};
static const struct kernel_param_ops sprd_volt_state_ops = {
	.set = sprd_volt_param_ro,
	.get = sprd_volt_state_get,
};
static const struct kernel_param_ops sprd_volt_limits_ops = {
	.set = sprd_volt_param_ro,
	.get = sprd_volt_limits_get,
};
module_param_cb(voltage_offset_uv, &sprd_volt_offset_ops, NULL, 0644);
MODULE_PARM_DESC(voltage_offset_uv, "EXPERIMENTAL: CPU voltage offsets of domains 0,1,2 in uV, once per boot (the initramfs)");
module_param_cb(voltage_state, &sprd_volt_state_ops, NULL, 0444);
MODULE_PARM_DESC(voltage_state, "probing, waiting, queued, applied, timeout, failed or disabled");
module_param_cb(voltage_limits, &sprd_volt_limits_ops, NULL, 0444);
MODULE_PARM_DESC(voltage_limits, "what voltage_offset_uv accepts");

/**
 * sprd_cpufreq_update_opp() - returns the max freq of a cpu and update dvfs
 * table by temp_now
 *
 * @cpu: which cpu you want to update dvfs table
 * @now_temp: current temperature on this cpu, mini-degree.
 *
 * Return:
 * 1.cluster is not working, then return 0
 * 2.succeed to update dvfs table then return max freq(KHZ) of this cluster
 */
unsigned int sprd_cpufreq_update_opp_v2(int cpu, int now_temp)
{
	struct cpufreq_policy *policy;
	struct cluster_info *cluster;
	int temp = now_temp / 1000;
	struct temp_node *node;
	u64 freq = 0;
	int ret;

	policy = cpufreq_cpu_get(cpu);
	if (!policy) {
		dev_err(dev, "%s: get cpu %u policy error\n", __func__, cpu);
		return 0;
	}

	cluster = (struct cluster_info *)policy->driver_data;
	if (!cluster || !cluster->pair_get) {
		dev_err(dev, "%s: cpu %u cluster info error\n", __func__, cpu);
		cpufreq_cpu_put(policy);
		return 0;
	}

	mutex_lock(&cluster->mutex);

	node = sprd_temp_list_find(&cluster->temp_list_head, temp);

	if (!node) {
		cluster->temp_level_node = cluster->temp_currt_node;
		cluster->temp_tick = 0U;
		goto ret_error;
	}

	/* immediate response to temp rise */
	if (node->temp > cluster->temp_currt_node->temp) {
		cluster->temp_level_node = node;
		cluster->temp_tick = DVFS_TEMP_MAX_TICKS;
	} else if (node != cluster->temp_level_node) {
		cluster->temp_level_node = node;
		cluster->temp_tick = 0U;
		goto ret_error;
	}

	++cluster->temp_tick;
	if (cluster->temp_tick < DVFS_TEMP_MAX_TICKS)
		goto ret_error;

	cluster->temp_tick = 0U;

	if (cluster->temp_level_node == cluster->temp_currt_node)
		goto ret_error;

	dev_info(dev, "%s: update cluster %u table to %d(%d) degrees celsius\n", __func__, cluster->id, temp, cluster->temp_level_node->temp);

	ret = sprd_policy_table_update(policy, cluster->temp_level_node);
	if (ret) {
		dev_err(dev, "%s: update cluster %u table error\n", __func__, cluster->id);
		goto ret_error;
	}

	cluster->temp_currt_node = cluster->temp_level_node;

	ret = cluster->pair_get(cluster->id, cluster->table_entry_num - 1, &freq, NULL);
	if (ret) {
		dev_err(dev, "%s: get cluster %u max freq error\n", __func__, cluster->id);
		goto ret_error;
	}

	cpufreq_cpu_put(policy);
	mutex_unlock(&cluster->mutex);

	do_div(freq, 1000); /* from Hz to KHz */
	return (unsigned int)freq;

ret_error:
	cpufreq_cpu_put(policy);
	mutex_unlock(&cluster->mutex);

	return 0;
}

static const struct of_device_id sprd_cpufreq_of_match[] = {
	{
		.compatible = "sprd,cpufreq-v2",
	},
	{
		/* Sentinel */
	},
};

static struct platform_driver sprd_cpufreq_platform_driver = {
	.driver = {
		.name = "sprd-cpufreq-v2",
		.of_match_table = sprd_cpufreq_of_match,
		.suppress_bind_attrs = true,	/* MU300: no unbind - DVFS_INIT runs once per boot */
	},
	.probe = sprd_cpufreq_probe,
};

static int __init sprd_cpufreq_platform_driver_register(void)
{
	return platform_driver_register(&sprd_cpufreq_platform_driver);
}

device_initcall(sprd_cpufreq_platform_driver_register);

MODULE_DESCRIPTION("sprd cpufreq v2 driver");
MODULE_LICENSE("GPL v2");
