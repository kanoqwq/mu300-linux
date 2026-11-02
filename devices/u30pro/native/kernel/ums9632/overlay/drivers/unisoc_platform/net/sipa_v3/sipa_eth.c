// SPDX-License-Identifier: GPL-2.0
/* Copyright 2021-2023 Unisoc(Shanghai)Technologies Co.Ltd
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

#define pr_fmt(fmt) "sipa_eth: " fmt

#include <linux/of.h>
#include <linux/debugfs.h>
#include <linux/device.h>
#include <linux/etherdevice.h>
#include <linux/if_arp.h>
#include <linux/init.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/seq_file.h>
#include <linux/skbuff.h>
#include <linux/ratelimit.h>
#include <linux/spinlock.h>
#include <net/ipv6.h>
#include <soc/unisoc/sipa.h>

#include "sipa_eth.h"
#include "sipa_dummy.h"
#include "sipa_priv.h"

/* Device status */
#define DEV_ON 1
#define DEV_OFF 0

#define SIPA_ETH_NAPI_WEIGHT 64
#define SIPA_ETH_IFACE_PREF "sipa_eth"

/* Cmd for ioctl */
#define SIOC_SIPA_ETH_SET_UID (SIOCDEVPRIVATE + 0)
#define SIOC_SIPA_ETH_DEL_UID (SIOCDEVPRIVATE + 1)

static DEFINE_RATELIMIT_STATE(rlimit_eth, 10 * HZ, 1);

static unsigned long queue_lock_flags;
static spinlock_t queue_lock; /* spin-lock for queue status protection */

#ifdef CONFIG_DEBUG_FS
static struct dentry *root;
static int sipa_eth_debugfs_mknod(void *root, void *data);
#endif

static void sipa_eth_flowctrl_handler(void *priv, int flowctrl)
{
	struct sipa_eth *sipa_eth = (struct sipa_eth *)priv;
	struct net_device *dev = sipa_eth->netdev;

	spin_lock_irqsave(&queue_lock, queue_lock_flags);

	if (flowctrl) {
		netif_stop_queue(dev);
		pr_info("stop queue on dev %s\n", dev->name);
	} else if (netif_queue_stopped(dev)) {
		netif_wake_queue(dev);
		pr_info("wakeup queue on dev %s\n", dev->name);
	}

	spin_unlock_irqrestore(&queue_lock, queue_lock_flags);
}

static void sipa_eth_notify_cb(void *priv, enum sipa_evt_type evt,
			       unsigned long data)
{
	struct sipa_eth *sipa_eth = (struct sipa_eth *)priv;

	switch (evt) {
	case SIPA_LEAVE_FLOWCTRL:
		pr_debug("dev %s SIPA LEAVE FLOWCTRL\n", sipa_eth->netdev->name);
		sipa_eth_flowctrl_handler(priv, 0);
		break;
	/* following cases may be deprecated */
	case SIPA_RECEIVE:
		break;
	case SIPA_ENTER_FLOWCTRL:
		pr_debug("dev %s SIPA ENTER FLOWCTRL\n", sipa_eth->netdev->name);
		sipa_eth_flowctrl_handler(priv, 1);
		break;
	default:
		break;
	}
}

static int get_pkt_proto(struct sk_buff *skb)
{
	struct iphdr *iph = ip_hdr(skb);
	struct ipv6hdr *ipv6_hdr;

	if (iph->version == 4)
		return iph->protocol;

	ipv6_hdr = (struct ipv6hdr *)iph;
	return ipv6_hdr->nexthdr;
}

static struct tcphdr *get_pkt_tcphdr(struct sk_buff *skb)
{
	u8 iph_len;
	struct iphdr *iph = ip_hdr(skb);

	if (iph->version == 4)
		iph_len = iph->ihl * 4;
	else
		iph_len = sizeof(struct ipv6hdr);
	return (struct tcphdr *)((char *)iph + iph_len);
}

