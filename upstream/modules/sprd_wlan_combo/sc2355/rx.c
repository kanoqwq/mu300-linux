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

#include <linux/icmp.h>
#include <linux/ip.h>
#include <linux/ktime.h>
#include <linux/moduleparam.h>
#include <linux/rcupdate.h>
#include <net/ip6_checksum.h>

#include "cmdevt.h"
#include "common/common.h"
#include "common/debug.h"
#include "common/delay_work.h"
#include "common/msg.h"
#include "common/chip_ops.h"
#include "rx.h"
#include "txrx.h"

/* Opt-in diagnostic: driver ingress to network-stack delivery for ICMP.
 * No timestamping or per-packet accounting occurs while disabled. */
static bool mu300_rx_probe;
static atomic64_t mu300_rx_0_5 = ATOMIC64_INIT(0);
static atomic64_t mu300_rx_5_10 = ATOMIC64_INIT(0);
static atomic64_t mu300_rx_10_20 = ATOMIC64_INIT(0);
static atomic64_t mu300_rx_20_50 = ATOMIC64_INIT(0);
static atomic64_t mu300_rx_50_plus = ATOMIC64_INIT(0);
module_param_named(mu300_rx_probe, mu300_rx_probe, bool, 0644);

static int mu300_rx_stats_get(char *buffer, const struct kernel_param *kp)
{
	return scnprintf(buffer, 160, "lt5=%lld 5to10=%lld 10to20=%lld 20to50=%lld ge50=%lld\n",
		(long long)atomic64_read(&mu300_rx_0_5),
		(long long)atomic64_read(&mu300_rx_5_10),
		(long long)atomic64_read(&mu300_rx_10_20),
		(long long)atomic64_read(&mu300_rx_20_50),
		(long long)atomic64_read(&mu300_rx_50_plus));
}

static const struct kernel_param_ops mu300_rx_stats_ops = {
	.get = mu300_rx_stats_get,
};
module_param_cb(mu300_rx_stats, &mu300_rx_stats_ops, NULL, 0444);

static void mu300_rx_probe_icmp(struct sk_buff *skb)
{
	const struct ethhdr *eth;
	const struct iphdr *ip;
	const struct icmphdr *icmp;
	s64 ms;

	if (skb->len < ETH_HLEN + sizeof(*ip))
		return;
	eth = (const struct ethhdr *)skb->data;
	if (eth->h_proto != htons(ETH_P_IP))
		return;
	ip = (const struct iphdr *)(skb->data + ETH_HLEN);
	if (ip->protocol != IPPROTO_ICMP || ip->ihl < 5 ||
	    skb->len < ETH_HLEN + ip->ihl * 4 + sizeof(*icmp))
		return;
	icmp = (const struct icmphdr *)(skb->data + ETH_HLEN + ip->ihl * 4);
	if (icmp->type != ICMP_ECHO && icmp->type != ICMP_ECHOREPLY)
		return;
	if (!ktime_to_ns(skb->tstamp))
		return;
	ms = ktime_ms_delta(ktime_get(), skb->tstamp);
	if (ms < 5)
		atomic64_inc(&mu300_rx_0_5);
	else if (ms < 10)
		atomic64_inc(&mu300_rx_5_10);
	else if (ms < 20)
		atomic64_inc(&mu300_rx_10_20);
	else if (ms < 50)
		atomic64_inc(&mu300_rx_20_50);
	else
		atomic64_inc(&mu300_rx_50_plus);
}

static bool rx_mh_ipv6_ext_hdr(unsigned char nexthdr)
{
	return (nexthdr == NEXTHDR_HOP ||
		nexthdr == NEXTHDR_ROUTING || nexthdr == NEXTHDR_DEST);
}

