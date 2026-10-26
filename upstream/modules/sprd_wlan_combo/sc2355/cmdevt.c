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

#include <linux/completion.h>
#include "cmdevt.h"
#include "common/acs.h"
#include "common/cfg80211.h"
#include "common/chip_ops.h"
#include "common/common.h"
#include "common/delay_work.h"
#include "common/hif.h"
#include "common/iface.h"
#include "common/msg.h"
#include "common/report.h"
#include "common/tdls.h"
#include "hw_param.h"
#ifdef CONFIG_SC2355_WLAN_NAN
#include "nan.h"
#endif /* CONFIG_SC2355_WLAN_NAN */
#include "qos.h"
#include "scan.h"
#include "rtt.h"
#include "rx.h"
#include "tx.h"

#define ASSERT_INFO_BUF_SIZE	100

#define SEC1			1
#define SEC2			2
#define SEC3			3
#define SEC4			4

#define FLAG_SIZE		5

#define WIFI_EVENT_WFD_RATE	0x30

#define SPRD_SECTION_NUM	4

static const u16 CRC_table[] = {
	0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00,
	0x2800, 0xE401, 0xA001, 0x6C00, 0x7800, 0xB401,
	0x5000, 0x9C01, 0x8801, 0x4400,
};

static void cmdevt_flush_tdls_flow(struct sprd_vif *vif, const u8 *peer, u8 oper);

static int bss_count;
static const char *cmdevt_cmd2str(u8 cmd)
{
	switch (cmd) {
	case CMD_ERR:
		return "CMD_ERR";
	case CMD_GET_INFO:
		return "CMD_GET_INFO";
	case CMD_SET_REGDOM:
		return "CMD_SET_REGDOM";
	case CMD_OPEN:
		return "CMD_OPEN";
	case CMD_CLOSE:
		return "CMD_CLOSE";
	case CMD_POWER_SAVE:
		return "CMD_POWER_SAVE";
	case CMD_SET_PARAM:
		return "CMD_SET_PARAM";
	case CMD_REQ_LTE_CONCUR:
		return "CMD_REQ_LTE_CONCUR";
	case CMD_SYNC_VERSION:
		return "CMD_SYNC_VERSION";
	case CMD_CONNECT:
		return "CMD_CONNECT";

	case CMD_SCAN:
		return "CMD_SCAN";
	case CMD_SCHED_SCAN:
		return "CMD_SCHED_SCAN";
	case CMD_DISCONNECT:
		return "CMD_DISCONNECT";
	case CMD_KEY:
		return "CMD_KEY";
	case CMD_SET_PMKSA:
		return "CMD_SET_PMKSA";
	case CMD_GET_STATION:
		return "CMD_GET_STATION";
	case CMD_SET_CHANNEL:
		return "CMD_SET_CHANNEL";

	case CMD_SET_MIRACAST:
		return "CMD_SET_MIRACAST";

	case CMD_START_AP:
		return "CMD_START_AP";
	case CMD_DEL_STATION:
		return "CMD_DEL_STATION";
	case CMD_SET_BLACKLIST:
		return "CMD_SET_BLACKLIST";
	case CMD_SET_WHITELIST:
		return "CMD_SET_WHITELIST";
	case CMD_MULTICAST_FILTER:
		return "CMD_MULTICAST_FILTER";

	case CMD_TX_MGMT:
		return "CMD_TX_MGMT";
	case CMD_REGISTER_FRAME:
		return "CMD_REGISTER_FRAME";
	case CMD_REMAIN_CHAN:
		return "CMD_REMAIN_CHAN";
	case CMD_CANCEL_REMAIN_CHAN:
		return "CMD_CANCEL_REMAIN_CHAN";

	case CMD_SET_IE:
		return "CMD_SET_IE";
	case CMD_NOTIFY_IP_ACQUIRED:
		return "CMD_NOTIFY_IP_ACQUIRED";

	case CMD_SET_CQM:
		return "CMD_SET_CQM";
	case CMD_SET_ROAM_OFFLOAD:
		return "CMD_SET_ROAM_OFFLOAD";
	case CMD_SET_MEASUREMENT:
		return "CMD_SET_MEASUREMENT";
	case CMD_SET_QOS_MAP:
		return "CMD_SET_QOS_MAP";
	case CMD_TDLS:
		return "CMD_TDLS";
	case CMD_11V:
		return "CMD_11V";
	case CMD_NPI_MSG:
		return "CMD_NPI_MSG";
	case CMD_NPI_GET:
		return "CMD_NPI_GET";

	case CMD_ASSERT:
		return "CMD_ASSERT";
	case CMD_FLUSH_SDIO:
		return "CMD_FLUSH_SDIO";
	case CMD_ADD_TX_TS:
		return "CMD_ADD_TX_TS";
	case CMD_DEL_TX_TS:
		return "CMD_DEL_TX_TS";
	case CMD_LLSTAT:
		return "CMD_LLSTAT";

	case CMD_GSCAN:
		return "CMD_GSCAN";
	case CMD_RTT:
		return "CMD_RTT";
	case CMD_RSSI_MONITOR:
		return "CMD_RSSI_MONITOR";

	case CMD_IBSS_JOIN:
		return "CMD_IBSS_JOIN";
	case CMD_SET_IBSS_ATTR:
		return "CMD_SET_IBSS_ATTR";
	case CMD_NAN:
		return "CMD_NAN";
	case CMD_RND_MAC:
		return "CMD_RND_MAC";
	case CMD_BA:
		return "CMD_BA";
	case CMD_SET_MAX_CLIENTS_ALLOWED:
		return "CMD_SET_MAX_CLIENTS_ALLOWED";
	case CMD_TX_DATA:
		return "CMD_TX_DATA";
	case CMD_ADDBA_REQ:
		return "CMD_ADDBA_REQ";
	case CMD_DELBA_REQ:
		return "CMD_DELBA_REQ";
	case CMD_SET_PROTECT_MODE:
		return "CMD_SET_PROTECT_MODE";
	case CMD_GET_PROTECT_MODE:
		return "CMD_GET_PROTECT_MODE";
	case CMD_DOWNLOAD_INI:
		return "CMD_DOWNLOAD_INI";
	case CMD_PACKET_OFFLOAD:
		return "CMD_PACKET_OFFLOAD";
	case CMD_HANG_RECEIVED:
		return "CMD_HANG_RECEIVED";
	case CMD_VOWIFI_DATA_PROTECT:
		return "CMD_VOWIFI_DATA_PROTECT";
	case CMD_SET_TLV:
		return "CMD_SET_TLV";
	case CMD_SET_SAE_PARAM:
		return "CMD_SET_SAE_PARAM";
	case CMD_SET_SNIFFER:
		return "CMD_SET_SNIFFER";
	case CMD_EXTENDED_LLSTAT:
		return "CMD_EXTENDED_LLSTAT";
	default:
		return "CMD_UNKNOWN";
	}
}

const char *sc2355_cmdevt_cmd2str(u8 cmd)
{
	return cmdevt_cmd2str(cmd);
}

int cmdevt_report_ip_addr(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct ip_addr_info *info = (struct ip_addr_info *)data;
	u8 *p;

	if (!len) {
		netdev_err(vif->ndev, "%s event data len=0\n", __func__);
		return -EINVAL;
	}
	p = (u8 *)info->ip_addr;
	if (info->type == 0x0800) {
		netdev_info(vif->ndev, "%s ipv4: %pI4\n", __func__, p);
	} else if (info->type == 0x0806) {
		netdev_info(vif->ndev, "%s ARP ip: %pI4\n", __func__, p);
	} else if (info->type == 0x86DD) {
		netdev_info(vif->ndev, "%s ipv6: %pI6", __func__, p);
	} else if (info->type == 0x888E) {
		netdev_err(vif->ndev, "%s type: EAPOL(GTK/PTK)\n", __func__);
	} else {
		netdev_err(vif->ndev, "%s unknow type:%x\n", __func__, info->type);
	}

	return 0;
}

static const char *cmdevt_evt2str(u8 evt)
{
	switch (evt) {
	case EVT_CONNECT:
		return "EVT_CONNECT";
	case EVT_DISCONNECT:
		return "EVT_DISCONNECT";
	case EVT_SCAN_DONE:
		return "EVT_SCAN_DONE";
	case EVT_MGMT_FRAME:
		return "EVT_MGMT_FRAME";
	case EVT_MGMT_TX_STATUS:
		return "EVT_MGMT_TX_STATUS";
	case EVT_REMAIN_CHAN_EXPIRED:
		return "EVT_REMAIN_CHAN_EXPIRED";
	case EVT_MIC_FAIL:
		return "EVT_MIC_FAIL";
	case EVT_NEW_STATION:
		return "EVT_NEW_STATION";
	case EVT_CQM:
		return "EVT_CQM";
	case EVT_MEASUREMENT:
		return "EVT_MEASUREMENT";
	case EVT_TDLS:
		return "EVT_TDLS";
	case EVT_SDIO_SEQ_NUM:
		return "EVT_SDIO_SEQ_NUM";
	case EVT_SDIO_FLOWCON:
		return "EVT_SDIO_FLOWCON";
	case EVT_BA:
		return "EVT_BA";
	case EVT_RSSI_MONITOR:
		return "EVT_RSSI_MONITOR";
	case EVT_GSCAN_FRAME:
		return "EVT_GSCAN_FRAME";
	case EVT_STA_LUT_INDEX:
		return "EVT_STA_LUT_INDEX";
	case EVT_SUSPEND_RESUME:
		return "EVT_SUSPEND_RESUME";
	case EVT_NAN:
		return "EVT_NAN";
	case EVT_RTT:
		return "EVT_RTT";
	case EVT_HANG_RECOVERY:
		return "EVT_HANG_RECOVERY";
	case EVT_THERMAL_WARN:
		return "EVT_THERMAL_WARN";
	case EVT_WFD_MIB_CNT:
		return "EVT_WFD_MIB_CNT";
	case EVT_FW_PWR_DOWN:
		return "EVT_FW_PWR_DOWN";
	case EVT_CHAN_CHANGED:
		return "EVT_CHAN_CHANGED";
	case EVT_COEX_BT_ON_OFF:
		return "EVT_COEX_BT_ON_OFF";
	case EVT_ACS_DONE:
		return "EVT_ACS_DONE";
	case EVT_ACS_LTE_CONFLICT_EVENT:
		return "EVT_ACS_LTE_CONFLICT_EVENT";
	case EVT_FRESH_POWER_BO:
		return "EVT_FRESH_POWER_BO";
	case EVT_REPORT_IP_ADDR:
		return "EVT_REPORT_IP_ADDR";
	default:
		return "WIFI_EVENT_UNKNOWN";
	}
}

static const char *cmdevt_assert_reason_to_str(u8 reason)
{
	switch (reason) {
	case SCAN_ERROR:
		return "SCAN_ERROR";
	case RSP_CNT_ERROR:
		return "RSP_CNT_ERROR";
	case HANDLE_FLAG_ERROR:
		return "HANDLE_FLAG_ERROR";
	case CMD_RSP_TIMEOUT_ERROR:
		return "CMD_RSP_TIMEOUT_ERROR";
	case LOAD_INI_DATA_FAILED:
		return "LOAD_INI_DATA_FAILED";
	case DOWNLOAD_INI_DATA_FAILED:
		return "DOWNLOAD_INI_DATA_FAILED";
	default:
		return "UNKNOWN ASSERT REASON";
	}
}

static u16 cmdevt_crc16(u8 *buf, u16 len)
{
	u16 CRC = 0xFFFF;
	u16 i;
	u8 ch_char;

	for (i = 0; i < len; i++) {
		ch_char = *buf++;
		CRC = CRC_table[(ch_char ^ CRC) & 15] ^ (CRC >> 4);
		CRC = CRC_table[((ch_char >> 4) ^ CRC) & 15] ^ (CRC >> 4);
	}
	return CRC;
}

static const char *cmdevt_err2str(s8 error)
{
	char *str = NULL;

	switch (error) {
	case SPRD_CMD_STATUS_ARG_ERROR:
		str = "SPRD_CMD_STATUS_ARG_ERROR";
		break;
	case SPRD_CMD_STATUS_GET_RESULT_ERROR:
		str = "SPRD_CMD_STATUS_GET_RESULT_ERROR";
		break;
	case SPRD_CMD_STATUS_EXEC_ERROR:
		str = "SPRD_CMD_STATUS_EXEC_ERROR";
		break;
	case SPRD_CMD_STATUS_MALLOC_ERROR:
		str = "SPRD_CMD_STATUS_MALLOC_ERROR";
		break;
	case SPRD_CMD_STATUS_WIFIMODE_ERROR:
		str = "SPRD_CMD_STATUS_WIFIMODE_ERROR";
		break;
	case SPRD_CMD_STATUS_ERROR:
		str = "SPRD_CMD_STATUS_ERROR";
		break;
	case SPRD_CMD_STATUS_CONNOT_EXEC_ERROR:
		str = "SPRD_CMD_STATUS_CONNOT_EXEC_ERROR";
		break;
	case SPRD_CMD_STATUS_NOT_SUPPORT_ERROR:
		str = "SPRD_CMD_STATUS_NOT_SUPPORT_ERROR";
		break;
	case SPRD_CMD_STATUS_CRC_ERROR:
		str = "SPRD_CMD_STATUS_CRC_ERROR";
		break;
	case SPRD_CMD_STATUS_INI_INDEX_ERROR:
		str = "SPRD_CMD_STATUS_INI_INDEX_ERROR";
		break;
	case SPRD_CMD_STATUS_LENGTH_ERROR:
		str = "SPRD_CMD_STATUS_LENGTH_ERROR";
		break;
	case SPRD_CMD_STATUS_OTHER_ERROR:
		str = "SPRD_CMD_STATUS_OTHER_ERROR";
		break;
	case SPRD_CMD_STATUS_OK:
		str = "CMD STATUS OK";
		break;
	default:
		str = "SPRD_CMD_STATUS_UNKNOWN_ERROR";
		break;
	}
	return str;
}

static void cmdevt_set_cmd(struct sprd_cmd *cmd, struct sprd_cmd_hdr *hdr)
{
	u32 msec;
	ktime_t kt;

	kt = ktime_get();
	msec = (u32)div_u64(kt, NSEC_PER_MSEC);
	hdr->mstime = cpu_to_le32(msec);
	spin_lock_bh(&cmd->lock);
	kfree(cmd->data);
	cmd->data = NULL;
	cmd->mstime = msec;
	cmd->cmd_id = hdr->cmd_id;
	atomic_set(&cmd->ignore_resp,
		   hdr->common.rsp == SPRD_HEAD_NORSP ? 1 : 0);
	spin_unlock_bh(&cmd->lock);
}

static void cmdevt_clean_cmd(struct sprd_cmd *cmd)
{
	spin_lock_bh(&cmd->lock);
	kfree(cmd->data);
	cmd->data = NULL;
	cmd->mstime = 0;
	cmd->cmd_id = 0;
	spin_unlock_bh(&cmd->lock);
}

static int cmdevt_lock_cmd(struct sprd_cmd *cmd, struct sprd_hif *hif)
{

	if (atomic_inc_return(&cmd->refcnt) >= SPRD_CMD_EXIT_VAL) {
		atomic_dec(&cmd->refcnt);
		pr_err("%s failed, cmd->refcnt=%d\n",
		       __func__, atomic_read(&cmd->refcnt));
		return -1;
	}
	mutex_lock(&cmd->cmd_lock);
#ifdef DRV_RESET_SELF
	if (hif->cp_asserted == 1) {
		mutex_unlock(&cmd->cmd_lock);
		pr_err("%s failed, cp_asserted unlock cmd_lock\n", __func__);
		return -1;
	}
#endif
	if (hif->priv->is_suspending == 0)
		__pm_stay_awake(cmd->wake_lock);
	pr_debug("cmd->refcnt=%x\n", atomic_read(&cmd->refcnt));

	return 0;
}

static void cmdevt_unlock_cmd(struct sprd_cmd *cmd, struct sprd_hif *hif)
{

	mutex_unlock(&cmd->cmd_lock);
	atomic_dec(&cmd->refcnt);
	if (hif->priv->is_suspending == 0)
		__pm_relax(cmd->wake_lock);
	if (hif->priv->is_suspending == 1)
		hif->priv->is_suspending = 0;
}

/* if erro, data is released in this function
 * if OK, data is released
 */
static int cmdevt_send_cmd(struct sprd_priv *priv, struct sprd_msg *msg)
{
	struct sprd_cmd_hdr *hdr;
	int ret;

	hdr = (struct sprd_cmd_hdr *)(msg->tran_data + priv->hif.hif_offset);
	/*TODO:consider common this if condition since
	 * SPRD_HEAD_NORSP not used any more
	 */
	if (hdr->common.rsp)
		cmdevt_set_cmd(&priv->cmd, hdr);

	pr_debug("[%u]ctx_id %d send[%s]\n",
		le32_to_cpu(hdr->mstime),
		hdr->common.mode, cmdevt_cmd2str(hdr->cmd_id));

	/* Keep command identifiers above, never dump credential-bearing payloads. */

	ret = sprd_chip_tx(&priv->chip, msg);
	if (ret)
		pr_err("%s TX cmd Err: %d\n", __func__, ret);

	return ret;
}

