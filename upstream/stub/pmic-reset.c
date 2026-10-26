/*
 * Bare-metal probe for the MU300 LK entry point: reset the board through the UMP9620 PMIC (ADI bus, MMU off).
 * Used to prove that a reset path exists before the kernel runs. SPDX-License-Identifier: GPL-2.0-only
 */
typedef unsigned int u32;
typedef unsigned long u64;

#define ADI_BASE	0x64400000UL
#define ADI_SLAVE	(ADI_BASE + 0x20000)	/* 15-bit slave address space */
#define RD_CMD		0x28
#define RD_DATA		0x2c
#define FIFO_STS	0x30
#define FIFO_EMPTY	(1u << 10)
#define RD_BUSY		(1u << 31)

static inline u32 rl(u64 a) { return *(volatile u32 *)a; }
static inline void wl(u64 a, u32 v) { *(volatile u32 *)a = v; }

static u32 adi_read(u32 reg)
{
	wl(ADI_BASE + RD_CMD, reg);
	for (int i = 0; i < 1000000; i++) {
		u32 v = rl(ADI_BASE + RD_DATA);
		if (!(v & RD_BUSY))
			return v & 0xffff;
	}
	return 0;
}

static void adi_write(u32 reg, u32 val)
{
	for (int i = 0; i < 1000000; i++)
		if (rl(ADI_BASE + FIFO_STS) & FIFO_EMPTY)
			break;
	wl(ADI_SLAVE + reg, val);
	for (int i = 0; i < 1000000; i++)
		if (rl(ADI_BASE + FIFO_STS) & FIFO_EMPTY)
			break;
}

void pmic_reset(void)
{
	u32 v;

	v = adi_read(0x23ac);			/* RST_STATUS: reboot mode normal */
	adi_write(0x23ac, (v & ~0xffu) | 0x40);
	v = adi_read(0x23f8);			/* SWRST_CTRL0: enable register reset */
	adi_write(0x23f8, v | (1u << 4));
	v = adi_read(0x2024);			/* SOFT_RST_HW: reset */
	adi_write(0x2024, v | 1u);
	for (;;)
		;
}
