/*
 * Copyright (C) 2017 Spreadtrum Communications Inc.
 *
 * SPDX-License-Identifier: (GPL-2.0+ OR MIT)
 */

#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/io.h>
#include <linux/i2c.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/sysfs.h>

#define I2C_CTL			0x00
#define I2C_ADDR_CFG		0x04
#define I2C_COUNT		0x08
#define I2C_RX			0x0c
#define I2C_TX			0x10
#define I2C_STATUS		0x14
#define I2C_HSMODE_CFG		0x18
#define I2C_VERSION		0x1c
#define ADDR_DVD0		0x20
#define ADDR_DVD1		0x24
#define ADDR_STA0_DVD		0x28
#define ADDR_RST		0x2c

/* I2C_CTL */
#define I2C_NACK_EN		BIT(22)
#define I2C_TRANS_EN		BIT(21)
#define STP_EN			BIT(20)
#define FIFO_AF_LVL_MASK	GENMASK(19, 16)
#define FIFO_AF_LVL		16
#define FIFO_AE_LVL_MASK	GENMASK(15, 12)
#define FIFO_AE_LVL		12
#define I2C_DMA_EN		BIT(11)
#define FULL_INTEN		BIT(10)
#define EMPTY_INTEN		BIT(9)
#define I2C_DVD_OPT		BIT(8)
#define I2C_OUT_OPT		BIT(7)
#define I2C_TRIM_OPT		BIT(6)
#define I2C_HS_MODE		BIT(4)
#define I2C_MODE		BIT(3)
#define I2C_EN			BIT(2)
#define I2C_INT_EN		BIT(1)
#define I2C_START		BIT(0)

/* I2C_STATUS */
#define SDA_IN			BIT(21)
#define SCL_IN			BIT(20)
#define FIFO_FULL		BIT(4)
#define FIFO_EMPTY		BIT(3)
#define I2C_INT			BIT(2)
#define I2C_RX_ACK		BIT(1)
#define I2C_BUSY		BIT(0)

/* ADDR_RST */
#define I2C_RST			BIT(0)

#define I2C_FIFO_DEEP		12
#define I2C_FIFO_FULL_THLD	15
#define I2C_FIFO_EMPTY_THLD	4
#define I2C_DATA_STEP		8
#define I2C_ADDR_DVD0_CALC(high, low)	\
	((((high) & GENMASK(15, 0)) << 16) | ((low) & GENMASK(15, 0)))
#define I2C_ADDR_DVD1_CALC(high, low)	\
	(((high) & GENMASK(31, 16)) | (((low) & GENMASK(31, 16)) >> 16))

/* timeout (ms) for pm runtime autosuspend */
#define SPRD_I2C_PM_TIMEOUT	1000
/* timeout (ms) for transfer message */
#define I2C_XFER_TIMEOUT	1000

/* UMS9632's separate transfer/NACK events are armed only for an active
 * prepared message. Do not arm them at probe or runtime-resume time.
 * Its FIFO threshold follows the MU5358 BSP; legacy SC9860 is unchanged.
 */
struct sprd_i2c_variant {
 u32 event_mask;
 u32 rx_full_threshold;
};
static const struct sprd_i2c_variant sc9860_variant = {
 .rx_full_threshold = I2C_FIFO_FULL_THLD,
};
static const struct sprd_i2c_variant ums9632_variant = {
 .event_mask = I2C_TRANS_EN | I2C_NACK_EN,
 .rx_full_threshold = 12,
};

/* SPRD i2c data structure */
struct sprd_i2c {
	struct i2c_adapter adap;
	struct device *dev;
	void __iomem *base;
	struct i2c_msg *msg;
	struct clk *clk;
	const struct sprd_i2c_variant *variant;
	bool msg_ack;
	u32 src_clk;
	u32 bus_freq;
	struct completion complete;
	u8 *buf;
	u32 count;
	int irq;
	int err;
};

static void sprd_i2c_set_count(struct sprd_i2c *i2c_dev, u32 count)
{
	writel(count, i2c_dev->base + I2C_COUNT);
}

static void sprd_i2c_send_stop(struct sprd_i2c *i2c_dev, int stop)
{
	u32 tmp = readl(i2c_dev->base + I2C_CTL);

	if (stop)
		writel(tmp & ~STP_EN, i2c_dev->base + I2C_CTL);
	else
		writel(tmp | STP_EN, i2c_dev->base + I2C_CTL);
}

