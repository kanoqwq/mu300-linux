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
#ifndef __CHIP_OPS_H__
#define __CHIP_OPS_H__

#include <linux/ieee80211.h>
#include <linux/nl80211.h>
#include <linux/skbuff.h>
#include "common/cmd.h"
#include "common/common.h"
#include "common/msg.h"

struct sprd_chip_ops {
	struct sprd_msg *(*get_msg)(struct sprd_chip *chip,
				    enum sprd_head_type type,
				    enum sprd_mode mode);
	void (*free_msg)(struct sprd_chip *chip, struct sprd_msg *msg);
	int (*tx_prepare)(struct sprd_chip *chip, struct sk_buff *skb);
	int (*tx)(struct sprd_chip *chip, struct sprd_msg *msg);
	int (*force_exit)(struct sprd_chip *chip);
	int (*is_exit)(struct sprd_chip *chip);
	void (*drop_tcp_msg)(struct sprd_chip *chip, struct sprd_msg *msg);
	void (*set_qos)(struct sprd_chip *chip, enum sprd_mode mode,
			int enable);
	int (*suspend)(struct sprd_chip *chip);
	int (*resume)(struct sprd_chip *chip);

	void (*setup_wiphy)(struct wiphy *wiphy, struct sprd_priv *priv);
	void (*cfg80211_ops_update)(struct cfg80211_ops *ops);
	void (*cmd_init)(struct sprd_cmd *cmd);
	void (*cmd_deinit)(struct sprd_cmd *cmd);
	int (*get_fw_info)(struct sprd_priv *priv);
	int (*open_fw)(struct sprd_priv *priv,
		       struct sprd_vif *vif, u8 *mac_addr);
	int (*close_fw)(struct sprd_priv *priv, struct sprd_vif *vif);
	int (*power_save)(struct sprd_priv *priv, struct sprd_vif *vif,
			  u8 sub_type, u8 status);
	int (*set_sar)(struct sprd_priv *priv, struct sprd_vif *vif,
		       u8 sub_type, s8 value);
	void (*fcc_reset)(void);
	void (*fcc_init)(void);
	int (*add_key)(struct sprd_priv *priv, struct sprd_vif *vif,
		       const u8 *key_data, u8 key_len, bool pairwise,
		       u8 key_index, const u8 *key_seq, u8 cypher_type,
		       const u8 *mac_addr);
	int (*del_key)(struct sprd_priv *priv,
		       struct sprd_vif *vif, u8 key_index,
		       bool pairwise, const u8 *mac_addr);
	int (*set_def_key)(struct sprd_priv *priv,
			   struct sprd_vif *vif, u8 key_index);
	int (*set_beacon_ie)(struct sprd_priv *priv, struct sprd_vif *vif,
			     const u8 *ie, u16 len);
	int (*set_probereq_ie)(struct sprd_priv *priv, struct sprd_vif *vif,
			       const u8 *ie, u16 len);
	int (*set_proberesp_ie)(struct sprd_priv *priv, struct sprd_vif *vif,
				const u8 *ie, u16 len);
	int (*set_assocreq_ie)(struct sprd_priv *priv, struct sprd_vif *vif,
			       const u8 *ie, u16 len);
	int (*set_assocresp_ie)(struct sprd_priv *priv, struct sprd_vif *vif,
				const u8 *ie, u16 len);
	int (*set_sae_ie)(struct sprd_priv *priv, struct sprd_vif *vif,
			  const u8 *ie, u16 len);
	int (*start_ap)(struct sprd_priv *priv, struct sprd_vif *vif,
			u8 *beacon, u16 len, struct cfg80211_ap_settings *settings);
	int (*del_station)(struct sprd_priv *priv, struct sprd_vif *vif,
			   const u8 *mac_addr, u16 reason_code);
	int (*get_station)(struct sprd_priv *priv, struct sprd_vif *vif,
			   struct sprd_sta_info *sta);
	int (*set_channel)(struct sprd_priv *priv, struct sprd_vif *vif,
			   u8 channel);
	int (*connect)(struct sprd_priv *priv, struct sprd_vif *vif,
		       struct cmd_connect *p);
	int (*disconnect)(struct sprd_priv *priv, struct sprd_vif *vif,
			  u16 reason_code);
	int (*pmksa)(struct sprd_priv *priv, struct sprd_vif *vif,
		     const u8 *bssid, const u8 *pmkid, u8 type);
	int (*remain_chan)(struct sprd_priv *priv, struct sprd_vif *vif,
			   struct ieee80211_channel *channel,
			   enum nl80211_channel_type channel_type,
			   u32 duration, u64 *cookie);
	int (*cancel_remain_chan)(struct sprd_priv *priv, struct sprd_vif *vif,
				  u64 cookie);
	int (*tx_mgmt)(struct sprd_priv *priv, struct sprd_vif *vif, u8 channel,
		       u8 dont_wait_for_ack, u32 wait, u64 *cookie,
		       const u8 *mac, size_t mac_len);
	int (*register_frame)(struct sprd_priv *priv, struct sprd_vif *vif,
			      u16 type, u8 reg);
	int (*tdls_mgmt)(struct sprd_vif *vif, struct sk_buff *skb);
	int (*tdls_oper)(struct sprd_priv *priv, struct sprd_vif *vif,
			 const u8 *peer, int oper);
	int (*start_tdls_channel_switch)(struct sprd_priv *priv,
					 struct sprd_vif *vif,
					 const u8 *peer_mac, u8 primary_chan,
					 u8 second_chan_offset, u8 band);
	int (*cancel_tdls_channel_switch)(struct sprd_priv *priv,
					  struct sprd_vif *vif,
					  const u8 *peer_mac);
	int (*set_cqm_rssi)(struct sprd_priv *priv, struct sprd_vif *vif,
			    s32 rssi_thold, u32 rssi_hyst);
	int (*set_roam_offload)(struct sprd_priv *priv, struct sprd_vif *vif,
				u8 sub_type, const u8 *data, u8 len);
	int (*set_blacklist)(struct sprd_priv *priv, struct sprd_vif *vif,
			     u8 sub_type, u8 num, u8 *mac_addr);
	int (*set_whitelist)(struct sprd_priv *priv, struct sprd_vif *vif,
			     u8 sub_type, u8 num, u8 *mac_addr);
	int (*set_param)(struct sprd_priv *priv, u32 rts, u32 frag);
	int (*set_qos_map)(struct sprd_priv *priv, struct sprd_vif *vif,
			   void *map);
	int (*add_tx_ts)(struct sprd_priv *priv, struct sprd_vif *vif, u8 tsid,
			 const u8 *peer, u8 user_prio, u16 admitted_time);
	int (*del_tx_ts)(struct sprd_priv *priv, struct sprd_vif *vif, u8 tsid,
			 const u8 *peer);
	int (*sync_wmm_param)(struct sprd_priv *priv,
			      struct sprd_connect_info *conn_info);
	int (*set_mc_filter)(struct sprd_priv *priv, struct sprd_vif *vif,
			     u8 sub_type, u8 num, u8 *mac_addr);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	int (*set_miracast)(struct net_device *ndev, void __user *data);
#else
	int (*set_miracast)(struct net_device *ndev, struct ifreq *ifr);
#endif
	int (*set_11v_feature_support)(struct sprd_priv *priv,
				       struct sprd_vif *vif,
				       u16 val);
	int (*set_11v_sleep_mode)(struct sprd_priv *priv, struct sprd_vif *vif,
				  u8 status, u16 interval);
	int (*xmit_data2cmd)(struct sk_buff *skb, struct net_device *ndev);
	int (*set_random_mac)(struct sprd_priv *priv, struct sprd_vif *vif,
			      u8 random_mac_flag, u8 *addr);
	int (*set_max_clients_allowed)(struct sprd_priv *priv,
				       struct sprd_vif *vif,
				       int n_clients);
	bool (*do_delay_work)(struct sprd_work *work);
	int (*notify_ip)(struct sprd_priv *priv, struct sprd_vif *vif,
			 u8 ip_type, u8 *ip_addr);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	int (*set_vowifi)(struct net_device *ndev, void __user *data);
#else
	int (*set_vowifi)(struct net_device *ndev, struct ifreq *ifr);
#endif
	int (*dump_survey)(struct wiphy *wiphy, struct net_device *ndev,
			   int idx, struct survey_info *s_info);

