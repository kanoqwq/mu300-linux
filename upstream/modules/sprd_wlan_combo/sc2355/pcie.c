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

#include <linux/io.h>
#include <linux/regmap.h>
#include <linux/of_device.h>
#include <linux/rcupdate.h>

#include <misc/marlin_platform.h>

#include "common/common.h"
#include "common/chip_ops.h"
#include "common/delay_work.h"
#include "pcie.h"
#include "rx.h"
#include "tx.h"
#include "qos.h"
#include "cmdevt.h"
#include "txrx.h"

#define SPRD_NORMAL_MEM	0
#define SPRD_DEFRAG_MEM	1

#define INIT_INTF_SC2355(num, type, out, interval, bsize, psize, max, \
		threshold, time, in_irq, pending, pop, push, complete, suspend) \
		{ .channel = num, .hif_type = type, .inout = out, .intr_interval = interval, \
		.buf_size = bsize, .pool_size = psize, .once_max_trans = max, \
		.rx_threshold = threshold, .timeout = time, .cb_in_irq = in_irq, \
		.max_pending = pending, .pop_link = pop, .push_link = push, \
		.tx_complete = complete, .power_notify = suspend }

struct sc2355_hif {
	unsigned int max_num;
	void *hif;
	struct mchn_ops_t *mchn_ops;
};

static struct sc2355_hif sc2355_hif;

#if defined(MORE_DEBUG)
static void pcie_dump_stats(struct sprd_hif *hif)
{
	pr_err("++print txrx statistics++\n");
	pr_err("tx packets: %lu, tx bytes: %lu\n", hif->stats.tx_packets,
	       hif->stats.tx_bytes);
	pr_err("tx filter num: %lu\n", hif->stats.tx_filter_num);
	pr_err("tx errors: %lu, tx dropped: %lu\n", hif->stats.tx_errors,
	       hif->stats.tx_dropped);
	pr_err("tx avg time: %lu\n", hif->stats.tx_avg_time);
	pr_err("tx realloc: %lu\n", hif->stats.tx_realloc);
	pr_err("tx arp num: %lu\n", hif->stats.tx_arp_num);
	pr_err("rx packets: %lu, rx bytes: %lu\n", hif->stats.rx_packets,
	       hif->stats.rx_bytes);
	pr_err("rx errors: %lu, rx dropped: %lu\n", hif->stats.rx_errors,
	       hif->stats.rx_dropped);
	pr_err("rx multicast: %lu, tx multicast: %lu\n",
	       hif->stats.rx_multicast, hif->stats.tx_multicast);
	pr_err("--print txrx statistics--\n");
}

static void pcie_clear_stats(struct sprd_hif *hif)
{
	memset(&hif->stats, 0x0, sizeof(struct txrx_stats));
}

/*calculate packets  average sent time from received
 *from network stack to freed by HIF every STATS_COUNT packets
 */
static void pcie_get_tx_avg_time(struct sprd_hif *hif,
				 unsigned long tx_start_time)
{
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	struct timespec64 tx_end;

	ktime_get_real_ts64(&tx_end);
	hif->stats.tx_cost_time += timespec64_to_ns(&tx_end) - tx_start_time;
#else
	struct timespec tx_end;

	getnstimeofday(&tx_end);
	hif->stats.tx_cost_time += timespec_to_ns(&tx_end) - tx_start_time;
#endif
	if (hif->stats.gap_num >= STATS_COUNT) {
		hif->stats.tx_avg_time =
		    hif->stats.tx_cost_time / hif->stats.gap_num;
		pcie_dump_stats(hif);
		hif->stats.gap_num = 0;
		hif->stats.tx_cost_time = 0;
		pr_info("%s:%d packets avg cost time: %lu\n",
			__func__, __LINE__, hif->stats.tx_avg_time);
	}
}
#endif

unsigned long pcie_mbufalloc;
unsigned long pcie_mbufpop;
static int pcie_tx_one(struct sprd_hif *hif, unsigned char *data,
		       int len, int chn)
{
	int ret;
	struct mbuf_t *head = NULL, *tail = NULL, *mbuf = NULL;
	int num = 1;

	ret = sprdwcn_bus_list_alloc(chn, &head, &tail, &num);
	//ret = 0;
	if (ret || !head || !tail) {
		/* MU300: rate-limited, it fails on every command while the WCN is in dump status (issue #94) */
		pr_err_ratelimited("%s:%d sprdwcn_bus_list_alloc fail\n",
				   __func__, __LINE__);
		return -1;
	}

	pcie_mbufalloc += num;
	mbuf = head;
	mbuf->buf = data;
	mbuf->len = len;
	mbuf->next = NULL;
	/* Command payloads may contain passphrases, PSKs or PMKs. */
	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		mbuf->phy = sc2355_mm_virt_to_phys(&hif->pdev->dev, mbuf->buf,
						   mbuf->len, DMA_TO_DEVICE);
		ret = sc2355_pcie_push_link(hif, chn, head, tail, num,
				       sc2355_pcie_tx_cmd_pop_list);
	} else {
		ret = sprdwcn_bus_push_list(chn, head, tail, num);
	}

	if (ret) {
		mbuf = head;
		if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
			sc2355_mm_phys_to_virt(&hif->pdev->dev, mbuf->phy,
					       mbuf->len, DMA_TO_DEVICE, false);
			mbuf->phy = 0;
		}
		kfree(mbuf->buf);
		mbuf->buf = NULL;

		sprdwcn_bus_list_free(chn, head, tail, num);
		pcie_mbufalloc -= num;
	}

	return ret;
}

#define ADDR_OFFSET 7
static inline struct pcie_addr_buffer
*pcie_alloc_pcie_addr_buf(int tx_count)
{
	struct pcie_addr_buffer *addr_buffer;

	addr_buffer =
	    kzalloc(sizeof(struct pcie_addr_buffer) +
		    tx_count * SPRD_PHYS_LEN, GFP_ATOMIC);
	mb();
	if (!addr_buffer) {
		pr_err("%s:%d alloc pcie addr buf fail\n", __func__, __LINE__);
		return NULL;
	}
	addr_buffer->common.type = SPRD_TYPE_DATA_PCIE_ADDR;
	addr_buffer->common.direction_ind = 0;
	addr_buffer->common.buffer_type = 1;
	addr_buffer->number = tx_count;
	addr_buffer->offset = ADDR_OFFSET;
	addr_buffer->buffer_ctrl.buffer_inuse = 1;

	return addr_buffer;
}

static inline struct pcie_addr_buffer
*pcie_set_addr_to_mbuf(struct tx_mgmt *tx_mgmt, struct mbuf_t *mbuf,
		       int tx_count)
{
	struct pcie_addr_buffer *addr_buffer;

	addr_buffer = pcie_alloc_pcie_addr_buf(tx_count);
	if (!addr_buffer)
		return NULL;
	mbuf->len = ADDR_OFFSET + tx_count * SPRD_PHYS_LEN;
	mbuf->buf = (unsigned char *)addr_buffer;
	if (sprd_get_debug_level() >= L_DBG)
		sc2355_hex_dump("tx buf:", mbuf->buf, mbuf->len);

	return addr_buffer;
}

/*cut data list from tx data list*/
static inline void
pcie_list_cut_position(struct list_head *tx_list_head,
		       struct list_head *tx_list,
		       struct list_head *tail_entry, int ac_index)
{
	spinlock_t *lock;
	struct sprd_msg *msg = NULL;

	if (!tail_entry)
		return;
	msg = list_first_entry(tx_list, struct sprd_msg, list);
	if (msg->msg_type != SPRD_TYPE_DATA) {
		lock = &msg->msglist->busylock;
	} else {
		if (ac_index != SPRD_AC_MAX)
			lock = &msg->data_list->p_lock;
		else
			lock = &msg->xmit_msg_list->send_lock;
	}
	spin_lock_bh(lock);
	list_cut_position(tx_list_head, tx_list, tail_entry);
	spin_unlock_bh(lock);
}

static inline void
sprdwl_list_cut_to_send_list(struct list_head *head_entry,
			     struct list_head *tail_entry,
			     int count)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct tx_mgmt *tx_msg = (struct tx_mgmt *)hif->tx_mgmt;
	struct list_head list_tmp;
	struct list_head *head;

	INIT_LIST_HEAD(&list_tmp);
	spin_lock(&tx_msg->xmit_msg_list.free_lock);
	head = head_entry->prev;
	list_cut_position(&list_tmp, head, tail_entry);
	atomic_sub(count, &tx_msg->xmit_msg_list.free_num);
	spin_unlock(&tx_msg->xmit_msg_list.free_lock);
	list_splice(&list_tmp, &tx_msg->xmit_msg_list.to_send_list);
}

/*cut data list from tx data list*/
static inline int
sprdwl_list_cut_to_free_list(struct list_head *tx_list_head,
		struct list_head *tx_list, struct list_head *tail_entry,
		int tx_count)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct tx_mgmt *tx_msg = (struct tx_mgmt *)hif->tx_mgmt;
	int ret = 0;
	struct list_head tx_list_tmp;

	if (tail_entry == NULL) {
		pr_err("%s, %d, error tail_entry\n", __func__, __LINE__);
		return -1;
	}

	INIT_LIST_HEAD(&tx_list_tmp);
	list_cut_position(&tx_list_tmp, tx_list, tail_entry);
	spin_lock(&tx_msg->xmit_msg_list.free_lock);
	list_splice_tail(&tx_list_tmp, &tx_msg->xmit_msg_list.to_free_list);
	spin_unlock(&tx_msg->xmit_msg_list.free_lock);
	atomic_add(tx_count, &tx_msg->xmit_msg_list.free_num);

	return ret;
}

static inline int
pcie_list_cut_to_free_list(struct list_head *tx_list_head,
			   struct list_head *tx_list,
			   struct list_head *tail_entry, int tx_count)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	struct sprd_work *misc_work = NULL;
	int ret = 0;

	if (!tail_entry) {
		pr_err("%s, %d, error tail_entry\n", __func__, __LINE__);
		return -1;
	}

	/* TODO: How to do with misc_work = NULL? */
	misc_work = sprd_alloc_work(sizeof(struct list_head));
	if (misc_work) {
		if (hif->hw_type == SPRD_HW_SC2355_PCIE)
			misc_work->id = SPRD_PCIE_TX_MOVE_BUF;
		misc_work->len = tx_count;
		misc_work->hw_type = SPRD_HW_SC2355_PCIE;
		INIT_LIST_HEAD((struct list_head *)misc_work->data);
		list_cut_position((struct list_head *)misc_work->data,
				  tx_list, tail_entry);

		sprd_queue_work(hif->priv, misc_work);
		atomic_add(tx_count, &tx_mgmt->xmit_msg_list.free_num);
	} else {
		pr_err("%s: fail to alloc tx move misc work\n", __func__);
		ret = -1;
	}

	return ret;
}

