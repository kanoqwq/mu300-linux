// SPDX-License-Identifier: GPL-2.0-only
/*
 * ZTE V50 (MU3351) SIM multiplexer, on the stock "zte,sim_gpio_ctrl" binding.
 *
 * From kanoqwq's reconstruction of the stock B15 sim_gpio_ctl module (kanoqwq/mu300-linux f0ed60ff, b4c051c8).
 * The modem has two SIM interfaces; a V50 can carry up to four cards, so two of them sit behind board switches:
 *   soft-psim-switch    SIM1's interface: 0 the first built-in card, 1 the external card (tray)
 *   esim2-esim3-switch  SIM2's interface: 0 the second built-in card, 1 the third one
 *   esim1-psim-switch   an INPUT despite its name: the card holder's detect line; never written
 *
 * MU300 (docs/FINDINGS.md 204): nothing is driven at probe. The stock module set both outputs from
 * androidboot.simswitch/psimswitch, or high/low without them; this one takes the lines as the bootloader left them,
 * so a V50 whose owner never chose a card keeps exactly the routing it had before this driver existed. A level is
 * written only when userspace asks for one (mu300-sim, before the modem's CP starts, at the boot after a choice),
 * and only when it differs from the line's present level. Every GPIO number comes from the board's device tree.
 */
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/sysfs.h>

struct v50_sim {
	struct gpio_desc *detect, *chip, *psim;
	struct mutex lock;
};

static ssize_t v50_read(struct v50_sim *sim, struct gpio_desc *gpio, char *buf)
{
	int value;

	mutex_lock(&sim->lock);
	value = gpiod_get_value_cansleep(gpio);
	mutex_unlock(&sim->lock);
	return value < 0 ? value : sysfs_emit(buf, "%d\n", value);
}

static ssize_t v50_write(struct v50_sim *sim, struct gpio_desc *gpio, const char *buf, size_t count)
{
	unsigned int value;
	int ret;

	ret = kstrtouint(buf, 10, &value);
	if (ret || value > 1)
		return -EINVAL;
	mutex_lock(&sim->lock);
	ret = gpiod_get_value_cansleep(gpio);
	/* the same level: the line is left as it is (an input the bootloader left with a pull stays one) */
	if (ret >= 0 && ret != value)
		ret = gpiod_direction_output(gpio, value);
	if (ret >= 0) {
		ret = gpiod_get_value_cansleep(gpio);
		if (ret >= 0)
			ret = ret == value ? 0 : -EIO;
	}
	mutex_unlock(&sim->lock);
	return ret < 0 ? ret : count;
}

static ssize_t chip_select_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct v50_sim *sim = dev_get_drvdata(dev);

	return v50_read(sim, sim->chip, buf);
}

static ssize_t chip_select_store(struct device *dev, struct device_attribute *attr,
				 const char *buf, size_t count)
{
	struct v50_sim *sim = dev_get_drvdata(dev);

	return v50_write(sim, sim->chip, buf, count);
}

static ssize_t psim_select_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct v50_sim *sim = dev_get_drvdata(dev);

	return v50_read(sim, sim->psim, buf);
}

static ssize_t psim_select_store(struct device *dev, struct device_attribute *attr,
				 const char *buf, size_t count)
{
	struct v50_sim *sim = dev_get_drvdata(dev);

	return v50_write(sim, sim->psim, buf, count);
}

static ssize_t psim_present_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct v50_sim *sim = dev_get_drvdata(dev);

	return v50_read(sim, sim->detect, buf);
}

static DEVICE_ATTR_RW(chip_select);
static DEVICE_ATTR_RW(psim_select);
static DEVICE_ATTR_RO(psim_present);
static struct attribute *v50_sim_attrs[] = {
	&dev_attr_chip_select.attr,
	&dev_attr_psim_select.attr,
	&dev_attr_psim_present.attr,
	NULL,
};
ATTRIBUTE_GROUPS(v50_sim);

static int v50_sim_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *v50;
	struct v50_sim *sim;

	/* other boards' vendor trees may carry an unused node of this binding: the V50 is the one with the AW9523B */
	v50 = of_find_compatible_node(NULL, NULL, "awinic,aw9523b");
	if (!v50)
		return -ENODEV;
	of_node_put(v50);
	sim = devm_kzalloc(dev, sizeof(*sim), GFP_KERNEL);
	if (!sim)
		return -ENOMEM;
	mutex_init(&sim->lock);
	/* the stock binding spells these with a singular -gpio, which gpiolib also takes; GPIOD_ASIS: no level set */
	sim->detect = devm_gpiod_get(dev, "esim1-psim-switch", GPIOD_ASIS);
	if (IS_ERR(sim->detect))
		return dev_err_probe(dev, PTR_ERR(sim->detect), "card detect GPIO\n");
	sim->chip = devm_gpiod_get(dev, "esim2-esim3-switch", GPIOD_ASIS);
	if (IS_ERR(sim->chip))
		return dev_err_probe(dev, PTR_ERR(sim->chip), "SIM2 selector GPIO\n");
	sim->psim = devm_gpiod_get(dev, "soft-psim-switch", GPIOD_ASIS);
	if (IS_ERR(sim->psim))
		return dev_err_probe(dev, PTR_ERR(sim->psim), "SIM1 selector GPIO\n");
	platform_set_drvdata(pdev, sim);
	dev_info(dev, "MU300: ZTE V50 SIM switches, SIM1 %d, SIM2 %d (left as found)\n",
		 gpiod_get_value_cansleep(sim->psim), gpiod_get_value_cansleep(sim->chip));
	return 0;
}

static const struct of_device_id v50_sim_match[] = {
	{ .compatible = "zte,sim_gpio_ctrl" },
	{ }
};
MODULE_DEVICE_TABLE(of, v50_sim_match);

static struct platform_driver v50_sim_driver = {
	.probe = v50_sim_probe,
	.driver = {
		.name = "zte-v50-sim",
		.of_match_table = v50_sim_match,
		.dev_groups = v50_sim_groups,
	},
};
module_platform_driver(v50_sim_driver);
MODULE_DESCRIPTION("ZTE V50 SIM multiplexer (stock zte,sim_gpio_ctrl binding)");
MODULE_LICENSE("GPL");
