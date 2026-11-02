// SPDX-License-Identifier: GPL-2.0
// Copyright (C) 2024 Unisoc, Inc.
/*
 * Unisoc ASE glue for the PMIC core supply ("sprd,ase-generic", ase id 0): prepares the core supply for the ASE test voltage and applies the
 * work/deep voltages plus the aged-silicon margin afterwards.  Ported from the vendor driver sprd-ase-generic.c (GPL-2.0).
 * Inactive unless u30pro_ase=1 is on the kernel command line, see u30pro-sprd-ase.c.
 */

#include <linux/sprd_ase_ops.h>
#include <linux/sprd-ase.h>
#include "sprd-ase-generic.h"

extern int u30pro_ase;

static struct ase_core_data *parchdev;

static int sprd_ase_core_test_prepare(u32 sys_id, u32 test_volt)
{
	int ret;

	ret = regmap_update_bits(parchdev->regmap, parchdev->reg[0], CTRL_MASK,
				 (test_volt / DCDC_STEP_VOL));
	if (ret)
		return ret;

	return 0;
}

static int sprd_ase_core_test_update(u32 sys_id, u32 volt_uv)
{
	int ret = 0;

	if (sys_id != parchdev->ase_id) {
		pr_err("sys_id parameter is error");
		return -EINVAL;
	}

	volt_uv = volt_uv <  parchdev->volt_threshold ? volt_uv : parchdev->volt_threshold;

	ret = regmap_update_bits(parchdev->regmap, parchdev->reg[0], CTRL_MASK,
				 (parchdev->work_volt + volt_uv) / DCDC_STEP_VOL);
	if (ret)
		return ret;

	ret = regmap_update_bits(parchdev->regmap, parchdev->reg[1], CTRL_MASK,
				 (parchdev->deep_volt + volt_uv) / DCDC_STEP_VOL);
	if (ret)
		return ret;

	return 0;
}

static struct ase_ops ase_core_ops = {
	.sys_prepare = sprd_ase_core_test_prepare,
	.sys_update = sprd_ase_core_test_update,
};

static int sprd_ase_core_init(struct device *dev)
{
	struct device_node *dev_np;
	struct platform_device *ase_pdev;
	struct sprd_ase_ops *ase_driver_ops;
	int ret;

	dev_np = of_parse_phandle(dev->of_node, "sprd,ase-device", 0);
	if (!dev_np) {
		pr_info("No need to enable ase\n");
		return 0;
	}

	ase_pdev = of_find_device_by_node(dev_np);

	of_node_put(dev_np);
	if (IS_ERR_OR_NULL(ase_pdev)) {
		pr_err("get ase sys platform device error\n");
		return -EINVAL;
	}

	ase_driver_ops = (struct sprd_ase_ops *)platform_get_drvdata(ase_pdev);
	if (IS_ERR_OR_NULL(ase_driver_ops)) {
		pr_err("ase device data has not been set\n");
		return -EPROBE_DEFER;
	}

	/* get ase id */
	ret = of_property_read_u32(dev->of_node, "sprd,ase-id", &parchdev->ase_id);
	if (ret) {
		pr_err("get sprd,ase-id val fail, ret(%d)\n", ret);
		return ret;
	}

	/* get work volt */
	ret = of_property_read_u32(dev->of_node, "sprd,ase-core-work", &parchdev->work_volt);
	if (ret) {
		pr_err("get sprd,ase-core-work val fail, ret(%d)\n", ret);
		return ret;
	}

	/* get deep volt */
	ret = of_property_read_u32(dev->of_node, "sprd,ase-core-deep", &parchdev->deep_volt);
	if (ret) {
		pr_err("get sprd,ase-core-deep val fail, ret(%d)\n", ret);
		return ret;
	}

	/* get volt threshold */
	ret = of_property_read_u32(dev->of_node, "sprd,volt-threshold", &parchdev->volt_threshold);
	if (ret) {
		pr_err("get sprd,volt-threshold val fail, ret(%d)\n", ret);
		return ret;
	}

	/* init ase sys ops */
	ret = ase_driver_ops->ops_init(parchdev->ase_id, &ase_core_ops, true);
	if (ret)
		pr_err("ase core ops init fail(%d)\n", ret);

	return 0;
}

static int sprd_ase_core_driver_probe(struct platform_device *pdev)
{
	struct device_node *np;
	struct property *prop;
	int ret, num;

	if (!u30pro_ase)
		return -ENODEV;

	parchdev = devm_kzalloc(&pdev->dev, sizeof(*parchdev), GFP_KERNEL);
	if (!parchdev)
		return -ENOMEM;

	np = pdev->dev.of_node;
	if (!np) {
		pr_err("ase has not found device node\n");
		return -ENODEV;
	}

	parchdev->dev = &pdev->dev;

	parchdev->regmap = dev_get_regmap(pdev->dev.parent, NULL);
	if (!parchdev->regmap) {
		dev_err(&pdev->dev, "sprd vddcore ase probe failed!\n");
		return -EINVAL;
	}

	prop = of_find_property(np, "reg", &num);
	if (!prop) {
		dev_err(&pdev->dev, "failed to get vddcore ctrl reg address\n");
		return -ENODEV;
	}

	if (num / sizeof(u32) == 2) {
		ret = of_property_read_u32_index(np, "reg", 0, &parchdev->reg[0]);
		if (ret)
			return ret;

		ret = of_property_read_u32_index(np, "reg", 1, &parchdev->reg[1]);
		if (ret)
			return ret;
	} else {
		pr_err("Failed to get ase reg info\n");
		return -EINVAL;
	}

	ret = sprd_ase_core_init(&pdev->dev);
	if (ret) {
		pr_err("init cpufreq ase error(%d)\n", ret);
		return ret;
	}

	pr_info("probe sprd ase core driver successful\n");
	return 0;
}

static void sprd_ase_core_driver_remove(struct platform_device *pdev)
{
}

static const struct of_device_id sprd_ase_core_match[] = {
	{ .compatible = "sprd,ase-generic" },
	{ /* Sentinel */  },
};

static struct platform_driver sprd_ase_core_platform_driver = {
	.driver = {
		.name = "sprd-ase-generic",
		.of_match_table = sprd_ase_core_match,
	},
	.probe = sprd_ase_core_driver_probe,
	.remove = sprd_ase_core_driver_remove,
};

static int __init sprd_ase_core_platform_driver_register(void)
{
	return platform_driver_register(&sprd_ase_core_platform_driver);
}

device_initcall(sprd_ase_core_platform_driver_register);

MODULE_DESCRIPTION("ASE GENERIC driver");
MODULE_LICENSE("GPL");
