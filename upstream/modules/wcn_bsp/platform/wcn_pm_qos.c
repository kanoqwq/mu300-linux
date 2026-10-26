// SPDX-License-Identifier: GPL-2.0
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
#include <linux/cpufreq.h>
#include <misc/wcn_bus.h>
#include "wcn_glb.h"
#include "wcn_gnss.h"
#include "wcn_procfs.h"
#include "wcn_pm_qos.h"

static struct wcn_pm_qos *wpq;

struct wcn_pm_qos *wcn_pm_qos_get(void)
{
	return wpq;
}

static void wcn_pm_qos_cpu_pd_forbid(struct pm_qos_request *pm_qos)
{
	pm_qos_update_request(pm_qos, CPU_DMA_FORBID_POWER_DOWN_LATENCY);
}

static void wcn_pm_qos_cpu_pd_allow(struct pm_qos_request *pm_qos)
{
	pm_qos_update_request(pm_qos, PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE);
}

static int wcn_freq_qos_update_request_up(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int i = 0, ret = 0;

	if (IS_ERR_OR_NULL(pqos))
		return -EINVAL;

	for (i = 0; i < ARRAY_SIZE(pqos->wcn_freq_qos_req); i++) {
		/* ret = freq_qos_update_request(&pqos->wcn_freq_qos_req[i], pqos->max_freq[i]); */
		ret = freq_qos_update_request(&pqos->wcn_freq_qos_req[i],
				pqos->cpu_freq_table[i][CPU_FREQ_LEVEL_INDEX]);
		if (ret < 0) {
			WCN_ERR("%s freq update failed(%d) %d\n", __func__, i, ret);
			goto out;
		}
		WCN_DBG("update CPU%d scaling_min_freq to %u\n", i + CPU_CORE_NUM_OFFSET,
				pqos->cpu_freq_table[i][CPU_FREQ_LEVEL_INDEX]);
	}

	return 0;

out:
	for (--i; i >= 0; i--) {
		ret = freq_qos_update_request(&pqos->wcn_freq_qos_req[i],
				pqos->cpu_freq_table[i][CPU_FREQ_LEVEL_INDEX]);
		if (ret < 0) {
			WCN_ERR("%s freq restore failed(%d) %d\n", __func__, i, ret);
		}
	}

	return -1;
}

static int wcn_freq_qos_update_request_down(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int i = 0, ret = 0;

	if (IS_ERR_OR_NULL(pqos))
		return -EINVAL;

	for (i = 0; i < ARRAY_SIZE(pqos->wcn_freq_qos_req); i++) {
		ret = freq_qos_update_request(&pqos->wcn_freq_qos_req[i], pqos->min_freq[i]);
		if (ret < 0) {
			WARN(ret, "%s freq update failed(%d) %d\n", __func__, i, ret);
			return -1;
		}
		WCN_DBG("update CPU%d scaling_min_freq to %u\n", i + CPU_CORE_NUM_OFFSET,
				pqos->min_freq[i]);
	}

	return 0;
}

static bool wcn_freq_pm_qos_swicth(unsigned long constraint, bool *cpu_pd_set, bool *ddr_freq_set)
{
	*cpu_pd_set = false;
	*ddr_freq_set = false;

	if (test_bit(WIFI_TX_HIGH_THROUGHPUT, &constraint)) {
		*cpu_pd_set = true;
		*ddr_freq_set = true;
		return true;
	}

	if (test_bit(WIFI_RX_HIGH_THROUGHPUT, &constraint)) {
		*cpu_pd_set = true;
		*ddr_freq_set = true;
		return true;
	}

	if (test_bit(WIFI_AP, &constraint) && test_bit(BT_OPP, &constraint) &&
		test_bit(BT_A2DP, &constraint))
		return true;

	return false;
}

