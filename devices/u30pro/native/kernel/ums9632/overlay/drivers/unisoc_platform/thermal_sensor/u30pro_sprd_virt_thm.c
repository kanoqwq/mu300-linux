// SPDX-License-Identifier: GPL-2.0
/*
 * Virtual thermal sensors of the UMS9632: a thermal zone whose temperature is made of other zones.
 *
 * The stock device tree has three of them:
 *  - "sprd,virtual-thermal" (virtual-sensor@0): the SoC temperature, the highest of the eight core zones and the GPU zone; the "soc-thmzone"
 *    zone built on it has the passive trips (70/85 C) with the cooling maps to the CPUs, a "hot" trip at 105 C and the critical trip at 115 C.
 *  - "sprd,virt-thm" (cluster0-sensor, cluster1-sensor): the temperature of a CPU cluster from its core zones; "cluster0-thmzone" and
 *    "cluster1-thmzone" are built on them and the apcpu cpufreq driver reads them to pick the DVFS table of a temperature level.
 *
 * Based on the vendor drivers sprd_virt_thm.c and sprd_soc_thermal.c (GPL-2.0, Unisoc).  The vendor SoC driver also contains its own
 * power-allocator and a polling thread; here the mainline thermal core and its power_allocator governor do that job (the zone's
 * "governor" property and sustainable-power are in the device tree), so this driver only has to report the temperature.
 *
 * "best-temp" (sprd,virt-thm): when any real zone is colder than this value the lowest temperature is reported, otherwise the highest.
 *
 * Locking: get_temp() runs with the lock of the virtual zone held.  The thermal core takes the global zone list lock first and a zone's
 * lock second when it binds a cooling device, so get_temp() must never look a zone up by name (that takes the list lock): that is an
 * ABBA deadlock, seen as a hang while the cpufreq cooling devices register.  The real zones are therefore found by a work item that
 * holds no zone lock, and get_temp() only uses the pointers it has found so far.
 */
#include <linux/err.h>
#include <linux/kernel.h>
#include <linux/limits.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/thermal.h>
#include <linux/workqueue.h>

#define VIRT_THM_LOOKUP_MS	500
#define VIRT_THM_LOOKUP_TRIES	240	/* two minutes: the real zones appear when their sensor driver has probed */

struct virt_thm {
	struct device *dev;
	struct thermal_zone_device *tz;
	int num;
	const char **names;
	struct thermal_zone_device **zones;
	int best_temp;
	bool use_best;
	struct delayed_work lookup;
	int tries;
};

static int virt_thm_get_temp(struct thermal_zone_device *tz, int *temp)
{
	struct virt_thm *v = thermal_zone_device_priv(tz);
	int i, t, max_temp = INT_MIN, min_temp = INT_MAX, got = 0;
	bool cold = false;

	for (i = 0; i < v->num; i++) {
		struct thermal_zone_device *real = READ_ONCE(v->zones[i]);

		if (!real || thermal_zone_get_temp(real, &t))
			continue;

		got++;
		max_temp = max(max_temp, t);
		min_temp = min(min_temp, t);
		if (v->use_best && t < v->best_temp)
			cold = true;
	}

	if (!got)
		return -ENODATA;

	*temp = cold ? min_temp : max_temp;
	return 0;
}

static const struct thermal_zone_device_ops virt_thm_ops = {
	.get_temp = virt_thm_get_temp,
};

/* find the real zones by name, outside of any zone lock; retried until all are there */
static void virt_thm_lookup(struct work_struct *work)
{
	struct virt_thm *v = container_of(to_delayed_work(work), struct virt_thm, lookup);
	int i, missing = 0;

	for (i = 0; i < v->num; i++) {
		struct thermal_zone_device *real;

		if (READ_ONCE(v->zones[i]))
			continue;

		real = thermal_zone_get_zone_by_name(v->names[i]);
		if (IS_ERR(real)) {
			missing++;
			continue;
		}
		WRITE_ONCE(v->zones[i], real);
	}

	if (v->tz)
		thermal_zone_device_update(v->tz, THERMAL_EVENT_UNSPECIFIED);

	if (missing && ++v->tries < VIRT_THM_LOOKUP_TRIES)
		schedule_delayed_work(&v->lookup, msecs_to_jiffies(VIRT_THM_LOOKUP_MS));
	else if (missing)
		dev_warn(v->dev, "%d of %d thermal zones were never found\n", missing, v->num);
}

static void virt_thm_stop(void *data)
{
	struct virt_thm *v = data;

	cancel_delayed_work_sync(&v->lookup);
}

static int virt_thm_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *np = dev->of_node;
	struct virt_thm *v;
	int i, num, ret;

	num = of_count_phandle_with_args(np, "thmzone-cells", NULL);
	if (num <= 0) {
		dev_err(dev, "no thmzone-cells\n");
		return -EINVAL;
	}

	v = devm_kzalloc(dev, sizeof(*v), GFP_KERNEL);
	if (!v)
		return -ENOMEM;

	v->dev = dev;
	v->num = num;
	v->names = devm_kcalloc(dev, num, sizeof(*v->names), GFP_KERNEL);
	v->zones = devm_kcalloc(dev, num, sizeof(*v->zones), GFP_KERNEL);
	if (!v->names || !v->zones)
		return -ENOMEM;

	for (i = 0; i < num; i++) {
		struct device_node *zone_np = of_parse_phandle(np, "thmzone-cells", i);

		if (!zone_np) {
			dev_err(dev, "thmzone-cells entry %d is not valid\n", i);
			return -EINVAL;
		}

		/* thermal zones are named after their device tree nodes */
		v->names[i] = devm_kstrdup(dev, zone_np->name, GFP_KERNEL);
		of_node_put(zone_np);
		if (!v->names[i])
			return -ENOMEM;
	}

	v->use_best = of_device_is_compatible(np, "sprd,virt-thm") && !of_property_read_u32(np, "best-temp", &v->best_temp);

	INIT_DELAYED_WORK(&v->lookup, virt_thm_lookup);
	ret = devm_add_action_or_reset(dev, virt_thm_stop, v);
	if (ret)
		return ret;

	v->tz = devm_thermal_of_zone_register(dev, 0, v, &virt_thm_ops);
	if (IS_ERR(v->tz)) {
		ret = PTR_ERR(v->tz);
		v->tz = NULL;
		dev_err(dev, "cannot register the thermal zone: %d\n", ret);
		return ret;
	}

	schedule_delayed_work(&v->lookup, msecs_to_jiffies(VIRT_THM_LOOKUP_MS));

	dev_info(dev, "virtual thermal sensor of %d zones registered\n", num);
	return 0;
}

static const struct of_device_id virt_thm_of_match[] = {
	{ .compatible = "sprd,virtual-thermal" },
	{ .compatible = "sprd,virt-thm" },
	{ /* sentinel */ },
};

static struct platform_driver virt_thm_driver = {
	.probe = virt_thm_probe,
	.driver = {
		.name = "u30pro-sprd-virt-thm",
		.of_match_table = virt_thm_of_match,
	},
};
module_platform_driver(virt_thm_driver);

MODULE_DESCRIPTION("Unisoc virtual thermal sensors (SoC and CPU clusters)");
MODULE_LICENSE("GPL");