static int pcie_rx_fill_mbuf(struct mbuf_t *head, struct mbuf_t *tail, int num,
			     int len)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	int ret = 0, count = 0;
	struct mbuf_t *pos = NULL;

	/* Called from the PCIe interrupt: a NULL here is a kernel panic, not an error return (seen at ~30 s of
	 * a boot, "NULL pointer dereference at 0000000000000000" in pcie_rx_fill_mbuf+0x6c).
	 */
	if (!hif || !hif->pdev) {
		pr_warn("%s: no hif yet\n", __func__);
		return -ENODEV;
	}

	for (pos = head, count = 0; count < num; count++) {
		pr_debug("%s: pos: %p\n", __func__, pos);
		/* the bus hands us a list it says has num entries; believe the list, not the count */
		if (unlikely(!pos)) {
			pr_warn("%s: list ended after %d of %d buffers\n", __func__, count, num);
			ret = -EINVAL;
			break;
		}
		pos->len = ALIGN(len, SMP_CACHE_BYTES);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		pos->buf = __netdev_alloc_frag_align(pos->len, ~0u);
#else
		pos->buf = netdev_alloc_frag(pos->len);
#endif

		if (unlikely(!pos->buf)) {
			pr_err("%s: buffer error\n", __func__);
			ret = -ENOMEM;
			break;
		}

		pos->phy = sc2355_mm_virt_to_phys(&hif->pdev->dev, pos->buf,
						  pos->len, DMA_FROM_DEVICE);

		if (unlikely(!pos->phy)) {
			pr_err("%s: buffer error\n", __func__);
			ret = -ENOMEM;
			break;
		}

		pos = pos->next;
	}

	if (ret) {
		pos = head;
		while (count-- && pos) {
			sc2355_free_data(pos->buf, SPRD_DEFRAG_MEM);
			pos = pos->next;
		}
	}

	return ret;
}

static int pcie_rx_common_push(int chn, struct mbuf_t **head,
			       struct mbuf_t **tail, int *num, int len)
{
	int ret = 0;

	if (0 == (*num))
		return ret;

	ret = sprdwcn_bus_list_alloc(chn, head, tail, num);
	if (ret || head == NULL || tail == NULL || *head == NULL || *tail == NULL) {
		/* MU300: rate-limited, as in pcie_tx_one */
		pr_err_ratelimited("%s:%d sprdwcn_bus_list_alloc fail\n", __func__,
				   __LINE__);
		ret = -ENOMEM;
	} else {
		ret = pcie_rx_fill_mbuf(*head, *tail, *num, len);
		if (ret) {
			pr_err("%s: alloc buf fail\n", __func__);
			sprdwcn_bus_list_free(chn, *head, *tail, *num);
			*head = NULL;
			*tail = NULL;
			*num = 0;
		}
	}

	return ret;
}

static int pcie_rx_handle(int chn, struct mbuf_t *head,
			  struct mbuf_t *tail, int num)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct rx_mgmt *rx_mgmt;
	struct sprd_msg *msg = NULL;
	int buf_num = 0, len = 0, ret = 0;
	struct mbuf_t *pos = head;

	pr_debug("%s: channel:%d head:%p tail:%p num:%d\n",
	       __func__, chn, head, tail, num);

	/*
	 * MU300: an RX interrupt outside the channels' lifetime (post_init publishes hif before it registers
	 * them, post_deinit clears it after it unregistered them, sprd_iface_set_power serialises the two).
	 * Without hif there is no device to unmap the buffers from and no queue to hand them to, and the
	 * channel's mbuf pool is already freed (no sprdwcn_bus_list_free): the data buffers of this one list
	 * leak, and it is said once.  A NULL here was a panic in hard IRQ (FINDINGS 31n).
	 */
	rx_mgmt = hif ? (struct rx_mgmt *)READ_ONCE(hif->rx_mgmt) : NULL;
	if (unlikely(!rx_mgmt)) {
		pr_err_once("%s: RX on channel %d with no Wi-Fi context, %d buffer(s) dropped\n",
			    __func__, chn, num);
		return 0;
	}

	for (buf_num = num; buf_num > 0; buf_num--, pos = pos->next) {
		if (unlikely(!pos)) {
			pr_err("%s: NULL mbuf\n", __func__);
			break;
		}

		sc2355_mm_phys_to_virt(&hif->pdev->dev, pos->phy,
				       pos->len, DMA_FROM_DEVICE,
				       false);
		pos->phy = 0;

		msg = sprd_alloc_msg(&rx_mgmt->rx_list);
		if (!msg) {
			pr_err("%s: no more msg\n", __func__);
			sc2355_free_data(pos->buf, SPRD_DEFRAG_MEM);
			pos->buf = NULL;
			continue;
		}

		sprd_fill_msg(msg, NULL, pos->buf, pos->len);

		msg->fifo_id = chn;
		msg->buffer_type = SPRD_DEFRAG_MEM;
		msg->data = msg->tran_data + hif->hif_offset;
		pos->buf = NULL;

		sprd_queue_msg(msg, &rx_mgmt->rx_list);
	}

	if (hif->rx_cmd_port == chn)
		len = SPRD_MAX_CMD_RXLEN;
	else
		len = SPRD_MAX_DATA_RXLEN;

	ret = pcie_rx_fill_mbuf(head, tail, num, len);
	if (ret) {
		pr_err("%s: alloc buf fail\n", __func__);
		sprdwcn_bus_list_free(chn, head, tail, num);
		head = NULL;
		tail = NULL;
		num = 0;
	}

	if (!ret)
		sprdwcn_bus_push_list(chn, head, tail, num);

	if (!work_pending(&rx_mgmt->rx_work))
		queue_work(rx_mgmt->rx_queue, &rx_mgmt->rx_work);

	return 0;
}

#ifdef CONFIG_SPRD_WLAN_NAPI
static int pcie_data_rx_handle(int chn, struct mbuf_t *head,
			       struct mbuf_t *tail, int num)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	struct sprd_msg *msg = NULL;

	pr_debug("%s: channel:%d head:%p tail:%p num:%d\n",
		 __func__, chn, head, tail, num);

	/* FIXME: Should we use replace msg? */
	msg = sprd_alloc_msg(&rx_mgmt->rx_data_list);
	if (!msg) {
		pr_err("%s: no more msg\n", __func__);
		sprdwcn_bus_push_list(chn, head, tail, num);
		return 0;
	}

	sprd_fill_msg(msg, NULL, (void *)head, num);
	msg->fifo_id = chn;
	msg->buffer_type = SPRD_DEFRAG_MEM;
	msg->data = (void *)tail;

	sprd_queue_msg(msg, &rx_mgmt->rx_data_list);
	napi_schedule(&rx_mgmt->napi);

	return 0;
}
#endif

static int pcie_rx_cmd_push(int chn, struct mbuf_t **head, struct mbuf_t **tail,
			    int *num)
{
	return pcie_rx_common_push(chn, head, tail, num, SPRD_MAX_CMD_RXLEN);
}

static int pcie_rx_data_push(int chn, struct mbuf_t **head,
			     struct mbuf_t **tail, int *num)
{
	return pcie_rx_common_push(chn, head, tail, num, SPRD_MAX_DATA_RXLEN);
}

/*
 * mode:
 * 0 - suspend
 * 1 - resume
 */
static int pcie_suspend_resume_handle(int chn, int mode)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct sprd_priv *priv = hif->priv;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	int ret;
	struct sprd_vif *vif = NULL, *tmp_vif;
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	struct timespec64 time;
#else
	struct timespec time;
#endif

	spin_lock_bh(&priv->list_lock);
	list_for_each_entry(tmp_vif, &priv->vif_list, vif_node) {
		if (tmp_vif->state & VIF_STATE_OPEN) {
			vif = tmp_vif;
			break;
		}
	}
	spin_unlock_bh(&priv->list_lock);

	if (!vif || hif->cp_asserted) {
		pr_err("%s, %d, error! NULL vif or assert\n", __func__,
		       __LINE__);
		sprd_put_vif(vif);
		return -EBUSY;
	}

	if (mode == 0) {
		if (atomic_read(&tx_mgmt->tx_list_qos_pool.ref) > 0 ||
		    atomic_read(&tx_mgmt->tx_list_cmd.ref) > 0 ||
		    !list_empty(&tx_mgmt->xmit_msg_list.to_send_list) ||
		    !list_empty(&tx_mgmt->xmit_msg_list.to_free_list)) {
			pr_info("%s, %d,Q not empty suspend not allowed\n",
				__func__, __LINE__);
			sprd_put_vif(vif);
			return -EBUSY;
		}
		hif->suspend_mode = SPRD_PS_SUSPENDING;
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		ktime_get_real_ts64(&time);
		hif->sleep_time = timespec64_to_ns(&time);
#else
		getnstimeofday(&time);
		hif->sleep_time = timespec_to_ns(&time);
#endif
		priv->is_suspending = 1;
		ret = sprd_power_save(priv, vif, SPRD_SUSPEND_RESUME, 0);
		if (ret == 0)
			hif->suspend_mode = SPRD_PS_SUSPENDED;
		else
			hif->suspend_mode = SPRD_PS_RESUMED;
		sprd_put_vif(vif);
		return ret;
	} else if (mode == 1) {
		hif->suspend_mode = SPRD_PS_RESUMING;
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		ktime_get_real_ts64(&time);
		hif->sleep_time = timespec64_to_ns(&time) - hif->sleep_time;
#else
		getnstimeofday(&time);
		hif->sleep_time = timespec_to_ns(&time) - hif->sleep_time;
#endif
		ret = sprd_power_save(priv, vif, SPRD_SUSPEND_RESUME, 1);
		pr_info("%s, %d,resume ret=%d, resume after %lu ms\n",
			__func__, __LINE__, ret, hif->sleep_time / 1000000);
		sprd_put_vif(vif);
		return ret;
	}
	sprd_put_vif(vif);
	return -EBUSY;
}

/*
 * MU300 (kanoqwq's clean-tf-7.2 149c9d2a): the EDMA channel's pending ring (four slots, three usable) refuses a
 * submission with -EAGAIN when it is full and takes nothing (wcn_bsp edma_pending_q_buffer). That is backpressure,
 * not an error: the TX thread keeps the frames, does not count it towards its flush thresholds, and sleeps until the
 * bus completes a transfer on the channel and calls this (in IRQ context) - armed before the submission, since the
 * completion can come before the push returns. Before, the refusal was -1, counted like a hard failure (more than
 * 100 in a row flushed the queued frames), and nothing in particular woke the thread for the retry.
 */
