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

#ifndef __CMDEVT_H__
#define __CMDEVT_H__

#include <linux/math64.h>
#include "common/cfg80211.h"
#include "common/cmd.h"
#include "common/delay_work.h"
#include "common/msg.h"
#include "fcc.h"

#define STAP_MODE_NPI 0
#define STAP_MODE_STA 0
#define STAP_MODE_AP 1
#define STAP_MODE_P2P 1
#define STAP_MODE_P2P_DEVICE 2
#define STAP_MODE_COEXI_NUM 4
#define STAP_MODE_OTHER STAP_MODE_STA

#define CMD_SCAN_WAIT_TIMEOUT	(9000)
#define CMD_DISCONNECT_TIMEOUT	(5500)

#define ABORT_SCAN_MODE		(0x1)
#define NORMAL_SCAN_MODE	(0)
#define ABORT_SCAN_MODE		(0x1)

/* Set scan timeout to 9s due to split scan
 * to several period in CP2
 * Framework && wpa_supplicant timeout is 10s
 * so it should be smaller than 10s
 * Please don't change it!!!
 */
#define SPRD_MAX_SDIO_SEND_COUT	1024
#define SPRD_SCHED_SCAN_BUF_END	BIT(0)

#define SPRD_SEND_FLAG_IFRC	BIT(0)
#define SPRD_SEND_FLAG_SSID	BIT(1)
#define SPRD_SEND_FLAG_MSSID	BIT(2)
#define SPRD_SEND_FLAG_IE	BIT(4)

#define SPRD_TDLS_ENABLE_LINK		11
#define SPRD_TDLS_DISABLE_LINK		12
#define SPRD_TDLS_TEARDOWN		3
#define SPRD_TDLS_DISCOVERY_RESPONSE	14
#define SPRD_TDLS_START_CHANNEL_SWITCH	13
#define SPRD_TDLS_CANCEL_CHANNEL_SWITCH	14
#define WLAN_TDLS_CMD_TX_DATA		0x11
#define SPRD_TDLS_UPDATE_PEER_INFOR	15
#define SPRD_TDLS_CMD_CONNECT		16

/* wnm feature */
#define SPRD_11V_BTM                  BIT(0)
#define SPRD_11V_PARP                 BIT(1)
#define SPRD_11V_MIPM                 BIT(2)
#define SPRD_11V_DMS                  BIT(3)
#define SPRD_11V_SLEEP                BIT(4)
#define SPRD_11V_TFS                  BIT(5)
#define SPRD_11V_ALL_FEATURE          0xFFFF

#define SEC1_LEN 24

#define SPRD_CAPA_NAN             BIT(12)
#define SPRD_CAPA_CONFIG_NDO      BIT(13)
#define SPRD_CAPA_D2D_RTT         BIT(14)
#define SPRD_CAPA_D2AP_RTT        BIT(15)
#define SPRD_CAPA_TDLS_OFFCHANNEL BIT(16)
#define SPRD_CAPA_GSCAN			BIT(17)
#define SPRD_CAPA_BATCH_SCAN		BIT(18)
#define SPRD_CAPA_PNO				BIT(19)
#define SPRD_CAPA_EPNO			BIT(20)
#define SPRD_CAPA_RSSI_MONITOR	BIT(21)
#define SPRD_CAPA_SCAN_RAND		BIT(22)
#define SPRD_CAPA_ADDITIONAL_STA	BIT(23)
#define SPRD_CAPA_EPR				BIT(24)
#define SPRD_CAPA_AP_STA			BIT(25)
#define SPRD_CAPA_WIFI_LOGGER		BIT(26)
#define SPRD_CAPA_MKEEP_ALIVE		BIT(27)
#define SPRD_CAPA_TX_POWER		BIT(28)
#define SPRD_CAPA_IE_WHITELIST	BIT(29)

#define TX_WITH_CREDIT	(0)
#define TX_NO_CREDIT	(1)

#define SPRD_EXTEND_FEATURE_SAE          BIT(0)
#define SPRD_EXTEND_FEATURE_OWE          BIT(1)
#define SPRD_EXTEND_FEATURE_DPP          BIT(2)
#define SPRD_EXTEND_8021X_SUITE_B_192    BIT(3)
#define SPRD_EXTEND_FEATURE_OCE	   BIT(4)
#define SPRD_EXTEND_FEATURE_LLSTATE	   BIT(5)
#define SPRD_EXTEND_SOATAP_WPA3	   BIT(6)

#define SPRD_SET_SAR	0x10

#define	SPRD_IE_BEACON		0
#define	SPRD_IE_PROBE_REQ		1
#define	SPRD_IE_PROBE_RESP		2
#define	SPRD_IE_ASSOC_REQ		3
#define	SPRD_IE_ASSOC_RESP		4
#define	SPRD_IE_BEACON_HEAD		5
#define	SPRD_IE_BEACON_TAIL		6
#define	SPRD_IE_SAE			7

#define SCAN_RANDOM_MAC_ADDR	BIT(29)

#define SPRD_FRAME_NORMAL		1
#define	SPRD_FRAME_DEAUTH		2
#define	SPRD_FRAME_DISASSOC		3
#define	SPRD_FRAME_SCAN		4
#define SPRD_FRAME_ROAMING		5

