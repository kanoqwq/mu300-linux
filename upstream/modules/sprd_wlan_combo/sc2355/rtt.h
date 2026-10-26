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

#ifndef __RTT_H__
#define __RTT_H__

#include "common/vendor.h"

#define RTT_MAX_CONFIG		5
#define LCI_MAX_LEN		200
#define LCR_MAX_LEN		200
#define RTT_MAX_RESULT_SUPPORT	100

/* FTM/indoor location subcommands */
enum rtt_subcmds {
	SPRD_NL80211_SUBCMD_LOC_GET_CAPA = 128,
	SPRD_NL80211_SUBCMD_RTT_START_SESSION = 129,
	SPRD_NL80211_SUBCMD_RTT_ABORT_SESSION = 130,
	SPRD_NL80211_SUBCMD_RTT_MEAS_RESULT = 131,
	SPRD_NL80211_SUBCMD_RTT_SESSION_DONE = 132,
	SPRD_NL80211_SUBCMD_RTT_CFG_RESPONDER = 133,
	SPRD_NL80211_SUBCMD_AOA_MEAS = 134,
	SPRD_NL80211_SUBCMD_AOA_ABORT_MEAS = 135,
	SPRD_NL80211_SUBCMD_AOA_MEAS_RESULT = 136,
};

/* enum rtt_attr_loc - attributes for FTM and AOA commands
 *
 * @SPRD_ATTR_RTT_SESSION_COOKIE: Session cookie, specified in
 *  %SPRD_NL80211_SUBCMD_RTT_START_SESSION. It will be provided by driver
 *  events and can be used to identify events targeted for this session.
 * @SPRD_ATTR_LOC_CAPA: Nested attribute containing extra
 *  FTM/AOA capabilities, returned by %SPRD_NL80211_SUBCMD_LOC_GET_CAPA.
 *  see %enum rtt_attr_loc_capa.
 * @SPRD_ATTR_RTT_MEAS_PEERS: array of nested attributes
 *  containing information about each peer in measurement session
 *  request. See %enum sprd_vendor_attr_peer_info for supported
 *  attributes for each peer
 * @SPRD_ATTR_RTT_PEER_RESULTS: nested attribute containing
 *  measurement results for a peer. reported by the
 *  %SPRD_NL80211_SUBCMD_RTT_MEAS_RESULT event.
 *  See %enum sprd_vendor_attr_peer_result for list of supported
 *  attributes.
 * @SPRD_ATTR_RTT_RESPONDER_ENABLE: flag attribute for
 *  enabling or disabling responder functionality.
 * @SPRD_ATTR_RTT_LCI: used in the
 *  %SPRD_NL80211_SUBCMD_RTT_CFG_RESPONDER command in order to
 *  specify the LCI report that will be sent by the responder during
 *  a measurement exchange. The format is defined in IEEE P802.11-REVmc/D5.0,
 *  9.4.2.22.10
 * @SPRD_ATTR_RTT_LCR: provided with the
 *  %SPRD_NL80211_SUBCMD_RTT_CFG_RESPONDER command in order to
 *  specify the location civic report that will be sent by the responder during
 *  a measurement exchange. The format is defined in IEEE P802.11-REVmc/D5.0,
 *  9.4.2.22.13
 * @SPRD_ATTR_LOC_SESSION_STATUS: session/measurement completion
 *  status code, reported in %SPRD_NL80211_SUBCMD_RTT_SESSION_DONE
 *  and %SPRD_NL80211_SUBCMD_AOA_MEAS_RESULT
 * @SPRD_ATTR_RTT_INITIAL_TOKEN: initial dialog token used
 *  by responder (0 if not specified)
 * @SPRD_ATTR_AOA_TYPE: AOA measurement type. Requested in
 *  %SPRD_NL80211_SUBCMD_AOA_MEAS and optionally in
 *  %SPRD_NL80211_SUBCMD_RTT_START_SESSION if AOA measurements
 *  are needed as part of an FTM session.
 *  Reported by SPRD_NL80211_SUBCMD_AOA_MEAS_RESULT.
 *  See enum rtt_attr_aoa_type.
 * @SPRD_ATTR_LOC_ANTENNA_ARRAY_MASK: bit mask indicating
 *  which antenna arrays were used in location measurement.
 *  Reported in %SPRD_NL80211_SUBCMD_RTT_MEAS_RESULT and
 *  %SPRD_NL80211_SUBCMD_AOA_MEAS_RESULT
 * @SPRD_ATTR_AOA_MEAS_RESULT: AOA measurement data.
 *  Its contents depends on the AOA type and antenna array mask:
 *  %SPRD_ATTR_AOA_TYPE_TOP_CIR_PHASE: array of U16 values,
 *  phase of the strongest CIR path for each antenna in the measured
 *  array(s).
 *  %SPRD_ATTR_AOA_TYPE_TOP_CIR_PHASE_AMP: array of 2 U16
 *  values, phase and amplitude of the strongest CIR path for each
 *  antenna in the measured array(s)
 * @SPRD_ATTR_FREQ: Frequency where peer is listening, in MHz.
 *  Unsigned 32 bit value.
 */
