/* SPDX-License-Identifier: GPL-2.0-only */
/* U30 Air stock PMIC detector sequence, driven asynchronously by VBUS events.
 * Source: ZTE GPL b50db5b6224c, sprd-bc1p2.c + qogirn6pro USB PHY/DWC3.
 * dpdm_lock serializes the detector with data-role changes. The extcon atomic
 * notifier only invalidates the generation and queues work; it never sleeps.
 */
#define UMP9620_BC12_STATUS 0x239c
#define UMP9620_BC12_CTRL2 0x243c
#define UMP9620_BC12_DONE BIT(11)
#define UMP9620_BC12_PRESENT BIT(2)
#define UMP9620_BC12_TYPES (BIT(7) | BIT(6) | BIT(5))
#define UMP9620_BC12_INTERVAL_MS 20
#define UMP9620_BC12_FIRST_SAMPLES 60
#define UMP9620_BC12_RETRY_SAMPLES (60 + (960 - 20) / 20)

static enum usb_charger_type sprd_ssphy_bc12_type(u32 status)
{
	if (!(status & UMP9620_BC12_DONE))
		return UNKNOWN_TYPE;
	switch (status & UMP9620_BC12_TYPES) {
	case BIT(7): return SDP_TYPE;
	case BIT(6): return DCP_TYPE;
	case BIT(5): return CDP_TYPE;
	default: return UNKNOWN_TYPE;
	}
}

static bool sprd_ssphy_bc12_publish(struct sprd_ssphy *phy,
			 enum usb_charger_type type, bool present, u32 generation)
{
	unsigned long flags;

	/* Publish even when presence did not change: detection completes later
	 * than VBUS, and consumers must receive the completed type too.
	 * charger_detect below returns this cache only, never starts detection.
	 */
	spin_lock_irqsave(&phy->bc12_event_lock, flags);
	if (generation != (u32)atomic_read(&phy->bc12_generation)) {
		spin_unlock_irqrestore(&phy->bc12_event_lock, flags);
		return false;
	}
	WRITE_ONCE(phy->phy.chg_type, type);
	WRITE_ONCE(phy->phy.chg_state,
		   present ? USB_CHARGER_PRESENT : USB_CHARGER_ABSENT);
	spin_unlock_irqrestore(&phy->bc12_event_lock, flags);
	schedule_work(&phy->phy.chg_work);
	return true;
}

static enum usb_charger_type sprd_ssphy_charger_detect(struct usb_phy *x)
{
	return READ_ONCE(x->chg_type);
}

static void sprd_ssphy_bc12_role(struct sprd_ssphy *phy)
{
	int ret;

	if (phy->is_host) {
		/* A USB data host owns DP/DM even with external sink power. */
		ret = regmap_update_bits(phy->pmic, UMP9620_BC12_CTRL2, BIT(0), 0);
		if (!ret)
			ret = sc27xx_dpdm_switch_to_phy(phy->pmic, true);
		if (ret)
			dev_err(phy->dev, "BC1.2 host handoff failed: %d\n", ret);
	}
	atomic_inc(&phy->bc12_generation);
	if (READ_ONCE(phy->bc12_ready) && !READ_ONCE(phy->bc12_stopping))
		mod_delayed_work(system_power_efficient_wq, &phy->bc12_work, 0);
}

