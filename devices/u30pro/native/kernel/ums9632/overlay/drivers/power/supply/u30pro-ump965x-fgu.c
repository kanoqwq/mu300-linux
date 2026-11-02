// SPDX-License-Identifier: GPL-2.0
/*
 * UMP965x native battery measurements for U30 Pro.
 * Register/calibration/NTC formulas derived from the Unisoc GPL sources:
 * sprd_ump965x_fuel_gauge.c, sprd_fuel_gauge_core.c, sprd_battery_info.c
 * Original fuel-gauge sources: Copyright (C) 2011 Unisoc Co., Ltd.
 *
 * Keep retained coulomb count, interrupts and USER areas. Calibrate the
 * volatile current ADC zero point through its internal input-short switch.
 * SOC is a software estimate (OCV table at start-up, current integration, relax / full / empty corrections).
 * The retained Android percentage in the USER area is never used or written.
 */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/iio/consumer.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/nvmem-consumer.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
#include <linux/slab.h>
#include <linux/workqueue.h>
#include <linux/ktime.h>

#define FGU_MODULE_EN 0x2008
#define FGU_CLOCK_EN 0x2010
#define FGU_ADC_CTRL0 0x08
#define FGU_STATUS 0x14
#define FGU_VOLTAGE 0x28
#define FGU_CURRENT_H 0x34
#define FGU_CURRENT_L 0x38
#define FGU_COUNT_H 0x5c
#define FGU_COUNT_M 0x60
#define FGU_COUNT_L 0x64
#define FGU_OFFSET_H 0x84
#define FGU_OFFSET_L 0x88
#define FGU_USER_STATUS 0x94

struct ump_ntc_point { int uv; int temp; };
struct ump_ocv_point { int uv; int pct; };
#define UMP_SOC_PERIOD_MS	2000
#define UMP_RELAX_UA		30000
#define UMP_RELAX_SYNC_MS	60000
struct ump_fgu {
	struct device *dev;
	struct regmap *map;
	struct mutex lock;
	struct iio_channel *ntc, *charge;
	struct power_supply *psy;
	u32 base, sense_uohm, comp_mohm, design_uah;
	u32 efuse[3];
	int voltage_gain, current_gain, battery_index, battery_id_mv;
	struct ump_ntc_point *ntc_table;
	int ntc_count;
	u32 previous_offset, programmed_offset;
	int zero_point;
	/* state of charge */
	struct ump_ocv_point *ocv;
	int ocv_count;
	u32 full_uah, r_uohm, full_uv, full_ua;
	s64 charge_uah;
	bool soc_ready;
	u32 relax_ms;
	ktime_t last;
	struct delayed_work soc_work;
};

static s64 ump_round(s64 val, s32 divisor)
{
	return div_s64(val + (val >= 0 ? divisor / 2 : -divisor / 2), divisor);
}

static int ump_read(struct ump_fgu *f, unsigned int offset, unsigned int *val)
{
	return regmap_read(f->map, f->base + offset, val);
}

/* Split live registers must be stable across the complete read. */
static int ump_current_code(struct ump_fgu *f, u32 *val)
{
	unsigned int high, low, check;
	int ret, i;
	for (i = 0; i < 5; i++) {
		ret = ump_read(f, FGU_CURRENT_H, &high);
		if (ret)
			return ret;
		ret = ump_read(f, FGU_CURRENT_L, &low);
		if (ret)
			return ret;
		ret = ump_read(f, FGU_CURRENT_H, &check);
		if (ret)
			return ret;
		if ((high & 0x1f) == (check & 0x1f)) {
			*val = ((high & 0x1f) << 16) | (low & 0xffff);
			return 0;
		}
	}
	return -EAGAIN;
}

static int ump_current_raw(struct ump_fgu *f, u32 *val)
{
	u32 ctrl;
	int ret = ump_read(f, FGU_ADC_CTRL0, &ctrl);
	if (ret)
		return ret;
	if (ctrl & BIT(8))
		return -EBUSY;
	return ump_current_code(f, val);
}

static int ump_set_offset(struct ump_fgu *f, u32 value)
{
	int ret = regmap_update_bits(f->map, f->base + FGU_OFFSET_H, 0x1f, value >> 16);
	if (ret)
		return ret;
	return regmap_update_bits(f->map, f->base + FGU_OFFSET_L, 0xffff, value & 0xffff);
}

