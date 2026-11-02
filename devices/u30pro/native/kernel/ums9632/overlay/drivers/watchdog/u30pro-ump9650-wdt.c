// SPDX-License-Identifier: GPL-2.0-only
/* U30 Pro UMP9650 watchdog, using the native Linux watchdog framework.
 * Register layout and 32768-Hz counter from the MU5358 vendor BSP.
 * System suspend is gated until cross-processor wake ownership is validated.
 */
#include <linux/bitops.h>
#include <linux/device.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/mutex.h>
#include <linux/watchdog.h>

#define UMP_WDT_LOAD_LOW 0x00
#define UMP_WDT_LOAD_HIGH 0x04
#define UMP_WDT_CTRL 0x08
#define UMP_WDT_RAW 0x10
#define UMP_WDT_LOCK 0x20
#define UMP_WDT_INT_EN BIT(0)
#define UMP_WDT_COUNT_EN BIT(1)
#define UMP_WDT_NEW_VER BIT(2)
#define UMP_WDT_RESET_EN BIT(3)
#define UMP_WDT_LOAD_BUSY BIT(4)
#define UMP_WDT_KEY 0xe551
#define UMP_WDT_HZ 32768U

struct u30pro_pmic_wdt {
 struct watchdog_device wdd;
 struct regmap *map;
 struct mutex lock;
 u32 base;
};

static int ump_wdt_relock(struct u30pro_pmic_wdt *w, int ret)
{
 int lock_ret = regmap_write(w->map, w->base + UMP_WDT_LOCK, (~UMP_WDT_KEY) & 0xffff);
 return ret ? ret : lock_ret;
}

/* Caller holds the device mutex. The PMIC clock is always on on UMP9650. */
static int ump_wdt_load(struct u30pro_pmic_wdt *w, unsigned int timeout)
{
 unsigned int status;
 u32 ticks = timeout * UMP_WDT_HZ;
 int ret;
 ret = regmap_read_poll_timeout(w->map, w->base + UMP_WDT_RAW,
                                status, !(status & UMP_WDT_LOAD_BUSY), 100, 5000);
 if (ret)
  return ret;
 ret = regmap_write(w->map, w->base + UMP_WDT_LOCK, UMP_WDT_KEY);
 if (ret)
  return ret;
 ret = regmap_write(w->map, w->base + UMP_WDT_LOAD_HIGH, ticks >> 16);
 if (!ret)
  ret = regmap_write(w->map, w->base + UMP_WDT_LOAD_LOW, ticks & 0xffff);
 return ump_wdt_relock(w, ret);
}

static int ump_wdt_ping(struct watchdog_device *wdd)
{
 struct u30pro_pmic_wdt *w = watchdog_get_drvdata(wdd);
 int ret;
 mutex_lock(&w->lock);
 ret = ump_wdt_load(w, wdd->timeout);
 mutex_unlock(&w->lock);
 return ret;
}

static int ump_wdt_start(struct watchdog_device *wdd)
{
 struct u30pro_pmic_wdt *w = watchdog_get_drvdata(wdd);
 int ret;
 mutex_lock(&w->lock);
 ret = ump_wdt_load(w, wdd->timeout);
 if (!ret) {
  ret = regmap_write(w->map, w->base + UMP_WDT_LOCK, UMP_WDT_KEY);
  if (!ret) {
   /* This driver implements reset protection, not a pretimeout interrupt. */
   ret = regmap_update_bits(w->map, w->base + UMP_WDT_CTRL,
             UMP_WDT_COUNT_EN | UMP_WDT_NEW_VER | UMP_WDT_RESET_EN | UMP_WDT_INT_EN,
             UMP_WDT_COUNT_EN | UMP_WDT_NEW_VER | UMP_WDT_RESET_EN);
   ret = ump_wdt_relock(w, ret);
  }
 }
 if (!ret)
  set_bit(WDOG_HW_RUNNING, &wdd->status);
 mutex_unlock(&w->lock);
 return ret;
}

