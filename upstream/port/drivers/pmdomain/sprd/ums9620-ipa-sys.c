// SPDX-License-Identifier: GPL-2.0-only
/*
 * Unisoc UMS9620 (qogirn6pro) IPA subsystem power domain.
 * The USB 3.1 controller/PHY and the IPA block live in this domain. Ported from the Unisoc 5.4 kernel
 * (drivers/net/sprd/sipa_sys, v3 callbacks), without the SIPA network dependencies.
 *
 * Copyright (C) 2019 Spreadtrum Communications Inc.
 */
#include <linux/clk.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/regmap.h>

#define IPA_POWER_OFF			0x07
#define MASK_IPA_POWER_STATE		0x1f00
#define REG_APB_EB			0x0
#define REG_USB31PLL_CTRL0		0x0
#define REG_USB31PLL_REG_SEL_CFG_0	0x28
#define MASK_USB31PLL_EB		BIT(4)
#define MASK_USB31PLLV_PD		BIT(30)
#define MASK_DBG_SEL_USB31PLLV_PD	BIT(8)
#define POLL_US				50
#define TIMEOUT_US			5000

enum { AUTOSHUTDOWNEN, DSLPEN, STATE, FORCELSLP, LSLPEN, SMARTLSLPEN, ACCESSEN, NR_REGS };

static const char * const reg_names[NR_REGS] = {
	"ipa-sys-autoshutdownen", "ipa-sys-dslpen", "ipa-sys-state", "ipa-sys-forcelslp",
	"ipa-sys-lslpen", "ipa-sys-smartlslpen", "ipa-sys-accessen",
};

struct ipa_reg {
	struct regmap *rmap;
	u32 reg;
	u32 mask;
};

struct ipa_sys_pd {
	struct device *dev;
	struct generic_pm_domain gpd;
	struct ipa_reg regs[NR_REGS];
	struct regmap *dispc1, *anlg;
	struct clk *core, *core_parent, *core_default, *ckg_eb;
};

static void ipa_set(struct ipa_sys_pd *pd, int i, bool set)
{
	struct ipa_reg *r = &pd->regs[i];

	if (r->rmap)
		regmap_update_bits(r->rmap, r->reg, r->mask, set ? r->mask : 0);
}

static int ipa_wait_state(struct ipa_sys_pd *pd, u32 want)
{
	struct ipa_reg *r = &pd->regs[STATE];
	u32 val;

	if (!r->rmap) {
		usleep_range(1250, 5000);
		return 0;
	}
	return regmap_read_poll_timeout(r->rmap, r->reg, val,
					((val & r->mask & MASK_IPA_POWER_STATE) >> 8) == want,
					POLL_US, TIMEOUT_US);
}

static int ipa_sys_power_on(struct generic_pm_domain *domain)
{
	struct ipa_sys_pd *pd = container_of(domain, struct ipa_sys_pd, gpd);
	struct ipa_reg *r = &pd->regs[AUTOSHUTDOWNEN];
	u32 val = 0;
	int ret;

	ipa_set(pd, DSLPEN, false);
	if (r->rmap && !regmap_read(r->rmap, r->reg, &val) && !((val & r->mask) >> 24))
		regmap_update_bits(r->rmap, r->reg, r->mask, r->mask);

	ret = ipa_wait_state(pd, 0);
	if (ret)
		dev_warn(pd->dev, "power on timeout\n");

	ipa_set(pd, ACCESSEN, true);

	if (pd->core && pd->core_parent && pd->ckg_eb) {
		ret = clk_prepare_enable(pd->core_parent);
		if (ret)
			return ret;
		ret = clk_prepare_enable(pd->ckg_eb);
		if (ret)
			return ret;
		clk_set_parent(pd->core, pd->core_parent);
	}

	/* take the USB31 PLL out of power down */
	regmap_update_bits(pd->dispc1, REG_APB_EB, MASK_USB31PLL_EB, MASK_USB31PLL_EB);
	regmap_update_bits(pd->anlg, REG_USB31PLL_REG_SEL_CFG_0, MASK_DBG_SEL_USB31PLLV_PD, MASK_DBG_SEL_USB31PLLV_PD);
	regmap_update_bits(pd->anlg, REG_USB31PLL_CTRL0, MASK_USB31PLLV_PD, 0);
	regmap_update_bits(pd->dispc1, REG_APB_EB, MASK_USB31PLL_EB, 0);
	dev_info(pd->dev, "power on\n");
	return 0;
}