enum rtt_attr_loc {
	/* we reuse these attributes */
	SPRD_ATTR_MAC_ADDR = 6,
	SPRD_ATTR_PAD = 13,
	SPRD_ATTR_RTT_SESSION_COOKIE = 14,
	SPRD_ATTR_LOC_CAPA = 15,
	SPRD_ATTR_RTT_MEAS_PEERS = 16,
	SPRD_ATTR_RTT_MEAS_PEER_RESULTS = 17,
	SPRD_ATTR_RTT_RESPONDER_ENABLE = 18,
	SPRD_ATTR_RTT_LCI = 19,
	SPRD_ATTR_RTT_LCR = 20,
	SPRD_ATTR_LOC_SESSION_STATUS = 21,
	SPRD_ATTR_RTT_INITIAL_TOKEN = 22,
	SPRD_ATTR_AOA_TYPE = 23,
	SPRD_ATTR_LOC_ANTENNA_ARRAY_MASK = 24,
	SPRD_ATTR_AOA_MEAS_RESULT = 25,
	SPRD_ATTR_FREQ = 28,
	/* keep last */
	SPRD_ATTR_LOC_AFTER_LAST,
	SPRD_ATTR_LOC_MAX = SPRD_ATTR_LOC_AFTER_LAST - 1,
};

/* enum rtt_attr_loc_capa - indoor location capabilities
 *
 * @SPRD_ATTR_LOC_CAPA_FLAGS: various flags. See
 *  %enum rtt_attr_loc_capa_flags
 * @SPRD_ATTR_RTT_CAPA_MAX_NUM_SESSIONS: Maximum number
 *  of measurement sessions that can run concurrently.
 *  Default is one session (no session concurrency)
 * @SPRD_ATTR_RTT_CAPA_MAX_NUM_PEERS: The total number of unique
 *  peers that are supported in running sessions. For example,
 *  if the value is 8 and maximum number of sessions is 2, you can
 *  have one session with 8 unique peers, or 2 sessions with 4 unique
 *  peers each, and so on.
 * @SPRD_ATTR_RTT_CAPA_MAX_NUM_BURSTS_EXP: Maximum number
 *  of bursts per peer, as an exponent (2^value). Default is 0,
 *  meaning no multi-burst support.
 * @SPRD_ATTR_RTT_CAPA_MAX_MEAS_PER_BURST: Maximum number
 *  of measurement exchanges allowed in a single burst
 * @SPRD_ATTR_AOA_CAPA_SUPPORTED_TYPES: Supported AOA measurement
 *  types. A bit mask (unsigned 32 bit value), each bit corresponds
 *  to an AOA type as defined by %enum qca_vendor_attr_aoa_type.
 */
enum rtt_attr_loc_capa {
	SPRD_ATTR_LOC_CAPA_INVALID,
	SPRD_ATTR_LOC_CAPA_FLAGS,
	SPRD_ATTR_RTT_CAPA_MAX_NUM_SESSIONS,
	SPRD_ATTR_RTT_CAPA_MAX_NUM_PEERS,
	SPRD_ATTR_RTT_CAPA_MAX_NUM_BURSTS_EXP,
	SPRD_ATTR_RTT_CAPA_MAX_MEAS_PER_BURST,
	SPRD_ATTR_AOA_CAPA_SUPPORTED_TYPES,
	/* keep last */
	SPRD_ATTR_LOC_CAPA_AFTER_LAST,
	SPRD_ATTR_LOC_CAPA_MAX = SPRD_ATTR_LOC_CAPA_AFTER_LAST - 1,
};

