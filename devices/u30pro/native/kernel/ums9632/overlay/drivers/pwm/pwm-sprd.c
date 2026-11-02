// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2022 Spreadtrum Communications Inc.
 */

#include <linux/clk.h>
#include <linux/debugfs.h>
#include <linux/err.h>
#include <linux/io.h>
#include <linux/math64.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/pwm.h>
#include <linux/seq_file.h>

#define SPRD_PWM_PRESCALE	0x0
#define SPRD_PWM_MOD		0x4
#define SPRD_PWM_DUTY		0x8
#define SPRD_PWM_ENABLE		0x18

#define SPRD_PWM_MOD_MAX	GENMASK(15, 0)
#define SPRD_PWM_MOD_DEFAULT	GENMASK(9, 0)
#define SPRD_PWM_DUTY_MSK	GENMASK(15, 0)
#define SPRD_PWM_PRESCALE_MSK	GENMASK(7, 0)
#define SPRD_PWM_ENABLE_BIT	BIT(0)

#define SPRD_PWM_CHN_NUM	4
#define SPRD_PWM_CHN_CLKS_NUM	2
#define SPRD_PWM_CHN_OUTPUT_CLK	1

struct sprd_pwm_chn {
	struct clk_bulk_data clks[SPRD_PWM_CHN_CLKS_NUM];
	u32 clk_rate;
};

struct sprd_pwm_data {
	int reg_shift;
};

struct sprd_pwm_chip {
	void __iomem *base;
	struct device *dev;
	struct pwm_chip *chip;
	const struct sprd_pwm_data *pdata;
	u32 num_pwms;
	struct sprd_pwm_chn chn[SPRD_PWM_CHN_NUM];
	u32 mod[SPRD_PWM_CHN_NUM];
};

static const struct sprd_pwm_data ums512_data = {
	.reg_shift = 5,
};

static const struct sprd_pwm_data ums9620_data = {
	.reg_shift = 14,
};

/*
 * The list of clocks required by PWM channels, and each channel has 2 clocks:
 * enable clock and pwm clock.
 */
static const char * const sprd_pwm_clks[] = {
	"enable0", "pwm0",
	"enable1", "pwm1",
	"enable2", "pwm2",
	"enable3", "pwm3",
};

static u32 sprd_pwm_read(struct sprd_pwm_chip *spc, u32 hwid, u32 reg)
{
	u32 offset = reg + (hwid << spc->pdata->reg_shift);

	return readl_relaxed(spc->base + offset);
}

static void sprd_pwm_write(struct sprd_pwm_chip *spc, u32 hwid,
			   u32 reg, u32 val)
{
	u32 offset = reg + (hwid << spc->pdata->reg_shift);

	writel_relaxed(val, spc->base + offset);
}

static int sprd_pwm_get_state(struct pwm_chip *chip, struct pwm_device *pwm,
			      struct pwm_state *state)
{
	struct sprd_pwm_chip *spc = pwmchip_get_drvdata(chip);
	struct sprd_pwm_chn *chn = &spc->chn[pwm->hwpwm];
	u32 val, duty, prescale;
	u64 tmp;
	int ret;

	/*
	 * The clocks to PWM channel has to be enabled first before
	 * reading to the registers.
	 */
	ret = clk_bulk_prepare_enable(SPRD_PWM_CHN_CLKS_NUM, chn->clks);
	if (ret) {
		dev_err(spc->dev, "failed to enable pwm%u clocks\n",
			pwm->hwpwm);
		return ret;
	}

	val = sprd_pwm_read(spc, pwm->hwpwm, SPRD_PWM_ENABLE);
	if (val & SPRD_PWM_ENABLE_BIT)
		state->enabled = true;
	else
		state->enabled = false;

	/*
	 * The hardware provides a counter that is feed by the source clock.
	 * The period length is (PRESCALE + 1) * (MOD + 1) counter steps.
	 * The duty cycle length is (PRESCALE + 1) * DUTY counter steps.
	 * Thus the period_ns and duty_ns calculation formula should be:
	 * period_ns = NSEC_PER_SEC * (prescale + 1) * (mod + 1) / clk_rate
	 * duty_ns = NSEC_PER_SEC * (prescale + 1) * duty / clk_rate
	 */
	val = sprd_pwm_read(spc, pwm->hwpwm, SPRD_PWM_PRESCALE);
	prescale = val & SPRD_PWM_PRESCALE_MSK;
	tmp = (prescale + 1) * NSEC_PER_SEC * (spc->mod[pwm->hwpwm] + 1);
	state->period = DIV_ROUND_CLOSEST_ULL(tmp, chn->clk_rate);

	val = sprd_pwm_read(spc, pwm->hwpwm, SPRD_PWM_DUTY);
	duty = val & SPRD_PWM_DUTY_MSK;
	tmp = (prescale + 1) * NSEC_PER_SEC * duty;
	state->duty_cycle = DIV_ROUND_CLOSEST_ULL(tmp, chn->clk_rate);
	state->polarity = PWM_POLARITY_NORMAL;