	int (*npi_send_recv)(struct sprd_priv *priv, struct sprd_vif *vif,
			     u8 *s_buf, u16 s_len, u8 *r_buf, u16 *r_len);

	void (*qos_init_default_map)(void);
	void (*qos_enable)(int flag);
	void (*qos_wmm_ac_init)(struct sprd_priv *priv);
	void (*qos_reset_wmmac_parameters)(struct sprd_priv *priv);
	void (*qos_reset_wmmac_ts_info)(void);
	int (*scan)(struct wiphy *wiphy,
		    struct cfg80211_scan_request *request);
	int (*sched_scan_start)(struct wiphy *wiphy, struct net_device *ndev,
				struct cfg80211_sched_scan_request *request);
	int (*sched_scan_stop)(struct wiphy *wiphy, struct net_device *ndev,
			       u64 reqid);
	void (*scan_timeout)(struct timer_list *t);

	void (*tcp_ack_init)(struct sprd_priv *priv);
	void (*tcp_ack_deinit)(struct sprd_priv *priv);
	int (*vendor_init)(struct wiphy *wiphy);
	int (*vendor_deinit)(struct wiphy *wiphy);

	int (*send_data)(struct sprd_vif *vif, struct sprd_msg *msg,
			 struct sk_buff *skb, u8 type, u8 offset, bool flag);
	int (*send_data_offset)(void);
	void (*fc_add_share_credit)(struct sprd_vif *vif);
	void (*defrag_recover)(struct sprd_vif *vif);
	int (*set_sniffer)(struct net_device *ndev, struct ifreq *ifr);
};

