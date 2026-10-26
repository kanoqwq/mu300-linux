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

#ifndef __TX_H__
#define __TX_H__
#include <linux/types.h>
#include <linux/workqueue.h>

#include "common/cfg80211.h"
#include "common/msg.h"
#include "common/qos.h"
#include "sdio.h"
#include "pcie.h"

#define WAPI_TYPE                 0x88B4
/* descriptor len + sdio/sdio.header len + offset */
#define SKB_DATA_OFFSET			15
#define SPRD_SDIO_MASK_LIST_CMD		0x1
#define SPRD_SDIO_MASK_LIST_SPECIAL	0x2
#define SPRD_SDIO_MASK_LIST_DATA	0x4
#define TX_TIMEOUT_DROP_RATE		50

/* The number of bytes in an ethernet (MAC) address. */
#define	ETHER_ADDR_LEN 	6

/* The number of bytes in the type field. */
#define	ETHER_TYPE_LEN	2

/* The length of the combined header. */
#define	ETHER_HDR_LEN	(ETHER_ADDR_LEN * 2 + ETHER_TYPE_LEN)

#define DHCP_SERVER_PORT	0x0043
#define DHCP_CLIENT_PORT	0x0044
#define DHCP_SERVER_PORT_IPV6	0x0223
#define DHCP_CLIENT_PORT_IPV6	0x0222
#define ETH_P_PREAUTH		0x88C7

#define DNS_SERVER_PORT		0x0035

#define VOWIFI_SIP_DSCP		0x1a
#define VOWIFI_IKE_DSCP		0x30
#define VOWIFI_VIDEO_DSCP	0x28
#define VOWIFI_AUDIO_DSCP	0x36

#define VOWIFI_IKE_SIP_PORT	4500
#define VOWIFI_IKE_ONLY_PORT	500

#define DUAL_VOWIFI_NOT_SUPPORT  	0x0
#define DUAL_VOWIFI_SIP_MARK  		0x10000000
#define DUAL_VOWIFI_IKE_MARK  		0x20000000
#define DUAL_VOWIFI_VOICE_MARK  	0x04000000
#define DUAL_VOWIFI_VIDEO_MARK  	0x08000000
#define DUAL_VOWIFI_MASK_MARK		0x3C000000

#define TX_MAX_POLLING	10

#define MAX_COLOR_BIT	4
/* from hw_if */
#define HW_TYPE_SDIO 	0
#define HW_TYPE_PCIE	1
#define HW_TYPE_SIPC	2
#define HW_TYPE_USB	3

#define MAX_CHN_NUM	16

#define DSCR_LEN	sizeof(struct tx_msdu_dscr)

#define GET_MSG_BUF(ptr) \
	((struct sprd_msg *) \
	(*(unsigned long *)((ptr)->buf - sizeof(unsigned long *))))

#if defined(MORE_DEBUG)
#define STATS_COUNT	200

#define UPDATE_TX_PACKETS(dev, tx_count, tx_bytes) do { \
	(dev)->stats.tx_packets += (tx_count); \
	(dev)->stats.tx_bytes += (tx_bytes); \
	(dev)->stats.gap_num += (tx_count); \
} while (0)
#endif

#define DOT11_ADDBA_POLICY_DELAYED	0	/* delayed BA policy */

#define DOT11_ADDBA_POLICY_IMMEDIATE	1	/* immediate BA policy */

struct flow_control {
	enum sprd_mode mode;
	u8 color_bit;
	atomic_t flow;
};

struct tx_mgmt {
#define HANG_RECOVERY_ACKED	2
	struct sprd_hif *hif;
	unsigned long cmd_timeout;
	unsigned long data_timeout;
	unsigned long net_stop_cnt;
	unsigned long net_start_cnt;
	unsigned long drop_cmd_cnt;
	unsigned long drop_data_cnt;
	/* sta */
	unsigned long drop_data1_cnt;
	/* p2p */
	unsigned long drop_data2_cnt;
	unsigned long ring_cp;
	unsigned long ring_ap;
	atomic_t flow0;
	/* MU300: a PCIe data submission waits for the bus' completion (pcie.c) */
	atomic_t pcie_tx_waiting;
	atomic_t flow1;
	atomic_t flow2;
	unsigned long tx_num;
	unsigned long txc_num;