static int ump_wdt_stop(struct watchdog_device *wdd)
{
 struct u30pro_pmic_wdt *w = watchdog_get_drvdata(wdd);
 int ret;
 mutex_lock(&w->lock);
 ret = regmap_write(w->map, w->base + UMP_WDT_LOCK, UMP_WDT_KEY);
 if (!ret) {
  ret = regmap_update_bits(w->map, w->base + UMP_WDT_CTRL,
                          UMP_WDT_COUNT_EN | UMP_WDT_RESET_EN | UMP_WDT_INT_EN, 0);
  ret = ump_wdt_relock(w, ret);
 }
 mutex_unlock(&w->lock);
 return ret;
}

static int ump_wdt_set_timeout(struct watchdog_device *wdd, unsigned int timeout)
{
 struct u30pro_pmic_wdt *w = watchdog_get_drvdata(wdd);
 int ret;
 mutex_lock(&w->lock);
 ret = ump_wdt_load(w, timeout);
 if (!ret)
  wdd->timeout = timeout;
 mutex_unlock(&w->lock);
 return ret;
}

static const struct watchdog_info ump_wdt_info = {
 .identity = "U30 Pro UMP9650 watchdog",
 .options = WDIOF_SETTIMEOUT | WDIOF_KEEPALIVEPING,
};
static const struct watchdog_ops ump_wdt_ops = {
 .owner = THIS_MODULE,
 .start = ump_wdt_start,
 .stop = ump_wdt_stop,
 .ping = ump_wdt_ping,
 .set_timeout = ump_wdt_set_timeout,
};

static int ump_wdt_probe(struct platform_device *pdev)
{
 struct u30pro_pmic_wdt *w;
 int ret;
 w = devm_kzalloc(&pdev->dev, sizeof(*w), GFP_KERNEL);
 if (!w)
  return -ENOMEM;
 w->map = dev_get_regmap(pdev->dev.parent, NULL);
 if (!w->map)
  return -EPROBE_DEFER;
 ret = of_property_read_u32(pdev->dev.of_node, "reg", &w->base);
 if (ret)
  return ret;
 mutex_init(&w->lock);
 w->wdd.info = &ump_wdt_info;
 w->wdd.ops = &ump_wdt_ops;
 w->wdd.parent = &pdev->dev;
 w->wdd.min_timeout = 3;
 w->wdd.max_timeout = 300;
 w->wdd.timeout = 300;
 w->wdd.max_hw_heartbeat_ms = 300000;
 watchdog_set_drvdata(&w->wdd, w);
 watchdog_set_nowayout(&w->wdd, true);
 ret = watchdog_init_timeout(&w->wdd, 0, &pdev->dev);
 if (ret)
  return ret;
 /* Do not stop or clear the armed watchdog if registration later fails. */
 ret = ump_wdt_start(&w->wdd);
 if (ret)
  return dev_err_probe(&pdev->dev, ret, "cannot take over UMP9650 watchdog\n");
 ret = devm_watchdog_register_device(&pdev->dev, &w->wdd);
 if (ret)
  return dev_err_probe(&pdev->dev, ret, "cannot register UMP9650 watchdog\n");
 platform_set_drvdata(pdev, w);
 return 0;
}
static const struct of_device_id ump_wdt_match[] = {
 { .compatible = "sprd,ump9650-wdt" },
 { }
};
MODULE_DEVICE_TABLE(of, ump_wdt_match);
static struct platform_driver ump_wdt_driver = {
 .probe = ump_wdt_probe,
 .driver = { .name = "u30pro-ump9650-wdt", .of_match_table = ump_wdt_match },
};
module_platform_driver(ump_wdt_driver);
MODULE_DESCRIPTION("U30 Pro native Linux UMP9650 watchdog");
MODULE_LICENSE("GPL");
