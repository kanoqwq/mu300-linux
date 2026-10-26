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

#include "common.h"
#include "delay_work.h"

struct sprd_work *sprd_alloc_work(int len)
{
	struct sprd_work *sprd_work;
	int size = sizeof(*sprd_work) + len;

	sprd_work = kzalloc(size, GFP_ATOMIC);
	if (sprd_work) {
		INIT_LIST_HEAD(&sprd_work->list);
		sprd_work->len = len;
	}

	return sprd_work;
}
EXPORT_SYMBOL(sprd_alloc_work);

void sprd_queue_work(struct sprd_priv *priv, struct sprd_work *sprd_work)
{
	spin_lock_bh(&priv->work_lock);
	list_add_tail(&sprd_work->list, &priv->work_list);
	spin_unlock_bh(&priv->work_lock);

	if (!work_pending(&priv->work))
		queue_work(priv->common_workq, &priv->work);
}
EXPORT_SYMBOL(sprd_queue_work);

void sprd_cancel_work(struct sprd_priv *priv, struct sprd_vif *vif)
{
	struct sprd_work *sprd_work, *pos;

	spin_lock_bh(&priv->work_lock);
	list_for_each_entry_safe(sprd_work, pos, &priv->work_list, list) {
		if (vif == sprd_work->vif) {
			list_del(&sprd_work->list);
			kfree(sprd_work);
		}
	}
	spin_unlock_bh(&priv->work_lock);

	flush_work(&priv->work);
}
EXPORT_SYMBOL(sprd_cancel_work);

void sprd_clean_work(struct sprd_priv *priv)
{
	struct sprd_work *sprd_work, *pos;

	cancel_work_sync(&priv->work);

	spin_lock_bh(&priv->work_lock);
	list_for_each_entry_safe(sprd_work, pos, &priv->work_list, list) {
		list_del(&sprd_work->list);
		kfree(sprd_work);
	}
	spin_unlock_bh(&priv->work_lock);

	flush_workqueue(priv->common_workq);
}
EXPORT_SYMBOL(sprd_clean_work);
