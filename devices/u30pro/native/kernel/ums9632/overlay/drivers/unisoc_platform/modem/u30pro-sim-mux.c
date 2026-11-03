// SPDX-License-Identifier: GPL-2.0-only
/* U30 Pro SIM routing. GPIO names, polarity and boot arguments follow ZTE's
 * Android sim_gpio_ctl.c; card IDs follow MU5358 BoardIdConfigEnum.
 * No AT, modem reset, NV or automatic policy owner belongs in this driver.
 */
#include <linux/ctype.h>
#include <linux/device.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/workqueue.h>

struct u30pro_mux {
	struct device *dev;
	struct gpio_desc *detect, *chip, *psim;
	struct mutex lock;
	struct delayed_work changed;
};

/* Parse complete tokens, never a substring or a fixed firmware fingerprint. */
static int boot_value(const char *args, const char *key, int fallback)
{
	size_t len = strlen(key);
	const char *p = args;

	if (!p)
		return fallback;
	while (*p) {
		while (isspace(*p))
			p++;
		if (!strncmp(p, key, len) && p[len] >= '0' && p[len] <= '9' &&
		    (!p[len + 1] || isspace(p[len + 1])))
			return p[len] - '0';
		while (*p && !isspace(*p))
			p++;
	}
	return fallback;
}

static ssize_t chip_select_show(struct device *dev, struct device_attribute *a, char *buf)
{
	struct u30pro_mux *m = dev_get_drvdata(dev);
	int val = gpiod_get_value_cansleep(m->chip);
	return val < 0 ? val : sysfs_emit(buf, "%d\n", val);
}
static ssize_t psim_select_show(struct device *dev, struct device_attribute *a, char *buf)
{
	struct u30pro_mux *m = dev_get_drvdata(dev);
	int val = gpiod_get_value_cansleep(m->psim);
	return val < 0 ? val : sysfs_emit(buf, "%d\n", val);
}
static ssize_t selector_store(struct u30pro_mux *m, struct gpio_desc *gpio,
			      const char *buf, size_t count)
{
	unsigned int value;
	int ret = kstrtouint(buf, 10, &value);

	if (ret || value > 1)
		return -EINVAL;
	mutex_lock(&m->lock);
	gpiod_set_value_cansleep(gpio, value);
	ret = gpiod_get_value_cansleep(gpio);
	mutex_unlock(&m->lock);
	if (ret < 0)
		return ret;
	return ret == value ? count : -EIO;
}
static ssize_t chip_select_store(struct device *dev, struct device_attribute *a,
				const char *buf, size_t count)
{
	struct u30pro_mux *m = dev_get_drvdata(dev);
	return selector_store(m, m->chip, buf, count);
}
static ssize_t psim_select_store(struct device *dev, struct device_attribute *a,
				const char *buf, size_t count)
{
	struct u30pro_mux *m = dev_get_drvdata(dev);
	return selector_store(m, m->psim, buf, count);
}
static ssize_t physical_present_show(struct device *dev, struct device_attribute *a, char *buf)
{
	struct u30pro_mux *m = dev_get_drvdata(dev);
	int val = gpiod_get_value_cansleep(m->detect);
	return val < 0 ? val : sysfs_emit(buf, "%d\n", !val);
}
static DEVICE_ATTR_RW(chip_select);
static DEVICE_ATTR_RW(psim_select);
static DEVICE_ATTR_RO(physical_present);
static struct attribute *mux_attrs[] = {
	&dev_attr_chip_select.attr, &dev_attr_psim_select.attr,
	&dev_attr_physical_present.attr, NULL
};
static const struct attribute_group mux_group = { .attrs = mux_attrs };

