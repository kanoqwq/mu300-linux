// SPDX-License-Identifier: GPL-2.0-only
/* Copyright (C) 2019 Spreadtrum Communications Inc.
 *
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#include <linux/atomic.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/percpu-defs.h>
#include <linux/skbuff.h>
#include <linux/sched.h>
#include <linux/sipa.h>
#include <linux/netdevice.h>
#include <uapi/linux/sched/types.h>

#include "sipa_priv.h"
#include "sipa_hal.h"

#define SIPA_RECEIVER_BUF_LEN     1600

static void sipa_inform_evt_to_nics(struct sipa_skb_sender *sender,
				    enum sipa_evt_type evt)
{
	unsigned long flags;
	struct sipa_nic *nic;

	spin_lock_irqsave(&sender->nic_lock, flags);
	list_for_each_entry(nic, &sender->nic_list, list) {
		if (nic->flow_ctrl_status) {
			nic->flow_ctrl_status = false;
			sipa_nic_notify_evt(nic, evt);
		}
	}
	spin_unlock_irqrestore(&sender->nic_lock, flags);
}

static void sipa_sender_notify_cb(void *priv, enum sipa_hal_evt_type evt,
				  unsigned long data, int irq)
{
	struct sipa_skb_sender *sender = (struct sipa_skb_sender *)priv;

	if (evt & SIPA_RECV_WARN_EVT) {
		dev_err(sender->dev,
			"sipa overflow on ep:%d evt = 0x%x\n",
			sender->ep->id, evt);
		sender->no_free_cnt++;
		wake_up(&sender->free_waitq);
	}

	if (evt & SIPA_RECV_EVT)
		wake_up(&sender->free_waitq);

	if (evt & SIPA_HAL_ENTER_FLOW_CTRL)
		sender->enter_flow_ctrl_cnt++;

	if (evt & SIPA_HAL_EXIT_FLOW_CTRL)
		sender->exit_flow_ctrl_cnt++;
}

/**
 * sipa_find_sent_skb() - match the corresponding skb through dma address.
 * @addr: nic devices that need to be notified.
 *
 * If it returns NULL, it means the match failed, if it returned pointer is
 * normally, this interface will do unmap processing to ensure data consistency.
 */
struct sk_buff *sipa_find_sent_skb(dma_addr_t addr)
{
	unsigned long flags;
	struct sipa_skb_sender *sender;
	struct sipa_skb_dma_addr_pair *iter, *_iter;
	struct sipa_plat_drv_cfg *ipa = sipa_get_ctrl_pointer();

	if (!ipa)
		return NULL;

	sender = ipa->sender;
	spin_lock_irqsave(&sender->send_lock, flags);
	if (list_empty(&sender->sending_list)) {
		spin_unlock_irqrestore(&sender->send_lock, flags);
		dev_err(sender->dev, "send list is empty when find skb\n");
		return NULL;
	}

	list_for_each_entry_safe(iter, _iter,
				 &sender->sending_list, list) {
		if (iter->dma_addr == addr) {
			spin_unlock_irqrestore(&sender->send_lock, flags);
			dma_unmap_single(sender->dev, iter->dma_addr,
					 iter->skb->len +
					 skb_headroom(iter->skb),
					 DMA_TO_DEVICE);
			iter->need_unmap = false;
			return iter->skb;
		}
	}
	spin_unlock_irqrestore(&sender->send_lock, flags);

	return NULL;
}
EXPORT_SYMBOL(sipa_find_sent_skb);

