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
#include "common/acs.h"
#include "common/cfg80211.h"
#include "common/chip_ops.h"
#include "common/common.h"
#include "common/report.h"
#include "scan.h"

static void scan_clean_list(struct sprd_vif *vif)
{
	struct scan_result *node, *pos;
	int count = 0;

	list_for_each_entry_safe(node, pos, &vif->scan_head_ptr, list) {
		list_del(&node->list);
		kfree(node);
		count++;
	}
	pr_err("delete scan node num:%d\n", count);
}

void sc2355_clean_scan(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;

	if (priv->scan_vif && priv->scan_vif == vif) {
		spin_lock_bh(&priv->scan_lock);

		if (priv->scan_request) {
			/* delete scan node */
			if (!list_empty(&vif->scan_head_ptr))
				scan_clean_list(vif);
		}
		spin_unlock_bh(&priv->scan_lock);
	}
}

static void scan_init_list(struct sprd_vif *vif)
{
	if (!list_empty(&vif->scan_head_ptr)) {
		/*clean scan list if not empty first*/
		scan_clean_list(vif);
	}
	INIT_LIST_HEAD(&vif->scan_head_ptr);
}

void clean_survey_info_list(struct sprd_vif *vif)
{
	struct bssid_node *bssid = NULL, *pos_bssid = NULL;
	struct survey_info_node *info = NULL, *pos_info = NULL;

	list_for_each_entry_safe(info, pos_info,
				 &vif->survey_info_list, survey_list) {
		list_del(&info->survey_list);

		if (!list_empty(&info->bssid_list)) {
			list_for_each_entry_safe(bssid, pos_bssid,
						 &info->bssid_list, list) {
				list_del(&bssid->list);
				kfree(bssid);
				bssid = NULL;
			}
		}

		kfree(info);
		info = NULL;
	}
}

static unsigned short scan_cal_total_beacon(struct sprd_vif *vif,
					    struct survey_info_node *info)
{
	unsigned short total_beacon = 0;
	short pos_chan, chan;

	total_beacon += info->beacon_num;
	chan = (short)info->chan;

	if (chan > 0 && chan < 15) {
		/* Calculate overlapping channels */
		list_for_each_entry(info, &vif->survey_info_list, survey_list) {
			pos_chan = (short)info->chan;
			if (pos_chan > (chan - 4) && pos_chan < (chan + 4) &&
			    pos_chan != chan) {
				total_beacon += info->beacon_num;
			}
		}
	}

	netdev_info(vif->ndev, "survey chan: %d, total beacon: %d!\n",
		    chan, total_beacon);
	return total_beacon;
}

/* Transfer beacons to survey info */
void transfer_survey_info(struct sprd_vif *vif)
{
	struct ieee80211_channel *channel = NULL;
	struct wiphy *wiphy = vif->wdev.wiphy;
	struct survey_info_node *info = NULL;
	u16 band;
	unsigned int freq;
	unsigned short total_beacon = 0;

	list_for_each_entry(info, &vif->survey_info_list, survey_list) {
		band = sprd_channel_to_band(info->chan);
		freq = ieee80211_channel_to_frequency(info->chan, band);
		channel = ieee80211_get_channel(wiphy, freq);
		if (channel) {
			total_beacon = scan_cal_total_beacon(vif, info);
			info->cca_busy_time =
			    (total_beacon < 20) ? total_beacon : 20;
			info->noise =
			    -95 + ((total_beacon < 30) ? total_beacon : 30);
			info->channel = channel;
		}

		freq = 0;
		channel = NULL;
		total_beacon = 0;
	}
}

static bool scan_find_bssid(struct survey_info_node *info,
			    unsigned char *nbssid)
{
	struct bssid_node *bssid = NULL;
	int ret = false;

	if (!list_empty(&info->bssid_list)) {
		list_for_each_entry(bssid, &info->bssid_list, list) {
			if (!memcmp(bssid->bssid, nbssid, ETH_ALEN)) {
				ret = true;
				break;
			}
		}
	}

	return ret;
}

