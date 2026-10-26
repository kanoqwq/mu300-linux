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

#ifndef __RX_H__
#define __RX_H__

#include "defrag.h"
#include "mm.h"
#include "reorder.h"
#include "sdio.h"
#include "pcie.h"

#define SPRD_MAC_INDEX_MAX	4

#define MAX_SEQNO_BY_TWO	2048
#define SEQNO_MASK		0xfff
#define SEQNO_ADD(seq1, seq2)	(((seq1) + (seq2)) & SEQNO_MASK)
#define SEQNO_SUB(seq1, seq2)	(((seq1) - (seq2)) & SEQNO_MASK)

#define SPRD_GET_FIRST_SKB(skb, list) {\
	skb = list;\
	list = skb->next;\
	skb->next = NULL;\
}

enum seqno_bound {
	LESSER_THAN_SEQLO = 0,
	GREATER_THAN_SEQHI,
	BETWEEN_SEQLO_SEQHI,
};

enum cipher_type {
	SPRD_HW_WEP = 0,
	SPRD_HW_TKIP,
	SPRD_HW_CCMP,
	SPRD_HW_NO_CIPHER,
	SPRD_HW_WEP_104,
	SPRD_HW_GCMP_128,
	SPRD_HW_GCMP_256,
	SPRD_HW_WAPI,
	SPRD_HW_CCMP_256,
	SPRD_HW_BIP_CMAC_128,
	SPRD_HW_BIP_CMAC_256,
	SPRD_HW_BIP_GMAC_128,
	SPRD_HW_BIP_GMAC_256,
};

struct rx_mgmt {
	struct sprd_hif *hif;

	struct sprd_msg_list rx_list;

	struct work_struct rx_work;
	struct workqueue_struct *rx_queue;

	struct mem_mgmt mm_entry;
	struct rx_ba_entry ba_entry;
	struct rx_defrag_entry defrag_entry;
	u8 rsp_event_cnt;
	void *addr_trans_head;
	void *addr_trans_tail;
	int addr_trans_num;

	struct work_struct rx_net_work;
	struct workqueue_struct *rx_net_workq;

	struct completion rx_completed;
	unsigned long rx_total_len;
	ktime_t rxtimebegin;
	ktime_t rxtimeend;

	int rx_chn;
	u64 rx_handle_ns;
	u64 rx_queue_ns;

	u8 rx_snaphdr_flag;
	u16 rx_snaphdr_seqnum;
	u8 rx_snaphdr_lut;
	u8 rx_snaphdr_tid;

	/* MU300: the RX refill's retry after an attempt that got nowhere (see sc2355_mm_fill_buffer) */
	struct delayed_work refill_retry;
	unsigned int refill_backoff_ms;
	/*
	 * MU300 (kanoqwq's clean-tf-7.2 cf4dcd61): a refill refused by a full EDMA pending ring (-EAGAIN) waits for
	 * the bus to complete a transfer on the data channel (sc2355_rx_bus_complete) rather than for the whole
	 * back-off. The lock orders that completion (IRQ) against arming the timer and against teardown.
	 */
	spinlock_t refill_lock;
	bool refill_bus_waiting;
	bool refill_bus_ready;
	/* MU300: set by sc2355_rx_deinit(); nothing queues or times another refill after it */
	bool stopping;
};

