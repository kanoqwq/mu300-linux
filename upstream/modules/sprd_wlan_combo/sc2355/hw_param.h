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

#ifndef __HW_PARAM_H__
#define __HW_PARAM_H__

struct nvm_cali_cmd {
	s8 itm[64];
	s32 par[512];
	s32 num;
};

struct nvm_name_table {
	s8 *itm;
	u32 mem_offset;
	s32 type;
};

/*[Section 1: Version] */
struct version_t {
	u16 major;
	u16 minor;
};

/*[Section 2: Board Config]*/
struct board_config_t {
	u16 calib_bypass;
	u8 txchain_mask;
	u8 rxchain_mask;
};

/*[Section 3: Board Config TPC]*/
struct board_config_tpc_t {
	u8 dpd_lut_idx[8];
	u16 tpc_goal_chain0[8];
	u16 tpc_goal_chain1[8];
};

struct tpc_element_lut_t {
	u8 rf_gain_idx;
	u8 pa_bias_idx;
	s8 dvga_offset;
	s8 residual_error;
};

/*[Section 4: TPC-LUT]*/
struct tpc_lut_t {
	struct tpc_element_lut_t chain0_lut[8];
	struct tpc_element_lut_t chain1_lut[8];
};

/*[Section 5: Board Config Frequency Compensation]*/
struct board_conf_freq_comp_t {
	s8 channel_2g_chain0[14];
	s8 channel_2g_chain1[14];
	s8 channel_5g_chain0[25];
	s8 channel_5g_chain1[25];
	s8 reserved[2];
};

/*[Section 6: Rate To Power with BW 20M]*/
struct power_20m_t {
	s8 power_11b[4];
	s8 power_11ag[8];
	s8 power_11n[17];
	s8 power_11ac[20];
	s8 reserved[3];
};

/*[Section 7: Power Backoff]*/
struct power_backoff_t {
	s8 green_wifi_offset;
	s8 ht40_power_offset;
	s8 vht40_power_offset;
	s8 vht80_power_offset;
	s8 sar_power_offset;
	s8 mean_power_offset;
	s8 reserved[2];
};

/*[Section 8: Reg Domain]*/
struct reg_domain_t {
	u32 reg_domain1;
	u32 reg_domain2;
};

/*[Section 9: Band Edge Power offset (MKK, FCC, ETSI)]*/
struct band_edge_power_offset_t {
	u8 bw20m[39];
	u8 bw40m[21];
	u8 bw80m[6];
	u8 reserved[2];
};

/*[Section 10: TX Scale]*/
struct tx_scale_t {
	s8 chain0[39][16];
	s8 chain1[39][16];
};

/*[Section 11: misc]*/
struct misc_t {
	s8 dfs_switch;
	s8 power_save_switch;
	s8 fem_lan_param_setup;
	s8 rssi_report_diff;
};

/*[Section 12: debug reg]*/
struct debug_reg_t {
	u32 address[16];
	u32 value[16];
};

/*[Section 13:coex_config] */
struct coex_config_t {
	u32 bt_performance_cfg0;
	u32 bt_performance_cfg1;
	u32 wifi_performance_cfg0;
	u32 wifi_performance_cfg2;
	u32 strategy_cfg0;
	u32 strategy_cfg1;
	u32 strategy_cfg2;
	u32 compatibility_cfg0;
	u32 compatibility_cfg1;
	u32 ant_cfg0;
	u32 ant_cfg1;
	u32 isolation_cfg0;
	u32 isolation_cfg1;
	u32 reserved_cfg0;
	u32 reserved_cfg1;
	u32 reserved_cfg2;
	u32 reserved_cfg3;
	u32 reserved_cfg4;
	u32 reserved_cfg5;
	u32 reserved_cfg6;
	u32 reserved_cfg7;
};

struct rf_config_t {
	int rf_data_len;
	u8 rf_data[1500];
};

/*wifi config section1 struct*/
struct wifi_conf_sec1_t {
	struct version_t version;
	struct board_config_t board_config;
	struct board_config_tpc_t board_config_tpc;
	struct tpc_lut_t tpc_lut;
	struct board_conf_freq_comp_t board_conf_freq_comp;
	struct power_20m_t power_20m;
	struct power_backoff_t power_backoff;
	struct reg_domain_t reg_domain;
	struct band_edge_power_offset_t band_edge_power_offset;
};

/*wifi config section2 struct*/
struct wifi_conf_sec2_t {
	struct tx_scale_t tx_scale;
	struct misc_t misc;
	struct debug_reg_t debug_reg;
	struct coex_config_t coex_config;
	/*struct rf_config_t rf_config;*/
};

struct roaming_param_t {
	u8 trigger;
	u8 delta;
	u8 band_5g_prefer;
};

struct wifi_config_param_t {
	struct roaming_param_t roaming_param;
};

/*wifi config struct*/
struct wifi_conf_t {
	struct version_t version;
	struct board_config_t board_config;
	struct board_config_tpc_t board_config_tpc;
	struct tpc_lut_t tpc_lut;
	struct board_conf_freq_comp_t board_conf_freq_comp;
	struct power_20m_t power_20m;
	struct power_backoff_t power_backoff;
	struct reg_domain_t reg_domain;
	struct band_edge_power_offset_t band_edge_power_offset;
	struct tx_scale_t tx_scale;
	struct misc_t misc;
	struct debug_reg_t debug_reg;
	struct coex_config_t coex_config;
	struct rf_config_t rf_config;
	struct wifi_config_param_t wifi_param;
};

int sc2355_get_nvm_table(struct sprd_priv *priv, struct wifi_conf_t *p);

#endif
