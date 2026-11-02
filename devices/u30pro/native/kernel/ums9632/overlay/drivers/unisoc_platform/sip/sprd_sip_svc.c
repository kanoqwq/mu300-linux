// SPDX-License-Identifier: GPL-2.0
/*
 * Unisoc SIP firmware service: the SMC interface to the secure monitor (SML/BL31) of the UMS9632.
 *
 * Derived from the vendor driver drivers/unisoc_platform/sip/sprd_sip_svc.c (Copyright 2021-2023 Unisoc, GPL-2.0).
 * Only the part the native kernel uses is kept: the DVFS service that the apcpu cpufreq driver talks to.  The CPU frequency/voltage
 * tables, the clock tree switching and the PMIC voltage control live in the firmware; Linux only picks table indexes.
 *
 * Every function id is SMC32 fast call, owner SIP (0x82000000 | id).  Unlike the vendor code the service is only offered when the
 * firmware really answers the DVFS revision call, so that an older secure monitor (or QEMU) never sees the other calls.
 */
#define pr_fmt(fmt) "sprd_sip: " fmt

#include <linux/arm-smccc.h>
#include <linux/errno.h>
#include <linux/init.h>
#include <linux/module.h>
#include <linux/printk.h>
#include <linux/types.h>
#include <soc/unisoc/sprd_sip_svc.h>

#define SIP_FN(id)	ARM_SMCCC_CALL_VAL(ARM_SMCCC_FAST_CALL, ARM_SMCCC_SMC_32, ARM_SMCCC_OWNER_SIP, (id))

#define SPRD_SIP_SVC_DVFS_REV		SIP_FN(0x0600)
#define SPRD_SIP_SVC_DVFS_ENABLE	SIP_FN(0x0601)
#define SPRD_SIP_SVC_DVFS_TABLE_UPDATE	SIP_FN(0x0602)
#define SPRD_SIP_SVC_DVFS_STEP_SET	SIP_FN(0x0603)
#define SPRD_SIP_SVC_DVFS_MARGIN_SET	SIP_FN(0x0604)
#define SPRD_SIP_SVC_DVFS_FREQ_SET	SIP_FN(0x0605)
#define SPRD_SIP_SVC_DVFS_FREQ_GET	SIP_FN(0x0606)
#define SPRD_SIP_SVC_DVFS_PAIR_GET	SIP_FN(0x0607)
#define SPRD_SIP_SVC_DVFS_PMIC_SET	SIP_FN(0x0608)
#define SPRD_SIP_SVC_DVFS_INIT		SIP_FN(0x060b)
#define SPRD_SIP_SVC_DVFS_DEBUG_INIT	SIP_FN(0x060c)
#define SPRD_SIP_SVC_DVFS_ASE_INIT	SIP_FN(0x0611)
#define SPRD_SIP_SVC_DVFS_ASE_SET	SIP_FN(0x0612)
#define SPRD_SIP_SVC_DVFS_VOTE_SCU_GET	SIP_FN(0x0613)
#define SPRD_SIP_SVC_DVFS_SCU_SET	SIP_FN(0x0614)
#define SPRD_SIP_SVC_DVFS_SCU_GET	SIP_FN(0x0615)

#define SPRD_SIP_RET_UNK		0xFFFFFFFFUL	/* SMCCC "not supported" in w0 */

static struct sprd_sip_svc_handle sprd_sip_svc_handle;
static bool sprd_sip_dvfs_present;

/*
 * Returns the SIP handle, or NULL when this machine has no SMC conduit or the secure monitor does not implement the SIP DVFS
 * service.  Clients must check for NULL.
 */
struct sprd_sip_svc_handle *sprd_sip_svc_get_handle(void)
{
	return sprd_sip_dvfs_present ? &sprd_sip_svc_handle : NULL;
}
EXPORT_SYMBOL(sprd_sip_svc_get_handle);

static int sprd_sip_remap_err(unsigned long err)
{
	return err == 0 ? 0 : -EINVAL;
}

static int sprd_sip_svc_dvfs_enable(u32 cluster)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_ENABLE, cluster, 0, 0, 0, 0, 0, 0, &res);
	return res.a0;
}

static int sprd_sip_svc_dvfs_table_update(u32 cluster, u32 temp, u32 *num, u32 sign)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_TABLE_UPDATE, cluster, temp, sign, 0, 0, 0, 0, &res);
	if (num)
		*num = res.a1;
	return res.a0;
}

static int sprd_sip_svc_dvfs_step_set(u32 cluster, u32 step)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_STEP_SET, cluster, step, 0, 0, 0, 0, 0, &res);
	return res.a0;
}

static int sprd_sip_svc_dvfs_margin_set(u32 cluster, u32 margin)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_MARGIN_SET, cluster, margin, 0, 0, 0, 0, 0, &res);
	return res.a0;
}

static int sprd_sip_svc_dvfs_freq_set(u32 cluster, u32 index)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_FREQ_SET, cluster, index, 0, 0, 0, 0, 0, &res);
	return res.a0;
}