static int rx_ipv6_csum(void *data, __wsum csum)
{
	int ret = 0;
	struct rx_msdu_desc *msdu_desc = (struct rx_msdu_desc *)data;
	struct ethhdr *eth = (struct ethhdr *)(data + msdu_desc->msdu_offset);
	struct ipv6hdr *ip6h = NULL;
	struct ipv6_opt_hdr *hp = NULL;
	unsigned short dataoff = ETH_HLEN;
	unsigned short nexthdr = 0;

	pr_debug("%s: eth_type: 0x%x\n", __func__, eth->h_proto);

	if (eth->h_proto == cpu_to_be16(ETH_P_IPV6)) {
		data += msdu_desc->msdu_offset;
		ip6h = data + dataoff;
		nexthdr = ip6h->nexthdr;
		dataoff += sizeof(*ip6h);

		while (rx_mh_ipv6_ext_hdr(nexthdr)) {
			pr_debug("%s: nexthdr: %d\n", __func__, nexthdr);
			hp = (struct ipv6_opt_hdr *)(data + dataoff);
			dataoff += ipv6_optlen(hp);
			nexthdr = hp->nexthdr;
		}

		pr_debug("%s: nexthdr: %d, dataoff: %d, len: %d\n",
			 __func__, nexthdr, dataoff,
			 (msdu_desc->msdu_len - dataoff));

		if (!csum_ipv6_magic(&ip6h->saddr, &ip6h->daddr,
				     (msdu_desc->msdu_len - dataoff),
				     nexthdr, csum)) {
			ret = 1;
		} else {
			ret = -1;
		}

		pr_debug("%s: ret: %d\n", __func__, ret);
	}

	return ret;
}

static void rx_send_cmd_process(struct sprd_priv *priv, void *data, int len,
				unsigned char id, unsigned char ctx_id)
{
	struct sprd_vif *vif;
	struct sprd_work *misc_work = NULL;

	if (unlikely(!priv)) {
		pr_err("%s priv not init.\n", __func__);
	} else if (ctx_id > STAP_MODE_P2P_DEVICE) {
		pr_err("%s [ctx_id %d]RX err\n", __func__, ctx_id);
	} else {
		vif = sc2355_ctxid_to_vif(priv, ctx_id);
		if (!vif) {
			pr_err("%s cant't get vif from ctx_id%d\n",
			       __func__, ctx_id);
		} else {
			misc_work = sprd_alloc_work(len);
			if (!misc_work) {
				pr_err("%s out of memory", __func__);
			} else {
				misc_work->vif = vif;
				misc_work->id = id;
				memcpy(misc_work->data, data, len);
				sprd_queue_work(vif->priv, misc_work);
			}
			sprd_put_vif(vif);
		}
	}
}

int sprd_rx_defragment_attack_check(struct sprd_priv *priv, struct sk_buff *skb)
{
	struct sprd_hif *hif = &priv->hif;
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	struct rx_msdu_desc *msdu_desc = (struct rx_msdu_desc *)skb->data;

	if (msdu_desc->ctx_id >= SPRD_MAC_INDEX_MAX) {
		pr_err("%s [ctx_id %d]RX err\n", __func__, msdu_desc->ctx_id);
		return -1;
	}

	if ((msdu_desc->amsdu_flag == 1) && (msdu_desc->snap_hdr_present == 0)
	    && (msdu_desc->first_msdu_of_mpdu == 1)) {
		rx_mgmt->rx_snaphdr_flag = 1;
		rx_mgmt->rx_snaphdr_seqnum = msdu_desc->seq_num;
		rx_mgmt->rx_snaphdr_lut = msdu_desc->sta_lut_index;
		rx_mgmt->rx_snaphdr_tid = msdu_desc->tid;
		pr_err("%s snaphdr attect flag %d %d %d\n", __func__,
			msdu_desc->seq_num,
			msdu_desc->sta_lut_index, msdu_desc->tid);
		if (msdu_desc->last_buff_of_mpdu == 1) {
			rx_mgmt->rx_snaphdr_flag = 0;
			pr_err("%s snaphdr attect over %d last %d %d %d\n", __func__,
				msdu_desc->snap_hdr_present,
				msdu_desc->last_msdu_of_mpdu,
				msdu_desc->last_buff_of_mpdu,
				msdu_desc->last_msdu_of_buff);
		}
		return -1;
	}

	if (rx_mgmt->rx_snaphdr_flag == 1) {
		if ((rx_mgmt->rx_snaphdr_seqnum == msdu_desc->seq_num) &&
		    (rx_mgmt->rx_snaphdr_lut == msdu_desc->sta_lut_index) &&
		    (rx_mgmt->rx_snaphdr_tid == msdu_desc->tid)) {
			pr_err("%s snaphdr attect %d %d %d\n", __func__,
			       msdu_desc->seq_num,
			       msdu_desc->sta_lut_index, msdu_desc->tid);
			if (msdu_desc->last_buff_of_mpdu == 1) {
				rx_mgmt->rx_snaphdr_flag = 0;
				pr_err("%s snaphdr attect over %d %d %d %d last %d %d %d\n",
				       __func__, msdu_desc->snap_hdr_present,
					msdu_desc->seq_num,
					msdu_desc->sta_lut_index, msdu_desc->tid,
					msdu_desc->last_msdu_of_mpdu,
					msdu_desc->last_buff_of_mpdu,
					msdu_desc->last_msdu_of_buff);
			}
			return -1;
		}
	}
	return 0;
}

