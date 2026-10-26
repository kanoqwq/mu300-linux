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

#ifndef __FCC_H__
#define __FCC_H__

#define MAX_PHY_MODE	7
#define MAX_POWER_BACKOFF_RULE	100
#define MAX_FCC_COUNTRY_NUM	76

struct sprd_priv;

struct sprd_power_backoff {
	u8 subtype;
	u8 channel;
	u8 bw;
	u8 power_rule[MAX_PHY_MODE][2];
} __packed;

struct fcc_power_bo {
	char country[3];
	u8 num;
	struct sprd_power_backoff power_backoff[MAX_POWER_BACKOFF_RULE];
} __packed;

struct fresh_bo_info {
	u8 pw_channel;
	u8 pw_bw;
};

struct sprd_fcc_priv {
	struct mutex lock;/* protects the FCC data */
	struct fcc_power_bo *cur_power_bo;
	bool flag;
	u8 channel;
	u8 bw;
};

void sc2355_fcc_fresh_bo_work(struct sprd_priv *priv, void *data, u16 len);
void sc2355_fcc_match_country(struct sprd_priv *priv, const char *alpha2);
void sc2355_fcc_reset_bo(void);
void sc2355_fcc_init(void);
#endif

