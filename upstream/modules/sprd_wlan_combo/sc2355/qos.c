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

#include "common/common.h"
#include "common/msg.h"
#include "qos.h"

static unsigned int qos_enable;

static bool wmmac_available[NUM_AC] = { false, false, false, false };
static u32 wmmac_admittedtime[NUM_AC] = { 0 };
static u32 wmmac_usedtime[NUM_AC] = { 0 };

static struct qos_wmm_ac_ts_t sta_ts_info[NUM_TID];

struct qos_map_set qos_map;

static const u8 up_to_ac[] = {
	0,			/*SPRD_AC_BE */
	1,			/*SPRD_AC_BK */
	1,			/*SPRD_AC_BK */
	0,			/*SPRD_AC_BE */
	4,			/*SPRD_AC_VI */
	4,			/*SPRD_AC_VI */
	6,			/*SPRD_AC_VO */
	6			/*SPRD_AC_VO */
};

static unsigned int qos_pkt_get_prio(void *skb, int data_offset, unsigned char *tos)
{
	struct qos_ether_header *eh;
	struct qos_ethervlan_header *evh;
	unsigned char *pktdata;
	unsigned int priority = prio_6;

	pktdata = ((struct sk_buff *)(skb))->data + data_offset;
	eh = (struct qos_ether_header *)pktdata;

	if (eh->ether_type == cpu_to_be16(ETHER_TYPE_8021Q)) {
		unsigned short vlan_tag;
		int vlan_prio;

		evh = (struct qos_ethervlan_header *)eh;

		vlan_tag = be16_to_cpu(evh->vlan_tag);
		vlan_prio = (int)(vlan_tag >> VLAN_PRI_SHIFT) & VLAN_PRI_MASK;
		priority = vlan_prio;
	} else {
		unsigned char *ip_body =
		    pktdata + sizeof(struct qos_ether_header);
		unsigned char tos_tc = IP_TOS46(ip_body) & 0xE0;

		*tos = IP_TOS46(ip_body);
		switch (tos_tc) {
		case 0x00:
		case 0x60:
			priority = prio_0;/*BE*/
			break;
		case 0x20:
		case 0x40:
			priority = prio_1;/*BK*/
			break;
		case 0x80:
		case 0xA0:
			priority = prio_4;/*VI*/
			break;
		default:
			priority = prio_6;/*VO*/
			break;
		}
	}

	PKT_SET_PRIO(skb, priority);
	return priority;
}

static unsigned int qos_map_edca_ac_to_priority(u8 ac)
{
	unsigned int priority;

	switch (ac) {
	case AC_BK:
		priority = prio_1;
		break;
	case AC_VI:
		priority = prio_4;
		break;
	case AC_VO:
		priority = prio_6;
		break;
	case AC_BE:
	default:
		priority = prio_0;
		break;
	}
	return priority;
}

unsigned int priority_map_to_qos_index(int priority)
{
	enum qos_head_type_t qos_index = SPRD_AC_BE;

	switch (up_to_ac[priority]) {
	case prio_1:
		qos_index = SPRD_AC_BK;
		break;
	case prio_4:
		qos_index = SPRD_AC_VI;
		break;
	case prio_6:
		qos_index = SPRD_AC_VO;
		break;
	default:
		qos_index = SPRD_AC_BE;
		break;
	}
	/*return data_type as qos queue index */
	return qos_index;
}

void qos_update_wmmac_edcaftime_timeout(struct timer_list *t)
{
	struct sprd_wmmac_params *wmmac =
	    from_timer(wmmac, t, wmmac_edcaf_timer);

	/*restart edcaf timer per second */
	mod_timer(&wmmac->wmmac_edcaf_timer,
		  jiffies + WMMAC_EDCA_TIMEOUT_MS * HZ / 1000);

	if (wmmac_admittedtime[AC_VO] > 0) {
		wmmac_usedtime[AC_VO] = 0;
		wmmac_available[AC_VO] = true;
	}
	if (wmmac_admittedtime[AC_VI] > 0) {
		wmmac_usedtime[AC_VI] = 0;
		wmmac_available[AC_VI] = true;
	}
}

