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

#include <linux/limits.h>
#include "common/common.h"
#include "common/iface.h"
#include "rtt.h"
#include "cmdevt.h"

/* FTM session ID we use with FW */
#define RTT_ESSION_ID			1

/* fixed spare allocation we reserve in NL messages we allocate */
#define RTT_NL_EXTRA_ALLOC		32

/* approx maximum length for FTM_MEAS_RESULT NL80211 event */
#define RTT_MEAS_RESULT_MAX_LENGTH	2048

/* maximum number of allowed FTM measurements per burst */
#define RTT_MAX_MEAS_PER_BURST		31

/* initial token to use on non-secure FTM measurement */
#define RTT_DEFAULT_INITIAL_TOKEN	2

#define RTT_MAX_LCI_LENGTH		(240)
#define RTT_MAX_LCR_LENGTH		(240)

/* max rtt cmd response length */
#define RTT_RSP_LEN			(128)

#ifndef BITS_PER_LONG
#ifdef CONFIG_64BIT
#define BITS_PER_LONG			64
#else
#define BITS_PER_LONG			32
#endif /* CONFIG_64BIT */
#endif

enum rtt_session_start_flags {
	FTM_SESSION_START_FLAG_SECURED = 0x1,
	FTM_SESSION_START_FLAG_ASAP = 0x2,
	FTM_SESSION_START_FLAG_LCI_REQ = 0x4,
	FTM_SESSION_START_FLAG_LCR_REQ = 0x8,
};

enum rtt_subcmd {
	RTT_ENABLE,
	RTT_DISABLE,
	RTT_GET_CAPABILITIES,
	RTT_RANGE_REQUEST,
	RTT_RANGE_CANCEL,
	RTT_SET_CLI,
	RTT_SET_CLR,
	RTT_GET_RESPONDER_INFO,
	RTT_ENABLE_RESPONDER,
	RTT_DISABLE_RESPONDER,
};

enum rtt_subevt {
	RTT_SESSION_END,
	RTT_PER_DEST_RES,
};

/* Responder FTM Results */
struct rtt_responder_res {
	u8 t1[6];
	u8 t2[6];
	u8 t3[6];
	u8 t4[6];
	__le16 tod_err;
	__le16 toa_err;
	__le16 tod_err_initiator;
	__le16 toa_err_initiator;
} __packed;

enum rtt_per_dest_res_status {
	FTM_PER_DEST_RES_NO_ERROR = 0x00,
	FTM_PER_DEST_RES_TX_RX_FAIL = 0x01,
	FTM_PER_DEST_RES_PARAM_DONT_MATCH = 0x02,
};

enum rtt_per_dest_res_flags {
	FTM_PER_DEST_RES_REQ_START = 0x01,
	FTM_PER_DEST_RES_BURST_REPORT_END = 0x02,
	FTM_PER_DEST_RES_REQ_END = 0x04,
	FTM_PER_DEST_RES_PARAM_UPDATE = 0x08,
};

struct rtt_per_dest_res {
	/* FTM session ID */
	__le32 session_id;
	/* destination MAC address */
	u8 dst_mac[ETH_ALEN];
	/* wmi_tof_ftm_per_dest_res_flags_e */
	u8 flags;
	/* wmi_tof_ftm_per_dest_res_status_e */
	u8 status;
	/* responder ASAP */
	u8 responder_asap;
	/* responder number of FTM per burst */
	u8 responder_num_ftm_per_burst;
	/* responder number of FTM burst exponent */
	u8 responder_num_ftm_bursts_exp;
	/* responder burst duration ,wmi_tof_burst_duration_e */
	u8 responder_burst_duration;
	/* responder burst period, indicate interval between two consecutive
	 * burst instances, in units of 100 ms
	 */
	__le16 responder_burst_period;
	/* receive burst counter */
	__le16 bursts_cnt;
	/* tsf of responder start burst */
	__le32 tsf_sync;
	/* actual received ftm per burst */
	u8 actual_ftm_per_burst;
	u8 reserved0[7];
	struct rtt_responder_res responder_ftm_res[0];
} __packed;

struct rtt_dest_info {
	u8 channel;
	u8 flags;
	u8 initial_token;
	u8 num_of_ftm_per_burst;
	u8 num_of_bursts_exp;
	u8 burst_duration;
	/* Burst Period indicate interval between two consecutive burst
	 * instances, in units of 100 ms
	 */
	__le16 burst_period;
	u8 dst_mac[ETH_ALEN];
	__le16 reserved;
} __packed;

struct rtt_session_start {
	__le32 session_id;
	u8 num_of_aoa_measures;
	u8 aoa_type;
	__le16 num_of_dest;
	u8 reserved[4];
	struct rtt_dest_info dest_info[0];
} __packed;

struct cmd_rtt {
	u8 sub_cmd;
	__le16 len;
	u8 data[0];
} __packed;

