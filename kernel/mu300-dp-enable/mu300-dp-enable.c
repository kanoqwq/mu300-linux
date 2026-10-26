// SPDX-License-Identifier: GPL-2.0
/*
 * The F50/MU300 device tree ships with the PMIC USB-PD block and the DisplayPort controller disabled (the product has
 * no display). Enable both at runtime, without touching the DTB on flash: mark the nodes "okay" and create their
 * platform devices under the right parents. The internal-panel DPU0 has no panel and defers forever, which keeps the
 * DRM master from binding, so mark it disabled. Load before sc27xx_pd and sprd-drm.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/spi/spi.h>

static struct device *parent_of(struct device_node *np)
{
	struct device_node *pnp = of_get_parent(np);
	struct platform_device *pdev;
	struct device *dev = NULL;

	if (!pnp)
		return NULL;
	pdev = of_find_device_by_node(pnp);
	if (pdev)
		dev = &pdev->dev;
	else
		dev = bus_find_device_by_of_node(&spi_bus_type, pnp);	/* the PMIC is an SPI (ADI) device */
	of_node_put(pnp);
	return dev;
}

static int enable_node(const char *path)
{
	struct device_node *np = of_find_node_by_path(path);
	struct property *prop;
	struct platform_device *pdev;
	struct device *parent;

	if (!np) {
		pr_err("mu300-dp-enable: %s not found\n", path);
		return -ENODEV;
	}
	if (of_find_device_by_node(np)) {
		pr_info("mu300-dp-enable: %s already has a device\n", path);
		of_node_put(np);
		return 0;
	}
	/*
	 * of_update_property() is not exported here. The property value lives in the read-only flattened DT blob,
	 * but the struct property does not: point it at a new "okay" string.
	 */
	prop = of_find_property(np, "status", NULL);
	if (prop) {
		prop->value = kstrdup("okay", GFP_KERNEL);
		prop->length = sizeof("okay");
	}
	if (!of_device_is_available(np)) {
		pr_err("mu300-dp-enable: could not enable %s\n", path);
		of_node_put(np);
		return -EIO;
	}

	parent = parent_of(np);
	pdev = of_platform_device_create(np, NULL, parent);
	pr_info("mu300-dp-enable: %s -> %s (parent %s)\n", path, pdev ? dev_name(&pdev->dev) : "FAILED",
		parent ? dev_name(parent) : "none");
	of_node_put(np);
	return pdev ? 0 : -EIO;
}

static void disable_node(const char *path)
{
	struct device_node *np = of_find_node_by_path(path);
	struct property *prop = np ? of_find_property(np, "status", NULL) : NULL;

	if (prop) {
		prop->value = kstrdup("disabled", GFP_KERNEL);
		prop->length = sizeof("disabled");
		pr_info("mu300-dp-enable: %s disabled\n", path);
	}
	of_node_put(np);
}

static int __init mu300_dp_enable_init(void)
{
	int ret;

	disable_node("/soc/dpuvsp/dpu@31000000");
	disable_node("/soc/dpuvsp/dsi@31300000");
	ret = enable_node("/soc/aon/spi@400000/pmic@0/pd@e00");
	return ret ? ret : enable_node("/soc/ipa-apb/dptx@31890000");
}
module_init(mu300_dp_enable_init);
MODULE_LICENSE("GPL");
