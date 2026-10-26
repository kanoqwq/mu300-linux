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

#include "cmdevt.h"
#include "common/cmd.h"
#include "common/common.h"
#include "common/debug.h"
#include "qos.h"
#include "reorder.h"
#include "rx.h"

static void reorder_ba_timeout(struct timer_list *t);

static inline unsigned int reorder_get_index_size(unsigned int size)
{
	unsigned int index_size = MIN_INDEX_SIZE;

	while (size > index_size)
		index_size = (index_size << 1);

	pr_info("%s: rx ba index size: %d\n", __func__, index_size);

	return index_size;
}

static inline void
reorder_set_ba_node_desc(struct rx_ba_node_desc *ba_node_desc,
			 unsigned short win_start, unsigned short win_size,
			 unsigned int index_mask)
{
	ba_node_desc->win_size = win_size;
	ba_node_desc->win_start = win_start;
	ba_node_desc->win_limit = SEQNO_ADD(ba_node_desc->win_start,
					    (ba_node_desc->win_size - 1));
	ba_node_desc->win_tail = SEQNO_SUB(ba_node_desc->win_start, 1);
	ba_node_desc->index_mask = index_mask;
	ba_node_desc->buff_cnt = 0;

	pr_info("%s:(win_start:%d, win_size:%d, win_tail:%d, index_mask:%d)\n",
		__func__, ba_node_desc->win_start, ba_node_desc->win_size,
		ba_node_desc->win_tail, ba_node_desc->index_mask);
}

static inline void reorder_set_ba_pkt_desc(struct rx_ba_pkt_desc *ba_pkt_desc,
					   struct rx_msdu_desc *msdu_desc)
{
	ba_pkt_desc->seq = msdu_desc->seq_num;
	ba_pkt_desc->pn_l = msdu_desc->pn_l;
	ba_pkt_desc->pn_h = msdu_desc->pn_h;
	ba_pkt_desc->cipher_type = msdu_desc->cipher_type;
}

static inline void
reorder_set_skb_list(struct rx_ba_entry *ba_entry,
		     struct sk_buff *skb_head, struct sk_buff *skb_last)
{
	spin_lock_bh(&ba_entry->skb_list_lock);
	if (!ba_entry->skb_head) {
		ba_entry->skb_head = skb_head;
		ba_entry->skb_last = skb_last;
	} else {
		ba_entry->skb_last->next = skb_head;
		ba_entry->skb_last = skb_last;
	}
	ba_entry->skb_last->next = NULL;
	spin_unlock_bh(&ba_entry->skb_list_lock);
}

static inline void reorder_mod_timer(struct rx_ba_node *ba_node)
{
	if (ba_node->rx_ba->buff_cnt) {
		mod_timer(&ba_node->reorder_timer,
			  jiffies + RX_BA_LOSS_RECOVERY_TIMEOUT);
	} else {
		del_timer(&ba_node->reorder_timer);
		ba_node->timeout_cnt = 0;
	}
}

static inline bool reorder_is_same_pn(struct rx_ba_pkt_desc *ba_pkt_desc,
				      struct rx_msdu_desc *msdu_desc)
{
	bool ret = true;
	unsigned char cipher_type = 0;

	cipher_type = ba_pkt_desc->cipher_type;
	if (cipher_type == SPRD_HW_TKIP || cipher_type == SPRD_HW_CCMP) {
		if (ba_pkt_desc->pn_l != msdu_desc->pn_l ||
		    ba_pkt_desc->pn_h != msdu_desc->pn_h)
			ret = false;
	}

	return ret;
}

static inline bool reorder_replay_detection(struct rx_ba_pkt_desc *ba_pkt_desc,
					    struct rx_ba_node_desc *ba_node_desc)
{
	bool ret = true;
	unsigned int old_val_low = 0;
	unsigned int old_val_high = 0;
	unsigned int rx_val_low = 0;
	unsigned int rx_val_high = 0;
	unsigned char cipher_type = 0;

	cipher_type = ba_pkt_desc->cipher_type;

	if (cipher_type == SPRD_HW_TKIP || cipher_type == SPRD_HW_CCMP) {
		old_val_low = ba_node_desc->pn_l;
		old_val_high = ba_node_desc->pn_h;
		rx_val_low = ba_pkt_desc->pn_l;
		rx_val_high = ba_pkt_desc->pn_h;

		if (ba_node_desc->reset_pn == 1 &&
		    old_val_low >= rx_val_low && old_val_high >= rx_val_high) {
			pr_err
			    ("%s: clear reset_pn,old_val_low: %d, old_val_high: %d,"
			     " rx_val_low: %d, rx_val_high: %d\n",
			     __func__, old_val_low, old_val_high, rx_val_low,
			     rx_val_high);
			ba_node_desc->reset_pn = 0;
			ba_node_desc->pn_l = rx_val_low;
			ba_node_desc->pn_h = rx_val_high;
		} else if (((old_val_high == rx_val_high) &&
			    (old_val_low < rx_val_low)) ||
			   (old_val_high < rx_val_high)) {
			ba_node_desc->pn_l = rx_val_low;
			ba_node_desc->pn_h = rx_val_high;
		} else {
			ret = false;
			pr_err("%s: old_val_low: %d, old_val_high: %d\n",
			       __func__, old_val_low, old_val_high);
			pr_err("%s: rx_val_low: %d, rx_val_high: %d\n",
			       __func__, rx_val_low, rx_val_high);
		}
	}