/* enum rtt_attr_loc_capa_flags: Indoor location capability flags
 *
 * @SPRD_ATTR_LOC_CAPA_FLAG_RTT_RESPONDER: Set if driver
 *  can be configured as an FTM responder (for example, an AP that
 *  services FTM requests). %SPRD_NL80211_SUBCMD_RTT_CFG_RESPONDER
 *  will be supported if set.
 * @SPRD_ATTR_LOC_CAPA_FLAG_RTT_INITIATOR: Set if driver
 *  can run FTM sessions. %SPRD_NL80211_SUBCMD_RTT_START_SESSION
 *  will be supported if set.
 * @SPRD_ATTR_LOC_CAPA_FLAG_ASAP: Set if FTM responder
 *  supports immediate (ASAP) response.
 * @SPRD_ATTR_LOC_CAPA_FLAG_AOA: Set if driver supports standalone
 *  AOA measurement using %SPRD_NL80211_SUBCMD_AOA_MEAS
 * @SPRD_ATTR_LOC_CAPA_FLAG_AOA_IN_RTT: Set if driver supports
 *  requesting AOA measurements as part of an FTM session.
 */
enum rtt_attr_loc_capa_flags {
	SPRD_ATTR_LOC_CAPA_FLAG_RTT_RESPONDER = 1 << 0,
	SPRD_ATTR_LOC_CAPA_FLAG_RTT_INITIATOR = 1 << 1,
	SPRD_ATTR_LOC_CAPA_FLAG_ASAP = 1 << 2,
	SPRD_ATTR_LOC_CAPA_FLAG_AOA = 1 << 3,
	SPRD_ATTR_LOC_CAPA_FLAG_AOA_IN_RTT = 1 << 4,
};

/* enum sprd_vendor_attr_peer_info: information about
 *  a single peer in a measurement session.
 *
 * @SPRD_ATTR_RTT_PEER_MAC_ADDR: The MAC address of the peer.
 * @SPRD_ATTR_RTT_PEER_MEAS_FLAGS: Various flags related
 *  to measurement. See %enum rtt_attr_peer_meas_flags.
 * @SPRD_ATTR_RTT_PEER_MEAS_PARAMS: Nested attribute of
 *  FTM measurement parameters, as specified by IEEE P802.11-REVmc/D7.0,
 *  9.4.2.167. See %enum rtt_attr_meas_param for
 *  list of supported attributes.
 * @SPRD_ATTR_RTT_PEER_SECURE_TOKEN_ID: Initial token ID for
 *  secure measurement
 * @SPRD_ATTR_RTT_PEER_AOA_BURST_PERIOD: Request AOA
 *  measurement every _value_ bursts. If 0 or not specified,
 *  AOA measurements will be disabled for this peer.
 * @SPRD_ATTR_RTT_PEER_FREQ: Frequency in MHz where
 *  peer is listening. Optional; if not specified, use the
 *  entry from the kernel scan results cache.
 */
enum rtt_attr_peer_info {
	SPRD_ATTR_RTT_PEER_INVALID,
	SPRD_ATTR_RTT_PEER_MAC_ADDR,
	SPRD_ATTR_RTT_PEER_MEAS_FLAGS,
	SPRD_ATTR_RTT_PEER_MEAS_PARAMS,
	SPRD_ATTR_RTT_PEER_SECURE_TOKEN_ID,
	SPRD_ATTR_RTT_PEER_AOA_BURST_PERIOD,
	SPRD_ATTR_RTT_PEER_FREQ,
	/* keep last */
	SPRD_ATTR_RTT_PEER_AFTER_LAST,
	SPRD_ATTR_RTT_PEER_MAX = SPRD_ATTR_RTT_PEER_AFTER_LAST - 1,
};

/* enum rtt_attr_peer_meas_flags: Measurement request flags,
 *  per-peer
 * @SPRD_ATTR_RTT_PEER_MEAS_FLAG_ASAP: If set, request
 *  immediate (ASAP) response from peer
 * @SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCI: If set, request
 *  LCI report from peer. The LCI report includes the absolute
 *  location of the peer in "official" coordinates (similar to GPS).
 *  See IEEE P802.11-REVmc/D7.0, 11.24.6.7 for more information.
 * @SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCR: If set, request
 *  Location civic report from peer. The LCR includes the location
 *  of the peer in free-form format. See IEEE P802.11-REVmc/D7.0,
 *  11.24.6.7 for more information.
 * @SPRD_ATTR_RTT_PEER_MEAS_FLAG_SECURE: If set,
 *  request a secure measurement.
 *  %SPRD_ATTR_RTT_PEER_SECURE_TOKEN_ID must also be provided.
 */
