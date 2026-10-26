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

#include "common/cmd.h"
#include "common/common.h"
#include "common/debug.h"
#include "common/vendor.h"
#include "cmdevt.h"
#ifdef CONFIG_SC2355_WLAN_NAN
#include <linux/version.h>
#include "nan.h"
#endif /* CONFIG_SC2355_WLAN_NAN */
#include "rtt.h"

#define SPRD_ACS_LTE_EVENT_INDEX	35
#define SPRD_REINIT_ACS			0x35

#define MAX_CHANNELS			16
#define MAX_BUCKETS			4

struct llstat_data {
	int rssi_mgmt;
	u32 bcn_rx_cnt;
	struct wmm_ac_stat ac[WIFI_AC_MAX];
	u32 on_time;
	u32 on_time_scan;
	u64 radio_tx_time;
	u64 radio_rx_time;
};

enum vendor_subcmds_index {
	VENDOR_SUBCMD_LL_STATS_SET_INDEX,
	VENDOR_SUBCMD_LL_STATS_GET_INDEX,
	VENDOR_SUBCMD_LL_STATS_CLR_INDEX,
	VENDOR_SUBCMD_LL_RADIO_STATS_INDEX,
	VENDOR_SUBCMD_LL_IFACE_STATS_INDEX,
	VENDOR_SUBCMD_LL_PEER_INFO_STATS_INDEX,
	/* GSCAN Events */
	VENDOR_SUBCMD_GSCAN_START_INDEX,
	VENDOR_SUBCMD_GSCAN_STOP_INDEX,
	VENDOR_SUBCMD_GSCAN_GET_CAPABILITIES_INDEX,
	VENDOR_SUBCMD_GSCAN_GET_CACHED_RESULTS_INDEX,
	VENDOR_SUBCMD_GSCAN_SCAN_RESULTS_AVAILABLE_INDEX,
	VENDOR_SUBCMD_GSCAN_FULL_SCAN_RESULT_INDEX,
	VENDOR_SUBCMD_GSCAN_SCAN_EVENT_INDEX,
	VENDOR_SUBCMD_GSCAN_HOTLIST_AP_FOUND_INDEX,
	VENDOR_SUBCMD_GSCAN_HOTLIST_AP_LOST_INDEX,
	VENDOR_SUBCMD_GSCAN_SET_BSSID_HOTLIST_INDEX,
	VENDOR_SUBCMD_GSCAN_RESET_BSSID_HOTLIST_INDEX,
	VENDOR_SUBCMD_SIGNIFICANT_CHANGE_INDEX,
	VENDOR_SUBCMD_SET_SIGNIFICANT_CHANGE_INDEX,
	VENDOR_SUBCMD_RESET_SIGNIFICANT_CHANGE_INDEX,
	/* EXT TDLS */
	VENDOR_SUBCMD_TDLS_STATE_CHANGE_INDEX,
	VENDOR_SUBCMD_NAN_INDEX,
};

struct gscan_capa {
	s16 max_scan_cache_size;
	u8 max_scan_buckets;
	u8 max_ap_cache_per_scan;
	u8 max_rssi_sample_size;
	u8 max_scan_reporting_threshold;
	u8 max_hotlist_bssids;
	u8 max_hotlist_ssids;
	u8 max_significant_wifi_change_aps;
	u8 max_bssid_history_entries;
	u8 max_number_epno_networks;
	u8 max_number_epno_networks_by_ssid;
	u8 max_whitelist_ssid;
	u8 max_blacklist_size;
};

struct gscan_channel_spec {
	u8 channel;
	u8 dwelltime;
	u8 passive;
	u8 resv;
};

struct gscan_bucket_spec {
	u8 bucket;
	u8 band;
	u8 num_channels;
	u8 base;
	u8 step_count;
	/* reserved for data align */
	u8 reserved;
	u16 report_events;
	u32 period;
	u32 max_period;
	struct gscan_channel_spec channels[MAX_CHANNELS];
};

struct cmd_gscan_set_config {
	u32 base_period;
	u8 max_ap_per_scan;
	u8 report_threshold;
	u8 report_threshold_num_scans;
	u8 num_buckets;
	struct gscan_bucket_spec buckets[MAX_BUCKETS];
};

struct cmd_gscan_channel_list {
	int num_channels;
	int channels[SPRD_TOTAL_CHAN_NR];
};

/*begain of sar scence param define*/
struct set_sar_limit_param {
	u32 sar_scence;
	u8 sar_type;
	s8 power_value;
	u8 phy_mode;
};

/*sar scence define		*
 *scence value: 		*
 *reciver on scence   1 *
 *recive off (normal scence) scence  5 *
 *hot spot scence     3 */
enum sar_scence_value {
	WLAN_SAR_SCENCE_RECIVER_ON = 1,
	WLAN_SAR_SCENCE_HOTSPOT = 3,
	WLAN_SAR_SCENCE_RECIVER_OFF = 5,
};
/*end of sar scence param define*/

/*the map of sar scence to parameter*/
static struct set_sar_limit_param sar_param_map[] = {
	{
		/*recive on scence*/
		WLAN_SAR_SCENCE_RECIVER_ON,
		SPRD_SET_SAR_ABSOLUTE,
		9,
		SPRD_SET_SAR_ALL_MODE,
	},
	{
		/*recive off scence*/
		WLAN_SAR_SCENCE_RECIVER_OFF,
		SPRD_SET_SAR_ABSOLUTE,
		127,
		SPRD_SET_SAR_ALL_MODE,
	},
	{
		/*hotspot scence*/
		WLAN_SAR_SCENCE_HOTSPOT,
		SPRD_SET_SAR_ABSOLUTE,
		6,
		SPRD_SET_SAR_ALL_MODE,
	},
};

/* Send link layer stats CMD */
static int vendor_link_layer_stat(struct sprd_priv *priv,
					 struct sprd_vif *vif, u8 subtype,
					 const void *buf, u8 len, u8 *r_buf,
					 u16 *r_len)
{
	u8 *sub_cmd, *buf_pos;
	struct sprd_msg *msg;

	msg = get_cmdbuf(priv, vif, len + 1, CMD_LLSTAT);
	if (!msg)
		return -ENOMEM;
	sub_cmd = (u8 *)msg->data;
	*sub_cmd = subtype;
	buf_pos = sub_cmd + 1;
	memcpy(buf_pos, buf, len);

	if (subtype == SUBCMD_SET)
		return send_cmd_recv_rsp(priv, msg, 0, 0);
	else
		return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

static int vendor_compose_radio_st(struct sk_buff *reply,
				   struct wifi_radio_stat *radio_st)
{
	/* 2.4G only,radio_num=1,if 5G supported radio_num=2 */
	int radio_num = 1;
	struct nlattr *chan_list, *chan_info;

	if (nla_put_u32(reply, ATTR_LL_STATS_NUM_RADIOS,
			radio_num))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ID,
			radio_st->radio))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ON_TIME,
			radio_st->on_time))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_TX_TIME,
			radio_st->tx_time))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_NUM_TX_LEVELS,
			radio_st->num_tx_levels))
		goto out_put_fail;
	if (radio_st->num_tx_levels > 0) {
		if (nla_put(reply, ATTR_LL_STATS_RADIO_TX_TIME_PER_LEVEL,
			    sizeof(u32) * radio_st->num_tx_levels,
			    radio_st->tx_time_per_levels))
			goto out_put_fail;
	}
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_RX_TIME,
			radio_st->rx_time))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ON_TIME_SCAN,
			radio_st->on_time_scan))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ON_TIME_NBD,
			radio_st->on_time_nbd))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ON_TIME_GSCAN,
			radio_st->on_time_gscan))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ON_TIME_ROAM_SCAN,
			radio_st->on_time_roam_scan))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ON_TIME_PNO_SCAN,
			radio_st->on_time_pno_scan))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_ON_TIME_HS20,
			radio_st->on_time_hs20))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_RADIO_NUM_CHANNELS,
			radio_st->num_channels))
		goto out_put_fail;
	if (radio_st->num_channels > 0) {
		chan_list = nla_nest_start(reply, ATTR_LL_STATS_CH_INFO);
		chan_info = nla_nest_start(reply, 0);
		if (nla_put_u32(reply, ATTR_LL_STATS_CHANNEL_INFO_WIDTH,
				radio_st->channels[0].channel.width))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_CHANNEL_INFO_CENTER_FREQ,
				radio_st->channels[0].channel.center_freq))
			goto out_put_fail;
		if (nla_put_u32
		    (reply, ATTR_LL_STATS_CHANNEL_INFO_CENTER_FREQ0,
		     radio_st->channels[0].channel.center_freq0))
			goto out_put_fail;

		if (nla_put_u32
		    (reply, ATTR_LL_STATS_CHANNEL_INFO_CENTER_FREQ1,
		     radio_st->channels[0].channel.center_freq1))
			goto out_put_fail;

		if (nla_put_u32(reply, ATTR_LL_STATS_CHANNEL_ON_TIME,
				radio_st->channels[0].on_time))
			goto out_put_fail;

		if (nla_put_u32(reply, ATTR_LL_STATS_CHANNEL_CCA_BUSY_TIME,
				radio_st->channels[0].cca_busy_time))
			goto out_put_fail;
		nla_nest_end(reply, chan_info);
		nla_nest_end(reply, chan_list);
	}
	return 0;
out_put_fail:
	return -EMSGSIZE;
}

static int vendor_compose_iface_st(struct sk_buff *reply,
				   struct wifi_iface_stat *iface_st)
{
	int i;
	struct nlattr *nest1, *nest2;

	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_INFO_MODE,
			iface_st->info.mode))
		goto out_put_fail;
	if (nla_put(reply, ATTR_LL_STATS_IFACE_INFO_MAC_ADDR,
		    sizeof(iface_st->info.mac_addr), iface_st->info.mac_addr))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_INFO_STATE,
			iface_st->info.state))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_INFO_ROAMING,
			iface_st->info.roaming))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_INFO_CAPABILITIES,
			iface_st->info.capabilities))
		goto out_put_fail;
	if (nla_put(reply, ATTR_LL_STATS_IFACE_INFO_SSID,
		    sizeof(iface_st->info.ssid), iface_st->info.ssid))
		goto out_put_fail;
	if (nla_put(reply, ATTR_LL_STATS_IFACE_INFO_BSSID,
		    sizeof(iface_st->info.bssid), iface_st->info.bssid))
		goto out_put_fail;
	if (nla_put(reply, ATTR_LL_STATS_IFACE_INFO_AP_COUNTRY_STR,
		    sizeof(iface_st->info.ap_country_str),
		    iface_st->info.ap_country_str))
		goto out_put_fail;
	if (nla_put(reply, ATTR_LL_STATS_IFACE_INFO_COUNTRY_STR,
		    sizeof(iface_st->info.country_str),
		    iface_st->info.country_str))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_INFO_TIME_SLICING_DUTY_CYCLE_PERCENT,
			iface_st->info.time_slicing_duty_cycle_percent))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_BEACON_RX,
			iface_st->beacon_rx))
		goto out_put_fail;
	if (nla_put_u64_64bit(reply, ATTR_LL_STATS_IFACE_AVERAGE_TSF_OFFSET,
			      iface_st->average_tsf_offset, 0))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_LEAKY_AP_DETECTED,
			iface_st->leaky_ap_detected))
		goto out_put_fail;
	if (nla_put_u32(reply,
			ATTR_LL_STATS_IFACE_LEAKY_AP_AVG_NUM_FRAMES_LEAKED,
			iface_st->leaky_ap_avg_num_frames_leaked))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_LEAKY_AP_GUARD_TIME,
			iface_st->leaky_ap_guard_time))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_MGMT_RX,
			iface_st->mgmt_rx))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_MGMT_ACTION_RX,
			iface_st->mgmt_action_rx))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_MGMT_ACTION_TX,
			iface_st->mgmt_action_tx))
		goto out_put_fail;
	if (nla_put_s32(reply, ATTR_LL_STATS_IFACE_RSSI_MGMT,
			iface_st->rssi_mgmt))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_RSSI_DATA,
			iface_st->rssi_data))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_RSSI_ACK,
			iface_st->rssi_ack))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_IFACE_RSSI_DATA,
			iface_st->rssi_data))
		goto out_put_fail;
	nest1 = nla_nest_start(reply, ATTR_LL_STATS_WMM_INFO);
	if (!nest1)
		goto out_put_fail;
	for (i = 0; i < WIFI_AC_MAX; i++) {
		nest2 = nla_nest_start(reply, ATTR_LL_STATS_WMM_AC_AC);
		if (!nest2)
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_AC,
				iface_st->ac[i].ac))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_TX_MPDU,
				iface_st->ac[i].tx_mpdu))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_RX_MPDU,
				iface_st->ac[i].rx_mpdu))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_TX_MCAST,
				iface_st->ac[i].tx_mcast))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_RX_MCAST,
				iface_st->ac[i].rx_mcast))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_RX_AMPDU,
				iface_st->ac[i].rx_ampdu))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_TX_AMPDU,
				iface_st->ac[i].tx_ampdu))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_MPDU_LOST,
				iface_st->ac[i].mpdu_lost))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_RETRIES,
				iface_st->ac[i].retries))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_RETRIES_SHORT,
				iface_st->ac[i].retries_short))
			goto out_put_fail;
		if (nla_put_u32(reply, ATTR_LL_STATS_WMM_AC_RETRIES_LONG,
				iface_st->ac[i].retries_long))
			goto out_put_fail;
		if (nla_put_u32(reply,
				ATTR_LL_STATS_WMM_AC_CONTENTION_TIME_MIN,
				iface_st->ac[i].contention_time_min))
			goto out_put_fail;
		if (nla_put_u32(reply,
				ATTR_LL_STATS_WMM_AC_CONTENTION_TIME_MAX,
				iface_st->ac[i].contention_time_max))
			goto out_put_fail;
		if (nla_put_u32(reply,
				ATTR_LL_STATS_WMM_AC_CONTENTION_TIME_AVG,
				iface_st->ac[i].contention_time_avg))
			goto out_put_fail;
		if (nla_put_u32(reply,
				ATTR_LL_STATS_WMM_AC_CONTENTION_NUM_SAMPLES,
				iface_st->ac[i].contention_num_samples))
			goto out_put_fail;
		nla_nest_end(reply, nest2);
	}
	nla_nest_end(reply, nest1);
	return 0;
out_put_fail:
	return -EMSGSIZE;
}

static void vendor_calc_radio_dif(struct sprd_llstat_radio *dif_radio,
				  struct llstat_data *llst,
				  struct sprd_llstat_radio *pre_radio)
{
	int i;

	dif_radio->rssi_mgmt = llst->rssi_mgmt;
	dif_radio->bcn_rx_cnt = llst->bcn_rx_cnt >= pre_radio->bcn_rx_cnt ?
	    llst->bcn_rx_cnt - pre_radio->bcn_rx_cnt : llst->bcn_rx_cnt;
	/* save lastest beacon count */
	pre_radio->bcn_rx_cnt = llst->bcn_rx_cnt;

	for (i = 0; i < WIFI_AC_MAX; i++) {
		dif_radio->ac[i].tx_mpdu = (llst->ac[i].tx_mpdu >=
					    pre_radio->ac[i].tx_mpdu) ?
		    llst->ac[i].tx_mpdu : pre_radio->ac[i].tx_mpdu;
		/* save lastest tx mpdu */
		pre_radio->ac[i].tx_mpdu = llst->ac[i].tx_mpdu;

		dif_radio->ac[i].rx_mpdu = (llst->ac[i].rx_mpdu >=
					    pre_radio->ac[i].rx_mpdu) ?
		    llst->ac[i].rx_mpdu : pre_radio->ac[i].rx_mpdu;
		/* save lastest rx mpdu */
		pre_radio->ac[i].rx_mpdu = llst->ac[i].rx_mpdu;

		dif_radio->ac[i].mpdu_lost = (llst->ac[i].mpdu_lost >=
			pre_radio->ac[i].mpdu_lost) ?
		    llst->ac[i].mpdu_lost : pre_radio->ac[i].mpdu_lost;
		/* save mpdu lost value */
		pre_radio->ac[i].mpdu_lost = llst->ac[i].mpdu_lost;

		dif_radio->ac[i].retries = (llst->ac[i].retries >=
			pre_radio->ac[i].retries) ?
		    llst->ac[i].retries : pre_radio->ac[i].retries;
		/* save retries value */
		pre_radio->ac[i].retries = llst->ac[i].retries;
	}
}

/* RSSI monitor function---CMD ID:80 */

static int vendor_send_rssi_cmd(struct sprd_priv *priv, struct sprd_vif *vif,
				const void *buf, u8 len)
{
	struct sprd_msg *msg;

	msg = get_cmdbuf(priv, vif, len, CMD_RSSI_MONITOR);
	if (!msg)
		return -ENOMEM;
	memcpy(msg->data, buf, len);
	return send_cmd_recv_rsp(priv, msg, 0, 0);
}

static int vendor_flush_epno_list(struct sprd_vif *vif)
{
	int ret;
	char flush_data = 1;
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	ret = sc2355_gscan_subcmd(vif->priv, vif,
				  (void *)&flush_data,
				  SPRD_GSCAN_SUBCMD_SET_EPNO_FLUSH,
				  sizeof(flush_data), (u8 *)(&rsp), &rlen);
	pr_debug("flush epno list, ret = %d\n", ret);
	return ret;
}

static int vendor_start_offload_packet(struct sprd_priv *priv,
				       struct sprd_vif *vif,
				       struct nlattr **tb, u32 request_id)
{
	u8 src[ETH_ALEN], dest[ETH_ALEN];
	u32 period, len;
	u16 prot_type;
	u8 *data, *pos;
	int ret;

	if (!tb[ATTR_OFFLOADED_PACKETS_IP_PACKET_DATA] ||
	    !tb[ATTR_OFFLOADED_PACKETS_SRC_MAC_ADDR] ||
	    !tb[ATTR_OFFLOADED_PACKETS_DST_MAC_ADDR] ||
	    !tb[ATTR_OFFLOADED_PACKETS_PERIOD] ||
	    !tb[ATTR_OFFLOADED_PACKETS_ETHER_PROTO_TYPE]) {
		pr_err("check start offload para failed\n");
		return -EINVAL;
	}

	period = nla_get_u32(tb[ATTR_OFFLOADED_PACKETS_PERIOD]);
	prot_type = nla_get_u16(tb[ATTR_OFFLOADED_PACKETS_ETHER_PROTO_TYPE]);
	prot_type = htons(prot_type);
	nla_memcpy(src, tb[ATTR_OFFLOADED_PACKETS_SRC_MAC_ADDR], ETH_ALEN);
	nla_memcpy(dest, tb[ATTR_OFFLOADED_PACKETS_DST_MAC_ADDR], ETH_ALEN);
	len = nla_len(tb[ATTR_OFFLOADED_PACKETS_IP_PACKET_DATA]);

	data = kzalloc(len + 14, GFP_KERNEL);
	if (!data)
		return -ENOMEM;