static void rx_skb_process(struct sprd_priv *priv, struct sk_buff *skb)
{
	struct sprd_vif *vif = NULL;
	struct net_device *ndev = NULL;
	struct rx_msdu_desc *msdu_desc = NULL;
	struct sk_buff *tx_skb = NULL;
	struct sprd_hif *hif;
	struct ethhdr *eth;
	int ret = 0;

	hif = &priv->hif;
	msdu_desc = (struct rx_msdu_desc *)skb->data;

	if (unlikely(!priv)) {
		pr_err("%s priv not init.\n", __func__);
		goto err;
	}

	ret = sprd_rx_defragment_attack_check(priv, skb);
	if (ret == -1)
		goto err;

	vif = sc2355_ctxid_to_vif(priv, msdu_desc->ctx_id);
	if (!vif) {
		pr_err("%s cannot get vif, ctx_id: %d\n",
		       __func__, msdu_desc->ctx_id);
		goto err;
	}

	if (!vif->ndev) {
		pr_err("%s ndev is NULL, ctx_id = %d\n",
		       __func__, msdu_desc->ctx_id);
		BUG_ON(1);
	}

	ndev = vif->ndev;
	skb_reserve(skb, msdu_desc->msdu_offset);
	skb_put(skb, msdu_desc->msdu_len);
	if (unlikely(READ_ONCE(mu300_rx_probe))) {
		mu300_rx_probe_icmp(skb);
		skb->tstamp = 0;
	}

	eth = (struct ethhdr *)skb->data;
	if (eth->h_proto == htons(ETH_P_IPV6))
		if (ether_addr_equal(skb->data, skb->data + ETH_ALEN)) {
			pr_err
			    ("%s, drop loopback pkt, macaddr:%02x:%02x:%02x:%02x:%02x:%02x\n",
			     __func__, skb->data[0], skb->data[1], skb->data[2],
			     skb->data[3], skb->data[4], skb->data[5]);
			goto err;
		}

	if (hif->tdls_flow_count_enable == 1)
		sc2355_tdls_count_flow(vif, skb->data + ETH_ALEN,
				       skb->len - ETH_ALEN);
	sc2355_sdio_rx_throughput_statistic(skb->len);

	if ((vif->mode == SPRD_MODE_AP ||
	     vif->mode == SPRD_MODE_P2P_GO) && msdu_desc->uc_w2w_flag) {
		skb->dev = ndev;
		dev_queue_xmit(skb);
	} else {
		if ((vif->mode == SPRD_MODE_AP ||
		     vif->mode == SPRD_MODE_P2P_GO) &&
		    msdu_desc->bc_mc_w2w_flag) {
			struct ethhdr *eth = (struct ethhdr *)skb->data;

			/* IP multicast is forwarded by the host network stack. */
			if (eth->h_proto != htons(ETH_P_IP) &&
			    eth->h_proto != htons(ETH_P_IPV6)) {
				tx_skb = pskb_copy(skb, GFP_ATOMIC);
				if (likely(tx_skb)) {
					tx_skb->dev = ndev;
					dev_queue_xmit(tx_skb);
				}
			}
		}

		/* skb->data MUST point to ETH HDR */
		sc2355_tcp_ack_filter_rx(priv, skb->data, msdu_desc->msdu_len);

		if (hif->hw_type == SPRD_HW_SC2355_PCIE)
			sc2355_count_rx_tp(hif, msdu_desc->msdu_len);
		sprd_netif_rx(ndev, skb);
	}

	sprd_put_vif(vif);

	return;

err:
	dev_kfree_skb(skb);
#if defined(MORE_DEBUG)
	hif->stats.rx_errors++;
	hif->stats.rx_dropped++;
#endif
}

