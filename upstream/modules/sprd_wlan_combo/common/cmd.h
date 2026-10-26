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
#ifndef __CMD_H__
#define __CMD_H__

#include <linux/ieee80211.h>
#include <linux/nl80211.h>
#include "common/cfg80211.h"

#define SPRD_IPV4			1
#define SPRD_IPV6			2
#define SPRD_IPV4_ADDR_LEN		4
#define SPRD_IPV6_ADDR_LEN		16

#define SPRD_VALID_CONFIG		(0x80)
#define SPRD_CMD_EXIT_VAL		(0x8000)
#define CMD_WAIT_TIMEOUT		(3000)
#define CMD_TIMEOUT_DEBUG_LEVEL		(5000)

#define SPRD_STD_11D			BIT(0)
#define SPRD_STD_11E			BIT(1)
#define SPRD_STD_11K			BIT(2)
#define SPRD_STD_11R			BIT(3)
#define SPRD_STD_11U			BIT(4)
#define SPRD_STD_11V			BIT(5)
#define SPRD_STD_11W			BIT(6)

#define SPRD_CAPA_5G			BIT(0)
#define SPRD_CAPA_MCC			BIT(1)
#define SPRD_CAPA_ACL			BIT(2)
#define SPRD_CAPA_AP_SME		BIT(3)
#define SPRD_CAPA_PMK_OKC_OFFLOAD	BIT(4)
#define SPRD_CAPA_11R_ROAM_OFFLOAD	BIT(5)
#define SPRD_CAPA_SCHED_SCAN		BIT(6)
#define SPRD_CAPA_TDLS			BIT(7)
#define SPRD_CAPA_MC_FILTER		BIT(8)
#define SPRD_CAPA_NS_OFFLOAD		BIT(9)
#define SPRD_CAPA_RA_OFFLOAD		BIT(10)
#define SPRD_CAPA_LL_STATS		BIT(11)

#define SPRD_SCREEN_ON_OFF		1
#define SPRD_SET_FCC_CHANNEL		2
#define SPRD_SET_TX_POWER		3
#define SPRD_SET_PS_STATE		4
#define SPRD_SUSPEND_RESUME		5
#define SPRD_FW_PWR_DOWN_ACK		6
#define SPRD_HOST_WAKEUP_FW		7

#define SPRD_ROAM_OFFLOAD_SET_FLAG	1
#define SPRD_ROAM_OFFLOAD_SET_FTIE	2
#define SPRD_ROAM_OFFLOAD_SET_PMK	3
#define SPRD_ROAM_SET_BLACK_LIST	4
#define SPRD_ROAM_SET_WHITE_LIST	5

#define EAP_PACKET_TYPE			(0)
#define EAP_FAILURE_CODE		(4)
#define EAP_WSC_DONE			(5)

#define SPRD_SAE_MAX_NUM		5
#define SPRD_SAE_NOT_SET		-1

enum random_mac_flags {
	SPRD_DISABLE_SCAN_RANDOM_ADDR,
	SPRD_ENABLE_SCAN_RANDOM_ADDR,
	SPRD_CONNECT_RANDOM_ADDR,
};

enum SUBCMD {
	SUBCMD_GET = 1,
	SUBCMD_SET,
	SUBCMD_ADD,
	SUBCMD_DEL,
	SUBCMD_FLUSH,
	SUBCMD_UPDATE,
	SUBCMD_ENABLE,
	SUBCMD_DISABLE,
	SUBCMD_REKEY,
	SUBCMD_MAX
};

