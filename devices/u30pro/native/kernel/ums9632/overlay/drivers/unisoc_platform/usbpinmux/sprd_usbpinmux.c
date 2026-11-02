// SPDX-License-Identifier: GPL-2.0-only
/*copyright (C) 2022 Spreadtrum Communications Inc.
 *
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/errno.h>
#include <linux/export.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>
#include <linux/soc/sprd/sprd_usbpinmux.h>

#define	BIT_USB_MUX_CONFIG	BIT(31)

unsigned char	*usb_uart_jtag_mux_reg;
static u32		usb2jtag_mux_reg;
static struct regmap	*usbmux_cfg;
static int usbmux_get_regs(struct platform_device *pdev)
{
	unsigned int args[2];
	struct resource *res = NULL;
	struct device *dev = &pdev->dev;
	struct property *usb_syscon;

	/*judge L6 usb-mux-syscon node*/
	usb_syscon = of_find_property(dev->of_node, "usb-mux-syscon", NULL);
	if (!usb_syscon) {
		/*try to parse out the reg attribute of items other than L6*/
		res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
		if (!res) {
			dev_err(dev, "missing IOMEM\n");
			return -EINVAL;
		}
		usb_uart_jtag_mux_reg = devm_ioremap(&pdev->dev, res->start,
						resource_size(res));
		if (!usb_uart_jtag_mux_reg) {
			dev_err(&pdev->dev, "failed to remap mux_reg\n");
			return -ENXIO;
		}
	} else {
		usbmux_cfg = syscon_regmap_lookup_by_phandle_args(dev->of_node,
			"usb-mux-syscon", 2, args);
		if (IS_ERR(usbmux_cfg)) {
			dev_err(dev, "property usb-mux-syscon parse failed\n");
			return -ENXIO;
		}
		usb2jtag_mux_reg = args[0];
		pr_info("usb_uart_jtag_mux:reg:%x,mask:%x\n", args[0], args[1]);
	}
	return 0;
}
/*
 *Desc：check sub pin mux
 *Parameter: NULL
 *Return Value: 1: success  other: failed
 */
int sprd_usbmux_check_mode(void)
{
	u32 mux_val;
	int ret;

	/*check usb_pin_mux pointer && reg addr */
	if (usb_uart_jtag_mux_reg) {
		mux_val = readl_relaxed(usb_uart_jtag_mux_reg);
		/* bit[31] will be config when usb mux jtag or uart */
		if (mux_val & BIT_USB_MUX_CONFIG) {
			pr_debug("USBMux open: mux_val = 0x%x\n", mux_val);
			return 1;
		}
	} else if (usbmux_cfg && !IS_ERR(usbmux_cfg)) {
		ret = regmap_read(usbmux_cfg, usb2jtag_mux_reg, &mux_val);
		if (ret) {
			pr_err("Failed to read mux_val\n");
			return -EINVAL;
		} else if (mux_val) {
			pr_info("USBMux open: mux_val = 0x%x\n", mux_val);
			return 1;
		}
	}
	return 0;
}
EXPORT_SYMBOL(sprd_usbmux_check_mode);
static int sprd_usbpinmux_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct regulator *vdd_on;
	u32 vdd_vol;
	int ret;
	ret = usbmux_get_regs(pdev);
	if (!ret)
		dev_info(dev, "usbmux_get_regs ok!\n");
	else
		dev_warn(dev, "usbmux_get_regs filed!\n");
	if (sprd_usbmux_check_mode() > 0) {
		ret = of_property_read_u32(dev->of_node, "sprd,vdd-voltage",
				&vdd_vol);
		if (ret < 0) {
			dev_err(dev, "unable to read usbmux vdd voltage\n");
			return ret;
		}
		vdd_on = devm_regulator_get(dev, "vdd");
		if (IS_ERR(vdd_on)) {
			dev_err(dev, "unable to get usbmux vdd supply\n");
			return PTR_ERR(vdd_on);
		}
		ret = regulator_set_voltage(vdd_on, vdd_vol, vdd_vol);
		if (ret < 0) {
			dev_err(dev, "fail to set usbmux vdd voltage at %dmV\n",
				vdd_vol);
			return ret;
		}
		ret = regulator_enable(vdd_on);
		if (ret) {
			dev_err(dev, "fail to enable regulator!\n");
			return ret;
		}
	}
	return 0;
}
static const struct of_device_id sprd_usbpinmux_of_match[] = {
	{ .compatible = "sprd,qogirl6-usbpinmux" },
	{ .compatible = "sprd,qogirn6l-usbpinmux" },
	{ .compatible = "sprd,qogirn5-usbpinmux" },
	{ .compatible = "sprd,qogirl8-usbpinmux" },
	{ /* sentinel */ },
};
MODULE_DEVICE_TABLE(of, sprd_usbpinmux_of_match);
static struct platform_driver sprd_usbpinmux_driver = {
	.probe = sprd_usbpinmux_probe,
	.driver = {
		.name = "sprd-usbpinmux",
		.of_match_table = sprd_usbpinmux_of_match,
	},
};
module_platform_driver(sprd_usbpinmux_driver);
MODULE_LICENSE("GPL");
MODULE_AUTHOR("Porter Xu<porter.xu@unisoc.com>");
MODULE_DESCRIPTION("unisoc platform usbpinmux driver");