static struct survey_info_node *scan_find_survey_info(struct sprd_vif *vif,
						      unsigned short chan)
{
	struct survey_info_node *info = NULL, *result = NULL;

	if (!list_empty(&vif->survey_info_list)) {
		list_for_each_entry(info, &vif->survey_info_list, survey_list) {
			if (chan == info->chan) {
				result = info;
				break;
			}
		}
	}

	return result;
}

static void scan_acs_result(struct sprd_vif *vif, u16 chan,
			    struct ieee80211_mgmt *mgmt)
{
	struct survey_info_node *info = NULL;
	struct bssid_node *bssid = NULL;

	info = scan_find_survey_info(vif, chan);
	if (info) {
		if (!scan_find_bssid(info, mgmt->bssid)) {
			bssid = kmalloc(sizeof(*bssid), GFP_KERNEL);
			if (bssid) {
				ether_addr_copy(bssid->bssid, mgmt->bssid);
				list_add_tail(&bssid->list, &info->bssid_list);
				info->beacon_num++;
			} else {
				netdev_err(vif->ndev,
					   "%s no memory for bssid!\n",
					   __func__);
			}
		}
	}
}

static void sc2355_cancel_scan(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;
	struct cfg80211_scan_info info;
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;
	u8 fw_ver = 0;

	pr_info("%s enter==\n", __func__);

	if (priv->scan_vif && priv->scan_vif == vif) {
		if (timer_pending(&priv->scan_timer))
			del_timer_sync(&priv->scan_timer);

		spin_lock_bh(&priv->scan_lock);

		if (priv->scan_request) {
			info.aborted = true;
			fw_ver = (api + CMD_SCAN)->fw_version;
			if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
				u8 drv_ver = 0;

				drv_ver = (api + CMD_SCAN)->drv_version;
				fw_ver = min(fw_ver, drv_ver);
			}
			if (vif->mode == SPRD_MODE_AP && fw_ver == 1)
				transfer_survey_info(vif);
			/*delete scan node*/
			if (!list_empty(&vif->scan_head_ptr))
				scan_clean_list(vif);

			if (priv->scan_request->n_channels != 0)
				cfg80211_scan_done(priv->scan_request, &info);
			else
				netdev_info(vif->ndev,
					    "%s, %d, error, scan_request freed",
					    __func__, __LINE__);
			priv->scan_request = NULL;
			priv->scan_vif = NULL;
		}
		spin_unlock_bh(&priv->scan_lock);
	}
}

static void sc2355_cancel_sched_scan(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;

	if (priv->sched_scan_vif && priv->sched_scan_vif == vif) {
		spin_lock_bh(&priv->sched_scan_lock);
		if (priv->sched_scan_request) {
			priv->sched_scan_request = NULL;
			priv->sched_scan_vif = NULL;
		}
		spin_unlock_bh(&priv->sched_scan_lock);
	}
}

void sc2355_scan_timeout(struct timer_list *t)
{
	struct sprd_priv *priv = from_timer(priv, t, scan_timer);
	struct cfg80211_scan_info info;
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;
	u8 fw_ver = 0;

	pr_info("%s\n", __func__);

	spin_lock_bh(&priv->scan_lock);
	if (priv->scan_request) {
		info.aborted = true;
		fw_ver = (api + CMD_SCAN)->fw_version;
		if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
			u8 drv_ver = 0;

			drv_ver = (api + CMD_SCAN)->drv_version;
			fw_ver = min(fw_ver, drv_ver);
		}
		if (fw_ver == 1 && priv->scan_vif)
			clean_survey_info_list(priv->scan_vif);
		cfg80211_scan_done(priv->scan_request, &info);
		priv->scan_vif = NULL;
		priv->scan_request = NULL;
	}
	spin_unlock_bh(&priv->scan_lock);
}

