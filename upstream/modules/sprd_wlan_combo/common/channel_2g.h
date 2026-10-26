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

#ifndef __CFG80211_H_2G__
#define __CFG80211_H_2G__


static struct ieee80211_rate sprd_rates[] = {
	RATETAB_ENT(10, 0x1, 0),
	RATETAB_ENT(20, 0x2, 0),
	RATETAB_ENT(55, 0x5, 0),
	RATETAB_ENT(110, 0xb, 0),
	RATETAB_ENT(60, 0x6, 0),
	RATETAB_ENT(90, 0x9, 0),
	RATETAB_ENT(120, 0xc, 0),
	RATETAB_ENT(180, 0x12, 0),
	RATETAB_ENT(240, 0x18, 0),
	RATETAB_ENT(360, 0x24, 0),
	RATETAB_ENT(480, 0x30, 0),
	RATETAB_ENT(540, 0x36, 0),

	RATETAB_ENT(65, 0x80, 0),
	RATETAB_ENT(130, 0x81, 0),
	RATETAB_ENT(195, 0x82, 0),
	RATETAB_ENT(260, 0x83, 0),
	RATETAB_ENT(390, 0x84, 0),
	RATETAB_ENT(520, 0x85, 0),
	RATETAB_ENT(585, 0x86, 0),
	RATETAB_ENT(650, 0x87, 0),
	RATETAB_ENT(130, 0x88, 0),
	RATETAB_ENT(260, 0x89, 0),
	RATETAB_ENT(390, 0x8a, 0),
	RATETAB_ENT(520, 0x8b, 0),
	RATETAB_ENT(780, 0x8c, 0),
	RATETAB_ENT(1040, 0x8d, 0),
	RATETAB_ENT(1170, 0x8e, 0),
	RATETAB_ENT(1300, 0x8f, 0),
};

static struct ieee80211_channel sprd_2ghz_channels[] = {
	CHAN2G(1, 2412, 0),
	CHAN2G(2, 2417, 0),
	CHAN2G(3, 2422, 0),
	CHAN2G(4, 2427, 0),
	CHAN2G(5, 2432, 0),
	CHAN2G(6, 2437, 0),
	CHAN2G(7, 2442, 0),
	CHAN2G(8, 2447, 0),
	CHAN2G(9, 2452, 0),
	CHAN2G(10, 2457, 0),
	CHAN2G(11, 2462, 0),
	CHAN2G(12, 2467, 0),
	CHAN2G(13, 2472, 0),
	CHAN2G(14, 2484, 0),
};

#endif
