// SPDX-License-Identifier: GPL-2.0
/*
 * True random number generator of the UMS9632 public crypto engine ("sprd,sprd-trng").
 *
 * Written from the register description of the hardware (the vendor driver is under another licence and is not used as a source):
 *
 *	0x018 CE_CLK		bit 24: TRNG clock enable
 *	0x200 RNG_EN		0x1ff03 enables the generator
 *	0x208 RNG_DATA		one 32-bit random word per read
 *	0x214 RNG_WORK_STATUS	bit 1: a word is available
 *	0x228 RNG_MODE		bit 0: true random mode
 *
 * Clocks (device tree): "ce-pub-eb" (the engine's bus gate), "ap-ce-clk" (its functional clock, parent "source").  The generator is
 * only powered while the hw_random core uses it (init / cleanup).
 */
#include <linux/bits.h>
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/hw_random.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mod_devicetable.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>

#define REG_CE_CLK		0x018
#define REG_RNG_EN		0x200
#define REG_RNG_DATA		0x208
#define REG_RNG_STATUS		0x214
#define REG_RNG_MODE		0x228

#define CE_CLK_TRNG_EN		BIT(24)
#define RNG_ENABLE		0x1ff03
#define RNG_DATA_VALID		BIT(1)
#define RNG_MODE_TRUE		BIT(0)

#define TRNG_MAX_BYTES		32
#define TRNG_POLL_TRIES		10

struct sprd_trng {
	void __iomem *base;
	struct hwrng rng;
	struct clk *clk;
	struct clk *ce_eb;
	struct mutex lock;
};

#define to_trng(p)	container_of(p, struct sprd_trng, rng)

static int sprd_trng_init(struct hwrng *rng)
{
	struct sprd_trng *t = to_trng(rng);
	int ret;

	ret = clk_prepare_enable(t->ce_eb);
	if (ret)
		return ret;
	ret = clk_prepare_enable(t->clk);
	if (ret) {
		clk_disable_unprepare(t->ce_eb);
		return ret;
	}

	mutex_lock(&t->lock);
	writel(readl(t->base + REG_CE_CLK) | CE_CLK_TRNG_EN, t->base + REG_CE_CLK);
	writel(readl(t->base + REG_RNG_EN) | RNG_ENABLE, t->base + REG_RNG_EN);
	writel(readl(t->base + REG_RNG_MODE) | RNG_MODE_TRUE, t->base + REG_RNG_MODE);
	mutex_unlock(&t->lock);

	return 0;
}

static void sprd_trng_cleanup(struct hwrng *rng)
{
	struct sprd_trng *t = to_trng(rng);

	mutex_lock(&t->lock);
	writel(readl(t->base + REG_RNG_EN) & ~RNG_ENABLE, t->base + REG_RNG_EN);
	writel(readl(t->base + REG_CE_CLK) & ~CE_CLK_TRNG_EN, t->base + REG_CE_CLK);
	mutex_unlock(&t->lock);

	clk_disable_unprepare(t->clk);
	clk_disable_unprepare(t->ce_eb);
}

static int sprd_trng_read(struct hwrng *rng, void *buf, size_t max, bool wait)
{
	struct sprd_trng *t = to_trng(rng);
	u32 *out = buf;
	size_t words;
	int tries = 0, got = 0;

	max = min_t(size_t, max, TRNG_MAX_BYTES);
	words = max / sizeof(u32);
	if (!words)
		return 0;

	mutex_lock(&t->lock);
	while (got < words) {
		if (readl(t->base + REG_RNG_STATUS) & RNG_DATA_VALID) {
			out[got++] = readl(t->base + REG_RNG_DATA);
			tries = 0;
			continue;
		}
		if (!wait || ++tries >= TRNG_POLL_TRIES)
			break;
		usleep_range(1000, 1500);
	}
	mutex_unlock(&t->lock);

	return got * sizeof(u32);
}

static int sprd_trng_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct clk *parent;
	struct sprd_trng *t;

	t = devm_kzalloc(dev, sizeof(*t), GFP_KERNEL);
	if (!t)
		return -ENOMEM;

	t->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(t->base))
		return PTR_ERR(t->base);

	t->ce_eb = devm_clk_get(dev, "ce-pub-eb");
	if (IS_ERR(t->ce_eb))
		return dev_err_probe(dev, PTR_ERR(t->ce_eb), "no ce-pub-eb clock\n");
	t->clk = devm_clk_get(dev, "ap-ce-clk");
	if (IS_ERR(t->clk))
		return dev_err_probe(dev, PTR_ERR(t->clk), "no ap-ce-clk clock\n");
	parent = devm_clk_get(dev, "source");
	if (IS_ERR(parent))
		return dev_err_probe(dev, PTR_ERR(parent), "no source clock\n");
	clk_set_parent(t->clk, parent);

	mutex_init(&t->lock);
	t->rng.name = "sprd-trng";
	t->rng.init = sprd_trng_init;
	t->rng.cleanup = sprd_trng_cleanup;
	t->rng.read = sprd_trng_read;
	t->rng.quality = 512;	/* bits of entropy per 1024 bits */

	return devm_hwrng_register(dev, &t->rng);
}

static const struct of_device_id sprd_trng_of_match[] = {
	{ .compatible = "sprd,sprd-trng" },
	{ /* sentinel */ },
};

static struct platform_driver sprd_trng_driver = {
	.probe = sprd_trng_probe,
	.driver = {
		.name = "u30pro-sprd-trng",
		.of_match_table = sprd_trng_of_match,
	},
};
module_platform_driver(sprd_trng_driver);

MODULE_DESCRIPTION("Unisoc true random number generator");
MODULE_LICENSE("GPL");