	pos = data;
	memcpy(pos, dest, ETH_ALEN);
	pos += ETH_ALEN;
	memcpy(pos, src, ETH_ALEN);
	pos += ETH_ALEN;
	memcpy(pos, &prot_type, 2);
	pos += 2;
	memcpy(pos, nla_data(tb[ATTR_OFFLOADED_PACKETS_IP_PACKET_DATA]), len);

	ret = sc2355_set_packet_offload(priv, vif,
					request_id, 1, period, len + 14, data);
	kfree(data);

	return ret;
}

static int vendor_stop_offload_packet(struct sprd_priv *priv,
				      struct sprd_vif *vif, u32 request_id)
{
	return sc2355_set_packet_offload(priv, vif, request_id, 0, 0, 0, NULL);
}

static int vendor_parse_sae_entry(struct sae_entry *entry,
				  const void *data, int len)
{
	int rem_len, type, data_len;
	struct nlattr *pos;

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		switch (type) {
		case VENDOR_SAE_PASSWORD:
			data_len = nla_len(pos);
			entry->passwd_len = data_len;
			if (data_len < sizeof(entry->password)) {
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
				nla_strscpy(entry->password, pos, data_len + 1);
#else
				nla_strlcpy(entry->password, pos, data_len + 1);
#endif
			} else {
				pr_err("entry->passwd_len(%d) is more than expected\n", data_len);
				return -ENOEXEC;
			}
			break;
		case VENDOR_SAE_IDENTIFIER:
			data_len = nla_len(pos);
			entry->id_len = data_len;
			if (entry->id_len > sizeof(entry->identifier)) {
				pr_err("invalid id_len %d\n", entry->id_len);
				return -EINVAL;
			}
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
			nla_strscpy(entry->identifier, pos, data_len);
#else
			nla_strlcpy(entry->identifier, pos, data_len);
#endif
			break;
		case VENDOR_SAE_PEER_ADDR:
			data_len = nla_len(pos);
			if (data_len != ETH_ALEN) {
				pr_err("invalid peer_addr len %d\n", data_len);
				return -EINVAL;
			}
			nla_memcpy(entry->peer_addr, pos, ETH_ALEN);
			break;
		case VENDOR_SAE_VLAN_ID:
			entry->vlan_id = (s32)nla_get_u32(pos);
			break;
		default:
			break;
		}
	}
	return 0;
}

static int vendor_softap_convert_para(struct sprd_vif *vif,
				      struct softap_sae_setting *setting,
				      char *para)
{
	char *pos;
	int header_len, index, data_len, *d;
	struct sae_entry *tmp;
	struct tlv_data *tlv;

	pos = para;
	data_len = 0;
	header_len = sizeof(struct tlv_data);

	for (index = 0; index < SPRD_SAE_MAX_NUM; index++) {
		if (setting->entry[index].used == 0)
			break;

		tmp = &setting->entry[index];
		/* add sae entry tlv first */
		tlv = (struct tlv_data *)pos;
		tlv->type = VENDOR_SAE_ENTRY - 1;
		tlv->len = 0;
		pos += header_len;
		data_len += header_len;

		/* PASSWORD ELEMENT */
		if (tmp->passwd_len > 0) {
			tlv = (struct tlv_data *)pos;
			tlv->type = VENDOR_SAE_PASSWORD - 1;
			tlv->len = tmp->passwd_len;

			memcpy(tlv->data, tmp->password, tmp->passwd_len);
			pos += (header_len + tmp->passwd_len);
			data_len += (header_len + tmp->passwd_len);
		}

		/* IDENTIFIER ELEMENT */
		tmp = &setting->entry[index];
		if (tmp->id_len > 0) {
			tlv = (struct tlv_data *)pos;
			tlv->type = VENDOR_SAE_IDENTIFIER - 1;
			tlv->len = tmp->id_len;
			memcpy(tlv->data, tmp->identifier, tmp->id_len);
			pr_info("%s id: %s, len:%d\n", __func__,
				tmp->identifier, tmp->id_len);
			pos += (header_len + tmp->id_len);
			data_len += (header_len + tmp->id_len);
		}
		/* PEER_ADDRESS ELEMENT */
		if (!is_zero_ether_addr(tmp->peer_addr)) {
			tlv = (struct tlv_data *)pos;
			tlv->type = VENDOR_SAE_PEER_ADDR - 1;
			tlv->len = ETH_ALEN;

			memcpy(tlv->data, tmp->peer_addr, ETH_ALEN);
			pos += (header_len + ETH_ALEN);
			data_len += (header_len + ETH_ALEN);
		}
		/* VLAN_ID ELEMENT */
		if (tmp->vlan_id != -1) {
			tlv = (struct tlv_data *)pos;
			tlv->type = VENDOR_SAE_VLAN_ID - 1;
			tlv->len = sizeof(tmp->vlan_id);
			d = (u32 *)tlv->data;
			*d = (u32)tmp->vlan_id;
			pos += (header_len + sizeof(tmp->vlan_id));
			data_len += (header_len + sizeof(tmp->vlan_id));
		}
	}

	if (setting->passphrase_len) {
		/* ADD ENTRY */
		tlv = (struct tlv_data *)pos;
		tlv->type = VENDOR_SAE_ENTRY - 1;
		tlv->len = 0;
		pos += header_len;
		data_len += header_len;

		/* PASSWORD ELEMENT */
		tlv = (struct tlv_data *)pos;
		tlv->type = VENDOR_SAE_PWD - 1;
		tlv->len = setting->passphrase_len;
		memcpy(tlv->data, setting->passphrase, setting->passphrase_len);
		pos += (header_len + setting->passphrase_len);
		data_len += (header_len + setting->passphrase_len);
	}

	/* GROUP */
	if (setting->group_count) {
		tlv = (struct tlv_data *)pos;
		tlv->type = VENDOR_SAE_GROUP_ID - 1;
		tlv->len = setting->group_count;
		pos = tlv->data;
		for (index = 0; index < setting->group_count; index++) {
			*pos = (unsigned char)(setting->groups[index]);
			pos++;
		}
		data_len += (header_len + setting->group_count);
	}

	/* ACT */
	if (setting->act != -1) {
		tlv = (struct tlv_data *)pos;
		tlv->type = VENDOR_SAE_ACT - 1;
		tlv->len = sizeof(u32);
		d = (u32 *)tlv->data;
		*d = (setting->act);
		pos += header_len + sizeof(u32);
		data_len += header_len + sizeof(u32);
	}

	/* End */
	tlv = (struct tlv_data *)pos;
	tlv->type = VENDOR_SAE_END;
	tlv->len = 0;
	data_len += header_len;

	return data_len;
}

/* enable roaming functon------ CMD ID:9 */
static int vendor_roaming_enable(struct wiphy *wiphy,
				 struct wireless_dev *wdev,
				 const void *data, int len)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	u8 roam_state;
	struct nlattr *tb[ATTR_ROAMING_POLICY + 1];
	int ret = 0;

	if (nla_parse(tb, ATTR_ROAMING_POLICY,
		      data, len, NULL, NULL)) {
		pr_err("Invalid ATTR\n");
		return -EINVAL;
	}

	if (tb[ATTR_ROAMING_POLICY]) {
		roam_state = (u8)nla_get_u32(tb[ATTR_ROAMING_POLICY]);
		pr_info("roaming offload state:%d\n", roam_state);
		/* send roam state with roam params by roaming CMD */
		ret = sc2355_set_roam_offload(priv, vif,
					      SPRD_ROAM_OFFLOAD_SET_FLAG,
					      &roam_state, sizeof(roam_state));
	}

	return ret;
}

static int vendor_nan_enable(struct wiphy *wiphy,
			     struct wireless_dev *wdev,
			     const void *data, int len)
{
	return VENDOR_WIFI_SUCCESS;
}

/* set link layer status function-----CMD ID:14 */
static int vendor_set_llstat_handler(struct wiphy *wiphy,
				     struct wireless_dev *wdev,
				     const void *data, int len)
{
	int ret = 0, err = 0;
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct wifi_link_layer_params *ll_params;
	struct nlattr *tb[ATTR_LL_STATS_SET_MAX + 1];

	if (!priv || !vif)
		return -EIO;
	if (!(priv->fw_capa & SPRD_CAPA_LL_STATS))
		return -ENOTSUPP;
	if (!data) {
		pr_err("%s llstat param check filed\n", __func__);
		return -EINVAL;
	}
	err = nla_parse(tb, ATTR_LL_STATS_SET_MAX, data,
			len, ll_stats_policy, NULL);
	if (err)
		return err;
	ll_params = kzalloc(sizeof(*ll_params), GFP_KERNEL);
	if (!ll_params)
		return -ENOMEM;
	if (tb[ATTR_LL_STATS_MPDU_THRESHOLD]) {
		ll_params->mpdu_size_threshold =
		    nla_get_u32(tb[ATTR_LL_STATS_MPDU_THRESHOLD]);
	}
	if (tb[ATTR_LL_STATS_GATHERING]) {
		ll_params->aggressive_statistics_gathering =
		    nla_get_u32(tb[ATTR_LL_STATS_GATHERING]);
	}

	wiphy_err(wiphy, "%s mpdu_threshold =%u\n gathering=%u\n",
		  __func__, ll_params->mpdu_size_threshold,
		  ll_params->aggressive_statistics_gathering);
	if (ll_params->aggressive_statistics_gathering)
		ret = vendor_link_layer_stat(priv, vif, SUBCMD_SET,
						    ll_params,
						    sizeof(*ll_params), 0, 0);
	kfree(ll_params);
	return ret;
}

/* get link layer status function---CMD ID:15 */
static int vendor_get_llstat_handler(struct wiphy *wiphy,
				     struct wireless_dev *wdev,
				     const void *data, int len)
{
	struct sk_buff *reply_radio, *reply_iface;
	struct llstat_data *llst;
	struct wifi_radio_stat *radio_st;
	struct wifi_iface_stat *iface_st;
	struct sprd_llstat_radio *dif_radio;
	u16 r_len = sizeof(*llst);
	u8 r_buf[sizeof(*llst)], i;
	u32 reply_radio_length, reply_iface_length;
	int ret = 0;
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	u16 recv_len = sizeof(struct llstat_channel_info) + 4;
	char recv_buf[50] = { 0x00 };
	u32 channel_num = 0;
	struct llstat_channel_info *info;
	char *pos;

	if (!priv || !vif)
		return -EIO;
	if (!(priv->fw_capa & SPRD_CAPA_LL_STATS))
		return -ENOTSUPP;
	memset(r_buf, 0, r_len);
	radio_st = kzalloc(sizeof(*radio_st), GFP_KERNEL);
	iface_st = kzalloc(sizeof(*iface_st), GFP_KERNEL);
	dif_radio = kzalloc(sizeof(*dif_radio), GFP_KERNEL);

	if (!radio_st || !iface_st || !dif_radio) {
		ret = -ENOMEM;
		goto clean;
	}
	ret = vendor_link_layer_stat(priv, vif, SUBCMD_GET, NULL, 0,
				     r_buf, &r_len);
	if (ret)
		goto clean;

	llst = (struct llstat_data *)r_buf;

	vendor_calc_radio_dif(dif_radio, llst, &priv->pre_radio);

	/* set data for iface struct */
	iface_st->info.mode = vif->mode;
	iface_st->info.time_slicing_duty_cycle_percent = 50;
	memcpy(iface_st->info.mac_addr, vif->ndev->dev_addr, ETH_ALEN);
	iface_st->info.state = (enum vendor_wifi_connection_state)vif->sm_state;
	memcpy(iface_st->info.ssid, vif->ssid, IEEE80211_MAX_SSID_LEN);
	ether_addr_copy(iface_st->info.bssid, vif->bssid);
	iface_st->beacon_rx = dif_radio->bcn_rx_cnt;
	iface_st->rssi_mgmt = dif_radio->rssi_mgmt;
	for (i = 0; i < WIFI_AC_MAX; i++) {
		iface_st->ac[i].tx_mpdu = dif_radio->ac[i].tx_mpdu;
		iface_st->ac[i].rx_mpdu = dif_radio->ac[i].rx_mpdu;
		iface_st->ac[i].mpdu_lost = dif_radio->ac[i].mpdu_lost;
		iface_st->ac[i].retries = dif_radio->ac[i].retries;
	}
	/* set data for radio struct */
	radio_st->on_time = llst->on_time;
	radio_st->tx_time = (u32)llst->radio_tx_time;
	radio_st->rx_time = (u32)llst->radio_rx_time;
	radio_st->on_time_scan = llst->on_time_scan;
	pr_info("beacon_rx=%d, rssi_mgmt=%d\n",
		iface_st->beacon_rx, iface_st->rssi_mgmt);
	pr_info("on_time=%d, tx_time=%d\n",
		radio_st->on_time, radio_st->tx_time);
	pr_info("rx_time=%d, on_time_scan=%d,\n",
		radio_st->rx_time, radio_st->on_time_scan);
	radio_st->num_tx_levels = 1;
	radio_st->tx_time_per_levels = (u32 *)&llst->radio_tx_time;
	/* androidR need get channel info to pass vts test */
	if (priv->extend_feature & SPRD_EXTEND_FEATURE_LLSTATE) {
		ret =
		    sc2355_externed_llstate(priv, vif, SUBCMD_GET,
					    SPRD_SUBTYPE_CHANNEL_INFO, NULL,
					    0, recv_buf, &recv_len);
		if (ret) {
			pr_err("set externed llstate failed\n");
			goto clean;
		}

		pos = recv_buf;
		channel_num = *(u32 *)pos;
		pos += sizeof(u32);
		wiphy_info(wiphy, "channel num %d\n", channel_num);
		radio_st->num_channels = channel_num;

		if (channel_num) {
			info = (struct llstat_channel_info *)(pos);
			pr_info("cca busy time : %d, on time : %d\n",
				info->cca_busy_time, info->on_time);
			pr_info
			    ("center width : %d, center_freq : %d, center_freq0 : %d, center_freq1  :%d\n",
			     info->channel_width, info->center_freq,
			     info->center_freq0, info->center_freq1);
			radio_st->channels[0].cca_busy_time =
			    info->cca_busy_time;
			radio_st->channels[0].on_time = info->on_time;
			radio_st->channels[0].channel.center_freq =
			    info->center_freq;
			radio_st->channels[0].channel.center_freq0 =
			    info->center_freq0;
			radio_st->channels[0].channel.center_freq1 =
			    info->center_freq1;
			radio_st->channels[0].channel.width =
			    info->channel_width;
		}
	}

	/* alloc radio reply buffer */
	reply_radio_length = sizeof(struct wifi_radio_stat) + 1000;
	reply_iface_length = sizeof(struct wifi_iface_stat) + 1000;

	pr_info("start to put radio data\n");
	reply_radio = cfg80211_vendor_cmd_alloc_reply_skb(wiphy,
							  reply_radio_length);
	if (!reply_radio)
		goto clean;

	if (nla_put_u32(reply_radio, NL80211_ATTR_VENDOR_ID, OUI_SPREAD))
		goto radio_out_put_fail;
	if (nla_put_u32(reply_radio, NL80211_ATTR_VENDOR_SUBCMD,
			VENDOR_GET_LLSTAT))
		goto radio_out_put_fail;
	if (nla_put_u32(reply_radio, ATTR_LL_STATS_TYPE,
			ATTR_CMD_LL_STATS_GET_TYPE_RADIO))
		goto radio_out_put_fail;

	ret = vendor_compose_radio_st(reply_radio, radio_st);

	ret = cfg80211_vendor_cmd_reply(reply_radio);

	pr_info("start to put iface data\n");
	/* alloc iface reply buffer */
	reply_iface = cfg80211_vendor_cmd_alloc_reply_skb(wiphy,
							  reply_iface_length);
	if (!reply_iface)
		goto clean;

	if (nla_put_u32(reply_iface, NL80211_ATTR_VENDOR_ID, OUI_SPREAD))
		goto iface_out_put_fail;
	if (nla_put_u32(reply_iface, NL80211_ATTR_VENDOR_SUBCMD,
			VENDOR_GET_LLSTAT))
		goto iface_out_put_fail;
	if (nla_put_u32(reply_iface, ATTR_LL_STATS_TYPE,
			ATTR_CMD_LL_STATS_GET_TYPE_IFACE))
		goto iface_out_put_fail;
	ret = vendor_compose_iface_st(reply_iface, iface_st);
	ret = cfg80211_vendor_cmd_reply(reply_iface);

clean:
	kfree(radio_st);
	kfree(iface_st);
	kfree(dif_radio);
	return ret;
radio_out_put_fail:
	kfree(radio_st);
	kfree(iface_st);
	kfree(dif_radio);
	kfree_skb(reply_radio);
	WARN_ON(1);
	return -EMSGSIZE;
iface_out_put_fail:
	kfree(radio_st);
	kfree(iface_st);
	kfree(dif_radio);
	kfree_skb(reply_iface);
	WARN_ON(1);
	return -EMSGSIZE;
}

/* clear link layer status function--- CMD ID:16 */
static int vendor_clr_llstat_handler(struct wiphy *wiphy,
				     struct wireless_dev *wdev,
				     const void *data, int len)
{
	int ret = 0;
	struct sk_buff *reply;
	struct wifi_clr_llstat_rsp clr_rsp;
	struct nlattr *tb[ATTR_LL_STATS_CLR_MAX + 1];
	u32 *stats_clear_rsp_mask, stats_clear_req_mask = 0;
	u16 r_len = sizeof(*stats_clear_rsp_mask);
	u8 r_buf[sizeof(*stats_clear_rsp_mask)];
	u32 reply_length, err;

	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);

	if (!(priv->fw_capa & SPRD_CAPA_LL_STATS))
		return -ENOTSUPP;
	memset(r_buf, 0, r_len);
	if (!data) {
		pr_err("%s wrong llstat clear req mask\n", __func__);
		return -EINVAL;
	}
	err = nla_parse(tb, ATTR_LL_STATS_CLR_MAX, data, len, NULL, NULL);
	if (err)
		return err;
	if (tb[ATTR_LL_STATS_CLR_CONFIG_REQ_MASK]) {
		stats_clear_req_mask =
		    nla_get_u32(tb[ATTR_LL_STATS_CLR_CONFIG_REQ_MASK]);
	}
	wiphy_info(wiphy, "stats_clear_req_mask = %u\n", stats_clear_req_mask);
	ret = vendor_link_layer_stat(priv, vif, SUBCMD_DEL,
				     &stats_clear_req_mask, r_len, r_buf,
				     &r_len);
	stats_clear_rsp_mask = (u32 *)r_buf;
	clr_rsp.stats_clear_rsp_mask = *stats_clear_rsp_mask;
	clr_rsp.stop_rsp = 1;

	reply_length = sizeof(clr_rsp) + 100;
	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, reply_length);
	if (!reply)
		return -ENOMEM;
	if (nla_put_u32(reply, NL80211_ATTR_VENDOR_ID, OUI_SPREAD))
		goto out_put_fail;
	if (nla_put_u32(reply, NL80211_ATTR_VENDOR_SUBCMD,
			VENDOR_CLR_LLSTAT))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_CLR_CONFIG_RSP_MASK,
			clr_rsp.stats_clear_rsp_mask))
		goto out_put_fail;
	if (nla_put_u32(reply, ATTR_LL_STATS_CLR_CONFIG_STOP_RSP,
			clr_rsp.stop_rsp))
		goto out_put_fail;
	ret = cfg80211_vendor_cmd_reply(reply);

	return ret;
