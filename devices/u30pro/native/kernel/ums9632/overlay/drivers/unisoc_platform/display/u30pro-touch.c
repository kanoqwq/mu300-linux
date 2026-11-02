// SPDX-License-Identifier: GPL-2.0
/*
 * Touch controller of the ZTE U30 Pro (stock node "tlsc6x,tlsc6x_ts", I2C address 0x2e; the chip reports
 * itself as a CHSC6448 / TLSC6x family part).  Only the run-time reporting path is implemented: no firmware
 * download, calibration or proximity/gesture features of the stock driver.
 *
 * Report format (register 0, 3 bytes):
 *   b0: bit7 = y[8], bit6 = x[8], bit4 = 1 for "up", bit2 = finger id (0/1), bits1:0 = number of fingers
 *   b1: x[7:0]    b2: y[7:0]
 * A few fixed patterns mean "no data" (ff a5 5a, ff a5 aa, 00 00 00, ff ff ff).
 *
 * The input device keeps the stock name "tlsc6x_touch" and the 0..239 coordinate range, so existing user
 * space that looks the device up by name keeps working.
 */
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/of.h>

#define U30_TS_SLOTS	2

struct u30_ts {
	struct i2c_client *client;
	struct input_dev *input;
	struct gpio_desc *reset;
	struct gpio_desc *power;
	struct gpio_desc *irq_gpio;
};

static int u30_ts_read(struct u30_ts *ts, u8 *buf)
{
	u8 reg = 0;
	struct i2c_msg msgs[2] = {
		{ .addr = ts->client->addr, .flags = 0, .len = 1, .buf = &reg },
		{ .addr = ts->client->addr, .flags = I2C_M_RD, .len = 3, .buf = buf },
	};
	int ret = i2c_transfer(ts->client->adapter, msgs, 2);

	if (ret < 0)
		return ret;
	return ret == 2 ? 0 : -EIO;
}

static irqreturn_t u30_ts_irq(int irq, void *data)
{
	struct u30_ts *ts = data;
	u8 b[3];
	int i, ret;

	ret = u30_ts_read(ts, b);
	if (ret) {
		dev_dbg_ratelimited(&ts->client->dev, "read failed: %d\n", ret);
		return IRQ_HANDLED;
	}

	if ((b[0] == 0xff && b[1] == 0xa5 && (b[2] == 0x5a || b[2] == 0xaa)) ||
	    (!b[0] && !b[1] && !b[2]) || (b[0] == 0xff && b[1] == 0xff && b[2] == 0xff))
		return IRQ_HANDLED;

	if (!(b[0] & 0x03)) {
		for (i = 0; i < U30_TS_SLOTS; i++) {
			input_mt_slot(ts->input, i);
			input_mt_report_slot_state(ts->input, MT_TOOL_FINGER, false);
		}
		input_report_key(ts->input, BTN_TOUCH, 0);
		input_sync(ts->input);
		return IRQ_HANDLED;
	}

	{
		bool press = !(b[0] & 0x10);
		u16 x = ((b[0] & 0x40) >> 6) << 8 | b[1];
		u16 y = ((b[0] & 0x80) >> 7) << 8 | b[2];

		input_mt_slot(ts->input, (b[0] >> 2) & 1);
		input_mt_report_slot_state(ts->input, MT_TOOL_FINGER, press);
		if (press) {
			input_report_abs(ts->input, ABS_MT_POSITION_X, x);
			input_report_abs(ts->input, ABS_MT_POSITION_Y, y);
			input_report_abs(ts->input, ABS_MT_TOUCH_MAJOR, 0x10);
			input_report_key(ts->input, BTN_TOUCH, 1);
		}
		input_sync(ts->input);
	}
	return IRQ_HANDLED;
}

