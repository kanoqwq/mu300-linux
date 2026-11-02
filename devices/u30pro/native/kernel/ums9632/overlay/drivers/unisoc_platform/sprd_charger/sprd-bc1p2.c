// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (C) 2022 Unisoc Co., Ltd.
 * Chen Yongzhi <yongzhi.chen@unisoc.com>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/power/sprd-bc1p2.h>
#include <linux/extcon.h>
#include <linux/usb/sprd_typec.h>
#if IS_ENABLED(CONFIG_VENDOR_SQC_CHARGER_SPRD)
#include <vendor/common/zte_misc.h>
#endif
#if IS_ENABLED(CONFIG_ZTE_POWER_SUPPLY)
#include <vendor/common/zte_power_supply.h>
#endif
#if IS_ENABLED(CONFIG_VENDOR_SQC_CHARGER_SPRD)
#include <sqc_common.h>
int sqc_notify_daemon_changed(int chg_id, int msg_type, int msg_val);
#endif




static const struct sprd_bc1p2_data sc2720_data = {
	.charge_status = SC2720_CHARGE_STATUS,
	.chg_det_fgu_ctrl = SC2720_CHG_DET_FGU_CTRL,
	.chg_bc1p2_ctrl2 = 0,
	.chg_int_delay_mask = SC27XX_CHG_INT_DELAY_MASK,
	.chg_int_delay_offset = SC27XX_CHG_INT_DELAY_OFFSET,
	.id = PMIC_SC2720,
};

static const struct sprd_bc1p2_data sc2721_data = {
	.charge_status = SC2721_CHARGE_STATUS,
	.chg_det_fgu_ctrl = SC2721_CHG_DET_FGU_CTRL,
	.chg_bc1p2_ctrl2 = 0,
	.chg_int_delay_mask = SC27XX_CHG_INT_DELAY_MASK,
	.chg_int_delay_offset = SC27XX_CHG_INT_DELAY_OFFSET,
	.id = PMIC_SC2720,
};

static const struct sprd_bc1p2_data sc2730_data = {
	.charge_status = SC2730_CHARGE_STATUS,
	.chg_det_fgu_ctrl = SC2730_CHG_DET_FGU_CTRL,
	.chg_bc1p2_ctrl2 = 0,
	.chg_int_delay_mask = SC27XX_CHG_INT_DELAY_MASK,
	.chg_int_delay_offset = SC27XX_CHG_INT_DELAY_OFFSET,
	.id = PMIC_SC2730,
};

static const struct sprd_bc1p2_data sc2731_data = {
	.charge_status = SC2731_CHARGE_STATUS,
	.chg_det_fgu_ctrl = SC2731_CHG_DET_FGU_CTRL,
	.chg_bc1p2_ctrl2 = 0,
	.chg_int_delay_mask = SC27XX_CHG_INT_DELAY_MASK,
	.chg_int_delay_offset = SC27XX_CHG_INT_DELAY_OFFSET,
	.id = PMIC_SC2730,
};

static const struct sprd_bc1p2_data ump9620_data = {
	.charge_status = UMP9620_CHARGE_STATUS,
	.chg_det_fgu_ctrl = UMP9620_CHG_DET_FGU_CTRL,
	.chg_bc1p2_ctrl2 = UMP9620_CHG_BC1P2_CTRL2,
	.chg_int_delay_mask = UMP96XX_CHG_INT_DELAY_MASK,
	.chg_int_delay_offset = UMP96XX_CHG_INT_DELAY_OFFSET,
	.id = PMIC_UMP9620,
};

static const struct sprd_bc1p2_data ump9651_data = {
	.charge_status = UMP9651_CHARGE_STATUS,
	.chg_det_fgu_ctrl = UMP9651_CHG_DET_FGU_CTRL,
	.chg_bc1p2_ctrl2 = UMP9651_CHG_BC1P2_CTRL2,
	.chg_int_delay_mask = UMP96XX_CHG_INT_DELAY_MASK,
	.chg_int_delay_offset = UMP96XX_CHG_INT_DELAY_OFFSET,
	.id = PMIC_UMP9651,
};

static const struct sprd_bc1p2_data ump9360_data = {
	.charge_status = UMP9360_CHARGE_STATUS,
	.chg_det_fgu_ctrl = UMP9360_CHG_DET_FGU_CTRL,
	.chg_bc1p2_ctrl2 = UMP9360_CHG_BC1P2_CTRL2,
	.chg_int_delay_mask = UMP9360_CHG_INT_DELAY_MASK,
	.chg_int_delay_offset = UMP9360_CHG_INT_DELAY_OFFSET,
	.id = PMIC_UMP9360,
};

static struct sprd_bc1p2_priv bc1p2_info;
static struct sprd_bc1p2_priv *bc1p2 = &bc1p2_info;

#if !(IS_ENABLED(CONFIG_UNISOC_FAST_CHARGER_SC27XX))