static const struct
nla_policy nl80211_loc_policy[SPRD_ATTR_LOC_MAX + 1] = {
	[SPRD_ATTR_RTT_SESSION_COOKIE] = {.type = NLA_U64},
	[SPRD_ATTR_LOC_CAPA] = {.type = NLA_NESTED},
	[SPRD_ATTR_RTT_MEAS_PEERS] = {.type = NLA_NESTED},
	[SPRD_ATTR_RTT_MEAS_PEER_RESULTS] = {.type = NLA_NESTED},
	[SPRD_ATTR_RTT_RESPONDER_ENABLE] = {.type = NLA_FLAG},
	[SPRD_ATTR_LOC_SESSION_STATUS] = {.type = NLA_U32},
	[SPRD_ATTR_RTT_INITIAL_TOKEN] = {.type = NLA_U8},
	[SPRD_ATTR_AOA_TYPE] = {.type = NLA_U32},
	[SPRD_ATTR_LOC_ANTENNA_ARRAY_MASK] = {.type = NLA_U32},
	[SPRD_ATTR_FREQ] = {.type = NLA_U32},
};

static const struct
nla_policy nl80211_rtt_peer_policy[SPRD_ATTR_RTT_PEER_MAX + 1] = {
	[SPRD_ATTR_RTT_PEER_MAC_ADDR] = {.len = ETH_ALEN},
	[SPRD_ATTR_RTT_PEER_MEAS_FLAGS] = {.type = NLA_U32},
	[SPRD_ATTR_RTT_PEER_MEAS_PARAMS] = {.type = NLA_NESTED},
	[SPRD_ATTR_RTT_PEER_SECURE_TOKEN_ID] = {.type = NLA_U8},
	[SPRD_ATTR_RTT_PEER_FREQ] = {.type = NLA_U32},
};

static const struct
nla_policy nl80211_rtt_meas_param_policy[SPRD_ATTR_RTT_PARAM_MAX + 1] = {
	[SPRD_ATTR_RTT_PARAM_MEAS_PER_BURST] = {.type = NLA_U8},
	[SPRD_ATTR_RTT_PARAM_NUM_BURSTS_EXP] = {.type = NLA_U8},
	[SPRD_ATTR_RTT_PARAM_BURST_DURATION] = {.type = NLA_U8},
	[SPRD_ATTR_RTT_PARAM_BURST_PERIOD] = {.type = NLA_U16},
};

static const struct nla_policy rtt_policy[SPRD_RTT_ATTRIBUTE_MAX + 1] = {
	[SPRD_RTT_ATTRIBUTE_TARGET_MAC] = {.len = ETH_ALEN},
	[SPRD_RTT_ATTRIBUTE_TARGET_TYPE] = {.type = NLA_U8},
	[SPRD_RTT_ATTRIBUTE_TARGET_PEER] = {.type = NLA_U8},
	[SPRD_RTT_ATTRIBUTE_TARGET_CHAN] = {.len =
					    sizeof(struct wifi_channel_info)},
	[SPRD_RTT_ATTRIBUTE_TARGET_PERIOD] = {.type = NLA_U32},
	[SPRD_RTT_ATTRIBUTE_TARGET_NUM_BURST] = {.type = NLA_U32},
	[SPRD_RTT_ATTRIBUTE_TARGET_NUM_FTM_BURST] = {.type = NLA_U32},
	[SPRD_RTT_ATTRIBUTE_TARGET_NUM_RETRY_FTM] = {.type = NLA_U32},
	[SPRD_RTT_ATTRIBUTE_TARGET_NUM_RETRY_FTMR] = {.type = NLA_U32},
	[SPRD_RTT_ATTRIBUTE_TARGET_LCI] = {.type = NLA_U8},
	[SPRD_RTT_ATTRIBUTE_TARGET_LCR] = {.type = NLA_U8},
	[SPRD_RTT_ATTRIBUTE_TARGET_BURST_DURATION] = {.type = NLA_U32},
	[SPRD_RTT_ATTRIBUTE_TARGET_PREAMBLE] = {.type = NLA_U8},
	[SPRD_RTT_ATTRIBUTE_TARGET_BW] = {.type = NLA_U8},
	[SPRD_RTT_ATTRIBUTE_TARGET_RESPONDER_INFO] = {.type = NLA_U32}

};

static u8 rtt_get_channel(struct wiphy *wiphy, const u8 *mac_addr, u32 freq)
{
	struct cfg80211_bss *bss;
	struct ieee80211_channel *chan;
	u8 channel;

	if (freq) {
		chan = ieee80211_get_channel(wiphy, freq);
		if (!chan) {
			pr_err("invalid freq: %d\n", freq);
			return 0;
		}
		channel = chan->hw_value;
	} else {
		bss = cfg80211_get_bss(wiphy, NULL, mac_addr,
				       NULL, 0, WLAN_CAPABILITY_ESS,
				       WLAN_CAPABILITY_ESS);
		if (!bss) {
			pr_err("Unable to find BSS\n");
			return 0;
		}
		channel = bss->channel->hw_value;
		cfg80211_put_bss(wiphy, bss);
	}

	pr_info("target %pM at channel %d\n", mac_addr, channel);
	return channel;
}