static inline void
rx_mh_data_process(struct rx_mgmt *rx_mgmt, void *data,
		   int len, int buffer_type)
{
	sc2355_mm_mh_data_process(&rx_mgmt->mm_entry, data, len, buffer_type);
}
void sc2355_count_rx_tp(struct sprd_hif *hif, int len)
{
	unsigned long long timeus = 0;
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	struct sprd_msg *drop_msg;

	rx_mgmt->rx_total_len += len;
	if (rx_mgmt->rx_total_len == len) {
		rx_mgmt->rxtimebegin = ktime_get();
		return;
	}

	rx_mgmt->rxtimeend = ktime_get();
	timeus =
	    div_u64(rx_mgmt->rxtimeend - rx_mgmt->rxtimebegin, NSEC_PER_USEC);
	if (div_u64((rx_mgmt->rx_total_len * 8), timeus) >=
	    hif->priv->debug.tcpack_delay_th_in_mb &&
	    timeus > hif->priv->debug.tcpack_time_in_ms * USEC_PER_MSEC) {
		rx_mgmt->rx_total_len = 0;
		adjust_tcp_ack("tcpack_delay_en=1", strlen("tcpack_delay_en="));
		drop_msg = tcp_ack_delay(&hif->priv->ack_m);

		if (drop_msg)
			sprd_chip_drop_tcp_msg(&hif->priv->chip, drop_msg);
	} else if (div_u64((rx_mgmt->rx_total_len * 8), timeus) <
		   hif->priv->debug.tcpack_delay_th_in_mb &&
		   timeus >
		   hif->priv->debug.tcpack_time_in_ms * USEC_PER_MSEC) {
		rx_mgmt->rx_total_len = 0;
		adjust_tcp_ack("tcpack_delay_en=1", strlen("tcpack_delay_en="));
		drop_msg = tcp_ack_delay(&hif->priv->ack_m);

		if (drop_msg)
			sprd_chip_drop_tcp_msg(&hif->priv->chip, drop_msg);
	}
}

void
sc2355_rx_mh_addr_process(struct rx_mgmt *rx_mgmt, void *data,
		   int len, int buffer_type)
{
	struct sprd_hif *hif = rx_mgmt->hif;
	struct sprd_common_hdr *hdr =
	    (struct sprd_common_hdr *)(data + hif->hif_offset);

	pr_debug("%s: rx_data_addr=0x%lx\n", __func__, (unsigned long)data);

	if (hdr->reserv) {
		pr_debug("%s: Add RX code here\n", __func__);
		sc2355_mm_mh_data_event_process(&rx_mgmt->mm_entry, data,
						len, buffer_type);
		sc2355_free_data(data, buffer_type);

	} else {
		pr_debug("%s: Add TX complete code here\n", __func__);
		/* Already in the RX worker, not the EDMA hard IRQ. Return buffers
		 * before publishing credit, without allocating a second work item.
		 * Losing that allocation used to lose the only TX completion receipt.
		 */
		sc2355_tx_free_pcie_data(data, len);
		sc2355_free_data(data, buffer_type);
	}
}

static void rx_net_work_queue(struct work_struct *work)
{
	struct rx_mgmt *rx_mgmt;
	struct sprd_priv *priv;
	struct sk_buff *reorder_skb = NULL, *skb = NULL;

	rx_mgmt = container_of(work, struct rx_mgmt, rx_net_work);
	priv = rx_mgmt->hif->priv;

	reorder_skb = sc2355_reorder_get_skb_list(&rx_mgmt->ba_entry);
	while (reorder_skb) {
		SPRD_GET_FIRST_SKB(skb, reorder_skb);
		skb = sc2355_defrag_data_process(&rx_mgmt->defrag_entry, skb);
		if (skb)
			rx_skb_process(priv, skb);
	}
}



