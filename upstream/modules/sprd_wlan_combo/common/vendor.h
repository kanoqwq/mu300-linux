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
#ifndef __VENDOR_H__
#define __VENDOR_H__

#include <linux/ctype.h>
#include <net/cfg80211.h>
#include <net/netlink.h>

#define SPRD_VENDOR_EVENT_NAN_INDEX	32

#define VENDOR_SCAN_RESULT_EXPIRE	(7 * HZ)

#define MAX_AP_CACHE_PER_SCAN		32
#define OUI_SPREAD			0x001374

enum {
	/* Memory dump of FW */
	WIFI_LOGGER_MEMORY_DUMP_SUPPORTED = (1 << (0)),
	/* PKT status */
	WIFI_LOGGER_PER_PACKET_TX_RX_STATUS_SUPPORTED = (1 << (1)),
	/* Connectivity event */
	WIFI_LOGGER_CONNECT_EVENT_SUPPORTED = (1 << (2)),
	/* POWER of Driver */
	WIFI_LOGGER_POWER_EVENT_SUPPORTED = (1 << (3)),
	/* WAKE LOCK of Driver */
	WIFI_LOGGER_WAKE_LOCK_SUPPORTED = (1 << (4)),
	/* verbose log of FW */
	WIFI_LOGGER_VERBOSE_SUPPORTED = (1 << (5)),
	/* monitor the health of FW */
	WIFI_LOGGER_WATCHDOG_TIMER_SUPPORTED = (1 << (6)),
	/* dumps driver state */
	WIFI_LOGGER_DRIVER_DUMP_SUPPORTED = (1 << (7)),
	/* tracks connection packets' fate */
	WIFI_LOGGER_PACKET_FATE_SUPPORTED = (1 << (8)),
};

enum vendor_wifi_error {
	VENDOR_WIFI_SUCCESS = 0,
	VENDOR_WIFI_ERROR_UNKNOWN = -1,
	VENDOR_WIFI_ERROR_UNINITIALIZED = -2,
	VENDOR_WIFI_ERROR_NOT_SUPPORTED = -3,
	VENDOR_WIFI_ERROR_NOT_AVAILABLE = -4,
	VENDOR_WIFI_ERROR_INVALID_ARGS = -5,
	VENDOR_WIFI_ERROR_INVALID_REQUEST_ID = -6,
	VENDOR_WIFI_ERROR_TIMED_OUT = -7,
	VENDOR_WIFI_ERROR_TOO_MANY_REQUESTS = -8,
	VENDOR_WIFI_ERROR_OUT_OF_MEMORY = -9,
	VENDOR_WIFI_ERROR_BUSY = -10,
};

enum vendor_cmd_id {
	VENDOR_CMD_ROAMING = 9,
	VENDOR_CMD_NAN = 12,
	VENDOR_SET_LLSTAT = 14,
	VENDOR_GET_LLSTAT = 15,
	VENDOR_CLR_LLSTAT = 16,
	VENDOR_CMD_GSCAN_START = 20,
	VENDOR_CMD_GSCAN_STOP = 21,
	VENDOR_CMD_GSCAN_GET_CHANNEL = 22,
	VENDOR_CMD_GSCAN_GET_CAPABILITIES = 23,
	VENDOR_CMD_GSCAN_GET_CACHED_RESULTS = 24,
	/* Used when report_threshold is reached in scan cache. */
	VENDOR_CMD_GSCAN_SCAN_RESULTS_AVAILABLE = 25,
	/* Used to report scan results when each probe rsp. is received,
	 * if report_events enabled in wifi_scan_cmd_params.
	 */
	VENDOR_CMD_GSCAN_FULL_SCAN_RESULT = 26,
	/* Indicates progress of scanning state-machine. */
	VENDOR_CMD_GSCAN_SCAN_EVENT = 27,
	/* Indicates BSSID Hotlist. */
	VENDOR_CMD_GSCAN_HOTLIST_AP_FOUND = 28,
	VENDOR_CMD_GSCAN_SET_BSSID_HOTLIST = 29,
	VENDOR_CMD_GSCAN_RESET_BSSID_HOTLIST = 30,
	VENDOR_CMD_GSCAN_SIGNIFICANT_CHANGE = 31,
	VENDOR_CMD_GSCAN_SET_SIGNIFICANT_CHANGE = 32,
	VENDOR_CMD_GSCAN_RESET_SIGNIFICANT_CHANGE = 33,
	VENDOR_CMD_GET_SUPPORT_FEATURE = 38,
	VENDOR_CMD_SET_MAC_OUI = 39,
	VENDOR_CMD_GSCAN_HOTLIST_AP_LOST = 41,
	VENDOR_CMD_GET_CONCURRENCY_MATRIX = 42,
	VENDOR_CMD_SET_SAE_PASSWORD = 43,
	VENDOR_CMD_GET_FEATURE = 55,
	VENDOR_CMD_GET_WIFI_INFO = 61,
	VENDOR_CMD_START_LOGGING = 62,
	VENDOR_CMD_WIFI_LOGGER_MEMORY_DUMP = 63,
	VENDOR_CMD_ROAM = 64,
	VENDOR_CMD_GSCAN_SET_SSID_HOTLIST = 65,
	VENDOR_CMD_GSCAN_RESET_SSID_HOTLIST = 66,
	VENDOR_CMD_PNO_SET_LIST = 69,
	VENDOR_CMD_PNO_SET_PASSPOINT_LIST = 70,
	VENDOR_CMD_PNO_RESET_PASSPOINT_LIST = 71,
	VENDOR_CMD_PNO_NETWORK_FOUND = 72,
	VENDOR_CMD_GET_LOGGER_FEATURE_SET = 76,
	VENDOR_CMD_GET_RING_DATA = 77,
	VENDOR_CMD_OFFLOADED_PACKETS = 79,
	VENDOR_CMD_MONITOR_RSSI = 80,
	VENDOR_CMD_ENABLE_ND_OFFLOAD = 82,
	VENDOR_CMD_GET_WAKE_REASON_STATS = 85,
	VENDOR_CMD_SET_SAR_LIMITS = 146,
	VENDOR_CMD_GET_AKM_SUITE = 0xB0,
	VENDOR_SET_COUNTRY_CODE = 0x100E,
	VENDOR_CMD_RTT_GET_CAPA = 0x1102,

	VENDOR_CMD_MAX
};

/* attribute id */

enum vendor_attr_gscan_id {
	ATTR_FEATURE_SET = 1,
	ATTR_GSCAN_NUM_CHANNELS = 3,
	ATTR_GSCAN_CHANNELS = 4,
	ATTR_MAX
};

/* link layer stats */
enum vendor_attr {
	ATTR_UNSPEC,
	ATTR_GET_LLSTAT,
	ATTR_CLR_LLSTAT,
	/* NAN */
	ATTR_NAN,
	ATTR_ROAMING_POLICY = 5,
	ATTR_VENDOR_AFTER_LAST,
	ATTR_VENDOR_MAX =
		ATTR_VENDOR_AFTER_LAST - 1,
};

static const struct nla_policy
	roaming_policy[ATTR_VENDOR_MAX + 1] = {
	[ATTR_ROAMING_POLICY] = {.type = NLA_U32},
};

/*start of link layer stats, CMD ID:14,15,16*/
enum vendor_attr_ll_stats_set {
	ATTR_LL_STATS_SET_INVALID = 0,
	/* Unsigned 32-bit value */
	ATTR_LL_STATS_MPDU_THRESHOLD = 1,
	ATTR_LL_STATS_GATHERING = 2,
	/* keep last */
	ATTR_LL_STATS_SET_AFTER_LAST,
	ATTR_LL_STATS_SET_MAX = ATTR_LL_STATS_SET_AFTER_LAST - 1,
};

static const struct nla_policy
	ll_stats_policy[ATTR_LL_STATS_SET_MAX + 1] = {
	[ATTR_LL_STATS_MPDU_THRESHOLD] = {.type = NLA_U32},
	[ATTR_LL_STATS_GATHERING] = {.type = NLA_U32},
};

