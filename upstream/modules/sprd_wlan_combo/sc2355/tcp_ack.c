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

#include <net/tcp.h>

#include "common/chip_ops.h"
#include "common/common.h"
#include "common/msg.h"
#include "common/tcp_ack.h"

#define SPRD_U32_BEFORE(a, b)	((__s32)((__u32)(a) - (__u32)(b)) <= 0)

#define MAX_TCP_ACK	200
/*min window size in KB, it's 256KB*/
#define MIN_WIN		256
#define SIZE_KB		1024

static void tcp_ack_timeout(struct timer_list *t)
{
	struct tcp_ack_info *ack_info = from_timer(ack_info, t, timer);
	struct sprd_msg *msg;
	struct sprd_tcp_ack_manage *ack_m = NULL;

	ack_m = container_of(ack_info, struct sprd_tcp_ack_manage,
			     ack_info[ack_info->ack_info_num]);

	write_seqlock_bh(&ack_info->seqlock);
	msg = ack_info->msg;
	if (ack_info->busy && msg && !ack_info->in_send_msg) {
		ack_info->msg = NULL;
		ack_info->drop_cnt = 0;
		ack_info->in_send_msg = msg;
		write_sequnlock_bh(&ack_info->seqlock);
		if (sprd_chip_tx(&ack_m->priv->chip, msg))
			pr_err("%s TX data error\n", __func__);
		return;
	}
	write_sequnlock_bh(&ack_info->seqlock);
}

static int tcp_ack_check_quick(unsigned char *buf, struct tcp_ack_msg *ack_msg)
{
	int ip_hdr_len;
	unsigned char *temp;
	struct ethhdr *ethhdr;
	struct iphdr *iphdr;
	struct tcphdr *tcphdr;

	ethhdr = (struct ethhdr *)buf;
	if (ethhdr->h_proto != htons(ETH_P_IP))
		return 0;
	iphdr = (struct iphdr *)(ethhdr + 1);
	if (iphdr->version != 4 || iphdr->protocol != IPPROTO_TCP)
		return 0;
	ip_hdr_len = iphdr->ihl * 4;
	temp = (unsigned char *)(iphdr) + ip_hdr_len;
	tcphdr = (struct tcphdr *)temp;
	/* TCP_FLAG_ACK */
	if (!(temp[13] & 0x10))
		return 0;

	if (temp[13] & 0x8) {
		ack_msg->saddr = iphdr->daddr;
		ack_msg->daddr = iphdr->saddr;
		ack_msg->source = tcphdr->dest;
		ack_msg->dest = tcphdr->source;
		ack_msg->seq = ntohl(tcphdr->seq);
		return 1;
	}

	return 0;
}

static int tcp_ack_is_drop(struct tcphdr *tcphdr, int tcp_tot_len,
			   unsigned short *win_scale)
{
	int drop = 1;
	int len = tcphdr->doff * 4;
	unsigned char *ptr;

	/* tcp.doff is only 4 bits,max 15 * 4 bytes */
	if (len < sizeof(struct tcphdr) || len > 15*4) {
		drop = 0;
		return drop;
	}
	if (tcp_tot_len > len) {
		drop = 0;
	} else {
		len -= sizeof(struct tcphdr);
		ptr = (unsigned char *)(tcphdr + 1);

		while ((len > 0) && drop) {
			int opcode = *ptr++;
			int opsize;

			switch (opcode) {
			case TCPOPT_EOL:
				break;
			case TCPOPT_NOP:
				len--;
				continue;
			default:
				opsize = *ptr++;
				if (opsize < 2)
					break;
				if (opsize > len)
					break;

				switch (opcode) {
				case TCPOPT_TIMESTAMP:
					break;
				case TCPOPT_WINDOW:
					if (*ptr < 15)
						*win_scale = (1 << (*ptr));
					break;
				default:
					drop = 2;
				}

				ptr += opsize - 2;
				len -= opsize;
			}
		}
	}

	return drop;
}

/* flag:0 for not tcp ack
 *	1 for ack which can be drop
 *	2 for other ack whith more info
 */