	/* Disable PWM clocks if the PWM channel is not in enable state. */
	if (!state->enabled)
		clk_bulk_disable_unprepare(SPRD_PWM_CHN_CLKS_NUM, chn->clks);

	return 0;
}

static int sprd_pwm_config(struct sprd_pwm_chip *spc, struct pwm_device *pwm,
			   int duty_ns, int period_ns)
{
	struct sprd_pwm_chn *chn = &spc->chn[pwm->hwpwm];
	u32 prescale, duty, mod;
	u64 tmp;

	/*
	 * The hardware provides a counter that is feed by the source clock.
	 * The period length is (PRESCALE + 1) * (MOD + 1) counter steps.
	 * The duty cycle length is (PRESCALE + 1) * DUTY counter steps.
	 *
	 * To keep the maths simple we're always using MOD = SPRD_PWM_MOD_MAX.
	 * The value for PRESCALE is selected such that the resulting period
	 * gets the maximal length not bigger than the requested one with the
	 * given settings (MOD = SPRD_PWM_MOD_MAX and input clock).
	 */
	mod = spc->mod[pwm->hwpwm];
	tmp = (u64)duty_ns * (mod + 1);
	duty = DIV_ROUND_CLOSEST_ULL(tmp, period_ns);

	tmp = (u64)chn->clk_rate * period_ns;
	do_div(tmp, NSEC_PER_SEC);
	prescale = DIV_ROUND_CLOSEST_ULL(tmp, mod + 1);
	if (prescale < 1)
		prescale = 1;
	prescale--;

	if (prescale > SPRD_PWM_PRESCALE_MSK)
		prescale = SPRD_PWM_PRESCALE_MSK;

	/*
	 * Note: Writing DUTY triggers the hardware to actually apply the
	 * values written to MOD and DUTY to the output, so must keep writing
	 * DUTY last.
	 *
	 * The hardware can ensures that current running period is completed
	 * before changing a new configuration to avoid mixed settings.
	 */
	sprd_pwm_write(spc, pwm->hwpwm, SPRD_PWM_PRESCALE, prescale);
	sprd_pwm_write(spc, pwm->hwpwm, SPRD_PWM_MOD, mod);
	sprd_pwm_write(spc, pwm->hwpwm, SPRD_PWM_DUTY, duty);

	return 0;
}

static int sprd_pwm_apply(struct pwm_chip *chip, struct pwm_device *pwm,
			  const struct pwm_state *state)
{
	struct sprd_pwm_chip *spc = pwmchip_get_drvdata(chip);
	struct sprd_pwm_chn *chn = &spc->chn[pwm->hwpwm];
	struct pwm_state *cstate = &pwm->state;
	int ret;

	if (state->polarity != PWM_POLARITY_NORMAL)
		return -EINVAL;

	if (state->enabled) {
		if (!cstate->enabled) {
			/*
			 * The clocks to PWM channel has to be enabled first
			 * before writing to the registers.
			 */
			ret = clk_bulk_prepare_enable(SPRD_PWM_CHN_CLKS_NUM,
						      chn->clks);
			if (ret) {
				dev_err(spc->dev,
					"failed to enable pwm%u clocks\n",
					pwm->hwpwm);
				return ret;
			}
		}

		ret = sprd_pwm_config(spc, pwm, state->duty_cycle,
				      state->period);
		if (ret)
			return ret;

		sprd_pwm_write(spc, pwm->hwpwm, SPRD_PWM_ENABLE, 1);
	} else if (cstate->enabled) {
		/*
		 * Note: After setting SPRD_PWM_ENABLE to zero, the controller
		 * will not wait for current period to be completed, instead it
		 * will stop the PWM channel immediately.
		 */
		sprd_pwm_write(spc, pwm->hwpwm, SPRD_PWM_ENABLE, 0);

		clk_bulk_disable_unprepare(SPRD_PWM_CHN_CLKS_NUM, chn->clks);
	}

	return 0;
}

static const struct pwm_ops sprd_pwm_ops = {
	.apply = sprd_pwm_apply,
	.get_state = sprd_pwm_get_state,
};

static int sprd_pwm_clk_init(struct sprd_pwm_chip *spc)
{
	struct clk *clk_pwm;
	int ret, i;

	for (i = 0; i < spc->num_pwms; i++) {
		struct sprd_pwm_chn *chn = &spc->chn[i];
		int j;

		for (j = 0; j < SPRD_PWM_CHN_CLKS_NUM; ++j)
			chn->clks[j].id =
				sprd_pwm_clks[i * SPRD_PWM_CHN_CLKS_NUM + j];

		ret = devm_clk_bulk_get(spc->dev, SPRD_PWM_CHN_CLKS_NUM,
					chn->clks);
		if (ret) {
			if (ret == -ENOENT)
				break;

			return dev_err_probe(spc->dev, ret,
					     "failed to get channel clocks\n");
		}

		clk_pwm = chn->clks[SPRD_PWM_CHN_OUTPUT_CLK].clk;
		chn->clk_rate = clk_get_rate(clk_pwm);
	}

	if (!i) {
		dev_err(spc->dev, "no available PWM clocks\n");
		return -ENODEV;
	}

	return 0;
}

