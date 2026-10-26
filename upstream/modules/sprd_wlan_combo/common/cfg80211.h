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

#ifndef __CFG80211_H__
#define __CFG80211_H__

#include <net/cfg80211.h>
#include <linux/version.h>
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
#include <linux/ieee80211.h>
#endif

/* auth type */
#define SPRD_AUTH_OPEN			0
#define SPRD_AUTH_SHARED		1
#define SPRD_AUTH_SAE			4
/* parise or group key type */
#define SPRD_GROUP			0
#define SPRD_PAIRWISE			1
/* WPA version */
#define SPRD_WPA_VERSION_NONE		0
#define SPRD_WPA_VERSION_1		BIT(0)
#define SPRD_WPA_VERSION_2		BIT(1)
#define SPRD_WAPI_VERSION_1		BIT(2)
#define SPRD_WPA_VERSION_3		BIT(3)
/* cipher type */
#define SPRD_CIPHER_NONE		0
#define SPRD_CIPHER_WEP40		1
#define SPRD_CIPHER_WEP104		2
#define SPRD_CIPHER_TKIP		3
#define SPRD_CIPHER_CCMP		4
#define SPRD_CIPHER_AP_TKIP		5
#define SPRD_CIPHER_AP_CCMP		6
#define SPRD_CIPHER_WAPI		7
#define SPRD_CIPHER_AES_CMAC		8
/* cipher suite */
#define WLAN_CIPHER_SUITE_PMK		0x000FACFF
#define WLAN_CIPHER_SUITE_DPP		0x506F9A02
/* AKM suite */
#define WLAN_AKM_SUITE_WAPI_CERT	0x00147201
#define WLAN_AKM_SUITE_WAPI_PSK		0x00147202
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
//#define WLAN_AKM_SUITE_OWE		0x000FAC12
#define MGMT_REG_MASK_BIT	32
#else
#define WLAN_AKM_SUITE_OWE		0x000FAC12
#endif


#define SPRD_AKM_SUITE_NONE		(0)
#define SPRD_AKM_SUITE_8021X		(1)
#define SPRD_AKM_SUITE_PSK		(2)
#define SPRD_AKM_SUITE_FT_8021X	(3)
#define SPRD_AKM_SUITE_FT_PSK		(4)
#define SPRD_AKM_SUITE_WAPI_PSK	(4)
#define SPRD_AKM_SUITE_8021X_SHA256	(5)
#define SPRD_AKM_SUITE_PSK_SHA256	(6)
#define SPRD_AKM_SUITE_SAE		(8)
#define SPRD_AKM_SUITE_WAPI_CERT	(12)
#define SPRD_AKM_SUITE_OWE		(18)

#define WLAN_REASON_DEAUTH_LEAVING	3

#define WIPHY_FLAG_SUPPORTS_SCHED_SCAN	BIT(11)

/* determine the actual values for the macros below*/
#define SPRD_MAX_SCAN_SSIDS		12
#define SPRD_MAX_SCAN_IE_LEN		2304
#define SPRD_MAX_NUM_PMKIDS		4
#define SPRD_MAX_KEY_INDEX		5
#define SPRD_SCAN_TIMEOUT_MS		8000
#define SPRD_MIN_IE_LEN			6
#define SPRD_MAX_IE_LEN			500
#define SPRD_SCAN_RESULT_MAX_IE_LEN	1500

#define SPRD_2G_CHAN_NR			14
#define SPRD_5G_CHAN_NR			25
#define SPRD_TOTAL_CHAN_NR		\
	(SPRD_2G_CHAN_NR + SPRD_5G_CHAN_NR)
#define SPRD_TOTAL_SSID_NR		9

#define SPRD_AP_HIDDEN_FLAG_LEN		1
#define SPRD_AP_SSID_LEN_OFFSET		(37)
/* set wfa_cap a specified value to pass WFA Certification */
#define SPRD_WFA_CAP_11R		BIT(0)
#define SPRD_WFA_CAP_11K		BIT(1)
#define SPRD_WFA_CAP_WMM_AC		BIT(2)
#define SPRD_WFA_CAP_11U_QOS_MAP	BIT(3)
#define SPRD_WFA_CAP_11N_WMM		BIT(4)
#define SPRD_WFA_CAP_NON_RAN_MAC	BIT(5)

#define RATETAB_ENT(_rate, _rateid, _flags)				\
{									\
	.bitrate	= (_rate),					\
	.hw_value	= (_rateid),					\
	.flags		= (_flags),					\
}