static void sprd_i2c_clear_start(struct sprd_i2c *i2c_dev)
{
	u32 tmp = readl(i2c_dev->base + I2C_CTL);

	writel(tmp & ~I2C_START, i2c_dev->base + I2C_CTL);
}

static void sprd_i2c_clear_ack(struct sprd_i2c *i2c_dev)
{
	u32 tmp = readl(i2c_dev->base + I2C_STATUS);

	writel(tmp & ~I2C_RX_ACK, i2c_dev->base + I2C_STATUS);
}

static void sprd_i2c_clear_irq(struct sprd_i2c *i2c_dev)
{
	u32 tmp = readl(i2c_dev->base + I2C_STATUS);

	writel(tmp & ~I2C_INT, i2c_dev->base + I2C_STATUS);
}

static void sprd_i2c_reset_fifo(struct sprd_i2c *i2c_dev)
{
	writel(I2C_RST, i2c_dev->base + ADDR_RST);
}

static void sprd_i2c_set_devaddr(struct sprd_i2c *i2c_dev, struct i2c_msg *m)
{
	writel(m->addr << 1, i2c_dev->base + I2C_ADDR_CFG);
}

static void sprd_i2c_write_bytes(struct sprd_i2c *i2c_dev, u8 *buf, u32 len)
{
	u32 i;

	for (i = 0; i < len; i++)
		writeb(buf[i], i2c_dev->base + I2C_TX);
}

static void sprd_i2c_read_bytes(struct sprd_i2c *i2c_dev, u8 *buf, u32 len)
{
	u32 i;

	for (i = 0; i < len; i++)
		buf[i] = readb(i2c_dev->base + I2C_RX);
}

static void sprd_i2c_set_full_thld(struct sprd_i2c *i2c_dev, u32 full_thld)
{
	u32 tmp = readl(i2c_dev->base + I2C_CTL);

	tmp &= ~FIFO_AF_LVL_MASK;
	tmp |= full_thld << FIFO_AF_LVL;
	writel(tmp, i2c_dev->base + I2C_CTL);
};

static void sprd_i2c_set_empty_thld(struct sprd_i2c *i2c_dev, u32 empty_thld)
{
	u32 tmp = readl(i2c_dev->base + I2C_CTL);

	tmp &= ~FIFO_AE_LVL_MASK;
	tmp |= empty_thld << FIFO_AE_LVL;
	writel(tmp, i2c_dev->base + I2C_CTL);
};

static void sprd_i2c_set_fifo_full_int(struct sprd_i2c *i2c_dev, int enable)
{
	u32 tmp = readl(i2c_dev->base + I2C_CTL);

	if (enable)
		tmp |= FULL_INTEN;
	else
		tmp &= ~FULL_INTEN;

	writel(tmp, i2c_dev->base + I2C_CTL);
};

static void sprd_i2c_set_fifo_empty_int(struct sprd_i2c *i2c_dev, int enable)
{
	u32 tmp = readl(i2c_dev->base + I2C_CTL);

	if (enable)
		tmp |= EMPTY_INTEN;
	else
		tmp &= ~EMPTY_INTEN;

	writel(tmp, i2c_dev->base + I2C_CTL);
};

static void sprd_i2c_opt_start(struct sprd_i2c *i2c_dev)
{
	u32 tmp = readl(i2c_dev->base + I2C_CTL);

	writel(tmp | I2C_START, i2c_dev->base + I2C_CTL);
}

static void sprd_i2c_opt_mode(struct sprd_i2c *i2c_dev, int rw)
{
	u32 cmd = readl(i2c_dev->base + I2C_CTL) & ~I2C_MODE;

	writel(cmd | rw << 3, i2c_dev->base + I2C_CTL);
}