static int rtt_parse_meas_params(struct sprd_vif *vif,
				 struct nlattr *attr,
				 struct rtt_meas_params *params)
{
	struct nlattr *tb[SPRD_ATTR_RTT_PARAM_MAX + 1];
	int rc;

	if (!attr) {
		/* temporary defaults for one-shot measurement */
		params->meas_per_burst = 1;
		/* 500 milliseconds */
		params->burst_period = 5;
		return 0;
	}
	rc = nla_parse_nested(tb, SPRD_ATTR_RTT_PARAM_MAX,
			      attr, nl80211_rtt_meas_param_policy, NULL);
	if (rc) {
		netdev_err(vif->ndev,
			   "%s: invalid measurement params\n", __func__);
		return rc;
	}
	if (tb[SPRD_ATTR_RTT_PARAM_MEAS_PER_BURST])
		params->meas_per_burst =
		    nla_get_u8(tb[SPRD_ATTR_RTT_PARAM_MEAS_PER_BURST]);
	if (tb[SPRD_ATTR_RTT_PARAM_NUM_BURSTS_EXP])
		params->num_of_bursts_exp =
		    nla_get_u8(tb[SPRD_ATTR_RTT_PARAM_NUM_BURSTS_EXP]);
	if (tb[SPRD_ATTR_RTT_PARAM_BURST_DURATION])
		params->burst_duration =
		    nla_get_u8(tb[SPRD_ATTR_RTT_PARAM_BURST_DURATION]);
	if (tb[SPRD_ATTR_RTT_PARAM_BURST_PERIOD])
		params->burst_period =
		    nla_get_u16(tb[SPRD_ATTR_RTT_PARAM_BURST_PERIOD]);
	return 0;
}

static int
rtt_validate_meas_params(struct sprd_vif *vif, struct rtt_meas_params *params)
{
	if (params->meas_per_burst > RTT_MAX_MEAS_PER_BURST ||
	    params->num_of_bursts_exp != 0) {
		netdev_err(vif->ndev, "%s: invalid meas per burst\n", __func__);
		return -EINVAL;
	}

	return 0;
}

static int rtt_append_meas_params(struct sprd_priv *priv,
				  struct sk_buff *msg,
				  struct rtt_meas_params *params)
{
	struct nlattr *nl_p;

	nl_p = nla_nest_start(msg, SPRD_ATTR_RTT_PEER_RES_MEAS_PARAMS);
	if (!nl_p)
		goto out_put_failure;
	if (nla_put_u8(msg, SPRD_ATTR_RTT_PARAM_MEAS_PER_BURST,
		       params->meas_per_burst) ||
	    nla_put_u8(msg, SPRD_ATTR_RTT_PARAM_NUM_BURSTS_EXP,
		       params->num_of_bursts_exp) ||
	    nla_put_u8(msg, SPRD_ATTR_RTT_PARAM_BURST_DURATION,
		       params->burst_duration) ||
	    nla_put_u16(msg, SPRD_ATTR_RTT_PARAM_BURST_PERIOD,
			params->burst_period))
		goto out_put_failure;
	nla_nest_end(msg, nl_p);
	return 0;
out_put_failure:
	return -ENOBUFS;
}

static int rtt_append_peer_meas_res(struct sprd_priv *priv,
				    struct sk_buff *msg,
				    struct rtt_peer_meas_res *res)
{
	struct nlattr *nl_mres, *nl_f;
	int i;

	if (nla_put(msg, SPRD_ATTR_RTT_PEER_RES_MAC_ADDR,
		    ETH_ALEN, res->mac_addr) ||
	    nla_put_u32(msg, SPRD_ATTR_RTT_PEER_RES_FLAGS,
			res->flags) ||
	    nla_put_u8(msg, SPRD_ATTR_RTT_PEER_RES_STATUS, res->status))
		goto out_put_failure;
	if (res->status == SPRD_ATTR_RTT_PEER_RES_STATUS_FAILED &&
	    nla_put_u8(msg,
		       SPRD_ATTR_RTT_PEER_RES_VALUE_SECONDS,
		       res->value_seconds))
		goto out_put_failure;
	if (res->has_params && rtt_append_meas_params(priv, msg, &res->params))
		goto out_put_failure;
	nl_mres = nla_nest_start(msg, SPRD_ATTR_RTT_PEER_RES_MEAS);
	if (!nl_mres)
		goto out_put_failure;
	for (i = 0; i < res->n_meas; i++) {
		nl_f = nla_nest_start(msg, i);
		if (!nl_f)
			goto out_put_failure;
		if (nla_put_u64_64bit(msg, SPRD_ATTR_RTT_MEAS_T1,
				      res->meas[i].t1, 0) ||
		    nla_put_u64_64bit(msg, SPRD_ATTR_RTT_MEAS_T2,
				      res->meas[i].t2, 0) ||
		    nla_put_u64_64bit(msg, SPRD_ATTR_RTT_MEAS_T3,
				      res->meas[i].t3, 0) ||
		    nla_put_u64_64bit(msg, SPRD_ATTR_RTT_MEAS_T4,
				      res->meas[i].t4, 0))
			goto out_put_failure;
		nla_nest_end(msg, nl_f);
	}
	nla_nest_end(msg, nl_mres);
	return 0;
out_put_failure:
	pr_err("%s: fail to append peer result\n", __func__);
	return -ENOBUFS;
}

