// SPDX-License-Identifier: GPL-2.0
// Copyright (C) 2023 Unisoc, Inc.
/*
 * Unisoc ASE (aging sensor) driver for the UMS9632 ("sprd,ase-v0"), ported from the vendor driver drivers/unisoc_platform/ase/sprd-ase.c
 * to Linux 6.12 (GPL-2.0).  The ASE block measures how much the silicon has aged by running ring-oscillator tests at a fixed supply
 * voltage; the result is turned into an extra voltage that is handed to the subsystem's DVFS (cpufreq) through ase_ops.sys_update.
 *
 * Differences from the vendor driver: it does nothing unless u30pro_ase=1 is on the kernel command line (the test flow lowers the core
 * voltage through the secure monitor and the PMIC and runs on a timer 120 s after probe and then every 28 days, so it is kept off until
 * it has been checked on the device); devm_ioremap_resource() errors are really checked; the debugfs interface is not ported.
 */
#define pr_fmt(fmt) "sprd-ase: " fmt
#include <linux/cpu.h>
#include <linux/cpumask.h>
#include <linux/pm_wakeup.h>
#include <linux/pm_qos.h>
#include <linux/moduleparam.h>
#include <linux/sort.h>
#include <linux/sprd-ase.h>
#include <linux/sched.h>
#include <linux/suspend.h>
#include <linux/wait.h>

#define GENREGSET(r, o, m)		{.reg = r, .off = o, .msk = m, .val = 0}
#define GENREGVALSET(r, o, m, v)	{.reg = r, .off = o, .msk = m, .val = v}

#define ASE_WAKEUP_MS			(500U)
#define ASE_CNT_NUM			(6U)
#define ASE_CYCLE_TIME			(28 * 24 * 3600 * 1000ULL)

/* off unless u30pro_ase=1 is on the kernel command line (also read by the cpufreq driver and the generic ASE driver) */
int u30pro_ase;
core_param(u30pro_ase, u30pro_ase, int, 0444);
EXPORT_SYMBOL_GPL(u30pro_ase);

static struct ase_sys_info *pinfos;
static struct ase_device *parchdev;
static DECLARE_WAIT_QUEUE_HEAD(ase_wq);
static struct pm_qos_request ase_ctrl_pm_qos_latency;
static enum cpuhp_state ase_hp_online;

inline void ase_reg_off(struct ase_reg_info *dst, const struct ase_reg_info *src, u32 off)
{
	dst->reg = src->reg + off;
	dst->off = src->off;
	dst->msk = src->msk;
}

inline void ase_bit_off(struct ase_reg_info *dst, const struct ase_reg_info *src, u32 off)
{
	dst->reg = src->reg;
	dst->off = src->off + off;
	dst->msk = src->msk;
}

static void ase_bits_update(u32 reg, u32 msk, u32 val)
{
	u32 new_data;

	if (!parchdev) {
		pr_err("ase bits update failed\n");
		return;
	}

	new_data = readl((parchdev->base + reg)) & ~msk;

	new_data |= val & msk;

	writel(new_data, (parchdev->base + reg));
}

static void ase_bits_get(u32 reg, u32 msk, u32 *val)
{
	if (!parchdev || !val) {
		pr_err("ase bits get failed\n");
		return;
	}

	*val = readl((parchdev->base + reg)) & msk;
}

static int ase_start_cpu(unsigned int cpu)
{
	wake_up_all(&ase_wq);
	return 0;
}

