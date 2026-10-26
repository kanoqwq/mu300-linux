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
#include "common/acs.h"
#include "common/common.h"

#define SPRD_ACS_SCAN_TIME		20

int sc2355_dump_survey(struct wiphy *wiphy, struct net_device *ndev,
		       int idx, struct survey_info *s_info)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_api_version_t *api = (&vif->priv->sync_api)->api_array;
	struct survey_info_node *info = NULL;
	struct survey_info_new_node *info_new = NULL;
	struct bssid_node *bssid = NULL, *pos = NULL;
	static int survey_count;
	int survey_cnt = 0;
	u8 fw_ver = 0;
	int ret = 0;
	u8 drv_ver = 0;
	int err = 0;

	fw_ver = (api + CMD_SCAN)->fw_version;
	if (vif->priv->hif.hw_type == SPRD_HW_SC2355_PCIE) {
		drv_ver = (api + CMD_SCAN)->drv_version;
		fw_ver = min(fw_ver, drv_ver);
	}
	if (fw_ver == 1) {
		if (vif->mode != SPRD_MODE_AP) {
			netdev_dbg(vif->ndev, "Not AP mode, exit %s!\n",
				   __func__);
			err = -ENOENT;
			goto out;
		}

		if (!list_empty(&vif->survey_info_list)) {
			info = list_first_entry(&vif->survey_info_list,
						struct survey_info_node,
						survey_list);
			list_del(&info->survey_list);

			if (info->channel) {
				s_info->channel = info->channel;
				s_info->noise = info->noise;
				s_info->time = SPRD_ACS_SCAN_TIME;
				s_info->time_busy = info->cca_busy_time;
				s_info->filled = (SURVEY_INFO_NOISE_DBM |
						  SURVEY_INFO_TIME |
						  SURVEY_INFO_TIME_BUSY);

				survey_count++;
			}

			list_for_each_entry_safe(bssid, pos, &info->bssid_list,
						 list) {
				list_del(&bssid->list);
				kfree(bssid);
				bssid = NULL;
			}

			kfree(info);
		} else {
			/* There are no more survey info in list */
			err = -ENOENT;
			netdev_info(vif->ndev, "%s report %d surveys\n",
				    __func__, survey_count);
			survey_count = 0;
		}

out:
		return err;
	}

	/*
	 * MU300 (#140): cfg80211 polls dump_survey until it returns -ENOENT, so every line below is per-poll trace of
	 * the survey data (or of an empty list), not an error. The vendor printed them all with netdev_err, which
	 * put two lines in the log every few seconds while anything read the survey; netdev_dbg like the 5.4 build.
	 */
	netdev_dbg(vif->ndev, "%s, idx %d\n", __func__, idx);
	if (!list_empty(&vif->survey_info_list)) {
		info_new = list_first_entry(&vif->survey_info_list,
					    struct survey_info_new_node,
					    survey_list);
		list_del(&info_new->survey_list);
		if (info_new->channel) {
			s_info->channel = info_new->channel;
			s_info->noise = info_new->noise;
			if (vif->priv->hif.hw_type ==
			    SPRD_HW_SC2355_PCIE) {
				if (vif->acs_scan_index < 6) {
					s_info->time = info_new->time;
					s_info->time_busy =
					    info_new->cca_busy_time;
					s_info->filled =
					    (SURVEY_INFO_NOISE_DBM |
					     SURVEY_INFO_TIME |
					     SURVEY_INFO_TIME_BUSY);
					netdev_dbg(vif->ndev,
						   "%s, noise:%d, time:%llu, time_busy:%llu, center_freq:%d\n",
						   __func__,
						   s_info->noise,
						   s_info->time,
						   s_info->time_busy,
						   s_info->channel->center_freq);
				} else {
					s_info->filled =
					    SURVEY_INFO_NOISE_DBM;
					netdev_dbg(vif->ndev,
						   "%s, noise:%d,center_freq:%d\n",
						   __func__,
						   s_info->noise,
						   s_info->channel->center_freq);
				}
			} else {
				s_info->time = info_new->time;
				s_info->time_busy = info_new->cca_busy_time;

				s_info->filled =
				    (SURVEY_INFO_NOISE_DBM |
				     SURVEY_INFO_TIME |
				     SURVEY_INFO_TIME_BUSY |
				     SURVEY_INFO_TIME_EXT_BUSY);
			}
		}
		netdev_dbg(vif->ndev, "%s, time %llu\n", __func__,
			   s_info->time);
		survey_cnt++;
		kfree(info_new);

	} else {
		netdev_dbg(vif->ndev, "%s, survey_cnt %d\n", __func__,
			   survey_cnt);
		ret = -ENOENT;
	}

	return ret;
}
