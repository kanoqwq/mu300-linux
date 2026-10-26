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
#include "common.h"
#include "delay_work.h"
#include "chip_ops.h"

/* MU300: cfg80211_new_sta()/del_sta() take the wireless_dev in 7.x, the net_device before */
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(7, 0, 0))
#define MU300_STA_DEV(vif) (&(vif)->wdev)
#else
#define MU300_STA_DEV(vif) ((vif)->ndev)
#endif

void sprd_report_scan_done(struct sprd_vif *vif, bool abort)
{
	struct sprd_priv *priv = vif->priv;
	struct cfg80211_scan_info info;

	if (priv->scan_vif && priv->scan_vif == vif) {
		if (timer_pending(&priv->scan_timer))
			del_timer_sync(&priv->scan_timer);

		spin_lock_bh(&priv->scan_lock);
		if (priv->scan_request) {
			info.aborted = abort;
			/* delete scan node */

			if (priv->scan_request->n_channels != 0) {
				netdev_info(vif->ndev, "%s scan is %s\n",
					    __func__,
					    abort ? "Aborted" : "Done");
				cfg80211_scan_done(priv->scan_request, &info);
			} else {
				netdev_info(vif->ndev,
					    "%s, %d, error, scan_request freed",
					    __func__, __LINE__);
			}
			priv->scan_request = NULL;
			priv->scan_vif = NULL;
		}
		spin_unlock_bh(&priv->scan_lock);
	}
}
EXPORT_SYMBOL(sprd_report_scan_done);

void sprd_report_sched_scan_done(struct sprd_vif *vif, bool abort)
{
	struct sprd_priv *priv = vif->priv;
	u64 reqid = 0;

	if (priv->sched_scan_vif && priv->sched_scan_vif == vif) {
		spin_lock_bh(&priv->sched_scan_lock);
		if (priv->sched_scan_request) {
			cfg80211_sched_scan_results(vif->wdev.wiphy, reqid);
			netdev_info(priv->sched_scan_vif->ndev,
				    "%s report result\n", __func__);
			priv->sched_scan_request = NULL;
			priv->sched_scan_vif = NULL;
		}
		spin_unlock_bh(&priv->sched_scan_lock);
	}
}
EXPORT_SYMBOL(sprd_report_sched_scan_done);

/* MU300: the SC2355 firmware's soft-AP SAE offload (U30 Air, docs/U30AIR-SAE.md, FINDINGS 80) is switched off.
 * Its PMK record, vendor element dd 34 40:45:da:04 <PMK 32><PMKID 16>, arrived in this element list behind the
 * station's own association-request elements, with nothing to tell the firmware's record from one a client sent
 * over the air: a hostapd that imported it could be handed a PMK by a client that never knew the passphrase. So no
 * such record reaches cfg80211, whoever wrote it: every vendor element with that prefix is dropped, and so is a
 * malformed tail (an element longer than what is left), which no element parser takes apart. The list is compacted
 * in place; returns its new length. */
static u16 mu300_strip_sae_pmk_record(u8 *ies, u16 len)
{
	static const u8 prefix[4] = { 0x40, 0x45, 0xda, 0x04 };
	unsigned int in = 0, out = 0;

	while (in + 2 <= len) {
		unsigned int elen = ies[in + 1];

		if (in + 2 + elen > len)
			break;
		if (!(ies[in] == 221 && elen >= sizeof(prefix) &&
		      !memcmp(ies + in + 2, prefix, sizeof(prefix)))) {
			if (out != in)
				memmove(ies + out, ies + in, 2 + elen);
			out += 2 + elen;
		}
		in += 2 + elen;
	}
	return out;
}

void sprd_report_softap(struct sprd_vif *vif, u8 is_connect, u8 *addr,
			u8 *req_ie, u16 req_ie_len)
{
	struct station_info sinfo = { 0 };

	/* P2P device is NULL net device,and should return if
	 * vif->ndev is NULL.
	 */

	if (!addr || !vif->ndev)
		return;

	if (req_ie && req_ie_len > 0) {
		u16 n = mu300_strip_sae_pmk_record(req_ie, req_ie_len);

		if (n != req_ie_len)
			pr_info_ratelimited("sprd_wlan: %pM: a firmware PMK record or a malformed tail dropped from the association elements (SAE offload is off)\n",
					    addr);
		req_ie_len = n;
	}

	if (req_ie_len > 0) {
		sinfo.assoc_req_ies = req_ie;
		sinfo.assoc_req_ies_len = req_ie_len;
	}

	if (is_connect) {
		if (!netif_carrier_ok(vif->ndev)) {
			netif_carrier_on(vif->ndev);
			netif_wake_queue(vif->ndev);
		}
		cfg80211_new_sta(MU300_STA_DEV(vif), addr, &sinfo, GFP_KERNEL);
		netdev_info(vif->ndev, "New station (%pM) connected\n", addr);
	} else {
		cfg80211_del_sta(MU300_STA_DEV(vif), addr, GFP_KERNEL);
		netdev_info(vif->ndev, "The station (%pM) disconnected\n",
			    addr);
	}
}
EXPORT_SYMBOL(sprd_report_softap);