int sc2355_scan(struct wiphy *wiphy, struct cfg80211_scan_request *request)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif =
	    container_of(request->wdev, struct sprd_vif, wdev);
	struct cfg80211_ssid *ssids = request->ssids;
	struct sprd_scan_ssid *scan_ssids;
	u8 *ssids_ptr = NULL;
	int ssids_len = 0;
	u32 channels = 0;
	const int ssids_bufsize = 512;
	unsigned int i, n;
	int ret;
	u16 n_5g_chn = 0, chns_5g[64];

	u32 flags = request->flags;
	int random_mac_flag;
	u8 rand_addr[ETH_ALEN];
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;
	u8 fw_ver = 0;

	netdev_info(vif->ndev, "%s n_channels %u\n", __func__,
		    request->n_channels);

	if (priv->scan_request)
		netdev_err(vif->ndev, "%s error scan %p running [%p, %p]\n",
			   __func__, priv->scan_request, priv->scan_vif, vif);
	if (vif->mode == SPRD_MODE_STATION ||
		vif->mode == SPRD_MODE_STATION_SECOND) {
		sc2355_random_mac_addr(rand_addr);
		if ((flags & NL80211_SCAN_FLAG_RANDOM_ADDR) && (priv->rand_mac_flag == 0)) {
			random_mac_flag = SPRD_ENABLE_SCAN_RANDOM_ADDR;
			wiphy_err(wiphy, "random mac addr: %pM\n", rand_addr);
		} else {
			wiphy_err(wiphy, "random mac feature disabled\n");
			random_mac_flag = SPRD_DISABLE_SCAN_RANDOM_ADDR;
		}
		if (sprd_set_random_mac(vif->priv, vif, random_mac_flag, rand_addr))
			wiphy_err(wiphy, "Failed to set random mac to STA!\n");
	}

	/* set WPS ie */
	if (request->ie_len > 0) {
		if (request->ie_len > SPRD_MAX_SCAN_REQ_IE_LEN) {
			netdev_err(vif->ndev, "%s invalid len: %zu\n", __func__,
				   request->ie_len);
			ret = -EOPNOTSUPP;
			goto err;
		}

		ret = sprd_set_probereq_ie(priv, vif, request->ie,
					   request->ie_len);
		if (ret)
			goto err;
	}

	for (i = 0; i < request->n_channels; i++) {
		switch (request->channels[i]->hw_value) {
		case 0:
			break;

		case 1 ... 14:
			channels |= (1 << (request->channels[i]->hw_value - 1));
			break;

		default:
			if (n_5g_chn >= ARRAY_SIZE(chns_5g))
				break;
			chns_5g[n_5g_chn] = request->channels[i]->hw_value;
			n_5g_chn++;
			break;
		}
		fw_ver = (api + CMD_SCAN)->fw_version;
		if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
			u8 drv_ver = 0;

			drv_ver = (api + CMD_SCAN)->drv_version;
			fw_ver = min(fw_ver, drv_ver);
		}
		if (vif->mode == SPRD_MODE_AP && fw_ver == 1) {
			struct survey_info_node *info = NULL;

			if (!i && !list_empty(&vif->survey_info_list)) {
				netdev_err(vif->ndev,
					   "%s survey info list is not empty!\n",
					   __func__);
				clean_survey_info_list(vif);
			}

			info = kmalloc(sizeof(*info), GFP_KERNEL);
			if (!info) {
				ret = -ENOMEM;
				goto err;
			}

			INIT_LIST_HEAD(&info->bssid_list);
			info->chan = request->channels[i]->hw_value;
			info->beacon_num = 0;
			info->channel = NULL;
			list_add_tail(&info->survey_list,
				      &vif->survey_info_list);
		}
	}

	n = min(request->n_ssids, SPRD_TOTAL_SSID_NR);
	if (n) {
		ssids_ptr = kzalloc(ssids_bufsize, GFP_KERNEL);
		if (!ssids_ptr) {
			ret = -ENOMEM;
			goto err;
		}

		scan_ssids = (struct sprd_scan_ssid *)ssids_ptr;
		for (i = 0; i < n; i++) {
			if (!ssids[i].ssid_len || ssids[i].ssid_len > IEEE80211_MAX_SSID_LEN)
				continue;
			scan_ssids->len = ssids[i].ssid_len;
			strncpy(scan_ssids->ssid, ssids[i].ssid,
				ssids[i].ssid_len);
			ssids_len += (ssids[i].ssid_len
				      + sizeof(scan_ssids->len));
			scan_ssids = (struct sprd_scan_ssid *)
			    (ssids_ptr + ssids_len);
		}
	}

	/*init scan list*/
	scan_init_list(vif);

	spin_lock_bh(&priv->scan_lock);
	priv->scan_request = request;
	priv->scan_vif = vif;
	spin_unlock_bh(&priv->scan_lock);
	mod_timer(&priv->scan_timer,
		  jiffies + SPRD_SCAN_TIMEOUT_MS * HZ / 1000);

	ret = sc2355_cmd_scan(vif->priv, vif, channels, ssids_len, ssids_ptr,
			      n_5g_chn, chns_5g);
	kfree(ssids_ptr);
	if (ret) {
		sc2355_cancel_scan(vif);
		goto err;
	}

	return 0;