#define	SPRD_SCAN_DONE		1
#define	SPRD_SCHED_SCAN_DONE		2
#define SPRD_SCAN_ERROR		3
#define SPRD_GSCAN_DONE		4
#define SPRD_SCAN_ABORT_DONE  5

#define	SPRD_CQM_RSSI_LOW	1
#define	SPRD_CQM_RSSI_HIGH	2
#define	SPRD_CQM_BEACON_LOSS	3

#define SPRD_ADDBA_REQ_CMD 0
#define SPRD_ADDBA_RSP_CMD 1
#define SPRD_DELBA_CMD 2
#define SPRD_DELBA_ALL_CMD 5

#define SPRD_ADDBA_REQ_EVENT 0
#define SPRD_ADDBA_RSP_EVENT 1
#define SPRD_DELBA_EVENT 2
#define SPRD_BAR_EVENT 3
#define SPRD_FILTER_EVENT 4
#define SPRD_DELBA_ALL_EVENT 5
#define SPRD_DELTXBA_EVENT 6

#define SCAN_ERROR 0
#define RSP_CNT_ERROR 1
#define HANDLE_FLAG_ERROR 2
#define CMD_RSP_TIMEOUT_ERROR 3
#define LOAD_INI_DATA_FAILED 4
#define DOWNLOAD_INI_DATA_FAILED 5

#define HANG_RECOVERY_BEGIN 0
#define HANG_RECOVERY_END 1

#define THERMAL_TX_RESUME 0
#define THERMAL_TX_STOP 1
#define THERMAL_WIFI_DOWN 2

#define BT_ON 1
#define BT_OFF 0

/* TLV rbuf size */
#define GET_INFO_TLV_RBUF_SIZE	300

/* TLV type list */
#define GET_INFO_TLV_TP_OTT	1
#define NOTIFY_AP_VERSION	2

#define NOTIFY_AP_VERSION_USER 0
#define NOTIFY_AP_VERSION_USER_DEBUG 1

#define		SPRD_SAE_PASSPHRASE		1
#define		SPRD_SAE_PASSWORD_ENTRY	2

#define CMD_SNIFFER_MODE		"SNIFFER_MODE"
#define CMD_SNIFFER_LISTEN_CHANNEL	"LISTEN_CHANNEL"
#define CMD_SNIFFER_FILTER		"FILTER"
#define CMD_SNIFFER_BAND		"BAND"

enum CMD_LIST {
	CMD_MIN = 0,
	CMD_ERR = CMD_MIN,
	/* All Interface */
	CMD_GET_INFO = 1,
	CMD_SET_REGDOM,
	CMD_OPEN,
	CMD_CLOSE,
	CMD_POWER_SAVE,
	CMD_SET_PARAM,
	CMD_SET_CHANNEL,
	CMD_REQ_LTE_CONCUR,
	CMD_SYNC_VERSION = 9,
	/* Connect */
	CMD_CONNECT = 10,

	/* Station */
	CMD_SCAN = 11,
	CMD_SCHED_SCAN,
	CMD_DISCONNECT,
	CMD_KEY,
	CMD_SET_PMKSA,
	CMD_GET_STATION,

	/* SoftAP */
	CMD_START_AP = 17,
	CMD_DEL_STATION,
	CMD_SET_BLACKLIST,
	CMD_SET_WHITELIST,

	/* P2P */
	CMD_TX_MGMT = 21,
	CMD_REGISTER_FRAME,
	CMD_REMAIN_CHAN,
	CMD_CANCEL_REMAIN_CHAN,

	/* Public/New Feature */
	CMD_SET_IE = 25,
	CMD_NOTIFY_IP_ACQUIRED,
	/* Roaming */
	CMD_SET_CQM,		/* Uplayer Roaming */
	CMD_SET_ROAM_OFFLOAD,	/* fw Roaming */
	CMD_SET_MEASUREMENT,
	CMD_SET_QOS_MAP,
	CMD_TDLS,
	CMD_11V,

	/* NPI/DEBUG/OTHER */
	CMD_NPI_MSG = 33,
	CMD_NPI_GET,

	CMD_ASSERT,
	CMD_FLUSH_SDIO,

	/* WMM Admisson Control */
	CMD_ADD_TX_TS = 37,
	CMD_DEL_TX_TS = 38,

	/* Multicast filter */
	CMD_MULTICAST_FILTER,

	CMD_ADDBA_REQ = 40,
	CMD_DELBA_REQ,

	CMD_LLSTAT = 56,

	CMD_CHANGE_BSS_IBSS_MODE = 57,

	/* IBSS */
	CMD_IBSS_JOIN = 58,
	CMD_SET_IBSS_ATTR,
	CMD_IBSS_LEAVE,
	CMD_IBSS_VSIE_SET,
	CMD_IBSS_VSIE_DELETE,
	CMD_IBSS_SET_PS,
	CMD_RND_MAC = 64,
	/* gscan */
	CMD_GSCAN = 65,

	CMD_RTT = 66,
	/* NAN */
	CMD_NAN = 67,

	/* BA */
	CMD_BA = 68,

	CMD_SET_PROTECT_MODE = 69,
	CMD_GET_PROTECT_MODE,

