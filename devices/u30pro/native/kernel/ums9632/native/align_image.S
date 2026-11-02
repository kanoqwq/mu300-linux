/* SPDX-License-Identifier: MIT
 * Position-independent arm64 raw-Image alignment wrapper. No DTB/device writes.
 * Optional 128-byte early journal in an empty, DT-reserved ramoops dump tail.
 * Entry: EL1/EL2, x0=8-aligned FDT; MMU off or firmware identity map.
 * Normalize the raw non-EFI handoff: clean/invalidate caches, then MMU/C/I off.
 * Reserve the entire relocation arena in the outer Image header; move only
 * the payload file bytes, backwards when overlapping. Never relocate the DTB.
 */
#ifndef U30PRO_JOURNAL_FDT
#define U30PRO_JOURNAL_FDT 0x86000000
#define U30PRO_JOURNAL_SLOT 0xfffa8000
#define U30PRO_JOURNAL_ADDR 0xfffaff00
#define U30PRO_JOURNAL_SIG 0x43474244
#endif
.macro journal_stage number
 cbz x19, .Ljournal_skip\@
 mov x9, #\number
 str x9, [x19, #16]
 mvn x16, x9
 str x16, [x19, #120]
 bl journal_flush
.Ljournal_skip\@:
.endm
.section .text,"ax"
.global _start
_start:
 .inst 0x91005a4d             /* harmless ADD x13,x18,#0x16, also "MZ" */
 b wrapper_start
 .quad OUTER_TEXT_OFFSET
 .quad ARENA_SIZE
 .quad IMAGE_FLAGS
 .quad 0, 0, 0
 .ascii "ARMd"
 .long 0                    /* raw Image, no EFI executable entry */
wrapper_start:
 mov x20, x0
 mov x19, xzr              /* disabled until exact FDT/empty-slot guards pass */
 msr daifset, #0xf
 mrs x8, CurrentEL
 mov x22, x8
 cmp x8, #8
 b.eq 8f
 cmp x8, #4
 b.ne failure
 mrs x8, sctlr_el1
 b 9f
8:
 mrs x8, sctlr_el2
9:
 mov x23, x8
 /* Exact board handoff address plus an existing empty dump slot are required.
  * This does not alter the pstore header, console, pmsg or a stored crash. */
 ldr x9, =U30PRO_JOURNAL_FDT
 cmp x0, x9
 b.ne journal_disabled
 ldr x9, =U30PRO_JOURNAL_SLOT
 ldr w10, [x9]
 ldr w11, =U30PRO_JOURNAL_SIG
 cmp w10, w11
 b.ne journal_disabled
 ldr w10, [x9, #8]
 cbnz w10, journal_disabled
 ldr x19, =U30PRO_JOURNAL_ADDR
 stp xzr, xzr, [x19, #16]
 stp xzr, xzr, [x19, #32]
 stp xzr, xzr, [x19, #48]
 stp xzr, xzr, [x19, #64]
 stp xzr, xzr, [x19, #80]
 stp xzr, xzr, [x19, #96]
 stp xzr, xzr, [x19, #112]
 ldr x9, =0x314c4e524a303355  /* U30JRNL1 */
 str x9, [x19]
 ldr x9, =0x0000355234315055  /* UP14R5 */
 str x9, [x19, #8]
 str x22, [x19, #24]
 str x23, [x19, #32]
 adr x9, _start
 str x9, [x19, #40]
 str x20, [x19, #56]
 mrs x9, ctr_el0
 str x9, [x19, #72]
 journal_stage 1
journal_disabled:
 /* Preserve initial SCTLR until the relocated image and full cache are clean. */
 cbz x0, failure
 tst x0, #7
 b.ne failure
 ldr w9, [x0]               /* FDT_MAGIC, big-endian in memory */
 mov w10, #0x0dd0
 movk w10, #0xedfe, lsl #16
 cmp w9, w10
 b.ne failure
 ldr w9, [x0, #4]
 rev w9, w9                 /* bounded totalsize, boot ABI <=2 MiB */
 cmp w9, #40
 b.lo failure
 mov x11, #0x200000
 cmp x9, x11
 b.hi failure
 adds x21, x0, x9            /* FDT exclusive end */
 b.cs failure
 journal_stage 2
 adr x10, payload_start
 mov x11, #0x1fffff
 adds x12, x10, x11
 b.cs failure
 bic x12, x12, x11          /* destination ALIGN_UP(payload,2 MiB) */
 ldr x13, =PAYLOAD_MEMORY_SIZE
 adds x14, x12, x13
 b.cs failure
 /* The destination incl. BSS must not overwrite the bootloader's DTB. */
 cmp x20, x14
 b.hs 1f
 cmp x21, x12
 b.hi failure
1:
 cbz x19, 3f
 str x12, [x19, #48]
 str x14, [x19, #80]
3:
 journal_stage 3
 ldr x13, =PAYLOAD_FILE_SIZE
 cmp x12, x10
 b.eq copied
 add x14, x10, x13
 add x15, x12, x13
copy_backwards:
 ldr x16, [x14, #-8]!
 str x16, [x15, #-8]!
 subs x13, x13, #8
 b.ne copy_backwards
copied:
 journal_stage 4
 /* Clean copied instructions to PoC, then invalidate local instruction cache. */
 mrs x14, ctr_el0
 ubfx x14, x14, #16, #4
 mov x15, #4
 lsl x15, x15, x14
 mov x16, x12
 ldr x13, =PAYLOAD_FILE_SIZE
 add x17, x12, x13
2:
 dc cvac, x16
 add x16, x16, x15
 cmp x16, x17
 b.lo 2b
 dsb sy
 mov x24, x12               /* destination survives cache geometry scratch */
 /* All dirty firmware data must reach RAM before turning translation/cache off.
  * Enumerate data/unified cache levels, including FEAT_CCIDX geometry.
  * Register fields cross-checked against upstream U-Boot armv8 cache.S.
  */
 mrs x25, clidr_el1
 ubfx x26, x25, #24, #3     /* number of coherent cache levels */
 mrs x28, id_aa64mmfr2_el1
 ubfx x28, x28, #20, #4     /* CCIDX changes CCSIDR ways/set fields */
 mrs x29, csselr_el1
 mov x27, #0
cache_level:
 cmp x27, x26
 b.hs cache_done
 add x9, x27, x27, lsl #1
 lsr x9, x25, x9
 and x9, x9, #7
 cmp x9, #2                /* skip absent/instruction-only levels */
 b.lo cache_next_level
 lsl x9, x27, #1
 msr csselr_el1, x9
 isb
 mrs x9, ccsidr_el1
 and x10, x9, #7
 add x10, x10, #4          /* log2(bytes per line) */
 cbz x28, cache_legacy
 ubfx x11, x9, #3, #21     /* maximum way index */
 ubfx x12, x9, #32, #24    /* maximum set index */
 b cache_geometry_ready
cache_legacy:
 ubfx x11, x9, #3, #10
 ubfx x12, x9, #13, #15
cache_geometry_ready:
 clz w13, w11
 mov x14, #0
cache_set:
 mov x15, #0
cache_way:
 lsl x16, x15, x13
 orr x16, x16, x27, lsl #1
 lsl x17, x14, x10
 orr x16, x16, x17
 dc cisw, x16
 add x15, x15, #1
 cmp x15, x11
 b.ls cache_way
 add x14, x14, #1
 cmp x14, x12
 b.ls cache_set
cache_next_level:
 add x27, x27, #1
 b cache_level
cache_done:
 msr csselr_el1, x29
 dsb sy
 ic iallu
 dsb sy
 isb
 journal_stage 5
 bic x8, x23, #1
 bic x8, x8, #4
 bic x8, x8, #0x1000
 cmp x22, #8
 b.eq disable_el2
 msr sctlr_el1, x8
 isb
 mrs x8, sctlr_el1
 b normalized
 disable_el2:
 msr sctlr_el2, x8
 isb
 mrs x8, sctlr_el2
normalized:
 ic iallu
 dsb sy
 isb
 mov x12, x24
 cbz x19, 4f
 str x8, [x19, #64]
4:
 journal_stage 6
 mov x0, x20
 mov x1, xzr
 mov x2, xzr
 mov x3, xzr
 br x12
failure:
 journal_stage 0xffff
 /* Existing boot watchdogs can recover; never feed or modify slot metadata. */
failure_wait:
 wfe
 b failure_wait

/* x16-x18 are caller scratch, no stack or MMU state change. */
journal_flush:
 cbz x19, journal_flush_done
 mrs x16, ctr_el0
 ubfx x16, x16, #16, #4
 mov x17, #4
 lsl x17, x17, x16
 sub x18, x17, #1
 bic x16, x19, x18
 add x18, x19, #128
journal_flush_line:
 dc cvac, x16
 add x16, x16, x17
 cmp x16, x18
 b.lo journal_flush_line
 dsb sy
journal_flush_done:
 ret
.ltorg
.org 4096
payload_start:
.incbin "payload.bin"
