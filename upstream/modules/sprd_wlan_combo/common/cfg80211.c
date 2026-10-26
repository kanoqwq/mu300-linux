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

#include "acs.h"
#include "cfg80211.h"
#include "chip_ops.h"
#include "cmd.h"
#include "common.h"
#include "delay_work.h"
#include "hif.h"
#include "iface.h"
#include "report.h"
#include "tdls.h"
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
#include <linux/bitops.h>
#endif

static char type_name[16][32] = {
	"ASSO REQ",
	"ASSO RESP",
	"REASSO REQ",
	"REASSO RESP",
	"PROBE REQ",
	"PROBE RESP",
	"TIMING ADV",
	"RESERVED",
	"BEACON",
	"ATIM",
	"DISASSO",
	"AUTH",
	"DEAUTH",
	"ACTION",
	"ACTION NO ACK",
	"RESERVED"
};

static char pub_action_name[][32] = {
	"GO Negotiation Req",
	"GO Negotiation Resp",
	"GO Negotiation Conf",
	"P2P Invitation Req",
	"P2P Invitation Resp",
	"Device Discovery Req",
	"Device Discovery Resp",
	"Provision Discovery Req",
	"Provision Discovery Resp",
	"Reserved"
};

static char p2p_action_name[][32] = {
	"Notice of Absence",
	"P2P Precence Req",
	"P2P Precence Resp",
	"GO Discoverability Req",
	"Reserved"
};

void sprd_dump_frame_prot_info(int send, int freq, const unsigned char *buf,
			       int len)
{
	int idx = 0, type, subtype;
	int action, action_subtype;
	char print_buf[PRINT_BUF_LEN] = { 0 };
	char *p = print_buf;

	if (len < 16)
		return;
	type = ((*buf) & IEEE80211_FCTL_FTYPE) >> 2;
	subtype = ((*buf) & IEEE80211_FCTL_STYPE) >> 4;

	idx += snprintf(p + idx, PRINT_BUF_LEN - idx, "[cfg80211] ");

	if (send)
		idx += snprintf(p + idx, PRINT_BUF_LEN - idx, "SEND: ");
	else
		idx += snprintf(p + idx, PRINT_BUF_LEN - idx, "RECV: ");

	if (type == IEEE80211_FTYPE_MGMT) {
		idx += snprintf(p + idx, PRINT_BUF_LEN - idx, "%dMHz, %s, ",
			       freq, type_name[subtype]);
	} else {
		idx += snprintf(p + idx, PRINT_BUF_LEN - idx,
			       "%dMHz, not mgmt frame, type=%d, ", freq, type);
	}

	if (subtype == ACTION_TYPE) {
		if (len < MAC_LEN)
			goto out;
		action = *(buf + MAC_LEN);
		if ((action == PUB_ACTION) && (len >= ACTION_SUBTYPE_OFFSET)) {
			action_subtype = *(buf + ACTION_SUBTYPE_OFFSET);
			if (action_subtype < ARRAY_SIZE(pub_action_name))
				idx += snprintf(p + idx, PRINT_BUF_LEN - idx, "PUB:%s ",
				       pub_action_name[action_subtype]);
		} else if ((action == P2P_ACTION) && (len >= ACTION_SUBTYPE_OFFSET)) {
			action_subtype = *(buf + ACTION_SUBTYPE_OFFSET);
			if (action_subtype < ARRAY_SIZE(p2p_action_name))
				idx += snprintf(p + idx, PRINT_BUF_LEN - idx, "P2P:%s ",
				       p2p_action_name[action_subtype]);
		} else {
			idx += snprintf(p + idx, PRINT_BUF_LEN - idx, "Unknown ACTION(0x%x)", action);
		}
	}

out:
	if (idx < PRINT_BUF_LEN)
		p[idx] = '\0';

	pr_debug("%s %pM %pM\n", p, &buf[4], &buf[10]);
}
EXPORT_SYMBOL(sprd_dump_frame_prot_info);

static void cfg80211_do_work(struct work_struct *work)
{
	struct sprd_work *sprd_work = NULL;
	struct sprd_reg_mgmt *reg_mgmt;
	struct sprd_vif *vif;
	struct sprd_priv *priv = container_of(work, struct sprd_priv, work);

	while (1) {
		spin_lock_bh(&priv->work_lock);
		if (list_empty(&priv->work_list)) {
			spin_unlock_bh(&priv->work_lock);
			return;
		}

		sprd_work = list_first_entry(&priv->work_list,
					     struct sprd_work, list);
		if (!sprd_work) {
			pr_err("%s get sprd work error!\n", __func__);
			spin_unlock_bh(&priv->work_lock);
			return;
		}
		list_del(&sprd_work->list);
		spin_unlock_bh(&priv->work_lock);

		vif = sprd_work->vif;
		if (vif)
			netdev_dbg(vif->ndev, "process delayed work: %d\n", sprd_work->id);
		else
			pr_debug("process delayed work: %d\n", sprd_work->id);

		switch (sprd_work->id) {
		case SPRD_WORK_REG_MGMT:
			reg_mgmt = (struct sprd_reg_mgmt *)sprd_work->data;
			sprd_register_frame(priv, vif,
					    reg_mgmt->type,
					    reg_mgmt->reg ? 1 : 0);
			break;
		case SPRD_WORK_DEAUTH:
		case SPRD_WORK_DISASSOC:
			cfg80211_rx_unprot_mlme_mgmt(vif->ndev,
						     sprd_work->data,
						     sprd_work->len);
			break;
		case SPRD_WORK_MC_FILTER:
			if (vif->mc_filter->mc_change)
				sprd_set_mc_filter(priv, vif,
						   vif->mc_filter->subtype,
						   vif->mc_filter->mac_num,
						   vif->mc_filter->mac_addr);
			break;
		case SPRD_WORK_NOTIFY_IP:
			sprd_notify_ip(priv, vif, SPRD_IPV6, sprd_work->data);
			break;
		default:
			if (sprd_do_delay_work(priv, sprd_work) == false) {
				netdev_dbg(vif->ndev,
					   "Unknown delayed work: %d\n",
					   sprd_work->id);
			}
			break;
		}

		kfree(sprd_work);
		/*
		 * MU300: this loop drains every queued item in one run, and items requeue themselves (the RX
		 * refill did, back to back, in issue #94). Yield between them so a long run cannot hold the CPU.
		 */
		cond_resched();
	}
}

static int cfg80211_init_work(struct sprd_priv *priv)
{
	spin_lock_init(&priv->work_lock);
	INIT_LIST_HEAD(&priv->work_list);
	INIT_WORK(&priv->work, cfg80211_do_work);

	priv->common_workq = alloc_ordered_workqueue("sprd_work",
						     WQ_HIGHPRI |
						     WQ_CPU_INTENSIVE |
						     WQ_MEM_RECLAIM);
	if (!priv->common_workq) {
		pr_err("%s sprd_work create failed\n", __func__);
		return -ENOMEM;
	}
	return 0;
}

static void cfg80211_deinit_work(struct sprd_priv *priv)
{
	sprd_clean_work(priv);

	destroy_workqueue(priv->common_workq);
}

#ifdef DRV_RESET_SELF
static int cfg80211_host_reset_self(struct sprd_priv *priv)
{
	struct sprd_hif *hif = &priv->hif;

	if (hif->ops->reset_self)
		return hif->ops->reset_self(priv);
	return 0;
}

/*create self_reset work*/
static void cfg80211_do_reset_work(struct work_struct *work)
{
	int ret;
	struct sprd_priv *priv = container_of(work, struct sprd_priv, reset_work);

	ret = cfg80211_host_reset_self(priv);
	if (!ret)
		pr_err("%s host reset self success!\n", __func__);
}

static int cfg80211_init_reset_work(struct sprd_priv *priv)
{
	INIT_WORK(&priv->reset_work, cfg80211_do_reset_work);

	priv->reset_workq = alloc_ordered_workqueue("sprd_reset_work",
						     WQ_HIGHPRI |
						     WQ_CPU_INTENSIVE |
						     WQ_MEM_RECLAIM);
	if (!priv->reset_workq) {
		pr_err("%s sprd_reset_work create failed\n", __func__);
		return -ENOMEM;
	}
	return 0;
}

static void cfg80211_deinit_reset_work(struct sprd_priv *priv)
{
	cancel_work_sync(&priv->reset_work);
	flush_workqueue(priv->reset_workq);
	destroy_workqueue(priv->common_workq);
}

void sprd_cancel_reset_work(struct sprd_priv *priv)
{
	flush_work(&priv->reset_work);
}
#endif

