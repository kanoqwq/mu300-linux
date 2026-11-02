/* SPDX-License-Identifier: GPL-2.0-only */
/* Opt-in boot checkpoint: no stack, RAM stores, UART, or board-register I/O.
 * The known standard PSCI reset is deliberately delayed so the host can
 * distinguish reaching this checkpoint from the normal failed-boot window.
 */
#include <linux/linkage.h>

#if CONFIG_U30PRO_TIMED_RESET_STAGE <= 3
	.section ".idmap.text", "ax"
#else
/* The virtual alias of .idmap.text is NX after enabling the kernel MMU.
 * Keep post-switch code executable and persistent: do_one_initcall can also
 * be used by module loading after init sections have been freed.
 */
	.section ".text", "ax"
#endif
	.align 4
SYM_CODE_START(u30pro_timed_reset)
	msr	daifset, #0xf
	mrs	x4, cntfrq_el0
	cbnz	x4, 1f
	ldr	x4, =26000000
1:	adrp	x5, u30pro_timed_reset_delay_ms
	ldr	w5, [x5, :lo12:u30pro_timed_reset_delay_ms]
	mul	x4, x4, x5
	mov	x7, #1000
	udiv	x4, x4, x7
	isb
	mrs	x5, cntpct_el0
SYM_INNER_LABEL(u30pro_timed_reset_wait, SYM_L_GLOBAL)
	isb
	mrs	x6, cntpct_el0
	sub	x6, x6, x5
	cmp	x6, x4
	b.lo	u30pro_timed_reset_wait
	dsb	sy
	movz	x0, #0x8400, lsl #16
	movk	x0, #0x0009
	mov	x1, xzr
	mov	x2, xzr
	mov	x3, xzr
SYM_INNER_LABEL(u30pro_timed_reset_smc, SYM_L_GLOBAL)
	smc	#0
2:	wfe
	b	2b
SYM_CODE_END(u30pro_timed_reset)
	.ltorg

/* Two read-only Image parameters. Keeping the build/config fixed permits
 * byte-identical executable code between all checkpoints/initcall trials.
 * The pre-MMU checkpoints 1..4 retain their compile-time selection.
 */
	.section ".rodata", "a"
	.balign 4
SYM_DATA_START(u30pro_timed_reset_selector)
	.long CONFIG_U30PRO_TIMED_RESET_STAGE
SYM_DATA_END(u30pro_timed_reset_selector)
SYM_DATA_START(u30pro_timed_reset_delay_ms)
	.long CONFIG_U30PRO_TIMED_RESET_SECONDS * 1000
SYM_DATA_END(u30pro_timed_reset_delay_ms)