	return ret;
}

static inline void reorder_send_order_msdu(struct rx_ba_entry *ba_entry,
					   struct rx_msdu_desc *msdu_desc,
					   struct sk_buff *skb,
					   struct rx_ba_node_desc *ba_node_desc)
{
	struct rx_ba_pkt_desc ba_pkt_desc;

	reorder_set_ba_pkt_desc(&ba_pkt_desc, msdu_desc);
	ba_node_desc->win_start = SEQNO_ADD(ba_node_desc->win_start, 1);
	ba_node_desc->win_limit = SEQNO_ADD(ba_node_desc->win_limit, 1);
	ba_node_desc->win_tail = SEQNO_SUB(ba_node_desc->win_start, 1);

	pr_debug("%s: seq: %d\n", __func__, ba_pkt_desc.seq);
	pr_debug("%s: win_start: %d, win_tail: %d, buff_cnt: %d\n",
		 __func__, ba_node_desc->win_start,
		 ba_node_desc->win_tail, ba_node_desc->buff_cnt);

	if (skb) {
		if (reorder_replay_detection(&ba_pkt_desc, ba_node_desc))
			reorder_set_skb_list(ba_entry, skb, skb);
		else
			dev_kfree_skb(skb);
	}
}

static inline void
reorder_send_skb(struct rx_ba_entry *ba_entry,
		 struct rx_ba_node_desc *ba_node_desc, struct rx_ba_pkt *pkt)
{
	pr_debug("%s: seq: %d, msdu num: %d\n", __func__,
		 pkt->desc.seq, pkt->desc.msdu_num);

	if (pkt->skb && pkt->skb_last) {
		if (reorder_replay_detection(&pkt->desc, ba_node_desc))
			reorder_set_skb_list(ba_entry, pkt->skb, pkt->skb_last);
		else
			kfree_skb_list(pkt->skb);
	}

	memset(pkt, 0, sizeof(struct rx_ba_pkt));
}

static inline void reorder_flush_buffer(struct rx_ba_node_desc *ba_node_desc)
{
	int i = 0;
	struct rx_ba_pkt *pkt = NULL;

	for (i = 0; i < (ba_node_desc->index_mask + 1); i++) {
		pkt = &ba_node_desc->reorder_buffer[i];
		kfree_skb_list(pkt->skb);
		memset(pkt, 0, sizeof(struct rx_ba_pkt));
	}

	ba_node_desc->buff_cnt = 0;
}

static inline void reorder_joint_msdu(struct rx_ba_pkt *pkt,
				      struct sk_buff *newsk)
{
	if (newsk) {
		if (pkt->skb_last) {
			pkt->skb_last->next = newsk;
			pkt->skb_last = pkt->skb_last->next;
		} else {
			pkt->skb = newsk;
			pkt->skb_last = pkt->skb;
		}
		pkt->skb_last->next = NULL;
	}
}

static unsigned short reorder_send_msdu_in_order(struct rx_ba_entry *ba_entry,
						 struct rx_ba_node_desc *ba_node_desc)
{
	unsigned short seq_num = ba_node_desc->win_start;
	struct rx_ba_pkt *pkt = NULL;
	unsigned int index = 0;

	while (1) {
		index = seq_num & ba_node_desc->index_mask;
		pkt = &ba_node_desc->reorder_buffer[index];
		if (!ba_node_desc->buff_cnt || !pkt->desc.last)
			break;

		reorder_send_skb(ba_entry, ba_node_desc, pkt);
		ba_node_desc->buff_cnt--;
		seq_num++;
	}

	seq_num &= SEQNO_MASK;
	return seq_num;
}

static unsigned short
reorder_send_msdu_with_gap(struct rx_ba_entry *ba_entry,
			   struct rx_ba_node_desc *ba_node_desc,
			   unsigned short last_seqno)
{
	unsigned short seq_num = ba_node_desc->win_start;
	struct rx_ba_pkt *pkt = NULL;
	unsigned short num_frms = 0;
	unsigned short num = SEQNO_SUB(last_seqno, seq_num);
	unsigned int index = 0;

	while (num--) {
		index = seq_num & ba_node_desc->index_mask;
		pkt = &ba_node_desc->reorder_buffer[index];
		if (ba_node_desc->buff_cnt && pkt->desc.msdu_num) {
			reorder_send_skb(ba_entry, ba_node_desc, pkt);
			num_frms++;
			ba_node_desc->buff_cnt--;
		}
		seq_num++;
	}

	return num_frms;
}

