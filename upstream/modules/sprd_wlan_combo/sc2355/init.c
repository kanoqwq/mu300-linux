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

#include "cmdevt.h"
#include "common/cfg80211.h"
#include "common/channel_2g.h"
#include "common/channel_5g.h"
#include "common/chip_ops.h"
#include "common/common.h"
#include "qos.h"
#include "scan.h"
#include "txrx.h"

#define SPRD_MAX_PFN_LIST_COUNT	9

#define SPRD_G_RATE_NUM	28
#define G_RATES		(sprd_rates)
#define SPRD_A_RATE_NUM	24
#define A_RATES		(sprd_rates + 4)

#define g_htcap (IEEE80211_HT_CAP_SUP_WIDTH_20_40 | \
			IEEE80211_HT_CAP_SGI_20		 | \
			IEEE80211_HT_CAP_SGI_40)

#define a_htcap (IEEE80211_HT_CAP_SUP_WIDTH_20_40 | \
			IEEE80211_HT_CAP_SGI_20	| \
			IEEE80211_HT_CAP_SM_PS | IEEE80211_HT_CAP_SGI_40)

#define v_htcap (IEEE80211_VHT_CAP_MAX_MPDU_LENGTH_7991 | \
		IEEE80211_VHT_CAP_SU_BEAMFORMEE_CAPABLE | \
		IEEE80211_VHT_CAP_BEAMFORMEE_STS_SHIFT | \
		IEEE80211_VHT_CAP_MU_BEAMFORMEE_CAPABLE | \
		IEEE80211_VHT_CAP_MAX_A_MPDU_LENGTH_EXPONENT_SHIFT | \
		IEEE80211_VHT_CAP_VHT_TXOP_PS)

#define SPRD_BAND_2G	(&sc2355_band_2ghz)
#define SPRD_BAND_5G	(&sc2355_band_5ghz)
static struct ieee80211_supported_band sc2355_band_2ghz = {
	.n_channels = ARRAY_SIZE(sprd_2ghz_channels),
	.channels = sprd_2ghz_channels,
	.n_bitrates = SPRD_G_RATE_NUM,
	.bitrates = G_RATES,
	.ht_cap.cap = g_htcap,
	.ht_cap.ht_supported = true
};

static struct ieee80211_supported_band sc2355_band_5ghz = {
	.n_channels = ARRAY_SIZE(sprd_5ghz_channels),
	.channels = sprd_5ghz_channels,
	.n_bitrates = SPRD_A_RATE_NUM,
	.bitrates = A_RATES,
	.ht_cap.cap = a_htcap,
	.ht_cap.ht_supported = true,
	.vht_cap.vht_supported = true,
	.vht_cap.cap = v_htcap,
	.vht_cap.vht_mcs.rx_mcs_map = 0xfff0,
	.vht_cap.vht_mcs.tx_mcs_map = 0xfff0,
	.vht_cap.vht_mcs.rx_highest = 0,
	.vht_cap.vht_mcs.tx_highest = 0,
};

static void sc2355_ht_cap_update(struct ieee80211_sta_ht_cap *ht_info,
				 struct sprd_priv *priv)
{
	struct wiphy_sec2_t *sec2 = &priv->wiphy_sec2;

	pr_info("%s enter:\n", __func__);
	ht_info->ht_supported = true;
	/* set Max A-MPDU length factor */
	if (sec2->ampdu_para) {
		/* bit 0,1 */
		ht_info->ampdu_factor = (sec2->ampdu_para & 0x3);
		/* bit 2,3,4 */
		ht_info->ampdu_density = ((sec2->ampdu_para >> 2) & 0x7);
	}
	/* set HT capabilities map as described in 802.11n spec */
	if (sec2->ht_cap_info) {
		ht_info->cap = sec2->ht_cap_info;
		pr_debug("%s, %d, sec2->ht_cap_info=%x\n",
			 __func__, __LINE__, sec2->ht_cap_info);
	}
	pr_debug("%s, %d, ht_info->cap=%x\n", __func__, __LINE__, ht_info->cap);
	/* set Supported MCS rates */
	memcpy(&ht_info->mcs, &sec2->ht_mcs_set,
	       sizeof(struct ieee80211_mcs_info));
}

