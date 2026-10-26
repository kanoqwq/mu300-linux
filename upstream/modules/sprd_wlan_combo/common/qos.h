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

#ifndef __COMMON_QOS_H__
#define __COMMON_QOS_H__

#define SPRD_QOS_NUM	5
#define MAX_LUT_NUM	32

struct sprd_priv;
struct sprd_msg_list;
struct sprd_msg;

struct sprd_qos_peer_list {
	struct list_head head_list;
	spinlock_t p_lock;	/*peer list lock */
	atomic_t l_num;
};

struct qos_list {
	struct sprd_msg *head;
	struct sprd_msg *tail;
	struct sprd_qos_peer_list p_list[MAX_LUT_NUM];
};

struct sprd_qos_t {
	struct list_head *head;
	struct list_head *last_node;
	struct sprd_msg_list *txlist;
	int enable;
	int change;
	int num_wrap;
	int txnum;
	/* 0 for not qos data, [1, 4] for qos data */
	int cur_weight[SPRD_QOS_NUM];
	int num[SPRD_QOS_NUM];
	struct qos_list list[SPRD_QOS_NUM];
};

#endif
