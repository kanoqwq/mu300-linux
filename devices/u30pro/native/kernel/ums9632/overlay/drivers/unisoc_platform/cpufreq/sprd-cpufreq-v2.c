// SPDX-License-Identifier: GPL-2.0
/*
 * Unisoc apcpu hardware DVFS cpufreq driver ("sprd,cpufreq-v2") for the UMS9632.
 *
 * Port of the vendor driver drivers/unisoc_platform/cpufreq/sprd-cpufreq-v2-driver.c (Copyright (C) 2021 Unisoc, GPL-2.0) to Linux 6.12.
 *
 * How the hardware works (as the vendor driver shows):
 *  - The frequency/voltage table of each cluster, the clock muxing and the DCDC control are inside the secure monitor.  Linux talks to
 *    it with SIP SMC calls (soc/unisoc/sprd_sip_svc.h): table_update returns how many table entries are valid for a temperature,
 *    pair_get returns the (Hz, uV) pair of an entry, freq_set selects an entry, freq_get reads the running frequency and
 *    dvfs_enable switches the cluster over to the hardware DVFS state machine.
 *  - The device tree (stock DTB, node "apcpu-dvfs", compatible "sprd,cpufreq-v2") names the cluster of every CPU
 *    ("sprd,freq-domain"), the voltage margin, the temperature thresholds and the thermal zone used to select the table.  The tables
 *    themselves come from the firmware at run time and are turned into an OPP table, so no operating-points are needed in the DT.
 *  - The table depends on the temperature: below/above the "sprd,temp-threshold" values different voltages are valid and the highest
 *    frequencies may disappear.  A work item reads the cluster thermal zone every 1.5 s and, after three identical readings (a
 *    temperature rise is taken immediately), asks the firmware for the new table.  When the table shrinks, the maximum frequency QoS
 *    request is lowered first.
 *  - For 60 s after the driver probes, "sprd,cpufreq-boost" clusters ignore governor requests and keep the frequency the boot loader
 *    left (maximum) so that boot is fast.
 *
 * ASE (aging sensor, drivers/unisoc_platform/ase): measures the aging of the silicon and raises the table voltage of aged clusters.  It is
 * ported but has its own switch: only with u30pro_ase=1 does this driver wait for the ASE device and register its test hooks; otherwise the
 * firmware keeps its factory voltages (fine for a young chip).
 *
 * scufreq / PMU sampling (drivers/unisoc_platform/{pmu,scufreq}): software control of the DSU (SCU) frequency ("sprd,scu-soft").  This
 *  driver only provides what scufreq needs: the cpufreq driver data (struct sprd_dvfs_scu_data: the SCU set/get operations, the CPU
 *  mask and the CPU frequency -> SCU index table of every cluster, built from the firmware "vote scu" of each table entry).  It is
 *  only set up when u30pro_scufreq=1 is on the kernel command line; otherwise the DSU stays in the firmware's autotune mode.
 *
 * The read-only debugfs table of the vendor driver is ported: /sys/kernel/debug/sprd-cpufreq-v2/cluster<N>/dvfs_table.
 *
 * Because wrong DVFS can hang the SoC, the driver stays off unless the kernel command line contains u30pro_cpufreq=1.
 */
#define pr_fmt(fmt) "sprd-apcpu-dvfs: " fmt

#include <linux/cpu.h>
#include <linux/cpufreq.h>
#include <linux/cpumask.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/init.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_opp.h>
#include <linux/pm_qos.h>
#include <linux/printk.h>
#include <linux/slab.h>
#include <linux/thermal.h>
#include <linux/workqueue.h>
#include <linux/cpu_cooling.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
#include <linux/sprd-ase.h>
#include <linux/sprd_ase_ops.h>
#include <soc/unisoc/sprd_dvfs.h>
#include <soc/unisoc/sprd_sip_svc.h>

#define DVFS_TEMP_UPPER_LIMIT		(274)	/* degrees Celsius */
#define DVFS_TEMP_LOW_LIMIT		(-274)
#define DVFS_TEMP_DEFAULT		(0)
#define DVFS_TEMP_MAX_TICKS		(3)
#define DVFS_TEMP_UPDATE_MS		(1500)
#define DVFS_SUSPEND_INDEX		(0)

#define low_check(temp)		((int)(temp) < (int)DVFS_TEMP_LOW_LIMIT)
#define upper_check(temp)	((int)(temp) >= (int)DVFS_TEMP_UPPER_LIMIT)
#define temp_check(temp)	(low_check(temp) || upper_check(temp))

#define ON_BOOST		(0)
#define OUT_BOOST		(1)
#define ONLY_READ		(1)
#define UPDATE_TABLE		(0)
#define SPRD_CPUFREQ_BOOST_DURATION	(60ul * HZ)
#define SPRD_DVFS_DEBUG_MAGIC	(0x5A)

/* off unless u30pro_cpufreq=1 is on the kernel command line */
static int u30pro_cpufreq;
#ifdef MODULE
module_param_named(u30pro_cpufreq, u30pro_cpufreq, int, 0444);
#else
core_param(u30pro_cpufreq, u30pro_cpufreq, int, 0444);
#endif

/* software SCU frequency control (scufreq) is only set up with u30pro_scufreq=1; the scufreq driver reads this too */
int u30pro_scufreq;
#ifdef MODULE
module_param_named(u30pro_scufreq, u30pro_scufreq, int, 0444);
#else
core_param(u30pro_scufreq, u30pro_scufreq, int, 0444);
#endif
EXPORT_SYMBOL_GPL(u30pro_scufreq);

/* Stock UMS9632 DVFS_INIT takes two signed-mV offset bytes in flag0.
 * Applied once, before table selection. Keep MU300's boot-only ABI and guard. */
static int voltage_offset_uv[2];
static unsigned int voltage_offset_count;
static bool firmware_initialized;
module_param_array(voltage_offset_uv, int, &voltage_offset_count, 0444);
MODULE_PARM_DESC(voltage_offset_uv, "Boot offsets for two UMS9632 domains in uV (-50000..25000, step 3125)");

static int sprd_voltage_flag(u32 *flag)
{
    unsigned int i;
    u32 encoded;
    int uv;
    if (voltage_offset_count && voltage_offset_count != ARRAY_SIZE(voltage_offset_uv))
        return -EINVAL;
    *flag &= 0xff;
    for (i = 0; i < ARRAY_SIZE(voltage_offset_uv); i++) {
        uv = voltage_offset_uv[i];
        if (uv < -50000 || uv > 25000 || uv % 3125)
            return -ERANGE;
        if (uv && !of_machine_is_compatible("sprd,ums9632"))
            return -EOPNOTSUPP;
        encoded = abs(uv) / 1000;
        if (uv < 0)
            encoded |= 0x80;
        *flag |= encoded << (8 * (i + 1));
    }
    return 0;
}