static int sprd_sip_svc_dvfs_freq_get(u32 cluster, u64 *freq)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_FREQ_GET, cluster, 0, 0, 0, 0, 0, 0, &res);
	if (freq)
		*freq = res.a1;
	return res.a0;
}

static int sprd_sip_svc_dvfs_pair_get(u32 cluster, u32 index, u64 *freq, u64 *vol)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_PAIR_GET, cluster, index, 0, 0, 0, 0, 0, &res);
	if (freq)
		*freq = res.a1;
	if (vol)
		*vol = res.a2;
	return res.a0;
}

static int sprd_sip_svc_dvfs_pmic_set(u32 cluster, u32 num)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_PMIC_SET, cluster, num, 0, 0, 0, 0, 0, &res);
	return res.a0;
}

/* @flag: voltage increase/decrease debug flags, @flag2: fixed min/max frequency, @flag3: fixed min voltage (sprdboot.dvfs_set) */
static int sprd_sip_svc_dvfs_init(u32 flag, u32 flag2, u32 flag3)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_INIT, flag, flag2, flag3, 0, 0, 0, 0, &res);
	return res.a0;
}

static int sprd_sip_svc_dvfs_debug_init(void)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_DEBUG_INIT, 0, 0, 0, 0, 0, 0, 0, &res);
	return res.a0;
}

static int sprd_sip_svc_dvfs_vote_scu_get(u32 cluster, u32 index, u32 *vote_scu)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_VOTE_SCU_GET, cluster, index, 0, 0, 0, 0, 0, &res);
	if (vote_scu)
		*vote_scu = res.a1;
	return res.a0;
}

static int sprd_sip_svc_dvfs_scu_set(u32 enable, u32 index)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_SCU_SET, enable, index, 0, 0, 0, 0, 0, &res);
	return res.a0;
}

static int sprd_sip_svc_dvfs_scu_get(u32 *index)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_SCU_GET, 0, 0, 0, 0, 0, 0, 0, &res);
	if (index)
		*index = res.a1;
	return res.a0;
}

static int sprd_sip_svc_dvfs_ase_init(u32 cluster, u32 test_volt)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_ASE_INIT, cluster, test_volt, 0, 0, 0, 0, 0, &res);
	return sprd_sip_remap_err(res.a0);
}

static int sprd_sip_svc_dvfs_ase_set(u32 cluster, u32 age_volt, u32 *num)
{
	struct arm_smccc_res res;

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_ASE_SET, cluster, age_volt, 0, 0, 0, 0, 0, &res);
	if (num)
		*num = res.a1;
	return sprd_sip_remap_err(res.a0);
}

static int __init sprd_sip_svc_init(void)
{
	struct arm_smccc_res res;
	struct sprd_sip_svc_dvfs_ops *ops = &sprd_sip_svc_handle.dvfs_ops;

	if (arm_smccc_1_1_get_conduit() == SMCCC_CONDUIT_NONE) {
		pr_info("no SMC conduit, SIP services are not available\n");
		return 0;
	}

	arm_smccc_smc(SPRD_SIP_SVC_DVFS_REV, 0, 0, 0, 0, 0, 0, 0, &res);
	if ((u32)res.a0 == SPRD_SIP_RET_UNK) {
		pr_info("the secure monitor has no SIP DVFS service\n");
		return 0;
	}
	ops->rev.major_ver = (u32)res.a0;
	ops->rev.minor_ver = (u32)res.a1;

	ops->dvfs_enable = sprd_sip_svc_dvfs_enable;
	ops->table_update = sprd_sip_svc_dvfs_table_update;
	ops->step_set = sprd_sip_svc_dvfs_step_set;
	ops->margin_set = sprd_sip_svc_dvfs_margin_set;
	ops->freq_set = sprd_sip_svc_dvfs_freq_set;
	ops->freq_get = sprd_sip_svc_dvfs_freq_get;
	ops->pair_get = sprd_sip_svc_dvfs_pair_get;
	ops->pmic_set = sprd_sip_svc_dvfs_pmic_set;
	ops->dvfs_init = sprd_sip_svc_dvfs_init;
	ops->dvfs_debug_init = sprd_sip_svc_dvfs_debug_init;
	ops->ase_init = sprd_sip_svc_dvfs_ase_init;
	ops->ase_set = sprd_sip_svc_dvfs_ase_set;
	ops->vote_scu_get = sprd_sip_svc_dvfs_vote_scu_get;
	ops->scu_set = sprd_sip_svc_dvfs_scu_set;
	ops->scu_get = sprd_sip_svc_dvfs_scu_get;
	sprd_sip_dvfs_present = true;

	pr_notice("SIP DVFS service v%u.%u detected in the firmware\n", ops->rev.major_ver, ops->rev.minor_ver);
	return 0;
}
subsys_initcall(sprd_sip_svc_init);

MODULE_DESCRIPTION("Unisoc SIP firmware service (DVFS)");
MODULE_LICENSE("GPL");