	CMD_SET_MAX_CLIENTS_ALLOWED,
	CMD_TX_DATA = 72,
	CMD_NAN_DATA_PATH = 73,
	CMD_SET_TLV = 74,
	CMD_RSSI_MONITOR = 75,
	CMD_DOWNLOAD_INI = 76,
	CMD_RADAR_DETECT = 77,
	CMD_HANG_RECEIVED = 78,
	CMD_RESET_BEACON = 79,
	CMD_VOWIFI_DATA_PROTECT = 80,
	/*Please add new command above line,
	 * conditional compile flag is not recommended
	 */
	CMD_SET_MIRACAST = 82,
	CMD_PACKET_OFFLOAD = 84,
	CMD_SET_SAE_PARAM = 85,
	CMD_RESERVED_FOR_PAM_WIFI = 86,
	CMD_SET_SNIFFER = 87,
	CMD_RESVERED_FOR_FILTER = 88,
	CMD_EXTENDED_LLSTAT = 89,
	CMD_MAX
};

enum sar_mode {
	SPRD_SET_SAR_2G_11B = 0,
	SPRD_SET_SAR_2G_11G = 1,
	SPRD_SET_SAR_2G_11N = 2,
	SPRD_SET_SAR_2G_11AC = 3,
	SPRD_SET_SAR_5G_11A = 4,
	SPRD_SET_SAR_5G_11N = 5,
	SPRD_SET_SAR_5G_11AC = 6,
	SPRD_SET_SAR_ALL_MODE = 7,
};

enum sniffer_para_type {
	SPRD_SNIFFER_ENABLE = 0,
	SPRD_SNIFFER_FILTER = 1,
	SPRD_SNIFFER_BAND = 2,
};

enum sniffer_filter_value {
	SPRD_SNIFFER_ALL = 0,
	SPRD_SNIFFER_MGMT = 1,
	SPRD_SNIFFER_BC_DATA = 2,
	SPRD_SNIFFER_UC_DATA = 3,
	SPRD_SNIFFER_ALL_DATA = 4,
};

enum sniffer_band {
	SPRD_SNIFFER_BW_20M = 0,
	SPRD_SNIFFER_BW_40M = 1,
	SPRD_SNIFFER_BW_80M = 2,
	SPRD_SNIFFER_BW_160M = 3,
	SPRD_SNIFFER_BW_80P80 = 4,
};

/*CMD SYNC_VERSION struct*/
struct cmd_api_t {
	u32 main_ver;
	u8 api_map[256];
};

/* CMD_GET_INFO
 * @SPRD_STD_11D:  The fw supports regulatory domain.
 * @SPRD_STD_11E:  The fw supports WMM/WMM-AC/WMM-PS.
 * @SPRD_STD_11K:  The fw supports Radio Resource Measurement.
 * @SPRD_STD_11R:  The fw supports FT roaming.
 * @SPRD_STD_11U:  The fw supports Interworking Network.
 * @SPRD_STD_11V:  The fw supports Wireless Network Management.
 * @SPRD_STD_11W:  The fw supports Protected Management Frame.
 *
 * @SPRD_CAPA_5G:  The fw supports dual band (2.4G/5G).
 * @SPRD_CAPA_MCC:  The fw supports Multi Channel Concurrency.
 * @SPRD_CAPA_ACL:  The fw supports ACL.
 * @SPRD_CAPA_AP_SME:  The fw integrates AP SME.
 * @SPRD_CAPA_PMK_OKC_OFFLOAD:  The fw supports PMK/OKC roaming offload.
 * @SPRD_CAPA_11R_ROAM_OFFLOAD:  The fw supports FT roaming offload.
 * @SPRD_CAPA_SCHED_SCAN:  The fw supports scheduled scans.
 * @SPRD_CAPA_TDLS:  The fw supports TDLS (802.11z) operation.
 * @SPRD_CAPA_MC_FILTER:  The fw supports multicast filter operation.
 * @SPRD_CAPA_NS_OFFLOAD:  The fw supports ipv6 NS operation.
 * @SPRD_CAPA_RA_OFFLOAD:  The fw supports ipv6 RA offload.
 * @SPRD_CAPA_LL_STATS:  The fw supports link layer stats.
 */

struct cmd_fw_info {
	__le32 chip_model;
	__le32 chip_version;
	__le32 fw_version;
	__le32 fw_std;
	__le32 fw_capa;
	u8 max_ap_assoc_sta;
	u8 max_acl_mac_addrs;
	u8 max_mc_mac_addrs;
	u8 wnm_ft_support;
	struct wiphy_sec2_t wiphy_sec2;
	u8 mac_addr[ETH_ALEN];
	/* with credit or without credit */
	unsigned char credit_capa;
	__le32 extend_feature;
};

/* CMD_OPEN */
struct cmd_open {
	u8 mode;
	u8 reserved;
	u8 mac[ETH_ALEN];
} __packed;

/* CMD_CLOSE */
struct cmd_close {
	u8 mode;
} __packed;

struct cmd_power_save {
	u8 sub_type;
	u8 value;
} __packed;

/**
 * cmd_set_sar: this struct used to describe set sar parameters.
 *
 * @power_save_type:power save command type, we send sar para through
 *  power save command,so need provide power_save_sub_type,in this case
 *  this value always set to SPRD_SET_SAR, other sub type please ref
 *  cmd_power_save struct.
 * @sub_type: Please refer sar_subtype struct.
 * @value: The value we set.
 * @mode: 802.11mode, please refer sar_mode struct.
 */
struct cmd_set_sar {
	u8 power_save_type;
	u8 sub_type;
	s8 value;
	u8 mode;
} __packed;