err:
	netdev_err(vif->ndev, "%s failed (%d)\n", __func__, ret);
	return ret;
}

void sc2355_abort_scan(struct wiphy *wiphy, struct wireless_dev *wdev)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;
	u8 fw_ver = 0, drv_ver = 0;
	struct sprd_hif *hif;

	fw_ver = (api + CMD_SCAN)->fw_version;
	drv_ver = (api + CMD_SCAN)->drv_version;
	fw_ver = min(fw_ver, drv_ver);

	if (!priv) {
		pr_err("can not get priv!\n");
		return;
	}

	hif = &priv->hif;

	if (sprd_chip_is_exit(&priv->chip) || hif->cp_asserted) {
		pr_info("%s Assert happened!\n", __func__);
		if (vif->mode == SPRD_MODE_P2P_DEVICE) {
			pr_info("p2p device need cancel scan\n");
			sprd_report_scan_done(vif, true);
			pr_info("%s p2p device cancel scan finished!\n",
				__func__);
		}
	}

	if (fw_ver < 3) {
		wiphy_err(wiphy, "%s Abort scan not support.\n", __func__);
		return;
	}

	if (!priv->scan_request) {
		wiphy_err(wiphy, "%s Not running scan.\n", __func__);
		return;
	}

	if (priv->scan_request->wdev != wdev) {
		wiphy_err(wiphy,
			  "%s Running scan[%u] isn't equal to abort scan[%u]\n",
			  __func__, priv->scan_request->wdev->iftype,
			  wdev->iftype);
		return;
	}
	sc2355_cmd_abort_scan(priv, vif);
}