static
inline struct sprd_msg *sprd_chip_get_msg(struct sprd_chip *chip,
					  enum sprd_head_type type,
					  enum sprd_mode mode)
{
	return chip->ops->get_msg(chip, type, mode);
}

static inline void sprd_chip_free_msg(struct sprd_chip *chip,
				      struct sprd_msg *msg)
{
	return chip->ops->free_msg(chip, msg);
}

static inline int sprd_chip_tx_prepare(struct sprd_chip *chip,
				       struct sk_buff *skb)
{
	if (chip->ops->tx_prepare)
		return chip->ops->tx_prepare(chip, skb);
	return 0;
}

/* return:
 *      0: msg buf freed by the real driver
 *      others: skb need free by the caller, remember not use msg->skb!
 */
static inline int sprd_chip_tx(struct sprd_chip *chip, struct sprd_msg *msg)
{
	return chip->ops->tx(chip, msg);
}

static inline int sprd_chip_force_exit(void *sprd_chip)
{
	struct sprd_chip *chip = (struct sprd_chip *)sprd_chip;

	return chip->ops->force_exit(chip);
}

static inline int sprd_chip_is_exit(struct sprd_chip *chip)
{
	return chip->ops->is_exit(chip);
}

static inline int sprd_chip_suspend(struct sprd_chip *chip)
{
	if (chip->ops->suspend)
		return chip->ops->suspend(chip);

	return 0;
}

static inline int sprd_chip_resume(struct sprd_chip *chip)
{
	if (chip->ops->resume)
		return chip->ops->resume(chip);

	return 0;
}

static inline void sprd_chip_drop_tcp_msg(struct sprd_chip *chip,
					  struct sprd_msg *msg)
{
	if (chip->ops->drop_tcp_msg)
		chip->ops->drop_tcp_msg(chip, msg);
}

static inline void sprd_chip_set_qos(struct sprd_chip *chip,
				     enum sprd_mode mode, int enable)
{
	if (chip->ops->set_qos)
		chip->ops->set_qos(chip, mode, enable);
}

static inline void sprd_cmd_init(struct sprd_priv *priv)
{
	if (priv->chip.ops->cmd_init)
		priv->chip.ops->cmd_init(&priv->cmd);
}

static inline void sprd_cmd_deinit(struct sprd_priv *priv)
{
	if (priv->chip.ops->cmd_deinit)
		priv->chip.ops->cmd_deinit(&priv->cmd);
}

static inline void sprd_setup_wiphy(struct wiphy *wiphy, struct sprd_priv *priv)
{
	if (priv->chip.ops->setup_wiphy)
		priv->chip.ops->setup_wiphy(wiphy, priv);
}

