/* SPDX-License-Identifier: GPL-2.0 */
/*
 * MU300: linux/of_gpio.h is gone in 7.x; the legacy GPIO number API the vendor code feeds with it is not. kbuild
 * searches the kernel's include directories first, so this file is only used by a kernel that has no header of
 * this name - on 6.x the kernel's own of_get_named_gpio() is.
 */
#ifndef _MU300_OF_GPIO_H
#define _MU300_OF_GPIO_H

#include <linux/err.h>
#include <linux/gpio.h>
#include <linux/gpio/consumer.h>
#include <linux/of.h>
#include <linux/string.h>

static inline int of_get_named_gpio(const struct device_node *np, const char *propname, int index)
{
	struct gpio_desc *desc;
	char con[64];
	size_t n;
	int gpio;

	/* the fwnode lookup takes the property name without its -gpios/-gpio suffix */
	strscpy(con, propname, sizeof(con));
	n = strlen(con);
	if (n > 6 && !strcmp(con + n - 6, "-gpios"))
		con[n - 6] = '\0';
	else if (n > 5 && !strcmp(con + n - 5, "-gpio"))
		con[n - 5] = '\0';

	desc = fwnode_gpiod_get_index(of_fwnode_handle((struct device_node *)np), con, index,
				      GPIOD_ASIS, "mu300-of-gpio");
	if (IS_ERR(desc))
		return PTR_ERR(desc);
	gpio = desc_to_gpio(desc);
	/* like of_get_named_gpio(): the caller requests the number itself */
	gpiod_put(desc);
	return gpio;
}

#endif