int sc2355_sched_scan_start(struct wiphy *wiphy, struct net_device *ndev,
			    struct cfg80211_sched_scan_request *request)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct cfg80211_sched_scan_plan *scan_plans = NULL;
	struct sprd_sched_scan *sscan_buf = NULL;
	struct sprd_vif *vif = NULL;
	struct cfg80211_ssid *ssid_tmp = NULL;
	struct cfg80211_match_set *match_ssid_tmp = NULL;
	int ret = 0;
	int i = 0, j = 0;

	if (!ndev) {
		netdev_err(ndev, "%s NULL ndev\n", __func__);
		return ret;
	}
	vif = netdev_priv(ndev);
	/*scan not allowed if closed*/
	if (!(vif->state & VIF_STATE_OPEN)) {
		wiphy_err(wiphy,
			  "%s, %d, error!mode%d scan after closed not allowed\n",
			  __func__, __LINE__, vif->mode);
		return -ENOMEM;
	}

	if (vif->priv->sched_scan_request) {
		netdev_err(ndev, "%s  schedule scan is running\n", __func__);
		return 0;
	}
	/*to protect the size of struct sprd_sched_scan*/
	if (request->n_channels > SPRD_TOTAL_CHAN_NR) {
		wiphy_err(wiphy, "%s, %d, error! request->n_channels=%d\n",
			  __func__, __LINE__, request->n_channels);
		request->n_channels = SPRD_TOTAL_CHAN_NR;
	}
	if (request->n_ssids > SPRD_TOTAL_SSID_NR) {
		wiphy_err(wiphy, "%s, %d, error! request->n_ssids=%d\n",
			  __func__, __LINE__, request->n_ssids);
		request->n_ssids = SPRD_TOTAL_SSID_NR;
	}
	if (request->n_match_sets > SPRD_TOTAL_SSID_NR) {
		wiphy_err(wiphy, "%s, %d, error! request->n_match_sets=%d\n",
			  __func__, __LINE__, request->n_match_sets);
		request->n_match_sets = SPRD_TOTAL_SSID_NR;
	}
	sscan_buf = kzalloc(sizeof(*sscan_buf), GFP_KERNEL);
	if (!sscan_buf)
		return -ENOMEM;

	scan_plans = request->scan_plans;
	sscan_buf->interval = scan_plans->interval;
	sscan_buf->flags = request->flags;

	if (request->min_rssi_thold <= NL80211_SCAN_RSSI_THOLD_OFF)
		sscan_buf->rssi_thold = 0;
	else if (request->min_rssi_thold < SPRD_MIN_RSSI_THOLD)
		sscan_buf->rssi_thold = SPRD_MIN_RSSI_THOLD;
	else
		sscan_buf->rssi_thold = request->min_rssi_thold;

	for (i = 0, j = 0; i < request->n_channels; i++) {
		u16 ch = cpu_to_le16(request->channels[i]->hw_value);

		if (ch == 0) {
			netdev_info(ndev, "%s  unknown frequency %dMhz\n",
				    __func__,
				    request->channels[i]->center_freq);
			continue;
		}

		netdev_info(ndev, "%s: channel is %d\n", __func__, ch);
		if ((j + 1) < SPRD_TOTAL_CHAN_NR)
			sscan_buf->channel[j + 1] = ch;
		j++;
	}
	sscan_buf->channel[0] = j;

	if (request->ssids && request->n_ssids > 0) {
		sscan_buf->n_ssids = request->n_ssids;

		for (i = 0; i < request->n_ssids; i++) {
			ssid_tmp = request->ssids + i;
			sscan_buf->ssid[i] = ssid_tmp->ssid;
		}
	}

	if (request->match_sets && request->n_match_sets > 0) {
		sscan_buf->n_match_ssids = request->n_match_sets;

		for (i = 0; i < request->n_match_sets; i++) {
			match_ssid_tmp = request->match_sets + i;
			sscan_buf->mssid[i] = match_ssid_tmp->ssid.ssid;
		}
	}
	sscan_buf->ie_len = request->ie_len;
	sscan_buf->ie = request->ie;

	spin_lock_bh(&priv->sched_scan_lock);
	vif->priv->sched_scan_request = request;
	vif->priv->sched_scan_vif = vif;
	spin_unlock_bh(&priv->sched_scan_lock);

	ret = sc2355_cmd_sched_scan_start(priv, vif, sscan_buf);
	if (ret)
		sc2355_cancel_sched_scan(vif);

	kfree(sscan_buf);
	return ret;
}

int sc2355_sched_scan_stop(struct wiphy *wiphy, struct net_device *ndev,
			   u64 reqid)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = NULL;
	int ret = 0;

	if (!ndev) {
		netdev_err(ndev, "%s NULL ndev\n", __func__);
		return ret;
	}
	vif = netdev_priv(ndev);
	ret = sc2355_cmd_sched_scan_stop(priv, vif);
	if (!ret) {
		spin_lock_bh(&priv->sched_scan_lock);
		vif->priv->sched_scan_request = NULL;
		vif->priv->sched_scan_vif = NULL;
		spin_unlock_bh(&priv->sched_scan_lock);
	} else {
		netdev_err(ndev, "%s  scan stop failed\n", __func__);
	}
	return ret;
}