static int cmdevt_recv_rsp_timeout(struct sprd_priv *priv, unsigned int timeout)
{
	int ret;
	struct sprd_cmd *cmd = &priv->cmd;
	struct sprd_hif *hif = &priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	ret = wait_for_completion_timeout(&cmd->completed,
					  msecs_to_jiffies(timeout));
	if (!ret) {
		pr_err("[%s]timeout\n", cmdevt_cmd2str(cmd->cmd_id));
		return -1;
	} else if (sprd_chip_is_exit(&priv->chip) ||
		   atomic_read(&cmd->refcnt) >= SPRD_CMD_EXIT_VAL) {
		pr_err("%s cmd->refcnt=%x\n", __func__,
		       atomic_read(&cmd->refcnt));
		return -1;
	} else if (tx_mgmt->hang_recovery_status == HANG_RECOVERY_ACKED &&
		   cmd->cmd_id != CMD_HANG_RECEIVED) {
		pr_err("%s hang recovery happen\n", __func__);
		return -1;
	}

	spin_lock_bh(&cmd->lock);
	ret = cmd->data ? 0 : -1;
	spin_unlock_bh(&cmd->lock);

	return ret;
}

struct sprd_vif *sc2355_ctxid_to_vif(struct sprd_priv *priv, u8 vif_ctx_id)
{
	struct sprd_vif *vif, *found = NULL;

	spin_lock_bh(&priv->list_lock);
	list_for_each_entry(vif, &priv->vif_list, vif_node) {
		if (vif->ctx_id == vif_ctx_id) {
			vif->ref++;
			found = vif;
			break;
		}
	}
	spin_unlock_bh(&priv->list_lock);

	return found;
}

int sc2355_assert_cmd(struct sprd_priv *priv, struct sprd_vif *vif, u8 cmd_id,
		      u8 reason)
{
	struct sprd_hif *hif = &priv->hif;
	struct rx_mgmt *rx_mgmt = NULL;
	char buf[ASSERT_INFO_BUF_SIZE] = { 0 };
	u8 idx = 0;

	pr_err("%s cmd_id:%d, reason:%d, cp_asserted:%d\n",
	       __func__, cmd_id, reason, hif->cp_asserted);

	rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	if (rx_mgmt) {
		pr_err("%s latest rx chn %u (%llu %llu).\n", __func__,
			rx_mgmt->rx_chn, rx_mgmt->rx_handle_ns, rx_mgmt->rx_queue_ns);
	}

	if (hif->cp_asserted == 0) {
		hif->cp_asserted = 1;

		if ((strlen(cmdevt_cmd2str(cmd_id)) +
		     strlen(cmdevt_assert_reason_to_str(reason)) + strlen("[CMD] ") +
		     strlen(", [REASON] ")) < ASSERT_INFO_BUF_SIZE)
			idx +=
			    sprintf(buf + idx, "[CMD] %s, [REASON] %s",
				    cmdevt_cmd2str(cmd_id),
				    cmdevt_assert_reason_to_str(reason));
		else
			idx +=
			    sprintf(buf + idx, "[CMD ID] %d, [REASON ID] %d",
				    cmd_id, reason);

		buf[idx] = '\0';

		mdbg_assert_interface(buf);

		return 1;
	} else {
		return -1;
	}

#undef ASSERT_INFO_BUF_SIZE
}

struct sprd_msg *sc2355_get_cmdbuf(struct sprd_priv *priv, struct sprd_vif *vif,
				   u16 len, u8 cmd_id, enum sprd_head_rsp rsp,
				   gfp_t flags)
{
	struct sprd_msg *msg;
	struct sprd_cmd_hdr *hdr;
	u16 plen = sizeof(*hdr) + len;
	enum sprd_mode mode = SPRD_MODE_NONE;	/*default to open new device*/
	u8 ctx_id;

	void *data = NULL;

	if (!sprd_hif_is_on(&priv->hif)) {
		pr_err("%s Drop command %s in case of power off\n",
		       __func__, cmdevt_cmd2str(cmd_id));

		return NULL;
	}

	if (!vif) {
		mode = SPRD_MODE_NONE;
		ctx_id = SPRD_MODE_NONE;
	} else {
		mode = vif->mode;
		ctx_id = vif->ctx_id;
	}

	/* bug：1807181
	 * CMD_SET_MIRACAST command is sent to driver through station mode
	 * by supplicant, but it needs to be sent to CP2 through P2P mode*/
	if ((cmd_id == CMD_SET_MIRACAST) && (vif))
		ctx_id = vif->mode;

	if (cmd_id >= CMD_OPEN) {
		sprd_put_vif(vif);

		if (cmd_id == CMD_POWER_SAVE &&
		    (!atomic_read(&priv->power_back_off)) &&
		    (!vif || !(vif->state & VIF_STATE_OPEN))) {
			pr_err("%s:send [%s] fail because mode close",
			       __func__, cmdevt_cmd2str(cmd_id));
			return NULL;
		}
		if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
			if (cmd_id != CMD_POWER_SAVE &&
			    sprdwcn_bus_get_status() == WCN_BUS_DOWN) {
				pr_err("%s:send [%s] fail because bus done",
				       __func__, cmdevt_cmd2str(cmd_id));
				return NULL;
			}
		}
	}
#ifdef DRV_RESET_SELF
	if (priv->hif.drv_resetting == 1 &&
	   !(cmd_id == CMD_SYNC_VERSION ||
	    cmd_id == CMD_DOWNLOAD_INI ||
	    cmd_id == CMD_GET_INFO ||
	    cmd_id == CMD_OPEN)) {
		pr_err("%s:wifi resetting, cannot send [%s]",
			__func__, cmdevt_cmd2str(cmd_id));
		return NULL;
	}
#endif
	msg = sprd_chip_get_msg(&priv->chip, SPRD_TYPE_CMD, mode);
	if (!msg) {
		pr_err("%s, %d, fail to get msg, mode=%d\n",
		       __func__, __LINE__, mode);
		return NULL;
	}

	data = kzalloc((plen + priv->hif.hif_offset), flags);
	if (data) {
		hdr = (struct sprd_cmd_hdr *)(data + priv->hif.hif_offset);
		hdr->common.type = SPRD_TYPE_CMD;
		hdr->common.reserv = 0;
		hdr->common.rsp = rsp;
		hdr->common.mode = ctx_id;
		hdr->plen = cpu_to_le16(plen);
		hdr->cmd_id = cmd_id;
		sprd_fill_msg(msg, NULL, data, plen);
		msg->data = hdr + 1;
	} else {
		pr_err("%s failed to allocate skb\n", __func__);
		sprd_chip_free_msg(&priv->chip, msg);
		return NULL;
	}
	return msg;
}

/* msg is released in this function or the realy driver
 * rbuf: the msg after sprd_cmd_hdr
 * rlen: input the length of rbuf
 *       output the length of the msg,if *rlen == 0, rbuf get nothing
 */
int sc2355_send_cmd_recv_rsp(struct sprd_priv *priv, struct sprd_msg *msg, u8 *rbuf,
			     u16 *rlen, unsigned int timeout)
{
	u8 cmd_id;
	u16 plen;
	int ret = 0;
	struct sprd_cmd_hdr *hdr;
	u8 ctx_id;
	struct sprd_vif *vif;
	struct sprd_hif *hif;
	struct tx_mgmt *tx_mgmt;
	struct sprd_cmd *cmd = &priv->cmd;

	hif = &priv->hif;
	if (hif->cp_asserted == 1) {
		pr_info("%s CP2 assert\n", __func__);
		sprd_chip_free_msg(&priv->chip, msg);
		kfree(msg->tran_data);
		return -EIO;
	}

	/*
	 * MU300: a command that is not sent is not a success. The lock refused (the driver going away) and the two
	 * blocked windows below returned 0 with nothing sent and, for the blocked ones, *rlen as the caller set it: the
	 * caller then read its own uninitialised reply buffer as the firmware's answer (sc2355_get_station: a station's
	 * signal and rates from the kernel stack, to any user's nl80211 request), and every setting "succeeded" that the
	 * firmware never saw. After kanoqwq's clean-tf-7.2 0e19fe32; FINDINGS 106.
	 */
	ret = sc2355_api_version_available_check(priv, msg);
	if (!ret && cmdevt_lock_cmd(cmd, hif))
		ret = -EIO;
	if (ret) {
		sprd_chip_free_msg(&priv->chip, msg);
		kfree(msg->tran_data);
		if (rlen)
			*rlen = 0;
		if (ret != -EIO)
			pr_err("API check fail, return!!\n");
		goto out;
	}
	hdr = (struct sprd_cmd_hdr *)(msg->tran_data + priv->hif.hif_offset);
	cmd_id = hdr->cmd_id;
	ctx_id = hdr->common.mode;

	if (atomic_read(&priv->hif.block_cmd_after_close) == 1) {
		if (cmd_id != CMD_CLOSE) {
			pr_info("%s need block cmd after close : %s\n",
				__func__, cmdevt_cmd2str(cmd_id));
			sprd_chip_free_msg(&priv->chip, msg);
			kfree(msg->tran_data);
			cmdevt_unlock_cmd(cmd, hif);
			if (rlen)
				*rlen = 0;
			ret = -EBUSY;
			goto out;
		}
	}

	if (atomic_read(&priv->hif.change_iface_block_cmd) == 1) {
		if (cmd_id != CMD_CLOSE && cmd_id != CMD_OPEN) {
			pr_info("%s need block cmd while change iface : %s\n",
				__func__, cmdevt_cmd2str(cmd_id));
			sprd_chip_free_msg(&priv->chip, msg);
			kfree(msg->tran_data);
			cmdevt_unlock_cmd(cmd, hif);
			if (rlen)
				*rlen = 0;
			ret = -EBUSY;
			goto out;
		}
	}

	reinit_completion(&cmd->completed);

	ret = cmdevt_send_cmd(priv, msg);
	if (ret) {
		cmdevt_unlock_cmd(cmd, hif);
		return -1;
	}

	/*
	 * console_loglevel > 4 will cause cmd resp timeout easily,
	 * so adjust timeout to 5 seconds when loglevel > 4.
	 */
	if ((console_loglevel > 4) && (timeout == CMD_WAIT_TIMEOUT))
		timeout = CMD_TIMEOUT_DEBUG_LEVEL;

	ret = cmdevt_recv_rsp_timeout(priv, timeout);
	if (ret != -1) {
#ifndef DRV_RESET_SELF
		if (rbuf && rlen && *rlen) {
#else
		if (rbuf && rlen && *rlen && !hif->cp_asserted) {
#endif
			hdr = (struct sprd_cmd_hdr *)cmd->data;
			plen = le16_to_cpu(hdr->plen) - sizeof(*hdr);
			*rlen = min(*rlen, plen);
			ctx_id = hdr->common.mode;
			memcpy(rbuf, hdr->paydata, *rlen);
			pr_warn("ctx_id:%d cmd_id:%d [%s]rsp received\n",
				ctx_id, cmd_id, cmdevt_cmd2str(cmd_id));
			if (cmd_id == CMD_OPEN)
				rbuf[0] = ctx_id;
		}
	} else {
		pr_err("ctx_id %d [%s]rsp timeout, printk=%d\n",
		       ctx_id, cmdevt_cmd2str(cmd_id), console_loglevel);
		vif = sc2355_ctxid_to_vif(priv, ctx_id);
		if (cmd_id == CMD_CLOSE) {
			sc2355_assert_cmd(priv, vif, cmd_id, CMD_RSP_TIMEOUT_ERROR);
			cmdevt_unlock_cmd(cmd, hif);
			return ret;
		}
		if (vif) {
			tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
			if (!hif->cp_asserted &&
			    tx_mgmt->hang_recovery_status == HANG_RECOVERY_END &&
			    !hif->exit)
				sc2355_assert_cmd(priv, vif, cmd_id,
						  CMD_RSP_TIMEOUT_ERROR);
			sprd_put_vif(vif);
		}
	}
	cmdevt_unlock_cmd(cmd, hif);
out:
	return ret;
}

/* Commands */
int sc2355_cmd_scan(struct sprd_priv *priv, struct sprd_vif *vif, u32 channels,
		    int ssid_len, const u8 *ssid_list, u16 chn_count_5g,
		    const u16 *chns_5g)
{
	struct sprd_msg *msg;
	struct cmd_scan *p;
	struct cmd_rsp_state_code state;
	struct cmd_5g_chn *ext_5g;
	u16 rlen;
	u32 data_len, chns_len_5g;

	chns_len_5g = chn_count_5g * sizeof(*chns_5g);
	data_len = sizeof(*p) + ssid_len + chns_len_5g +
	    sizeof(ext_5g->n_5g_chn);
	msg = get_cmdbuf(priv, vif, data_len, CMD_SCAN);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_scan *)msg->data;
	p->channels = channels;
	if (ssid_len > 0) {
		memcpy(p->ssid, ssid_list, ssid_len);
		p->ssid_len = cpu_to_le16(ssid_len);
	}

	ext_5g = (struct cmd_5g_chn *)(p->ssid + ssid_len);
	if (chn_count_5g > 0) {
		ext_5g->n_5g_chn = chn_count_5g;
		memcpy(ext_5g->chns, chns_5g, chns_len_5g);
	} else {
		ext_5g->n_5g_chn = 0;
	}

	print_hex_dump_debug("scan hex:", DUMP_PREFIX_OFFSET,
			     16, 1, p, data_len, true);

	rlen = sizeof(state);

	return sc2355_send_cmd_recv_rsp(priv, msg, (u8 *)&state, &rlen,
				   CMD_SCAN_WAIT_TIMEOUT);
}

int sc2355_cmd_abort_scan(struct sprd_priv *priv, struct sprd_vif *vif)
{
	struct sprd_msg *msg;
	struct cmd_scan *p;
	struct cmd_rsp_state_code state;
	u16 rlen;

	msg = get_cmdbuf(priv, vif, sizeof(struct cmd_scan), CMD_SCAN);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_scan *)msg->data;
	p->reserved = ABORT_SCAN_MODE;

	rlen = sizeof(state);

	return sc2355_send_cmd_recv_rsp(priv, msg, (u8 *)&state, &rlen,
				   CMD_SCAN_WAIT_TIMEOUT);
}

int sc2355_cmd_sched_scan_start(struct sprd_priv *priv, struct sprd_vif *vif,
				struct sprd_sched_scan *buf)
{
	struct sprd_msg *msg;
	struct cmd_sched_scan_hd *sscan_head = NULL;
	struct cmd_sched_scan_ie_hd *ie_head = NULL;
	struct cmd_sched_scan_ifrc *sscan_ifrc = NULL;
	u16 datalen;
	u8 *p = NULL;
	int len = 0, i, hd_len;

	datalen = sizeof(*sscan_head) + sizeof(*ie_head) + sizeof(*sscan_ifrc)
	    + buf->n_ssids * IEEE80211_MAX_SSID_LEN
	    + buf->n_match_ssids * IEEE80211_MAX_SSID_LEN + buf->ie_len;
	hd_len = sizeof(*ie_head);
	datalen = datalen + (buf->n_ssids ? hd_len : 0)
	    + (buf->n_match_ssids ? hd_len : 0)
	    + (buf->ie_len ? hd_len : 0);

	msg = get_cmdbuf(priv, vif, datalen, CMD_SCHED_SCAN);
	if (!msg)
		return -ENOMEM;

	p = msg->data;

	sscan_head = (struct cmd_sched_scan_hd *)(p + len);
	sscan_head->started = 1;
	sscan_head->buf_flags = SPRD_SCHED_SCAN_BUF_END;
	len += sizeof(*sscan_head);

	ie_head = (struct cmd_sched_scan_ie_hd *)(p + len);
	ie_head->ie_flag = SPRD_SEND_FLAG_IFRC;
	ie_head->ie_len = sizeof(*sscan_ifrc);
	len += sizeof(*ie_head);

	sscan_ifrc = (struct cmd_sched_scan_ifrc *)(p + len);

	sscan_ifrc->interval = buf->interval;
	sscan_ifrc->flags = buf->flags;
	sscan_ifrc->rssi_thold = buf->rssi_thold;
	memcpy(sscan_ifrc->chan, buf->channel, SPRD_TOTAL_CHAN_NR);
	len += ie_head->ie_len;

	if (buf->n_ssids > 0) {
		ie_head = (struct cmd_sched_scan_ie_hd *)(p + len);
		ie_head->ie_flag = SPRD_SEND_FLAG_SSID;
		ie_head->ie_len = buf->n_ssids * IEEE80211_MAX_SSID_LEN;
		len += sizeof(*ie_head);
		for (i = 0; i < buf->n_ssids; i++) {
			memcpy((p + len + i * IEEE80211_MAX_SSID_LEN),
			       buf->ssid[i], IEEE80211_MAX_SSID_LEN);
		}
		len += ie_head->ie_len;
	}

	if (buf->n_match_ssids > 0) {
		ie_head = (struct cmd_sched_scan_ie_hd *)(p + len);
		ie_head->ie_flag = SPRD_SEND_FLAG_MSSID;
		ie_head->ie_len = buf->n_match_ssids * IEEE80211_MAX_SSID_LEN;
		len += sizeof(*ie_head);
		for (i = 0; i < buf->n_match_ssids; i++) {
			memcpy((p + len + i * IEEE80211_MAX_SSID_LEN),
			       buf->mssid[i], IEEE80211_MAX_SSID_LEN);
		}
		len += ie_head->ie_len;
	}

	if (buf->ie_len > 0) {
		ie_head = (struct cmd_sched_scan_ie_hd *)(p + len);
		ie_head->ie_flag = SPRD_SEND_FLAG_IE;
		ie_head->ie_len = buf->ie_len;
		len += sizeof(*ie_head);

		pr_info("%s: ie len is %zu, ie:%s\n",
			__func__, buf->ie_len, buf->ie);
		memcpy((p + len), buf->ie, buf->ie_len);
		len += ie_head->ie_len;
	}

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_cmd_sched_scan_stop(struct sprd_priv *priv, struct sprd_vif *vif)
{
	struct sprd_msg *msg;
	struct cmd_sched_scan_hd *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_SCHED_SCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_sched_scan_hd *)msg->data;
	p->started = 0;
	p->buf_flags = SPRD_SCHED_SCAN_BUF_END;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_gscan_subcmd(struct sprd_priv *priv, struct sprd_vif *vif,
			void *data, u16 subcmd, u16 len, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + len, CMD_GSCAN);

	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = subcmd;

