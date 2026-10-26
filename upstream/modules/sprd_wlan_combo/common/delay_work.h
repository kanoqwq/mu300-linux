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

#ifndef __DELAY_WORK_H__
#define __DELAY_WORK_H__

#include <linux/list.h>
#include <linux/netdevice.h>
#include <linux/spinlock.h>
#include <linux/types.h>

#include "iface.h"
#include "hif.h"

#define SPRD_WORK_NONE				0
#define SPRD_WORK_REG_MGMT			1
#define SPRD_WORK_DEAUTH			2
#define SPRD_WORK_DISASSOC			3
#define SPRD_WORK_MC_FILTER			4
#define SPRD_WORK_NOTIFY_IP			5
#define SPRD_WORK_BA_MGMT			6
#define SPRD_WORK_ADDBA				7
#define SPRD_WORK_DELBA				8
#define SPRD_ASSERT				10
#define SPRD_HANG_RECEIVED			11
#define SPRD_POP_MBUF				12
#define SPRD_TDLS_CMD				13
#define SPRD_P2P_GO_DEL_STATION			14
#define SPRD_SEND_CLOSE				15
#define SPRD_CMD_TX_DATA			16
#define SPRD_WORK_FW_PWR_DOWN			17
#define SPRD_WORK_HOST_WAKEUP_FW		18
#define SPRD_WORK_VOWIFI_DATA_PROTECTION	19
#define SPRD_PCIE_RX_ALLOC_BUF			20
#define SPRD_PCIE_RX_FLUSH_BUF			21
#define SPRD_PCIE_TX_MOVE_BUF			22
#define SPRD_PCIE_TX_FREE_BUF			23
#define SPRD_WORK_FRESH_BO			24
#define SPRD_WORK_ADAPTIVE			26

struct sprd_work {
	struct list_head list;
	struct sprd_vif *vif;
	enum sprd_hif_type hw_type;
	u8 id;
	u32 len;
	u8 data[0];
};

struct sprd_reg_mgmt {
	u16 type;
	bool reg;
};

struct sprd_data2mgmt {
	struct sk_buff *skb;
	struct net_device *ndev;
};

struct sprd_tdls_work {
	u8 vif_ctx_id;
	u8 peer[ETH_ALEN];
	int oper;
};

struct sprd_work *sprd_alloc_work(int len);
void sprd_queue_work(struct sprd_priv *priv, struct sprd_work *sprd_work);
void sprd_cancel_work(struct sprd_priv *priv, struct sprd_vif *vif);
void sprd_clean_work(struct sprd_priv *priv);

#endif