static int  wcn_pm_qos_request(unsigned long constraint_pending)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	bool freq_set = false, cpu_pd_set = false, ddr_freq_set;
	int ret = 0;

	if (IS_ERR_OR_NULL(pqos))
		return -EINVAL;

	WCN_INFO("%s constraint=0x%lx, cond=%d,%d,%d,%d, set_flag=%d,%d,%d\n",
		__func__, constraint_pending, pqos->cond.blank_cond, pqos->cond.cpu_pd_forbid,
		pqos->cond.cpu_freq_change, pqos->cond.ddr_freq_change, pqos->freq_set_flag,
		pqos->cpu_pd_set_flag, pqos->ddr_freq_set_flag);

	freq_set = wcn_freq_pm_qos_swicth(constraint_pending, &cpu_pd_set, &ddr_freq_set);
	WCN_INFO("%s freq_set=%d, cpu_pd_set=%d, ddr_freq_set=%d\n", __func__,
				freq_set, cpu_pd_set, ddr_freq_set);

	if (pqos->cond.cpu_freq_change) {
		if (freq_set && pqos->freq_set_flag == false) {
			WCN_INFO("%s CPU freq up\n", __func__);
			ret = wcn_freq_qos_update_request_up();
			if (ret >= 0)
				pqos->freq_set_flag = true;
		} else if (!freq_set && pqos->freq_set_flag == true) {
			WCN_INFO("%s CPU freq down\n", __func__);
			ret = wcn_freq_qos_update_request_down();
			if (ret >= 0)
				pqos->freq_set_flag = false;
		} else
			WCN_DBG("Ignore set CPU frequency request\n");
	}

	if (pqos->cond.cpu_pd_forbid) {
		if (cpu_pd_set && pqos->cpu_pd_set_flag == false) {
			WCN_INFO("%s CPU forbid powerdown\n", __func__);
			wcn_pm_qos_cpu_pd_forbid(&pqos->pm_qos_req);
			pqos->cpu_pd_set_flag = true;
		} else if (!cpu_pd_set && pqos->cpu_pd_set_flag == true) {
			WCN_INFO("%s CPU allow powerdown\n", __func__);
			wcn_pm_qos_cpu_pd_allow(&pqos->pm_qos_req);
			pqos->cpu_pd_set_flag = false;
		}
	}

	if (pqos->cond.ddr_freq_change) {
		/* setting DDR frequency is not supported! */
		if (ddr_freq_set && pqos->ddr_freq_set_flag == false) {
			pqos->ddr_freq_set_flag = true;
			/* scene_dfs_request("wcn"); */
		} else if (!ddr_freq_set && pqos->ddr_freq_set_flag == true) {
			pqos->ddr_freq_set_flag = false;
			/* scene_exit(); */
		}
	}
	return ret;
}

void wcn_pm_qos_enable(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int ret = 0;

	if (IS_ERR_OR_NULL(pqos) || !pqos->cond.blank_cond)
		return;

	if (!pqos->pm_qos_disable) {
		WCN_INFO("%s Already\n", __func__);
		return;
	}

	mutex_lock(&pqos->mutex);

	pqos->pm_qos_disable = false;

	if (pqos->active_modules_pending != pqos->active_modules) {
		WCN_INFO("%s: set pm_qos (%lx to %lx)\n", __func__,
				pqos->active_modules, pqos->active_modules_pending);
		ret = wcn_pm_qos_request(pqos->active_modules_pending);
	}

	if (ret >= 0)
		pqos->active_modules = pqos->active_modules_pending;
	else
		WCN_INFO("%s: failed to set pm_qos\n", __func__);

	mutex_unlock(&pqos->mutex);
}
EXPORT_SYMBOL_GPL(wcn_pm_qos_enable);

void wcn_pm_qos_disable(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int ret = 0;

	if (IS_ERR_OR_NULL(pqos) || !pqos->cond.blank_cond)
		return;

	if (pqos->pm_qos_disable) {
		WCN_INFO("%s Already\n", __func__);
		return;
	}
	mutex_lock(&pqos->mutex);
	pqos->pm_qos_disable = true;

	/* Restore Default */
	ret = wcn_pm_qos_request(0);
	if (ret >= 0)
		pqos->active_modules = 0;
	else
		WCN_INFO("%s: failed to set pm_qos\n", __func__);

	mutex_unlock(&pqos->mutex);
}
EXPORT_SYMBOL_GPL(wcn_pm_qos_disable);

int wcn_pm_qos_condition_config(struct wcn_pm_qos_condition *cond)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();

	if (IS_ERR_OR_NULL(pqos))
		return -EINVAL;

	memcpy(&pqos->cond, cond, sizeof(struct wcn_pm_qos_condition));
	pqos->cond_set = true;
	if (cond->blank_cond) {
		WCN_INFO("%s default on screen, disable pm_qos\n", __func__);
		wcn_pm_qos_disable();
	}

	return 0;
}

int wcn_pm_qos_config_common(unsigned int mode, bool set)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int ret = 0;

	if (IS_ERR_OR_NULL(pqos) || mode >= BT_MAX)
		return -EINVAL;

	mutex_lock(&pqos->mutex);

	if (set)
		set_bit(mode, &pqos->active_modules_pending);
	else
		clear_bit(mode, &pqos->active_modules_pending);

	if (pqos->cond.blank_cond && pqos->pm_qos_disable) {
		WCN_INFO("%s: conditions are not met, set later(0x%lx)\n", __func__,
				pqos->active_modules_pending);
		goto unlock;
	}

	if (pqos->active_modules_pending != pqos->active_modules)
		ret = wcn_pm_qos_request(pqos->active_modules_pending);

	if (ret >= 0)
		pqos->active_modules = pqos->active_modules_pending;
	else
		WCN_INFO("%s: failed to set pm_qos\n", __func__);
unlock:
	mutex_unlock(&pqos->mutex);

	return ret;
}

void wcn_pm_qos_reset(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int ret = 0;

	if (IS_ERR_OR_NULL(pqos))
		return;

	mutex_lock(&pqos->mutex);

	pqos->active_modules = 0;
	pqos->active_modules_pending = 0;
	ret = wcn_pm_qos_request(0);
	WARN((ret < 0), "CPU freq cannot be recovered");

	mutex_unlock(&pqos->mutex);
}
EXPORT_SYMBOL_GPL(wcn_pm_qos_reset);