	enum sprd_mode mode;

	/* 4 flow control color, 00/01/10/11 */
	struct flow_control flow_ctrl[MAX_COLOR_BIT];
	unsigned char color_num[MAX_COLOR_BIT];
	unsigned char seq_num;
	/* temp for cmd debug, remove in future */
	unsigned int cmd_send;
	unsigned int cmd_poped;
	int mbuf_short;
	ktime_t kt;
	int hang_recovery_status;
	int thermal_status;

	struct sprd_msg_list tx_list_cmd;
	struct sprd_msg_list tx_list_qos_pool;
	struct sprd_xmit_msg_list xmit_msg_list;
	struct qos_tx_t *tx_list[SPRD_MODE_MAX];
	int net_stopped;

	int tx_thread_exit;
	struct completion tx_completed;
	struct task_struct *tx_thread;
};

struct tx_msdu_dscr {
	struct {
		/* 0:cmd, 1:event, 2:normal data,
		 * 3:special data, 4:PCIE remote addr
		 */
		unsigned char type:3;
		/* direction of address buffer of cmd/event,
		 * 0:Tx, 1:Rx
		 */
		unsigned char direction_ind:1;
		unsigned char need_rsp:1;
		/* ctxt_id */
		unsigned char interface:3;
	} common;
	unsigned char offset;
	struct {
		/* 1:need HW to do checksum */
		unsigned char checksum_offload:1;
		/* 0:udp, 1:tcp */
		unsigned char checksum_type:1;
		/* 1:use SW rate,no aggregation 0:normal */
		unsigned char sw_rate:1;
		/* WDS frame */
		unsigned char wds:1;
		/* 1:frame sent from SWQ to MH,
		 * 0:frame sent from TXQ to MH,
		 * default:0
		 */
		unsigned char swq_flag:1;
		unsigned char rsvd:1;
		/* used by PCIe address buffer, need set default:0 */
		unsigned char next_buffer_type:1;
		/* used by PCIe address buffer, need set default:0 */
		unsigned char pcie_mh_readcomp:1;
	} tx_ctrl;
	unsigned short pkt_len;
	struct {
		unsigned char msdu_tid:4;
		unsigned char mac_data_offset:4;
	} buffer_info;
	unsigned char sta_lut_index;
	unsigned char color_bit:2;
	unsigned char seq_num:8;
	unsigned char rsvd:6;
	unsigned short tcp_udp_header_offset;
} __packed;

struct txc_addr_buff {
	struct {
		unsigned char type:3;
		unsigned char direction_ind:1;
		unsigned char need_rsp:1;
		unsigned char interface:3;
	} common;
	/* addr offset from common */
	unsigned char offset;
	struct {
		unsigned char cksum:1;
		unsigned char cksum_type:1;
		unsigned char sw_ctrl:1;
		unsigned char wds:1;
		unsigned char swq_flag:1;
		unsigned char rsvd:1;
		/* 0: data buffer, 1: address buffer */
		unsigned char next_buffer_type:1;
		/* used only by address buffer
		 * 0: MH process done, 1: before send to MH
		 */
		unsigned char mh_done:1;
	} tx_ctrl;
	unsigned short number;
	unsigned short rsvd;
} __packed;

enum addba_req_result {
	ADDBA_REQ_RESULT_SUCCESS,
	ADDBA_REQ_RESULT_FAIL,
	ADDBA_REQ_RESULT_TIMEOUT,
	ADDBA_REQ_RESULT_DECLINE,
};

struct ieeetypes_addba_param {
	u16 amsdu_permit:1;
	u16 ba_policy:1;
	u16 tid:4;
	u16 buffer_size:10;
} __packed;

struct ieeetypes_delba_param {
	u16 reserved:11;
	u16 initiator:1;
	u16 tid:4;
} __packed;