static inline void sprd_cfg80211_ops_update(struct sprd_priv *priv,
					    struct cfg80211_ops *ops)
{
	if (priv->chip.ops->cfg80211_ops_update)
		priv->chip.ops->cfg80211_ops_update(ops);
}

static inline int sprd_get_fw_info(struct sprd_priv *priv)
{
	if (priv->chip.ops->get_fw_info)
		return priv->chip.ops->get_fw_info(priv);

	return 0;
}

static inline int sprd_open_fw(struct sprd_priv *priv,
			       struct sprd_vif *vif, u8 *mac_addr)
{
	if (priv->chip.ops->open_fw)
		return priv->chip.ops->open_fw(priv, vif, mac_addr);

	return 0;
}

static inline int sprd_close_fw(struct sprd_priv *priv, struct sprd_vif *vif)
{
	if (priv->chip.ops->close_fw)
		return priv->chip.ops->close_fw(priv, vif);

	return 0;
}

static inline int sprd_power_save(struct sprd_priv *priv, struct sprd_vif *vif,
				  u8 sub_type, u8 status)
{
	if (priv->chip.ops->power_save)
		return priv->chip.ops->power_save(priv, vif, sub_type, status);

	return 0;
}

static inline int sprd_set_sar(struct sprd_priv *priv, struct sprd_vif *vif,
			       u8 sub_type, s8 value)
{
	if (priv->chip.ops->set_sar)
		return priv->chip.ops->set_sar(priv, vif, sub_type, value);

	return 0;
}

static inline void sprd_fcc_reset_bo(struct sprd_priv *priv)
{
	if (priv->chip.ops->fcc_reset)
		return priv->chip.ops->fcc_reset();
}

static inline void sprd_fcc_init(struct sprd_priv *priv)
{
	if (priv->chip.ops->fcc_init)
		return priv->chip.ops->fcc_init();
}

static inline int sprd_add_key(struct sprd_priv *priv, struct sprd_vif *vif,
			       const u8 *key_data, u8 key_len, bool pairwise,
			       u8 key_index, const u8 *key_seq, u8 cypher_type,
			       const u8 *mac_addr)
{
	if (priv->chip.ops->add_key)
		return priv->chip.ops->add_key(priv, vif, key_data, key_len,
					       pairwise, key_index, key_seq,
					       cypher_type, mac_addr);

	return 0;
}

static inline int sprd_del_key(struct sprd_priv *priv,
			       struct sprd_vif *vif, u8 key_index,
			       bool pairwise, const u8 *mac_addr)
{
	if (priv->chip.ops->del_key)
		return priv->chip.ops->del_key(priv, vif, key_index, pairwise,
					       mac_addr);

	return 0;
}

static inline int sprd_set_def_key(struct sprd_priv *priv,
				   struct sprd_vif *vif, u8 key_index)
{
	if (priv->chip.ops->set_def_key)
		return priv->chip.ops->set_def_key(priv, vif, key_index);

	return 0;
}

static inline int sprd_set_beacon_ie(struct sprd_priv *priv,
				     struct sprd_vif *vif, const u8 *ie,
				     u16 len)
{
	if (priv->chip.ops->set_beacon_ie)
		return priv->chip.ops->set_beacon_ie(priv, vif, ie, len);

	return 0;
}

static inline int sprd_set_probereq_ie(struct sprd_priv *priv,
				       struct sprd_vif *vif, const u8 *ie,
				       u16 len)
{
	if (priv->chip.ops->set_probereq_ie)
		return priv->chip.ops->set_probereq_ie(priv, vif, ie, len);

	return 0;
}

static inline int sprd_set_proberesp_ie(struct sprd_priv *priv,
					struct sprd_vif *vif, const u8 *ie,
					u16 len)
{
	if (priv->chip.ops->set_proberesp_ie)
		return priv->chip.ops->set_proberesp_ie(priv, vif, ie, len);

	return 0;
}

static inline int sprd_set_assocreq_ie(struct sprd_priv *priv,
				       struct sprd_vif *vif, const u8 *ie,
				       u16 len)
{
	if (priv->chip.ops->set_assocreq_ie)
		return priv->chip.ops->set_assocreq_ie(priv, vif, ie, len);

	return 0;
}