static int sprd_ase_volt_calc(struct ase_sys_info *info, u32 *volt)
{
	u32 age_min, young_max, volt_calc;
	int c1, c2, c3;

	if (!info || !volt)
		return -EINVAL;

	sort(&info->age_calc[0], ASE_TEST_TIMES, sizeof(unsigned int), sort_ase_cmp, sort_ase_swap);
	age_min = info->age_calc[1];

	sort(&info->young_calc[0], ASE_TEST_TIMES, sizeof(unsigned int), sort_ase_cmp,
	     sort_ase_swap);
	young_max = info->young_calc[ASE_TEST_TIMES - 2];

	if (age_min < young_max)
		c2 = -(((young_max - age_min) * parchdev->priv->ase_clk * 10000) /
		       parchdev->priv->cfg_duration.val);
	else
		c2 = ((age_min - young_max) * parchdev->priv->ase_clk * 10000) /
			parchdev->priv->cfg_duration.val;

	if (info->young_src & (0x40UL))
		c1 = info->young_src & 0x3f;
	else
		c1 = -(info->young_src & 0x3f);

	c3 = (c2 - c1 * 1000);

	if (c3 < 0)
		volt_calc = ((-info->ase_h) * c3 * 1000) / info->age_src;
	else
		volt_calc = 0;

	volt_calc = (volt_calc / DCDC_STEP_VOL) * DCDC_STEP_VOL;

	*volt = clamp(volt_calc, 0, ASE_MAX_VOLT);

	return 0;
}

static int sprd_ase_normal_init(struct ase_sys_info *info)
{
	const struct ase_reg_info *regdata;

	/* Rst ASE */
	regmap_update_bits(parchdev->aonmap, parchdev->rst_reg, parchdev->rst_bit,
			   parchdev->rst_bit);
	udelay(1);
	regmap_update_bits(parchdev->aonmap, parchdev->rst_reg, parchdev->rst_bit,
			   ~parchdev->rst_bit);

	/* Init ASE duration */
	regdata = &parchdev->priv->cfg_duration;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase duration reginfo is empty\n");
		return -EINVAL;
	}

	ase_bits_update(regdata->reg, regdata->msk << regdata->off,
			parchdev->priv->cfg_duration.val << regdata->off);

	/* Set Chain and Sequence select */
	regdata = &parchdev->priv->sel_x;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase sel x reginfo is empty\n");
		return -EINVAL;
	}
	ase_bits_update(regdata->reg, regdata->msk << regdata->off, info->chain << regdata->off);

	regdata = &parchdev->priv->sel_y;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase sel y reginfo is empty\n");
		return -EINVAL;
	}
	ase_bits_update(regdata->reg, regdata->msk << regdata->off, info->sequence << regdata->off);

	/* Set ASE work mode */
	regdata = &parchdev->priv->work_mode;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase work mode reginfo is empty\n");
		return -EINVAL;
	}
	ase_bits_update(regdata->reg, regdata->msk << regdata->off,
			ASE_NORMAL_MODE << regdata->off);

	udelay(1);

	return 0;
}

static int sprd_ase_fsm_check(struct ase_sys_info *info)
{
	const struct ase_reg_info *regdata;
	u32 fsm_val = 0, fail_val = 0;
	unsigned long time_limit = 0;

	regdata = &parchdev->priv->cur_state;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase cur state reginfo is empty\n");
		return -EINVAL;
	}

	time_limit = jiffies + msecs_to_jiffies(10);
	while (time_before(jiffies, time_limit)) {
		ase_bits_get(regdata->reg, regdata->msk << regdata->off, &fsm_val);

		if (fsm_val == INIT_FAIL || fsm_val == ASE_IDLE)
			break;
	}

	if (fsm_val != INIT_FAIL && fsm_val != ASE_IDLE) {
		pr_err("ase(%u) fsm state is timeout with(%u)\n", info->id, fsm_val);
		return -EINVAL;
	}

	if (fsm_val == INIT_FAIL) {
		regdata = &info->init_fail;
		if (!regdata->reg && !regdata->off) {
			pr_err("ase(%u) init fail reginfo is empty\n", info->id);
			return -EINVAL;
		}

		ase_bits_get(regdata->reg, BIT(regdata->off), &fail_val);
		if (fail_val) {
			pr_err("ase(%u) power supply is no ready\n", info->id);
			return -EINVAL;
		}

		regdata = &parchdev->idle_mode;
		if (!regdata->reg && !regdata->off) {
			pr_err("ase idle mode reginfo is empty\n");
			return -EINVAL;
		}
		ase_bits_update(regdata->reg, BIT(regdata->off),
				BIT(regdata->off));
	}

	return 0;
}

