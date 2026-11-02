// SPDX-License-Identifier: GPL-2.0-only
/* Sleep code is needed for arm64 CPU shutdown during kexec. Board sleep and
 * cross-processor watchdog ownership have not been validated, so reject PM
 * preparation before freezing tasks or suspending a single device.
 * Kexec uses kernel_kexec(), not the system-sleep notifier chain.
 */
#include <linux/init.h>
#include <linux/notifier.h>
#include <linux/of.h>
#include <linux/suspend.h>

static int u30pro_sleep_prepare(struct notifier_block *nb,
			      unsigned long event, void *unused)
{
	switch (event) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
		pr_warn("u30pro: system sleep unavailable in kexec bootstrap\n");
		return notifier_from_errno(-EBUSY);
	default:
		return NOTIFY_DONE;
	}
}

static struct notifier_block u30pro_sleep_nb = {
	.notifier_call = u30pro_sleep_prepare,
};

static int __init u30pro_kexec_base_init(void)
{
	int ret;

	if (!of_machine_is_compatible("sprd,ums9632"))
		return 0;
	ret = register_pm_notifier(&u30pro_sleep_nb);
	if (ret)
		panic("u30pro: cannot install bootstrap sleep guard: %d", ret);
	pr_info("u30pro: kexec bootstrap active; system sleep guarded\n");
	return 0;
}
early_initcall(u30pro_kexec_base_init);