static int sc2355_pcie_tx_data_complete(int chn, int timeout)
{
	struct sprd_hif *hif;
	struct tx_mgmt *tx_mgmt;

	/* MU300: RCU keeps hif->rx_mgmt alive across sc2355_rx_bus_complete (sc2355_rx_deinit waits for it) */
	rcu_read_lock();
	hif = sc2355_pcie_get_hif();
	tx_mgmt = hif ? smp_load_acquire((struct tx_mgmt **)&hif->tx_mgmt) : NULL;
	if (!timeout && tx_mgmt && atomic_xchg(&tx_mgmt->pcie_tx_waiting, 0))
		sc2355_tx_up(tx_mgmt);
	/* MU300 (kanoqwq's cf4dcd61): the RX address list travels on this channel too; a slot is free again */
	if (!timeout && hif && chn == hif->tx_data_port)
		sc2355_rx_bus_complete(hif);
	rcu_read_unlock();
	return 0;
}

static int sc2355_pcie_tx_data_push(struct sprd_hif *hif, struct mbuf_t *head, struct mbuf_t *tail, int num)
{
	struct tx_mgmt *tx_mgmt = hif->tx_mgmt;
	int ret;

	atomic_set(&tx_mgmt->pcie_tx_waiting, 1);
	ret = sc2355_pcie_push_link(hif, hif->tx_data_port, head, tail, num, sc2355_pcie_tx_data_pop_list);
	if (ret != -EAGAIN)
		atomic_set(&tx_mgmt->pcie_tx_waiting, 0);
	return ret;
}

struct mchn_ops_t sc2355_pcie_hif_ops[] = {
	/* ADD HIF ops config here */
	/* NOTE: Requested by SDIO team, pool_size MUST be 1 in RX */
	/* RX channels 1 3 5 7 9 11 13 15 */
#ifndef SC2355_RX_NO_LOOP
	INIT_INTF_SC2355(PCIE_RX_CMD_PORT, 1, 0, 0, SPRD_MAX_CMD_RXLEN,
			10, 0, 0, 0, 1, 32, pcie_rx_handle,
			pcie_rx_cmd_push, NULL, NULL),
	INIT_INTF_SC2355(PCIE_RX_DATA_PORT, 1, 0, 0, SPRD_MAX_CMD_RXLEN,
			100, 0, 0, 0, 1, 32, pcie_rx_handle,
			pcie_rx_data_push, NULL, NULL),
#ifdef PCIE_DEBUG
	INIT_INTF_SC2355(PCIE_RX_ADDR_DATA_PORT, 1, 0, 0, SPRDWL_MAX_CMD_RXLEN,
			100, 0, 0, 0, 1, 32, sprdwl_sc2355_rx_handle_for_debug,
			sprdwl_rx_data_push, NULL, NULL),
#else
	INIT_INTF_SC2355(PCIE_RX_ADDR_DATA_PORT, 1, 0, 0, SPRD_MAX_CMD_RXLEN,
			100, 0, 0, 0, 1, 32, pcie_rx_handle,
			pcie_rx_data_push, NULL, NULL),
#endif
#else
	INIT_INTF_SC2355(PCIE_RX_CMD_PORT, 1, 0, 0, SPRDWL_MAX_CMD_RXLEN,
			10, 0, 0, 0, 1, 32, sprdwl_sc2355_rx_handle_no_loop,
			sprdwl_rx_cmd_push, NULL, NULL),
	INIT_INTF_SC2355(PCIE_RX_DATA_PORT, 1, 0, 0, SPRDWL_MAX_CMD_RXLEN,
			100, 0, 0, 0, 1, 32, sprdwl_sc2355_rx_handle_no_loop,
			sprdwl_rx_data_push, NULL, NULL),
	INIT_INTF_SC2355(PCIE_RX_ADDR_DATA_PORT, 1, 0, 0, SPRDWL_MAX_CMD_RXLEN,
			100, 0, 0, 0, 1, 32, sprdwl_sc2355_rx_handle_no_loop,
			sprdwl_rx_data_push, NULL, NULL),
#endif
	/* TX channels 0 2 4 6 8 10 12 14 */
	INIT_INTF_SC2355(PCIE_TX_CMD_PORT, 1, 1, 0, SPRD_MAX_CMD_TXLEN,
			10, 0, 0, 0, 1, 32, sc2355_pcie_tx_cmd_pop_list,
			NULL, NULL, pcie_suspend_resume_handle),
	INIT_INTF_SC2355(PCIE_TX_DATA_PORT, 1, 1, 0, SPRD_MAX_CMD_TXLEN,
			300, 0, 0, 0, 1, 32, sc2355_pcie_tx_data_pop_list,
			NULL, sc2355_pcie_tx_data_complete, NULL),
	INIT_INTF_SC2355(PCIE_TX_ADDR_DATA_PORT, 1, 1, 0, SPRD_MAX_CMD_TXLEN,
			300, 0, 0, 0, 1, 4, sc2355_pcie_tx_data_pop_list,
			NULL, sc2355_pcie_tx_data_complete, NULL),
};

static void pcie_tx_ba_mgmt(struct sprd_priv *priv, struct sprd_vif *vif,
			    void *data, int len, unsigned char cmd_id)
{
	struct sprd_msg *msg;
	unsigned char *data_ptr;
	u8 *rbuf;
	u16 rlen = (1 + sizeof(struct host_addba_param));

	msg = get_cmdbuf(priv, vif, len, cmd_id);
	if (!msg) {
		pr_err("%s, %d, get msg err\n", __func__, __LINE__);
		return;
	}
	rbuf = kzalloc(rlen, GFP_KERNEL);
	if (!rbuf) {
		pr_err("%s, %d, alloc rbuf err\n", __func__, __LINE__);
		return;
	}
	memcpy(msg->data, data, len);
	data_ptr = (unsigned char *)data;

	if (sprd_get_debug_level() >= L_INFO)
		sc2355_hex_dump("pcie_tx_ba_mgmt", data_ptr, len);

	if (send_cmd_recv_rsp(priv, msg, rbuf, &rlen))
		goto out;
	/*if tx ba req failed, need to clear txba map*/
	if (cmd_id == CMD_ADDBA_REQ && rbuf[0] != ADDBA_REQ_RESULT_SUCCESS) {
		struct host_addba_param *addba;
		struct sprd_peer_entry *peer_entry = NULL;
		struct sprd_hif *hif = sc2355_pcie_get_hif();
		u16 tid = 0;

		addba = (struct host_addba_param *)(rbuf + 1);
		peer_entry = &hif->peer_entry[addba->lut_index];
		tid = addba->addba_param.tid;
		if (!test_and_clear_bit(tid, &peer_entry->ba_tx_done_map))
			goto out;
		pr_err
		    ("%s, %d, tx_addba failed, reason=%d, lut_index=%d, tid=%d, map=%lu\n",
		     __func__, __LINE__, rbuf[0], addba->lut_index, tid,
		     peer_entry->ba_tx_done_map);
	}
out:
	kfree(rbuf);
}

struct sprd_hif *sc2355_pcie_get_hif(void)
{
	return (struct sprd_hif *)smp_load_acquire(&sc2355_hif.hif);
}

#define INTF_IS_PCIE \
	(sc2355_pcie_get_hif()->hw_type == SPRD_HW_SC2355_PCIE)

void sc2355_pcie_set_coex_bt_on_off(u8 action)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();

	hif->coex_bt_on = action;
}

inline int sc2355_pcie_tx_cmd(struct sprd_hif *hif, unsigned char *data, int len)
{
	return pcie_tx_one(hif, data, len, hif->tx_cmd_port);
}

inline int sc2355_tx_addr_trans_pcie(struct sprd_hif *hif,
				     unsigned char *data, int len,
				     bool send_now)
{
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	struct mbuf_t *head = NULL, *tail = NULL, *mbuf = NULL;
	int num = 1, ret = 0;

	if (data) {
		ret = sprdwcn_bus_list_alloc(hif->tx_data_port,
					     &head, &tail, &num);
		if (ret || !head || !tail) {
			/*
			 * MU300: rate-limited. The RX refill retries this, and in issue #94 (the WCN in dump status for
			 * minutes) it and its siblings flooded the console until a CPU soft-locked in printk.
			 */
			pr_err_ratelimited("%s:%d sprdwcn_bus_list_alloc fail, chn: %d\n",
					   __func__, __LINE__, hif->tx_data_port);
		} else {
			mbuf = head;
			mbuf->buf = data;
			mbuf->len = len;
			mbuf->next = NULL;
			mbuf->phy =
			    sc2355_mm_virt_to_phys(&hif->pdev->dev, mbuf->buf,
						   mbuf->len, DMA_TO_DEVICE);

			if (rx_mgmt->addr_trans_head) {
				((struct mbuf_t *)
				 rx_mgmt->addr_trans_tail)->next = head;
				rx_mgmt->addr_trans_tail = (void *)tail;
				rx_mgmt->addr_trans_num += num;
			} else {
				rx_mgmt->addr_trans_head = (void *)head;
				if (!head)
					pr_err
					    ("ERROR! %s, %d, addr_trans_head set to NULL\n",
					     __func__, __LINE__);
				rx_mgmt->addr_trans_tail = (void *)tail;
				rx_mgmt->addr_trans_num = num;
			}
		}
	}

	if (!ret && send_now && rx_mgmt->addr_trans_head) {
		ret = sc2355_pcie_push_link(hif, hif->tx_data_port,
				       (struct mbuf_t *)rx_mgmt->addr_trans_head,
				       (struct mbuf_t *)rx_mgmt->addr_trans_tail,
				       rx_mgmt->addr_trans_num,
				       sc2355_pcie_tx_cmd_pop_list);
		if (!ret) {
			rx_mgmt->addr_trans_head = NULL;
			rx_mgmt->addr_trans_tail = NULL;
			rx_mgmt->addr_trans_num = 0;
		}
	}
	pr_debug("%s, trans rx buf, %d, cp2 buffer: %d\n", __func__, ret,
		skb_queue_len(&rx_mgmt->mm_entry.buffer_list));
	return ret;
}

inline void sc2355_pcie_tx_addr_trans_free(struct sprd_hif *hif)
{
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;

	sc2355_pcie_tx_cmd_pop_list(hif->tx_data_port,
			       (struct mbuf_t *)rx_mgmt->addr_trans_head,
			       (struct mbuf_t *)rx_mgmt->addr_trans_tail,
			       rx_mgmt->addr_trans_num);

	rx_mgmt->addr_trans_head = NULL;
	rx_mgmt->addr_trans_tail = NULL;
	rx_mgmt->addr_trans_num = 0;
}
static inline void sprdwl_mbuf_list_free(struct sprd_hif *hif,
					 struct mbuf_t *head,
					 struct mbuf_t *tail,
					 int count)
{
	int i;
	struct mbuf_t *mbuf_pos = head;

	for (i = 0; i < count && mbuf_pos; i++) {
		if (mbuf_pos->buf) {
			sc2355_mm_phys_to_virt(&hif->pdev->dev,
					mbuf_pos->phy, mbuf_pos->len,
					DMA_TO_DEVICE, false);
			kfree(mbuf_pos->buf);
			mbuf_pos->phy = 0;
			mbuf_pos->buf = 0;
			mbuf_pos->len = 0;
		}
		mbuf_pos = mbuf_pos->next;
	}
	sprdwcn_bus_list_free(hif->tx_data_port, head, tail, count);
}