struct host_addba_param {
	u8 lut_index;
	u8 perr_mac_addr[ETH_ALEN];
	u8 dialog_token;
	struct ieeetypes_addba_param addba_param;
	u16 timeout;
} __packed;

struct host_delba_param {
	u8 lut_index;
	u8 perr_mac_addr[ETH_ALEN];
	struct ieeetypes_delba_param delba_param;
	u16 reason_code;
} __packed;

struct pop_work {
	int chn;
	void *head;
	void *tail;
	int num;
};

static inline bool sc2355_is_group(unsigned char *addr)
{
	if ((addr[0] & BIT(0)) != 0)
		return true;

	return false;
}

void sc2355_fc_add_share_credit(struct sprd_vif *vif);
void sc2355_tx_down(struct tx_mgmt *tx_mgmt);
void sc2355_tx_up(struct tx_mgmt *tx_mgmt);

struct sprd_msg *sc2355_tx_get_msg(struct sprd_chip *chip,
				   enum sprd_head_type type,
				   enum sprd_mode mode);
void sc2355_tx_free_msg(struct sprd_chip *chip, struct sprd_msg *msg);
int sc2355_tx_prepare(struct sprd_chip *chip, struct sk_buff *skb);
int sc2355_tx(struct sprd_chip *chip, struct sprd_msg *msg);
int sc2355_tx_force_exit(struct sprd_chip *chip);
int sc2355_tx_is_exit(struct sprd_chip *chip);
int sc2355_reset(struct sprd_hif *hif);
#ifdef DRV_RESET_SELF
int sc2355_reset_self(struct sprd_priv *priv);
#endif
void sc2355_tx_drop_tcp_msg(struct sprd_chip *chip, struct sprd_msg *msg);
int sc2355_sdio_process_credit(struct sprd_hif *hif, void *data);
int sc2355_set_thread_uclamp(struct task_struct *thread, int sched_util_min);
int sc2355_tx_init(struct sprd_hif *hif);
void sc2355_tx_deinit(struct sprd_hif *hif);
int sprd_tx_filter_packet(struct sk_buff *skb, struct net_device *ndev);
void sc2355_dequeue_data_buf(struct sprd_msg *msg);
void sc2355_dequeue_data_list(struct mbuf_t *head, int num);
void sc2355_free_cmd_buf(struct sprd_msg *msg, struct sprd_msg_list *list);
void sc2355_wake_net_ifneed(struct sprd_hif *hif, struct sprd_msg_list *list,
			    enum sprd_mode mode);
u8 sc2355_fc_set_clor_bit(struct tx_mgmt *tx_mgmt, int num);
void sc2355_handle_tx_status_after_close(struct sprd_vif *vif);
void sc2355_flush_tx_qoslist(struct tx_mgmt *tx_mgmt, int mode, int ac_index,
			     int lut_index);
void sc2355_flush_mode_txlist(struct tx_mgmt *tx_mgmt, enum sprd_mode mode);
void sc2355_flush_tosendlist(struct tx_mgmt *tx_mgmt);
bool sc2355_is_vowifi_pkt(struct sk_buff *skb, bool *b_cmd_path);
void sc2355_dequeue_tofreelist_buf(struct sprd_hif *hif, struct sprd_msg *msg);
void sc2355_tx_flush(struct sprd_hif *hif, struct sprd_vif *vif);
int sprd_tx_special_data(struct sk_buff *skb, struct net_device *ndev);
int sc2355_send_data_offset(void);
int sc2355_send_data(struct sprd_vif *vif, struct sprd_msg *msg,
		     struct sk_buff *skb, u8 type, u8 offset, bool flag);
/* MU300: 7.3 gave dma_capable() the DMA attributes (0: a plain mapping, as this driver's dma_map_single() makes);
 * here and not in tx.c so that tx.c, its lines (the BUG table) and its object code stay as they were before 7.3
 * (FINDINGS 206). The parenthesised name calls the function itself, not this macro.
 */
#include <linux/version.h>
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(7, 3, 0))
#include <linux/dma-direct.h>
#define dma_capable(dev, addr, size, is_ram) (dma_capable)(dev, addr, size, is_ram, 0)
#endif

#endif