static inline void reorder_between_seqlo_seqhi(struct rx_ba_entry *ba_entry,
					       struct rx_ba_node_desc *ba_node_desc)
{
	ba_node_desc->win_start =
	    reorder_send_msdu_in_order(ba_entry, ba_node_desc);
	ba_node_desc->win_limit =
	    SEQNO_ADD(ba_node_desc->win_start, (ba_node_desc->win_size - 1));
}

static inline void
reorder_greater_than_seqhi(struct rx_ba_entry *ba_entry,
			   struct rx_ba_node_desc *ba_node_desc,
			   unsigned short seq_num)
{
	unsigned short pos_win_end;
	unsigned short pos_win_start;

	pos_win_end = seq_num;
	pos_win_start = SEQNO_SUB(pos_win_end, (ba_node_desc->win_size - 1));
	reorder_send_msdu_with_gap(ba_entry, ba_node_desc, pos_win_start);
	ba_node_desc->win_start = pos_win_start;
	ba_node_desc->win_start =
	    reorder_send_msdu_in_order(ba_entry, ba_node_desc);
	ba_node_desc->win_limit =
	    SEQNO_ADD(ba_node_desc->win_start, (ba_node_desc->win_size - 1));
}

static inline void reorder_bar_send_ba_buffer(struct rx_ba_entry *ba_entry,
					      struct rx_ba_node_desc *ba_node_desc,
					      unsigned short seq_num)
{
	if (!seqno_leq(seq_num, ba_node_desc->win_start)) {
		reorder_send_msdu_with_gap(ba_entry, ba_node_desc, seq_num);
		ba_node_desc->win_start = seq_num;
		ba_node_desc->win_start =
		    reorder_send_msdu_in_order(ba_entry, ba_node_desc);
		ba_node_desc->win_limit =
		    SEQNO_ADD(ba_node_desc->win_start,
			      (ba_node_desc->win_size - 1));
	}
}

static inline int
reorder_insert_msdu(struct rx_msdu_desc *msdu_desc, struct sk_buff *skb,
		    struct rx_ba_node_desc *ba_node_desc)
{
	int ret = 0;
	unsigned short seq_num = msdu_desc->seq_num;
	unsigned short index = seq_num & ba_node_desc->index_mask;
	struct rx_ba_pkt *insert = &ba_node_desc->reorder_buffer[index];
	bool last_msdu_flag = msdu_desc->last_msdu_of_mpdu;

	pr_debug("%s: index: %d, seq: %d\n", __func__, index, insert->desc.seq);

	if (insert->desc.msdu_num != 0) {
		if (insert->desc.seq == seq_num && insert->desc.last != 1 &&
		    reorder_is_same_pn(&insert->desc, msdu_desc)) {
			reorder_joint_msdu(insert, skb);
			insert->desc.msdu_num++;
			insert->desc.last = last_msdu_flag;
		} else {
			pr_err("%s: in_use: %d\n", __func__, insert->desc.seq);
			ret = -EINVAL;
		}
	} else {
		reorder_joint_msdu(insert, skb);
		reorder_set_ba_pkt_desc(&insert->desc, msdu_desc);
		insert->desc.last = last_msdu_flag;
		insert->desc.msdu_num = 1;
		ba_node_desc->buff_cnt++;
	}

	return ret;
}

static int reorder_msdu(struct rx_ba_entry *ba_entry,
			struct rx_msdu_desc *msdu_desc,
			struct sk_buff *skb, struct rx_ba_node *ba_node)
{
	int ret = -EINVAL;
	unsigned short seq_num = msdu_desc->seq_num;
	struct rx_ba_node_desc *ba_node_desc = ba_node->rx_ba;

	if (seqno_geq(seq_num, ba_node_desc->win_start)) {
		if (!seqno_leq(seq_num, ba_node_desc->win_limit)) {
			/* Buffer is full, send data now */
			reorder_greater_than_seqhi(ba_entry, ba_node_desc,
						   seq_num);
		}

		ret = reorder_insert_msdu(msdu_desc, skb, ba_node_desc);
		if (!ret && seqno_geq(seq_num, ba_node_desc->win_tail))
			ba_node_desc->win_tail = seq_num;
	} else {
		/* MU300 (#140): a retransmission the window already moved past; the drop below is the normal outcome */
		pr_debug_ratelimited("%s: seq_num: %d is less than win_start: %d\n",
				     __func__, seq_num, ba_node_desc->win_start);
	}

	if (ret && skb) {
		pr_debug_ratelimited("%s: kfree skb %d", __func__, ret);
		dev_kfree_skb(skb);
	}

