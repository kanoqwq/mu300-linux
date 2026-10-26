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

#include <linux/jiffies.h>
#include <linux/spinlock.h>

#include "cfg80211.h"
#include "debug.h"
#include "iface.h"
#include "tcp_ack.h"
#include "common.h"

static unsigned int max_fw_tx_dscr;
static unsigned int tdls_threshold;
static unsigned int vo_ratio = 87;
static unsigned int vi_ratio = 90;
static unsigned int be_ratio = 81;
static unsigned int wmmac_ratio = 10;
static atomic_t tcp_ack_enable;
int sprd_dbg_level = L_WARN;

int get_max_fw_tx_dscr(void)
{
	return max_fw_tx_dscr;
}
EXPORT_SYMBOL(get_max_fw_tx_dscr);

int get_tdls_threshold(void)
{
	return tdls_threshold;
}
EXPORT_SYMBOL(get_tdls_threshold);

int get_vo_ratio(void)
{
	return vo_ratio;
}
EXPORT_SYMBOL(get_vo_ratio);

int get_vi_ratio(void)
{
	return vi_ratio;
}
EXPORT_SYMBOL(get_vi_ratio);

int get_be_ratio(void)
{
	return be_ratio;
}
EXPORT_SYMBOL(get_be_ratio);

int get_wmmac_ratio(void)
{
	return wmmac_ratio;
}
EXPORT_SYMBOL(get_wmmac_ratio);

int is_tcp_ack_enabled(void)
{
	return atomic_read(&tcp_ack_enable);
}
EXPORT_SYMBOL(is_tcp_ack_enabled);

void adjust_tcp_ack(char *buf, unsigned char offset)
{
	int enable = buf[offset] - '0';

	if (!enable)
		atomic_set(&tcp_ack_enable, 0);
	else
		atomic_set(&tcp_ack_enable, 1);
}
EXPORT_SYMBOL(adjust_tcp_ack);

void adjust_max_fw_tx_dscr(char *buf, unsigned char offset)
{
	unsigned int value = 0;
	unsigned int i = 0;
	unsigned int len = strlen(buf) - strlen("max_fw_tx_dscr=");

	for (i = 0; i < len; value *= 10, i++) {
		if (buf[offset + i] >= '0' && buf[offset + i] <= '9') {
			value += (buf[offset + i] - '0');
		} else {
			value /= 10;
			break;
		}
	}
	max_fw_tx_dscr = value;
	pr_err("%s, change max_fw_tx_dscr to %d\n", __func__, value);
}
EXPORT_SYMBOL(adjust_max_fw_tx_dscr);
#ifdef CONFIG_SPRD_WLAN_DEBUG

static struct sprd_debug *sprd_dbg;
static struct debug_ctrl dbg_ctrl;
static struct debug_time_stamp dbg_ts[MAX_DEBUG_TS_INDEX];
static struct debug_cnt dbg_cnt[MAX_DEBUG_CNT_INDEX];
static struct debug_record dbg_record[MAX_RECORD_NUM];

int sprd_get_debug_level(void)
{
	return sprd_dbg_level;
}
EXPORT_SYMBOL(sprd_get_debug_level);

static void debug_ctrl_init(void)
{
	spin_lock_init(&dbg_ctrl.debug_ctrl_lock);
	dbg_ctrl.start = false;
}

static bool debug_check_ctrl(void)
{
	bool value = false;

	spin_lock_bh(&dbg_ctrl.debug_ctrl_lock);
	if (dbg_ctrl.start)
		value = true;
	spin_unlock_bh(&dbg_ctrl.debug_ctrl_lock);

	return value;
}

static void debug_ts_show(struct seq_file *s, enum debug_ts_index index)
{
	unsigned int i = 0;
	unsigned int avr_time = 0, avr_cnt = 0;
	struct debug_time_stamp *ts = &dbg_ts[index];

	if (!debug_check_ctrl())
		return;

	seq_printf(s, "%s(us):", ts_index2str(index));
	for (i = 0; i < MAX_TS_NUM; i++) {
		seq_printf(s, " %d", ts->ts_record[i]);
		if (ts->ts_record[i] != 0) {
			avr_time += ts->ts_record[i];
			avr_cnt++;
		}
	}
	seq_printf(s, "\n%s average time(us): %d\n", ts_index2str(index),
		   avr_time / avr_cnt);
	seq_printf(s, "%s max time(us): %d\n", ts_index2str(index), ts->max_ts);
}

static void debug_cnt_show(struct seq_file *s, enum debug_cnt_index index)
{
	if (!debug_check_ctrl())
		return;

	seq_printf(s, "%s: %d\n", cnt_index2str(index), dbg_cnt[index].cnt);
}