out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

/* start gscan functon, including scan params configuration------ CMD ID:20 */
static int vendor_gscan_start(struct wiphy *wiphy,
			      struct wireless_dev *wdev,
			      const void *data, int len)
{
	u64 tlen;
	int i, j, ret = 0, enable;
	int rem_len, rem_outer_len, type;
	int rem_inner_len, rem_outer_len1, rem_inner_len1;
	struct nlattr *pos, *outer_iter, *inner_iter;
	struct nlattr *outer_iter1, *inner_iter1;
	struct cmd_gscan_set_config *params;
	struct cmd_gscan_rsp_header rsp;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	pr_info("%s enter\n", __func__);
	params = kmalloc(sizeof(*params), GFP_KERNEL);
	if (!params)
		return -ENOMEM;

	/* malloc memory to store scan params */
	memset(params, 0, sizeof(*params));

	/* parse attri from hal, to configure scan params */
	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		switch (type) {
		case GSCAN_ATTR_CONFIG_REQUEST_ID:
			vif->priv->gscan_req_id = nla_get_u32(pos);
			break;

		case GSCAN_ATTR_CONFIG_BASE_PERIOD:
			params->base_period = nla_get_u32(pos);
			break;

		case GSCAN_ATTR_CONFIG_MAX_AP_PER_SCAN:
			params->max_ap_per_scan = nla_get_u32(pos);
			break;

		case GSCAN_ATTR_CONFIG_REPORT_THR:
			params->report_threshold = nla_get_u8(pos);
			break;

		case GSCAN_ATTR_CONFIG_REPORT_NUM_SCANS:
			params->report_threshold_num_scans = nla_get_u8(pos);
			break;

		case GSCAN_ATTR_CONFIG_NUM_BUCKETS:
			params->num_buckets = nla_get_u8(pos);
			break;

		case GSCAN_ATTR_CONFIG_BUCKET_SPEC:
		i = 0;
		nla_for_each_nested(outer_iter, pos, rem_outer_len) {
			nla_for_each_nested(inner_iter, outer_iter,
					    rem_inner_len) {
				type = nla_type(inner_iter);
				switch (type) {
				case GSCAN_ATTR_CONFIG_BUCKET_INDEX:
					params->buckets[i].bucket =
					    nla_get_u8(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_BUCKET_BAND:
					params->buckets[i].band =
					    nla_get_u8(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_BUCKET_PERIOD:
					params->buckets[i].period =
					    nla_get_u32(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_BUCKET_REPORT_EVENTS:
					params->buckets[i].report_events =
					    nla_get_u8(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_BUCKET_NUM_CHANNEL_SPECS:
					params->buckets[i].num_channels =
					    nla_get_u32(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_BUCKET_MAX_PERIOD:
					params->buckets[i].max_period =
					    nla_get_u32(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_BUCKET_BASE:
					params->buckets[i].base =
					    nla_get_u32(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_BUCKET_STEP_COUNT:
					params->buckets[i].step_count =
					    nla_get_u32(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_CHAN_SPEC:
				j = 0;
				nla_for_each_nested(outer_iter1,
						    inner_iter,
					rem_outer_len1){
					nla_for_each_nested(inner_iter1,
							    outer_iter1,
							    rem_inner_len1) {
						type = nla_type(inner_iter1);
					switch (type) {
					case GSCAN_ATTR_CONFIG_CHANNEL_SPEC:
						params->buckets[i]
						.channels[j].channel =
						    nla_get_u32(inner_iter1);
					break;
					case GSCAN_ATTR_CONFIG_CHANNEL_DWELL_TIME:
						params->buckets[i]
						.channels[j].dwelltime =
						    nla_get_u32(inner_iter1);
					break;
					case GSCAN_ATTR_CONFIG_CHANNEL_PASSIVE:
						params->buckets[i]
						.channels[j].passive =
						    nla_get_u32(inner_iter1);
						break;
						}
					}
					j++;
					if (j >= MAX_CHANNELS)
						break;
				}
				break;

				default:
					netdev_err(vif->ndev,
						   "bucket nla type 0x%x not support\n",
						   type);
					ret = -EINVAL;
				break;
					}
				}
				if (ret < 0)
					break;
				i++;
				if (i >= MAX_BUCKETS)
					break;
			}
			break;

		default:
			netdev_err(vif->ndev, "nla type 0x%x not support\n",
				   type);
			ret = -EINVAL;
			break;
		}
	}

	netdev_info(vif->ndev, "parse config %s\n",
		    !ret ? "success" : "failture");

	kfree(vif->priv->gscan_res);
	vif->priv->gscan_buckets_num = params->num_buckets;
	tlen = sizeof(struct sprd_gscan_cached_results);

	/* malloc memory to store scan results */
	vif->priv->gscan_res =
	    kmalloc((u64)(vif->priv->gscan_buckets_num * tlen), GFP_KERNEL);

	if (!vif->priv->gscan_res) {
		kfree(params);
		return -ENOMEM;
	}

	memset(vif->priv->gscan_res, 0x0,
	       vif->priv->gscan_buckets_num *
	       sizeof(struct sprd_gscan_cached_results));

	tlen = sizeof(struct cmd_gscan_set_config);

	for (i = 0; i < params->num_buckets; i++) {
		if (params->buckets[i].num_channels == 0) {
			pr_err("%s, %d, gscan channel not set\n", __func__,
			       __LINE__);
			params->buckets[i].num_channels = 11;
			for (j = 0; j < 11; j++)
				params->buckets[i].channels[j].channel = j + 1;
		}
	}
	/* send scan params configure command */
	ret = sc2355_gscan_subcmd(vif->priv, vif,
				  (void *)params,
				  SPRD_GSCAN_SUBCMD_SET_CONFIG,
				  tlen, (u8 *)(&rsp), &rlen);
	if (ret == 0) {
		enable = 1;

		/* start gscan */
		ret = sc2355_gscan_subcmd(vif->priv, vif,
					  (void *)(&enable),
					  SPRD_GSCAN_SUBCMD_ENABLE_GSCAN,
					  sizeof(int), (u8 *)(&rsp), &rlen);
	}

	if (ret < 0)
		kfree(vif->priv->gscan_res);
	kfree(params);

	return ret;
}

/* stop gscan functon------ CMD ID:21 */
static int vendor_gscan_stop(struct wiphy *wiphy,
			     struct wireless_dev *wdev,
			     const void *data, int len)
{
	int enable;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	enable = 0;
	netdev_info(vif->ndev, "%s\n", __func__);

	return sc2355_gscan_subcmd(vif->priv, vif,
				   (void *)(&enable),
				   SPRD_GSCAN_SUBCMD_ENABLE_GSCAN,
				   sizeof(int), (u8 *)(&rsp), &rlen);
}

/* get valid channel list functon, need input band value------ CMD ID:22 */
static int vendor_get_channel_list(struct wiphy *wiphy,
				   struct wireless_dev *wdev,
				   const void *data, int len)
{
	int ret = 0, payload, request_id;
	int type;
	int band = 0, max_channel;
	int rem_len;
	struct nlattr *pos;
	struct cmd_gscan_channel_list channel_list;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct sk_buff *reply;
	u16 rlen;

	rlen = sizeof(struct cmd_gscan_channel_list)
	    + sizeof(struct cmd_gscan_rsp_header);

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		switch (type) {
		case GSCAN_ATTR_CONFIG_REQUEST_ID:
			request_id = nla_get_u32(pos);
			break;
		case GSCAN_ATTR_CONFIG_WIFI_BAND:
			band = nla_get_u32(pos);
			break;
		case GSCAN_ATTR_CONFIG_MAX_CHANNELS:
			max_channel = nla_get_u32(pos);
			break;
		default:
			netdev_err(vif->ndev, "nla type 0x%x not support\n",
				   type);
			ret = -EINVAL;
			break;
		}
		if (ret < 0)
			break;
	}

	netdev_info(vif->ndev, "parse channel list %s band=%d\n",
		    !ret ? "success" : "failture", band);

	payload = rlen + 0x100;
	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, payload);
	if (!reply) {
		ret = -ENOMEM;
		goto out;
	}
	if (band == VENDOR_GSCAN_WIFI_BAND_A) {
		channel_list.num_channels =
		    vif->priv->ch_5g_without_dfs_info.num_channels;
		memcpy(channel_list.channels,
		       vif->priv->ch_5g_without_dfs_info.channels,
		       sizeof(int) * channel_list.num_channels);
	} else if (band == VENDOR_GSCAN_WIFI_BAND_A_DFS) {
		channel_list.num_channels =
		    vif->priv->ch_5g_dfs_info.num_channels;
		memcpy(channel_list.channels,
		       vif->priv->ch_5g_dfs_info.channels,
		       sizeof(int) * channel_list.num_channels);
	} else {
		/* return 2.4G channel list by default */
		channel_list.num_channels = vif->priv->ch_2g4_info.num_channels;
		memcpy(channel_list.channels, vif->priv->ch_2g4_info.channels,
		       sizeof(int) * channel_list.num_channels);
	}

	if (nla_put_u32(reply, ATTR_GSCAN_RESULTS_NUM_CHANNELS,
			channel_list.num_channels))
		goto out_put_fail;
	if (nla_put(reply, ATTR_GSCAN_RESULTS_CHANNELS,
		    sizeof(int) * channel_list.num_channels,
		    channel_list.channels))

		goto out_put_fail;
	ret = cfg80211_vendor_cmd_reply(reply);
	if (ret)
		netdev_err(vif->ndev, "%s failed to reply skb!\n", __func__);

out:
	return ret;
out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

/* Gscan get capabilities function----CMD ID:23 */
static int vendor_get_gscan_capabilities(struct wiphy *wiphy,
					 struct wireless_dev *wdev,
					 const void *data, int len)
{
	u16 rlen;
	struct sk_buff *reply;
	int ret = 0, payload;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct cmd_gscan_rsp_header *hdr;
	struct gscan_capa *p = NULL;
	void *rbuf;

	pr_info("%s enter\n", __func__);

	rlen = sizeof(struct gscan_capa) +
	    sizeof(struct cmd_gscan_rsp_header);
	rbuf = kmalloc(rlen, GFP_KERNEL);
	if (!rbuf)
		return -ENOMEM;

	ret = sc2355_gscan_subcmd(vif->priv, vif,
				  NULL, SPRD_GSCAN_SUBCMD_GET_CAPABILITIES,
				  0, (u8 *)rbuf, &rlen);

	if (ret < 0) {
		netdev_err(vif->ndev, "%s failed to get capabilities!\n",
			   __func__);
		goto out;
	}
	hdr = (struct cmd_gscan_rsp_header *)rbuf;
	p = (struct gscan_capa *)
	    (rbuf + sizeof(struct cmd_gscan_rsp_header));
	pr_info("cache_size: %d scan_bucket:%d\n",
		p->max_scan_cache_size, p->max_scan_buckets);
	pr_info("max AP per scan:%d,max_rssi_sample_size:%d\n",
		p->max_ap_cache_per_scan, p->max_rssi_sample_size);
	pr_info("max_white_list:%d,max_black_list:%d\n",
		p->max_whitelist_ssid, p->max_blacklist_size);
	payload = rlen + 0x100;
	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, payload);
	if (!reply) {
		ret = -ENOMEM;
		goto out;
	}

	if (nla_put_u32(reply, ATTR_GSCAN_SCAN_CACHE_SIZE,
			p->max_scan_cache_size) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_SCAN_BUCKETS,
			p->max_scan_buckets) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_AP_CACHE_PER_SCAN,
			p->max_ap_cache_per_scan) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_RSSI_SAMPLE_SIZE,
			p->max_rssi_sample_size) ||
	    nla_put_s32(reply, ATTR_GSCAN_MAX_SCAN_REPORTING_THRESHOLD,
			p->max_scan_reporting_threshold) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_HOTLIST_BSSIDS,
			p->max_hotlist_bssids) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_SIGNIFICANT_WIFI_CHANGE_APS,
			p->max_significant_wifi_change_aps) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_BSSID_HISTORY_ENTRIES,
			p->max_bssid_history_entries) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_HOTLIST_SSIDS,
			p->max_hotlist_bssids) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_NUM_EPNO_NETS,
			p->max_number_epno_networks) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_NUM_EPNO_NETS_BY_SSID,
			p->max_number_epno_networks_by_ssid) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_NUM_WHITELISTED_SSID,
			p->max_whitelist_ssid) ||
	    nla_put_u32(reply, ATTR_GSCAN_MAX_NUM_BLACKLISTED_BSSID,
			p->max_blacklist_size)) {
		pr_err("failed to put Gscan capabilies\n");
		goto out_put_fail;
	}
	vif->priv->roam_capa.max_blacklist_size = p->max_blacklist_size;
	vif->priv->roam_capa.max_whitelist_size = p->max_whitelist_ssid;

	ret = cfg80211_vendor_cmd_reply(reply);
	if (ret)
		netdev_err(vif->ndev, "%s failed to reply skb!\n", __func__);
out:
	kfree(rbuf);
	return ret;
out_put_fail:
	kfree_skb(reply);
	kfree(rbuf);
	return ret;
}

/* get cached gscan results functon------ CMD ID:24 */
static int vendor_get_cached_gscan_results(struct wiphy *wiphy,
					   struct wireless_dev *wdev,
					   const void *data, int len)
{
	int ret = 0, i, j, rlen, payload, request_id = 0, moredata = 0;
	int rem_len, type, flush = 0, max_param = 0, n, buckets_scanned = 1;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct sk_buff *reply;
	struct nlattr *pos, *scan_res, *cached_list, *res_list;
	struct nlattr *ap;
	struct sprd_gscan_cached_results *p;

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		switch (type) {
		case GSCAN_ATTR_CONFIG_REQUEST_ID:
			request_id = nla_get_u32(pos);
			break;
		case GSCAN_ATTR_CONFIG_CACHED_PARAM_FLUSH:
			flush = nla_get_u32(pos);
			break;
		case GSCAN_ATTR_CONFIG_CACHED_PARAM_MAX:
			max_param = nla_get_u32(pos);
			break;
		default:
			netdev_err(vif->ndev,
				   "nla gscan result 0x%x not support\n", type);
			ret = -EINVAL;
			break;
		}
		if (ret < 0)
			break;
	}

	rlen = vif->priv->gscan_buckets_num
	    * sizeof(struct sprd_gscan_cached_results);
	payload = rlen + 0x100;
	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, payload);
	if (!reply)
		return -ENOMEM;
	for (i = 0; i < vif->priv->gscan_buckets_num; i++) {
		if (!(vif->priv->gscan_res + i)->num_results)
			continue;

		for (j = 0; j <= (vif->priv->gscan_res + i)->num_results; j++) {
			if (time_after(jiffies - VENDOR_SCAN_RESULT_EXPIRE,
				       (vif->priv->gscan_res + i)->results[j].ts)) {
				memcpy((void *)
				(&(vif->priv->gscan_res + i)->results[j]),
				(void *)
				(&(vif->priv->gscan_res + i)->results[j + 1]),
				sizeof(struct gscan_result)
				* ((vif->priv->gscan_res + i)->num_results
				- j - 1));
				(vif->priv->gscan_res + i)->num_results--;
				j = 0;
			}
		}

		if (nla_put_u32(reply, ATTR_GSCAN_RESULTS_REQUEST_ID,
				request_id) ||
		    nla_put_u32(reply,
				ATTR_GSCAN_RESULTS_NUM_RESULTS_AVAILABLE,
				(vif->priv->gscan_res + i)->num_results)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}

		if (nla_put_u8(reply,
			       ATTR_GSCAN_RESULTS_SCAN_RESULT_MORE_DATA, moredata)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}

		if (nla_put_u32(reply,
				ATTR_GSCAN_CACHED_RESULTS_SCAN_ID,
				(vif->priv->gscan_res + i)->scan_id)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}

		if ((vif->priv->gscan_res + i)->num_results == 0)
			break;

		cached_list = nla_nest_start(reply, ATTR_GSCAN_CACHED_RESULTS_LIST);
		for (n = 0; n < vif->priv->gscan_buckets_num; n++) {
			res_list = nla_nest_start(reply, n);

			if (!res_list)
				goto out_put_fail;

			if (nla_put_u32(reply,
					ATTR_GSCAN_CACHED_RESULTS_SCAN_ID,
					(vif->priv->gscan_res + i)->scan_id)) {
				netdev_err(vif->ndev, "failed to put!\n");
				goto out_put_fail;
			}

			if (nla_put_u32(reply,
					ATTR_GSCAN_CACHED_RESULTS_FLAGS,
					(vif->priv->gscan_res + i)->flags)) {
				netdev_err(vif->ndev, "failed to put!\n");
				goto out_put_fail;
			}

			if (nla_put_u32(reply,
					ATTR_GSCAN_RESULTS_BUCKETS_SCANNED,
					buckets_scanned)) {
				netdev_err(vif->ndev, "failed to put!\n");
				goto out_put_fail;
			}

			if (nla_put_u32(reply,
					ATTR_GSCAN_RESULTS_NUM_RESULTS_AVAILABLE,
				(vif->priv->gscan_res + i)->num_results)) {
				netdev_err(vif->ndev, "failed to put!\n");
				goto out_put_fail;
			}

			scan_res = nla_nest_start(reply, ATTR_GSCAN_RESULTS_LIST);
			if (!scan_res)
				goto out_put_fail;

			for (j = 0;
			     j < (vif->priv->gscan_res + i)->num_results; j++) {
				p = vif->priv->gscan_res + i;
				pr_info("[index=%d] Timestamp(%lu) Ssid (%s) Bssid: %pM Channel (%d) Rssi (%d) RTT (%u) RTT_SD (%u)\n",
					j,
					p->results[j].ts,
					p->results[j].ssid,
					p->results[j].bssid,
					p->results[j].channel,
					p->results[j].rssi,
					p->results[j].rtt,
					p->results[j].rtt_sd);

				ap = nla_nest_start(reply, j + 1);
				if (!ap) {
					netdev_err(vif->ndev,
						   "failed to put!\n");
					goto out_put_fail;
				}
				if (nla_put_u64_64bit(reply,
					ATTR_GSCAN_RESULTS_SCAN_RESULT_TIME_STAMP,
					p->results[j].ts, 0)) {
					netdev_err(vif->ndev, "failed to put!\n");
					goto out_put_fail;
				}
				if (nla_put(reply,
					    ATTR_GSCAN_RESULTS_SCAN_RESULT_SSID,
					    sizeof(p->results[j].ssid),
					    p->results[j].ssid)) {
					netdev_err(vif->ndev,
						   "failed to put!\n");
					goto out_put_fail;
				}
				if (nla_put(reply,
					    ATTR_GSCAN_RESULTS_SCAN_RESULT_BSSID,
					    sizeof(p->results[j].bssid),
					    p->results[j].bssid)) {
					netdev_err(vif->ndev,
						   "failed to put!\n");
					goto out_put_fail;
				}
				if (nla_put_u32(reply,
					ATTR_GSCAN_RESULTS_SCAN_RESULT_CHANNEL,
					p->results[j].channel)) {
					netdev_err(vif->ndev,
						   "failed to put!\n");
					goto out_put_fail;
				}
				if (nla_put_s32(reply,
						ATTR_GSCAN_RESULTS_SCAN_RESULT_RSSI,
						p->results[j].rssi)) {
					netdev_err(vif->ndev,
						   "failed to put!\n");
					goto out_put_fail;
				}
				if (nla_put_u32(reply,
						ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT,
						p->results[j].rtt)) {
					netdev_err(vif->ndev,
						   "failed to put!\n");
					goto out_put_fail;
				}
				if (nla_put_u32(reply,
					ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT_SD,
					p->results[j].rtt_sd)) {
					netdev_err(vif->ndev,
						   "failed to put!\n");
					goto out_put_fail;
				}
				nla_nest_end(reply, ap);
			}
			nla_nest_end(reply, scan_res);
			nla_nest_end(reply, res_list);
		}
		nla_nest_end(reply, cached_list);
	}

	ret = cfg80211_vendor_cmd_reply(reply);
	if (ret < 0)
		netdev_err(vif->ndev, "%s failed to reply skb!\n", __func__);
	return ret;

