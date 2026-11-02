/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright 2021-2023 Unisoc(Shanghai) Technologies Co.Ltd
 *
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#ifndef __SPRD_SIP_SVC_H__
#define __SPRD_SIP_SVC_H__

#include <linux/arm-smccc.h>
#include <linux/init.h>
#include <linux/types.h>

#ifdef CONFIG_ARM64
#define SPRD_SIP_SMC_CONVENTION          ARM_SMCCC_SMC_64
#else
#define SPRD_SIP_SMC_CONVENTION          ARM_SMCCC_SMC_32
#endif

#define SPRD_SIP_SMC_CMD(fn_id) \
	ARM_SMCCC_CALL_VAL(ARM_SMCCC_FAST_CALL, SPRD_SIP_SMC_CONVENTION, \
			   ARM_SMCCC_OWNER_SIP, fn_id)

/*
 * Provides interfaces to SoC implementation-specific services on this
 * platform, for example secure platform initialization, configuration,
 * and some power control services.
 */

/* structure definitions */

/**
 * struct sprd_sip_svc_rev_info - version information structure
 *
 * @major_ver: Major ABI version. Change here implies risk of backward
 *	compatibility break.
 * @minor_ver: Minor ABI version. Change here implies new feature addition,
 *	or compatible change in ABI.
 */
struct sprd_sip_svc_rev_info {
	u32 major_ver;
	u32 minor_ver;
};

/**
 * struct sprd_sip_svc_perf_ops - represents the various operations provided
 *	by SPRD SIP PERF
 *
 * @set_freq: sets the frequency of a given device
 * @get_freq: gets the frequency of a given device
 *
 * @set_div: sets the clock divisor of a given device
 * @get_div: gets the clock divisor of a given device
 *
 * @set_parent: sets the parent of a given device
 * @get_parent: gets the parent of a given device
 */
struct sprd_sip_svc_perf_ops {
	struct sprd_sip_svc_rev_info rev;

	int (*set_freq)(u32 id, u32 parent_id, u32 freq);
	int (*get_freq)(u32 id, u32 parent_id, u32 *p_freq);

	int (*set_div)(u32 id, u32 div);
	int (*get_div)(u32 id, u32 *p_div);

	int (*set_parent)(u32 id, u32 parent_id);
	int (*get_parent)(u32 id, u32 *p_parent_id);
};

/**
 * struct sprd_sip_svc_dbg_ops - represents the various operations provided
 *	by SPRD SIP DBG
 *
 * @set_hang_handle: set hang handle
 */
struct sprd_sip_svc_dbg_ops {
	struct sprd_sip_svc_rev_info rev;

	int (*set_hang_hdl)(uintptr_t hdl, uintptr_t pdg, unsigned long level);
	int (*get_hang_ctx)(unsigned long id, unsigned long core_id, uintptr_t *val);
};

/**
 * struct sprd_sip_svc_pwr_ops - represents the various operations
 *      provided by SPRD SIP POWER
 *
 * @get_wakeup_source: gets the wakeup source
 *
 */
struct sprd_sip_svc_pwr_ops {
	struct sprd_sip_svc_rev_info rev;
	int (*get_wakeup_source)(u32 *major, u32 *second, u32 *thrid);
	u64 (*pdbg_info_trans)(u32 scene, u32 priv0, u32 priv1, u32 priv2,
			       struct arm_smccc_res *ret);
};

/**
 * struct sprd_sip_svc_dvfs_ops - represents the various operations
 *      provided by SPRD SIP DVFS
 *
 * @dvfs_enable: enable cluster dvfs
 * @table_update: update dvfs table for cluster
 *
 * @step_set: set the cluster dcdc update step
 * @margin_set: set the cluster dcdc update margin
 *
 * @freq_set: set cluster frequency
 * @freq_get: get cluster current frequency
 * @pair_get: get freq and volt of index
 *
 * @pmic_set: set the dcdc_cpu uses pmic type
 * @dvfs_init: apcpu hwdvfs logic init entry
 */
