/*
* SPDX-FileCopyrightText: 2020-2022 Unisoc (Shanghai) Technologies Co., Ltd
* SPDX-License-Identifier: GPL-2.0
*
* Copyright 2020-2022 Unisoc (Shanghai) Technologies Co., Ltd
*
* This program is free software; you can redistribute it and/or modify it
* under the terms of version 2 of the GNU General Public License
* as published by the Free Software Foundation.
*/

#ifndef __NPI_H__
#define __NPI_H__

#define SPRD_NPI_CMD_START			(0)
#define SPRD_NPI_CMD_SET_WLAN_CAP		(40)
#define SPRD_STA_GC_EN_SLEEP			(0x3)
#define SPRD_STA_GC_NO_SLEEP			(0x0)
#define SPRD_PSM_PATH				"/opt/etc/.psm.info"
#define SPRD_NPI_CMD_GET_CHIPID			(136)
#define SPRD_NPI_CMD_SET_COUNTRY		200

#define SPRD_NPI_CMD_SET_PROTECTION_MODE	50
#define SPRD_NPI_CMD_GET_PROTECTION_MODE	51
#define SPRD_NPI_CMD_SET_RTS_THRESHOLD		52
#define SPRD_NPI_CMD_SET_CCA_PARAM          198
#define SPRD_NPI_CMD_SET_RANDOM_MAC         199

struct sprd_priv;
struct sprd_vif;

/* enable: 0x0
 * disable: 0x1
 * STA: bit 0
 * GC: bit 1
 */
enum sprd_nl_commands {
	SPRD_NL_CMD_UNSPEC,
	SPRD_NL_CMD_NPI,
	SPRD_NL_CMD_GET_INFO,
	SPRD_NL_CMD_MAX,
};

enum sprd_nl_attrs {
	SPRD_NL_ATTR_UNSPEC,
	SPRD_NL_ATTR_IFINDEX,
	SPRD_NL_ATTR_AP2CP,
	SPRD_NL_ATTR_CP2AP,
	SPRD_NL_ATTR_MAX,
};

struct sprd_npi_cmd_hdr {
	unsigned char type;
	unsigned char subtype;
	unsigned short len;
} __packed;

struct sprd_npi_cmd_resp_hdr {
	unsigned char type;
	unsigned char subtype;
	unsigned short len;
	int status;
} __packed;

enum sprd_npi_cmd_type {
	SPRD_HT2CP_CMD = 1,
	SPRD_CP2HT_REPLY,
};

enum sprd_npi_cca_type {
	SPRD_NPI_CCA_CLOSE,
	SPRD_NPI_CCA_ABSOLUTE,
	SPRD_NPI_CCA_RELATIVE,
	SPRD_NPI_CCA_CE,
	SPRD_NPI_CCA_MAX,
};

enum sprd_npi_ce_subtype {
	SPRD_NPI_CE_DISABLE,
	SPRD_NPI_CE_ENABLE,
	SPRD_NPI_CE_MAX,
};

enum sprd_npi_data_flag {
	SPRD_NPI_DATA_SPECIAL,
	SPRD_NPI_NORMAL_UNENCRYP,
	SPRD_NPI_NORMAL_ALL,
	SPRD_NPI_DATA_FLAG_MAX,
};

struct sprd_wlan_adap_param {
	int special_data_flag;
	char wifi_adaptive_flag;
	spinlock_t adap_lock;

};

void sprd_init_npi(void);
void sprd_deinit_npi(void);
void sprd_npi_set_cca_param(struct sprd_priv *priv, struct sprd_vif *vif);
void sprd_evt_adaptive(struct sprd_vif *vif);
void sprd_wifi_adaptive_work(struct sprd_priv *priv, struct sprd_vif *vif);

#endif