void sc2355_pcie_add_to_free_list(struct sprd_priv *priv,
			     struct list_head *tx_list_head, int tx_count)
{
	struct sprd_hif *hif = &priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	spin_lock_bh(&tx_mgmt->xmit_msg_list.free_lock);
	list_splice_tail(tx_list_head, &tx_mgmt->xmit_msg_list.to_free_list);
	spin_unlock_bh(&tx_mgmt->xmit_msg_list.free_lock);
}

int sc2355_pcie_hif_tx_list(struct sprd_hif *hif,
		       struct list_head *tx_list,
		       struct list_head *tx_list_head,
		       int tx_count, int ac_index, u8 coex_bt_on)
{
	int ret = 0, i = 0, j = PCIE_TX_NUM;
	int pcie_count = 0, cnt = 0, num = 0, k = 0;
	struct sprd_msg *msg_pos;
	struct pcie_addr_buffer *addr_buffer = NULL;
	struct tx_mgmt *tx_mgmt;
	struct mbuf_t *head = NULL, *tail = NULL, *mbuf_pos;
	struct list_head *pos, *tx_list_tail, *tx_head = NULL;
	struct tx_msdu_dscr *dscr;
	int print_len;
#if defined(MORE_DEBUG)
	unsigned long tx_bytes = 0;
#endif

	pr_debug("%s:%d tx_count is %d\n", __func__, __LINE__, tx_count);

	tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		if (tx_count <= PCIE_TX_NUM) {
			pcie_count = 1;
		} else {
			cnt = tx_count;
			while (cnt > PCIE_TX_NUM) {
				++num;
				cnt -= PCIE_TX_NUM;
			}
			pcie_count = num + 1;
		}
		ret = sprdwcn_bus_list_alloc(hif->tx_data_port, &head, &tail,
				&pcie_count); //port is 6
	} else {
		ret = sprdwcn_bus_list_alloc(hif->tx_data_port, &head, &tail,
					&tx_count); //port is 9
	}
	if (ret != 0 || head == NULL || tail == NULL || pcie_count == 0) {
		pr_err("%s:%d mbuf link alloc fail\n", __func__, __LINE__);
		return -1;
	}

	mbuf_pos = head;
	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		for (i = 0; i < pcie_count && mbuf_pos; i++) {
			/* To prevent the mbuf_pos->buf not NULL case */
			mbuf_pos->buf = NULL;
			mbuf_pos = mbuf_pos->next;
		}
		mbuf_pos = head;
		if (pcie_count > 1) {
			addr_buffer =
			pcie_set_addr_to_mbuf(tx_mgmt,
				mbuf_pos, PCIE_TX_NUM);
		} else {
			addr_buffer =
			pcie_set_addr_to_mbuf(tx_mgmt,
				mbuf_pos, tx_count);
		}
		if (addr_buffer == NULL) {
			pr_err("%s:%d alloc pcie addr buf fail\n",
			       __func__, __LINE__);
			sprdwl_mbuf_list_free(hif, head, tail, pcie_count);
			return -1;
		}

	}

	i = 0;
	list_for_each(pos, tx_list) {
		msg_pos = list_entry(pos, struct sprd_msg, list);
		sc2355_tcp_ack_move_msg(hif->priv, msg_pos);
		if (tx_head == NULL)
			tx_head = pos;

/*TODO*/
		if (msg_pos->len > 200)
			print_len = 200;
		else
			print_len = msg_pos->len;
		if (sprd_get_debug_level() >= L_DBG)
			sc2355_hex_dump("tx to cp2 data",
					(unsigned char *)(msg_pos->tran_data),
					print_len);
#if defined(MORE_DEBUG)
		tx_bytes += msg_pos->skb->len;
#endif
		if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
			if (pcie_count > 1 && num > 0 && i >= j) {
				if (--num == 0) {
					if (cnt > 0) {
						mbuf_pos->phy =
							sc2355_mm_virt_to_phys(&hif->pdev->dev,
									       mbuf_pos->buf,
									       mbuf_pos->len,
									       DMA_TO_DEVICE);
						if (sprd_get_debug_level() >= L_DBG)
							sc2355_hex_dump("tx to addr trans",
							(unsigned char *)(mbuf_pos->buf),
								mbuf_pos->len);
						mbuf_pos = mbuf_pos->next;
						addr_buffer =
						pcie_set_addr_to_mbuf(
						tx_mgmt, mbuf_pos, cnt);
					} else {
						pr_err("%s: cnt %d\n", __func__, cnt);
					}
				} else {
					/*if data num greater than PCIE_TX_NUM,
					 *alloc another pcie addr buf
					 */
					j += PCIE_TX_NUM;
					mbuf_pos->phy =
						sc2355_mm_virt_to_phys(&hif->pdev->dev,
								       mbuf_pos->buf,
								       mbuf_pos->len,
								       DMA_TO_DEVICE);
					if (sprd_get_debug_level() >= L_DBG)
						sc2355_hex_dump("tx to addr trans",
								(unsigned char *)(mbuf_pos->buf),
								mbuf_pos->len);
					mbuf_pos = mbuf_pos->next;
					addr_buffer =
						pcie_set_addr_to_mbuf(
						tx_mgmt, mbuf_pos, PCIE_TX_NUM);
				}
				if (addr_buffer == NULL) {
					pr_err("%s:%d alloc pcie addr buf fail\n",
					       __func__, __LINE__);
					sprdwl_mbuf_list_free(hif, head, tail, pcie_count);
					return -1;
				}

				k = 0;
			}
			if (msg_pos->skb)
				pcie_skb_to_tx_buf(hif, msg_pos);
			pr_debug("debug pcie addr: 0x%lx\n", msg_pos->pcie_addr);
			memcpy(&addr_buffer->pcie_addr[k],
			       &msg_pos->pcie_addr, SPRD_PHYS_LEN);
			dscr = (struct tx_msdu_dscr *)(msg_pos->tran_data + MSDU_DSCR_RSVD);
			addr_buffer->common.interface = dscr->common.interface;

			k++;
		} else {
			mbuf_pos->buf = msg_pos->tran_data - hif->hif_offset;
			mbuf_pos->len = msg_pos->len;
			mbuf_pos = mbuf_pos->next;
		}
		if (++i == tx_count)
			break;
	}

	mbuf_pos->phy =
		sc2355_mm_virt_to_phys(&hif->pdev->dev, mbuf_pos->buf,
				mbuf_pos->len, DMA_TO_DEVICE);
	if (sprd_get_debug_level() >= L_DBG)
		sc2355_hex_dump("tx to addr trans",
			(unsigned char *)(mbuf_pos->buf), mbuf_pos->len);

	tx_list_tail = pos;

	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		sprdwl_list_cut_to_free_list(tx_list_head,
			     tx_list, tx_list_tail,
			     tx_count);
		hif->mbuf_head = (void *)head;
		hif->mbuf_tail = (void *)tail;
		hif->mbuf_num = pcie_count;

		if (hif->mbuf_head) {
			/*ret = mchn_push_link(9, head, tail, pcie_count);*/
			/*edma sync function*/
			ret = sc2355_pcie_tx_data_push(hif, (struct mbuf_t *)hif->mbuf_head,
						       (struct mbuf_t *)hif->mbuf_tail, hif->mbuf_num);
			if (ret != 0) {
				/*pr_err("%s: push link fail\n", __func__); */
				if (ret != -EAGAIN)	/* MU300: a full pending ring is not a failure */
					hif->pushfail_count++;
				sprdwl_list_cut_to_send_list(tx_head,
								 tx_list_tail,
								 tx_count);
				if (tx_list_tail)
					sprdwl_mbuf_list_free(hif, head, tail, pcie_count);
			} else {
#if defined(MORE_DEBUG)
				UPDATE_TX_PACKETS(hif, tx_count, tx_bytes);
#endif
				INIT_LIST_HEAD(tx_list_head);
				hif->mbuf_head = NULL;
				hif->mbuf_tail = NULL;
				hif->mbuf_num = 0;
				hif->pushfail_count = 0;
				tx_mgmt->tx_num += tx_count;
			}

		}
		return ret;
	}

	return ret;
}

struct sprd_peer_entry
*sc2355_pcie_find_peer_entry_using_addr(struct sprd_vif *vif, u8 *addr)
{
	struct sprd_hif *hif;
	struct sprd_peer_entry *peer_entry = NULL;
	u8 i;

	hif = &vif->priv->hif;
	for (i = 0; i < MAX_LUT_NUM; i++) {
		if (ether_addr_equal(hif->peer_entry[i].tx.da, addr)) {
			peer_entry = &hif->peer_entry[i];
			break;
		}
	}
	if (!peer_entry)
		pr_err("not find peer_entry at :%s\n", __func__);

	return peer_entry;
}