/* NOTE: MUST not modify, defined by HW */
/* It still change now */
struct rx_msdu_desc {
	/* WORD7 */
	u32 host_type:4;	/* indicate data/event/rsp, host driver used */
	u32 ctx_id:4;		/* indicate hw mac address index */
	u32 msdu_offset:8;	/* 802.3 header offset from msdu_dscr_header */
	u32 msdu_len:16;	/* len of 802.3 frame */
	/* WORD8 */
	u32 curr_buff_base_addr_l;	/* base buffer addr of this msdu
					 * low 32 bit
					 */
	/* WORD9 */
	union {
		u8 curr_buff_base_addr_h;	/* base buffer addr of
						 * this msdu high 8 bit
						 */
		u8 short_pkt_num;	/* sw use, used in short
					 * pkt process in SDIO mode
					 */
	};
	u8 msdu_index_of_mpdu;		/* msdu index of mpdu */
	u16 first_msdu_of_buff:1;	/* indicate whether this msdu is
					 * the first msdu in buffer
					 */
	u16 last_msdu_of_buff:1;	/* indicate whether this msdu is
					 * the last msdu in buffer
					 */
	u16 rsvd1:2;			/* reserved */
	u16 first_msdu_of_mpdu:1;	/* indicate whether this msdu is
					 * the first msdu of mpdu
					 */
	u16 last_msdu_of_mpdu:1;	/* indicate whether this msdu is
					 * the last msdu of mpdu
					 */
	u16 null_frame_flag:1;		/* indicate this msdu is null */
	u16 qos_null_frame_flag:1;	/* indicate this msdu is qos null */
	u16 first_buff_of_mpdu:1;	/* indicate whether the buffer this msdu
					 * is the first buff of mpdu
					 */
	u16 last_buff_of_mpdu:1;	/* indicate whether the buffer this msdu
					 * is the last buff of mpdu
					 */
	u16 sta_lut_valid:1;	/* indicate if find hw sta lut */
	u16 sta_lut_index:5;	/* hw sta lut index, valid only
				 * when sta_lut_valid is true
				 */
	/* WORD 10 */
	u32 more_data_bit:1;	/* more data bit in mac header */
	u32 eosp_bit:1;		/* eosp bit in mac header */
	u32 pm_bit:1;		/* pm bit in mac header */
	u32 bc_mc_w2w_flag:1;	/* bc/mc wlan2wlan flag */
	u32 bc_mc_flag:1;	/* bc/mc flag */
	u32 uc_w2w_flag:1;	/* uc wlan2wlan flag */
	u32 eapol_flag:1;	/* eapol flag */
	u32 vlan_type_flag:1;	/* vlan pkt */
	u32 snap_hdr_present:1;	/* indicate if hw find snap header
				 * (0xAA 0xAA 0x03 0x00 0x00 0x00)
				 * (0xAA 0xAA 0x03 0x00 0x00 0xFB)
				 */
	u32 snap_hdr_type:1;	/* snap header type: rfc1042/rfc896(802.1h) */
	u32 ba_session_flag:1;	/* indicate if this msdu is
				 * received in rx ba session period
				 */
	u32 ampdu_flag:1;	/* indicate if this msdu is in ampdu */
	u32 amsdu_flag:1;	/* indicate if this msdu is in amsdu */
	u32 qos_flag:1;		/* qos flag */
	u32 rsvd2:2;		/* reserved */
	u32 tid:4;		/* TID */
	u32 seq_num:12;		/* sequence number */
	/* WORD11 */
	u32 pn_l;		/* PN, low 4 bytes, hw has got real PN value */
	/* WORD12 */
	u32 pn_h:16;		/* PN, high 2 bytes */
	u32 frag_num:4;		/* fragment number in mac header */
	u32 more_frag_bit:1;	/* more fragment bit in mac header */
	u32 retry_bit:1;	/* retransmission bit in mac header */
	u32 rsvd3:2;		/* reserved */
	u32 cipher_type:4;	/* cipher type */
	u32 rsvd4:3;		/* reserved */
	u32 data_write_done:1;	/* in PCIE mode, indicate if data has been
				 * transferred from HW to ap, host driver use
				 */
	/* WORD13 */
	u32 rsvd5;		/* reserved */
} __packed;

