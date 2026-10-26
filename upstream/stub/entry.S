	.section .text.entry
	.global _entry
_entry:
	ldr	x9, =0x80170000		/* stack below the kernel at 0x80200000 */
	mov	sp, x9
	bl	pmic_reset
	b	.
