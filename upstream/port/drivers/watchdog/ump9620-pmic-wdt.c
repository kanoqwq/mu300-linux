// SPDX-License-Identifier: GPL-2.0-only
/*
 * UMP9620 PMIC watchdog (same register layout as sprd_wdt, behind the ADI regmap). LK arms it for 300 s and, on
 * Android, the PM co-processor firmware takes it over. Mainline has no PM firmware, so this driver takes it over
 * instead: it shortens the timeout and lets the watchdog core ping it until userspace opens /dev/watchdog. A hard
 * hang then resets the board, and LK falls back to Android. MU300 bring-up driver.
 *
 * The watchdog runs on the PMIC's 32 kHz clock and keeps counting while the AP sleeps, where nothing pings it
 * (Android has the PM co-processor feed it): a system sleep longer than the timeout reset the board (FINDINGS 37).
 * It is stopped as the last thing before the sleep and armed again, with a fresh count, as the first thing after
 * it - the vendor's sprd_wdt does the same for the AP watchdog.
 */
#include <linux/bits.h>
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/watchdog.h>

#define WDT_LOAD_LOW	0x00
#define WDT_LOAD_HIGH	0x04
#define WDT_CTRL	0x08
#define WDT_INT_RAW	0x10
#define WDT_LOCK	0x20
#define WDT_CNT_EN	BIT(1)
#define WDT_RST_EN	BIT(3)
#define WDT_LD_BUSY	BIT(4)
#define WDT_UNLOCK_KEY	0xe551
#define WDT_CNT_STEP	32768
#define WDT_DEFAULT	60
#define WDT_MAX		300

struct ump9620_wdt {
	struct watchdog_device wdd;
	struct regmap *map;
	u32 base;
	bool suspended;		/* stopped for a system sleep, to be armed again on resume */
};

static int ump9620_wdt_load(struct ump9620_wdt *w, unsigned int timeout)
{
	u32 cnt = timeout * WDT_CNT_STEP, raw;
	int i;

	regmap_write(w->map, w->base + WDT_LOCK, WDT_UNLOCK_KEY);
	regmap_write(w->map, w->base + WDT_LOAD_HIGH, cnt >> 16);
	regmap_write(w->map, w->base + WDT_LOAD_LOW, cnt & 0xffff);
	regmap_write(w->map, w->base + WDT_LOCK, (u16)~WDT_UNLOCK_KEY);
	/* the new load value is taken over at the next 32 kHz edge */
	for (i = 0; i < 11; i++) {
		regmap_read(w->map, w->base + WDT_INT_RAW, &raw);
		if (!(raw & WDT_LD_BUSY))
			return 0;
		usleep_range(10, 100);
	}
	return -EBUSY;
}

static int ump9620_wdt_ctrl(struct ump9620_wdt *w, bool on)
{
	regmap_write(w->map, w->base + WDT_LOCK, WDT_UNLOCK_KEY);
	regmap_update_bits(w->map, w->base + WDT_CTRL, WDT_CNT_EN | WDT_RST_EN, on ? WDT_CNT_EN | WDT_RST_EN : 0);
	regmap_write(w->map, w->base + WDT_LOCK, (u16)~WDT_UNLOCK_KEY);
	return 0;
}

static int ump9620_wdt_start(struct watchdog_device *wdd)
{
	struct ump9620_wdt *w = watchdog_get_drvdata(wdd);
	int ret = ump9620_wdt_load(w, wdd->timeout);

	if (ret)
		return ret;
	set_bit(WDOG_HW_RUNNING, &wdd->status);
	return ump9620_wdt_ctrl(w, true);
}

static int ump9620_wdt_stop(struct watchdog_device *wdd)
{
	struct ump9620_wdt *w = watchdog_get_drvdata(wdd);

	return ump9620_wdt_ctrl(w, false);
}

static int ump9620_wdt_ping(struct watchdog_device *wdd)
{
	return ump9620_wdt_load(watchdog_get_drvdata(wdd), wdd->timeout);
}

