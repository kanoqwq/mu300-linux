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

#ifndef __IFACE_H__
#define __IFACE_H__

#include <linux/platform_device.h>

#include "acs.h"
#include "cfg80211.h"
#include "msg.h"

#define VIF_STATE_OPEN			BIT(0)

#define WIFI_MAC_ADDR_PATH		"/mnt/vendor/wifimac.txt"
#define WIFI_MAC_ADDR_TEMP		"/data/vendor/wifi/wifimac_temp.txt"

#define SPRDWLIOCTL			(SIOCDEVPRIVATE + 1)
#define SPRDWLSETMIRACAST		(SIOCDEVPRIVATE + 2)
#define SPRDWLSETFCC			(SIOCDEVPRIVATE + 3)
#define SPRDWLSETSUSPEND		(SIOCDEVPRIVATE + 4)
#define SPRDWLSETCOUNTRY		(SIOCDEVPRIVATE + 5)
#define SPRDWLSETP2PMAC			(SIOCDEVPRIVATE + 6)
#define SPRDWLVOWIFI			(SIOCDEVPRIVATE + 7)
#define SPRDWLSETNDEVMAC		(SIOCDEVPRIVATE + 8)
#define SPRDWLSNIFFER			(SIOCDEVPRIVATE + 9)

#define SPRD_RX_MODE_MULTICAST		1

#define CMD_BLACKLIST_ENABLE		"BLOCK"
#define CMD_BLACKLIST_DISABLE		"UNBLOCK"
#define CMD_ADD_WHITELIST		"WHITE_ADD"
#define CMD_DEL_WHITELIST		"WHITE_DEL"
#define CMD_ENABLE_WHITELIST		"WHITE_EN"
#define CMD_DISABLE_WHITELIST		"WHITE_DIS"
#define CMD_SETSUSPENDMODE		"SETSUSPENDMODE"
#define CMD_SET_FCC_CHANNEL		"SET_FCC_CHANNEL"
#define CMD_SET_COUNTRY			"COUNTRY"
#define CMD_REDUCE_TX_POWER		"SET_TX_POWER_CALLING"
#define CMD_11V_GET_CFG			"11VCFG_GET"
#define CMD_11V_SET_CFG			"11VCFG_SET"
#define CMD_11V_WNM_SLEEP		"WNM_SLEEP"
#define CMD_P2P_MAC			"P2PMACADDR"
#define CMD_SET_MAX_CLIENTS		"MAX_STA"
#define CMD_SET_SAR			"SET_SAR"
#define CMD_BT_COEX_MODE		"BTCOEXMODE"
#define CMD_BT_COEX_SCAN		"BTCOEXSCAN"


struct sprd_priv;
struct sprd_hif_ops;
struct sprd_chip_ops;
struct sprd_hif;

struct sprd_vif {
	struct net_device *ndev;	/* Linux net device */
	struct wireless_dev wdev;	/* Linux wireless device */
	struct sprd_priv *priv;

	char name[IFNAMSIZ];
	enum sprd_mode mode;
	u8 ctx_id;
	struct list_head vif_node;	/* node for virtual interface list */
	int ref;

	/* multicast filter stuff */
	struct sprd_mc_filter *mc_filter;

	/* common stuff */
	unsigned short state;
	enum sprd_sm_state sm_state;
	unsigned char mac[ETH_ALEN];
	int ssid_len;
	u8 ssid[IEEE80211_MAX_SSID_LEN];
	u8 bssid[ETH_ALEN];
	unsigned char beacon_loss;
	bool local_mac_flag;

	/* encryption stuff */
	u8 prwise_crypto;
	u8 grp_crypto;
	u8 key_index[2];
	u8 key[2][6][WLAN_MAX_KEY_LEN];
	u8 key_len[2][6];
	u8 key_txrsc[2][WLAN_MAX_KEY_LEN];
	unsigned long mgmt_reg;

	/* P2P stuff */
	struct ieee80211_channel listen_channel;
	u64 listen_cookie;
	struct list_head survey_info_list;
	/* mutex for survey_info_list */
	struct mutex survey_lock;
	u8 acs_scan_index;
	u8 random_mac[ETH_ALEN];
	bool has_rand_mac;
	u8 wps_flag;
	/* unused */
	struct list_head scan_head_ptr;
	struct kobject sprd_power_obj;
	bool reduce_power;
	enum nl80211_cqm_rssi_threshold_event cqm;
};

void sprd_put_vif(struct sprd_vif *vif);
struct sprd_vif *sprd_mode_to_vif(struct sprd_priv *priv, u8 vif_mode);

void sprd_net_flowcontrl(struct sprd_priv *priv, enum sprd_mode mode,
			 bool state);
void sprd_netif_rx(struct net_device *ndev, struct sk_buff *skb);
void sprd_rx_monitor_process(struct sprd_vif *vif,
			     unsigned char *data, unsigned int len);
struct wireless_dev *sprd_add_iface(struct sprd_priv *priv, const char *name,
				    enum nl80211_iftype type, u8 *addr);
int sprd_del_iface(struct sprd_priv *priv, struct sprd_vif *vif);

int sprd_iface_probe(struct platform_device *pdev,
		     struct sprd_hif_ops *hif_ops,
		     struct sprd_chip_ops *chip_ops);
int sprd_iface_remove(struct platform_device *pdev);

int sprd_iface_set_power(struct sprd_hif *hif, int val);

#endif