static enum sprd_mode cfg80211_type_to_mode(enum nl80211_iftype type, char *name)
{
	enum sprd_mode mode;

	switch (type) {
	case NL80211_IFTYPE_STATION:
		if (strncmp(name, "wlan1", 5) == 0)
			mode = SPRD_MODE_STATION_SECOND;
		else
			mode = SPRD_MODE_STATION;
		break;
	case NL80211_IFTYPE_AP:
		mode = SPRD_MODE_AP;
		break;
	case NL80211_IFTYPE_P2P_GO:
		mode = SPRD_MODE_P2P_GO;
		break;
	case NL80211_IFTYPE_P2P_CLIENT:
		mode = SPRD_MODE_P2P_CLIENT;
		break;
	case NL80211_IFTYPE_P2P_DEVICE:
		mode = SPRD_MODE_P2P_DEVICE;
		break;
	case NL80211_IFTYPE_MONITOR:
		mode = SPRD_MODE_MONITOR;
		break;
	default:
		mode = SPRD_MODE_NONE;
		break;
	}

	return mode;
}

static int cfg80211_add_cipher_key(struct sprd_vif *vif, bool pairwise,
				   u8 key_index, u32 cipher, const u8 *key_seq,
				   const u8 *mac_addr)
{
	u8 cipher_ptr;
	int ret = 0;

	netdev_info(vif->ndev, "%s %s key_index %d\n", __func__,
		    pairwise ? "pairwise" : "group", key_index);

	if (vif->key_len[pairwise][0] || vif->key_len[pairwise][1] ||
	    vif->key_len[pairwise][2] || vif->key_len[pairwise][3] ||
	    vif->key_len[pairwise][4] || vif->key_len[pairwise][5]) {
		vif->prwise_crypto = sprd_parse_cipher(cipher);
		cipher_ptr = vif->prwise_crypto;

		ret = sprd_add_key(vif->priv, vif,
				   vif->key[pairwise][key_index],
				   vif->key_len[pairwise][key_index],
				   pairwise, key_index, key_seq,
				   cipher_ptr, mac_addr);
	}

	return ret;
}

static int cfg80211_set_beacon_ies(struct sprd_vif *vif,
				   struct cfg80211_beacon_data *beacon)
{
	int ret = 0;

	if (!beacon)
		return -EINVAL;

	if (beacon->beacon_ies_len && beacon->beacon_ies_len <= 0xFFFF) {
		netdev_dbg(vif->ndev, "set beacon IE\n");
		ret = sprd_set_beacon_ie(vif->priv, vif, beacon->beacon_ies,
					 beacon->beacon_ies_len);
	}

	if (beacon->proberesp_ies_len && beacon->proberesp_ies_len <= 0xFFFF) {
		netdev_dbg(vif->ndev, "set probe response IE\n");
		ret = sprd_set_proberesp_ie(vif->priv, vif,
					    beacon->proberesp_ies,
					    beacon->proberesp_ies_len);
	}

	if (beacon->assocresp_ies_len && beacon->assocresp_ies_len <= 0xFFFF) {
		netdev_dbg(vif->ndev, "set associate response IE\n");
		ret = sprd_set_assocresp_ie(vif->priv, vif,
					    beacon->assocresp_ies,
					    beacon->assocresp_ies_len);
	}

	if (ret)
		netdev_err(vif->ndev, "%s failed\n", __func__);

	return ret;
}

struct wireless_dev *sprd_cfg80211_add_iface(struct wiphy *wiphy,
					     const char *name,
					     unsigned char name_assign_type,
					     enum nl80211_iftype type,
					     struct vif_params *params)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);

	return sprd_add_iface(priv, name, type, params->macaddr);
}

int sprd_cfg80211_del_iface(struct wiphy *wiphy, struct wireless_dev *wdev)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	struct sprd_vif *vif = NULL, *tmp_vif = NULL;
	struct sprd_hif *hif;

	if (!priv) {
		pr_err("can not get priv!\n");
		return -ENODEV;
	}
	hif = &priv->hif;

	if (hif->remove_flag == 1) {
		wiphy_err(wiphy, "%s driver removing!\n", __func__);
		return 0;
	}
	if (sprd_chip_is_exit(&priv->chip) || hif->cp_asserted)
		pr_info("del interface while assert\n");

	spin_lock_bh(&priv->list_lock);
	list_for_each_entry_safe(vif, tmp_vif, &priv->vif_list, vif_node) {
		if (&vif->wdev == wdev) {
			list_del(&vif->vif_node);
			spin_unlock_bh(&priv->list_lock);
			return sprd_del_iface(priv, vif);
		}
	}
	spin_unlock_bh(&priv->list_lock);

	return 0;
}

int sprd_cfg80211_change_iface(struct wiphy *wiphy, struct net_device *ndev,
			       enum nl80211_iftype type,
			       struct vif_params *params)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_hif *hif = NULL;
	enum nl80211_iftype old_type = NL80211_IFTYPE_UNSPECIFIED;
	int ret;

	if (!vif || !vif->priv) {
		netdev_err(ndev, "%s can not get vif or priv!\n", __func__);
		return -ENODEV;
	}

	old_type = vif->wdev.iftype;
	hif = &vif->priv->hif;
	if (!hif) {
		netdev_err(ndev, "%s can not get hif!\n", __func__);
		return -ENODEV;
	}

	netdev_info(ndev, "%s type %d -> %d\n", __func__, old_type, type);

	/*
	 * hif->power_cnt = 1 means there is only one mode and all
	 * of the mode are set to NONE after close command. but it
	 * should not send any command between close and open,
	 * change_iface_block_cmd need set to 1 to block other cmd.
	 * more info please ref:1576772
	 */
	if (atomic_read(&hif->power_cnt) == 1) {
		netdev_info(ndev, "hif->power_cnt is 1, need block command!\n");
		atomic_set(&hif->change_iface_block_cmd, 1);
	}

	ret = sprd_uninit_fw(vif);
	if (ret && type == NL80211_IFTYPE_AP)
		vif->wdev.iftype = type;
	if (!ret) {
		vif->wdev.iftype = type;
		ret = sprd_init_fw(vif);
		if (ret)
			vif->wdev.iftype = old_type;
	}

	if (atomic_read(&hif->change_iface_block_cmd) == 1) {
		netdev_info(ndev, "block command finished, reset change_iface_block_cmd!\n");
		atomic_set(&hif->change_iface_block_cmd, 0);
	}

	if (vif->wdev.iftype == NL80211_IFTYPE_AP ||
	    vif->wdev.iftype == NL80211_IFTYPE_P2P_GO)
		ndev->priv_flags &= ~IFF_DONT_BRIDGE;

	return ret;
}
EXPORT_SYMBOL(sprd_cfg80211_change_iface);

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sprd_cfg80211_add_key(struct wiphy *wiphy, struct net_device *ndev,
			  int link_id, u8 key_index, bool pairwise, const u8 *mac_addr,
			  struct key_params *params)
#else
int sprd_cfg80211_add_key(struct wiphy *wiphy, struct net_device *ndev,
			  u8 key_index, bool pairwise, const u8 *mac_addr,
			  struct key_params *params)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s key_index=%d, pairwise=%d, key_len=%d\n",
		    __func__, key_index, pairwise, params->key_len);

	vif->key_index[pairwise] = key_index;
	vif->key_len[pairwise][key_index] = params->key_len;
	memcpy(vif->key[pairwise][key_index], params->key, params->key_len);

	/* PMK is for Roaming offload */
	if (params->cipher == WLAN_CIPHER_SUITE_PMK)
		return sprd_set_roam_offload(vif->priv, vif,
					     SPRD_ROAM_OFFLOAD_SET_PMK,
					     params->key, params->key_len);
	else
		return cfg80211_add_cipher_key(vif, pairwise, key_index,
					       params->cipher, params->seq,
					       mac_addr);
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sprd_cfg80211_del_key(struct wiphy *wiphy, struct net_device *ndev,
			 int link_id, u8 key_index, bool pairwise, const u8 *mac_addr)
#else
int sprd_cfg80211_del_key(struct wiphy *wiphy, struct net_device *ndev,
			  u8 key_index, bool pairwise, const u8 *mac_addr)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s key_index=%d, pairwise=%d\n",
		    __func__, key_index, pairwise);

	if (key_index > SPRD_MAX_KEY_INDEX) {
		netdev_err(ndev, "%s key index %d out of bounds!\n", __func__,
			   key_index);
		return -ENOENT;
	}

	if (!vif->key_len[pairwise][key_index]) {
		netdev_err(ndev, "%s key index %d is empty!\n", __func__,
			   key_index);
		return 0;
	}

	vif->key_len[pairwise][key_index] = 0;
	vif->prwise_crypto = SPRD_CIPHER_NONE;
	vif->grp_crypto = SPRD_CIPHER_NONE;

	return sprd_del_key(vif->priv, vif, key_index, pairwise, mac_addr);
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sprd_cfg80211_set_default_key(struct wiphy *wiphy, struct net_device *ndev,
				 int link_id, u8 key_index, bool unicast, bool multicast)