enum vendor_attr_ll_stats_results {
	ATTR_LL_STATS_INVALID = 0,
	ATTR_LL_STATS_RESULTS_REQ_ID = 1,
	ATTR_LL_STATS_IFACE_BEACON_RX = 2,
	ATTR_LL_STATS_IFACE_MGMT_RX = 3,
	ATTR_LL_STATS_IFACE_MGMT_ACTION_RX = 4,
	ATTR_LL_STATS_IFACE_MGMT_ACTION_TX = 5,
	ATTR_LL_STATS_IFACE_RSSI_MGMT = 6,
	ATTR_LL_STATS_IFACE_RSSI_DATA = 7,
	ATTR_LL_STATS_IFACE_RSSI_ACK = 8,
	ATTR_LL_STATS_IFACE_INFO_MODE = 9,
	ATTR_LL_STATS_IFACE_INFO_MAC_ADDR = 10,
	ATTR_LL_STATS_IFACE_INFO_STATE = 11,
	ATTR_LL_STATS_IFACE_INFO_ROAMING = 12,
	ATTR_LL_STATS_IFACE_INFO_CAPABILITIES = 13,
	ATTR_LL_STATS_IFACE_INFO_SSID = 14,
	ATTR_LL_STATS_IFACE_INFO_BSSID = 15,
	ATTR_LL_STATS_IFACE_INFO_AP_COUNTRY_STR = 16,
	ATTR_LL_STATS_IFACE_INFO_COUNTRY_STR = 17,
	ATTR_LL_STATS_WMM_AC_AC = 18,
	ATTR_LL_STATS_WMM_AC_TX_MPDU = 19,
	ATTR_LL_STATS_WMM_AC_RX_MPDU = 20,
	ATTR_LL_STATS_WMM_AC_TX_MCAST = 21,
	ATTR_LL_STATS_WMM_AC_RX_MCAST = 22,
	ATTR_LL_STATS_WMM_AC_RX_AMPDU = 23,
	ATTR_LL_STATS_WMM_AC_TX_AMPDU = 24,
	ATTR_LL_STATS_WMM_AC_MPDU_LOST = 25,
	ATTR_LL_STATS_WMM_AC_RETRIES = 26,
	ATTR_LL_STATS_WMM_AC_RETRIES_SHORT = 27,
	ATTR_LL_STATS_WMM_AC_RETRIES_LONG = 28,
	ATTR_LL_STATS_WMM_AC_CONTENTION_TIME_MIN = 29,
	ATTR_LL_STATS_WMM_AC_CONTENTION_TIME_MAX = 30,
	ATTR_LL_STATS_WMM_AC_CONTENTION_TIME_AVG = 31,
	ATTR_LL_STATS_WMM_AC_CONTENTION_NUM_SAMPLES = 32,
	ATTR_LL_STATS_IFACE_NUM_PEERS = 33,
	ATTR_LL_STATS_PEER_INFO_TYPE = 34,
	ATTR_LL_STATS_PEER_INFO_MAC_ADDRESS = 35,
	ATTR_LL_STATS_PEER_INFO_CAPABILITIES = 36,
	ATTR_LL_STATS_PEER_INFO_NUM_RATES = 37,
	ATTR_LL_STATS_RATE_PREAMBLE = 38,
	ATTR_LL_STATS_RATE_NSS = 39,
	ATTR_LL_STATS_RATE_BW = 40,
	ATTR_LL_STATS_RATE_MCS_INDEX = 41,
	ATTR_LL_STATS_RATE_BIT_RATE = 42,
	ATTR_LL_STATS_RATE_TX_MPDU = 43,
	ATTR_LL_STATS_RATE_RX_MPDU = 44,
	ATTR_LL_STATS_RATE_MPDU_LOST = 45,
	ATTR_LL_STATS_RATE_RETRIES = 46,
	ATTR_LL_STATS_RATE_RETRIES_SHORT = 47,
	ATTR_LL_STATS_RATE_RETRIES_LONG = 48,
	ATTR_LL_STATS_RADIO_ID = 49,
	ATTR_LL_STATS_RADIO_ON_TIME = 50,
	ATTR_LL_STATS_RADIO_TX_TIME = 51,
	ATTR_LL_STATS_RADIO_RX_TIME = 52,
	ATTR_LL_STATS_RADIO_ON_TIME_SCAN = 53,
	ATTR_LL_STATS_RADIO_ON_TIME_NBD = 54,
	ATTR_LL_STATS_RADIO_ON_TIME_GSCAN = 55,
	ATTR_LL_STATS_RADIO_ON_TIME_ROAM_SCAN = 56,
	ATTR_LL_STATS_RADIO_ON_TIME_PNO_SCAN = 57,
	ATTR_LL_STATS_RADIO_ON_TIME_HS20 = 58,
	ATTR_LL_STATS_RADIO_NUM_CHANNELS = 59,
	ATTR_LL_STATS_CHANNEL_INFO_WIDTH = 60,
	ATTR_LL_STATS_CHANNEL_INFO_CENTER_FREQ = 61,
	ATTR_LL_STATS_CHANNEL_INFO_CENTER_FREQ0 = 62,
	ATTR_LL_STATS_CHANNEL_INFO_CENTER_FREQ1 = 63,
	ATTR_LL_STATS_CHANNEL_ON_TIME = 64,
	ATTR_LL_STATS_CHANNEL_CCA_BUSY_TIME = 65,
	ATTR_LL_STATS_NUM_RADIOS = 66,
	ATTR_LL_STATS_CH_INFO = 67,
	ATTR_LL_STATS_PEER_INFO = 68,
	ATTR_LL_STATS_PEER_INFO_RATE_INFO = 69,
	ATTR_LL_STATS_WMM_INFO = 70,
	ATTR_LL_STATS_RESULTS_MORE_DATA = 71,
	ATTR_LL_STATS_IFACE_AVERAGE_TSF_OFFSET = 72,
	ATTR_LL_STATS_IFACE_LEAKY_AP_DETECTED = 73,
	ATTR_LL_STATS_IFACE_LEAKY_AP_AVG_NUM_FRAMES_LEAKED = 74,
	ATTR_LL_STATS_IFACE_LEAKY_AP_GUARD_TIME = 75,
	ATTR_LL_STATS_TYPE = 76,
	ATTR_LL_STATS_RADIO_NUM_TX_LEVELS = 77,
	ATTR_LL_STATS_RADIO_TX_TIME_PER_LEVEL = 78,
	ATTR_LL_STATS_IFACE_RTS_SUCC_CNT = 79,
	ATTR_LL_STATS_IFACE_RTS_FAIL_CNT = 80,
	ATTR_LL_STATS_IFACE_PPDU_SUCC_CNT = 81,
	ATTR_LL_STATS_IFACE_PPDU_FAIL_CNT = 82,
	ATTR_LL_STATS_IFACE_INFO_TIME_SLICING_DUTY_CYCLE_PERCENT = 84,

	/* keep last */
	ATTR_LL_STATS_AFTER_LAST,
	ATTR_LL_STATS_MAX = ATTR_LL_STATS_AFTER_LAST - 1,
};

enum vendor_attr_ll_stats_type {
	ATTR_CMD_LL_STATS_GET_TYPE_INVALID = 0,
	ATTR_CMD_LL_STATS_GET_TYPE_RADIO = 1,
	ATTR_CMD_LL_STATS_GET_TYPE_IFACE = 2,
	ATTR_CMD_LL_STATS_GET_TYPE_PEERS = 3,

	/* keep last */
	ATTR_CMD_LL_STATS_TYPE_AFTER_LAST,
	ATTR_CMD_LL_STATS_TYPE_MAX = ATTR_CMD_LL_STATS_TYPE_AFTER_LAST - 1,
};

static const struct
nla_policy ll_stats_get_policy[ATTR_CMD_LL_STATS_TYPE_MAX + 1] = {
		[ATTR_CMD_LL_STATS_GET_TYPE_RADIO] = {.type = NLA_U32},
		[ATTR_CMD_LL_STATS_GET_TYPE_IFACE] = {.type = NLA_U32},
};

enum vendor_attr_ll_stats_clr {
	ATTR_LL_STATS_CLR_INVALID = 0,
	ATTR_LL_STATS_CLR_CONFIG_REQ_MASK = 1,
	ATTR_LL_STATS_CLR_CONFIG_STOP_REQ = 2,
	ATTR_LL_STATS_CLR_CONFIG_RSP_MASK = 3,
	ATTR_LL_STATS_CLR_CONFIG_STOP_RSP = 4,
	/* keep last */
	ATTR_LL_STATS_CLR_AFTER_LAST,
	ATTR_LL_STATS_CLR_MAX = ATTR_LL_STATS_CLR_AFTER_LAST - 1,
};

static const struct
nla_policy ll_stats_clr_policy[ATTR_LL_STATS_CLR_MAX + 1] = {
		[ATTR_LL_STATS_CLR_CONFIG_REQ_MASK] = {.type = NLA_U32},
		[ATTR_LL_STATS_CLR_CONFIG_STOP_REQ] = {.type = NLA_U8},
		[ATTR_LL_STATS_CLR_CONFIG_RSP_MASK] = {.type = NLA_U32},
		[ATTR_LL_STATS_CLR_CONFIG_STOP_RSP] = {.type = NLA_U32},
};

/* end of link layer stats */

enum vendor_attr_gscan_config_params {
	GSCAN_ATTR_CONFIG_INVALID = 0,
	GSCAN_ATTR_CONFIG_REQUEST_ID,
	GSCAN_ATTR_CONFIG_WIFI_BAND,
	/* Unsigned 32-bit value */
	GSCAN_ATTR_CONFIG_MAX_CHANNELS,

	/* Attributes for input params used by
	 * NL80211_VENDOR_SUBCMD_GSCAN_START sub command.
	 */

	/* Unsigned 32-bit value; channel frequency */
	GSCAN_ATTR_CONFIG_CHANNEL_SPEC,
	/* Unsigned 32-bit value; dwell time in ms. */
	GSCAN_ATTR_CONFIG_CHANNEL_DWELL_TIME,
	/* Unsigned 8-bit value; 0: active; 1: passive; N/A for DFS */
	GSCAN_ATTR_CONFIG_CHANNEL_PASSIVE,
	/* Unsigned 8-bit value; channel class */
	GSCAN_ATTR_CONFIG_CHANNEL_CLASS,

	/* Unsigned 8-bit value; bucket index, 0 based */
	GSCAN_ATTR_CONFIG_BUCKET_INDEX,
	/* Unsigned 8-bit value; band. */
	GSCAN_ATTR_CONFIG_BUCKET_BAND,
	/* Unsigned 32-bit value; desired period, in ms. */
	GSCAN_ATTR_CONFIG_BUCKET_PERIOD = 10,
	/* Unsigned 8-bit value; report events semantics. */
	GSCAN_ATTR_CONFIG_BUCKET_REPORT_EVENTS,
	/* Unsigned 32-bit value. Followed by a nested array of
	 * GSCAN_CHANNEL_* attributes.
	 */
	GSCAN_ATTR_CONFIG_BUCKET_NUM_CHANNEL_SPECS,

	/* Array of GSCAN_ATTR_CHANNEL_* attributes.
	 * Array size: GSCAN_ATTR_CONFIG_BUCKET_NUM_CHANNEL_SPECS
	 */
	GSCAN_ATTR_CONFIG_CHAN_SPEC,
	/* Unsigned 32-bit value; base timer period in ms. */
	GSCAN_ATTR_CONFIG_BASE_PERIOD,
	/* Unsigned 32-bit value; number of APs to store in each scan in the
	 * BSSID/RSSI history buffer (keep the highest RSSI APs).
	 */
	GSCAN_ATTR_CONFIG_MAX_AP_PER_SCAN = 15,
	/* Unsigned 8-bit value; In %, when scan buffer is this much full,
	 *wake up APPS.
	 */
	GSCAN_ATTR_CONFIG_REPORT_THR,
	/* Unsigned 8-bit value; number of scan bucket specs; followed by
	 *a nested array of_VENDOR_GSCAN_ATTR_BUCKET_SPEC_* attributes and values.
	 *The size of the array is determined by NUM_BUCKETS.
	 */
	GSCAN_ATTR_CONFIG_NUM_BUCKETS,
	/* Array of GSCAN_ATTR_BUCKET_SPEC_* attributes.
	 * Array size: GSCAN_ATTR_CONFIG_NUM_BUCKETS
	 */
	GSCAN_ATTR_CONFIG_BUCKET_SPEC,
	/* Unsigned 8-bit value */
	GSCAN_ATTR_CONFIG_CACHED_PARAM_FLUSH,
	/* Unsigned 32-bit value; maximum number of results to be returned. */
	GSCAN_ATTR_CONFIG_CACHED_PARAM_MAX = 20,