/* It is tx private function, just use in sc2355_hif_fill_msdu_dscr()  */
unsigned char sc2355_pcie_find_lut_index(struct sprd_hif *hif, struct sprd_vif *vif)
{
	unsigned char i;

	if (is_zero_ether_addr(hif->skb_da))/*TODO*/
		goto out;

	pr_debug("%s,bssid: %02x:%02x:%02x:%02x:%02x:%02x\n", __func__,
		 hif->skb_da[0], hif->skb_da[1], hif->skb_da[2],
		 hif->skb_da[3], hif->skb_da[4], hif->skb_da[5]);
	if (sc2355_is_group(hif->skb_da) &&
	    (vif->mode == SPRD_MODE_AP || vif->mode == SPRD_MODE_P2P_GO)) {
		for (i = 0; i < MAX_LUT_NUM; i++) {
			if ((sc2355_is_group(hif->peer_entry[i].tx.da)) &&
			    hif->peer_entry[i].ctx_id == vif->ctx_id) {
				pr_debug_ratelimited("%s, %d, group lut_index=%d\n",
					__func__, __LINE__,
					hif->peer_entry[i].lut_index);
				return hif->peer_entry[i].lut_index;
			}
		}
		if (vif->mode == SPRD_MODE_AP) {
			pr_debug("%s,AP mode, group bssid,\n"
				"lut not found, ctx_id:%d, return lut:4\n",
				__func__, vif->ctx_id);
			return 4;
		}
		if (vif->mode == SPRD_MODE_P2P_GO) {
			pr_debug("%s,GO mode, group bssid,\n"
				"lut not found, ctx_id:%d, return lut:5\n",
				__func__, vif->ctx_id);
			return 5;
		}
	}

	for (i = 0; i < MAX_LUT_NUM; i++) {
		if ((memcmp(hif->peer_entry[i].tx.da,
			    hif->skb_da, ETH_ALEN) == 0) &&
		    hif->peer_entry[i].ctx_id == vif->ctx_id) {
			pr_debug("%s, %d, lut_index=%d\n",
				 __func__, __LINE__,
				 hif->peer_entry[i].lut_index);
			return hif->peer_entry[i].lut_index;
		}
	}

	for (i = 0; i < MAX_LUT_NUM; i++) {
		if ((vif->mode == SPRD_MODE_STATION ||
		     vif->mode == SPRD_MODE_P2P_CLIENT) &&
		    hif->peer_entry[i].ctx_id == vif->ctx_id) {
			pr_debug("%s, %d, lut_index=%d\n",
				 __func__, __LINE__,
				 hif->peer_entry[i].lut_index);
			return hif->peer_entry[i].lut_index;
		}
	}

out:
	/* MU300 (#140): a multicast/broadcast frame has no peer entry by design; the per-mode default is the answer, not an error */
	if (vif->mode == SPRD_MODE_STATION ||
	    vif->mode == SPRD_MODE_P2P_CLIENT) {
		pr_debug("%s,%d,bssid not found, multicast?\n"
			 "default of STA/GC = 0,\n", __func__, vif->ctx_id);
		return 0;
	}
	if (vif->mode == SPRD_MODE_AP) {
		pr_debug("%s,%d,bssid not found, multicast?\n"
			 "default of AP = 4\n", __func__, vif->ctx_id);
		return 4;
	}
	if (vif->mode == SPRD_MODE_P2P_GO) {
		pr_debug("%s,%d,bssid not found, multicast?\n"
			 "default of GO = 5\n", __func__, vif->ctx_id);
		return 5;
	}
	return 0;
}

int sc2355_pcie_hif_fill_msdu_dscr(struct sprd_vif *vif,
			      struct sk_buff *skb, u8 type, u8 offset)
{
	u8 protocol;
	struct tx_msdu_dscr *dscr;
	struct sprd_hif *hif;
	u8 lut_index;
	unsigned char dscr_rsvd = 0;
	struct ethhdr *ethhdr = (struct ethhdr *)skb->data;
	u8 is_special_data = 0;
	unsigned long dma_addr = 0;
	bool is_vowifi2cmd = false;

#define MSG_PTR_LEN 8

	if (ethhdr->h_proto == htons(ETH_P_ARP) ||
	    ethhdr->h_proto == htons(ETH_P_TDLS) ||
	    ethhdr->h_proto == htons(ETH_P_PREAUTH))
		is_special_data = 1;
	else if ((type == SPRD_TYPE_CMD) &&
		 sc2355_is_vowifi_pkt(skb, &is_vowifi2cmd))
		is_special_data = 1;

	hif = &vif->priv->hif;
	dscr_rsvd = INTF_IS_PCIE ? MSDU_DSCR_RSVD : 0;
	/*
	 * MU300 (kanoqwq's clean-tf-7.2 149c9d2a): the headroom is made in place. The vendor replaced the skb with
	 * skb_realloc_headroom() and freed the old one, but only this function's copy of the pointer changed: every
	 * caller went on with the freed skb (and freed it again when this failed). skb_cow_head() keeps the skb the
	 * caller holds, also makes a header shared with a clone (a bridge flooding a frame) private before the
	 * descriptor is written into it, and counts the offset the push below adds; on failure the caller frees.
	 */
	if (skb_headroom(skb) < (DSCR_LEN + offset + hif->hif_offset + MSG_PTR_LEN + dscr_rsvd) ||
	    skb_header_cloned(skb)) {
		if (skb_cow_head(skb, DSCR_LEN + offset + hif->hif_offset + MSG_PTR_LEN + dscr_rsvd)) {
			pr_err_ratelimited("%s: no headroom for the descriptor\n", __func__);
			return -ENOMEM;
		}
#if defined(MORE_DEBUG)
		hif->stats.tx_realloc++;
#endif
	}
	ethhdr = (struct ethhdr *)skb->data;	/* skb_cow_head may have moved the data */

	memcpy(hif->skb_da, skb->data, ETH_ALEN);

	lut_index = sc2355_pcie_find_lut_index(hif, vif);
	if (lut_index < 6 && (!sc2355_is_group(hif->skb_da))) {
		pr_debug_ratelimited("%s, %d, sta disconn, no data tx!\n", __func__, __LINE__);
		return -EPERM;
	}
	//skb_push(skb, sizeof(struct tx_msdu_dscr) + offset + dscr_rsvd);
	skb_push(skb, sizeof(struct tx_msdu_dscr) + offset);
	dscr = (struct tx_msdu_dscr *)(skb->data);
	memset(dscr, 0x00, sizeof(struct tx_msdu_dscr));
	dscr->common.type = (type == SPRD_TYPE_CMD ?
			     SPRD_TYPE_CMD : SPRD_TYPE_DATA);
/*remove unnecessary repeated assignment*/
	//dscr->common.direction_ind = 0;
	//dscr->common.need_rsp = 0;/*TODO*/
	dscr->common.interface = vif->ctx_id;
	//dscr->pkt_len = cpu_to_le16(skb->len - DSCR_LEN - dscr_rsvd);
	dscr->pkt_len = cpu_to_le16(skb->len - DSCR_LEN);
	dscr->offset = DSCR_LEN;
/*TODO*/
	dscr->tx_ctrl.sw_rate = (is_special_data == 1 ? 1 : 0);
	//dscr->tx_ctrl.wds = 0; /*TBD*/
	//dscr->tx_ctrl.swq_flag = 0; /*TBD*/
	//dscr->tx_ctrl.rsvd = 0; /*TBD*/
	//dscr->tx_ctrl.next_buffer_type = 0;
	//dscr->tx_ctrl.pcie_mh_readcomp = 0;
	//dscr->buffer_info.msdu_tid = 0;
	//dscr->buffer_info.mac_data_offset = 0;
	dscr->sta_lut_index = lut_index;

	/* For MH to get phys addr */
	if (INTF_IS_PCIE) {
		skb_push(skb, dscr_rsvd);
		dma_addr = virt_to_phys(skb->data) | SPRD_MH_ADDRESS_BIT;
		memcpy(skb->data, &dma_addr, dscr_rsvd);
	}

	if (skb->ip_summed == CHECKSUM_PARTIAL) {
		dscr->tx_ctrl.checksum_offload = 1;
		if (ethhdr->h_proto == htons(ETH_P_IPV6))
			protocol = ipv6_hdr(skb)->nexthdr;
		else
			protocol = ip_hdr(skb)->protocol;

		dscr->tx_ctrl.checksum_type = protocol == IPPROTO_TCP ? 1 : 0;
		dscr->tcp_udp_header_offset =
		    skb->transport_header - skb->mac_header;
		pr_debug("%s: offload: offset: %d, protocol: %d\n",
			 __func__, dscr->tcp_udp_header_offset, protocol);
	}

	return 0;
}
#if 0
inline void *sc2355_get_rx_data(struct sprd_hif *hif,
				void *pos, void **data,
				void **tran_data, int *len, int offset)
{
	struct mbuf_t *mbuf = (struct mbuf_t *)pos;

	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		sc2355_mm_phys_to_virt(&hif->pdev->dev, mbuf->phy, mbuf->len,
				       DMA_FROM_DEVICE, false);
		mbuf->phy = 0;
	}

	*tran_data = mbuf->buf;
	*data = (*tran_data) + offset;
	*len = mbuf->len;
	mbuf->buf = NULL;

	return (void *)mbuf->next;
}

inline void sc2355_free_rx_data(struct sprd_hif *hif,
				int chn, void *head, void *tail, int num)
{
	int len = 0, ret = 0;

	/* We should refill mbuf in pcie mode */
	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		if (hif->rx_cmd_port == chn)
			len = SPRD_MAX_CMD_RXLEN;
		else
			len = SPRD_MAX_DATA_RXLEN;

		ret = pcie_rx_fill_mbuf(head, tail, num, len);
		if (ret) {
			pr_err("%s: alloc buf fail\n", __func__);
			sprdwcn_bus_list_free(chn, (struct mbuf_t *)head,
					      (struct mbuf_t *)tail, num);
			head = NULL;
			tail = NULL;
			num = 0;
		}
	}

	if (!ret)
		sprdwcn_bus_push_list(chn, (struct mbuf_t *)head,
				      (struct mbuf_t *)tail, num);
}
#endif

void sc2355_pcie_handle_pop_list(void *data)
{
	int i;
	struct sprd_msg *msg_pos;
	struct mbuf_t *mbuf_pos = NULL;
	struct pop_work *pop = (struct pop_work *)data;
	struct tx_mgmt *tx_mgmt;
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct list_head tmp_list;
	struct sprd_msg *msg_head, *msg_tail;

	tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	mbuf_pos = (struct mbuf_t *)pop->head;
	msg_pos = GET_MSG_BUF(mbuf_pos);

	msg_head = GET_MSG_BUF((struct mbuf_t *)pop->head);
	msg_tail = GET_MSG_BUF((struct mbuf_t *)pop->tail);

	spin_lock_bh(&tx_mgmt->xmit_msg_list.free_lock);
	list_cut_position(&tmp_list, msg_head->list.prev, &msg_tail->list);
	spin_unlock_bh(&tx_mgmt->xmit_msg_list.free_lock);

	for (i = 0; i < pop->num; i++) {
		msg_pos = GET_MSG_BUF(mbuf_pos);
		dev_kfree_skb(msg_pos->skb);
		mbuf_pos = mbuf_pos->next;
	}

	spin_lock_bh(&tx_mgmt->tx_list_qos_pool.freelock);
	list_splice_tail(&tmp_list, &msg_pos->msglist->freelist);
	spin_unlock_bh(&tx_mgmt->tx_list_qos_pool.freelock);
	sprdwcn_bus_list_free(pop->chn, pop->head, pop->tail, pop->num);
	pcie_mbufpop += pop->num;
}

int sc2355_pcie_add_topop_list(int chn, struct mbuf_t *head,
			  struct mbuf_t *tail, int num)
{
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct sprd_work *misc_work;
	struct pop_work pop_work;

	pop_work.chn = chn;
	pop_work.head = (void *)head;
	pop_work.tail = (void *)tail;
	pop_work.num = num;