#define CHAN2G(_channel, _freq, _flags)					\
{									\
	.band                   = NL80211_BAND_2GHZ,			\
	.center_freq            = (_freq),				\
	.hw_value               = (_channel),				\
	.flags                  = (_flags),				\
	.max_antenna_gain       = 0,					\
	.max_power              = 30,					\
}

#define CHAN5G(_channel, _flags)					\
{									\
	.band                   = NL80211_BAND_5GHZ,			\
	.center_freq		= 5000 + (5 * (_channel)),		\
	.hw_value		= (_channel),				\
	.flags			= (_flags),				\
	.max_antenna_gain	= 0,					\
	.max_power		= 30,					\
}

static const u32 sprd_cipher_suites[] = {
	WLAN_CIPHER_SUITE_WEP40,
	WLAN_CIPHER_SUITE_WEP104,
	WLAN_CIPHER_SUITE_TKIP,
	WLAN_CIPHER_SUITE_CCMP,
	WLAN_CIPHER_SUITE_SMS4,
	/* required by ieee802.11w */
	WLAN_CIPHER_SUITE_AES_CMAC,
	WLAN_CIPHER_SUITE_PMK,
};

/* Supported mgmt frame types to be advertised to cfg80211 */
static const struct ieee80211_txrx_stypes
sprd_mgmt_stypes[NUM_NL80211_IFTYPES] = {
	[NL80211_IFTYPE_STATION] = {
		.tx = 0xffff,
		.rx = BIT(IEEE80211_STYPE_ACTION >> 4) |
		BIT(IEEE80211_STYPE_PROBE_REQ >> 4)
		},
	[NL80211_IFTYPE_AP] = {
		.tx = 0xffff,
		.rx = BIT(IEEE80211_STYPE_ASSOC_REQ >> 4) |
		BIT(IEEE80211_STYPE_REASSOC_REQ >> 4) |
		BIT(IEEE80211_STYPE_PROBE_REQ >> 4) |
		BIT(IEEE80211_STYPE_DISASSOC >> 4) |
		BIT(IEEE80211_STYPE_AUTH >> 4) |
		BIT(IEEE80211_STYPE_DEAUTH >> 4) |
		BIT(IEEE80211_STYPE_ACTION >> 4)
		},
	[NL80211_IFTYPE_P2P_CLIENT] = {
		.tx = 0xffff,
		.rx = BIT(IEEE80211_STYPE_ACTION >> 4) |
		BIT(IEEE80211_STYPE_PROBE_REQ >> 4)
		},
	[NL80211_IFTYPE_P2P_GO] = {
		.tx = 0xffff,
		.rx = BIT(IEEE80211_STYPE_ASSOC_REQ >> 4) |
		BIT(IEEE80211_STYPE_REASSOC_REQ >> 4) |
		BIT(IEEE80211_STYPE_PROBE_REQ >> 4) |
		BIT(IEEE80211_STYPE_DISASSOC >> 4) |
		BIT(IEEE80211_STYPE_AUTH >> 4) |
		BIT(IEEE80211_STYPE_DEAUTH >> 4) |
		BIT(IEEE80211_STYPE_ACTION >> 4)
		},
	[NL80211_IFTYPE_P2P_DEVICE] = {
		.tx = 0xffff,
		.rx = BIT(IEEE80211_STYPE_ACTION >> 4) |
		BIT(IEEE80211_STYPE_PROBE_REQ >> 4)
		},
};

static const struct ieee80211_iface_limit sprd_iface_limits[] = {
	{
		.max = 1,
		.types = BIT(NL80211_IFTYPE_STATION) | BIT(NL80211_IFTYPE_AP)
	},
	{
		.max = 1,
		.types = BIT(NL80211_IFTYPE_P2P_CLIENT) |
			BIT(NL80211_IFTYPE_P2P_GO)
	},
	{
		.max = 1,
		.types = BIT(NL80211_IFTYPE_P2P_DEVICE)
	}
};

static const struct ieee80211_iface_combination sprd_iface_combos[] = {
	{
		.max_interfaces = 3,
		.num_different_channels = 2,
		.n_limits = ARRAY_SIZE(sprd_iface_limits),
		.limits = sprd_iface_limits
	}
};

#ifdef CONFIG_PM
static const struct wiphy_wowlan_support sprd_wowlan_support = {
	.flags = WIPHY_WOWLAN_ANY | WIPHY_WOWLAN_DISCONNECT,
};
#endif