static int ipa_sys_power_off(struct generic_pm_domain *domain)
{
	struct ipa_sys_pd *pd = container_of(domain, struct ipa_sys_pd, gpd);

	if (pd->core && pd->core_parent && pd->core_default && pd->ckg_eb) {
		clk_set_parent(pd->core, pd->core_default);
		clk_disable_unprepare(pd->core_parent);
		clk_disable_unprepare(pd->ckg_eb);
	}
	ipa_set(pd, DSLPEN, true);
	ipa_set(pd, ACCESSEN, false);
	if (ipa_wait_state(pd, IPA_POWER_OFF))
		dev_info(pd->dev, "power off maybe failed\n");
	return 0;
}

static int ipa_sys_probe(struct platform_device *pdev)
{
	struct device_node *np = pdev->dev.of_node;
	struct ipa_sys_pd *pd;
	u32 args[2];
	int i, ret;

	pd = devm_kzalloc(&pdev->dev, sizeof(*pd), GFP_KERNEL);
	if (!pd)
		return -ENOMEM;
	pd->dev = &pdev->dev;

	for (i = 0; i < NR_REGS; i++) {
		struct regmap *m = syscon_regmap_lookup_by_phandle_args(np, reg_names[i], 2, args);

		if (IS_ERR(m)) {
			dev_warn(&pdev->dev, "no %s\n", reg_names[i]);
			continue;
		}
		pd->regs[i] = (struct ipa_reg){ m, args[0], args[1] };
	}
	pd->dispc1 = syscon_regmap_lookup_by_phandle(np, "sprd,syscon-dispc1-glb");
	pd->anlg = syscon_regmap_lookup_by_phandle(np, "sprd,syscon-anlg-phy");
	if (IS_ERR(pd->dispc1) || IS_ERR(pd->anlg))
		return dev_err_probe(&pdev->dev, -ENODEV, "missing syscons\n");

	pd->core = devm_clk_get(&pdev->dev, "ipa_core");
	pd->core_parent = devm_clk_get(&pdev->dev, "ipa_core_source");
	pd->core_default = devm_clk_get(&pdev->dev, "ipa_core_default");
	pd->ckg_eb = devm_clk_get(&pdev->dev, "clk_ipa_ckg_eb");
	for (i = 0; i < 4; i++) {
		struct clk *c = (struct clk *[]){ pd->core, pd->core_parent, pd->core_default, pd->ckg_eb }[i];

		if (IS_ERR(c))
			return dev_err_probe(&pdev->dev, PTR_ERR(c), "missing clock %d\n", i);
	}

	pd->gpd.name = devm_kstrdup(&pdev->dev, np->name, GFP_KERNEL);
	pd->gpd.power_on = ipa_sys_power_on;
	pd->gpd.power_off = ipa_sys_power_off;
	ret = pm_genpd_init(&pd->gpd, NULL, true);
	if (ret)
		return ret;
	ret = of_genpd_add_provider_simple(np, &pd->gpd);
	if (ret) {
		pm_genpd_remove(&pd->gpd);
		return ret;
	}

	/* light sleep policy as in the vendor driver */
	ipa_set(pd, FORCELSLP, false);
	ipa_set(pd, LSLPEN, true);
	ipa_set(pd, SMARTLSLPEN, true);
	dev_info(&pdev->dev, "IPA subsystem power domain registered\n");
	return 0;
}

static const struct of_device_id ipa_sys_match[] = {
	{ .compatible = "sprd,qogirn6pro-ipa-sys-power-domain" },
	{ }
};

static struct platform_driver ipa_sys_driver = {
	.probe = ipa_sys_probe,
	.driver = { .name = "ums9620-ipa-sys-pd", .of_match_table = ipa_sys_match },
};
builtin_platform_driver(ipa_sys_driver);
MODULE_LICENSE("GPL");