	return ret;
}

static void reorder_msdu_process(struct rx_ba_entry *ba_entry,
				 struct rx_msdu_desc *msdu_desc,
				 struct sk_buff *skb,
				 struct rx_ba_node *ba_node)
{
	struct rx_ba_node_desc *ba_node_desc = ba_node->rx_ba;
	int ret = 0;
	int seq_num = msdu_desc->seq_num;
	bool last_msdu_flag = msdu_desc->last_msdu_of_mpdu;
	unsigned short old_win_start = 0;

	spin_lock_bh(&ba_node->ba_node_lock);
	if (likely(ba_node->active)) {
		pr_debug("%s: seq: %d, last_msdu_of_mpdu: %d\n",
			 __func__, seq_num, last_msdu_flag);
		pr_debug("%s: win_start: %d, win_tail: %d, buff_cnt: %d\n",
			 __func__, ba_node_desc->win_start,
			 ba_node_desc->win_tail, ba_node_desc->buff_cnt);

		if (seq_num == ba_node_desc->win_start &&
		    !ba_node_desc->buff_cnt && last_msdu_flag) {
			reorder_send_order_msdu(ba_entry, msdu_desc, skb,
						ba_node_desc);
			goto out;
		}

		/* add log for bug:1592287 */
		if (seqno_geq(seq_num, ba_node_desc->win_start) &&
		    !msdu_desc->ampdu_flag) {
			pr_info("%s receive non ampdu packet, seq:%d, win_start:%d\n",
				__func__, seq_num, ba_node_desc->win_start);
		}

		old_win_start = ba_node_desc->win_start;
		ret = reorder_msdu(ba_entry, msdu_desc, skb, ba_node);
		if (!ret) {
			if (last_msdu_flag &&
			    seq_num == ba_node_desc->win_start) {
				reorder_between_seqlo_seqhi(ba_entry,
							    ba_node_desc);
				reorder_mod_timer(ba_node);
			} else if (!timer_pending(&ba_node->reorder_timer) ||
				   (old_win_start != ba_node_desc->win_start)) {
				pr_debug("%s: start timer\n", __func__);
				reorder_mod_timer(ba_node);
			}
		} else if (unlikely(!ba_node_desc->buff_cnt)) {
			/* Should never happen */
			del_timer(&ba_node->reorder_timer);
			ba_node->timeout_cnt = 0;
		}
	} else {
		pr_err
		    ("%s: BA SESSION IS NO ACTIVE sta_lut_index: %d, tid: %d\n",
		     __func__, msdu_desc->sta_lut_index, msdu_desc->tid);
		reorder_set_skb_list(ba_entry, skb, skb);
	}

out:
	spin_unlock_bh(&ba_node->ba_node_lock);
}

static inline void reorder_init_ba_node(struct rx_ba_entry *ba_entry,
					struct rx_ba_node *ba_node,
					unsigned char sta_lut_index,
					unsigned char tid)
{
	/* Init reorder spinlock */
	spin_lock_init(&ba_node->ba_node_lock);

	ba_node->active = 0;
	ba_node->sta_lut_index = sta_lut_index;
	ba_node->tid = tid;
	ba_node->ba_entry = ba_entry;

	/* Init reorder timer */
	timer_setup(&ba_node->reorder_timer, reorder_ba_timeout, 0);
}

static inline bool reorder_is_same_ba(struct rx_ba_node *ba_node,
				      unsigned char sta_lut_index,
				      unsigned char tid)
{
	bool ret = false;

	if (ba_node) {
		if (ba_node->sta_lut_index == sta_lut_index &&
		    ba_node->tid == tid)
			ret = true;
	}

	return ret;
}

static struct rx_ba_node
*reorder_find_ba_node(struct rx_ba_entry *ba_entry,
		      unsigned char sta_lut_index, unsigned char tid)
{
	struct rx_ba_node *ba_node = NULL;

	if (tid < NUM_TIDS) {
		if (reorder_is_same_ba
		    (ba_entry->current_ba_node, sta_lut_index, tid)) {
			ba_node = ba_entry->current_ba_node;
		} else {
			struct hlist_head *head = &ba_entry->hlist[tid];

			if (!hlist_empty(head)) {
				hlist_for_each_entry(ba_node, head, hlist) {
					if (sta_lut_index ==
					    ba_node->sta_lut_index) {
						ba_entry->current_ba_node =
						    ba_node;
						break;
					}
				}
			}
		}
	} else {
		pr_err("%s: TID is too large sta_lut_index: %d, tid: %d\n",
		       __func__, sta_lut_index, tid);
	}

	return ba_node;
}