#if IS_ENABLED(CONFIG_VENDOR_SQC_CHARGER_SPRD)
static int zte_sqc_set_prop_by_name(const char *name, enum zte_power_supply_property psp, int data)
{
	struct zte_power_supply *psy = NULL;
	union power_supply_propval val = {0, };
	int rc = 0;

	if (name == NULL) {
		pr_err("bc1p2: psy name is NULL!!\n");
		goto failed_loop;
	}

	psy = zte_power_supply_get_by_name(name);
	if (!psy) {
		pr_err("bc1p2: get %s psy failed!!\n", name);
		goto failed_loop;
	}

	val.intval = data;

	rc = zte_power_supply_set_property(psy,
				psp, &val);
	if (rc < 0) {
		pr_err("bc1p2: Failed to set %s property:%d rc=%d\n", name, psp, rc);
		return rc;
	}

	zte_power_supply_put(psy);

	return 0;

failed_loop:
	return -EINVAL;
}
#endif

#if IS_ENABLED(CONFIG_VENDOR_SQC_CHARGER_SPRD)
extern struct sqc_bc1d2_proto_ops sqc_bc1d2_proto_node;
static int sqc_chg_type = SQC_NONE_TYPE;

static int sqc_bc1d2_get_charger_type(int *chg_type)
{
	struct sprd_bc1p2_priv *bc1p2 = (struct sprd_bc1p2_priv *)sqc_bc1d2_proto_node.arg;

	if (IS_ERR_OR_NULL(bc1p2) || IS_ERR_OR_NULL(bc1p2->phy)) {
		pr_err("bc1p2: [SQC-HW]: info is null\n");
		*chg_type = SQC_NONE_TYPE;
		return 0;
	}

  	switch (bc1p2->phy->chg_type) {
	case SDP_TYPE:
		*chg_type = SQC_SDP_TYPE;
		break;
	case DCP_TYPE:
		*chg_type = SQC_DCP_TYPE;
		break;
	case CDP_TYPE:
		*chg_type = SQC_CDP_TYPE;
		break;
	case ACA_TYPE:
		*chg_type = SQC_FLOAT_TYPE;
		break;
	case UNKNOWN_TYPE:
		*chg_type = SQC_NONE_TYPE;
		break;
	default:
		*chg_type = SQC_NONE_TYPE;
	}

	if (bc1p2->phy->chg_state == USB_CHARGER_PRESENT && bc1p2->phy->chg_type == UNKNOWN_TYPE) {
		pr_err("%s: force report float\n", __func__);
		*chg_type = SQC_FLOAT_TYPE;
	}

	if (!IS_ERR_OR_NULL(bc1p2->phy->edev)) {
		if (extcon_get_state(bc1p2->phy->edev, EXTCON_SOURCE)) {
			*chg_type = SQC_NONE_TYPE;
			pr_err("%s: typec extcon is source, force type is none.\n", __func__);
		}
	}

	pr_err("bc1p2:sqc_chg_type: %d, chg_type: %d\n", sqc_chg_type, *chg_type);

	if ((sqc_chg_type == SQC_SLEEP_MODE_TYPE)
			&& (*chg_type == SQC_NONE_TYPE)) {
		sqc_chg_type = *chg_type;
		zte_sqc_set_prop_by_name("zte_battery", POWER_SUPPLY_PROP_BATTERY_CHARGING_ENABLED, 1);
	} else if (sqc_chg_type == SQC_SLEEP_MODE_TYPE) {
		*chg_type = sqc_chg_type;
	}

	pr_err("bc1p2: [SQC-HW]: chg_state: %d sprd_type: %d, chg_type: %d\n",
				bc1p2->phy->chg_state, bc1p2->phy->chg_type, *chg_type);

	return 0;
}


struct sqc_bc1d2_proto_ops sqc_bc1d2_proto_node = {
	.get_charger_type = sqc_bc1d2_get_charger_type,
};

int sqc_sleep_node_set(const char *val, const void *arg)
{
	int sleep_mode_enable = 0;
	struct sprd_bc1p2_priv *bc1p2 = NULL;

	bc1p2 = (struct sprd_bc1p2_priv *)sqc_bc1d2_proto_node.arg;

	sscanf(val, "%d", &sleep_mode_enable);

	pr_err("bc1p2: sleep_mode_enable = %d\n", sleep_mode_enable);

	if (sleep_mode_enable) {
		if (sqc_chg_type != SQC_SLEEP_MODE_TYPE) {
			pr_err("bc1p2: sleep on status");

			/*disabel sqc-daemon*/
			sqc_chg_type = SQC_SLEEP_MODE_TYPE;
			sqc_notify_daemon_changed(SQC_NOTIFY_USB, SQC_NOTIFY_USB_STATUS_CHANGED, 1);

			/*enter sleep mode*/
			zte_sqc_set_prop_by_name("zte_battery", POWER_SUPPLY_PROP_BATTERY_CHARGING_ENABLED, 0);

		}
	} else {
		if (sqc_chg_type != SQC_SLEEP_MODE_TYPE) {
			pr_err("bc1p2: sleep off status");
			sqc_chg_type = SQC_NONE_TYPE;
		}
	}

	return 0;
}

int sqc_sleep_node_get(char *val, const void *arg)
{
	int sleep_mode = 0;

	if (sqc_chg_type == SQC_SLEEP_MODE_TYPE)
		sleep_mode = 1;

	return snprintf(val, PAGE_SIZE, "%u", sleep_mode);
}