struct cmd_set_power_backoff {
#define SPRD_SET_POWER_BACKOFF	0x11
	u8 power_save_type;
	struct sprd_power_backoff backoff;
} __packed;

struct cmd_vowifi {
	u8 value;
} __packed;

struct cmd_add_key {
	u8 key_index;
	u8 pairwise;
	u8 mac[ETH_ALEN];
	u8 keyseq[16];
	u8 cypher_type;
	u8 key_len;
	u8 value[0];
} __packed;

struct cmd_del_key {
	u8 key_index;
	u8 pairwise;		/* pairwise or group */
	u8 mac[ETH_ALEN];
} __packed;

struct cmd_set_def_key {
	u8 key_index;
} __packed;

struct cmd_set_rekey {
	u8 kek[NL80211_KEK_LEN];
	u8 kck[NL80211_KCK_LEN];
	u8 replay_ctr[NL80211_REPLAY_CTR_LEN];
} __packed;

/* CMD_SET_IE */
struct cmd_set_ie {
	u8 type;
	__le16 len;
	u8 data[0];
} __packed;

/* CMD_START_AP */
struct cmd_start_ap {
	__le16 len;
	u8 value[0];
} __packed;

/* CMD_DEL_STATION */
struct cmd_del_station {
	u8 mac[ETH_ALEN];
	__le16 reason_code;
} __packed;

/* CMD_GET_STATION */
struct cmd_get_station {
	struct sprd_rate_info tx_rate;
	s8 signal;
	u8 noise;
	u8 reserved;
	__le32 txfailed;
	struct sprd_rate_info rx_rate;
} __packed;

/* CMD_SET_CHANNEL */
struct cmd_set_channel {
	u8 channel;
} __packed;

struct cmd_5g_chn {
	u16 n_5g_chn;
	u16 chns[0];
};

/* CMD_SCAN */
struct cmd_scan {
	__le32 channels;	/* One bit for one channel */
	__le32 reserved;
	u16 ssid_len;
	u8 ssid[0];
} __packed;

/* CMD_SCHED_SCAN */
struct cmd_sched_scan_hd {
	u16 started;
	u16 buf_flags;
} __packed;

struct cmd_sched_scan_ie_hd {
	u16 ie_flag;
	u16 ie_len;
} __packed;

struct cmd_sched_scan_ifrc {
	u32 interval;
	u32 flags;
	s32 rssi_thold;
	u8 chan[SPRD_TOTAL_CHAN_NR];
} __packed;

/* CMD_DISCONNECT */
struct cmd_disconnect {
	__le16 reason_code;
} __packed;

/* CMD_SET_PARAM */
struct cmd_set_param {
	__le32 rts;
	__le32 frag;
} __packed;

struct cmd_pmkid {
	u8 bssid[ETH_ALEN];
	u8 pmkid[WLAN_PMKID_LEN];
} __packed;

struct cmd_dscp_exception {
	u8 dscp;
	u8 up;
} __packed;

struct cmd_dscp_range {
	u8 low;
	u8 high;
} __packed;

struct cmd_qos_map {
	u8 num_des;
	struct cmd_dscp_exception dscp_exception[21];
	struct cmd_dscp_range up[8];
} __packed;

struct cmd_tx_ts {
	u8 tsid;
	u8 peer[ETH_ALEN];
	u8 user_prio;
	__le16 admitted_time;
} __packed;

/* CMD_REMAIN_CHAN */
struct cmd_remain_chan {
	u8 chan;
	u8 chan_type;
	__le32 duraion;
	__le64 cookie;
} __packed;

/* CMD_CANCEL_REMAIN_CHAN */
struct cmd_cancel_remain_chan {
	__le64 cookie;		/* cookie */
} __packed;

/* CMD_TX_MGMT */
struct cmd_mgmt_tx {
	u8 chan;		/* send channel */
	u8 dont_wait_for_ack;	/*don't wait for ack */
	__le32 wait;		/* wait time */
	__le64 cookie;		/* cookie */
	__le16 len;		/* mac length */
	u8 value[0];		/* mac */
} __packed;

/* CMD_REGISTER_FRAME */
struct cmd_register_frame {
	__le16 type;
	u8 reg;
} __packed;

/* CMD_SET_CQM */
struct cmd_cqm_rssi {
	__le32 rssih;
	__le32 rssil;
} __packed;

struct cmd_roam_offload_data {
	u8 type;
	u8 len;
	u8 value[0];
} __packed;

struct cmd_tdls {
	u8 tdls_sub_cmd_mgmt;
	u8 da[ETH_ALEN];
	u8 initiator;
	u8 rsvd;
	u8 paylen;
	u8 payload[0];
} __packed;

struct cmd_blacklist {
	u8 sub_type;
	u8 num;
	u8 mac[0];
} __packed;

struct cmd_tdls_channel_switch {
	u8 primary_chan;
	u8 second_chan_offset;
	u8 band;
} __packed;

struct cmd_set_mac_addr {
	u8 sub_type;
	u8 num;
	u8 mac[0];
} __packed;

struct cmd_rsp_state_code {
	__le32 code;
} __packed;

struct cmd_miracast {
	u8 value;
} __packed;

/* 11v cmd struct */
struct cmd_11v {
	u16 cmd;
	u16 len;
	union {
		u32 value;
		u8 buf[0];
	};
} __packed;