#else
int sprd_cfg80211_set_default_key(struct wiphy *wiphy, struct net_device *ndev,
				  u8 key_index, bool unicast, bool multicast)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);

	if (key_index > SPRD_MAX_KEY_INDEX) {
		netdev_err(ndev, "%s invalid key index: %d\n", __func__,
			   key_index);
		return -EINVAL;
	}

	return sprd_set_def_key(vif->priv, vif, key_index);
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sprd_cfg80211_set_default_mgmt_key(struct wiphy *wiphy,
				       struct net_device *netdev, int link_id, u8 key_index)
#else
int sprd_cfg80211_set_default_mgmt_key(struct wiphy *wiphy,
				       struct net_device *netdev, u8 key_index)
#endif
{
	return 0;
}

int sprd_cfg80211_start_ap(struct wiphy *wiphy, struct net_device *ndev,
			   struct cfg80211_ap_settings *settings)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct cfg80211_beacon_data *beacon = &settings->beacon;
	struct ieee80211_mgmt *mgmt;
	u16 mgmt_len, index = 0, hidden_index;
	int hidden_len = SPRD_AP_HIDDEN_FLAG_LEN;
	u8 *data = NULL;
	u8 ds_channel = 0;
	int ret;

	if (!settings->ssid) {
		netdev_err(ndev, "%s invalid SSID!\n", __func__);
		return -EINVAL;
	}

	strncpy(vif->ssid, settings->ssid, settings->ssid_len);
	vif->ssid_len = settings->ssid_len;
	cfg80211_set_beacon_ies(vif, beacon);

	if (!beacon->head)
		return -EINVAL;
	mgmt_len = beacon->head_len;

	if (settings->hidden_ssid)
		hidden_len += settings->ssid_len;
	mgmt_len += hidden_len;

	if (beacon->tail)
		mgmt_len += beacon->tail_len;

	/*
	 * The firmware takes the AP channel from the DS Parameter Set element.
	 * Unisoc's Android hostapd always adds it; upstream hostapd omits it on
	 * 5 GHz, and START_AP then fails with NOT_SUPPORT (or no beacons are sent).
	 */
	if (settings->chandef.chan &&
	    beacon->head_len > SPRD_AP_SSID_LEN_OFFSET &&
	    !cfg80211_find_ie(WLAN_EID_DS_PARAMS,
			      beacon->head + SPRD_AP_SSID_LEN_OFFSET - 1,
			      beacon->head_len - SPRD_AP_SSID_LEN_OFFSET + 1) &&
	    !(beacon->tail &&
	      cfg80211_find_ie(WLAN_EID_DS_PARAMS, beacon->tail, beacon->tail_len))) {
		ds_channel = ieee80211_frequency_to_channel(
				settings->chandef.chan->center_freq);
		mgmt_len += 3;
	}

	mgmt = kmalloc(mgmt_len, GFP_KERNEL);
	if (!mgmt)
		return -ENOMEM;
	data = (u8 *)mgmt;

	memcpy(data, beacon->head, SPRD_AP_SSID_LEN_OFFSET);
	index += SPRD_AP_SSID_LEN_OFFSET;
	/* modify ssid_len */
	*(data + index) = (u8)(settings->ssid_len + 1);
	index += 1;
	/* copy ssid */
	strncpy(data + index, settings->ssid, settings->ssid_len);
	index += settings->ssid_len;
	/* set hidden ssid flag */
	*(data + index) = (u8)settings->hidden_ssid;
	index += 1;

	/* cope left settings */
	if (settings->hidden_ssid)
		hidden_index = index - settings->ssid_len;
	else
		hidden_index = index;

	memcpy(data + index, beacon->head + hidden_index - 1,
	       beacon->head_len + 1 - hidden_index);

	index = beacon->head_len + hidden_len;
	if (ds_channel) {
		data[index++] = WLAN_EID_DS_PARAMS;
		data[index++] = 1;
		data[index++] = ds_channel;
	}
	if (beacon->tail)
		memcpy(data + index, beacon->tail, beacon->tail_len);

	ret = sprd_start_ap(vif->priv, vif, (unsigned char *)mgmt, mgmt_len, settings);
	if (ret)
		netdev_err(ndev, "%s failed to start AP!\n", __func__);

	netif_carrier_on(ndev);

	kfree(mgmt);
	return ret;
}

int sprd_cfg80211_change_beacon(struct wiphy *wiphy, struct net_device *ndev,
				struct cfg80211_ap_update *info)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_dbg(ndev, "%s\n", __func__);

	return cfg80211_set_beacon_ies(vif, &info->beacon);
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sprd_cfg80211_stop_ap(struct wiphy *wiphy, struct net_device *ndev, unsigned int link_id)
#else
int sprd_cfg80211_stop_ap(struct wiphy *wiphy, struct net_device *ndev)
#endif
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	netdev_info(ndev, "%s\n", __func__);
	sprd_fcc_reset_bo(priv);

	return 0;
}

int sprd_cfg80211_add_station(struct wiphy *wiphy, struct net_device *ndev,
			      const u8 *mac, struct station_parameters *params)
{
	return 0;
}

int sprd_p2p_go_del_station(struct sprd_priv *priv, struct sprd_vif *vif,
				  const u8 *mac_addr, u16 reason_code)
{
	struct sprd_work *misc_work;

	misc_work = sprd_alloc_work(ETH_ALEN + sizeof(u16));
	if (!misc_work) {
		netdev_err(vif->ndev, "%s out of memory\n", __func__);
		return -1;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_P2P_GO_DEL_STATION;

	memcpy(misc_work->data, mac_addr, ETH_ALEN);
	memcpy(misc_work->data + ETH_ALEN, &reason_code, sizeof(u16));

	sprd_queue_work(vif->priv, misc_work);
	return 0;
}

int sprd_cfg80211_del_station(struct wiphy *wiphy, struct net_device *ndev,
			      struct station_del_parameters *params)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	if (!params->mac) {
		netdev_dbg(ndev, "ignore NULL MAC address!\n");
		goto out;
	}

	netdev_info(ndev, "%s %pM reason:%d\n", __func__, params->mac,
		    params->reason_code);
	if (vif->mode == SPRD_MODE_P2P_GO)
		sprd_p2p_go_del_station(vif->priv, vif, params->mac, params->reason_code);
	else
		sprd_del_station(vif->priv, vif, params->mac, params->reason_code);

out:
	return 0;
}

int sprd_cfg80211_change_station(struct wiphy *wiphy, struct net_device *ndev,
				 const u8 *mac,
				 struct station_parameters *params)
{
	return 0;
}

/*
 * MU300: whether MAC is a station of this access point: an entry of this interface in the firmware's peer table
 * (as dump_station below lists them), not the interface's own address.
 */
static bool sprd_ap_has_peer(struct sprd_vif *vif, const u8 *mac)
{
	struct sprd_hif *hif = &vif->priv->hif;
	int i;

	if (!is_valid_ether_addr(mac) || ether_addr_equal(mac, vif->ndev->dev_addr))
		return false;
	for (i = 0; i < MAX_LUT_NUM; i++)
		if (READ_ONCE(hif->peer_entry[i].ctx_id) == vif->ctx_id &&
		    ether_addr_equal(hif->peer_entry[i].tx.da, mac))
			return true;
	return false;
}