unsigned int qos_match_q(void *skb, int data_offset)
{
	int priority;
	struct qos_ether_header *eh;
	enum qos_head_type_t data_type = SPRD_AC_BE;
	unsigned char tos = 0;

	if (!qos_enable)
		return SPRD_AC_BE;
	/* vo vi bk be */
	eh = (struct qos_ether_header *)(((struct sk_buff *)(skb))->data +
					 data_offset);

	if (cpu_to_be16(ETHER_TYPE_IP) != eh->ether_type &&
	    cpu_to_be16(ETHER_TYPE_IPV6) != eh->ether_type) {
		goto OUT;
	}
	priority = qos_pkt_get_prio(skb, data_offset, &tos);
	switch (priority) {
	case prio_1:
		data_type = SPRD_AC_BK;
		break;
	case prio_4:
		data_type = SPRD_AC_VI;
		break;
	case prio_6:
		data_type = SPRD_AC_VO;
		break;
	default:
		data_type = SPRD_AC_BE;
		break;
	}
OUT:
	/*return data_type as qos queue index */
	return data_type;
}

const u8 *get_wmm_ie(u8 *res, u16 ie_len, u8 ie, uint oui, uint oui_type)
{
	const u8 *end, *pos;

	pos = res;
	end = pos + ie_len;
	while (pos + 1 < end) {
		if (pos + 2 + pos[1] > end)
			break;
		/*try to find VENDOR_SPECIFIC ie, which wmm ie located */
		if (pos[0] == ie) {
			/*match the OUI_MICROSOFT 0x0050f2 ie, and WMM ie */
			if ((((pos[2] << 16) | (pos[3] << 8) | pos[4]) == oui) &&
			    pos[5] == WMM_OUI_TYPE) {

				/*skip head of wmm_ac parameter(oui[3],oui_type,...,reserved)*/
				if (pos[1] > 10) {
					pos += 10;
				} else {
					pos += 2;
				}

				return pos;
			}
			break;
		}
		pos += 2 + pos[1];
	}
	return NULL;
}

void sc2355_qos_init(struct qos_tx_t *tx_list)
{
	int i, j;

	/*tx_list->index = SPRD_AC_VO; */
	for (i = 0; i < SPRD_AC_MAX; i++) {
		for (j = 0; j < MAX_LUT_NUM; j++) {
			INIT_LIST_HEAD(&tx_list->q_list[i].p_list[j].head_list);
			spin_lock_init(&tx_list->q_list[i].p_list[j].p_lock);
			atomic_set(&tx_list->q_list[i].p_list[j].l_num, 0);
		}
	}
}

unsigned int sc2355_qos_tid_map_to_index(unsigned char tid)
{
	enum qos_head_type_t qos_index = SPRD_AC_BE;

	switch (tid) {
	case prio_1:
		qos_index = SPRD_AC_BK;
		break;
	case prio_4:
		qos_index = SPRD_AC_VI;
		break;
	case prio_6:
		qos_index = SPRD_AC_VO;
		break;
	default:
		qos_index = SPRD_AC_BE;
		break;
	}
	/*return data_type as qos queue index */
	return qos_index;
}

unsigned int sc2355_qos_get_tid_index(void *skb, int data_offset,
				      unsigned char *tid, unsigned char *tos)
{
	int priority;
	struct qos_ether_header *eh;

	if (!qos_enable)
		return SPRD_AC_BE;
	/* vo vi bk be */
	eh = (struct qos_ether_header *)(((struct sk_buff *)(skb))->data +
					 data_offset);
	priority = qos_pkt_get_prio(skb, data_offset, tos);
	*tid = priority;

	/*return data_type as qos queue index */
	return sc2355_qos_tid_map_to_index(*tid);
}

int sc2355_qos_get_list_num(struct list_head *list)
{
	int num = 0;
	struct list_head *pos;
	struct list_head *n_list;

	if (list_empty(list))
		return 0;
	list_for_each_safe(pos, n_list, list)
		num++;

	return num;
}