struct evt_suspend_resume {
	u32 status;
} __packed;

enum EVT_LIST {
	EVT_MIN = 0x80,
	/* Station/P2P */
	EVT_CONNECT = EVT_MIN,
	EVT_DISCONNECT,
	EVT_SCAN_DONE,
	EVT_MGMT_FRAME,
	EVT_MGMT_TX_STATUS,
	EVT_REMAIN_CHAN_EXPIRED,
	EVT_MIC_FAIL,
	EVT_GSCAN_FRAME = 0X88,
	EVT_RSSI_MONITOR = 0x89,
	EVT_COEX_BT_ON_OFF = 0x90,

	/* SoftAP */
	EVT_NEW_STATION = 0xA0,
	EVT_RADAR_DETECTED = 0xA1,
	EVT_FRESH_POWER_BO = 0xA2,

	/* New Feature */
	/* Uplayer Roaming */
	EVT_CQM = 0xB0,
	EVT_MEASUREMENT,
	EVT_TDLS,
	EVT_SDIO_FLOWCON = 0xB3,

	EVT_REPORT_IP_ADDR = 0xc0,
	/* DEBUG/OTHER */
	EVT_SDIO_SEQ_NUM = 0xE0,

	EVT_BA = 0xf3,
	/* RTT */
	EVT_RTT = 0xf2,

	/* NAN */
	EVT_NAN = 0xf4,
	EVT_STA_LUT_INDEX = 0xf5,
	EVT_HANG_RECOVERY = 0xf6,
	EVT_THERMAL_WARN = 0xf7,
	EVT_SUSPEND_RESUME = 0xf8,
	EVT_WFD_MIB_CNT = 0xf9,
	EVT_FW_PWR_DOWN = 0xfa,
	EVT_CHAN_CHANGED = 0xfb,
	EVT_ACS_DONE = 0xfc,
	EVT_ACS_LTE_CONFLICT_EVENT = 0xfd,
	EVT_MAX
};

/* EVT_DISCONNECT */
struct evt_disconnect {
	u16 reason_code;
} __packed;

/* EVT_MGMT_FRAME */
struct evt_mgmt_frame {
	u8 type;
	u8 channel;
	s8 signal;		/* signal should be signed */
	u8 reserved;
	u8 bssid[ETH_ALEN];	/* roaming frame */
	__le16 len;
	u8 data[0];
} __packed;

/* EVT_SCAN_COMP */
struct evt_scan_done {
	u8 type;
} __packed;

/* EVT_GSCAN_COMP */
struct evt_gscan_done {
	struct evt_scan_done evt;
	u8 bucket_id;
} __packed;

/* EVT_MLME_TX_STATUS */
struct evt_mgmt_tx_status {
	__le64 cookie;		/* cookie */
	u8 ack;			/* status */
	__le16 len;		/* frame len */
	u8 buf[0];		/* mgmt frame */
} __packed;

/* EVT_NEW_STATION  */
struct evt_new_station {
	u8 is_connect;
	u8 mac[ETH_ALEN];
	__le16 ie_len;
	u8 ie[0];
} __packed;

/* EVT_MIC_FAIL */
struct evt_mic_failure {
	u8 key_id;
	u8 is_mcast;
} __packed;

/* EVT_CQM  */
struct evt_cqm {
	u8 status;
} __packed;

struct evt_tdls {
	u8 tdls_sub_cmd_mgmt;
	u8 mac[ETH_ALEN];
	u8 payload_len;
	u8 rcpi;
} __packed;

struct cmd_gscan_header {
	u16 subcmd;
	u16 data_len;
	u8 data[0];
} __packed;

struct llc_hdr {
	u8 dsap;
	u8 ssap;
	u8 cntl;
	u8 org_code[3];
	__be16 eth_type;
} __packed;

struct cmd_ba {
	unsigned char type;
	unsigned char tid;
	unsigned char da[6];
	unsigned char success;
} __packed;

struct win_param {
	unsigned short win_start;
	unsigned short win_size;
} __packed;

struct msdu_param {
	unsigned short seq_num;
} __packed;

struct evt_ba {
	unsigned char type;
	unsigned char tid;
	unsigned char sta_lut_index;
	unsigned char reserved;
	union {
		struct win_param win_param;
		struct msdu_param msdu_param;
	} __packed;
} __packed;

struct evt_sta_lut_ind {
	u8 ctx_id;
	u8 action;
	u8 sta_lut_index;
	u8 ra[ETH_ALEN];
	u8 is_ht_enable;
	u8 is_vht_enable;
} __packed;

struct chan_changed_info {
	u8 initiator;
	u8 target_channel;
} __packed;

struct cmd_set_assert {
	u8 reason;
} __packed;

struct evt_hang_recovery {
	u32 action;
} __packed;

struct evt_thermal_warn {
	u32 action;
} __packed;

struct vdev_tx_stats {
	u32 tx_mpdu_cnt[4];
	u32 tx_mpdu_suc_cnt[4];
	u32 tx_mpdu_lost_cnt[4];
	u32 tx_retries[4];
	u32 tx_mpdu_len;
	u32 tx_tp_in_mbps;
} __packed;

struct vdev_rx_stats {
	u32 rx_mpdu_cnt[4];
	u32 rx_retry_cnt[4];
	u32 rx_mpdu_len;
	u32 rx_tp_in_mbps;
} __packed;