static int ump_calibrate_zero(struct ump_fgu *f)
{
	u32 high, low, ctrl, raw, verify_high, verify_low;
	int ret, cleanup, i, zero[3], smallest, largest;
	ret = ump_read(f, FGU_ADC_CTRL0, &ctrl);
	if (ret)
		return ret;
	if (ctrl & BIT(8))
		return -EBUSY;
	ret = ump_read(f, FGU_OFFSET_H, &high);
	if (ret)
		return ret;
	ret = ump_read(f, FGU_OFFSET_L, &low);
	if (ret)
		return ret;
	f->previous_offset = ((high & 0x1f) << 16) | (low & 0xffff);
	ret = regmap_update_bits(f->map, f->base + FGU_ADC_CTRL0, BIT(8), BIT(8));
	if (ret)
		return ret;
	/* Measure absolute bias on every kexec, independently of an old offset.
	 * This shorts only the ADC's internal inputs, never battery terminals.
	 */
	ret = ump_set_offset(f, 0);
	if (ret)
		goto restore;
	msleep(1010); /* More than two 2Hz sample periods, as specified by BSP. */
	for (i = 0; i < ARRAY_SIZE(zero); i++) {
		ret = ump_current_code(f, &raw);
		if (ret)
			goto restore;
		zero[i] = DIV_ROUND_CLOSEST(raw, 15);
		if (i < ARRAY_SIZE(zero) - 1)
			msleep(510);
	}
	smallest = min3(zero[0], zero[1], zero[2]);
	largest = max3(zero[0], zero[1], zero[2]);
	if (smallest < 65536 - 2048 || largest > 65536 + 2048 || largest - smallest > 16) {
		ret = -ERANGE;
		goto restore;
	}
	f->zero_point = DIV_ROUND_CLOSEST(zero[0] + zero[1] + zero[2], 3);
	f->programmed_offset = ((65536 - f->zero_point) * 15) & 0x1fffff;
	ret = ump_set_offset(f, f->programmed_offset);
	if (ret)
		goto restore;
	ret = ump_read(f, FGU_OFFSET_H, &verify_high);
	if (!ret)
		ret = ump_read(f, FGU_OFFSET_L, &verify_low);
	if (!ret && (((verify_high & 0x1f) << 16) | (verify_low & 0xffff)) != f->programmed_offset)
		ret = -EIO;
	if (ret)
		goto restore;
	goto clear_short;
restore:
	cleanup = ump_set_offset(f, f->previous_offset);
	if (cleanup)
		dev_err(f->dev, "offset restore failed: %d (initial error %d)\n", cleanup, ret);
clear_short:
	cleanup = -EIO;
	for (i = 0; i < 3 && cleanup; i++) {
		cleanup = regmap_update_bits(f->map, f->base + FGU_ADC_CTRL0, BIT(8), 0);
		if (cleanup)
			usleep_range(100, 200);
	}
	if (cleanup) {
		dev_err(f->dev, "ADC internal short cleanup failed: %d\n", cleanup);
		return cleanup;
	}
	if (ret)
		return ret;
	msleep(1010); /* Discard samples taken before the input switch settled. */
	dev_info(f->dev, "ADC zero=%d offset=%05x previous=%05x samples=%d,%d,%d\n",
		f->zero_point, f->programmed_offset, f->previous_offset, zero[0], zero[1], zero[2]);
	return 0;
}

static int ump_current_ua(struct ump_fgu *f, int *ua)
{
	u32 raw;
	int ret = ump_current_raw(f, &raw);
	if (ret)
		return ret;
	*ua = ump_round(((s64)raw / 15 - 65536) * 1000000, f->current_gain);
	return 0;
}

static int ump_count_raw(struct ump_fgu *f, u64 *count)
{
	u32 h, m, l, h2, m2;
	int ret, i;
	for (i = 0; i < 5; i++) {
		ret = ump_read(f, FGU_COUNT_H, &h);
		if (ret)
			return ret;
		ret = ump_read(f, FGU_COUNT_M, &m);
		if (ret)
			return ret;
		ret = ump_read(f, FGU_COUNT_L, &l);
		if (ret)
			return ret;
		ret = ump_read(f, FGU_COUNT_M, &m2);
		if (ret)
			return ret;
		ret = ump_read(f, FGU_COUNT_H, &h2);
		if (ret)
			return ret;
		if ((h & 0xf) == (h2 & 0xf) && (m & 0xffff) == (m2 & 0xffff)) {
			*count = ((u64)(h & 0xf) << 32) | ((u64)(m & 0xffff) << 16) | (l & 0xffff);
			return 0;
		}
	}
	return -EAGAIN;
}