static struct zte_misc_ops qc3dp_sleep_mode_node = {
	.node_name = "qc3dp_sleep_mode",
	.set = sqc_sleep_node_set,
	.get = sqc_sleep_node_get,
	.free = NULL,
	.arg = NULL,
};
#endif

#endif

static int sprd_bc1p2_redetect_control(bool enable)
{
	int ret = 0;

	if (enable)
		ret = regmap_update_bits(bc1p2->regmap, bc1p2->data->chg_bc1p2_ctrl2,
					 SPRD_CHG_DET_EB_MASK,
					 SPRD_CHG_BC1P2_REDET_ENABLE);
	else
		ret = regmap_update_bits(bc1p2->regmap, bc1p2->data->chg_bc1p2_ctrl2,
					 SPRD_CHG_DET_EB_MASK,
					 SPRD_CHG_BC1P2_REDET_DISABLE);

	if (ret)
		pr_err("bc1p2:fail to enable/disable redetect, enable = %d\n", enable);
	return ret;
}

static void sprd_bc1p2_enable_software_control(bool enable)
{
	int ret = 0;

	if (bc1p2->data->id == PMIC_UMP9360) {
		if (enable)
			ret = regmap_update_bits(bc1p2->regmap, UMP9360_CHG_TOP,
						 UMP9360_CHG_BC1P2_EN_MASK,
						 UMP9360_CHG_BC1P2_ENABLE);
		else
			ret = regmap_update_bits(bc1p2->regmap, UMP9360_CHG_TOP,
						 UMP9360_CHG_BC1P2_EN_MASK,
						 UMP9360_CHG_BC1P2_DISABLE);

		if (ret)
			pr_err("bc1p2:fail to enable/disable BC1P2_EN, enable = %d\n", enable);

		msleep(UMP9360_CHG_BC1P2_ENABLE_DELAY);
	}
}

static bool sprd_bc1p2_vchg_is_present(unsigned int *chg_det_fgu_ctrl, unsigned int *bc1p2_status)
{
	int ret = 0;

	ret = regmap_read(bc1p2->regmap, bc1p2->data->chg_det_fgu_ctrl, chg_det_fgu_ctrl);
	if (ret)
		pr_warn("bc1p2:line%d: fail to get chg_det_fgu_ctrl, ret = %d\n", __LINE__, ret);

	ret = regmap_read(bc1p2->regmap, bc1p2->data->charge_status, bc1p2_status);
	if (ret) {
		pr_err("bc1p2:line%d: fail to get bc1p2_status, ret = %d\n", __LINE__, ret);
		return false;
	}

	if (*bc1p2_status & BIT_CHGR_INT)
		return true;

	return false;
}

static enum usb_charger_type sprd_ump9360_bc1p2_detect(void)
{
	enum usb_charger_type type = UNKNOWN_TYPE;
	u32 status = 0, val = 0;
	int ret, vbus_cnt = 0, cnt = bc1p2->chg_detect_poll_count;

	if (bc1p2->chg_redet_poll_count)
		cnt = bc1p2->chg_redet_poll_count;

	bc1p2->chg_redet_poll_count = 0;

	do {
		if (bc1p2->shutdown)
			return UNKNOWN_TYPE;

		ret = regmap_read(bc1p2->regmap, bc1p2->data->charge_status, &val);
		if (ret) {
			type = UNKNOWN_TYPE;
			goto bc1p2_detect_end;
		}

		if (!(val & BIT_CHGR_INT) && vbus_cnt++ > SPRD_CHG_VBUS_POLL_COUNT) {
			pr_err("bc1p2:line%d absent bc1p2_status = 0x%x cnt = %d\n",
				__LINE__, val, cnt);
			type = UNKNOWN_TYPE;
			goto bc1p2_detect_end;
		}

		ret = regmap_read(bc1p2->regmap, UMP9360_MSI_RTC_DEBUG0, &val);
		if (ret) {
			type = UNKNOWN_TYPE;
			goto bc1p2_detect_end;
		}

		if (val & UMP9360_CHG_DET_DONE) {
			ret = regmap_read(bc1p2->regmap, UMP9360_MSI_CORE_DEBUG2, &val);
			if (ret) {
				type = UNKNOWN_TYPE;
				goto bc1p2_detect_end;
			}

			status = val & (UMP9360_CDP_INT | UMP9360_DCP_INT | UMP9360_SDP_INT);
			break;
		}

		msleep(SPRD_CHG_DET_POLL_DELAY);
	} while (--cnt > 0);

	switch (status) {
	case UMP9360_CDP_INT:
		type = CDP_TYPE;
		break;
	case UMP9360_DCP_INT:
		type = DCP_TYPE;
		break;
	case UMP9360_SDP_INT:
		type = SDP_TYPE;
		break;
	default:
		pr_info("bc1p2:line%d bc1p2_status = 0x%x, cnt = %d\n", __LINE__, val, cnt);
		type = UNKNOWN_TYPE;
	}

bc1p2_detect_end:
	if (bc1p2->redetect_enable)
		sprd_bc1p2_redetect_control(false);
	return type;
}

static enum usb_charger_type sprd_common_bc1p2_detect(void)
{
	enum usb_charger_type type = UNKNOWN_TYPE;
	u32 status = 0, val = 0;
	int ret, vbus_cnt = 0, cnt = bc1p2->chg_detect_poll_count;