	if (data) {
		p->data_len = len;
		memcpy(p->data, data, len);
	} else {
		p->data_len = 0;
	}
	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_set_gscan_config(struct sprd_priv *priv, struct sprd_vif *vif,
			    void *data, u16 len, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + len, CMD_GSCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = SPRD_GSCAN_SUBCMD_SET_CONFIG;
	p->data_len = len;
	memcpy(p->data, data, len);
	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_set_gscan_scan_config(struct sprd_priv *priv, struct sprd_vif *vif,
				 void *data, u16 len, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + len, CMD_GSCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = SPRD_GSCAN_SUBCMD_SET_SCAN_CONFIG;
	p->data_len = len;
	memcpy(p->data, data, len);
	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_enable_gscan(struct sprd_priv *priv, struct sprd_vif *vif,
			void *data, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + sizeof(int), CMD_GSCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = SPRD_GSCAN_SUBCMD_ENABLE_GSCAN;
	p->data_len = sizeof(int);
	memcpy(p->data, data, p->data_len);
	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_get_gscan_capabilities(struct sprd_priv *priv, struct sprd_vif *vif,
				  u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_GSCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = SPRD_GSCAN_SUBCMD_GET_CAPABILITIES;
	p->data_len = 0;

	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_get_gscan_channel_list(struct sprd_priv *priv, struct sprd_vif *vif,
				  void *data, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	int *band;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + sizeof(*band), CMD_GSCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = SPRD_GSCAN_SUBCMD_GET_CHANNEL_LIST;
	p->data_len = sizeof(*band);

	band = (int *)(msg->data + sizeof(struct cmd_gscan_header));

	*band = *((int *)data);
	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_set_gscan_bssid_hotlist(struct sprd_priv *priv, struct sprd_vif *vif,
				   void *data, u16 len, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + len, CMD_GSCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = SPRD_GSCAN_SUBCMD_SET_HOTLIST;
	p->data_len = len;
	memcpy(p->data, data, len);
	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_set_gscan_bssid_blacklist(struct sprd_priv *priv,
				     struct sprd_vif *vif, void *data,
				     u16 len, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_gscan_header *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + len, CMD_GSCAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_gscan_header *)msg->data;
	p->subcmd = SPRD_WIFI_SUBCMD_SET_BSSID_BLACKLIST;
	p->data_len = len;
	memcpy(p->data, data, len);
	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

void sc2355_vowifi_data_protection(struct sprd_vif *vif)
{
	struct sprd_work *misc_work;

	pr_info("enter--at %s\n", __func__);

	misc_work = sprd_alloc_work(0);
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_VOWIFI_DATA_PROTECTION;

	sprd_queue_work(vif->priv, misc_work);
}

void sc2355_work_host_wakeup_fw(struct sprd_vif *vif)
{
	struct sprd_work *misc_work;

	misc_work = sprd_alloc_work(0);
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return;
	}
	if (!vif) {
		pr_err("%s vif is null!\n", __func__);
		return;
	}
	if (!vif->priv) {
		pr_err("%s priv is null!\n", __func__);
		return;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_HOST_WAKEUP_FW;

	sprd_queue_work(vif->priv, misc_work);
}

int sc2355_cmd_host_wakeup_fw(struct sprd_priv *priv, struct sprd_vif *vif)
{
	struct sprd_msg *msg;
	struct cmd_power_save *p;
	u8 r_buf = -1;
	u16 r_len = 1;
	int ret = 0;
	struct sprd_hif *hif = &priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_POWER_SAVE);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_power_save *)msg->data;
	p->sub_type = SPRD_HOST_WAKEUP_FW;
	p->value = 0;

	ret = send_cmd_recv_rsp(priv, msg, &r_buf, &r_len);

	if (!ret && r_buf == 1) {
		hif->fw_awake = 1;
		sc2355_tx_up(tx_mgmt);
	} else {
		hif->fw_awake = 0;
		pr_err("host wakeup fw cmd failed, ret=%d\n", ret);
	}

	return ret;
}

int sc2355_cmd_req_lte_concur(struct sprd_priv *priv, struct sprd_vif *vif,
			      u8 user_channel)
{
	struct sprd_msg *msg;

	msg = get_cmdbuf(priv, vif, sizeof(user_channel), CMD_REQ_LTE_CONCUR);
	if (!msg)
		return -ENOMEM;

	*(u8 *)msg->data = user_channel;
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_packet_offload(struct sprd_priv *priv, struct sprd_vif *vif,
			      u32 req, u8 enable, u32 interval, u32 len,
			      u8 *data)
{
	struct sprd_msg *msg;
	struct cmd_packet_offload *p;
	struct cmd_packet_offload *packet = NULL;
	u16 r_len = sizeof(*packet);
	u8 r_buf[sizeof(*packet)];

	if (len > (U16_MAX - sizeof(*p))) {
		pr_err("%s err datalen %u.\n", __func__, len);
		return -EINVAL;
	}

	msg = get_cmdbuf(priv, vif, sizeof(*p) + len, CMD_PACKET_OFFLOAD);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_packet_offload *)msg->data;

	p->enable = enable;
	p->req_id = req;
	if (enable) {
		p->period = interval;
		p->len = len;
		memcpy(p->data, data, len);
	}

	return send_cmd_recv_rsp(priv, msg, r_buf, &r_len);
}

int sc2355_externed_llstate(struct sprd_priv *priv, struct sprd_vif *vif,
			    u8 type, u8 subtype, void *buf, u8 len,
			    u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;
	struct cmd_extended_llstate *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_EXTENDED_LLSTAT);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_extended_llstate *)msg->data;
	p->type = type;
	p->subtype = subtype;
	p->len = len;
	memcpy(p->data, buf, len);

	if (type == SUBCMD_SET)
		return send_cmd_recv_rsp(priv, msg, 0, 0);
	else
		return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

void sc2355_random_mac_addr(u8 *addr)
{
	get_random_bytes(addr, ETH_ALEN);
	addr[0] &= 0xfe;	/* unicast */
	addr[0] |= 0x02;	/* locally administered */
}

void sc2355_cmd_init(struct sprd_cmd *cmd)
{
	/* memset(cmd, 0, sizeof(*cmd)); */
	cmd->data = NULL;
	spin_lock_init(&cmd->lock);
	mutex_init(&cmd->cmd_lock);
	cmd->wake_lock = wakeup_source_create("Wi-Fi_cmd_wakelock");
	wakeup_source_add(cmd->wake_lock);
	init_completion(&cmd->completed);
	cmd->init_ok = 1;
}

void sc2355_cmd_deinit(struct sprd_cmd *cmd)
{
	unsigned long timeout;

	atomic_add(SPRD_CMD_EXIT_VAL, &cmd->refcnt);
	if (!completion_done(&cmd->completed))
		complete_all(&cmd->completed);

	timeout = jiffies + msecs_to_jiffies(1000);
	while (atomic_read(&cmd->refcnt) > SPRD_CMD_EXIT_VAL) {
		if (time_after(jiffies, timeout)) {
			pr_err("%s cmd lock timeout\n", __func__);
			break;
		}
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
		usleep_range_state(2000, 2500, TASK_UNINTERRUPTIBLE);
#else
		usleep_range(2000, 2500);
#endif
	}
	cmdevt_clean_cmd(cmd);
	mutex_destroy(&cmd->cmd_lock);
	wakeup_source_remove(cmd->wake_lock);
}

/*Commands to sync API version with firmware*/
int sc2355_sync_version(struct sprd_priv *priv)
{
	struct sprd_msg *msg;
	struct cmd_api_t *drv_api = NULL;
	struct cmd_api_t *fw_api = NULL;
	u16 r_len = sizeof(*fw_api);
	u8 r_buf[sizeof(*fw_api)];
	int ret = 0;

	msg = get_cmdbuf(priv, NULL, sizeof(struct cmd_api_t), CMD_SYNC_VERSION);
	if (!msg)
		return -ENOMEM;
	drv_api = (struct cmd_api_t *)msg->data;
	/*fill drv api version got from local*/
	sc2355_api_version_fill_drv(priv, drv_api);

	ret = send_cmd_recv_rsp(priv, msg, r_buf, &r_len);
	if (!ret && r_len) {
		fw_api = (struct cmd_api_t *)r_buf;
		/*fill fw api version to priv got from firmware*/
		sc2355_api_version_fill_fw(priv, fw_api);
	}
	return ret;
}

static int cmdevt_download_ini(struct sprd_priv *priv, u8 *data, u32 len, u8 sec_num)
{
	int ret = 0;
	struct sprd_msg *msg;
	u8 *p = NULL;
	u16 CRC = 0;

	/*reserved 4 byte of section num for align */
	msg = get_cmdbuf(priv, NULL, len + SPRD_SECTION_NUM + sizeof(CRC), CMD_DOWNLOAD_INI);
	if (!msg)
		return -ENOMEM;

	/*calc CRC value*/
	CRC = cmdevt_crc16(data, len);
	pr_info("CRC value:%d\n", CRC);

	p = msg->data;
	*p = sec_num;

	/*copy data after section num*/
	memcpy((p + SPRD_SECTION_NUM), data, len);
	/*put CRC value at the tail of INI data*/
	memcpy((p + SPRD_SECTION_NUM + len), &CRC, sizeof(CRC));

	ret = send_cmd_recv_rsp(priv, msg, NULL, NULL);
	return ret;
}

void sc2355_download_hw_param(struct sprd_priv *priv)
{
	int ret;
	struct wifi_conf_t *wifi_data;
	struct wifi_conf_sec1_t *sec1;
	struct wifi_conf_sec2_t *sec2;
	struct wifi_config_param_t *wifi_param;
	struct sprd_hif *hif = &priv->hif;

	if (hif->hw_type != SPRD_HW_SC2355_PCIE) {
		if (!cali_ini_need_download(MARLIN_WIFI)) {
			pr_err("RF ini download already, skip!\n");
			return;
		}
	}

	wifi_data = kzalloc(sizeof(*wifi_data), GFP_KERNEL);
	/*init INI data struct */
	/*got ini data from file*/
	ret = sc2355_get_nvm_table(priv, wifi_data);
	if (ret) {
		pr_err("load ini data failed, return\n");
		kfree(wifi_data);
		sc2355_assert_cmd(priv, NULL, CMD_DOWNLOAD_INI, LOAD_INI_DATA_FAILED);
		return;
	}

	pr_info("total config len:%ld,sec1 len:%ld, sec2 len:%ld\n",
		(unsigned long)sizeof(wifi_data),
		(unsigned long)sizeof(*sec1),
		(unsigned long)sizeof(*sec2));
	/*devide wifi_conf into sec1 and sec2 since it's too large*/
	sec1 = (struct wifi_conf_sec1_t *)wifi_data;
	sec2 = (struct wifi_conf_sec2_t *)((char *)wifi_data +
					   sizeof(struct wifi_conf_sec1_t));
	wifi_param = (struct wifi_config_param_t *)(&wifi_data->wifi_param);

	pr_info("download the first section of config file\n");
	ret = cmdevt_download_ini(priv, (u8 *)sec1, sizeof(*sec1), SEC1);
	if (ret) {
		pr_err("download the first section of ini fail,return\n");
		kfree(wifi_data);
		sc2355_assert_cmd(priv, NULL, CMD_DOWNLOAD_INI,
				  DOWNLOAD_INI_DATA_FAILED);
		return;
	}

	pr_info("download the second section of config file\n");
	ret = cmdevt_download_ini(priv, (u8 *)sec2, sizeof(*sec2), SEC2);
	if (ret) {
		pr_err("download the second section of ini fail,return\n");
		kfree(wifi_data);
		sc2355_assert_cmd(priv, NULL, CMD_DOWNLOAD_INI,
				  DOWNLOAD_INI_DATA_FAILED);
		return;
	}

	if (wifi_data->rf_config.rf_data_len) {
		pr_info("download the third section of config file\n");
		pr_info("rf_data_len = %d\n", wifi_data->rf_config.rf_data_len);
		ret = cmdevt_download_ini(priv, wifi_data->rf_config.rf_data,
					  wifi_data->rf_config.rf_data_len, SEC3);
		if (ret) {
			pr_err
			    ("download the third section of ini fail,return\n");
			kfree(wifi_data);
			sc2355_assert_cmd(priv, NULL, CMD_DOWNLOAD_INI,
					  DOWNLOAD_INI_DATA_FAILED);
			return;
		}
	}

	pr_info("download the 4th section of config file\n");
	pr_info("trigger = %d, delta = %d, prefer = %d\n",
		wifi_param->roaming_param.trigger,
		wifi_param->roaming_param.delta,
		wifi_param->roaming_param.band_5g_prefer);
	ret = cmdevt_download_ini(priv, (u8 *)wifi_param, sizeof(*wifi_param), SEC4);
	if (ret) {
		pr_err("download the 4th section of ini fail,return\n");
		kfree(wifi_data);
		sc2355_assert_cmd(priv, NULL, CMD_DOWNLOAD_INI,
				  DOWNLOAD_INI_DATA_FAILED);
		return;
	}

	kfree(wifi_data);
}

static void cmdevt_set_tlv_elmt(u8 *addr, u16 type, u16 len, u8 *data)
{
	struct tlv_data *p = (struct tlv_data *)addr;

	p->type = type;
	p->len = len;
	memcpy(p->data, data, len);
}

int sc2355_get_fw_info(struct sprd_priv *priv)
{
	int ret;
	struct sprd_msg *msg;
	struct cmd_fw_info *p;
	struct tlv_data *tlv;
	u16 r_len = sizeof(*p) + GET_INFO_TLV_RBUF_SIZE;
	u16 r_len_ori = r_len;
	u8 r_buf[sizeof(*p) + GET_INFO_TLV_RBUF_SIZE];
	u8 compat_ver = 0;
	unsigned int len_count = 0;
	bool b_tlv_data_chk = true;
	u16 tlv_len = sizeof(struct ap_version_tlv_elmt);
#ifdef CONFIG_SPRD_WLAN_DEBUG
	u8 ap_version = NOTIFY_AP_VERSION_USER_DEBUG;
#else
	u8 ap_version = NOTIFY_AP_VERSION_USER;
#endif

	memset(r_buf, 0, r_len);
	msg = get_cmdbuf(priv, NULL, tlv_len, CMD_GET_INFO);
	if (!msg)
		return -ENOMEM;

	compat_ver =
	    sc2355_api_version_need_compat_operation(priv, CMD_GET_INFO);
	if (compat_ver) {
		switch (compat_ver) {
		case VERSION_1:
			/*add data struct modification in here!*/
			priv->sync_api.compat = VERSION_1;
			break;
		case VERSION_2:
			/*add data struct modification in here!*/
			priv->sync_api.compat = VERSION_2;
			break;
		case VERSION_3:
			/*add data struct modification in here!*/
			priv->sync_api.compat = VERSION_3;
			break;
		default:
			break;
		}
	}

	cmdevt_set_tlv_elmt((u8 *)msg->data, NOTIFY_AP_VERSION, sizeof(ap_version),
			    &ap_version);

	ret = send_cmd_recv_rsp(priv, msg, r_buf, &r_len);
	if (!ret && r_len) {
		/* Version 1 Section */
		p = (struct cmd_fw_info *)r_buf;
		priv->chip_model = p->chip_model;
		priv->chip_ver = p->chip_version;
		priv->fw_ver = p->fw_version;
		priv->fw_capa = p->fw_capa;
		priv->fw_std = p->fw_std;
		priv->extend_feature = p->extend_feature;
		priv->max_ap_assoc_sta = p->max_ap_assoc_sta;
		priv->max_acl_mac_addrs = p->max_acl_mac_addrs;
		priv->max_mc_mac_addrs = p->max_mc_mac_addrs;
		priv->wnm_ft_support = p->wnm_ft_support;
		len_count += SEC1_LEN;
		/*check sec2 data length got from fw*/
		if ((r_len - len_count) >= sizeof(struct wiphy_sec2_t)) {
			priv->wiphy_sec2_flag = 1;
			pr_info("save wiphy section2 info to sprd_priv\n");
			memcpy(&priv->wiphy_sec2, &p->wiphy_sec2,
			       sizeof(struct wiphy_sec2_t));
			pr_debug("%s, %d, priv->wiphy_sec2.ht_cap_info=%x\n",
				 __func__, __LINE__,
				 priv->wiphy_sec2.ht_cap_info);
		} else {
			goto out;
		}
		len_count += sizeof(struct wiphy_sec2_t);

		if ((r_len - len_count) >= ETH_ALEN) {
			ether_addr_copy(priv->mac_addr, p->mac_addr);
		} else {
			memset(priv->mac_addr, 0x00, ETH_ALEN);
			memset(priv->mac_addr_sta_second, 0x00, ETH_ALEN);
			goto out;
		}
		len_count += ETH_ALEN;

		if ((r_len - len_count) >= 1)
			priv->credit_capa = p->credit_capa;
		else
			priv->credit_capa = TX_WITH_CREDIT;

		/* Version 2 Section */
		if (compat_ver == VERSION_1) {
			/* Set default value for non-version-1 variable */
			priv->ott_supt = OTT_NO_SUPT;
		} else {
			len_count = sizeof(struct cmd_fw_info);
			tlv = (struct tlv_data *)((u8 *)r_buf + len_count);
			while ((len_count + sizeof(struct tlv_data) +
				tlv->len) <= r_len) {
				b_tlv_data_chk = false;
				switch (tlv->type) {
				case GET_INFO_TLV_TP_OTT:
					if (tlv->len == 1) {
						priv->ott_supt =
						    *((unsigned char *)(tlv->data));
						b_tlv_data_chk = true;
					}
					break;
				default:
					break;
				}

				pr_info
				    ("%s, TLV type=%d, len=%d, data_chk=%d\n",
				     __func__, tlv->type, tlv->len,
				     b_tlv_data_chk);

				if (!b_tlv_data_chk) {
					pr_err
					    ("%s TLV check failed: type=%d, len=%d\n",
					     __func__, tlv->type, tlv->len);
					goto out;
				}

				len_count +=
				    (sizeof(struct tlv_data) + tlv->len);
				tlv =
				    (struct tlv_data *)((u8 *)r_buf +
							len_count);
			}

			if (r_len_ori <= r_len) {
				pr_warn
				    ("%s check tlv rbuf size: r_len_ori=%d, r_len=%d\n",
				     __func__, r_len_ori, r_len);
			}

			if (len_count != r_len) {
				pr_err
				    ("%s length mismatch: len_count=%d, r_len=%d\n",
				     __func__, len_count, r_len);
				goto out;
			}
		}

out:
		pr_info("%s, drv_version=%d, fw_version=%d, compat_ver=%d, ap_version=%d\n",
			__func__,
			(&priv->sync_api)->api_array[CMD_GET_INFO].drv_version,
			(&priv->sync_api)->api_array[CMD_GET_INFO].fw_version,
			compat_ver, ap_version);
		pr_info("chip_model:0x%x, chip_ver:0x%x\n", priv->chip_model,
			priv->chip_ver);
		pr_info("fw_ver:%d, fw_std:0x%x, fw_capa:0x%x\n", priv->fw_ver,
			priv->fw_std, priv->fw_capa);
		if (is_valid_ether_addr(priv->mac_addr))
			pr_info("mac_addr:%02x:%02x:%02x:%02x:%02x:%02x\n",
				priv->mac_addr[0], priv->mac_addr[1],
				priv->mac_addr[2], priv->mac_addr[3],
				priv->mac_addr[4], priv->mac_addr[5]);
		pr_info("credit_capa:%s\n",
			(priv->credit_capa ==
			 TX_WITH_CREDIT) ? "TX_WITH_CREDIT" : "TX_NO_CREDIT");
		pr_info("ott support:%d\n", priv->ott_supt);
	}

	return ret;
}

int sc2355_set_regdom(struct sprd_priv *priv, u8 *regdom, u32 len)
{
	struct sprd_msg *msg;
	struct sprd_ieee80211_regdomain *p;
	u8 compat_ver = 0;

	msg = get_cmdbuf(priv, NULL, len, CMD_SET_REGDOM);
	if (!msg)
		return -ENOMEM;
	compat_ver =
	    sc2355_api_version_need_compat_operation(priv, CMD_SET_REGDOM);
	if (compat_ver) {
		switch (compat_ver) {
		case VERSION_1:
			/*add data struct modification in here!*/
			priv->sync_api.compat = VERSION_1;
			break;
		case VERSION_2:
			/*add data struct modification in here!*/
			priv->sync_api.compat = VERSION_2;
			break;
		case VERSION_3:
			/*add data struct modification in here!*/
			priv->sync_api.compat = VERSION_3;
			break;
		default:
			break;
		}
	}
	p = (struct sprd_ieee80211_regdomain *)msg->data;
	memcpy(p, regdom, len);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_open_fw(struct sprd_priv *priv, struct sprd_vif *vif, u8 *mac_addr)
{
	struct sprd_msg *msg;
	struct cmd_open *p;
	u16 rlen = 1;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_OPEN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_open *)msg->data;
	p->mode = vif->mode;
	if (mac_addr)
		memcpy(&p->mac[0], mac_addr, sizeof(p->mac));
	else
		pr_err("%s, %d, mac_addr error!\n", __func__, __LINE__);

	p->reserved = 0;
	if (wfa_cap) {
		p->reserved = wfa_cap;
		wfa_cap = 0;
	}

	return send_cmd_recv_rsp(priv, msg, &vif->ctx_id, &rlen);
}

int sc2355_close_fw(struct sprd_priv *priv, struct sprd_vif *vif)
{
	struct sprd_msg *msg;
	struct cmd_close *p;
	struct tx_mgmt *tx_mgmt;
	struct sprd_hif *hif;
	int i;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_CLOSE);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_close *)msg->data;
	p->mode = vif->mode;

	send_cmd_recv_rsp(priv, msg, NULL, NULL);
	/* FIXME - in case of close failure */

	hif = &vif->priv->hif;
	tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	for (i = 0; i < MAX_COLOR_BIT; i++) {
		if (tx_mgmt->flow_ctrl[i].mode == vif->mode) {
			pr_info(" %s, %d, _fc_, clear mode%d because closed\n",
				__func__, __LINE__, vif->mode);
			tx_mgmt->flow_ctrl[i].mode = SPRD_MODE_NONE;
		}
	}

	return 0;
}

int sc2355_power_save(struct sprd_priv *priv, struct sprd_vif *vif,
		      u8 sub_type, u8 status)
{
	struct sprd_msg *msg;
	struct cmd_power_save *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_POWER_SAVE);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_power_save *)msg->data;
	p->sub_type = sub_type;
	p->value = status;
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_sar(struct sprd_priv *priv, struct sprd_vif *vif,
		   u8 sub_type, s8 value)
{
	struct sprd_msg *msg;
	struct cmd_set_sar *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_POWER_SAVE);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_set_sar *)msg->data;
	p->power_save_type = SPRD_SET_SAR;
	p->sub_type = sub_type;
	p->value = value;
	p->mode = SPRD_SET_SAR_ALL_MODE;
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_power_backoff(struct sprd_priv *priv, struct sprd_vif *vif,
			     struct sprd_power_backoff *data)
{
	struct sprd_msg *msg;
	struct cmd_set_power_backoff *p;
	int i;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_POWER_SAVE);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_set_power_backoff *)msg->data;
	memset(p, 0, sizeof(*p));
	p->power_save_type = SPRD_SET_POWER_BACKOFF;
	if (data)
		memcpy(&p->backoff, data, sizeof(*data));
	for (i = 0; i < sizeof(*p); i++)
		pr_debug("%hhu\t", *((u8 *)p + i));
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_enable_miracast(struct sprd_priv *priv,
			   struct sprd_vif *vif, int val)
{
	struct sprd_msg *msg;
	struct cmd_miracast *p = NULL;

