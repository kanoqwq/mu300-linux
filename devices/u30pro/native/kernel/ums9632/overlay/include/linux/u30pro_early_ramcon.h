/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_U30PRO_EARLY_RAMCON_H
#define _LINUX_U30PRO_EARLY_RAMCON_H
#include <linux/types.h>

#ifdef CONFIG_U30PRO_EARLY_RAMCON
void u30pro_early_ramcon_init(void);
void u30pro_early_ramcon_checkpoint(const char *stage);
void u30pro_early_ramcon_handoff(phys_addr_t console_addr);
#else
static inline void u30pro_early_ramcon_init(void) { }
static inline void u30pro_early_ramcon_checkpoint(const char *stage) { }
static inline void u30pro_early_ramcon_handoff(phys_addr_t console_addr) { }
#endif
#endif
