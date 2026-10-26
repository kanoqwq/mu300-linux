/* SPDX-License-Identifier: GPL-2.0 */
/*
 * MU300: the CPU voltage offset of sprd-cpufreq-v2 (EXPERIMENTAL), the part without kernel state: the limits, the
 * parser of "A,B,C" and the encoding into the DVFS_INIT flag word. Pure functions, so tests/test_cpu_voltage.py
 * compiles this header on the host and checks every value the kernel accepts or refuses.
 *
 * The offset is applied by the SoC firmware (SML/ATF), never by Linux: the one-shot SIP call DVFS_INIT
 * (0x8200060b) takes a flag word whose byte 0 is the vendor's androidboot.dvfs_set and whose bytes 1..3 are the
 * offsets of DVFS domains 0..2 (CPUs 0-3, 4-6, 7): bit 7 the sign (1 = lower), bits 0-6 whole millivolts. The
 * firmware turns them into ceil(mV * 1000 / 3125) steps of its 3.125 mV selector and moves the CPU and the SRAM
 * tables by that many steps. Encoding and firmware analysis: kano (github.com/kanoqwq/mu300-linux@3386928),
 * docs/FINDINGS.md 133.
 *
 * Limits (ours, tighter than kano's -50..+25 mV): -25 mV .. +12.5 mV, a whole number of 3.125 mV steps (the UMP9620
 * CPU DCDC's own step). A raise is also held under the DCDC's regulator-max-microvolt from the device tree by the
 * driver (an OPP that would go above it is never used).
 */
#ifndef __SPRD_CPUFREQ_V2_VOLTAGE_H__
#define __SPRD_CPUFREQ_V2_VOLTAGE_H__

#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/types.h>
#endif

#define SPRD_VOLT_DOMAINS	3
#define SPRD_VOLT_STEP_UV	3125		/* the selector step of the firmware and of the UMP9620 CPU DCDCs */
#define SPRD_VOLT_MIN_UV	(-25000)	/* 8 steps down */
#define SPRD_VOLT_MAX_UV	12500		/* 4 steps up */
#define SPRD_VOLT_WAIT_S	45		/* no word from the initramfs by then: DVFS starts at the stock voltages */

/* one offset: inside the limits and a whole number of steps */
static inline int sprd_volt_check_one(int uv)
{
	if (uv < SPRD_VOLT_MIN_UV || uv > SPRD_VOLT_MAX_UV)
		return -ERANGE;
	if (uv % SPRD_VOLT_STEP_UV)
		return -ERANGE;
	return 0;
}

/*
 * The byte of one domain: whole millivolts, rounded down, so that the firmware's ceil(mV * 1000 / 3125) gives back
 * exactly uv / 3125 steps (floor(3.125 k) * 1000 / 3125 lies in (k - 1, k] for every k: tests check all of them).
 */
static inline unsigned int sprd_volt_encode_one(int uv)
{
	unsigned int mag = (unsigned int)(uv < 0 ? -uv : uv) / 1000U;

	return (mag & 0x7fU) | (uv < 0 ? 0x80U : 0U);
}

/* what the firmware does with one byte: the offset in uV that it applies (whole selector steps) */
static inline int sprd_volt_firmware_uv(unsigned int byte)
{
	unsigned int mv = byte & 0x7fU;
	int steps = (int)((mv * 1000U + SPRD_VOLT_STEP_UV - 1U) / SPRD_VOLT_STEP_UV);

	return (byte & 0x80U) ? -steps * SPRD_VOLT_STEP_UV : steps * SPRD_VOLT_STEP_UV;
}

/*
 * The flag word for DVFS_INIT. base is what the vendor's code would pass (androidboot.dvfs_set). All three offsets
 * zero: base exactly, the stock call. Otherwise every offset is checked before anything is encoded (all or none),
 * and a base with any of the upper bytes set is refused: they are the vendor's, with a meaning we do not know.
 */
static inline int sprd_volt_flag(unsigned int base, const int uv[SPRD_VOLT_DOMAINS], unsigned int *flag)
{
	unsigned int f = base;
	int i, ret, any = 0;

	for (i = 0; i < SPRD_VOLT_DOMAINS; i++) {
		ret = sprd_volt_check_one(uv[i]);
		if (ret)
			return ret;
		any |= uv[i] != 0;
	}
	if (!any) {
		*flag = base;
		return 0;
	}
	if (base & 0xffffff00U)
		return -EOPNOTSUPP;
	for (i = 0; i < SPRD_VOLT_DOMAINS; i++)
		f |= sprd_volt_encode_one(uv[i]) << (8 * (i + 1));
	*flag = f;
	return 0;
}

/*
 * "A,B,C" (commas or blanks, an optional trailing newline): exactly three decimal integers of at most 6 digits each.
 * Nothing else is accepted - no hex, no fourth value, no missing one.
 */
static inline int sprd_volt_parse(const char *s, int uv[SPRD_VOLT_DOMAINS])
{
	int n;

	for (n = 0; n < SPRD_VOLT_DOMAINS; n++) {
		int neg = 0, digits = 0, v = 0, sep = 0;

		/* before the second and third value a separator: a comma, blanks, or both */
		while (*s == ' ') {
			s++;
			sep = 1;
		}
		if (n && *s == ',') {
			s++;
			sep = 1;
			while (*s == ' ')
				s++;
		}
		if (n && !sep)
			return -EINVAL;
		if (*s == '-' || *s == '+') {
			neg = *s == '-';
			s++;
		}
		while (*s >= '0' && *s <= '9') {
			if (++digits > 6)
				return -EINVAL;
			v = v * 10 + (*s - '0');
			s++;
		}
		if (!digits)
			return -EINVAL;
		uv[n] = neg ? -v : v;
	}
	while (*s == ' ')
		s++;
	if (*s == '\n')
		s++;
	return *s ? -EINVAL : 0;
}

#endif /* __SPRD_CPUFREQ_V2_VOLTAGE_H__ */