enum rtt_attr_peer_meas_flags {
	SPRD_ATTR_RTT_PEER_MEAS_FLAG_ASAP = 1 << 0,
	SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCI = 1 << 1,
	SPRD_ATTR_RTT_PEER_MEAS_FLAG_LCR = 1 << 2,
	SPRD_ATTR_RTT_PEER_MEAS_FLAG_SECURE = 1 << 3,
};

/* enum rtt_attr_meas_param: Measurement parameters
 *
 * @SPRD_ATTR_RTT_PARAM_MEAS_PER_BURST: Number of measurements
 *  to perform in a single burst.
 * @SPRD_ATTR_RTT_PARAM_NUM_BURSTS_EXP: Number of bursts to
 *  perform, specified as an exponent (2^value)
 * @SPRD_ATTR_RTT_PARAM_BURST_DURATION: Duration of burst
 *  instance, as specified in IEEE P802.11-REVmc/D7.0, 9.4.2.167
 * @SPRD_ATTR_RTT_PARAM_BURST_PERIOD: Time between bursts,
 *  as specified in IEEE P802.11-REVmc/D7.0, 9.4.2.167. Must
 *  be larger than %SPRD_ATTR_RTT_PARAM_BURST_DURATION
 */
enum rtt_attr_meas_param {
	SPRD_ATTR_RTT_PARAM_INVALID,
	SPRD_ATTR_RTT_PARAM_MEAS_PER_BURST,
	SPRD_ATTR_RTT_PARAM_NUM_BURSTS_EXP,
	SPRD_ATTR_RTT_PARAM_BURST_DURATION,
	SPRD_ATTR_RTT_PARAM_BURST_PERIOD,
	/* keep last */
	SPRD_ATTR_RTT_PARAM_AFTER_LAST,
	SPRD_ATTR_RTT_PARAM_MAX = SPRD_ATTR_RTT_PARAM_AFTER_LAST - 1,
};

/* enum rtt_attr_peer_result: Per-peer results
 *
 * @SPRD_ATTR_RTT_PEER_RES_MAC_ADDR: MAC address of the reported
 *  peer
 * @SPRD_ATTR_RTT_PEER_RES_STATUS: Status of measurement
 *  request for this peer.
 *  See %enum rtt_attr_peer_result_status
 * @SPRD_ATTR_RTT_PEER_RES_FLAGS: Various flags related
 *  to measurement results for this peer.
 *  See %enum rtt_attr_peer_result_flags
 * @SPRD_ATTR_RTT_PEER_RES_VALUE_SECONDS: Specified when
 *  request failed and peer requested not to send an additional request
 *  for this number of seconds.
 * @SPRD_ATTR_RTT_PEER_RES_LCI: LCI report when received
 *  from peer. In the format specified by IEEE P802.11-REVmc/D7.0,
 *  9.4.2.22.10
 * @SPRD_ATTR_RTT_PEER_RES_LCR: Location civic report when
 *  received from peer.In the format specified by IEEE P802.11-REVmc/D7.0,
 *  9.4.2.22.13
 * @SPRD_ATTR_RTT_PEER_RES_MEAS_PARAMS: Reported when peer
 *  overridden some measurement request parameters. See
 *  enum rtt_attr_meas_param.
 * @SPRD_ATTR_RTT_PEER_RES_AOA_MEAS: AOA measurement
 *  for this peer. Same contents as %SPRD_ATTR_AOA_MEAS_RESULT
 * @SPRD_ATTR_RTT_PEER_RES_MEAS: Array of measurement
 *  results. Each entry is a nested attribute defined
 *  by enum rtt_attr_meas.
 */
enum rtt_attr_peer_result {
	SPRD_ATTR_RTT_PEER_RES_INVALID,
	SPRD_ATTR_RTT_PEER_RES_MAC_ADDR,
	SPRD_ATTR_RTT_PEER_RES_STATUS,
	SPRD_ATTR_RTT_PEER_RES_FLAGS,
	SPRD_ATTR_RTT_PEER_RES_VALUE_SECONDS,
	SPRD_ATTR_RTT_PEER_RES_LCI,
	SPRD_ATTR_RTT_PEER_RES_LCR,
	SPRD_ATTR_RTT_PEER_RES_MEAS_PARAMS,
	SPRD_ATTR_RTT_PEER_RES_AOA_MEAS,
	SPRD_ATTR_RTT_PEER_RES_MEAS,
	/* keep last */
	SPRD_ATTR_RTT_PEER_RES_AFTER_LAST,
	SPRD_ATTR_RTT_PEER_RES_MAX = SPRD_ATTR_RTT_PEER_RES_AFTER_LAST - 1,
};