static int tcp_ack_check(unsigned char *buf, struct tcp_ack_msg *ack_msg,
			 unsigned short *win_scale)
{
	int ret;
	int ip_hdr_len;
	int tcp_tot_len;
	unsigned char *temp;
	struct ethhdr *ethhdr;
	struct iphdr *iphdr;
	struct tcphdr *tcphdr;

	ethhdr = (struct ethhdr *)buf;
	if (ethhdr->h_proto != htons(ETH_P_IP))
		return 0;
	iphdr = (struct iphdr *)(ethhdr + 1);
	if (iphdr->version != 4 || iphdr->protocol != IPPROTO_TCP)
		return 0;
	ip_hdr_len = iphdr->ihl * 4;
	temp = (unsigned char *)(iphdr) + ip_hdr_len;
	tcphdr = (struct tcphdr *)temp;
	/* TCP_FLAG_ACK */
	if (!(temp[13] & 0x10))
		return 0;

	tcp_tot_len = ntohs(iphdr->tot_len) - ip_hdr_len;
	ret = tcp_ack_is_drop(tcphdr, tcp_tot_len, win_scale);

	if (ret > 0) {
		ack_msg->saddr = iphdr->saddr;
		ack_msg->daddr = iphdr->daddr;
		ack_msg->source = tcphdr->source;
		ack_msg->dest = tcphdr->dest;
		ack_msg->seq = ntohl(tcphdr->ack_seq);
		ack_msg->win = ntohs(tcphdr->window);
	}

	return ret;
}

/* return val: -1 for not match, others for match */
static int tcp_ack_match(struct sprd_tcp_ack_manage *ack_m,
			 struct tcp_ack_msg *ack_msg)
{
	int i, ret = -1;
	unsigned int start;
	struct tcp_ack_info *ack_info;
	struct tcp_ack_msg *ack;

	for (i = 0; ((ret < 0) && (i < SPRD_TCP_ACK_NUM)); i++) {
		ack_info = &ack_m->ack_info[i];
		do {
			start = read_seqbegin(&ack_info->seqlock);
			ret = -1;

			ack = &ack_info->ack_msg;
			if (ack_info->busy &&
			    ack->dest == ack_msg->dest &&
			    ack->source == ack_msg->source &&
			    ack->saddr == ack_msg->saddr &&
			    ack->daddr == ack_msg->daddr)
				ret = i;
		} while (read_seqretry(&ack_info->seqlock, start));
	}

	return ret;
}

static void tcp_ack_update(struct sprd_tcp_ack_manage *ack_m)
{
	int i;
	struct tcp_ack_info *ack_info;

	if (time_after(jiffies, ack_m->last_time + ack_m->timeout)) {
		spin_lock_bh(&ack_m->lock);
		ack_m->last_time = jiffies;
		for (i = SPRD_TCP_ACK_NUM - 1; i >= 0; i--) {
			ack_info = &ack_m->ack_info[i];
			write_seqlock_bh(&ack_info->seqlock);
			if (ack_info->busy &&
			    time_after(jiffies, ack_info->last_time +
				       ack_info->timeout)) {
				ack_m->free_index = i;
				ack_m->max_num--;
				ack_info->busy = 0;
			}
			write_sequnlock_bh(&ack_info->seqlock);
		}
		spin_unlock_bh(&ack_m->lock);
	}
}

/* return val: -1 for no index, others for index */
static int tcp_ack_alloc_index(struct sprd_tcp_ack_manage *ack_m)
{
	int i, ret = -1;
	struct tcp_ack_info *ack_info;
	unsigned int start;

	spin_lock_bh(&ack_m->lock);
	if (ack_m->max_num == SPRD_TCP_ACK_NUM) {
		spin_unlock_bh(&ack_m->lock);
		return -1;
	}

	if (ack_m->free_index >= 0) {
		i = ack_m->free_index;
		ack_m->free_index = -1;
		ack_m->max_num++;
		spin_unlock_bh(&ack_m->lock);
		return i;
	}

	for (i = 0; ((ret < 0) && (i < SPRD_TCP_ACK_NUM)); i++) {
		ack_info = &ack_m->ack_info[i];
		do {
			start = read_seqbegin(&ack_info->seqlock);
			ret = -1;
			if (!ack_info->busy) {
				ack_m->free_index = -1;
				ack_m->max_num++;
				ret = i;
			}
		} while (read_seqretry(&ack_info->seqlock, start));
	}
	spin_unlock_bh(&ack_m->lock);

	return ret;
}

/* return val: 0 for handle tx, 1 for not handle tx */
static int tcp_ack_handle(struct sprd_msg *new_msg,
			  struct sprd_tcp_ack_manage *ack_m,
			  struct tcp_ack_info *ack_info,
			  struct tcp_ack_msg *ack_msg, int type)
{
	int quick_ack = 0;
	struct tcp_ack_msg *ack;
	int ret = 0;
	struct sprd_msg *drop_msg = NULL;

	write_seqlock_bh(&ack_info->seqlock);

	ack_info->last_time = jiffies;
	ack = &ack_info->ack_msg;