void sprd_report_connection(struct sprd_vif *vif,
			    struct sprd_connect_info *conn_info, u8 status_code)
{
	struct sprd_priv *priv = vif->priv;
	struct wiphy *wiphy = priv->wiphy;
	struct ieee80211_channel *channel;
	struct ieee80211_mgmt *mgmt;
	struct cfg80211_bss *bss = NULL;
	struct timespec64 ts;
	const u8 *ssid_ie;
	u16 band, capability, beacon_interval;
	u32 freq;
	u64 tsf;
	u8 *ie;
	size_t ielen;
	int index = 0;
	int hidden_ssid = 0;
	struct cfg80211_roam_info roam_info;

	if (vif->sm_state != SPRD_CONNECTING &&
	    vif->sm_state != SPRD_CONNECTED) {
		netdev_err(vif->ndev, "%s Unexpected event!\n", __func__);
		return;
	}
	if (status_code != SPRD_CONNECT_SUCCESS &&
	    status_code != SPRD_ROAM_SUCCESS)
		goto err;
	if (!conn_info->bssid) {
		netdev_err(vif->ndev, "%s NULL BSSID!\n", __func__);
		goto err;
	}
	if (!conn_info->req_ie_len) {
		netdev_err(vif->ndev, "%s No associate REQ IE!\n", __func__);
		goto err;
	}
	if (!conn_info->resp_ie_len) {
		netdev_err(vif->ndev, "%s No associate RESP IE!\n", __func__);
		goto err;
	}

	if (conn_info->bea_ie_len) {
		band = sprd_channel_to_band(conn_info->chan);
		freq = ieee80211_channel_to_frequency(conn_info->chan, band);
		channel = ieee80211_get_channel(wiphy, freq);
		if (!channel) {
			netdev_err(vif->ndev, "%s invalid channel %d\n",
				   __func__, conn_info->chan);
			goto err;
		}

		mgmt = (struct ieee80211_mgmt *)conn_info->bea_ie;
		netdev_info(vif->ndev, "%s update BSS %pM\n", __func__,
			    vif->bssid);
		if (!mgmt) {
			netdev_err(vif->ndev, "%s NULL frame!\n", __func__);
			goto err;
		}
		if (!ether_addr_equal(conn_info->bssid, mgmt->bssid))
			netdev_warn(vif->ndev,
				    "%s BSSID mismatch! %pM, :%pM\n",
				    __func__, conn_info->bssid, mgmt->bssid);
		ie = mgmt->u.probe_resp.variable;
		if (IS_ERR_OR_NULL(ie)) {
			netdev_err(vif->ndev, "%s Invalid IE in beacon!\n",
				   __func__);
			goto err;
		}
		ielen = conn_info->bea_ie_len - offsetof(struct ieee80211_mgmt,
							 u.probe_resp.variable);
		if (ielen > SPRD_SCAN_RESULT_MAX_IE_LEN) {
			netdev_err(vif->ndev, "%s Invalid IE length!\n",
				   __func__);
			goto err;
		}

		ssid_ie = cfg80211_find_ie(WLAN_EID_SSID, ie, ielen);
		if (ssid_ie) {
			/* for hidden ssid, refer to bug 1370976,
			 * cp should report prob resp, but sometimes
			 * cp report beacon with ssid value in zero
			 * so add these code to cover this issue
			 */
			if (ssid_ie[1] != 0) {
				for (; index < ssid_ie[1]; index++)
					hidden_ssid |= ssid_ie[2 + index];

				if (!hidden_ssid) {
					netdev_err(vif->ndev,
						   "no need update bss for hidden ssid\n");
					goto done;
				}
			}
		}
		/* framework use system bootup time */
		ktime_get_boottime_ts64(&ts);
		tsf = (u64)ts.tv_sec * 1000000 + div_u64(ts.tv_nsec, 1000);
		beacon_interval = le16_to_cpu(mgmt->u.probe_resp.beacon_int);
		capability = le16_to_cpu(mgmt->u.probe_resp.capab_info);
		netdev_dbg(vif->ndev, "%s, %pM, signal: %d\n",
			   ieee80211_is_probe_resp(mgmt->frame_control)
			   ? "proberesp" : "beacon", mgmt->bssid,
			   conn_info->signal);

		bss = cfg80211_inform_bss(wiphy, channel,
					  CFG80211_BSS_FTYPE_UNKNOWN,
					  mgmt->bssid, tsf,
					  capability, beacon_interval,
					  ie, ielen, conn_info->signal,
					  GFP_KERNEL);
		if (unlikely(!bss))
			netdev_err(vif->ndev,
				   "%s failed to inform bss frame!\n",
				   __func__);
	} else {
		netdev_warn(vif->ndev, "%s No Beason IE!\n", __func__);
	}
done:
	if (vif->sm_state == SPRD_CONNECTING &&
	    status_code == SPRD_CONNECT_SUCCESS)
		cfg80211_connect_result(vif->ndev,
					conn_info->bssid,
					conn_info->req_ie,
					conn_info->req_ie_len,
					conn_info->resp_ie,
					conn_info->resp_ie_len,
					WLAN_STATUS_SUCCESS, GFP_KERNEL);
	else if (vif->sm_state == SPRD_CONNECTED &&
		 status_code == SPRD_ROAM_SUCCESS) {
		memset(&roam_info, 0, sizeof(roam_info));
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		roam_info.links[0].bss = bss;
#else
		roam_info.bss = bss;
#endif
		roam_info.req_ie = conn_info->req_ie;
		roam_info.req_ie_len = conn_info->req_ie_len;
		roam_info.resp_ie = conn_info->resp_ie;
		roam_info.resp_ie_len = conn_info->resp_ie_len;
		cfg80211_roamed(vif->ndev, &roam_info, GFP_KERNEL);
	} else {
		netdev_err(vif->ndev, "%s sm_state (%d), status code (%d)!\n",
			   __func__, vif->sm_state, status_code);
		goto err;
	}