out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

/* set_ssid_hotlist function---CMD ID:29 */
static int vendor_set_bssid_hotlist(struct wiphy *wiphy,
				    struct wireless_dev *wdev,
				    const void *data, int len)
{
	int i, ret = 0, tlen;
	int type;
	int rem_len, rem_outer_len, rem_inner_len;
	struct nlattr *pos, *outer_iter, *inner_iter;
	struct wifi_bssid_hotlist_params *bssid_hotlist_params;
	struct cmd_gscan_rsp_header rsp;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	bssid_hotlist_params =
	    kmalloc(sizeof(*bssid_hotlist_params), GFP_KERNEL);

	if (!bssid_hotlist_params)
		return -ENOMEM;

	vif->priv->hotlist_res =
	    kmalloc(sizeof(struct sprd_gscan_hotlist_results), GFP_KERNEL);

	if (!vif->priv->hotlist_res) {
		ret = -ENOMEM;
		goto out;
	}

	memset(vif->priv->hotlist_res, 0x0,
	       sizeof(struct sprd_gscan_hotlist_results));

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);

		switch (type) {
		case GSCAN_ATTR_CONFIG_REQUEST_ID:
			vif->priv->hotlist_res->req_id = nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_BSSID_HOTLIST_LOST_AP_SAMPLE_SIZE:
			bssid_hotlist_params->lost_ap_sample_size =
			    nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_BSSID_HOTLIST_NUM_AP:
			bssid_hotlist_params->num_bssid = nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_AP_THR_PARAM:
			i = 0;
			nla_for_each_nested(outer_iter, pos, rem_outer_len) {
				nla_for_each_nested(inner_iter, outer_iter,
						    rem_inner_len) {
					type = nla_type(inner_iter);
					switch (type) {
					case GSCAN_ATTR_CONFIG_AP_THR_BSSID:
						if (nla_len(inner_iter) < 6 * sizeof(unsigned char)) {
							netdev_err(vif->ndev,
								"nla_data for networks nla type 0x%x not support\n",
								type);
							ret = -EINVAL;
						} else {
							memcpy(bssid_hotlist_params->ap[i].bssid,
								nla_data(inner_iter),
								6 * sizeof(unsigned char));
						}
					break;

					case GSCAN_ATTR_CONFIG_AP_THR_RSSI_LOW:
						bssid_hotlist_params->ap[i].low
						    = nla_get_s32(inner_iter);
						break;

					case GSCAN_ATTR_CONFIG_AP_THR_RSSI_HIGH:
						bssid_hotlist_params->ap[i].high
						    = nla_get_s32(inner_iter);
						break;
					default:
						netdev_err(vif->ndev,
							   "networks nla type 0x%x not support\n",
							   type);
						ret = -EINVAL;
						break;
					}
				}

				if (ret < 0)
					break;

				i++;
				if (i >= MAX_HOTLIST_APS)
					break;
			}
			break;

		default:
			netdev_err(vif->ndev, "nla type 0x%x not support\n",
				   type);
			ret = -EINVAL;
			break;
		}

		if (ret < 0)
			break;
	}

	netdev_info(vif->ndev, "parse bssid hotlist %s\n",
		    !ret ? "success" : "failture");

	tlen = sizeof(struct wifi_bssid_hotlist_params);

	if (!ret)
		ret = sc2355_gscan_subcmd(vif->priv, vif,
					  (void *)bssid_hotlist_params,
					  SPRD_GSCAN_SUBCMD_SET_HOTLIST,
					  tlen, (u8 *)(&rsp), &rlen);

	if (ret < 0)
		kfree(vif->priv->hotlist_res);

out:
	kfree(bssid_hotlist_params);
	return ret;
}

/* reset_bssid_hotlist function---CMD ID:30 */
static int vendor_reset_bssid_hotlist(struct wiphy *wiphy,
				      struct wireless_dev *wdev,
				      const void *data, int len)
{
	int flush = 1;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	netdev_info(vif->ndev, "%s %d\n", __func__, flush);

	memset(vif->priv->hotlist_res, 0x0,
	       sizeof(struct sprd_gscan_hotlist_results));

	return sc2355_gscan_subcmd(vif->priv, vif,
				   (void *)(&flush),
				   SPRD_GSCAN_SUBCMD_RESET_HOTLIST,
				   sizeof(int), (u8 *)(&rsp), &rlen);
}

/* set_significant_change function---CMD ID:32 */
static int vendor_set_significant_change(struct wiphy *wiphy,
					 struct wireless_dev *wdev,
					 const void *data, int len)
{
	int i, ret = 0, tlen;
	int type;
	int rem_len, rem_outer_len, rem_inner_len;
	struct nlattr *pos, *outer_iter, *inner_iter;
	struct wifi_significant_change_params *significant_change_params;
	struct cmd_gscan_rsp_header rsp;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	significant_change_params =
	    kmalloc(sizeof(*significant_change_params), GFP_KERNEL);

	if (!significant_change_params)
		return -ENOMEM;

	vif->priv->significant_res =
	    kmalloc(sizeof(struct sprd_significant_change_result),
		    GFP_KERNEL);

	if (!vif->priv->significant_res) {
		ret = -ENOMEM;
		goto out;
	}

	memset(vif->priv->significant_res, 0x0,
	       sizeof(struct sprd_significant_change_result));

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		switch (type) {
		case GSCAN_ATTR_CONFIG_REQUEST_ID:
			vif->priv->significant_res->req_id = nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_SIGNIFICANT_RSSI_SAMPLE_SIZE:
			significant_change_params->rssi_sample_size =
			    nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_SIGNIFICANT_LOST_AP_SAMPLE_SIZE:
			significant_change_params->lost_ap_sample_size =
			    nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_SIGNIFICANT_MIN_BREACHING:
			significant_change_params->min_breaching =
			    nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_SIGNIFICANT_NUM_AP:
			significant_change_params->num_bssid = nla_get_s32(pos);
			/* the max num in cp is 8 */
			if (significant_change_params->num_bssid > 8) {
				kfree(significant_change_params);
				return -EINVAL;
			}
			break;

		case GSCAN_ATTR_CONFIG_AP_THR_PARAM:
		i = 0;
		nla_for_each_nested(outer_iter, pos, rem_outer_len) {
			nla_for_each_nested(inner_iter, outer_iter,
					    rem_inner_len) {
				type = nla_type(inner_iter);
				switch (type) {
				case GSCAN_ATTR_CONFIG_AP_THR_BSSID:
					if (nla_len(inner_iter) < 6 * sizeof(unsigned char)) {
						netdev_err(vif->ndev,
							"nla_data for networks nla type 0x%x not support\n",
							type);
						ret = -EINVAL;
					} else {
						memcpy(significant_change_params->ap[i].bssid,
							nla_data(inner_iter),
							6 * sizeof(unsigned char));
					}
				break;

				case GSCAN_ATTR_CONFIG_AP_THR_RSSI_LOW:
					significant_change_params->ap[i].low =
					    nla_get_s32(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_AP_THR_RSSI_HIGH:
					significant_change_params->ap[i].high =
					    nla_get_s32(inner_iter);
				break;
				default:
					netdev_err(vif->ndev,
						   "networks nla type 0x%x not support\n",
					type);
					ret = -EINVAL;
				break;
				}
			}
			if (ret < 0)
				break;
			i++;
			if (i >= MAX_SIGNIFICANT_CHANGE_APS)
				break;
		}
		break;

		default:
		netdev_err(vif->ndev, "nla type 0x%x not support\n", type);
		ret = -EINVAL;
		break;
		}

		if (ret < 0)
			break;
	}

	tlen = sizeof(struct wifi_significant_change_params);
	ret = sc2355_gscan_subcmd(vif->priv, vif,
				  (void *)significant_change_params,
			SPRD_GSCAN_SUBCMD_SET_SIGNIFICANT_CHANGE_CONFIG,
			tlen, (u8 *)(&rsp), &rlen);

	if (ret < 0)
		kfree(vif->priv->significant_res);

out:
	kfree(significant_change_params);
	return ret;
}

/* set_significant_change function---CMD ID:33 */
static int vendor_reset_significant_change(struct wiphy *wiphy,
					   struct wireless_dev *wdev,
					   const void *data, int len)
{
	int flush = 1;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	netdev_info(vif->ndev, "%s %d\n", __func__, flush);

	if (vif->priv->significant_res) {
		memset(vif->priv->significant_res, 0x0,
		       sizeof(struct sprd_significant_change_result));
	}

	return sc2355_gscan_subcmd(vif->priv, vif,
		(void *)(&flush),
		SPRD_GSCAN_SUBCMD_RESET_SIGNIFICANT_CHANGE_CONFIG,
		sizeof(int), (u8 *)(&rsp), &rlen);
}

/* get support feature function---CMD ID:38 */
static int vendor_get_support_feature(struct wiphy *wiphy,
				      struct wireless_dev *wdev,
				      const void *data, int len)
{
	int ret;
	struct sk_buff *reply;
	u32 feature = 0, payload;
	struct sprd_priv *priv = wiphy_priv(wiphy);

	wiphy_info(wiphy, "%s\n", __func__);
	payload = sizeof(feature);
	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, payload);

	if (!reply)
		return -ENOMEM;
	/* bit 1:Basic infrastructure mode */
	if (wiphy->interface_modes & BIT(NL80211_IFTYPE_STATION)) {
		pr_info("STA mode is supported\n");
		feature |= WIFI_FEATURE_INFRA;
	}
	/* bit 2:Support for 5 GHz Band */
	if (priv->fw_capa & SPRD_CAPA_5G) {
		pr_info("INFRA 5G is supported\n");
		feature |= WIFI_FEATURE_INFRA_5G;
	}
	/* bit3:HOTSPOT is a supplicant feature, enable it by default */
	pr_info("HotSpot feature is supported\n");
	feature |= WIFI_FEATURE_HOTSPOT;
	/* bit 4:P2P */
	if ((wiphy->interface_modes & BIT(NL80211_IFTYPE_P2P_CLIENT)) &&
	    (wiphy->interface_modes & BIT(NL80211_IFTYPE_P2P_GO))) {
		pr_info("P2P is supported\n");
		feature |= WIFI_FEATURE_P2P;
	}
	/* bit 5:soft AP feature supported */
	if (wiphy->interface_modes & BIT(NL80211_IFTYPE_AP)) {
		pr_info("Soft AP is supported\n");
		feature |= WIFI_FEATURE_SOFT_AP;
	}
	/* bit 6:GSCAN feature supported */
	if (priv->fw_capa & SPRD_CAPA_GSCAN) {
		pr_info("GSCAN feature supported\n");
		feature |= WIFI_FEATURE_GSCAN;
	}
	/* bit 7:NAN feature supported */
	if (priv->fw_capa & SPRD_CAPA_NAN) {
		pr_info("NAN is supported\n");
		feature |= WIFI_FEATURE_NAN;
	}
	/* bit 8: Device-to-device RTT */
	if (priv->fw_capa & SPRD_CAPA_D2D_RTT) {
		pr_info("D2D RTT supported\n");
		feature |= WIFI_FEATURE_D2D_RTT;
	}
	/* bit 9: Device-to-AP RTT */
	if (priv->fw_capa & SPRD_CAPA_D2AP_RTT) {
		pr_info("Device-to-AP RTT supported\n");
		feature |= WIFI_FEATURE_D2AP_RTT;
	}
	/* bit 10: Batched Scan (legacy) */
	if (priv->fw_capa & SPRD_CAPA_BATCH_SCAN) {
		pr_info("Batched Scan supported\n");
		feature |= WIFI_FEATURE_BATCH_SCAN;
	}
	/* bit 11: PNO feature supported */
	if (priv->fw_capa & SPRD_CAPA_PNO) {
		pr_info("PNO feature supported\n");
		feature |= WIFI_FEATURE_PNO;
	}
	/* bit 12:Support for two STAs */
	if (priv->fw_capa & SPRD_CAPA_ADDITIONAL_STA) {
		pr_info("Two sta feature supported\n");
		feature |= WIFI_FEATURE_ADDITIONAL_STA;
	}
	/* bit 13:Tunnel directed link setup */
	if (priv->fw_capa & SPRD_CAPA_TDLS) {
		pr_info("TDLS feature supported\n");
		feature |= WIFI_FEATURE_TDLS;
	}
	/* bit 14:Support for TDLS off channel */
	if (priv->fw_capa & SPRD_CAPA_TDLS_OFFCHANNEL) {
		pr_info("TDLS off channel supported\n");
		feature |= WIFI_FEATURE_TDLS_OFFCHANNEL;
	}
	/* bit 15:Enhanced power reporting */
	if (priv->fw_capa & SPRD_CAPA_EPR) {
		pr_info("Enhanced power report supported\n");
		feature |= WIFI_FEATURE_EPR;
	}
	/* bit 16:Support for AP STA Concurrency */
	if (priv->fw_capa & SPRD_CAPA_AP_STA) {
		pr_info("AP STA Concurrency supported\n");
		feature |= WIFI_FEATURE_AP_STA;
	}
	/* bit 17:Link layer stats collection */
	if (priv->fw_capa & SPRD_CAPA_LL_STATS) {
		pr_info("LinkLayer status supported\n");
		feature |= WIFI_FEATURE_LINK_LAYER_STATS;
	}
	/* bit 18:WiFi Logger */
	if (priv->fw_capa & SPRD_CAPA_WIFI_LOGGER) {
		pr_info("WiFi Logger supported\n");
		feature |= WIFI_FEATURE_LOGGER;
	}
	/* bit 19:WiFi PNO enhanced */
	if (priv->fw_capa & SPRD_CAPA_EPNO) {
		pr_info("WIFI ENPO supported\n");
		feature |= WIFI_FEATURE_HAL_EPNO;
	}
	/* bit 20:RSSI monitor supported */
	if (priv->fw_capa & SPRD_CAPA_RSSI_MONITOR) {
		pr_info("RSSI Monitor supported\n");
		feature |= WIFI_FEATURE_RSSI_MONITOR;
	}
	/* bit 21:WiFi mkeep_alive */
	if (priv->fw_capa & SPRD_CAPA_MKEEP_ALIVE) {
		pr_info("WiFi mkeep alive supported\n");
		feature |= WIFI_FEATURE_MKEEP_ALIVE;
	}
	/* bit 22:ND offload configure */
	if (priv->fw_capa & SPRD_CAPA_CONFIG_NDO) {
		pr_info("ND offload supported\n");
		feature |= WIFI_FEATURE_CONFIG_NDO;
	}
	/* bit 23:Capture Tx transmit power levels */
	if (priv->fw_capa & SPRD_CAPA_TX_POWER) {
		pr_info("Tx power supported\n");
		feature |= WIFI_FEATURE_TX_TRANSMIT_POWER;
	}
	/* bit 24:Enable/Disable firmware roaming */
	if ((priv->fw_capa & SPRD_CAPA_11R_ROAM_OFFLOAD) &&
	    (priv->fw_capa & SPRD_CAPA_GSCAN)) {
		pr_info("ROAMING offload supported\n");
		feature |= WIFI_FEATURE_CONTROL_ROAMING;
	}
	/* bit 25:Support Probe IE white listing */
	if (priv->fw_capa & SPRD_CAPA_IE_WHITELIST) {
		pr_info("Probe IE white listing supported\n");
		feature |= WIFI_FEATURE_IE_WHITELIST;
	}
	/* bit 26: Support MAC & Probe Sequence Number randomization */
	if (priv->fw_capa & SPRD_CAPA_SCAN_RAND) {
		pr_info("RAND MAC SCAN supported\n");
		feature |= WIFI_FEATURE_SCAN_RAND;
	}
	/* bit 27: Support SET sar limit function */
	if (priv->extend_feature & SPRD_CAPA_TX_POWER) {
		pr_info("Set sar limit function supported\n");
		feature |= WIFI_FEATURE_SET_SAR_LIMIT;
	}

	pr_info("Supported Feature:0x%x\n", feature);

	if (nla_put_u32(reply, ATTR_FEATURE_SET, feature)) {
		wiphy_err(wiphy, "%s put u32 error\n", __func__);
		goto out_put_fail;
	}

	ret = cfg80211_vendor_cmd_reply(reply);
	if (ret)
		wiphy_err(wiphy, "%s reply cmd error\n", __func__);
	return ret;

out_put_fail:
	kfree_skb(reply);
	return -EMSGSIZE;
}

/* set_mac_oui functon------ CMD ID:39 */
static int vendor_set_mac_oui(struct wiphy *wiphy,
			      struct wireless_dev *wdev,
			      const void *data, int len)
{
	struct nlattr *pos;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct v_MACADDR_t *rand_mac;
	int tlen = 0, ret = 0, rem_len, type;
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	wiphy_info(wiphy, "%s\n", __func__);

	rand_mac = kmalloc(sizeof(*rand_mac), GFP_KERNEL);
	if (!rand_mac)
		return -ENOMEM;

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		switch (type) {
		case ATTR_SET_SCANNING_MAC_OUI:
			if (nla_len(pos) < sizeof(struct v_MACADDR_t)) {
				netdev_err(vif->ndev, "nla_data for nla type 0x%x not support\n",
					type);
				ret = -EINVAL;
				goto out;
			}
			memcpy(rand_mac, nla_data(pos), 3);
			break;

		default:
			netdev_err(vif->ndev, "nla type 0x%x not support\n",
				   type);
			ret = -EINVAL;
			goto out;
		}
	}

	tlen = sizeof(struct v_MACADDR_t);
	ret = sc2355_gscan_subcmd(vif->priv, vif,
				  (void *)rand_mac,
				  SPRD_WIFI_SUBCMD_SET_PNO_RANDOM_MAC_OUI,
				  tlen, (u8 *)(&rsp), &rlen);

