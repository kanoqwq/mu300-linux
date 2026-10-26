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

#ifndef __REORDER_H__
#define __REORDER_H__

#include <linux/skbuff.h>
#include <linux/timer.h>

#include "common/hif.h"

#define NUM_TIDS		8
#define RX_BA_LOSS_RECOVERY_TIMEOUT	(HZ / 10)
#define MAX_TIMEOUT_CNT		60
#define MIN_INDEX_SIZE		BIT(6)
#define INDEX_SIZE_MASK(index_size)	((index_size) - 1)

struct rx_ba_pkt_desc {
	unsigned int pn_l;
	unsigned short pn_h;
	unsigned short seq;
	unsigned char cipher_type;
	unsigned char last;
	unsigned short msdu_num;
};

struct rx_ba_pkt {
	struct sk_buff *skb;
	struct sk_buff *skb_last;
	struct rx_ba_pkt_desc desc;
};

struct rx_ba_node_desc {
	unsigned short win_start;
	unsigned short win_limit;
	unsigned short win_tail;
	unsigned short win_size;
	unsigned short buff_cnt;
	unsigned short pn_h;
	unsigned int pn_l;
	unsigned char reset_pn;
	unsigned int index_mask;
	struct rx_ba_pkt reorder_buffer[0];
};

struct rx_ba_node {
	unsigned char sta_lut_index;
	unsigned char tid;
	unsigned char active;
	unsigned char timeout_cnt;
	struct hlist_node hlist;
	struct rx_ba_node_desc *rx_ba;

	/* For reorder timeout */
	spinlock_t ba_node_lock;
	struct timer_list reorder_timer;
	struct rx_ba_entry *ba_entry;
};

struct rx_ba_entry {
	struct hlist_head hlist[NUM_TIDS];
	struct rx_ba_node *current_ba_node;
	/* protect skb_list */
	spinlock_t skb_list_lock;
	struct sk_buff *skb_head;
	struct sk_buff *skb_last;
};

void sc2355_reorder_init(struct rx_ba_entry *ba_entry);
void sc2355_reorder_deinit(struct rx_ba_entry *ba_entry);
struct sk_buff *sc2355_reorder_data_process(struct rx_ba_entry *ba_entry,
					    struct sk_buff *pskb);
struct sk_buff *sc2355_reorder_get_skb_list(struct rx_ba_entry *ba_entry);
void sc2355_wlan_ba_session_event(struct sprd_hif *hif, unsigned char *data,
				  unsigned short len);
void sc2355_peer_entry_delba(struct sprd_hif *hif, unsigned char sta_lut_index);
void sc2355_reset_pn(struct sprd_priv *priv, const u8 *mac_addr);

#endif
