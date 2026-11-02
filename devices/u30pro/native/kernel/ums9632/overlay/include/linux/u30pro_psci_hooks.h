/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Hooks into the PSCI driver for the Trusty (secure OS) driver.
 *
 * The vendor (Android) kernel patches drivers/firmware/psci/psci.c with two restricted vendor hooks that the Unisoc Trusty PM driver uses:
 *  - android_rvh_psci_tos_resident_on(cpu, &resident): the secure OS is resident on this CPU (it must not be taken offline)
 *  - android_rvh_psci_cpu_suspend(state, &deny): do not issue PSCI CPU_SUSPEND now (the secure OS is busy on this CPU)
 * Both are answered with the secure-monitor fast call SMC_FC_CPU_CAN_DOWN.  Without them a core that enters a power-down idle state while
 * the secure OS has work for it breaks the secure-world channel (the baseband start then times out talking to the "kernelbootcp" service).
 * Mainline has no vendor hooks, so the same two decisions are taken through these function pointers.
 */
#ifndef _LINUX_U30PRO_PSCI_HOOKS_H
#define _LINUX_U30PRO_PSCI_HOOKS_H

#include <linux/types.h>

/* true: the secure OS is resident on @cpu */
extern bool (*u30pro_psci_tos_resident_on)(int cpu);
/* true: refuse this PSCI CPU_SUSPEND (the caller returns -EPERM and the CPU idles in WFI instead) */
extern bool (*u30pro_psci_cpu_suspend_deny)(u32 state);

#endif