static void rtt_send_meas_result(struct sprd_priv *priv,
				 struct rtt_peer_meas_res *res)
{
	struct sk_buff *skb = NULL;
	struct nlattr *nl_res;
	int rc = 0;

	pr_info("sending %d results for peer %pM\n",
		res->n_meas, res->mac_addr);

	skb = cfg80211_vendor_event_alloc(priv->wiphy, NULL,
					  RTT_MEAS_RESULT_MAX_LENGTH,
					  SPRD_EVENT_RTT_MEAS_RESULT_INDEX,
					  GFP_KERNEL);
	if (!skb) {
		pr_err("fail to allocate measurement result\n");
		rc = -ENOMEM;
		goto out;
	}
	if (nla_put_u64_64bit(skb, SPRD_ATTR_RTT_SESSION_COOKIE,
			      priv->ftm.session_cookie, 0)) {
		rc = -ENOBUFS;
		goto out;
	}

	nl_res = nla_nest_start(skb, SPRD_ATTR_RTT_MEAS_PEER_RESULTS);
	if (!nl_res) {
		rc = -ENOBUFS;
		goto out;
	}

	rc = rtt_append_peer_meas_res(priv, skb, res);
	if (rc)
		goto out;

	nla_nest_end(skb, nl_res);
	cfg80211_vendor_event(skb, GFP_KERNEL);
	skb = NULL;
out:
	if (skb)
		kfree_skb(skb);
	if (rc)
		pr_err("send peer result failed, err %d\n", rc);
}

static void rtt_send_peer_res(struct sprd_priv *priv)
{
	if (!priv->ftm.has_ftm_res || !priv->ftm.ftm_res)
		return;

	rtt_send_meas_result(priv, priv->ftm.ftm_res);
	priv->ftm.has_ftm_res = 0;
	priv->ftm.ftm_res->n_meas = 0;
}

static int
rtt_cfg80211_start_session(struct sprd_priv *priv,
			   struct sprd_vif *vif,
			   struct rtt_session_request *request)
{
	int ret = 0;
	bool has_lci = false, has_lcr = false;
	u8 max_meas = 0, channel, *ptr;
	u32 i, cmd_len;
	struct rtt_session_start *cmd = NULL;
	struct sprd_msg *msg;
	struct cmd_rtt *rtt;

	mutex_lock(&priv->ftm.lock);
	if (priv->ftm.session_started) {
		pr_err("%s: FTM session already running\n", __func__);
		ret = -EALREADY;
		goto out;
	}

	for (i = 0; i < request->n_peers; i++) {
		if (request->peers[i].flags & SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCI)
			has_lci = true;
		if (request->peers[i].flags & SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCR)
			has_lcr = true;
		max_meas = max(max_meas,
			       request->peers[i].params.meas_per_burst);
	}

	priv->ftm.ftm_res = kzalloc(sizeof(*priv->ftm.ftm_res) +
				    max_meas *
				    sizeof(struct rtt_peer_meas) +
				    (has_lci ? RTT_MAX_LCI_LENGTH : 0) +
				    (has_lcr ? RTT_MAX_LCR_LENGTH : 0),
				    GFP_KERNEL);
	if (!priv->ftm.ftm_res) {
		ret = -ENOMEM;
		goto out;
	}
	ptr = (u8 *)priv->ftm.ftm_res;
	ptr += sizeof(struct rtt_peer_meas_res) +
	    max_meas * sizeof(struct rtt_peer_meas);
	if (has_lci) {
		priv->ftm.ftm_res->lci = ptr;
		ptr += RTT_MAX_LCI_LENGTH;
	}
	if (has_lcr)
		priv->ftm.ftm_res->lcr = ptr;
	priv->ftm.max_ftm_meas = max_meas;

	cmd_len = sizeof(struct rtt_session_start) +
	    request->n_peers * sizeof(struct rtt_dest_info);
	cmd = kzalloc(cmd_len, GFP_KERNEL);
	if (!cmd) {
		ret = -ENOMEM;
		goto out_ftm_res;
	}

