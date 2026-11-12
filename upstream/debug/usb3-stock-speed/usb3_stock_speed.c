// SPDX-License-Identifier: GPL-2.0-only
/* Manual stock-DCFG comparison, not a production driver or recovery loop.
 * Default: read only. apply=1 changes only inactive gadget speed while HOST.
 * Hold a runtime-PM reference and serialize with DWC3's role worker. Restore
 * our speed field on unload if the same host still owns our written value.
 */
#include <linux/module.h>
#include <linux/io.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include "core.h"

/* Same mapped-global offset as DWC3 io.h, without its unexported tracepoints. */
static u32 stock_readl(struct dwc3 *dwc, u32 reg)
{
	return readl(dwc->regs + reg - DWC3_GLOBALS_REGS_START);
}

static void stock_writel(struct dwc3 *dwc, u32 reg, u32 value)
{
	writel(value, dwc->regs + reg - DWC3_GLOBALS_REGS_START);
}

static bool apply;
module_param(apply, bool, 0400);
MODULE_PARM_DESC(apply, "Explicit temporary stock DCFG speed=SuperSpeed test (host only)");
static struct platform_device *pdev;
static struct dwc3 *controller;
static u32 original_speed;
static bool modified, pm_held;

static int __init stock_speed_init(void)
{
	struct device_node *np;
	struct dwc3 *dwc;
	u32 gctl, dcfg;
	int ret;

	np = of_find_compatible_node(NULL, NULL, "snps,sprd-dwc3");
	if (!np)
		return -ENODEV;
	pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!pdev)
		return -ENODEV;
	dwc = platform_get_drvdata(pdev);
	if (!dwc) {
		ret = -ENODEV;
		goto release_device;
	}
	ret = pm_runtime_get_if_active(&pdev->dev);
	if (ret <= 0) {
		ret = ret < 0 ? ret : -EBUSY;
		goto release_device;
	}
	pm_held = true;
	controller = dwc;
	mutex_lock(&dwc->mutex);
	gctl = stock_readl(dwc, DWC3_GCTL);
	dcfg = stock_readl(dwc, DWC3_DCFG);
	if (dwc->current_dr_role != DWC3_GCTL_PRTCAP_HOST ||
	    DWC3_GCTL_PRTCAP(gctl) != DWC3_GCTL_PRTCAP_HOST) {
		ret = -EBUSY;
		goto unlock;
	}
	original_speed = dcfg & DWC3_DCFG_SPEED_MASK;
	pr_info("usb3-stock-speed: host DCFG=%#x apply=%u\n", dcfg, apply);
	if (apply) {
		if (original_speed != DWC3_DCFG_SUPERSPEED_PLUS) {
			ret = -EINVAL;
			goto unlock;
		}
		dcfg = (dcfg & ~DWC3_DCFG_SPEED_MASK) | DWC3_DCFG_SUPERSPEED;
		stock_writel(dwc, DWC3_DCFG, dcfg);
		modified = true;
		pr_info("usb3-stock-speed: temporary DCFG=%#x\n",
			stock_readl(dwc, DWC3_DCFG));
	}
	mutex_unlock(&dwc->mutex);
	return 0;
unlock:
	mutex_unlock(&dwc->mutex);
	pm_runtime_put(&pdev->dev);
	pm_held = false;
release_device:
	put_device(&pdev->dev);
	pdev = NULL;
	return ret;
}

static void __exit stock_speed_exit(void)
{
	u32 dcfg, gctl;
	struct dwc3 *dwc = controller;

	if (modified) {
		mutex_lock(&dwc->mutex);
		gctl = stock_readl(dwc, DWC3_GCTL);
		dcfg = stock_readl(dwc, DWC3_DCFG);
		if (dwc->current_dr_role == DWC3_GCTL_PRTCAP_HOST &&
		    DWC3_GCTL_PRTCAP(gctl) == DWC3_GCTL_PRTCAP_HOST &&
		    (dcfg & DWC3_DCFG_SPEED_MASK) == DWC3_DCFG_SUPERSPEED) {
			stock_writel(dwc, DWC3_DCFG,
				(dcfg & ~DWC3_DCFG_SPEED_MASK) | original_speed);
			pr_info("usb3-stock-speed: restored DCFG=%#x\n",
				stock_readl(dwc, DWC3_DCFG));
		} else {
			pr_warn("usb3-stock-speed: role/value changed; not overwriting the new owner's state\n");
		}
		mutex_unlock(&dwc->mutex);
	}
	if (pm_held)
		pm_runtime_put(&pdev->dev);
	put_device(&pdev->dev);
}

module_init(stock_speed_init);
module_exit(stock_speed_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Manual, reversible F50 stock DCFG speed comparison");