static int ump_temperature(struct ump_fgu *f, int *temp)
{
	s64 uv, resistance_vol, calib_resistance_vol, correction, denominator;
	int mv, ua, ma, ret, i;
	struct ump_ntc_point *a, *b;
	ret = iio_read_channel_processed(f->ntc, &mv);
	if (ret < 0)
		return ret;
	if (mv <= 0 || mv >= 1875)
		return -ERANGE;
	uv = (s64)mv * 1000;
	if (f->comp_mohm) {
		ret = ump_current_ua(f, &ua);
		if (ret)
			return ret;
		ma = ump_round(ua, 1000);
		resistance_vol = ump_round((s64)ma * f->comp_mohm, 10);
		calib_resistance_vol = ump_round((s64)ma * (f->sense_uohm / 10), 1000) + resistance_vol;
		denominator = 187500 - calib_resistance_vol;
		if (denominator <= 0 || denominator > INT_MAX)
			return -ERANGE;
		correction = ump_round((uv / 10 - resistance_vol) * calib_resistance_vol, denominator);
		uv += correction * 10 - resistance_vol * 10;
	}
	if (uv <= 0 || uv >= 1875000)
		return -ERANGE;
	for (i = 0; i < f->ntc_count; i++)
		if (uv > f->ntc_table[i].uv)
			break;
	if (i == 0)
		*temp = f->ntc_table[0].temp - 1000;
	else if (i == f->ntc_count)
		*temp = f->ntc_table[i - 1].temp - 1000;
	else {
		a = &f->ntc_table[i - 1]; b = &f->ntc_table[i];
		*temp = a->temp + ump_round((s64)(b->temp - a->temp) * (a->uv - uv), a->uv - b->uv) - 1000;
	}
	return 0;
}

static int ump_ocv_to_pct(struct ump_fgu *f, int uv)
{
	int i;
	if (!f->ocv_count)
		return 0;
	if (uv >= f->ocv[0].uv)
		return f->ocv[0].pct;
	for (i = 1; i < f->ocv_count; i++) {
		if (uv > f->ocv[i].uv) {
			struct ump_ocv_point *a = &f->ocv[i - 1], *b = &f->ocv[i];
			return b->pct + div_s64((s64)(a->pct - b->pct) * (uv - b->uv), a->uv - b->uv);
		}
	}
	return f->ocv[f->ocv_count - 1].pct;
}

static int ump_voltage_uv(struct ump_fgu *f, int *uv)
{
	unsigned int raw;
	int ret = ump_read(f, FGU_VOLTAGE, &raw);
	if (!ret)
		*uv = ump_round((s64)raw * 1000000, f->voltage_gain);
	return ret;
}

/* Battery voltage minus the drop over the internal resistance (positive current = charging). */
static int ump_ocv_uv(struct ump_fgu *f, int uv, int ua)
{
	return uv - div_s64((s64)ua * f->r_uohm, 1000000);
}

static void ump_soc_clamp(struct ump_fgu *f)
{
	if (f->charge_uah < 0)
		f->charge_uah = 0;
	if (f->charge_uah > f->full_uah)
		f->charge_uah = f->full_uah;
}

static void ump_soc_init(struct ump_fgu *f)
{
	int uv, ua = 0, pct;
	if (!f->ocv_count || !f->full_uah || ump_voltage_uv(f, &uv))
		return;
	if (ump_current_ua(f, &ua))
		ua = 0;
	pct = ump_ocv_to_pct(f, ump_ocv_uv(f, uv, ua));
	f->charge_uah = div_s64((s64)f->full_uah * pct, 100);
	ump_soc_clamp(f);
	f->last = ktime_get();
	f->soc_ready = true;
}

static void ump_cancel_soc(void *work)
{
	cancel_delayed_work_sync(work);
}

