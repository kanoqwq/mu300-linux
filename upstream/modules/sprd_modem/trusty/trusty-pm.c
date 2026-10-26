#include <linux/module.h>
#include <linux/init.h>
#include <linux/syscore_ops.h>
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

static void trusty_pm_resume(void *data)
{
	trusty_sync_boot_time();
}

static const struct syscore_ops trusty_pm_ops = {
	.resume = trusty_pm_resume,
};

static struct syscore trusty_pm_syscore = {
	.ops = &trusty_pm_ops,
};


static int __init trusty_pm_init(void)
{
	/* Fist time sync on boot up */
	trusty_sync_boot_time();

	register_syscore(&trusty_pm_syscore);
	/*
	 * MU300: the ACK PSCI vendor hooks (keep the CPU that Trusty resides on from powering down) do not exist in
	 * mainline; the secure firmware migrates or refuses on its own (PSCI MIGRATE_INFO_TYPE: not supported).
	 */

	return 0;
}

static void __exit trusty_pm_exit(void)
{
	unregister_syscore(&trusty_pm_syscore);
}

module_init(trusty_pm_init);
module_exit(trusty_pm_exit);

MODULE_DESCRIPTION("Sprd trusty pm driver");
MODULE_LICENSE("GPL v2");