static inline int sprd_set_assocresp_ie(struct sprd_priv *priv,
					struct sprd_vif *vif, const u8 *ie,
					u16 len)
{
	if (priv->chip.ops->set_assocresp_ie)
		return priv->chip.ops->set_assocresp_ie(priv, vif, ie, len);

	return 0;
}

static inline int sprd_set_sae_ie(struct sprd_priv *priv, struct sprd_vif *vif,
				  const u8 *ie, u16 len)
{
	if (priv->chip.ops->set_sae_ie)
		return priv->chip.ops->set_sae_ie(priv, vif, ie, len);

	return 0;
}

static inline int sprd_start_ap(struct sprd_priv *priv, struct sprd_vif *vif,
				u8 *beacon, u16 len, struct cfg80211_ap_settings *settings)
{
	if (priv->chip.ops->start_ap)
		return priv->chip.ops->start_ap(priv, vif, beacon, len, settings);

	return 0;
}

static inline int sprd_del_station(struct sprd_priv *priv, struct sprd_vif *vif,
				   const u8 *mac_addr, u16 reason_code)
{
	if (priv->chip.ops->del_station)
		return priv->chip.ops->del_station(priv, vif, mac_addr,
						   reason_code);

	return 0;
}

static inline int sprd_get_station(struct sprd_priv *priv, struct sprd_vif *vif,
				   struct sprd_sta_info *sta)
{
	if (priv->chip.ops->get_station)
		return priv->chip.ops->get_station(priv, vif, sta);

	return 0;
}

static inline int sprd_set_channel(struct sprd_priv *priv, struct sprd_vif *vif,
				   u8 channel)
{
	if (priv->chip.ops->set_channel)
		return priv->chip.ops->set_channel(priv, vif, channel);

	return 0;
}

static inline int sprd_connect(struct sprd_priv *priv, struct sprd_vif *vif,
			       struct cmd_connect *p)
{
	if (priv->chip.ops->connect)
		return priv->chip.ops->connect(priv, vif, p);

	return 0;
}

static inline int sprd_disconnect(struct sprd_priv *priv, struct sprd_vif *vif,
				  u16 reason_code)
{
	if (priv->chip.ops->disconnect)
		return priv->chip.ops->disconnect(priv, vif, reason_code);

	return 0;
}

static inline int sprd_pmksa(struct sprd_priv *priv, struct sprd_vif *vif,
			     const u8 *bssid, const u8 *pmkid, u8 type)
{
	if (priv->chip.ops->pmksa)
		return priv->chip.ops->pmksa(priv, vif, bssid, pmkid, type);

	return 0;
}

static inline int sprd_remain_chan(struct sprd_priv *priv, struct sprd_vif *vif,
				   struct ieee80211_channel *channel,
				   enum nl80211_channel_type channel_type,
				   u32 duration, u64 *cookie)
{
	if (priv->chip.ops->remain_chan)
		return priv->chip.ops->remain_chan(priv, vif, channel,
				channel_type, duration, cookie);

	return 0;
}

static inline int sprd_cancel_remain_chan(struct sprd_priv *priv,
					  struct sprd_vif *vif, u64 cookie)
{
	if (priv->chip.ops->cancel_remain_chan)
		return priv->chip.ops->cancel_remain_chan(priv, vif, cookie);

	return 0;
}

static inline int sprd_tx_mgmt(struct sprd_priv *priv, struct sprd_vif *vif,
			       u8 channel, u8 dont_wait_for_ack, u32 wait,
			       u64 *cookie, const u8 *mac, size_t mac_len)
{
	if (priv->chip.ops->tx_mgmt)
		return priv->chip.ops->tx_mgmt(priv, vif, channel,
					       dont_wait_for_ack, wait, cookie,
					       mac, mac_len);

	return 0;
}

static inline int sprd_register_frame(struct sprd_priv *priv,
				      struct sprd_vif *vif, u16 type, u8 reg)
{
	if (priv->chip.ops->register_frame)
		return priv->chip.ops->register_frame(priv, vif, type, reg);

	return 0;
}

static inline int sprd_tdls_mgmt(struct sprd_priv *priv, struct sprd_vif *vif,
				 struct sk_buff *skb)
{
	if (priv->chip.ops->tdls_mgmt)
		return priv->chip.ops->tdls_mgmt(vif, skb);