static void ump_soc_work(struct work_struct *work)
{
	struct ump_fgu *f = container_of(work, struct ump_fgu, soc_work.work);
	ktime_t now = ktime_get();
	s64 dt_ms = ktime_ms_delta(now, f->last);
	int uv, ua, ret;

	mutex_lock(&f->lock);
	if (!f->soc_ready) {
		ump_soc_init(f);
		goto out;
	}
	ret = ump_voltage_uv(f, &uv);
	if (!ret)
		ret = ump_current_ua(f, &ua);
	if (ret)
		goto out;
	f->last = now;
	f->charge_uah += div_s64((s64)ua * dt_ms, 3600000);
	if (abs(ua) < UMP_RELAX_UA) {
		f->relax_ms += dt_ms;
		if (f->relax_ms >= UMP_RELAX_SYNC_MS) {
			/* relaxed: pull the counter 1/4 of the way to the OCV estimate each period */
			s64 target = div_s64((s64)f->full_uah * ump_ocv_to_pct(f, ump_ocv_uv(f, uv, ua)), 100);
			f->charge_uah += (target - f->charge_uah) / 4;
		}
	} else {
		f->relax_ms = 0;
	}
	if (ua > 0 && ua < f->full_ua && uv >= f->full_uv)
		f->charge_uah = f->full_uah;
	if (uv < 3300000 && ua <= 0)
		f->charge_uah = min_t(s64, f->charge_uah, div_s64(f->full_uah, 100));
	ump_soc_clamp(f);
out:
	mutex_unlock(&f->lock);
	schedule_delayed_work(&f->soc_work, msecs_to_jiffies(UMP_SOC_PERIOD_MS));
}

static enum power_supply_property ump_properties[] = {
	POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_CURRENT_NOW,
	POWER_SUPPLY_PROP_TEMP,
	POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN,
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_CAPACITY_LEVEL,
	POWER_SUPPLY_PROP_CHARGE_FULL,
	POWER_SUPPLY_PROP_CHARGE_NOW,
	POWER_SUPPLY_PROP_VOLTAGE_OCV,
	POWER_SUPPLY_PROP_PRESENT,
};

static int ump_get_property(struct power_supply *psy, enum power_supply_property prop,
			    union power_supply_propval *val)
{
	struct ump_fgu *f = power_supply_get_drvdata(psy);
	unsigned int raw;
	int ret = 0;
	mutex_lock(&f->lock);
	switch (prop) {
	case POWER_SUPPLY_PROP_VOLTAGE_NOW:
		ret = ump_read(f, FGU_VOLTAGE, &raw);
		if (!ret)
			val->intval = ump_round((s64)raw * 1000000, f->voltage_gain);
		break;
	case POWER_SUPPLY_PROP_CURRENT_NOW:
		ret = ump_current_ua(f, &val->intval);
		break;
	case POWER_SUPPLY_PROP_TEMP:
		ret = ump_temperature(f, &val->intval);
		break;
	case POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN:
		val->intval = f->design_uah;
		break;
	case POWER_SUPPLY_PROP_PRESENT:
		val->intval = 1;
		break;
	case POWER_SUPPLY_PROP_CAPACITY:
	case POWER_SUPPLY_PROP_CAPACITY_LEVEL:
		if (!f->soc_ready || !f->full_uah) {
			ret = -ENODATA;
			break;
		}
		{
			int pct = div_s64((s64)f->charge_uah * 100 + f->full_uah / 2, f->full_uah);
			if (prop == POWER_SUPPLY_PROP_CAPACITY)
				val->intval = pct;
			else if (pct >= 100)
				val->intval = POWER_SUPPLY_CAPACITY_LEVEL_FULL;
			else if (pct >= 80)
				val->intval = POWER_SUPPLY_CAPACITY_LEVEL_HIGH;
			else if (pct >= 20)
				val->intval = POWER_SUPPLY_CAPACITY_LEVEL_NORMAL;
			else if (pct >= 5)
				val->intval = POWER_SUPPLY_CAPACITY_LEVEL_LOW;
			else
				val->intval = POWER_SUPPLY_CAPACITY_LEVEL_CRITICAL;
		}
		break;
	case POWER_SUPPLY_PROP_CHARGE_FULL:
		val->intval = f->full_uah;
		break;
	case POWER_SUPPLY_PROP_CHARGE_NOW:
		if (!f->soc_ready) {
			ret = -ENODATA;
			break;
		}
		val->intval = f->charge_uah;
		break;
	case POWER_SUPPLY_PROP_VOLTAGE_OCV: {
		int uv, ua;
		ret = ump_voltage_uv(f, &uv);
		if (!ret)
			ret = ump_current_ua(f, &ua);
		if (!ret)
			val->intval = ump_ocv_uv(f, uv, ua);
		break;
	}
	default:
		ret = -EINVAL;
	}
	mutex_unlock(&f->lock);
	return ret;
}

