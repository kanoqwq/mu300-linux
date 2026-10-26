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

#ifndef __REPORT_H__
#define __REPORT_H__

void sprd_report_scan_done(struct sprd_vif *vif, bool abort);
void sprd_report_sched_scan_done(struct sprd_vif *vif, bool abort);
void sprd_report_softap(struct sprd_vif *vif, u8 is_connect, u8 *addr,
			u8 *req_ie, u16 req_ie_len);
void sprd_report_connection(struct sprd_vif *vif,
			    struct sprd_connect_info *conn_info,
			    u8 status_code);
void sprd_report_disconnection(struct sprd_vif *vif, u16 reason_code);
void sprd_report_mic_failure(struct sprd_vif *vif, u8 is_mcast, u8 key_id);
void sprd_report_remain_on_channel_expired(struct sprd_vif *vif);
void sprd_report_mgmt_tx_status(struct sprd_vif *vif, u64 cookie,
				const u8 *buf, u32 len, u8 ack);
void sprd_report_mgmt(struct sprd_vif *vif, u8 chan, const u8 *buf, size_t len);
void sprd_report_mgmt_deauth(struct sprd_vif *vif, const u8 *buf, size_t len);
void sprd_report_mgmt_disassoc(struct sprd_vif *vif, const u8 *buf, size_t len);
void sprd_report_cqm(struct sprd_vif *vif, u8 rssi_event);

#endif