struct sprd_sip_svc_dvfs_ops {
	struct sprd_sip_svc_rev_info rev;

	int (*dvfs_enable)(u32 cluster);
	int (*table_update)(u32 cluster, u32 temp, u32 *num, u32 sign);
	int (*step_set)(u32 cluster, u32 step);
	int (*margin_set)(u32 cluster, u32 margin);
	int (*freq_set)(u32 cluster, u32 index);
	int (*freq_get)(u32 cluster, u64 *freq);
	int (*pair_get)(u32 cluster, u32 index, u64 *freq, u64 *vol);
	int (*pmic_set)(u32 cluster, u32 num);
	int (*dvfs_init)(u32 flag, u32 flag2, u32 flag3);
	int (*dvfs_debug_init)(void);
	int (*ase_init)(u32 cluster, u32 test_volt);
	int (*ase_set)(u32 cluster, u32 age_volt, u32 *num);
	int (*vote_scu_get)(u32 cluster, u32 index, u32 *vote_scu);
	int (*scu_set)(u32 enable, u32 index);
	int (*scu_get)(u32 *index);
};



/**
 * struct sprd_sip_svc_gpu_ops - represents the various operations
 * provided by SPRD SIP GPU
 *
 * @get_id: gets the chip id and gpu bin
 * @update_voltage_list: update the voltage index corresponding meaning for DVFS
 *
 */
struct sprd_sip_svc_gpu_ops {
	struct sprd_sip_svc_rev_info rev;
	int (*get_id)(u32 *chip_id, u32 *bin_index);
	int (*update_voltage_list)(u32 temp, u32 pos);
	int (*version_set)(u64 *ver);
	int (*ase_set)(u32 age_volt);
};

/**
 * struct sprd_sip_svc_cachedump_ops - represents the various operations
 * provided by SPRD SIP CACHEDUMP
 *
 * @cachedump_func_api: execute cachedump function
 * @mesi: 0x1001 means save INVALID and MODIFED dcache
 * @sec: 0x1 means save no_security dcache
 * @valid: 0x1100 means save A64+A32 in A76, but means save Invalid+T32 in A55
 */
struct sprd_sip_svc_cachedump_ops {
	struct sprd_sip_svc_rev_info rev;

	int (*cachedump_func_api)(uint8_t mesi, uint8_t valid);
};

/**
 * struct sprd_sip_svc_socdump_ops - represents the various operations
 * provided by SPRD SIP SOCDUMP
 *
 * @socdump_func_api: execute socdump function
 */
struct sprd_sip_svc_socdump_ops {
	struct sprd_sip_svc_rev_info rev;

	int (*socdump_func_api)(void);
};

/**
 * struct sprd_sip_svc_handle - Handle returned to SPRD SIP clients for usage
 *
 * @perf_ops: pointer to set of performance operations
 * @dbg_ops: pointer to set of dbg operations
 * @gpu_ops: pointer to set of gpu operations
 */
struct sprd_sip_svc_handle {
	struct sprd_sip_svc_perf_ops perf_ops;
	struct sprd_sip_svc_dbg_ops dbg_ops;
	struct sprd_sip_svc_pwr_ops pwr_ops;
	struct sprd_sip_svc_dvfs_ops dvfs_ops;
	struct sprd_sip_svc_gpu_ops gpu_ops;
	struct sprd_sip_svc_cachedump_ops cachedump_ops;
	struct sprd_sip_svc_socdump_ops socdump_ops;
};

/**
 * sprd_sip_svc_get_handle() - returns a pointer to SPRD SIP handle
 *
 * Return: a pointer to SPRD_SIP handle
 */
struct sprd_sip_svc_handle *sprd_sip_svc_get_handle(void);

#endif /* __SPRD_SIP_SVC_H__ */