static bool sipa_eth_check_ping_packet(struct sk_buff *skb)
{
	u8 protocol = get_pkt_proto(skb);

	if (protocol == IPPROTO_ICMP || protocol == IPPROTO_ICMPV6)
		return true;
	return false;
}

static bool sipa_eth_check_tcp_ack_packet(struct sk_buff *skb)
{
	struct tcphdr *tcph;
	struct ipv6hdr *ip6h;
	struct iphdr *iph = ip_hdr(skb);
	u8 protocol = get_pkt_proto(skb);

	if (protocol == IPPROTO_TCP) {
		tcph = get_pkt_tcphdr(skb);

		/* we simply consider a tcp syn
		 * is a tcp pkt with syn == 1
		 */
		if (tcph->syn)
			return true;

		/* we simply consider a tcp ack
		 * is a tcp pkt with data_len = 0
		 */
		if (iph->version == 4) {
			if ((ntohs(iph->tot_len) - iph->ihl * 4)
			    == tcph->doff * 4)
				return true;
		} else if (iph->version == 6) {
			ip6h = (struct ipv6hdr *)iph;
			if (ntohs(ip6h->payload_len) == tcph->doff * 4)
				return true;
		}
	}
	return false;
}

static inline bool sipa_eth_pkt_need_nodelay(struct sk_buff *skb)
{
	if (sipa_eth_check_ping_packet(skb) ||
	    sipa_eth_check_tcp_ack_packet(skb))
		return true;

	return false;
}

static void sipa_eth_wakeup_cp(struct sk_buff *skb)
{
	struct sipa_plat_drv_cfg *ipa = sipa_get_ctrl_pointer();

	if (unlikely(!ipa->pscp_pd_status.notify_cb)) {
		pr_err("pscp_pd_status.notify_cb is null\n");
		return;
	}

	if (sipa_eth_pkt_need_nodelay(skb)) {
		if (sipa_check_pscp_enter_sleep()) {
			if (__ratelimit(&rlimit_eth))
				pr_info("pkt need nodelay, wakeup pscp now\n");
			ipa->pscp_pd_status.notify_cb(ipa->pscp_pd_status.user_data);
			return;
		}
		if (sipa_check_phycp_enter_sleep()) {
			if (__ratelimit(&rlimit_eth))
				pr_info("pkt need nodelay, wakeup phycp now\n");
			ipa->pscp_pd_status.notify_cb(ipa->pscp_pd_status.user_data);
			return;
		}
	}
}

static netdev_tx_t sipa_eth_start_xmit(struct sk_buff *skb,
				       struct net_device *dev)
{
	struct sipa_eth *sipa_eth = netdev_priv(dev);
	struct sipa_eth_init_data *pdata = sipa_eth->pdata;
	struct net_device_stats *stats = sipa_eth->stats;
	struct sipa_plat_drv_cfg *ipa = sipa_get_ctrl_pointer();
	int ret = 0;
	int netid;
	unsigned int len;
	unsigned long tx_threshold;

	if (sipa_eth->state != DEV_ON) {
		pr_err("called when %s is down\n", dev->name);
		stats->tx_dropped++;
		dev_kfree_skb_any(skb);
		return NETDEV_TX_OK;
	}

	ret = skb_linearize(skb);
	if (unlikely(ret)) {
		stats->tx_dropped++;
		dev_kfree_skb_any(skb);
		return ret;
	}

	netid = pdata->netid;
	len = skb->len;
	ipa->ul_threshold.send_bytes += len;
	tx_threshold = ipa->ul_threshold.bytes_threshold;

	/* because the BPF program and dev->type is rawip,
	 * the IPv6 forwarding packet is without ethernet header
	 */
	if (likely(skb_headroom(skb) >= ETH_HLEN)) {
		if (skb->mac_header == skb->network_header)
			skb_push(skb, ETH_HLEN);
	}

	if (ipa->ul_threshold.persec_bytes < tx_threshold)
		sipa_eth_wakeup_cp(skb);