	msg = get_cmdbuf(priv, vif, sizeof(*p),
			 CMD_SET_MIRACAST);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_miracast *)msg->data;
	p->value = val;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_add_key(struct sprd_priv *priv, struct sprd_vif *vif,
		   const u8 *key_data, u8 key_len, bool pairwise, u8 key_index,
		   const u8 *key_seq, u8 cypher_type, const u8 *mac_addr)
{
	struct sprd_msg *msg;
	struct cmd_add_key *p;
	u8 *sub_cmd;
	int datalen = sizeof(*p) + sizeof(*sub_cmd) + key_len;

	msg = get_cmdbuf(priv, vif, datalen, CMD_KEY);
	if (!msg)
		return -ENOMEM;

	sub_cmd = (u8 *)msg->data;
	*sub_cmd = SUBCMD_ADD;
	p = (struct cmd_add_key *)(++sub_cmd);

	p->key_index = key_index;
	p->pairwise = (u8)pairwise;
	p->cypher_type = cypher_type;
	p->key_len = key_len;
	if (key_seq) {
		if (cypher_type == SPRD_CIPHER_WAPI)
			memcpy(p->keyseq, key_seq, WAPI_PN_SIZE);
		else
			memcpy(p->keyseq, key_seq, 8);
	}
	if (mac_addr)
		ether_addr_copy(p->mac, mac_addr);
	if (key_data)
		memcpy(p->value, key_data, key_len);

	if (mac_addr)
		sc2355_reset_pn(priv, mac_addr);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_del_key(struct sprd_priv *priv, struct sprd_vif *vif, u8 key_index,
		   bool pairwise, const u8 *mac_addr)
{
	struct sprd_msg *msg;
	struct cmd_del_key *p;
	u8 *sub_cmd;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + sizeof(*sub_cmd), CMD_KEY);
	if (!msg)
		return -ENOMEM;

	sub_cmd = (u8 *)msg->data;
	*sub_cmd = SUBCMD_DEL;
	p = (struct cmd_del_key *)(++sub_cmd);

	p->key_index = key_index;
	p->pairwise = (u8)pairwise;
	if (mac_addr)
		ether_addr_copy(p->mac, mac_addr);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_def_key(struct sprd_priv *priv, struct sprd_vif *vif,
		       u8 key_index)
{
	struct sprd_msg *msg;
	struct cmd_set_def_key *p;
	u8 *sub_cmd;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + sizeof(*sub_cmd), CMD_KEY);
	if (!msg)
		return -ENOMEM;

	sub_cmd = (u8 *)msg->data;
	*sub_cmd = SUBCMD_SET;
	p = (struct cmd_set_def_key *)(++sub_cmd);

	p->key_index = key_index;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

static int cmdevt_set_ie(struct sprd_priv *priv, struct sprd_vif *vif, u8 type,
			 const u8 *ie, u16 len)
{
	struct sprd_msg *msg;
	struct cmd_set_ie *p;
	size_t datalen = sizeof(*p) + len;

	if (datalen > 0xFFFF) {
		pr_err("%s err datalen %zu.\n", __func__, datalen);
		return -EINVAL;
	}

	msg = get_cmdbuf(priv, vif, (u16)datalen, CMD_SET_IE);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_set_ie *)msg->data;
	p->type = type;
	p->len = len;
	memcpy(p->data, ie, len);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_beacon_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			 const u8 *ie, u16 len)
{
	return cmdevt_set_ie(priv, vif, SPRD_IE_BEACON, ie, len);
}

int sc2355_set_probereq_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			   const u8 *ie, u16 len)
{
	return cmdevt_set_ie(priv, vif, SPRD_IE_PROBE_REQ, ie, len);
}

int sc2355_set_proberesp_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			    const u8 *ie, u16 len)
{
	return cmdevt_set_ie(priv, vif, SPRD_IE_PROBE_RESP, ie, len);
}

int sc2355_set_assocreq_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			   const u8 *ie, u16 len)
{
	return cmdevt_set_ie(priv, vif, SPRD_IE_ASSOC_REQ, ie, len);
}

int sc2355_set_assocresp_ie(struct sprd_priv *priv, struct sprd_vif *vif,
			    const u8 *ie, u16 len)
{
	return cmdevt_set_ie(priv, vif, SPRD_IE_ASSOC_RESP, ie, len);
}

int sc2355_set_sae_ie(struct sprd_priv *priv, struct sprd_vif *vif,
		      const u8 *ie, u16 len)
{
	return cmdevt_set_ie(priv, vif, SPRD_IE_SAE, ie, len);
}

int sc2355_start_ap(struct sprd_priv *priv, struct sprd_vif *vif, u8 *beacon,
		    u16 len, struct cfg80211_ap_settings *settings)
{
	struct sprd_msg *msg;
	struct cmd_start_ap *p;
	u16 datalen = sizeof(*p) + len;
	struct sprd_api_version_t *api = (&priv->sync_api)->api_array;
	u8 fw_ver = 0;

	fw_ver = (api + CMD_SCAN)->fw_version;
	if (vif->priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
		u8 drv_ver = 0;

		drv_ver = (api + CMD_SCAN)->drv_version;
		fw_ver = min(fw_ver, drv_ver);
	}
	if (vif->mode == SPRD_MODE_AP &&
	    !list_empty(&vif->survey_info_list) && fw_ver == 1) {
		clean_survey_info_list(vif);
	}

	msg = get_cmdbuf(priv, vif, datalen, CMD_START_AP);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_start_ap *)msg->data;
	p->len = cpu_to_le16(len);
	memcpy(p->value, beacon, len);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_del_station(struct sprd_priv *priv, struct sprd_vif *vif,
		       const u8 *mac_addr, u16 reason_code)
{
	struct sprd_msg *msg;
	struct cmd_del_station *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_DEL_STATION);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_del_station *)msg->data;
	if (mac_addr)
		memcpy(&p->mac[0], mac_addr, sizeof(p->mac));
	p->reason_code = cpu_to_le16(reason_code);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_get_station(struct sprd_priv *priv, struct sprd_vif *vif,
		       struct sprd_sta_info *sta)
{
	struct sprd_msg *msg;
	/* zeroed: a reply shorter than the structure (or none) must not hand on the stack's bytes */
	struct cmd_get_station get_sta = {};
	u8 *r_buf = (u8 *)&get_sta;
	u16 r_len = sizeof(get_sta);
	int ret;

	msg = get_cmdbuf(priv, vif, 0, CMD_GET_STATION);
	if (!msg)
		return -ENOMEM;
	ret = send_cmd_recv_rsp(priv, msg, r_buf, &r_len);

	sta->tx_rate = get_sta.tx_rate;
	sta->rx_rate = get_sta.rx_rate;
	sta->signal = get_sta.signal;
	sta->noise = get_sta.noise;
	sta->txfailed = get_sta.txfailed;

	return ret;
}

int sc2355_set_channel(struct sprd_priv *priv, struct sprd_vif *vif, u8 channel)
{
	struct sprd_msg *msg;
	struct cmd_set_channel *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_SET_CHANNEL);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_set_channel *)msg->data;
	p->channel = channel;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_connect(struct sprd_priv *priv, struct sprd_vif *vif,
		   struct cmd_connect *p)
{
	struct sprd_msg *msg;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_CONNECT);
	if (!msg)
		return -ENOMEM;

	memcpy(msg->data, p, sizeof(*p));

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_disconnect(struct sprd_priv *priv, struct sprd_vif *vif,
		      u16 reason_code)
{
	struct sprd_msg *msg;
	struct cmd_disconnect *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_DISCONNECT);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_disconnect *)msg->data;
	p->reason_code = cpu_to_le16(reason_code);

	return sc2355_send_cmd_recv_rsp(priv, msg, NULL, NULL,
				   CMD_DISCONNECT_TIMEOUT);
}

int sc2355_set_param(struct sprd_priv *priv, u32 rts, u32 frag)
{
	struct sprd_msg *msg;
	struct cmd_set_param *p;

	msg = get_cmdbuf(priv, NULL, sizeof(*p), CMD_SET_PARAM);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_set_param *)msg->data;
	p->rts = cpu_to_le32(rts);
	p->frag = cpu_to_le32(frag);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_pmksa(struct sprd_priv *priv, struct sprd_vif *vif, const u8 *bssid,
		 const u8 *pmkid, u8 type)
{
	struct sprd_msg *msg;
	struct cmd_pmkid *p;
	u8 *sub_cmd;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + sizeof(*sub_cmd), CMD_SET_PMKSA);
	if (!msg)
		return -ENOMEM;

	sub_cmd = (u8 *)msg->data;
	*sub_cmd = type;
	p = (struct cmd_pmkid *)(++sub_cmd);

	if (bssid)
		memcpy(p->bssid, bssid, sizeof(p->bssid));
	if (pmkid)
		memcpy(p->pmkid, pmkid, sizeof(p->pmkid));

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_qos_map(struct sprd_priv *priv, struct sprd_vif *vif, void *map)
{
	struct sprd_msg *msg;
	struct cmd_qos_map *p;
	int index;

	if (!map)
		return 0;
	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_SET_QOS_MAP);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_qos_map *)msg->data;
	memset((u8 *)p, 0, sizeof(*p));
	memcpy((u8 *)p, map, sizeof(*p));
	memcpy(&qos_map.qos_exceptions[0], &p->dscp_exception[0],
	       sizeof(struct cmd_dscp_exception) * QOS_MAP_MAX_DSCP_EXCEPTION);

	for (index = 0; index < 8; index++) {
		qos_map.qos_ranges[index].low = p->up[index].low;
		qos_map.qos_ranges[index].high = p->up[index].high;
		qos_map.qos_ranges[index].up = index;
	}

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_add_tx_ts(struct sprd_priv *priv, struct sprd_vif *vif, u8 tsid,
		     const u8 *peer, u8 user_prio, u16 admitted_time)
{
	struct sprd_msg *msg;
	struct cmd_tx_ts *p;
	enum qos_edca_ac_t ac;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_ADD_TX_TS);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_tx_ts *)msg->data;
	memset((u8 *)p, 0, sizeof(*p));

	p->tsid = tsid;
	ether_addr_copy(p->peer, peer);
	p->user_prio = user_prio;
	p->admitted_time = cpu_to_le16(admitted_time);

	ac = sc2355_qos_map_priority_to_edca_ac(p->user_prio);
	sc2355_qos_update_wmmac_ts_info(p->tsid, p->user_prio, ac, true,
					p->admitted_time);
	sc2355_qos_update_admitted_time(priv, p->tsid, p->admitted_time, true);
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_del_tx_ts(struct sprd_priv *priv, struct sprd_vif *vif, u8 tsid,
		     const u8 *peer)
{
	struct sprd_msg *msg;
	struct cmd_tx_ts *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_DEL_TX_TS);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_tx_ts *)msg->data;
	memset((u8 *)p, 0, sizeof(*p));

	p->tsid = tsid;
	ether_addr_copy(p->peer, peer);

	p->admitted_time = sc2355_qos_get_wmmac_admitted_time(p->tsid);
	sc2355_qos_update_admitted_time(priv, p->tsid, p->admitted_time, false);
	sc2355_qos_remove_wmmac_ts_info(p->tsid);
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_remain_chan(struct sprd_priv *priv, struct sprd_vif *vif,
		       struct ieee80211_channel *channel,
		       enum nl80211_channel_type channel_type,
		       u32 duration, u64 *cookie)
{
	struct sprd_msg *msg;
	struct cmd_remain_chan *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_REMAIN_CHAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_remain_chan *)msg->data;
	p->chan = ieee80211_frequency_to_channel(channel->center_freq);
	p->chan_type = channel_type;
	p->duraion = cpu_to_le32(duration);
	p->cookie = cpu_to_le64(*cookie);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_cancel_remain_chan(struct sprd_priv *priv, struct sprd_vif *vif,
			      u64 cookie)
{
	struct sprd_msg *msg;
	struct cmd_cancel_remain_chan *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_CANCEL_REMAIN_CHAN);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_cancel_remain_chan *)msg->data;
	p->cookie = cpu_to_le64(cookie);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_tx_mgmt(struct sprd_priv *priv, struct sprd_vif *vif, u8 channel,
		   u8 dont_wait_for_ack, u32 wait, u64 *cookie,
		   const u8 *buf, size_t len)
{
	struct sprd_msg *msg;
	struct cmd_mgmt_tx *p;
	size_t datalen = sizeof(*p) + len;

	if (datalen > 0xFFFF) {
		pr_err("%s err datalen %zu.\n", __func__, datalen);
		return -EINVAL;
	}

	msg = get_cmdbuf(priv, vif, (u16)datalen, CMD_TX_MGMT);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_mgmt_tx *)msg->data;

	p->chan = channel;
	p->dont_wait_for_ack = dont_wait_for_ack;
	p->wait = cpu_to_le32(wait);
	if (cookie)
		p->cookie = cpu_to_le64(*cookie);
	p->len = cpu_to_le16(len);
	memcpy(p->value, buf, len);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_register_frame(struct sprd_priv *priv, struct sprd_vif *vif,
			  u16 type, u8 reg)
{
	struct sprd_msg *msg;
	struct cmd_register_frame *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_REGISTER_FRAME);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_register_frame *)msg->data;
	p->type = type;
	p->reg = reg;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_cqm_rssi(struct sprd_priv *priv, struct sprd_vif *vif,
			s32 rssi_thold, u32 rssi_hyst)
{
	struct sprd_msg *msg;
	struct cmd_cqm_rssi *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_SET_CQM);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_cqm_rssi *)msg->data;
	p->rssih = cpu_to_le32(rssi_thold);
	p->rssil = cpu_to_le32(rssi_hyst);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_roam_offload(struct sprd_priv *priv, struct sprd_vif *vif,
			    u8 sub_type, const u8 *data, u8 len)
{
	struct sprd_msg *msg;
	struct cmd_roam_offload_data *p;

	if (!(priv->fw_capa & SPRD_CAPA_11R_ROAM_OFFLOAD)) {
		pr_err("%s, not supported\n", __func__);
		return -ENOTSUPP;
	}
	msg = get_cmdbuf(priv, vif, sizeof(*p) + len, CMD_SET_ROAM_OFFLOAD);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_roam_offload_data *)msg->data;
	p->type = sub_type;
	p->len = len;
	memcpy(p->value, data, len);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