static void sprd_i2c_data_transfer(struct sprd_i2c *i2c_dev)
{
	u32 i2c_count = i2c_dev->count;
	u32 need_tran = i2c_count <= I2C_FIFO_DEEP ? i2c_count : I2C_FIFO_DEEP;
	struct i2c_msg *msg = i2c_dev->msg;

	if (msg->flags & I2C_M_RD) {
		sprd_i2c_read_bytes(i2c_dev, i2c_dev->buf, i2c_dev->variant->rx_full_threshold);
		i2c_dev->count -= i2c_dev->variant->rx_full_threshold;
		i2c_dev->buf += i2c_dev->variant->rx_full_threshold;

		/*
		 * If the read data count is larger than rx fifo full threshold,
		 * we should enable the rx fifo full interrupt to read data
		 * again.
		 */
		if (i2c_dev->count >= i2c_dev->variant->rx_full_threshold)
			sprd_i2c_set_fifo_full_int(i2c_dev, 1);
	} else {
		sprd_i2c_write_bytes(i2c_dev, i2c_dev->buf, need_tran);
		i2c_dev->buf += need_tran;
		i2c_dev->count -= need_tran;

		/*
		 * If the write data count is arger than tx fifo depth which
		 * means we can not write all data in one time, then we should
		 * enable the tx fifo empty interrupt to write again.
		 */
		if (i2c_count > I2C_FIFO_DEEP)
			sprd_i2c_set_fifo_empty_int(i2c_dev, 1);
	}
}

static int sprd_i2c_handle_msg(struct i2c_adapter *i2c_adap,
			       struct i2c_msg *msg, bool is_last_msg)
{
	struct sprd_i2c *i2c_dev = i2c_adap->algo_data;
	unsigned long time_left;
	bool prepared_irq = i2c_dev->variant->event_mask != 0;
	int ret;

	if (prepared_irq) {
		/* Quiesce old completion IRQs before publishing a new message. */
		disable_irq(i2c_dev->irq);
		sprd_i2c_clear_start(i2c_dev);
		sprd_i2c_clear_irq(i2c_dev);
	}
	i2c_dev->msg = msg;
	i2c_dev->buf = msg->buf;
	i2c_dev->count = msg->len;

	reinit_completion(&i2c_dev->complete);
	sprd_i2c_reset_fifo(i2c_dev);
	sprd_i2c_set_devaddr(i2c_dev, msg);
	sprd_i2c_set_count(i2c_dev, msg->len);

	if (msg->flags & I2C_M_RD) {
		sprd_i2c_opt_mode(i2c_dev, 1);
		sprd_i2c_send_stop(i2c_dev, 1);
	} else {
		sprd_i2c_opt_mode(i2c_dev, 0);
		sprd_i2c_send_stop(i2c_dev, !!is_last_msg);
	}

	/*
	 * We should enable rx fifo full interrupt to get data when receiving
	 * full data.
	 */
	if (msg->flags & I2C_M_RD)
		sprd_i2c_set_fifo_full_int(i2c_dev, 1);
	else
		sprd_i2c_data_transfer(i2c_dev);

	if (prepared_irq) {
		u32 ctl = readl(i2c_dev->base + I2C_CTL);

		/* Do not enable this event with an unprepared/NULL message. */
		writel(ctl | i2c_dev->variant->event_mask,
		       i2c_dev->base + I2C_CTL);
	}
	sprd_i2c_opt_start(i2c_dev);
	if (prepared_irq)
		enable_irq(i2c_dev->irq);

	time_left = wait_for_completion_timeout(&i2c_dev->complete,
				msecs_to_jiffies(I2C_XFER_TIMEOUT));
	ret = time_left ? i2c_dev->err : -ETIMEDOUT;
	if (prepared_irq) {
		u32 ctl;

		/* Wait out the threaded handler before invalidating its message. */
		disable_irq(i2c_dev->irq);
		if (ret)
			sprd_i2c_send_stop(i2c_dev, 1);
		sprd_i2c_clear_start(i2c_dev);
		sprd_i2c_clear_irq(i2c_dev);
		ctl = readl(i2c_dev->base + I2C_CTL);
		writel(ctl & ~(i2c_dev->variant->event_mask | FULL_INTEN | EMPTY_INTEN),
		       i2c_dev->base + I2C_CTL);
		WRITE_ONCE(i2c_dev->msg, NULL);
		enable_irq(i2c_dev->irq);
	}
	return ret;
}

static int sprd_i2c_xfer(struct i2c_adapter *i2c_adap,
			 struct i2c_msg *msgs, int num)
{
	struct sprd_i2c *i2c_dev = i2c_adap->algo_data;
	int im, ret;

	ret = pm_runtime_resume_and_get(i2c_dev->dev);
	if (ret < 0)
		return ret;

	for (im = 0; im < num - 1; im++) {
		ret = sprd_i2c_handle_msg(i2c_adap, &msgs[im], 0);
		if (ret)
			goto err_msg;
	}

