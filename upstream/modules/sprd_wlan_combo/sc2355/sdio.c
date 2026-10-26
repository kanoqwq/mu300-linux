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
#include <linux/of_device.h>
#include <linux/regmap.h>
#include <misc/marlin_platform.h>
#include <linux/pm_qos.h>

#include "cmdevt.h"
#include "common/chip_ops.h"
#include "common/common.h"
#include "common/iface.h"
#include "qos.h"
#include "rx.h"
#include "sdio.h"
#include "tx.h"
#include "txrx.h"

#define SPRD_NORMAL_MEM	0
#define SPRD_DEFRAG_MEM	1

#define INIT_INTF_SC2355(num, type, out, interval, bsize, psize, max,\
			 threshold, time, pop, push, complete, suspend) \
{ .channel = num, .hif_type = type, .inout = out, .intr_interval = interval,\
.buf_size = bsize, .pool_size = psize, .once_max_trans = max,\
.rx_threshold = threshold, .timeout = time, .pop_link = pop,\
.push_link = push, .tx_complete = complete, .power_notify = suspend }

struct sc2355_hif {
	unsigned int max_num;
	void *hif;
	struct mchn_ops_t *mchn_ops;
};

static struct sc2355_hif sc2355_hif;

#if defined(MORE_DEBUG)
static void sdio_dump_stats(struct sprd_hif *hif)
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

/*calculate packets  average sent time from received
 *from network stack to freed by HIF every STATS_COUNT packets
 */
static void sdio_get_tx_avg_time(struct sprd_hif *hif,
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
		sdio_dump_stats(hif);
		hif->stats.gap_num = 0;
		hif->stats.tx_cost_time = 0;
		pr_info("%s:%d packets avg cost time: %lu\n",
			__func__, __LINE__, hif->stats.tx_avg_time);
	}
}
#endif

unsigned long mbufalloc;
unsigned long mbufpop;

static int sdio_tx_one(struct sprd_hif *hif, unsigned char *data,
		       int len, int chn)
{
	int ret;
	struct mbuf_t *head = NULL, *tail = NULL, *mbuf = NULL;
	int num = 1;

	ret = sprdwcn_bus_list_alloc(chn, &head, &tail, &num);
	ret = 0;
	if (ret || !head || !tail) {
		pr_err("%s:%d sprdwcn_bus_list_alloc fail\n",
		       __func__, __LINE__);
		return -1;
	}

	mbufalloc += num;
	mbuf = head;
	mbuf->buf = data;
	mbuf->len = len;
	mbuf->next = NULL;
	/* Command payloads may contain passphrases, PSKs or PMKs. */

	ret = sprdwcn_bus_push_list(chn, head, tail, num);

	if (ret) {
		mbuf = head;
		kfree(mbuf->buf);
		mbuf->buf = NULL;

		sprdwcn_bus_list_free(chn, head, tail, num);
		mbufalloc -= num;
	}

	return ret;
}

static void sdio_add_tx_list_head(struct list_head *tx_fail_list,
				  struct list_head *tx_list,
				  int ac_index, int tx_count)
{
	struct sprd_msg *msg = NULL;
	struct list_head *head, *tail;
	/* protect plist or send list*/
	spinlock_t *lock;
	/* protect free list */
	spinlock_t *free_lock;

	if (!tx_fail_list)
		return;
	msg = list_first_entry(tx_fail_list, struct sprd_msg, list);
	free_lock = &msg->xmit_msg_list->free_lock;
	if (msg->msg_type != SPRD_TYPE_DATA) {
		lock = &msg->msglist->busylock;
	} else {
		if (ac_index != SPRD_AC_MAX)
			lock = &msg->data_list->p_lock;
		else
			lock = &msg->xmit_msg_list->send_lock;
	}
	spin_lock_bh(free_lock);
	head = tx_fail_list->next;
	tail = tx_fail_list->prev;
	head->prev->next = tail->next;
	tail->next->prev = head->prev;
	head->prev = tx_fail_list;
	tail->next = tx_fail_list;
	spin_unlock_bh(free_lock);

	spin_lock_bh(lock);
	list_splice(tx_fail_list, tx_list);
	spin_unlock_bh(lock);
	INIT_LIST_HEAD(tx_fail_list);
}

