// SPDX-License-Identifier: GPL-2.0
/*
 * Shell (case surface) temperature estimators of the UMS9632 board ("sprd,shell-thermal").
 *
 * The device has no sensor on the case.  The stock device tree describes four virtual sensors (frame, front, back, bkchg) that estimate the
 * temperature of a place on the case from the seven board NTC zones (wcn, usb, chg, pa5g, pa, board, ambient: the "sensor-names"
 * property): every 5 s each NTC temperature is stored in a ring of ntemp samples and
 *
 *	temp = sum over the sensors i and the ring j (oldest first) of (coeff[i][j] * temp[i][j]) / 10000 + const
 *
 * with coeff = temp-coeff - coeff-offset and const = temp-const - const-offset (all temperatures in millidegrees).  A step larger than
 * virt-temp-diff between two estimates and a step larger than temp-diff[i] between two readings of NTC i is clipped.  While the ring is
 * filling (the first ntemp periods and after a resume) the board NTC temperature is reported instead.
 *
 * Based on the vendor driver sprd_shell_thm.c (GPL-2.0, Unisoc).  Differences: the NTC zones are read with thermal_zone_get_temp() (the
 * vendor driver calls the zone ops directly), the failure counter is per sensor, no fixed-size stack buffer, a zone that has no estimate
 * yet reports -ENODATA, and the zones get hwmon entries.
 *
 * Locking: get_temp() only returns the cached estimate; the NTC zones are looked up and read by the work item, which holds no zone lock.
 */
#define pr_fmt(fmt) "sprd-shell-thm: " fmt

#include <linux/err.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm.h>
#include <linux/slab.h>
#include <linux/thermal.h>
#include <linux/workqueue.h>

#include "../../thermal/thermal_hwmon.h"

#define SHELL_THM_PERIOD_MS	5000
#define SHELL_THM_DEF_ZONE	"board-thmzone"
#define SHELL_THM_ABNORMAL	120000
#define SHELL_THM_INIT_TEMP	25000
#define SHELL_THM_READ_TRIES	3
#define SHELL_THM_DEF_NTC_DIFF	250
#define SHELL_THM_DEF_VIRT_DIFF	200

struct shell_sensor {
	struct device *dev;
	struct thermal_zone_device *tz;

	size_t nsensor;
	size_t ntemp;
	const char **sensor_names;
	struct thermal_zone_device **ntc;	/* the NTC zones, found by the work item */
	int *coeff;				/* nsensor x ntemp */
	int *hty_temp;				/* nsensor x ntemp */
	int *ntc_temp_diff;
	int const_temp;
	int virt_temp_diff;

	int index;
	bool init_flag;				/* the ring is filling: report the board NTC */
	int last_temp;
	int cur_temp;
	bool have_temp;
	struct thermal_zone_device *board;
	struct delayed_work work;
};

#define COEFF(s, i, j)	((s)->coeff[(i) * (s)->ntemp + (j)])
#define HTY(s, i, j)	((s)->hty_temp[(i) * (s)->ntemp + (j)])

static int shell_virt_temp(struct shell_sensor *s, int index)
{
	long long sum = 0;
	int i, j, temp;

	for (i = 0; i < s->nsensor; i++) {
		for (j = 0; j < s->ntemp; j++) {
			/* oldest sample first: index is the newest one */
			int k = (index + 1 + j) % s->ntemp;

			sum += (long long)COEFF(s, i, j) * HTY(s, i, k);
		}
	}
	temp = (int)div_s64(sum, 10000) + s->const_temp;

	if (s->last_temp != SHELL_THM_ABNORMAL && s->virt_temp_diff > 0) {
		int diff = temp - s->last_temp;

		if (abs(diff) > s->virt_temp_diff)
			temp = diff > 0 ? s->last_temp + s->virt_temp_diff : s->last_temp - s->virt_temp_diff;
	}
	s->last_temp = temp;

	return temp;
}