out:
	kfree(rand_mac);
	return ret;
}

/* get concurrency matrix function---CMD ID:42
 * vendor_get_concurrency_matrix() - to retrieve concurrency matrix
 * @wiphy: pointer phy adapter
 * @wdev: pointer to wireless device structure
 * @data: pointer to data buffer
 * @data: length of data
 *
 * This routine will give concurrency matrix
 *
 * Return: int status code
 */

static int vendor_get_concurrency_matrix(struct wiphy *wiphy,
					 struct wireless_dev *wdev,
					 const void *data, int len)
{
	u32 feature_set_matrix[CDS_MAX_FEATURE_SET] = { 0 };
	u8 i, feature_sets, max_feature_sets;
	struct nlattr *tb[ATTR_CO_MATRIX_MAX + 1];
	struct sk_buff *reply_skb;

	if (nla_parse(tb, ATTR_CO_MATRIX_MAX, data, len, NULL, NULL)) {
		pr_err("Invalid ATTR\n");
		return -EINVAL;
	}

	/* Parse and fetch max feature set */
	if (!tb[ATTR_CO_MATRIX_CONFIG_PARAM_SET_SIZE_MAX]) {
		pr_err("Attr max feature set size failed\n");
		return -EINVAL;
	}
	max_feature_sets =
	    nla_get_u32(tb[ATTR_CO_MATRIX_CONFIG_PARAM_SET_SIZE_MAX]);

	pr_info("Max feature set size (%d)", max_feature_sets);

	/* Fill feature combination matrix */
	feature_sets = 0;
	feature_set_matrix[feature_sets++] =
	    WIFI_FEATURE_INFRA | WIFI_FEATURE_P2P;
	feature_set_matrix[feature_sets++] =
	    WIFI_FEATURE_INFRA_5G | WIFI_FEATURE_P2P;
	feature_set_matrix[feature_sets++] =
	    WIFI_FEATURE_INFRA | WIFI_FEATURE_GSCAN;
	feature_set_matrix[feature_sets++] =
	    WIFI_FEATURE_INFRA_5G | WIFI_FEATURE_GSCAN;

	feature_sets = min(feature_sets, max_feature_sets);
	pr_info("Number of feature sets (%d)\n", feature_sets);

	pr_info("Feature set matrix:");
	for (i = 0; i < feature_sets; i++)
		pr_info("[%d] 0x%02X", i, feature_set_matrix[i]);

	reply_skb = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, sizeof(u32) +
							sizeof(u32) *
							feature_sets);

	if (reply_skb) {
		if (nla_put_u32(reply_skb,
				ATTR_CO_MATRIX_RESULTS_SET_SIZE,
				feature_sets) ||
		    nla_put(reply_skb,
			    ATTR_CO_MATRIX_RESULTS_SET,
			    sizeof(u32) * feature_sets, feature_set_matrix)) {
			pr_err("nla put failure\n");
			kfree_skb(reply_skb);
			return -EINVAL;
		}
		return cfg80211_vendor_cmd_reply(reply_skb);
	}
	pr_err("set matrix: buffer alloc failure\n");
	return -ENOMEM;
}

/* get support feature function---CMD ID:55 */
static int vendor_get_feature(struct wiphy *wiphy,
			      struct wireless_dev *wdev,
			      const void *data, int len)
{
	return 0;
}

/* get wake up reason statistic */
static int vendor_get_wake_state(struct wiphy *wiphy,
				 struct wireless_dev *wdev,
				 const void *data, int len)
{
	struct sk_buff *skb;
	u32 buf_len;

	wiphy_info(wiphy, "%s\n", __func__);
	buf_len = NLMSG_HDRLEN;
	buf_len += ATTR_WAKE_MAX * (NLMSG_HDRLEN + sizeof(u32));
	skb = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, buf_len);

	if (!skb) {
		pr_err("cfg80211_vendor_cmd_alloc_reply_skb failed\n");
		return -ENOMEM;
	}

	if (nla_put_u32(skb, ATTR_WAKE_CMD_EVT_WAKE_CNT_PTR, 0) ||
	    nla_put_u32(skb, ATTR_WAKE_CMD_EVT_WAKE_CNT_SZ, 0) ||
	    nla_put_u32(skb, ATTR_WAKE_DRV_FW_LOCAL_WAKE_CNT_PTR, 0) ||
	    nla_put_u32(skb, ATTR_WAKE_DRV_FW_LOCAL_WAKE_CNT_SZ, 0)) {
		pr_err("nla put failure\n");
		goto nla_put_failure;
	}
	if (cfg80211_vendor_cmd_reply(skb))
		pr_err("cfg80211_vendor_cmd_reply failed\n");

	return VENDOR_WIFI_SUCCESS;

nla_put_failure:
	kfree_skb(skb);
	return -EINVAL;
}

static int vendor_enable_nd_offload(struct wiphy *wiphy,
				    struct wireless_dev *wdev,
				    const void *data, int len)
{
	wiphy_info(wiphy, "%s\n", __func__);

	return VENDOR_WIFI_SUCCESS;
}

static int vendor_start_logging(struct wiphy *wiphy,
				struct wireless_dev *wdev,
				const void *data, int len)
{
	wiphy_info(wiphy, "%s\n", __func__);

	return VENDOR_WIFI_SUCCESS;
}

static int vendor_get_ring_data(struct wiphy *wiphy,
				struct wireless_dev *wdev,
				const void *data, int len)
{
	wiphy_info(wiphy, "%s\n", __func__);

	return VENDOR_WIFI_SUCCESS;
}

static int vendor_memory_dump(struct wiphy *wiphy,
			      struct wireless_dev *wdev,
			      const void *data, int len)
{
	wiphy_info(wiphy, "%s\n", __func__);

	return -EOPNOTSUPP;
}

/* CMD ID:61 */
static int vendor_get_driver_info(struct wiphy *wiphy,
				  struct wireless_dev *wdev,
				  const void *data, int len)
{
	int ret, payload = 0;
	struct sk_buff *reply;
	u8 attr;
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct nlattr *tb_vendor[ATTR_WIFI_INFO_GET_MAX + 1];
	char version[89];

	pr_info("%s enter\n", __func__);
	if (nla_parse(tb_vendor, ATTR_WIFI_INFO_GET_MAX, data,
		      len, get_wifi_info_policy, NULL)) {
		pr_err("WIFI_INFO_GET CMD parsing failed\n");
		return -EINVAL;
	}

	if (tb_vendor[ATTR_WIFI_INFO_DRIVER_VERSION]) {
		pr_info("Recived req for Drv version\n");
		memcpy(version, &priv->wl_ver, sizeof(version));
		attr = ATTR_WIFI_INFO_DRIVER_VERSION;
		payload = sizeof(priv->wl_ver);
	} else if (tb_vendor[ATTR_WIFI_INFO_FIRMWARE_VERSION]) {
		pr_info("Recived req for FW version\n");
		snprintf(version, sizeof(version), "%d", priv->fw_ver);
		pr_info("fw version:%s\n", version);
		attr = ATTR_WIFI_INFO_FIRMWARE_VERSION;
		payload = strlen(version);
	}

	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, payload);

	if (!reply)
		return -ENOMEM;

	if (nla_put(reply, ATTR_WIFI_INFO_DRIVER_VERSION,
		    payload, version)) {
		wiphy_err(wiphy, "%s put version error\n", __func__);
		goto out_put_fail;
	}

	ret = cfg80211_vendor_cmd_reply(reply);
	if (ret)
		wiphy_err(wiphy, "%s reply cmd error\n", __func__);
	return ret;

out_put_fail:
	kfree_skb(reply);
	return -EMSGSIZE;
}

/* Roaming function---CMD ID:64 */
static int vendor_set_roam_params(struct wiphy *wiphy,
				  struct wireless_dev *wdev,
				  const void *data, int len)
{
	u32 cmd_type, req_id;
	struct roam_white_list_params white_params;
	struct roam_black_list_params black_params;
	struct nlattr *curr_attr;
	struct nlattr *tb[ATTR_ROAM_MAX + 1];
	struct nlattr *tb2[ATTR_ROAM_MAX + 1];
	int rem, i;
	int white_limit = 0, black_limit = 0;
	int fw_max_whitelist = 0, fw_max_blacklist = 0;
	u32 buf_len = 0;
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_priv *priv = wiphy_priv(wiphy);
	int ret = 0;

	memset(&white_params, 0, sizeof(white_params));
	memset(&black_params, 0, sizeof(black_params));
	if (nla_parse(tb, ATTR_ROAM_MAX, data, len, NULL, NULL)) {
		pr_err("Invalid ATTR\n");
		return -EINVAL;
	}
	/* Parse and fetch Command Type */
	if (!tb[ATTR_ROAM_SUBCMD]) {
		pr_err("roam cmd type failed\n");
		goto fail;
	}

	cmd_type = nla_get_u32(tb[ATTR_ROAM_SUBCMD]);
	if (!tb[ATTR_ROAM_REQ_ID]) {
		pr_err("%s:attr request id failed\n", __func__);
		goto fail;
	}
	req_id = nla_get_u32(tb[ATTR_ROAM_REQ_ID]);
	pr_info("Req ID:%d, Cmd Type:%d", req_id, cmd_type);
	switch (cmd_type) {
	case ATTR_ROAM_SUBCMD_SSID_WHITE_LIST:
		if (!tb[ATTR_ROAM_WHITE_LIST_SSID_LIST])
			break;
		i = 0;
		nla_for_each_nested(curr_attr,
				    tb[ATTR_ROAM_WHITE_LIST_SSID_LIST], rem) {
			if (nla_parse(tb2, ATTR_ROAM_SUBCMD_MAX,
				      nla_data(curr_attr),
				      nla_len(curr_attr), NULL, NULL)) {
				pr_err("nla parse failed\n");
				goto fail;
			}
			/* Parse and Fetch allowed SSID list */
			if (!tb2[ATTR_ROAM_WHITE_LIST_SSID]) {
				pr_err("attr allowed ssid failed\n");
				goto fail;
			}
			buf_len = nla_len(tb2[ATTR_ROAM_WHITE_LIST_SSID]);
			/* Upper Layers include a null termination character.
			 * Check for the actual permissible length of SSID and
			 * also ensure not to copy the NULL termination
			 * character to the driver buffer.
			 */
			fw_max_whitelist = priv->roam_capa.max_whitelist_size;
			white_limit = min(fw_max_whitelist, MAX_WHITE_SSID);

			if (buf_len && i < white_limit &&
			    ((buf_len - 1) <= IEEE80211_MAX_SSID_LEN)) {
				nla_memcpy(white_params.white_list[i].ssid_str,
					   tb2[ATTR_ROAM_WHITE_LIST_SSID],
					   buf_len - 1);
				white_params.white_list[i].length = buf_len - 1;
				pr_info("SSID[%d]:%.*s, length=%d\n", i,
					white_params.white_list[i].length,
					white_params.white_list[i].ssid_str,
					white_params.white_list[i].length);
				i++;
			} else {
				pr_err("Invalid buffer length\n");
			}
		}
		white_params.num_white_ssid = i;
		pr_info("Num of white list:%d", i);
		/* send white list with roam params by roaming CMD */
		ret = sc2355_set_roam_offload(priv, vif,
					      SPRD_ROAM_SET_WHITE_LIST,
					      (u8 *)&white_params,
					      (i * sizeof(struct ssid_t) + 1));
		break;
	case ATTR_ROAM_SUBCMD_SET_BLACKLIST_BSSID:
		/* Parse and fetch number of blacklist BSSID */
		if (!tb[ATTR_ROAM_SET_BSSID_PARAMS_NUM_BSSID]) {
			pr_err("attr num of blacklist bssid failed\n");
			goto fail;
		}
		black_params.num_black_bssid =
		    nla_get_u32(tb[ATTR_ROAM_SET_BSSID_PARAMS_NUM_BSSID]);
		pr_info("Num of black BSSID:%d\n",
			black_params.num_black_bssid);

		if (!tb[ATTR_ROAM_SET_BSSID_PARAMS])
			break;

		fw_max_blacklist = priv->roam_capa.max_blacklist_size;
		black_limit = min(fw_max_blacklist, MAX_BLACK_BSSID);

		if (black_params.num_black_bssid > black_limit) {
			pr_err("black size exceed the limit:%d\n", black_limit);
			break;
		}
		i = 0;
		nla_for_each_nested(curr_attr,
				    tb[ATTR_ROAM_SET_BSSID_PARAMS], rem) {
			if (nla_parse(tb2, ATTR_ROAM_MAX,
				      nla_data(curr_attr), nla_len(curr_attr),
				      NULL, NULL)) {
				pr_err("nla parse failed\n");
				goto fail;
			}
			/* Parse and fetch MAC address */
			if (!tb2[ATTR_ROAM_SET_BSSID_PARAMS_BSSID]) {
				pr_err("attr blacklist addr failed\n");
				goto fail;
			}
			nla_memcpy(black_params.black_list[i].MAC_addr,
				   tb2[ATTR_ROAM_SET_BSSID_PARAMS_BSSID],
				   sizeof(struct bssid_t));
			pr_info("black list mac addr:%pM\n",
				black_params.black_list[i].MAC_addr);
			i++;
			if (i >= MAX_BLACK_BSSID)
				goto fail;
		}
		black_params.num_black_bssid = i;
		/* send black list with roam_params CMD */
		ret = sc2355_set_roam_offload(priv, vif,
					      SPRD_ROAM_SET_BLACK_LIST,
					      (u8 *)&black_params,
					      (i * sizeof(struct bssid_t) + 1));
		break;
	default:
		break;
	}
	return ret;
fail:
	return -EINVAL;
}

/* set_ssid_hotlist function---CMD ID:65 */
static int vendor_set_ssid_hotlist(struct wiphy *wiphy,
				   struct wireless_dev *wdev,
				   const void *data, int len)
{
	int i, ret = 0, tlen;
	int type, request_id;
	int rem_len, rem_outer_len, rem_inner_len;
	struct nlattr *pos, *outer_iter, *inner_iter;
	struct wifi_ssid_hotlist_params *ssid_hotlist_params;
	struct cmd_gscan_rsp_header rsp;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	ssid_hotlist_params = kmalloc(sizeof(*ssid_hotlist_params), GFP_KERNEL);

	if (!ssid_hotlist_params)
		return -ENOMEM;

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);

		switch (type) {
		case GSCAN_ATTR_CONFIG_REQUEST_ID:
			request_id = nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_SSID_HOTLIST_LOST_SSID_SAMPLE_SIZE:
			ssid_hotlist_params->lost_ssid_sample_size =
			    nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_SSID_HOTLIST_NUM_SSID:
			ssid_hotlist_params->num_ssid = nla_get_s32(pos);
			break;

		case GSCAN_ATTR_CONFIG_SSID_THR:
			i = 0;
			nla_for_each_nested(outer_iter, pos, rem_outer_len) {
				nla_for_each_nested(inner_iter, outer_iter,
						    rem_inner_len) {
					type = nla_type(inner_iter);
				switch (type) {
				case GSCAN_ATTR_CONFIG_SSID_THR_SSID:
					if (nla_len(inner_iter) >
					    IEEE80211_MAX_SSID_LEN * sizeof(unsigned char)) {
						netdev_err(vif->ndev,
							   "nla_data for networks \
							   nla_type 0x%x is invalid\n", type);
							ret = -EINVAL;
					} else {
						memcpy(ssid_hotlist_params->ssid[i].ssid,
						       nla_data(inner_iter),
						       nla_len(inner_iter));
					}
				break;

				case GSCAN_ATTR_CONFIG_SSID_THR_RSSI_LOW:
					ssid_hotlist_params->ssid[i].low =
					    nla_get_s32(inner_iter);
				break;

				case GSCAN_ATTR_CONFIG_SSID_THR_RSSI_HIGH:
					ssid_hotlist_params->ssid[i].high =
					    nla_get_s32(inner_iter);
				break;
				default:
					netdev_err(vif->ndev,
						   "networks nla type 0x%x not support\n",
						   type);
						ret = -EINVAL;
				break;
				}
			}

			if (ret < 0)
				break;

			i++;
			if (i >= MAX_HOTLIST_APS)
				break;
		}
		break;

		default:
			netdev_err(vif->ndev, "nla type 0x%x not support\n",
				   type);
			ret = -EINVAL;
		break;
		}

	if (ret < 0)
		break;
	}

	netdev_info(vif->ndev, "parse bssid hotlist %s\n",
		    !ret ? "success" : "failture");

	tlen = sizeof(struct wifi_ssid_hotlist_params);
	ret = sc2355_gscan_subcmd(vif->priv, vif,
				  (void *)ssid_hotlist_params,
				  SPRD_GSCAN_SUBCMD_SET_SSID_HOTLIST,
				  tlen, (u8 *)(&rsp), &rlen);

	kfree(ssid_hotlist_params);
	return ret;
}

/* reset_ssid_hotlist function---CMD ID:66 */
static int vendor_reset_ssid_hotlist(struct wiphy *wiphy,
				     struct wireless_dev *wdev,
				     const void *data, int len)
{
	int flush = 1;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	netdev_info(vif->ndev, "%s %d\n", __func__, flush);

	return sc2355_gscan_subcmd(vif->priv, vif,
				   (void *)(&flush),
				   SPRD_GSCAN_SUBCMD_RESET_SSID_HOTLIST,
				   sizeof(int), (u8 *)(&rsp), &rlen);
}