static struct rx_ba_node
*reorder_create_ba_node(struct rx_ba_entry *ba_entry,
			unsigned char sta_lut_index, unsigned char tid,
			unsigned int size)
{
	struct rx_ba_node *ba_node = NULL;
	struct hlist_head *head = &ba_entry->hlist[tid];
	unsigned int rx_ba_size = sizeof(struct rx_ba_node_desc) +
	    (size * sizeof(struct rx_ba_pkt));

	ba_node = kzalloc(sizeof(*ba_node), GFP_ATOMIC);
	if (ba_node) {
		ba_node->rx_ba = kzalloc(rx_ba_size, GFP_ATOMIC);
		if (ba_node->rx_ba) {
			reorder_init_ba_node(ba_entry, ba_node, sta_lut_index,
					     tid);
			INIT_HLIST_NODE(&ba_node->hlist);
			hlist_add_head(&ba_node->hlist, head);
			ba_entry->current_ba_node = ba_node;
		} else {
			kfree(ba_node);
			ba_node = NULL;
		}
	}

	return ba_node;
}

static void reorder_wlan_filter_event(struct rx_ba_entry *ba_entry,
				      struct evt_ba *ba_event)
{
	struct rx_ba_node *ba_node = NULL;
	struct rx_msdu_desc msdu_desc;

	ba_node = reorder_find_ba_node(ba_entry,
				       ba_event->sta_lut_index, ba_event->tid);
	if (ba_node) {
		msdu_desc.last_msdu_of_mpdu = 1;
		msdu_desc.seq_num = ba_event->msdu_param.seq_num;
		msdu_desc.msdu_index_of_mpdu = 0;
		msdu_desc.pn_l = 0;
		msdu_desc.pn_h = 0;
		msdu_desc.cipher_type = 0;
		msdu_desc.tid = ba_event->tid;
		msdu_desc.sta_lut_index = ba_event->sta_lut_index;
		reorder_msdu_process(ba_entry, &msdu_desc, NULL, ba_node);
	}
}

static void reorder_wlan_delba_event(struct rx_ba_entry *ba_entry,
				     struct evt_ba *ba_event)
{
	struct rx_ba_node *ba_node = NULL;
	struct rx_ba_node_desc *ba_node_desc = NULL;

	pr_debug("enter %s\n", __func__);
	ba_node = reorder_find_ba_node(ba_entry,
				       ba_event->sta_lut_index, ba_event->tid);
	if (!ba_node) {
		/* MU300 (#140): the firmware sends DELBA for sessions the host never opened or already tore down */
		pr_debug("%s: NOT FOUND sta_lut_index: %d, tid: %d\n",
			 __func__, ba_event->sta_lut_index, ba_event->tid);
		return;
	}

	del_timer_sync(&ba_node->reorder_timer);
	spin_lock_bh(&ba_node->ba_node_lock);
	if (ba_node->active) {
		ba_node_desc = ba_node->rx_ba;
		ba_node->active = 0;
		ba_node->timeout_cnt = 0;
		reorder_between_seqlo_seqhi(ba_entry, ba_node_desc);
		reorder_flush_buffer(ba_node_desc);
	}
	hlist_del(&ba_node->hlist);
	spin_unlock_bh(&ba_node->ba_node_lock);

	kfree(ba_node->rx_ba);
	kfree(ba_node);
	ba_node = NULL;
	ba_entry->current_ba_node = NULL;
}

static void reorder_wlan_bar_event(struct rx_ba_entry *ba_entry,
				   struct evt_ba *ba_event)
{
	struct rx_ba_node *ba_node = NULL;
	struct rx_ba_node_desc *ba_node_desc = NULL;

	pr_debug("enter %s\n", __func__);
	ba_node = reorder_find_ba_node(ba_entry,
				       ba_event->sta_lut_index, ba_event->tid);
	if (!ba_node) {
		pr_err("%s: NOT FOUND sta_lut_index: %d, tid: %d\n",
		       __func__, ba_event->sta_lut_index, ba_event->tid);
		return;
	}

	spin_lock_bh(&ba_node->ba_node_lock);
	if (ba_node->active) {
		ba_node_desc = ba_node->rx_ba;
		if (!seqno_leq(ba_event->win_param.win_start,
			       ba_node_desc->win_start)) {
			reorder_bar_send_ba_buffer(ba_entry, ba_node_desc,
						   ba_event->win_param.win_start);
			reorder_mod_timer(ba_node);
		}

		pr_info("%s:(active:%d, tid:%d)\n",
			__func__, ba_node->active, ba_node->tid);
		pr_info("%s:(win_size:%d, win_start:%d, win_tail:%d)\n",
			__func__, ba_node_desc->win_size,
			ba_node_desc->win_start, ba_node_desc->win_tail);
	} else {
		pr_err
		    ("%s: BA SESSION IS NO ACTIVE sta_lut_index: %d, tid: %d\n",
		     __func__, ba_event->sta_lut_index, ba_event->tid);
	}
	spin_unlock_bh(&ba_node->ba_node_lock);
}