/* enum rtt_attr_peer_result_status
 *
 * @SPRD_ATTR_RTT_PEER_RES_STATUS_OK: Request sent ok and results
 *  will be provided. Peer may have overridden some measurement parameters,
 *  in which case overridden parameters will be report by
 *  %SPRD_ATTR_RTT_PEER_RES_MEAS_PARAMS attribute
 * @SPRD_ATTR_RTT_PEER_RES_STATUS_INCAPABLE: Peer is incapable
 *  of performing the measurement request. No more results will be sent
 *  for this peer in this session.
 * @SPRD_ATTR_RTT_PEER_RES_STATUS_FAILED: Peer reported request
 *  failed, and requested not to send an additional request for number
 *  of seconds specified by %SPRD_ATTR_RTT_PEER_RES_VALUE_SECONDS
 *  attribute.
 * @SPRD_ATTR_RTT_PEER_RES_STATUS_INVALID: Request validation
 *  failed. Request was not sent over the air.
 */
enum rtt_attr_peer_result_status {
	SPRD_ATTR_RTT_PEER_RES_STATUS_OK,
	SPRD_ATTR_RTT_PEER_RES_STATUS_INCAPABLE,
	SPRD_ATTR_RTT_PEER_RES_STATUS_FAILED,
	SPRD_ATTR_RTT_PEER_RES_STATUS_INVALID,
};

/* enum rtt_attr_peer_result_flags : Various flags
 *  for measurement result, per-peer
 *
 * @SPRD_ATTR_RTT_PEER_RES_FLAG_DONE: If set,
 *  measurement completed for this peer. No more results will be reported
 *  for this peer in this session.
 */
enum rtt_attr_peer_result_flags {
	SPRD_ATTR_RTT_PEER_RES_FLAG_DONE = 1 << 0,
};

/* enum qca_vendor_attr_loc_session_status: Session completion status code
 *
 * @SPRD_ATTR_LOC_SESSION_STATUS_OK: Session completed
 *  successfully.
 * @SPRD_ATTR_LOC_SESSION_STATUS_ABORTED: Session aborted
 *  by request
 * @SPRD_ATTR_LOC_SESSION_STATUS_INVALID: Session request
 *  was invalid and was not started
 * @SPRD_ATTR_LOC_SESSION_STATUS_FAILED: Session had an error
 *  and did not complete normally (for example out of resources)
 *
 */
enum rtt_attr_loc_session_status {
	SPRD_ATTR_LOC_SESSION_STATUS_OK,
	SPRD_ATTR_LOC_SESSION_STATUS_ABORTED,
	SPRD_ATTR_LOC_SESSION_STATUS_INVALID,
	SPRD_ATTR_LOC_SESSION_STATUS_FAILED,
};

/* enum rtt_attr_meas: Single measurement data
 *
 * @SPRD_ATTR_RTT_MEAS_T1: Time of departure(TOD) of FTM packet as
 *  recorded by responder, in picoseconds.
 *  See IEEE P802.11-REVmc/D7.0, 11.24.6.4 for more information.
 * @SPRD_ATTR_RTT_MEAS_T2: Time of arrival(TOA) of FTM packet at
 *  initiator, in picoseconds.
 *  See IEEE P802.11-REVmc/D7.0, 11.24.6.4 for more information.
 * @SPRD_ATTR_RTT_MEAS_T3: TOD of ACK packet as recorded by
 *  initiator, in picoseconds.
 *  See IEEE P802.11-REVmc/D7.0, 11.24.6.4 for more information.
 * @SPRD_ATTR_RTT_MEAS_T4: TOA of ACK packet at
 *  responder, in picoseconds.
 *  See IEEE P802.11-REVmc/D7.0, 11.24.6.4 for more information.
 * @SPRD_ATTR_RTT_MEAS_RSSI: RSSI (signal level) as recorded
 *  during this measurement exchange. Optional and will be provided if
 *  the hardware can measure it.
 * @SPRD_ATTR_RTT_MEAS_TOD_ERR: TOD error reported by
 *  responder. Not always provided.
 *  See IEEE P802.11-REVmc/D7.0, 9.6.8.33 for more information.
 * @SPRD_ATTR_RTT_MEAS_TOA_ERR: TOA error reported by
 *  responder. Not always provided.
 *  See IEEE P802.11-REVmc/D7.0, 9.6.8.33 for more information.
 * @SPRD_ATTR_RTT_MEAS_INITIATOR_TOD_ERR: TOD error measured by
 *  initiator. Not always provided.
 *  See IEEE P802.11-REVmc/D7.0, 9.6.8.33 for more information.
 * @SPRD_ATTR_RTT_MEAS_INITIATOR_TOA_ERR: TOA error measured by
 *  initiator. Not always provided.
 *  See IEEE P802.11-REVmc/D7.0, 9.6.8.33 for more information.
 * @SPRD_ATTR_RTT_MEAS_PAD: Dummy attribute for padding.
 */
