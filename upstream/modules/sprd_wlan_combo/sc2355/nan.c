/*
* SPDX-FileCopyrightText: 2020-2022 Unisoc (Shanghai) Technologies Co., Ltd
* SPDX-License-Identifier: GPL-2.0
*
* Copyright 2020-2022 Unisoc (Shanghai) Technologies Co., Ltd
*
* This program is free software; you can redistribute it and/or modify it
* under the terms of version 2 of the GNU General Public License
* as published by the Free Software Foundation.
*/

#include <linux/limits.h>
#include "common/cfg80211.h"
#include "common/cmd.h"
#include "common/common.h"
#include "common/debug.h"
#include "common/vendor.h"
#include "cmdevt.h"
#include "nan.h"

#define NAN_RSP_LEN	128

int sc2355_nan_vendor_cmds(struct wiphy *wiphy, struct wireless_dev *wdev,
			   const void *data, int len)
{
	struct sprd_msg *msg;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	u8 rsp[NAN_RSP_LEN] = { 0x0 };
	u16 rsp_len = NAN_RSP_LEN;
	int ret = 0;

	/* bug 2028856, hackerone 1701201 */
	if (U16_MAX < len || len <= 0) {
		netdev_err(vif->ndev,
			   "%s: param data len is invalid\n", __func__);
		return -EINVAL;
	}

	msg = get_cmdbuf(vif->priv, vif, len, CMD_NAN);
	if (!msg)
		return -ENOMEM;

	memcpy(msg->data, data, len);
	ret = send_cmd_recv_rsp(vif->priv, msg, rsp, &rsp_len);

	if (!ret && rsp_len)
		sc2355_nan_event(vif, rsp, rsp_len);
	else
		pr_err("%s: ret=%d, rsp_len=%d\n", __func__, ret, rsp_len);

	return ret;
}

/* event handler*/
int sc2355_nan_event(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *skb;

	/* Alloc the skb for vendor event */
	skb = cfg80211_vendor_event_alloc(wiphy, &vif->wdev, NLMSG_HDRLEN + len,
					  SPRD_VENDOR_EVENT_NAN_INDEX,
					  GFP_KERNEL);
	if (!skb) {
		netdev_info(vif->ndev, "skb alloc failed");
		return -ENOMEM;
	}

	/* Push the data to the skb */
	if (nla_put(skb, ATTR_NAN, len, data)) {
		netdev_info(vif->ndev, "nla put failed");
		kfree_skb(skb);
		return -1;
	}

	cfg80211_vendor_event(skb, GFP_KERNEL);

	return 0;
}
