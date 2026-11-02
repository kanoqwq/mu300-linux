// SPDX-License-Identifier: GPL-2.0-only
/* User-authorized fw_devlink experiment only. This is NOT a charger driver.
 * Bind the existing DT I2C client without any I2C transfer or child device,
 * regulator, power_supply, GPIO, IRQ, or charging policy registration.
 * Tests whether the core can pick up dangling child-fwnode consumers after
 * the original master client is driver-bound. No host-mode/PD claims.
 */
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>

static int u30pro_charger_link_probe(struct i2c_client *client)
{
	if (!of_machine_is_compatible("sprd,ums9632") ||
	    client->addr != 0x6b ||
	    of_alias_get_id(client->adapter->dev.of_node, "i2c") != 6)
		return -ENODEV;
	dev_warn(&client->dev,
		 "DIAGNOSTIC ONLY: fwnode parent bound, no I2C/charger/regulator operations\n");
	return 0;
}

static const struct of_device_id u30pro_charger_link_match[] = {
	{ .compatible = "ti,bq2560x_chg" },
	{ }
};
MODULE_DEVICE_TABLE(of, u30pro_charger_link_match);

static struct i2c_driver u30pro_charger_link_driver = {
	.driver = {
		.name = "u30pro-charger-link-diagnostic",
		.of_match_table = u30pro_charger_link_match,
	},
	.probe = u30pro_charger_link_probe,
};
module_i2c_driver(u30pro_charger_link_driver);
MODULE_DESCRIPTION("U30 Pro diagnostic-only fw_devlink parent binding; NO charger support");
MODULE_LICENSE("GPL");