enum sprd_mode {
	SPRD_MODE_NONE,
	SPRD_MODE_STATION,
	SPRD_MODE_AP,

	SPRD_MODE_P2P_DEVICE = 4,
	SPRD_MODE_P2P_CLIENT,
	SPRD_MODE_P2P_GO,

	SPRD_MODE_MONITOR = 7,

	SPRD_MODE_STATION_SECOND = 10,

	SPRD_MODE_MAX,
};

enum sprd_sm_state {
	SPRD_UNKNOWN = 0,
	SPRD_SCANNING,
	SPRD_SCAN_ABORTING,
	SPRD_DISCONNECTING,
	SPRD_DISCONNECTED,
	SPRD_CONNECTING,
	SPRD_CONNECTED
};

enum sprd_connect_result {
	SPRD_CONNECT_SUCCESS,
	SPRD_CONNECT_FAILED,
	SPRD_ROAM_SUCCESS,
	SPRD_ROAM_FAILED
};

enum sprd_acl_mode {
	SPRD_ACL_MODE_DISABLE,
	SPRD_ACL_MODE_WHITELIST,
	SPRD_ACL_MODE_BLACKLIST,
};

struct sprd_scan_ssid {
	u8 len;
	u8 ssid[0];
} __packed;

struct sprd_sched_scan {
	u32 interval;
	u32 flags;
	s32 rssi_thold;
	u8 channel[SPRD_TOTAL_CHAN_NR];

	u32 n_ssids;
	u8 *ssid[SPRD_TOTAL_CHAN_NR];
	u32 n_match_ssids;
	u8 *mssid[SPRD_TOTAL_CHAN_NR];

	const u8 *ie;
	size_t ie_len;
};

struct sprd_connect_info {
	u8 *bssid;
	u8 chan;
	s8 signal;
	u8 *bea_ie;
	u16 bea_ie_len;
	u8 *req_ie;
	u16 req_ie_len;
	u8 *resp_ie;
	u16 resp_ie_len;
} __packed;

struct sprd_reg_rule {
	struct ieee80211_freq_range freq_range;
	struct ieee80211_power_rule power_rule;
	u32 flags;
	u32 dfs_cac_ms;
};

struct sprd_ieee80211_regdomain {
	u32 n_reg_rules;
	char alpha2[2];
	struct sprd_reg_rule reg_rules[];
};

static inline __le32 sprd_convert_wpa_version(u32 version)
{
	u32 ret;

	switch (version) {
	case NL80211_WPA_VERSION_1:
		ret = SPRD_WPA_VERSION_1;
		break;
	case NL80211_WPA_VERSION_2:
		ret = SPRD_WPA_VERSION_2;
		break;
	case NL80211_WPA_VERSION_3:
		ret = SPRD_WPA_VERSION_3;
		break;
	case NL80211_WAPI_VERSION_1:
		ret = SPRD_WAPI_VERSION_1;
		break;
	default:
		ret = SPRD_WPA_VERSION_NONE;
		break;
	}

	return cpu_to_le32(ret);
}

static inline u8 sprd_parse_akm(u32 akm)
{
	u8 ret;

	switch (akm) {
	case WLAN_AKM_SUITE_PSK:
		ret = SPRD_AKM_SUITE_PSK;
		break;
	case WLAN_AKM_SUITE_8021X:
		ret = SPRD_AKM_SUITE_8021X;
		break;
	case WLAN_AKM_SUITE_FT_PSK:
		ret = SPRD_AKM_SUITE_FT_PSK;
		break;
	case WLAN_AKM_SUITE_FT_8021X:
		ret = SPRD_AKM_SUITE_FT_8021X;
		break;
	case WLAN_AKM_SUITE_WAPI_PSK:
		ret = SPRD_AKM_SUITE_WAPI_PSK;
		break;
	case WLAN_AKM_SUITE_WAPI_CERT:
		ret = SPRD_AKM_SUITE_WAPI_CERT;
		break;
	case WLAN_AKM_SUITE_PSK_SHA256:
		ret = SPRD_AKM_SUITE_PSK_SHA256;
		break;
	case WLAN_AKM_SUITE_8021X_SHA256:
		ret = SPRD_AKM_SUITE_8021X_SHA256;
		break;
	case WLAN_AKM_SUITE_OWE:
		ret = SPRD_AKM_SUITE_OWE;
		break;
	case WLAN_AKM_SUITE_SAE:
		ret = SPRD_AKM_SUITE_SAE;
		break;
	default:
		ret = SPRD_AKM_SUITE_NONE;
		break;
	}

	return ret;
}