static int sprd_ase_config_value(struct ase_sys_info *info)
{
	const struct ase_reg_info *regdata;
	u32 fail_val = 0;

	regdata = &parchdev->config_fail;
	if (!regdata->reg && !regdata->off) {
		pr_err("ase config fail reginfo is empty\n");
		return -EINVAL;
	}
	ase_bits_get(regdata->reg, BIT(regdata->off), &fail_val);

	if (fail_val) {
		pr_err("ase(%u) config para is error\n", info->id);
		return -EINVAL;
	}

	return 0;
}

static int sprd_ase_rd_value(struct ase_sys_info *info)
{
	const struct ase_reg_info *regdata;
	unsigned long time_limit = 0;
	u32 rd_valid = 0;

	regdata = &parchdev->priv->rd_valid;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase rd valid reginfo is empty\n");
		return -EINVAL;
	}

	time_limit = jiffies + msecs_to_jiffies(10);
	while (time_before(jiffies, time_limit)) {
		ase_bits_get(regdata->reg, BIT(regdata->off), &rd_valid);
		if (rd_valid)
			break;
	}

	if (!rd_valid) {
		pr_err("ase(%u) rd valid is timeout\n", info->id);
		return -EINVAL;
	}

	return 0;
}

static int sprd_ase_normal_mode(struct ase_sys_info *info, u32 *age_cnt, u32 *young_cnt)
{
	const struct ase_reg_info *regdata;
	int ret;

	if (!parchdev) {
		pr_err("no ase device found\n");
		return -ENODEV;
	}

	/* Normal mode init */
	ret = sprd_ase_normal_init(info);
	if (ret)
		return ret;

	/* Check FSM state */
	ret = sprd_ase_fsm_check(info);
	if (ret)
		return ret;

	/* Check config value */
	ret = sprd_ase_config_value(info);
	if (ret)
		return ret;

	/* Make ASE running */
	regdata = &parchdev->priv->run_ctrl;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase run ctrl reginfo is empty\n");
		return -EINVAL;
	}
	ase_bits_update(regdata->reg, BIT(regdata->off), BIT(regdata->off));

	/* Check RD valid */
	ret = sprd_ase_rd_value(info);
	if (ret)
		return ret;

	/* Read ASE count */
	regdata = &info->age_calc_reg;
	ase_bits_get(regdata->reg, regdata->msk << regdata->off, age_cnt);

	regdata = &info->young_calc_reg;
	ase_bits_get(regdata->reg, regdata->msk << regdata->off, young_cnt);

	return 0;
}

static int sprd_ase_nvmem_read(struct device_node *node, const char *name, u32 *value)
{
	struct nvmem_cell *cell;
	void *buf;
	size_t len = 0;
	int ret = 0;

	cell = of_nvmem_cell_get(node, name);
	if (IS_ERR(cell)) {
		ret = PTR_ERR(cell);
		if (ret == -EPROBE_DEFER)
			pr_warn("cell for ase not ready, retry\n");
		else
			pr_err("failed to get cell(%s) for ase, ret(%d)\n", name, ret);

		return ret;
	}

	buf = nvmem_cell_read(cell, &len);
	nvmem_cell_put(cell);

	if (IS_ERR(buf))
		return PTR_ERR(buf);

	if (len > sizeof(u32))
		len = sizeof(u32);

	memcpy(value, buf, len);
	kfree(buf);
	buf = NULL;

	return 0;
}

static int sprd_ase_sys_trigger(u32 sys_id, u32 *volt_margin)
{
	struct ase_sys_info *info;
	u32 volt;
	int i, ret = 0;

	if (!parchdev) {
		pr_err("no ase device found\n");
		return -ENODEV;
	}

	if (sys_id >= parchdev->sys_num || !volt_margin) {
		pr_err("ase(%u) or parameter is error\n", sys_id);
		return -EINVAL;
	}

	info = pinfos + sys_id;
	if (!info) {
		pr_err("failed to get the info of ase id(%u)\n", sys_id);
		return -EINVAL;
	}

	if (!info->young_src || !info->age_src) {
		pr_info("ase(%u) init para is not ready\n", sys_id);
		*volt_margin = 0;
		return 0;
	}

	for (i = 0; i < ASE_TEST_TIMES; i++) {
		ret = sprd_ase_normal_mode(info, &info->age_calc[i], &info->young_calc[i]);
		if (ret) {
			pr_err("ase(%u) failed to trigger(%d)\n", sys_id, ret);
			info->aging_volt = ASE_MAX_VOLT;
			return ret;
		}
	}

	ret = parchdev->priv->volt_calc(info, &volt);
	if (ret) {
		pr_err("ase(%u) failed to calc volt\n", sys_id);
		return ret;
	}

	*volt_margin = volt;

	info->aging_volt = volt;

	return ret;
}

