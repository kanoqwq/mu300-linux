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

#ifndef __PCIE_BUF_MM_H__
#define __PCIE_BUF_MM_H__

#include <linux/types.h>
#include <linux/spinlock.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include "common/msg.h"
#include "common/hif.h"

enum {
	LOC_BUFF_FREE,
	LOC_TX_INTF,
	LOC_RX_INTF,
};

enum {
	SPRD_MEMORY_FREE,
	SPRD_MEMORY_ALLOC = 0x5a,
};

struct sprd_buf_node {
	u8 flag;
	u8 ctxt_id;
	u8 location;
	u8 rsve;
	union {
		struct sprd_buf_node *next;
		void *addr;
	};
	u8 buf[0];
} __packed;

#define PCIE_BUF_BLOCK_TYPE               (1)
#define PCIE_BUF_SINGLE_TYPE              (0)
#define PCIE_TX_BUF_MAX_NUM               (1024)
struct sprd_buf_mm {
	struct sprd_buf_node *head;
	struct sprd_buf_node *tail;
	int max_num;
	int free_num;
	int len;
	int type;
	spinlock_t freelock;
	int addr_count;
	union {
		unsigned long buf_addr[0][2];
		void *addr[0];
	};
};

#define SPRD_ALIGN(len, align) ((((len)+(align)-1)/(align)) * (align))
#define SPRD_SIPC_ALIGN		4
#define SPRD_BUF_SET_LOC(node, loc)		((node)->location = (loc))

extern struct sprd_buf_node *pcie_buf_mm_alloc(struct sprd_buf_mm *mm);
extern int pcie_buf_mm_free(struct sprd_buf_mm *mm,
			      struct sprd_buf_node *node);
extern struct sprd_buf_node *pcie_alloc_tx_buf(void);
extern void pcie_free_tx_buf(struct sprd_buf_node *node);
extern int pcie_get_tx_buf_len(void);
extern int pcie_get_tx_buf_num(void);
extern int pcie_get_tx_buf_free_num(void);
extern int pcie_buf_init(void);
extern void pcie_buf_deinit(void);
extern int pcie_skb_to_tx_buf(struct sprd_hif *dev,
				struct sprd_msg *msg_pos);
#endif /*__PCIE_BUF_MM_H__*/
