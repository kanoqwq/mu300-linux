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

#ifndef __ACS_H__
#define __ACS_H__

#include <linux/list.h>
#include <uapi/linux/if_ether.h>

struct sprd_vif;

struct bssid_node {
	unsigned char bssid[ETH_ALEN];
	struct list_head list;
};

struct scan_result {
	struct list_head list;
	int signal;
	unsigned char bssid[6];
};

struct survey_info_node {
	/* survey info */
	unsigned int cca_busy_time;
	char noise;
	struct ieee80211_channel *channel;
	struct list_head survey_list;
	/* channel info */
	unsigned short chan;
	unsigned short beacon_num;
	struct list_head bssid_list;
	u8 channel_num;
	u8 duration;
	u8 busy;
};

struct survey_info_new_node {
	/* survey info */
	unsigned int cca_busy_time;
	unsigned int busy_ext_time;
	unsigned int time;
	s8 noise;
	struct ieee80211_channel *channel;
	struct list_head survey_list;
};

void clean_survey_info_list(struct sprd_vif *vif);
void transfer_survey_info(struct sprd_vif *vif);

#endif