int sprd_cfg80211_get_station(struct wiphy *wiphy, struct net_device *ndev,
			      const u8 *mac, struct station_info *sinfo)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_sta_info sta;
	struct sprd_rate_info *tx_rate;
	struct sprd_rate_info *rx_rate;
	int ret;

	/*
	 * MU300: what follows is the station side's: the interface's own counters and the firmware's GET_STATION,
	 * which reports the link to the access point this interface is connected to. On an access point it would hand
	 * those out as any client's (and for any address asked). The firmware has no per-client figures: a station
	 * of ours is answered with nothing filled in, as dump_station does, an unknown address with -ENOENT.
	 */
	if (vif->wdev.iftype == NL80211_IFTYPE_AP || vif->wdev.iftype == NL80211_IFTYPE_P2P_GO) {
		if (!sprd_ap_has_peer(vif, mac))
			return -ENOENT;
		sinfo->filled = 0;
		return 0;
	}

	sinfo->filled |= BIT(NL80211_STA_INFO_TX_BYTES) |
	    BIT(NL80211_STA_INFO_TX_PACKETS) |
	    BIT(NL80211_STA_INFO_RX_BYTES) |
	    BIT(NL80211_STA_INFO_RX_PACKETS);
	sinfo->tx_bytes = ndev->stats.tx_bytes;
	sinfo->tx_packets = ndev->stats.tx_packets;
	sinfo->rx_bytes = ndev->stats.rx_bytes;
	sinfo->rx_packets = ndev->stats.rx_packets;

	/* Get current station info */
	ret = sprd_get_station(vif->priv, vif, &sta);
	if (ret)
		goto out;
	tx_rate = &sta.tx_rate;
	rx_rate = &sta.rx_rate;

	sinfo->signal = sta.signal;
	sinfo->filled |= BIT(NL80211_STA_INFO_SIGNAL);

	sinfo->tx_failed = sta.txfailed;
	sinfo->filled |= BIT(NL80211_STA_INFO_TX_BITRATE) |
	    BIT(NL80211_STA_INFO_TX_FAILED);

	sinfo->filled |= BIT(NL80211_STA_INFO_RX_BITRATE);

	/* fill rate info if bit 2,3,4 not set */
	if (!(tx_rate->flags & 0x1c))
		sinfo->txrate.bw = RATE_INFO_BW_20;

	if (tx_rate->flags & BIT(2))
		sinfo->txrate.bw = RATE_INFO_BW_40;

	if (tx_rate->flags & BIT(3))
		sinfo->txrate.bw = RATE_INFO_BW_80;

	if (tx_rate->flags & BIT(4) || tx_rate->flags & BIT(5))
		sinfo->txrate.bw = RATE_INFO_BW_160;

	if (tx_rate->flags & BIT(6))
		sinfo->txrate.flags |= RATE_INFO_FLAGS_SHORT_GI;

	if (tx_rate->flags & RATE_INFO_FLAGS_MCS ||
	    tx_rate->flags & RATE_INFO_FLAGS_VHT_MCS) {
		sinfo->txrate.flags |= (tx_rate->flags & 0x3);
		sinfo->txrate.mcs = tx_rate->mcs;

		if (tx_rate->flags & RATE_INFO_FLAGS_VHT_MCS && tx_rate->nss)
			sinfo->txrate.nss = tx_rate->nss;
	} else {
		sinfo->txrate.legacy = tx_rate->legacy;
	}

	/* rx rate */
	if (!(rx_rate->flags & 0x1c))
		sinfo->rxrate.bw = RATE_INFO_BW_20;

	if (rx_rate->flags & BIT(2))
		sinfo->rxrate.bw = RATE_INFO_BW_40;

	if (rx_rate->flags & BIT(3))
		sinfo->rxrate.bw = RATE_INFO_BW_80;

	if (rx_rate->flags & BIT(4) || rx_rate->flags & BIT(5))
		sinfo->rxrate.bw = RATE_INFO_BW_160;

	if (rx_rate->flags & BIT(6))
		sinfo->rxrate.flags |= RATE_INFO_FLAGS_SHORT_GI;

	if (rx_rate->flags & RATE_INFO_FLAGS_MCS ||
	    rx_rate->flags & RATE_INFO_FLAGS_VHT_MCS) {
		sinfo->rxrate.flags |= (rx_rate->flags & 0x3);
		sinfo->rxrate.mcs = rx_rate->mcs;

		if (rx_rate->flags & RATE_INFO_FLAGS_VHT_MCS && rx_rate->nss)
			sinfo->rxrate.nss = rx_rate->nss;
	} else {
		sinfo->rxrate.legacy = rx_rate->legacy;
	}

	netdev_info(ndev,
		    "%s signal %d noise=%d, txlegacy %d txmcs:%d txflags:0x:%x, rxlegacy %d rxmcs:%d rxflags:0x:%x\n",
		    __func__, sinfo->signal, sta.noise,
		    sinfo->txrate.legacy, tx_rate->mcs, tx_rate->flags,
		    sinfo->rxrate.legacy, rx_rate->mcs, rx_rate->flags);
out:
	return ret;
}

int sprd_cfg80211_set_channel(struct wiphy *wiphy, struct net_device *ndev,
			      struct ieee80211_channel *chan)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	return sprd_set_channel(vif->priv, vif,
				ieee80211_frequency_to_channel
				(chan->center_freq));
}

int sprd_cfg80211_scan(struct wiphy *wiphy,
		       struct cfg80211_scan_request *request)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);

	return sprd_scan(priv, wiphy, request);
}

int sprd_cfg80211_disconnect(struct wiphy *wiphy, struct net_device *ndev,
			     u16 reason_code)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	enum sprd_sm_state old_state = vif->sm_state;
	int ret;
    
    //modified by HQ:zhuziwen sprd_patch_2247860 for wifi name security
	netdev_info(ndev, "%s %pM reason: %d\n", __func__,
		    vif->bssid, reason_code);
    //modified by HQ:zhuziwen end

	vif->sm_state = SPRD_DISCONNECTING;
	ret = sprd_disconnect(vif->priv, vif, reason_code);
	if (ret)
		vif->sm_state = old_state;

	return ret;
}

