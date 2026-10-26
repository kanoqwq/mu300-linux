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

#ifndef __TDLS_H__
#define __TDLS_H__

#include <net/cfg80211.h>
#include "iface.h"

#define SPRD_TDLS_ENABLE_LINK		11
#define SPRD_TDLS_DISABLE_LINK		12
#define SPRD_TDLS_TEARDOWN		3
#define SPRD_TDLS_DISCOVERY_RESPONSE	14
#define SPRD_TDLS_START_CHANNEL_SWITCH	13
#define SPRD_TDLS_CANCEL_CHANNEL_SWITCH	14
#define SPRD_TDLS_CMD_CONNECT		16

struct cmd_tdls_mgmt {
	u8 da[ETH_ALEN];
	u8 sa[ETH_ALEN];
	__le16 ether_type;
	u8 payloadtype;
	u8 category;
	u8 action_code;
	union {
		struct {
			u8 dialog_token;
		} __packed setup_req;
		struct {
			__le16 status_code;
			u8 dialog_token;
		} __packed setup_resp;
		struct {
			__le16 status_code;
			u8 dialog_token;
		} __packed setup_cfm;
		struct {
			__le16 reason_code;
		} __packed teardown;
		struct {
			u8 dialog_token;
		} __packed discover_resp;
	} u;
	__le32 len;
	u8 frame[0];
} __packed;

struct tdls_update_peer_infor {
	u8 tdls_cmd_type;
	u8 da[ETH_ALEN];
	u8 valid;
	u8 timer;
	u8 rsvd;
	u16 txrx_len;
} __packed;

struct tdls_flow_count_para {
	u8 valid;
	u8 da[ETH_ALEN];
	/* seconds */
	/* u8 timer; */
	u16 threshold;		/*KBytes */
	u32 data_len_counted;	/*bytes */
	u32 start_mstime;	/*ms */
	u8 timer;		/*seconds */
};

int sprd_cfg80211_tdls_mgmt(struct wiphy *wiphy, struct net_device *ndev,
			    const u8 *peer, int link_id, u8 action_code, u8 dialog_token,
			    u16 status_code, u32 peer_capability,
			    bool initiator, const u8 *buf, size_t len);
int sprd_cfg80211_tdls_oper(struct wiphy *wiphy, struct net_device *ndev,
			    const u8 *peer, enum nl80211_tdls_operation oper);
int sprd_cfg80211_tdls_chan_switch(struct wiphy *wiphy, struct net_device *ndev,
				   const u8 *addr, u8 oper_class,
				   struct cfg80211_chan_def *chandef);
void sprd_cfg80211_tdls_cancel_chan_switch(struct wiphy *wiphy,
					   struct net_device *ndev,
					   const u8 *addr);

void sprd_report_tdls(struct sprd_vif *vif, const u8 *peer, u8 oper,
		      u16 reason_code);

#endif