enum rtt_attr_meas {
	SPRD_ATTR_RTT_MEAS_INVALID,
	SPRD_ATTR_RTT_MEAS_T1,
	SPRD_ATTR_RTT_MEAS_T2,
	SPRD_ATTR_RTT_MEAS_T3,
	SPRD_ATTR_RTT_MEAS_T4,
	SPRD_ATTR_RTT_MEAS_RSSI,
	SPRD_ATTR_RTT_MEAS_TOD_ERR,
	SPRD_ATTR_RTT_MEAS_TOA_ERR,
	SPRD_ATTR_RTT_MEAS_INITIATOR_TOD_ERR,
	SPRD_ATTR_RTT_MEAS_INITIATOR_TOA_ERR,
	SPRD_ATTR_RTT_MEAS_PAD,
	/* keep last */
	SPRD_ATTR_RTT_MEAS_AFTER_LAST,
	SPRD_ATTR_RTT_MEAS_MAX = SPRD_ATTR_RTT_MEAS_AFTER_LAST - 1,
};

enum rtt_attr_aoa_type {
	SPRD_ATTR_AOA_TYPE_TOP_CIR_PHASE,
	SPRD_ATTR_AOA_TYPE_TOP_CIR_PHASE_AMP,
	SPRD_ATTR_AOA_TYPE_MAX,
};

/* vendor event indices, used from both cfg80211.c and ftm.c */
enum rtt_events_index {
	SPRD_EVENT_RTT_MEAS_RESULT_INDEX = 64,
	SPRD_EVENT_RTT_SESSION_DONE_INDEX,
};

/* vendor rtt cap */
enum rtt_cap_index {
	SPRD_RTT_ONE_SIDED_SUPPORTED,
	SPRD_RTT_SUPPORTED,
	SPRD_LCI_SUPPORT,
	SPRD_LCR_SUPPORT,
	SPRD_PREAMBLE_SUPPORT,
	SPRD_BW_SUPPORT,
	SPRD_RESPONDER_SUPPORTED,
	SPRD_MC_version,
};

enum rtt_attribute {
	SPRD_RTT_ATTRIBUTE_TARGET_CNT = 0,
	SPRD_RTT_ATTRIBUTE_TARGET_INFO,
	SPRD_RTT_ATTRIBUTE_TARGET_MAC,
	SPRD_RTT_ATTRIBUTE_TARGET_TYPE,
	SPRD_RTT_ATTRIBUTE_TARGET_PEER,
	SPRD_RTT_ATTRIBUTE_TARGET_CHAN,
	SPRD_RTT_ATTRIBUTE_TARGET_PERIOD,
	SPRD_RTT_ATTRIBUTE_TARGET_NUM_BURST,
	SPRD_RTT_ATTRIBUTE_TARGET_NUM_FTM_BURST,
	SPRD_RTT_ATTRIBUTE_TARGET_NUM_RETRY_FTM,
	SPRD_RTT_ATTRIBUTE_TARGET_NUM_RETRY_FTMR,
	SPRD_RTT_ATTRIBUTE_TARGET_LCI,
	SPRD_RTT_ATTRIBUTE_TARGET_LCR,
	SPRD_RTT_ATTRIBUTE_TARGET_BURST_DURATION,
	SPRD_RTT_ATTRIBUTE_TARGET_PREAMBLE,
	SPRD_RTT_ATTRIBUTE_TARGET_BW,
	SPRD_RTT_ATTRIBUTE_TARGET_RESPONDER_INFO,
	SPRD_RTT_ATTRIBUTE_MAX
};