static int cmdevt_send_tdls_by_cmd(struct sk_buff *skb, struct sprd_vif *vif)
{
	struct sprd_msg *msg;
	struct cmd_tdls *p;
	struct sprd_hif *hif;

	hif = &vif->priv->hif;
	msg = get_cmdbuf(vif->priv, vif, sizeof(*p) + skb->len, CMD_TDLS);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_tdls *)msg->data;
	p->tdls_sub_cmd_mgmt = WLAN_TDLS_CMD_TX_DATA;
	ether_addr_copy(p->da, skb->data);
	p->paylen = skb->len;/*TBD*/
	memcpy(p->payload, skb->data, skb->len);

	return send_cmd_recv_rsp(vif->priv, msg, NULL, NULL);
}

int sc2355_tdls_mgmt(struct sprd_vif *vif, struct sk_buff *skb)
{
	int ret;

	/* temp debug use */
	if (skb_headroom(skb) < vif->ndev->needed_headroom)
		pr_err("%s skb head len err:%d %d\n",
		       __func__, skb_headroom(skb), vif->ndev->needed_headroom);
	/*send TDLS mgmt through cmd port instead of data port,needed by CP2*/
	ret = cmdevt_send_tdls_by_cmd(skb, vif);
	if (ret) {
		pr_err("%s drop msg due to TX Err\n", __func__);
		goto out;
	}

	vif->ndev->stats.tx_bytes += skb->len;
	vif->ndev->stats.tx_packets++;

	print_hex_dump_debug("TX packet: ", DUMP_PREFIX_OFFSET,
			     16, 1, skb->data, skb->len, 0);
out:
	return ret;
}

int sc2355_send_tdls_cmd(struct sprd_vif *vif, const u8 *peer, int oper)
{
	struct sprd_work *misc_work;
	struct sprd_tdls_work tdls;

	tdls.vif_ctx_id = vif->ctx_id;
	if (peer)
		ether_addr_copy(tdls.peer, peer);
	tdls.oper = oper;

	misc_work = sprd_alloc_work(sizeof(struct sprd_tdls_work));
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return -1;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_TDLS_CMD;
	memcpy(misc_work->data, &tdls, sizeof(struct sprd_tdls_work));

	sprd_queue_work(vif->priv, misc_work);
	return 0;
}

void sc2355_tdls_count_flow(struct sprd_vif *vif, u8 *data, u16 len)
{
	u8 i, found = 0;
	u32 msec;
	u8 elapsed_time;
	u8 unit_time;
	ktime_t kt;
	struct sprd_hif *hif = &vif->priv->hif;
	int ret = 0;

	for (i = 0; i < MAX_TDLS_PEER; i++) {
		if (hif->tdls_flow_count[i].valid == 1 &&
		    ether_addr_equal(data, hif->tdls_flow_count[i].da))
			goto count_it;
	}
	return;

count_it:
	if (get_tdls_threshold())
		hif->tdls_flow_count[i].threshold = get_tdls_threshold();
	kt = ktime_get();
	msec = (u32)(div_u64(kt, NSEC_PER_MSEC));
	elapsed_time =
	    (msec - hif->tdls_flow_count[i].start_mstime) / MSEC_PER_SEC;
	unit_time = elapsed_time / hif->tdls_flow_count[i].timer;
	pr_info("%s,%d, tdls_id=%d, len_counted=%d, len=%d, threshold=%dK\n",
		__func__, __LINE__, i,
		hif->tdls_flow_count[i].data_len_counted, len,
		hif->tdls_flow_count[i].threshold);
	pr_info("currenttime=%u, elapsetime=%d, unit_time=%d\n",
		msec, elapsed_time, unit_time);

	if ((hif->tdls_flow_count[i].data_len_counted == 0 &&
	     len > (hif->tdls_flow_count[i].threshold * 1024)) ||
	    (hif->tdls_flow_count[i].data_len_counted > 0 &&
	     ((hif->tdls_flow_count[i].data_len_counted + len) >
	      hif->tdls_flow_count[i].threshold * 1024 *
	      (unit_time == 0 ? 1 : unit_time)))) {
		ret = sc2355_send_tdls_cmd(vif,
					   (u8 *)hif->tdls_flow_count[i].da,
					   SPRD_TDLS_CMD_CONNECT);
		memset(&hif->tdls_flow_count[i], 0,
		       sizeof(struct tdls_flow_count_para));
	} else {
		if (hif->tdls_flow_count[i].data_len_counted == 0) {
			hif->tdls_flow_count[i].start_mstime = msec;
			hif->tdls_flow_count[i].data_len_counted += len;
		}
		if (hif->tdls_flow_count[i].data_len_counted > 0 &&
		    unit_time > 1) {
			hif->tdls_flow_count[i].start_mstime = msec;
			hif->tdls_flow_count[i].data_len_counted = len;
		}
		if (hif->tdls_flow_count[i].data_len_counted > 0 &&
		    unit_time <= 1) {
			hif->tdls_flow_count[i].data_len_counted += len;
		}
	}
	for (i = 0; i < MAX_TDLS_PEER; i++) {
		if (hif->tdls_flow_count[i].valid == 1)
			found++;
	}
	if (found == 0)
		hif->tdls_flow_count_enable = 0;
}

int sc2355_tdls_oper(struct sprd_priv *priv, struct sprd_vif *vif,
		     const u8 *peer, int oper)
{
	struct sprd_msg *msg;
	struct cmd_tdls *p;
	int ret;

	if (oper == SPRD_TDLS_ENABLE_LINK)
		cmdevt_flush_tdls_flow(vif, peer, oper);

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_TDLS);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_tdls *)msg->data;
	if (peer)
		ether_addr_copy(p->da, peer);
	p->tdls_sub_cmd_mgmt = oper;

	ret = send_cmd_recv_rsp(priv, msg, NULL, NULL);
	if (!ret && oper == SPRD_TDLS_ENABLE_LINK) {
		u8 i;
		struct sprd_hif *hif;

		hif = &vif->priv->hif;
		for (i = 0; i < MAX_LUT_NUM; i++) {
			if ((memcmp(hif->peer_entry[i].tx.da,
				    peer, ETH_ALEN) == 0) &&
			    hif->peer_entry[i].ctx_id == vif->ctx_id) {
				pr_info("%s, %d, lut_index=%d\n",
					__func__, __LINE__,
					hif->peer_entry[i].lut_index);
				hif->peer_entry[i].ip_acquired = 1;
				break;
			}
		}
	}

	return ret;
}

int sc2355_start_tdls_channel_switch(struct sprd_priv *priv,
				     struct sprd_vif *vif, const u8 *peer_mac,
				     u8 primary_chan, u8 second_chan_offset,
				     u8 band)
{
	struct sprd_msg *msg;
	struct cmd_tdls *p;
	struct cmd_tdls_channel_switch chan_switch;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + sizeof(chan_switch), CMD_TDLS);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_tdls *)msg->data;
	p->tdls_sub_cmd_mgmt = SPRD_TDLS_START_CHANNEL_SWITCH;
	if (peer_mac)
		ether_addr_copy(p->da, peer_mac);
	p->initiator = 1;
	chan_switch.primary_chan = primary_chan;
	chan_switch.second_chan_offset = second_chan_offset;
	chan_switch.band = band;
	p->paylen = sizeof(chan_switch);
	memcpy(p->payload, &chan_switch, p->paylen);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_cancel_tdls_channel_switch(struct sprd_priv *priv,
				      struct sprd_vif *vif, const u8 *peer_mac)
{
	struct sprd_msg *msg;
	struct cmd_tdls *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_TDLS);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_tdls *)msg->data;
	p->tdls_sub_cmd_mgmt = SPRD_TDLS_CANCEL_CHANNEL_SWITCH;
	if (peer_mac)
		ether_addr_copy(p->da, peer_mac);
	p->initiator = 1;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_notify_ip(struct sprd_priv *priv, struct sprd_vif *vif, u8 ip_type,
		     u8 *ip_addr)
{
	struct sprd_msg *msg;
	struct sprd_peer_entry *entry;
	u8 *ip_value;
	u8 ip_len;
	struct sprd_hif *hif = &priv->hif;

	if (ip_type != SPRD_IPV4 && ip_type != SPRD_IPV6)
		return -EINVAL;

	if (hif->hw_type == SPRD_HW_SC2355_PCIE)
		entry = sc2355_pcie_find_peer_entry_using_addr(vif, vif->bssid);
	else
		entry = sc2355_find_peer_entry_using_addr(vif, vif->bssid);
	if (entry && ip_type == SPRD_IPV4) {
		if (entry->ctx_id == vif->ctx_id)
			entry->ip_acquired = 1;
		else
			pr_err("ctx_id(%d) mismatch\n", entry->ctx_id);
	}

	ip_len = (ip_type == SPRD_IPV4) ?
	    SPRD_IPV4_ADDR_LEN : SPRD_IPV6_ADDR_LEN;
	msg = get_cmdbuf(priv, vif, ip_len, CMD_NOTIFY_IP_ACQUIRED);
	if (!msg)
		return -ENOMEM;
	ip_value = (unsigned char *)msg->data;
	memcpy(ip_value, ip_addr, ip_len);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_blacklist(struct sprd_priv *priv,
			 struct sprd_vif *vif, u8 sub_type, u8 num,
			 u8 *mac_addr)
{
	struct sprd_msg *msg;
	struct cmd_blacklist *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + num * ETH_ALEN,
			 CMD_SET_BLACKLIST);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_blacklist *)msg->data;
	p->sub_type = sub_type;
	p->num = num;
	if (mac_addr)
		memcpy(p->mac, mac_addr, num * ETH_ALEN);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_whitelist(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 sub_type, u8 num, u8 *mac_addr)
{
	struct sprd_msg *msg;
	struct cmd_set_mac_addr *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + num * ETH_ALEN,
			 CMD_SET_WHITELIST);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_set_mac_addr *)msg->data;
	p->sub_type = sub_type;
	p->num = num;
	if (mac_addr)
		memcpy(p->mac, mac_addr, num * ETH_ALEN);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_mc_filter(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 sub_type, u8 num, u8 *mac_addr)
{
	struct sprd_msg *msg;
	struct cmd_set_mac_addr *p;

	if (priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
		/*wcn bus is down, drop skb*/
		if (sprdwcn_bus_get_status() == WCN_BUS_DOWN) {
			pr_err("%s,wcn bus is down, drop cmd!\n", __func__);
			return 0;
		}
	}

	msg = get_cmdbuf(priv, vif, sizeof(*p) + num * ETH_ALEN,
			 CMD_MULTICAST_FILTER);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_set_mac_addr *)msg->data;
	p->sub_type = sub_type;
	p->num = num;
	if (num && mac_addr)
		memcpy(p->mac, mac_addr, num * ETH_ALEN);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_npi_send_recv(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 *s_buf, u16 s_len, u8 *r_buf, u16 *r_len)
{
	struct sprd_msg *msg;

	msg = get_cmdbuf(priv, vif, s_len, CMD_NPI_MSG);
	if (!msg)
		return -ENOMEM;
	memcpy(msg->data, s_buf, s_len);

	return send_cmd_recv_rsp(priv, msg, r_buf, r_len);
}

int sc2355_set_11v_feature_support(struct sprd_priv *priv,
				   struct sprd_vif *vif, u16 val)
{
	struct sprd_msg *msg = NULL;
	struct cmd_rsp_state_code state;
	struct cmd_11v *p = NULL;
	u16 rlen = sizeof(state);

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_11V);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_11v *)msg->data;

	p->cmd = SUBCMD_SET;
	p->value = (val << 16) | val;
	/* len  only 8 =  cmd(2) + len(2) +value(4) */
	p->len = 8;

	return send_cmd_recv_rsp(priv, msg, (u8 *)&state, &rlen);
}

int sc2355_set_11v_sleep_mode(struct sprd_priv *priv, struct sprd_vif *vif,
			      u8 status, u16 interval)
{
	struct sprd_msg *msg = NULL;
	struct cmd_rsp_state_code state;
	struct cmd_11v *p = NULL;
	u16 rlen = sizeof(state);
	u32 value = 0;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_11V);
	if (!msg)
		return -ENOMEM;
	p = (struct cmd_11v *)msg->data;

	p->cmd = SUBCMD_ENABLE;
	/* 24-31 feature 16-23 status 0-15 interval */
	value = SPRD_11V_SLEEP << 8;
	value = (value | status) << 16;
	value = value | interval;
	p->value = value;
	/* len =  cmd(2) + len(2) +value(4) = 8 */
	p->len = 8;

	return send_cmd_recv_rsp(priv, msg, (u8 *)&state, &rlen);
}

int sc2355_set_max_clients_allowed(struct sprd_priv *priv,
				   struct sprd_vif *vif, int n_clients)
{
	int *max;
	struct sprd_msg *msg;

