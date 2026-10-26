/*
* SPDX-FileCopyrightText: 2021-2022 Unisoc (Shanghai) Technologies Co., Ltd
* SPDX-License-Identifier: GPL-2.0
*
* Copyright 2021-2022 Unisoc (Shanghai) Technologies Co., Ltd
*
* This program is free software; you can redistribute it and/or modify it
* under the terms of version 2 of the GNU General Public License
* as published by the Free Software Foundation.
*/

#ifndef __MM_H__
#define __MM_H__

#include <linux/dma-direct.h>
#include <linux/dma-mapping.h>
#include <linux/skbuff.h>

#ifdef CONFIG_64BIT
#define SPRD_PHYS_LEN 5
#else
#define SPRD_PHYS_LEN 4
#endif

#define SPRD_PHYS_MASK (((uint64_t)1 << 40) - 1)
#define SPRD_MH_ADDRESS_BIT ((uint64_t)1 << 39)

#define SPRD_MAX_MH_BUF 500
#define SPRD_ADD_MH_BUF_THRESHOLD 300
#define SPRD_MAX_ADD_MH_BUF_ONCE 100
#define SPRD_ADDR_BUF_LEN (sizeof(struct sprd_addr_hdr) +\
			     sizeof(struct addr_trans_value) +\
			     (SPRD_MAX_ADD_MH_BUF_ONCE * SPRD_PHYS_LEN))

#define SPRD_PROCESS_BUFFER 0
#define SPRD_FREE_BUFFER 1
#define SPRD_REQUEST_BUFFER 2
#define SPRD_FLUSH_BUFFER 3

struct addr_trans_value {
	unsigned char type;
	unsigned char num;
	unsigned char address[0][5];
} __packed;

struct addr_trans {
	unsigned int timestamp;
	unsigned short seq_num;
	unsigned char tlv_num;
	struct addr_trans_value value[0];
} __packed;

struct mem_mgmt {
	int hif_offset;
	struct sk_buff_head buffer_list;
	/* hdr point to hdr of addr buf */
	void *hdr;
	/* addr_trans point to addr trans of addr buf */
	void *addr_trans;
	atomic_t alloc_num;
};

struct mem_mgmt_tmp {
	int len;
	void *hdr;
};

int sc2355_mm_init(struct mem_mgmt *mm_entry, void *intf);
int sc2355_mm_deinit(struct mem_mgmt *mm_entry, void *intf);
void sc2355_mm_mh_data_process(struct mem_mgmt *mm_entry, void *data, int len,
			       int buffer_type);
void sc2355_mm_mh_data_event_process(struct mem_mgmt *mm_entry, void *data,
				     int len, int buffer_type);
unsigned long sc2355_mm_virt_to_phys(struct device *dev, void *buffer,
				     size_t size,
				     enum dma_data_direction direction);
void *sc2355_mm_phys_to_virt(struct device *dev, unsigned long pcie_addr,
			     size_t size, enum dma_data_direction direction,
			     bool is_mh);
int sc2355_mm_buffer_alloc(struct mem_mgmt *mm_entry, int need_num);
void sc2355_mm_flush_buffer(struct mem_mgmt *mm_entry);
void sc2355_free_data(void *data, int buffer_type);
#endif
