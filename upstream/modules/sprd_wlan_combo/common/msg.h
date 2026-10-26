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

#ifndef __MSG_H__
#define __MSG_H__

#include <asm/byteorder.h>
#include <linux/list.h>
#include <linux/spinlock.h>
#include <linux/types.h>
#include <linux/version.h>

#define SPRD_DATA_TYPE_NORMAL			0
#define SPRD_MSG_EXIT_VAL			0x8000

#define SPRD_CMD_STATUS_OK			0
#define SPRD_CMD_STATUS_ARG_ERROR		-1
#define SPRD_CMD_STATUS_GET_RESULT_ERROR	-2
#define SPRD_CMD_STATUS_EXEC_ERROR		-3
#define SPRD_CMD_STATUS_MALLOC_ERROR		-4
#define SPRD_CMD_STATUS_WIFIMODE_ERROR		-5
#define SPRD_CMD_STATUS_ERROR			-6
#define SPRD_CMD_STATUS_CONNOT_EXEC_ERROR	-7
#define SPRD_CMD_STATUS_NOT_SUPPORT_ERROR	-8
#define SPRD_CMD_STATUS_CRC_ERROR		-9
#define SPRD_CMD_STATUS_INI_INDEX_ERROR		-10
#define SPRD_CMD_STATUS_LENGTH_ERROR		-11
#define SPRD_CMD_STATUS_OTHER_ERROR		-127

#define WAPI_PN_SIZE				16
#define SPRD_DATA_OFFSET			2

/* bit[7][6][5] type: 0 for normal data, 1 for wapi data
 * bit[4][3][2][1][0] offset: the ETH data after this struct address
 */
#define SPRD_DATA_TYPE_NORMAL			0
#define SPRD_DATA_TYPE_WAPI			(0x1 << 5)
#define SPRD_DATA_TYPE_MGMT			(0x2 << 5)
#define SPRD_DATA_TYPE_ROUTE			(0x3 << 5)
#define SPRD_DATA_TYPE_MAX			SPRD_DATA_TYPE_ROUTE
#define SPRD_GET_DATA_TYPE(info)		((info) & 0xe0)
#define SPRD_DATA_OFFSET_MASK			0x1f

/* 0 for cmd, 1 for event, 2 for data, 3 for mh data */
enum sprd_head_type {
	SPRD_TYPE_CMD,
	SPRD_TYPE_EVENT,
	SPRD_TYPE_DATA,
	SPRD_TYPE_DATA_SPECIAL,
	SPRD_TYPE_DATA_PCIE_ADDR,
	SPRD_TYPE_PKT_LOG,
};

enum sprd_head_rsp {
	/* cmd need no rsp */
	SPRD_HEAD_NORSP,
	/* cmd need rsp */
	SPRD_HEAD_RSP,
};

/* bit[7][6][5] ctx_id: context id
 * bit[4] rsp: sprd_head_rsp
 * bit[3] reserv
 * bit[2][1][0] type: sprd_head_type
 */
struct sprd_common_hdr {
#if defined(__LITTLE_ENDIAN_BITFIELD)
	__u8 type:3;
	__u8 reserv:1;
	__u8 rsp:1;
	__u8 mode:3;
#elif defined(__BIG_ENDIAN_BITFIELD)
	__u8 mode:3;
	__u8 rsp:1;
	__u8 reserv:1;
	__u8 type:3;
#else
#error  "check <asm/byteorder.h> defines"
#endif
};

#define SPRD_HEAD_GET_TYPE(common) \
	(((struct sprd_common_hdr *)(common))->type)

#define SPRD_HEAD_GET_CTX_ID(common) \
	(((struct sprd_common_hdr *)(common))->ctx_id)

#define SPRD_HEAD_GET_RESUME_BIT(common) \
	(((struct sprd_common_hdr *)(common))->reserv)

struct sprd_cmd_hdr {
	struct sprd_common_hdr common;
	u8 cmd_id;
	/* the payload len include the size of this struct */
	__le16 plen;
	__le32 mstime;
	s8 status;
	u8 rsp_cnt;
	u8 reserv[2];
	u8 paydata[0];
} __packed;

struct sprd_addr_hdr {
	struct sprd_common_hdr common;
	u8 paydata[0];
} __packed;

#define SPRD_GET_CMD_PAYDATA(msg) \
	    (((struct sprd_cmd_hdr *)((msg)->skb->data))->paydata)

struct sprd_data_hdr {
	struct sprd_common_hdr common;
	u8 info1;		/*no used in marlin3 */
	/* the payload len include the size of this struct */
	__le16 plen;
	/* the flow contrl shared by sta and p2p */
	u8 flow0;
	/* the sta flow contrl */
	u8 flow1;
	/* the p2p flow contrl */
	u8 flow2;
	/* flow3 0: share, 1: self */
	u8 flow3;
} __packed;

struct sprd_pktlog_hdr {
	struct sprd_common_hdr common;
	u8 rsvd;
	/* the payload len include the size of this struct */
	__le16 plen;
} __packed;

struct sprd_msg_list {
	struct list_head freelist;
	struct list_head busylist;
	/* cmd to be free list */
	struct list_head cmd_to_free;
	int maxnum;
	/* freelist lock */
	spinlock_t freelock;
	/* busylist lock */
	spinlock_t busylock;
	/* cmd_to_free lock */
	spinlock_t complock;
	atomic_t ref;
	/* data flow contrl */
	atomic_t flow;
};

struct sprd_xmit_msg_list {
	/* merge qos queues to this list */
	struct list_head to_send_list;
	/* data list sending by HIF, will be freed later */
	struct list_head to_free_list;
	/* protect send_lock */
	spinlock_t send_lock;
	/* protect free_lock */
	spinlock_t free_lock;
	u8 mode;
	unsigned long failcount;
	atomic_t free_num;
};

struct sprd_msg {
	struct list_head list;
	struct sk_buff *skb;
	/* data just tx cmd use,not include the head */
	void *data;
	void *tran_data;
	void *node;
	unsigned long pcie_addr;
	u8 type;
	u8 mode;
	u16 len;
	unsigned long timeout;
	/* marlin 2 */
	unsigned int fifo_id;
	struct sprd_msg_list *msglist;
	/* marlin 3 */
	unsigned char buffer_type;
	struct sprd_qos_peer_list *data_list;
	struct sprd_xmit_msg_list *xmit_msg_list;
	unsigned char msg_type;
#if defined(MORE_DEBUG)
	unsigned long tx_start_time;
#endif
	unsigned long last_time;
	struct sprd_msg *next;
	/* qos queue index */
	int index;
};

static inline void sprd_fill_msg(struct sprd_msg *msg,
				 struct sk_buff *skb, void *data, u16 len)
{
	msg->skb = skb;
	msg->tran_data = data;
	msg->len = len;
}

static inline int sprd_msg_tx_pended(struct sprd_msg_list *msglist)
{
	return !list_empty(&msglist->busylist);
}

int sprd_init_msg(int num, struct sprd_msg_list *list);
void sprd_deinit_msg(struct sprd_msg_list *list);
struct sprd_msg *sprd_alloc_msg(struct sprd_msg_list *list);
void sprd_free_msg(struct sprd_msg *msg, struct sprd_msg_list *list);
void sprd_queue_msg(struct sprd_msg *msg, struct sprd_msg_list *list);
struct sprd_msg *sprd_peek_msg(struct sprd_msg_list *list);
void sprd_dequeue_msg(struct sprd_msg *msg, struct sprd_msg_list *list);
#endif