struct evt_wfd_mib_cnt {
	u32 wfd_throughput;
	u32 sum_tx_throughput;
	u32 tx_mpdu_lost_cnt[4];
	u32 ctxt_id;
	u32 tx_frame_cnt;
	u32 rx_mine_cycle_cnt;
	u32 rx_clear_cnt;
	u32 mib_cycle_cnt;
	struct vdev_tx_stats tx_stats;
	struct vdev_rx_stats rx_stats;
} __packed;

struct evt_coex_mode_changed {
	u8 action;
} __packed;

struct sprd_priv;

/* TLV info */
struct tlv_data {
	u16 type;
	u16 len;
	u8 data[0];
} __packed;

struct ap_version_tlv_elmt {
	struct tlv_data hdr;
	u8 ap_version;
} __packed;

/* IOCTL command=SPRDWLSETTLV */
/* type list */
enum IOCTL_TLV_TYPE_LIST {
	IOCTL_TLV_TP_VOWIFI_INFO = 6,
	IOCTL_TLV_TP_ADD_VOWIFI_PAIR = 7,
	IOCTL_TLV_TP_DEL_VOWIFI_PAIR = 8,
	IOCTL_TLV_TP_FLUSH_VOWIFI_PAIR = 9
};

/* structure */
/* tlv type = 6 */
struct vowifi_info {
	u8 data;		/* vowifi status: 0:disable,1:enable */
	u8 call_type;		/* vowifi type: 0:video,1:voice */
};

/* recv acs result from firmware */
struct acs_result {
	u8 ch;
	s8 noise;
	u32 time;
	u32 time_busy;
	u32 time_ext_busy;
};

/*recv ip addr from cp2*/
struct ip_addr_info {
	u16 type;
	u8 ip_addr[16];
};

/* packet offload struct */
struct cmd_packet_offload {
	u32 req_id;
	u8 enable;
	u32 period;
	u16 len;
	u8 data[0];
} __packed;

struct sae_param {
	u16 request_type;
	u8 data[0];
} __packed;

/* externed link layer status struct */
enum SPRD_EXTERN_LLSTAT_SUBTYPE {
	SPRD_SUBTYPE_CHANNEL_INFO = 1,
};

struct cmd_extended_llstate {
	u8 type;
	u8 subtype;
	u16 len;
	u8 data[0];
} __packed;

struct cmd_sniffer_para {
	u8 type;
	u8 value;
} __packed;

const char *sc2355_cmdevt_cmd2str(u8 cmd);
struct sprd_vif *sc2355_ctxid_to_vif(struct sprd_priv *priv, u8 vif_ctx_id);
void sc2355_tdls_count_flow(struct sprd_vif *vif, u8 *data, u16 len);

unsigned short sc2355_rx_rsp_process(struct sprd_priv *priv, u8 *msg);
unsigned short sc2355_rx_evt_process(struct sprd_priv *priv, u8 *msg);
/*driver & fw API sync function start*/
void sc2355_api_version_fill_drv(struct sprd_priv *priv,
				 struct cmd_api_t *drv_api);
void sc2355_api_version_fill_fw(struct sprd_priv *priv,
				struct cmd_api_t *fw_api);
int sc2355_api_version_available_check(struct sprd_priv *priv,
				       struct sprd_msg *msg);
int sc2355_api_version_need_compat_operation(struct sprd_priv *priv, u8 cmd_id);
/*driver & fw API sync function end*/

int sc2355_cmd_scan(struct sprd_priv *priv, struct sprd_vif *vif, u32 channels,
		    int ssid_len, const u8 *ssid_list, u16 chn_count_5g,
		    const u16 *chns_5g);
int sc2355_cmd_sched_scan_start(struct sprd_priv *priv, struct sprd_vif *vif,
				struct sprd_sched_scan *buf);
int sc2355_cmd_sched_scan_stop(struct sprd_priv *priv, struct sprd_vif *vif);

int sc2355_set_gscan_config(struct sprd_priv *priv, struct sprd_vif *vif,
			    void *data, u16 len, u8 *r_buf, u16 *r_len);
int sc2355_set_gscan_scan_config(struct sprd_priv *priv, struct sprd_vif *vif,
				 void *data, u16 len, u8 *r_buf, u16 *r_len);
int sc2355_enable_gscan(struct sprd_priv *priv, struct sprd_vif *vif,
			void *data, u8 *r_buf, u16 *r_len);
int sprd_xmit_data2cmd_wq(struct sk_buff *skb, struct net_device *ndev);
void sc2355_vowifi_data_protection(struct sprd_vif *vif);
int sc2355_get_gscan_capabilities(struct sprd_priv *priv, struct sprd_vif *vif,
				  u8 *r_buf, u16 *r_len);
void sc2355_evt_frame(struct sprd_vif *vif, u8 *data, u16 len, int flag);
int sc2355_set_gscan_bssid_hotlist(struct sprd_priv *priv, struct sprd_vif *vif,
				   void *data, u16 len, u8 *r_buf, u16 *r_len);
int sc2355_gscan_subcmd(struct sprd_priv *priv, struct sprd_vif *vif,
			void *data, u16 subcmd, u16 len, u8 *r_buf, u16 *r_len);
