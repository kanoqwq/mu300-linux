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

#ifndef __HIF_H__
#define __HIF_H__

#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <misc/marlin_platform.h>

#include "debug.h"
#include "msg.h"
#include "qos.h"
#include "tdls.h"

#define MAX_TDLS_PEER		32
#define MAX_LUT_NUM		32
#define SPRD_TX_CMD_TIMEOUT	3000
#define SPRD_TX_DATA_TIMEOUT	4000

#define SPRD_PS_SUSPENDING	1
#define SPRD_PS_SUSPENDED	2
#define SPRD_PS_RESUMING	3
#define SPRD_PS_RESUMED		0

enum sprd_hif_type {
	SPRD_HW_SC2332_SDIO,
	SPRD_HW_SC2332_SIPC,
	SPRD_HW_SC2355_SDIO,
	SPRD_HW_SC2355_PCIE,
	SPRD_HW_SC2355_USB,
	SPRD_HW_SC2355_SIPC,
};

struct tx_address {
	u8 da[ETH_ALEN];
	u8 sa[ETH_ALEN];
};

struct rx_address {
	u8 sa[ETH_ALEN];
	u8 da[ETH_ALEN];
};

#if defined(MORE_DEBUG)
/* tx/rx states and performance statistics */
struct txrx_stats {
	unsigned long rx_packets;
	/* tx success packets num */
	unsigned long tx_packets;
	unsigned long rx_bytes;
	/* tx success bytes num */
	unsigned long tx_bytes;
	unsigned long rx_errors;
	unsigned long tx_errors;
	unsigned int tx_nomem_errors;
	unsigned int tx_fail_errors;
	unsigned long rx_dropped;
	unsigned long tx_dropped;
	/* alloc pkt fail */
	unsigned long rx_pktgetfail;
	unsigned long tx_pktgetfail;
	/* Number of tx packets we had to realloc for headroom */
	unsigned long tx_realloc;
	/* multicast packets received */
	unsigned long rx_multicast;
	unsigned long tx_multicast;
	unsigned long tx_cost_time;
	unsigned long tx_avg_time;
	unsigned long tx_arp_num;
	/* qos ac stream1 sent num */
	unsigned long ac1_num;
	/* qos ac stream2 sent num */
	unsigned long ac2_num;
	unsigned long tx_filter_num;
	/* statistical sample count */
	unsigned int gap_num;
};
#endif

struct sprd_peer_entry {
	union {
		struct rx_address rx;
		struct tx_address tx;
	};

	u8 lut_index;
	u8 ctx_id;
	u8 cipher_type;
	u8 pending_num;
	u8 ht_enable;
	u8 vht_enable;
	u8 ip_acquired;
	/* tx ba done based on tid */
	unsigned long ba_tx_done_map;
	u8 vowifi_enabled;
	u8 vowifi_pkt_cnt;
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	struct timespec64 time[6 + 1];
#else
	struct timespec time[6 + 1];
#endif
};

struct sprd_hif {
	struct platform_device *pdev;
	const struct sprd_hif_ops *ops;
	struct sprd_priv *priv;
	netdev_features_t feature;

	int exit;
	atomic_t power_cnt;
	/* MU300: one WCN power transition at a time (sprd_iface_set_power) */
	struct mutex power_lock;
	int flag;
	int lastflag;
	u8 cp_asserted;
	enum sprd_hif_type hw_type;

	/* SC2332 */
	unsigned long cmd_timeout;
	unsigned long data_timeout;
	/* lock for do_tx */
	spinlock_t lock;
	unsigned long do_tx;
	wait_queue_head_t waitq;
	unsigned int net_stop_cnt;
	unsigned int net_start_cnt;
	unsigned int drop_cmd_cnt;
	/* sta */
	unsigned int drop_data1_cnt;
	/* p2p */
	unsigned int drop_data2_cnt;
	unsigned int ring_cp;
	unsigned int ring_ap;
	atomic_t flow0;
	atomic_t flow1;
	atomic_t flow2;
	struct sprd_qos_t qos0;
	struct sprd_qos_t qos1;
	struct sprd_qos_t qos2;