static int sprd_ase_test_flow(struct ase_sys_info *info)
{
	struct ase_ops *ops = info->sys_ops;
	u32 volt;
	int ret = 0, ret2 = 0;

	if (!ops || !ops->sys_prepare || !ops->sys_update) {
		pr_err("ase(%u) ops error\n", info->id);
		return -EINVAL;
	}

	cpu_latency_qos_update_request(&ase_ctrl_pm_qos_latency, 0);

	wait_event(ase_wq, cpumask_any_and(&info->cpus, cpu_online_mask) < nr_cpu_ids);

	__pm_stay_awake(parchdev->wake_lock);
	cpu_hotplug_disable();

	if (cpumask_any_and(&info->cpus, cpu_online_mask) >= nr_cpu_ids) {
		cpu_hotplug_enable();
		__pm_relax(parchdev->wake_lock);
		cpu_latency_qos_update_request(&ase_ctrl_pm_qos_latency, PM_QOS_DEFAULT_VALUE);
		pr_err("ase(%u) is offline\n", info->id);
		return -1;
	}

	mutex_lock(&parchdev->ase_lock);

	ret = ops->sys_prepare(info->id, info->test_volt);
	if (ret) {
		pr_err("ase(%u) prepare ops error(%d)\n", info->id, ret);
		goto unlock_ret;
	}

	ret2 = sprd_ase_sys_trigger(info->id, &volt);
	if (ret2) {
		pr_err("ase(%u) normal work error(%d)\n", info->id, ret);
		volt = info->aging_volt;
	}

	pr_info("ase(%u) update volt margin(%u)\n", info->id, volt);

	ret = ops->sys_update(info->id, volt);
	if (ret)
		pr_err("ase(%u) update ops error(%d)\n", info->id, ret);

unlock_ret:
	mutex_unlock(&parchdev->ase_lock);
	cpu_hotplug_enable();
	__pm_relax(parchdev->wake_lock);

	cpu_latency_qos_update_request(&ase_ctrl_pm_qos_latency, PM_QOS_DEFAULT_VALUE);

	return (ret || ret2);
}

static void sprd_ase_work_callback(struct work_struct *work)
{
	struct ase_sys_info *info = container_of(work, struct ase_sys_info, ase_work);
	int ret;

	if (!info) {
		pr_err("get ase work sys info error\n");
		return;
	}

	ret = sprd_ase_test_flow(info);
	if (ret) {
		pr_err("ase(%u) ase_cnt(%d) test error(%d)\n", info->id,
		       atomic_read(&info->ase_cnt), ret);
		atomic_inc(&info->ase_cnt);
		msleep(2000);
		if (atomic_read(&info->ase_cnt) <= ASE_CNT_NUM)
			queue_work(parchdev->ase_wq_struct, &info->ase_work);
	}

}

static enum alarmtimer_restart sprd_ase_alarm_callback(struct alarm *alarm, ktime_t now)
{
	struct ase_sys_info *info;
	int i;

	if (!parchdev) {
		pr_err("no ase device found\n");
		return ALARMTIMER_NORESTART;
	}

	for (i = 0; i < parchdev->sys_num; i++) {
		info = pinfos + i;
		if (!info) {
			pr_err("failed to get the info of sys id(%u)\n", i);
			continue;
		}

		if (info->tested) {
			info->tested = false;
			continue;
		}

		if (!info->age_src || !info->young_src)
			continue;

		if (IS_ERR_OR_NULL(info->sys_ops)) {
			pr_err("sys(%u) is not init sys ops\n", info->id);
			continue;
		}

		atomic_set(&info->ase_cnt, 0);

		queue_work(parchdev->ase_wq_struct, &info->ase_work);
	}

