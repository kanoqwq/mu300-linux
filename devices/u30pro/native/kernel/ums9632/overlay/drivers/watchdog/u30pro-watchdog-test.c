// SPDX-License-Identifier: GPL-2.0-only
/* Virtual-only Linux watchdog-core fixtures. No physical timer or register I/O. */
#include <linux/module.h>
#include <linux/moduleparam.h>
#include <linux/of.h>
#include <linux/watchdog.h>
#include <linux/kernel.h>
struct fixture { struct watchdog_device wdd; unsigned long pings; };
static struct fixture fixtures[2];
static bool enable, reverse;
module_param(enable, bool, 0444);
module_param(reverse, bool, 0444);
static int get_pings(char *buf, const struct kernel_param *kp)
{
 struct fixture *f = kp->arg;
 return scnprintf(buf, PAGE_SIZE, "%lu\n", READ_ONCE(f->pings));
}
static const struct kernel_param_ops ping_ops = { .get = get_pings };
module_param_cb(ap_pings, &ping_ops, &fixtures[0], 0444);
module_param_cb(pmic_pings, &ping_ops, &fixtures[1], 0444);
static int fixture_ping(struct watchdog_device *wdd)
{
 struct fixture *f = watchdog_get_drvdata(wdd);
 WRITE_ONCE(f->pings, READ_ONCE(f->pings) + 1);
 return 0;
}
static int fixture_start(struct watchdog_device *wdd)
{
 set_bit(WDOG_HW_RUNNING, &wdd->status);
 return fixture_ping(wdd);
}
static int fixture_stop(struct watchdog_device *wdd)
{
 clear_bit(WDOG_HW_RUNNING, &wdd->status);
 return 0;
}
static int fixture_timeout(struct watchdog_device *wdd, unsigned int value)
{
 wdd->timeout = value;
 return 0;
}
static const struct watchdog_ops fixture_ops = {
 .owner = THIS_MODULE, .start = fixture_start, .stop = fixture_stop,
 .ping = fixture_ping, .set_timeout = fixture_timeout,
};
static const struct watchdog_info infos[2] = {
 { .identity = "Spreadtrum Watchdog Timer", .options = WDIOF_SETTIMEOUT | WDIOF_KEEPALIVEPING },
 { .identity = "U30 Pro UMP9650 watchdog", .options = WDIOF_SETTIMEOUT | WDIOF_KEEPALIVEPING },
};
static int __init fixture_init(void)
{
 int n, k, ret;
 if (!enable || !of_machine_is_compatible("linux,dummy-virt"))
  return 0;
 for (n = 0; n < 2; n++) {
  k = reverse ? 1 - n : n;
  fixtures[k].wdd.info = &infos[k];
  fixtures[k].wdd.ops = &fixture_ops;
  fixtures[k].wdd.min_timeout = 3;
  fixtures[k].wdd.max_timeout = k ? 300 : 60;
  fixtures[k].wdd.timeout = k ? 300 : 12;
  fixtures[k].wdd.max_hw_heartbeat_ms = fixtures[k].wdd.max_timeout * 1000;
  watchdog_set_drvdata(&fixtures[k].wdd, &fixtures[k]);
  watchdog_set_nowayout(&fixtures[k].wdd, true);
  set_bit(WDOG_HW_RUNNING, &fixtures[k].wdd.status);
  ret = watchdog_register_device(&fixtures[k].wdd);
  if (ret) {
   if (n)
    watchdog_unregister_device(&fixtures[reverse ? 1 : 0].wdd);
   return ret;
  }
 }
 pr_info("u30pro watchdog fixtures: AP=%d PMIC=%d (virtual counters only)\n",
         fixtures[0].wdd.id, fixtures[1].wdd.id);
 return 0;
}
module_init(fixture_init);
MODULE_DESCRIPTION("QEMU-only watchdog ioctl/ownership fixtures");
MODULE_LICENSE("GPL");