	if (type == 2) {
		if (SPRD_U32_BEFORE(ack->seq, ack_msg->seq)) {
			ack->seq = ack_msg->seq;
			if (ack_info->psh_flag &&
			    !SPRD_U32_BEFORE(ack_msg->seq, ack_info->psh_seq)) {
				ack_info->psh_flag = 0;
			}

			if (ack_info->msg) {
				drop_msg = ack_info->msg;
				ack_info->msg = NULL;
				del_timer(&ack_info->timer);
			}

			ack_info->in_send_msg = NULL;
			ack_info->drop_cnt = atomic_read(&ack_m->max_drop_cnt);
		} else {
			/* MU300 (#140): a duplicate or reordered ACK (every TCP fast-retransmit sends three), skipped by design */
			pr_debug_ratelimited("%s before abnormal ack: %d, %d\n",
					     __func__, ack->seq, ack_msg->seq);
			drop_msg = new_msg;
			ret = 1;
		}
	} else if (SPRD_U32_BEFORE(ack->seq, ack_msg->seq)) {
		if (ack_info->msg) {
			drop_msg = ack_info->msg;
			ack_info->msg = NULL;
		}

		if (ack_info->psh_flag &&
		    !SPRD_U32_BEFORE(ack_msg->seq, ack_info->psh_seq)) {
			ack_info->psh_flag = 0;
			quick_ack = 1;
		} else {
			ack_info->drop_cnt++;
		}

		ack->seq = ack_msg->seq;

		if (quick_ack || (!ack_info->in_send_msg &&
				  ack_info->drop_cnt >=
				  atomic_read(&ack_m->max_drop_cnt))) {
			ack_info->drop_cnt = 0;
			ack_info->in_send_msg = new_msg;
			del_timer(&ack_info->timer);
		} else {
			ret = 1;
			ack_info->msg = new_msg;
			if (!timer_pending(&ack_info->timer))
				mod_timer(&ack_info->timer,
					  (jiffies + msecs_to_jiffies(5)));
		}
	} else {
		pr_err("%s before ack: %d, %d\n",
		       __func__, ack->seq, ack_msg->seq);
		drop_msg = new_msg;
		ret = 1;
	}

	write_sequnlock_bh(&ack_info->seqlock);

	if (drop_msg)
		sprd_chip_drop_tcp_msg(&ack_m->priv->chip, drop_msg);

	return ret;
}

struct sprd_msg *tcp_ack_delay(struct sprd_tcp_ack_manage *ack_m)
{
	struct sprd_msg *drop_msg = NULL;
	int i;

	if (!is_tcp_ack_enabled()) {
		for (i = 0; i < SPRD_TCP_ACK_NUM; i++) {
			drop_msg = NULL;

			write_seqlock_bh(&ack_m->ack_info[i].seqlock);
			drop_msg = ack_m->ack_info[i].msg;
			ack_m->ack_info[i].msg = NULL;
			del_timer(&ack_m->ack_info[i].timer);
			write_sequnlock_bh(&ack_m->ack_info[i].seqlock);
		}
	}

	return drop_msg;
}

void sc2355_tcp_ack_filter_rx(struct sprd_priv *priv, unsigned char *buf,
			      unsigned int plen)
{
	int index;
	struct tcp_ack_msg ack_msg;
	struct tcp_ack_info *ack_info;
	struct sprd_tcp_ack_manage *ack_m = &priv->ack_m;

	if (!atomic_read(&ack_m->enable))
		return;

	if (plen > MAX_TCP_ACK || !tcp_ack_check_quick(buf, &ack_msg))
		return;

	index = tcp_ack_match(ack_m, &ack_msg);
	if (index >= 0) {
		ack_info = ack_m->ack_info + index;
		write_seqlock_bh(&ack_info->seqlock);
		ack_info->psh_flag = 1;
		ack_info->psh_seq = ack_msg.seq;
		write_sequnlock_bh(&ack_info->seqlock);
	}
}