static void reorder_send_addba_rsp(struct rx_ba_entry *ba_entry,
				   unsigned char tid,
				   unsigned char sta_lut_index, int status)
{
	struct cmd_ba addba_rsp;
	struct sprd_hif *hif = NULL;
	struct sprd_peer_entry *peer_entry = NULL;
	struct rx_mgmt *rx_mgmt = container_of(ba_entry,
					       struct rx_mgmt,
					       ba_entry);

	hif = rx_mgmt->hif;
	if (hif->hw_type == SPRD_HW_SC2355_PCIE)
		peer_entry = sc2355_pcie_find_peer_entry_using_lut_index(hif, sta_lut_index);
	else
		peer_entry = sc2355_find_peer_entry_using_lut_index(hif, sta_lut_index);
	if (!peer_entry) {
		pr_err("%s, peer not found\n", __func__);
		return;
	}

	addba_rsp.type = SPRD_ADDBA_RSP_CMD;
	addba_rsp.tid = tid;
	ether_addr_copy(addba_rsp.da, peer_entry->tx.da);
	addba_rsp.success = (status) ? 0 : 1;

	sc2355_rx_send_cmd(hif, (void *)(&addba_rsp), sizeof(addba_rsp),
			   SPRD_WORK_BA_MGMT, peer_entry->ctx_id);
}

static void reorder_send_delba(struct rx_ba_entry *ba_entry,
			       unsigned short tid, unsigned char sta_lut_index)
{
	struct cmd_ba delba;
	struct sprd_hif *hif = NULL;
	struct sprd_peer_entry *peer_entry = NULL;
	struct rx_mgmt *rx_mgmt = container_of(ba_entry,
					       struct rx_mgmt,
					       ba_entry);

	hif = rx_mgmt->hif;
	peer_entry = sc2355_find_peer_entry_using_lut_index(hif, sta_lut_index);
	if (!peer_entry) {
		pr_err("%s, peer not found\n", __func__);
		return;
	}

	delba.type = SPRD_DELBA_CMD;
	delba.tid = tid;
	ether_addr_copy(delba.da, peer_entry->tx.da);
	delba.success = 1;

	sc2355_rx_send_cmd(hif, (void *)(&delba), sizeof(delba),
			   SPRD_WORK_BA_MGMT, peer_entry->ctx_id);
}

static int reorder_wlan_addba_event(struct rx_ba_entry *ba_entry,
				    struct evt_ba *ba_event)
{
	struct rx_ba_node *ba_node = NULL;
	int ret = 0;
	unsigned char sta_lut_index = ba_event->sta_lut_index;
	unsigned char tid = ba_event->tid;
	unsigned short win_start = ba_event->win_param.win_start;
	unsigned short win_size = ba_event->win_param.win_size;
	unsigned int index_size = reorder_get_index_size(2 * win_size);

	pr_debug("enter %s\n", __func__);
	ba_node = reorder_find_ba_node(ba_entry, sta_lut_index, tid);
	if (!ba_node) {
		ba_node = reorder_create_ba_node(ba_entry, sta_lut_index,
						 tid, index_size);
		if (!ba_node) {
			pr_err("%s: Create ba_entry fail\n", __func__);
			ret = -ENOMEM;
			goto out;
		}
	}

	spin_lock_bh(&ba_node->ba_node_lock);
	if (likely(!ba_node->active)) {
		reorder_set_ba_node_desc(ba_node->rx_ba, win_start, win_size,
					 INDEX_SIZE_MASK(index_size));
		ba_node->active = 1;
		pr_debug("%s:(active:%d, tid:%d)\n",
			 __func__, ba_node->active, ba_node->tid);
	} else {
		/* Should never happen */
		pr_err("%s: BA SESSION IS ACTIVE sta_lut_index: %d, tid: %d\n",
		       __func__, sta_lut_index, tid);
		ret = -EINVAL;
	}
	spin_unlock_bh(&ba_node->ba_node_lock);

out:
	return ret;
}

static unsigned short
reorder_get_first_seqno_in_buff(struct rx_ba_node_desc *ba_node_desc)
{
	unsigned short seqno = ba_node_desc->win_start;
	unsigned short index = 0;

	while (seqno_leq(seqno, ba_node_desc->win_tail)) {
		index = seqno & ba_node_desc->index_mask;
		if (ba_node_desc->reorder_buffer[index].desc.last)
			break;

		seqno = SEQNO_ADD(seqno, 1);
	}

	pr_debug("%s: first seqno: %d\n", __func__, seqno);
	return seqno;
}

