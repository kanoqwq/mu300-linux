/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_U30PRO_TIMED_RESET_H
#define _LINUX_U30PRO_TIMED_RESET_H
#include <linux/compiler.h>

#ifdef CONFIG_U30PRO_TIMED_RESET
#include <asm/sections.h>
/* Defined in assembly so the C compiler cannot fold the selected stage and
 * delete the remainder of start_kernel/setup_arch as unreachable code.
 */
extern const volatile unsigned int u30pro_timed_reset_selector;
void __noreturn u30pro_timed_reset(void);
static __always_inline void u30pro_reset_checkpoint(unsigned int stage)
{
	if (stage == READ_ONCE(u30pro_timed_reset_selector))
		u30pro_timed_reset();
}
/* High bit selects an image-relative initcall function address; bit30 picks
 * after-return rather than before-call. No counter/state writes are needed.
 */
static __always_inline void u30pro_reset_initcall(unsigned long address, bool after)
{
	unsigned int requested = READ_ONCE(u30pro_timed_reset_selector);

	if (!(requested & 0x80000000U) ||
	    !!(requested & 0x40000000U) != after)
		return;
	if (address - (unsigned long)_text == (requested & 0x3fffffffU))
		u30pro_timed_reset();
}
#else
static inline void u30pro_reset_checkpoint(unsigned int stage) { }
static inline void u30pro_reset_initcall(unsigned long address, bool after) { }
#endif
#endif