unsigned int sc2355_qos_map_priority_to_edca_ac(int priority)
{
	int ac;

	switch (priority) {
	case 01:
	case 02:
		ac = AC_BK;
		break;

	case 04:
	case 05:
		ac = AC_VI;
		break;

	case 06:
	case 07:
		ac = AC_VO;
		break;

	case 00:
	case 03:
	default:
		ac = AC_BE;
		break;
	}
	/*return data_type as qos queue index */
	return ac;
}

void sc2355_qos_update_wmmac_ts_info(u8 tsid, u8 up, u8 ac, bool status,
				     u16 admitted_time)
{
	sta_ts_info[tsid].exist = status;
	sta_ts_info[tsid].ac = ac;
	sta_ts_info[tsid].up = up;
	sta_ts_info[tsid].admitted_time = admitted_time;
}

u16 sc2355_qos_get_wmmac_admitted_time(u8 tsid)
{
	u16 value = 0;

	if (sta_ts_info[tsid].exist)
		value = sta_ts_info[tsid].admitted_time;

	return value;
}

void sc2355_qos_remove_wmmac_ts_info(u8 tsid)
{
	memset(&sta_ts_info[tsid], 0, sizeof(struct qos_wmm_ac_ts_t));
}

void sc2355_qos_update_admitted_time(struct sprd_priv *priv, u8 tsid,
				     u16 medium_time, bool increase)
{
	u8 ac = sta_ts_info[tsid].ac;

	if (increase) {
		wmmac_admittedtime[ac] += (medium_time << 5);
		mod_timer(&priv->wmmac.wmmac_edcaf_timer,
			  jiffies + WMMAC_EDCA_TIMEOUT_MS * HZ / 1000);
	} else {
		if (wmmac_admittedtime[ac] > (medium_time << 5)) {
			wmmac_admittedtime[ac] -= (medium_time << 5);
		} else {
			wmmac_admittedtime[ac] = 0;
			if (timer_pending(&priv->wmmac.wmmac_edcaf_timer))
				del_timer_sync(&priv->wmmac.wmmac_edcaf_timer);
		}
	}

	wmmac_available[ac] = (wmmac_usedtime[ac] < wmmac_admittedtime[ac]);
}

int sc2355_sync_wmm_param(struct sprd_priv *priv,
			  struct sprd_connect_info *conn_info)
{
	struct sprd_wmmac_params *wmm_params = NULL;
	int i;

	wmm_params = (struct sprd_wmmac_params *)
			get_wmm_ie(conn_info->resp_ie,
				   conn_info->resp_ie_len,
				   WLAN_EID_VENDOR_SPECIFIC,
				   OUI_MICROSOFT,
				   WMM_OUI_TYPE);
	if (wmm_params != NULL) {
		for (i = 0; i < NUM_AC; i++) {
			pr_info("%s: wmm_params->ac[%d].aci_aifsn: %x",
				__func__, i, wmm_params->ac[i].aci_aifsn);
			priv->wmmac.ac[i].aci_aifsn =
				wmm_params->ac[i].aci_aifsn;
		}
		return 1;
	} else {
		pr_err("%s, wmm_params is NULL!!!!", __func__);
		return 0;
	}
}