/* one period: read the NTC zones into the ring and update the estimate */
static void shell_update(struct shell_sensor *s)
{
	int index = s->index;
	int i, ret, temp = 0;

	for (i = 0; i < s->nsensor; i++) {
		int tries, val = 0;

		for (tries = 0; tries < SHELL_THM_READ_TRIES; tries++) {
			ret = thermal_zone_get_temp(s->ntc[i], &val);
			if (!ret && val < SHELL_THM_ABNORMAL)
				break;
		}
		if (tries == SHELL_THM_READ_TRIES) {
			pr_warn_ratelimited("reading %s failed\n", s->sensor_names[i]);
			goto fallback;
		}

		if (!s->init_flag && s->ntc_temp_diff) {
			int old = HTY(s, i, (index + s->ntemp - 1) % s->ntemp);
			int diff = val - old;

			if (abs(diff) > s->ntc_temp_diff[i])
				val = diff > 0 ? old + s->ntc_temp_diff[i] : old - s->ntc_temp_diff[i];
		}
		HTY(s, i, index) = val;
	}

	if (!s->init_flag) {
		temp = shell_virt_temp(s, index);
		goto done;
	}

fallback:
	ret = s->board ? thermal_zone_get_temp(s->board, &temp) : -ENODEV;
	if (ret || temp > SHELL_THM_ABNORMAL) {
		pr_warn_ratelimited("reading %s failed\n", SHELL_THM_DEF_ZONE);
		return;
	}

done:
	WRITE_ONCE(s->cur_temp, temp);
	WRITE_ONCE(s->have_temp, true);

	s->index = (index + 1) % s->ntemp;
	if (index == s->ntemp - 1)
		s->init_flag = false;
}

static void shell_work(struct work_struct *work)
{
	struct shell_sensor *s = container_of(to_delayed_work(work), struct shell_sensor, work);
	int i;

	/* the NTC zones appear when generic-adc-thermal has probed */
	for (i = 0; i < s->nsensor; i++) {
		if (s->ntc[i])
			continue;
		s->ntc[i] = thermal_zone_get_zone_by_name(s->sensor_names[i]);
		if (IS_ERR(s->ntc[i])) {
			s->ntc[i] = NULL;
			schedule_delayed_work(&s->work, msecs_to_jiffies(SHELL_THM_PERIOD_MS));
			return;
		}
	}
	if (!s->board) {
		s->board = thermal_zone_get_zone_by_name(SHELL_THM_DEF_ZONE);
		if (IS_ERR(s->board))
			s->board = NULL;
	}

	shell_update(s);
	schedule_delayed_work(&s->work, msecs_to_jiffies(SHELL_THM_PERIOD_MS));
}

static int shell_get_temp(struct thermal_zone_device *tz, int *temp)
{
	struct shell_sensor *s = thermal_zone_device_priv(tz);

	if (!READ_ONCE(s->have_temp))
		return -ENODATA;

	*temp = READ_ONCE(s->cur_temp);
	return 0;
}

static const struct thermal_zone_device_ops shell_ops = {
	.get_temp = shell_get_temp,
};