	alarm_forward(alarm, now, ms_to_ktime(parchdev->cycle_time_ms));

	return ALARMTIMER_RESTART;
}

static int sprd_ase_sys_info_init(struct ase_sys_info *info)
{
	const struct ase_reg_info *regdata;
	struct device_node *np = info->node;
	int ret, ic;
	u32 cpumask;

	ic = strlen(np->full_name) + 1;
	info->full_name = devm_kzalloc(parchdev->dev, ic, GFP_KERNEL);
	if (!info->full_name)
		return -ENOMEM;

	strscpy(info->full_name, np->full_name, ic - 1);

	info->chain = info->id;

	ret = of_property_read_u32(np, "sprd,cpumask", &cpumask);
	if (!ret) {
		info->bind_enable = true;
		info->cpus.bits[0] = (unsigned long)cpumask;
	}

	ret = sprd_ase_nvmem_read(np, "ase_slope", &info->age_src);
	if (ret)
		return ret;

	ret = sprd_ase_nvmem_read(np, "ase_delta_src", &info->young_src);
	if (ret)
		return ret;

	ret = of_property_read_u32(np, "sprd,test-volt", &info->test_volt);
	if (ret) {
		pr_err("get ase sys(%u) test volt error\n", info->id);
		return ret;
	}

	ret = of_property_read_u32(np, "sprd,ase-h", &info->ase_h);
	if (ret) {
		pr_err("get ase sys(%u) ase_h  error\n", info->ase_h);
		return ret;
	}

	/* Init ase work */
	INIT_WORK(&info->ase_work, sprd_ase_work_callback);

	/* Init age calc reginfo */
	regdata = &parchdev->priv->calc_base;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase calc base reginfo is empty\n");
		return -EINVAL;
	}

	ase_reg_off(&info->age_calc_reg, regdata, info->chain * 8);
	ase_reg_off(&info->young_calc_reg, regdata, info->chain * 8 + 4);

	/* Init init_fail reginfo */
	regdata = &parchdev->priv->init_fail;
	ase_bit_off(&info->init_fail, regdata, 32 - parchdev->sys_num + info->chain);

	return 0;
}

static int sprd_ase_info_init(struct ase_device *pdev)
{
	struct device_node *node = pdev->of_node;
	struct ase_sys_info *pinfo;
	int ret, i;

	pinfos = devm_kzalloc(pdev->dev, sizeof(*pinfos) * pdev->sys_num, GFP_KERNEL);
	if (!pinfos)
		return -ENOMEM;

	for (i = 0; i < pdev->sys_num; i++) {
		pinfo = pinfos + i;
		pinfo->id = i;
		atomic_set(&pinfo->ase_cnt, 0);
		pinfo->node = of_parse_phandle(node, "sprd,ase-subsys", i);
		if (!pinfo->node) {
			pr_err("failed to find ase sys(%d) data\n", i);
			return -ENOENT;
		}

		ret = sprd_ase_sys_info_init(pinfo);
		if (ret) {
			pr_err("init sys(%d) info error(%d)\n", i, ret);
			return ret;
		}

		ret = sprd_ase_debug_sys_init(pinfo);
		if (ret) {
			pr_err("init ase debug sys error\n");
			return ret;
		}
	}

	return 0;
}