	misc_work = sprd_alloc_work(sizeof(struct pop_work));
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return -1;
	}
	misc_work->vif = NULL;
	misc_work->id = SPRD_POP_MBUF;
	misc_work->hw_type = SPRD_HW_SC2355_PCIE;
	memcpy(misc_work->data, &pop_work, sizeof(struct pop_work));

	sprd_queue_work(hif->priv, misc_work);
	return 0;
}

/*call back func for HIF pop_link*/
int sc2355_pcie_tx_data_pop_list(int channel, struct mbuf_t *head,
			    struct mbuf_t *tail, int num)
{
	struct mbuf_t *mbuf_pos = NULL;
#if defined(MORE_DEBUG)
	struct sprd_msg *msg_head;
#endif
	struct sprd_hif *hif = sc2355_pcie_get_hif();

	pr_debug("%s channel: %d, head: %p, tail: %p num: %d\n",
		__func__, channel, head, tail, num);

	/* same window as in sc2355_pcie_tx_cmd_pop_list() below */
	if (!hif || !smp_load_acquire(&hif->tx_mgmt)) {
		pr_warn("%s: no tx context yet, freeing %d buffer(s)\n", __func__, num);
		sprdwcn_bus_list_free(channel, head, tail, num);
		return 0;
	}

	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		int tmp_num = num;

		/* FIXME: Temp solution, addr node pos hard to sync dma */
		for (mbuf_pos = head; mbuf_pos != NULL;
		     mbuf_pos = mbuf_pos->next) {
			sc2355_mm_phys_to_virt(&hif->pdev->dev, mbuf_pos->phy,
					       mbuf_pos->len, DMA_TO_DEVICE,
					       false);
			mbuf_pos->phy = 0;
			kfree(mbuf_pos->buf);
			mbuf_pos->buf = NULL;
			if (--tmp_num == 0)
				break;
		}
		sprdwcn_bus_list_free(channel, head, tail, num);
		pr_debug("%s:%d free : %d msg buf\n", __func__, __LINE__, num);
		return 0;
	}
#if defined(MORE_DEBUG)
	msg_head = GET_MSG_BUF(head);
	/*show packet average sent time, unit: ns*/
	pcie_get_tx_avg_time(hif, msg_head->tx_start_time);
#endif

	sc2355_dequeue_data_list(head, num);
	pr_debug("%s:%d free : %d msg buf\n", __func__, __LINE__, num);
	sprdwcn_bus_list_free(channel, head, tail, num);

	return 0;
}

static inline int sprd_tx_free_txc_msg(struct tx_mgmt *tx_msg,
					 struct sprd_msg *msg_buf)
{
	struct sprd_msg *pos_msg = NULL;
	unsigned long lockflag_txc = 0;
	int found = 0;

	spin_lock_irqsave(&tx_msg->xmit_msg_list.free_lock, lockflag_txc);
	list_for_each_entry(pos_msg, &tx_msg->xmit_msg_list.to_free_list,
			    list) {
		if (pos_msg == msg_buf) {
			found = 1;
			break;
		}
	}

	if (found != 1) {
		pr_err("%s: msg_buf %lx not in to free list\n",
			__func__, msg_buf);
		spin_unlock_irqrestore(&tx_msg->xmit_msg_list.free_lock,
				       lockflag_txc);
		return -1;
	}
	list_del(&msg_buf->list);
	spin_unlock_irqrestore(&tx_msg->xmit_msg_list.free_lock, lockflag_txc);
	if (msg_buf->node)
		pcie_free_tx_buf(msg_buf->node);
	if (msg_buf->skb)
		dev_kfree_skb(msg_buf->skb);

	msg_buf->node = NULL;
	msg_buf->skb = NULL;
	msg_buf->data = NULL;
	msg_buf->tran_data = NULL;
	msg_buf->len = 0;

	sprd_free_msg(msg_buf, msg_buf->msglist);
	return 0;
}

/*free PCIe data when receive txc event from cp*/
int sc2355_tx_free_pcie_data(unsigned char *data)
{
	int i;
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)smp_load_acquire(&hif->tx_mgmt);
	void *data_addr_ptr;
	unsigned long pcie_addr;
	unsigned short data_num;
	struct txc_addr_buff *txc_addr;
	unsigned char (*pos)[5];
	struct sprd_msg *msg, *last_msg = NULL;
#if defined(MORE_DEBUG)
	unsigned long tx_start_time = 0;
#endif
	unsigned char *tmp;
	static unsigned long caller_jiffies;
	struct sprd_priv *priv = hif->priv;

	pr_debug("%s:=0x%x %p %p\n", __func__, data, tx_mgmt, hif);

	/* a TX-complete event outside the tx context's lifetime (see sc2355_pcie_tx_cmd_pop_list) */
	if (!tx_mgmt)
		return 0;

	if (tx_mgmt->net_stopped == 1) {
		sprd_net_flowcontrl(priv, SPRD_MODE_NONE, true);
		tx_mgmt->net_stopped = 0;
	}

	txc_addr = (struct txc_addr_buff *)data;
	data_num = txc_addr->number;
	tx_mgmt->txc_num += data_num;
	tmp = (unsigned char *)txc_addr;
	pr_debug("%s: seq_num=0x%x", __func__, *(tmp + 1));
	pr_debug("%s, total free num:%lu; total tx_num:%lu\n", __func__,
		 tx_mgmt->txc_num, tx_mgmt->tx_num);


	if (printk_timed_ratelimit(&caller_jiffies, 1000)) {
		pr_debug("%s, free_num: %d, to_free_list num: %d\n",
			__func__, data_num,
			atomic_read(&tx_mgmt->xmit_msg_list.free_num));
	}

	pos = (unsigned char (*)[5])(txc_addr + 1);
	for (i = 0; i < data_num; i++, pos++) {
		memcpy(&pcie_addr, pos, SPRD_PHYS_LEN);
		pcie_addr -= 0x10;	//Workaround for HW issue

		pr_debug("%s: pcie_addr=0x%lx", __func__, pcie_addr);
		data_addr_ptr =
		    sc2355_mm_phys_to_virt(&hif->pdev->dev, pcie_addr,
					   SPRD_MAX_DATA_TXLEN, DMA_TO_DEVICE,
					   false);
		msg = NULL;
		RESTORE_ADDR(msg, data_addr_ptr, sizeof(msg));

		if (last_msg == msg) {
			pr_info("%s: same msg buf: %lx, %lx\n", __func__,
				(unsigned long)msg, (unsigned long)last_msg);
		}
		pr_debug("data_addr_ptr: 0x%lx, msg: 0x:%lx\n",
					data_addr_ptr, msg);
#if defined(MORE_DEBUG)
		if (i == 0)
			tx_start_time = msg->tx_start_time;
#endif
		if (!sprd_tx_free_txc_msg(tx_mgmt, msg))
			last_msg = msg;
	}

#if defined(MORE_DEBUG)
	pcie_get_tx_avg_time(hif, tx_start_time);
#endif

	return 0;
}

int sc2355_pcie_tx_cmd_pop_list(int channel, struct mbuf_t *head,
			   struct mbuf_t *tail, int num)
{
	int count = 0;
	struct mbuf_t *pos = NULL;
	struct sprd_hif *hif = sc2355_pcie_get_hif();
	struct tx_mgmt *tx_mgmt;
	struct sprd_msg *pos_buf, *temp_buf;

	pr_debug("%s channel: %d, head: %p, tail: %p num: %d\n",
		 __func__, channel, head, tail, num);

	/* The chip completes commands through an MSI, and that can arrive before tx_init() has allocated
	 * tx_mgmt or after tx_deinit() has set it back to NULL - a window the bus callbacks are registered
	 * across. Dereferencing it there panics the kernel from interrupt context (seen twice at boot, in
	 * sc2355_pcie_tx_cmd_pop_list+0x2c, "paging request at 00000000000030b0"). The buffers still have to
	 * go back to the bus, so hand them over and leave.
	 */
	/* one acquire load: tx_init() publishes tx_mgmt with a release store once it is complete, and
	 * tx_deinit() clears it and waits (synchronize_rcu) for handlers like this one before freeing it
	 */
	tx_mgmt = hif ? (struct tx_mgmt *)smp_load_acquire(&hif->tx_mgmt) : NULL;
	if (!tx_mgmt) {
		pr_warn("%s: no tx context yet, freeing %d buffer(s)\n", __func__, num);
		sprdwcn_bus_list_free(channel, head, tail, num);
		return 0;
	}

	pr_debug("%s len: %d buf: %s\n", __func__, head->len, head->buf + 4);

	pos = head;

	list_for_each_entry_safe(pos_buf, temp_buf,
				 &tx_mgmt->tx_list_cmd.cmd_to_free, list) {
		if (pos_buf->tran_data == pos->buf) {
			pr_debug("move CMD node from to_free to free list\n");
			/*list msg from to_free list  to free list*/
			sc2355_free_cmd_buf(pos_buf, &tx_mgmt->tx_list_cmd);

			if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
				sc2355_mm_phys_to_virt(&hif->pdev->dev,
						       pos->phy, pos->len,
						       DMA_TO_DEVICE, false);
				pos->phy = 0;
			}
			/*free it*/
			kfree(pos->buf);
			pos->buf = NULL;
			pos = pos->next;
			count++;
		}
		if (count == num)
			break;
	}

	tx_mgmt->cmd_poped += num;
	pr_debug("tx_cmd_pop num: %d,cmd_poped=%d, cmd_send=%d\n",
		num, tx_mgmt->cmd_poped, tx_mgmt->cmd_send);
	sprdwcn_bus_list_free(channel, head, tail, num);

	return 0;
}

int sc2355_pcie_push_link(struct sprd_hif *hif, int chn,
		     struct mbuf_t *head, struct mbuf_t *tail, int num,
		     int (*pop)(int, struct mbuf_t *, struct mbuf_t *, int))
{
	int ret = 0;
	unsigned long time = 0;
	struct mbuf_t *pos = head;
	int i = 0;
	unsigned int low = 0xffffffff;
	unsigned int low1 = 0;

	for (i = 0; i < num; i++) {
		if ((memcmp(&pos->phy, &low, 4) == 0) ||
		    (memcmp(&pos->phy, &low1, 4) == 0)) {
			pr_err
			    ("err phy address: %lx\n, err virt address: %lx\n, err port: %d\n",
			     pos->phy, pos->buf, chn);
			return -ENOMEM;
		}
		if (i == num && pos != tail)
			pr_info("num of head to tail is not match\n");

		pos = pos->next;
	}
	time = jiffies;
	ret = sprdwcn_bus_push_list(chn, head, tail, num);
	time = jiffies - time;

	if (ret && ret != -EAGAIN) {
		/* MU300: rate-limited, the RX refill and every TX retry it. -EAGAIN is the EDMA pending ring's
		 * backpressure, which every caller retries (#339): wcn_bsp says that one, once a minute. */
		pr_err_ratelimited("%s: push link fail: %d, chn: %d!\n", __func__, ret,
				   chn);
	}
	return ret;
}