	cmd->session_id = cpu_to_le32(RTT_ESSION_ID);
	cmd->num_of_dest = cpu_to_le16(request->n_peers);
	for (i = 0; i < request->n_peers; i++) {
		ether_addr_copy(cmd->dest_info[i].dst_mac,
				request->peers[i].mac_addr);
		channel = rtt_get_channel(priv->wiphy,
					  request->peers[i].mac_addr,
					  request->peers[i].freq);
		if (!channel) {
			pr_err("%s: can't find FTM target at index %d\n",
			       __func__, i);
			ret = -EINVAL;
			goto out_cmd;
		}
		cmd->dest_info[i].channel = channel - 1;
		if (request->peers[i].flags &
		    SPRD_ATTR_RTT_PEER_MEAS_FLAG_SECURE) {
			cmd->dest_info[i].flags |=
			    FTM_SESSION_START_FLAG_SECURED;
			cmd->dest_info[i].initial_token =
			    request->peers[i].secure_token_id;
		} else {
			cmd->dest_info[i].initial_token =
			    RTT_DEFAULT_INITIAL_TOKEN;
		}
		if (request->peers[i].flags & SPRD_ATTR_RTT_PEER_MEAS_FLAG_ASAP)
			cmd->dest_info[i].flags |= FTM_SESSION_START_FLAG_ASAP;
		if (request->peers[i].flags & SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCI)
			cmd->dest_info[i].flags |=
			    FTM_SESSION_START_FLAG_LCI_REQ;
		if (request->peers[i].flags & SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCR)
			cmd->dest_info[i].flags |=
			    FTM_SESSION_START_FLAG_LCR_REQ;
		cmd->dest_info[i].num_of_ftm_per_burst =
		    request->peers[i].params.meas_per_burst;
		cmd->dest_info[i].num_of_bursts_exp =
		    request->peers[i].params.num_of_bursts_exp;
		cmd->dest_info[i].burst_duration =
		    request->peers[i].params.burst_duration;
		cmd->dest_info[i].burst_period =
		    cpu_to_le16(request->peers[i].params.burst_period);
	}

	/* send range request data to the FW */
	msg = get_cmdbuf(priv, vif, sizeof(struct cmd_rtt) + cmd_len, CMD_RTT);
	if (!msg) {
		ret = -ENOMEM;
		goto out_cmd;
	}
	rtt = (struct cmd_rtt *)msg->data;
	rtt->sub_cmd = RTT_RANGE_REQUEST;
	rtt->len = cmd_len;
	memcpy(rtt->data, cmd, cmd_len);

	ret = send_cmd_recv_rsp(priv, msg,  NULL, 0);
	if (ret) {
		netdev_err(vif->ndev, "%s: ret=%d\n", __func__, ret);
	} else {
		priv->ftm.session_cookie = request->session_cookie;
		priv->ftm.session_started = 1;
	}
out_cmd:
	kfree(cmd);
out_ftm_res:
	if (ret) {
		kfree(priv->ftm.ftm_res);
		priv->ftm.ftm_res = NULL;
	}
out:
	mutex_unlock(&priv->ftm.lock);
	return ret;
}

static void rtt_session_ended(struct sprd_priv *priv, u32 status)
{
	struct sk_buff *skb = NULL;

	mutex_lock(&priv->ftm.lock);

	if (!priv->ftm.session_started) {
		pr_err("%s: FTM session not started, ignoring\n", __func__);
		return;
	}

	pr_info("%s: finishing FTM session\n", __func__);

	/* send left-over results if any */
	rtt_send_peer_res(priv);

	priv->ftm.session_started = 0;
	kfree(priv->ftm.ftm_res);
	priv->ftm.ftm_res = NULL;

	skb = cfg80211_vendor_event_alloc(priv->wiphy, NULL,
					  RTT_NL_EXTRA_ALLOC,
					  SPRD_EVENT_RTT_SESSION_DONE_INDEX,
					  GFP_KERNEL);
	if (!skb)
		goto out;
	if (nla_put_u64_64bit(skb,
			      SPRD_ATTR_RTT_SESSION_COOKIE,
			      priv->ftm.session_cookie, 0) ||
	    nla_put_u32(skb, SPRD_ATTR_LOC_SESSION_STATUS, status)) {
		pr_err("%s: failed to fill session done event\n", __func__);
		goto out;
	}
	cfg80211_vendor_event(skb, GFP_KERNEL);
	skb = NULL;
out:
	kfree_skb(skb);
	mutex_unlock(&priv->ftm.lock);
}

static void rtt_event_per_dest_res(struct sprd_priv *priv,
				   struct rtt_per_dest_res *res)
{
	u32 i, index;
	__le64 tmp = 0;
	u8 n_meas;

	mutex_lock(&priv->ftm.lock);

	if (!priv->ftm.session_started || !priv->ftm.ftm_res) {
		pr_err("%s: Session not running, ignoring res event\n",
		       __func__);
		goto out;
	}
	if (priv->ftm.has_ftm_res &&
	    !ether_addr_equal(res->dst_mac, priv->ftm.ftm_res->mac_addr)) {
		pr_err("%s: previous peer not properly terminated\n", __func__);
		rtt_send_peer_res(priv);
	}

	if (!priv->ftm.has_ftm_res) {
		ether_addr_copy(priv->ftm.ftm_res->mac_addr, res->dst_mac);
		priv->ftm.has_ftm_res = 1;
	}