struct rx_mh_desc {
	/* WORD0 */
	u32 next_msdu_ptr_l;	/* ptr to next msdu low 32 bit */
	/* WORD1 */
	u32 next_msdu_ptr_h:8;	/* ptr to next msdu high 8 bit */
	u32 transfer_len:16;	/* SDIO HW use */
	u32 offset_for_sdio:8;	/* SDIO HW use, default:0 */
	/* WORD2 */
	u32 tcp_checksum_offset:12;	/* HW use */
	u32 tcp_checksum_len:16;	/* HW use */
	u32 tcp_checksum_en:1;	/* HW use */
	u32 rsvd1:3;		/* reserved */
	/* WORD3 */
	u32 tcp_hw_checksum:16;	/* MAC HW fill, host driver use */
	u32 last_procq_msdu_of_buff:1;	/* indicate whether this msdu
					 * is the last procq msdu in buffer
					 */
	u32 rsvd2:7;		/* reserved */
	u32 filter_status:6;	/* used in filter queue */
	u32 msdu_sta_ps_flag:1;	/* indicate if this msdu is received
				 * in STA ps state
				 */
	u32 filter_flag:1;	/* indicate if this msdu is
				 * a filter msdu
				 */
	/* WORD4 */
	u32 data_rate:8;	/* data rate from PA RX DESCRIPTOR */
	u32 rss1:8;		/* RSS1 from PA RX DESCRIPTOR */
	u32 rss2:8;		/* RSS2 from PA RX DESCRIPTOR */
	u32 snr1:8;		/* SNR1 from PA RX DESCRIPTOR */
	/* WORD5 */
	u32 snr2:8;		/* SNR2 from PA RX DESCRIPTOR */
	u32 snr_combo:8;	/* SNR-COMBO from PA RX DESCRIPTOR */
	u32 snr_l:8;		/* SNR-L from PA RX DESCRIPTOR */
	u32 rsvd3:8;		/* reserved */
	/* WORD6 */
	u32 phy_rx_mode;	/* PHY RX MODE from PA RX DESCRIPTOR */
} __packed;

static inline int msdu_total_len(struct rx_msdu_desc *msdu_desc)
{
	return msdu_desc->msdu_offset + msdu_desc->msdu_len;
}

static inline bool seqno_leq(unsigned short seq1, unsigned short seq2)
{
	bool ret = false;

	if ((seq1 <= seq2 && ((seq2 - seq1) < MAX_SEQNO_BY_TWO)) ||
	    (seq1 > seq2 && ((seq1 - seq2) >= MAX_SEQNO_BY_TWO)))
		ret = true;
	return ret;
}

static inline bool seqno_geq(unsigned short seq1, unsigned short seq2)
{
	return seqno_leq(seq2, seq1);
}

static inline unsigned short rx_data_process(struct sprd_priv *priv,
				      unsigned char *msg)
{
	return 0;
}

int sc2355_fill_skb_csum(struct sk_buff *skb, unsigned short csum);
void sc2355_rx_process(struct rx_mgmt *rx_mgmt, struct sk_buff *pskb);
void sc2355_rx_send_cmd(struct sprd_hif *hif, void *data, int len,
			unsigned char id, unsigned char ctx_id);
void sc2355_queue_rx_buff_work(struct sprd_priv *priv, unsigned char id);
int sc2355_mm_fill_buffer(struct sprd_hif *intf);
/* MU300: the PCIe data-channel completion; the caller holds rcu_read_lock() across its hif->rx_mgmt lookup */
void sc2355_rx_bus_complete(struct sprd_hif *hif);
void sc2355_mm_fill_all_buffer(void *intf);
void sc2355_rx_flush_buffer(void *intf);
void sc2355_rx_up(struct rx_mgmt *rx_mgmt);
int sc2355_rx_init(struct sprd_hif *hif);
int sc2355_rx_deinit(struct sprd_hif *hif);
void sc2355_rx_mh_addr_process(struct rx_mgmt *rx_mgmt, void *data,
		   int len, int buffer_type);
void sc2355_count_rx_tp(struct sprd_hif *hif, int len);
void sc2355_sdio_rx_throughput_statistic(unsigned int len);

#endif