	msg = get_cmdbuf(priv, vif, sizeof(*max), CMD_SET_MAX_CLIENTS_ALLOWED);
	if (!msg)
		return -ENOMEM;
	*(int *)msg->data = n_clients;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sprd_send_data2cmd(struct sprd_priv *priv, struct sprd_vif *vif, void *data,
		       u16 len)
{
	struct sprd_msg *msg = NULL;

	msg = get_cmdbuf(priv, vif, len, CMD_TX_DATA);
	if (!msg)
		return -ENOMEM;
	memcpy(msg->data, data, len);
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sprd_xmit_data2cmd_wq(struct sk_buff *skb, struct net_device *ndev)
{
#define FLAG_SIZE 5
	struct sprd_vif *vif = netdev_priv(ndev);
	u8 *temp_flag = "01234";
	struct tx_msdu_dscr *dscr;
	struct sprd_work *misc_work = NULL;
	struct sprd_hif *hif = &vif->priv->hif;

	/*fill dscr header first*/
	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		if (sc2355_pcie_hif_fill_msdu_dscr(vif, skb, SPRD_TYPE_CMD, 0)) {
			dev_kfree_skb(skb);
			return -EPERM;
		}
	} else {
		if (sc2355_hif_fill_msdu_dscr(vif, skb, SPRD_TYPE_CMD, 0)) {
			dev_kfree_skb(skb);
			return -EPERM;
		}
	}
	/*
	 * MU300 (kanoqwq's clean-tf-7.2 149c9d2a): the descriptor is where the fill put it - behind the 5-byte address
	 * prefix on PCIe - and is found before SDIO's flag goes in front. The vendor read it at skb->data, i.e. the
	 * prefix on PCIe, so "sta_lut_index" was the descriptor's offset byte (11) and group frames never got the BK
	 * TID that this is for.
	 */
	dscr = (struct tx_msdu_dscr *)(skb->data +
		(hif->hw_type == SPRD_HW_SC2355_PCIE ? MSDU_DSCR_RSVD : 0));
	/*alloc five byte for fw 16 byte need
	 *dscr:11+flag:5 =16
	 */
	if (hif->hw_type != SPRD_HW_SC2355_PCIE) {
		skb_push(skb,FLAG_SIZE);
		memcpy(skb->data,temp_flag,FLAG_SIZE);
	}
	/*send group in BK to avoid FW hang*/
	if ((vif->mode == SPRD_MODE_AP ||
	     vif->mode == SPRD_MODE_P2P_GO) && dscr->sta_lut_index < 6) {
		dscr->buffer_info.msdu_tid = prio_1;
		pr_debug_ratelimited("%s, %d, SOFTAP/GO group go as BK\n", __func__, __LINE__);
	}

	/*create work queue*/
	misc_work = sprd_alloc_work(skb->len);
	if (!misc_work) {
		pr_err("%s:work queue alloc failure\n", __func__);
		dev_kfree_skb(skb);
		return -1;
	}
	memcpy(misc_work->data, skb->data, skb->len);
	dev_kfree_skb(skb);
	misc_work->vif = vif;
	misc_work->id = SPRD_CMD_TX_DATA;
	sprd_queue_work(vif->priv, misc_work);

	return 0;
}

int sc2355_set_random_mac(struct sprd_priv *priv, struct sprd_vif *vif,
			  u8 random_mac_flag, u8 *addr)
{
	struct sprd_msg *msg;
	u8 *p;

	msg = get_cmdbuf(priv, vif, ETH_ALEN + 1, CMD_RND_MAC);
	if (!msg)
		return -ENOMEM;
	p = (u8 *)msg->data;
	*p = random_mac_flag;

	memcpy(p + 1, addr, ETH_ALEN);
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

static int cmdevt_set_tlv_data(struct sprd_priv *priv, struct sprd_vif *vif,
			       struct tlv_data *tlv, int length)
{
	struct sprd_msg *msg;

	if (!priv || !tlv)
		return -EINVAL;

	msg = get_cmdbuf(priv, vif, length, CMD_SET_TLV);
	if (!msg)
		return -ENOMEM;

	memcpy(msg->data, tlv, length);

	pr_info("%s tlv type = %d\n", __func__, tlv->type);

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sc2355_set_vowifi(struct net_device *ndev, void __user *data)
#else
int sc2355_set_vowifi(struct net_device *ndev, struct ifreq *ifr)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;
	struct android_wifi_priv_cmd priv_cmd;
	struct tlv_data *tlv;
	int ret;

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	if (!data)
		return -EINVAL;

	if (copy_from_user(&priv_cmd, data, sizeof(priv_cmd)))
		return -EFAULT;
#else
	if (!ifr->ifr_data)
		return -EINVAL;

	if (copy_from_user(&priv_cmd, ifr->ifr_data, sizeof(priv_cmd)))
		return -EFAULT;
#endif

	/*bug1743709, add length check to avoid invalid NULL ptr*/
	if ((priv_cmd.total_len < sizeof(*tlv)) ||
	    (priv_cmd.total_len > SPRD_MAX_CMD_TXLEN)) {
		netdev_info(ndev, "%s: priv cmd total len is invalid: %d\n",
			    __func__, priv_cmd.total_len);
		return -EINVAL;
	}

	tlv = kmalloc(priv_cmd.total_len, GFP_KERNEL);
	if (!tlv)
		return -ENOMEM;

	if (copy_from_user(tlv, priv_cmd.buf, priv_cmd.total_len)) {
		ret = -EFAULT;
		goto out;
	}
	/*vowifi case, should send delba*/
	if (tlv->type == IOCTL_TLV_TP_VOWIFI_INFO &&
	    vif->sm_state == SPRD_CONNECTED &&
	    (is_valid_ether_addr(vif->bssid))) {
		struct sprd_hif *hif = NULL;
		struct sprd_peer_entry *peer_entry = NULL;
		struct vowifi_info *info = (struct vowifi_info *)(tlv->data);
		hif = &vif->priv->hif;
		if (hif == NULL)
			return -EINVAL;

		if (hif->hw_type == SPRD_HW_SC2355_PCIE)
			peer_entry = sc2355_pcie_find_peer_entry_using_addr(vif, vif->bssid);
		else
			peer_entry = sc2355_find_peer_entry_using_addr(vif, vif->bssid);
		if (hif && peer_entry) {
			pr_info("lut:%d, vowifi_enabled, txba_map:%lu\n",
				peer_entry->lut_index,
				peer_entry->ba_tx_done_map);

			if (tlv->len && !info->data) {
				peer_entry->vowifi_enabled = 0;
			} else {
				u16 tid = qos_index_2_tid(SPRD_AC_VO);

				peer_entry->vowifi_enabled = 1;
				peer_entry->vowifi_pkt_cnt = 0;
				if (test_bit(tid, &peer_entry->ba_tx_done_map))
					sc2355_tx_delba(hif, peer_entry,
							SPRD_AC_VO);
			}
		}
	}

	ret = cmdevt_set_tlv_data(priv, vif, tlv, priv_cmd.total_len);
	if (ret)
		netdev_err(ndev, "%s set tlv(type=%#x) error\n",
			   __func__, tlv->type);
out:
	kfree(tlv);
	return ret;
}

static int cmdevt_set_sniffer(struct sprd_priv *priv, struct sprd_vif *vif,
			      u8 type, u8 value)
{
	struct sprd_msg *msg;
	struct cmd_sniffer_para *p;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_SET_SNIFFER);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_sniffer_para *)msg->data;
	p->type = type;
	p->value = value;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

int sc2355_set_sniffer(struct net_device *ndev, struct ifreq *ifr)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;
	struct android_wifi_priv_cmd priv_cmd;
	char *command = NULL;
	int ret = 0, skip, value;
	unsigned int channel = 0;
	u16 chns_5g[64] = {0x00};

	if (!ifr->ifr_data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, ifr->ifr_data, sizeof(priv_cmd)))
		return -EFAULT;

	/* add length check to avoid invalid NULL ptr */
	if (priv_cmd.total_len <= 0) {
		netdev_err(ndev, "%s: priv cmd total len is invalid\n",
			   __func__);
		return -EINVAL;
	}

	command = kmalloc(priv_cmd.total_len, GFP_KERNEL);
	if (!command)
		return -ENOMEM;
	if (copy_from_user(command, priv_cmd.buf, priv_cmd.total_len)) {
		ret = -EFAULT;
		goto out;
	}

	if (!strncasecmp(command, CMD_SNIFFER_MODE,
			 strlen(CMD_SNIFFER_MODE))) {
		skip = strlen(CMD_SNIFFER_MODE) + 1;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: set sniffer monitor mode, value: %d\n",
			    __func__, value);
		if (value == 1) {
			if (atomic_read(&priv->monitor_mode) == 1) {
				netdev_err(ndev, "already in sniffer monitor mode\n");
				goto out;
			}
			ret = cmdevt_set_sniffer(priv, vif, SPRD_SNIFFER_ENABLE, value);
			if (!ret) {
				netdev_err(ndev, "set sniffer monitor success\n");
				atomic_set(&priv->monitor_mode, 1);
				priv->monitor_data_cnt = 0;
				priv->monitor_mgmt_cnt = 0;
			}
		} else {
			if (atomic_read(&priv->monitor_mode) == 0) {
				netdev_err(ndev, "not in sniffer monitor mode, just return\n");
				goto out;
			}
			ret = cmdevt_set_sniffer(priv, vif, SPRD_SNIFFER_ENABLE, value);
			if (!ret) {
				atomic_set(&priv->monitor_mode, 0);
				netdev_err(ndev, "exit sniffer monitor success\n");
			}
		}
	} else if (!strncasecmp(command, CMD_SNIFFER_LISTEN_CHANNEL,
				strlen(CMD_SNIFFER_LISTEN_CHANNEL))) {
		skip = strlen(CMD_SNIFFER_LISTEN_CHANNEL) + 1;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: set sniffer monitor listen channel, value: %d\n",
			    __func__, value);
		if (!atomic_read(&priv->monitor_mode))
			netdev_err(ndev, "%s: set listen channel not in monitor mode\n",
				   __func__);
		/* use scan command to set channel */
		if (value <= 14) {
			pr_info("2.4G channel: %d\n", value);
			channel |= (1 << (value - 1));
		} else {
			pr_info("set 5G channel\n");
			chns_5g[0] = value;
		}
		ret = sc2355_cmd_scan(vif->priv, vif, channel, 0, NULL, 1, chns_5g);
		if (ret) {
			netdev_err(ndev, "sniffer set channel failed\n");
			goto out;
		}
	} else if (!strncasecmp(command, CMD_SNIFFER_FILTER,
				strlen(CMD_SNIFFER_FILTER))) {
		skip = strlen(CMD_SNIFFER_FILTER) + 1;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: set sniffer monitor filter, value: %d\n",
			    __func__, value);
		if (!atomic_read(&priv->monitor_mode))
			netdev_err(ndev, "%s: set sniffer monitor filter not in monitor mode\n",
				   __func__);
		ret = cmdevt_set_sniffer(priv, vif, SPRD_SNIFFER_FILTER, value);
		if (ret) {
			netdev_err(ndev, "sniffer set filter failed\n");
			goto out;
		}
	} else if (!strncasecmp(command, CMD_SNIFFER_BAND,
				strlen(CMD_SNIFFER_BAND))) {
		skip = strlen(CMD_SNIFFER_BAND) + 1;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: set sniffer monitor band, value: %d\n",
			    __func__, value);
		if (!atomic_read(&priv->monitor_mode))
			netdev_err(ndev, "%s: set sniffer monitor band not in monitor mode\n",
				   __func__);
		ret = cmdevt_set_sniffer(priv, vif, SPRD_SNIFFER_BAND, value);
		if (ret) {
			netdev_err(ndev, "sniffer set band failed\n");
			goto out;
		}
	} else {
		netdev_err(ndev, "%s command not support\n", __func__);
		ret = -EOPNOTSUPP;
	}

out:
	kfree(command);
	return ret;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
int sc2355_set_miracast(struct net_device *ndev, void __user *data)
#else
int sc2355_set_miracast(struct net_device *ndev, struct ifreq *ifr)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;
	struct android_wifi_priv_cmd priv_cmd;
	char *command = NULL;
	unsigned short subtype;
	int ret = 0, value;
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	if (data == NULL)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, data, sizeof(priv_cmd)))
		return -EINVAL;

#else
	if (ifr->ifr_data == NULL)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, ifr->ifr_data, sizeof(priv_cmd)))
		return -EINVAL;
#endif

	/*add length check to avoid invalid NULL ptr*/
	if (priv_cmd.total_len <= 0) {
		pr_err("%s: priv cmd total len is invalid", __func__);
		return -EINVAL;
	}

	command = kmalloc(priv_cmd.total_len, GFP_KERNEL);
	if (command == NULL)
		return -EINVAL;
	if (copy_from_user(command, priv_cmd.buf, priv_cmd.total_len)) {
		ret = -EFAULT;
		goto out;
	}

	subtype = *(unsigned short *)command;
	if (subtype == 5) {
		value = *((int *)(command + 2 * sizeof(unsigned short)));
		pr_info("%s: set miracast value : %d", __func__, value);
		ret = sc2355_enable_miracast(priv, vif, value);
	}
out:
	kfree(command);
	return ret;
}

int sc2355_set_rekey_data(struct sprd_priv *priv, struct sprd_vif *vif,
			  struct cfg80211_gtk_rekey_data *data)
{
	struct sprd_msg *msg;
	struct cmd_set_rekey *p;
	u8 *sub_cmd;

	msg = get_cmdbuf(priv, vif, sizeof(*p) + sizeof(*sub_cmd), CMD_KEY);
	if (!msg)
		return -ENOMEM;
	sub_cmd = (u8 *)msg->data;
	*sub_cmd = SUBCMD_REKEY;
	p = (struct cmd_set_rekey *)(++sub_cmd);
	memcpy(p->kek, data->kek, NL80211_KEK_LEN);
	memcpy(p->kck, data->kck, NL80211_KCK_LEN);
	memcpy(p->replay_ctr, data->replay_ctr, NL80211_REPLAY_CTR_LEN);
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

static int cmdevt_send_ba_mgmt(struct sprd_priv *priv, struct sprd_vif *vif,
			       void *data, u16 len)
{
	struct sprd_msg *msg = NULL;

	msg = get_cmdbuf(priv, vif, sizeof(struct cmd_ba), CMD_BA);
	if (!msg)
		return -ENOMEM;

	memcpy(msg->data, data, len);
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

/* CP2 send EVT_HANG_RECOVERY to Driver,
 * then Driver need to send a CMD_HANG_RECEIVED cmd to CP2
 * to notify that CP2 can reset credit now.
 */
static int cmdevt_send_hang_received_cmd(struct sprd_priv *priv, struct sprd_vif *vif)
{
	struct sprd_msg *msg;
	struct sprd_hif *hif = &priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	msg = get_cmdbuf(priv, vif, 0, CMD_HANG_RECEIVED);
	if (!msg)
		return -ENOMEM;
	tx_mgmt->hang_recovery_status = HANG_RECOVERY_ACKED;
	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

static int cmdevt_fw_power_down_ack(struct sprd_priv *priv, struct sprd_vif *vif)
{
	struct sprd_msg *msg;
	struct cmd_power_save *p;
	int ret = 0;
	struct sprd_hif *hif = &priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	enum sprd_mode mode = SPRD_MODE_NONE;
	int tx_num = 0;

	msg = get_cmdbuf(priv, vif, sizeof(*p), CMD_POWER_SAVE);
	if (!msg)
		return -ENOMEM;

	p = (struct cmd_power_save *)msg->data;
	p->sub_type = SPRD_FW_PWR_DOWN_ACK;

	for (mode = SPRD_MODE_NONE; mode < SPRD_MODE_MAX; mode++) {
		int num = atomic_read(&tx_mgmt->tx_list[mode]->mode_list_num);

		tx_num += num;
	}

	if (tx_num > 0 ||
	    !list_empty(&tx_mgmt->xmit_msg_list.to_send_list) ||
	    !list_empty(&tx_mgmt->xmit_msg_list.to_free_list)) {
		if (hif->fw_power_down == 1)
			goto err;
		p->value = 0;
		hif->fw_power_down = 0;
		hif->fw_awake = 1;
	} else {
		p->value = 1;
		hif->fw_power_down = 1;
		hif->fw_awake = 0;
	}
	pr_info("%s, value=%d, fw_pwr_down=%d, fw_awake=%d, %d, %d, %d, %d\n",
		__func__,
		p->value,
		hif->fw_power_down,
		hif->fw_awake,
		atomic_read(&tx_mgmt->tx_list_qos_pool.ref),
		tx_num,
		list_empty(&tx_mgmt->xmit_msg_list.to_send_list),
		list_empty(&tx_mgmt->xmit_msg_list.to_free_list));

	ret = send_cmd_recv_rsp(priv, msg, NULL, NULL);

	if (ret)
		pr_err("host send data cmd failed, ret=%d\n", ret);

	return ret;
err:
	pr_err("%s donot ack FW_PWR_DOWN twice\n", __func__);
	sprd_chip_free_msg(&priv->chip, msg);
	return -1;
}

static int cmdevt_send_vowifi_data_prot(struct sprd_priv *priv, struct sprd_vif *vif,
					void *data, int len)
{
	struct sprd_msg *msg;

	pr_info("enter--at %s\n", __func__);

	if (!priv)
		return -EINVAL;

	msg = get_cmdbuf(priv, vif, 0, CMD_VOWIFI_DATA_PROTECT);
	if (!msg)
		return -ENOMEM;

	return send_cmd_recv_rsp(priv, msg, NULL, NULL);
}

bool sc2355_do_delay_work(struct sprd_work *work)
{
	struct sprd_tdls_work *tdls;
	unsigned char *data = NULL;
	u8 mac_addr[ETH_ALEN];
	u16 reason_code;
	struct sprd_vif *vif;
	enum sprd_hif_type hw_type;
	struct sprd_hif *hif = NULL;

	if (!work)
		return false;

	vif = work->vif;
	hw_type = work->hw_type;

	switch (work->id) {
	case SPRD_WORK_BA_MGMT:
		cmdevt_send_ba_mgmt(vif->priv, vif, work->data, work->len);
		break;
	case SPRD_WORK_ADDBA:
		if (hw_type == SPRD_HW_SC2355_PCIE)
			sc2355_pcie_tx_send_addba(vif, work->data, work->len);
		else
			sc2355_tx_send_addba(vif, work->data, work->len);
		break;
	case SPRD_WORK_DELBA:
		if (hw_type == SPRD_HW_SC2355_PCIE)
			sc2355_pcie_tx_send_delba(vif, work->data, work->len);
		else
			sc2355_tx_send_delba(vif, work->data, work->len);
		break;
	case SPRD_HANG_RECEIVED:
		cmdevt_send_hang_received_cmd(vif->priv, vif);
		break;
	case SPRD_POP_MBUF:
		if (hw_type == SPRD_HW_SC2355_PCIE)
			sc2355_pcie_handle_pop_list(work->data);
		else
			sc2355_handle_pop_list(work->data);
		break;
	case SPRD_TDLS_CMD:
		tdls = (struct sprd_tdls_work *)work->data;
		sprd_tdls_oper(vif->priv, vif, tdls->peer, tdls->oper);
		break;
	case SPRD_SEND_CLOSE:
		hif = &vif->priv->hif;
		if (!hif) {
			pr_err("%s can not get hif!\n", __func__);
			return false;
		}
		vif->state &= ~VIF_STATE_OPEN;
		sprd_hif_tx_flush(hif, vif);
		sprd_close_fw(vif->priv, vif);
		break;
	case SPRD_PCIE_RX_ALLOC_BUF:
		sc2355_mm_fill_buffer(&vif->priv->hif);
		break;
	case SPRD_PCIE_RX_FLUSH_BUF:
		sc2355_rx_flush_buffer(&vif->priv->hif);
		break;
	case SPRD_PCIE_TX_MOVE_BUF:
		if (hw_type == SPRD_HW_SC2355_PCIE)
			sc2355_pcie_add_to_free_list(vif->priv,
					(struct list_head *)work->data,
					work->len);
		else
			sc2355_add_to_free_list(vif->priv,
					(struct list_head *)work->data,
					work->len);
		break;
	case SPRD_PCIE_TX_FREE_BUF:
		memcpy((unsigned char *)&data, work->data,
		       sizeof(unsigned char *));
		sc2355_tx_free_pcie_data(data);
		sc2355_free_data(data, work->len);
		break;
	case SPRD_CMD_TX_DATA:
		sprd_send_data2cmd(vif->priv, vif, work->data, work->len);
		break;
	case SPRD_WORK_FW_PWR_DOWN:
		cmdevt_fw_power_down_ack(vif->priv, vif);
		break;
	case SPRD_WORK_HOST_WAKEUP_FW:
		sc2355_cmd_host_wakeup_fw(vif->priv, vif);
		break;
	case SPRD_WORK_VOWIFI_DATA_PROTECTION:
		cmdevt_send_vowifi_data_prot(vif->priv, vif, work->data, work->len);
		break;
	case SPRD_P2P_GO_DEL_STATION:
		memcpy(mac_addr, (u8 *)work->data, ETH_ALEN);
		memcpy(&reason_code, (u16 *)(work->data + ETH_ALEN), sizeof(u16));
		sc2355_del_station(vif->priv, vif, mac_addr, reason_code);
		break;
	case SPRD_WORK_FRESH_BO:
		sc2355_fcc_fresh_bo_work(vif->priv, work->data, work->len);
		break;
	case SPRD_WORK_ADAPTIVE:
		sprd_wifi_adaptive_work(vif->priv, vif);
	default:
		return false;
	}

	return true;
}

static int cmdevt_handle_rsp_status_err(u8 cmd_id, s8 status)
{
	int flag = 0;

	switch (cmd_id) {
	case CMD_DOWNLOAD_INI:
		if (status == SPRD_CMD_STATUS_CRC_ERROR ||
		    status == SPRD_CMD_STATUS_INI_INDEX_ERROR ||
		    status == SPRD_CMD_STATUS_LENGTH_ERROR)
			flag = -1;
		break;
	default:
		flag = 0;
		break;
	}

	return flag;
}

/* Events */
static void cmdevt_report_connect_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_connect_info conn_info = { 0 };
	u8 status_code;
	u8 *pos = data;
	unsigned int left = len;
	u8 compat_ver = 0;
	struct sprd_priv *priv = vif->priv;

	compat_ver =
	    sc2355_api_version_need_compat_operation(priv, EVT_CONNECT);
	if (compat_ver) {
		switch (compat_ver) {
		case VERSION_1:
			/*add data struct modification in here!*/
			break;
		case VERSION_2:
			/*add data struct modification in here!*/
			break;
		case VERSION_3:
			/*add data struct modification in here!*/
			break;
		default:
			break;
		}
	}

	/* the first byte is status code */
	memcpy(&status_code, pos, sizeof(status_code));
	if (status_code != SPRD_CONNECT_SUCCESS &&
	    status_code != SPRD_ROAM_SUCCESS) {
		/*Assoc response status code by set in the 3 byte if failure*/
		memcpy(&status_code, pos + 2, sizeof(status_code));
		goto out;
	}
	pos += sizeof(status_code);
	left -= sizeof(status_code);

	/* parse BSSID */
	if (left < ETH_ALEN)
		goto out;
	conn_info.bssid = pos;
	pos += ETH_ALEN;
	left -= ETH_ALEN;

	/* get channel */
	if (left < sizeof(conn_info.chan))
		goto out;
	memcpy(&conn_info.chan, pos, sizeof(conn_info.chan));
	pos += sizeof(conn_info.chan);
	left -= sizeof(conn_info.chan);

	/* get signal */
	if (left < sizeof(conn_info.signal))
		goto out;
	memcpy(&conn_info.signal, pos, sizeof(conn_info.signal));
	pos += sizeof(conn_info.signal);
	left -= sizeof(conn_info.signal);

	/* parse REQ IE */
	if (!left)
		goto out;
	memcpy(&conn_info.req_ie_len, pos, sizeof(conn_info.req_ie_len));
	pos += sizeof(conn_info.req_ie_len);
	left -= sizeof(conn_info.req_ie_len);
	conn_info.req_ie = pos;
	pos += conn_info.req_ie_len;
	left -= conn_info.req_ie_len;

	/* parse RESP IE */
	if (!left)
		goto out;
	memcpy(&conn_info.resp_ie_len, pos, sizeof(conn_info.resp_ie_len));
	pos += sizeof(conn_info.resp_ie_len);
	left -= sizeof(conn_info.resp_ie_len);
	conn_info.resp_ie = pos;
	pos += conn_info.resp_ie_len;
	left -= conn_info.resp_ie_len;

	/* parse BEA IE */
	if (!left)
		goto out;
	memcpy(&conn_info.bea_ie_len, pos, sizeof(conn_info.bea_ie_len));
	pos += sizeof(conn_info.bea_ie_len);
	left -= sizeof(conn_info.bea_ie_len);
	conn_info.bea_ie = pos;
out:
	sprd_report_connection(vif, &conn_info, status_code);
}

static void cmdevt_report_disconnect_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	u16 reason_code;

	memcpy(&reason_code, data, sizeof(reason_code));
	sprd_report_disconnection(vif, reason_code);
}

static void cmdevt_report_remain_on_channel_evt(struct sprd_vif *vif, u8 *data,
						u16 len)
{
	sprd_report_remain_on_channel_expired(vif);
}

static void cmdevt_report_new_station_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct evt_new_station *sta = (struct evt_new_station *)data;
	u16 ie_len;