/* set_passpoint_list functon------ CMD ID:70 */
static int vendor_set_passpoint_list(struct wiphy *wiphy,
				     struct wireless_dev *wdev,
				     const void *data, int len)
{
	struct nlattr *tb[GSCAN_ATTR_MAX + 1] = {NULL,};
	struct nlattr *tb2[GSCAN_ATTR_MAX + 1] = {NULL,};
	struct nlattr *HS_list;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct wifi_passpoint_network *HS_list_params;
	int i = 0, rem, flush, ret = 0, tlen, hs_num;
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);
	int maxtype = ARRAY_SIZE(wlan_gscan_config_policy);

	if (maxtype > GSCAN_ATTR_MAX)
		maxtype = GSCAN_ATTR_MAX;
	if (nla_parse(tb, maxtype, data, len,
		      wlan_gscan_config_policy, NULL)) {
		netdev_info(vif->ndev,
			    "%s :Fail to parse attribute\n", __func__);
		return -EINVAL;
	}

	HS_list_params = kmalloc(sizeof(*HS_list_params), GFP_KERNEL);
	if (!HS_list_params)
		return -ENOMEM;

	/* Parse and fetch */
	if (!tb[GSCAN_ATTR_ANQPO_LIST_FLUSH]) {
		netdev_info(vif->ndev,
			    "%s :Fail to parse GSCAN_ATTR_ANQPO_LIST_FLUSH\n",
			    __func__);
		ret = -EINVAL;
		goto out;
	}

	flush = nla_get_u32(tb[GSCAN_ATTR_ANQPO_LIST_FLUSH]);

	/* Parse and fetch */
	if (!tb[GSCAN_ATTR_ANQPO_HS_LIST_SIZE]) {
		if (flush == 1) {
			ret = sc2355_gscan_subcmd(vif->priv, vif,
						  (void *)(&flush),
					SPRD_GSCAN_SUBCMD_RESET_ANQPO_CONFIG,
					sizeof(int), (u8 *)(&rsp), &rlen);
		} else {
			ret = -EINVAL;
		}
		goto out;
	}

	hs_num = nla_get_u32(tb[GSCAN_ATTR_ANQPO_HS_LIST_SIZE]);

	nla_for_each_nested(HS_list, tb[GSCAN_ATTR_ANQPO_HS_LIST], rem) {
		if (nla_parse(tb2, GSCAN_ATTR_MAX,
			      nla_data(HS_list), nla_len(HS_list),
			      NULL, NULL)) {
			netdev_info(vif->ndev,
				    "%s :Fail to parse tb2\n", __func__);
			ret = -EINVAL;
			goto out;
		}

		if (!tb2[GSCAN_ATTR_ANQPO_HS_NETWORK_ID]) {
			netdev_info(vif->ndev,
				    "%s :Fail to parse GSCAN_ATTR_ANQPO_HS_NETWORK_ID\n",
				    __func__);
			ret = -EINVAL;
			goto out;
		}

		HS_list_params->id = nla_get_u32(tb[GSCAN_ATTR_ANQPO_HS_NETWORK_ID]);

		if (!tb2[GSCAN_ATTR_ANQPO_HS_NAI_REALM] ||
		    (nla_len(tb2[GSCAN_ATTR_ANQPO_HS_NAI_REALM]) >
		    sizeof(HS_list_params->realm))) {
			netdev_info(vif->ndev,
				    "%s :Fail to parse GSCAN_ATTR_ANQPO_HS_NAI_REALM\n",
				    __func__);
			ret = -EINVAL;
			goto out;
		}
		memcpy(HS_list_params->realm,
		       nla_data(tb2[GSCAN_ATTR_ANQPO_HS_NAI_REALM]),
		       nla_len(tb2[GSCAN_ATTR_ANQPO_HS_NAI_REALM]));

		if (!tb2[GSCAN_ATTR_ANQPO_HS_ROAM_CONSORTIUM_ID] ||
		    (nla_len(tb2[GSCAN_ATTR_ANQPO_HS_ROAM_CONSORTIUM_ID]) >
		    sizeof(HS_list_params->roaming_ids))) {
			netdev_info(vif->ndev,
				    "%s :Fail to parse GSCAN_ATTR_ANQPO_HS_ROAM_CONSORTIUM_ID\n",
				    __func__);
			ret = -EINVAL;
			goto out;
		}

		memcpy(HS_list_params->roaming_ids,
		       nla_data(tb2[GSCAN_ATTR_ANQPO_HS_ROAM_CONSORTIUM_ID]),
		       nla_len(tb2[GSCAN_ATTR_ANQPO_HS_ROAM_CONSORTIUM_ID]));

		if (!tb2[GSCAN_ATTR_ANQPO_HS_PLMN] ||
		    (nla_len(tb2[GSCAN_ATTR_ANQPO_HS_PLMN]) >
		    sizeof(HS_list_params->plmn))) {
			netdev_info(vif->ndev,
				    "%s :Fail to parse GSCAN_ATTR_ANQPO_HS_PLMN\n",
				    __func__);
			ret = -EINVAL;
			goto out;
		}

		memcpy(HS_list_params->plmn,
		       nla_data(tb2[GSCAN_ATTR_ANQPO_HS_PLMN]),
		       nla_len(tb2[GSCAN_ATTR_ANQPO_HS_PLMN]));
		i++;
	}

	tlen = sizeof(struct wifi_passpoint_network);
	ret = sc2355_gscan_subcmd(vif->priv, vif,
				  (void *)HS_list_params,
				  SPRD_GSCAN_SUBCMD_ANQPO_CONFIG,
				  tlen, (u8 *)(&rsp), &rlen);

out:
	kfree(HS_list_params);
	return ret;
}

/* reset_passpoint_list functon------ CMD ID:71 */
static int vendor_reset_passpoint_list(struct wiphy *wiphy,
				       struct wireless_dev *wdev,
				       const void *data, int len)
{
	int flush = 1;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct cmd_gscan_rsp_header rsp;
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	netdev_info(vif->ndev, "%s %d\n", __func__, flush);

	return sc2355_gscan_subcmd(vif->priv, vif,
				   (void *)(&flush),
				   SPRD_GSCAN_SUBCMD_RESET_ANQPO_CONFIG,
				   sizeof(int), (u8 *)(&rsp), &rlen);
}

static int vendor_monitor_rssi(struct wiphy *wiphy,
			       struct wireless_dev *wdev,
			       const void *data, int len)
{
	struct nlattr *tb[ATTR_RSSI_MONITOR_MAX + 1];
	u32 control;
	struct rssi_monitor_req req;
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_priv *priv = wiphy_priv(wiphy);

	/* if wifi not connected,return */
	if (vif->sm_state != SPRD_CONNECTED) {
		pr_err("Wifi not connected!\n");
		return -ENOTSUPP;
	}
	if (nla_parse(tb, ATTR_RSSI_MONITOR_MAX, data, len,
		      rssi_monitor_policy, NULL)) {
		pr_err("Invalid ATTR\n");
		return -EINVAL;
	}

	if (!tb[ATTR_RSSI_MONITOR_REQUEST_ID]) {
		pr_err("attr request id failed\n");
		return -EINVAL;
	}

	if (!tb[ATTR_RSSI_MONITOR_CONTROL]) {
		pr_err("attr control failed\n");
		return -EINVAL;
	}

	req.request_id = nla_get_u32(tb[ATTR_RSSI_MONITOR_REQUEST_ID]);
	control = nla_get_u32(tb[ATTR_RSSI_MONITOR_CONTROL]);

	if (control == VENDOR_RSSI_MONITOR_START) {
		req.control = true;
		if (!tb[ATTR_RSSI_MONITOR_MIN_RSSI]) {
			pr_err("get min rssi fail\n");
			return -EINVAL;
		}

		if (!tb[ATTR_RSSI_MONITOR_MAX_RSSI]) {
			pr_err("get max rssi fail\n");
			return -EINVAL;
		}

		req.min_rssi = nla_get_s8(tb[ATTR_RSSI_MONITOR_MIN_RSSI]);
		req.max_rssi = nla_get_s8(tb[ATTR_RSSI_MONITOR_MAX_RSSI]);

		if (!(req.min_rssi < req.max_rssi)) {
			pr_err("min rssi %d must be less than max_rssi:%d\n",
			       req.min_rssi, req.max_rssi);
			return -EINVAL;
		}
		pr_info("min_rssi:%d max_rssi:%d\n",
			req.min_rssi, req.max_rssi);
	} else if (control == VENDOR_RSSI_MONITOR_STOP) {
		req.control = false;
		pr_info("stop rssi monitor!\n");
	} else {
		pr_err("Invalid control cmd:%d\n", control);
		return -EINVAL;
	}
	pr_info("Request id:%u,control:%d", req.request_id, req.control);

	/* send rssi monitor cmd */
	vendor_send_rssi_cmd(priv, vif, &req, sizeof(req));

	return 0;
}

static int vendor_get_logger_feature(struct wiphy *wiphy,
				     struct wireless_dev *wdev,
				     const void *data, int len)
{
	int ret;
	struct sk_buff *reply;
	int feature, payload;

	payload = sizeof(feature);
	feature = 0;
	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, payload);

	if (!reply)
		return -ENOMEM;

	feature |= WIFI_LOGGER_CONNECT_EVENT_SUPPORTED;

	/* vts will test wake reason state function */
	feature |= WIFI_LOGGER_WAKE_LOCK_SUPPORTED;

	if (nla_put_u32(reply, ATTR_FEATURE_SET, feature)) {
		wiphy_err(wiphy, "put skb u32 failed\n");
		goto out_put_fail;
	}

	ret = cfg80211_vendor_cmd_reply(reply);
	if (ret)
		wiphy_err(wiphy, "reply cmd error\n");
	return ret;

out_put_fail:
	kfree_skb(reply);
	return -EMSGSIZE;
}

static int vendor_set_epno_list(struct wiphy *wiphy,
				struct wireless_dev *wdev,
				const void *data, int len)
{
	int i, ret = 0;
	int type;
	int rem_len, rem_outer_len, rem_inner_len;
	struct nlattr *pos, *outer_iter, *inner_iter;
	struct wifi_epno_network *epno_network;
	struct wifi_epno_params epno_params;
	struct cmd_gscan_rsp_header rsp;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	u16 rlen = sizeof(struct cmd_gscan_rsp_header);

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		switch (type) {
		case ATTR_GSCAN_RESULTS_REQUEST_ID:
			epno_params.request_id = nla_get_u32(pos);
			break;

		case ATTR_EPNO_MIN5GHZ_RSSI:
			epno_params.min5ghz_rssi = nla_get_u32(pos);
			break;

		case ATTR_EPNO_MIN24GHZ_RSSI:
			epno_params.min24ghz_rssi = nla_get_u32(pos);
			break;

		case ATTR_EPNO_INITIAL_SCORE_MAX:
			epno_params.initial_score_max = nla_get_u32(pos);
			break;

		case ATTR_EPNO_CURRENT_CONNECTION_BONUS:
			epno_params.current_connection_bonus = nla_get_u32(pos);
			break;

		case ATTR_EPNO_SAME_NETWORK_BONUS:
			epno_params.same_network_bonus = nla_get_u32(pos);
			break;

		case ATTR_EPNO_SECURE_BONUS:
			epno_params.secure_bonus = nla_get_u32(pos);
			break;

		case ATTR_EPNO_BAND5GHZ_BONUS:
			epno_params.band5ghz_bonus = nla_get_u32(pos);
			break;

		case ATTR_PNO_SET_LIST_PARAM_NUM_NETWORKS:
			epno_params.num_networks = nla_get_u32(pos);
			if (epno_params.num_networks == 0)
				return vendor_flush_epno_list(vif);

			break;

		case ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORKS_LIST:
			i = 0;
			nla_for_each_nested(outer_iter, pos, rem_outer_len) {
				epno_network = &epno_params.networks[i];
				nla_for_each_nested(inner_iter, outer_iter,
						    rem_inner_len) {
					type = nla_type(inner_iter);
					switch (type) {
					case ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_SSID:
						if (nla_len(inner_iter) > IEEE80211_MAX_SSID_LEN) {
							netdev_err(vif->ndev,
								   "nla_data for networks \
								   nla_type 0x%x is invalid\n", type);
								ret = -EINVAL;
						} else {
							memcpy(epno_network->ssid,
							       nla_data(inner_iter),
							       nla_len(inner_iter));
						}
						break;

					case ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_FLAGS:
						epno_network->flags =
						    nla_get_u8(inner_iter);
						break;

					case ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_AUTH_BIT:
						epno_network->auth_bit_field =
						    nla_get_u8(inner_iter);
						break;

					default:
						netdev_err(vif->ndev,
							   "networks nla type 0x%x not support\n",
							   type);
						ret = -EINVAL;
						break;
					}
				}

				if (ret < 0)
					break;

				i++;
				if (i >= MAX_EPNO_NETWORKS)
					break;
			}
			break;

		default:
			netdev_err(vif->ndev, "nla type 0x%x not support\n",
				   type);
			ret = -EINVAL;
			break;
		}

		if (ret < 0)
			break;
	}

	epno_params.boot_time = jiffies;

	netdev_info(vif->ndev, "parse epno list %s\n",
		    !ret ? "success" : "failture");
	if (!ret)
		ret = sc2355_gscan_subcmd(vif->priv, vif,
					  (void *)&epno_params,
					  SPRD_GSCAN_SUBCMD_SET_EPNO_SSID,
					  sizeof(epno_params), (u8 *)(&rsp),
					  &rlen);

	return ret;
}

/*read sar param according to scence code*/
/*scence code:			 *
 *reciver on scence    1 *
 *reciver off scence   5 *
 *hotspot scence       3 */
static struct set_sar_limit_param *
		sprd_vendor_read_sar_param(u32 sar_scence)
{
	int i = 0;
	int sar_map_len = ARRAY_SIZE(sar_param_map);
	struct set_sar_limit_param *psar_map = NULL;

	for (i = 0; i < sar_map_len; i++) {
		if (sar_scence == sar_param_map[i].sar_scence) {
			psar_map = &sar_param_map[i];
			break;
		}
	}
	return psar_map;
}

/* set SAR limits function------CMD ID:146 */
static int vendor_set_sar_limits(struct wiphy *wiphy,
				 struct wireless_dev *wdev,
				 const void *data, int len)
{
/**set sar param according to different scence**/
	int ret = VENDOR_WIFI_SUCCESS;
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct nlattr *tb[ATTR_SAR_LIMITS_MAX + 1];
	struct set_sar_limit_param *psar_param;
	u32 sar_scence = 0;

	pr_info("%s enter:\n", __func__);
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0)
	if (nla_parse(tb, ATTR_SAR_LIMITS_MAX, data, len, NULL, NULL)) {
#else
	if (nla_parse(tb, ATTR_SAR_LIMITS_MAX, data, len, NULL)) {
#endif
		pr_err("Invalid ATTR\n");
		return -EINVAL;
	}

	if (!tb[ATTR_SAR_LIMITS_SAR_ENABLE]) {
		pr_err("attr sar enable failed\n");
		return -EINVAL;
	}

	sar_scence = nla_get_u32(tb[ATTR_SAR_LIMITS_SAR_ENABLE]);
	psar_param = sprd_vendor_read_sar_param(sar_scence);
	if (psar_param) {
		netdev_info(vif->ndev, "%s: set sar, scence: %d, value : %d\n",
					__func__, psar_param->sar_scence,
					psar_param->power_value);
		ret = sc2355_set_sar(priv, vif,
						psar_param->sar_type,
						psar_param->power_value);
		if (ret)
			pr_err("set sar value failed, result: %d", ret);
	} else {
		pr_err("invalid sar scence: %d\n", sar_scence);
	}

	/* To pass vts test */
	return ret;
}

static int vendor_get_akm_suite(struct wiphy *wiphy,
				struct wireless_dev *wdev,
				const void *data, int len)
{
	int ret, akm_len;
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sk_buff *reply;
	int index = 0;
	int akm[8] = { 0 };

	if (priv->extend_feature & SPRD_EXTEND_FEATURE_SAE)
		akm[index++] = WLAN_AKM_SUITE_SAE;
	if (priv->extend_feature & SPRD_EXTEND_FEATURE_OWE)
		akm[index++] = WLAN_AKM_SUITE_OWE;
	if (priv->extend_feature & SPRD_EXTEND_FEATURE_DPP)
		akm[index++] = WLAN_CIPHER_SUITE_DPP;
	if (priv->extend_feature & SPRD_EXTEND_8021X_SUITE_B_192)
		akm[index++] = WLAN_CIPHER_SUITE_BIP_GMAC_256;

	akm_len = index * sizeof(akm[0]);
	pr_debug("akm suites count = %d\n", index);
	reply = cfg80211_vendor_cmd_alloc_reply_skb(wiphy, len);
	if (!reply)
		return -ENOMEM;
	if (nla_put(reply, NL80211_ATTR_AKM_SUITES, akm_len, akm)) {
		kfree_skb(reply);
		return -EMSGSIZE;
	}
	ret = cfg80211_vendor_cmd_reply(reply);
	if (ret)
		wiphy_err(wiphy, "reply cmd error\n");
	return ret;
}

static int vendor_set_offload_packet(struct wiphy *wiphy,
				     struct wireless_dev *wdev,
				     const void *data, int len)
{
	int err;
	u8 control;
	u32 req;
	struct nlattr *tb[ATTR_OFFLOADED_PACKETS_MAX + 1];
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_priv *priv = wiphy_priv(wiphy);

	if (!data) {
		wiphy_err(wiphy, "%s offload failed\n", __func__);
		return -EINVAL;
	}

	err = nla_parse(tb, ATTR_OFFLOADED_PACKETS_MAX, data, len, NULL, NULL);
	if (err) {
		wiphy_err(wiphy, "%s parse attr failed", __func__);
		return err;
	}

	if (!tb[ATTR_OFFLOADED_PACKETS_REQUEST_ID] ||
	    !tb[ATTR_OFFLOADED_PACKETS_SENDING_CONTROL]) {
		wiphy_err(wiphy, "check request id or control failed\n");
		return -EINVAL;
	}

	req = nla_get_u32(tb[ATTR_OFFLOADED_PACKETS_REQUEST_ID]);
	control = nla_get_u32(tb[ATTR_OFFLOADED_PACKETS_SENDING_CONTROL]);

	switch (control) {
	case VENDOR_OFFLOADED_PACKETS_SENDING_STOP:
		return vendor_stop_offload_packet(priv, vif, req);
	case VENDOR_OFFLOADED_PACKETS_SENDING_START:
		return vendor_start_offload_packet(priv, vif, tb, req);
	default:
		wiphy_err(wiphy, "control value is invalid\n");
		return -EINVAL;
	}

	return 0;
}

