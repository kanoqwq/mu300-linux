// SPDX-License-Identifier: GPL-2.0-only
/*
 * The ZTE V50's (MU3351) front LEDs: an Awinic AW9523B at 0x5b on I2C3, as ZTE's Android device tree describes it
 * ("awinic,aw9523b", aw9523b,led_enable = 1, aw9523b,gpio_enable = 0, an "aw9523b,led" node with the groups). Not
 * mainline's pinctrl-aw9523, which takes another binding: this one is the vendor's, used as it is.
 *
 * By kano (kanoqwq), from his reading of ZTE's Android driver and his V50 (2026-10-14; his line of this project,
 * commit f6b0668d); brought over unchanged in what it does (docs/FINDINGS.md 95). What he found:
 *   - a group's aw9523b,idx are GPIO pin numbers, not DIM registers: P0_0-7 dim at 0x24-0x2b, P1_0-3 at 0x20-0x23,
 *     P1_4-7 at 0x2c-0x2f (aw9523b_brightness_work);
 *   - the current limit (GCR bits 1:0) is the low two bits of aw9523b,default_imax (aw9523b_set_led_imax);
 *   - the vendor's groups are colours across all lamps; the board has three lamps, NET1, NET2 and Wi-Fi, each with
 *     white, blue and red emitters (groupcolor_store, and lit one group at a time on the device): v50_lamps below.
 *
 * What it registers: the device tree's groups under their own names, every wired pin alone (aw9523b:pinN), and,
 * only when the binding is exactly the V50's, the lamps by colour (v50:net1:white ...; mu300-led uses those). No
 * interrupt, worker or thread; every write is synchronous. All dark at unbind and shutdown.
 * A board without this node never gets here: the F50's and the U30 Air's device trees have none.
 */
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>

#define AW_INPUT_MASK0	0x06
#define AW_INPUT_MASK1	0x07
#define AW_ID		0x10
#define AW_ID_VALUE	0x23
#define AW_GCR		0x11
#define AW_GCR_PUSHPULL	0x10
#define AW_MODE0	0x12
#define AW_MODE1	0x13
#define AW_RESET	0x7f
#define AW_CHANNELS	16

struct aw9523b;
struct aw9523b_led {
	struct led_classdev cdev;
	struct aw9523b *chip;
	u16 pins;
};

struct aw9523b {
	struct i2c_client *client;
	struct mutex lock;
	u16 pins;
	unsigned int count;
	struct aw9523b_led leds[];
};

/* P0_0..7 => DIM4..11 (0x24..), P1_0..3 => DIM0..3 (0x20..), P1_4..7 => DIM12..15 (0x2c..) */
static unsigned int aw9523b_dim(unsigned int pin)
{
	return pin < 8 ? 0x24 + pin : pin < 12 ? 0x18 + pin : 0x20 + pin;
}

/* the three lamps: two white emitters each, Wi-Fi one blue and one red; white is brighter, so its limit is lower */
static const struct {
	const char *name;
	u16 pins;
	unsigned int maximum;
} v50_lamps[] = {
	{ "v50:net1:white", BIT(8) | BIT(11), 80 },
	{ "v50:net1:blue", BIT(9) | BIT(12), 120 },
	{ "v50:net1:red", BIT(10) | BIT(13), 120 },
	{ "v50:net2:white", BIT(14) | BIT(1), 80 },
	{ "v50:net2:blue", BIT(15) | BIT(2), 120 },
	{ "v50:net2:red", BIT(0) | BIT(3), 120 },
	{ "v50:wifi:white", BIT(4) | BIT(5), 80 },
	{ "v50:wifi:blue", BIT(6), 120 },
	{ "v50:wifi:red", BIT(7), 120 },
};

static int aw9523b_brightness(struct led_classdev *cdev, enum led_brightness brightness)
{
	struct aw9523b_led *led = container_of(cdev, struct aw9523b_led, cdev);
	struct aw9523b *chip = led->chip;
	unsigned int pin;
	int ret = 0;

	mutex_lock(&chip->lock);
	for (pin = 0; pin < AW_CHANNELS && !ret; pin++)
		if (led->pins & BIT(pin))
			ret = i2c_smbus_write_byte_data(chip->client, aw9523b_dim(pin), brightness);
	mutex_unlock(&chip->lock);
	return ret;
}