int sprd_cfg80211_connect(struct wiphy *wiphy, struct net_device *ndev,
			  struct cfg80211_connect_params *sme)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct cmd_connect con = { 0 };
	enum sprd_sm_state old_state = vif->sm_state;
	bool ie_set_flag = false;
	u16 center_freq = 0;
	int is_wep = (sme->crypto.cipher_group == WLAN_CIPHER_SUITE_WEP40) ||
	    (sme->crypto.cipher_group == WLAN_CIPHER_SUITE_WEP104);
	int ret, i;

	/* workround for bug 795430 */
	if (!(vif->state & VIF_STATE_OPEN)) {
		wiphy_err(wiphy,
			  "%s, error! mode%d connect after closed not allowed",
			  __func__, vif->mode);
		ret = -EACCES;
		goto err;
	}

	/* workround for bug 771600 */
	if (vif->sm_state == SPRD_CONNECTING) {
		netdev_info(ndev, "sm_state is CONNECTING, disconnect first\n");
		sprd_cfg80211_disconnect(wiphy, ndev,
					 WLAN_REASON_DEAUTH_LEAVING);
	}

	/* Set WPS ie and SAE ie */
	if (sme->ie_len > SPRD_MIN_IE_LEN) {
		for (i = 0; i < sme->ie_len - SPRD_MIN_IE_LEN; i++) {
			if (sme->ie[i] == 0xDD && sme->ie[i + 2] == 0x40 &&
			    sme->ie[i + 3] == 0x45 && sme->ie[i + 4] == 0xDA &&
			    sme->ie[i + 5] == 0x02) {
				ie_set_flag = true;
				ret = sprd_set_assocreq_ie(vif->priv, vif,
							   sme->ie, i);
				if (ret)
					goto err;

				ret = sprd_set_sae_ie(vif->priv, vif,
						      sme->ie + i,
						      (sme->ie_len - i));
				if (ret)
					goto err;
			}
		}

		if (!ie_set_flag) {
			netdev_info(ndev, "set assoc req ie, len %zx\n",
				    sme->ie_len);

			ret = sprd_set_assocreq_ie(vif->priv, vif, sme->ie,
						   sme->ie_len);
			if (ret)
				goto err;
		}
	} else {
		netdev_info(ndev, "set assoc req ie, len %zx\n", sme->ie_len);
		ret = sprd_set_assocreq_ie(vif->priv, vif, sme->ie,
					   sme->ie_len);

		if (ret)
			goto err;
	}

	con.wpa_versions = sprd_convert_wpa_version(sme->crypto.wpa_versions);
	netdev_info(ndev, "sme->wpa versions %#x, con.wpa_version:%#x\n",
		    sme->crypto.wpa_versions, con.wpa_versions);
	netdev_info(ndev, "management frame protection %#x\n", sme->mfp);
	con.mfp_enable = sme->mfp;

	netdev_info(ndev, "auth type %#x\n", sme->auth_type);
	if (sme->auth_type == NL80211_AUTHTYPE_OPEN_SYSTEM ||
	    (sme->auth_type == NL80211_AUTHTYPE_AUTOMATIC && !is_wep))
		con.auth_type = SPRD_AUTH_OPEN;
	else if (sme->auth_type == NL80211_AUTHTYPE_SHARED_KEY ||
		 (sme->auth_type == NL80211_AUTHTYPE_AUTOMATIC && is_wep))
		con.auth_type = SPRD_AUTH_SHARED;
	else if (sme->auth_type == NL80211_AUTHTYPE_SAE)
		con.auth_type = SPRD_AUTH_SAE;

	/* Set pairewise cipher */
	if (sme->crypto.n_ciphers_pairwise) {
		netdev_info(ndev, "pairwise cipher %#x\n",
			    sme->crypto.ciphers_pairwise[0]);
		vif->prwise_crypto =
		    sprd_parse_cipher(sme->crypto.ciphers_pairwise[0]);
		con.pairwise_cipher = vif->prwise_crypto | SPRD_VALID_CONFIG;
	} else {
		netdev_dbg(ndev, "No pairewise cipher specified!\n");
		vif->prwise_crypto = SPRD_CIPHER_NONE;
	}

	/* Set group cipher */
	vif->grp_crypto = sprd_parse_cipher(sme->crypto.cipher_group);
	netdev_info(ndev, "group cipher %#x\n", sme->crypto.cipher_group);
	con.group_cipher = vif->grp_crypto | SPRD_VALID_CONFIG;

	/* Set auth key management (akm) */
	if (sme->crypto.n_akm_suites) {
		netdev_info(ndev, "akm suites %#x\n",
			    sme->crypto.akm_suites[0]);
		con.key_mgmt = sprd_parse_akm(sme->crypto.akm_suites[0]);
		con.key_mgmt |= SPRD_VALID_CONFIG;
	} else {
		netdev_dbg(ndev, "No akm suites specified!\n");
	}

	/* Set PSK */
	if (sme->key_len) {
		if (sme->key_len > WLAN_MAX_KEY_LEN) {
			netdev_err(ndev, "%s invalid key len: %d\n", __func__,
				   sme->key_len);
			ret = -EINVAL;
			goto err;
		} else if (sme->crypto.cipher_group == WLAN_CIPHER_SUITE_WEP40 ||
		    sme->crypto.cipher_group == WLAN_CIPHER_SUITE_WEP104 ||
		    sme->crypto.ciphers_pairwise[0] ==
		    WLAN_CIPHER_SUITE_WEP40 ||
		    sme->crypto.ciphers_pairwise[0] ==
		    WLAN_CIPHER_SUITE_WEP104) {
			vif->key_index[SPRD_GROUP] = sme->key_idx;
			vif->key_len[SPRD_GROUP][sme->key_idx] = sme->key_len;
			memcpy(vif->key[SPRD_GROUP][sme->key_idx], sme->key,
			       sme->key_len);
			ret =
			    cfg80211_add_cipher_key(vif, 0, sme->key_idx,
						    sme->crypto.ciphers_pairwise[0],
						    NULL, NULL);
			if (ret)
				goto err;
		} else {
			con.psk_len = sme->key_len;
			memcpy(con.psk, sme->key, sme->key_len);
		}
	}

	/* Auth RX unencrypted EAPOL is not implemented, do nothing */
	/* Set channel */
	if (sme->channel) {
		center_freq = sme->channel->center_freq;
		con.channel =
		    ieee80211_frequency_to_channel(sme->channel->center_freq);
		netdev_info(ndev, "channel %d, band %d, center_freq %u.\n",
			con.channel, sme->channel->band, sme->channel->center_freq);
	} else if (sme->channel_hint) {
		center_freq = sme->channel_hint->center_freq;
		con.channel =
		    ieee80211_frequency_to_channel(sme->
						   channel_hint->center_freq);
		netdev_info(ndev, "channel_hint %d, band %d, center_freq %u.\n", con.channel,
			sme->channel_hint->band, sme->channel_hint->center_freq);
	} else {
		netdev_info(ndev, "No channel specified!\n");
	}

	/* Set BSSID */
	if (sme->bssid) {
		netdev_info(ndev, "bssid %pM\n", sme->bssid);
		memcpy(con.bssid, sme->bssid, sizeof(con.bssid));
		memcpy(vif->bssid, sme->bssid, sizeof(vif->bssid));
	} else if (sme->bssid_hint) {
		netdev_info(ndev, "bssid_hint %pM\n", sme->bssid_hint);
		memcpy(con.bssid, sme->bssid_hint, sizeof(con.bssid));
		memcpy(vif->bssid, sme->bssid_hint, sizeof(vif->bssid));
	} else {
		netdev_info(ndev, "No BSSID specified!\n");
	}

	/* Special process for WEP(WEP key must be set before essid) */
	if (sme->crypto.cipher_group == WLAN_CIPHER_SUITE_WEP40 ||
	    sme->crypto.cipher_group == WLAN_CIPHER_SUITE_WEP104) {
		netdev_info(ndev, "%s WEP cipher_group\n", __func__);

		if (sme->key_len <= 0) {
			netdev_dbg(ndev, "No key specified!\n");
		} else {
			if (sme->key_len != WLAN_KEY_LEN_WEP104 &&
			    sme->key_len != WLAN_KEY_LEN_WEP40) {
				netdev_err(ndev, "%s invalid WEP key length!\n",
					   __func__);
				ret = -EINVAL;
				goto err;
			}

			ret = sprd_set_def_key(vif->priv, vif, sme->key_idx);
			if (ret)
				goto err;
		}
	}

	/* Set ESSID */
	if (!sme->ssid) {
		netdev_dbg(ndev, "No SSID specified!\n");
	} else {
		strncpy(con.ssid, sme->ssid, sme->ssid_len);
		con.ssid_len = sme->ssid_len;
		vif->sm_state = SPRD_CONNECTING;

		ret = sprd_connect(vif->priv, vif, &con);
		if (ret)
			goto err;
		strncpy(vif->ssid, sme->ssid, sme->ssid_len);
		vif->ssid_len = sme->ssid_len;
        //modified by HQ:zhuziwen sprd_patch_2247860 for wifi name security
		netdev_info(ndev, "%s %pM\n", __func__, vif->bssid);
        //modified by HQ:zhuziwen end
	}

	return 0;
err:
	netdev_err(ndev, "%s failed\n", __func__);
	vif->sm_state = old_state;
	return ret;
}

int sprd_cfg80211_set_wiphy_params(struct wiphy *wiphy, int radio_idx, u32 changed)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);
	u16 rts = 0, frag = 0;

	if (changed & WIPHY_PARAM_RTS_THRESHOLD)
		rts = wiphy->rts_threshold;

	if (changed & WIPHY_PARAM_FRAG_THRESHOLD)
		frag = wiphy->frag_threshold;

	return sprd_set_param(priv, rts, frag);
}

int sprd_cfg80211_dump_survey(struct wiphy *wiphy, struct net_device *ndev,
			      int idx, struct survey_info *s_info)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	return sprd_dump_survey(vif->priv, wiphy, ndev, idx, s_info);
}

int sprd_cfg80211_set_pmksa(struct wiphy *wiphy, struct net_device *ndev,
			    struct cfg80211_pmksa *pmksa)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);

	return sprd_pmksa(vif->priv, vif, pmksa->bssid,
			  pmksa->pmkid, SUBCMD_SET);
}

int sprd_cfg80211_del_pmksa(struct wiphy *wiphy, struct net_device *ndev,
			    struct cfg80211_pmksa *pmksa)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);

	return sprd_pmksa(vif->priv, vif, pmksa->bssid,
			  pmksa->pmkid, SUBCMD_DEL);
}

int sprd_cfg80211_flush_pmksa(struct wiphy *wiphy, struct net_device *ndev)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);

	return sprd_pmksa(vif->priv, vif, vif->bssid, NULL, SUBCMD_FLUSH);
}

/* P2P related stuff */
int sprd_cfg80211_remain_on_channel(struct wiphy *wiphy,
				    struct wireless_dev *wdev,
				    struct ieee80211_channel *chan,
				    unsigned int duration, u64 *cookie)
{
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	enum nl80211_channel_type channel_type = 0;
	static u64 remain_index;
	int ret;

	*cookie = vif->listen_cookie = MU300_COOKIE(*cookie, ++remain_index);
	netdev_info(wdev->netdev, "%s %d for %dms, cookie %lld\n",
		    __func__, chan->center_freq, duration, *cookie);
	memcpy(&vif->listen_channel, chan, sizeof(struct ieee80211_channel));

	ret = sprd_remain_chan(vif->priv, vif, chan,
			       channel_type, duration, cookie);
	if (ret)
		return ret;

	cfg80211_ready_on_channel(wdev, *cookie, chan, duration, GFP_KERNEL);

	return 0;
}

int sprd_cfg80211_cancel_remain_on_channel(struct wiphy *wiphy,
					   struct wireless_dev *wdev,
					   u64 cookie)
{
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);

	netdev_info(wdev->netdev, "%s cookie %lld\n", __func__, cookie);

	return sprd_cancel_remain_chan(vif->priv, vif, cookie);
}