static void sc2355_vht_cap_update(struct ieee80211_sta_vht_cap *vht_cap,
				  struct sprd_priv *priv)
{
	struct wiphy_sec2_t *sec2 = &priv->wiphy_sec2;

	pr_info("%s enter:\n", __func__);
	vht_cap->vht_supported = true;
	if (sec2->vht_cap_info)
		vht_cap->cap = sec2->vht_cap_info;
	memcpy(&vht_cap->vht_mcs, &sec2->vht_mcs_set,
	       sizeof(struct ieee80211_vht_mcs_info));
}

static void save_chan_info(struct sprd_priv *priv, u32 band, u32 flags,
			   int center_freq)
{
	int index = 0;

	if (band == NL80211_BAND_2GHZ) {
		index = priv->ch_2g4_info.num_channels;
		priv->ch_2g4_info.channels[index] = center_freq;
		priv->ch_2g4_info.num_channels++;
	} else if (band == NL80211_BAND_5GHZ) {
		if (flags & IEEE80211_CHAN_RADAR) {
			index = priv->ch_5g_dfs_info.num_channels;
			priv->ch_5g_dfs_info.channels[index] = center_freq;
			priv->ch_5g_dfs_info.num_channels++;
		} else {
			index = priv->ch_5g_without_dfs_info.num_channels;
			priv->ch_5g_without_dfs_info.channels[index] =
			    center_freq;
			priv->ch_5g_without_dfs_info.num_channels++;
		}
	} else {
		pr_err("invalid band param!\n");
	}
}

static void sc2355_reg_notify(struct wiphy *wiphy,
			      struct regulatory_request *request)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct ieee80211_supported_band *sband;
	struct ieee80211_channel *chan;
	const struct ieee80211_freq_range *freq_range;
	const struct ieee80211_reg_rule *reg_rule;
	struct sprd_ieee80211_regdomain *rd = NULL;
	u32 band, channel, i;
	u32 last_start_freq;
	u32 n_rules = 0, rd_size;

	wiphy_info(wiphy, "%s %c%c initiator %d hint_type %d\n", __func__,
		   request->alpha2[0], request->alpha2[1],
		   request->initiator, request->user_reg_hint_type);

	memset(&priv->ch_2g4_info, 0, sizeof(struct sprd_channel_list));
	memset(&priv->ch_5g_without_dfs_info, 0,
	       sizeof(struct sprd_channel_list));
	memset(&priv->ch_5g_dfs_info, 0, sizeof(struct sprd_channel_list));

	/* Figure out the actual rule number */
	for (band = 0; band < NUM_NL80211_BANDS; band++) {
		sband = wiphy->bands[band];
		if (!sband)
			continue;

		last_start_freq = 0;
		for (channel = 0; channel < sband->n_channels; channel++) {
			chan = &sband->channels[channel];

			reg_rule =
			    freq_reg_info(wiphy, MHZ_TO_KHZ(chan->center_freq));
			if (IS_ERR(reg_rule))
				continue;

			freq_range = &reg_rule->freq_range;
			if (last_start_freq != freq_range->start_freq_khz) {
				last_start_freq = freq_range->start_freq_khz;
				n_rules++;
			}

			save_chan_info(priv, band, chan->flags,
				       (int)(chan->center_freq));
		}
	}

	rd_size = sizeof(struct sprd_ieee80211_regdomain) +
	    n_rules * sizeof(struct sprd_reg_rule);

	rd = kzalloc(rd_size, GFP_KERNEL);
	if (!rd)
		return;

	/* Fill regulatory domain */
	rd->n_reg_rules = n_rules;
	memcpy(rd->alpha2, request->alpha2, ARRAY_SIZE(rd->alpha2));
	for (band = 0, i = 0; band < NUM_NL80211_BANDS; band++) {
		sband = wiphy->bands[band];
		if (!sband)
			continue;

		last_start_freq = 0;
		for (channel = 0; channel < sband->n_channels; channel++) {
			chan = &sband->channels[channel];

			if (chan->flags & IEEE80211_CHAN_DISABLED)
				continue;

			reg_rule =
			    freq_reg_info(wiphy, MHZ_TO_KHZ(chan->center_freq));
			if (IS_ERR(reg_rule))
				continue;

			freq_range = &reg_rule->freq_range;
			if (last_start_freq != freq_range->start_freq_khz &&
			    i < n_rules) {
				last_start_freq = freq_range->start_freq_khz;
				memcpy(&rd->reg_rules[i].freq_range,
				       &reg_rule->freq_range,
				       sizeof(struct ieee80211_freq_range));
				memcpy(&rd->reg_rules[i].power_rule,
				       &reg_rule->power_rule,
				       sizeof(struct ieee80211_power_rule));
				rd->reg_rules[i].flags = reg_rule->flags;
				rd->reg_rules[i].dfs_cac_ms =
				    reg_rule->dfs_cac_ms;
				i++;

				wiphy_dbg(wiphy,
					  "   %d KHz - %d KHz @ %d KHz flags %#x\n",
					  freq_range->start_freq_khz,
					  freq_range->end_freq_khz,
					  freq_range->max_bandwidth_khz,
					  reg_rule->flags);
			}
		}
	}

	if (sc2355_set_regdom(priv, (u8 *)rd, rd_size))
		wiphy_err(wiphy, "%s failed to set regdomain!\n", __func__);

	sc2355_fcc_match_country(priv, request->alpha2);

	kfree(rd);
}