static int sprd_pwm_get_mod(struct platform_device *pdev)
{
	int i, ret;
	struct sprd_pwm_chip *spc = platform_get_drvdata(pdev);

	ret = of_property_read_u32_array(pdev->dev.of_node,
					 "sprd,mod", spc->mod, spc->num_pwms);
	if (ret) {
		for (i = 0; i < spc->num_pwms; i++)
			spc->mod[i] = SPRD_PWM_MOD_DEFAULT;
	}

	return ret;
}

/* debugfs "sprd-pwm-regs": what the hardware holds for every enabled channel (the cached request that /sys/kernel/debug/pwm shows can differ) */
static int sprd_pwm_regs_show(struct seq_file *m, void *unused)
{
	struct sprd_pwm_chip *spc = dev_get_drvdata(m->private);
	u32 i;

	for (i = 0; i < spc->num_pwms; i++) {
		if (!spc->chip->pwms[i].state.enabled)
			continue;
		seq_printf(m, "pwm%u: prescale=%u mod=%u duty=%u enable=%u clk=%u Hz\n", i,
			   sprd_pwm_read(spc, i, SPRD_PWM_PRESCALE) & SPRD_PWM_PRESCALE_MSK, sprd_pwm_read(spc, i, SPRD_PWM_MOD),
			   sprd_pwm_read(spc, i, SPRD_PWM_DUTY) & SPRD_PWM_DUTY_MSK, sprd_pwm_read(spc, i, SPRD_PWM_ENABLE) & 1, spc->chn[i].clk_rate);
	}
	return 0;
}

static int sprd_pwm_probe(struct platform_device *pdev)
{
	struct sprd_pwm_chip *spc;
	const void *priv;
	int ret;

	spc = devm_kzalloc(&pdev->dev, sizeof(*spc), GFP_KERNEL);
	if (!spc)
		return -ENOMEM;

	spc->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(spc->base))
		return PTR_ERR(spc->base);

	priv = of_device_get_match_data(&pdev->dev);
	if (!priv) {
		dev_err(&pdev->dev, "get regs shift failed!\n");
		return -EINVAL;
	}
	spc->pdata = priv;

	spc->dev = &pdev->dev;
	platform_set_drvdata(pdev, spc);

	ret = of_property_read_u32(pdev->dev.of_node, "sprd,pwm_num",
				   &spc->num_pwms);
	if (ret)
		return ret;
	if (spc->num_pwms > SPRD_PWM_CHN_NUM) {
		spc->num_pwms = SPRD_PWM_CHN_NUM;
		dev_err(&pdev->dev, "pwms exceed the maximum!\n");
	}

	ret = sprd_pwm_clk_init(spc);
	if (ret)
		return ret;

	ret = sprd_pwm_get_mod(pdev);
	if (ret)
		dev_err(&pdev->dev, "get pwm mod from dt failed!\n");

	spc->chip = devm_pwmchip_alloc(&pdev->dev, spc->num_pwms, 0);
	if (IS_ERR(spc->chip))
		return PTR_ERR(spc->chip);
	pwmchip_set_drvdata(spc->chip, spc);
	spc->chip->ops = &sprd_pwm_ops;

	ret = pwmchip_add(spc->chip);
	if (ret)
		dev_err(&pdev->dev, "failed to add PWM chip\n");
	else
		debugfs_create_devm_seqfile(&pdev->dev, "sprd-pwm-regs", NULL, sprd_pwm_regs_show);

	return ret;
}

static void sprd_pwm_remove(struct platform_device *pdev)
{
	struct sprd_pwm_chip *spc = platform_get_drvdata(pdev);

	pwmchip_remove(spc->chip);
}

static const struct of_device_id sprd_pwm_of_match[] = {
	{ .compatible = "sprd,ums512-pwm",	.data = (void *)&ums512_data},
	{ .compatible = "sprd,ums9620-pwm",	.data = (void *)&ums9620_data},
	{ },
};
MODULE_DEVICE_TABLE(of, sprd_pwm_of_match);

static struct platform_driver sprd_pwm_driver = {
	.driver = {
		.name = "sprd-pwm",
		.of_match_table = sprd_pwm_of_match,
	},
	.probe = sprd_pwm_probe,
	.remove_new = sprd_pwm_remove,
};

module_platform_driver(sprd_pwm_driver);

MODULE_DESCRIPTION("Spreadtrum PWM Driver");
MODULE_LICENSE("GPL v2");