static int u30_ts_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct u30_ts *ts;
	u32 max_x = 239, max_y = 239;
	u8 b[3];
	int ret, irq;

	ts = devm_kzalloc(dev, sizeof(*ts), GFP_KERNEL);
	if (!ts)
		return -ENOMEM;
	ts->client = client;
	of_property_read_u32(dev->of_node, "TP_MAX_X", &max_x);
	of_property_read_u32(dev->of_node, "TP_MAX_Y", &max_y);

	ts->power = devm_gpiod_get_optional(dev, "tlsc6x,power", GPIOD_OUT_HIGH);
	if (IS_ERR(ts->power))
		return dev_err_probe(dev, PTR_ERR(ts->power), "power gpio\n");
	ts->reset = devm_gpiod_get(dev, "tlsc6x,reset", GPIOD_OUT_HIGH);
	if (IS_ERR(ts->reset))
		return dev_err_probe(dev, PTR_ERR(ts->reset), "reset gpio\n");
	ts->irq_gpio = devm_gpiod_get(dev, "tlsc6x,irq", GPIOD_IN);
	if (IS_ERR(ts->irq_gpio))
		return dev_err_probe(dev, PTR_ERR(ts->irq_gpio), "irq gpio\n");

	/* stock sequence: supply on, reset high 10 ms, low 20 ms, high, 35 ms before the first access */
	usleep_range(2000, 2100);
	gpiod_set_value_cansleep(ts->reset, 1);
	usleep_range(10000, 11000);
	gpiod_set_value_cansleep(ts->reset, 0);
	msleep(20);
	gpiod_set_value_cansleep(ts->reset, 1);
	msleep(35);

	ret = u30_ts_read(ts, b);
	if (ret)
		return dev_err_probe(dev, ret, "controller does not answer\n");

	ts->input = devm_input_allocate_device(dev);
	if (!ts->input)
		return -ENOMEM;
	ts->input->name = "tlsc6x_touch";
	ts->input->id.bustype = BUS_I2C;
	__set_bit(INPUT_PROP_DIRECT, ts->input->propbit);
	input_set_capability(ts->input, EV_KEY, BTN_TOUCH);
	input_set_abs_params(ts->input, ABS_MT_POSITION_X, 0, max_x, 0, 0);
	input_set_abs_params(ts->input, ABS_MT_POSITION_Y, 0, max_y, 0, 0);
	input_set_abs_params(ts->input, ABS_MT_TOUCH_MAJOR, 0, 15, 0, 0);
	ret = input_mt_init_slots(ts->input, U30_TS_SLOTS, INPUT_MT_DIRECT);
	if (ret)
		return ret;
	ret = input_register_device(ts->input);
	if (ret)
		return ret;

	irq = gpiod_to_irq(ts->irq_gpio);
	if (irq < 0)
		return dev_err_probe(dev, irq, "no irq for the interrupt gpio\n");
	ret = devm_request_threaded_irq(dev, irq, NULL, u30_ts_irq,
					IRQF_TRIGGER_FALLING | IRQF_ONESHOT, "u30pro-touch", ts);
	if (ret)
		return dev_err_probe(dev, ret, "request irq\n");

	i2c_set_clientdata(client, ts);
	dev_info(dev, "touch ready: irq %d, range 0..%u x 0..%u, first report %02x %02x %02x\n",
		 irq, max_x, max_y, b[0], b[1], b[2]);
	return 0;
}

static const struct of_device_id u30_ts_of_match[] = {
	{ .compatible = "tlsc6x,tlsc6x_ts" },
	{ }
};
MODULE_DEVICE_TABLE(of, u30_ts_of_match);

static const struct i2c_device_id u30_ts_id[] = {
	{ "tlsc6x_ts" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, u30_ts_id);

static struct i2c_driver u30_ts_driver = {
	.driver = {
		.name = "u30pro-touch",
		.of_match_table = u30_ts_of_match,
	},
	.id_table = u30_ts_id,
	.probe = u30_ts_probe,
};
module_i2c_driver(u30_ts_driver);

MODULE_DESCRIPTION("ZTE U30 Pro touch controller (tlsc6x/CHSC6448)");
MODULE_LICENSE("GPL");