struct sprd_peer_entry
*sc2355_pcie_find_peer_entry_using_lut_index(struct sprd_hif *hif,
					unsigned char sta_lut_index)
{
	int i = 0;
	struct sprd_peer_entry *peer_entry = NULL;

	for (i = 0; i < MAX_LUT_NUM; i++) {
		if (sta_lut_index == hif->peer_entry[i].lut_index) {
			peer_entry = &hif->peer_entry[i];
			break;
		}
	}

	return peer_entry;
}

/* update lut-inidex if event_sta_lut received
 * at CP side, lut_index range 0-31
 * but 0-3 were used to send non-assoc frame(only used by CP)
 * so for Ap-CP interface, there is only 4-31
 */
void sc2355_pcie_event_sta_lut(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_hif *hif;
	struct evt_sta_lut_ind *sta_lut = NULL;
	u8 i;

	if (len < sizeof(*sta_lut)) {
		pr_err("%s, len:%d too short!\n", __func__, len);
		return;
	}
	hif = &vif->priv->hif;
	sta_lut = (struct evt_sta_lut_ind *)data;
	if (hif != sc2355_pcie_get_hif()) {
		pr_err("%s, wrong hif!\n", __func__);
		return;
	}
	if (!sta_lut) {
		pr_err("%s, NULL input data!\n", __func__);
		return;
	}

	i = sta_lut->sta_lut_index;

	pr_info("ctx_id:%d,action:%d,lut:%d\n", sta_lut->ctx_id,
		sta_lut->action, sta_lut->sta_lut_index);
	switch (sta_lut->action) {
	case DEL_LUT_INDEX:
		if (hif->peer_entry[i].ba_tx_done_map != 0) {
			hif->peer_entry[i].ht_enable = 0;
			hif->peer_entry[i].ip_acquired = 0;
			hif->peer_entry[i].ba_tx_done_map = 0;
			/*sc2355_tx_delba(hif, hif->peer_entry + i);*/
		}
		sc2355_peer_entry_delba(hif, i);
		memset(&hif->peer_entry[i], 0x00,
		       sizeof(struct sprd_peer_entry));
		hif->peer_entry[i].ctx_id = 0xFF;
		hif->tx_num[i] = 0;
		sc2355_pcie_dis_flush_txlist(hif, i);
		break;
	case UPD_LUT_INDEX:
		sc2355_peer_entry_delba(hif, i);
		sc2355_pcie_dis_flush_txlist(hif, i);
	case ADD_LUT_INDEX:
		hif->peer_entry[i].lut_index = i;
		hif->peer_entry[i].ctx_id = sta_lut->ctx_id;
		hif->peer_entry[i].ht_enable = sta_lut->is_ht_enable;
		hif->peer_entry[i].vht_enable = sta_lut->is_vht_enable;
		hif->peer_entry[i].ba_tx_done_map = 0;
		hif->tx_num[i] = 0;

		pr_info("ctx_id%d,action%d,lut%d,%x:%x:%x:%x:%x:%x\n",
			sta_lut->ctx_id, sta_lut->action,
			sta_lut->sta_lut_index,
			sta_lut->ra[0], sta_lut->ra[1], sta_lut->ra[2],
			sta_lut->ra[3], sta_lut->ra[4], sta_lut->ra[5]);
		ether_addr_copy(hif->peer_entry[i].tx.da, sta_lut->ra);
		break;
	default:
		break;
	}
}

void sc2355_pcie_tx_send_addba(struct sprd_vif *vif, void *data, int len)
{
	pcie_tx_ba_mgmt(vif->priv, vif, data, len, CMD_ADDBA_REQ);
}

void sc2355_pcie_tx_send_delba(struct sprd_vif *vif, void *data, int len)
{
	struct host_delba_param *delba;

	delba = (struct host_delba_param *)data;
	pcie_tx_ba_mgmt(vif->priv, vif, delba,
			sizeof(struct host_delba_param), CMD_DELBA_REQ);
}

void sc2355_pcie_tx_addba(struct sprd_hif *hif,
		     struct sprd_peer_entry *peer_entry, unsigned char tid)
{
#define WIN_SIZE 64
	struct host_addba_param addba;
	struct sprd_work *misc_work;
	struct sprd_vif *vif;

	vif = sc2355_ctxid_to_vif(hif->priv, peer_entry->ctx_id);
	if (!vif)
		return;
	memset(&addba, 0x0, sizeof(struct host_addba_param));

	addba.lut_index = peer_entry->lut_index;
	ether_addr_copy(addba.perr_mac_addr, peer_entry->tx.da);
	pr_info("%s, lut=%d, tid=%d\n", __func__, peer_entry->lut_index, tid);
	addba.dialog_token = 1;
	addba.addba_param.amsdu_permit = 0;
	addba.addba_param.ba_policy = DOT11_ADDBA_POLICY_IMMEDIATE;
	addba.addba_param.tid = tid;
	addba.addba_param.buffer_size = WIN_SIZE;
	misc_work = sprd_alloc_work(sizeof(struct host_addba_param));
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		sprd_put_vif(vif);
		return;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_ADDBA;
	misc_work->hw_type = SPRD_HW_SC2355_PCIE;
	memcpy(misc_work->data, &addba, sizeof(struct host_addba_param));

	sprd_queue_work(vif->priv, misc_work);
	sprd_put_vif(vif);
}

void sc2355_pcie_tx_delba(struct sprd_hif *hif,
		     struct sprd_peer_entry *peer_entry, unsigned int ac_index)
{
	struct host_delba_param delba;
	struct sprd_work *misc_work;
	struct sprd_vif *vif;

	vif = sc2355_ctxid_to_vif(hif->priv, peer_entry->ctx_id);
	if (!vif)
		return;
	memset(&delba, 0x0, sizeof(delba));

	pr_info("enter--at %s\n", __func__);
	ether_addr_copy(delba.perr_mac_addr, peer_entry->tx.da);
	delba.lut_index = peer_entry->lut_index;
	delba.delba_param.initiator = 1;
	delba.delba_param.tid = qos_index_2_tid(ac_index);
	delba.reason_code = 0;

	misc_work = sprd_alloc_work(sizeof(struct host_delba_param));
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		sprd_put_vif(vif);
		return;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_DELBA;
	misc_work->hw_type = SPRD_HW_SC2355_PCIE;
	memcpy(misc_work->data, &delba, sizeof(struct host_delba_param));
	clear_bit(qos_index_2_tid(ac_index), &peer_entry->ba_tx_done_map);

	sprd_queue_work(vif->priv, misc_work);
	sprd_put_vif(vif);
}

int sc2355_pcie_dis_flush_txlist(struct sprd_hif *hif, u8 lut_index)
{
	struct tx_mgmt *tx_mgmt;
	int i, j;

	if (lut_index <= 5) {
		pr_err("err lut_index:%d, %s, %d\n",
		       lut_index, __func__, __LINE__);
		return -1;
	}
	pr_err("disconnect, flush qoslist, %s, %d\n", __func__, __LINE__);
	tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	for (i = 0; i < SPRD_MODE_MAX; i++)
		for (j = 0; j < SPRD_AC_MAX; j++)
			sc2355_flush_tx_qoslist(tx_mgmt, i, j, lut_index);
	return 0;
}

unsigned short sc2355_pcie_get_data_csum(void *entry, void *data)
{
	unsigned short csum = 0;
	struct rx_mh_desc *mh_desc = (struct rx_mh_desc *)data;
	struct sprd_hif *hif = (struct sprd_hif *)entry;

	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		if (mh_desc->tcp_checksum_en)
			csum = mh_desc->tcp_hw_checksum;
	}

	return csum;
}

void pcie_mm_fill_all_buffer(struct sprd_hif *hif)
{
	struct rx_mgmt *rx_mgmt =
	    (struct rx_mgmt *)((struct sprd_hif *)hif)->rx_mgmt;
	struct mem_mgmt *mm_entry = &rx_mgmt->mm_entry;
	int num = SPRD_MAX_MH_BUF - skb_queue_len(&mm_entry->buffer_list);
	u8 mode_opened = 0;
	struct sprd_priv *priv = hif->priv;
	struct sprd_vif *tmp_vif;

	/*TODO make sure driver send buf only once*/
	spin_lock_bh(&priv->list_lock);
	list_for_each_entry(tmp_vif, &priv->vif_list, vif_node) {
		if (tmp_vif->state & VIF_STATE_OPEN)
			mode_opened++;
	}
	spin_unlock_bh(&priv->list_lock);

	if (mode_opened > 1) {
		pr_info("%s, mm buffer already filled\n", __func__);
		return;
	}

	if (num >= 0) {
		atomic_add(num, &mm_entry->alloc_num);
		sc2355_mm_fill_buffer(hif);
	}
}

void sc2355_pcie_handle_tx_return(struct sprd_hif *hif,
				  struct sprd_msg_list *list,
				  int send_num, int ret)
{
	if (ret == -2) {
		atomic_sub(send_num, &list->ref);
		pr_debug("%s,%d,debug: %d\n", __func__, __LINE__, atomic_read(&list->ref));
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		usleep_range_state(100, 200, TASK_UNINTERRUPTIBLE);
#else
		usleep_range(100, 200);
#endif
		return;
	} else if (ret < 0) {
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		usleep_range_state(100, 200, TASK_UNINTERRUPTIBLE);
#else
		usleep_range(100, 200);
#endif
		return;
	} else {
		pr_debug("%s,%d,debug: %d\n", __func__, __LINE__, atomic_read(&list->ref));
	}
}