static void sipa_free_sent_items(void)
{
	bool status = false;
	unsigned long flags;
	u32 i, num = 0, success_cnt = 0, retry_cnt = 10;
	struct sipa_node_desc_tag *node = NULL;
	struct sipa_skb_sender *sender;
	struct sipa_skb_dma_addr_pair *iter, *_iter;
	struct sipa_plat_drv_cfg *ipa = sipa_get_ctrl_pointer();
	struct skb_shared_info *shinfo;

	if (!ipa)
		return;

	sender = ipa->sender;
	sipa_hal_cmn_fifo_get_filled_depth(sender->dev,
					   sender->ep->send_fifo.idx,
					   NULL, &num);
	if (!num)
		return;

	sipa_hal_sync_node_from_tx_fifo(sender->dev,
					sender->ep->send_fifo.idx, num);
	for (i = 0; i < num; i++) {
		node = sipa_hal_get_tx_node_rptr(sender->dev,
						 sender->ep->send_fifo.idx, i);

		if (!node) {
			dev_err(sender->dev, "sender node is null\n");
			sipa_hal_add_tx_fifo_rptr(sender->dev,
						  sender->ep->send_fifo.idx,
						  1);
			return;
		}

		while (!node->address && --retry_cnt)
			udelay(1);

		/* MU300: per packet, and it comes in bursts while the delegate is not up yet: rate limited */
		if (node->err_code || !retry_cnt)
			dev_err_ratelimited(sender->dev,
				"node->address = 0x%llx, have node transfer err = %d retry_cnt = %d\n",
				(u64)node->address, node->err_code, retry_cnt);

		retry_cnt = 10;
check_again:
		spin_lock_irqsave(&sender->send_lock, flags);
		if (list_empty(&sender->sending_list)) {
			dev_err(sender->dev,
				"fifo id %d: send list is empty i = %d num = %d\n",
				sender->ep->send_fifo.idx, i, num);
			spin_unlock_irqrestore(&sender->send_lock, flags);
			continue;
		}

		list_for_each_entry_safe(iter, _iter,
					 &sender->sending_list, list) {
			if (iter->dma_addr == node->address) {
				list_del(&iter->list);
				list_add_tail(&iter->list,
					      &sender->pair_free_list);
				status = true;
				break;
			}
		}
		spin_unlock_irqrestore(&sender->send_lock, flags);
		if (status) {
			shinfo = skb_shinfo(iter->skb);
			if (iter->need_unmap) {
				iter->need_unmap = false;
				dma_unmap_single(sender->dev, iter->dma_addr,
						 iter->skb->len +
						 skb_headroom(iter->skb),
						 DMA_TO_DEVICE);
			}

			if (shinfo->frag_list || shinfo->nr_frags) {
				dev_err(sender->dev, "skb addr %p, skb nr frags is %d,"
					"skb has frag list %d\n",
					iter->skb,
					shinfo->nr_frags,
					skb_has_frag_list(iter->skb));
#ifdef NET_SKBUFF_DATA_USES_OFFSET
				dev_err(sender->dev, "skb len %d, skb data len %d,"
					"skb head room %d, skb head pointer %p,"
					"skb data pointer %p, skb tail pointer %d,"
					"skb end poineter %d\n",
					iter->skb->len, iter->skb->data_len,
					skb_headroom(iter->skb), iter->skb->head,
					iter->skb->data, iter->skb->tail,
					iter->skb->end);
#else
				dev_err(sender->dev, "skb len %d, skb data len %d,"
					"skb head room %d, skb head pointer %p,"
					"skb data pointer %p, skb tail pointer %p,"
					"skb end poineter %p\n",
					iter->skb->len, iter->skb->data_len,
					skb_headroom(iter->skb), iter->skb->head,
					iter->skb->data, iter->skb->tail,
					iter->skb->end);
#endif
				memset(shinfo, 0, offsetof(struct skb_shared_info, dataref));

				/* when network deliver skb to dev, we should modify shinfo->dataref carefully,
				 * because of other module holding payload dataref.
				 * entire dataref count must greater than or equal to the payload dataref.
				 * entire dataref include header part.
				 */
				dev_err(sender->dev, "payload dataref is %d, "
					"entire dataref is %d\n",
					atomic_read(&shinfo->dataref) >> SKB_DATAREF_SHIFT,
					atomic_read(&shinfo->dataref) & SKB_DATAREF_MASK);
			}

			dev_kfree_skb_any(iter->skb);
			success_cnt++;
			status = false;
		} else if (!status && --retry_cnt) {
			udelay(1);
			goto check_again;
		}
	}
	sipa_hal_add_tx_fifo_rptr(sender->dev,
				  sender->ep->send_fifo.idx, num);
	atomic_add(success_cnt, &sender->left_cnt);
	if (sender->free_notify_net &&
	    atomic_read(&sender->left_cnt) >
	    sender->ep->send_fifo.rx_fifo.fifo_depth / 4) {
		sender->free_notify_net = false;
		sipa_inform_evt_to_nics(sender, SIPA_LEAVE_FLOWCTRL);
	}
	if (num != success_cnt || i != num)
		dev_err(sender->dev,
			"i = %d recv num = %d release num = %d\n",
			i, num, success_cnt);
}

static bool sipa_sender_ck_unfree(struct sipa_skb_sender *sender)
{
	bool ret;

	atomic_set(&sender->check_flag, 1);
	if (atomic_read(&sender->check_suspend)) {
		atomic_set(&sender->check_flag, 0);
		dev_err(sender->dev,
			"sipa send ep id = %d, check_suspend = %d\n",
			sender->ep->id, atomic_read(&sender->check_suspend));
		return true;
	}

	ret = sipa_hal_get_tx_fifo_empty_status(sender->dev,
						sender->ep->send_fifo.idx);
	atomic_set(&sender->check_flag, 0);

	return ret;
}

