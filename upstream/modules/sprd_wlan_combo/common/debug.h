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

#ifndef __DEBUG_H__
#define __DEBUG_H__

#include <linux/spinlock.h>
#include <linux/types.h>
#include <uapi/linux/if_ether.h>

/* will not drop TCP ACK if TCPRX tp under this Mb level */
#define DROPACK_TP_TH_IN_M	40
/* count RX TP timer in ms */
#define RX_TP_COUNT_IN_MS	500

#define MAX_TS_NUM 20

#ifdef pr_fmt
#undef pr_fmt
#endif
#define pr_fmt(fmt) "sprd-wlan: " fmt

enum {
	L_ERR = 0,		/*LEVEL_ERR */
	L_WARN,			/*LEVEL_WARNING */
	L_INFO,			/*LEVEL_INFO */
	L_DBG,			/*LEVEL_DEBUG */
};

enum debug_ts_index {
	RX_SDIO_PORT,
	MAX_DEBUG_TS_INDEX,
};

enum debug_cnt_index {
	REORDER_TIMEOUT_CNT,
	MAX_DEBUG_CNT_INDEX,
};

enum debug_record_index {
	TX_CREDIT_RECORD,
	TX_CREDIT_TIME_DIFF,
	TX_CREDIT_PER_ADD,
	TX_CREDIT_ADD,
	MAX_DEBUG_RECORD_INDEX,
};

struct sprd_debug {
	struct dentry *dir;

	u8 tsq_shift;
	unsigned int tcpack_delay_th_in_mb;
	unsigned int tcpack_time_in_ms;
};

struct debug_ctrl {
	/* protect debug_ctrl_lock */
	spinlock_t debug_ctrl_lock;
	bool start;
};

struct debug_cnt {
	int cnt;
};

struct debug_time_stamp {
	unsigned long ts_enter;
	unsigned int pos;
	unsigned int ts_record[MAX_TS_NUM];
	unsigned int max_ts;
};

struct debug_info_s {
	void (*func)(char *buf, unsigned char offset);
	char str[30];
};

struct debug_record {
	unsigned int pos;
	int record[MAX_TS_NUM];
};

static inline char *ts_index2str(u8 index)
{
	switch (index) {
	case RX_SDIO_PORT:
		return "RX_SDIO_PORT";
	default:
		return "UNKNOWN_DEBUG_TS_INDEX";
	}
}

static inline char *record_index2str(u8 index)
{
	switch (index) {
	case TX_CREDIT_RECORD:
		return "TX_CREDIT_RECORD";
	case TX_CREDIT_TIME_DIFF:
		return "TX_CREDIT_TIME_DIFF";
	case TX_CREDIT_PER_ADD:
		return "TX_CREDIT_PER_ADD";
	case TX_CREDIT_ADD:
		return "TX_CREDIT_ADD";
	default:
		return "UNKNOWN_DEBUG_RECORD_INDEX";
	}
}

static inline char *cnt_index2str(u8 index)
{
	switch (index) {
	case REORDER_TIMEOUT_CNT:
		return "REORDER_TIMEOUT_CNT";
	default:
		return "UNKNOWN_DEBUG_CNT_INDEX";
	}
}

extern int sprd_dbg_level;

#define wl_debug(fmt, args...) \
	do { \
		if (sprd_dbg_level >= L_DBG) \
			pr_info(fmt, ##args); \
	} while (0)

#define wl_err(fmt, args...) \
	do { \
		if (sprd_dbg_level >= L_ERR) \
			pr_err(fmt, ##args); \
	} while (0)

#define wl_warn(fmt, args...) \
	do { \
		if (sprd_dbg_level >= L_WARN) \
			pr_err(fmt, ##args); \
	} while (0)

#define wl_info(fmt, args...) \
	do { \
		if (sprd_dbg_level >= L_INFO) \
			pr_info(fmt, ##args); \
	} while (0)

int get_max_fw_tx_dscr(void);
int get_tdls_threshold(void);
int get_vo_ratio(void);
int get_vi_ratio(void);
int get_be_ratio(void);
int get_wmmac_ratio(void);
int is_tcp_ack_enabled(void);
void adjust_tcp_ack(char *buf, unsigned char offset);
void adjust_max_fw_tx_dscr(char *buf, unsigned char offset);

#ifdef CONFIG_SPRD_WLAN_DEBUG

#define MAX_RECORD_NUM 20
#define SPRD_SDIO_DEBUG_BUFLEN 128

struct seq_file;

int sprd_get_debug_level(void);

void sprd_debug_ts_enter(enum debug_ts_index index);
void sprd_debug_ts_leave(enum debug_ts_index index);

void sprd_debug_cnt_inc(enum debug_cnt_index index);
void sprd_debug_cnt_dec(enum debug_cnt_index index);

void sprd_debug_record_add(enum debug_record_index index, int record);

void sprd_debug_init(struct sprd_debug *dbg);
void sprd_debug_deinit(struct sprd_debug *dbg);
#else
#define sprd_debug_ts_enter(index)		do {} while (0)
#define sprd_debug_ts_leave(index)		do {} while (0)
#define sprd_debug_cnt_inc(index)		do {} while (0)
#define sprd_debug_cnt_dec(index)		do {} while (0)
#define sprd_debug_record_add(index, num)	do {} while (0)
#define sprd_debug_init(dbg)			do {} while (0)
#define sprd_debug_deinit(dbg)			do {} while (0)
static inline int sprd_get_debug_level(void)
{
	return sprd_dbg_level;
}
#endif /* CONFIG_SPRD_WLAN_DEBUG */

#endif
