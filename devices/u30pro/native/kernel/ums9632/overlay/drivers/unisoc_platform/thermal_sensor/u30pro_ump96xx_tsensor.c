// SPDX-License-Identifier: GPL-2.0
/*
 * UMP96xx PMIC TSX crystal temperature for the modem (port of the vendor sprd_ump96xx_tsensor.c, normal-boot path only).
 *
 * The stock RIL reads /sys/bus/platform/drivers/ump96xx-tsensor/modem_tsen (m°„C) on boards whose command line says crystal=7
 * (TSX) and sends it to the CP with AT+SPTSXTMP before every protocol stack start.  Each read powers the PMIC temperature
 * sensor on, samples the TSX thermistor channel and powers it off again, as the vendor driver does.  The calibration-mode
 * thermal zones and the GNSS hook of the vendor driver are left out.  Fixed against the vendor code: the lock is not left held
 * when enabling the sensor fails, and the reply is not cut to sizeof(pointer) bytes.
 *
 * Copyright (C) 2021 Spreadtrum Communications Inc.
 */

#include <linux/bits.h>
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/sysfs.h>

#define UMP96XX_XTL_WAIT_CTRL0		0x20e0
#define UMP96XX_XTL_EN			BIT(8)

#define UMP96XX_TSEN_CTRL0		0x0
#define UMP96XX_TSEN_CLK_SRC_SEL	BIT(4)
#define UMP96XX_TSEN_CTRL1		0x4
#define UMP96XX_TSEN_26M_EN		BIT(0)
#define UMP96XX_TSEN_SDADC_EN		BIT(4)
#define UMP96XX_TSEN_CTRL3		0xc
#define UMP96XX_TSEN_SEL_CH		BIT(3)
#define UMP96XX_TSEN_EN			BIT(4)
#define UMP96XX_TSEN_UGBUF_EN		BIT(8)
#define UMP96XX_TSEN_ADCLDO_EN		BIT(12)
#define UMP96XX_TSEN_CTRL4		0x10
#define UMP96XX_TSEN_CTRL6		0x18
#define UMP96XX_TSEN_SEL_EN		(BIT(6) | BIT(7))

#define UMP96XX_TSEN_TSEN_TEMP(h, l) \
	(((((h & GENMASK(2, 0)) << 16) | (l & GENMASK(15, 0))) << 4) & 0x7FFFFF)
#define UMP96XX_TSEN_INDEX_MASK		GENMASK(7, 0)
#define UMP96XX_TSEN_INDEX_SHIFT	12
#define UMP96XX_TSEN_FRAC_MASK		GENMASK(11, 0)
#define ABNORMAL_TEMP			97697

/* temperature by integral index; crystal TSX, maker TXC */
static int v2t_table_0[256] = {
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119, 298119,
	298119, 298119, 298119, 298119, 280421, 264933, 251238, 238928, 227748, 217492, 208038,
	199268, 191078, 183383, 176104, 169240, 162710, 156500, 150570, 144893, 139428, 134191,
	129138, 124253, 119525, 114943, 110500, 106187, 101990, 97905, 93919, 90026, 86224, 82506,
	78860, 75286, 71777, 68330, 64940, 61606, 58337, 55113, 51932, 48790, 45696, 42642, 39620,
	36627, 33675, 30750, 27848, 24974, 22128, 19300, 16490, 13707, 10937, 8180, 5444, 2717, 0,
	-2702, -5398, -8088, -10768, -13446, -16120, -18791, -21462, -24132, -26805, -29481, -32160,
	-34847, -37539, -40241, -42956, -45680, -48419, -51175, -53944, -56734, -59548, -62379,
	-65234, -68124, -71039, -73979, -76964, -79985, -83041, -86136, -89290, -92493, -95747,
	-99057, -102428, -105886, -109418, -113031, -116733, -120533, -124438,  -128458, -132609,
	-136903, -141357, -145991, -150823, -155881, -161216, -166830, -172781, -179140, -185969,
	-193354, -201441, -210423, -220528, -232181, -245984, -263191, -263191, -263191
};