static void aw9523b_off(void *data)
{
	struct aw9523b *chip = data;
	unsigned int pin;

	mutex_lock(&chip->lock);
	for (pin = 0; pin < AW_CHANNELS; pin++)
		if (chip->pins & BIT(pin))
			i2c_smbus_write_byte_data(chip->client, aw9523b_dim(pin), 0);
	mutex_unlock(&chip->lock);
}

static int aw9523b_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct device_node *bank;
	struct gpio_desc *reset;
	struct aw9523b *chip;
	u32 enabled, gpio_enabled, imax, pins[AW_CHANNELS];
	unsigned int count, index = 0, pin;
	int ret;

	if (of_property_read_u32(dev->of_node, "aw9523b,led_enable", &enabled) || enabled != 1 ||
	    of_property_read_u32(dev->of_node, "aw9523b,gpio_enable", &gpio_enabled) || gpio_enabled)
		return -ENODEV;
	bank = of_get_child_by_name(dev->of_node, "aw9523b,led");
	if (!bank)
		return -EINVAL;
	count = of_get_available_child_count(bank);
	if (!count || count > AW_CHANNELS ||
	    of_property_read_u32(bank, "aw9523b,default_imax", &imax) || imax > 255) {
		ret = -EINVAL;
		goto out_node;
	}
	/* the groups, then one per pin, then the lamps */
	chip = devm_kzalloc(dev, struct_size(chip, leds, count + AW_CHANNELS + ARRAY_SIZE(v50_lamps)), GFP_KERNEL);
	if (!chip) {
		ret = -ENOMEM;
		goto out_node;
	}
	mutex_init(&chip->lock);
	chip->client = client;
	chip->count = count;
	for_each_available_child_of_node_scoped(bank, node) {
		struct aw9523b_led *led = &chip->leds[index++];
		u32 n, maximum, initial;
		const char *name;

		if (of_property_read_string(node, "aw9523b,name", &name) ||
		    of_property_read_u32(node, "aw9523b,idx_count", &n) || !n || n > AW_CHANNELS ||
		    of_property_count_u32_elems(node, "aw9523b,idx") != n ||
		    of_property_read_u32_array(node, "aw9523b,idx", pins, n) ||
		    of_property_read_u32(node, "aw9523b,max_brightness", &maximum) || !maximum || maximum > 255 ||
		    of_property_read_u32(node, "aw9523b,default_brightness", &initial) || initial > maximum) {
			ret = -EINVAL;
			goto out_node;
		}
		for (pin = 0; pin < n; pin++) {
			if (pins[pin] >= AW_CHANNELS || (led->pins & BIT(pins[pin]))) {
				ret = -EINVAL;
				goto out_node;
			}
			led->pins |= BIT(pins[pin]);
		}
		chip->pins |= led->pins;
		led->chip = chip;
		led->cdev.name = name;
		led->cdev.max_brightness = maximum;
		led->cdev.brightness = initial;
		led->cdev.brightness_set_blocking = aw9523b_brightness;
	}
	/* the whole binding is checked before the chip is touched */
	if (!i2c_check_functionality(client->adapter, I2C_FUNC_SMBUS_BYTE_DATA)) {
		ret = -EOPNOTSUPP;
		goto out_node;
	}
	reset = devm_gpiod_get_optional(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(reset)) {
		ret = PTR_ERR(reset);
		goto out_node;
	}
	if (reset) {
		gpiod_set_value_cansleep(reset, 0);
		usleep_range(1000, 2000);
		gpiod_set_value_cansleep(reset, 1);
		usleep_range(2000, 3000);
	}
	ret = i2c_smbus_read_byte_data(client, AW_ID);
	if (ret != AW_ID_VALUE) {
		ret = ret < 0 ? ret : -ENODEV;
		goto out_node;
	}
	ret = i2c_smbus_write_byte_data(client, AW_RESET, 0);
	if (ret)
		goto out_node;
	usleep_range(1000, 2000);
	ret = devm_add_action_or_reset(dev, aw9523b_off, chip);
	if (ret)
		goto out_node;
	for (pin = 0; pin < AW_CHANNELS && !ret; pin++)
		ret = i2c_smbus_write_byte_data(client, aw9523b_dim(pin), 0);
	/* LEDs only: no input interrupts; the unused pins stay GPIOs. The current limit has two bits. */
	if (!ret)
		ret = i2c_smbus_write_byte_data(client, AW_INPUT_MASK0, 0xff);
	if (!ret)
		ret = i2c_smbus_write_byte_data(client, AW_INPUT_MASK1, 0xff);
	if (!ret)
		ret = i2c_smbus_write_byte_data(client, AW_GCR, AW_GCR_PUSHPULL | (imax & 3));
	if (!ret)
		ret = i2c_smbus_write_byte_data(client, AW_MODE0, ~chip->pins & 0xff);
	if (!ret)
		ret = i2c_smbus_write_byte_data(client, AW_MODE1, (~chip->pins >> 8) & 0xff);
	if (ret)
		goto out_node;
	for (index = 0; index < count; index++) {
		struct aw9523b_led *led = &chip->leds[index];

		ret = aw9523b_brightness(&led->cdev, led->cdev.brightness);
		if (!ret)
			ret = devm_led_classdev_register(dev, &led->cdev);
		if (ret)
			goto out_node;
	}
	for (pin = 0; pin < AW_CHANNELS; pin++) {
		struct aw9523b_led *led;
		unsigned int maximum = 255;

		if (!(chip->pins & BIT(pin)))
			continue;
		/* a pin alone is no brighter than any group it is in allows */
		for (index = 0; index < count; index++)
			if (chip->leds[index].pins & BIT(pin))
				maximum = min(maximum, chip->leds[index].cdev.max_brightness);
		led = &chip->leds[chip->count++];
		led->chip = chip;
		led->pins = BIT(pin);
		led->cdev.name = devm_kasprintf(dev, GFP_KERNEL, "aw9523b:pin%u", pin);
		if (!led->cdev.name) {
			ret = -ENOMEM;
			goto out_node;
		}
		led->cdev.max_brightness = maximum;
		ret = i2c_smbus_read_byte_data(client, aw9523b_dim(pin));
		if (ret < 0)
			goto out_node;
		led->cdev.brightness = ret;
		led->cdev.brightness_set_blocking = aw9523b_brightness;
		ret = devm_led_classdev_register(dev, &led->cdev);
		if (ret)
			goto out_node;
	}
	/* the lamps only for the V50's own binding: four groups (all, white, blue, red) over all 16 pins */
	if (count == 4 && chip->pins == 0xffff &&
	    chip->leds[1].pins == (BIT(8) | BIT(11) | BIT(14) | BIT(1) | BIT(4) | BIT(5)) &&
	    chip->leds[2].pins == (BIT(9) | BIT(12) | BIT(15) | BIT(2) | BIT(6)) &&
	    chip->leds[3].pins == (BIT(10) | BIT(13) | BIT(0) | BIT(3) | BIT(7))) {
		for (index = 0; index < ARRAY_SIZE(v50_lamps); index++) {
			struct aw9523b_led *led = &chip->leds[chip->count++];

			led->chip = chip;
			led->pins = v50_lamps[index].pins;
			led->cdev.name = v50_lamps[index].name;
			led->cdev.max_brightness = v50_lamps[index].maximum;
			led->cdev.brightness_set_blocking = aw9523b_brightness;
			ret = devm_led_classdev_register(dev, &led->cdev);
			if (ret)
				goto out_node;
		}
	}
	ret = 0;
	i2c_set_clientdata(client, chip);
	dev_info(dev, "%u LED groups, pin mask %#x\n", count, chip->pins);
out_node:
	of_node_put(bank);
	return ret;
}

static void aw9523b_shutdown(struct i2c_client *client)
{
	struct aw9523b *chip = i2c_get_clientdata(client);

	if (chip)
		aw9523b_off(chip);
}

static const struct of_device_id aw9523b_match[] = {
	{ .compatible = "awinic,aw9523b" },
	{ }
};
MODULE_DEVICE_TABLE(of, aw9523b_match);

static struct i2c_driver aw9523b_driver = {
	.driver = { .name = "aw9523b-led", .of_match_table = aw9523b_match },
	.probe = aw9523b_probe,
	.shutdown = aw9523b_shutdown,
};
module_i2c_driver(aw9523b_driver);
MODULE_DESCRIPTION("AW9523B LEDs of the ZTE V50 (MU3351), vendor binding");
MODULE_AUTHOR("kanoqwq");
MODULE_LICENSE("GPL");