	if (bc1p2->chg_redet_poll_count)
		cnt = bc1p2->chg_redet_poll_count;

	bc1p2->chg_redet_poll_count = 0;

	do {
		if (bc1p2->shutdown)
			return UNKNOWN_TYPE;

		ret = regmap_read(bc1p2->regmap, bc1p2->data->charge_status, &val);
		if (ret) {
			type = UNKNOWN_TYPE;
			goto bc1p2_detect_end;
		}

		if (!(val & BIT_CHGR_INT) && vbus_cnt++ > SPRD_CHG_VBUS_POLL_COUNT) {
			pr_err("bc1p2:line%d absent bc1p2_status = 0x%x cnt = %d\n",
				__LINE__, val, cnt);
			type = UNKNOWN_TYPE;
			goto bc1p2_detect_end;
		}

		if (val & BIT_CHG_DET_DONE) {
			status = val & (BIT_CDP_INT | BIT_DCP_INT | BIT_SDP_INT);
			break;
		}

		msleep(SPRD_CHG_DET_POLL_DELAY);
	} while (--cnt > 0);

	switch (status) {
	case BIT_CDP_INT:
		type = CDP_TYPE;
		break;
	case BIT_DCP_INT:
		type = DCP_TYPE;
		break;
	case BIT_SDP_INT:
		type = SDP_TYPE;
		break;
	default:
		pr_err("bc1p2:line%d bc1p2_status = 0x%x, cnt = %d\n", __LINE__, val, cnt);
		type = UNKNOWN_TYPE;
	}

bc1p2_detect_end:
	if (bc1p2->redetect_enable)
		sprd_bc1p2_redetect_control(false);
	return type;
}

static enum usb_charger_type sprd_bc1p2_detect(void)
{
	enum usb_charger_type type = UNKNOWN_TYPE;

	if (bc1p2->data->id == PMIC_UMP9360) {
		type = sprd_ump9360_bc1p2_detect();
		return type;
	}

	type = sprd_common_bc1p2_detect();
	return type;
}

static int sprd_bc1p2_redetect_trigger(u32 time_ms, bool is_need_vbus_check)
{
	int ret = 0, redet_delay_ms = 0;
	u32 reg_val = 0;
	unsigned int chg_det_fgu_ctrl = 0, bc1p2_status = 0;

	if (time_ms > SPRD_CHG_REDET_DELAY_MS_MAX)
		time_ms = SPRD_CHG_REDET_DELAY_MS_MAX;

	reg_val = time_ms / SPRD_CHG_DET_DELAY_STEP_MS;

	if (bc1p2->data->id == PMIC_UMP9360) {
		ret = regmap_update_bits(bc1p2->regmap, bc1p2->data->chg_det_fgu_ctrl,
					 UMP9360_CHG_REDET_DELAY_MASK,
					 reg_val << UMP9360_CHG_REDET_DELAY_OFFSET);
	} else {
		ret = regmap_update_bits(bc1p2->regmap, bc1p2->data->chg_det_fgu_ctrl,
					 SPRD_CHG_REDET_DELAY_MASK,
					 reg_val << SPRD_CHG_REDET_DELAY_OFFSET);
	}
	if (ret)
		return SPRD_ERROR_REGMAP_UPDATE;

	ret = sprd_bc1p2_redetect_control(true);
	if (ret)
		return SPRD_ERROR_REGMAP_UPDATE;

	msleep(SPRD_CHG_DET_POLL_DELAY);

	if (likely(is_need_vbus_check)) {
		if (!sprd_bc1p2_vchg_is_present(&chg_det_fgu_ctrl, &bc1p2_status)) {
			pr_err("bc1p2:line%d: absent, chg_det_fgu_ctrl = 0x%x, bc1p2_status = 0x%x\n",
				__LINE__, chg_det_fgu_ctrl, bc1p2_status);
			return SPRD_ERROR_CHARGER_INIT;
		}
	}

	if (bc1p2->data->id == PMIC_UMP9360) {
		ret = regmap_read(bc1p2->regmap, UMP9360_MSI_RTC_DEBUG0, &bc1p2_status);
		if (ret) {
			pr_err("bc1p2:line%d: fail to get bc1p2_status, ret = %d\n", __LINE__, ret);
			return SPRD_ERROR_CHARGER_INIT;
		}

		if (bc1p2_status & UMP9360_CHG_DET_DONE)
			return SPRD_ERROR_CHARGER_DETDONE;
	} else {
		if (bc1p2_status & BIT_CHG_DET_DONE)
			return SPRD_ERROR_CHARGER_DETDONE;
	}

	redet_delay_ms = time_ms + SPRD_CHG_BC1P2_DETECT_TIME;
	bc1p2->chg_redet_poll_count = redet_delay_ms / SPRD_CHG_DET_POLL_DELAY;

	return SPRD_ERROR_NO_ERROR;
}

enum usb_charger_type sprd_bc1p2_charger_detect(struct usb_phy *x)
{
	int vbus_cnt = 0, cnt = bc1p2->chg_detect_all_count;

	if (bc1p2->is_bc1p2_parallel) {
		bc1p2->is_dpdm_high_resistance = true;
		complete_all(&bc1p2->dpdm_high_resistance);
	}