/* the ASE switch (u30pro_ase=1) lives in the ASE driver */
#if IS_ENABLED(CONFIG_U30PRO_SPRD_ASE)
extern int u30pro_ase;
#define ASE_ENABLED()	(u30pro_ase)
#else
#define ASE_ENABLED()	0
#endif

struct temp_node {
	int temp;
	u32 temp_num;
	struct cpufreq_frequency_table *temp_table;
	struct list_head list;
};

struct cluster_info {
	u32 id, cpu;
	struct cpumask cpus;

	struct delayed_work temp_work;
	bool temp_enable;

	struct freq_qos_request max_req;

	struct mutex mutex;
	struct device_node *node;

	u32 voltage_step;
	u32 voltage_margin;
	u32 pmic_type;
	u32 transition_delay;
	u32 table_entry_num;

	u32 ase_id_num;
	u32 *ase_id;
	u32 ase_max_volt;

	bool boost_enable;
	bool fast_switch;
	bool ase_seq;

	u32 temp_tick;
	struct temp_node *temp_currt_node;
	struct temp_node *temp_level_node;
	struct list_head temp_list_head;

	struct thermal_zone_device *cpu_tz;
	const char *tz_name;
};

static struct device *dev;
static struct cluster_info *pclusters;
static struct sprd_sip_svc_dvfs_ops *dvfs;
static struct sprd_dvfs_scu_data *scu_data;
static struct sprd_ase_ops *ase_driver_ops;
static unsigned long boot_done_timestamp;
static int cluster_num;

/* ASE glue: the ASE driver tests a subsystem (ase id) and reports the extra voltage through these ops */
static int sprd_cpufreq_ase_to_cluster(u32 sys_id, u32 *clu_id)
{
	struct cluster_info *cluster;
	int id, ic;

	for (id = 0; id < cluster_num; id++) {
		cluster = pclusters + id;

		for (ic = 0; ic < cluster->ase_id_num; ic++) {
			if (cluster->ase_id[ic] == sys_id) {
				*clu_id = cluster->id;
				return 0;
			}
		}
	}

	pr_err("no match ase id was found\n");
	return -EINVAL;
}

/* before a test: put the cluster at the test voltage */
static int sprd_cpufreq_ase_test_prepare(u32 sys_id, u32 volt_uv)
{
	struct cluster_info *cluster;
	u32 clu_id;
	int ret;

	ret = sprd_cpufreq_ase_to_cluster(sys_id, &clu_id);
	if (ret)
		return ret;

	cluster = pclusters + clu_id;

	mutex_lock(&cluster->mutex);

	ret = dvfs->ase_init(clu_id, volt_uv);
	if (ret)
		pr_err("cluster %u ase init error\n", cluster->id);

	mutex_unlock(&cluster->mutex);

	return ret;
}

/* after a test: hand the aged-silicon voltage to the firmware, which may shorten the table; lower the max-frequency QoS to match */
static int sprd_cpufreq_ase_volt_update(u32 sys_id, u32 volt_uv)
{
	struct cluster_info *cluster;
	u32 clu_id;
	int ret;
	u64 freq;

	ret = sprd_cpufreq_ase_to_cluster(sys_id, &clu_id);
	if (ret)
		return ret;

	cluster = pclusters + clu_id;

	cluster->ase_max_volt = volt_uv > cluster->ase_max_volt ? volt_uv : cluster->ase_max_volt;

	mutex_lock(&cluster->mutex);

	ret = dvfs->ase_set(clu_id, cluster->ase_max_volt, &cluster->table_entry_num);
	if (ret) {
		pr_err("cluster %u ase init error\n", cluster->id);
		goto unlock_ret;
	}

	if (!cluster->table_entry_num)
		goto unlock_ret;

	ret = dvfs->pair_get(cluster->id, cluster->table_entry_num - 1, &freq, NULL);
	if (ret) {
		pr_err("get cluster %u max freq error\n", cluster->id);
		goto unlock_ret;
	}

	do_div(freq, 1000);	/* Hz -> kHz */

	ret = freq_qos_update_request(&cluster->max_req, freq);
	if (ret < 0) {
		pr_err("cluster %u qos update max freq(%llu) error\n", cluster->id, freq);
		goto unlock_ret;
	}

	ret = 0;

unlock_ret:
	mutex_unlock(&cluster->mutex);
	return ret;
}

static struct ase_ops cpufreq_ase_ops = {
	.sys_prepare = sprd_cpufreq_ase_test_prepare,
	.sys_update = sprd_cpufreq_ase_volt_update,
};

/* register the test hooks for every ase id of the cluster ("sprd,ase-id") */
static int sprd_cluster_ase_init(struct cluster_info *cluster)
{
	struct property *prop;
	const char *name = "sprd,ase-id";
	int ret, num, i;

	prop = of_find_property(cluster->node, name, &num);
	if (!prop || !num) {
		pr_warn("find cluster %u ase id property error\n", cluster->id);
		return -EINVAL;
	}

	if (!cluster->ase_id) {
		cluster->ase_id_num = num / sizeof(u32);
		cluster->ase_id = devm_kzalloc(dev, num, GFP_KERNEL);
		if (!cluster->ase_id)
			return -ENOMEM;
	}

	for (i = 0; i < cluster->ase_id_num; i++) {
		ret = of_property_read_u32_index(cluster->node, name, i, &cluster->ase_id[i]);
		if (ret) {
			pr_err("get cluster %u ase id error\n", cluster->id);
			return ret;
		}

		ret = ase_driver_ops->ops_init(cluster->ase_id[i], &cpufreq_ase_ops, cluster->ase_seq);
		if (ret) {
			pr_err("cluster(%u) ase ops init fail(%d)\n", cluster->id, ret);
			return ret;
		}
	}

	return 0;
}

#ifdef CONFIG_DEBUG_FS
static struct dentry *dvfs_debug_dir;

