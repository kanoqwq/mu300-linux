/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright (C) 2020 Unisoc Inc.
 */

#ifndef _TRUSTY_MEM_H_
#define _TRUSTY_MEM_H_

/*
 * Ring buffer that supports one secure producer thread and one
 * linux side consumer thread.
 */
#include <linux/types.h>
#include <linux/printk.h>
#include <linux/trusty/smcall.h>

#if defined(CONFIG_ARM64)
int get_mem_attr(struct page *page, pgprot_t pgprot)
{
	uint64_t mair;
	uint attr_index = (pgprot_val(pgprot) & PTE_ATTRINDX_MASK) >> 2;

	asm ("mrs %0, mair_el1\n" : "=&r" (mair));
	return (mair >> (attr_index * 8)) & 0xff;
}
#elif defined(CONFIG_ARM_LPAE)
int get_mem_attr(struct page *page, pgprot_t pgprot)
{
	uint32_t mair;
	uint attr_index = ((pgprot_val(pgprot) & L_PTE_MT_MASK) >> 2);

	if (attr_index >= 4) {
		attr_index -= 4;
		asm volatile("mrc p15, 0, %0, c10, c2, 1\n" : "=&r" (mair));
	} else {
		asm volatile("mrc p15, 0, %0, c10, c2, 0\n" : "=&r" (mair));
	}
	return (mair >> (attr_index * 8)) & 0xff;
}
#elif defined(CONFIG_ARM)
int get_mem_attr(struct page *page, pgprot_t pgprot)
{
		/* check memory type */
	switch (pgprot_val(pgprot) & L_PTE_MT_MASK) {
	case L_PTE_MT_WRITEALLOC:
		/* Normal: write back write allocate */
		return 0xFF;

	case L_PTE_MT_BUFFERABLE:
		/* Normal: non-cacheble */
		return 0x44;

	case L_PTE_MT_WRITEBACK:
		/* Normal: writeback, read allocate */
		return 0xEE;

	case L_PTE_MT_WRITETHROUGH:
		/* Normal: write through */
		return 0xAA;

	case L_PTE_MT_UNCACHED:
		/* strongly ordered */
		return 0x00;

	case L_PTE_MT_DEV_SHARED:
	case L_PTE_MT_DEV_NONSHARED:
		/* device */
		return 0x04;

	default:
		return -EINVAL;
	}
}
#else
int get_mem_attr(struct page *page, pgprot_t pgprot)
{
	return 0;
}
#endif

#endif