int sc2355_cmd_host_wakeup_fw(struct sprd_priv *priv, struct sprd_vif *vif);
void sc2355_work_host_wakeup_fw(struct sprd_vif *vif);
int sc2355_externed_llstate(struct sprd_priv *priv, struct sprd_vif *vif,
			    u8 type, u8 subtype, void *buf, u8 len,
			    u8 *r_buf, u16 *r_len);
int sc2355_set_packet_offload(struct sprd_priv *priv, struct sprd_vif *vif,
			      u32 req, u8 enable, u32 interval, u32 len,
			      u8 *data);

void sc2355_report_gscan_frame_evt(struct sprd_vif *vif, u8 *data, u16 len);
int sc2355_gscan_done(struct sprd_vif *vif, u8 bucket_id);
void sc2355_evt_rssi_monitor(struct sprd_vif *vif, u8 *data, u16 len);
int sc2355_report_acs_lte_event(struct sprd_vif *vif);

int sc2355_assert_cmd(struct sprd_priv *priv, struct sprd_vif *vif, u8 cmd_id,
		      u8 reason);

struct sprd_msg *sc2355_get_cmdbuf(struct sprd_priv *priv, struct sprd_vif *vif,
				   u16 len, u8 cmd_id, enum sprd_head_rsp rsp,
				   gfp_t flags);
static inline struct sprd_msg *get_cmdbuf(struct sprd_priv *priv,
					  struct sprd_vif *vif, u16 len,
					  u8 cmd_id)
{
	return sc2355_get_cmdbuf(priv, vif, len, cmd_id, SPRD_HEAD_RSP,
				 GFP_KERNEL);
}

static inline struct sprd_msg *get_databuf(struct sprd_priv *priv,
					   struct sprd_vif *vif, u16 len,
					   u8 cmd_id)
{
	return sc2355_get_cmdbuf(priv, vif, len, cmd_id, SPRD_HEAD_RSP,
				 GFP_ATOMIC);
}

int sc2355_send_cmd_recv_rsp(struct sprd_priv *priv, struct sprd_msg *msg,
			     u8 *rbuf, u16 *rlen, unsigned int timeout);
static inline int send_cmd_recv_rsp(struct sprd_priv *priv,
				    struct sprd_msg *msg, u8 *rbuf, u16 *rlen)
{
	return sc2355_send_cmd_recv_rsp(priv, msg, rbuf, rlen,
					CMD_WAIT_TIMEOUT);
}

int sprd_send_data2cmd(struct sprd_priv *priv, struct sprd_vif *vif, void *data,
		       u16 len);

void mdbg_assert_interface(char *str);
int sc2355_cmd_abort_scan(struct sprd_priv *priv, struct sprd_vif *vif);
void sc2355_clean_scan(struct sprd_vif *vif);
void sc2355_random_mac_addr(u8 *addr);

int sc2355_set_regdom(struct sprd_priv *priv, u8 *regdom, u32 len);

void sc2355_setup_wiphy(struct wiphy *wiphy, struct sprd_priv *priv);
void sc2355_cmd_init(struct sprd_cmd *cmd);
void sc2355_cmd_deinit(struct sprd_cmd *cmd);
int sc2355_sync_version(struct sprd_priv *priv);
void sc2355_download_hw_param(struct sprd_priv *priv);

int sc2355_get_fw_info(struct sprd_priv *priv);
int sc2355_open_fw(struct sprd_priv *priv, struct sprd_vif *vif, u8 *mac_addr);
int sc2355_close_fw(struct sprd_priv *priv, struct sprd_vif *vif);
int sc2355_power_save(struct sprd_priv *priv, struct sprd_vif *vif,
		      u8 sub_type, u8 status);
int sc2355_set_sar(struct sprd_priv *priv, struct sprd_vif *vif,
		   u8 sub_type, s8 value);
int sc2355_set_power_backoff(struct sprd_priv *priv, struct sprd_vif *vif,
			     struct sprd_power_backoff *data);
int sc2355_add_key(struct sprd_priv *priv, struct sprd_vif *vif,
		   const u8 *key_data, u8 key_len, bool pairwise, u8 key_index,
		   const u8 *key_seq, u8 cypher_type, const u8 *mac_addr);
int sc2355_enable_miracast(struct sprd_priv *priv,
			   struct sprd_vif *vif, int val);
int sc2355_del_key(struct sprd_priv *priv, struct sprd_vif *vif, u8 key_index,
		   bool pairwise, const u8 *mac_addr);
int sc2355_set_def_key(struct sprd_priv *priv, struct sprd_vif *vif,
		       u8 key_index);
int sc2355_set_rekey_data(struct sprd_priv *priv, struct sprd_vif *vif,
			  struct cfg80211_gtk_rekey_data *data);
int sc2355_set_beacon_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			 const u8 *ie, u16 len);
int sc2355_set_probereq_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			   const u8 *ie, u16 len);
int sc2355_set_proberesp_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			    const u8 *ie, u16 len);
int sc2355_set_assocreq_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			   const u8 *ie, u16 len);
int sc2355_set_assocresp_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			    const u8 *ie, u16 len);
int sc2355_set_sae_ie(struct sprd_priv *priv, struct sprd_vif *vif,
		      const u8 *ie, u16 len);
int sc2355_start_ap(struct sprd_priv *priv, struct sprd_vif *vif, u8 *beacon,
		    u16 len, struct cfg80211_ap_settings *settings);
int sc2355_del_station(struct sprd_priv *priv, struct sprd_vif *vif,
		       const u8 *mac_addr, u16 reason_code);