	ret = sprd_i2c_handle_msg(i2c_adap, &msgs[im++], 1);

err_msg:
	pm_runtime_mark_last_busy(i2c_dev->dev);
	pm_runtime_put_autosuspend(i2c_dev->dev);

	return ret < 0 ? ret : im;
}

static u32 sprd_i2c_func(struct i2c_adapter *adap)
{
	return I2C_FUNC_I2C | I2C_FUNC_SMBUS_EMUL;
}

static const struct i2c_algorithm sprd_i2c_algo = {
	.xfer = sprd_i2c_xfer,
	.functionality = sprd_i2c_func,
};

static void sprd_i2c_set_clk(struct sprd_i2c *i2c_dev, u32 freq)
{
	u32 apb_clk = i2c_dev->src_clk;
	/*
	 * From I2C databook, the prescale calculation formula:
	 * prescale = freq_i2c / (4 * freq_scl) - 1;
	 */
	u32 i2c_dvd = apb_clk / (4 * freq) - 1;
	/*
	 * From I2C databook, the high period of SCL clock is recommended as
	 * 40% (2/5), and the low period of SCL clock is recommended as 60%
	 * (3/5), then the formula should be:
	 * high = (prescale * 2 * 2) / 5
	 * low = (prescale * 2 * 3) / 5
	 */
	u32 high = ((i2c_dvd << 1) * 2) / 5;
	u32 low = ((i2c_dvd << 1) * 3) / 5;
	u32 div0 = I2C_ADDR_DVD0_CALC(high, low);
	u32 div1 = I2C_ADDR_DVD1_CALC(high, low);

	writel(div0, i2c_dev->base + ADDR_DVD0);
	writel(div1, i2c_dev->base + ADDR_DVD1);

	/* Start hold timing = hold time(us) * source clock */
	if (freq == I2C_MAX_FAST_MODE_FREQ)
		writel((6 * apb_clk) / 10000000, i2c_dev->base + ADDR_STA0_DVD);
	else if (freq == I2C_MAX_STANDARD_MODE_FREQ)
		writel((4 * apb_clk) / 1000000, i2c_dev->base + ADDR_STA0_DVD);
}

static void sprd_i2c_enable(struct sprd_i2c *i2c_dev)
{
	u32 tmp = I2C_DVD_OPT;

	writel(tmp, i2c_dev->base + I2C_CTL);

	sprd_i2c_set_full_thld(i2c_dev, i2c_dev->variant->rx_full_threshold);
	sprd_i2c_set_empty_thld(i2c_dev, I2C_FIFO_EMPTY_THLD);

	sprd_i2c_set_clk(i2c_dev, i2c_dev->bus_freq);
	sprd_i2c_reset_fifo(i2c_dev);
	sprd_i2c_clear_irq(i2c_dev);

	tmp = readl(i2c_dev->base + I2C_CTL);
	writel(tmp | I2C_EN | I2C_INT_EN, i2c_dev->base + I2C_CTL);
}

static irqreturn_t sprd_i2c_isr_thread(int irq, void *dev_id)
{
	struct sprd_i2c *i2c_dev = dev_id;
	struct i2c_msg *msg = READ_ONCE(i2c_dev->msg);
	bool ack = i2c_dev->variant->event_mask ? READ_ONCE(i2c_dev->msg_ack) :
		!(readl(i2c_dev->base + I2C_STATUS) & I2C_RX_ACK);
	u32 i2c_tran;
	if (!msg) {
		return IRQ_HANDLED;
	}


	if (msg->flags & I2C_M_RD)
		i2c_tran = i2c_dev->count >= i2c_dev->variant->rx_full_threshold;
	else
		i2c_tran = i2c_dev->count;

	/*
	 * If we got one ACK from target when writing data, and we did not
	 * finish this transmission (i2c_tran is not zero), then we should
	 * continue to write data.
	 *
	 * For reading data, ack is always true, if i2c_tran is not 0 which
	 * means we still need to contine to read data from target.
	 */
	if (i2c_tran && ack) {
		sprd_i2c_data_transfer(i2c_dev);
		return IRQ_HANDLED;
	}

	i2c_dev->err = 0;

	/*
	 * If we did not get one ACK from target when writing data, we should
	 * return -EIO to notify users.
	 */
	if (!ack)
		i2c_dev->err = -EIO;
	else if (msg->flags & I2C_M_RD && i2c_dev->count)
		sprd_i2c_read_bytes(i2c_dev, i2c_dev->buf, i2c_dev->count);

	/* Transmission is done and clear ack and start operation */
	sprd_i2c_clear_ack(i2c_dev);
	sprd_i2c_clear_start(i2c_dev);
	complete(&i2c_dev->complete);

	return IRQ_HANDLED;
}