static int sprd_ase_device_init(struct ase_device *pdev)
{
	struct device_node *node = pdev->of_node;
	u32 args[2], enable_reg, ase_eb_msk;
	const struct ase_reg_info *regdata;
	struct property *prop;
	int ret, num;

	pdev->cycle_time_ms = ASE_CYCLE_TIME;
	pdev->duration = pdev->priv->cfg_duration.val;

	/* Enable ASE clk */
	pdev->aonmap = syscon_regmap_lookup_by_phandle_args(node, "enable-syscon", 2, args);
	if (IS_ERR(pdev->aonmap)) {
		pr_err("no dvfs module enable-syscon\n");
		return PTR_ERR(pdev->aonmap);
	}

	enable_reg = args[0];
	ase_eb_msk = args[1];

	ret = regmap_update_bits(pdev->aonmap, enable_reg, ase_eb_msk, ase_eb_msk);
	if (ret) {
		pr_err("failed to enable dvfs module\n");
		return ret;
	}

	/* Init ASE rst info */
	prop = of_find_property(node, "sprd,ase-rst", &num);
	if (!prop) {
		pr_err("No 'sprd,ase-rst' property found\n");
		return -ENODEV;
	}

	if (num / sizeof(u32) == 2) {
		ret = of_property_read_u32_index(node, "sprd,ase-rst", 0, &pdev->rst_reg);
		if (ret)
			return ret;

		ret = of_property_read_u32_index(node, "sprd,ase-rst", 1, &pdev->rst_bit);
		if (ret)
			return ret;

	} else {
		pr_err("Failed to get ase rst info\n");
		return -EINVAL;
	}

	/* Init ASE subsys num */
	if (!of_find_property(node, "sprd,ase-subsys", &num)) {
		pr_err("get ase 'sprd,ase-subsys' error\n");
		return -EINVAL;
	}

	num = num / sizeof(u32);
	pdev->sys_num = num;

	/* Init ASE idle mode reginfo */
	regdata = &parchdev->priv->idle_mode;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase idle mode reginfo is empty\n");
		return -EINVAL;
	}

	ase_reg_off(&pdev->idle_mode, regdata, pdev->sys_num * 8);

	/* Init ASE config fail reginfo */
	regdata = &parchdev->priv->init_fail;
	if (!regdata->reg && !regdata->off && !regdata->msk) {
		pr_err("ase init fail reginfo is empty\n");
		return -EINVAL;
	}

	ase_bit_off(&pdev->config_fail, regdata, 31 - pdev->sys_num);

	/* Init ASE debugfs */
	ret = sprd_ase_debug_init(pdev);
	if (ret) {
		pr_err("init ase debug error(%d)\n", ret);
		return ret;
	}

	return 0;
};

static int sprd_ase_sys_ops_init(u32 sys_id, struct ase_ops *ops, bool is_test)
{
	struct ase_sys_info *info;
	int ret;

	if (!parchdev) {
		pr_err("no ase device found\n");
		return -ENODEV;
	}

	if (sys_id >= parchdev->sys_num || !ops) {
		pr_err("the ase id(%u) or parameter is error\n", sys_id);
		return -EINVAL;
	}

	info = pinfos + sys_id;
	if (!info) {
		pr_err("failed to get the info of ase id(%u)\n", sys_id);
		return -EINVAL;
	}

	info->sys_ops = ops;

	if (is_test) {
		ret = sprd_ase_test_flow(info);
		if (ret) {
			pr_err("ase(%u) first test error(%d)\n", sys_id, ret);
			return ret;
		}

		info->tested = true;
	}

	return 0;
}

static struct sprd_ase_ops ase_driver_ops = {
	.ops_init = sprd_ase_sys_ops_init,
	.ase_device = NULL,
};