static inline u8 sprd_parse_cipher(u32 cipher)
{
	u8 ret;

	switch (cipher) {
	case WLAN_CIPHER_SUITE_WEP40:
		ret = SPRD_CIPHER_WEP40;
		break;
	case WLAN_CIPHER_SUITE_WEP104:
		ret = SPRD_CIPHER_WEP104;
		break;
	case WLAN_CIPHER_SUITE_TKIP:
		ret = SPRD_CIPHER_TKIP;
		break;
	case WLAN_CIPHER_SUITE_CCMP:
		ret = SPRD_CIPHER_CCMP;
		break;
	case WLAN_CIPHER_SUITE_SMS4:
		ret = SPRD_CIPHER_WAPI;
		break;
	case WLAN_CIPHER_SUITE_AES_CMAC:
		ret = SPRD_CIPHER_AES_CMAC;
		break;
	default:
		ret = SPRD_CIPHER_NONE;
		break;
	}

	return ret;
}

static inline u16 sprd_channel_to_band(u16 chan)
{
	return chan <= SPRD_2G_CHAN_NR ? NL80211_BAND_2GHZ : NL80211_BAND_5GHZ;
}

struct sprd_vif;
struct sprd_priv;
struct sprd_chip_ops;

int sprd_cfg80211_scan(struct wiphy *wiphy,
		       struct cfg80211_scan_request *request);
int sprd_cfg80211_sched_scan_start(struct wiphy *wiphy, struct net_device *ndev,
				   struct cfg80211_sched_scan_request *request);
void sprd_cfg80211_abort_scan(struct wiphy *wiphy, struct wireless_dev *wdev);
int sprd_cfg80211_sched_scan_stop(struct wiphy *wiphy, struct net_device *ndev,
				  u64 reqid);
void sprd_dump_frame_prot_info(int send, int freq, const unsigned char *buf,
			       int len);
int sprd_p2p_go_del_station(struct sprd_priv *priv, struct sprd_vif *vif,
				const u8 *mac_addr, u16 reason_code);
int sprd_init_fw(struct sprd_vif *vif);
int sprd_uninit_fw(struct sprd_vif *vif);
struct sprd_priv *sprd_core_create(struct sprd_chip_ops *chip_ops);
void sprd_core_free(struct sprd_priv *priv);
#ifdef DRV_RESET_SELF
void sprd_cancel_reset_work(struct sprd_priv *priv);
#endif
int sprd_cfg80211_change_iface(struct wiphy *wiphy, struct net_device *ndev,
			       enum nl80211_iftype type,
			       struct vif_params *params);

/* MU300: since 7.3 cfg80211 picks the cookie of a remain-on-channel or a management frame itself and hands it to the
 * driver by value (nl80211 assigns it before the call); before, the driver chose one and returned it. cfg80211.c's
 * functions keep their form (ck_t: the cookie argument, as short as the "u64 *" it stands for, so even the debug
 * information's columns stay): on 7.3 the by-value ops hand cfg80211's cookie in through the pointer and MU300_COOKIE
 * keeps it; on older kernels it is the driver's next number, as before. These are here and not in cfg80211.c so that
 * its lines (the BUG table) and its object code stay as they were on the older kernels (FINDINGS 206).
 */
#include <linux/version.h>
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(7, 3, 0))
#define MU300_COOKIE(given, next) ((void)(next), (given))
#define ck_t u64
#define MU300_COOKIE_PTR(c) (&(c))
int sprd_cfg80211_mgmt_tx(struct wiphy *wiphy, struct wireless_dev *wdev,
			  struct cfg80211_mgmt_tx_params *params, u64 *cookie);
static inline int sprd_cfg80211_mgmt_tx_by_value(struct wiphy *wiphy, struct wireless_dev *wdev,
						 struct cfg80211_mgmt_tx_params *params, u64 cookie)
{
	return sprd_cfg80211_mgmt_tx(wiphy, wdev, params, &cookie);
}
#define MU300_MGMT_TX sprd_cfg80211_mgmt_tx_by_value
#else
#define MU300_COOKIE(given, next) (next)
#define ck_t u64 *
#define MU300_COOKIE_PTR(c) (c)
#define MU300_MGMT_TX sprd_cfg80211_mgmt_tx
#endif

#endif