static irqreturn_t sprd_i2c_isr(int irq, void *dev_id)
{
	struct sprd_i2c *i2c_dev = dev_id;
	struct i2c_msg *msg = READ_ONCE(i2c_dev->msg);
	bool ack = !(readl(i2c_dev->base + I2C_STATUS) & I2C_RX_ACK);
	u32 i2c_tran;
	if (!msg) {
		sprd_i2c_clear_irq(i2c_dev);
		return IRQ_HANDLED;
	}
	if (i2c_dev->variant->event_mask)
		WRITE_ONCE(i2c_dev->msg_ack, ack);


	if (msg->flags & I2C_M_RD)
		i2c_tran = i2c_dev->count >= i2c_dev->variant->rx_full_threshold;
	else
		i2c_tran = i2c_dev->count;

	/*
	 * If we did not get one ACK from target when writing data, then we
	 * should finish this transmission since we got some errors.
	 *
	 * When writing data, if i2c_tran == 0 which means we have writen
	 * done all data, then we can finish this transmission.
	 *
	 * When reading data, if conut < rx fifo full threshold, which
	 * means we can read all data in one time, then we can finish this
	 * transmission too.
	 */
	if (!i2c_tran || !ack) {
		sprd_i2c_clear_start(i2c_dev);
		sprd_i2c_clear_irq(i2c_dev);
	}

	sprd_i2c_set_fifo_empty_int(i2c_dev, 0);
	sprd_i2c_set_fifo_full_int(i2c_dev, 0);

	return IRQ_WAKE_THREAD;
}

static int sprd_i2c_clk_init(struct sprd_i2c *i2c_dev)
{
	struct clk *clk_i2c, *clk_parent;

	clk_i2c = devm_clk_get(i2c_dev->dev, "i2c");
	if (IS_ERR(clk_i2c)) {
		dev_warn(i2c_dev->dev, "i2c%d can't get the i2c clock\n",
			 i2c_dev->adap.nr);
		clk_i2c = NULL;
	}

	clk_parent = devm_clk_get(i2c_dev->dev, "source");
	if (IS_ERR(clk_parent)) {
		dev_warn(i2c_dev->dev, "i2c%d can't get the source clock\n",
			 i2c_dev->adap.nr);
		clk_parent = NULL;
	}

	if (clk_set_parent(clk_i2c, clk_parent))
		i2c_dev->src_clk = clk_get_rate(clk_i2c);
	else
		i2c_dev->src_clk = 26000000;

	dev_dbg(i2c_dev->dev, "i2c%d set source clock is %d\n",
		i2c_dev->adap.nr, i2c_dev->src_clk);

	i2c_dev->clk = devm_clk_get(i2c_dev->dev, "enable");
	if (IS_ERR(i2c_dev->clk)) {
		dev_err(i2c_dev->dev, "i2c%d can't get the enable clock\n",
			i2c_dev->adap.nr);
		return PTR_ERR(i2c_dev->clk);
	}

	return 0;
}