static const struct power_supply_desc ump_psy_desc = {
	.name = "u30pro-battery",
	.type = POWER_SUPPLY_TYPE_BATTERY,
	.properties = ump_properties,
	.num_properties = ARRAY_SIZE(ump_properties),
	.get_property = ump_get_property,
};

static int ump_calibration(struct ump_fgu *f)
{
	static const char * const names[] = { "fgu_calib1", "fgu_calib2", "fgu_calib3" };
	struct nvmem_cell *cell;
	void *data;
	size_t len;
	int i, zero, span, voltage;
	for (i = 0; i < 3; i++) {
		cell = devm_nvmem_cell_get(f->dev, names[i]);
		if (IS_ERR(cell))
			return PTR_ERR(cell);
		data = nvmem_cell_read(cell, &len);
		if (IS_ERR(data))
			return PTR_ERR(data);
		if (len != 2) {
			kfree(data);
			return -EINVAL;
		}
		f->efuse[i] = ((u8 *)data)[0] | ((u8 *)data)[1] << 8;
		kfree(data);
	}
	zero = ((f->efuse[0] >> 4) & 0xfff) + 65536 - 256;
	span = f->efuse[1] + 104875 - 4096 - zero;
	voltage = ((f->efuse[2] >> 7) & 0x1ff) + 6963 - 4096 - 256;
	if (span <= 0 || voltage <= 0)
		return -EINVAL;
	f->current_gain = ump_round((s64)span * f->sense_uohm, 42000);
	f->voltage_gain = DIV_ROUND_CLOSEST(voltage * 10, 42);
	return f->current_gain > 0 && f->voltage_gain > 0 ? 0 : -EINVAL;
}