	n_meas = res->actual_ftm_per_burst;
	switch (res->status) {
	case FTM_PER_DEST_RES_NO_ERROR:
		priv->ftm.ftm_res->status = SPRD_ATTR_RTT_PEER_RES_STATUS_OK;
		break;
	case FTM_PER_DEST_RES_TX_RX_FAIL:
		/* FW reports corrupted results here, discard. */
		n_meas = 0;
		priv->ftm.ftm_res->status = SPRD_ATTR_RTT_PEER_RES_STATUS_OK;
		break;
	case FTM_PER_DEST_RES_PARAM_DONT_MATCH:
		priv->ftm.ftm_res->status =
		    SPRD_ATTR_RTT_PEER_RES_STATUS_INVALID;
		break;
	default:
		pr_err("%s: unexpected status %d\n", __func__, res->status);
		priv->ftm.ftm_res->status =
		    SPRD_ATTR_RTT_PEER_RES_STATUS_INVALID;
		break;
	}

	for (i = 0; i < n_meas; i++) {
		index = priv->ftm.ftm_res->n_meas;
		if (index >= priv->ftm.max_ftm_meas) {
			pr_info("%s: Too many measurements\n", __func__);
			break;
		}
		memcpy(&tmp, res->responder_ftm_res[i].t1,
		       sizeof(res->responder_ftm_res[i].t1));
		priv->ftm.ftm_res->meas[index].t1 = le64_to_cpu(tmp);
		memcpy(&tmp, res->responder_ftm_res[i].t2,
		       sizeof(res->responder_ftm_res[i].t2));
		priv->ftm.ftm_res->meas[index].t2 = le64_to_cpu(tmp);
		memcpy(&tmp, res->responder_ftm_res[i].t3,
		       sizeof(res->responder_ftm_res[i].t3));
		priv->ftm.ftm_res->meas[index].t3 = le64_to_cpu(tmp);
		memcpy(&tmp, res->responder_ftm_res[i].t4,
		       sizeof(res->responder_ftm_res[i].t4));
		priv->ftm.ftm_res->meas[index].t4 = le64_to_cpu(tmp);
		priv->ftm.ftm_res->n_meas++;
	}

	if (res->flags & FTM_PER_DEST_RES_BURST_REPORT_END)
		rtt_send_peer_res(priv);
out:
	mutex_unlock(&priv->ftm.lock);
}

static void rtt_event_end(struct sprd_priv *priv)
{
	struct sk_buff *reply;
	struct wiphy *wiphy = priv->wiphy;
	struct sprd_vif *vif = sprd_mode_to_vif(priv, SPRD_MODE_STATION);
	int i;
	struct nlattr *nl_res;

	reply = cfg80211_vendor_event_alloc(wiphy, &vif->wdev,
					    priv->rtt_results.peer_num *
					    sizeof(struct rtt_wifi_hal_result) +
					    NLMSG_HDRLEN,
					    SPRD_RTT_EVENT_COMPLETE_INDEX,
					    GFP_KERNEL);
	if (!reply) {
		pr_err("%s, %d\n", __func__, __LINE__);
		return;
	}

	for (i = 0; i < priv->rtt_results.peer_num; i++) {
		pr_err("%s, %d\n", __func__, i);
		nl_res =
		    nla_nest_start(reply,
				   SPRD_RTT_ATTRIBUTE_RESULTS_PER_TARGET);
		if (!nl_res) {
			pr_err("%s, %d\n", __func__, __LINE__);
			goto out;
		}
		if (nla_put(reply, SPRD_RTT_ATTRIBUTE_RESULT_MAC, 6,
			    priv->rtt_results.peer_rtt_result[i]->mac_addr) ||
		    nla_put_u32(reply,
				SPRD_RTT_ATTRIBUTE_RESULT_CNT_CNT, i + 1) ||
				nla_put(reply, SPRD_RTT_ATTRIBUTE_RESULT,
					2 * sizeof(struct rtt_wifi_hal_result),
					priv->rtt_results.peer_rtt_result[i])) {
			pr_info("%s, %d\n", __func__, __LINE__);
			goto out;
		}

		nla_nest_end(reply, nl_res);
	}
	nla_put_u32(reply, SPRD_RTT_ATTRIBUTE_RESULTS_COMPLETE, 1);
	cfg80211_vendor_event(reply, GFP_KERNEL);
	reply = NULL;
	pr_info("report rtt result\n");
	priv->ftm.session_started = 0;

	priv->rtt_results.peer_num = 0;
out:
	if (reply)
		kfree_skb(reply);
}

int sc2355_rtt_event(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_priv *priv = vif->priv;
	u8 sub_event;
	u32 status;
	struct rtt_per_dest_res *res;

	memcpy(&sub_event, data, sizeof(sub_event));
	data += sizeof(sub_event);
	len -= sizeof(sub_event);

	switch (sub_event) {
	case RTT_SESSION_END:
		memcpy(&status, data, sizeof(status));
		rtt_event_end(priv);
		break;
	case RTT_PER_DEST_RES:
		if (len < sizeof(struct rtt_per_dest_res)) {
			netdev_err(vif->ndev, "%s: invalid data len\n", __func__);
			return -1;
		}

		res = (struct rtt_per_dest_res *)data;
		rtt_event_per_dest_res(priv, res);
		break;
	default:
		netdev_err(vif->ndev, "%s: unknown FTM event\n", __func__);
		break;
	}
	return 0;
}