	ret = sipa_nic_tx(sipa_eth->nic_id, pdata->src_id, netid, skb);
	if (unlikely(ret != 0)) {
		pr_err("fail to send, ret %d\n", ret);
		stats->tx_errors++;
		if (likely(skb_headroom(skb) >= ETH_HLEN)) {
			if (skb->mac_header == skb->network_header)
				skb_pull_inline(skb, ETH_HLEN);
		}
		return NETDEV_TX_BUSY;
	}

	/* update netdev statistics */
	stats->tx_packets++;
	stats->tx_bytes += len;

	return NETDEV_TX_OK;
}

/* Open interface */
static int sipa_eth_open(struct net_device *dev)
{
	struct sipa_eth *sipa_eth = netdev_priv(dev);
	struct sipa_eth_init_data *pdata = sipa_eth->pdata;
	int ret = 0;

	ret = sipa_nic_open(pdata->src_id,
			    pdata->netid,
			    sipa_eth_notify_cb,
			    (void *)sipa_eth);

	if (ret < 0) {
		pr_err("fail to open %s, ret %d\n", dev->name, ret);
		return ret;
	}

	sipa_eth->nic_id = ret;
	sipa_eth->state = DEV_ON;

	pr_info("open %s netid %d src_id %d nic_id %d\n",
		dev->name, pdata->netid, pdata->src_id,
		sipa_eth->nic_id);

	if (!netif_carrier_ok(sipa_eth->netdev)) {
		netif_carrier_on(sipa_eth->netdev);
		pr_info("set netif_carrier_on\n");
	}

	netif_start_queue(dev);

	return 0;
}

/* Close interface */
static int sipa_eth_close(struct net_device *dev)
{
	struct sipa_eth *sipa_eth = netdev_priv(dev);

	sipa_nic_close(sipa_eth->nic_id);
	pr_info("close %s\n", dev->name);

	sipa_eth->state = DEV_OFF;

	netif_stop_queue(dev);

	return 0;
}

static struct net_device_stats *sipa_eth_get_stats(struct net_device *dev)
{
	struct sipa_eth *sipa_eth = netdev_priv(dev);

	return sipa_eth->stats;
}

static int sipa_eth_siocdevprivate(struct net_device *dev,
				   struct ifreq *ifr,
				   void __user *data,
				   int cmd)
{
	int vip_uid = 0;
	int ret = 0;
	int i = 0;
	struct sipa_vip_attrs *eth_vip_attrs = sipa_eth_vip_attrs();

	switch (cmd) {
	case SIOC_SIPA_ETH_SET_UID:
		eth_vip_attrs->vip_enable = 1;

		ret = copy_from_user(&vip_uid, data, sizeof(vip_uid));
		pr_info("copy from userspace vip_uid is %d\n", vip_uid);
		if (ret)
			return -EFAULT;

		for (; i < MAX_UID_NUM; i++) {
			if (eth_vip_attrs->vip_uids[i] == vip_uid)
				break;

			if (!eth_vip_attrs->vip_uids[i]) {
				eth_vip_attrs->vip_uids[i] = vip_uid;
				break;
			}
		}

		if (i >= (MAX_UID_NUM - 1))
			pr_info("the vip_uids from userspace are full\n");

		break;
	case SIOC_SIPA_ETH_DEL_UID:
		eth_vip_attrs->vip_enable = 0;
		pr_info("set vip_enable %d, delete all the vip uids\n", eth_vip_attrs->vip_enable);
		memset(eth_vip_attrs->vip_uids, 0, sizeof(eth_vip_attrs->vip_uids));
		break;
	default:
		pr_err("ioctl cmd not support.\n");
		return -EOPNOTSUPP;
	}

	return 0;
}

static const struct net_device_ops sipa_eth_ops = {
	.ndo_open = sipa_eth_open,
	.ndo_stop = sipa_eth_close,
	.ndo_start_xmit = sipa_eth_start_xmit,
	.ndo_get_stats = sipa_eth_get_stats,
	.ndo_siocdevprivate = sipa_eth_siocdevprivate,
};