	/* An array of 6 x Unsigned 8-bit value */
	GSCAN_ATTR_CONFIG_AP_THR_BSSID,
	/* Signed 32-bit value */
	GSCAN_ATTR_CONFIG_AP_THR_RSSI_LOW,
	/* Signed 32-bit value */
	GSCAN_ATTR_CONFIG_AP_THR_RSSI_HIGH,
	/* Unsigned 32-bit value */
	GSCAN_ATTR_CONFIG_AP_THR_CHANNEL,

	/* Number of hotlist APs as unsigned 32-bit value, followed by a nested
	 * array of AP_THR_PARAM attributes and values. The size of the
	 * array is determined by NUM_AP.
	 */
	GSCAN_ATTR_CONFIG_BSSID_HOTLIST_NUM_AP = 25,

	/* Array of GSCAN_ATTR_AP_THR_PARAM_* attributes.
	 * Array size: GSCAN_ATTR_CONFIG_BUCKET_NUM_CHANNEL_SPECS
	 */
	GSCAN_ATTR_CONFIG_AP_THR_PARAM,

	/* Unsigned 32bit value; number of samples for averaging RSSI. */
	GSCAN_ATTR_CONFIG_SIGNIFICANT_RSSI_SAMPLE_SIZE,
	/* Unsigned 32bit value; number of samples to confirm AP loss. */
	GSCAN_ATTR_CONFIG_SIGNIFICANT_LOST_AP_SAMPLE_SIZE,
	/* Unsigned 32bit value; number of APs breaching threshold. */
	GSCAN_ATTR_CONFIG_SIGNIFICANT_MIN_BREACHING,
	/* Unsigned 32bit value; number of APs. Followed by an array of
	 * AP_THR_PARAM attributes. Size of the array is NUM_AP.
	 */
	GSCAN_ATTR_CONFIG_SIGNIFICANT_NUM_AP = 30,
	/* Unsigned 32-bit value; number of samples to confirm AP loss. */
	GSCAN_ATTR_CONFIG_BSSID_HOTLIST_LOST_AP_SAMPLE_SIZE,

	GSCAN_ATTR_CONFIG_BUCKET_MAX_PERIOD = 32,
	/* Unsigned 32-bit value. */
	GSCAN_ATTR_CONFIG_BUCKET_BASE = 33,
	/* Unsigned 32-bit value. For exponential back off bucket, number of
	 * scans to perform for a given period.
	 */
	GSCAN_ATTR_CONFIG_BUCKET_STEP_COUNT = 34,
	GSCAN_ATTR_CONFIG_REPORT_NUM_SCANS = 35,
	/* Attributes for data used by
	 * SPRD_NL80211_VENDOR_SUBCMD_GSCAN_SET_SSID_HOTLIST sub command.
	 */
	/* Unsigned 3-2bit value; number of samples to confirm SSID loss. */
	GSCAN_ATTR_CONFIG_SSID_HOTLIST_LOST_SSID_SAMPLE_SIZE = 36,
	/* Number of hotlist SSIDs as unsigned 32-bit value, followed by a
	 * nested array of SSID_THRESHOLD_PARAM_* attributes and values. The
	 * size of the array is determined by NUM_SSID.
	 */
	GSCAN_ATTR_CONFIG_SSID_HOTLIST_NUM_SSID = 37,
	/* Array of GSCAN_ATTR_GSCAN_SSID_THRESHOLD_PARAM_*
	 * attributes.
	 * Array size: GSCAN_ATTR_CONFIG_SSID_HOTLIST_NUM_SSID
	 */
	GSCAN_ATTR_CONFIG_SSID_THR = 38,

	/* An array of 33 x unsigned 8-bit value; NULL terminated SSID */
	GSCAN_ATTR_CONFIG_SSID_THR_SSID = 39,
	/* Unsigned 8-bit value */
	GSCAN_ATTR_CONFIG_SSID_THR_BAND = 40,
	/* Signed 32-bit value */
	GSCAN_ATTR_CONFIG_SSID_THR_RSSI_LOW = 41,
	/* Signed 32-bit value */
	GSCAN_ATTR_CONFIG_SSID_THR_RSSI_HIGH = 42,
	/* Unsigned 32-bit value; a bitmask with additional gscan config flag.
	 */
	GSCAN_ATTR_CONFIGURATION_FLAGS = 43,

	/* keep last */
	GSCAN_ATTR_CONFIG_AFTER_LAST,
	GSCAN_ATTR_CONFIG_MAX =
	    GSCAN_ATTR_CONFIG_AFTER_LAST - 1,
};

static const struct nla_policy
	wlan_gscan_config_policy[GSCAN_ATTR_CONFIG_MAX + 1] = {
	[GSCAN_ATTR_CONFIG_REQUEST_ID] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_WIFI_BAND] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_MAX_CHANNELS] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_CHANNEL_SPEC] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_CHANNEL_DWELL_TIME] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_CHANNEL_PASSIVE] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_CHANNEL_CLASS] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_BUCKET_INDEX] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_BUCKET_BAND] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_BUCKET_PERIOD] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_BUCKET_REPORT_EVENTS] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_BUCKET_NUM_CHANNEL_SPECS] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_BUCKET_MAX_PERIOD] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_BUCKET_BASE] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_BUCKET_STEP_COUNT] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_CHAN_SPEC] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_BASE_PERIOD] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_MAX_AP_PER_SCAN] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_REPORT_THR] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_REPORT_NUM_SCANS] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_NUM_BUCKETS] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_BUCKET_SPEC] = {.type = NLA_NESTED},
	[GSCAN_ATTR_CONFIG_CACHED_PARAM_FLUSH] = {.type = NLA_U8},
	[GSCAN_ATTR_CONFIG_CACHED_PARAM_MAX] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_AP_THR_BSSID] = {.type = NLA_UNSPEC},
	[GSCAN_ATTR_CONFIG_AP_THR_RSSI_LOW] = {.type = NLA_S32},
	[GSCAN_ATTR_CONFIG_AP_THR_RSSI_HIGH] = {.type = NLA_S32},
	[GSCAN_ATTR_CONFIG_AP_THR_CHANNEL] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_BSSID_HOTLIST_NUM_AP] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_AP_THR_PARAM] = {.type = NLA_NESTED},
	[GSCAN_ATTR_CONFIG_SIGNIFICANT_RSSI_SAMPLE_SIZE] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_SIGNIFICANT_LOST_AP_SAMPLE_SIZE] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_SIGNIFICANT_MIN_BREACHING] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_SIGNIFICANT_NUM_AP] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_BSSID_HOTLIST_LOST_AP_SAMPLE_SIZE] = {.type = NLA_U32},
	[GSCAN_ATTR_CONFIG_SSID_HOTLIST_LOST_SSID_SAMPLE_SIZE] = {.type = NLA_S32},
	[GSCAN_ATTR_CONFIG_SSID_HOTLIST_NUM_SSID] = {.type = NLA_S32},
	[GSCAN_ATTR_CONFIG_SSID_THR] = {.type = NLA_NESTED},
};

/*start of gscan----CMD ID:23*/
enum vendor_attr_gscan_results {
	ATTR_GSCAN_RESULTS_INVALID = 0,
	ATTR_GSCAN_RESULTS_REQUEST_ID = 1,
	ATTR_GSCAN_STATUS = 2,
	ATTR_GSCAN_RESULTS_NUM_CHANNELS = 3,
	ATTR_GSCAN_RESULTS_CHANNELS = 4,
	ATTR_GSCAN_SCAN_CACHE_SIZE = 5,
	ATTR_GSCAN_MAX_SCAN_BUCKETS = 6,
	ATTR_GSCAN_MAX_AP_CACHE_PER_SCAN = 7,
	ATTR_GSCAN_MAX_RSSI_SAMPLE_SIZE = 8,
	ATTR_GSCAN_MAX_SCAN_REPORTING_THRESHOLD = 9,
	ATTR_GSCAN_MAX_HOTLIST_BSSIDS = 10,
	ATTR_GSCAN_MAX_SIGNIFICANT_WIFI_CHANGE_APS = 11,
	ATTR_GSCAN_MAX_BSSID_HISTORY_ENTRIES = 12,
	ATTR_GSCAN_RESULTS_NUM_RESULTS_AVAILABLE = 13,
	ATTR_GSCAN_RESULTS_LIST = 14,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_TIME_STAMP = 15,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_SSID = 16,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_BSSID = 17,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_CHANNEL = 18,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_RSSI = 19,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT = 20,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_RTT_SD = 21,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_BEACON_PERIOD = 22,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_CAPABILITY = 23,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_IE_LENGTH = 24,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_IE_DATA = 25,
	ATTR_GSCAN_RESULTS_SCAN_RESULT_MORE_DATA = 26,
	ATTR_GSCAN_RESULTS_SCAN_EVENT_TYPE = 27,
	ATTR_GSCAN_RESULTS_SCAN_EVENT_STATUS = 28,
	ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_BSSID = 29,
	ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_CHANNEL = 30,
	ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_NUM_RSSI = 31,
	ATTR_GSCAN_RESULTS_SIGNIFICANT_CHANGE_RESULT_RSSI_LIST = 32,
	ATTR_GSCAN_CACHED_RESULTS_LIST = 33,
	ATTR_GSCAN_CACHED_RESULTS_SCAN_ID = 34,
	ATTR_GSCAN_CACHED_RESULTS_FLAGS = 35,
	ATTR_GSCAN_PNO_RESULTS_PASSPOINT_NETWORK_FOUND_NUM_MATCHES = 36,
	ATTR_GSCAN_PNO_RESULTS_PASSPOINT_MATCH_RESULT_LIST = 37,
	ATTR_GSCAN_PNO_RESULTS_PASSPOINT_MATCH_ID = 38,
	ATTR_GSCAN_PNO_RESULTS_PASSPOINT_MATCH_ANQP_LEN = 39,
	ATTR_GSCAN_PNO_RESULTS_PASSPOINT_MATCH_ANQP = 40,
	ATTR_GSCAN_MAX_HOTLIST_SSIDS = 41,
	ATTR_GSCAN_MAX_NUM_EPNO_NETS = 42,
	ATTR_GSCAN_MAX_NUM_EPNO_NETS_BY_SSID = 43,
	ATTR_GSCAN_MAX_NUM_WHITELISTED_SSID = 44,
	ATTR_GSCAN_RESULTS_BUCKETS_SCANNED = 45,
	ATTR_GSCAN_MAX_NUM_BLACKLISTED_BSSID = 46,
	ATTR_GSCAN_RESULTS_AFTER_LAST,
	ATTR_GSCAN_RESULTS_MAX = ATTR_GSCAN_RESULTS_AFTER_LAST - 1,
};