int sc2355_rtt_get_capabilities(struct wiphy *wiphy, struct wireless_dev *wdev,
				const void *data, int len)
{
	struct sprd_msg *msg;
	struct cmd_rtt *cmd;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	u8 rsp[RTT_RSP_LEN] = { 0x0 };
	u16 rsp_len = RTT_RSP_LEN;
	int ret = 0;
	struct sk_buff *skb;
	struct nlattr *attr;

	/* get the capabilities from the FW */
	msg = get_cmdbuf(vif->priv, vif, sizeof(struct cmd_rtt) + len, CMD_RTT);
	if (!msg)
		return -ENOMEM;
	cmd = (struct cmd_rtt *)msg->data;
	cmd->sub_cmd = RTT_GET_CAPABILITIES;
	cmd->len = len;
	memcpy(cmd->data, data, len);

	ret = send_cmd_recv_rsp(vif->priv, msg, rsp, &rsp_len);
	if (ret) {
		netdev_err(vif->ndev,
			   "%s: ret=%d, rsp_len=%d\n", __func__, ret, rsp_len);
	}

	/* report capabilities */
	skb = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, RTT_RSP_LEN);
	if (!skb)
		return -ENOMEM;
	attr = nla_nest_start(skb, SPRD_ATTR_LOC_CAPA);
	if (!attr ||
	    nla_put_u32(skb, SPRD_ATTR_LOC_CAPA_FLAGS,
			SPRD_ATTR_LOC_CAPA_FLAG_RTT_RESPONDER |
			SPRD_ATTR_LOC_CAPA_FLAG_RTT_INITIATOR |
			SPRD_ATTR_LOC_CAPA_FLAG_ASAP |
			SPRD_ATTR_LOC_CAPA_FLAG_AOA) ||
	    nla_put_u16(skb, SPRD_ATTR_RTT_CAPA_MAX_NUM_SESSIONS,
			1) ||
	    nla_put_u16(skb, SPRD_ATTR_RTT_CAPA_MAX_NUM_PEERS, 1) ||
	    nla_put_u8(skb, SPRD_ATTR_RTT_CAPA_MAX_NUM_BURSTS_EXP,
		       0) ||
	    nla_put_u8(skb, SPRD_ATTR_RTT_CAPA_MAX_MEAS_PER_BURST,
		       4) ||
	    nla_put_u32(skb, SPRD_ATTR_AOA_CAPA_SUPPORTED_TYPES,
			BIT(SPRD_ATTR_AOA_TYPE_TOP_CIR_PHASE))) {
		netdev_err(vif->ndev,
			   "%s: fail to fill capabilities\n", __func__);
		kfree_skb(skb);
		return -ENOMEM;
	}
	nla_nest_end(skb, attr);

	return cfg80211_vendor_cmd_reply(skb);
}