static ssize_t gro_enable_show(struct device *dev,
			       struct device_attribute *attr,
			       char *buf)
{
	struct sipa_eth *sipa_eth = dev_get_drvdata(dev);

	return scnprintf(buf, PAGE_SIZE, "%d\n", sipa_eth->gro_enable);
}

static ssize_t gro_enable_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	int ret;
	int gro_enable;
	struct sipa_eth *sipa_eth = dev_get_drvdata(dev);

	ret = kstrtou32(buf, 10, &gro_enable);
	if (ret)
		return ret;

	sipa_eth->gro_enable = gro_enable;

	return count;
}

static ssize_t upload_threshold_show(struct device *dev,
			       struct device_attribute *attr,
			       char *buf)
{
	unsigned long tx_threshold;
	struct sipa_plat_drv_cfg *ipa = sipa_get_ctrl_pointer();

	tx_threshold = SIPA_BYTES2MBPS(ipa->ul_threshold.bytes_threshold);
	return scnprintf(buf, PAGE_SIZE, "%lu\n", tx_threshold);
}

static ssize_t upload_threshold_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	int ret;
	unsigned long tx_threshold;
	struct sipa_plat_drv_cfg *ipa = sipa_get_ctrl_pointer();

	ret = kstrtoul(buf, 10, &tx_threshold);
	if (ret)
		return ret;

	ipa->ul_threshold.bytes_threshold = SIPA_MBPS2BYTES(tx_threshold);

	return count;
}

static DEVICE_ATTR_RW(gro_enable);
static DEVICE_ATTR_RW(upload_threshold);

static ssize_t vip_enable_show(struct device *dev,
				    struct device_attribute *attr, char *buf)
{
	struct sipa_vip_attrs *eth_vip_attrs = sipa_eth_vip_attrs();

	return scnprintf(buf, PAGE_SIZE, "%d\n", eth_vip_attrs->vip_enable);
}

static ssize_t vip_enable_store(struct device *dev,
				     struct device_attribute *attr,
				     const char *buf,
				     size_t count)
{
	int flag, ret;
	struct sipa_vip_attrs *eth_vip_attrs = sipa_eth_vip_attrs();

	ret = kstrtoint(buf, 10, &flag);
	if (ret)
		return ret;

	eth_vip_attrs->vip_enable = flag;

	return count;
}
static DEVICE_ATTR_RW(vip_enable);

static struct attribute *sipa_eth_attributes[] = {
	&dev_attr_gro_enable.attr,
	&dev_attr_vip_enable.attr,
	&dev_attr_upload_threshold.attr,
	NULL,
};

static struct attribute_group sipa_eth_attribute_group = {
	.attrs = sipa_eth_attributes,
};

static int sipa_eth_parse_dt(struct sipa_eth_init_data **init,
			     struct device *dev)
{
	struct sipa_eth_init_data *pdata = NULL;
	struct device_node *np = dev->of_node;
	int ret;
	u32 udata, id;
	s32 sdata;

	if (!np)
		pr_err("dev of_node np is null\n");

	pdata = devm_kzalloc(dev, sizeof(*pdata), GFP_KERNEL);
	if (!pdata)
		return -ENOMEM;

	id = of_alias_get_id(np, "eth");
	switch (id) {
	case 0 ... 19:
		snprintf(pdata->name, IFNAMSIZ, "%s%d",
			 SIPA_ETH_IFACE_PREF, id);
		break;
	default:
		pr_err("wrong alias id from dts, id %d\n", id);
		return -EINVAL;
	}

	ret = of_property_read_s32(np, "sprd,netid", &sdata);
	if (ret) {
		pr_err("read sprd,netid ret %d\n", ret);
		return ret;
	}
	/* dts reflect */
	pdata->netid = sdata - 1;

	ret = of_property_read_u32(np, "sprd,term-type", &udata);
	if (ret) {
		pr_err("read sprd,term-type ret %d\n", ret);
		return ret;
	}

	pdata->src_id = udata;

	*init = pdata;
	pr_debug("after dt parse, name %s netid %d src_id %d\n",
		 pdata->name, pdata->netid, pdata->src_id);
	return 0;
}

