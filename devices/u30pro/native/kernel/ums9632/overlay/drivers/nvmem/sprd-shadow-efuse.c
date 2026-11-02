// SPDX-License-Identifier: GPL-2.0
// Copyright (C) 2018 Spreadtrum Communications Inc.

#include <linux/genalloc.h>
#include <linux/kernel.h>
#include <linux/io.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/nvmem-provider.h>
#include <linux/of_address.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/vmalloc.h>

#define HEAD_LEN		4

struct sprd_efuse {
	struct device *dev;
	void __iomem *base;
};

static int sprd_efuse_read(void *context, u32 offset, void *val, size_t bytes)
{
	struct sprd_efuse *efuse = context;
	u8 *data = (u8 *)val;
	int i;

	for (i = 0; i < bytes; i++)
		*data++ = readb(efuse->base + HEAD_LEN + offset + i);

	return 0;
}

/*
 * Legacy firmware describes aliases such as calib@62 and calib@63 with the
 * same reg offset. NVMEM strips the unit address from the cell name, then
 * names its sysfs files with name/offset/bit_offset; aliases would collide
 * and fail registration of the entire provider. Retain the full node name
 * only for those collisions. The OF node, offset, size and bits stay intact.
 */
static void sprd_shadow_efuse_fixup_cell_info(struct nvmem_device *nvmem,
					   struct nvmem_cell_info *cell)
{
	struct device_node *parent, *sibling;
	bool collision = false;
	u32 offset, bit_offset;
	const char *name;
	int len;

	(void)nvmem;
	if (!cell->np || !cell->name)
		return;
	parent = of_get_parent(cell->np);
	if (!parent)
		return;

	for_each_child_of_node(parent, sibling) {
		if (sibling == cell->np || !of_node_name_eq(sibling, cell->name))
			continue;
		if (!of_get_property(sibling, "reg", &len) || len < 2 * sizeof(u32) ||
		    of_property_read_u32_index(sibling, "reg", 0, &offset))
			continue;
		bit_offset = 0;
		if (of_property_count_u32_elems(sibling, "bits") == 2)
			of_property_read_u32_index(sibling, "bits", 0, &bit_offset);
		if (offset == cell->offset && bit_offset == cell->bit_offset) {
			collision = true;
			of_node_put(sibling);
			break;
		}
	}
	of_node_put(parent);
	if (!collision)
		return;

	name = kstrdup(kbasename(cell->np->full_name), GFP_KERNEL);
	kfree(cell->name);
	cell->name = name;
}

static int sprd_efuse_probe(struct platform_device *pdev)
{
	struct device_node *np = pdev->dev.of_node;
	struct nvmem_device *nvmem;
	struct nvmem_config econfig = { };
	struct sprd_efuse *efuse;
	struct resource *res;
	u32 blk_width, blk_num;
	int ret;

	efuse = devm_kzalloc(&pdev->dev, sizeof(*efuse), GFP_KERNEL);
	if (!efuse)
		return -ENOMEM;

	efuse->dev = &pdev->dev;
	efuse->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);
	if (IS_ERR(efuse->base))
		return PTR_ERR(efuse->base);

	if (resource_size(res) < HEAD_LEN)
		return -EINVAL;

	blk_num = readl(efuse->base);
	if (blk_num == 0) {
		dev_err(efuse->dev, "failed to get shadow_efuse blk_num\n");
		return -EINVAL;
	}

	ret = of_property_read_u32(np, "sprd,blk_width", &blk_width);
	if (ret) {
		dev_err(efuse->dev, "failed to get sprd,blk_width\n");
		return ret;
	}

	efuse->dev = &pdev->dev;

	ret = of_property_read_string(np, "sprd,shadow-efuse-name", &econfig.name);
	if (ret) {
		dev_err(&pdev->dev, "failed to get sprd,shadow-efuse-name\n");
		return ret;
	}

	econfig.stride = 1;
	econfig.word_size = 1;
	econfig.read_only = true;
	econfig.id = NVMEM_DEVID_NONE;
	if (!blk_width || blk_num > (resource_size(res) - HEAD_LEN) / blk_width)
		return dev_err_probe(&pdev->dev, -EINVAL, "shadow efuse outside resource\n");
	econfig.size = blk_num * blk_width;
	econfig.reg_read = sprd_efuse_read;
	econfig.priv = efuse;
	econfig.dev = &pdev->dev;
	econfig.add_legacy_fixed_of_cells = true;
	econfig.fixup_dt_cell_info = sprd_shadow_efuse_fixup_cell_info;
	nvmem = devm_nvmem_register(&pdev->dev, &econfig);
	if (IS_ERR(nvmem)) {
		dev_err(&pdev->dev, "failed to register nvmem\n");
		return PTR_ERR(nvmem);
	}

	platform_set_drvdata(pdev, efuse);
	return 0;
}

static const struct of_device_id sprd_efuse_of_match[] = {
	{ .compatible = "sprd,shadow-efuse", },
	{ }
};

MODULE_DEVICE_TABLE(of, sprd_efuse_of_match);

static struct platform_driver sprd_efuse_driver = {
	.probe = sprd_efuse_probe,
	.driver = {
		.name = "sprd-shadow-efuse",
		.of_match_table = sprd_efuse_of_match,
	},
};

module_platform_driver(sprd_efuse_driver);

MODULE_AUTHOR("Yanxin Huang <Yanxin.Huang@unisoc.com>");
MODULE_DESCRIPTION("Unisoc AP shadow efuse driver");
MODULE_LICENSE("GPL");
