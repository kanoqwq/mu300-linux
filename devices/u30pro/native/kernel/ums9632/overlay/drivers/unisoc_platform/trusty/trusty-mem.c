// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2020 Unisoc Inc.
 */

#include "trusty.h"
#include "trusty-mem.h"

#ifdef pr_fmt
#undef pr_fmt
#endif
#define pr_fmt(fmt) "sprd-trusty-mem: " fmt

int trusty_encode_page_info(struct ns_mem_page_info *inf,
			    struct page *page, pgprot_t pgprot)
{
	int mem_attr;
	uint64_t pte;

	if (!inf || !page)
		return -EINVAL;

	/* get physical address */
	pte = (uint64_t) page_to_phys(page);

	/* get memory attributes */
	mem_attr = get_mem_attr(page, pgprot);
	if (mem_attr < 0)
		return mem_attr;

	/* add other attributes */
#if defined(CONFIG_ARM64) || defined(CONFIG_ARM_LPAE)
	pte |= pgprot_val(pgprot);
#elif defined(CONFIG_ARM)
	if (pgprot_val(pgprot) & L_PTE_USER)
		pte |= (1 << 6);
	if (pgprot_val(pgprot) & L_PTE_RDONLY)
		pte |= (1 << 7);
	if (pgprot_val(pgprot) & L_PTE_SHARED)
		pte |= (3 << 8); /* inner sharable */
#endif

	inf->attr = (pte & 0x0000FFFFFFFFFFFFull) | ((uint64_t)mem_attr << 48);
	return 0;
}

int trusty_call32_mem_buf(struct device *dev, u32 smcnr,
			  struct page *page,  u32 size,
			  pgprot_t pgprot)
{
	int ret;
	struct ns_mem_page_info pg_inf;

	if (!dev || !page)
		return -EINVAL;

	ret = trusty_encode_page_info(&pg_inf, page, pgprot);
	if (ret)
		return ret;

	if (SMC_IS_FASTCALL(smcnr)) {
		return trusty_fast_call32(dev, smcnr,
					  (u32)pg_inf.attr,
					  (u32)(pg_inf.attr >> 32), size);
	} else {
		return trusty_std_call32(dev, smcnr,
					 (u32)pg_inf.attr,
					 (u32)(pg_inf.attr >> 32), size);
	}
}
EXPORT_SYMBOL_GPL(trusty_call32_mem_buf);