	return 0;
}

static inline int sprd_tdls_oper(struct sprd_priv *priv, struct sprd_vif *vif,
				 const u8 *peer, int oper)
{
	if (priv->chip.ops->tdls_oper)
		return priv->chip.ops->tdls_oper(priv, vif, peer, oper);

	return 0;
}

static inline int sprd_start_tdls_channel_switch(struct sprd_priv *priv,
						 struct sprd_vif *vif,
						 const u8 *peer_mac,
						 u8 primary_chan,
						 u8 second_chan_offset, u8 band)
{
	if (priv->chip.ops->start_tdls_channel_switch)
		return priv->chip.ops->start_tdls_channel_switch(priv, vif,
				peer_mac, primary_chan, second_chan_offset,
				band);

	return 0;
}

static inline int sprd_cancel_tdls_channel_switch(struct sprd_priv *priv,
						  struct sprd_vif *vif,
						  const u8 *peer_mac)
{
	if (priv->chip.ops->cancel_tdls_channel_switch)
		return priv->chip.ops->cancel_tdls_channel_switch(priv, vif,
								  peer_mac);

	return 0;
}

static inline int sprd_set_cqm_rssi(struct sprd_priv *priv,
				    struct sprd_vif *vif,
				    s32 rssi_thold, u32 rssi_hyst)
{
	if (priv->chip.ops->set_cqm_rssi)
		return priv->chip.ops->set_cqm_rssi(priv, vif, rssi_thold,
						    rssi_hyst);

	return 0;
}

static inline int sprd_set_roam_offload(struct sprd_priv *priv,
					struct sprd_vif *vif,
					u8 sub_type, const u8 *data, u8 len)
{
	if (priv->chip.ops->set_roam_offload)
		return priv->chip.ops->set_roam_offload(priv, vif, sub_type,
							data, len);

	return 0;
}

static inline int sprd_set_blacklist(struct sprd_priv *priv,
				     struct sprd_vif *vif,
				     u8 sub_type, u8 num, u8 *mac_addr)
{
	if (priv->chip.ops->set_blacklist)
		return priv->chip.ops->set_blacklist(priv, vif, sub_type,
						     num, mac_addr);

	return 0;
}

static inline int sprd_set_whitelist(struct sprd_priv *priv,
				     struct sprd_vif *vif,
				     u8 sub_type, u8 num, u8 *mac_addr)
{
	if (priv->chip.ops->set_whitelist)
		return priv->chip.ops->set_whitelist(priv, vif, sub_type,
						     num, mac_addr);

	return 0;
}

static inline int sprd_set_param(struct sprd_priv *priv, u32 rts, u32 frag)
{
	if (priv->chip.ops->set_param)
		return priv->chip.ops->set_param(priv, rts, frag);

	return 0;
}

static inline int sprd_set_qos_map(struct sprd_priv *priv, struct sprd_vif *vif,
				   void *map)
{
	if (priv->chip.ops->set_qos_map)
		return priv->chip.ops->set_qos_map(priv, vif, map);

	return 0;
}
static inline int sprd_chip_sync_wmm_param(struct sprd_priv *priv,
					   struct sprd_connect_info *conn_info)
{
	if (priv->chip.ops->sync_wmm_param)
		return priv->chip.ops->sync_wmm_param(priv, conn_info);
	return 0;
}

static inline int sprd_add_tx_ts(struct sprd_priv *priv, struct sprd_vif *vif,
				 u8 tsid, const u8 *peer, u8 user_prio,
				 u16 admitted_time)
{
	if (priv->chip.ops->add_tx_ts)
		return priv->chip.ops->add_tx_ts(priv, vif, tsid, peer,
						 user_prio, admitted_time);

	return 0;
}

static inline int sprd_del_tx_ts(struct sprd_priv *priv, struct sprd_vif *vif,
				 u8 tsid, const u8 *peer)
{
	if (priv->chip.ops->del_tx_ts)
		return priv->chip.ops->del_tx_ts(priv, vif, tsid, peer);

	return 0;
}

static inline int sprd_set_mc_filter(struct sprd_priv *priv,
				     struct sprd_vif *vif,
				     u8 sub_type, u8 num, u8 *mac_addr)
{
	if (priv->chip.ops->set_mc_filter)
		return priv->chip.ops->set_mc_filter(priv, vif, sub_type, num,
						     mac_addr);

	return 0;
}