void sc2355_report_scan_result(struct sprd_vif *vif, u16 chan, s16 rssi,
			       u8 *frame, u16 len)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct ieee80211_mgmt *mgmt = (struct ieee80211_mgmt *)frame;
	struct ieee80211_channel *channel;
	struct cfg80211_bss *bss;
	struct timespec64 ts;
	u16 band, capability, beacon_interval;
	u32 freq;
	s32 signal;
	u64 tsf;
	u8 *ie;
	size_t ielen;
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;
	u8 fw_ver = 0;
	const u8 *ssidie = NULL;
	u8 ssid_len = 0, ssid[IEEE80211_MAX_SSID_LEN + 1] = {0};

	if (!priv->scan_request && !priv->sched_scan_request) {
		netdev_err(vif->ndev, "%s Unexpected event\n", __func__);
		return;
	}

	band = sprd_channel_to_band(chan);
	freq = ieee80211_channel_to_frequency(chan, band);
	channel = ieee80211_get_channel(wiphy, freq);
	if (!channel) {
		netdev_err(vif->ndev, "%s invalid freq!\n", __func__);
		return;
	}

	if (!mgmt) {
		netdev_err(vif->ndev, "%s NULL frame!\n", __func__);
		return;
	}

	signal = rssi * 100;
	/*signal level enhance*/
	fw_ver = (api + CMD_SCAN)->fw_version;
	if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
		u8 drv_ver = 0;

		drv_ver = (api + CMD_SCAN)->drv_version;
		fw_ver = min(fw_ver, drv_ver);
	}
	if (vif->mode == SPRD_MODE_AP && fw_ver == 1)
		scan_acs_result(vif, chan, mgmt);

	ie = mgmt->u.probe_resp.variable;
	if (IS_ERR_OR_NULL(ie)) {
		netdev_err(vif->ndev, "%s Invalid IE in beacon!\n", __func__);
		return;
	}
	ielen = len - offsetof(struct ieee80211_mgmt, u.probe_resp.variable);
	if (ielen > SPRD_SCAN_RESULT_MAX_IE_LEN) {
		netdev_err(vif->ndev, "%s Invalid IE length!\n", __func__);
		return;
	}
	/* framework use system bootup time */
	ktime_get_boottime_ts64(&ts);
	tsf = (u64)ts.tv_sec * 1000000 + div_u64(ts.tv_nsec, 1000);
	beacon_interval = le16_to_cpu(mgmt->u.probe_resp.beacon_int);
	capability = le16_to_cpu(mgmt->u.probe_resp.capab_info);

	ssidie = cfg80211_find_ie(WLAN_EID_SSID, ie, ielen);
	if (ssidie && ssidie[1] && ssidie[1] <= IEEE80211_MAX_SSID_LEN) {
		ssid_len = ssidie[1];
		memcpy(ssid, (ssidie + 2), ssid_len);
	}

    //modified by HQ:zhuziwen sprd_patch_2247860 for wifi name security
	netdev_info(vif->ndev, "   %s, %pM %u, channel %2u, signal %d, freq %u\n",
		   ieee80211_is_probe_resp(mgmt->frame_control) ? "proberesp" : "beacon   ",
		   mgmt->bssid, ssid_len, chan, signal, freq);
    //modified by HQ:zhuziwen end

	bss = cfg80211_inform_bss(wiphy, channel, CFG80211_BSS_FTYPE_UNKNOWN,
				  mgmt->bssid, tsf, capability, beacon_interval,
				  ie, ielen, signal, GFP_KERNEL);

	if (unlikely(!bss))
		netdev_err(vif->ndev,
			   "%s failed to inform bss frame!\n", __func__);
	cfg80211_put_bss(wiphy, bss);

	if (vif->beacon_loss) {
		bss = cfg80211_get_bss(wiphy, NULL, vif->bssid,
				       vif->ssid, vif->ssid_len,
				       IEEE80211_BSS_TYPE_ESS,
				       IEEE80211_PRIVACY_ANY);
		if (bss) {
			cfg80211_unlink_bss(wiphy, bss);
			netdev_info(vif->ndev,
				    "unlink %pM due to beacon loss\n",
				    bss->bssid);
			vif->beacon_loss = 0;
		}
	}
}