	/* MU300 (from PR #237, oswaldxdd): the event's own length bounds what is read. A station that left needs
	 * only the flag and the address (its ie_len is not used); a new one hands cfg80211 at most the bytes the
	 * event carries, never what ie_len claims beyond them.
	 */
	if (len < sizeof(sta->is_connect) + sizeof(sta->mac))
		return;
	if (!sta->is_connect) {
		sprd_report_softap(vif, 0, sta->mac, NULL, 0);
		return;
	}
	if (len < sizeof(*sta))
		return;
	ie_len = le16_to_cpu(sta->ie_len);
	if (ie_len > len - sizeof(*sta))
		return;
	sprd_report_softap(vif, sta->is_connect, sta->mac, sta->ie, ie_len);
}

/* @flag: 1 for data, 0 for event */
static void cmdevt_report_frame_evt(struct sprd_vif *vif, u8 *data, u16 len, int flag)
{
	struct evt_mgmt_frame *frame;
	u16 buf_len;
	u8 *buf = NULL;
	u8 channel, type;

	if (flag) {
		/* here frame maybe not 4 bytes align */
		frame = (struct evt_mgmt_frame *)
		    (data - sizeof(*frame) + len);
		buf = data - sizeof(*frame);
	} else {
		frame = (struct evt_mgmt_frame *)data;
		buf = frame->data;
	}
	channel = frame->channel;
	type = frame->type;
	buf_len = SPRD_GET_LE16(frame->len);

	if (atomic_read(&vif->priv->monitor_mode)) {
		pr_info("%s: enter rx monitor process\n", __func__);
		sprd_rx_monitor_process(vif, buf, buf_len);
		return;
	}

	sprd_dump_frame_prot_info(0, 0, buf, buf_len);

	switch (type) {
	case SPRD_FRAME_NORMAL:
		sprd_report_mgmt(vif, channel, buf, buf_len);
		break;
	case SPRD_FRAME_DEAUTH:
		sprd_report_mgmt_deauth(vif, buf, buf_len);
		break;
	case SPRD_FRAME_DISASSOC:
		sprd_report_mgmt_disassoc(vif, buf, buf_len);
		break;
	case SPRD_FRAME_SCAN:
		sc2355_report_scan_result(vif, channel, frame->signal,
					  buf, buf_len);
		++bss_count;
		break;
	default:
		netdev_err(vif->ndev, "%s invalid frame type: %d!\n",
			   __func__, type);
		break;
	}
}

static void cmdevt_report_scan_done_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct evt_scan_done *p = (struct evt_scan_done *)data;
#ifdef CONFIG_SPRD_WLAN_VENDOR_SPECIFIC
	u8 bucket_id = 0;
#endif /* CONFIG_SPRD_WLAN_VENDOR_SPECIFIC */

	switch (p->type) {
	case SPRD_SCAN_DONE:
		sc2355_clean_scan(vif);
		sprd_report_scan_done(vif, false);
		netdev_info(vif->ndev, "%s got %d BSSes\n", __func__,
			    bss_count);
		break;
	case SPRD_SCHED_SCAN_DONE:
		sprd_report_sched_scan_done(vif, false);
		netdev_info(vif->ndev, "%s schedule scan got %d BSSes\n",
			    __func__, bss_count);
		break;
#ifdef CONFIG_SPRD_WLAN_VENDOR_SPECIFIC
	case SPRD_GSCAN_DONE:
		bucket_id = ((struct evt_gscan_done *)data)->bucket_id;
		sc2355_gscan_done(vif, bucket_id);
		netdev_info(vif->ndev, "%s gscan got %d bucketid done\n",
			    __func__, bucket_id);
		break;
#endif /* CONFIG_SPRD_WLAN_VENDOR_SPECIFIC */
	case SPRD_SCAN_ABORT_DONE:
		sc2355_clean_scan(vif);
		sprd_report_scan_done(vif, true);
		netdev_info(vif->ndev, "%s scan abort got %d BSSes\n",
			    __func__, bss_count);
		break;
	case SPRD_SCAN_ERROR:
	default:
		sc2355_clean_scan(vif);
		sprd_report_scan_done(vif, true);
		sprd_report_sched_scan_done(vif, false);
		if (p->type == SPRD_SCAN_ERROR)
			netdev_err(vif->ndev, "%s error!\n", __func__);
		else
			netdev_err(vif->ndev, "%s invalid scan done type: %d\n",
				   __func__, p->type);
		break;
	}
	bss_count = 0;
}

static void cmdevt_report_mic_failure_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct evt_mic_failure *mic_failure = (struct evt_mic_failure *)data;

	sprd_report_mic_failure(vif, mic_failure->is_mcast,
				mic_failure->key_id);
}

static void cmdevt_report_cqm_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct evt_cqm *p;
	u8 rssi_event;

	p = (struct evt_cqm *)data;
	switch (p->status) {
	case SPRD_CQM_RSSI_LOW:
		rssi_event = NL80211_CQM_RSSI_THRESHOLD_EVENT_LOW;
		break;
	case SPRD_CQM_RSSI_HIGH:
		rssi_event = NL80211_CQM_RSSI_THRESHOLD_EVENT_HIGH;
		break;
	case SPRD_CQM_BEACON_LOSS:
		/* TODO wpa_supplicant not support the event ,
		 * so we workaround this issue
		 */
		rssi_event = NL80211_CQM_RSSI_THRESHOLD_EVENT_LOW;
		vif->beacon_loss = 1;
		break;
	default:
		netdev_err(vif->ndev, "%s invalid event!\n", __func__);
		return;
	}

	sprd_report_cqm(vif, rssi_event);
}

static void cmdevt_report_mlme_tx_status_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct evt_mgmt_tx_status *tx_status =
	    (struct evt_mgmt_tx_status *)data;

	sprd_report_mgmt_tx_status(vif, SPRD_GET_LE64(tx_status->cookie),
				   tx_status->buf,
				   SPRD_GET_LE16(tx_status->len),
				   tx_status->ack);
}

static void cmdevt_report_tdls_flow_count(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_hif *hif = &vif->priv->hif;
	u8 i;
	u8 found = 0;
	struct tdls_update_peer_infor *peer_info =
	    (struct tdls_update_peer_infor *)data;
	ktime_t kt;

	if (len < sizeof(struct tdls_update_peer_infor)) {
		pr_err("%s, event data len not in range\n", __func__);
		return;
	}
	for (i = 0; i < MAX_TDLS_PEER; i++) {
		if (ether_addr_equal(hif->tdls_flow_count[i].da,
				     peer_info->da)) {
			found = 1;
			break;
		}
	}
	/* 0 to delete entry */
	if (peer_info->valid == 0) {
		if (found == 0) {
			pr_err("%s, invalid da, fail to del\n", __func__);
			return;
		}
		memset(&hif->tdls_flow_count[i], 0,
		       sizeof(struct tdls_flow_count_para));

		for (i = 0; i < MAX_TDLS_PEER; i++) {
			if (hif->tdls_flow_count[i].valid == 1)
				found++;
		}
		if (found == 1)
			hif->tdls_flow_count_enable = 0;
	} else if (peer_info->valid == 1) {
		if (found == 0) {
			for (i = 0; i < MAX_TDLS_PEER; i++) {
				if (hif->tdls_flow_count[i].valid == 0) {
					found = 1;
					break;
				}
			}
		}
		if (found == 0) {
			pr_err("%s, no free TDLS entry\n", __func__);
			i = 0;
		}

		hif->tdls_flow_count_enable = 1;
		hif->tdls_flow_count[i].valid = 1;
		ether_addr_copy(hif->tdls_flow_count[i].da, peer_info->da);
		hif->tdls_flow_count[i].threshold = peer_info->txrx_len;
		hif->tdls_flow_count[i].data_len_counted = 0;

		pr_info("%s,%d, tdls_id=%d,threshold=%d, timer=%d, da=(%pM)\n",
			__func__, __LINE__, i,
			hif->tdls_flow_count[i].threshold,
			peer_info->timer, peer_info->da);

		kt = ktime_get();
		hif->tdls_flow_count[i].start_mstime =
		    (u32)div_u64(kt, NSEC_PER_MSEC);
		hif->tdls_flow_count[i].timer = peer_info->timer;
		pr_info("%s,%d, tdls_id=%d,start_time:%u\n",
			__func__, __LINE__, i,
			hif->tdls_flow_count[i].start_mstime);
	}
}

static void cmdevt_flush_tdls_flow(struct sprd_vif *vif, const u8 *peer, u8 oper)
{
	struct sprd_hif *hif = &vif->priv->hif;
	u8 i;

	if (oper == NL80211_TDLS_SETUP || oper == NL80211_TDLS_ENABLE_LINK) {
		for (i = 0; i < MAX_TDLS_PEER; i++) {
			if (ether_addr_equal(hif->tdls_flow_count[i].da,
					     peer)) {
				memset(&hif->tdls_flow_count[i], 0,
				       sizeof(struct tdls_flow_count_para));
				break;
			}
		}
	}
}

static void cmdevt_report_tdls_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	unsigned char peer[ETH_ALEN];
	u8 oper;
	u16 reason_code;
	struct evt_tdls *report_tdls = NULL;

	if (len < sizeof(struct evt_tdls)) {
		pr_err("%s event_tdls len is invalid!\n", __func__);
		return;
	}

	report_tdls = (struct evt_tdls *)data;
	ether_addr_copy(&peer[0], &report_tdls->mac[0]);
	oper = report_tdls->tdls_sub_cmd_mgmt;

	if (oper == SPRD_TDLS_TEARDOWN) {
		oper = NL80211_TDLS_TEARDOWN;
	} else if (oper == SPRD_TDLS_UPDATE_PEER_INFOR) {
		cmdevt_report_tdls_flow_count(vif, data, len);
	} else {
		oper = NL80211_TDLS_SETUP;
		cmdevt_flush_tdls_flow(vif, peer, oper);
	}

	reason_code = 0;
	sprd_report_tdls(vif, peer, oper, reason_code);
}

static void cmdevt_report_suspend_resume_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct evt_suspend_resume *suspend_resume = NULL;
	struct sprd_hif *hif = &vif->priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	suspend_resume = (struct evt_suspend_resume *)data;
	if (suspend_resume->status == 1 &&
	    hif->suspend_mode == SPRD_PS_RESUMING) {
		hif->suspend_mode = SPRD_PS_RESUMED;
		sc2355_tx_up(tx_mgmt);
		pr_info("%s, %d,resumed,wakeuptx\n", __func__, __LINE__);
	}
}

static inline void cmdevt_report_ba_mgmt_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	sc2355_wlan_ba_session_event(&vif->priv->hif, data, len);
}

static void cmdevt_add_hang_cmd(struct sprd_vif *vif)
{
	struct sprd_work *misc_work;
	struct sprd_hif *hif = &vif->priv->hif;
	struct sprd_cmd *cmd = &vif->priv->cmd;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;

	if (sprd_chip_is_exit(&vif->priv->chip) ||
	    (tx_mgmt->hang_recovery_status == HANG_RECOVERY_ACKED &&
	     cmd->cmd_id != CMD_HANG_RECEIVED)) {
		complete(&cmd->completed);
	}
	misc_work = sprd_alloc_work(0);
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_HANG_RECEIVED;

	sprd_queue_work(vif->priv, misc_work);
}

static void cmdevt_report_hang_recovery_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_hif *hif = &vif->priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	struct evt_hang_recovery *hang = NULL;

	if (len < sizeof(struct evt_hang_recovery)) {
		pr_err("%s event data len is invalid!\n", __func__);
		return;
	}
	hang = (struct evt_hang_recovery *)data;

	tx_mgmt->hang_recovery_status = hang->action;
	pr_info("%s, %d, action=%d, status=%d\n",
		__func__, __LINE__,
		hang->action, tx_mgmt->hang_recovery_status);
	if (hang->action == HANG_RECOVERY_BEGIN)
		cmdevt_add_hang_cmd(vif);
	if (hang->action == HANG_RECOVERY_END)
		sc2355_tx_up(tx_mgmt);
}