static void reorder_ba_timeout(struct timer_list *t)
{
	struct rx_ba_node *ba_node = from_timer(ba_node, t, reorder_timer);
	struct rx_ba_entry *ba_entry = ba_node->ba_entry;
	struct rx_ba_node_desc *ba_node_desc = ba_node->rx_ba;
	struct rx_mgmt *rx_mgmt = container_of(ba_entry,
					       struct rx_mgmt,
					       ba_entry);
	unsigned short pos_seqno = 0;

	pr_debug("enter %s\n", __func__);
	sprd_debug_cnt_inc(REORDER_TIMEOUT_CNT);
	spin_lock_bh(&ba_node->ba_node_lock);
	if (ba_node->active && ba_node_desc->buff_cnt &&
	    !timer_pending(&ba_node->reorder_timer)) {
		pos_seqno = reorder_get_first_seqno_in_buff(ba_node_desc);
		reorder_send_msdu_with_gap(ba_entry, ba_node_desc, pos_seqno);
		ba_node_desc->win_start = pos_seqno;
		ba_node_desc->win_start =
		    reorder_send_msdu_in_order(ba_entry, ba_node_desc);
		ba_node_desc->win_limit =
		    SEQNO_ADD(ba_node_desc->win_start,
			      (ba_node_desc->win_size - 1));

		ba_node->timeout_cnt++;
		if (ba_node->timeout_cnt > MAX_TIMEOUT_CNT) {
			ba_node->active = 0;
			ba_node->timeout_cnt = 0;
			pr_info("%s, %d, reorder_send_delba\n", __func__,
				__LINE__);
			reorder_send_delba(ba_entry, ba_node->tid,
					   ba_node->sta_lut_index);
		}

		reorder_mod_timer(ba_node);
	}
	spin_unlock_bh(&ba_node->ba_node_lock);

	spin_lock_bh(&ba_entry->skb_list_lock);
	if (ba_entry->skb_head) {
		spin_unlock_bh(&ba_entry->skb_list_lock);

		if (!work_pending(&rx_mgmt->rx_work)) {
			pr_debug("%s: queue rx workqueue\n", __func__);
			queue_work(rx_mgmt->rx_queue, &rx_mgmt->rx_work);
		}
	} else {
		spin_unlock_bh(&ba_entry->skb_list_lock);
	}
	pr_debug("leave %s\n", __func__);
}

struct sk_buff *sc2355_reorder_get_skb_list(struct rx_ba_entry *ba_entry)
{
	struct sk_buff *skb = NULL;

	spin_lock_bh(&ba_entry->skb_list_lock);
	skb = ba_entry->skb_head;
	ba_entry->skb_head = NULL;
	ba_entry->skb_last = NULL;
	spin_unlock_bh(&ba_entry->skb_list_lock);

	return skb;
}

void sc2355_reset_pn(struct sprd_priv *priv, const u8 *mac_addr)
{
	struct sprd_hif *hif = NULL;
	struct rx_mgmt *rx_mgmt = NULL;
	struct rx_ba_entry *ba_entry = NULL;
	unsigned char i, tid, lut_id = 0xff;
	struct rx_ba_node *ba_node = NULL;

	if (!mac_addr)
		return;

	if (priv) {
		hif = &priv->hif;
		rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
		ba_entry = &rx_mgmt->ba_entry;
	} else {
		pr_err("%s: parameter priv is NULL\n", __func__);
		return;
	}

	for (i = 0; i < MAX_LUT_NUM; i++) {
		if (ether_addr_equal(hif->peer_entry[i].tx.da, mac_addr)) {
			lut_id = hif->peer_entry[i].lut_index;
			break;
		}
	}
	if (lut_id == 0xff)
		return;

	for (tid = 0; tid < NUM_TIDS; tid++) {
		ba_node = reorder_find_ba_node(ba_entry, lut_id, tid);
		if (ba_node) {
			spin_lock_bh(&ba_node->ba_node_lock);
			ba_node->rx_ba->reset_pn = 1;
			pr_err("%s: set,lut=%d, tid=%d, pn_l=%d, pn_h=%d\n",
			       __func__, lut_id, tid,
			       ba_node->rx_ba->pn_l, ba_node->rx_ba->pn_h);
			spin_unlock_bh(&ba_node->ba_node_lock);
		}
	}
}

struct sk_buff *sc2355_reorder_data_process(struct rx_ba_entry *ba_entry,
					    struct sk_buff *pskb)
{
	struct rx_ba_node *ba_node = NULL;
	struct rx_msdu_desc *msdu_desc = NULL;

	if (pskb) {
		msdu_desc = (struct rx_msdu_desc *)pskb->data;

		pr_debug("%s: qos_flag: %d, ampdu_flag: %d, bc_mc_flag: %d\n",
			 __func__, msdu_desc->qos_flag,
			 msdu_desc->ampdu_flag, msdu_desc->bc_mc_flag);

		if (!msdu_desc->bc_mc_flag && msdu_desc->qos_flag) {
			ba_node = reorder_find_ba_node(ba_entry,
						       msdu_desc->sta_lut_index,
						       msdu_desc->tid);
			if (ba_node)
				reorder_msdu_process(ba_entry, msdu_desc,
						     pskb, ba_node);
			else
				reorder_set_skb_list(ba_entry, pskb, pskb);
		} else {
			reorder_set_skb_list(ba_entry, pskb, pskb);
		}
	}

