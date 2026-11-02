// SPDX-License-Identifier: GPL-2.0
/*
 * PWM backlight of the ZTE U30 Pro, bound to the stock "sprd,qogirn5-backlight" device tree node.
 *
 * The node describes one PWM channel and the stock brightness scale (0..sprd,max-brightness-level).  The
 * class device keeps the stock name so existing user space (/sys/class/backlight/sprd_backlight) works.
 */
#include <linux/backlight.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pwm.h>

struct u30_bl {
	struct pwm_device *pwm;
	u32 min_level;
	u32 max_level;
};

static int u30_bl_apply(struct u30_bl *bl, int level)
{
	struct pwm_state state;

	pwm_init_state(bl->pwm, &state);
	if (level <= 0) {
		/* keep the PWM running at 0 %: a disabled sprd PWM leaves its pad high, i.e. full brightness */
		state.enabled = true;
		state.duty_cycle = 0;
	} else {
		if (level < bl->min_level)
			level = bl->min_level;
		state.enabled = true;
		state.duty_cycle = div_u64((u64)state.period * level, bl->max_level);
	}
	return pwm_apply_might_sleep(bl->pwm, &state);
}

static int u30_bl_update_status(struct backlight_device *bd)
{
	struct u30_bl *bl = bl_get_data(bd);
	int level = backlight_get_brightness(bd);

	return u30_bl_apply(bl, level);
}

static const struct backlight_ops u30_bl_ops = {
	.options = BL_CORE_SUSPENDRESUME,
	.update_status = u30_bl_update_status,
};

static int u30_bl_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *np = dev->of_node;
	struct backlight_properties props = { .type = BACKLIGHT_RAW };
	struct backlight_device *bd;
	struct u30_bl *bl;
	u32 def = 0;

	bl = devm_kzalloc(dev, sizeof(*bl), GFP_KERNEL);
	if (!bl)
		return -ENOMEM;

	bl->pwm = devm_pwm_get(dev, "backlight");
	if (IS_ERR(bl->pwm))
		return dev_err_probe(dev, PTR_ERR(bl->pwm), "no PWM\n");

	bl->min_level = 0;
	bl->max_level = 4095;
	of_property_read_u32(np, "sprd,min-brightness-level", &bl->min_level);
	of_property_read_u32(np, "sprd,max-brightness-level", &bl->max_level);
	of_property_read_u32(np, "default-brightness-level", &def);
	if (!bl->max_level)
		return -EINVAL;
	if (def > bl->max_level)
		def = bl->max_level;

	props.max_brightness = bl->max_level;
	props.brightness = def;
	props.power = BACKLIGHT_POWER_ON;

	bd = devm_backlight_device_register(dev, "sprd_backlight", dev, bl, &u30_bl_ops, &props);
	if (IS_ERR(bd))
		return dev_err_probe(dev, PTR_ERR(bd), "backlight register failed\n");

	platform_set_drvdata(pdev, bd);
	dev_info(dev, "PWM backlight ready: 0..%u, default %u\n", bl->max_level, def);
	return 0;
}

static const struct of_device_id u30_bl_of_match[] = {
	{ .compatible = "sprd,qogirn5-backlight" },
	{ }
};
MODULE_DEVICE_TABLE(of, u30_bl_of_match);

static struct platform_driver u30_bl_driver = {
	.probe = u30_bl_probe,
	.driver = {
		.name = "u30pro-backlight",
		.of_match_table = u30_bl_of_match,
	},
};
module_platform_driver(u30_bl_driver);

MODULE_DESCRIPTION("U30 Pro PWM backlight");
MODULE_LICENSE("GPL");