static void sipa_eth_setup(struct net_device *dev)
{
	ether_setup(dev);
	/* avoid mdns to be send
	 * also disable arp
	 */
	dev->flags |= IFF_NOARP;
	dev->flags &= ~(IFF_BROADCAST | IFF_MULTICAST);
}

/*
 * SYSFS interfaces:
 * /sys/devices/platform/sipa-ethx/gro_enable [rw] [userspace]
 * /sys/devices/platform/sipa-ethx/vip_enable [rw] [userspace]
 * /sys/devices/platform/sipa-ethx/upload_threshold [rw] [userspace]
 */
static int sipa_eth_probe(struct platform_device *pdev)
{
	struct sipa_eth_init_data *pdata = pdev->dev.platform_data;
	struct net_device *netdev;
	struct sipa_eth *sipa_eth;
	char ifname[IFNAMSIZ];
	int ret;

	if (pdev->dev.of_node && !pdata) {
		ret = sipa_eth_parse_dt(&pdata, &pdev->dev);
		if (ret) {
			pr_err("failed to parse device tree, ret=%d\n", ret);
			return ret;
		}
		pdev->dev.platform_data = pdata;
	}

	strscpy(ifname, pdata->name, IFNAMSIZ);
	netdev = alloc_netdev(sizeof(struct sipa_eth),
			      ifname,
			      NET_NAME_UNKNOWN,
			      sipa_eth_setup);

	if (!netdev) {
		pr_err("alloc_netdev() failed.\n");
		return -ENOMEM;
	}

	/* The dev selects a new IPv6 interface identifier each time a new PDN connection
	 * is opened. If the dev type is ARPHRD_RAWIP, the IPv6 interface identifier use
	 * eui64 suffix, doesn't meet the test requirements.
	 * So set the sipa_eth net_device's type to ARPHRD_NONE here. ARPHRD_NONE can meet
	 * clat464 and use GSNN suffix.
	 */
	netdev->type = ARPHRD_NONE;
	netdev->flags |= IFF_NOARP;

	sipa_eth = netdev_priv(netdev);
	sipa_eth->netdev = netdev;
	sipa_eth->pdata = pdata;
	sipa_eth->stats = &netdev->stats;
	netdev->netdev_ops = &sipa_eth_ops;
	netdev->watchdog_timeo = 1 * HZ;
	netdev->hard_header_len = 0;

	eth_hw_addr_random(netdev);

	netdev->hw_features |= NETIF_F_SG;
	netdev->hw_features |= NETIF_F_RXCSUM | NETIF_F_IP_CSUM |
		NETIF_F_IPV6_CSUM;
	netdev->features |= netdev->hw_features;

	/* Register new Ethernet interface */
	ret = register_netdev(netdev);
	if (ret) {
		pr_err("register_netdev() failed (%d)\n", ret);
		free_netdev(netdev);
		return ret;
	}

	sipa_eth->gro_enable = true;
	sipa_eth->state = DEV_OFF;
	/* Set link as disconnected */
	netif_carrier_off(netdev);
	platform_set_drvdata(pdev, sipa_eth);

	ret = sysfs_create_group(&pdev->dev.kobj, &sipa_eth_attribute_group);
	if (ret) {
		pr_err("sysfs_create_group err %d\n", ret);
		free_netdev(netdev);
		return ret;
	}
#ifdef CONFIG_DEBUG_FS
	sipa_eth_debugfs_mknod(root, (void *)sipa_eth);
#endif
	return 0;
}

/* Cleanup Ethernet device driver. */
static void sipa_eth_remove(struct platform_device *pdev)
{
	struct sipa_eth *sipa_eth = platform_get_drvdata(pdev);

	unregister_netdev(sipa_eth->netdev);
	free_netdev(sipa_eth->netdev);
	platform_set_drvdata(pdev, NULL);

	return;
}

static const struct of_device_id sipa_eth_match_table[] = {
	{ .compatible = "sprd,sipa_eth"},
	{ }
};

