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

#include "cmdevt.h"
#include "common/cmd.h"
#include "common/common.h"

struct sprd_api_version_t api_array[] = {
	[0]{	/*ID:0*/
		.cmd_id = CMD_ERR,
		.drv_version = 1,
	},
	{	/*ID:1*/
		.cmd_id = CMD_GET_INFO,
		.drv_version = 2,
	},
	{	/*ID:2*/
		.cmd_id = CMD_SET_REGDOM,
		.drv_version = 1,
	},
	{	/*ID:3*/
		.cmd_id = CMD_OPEN,
		.drv_version = 1,
	},
	{	/*ID:4*/
		.cmd_id = CMD_CLOSE,
		.drv_version = 1,
	},
	{	/*ID:5*/
		.cmd_id = CMD_POWER_SAVE,
		.drv_version = 1,
	},
	{	/*ID:6*/
		.cmd_id = CMD_SET_PARAM,
		.drv_version = 1,
	},
	{	/*ID:7*/
		.cmd_id = CMD_SET_CHANNEL,
		.drv_version = 1,
	},
	{	/*ID:8*/
		.cmd_id = CMD_REQ_LTE_CONCUR,
		.drv_version = 1,
	},
	{	/*ID:9*/
		.cmd_id = CMD_SYNC_VERSION,
		.drv_version = 1,
	},
	{	/*ID:10*/
		.cmd_id = CMD_CONNECT,
		.drv_version = 1,
	},
	{	/*ID:11*/
		.cmd_id = CMD_SCAN,
		.drv_version = 2,
	},
	{	/*ID:12*/
		.cmd_id = CMD_SCHED_SCAN,
		.drv_version = 1,
	},
	{	/*ID:13*/
		.cmd_id = CMD_DISCONNECT,
		.drv_version = 1,
	},
	{	/*ID:14*/
		.cmd_id = CMD_KEY,
		.drv_version = 1,
	},
	{	/*ID:15*/
		.cmd_id = CMD_SET_PMKSA,
		.drv_version = 1,
	},
	{	/*ID:16*/
		.cmd_id = CMD_GET_STATION,
		.drv_version = 1,
	},
	{	/*ID:17*/
		.cmd_id = CMD_START_AP,
		.drv_version = 1,
	},
	{	/*ID:18*/
		.cmd_id = CMD_DEL_STATION,
		.drv_version = 1,
	},
	{	/*ID:19*/
		.cmd_id = CMD_SET_BLACKLIST,
		.drv_version = 1,
	},
	{	/*ID:20*/
		.cmd_id = CMD_SET_WHITELIST,
		.drv_version = 1,
	},
	{	/*ID:21*/
		.cmd_id = CMD_TX_MGMT,
		.drv_version = 1,
	},
	{	/*ID:22*/
		.cmd_id = CMD_REGISTER_FRAME,
		.drv_version = 1,
	},
	{	/*ID:23*/
		.cmd_id = CMD_REMAIN_CHAN,
		.drv_version = 1,
	},
	{	/*ID:24*/
		.cmd_id = CMD_CANCEL_REMAIN_CHAN,
		.drv_version = 1,
	},
	{	/*ID:25*/
		.cmd_id = CMD_SET_IE,
		.drv_version = 1,
	},
	{	/*ID:26*/
		.cmd_id = CMD_NOTIFY_IP_ACQUIRED,
		.drv_version = 1,
	},
	{	/*ID:27*/
		.cmd_id = CMD_SET_CQM,
		.drv_version = 1,
	},
	{	/*ID:28*/
		.cmd_id = CMD_SET_ROAM_OFFLOAD,
		.drv_version = 1,
	},
	{	/*ID:29*/
		.cmd_id = CMD_SET_MEASUREMENT,
		.drv_version = 1,
	},
	{	/*ID:30*/
		.cmd_id = CMD_SET_QOS_MAP,
		.drv_version = 1,
	},
	{	/*ID:31*/
		.cmd_id = CMD_TDLS,
		.drv_version = 1,
	},
	{	/*ID:32*/
		.cmd_id = CMD_11V,
		.drv_version = 1,
	},
	{	/*ID:33*/
		.cmd_id = CMD_NPI_MSG,
		.drv_version = 1,
	},
	{	/*ID:34*/
		.cmd_id = CMD_NPI_GET,
		.drv_version = 1,
	},
	{	/*ID:35*/
		.cmd_id = CMD_ASSERT,
		.drv_version = 1,
	},
	{	/*ID:36*/
		.cmd_id = CMD_FLUSH_SDIO,
		.drv_version = 1,
	},
	{	/*ID:37*/
		.cmd_id = CMD_ADD_TX_TS,
		.drv_version = 1,
	},
	{	/*ID:38*/
		.cmd_id = CMD_DEL_TX_TS,
		.drv_version = 1,
	},
	{	/*ID:39*/
		.cmd_id = CMD_MULTICAST_FILTER,
		.drv_version = 1,
	},
	{	/*ID:40*/
		.cmd_id = CMD_ADDBA_REQ,
		.drv_version = 1,
	},
	{	/*ID:41*/
		.cmd_id = CMD_DELBA_REQ,
		.drv_version = 1,
	},
	[56]{	/*ID:56*/
		.cmd_id = CMD_LLSTAT,
		.drv_version = 1,
	},
	{	/*ID:57*/
		.cmd_id = CMD_CHANGE_BSS_IBSS_MODE,
		.drv_version = 1,
	},
	{	/*ID:58*/
		.cmd_id = CMD_IBSS_JOIN,
		.drv_version = 1,
	},
	{	/*ID:59*/
		.cmd_id = CMD_SET_IBSS_ATTR,
		.drv_version = 1,
	},
	{	/*ID:60*/
		.cmd_id = CMD_IBSS_LEAVE,
		.drv_version = 1,
	},
	{	/*ID:61*/
		.cmd_id = CMD_IBSS_VSIE_SET,
		.drv_version = 1,
	},
	{	/*ID:62*/
		.cmd_id = CMD_IBSS_VSIE_DELETE,
		.drv_version = 1,
	},
	{	/*ID:63*/
		.cmd_id = CMD_IBSS_SET_PS,
		.drv_version = 1,
	},
	{	/*ID:64*/
		.cmd_id = CMD_RND_MAC,
		.drv_version = 1,
	},
	{	/*ID:65*/
		.cmd_id = CMD_GSCAN,
		.drv_version = 1,
	},
	{	/*ID:66*/
		.cmd_id = CMD_RTT,
		.drv_version = 1,
	},
	{	/*ID:67*/
		.cmd_id = CMD_NAN,
		.drv_version = 1,
	},
	{	/*ID:68*/
		.cmd_id = CMD_BA,
		.drv_version = 1,
	},
	{	/*ID:69*/
		.cmd_id = CMD_SET_PROTECT_MODE,
		.drv_version = 1,
	},
	{	/*ID:70*/
		.cmd_id = CMD_GET_PROTECT_MODE,
		.drv_version = 1,
	},
	{	/*ID:71*/
		.cmd_id = CMD_SET_MAX_CLIENTS_ALLOWED,
		.drv_version = 1,
	},
	{	/*ID:72*/
		.cmd_id = CMD_TX_DATA,
		.drv_version = 1,
	},
	{	/*ID:73*/
		.cmd_id = CMD_NAN_DATA_PATH,
		.drv_version = 1,
	},
	[74]{	/*ID:74*/
		.cmd_id = CMD_SET_TLV,
		.drv_version = 1,
	},
	{	/*ID:75*/
		.cmd_id = CMD_RSSI_MONITOR,
		.drv_version = 1,
	},
	{	/*ID:76*/
		.cmd_id = CMD_DOWNLOAD_INI,
		.drv_version = 1,
	},
	{	/*ID:77*/
		.cmd_id = CMD_RADAR_DETECT,
		.drv_version = 1,
	},
	{	/*ID:78*/
		.cmd_id = CMD_HANG_RECEIVED,
		.drv_version = 1,
	},
	{	/*ID:79*/
		.cmd_id = CMD_RESET_BEACON,
		.drv_version = 1,
	},
	{	/*ID:80*/
		.cmd_id = CMD_VOWIFI_DATA_PROTECT,
		.drv_version = 1,
	},
	[82]{
		/*ID:82*/
		.cmd_id = CMD_SET_MIRACAST,
		.drv_version = 1,
	},
	[84]{
		/*ID:84*/
		.cmd_id = CMD_PACKET_OFFLOAD,
		.drv_version = 1,
	},
	[85]{
		/*ID:85*/
		.cmd_id = CMD_SET_SAE_PARAM,
		.drv_version = 1,
	},
	[87]{
		/*ID:87*/
		.cmd_id = CMD_SET_SNIFFER,
		.drv_version = 1,
	},
	[89]{
		/*ID:89*/
		.cmd_id = CMD_EXTENDED_LLSTAT,
		.drv_version = 1,
	},
	[128]{	/*ID:0x80*/
		.cmd_id = EVT_CONNECT,
		.drv_version = 1,
	},
	[129]{	/*ID:0x81*/
		.cmd_id = EVT_DISCONNECT,
		.drv_version = 1,
	},
	[130]{	/*ID:0x82*/
		.cmd_id = EVT_SCAN_DONE,
		.drv_version = 1,
	},
	[131]{	/*ID:0x83*/
		.cmd_id = EVT_MGMT_FRAME,
		.drv_version = 1,
	},
	[132]{	/*ID:0x84*/
		.cmd_id = EVT_MGMT_TX_STATUS,
		.drv_version = 1,
	},
	[133]{	/*ID:0x85*/
		.cmd_id = EVT_REMAIN_CHAN_EXPIRED,
		.drv_version = 1,
	},
	[134]{	/*ID:0x86*/
		.cmd_id = EVT_MIC_FAIL,
		.drv_version = 1,
	},
	[136]{	/*ID:0x88*/
		.cmd_id = EVT_GSCAN_FRAME,
		.drv_version = 1,
	},
	[137]{	/*ID:0x89*/
		.cmd_id = EVT_RSSI_MONITOR,
		.drv_version = 1,
	},
	[160]{	/*ID:0xa0*/
		.cmd_id = EVT_NEW_STATION,
		.drv_version = 1,
	},
	[161]{	/*ID:0xa1*/
		.cmd_id = EVT_RADAR_DETECTED,
		.drv_version = 1,
	},
	[176]{	/*ID:0xb0*/
		.cmd_id = EVT_CQM,
		.drv_version = 1,
	},
	[177]{	/*ID:0xb1*/
		.cmd_id = EVT_MEASUREMENT,
		.drv_version = 1,
	},
	[178]{	/*ID:0xb2*/
		.cmd_id = EVT_TDLS,
		.drv_version = 1,
	},
	[179]{	/*ID:0xb3*/
		.cmd_id = EVT_SDIO_FLOWCON,
		.drv_version = 1,
	},
	[224]{	/*ID:0xe0*/
		.cmd_id = EVT_SDIO_SEQ_NUM,
		.drv_version = 1,
	},
	[242]{	/*ID:0xf2*/
		.cmd_id = EVT_RTT,
		.drv_version = 1,
	},
	[243]{	/*ID:0xf3*/
		.cmd_id = EVT_BA,
		.drv_version = 1,
	},
	[244]{	/*ID:0xf4*/
		.cmd_id = EVT_NAN,
		.drv_version = 1,
	},
	[245]{	/*ID:0xf5*/
		.cmd_id = EVT_STA_LUT_INDEX,
		.drv_version = 1,
	},
	[246]{	/*ID:0xf6*/
		.cmd_id = EVT_HANG_RECOVERY,
		.drv_version = 1,
	},
	[247]{	/*ID:0xf7*/
		.cmd_id = EVT_THERMAL_WARN,
		.drv_version = 1,
	},
	[248]{	/*ID:0xf8*/
		.cmd_id = EVT_SUSPEND_RESUME,
		.drv_version = 1,
	},
	[255]{
		.drv_version = 0,
	}
};