void sc2355_pcie_rx_work_queue(struct work_struct *work)
{
	struct sprd_msg *msg;
	struct sprd_priv *priv;
	struct rx_mgmt *rx_mgmt;
	struct sprd_hif *hif;

	rx_mgmt = container_of(work, struct rx_mgmt, rx_work);
	hif = rx_mgmt->hif;
	priv = hif->priv;

	if (!hif->exit && !sprd_peek_msg(&rx_mgmt->rx_list))
		sc2355_rx_process(rx_mgmt, NULL);

	while ((msg = sprd_peek_msg(&rx_mgmt->rx_list))) {
		if (hif->exit)
			goto next;
		pr_debug("%s: rx type:%d\n",  __func__, SPRD_HEAD_GET_TYPE(msg->data));

		/* RX control messages can include firmware key material. */

		switch (SPRD_HEAD_GET_TYPE(msg->data)) {
		case SPRD_TYPE_DATA:
#if defined FPGA_LOOPBACK_TEST
			if (hif->loopback_n < 500) {
				unsigned char *r_buf;
				r_buf = (unsigned char *)msg->data;
				sprdwl_intf_tx_data_fpga_test(hif, r_buf, msg->len);
			}
#else
			if (msg->len > SPRD_MAX_DATA_RXLEN)
				pr_err("err rx data too long:%d > %d\n", msg->len,
				SPRD_MAX_DATA_RXLEN);
			rx_data_process(priv, msg->data);
#endif
			break;
		case SPRD_TYPE_CMD:
			if (msg->len > SPRD_MAX_CMD_RXLEN)
				pr_err("err rx cmd too long:%d > %d\n",
					msg->len, SPRD_MAX_CMD_RXLEN);
			sc2355_rx_rsp_process(priv, msg->data);
			break;
		case SPRD_TYPE_EVENT:
			if (msg->len > SPRD_MAX_CMD_RXLEN)
				pr_err("err rx event too long:%d > %d\n", msg->len,
					SPRD_MAX_CMD_RXLEN);
			sc2355_rx_evt_process(priv, msg->data);
			break;
		case SPRD_TYPE_DATA_SPECIAL:
			if (msg->len > SPRD_MAX_DATA_RXLEN)
				pr_err("err data trans too long:%d > %d\n", msg->len,
					SPRD_MAX_CMD_RXLEN);

			sc2355_mm_mh_data_process(&rx_mgmt->mm_entry, msg->tran_data,
							msg->len, msg->buffer_type);
			msg->tran_data = NULL;
			msg->data = NULL;
			break;
		case SPRD_TYPE_DATA_PCIE_ADDR:
			if (msg->len > SPRD_MAX_CMD_RXLEN)
				pr_err("err rx mh data too long:%d > %d\n", msg->len,
					SPRD_MAX_DATA_RXLEN);

			sc2355_rx_mh_addr_process(rx_mgmt, msg->tran_data, msg->len,
						msg->buffer_type);
			msg->tran_data = NULL;
			msg->data = NULL;
			break;
		default:
			pr_err("rx unknown type:%d\n", SPRD_HEAD_GET_TYPE(msg->data));
			break;
		}
next:
		if (msg->tran_data)
			sc2355_free_data(msg->tran_data, msg->buffer_type);
		sprd_dequeue_msg(msg, &rx_mgmt->rx_list);
	}
}

int sc2355_pcie_fc_get_send_num(struct sprd_hif *hif,
			   enum sprd_mode mode, int data_num)
{
	int free_num = 0;
	struct tx_mgmt *tx_mgmt = hif->tx_mgmt;
	/*send all data in buff with PCIe interface*/
	unsigned int tx_buf_max = get_max_fw_tx_dscr() >
				  pcie_get_tx_buf_num() ?
				  pcie_get_tx_buf_num() :
				  get_max_fw_tx_dscr();

	if (data_num <= 0 || mode == SPRD_MODE_NONE)
		return 0;

	free_num = atomic_read(&tx_mgmt->xmit_msg_list.free_num);

	if ((free_num + data_num) >= tx_buf_max) {
		pr_debug("%s, free_num=%d, data_num=%d\n", __func__,
				   free_num, data_num);
		return (tx_buf_max - free_num);
	} else {
		return data_num;
	}

}

int sc2355_pcie_fc_test_send_num(struct sprd_hif *hif,
			    enum sprd_mode mode, int data_num)
{
	int free_num = 0;
	struct tx_mgmt *tx_mgmt = hif->tx_mgmt;
	/*send all data in buff with PCIe interface, TODO*/
	unsigned int tx_buf_max = get_max_fw_tx_dscr() >
				  pcie_get_tx_buf_num() ?
				  pcie_get_tx_buf_num() :
				  get_max_fw_tx_dscr();

	if (data_num <= 0 || mode == SPRD_MODE_NONE)
		return 0;

	free_num = atomic_read(&tx_mgmt->xmit_msg_list.free_num);

	if ((free_num + data_num) >= tx_buf_max) {
		/*
		 * MU300 (from kanoqwq/mu300-linux): a full TX ring is ordinary flow control - the caller sends what fits
		 * and the rest waits for the next completion - not a firmware error. The vendor printed it with pr_err on
		 * every scheduling pass, i.e. continuously under load; pr_debug as in sc2355_pcie_fc_get_send_num().
		 */
		pr_debug_ratelimited("%s,%d free_num=%d, data_num=%d\n",
				     __func__, __LINE__, free_num, data_num);
		return (tx_buf_max - free_num);
	} else {
		return data_num;
	}

}

int pcie_init(struct sprd_hif *hif)
{
	u8 i;
	int ret = -EINVAL;

	hif->hw_type = SPRD_HW_SC2355_PCIE;
	dma_coerce_mask_and_coherent(&hif->pdev->dev, DMA_BIT_MASK(39));

	for (i = 0; i < MAX_LUT_NUM; i++)
		hif->peer_entry[i].ctx_id = 0xff;


	hif->hif_offset = 0;
	hif->rx_cmd_port = PCIE_RX_CMD_PORT;
	hif->rx_data_port = PCIE_RX_ADDR_DATA_PORT;
	hif->tx_cmd_port = PCIE_TX_CMD_PORT;
	hif->tx_data_port = PCIE_TX_ADDR_DATA_PORT;

	if (pcie_buf_init()) {
		ret = -ENOMEM;
		pr_err("%s txrx buf init failed.\n", __func__);
		return ret;
	}
	adjust_max_fw_tx_dscr("max_fw_tx_dscr=1024", strlen("max_fw_tx_dscr="));

	ret = sc2355_rx_init(hif);
	if (ret) {
		pr_err("%s rx init failed: %d\n", __func__, ret);
		goto err_rx_init;
	}

	ret = sc2355_tx_init(hif);
	if (ret) {
		pr_err("%s tx_list init failed\n", __func__);
		goto err_tx_init;
	}

	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		sc2355_hif.mchn_ops = sc2355_pcie_hif_ops;
		sc2355_hif.max_num =
		    sizeof(sc2355_pcie_hif_ops) / sizeof(struct mchn_ops_t);
	}
	if (hif->hw_type == SPRD_HW_SC2355_PCIE)
		hif->feature = NETIF_F_SG;
	else
		hif->feature = NETIF_F_CSUM_MASK | NETIF_F_SG;

	return 0;

err_tx_init:
	sc2355_rx_deinit(hif);
err_rx_init:
	pcie_buf_deinit();

	return ret;
}

int pcie_post_init(struct sprd_hif *hif)
{
	int ret = -EINVAL, chn = 0;

	/* MU300: published before any channel can interrupt (the RX/TX callbacks read it in hard IRQ) */
	smp_store_release(&sc2355_hif.hif, (void *)hif);
	/*
	 * MU300 (from kanoqwq/mu300-linux): a failed/aborted earlier power cycle clears this pointer in the
	 * error path below.  The vendor driver only restores it in pcie_init(),
	 * but a later WCN power-on may call post_init directly.  Passing
	 * &NULL[0] to mchn_init then makes Wi-Fi permanently fail until reboot.
	 * The channel table is static, so make every post-init self-contained.
	 */
	sc2355_hif.mchn_ops = sc2355_pcie_hif_ops;
	sc2355_hif.max_num =
		sizeof(sc2355_pcie_hif_ops) / sizeof(struct mchn_ops_t);

	if (sc2355_hif.max_num < MAX_CHN_NUM) {
		pr_info("%s: register %d ops\n", __func__, sc2355_hif.max_num);

		for (chn = 0; chn < sc2355_hif.max_num; chn++) {
			ret = sprdwcn_bus_chn_init(&sc2355_hif.mchn_ops[chn]);
			if (ret < 0)
				goto err;
		}

		hif->fw_awake = 1;
		hif->fw_power_down = 0;
	}

	return 0;

err:
	pr_err("%s: unregister %d ops\n", __func__, sc2355_hif.max_num);

	/* MU300: undo the channels that were set up and the one that failed, chn .. 0 (the vendor loop stopped at
	 * 1; a failed channel can be registered with half its EDMA state, and deinit skips one never registered) */
	for (; chn >= 0; chn--)
		sprdwcn_bus_chn_deinit(&sc2355_hif.mchn_ops[chn]);
	sc2355_hif.mchn_ops = NULL;
	sc2355_hif.max_num = 0;

	return ret;
}

void pcie_post_deinit(struct sprd_hif *hif)
{
	int chn = 0;

	for (chn = 0; chn < sc2355_hif.max_num; chn++)
		sprdwcn_bus_chn_deinit(&sc2355_hif.mchn_ops[chn]);
	/* MU300: cleared only after every channel is unregistered (the callbacks check for NULL) */
	WRITE_ONCE(sc2355_hif.hif, NULL);
	sc2355_hif.max_num = 0;

}
void pcie_deinit(struct sprd_hif *hif)
{
	sc2355_tx_deinit(hif);
	sc2355_rx_deinit(hif);
	pcie_buf_deinit();
}

static struct sprd_hif_ops sc2355_pcie_ops = {
	.init = pcie_init,
	.deinit = pcie_deinit,
	.post_init = pcie_post_init,
	.post_deinit = pcie_post_deinit,
	.sync_version = sc2355_sync_version,
	.download_hw_param = sc2355_download_hw_param,
	.reset = sc2355_reset,
	.fill_all_buffer = pcie_mm_fill_all_buffer,
	.tx_special_data = sprd_tx_special_data,
	.free_msg_content = pcie_free_msg_content,
	.tx_addr_trans = sc2355_tx_addr_trans_pcie,
	.tx_flush = sc2355_tx_flush,
};

extern struct sprd_chip_ops sc2355_chip_ops;
int pcie_probe(struct platform_device *pdev)
{
	return sprd_iface_probe(pdev, &sc2355_pcie_ops, &sc2355_chip_ops);
}
#if 0
static void pcie_remove(struct platform_device *pdev)
{
	return;
}

static const struct of_device_id sc2355_pcie_of_match[] = {
	{.compatible = "sprd,sc2355-pcie-wifi",},
	{}
};

MODULE_DEVICE_TABLE(of, sc2355_pcie_of_match);

static struct platform_driver sc2355_pcie_driver = {
	.probe = pcie_probe,
	.remove = pcie_remove,
	.driver = {
		   .owner = THIS_MODULE,
		   .name = "wlan",
		   .of_match_table = sc2355_pcie_of_match,
	}
};

module_platform_driver(sc2355_pcie_driver);

MODULE_DESCRIPTION("Spreadtrum SC2355 PCIE Initialization");
MODULE_AUTHOR("Spreadtrum WCN Division");
MODULE_LICENSE("GPL");
#endif