static struct platform_driver sipa_eth_driver = {
	.probe = sipa_eth_probe,
	.remove = sipa_eth_remove,
	.driver = {
		.owner = THIS_MODULE,
		.name = SIPA_ETH_IFACE_PREF,
		.of_match_table = sipa_eth_match_table
	}
};

#ifdef CONFIG_DEBUG_FS
static int sipa_eth_debug_show(struct seq_file *m, void *v)
{
	struct sipa_eth *sipa_eth = (struct sipa_eth *)(m->private);
	struct net_device_stats *stats;
	struct sipa_eth_init_data *pdata;

	if (!sipa_eth) {
		pr_err("invalid data, sipa_eth is NULL\n");
		return -EINVAL;
	}
	pdata = sipa_eth->pdata;
	stats = sipa_eth->stats;

	seq_puts(m, "*************************************************\n");
	seq_printf(m, "DEVICE: %s, src_id %d, netid %d, state %s\n",
		   pdata->name, pdata->src_id, pdata->netid,
		   sipa_eth->state == DEV_ON ? "UP" : "DOWN");

	seq_printf(m, "SG feature %s\n",
		   sipa_eth->netdev->hw_features & NETIF_F_SG ? "on" : "off");

	seq_puts(m, "\nRX statistics:\n");
	seq_printf(m, "rx_bytes=%lu, rx_packets=%lu\n",
		   stats->rx_bytes,
		   stats->rx_packets);
	seq_printf(m, "rx_errors=%lu, rx_dropped=%lu\n",
		   stats->tx_errors, stats->rx_dropped);

	seq_puts(m, "\nTX statistics:\n");
	seq_printf(m, "tx_bytes=%lu, tx_packets=%lu\n",
		   stats->tx_bytes,
		   stats->tx_packets);
	seq_printf(m, "tx_errors=%lu, tx_dropped=%lu\n",
		   stats->tx_errors, stats->tx_dropped);

	seq_printf(m, "flowctl=%s\n",
		   netif_queue_stopped(sipa_eth->netdev) ? "true" : "false");
	seq_puts(m, "*************************************************\n");

	return 0;
}

static int sipa_eth_debug_open(struct inode *inode, struct file *file)
{
	return single_open(file, sipa_eth_debug_show, inode->i_private);
}

static const struct file_operations sipa_eth_debug_fops = {
	.open = sipa_eth_debug_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int sipa_eth_debugfs_mknod(void *root, void *data)
{
	struct sipa_eth *sipa_eth = (struct sipa_eth *)data;
	struct dentry *subroot;

	if (!sipa_eth)
		return -ENODEV;

	if (!root)
		return -ENXIO;
	subroot = debugfs_create_dir(sipa_eth->netdev->name,
				     (struct dentry *)root);
	if (!subroot)
		return -ENOMEM;

	debugfs_create_file("stats",
			    0444,
			    subroot,
			    data,
			    &sipa_eth_debug_fops);
	return 0;
}
#endif

static void __init sipa_eth_debugfs_init(void)
{
#ifdef CONFIG_DEBUG_FS
	root = debugfs_create_dir(SIPA_ETH_IFACE_PREF, NULL);
	if (!root)
		pr_err("failed to create sipa_eth debugfs dir\n");
#endif
}

static void sipa_eth_debugfs_remove(void)
{
#ifdef CONFIG_DEBUG_FS
	debugfs_remove_recursive(root);
#endif
}

static int __init sipa_eth_init(void)
{
	sipa_eth_debugfs_init();
	spin_lock_init(&queue_lock);
	return platform_driver_register(&sipa_eth_driver);
}

static void __exit sipa_eth_exit(void)
{
	sipa_eth_debugfs_remove();
	platform_driver_unregister(&sipa_eth_driver);
}

module_init(sipa_eth_init);
module_exit(sipa_eth_exit);

MODULE_AUTHOR("Wade.Shu");
MODULE_DESCRIPTION("Unisoc Ethernet device driver for IPA");
MODULE_LICENSE("GPL");