inline int sc2355_fill_skb_csum(struct sk_buff *skb, unsigned short csum)
{
	/*
	 * The firmware checksum does not match what the stack expects for CHECKSUM_COMPLETE: forwarded frames trigger
	 * "hw csum failure" with a full packet dump each time. Let the stack verify checksums in software.
	 * (Same as kernel/patches/wlan_combo-rx-software-checksum.patch does for the 5.4 build: without this,
	 * rx_ipv6_csum() returns -1 for IPv6 TCP/UDP frames whose msdu_len counts WiFi padding, and mm.c then
	 * drops the skb before netif_rx - every client IPv6 TCP/UDP frame over Wi-Fi vanished there.)
	 */
	(void)rx_ipv6_csum;
	(void)csum;
	skb->ip_summed = CHECKSUM_NONE;
	return 0;
}

void sc2355_rx_send_cmd(struct sprd_hif *hif, void *data, int len,
			unsigned char id, unsigned char ctx_id)
{
	struct sprd_priv *priv = hif->priv;

	rx_send_cmd_process(priv, data, len, id, ctx_id);
}

void sc2355_queue_rx_buff_work(struct sprd_priv *priv, unsigned char id)
{
	struct sprd_work *misc_work;
	struct sprd_vif *tmp_vif;
	struct rx_mgmt *rx_mgmt = priv->hif.rx_mgmt;

	if (!rx_mgmt || READ_ONCE(rx_mgmt->stopping))
		return;

	misc_work = sprd_alloc_work(0);
	if (!misc_work) {
		pr_err_ratelimited("%s out of memory\n", __func__);
		return;
	}
	spin_lock_bh(&priv->list_lock);
	list_for_each_entry(tmp_vif, &priv->vif_list, vif_node) {
		if (tmp_vif->state & VIF_STATE_OPEN) {
			misc_work->vif = tmp_vif;
			break;
		}
	}
	spin_unlock_bh(&priv->list_lock);
	/*
	 * MU300: sc2355_do_delay_work() dereferences work->vif for both ids, and with no interface open there is no
	 * RX ring to refill or flush. The vendor queued it anyway; the refill's timed retry can now fire after the
	 * last interface closed.
	 */
	if (!misc_work->vif) {
		kfree(misc_work);
		return;
	}
	switch (id) {
	case SPRD_PCIE_RX_ALLOC_BUF:
	case SPRD_PCIE_RX_FLUSH_BUF:
		misc_work->id = id;
		sprd_queue_work(priv, misc_work);
		break;
	default:
		pr_err("%s: err id: %d\n", __func__, id);
		kfree(misc_work);
		break;
	}
}

void sc2355_rx_up(struct rx_mgmt *rx_mgmt)
{
	complete(&rx_mgmt->rx_completed);
}

void sc2355_rx_process(struct rx_mgmt *rx_mgmt, struct sk_buff *pskb)
{
	if (pskb && unlikely(READ_ONCE(mu300_rx_probe)))
		pskb->tstamp = ktime_get();
	sc2355_reorder_data_process(&rx_mgmt->ba_entry, pskb);

	if (!work_pending(&rx_mgmt->rx_net_work))
		queue_work(rx_mgmt->rx_net_workq, &rx_mgmt->rx_net_work);
}

/*
 * MU300: back-off for an RX refill that got nowhere. The vendor requeued the refill at once whenever buffers were
 * still owed, so when the chip stopped taking them (issue #94: channel 10's links refused for minutes while the
 * WCN sat in dump status) the shared sprd_work worker retried back to back, printing on every attempt, until a CPU
 * soft-locked in printk and softlockup_panic rebooted the device. An attempt that neither allocates a buffer nor
 * sends the pending address list now retries from a timer, 20 ms doubling to 1 s; one that makes progress
 * resets it and requeues at once, as before.
 */