static int sprd_i2c_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct sprd_i2c *i2c_dev;
	u32 prop;
	int ret;

	pdev->id = of_alias_get_id(dev->of_node, "i2c");

	i2c_dev = devm_kzalloc(dev, sizeof(struct sprd_i2c), GFP_KERNEL);
	if (!i2c_dev)
		return -ENOMEM;

	i2c_dev->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(i2c_dev->base))
		return PTR_ERR(i2c_dev->base);

	i2c_dev->irq = platform_get_irq(pdev, 0);
	if (i2c_dev->irq < 0)
		return i2c_dev->irq;

	i2c_set_adapdata(&i2c_dev->adap, i2c_dev);
	init_completion(&i2c_dev->complete);
	snprintf(i2c_dev->adap.name, sizeof(i2c_dev->adap.name),
		 "%s", "sprd-i2c");

	i2c_dev->bus_freq = I2C_MAX_STANDARD_MODE_FREQ;
	i2c_dev->adap.owner = THIS_MODULE;
	i2c_dev->dev = dev;
	i2c_dev->variant = of_device_get_match_data(dev);
	if (!i2c_dev->variant)
		i2c_dev->variant = &sc9860_variant;
	i2c_dev->adap.retries = 3;
	i2c_dev->adap.algo = &sprd_i2c_algo;
	i2c_dev->adap.algo_data = i2c_dev;
	i2c_dev->adap.dev.parent = dev;
	i2c_dev->adap.nr = pdev->id;
	i2c_dev->adap.dev.of_node = dev->of_node;

	if (!of_property_read_u32(dev->of_node, "clock-frequency", &prop))
		i2c_dev->bus_freq = prop;

	/* We only support 100k and 400k now, otherwise will return error. */
	if (i2c_dev->bus_freq != I2C_MAX_STANDARD_MODE_FREQ &&
	    i2c_dev->bus_freq != I2C_MAX_FAST_MODE_FREQ)
		return -EINVAL;

	ret = sprd_i2c_clk_init(i2c_dev);
	if (ret)
		return ret;

	platform_set_drvdata(pdev, i2c_dev);

	ret = clk_prepare_enable(i2c_dev->clk);
	if (ret)
		return ret;

	sprd_i2c_enable(i2c_dev);

	pm_runtime_set_autosuspend_delay(i2c_dev->dev, SPRD_I2C_PM_TIMEOUT);
	pm_runtime_use_autosuspend(i2c_dev->dev);
	pm_runtime_set_active(i2c_dev->dev);
	pm_runtime_enable(i2c_dev->dev);

	ret = pm_runtime_get_sync(i2c_dev->dev);
	if (ret < 0)
		goto err_rpm_put;

	ret = devm_request_threaded_irq(dev, i2c_dev->irq,
		sprd_i2c_isr, sprd_i2c_isr_thread,
		IRQF_NO_SUSPEND | IRQF_ONESHOT,
		pdev->name, i2c_dev);
	if (ret) {
		dev_err(&pdev->dev, "failed to request irq %d\n", i2c_dev->irq);
		goto err_rpm_put;
	}

	ret = i2c_add_numbered_adapter(&i2c_dev->adap);
	if (ret) {
		dev_err(&pdev->dev, "add adapter failed\n");
		goto err_rpm_put;
	}

	pm_runtime_mark_last_busy(i2c_dev->dev);
	pm_runtime_put_autosuspend(i2c_dev->dev);
	return 0;

err_rpm_put:
	pm_runtime_put_noidle(i2c_dev->dev);
	pm_runtime_disable(i2c_dev->dev);
	clk_disable_unprepare(i2c_dev->clk);
	return ret;
}

static void sprd_i2c_remove(struct platform_device *pdev)
{
	struct sprd_i2c *i2c_dev = platform_get_drvdata(pdev);
	int ret;

	ret = pm_runtime_get_sync(i2c_dev->dev);
	if (ret < 0)
		dev_err(&pdev->dev, "Failed to resume device (%pe)\n", ERR_PTR(ret));

	i2c_del_adapter(&i2c_dev->adap);

	if (ret >= 0)
		clk_disable_unprepare(i2c_dev->clk);

	pm_runtime_put_noidle(i2c_dev->dev);
	pm_runtime_disable(i2c_dev->dev);
}

static int __maybe_unused sprd_i2c_suspend_noirq(struct device *dev)
{
	struct sprd_i2c *i2c_dev = dev_get_drvdata(dev);

	i2c_mark_adapter_suspended(&i2c_dev->adap);
	return pm_runtime_force_suspend(dev);
}

static int __maybe_unused sprd_i2c_resume_noirq(struct device *dev)
{
	struct sprd_i2c *i2c_dev = dev_get_drvdata(dev);

	i2c_mark_adapter_resumed(&i2c_dev->adap);
	return pm_runtime_force_resume(dev);
}

static int __maybe_unused sprd_i2c_runtime_suspend(struct device *dev)
{
	struct sprd_i2c *i2c_dev = dev_get_drvdata(dev);

	clk_disable_unprepare(i2c_dev->clk);

	return 0;
}

static int __maybe_unused sprd_i2c_runtime_resume(struct device *dev)
{
	struct sprd_i2c *i2c_dev = dev_get_drvdata(dev);
	int ret;

	ret = clk_prepare_enable(i2c_dev->clk);
	if (ret)
		return ret;

	sprd_i2c_enable(i2c_dev);

	return 0;
}