static int sipa_free_thread(void *data)
{
	struct sipa_skb_sender *sender = (struct sipa_skb_sender *)data;
	struct sched_param param = {.sched_priority = 90};

	sched_setscheduler(current, SCHED_RR, &param);
	/*
	 * MU300: the vendor created this thread with kthread_create() and woke it only at the IPA's first runtime
	 * resume; on a device whose IPA never resumes (no SIM) it stayed in the kthread pre-start sleep, which is
	 * TASK_UNINTERRUPTIBLE: +1 load and a hung-task report every two minutes. It now starts at once and waits here,
	 * interruptibly, until that resume; from then on the loop is the vendor's (which must not run before it:
	 * the IPA is not powered, hence the loop around an early return).
	 */
	while (!READ_ONCE(sender->free_started) && !kthread_should_stop())
		wait_event_interruptible(sender->free_waitq,
					 READ_ONCE(sender->free_started) || kthread_should_stop());
	while (!kthread_should_stop()) {
		wait_event_interruptible(sender->free_waitq,
					 !sipa_sender_ck_unfree(sender) ||
					 sender->free_notify_net);
		sipa_free_sent_items();
	}
	return 0;
}

static int sipa_skb_sender_init(struct sipa_skb_sender *sender)
{
	struct sipa_comm_fifo_params attr;

	attr.tx_intr_delay_us = 1000;
	attr.tx_intr_threshold = 128;
	attr.flow_ctrl_cfg = flow_ctrl_tx_full;
	attr.flowctrl_in_tx_full = true;
	attr.errcode_intr = true;
	attr.flow_ctrl_irq_mode = enter_exit_flow_ctrl;
	attr.rx_enter_flowctrl_watermark = 0;
	attr.rx_leave_flowctrl_watermark = 0;
	attr.tx_enter_flowctrl_watermark = 0;
	attr.tx_leave_flowctrl_watermark = 0;

	sipa_hal_open_cmn_fifo(sender->dev,
			       sender->ep->send_fifo.idx,
			       &attr, NULL, true,
			       sipa_sender_notify_cb, sender);
	sender->init_flag = true;

	atomic_set(&sender->check_suspend, 0);
	atomic_set(&sender->check_flag, 0);

	return 0;
}

int sipa_sender_prepare_suspend(struct sipa_skb_sender *sender)
{
	atomic_set(&sender->check_suspend, 1);

	if (atomic_read(&sender->check_flag)) {
		dev_warn(sender->dev, "sender free_thread is running\n");
		atomic_set(&sender->check_suspend, 0);
		wake_up(&sender->free_waitq);
		return -EAGAIN;
	}

	if (!list_empty(&sender->sending_list)) {
		atomic_set(&sender->check_suspend, 0);
		dev_err(sender->dev, "sending list have unsend node\n");
		return -EAGAIN;
	}

	if (!sipa_hal_check_send_cmn_fifo_com(sender->dev,
					      sender->ep->send_fifo.idx)) {
		atomic_set(&sender->check_suspend, 0);
		dev_err(sender->dev, "sender have something to handle\n");
		return -EAGAIN;
	}

	return 0;
}

int sipa_sender_prepare_resume(struct sipa_skb_sender *sender)
{
	atomic_set(&sender->check_suspend, 0);
	if (unlikely(sender->init_flag)) {
		/* MU300: what used to be the thread's first wake-up (sipa_free_thread) */
		WRITE_ONCE(sender->free_started, true);
		wake_up(&sender->free_waitq);
		sender->init_flag = false;
	}

	return 0;
}

int sipa_create_skb_sender(struct sipa_plat_drv_cfg *ipa,
			   struct sipa_endpoint *ep,
			   struct sipa_skb_sender **sender_pp)
{
	int i, ret;

	struct sipa_skb_sender *sender = NULL;

	dev_info(ipa->dev, "ep->id = %d start\n", ep->id);
	sender = devm_kzalloc(ipa->dev, sizeof(*sender), GFP_KERNEL);
	if (!sender) {
		dev_err(ipa->dev, "alloc sender err\n");
		return -ENOMEM;
	}

	sender->pair_cache = devm_kcalloc(ipa->dev,
					  ep->send_fifo.rx_fifo.fifo_depth,
					  sizeof(struct sipa_skb_dma_addr_pair),
					  GFP_KERNEL);
	if (!sender->pair_cache) {
		dev_err(ipa->dev,
			"alloc sender pair_cache err rx depth = 0x%x\n",
			ep->send_fifo.rx_fifo.fifo_depth);
		kfree(sender);
		return -ENOMEM;
	}

	INIT_LIST_HEAD(&sender->nic_list);
	INIT_LIST_HEAD(&sender->sending_list);
	INIT_LIST_HEAD(&sender->pair_free_list);
	spin_lock_init(&sender->nic_lock);
	spin_lock_init(&sender->send_lock);