#define SPRD_REFILL_BACKOFF_MIN_MS	20U
#define SPRD_REFILL_BACKOFF_MAX_MS	1000U

void sc2355_rx_bus_complete(struct sprd_hif *hif)
{
	struct rx_mgmt *rx_mgmt = smp_load_acquire(&hif->rx_mgmt);
	unsigned long flags;

	if (!rx_mgmt)
		return;

	spin_lock_irqsave(&rx_mgmt->refill_lock, flags);
	if (!rx_mgmt->stopping && rx_mgmt->refill_bus_waiting &&
	    !rx_mgmt->refill_bus_ready) {
		/* A completion can race the failed submission or timer arming.
		 * Remember it even when no retry timer has been queued yet.
		 */
		rx_mgmt->refill_bus_ready = true;
		if (delayed_work_pending(&rx_mgmt->refill_retry))
			mod_delayed_work(system_wq, &rx_mgmt->refill_retry, 0);
	}
	spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);
}

static void rx_refill_retry(struct work_struct *work)
{
	struct rx_mgmt *rx_mgmt =
	    container_of(to_delayed_work(work), struct rx_mgmt, refill_retry);

	/* back onto the ordered sprd_work queue: a refill must not run beside another one */
	if (!READ_ONCE(rx_mgmt->stopping))
		sc2355_queue_rx_buff_work(rx_mgmt->hif->priv, SPRD_PCIE_RX_ALLOC_BUF);
}

int sc2355_mm_fill_buffer(struct sprd_hif *hif)
{
	struct rx_mgmt *rx_mgmt =
	    (struct rx_mgmt *)hif->rx_mgmt;
	struct mem_mgmt *mm_entry = &rx_mgmt->mm_entry;
	unsigned int num = 0, alloc_num;
	unsigned long flags;
	bool had_trans, progress, immediate = false;
	int ret = 0;

	spin_lock_irqsave(&rx_mgmt->refill_lock, flags);
	if (rx_mgmt->stopping) {
		spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);
		return 0;
	}

	/* Ordinary RX events must not bypass a fault/allocation back-off.
	 * Actual DMA progress may release a bus-pressure retry early.
	 */
	if (delayed_work_pending(&rx_mgmt->refill_retry) &&
	    !rx_mgmt->refill_bus_ready) {
		spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);
		return atomic_read(&mm_entry->alloc_num);
	}
	if (rx_mgmt->refill_bus_ready)
		cancel_delayed_work(&rx_mgmt->refill_retry);
	rx_mgmt->refill_bus_ready = false;
	rx_mgmt->refill_bus_waiting = false;
	spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);

	alloc_num = atomic_xchg(&mm_entry->alloc_num, 0);
	had_trans = rx_mgmt->addr_trans_head != NULL;

	num = sc2355_mm_buffer_alloc(&rx_mgmt->mm_entry, alloc_num);
	progress = num < alloc_num;
	/* Arm before pushing: the IRQ can run before tx_addr_trans returns.
	 * No allocation, DMA submission or refill itself runs under this lock.
	 */
	spin_lock_irqsave(&rx_mgmt->refill_lock, flags);
	rx_mgmt->refill_bus_waiting = !rx_mgmt->stopping &&
		hif->hw_type == SPRD_HW_SC2355_PCIE &&
		rx_mgmt->addr_trans_head && hif->ops->tx_addr_trans;
	spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);
	if (hif->ops->tx_addr_trans)
		ret = hif->ops->tx_addr_trans(hif, NULL, 0, true);
	if (had_trans && !rx_mgmt->addr_trans_head)
		progress = true;
	if (num)
		num = atomic_add_return(num, &mm_entry->alloc_num);

	spin_lock_irqsave(&rx_mgmt->refill_lock, flags);
	if (rx_mgmt->stopping) {
		rx_mgmt->refill_bus_waiting = false;
		rx_mgmt->refill_bus_ready = false;
		spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);
		return num;
	}
	if (progress)
		rx_mgmt->refill_backoff_ms = 0;

	rx_mgmt->refill_bus_waiting = hif->hw_type == SPRD_HW_SC2355_PCIE &&
		ret == -EAGAIN && rx_mgmt->addr_trans_head;
	if (!rx_mgmt->refill_bus_waiting)
		rx_mgmt->refill_bus_ready = false;

	if (num > SPRD_MAX_ADD_MH_BUF_ONCE || rx_mgmt->addr_trans_head) {
		if (progress) {
			rx_mgmt->refill_bus_waiting = false;
			rx_mgmt->refill_bus_ready = false;
			immediate = true;
		} else {
			rx_mgmt->refill_backoff_ms =
			    clamp(rx_mgmt->refill_backoff_ms * 2,
				  SPRD_REFILL_BACKOFF_MIN_MS,
				  SPRD_REFILL_BACKOFF_MAX_MS);
			/* The IRQ and timer arming use the same lock, so a zero-delay
			 * wake cannot be overwritten by an older back-off decision.
			 */
			mod_delayed_work(system_wq, &rx_mgmt->refill_retry,
					rx_mgmt->refill_bus_ready ? 0 :
					msecs_to_jiffies(rx_mgmt->refill_backoff_ms));
		}
	}
	spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);
	if (immediate)
		sc2355_queue_rx_buff_work(rx_mgmt->hif->priv,
					  SPRD_PCIE_RX_ALLOC_BUF);

	return num;
}