	do {
		if (bc1p2->shutdown)
			return UNKNOWN_TYPE;

		if (vbus_cnt++ > SPRD_CHG_VBUS_POLL_COUNT && x->chg_state == USB_CHARGER_ABSENT)
			return UNKNOWN_TYPE;

		if (bc1p2->flags & CHARGER_DETECT_DONE)
			break;
		msleep(SPRD_CHG_DET_POLL_DELAY);
	} while (--cnt > 0);

	return x->chg_type;
}
EXPORT_SYMBOL_GPL(sprd_bc1p2_charger_detect);

enum usb_charger_type sprd_bc1p2_retry_detect(struct usb_phy *x, int times, bool is_need_vbus_check)
{
	enum usb_charger_type type = UNKNOWN_TYPE;
	int ret = 0;

	if (bc1p2->shutdown)
		return UNKNOWN_TYPE;

	ret = sprd_bc1p2_redetect_trigger(times, is_need_vbus_check);
	if (ret) {
		sprd_bc1p2_redetect_control(false);
		if (ret)
			pr_err("bc1p2: trigger redetect bc1p2 failed, ret= %d\n", ret);
		return UNKNOWN_TYPE;
	}

	type = sprd_bc1p2_detect();

	return type;
}

static void sprd_bc1p2_notify_charger(struct usb_phy *x)
{
	switch (bc1p2->detect_state) {
	case CHG_STATE_UNDETECT:
	case CHG_STATE_DETECTED:
		if (x->chg_type == SDP_TYPE)
			usb_phy_set_charger_current(x, DEFAULT_CUR_MIN);
		else
			schedule_work(&x->chg_work);
		break;
	case CHG_STATE_RETRY_DETECT:
		if (bc1p2->retry_chg_detect_count == 0 && x->chg_type == UNKNOWN_TYPE)
			schedule_work(&x->chg_work);
		break;
	case CHG_STATE_RETRY_DETECTED:
		if (x->chg_type == SDP_TYPE)
			usb_phy_set_charger_current(x, DEFAULT_CUR_MIN);
		else if (x->chg_type != UNKNOWN_TYPE)
			schedule_work(&x->chg_work);
		break;
	default:
		break;
	}
}

static void sprd_get_bc1p2_type(struct sprd_bc1p2_priv *bc1p2)
{
	struct usb_phy *x = bc1p2->phy;
	unsigned int chg_det_fgu_ctrl = 0, bc1p2_status = 0;
	unsigned long timeout;

	switch (bc1p2->detect_state) {
	case CHG_STATE_INITIALIZE:
		if (bc1p2->data->id == PMIC_UMP9360) {
			bc1p2->type = sprd_bc1p2_detect();
			sprd_bc1p2_enable_software_control(false);
			if (bc1p2->type == CDP_TYPE) {
				x->chg_type = bc1p2->type;
				bc1p2->detect_state = CHG_STATE_DETECTED;
				bc1p2->flags |= CHARGER_DETECT_DONE;
				pr_info("bc1p2:chg initialize detect with %d\n", x->chg_type);
				break;
			}
		}
		fallthrough;
	case CHG_STATE_UNDETECT:
		pr_err("bc1p2:chg entry\n");
		x->chg_type = UNKNOWN_TYPE;
		bc1p2->detect_state = CHG_STATE_DETECT;
		if (bc1p2->flags & CHARGER_DETECT_DONE)
			bc1p2->detect_state = CHG_STATE_DETECTED;
		fallthrough;
	case CHG_STATE_DETECT:
		pr_err("bc1p2:chg detect\n");
		if (bc1p2->is_bc1p2_parallel) {
			if (!bc1p2->is_dpdm_high_resistance) {
				timeout = wait_for_completion_timeout(&bc1p2->dpdm_high_resistance,
								      SPRD_CHG_WAIT_SET_DMDP);
				if (!timeout)
					pr_err("%s wait dpdm_high_resistance timeout\n", __func__);
			}

			sprd_bc1p2_enable_software_control(true);
			bc1p2->type = sprd_bc1p2_detect();
		} else {
			bc1p2->type = sprd_bc1p2_detect();
		}

		bc1p2->detect_state = CHG_STATE_DETECTED;
		fallthrough;
	case CHG_STATE_DETECTED:
		x->chg_type = bc1p2->type;
		pr_err("bc1p2:chg detect finished with %d\n", x->chg_type);

		if (bc1p2->flags & CHARGER_DETECT_DONE)
			break;

		if (x->chg_type == SDP_TYPE || x->chg_type == CDP_TYPE || x->chg_type == DCP_TYPE) {
			bc1p2->flags |= CHARGER_DETECT_DONE;
			sprd_bc1p2_enable_software_control(false);
			break;
		}

		if (!bc1p2->redetect_enable) {
			bc1p2->flags |= CHARGER_DETECT_DONE;
			break;
		}

		if (!sprd_bc1p2_vchg_is_present(&chg_det_fgu_ctrl, &bc1p2_status)) {
			pr_err("bc1p2:line%d: absent, chg_det_fgu_ctrl = 0x%x, bc1p2_status = 0x%x\n",
				__LINE__, chg_det_fgu_ctrl, bc1p2_status);
			break;
		}

		if (x->chg_type == UNKNOWN_TYPE) {
			bc1p2->detect_state = CHG_STATE_RETRY_DETECT;
			bc1p2->rework = true;
		}
		break;
	case CHG_STATE_RETRY_DETECT:
		pr_err("bc1p2:bc1.2 retry detect count=%d", bc1p2->retry_chg_detect_count);
		if (!sprd_bc1p2_vchg_is_present(&chg_det_fgu_ctrl, &bc1p2_status)) {
			pr_err("bc1p2:line%d: absent, chg_det_fgu_ctrl = 0x%x, bc1p2_status = 0x%x\n",
				__LINE__, chg_det_fgu_ctrl, bc1p2_status);
			break;
		}

		bc1p2->type = sprd_bc1p2_retry_detect(x, SPRD_CHG_REDET_DELAY_MS_MAX, true);

		if (!sprd_bc1p2_vchg_is_present(&chg_det_fgu_ctrl, &bc1p2_status)) {
			pr_err("bc1p2:line%d: absent, chg_det_fgu_ctrl = 0x%x, bc1p2_status = 0x%x\n",
				__LINE__, chg_det_fgu_ctrl, bc1p2_status);
			break;
		}

		if (bc1p2->type == UNKNOWN_TYPE && !bc1p2->shutdown &&
		    bc1p2->retry_chg_detect_count < SPRD_CHG_MAX_REDETECT_COUNT) {
			bc1p2->rework = true;
			bc1p2->retry_chg_detect_count++;
			pr_err("bc1p2:line%d: count=%d\n", __LINE__, bc1p2->retry_chg_detect_count);
			break;
		}

		bc1p2->detect_state = CHG_STATE_RETRY_DETECTED;
		fallthrough;
	case CHG_STATE_RETRY_DETECTED:
		x->chg_type = bc1p2->type;
		pr_err("bc1p2:chg retry_detect finished with %d\n", x->chg_type);

		if (x->chg_type == NON_DCP_TYPE) {
			pr_err("bc1p2: BC1.2 type is NON_DCP, force report DCP\n");
			x->chg_type = DCP_TYPE;
		}

		/*add by zte, force report float type when pd hard reset or typec connect*/
		if (!IS_ERR_OR_NULL(bc1p2->phy->edev)
				&& extcon_get_state(bc1p2->phy->edev, EXTCON_SINK)
				&& (x->chg_type == UNKNOWN_TYPE)) {
			pr_err("bc1p2:line%d: EXTCON_SINK force report DCP\n", __LINE__);
			x->chg_type = DCP_TYPE;
		}

		bc1p2->flags |= CHARGER_DETECT_DONE;
		sprd_bc1p2_enable_software_control(false);

		break;
	default:
		break;
	}
}