static inline int sprd_set_11v_feature_support(struct sprd_priv *priv,
					       struct sprd_vif *vif, u16 val)
{
	if (priv->chip.ops->set_11v_feature_support)
		return priv->chip.ops->set_11v_feature_support(priv, vif, val);

	return 0;
}

static inline int sprd_set_11v_sleep_mode(struct sprd_priv *priv,
					  struct sprd_vif *vif, u8 status,
					  u16 interval)
{
	if (priv->chip.ops->set_11v_sleep_mode)
		return priv->chip.ops->set_11v_sleep_mode(priv, vif, status,
							  interval);

	return 0;
}

static inline int sprd_xmit_data2cmd(struct sprd_priv *priv,
				     struct sk_buff *skb,
				     struct net_device *ndev)
{
	if (priv->chip.ops->xmit_data2cmd)
		return priv->chip.ops->xmit_data2cmd(skb, ndev);

	return 0;
}

static inline int sprd_set_random_mac(struct sprd_priv *priv,
				      struct sprd_vif *vif,
				      u8 random_mac_flag, u8 *addr)
{
	if (priv->chip.ops->set_random_mac)
		return priv->chip.ops->set_random_mac(priv, vif,
						      random_mac_flag, addr);

	return 0;
}

static inline int sprd_set_max_clients_allowed(struct sprd_priv *priv,
					       struct sprd_vif *vif,
					       int n_clients)
{
	if (priv->chip.ops->set_max_clients_allowed)
		return priv->chip.ops->set_max_clients_allowed(priv, vif,
							       n_clients);

	return 0;
}

static inline bool sprd_do_delay_work(struct sprd_priv *priv,
				      struct sprd_work *work)
{
	if (priv->chip.ops->do_delay_work)
		return priv->chip.ops->do_delay_work(work);

	return false;
}