static int vendor_softap_set_sae_para(struct sprd_priv *priv,
				      struct sprd_vif *vif,
				      char *data, int data_len)
{
	struct sae_param *param;
	struct sprd_msg *msg;
	int len;

	len = sizeof(*param) + data_len;
	pr_info("total len: %d, data len: %d\n", len, data_len);

	msg = get_cmdbuf(priv, vif, len, CMD_SET_SAE_PARAM);
	if (!msg)
		return -ENOMEM;

	param = (struct sae_param *)msg->data;
	param->request_type = SPRD_SAE_PASSWORD_ENTRY;
	memcpy(param->data, data, data_len);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

static int vendor_set_sae_password(struct wiphy *wiphy,
				   struct wireless_dev *wdev,
				   const void *data, int len)
{
	int group_index = 0, sae_entry_index = 0, rem_len, type;
	struct nlattr *pos;
	struct softap_sae_setting sae_para;
	struct sprd_vif *vif = netdev_priv(wdev->netdev);
	struct sprd_priv *priv = wiphy_priv(wiphy);
	char *para;
	int para_len, ret;

	/* MU300: the firmware's soft-AP SAE (the U30 Air's SAE offload, FINDINGS 80) is switched off: the PMK it hands
	 * back cannot be told from a record a client sends (common/report.c drops it), so the firmware is never given
	 * an SAE password. The access point is WPA2-PSK only. */
	pr_info_once("sprd_wlan: soft-AP SAE refused: the firmware SAE offload is disabled (WPA2-PSK only)\n");
	return -EOPNOTSUPP;

	if (!(priv->extend_feature & SPRD_EXTEND_SOATAP_WPA3)) {
		pr_err("firmware not support softap wpa3!\n");
		return -ENOTSUPP;
	}

	memset(&sae_para, 0x00, sizeof(sae_para));

	nla_for_each_attr(pos, (void *)data, len, rem_len) {
		type = nla_type(pos);
		pr_info("%s type : %d\n", __func__, type);

		switch (type) {
		case VENDOR_SAE_ENTRY:
			if (sae_entry_index >= SPRD_SAE_MAX_NUM)
				return -EINVAL;
			sae_para.entry[sae_entry_index].vlan_id =
			    SPRD_SAE_NOT_SET;
			sae_para.entry[sae_entry_index].used = 1;
			if (vendor_parse_sae_entry(&sae_para.entry[sae_entry_index],
					       nla_data(pos), nla_len(pos)) != 0) {
				pr_err("%s %d error.\n", __func__, __LINE__);
				return -EINVAL;
			}
			sae_entry_index++;
			break;

		case VENDOR_SAE_GROUP_ID:
			if (sae_para.group_count >= 31)
				return 0;
			sae_para.groups[group_index] = nla_get_u32(pos);
			group_index++;
			break;

		case VENDOR_SAE_ACT:
			sae_para.act = nla_get_u32(pos);
			break;

		case VENDOR_SAE_PWD:
			sae_para.passphrase_len = nla_len(pos);
			if (sae_para.passphrase_len < sizeof(sae_para.passphrase)) {
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
				nla_strscpy(sae_para.passphrase, pos,
					    sae_para.passphrase_len + 1);
#else
				nla_strlcpy(sae_para.passphrase, pos,
					    sae_para.passphrase_len + 1);
#endif
			} else {
				pr_err("%s %d error.\n", __func__, __LINE__);
				return -EINVAL;
			}
			break;
		default:
			break;
		}
	}

	para = kzalloc(1024, GFP_KERNEL);
	if (!para)
		return -ENOMEM;

	/* all para need translate to tlv format */
	para_len = vendor_softap_convert_para(vif, &sae_para, para);
	ret = vendor_softap_set_sae_para(vif->priv, vif, para, para_len);

	kfree(para);
	return ret;
}

static const struct wiphy_vendor_command vendor_cmd[] = {
	{/* 9 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_ROAMING,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_roaming_enable,
		.policy = roaming_policy,
		.maxattr = ATTR_VENDOR_MAX,
	},
	{/* 12 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_NAN,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_nan_enable,
		.policy = VENDOR_CMD_RAW_DATA,
	},
	{/* 14 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_SET_LLSTAT
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_llstat_handler,
		.policy = ll_stats_policy,
		.maxattr = ATTR_LL_STATS_SET_MAX,
	},
	{/* 15 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_GET_LLSTAT
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_llstat_handler,
		.policy = ll_stats_get_policy,
		.maxattr = ATTR_CMD_LL_STATS_TYPE_MAX,
	},
	{/* 16 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CLR_LLSTAT
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_clr_llstat_handler,
		.policy = ll_stats_clr_policy,
		.maxattr = ATTR_LL_STATS_CLR_MAX,
	},
	{/* 20 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_START,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_gscan_start,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 21 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_STOP,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_gscan_stop,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 22 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_GET_CHANNEL,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_channel_list,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 23 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_GET_CAPABILITIES,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_gscan_capabilities,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 24 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_GET_CACHED_RESULTS,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_cached_gscan_results,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 29 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_SET_BSSID_HOTLIST,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_bssid_hotlist,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 30 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_RESET_BSSID_HOTLIST,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_reset_bssid_hotlist,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 32 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_SET_SIGNIFICANT_CHANGE,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_significant_change,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 33 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd =
			VENDOR_CMD_GSCAN_RESET_SIGNIFICANT_CHANGE,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_reset_significant_change,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 38 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GET_SUPPORT_FEATURE,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_support_feature,
		.policy = VENDOR_CMD_RAW_DATA,
	},
	{/* 39 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_SET_MAC_OUI,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_mac_oui,
		.policy = mac_oui_policy,
		.maxattr = ATTR_SET_SCANNING_MAC_OUI_MAX,
	},
	{/* 42 */
		{
			.vendor_id = OUI_SPREAD,
			.subcmd = VENDOR_CMD_GET_CONCURRENCY_MATRIX,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_concurrency_matrix,
		.policy = get_concurrency_matrix_policy,
		.maxattr = ATTR_CO_MATRIX_MAX,
	},
	{/* 55 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GET_FEATURE,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_feature,
		.policy = VENDOR_CMD_RAW_DATA,
	},
	{/* 61 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GET_WIFI_INFO,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_driver_info,
		.policy = get_wifi_info_policy,
		.maxattr = ATTR_WIFI_INFO_GET_MAX,
	},
	{/* 62 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_START_LOGGING,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_start_logging,
		.policy = wifi_logger_start_policy,
		.maxattr = ATTR_WIFI_LOGGER_START_GET_MAX,
	},
	{/* 63 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_WIFI_LOGGER_MEMORY_DUMP,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_memory_dump,
		.policy = wifi_logger_start_policy,
		.maxattr = ATTR_WIFI_LOGGER_START_GET_MAX,
	},
	{/* 64 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_ROAM,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_roam_params,
		.policy = roaming_config_policy,
		.maxattr = ATTR_ROAM_MAX,
	},
	{/* 65 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_SET_SSID_HOTLIST,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_ssid_hotlist,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 66 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GSCAN_RESET_SSID_HOTLIST,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_reset_ssid_hotlist,
		.policy = wlan_gscan_config_policy,
		.maxattr = GSCAN_ATTR_CONFIG_MAX,
	},
	{/* 69 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_PNO_SET_LIST,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_epno_list,
		.policy = wlan_gscan_result_policy,
		.maxattr = ATTR_PNO_MAX,
	},
	{/* 70 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_PNO_SET_PASSPOINT_LIST,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_passpoint_list,
		.policy = wlan_gscan_result_policy,
		.maxattr = ATTR_PNO_MAX,
	},
	{/* 71 */
		{
			.vendor_id = OUI_SPREAD,
			.subcmd = VENDOR_CMD_PNO_RESET_PASSPOINT_LIST,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_reset_passpoint_list,
		.policy = wlan_gscan_result_policy,
		.maxattr = ATTR_PNO_MAX,
	},
	{/* 76 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GET_LOGGER_FEATURE_SET,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_logger_feature,
		.policy = get_logger_features_policy,
		.maxattr = ATTR_LOGGER_MAX,
	},
	{/* 77 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GET_RING_DATA,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_ring_data,
		.policy = wifi_logger_start_policy,
		.maxattr = ATTR_WIFI_LOGGER_START_GET_MAX,
	},
	{/* 79 */
		{
			.vendor_id = OUI_SPREAD,
			.subcmd = VENDOR_CMD_OFFLOADED_PACKETS,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_RUNNING,
		.doit = vendor_set_offload_packet,
		.policy = offloaded_packets_policy,
		.maxattr = ATTR_OFFLOADED_PACKETS_MAX,
	},
	{/* 80 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_MONITOR_RSSI,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_monitor_rssi,
		.policy = rssi_monitor_policy,
		.maxattr = ATTR_RSSI_MONITOR_MAX,
	},
	{/* 82 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_ENABLE_ND_OFFLOAD,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_enable_nd_offload,
		.policy = nd_offload_policy,
		.maxattr = ATTR_ND_OFFLOAD_MAX,
	},
	{/* 85 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GET_WAKE_REASON_STATS,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_get_wake_state,
		.policy = wake_stats_policy,
		.maxattr = ATTR_WAKE_MAX,
	},
	{/* 146 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_SET_SAR_LIMITS,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			 WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = vendor_set_sar_limits,
		.policy = vendor_sar_limits_policy,
		.maxattr = ATTR_SAR_LIMITS_MAX,
	},

#ifdef CONFIG_SC2355_WLAN_NAN
	{/* 0x1300 */
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_NAN
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_NETDEV,
		.doit = sc2355_nan_vendor_cmds,
		.policy = VENDOR_CMD_RAW_DATA,
	},
#endif /* CONFIG_SC2355_WLAN_NAN */
#ifdef CONFIG_SC2355_WLAN_RTT
	{
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = SPRD_NL80211_SUBCMD_LOC_GET_CAPA
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_RUNNING,
		.doit = sc2355_rtt_get_capabilities,
		.policy = VENDOR_CMD_RAW_DATA,
	},
	{
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = SPRD_NL80211_SUBCMD_RTT_START_SESSION
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_RUNNING,
		.doit = sc2355_rtt_start_session,
		.policy = VENDOR_CMD_RAW_DATA,
	},
	{
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = SPRD_NL80211_SUBCMD_RTT_ABORT_SESSION
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_RUNNING,
		.doit = sc2355_rtt_abort_session,
		.policy = VENDOR_CMD_RAW_DATA,
	},
	{
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = SPRD_NL80211_SUBCMD_RTT_CFG_RESPONDER
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_RUNNING,
		.doit = sc2355_rtt_configure_responder,
		.policy = VENDOR_CMD_RAW_DATA,
	},
#endif /* CONFIG_SC2355_WLAN_RTT */
	{
		{
		    .vendor_id = OUI_SPREAD,
		    .subcmd = VENDOR_CMD_GET_AKM_SUITE,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_RUNNING,
		.doit = vendor_get_akm_suite,
		.policy = VENDOR_CMD_RAW_DATA,
	},
	{/* WPA3 softap */
		{
			.vendor_id = OUI_SPREAD,
			.subcmd = VENDOR_CMD_SET_SAE_PASSWORD,
		},
		.flags = WIPHY_VENDOR_CMD_NEED_WDEV |
			WIPHY_VENDOR_CMD_NEED_RUNNING,
		.doit = vendor_set_sae_password,
		.policy = vendor_sae_policy,
		.maxattr = VENDOR_SAE_MAX,
	},
};

static const struct nl80211_vendor_cmd_info vendor_events[] = {
	[VENDOR_EVENT_NAN_MONITOR_RSSI] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_MONITOR_RSSI,
	},
	{/* 1 */
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_GSCAN_WIFI_EVT_RESERVED2,
	},
	{/* 2 */
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_GSCAN_WIFI_EVT_RESERVED2,
	},
	{/* 3 */
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_GSCAN_WIFI_EVT_RESERVED2,
	},
	{/* 4 */
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_GSCAN_WIFI_EVT_RESERVED2,
	},
	{/* 5 */
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_GSCAN_WIFI_EVT_RESERVED2,
	},
	/* reserver for array align */
	[VENDOR_EVENT_GSCAN_START] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_START
	},
	[VENDOR_EVENT_GSCAN_STOP] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_STOP
	},
	[VENDOR_EVENT_GSCAN_GET_CAPABILITIES] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_GET_CAPABILITIES
	},
	[VENDOR_EVENT_GSCAN_GET_CACHE_RESULTS] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_GET_CACHED_RESULTS
	},
	[VENDOR_EVENT_GSCAN_SCAN_RESULTS_AVAILABLE] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_SCAN_RESULTS_AVAILABLE
	},
	[VENDOR_EVENT_GSCAN_FULL_SCAN_RESULT] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_FULL_SCAN_RESULT
	},
	[VENDOR_EVENT_GSCAN_SCAN_EVENT] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_SCAN_EVENT
	},
	[VENDOR_EVENT_GSCAN_HOTLIST_AP_FOUND] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_HOTLIST_AP_FOUND
	},
	[VENDOR_EVENT_GSCAN_HOTLIST_AP_LOST] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_HOTLIST_AP_LOST
	},
	[VENDOR_EVENT_GSCAN_SET_BSSID_HOTLIST] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_SET_BSSID_HOTLIST
	},
	[VENDOR_EVENT_GSCAN_RESET_BSSID_HOTLIST] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_RESET_BSSID_HOTLIST
	},
	[VENDOR_EVENT_GSCAN_SIGNIFICANT_CHANGE] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_SIGNIFICANT_CHANGE
	},
	[VENDOR_EVENT_GSCAN_SET_SIGNIFICANT_CHANGE] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_SET_SIGNIFICANT_CHANGE
	},
	[VENDOR_EVENT_GSCAN_RESET_SIGNIFICANT_CHANGE] {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_GSCAN_RESET_SIGNIFICANT_CHANGE
	},
	/* report acs lte event to uplayer */
	[SPRD_ACS_LTE_EVENT_INDEX] = {
		.vendor_id = OUI_SPREAD,
		.subcmd = SPRD_REINIT_ACS,
	},
	[SPRD_VENDOR_EVENT_NAN_INDEX] = {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_EVENT_NAN,
	},
	[VENDOR_CMD_PNO_NETWORK_FOUND] = {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_CMD_PNO_NETWORK_FOUND,
	},
	[SPRD_EVENT_RTT_MEAS_RESULT_INDEX] = {
		.vendor_id = OUI_SPREAD,
		.subcmd = SPRD_NL80211_SUBCMD_RTT_MEAS_RESULT
	},
	[SPRD_EVENT_RTT_SESSION_DONE_INDEX] = {
		.vendor_id = OUI_SPREAD,
		.subcmd = SPRD_NL80211_SUBCMD_RTT_SESSION_DONE
	},
	[SPRD_RTT_EVENT_COMPLETE_INDEX] = {
		.vendor_id = OUI_SPREAD,
		.subcmd = VENDOR_GSCAN_WIFI_EVT_RTT_EVENT_COMPLETE
	}
};

/* buffer scan result in host driver when receive frame from cp2 */
static int vendor_cache_scan_result(struct sprd_vif *vif, u8 bucket_id,
				    struct gscan_result *item)
{
	struct sprd_priv *priv = vif->priv;
	u32 i;
	struct sprd_gscan_cached_results *p = NULL;

	if (bucket_id >= priv->gscan_buckets_num || !priv->gscan_res) {
		netdev_err(vif->ndev,
			   "%s the gscan buffer invalid!priv->gscan_buckets_num: %d, bucket_id:%d\n",
			   __func__, priv->gscan_buckets_num, bucket_id);
		return -EINVAL;
	}
	for (i = 0; i < priv->gscan_buckets_num; i++) {
		p = priv->gscan_res + i;
		if (p->scan_id == bucket_id)
			break;
	}
	if (!p) {
		netdev_err(vif->ndev, "%s the bucket isnot exsit.\n", __func__);
		return -EINVAL;
	}
	if (p->num_results >= MAX_AP_CACHE_PER_SCAN) {
		netdev_err(vif->ndev, "%s the scan result reach the MAX num.\n",
			   __func__);
		return -EINVAL;
	}

	for (i = 0; i < p->num_results; i++) {
		if (time_after(jiffies - VENDOR_SCAN_RESULT_EXPIRE,
			       p->results[i].ts)) {
			memcpy((void *)(&p->results[i]),
			       (void *)(&p->results[i + 1]),
			       sizeof(struct gscan_result)
			       * (p->num_results - i - 1));

			p->num_results--;
		}

		if (!memcmp(p->results[i].bssid, item->bssid, ETH_ALEN) &&
		    strlen(p->results[i].ssid) == strlen(item->ssid) &&
		    !memcmp(p->results[i].ssid, item->ssid,
			    strlen(item->ssid))) {
			netdev_err(vif->ndev,
				   "%s BSS : %s  %pM exist, but also update it.\n",
				   __func__, item->ssid, item->bssid);

			memcpy((void *)(&p->results[i]),
			       (void *)item,
			       sizeof(struct gscan_result));
			return 0;
		}
	}
	memcpy((void *)(&p->results[p->num_results]),
	       (void *)item, sizeof(struct gscan_result));
	p->results[p->num_results].ie_length = 0;
	p->results[p->num_results].ie_data[0] = 0;
	p->num_results++;
	return 0;
}

static int vendor_cache_hotlist_result(struct sprd_vif *vif,
				       struct gscan_result *item)
{
	struct sprd_priv *priv = vif->priv;
	u32 i;
	struct sprd_gscan_hotlist_results *p = priv->hotlist_res;

	if (!priv->hotlist_res) {
		netdev_err(vif->ndev, "%s the hotlist buffer invalid!\n",
			   __func__);
		return -EINVAL;
	}

	if (p->num_results >= MAX_HOTLIST_APS) {
		netdev_err(vif->ndev,
			   "%s the hotlist result reach the MAX num.\n",
			   __func__);
		return -EINVAL;
	}

	for (i = 0; i < p->num_results; i++) {
		if (time_after(jiffies - VENDOR_SCAN_RESULT_EXPIRE,
			       p->results[i].ts)) {
			memcpy((void *)(&p->results[i]),
			       (void *)(&p->results[i + 1]),
			       sizeof(struct gscan_result)
			       * (p->num_results - i - 1));

			p->num_results--;
		}

		if (!memcmp(p->results[i].bssid, item->bssid, ETH_ALEN) &&
		    strlen(p->results[i].ssid) == strlen(item->ssid) &&
		    !memcmp(p->results[i].ssid, item->ssid,
			    strlen(item->ssid))) {
			netdev_err(vif->ndev,
				   "%s BSS : %s  %pM exist, but also update it.\n",
				   __func__, item->ssid, item->bssid);

			memcpy((void *)(&p->results[i]),
			       (void *)item,
			       sizeof(struct gscan_result));
			return 0;
		}
	}
	memcpy((void *)(&p->results[p->num_results]),
	       (void *)item, sizeof(struct gscan_result));
	p->results[p->num_results].ie_length = 0;
	p->results[p->num_results].ie_data[0] = 0;
	p->num_results++;
	return 0;
}

static int vendor_cache_significant_change_result(struct sprd_vif *vif,
						  u8 *data,
						  u16 data_len)
{
	struct sprd_priv *priv = vif->priv;
	struct significant_change_info *frame;
	struct sprd_significant_change_result *p = priv->significant_res;
	u8 *pos = data;
	u16 avail_len = data_len;

	if (!priv->significant_res) {
		netdev_err(vif->ndev,
			   "%s the significant_change buffer invalid!\n",
			   __func__);
		return -EINVAL;
	}

	if (p->num_results >= MAX_SIGNIFICANT_CHANGE_APS) {
		netdev_err(vif->ndev,
			   "%s the significant_change result reach the MAX num.\n",
			   __func__);
		return -EINVAL;
	}

	while (avail_len > 0) {
		if (avail_len < (sizeof(struct significant_change_info) + 1)) {
			netdev_err(vif->ndev,
				   "%s invalid available length: %d!\n",
				   __func__, avail_len);
			break;
		}

		pos++;
		frame = (struct significant_change_info *)pos;

		if (p->num_results < MAX_SIGNIFICANT_CHANGE_APS)
			memcpy((void *)(&p->results[p->num_results]),
			       (void *)pos,
			       sizeof(struct significant_change_info));
		p->num_results++;

		avail_len -= sizeof(struct significant_change_info) + 1;
		pos += sizeof(struct significant_change_info);
	}
	return 0;
}