	if (!(sprd_chip_sync_wmm_param(vif->priv, conn_info)))
		pr_err("%s: failed to synchronize wmm parameter", __func__);

	if (!netif_carrier_ok(vif->ndev)) {
		netif_carrier_on(vif->ndev);
		netif_wake_queue(vif->ndev);
	}

	vif->sm_state = SPRD_CONNECTED;
	memcpy(vif->bssid, conn_info->bssid, sizeof(vif->bssid));
    //modified by HQ:zhuziwen sprd_patch_2247860 for wifi name security
	netdev_info(vif->ndev, "%s %s to (%pM)\n", __func__,
		    status_code == SPRD_CONNECT_SUCCESS ? "connect" : "roam",
		    vif->bssid);
    //modified by HQ:zhuziwen end

	sprd_evt_adaptive(vif);
	return;
err:
	if (status_code == WLAN_STATUS_SUCCESS)
		status_code = WLAN_STATUS_UNSPECIFIED_FAILURE;
	if (vif->sm_state == SPRD_CONNECTING)
		cfg80211_connect_result(vif->ndev, vif->bssid, NULL, 0, NULL, 0,
					status_code, GFP_KERNEL);
	else if (vif->sm_state == SPRD_CONNECTED)
		cfg80211_disconnected(vif->ndev, status_code, NULL, 0,
				      true, GFP_KERNEL);
    //modified by HQ:zhuziwen sprd_patch_2247860 for wifi name security
	netdev_err(vif->ndev, "%s %pM failed (%d)!\n", __func__, vif->bssid,
		   status_code);
    //modified by HQ:zhuziwen end
	memset(vif->bssid, 0, sizeof(vif->bssid));
	memset(vif->ssid, 0, sizeof(vif->ssid));
	vif->sm_state = SPRD_DISCONNECTED;
}
EXPORT_SYMBOL(sprd_report_connection);

void sprd_report_disconnection(struct sprd_vif *vif, u16 reason_code)
{
	if (vif->sm_state == SPRD_CONNECTING) {
		cfg80211_connect_result(vif->ndev, vif->bssid, NULL, 0, NULL, 0,
					WLAN_STATUS_UNSPECIFIED_FAILURE,
					GFP_KERNEL);
	} else if (vif->sm_state == SPRD_CONNECTED) {
		cfg80211_disconnected(vif->ndev, reason_code, NULL, 0,
				      false, GFP_KERNEL);
        //modified by HQ:zhuziwen sprd_patch_2247860 for wifi name security
		netdev_info(vif->ndev,
			    "%s %pM, passive disconnection, reason_code %d\n", __func__,
			    vif->bssid, reason_code);
	} else if (vif->sm_state == SPRD_DISCONNECTING) {
		cfg80211_disconnected(vif->ndev, reason_code, NULL, 0,
				      true, GFP_KERNEL);
		netdev_info(vif->ndev,
			    "%s %pM, active disconnection, reason_code %d\n", __func__,
			    vif->bssid, reason_code);
        //modified by HQ:zhuziwen end
	} else {
		netdev_err(vif->ndev, "%s Unexpected event!\n", __func__);
		return;
	}

	sprd_defrag_recover(vif->priv, vif);
	sprd_fcc_reset_bo(vif->priv);
	vif->sm_state = SPRD_DISCONNECTED;
	memset(vif->bssid, 0, sizeof(vif->bssid));
	memset(vif->ssid, 0, sizeof(vif->ssid));

	if (netif_carrier_ok(vif->ndev)) {
		netif_carrier_off(vif->ndev);
		netif_stop_queue(vif->ndev);
	}
#ifdef CONFIG_SPRD_WLAN_VENDOR_SPECIFIC
	/* clear link layer status data */
	memset(&vif->priv->pre_radio, 0, sizeof(vif->priv->pre_radio));
#endif
}
EXPORT_SYMBOL(sprd_report_disconnection);