static inline int sprd_notify_ip(struct sprd_priv *priv, struct sprd_vif *vif,
				 u8 ip_type, u8 *ip_addr)
{
	if (priv->chip.ops->notify_ip)
		return priv->chip.ops->notify_ip(priv, vif, ip_type, ip_addr);

	return 0;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static inline int sprd_set_vowifi(struct sprd_priv *priv,
				  struct net_device *ndev, void __user *data)
{
	if (priv->chip.ops->set_vowifi)
		return priv->chip.ops->set_vowifi(ndev, data);

	return 0;
}

static inline int sprd_set_miracast(struct sprd_priv *priv,
				struct net_device *ndev, void __user *data)
{
	if (priv->chip.ops->set_miracast)
		return priv->chip.ops->set_miracast(ndev, data);
	return 0;
}
#else
static inline int sprd_set_vowifi(struct sprd_priv *priv,
				  struct net_device *ndev, struct ifreq *ifr)
{
	if (priv->chip.ops->set_vowifi)
		return priv->chip.ops->set_vowifi(ndev, ifr);

	return 0;
}

static inline int sprd_set_miracast(struct sprd_priv *priv,
				struct net_device *ndev, struct ifreq *ifr)
{
	if (priv->chip.ops->set_miracast)
		return priv->chip.ops->set_miracast(ndev, ifr);
	return 0;
}
#endif

static inline int sprd_dump_survey(struct sprd_priv *priv, struct wiphy *wiphy,
				   struct net_device *ndev, int idx,
				   struct survey_info *s_info)
{
	if (priv->chip.ops->dump_survey)
		return priv->chip.ops->dump_survey(wiphy, ndev, idx, s_info);

	return 0;
}

static inline int sprd_npi_send_recv(struct sprd_priv *priv,
				     struct sprd_vif *vif, u8 *s_buf,
				     u16 s_len, u8 *r_buf, u16 *r_len)
{
	if (priv->chip.ops->npi_send_recv)
		return priv->chip.ops->npi_send_recv(priv, vif, s_buf, s_len,
						     r_buf, r_len);

	return 0;
}

static inline void sprd_qos_init_default_map(struct sprd_priv *priv)
{
	if (priv->chip.ops->qos_init_default_map)
		priv->chip.ops->qos_init_default_map();
}

static inline void sprd_qos_enable(struct sprd_priv *priv, int flag)
{
	if (priv->chip.ops->qos_enable)
		priv->chip.ops->qos_enable(flag);
}

static inline void sprd_qos_wmm_ac_init(struct sprd_priv *priv)
{
	if (priv->chip.ops->qos_wmm_ac_init)
		priv->chip.ops->qos_wmm_ac_init(priv);
}

static inline void sprd_qos_reset_wmmac_parameters(struct sprd_priv *priv)
{
	if (priv->chip.ops->qos_reset_wmmac_parameters)
		priv->chip.ops->qos_reset_wmmac_parameters(priv);
}

static inline void sprd_qos_reset_wmmac_ts_info(struct sprd_priv *priv)
{
	if (priv->chip.ops->qos_reset_wmmac_ts_info)
		priv->chip.ops->qos_reset_wmmac_ts_info();
}

static inline int sprd_scan(struct sprd_priv *priv, struct wiphy *wiphy,
			    struct cfg80211_scan_request *request)
{
	if (priv->chip.ops->scan)
		return priv->chip.ops->scan(wiphy, request);

	return 0;
}

static inline int sprd_sched_scan_start(struct sprd_priv *priv,
					struct wiphy *wiphy,
					struct net_device *ndev,
					struct cfg80211_sched_scan_request *request)
{
	if (priv->chip.ops->sched_scan_start)
		return priv->chip.ops->sched_scan_start(wiphy, ndev, request);

	return 0;
}

static inline int sprd_sched_scan_stop(struct sprd_priv *priv,
				       struct wiphy *wiphy,
				       struct net_device *ndev, u64 reqid)
{
	if (priv->chip.ops->sched_scan_stop)
		return priv->chip.ops->sched_scan_stop(wiphy, ndev, reqid);

	return 0;
}

static inline void sprd_scan_timeout(struct sprd_priv *priv,
				     struct timer_list *t)
{
	if (priv->chip.ops->scan_timeout)
		priv->chip.ops->scan_timeout(t);
}

static inline void sprd_tcp_ack_init(struct sprd_priv *priv)
{
	if (priv->chip.ops->tcp_ack_init)
		priv->chip.ops->tcp_ack_init(priv);
}

static inline void sprd_tcp_ack_deinit(struct sprd_priv *priv)
{
	if (priv->chip.ops->tcp_ack_deinit)
		priv->chip.ops->tcp_ack_deinit(priv);
}

static inline int sprd_vendor_init(struct sprd_priv *priv, struct wiphy *wiphy)
{
	if (priv->chip.ops->vendor_init)
		return priv->chip.ops->vendor_init(wiphy);

	return 0;
}

static inline int sprd_vendor_deinit(struct sprd_priv *priv,
				     struct wiphy *wiphy)

{
	if (priv->chip.ops->vendor_deinit)
		return priv->chip.ops->vendor_deinit(wiphy);

	return 0;
}

static inline int sprd_send_data(struct sprd_priv *priv, struct sprd_vif *vif,
				 struct sprd_msg *msg, struct sk_buff *skb,
				 u8 type, u8 offset, bool flag)
{
	if (priv->chip.ops->send_data)
		return priv->chip.ops->send_data(vif, msg, skb, type, offset,
						 flag);

	return 0;
}

static inline int sprd_send_data_offset(struct sprd_priv *priv)
{
	if (priv->chip.ops->send_data_offset)
		return priv->chip.ops->send_data_offset();

	return 0;
}

static inline void sprd_fc_add_share_credit(struct sprd_priv *priv,
					    struct sprd_vif *vif)
{
	if (priv->chip.ops->fc_add_share_credit)
		return priv->chip.ops->fc_add_share_credit(vif);
}

static inline void sprd_defrag_recover(struct sprd_priv *priv,
				       struct sprd_vif *vif)
{
	if (priv->chip.ops->defrag_recover)
		return priv->chip.ops->defrag_recover(vif);
}

static inline int sprd_set_sniffer(struct sprd_priv *priv,
				   struct net_device *ndev, struct ifreq *ifr)
{
	if (priv->chip.ops->set_sniffer)
		return priv->chip.ops->set_sniffer(ndev, ifr);

	return 0;
}

#endif
