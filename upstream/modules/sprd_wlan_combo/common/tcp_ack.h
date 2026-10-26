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
#ifndef __TCP_ACK_H__
#define __TCP_ACK_H__

#define SPRD_TCP_ACK_NUM	32
#define SPRD_TCP_ACK_DROP_CNT	12
#define SPRD_TCP_ACK_EXIT_VAL	0x800

#define SPRD_ACK_OLD_TIME	4000

extern unsigned int tcp_ack_drop_cnt;

struct sprd_priv;

struct tcp_ack_msg {
	u16 source;
	u16 dest;
	s32 saddr;
	s32 daddr;
	u32 seq;
	u16 win;
};

struct tcp_ack_info {
	int ack_info_num;
	int busy;
	int drop_cnt;
	int psh_flag;
	u32 psh_seq;
	u16 win_scale;
	/* seqlock for ack info */
	seqlock_t seqlock;
	/* protect tcp ack info */
	spinlock_t lock;
	unsigned long last_time;
	unsigned long timeout;
	struct timer_list timer;
	struct sprd_msg *msg;
	struct sprd_msg *in_send_msg;
	struct tcp_ack_msg ack_msg;
};

struct sprd_tcp_ack_manage {
	atomic_t enable;
	int max_num;
	int free_index;
	unsigned long last_time;
	unsigned long timeout;
	unsigned long drop_time;
	atomic_t max_drop_cnt;
	/* lock for tcp ack alloc and free */
	spinlock_t lock;
	struct sprd_priv *priv;
	struct tcp_ack_info ack_info[SPRD_TCP_ACK_NUM];
	/*size in KB */
	unsigned int ack_winsize;
	atomic_t ref;
};

struct sprd_msg *tcp_ack_delay(struct sprd_tcp_ack_manage *ack_m);

#endif
