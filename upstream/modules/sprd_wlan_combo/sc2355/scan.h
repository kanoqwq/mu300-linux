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

#ifndef __SCAN_H__
#define __SCAN_H__

#include <net/cfg80211.h>

#define SPRD_MAX_SCAN_REQ_IE_LEN	(255)
#define SPRD_MIN_RSSI_THOLD		(-127)

void sc2355_abort_scan(struct wiphy *wiphy, struct wireless_dev *wdev);
void sc2355_report_scan_result(struct sprd_vif *vif, u16 chan, s16 rssi,
			       u8 *frame, u16 len);

#endif