void sprd_report_mic_failure(struct sprd_vif *vif, u8 is_mcast, u8 key_id)
{
	netdev_info(vif->ndev,
		    "%s is_mcast:0x%x key_id: 0x%x bssid: %pM\n",
		    __func__, is_mcast, key_id, vif->bssid);

	cfg80211_michael_mic_failure(vif->ndev, vif->bssid,
				     (is_mcast ? NL80211_KEYTYPE_GROUP :
				      NL80211_KEYTYPE_PAIRWISE),
				     key_id, NULL, GFP_KERNEL);
}
EXPORT_SYMBOL(sprd_report_mic_failure);

void sprd_report_remain_on_channel_expired(struct sprd_vif *vif)
{
	netdev_info(vif->ndev, "%s\n", __func__);

	cfg80211_remain_on_channel_expired(&vif->wdev, vif->listen_cookie,
					   &vif->listen_channel, GFP_KERNEL);
}
EXPORT_SYMBOL(sprd_report_remain_on_channel_expired);

void sprd_report_mgmt_tx_status(struct sprd_vif *vif, u64 cookie,
				const u8 *buf, u32 len, u8 ack)
{
	netdev_info(vif->ndev, "%s cookie %lld\n", __func__, cookie);

	cfg80211_mgmt_tx_status(&vif->wdev, cookie, buf, len, ack, GFP_KERNEL);
}
EXPORT_SYMBOL(sprd_report_mgmt_tx_status);

void sprd_report_mgmt(struct sprd_vif *vif, u8 chan, const u8 *buf, size_t len)
{
	u16 band;
	int freq;
	bool ret;

	band = sprd_channel_to_band(chan);
	freq = ieee80211_channel_to_frequency(chan, band);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	ret = cfg80211_rx_mgmt_khz(&vif->wdev, MHZ_TO_KHZ(freq), 0, buf, len, GFP_ATOMIC);
#else
	ret = cfg80211_rx_mgmt(&vif->wdev, freq, 0, buf, len, GFP_ATOMIC);
#endif
	if (!ret)
		netdev_err(vif->ndev, "%s unregistered frame!", __func__);
}
EXPORT_SYMBOL(sprd_report_mgmt);

void sprd_report_mgmt_deauth(struct sprd_vif *vif, const u8 *buf, size_t len)
{
	struct sprd_work *misc_work;

	misc_work = sprd_alloc_work(len);
	if (!misc_work) {
		netdev_err(vif->ndev, "%s out of memory", __func__);
		return;
	}

	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_DEAUTH;
	memcpy(misc_work->data, buf, len);

	sprd_queue_work(vif->priv, misc_work);
}
EXPORT_SYMBOL(sprd_report_mgmt_deauth);

void sprd_report_mgmt_disassoc(struct sprd_vif *vif, const u8 *buf, size_t len)
{
	struct sprd_work *misc_work;

	misc_work = sprd_alloc_work(len);
	if (!misc_work) {
		netdev_err(vif->ndev, "%s out of memory", __func__);
		return;
	}

	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_DISASSOC;
	memcpy(misc_work->data, buf, len);

	sprd_queue_work(vif->priv, misc_work);
}
EXPORT_SYMBOL(sprd_report_mgmt_disassoc);

void sprd_report_cqm(struct sprd_vif *vif, u8 rssi_event)
{
	netdev_info(vif->ndev, "%s rssi_event: %d\n", __func__, rssi_event);

	cfg80211_cqm_rssi_notify(vif->ndev, rssi_event, 0, GFP_KERNEL);
}
EXPORT_SYMBOL(sprd_report_cqm);

void sprd_report_tdls(struct sprd_vif *vif, const u8 *peer, u8 oper,
		      u16 reason_code)
{
	netdev_info(vif->ndev, "%s A station (%pM)found\n", __func__, peer);

	cfg80211_tdls_oper_request(vif->ndev, peer, (enum nl80211_tdls_operation)oper,
				   reason_code, GFP_KERNEL);
}
EXPORT_SYMBOL(sprd_report_tdls);