/*change priority according to the wmmac_available value */
unsigned int sc2355_qos_change_priority_if(struct sprd_priv *priv,
					   unsigned char *tid,
					   unsigned char *tos, u16 len)
{
	unsigned int qos_index, ac;
	int match_index = 0;
	unsigned char priority = *tos;

	priority >>= 2;

	for (match_index = 0; match_index < QOS_MAP_MAX_DSCP_EXCEPTION;
	     match_index++) {
		if (priority == qos_map.qos_exceptions[match_index].dscp) {
			*tid = qos_map.qos_exceptions[match_index].up;
			break;
		}
	}

	if (match_index >= QOS_MAP_MAX_DSCP_EXCEPTION) {
		for (match_index = 0; match_index < 8; match_index++) {
			if (priority >=
			     qos_map.qos_ranges[match_index].low && priority <=
				qos_map.qos_ranges[match_index].high) {
				*tid = qos_map.qos_ranges[match_index].up;
				break;
			}
		}
	}

	if (qos_enable == 1) {
		ac = sc2355_qos_map_priority_to_edca_ac(*tid);
		while (ac != 0) {
			if (!!(priv->wmmac.ac[ac].aci_aifsn & WMM_AC_ACM)) {
				/*current ac is available, use it directly */
				if (wmmac_available[ac]) {
					wmmac_usedtime[ac] +=
					    (len + 4) *
					    8 * get_wmmac_ratio() / 10 / 54;
					wmmac_available[ac] =
					    (wmmac_usedtime[ac] <
					     wmmac_admittedtime[ac]);
					break;
				}
				if (!wmmac_available[ac] &&
				    wmmac_usedtime[ac] != 0)
					return SPRD_AC_MAX;
				/*downgrade to lower ac, then try again */
				ac--;
			} else {
				break;
			}
		}

		*tid = qos_map_edca_ac_to_priority(ac);
	}

	switch (*tid) {
	case prio_1:
		qos_index = SPRD_AC_BK;
		break;
	case prio_4:
		qos_index = SPRD_AC_VI;
		break;
	case prio_6:
		qos_index = SPRD_AC_VO;
		break;
	default:
		qos_index = SPRD_AC_BE;
		break;
	}

	/*return data_type as qos queue index */
	return qos_index;
}

void sc2355_qos_init_default_map(void)
{
	u8 index;

	for (index = 0; index < QOS_MAP_MAX_DSCP_EXCEPTION; index++) {
		qos_map.qos_exceptions[index].dscp = 0xFF;
		qos_map.qos_exceptions[index].up = prio_0;
	}

	index = 0;
	qos_map.qos_ranges[index].low = 0x0;	/*IP-PL0 */
	qos_map.qos_ranges[index].high = 0x0;
	qos_map.qos_ranges[index].up = prio_0;

	index++;
	qos_map.qos_ranges[index].low = 0x3;	/*IP-PL3 */
	qos_map.qos_ranges[index].high = 0x3;
	qos_map.qos_ranges[index].up = prio_0;

	index++;
	qos_map.qos_ranges[index].low = 0x1;	/*IP-PL1 */
	qos_map.qos_ranges[index].high = 0x2;	/*IP-PL2 */
	qos_map.qos_ranges[index].up = prio_1;

	index++;
	qos_map.qos_ranges[index].low = 0x4;	/*IP-PL4 */
	qos_map.qos_ranges[index].high = 0x5;	/*IP-PL5 */
	qos_map.qos_ranges[index].up = prio_4;

	index++;
	qos_map.qos_ranges[index].low = 0x6;	/*IP-PL6 */
	qos_map.qos_ranges[index].high = 0x7;	/*IP-PL7 */
	qos_map.qos_ranges[index].up = prio_6;
}

void sc2355_qos_enable(int flag)
{
	qos_enable = flag;
}

/*init wmmac params, include timer and ac params*/
void sc2355_qos_wmm_ac_init(struct sprd_priv *priv)
{
	u8 ac;

	for (ac = 0; ac < NUM_AC; ac++) {
		wmmac_usedtime[ac] = 0;
		wmmac_available[ac] = false;
		wmmac_admittedtime[ac] = 0;
	}

	timer_setup(&priv->wmmac.wmmac_edcaf_timer,
		    qos_update_wmmac_edcaftime_timeout, 0);
	memset(&priv->wmmac.ac[0], 0, 4 * sizeof(struct wmm_ac_params));
}

void sc2355_qos_reset_wmmac_parameters(struct sprd_priv *priv)
{
	u8 ac;

	for (ac = 0; ac < NUM_AC; ac++) {
		wmmac_usedtime[ac] = 0;
		wmmac_available[ac] = false;
		wmmac_admittedtime[ac] = 0;
	}
	if (timer_pending(&priv->wmmac.wmmac_edcaf_timer))
		del_timer_sync(&priv->wmmac.wmmac_edcaf_timer);

	memset(&priv->wmmac.ac[0], 0, 4 * sizeof(struct wmm_ac_params));
}

void sc2355_qos_reset_wmmac_ts_info(void)
{
	u8 tsid;

	for (tsid = 0; tsid < NUM_TID; tsid++)
		sc2355_qos_remove_wmmac_ts_info(tsid);
}