/* Real stock B logs prove the resistor-ID path, not GPIO_BATID_CHECK. */
static int ump_battery_profile(struct ump_fgu *f)
{
	struct device_node *chosen, *first, *battery;
	const char *cmdline, *id;
	char text[16], name[32];
	u32 pullup = 475, ref = 1850, target, v, t;
	int i, len, ret, resistance, count, match = -1;
	chosen = of_find_node_by_path("/chosen");
	if (!chosen)
		return -EINVAL;
	ret = of_property_read_string(chosen, "bootargs", &cmdline);
	of_node_put(chosen);
	if (ret)
		return ret;
	id = cmdline;
	while ((id = strstr(id, "bat.id="))) {
		if (id == cmdline || id[-1] == ' ')
			break;
		id++;
	}
	if (!id)
		return -ENODATA;
	id += strlen("bat.id=");
	len = strcspn(id, " \n\t");
	if (!len || len >= sizeof(text))
		return -EINVAL;
	memcpy(text, id, len); text[len] = 0;
	ret = kstrtoint(text, 10, &f->battery_id_mv);
	if (ret)
		return ret;
	first = of_parse_phandle(f->dev->of_node, "monitored-battery-0", 0);
	if (!first)
		return -EINVAL;
	of_property_read_u32(first, "batt-id-pullup-kohm", &pullup);
	of_property_read_u32(first, "batt-id-ref-vol", &ref);
	if (ref > 5000 || pullup > 10000 || f->battery_id_mv < 0 || f->battery_id_mv >= ref) {
		ret = -EINVAL;
		goto put_first;
	}
	resistance = DIV_ROUND_CLOSEST(f->battery_id_mv * pullup, ref - f->battery_id_mv);
	count = of_property_count_u32_elems(first, "batt-id-list");
	if (count <= 0 || count > 16) {
		ret = -EINVAL;
		goto put_first;
	}
	for (i = 0; i < count; i++) {
		ret = of_property_read_u32_index(first, "batt-id-list", i, &target);
		if (ret)
			goto put_first;
		if ((resistance < 10 && abs(resistance - (int)target) < 5) ||
		    (resistance >= target * 80 / 100 && resistance <= target * 120 / 100)) {
			match = i;
			break;
		}
	}
	ret = match < 0 ? -ENODATA : 0;
put_first:
	of_node_put(first);
	if (ret)
		return ret;
	f->battery_index = match;
	snprintf(name, sizeof(name), "monitored-battery-%d", match);
	battery = of_parse_phandle(f->dev->of_node, name, 0);
	if (!battery)
		return -EINVAL;
	ret = of_property_read_u32(battery, "charge-full-design-microamp-hours", &f->design_uah);
	if (ret || !f->design_uah || f->design_uah > 20000000) {
		ret = -EINVAL;
		goto put_battery;
	}
	count = of_property_count_u32_elems(battery, "voltage-temp-table");
	if (count < 4 || count > 1024 || count % 2) {
		ret = -EINVAL;
		goto put_battery;
	}
	f->ntc_count = count / 2;
	f->ntc_table = devm_kcalloc(f->dev, f->ntc_count, sizeof(*f->ntc_table), GFP_KERNEL);
	if (!f->ntc_table) {
		ret = -ENOMEM;
		goto put_battery;
	}
	for (i = 0; i < f->ntc_count; i++) {
		ret = of_property_read_u32_index(battery, "voltage-temp-table", i * 2, &v);
		if (ret)
			goto put_battery;
		ret = of_property_read_u32_index(battery, "voltage-temp-table", i * 2 + 1, &t);
		if (ret)
			goto put_battery;
		if (v > 1875000 || t > 2000 || (i && (v >= f->ntc_table[i-1].uv || t <= f->ntc_table[i-1].temp))) {
			ret = -EINVAL;
			goto put_battery;
		}
		f->ntc_table[i].uv = v; f->ntc_table[i].temp = t;
	}
	/* state-of-charge data (stock simple-battery node, 20 C table) */
	of_property_read_u32(battery, "charge-full-microamp-hours", &f->full_uah);
	if (!f->full_uah)
		f->full_uah = f->design_uah;
	f->r_uohm = 160000;
	of_property_read_u32(battery, "factory-internal-resistance-micro-ohms", &f->r_uohm);
	f->full_uv = 4300000;
	of_property_read_u32(battery, "fullbatt-voltage", &f->full_uv);
	f->full_ua = 120000;
	of_property_read_u32(battery, "fullbatt-current", &f->full_ua);
	count = of_property_count_u32_elems(battery, "ocv-capacity-table-0");
	if (count >= 4 && count <= 256 && !(count % 2)) {
		f->ocv = devm_kcalloc(f->dev, count / 2, sizeof(*f->ocv), GFP_KERNEL);
		if (!f->ocv) {
			ret = -ENOMEM;
			goto put_battery;
		}
		for (i = 0; i < count / 2; i++) {
			if (of_property_read_u32_index(battery, "ocv-capacity-table-0", i * 2, &v) ||
			    of_property_read_u32_index(battery, "ocv-capacity-table-0", i * 2 + 1, &t) ||
			    t > 100 || (i && v >= f->ocv[i - 1].uv)) {
				f->ocv_count = 0;
				break;
			}
			f->ocv[i].uv = v;
			f->ocv[i].pct = t;
			f->ocv_count = i + 1;
		}
	}
	ret = 0;
put_battery:
	of_node_put(battery);
	return ret;
}

static ssize_t native_state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct ump_fgu *f = dev_get_drvdata(dev);
	u64 count;
	u32 voltage, raw_current, high, low, status, user;
	int ret;
	mutex_lock(&f->lock);
	ret = ump_read(f, FGU_VOLTAGE, &voltage);
	if (!ret) ret = ump_current_raw(f, &raw_current);
	if (!ret) ret = ump_count_raw(f, &count);
	if (!ret) ret = ump_read(f, FGU_OFFSET_H, &high);
	if (!ret) ret = ump_read(f, FGU_OFFSET_L, &low);
	if (!ret) ret = ump_read(f, FGU_STATUS, &status);
	if (!ret) ret = ump_read(f, FGU_USER_STATUS, &user);
	mutex_unlock(&f->lock);
	if (ret)
		return ret;
	return sysfs_emit(buf,
		"factory=%04x,%04x,%04x sense_uohm=%u voltage_gain=%d current_gain=%d "
		"battery_id_mv=%d profile=%d raw_voltage=%u raw_current=%u count36=%llu "
		"current_offset=%05x previous_offset=%05x zero_point=%d status=%04x retained_user=%04x SOC_ready=%d charge_uah=%lld zero_calibration=measured\n",
		f->efuse[0], f->efuse[1], f->efuse[2], f->sense_uohm, f->voltage_gain, f->current_gain,
		f->battery_id_mv, f->battery_index, voltage, raw_current, count,
		((high & 0x1f) << 16) | (low & 0xffff), f->previous_offset, f->zero_point, status, user,
		f->soc_ready, f->charge_uah);
}
static DEVICE_ATTR_RO(native_state);
static struct attribute *ump_attrs[] = { &dev_attr_native_state.attr, NULL };
static const struct attribute_group ump_attr_group = { .attrs = ump_attrs };