void sc2355_api_version_fill_drv(struct sprd_priv *priv,
				 struct cmd_api_t *drv_api)
{
	int count;
	struct sprd_api_version_t *p;
	/*init priv sync_api struct*/
	priv->sync_api.main_drv = MAIN_DRV_VERSION;
	priv->sync_api.compat = DEFAULT_COMPAT;
	(&priv->sync_api)->api_array = api_array;
	/*fill CMD struct drv_api*/
	drv_api->main_ver = priv->sync_api.main_drv;
	for (count = 0; count < MAX_API; count++) {
		p = &api_array[count];
		if (p->drv_version)
			drv_api->api_map[count] = p->drv_version;
		else
			drv_api->api_map[count] = 0;
	}
}

void sc2355_api_version_fill_fw(struct sprd_priv *priv,
				struct cmd_api_t *fw_api)
{
	int count;
	/*define tmp struct *p */
	struct sprd_api_version_t *p;
	/*got main fw_version*/
	priv->sync_api.main_fw = fw_api->main_ver;
	/*if main version not match, trigger it assert*/

	for (count = 0; count < MAX_API; count++) {
		p = &api_array[count];
		p->fw_version = fw_api->api_map[count];
		if (p->fw_version != p->drv_version) {
			pr_info
			    ("API version not match!! CMD ID:%d,drv:%d,fw:%d\n",
			     count, p->drv_version, p->fw_version);
		}
	}
}

