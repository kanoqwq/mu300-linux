// SPDX-License-Identifier: GPL-2.0
/*
 * The ZTE U30 Air's white LEDs: not GPIOs but three PMIC LDOs (the camera supplies VDDCAMA0-2, 3.3 V), which
 * ZTE's zte_ldo_leds switches under Android. Without a driver the bootloader leaves them on and
 * regulator_ignore_unused keeps them that way, so the network, Wi-Fi and power lights never went out under
 * mainline. Each supply becomes an LED class device (zte-ldo0..2): on is the LDO enabled at 3.3 V, off disabled.
 * They start dark; mu300-led drives them.
 */
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regulator/consumer.h>

#define ZTE_LDO_LEDS	3
#define ZTE_LDO_UV	3300000

struct zte_ldo_led {
	struct led_classdev cdev;
	struct regulator *supply;
	bool on;
};

static int zte_ldo_led_set(struct led_classdev *cdev, enum led_brightness value)
{
	struct zte_ldo_led *led = container_of(cdev, struct zte_ldo_led, cdev);
	int ret = 0;

	if (value && !led->on) {
		regulator_set_voltage(led->supply, ZTE_LDO_UV, ZTE_LDO_UV);
		ret = regulator_enable(led->supply);
		if (!ret)
			led->on = true;
	} else if (!value && led->on) {
		ret = regulator_disable(led->supply);
		if (!ret)
			led->on = false;
	}
	return ret;
}

static int zte_ldo_leds_probe(struct platform_device *pdev)
{
	static const char * const supplies[ZTE_LDO_LEDS] = { "vddcama0", "vddcama1", "vddcama2" };
	struct device *dev = &pdev->dev;
	struct zte_ldo_led *leds;
	int i, ret, found = 0;

	leds = devm_kcalloc(dev, ZTE_LDO_LEDS, sizeof(*leds), GFP_KERNEL);
	if (!leds)
		return -ENOMEM;

	for (i = 0; i < ZTE_LDO_LEDS; i++) {
		struct zte_ldo_led *led = &leds[i];

		led->supply = devm_regulator_get_optional(dev, supplies[i]);
		if (IS_ERR(led->supply)) {
			if (PTR_ERR(led->supply) == -EPROBE_DEFER)
				return -EPROBE_DEFER;
			continue;
		}
		/*
		 * The bootloader left the LDO on, with no consumer: enabling and disabling it once makes it ours
		 * and turns it off (use count back to 0), which a plain disable would refuse as unbalanced.
		 */
		regulator_set_voltage(led->supply, ZTE_LDO_UV, ZTE_LDO_UV);
		if (!regulator_enable(led->supply))
			regulator_disable(led->supply);

		led->cdev.name = devm_kasprintf(dev, GFP_KERNEL, "zte-ldo%d", i);
		led->cdev.max_brightness = 1;
		led->cdev.brightness_set_blocking = zte_ldo_led_set;
		ret = devm_led_classdev_register(dev, &led->cdev);
		if (ret)
			return dev_err_probe(dev, ret, "cannot register %s\n", led->cdev.name);
		found++;
	}
	if (!found)
		return dev_err_probe(dev, -ENODEV, "no LED supply\n");
	return 0;
}

static const struct of_device_id zte_ldo_leds_of_match[] = {
	{ .compatible = "zte-ldo-leds" },
	{ }
};
MODULE_DEVICE_TABLE(of, zte_ldo_leds_of_match);

static struct platform_driver zte_ldo_leds_driver = {
	.probe = zte_ldo_leds_probe,
	.driver = {
		.name = "zte-ldo-leds",
		.of_match_table = zte_ldo_leds_of_match,
	},
};
module_platform_driver(zte_ldo_leds_driver);

MODULE_DESCRIPTION("ZTE U30 Air LEDs on PMIC LDOs");
MODULE_LICENSE("GPL");