void sc2355_mm_fill_all_buffer(void *hif)
{
	struct rx_mgmt *rx_mgmt =
	    (struct rx_mgmt *)((struct sprd_hif *)hif)->rx_mgmt;
	struct mem_mgmt *mm_entry = &rx_mgmt->mm_entry;
	int num = SPRD_MAX_MH_BUF - skb_queue_len(&mm_entry->buffer_list);

	if (num >= 0) {
		atomic_add(num, &mm_entry->alloc_num);
		sc2355_mm_fill_buffer(hif);
	}
}

void sc2355_rx_flush_buffer(void *hif)
{
	struct rx_mgmt *rx_mgmt =
	    (struct rx_mgmt *)((struct sprd_hif *)hif)->rx_mgmt;
	struct mem_mgmt *mm_entry = &rx_mgmt->mm_entry;
	enum sprd_hif_type hw_type =
		((struct sprd_hif *)hif)->hw_type;
	unsigned long flags;

	if (rx_mgmt->addr_trans_head) {
		if (hw_type == SPRD_HW_SC2355_PCIE) {
			sc2355_pcie_tx_addr_trans_free(hif);
		} else {
			sc2355_tx_addr_trans_free(hif);
		}
	}

	sc2355_mm_flush_buffer(mm_entry);
	/* MU300: the ring is gone, and with it any timed refill (not _sync: this can run in RX context) */
	spin_lock_irqsave(&rx_mgmt->refill_lock, flags);
	cancel_delayed_work(&rx_mgmt->refill_retry);
	rx_mgmt->refill_backoff_ms = 0;
	rx_mgmt->refill_bus_waiting = false;
	rx_mgmt->refill_bus_ready = false;
	spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);
}

