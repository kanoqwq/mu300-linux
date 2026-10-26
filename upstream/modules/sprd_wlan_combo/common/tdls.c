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

#include "cfg80211.h"
#include "chip_ops.h"
#include "cmd.h"
#include "common.h"

int sprd_cfg80211_tdls_mgmt(struct wiphy *wiphy, struct net_device *ndev,
			    const u8 *peer, int link_id, u8 action_code, u8 dialog_token,
			    u16 status_code, u32 peer_capability,
			    bool initiator, const u8 *buf, size_t len)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sk_buff *tdls_skb;
	struct cmd_tdls_mgmt *p;
	u16 datalen, ielen;
	u32 end = 0x1a2b3c4d;
	int ret = 0;

	netdev_info(ndev, "%s action_code=%d(%pM)\n", __func__,
		    action_code, peer);

	datalen = sizeof(*p) + len + sizeof(end);
	ielen = len + sizeof(end);
	tdls_skb = dev_alloc_skb(datalen + NET_IP_ALIGN);
	if (!tdls_skb) {
		wiphy_err(wiphy, "dev_alloc_skb failed\n");
		return -ENOMEM;
	}
	skb_reserve(tdls_skb, NET_IP_ALIGN);
	p = (struct cmd_tdls_mgmt *)skb_put(tdls_skb, offsetof(struct
							       cmd_tdls_mgmt,
							       u));

	ether_addr_copy(p->da, peer);
	ether_addr_copy(p->sa, vif->ndev->dev_addr);
	p->ether_type = cpu_to_be16(ETH_P_TDLS);
	p->payloadtype = WLAN_TDLS_SNAP_RFTYPE;
	switch (action_code) {
	case WLAN_TDLS_SETUP_REQUEST:
		p->category = WLAN_CATEGORY_TDLS;
		p->action_code = WLAN_TDLS_SETUP_REQUEST;
		p = (struct cmd_tdls_mgmt *)skb_put(tdls_skb,
						    (sizeof
						     (p->u.setup_req) + ielen));
		memcpy(p, &dialog_token, 1);
		memcpy((u8 *)p + 1, buf, len);
		memcpy((u8 *)p + 1 + len, &end, sizeof(end));
		break;
	case WLAN_TDLS_SETUP_RESPONSE:
		p->category = WLAN_CATEGORY_TDLS;
		p->action_code = WLAN_TDLS_SETUP_RESPONSE;
		p = (struct cmd_tdls_mgmt *)skb_put(tdls_skb,
						    (sizeof
						     (p->u.setup_resp) +
						     ielen));
		memcpy(p, &status_code, 2);
		memcpy((u8 *)p + 2, &dialog_token, 1);
		memcpy((u8 *)p + 3, buf, len);
		memcpy((u8 *)p + 3 + len, &end, sizeof(end));
		break;
	case WLAN_TDLS_SETUP_CONFIRM:
		p->category = WLAN_CATEGORY_TDLS;
		p->action_code = WLAN_TDLS_SETUP_CONFIRM;
		p = (struct cmd_tdls_mgmt *)skb_put(tdls_skb,
						    (sizeof
						     (p->u.setup_cfm) + ielen));
		memcpy(p, &status_code, 2);
		memcpy((u8 *)p + 2, &dialog_token, 1);
		memcpy((u8 *)p + 3, buf, len);
		memcpy((u8 *)p + 3 + len, &end, sizeof(end));
		break;
	case WLAN_TDLS_TEARDOWN:
		p->category = WLAN_CATEGORY_TDLS;
		p->action_code = WLAN_TDLS_TEARDOWN;
		p = (struct cmd_tdls_mgmt *)skb_put(tdls_skb,
						    (sizeof
						     (p->u.teardown) + ielen));
		memcpy(p, &status_code, 2);
		memcpy((u8 *)p + 2, buf, len);
		memcpy((u8 *)p + 2 + len, &end, sizeof(end));
		break;
	case SPRD_TDLS_DISCOVERY_RESPONSE:
		p->category = WLAN_CATEGORY_TDLS;
		p->action_code = SPRD_TDLS_DISCOVERY_RESPONSE;
		p = (struct cmd_tdls_mgmt *)skb_put(tdls_skb,
						    (sizeof
						     (p->u.discover_resp) +
						     ielen));
		memcpy(p, &dialog_token, 1);
		memcpy((u8 *)p + 1, buf, len);
		memcpy((u8 *)p + 1 + len, &end, sizeof(end));
		break;
	default:
		wiphy_err(wiphy, "invalid action_code %d\n", action_code);
		dev_kfree_skb(tdls_skb);
		return -EINVAL;
	}

	ret = sprd_tdls_mgmt(vif->priv, vif, tdls_skb);
	dev_kfree_skb(tdls_skb);

	return ret;
}

int sprd_cfg80211_tdls_oper(struct wiphy *wiphy, struct net_device *ndev,
			    const u8 *peer, enum nl80211_tdls_operation oper)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s oper=%d\n", __func__, oper);

	if (oper == NL80211_TDLS_ENABLE_LINK)
		oper = SPRD_TDLS_ENABLE_LINK;
	else if (oper == NL80211_TDLS_DISABLE_LINK)
		oper = SPRD_TDLS_DISABLE_LINK;
	else
		netdev_err(ndev, "unsupported this TDLS oper\n");

	return sprd_tdls_oper(vif->priv, vif, peer, oper);
}

int sprd_cfg80211_tdls_chan_switch(struct wiphy *wiphy, struct net_device *ndev,
				   const u8 *addr, u8 oper_class,
				   struct cfg80211_chan_def *chandef)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	u8 chan, band;

	chan = chandef->chan->hw_value;
	band = chandef->chan->band;

	netdev_info(ndev, "%s: chan=%u, band=%u\n", __func__, chan, band);
	return sprd_start_tdls_channel_switch(vif->priv, vif, addr,
					      chan, 0, band);
}

void sprd_cfg80211_tdls_cancel_chan_switch(struct wiphy *wiphy,
					   struct net_device *ndev,
					   const u8 *addr)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);
	sprd_cancel_tdls_channel_switch(vif->priv, vif, addr);
}
