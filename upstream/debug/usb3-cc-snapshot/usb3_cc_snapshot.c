// SPDX-License-Identifier: GPL-2.0-only
/* Manual snapshot of known UMP9620 configuration/status registers only.
 * No FIFO, interrupt-clear, calibration, role or power writes.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>

static struct regmap *map;
static struct device *map_dev;
static struct dentry *directory;
static u32 cc_base, pd_base;

static int read_register(struct seq_file *s, const char *name, u32 reg)
{
	u32 value;
	int ret = regmap_read(map, reg, &value);

	if (ret)
		return ret;
	seq_printf(s, "%s[%#x]=%#x\n", name, reg, value);
	return 0;
}

static int snapshot_show(struct seq_file *s, void *unused)
{
	static const struct { const char *name; u32 offset; } cc[] = {
		{ "TYPEC_EN", 0x00 }, { "TYPEC_MODE", 0x04 },
		{ "TYPEC_PD_CFG", 0x08 }, { "TYPEC_STATUS", 0x1c },
		{ "TYPEC_DBG1", 0x60 },
	}, pd[] = {
		{ "PD_HEAD_CFG", 0x08 }, { "PD_CFG0", 0x10 },
		{ "PD_CFG1", 0x14 }, { "PD_STS0", 0x1c },
		{ "PD_STS1", 0x20 },
	};
	int i, ret;

	for (i = 0; i < ARRAY_SIZE(cc); i++) {
		ret = read_register(s, cc[i].name, cc_base + cc[i].offset);
		if (ret)
			return ret;
	}
	for (i = 0; i < ARRAY_SIZE(pd); i++) {
		ret = read_register(s, pd[i].name, pd_base + pd[i].offset);
		if (ret)
			return ret;
	}
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(snapshot);

static int node_base(const char *compatible, u32 *base)
{
	struct device_node *node;
	int ret;

	node = of_find_compatible_node(NULL, NULL, compatible);
	if (!node)
		return -ENODEV;
	ret = of_property_read_u32(node, "reg", base);
	of_node_put(node);
	if (ret)
		return ret;
	return *base && !(*base & 3) && *base < 0x10000 ? 0 : -EINVAL;
}

static int __init snapshot_init(void)
{
	struct device_node *node;
	struct platform_device *pdev;
	int ret;

	ret = node_base("sprd,sc27xx-typec", &cc_base);
	if (ret)
		return ret;
	ret = node_base("sprd,ump9620-pd", &pd_base);
	if (ret)
		return ret;
	node = of_find_compatible_node(NULL, NULL, "sprd,ump962x-syscon");
	if (!node)
		return -ENODEV;
	pdev = of_find_device_by_node(node);
	of_node_put(node);
	if (!pdev)
		return -ENODEV;
	map_dev = get_device(pdev->dev.parent);
	put_device(&pdev->dev);
	if (!map_dev)
		return -ENODEV;
	map = dev_get_regmap(map_dev, NULL);
	if (!map) {
		ret = -ENODEV;
		goto release;
	}
	directory = debugfs_create_dir("ums9620-usb3-cc-snapshot", NULL);
	if (IS_ERR(directory)) {
		ret = PTR_ERR(directory);
		goto release;
	}
	if (IS_ERR(debugfs_create_file("status", 0400, directory, NULL,
				      &snapshot_fops))) {
		debugfs_remove_recursive(directory);
		ret = -ENOMEM;
		goto release;
	}
	return 0;
release:
	put_device(map_dev);
	return ret;
}

static void __exit snapshot_exit(void)
{
	debugfs_remove_recursive(directory);
	put_device(map_dev);
}
module_init(snapshot_init);
module_exit(snapshot_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Manual read-only UMP9620 CC/PD status snapshot");