int sc2355_rtt_start_session(struct wiphy *wiphy, struct wireless_dev *wdev,
			     const void *data, int data_len)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct rtt_session_request *request;
	struct nlattr *tb[SPRD_ATTR_LOC_MAX + 1];
	struct nlattr *tb2[SPRD_ATTR_RTT_PEER_MAX + 1];
	struct nlattr *peer;
	int rc, n_peers = 0, index = 0, rem;

	rc = nla_parse(tb, SPRD_ATTR_LOC_MAX, data, data_len,
		       nl80211_loc_policy, NULL);
	if (rc) {
		netdev_err(vif->ndev, "%s: invalid FTM attribute\n", __func__);
		return rc;
	}

	if (!tb[SPRD_ATTR_RTT_MEAS_PEERS]) {
		netdev_err(vif->ndev, "%s: no peers specified\n", __func__);
		return -EINVAL;
	}

	if (!tb[SPRD_ATTR_RTT_SESSION_COOKIE]) {
		netdev_err(vif->ndev,
			   "%s: session cookie not specified\n", __func__);
		return -EINVAL;
	}

	nla_for_each_nested(peer, tb[SPRD_ATTR_RTT_MEAS_PEERS], rem)
		n_peers++;

	if (!n_peers) {
		netdev_err(vif->ndev, "%s: empty peer list\n", __func__);
		return -EINVAL;
	}

	/* for now only allow measurement for a single peer */
	if (n_peers != 1) {
		netdev_err(vif->ndev,
			   "%s: only single peer allowed\n", __func__);
		return -EINVAL;
	}

	request = kzalloc(sizeof(*request) +
			  n_peers * sizeof(struct rtt_meas_peer_info),
			  GFP_KERNEL);
	if (!request)
		return -ENOMEM;

	request->session_cookie = nla_get_u64(tb[SPRD_ATTR_RTT_SESSION_COOKIE]);
	request->n_peers = n_peers;
	nla_for_each_nested(peer, tb[SPRD_ATTR_RTT_MEAS_PEERS], rem) {
		rc = nla_parse_nested(tb2, SPRD_ATTR_RTT_PEER_MAX,
				      peer, nl80211_rtt_peer_policy, NULL);
		if (rc) {
			netdev_err(vif->ndev,
				   "%s: invalid peer attribute\n", __func__);
			goto out;
		}
		if (!tb2[SPRD_ATTR_RTT_PEER_MAC_ADDR] ||
		    nla_len(tb2[SPRD_ATTR_RTT_PEER_MAC_ADDR])
		    != ETH_ALEN) {
			netdev_err(vif->ndev,
				   "%s: peer MAC address missing or invalid\n",
				   __func__);
			rc = -EINVAL;
			goto out;
		}
		memcpy(request->peers[index].mac_addr,
		       nla_data(tb2[SPRD_ATTR_RTT_PEER_MAC_ADDR]), ETH_ALEN);
		if (tb2[SPRD_ATTR_RTT_PEER_FREQ])
			request->peers[index].freq =
			    nla_get_u32(tb2[SPRD_ATTR_RTT_PEER_FREQ]);
		if (tb2[SPRD_ATTR_RTT_PEER_MEAS_FLAGS])
			request->peers[index].flags =
			    nla_get_u32(tb2[SPRD_ATTR_RTT_PEER_MEAS_FLAGS]);
		if (tb2[SPRD_ATTR_RTT_PEER_SECURE_TOKEN_ID])
			request->peers[index].secure_token_id =
			    nla_get_u8(tb2[SPRD_ATTR_RTT_PEER_SECURE_TOKEN_ID]);
		rc = rtt_parse_meas_params(vif,
					   tb2
					   [SPRD_ATTR_RTT_PEER_MEAS_PARAMS],
					   &request->peers[index].params);
		if (!rc)
			rc = rtt_validate_meas_params(vif,
						      &request->peers[index].params);
		if (rc)
			goto out;
		index++;
	}

	rc = rtt_cfg80211_start_session(priv, vif, request);
out:
	kfree(request);
	return rc;
}

int sc2355_rtt_abort_session(struct wiphy *wiphy, struct wireless_dev *wdev,
			     const void *data, int len)
{
	struct sprd_msg *msg;
	struct cmd_rtt *cmd;
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	int ret;

	mutex_lock(&priv->ftm.lock);
	if (!priv->ftm.session_started) {
		netdev_err(vif->ndev,
			   "%s: FTM session not started\n", __func__);
		return -EAGAIN;
	}

	/* bug 2028856, hackerone 1701183 */
	if (U16_MAX < (sizeof(struct cmd_rtt) + len) || len <= 0) {
		netdev_err(vif->ndev,
			   "%s: param data len is invalid\n", __func__);
		return -EINVAL;
	}

	/* send cancel range request */
	msg = sc2355_get_cmdbuf(priv, vif, sizeof(struct cmd_rtt) + len,
				CMD_RTT, SPRD_HEAD_NORSP, GFP_KERNEL);
	if (!msg)
		return -ENOMEM;
	cmd = (struct cmd_rtt *)msg->data;
	cmd->sub_cmd = RTT_RANGE_CANCEL;
	cmd->len = len;
	memcpy(cmd->data, data, len);

	ret = send_cmd_recv_rsp(priv, msg, NULL, 0);
	if (ret)
		netdev_err(vif->ndev, "%s: ret=%d\n", __func__, ret);

	mutex_unlock(&priv->ftm.lock);

	return ret;
}

int sc2355_rtt_get_responder_info(struct wiphy *wiphy,
				  struct wireless_dev *wdev,
				  const void *data, int len)
{
	struct sprd_vif *vif = netdev_priv(wdev->netdev);

	/* get responder info */
	netdev_info(vif->ndev, "%s: not implemented yet\n", __func__);
	return -ENOTSUPP;
}

int sc2355_rtt_configure_responder(struct wiphy *wiphy,
				   struct wireless_dev *wdev,
				   const void *data, int data_len)
{
	struct sprd_vif *vif = netdev_priv(wdev->netdev);

	/* enable or disable responder */
	netdev_info(vif->ndev, "%s: not implemented yet\n", __func__);
	return -ENOTSUPP;
}

void sc2355_rtt_stop_operations(struct sprd_priv *priv)
{
	rtt_session_ended(priv, SPRD_ATTR_LOC_SESSION_STATUS_ABORTED);
}

void sc2355_rtt_init(struct sprd_priv *priv)
{
	priv->ftm.session_started = 0;
	mutex_init(&priv->ftm.lock);
}

void sc2355_rtt_deinit(struct sprd_priv *priv)
{
	int i;

	kfree(priv->ftm.ftm_res);

	for (i = 0; i < 10; i++)
		kfree(priv->rtt_results.peer_rtt_result[i]);
}