/* temperature by integral index; crystal TSX (the device tree of this board selects table 1) */
static int v2t_table_1[256] = {
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768, 297768,
	297768, 297768, 297768, 297768, 280022, 264541, 250827, 238540, 227437, 217248, 207794,
	199075, 190918, 183219, 175974, 169165, 162632, 156430, 150512, 144824, 139369, 134151,
	129116, 124240, 119517, 114923, 110488, 106189, 101977, 97891, 93912, 90023, 86216, 82497,
	78868, 75287, 71782, 68342, 64943, 61612, 58346, 55122, 51944, 48810, 45706, 42657, 39637,
	36640, 33683, 30762, 27866, 24991, 22141, 19308, 16495, 13716, 10947, 8187, 5448, 2719, 0,
	-2676, -5364, -8063, -10745, -13423, -16096, -18774, -21457, -24149, -26796, -29446, -32123,
	-34809, -37502, -40214, -42926, -45643, -48372, -51122, -53890, -56690, -59510, -62348,
	-65214, -68106, -71023, -73966, -76944, -79974, -83043, -86145, -89309, -92532, -95805,
	-99133, -102537, -106000, -109565, -113208, -116941, -120778, -124722, -128784, -132989,
	-137339, -141848, -146539, -151424, -156567, -161964, -167642, -173677, -180078, -187046,
	-194557, -202751, -211799, -222049, -233806, -247837, -265250, -265250, -265250
};

static struct {
	struct regmap *regmap;
	u32 base;
	int table_id;
} tsen;
static DEFINE_MUTEX(tsen_mutex);

static int tsen_on(struct regmap *r, u32 base)
{
	static const struct { u32 reg; u32 bits; } seq[] = {
		{ UMP96XX_TSEN_CTRL0, UMP96XX_TSEN_CLK_SRC_SEL },
		{ UMP96XX_TSEN_CTRL1, UMP96XX_TSEN_26M_EN },
		{ UMP96XX_TSEN_CTRL3, UMP96XX_TSEN_ADCLDO_EN },
		{ UMP96XX_TSEN_CTRL1, UMP96XX_TSEN_SDADC_EN },
		{ UMP96XX_TSEN_CTRL3, UMP96XX_TSEN_UGBUF_EN },
		{ UMP96XX_TSEN_CTRL3, UMP96XX_TSEN_EN },
	};
	int i, ret;

	/* the crystal wait control is a global PMIC register, not relative to the sensor block */
	ret = regmap_update_bits(r, UMP96XX_XTL_WAIT_CTRL0, UMP96XX_XTL_EN, UMP96XX_XTL_EN);
	for (i = 0; !ret && i < ARRAY_SIZE(seq); i++)
		ret = regmap_update_bits(r, base + seq[i].reg, seq[i].bits, seq[i].bits);
	if (!ret)
		ret = regmap_update_bits(r, base + UMP96XX_TSEN_CTRL6, UMP96XX_TSEN_SEL_EN, 0);
	return ret;
}

static void tsen_off(struct regmap *r, u32 base)
{
	regmap_update_bits(r, base + UMP96XX_TSEN_CTRL0, UMP96XX_TSEN_CLK_SRC_SEL, 0);
	regmap_update_bits(r, base + UMP96XX_TSEN_CTRL3, UMP96XX_TSEN_EN | UMP96XX_TSEN_SEL_CH, 0);
	regmap_update_bits(r, base + UMP96XX_TSEN_CTRL6, UMP96XX_TSEN_SEL_EN, 0);
	regmap_update_bits(r, base + UMP96XX_TSEN_CTRL1, UMP96XX_TSEN_SDADC_EN, 0);
	regmap_update_bits(r, base + UMP96XX_TSEN_CTRL3, UMP96XX_TSEN_UGBUF_EN, 0);
	regmap_update_bits(r, base + UMP96XX_TSEN_CTRL3, UMP96XX_TSEN_ADCLDO_EN, 0);
}