static int shell_parse_dt(struct device *dev, struct shell_sensor *s)
{
	struct device_node *np = dev->of_node;
	u32 coeff_offset, const_offset, tmp;
	u32 *raw;
	int count, i, ret;

	count = of_property_count_strings(np, "sensor-names");
	if (count <= 0) {
		dev_err(dev, "sensor names not found\n");
		return -EINVAL;
	}
	s->nsensor = count;

	s->sensor_names = devm_kcalloc(dev, count, sizeof(*s->sensor_names), GFP_KERNEL);
	s->ntc = devm_kcalloc(dev, count, sizeof(*s->ntc), GFP_KERNEL);
	s->ntc_temp_diff = devm_kcalloc(dev, count, sizeof(*s->ntc_temp_diff), GFP_KERNEL);
	if (!s->sensor_names || !s->ntc || !s->ntc_temp_diff)
		return -ENOMEM;

	ret = of_property_read_string_array(np, "sensor-names", s->sensor_names, count);
	if (ret < 0) {
		dev_err(dev, "cannot read sensor-names: %d\n", ret);
		return ret;
	}

	count = of_property_count_u32_elems(np, "temp-coeff");
	if (count <= 0 || count % s->nsensor) {
		dev_err(dev, "temp-coeff does not match %zu sensors\n", s->nsensor);
		return -EINVAL;
	}
	s->ntemp = count / s->nsensor;

	raw = kcalloc(count, sizeof(*raw), GFP_KERNEL);
	if (!raw)
		return -ENOMEM;
	ret = of_property_read_u32_array(np, "temp-coeff", raw, count);
	if (ret) {
		kfree(raw);
		dev_err(dev, "cannot read temp-coeff\n");
		return -EINVAL;
	}

	s->coeff = devm_kcalloc(dev, count, sizeof(*s->coeff), GFP_KERNEL);
	s->hty_temp = devm_kcalloc(dev, count, sizeof(*s->hty_temp), GFP_KERNEL);
	if (!s->coeff || !s->hty_temp) {
		kfree(raw);
		return -ENOMEM;
	}

	if (of_property_read_u32(np, "coeff-offset", &coeff_offset) ||
	    of_property_read_u32(np, "temp-const", &tmp) ||
	    of_property_read_u32(np, "const-offset", &const_offset)) {
		kfree(raw);
		dev_err(dev, "coeff-offset, temp-const or const-offset missing\n");
		return -EINVAL;
	}

	for (i = 0; i < count; i++) {
		s->coeff[i] = (int)raw[i] - (int)coeff_offset;
		s->hty_temp[i] = SHELL_THM_INIT_TEMP;
	}
	kfree(raw);
	s->const_temp = (int)tmp - (int)const_offset;

	if (of_property_read_u32(np, "virt-temp-diff", &tmp)) {
		dev_err(dev, "no virt-temp-diff, using %d\n", SHELL_THM_DEF_VIRT_DIFF);
		tmp = SHELL_THM_DEF_VIRT_DIFF;
	}
	s->virt_temp_diff = tmp;

	if (of_property_count_u32_elems(np, "temp-diff") != s->nsensor ||
	    of_property_read_u32_array(np, "temp-diff", (u32 *)s->ntc_temp_diff, s->nsensor)) {
		dev_err(dev, "no usable temp-diff, using %d\n", SHELL_THM_DEF_NTC_DIFF);
		for (i = 0; i < s->nsensor; i++)
			s->ntc_temp_diff[i] = SHELL_THM_DEF_NTC_DIFF;
	}

	return 0;
}

static void shell_stop(void *data)
{
	struct shell_sensor *s = data;

	cancel_delayed_work_sync(&s->work);
}

static int shell_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct shell_sensor *s;
	int ret;

	s = devm_kzalloc(dev, sizeof(*s), GFP_KERNEL);
	if (!s)
		return -ENOMEM;

	s->dev = dev;
	s->init_flag = true;
	s->last_temp = SHELL_THM_ABNORMAL;

	ret = shell_parse_dt(dev, s);
	if (ret)
		return ret;

	INIT_DELAYED_WORK(&s->work, shell_work);
	ret = devm_add_action_or_reset(dev, shell_stop, s);
	if (ret)
		return ret;

	s->tz = devm_thermal_of_zone_register(dev, 0, s, &shell_ops);
	if (IS_ERR(s->tz)) {
		ret = PTR_ERR(s->tz);
		dev_err(dev, "cannot register the thermal zone: %d\n", ret);
		return ret;
	}
	devm_thermal_add_hwmon_sysfs(dev, s->tz);

	platform_set_drvdata(pdev, s);
	schedule_delayed_work(&s->work, 0);

	dev_info(dev, "shell temperature estimator of %zu sensors x %zu samples registered\n", s->nsensor, s->ntemp);
	return 0;
}

/* after a resume the history is stale: start the ring again */
static int shell_resume(struct device *dev)
{
	struct shell_sensor *s = dev_get_drvdata(dev);

	s->index = 0;
	s->init_flag = true;
	s->last_temp = SHELL_THM_ABNORMAL;

	return 0;
}

static DEFINE_SIMPLE_DEV_PM_OPS(shell_pm_ops, NULL, shell_resume);

static const struct of_device_id shell_of_match[] = {
	{ .compatible = "sprd,shell-thermal" },
	{ /* sentinel */ },
};

static struct platform_driver shell_driver = {
	.probe = shell_probe,
	.driver = {
		.name = "u30pro-sprd-shell-thm",
		.of_match_table = shell_of_match,
		.pm = pm_sleep_ptr(&shell_pm_ops),
	},
};
module_platform_driver(shell_driver);

MODULE_DESCRIPTION("Unisoc shell temperature estimators");
MODULE_LICENSE("GPL");
