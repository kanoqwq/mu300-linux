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

#include "common/debug.h"
#include "pcie_buf.h"
#include "mm.h"
#include "pcie.h"

static struct sprd_buf_mm *g_tx_buf;

static inline void pcie_buf_node_init(struct sprd_buf_node *node)
{
	node->flag = SPRD_MEMORY_FREE;
	node->location = LOC_BUFF_FREE;
	node->ctxt_id = 0;
	node->next = NULL;
}

void pcie_buf_mm_dump(void)
{
	int i;

	if (g_tx_buf) {
		for (i = 0; i < g_tx_buf->max_num; i++)
			if (g_tx_buf->type == PCIE_BUF_SINGLE_TYPE)
				pr_err("%s:addr [%d]%p\n",
					__func__, i, g_tx_buf->addr[i]);
	}
}

struct sprd_buf_node *pcie_buf_mm_alloc(struct sprd_buf_mm *mm)
{
	struct sprd_buf_node *node = NULL;

	spin_lock_bh(&mm->freelock);
	if (likely(mm->head)) {
		node = mm->head;
		if (unlikely(mm->free_num == 1)) {
			mm->head = NULL;
			mm->tail = NULL;
		} else {
			mm->head = node->next;
		}
		mm->free_num--;
		node->next = NULL;
		node->flag = SPRD_MEMORY_ALLOC;
	}
	spin_unlock_bh(&mm->freelock);
	return node;
}

static inline struct sprd_buf_node *
sprd_buf_mm_single_alloc(struct sprd_buf_mm *mm)
{
	struct sprd_buf_node *node = NULL;

	if (mm->max_num >= mm->addr_count)
		return NULL;

	node = kmalloc(mm->len+sizeof(struct sprd_buf_node),
						GFP_DMA|GFP_KERNEL);
	if (node == NULL) {
		pr_warn("%s: alloc buf_node failed.\n", __func__);
		return NULL;
	}

	pcie_buf_node_init(node);
	spin_lock_bh(&mm->freelock);
	mm->addr[mm->max_num] = (void *)node;
	mm->max_num += 1;
	spin_unlock_bh(&mm->freelock);
	node->flag = SPRD_MEMORY_ALLOC;

	return node;
}

int pcie_buf_mm_free(struct sprd_buf_mm *mm,
					struct sprd_buf_node *node)
{
	if (node->flag != SPRD_MEMORY_ALLOC) {
		pr_err("%s:The buf[%p] is refree or bad.\n",
			__func__, node);
		pcie_buf_mm_dump();
		return -1;
	}

	spin_lock_bh(&mm->freelock);
	pcie_buf_node_init(node);
	if (mm->free_num)
		mm->tail->next = node;
	else
		mm->head = node;
	mm->tail = node;
	mm->free_num++;
	spin_unlock_bh(&mm->freelock);
	return 0;
}

struct sprd_buf_node *pcie_alloc_tx_buf(void)
{
	struct sprd_buf_node *node;

	if (g_tx_buf == NULL) {
		pr_err("%s:Tx_buf is not init.\n", __func__);
		return NULL;
	}
	node = pcie_buf_mm_alloc(g_tx_buf);
	if (node)
		return node;

	if (g_tx_buf->type == PCIE_BUF_SINGLE_TYPE)
		return sprd_buf_mm_single_alloc(g_tx_buf);
	return NULL;
}

void pcie_free_tx_buf(struct sprd_buf_node *node)
{
	if (g_tx_buf == NULL) {
		pr_err("%s:Tx_buf is not init.\n", __func__);
		return;
	}
	pcie_buf_mm_free(g_tx_buf, node);
}

int pcie_get_tx_buf_len(void)
{
	if (g_tx_buf == NULL)
		return 0;

	return g_tx_buf->len;
}

int pcie_get_tx_buf_num(void)
{
	if (g_tx_buf == NULL)
		return 0;
	if (g_tx_buf->type == PCIE_BUF_SINGLE_TYPE)
		return g_tx_buf->addr_count;
	return g_tx_buf->max_num;
}

int pcie_get_tx_buf_free_num(void)
{
	if (g_tx_buf == NULL)
		return 0;
	return g_tx_buf->free_num;
}

static inline int sprdwl_txrx_buf_single_init(void)
{
	int i;

	if (g_tx_buf) {
		pr_err("%s: txrx buf had been inited.\n", __func__);
		return -1;
	}

	g_tx_buf = kzalloc((sizeof(struct sprd_buf_mm)+
			    sizeof(unsigned long)*PCIE_TX_BUF_MAX_NUM),
			   GFP_KERNEL);
	if (!g_tx_buf) {
		pr_err("%s: alloc sprd_buf_mm fail", __func__);
		return -1;
	}
	spin_lock_init(&g_tx_buf->freelock);
	g_tx_buf->type = PCIE_BUF_SINGLE_TYPE;
	g_tx_buf->len = SPRD_MAX_DATA_TXLEN;
	g_tx_buf->addr_count = PCIE_TX_BUF_MAX_NUM;

	for (i = 0; i < g_tx_buf->addr_count; i++) {
		struct sprd_buf_node *node;

		node = sprd_buf_mm_single_alloc(g_tx_buf);
		if (node)
			pcie_free_tx_buf(node);
	}
	return 0;
}

static inline void
pcie_buf_single_deinit(struct sprd_buf_mm *mm)
{
	int i;

	for (i = 0; i < mm->max_num; i++) {
		kfree(mm->addr[i]);
		mm->addr[i] = NULL;
	}
}

int pcie_buf_init(void)
{
	return sprdwl_txrx_buf_single_init();
}

void pcie_buf_deinit(void)
{
	if (g_tx_buf) {
		if (g_tx_buf->type == PCIE_BUF_SINGLE_TYPE)
			pcie_buf_single_deinit(g_tx_buf);
		kfree(g_tx_buf);
		g_tx_buf = NULL;
	}

}

int pcie_skb_to_tx_buf(struct sprd_hif *dev,
						struct sprd_msg *msg_pos)
{
	struct sk_buff *skb = msg_pos->skb;
	unsigned long dma_addr = 0;
	struct sprd_buf_node *node = NULL;

	node = pcie_alloc_tx_buf();
	if (unlikely(node == NULL) || unlikely(node->buf == NULL)) {
		pr_debug("%s: alloc tx buf fail.\n", __func__);
		return -1;
	}

	node->location = LOC_TX_INTF;
	/*NOTE : next memcpy do save  SAVE_ADDR(node->buf, msg_pos, sizeof(msg_pos)) */
	memcpy(&node->addr, &msg_pos, sizeof(msg_pos));
	if (skb->len > pcie_get_tx_buf_len()) {
		pr_err("%s: skb->len(%d) > tx buf len(%d).\n",
			__func__, skb->len, pcie_get_tx_buf_len());
		pcie_free_tx_buf(node);
		return -1;
	}

	memcpy(node->buf, skb->data, skb->len);
	dma_addr = virt_to_phys(node->buf) | SPRD_MH_ADDRESS_BIT;
	memcpy(node->buf, &dma_addr, MSDU_DSCR_RSVD);
	dev_kfree_skb(msg_pos->skb);
	msg_pos->skb = NULL;
	msg_pos->node = node;
	msg_pos->tran_data = node->buf;
	msg_pos->pcie_addr = sc2355_mm_virt_to_phys(&dev->pdev->dev,
					     msg_pos->tran_data,
					     msg_pos->len,
					     DMA_TO_DEVICE);

	return 0;
}