static const struct dev_pm_ops sprd_i2c_pm_ops = {
	SET_RUNTIME_PM_OPS(sprd_i2c_runtime_suspend,
			   sprd_i2c_runtime_resume, NULL)

	SET_NOIRQ_SYSTEM_SLEEP_PM_OPS(sprd_i2c_suspend_noirq,
				      sprd_i2c_resume_noirq)
};

static const struct of_device_id sprd_i2c_of_match[] = {
	{ .compatible = "sprd,ums9632-i2c", .data = &ums9632_variant },
	{ .compatible = "sprd,sc9860-i2c", .data = &sc9860_variant },
	{},
};
MODULE_DEVICE_TABLE(of, sprd_i2c_of_match);

/* Read-only U30 Pro attribute: no FIFO reads or direct control writes.
 * Upstream runtime_resume reinitializes the controller if it was suspended;
 * this attribute is not a promise of a completely passive MMIO snapshot.
 * Take the real adapter lock and runtime PM reference so register observations
 * cannot race a transfer or access a gated MMIO block. Device transfers and
 * enable/IRQ programming above remain byte-for-byte upstream behavior.
 */
static ssize_t u30pro_registers_show(struct device *dev,
                                   struct device_attribute *attr, char *buf)
{
 static const struct { const char *name; u32 offset; } regs[] = {
  { "CTL", I2C_CTL }, { "ADDR", I2C_ADDR_CFG }, { "COUNT", I2C_COUNT },
  { "STATUS", I2C_STATUS }, { "HSMODE", I2C_HSMODE_CFG },
  { "VERSION", I2C_VERSION }, { "DVD0", ADDR_DVD0 },
  { "DVD1", ADDR_DVD1 }, { "START_HOLD", ADDR_STA0_DVD },
 };
 struct sprd_i2c *i2c_dev = dev_get_drvdata(dev);
 int ret, i, len = 0;

 if (!i2c_dev || !of_device_is_compatible(dev->of_node, "sprd,ums9632-i2c") ||
     of_alias_get_id(dev->of_node, "i2c") != 6)
  return -ENODEV;
 i2c_lock_bus(&i2c_dev->adap, I2C_LOCK_SEGMENT);
 ret = pm_runtime_resume_and_get(dev);
 if (ret < 0)
  goto unlock;
 len = sysfs_emit(buf, "adapter=%d source_hz=%u bus_hz=%u\n",
                  i2c_dev->adap.nr, i2c_dev->src_clk, i2c_dev->bus_freq);
 for (i = 0; i < ARRAY_SIZE(regs); i++)
  len += sysfs_emit_at(buf, len, "%s[%02x]=%08x\n", regs[i].name,
                       regs[i].offset, readl(i2c_dev->base + regs[i].offset));
 pm_runtime_mark_last_busy(dev);
 pm_runtime_put_autosuspend(dev);
 ret = len;
 unlock:
 i2c_unlock_bus(&i2c_dev->adap, I2C_LOCK_SEGMENT);
 return ret;
}
static DEVICE_ATTR(u30pro_registers, 0400, u30pro_registers_show, NULL);
static struct attribute *sprd_i2c_u30pro_attrs[] = {
 &dev_attr_u30pro_registers.attr,
 NULL,
};
static umode_t sprd_i2c_u30pro_visible(struct kobject *kobj,
                                     struct attribute *attr, int index)
{
 struct device *dev = kobj_to_dev(kobj);

 return of_device_is_compatible(dev->of_node, "sprd,ums9632-i2c") &&
        of_alias_get_id(dev->of_node, "i2c") == 6 ? 0400 : 0;
}
static const struct attribute_group sprd_i2c_u30pro_group = {
 .attrs = sprd_i2c_u30pro_attrs,
 .is_visible = sprd_i2c_u30pro_visible,
};
static const struct attribute_group *sprd_i2c_u30pro_groups[] = {
 &sprd_i2c_u30pro_group,
 NULL,
};

static struct platform_driver sprd_i2c_driver = {
	.probe = sprd_i2c_probe,
	.remove_new = sprd_i2c_remove,
	.driver = {
		   .name = "sprd-i2c",
		   .of_match_table = sprd_i2c_of_match,
		   .dev_groups = sprd_i2c_u30pro_groups,
		   .pm = &sprd_i2c_pm_ops,
	},
};

module_platform_driver(sprd_i2c_driver);

MODULE_DESCRIPTION("Spreadtrum I2C controller driver");
MODULE_LICENSE("GPL v2");