/* return val: 0 for not filter, 1 for filter */
int sc2355_tcp_ack_filter_send(struct sprd_priv *priv, struct sprd_msg *msg,
			       unsigned char *buf, unsigned int plen)
{
	int ret = 0;
	int index, drop;
	unsigned short win_scale = 0;
	unsigned int win = 0;
	struct tcp_ack_msg ack_msg;
	struct tcp_ack_msg *ack;
	struct tcp_ack_info *ack_info;
	struct sprd_tcp_ack_manage *ack_m = &priv->ack_m;

	if (!atomic_read(&ack_m->enable))
		return 0;

	if (plen > MAX_TCP_ACK)
		return 0;

	tcp_ack_update(ack_m);
	drop = tcp_ack_check(buf, &ack_msg, &win_scale);
	if (!drop && !win_scale)
		return 0;

	index = tcp_ack_match(ack_m, &ack_msg);
	if (index >= 0) {
		ack_info = ack_m->ack_info + index;
		if ((win_scale) && ack_info->win_scale != win_scale) {
			write_seqlock_bh(&ack_info->seqlock);
			ack_info->win_scale = win_scale;
			write_sequnlock_bh(&ack_info->seqlock);
		}

		if (drop > 0) {
			win = ack_info->win_scale * ack_msg.win;
			if (win < (ack_m->ack_winsize * SIZE_KB))
				drop = 2;

			ret = tcp_ack_handle(msg, ack_m, ack_info,
					     &ack_msg, drop);
		}

		goto out;
	}

	index = tcp_ack_alloc_index(ack_m);
	if (index >= 0) {
		write_seqlock_bh(&ack_m->ack_info[index].seqlock);
		ack_m->ack_info[index].busy = 1;
		ack_m->ack_info[index].psh_flag = 0;
		ack_m->ack_info[index].last_time = jiffies;
		ack_m->ack_info[index].drop_cnt =
		    atomic_read(&ack_m->max_drop_cnt);
		ack_m->ack_info[index].win_scale =
		    (win_scale != 0) ? win_scale : 1;

		ack = &ack_m->ack_info[index].ack_msg;
		ack->dest = ack_msg.dest;
		ack->source = ack_msg.source;
		ack->saddr = ack_msg.saddr;
		ack->daddr = ack_msg.daddr;
		ack->seq = ack_msg.seq;
		write_sequnlock_bh(&ack_m->ack_info[index].seqlock);
	}

out:
	return ret;
}

void sc2355_tcp_ack_move_msg(struct sprd_priv *priv, struct sprd_msg *msg)
{
	struct tcp_ack_info *ack_info;
	struct sprd_tcp_ack_manage *ack_m = &priv->ack_m;
	int i = 0;

	if (!atomic_read(&ack_m->enable))
		return;

	if (msg->len > MAX_TCP_ACK)
		return;

	for (i = 0; i < SPRD_TCP_ACK_NUM; i++) {
		ack_info = &ack_m->ack_info[i];
		write_seqlock_bh(&ack_info->seqlock);
		if (ack_info->busy && ack_info->in_send_msg == msg)
			ack_info->in_send_msg = NULL;
		write_sequnlock_bh(&ack_info->seqlock);
	}
}

void sc2355_tcp_ack_init(struct sprd_priv *priv)
{
	int i;
	struct sprd_tcp_ack_manage *ack_m = &priv->ack_m;
	struct tcp_ack_info *ack_info;

	memset(ack_m, 0, sizeof(struct sprd_tcp_ack_manage));
	ack_m->priv = priv;
	spin_lock_init(&ack_m->lock);
	atomic_set(&ack_m->max_drop_cnt, SPRD_TCP_ACK_DROP_CNT);
	ack_m->last_time = jiffies;
	ack_m->timeout = msecs_to_jiffies(SPRD_ACK_OLD_TIME);

	for (i = 0; i < SPRD_TCP_ACK_NUM; i++) {
		ack_info = &ack_m->ack_info[i];
		ack_info->ack_info_num = i;
		seqlock_init(&ack_info->seqlock);
		ack_info->last_time = jiffies;
		ack_info->timeout = msecs_to_jiffies(SPRD_ACK_OLD_TIME);
		timer_setup(&ack_info->timer, tcp_ack_timeout, 0);
	}

	atomic_set(&ack_m->enable, 1);
	ack_m->ack_winsize = MIN_WIN;
}

void sc2355_tcp_ack_deinit(struct sprd_priv *priv)
{
	int i;
	struct sprd_tcp_ack_manage *ack_m = &priv->ack_m;
	struct sprd_msg *drop_msg = NULL;

	atomic_set(&ack_m->enable, 0);

	for (i = 0; i < SPRD_TCP_ACK_NUM; i++) {
		drop_msg = NULL;

		write_seqlock_bh(&ack_m->ack_info[i].seqlock);
		del_timer(&ack_m->ack_info[i].timer);
		drop_msg = ack_m->ack_info[i].msg;
		ack_m->ack_info[i].msg = NULL;
		write_sequnlock_bh(&ack_m->ack_info[i].seqlock);

		if (drop_msg)
			sprd_chip_drop_tcp_msg(&priv->chip, drop_msg);
	}

	/*
	 * MU300 (issue #146): del_timer() above cannot wait under the seqlock the callback takes, so a timeout
	 * already running on another CPU could still be inside ack_info after this returns, and priv (with
	 * ack_m in it) is freed next. With enable 0 and msg NULL the callback does nothing; wait for it.
	 */
	for (i = 0; i < SPRD_TCP_ACK_NUM; i++)
		timer_shutdown_sync(&ack_m->ack_info[i].timer);
}