struct gscan_result {
	unsigned long ts;
	char ssid[32 + 1];
	char bssid[ETH_ALEN];
	u8 channel;
	s8 rssi;
	u32 rtt;
	u32 rtt_sd;
	u16 beacon_period;
	u16 capability;
	u16 ie_length;
	char ie_data[1];
} __packed;

enum vendor_attr_set_scanning_mac_oui {
	ATTR_SET_SCANNING_MAC_OUI_INVALID = 0,
	ATTR_SET_SCANNING_MAC_OUI = 1,
	/* keep last */
	ATTR_SET_SCANNING_MAC_OUI_AFTER_LAST,
	ATTR_SET_SCANNING_MAC_OUI_MAX =
	    ATTR_SET_SCANNING_MAC_OUI_AFTER_LAST - 1,
};

static const struct nla_policy
	mac_oui_policy[ATTR_SET_SCANNING_MAC_OUI_MAX + 1] = {
	[ATTR_SET_SCANNING_MAC_OUI] = { .type = NLA_BINARY, .len = 3},
};

/* end of gscan capability---CMD ID:23 */

/* start of get supported feature---CMD ID:38 */
/* Feature enums */
/* Basic infrastructure mode */
#define WIFI_FEATURE_INFRA              0x0001
/* Support for 5 GHz Band */
#define WIFI_FEATURE_INFRA_5G           0x0002
/* Support for GAS/ANQP */
#define WIFI_FEATURE_HOTSPOT            0x0004
/* Wifi-Direct */
#define WIFI_FEATURE_P2P                0x0008
/* Soft AP */
#define WIFI_FEATURE_SOFT_AP            0x0010
/* Google-Scan APIs */
#define WIFI_FEATURE_GSCAN              0x0020
/* Neighbor Awareness Networking */
#define WIFI_FEATURE_NAN                0x0040
/* Device-to-device RTT */
#define WIFI_FEATURE_D2D_RTT            0x0080
/* Device-to-AP RTT */
#define WIFI_FEATURE_D2AP_RTT           0x0100
/* Batched Scan (legacy) */
#define WIFI_FEATURE_BATCH_SCAN         0x0200
/* Preferred network offload */
#define WIFI_FEATURE_PNO                0x0400
/* Support for two STAs */
#define WIFI_FEATURE_ADDITIONAL_STA     0x0800
/* Tunnel directed link setup */
#define WIFI_FEATURE_TDLS               0x1000
/* Support for TDLS off channel */
#define WIFI_FEATURE_TDLS_OFFCHANNEL    0x2000
/* Enhanced power reporting */
#define WIFI_FEATURE_EPR                0x4000
/* Support for AP STA Concurrency */
#define WIFI_FEATURE_AP_STA             0x8000
/* Link layer stats collection */
#define WIFI_FEATURE_LINK_LAYER_STATS   0x10000
/* WiFi Logger */
#define WIFI_FEATURE_LOGGER             0x20000
/* WiFi PNO enhanced */
#define WIFI_FEATURE_HAL_EPNO           0x40000
/* RSSI Monitor */
#define WIFI_FEATURE_RSSI_MONITOR       0x80000
/* WiFi mkeep_alive */
#define WIFI_FEATURE_MKEEP_ALIVE        0x100000
/* ND offload configure */
#define WIFI_FEATURE_CONFIG_NDO         0x200000
/* Capture Tx transmit power levels */
#define WIFI_FEATURE_TX_TRANSMIT_POWER  0x400000
/* Enable/Disable firmware roaming */
#define WIFI_FEATURE_CONTROL_ROAMING    0x800000
/* Support Probe IE white listing */
#define WIFI_FEATURE_IE_WHITELIST       0x1000000
/* Support MAC & Probe Sequence Number randomization */
#define WIFI_FEATURE_SCAN_RAND          0x2000000
/*Support TX power limit function */
#define WIFI_FEATURE_SET_SAR_LIMIT	0x4000000

/* start of get supported feature---CMD ID:42 */

#define CDS_MAX_FEATURE_SET   8

/* enum vendor_attr_get_concurrency_matrix - get concurrency matrix */
enum vendor_attr_get_concurrency_matrix {
	ATTR_CO_MATRIX_INVALID = 0,
	ATTR_CO_MATRIX_CONFIG_PARAM_SET_SIZE_MAX = 1,
	ATTR_CO_MATRIX_RESULTS_SET_SIZE = 2,
	ATTR_CO_MATRIX_RESULTS_SET = 3,
	ATTR_CO_MATRIX_AFTER_LAST,
	ATTR_CO_MATRIX_MAX = ATTR_CO_MATRIX_AFTER_LAST - 1,
};

static const struct
nla_policy get_concurrency_matrix_policy[ATTR_CO_MATRIX_MAX + 1] = {
		[ATTR_CO_MATRIX_CONFIG_PARAM_SET_SIZE_MAX] = {.type = NLA_U32},
};

/* end of get supported feature---CMD ID:42 */

/* start of get wifi info----CMD ID:61 */
enum vendor_attr_get_wifi_info {
	ATTR_WIFI_INFO_GET_INVALID = 0,
	ATTR_WIFI_INFO_DRIVER_VERSION = 1,
	ATTR_WIFI_INFO_FIRMWARE_VERSION = 2,
	ATTR_WIFI_INFO_GET_AFTER_LAST,
	ATTR_WIFI_INFO_GET_MAX = ATTR_WIFI_INFO_GET_AFTER_LAST - 1,
};

static const struct
nla_policy get_wifi_info_policy[ATTR_WIFI_INFO_GET_MAX + 1] = {
		[ATTR_WIFI_INFO_DRIVER_VERSION] = {.type = NLA_U32},
		[ATTR_WIFI_INFO_FIRMWARE_VERSION] = {.type = NLA_U32},
};

/* end of get wifi info----CMD ID:61 */

/* start of wifi logger start, CMD ID:62 */
enum vendor_attr_wifi_logger_start {
	ATTR_WIFI_LOGGER_START_INVALID = 0,
	ATTR_WIFI_LOGGER_RING_ID = 1,
	ATTR_WIFI_LOGGER_VERBOSE_LEVEL = 2,
	ATTR_WIFI_LOGGER_FLAGS = 3,

	/* keep last */
	ATTR_WIFI_LOGGER_START_AFTER_LAST,
	ATTR_WIFI_LOGGER_START_GET_MAX =
		ATTR_WIFI_LOGGER_START_AFTER_LAST - 1,
};

static const struct
nla_policy wifi_logger_start_policy[ATTR_WIFI_LOGGER_START_GET_MAX + 1] = {
		[ATTR_WIFI_LOGGER_RING_ID] = {.type = NLA_U32},
		[ATTR_WIFI_LOGGER_VERBOSE_LEVEL] = {.type = NLA_U32},
		[ATTR_WIFI_LOGGER_FLAGS] = {.type = NLA_U32},
};

/* end of wifi logger start----CMD ID:62 */

/* start of roaming data structure,CMD ID:64,CMD ID:9 */
enum fw_roaming_state {
	ROAMING_DISABLE,
	ROAMING_ENABLE
};

enum vendor_attr_roaming_config_params {
	ATTR_ROAM_INVALID = 0,
	ATTR_ROAM_SUBCMD = 1,
	ATTR_ROAM_REQ_ID = 2,
	ATTR_ROAM_WHITE_LIST_SSID_NUM_NETWORKS = 3,
	ATTR_ROAM_WHITE_LIST_SSID_LIST = 4,
	ATTR_ROAM_WHITE_LIST_SSID = 5,
	ATTR_ROAM_A_BAND_BOOST_THRESHOLD = 6,
	ATTR_ROAM_A_BAND_PENALTY_THRESHOLD = 7,
	ATTR_ROAM_A_BAND_BOOST_FACTOR = 8,
	ATTR_ROAM_A_BAND_PENALTY_FACTOR = 9,
	ATTR_ROAM_A_BAND_MAX_BOOST = 10,
	ATTR_ROAM_LAZY_ROAM_HISTERESYS = 11,
	ATTR_ROAM_ALERT_ROAM_RSSI_TRIGGER = 12,
	/* Attribute for set_lazy_roam */
	ATTR_ROAM_SET_LAZY_ROAM_ENABLE = 13,
	/* Attribute for set_lazy_roam with preferences */
	ATTR_ROAM_SET_BSSID_PREFS = 14,
	ATTR_ROAM_SET_LAZY_ROAM_NUM_BSSID = 15,
	ATTR_ROAM_SET_LAZY_ROAM_BSSID = 16,
	ATTR_ROAM_SET_LAZY_ROAM_RSSI_MODIFIER = 17,
	/* Attribute for set_blacklist bssid params */
	ATTR_ROAM_SET_BSSID_PARAMS = 18,
	ATTR_ROAM_SET_BSSID_PARAMS_NUM_BSSID = 19,
	ATTR_ROAM_SET_BSSID_PARAMS_BSSID = 20,
	/* keep last */
	ATTR_ROAM_AFTER_LAST,
	ATTR_ROAM_MAX = ATTR_ROAM_AFTER_LAST - 1,
};

static const struct nla_policy roaming_config_policy[ATTR_ROAM_MAX + 1] = {
		[ATTR_ROAM_SUBCMD] = {.type = NLA_U32},
		[ATTR_ROAM_REQ_ID] = {.type = NLA_U32},
		[ATTR_ROAM_WHITE_LIST_SSID_NUM_NETWORKS] = {.type = NLA_U32},
		[ATTR_ROAM_WHITE_LIST_SSID_LIST] = {.type = NLA_NESTED},
		[ATTR_ROAM_WHITE_LIST_SSID] = {.type = NLA_BINARY},
		[ATTR_ROAM_SET_BSSID_PARAMS] = {.type = NLA_NESTED},
		[ATTR_ROAM_SET_BSSID_PARAMS_NUM_BSSID] = {.type = NLA_U32},
		[ATTR_ROAM_SET_BSSID_PARAMS_BSSID] = {.type = NLA_BINARY},
};