/* the OPP table of the cluster as it stands for the current temperature level (Hz / uV), like the vendor "dvfs_table" node */
static int sprd_dvfs_table_show(struct seq_file *m, void *unused)
{
	struct cluster_info *cluster = m->private;
	unsigned long rate = 0;
	struct device *cpu_dev;
	int opp_num, i;

	mutex_lock(&cluster->mutex);

	cpu_dev = get_cpu_device(cluster->cpu);
	opp_num = cpu_dev ? dev_pm_opp_get_opp_count(cpu_dev) : -ENODEV;
	if (opp_num < 0) {
		mutex_unlock(&cluster->mutex);
		return opp_num;
	}

	seq_printf(m, "     DVFS Table(%d)\nFreq(Hz)\tVolt(uV)\n", cluster->temp_level_node->temp);

	for (i = 0; i < opp_num; i++, rate++) {
		struct dev_pm_opp *opp = dev_pm_opp_find_freq_ceil(cpu_dev, &rate);

		if (IS_ERR(opp))
			break;

		seq_printf(m, "%lu\t%lu\n", dev_pm_opp_get_freq(opp), dev_pm_opp_get_voltage(opp));
		dev_pm_opp_put(opp);
	}

	mutex_unlock(&cluster->mutex);

	return 0;
}
DEFINE_SHOW_ATTRIBUTE(sprd_dvfs_table);

static void sprd_debug_init(void)
{
	int i;

	dvfs_debug_dir = debugfs_create_dir("sprd-cpufreq-v2", NULL);
	for (i = 0; i < cluster_num; i++) {
		char name[16];
		struct dentry *dir;

		snprintf(name, sizeof(name), "cluster%d", i);
		dir = debugfs_create_dir(name, dvfs_debug_dir);
		debugfs_create_file("dvfs_table", 0444, dir, pclusters + i, &sprd_dvfs_table_fops);
	}
}

static void sprd_debug_exit(void)
{
	debugfs_remove_recursive(dvfs_debug_dir);
	dvfs_debug_dir = NULL;
}
#else
static inline void sprd_debug_init(void) { }
static inline void sprd_debug_exit(void) { }
#endif

/* cluster of @cpu_idx from the "sprd,freq-domain" phandle argument; @cpus (optional) gets every CPU of that cluster */
static int sprd_cluster_info(u32 cpu_idx, cpumask_var_t cpus)
{
	struct of_phandle_args args;
	int ret;

	if (cpu_idx >= nr_cpu_ids)
		return -EINVAL;

	if (!cpus)
		ret = parse_perf_domain(cpu_idx, "sprd,freq-domain", "#freq-domain-cells", &args);
	else
		/* CPUs in the same cluster share the same clock and power domain */
		ret = of_perf_domain_get_sharing_cpumask(cpu_idx, "sprd,freq-domain", "#freq-domain-cells", cpus, &args);
	if (ret < 0) {
		pr_err("cpufreq cluster cpumask error\n");
		return ret;
	}

	of_node_put(args.np);
	return args.args[0];
}

static int sprd_cluster_num(void)
{
	unsigned int cpu_max = num_possible_cpus() - 1;
	int ret = sprd_cluster_info(cpu_max, NULL);

	return ret < 0 ? ret : ret + 1;
}

static int sprd_cpufreq_boost_judge(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;

	if (time_after(jiffies, boot_done_timestamp)) {
		cluster->boost_enable = false;
		pr_debug("policy[%d] disables boost it is %lu seconds after boot up\n",
				policy->cpu, SPRD_CPUFREQ_BOOST_DURATION / HZ);
		return OUT_BOOST;
	}

	if (policy->max < policy->cpuinfo.max_freq) {
		cluster->boost_enable = false;
		pr_debug("policy[%d] disables boost due to policy max(%d<%d)\n",
				policy->cpu, policy->max, policy->cpuinfo.max_freq);
		return OUT_BOOST;
	}

	return ON_BOOST;
}

/* temperature list: sorted thresholds bounded by DVFS_TEMP_LOW_LIMIT and DVFS_TEMP_UPPER_LIMIT; a node covers [temp, next->temp) */
static int sprd_temp_list_init(struct list_head *head)
{
	struct temp_node *node;

	if (!list_empty_careful(head)) {
		pr_warn("temp list is also init\n");
		return 0;
	}

	node = devm_kzalloc(dev, sizeof(*node), GFP_KERNEL);
	if (!node)
		return -ENOMEM;
	node->temp = DVFS_TEMP_UPPER_LIMIT;
	list_add(&node->list, head);

	node = devm_kzalloc(dev, sizeof(*node), GFP_KERNEL);
	if (!node)
		return -ENOMEM;
	node->temp = DVFS_TEMP_LOW_LIMIT;
	list_add(&node->list, head);

	return 0;
}

static int sprd_temp_list_add(struct list_head *head, int temp)
{
	struct temp_node *node, *pos;

	if (temp_check(temp) || list_empty_careful(head)) {
		pr_err("temp %d or list is error\n", temp);
		return -EINVAL;
	}

	list_for_each_entry(pos, head, list) {
		if (temp == pos->temp)
			return 0;
		if (temp < pos->temp)
			break;
	}

	node = devm_kzalloc(dev, sizeof(*node), GFP_KERNEL);
	if (!node)
		return -ENOMEM;
	node->temp = temp;
	list_add_tail(&node->list, &pos->list);

	return 0;
}

static struct temp_node *sprd_temp_list_find(struct list_head *head, int temp)
{
	struct temp_node *pos, *next;

	if (temp_check(temp)) {
		pr_err("temp %d is out of range\n", temp);
		return NULL;
	}

	list_for_each_entry(pos, head, list) {
		next = list_entry(pos->list.next, typeof(*pos), list);
		if ((temp >= pos->temp) && (temp < next->temp))
			break;
	}

	return pos;
}

/* with fast switch the number of valid entries of every temperature level is needed up front */
static int sprd_temp_list_num_init(struct cluster_info *cluster, struct list_head *head)
{
	struct temp_node *pos;
	int ret = 0;

	list_for_each_entry(pos, head, list) {
		if (pos->temp == DVFS_TEMP_UPPER_LIMIT)
			break;

		ret = dvfs->table_update(cluster->id, pos->temp, &pos->temp_num, ONLY_READ);
		if (ret) {
			pr_err("init temp %d entry num error\n", pos->temp);
			return ret;
		}
	}

	return ret;
}

/* sprdboot.dvfs_set=0x<flag>,<flag2>,<flag3> from the boot loader; flag & 0xff == 0x5a disables apcpu DVFS (debug) */
static void sprd_cpufreq_get_debug_flag(u32 *value)
{
	struct device_node *chosen;
	const char *cmd_line, *dvfs_set;
	int ret;

	chosen = of_find_node_by_path("/chosen");
	ret = of_property_read_string(chosen, "bootargs", &cmd_line);
	of_node_put(chosen);
	if (ret) {
		pr_err("Fail to find cmdline bootargs property\n");
		return;
	}

	dvfs_set = strstr(cmd_line, "sprdboot.dvfs_set=0x");
	if (!dvfs_set) {
		pr_info("no property sprdboot.dvfs_set found\n");
		return;
	}

	ret = sscanf(dvfs_set, "sprdboot.dvfs_set=0x%x,%x,%x", &value[0], &value[1], &value[2]);
	if (ret != 3)
		pr_err("property dvfs_set para is error\n");
}