static void debug_record_show(struct seq_file *s, enum debug_record_index index)
{
	struct debug_record *record = &dbg_record[index];
	unsigned int i = 0;

	if (!debug_check_ctrl())
		return;

	seq_printf(s, "%s:", record_index2str(index));
	for (i = 0; i < MAX_RECORD_NUM; i++)
		seq_printf(s, " %d", record->record[i]);

	seq_puts(s, "\n");
}

static void debug_adjust_debug_level(char *buf, unsigned char offset)
{
	int level = buf[offset] - '0';

	pr_err("input debug level: %d!\n", level);
	switch (level) {
	case L_ERR:
		sprd_dbg_level = L_ERR;
		break;
	case L_WARN:
		sprd_dbg_level = L_WARN;
		break;
	case L_INFO:
		sprd_dbg_level = L_INFO;
		break;
	case L_DBG:
		sprd_dbg_level = L_DBG;
		break;
	default:
		sprd_dbg_level = L_ERR;
		pr_err("input wrong debug level\n");
	}

	pr_err("set debug_level: %d\n", sprd_dbg_level);
}

static void debug_adjust_qos_ratio(char *buf, unsigned char offset)
{
	unsigned int qos_ratio =
	    (buf[offset + 3] - '0') * 10 + (buf[offset + 4] - '0');

	if (buf[offset] == 'v') {
		if (buf[offset + 1] == 'o')
			vo_ratio = qos_ratio;
		else if (buf[offset + 1] == 'i')
			vi_ratio = qos_ratio;
	} else if (buf[offset] == 'b' && buf[offset + 1] == 'e') {
		be_ratio = qos_ratio;
	} else if (buf[offset] == 'a' && buf[offset + 1] == 'm') {
		wmmac_ratio = qos_ratio;
	}

	pr_err("vo ratio:%u, vi ratio:%u, be ratio:%u, wmmac_ratio:%u\n",
	       vo_ratio, vi_ratio, be_ratio, wmmac_ratio);
}

static void debug_adjust_ts_cnt(char *buf, unsigned char offset)
{
	int level = buf[offset] - '0';

	spin_lock_bh(&dbg_ctrl.debug_ctrl_lock);
	if (level == 0) {
		dbg_ctrl.start = false;
		spin_unlock_bh(&dbg_ctrl.debug_ctrl_lock);
	} else {
		memset(dbg_ts, 0,
		       (MAX_DEBUG_TS_INDEX * sizeof(struct debug_time_stamp)));
		memset(dbg_cnt, 0,
		       (MAX_DEBUG_CNT_INDEX * sizeof(struct debug_cnt)));
		memset(dbg_record, 0,
		       (MAX_RECORD_NUM * sizeof(struct debug_record)));
		dbg_ctrl.start = true;
		spin_unlock_bh(&dbg_ctrl.debug_ctrl_lock);
	}
}

static void debug_adjust_tcpack_delay(char *buf, unsigned char offset)
{
#define MAX_LEN 2
	unsigned int cnt = 0;
	unsigned int i = 0;
	struct sprd_tcp_ack_manage *ack_m = NULL;
	struct sprd_priv *priv = NULL;

	for (i = 0; i < MAX_LEN; (cnt *= 10), i++) {
		if ((buf[offset + i] >= '0') && (buf[offset + i] <= '9')) {
			cnt += (buf[offset + i] - '0');
		} else {
			cnt /= 10;
			break;
		}
	}

	pr_err("cnt: %d\n", cnt);

	if (cnt >= 100)
		cnt = SPRD_TCP_ACK_DROP_CNT;

	if (sprd_dbg) {
		priv = container_of(sprd_dbg, struct sprd_priv, debug);
		ack_m = &priv->ack_m;

		atomic_set(&ack_m->max_drop_cnt, cnt);
		pr_err("drop time: %d, atomic drop time: %d\n", cnt,
		       atomic_read(&ack_m->max_drop_cnt));
	}
#undef MAX_LEN
}

static void debug_adjust_tcpack_delay_win(char *buf, unsigned char offset)
{
	unsigned int value = 0;
	unsigned int i = 0;
	unsigned int len = strlen(buf) - strlen("tcpack_delay_win=");
	struct sprd_tcp_ack_manage *ack_m = NULL;
	struct sprd_priv *priv = NULL;

	for (i = 0; i < len; (value *= 10), i++) {
		if ((buf[offset + i] >= '0') && (buf[offset + i] <= '9')) {
			value += (buf[offset + i] - '0');
		} else {
			value /= 10;
			break;
		}
	}
	if (sprd_dbg) {
		priv = container_of(sprd_dbg, struct sprd_priv, debug);
		ack_m = &priv->ack_m;
		ack_m->ack_winsize = value;
		pr_err("%s, change tcpack_delay_win to %dKB\n", __func__, value);
	}
}