static void sprd_ssphy_bc12_work(struct work_struct *work)
{
	struct sprd_ssphy *phy = container_of(to_delayed_work(work),
					    struct sprd_ssphy, bc12_work);
	enum usb_charger_type type = UNKNOWN_TYPE;
	u32 generation, status;
	unsigned long delay = msecs_to_jiffies(UMP9620_BC12_INTERVAL_MS);
	bool data, present;
	int ret;

	mutex_lock(&phy->dpdm_lock);
	if (READ_ONCE(phy->bc12_stopping))
		goto out;
	generation = atomic_read(&phy->bc12_generation);
	present = READ_ONCE(phy->vbus_present);
	if (phy->is_host) {
		/* Never drive BC1.2 signals onto a live host/dock data path.
		 * A data-only role change does not revoke this cable's completed
		 * charging permission. VBUS notifier clears chg_type under the
		 * event lock on every cable edge; generation checking rejects a
		 * racing old result. Unknown/new cables retain the USB budget.
		 */
		type = present ? READ_ONCE(phy->phy.chg_type) : UNKNOWN_TYPE;
		sprd_ssphy_bc12_publish(phy, type, present, generation);
		goto out;
	}
	if (generation != phy->bc12_seen) {
		phy->bc12_seen = generation;
		phy->bc12_samples = 0;
		phy->bc12_retry = false;
		ret = sc27xx_dpdm_switch_to_phy(phy->pmic, false);
		if (ret)
			goto failed;
		sprd_ssphy_notify_disconnect(&phy->phy, USB_SPEED_UNKNOWN);
		sprd_ssphy_bc12_publish(phy, UNKNOWN_TYPE, present, generation);
	}
	if (!present) {
		ret = regmap_update_bits(phy->pmic, UMP9620_BC12_CTRL2, BIT(0), 0);
		if (ret)
			goto failed;
		goto out;
	}
	ret = regmap_read(phy->pmic, UMP9620_BC12_STATUS, &status);
	if (ret)
		goto failed;
	/* A queued result from the previous cable/role must never be published. */
	if (generation != atomic_read(&phy->bc12_generation))
		goto changed;
	if (!(status & UMP9620_BC12_PRESENT) &&
	    (phy->bc12_samples || (status & UMP9620_BC12_DONE))) {
		ret = -ENOTCONN;
		goto failed;
	}
	if (status & UMP9620_BC12_DONE)
		type = sprd_ssphy_bc12_type(status);
	if ((status & UMP9620_BC12_DONE) ||
	    ++phy->bc12_samples >= (phy->bc12_retry ?
		UMP9620_BC12_RETRY_SAMPLES : UMP9620_BC12_FIRST_SAMPLES)) {
		ret = regmap_update_bits(phy->pmic, UMP9620_BC12_CTRL2, BIT(0), 0);
		if (ret)
			goto failed;
		if (type == UNKNOWN_TYPE && !phy->bc12_retry) {
			/* The stock controller retries UNKNOWN once, with 960ms
			 * detection delay. Do not redetect a valid SDP as DCP.
			 */
			ret = regmap_update_bits(phy->pmic, CHGR_DET_FGU_CTRL,
					 GENMASK(7, 4), 15 << 4);
			if (!ret)
				ret = regmap_update_bits(phy->pmic, UMP9620_BC12_CTRL2,
						 BIT(0), BIT(0));
			if (ret)
				goto failed;
			phy->bc12_retry = true;
			phy->bc12_samples = 0;
			goto again;
		}
		data = type == SDP_TYPE || type == CDP_TYPE;
		ret = sc27xx_dpdm_switch_to_phy(phy->pmic, data);
		if (ret)
			goto failed;
		if (generation != atomic_read(&phy->bc12_generation))
			goto changed;
		if (data)
			sprd_ssphy_notify_connect(&phy->phy, USB_SPEED_UNKNOWN);
		if (!sprd_ssphy_bc12_publish(phy, type, true, generation))
			goto changed;
		dev_info(phy->dev, "BC1.2 complete: type %d, DP/DM %s\n",
			 type, data ? "USB" : "charger");
		goto out;
	}
again:
	if (generation != atomic_read(&phy->bc12_generation))
		goto changed;
	mod_delayed_work(system_power_efficient_wq, &phy->bc12_work, delay);
	goto out;
failed:
	regmap_update_bits(phy->pmic, UMP9620_BC12_CTRL2, BIT(0), 0);
	/* Unknown keeps the conservative current budget, not a second detector. */
	sprd_ssphy_bc12_publish(phy, UNKNOWN_TYPE, false, generation);
	dev_err(phy->dev, "BC1.2 detection/handoff failed: %d\n", ret);
	goto out;
changed:
	mod_delayed_work(system_power_efficient_wq, &phy->bc12_work, 0);
out:
	mutex_unlock(&phy->dpdm_lock);
}