/* DCDC supply mode: "power.from.extern=0" selects the "-v2" variant of the sprd,pmic-type property */
static void sprd_cluster_get_supply_mode(char *dcdc_supply)
{
	struct device_node *chosen;
	const char *cmd_line, *dcdc_type;
	int value = -1, ret;

	chosen = of_find_node_by_path("/chosen");
	ret = of_property_read_string(chosen, "bootargs", &cmd_line);
	of_node_put(chosen);
	if (ret) {
		pr_err("Fail to find cmdline bootargs property\n");
		return;
	}

	dcdc_type = strstr(cmd_line, "power.from.extern=");
	if (!dcdc_type) {
		pr_info("no property power.from.extern found\n");
		return;
	}

	if (sscanf(dcdc_type, "power.from.extern=%d", &value) == 1 && !value)
		strcat(dcdc_supply, "-v2");
}

/* ask the firmware for the table valid at node->temp and (re)build the OPP table and policy->freq_table from it */
static int sprd_policy_table_update(struct cpufreq_policy *policy, struct temp_node *node)
{
	struct cpufreq_frequency_table *new_table;
	struct cluster_info *cluster = policy->driver_data;
	struct sprd_dvfs_scu_info *scu_info = NULL;
	struct device *cpu;
	u64 freq, vol;
	u32 scu;
	int i, ret;

	if (!cluster) {
		pr_err("get cpu %u cluster info error\n", policy->cpu);
		return -EINVAL;
	}

	/* the CPU frequency -> SCU index table is read from the firmware once, with the first table of the cluster */
	if (!policy->freq_table && scu_data && scu_data->scu_info)
		scu_info = scu_data->scu_info + cluster->id;

	cpu = get_cpu_device(cluster->cpu);
	if (!cpu) {
		pr_err("get cpu %u dev error\n", policy->cpu);
		return -EINVAL;
	}

	pr_info("update cluster %u temp %d dvfs table\n", cluster->id, node->temp);

	ret = dvfs->table_update(cluster->id, node->temp, &cluster->table_entry_num, UPDATE_TABLE);
	if (ret) {
		pr_err("update cluster %u temp %d table error\n", cluster->id, node->temp);
		return ret;
	}

	pr_debug("cluster %u dvfs table entry num is %u\n", cluster->id, cluster->table_entry_num);

	for (i = 0; i < cluster->table_entry_num; ++i) {
		ret = dvfs->pair_get(cluster->id, i, &freq, &vol);
		if (ret) {
			pr_err("get cluster %u index %u pair error\n", cluster->id, i);
			return ret;
		}

		pr_debug("add %lluHz/%lluuV to opp\n", freq, vol);

		/* the entry may be there from a previous table */
		if (policy->freq_table) {
			dev_pm_opp_remove(cpu, freq);
		} else if (scu_info) {
			ret = dvfs->vote_scu_get(cluster->id, i, &scu);
			if (ret) {
				pr_err("get cluster %u votescu %u error\n", cluster->id, i);
				return ret;
			}
			scu_info->scu_table[i].scu_idx = scu;
			scu_info->scu_table[i].cpu_freq = freq;
		}

		ret = dev_pm_opp_add(cpu, freq, vol);
		if (ret) {
			pr_err("add %lluHz/%lluuV pair to opp error(%d)\n", freq, vol, ret);
			return ret;
		}
	}

	if (scu_info)
		scu_info->dvfs_num = cluster->table_entry_num;

	if (node->temp_table) {
		policy->freq_table = node->temp_table;
	} else {
		ret = dev_pm_opp_init_cpufreq_table(cpu, &new_table);
		if (ret) {
			pr_err("init cluster %u freq table error(%d)\n", cluster->id, ret);
			return ret;
		}

		policy->freq_table = new_table;
		node->temp_table = new_table;
	}

	policy->suspend_freq = policy->freq_table[DVFS_SUSPEND_INDEX].frequency;

	return 0;
}