	return NULL;
}

void sc2355_wlan_ba_session_event(struct sprd_hif *hif, unsigned char *data,
				  unsigned short len)
{
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	struct rx_ba_entry *ba_entry = &rx_mgmt->ba_entry;
	struct evt_ba *ba_event = (struct evt_ba *)data;
	unsigned char type = ba_event->type;
	int ret = 0;
	struct sprd_peer_entry *peer_entry = NULL;
	u8 qos_index;

	if (ba_event->sta_lut_index >= MAX_LUT_NUM) {
		pr_err("%s, error sta_lut_index %d!\n", __func__, ba_event->sta_lut_index);
		return;
	}

	switch (type) {
	case SPRD_ADDBA_REQ_EVENT:
		ret = reorder_wlan_addba_event(ba_entry, ba_event);
		reorder_send_addba_rsp(ba_entry, ba_event->tid,
				       ba_event->sta_lut_index, ret);
		break;
	case SPRD_DELBA_EVENT:
		reorder_wlan_delba_event(ba_entry, ba_event);
		break;
	case SPRD_BAR_EVENT:
		reorder_wlan_bar_event(ba_entry, ba_event);
		break;
	case SPRD_FILTER_EVENT:
		reorder_wlan_filter_event(ba_entry, ba_event);
		break;
	case SPRD_DELTXBA_EVENT:
		peer_entry = &hif->peer_entry[ba_event->sta_lut_index];
		qos_index = sc2355_qos_tid_map_to_index(ba_event->tid);
		peer_entry = &hif->peer_entry[ba_event->sta_lut_index];
		if (test_and_clear_bit
		    (ba_event->tid, &peer_entry->ba_tx_done_map))
			pr_info("%s, %d, deltxba, lut=%d, tid=%d, map=%lu\n",
				__func__, __LINE__, ba_event->sta_lut_index,
				ba_event->tid, peer_entry->ba_tx_done_map);
		break;
	default:
		pr_err("%s: Error type: %d\n", __func__, type);
		break;
	}
}

void sc2355_reorder_init(struct rx_ba_entry *ba_entry)
{
	int i = 0;

	for (i = 0; i < NUM_TIDS; i++)
		INIT_HLIST_HEAD(&ba_entry->hlist[i]);

	spin_lock_init(&ba_entry->skb_list_lock);
}

void sc2355_reorder_deinit(struct rx_ba_entry *ba_entry)
{
	int i = 0;
	struct rx_ba_node *ba_node = NULL;

	for (i = 0; i < NUM_TIDS; i++) {
		struct hlist_head *head = &ba_entry->hlist[i];
		struct hlist_node *node = NULL;

		if (hlist_empty(head))
			continue;

		hlist_for_each_entry_safe(ba_node, node, head, hlist) {
			del_timer_sync(&ba_node->reorder_timer);
			spin_lock_bh(&ba_node->ba_node_lock);
			ba_node->active = 0;
			reorder_flush_buffer(ba_node->rx_ba);
			hlist_del(&ba_node->hlist);
			spin_unlock_bh(&ba_node->ba_node_lock);
			kfree(ba_node->rx_ba);
			kfree(ba_node);
			ba_node = NULL;
		}
	}
}

void sc2355_peer_entry_delba(struct sprd_hif *hif, unsigned char lut_index)
{
	int tid = 0;
	struct rx_ba_node *ba_node = NULL;
	struct rx_ba_node_desc *ba_node_desc = NULL;
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	struct rx_ba_entry *ba_entry = &rx_mgmt->ba_entry;

	pr_debug("enter %s\n", __func__);
	for (tid = 0; tid < NUM_TIDS; tid++) {
		ba_node = reorder_find_ba_node(ba_entry, lut_index, tid);
		if (ba_node) {
			pr_info("%s: del ba lut_index: %d, tid %d\n",
				__func__, lut_index, tid);
			del_timer_sync(&ba_node->reorder_timer);
			spin_lock_bh(&ba_node->ba_node_lock);
			if (ba_node->active) {
				ba_node_desc = ba_node->rx_ba;
				ba_node->active = 0;
				ba_node->timeout_cnt = 0;
				reorder_flush_buffer(ba_node_desc);
			}
			hlist_del(&ba_node->hlist);
			spin_unlock_bh(&ba_node->ba_node_lock);

			kfree(ba_node->rx_ba);
			kfree(ba_node);
			ba_node = NULL;
			ba_entry->current_ba_node = NULL;
		}
	}
}