static int ump_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct power_supply_config config = {};
	struct ump_fgu *f;
	int ret;
	f = devm_kzalloc(dev, sizeof(*f), GFP_KERNEL);
	if (!f)
		return -ENOMEM;
	f->dev = dev;
	f->map = dev_get_regmap(dev->parent, NULL);
	if (!f->map)
		return -ENODEV;
	mutex_init(&f->lock);
	platform_set_drvdata(pdev, f);
	ret = of_property_read_u32(dev->of_node, "reg", &f->base);
	if (ret)
		return ret;
	ret = of_property_read_u32(dev->of_node, "sprd,calib-resistance-micro-ohms", &f->sense_uohm);
	if (ret || f->sense_uohm < 100 || f->sense_uohm > 10000)
		return -EINVAL;
	of_property_read_u32(dev->of_node, "sprd,comp-resistance-mohm", &f->comp_mohm);
	if (f->comp_mohm > 1000)
		return -EINVAL;
	ret = ump_calibration(f);
	if (ret)
		return dev_err_probe(dev, ret, "factory calibration unavailable\n");
	ret = ump_battery_profile(f);
	if (ret)
		return dev_err_probe(dev, ret, "battery profile unavailable\n");
	f->ntc = devm_iio_channel_get(dev, "bat-temp");
	if (IS_ERR(f->ntc))
		return dev_err_probe(dev, PTR_ERR(f->ntc), "NTC ADC unavailable\n");
	f->charge = devm_iio_channel_get(dev, "charge-vol");
	if (IS_ERR(f->charge))
		return dev_err_probe(dev, PTR_ERR(f->charge), "input ADC unavailable\n");
	/* Volatile FGU enable and measured current zero; no count, USER or NV writes. */
	ret = regmap_update_bits(f->map, FGU_MODULE_EN, BIT(7), BIT(7));
	if (ret)
		return ret;
	ret = regmap_update_bits(f->map, FGU_CLOCK_EN, BIT(6), BIT(6));
	if (ret)
		return ret;
	msleep(510);
	ret = ump_calibrate_zero(f);
	if (ret)
		return dev_err_probe(dev, ret, "current ADC zero calibration failed\n");
	config.drv_data = f;
	config.of_node = dev->of_node;
	f->psy = devm_power_supply_register(dev, &ump_psy_desc, &config);
	if (IS_ERR(f->psy))
		return dev_err_probe(dev, PTR_ERR(f->psy), "power supply registration failed\n");
	ret = devm_device_add_group(dev, &ump_attr_group);
	if (ret)
		return ret;
	INIT_DELAYED_WORK(&f->soc_work, ump_soc_work);
	ret = devm_add_action_or_reset(dev, ump_cancel_soc, &f->soc_work);
	if (ret)
		return ret;
	mutex_lock(&f->lock);
	ump_soc_init(f);
	mutex_unlock(&f->lock);
	schedule_delayed_work(&f->soc_work, msecs_to_jiffies(UMP_SOC_PERIOD_MS));
	dev_info(dev, "native FGU profile=%d ID=%dmV gain V=%d I=%d; SOC %s (full %u uAh, R %u uohm)\n",
		 f->battery_index, f->battery_id_mv, f->voltage_gain, f->current_gain,
		 f->soc_ready ? "estimated" : "unavailable", f->full_uah, f->r_uohm);
	return 0;
}

static const struct of_device_id ump_of_match[] = {
	{ .compatible = "sprd,ump965x-fgu" },
	{ }
};
MODULE_DEVICE_TABLE(of, ump_of_match);
static struct platform_driver ump_driver = {
	.probe = ump_probe,
	.driver = { .name = "u30pro-ump965x-fgu", .of_match_table = ump_of_match },
};
module_platform_driver(ump_driver);
MODULE_DESCRIPTION("Native UMP965x calibrated battery measurements for U30 Pro");
MODULE_LICENSE("GPL");