static void cmdevt_add_close_cmd(struct sprd_vif *vif, enum sprd_mode mode)
{
	struct sprd_work *misc_work;

	misc_work = sprd_alloc_work(1);
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_SEND_CLOSE;

	sprd_queue_work(vif->priv, misc_work);
}

static void cmdevt_report_thermal_warn_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_priv *priv = vif->priv;
	struct sprd_hif *hif = &priv->hif;
	struct tx_mgmt *tx_mgmt = (struct tx_mgmt *)hif->tx_mgmt;
	enum sprd_mode mode = SPRD_MODE_NONE;
	struct sprd_vif *tmp_vif;
	struct evt_thermal_warn *thermal = NULL;

	if (len < sizeof(struct evt_thermal_warn)) {
		pr_err("%s event data len is invalid!\n", __func__);
		return;
	}
	thermal = (struct evt_thermal_warn *)data;

	pr_info("%s, %d, action=%d, status=%d\n",
		__func__, __LINE__, thermal->action, tx_mgmt->thermal_status);
	if (tx_mgmt->thermal_status == THERMAL_WIFI_DOWN)
		return;
	tx_mgmt->thermal_status = thermal->action;
	switch (thermal->action) {
	case THERMAL_TX_RESUME:
		sprd_net_flowcontrl(priv, SPRD_MODE_NONE, true);
		sc2355_tx_up(tx_mgmt);
		break;
	case THERMAL_TX_STOP:
		pr_err("%s, %d, netif_stop_queue because of thermal warn\n",
		       __func__, __LINE__);
		sprd_net_flowcontrl(priv, SPRD_MODE_NONE, false);
		break;
	case THERMAL_WIFI_DOWN:
		pr_err("%s, %d, close wifi because of thermal warn\n",
		       __func__, __LINE__);
		sprd_net_flowcontrl(priv, SPRD_MODE_NONE, false);

		spin_lock_bh(&priv->list_lock);
		list_for_each_entry(tmp_vif, &priv->vif_list, vif_node) {
			if (tmp_vif->state & VIF_STATE_OPEN)
				cmdevt_add_close_cmd(vif, mode);
		}
		spin_unlock_bh(&priv->list_lock);

		break;
	default:
		break;
	}
}

extern int wfd_notifier_call_chain(unsigned long val, void *v);

static void cmdevt_report_wfd_mib_cnt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct evt_wfd_mib_cnt *wfd = (struct evt_wfd_mib_cnt *)data;
	u32 tx_cnt, busy_cnt, wfd_rate;

	pr_info("%s, %d, frame=%d, clear=%d, mib=%d\n",
		__func__, __LINE__,
		wfd->tx_frame_cnt, wfd->rx_clear_cnt, wfd->mib_cycle_cnt);
	if (!wfd->mib_cycle_cnt)
		return;

	tx_cnt = wfd->tx_frame_cnt / wfd->mib_cycle_cnt;
	busy_cnt = (10 * wfd->rx_clear_cnt) / wfd->mib_cycle_cnt;

	if (busy_cnt > 8)
		wfd_rate = wfd->tx_stats.tx_tp_in_mbps;
	else
		wfd_rate =
		    wfd->tx_stats.tx_tp_in_mbps +
		    wfd->tx_stats.tx_tp_in_mbps * (1 / tx_cnt) *
		    ((10 - busy_cnt) / 10) / 2;
	pr_info("%s, %d, wfd_rate=%d\n", __func__, __LINE__, wfd_rate);
	wfd_rate = 2;
}

static void cmdevt_report_fw_power_down_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_work *misc_work;

	misc_work = sprd_alloc_work(0);
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_FW_PWR_DOWN;

	sprd_queue_work(vif->priv, misc_work);
}

static void cmdevt_report_chan_changed_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	u8 channel;
	u16 freq;
	struct wiphy *wiphy = vif->wdev.wiphy;
	struct ieee80211_channel *ch = NULL;
	struct cfg80211_chan_def chandef;
	struct chan_changed_info *p = NULL;

	if (len < sizeof(struct chan_changed_info)) {
		pr_err("%s, event data len is invalid!\n", __func__);
		return;
	}
	p = (struct chan_changed_info *)data;

	if (p->initiator == 0) {
		pr_err("%s, unknowed event!\n", __func__);
	} else if (p->initiator == 1) {
		channel = p->target_channel;

		if (channel > 14)
			freq = 5000 + channel * 5;
		else
			freq = 2412 + (channel - 1) * 5;

		if (wiphy)
			ch = ieee80211_get_channel(wiphy, freq);
		else
			pr_err("%s, wiphy is null!\n", __func__);

		if (ch) {
			/* we will be active on the channel */
			cfg80211_chandef_create(&chandef, ch,
						NL80211_CHAN_HT20);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
			cfg80211_ch_switch_notify(vif->ndev, &chandef, 0);
#else
			cfg80211_ch_switch_notify(vif->ndev, &chandef);
#endif
		} else {
			pr_err("%s, ch is null!\n", __func__);
		}
	}
}

static void cmdevt_report_coex_bt_on_off_evt(u8 *data, u16 len, enum sprd_hif_type hw_type)
{
	struct evt_coex_mode_changed *coex_bt_on_off =
	    (struct evt_coex_mode_changed *)data;

	pr_info("%s, %d, action=%d\n",
		__func__, __LINE__, coex_bt_on_off->action);
	if (hw_type == SPRD_HW_SC2355_PCIE)
		sc2355_pcie_set_coex_bt_on_off(coex_bt_on_off->action);
	else
		sc2355_set_coex_bt_on_off(coex_bt_on_off->action);
}

static int cmdevt_report_acs_done_evt(struct sprd_vif *vif, u8 *data, u16 len)
{
	u16 res_cnt = 0;
	u16 band;
	struct ieee80211_channel *chan;
	unsigned int freq;
	struct acs_result *acs_res;
	struct survey_info_new_node *info;
	u8 i = 0;
	struct wiphy *wiphy = vif->wdev.wiphy;

	/* save acs result to survey list */
	res_cnt = len / sizeof(struct acs_result);

	pr_info("%s, tot len %d, acs len %d", __func__, len,
		(int)sizeof(struct acs_result));

	sc2355_hex_dump("sc2355_evt_acs_done", data, len);

	acs_res = (struct acs_result *)data;

	for (i = 0; i < res_cnt; i++) {
		info = kmalloc(sizeof(*info), GFP_KERNEL);
		if (!info)
			return -ENOMEM;

		band = sprd_channel_to_band(acs_res[i].ch);
		freq = ieee80211_channel_to_frequency(acs_res[i].ch, band);
		chan = ieee80211_get_channel(wiphy, freq);
		if (chan) {
			info->channel = chan;
			info->cca_busy_time = acs_res[i].time_busy;
			info->busy_ext_time = acs_res[i].time_ext_busy;
			info->time = acs_res[i].time;
			info->noise = acs_res[i].noise;
			list_add_tail(&info->survey_list,
				      &vif->survey_info_list);
		} else {
			kfree(info);
			netdev_info(vif->ndev, "%s channel is 0\n", __func__);
		}
	}

	return 0;
}

static int sc2355_evt_pw_backoff(struct sprd_vif *vif, u8 *data, u16 len)
{
	struct sprd_work *misc_work;

	if (!len) {
		netdev_err(vif->ndev, "%s event data len=0\n", __func__);
		return -EINVAL;
	}

	misc_work = sprd_alloc_work(len);
	if (!misc_work) {
		pr_err("%s out of memory\n", __func__);
		return -1;
	}
	misc_work->vif = vif;
	misc_work->id = SPRD_WORK_FRESH_BO;
	memcpy(misc_work->data, data, len);

	sprd_queue_work(vif->priv, misc_work);

	return 0;
}

unsigned short sc2355_rx_evt_process(struct sprd_priv *priv, u8 *msg)
{
	struct sprd_cmd_hdr *hdr = (struct sprd_cmd_hdr *)msg;
	struct sprd_vif *vif;
	u8 ctx_id;
	u16 len, plen;
	u8 *data;
	struct sprd_hif *hif;

	ctx_id = hdr->common.mode;
	/*TODO ctx_id range*/
	if (ctx_id > STAP_MODE_P2P_DEVICE) {
		pr_info("%s invalid ctx_id: %d\n", __func__, ctx_id);
		return 0;
	}

	plen = SPRD_GET_LE16(hdr->plen);
	if (!priv) {
		pr_err("%s priv is NULL [%u]ctx_id %d recv[%s]len: %d\n",
		       __func__, le32_to_cpu(hdr->mstime), ctx_id,
		       cmdevt_evt2str(hdr->cmd_id), hdr->plen);
		return plen;
	}

	if (hdr->cmd_id != EVT_SDIO_FLOWCON) {
		wl_info("[%u]ctx_id %d recv[%s]len: %d,rsp_cnt=%d\n",
			le32_to_cpu(hdr->mstime), ctx_id,
			cmdevt_evt2str(hdr->cmd_id), plen, hdr->rsp_cnt);
	}

	if (plen < sizeof(struct sprd_cmd_hdr)) {
		pr_err("%s plen is invalid!\n", __func__);
		return plen;
	}

	/* NEW_STATION can contain the firmware SAE PMK/PMKID. */
	if (hdr->cmd_id != EVT_NEW_STATION)
		print_hex_dump_debug("EVENT: ", DUMP_PREFIX_OFFSET, 16, 1,
				     (u8 *)hdr, hdr->plen, 0);

	len = plen - sizeof(*hdr);
	vif = sc2355_ctxid_to_vif(priv, ctx_id);
	if (!vif) {
		pr_info("%s NULL vif for ctx_id: %d, len:%d, id:%d\n",
			__func__, ctx_id, plen, hdr->cmd_id);
		if (hdr->cmd_id != EVT_COEX_BT_ON_OFF)
			return plen;
	}
	hif = &priv->hif;

	if (!((long)msg & 0x3)) {
		data = (u8 *)msg;
		data += sizeof(*hdr);
	} else {
		/* never into here when the dev is BA or MARLIN2,
		 * temply used as debug and safe
		 */
		WARN_ON(1);
		data = kmalloc(len, GFP_KERNEL);
		if (!data) {
			sprd_put_vif(vif);
			return plen;
		}
		memcpy(data, msg + sizeof(*hdr), len);
	}

	switch (hdr->cmd_id) {
	case EVT_CONNECT:
		cmdevt_report_connect_evt(vif, data, len);
		break;
	case EVT_DISCONNECT:
		cmdevt_report_disconnect_evt(vif, data, len);
		break;
	case EVT_REMAIN_CHAN_EXPIRED:
		cmdevt_report_remain_on_channel_evt(vif, data, len);
		break;
	case EVT_NEW_STATION:
		cmdevt_report_new_station_evt(vif, data, len);
		break;
	case EVT_MGMT_FRAME:
		cmdevt_report_frame_evt(vif, data, len, 0);
		break;
#ifdef CONFIG_SPRD_WLAN_VENDOR_SPECIFIC
	case EVT_GSCAN_FRAME:
		sc2355_report_gscan_frame_evt(vif, data, len);
		break;
	case EVT_RSSI_MONITOR:
		sc2355_evt_rssi_monitor(vif, data, len);
		break;
#endif /* CONFIG_SPRD_WLAN_VENDOR_SPECIFIC */
	case EVT_SCAN_DONE:
		cmdevt_report_scan_done_evt(vif, data, len);
		break;
	case EVT_SDIO_SEQ_NUM:
		break;
	case EVT_MIC_FAIL:
		cmdevt_report_mic_failure_evt(vif, data, len);
		break;
	case EVT_CQM:
		cmdevt_report_cqm_evt(vif, data, len);
		break;
	case EVT_MGMT_TX_STATUS:
		cmdevt_report_mlme_tx_status_evt(vif, data, len);
		break;
	case EVT_TDLS:
		cmdevt_report_tdls_evt(vif, data, len);
		break;
	case EVT_SUSPEND_RESUME:
		cmdevt_report_suspend_resume_evt(vif, data, len);
		break;
#ifdef CONFIG_SC2355_WLAN_NAN
	case EVT_NAN:
		sc2355_nan_event(vif, data, len);
		break;
#endif /* CONFIG_SC2355_WLAN_NAN */
	case EVT_STA_LUT_INDEX:
		if (hif->hw_type == SPRD_HW_SC2355_PCIE)
			sc2355_pcie_event_sta_lut(vif, data, len);
		else
			sc2355_event_sta_lut(vif, data, len);
		break;
	case EVT_BA:
		cmdevt_report_ba_mgmt_evt(vif, data, len);
		break;
#ifdef CONFIG_SC2355_WLAN_RTT
	case EVT_RTT:
		sc2355_rtt_event(vif, data, len);
		break;
#endif /* CONFIG_SC2355_WLAN_RTT */
	case EVT_HANG_RECOVERY:
		cmdevt_report_hang_recovery_evt(vif, data, len);
		break;
	case EVT_THERMAL_WARN:
		cmdevt_report_thermal_warn_evt(vif, data, len);
		break;
	case EVT_WFD_MIB_CNT:
		cmdevt_report_wfd_mib_cnt(vif, data, len);
		break;
	case EVT_FW_PWR_DOWN:
		cmdevt_report_fw_power_down_evt(vif, data, len);
		break;
	case EVT_SDIO_FLOWCON:
		break;
	case EVT_CHAN_CHANGED:
		cmdevt_report_chan_changed_evt(vif, data, len);
		break;
	case EVT_COEX_BT_ON_OFF:
		cmdevt_report_coex_bt_on_off_evt(data, len, hif->hw_type);
		break;
	case EVT_ACS_DONE:
		cmdevt_report_acs_done_evt(vif, data, len);
		break;
#ifdef CONFIG_SPRD_WLAN_VENDOR_SPECIFIC
	case EVT_ACS_LTE_CONFLICT_EVENT:
		sc2355_report_acs_lte_event(vif);
		break;
#endif /* CONFIG_SPRD_WLAN_VENDOR_SPECIFIC */
	case EVT_FRESH_POWER_BO:
		sc2355_evt_pw_backoff(vif, data, len);
		break;
	case EVT_REPORT_IP_ADDR:
		cmdevt_report_ip_addr(vif, data, len);
		break;
	default:
		pr_info("unsupported event: %d\n", hdr->cmd_id);
		break;
	}

	sprd_put_vif(vif);

	if ((long)msg & 0x3)
		kfree(data);

	return plen;
}

unsigned short sc2355_rx_rsp_process(struct sprd_priv *priv, u8 *msg)
{
	u16 plen;
	void *data;
	int handle_flag = 0;
	struct sprd_cmd *cmd = &priv->cmd;
	struct sprd_cmd_hdr *hdr;

	if (unlikely(!cmd->init_ok)) {
		pr_info("%s cmd coming too early, drop it\n", __func__);
		return 0;
	}

	hdr = (struct sprd_cmd_hdr *)msg;
	plen = SPRD_GET_LE16(hdr->plen);

	/* Command replies may echo credential-bearing payloads. */

	/* 2048 use mac */
	/*TODO here ctx_id range*/
	if (hdr->common.mode > STAP_MODE_P2P_DEVICE ||
	    hdr->cmd_id > CMD_MAX || plen > 2048) {
		pr_err("%s wrong CMD_RSP: ctx_id:%d;cmd_id:%d\n",
		       __func__, hdr->common.mode, hdr->cmd_id);
		return 0;
	}
	if (atomic_inc_return(&cmd->refcnt) >= SPRD_CMD_EXIT_VAL) {
		atomic_dec(&cmd->refcnt);
		pr_err("cmd->refcnt=%x\n", atomic_read(&cmd->refcnt));
		return 0;
	}

	if (atomic_read(&cmd->ignore_resp)) {
		atomic_dec(&cmd->refcnt);
		pr_warn("ignore %s response\n", cmdevt_cmd2str(hdr->cmd_id));
		return plen;
	}
	data = kmalloc(plen, GFP_KERNEL);
	if (!data) {
		atomic_dec(&cmd->refcnt);
		pr_err("cmd->refcnt=%x\n", atomic_read(&cmd->refcnt));
		return plen;
	}
	memcpy(data, (void *)hdr, plen);

	spin_lock_bh(&cmd->lock);
	if (!cmd->data && SPRD_GET_LE32(hdr->mstime) == cmd->mstime &&
	    hdr->cmd_id == cmd->cmd_id) {
		pr_debug("ctx_id %d recv rsp[%s]\n",
			hdr->common.mode, cmdevt_cmd2str(hdr->cmd_id));
		if (unlikely(hdr->status != 0)) {
			pr_err("%s ctx_id %d recv rsp[%s] status[%s]\n",
			       __func__, hdr->common.mode,
			       cmdevt_cmd2str(hdr->cmd_id), cmdevt_err2str(hdr->status));
			handle_flag = cmdevt_handle_rsp_status_err(hdr->cmd_id,
								   hdr->status);
			if (hdr->cmd_id == CMD_TX_MGMT) {
				pr_err("tx mgmt status : %d\n", hdr->status);
				priv->tx_mgmt_status = hdr->status;
			}
		}
		cmd->data = data;
		complete(&cmd->completed);
	} else {
		kfree(data);
		pr_err("%s ctx_id %d recv mismatched rsp[%s] status[%s]\n",
		       __func__, hdr->common.mode,
		       cmdevt_cmd2str(hdr->cmd_id), cmdevt_err2str(hdr->status));
		pr_err("%s mstime:[%u %u]\n", __func__,
		       SPRD_GET_LE32(hdr->mstime), cmd->mstime);
	}
	spin_unlock_bh(&cmd->lock);
	atomic_dec(&cmd->refcnt);
	pr_debug("cmd->refcnt=%x\n", atomic_read(&cmd->refcnt));

	if (handle_flag)
		sc2355_assert_cmd(priv, NULL, hdr->cmd_id, HANDLE_FLAG_ERROR);

	return plen;
}