enum vendor_attr_roam_subcmd {
	ATTR_ROAM_SUBCMD_INVALID = 0,
	ATTR_ROAM_SUBCMD_SSID_WHITE_LIST = 1,
	ATTR_ROAM_SUBCMD_SET_GSCAN_ROAM_PARAMS = 2,
	ATTR_ROAM_SUBCMD_SET_LAZY_ROAM = 3,
	ATTR_ROAM_SUBCMD_SET_BSSID_PREFS = 4,
	ATTR_ROAM_SUBCMD_SET_BSSID_PARAMS = 5,
	ATTR_ROAM_SUBCMD_SET_BLACKLIST_BSSID = 6,
	/*KEEP LAST */
	ATTR_ROAM_SUBCMD_AFTER_LAST,
	ATTR_ROAM_SUBCMD_MAX = ATTR_ROAM_SUBCMD_AFTER_LAST - 1,
};

static const struct
nla_policy roam_policy[ATTR_ROAM_SUBCMD_MAX + 1] = {
		[ATTR_ROAM_SUBCMD_SSID_WHITE_LIST] = {.type = NLA_U32},
		[ATTR_ROAM_SUBCMD_SET_GSCAN_ROAM_PARAMS] = {.type = NLA_U32},
		[ATTR_ROAM_SUBCMD_SET_LAZY_ROAM] = {.type = NLA_U32},
		[ATTR_ROAM_SUBCMD_SET_BSSID_PREFS] = {.type = NLA_U32},
		[ATTR_ROAM_SUBCMD_SET_BSSID_PARAMS] = {.type = NLA_U32},
		[ATTR_ROAM_SUBCMD_SET_BLACKLIST_BSSID] = {.type = NLA_U32},
};

#define MAX_WHITE_SSID 4
#define MAX_BLACK_BSSID  16

struct ssid_t {
	u32 length;
	char ssid_str[IEEE80211_MAX_SSID_LEN];
} __packed;

struct bssid_t {
	u8 MAC_addr[ETH_ALEN];
} __packed;

struct roam_white_list_params {
	u8 num_white_ssid;
	struct ssid_t white_list[MAX_WHITE_SSID];
} __packed;

struct roam_black_list_params {
	u8 num_black_bssid;
	struct bssid_t black_list[MAX_BLACK_BSSID];
} __packed;

/*end of roaming data structure,CMD ID:64*/

/*RSSI monitor start */

enum vendor_rssi_monitor_control {
	VENDOR_RSSI_MONITOR_CONTROL_INVALID = 0,
	VENDOR_RSSI_MONITOR_START,
	VENDOR_RSSI_MONITOR_STOP,
};

/* struct rssi_monitor_req - rssi monitoring
 * @request_id: request id
 * @session_id: session id
 * @min_rssi: minimum rssi
 * @max_rssi: maximum rssi
 * @control: flag to indicate start or stop
 */
struct rssi_monitor_req {
	u32 request_id;
	s8 min_rssi;
	s8 max_rssi;
	bool control;
} __packed;

struct rssi_monitor_event {
	u32 request_id;
	s8 curr_rssi;
	u8 curr_bssid[ETH_ALEN];
};

#define EVENT_BUF_SIZE (1024)

enum vendor_attr_rssi_monitor {
	ATTR_RSSI_MONITOR_INVALID = 0,
	ATTR_RSSI_MONITOR_CONTROL,
	ATTR_RSSI_MONITOR_REQUEST_ID,
	ATTR_RSSI_MONITOR_MAX_RSSI,
	ATTR_RSSI_MONITOR_MIN_RSSI,
	/* attributes to be used/received in callback */
	ATTR_RSSI_MONITOR_CUR_BSSID,
	ATTR_RSSI_MONITOR_CUR_RSSI,
	/* keep last */
	ATTR_RSSI_MONITOR_AFTER_LAST,
	ATTR_RSSI_MONITOR_MAX =
	    ATTR_RSSI_MONITOR_AFTER_LAST - 1,
};

static const struct nla_policy rssi_monitor_policy[ATTR_RSSI_MONITOR_MAX + 1] = {
	[ATTR_RSSI_MONITOR_REQUEST_ID] = {.type = NLA_U32},
	[ATTR_RSSI_MONITOR_CONTROL] = {.type = NLA_U32},
	[ATTR_RSSI_MONITOR_MIN_RSSI] = {.type = NLA_S8},
	[ATTR_RSSI_MONITOR_MAX_RSSI] = {.type = NLA_S8},
};

/*RSSI monitor End*/

enum vendor_attr_nd_offload {
	ATTR_ND_OFFLOAD_INVALID = 0,
	ATTR_ND_OFFLOAD_FLAG,
	ATTR_ND_OFFLOAD_AFTER_LAST,
	ATTR_ND_OFFLOAD_MAX =
		ATTR_ND_OFFLOAD_AFTER_LAST - 1,
};

static const struct nla_policy nd_offload_policy[ATTR_ND_OFFLOAD_MAX + 1] = {
	[ATTR_ND_OFFLOAD_FLAG] = {.type = NLA_U8},
};

enum vendor_event_gscan {
	VENDOR_EVENT_GSCAN_START = 6,
	VENDOR_EVENT_GSCAN_STOP,
	VENDOR_EVENT_GSCAN_GET_CAPABILITIES,
	VENDOR_EVENT_GSCAN_GET_CACHE_RESULTS,
	VENDOR_EVENT_GSCAN_SCAN_RESULTS_AVAILABLE,
	VENDOR_EVENT_GSCAN_FULL_SCAN_RESULT,
	VENDOR_EVENT_GSCAN_SCAN_EVENT,
	VENDOR_EVENT_GSCAN_HOTLIST_AP_FOUND,
	VENDOR_EVENT_GSCAN_HOTLIST_AP_LOST,
	VENDOR_EVENT_GSCAN_SET_BSSID_HOTLIST,
	VENDOR_EVENT_GSCAN_RESET_BSSID_HOTLIST,
	VENDOR_EVENT_GSCAN_SIGNIFICANT_CHANGE,
	VENDOR_EVENT_GSCAN_SET_SIGNIFICANT_CHANGE,
	VENDOR_EVENT_GSCAN_RESET_SIGNIFICANT_CHANGE,
	VENDOR_EVENT_EPNO_FOUND_INDEX,
	SPRD_RTT_EVENT_COMPLETE_INDEX,

	VENDOR_EVENT_INDEX_MAX,
};

enum vendor_event_nan {
	VENDOR_EVENT_NAN_MONITOR_RSSI = 0,
	/* NAN */
	VENDOR_EVENT_NAN = 0x1400,
};

/*end of get supported feature---CMD ID:38*/

enum vendor_wifi_connection_state {
	VENDOR_WIFI_DISCONNECTED = 0,
	VENDOR_WIFI_AUTHENTICATING = 1,
	VENDOR_WIFI_ASSOCIATING = 2,
	VENDOR_WIFI_ASSOCIATED = 3,
	VENDOR_WIFI_EAPOL_STARTED = 4,
	VENDOR_WIFI_EAPOL_COMPLETED = 5,
};

enum vendor_roam_state {
	VENDOR_ROAMING_IDLE = 0,
	VENDOR_ROAMING_ACTIVE = 1,
};

struct llstat_channel_info {
	u32 channel_width;
	u32 center_freq;
	u32 center_freq0;
	u32 center_freq1;
	u32 on_time;
	u32 cca_busy_time;
} __packed;

/* configuration params */
struct wifi_link_layer_params {
	u32 mpdu_size_threshold;
	u32 aggressive_statistics_gathering;
} __packed;

struct wifi_clr_llstat_rsp {
	u32 stats_clear_rsp_mask;
	u8 stop_rsp;
};

/* wifi rate */
struct wifi_rate {
	u32 preamble:3;
	u32 nss:2;
	u32 bw:3;
	u32 ratemcsidx:8;
	u32 reserved:16;
	u32 bitrate;
};

struct wifi_rate_stat {
	struct wifi_rate rate;
	u32 tx_mpdu;
	u32 rx_mpdu;
	u32 mpdu_lost;
	u32 retries;
	u32 retries_short;
	u32 retries_long;
};

/* per peer statistics */
struct wifi_peer_info {
	u8 type;
	u8 peer_mac_address[6];
	u32 capabilities;
	u32 num_rate;
	struct wifi_rate_stat rate_stats[];
};

struct wifi_interface_link_layer_info {
	enum sprd_mode mode;
	u8 mac_addr[6];
	enum vendor_wifi_connection_state state;
	enum vendor_roam_state roaming;
	u32 capabilities;
	u8 ssid[33];
	u8 bssid[6];
	u8 ap_country_str[3];
	u8 country_str[3];
	u8 time_slicing_duty_cycle_percent;
};

enum wifi_traffic_ac {
	WIFI_AC_VO = 0,
	WIFI_AC_VI = 1,
	WIFI_AC_BE = 2,
	WIFI_AC_BK = 3,
	WIFI_AC_MAX = 4,
};

/* Per access category statistics */
struct wifi_wmm_ac_stat {
	enum wifi_traffic_ac ac;
	u32 tx_mpdu;
	u32 rx_mpdu;
	u32 tx_mcast;
	u32 rx_mcast;
	u32 rx_ampdu;
	u32 tx_ampdu;
	u32 mpdu_lost;
	u32 retries;
	u32 retries_short;
	u32 retries_long;
	u32 contention_time_min;
	u32 contention_time_max;
	u32 contention_time_avg;
	u32 contention_num_samples;
};

/* interface statistics */
struct wifi_iface_stat {
	void *iface;
	struct wifi_interface_link_layer_info info;
	u32 beacon_rx;
	u64 average_tsf_offset;
	u32 leaky_ap_detected;
	u32 leaky_ap_avg_num_frames_leaked;
	u32 leaky_ap_guard_time;
	u32 mgmt_rx;
	u32 mgmt_action_rx;
	u32 mgmt_action_tx;
	u32 rssi_mgmt;
	u32 rssi_data;
	u32 rssi_ack;
	struct wifi_wmm_ac_stat ac[WIFI_AC_MAX];
	u32 num_peers;
	struct wifi_peer_info peer_info[];
};

/* WiFi Common definitions */
/* channel operating width */
enum vendor_channel_width {
	VENDOR_CHAN_WIDTH_20 = 0,
	VENDOR_CHAN_WIDTH_40 = 1,
	VENDOR_CHAN_WIDTH_80 = 2,
	VENDOR_CHAN_WIDTH_160 = 3,
	VENDOR_CHAN_WIDTH_80P80 = 4,
	VENDOR_CHAN_WIDTH_5 = 5,
	VENDOR_CHAN_WIDTH_10 = 6,
	VENDOR_CHAN_WIDTH_INVALID = -1
};

