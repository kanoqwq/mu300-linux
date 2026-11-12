/* SPDX-License-Identifier: GPL-2.0-only */
/* Call only after DWC3 has stopped the old link, before starting the new one. */
#define UMS9620_TCA_OP_MODE_MASK 3U
#define UMS9620_TCA_OP_MODE_SYSTEM 0U
#define UMS9620_TCA_OP_MODE_CONTROLLER 1U
#define UMS9620_TCA_DEVICE_ROLE BIT(4)
static int sprd_ssphy_sync_orientation(struct sprd_ssphy *phy, bool role_changed)
{
	u32 boot, mode, status, flip, gcfg, role, active, op_mode;
	int ret;

	ret = regmap_read(phy->aon_apb, REG_AON_APB_BOOT_MODE, &boot);
	if (ret)
		return ret;
	flip = boot & BIT(10) ? 0 : BIT(2);
	role = phy->is_host ? 0 : UMS9620_TCA_DEVICE_ROLE;
	gcfg = readl(phy->base + 0x10);
	mode = readl(phy->base + 0x18);
	status = readl(phy->base + 0x34);
	op_mode = gcfg & UMS9620_TCA_OP_MODE_MASK;
	active = op_mode == UMS9620_TCA_OP_MODE_CONTROLLER ?
		readl(phy->base + 0x14) : mode;
	if (!role_changed && !phy->orientation_error &&
	    op_mode <= UMS9620_TCA_OP_MODE_CONTROLLER &&
	    (gcfg & UMS9620_TCA_DEVICE_ROLE) == role &&
	    (active & (BIT(3) | BIT(2))) == flip &&
	    (status & (BIT(3) | BIT(2))) == flip)
		return 0;

	/* Use system mode to update the mux with both controllers stopped.
	 * Do not treat a matching final direction as proof that a disable pulse
	 * reached the PHY: observe its disabled output before changing/rearming.
	 */
	writel(gcfg & ~UMS9620_TCA_OP_MODE_MASK, phy->base + 0x10);
	writel(mode | BIT(3), phy->base + 0x18);
	ret = readl_poll_timeout(phy->base + 0x34, status,
				status & BIT(3), 10, 100000);
	if (ret)
		goto out;

	/* Match the stock U30 Air init: GCFG bit4 is clear for host, set for
	 * device. A role swap does not rerun legacy usb_phy_init() in mainline.
	 * This is a PHY data-role setting, not permission to source VBUS.
	 */
	writel((gcfg & ~(UMS9620_TCA_OP_MODE_MASK | UMS9620_TCA_DEVICE_ROLE)) | role,
	       phy->base + 0x10);
	mode = (mode & ~BIT(2)) | flip;
	writel(mode | BIT(3), phy->base + 0x18);
	ret = readl_poll_timeout(phy->base + 0x34, status,
				(status & (BIT(3) | BIT(2))) == (BIT(3) | flip),
				10, 100000);
	if (ret)
		goto out;
	writel(mode & ~BIT(3), phy->base + 0x18);
	ret = readl_poll_timeout(phy->base + 0x34, status,
				(status & (BIT(3) | BIT(2))) == flip, 10, 100000);
out:
	phy->orientation_error = !!ret;
	dev_info(phy->dev, "TCA direction: boot=%#x flip=%u output=%#x result=%d role_change=%u host=%u gcfg=%#x->%#x pstate=%#x\n",
		 boot, !!flip, status, ret, role_changed, phy->is_host,
		 gcfg, readl(phy->base + 0x10), readl(phy->base + 0x30));
	return ret;
}
