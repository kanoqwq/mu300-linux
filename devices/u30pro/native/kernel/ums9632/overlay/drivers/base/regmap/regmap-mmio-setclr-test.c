// SPDX-License-Identifier: GPL-2.0-only
/* Exercise the real regmap-MMIO backend with RAM-backed command windows. */
#include <kunit/test.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/regmap.h>
#include <linux/slab.h>

#define REGION 0x3000
static void exit_map(void *map) { regmap_exit(map); }
static struct regmap_config config_for_aliases(void)
{
 struct regmap_config c = { .reg_bits = 32, .val_bits = 32, .reg_stride = 4,
  .max_register = REGION - 4, .reg_set_offset = 0x1000, .reg_clr_offset = 0x2000 };
 return c;
}
static struct regmap *make_map(struct kunit *test, u8 **ram, struct regmap_config *cfg)
{
 struct regmap *map;
 int ret;
 *ram = kunit_kzalloc(test, REGION, GFP_KERNEL);
 if (!*ram)
  return ERR_PTR(-ENOMEM);
 map = regmap_init_mmio(NULL, (void __iomem *)*ram, cfg);
 if (IS_ERR(map))
  return map;
 ret = kunit_add_action_or_reset(test, exit_map, map);
 return ret ? ERR_PTR(ret) : map;
}
static void aliases_preserve_shared_register(struct kunit *test)
{
 struct regmap_config cfg = config_for_aliases();
 struct regmap *map;
 u8 *ram;
 map = make_map(test, &ram, &cfg);
 KUNIT_ASSERT_NOT_ERR_OR_NULL(test, map);
 writel(0xdeadbeef, (void __iomem *)(ram + 0xf00));
 KUNIT_ASSERT_EQ(test, regmap_update_bits(map, 0xf00, 0xff, 0xfedcba12), 0);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0xf00)), 0xdeadbeefU);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x1f00)), 0x12U);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x2f00)), 0xedU);
 /* Simulate a remote writer; subsequent commands still never rewrite the base. */
 writel(0x13572468, (void __iomem *)(ram + 0xf00));
 KUNIT_ASSERT_EQ(test, regmap_update_bits(map, 0xf00, 0xff, 0), 0);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0xf00)), 0x13572468U);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x2f00)), 0xffU);
}
static void default_mmio_retains_rmw(struct kunit *test)
{
 struct regmap_config cfg = config_for_aliases();
 struct regmap *map;
 u8 *ram;
 cfg.reg_set_offset = cfg.reg_clr_offset = 0;
 map = make_map(test, &ram, &cfg);
 KUNIT_ASSERT_NOT_ERR_OR_NULL(test, map);
 writel(0xdeadbeef, (void __iomem *)(ram + 0xf00));
 KUNIT_ASSERT_EQ(test, regmap_update_bits(map, 0xf00, 0xff, 0x12), 0);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0xf00)), 0xdeadbe12U);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x1f00)), 0U);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x2f00)), 0U);
}
static void rejects_range_without_partial_write(struct kunit *test)
{
 struct regmap_config cfg = config_for_aliases();
 struct regmap *map;
 u8 *ram;
 map = make_map(test, &ram, &cfg);
 KUNIT_ASSERT_NOT_ERR_OR_NULL(test, map);
 /* SET would fit, CLR would exceed the mapping: neither may be issued. */
 writel(0x1234, (void __iomem *)(ram + 0x2000));
 KUNIT_EXPECT_EQ(test, regmap_update_bits(map, 0x1000, 1, 1), -EINVAL);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x2000)), 0x1234U);
}
static void rejects_invalid_layouts(struct kunit *test)
{
 struct regmap_config cfg;
 struct regmap *map;
 u8 *ram = kunit_kzalloc(test, REGION, GFP_KERNEL);
 int i;
 KUNIT_ASSERT_NOT_NULL(test, ram);
 for (i = 0; i < 5; i++) {
  cfg = config_for_aliases();
  switch (i) {
  case 0: cfg.reg_clr_offset = 0; break;
  case 1: cfg.reg_clr_offset = cfg.reg_set_offset; break;
  case 2: cfg.reg_set_offset++; break;
  case 3: cfg.val_bits = 16; break;
  case 4: cfg.max_register = 0; break;
  }
  map = regmap_init_mmio(NULL, (void __iomem *)ram, &cfg);
  if (!IS_ERR(map)) {
   regmap_exit(map);
   KUNIT_FAIL(test, "invalid layout %d accepted", i);
   return;
  }
  KUNIT_EXPECT_EQ(test, PTR_ERR(map), (long)-EINVAL);
 }
}
static void zero_mask_writes_nothing(struct kunit *test)
{
 struct regmap_config cfg = config_for_aliases();
 struct regmap *map;
 u8 *ram;
 map = make_map(test, &ram, &cfg);
 KUNIT_ASSERT_NOT_ERR_OR_NULL(test, map);
 writel(0x11, (void __iomem *)(ram + 0x1000));
 writel(0x22, (void __iomem *)(ram + 0x2000));
 KUNIT_EXPECT_EQ(test, regmap_update_bits(map, 0, 0, 0xffffffff), 0);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x1000)), 0x11U);
 KUNIT_EXPECT_EQ(test, readl((void __iomem *)(ram + 0x2000)), 0x22U);
}
static struct kunit_case cases[] = {
 KUNIT_CASE(aliases_preserve_shared_register),
 KUNIT_CASE(default_mmio_retains_rmw),
 KUNIT_CASE(rejects_range_without_partial_write),
 KUNIT_CASE(rejects_invalid_layouts),
 KUNIT_CASE(zero_mask_writes_nothing),
 {}
};
static struct kunit_suite suite = { .name = "regmap-mmio-setclr", .test_cases = cases };
kunit_test_suite(suite);
MODULE_LICENSE("GPL");