/* channel information */
struct wifi_channel_info {
	enum vendor_channel_width width;
	u32 center_freq;
	u32 center_freq0;
	u32 center_freq1;
};

/* channel statistics */
struct wifi_channel_stat {
	struct wifi_channel_info channel;
	u32 on_time;
	u32 cca_busy_time;
};

/* radio statistics */
#define SPRD_LLSTATE_MAX_CHANEL_NUM	    1
struct wifi_radio_stat {
	u32 radio;
	u32 on_time;
	u32 tx_time;
	u32 num_tx_levels;
	u32 *tx_time_per_levels;
	u32 rx_time;
	u32 on_time_scan;
	u32 on_time_nbd;
	u32 on_time_gscan;
	u32 on_time_roam_scan;
	u32 on_time_pno_scan;
	u32 on_time_hs20;
	u32 num_channels;
	struct wifi_channel_stat channels[SPRD_LLSTATE_MAX_CHANEL_NUM];
};

struct vendor_data {
	struct wifi_radio_stat radio_st;
	struct wifi_iface_stat iface_st;
};

/*end of link layer stats*/

/* NL attributes for data used by
 * NL80211_VENDOR_SUBCMD_GET_WAKE_REASON_STATS.
 */
/*start of wake stats---CMD ID:85*/
enum vendor_attr_wake_stats {
	ATTR_WAKE_INVALID = 0,
	ATTR_WAKE_TOTAL_CMD_EVT_WAKE,
	ATTR_WAKE_CMD_EVT_WAKE_CNT_PTR,
	ATTR_WAKE_CMD_EVT_WAKE_CNT_SZ,
	ATTR_WAKE_TOTAL_DRV_FW_LOCAL_WAKE,
	ATTR_WAKE_DRV_FW_LOCAL_WAKE_CNT_PTR,
	ATTR_WAKE_DRV_FW_LOCAL_WAKE_CNT_SZ,
	ATTR_WAKE_TOTAL_RX_DATA_WAKE,
	ATTR_WAKE_RX_UNICAST_CNT,
	ATTR_WAKE_RX_MULTICAST_CNT,
	ATTR_WAKE_RX_BROADCAST_CNT,
	ATTR_WAKE_ICMP_PKT,
	ATTR_WAKE_ICMP6_PKT,
	ATTR_WAKE_ICMP6_RA,
	ATTR_WAKE_ICMP6_NA,
	ATTR_WAKE_ICMP6_NS,
	ATTR_WAKE_ICMP4_RX_MULTICAST_CNT,
	ATTR_WAKE_ICMP6_RX_MULTICAST_CNT,
	ATTR_WAKE_OTHER_RX_MULTICAST_CNT,
	/* keep last */
	ATTR_WAKE_AFTER_LAST,
	ATTR_WAKE_MAX = ATTR_WAKE_AFTER_LAST - 1,
};

static const struct
nla_policy wake_stats_policy[ATTR_WAKE_MAX + 1] = {
	[ATTR_WAKE_CMD_EVT_WAKE_CNT_SZ] = { .type = NLA_U32 },
	[ATTR_WAKE_DRV_FW_LOCAL_WAKE_CNT_SZ] = { .type = NLA_U32 },
};

/*end of wake sats---CMD ID:85*/

/*start of SAR limit---- CMD ID:146*/
enum vendor_sar_limits_select {
	VENDOR_SAR_LIMITS_BDF0 = 0,
	VENDOR_SAR_LIMITS_BDF1 = 1,
	VENDOR_SAR_LIMITS_BDF2 = 2,
	VENDOR_SAR_LIMITS_BDF3 = 3,
	VENDOR_SAR_LIMITS_BDF4 = 4,
	VENDOR_SAR_LIMITS_NONE = 5,
	VENDOR_SAR_LIMITS_USER = 6,
};

enum vendor_attr_sar_limits {
	ATTR_SAR_LIMITS_INVALID = 0,
	ATTR_SAR_LIMITS_SAR_ENABLE = 1,
	ATTR_SAR_LIMITS_NUM_SPECS = 2,
	ATTR_SAR_LIMITS_SPEC = 3,
	ATTR_SAR_LIMITS_SPEC_BAND = 4,
	ATTR_SAR_LIMITS_SPEC_CHAIN = 5,
	ATTR_SAR_LIMITS_SPEC_MODULATION = 6,
	ATTR_SAR_LIMITS_SPEC_POWER_LIMIT = 7,
	ATTR_SAR_LIMITS_AFTER_LAST,
	ATTR_SAR_LIMITS_MAX = ATTR_SAR_LIMITS_AFTER_LAST - 1
};

static const struct
nla_policy vendor_sar_limits_policy[ATTR_SAR_LIMITS_MAX + 1] = {
	[ATTR_SAR_LIMITS_SAR_ENABLE] = { .type = NLA_U32 },
	[ATTR_SAR_LIMITS_NUM_SPECS] = { .type = NLA_U32 },
	[ATTR_SAR_LIMITS_SPEC] = { .type = NLA_U32 },
	[ATTR_SAR_LIMITS_SPEC_BAND] = { .type = NLA_U32 },
	[ATTR_SAR_LIMITS_SPEC_CHAIN] = { .type = NLA_U32 },
	[ATTR_SAR_LIMITS_SPEC_MODULATION] = { .type = NLA_U32 },
	[ATTR_SAR_LIMITS_SPEC_POWER_LIMIT] = { .type = NLA_U32 },
};

/* end of SAR limit---CMD ID:146 */

enum vendor_gscan_attr {
	GSCAN_ATTR_NUM_BUCKETS = 1,
	GSCAN_ATTR_BASE_PERIOD,
	GSCAN_ATTR_BUCKETS_BAND,
	GSCAN_ATTR_BUCKET_ID,
	GSCAN_ATTR_BUCKET_PERIOD,
	GSCAN_ATTR_BUCKET_NUM_CHANNELS,
	GSCAN_ATTR_BUCKET_CHANNELS,
	GSCAN_ATTR_BUCKET_SPEC,
	GSCAN_ATTR_BUCKET_CHANNELS_SPEC,
	GSCAN_ATTR_CH_DWELL_TIME,
	GSCAN_ATTR_CH_PASSIVE,
	GSCAN_ATTR_NUM_AP_PER_SCAN,
	GSCAN_ATTR_REPORT_THRESHOLD,
	GSCAN_ATTR_NUM_SCANS_TO_CACHE,
	GSCAN_ATTR_BAND = GSCAN_ATTR_BUCKETS_BAND,

	GSCAN_ATTR_ENABLE_FEATURE,
	GSCAN_ATTR_SCAN_RESULTS_COMPLETE,	/* indicates no more results */
	GSCAN_ATTR_FLUSH_FEATURE,	/* Flush all the configs */
	GSCAN_ATTR_FULL_SCAN_RESULTS,
	GSCAN_ATTR_REPORT_EVENTS,

	/* remaining reserved for additional attributes */
	GSCAN_ATTR_NUM_OF_RESULTS,
	GSCAN_ATTR_FLUSH_RESULTS,
	GSCAN_ATTR_SCAN_RESULTS,	/* flat array of wifi_scan_result */
	GSCAN_ATTR_SCAN_ID,		/* indicates scan number */
	GSCAN_ATTR_SCAN_FLAGS,	/* indicates if scan was aborted */
	GSCAN_ATTR_AP_FLAGS,		/* flags on significant change event */
	GSCAN_ATTR_NUM_CHANNELS,
	GSCAN_ATTR_CHANNEL_LIST,

	/* remaining reserved for additional attributes */

	/* Adaptive scan attributes */
	GSCAN_ATTR_BUCKET_STEP_COUNT,
	GSCAN_ATTR_BUCKET_MAX_PERIOD,

	GSCAN_ATTR_SSID,
	GSCAN_ATTR_BSSID,
	GSCAN_ATTR_CHANNEL,
	GSCAN_ATTR_RSSI,
	GSCAN_ATTR_TIMESTAMP,
	GSCAN_ATTR_RTT,
	GSCAN_ATTR_RTTSD,

	/* remaining reserved for additional attributes */

	GSCAN_ATTR_HOTLIST_BSSIDS,
	GSCAN_ATTR_RSSI_LOW,
	GSCAN_ATTR_RSSI_HIGH,
	GSCAN_ATTR_HOTLIST_ELEM,
	GSCAN_ATTR_HOTLIST_FLUSH,

	/* remaining reserved for additional attributes */
	GSCAN_ATTR_RSSI_SAMPLE_SIZE,
	GSCAN_ATTR_LOST_AP_SAMPLE_SIZE,
	GSCAN_ATTR_MIN_BREACHING,
	GSCAN_ATTR_SIGNIFICANT_CHANGE_BSSIDS,
	GSCAN_ATTR_SIGNIFICANT_CHANGE_FLUSH,

	/* remaining reserved for additional attributes */

	GSCAN_ATTR_WHITELIST_SSID,
	GSCAN_ATTR_NUM_WL_SSID,
	/*GSCAN_ATTR_WL_SSID_LEN,*/
	GSCAN_ATTR_WL_SSID_FLUSH,
	GSCAN_ATTR_WHITELIST_SSID_ELEM,
	GSCAN_ATTR_NUM_BSSID,
	GSCAN_ATTR_BSSID_PREF_LIST,
	GSCAN_ATTR_BSSID_PREF_FLUSH,
	GSCAN_ATTR_BSSID_PREF,
	GSCAN_ATTR_RSSI_MODIFIER,

	/* remaining reserved for additional attributes */

	GSCAN_ATTR_A_BAND_BOOST_THRESHOLD,
	GSCAN_ATTR_A_BAND_PENALTY_THRESHOLD,
	GSCAN_ATTR_A_BAND_BOOST_FACTOR,
	GSCAN_ATTR_A_BAND_PENALTY_FACTOR,
	GSCAN_ATTR_A_BAND_MAX_BOOST,
	GSCAN_ATTR_LAZY_ROAM_HYSTERESIS,
	GSCAN_ATTR_ALERT_ROAM_RSSI_TRIGGER,
	GSCAN_ATTR_LAZY_ROAM_ENABLE,

	/* BSSID blacklist */
	GSCAN_ATTR_BSSID_BLACKLIST_FLUSH,
	GSCAN_ATTR_BLACKLIST_BSSID,
	GSCAN_ATTR_BLACKLIST_BSSID_SPEC,