int sc2355_api_version_available_check(struct sprd_priv *priv,
				       struct sprd_msg *msg)
{
	/*define tmp struct *p */
	struct sprd_api_version_t *p = NULL;
	/*cmd head struct point*/
	struct sprd_cmd_hdr *hdr = NULL;
	u8 cmd_id;
	u8 drv_ver = 0, fw_ver = 0;
	u32 min_ver = 255;

	hdr = (struct sprd_cmd_hdr *)(msg->tran_data + priv->hif.hif_offset);
	cmd_id = hdr->cmd_id;
	if (cmd_id == CMD_SYNC_VERSION || cmd_id == CMD_SCAN)
		return 0;

	p = &api_array[cmd_id];
	drv_ver = p->drv_version;
	fw_ver = p->fw_version;
	min_ver = min(drv_ver, fw_ver);
	if (min_ver) {
		if (min_ver == drv_ver || min_ver == priv->sync_api.compat) {
			priv->sync_api.compat = DEFAULT_COMPAT;
			return 0;
		}

		pr_err("CMD ID:%d,drv ver:%d, fw ver:%d,compat:%d\n",
		       cmd_id, drv_ver, fw_ver, priv->sync_api.compat);
		return -1;
	}

	pr_err("CMD ID:%d,drv ver:%d, fw ver:%d drop it!!\n",
	       cmd_id, drv_ver, fw_ver);
	return -1;
}

int sc2355_api_version_need_compat_operation(struct sprd_priv *priv, u8 cmd_id)
{
	u8 drv_ver = 0;
	u8 fw_ver = 0;
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;

	drv_ver = (api + cmd_id)->drv_version;
	fw_ver = (api + cmd_id)->fw_version;

	if (drv_ver != fw_ver && fw_ver == min(fw_ver, drv_ver)) {
		pr_info("drv ver:%d higher than fw ver:%d\n", drv_ver, fw_ver);
		pr_info("need compat operation!!\n");
		return fw_ver;

	} else {
		if (drv_ver != fw_ver)
			pr_info("drv ver:%d, fw_ver:%d\n no need compat!!",
				drv_ver, fw_ver);
		return 0;
	}
}
