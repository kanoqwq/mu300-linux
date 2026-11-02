// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2020 Unisoc Inc.
 */

#include <linux/module.h>
#include <linux/init.h>
#include <linux/syscore_ops.h>
#include <linux/u30pro_psci_hooks.h>
#include <linux/sched/clock.h>
#include <linux/timekeeping.h>
#include <linux/trusty/smcall.h>
#include <asm/arch_timer.h>

#include "trusty.h"

#ifdef pr_fmt
#undef pr_fmt
#endif
#define pr_fmt(fmt) "sprd-trusty-pm: " fmt

/*
 * This call synchronizes linux boot time to trusty OS by calling two SMC
 * fastcalls. One call passes arm arch timer counter to trusty OS to estimate
 * the period(T1) in which a SMC fastcall is sent from linux to trusty OS.
 * The other one passed the linux boot time(T2).
 * Assume that anytime the period of the SMC fastcall is same. The linux boot
 * time when trusty OS gets the second fastcall should be T1+T2.
 */
static int trusty_sync_boot_time(void)
{
	int ret = 0;
	u64 boot_time;
	u64 cnt;

	/*
	 * Use arm generic virtual timer counter as reference counter
	 * comparing to physical timer counter used in trusty OS.
	 * The offset between virtual timer and physical timer will be ingored.
	 */
	cnt = __arch_counter_get_cntvct();
	ret = trusty_fast_call32(NULL, SMC_FC_SYNC_TIMER_CNT,
				 (u32)cnt,  (u32)(cnt >> 32), 0);
	if (ret) {
		pr_err("Trusty fastcall SMC_FC_SYNC_TIMER_CNT failed(%d)\n",
		       ret);
		return ret;
	}

	/*
	 * Get linux boot time that includes system suspend time
	 */
	boot_time = ktime_get_boot_fast_ns();
	ret = trusty_fast_call32(NULL, SMC_FC_SYNC_BOOT_TIME,
				 (u32)boot_time, (u32)(boot_time >> 32), 0);
	if (ret) {
		pr_err("Trusty fastcall SMC_FC_SYNC_BOOT_TIME failed(%d)\n",
		       ret);
	}

	return ret;
}

static void trusty_pm_resume(void)
{
	trusty_sync_boot_time();
}

static struct syscore_ops trusty_pm_ops = {
	.resume = trusty_pm_resume,
};

/*
 * The secure OS tells whether a CPU can be taken down: SMC_FC_CPU_CAN_DOWN answers 0 while the secure OS is resident on the CPU (or has work
 * for it).  The vendor kernel hangs this on two Android PSCI vendor hooks; here it is installed through the hook pointers of psci.c
 * (include/linux/u30pro_psci_hooks.h).  Without it, a CPU that powers down in a PSCI idle state while the secure OS needs it breaks the
 * secure-world channel (the baseband start times out on the "kernelbootcp" service).
 */
static bool trusty_resident_on_cpu(int cpu)
{
	return !trusty_fast_call32(NULL, SMC_FC_CPU_CAN_DOWN, cpu, 0, 0);
}

static bool trusty_check_cpu_suspend(u32 state)
{
	return trusty_resident_on_cpu(smp_processor_id());
}

static int __init trusty_pm_init(void)
{
	/* Fist time sync on boot up */
	trusty_sync_boot_time();

	register_syscore_ops(&trusty_pm_ops);

	u30pro_psci_tos_resident_on = trusty_resident_on_cpu;
	u30pro_psci_cpu_suspend_deny = trusty_check_cpu_suspend;

	return 0;
}

module_init(trusty_pm_init);

MODULE_DESCRIPTION("Sprd trusty pm driver");
MODULE_LICENSE("GPL");