static int tsx_temp(const int *table, int n, int raw)
{
	int index = (raw >> UMP96XX_TSEN_INDEX_SHIFT) & UMP96XX_TSEN_INDEX_MASK;
	int frac = raw & UMP96XX_TSEN_FRAC_MASK;
	int t;

	/* interpolate between the two table entries, then out = ((t * 1000) >> 12) + 25000 (design document) */
	if (index != n - 1)
		t = (table[index] * (0x1000 - frac) + table[index + 1] * frac + 0x800) >> 12;
	else
		t = table[index];
	return ((t * 1000) >> 12) + 25000;
}

static int tsen_read(int *temp)
{
	const int *table = tsen.table_id ? v2t_table_1 : v2t_table_0;
	struct regmap *r = tsen.regmap;
	u32 base = tsen.base;
	int ret, i, tl, th;

	mutex_lock(&tsen_mutex);
	ret = tsen_on(r, base);
	if (!ret)
		ret = regmap_update_bits(r, base + UMP96XX_TSEN_CTRL6, UMP96XX_TSEN_SEL_EN, UMP96XX_TSEN_SEL_EN);
	if (!ret)
		ret = regmap_update_bits(r, base + UMP96XX_TSEN_CTRL3, UMP96XX_TSEN_SEL_CH, 0);
	*temp = ABNORMAL_TEMP;
	for (i = 0; !ret && i < 3 && *temp >= ABNORMAL_TEMP; i++) {
		msleep(20);
		ret = regmap_read(r, base + UMP96XX_TSEN_CTRL4, &tl);
		if (!ret)
			ret = regmap_read(r, base + UMP96XX_TSEN_CTRL6, &th);
		if (!ret)
			*temp = tsx_temp(table, 256, UMP96XX_TSEN_TSEN_TEMP(th, tl));
	}
	tsen_off(r, base);
	mutex_unlock(&tsen_mutex);
	return ret;
}

static ssize_t modem_tsen_show(struct device_driver *drv, char *buf)
{
	int temp, ret = tsen_read(&temp);

	return ret ? ret : sysfs_emit(buf, "%d\n", temp);
}
static DRIVER_ATTR_RO(modem_tsen);

static struct attribute *tsen_drv_attrs[] = { &driver_attr_modem_tsen.attr, NULL };
ATTRIBUTE_GROUPS(tsen_drv);

static int ump96xx_tsen_probe(struct platform_device *pdev)
{
	struct device_node *np = pdev->dev.of_node;
	u32 id = 1;

	tsen.regmap = dev_get_regmap(pdev->dev.parent, NULL);
	if (!tsen.regmap)
		return dev_err_probe(&pdev->dev, -ENODEV, "no PMIC regmap\n");
	if (of_property_read_u32(np, "reg", &tsen.base))
		return dev_err_probe(&pdev->dev, -EINVAL, "no reg\n");
	of_property_read_u32(np, "v2t_table_id", &id);
	tsen.table_id = id;
	dev_info(&pdev->dev, "TSX temperature at modem_tsen (base 0x%x, table %u)\n", tsen.base, id);
	return 0;
}

static const struct of_device_id ump96xx_tsen_of_match[] = {
	{ .compatible = "sprd,ump96xx-tsensor" },
	{ }
};
MODULE_DEVICE_TABLE(of, ump96xx_tsen_of_match);

static struct platform_driver ump96xx_tsen_driver = {
	.probe = ump96xx_tsen_probe,
	.driver = {
		.name = "ump96xx-tsensor",	/* the stock RIL reads /sys/bus/platform/drivers/ump96xx-tsensor/modem_tsen */
		.of_match_table = ump96xx_tsen_of_match,
		.groups = tsen_drv_groups,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(ump96xx_tsen_driver);

MODULE_DESCRIPTION("UMP96xx PMIC TSX temperature for the modem");
MODULE_LICENSE("GPL");