int sprd_cfg80211_mgmt_tx(struct wiphy *wiphy, struct wireless_dev *wdev,
			  struct cfg80211_mgmt_tx_params *params, u64 *cookie)
{
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct ieee80211_channel *chan = params->chan;
	const u8 *buf = params->buf;
	size_t len = params->len;
	unsigned int wait = params->wait;
	bool dont_wait_for_ack = params->dont_wait_for_ack;
	static u64 mgmt_index;
	int ret = 0;

	/* MU300 (from PR #237, oswaldxdd): nl80211 lets an access point send a management frame on its own channel
	 * without naming a frequency, and the vendor code dereferenced params->chan regardless (an oops at
	 * mgmt_tx+0x60). Without a channel: the access point's own, never an off-channel one.
	 */
	if (!chan) {
		if (params->offchan ||
		    (wdev->iftype != NL80211_IFTYPE_AP && wdev->iftype != NL80211_IFTYPE_P2P_GO))
			return -EINVAL;
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		/* one non-MLO link on this chip */
		if (wdev->valid_links || params->link_id > 0)
			return -EINVAL;
		chan = wdev->links[0].ap.chandef.chan;
#else
		chan = wdev->chandef.chan;
#endif
		if (!chan)
			return -ENODATA;
	}

	*cookie = MU300_COOKIE(*cookie, ++mgmt_index);
	netdev_info(wdev->netdev, "%s cookie %lld\n", __func__, *cookie);

	sprd_dump_frame_prot_info(1, chan->center_freq, buf, len);
	/* send tx mgmt */
	if (len > 0) {
		ret = sprd_tx_mgmt(vif->priv, vif,
				   ieee80211_frequency_to_channel
				   (chan->center_freq), dont_wait_for_ack,
				   wait, cookie, buf, len);
		if (ret || vif->priv->tx_mgmt_status)
			if (!dont_wait_for_ack) {
				cfg80211_mgmt_tx_status(wdev, *cookie, buf, len,
							0, GFP_KERNEL);
				vif->priv->tx_mgmt_status = 0;
			}
	}

	return ret;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
void sprd_cfg80211_mgmt_frame_register(struct wiphy *wiphy,
				       struct wireless_dev *wdev,
				       struct mgmt_frame_regs *upd)
{
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_work *misc_work;
	struct sprd_reg_mgmt *reg_mgmt;
	unsigned long new_mask, old_mask, change_mask;
	u16 frame_type;
	bool reg;
	int i = 0;

	if (vif->mode == SPRD_MODE_NONE)
		return;

	new_mask = upd->interface_stypes;
	old_mask = vif->mgmt_reg;
	if (new_mask == old_mask)
		return;
	change_mask = new_mask ^ old_mask;
	vif->mgmt_reg = new_mask;
	for_each_set_bit(i, &change_mask, MGMT_REG_MASK_BIT)  {
		if(test_bit(i, &old_mask))
			reg = 0;
		else
			reg = 1;

		frame_type = i << 4;
		netdev_info(wdev->netdev, "frame_type %d, reg %d\n", frame_type, reg);
		misc_work = sprd_alloc_work(sizeof(*reg_mgmt));
		if (!misc_work) {
			netdev_err(wdev->netdev, "%s out of memory\n", __func__);
			return;
		}

		misc_work->vif = vif;
		misc_work->id = SPRD_WORK_REG_MGMT;

		reg_mgmt = (struct sprd_reg_mgmt *)misc_work->data;
		reg_mgmt->type = frame_type;
		reg_mgmt->reg = reg;

		sprd_queue_work(vif->priv, misc_work);
	}
}
#else
void sprd_cfg80211_mgmt_frame_register(struct wiphy *wiphy,
				       struct wireless_dev *wdev,
				       u16 frame_type, bool reg)
{
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_work *misc_work;
	struct sprd_reg_mgmt *reg_mgmt;
	u16 mgmt_type;

	if (vif->mode == SPRD_MODE_NONE)
		return;

	mgmt_type = (frame_type & IEEE80211_FCTL_STYPE) >> 4;
	if ((reg && test_and_set_bit(mgmt_type, &vif->mgmt_reg)) ||
	    (!reg && !test_and_clear_bit(mgmt_type, &vif->mgmt_reg))) {
		netdev_dbg(wdev->netdev, "%s  mgmt %d has %sreg\n", __func__,
			   frame_type, reg ? "" : "un");
		return;
	}

	netdev_info(wdev->netdev, "frame_type %d, reg %d\n", frame_type, reg);

	misc_work = sprd_alloc_work(sizeof(*reg_mgmt));
	if (!misc_work) {
		netdev_err(wdev->netdev, "%s out of memory\n", __func__);
		return;
	}

	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_REG_MGMT;

	reg_mgmt = (struct sprd_reg_mgmt *)misc_work->data;
	reg_mgmt->type = frame_type;
	reg_mgmt->reg = reg;

	sprd_queue_work(vif->priv, misc_work);
}
#endif

int sprd_cfg80211_set_power_mgmt(struct wiphy *wiphy, struct net_device *ndev,
				 bool enabled, int timeout)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s power save status:%d\n", __func__, enabled);

	return sprd_power_save(vif->priv, vif, SPRD_SET_PS_STATE, enabled);
}

/* Roaming related stuff */
int sprd_cfg80211_set_cqm_rssi_config(struct wiphy *wiphy,
				      struct net_device *ndev,
				      s32 rssi_thold, u32 rssi_hyst)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s rssi_thold %d rssi_hyst %d",
		    __func__, rssi_thold, rssi_hyst);

	return sprd_set_cqm_rssi(vif->priv, vif, rssi_thold, rssi_hyst);
}

int sprd_cfg80211_sched_scan_start(struct wiphy *wiphy, struct net_device *ndev,
				   struct cfg80211_sched_scan_request *request)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);

	return sprd_sched_scan_start(priv, wiphy, ndev, request);
}

int sprd_cfg80211_sched_scan_stop(struct wiphy *wiphy, struct net_device *ndev,
				  u64 reqid)
{
	struct sprd_priv *priv = wiphy_priv(wiphy);

	return sprd_sched_scan_stop(priv, wiphy, ndev, reqid);
}

int sprd_cfg80211_start_p2p_device(struct wiphy *wiphy,
				   struct wireless_dev *wdev)
{
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_hif *hif = &vif->priv->hif;
	int ret;

	netdev_info(vif->ndev, "%s\n", __func__);

	wiphy_info(wiphy, "Power on WCN (%d time)\n",
		   atomic_read(&hif->power_cnt));
	ret = sprd_iface_set_power(hif, true);
	if (ret)
		return ret;

	return sprd_init_fw(vif);
}

void sprd_cfg80211_stop_p2p_device(struct wiphy *wiphy,
				   struct wireless_dev *wdev)
{
	struct sprd_vif *vif = container_of(wdev, struct sprd_vif, wdev);
	struct sprd_hif *hif = &vif->priv->hif;

	netdev_info(vif->ndev, "%s\n", __func__);

	/* hif->power_cnt = 1 means there is only one mode and
	 * stop_marlin will be called after closed.but it should
	 * not send any command between close and stop_marlin,
	 * block_cmd_after_close need set to 1 to block other cmd.
	 */
	if (atomic_read(&hif->power_cnt) == 1)
		atomic_set(&hif->block_cmd_after_close, 1);

	sprd_report_scan_done(vif, true);
	sprd_uninit_fw(vif);

	wiphy_info(wiphy, "Power off WCN (%d time)\n",
		   atomic_read(&hif->power_cnt));
	sprd_iface_set_power(hif, false);

	if (atomic_read(&hif->block_cmd_after_close) == 1)
		atomic_set(&hif->block_cmd_after_close, 0);
}