enum GSCAN_SUB_COMMAND {
	SPRD_GSCAN_SUBCMD_GET_CAPABILITIES,
	SPRD_GSCAN_SUBCMD_SET_CONFIG,
	SPRD_GSCAN_SUBCMD_SET_SCAN_CONFIG,
	SPRD_GSCAN_SUBCMD_ENABLE_GSCAN,
	SPRD_GSCAN_SUBCMD_GET_SCAN_RESULTS,
	SPRD_GSCAN_SUBCMD_SCAN_RESULTS,
	SPRD_GSCAN_SUBCMD_SET_HOTLIST,
	SPRD_GSCAN_SUBCMD_SET_SIGNIFICANT_CHANGE_CONFIG,
	SPRD_GSCAN_SUBCMD_ENABLE_FULL_SCAN_RESULTS,
	SPRD_GSCAN_SUBCMD_GET_CHANNEL_LIST,
	SPRD_WIFI_SUBCMD_GET_FEATURE_SET,
	SPRD_WIFI_SUBCMD_GET_FEATURE_SET_MATRIX,
	SPRD_WIFI_SUBCMD_SET_PNO_RANDOM_MAC_OUI,
	SPRD_WIFI_SUBCMD_NODFS_SET,
	SPRD_WIFI_SUBCMD_SET_COUNTRY_CODE,
	/* Add more sub commands here */
	SPRD_GSCAN_SUBCMD_SET_EPNO_SSID,
	SPRD_WIFI_SUBCMD_SET_SSID_WHITE_LIST,
	SPRD_WIFI_SUBCMD_SET_ROAM_PARAMS,
	SPRD_WIFI_SUBCMD_ENABLE_LAZY_ROAM,
	SPRD_WIFI_SUBCMD_SET_BSSID_PREF,
	SPRD_WIFI_SUBCMD_SET_BSSID_BLACKLIST,
	SPRD_GSCAN_SUBCMD_ANQPO_CONFIG,
	SPRD_WIFI_SUBCMD_SET_RSSI_MONITOR,
	SPRD_GSCAN_SUBCMD_SET_SSID_HOTLIST,
	SPRD_GSCAN_SUBCMD_RESET_HOTLIST,
	SPRD_GSCAN_SUBCMD_RESET_SIGNIFICANT_CHANGE_CONFIG,
	SPRD_GSCAN_SUBCMD_RESET_SSID_HOTLIST,
	SPRD_WIFI_SUBCMD_RESET_BSSID_BLACKLIST,
	SPRD_GSCAN_SUBCMD_RESET_ANQPO_CONFIG,
	SPRD_GSCAN_SUBCMD_SET_EPNO_FLUSH,
	/* Add more sub commands here */
	SPRD_GSCAN_SUBCMD_MAX
};

/**
 * @SPRD_SET_SAR_RECOVERY: Indicates that need reset sar value.
 * @SPRD_SET_SAR_ABSOLUTE: Indicates that set sar in absolute mode.
 * @SPRD_SET_SAR_RELATIVE: Indicates that set sar in relative mode.
 */
enum sar_subtype {
	SPRD_SET_SAR_RECOVERY = 0,
	SPRD_SET_SAR_ABSOLUTE = 1,
	SPRD_SET_SAR_RELATIVE = 2,
};

struct sprd_priv;
struct sprd_vif;
struct sprd_work;

/* struct rate_info - bitrate information
 *
 * Information about a receiving or transmitting bitrate
 *
 * @flags: bitflag of flags from &enum rate_info_flags
 * @mcs: mcs index if struct describes a 802.11n bitrate
 * @legacy: bitrate in 100kbit/s for 802.11abg
 * @nss: number of streams (VHT only)
 */
struct sprd_rate_info {
	u8 flags;
	u8 mcs;
	u16 legacy;
	u8 nss;
} __packed;

struct sprd_sta_info {
	struct sprd_rate_info tx_rate;
	struct sprd_rate_info rx_rate;
	s8 signal;
	u8 noise;
	__le32 txfailed;
};

struct sprd_eap_hdr {
	u8 version;
	u8 type;
	u16 len;
	u8 code;
	u8 id;
	u16 auth_proc_len;
	u8 auth_proc_type;
	u64 ex_id:24;
	u64 ex_type:32;
	u64 opcode:8;
};

/* wiphy section2 info struct use for get info CMD */
struct wiphy_sec2_t {
	u16 ht_cap_info;
	u16 ampdu_para;
	struct ieee80211_mcs_info ht_mcs_set;
	u32 vht_cap_info;
	struct ieee80211_vht_mcs_info vht_mcs_set;
	u32 antenna_tx;
	u32 antenna_rx;
	u8 retry_short;
	u8 retry_long;
	u16 reserved;
	u32 frag_threshold;
	u32 rts_threshold;
};

struct sprd_cmd {
	u8 cmd_id;
	int init_ok;
	u32 mstime;
	void *data;
	atomic_t refcnt;
	/* spin lock for command */
	spinlock_t lock;
	/* mutex for command */
	struct mutex cmd_lock;
	/* wake_lock for command */
	struct wakeup_source *wake_lock;
	/*complettion for command*/
	struct completion completed;
	atomic_t ignore_resp;
};

struct cmd_connect {
	__le32 wpa_versions;
	u8 bssid[ETH_ALEN];
	u8 channel;
	u8 auth_type;
	u8 pairwise_cipher;
	u8 group_cipher;
	u8 key_mgmt;
	u8 mfp_enable;
	u8 psk_len;
	u8 ssid_len;
	u8 psk[WLAN_MAX_KEY_LEN];
	u8 ssid[IEEE80211_MAX_SSID_LEN];
} __packed;

struct sae_entry {
	bool used;
	u8 peer_addr[ETH_ALEN];
	u8 id_len;
	char identifier[32];
	u8 passwd_len;
	char password[64];
	s32 vlan_id;
} __packed;

struct softap_sae_setting {
	struct sae_entry entry[SPRD_SAE_MAX_NUM];
	int passphrase_len;
	char passphrase[64];
	u32 act;
	int group_count;
	int groups[32];
} __packed;
#endif
