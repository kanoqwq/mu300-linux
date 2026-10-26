// SPDX-License-Identifier: GPL-2.0-only
/*
 * MU300 bring-up debug aid (not for upstream): reset the board through the UMP9620 PMIC (ADI bus) when the kernel
 * reaches MU300_RESET_STAGE. A fast reboot cycle means that stage was reached; there is no console to report it.
 */
#include <linux/io.h>
#include <linux/kernel.h>
#include <asm/early_ioremap.h>

#define MU300_RESET_STAGE 0
#define ADI_BASE	0x64400000UL
#define ADI_SIZE	0x30000

static u32 adi_read(void __iomem *b, u32 reg)
{
	int i;

	writel(reg, b + 0x28);
	for (i = 0; i < 1000000; i++) {
		u32 v = readl(b + 0x2c);

		if (!(v & BIT(31)))
			return v & 0xffff;
	}
	return 0;
}

static void adi_write(void __iomem *b, u32 reg, u32 val)
{
	int i;

	for (i = 0; i < 1000000 && !(readl(b + 0x30) & BIT(10)); i++)
		;
	writel(val, b + 0x20000 + reg);
	for (i = 0; i < 1000000 && !(readl(b + 0x30) & BIT(10)); i++)
		;
}

void __ref mu300_probe_reset(int stage, bool early)
{
	void __iomem *b;
	u32 v;

	if (stage != MU300_RESET_STAGE)
		return;
	b = early ? early_ioremap(ADI_BASE, ADI_SIZE) : ioremap(ADI_BASE, ADI_SIZE);
	if (!b)
		return;
	v = adi_read(b, 0x23ac);
	adi_write(b, 0x23ac, (v & ~0xffu) | 0x40);
	v = adi_read(b, 0x23f8);
	adi_write(b, 0x23f8, v | BIT(4));
	v = adi_read(b, 0x2024);
	adi_write(b, 0x2024, v | 1);
	for (;;)
		cpu_relax();
}