/*cut data list from tx data list*/
static inline void
sdio_list_cut_position(struct list_head *tx_list_head,
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

static int sdio_bus_list_alloc(struct sprd_hif *hif,
			       int tx_count,
			       struct mbuf_t **head,
			       struct mbuf_t **tail,
			       int *pcie_count, int *cnt, int *num)
{
	return sprdwcn_bus_list_alloc(hif->tx_data_port, head, tail, &tx_count);
}

static int sdio_rx_handle(int chn, struct mbuf_t *head,
			  struct mbuf_t *tail, int num)
{
	struct sprd_hif *hif = sc2355_get_hif();
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	struct sprd_msg *msg = NULL;

	pr_debug("%s: channel:%d head:%p tail:%p num:%d\n",
		 __func__, chn, head, tail, num);

	/*To process credit earlier*/
	if (hif->hw_type == SPRD_HW_SC2355_SDIO ||
	    hif->hw_type == SPRD_HW_SC2355_USB) {
		unsigned int i = 0;
		struct mbuf_t *mbuf = NULL;

		mbuf = head;
		for (i = num; i > 0; i--) {
			sc2355_sdio_process_credit(hif,
						   (void *)(mbuf->buf + hif->hif_offset));
			mbuf = mbuf->next;
		}

		msg = sprd_alloc_msg(&rx_mgmt->rx_list);
		if (!msg) {
			pr_err("%s: no more msg\n", __func__);
			sprdwcn_bus_push_list(chn, head, tail, num);
			return 0;
		}

		sprd_fill_msg(msg, NULL, (void *)head, num);
		msg->fifo_id = chn;
		msg->buffer_type = SPRD_DEFRAG_MEM;
		msg->data = (void *)tail;

		rx_mgmt->rx_chn = chn;
		rx_mgmt->rx_handle_ns = ktime_get_boot_fast_ns();

		sprd_queue_msg(msg, &rx_mgmt->rx_list);
	}
	queue_work(rx_mgmt->rx_queue, &rx_mgmt->rx_work);

	return 0;
}

/* mode:
 * 0 - suspend
 * 1 - resume
 */
struct throughput_sta throughput_static;
static int sdio_suspend_resume_handle(int chn, int mode)
{
	struct sprd_hif *hif = sc2355_get_hif();
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

	/* there is no need to suspend or resume since vif is NULL */
	if (!vif)
		return 0;

	if (hif->cp_asserted) {
		pr_err("%s, %d, error! cp2 has asserted!\n", __func__,
		       __LINE__);
		return 0;
	}

	if (throughput_static.disable_pd_flag) {
		throughput_static.disable_pd_flag = false;
		//allow core powerdown
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		cpu_latency_qos_update_request(&throughput_static.pm_qos_request_idle,
					      PM_QOS_CPU_LATENCY_DEFAULT_VALUE);
#else
		pm_qos_update_request(&throughput_static.pm_qos_request_idle,
					      PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE);
#endif
	}

	if (throughput_static.uclamp_set_flag) {
		//reset thread uclamp param
		sc2355_set_thread_uclamp(tx_mgmt->tx_thread, 0);
		throughput_static.uclamp_set_flag = false;
	}

	if (mode == 0) {
		if (atomic_read(&tx_mgmt->tx_list_qos_pool.ref) > 0 ||
		    atomic_read(&tx_mgmt->tx_list_cmd.ref) > 0 ||
		    !list_empty(&tx_mgmt->xmit_msg_list.to_send_list) ||
		    !list_empty(&tx_mgmt->xmit_msg_list.to_free_list)) {
			pr_info("%s, %d,Q not empty suspend not allowed\n",
				__func__, __LINE__);
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
		return ret;
	}
	return -EBUSY;
}

/*  SDIO TX:
 *  Type 3:WIFI
 *  Subtype 0  --> port 8
 *  Subtype 1  --> port 9
 *  Subtype 2  --> port 10(fifolen=8)
 *  Subtype 3  --> port 11(fifolen=8)
 *  SDIO RX:
 *  Type 3:WIFI
 *  Subtype 0  --> port 10
 *  Subtype 1  --> port 11
 *  Subtype 2  --> port 12(fifolen=8)
 *  Subtype 3  --> port 13(fifolen=8)
 */
struct mchn_ops_t sdio_hif_ops[] = {
	/* RX INTF */
	/* NOTE: Requested by SDIO team, pool_size MUST be 1 in RX */
	INIT_INTF_SC2355(SDIO_RX_CMD_PORT, 0, 0, 0,
			 SPRD_MAX_CMD_RXLEN, 1, 0, 0, 0,
			 sdio_rx_handle, NULL, NULL, NULL),
	INIT_INTF_SC2355(SDIO_RX_PKT_LOG_PORT, 0, 0, 0,
			 SPRD_MAX_DATA_RXLEN, 1, 0, 0, 0,
			 sdio_rx_handle, NULL, NULL, NULL),
	INIT_INTF_SC2355(SDIO_RX_DATA_PORT, 0, 0, 0,
			 SPRD_MAX_DATA_RXLEN, 1, 0, 0, 0,
			 sdio_rx_handle, NULL, NULL, NULL),

	/* TX INTF */
	INIT_INTF_SC2355(SDIO_TX_CMD_PORT, 0, 1, 0,
			 SPRD_MAX_CMD_TXLEN, 10, 0, 0, 0,
			 sc2355_tx_cmd_pop_list, NULL, NULL,
			 sdio_suspend_resume_handle),
	INIT_INTF_SC2355(SDIO_TX_DATA_PORT, 0, 1, 0,
			 SPRD_MAX_DATA_TXLEN, 600, 0, 0, 0,
			 sc2355_tx_data_pop_list, NULL, NULL, NULL),
};

static void sdio_tx_ba_mgmt(struct sprd_priv *priv, struct sprd_vif *vif,
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
	if (!rbuf)
		return;

	memcpy(msg->data, data, len);
	data_ptr = (unsigned char *)data;

	if (sprd_get_debug_level() >= L_INFO)
		sc2355_hex_dump("sdio_tx_ba_mgmt", data_ptr, len);

	if (send_cmd_recv_rsp(priv, msg, rbuf, &rlen))
		goto out;
	/*if tx ba req failed, need to clear txba map*/
	if (cmd_id == CMD_ADDBA_REQ && rbuf[0] != ADDBA_REQ_RESULT_SUCCESS) {
		struct host_addba_param *addba;
		struct sprd_peer_entry *peer_entry = NULL;
		struct sprd_hif *hif = sc2355_get_hif();
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
static int fc_find_color_per_mode(struct tx_mgmt *tx_mgmt,
				  enum sprd_mode mode, u8 *index)
{
	u8 i = 0, found = 0;
	enum sprd_mode tmp_mode;
	struct sprd_priv *priv = tx_mgmt->hif->priv;
	struct sprd_vif *vif;

	for (i = 0; i < MAX_COLOR_BIT; i++) {
		if (tx_mgmt->flow_ctrl[i].mode == mode) {
			found = 1;
			pr_debug("%s, %d, mode:%d found, index:%d\n",
				 __func__, __LINE__, mode, i);
			break;
		}
	}
	if (found == 0) {
		/*a new mode. sould assign new color to this mode*/
		for (i = 0; i < MAX_COLOR_BIT; i++) {
			tmp_mode = tx_mgmt->flow_ctrl[i].mode;
			vif = sprd_mode_to_vif(priv, tmp_mode);
			if (vif && !(vif->state & VIF_STATE_OPEN))
				tx_mgmt->flow_ctrl[i].mode = SPRD_MODE_NONE;
		}
		for (i = 0; i < MAX_COLOR_BIT; i++) {
			if (tx_mgmt->flow_ctrl[i].mode == SPRD_MODE_NONE) {
				found = 1;
				tx_mgmt->flow_ctrl[i].mode = mode;
				tx_mgmt->flow_ctrl[i].color_bit = i;
				pr_info
				    ("%s, %d, new mode:%d, assign color:%d\n",
				     __func__, __LINE__, mode, i);
				break;
			}
		}
	}
	if (found == 1)
		*index = i;
	return found;
}

static int fc_get_shared_num(struct tx_mgmt *tx_mgmt, u8 num)
{
	u8 i;
	int shared_flow_num = 0;
	unsigned int color_flow;

	for (i = 0; i < MAX_COLOR_BIT; i++) {
		color_flow = atomic_read(&tx_mgmt->flow_ctrl[i].flow);
		if (tx_mgmt->flow_ctrl[i].mode == SPRD_MODE_NONE &&
		    color_flow != 0) {
			if ((num - shared_flow_num) <= color_flow) {
				/*one shared color is enough?*/
				tx_mgmt->color_num[i] = num - shared_flow_num;
				shared_flow_num += num - shared_flow_num;
				break;
			}

			/*need one more shared color*/
			tx_mgmt->color_num[i] = color_flow;
			shared_flow_num += color_flow;
		}
	}
	return shared_flow_num;
}

/*to see there is shared flow or not*/
static int fc_test_shared_num(struct tx_mgmt *tx_mgmt)
{
	u8 i;
	int shared_flow_num = 0;
	unsigned int color_flow;

	for (i = 0; i < MAX_COLOR_BIT; i++) {
		color_flow = atomic_read(&tx_mgmt->flow_ctrl[i].flow);
		if (tx_mgmt->flow_ctrl[i].mode == SPRD_MODE_NONE &&
		    color_flow != 0) {
			shared_flow_num += color_flow;
		}
	}
	return shared_flow_num;
}

struct sprd_hif *sc2355_get_hif(void)
{
	return (struct sprd_hif *)sc2355_hif.hif;
}

void sc2355_set_coex_bt_on_off(u8 action)
{
	struct sprd_hif *hif = sc2355_get_hif();

	hif->coex_bt_on = action;
}

inline int sc2355_tx_cmd(struct sprd_hif *hif, unsigned char *data, int len)
{
	return sdio_tx_one(hif, data, len, hif->tx_cmd_port);
}

inline int sc2355_tx_addr_trans(struct sprd_hif *hif,
				unsigned char *data, int len)
{
	return sdio_tx_one(hif, data, len, hif->tx_data_port);
}

inline void sc2355_tx_addr_trans_free(struct sprd_hif *hif)
{
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;

	sc2355_tx_cmd_pop_list(hif->tx_data_port,
			       (struct mbuf_t *)rx_mgmt->addr_trans_head,
			       (struct mbuf_t *)rx_mgmt->addr_trans_tail,
			       rx_mgmt->addr_trans_num);

	rx_mgmt->addr_trans_head = NULL;
	rx_mgmt->addr_trans_tail = NULL;
	rx_mgmt->addr_trans_num = 0;
}

void sc2355_add_to_free_list(struct sprd_priv *priv,
			     struct list_head *tx_list_head, int tx_count)
{
	struct sprd_hif *hif = &priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	spin_lock_bh(&tx_mgmt->xmit_msg_list.free_lock);
	list_splice_tail(tx_list_head, &tx_mgmt->xmit_msg_list.to_free_list);
	spin_unlock_bh(&tx_mgmt->xmit_msg_list.free_lock);
}

unsigned long tx_packets;
int sc2355_hif_tx_list(struct sprd_hif *hif,
		       struct list_head *tx_list,
		       struct list_head *tx_list_head,
		       int tx_count, int ac_index, u8 coex_bt_on)
{
#define SPRD_MAX_PRINT_LEN 200
	int ret = 0, i = 0, pcie_count = 0, cnt = 0, num = 0;
	struct sprd_msg *msg_pos;

	struct tx_mgmt *tx_mgmt;
	struct mbuf_t *head = NULL, *tail = NULL, *mbuf_pos;
	struct list_head *pos, *tx_list_tail, *n_list;
	unsigned long *msg_ptr;
	unsigned char *data_ptr;
	struct tx_msdu_dscr *dscr;
#if defined(MORE_DEBUG)
	unsigned long tx_bytes = 0;
#endif
	int tx_count_saved = tx_count;
	int list_num;

	pr_debug("%s:%d tx_count is %d\n", __func__, __LINE__, tx_count);
	list_num = sc2355_qos_get_list_num(tx_list);
	if (list_num < tx_count) {
		pr_err("%s, %d, error!, tx_count:%d, list_num:%d\n",
		       __func__, __LINE__, tx_count, list_num);
		WARN_ON(1);
	}

	tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	if (-1 == sdio_bus_list_alloc(hif, tx_count, &head, &tail,
				      &pcie_count, &cnt, &num))
		return -ENOMEM;

	if (tx_count_saved != tx_count) {
		pr_err("%s, %d error!mbuf not enough%d\n",
		       __func__, __LINE__, (tx_count_saved - tx_count));
		tx_mgmt->mbuf_short += (tx_count_saved - tx_count);
		sprdwcn_bus_list_free(hif->tx_data_port, head, tail, tx_count);
		return -ENOMEM;
	}
	mbufalloc += tx_count;

	mbuf_pos = head;

	list_for_each_safe(pos, n_list, tx_list) {
		msg_pos = list_entry(pos, struct sprd_msg, list);
		sc2355_tcp_ack_move_msg(hif->priv, msg_pos);
		data_ptr = (unsigned char *)(msg_pos->tran_data) -
		    hif->hif_offset;
		dscr = (struct tx_msdu_dscr *)msg_pos->tran_data;
		dscr->color_bit = sc2355_fc_set_clor_bit(tx_mgmt, i + 1);
		tx_mgmt->seq_num++;
		dscr->seq_num = tx_mgmt->seq_num;

		if (sprd_get_debug_level() >= L_DBG) {
			int print_len = msg_pos->len;

			if (print_len > SPRD_MAX_PRINT_LEN)
				print_len = SPRD_MAX_PRINT_LEN;
			sc2355_hex_dump("tx to cp2 data",
					(unsigned char *)(msg_pos->tran_data),
					print_len);
		}
#if defined(MORE_DEBUG)
		tx_bytes += msg_pos->skb->len;
#endif
		msg_ptr = (unsigned long *)(data_ptr - sizeof(unsigned long *));
		/*store msg ptr to skb header room
		 *for call back func free
		 */
		*msg_ptr = (unsigned long)msg_pos;

		if (!mbuf_pos) {
			pr_err("%s:%d mbuf addr is NULL!\n", __func__,
			       __LINE__);
			return -1;
		}
		mbuf_pos->buf = data_ptr;
		mbuf_pos->len = msg_pos->len;
		mbuf_pos = mbuf_pos->next;
		if (++i == tx_count)
			break;
	}

	tx_list_tail = pos;
	sdio_list_cut_position(tx_list_head, tx_list, tx_list_tail, ac_index);
	sc2355_add_to_free_list(hif->priv, tx_list_head, tx_count);

	ret = sprdwcn_bus_push_list_direct(hif->tx_data_port,
					   head, tail, tx_count);
	if (ret != 0) {
		sprdwcn_bus_list_free(hif->tx_data_port, head, tail, tx_count);
		sdio_add_tx_list_head(tx_list_head, tx_list,
				      ac_index, tx_count);
		pr_err("%s:%d err Tx data fail\n", __func__, __LINE__);
		mbufalloc -= tx_count;
		tx_mgmt->seq_num -= tx_count;
	} else {
#if defined(MORE_DEBUG)
		UPDATE_TX_PACKETS(hif, tx_count, tx_bytes);
#endif
		INIT_LIST_HEAD(tx_list_head);
		tx_packets += tx_count;
		wl_debug("%s,tx_count=%d,total=%lu,mbuf=%lu,%lu\n",
			__func__, tx_count, tx_packets, mbufalloc, mbufpop);
		sc2355_add_topop_list(hif->tx_data_port, head, tail, tx_count);
	}
	return ret;
}

struct sprd_peer_entry
*sc2355_find_peer_entry_using_addr(struct sprd_vif *vif, u8 *addr)
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
unsigned char sc2355_find_lut_index(struct sprd_hif *hif, struct sprd_vif *vif)
{
	unsigned char i;

	if (is_zero_ether_addr(hif->skb_da))
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
			pr_debug("%s, %d, lut_index=%d\n", __func__, __LINE__,
				 hif->peer_entry[i].lut_index);
			return hif->peer_entry[i].lut_index;
		}
	}

	for (i = 0; i < MAX_LUT_NUM; i++) {
		if ((vif->mode == SPRD_MODE_STATION ||
			 vif->mode == SPRD_MODE_STATION_SECOND ||
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
		vif->mode == SPRD_MODE_STATION_SECOND ||
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

int sc2355_hif_fill_msdu_dscr(struct sprd_vif *vif,
			      struct sk_buff *skb, u8 type, u8 offset)
{
	u8 protocol;
	struct tx_msdu_dscr *dscr;
	struct sprd_hif *hif;
	u8 lut_index;
	unsigned char dscr_rsvd = 0;
	struct ethhdr *ethhdr = (struct ethhdr *)skb->data;
	u8 is_special_data = 0;
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
	dscr_rsvd = 0;
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

	if (skb->data) {
		memcpy(hif->skb_da, skb->data, ETH_ALEN);
	}

	lut_index = sc2355_find_lut_index(hif, vif);
	if (lut_index < 6 && (!sc2355_is_group(hif->skb_da))) {
		pr_debug_ratelimited("%s, %d, sta disconn, no data tx!\n", __func__, __LINE__);
		return -EPERM;
	}
	skb_push(skb, sizeof(struct tx_msdu_dscr) + offset + dscr_rsvd);
	dscr = (struct tx_msdu_dscr *)(skb->data);
	memset(dscr, 0x00, sizeof(struct tx_msdu_dscr));
	dscr->common.type = (type == SPRD_TYPE_CMD ?
			     SPRD_TYPE_CMD : SPRD_TYPE_DATA);
/*remove unnecessary repeated assignment*/
	//dscr->common.direction_ind = 0;
	//dscr->common.need_rsp = 0;/*TODO*/
	dscr->common.interface = vif->ctx_id;
	dscr->pkt_len = cpu_to_le16(skb->len - DSCR_LEN - dscr_rsvd);
	dscr->offset = DSCR_LEN;
/*TODO*/
	//dscr->tx_ctrl.sw_rate = (is_special_data == 1 ? 1 : 0);
	//dscr->tx_ctrl.wds = 0; /*TBD*/
	//dscr->tx_ctrl.swq_flag = 0; /*TBD*/
	//dscr->tx_ctrl.rsvd = 0; /*TBD*/
	//dscr->tx_ctrl.next_buffer_type = 0;
	//dscr->tx_ctrl.pcie_mh_readcomp = 0;
	//dscr->buffer_info.msdu_tid = 0;
	//dscr->buffer_info.mac_data_offset = 0;
	dscr->sta_lut_index = lut_index;

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

inline void *sc2355_get_rx_data(struct sprd_hif *hif,
				void *pos, void **data,
				void **tran_data, int *len, int offset)
{
	struct mbuf_t *mbuf = (struct mbuf_t *)pos;

	*tran_data = mbuf->buf;
	*data = (*tran_data) + offset;
	*len = mbuf->len;
	mbuf->buf = NULL;

	return (void *)mbuf->next;
}

inline void sc2355_free_rx_data(struct sprd_hif *hif,
				int chn, void *head, void *tail, int num)
{
	sprdwcn_bus_push_list(chn, (struct mbuf_t *)head, (struct mbuf_t *)tail,
			      num);
}

void sc2355_handle_pop_list(void *data)
{
	int i;
	struct sprd_msg *msg_pos;
	struct mbuf_t *mbuf_pos = NULL;
	struct pop_work *pop = (struct pop_work *)data;
	struct tx_mgmt *tx_mgmt;
	struct sprd_hif *hif = sc2355_get_hif();
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
	mbufpop += pop->num;
}

int sc2355_add_topop_list(int chn, struct mbuf_t *head,
			  struct mbuf_t *tail, int num)
{
	struct sprd_hif *hif = sc2355_get_hif();
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
	memcpy(misc_work->data, &pop_work, sizeof(struct pop_work));

	sprd_queue_work(hif->priv, misc_work);
	return 0;
}

/*call back func for HIF pop_link*/
int sc2355_tx_data_pop_list(int channel, struct mbuf_t *head,
			    struct mbuf_t *tail, int num)
{
#if defined(MORE_DEBUG)
	struct sprd_msg *msg_head;
#endif

	pr_info("%s channel: %d, head: %p, tail: %p num: %d\n",
		__func__, channel, head, tail, num);

#if defined(MORE_DEBUG)
	struct sprd_hif *hif = sc2355_get_hif();

	msg_head = GET_MSG_BUF(head);
	/*show packet average sent time, unit: ns*/
	sdio_get_tx_avg_time(hif, msg_head->tx_start_time);
#endif

	sc2355_add_topop_list(channel, head, tail, num);
	pr_info("%s:%d free : %d msg buf\n", __func__, __LINE__, num);

	return 0;
}

int sc2355_tx_cmd_pop_list(int channel, struct mbuf_t *head,
			   struct mbuf_t *tail, int num)
{
	int count = 0;
	struct mbuf_t *pos = NULL;
	struct sprd_hif *hif = sc2355_get_hif();
	struct tx_mgmt *tx_mgmt;
	struct sprd_msg *pos_buf, *temp_buf;

	pr_debug("%s channel: %d, head: %p, tail: %p num: %d\n",
		 __func__, channel, head, tail, num);

	tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	pr_debug("%s len: %d buf: %s\n", __func__, head->len, head->buf + 4);

	pos = head;

	list_for_each_entry_safe(pos_buf, temp_buf,
				 &tx_mgmt->tx_list_cmd.cmd_to_free, list) {
		if (pos_buf->tran_data == pos->buf) {
			pr_debug("move CMD node from to_free to free list\n");
			/*list msg from to_free list  to free list*/
			sc2355_free_cmd_buf(pos_buf, &tx_mgmt->tx_list_cmd);

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

int sc2355_push_link(struct sprd_hif *hif, int chn,
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
			    ("err phy address: %lx\n, err virt address: %p\n, err port: %d\n",
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

	if (ret) {
		pr_err("%s: push link fail: %d, chn: %d!\n", __func__, ret,
		       chn);
	}
	return ret;
}

struct sprd_peer_entry
*sc2355_find_peer_entry_using_lut_index(struct sprd_hif *hif,
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
void sc2355_event_sta_lut(struct sprd_vif *vif, u8 *data, u16 len)
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
	if (hif != sc2355_get_hif()) {
		pr_err("%s, wrong hif!\n", __func__);
		return;
	}
	if (!sta_lut) {
		pr_err("%s, NULL input data!\n", __func__);
		return;
	}

	i = sta_lut->sta_lut_index;
	if (i >= MAX_LUT_NUM) {
		pr_err("%s, error sta_lut_index %d!\n", __func__, i);
		return;
	}

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
		sc2355_dis_flush_txlist(hif, i);
		break;
	case UPD_LUT_INDEX:
		sc2355_peer_entry_delba(hif, i);
		sc2355_dis_flush_txlist(hif, i);
		fallthrough;
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

void sc2355_tx_send_addba(struct sprd_vif *vif, void *data, int len)
{
	sdio_tx_ba_mgmt(vif->priv, vif, data, len, CMD_ADDBA_REQ);
}

void sc2355_tx_send_delba(struct sprd_vif *vif, void *data, int len)
{
	struct host_delba_param *delba;

	delba = (struct host_delba_param *)data;
	sdio_tx_ba_mgmt(vif->priv, vif, delba,
			sizeof(struct host_delba_param), CMD_DELBA_REQ);
}

void sc2355_tx_addba(struct sprd_hif *hif,
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
	memcpy(misc_work->data, &addba, sizeof(struct host_addba_param));

	sprd_queue_work(vif->priv, misc_work);
	sprd_put_vif(vif);
}

void sc2355_tx_delba(struct sprd_hif *hif,
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
	memcpy(misc_work->data, &delba, sizeof(struct host_delba_param));
	clear_bit(qos_index_2_tid(ac_index), &peer_entry->ba_tx_done_map);

	sprd_queue_work(vif->priv, misc_work);
	sprd_put_vif(vif);
}

int sc2355_dis_flush_txlist(struct sprd_hif *hif, u8 lut_index)
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

unsigned short sc2355_get_data_csum(void *entry, void *data)
{
	unsigned short csum = 0;
	struct sc2355_sdiohal_puh *puh = (struct sc2355_sdiohal_puh *)data;
	struct rx_msdu_desc *msdu_desc =
	    (struct rx_msdu_desc *)(data + sizeof(*puh));
	unsigned int csum_offset = msdu_total_len(msdu_desc) + sizeof(*puh);
	struct sprd_hif *hif = (struct sprd_hif *)entry;

	pr_debug("%s: check_sum: %d\n", __func__, puh->check_sum);
	if (hif->hw_type == SPRD_HW_SC2355_SDIO && puh->check_sum) {
		memcpy(&csum, (void *)(data + csum_offset), sizeof(csum));
		pr_debug("%s: csum: 0x%x\n", __func__, csum);
	}

	return csum;
}

void sc2355_handle_tx_return(struct sprd_hif *hif,
			     struct sprd_msg_list *list, int send_num, int ret)
{
	u8 i;
	struct tx_mgmt *tx_mgmt = hif->tx_mgmt;
	struct sprd_priv *priv = hif->priv;

	if (ret) {
		printk_ratelimited("%s hif_tx_list err:%d\n", __func__, ret);
		memset(tx_mgmt->color_num, 0x00, MAX_COLOR_BIT);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		usleep_range_state(20, 30, TASK_UNINTERRUPTIBLE);
#else
		usleep_range(20, 30);
#endif
		return;
	}

	tx_mgmt->ring_ap += send_num;
	atomic_sub(send_num, &list->ref);
	sc2355_wake_net_ifneed(tx_mgmt->hif, list, tx_mgmt->mode);

	if (priv->credit_capa == TX_NO_CREDIT)
		return;

	for (i = 0; i < MAX_COLOR_BIT; i++) {
		if (tx_mgmt->color_num[i] == 0)
			continue;
		atomic_sub(tx_mgmt->color_num[i], &tx_mgmt->flow_ctrl[i].flow);
		pr_debug("%s, _fc_, color bit:%d, flow num-%d=%d, seq_num=%d\n",
			 __func__, i, tx_mgmt->color_num[i],
			 atomic_read(&tx_mgmt->flow_ctrl[i].flow),
			 tx_mgmt->seq_num);
	}
}

void sc2355_rx_work_queue(struct work_struct *work)
{
	struct sprd_msg *msg;
	struct sprd_priv *priv;
	struct rx_mgmt *rx_mgmt;
	struct sprd_hif *hif;
	void *pos = NULL, *data = NULL, *tran_data = NULL;
	int len = 0, num = 0;
	struct sprd_vif *vif;
	struct sprd_cmd_hdr *hdr;

	rx_mgmt = container_of(work, struct rx_mgmt, rx_work);
	hif = rx_mgmt->hif;
	priv = hif->priv;

	rx_mgmt->rx_queue_ns = ktime_get_boot_fast_ns();

	if (!hif->exit && !sprd_peek_msg(&rx_mgmt->rx_list))
		sc2355_rx_process(rx_mgmt, NULL);

	while ((msg = sprd_peek_msg(&rx_mgmt->rx_list))) {
		if (hif->exit)
			goto next;

		pos = msg->tran_data;
		for (num = msg->len; num > 0; num--) {
			pos = sc2355_get_rx_data(hif, pos, &data, &tran_data,
						 &len, hif->hif_offset);

			pr_debug("%s: rx type:%d, num = %d\n",
				 __func__, SPRD_HEAD_GET_TYPE(data), num);

			/* len in mbuf_t just means buffer len in ADMA,
			 * so need to get data len in sc2355_sdiohal_puh
			 */
			/* RX control messages can include firmware key material. */

			/* to check is the rsp_cnt from CP2
			 * eqaul to rsp_cnt count on driver side.
			 * if not equal, must be lost on SDIOHAL/PCIE.
			 * assert to warn CP2
			 */
			hdr = (struct sprd_cmd_hdr *)data;
			vif = sc2355_ctxid_to_vif(priv, hdr->common.mode);
			if ((SPRD_HEAD_GET_TYPE(data) == SPRD_TYPE_CMD ||
			     SPRD_HEAD_GET_TYPE(data) == SPRD_TYPE_EVENT)) {
				if (rx_mgmt->rsp_event_cnt != hdr->rsp_cnt) {
					pr_info
					    ("%s, %d, rsp_event_cnt=%d, hdr->cnt=%d\n",
					     __func__, __LINE__,
					     rx_mgmt->rsp_event_cnt,
					     hdr->rsp_cnt);

					if (hdr->rsp_cnt == 0) {
						rx_mgmt->rsp_event_cnt = 0;
						pr_info
						    ("%s reset rsp_event_cnt",
						     __func__);
					}
					/* hdr->rsp_cnt=0 means it's a
					 * old version CP2,
					 * so do not assert.
					 * vif=NULL means driver not init ok,
					 * send cmd may cause crash
					 */
					if (vif && hdr->rsp_cnt != 0)
						sc2355_assert_cmd(priv, vif,
								    hdr->cmd_id,
								 RSP_CNT_ERROR);
				}

				rx_mgmt->rsp_event_cnt++;
			}
			sprd_put_vif(vif);

			switch (SPRD_HEAD_GET_TYPE(data)) {
			case SPRD_TYPE_DATA:
				if (msg->len > SPRD_MAX_DATA_RXLEN)
					pr_err("err rx data too long:%d > %d\n",
					       len, SPRD_MAX_DATA_RXLEN);
				rx_data_process(priv, data);
				break;
			case SPRD_TYPE_CMD:
				if (msg->len > SPRD_MAX_CMD_RXLEN)
					pr_err("err rx cmd too long:%d > %d\n",
					       len, SPRD_MAX_CMD_RXLEN);
				sc2355_rx_rsp_process(priv, data);
				break;

			case SPRD_TYPE_EVENT:
				if (msg->len > SPRD_MAX_CMD_RXLEN)
					pr_err
					    ("err rx event too long:%d > %d\n",
					     len, SPRD_MAX_CMD_RXLEN);
				sc2355_rx_evt_process(priv, data);
				break;
			case SPRD_TYPE_DATA_SPECIAL:
				sprd_debug_ts_leave(RX_SDIO_PORT);
				sprd_debug_ts_enter(RX_SDIO_PORT);

				if (msg->len > SPRD_MAX_DATA_RXLEN)
					pr_err
					    ("err data trans too long:%d > %d\n",
					     len, SPRD_MAX_CMD_RXLEN);
				sc2355_mm_mh_data_process(&rx_mgmt->mm_entry, tran_data, len,
						   msg->buffer_type);
				tran_data = NULL;
				data = NULL;
				break;
			case SPRD_TYPE_DATA_PCIE_ADDR:
				if (msg->len > SPRD_MAX_CMD_RXLEN)
					pr_err
					    ("err rx mh data too long:%d > %d\n",
					     len, SPRD_MAX_DATA_RXLEN);
				sc2355_rx_mh_addr_process(rx_mgmt, tran_data, len,
						   msg->buffer_type);
				tran_data = NULL;
				data = NULL;
				break;
			default:
				pr_err("rx unknown type:%d\n",
				       SPRD_HEAD_GET_TYPE(data));
				break;
			}

			/* Marlin3 should release buffer by ourself */
			if (tran_data)
				sc2355_free_data(tran_data, msg->buffer_type);

			if (!pos) {
				pr_debug("%s no mbuf\n", __func__);
				break;
			}
		}
next:
		/* TODO: Should we free mbuf one by one? */
		sc2355_free_rx_data(hif, msg->fifo_id, msg->tran_data,
				    msg->data, msg->len);
		sprd_dequeue_msg(msg, &rx_mgmt->rx_list);
	}
}
int sc2355_fc_get_send_num(struct sprd_hif *hif,
			   enum sprd_mode mode, int data_num)
{
	int excusive_flow_num = 0, shared_flow_num = 0;
	int send_num = 0;
	u8 i = 0;
	struct tx_mgmt *tx_mgmt = hif->tx_mgmt;
	struct sprd_priv *priv = hif->priv;

	if (data_num <= 0 || mode == SPRD_MODE_NONE)
		return 0;

	if (data_num > 64)
		data_num = 64;
	if (priv->credit_capa == TX_NO_CREDIT)
		return data_num;

	memset(tx_mgmt->color_num, 0x00, MAX_COLOR_BIT);

	if (fc_find_color_per_mode(tx_mgmt, mode, &i) == 1) {
		excusive_flow_num = atomic_read(&tx_mgmt->flow_ctrl[i].flow);
		if (excusive_flow_num >= data_num) {
			/*excusive flow is enough, do not need shared flow*/
			send_num = tx_mgmt->color_num[i] = data_num;
		} else {
			/*excusive flow not enough, need shared flow
			 *total give num =  excusive + shared
			 *(may be more than one color)
			 */
			u8 num_need = data_num - excusive_flow_num;

			shared_flow_num = fc_get_shared_num(tx_mgmt, num_need);
			tx_mgmt->color_num[i] = excusive_flow_num;
			send_num = excusive_flow_num + shared_flow_num;
		}

		if (send_num <= 0) {
			pr_err
			    ("%s, %d, mode:%d, e_num:%d, s_num:%d, d_num:%d\n",
			     __func__, __LINE__, (u8)mode, excusive_flow_num,
			     shared_flow_num, data_num);
			return -ENOMEM;
		}
		pr_debug("%s,mode:%d,e_n:%d,s_n:%d,d_n:%d,{%d,%d,%d,%d}\n",
			__func__, mode, excusive_flow_num,
			shared_flow_num, data_num,
			tx_mgmt->color_num[0], tx_mgmt->color_num[1],
			tx_mgmt->color_num[2], tx_mgmt->color_num[3]);
	} else {
		pr_err("%s, %d, wrong mode:%d?\n",
		       __func__, __LINE__, (u8)mode);
		for (i = 0; i < MAX_COLOR_BIT; i++)
			pr_err("color[%d] assigned mode%d\n",
			       i, (u8)tx_mgmt->flow_ctrl[i].mode);
		return -ENOMEM;
	}

	return send_num;
}

int sc2355_fc_test_send_num(struct sprd_hif *hif,
			    enum sprd_mode mode, int data_num)
{
	int excusive_flow_num = 0, shared_flow_num = 0;
	int send_num = 0;
	u8 i = 0;
	struct tx_mgmt *tx_mgmt = hif->tx_mgmt;
	struct sprd_priv *priv = hif->priv;

	if (data_num <= 0 || mode == SPRD_MODE_NONE)
		return 0;

	if (data_num > 64)
		data_num = 64;
	if (priv->credit_capa == TX_NO_CREDIT)
		return data_num;

	if (fc_find_color_per_mode(tx_mgmt, mode, &i) == 1) {
		excusive_flow_num = atomic_read(&tx_mgmt->flow_ctrl[i].flow);
		shared_flow_num = fc_test_shared_num(tx_mgmt);
		send_num = excusive_flow_num + shared_flow_num;

		if (send_num <= 0) {
			pr_debug
			    ("%s, %d, err, mode:%d, e_num:%d, s_num:%d, d_num=%d\n",
			     __func__, __LINE__, (u8)mode, excusive_flow_num,
			     shared_flow_num, data_num);
			return -ENOMEM;
		}
		pr_debug("%s, %d, e_num=%d, s_num=%d, d_num=%d\n",
			 __func__, __LINE__, excusive_flow_num,
			 shared_flow_num, data_num);
	} else {
		pr_err("%s, %d, wrong mode:%d?\n",
		       __func__, __LINE__, (u8)mode);
		for (i = 0; i < MAX_COLOR_BIT; i++)
			printk_ratelimited("color[%d] assigned mode%d\n",
					   i, (u8)tx_mgmt->flow_ctrl[i].mode);
		return -ENOMEM;
	}

	return min(send_num, data_num);
}

void sc2355_sdio_throughput_static_init(void)
{
	throughput_static.tx_bytes = 0;
	throughput_static.last_time = jiffies;
	throughput_static.rx_bytes = 0;
	throughput_static.rx_last_time = jiffies;
	throughput_static.disable_pd_flag = false;
	throughput_static.uclamp_set_flag = false;
	throughput_static.throughput_tx = 0;
	throughput_static.throughput_rx = 0;
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	cpu_latency_qos_add_request(&throughput_static.pm_qos_request_idle,
			   PM_QOS_CPU_LATENCY_DEFAULT_VALUE);
#else
	pm_qos_add_request(&throughput_static.pm_qos_request_idle,
			   PM_QOS_CPU_DMA_LATENCY, PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE);
#endif
}

void sc2355_sdio_throughput_static_deinit(void)
{
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	cpu_latency_qos_remove_request(&throughput_static.pm_qos_request_idle);
#else
	pm_qos_remove_request(&throughput_static.pm_qos_request_idle);
#endif
}

void sc2355_sdio_throughput_ctl_core_pd(unsigned int len)
{
	throughput_static.tx_bytes += len;
	if (time_after(jiffies, throughput_static.last_time +  msecs_to_jiffies(1000))) {
		throughput_static.last_time = jiffies;
		if ((throughput_static.tx_bytes >= DISABLE_PD_THRESHOLD) ||
			(throughput_static.throughput_rx >= DISABLE_PD_THRESHOLD)) {
			if (!throughput_static.disable_pd_flag)	{
				throughput_static.disable_pd_flag = true;
				// forbid core powerdown
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
				cpu_latency_qos_update_request(
							 &throughput_static.pm_qos_request_idle,
							 100);
#else
				pm_qos_update_request(&throughput_static.pm_qos_request_idle,
					      100);
#endif
			}
		} else {
			if (throughput_static.disable_pd_flag) {
				throughput_static.disable_pd_flag = false;
				//allow core powerdown
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
				cpu_latency_qos_update_request(
							&throughput_static.pm_qos_request_idle,
							PM_QOS_CPU_LATENCY_DEFAULT_VALUE);
#else
				pm_qos_update_request(&throughput_static.pm_qos_request_idle,
					      PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE);
#endif
			}
		}
		throughput_static.throughput_tx = throughput_static.tx_bytes;
		throughput_static.tx_bytes = 0;
	}
}

void sc2355_sdio_rx_throughput_statistic(unsigned int len)
{
	throughput_static.rx_bytes += len;
	if (time_after(jiffies, throughput_static.rx_last_time +  msecs_to_jiffies(1000))) {
		throughput_static.rx_last_time = jiffies;
		throughput_static.throughput_rx = throughput_static.rx_bytes;
		throughput_static.rx_bytes = 0;
	}
}

int sc2355_sdio_init(struct sprd_hif *hif)
{
	u8 i;
	int ret = -EINVAL, chn = 0;
	struct tx_mgmt *tx_mgmt = NULL;

	hif->hw_type = SPRD_HW_SC2355_SDIO;

	for (i = 0; i < MAX_LUT_NUM; i++)
		hif->peer_entry[i].ctx_id = 0xff;

	hif->hif_offset = sizeof(struct sc2355_sdiohal_puh);
	hif->rx_cmd_port = SDIO_RX_CMD_PORT;
	hif->rx_data_port = SDIO_RX_DATA_PORT;
	hif->tx_cmd_port = SDIO_TX_CMD_PORT;
	hif->tx_data_port = SDIO_TX_DATA_PORT;

	ret = sc2355_rx_init(hif);
	if (ret) {
		pr_err("%s rx init failed: %d\n", __func__, ret);
		return ret;
	}

	ret = sc2355_tx_init(hif);
	if (ret) {
		pr_err("%s tx_list init failed\n", __func__);
		goto err_tx_init;
	}

	sc2355_sdio_throughput_static_init();
	tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	//reset thread uclamp param
	sc2355_set_thread_uclamp(tx_mgmt->tx_thread, 0);

	if (hif->hw_type == SPRD_HW_SC2355_SDIO) {
		sc2355_hif.mchn_ops = sdio_hif_ops;
		sc2355_hif.max_num =
		    sizeof(sdio_hif_ops) / sizeof(struct mchn_ops_t);
	}

	hif->feature = NETIF_F_CSUM_MASK | NETIF_F_SG;

	if (sc2355_hif.max_num < MAX_CHN_NUM) {
		pr_info("%s: register %d ops\n", __func__, sc2355_hif.max_num);

		for (chn = 0; chn < sc2355_hif.max_num; chn++) {
			ret = sprdwcn_bus_chn_init(&sc2355_hif.mchn_ops[chn]);
			if (ret < 0)
				goto err;
		}

		sc2355_hif.hif = (void *)hif;
		hif->fw_awake = 1;
		hif->fw_power_down = 0;
	}

	hif->cp_asserted = 0;
	hif->exit = 0;
	return 0;

err:
	pr_err("%s: unregister %d ops\n", __func__, sc2355_hif.max_num);

	for (; chn > 0; chn--)
		sprdwcn_bus_chn_deinit(&sc2355_hif.mchn_ops[chn]);
	sc2355_hif.mchn_ops = NULL;
	sc2355_hif.max_num = 0;

	sc2355_tx_deinit(hif);
err_tx_init:
	sc2355_rx_deinit(hif);

	return ret;
}

void sc2355_sdio_deinit(struct sprd_hif *hif)
{
	int chn = 0;

	for (chn = 0; chn < sc2355_hif.max_num; chn++)
		sprdwcn_bus_chn_deinit(&sc2355_hif.mchn_ops[chn]);
	sc2355_hif.hif = NULL;
	sc2355_hif.max_num = 0;

	sc2355_sdio_throughput_static_deinit();
	sc2355_tx_deinit(hif);
	sc2355_rx_deinit(hif);
}

static struct sprd_hif_ops sc2355_sdio_ops = {
	.init = sc2355_sdio_init,
	.deinit = sc2355_sdio_deinit,
	.sync_version = sc2355_sync_version,
	.tx_special_data = sprd_tx_special_data,
	.download_hw_param = sc2355_download_hw_param,
	.reset = sc2355_reset,
#ifdef DRV_RESET_SELF
	.reset_self = sc2355_reset_self,
#endif
	.throughput_ctl_pd = sc2355_sdio_throughput_ctl_core_pd,
	.tx_flush = sc2355_tx_flush,
};

extern struct sprd_chip_ops sc2355_chip_ops;
int sc2355_sdio_probe(struct platform_device *pdev)
{
	return sprd_iface_probe(pdev, &sc2355_sdio_ops, &sc2355_chip_ops);
}

#if 0
static void sdio_remove(struct platform_device *pdev)
{
	return;
}

static const struct of_device_id sc2355_sdio_of_match[] = {
	{.compatible = "sprd,sc2355-sdio-wifi",},
	{}
};

MODULE_DEVICE_TABLE(of, sc2355_sdio_of_match);

static struct platform_driver sc2355_sdio_driver = {
	.probe = sdio_probe,
	.remove = sdio_remove,
	.driver = {
		   .owner = THIS_MODULE,
		   .name = "wlan",
		   .of_match_table = sc2355_sdio_of_match,
	}
};

module_platform_driver(sc2355_sdio_driver);

MODULE_DESCRIPTION("Spreadtrum SC2355 SDIO Initialization");
MODULE_AUTHOR("Spreadtrum WCN Division");
MODULE_LICENSE("GPL");
#endif