/* returns the maximum frequency (kHz) of the cluster after a table update, 0 when nothing changed */
static unsigned int sprd_cpufreq_update_opp(unsigned int cpu, int now_temp)
{
	struct cpufreq_policy *policy;
	struct cluster_info *cluster;
	int temp = now_temp / 1000;
	struct temp_node *node;
	u64 freq;
	int ret;

	policy = cpufreq_cpu_get(cpu);
	if (!policy) {
		pr_err("get cpu %u policy error\n", cpu);
		return 0;
	}

	cluster = policy->driver_data;
	if (!cluster) {
		pr_err("cpu %u cluster info error\n", cpu);
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

	/* immediate response to a temperature rise */
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

	pr_debug("update cluster %u table to %d(%d)\n", cluster->id, temp, cluster->temp_level_node->temp);

	/* a smaller table first lowers the maximum-frequency QoS request, or the governor could pick a vanished entry */
	if (policy->fast_switch_enabled && node->temp_num && node->temp_num < cluster->temp_currt_node->temp_num) {
		cluster->table_entry_num = node->temp_num;
		freq = policy->freq_table[node->temp_num - 1].frequency;

		ret = freq_qos_update_request(&cluster->max_req, freq);
		if (ret < 0) {
			pr_err("cluster %u qos update max freq(%llu) error\n", cluster->id, freq);
			goto ret_error;
		}

		down_write(&policy->rwsem);
		refresh_frequency_limits(policy);
		up_write(&policy->rwsem);
	}

	/* delay is required to make sure the last transition has completed */
	udelay(100);

	ret = sprd_policy_table_update(policy, cluster->temp_level_node);
	if (ret) {
		cluster->table_entry_num = cluster->temp_currt_node->temp_num;
		freq = policy->freq_table[cluster->table_entry_num - 1].frequency;

		ret = freq_qos_update_request(&cluster->max_req, freq);
		if (ret)
			pr_err("cluster %u qos update max freq(%llu) error\n", cluster->id, freq);

		pr_err("update cluster %u table error(%d)\n", cluster->id, ret);
		goto ret_error;
	}

	if (cluster->fast_switch)
		cluster->temp_level_node->temp_num = cluster->table_entry_num;

	cluster->temp_currt_node = cluster->temp_level_node;

	ret = dvfs->pair_get(cluster->id, cluster->table_entry_num - 1, &freq, NULL);
	if (ret) {
		pr_err("get cluster %u max freq error\n", cluster->id);
		goto ret_error;
	}

	do_div(freq, 1000);	/* Hz -> kHz */

	ret = freq_qos_update_request(&cluster->max_req, freq);
	if (ret < 0) {
		pr_err("cluster %u qos update max freq(%llu) error\n", cluster->id, freq);
		goto ret_error;
	}

	udelay(100);

	cpufreq_cpu_put(policy);
	mutex_unlock(&cluster->mutex);

	return (unsigned int)freq;

ret_error:
	cpufreq_cpu_put(policy);
	mutex_unlock(&cluster->mutex);

	return 0;
}

static void sprd_cpufreq_temp_work_func(struct work_struct *work)
{
	struct delayed_work *dwork = to_delayed_work(work);
	struct cluster_info *cluster = container_of(dwork, struct cluster_info, temp_work);
	struct cpumask cluster_online_mask;
	unsigned int freq, cpu;
	int temp = 0, ret;

	if (IS_ERR_OR_NULL(cluster->cpu_tz)) {
		cluster->cpu_tz = thermal_zone_get_zone_by_name(cluster->tz_name);
		if (IS_ERR_OR_NULL(cluster->cpu_tz)) {
			cluster->cpu_tz = NULL;
			pr_warn_once("failed to get cluster %u thmzone '%s'\n", cluster->id, cluster->tz_name);
			queue_delayed_work(system_highpri_wq, &cluster->temp_work, msecs_to_jiffies(DVFS_TEMP_UPDATE_MS));
			return;
		}

		pr_info("get cluster %u thmzone successfully\n", cluster->id);
	}

	ret = thermal_zone_get_temp(cluster->cpu_tz, &temp);
	if (ret) {
		pr_warn("failed to get the cluster %u temp of cpu thmzone\n", cluster->id);
		queue_delayed_work(system_highpri_wq, &cluster->temp_work, msecs_to_jiffies(DVFS_TEMP_UPDATE_MS));
		return;
	}

	cpumask_and(&cluster_online_mask, &cluster->cpus, cpu_online_mask);
	cpu = cpumask_first(&cluster_online_mask);

	freq = sprd_cpufreq_update_opp(cpu, temp);
	if (freq)
		pr_info("cluster[%u] update max freq[%u] by temp[%d]\n", cluster->id, freq, temp);

	queue_delayed_work(system_highpri_wq, &cluster->temp_work, msecs_to_jiffies(DVFS_TEMP_UPDATE_MS));
}

/* sprd_cpufreq_driver interface */
static int sprd_cpufreq_init(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster;
	u64 freq = 0;
	int ret, cluster_index;

	cluster_index = sprd_cluster_info(policy->cpu, policy->cpus);
	if (cluster_index < 0 || cluster_index >= cluster_num) {
		pr_err("get cluster_index %d info error\n", cluster_index);
		return -EINVAL;
	}

	cluster = pclusters + cluster_index;

	mutex_lock(&cluster->mutex);

	cpumask_copy(&cluster->cpus, policy->cpus);
	if (scu_data && scu_data->scu_info)
		cpumask_copy(&scu_data->scu_info[cluster->id].cpus, policy->cpus);

	/* every CPU of the cluster can request the frequency */
	policy->dvfs_possible_from_any_cpu = true;

	policy->transition_delay_us = cluster->transition_delay;
	policy->driver_data = cluster;

	if (cluster->fast_switch)
		policy->fast_switch_possible = true;

	/* the table for the current temperature level */
	ret = sprd_policy_table_update(policy, cluster->temp_currt_node);
	if (ret) {
		pr_err("update cluster %u table error(%d)\n", cluster->id, ret);
		goto unlock_ret;
	}

	/* hand the cluster over to the hardware DVFS state machine */
	ret = dvfs->dvfs_enable(cluster->id);
	if (ret) {
		pr_err("enable cluster %u dvfs error\n", cluster->id);
		goto unlock_ret;
	}

	ret = dvfs->freq_get(cluster->id, &freq);
	if (ret) {
		pr_err("get cluster %u current freq error\n", cluster->id);
		goto unlock_ret;
	}

	do_div(freq, 1000);	/* Hz -> kHz */
	policy->cur = (u32)freq;

	/* with ASE the maximum starts at the running frequency and is raised by the ASE result */
	ret = freq_qos_add_request(&policy->constraints, &cluster->max_req, FREQ_QOS_MAX,
				   ase_driver_ops ? policy->cur : FREQ_QOS_MAX_DEFAULT_VALUE);
	if (ret < 0) {
		pr_err("add cluster %u freq qos error(%d)\n", cluster->id, ret);
		goto unlock_ret;
	}

	if (cluster->temp_enable)
		queue_delayed_work(system_highpri_wq, &cluster->temp_work, msecs_to_jiffies(DVFS_TEMP_UPDATE_MS));

	mutex_unlock(&cluster->mutex);

	if (cluster->ase_seq) {
		ret = sprd_cluster_ase_init(cluster);
		if (ret) {
			pr_err("init cluster %u ase error(%d)\n", cluster->id, ret);
			return ret;
		}
	}

	return 0;

unlock_ret:
	mutex_unlock(&cluster->mutex);

	return ret;
}

static void sprd_cpufreq_exit(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;
	struct device *cpu;

	if (policy->cdev) {
		cpufreq_cooling_unregister(policy->cdev);
		policy->cdev = NULL;
	}

	if (!cluster) {
		pr_err("policy is not init\n");
		return;
	}

	cpu = get_cpu_device(policy->cpu);
	if (!cpu) {
		pr_err("get cpu %u device error\n", policy->cpu);
		return;
	}

	mutex_lock(&cluster->mutex);

	if (cluster->temp_enable)
		cancel_delayed_work_sync(&cluster->temp_work);

	freq_qos_remove_request(&cluster->max_req);

	dev_pm_opp_free_cpufreq_table(cpu, &policy->freq_table);
	dev_pm_opp_of_remove_table(cpu);

	policy->driver_data = NULL;

	mutex_unlock(&cluster->mutex);
}

static int sprd_cpufreq_set_target_index(struct cpufreq_policy *policy, u32 index)
{
	struct cluster_info *cluster = policy->driver_data;
	int ret;

	if (!cluster) {
		pr_err("policy is not init\n");
		return -EINVAL;
	}

	if (unlikely(cluster->boost_enable)) {
		ret = sprd_cpufreq_boost_judge(policy);
		if (ret == ON_BOOST)
			return 0;
	}

	mutex_lock(&cluster->mutex);

	if (index >= cluster->table_entry_num) {
		pr_info("cluster %u index %u has been reduced\n", cluster->id, index);
		index = cluster->table_entry_num - 1;
	}

	ret = dvfs->freq_set(cluster->id, index);
	if (ret)
		pr_err("set cluster %u index %u error(%d)\n", cluster->id, index, ret);

	mutex_unlock(&cluster->mutex);

	return ret;
}

/* scheduler context: no mutex, the firmware call is a fast SMC */
static unsigned int sprd_cpufreq_hw_fast_switch(struct cpufreq_policy *policy, unsigned int target_freq)
{
	struct cluster_info *cluster = policy->driver_data;
	unsigned int index;
	int ret;

	if (policy->cached_target_freq == target_freq)
		index = policy->cached_resolved_idx;
	else
		index = cpufreq_table_find_index_al(policy, target_freq, false);

	if (unlikely(cluster->boost_enable)) {
		ret = sprd_cpufreq_boost_judge(policy);
		if (ret == ON_BOOST)
			return policy->freq_table[index].frequency;
	}

	if (index >= cluster->table_entry_num) {
		pr_debug("cluster %u index %u has been reduced\n", cluster->id, index);
		index = cluster->table_entry_num - 1;
	}

	ret = dvfs->freq_set(cluster->id, index);
	if (ret)
		return 0;

	return policy->freq_table[index].frequency;
}

static unsigned int sprd_cpufreq_get(unsigned int cpu)
{
	struct cluster_info *cluster;
	u64 freq;
	int ret, cluster_index;

	cluster_index = sprd_cluster_info(cpu, NULL);
	if (cluster_index < 0 || cluster_index >= cluster_num) {
		pr_err("get cluster_index %d info error\n", cluster_index);
		return 0;
	}
	cluster = pclusters + cluster_index;

	mutex_lock(&cluster->mutex);
	ret = dvfs->freq_get(cluster->id, &freq);
	mutex_unlock(&cluster->mutex);
	if (ret) {
		pr_err("get cluster %u current freq error\n", cluster->id);
		return 0;
	}

	do_div(freq, 1000);	/* Hz -> kHz */

	return (unsigned int)freq;
}

static int sprd_cpufreq_suspend(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;

	if (cluster->temp_enable)
		cancel_delayed_work_sync(&cluster->temp_work);

	if (!strcmp(policy->governor->name, "userspace")) {
		pr_info("do nothing for governor-%s\n", policy->governor->name);
		return 0;
	}

	if (unlikely(cluster->boost_enable)) {
		cluster->boost_enable = false;
		sprd_cpufreq_set_target_index(policy, DVFS_SUSPEND_INDEX);
	}

	return cpufreq_generic_suspend(policy);
}

static int sprd_cpufreq_resume(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;

	if (cluster->temp_enable)
		queue_delayed_work(system_highpri_wq, &cluster->temp_work, msecs_to_jiffies(DVFS_TEMP_UPDATE_MS));

	if (!strcmp(policy->governor->name, "userspace")) {
		pr_info("do nothing for governor-%s\n", policy->governor->name);
		return 0;
	}

	return cpufreq_generic_suspend(policy);
}

static int sprd_cpufreq_online(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;

	if (!cluster) {
		pr_err("policy is not init\n");
		return -EINVAL;
	}

	if (cluster->temp_enable)
		queue_delayed_work(system_highpri_wq, &cluster->temp_work, msecs_to_jiffies(DVFS_TEMP_UPDATE_MS));

	return 0;
}

static int sprd_cpufreq_offline(struct cpufreq_policy *policy)
{
	struct cluster_info *cluster = policy->driver_data;

	if (!cluster) {
		pr_err("policy is not init\n");
		return -EINVAL;
	}

	if (cluster->temp_enable)
		cancel_delayed_work_sync(&cluster->temp_work);

	return 0;
}

/* the cooling device is created from the cpu node's #cooling-cells and the thermal zone cooling maps of the DT */
static void sprd_cpufreq_ready(struct cpufreq_policy *policy)
{
	if (!policy->cdev) {
		struct thermal_cooling_device *cdev = of_cpufreq_cooling_register(policy);

		policy->cdev = IS_ERR(cdev) ? NULL : cdev;
	}
}

static ssize_t show_scaling_voltage_table(struct cpufreq_policy *policy, char *buf)
{
    struct cluster_info *cluster = policy->driver_data;
    u64 freq, volt;
    int i, ret, len = 0;
    mutex_lock(&cluster->mutex);
    for (i = 0; i < cluster->table_entry_num && len < PAGE_SIZE - 64; i++) {
        ret = dvfs->pair_get(cluster->id, i, &freq, &volt);
        if (ret) { len = ret; break; }
        len += sysfs_emit_at(buf, len, "%llu %llu\n", freq / 1000, volt);
    }
    mutex_unlock(&cluster->mutex);
    return len;
}
cpufreq_freq_attr_ro(scaling_voltage_table);
static ssize_t show_scaling_voltage_offset(struct cpufreq_policy *policy, char *buf)
{
    struct cluster_info *cluster = policy->driver_data;
    return sysfs_emit(buf, "%d\n", voltage_offset_uv[cluster->id]);
}
cpufreq_freq_attr_ro(scaling_voltage_offset);
static ssize_t show_scaling_voltage_domain(struct cpufreq_policy *policy, char *buf)
{
    struct cluster_info *cluster = policy->driver_data;
    return sysfs_emit(buf, "%u\n", cluster->id);
}
cpufreq_freq_attr_ro(scaling_voltage_domain);
static struct freq_attr *sprd_cpufreq_attr[] = {
    &cpufreq_freq_attr_scaling_available_freqs,
    &scaling_voltage_table, &scaling_voltage_offset, &scaling_voltage_domain, NULL,
};

static struct cpufreq_driver sprd_cpufreq_driver = {
	.name = "sprd-cpufreq-v2",
	.flags = CPUFREQ_NEED_INITIAL_FREQ_CHECK | CPUFREQ_HAVE_GOVERNOR_PER_POLICY,
	.init = sprd_cpufreq_init,
	.exit = sprd_cpufreq_exit,
	.verify = cpufreq_generic_frequency_table_verify,
	.target_index = sprd_cpufreq_set_target_index,
	.fast_switch = sprd_cpufreq_hw_fast_switch,
	.register_em = cpufreq_register_em_with_opp,
	.get = sprd_cpufreq_get,
	.suspend = sprd_cpufreq_suspend,
	.resume = sprd_cpufreq_resume,
	.online = sprd_cpufreq_online,
	.offline = sprd_cpufreq_offline,
	.attr = sprd_cpufreq_attr,
	.ready = sprd_cpufreq_ready,
};

/* init interface */
static int sprd_cluster_temp_init(struct cluster_info *cluster)
{
	const char *name = "sprd,temp-threshold";
	struct temp_node *node;
	struct property *prop;
	u32 num, val;
	int i, ret;

	INIT_LIST_HEAD(&cluster->temp_list_head);

	ret = sprd_temp_list_init(&cluster->temp_list_head);
	if (ret) {
		pr_err("init cluster %u temp limit error(%d)\n", cluster->id, ret);
		return ret;
	}

	prop = of_find_property(cluster->node, name, &num);
	if (!prop || !num) {
		pr_warn("find cluster %u temp property error\n", cluster->id);
		cluster->temp_enable = false;
		goto temp_node_init;
	}

	cluster->temp_enable = true;
	INIT_DELAYED_WORK(&cluster->temp_work, sprd_cpufreq_temp_work_func);

	if (of_property_read_string(cluster->node, "sprd,thmzone-names", &cluster->tz_name)) {
		pr_err("get cluster %u thmzone name error\n", cluster->id);
		return -EINVAL;
	}

	for (i = 0; i < num / sizeof(u32); i++) {
		ret = of_property_read_u32_index(cluster->node, name, i, &val);
		if (ret) {
			pr_err("get cluster %u temp error\n", cluster->id);
			return ret;
		}

		ret = sprd_temp_list_add(&cluster->temp_list_head, (int)val);
		if (ret) {
			pr_err("add cluster %u temp error(%d)\n", cluster->id, ret);
			return ret;
		}
	}

	if (cluster->fast_switch) {
		ret = sprd_temp_list_num_init(cluster, &cluster->temp_list_head);
		if (ret) {
			pr_err("init cluster %u temp list num error\n", cluster->id);
			return ret;
		}
	}

temp_node_init:
	node = sprd_temp_list_find(&cluster->temp_list_head, DVFS_TEMP_DEFAULT);
	if (!node) {
		pr_err("init cluster %u temp(%d) node error\n", cluster->id, DVFS_TEMP_DEFAULT);
		return -EINVAL;
	}

	cluster->temp_level_node = node;
	cluster->temp_currt_node = node;
	cluster->temp_tick = 0U;

	return 0;
}

/* voltage step / margin are pushed to the firmware, the transition delay stays on the cluster; pmic type selects the DCDC */
static int sprd_cluster_props_init(struct cluster_info *cluster)
{
	char dcdc_supply[32] = "sprd,pmic-type";
	int ret;

	ret = of_property_read_u32(cluster->node, "sprd,voltage-step", &cluster->voltage_step);
	if (ret) {
		pr_warn("get cluster %u 'sprd,voltage-step' value error\n", cluster->id);
		cluster->voltage_step = 0U;
	} else if (dvfs->step_set(cluster->id, cluster->voltage_step)) {
		pr_err("set cluster %u 'sprd,voltage-step' value error\n", cluster->id);
		return -EINVAL;
	}

	ret = of_property_read_u32(cluster->node, "sprd,voltage-margin", &cluster->voltage_margin);
	if (ret) {
		pr_warn("get cluster %u 'sprd,voltage-margin' value error\n", cluster->id);
		cluster->voltage_margin = 0U;
	} else if (dvfs->margin_set(cluster->id, cluster->voltage_margin)) {
		pr_err("set cluster %u 'sprd,voltage-margin' value error\n", cluster->id);
		return -EINVAL;
	}

	if (of_property_read_u32(cluster->node, "sprd,transition-delay", &cluster->transition_delay)) {
		pr_warn("get cluster %u 'sprd,transition-delay' value error\n", cluster->id);
		cluster->transition_delay = 0U;
	}

	if (of_property_read_bool(cluster->node, "sprd,multi-supply"))
		sprd_cluster_get_supply_mode(dcdc_supply);

	pr_info("cluster %u dcdc supply[%s]\n", cluster->id, dcdc_supply);

	ret = of_property_read_u32(cluster->node, dcdc_supply, &cluster->pmic_type);
	if (!ret && dvfs->pmic_set(cluster->id, cluster->pmic_type)) {
		pr_err("set cluster %u 'pmic-type' value error\n", cluster->id);
		return -EINVAL;
	}

	if (cluster->id == 0 && dvfs->dvfs_debug_init()) {
		pr_err("dvfs debug init error\n");
		return -EINVAL;
	}

	if (of_property_read_bool(cluster->node, "sprd,cpufreq-boost"))
		cluster->boost_enable = true;

	if (of_property_read_bool(cluster->node, "sprd,fast-switch"))
		cluster->fast_switch = true;

	return 0;
}

static struct device_node *sprd_cluster_node_init(u32 cpu_idx)
{
	struct device *cpu_dev = get_cpu_device(cpu_idx);

	if (!cpu_dev)
		return NULL;

	return of_parse_phandle(cpu_dev->of_node, "sprd,freq-domain", 0);
}

static int sprd_cluster_info_init(struct cluster_info *clusters)
{
	struct pm_qos_request latency_qos;
	struct cluster_info *cluster;
	unsigned int cpu;
	int ret = 0, cluster_index;

	/* the ASE test needs the CPUs awake */
	if (ase_driver_ops) {
		memset(&latency_qos, 0, sizeof(latency_qos));
		cpu_latency_qos_add_request(&latency_qos, 0);
	}

	for_each_possible_cpu(cpu) {
		cluster_index = sprd_cluster_info(cpu, NULL);
		if (cluster_index < 0 || cluster_index >= cluster_num) {
			pr_err("get cluster_index %d info error\n", cluster_index);
			return -EINVAL;
		}
		cluster = clusters + cluster_index;
		if (cluster->node) {
			pr_debug("cluster %u info is also init\n", cluster->id);
			continue;
		}

		cluster->node = sprd_cluster_node_init(cpu);
		if (!cluster->node) {
			pr_err("init cluster %u node error\n", cluster->id);
			ret = -EINVAL;
			goto remove_qos;
		}

		cluster->id = (u32)cluster_index;
		cluster->cpu = cpu;
		mutex_init(&cluster->mutex);

		ret = sprd_cluster_props_init(cluster);
		if (ret) {
			pr_err("init cluster %u props error(%d)\n", cluster->id, ret);
			goto remove_qos;
		}

		ret = sprd_cluster_temp_init(cluster);
		if (ret) {
			pr_err("init cluster %u temp error(%d)\n", cluster->id, ret);
			goto remove_qos;
		}

		cluster->ase_seq = of_property_read_bool(cluster->node->parent, "sprd,ase-init-seq");

		if (ase_driver_ops) {
			ret = sprd_cluster_ase_init(cluster);
			if (ret) {
				pr_err("init cluster %u ase error(%d)\n", cluster->id, ret);
				goto remove_qos;
			}
		}
	}

remove_qos:
	if (ase_driver_ops)
		cpu_latency_qos_remove_request(&latency_qos);

	return ret;
}

/* scufreq operations: the firmware SCU frequency (enable=1: hardware autotune, enable=0: fixed to index) */
static int sprd_cpufreq_scu_set(u32 enable, u32 index)
{
	struct cluster_info *cluster = pclusters;
	int ret;

	if (!cluster || !dvfs->scu_set)
		return -EINVAL;

	/* the ASE test flow talks to the firmware too and must not run in between */
	if (ase_driver_ops && ase_driver_ops->ase_device)
		mutex_lock(&ase_driver_ops->ase_device->ase_lock);

	mutex_lock(&cluster->mutex);
	ret = dvfs->scu_set(enable, index);
	if (ret)
		pr_err("set scu status %u and index %u error\n", enable, index);
	mutex_unlock(&cluster->mutex);

	if (ase_driver_ops && ase_driver_ops->ase_device)
		mutex_unlock(&ase_driver_ops->ase_device->ase_lock);

	return ret;
}

static int sprd_cpufreq_scu_get(u32 *index)
{
	struct cluster_info *cluster = pclusters;
	int ret;

	if (!cluster || !dvfs->scu_get)
		return -EINVAL;

	mutex_lock(&cluster->mutex);
	ret = dvfs->scu_get(index);
	mutex_unlock(&cluster->mutex);

	return ret;
}

static int sprd_cpufreq_scu_init(struct device *dev)
{
	if (!u30pro_scufreq || !of_property_read_bool(dev->of_node, "sprd,scu-soft"))
		return 0;

	scu_data = devm_kzalloc(dev, sizeof(*scu_data), GFP_KERNEL);
	if (!scu_data)
		return -ENOMEM;

	scu_data->cluster_num = cluster_num;
	scu_data->scu_info = devm_kcalloc(dev, cluster_num, sizeof(*scu_data->scu_info), GFP_KERNEL);
	if (!scu_data->scu_info)
		return -ENOMEM;

	scu_data->scu_ops.scu_set = sprd_cpufreq_scu_set;
	scu_data->scu_ops.scu_get = sprd_cpufreq_scu_get;
	sprd_cpufreq_driver.driver_data = scu_data;

	return 0;
}

/* the ASE device and its ops (drvdata of the ASE platform device); -EPROBE_DEFER until the ASE driver has probed */
static int sprd_cpufreq_ase_init(struct device *dev)
{
	struct device_node *dev_np;
	struct platform_device *ase_pdev;

	dev_np = of_parse_phandle(dev->of_node, "sprd,ase-device", 0);
	if (!dev_np) {
		pr_info("No need to enable ase\n");
		return 0;
	}

	ase_pdev = of_find_device_by_node(dev_np);

	of_node_put(dev_np);
	if (!ase_pdev) {
		pr_err("get ase sys platform device error\n");
		return -EINVAL;
	}

	ase_driver_ops = platform_get_drvdata(ase_pdev);
	if (!ase_driver_ops) {
		pr_err("ase device data has not been set\n");
		return -EPROBE_DEFER;
	}

	return 0;
}

static int sprd_cpufreq_driver_probe(struct platform_device *pdev)
{
	struct sprd_sip_svc_handle *sip;
	u32 flag[3] = {0};
	int ret;

	if (!u30pro_cpufreq) {
		dev_info(&pdev->dev, "apcpu DVFS is off; add u30pro_cpufreq=1 to the kernel command line to enable it\n");
		return -ENODEV;
	}

	sip = sprd_sip_svc_get_handle();
	if (!sip) {
		dev_err(&pdev->dev, "the firmware has no SIP DVFS service\n");
		return -ENODEV;
	}
	dvfs = &sip->dvfs_ops;

	if (firmware_initialized)
		return -EALREADY;
	sprd_cpufreq_get_debug_flag(flag);
	if ((flag[0] & 0xFF) == SPRD_DVFS_DEBUG_MAGIC) {
		pr_info("disable apcpu dvfs for debug!\n");
		return 0;
	}

	ret = sprd_voltage_flag(&flag[0]);
	if (ret)
		return ret;
	boot_done_timestamp = jiffies + SPRD_CPUFREQ_BOOST_DURATION;
	dev = &pdev->dev;

	if (ASE_ENABLED()) {
		ret = sprd_cpufreq_ase_init(dev);
		if (ret) {
			pr_err("init cpufreq ase error(%d)\n", ret);
			return ret;
		}
	} else if (of_property_read_bool(dev->of_node, "sprd,ase-device")) {
		pr_info("ASE aging compensation is off (u30pro_ase=1 turns it on); running with the factory voltage table\n");
	}

	cluster_num = sprd_cluster_num();
	if (cluster_num != ARRAY_SIZE(voltage_offset_uv)) {
		pr_err("no cpufreq cluster found\n");
		return -EINVAL;
	}

	ret = sprd_cpufreq_scu_init(dev);
	if (ret) {
		pr_err("init cpufreq scu error(%d)\n", ret);
		return ret;
	}

	pclusters = devm_kcalloc(dev, cluster_num, sizeof(*pclusters), GFP_KERNEL);
	if (!pclusters)
		return -ENOMEM;

	ret = sprd_cluster_info_init(pclusters);
	if (ret) {
		pr_err("init cluster info error(%d)\n", ret);
		return ret;
	}

	/* start the hardware DVFS logic of the firmware */
	firmware_initialized = true;
	ret = dvfs->dvfs_init(flag[0], flag[1], flag[2]);
	if (ret) {
		pr_err("init dvfs device error\n");
		return ret;
	}

	sprd_debug_init();

	ret = cpufreq_register_driver(&sprd_cpufreq_driver);
	if (ret) {
		pr_err("register cpufreq driver error(%d)\n", ret);
		sprd_debug_exit();
	} else {
		pr_info("register cpufreq driver success\n");
	}

	return ret;
}

static void sprd_cpufreq_driver_remove(struct platform_device *pdev)
{
	cpufreq_unregister_driver(&sprd_cpufreq_driver);
	sprd_debug_exit();
}

static const struct of_device_id sprd_cpufreq_of_match[] = {
	{ .compatible = "sprd,cpufreq-v2" },
	{ /* sentinel */ },
};

static struct platform_driver sprd_cpufreq_platform_driver = {
	.driver = {
		.name = "sprd-cpufreq-v2",
		.of_match_table = sprd_cpufreq_of_match,
		.suppress_bind_attrs = true,
	},
	.probe = sprd_cpufreq_driver_probe,
	.remove = sprd_cpufreq_driver_remove,
};

static int __init sprd_cpufreq_platform_driver_register(void)
{
	return platform_driver_register(&sprd_cpufreq_platform_driver);
}
device_initcall(sprd_cpufreq_platform_driver_register);

MODULE_DESCRIPTION("Unisoc apcpu hardware DVFS cpufreq driver");
MODULE_LICENSE("GPL");