static int sprd_ase_driver_probe(struct platform_device *pdev)
{
	const struct ase_private_data *pdata;
	struct device_node *np;
	void __iomem *base;
	int ret;

	if (!u30pro_ase) {
		dev_info(&pdev->dev, "ASE is off; add u30pro_ase=1 to the kernel command line to enable it\n");
		return -ENODEV;
	}

	parchdev = devm_kzalloc(&pdev->dev, sizeof(struct ase_device), GFP_KERNEL);
	if (!parchdev)
		return -ENOMEM;

	ase_hp_online = cpuhp_setup_state_nocalls(CPUHP_AP_ONLINE_DYN, "ase:on_line",
						  ase_start_cpu, NULL);
	if (ase_hp_online < 0) {
		pr_err("ase:on_line error\n");
		return -EINVAL;
	}

	ase_driver_ops.ase_device = parchdev;

	np = pdev->dev.of_node;
	if (!np) {
		pr_err("ase has not found device node\n");
		return -ENODEV;
	}

	pdata = of_device_get_match_data(&pdev->dev);
	if (!pdata) {
		pr_err("failed to find private driver data\n");
		return -EINVAL;
	}

	base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(base)) {
		pr_err("failed to remap the ase register\n");
		return PTR_ERR(base);
	}

	parchdev->ase_wq_struct = alloc_ordered_workqueue("ase-wq", WQ_FREEZABLE);
	if (!parchdev->ase_wq_struct)
		return -ENOMEM;

	parchdev->of_node = np;
	parchdev->dev = &pdev->dev;
	parchdev->priv = pdata;
	parchdev->base = base;

	mutex_init(&parchdev->ase_lock);

	ret = sprd_ase_device_init(parchdev);
	if (ret) {
		pr_err("failed to initialize the ase device\n");
		goto unmap_base;
	}

	ret = sprd_ase_info_init(parchdev);
	if (ret) {
		pr_err("failed to initialize the ase info\n");
		goto unmap_base;
	}

	parchdev->wake_lock = wakeup_source_register(parchdev->dev, dev_name(&pdev->dev));
	if (!parchdev->wake_lock) {
		dev_err(&pdev->dev, "register wakeup lock fail\n");
		goto unmap_base;
	}

	/* Save platform device private data */
	platform_set_drvdata(pdev, &ase_driver_ops);

	/* Alarm init & start */
	alarm_init(&parchdev->ase_alarm, ALARM_BOOTTIME, sprd_ase_alarm_callback);
	alarm_start_relative(&parchdev->ase_alarm, ms_to_ktime(120 * 1000));

	cpu_latency_qos_add_request(&ase_ctrl_pm_qos_latency, PM_QOS_DEFAULT_VALUE);
	pr_info("sprd ase is probed\n");

	return 0;

unmap_base:
	wakeup_source_unregister(parchdev->wake_lock);
	mutex_destroy(&parchdev->ase_lock);
	devm_iounmap(&pdev->dev, parchdev->base);
	parchdev->base = NULL;
	return ret;
}

static void sprd_ase_driver_remove(struct platform_device *pdev)
{
	alarm_cancel(&parchdev->ase_alarm);
	cpuhp_remove_state_nocalls(ase_hp_online);
	destroy_workqueue(parchdev->ase_wq_struct);
	wakeup_source_unregister(parchdev->wake_lock);
	sprd_ase_debug_exit();
}

static const struct ase_private_data ase_v0_private_data = {
	.cfg_duration = GENREGVALSET(0x0, 17, 0x3fff, 4096),
	.sel_x = GENREGSET(0x0, 8, 0xf),
	.sel_y = GENREGSET(0x0, 4, 0xf),
	.work_mode = GENREGSET(0x0, 0, 0x3),
	.run_ctrl = GENREGSET(0x4, 0, 0x1),
	.cur_state = GENREGSET(0x10, 0, 0xf),
	.rd_valid = GENREGSET(0xc, 0, 0x1),
	.init_fail = GENREGSET(0xc, 0, 0x1),
	.idle_mode = GENREGSET(0x14, 0, 0x1),
	.calc_base = GENREGSET(0x14, 0, 0xfffff),
	.volt_calc = sprd_ase_volt_calc,
	.ase_clk = 26,
};

static const struct of_device_id sprd_ase_of_match[] = {
	{
		.compatible = "sprd,ase-v0",
		.data = &ase_v0_private_data,
	}, {
		/* Sentinel */
	},
};


static struct platform_driver sprd_ase_driver = {
	.driver = {
		.name = "sprd-ase",
		.of_match_table = sprd_ase_of_match,
	},
	.probe = sprd_ase_driver_probe,
	.remove = sprd_ase_driver_remove,
};

static int __init sprd_ase_driver_register(void)
{
	return platform_driver_register(&sprd_ase_driver);
}

device_initcall(sprd_ase_driver_register);

MODULE_DESCRIPTION("sprd ase driver");
MODULE_LICENSE("GPL");