	/* ANQPO */
	GSCAN_ATTR_ANQPO_HS_LIST,
	GSCAN_ATTR_ANQPO_HS_LIST_SIZE,
	GSCAN_ATTR_ANQPO_HS_NETWORK_ID,
	GSCAN_ATTR_ANQPO_HS_NAI_REALM,
	GSCAN_ATTR_ANQPO_HS_ROAM_CONSORTIUM_ID,
	GSCAN_ATTR_ANQPO_HS_PLMN,

	GSCAN_ATTR_SSID_HOTLIST_FLUSH,
	GSCAN_ATTR_SSID_LOST_SAMPLE_SIZE,
	GSCAN_ATTR_HOTLIST_SSIDS,
	GSCAN_ATTR_SSID_RSSI_HIGH,
	GSCAN_ATTR_SSID_RSSI_LOW,
	GSCAN_ATTR_ANQPO_LIST_FLUSH,
	GSCAN_ATTR_PNO_RANDOM_MAC_OUI,

	GSCAN_ATTR_MAX
};

enum vendor_attr_pno_config_params {
	ATTR_PNO_INVALID = 0,
	/* Attributes for data used by
	 * SPRD_NL80211_VENDOR_SUBCMD_PNO_SET_PASSPOINT_LIST sub command.
	 */
	/* Unsigned 32-bit value */
	ATTR_PNO_PASSPOINT_LIST_PARAM_NUM = 1,
	/* Array of nested SPRD_WLAN_VENDOR_ATTR_PNO_PASSPOINT_NETWORK_PARAM_*
	 * attributes. Array size =
	 * ATTR_PNO_PASSPOINT_LIST_PARAM_NUM.
	 */
	ATTR_PNO_PASSPOINT_LIST_PARAM_NETWORK_ARRAY = 2,

	/* Unsigned 32-bit value */
	ATTR_PNO_PASSPOINT_NETWORK_PARAM_ID = 3,
	/* An array of 256 x unsigned 8-bit value; NULL terminated UTF-8 encoded
	 * realm, 0 if unspecified.
	 */
	ATTR_PNO_PASSPOINT_NETWORK_PARAM_REALM = 4,
	/* An array of 16 x unsigned 32-bit value; roaming consortium ids to
	 * match, 0 if unspecified.
	 */
	ATTR_PNO_PASSPOINT_NETWORK_PARAM_ROAM_CNSRTM_ID = 5,
	/* An array of 6 x unsigned 8-bit value; MCC/MNC combination, 0s if
	 * unspecified.
	 */
	ATTR_PNO_PASSPOINT_NETWORK_PARAM_ROAM_PLMN = 6,

	/* Attributes for data used by
	 * SPRD_NL80211_VENDOR_SUBCMD_PNO_SET_LIST sub command.
	 */
	/* Unsigned 32-bit value */
	ATTR_PNO_SET_LIST_PARAM_NUM_NETWORKS = 7,
	/* Array of nested
	 * SPRD_WLAN_VENDOR_ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_*
	 * attributes. Array size =
	 * ATTR_PNO_SET_LIST_PARAM_NUM_NETWORKS.
	 */
	ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORKS_LIST = 8,
	/* An array of 33 x unsigned 8-bit value; NULL terminated SSID */
	ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_SSID = 9,
	/* Signed 8-bit value; threshold for considering this SSID as found,
	 * required granularity for this threshold is 4 dBm to 8 dBm.
	 */
	ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_RSSI_THRESHOLD = 10,
	/* Unsigned 8-bit value; WIFI_PNO_FLAG_XXX */
	ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_FLAGS = 11,
	/* Unsigned 8-bit value; auth bit field for matching WPA IE */
	ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_AUTH_BIT = 12,
	/* Unsigned 8-bit to indicate ePNO type;
	 * It takes values from SPRD_wlan_epno_type
	 */
	ATTR_PNO_SET_LIST_PARAM_EPNO_TYPE = 13,

	/* Nested attribute to send the channel list */
	ATTR_PNO_SET_LIST_PARAM_EPNO_CHANNEL_LIST = 14,

	/* Unsigned 32-bit value; indicates the interval between PNO scan
	 * cycles in msec.
	 */
	ATTR_PNO_SET_LIST_PARAM_EPNO_SCAN_INTERVAL = 15,
	ATTR_EPNO_MIN5GHZ_RSSI = 16,
	ATTR_EPNO_MIN24GHZ_RSSI = 17,
	ATTR_EPNO_INITIAL_SCORE_MAX = 18,
	ATTR_EPNO_CURRENT_CONNECTION_BONUS = 19,
	ATTR_EPNO_SAME_NETWORK_BONUS = 20,
	ATTR_EPNO_SECURE_BONUS = 21,
	ATTR_EPNO_BAND5GHZ_BONUS = 22,

	/* keep last */
	ATTR_PNO_AFTER_LAST,
	ATTR_PNO_MAX = ATTR_PNO_AFTER_LAST - 1,
};

static const struct nla_policy
	wlan_gscan_result_policy[ATTR_PNO_MAX + 1] = {
	[ATTR_GSCAN_RESULTS_REQUEST_ID] = {.type = NLA_U32},
	[ATTR_PNO_PASSPOINT_LIST_PARAM_NUM] = {.type = NLA_U32},
	[ATTR_PNO_PASSPOINT_LIST_PARAM_NETWORK_ARRAY] = {.type = NLA_NESTED},
	[ATTR_PNO_PASSPOINT_NETWORK_PARAM_ID] = {.type = NLA_U32},
	[ATTR_PNO_PASSPOINT_NETWORK_PARAM_REALM] = {.type = NLA_STRING},
	[ATTR_PNO_PASSPOINT_NETWORK_PARAM_ROAM_CNSRTM_ID] = {.type = NLA_BINARY},
	[ATTR_PNO_PASSPOINT_NETWORK_PARAM_ROAM_PLMN] = {.type = NLA_BINARY},
	[ATTR_EPNO_MIN5GHZ_RSSI] = {.type = NLA_U32},
	[ATTR_EPNO_MIN24GHZ_RSSI] = {.type = NLA_U32},
	[ATTR_EPNO_INITIAL_SCORE_MAX] = {.type = NLA_U32},
	[ATTR_EPNO_CURRENT_CONNECTION_BONUS] = {.type = NLA_U32},
	[ATTR_EPNO_SAME_NETWORK_BONUS] = {.type = NLA_U32},
	[ATTR_EPNO_SECURE_BONUS] = {.type = NLA_U32},
	[ATTR_EPNO_BAND5GHZ_BONUS] = {.type = NLA_U32},
	[ATTR_PNO_SET_LIST_PARAM_NUM_NETWORKS] = {.type = NLA_U32},
	[ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORKS_LIST] = {.type = NLA_NESTED},
	[ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_SSID] = {.type = NLA_BINARY,
						       .len = IEEE80211_MAX_SSID_LEN},
	[ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_FLAGS] = {.type = NLA_U8},
	[ATTR_PNO_SET_LIST_PARAM_EPNO_NETWORK_AUTH_BIT] = {.type = NLA_U8},
};

enum qca_wlan_vendor_attr_get_logger_features {
	ATTR_LOGGER_INVALID = 0,
	ATTR_LOGGER_SUPPORTED = 1,
	/* keep last */
	ATTR_LOGGER_AFTER_LAST,
	ATTR_LOGGER_MAX =
	ATTR_LOGGER_AFTER_LAST - 1,
};

static const struct nla_policy
	get_logger_features_policy[ATTR_LOGGER_MAX + 1] = {
	[ATTR_LOGGER_SUPPORTED] = {.type = NLA_U32},
};

enum vendor_gscan_wifi_band {
	VENDOR_GSCAN_WIFI_BAND_UNSPECIFIED,
	VENDOR_GSCAN_WIFI_BAND_BG = 1,
	VENDOR_GSCAN_WIFI_BAND_A = 2,
	VENDOR_GSCAN_WIFI_BAND_A_DFS = 4,
	VENDOR_GSCAN_WIFI_BAND_A_WITH_DFS = 6,
	VENDOR_GSCAN_WIFI_BAND_ABG = 3,
	VENDOR_GSCAN_WIFI_BAND_ABG_WITH_DFS = 7,
};

enum vendor_gscan_wifi_event {
	VENDOR_GSCAN_WIFI_EVT_RESERVED1,
	VENDOR_GSCAN_WIFI_EVT_RESERVED2,
	VENDOR_GSCAN_WIFI_EVT_SIGNIFICANT_CHANGE_RESULTS,
	VENDOR_GSCAN_WIFI_EVT_HOTLIST_RESULTS_FOUND,
	VENDOR_GSCAN_WIFI_EVT_SCAN_RESULTS_AVAILABLE,
	VENDOR_GSCAN_WIFI_EVT_FULL_SCAN_RESULTS,
	VENDOR_GSCAN_WIFI_EVT_RTT_EVENT_COMPLETE,
	VENDOR_GSCAN_WIFI_EVT_COMPLETE_SCAN,
	VENDOR_GSCAN_WIFI_EVT_HOTLIST_RESULTS_LOST,
	VENDOR_GSCAN_WIFI_EVT_EPNO_EVENT,
	VENDOR_GSCAN_WIFI_EVT_GOOGLE_DEBUG_RING,
	VENDOR_GSCAN_WIFI_EVT_GOOGLE_DEBUG_MEM_DUMP,
	VENDOR_GSCAN_WIFI_EVT_ANQPO_HOTSPOT_MATCH,
	VENDOR_GSCAN_WIFI_EVT_GOOGLE_RSSI_MONITOR,
	VENDOR_GSCAN_WIFI_EVT_SSID_HOTLIST_RESULTS_FOUND,
	VENDOR_GSCAN_WIFI_EVT_SSID_HOTLIST_RESULTS_LOST,

};

#define REPORT_EVENTS_BUFFER_FULL			0
#define REPORT_EVENTS_EACH_SCAN				BIT(0)
#define REPORT_EVENTS_FULL_RESULTS			BIT(1)
#define REPORT_EVENTS_NO_BATCH				BIT(2)
#define REPORT_EVENTS_HOTLIST_RESULTS_FOUND		BIT(3)
#define REPORT_EVENTS_HOTLIST_RESULTS_LOST		BIT(4)
#define REPORT_EVENTS_SIGNIFICANT_CHANGE		BIT(5)
#define REPORT_EVENTS_EPNO				BIT(6)
#define REPORT_EVENTS_ANQPO_HOTSPOT_MATCH		BIT(7)
#define REPORT_EVENTS_SSID_HOTLIST_RESULTS_FOUND	BIT(8)
#define REPORT_EVENTS_SSID_HOTLIST_RESULTS_LOST		BIT(9)

