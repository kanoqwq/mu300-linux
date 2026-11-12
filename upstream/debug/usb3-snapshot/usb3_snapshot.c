// SPDX-License-Identifier: GPL-2.0-only
/* Temporary read-only TCA evidence. No register writes, role or clock changes. */
#include <linux/module.h>
#include <linux/io.h>
#include <linux/of_address.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
static void __iomem *base;
static struct dentry *dir;
static int snapshot_show(struct seq_file *s, void *unused)
{
 unsigned int off;
 for (off = 0; off <= 0x34; off += 4)
  seq_printf(s, "%02x: %08x\n", off, readl(base + off));
 return 0;
}
DEFINE_SHOW_ATTRIBUTE(snapshot);
static int __init snapshot_init(void)
{
 struct device_node *np;
 struct resource r;
 int idx, ret;
 np = of_find_compatible_node(NULL, NULL, "sprd,qogirn6pro-ssphy");
 if (!np) return -ENODEV;
 idx = of_property_match_string(np, "reg-names", "phy_glb_regs");
 ret = idx < 0 ? idx : of_address_to_resource(np, idx, &r);
 of_node_put(np);
 if (ret) return ret;
 if (resource_size(&r) < 0x38) return -EINVAL;
 base = ioremap(r.start, 0x38);
 if (!base) return -ENOMEM;
 dir = debugfs_create_dir("ums9620-usb3-snapshot", NULL);
 if (IS_ERR(dir)) { ret = PTR_ERR(dir); iounmap(base); return ret; }
 if (IS_ERR(debugfs_create_file("tca", 0400, dir, NULL, &snapshot_fops))) {
  debugfs_remove_recursive(dir); iounmap(base); return -ENOMEM;
 }
 return 0;
}
static void __exit snapshot_exit(void)
{
 debugfs_remove_recursive(dir);
 iounmap(base);
}
module_init(snapshot_init);
module_exit(snapshot_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read-only UMS9620 TCA snapshot, manual diagnostic only");