enum rtt_result_attribute {
	SPRD_RTT_ATTRIBUTE_RESULT_CNT = 0,
	SPRD_RTT_ATTRIBUTE_RESULT_INFO,
	SPRD_RTT_ATTRIBUTE_RESULT_MAC,
	SPRD_RTT_ATTRIBUTE_RESULT_TYPE,
	SPRD_RTT_ATTRIBUTE_RESULT_PEER,
	SPRD_RTT_ATTRIBUTE_RESULT_CHAN,
	SPRD_RTT_ATTRIBUTE_RESULT_PERIOD,
	SPRD_RTT_ATTRIBUTE_RESULT_NUM_BURST,
	SPRD_RTT_ATTRIBUTE_RESULT_NUM_FTM_BURST,
	SPRD_RTT_ATTRIBUTE_RESULT_NUM_RETRY_FTM,
	SPRD_RTT_ATTRIBUTE_RESULT_NUM_RETRY_FTMR,
	SPRD_RTT_ATTRIBUTE_RESULT_LCI,
	SPRD_RTT_ATTRIBUTE_RESULT_LCR,
	SPRD_RTT_ATTRIBUTE_RESULT_BURST_DURATION,
	SPRD_RTT_ATTRIBUTE_RESULT_PREAMBLE,
	SPRD_RTT_ATTRIBUTE_RESULT_BW,
	SPRD_RTT_ATTRIBUTE_RESULT_RESPONDER_INFO,
	SPRD_RTT_ATTRIBUTE_RESULTS_COMPLETE = 30,
	SPRD_RTT_ATTRIBUTE_RESULTS_PER_TARGET,
	SPRD_RTT_ATTRIBUTE_RESULT_CNT_CNT,
	SPRD_RTT_ATTRIBUTE_RESULT
};

/* measurement parameters. Specified for each peer as part
 * of measurement request, or provided with measurement
 * results for peer in case peer overridden parameters
 */
struct rtt_meas_params {
	u8 meas_per_burst;
	u8 num_of_bursts_exp;
	u8 burst_duration;
	u16 burst_period;
};

/* measurement request for a single peer */
struct rtt_meas_peer_info {
	u8 mac_addr[ETH_ALEN];
	u32 freq;
	u32 flags;
	struct rtt_meas_params params;
	u8 secure_token_id;
};

/* session request, passed to wil_ftm_cfg80211_start_session */
struct rtt_session_request {
	u64 session_cookie;
	u32 n_peers;
	/* keep last, variable size according to n_peers */
	struct rtt_meas_peer_info peers[0];
};

/* single measurement for a peer */
struct rtt_peer_meas {
	u64 t1, t2, t3, t4;
};

/* measurement results for a single peer */
struct rtt_peer_meas_res {
	u8 mac_addr[ETH_ALEN];
	u32 flags;
	u8 status;
	u8 value_seconds;
	bool has_params;
	struct rtt_meas_params params;
	u8 *lci;
	u8 lci_length;
	u8 *lcr;
	u8 lcr_length;
	u32 n_meas;
	/* keep last, variable size according to n_meas */
	struct rtt_peer_meas meas[0];
};

/* RTT Capabilities
 * @rtt_one_sided_supported: if 1-sided rtt data collection is supported
 * @rtt_ftm_supported: if ftm rtt data collection is supported
 * @lci_support: if initiator supports LCI request. Applies to 2-sided RTT
 * @lcr_support: if initiator supports LCR request. Applies to 2-sided RTT
 * @preamble_support: bit mask indicates what preamble is supported by initiator
 * @bw_support: bit mask indicates what BW is supported by initiator
 * @responder_supported: if 11mc responder mode is supported
 * @mc_version: draft 11mc spec version supported by chip.
 *   For instance, version 4.0 should be 40 and version 4.3 should be 43 etc.
 *
 */
struct rtt_capabilities {
	u8 rtt_one_sided_supported;
	u8 rtt_ftm_supported;
	u8 lci_support;
	u8 lcr_support;
	u8 preamble_support;
	u8 bw_support;
	u8 responder_supported;
	u8 mc_version;
};

enum rtt_wifi_preamble {
	WIFI_RTT_PREAMBLE_LEGACY = 0x1,
	WIFI_RTT_PREAMBLE_HT = 0x2,
	WIFI_RTT_PREAMBLE_VHT = 0x4
};