	/* 1 for send data; 0 for not send data */
	int driver_status;

	/* lists are not included in struct sprd_vif
	 * while sprd wifi driver uses less cmd ports
	 */
	struct sprd_msg_list tx_list0;
	/* for STA/SOFTAP data */
	struct sprd_msg_list tx_list1;
	/* for P2P data */
	struct sprd_msg_list tx_list2;
	struct wakeup_source *tx_wakelock;
	/* tx may go into deepsleep while screen is off */
	struct wakeup_source *keep_wake;
	unsigned long wake_last_time;
	unsigned long wake_timeout;
	unsigned long wake_pre_timeout;

	struct work_struct tx_work;
	struct workqueue_struct *tx_queue;

	/* SC2355 */
	int tx_mode;
	int rx_mode;

	/* Manage tx function */
	void *tx_mgmt;
	/* Manage rx function */
	void *rx_mgmt;

	struct sprd_peer_entry peer_entry[MAX_LUT_NUM];
	unsigned long tx_num[MAX_LUT_NUM];
	unsigned char skb_da[ETH_ALEN];

	int hif_offset;
	unsigned char rx_cmd_port;
	unsigned char rx_data_port;
	unsigned char tx_cmd_port;
	unsigned char tx_data_port;
#if defined(MORE_DEBUG)
	struct txrx_stats stats;
#endif

	u8 tdls_flow_count_enable;
	struct tdls_flow_count_para tdls_flow_count[MAX_TDLS_PEER];
	int suspend_mode;

	int fw_power_down;
	int fw_awake;

	/* for pkt log function */
	loff_t lp;
	struct file *pfile;
	/* for suspend resume time count */
	unsigned long sleep_time;

	/* wifi bt coex mode, 1: BT is on, 0: BT is off */
	u8 coex_bt_on;
	void *mbuf_head;
	void *mbuf_tail;
	int mbuf_num;
	int pushfail_count;
	int remove_flag;

	/* block command before stop marlin */
	atomic_t block_cmd_after_close;
	/* block command while change iface */
	atomic_t change_iface_block_cmd;
#ifdef DRV_RESET_SELF
	u8 drv_resetting;
#endif
};

struct sprd_hif_ops {
	int (*init)(struct sprd_hif *hif);
	void (*deinit)(struct sprd_hif *hif);
	int (*post_init)(struct sprd_hif *hif);
	void (*post_deinit)(struct sprd_hif *hif);
	int (*reserv_len)(struct sprd_hif *hif);
	int (*sync_version)(struct sprd_priv *priv);
	void (*download_hw_param)(struct sprd_priv *priv);
	void (*fill_all_buffer)(struct sprd_hif *hif);
	int (*tx_special_data)(struct sk_buff *skb,
			       struct net_device *ndev);
	void (*free_msg_content)(struct sprd_msg *msg);
	int (*tx_free_data)(struct sprd_priv *priv, unsigned char *data);
	int (*tx_addr_trans)(struct sprd_hif *hif,
			     unsigned char *data, int len,
			     bool send_now);
	int (*reset)(struct sprd_hif *hif);
	void (*throughput_ctl_pd)(unsigned int len);
#ifdef DRV_RESET_SELF
	int (*reset_self)(struct sprd_priv *priv);
#endif
	void (*tx_flush)(struct sprd_hif *hif, struct sprd_vif *vif);
};

void sprd_clean_work(struct sprd_priv *priv);

static inline int sprd_hif_post_init(struct sprd_hif *hif)
{
	if (hif->ops->post_init)
		return hif->ops->post_init(hif);
	return 0;
}

static inline void sprd_hif_post_deinit(struct sprd_hif *hif)
{
	if (hif->ops->post_deinit)
		hif->ops->post_deinit(hif);
}