void sc2355_setup_wiphy(struct wiphy *wiphy, struct sprd_priv *priv)
{
	struct wiphy_sec2_t *sec2 = NULL;
	struct ieee80211_sta_vht_cap *vht_info = NULL;
	struct ieee80211_sta_ht_cap *ht_info = NULL;

	wiphy->mgmt_stypes = sprd_mgmt_stypes;
	wiphy->interface_modes =
	    BIT(NL80211_IFTYPE_STATION) | BIT(NL80211_IFTYPE_AP) |
	    BIT(NL80211_IFTYPE_P2P_GO) | BIT(NL80211_IFTYPE_P2P_CLIENT) |
	    BIT(NL80211_IFTYPE_P2P_DEVICE);

	wiphy->features |= NL80211_FEATURE_CELL_BASE_REG_HINTS;
	wiphy->flags &= ~WIPHY_FLAG_PS_ON_BY_DEFAULT;
	wiphy->flags |= WIPHY_FLAG_HAS_REMAIN_ON_CHANNEL;
	wiphy->signal_type = CFG80211_SIGNAL_TYPE_MBM;
	wiphy->max_scan_ssids = SPRD_MAX_SCAN_SSIDS;
	wiphy->max_scan_ie_len = SPRD_MAX_SCAN_IE_LEN;
	wiphy->cipher_suites = sprd_cipher_suites;
	wiphy->n_cipher_suites = ARRAY_SIZE(sprd_cipher_suites);
	wiphy->bands[NL80211_BAND_2GHZ] = SPRD_BAND_2G;
	wiphy->max_ap_assoc_sta = priv->max_ap_assoc_sta;

	if (priv->wiphy_sec2_flag) {
		/* update HT capa got from fw */
		ht_info = &wiphy->bands[NL80211_BAND_2GHZ]->ht_cap;
		sc2355_ht_cap_update(ht_info, priv);

		sec2 = &priv->wiphy_sec2;
		/* set antenna mask */
		if (sec2->antenna_tx) {
			pr_info("tx antenna:%d\n", sec2->antenna_tx);
			wiphy->available_antennas_tx = sec2->antenna_tx;
		}
		if (sec2->antenna_rx) {
			pr_info("rx antenna:%d\n", sec2->antenna_rx);
			wiphy->available_antennas_rx = sec2->antenna_rx;
		}
		/* set retry limit for short or long frame */
		if (sec2->retry_short) {
			pr_info("retry short num:%d\n", sec2->retry_short);
			wiphy->retry_short = sec2->retry_short;
		}
		if (sec2->retry_long) {
			pr_info("retry long num:%d\n", sec2->retry_long);
			wiphy->retry_long = sec2->retry_long;
		}
		/* Fragmentation threshold (dot11FragmentationThreshold) */
		if (sec2->frag_threshold &&
		    sec2->frag_threshold <= IEEE80211_MAX_FRAG_THRESHOLD) {
			pr_info("frag threshold:%d\n", sec2->frag_threshold);
			wiphy->frag_threshold = sec2->frag_threshold;
		} else {
			pr_info("flag threshold invalid:%d,set to default:%d\n",
				sec2->frag_threshold,
				IEEE80211_MAX_FRAG_THRESHOLD);
			sec2->frag_threshold = IEEE80211_MAX_FRAG_THRESHOLD;
		}
		/* RTS threshold (dot11RTSThreshold); -1 = RTS/CTS disabled */
		if (sec2->rts_threshold &&
		    sec2->rts_threshold <= IEEE80211_MAX_RTS_THRESHOLD) {
			pr_info("rts threshold:%d\n", sec2->rts_threshold);
			wiphy->rts_threshold = sec2->rts_threshold;
		} else {
			pr_info("rts threshold invalid:%d,set to default:%d\n",
				sec2->rts_threshold,
				IEEE80211_MAX_RTS_THRESHOLD);
			wiphy->rts_threshold = IEEE80211_MAX_RTS_THRESHOLD;
		}
	}
#ifdef CONFIG_PM
	/* Set WoWLAN flags */
	wiphy->wowlan = &sprd_wowlan_support;
#endif
	wiphy->max_remain_on_channel_duration = 5000;
	wiphy->max_num_pmkids = SPRD_MAX_NUM_PMKIDS;
	/* Random MAC addr is enabled by default. And needs to be
	 * disabled to pass WFA Certification.
	 */
	if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
		wiphy->features |= NL80211_FEATURE_SCAN_RANDOM_MAC_ADDR;
	} else {
		if (!(wfa_cap & SPRD_WFA_CAP_NON_RAN_MAC)) {
			pr_info
			    ("\tRandom MAC address scan default supported\n");
			wiphy->features |= NL80211_FEATURE_SCAN_RANDOM_MAC_ADDR;
		}
	}

	if (priv->fw_std & SPRD_STD_11D) {
		pr_info("\tIEEE802.11d supported\n");
		wiphy->reg_notifier = sc2355_reg_notify;
		wiphy->regulatory_flags |= REGULATORY_DISABLE_BEACON_HINTS;
	}

	if (priv->fw_std & SPRD_STD_11E) {
		pr_info("\tIEEE802.11e supported\n");
		wiphy->features |= NL80211_FEATURE_SUPPORTS_WMM_ADMISSION;
		wiphy->flags |= WIPHY_FLAG_AP_UAPSD;
	}

	if (priv->fw_std & SPRD_STD_11K)
		pr_info("\tIEEE802.11k supported\n");

	if (priv->fw_std & SPRD_STD_11R)
		pr_info("\tIEEE802.11r supported\n");

	if (priv->fw_std & SPRD_STD_11U)
		pr_info("\tIEEE802.11u supported\n");

	if (priv->fw_std & SPRD_STD_11V)
		pr_info("\tIEEE802.11v supported\n");

	if (priv->fw_std & SPRD_STD_11W)
		pr_info("\tIEEE802.11w supported\n");

	if (priv->fw_capa & SPRD_CAPA_5G) {
		pr_info("\tDual band supported\n");
		wiphy->bands[NL80211_BAND_5GHZ] = SPRD_BAND_5G;
		if (priv->wiphy_sec2_flag) {
			/* update HT capa got from fw */
			ht_info = &wiphy->bands[NL80211_BAND_5GHZ]->ht_cap;
			sc2355_ht_cap_update(ht_info, priv);
			/* update VHT capa got from fw */
			vht_info = &wiphy->bands[NL80211_BAND_5GHZ]->vht_cap;
			sc2355_vht_cap_update(vht_info, priv);
		}
	}

	if (priv->fw_capa & SPRD_CAPA_MCC) {
		pr_info("\tMCC supported\n");
		wiphy->n_iface_combinations = ARRAY_SIZE(sprd_iface_combos);
		wiphy->iface_combinations = sprd_iface_combos;
	} else {
		pr_info("\tSCC supported\n");
		wiphy->software_iftypes =
		    BIT(NL80211_IFTYPE_STATION) | BIT(NL80211_IFTYPE_AP) |
		    BIT(NL80211_IFTYPE_P2P_CLIENT) |
		    BIT(NL80211_IFTYPE_P2P_GO) | BIT(NL80211_IFTYPE_P2P_DEVICE);
	}

	if (priv->fw_capa & SPRD_CAPA_ACL) {
		pr_info("\tACL supported (%d)\n", priv->max_acl_mac_addrs);
		wiphy->max_acl_mac_addrs = priv->max_acl_mac_addrs;
	}

	if (priv->fw_capa & SPRD_CAPA_AP_SME) {
		pr_info("\tAP SME enabled\n");
		wiphy->flags |= WIPHY_FLAG_HAVE_AP_SME;
		wiphy->ap_sme_capa = 1;
	}

	if (priv->fw_capa & SPRD_CAPA_PMK_OKC_OFFLOAD &&
	    priv->fw_capa & SPRD_CAPA_11R_ROAM_OFFLOAD) {
		pr_info("\tRoaming offload supported\n");
		wiphy->flags |= WIPHY_FLAG_SUPPORTS_FW_ROAM;
	}

	if (priv->fw_capa & SPRD_CAPA_SCHED_SCAN) {
		pr_info("\tScheduled scan supported\n");
		wiphy->max_sched_scan_ssids = SPRD_MAX_PFN_LIST_COUNT;
		wiphy->max_match_sets = SPRD_MAX_PFN_LIST_COUNT;
		wiphy->max_sched_scan_ie_len = SPRD_MAX_SCAN_IE_LEN;
	}

	if (priv->fw_capa & SPRD_CAPA_TDLS) {
		pr_info("\tTDLS supported\n");
		wiphy->flags |= WIPHY_FLAG_SUPPORTS_TDLS;
		wiphy->flags |= WIPHY_FLAG_TDLS_EXTERNAL_SETUP;
		wiphy->features |= NL80211_FEATURE_TDLS_CHANNEL_SWITCH;
	}

	if (priv->fw_capa & SPRD_CAPA_LL_STATS)
		pr_info("\tLink layer stats supported\n");

	wiphy->max_sched_scan_reqs = 1;
	wiphy_ext_feature_set(wiphy,
			      NL80211_EXT_FEATURE_SCHED_SCAN_RELATIVE_RSSI);
	wiphy->features |= NL80211_FEATURE_SAE;

	if (priv->extend_feature & SPRD_EXTEND_FEATURE_OCE) {
                pr_info("\tOCE supported\n");
                wiphy_ext_feature_set(wiphy,
                                      NL80211_EXT_FEATURE_ACCEPT_BCAST_PROBE_RESP);
                wiphy_ext_feature_set(wiphy,
                                      NL80211_EXT_FEATURE_FILS_MAX_CHANNEL_TIME);
                wiphy_ext_feature_set(wiphy,
                                      NL80211_EXT_FEATURE_OCE_PROBE_REQ_DEFERRAL_SUPPRESSION);
                wiphy_ext_feature_set(wiphy,
                                      NL80211_EXT_FEATURE_OCE_PROBE_REQ_HIGH_TX_RATE);
        }
}