int sc2355_get_station(struct sprd_priv *priv, struct sprd_vif *vif,
		       struct sprd_sta_info *sta);
int sc2355_set_channel(struct sprd_priv *priv, struct sprd_vif *vif,
		       u8 channel);
int sc2355_connect(struct sprd_priv *priv, struct sprd_vif *vif,
		   struct cmd_connect *p);
int sc2355_disconnect(struct sprd_priv *priv, struct sprd_vif *vif,
		      u16 reason_code);
int sc2355_set_param(struct sprd_priv *priv, u32 rts, u32 frag);
int sc2355_pmksa(struct sprd_priv *priv, struct sprd_vif *vif, const u8 *bssid,
		 const u8 *pmkid, u8 type);
int sc2355_set_qos_map(struct sprd_priv *priv, struct sprd_vif *vif, void *map);
int sc2355_add_tx_ts(struct sprd_priv *priv, struct sprd_vif *vif, u8 tsid,
		     const u8 *peer, u8 user_prio, u16 admitted_time);
int sc2355_del_tx_ts(struct sprd_priv *priv, struct sprd_vif *vif, u8 tsid,
		     const u8 *peer);
int sc2355_remain_chan(struct sprd_priv *priv, struct sprd_vif *vif,
		       struct ieee80211_channel *channel,
		       enum nl80211_channel_type channel_type,
		       u32 duration, u64 *cookie);
int sc2355_cancel_remain_chan(struct sprd_priv *priv, struct sprd_vif *vif,
			      u64 cookie);
int sc2355_tx_mgmt(struct sprd_priv *priv, struct sprd_vif *vif, u8 channel,
		   u8 dont_wait_for_ack, u32 wait, u64 *cookie,
		   const u8 *buf, size_t len);
int sc2355_register_frame(struct sprd_priv *priv, struct sprd_vif *vif,
			  u16 type, u8 reg);
int sc2355_set_cqm_rssi(struct sprd_priv *priv, struct sprd_vif *vif,
			s32 rssi_thold, u32 rssi_hyst);
int sc2355_set_roam_offload(struct sprd_priv *priv, struct sprd_vif *vif,
			    u8 sub_type, const u8 *data, u8 len);
int sc2355_tdls_mgmt(struct sprd_vif *vif, struct sk_buff *skb);
int sc2355_tdls_oper(struct sprd_priv *priv, struct sprd_vif *vif,
		     const u8 *peer, int oper);
int sc2355_start_tdls_channel_switch(struct sprd_priv *priv,
				     struct sprd_vif *vif, const u8 *peer_mac,
				     u8 primary_chan, u8 second_chan_offset,
				     u8 band);
int sc2355_cancel_tdls_channel_switch(struct sprd_priv *priv,
				      struct sprd_vif *vif, const u8 *peer_mac);
int sc2355_notify_ip(struct sprd_priv *priv, struct sprd_vif *vif, u8 ip_type,
		     u8 *ip_addr);
int sc2355_set_blacklist(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 sub_type, u8 num, u8 *mac_addr);
int sc2355_set_whitelist(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 sub_type, u8 num, u8 *mac_addr);
int sc2355_set_mc_filter(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 sub_type, u8 num, u8 *mac_addr);
int sc2355_npi_send_recv(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 *s_buf, u16 s_len, u8 *r_buf, u16 *r_len);
int sc2355_set_11v_feature_support(struct sprd_priv *priv,
				   struct sprd_vif *vif, u16 val);
int sc2355_set_11v_sleep_mode(struct sprd_priv *priv, struct sprd_vif *vif,
			      u8 status, u16 interval);
int sc2355_set_max_clients_allowed(struct sprd_priv *priv,
				   struct sprd_vif *vif, int n_clients);
int sc2355_set_random_mac(struct sprd_priv *priv, struct sprd_vif *vif,
			  u8 random_mac_flag, u8 *addr);
int sc2355_send_tdls_cmd(struct sprd_vif *vif, const u8 *peer, int oper);
bool sc2355_do_delay_work(struct sprd_work *work);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sc2355_set_vowifi(struct net_device *ndev, void __user *data);
int sc2355_set_miracast(struct net_device *ndev, void __user *data);
#else
int sc2355_set_vowifi(struct net_device *ndev, struct ifreq *ifr);
int sc2355_set_miracast(struct net_device *ndev, struct ifreq *ifr);
#endif
void sc2355_scan_timeout(struct timer_list *t);
int sc2355_scan(struct wiphy *wiphy,
		struct cfg80211_scan_request *request);
int sc2355_sched_scan_start(struct wiphy *wiphy, struct net_device *ndev,
			    struct cfg80211_sched_scan_request *request);
int sc2355_sched_scan_stop(struct wiphy *wiphy, struct net_device *ndev,
			   u64 reqid);
void sc2355_tcp_ack_init(struct sprd_priv *priv);
void sc2355_tcp_ack_deinit(struct sprd_priv *priv);
int sc2355_vendor_init(struct wiphy *wiphy);
int sc2355_vendor_deinit(struct wiphy *wiphy);
int sc2355_dump_survey(struct wiphy *wiphy, struct net_device *ndev,
		       int idx, struct survey_info *s_info);
int sc2355_set_sniffer(struct net_device *ndev, struct ifreq *ifr);
#endif