static void sprd_get_bc1p2_type_work(struct kthread_work *work)
{
	struct sprd_bc1p2_priv *bc1p2 = container_of(work, struct sprd_bc1p2_priv,
						     bc1p2_kwork.work);
	struct usb_phy *x = bc1p2->phy;

	switch (x->chg_state) {
	case USB_CHARGER_PRESENT:
		pr_err("bc1p2: usb_charger_present\n");
		mutex_lock(&bc1p2->bc1p2_lock);
		sprd_get_bc1p2_type(bc1p2);
		mutex_unlock(&bc1p2->bc1p2_lock);

		if (bc1p2->bc1p2_thread && bc1p2->rework) {
			kthread_mod_delayed_work(&bc1p2->bc1p2_kworker,
						 &bc1p2->bc1p2_kwork, SPRD_CHG_DETECT_DELAY);
			bc1p2->rework = false;
		}
		break;
	case USB_CHARGER_ABSENT:
		complete_all(&bc1p2->dpdm_high_resistance);
		mutex_lock(&bc1p2->bc1p2_lock);
		pr_err("bc1p2:usb_charger_absent\n");
		x->chg_type = UNKNOWN_TYPE;
		bc1p2->detect_state = CHG_STATE_UNDETECT;
		bc1p2->retry_chg_detect_count = 0;
		bc1p2->type = UNKNOWN_TYPE;
		bc1p2->is_dpdm_high_resistance = false;
		bc1p2->flags &= ~CHARGER_DETECT_DONE;
		mutex_unlock(&bc1p2->bc1p2_lock);
		break;
	default:
		pr_err("bc1p2:Unknown USB charger state: %d\n", x->chg_state);
		break;
	}

	sprd_bc1p2_notify_charger(x);

#if IS_ENABLED(CONFIG_VENDOR_SQC_CHARGER_SPRD)
	sqc_notify_daemon_changed(SQC_NOTIFY_USB,
					SQC_NOTIFY_USB_STATUS_CHANGED, 1);
#endif
}

static void sprd_usb_change_work(struct kthread_work *work)
{
	struct sprd_bc1p2_priv *bc1p2 = container_of(work, struct sprd_bc1p2_priv,
							  usb_change_kwork.work);

	spin_lock(&bc1p2->vbus_event_lock);
	while (bc1p2->vbus_events) {
		bc1p2->vbus_events = false;
		pr_err("%s: line %d\n", __func__, __LINE__);
		spin_unlock(&bc1p2->vbus_event_lock);
		if (bc1p2->bc1p2_thread)
			kthread_cancel_delayed_work_sync(&bc1p2->bc1p2_kwork);
		kthread_queue_delayed_work(&bc1p2->bc1p2_kworker,
					   &bc1p2->bc1p2_kwork, 0);
		spin_lock(&bc1p2->vbus_event_lock);
	}

	spin_unlock(&bc1p2->vbus_event_lock);
}