int sprd_cfg80211_set_mac_acl(struct wiphy *wiphy, struct net_device *ndev,
			      const struct cfg80211_acl_data *acl)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	int index, num;
	int mode = SPRD_ACL_MODE_DISABLE;
	unsigned char *mac_addr = NULL;
	int ret;

	if (!acl || !acl->n_acl_entries) {
		netdev_err(ndev, "%s no ACL data\n", __func__);
		return 0;
	}

	if (acl->acl_policy == NL80211_ACL_POLICY_DENY_UNLESS_LISTED) {
		mode = SPRD_ACL_MODE_WHITELIST;
	} else if (acl->acl_policy == NL80211_ACL_POLICY_ACCEPT_UNLESS_LISTED) {
		mode = SPRD_ACL_MODE_BLACKLIST;
	} else {
		netdev_err(ndev, "%s invalid ACL mode\n", __func__);
		return -EINVAL;
	}

	num = acl->n_acl_entries;
	netdev_info(ndev, "%s ACL MAC num:%d\n", __func__, num);
	if (num < 0 || num > vif->priv->max_acl_mac_addrs)
		return -EINVAL;

	mac_addr = kzalloc(num * ETH_ALEN, GFP_KERNEL);
	if (!mac_addr)
		return -ENOMEM;

	for (index = 0; index < num; index++) {
		netdev_info(ndev, "%s  MAC: %pM\n", __func__,
			    &acl->mac_addrs[index]);
		memcpy(mac_addr + index * ETH_ALEN,
		       &acl->mac_addrs[index], ETH_ALEN);
	}

	if (mode == SPRD_ACL_MODE_WHITELIST)
		ret = sprd_set_whitelist(vif->priv, vif,
					 SUBCMD_ENABLE, num, mac_addr);
	else
		ret = sprd_set_blacklist(vif->priv, vif,
					 SUBCMD_ADD, num, mac_addr);

	kfree(mac_addr);
	return ret;
}

int sprd_cfg80211_update_ft_ies(struct wiphy *wiphy, struct net_device *ndev,
				struct cfg80211_update_ft_ies_params *ftie)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);

	return sprd_set_roam_offload(vif->priv, vif,
				     SPRD_ROAM_OFFLOAD_SET_FTIE,
				     ftie->ie, ftie->ie_len);
}

int sprd_cfg80211_set_qos_map(struct wiphy *wiphy, struct net_device *ndev,
			      struct cfg80211_qos_map *qos_map)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);

	return sprd_set_qos_map(vif->priv, vif, (void *)qos_map);
}

int sprd_cfg80211_add_tx_ts(struct wiphy *wiphy, struct net_device *ndev,
			    u8 tsid, const u8 *peer, u8 user_prio,
			    u16 admitted_time)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);

	return sprd_add_tx_ts(vif->priv, vif, tsid, peer,
			      user_prio, admitted_time);
}

int sprd_cfg80211_del_tx_ts(struct wiphy *wiphy, struct net_device *ndev,
			    u8 tsid, const u8 *peer)
{
	struct sprd_vif *vif = netdev_priv(ndev);

	netdev_info(ndev, "%s\n", __func__);

	return sprd_del_tx_ts(vif->priv, vif, tsid, peer);
}

int sprd_init_fw(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;
	enum nl80211_iftype type = vif->wdev.iftype;
	enum sprd_mode mode;
	u8 *mac;
	int ret;
	char country_alpha[3] = "00";

	vif->ctx_id = 0;

	netdev_info(vif->ndev, "%s type %d, mode %d, name %s\n", __func__, type,
		    vif->mode, vif->name);

	if (vif->mode != SPRD_MODE_NONE) {
		netdev_err(vif->ndev, "%s already in use: mode %d\n",
			   __func__, vif->mode);
		return -EBUSY;
	}

	mode = cfg80211_type_to_mode(type, vif->name);
	if (mode <= SPRD_MODE_NONE || mode >= SPRD_MODE_MAX) {
		netdev_err(vif->ndev, "%s unsupported interface type: %d\n",
			   __func__, type);
		return -EINVAL;
	}

	if (vif->state & VIF_STATE_OPEN) {
		netdev_err(vif->ndev, "%s mode %d already opened\n",
			   __func__, mode);
		return 0;
	}

	vif->mode = mode;
	if (!vif->ndev)
		mac = vif->wdev.address;
	else
		mac = vif->ndev->dev_addr;

	if (vif->mode == SPRD_MODE_AP || vif->mode == SPRD_MODE_P2P_GO) {
		if (vif->has_rand_mac) {
			netdev_info(vif->ndev, "use random mac addr:%pM\n",
				    vif->random_mac);
			mac = vif->random_mac;
		}
	}

	if (sprd_open_fw(priv, vif, mac)) {
		netdev_err(vif->ndev, "%s failed!\n", __func__);
		vif->mode = SPRD_MODE_NONE;
		return -EIO;
	}
	vif->state |= VIF_STATE_OPEN;
	sprd_hif_fill_all_buffer(&priv->hif);

	if ((priv->hif.hw_type == SPRD_HW_SC2355_SDIO ||
		priv->hif.hw_type == SPRD_HW_SC2332_SIPC) &&
		(vif->mode == SPRD_MODE_AP || vif->mode == SPRD_MODE_STATION)) {
		ret = regulatory_hint(priv->wiphy, country_alpha);
		netdev_info(vif->ndev, "%s type %d, mode %d, name %s, regulatory_hint ret = %d.\n",
			__func__, type, vif->mode, vif->name, ret);
	}

	return 0;
}
#ifdef DRV_RESET_SELF
EXPORT_SYMBOL(sprd_init_fw);
#endif

extern void sc2355_handle_tx_status_after_close(struct sprd_vif *vif);
int sprd_uninit_fw(struct sprd_vif *vif)
{
	struct sprd_priv *priv = vif->priv;
	struct sprd_hif *hif = &priv->hif;

	if (vif->mode <= SPRD_MODE_NONE || vif->mode >= SPRD_MODE_MAX) {
		netdev_err(vif->ndev, "%s invalid operation mode: %d\n",
			   __func__, vif->mode);
		return -EINVAL;
	}

	if (!(vif->state & VIF_STATE_OPEN)) {
		netdev_err(vif->ndev, "%s mode %d already closed\n",
			   __func__, vif->mode);
		return -EBUSY;
	}

	//do not send data when mode close
	vif->state &= ~VIF_STATE_OPEN;
	sprd_hif_tx_flush(hif, vif);

	if (sprd_close_fw(priv, vif)) {
		netdev_err(vif->ndev, "%s failed!\n", __func__);
		return -EIO;
	}

	if (hif->hw_type == SPRD_HW_SC2355_PCIE)
		sc2355_handle_tx_status_after_close(vif);

	netdev_info(vif->ndev, "%s type %d, mode %d\n", __func__,
		    vif->wdev.iftype, vif->mode);
	vif->mode = SPRD_MODE_NONE;

	return 0;
}

/*
 * MU300: the stations of the access point. hostapd knows them, but iw, iwinfo and so LuCI ask the driver, which
 * had no dump_station (issue #8). The firmware's peer table has them: the entries of this interface with a
 * unicast address other than its own (the table also holds the interface itself and its broadcast entry). The
 * firmware reports no signal or byte counts for them, so only the address is filled in.
 */
static int sprd_cfg80211_dump_station(struct wiphy *wiphy, struct net_device *ndev, int idx, u8 *mac,
				      struct station_info *sinfo)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_hif *hif = &vif->priv->hif;
	int i, n = 0;

	if (idx < 0 || (vif->wdev.iftype != NL80211_IFTYPE_AP && vif->wdev.iftype != NL80211_IFTYPE_P2P_GO))
		return -ENOENT;
	for (i = 0; i < MAX_LUT_NUM; i++) {
		const u8 *da = hif->peer_entry[i].tx.da;

		if (hif->peer_entry[i].ctx_id != vif->ctx_id || !is_valid_ether_addr(da) ||
		    ether_addr_equal(da, ndev->dev_addr))
			continue;
		if (n++ < idx)
			continue;
		ether_addr_copy(mac, da);
		sinfo->filled = 0;
		return 0;
	}
	return -ENOENT;
}

/*
 * MU300: the access point's channel, which cfg80211 keeps from start_ap (iwinfo showed "Channel: 0"). The driver
 * has no MLO: link 0 only (links[] has IEEE80211_MLD_MAX_NUM_LINKS entries; another link_id is not ours to index).
 */
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static int sprd_cfg80211_get_channel(struct wiphy *wiphy, struct wireless_dev *wdev, unsigned int link_id,
				     struct cfg80211_chan_def *chandef)
{
	if (link_id || (wdev->iftype != NL80211_IFTYPE_AP && wdev->iftype != NL80211_IFTYPE_P2P_GO) ||
	    !wdev->links[0].ap.chandef.chan)
		return -ENODATA;
	*chandef = wdev->links[0].ap.chandef;
	return 0;
}
#else
static int sprd_cfg80211_get_channel(struct wiphy *wiphy, struct wireless_dev *wdev,
				     struct cfg80211_chan_def *chandef)
{
	if ((wdev->iftype != NL80211_IFTYPE_AP && wdev->iftype != NL80211_IFTYPE_P2P_GO) || !wdev->chandef.chan)
		return -ENODATA;
	*chandef = wdev->chandef;
	return 0;
}
#endif

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(7, 0, 0))
/*
 * MU300: 7.x hands the key and station ops a wireless_dev where 6.x passed the net_device, and adds rx_addr to
 * remain_on_channel. The driver's functions stay as they are; these adapt the calls.
 */
