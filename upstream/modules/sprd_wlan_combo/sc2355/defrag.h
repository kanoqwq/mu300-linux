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

#ifndef __DEFRAG__H__
#define __DEFRAG__H__

#include <linux/skbuff.h>
#include "common/iface.h"
#include "common/common.h"

#define MAX_DEFRAG_NUM 3

struct rx_defrag_desc {
	unsigned char sta_lut_index;
	unsigned char tid;
	unsigned char frag_num;
	unsigned short seq_num;
};

struct rx_defrag_node {
	struct list_head list;
	struct rx_defrag_desc desc;
	struct sk_buff_head skb_list;
	unsigned int msdu_len;
	unsigned char last_frag_num;
};

struct rx_defrag_entry {
	struct list_head list;
	struct sk_buff *skb_head;
	struct sk_buff *skb_last;
};

int sc2355_defrag_init(struct rx_defrag_entry *defrag_entry);
void sc2355_defrag_deinit(struct rx_defrag_entry *defrag_entry);
struct sk_buff *sc2355_defrag_data_process(struct rx_defrag_entry *defrag_entry,
					   struct sk_buff *pskb);
void sc2355_defrag_recover(struct sprd_vif *vif);

#endif