static void sprd_usb_changed(struct sprd_bc1p2_priv *bc1p2, enum usb_charger_state state)
{
	struct usb_phy *usb_phy = bc1p2->phy;

	if (!usb_phy) {
		pr_err("%s:line%d: NULL pointer!!!\n", __func__, __LINE__);
		return;
	}

	if (bc1p2->shutdown)
		return;

	spin_lock(&bc1p2->vbus_event_lock);
	bc1p2->vbus_events = true;

	usb_phy->chg_state = state;
	pr_err("%s: line %d\n", __func__, __LINE__);
	spin_unlock(&bc1p2->vbus_event_lock);

	if (bc1p2->bc1p2_thread)
		kthread_queue_delayed_work(&bc1p2->bc1p2_kworker,
					   &bc1p2->usb_change_kwork, 0);
}

static int sprd_bc1p2_vbus_notify(struct notifier_block *nb,
				  unsigned long event, void *data)
{
	struct sprd_bc1p2_priv *bc1p2 = container_of(nb, struct sprd_bc1p2_priv, vbus_nb);

	pr_err("%s: line %d\n", __func__, __LINE__);

	if (bc1p2->shutdown || !bc1p2->phy)
		return 0;

	if (bc1p2->phy->last_event == USB_EVENT_ID)
		return 0;

	pr_err("%s: line %d\n", __func__, __LINE__);

	pm_wakeup_event(bc1p2->dev, 400);
	sprd_usb_changed(bc1p2, event ? USB_CHARGER_PRESENT : USB_CHARGER_ABSENT);

	return 0;
}

static int sprd_bc1p2_add_extcon(struct sprd_bc1p2_priv *bc1p2)
{
	struct usb_phy *usb_phy = bc1p2->phy;
	int ret = 0;

	if (!usb_phy) {
		pr_err("%s:line%d: NULL pointer!!!\n", __func__, __LINE__);
		return -EPROBE_DEFER;
	}

	if (usb_phy->edev) {
		pr_err("%s: line %d name %s\n", __func__, __LINE__, extcon_get_edev_name(usb_phy->edev));
		bc1p2->vbus_nb.notifier_call = sprd_bc1p2_vbus_notify;
		ret = devm_extcon_register_notifier(bc1p2->dev, usb_phy->edev, EXTCON_USB,
						    &bc1p2->vbus_nb);
		if (ret < 0) {
			pr_err("bc1p2: %s failed\n", __func__);
			return ret;
		}
	}

	return 0;
}

int usb_add_bc1p2_init(struct usb_phy *x, bool is_bc1p2_parallel)
{
	if (!x->dev) {
		pr_err("bc1p2: no device provided for PHY\n");
		return -EINVAL;
	}

	bc1p2->phy = x;
	bc1p2->is_bc1p2_parallel = is_bc1p2_parallel;
	bc1p2->chg_detect_poll_count = SPRD_CHG_INT_DELAY_MS + SPRD_CHG_BC1P2_DETECT_TIME;
	bc1p2->chg_detect_poll_count += SPRD_CHG_VBUS_DEBOUNCE;
	bc1p2->chg_detect_poll_count = bc1p2->chg_detect_poll_count / SPRD_CHG_DET_POLL_DELAY;

	bc1p2->chg_detect_all_count = (SPRD_CHG_REDET_DELAY_MS + SPRD_CHG_BC1P2_DETECT_TIME) *
								(SPRD_CHG_MAX_REDETECT_COUNT + 1);
	bc1p2->chg_detect_all_count = bc1p2->chg_detect_all_count / SPRD_CHG_DET_POLL_DELAY +
		bc1p2->chg_detect_poll_count;
	init_completion(&bc1p2->dpdm_high_resistance);

	return 0;
}
EXPORT_SYMBOL_GPL(usb_add_bc1p2_init);

