// SPDX-License-Identifier: GPL-2.0
/*
 * The PMIC's keypad backlight sink (UMP9620), after Unisoc's leds-sc27xx-keypad.c (current mode only). The ZTE
 * U30 Air has no keypad: the sink drives the red of its network LED (no service), which ZTE's firmware blinks
 * through the same "keyboard-backlight" name - kept here, so mu300-led knows it under 5.4 and mainline alike.
 * Bootloader state is not trusted: it starts dark.
 */
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>

#define SC27XX_KPLED_V_SHIFT	12
#define SC27XX_KPLED_V_MSK	GENMASK(15, 12)
#define SC27XX_KPLED_PD		BIT(11)
#define SC27XX_KPLED_MAX	127

struct sc27xx_kpled {
	struct led_classdev cdev;
	struct regmap *regmap;
	struct mutex lock;
	u32 ctrl;
};

static int sc27xx_kpled_set(struct led_classdev *cdev, enum led_brightness value)
{
	struct sc27xx_kpled *led = container_of(cdev, struct sc27xx_kpled, cdev);
	/* 16 current steps: the lowest brightnesses still light it */
	u32 level = value ? max_t(u32, value / 16, 1) : 0;
	int ret;

	mutex_lock(&led->lock);
	ret = regmap_update_bits(led->regmap, led->ctrl, SC27XX_KPLED_V_MSK, level << SC27XX_KPLED_V_SHIFT);
	if (!ret)
		ret = regmap_update_bits(led->regmap, led->ctrl, SC27XX_KPLED_PD, value ? 0 : SC27XX_KPLED_PD);
	mutex_unlock(&led->lock);
	return ret;
}

static int sc27xx_kpled_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct sc27xx_kpled *led;
	int ret;

	led = devm_kzalloc(dev, sizeof(*led), GFP_KERNEL);
	if (!led)
		return -ENOMEM;
	led->regmap = dev_get_regmap(dev->parent, NULL);
	if (!led->regmap)
		return dev_err_probe(dev, -ENODEV, "no PMIC regmap\n");
	ret = of_property_read_u32_index(dev->of_node, "reg", 0, &led->ctrl);
	if (ret)
		return dev_err_probe(dev, ret, "no control register\n");
	ret = devm_mutex_init(dev, &led->lock);
	if (ret)
		return ret;

	led->cdev.name = "keyboard-backlight";
	led->cdev.max_brightness = SC27XX_KPLED_MAX;
	led->cdev.brightness_set_blocking = sc27xx_kpled_set;
	sc27xx_kpled_set(&led->cdev, LED_OFF);
	return devm_led_classdev_register(dev, &led->cdev);
}

static const struct of_device_id sc27xx_kpled_of_match[] = {
	{ .compatible = "sprd,ump9620-keypad-led" },
	{ .compatible = "sprd,sc2730-keypad-led" },
	{ }
};
MODULE_DEVICE_TABLE(of, sc27xx_kpled_of_match);

static struct platform_driver sc27xx_kpled_driver = {
	.probe = sc27xx_kpled_probe,
	.driver = {
		.name = "sc27xx-keypad-led",
		.of_match_table = sc27xx_kpled_of_match,
	},
};
module_platform_driver(sc27xx_kpled_driver);

MODULE_DESCRIPTION("Spreadtrum/Unisoc PMIC keypad backlight");
MODULE_LICENSE("GPL");