static inline bool sprd_hif_is_on(struct sprd_hif *hif)
{
	return (atomic_read(&hif->power_cnt) == 0 ? false : true);
}

static inline int sprd_sync_version(struct sprd_hif *hif)
{
	if (hif->ops->sync_version)
		return hif->ops->sync_version(hif->priv);

	return 0;
}

static inline void sprd_download_hw_param(struct sprd_hif *hif)
{
	if (hif->ops->download_hw_param)
		hif->ops->download_hw_param(hif->priv);
}

static inline int sprd_hif_power_on(struct sprd_hif *hif)
{
	atomic_add(1, &hif->power_cnt);

	if (atomic_read(&hif->power_cnt) != 1)
		return 0;

	if (start_marlin(MARLIN_WIFI)) {
		atomic_sub(1, &hif->power_cnt);
		return -ENODEV;
	}

	if (sprd_hif_post_init(hif)) {
		/* MU300: post_init unwound its channels; the chip was started, stop it as a power-off would */
		if (stop_marlin(MARLIN_WIFI))
			pr_err("stop_marlin failed!!\n");
		atomic_sub(1, &hif->power_cnt);
		return -ENODEV;
	}

	/* need reset hif->exit flag, if wcn reset happened */
	if (unlikely(hif->exit) || unlikely(hif->cp_asserted)) {
		pr_info("assert happended! need reset paras!\n");
		if (hif->ops->reset)
			hif->ops->reset(hif);
	}

	if (sprd_sync_version(hif)) {
		/*
		 * MU300: undo the power-on as sprd_hif_power_off() does.  The vendor left the channels registered
		 * and the chip started with the count at 0: the next power-on registered them again over live ones,
		 * and a probe that failed here freed hif under channels that could still interrupt.
		 */
		sprd_clean_work(hif->priv);
		sprd_hif_post_deinit(hif);
		if (stop_marlin(MARLIN_WIFI))
			pr_err("stop_marlin failed!!\n");
		atomic_sub(1, &hif->power_cnt);
		return -EIO;
	}

	sprd_download_hw_param(hif);

	return 0;
}

static inline void sprd_hif_power_off(struct sprd_hif *hif)
{
	atomic_sub(1, &hif->power_cnt);

	if (atomic_read(&hif->power_cnt) != 0)
		return;

	sprd_clean_work(hif->priv);
	sprd_hif_post_deinit(hif);

	if (stop_marlin(MARLIN_WIFI))
		pr_err("stop_marlin failed!!\n");
}

static inline int sprd_hif_init(struct sprd_hif *hif)
{
	return hif->ops->init(hif);
}

static inline void sprd_hif_deinit(struct sprd_hif *hif)
{
	hif->ops->deinit(hif);
}

static inline int sprd_hif_reserve_len(struct sprd_hif *hif)
{
	if (hif->ops->reserv_len)
		return hif->ops->reserv_len(hif);

	return 0;
}

static inline void sprd_hif_fill_all_buffer(struct sprd_hif *hif)
{
	if (hif->ops->fill_all_buffer)
		hif->ops->fill_all_buffer(hif);
}

static inline int sprd_hif_tx_special_data(struct sprd_hif *hif,
					   struct sk_buff *skb,
					   struct net_device *ndev)
{
	if (hif->ops->tx_special_data)
		return hif->ops->tx_special_data(skb, ndev);
	return -1;
}

static inline void sprd_hif_throughput_ctl_pd(struct sprd_hif *hif, unsigned int len)
{
	if (hif->ops->throughput_ctl_pd)
		hif->ops->throughput_ctl_pd(len);
}

static inline void sprd_hif_tx_flush(struct sprd_hif *hif, struct sprd_vif *vif)
{
	if (hif->ops->tx_flush)
		hif->ops->tx_flush(hif, vif);
}
#endif