static void wcn_cpu_freq_table_get(struct cpufreq_policy *policy, int cpu_index)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	struct cpufreq_frequency_table *pos = NULL;
	unsigned int freq_table_index = 0;

	if (IS_ERR_OR_NULL(pqos))
		return;

	cpufreq_for_each_valid_entry(pos, policy->freq_table) {
		if ((pos->flags & CPUFREQ_BOOST_FREQ))
			continue;

		pqos->cpu_freq_table[cpu_index][freq_table_index] = pos->frequency;
		WCN_DBG("%s: CPU%d freq_table[%d]=%u\n", __func__, cpu_index + CPU_CORE_NUM_OFFSET,
			freq_table_index, pqos->cpu_freq_table[cpu_index][freq_table_index]);
		freq_table_index++;
	}
	pqos->cpu_freq_table_num[cpu_index] = freq_table_index;
}

static int wcn_freq_pm_qos_init(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int ret = 0, i = 0, j = 0;

	if (IS_ERR_OR_NULL(pqos))
		return -ENOMEM;

	for (i = CPU_CORE_NUM_OFFSET; i < ARRAY_SIZE(pqos->wcn_freq_qos_req); i++, j++) {
		pqos->dev = get_cpu_device(i);
		if (unlikely(!pqos->dev)) {
			WCN_ERR("%s: No cpu device for cpu%d\n", __func__, i);
			ret = -ENODEV;
			goto fail;
		}

		pqos->policy = cpufreq_cpu_get(i);
		if (pqos->policy == NULL) {
			WCN_ERR("%s cpufreq_cpu_get failed\n", __func__);
			ret = -ENODEV;
			goto fail;
		}
		pqos->min_freq[j] = pqos->policy->cpuinfo.min_freq;
		pqos->max_freq[j] = pqos->policy->cpuinfo.max_freq;

		wcn_cpu_freq_table_get(pqos->policy, j);
		WCN_INFO("%s: CPU%d->%d min_freq=%lu,max_freq=%lu,requset freq=%lu\n", __func__,
			i, j, pqos->policy->cpuinfo.min_freq, pqos->policy->cpuinfo.max_freq,
			pqos->cpu_freq_table[j][CPU_FREQ_LEVEL_INDEX]);

		ret = freq_qos_add_request(&pqos->policy->constraints, &pqos->wcn_freq_qos_req[j],
			FREQ_QOS_MIN, pqos->policy->cpuinfo.min_freq);

		cpufreq_cpu_put(pqos->policy);
		if (ret < 0) {
			WCN_INFO("freq_qos_add_request failed (CPU%d->%d)%d\n", i, j, ret);
			goto fail;
		}
	}

	return 0;

fail:
	for (--j; j >= 0; j--)
		freq_qos_remove_request(&pqos->wcn_freq_qos_req[j]);

	return -1;
}

static void wcn_freq_pm_qos_exit(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();
	int ret = 0, j = 0;

	if (IS_ERR_OR_NULL(pqos))
		return;

	for (j = 0; j < ARRAY_SIZE(pqos->wcn_freq_qos_req); j++) {
		ret = freq_qos_remove_request(&pqos->wcn_freq_qos_req[j]);
		if (ret < 0)
			WCN_INFO("freq_qos_remove_request failed %d %d\n", j, ret);
	}
}

struct wcn_pm_qos_condition pm_qos_cond[] = {
	[0] = {false, true, false, false}, /* HW_TYPE_SDIO */
	[1] = {false, false, false, false}, /* HW_TYPE_PCIE */
	[2] = {true, false, true, false}, /* HW_TYPE_SIPC */
};

int wcn_pm_qos_init(void)
{
	struct wcn_pm_qos *pqos = NULL;
	int ret = 0;

	pqos = kvzalloc(sizeof(*pqos), GFP_KERNEL);
	if (pqos == NULL) {
		WCN_INFO("%s fail to malloc\n", __func__);
		return -ENOMEM;
	}
	wpq = pqos;
	mutex_init(&pqos->mutex);

	ret = sprdwcn_bus_get_hwintf_type();
	if (ret != HW_TYPE_INVALIED)
		wcn_pm_qos_condition_config(&pm_qos_cond[sprdwcn_bus_get_hwintf_type()]);

	pm_qos_add_request(&pqos->pm_qos_req, PM_QOS_CPU_DMA_LATENCY,
		PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE);

	ret = wcn_freq_pm_qos_init();
	if (ret < 0)
		goto failed;

	return 0;

failed:
	pm_qos_remove_request(&pqos->pm_qos_req);
	wpq = NULL;
	kzfree(pqos);
	return ret;
}

void wcn_pm_qos_exit(void)
{
	struct wcn_pm_qos *pqos = wcn_pm_qos_get();

	if (IS_ERR_OR_NULL(pqos))
		return;

	WCN_INFO("%s enter\n", __func__);

	wcn_freq_pm_qos_exit();

	pm_qos_remove_request(&pqos->pm_qos_req);
	mutex_destroy(&pqos->mutex);

	wpq = NULL;
	kzfree(pqos);
}