static void debug_adjust_tdls_threshold(char *buf, unsigned char offset)
{
	unsigned int value = 0;
	unsigned int i = 0;
	unsigned int len = strlen(buf) - strlen("tdls_threshold=");

	for (i = 0; i < len; (value *= 10), i++) {
		if ((buf[offset + i] >= '0') && (buf[offset + i] <= '9')) {
			value += (buf[offset + i] - '0');
		} else {
			value /= 10;
			break;
		}
	}
	tdls_threshold = value;
	pr_err("%s, change tdls_threshold to %d\n", __func__, value);
}

static void debug_adjust_tsq_shift(char *buf, unsigned char offset)
{
	unsigned int value = 0;
	unsigned int i = 0;
	unsigned int len = strlen(buf) - strlen("tsq_shift=");

	for (i = 0; i < len; value *= 10, i++) {
		if (buf[offset + i] >= '0' && buf[offset + i] <= '9') {
			value += (buf[offset + i] - '0');
		} else {
			value /= 10;
			break;
		}
	}
	sprd_dbg->tsq_shift = value;
	pr_err("%s, change tsq_shift to %d\n", __func__, value);
}

static void debug_adjust_tcpack_th_in_mb(char *buf, unsigned char offset)
{
#define MAX_LEN 4
	unsigned int cnt = 0;
	unsigned int i = 0;

	for (i = 0; i < MAX_LEN; cnt *= 10, i++) {
		if (buf[offset + i] >= '0' && buf[offset + i] <= '9') {
			cnt += (buf[offset + i] - '0');
		} else {
			cnt /= 10;
			break;
		}
	}

	if (cnt < 0 || cnt > 9999)
		cnt = DROPACK_TP_TH_IN_M;
	sprd_dbg->tcpack_delay_th_in_mb = cnt;
	pr_info("tcpack_delay_th_in_mb: %d\n", sprd_dbg->tcpack_delay_th_in_mb);
#undef MAX_LEN
}

static void debug_adjust_tcpack_time_in_ms(char *buf, unsigned char offset)
{
#define MAX_LEN 4
	unsigned int cnt = 0;
	unsigned int i = 0;

	for (i = 0; i < MAX_LEN; cnt *= 10, i++) {
		if (buf[offset + i] >= '0' && buf[offset + i] <= '9') {
			cnt += (buf[offset + i] - '0');
		} else {
			cnt /= 10;
			break;
		}
	}

	if (cnt < 0 || cnt > 9999)
		cnt = RX_TP_COUNT_IN_MS;
	sprd_dbg->tcpack_time_in_ms = cnt;
	pr_info("tcpack_time_in_ms: %d\n", sprd_dbg->tcpack_time_in_ms);
#undef MAX_LEN
}

static struct debug_info_s dbg_info[] = {
	{adjust_tcp_ack, "tcpack_delay_en="},
	{debug_adjust_tcpack_delay, "tcpack_delay_cnt="},
	{debug_adjust_tcpack_delay_win, "tcpack_delay_win="},
	{debug_adjust_tcpack_th_in_mb, "tcpack_delay_th_in_mb="},
	{debug_adjust_tcpack_time_in_ms, "tcpack_time_in_ms="},
	{debug_adjust_debug_level, "debug_level="},
	{debug_adjust_qos_ratio, "qos_ratio:"},
	{debug_adjust_ts_cnt, "debug_info="},
	{debug_adjust_tdls_threshold, "tdls_threshold="},
	{debug_adjust_tsq_shift, "tsq_shift="},
	{adjust_max_fw_tx_dscr, "max_fw_tx_dscr="},
};

