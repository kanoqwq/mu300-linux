/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Copyright 2021-2023 Unisoc(Shanghai) Technologies Co.Ltd
 *
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#ifndef _SLOG_BRIDGE_H
#define _SLOG_BRIDGE_H

#include <linux/delay.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/sched.h>
#include <linux/sipc.h>
#include <linux/slab.h>
#include <linux/workqueue.h>
#include <uapi/linux/sched/types.h>

#include "sprd_actions_queue.h"
#include "sblock.h"
#include "sipc_priv.h"

#define SLOG_POLL_ALL	(0xffff)
#define MAX_RX_BLOCK	8

/* actions */
enum {
	SLOG_READ_DATA_ACTION = 0,
	SLOG_CAN_WRITE_ACTION
};

struct slog_bridge;

/* sblock callback  struct */
struct slog_cb {
	u32			index;
	struct slog_bridge	*sb;
	struct sblock_mgr	*sblock;
};

/* sblock bridge action struct */
struct slog_action {
	void	*actions;
	char	name[20];
	struct slog_bridge	*sb;
};

/* slog_bridge struct */
struct slog_bridge {
	u8	dst_tx;
	u8	ch_tx;
	u8	dst_rx[MAX_RX_BLOCK];
	u8	ch_rx[MAX_RX_BLOCK];

	u32	priority;
	u32	rx_cnt;
	u32	rx_block_max_size;
	u32	rx_blks_cnt;/* tx bigger, 1 tx blocks can read rx blocks cnt. */
	u32 send_cnt;
	u32 total_size;
	u32 write_failed_cnt;

	bool	tx_is_usb;
	bool	wait_log_enable;

	struct timer_list	flush_timer; /* flush the smem log to usb. */

	struct sblock_mgr	*sblock_rx[MAX_RX_BLOCK];
	struct sblock_mgr	*sblock_tx;

	struct slog_cb		log_cb_tx;
	struct slog_cb		log_cb_rx[MAX_RX_BLOCK];

	struct slog_action	log_action;

	struct device *dev;
	struct work_struct register_work;
	struct list_head pool_list;
	struct list_head send_list;
	spinlock_t	list_lock;
};

struct block_node {
	void	*vaddr;
	void	*addr;
	u32	length;
	u32	send_len;
	u8	dst;
	u8	channel;
	struct	list_head list;
};

#if IS_ENABLED(CONFIG_USB_F_VSERIAL_BYPASS_USER)
extern ssize_t vser_pass_user_write(char *buf, size_t count);
extern void kernel_vser_register_callback(void *function, void *p);
extern void kernel_vser_set_pass_mode(bool pass);
#else
static ssize_t vser_pass_user_write(char *buf, size_t count) { return count; }
static void kernel_vser_register_callback(void *function, void *p) {}
static void kernel_vser_set_pass_mode(bool pass) {}
#endif

#endif //_SLOG_BRIDGE_H