static void vendor_report_epno_results(struct sprd_vif *vif, u8 *data,
				       u16 data_len)
{
	int i;
	u64 msecs, now;
	struct sk_buff *skb;
	struct nlattr *attr, *sub_attr;
	struct epno_results *epno_results;
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;

	print_hex_dump_debug("epno result:", DUMP_PREFIX_OFFSET,
			     16, 1, data, data_len, true);

	epno_results = (struct epno_results *)data;
	if (epno_results->nr_scan_results <= 0) {
		pr_err("%s invalid data\n", __func__);
		return;
	}

	skb = cfg80211_vendor_event_alloc(wiphy, &vif->wdev, data_len,
					  VENDOR_CMD_PNO_NETWORK_FOUND,
					  GFP_KERNEL);
	if (!skb) {
		netdev_err(vif->ndev, "skb alloc failed");
		return;
	}

	nla_put_u32(skb, ATTR_GSCAN_RESULTS_REQUEST_ID, epno_results->request_id);
	nla_put_u32(skb, ATTR_GSCAN_RESULTS_NUM_RESULTS_AVAILABLE,
		    epno_results->nr_scan_results);
	nla_put_u8(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_MORE_DATA, 0);

	attr = nla_nest_start(skb, ATTR_GSCAN_RESULTS_LIST);
	if (!attr)
		goto failed;

	now = jiffies;

	if (now > epno_results->boot_time) {
		msecs = jiffies_to_msecs(now - epno_results->boot_time);
	} else {
		now += (MAX_JIFFY_OFFSET - epno_results->boot_time) + 1;
		msecs = jiffies_to_msecs(now);
	}

	for (i = 0; i < epno_results->nr_scan_results; i++) {
		sub_attr = nla_nest_start(skb, i);
		if (!sub_attr)
			goto failed;
		nla_put_u64_64bit(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_TIME_STAMP,
				  msecs, 0);
		nla_put(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_BSSID, ETH_ALEN,
			epno_results->results[i].bssid);
		nla_put_u32(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_CHANNEL,
			    epno_results->results[i].channel);
		nla_put_s32(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_RSSI,
			    epno_results->results[i].rssi);
		nla_put_u32(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT,
			    epno_results->results[i].rtt);
		nla_put_u32(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT_SD,
			    epno_results->results[i].rtt_sd);
		nla_put_u16(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_BEACON_PERIOD,
			    epno_results->results[i].beacon_period);
		nla_put_u16(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_CAPABILITY,
			    epno_results->results[i].capability);
		nla_put_string(skb, ATTR_GSCAN_RESULTS_SCAN_RESULT_SSID,
			       epno_results->results[i].ssid);

		nla_nest_end(skb, sub_attr);
	}

	nla_nest_end(skb, attr);

	cfg80211_vendor_event(skb, GFP_KERNEL);
	pr_debug("report epno event success, count = %d\n",
	       epno_results->nr_scan_results);
	return;

failed:
	kfree_skb(skb);
	pr_err("%s report epno event failed\n", __func__);
}

/* report full scan result to upper layer, it will only report one AP
 * including its IE data
 */
static int vendor_report_full_scan(struct sprd_vif *vif,
				   struct gscan_result *item)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *reply;
	int payload, rlen;
	int ret = 0;

	rlen = sizeof(struct gscan_result) + item->ie_length;
	payload = rlen + 0x100;
	reply = cfg80211_vendor_event_alloc(wiphy, &vif->wdev,
					    payload,
		VENDOR_SUBCMD_GSCAN_FULL_SCAN_RESULT_INDEX,
		GFP_KERNEL);
	if (!reply) {
		ret = -ENOMEM;
		goto out;
	}

	if (nla_put_u32(reply, ATTR_GSCAN_RESULTS_REQUEST_ID,
			priv->gscan_req_id) ||
	    nla_put_u64_64bit(reply,
			      ATTR_GSCAN_RESULTS_SCAN_RESULT_TIME_STAMP,
			      item->ts, 0) ||
	    nla_put(reply, ATTR_GSCAN_RESULTS_SCAN_RESULT_SSID,
		    sizeof(item->ssid),
		    item->ssid) ||
	    nla_put(reply, ATTR_GSCAN_RESULTS_SCAN_RESULT_BSSID,
		    6,
		    item->bssid) ||
	    nla_put_u32(reply,
			ATTR_GSCAN_RESULTS_SCAN_RESULT_CHANNEL,
			item->channel) ||
	    nla_put_s32(reply, ATTR_GSCAN_RESULTS_SCAN_RESULT_RSSI,
			item->rssi) ||
	    nla_put_u32(reply, ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT,
			item->rtt) ||
	    nla_put_u32(reply,
			ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT_SD,
			item->rtt_sd) ||
	    nla_put_u16(reply,
			ATTR_GSCAN_RESULTS_SCAN_RESULT_BEACON_PERIOD,
			item->beacon_period) ||
	    nla_put_u16(reply,
			ATTR_GSCAN_RESULTS_SCAN_RESULT_CAPABILITY,
			item->capability) ||
	    nla_put_u32(reply,
			ATTR_GSCAN_RESULTS_SCAN_RESULT_IE_LENGTH, item->ie_length)) {
		netdev_err(vif->ndev, "%s nla put fail\n", __func__);
		goto out_put_fail;
	}
	if (nla_put(reply, ATTR_GSCAN_RESULTS_SCAN_RESULT_IE_DATA,
		    item->ie_length, item->ie_data)) {
		netdev_err(vif->ndev, "%s nla put fail\n", __func__);
		goto out_put_fail;
	}

	cfg80211_vendor_event(reply, GFP_KERNEL);
out:
	return ret;

out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

/* report event to upper layer when buffer is full,
 * it only include event, not scan result
 */
static int vendor_buffer_full_event(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *reply;
	int payload, rlen;
	int ret = 0;

	rlen = sizeof(enum vendor_gscan_event);
	payload = rlen + 0x100;
	reply = cfg80211_vendor_event_alloc(wiphy, &vif->wdev,
					    payload,
		VENDOR_SUBCMD_GSCAN_SCAN_RESULTS_AVAILABLE_INDEX,
		GFP_KERNEL);
	if (!reply) {
		ret = -ENOMEM;
		goto out;
	}

	if (nla_put_u32(reply, ATTR_GSCAN_RESULTS_REQUEST_ID, priv->gscan_req_id))
		goto out_put_fail;

	cfg80211_vendor_event(reply, GFP_KERNEL);
out:
	return ret;
out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

static int vendor_hotlist_change_event(struct sprd_vif *vif,
				       u32 report_event)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *reply;
	int payload, rlen, event_idx;
	int ret = 0, j, moredata = 0;
	struct nlattr *cached_list;
	struct nlattr *ap;
	struct sprd_gscan_hotlist_results *p = priv->hotlist_res;

	rlen = priv->hotlist_res->num_results
	    * sizeof(struct gscan_result) + sizeof(u32);
	payload = rlen + 0x100;

	if (report_event & REPORT_EVENTS_HOTLIST_RESULTS_FOUND) {
		event_idx = VENDOR_SUBCMD_GSCAN_HOTLIST_AP_FOUND_INDEX;
	} else if (report_event & REPORT_EVENTS_HOTLIST_RESULTS_LOST) {
		event_idx = VENDOR_SUBCMD_GSCAN_HOTLIST_AP_LOST_INDEX;
	} else {
		/* unknown event, should not happened */
		event_idx = VENDOR_GSCAN_WIFI_EVT_RESERVED1;
	}

	reply = cfg80211_vendor_event_alloc(wiphy, &vif->wdev, payload,
					    event_idx, GFP_KERNEL);

	if (!reply)
		return -ENOMEM;

	if (nla_put_u32(reply, ATTR_GSCAN_RESULTS_REQUEST_ID,
			priv->hotlist_res->req_id) ||
	    nla_put_u32(reply,
			ATTR_GSCAN_RESULTS_NUM_RESULTS_AVAILABLE,
			priv->hotlist_res->num_results)) {
		netdev_err(vif->ndev, "failed to put!\n");
		goto out_put_fail;
	}

	if (nla_put_u8(reply, ATTR_GSCAN_RESULTS_SCAN_RESULT_MORE_DATA, moredata)) {
		netdev_err(vif->ndev, "failed to put!\n");
		goto out_put_fail;
	}

	if (priv->hotlist_res->num_results == 0)
		goto out_put_fail;

	cached_list = nla_nest_start(reply, ATTR_GSCAN_RESULTS_LIST);
	if (!cached_list)
		goto out_put_fail;

	for (j = 0; j < priv->hotlist_res->num_results; j++) {
		pr_info("[index=%d] Timestamp(%lu) Ssid (%s) Bssid: %pM Channel (%d) Rssi (%d) RTT (%u) RTT_SD (%u)\n",
			j, p->results[j].ts, p->results[j].ssid,
			p->results[j].bssid, p->results[j].channel,
			p->results[j].rssi, p->results[j].rtt,
			p->results[j].rtt_sd);

		ap = nla_nest_start(reply, j + 1);
		if (!ap) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put_u64_64bit(reply,
				      ATTR_GSCAN_RESULTS_SCAN_RESULT_TIME_STAMP,
				      p->results[j].ts, 0)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put(reply,
			    ATTR_GSCAN_RESULTS_SCAN_RESULT_SSID,
			    sizeof(p->results[j].ssid), p->results[j].ssid)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put(reply,
			    ATTR_GSCAN_RESULTS_SCAN_RESULT_BSSID,
			    sizeof(p->results[j].bssid), p->results[j].bssid)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put_u32(reply,
				ATTR_GSCAN_RESULTS_SCAN_RESULT_CHANNEL,
				p->results[j].channel)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put_s32(reply,
				ATTR_GSCAN_RESULTS_SCAN_RESULT_RSSI,
				p->results[j].rssi)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put_u32(reply,
				ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT,
				p->results[j].rtt)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put_u32(reply,
				ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT_SD,
				p->results[j].rtt_sd)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		nla_nest_end(reply, ap);
	}
	nla_nest_end(reply, cached_list);

	cfg80211_vendor_event(reply, GFP_KERNEL);
	/* reset results buffer when finished event report */
	if (vif->priv->hotlist_res) {
		memset(vif->priv->hotlist_res, 0x0,
		       sizeof(struct sprd_gscan_hotlist_results));
	}

	return ret;

out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

static int vendor_significant_change_event(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *reply;
	int payload, rlen;
	int ret = 0, j;
	struct nlattr *cached_list;
	struct nlattr *ap;
	struct significant_change_info *p;

	rlen = sizeof(struct sprd_significant_change_result);
	payload = rlen + 0x100;

	reply = cfg80211_vendor_event_alloc(wiphy, &vif->wdev,
					    payload,
					VENDOR_SUBCMD_SIGNIFICANT_CHANGE_INDEX,
					    GFP_KERNEL);

	if (!reply)
		return -ENOMEM;

	if (nla_put_u32(reply, ATTR_GSCAN_RESULTS_REQUEST_ID,
			priv->significant_res->req_id) ||
	    nla_put_u32(reply,
			ATTR_GSCAN_RESULTS_NUM_RESULTS_AVAILABLE,
			priv->significant_res->num_results)) {
		netdev_err(vif->ndev, "failed to put!\n");
		goto out_put_fail;
	}

	cached_list = nla_nest_start(reply, ATTR_GSCAN_RESULTS_LIST);
	if (!cached_list)
		goto out_put_fail;

	for (j = 0; j < priv->significant_res->num_results; j++) {
		p = priv->significant_res->results + j;
		ap = nla_nest_start(reply, j + 1);
		if (!ap) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}

		if (nla_put(reply,
			    ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_BSSID,
			    sizeof(p->bssid), p->bssid)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}

		if (nla_put_u32(reply,
				ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_CHANNEL,
				p->channel)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}

		if (nla_put_u32(reply,
				ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_NUM_RSSI,
				p->num_rssi)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}
		if (nla_put(reply,
			    ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_RSSI_LIST,
			    sizeof(s8) * 3,	/* here, we fixed rssi list as 3 */
			    p->rssi)) {
			netdev_err(vif->ndev, "failed to put!\n");
			goto out_put_fail;
		}

		nla_nest_end(reply, ap);
	}
	nla_nest_end(reply, cached_list);

	cfg80211_vendor_event(reply, GFP_KERNEL);

	/* reset results buffer when finished event report */
	if (vif->priv->significant_res) {
		memset(vif->priv->significant_res, 0x0,
		       sizeof(struct sprd_significant_change_result));
	}

	return ret;

out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

void sc2355_report_gscan_frame_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	u32 report_event;
	u8 *pos = data;
	s32 avail_len = len;
	struct gscan_result *frame;
	u16 buf_len;
	u8 bucket_id = 0;

	report_event = *(u32 *)pos;
	avail_len -= sizeof(u32);
	pos += sizeof(u32);

	if (report_event & REPORT_EVENTS_EPNO)
		return vendor_report_epno_results(vif, pos, avail_len);

	/* significant change result is different with gsan with, deal it specially */
	if (report_event & REPORT_EVENTS_SIGNIFICANT_CHANGE) {
		vendor_cache_significant_change_result(vif, pos, avail_len);
		vendor_significant_change_event(vif);
		return;
	}

	while (avail_len > 0) {
		if (avail_len < sizeof(struct gscan_result)) {
			netdev_err(vif->ndev,
				   "%s invalid available length: %d!\n",
				   __func__, avail_len);
			break;
		}

		bucket_id = *(u8 *)pos;
		pos += sizeof(u8);
		frame = (struct gscan_result *)pos;
		frame->ts = jiffies;
		buf_len = frame->ie_length;

		if (report_event == REPORT_EVENTS_BUFFER_FULL ||
		    (report_event & REPORT_EVENTS_EACH_SCAN)) {
			vendor_cache_scan_result(vif, bucket_id, frame);
		} else if (report_event & REPORT_EVENTS_FULL_RESULTS) {
			vendor_report_full_scan(vif, frame);
		} else if (report_event & REPORT_EVENTS_HOTLIST_RESULTS_FOUND ||
			 report_event & REPORT_EVENTS_HOTLIST_RESULTS_LOST) {
			vendor_cache_hotlist_result(vif, frame);
		}

		avail_len -= sizeof(struct gscan_result) + buf_len + 1;
		pos += sizeof(struct gscan_result) + buf_len;

		netdev_info(vif->ndev,
			    "%s ch:%d id:%d len:%d aval:%d, report_event:%d\n",
			    __func__, frame->channel, bucket_id, buf_len,
			    avail_len, report_event);
	}

	if (report_event == REPORT_EVENTS_BUFFER_FULL) {
		vendor_buffer_full_event(vif);
	} else if (report_event & REPORT_EVENTS_EACH_SCAN) {
		sc2355_gscan_done(vif, bucket_id);
	} else if (report_event & REPORT_EVENTS_HOTLIST_RESULTS_FOUND ||
		 report_event & REPORT_EVENTS_HOTLIST_RESULTS_LOST) {
		vendor_hotlist_change_event(vif, report_event);
	}
}

/* report scan done event to upper layer */
int sc2355_gscan_done(struct sprd_vif *vif, u8 bucketid)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *reply;
	int payload, rlen, ret = 0;
	u8 event_type;

	rlen = sizeof(enum vendor_subcmds_index);
	payload = rlen + 0x100;
	reply = cfg80211_vendor_event_alloc(wiphy, &vif->wdev,
					    payload,
		VENDOR_SUBCMD_GSCAN_SCAN_EVENT_INDEX,
		GFP_KERNEL);
	if (!reply) {
		ret = -ENOMEM;
		goto out;
	}

	if (nla_put_u32(reply, ATTR_GSCAN_RESULTS_REQUEST_ID, priv->gscan_req_id))
		goto out_put_fail;

	event_type = VENDOR_GSCAN_EVT_COMPLETE;
	if (nla_put_u8(reply, ATTR_GSCAN_RESULTS_SCAN_EVENT_TYPE, event_type))
		goto out_put_fail;

	cfg80211_vendor_event(reply, GFP_KERNEL);
out:
	return ret;
out_put_fail:
	kfree_skb(reply);
	WARN_ON(1);
	return -EMSGSIZE;
}

void sc2355_evt_rssi_monitor(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *skb;
	struct rssi_monitor_event *mon = (struct rssi_monitor_event *)data;

	skb = cfg80211_vendor_event_alloc(wiphy, &vif->wdev,
					  EVENT_BUF_SIZE + NLMSG_HDRLEN,
				VENDOR_EVENT_NAN_MONITOR_RSSI,
				GFP_KERNEL);
	if (!skb) {
		pr_err("%s vendor alloc event failed\n", __func__);
		return;
	}
	pr_info("Req Id:%u,current RSSI:%d, Current BSSID:%pM\n",
		mon->request_id, mon->curr_rssi, mon->curr_bssid);
	if (nla_put_u32(skb, ATTR_RSSI_MONITOR_REQUEST_ID,
			mon->request_id) ||
	    nla_put(skb, ATTR_RSSI_MONITOR_CUR_BSSID,
		    sizeof(mon->curr_bssid), mon->curr_bssid) ||
	    nla_put_s8(skb, ATTR_RSSI_MONITOR_CUR_RSSI,
		       mon->curr_rssi)) {
		pr_err("nla data put fail\n");
		goto fail;
	}

	cfg80211_vendor_event(skb, GFP_KERNEL);
	return;

fail:
	kfree_skb(skb);
}

int sc2355_report_acs_lte_event(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct sk_buff *reply;
	int payload, ret = 0;

	payload = 4;
	reply =
	    cfg80211_vendor_event_alloc(wiphy, &vif->wdev, payload,
					SPRD_ACS_LTE_EVENT_INDEX, GFP_KERNEL);
	if (!reply) {
		wiphy_err(wiphy, "%s alloc acs lte event error\n", __func__);
		ret = -ENOMEM;
		goto out;
	}

	cfg80211_vendor_event(reply, GFP_KERNEL);
out:
	return ret;
}

int sc2355_vendor_init(struct wiphy *wiphy)
{
#ifdef CONFIG_SC2355_WLAN_RTT
	struct sprd_priv *priv = wiphy_priv(wiphy);
#endif /* CONFIG_SC2355_WLAN_RTT */

	wiphy->vendor_commands = vendor_cmd;
	wiphy->n_vendor_commands = ARRAY_SIZE(vendor_cmd);
	wiphy->vendor_events = vendor_events;
	wiphy->n_vendor_events = ARRAY_SIZE(vendor_events);
#ifdef CONFIG_SC2355_WLAN_RTT
	sc2355_rtt_init(priv);
#endif /* CONFIG_SC2355_WLAN_RTT */

	return 0;
}

int sc2355_vendor_deinit(struct wiphy *wiphy)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);

#ifdef CONFIG_SC2355_WLAN_RTT
	sc2355_rtt_deinit(priv);
#endif /* CONFIG_SC2355_WLAN_RTT */
	wiphy->vendor_commands = NULL;
	wiphy->n_vendor_commands = 0;
	kfree(priv->gscan_res);
	kfree(priv->hotlist_res);
	kfree(priv->significant_res);

	return 0;
}