static int sprd_bc1p2_probe(struct platform_device *pdev)
{
	int err = 0, chg_int_delay = 0;
	unsigned int chg_det_fgu_ctrl = 0, bc1p2_status = 0;
	struct sched_param param = { .sched_priority = 1 };
	struct device *dev = &pdev->dev;

	pr_err("%s: line %d into\n", __func__, __LINE__);

	if (!bc1p2->phy) {
		pr_err("bc1p2: failed to get phy, retry\n");
		return -EPROBE_DEFER;
	}

	if (!bc1p2->phy->edev) {
		pr_err("bc1p2: failed to get edev, retry\n");
		return -EPROBE_DEFER;
	}

	bc1p2->dev = dev;
	bc1p2->data = of_device_get_match_data(dev);
	if (!bc1p2->data) {
		pr_err("bc1p2: no matching driver data found\n");
		return -EINVAL;
	}

	bc1p2->regmap = dev_get_regmap(dev->parent, NULL);
	if (!bc1p2->regmap) {
		pr_err("bc1p2: failed to get regmap\n");
		return -ENODEV;
	}

	chg_int_delay = SPRD_CHG_INT_DELAY_MS / SPRD_CHG_DET_DELAY_STEP_MS;
	err = regmap_update_bits(bc1p2->regmap, bc1p2->data->chg_det_fgu_ctrl,
				 bc1p2->data->chg_int_delay_mask,
				 chg_int_delay << bc1p2->data->chg_int_delay_offset);
	if (err)
		return err;

	if (bc1p2->data->chg_bc1p2_ctrl2 == 0)
		bc1p2->redetect_enable = false;
	else
		bc1p2->redetect_enable = true;

	bc1p2->detect_state = CHG_STATE_INITIALIZE;
	if (!sprd_bc1p2_vchg_is_present(&chg_det_fgu_ctrl, &bc1p2_status))
		bc1p2->detect_state = CHG_STATE_UNDETECT;

	platform_set_drvdata(pdev, bc1p2);

	mutex_init(&bc1p2->bc1p2_lock);
	spin_lock_init(&bc1p2->vbus_event_lock);

	kthread_init_worker(&bc1p2->bc1p2_kworker);
	kthread_init_delayed_work(&bc1p2->usb_change_kwork, sprd_usb_change_work);
	kthread_init_delayed_work(&bc1p2->bc1p2_kwork, sprd_get_bc1p2_type_work);
	bc1p2->bc1p2_thread = kthread_run(kthread_worker_fn, &bc1p2->bc1p2_kworker,
					  "sprd_bc1p2_worker");
	if (IS_ERR(bc1p2->bc1p2_thread)) {
		bc1p2->bc1p2_thread = NULL;
		pr_err("bc1p2: failed to run bc1p2_thread\n");
		return PTR_ERR(bc1p2->bc1p2_thread);
	}

	sched_setscheduler(bc1p2->bc1p2_thread, SCHED_FIFO, &param);

	bc1p2->shutdown = false;
	err = sprd_bc1p2_add_extcon(bc1p2);
	if (err)
		return err;

	if (extcon_get_state(bc1p2->phy->edev, EXTCON_USB) > 0)
		sprd_usb_changed(bc1p2, USB_CHARGER_PRESENT);

#if !(IS_ENABLED(CONFIG_UNISOC_FAST_CHARGER_SC27XX))
	pr_err("%s: line %d register bc1d2 node\n", __func__, __LINE__);
#if IS_ENABLED(CONFIG_VENDOR_SQC_CHARGER_SPRD)
	sqc_bc1d2_proto_node.arg = (void *)bc1p2;
	sqc_hal_bc1d2_register(&sqc_bc1d2_proto_node);
	zte_misc_register_callback(&qc3dp_sleep_mode_node, bc1p2);
#endif

#endif

	pr_err("%s: line %d exit\n", __func__, __LINE__);

	return err;
}

static void usb_remove_bc1p2(struct platform_device *pdev)
{
	struct sprd_bc1p2_priv *bc1p2 = platform_get_drvdata(pdev);

	if (bc1p2->bc1p2_thread) {
		kthread_flush_worker(&bc1p2->bc1p2_kworker);
		kthread_stop(bc1p2->bc1p2_thread);
		bc1p2->bc1p2_thread = NULL;
	}
}

void usb_shutdown_bc1p2(struct platform_device *pdev)
{
	struct sprd_bc1p2_priv *bc1p2 = platform_get_drvdata(pdev);

	bc1p2->shutdown = true;

	if (bc1p2->bc1p2_thread) {
		kthread_cancel_delayed_work_sync(&bc1p2->usb_change_kwork);
		kthread_cancel_delayed_work_sync(&bc1p2->bc1p2_kwork);
		kthread_stop(bc1p2->bc1p2_thread);
		bc1p2->bc1p2_thread = NULL;
	}
}

static const struct of_device_id sprd_bc1p2_of_match[] = {
	{ .compatible = "sprd,sc2720-bc1p2", .data = &sc2720_data},
	{ .compatible = "sprd,sc2721-bc1p2", .data = &sc2721_data},
	{ .compatible = "sprd,sc2730-bc1p2", .data = &sc2730_data},
	{ .compatible = "sprd,sc2731-bc1p2", .data = &sc2731_data},
	{ .compatible = "sprd,ump9620-bc1p2", .data = &ump9620_data},
	{ .compatible = "sprd,ump9651-bc1p2", .data = &ump9651_data},
	{ .compatible = "sprd,ump9360-bc1p2", .data = &ump9360_data},
	{ }
};

MODULE_DEVICE_TABLE(of, sprd_bc1p2_of_match);

static struct platform_driver sprd_bc1p2_driver = {
	.driver = {
		.name = "sprd-bc1p2",
		.of_match_table = sprd_bc1p2_of_match,
	 },
	.probe = sprd_bc1p2_probe,
	.remove = usb_remove_bc1p2,
	.shutdown = usb_shutdown_bc1p2,
};

module_platform_driver(sprd_bc1p2_driver);

MODULE_AUTHOR("Yongzhi Chen <yongzhi.chen@unisoc.com>");
MODULE_DESCRIPTION("sprd bc1p2 driver");
MODULE_LICENSE("GPL");
MODULE_ALIAS("platform:sprd_bc1p2");