static ssize_t intf_read(struct file *file, char __user *user_buf,
			 size_t count, loff_t *ppos)
{
	size_t ret = 0;
	unsigned int buflen, len;
	unsigned char *buf;

	buflen = SPRD_SDIO_DEBUG_BUFLEN;
	buf = kzalloc(buflen, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	len = 0;
	len += scnprintf(buf, buflen, "Log level: %d.\n", sprd_dbg_level);
	if (len > buflen)
		len = buflen;

	ret = simple_read_from_buffer(user_buf, count, ppos, buf, len);
	kfree(buf);

	return ret;
}

static ssize_t intf_write(struct file *file, const char __user *__user_buf,
			  size_t count, loff_t *ppos)
{
	char buf[30];
	int type = 0;
	int debug_size = sizeof(dbg_info) / sizeof(struct debug_info_s);

	if (!count || count >= sizeof(buf)) {
		pr_err("write len too long:%zu >= %zu\n", count, sizeof(buf));
		return -EINVAL;
	}
	if (copy_from_user(buf, __user_buf, count))
		return -EFAULT;
	buf[count] = '\0';
	pr_err("write info:%s\n", buf);
	for (type = 0; type < debug_size; type++)
		if (!strncmp(dbg_info[type].str, buf,
			     strlen(dbg_info[type].str))) {
			pr_err("write info:type %d\n", type);
			dbg_info[type].func(buf, strlen(dbg_info[type].str));
			break;
		}

	return count;
}

static const struct file_operations intf_debug_fops = {
	.read = intf_read,
	.write = intf_write,
	.open = simple_open,
	.owner = THIS_MODULE,
	.llseek = default_llseek
};

static int debug_show_txrx(struct seq_file *s, void *p)
{
	unsigned int i = 0;

	for (i = 0; i < MAX_DEBUG_CNT_INDEX; i++)
		debug_cnt_show(s, i);

	for (i = 0; i < MAX_DEBUG_TS_INDEX; i++)
		debug_ts_show(s, i);

	for (i = 0; i < MAX_DEBUG_RECORD_INDEX; i++)
		debug_record_show(s, i);

	return 0;
}

static int debug_open_txrx(struct inode *inode, struct file *file)
{
	return single_open(file, debug_show_txrx, inode->i_private);
}

static ssize_t debug_write_txrx(struct file *file,
				const char __user *__user_buf,
				size_t count, loff_t *ppos)
{
	char buf[20] = "debug_info=";
	unsigned char len = strlen(buf);

	if (!count || (count + len) >= sizeof(buf)) {
		pr_err("write len too long:%zu >= %zu\n", count, sizeof(buf));
		return -EINVAL;
	}

	if (copy_from_user((buf + len), __user_buf, count))
		return -EFAULT;

	buf[count + len] = '\0';
	pr_err("write info:%s\n", buf);

	debug_adjust_ts_cnt(buf, len);

	return count;
}

static const struct file_operations txrx_debug_fops = {
	.owner = THIS_MODULE,
	.open = debug_open_txrx,
	.read = seq_read,
	.write = debug_write_txrx,
	.llseek = seq_lseek,
	.release = single_release,
};

void sprd_debug_ts_enter(enum debug_ts_index index)
{
	if (!debug_check_ctrl())
		return;

	dbg_ts[index].ts_enter = jiffies;
}
EXPORT_SYMBOL(sprd_debug_ts_enter);

void sprd_debug_ts_leave(enum debug_ts_index index)
{
	struct debug_time_stamp *ts = &dbg_ts[index];

	if (!debug_check_ctrl() || !ts->ts_enter)
		return;

	ts->ts_record[ts->pos] = jiffies_to_usecs(jiffies - ts->ts_enter);

	if (ts->ts_record[ts->pos] > ts->max_ts)
		ts->max_ts = ts->ts_record[ts->pos];

	(ts->pos < (MAX_TS_NUM - 1)) ? ts->pos++ : (ts->pos = 0);
}
EXPORT_SYMBOL(sprd_debug_ts_leave);

void sprd_debug_cnt_inc(enum debug_cnt_index index)
{
	if (!debug_check_ctrl())
		return;

	dbg_cnt[index].cnt++;
}
EXPORT_SYMBOL(sprd_debug_cnt_inc);

void sprd_debug_cnt_dec(enum debug_cnt_index index)
{
	if (!debug_check_ctrl())
		return;

	dbg_cnt[index].cnt--;
}
EXPORT_SYMBOL(sprd_debug_cnt_dec);

void sprd_debug_record_add(enum debug_record_index index, int num)
{
	struct debug_record *record = &dbg_record[index];

	if (!debug_check_ctrl())
		return;

	record->record[record->pos] = num;
	(record->pos < (MAX_RECORD_NUM - 1)) ?
	    record->pos++ : (record->pos = 0);
}
EXPORT_SYMBOL(sprd_debug_record_add);

void sprd_debug_init(struct sprd_debug *dbg)
{
	sprd_dbg = dbg;
	sprd_dbg_level = L_INFO;
	/* create debugfs */
	dbg->dir = debugfs_create_dir("sprd_wlan", NULL);
	if (IS_ERR(dbg->dir)) {
		pr_err("%s, create dir fail!\n", __func__);
		dbg->dir = NULL;
		return;
	}

	if (!debugfs_create_file("log_level", 0444,
				 dbg->dir, NULL, &intf_debug_fops))
		pr_err("%s, create file fail!\n", __func__);

	if (!debugfs_create_file("txrx_dbg", 0444,
				 dbg->dir, NULL, &txrx_debug_fops))
		pr_err("%s, %d, create_file fail!\n", __func__, __LINE__);
	else
		debug_ctrl_init();
}
EXPORT_SYMBOL(sprd_debug_init);

void sprd_debug_deinit(struct sprd_debug *dbg)
{
	/* remove debugfs */
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	debugfs_remove(dbg->dir);
#else
	debugfs_remove_recursive(dbg->dir);
#endif
}
EXPORT_SYMBOL(sprd_debug_deinit);

#endif /* CONFIG_SPRD_WLAN_DEBUG */