int sc2355_rx_init(struct sprd_hif *hif)
{
	int ret = 0;
	struct rx_mgmt *rx_mgmt = NULL;

	rx_mgmt = kzalloc(sizeof(*rx_mgmt), GFP_KERNEL);
	if (!rx_mgmt) {
		ret = -ENOMEM;
		goto err_rx_mgmt;
	}

	/* init rx_list */
	ret = sprd_init_msg(SPRD_RX_MSG_NUM, &rx_mgmt->rx_list);
	if (ret) {
		pr_err("%s tx_buf create failed: %d\n", __func__, ret);
		goto err_rx_list;
	}

	/* init rx_work */
	rx_mgmt->rx_queue =
	    alloc_ordered_workqueue("SPRD_RX_QUEUE", WQ_MEM_RECLAIM |
				    WQ_HIGHPRI | WQ_CPU_INTENSIVE);
	if (!rx_mgmt->rx_queue) {
		pr_err("%s SPRD_RX_QUEUE create failed\n", __func__);
		ret = -ENOMEM;
		goto err_rx_work;
	}

	/*init rx_queue*/
	if (hif->hw_type == SPRD_HW_SC2355_PCIE) {
		INIT_WORK(&rx_mgmt->rx_work, sc2355_pcie_rx_work_queue);
	} else {
		INIT_WORK(&rx_mgmt->rx_work, sc2355_rx_work_queue);
	}

	rx_mgmt->rx_net_workq = alloc_ordered_workqueue("SPRD_RX_NET_QUEUE",
							WQ_HIGHPRI |
							WQ_CPU_INTENSIVE |
							WQ_MEM_RECLAIM);
	if (!rx_mgmt->rx_net_workq) {
		pr_err("%s SPRD_RX_NET_QUEUE create failed\n", __func__);
		ret = -ENOMEM;
		goto err_rx_net_work;
	}

	/*init rx_queue*/
	INIT_WORK(&rx_mgmt->rx_net_work, rx_net_work_queue);

	ret = sc2355_defrag_init(&rx_mgmt->defrag_entry);
	if (ret) {
		pr_err("%s init defrag fail: %d\n", __func__, ret);
		goto err_rx_defrag;
	}

	ret = sc2355_mm_init(&rx_mgmt->mm_entry, (void *)hif);
	if (ret) {
		pr_err("%s init mm fail: %d\n", __func__, ret);
		goto err_rx_mm;
	}

	sc2355_reorder_init(&rx_mgmt->ba_entry);
	INIT_DELAYED_WORK(&rx_mgmt->refill_retry, rx_refill_retry);
	spin_lock_init(&rx_mgmt->refill_lock);

	hif->lp = 0;
	rx_mgmt->hif = hif;
	smp_store_release(&hif->rx_mgmt, (void *)rx_mgmt);

	return ret;

err_rx_mm:
	sc2355_mm_deinit(&rx_mgmt->mm_entry, hif);
err_rx_defrag:
	destroy_workqueue(rx_mgmt->rx_net_workq);
err_rx_net_work:
	destroy_workqueue(rx_mgmt->rx_queue);
err_rx_work:
	sprd_deinit_msg(&rx_mgmt->rx_list);
err_rx_list:
	kfree(rx_mgmt);
err_rx_mgmt:
	return ret;
}

int sc2355_rx_deinit(struct sprd_hif *hif)
{
	struct rx_mgmt *rx_mgmt = (struct rx_mgmt *)hif->rx_mgmt;
	unsigned long flags;

	/* Draining a producer is not enough if another queued work can re-arm its timer. */
	spin_lock_irqsave(&rx_mgmt->refill_lock, flags);
	WRITE_ONCE(rx_mgmt->stopping, true);
	rx_mgmt->refill_bus_waiting = false;
	rx_mgmt->refill_bus_ready = false;
	spin_unlock_irqrestore(&rx_mgmt->refill_lock, flags);

	/*
	 * Stop the timer, drain RX producers, then drain sprd_work. An in-flight
	 * refill may have passed its stopping check before teardown and re-armed
	 * the timer after the first cancel, so cancel again after all producers.
	 */
	cancel_delayed_work_sync(&rx_mgmt->refill_retry);
	flush_workqueue(rx_mgmt->rx_queue);
	destroy_workqueue(rx_mgmt->rx_queue);

	flush_workqueue(rx_mgmt->rx_net_workq);
	destroy_workqueue(rx_mgmt->rx_net_workq);
	sprd_clean_work(hif->priv);
	cancel_delayed_work_sync(&rx_mgmt->refill_retry);

	sprd_deinit_msg(&rx_mgmt->rx_list);

	sc2355_defrag_deinit(&rx_mgmt->defrag_entry);
	sc2355_mm_deinit(&rx_mgmt->mm_entry, hif);
	sc2355_reorder_deinit(&rx_mgmt->ba_entry);

	/* PCIe completions remain registered across RX init/deinit. Drain their
	 * protected lookups before freeing the context they may have acquired.
	 */
	smp_store_release(&hif->rx_mgmt, NULL);
	synchronize_rcu();
	kfree(rx_mgmt);

	return 0;
}