static void card_changed(struct work_struct *work)
{
	struct u30pro_mux *m = container_of(to_delayed_work(work), struct u30pro_mux, changed);
	int value = gpiod_get_value_cansleep(m->detect);
	char status[32];
	char *env[] = { "ZTE_DRIVER=zte_psim", status, NULL };

	if (value < 0)
		return;
	/* Original event polarity; a notification never changes the selected SIM. */
	snprintf(status, sizeof(status), "PSIM_STATUS=%d", value);
	kobject_uevent_env(&m->dev->kobj, KOBJ_CHANGE, env);
}
static irqreturn_t card_irq(int irq, void *data)
{
	struct u30pro_mux *m = data;
	mod_delayed_work(system_power_efficient_wq, &m->changed, msecs_to_jiffies(100));
	return IRQ_HANDLED;
}
static void cancel_events(void *data)
{
	struct u30pro_mux *m = data;
	cancel_delayed_work_sync(&m->changed);
}
static int mux_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *chosen;
	struct u30pro_mux *m;
	const char *args = NULL;
	int board, chip, psim, irq, ret;

	chosen = of_find_node_by_path("/chosen");
	if (chosen)
		of_property_read_string(chosen, "bootargs", &args);
	/* Stock MU5358: PSIM selector 0=eSIM1, 1=physical; chip 0=eSIM2,
	 * 1=eSIM3. Respect explicit boot choices before the userspace replay. */
	board = boot_value(args, "zteboot.zte_boardid=", -1);
	board = boot_value(args, "androidboot.zte_boardid=", board);
	chip = boot_value(args, "androidboot.simswitch=", board == 6);
	psim = boot_value(args, "androidboot.psimswitch=", board == 5 ? 0 : 1);
	of_node_put(chosen);
	if ((chip != 0 && chip != 1) || (psim != 0 && psim != 1))
		return dev_err_probe(dev, -EINVAL, "Invalid SIM boot selector\n");
	m = devm_kzalloc(dev, sizeof(*m), GFP_KERNEL);
	if (!m)
		return -ENOMEM;
	m->dev = dev;
	mutex_init(&m->lock);
	m->detect = devm_gpiod_get(dev, "esim1-psim-switch", GPIOD_IN);
	if (IS_ERR(m->detect))
		return dev_err_probe(dev, PTR_ERR(m->detect), "SIM detection GPIO\n");
	m->chip = devm_gpiod_get(dev, "esim2-esim3-switch", chip ? GPIOD_OUT_HIGH : GPIOD_OUT_LOW);
	if (IS_ERR(m->chip))
		return dev_err_probe(dev, PTR_ERR(m->chip), "SIM chip selector\n");
	m->psim = devm_gpiod_get(dev, "soft-psim-switch", psim ? GPIOD_OUT_LOW : GPIOD_OUT_HIGH);
	if (IS_ERR(m->psim))
		return dev_err_probe(dev, PTR_ERR(m->psim), "SIM physical selector\n");
	platform_set_drvdata(pdev, m);
	INIT_DELAYED_WORK(&m->changed, card_changed);
	ret = devm_add_action_or_reset(dev, cancel_events, m);
	if (ret)
		return ret;
	ret = devm_device_add_group(dev, &mux_group);
	if (ret)
		return ret;
	irq = gpiod_to_irq(m->detect);
	if (irq < 0)
		return dev_err_probe(dev, irq, "SIM detection IRQ\n");
	ret = devm_request_threaded_irq(dev, irq, NULL, card_irq,
		IRQF_ONESHOT | IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING,
		dev_name(dev), m);
	if (ret)
		return dev_err_probe(dev, ret, "SIM detection IRQ request\n");
	dev_info(dev, "native SIM mux ready; board=%d chip=%d psim=%d\n",
		 board, chip, !psim);
	return 0;
}
static const struct of_device_id mux_match[] = {
	{ .compatible = "zte,sim_gpio_ctrl" }, {}
};
MODULE_DEVICE_TABLE(of, mux_match);
static struct platform_driver mux_driver = {
	.probe = mux_probe,
	.driver = { .name = "u30pro-sim-mux", .of_match_table = mux_match },
};
module_platform_driver(mux_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("U30 Pro native SIM card routing");