enum rtt_wifi_bw {
	WIFI_RTT_BW_5 = 0x01,
	WIFI_RTT_BW_10 = 0x02,
	WIFI_RTT_BW_20 = 0x04,
	WIFI_RTT_BW_40 = 0x08,
	WIFI_RTT_BW_80 = 0x10,
	WIFI_RTT_BW_160 = 0x20
};

struct rtt_responder {
	struct wifi_channel_info channel;
	enum rtt_wifi_preamble preamble;
};

/* Ranging status */
enum rtt_wifi_status {
	RTT_STATUS_SUCCESS = 0,
	RTT_STATUS_FAILURE = 1,
	RTT_STATUS_FAIL_NO_RSP = 2,
	RTT_STATUS_FAIL_REJECTED = 3,
	RTT_STATUS_FAIL_NOT_SCHEDULED_YET = 4,
	RTT_STATUS_FAIL_TM_TIMEOUT = 5,
	RTT_STATUS_FAIL_AP_ON_DIFF_CHANNEL = 6,
	RTT_STATUS_FAIL_NO_CAPABILITY = 7,
	RTT_STATUS_ABORTED = 8,
	RTT_STATUS_FAIL_INVALID_TS = 9,
	RTT_STATUS_FAIL_PROTOCOL = 10,
	RTT_STATUS_FAIL_SCHEDULE = 11,
	RTT_STATUS_FAIL_BUSY_TRY_LATER = 12,
	RTT_STATUS_INVALID_REQ = 13,
	RTT_STATUS_NO_WIFI = 14,
	RTT_STATUS_FAIL_FTM_PARAM_OVERRIDE = 15
};

/* RTT Type */
enum rtt_wifi_type {
	RTT_TYPE_1_SIDED = 0x1,
	RTT_TYPE_2_SIDED = 0x2,
};

struct rtt_wifi_information_element {
	u8 id;
	u8 len;
	u8 *data;
};

/* RTT results */
struct rtt_wifi_hal_result {
	u8 mac_addr[ETH_ALEN];
	unsigned int burst_num;
	unsigned int measurement_number;
	unsigned int success_number;
	u8 number_per_burst_peer;
	enum rtt_wifi_status status;
	u8 retry_after_duration;
	enum rtt_wifi_type type;
	int rssi;
	int rssi_spread;
	struct wifi_rate tx_rate;
	struct wifi_rate rx_rate;

	u64 rtt;
	u64 rtt_sd;
	u64 rtt_spread;
	int distance_mm;
	int distance_sd_mm;
	int distance_spread_mm;
	u64 ts;
	int burst_duration;
	int negotiated_burst_num;
	struct rtt_wifi_information_element *LCI;
	struct rtt_wifi_information_element *LCR;
};

struct rtt_dot11_rm_ie {
	u8 id;
	u8 len;
	u8 token;
	u8 mode;
	u8 type;
};

struct rtt_wifi_result {
	u8 mac_addr[ETH_ALEN];
	enum rtt_wifi_status status;
	struct rtt_meas_params params;
	u8 measurement_number;
	u8 success_number;
	u8 retry_after_duration;
	enum rtt_wifi_type type;
	int rssi;
	int rssi_spread;
	struct wifi_rate tx_rate;
	struct wifi_rate rx_rate;

	u32 ts;
	u8 lci_length;
	u8 lci_content[LCI_MAX_LEN];
	u8 lcr_length;
	u8 lcr_content[LCR_MAX_LEN];
	u32 n_meas;
	struct rtt_peer_meas meas[RTT_MAX_RESULT_SUPPORT];
} __packed;

int sc2355_rtt_get_capabilities(struct wiphy *wiphy, struct wireless_dev *wdev,
				const void *data, int data_len);
int sc2355_rtt_start_session(struct wiphy *wiphy, struct wireless_dev *wdev,
			     const void *data, int data_len);
int sc2355_rtt_abort_session(struct wiphy *wiphy, struct wireless_dev *wdev,
			     const void *data, int data_len);
int sc2355_rtt_get_responder_info(struct wiphy *wiphy,
				  struct wireless_dev *wdev,
				  const void *data, int data_len);
int sc2355_rtt_configure_responder(struct wiphy *wiphy,
				   struct wireless_dev *wdev,
				   const void *data, int data_len);
int sc2355_rtt_event(struct sprd_vif *vif, u8 *data, u16 len);
void sc2355_rtt_stop_operations(struct sprd_priv *priv);

void sc2355_rtt_init(struct sprd_priv *priv);
void sc2355_rtt_deinit(struct sprd_priv *priv);

#endif