static int ump9620_wdt_set_timeout(struct watchdog_device *wdd, unsigned int t)
{
	wdd->timeout = t;
	return ump9620_wdt_ping(wdd);
}

static int ump9620_wdt_suspend_noirq(struct device *dev)
{
	struct ump9620_wdt *w = dev_get_drvdata(dev);
	u32 ctrl = 0;

	regmap_read(w->map, w->base + WDT_CTRL, &ctrl);
	w->suspended = ctrl & WDT_CNT_EN;
	if (w->suspended)
		ump9620_wdt_ctrl(w, false);
	return 0;
}

static int ump9620_wdt_resume_noirq(struct device *dev)
{
	struct ump9620_wdt *w = dev_get_drvdata(dev);
	int ret;

	if (!w->suspended)
		return 0;
	w->suspended = false;
	ret = ump9620_wdt_load(w, w->wdd.timeout);
	if (ret)
		dev_warn(dev, "reload after the sleep: %d\n", ret);
	return ump9620_wdt_ctrl(w, true);
}

static const struct dev_pm_ops ump9620_wdt_pm_ops = {
	NOIRQ_SYSTEM_SLEEP_PM_OPS(ump9620_wdt_suspend_noirq, ump9620_wdt_resume_noirq)
};

static const struct watchdog_info ump9620_wdt_info = {
	.options = WDIOF_SETTIMEOUT | WDIOF_KEEPALIVEPING | WDIOF_MAGICCLOSE,
	.identity = "UMP9620 PMIC watchdog",
};

static const struct watchdog_ops ump9620_wdt_ops = {
	.owner = THIS_MODULE,
	.start = ump9620_wdt_start,
	.stop = ump9620_wdt_stop,
	.ping = ump9620_wdt_ping,
	.set_timeout = ump9620_wdt_set_timeout,
};

static int ump9620_wdt_probe(struct platform_device *pdev)
{
	struct ump9620_wdt *w;
	u32 ctrl;
	int ret;

	w = devm_kzalloc(&pdev->dev, sizeof(*w), GFP_KERNEL);
	if (!w)
		return -ENOMEM;
	w->map = dev_get_regmap(pdev->dev.parent, NULL);
	if (!w->map)
		return -EPROBE_DEFER;
	if (of_property_read_u32(pdev->dev.of_node, "reg", &w->base))
		return -EINVAL;

	w->wdd.info = &ump9620_wdt_info;
	w->wdd.ops = &ump9620_wdt_ops;
	w->wdd.parent = &pdev->dev;
	w->wdd.min_timeout = 3;
	w->wdd.max_timeout = WDT_MAX;
	w->wdd.timeout = WDT_DEFAULT;
	watchdog_set_drvdata(&w->wdd, w);
	watchdog_stop_on_reboot(&w->wdd);

	regmap_read(w->map, w->base + WDT_CTRL, &ctrl);
	if (ctrl & WDT_CNT_EN) {
		/* armed by LK: take it over with our timeout, the core pings until userspace opens the device */
		ret = ump9620_wdt_start(&w->wdd);
		if (ret)
			return ret;
	}

	platform_set_drvdata(pdev, w);
	ret = devm_watchdog_register_device(&pdev->dev, &w->wdd);
	if (ret)
		return ret;
	dev_info(&pdev->dev, "LK ctrl %#x, %s with %u s timeout\n", ctrl,
		 (ctrl & WDT_CNT_EN) ? "running" : "stopped", w->wdd.timeout);
	return 0;
}

static const struct of_device_id ump9620_wdt_match[] = {
	{ .compatible = "sprd,ump9620-wdt" },
	{ }
};

static struct platform_driver ump9620_wdt_driver = {
	.probe = ump9620_wdt_probe,
	.driver = { .name = "ump9620-wdt", .of_match_table = ump9620_wdt_match, .pm = pm_sleep_ptr(&ump9620_wdt_pm_ops) },
};
module_platform_driver(ump9620_wdt_driver);
MODULE_DESCRIPTION("UMP9620 PMIC watchdog");
MODULE_LICENSE("GPL");