static int sprd_cfg80211_add_key_wdev(struct wiphy *wiphy, struct wireless_dev *wdev, int link_id,
				      u8 key_index, bool pairwise, const u8 *mac_addr,
				      struct key_params *params)
{
	return sprd_cfg80211_add_key(wiphy, wdev->netdev, link_id, key_index, pairwise, mac_addr, params);
}

static int sprd_cfg80211_del_key_wdev(struct wiphy *wiphy, struct wireless_dev *wdev, int link_id,
				      u8 key_index, bool pairwise, const u8 *mac_addr)
{
	return sprd_cfg80211_del_key(wiphy, wdev->netdev, link_id, key_index, pairwise, mac_addr);
}

static int sprd_cfg80211_set_default_mgmt_key_wdev(struct wiphy *wiphy, struct wireless_dev *wdev,
						   int link_id, u8 key_index)
{
	return sprd_cfg80211_set_default_mgmt_key(wiphy, wdev->netdev, link_id, key_index);
}

static int sprd_cfg80211_add_station_wdev(struct wiphy *wiphy, struct wireless_dev *wdev,
					  const u8 *mac, struct station_parameters *params)
{
	return sprd_cfg80211_add_station(wiphy, wdev->netdev, mac, params);
}

static int sprd_cfg80211_del_station_wdev(struct wiphy *wiphy, struct wireless_dev *wdev,
					  struct station_del_parameters *params)
{
	return sprd_cfg80211_del_station(wiphy, wdev->netdev, params);
}

static int sprd_cfg80211_change_station_wdev(struct wiphy *wiphy, struct wireless_dev *wdev,
					     const u8 *mac, struct station_parameters *params)
{
	return sprd_cfg80211_change_station(wiphy, wdev->netdev, mac, params);
}

static int sprd_cfg80211_get_station_wdev(struct wiphy *wiphy, struct wireless_dev *wdev,
					  const u8 *mac, struct station_info *sinfo)
{
	return sprd_cfg80211_get_station(wiphy, wdev->netdev, mac, sinfo);
}

static int sprd_cfg80211_dump_station_wdev(struct wiphy *wiphy, struct wireless_dev *wdev, int idx, u8 *mac,
					   struct station_info *sinfo)
{
	return sprd_cfg80211_dump_station(wiphy, wdev->netdev, idx, mac, sinfo);
}

static int sprd_cfg80211_remain_on_channel_wdev(struct wiphy *wiphy, struct wireless_dev *wdev,
						struct ieee80211_channel *chan, unsigned int duration,
						ck_t cookie, const u8 *rx_addr)
{
	return sprd_cfg80211_remain_on_channel(wiphy, wdev, chan, duration, MU300_COOKIE_PTR(cookie));
}
#define MU300_WDEV_OP(f) f##_wdev
#else
#define MU300_WDEV_OP(f) f
#endif

static struct cfg80211_ops sprd_cfg80211_ops = {
	.add_virtual_intf = sprd_cfg80211_add_iface,
	.del_virtual_intf = sprd_cfg80211_del_iface,
	.change_virtual_intf = sprd_cfg80211_change_iface,
	.add_key = MU300_WDEV_OP(sprd_cfg80211_add_key),
	.del_key = MU300_WDEV_OP(sprd_cfg80211_del_key),
	.set_default_key = sprd_cfg80211_set_default_key,
	.set_default_mgmt_key = MU300_WDEV_OP(sprd_cfg80211_set_default_mgmt_key),
	.start_ap = sprd_cfg80211_start_ap,
	.change_beacon = sprd_cfg80211_change_beacon,
	.stop_ap = sprd_cfg80211_stop_ap,
	.add_station = MU300_WDEV_OP(sprd_cfg80211_add_station),
	.del_station = MU300_WDEV_OP(sprd_cfg80211_del_station),
	.change_station = MU300_WDEV_OP(sprd_cfg80211_change_station),
	.get_station = MU300_WDEV_OP(sprd_cfg80211_get_station),
	.dump_station = MU300_WDEV_OP(sprd_cfg80211_dump_station),
	.get_channel = sprd_cfg80211_get_channel,
	.libertas_set_mesh_channel = sprd_cfg80211_set_channel,
	.scan = sprd_cfg80211_scan,
	.connect = sprd_cfg80211_connect,
	.disconnect = sprd_cfg80211_disconnect,
	.set_wiphy_params = sprd_cfg80211_set_wiphy_params,
	.dump_survey = sprd_cfg80211_dump_survey,
	.set_pmksa = sprd_cfg80211_set_pmksa,
	.del_pmksa = sprd_cfg80211_del_pmksa,
	.flush_pmksa = sprd_cfg80211_flush_pmksa,
	.remain_on_channel = MU300_WDEV_OP(sprd_cfg80211_remain_on_channel),
	.cancel_remain_on_channel = sprd_cfg80211_cancel_remain_on_channel,
	.mgmt_tx = MU300_MGMT_TX,
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	.update_mgmt_frame_registrations = sprd_cfg80211_mgmt_frame_register,
#else
	.mgmt_frame_register = sprd_cfg80211_mgmt_frame_register,
#endif
	.set_power_mgmt = sprd_cfg80211_set_power_mgmt,
	.set_cqm_rssi_config = sprd_cfg80211_set_cqm_rssi_config,
	.sched_scan_start = sprd_cfg80211_sched_scan_start,
	.sched_scan_stop = sprd_cfg80211_sched_scan_stop,
	.tdls_mgmt = sprd_cfg80211_tdls_mgmt,
	.tdls_oper = sprd_cfg80211_tdls_oper,
	.start_p2p_device = sprd_cfg80211_start_p2p_device,
	.stop_p2p_device = sprd_cfg80211_stop_p2p_device,
	.set_mac_acl = sprd_cfg80211_set_mac_acl,
	.update_ft_ies = sprd_cfg80211_update_ft_ies,
	.set_qos_map = sprd_cfg80211_set_qos_map,
	.add_tx_ts = sprd_cfg80211_add_tx_ts,
	.del_tx_ts = sprd_cfg80211_del_tx_ts,
	.tdls_channel_switch = sprd_cfg80211_tdls_chan_switch,
	.tdls_cancel_channel_switch = sprd_cfg80211_tdls_cancel_chan_switch,
};

void sprd_timer_scan_timeout(struct timer_list *t)
{
	struct sprd_priv *priv = from_timer(priv, t, scan_timer);

	sprd_scan_timeout(priv, t);
}

struct sprd_priv *sprd_core_create(struct sprd_chip_ops *chip_ops)
{
	struct wiphy *wiphy;
	struct sprd_priv *priv;
	struct sprd_chip *chip;

	wiphy = wiphy_new(&sprd_cfg80211_ops, sizeof(*priv));
	if (!wiphy) {
		pr_err("failed to allocate wiphy!\n");
		return NULL;
	}
	priv = wiphy_priv(wiphy);
	priv->wiphy = wiphy;

	chip = &priv->chip;
	chip->priv = priv;
	chip->ops = chip_ops;
	atomic_set(&priv->monitor_mode, 0);

	sprd_cfg80211_ops_update(priv, &sprd_cfg80211_ops);

	sprd_version_init(&priv->wl_ver);
	sprd_cmd_init(priv);

	timer_setup(&priv->scan_timer, sprd_timer_scan_timeout, 0);

	sprd_qos_wmm_ac_init(priv);
	sprd_qos_init_default_map(priv);

	spin_lock_init(&priv->scan_lock);
	spin_lock_init(&priv->sched_scan_lock);
	spin_lock_init(&priv->list_lock);
	INIT_LIST_HEAD(&priv->vif_list);
	cfg80211_init_work(priv);
#ifdef DRV_RESET_SELF
	cfg80211_init_reset_work(priv);
#endif

	return priv;
}

void sprd_core_free(struct sprd_priv *priv)
{
	struct wiphy *wiphy;

	if (!priv)
		return;

	cfg80211_deinit_work(priv);
#ifdef DRV_RESET_SELF
	cfg80211_deinit_reset_work(priv);
#endif
	/*
	 * MU300 (issue #146): the scan timer lives in priv and is only deleted when a scan completes; a scan still
	 * running at remove is reported done by iface_deinit_vif, but nothing owned the timer here, where priv goes.
	 */
	timer_shutdown_sync(&priv->scan_timer);
	sprd_cmd_deinit(priv);

	wiphy = priv->wiphy;
	if (wiphy)
		wiphy_free(wiphy);
}