enum vendor_gscan_event {
	VENDOR_GSCAN_EVT_BUFFER_FULL,
	VENDOR_GSCAN_EVT_COMPLETE,
};

struct cmd_gscan_rsp_header {
	u8 subcmd;
	u8 status;
	u16 data_len;
} __packed;

#define MAX_HOTLIST_APS 16
struct sprd_gscan_hotlist_results {
	int req_id;
	u8 flags;
	int num_results;
	struct gscan_result results[MAX_HOTLIST_APS];
};

struct ssid_threshold_param {
	unsigned char ssid[IEEE80211_MAX_SSID_LEN];	/* AP SSID*/
	s8 low;			/* low threshold*/
	s8 high;		/* low threshold*/
};

struct wifi_ssid_hotlist_params {
	u8 lost_ssid_sample_size;/* number of samples to confirm AP loss*/
	u8 num_ssid;		/* number of hotlist APs*/
	struct ssid_threshold_param ssid[MAX_HOTLIST_APS];	/* hotlist APs*/
};

struct ap_threshold_param {
	unsigned char bssid[6];	/* AP BSSID*/
	s8 low;			/* low threshold*/
	s8 high;		/* low threshold*/
};

struct wifi_bssid_hotlist_params {
	u8 lost_ap_sample_size;	/* number of samples to confirm AP loss*/
	u8 num_bssid;		/* number of hotlist APs*/
	struct ap_threshold_param ap[MAX_HOTLIST_APS];	/* hotlist APs*/
};

#define MAX_SIGNIFICANT_CHANGE_APS	16
struct wifi_significant_change_params {
	u8 rssi_sample_size;	/*number of samples for averaging RSSI */
	u8 lost_ap_sample_size;	/*number of samples to confirm AP loss*/
	u8 min_breaching;	/*number of APs breaching threshold */
	u8 num_bssid;		/*max 64*/
	struct ap_threshold_param ap[MAX_SIGNIFICANT_CHANGE_APS];
};

struct significant_change_info {
	unsigned char bssid[6];	/* AP BSSID*/
	u8 channel;		/*channel frequency in MHz*/
	u8 num_rssi;		/*number of rssi samples*/
	s8 rssi[3];		/*RSSI history in db, here fixed 3*/
} __packed;

struct sprd_significant_change_result {
	int req_id;
	u8 flags;
	int num_results;
	struct significant_change_info results[MAX_SIGNIFICANT_CHANGE_APS];
};

struct wifi_epno_network {
	u8 ssid[32 + 1];
	/* threshold for considering this SSID as found required
	 *granularity for this threshold is 4dBm to 8dBm
	 */
	/* unsigned char rssi_threshold; */
	u8 flags;		/* WIFI_PNO_FLAG_XXX*/
	u8 auth_bit_field;	/* auth bit field for matching WPA IE*/
} __packed;

#define MAX_EPNO_NETWORKS	16
struct wifi_epno_params {
	u64 boot_time;
	u8 request_id;
	/* minimum 5GHz RSSI for a BSSID to be considered */
	s8 min5ghz_rssi;

	/* minimum 2.4GHz RSSI for a BSSID to be considered */
	s8 min24ghz_rssi;

	/* the maximum score that a network can have before bonuses */
	s8 initial_score_max;

	/* only report when there is a network's score this much higher */
	/* than the current connection. */
	s8 current_connection_bonus;

	/* score bonus for all networks with the same network flag */
	s8 same_network_bonus;

	/* score bonus for networks that are not open */
	s8 secure_bonus;

	/* 5GHz RSSI score bonus (applied to all 5GHz networks) */
	s8 band5ghz_bonus;

	/* number of wifi_epno_network objects */
	s8 num_networks;

	/* PNO networks */
	struct wifi_epno_network networks[MAX_EPNO_NETWORKS];
} __packed;

struct epno_results {
	u64 boot_time;
	u8 request_id;
	u8 nr_scan_results;
	struct gscan_result results[0];
} __packed;

struct wifi_ssid {
	char ssid[32 + 1];	/* null terminated*/
};

struct wifi_roam_params {
	/* Lazy roam parameters
	 * A_band_XX parameters are applied to 5GHz BSSIDs when comparing with
	 * a 2.4GHz BSSID they may not be applied when comparing two 5GHz BSSIDs
	 */

	/* RSSI threshold above which 5GHz RSSI is favored*/
	int A_band_boost_threshold;

	/* RSSI threshold below which 5GHz RSSI is penalized*/
	int A_band_penalty_threshold;

	/* factor by which 5GHz RSSI is boosted*/
	/*boost=RSSI_measured-5GHz_boost_threshold)*5GHz_boost_factor*/
	int A_band_boost_factor;

	/* factor by which 5GHz RSSI is penalized*/
	/*penalty=(5GHz_penalty_factor-RSSI_measured)*5GHz_penalty_factor*/
	int A_band_penalty_factor;

	/* maximum boost that can be applied to a 5GHz RSSI*/
	int A_band_max_boost;

	/* Hysteresis: ensuring the currently associated BSSID is favored*/
	/*so as to prevent ping-pong situations,boost applied to current BSSID*/
	int lazy_roam_hysteresis;

	/* Alert mode enable, i.e. configuring when firmware enters alert mode*/
	/* RSSI below which "Alert" roam is enabled*/
	int alert_roam_rssi_trigger;
};

struct wifi_bssid_preference {
	unsigned char bssid[6];
	/* modifier applied to the RSSI of the BSSID for
	 *the purpose of comparing it with other roam candidate
	 */
	int rssi_modifier;
};

#define MAX_PREFER_APS	16
struct wifi_bssid_preference_params {
	int num_bssid;		/* number of preference APs*/
	/* preference APs*/
	struct wifi_bssid_preference pref_ap[MAX_PREFER_APS];
};

struct v_MACADDR_t {
	u8 bytes[3];
};

struct wifi_passpoint_network {
	/*identifier of this network block, report this in event*/
	u8 id;
	/*null terminated UTF8 encoded realm, 0 if unspecified*/
	char realm[256];
	/*roaming consortium ids to match, 0s if unspecified*/
	s64 roaming_ids[16];
	/*mcc/mnc combination as per rules, 0s if unspecified*/
	unsigned char plmn[3];
};

enum vendor_softap_sae_type {
	VENDOR_SAE_INVALID = 0,
	VENDOR_SAE_ENTRY = 1,
	VENDOR_SAE_PASSWORD = 2,
	VENDOR_SAE_IDENTIFIER = 3,
	VENDOR_SAE_PEER_ADDR = 4,
	VENDOR_SAE_VLAN_ID = 5,
	VENDOR_SAE_GROUP_ID = 6,
	VENDOR_SAE_ACT = 7,
	VENDOR_SAE_PWD = 8,
	VENDOR_SAE_END = 0xFF,
	VENDOR_SAE_MAX =
	    VENDOR_SAE_PWD,
};

static const struct
nla_policy vendor_sae_policy[VENDOR_SAE_MAX + 1] = {
	[VENDOR_SAE_ENTRY] = { .type = NLA_NESTED },
	[VENDOR_SAE_PASSWORD] = { .type = NLA_STRING },
	[VENDOR_SAE_IDENTIFIER] = { .type = NLA_STRING },
	[VENDOR_SAE_PEER_ADDR] = { .type = NLA_STRING },
	[VENDOR_SAE_VLAN_ID] = { .type = NLA_U32 },
	[VENDOR_SAE_GROUP_ID] = { .type = NLA_STRING },
	[VENDOR_SAE_ACT] = { .type = NLA_U32 },
	[VENDOR_SAE_PWD] = { .type = NLA_STRING },
};

enum vendor_offloaded_packets_sending_control {
	VENDOR_OFFLOADED_PACKETS_SENDING_CONTROL_INVALID = 0,
	VENDOR_OFFLOADED_PACKETS_SENDING_START,
	VENDOR_OFFLOADED_PACKETS_SENDING_STOP
};

enum vendor_attr_offloaded_packets {
	ATTR_OFFLOADED_PACKETS_INVALID = 0,
	ATTR_OFFLOADED_PACKETS_SENDING_CONTROL,
	ATTR_OFFLOADED_PACKETS_REQUEST_ID,
	ATTR_OFFLOADED_PACKETS_IP_PACKET_DATA,
	ATTR_OFFLOADED_PACKETS_SRC_MAC_ADDR,
	ATTR_OFFLOADED_PACKETS_DST_MAC_ADDR,
	ATTR_OFFLOADED_PACKETS_PERIOD,
	ATTR_OFFLOADED_PACKETS_ETHER_PROTO_TYPE,
	ATTR_OFFLOADED_PACKETS_AFTER_LAST,
	ATTR_OFFLOADED_PACKETS_MAX = ATTR_OFFLOADED_PACKETS_AFTER_LAST - 1,
};

static const struct
nla_policy offloaded_packets_policy[ATTR_OFFLOADED_PACKETS_MAX + 1] = {
	[ATTR_OFFLOADED_PACKETS_SENDING_CONTROL] = { .type = NLA_U32 },
	[ATTR_OFFLOADED_PACKETS_REQUEST_ID] = { .type = NLA_U32 },
	[ATTR_OFFLOADED_PACKETS_IP_PACKET_DATA] = { .type = NLA_BINARY },
	[ATTR_OFFLOADED_PACKETS_SRC_MAC_ADDR] = { .type = NLA_BINARY, .len = ETH_ALEN },
	[ATTR_OFFLOADED_PACKETS_DST_MAC_ADDR] = { .type = NLA_BINARY, .len = ETH_ALEN },
	[ATTR_OFFLOADED_PACKETS_PERIOD] = { .type = NLA_U32 },
	[ATTR_OFFLOADED_PACKETS_ETHER_PROTO_TYPE] = { .type = NLA_U16 },
};

struct wmm_ac_stat {
	u32 tx_mpdu;
	u32 rx_mpdu;
	u32 mpdu_lost;
	u32 retries;
} __packed;

struct sprd_llstat_radio {
	int rssi_mgmt;
	u32 bcn_rx_cnt;
	struct wmm_ac_stat ac[WIFI_AC_MAX];
};

struct sprd_gscan_cached_results {
	int scan_id;
	u8 flags;
	int num_results;
	struct gscan_result results[MAX_AP_CACHE_PER_SCAN];
};
#endif