int sc2355_set_rekey(struct wiphy *wiphy, struct net_device *ndev,
		     struct cfg80211_gtk_rekey_data *data)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	sc2355_defrag_recover(vif);

	return sc2355_set_rekey_data(vif->priv, vif, data);
}

void sc2355_ops_update(struct cfg80211_ops *ops)
{
	ops->abort_scan = sc2355_abort_scan;
	ops->set_rekey_data = sc2355_set_rekey;
}

struct sprd_chip_ops sc2355_chip_ops = {
	.get_msg = sc2355_tx_get_msg,
	.free_msg = sc2355_tx_free_msg,
	.tx_prepare = sc2355_tx_prepare,
	.tx = sc2355_tx,
	.force_exit = sc2355_tx_force_exit,
	.is_exit = sc2355_tx_is_exit,
	.drop_tcp_msg = sc2355_tx_drop_tcp_msg,

	.setup_wiphy = sc2355_setup_wiphy,
	.cfg80211_ops_update = sc2355_ops_update,
	.cmd_init = sc2355_cmd_init,
	.cmd_deinit = sc2355_cmd_deinit,
	.get_fw_info = sc2355_get_fw_info,
	.open_fw = sc2355_open_fw,
	.close_fw = sc2355_close_fw,
	.power_save = sc2355_power_save,
	.set_sar = sc2355_set_sar,
	.fcc_reset = sc2355_fcc_reset_bo,
	.fcc_init = sc2355_fcc_init,
	.add_key = sc2355_add_key,
	.del_key = sc2355_del_key,
	.set_def_key = sc2355_set_def_key,
	.set_beacon_ie = sc2355_set_beacon_ie,
	.set_probereq_ie = sc2355_set_probereq_ie,
	.set_proberesp_ie = sc2355_set_proberesp_ie,
	.set_assocreq_ie = sc2355_set_assocreq_ie,
	.set_assocresp_ie = sc2355_set_assocresp_ie,
	.set_sae_ie = sc2355_set_sae_ie,
	.start_ap = sc2355_start_ap,
	.del_station = sc2355_del_station,
	.get_station = sc2355_get_station,
	.set_channel = sc2355_set_channel,
	.connect = sc2355_connect,
	.disconnect = sc2355_disconnect,
	.pmksa = sc2355_pmksa,
	.remain_chan = sc2355_remain_chan,
	.cancel_remain_chan = sc2355_cancel_remain_chan,
	.tx_mgmt = sc2355_tx_mgmt,
	.register_frame = sc2355_register_frame,
	.tdls_mgmt = sc2355_tdls_mgmt,
	.tdls_oper = sc2355_tdls_oper,
	.start_tdls_channel_switch = sc2355_start_tdls_channel_switch,
	.cancel_tdls_channel_switch = sc2355_cancel_tdls_channel_switch,
	.set_cqm_rssi = sc2355_set_cqm_rssi,
	.set_roam_offload = sc2355_set_roam_offload,
	.set_blacklist = sc2355_set_blacklist,
	.set_whitelist = sc2355_set_whitelist,
	.set_param = sc2355_set_param,
	.set_qos_map = sc2355_set_qos_map,
	.add_tx_ts = sc2355_add_tx_ts,
	.sync_wmm_param = sc2355_sync_wmm_param,
	.set_miracast = sc2355_set_miracast,
	.del_tx_ts = sc2355_del_tx_ts,
	.set_mc_filter = sc2355_set_mc_filter,
	.set_11v_feature_support = sc2355_set_11v_feature_support,
	.set_11v_sleep_mode = sc2355_set_11v_sleep_mode,
	.xmit_data2cmd = sprd_xmit_data2cmd_wq,
	.set_random_mac = sc2355_set_random_mac,
	.set_max_clients_allowed = sc2355_set_max_clients_allowed,
	.do_delay_work = sc2355_do_delay_work,
	.notify_ip = sc2355_notify_ip,
	.set_vowifi = sc2355_set_vowifi,
	.dump_survey = sc2355_dump_survey,
	.npi_send_recv = sc2355_npi_send_recv,
	.qos_init_default_map = sc2355_qos_init_default_map,
	.qos_enable = sc2355_qos_enable,
	.qos_wmm_ac_init = sc2355_qos_wmm_ac_init,
	.qos_reset_wmmac_parameters = sc2355_qos_reset_wmmac_parameters,
	.qos_reset_wmmac_ts_info = sc2355_qos_reset_wmmac_ts_info,
	.scan = sc2355_scan,
	.sched_scan_start = sc2355_sched_scan_start,
	.sched_scan_stop = sc2355_sched_scan_stop,
	.scan_timeout = sc2355_scan_timeout,
	.tcp_ack_init = sc2355_tcp_ack_init,
	.tcp_ack_deinit = sc2355_tcp_ack_deinit,
#ifdef CONFIG_SPRD_WLAN_VENDOR_SPECIFIC
	.vendor_init = sc2355_vendor_init,
	.vendor_deinit = sc2355_vendor_deinit,
#endif /* CONFIG_SPRD_WLAN_VENDOR_SPECIFIC */
	.send_data = sc2355_send_data,
	.send_data_offset = sc2355_send_data_offset,
	.fc_add_share_credit = sc2355_fc_add_share_credit,
	.defrag_recover = sc2355_defrag_recover,
	.set_sniffer = sc2355_set_sniffer,
};

MODULE_DESCRIPTION("Spreadtrum SC2355 WLAN Driver");
MODULE_AUTHOR("Spreadtrum WCN Division");
MODULE_LICENSE("GPL");