	for (i = 0; i < ep->send_fifo.rx_fifo.fifo_depth; i++)
		list_add_tail(&((sender->pair_cache + i)->list),
			      &sender->pair_free_list);

	sender->dev = ipa->dev;
	sender->ep = ep;

	atomic_set(&sender->left_cnt, ep->send_fifo.rx_fifo.fifo_depth);

	/* reigster sender ipa event callback */
	sipa_skb_sender_init(sender);

	init_waitqueue_head(&sender->free_waitq);

	sender->free_thread = kthread_create(sipa_free_thread, sender,
					     "sipa-free-%d", ep->id);
	if (IS_ERR(sender->free_thread)) {
		dev_err(sender->dev, "Failed to create kthread: sipa-free-%d\n",
			ep->id);
		ret = PTR_ERR(sender->free_thread);
		kfree(sender->pair_cache);
		kfree(sender);
		return ret;
	}
	wake_up_process(sender->free_thread);	/* MU300: it waits for free_started */

	*sender_pp = sender;

	return 0;
}

void destroy_sipa_skb_sender(struct sipa_skb_sender *sender)
{
	kfree(sender->pair_cache);
	kfree(sender);
}

void sipa_skb_sender_add_nic(struct sipa_skb_sender *sender,
			     struct sipa_nic *nic)
{
	unsigned long flags;

	spin_lock_irqsave(&sender->nic_lock, flags);
	list_add_tail(&nic->list, &sender->nic_list);
	spin_unlock_irqrestore(&sender->nic_lock, flags);
}

void sipa_skb_sender_remove_nic(struct sipa_skb_sender *sender,
				struct sipa_nic *nic)
{
	unsigned long flags;

	spin_lock_irqsave(&sender->nic_lock, flags);
	list_del(&nic->list);
	spin_unlock_irqrestore(&sender->nic_lock, flags);
}

int sipa_skb_sender_send_data(struct sipa_skb_sender *sender,
			      struct sk_buff *skb,
			      enum sipa_term_type dst,
			      u8 netid)
{
	unsigned long flags;
	dma_addr_t dma_addr;
	struct sipa_skb_dma_addr_pair *node;
	struct sipa_node_desc_tag *des;

	dma_addr = dma_map_single(sender->dev, skb->head,
				  skb->len + skb_headroom(skb),
				  DMA_TO_DEVICE);

	if (unlikely(dma_mapping_error(sender->dev, dma_addr)))
		return -ENOMEM;

	spin_lock_irqsave(&sender->send_lock, flags);
	if (!atomic_read(&sender->left_cnt)) {
		spin_unlock_irqrestore(&sender->send_lock, flags);
		dma_unmap_single(sender->dev, dma_addr,
				 skb->len + skb_headroom(skb),
				 DMA_TO_DEVICE);
		sender->no_free_cnt++;
		return -EAGAIN;
	}

	des = sipa_hal_get_rx_node_wptr(sender->dev,
					sender->ep->send_fifo.idx, 0);
	if (!des) {
		spin_unlock_irqrestore(&sender->send_lock, flags);
		dma_unmap_single(sender->dev, dma_addr,
				 skb->len + skb_headroom(skb),
				 DMA_TO_DEVICE);
		sender->no_free_cnt++;
		return -EAGAIN;
	}

	memset(des, 0, sizeof(*des));
	atomic_dec(&sender->left_cnt);
	des->address = dma_addr;
	des->length = skb->len;
	des->offset = skb_headroom(skb);
	des->net_id = netid;
	des->dst = dst;
	des->src = sender->ep->send_fifo.src_id;
	if (skb->ip_summed == CHECKSUM_PARTIAL)
		des->cs_en = 1;

	node = list_first_entry(&sender->pair_free_list,
				struct sipa_skb_dma_addr_pair,
				list);
	node->skb = skb;
	node->dma_addr = dma_addr;
	node->need_unmap = true;
	list_del(&node->list);
	list_add_tail(&node->list, &sender->sending_list);
	sipa_hal_sync_node_to_rx_fifo(sender->dev,
				      sender->ep->send_fifo.idx, 1);
	sipa_hal_add_rx_fifo_wptr(sender->dev,
				  sender->ep->send_fifo.idx, 1);
	spin_unlock_irqrestore(&sender->send_lock, flags);

	return 0;
}

bool sipa_skb_sender_check_send_complete(struct sipa_skb_sender *sender)
{
	struct sipa_plat_drv_cfg *ipa = sipa_get_ctrl_pointer();

	if (!ipa)
		return true;

	if (!ipa->enable_cnt || !ipa->power_flag)
		return true;

	return sipa_hal_check_send_cmn_fifo_com(sender->dev,
						sender->ep->send_fifo.idx);
}
