// SPDX-License-Identifier: GPL-2.0-only
/*
 * mu300_thermal: the stock virtual thermal sensor of the UMS9620 boards, for mainline kernels.
 *
 * The vendor device tree (used unchanged under mainline) has 19 physical on-die zones without a single trip point,
 * and one zone with trips, "soc-thmzone": 70 and 85 C passive, 110 C critical, with cooling maps from the 85 C trip
 * to the three CPU clusters (cpu@0, cpu@400, cpu@700) and to vendor cluster, GPU and NPU cooling devices that mainline does
 * not have. Its sensor is "/virtual-sensor@0" (compatible "virtual-thermal"), whose "thmzone-cells" name the eleven
 * physical zones it aggregates (lit0-3, mid4-6, big7, gpu, ai0, ai1). The vendor kernel's driver for it is not in
 * mainline, and there is no in-tree aggregating sensor, so the zone did not exist and nothing in the kernel
 * throttled: userspace (thermal-guard) capped cpufreq itself.
 *
 * This driver binds to that node and reports the hottest of the referenced zones. The thermal core then registers
 * soc-thmzone from the device tree with its trips and polling delays, binds the cpufreq cooling devices through the
 * cooling maps (the maps to the vendor cooling nodes are ignored, nothing registers them), and the step_wise
 * governor throttles the clusters at 85 C; the critical trip powers the device off at 110 C through the thermal
 * core. thermal-guard notices the bound zone and hands frequency control over (docs/FINDINGS.md 48). The trend
 * step_wise steps by is this driver's (get_trend, mu300_trend.h): with the core's own, reading against reading, the
 * sensors' noise kept the cooling at a step or two and the SoC passed 98 C under a full CPU load (FINDINGS 166).
 *
 * After kanoqwq's mu300_thermal (github.com/kanoqwq/mu300-linux, branch clean-tf-7.2, commit 5c8c2f9), GPL-2.0.
 *
 * Nothing here is invented: no temperatures, no trips, no device tree rewrite, no polling thread of its own. A
 * physical zone that fails to read is left out of that round and the hottest of the others is reported; only when
 * none reads does get_temp fail, and then the core logs it once, skips the update (no trip is evaluated, the cooling
 * devices keep their state) and the zone's sysfs temp read fails too, which thermal-guard takes as "cap it myself".
 * An unreadable sensor is never reported as cold. The physical zones' drivers are built in and never unregister, so
 * plain pointers to them are kept. Only the UMS9620 is accepted.
 */
#include <linux/err.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/thermal.h>

#include "mu300_trend.h"

#define MU300_WINDOW		HZ		/* the slope is taken over about this long */
#define MU300_SPAN		(HZ / 2)	/* the readings of this long are averaged at either end of the window */
#define MU300_DROP_EVERY	HZ		/* in the throttling band, one step back at most this often */

struct mu300_virtual {
	struct mu300_history hist;	/* written by get_temp, read by get_trend: both under the zone's lock */
	unsigned int count;
	struct thermal_zone_device *zone[];
};

static int mu300_virtual_get_temp(struct thermal_zone_device *tz, int *temp)
{
	struct mu300_virtual *v = thermal_zone_device_priv(tz);
	int hottest = INT_MIN, value, ret, err = 0;
	unsigned int i, read = 0;

	for (i = 0; i < v->count; i++) {
		ret = thermal_zone_get_temp(v->zone[i], &value);
		if (ret) {
			err = ret;	/* one flaky sensor must not silence the zone: the others still throttle */
			continue;
		}
		read++;
		if (value > hottest)
			hottest = value;
	}
	if (!read)
		return err;
	*temp = hottest;
	mu300_record(&v->hist, jiffies, hottest);
	return 0;
}

/*
 * The trend step_wise throttles by, from the slope over about a second (mu300_trend.h, docs/FINDINGS.md 166): the
 * core's own (each reading against the one 100 ms before) follows the sensors' noise, and step_wise steps the
 * cooling down on every falling reading, so it never held the SoC under a full CPU load.
 */
static int mu300_virtual_get_trend(struct thermal_zone_device *tz, const struct thermal_trip *trip,
				   enum thermal_trend *trend)
{
	struct mu300_virtual *v = thermal_zone_device_priv(tz);
	long slope;
	int now;

	if (mu300_slope(&v->hist, HZ, MU300_WINDOW, MU300_SPAN, &now, &slope))
		return -ENODATA;	/* the core's own trend: this reading against the last */
	switch (mu300_trend(&v->hist, now, slope, trip->temperature, trip->hysteresis, jiffies, MU300_DROP_EVERY)) {
	case MU300_RAISING:
		*trend = THERMAL_TREND_RAISING;
		break;
	case MU300_DROPPING:
		*trend = THERMAL_TREND_DROPPING;
		break;
	default:
		*trend = THERMAL_TREND_STABLE;
	}
	return 0;
}

static const struct thermal_zone_device_ops mu300_virtual_ops = {
	.get_temp = mu300_virtual_get_temp,
	.get_trend = mu300_virtual_get_trend,
};

static int mu300_virtual_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct thermal_zone_device *tz;
	struct mu300_virtual *v;
	int count, i;

	if (!of_machine_is_compatible("sprd,ums9620"))
		return -ENODEV;
	count = of_count_phandle_with_args(dev->of_node, "thmzone-cells", NULL);
	if (count <= 0 || count > 32)
		return dev_err_probe(dev, -EINVAL, "thmzone-cells: %d zones\n", count);
	v = devm_kzalloc(dev, struct_size(v, zone, count), GFP_KERNEL);
	if (!v)
		return -ENOMEM;
	v->count = count;
	for (i = 0; i < count; i++) {
		struct device_node *node = of_parse_phandle(dev->of_node, "thmzone-cells", i);

		if (!node)
			return dev_err_probe(dev, -EINVAL, "thmzone-cells[%d]: not a node\n", i);
		/* thermal_of names a zone after its node, so the node name is the zone's type */
		v->zone[i] = thermal_zone_get_zone_by_name(node->name);
		if (IS_ERR(v->zone[i])) {
			int err = PTR_ERR(v->zone[i]);

			/* the sensor driver has not registered it yet: try again later; anything else is a wrong tree */
			dev_err_probe(dev, err == -ENODEV ? -EPROBE_DEFER : err, "zone %pOFn: not registered\n", node);
			of_node_put(node);
			return err == -ENODEV ? -EPROBE_DEFER : err;
		}
		of_node_put(node);
	}
	tz = devm_thermal_of_zone_register(dev, 0, v, &mu300_virtual_ops);
	if (IS_ERR(tz))
		return dev_err_probe(dev, PTR_ERR(tz), "no thermal zone for this sensor in the device tree\n");
	dev_info(dev, "%s: the hottest of %d zones\n", thermal_zone_device_type(tz), count);
	return 0;
}

static const struct of_device_id mu300_virtual_match[] = {
	{ .compatible = "virtual-thermal" },
	{ }
};
MODULE_DEVICE_TABLE(of, mu300_virtual_match);

static struct platform_driver mu300_virtual_driver = {
	.probe = mu300_virtual_probe,
	.driver = {
		.name = "mu300-virtual-thermal",
		.of_match_table = mu300_virtual_match,
	},
};
module_platform_driver(mu300_virtual_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("UMS9620 stock virtual thermal sensor (soc-thmzone) for mainline kernels");
