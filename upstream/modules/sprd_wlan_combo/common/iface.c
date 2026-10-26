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

#include <linux/ctype.h>
#include <linux/moduleparam.h>
#include <linux/reboot.h>
#include <linux/workqueue.h>
#include <misc/wcn_bus.h>
#include <linux/miscdevice.h>

#include "cfg80211.h"
#include "chip_ops.h"
#include "cmd.h"
#include "common.h"
#include "delay_work.h"
#include "hif.h"
#include "iface.h"
#include "msg.h"
#include "npi.h"
#include "qos.h"
#include "report.h"
#include "tcp_ack.h"

static struct sprd_priv *sprd_prv;

void sprd_put_vif(struct sprd_vif *vif)
{
	if (vif) {
		spin_lock_bh(&vif->priv->list_lock);
		vif->ref--;
		spin_unlock_bh(&vif->priv->list_lock);
	}
}
EXPORT_SYMBOL(sprd_put_vif);

struct sprd_vif *sprd_mode_to_vif(struct sprd_priv *priv, u8 vif_mode)
{
	struct sprd_vif *vif, *found = NULL;

	spin_lock_bh(&priv->list_lock);
	list_for_each_entry(vif, &priv->vif_list, vif_node) {
		if (vif->mode == vif_mode) {
			vif->ref++;
			found = vif;
			break;
		}
	}
	spin_unlock_bh(&priv->list_lock);

	return found;
}
EXPORT_SYMBOL(sprd_mode_to_vif);

static void iface_set_priv(struct sprd_priv *priv)
{
	sprd_prv = priv;
}

static struct sprd_priv *iface_get_priv(void)
{
	return sprd_prv;
}

static void iface_str2mac(const char *mac_addr, u8 *mac)
{
	unsigned int m[ETH_ALEN];

	if (sscanf(mac_addr, "%02x:%02x:%02x:%02x:%02x:%02x",
		   &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) != ETH_ALEN) {
		pr_err("failed to parse mac address '%s'", mac_addr);
		memset(m, 0, sizeof(unsigned int) * ETH_ALEN);
	}
	mac[0] = m[0];
	mac[1] = m[1];
	mac[2] = m[2];
	mac[3] = m[3];
	mac[4] = m[4];
	mac[5] = m[5];
}

static int wlan_open(struct inode *inode, struct file *filep)
{
	return 0;
}

static ssize_t wlan_read_data(struct file *filp, char __user *buf,
			     size_t count, loff_t *pos)
{
	return 0;
}

static int wlan_release(struct inode *inode, struct file *filep)
{
	return 0;
}

static const struct file_operations wlan_misc_fops = {
	.owner = THIS_MODULE,
	.open = wlan_open,
	.read = wlan_read_data,
	.release = wlan_release,
};

static struct miscdevice wlan_misc_device = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "wlan",
	.fops = &wlan_misc_fops,
};

#ifndef DRV_RESET_SELF
/*
 * MU300: the FW_ERROR uevent from a work item. wcn_reset_notifier_list is an atomic notifier chain (RCU read side),
 * and kobject_uevent_env() allocates with GFP_KERNEL and may sleep; the vendor sent it from the notifier, and also
 * leaked the path kobject_get_path() allocated for its log line. The uevent is what reopens Wi-Fi after the
 * recovery watchdog's reset (see iface_wd_tick).
 */
static void iface_fw_error_uevent(struct work_struct *work)
{
	char *envp[3] = {
		[0] = "SOURCE=unisocwl",
		[1] = "EVENT=FW_ERROR",
		[2] = NULL,
	};

	kobject_uevent_env(&wlan_misc_device.this_device->kobj, KOBJ_CHANGE, envp);
	pr_err("%s: FW_ERROR uevent sent on %s\n", __func__, dev_name(wlan_misc_device.this_device));
}
static DECLARE_WORK(iface_fw_error_work, iface_fw_error_uevent);

static int iface_host_reset(struct notifier_block *nb,
			    unsigned long data, void *ptr)
{
	struct sprd_priv *priv = iface_get_priv();
	struct sprd_hif *hif;

	if (!priv) {
		pr_err("%s sprd_prv is NULL\n", __func__);
		return NOTIFY_OK;
	}

	hif = &priv->hif;
	hif->cp_asserted = 1;

	schedule_work(&iface_fw_error_work);
	sprd_chip_force_exit((void *)&priv->chip);

	return NOTIFY_OK;
}
#else
static int iface_host_reset(struct notifier_block *nb,
			    unsigned long data, void *ptr)
{
	struct sprd_priv *priv = iface_get_priv();
	struct sprd_hif *hif;
	struct sprd_cmd *cmd = &priv->cmd;

	if (!priv) {
		pr_err("%s sprd_prv is NULL\n", __func__);
		return NOTIFY_OK;
	}

	hif = &priv->hif;
	hif->cp_asserted = 1;
	complete(&cmd->completed);
	sprd_chip_force_exit((void *)&priv->chip);

	pr_info("%s process wifi driver self reset work\n", __func__);
	if (!work_pending(&priv->reset_work))
		queue_work(priv->reset_workq, &priv->reset_work);

	return NOTIFY_OK;
}
#endif

static struct notifier_block iface_host_reset_cb = {
	.notifier_call = iface_host_reset,
};

/*
 * MU300: recovery watchdog (issue #94). After a firmware assert our builds leave the WCN in card-dump state for
 * good (wcn_bsp's reset_dump defaults to "dump"): every mbuf_link_alloc() fails, Wi-Fi is dead, and until the RX
 * refill was given a back-off the printk flood soft-locked a CPU and softlockup_panic rebooted the device - which
 * is what brought Wi-Fi back. Without that, something else has to:
 *
 * - every second, Wi-Fi counts as stuck while the card-dump flag is set, or while the firmware is marked asserted
 *   (cp_asserted) with an interface open, i.e. the chip was reset but Wi-Fi has not been reopened yet (after our
 *   reset: until a power-on has succeeded, interface open or not);
 * - card dump for 10 s: reset the chip once per boot (wcn_mu300_recover(), what procfs "rebootwcn" does), in a work
 *   item of its own so that a reset that hangs cannot stall this one. The reset's FW_ERROR uevent (iface_host_reset)
 *   is userspace's cue to reopen Wi-Fi (OpenWrt: /etc/hotplug.d/misc/10-mu300-wcn), which powers the chip on again;
 * - still stuck 70 s after it began (the reset hung, or Wi-Fi was not reopened), or card dump again after the one
 *   reset of this boot: orderly_reboot(), unless recovery_reboot=0. Never worse than the panic it replaces.
 * It runs outside every Wi-Fi lock and only reads two flags.
 */
static bool recovery_reboot = true;
module_param(recovery_reboot, bool, 0644);
MODULE_PARM_DESC(recovery_reboot, "MU300: reboot when Wi-Fi stays stuck after the WCN reset (default on)");

#define IFACE_WD_PERIOD		HZ
#define IFACE_WD_RESET_AFTER	(10 * HZ)
#define IFACE_WD_REBOOT_AFTER	(70 * HZ)

static struct {
	struct delayed_work tick;
	struct work_struct reset;
	struct sprd_hif *hif;
	unsigned long stuck_since;
	bool stuck;
	bool reset_used;	/* the one chip reset of this boot */
	bool reset_this_time;	/* ... and it was for the current stuck spell */
	bool gave_up;
} iface_wd;

static void iface_wd_reset(struct work_struct *work)
{
	if (wcn_mu300_recover())
		pr_err("MU300 recovery: WCN reset done, Wi-Fi comes back when it is reopened\n");
	else
		pr_err("MU300 recovery: no WCN reset (see wcn_mu300_recover's line above)\n");
}

static void iface_wd_give_up(const char *why)
{
	iface_wd.gave_up = true;
	if (recovery_reboot) {
		pr_emerg("MU300 recovery: Wi-Fi stuck (%s)%s, rebooting\n", why,
			 work_busy(&iface_wd.reset) ? ", the WCN reset has not returned" : "");
		orderly_reboot();
	} else {
		pr_emerg("MU300 recovery: Wi-Fi stuck (%s); recovery_reboot=0, so it stays down\n", why);
	}
}

static void iface_wd_tick(struct work_struct *work)
{
	struct sprd_hif *hif = iface_wd.hif;
	bool dump = sprdwcn_bus_get_carddump_status() != 0;
	/* after our reset only a successful power-on clears cp_asserted (sc2355_reset): a reopen that fails is stuck too */
	bool asserted = READ_ONCE(hif->cp_asserted) &&
			(atomic_read(&hif->power_cnt) > 0 || iface_wd.reset_this_time);
	const char *why = dump ? "WCN in card dump" : "firmware asserted, Wi-Fi not reopened";
	unsigned long now = jiffies;

	if (!dump && !asserted) {
		if (iface_wd.stuck)
			pr_err("MU300 recovery: Wi-Fi is back after %u s\n",
			       jiffies_to_msecs(now - iface_wd.stuck_since) / 1000);
		iface_wd.stuck = false;
		iface_wd.reset_this_time = false;
		goto again;
	}
	if (!iface_wd.stuck) {
		iface_wd.stuck = true;
		iface_wd.stuck_since = now;
		pr_err("MU300 recovery: %s, watching\n", why);
	}
	if (iface_wd.gave_up)
		goto again;

	if (dump && time_after_eq(now, iface_wd.stuck_since + IFACE_WD_RESET_AFTER)) {
		if (!iface_wd.reset_used) {
			iface_wd.reset_used = true;
			iface_wd.reset_this_time = true;
			pr_err("MU300 recovery: %s for %u s, resetting the WCN (once per boot)\n", why,
			       jiffies_to_msecs(now - iface_wd.stuck_since) / 1000);
			queue_work(system_unbound_wq, &iface_wd.reset);
		} else if (!iface_wd.reset_this_time) {
			iface_wd_give_up("card dump again, this boot's one WCN reset is used");
			goto again;
		}
	}
	if (time_after_eq(now, iface_wd.stuck_since + IFACE_WD_REBOOT_AFTER))
		iface_wd_give_up(why);
again:
	queue_delayed_work(system_wq, &iface_wd.tick, IFACE_WD_PERIOD);
}

static void iface_wd_start(struct sprd_priv *priv)
{
	iface_wd.hif = &priv->hif;
	INIT_DELAYED_WORK(&iface_wd.tick, iface_wd_tick);
	INIT_WORK(&iface_wd.reset, iface_wd_reset);
	queue_delayed_work(system_wq, &iface_wd.tick, IFACE_WD_PERIOD);
}

static void iface_wd_stop(void)
{
	cancel_delayed_work_sync(&iface_wd.tick);
	cancel_work_sync(&iface_wd.reset);
}

static void iface_stop_net(struct sprd_vif *vif)
{
	struct sprd_vif *real_vif, *tmp_vif;
	struct sprd_priv *priv = vif->priv;

	spin_lock_bh(&priv->list_lock);
	list_for_each_entry_safe(real_vif, tmp_vif, &priv->vif_list, vif_node)
		if (real_vif->ndev)
			netif_stop_queue(real_vif->ndev);
	spin_unlock_bh(&priv->list_lock);
}

/* MU300: no Android /mnt/vendor/wifimac.txt; LK puts androidboot.wifimac=<12 hex digits> in /chosen/bootargs */
static bool iface_bootargs_mac(u8 *addr)
{
	struct device_node *chosen = of_find_node_by_path("/chosen");
	const char *args, *p;
	u8 mac[ETH_ALEN];
	int i;

	if (!chosen)
		return false;
	if (of_property_read_string(chosen, "bootargs", &args) ||
	    !(p = strstr(args, "androidboot.wifimac="))) {
		of_node_put(chosen);
		return false;
	}
	p += strlen("androidboot.wifimac=");
	for (i = 0; i < ETH_ALEN; i++, p += 2)
		if (hex2bin(&mac[i], p, 1)) {
			of_node_put(chosen);
			return false;
		}
	of_node_put(chosen);
	if (!is_valid_ether_addr(mac))
		return false;
	ether_addr_copy(addr, mac);
	return true;
}

static void iface_set_mac_addr(struct sprd_vif *vif, u8 *pending_addr,
			       u8 *addr)
{
	enum nl80211_iftype type = vif->wdev.iftype;
	struct sprd_priv *priv = vif->priv;

	if (!addr) {
		return;
	} else if (priv && (strncmp(vif->name, "wlan0", 5) == 0) &&
				is_valid_ether_addr(priv->default_mac)) {
		ether_addr_copy(addr, priv->default_mac);
	} else if (priv && (strncmp(vif->name, "wlan1", 5) == 0) &&
				is_valid_ether_addr(priv->default_mac_sta_second)) {
		ether_addr_copy(addr, priv->default_mac_sta_second);
	} else if (strncmp(vif->name, "wlan1", 5) != 0 && iface_bootargs_mac(addr)) {
		/* MU300: factory address that LK passes in /chosen/bootargs */
	} else {
		random_ether_addr(addr);
		netdev_warn(vif->ndev, "%s Warning: use random MAC address\n",
				__func__);
		/* initialize MAC addr with specific OUI */
		addr[0] = 0x40;
		addr[1] = 0x45;
		addr[2] = 0xda;
	}

	if (!priv) {
		netdev_err(vif->ndev, "%s get pirv failed\n", __func__);
		return;
	}
	switch (type) {
	case NL80211_IFTYPE_STATION:
	case NL80211_IFTYPE_AP:
		if (strncmp(vif->name, "wlan1", 5) == 0)
			ether_addr_copy(priv->default_mac_sta_second, addr);
		else
			ether_addr_copy(priv->default_mac, addr);
		break;
	case NL80211_IFTYPE_P2P_CLIENT:
		fallthrough;
	case NL80211_IFTYPE_P2P_GO:
		addr[4] ^= 0x80;
		fallthrough;
	case NL80211_IFTYPE_P2P_DEVICE:
		addr[0] ^= 0x02;
		break;
	default:
		break;
	}
}

int sprd_iface_set_power(struct sprd_hif *hif, int val)
{
	int ret = 0;

	/*
	 * MU300: power_cnt is atomic, the transitions behind it are not.  Probe powers the chip off after it
	 * registered wlan0, outside RTNL, so userspace (hostapd, netifd) can open the interface meanwhile: the
	 * open's power-on (1st count) ran start_marlin + post_init while the probe's power-off (last count) was
	 * still in post_deinit.  post_deinit then cleared the channels and sc2355_hif.hif that post_init had just
	 * set, stop_marlin powered the chip down, and the next RX interrupt read hif->rx_mgmt from NULL
	 * (pcie_rx_handle+0x34, "paging request at 00000000000030b8", FINDINGS 31n).  One transition at a time.
	 */
	mutex_lock(&hif->power_lock);
	if (val) {
		sprd_wlan_power_status_sync(1, 1);
		ret = sprd_hif_power_on(hif);
		if (ret) {
			sprd_wlan_power_status_sync(1, 0);
			if (ret == -ENODEV)
				pr_err("failed to power on WCN!\n");
			else if (ret == -EIO)
				pr_err("SYNC cmd error!\n");

			goto out;
		}
		if (atomic_read(&hif->power_cnt) == 1)
			sprd_get_fw_info(hif->priv);
	} else
		sprd_hif_power_off(hif);
out:
	mutex_unlock(&hif->power_lock);
	return ret;
}
#ifdef DRV_RESET_SELF
EXPORT_SYMBOL(sprd_iface_set_power);
#endif

static int iface_open(struct net_device *ndev)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_hif *hif = &vif->priv->hif;
	int ret;

	netdev_info(ndev, "%s\n", __func__);

	netdev_info(ndev, "Power on WCN (%d time)\n",
		    atomic_read(&hif->power_cnt));

	ret = sprd_iface_set_power(hif, true);
	if (ret)
		return ret;

	ret = sprd_init_fw(vif);
	/* hostapd enslaves the AP interface to a bridge right after opening it */
	if (vif->wdev.iftype == NL80211_IFTYPE_AP ||
	    vif->wdev.iftype == NL80211_IFTYPE_P2P_GO)
		ndev->priv_flags &= ~IFF_DONT_BRIDGE;

	if (!ret && vif->wdev.iftype == NL80211_IFTYPE_AP) {
		netif_carrier_off(ndev);
		return 0;
	}
	netif_start_queue(ndev);

	return 0;
}

static int iface_close(struct net_device *ndev)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_hif *hif = &vif->priv->hif;

	netdev_info(ndev, "%s\n", __func__);

	sprd_report_scan_done(vif, true);
	sprd_report_sched_scan_done(vif, true);
	netif_stop_queue(ndev);
	if (netif_carrier_ok(ndev))
		netif_carrier_off(ndev);

	/* hif->power_cnt = 1 means there is only one mode and
	 * stop_marlin will be called after closed.but it should
	 * not send any command between close and stop_marlin,
	 * block_cmd_after_close need set to 1 to block other cmd.
	 */
	if (atomic_read(&hif->power_cnt) == 1)
		atomic_set(&hif->block_cmd_after_close, 1);

	sprd_uninit_fw(vif);
	netdev_info(ndev, "Power off WCN (%d time)\n",
		    atomic_read(&hif->power_cnt));
	sprd_iface_set_power(hif, false);

	if (atomic_read(&hif->block_cmd_after_close) == 1)
		atomic_set(&hif->block_cmd_after_close, 0);

	return 0;
}

static void iface_netflowcontrl_mode(struct sprd_priv *priv,
				     enum sprd_mode mode, bool state)
{
	struct sprd_vif *vif;

	vif = sprd_mode_to_vif(priv, mode);
	if (vif) {
		if (state)
			netif_wake_queue(vif->ndev);
		else
			netif_stop_queue(vif->ndev);
		sprd_put_vif(vif);
	}
}

static void iface_netflowcontrl_all(struct sprd_priv *priv, bool state)
{
	struct sprd_vif *real_vif, *tmp_vif;

	spin_lock_bh(&priv->list_lock);
	list_for_each_entry_safe(real_vif, tmp_vif, &priv->vif_list, vif_node)
		if (real_vif->ndev) {
			if (state)
				netif_wake_queue(real_vif->ndev);
			else
				netif_stop_queue(real_vif->ndev);
		}
	spin_unlock_bh(&priv->list_lock);
}

/* @state: true for netif_start_queue, false for netif_stop_queue */
void sprd_net_flowcontrl(struct sprd_priv *priv, enum sprd_mode mode,
			 bool state)
{
	if (mode != SPRD_MODE_NONE)
		iface_netflowcontrl_mode(priv, mode, state);
	else
		iface_netflowcontrl_all(priv, state);
}
EXPORT_SYMBOL(sprd_net_flowcontrl);

void sprd_netif_rx(struct net_device *ndev, struct sk_buff *skb)
{
	struct sprd_vif *vif;
	struct sprd_hif *hif;

	vif = netdev_priv(ndev);
	hif = &vif->priv->hif;

	/* report sniffer monitor data packet */
	if (atomic_read(&vif->priv->monitor_mode)) {
		if (skb->len >= 64)
			print_hex_dump(KERN_WARNING, "RX sniffer data packet: ", DUMP_PREFIX_OFFSET,
				       16, 1, skb->data, 64, 0);
		else
			print_hex_dump(KERN_WARNING, "RX sniffer data packet: ", DUMP_PREFIX_OFFSET,
				       16, 1, skb->data, skb->len, 0);

		pr_info("sniffer data cnt: %d\n", vif->priv->monitor_data_cnt++);

		skb->dev = ndev;
		/* report data for sniffer mode */
		skb_set_mac_header(skb, 0);
		skb->ip_summed = CHECKSUM_UNNECESSARY;
		skb->pkt_type = PACKET_OTHERHOST;
		skb->protocol = htons(ETH_P_802_2);

		ndev->stats.rx_packets++;
		ndev->stats.rx_bytes += skb->len;

		local_bh_disable();
		netif_receive_skb(skb);
		local_bh_enable();

		return;
	}

	print_hex_dump_debug("RX packet: ", DUMP_PREFIX_OFFSET,
			     16, 1, skb->data, skb->len, 0);
	skb->dev = ndev;
	skb->protocol = eth_type_trans(skb, ndev);
	/* CHECKSUM_UNNECESSARY not supported by our hardware */
	/* skb->ip_summed = CHECKSUM_UNNECESSARY; */

	ndev->stats.rx_packets++;
	ndev->stats.rx_bytes += skb->len;
#if defined(MORE_DEBUG)
	hif->stats.rx_packets++;
	hif->stats.rx_bytes += skb->len;
	if (skb->pkt_type == PACKET_MULTICAST)
		hif->stats.rx_multicast++;
#endif

	/* to ensure data handled in netif in order */
	local_bh_disable();
	netif_receive_skb(skb);
	local_bh_enable();
}
EXPORT_SYMBOL(sprd_netif_rx);

/* report sniffer monitor mgmt frame */
void sprd_rx_monitor_process(struct sprd_vif *vif,
			     unsigned char *data, unsigned int len)
{
	struct sk_buff *skb;
	struct net_device *ndev;

	skb = dev_alloc_skb(len + NET_IP_ALIGN);
	if (!skb)
		return;

	if (len >= 64)
		print_hex_dump(KERN_WARNING, "RX sniffer frame: ", DUMP_PREFIX_OFFSET,
			       16, 1, data, 64, 0);
	else
		print_hex_dump(KERN_WARNING, "RX sniffer frame: ", DUMP_PREFIX_OFFSET,
			       16, 1, data, len, 0);

	pr_info("sniffer mgmt frame cnt: %d\n", vif->priv->monitor_mgmt_cnt++);

	ndev = vif->ndev;
	skb_reserve(skb, NET_IP_ALIGN);
	memcpy(skb->data, data, len);
	skb_put(skb, len);

	skb->dev = ndev;
	/* report data for monitor mode */
	skb_set_mac_header(skb, 0);
	skb->ip_summed = CHECKSUM_UNNECESSARY;
	skb->pkt_type = PACKET_OTHERHOST;
	skb->protocol = htons(ETH_P_802_2);

	ndev->stats.rx_packets++;
	ndev->stats.rx_bytes += skb->len;

	local_bh_disable();
	netif_receive_skb(skb);
	local_bh_enable();
}
EXPORT_SYMBOL(sprd_rx_monitor_process);

static int iface_prepare_xmit(struct sprd_vif *vif, struct net_device *ndev,
			      struct sk_buff *skb)
{
	struct sprd_hif *hif = &vif->priv->hif;

	/* drop nonlinearize skb */
	if (skb_linearize(skb)) {
		pr_err("nonlinearize skb\n");
		dev_kfree_skb(skb);
		ndev->stats.tx_dropped++;
		return -1;
	}

	if (hif->cp_asserted == 1 || unlikely(hif->exit)) {
		dev_kfree_skb(skb);
		iface_stop_net(vif);
		return -1;
	}

	if (hif->hw_type == SPRD_HW_SC2355_PCIE &&
		hif->suspend_mode != SPRD_PS_RESUMED) {
		pr_err("not resumed, drop skb\n");
		dev_kfree_skb(skb);
		return -1;
	}

	return sprd_chip_tx_prepare(&vif->priv->chip, skb);
}

static netdev_tx_t iface_start_xmit(struct sk_buff *skb, struct net_device *ndev)
{
	int ret = 0;
	u8 *data_temp;
	int offset;
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_hif *hif = &vif->priv->hif;
	struct sprd_msg *msg = NULL;
	struct sprd_eap_hdr *eap_temp;
	struct sk_buff *tmp_skb = skb;
	unsigned int skb_len;

	ret = iface_prepare_xmit(vif, ndev, skb);
	if (-1 == ret)
		goto out;

	data_temp = (u8 *)(skb->data) + sizeof(struct ethhdr);
	eap_temp = (struct sprd_eap_hdr *)data_temp;

	if (vif->mode == SPRD_MODE_P2P_GO &&
	    skb->protocol == cpu_to_be16(ETH_P_PAE) &&
	    eap_temp->type == EAP_PACKET_TYPE &&
	    eap_temp->code == EAP_FAILURE_CODE) {
		sprd_xmit_data2cmd(vif->priv, skb, ndev);
		return NETDEV_TX_OK;
	}

	/* Hardware tx data queue prority is lower than management queue
	 * management frame will be send out early even that get into queue
	 * after data frame.
	 * Workaround way: Put eap failure frame to high queue
	 * by use tx mgmt cmd
	 */
	/* send 802.1x or WAPI frame from cmd channel */
	ret = sprd_hif_tx_special_data(&vif->priv->hif, skb, ndev);
	if (ret == NETDEV_TX_OK || ret == NETDEV_TX_BUSY)
		return ret;

	/* do not send packet before connected */
	if (((vif->mode == SPRD_MODE_STATION || vif->mode == SPRD_MODE_STATION_SECOND) &&
	     vif->sm_state != SPRD_CONNECTED) ||
	    ((vif->mode != SPRD_MODE_STATION && vif->mode != SPRD_MODE_STATION_SECOND) &&
	     !(vif->state & VIF_STATE_OPEN))) {
		printk_ratelimited("%s, %d, error! should not send this data\n",
				   __func__, __LINE__);
		dev_kfree_skb(skb);
		return NETDEV_TX_OK;
	}

	msg = sprd_chip_get_msg(&vif->priv->chip, SPRD_TYPE_DATA, vif->mode);
	if (!msg) {
		ndev->stats.tx_fifo_errors++;
		return NETDEV_TX_BUSY;
	}

	if (skb_headroom(skb) < ndev->needed_headroom) {
		skb = skb_realloc_headroom(skb, ndev->needed_headroom);
		dev_kfree_skb(tmp_skb);
		if (!skb) {
			netdev_err(ndev,
				   "%s skb_realloc_headroom failed\n",
				   __func__);
			sprd_chip_free_msg(&vif->priv->chip, msg);
			goto out;
		}
	}

	offset = sprd_send_data_offset(vif->priv);
	sprd_hif_throughput_ctl_pd(hif, skb->len);
	skb_len = skb->len;
#ifdef CONFIG_SPRD_WLAN_DEBUG
	print_hex_dump_debug("TX packet: ", DUMP_PREFIX_OFFSET,
				 16, 1, skb->data, skb_len, 0);
#endif
	ret = sprd_send_data(vif->priv, vif, msg, skb, SPRD_DATA_TYPE_NORMAL,
			     offset, true);
	if (ret) {
		/* once per dropped frame while a client is gone or the queue is full: bounded (docs/FINDINGS.md 92) */
		if (net_ratelimit())
			netdev_err(ndev, "%s drop msg due to TX Err\n", __func__);
		/* FIXME
		 * as debug sdiom later, just drop the msg here
		 * wapi temp drop
		 */
		dev_kfree_skb(skb);
		sprd_chip_free_msg(&vif->priv->chip, msg);
		return NETDEV_TX_OK;
	}

	vif->ndev->stats.tx_bytes += skb_len;
	vif->ndev->stats.tx_packets++;
out:
	return NETDEV_TX_OK;
}

static struct net_device_stats *iface_get_stats(struct net_device *ndev)
{
	return &ndev->stats;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static void iface_tx_timeout(struct net_device *ndev, unsigned int txqueue)
#else
static void iface_tx_timeout(struct net_device *ndev)
#endif
{
	netdev_info(ndev, "%s\n", __func__);
	netif_wake_queue(ndev);
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static int iface_priv_cmd(struct net_device *ndev, void __user *data)
#else
static int iface_priv_cmd(struct net_device *ndev, struct ifreq *ifr)
#endif
{
	int n_clients;
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;
	struct android_wifi_priv_cmd priv_cmd;
	char *command = NULL, *country = NULL;
	u16 interval = 0;
	u8 feat = 0, status = 0;
	u8 addr[ETH_ALEN] = { 0 }, *mac_addr = NULL, *tmp, *mac_list;
	int ret = 0, skip, counter, index;
	#define MAC_ADDR_STR_LEN strlen("00:11:22:33:44:55")

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	if (!data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, data, sizeof(priv_cmd)))
		return -EFAULT;
#else
	if (!ifr->ifr_data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, ifr->ifr_data, sizeof(priv_cmd)))
		return -EFAULT;
#endif

	/* add length check to avoid invalid NULL ptr */
	if (priv_cmd.total_len <= 0) {
		netdev_info(ndev, "%s: priv cmd total len is invalid\n",
			    __func__);
		return -EINVAL;
	}

	command = kmalloc(priv_cmd.total_len, GFP_KERNEL);
	if (!command)
		return -ENOMEM;
	if (copy_from_user(command, priv_cmd.buf, priv_cmd.total_len)) {
		ret = -EFAULT;
		goto out;
	}

	if (!strncasecmp(command, CMD_BLACKLIST_ENABLE,
			 strlen(CMD_BLACKLIST_ENABLE))) {
		skip = strlen(CMD_BLACKLIST_ENABLE) + 1;
		if (priv_cmd.total_len < skip + MAC_ADDR_STR_LEN)
			goto out;
		iface_str2mac(command + skip, addr);
		if (!is_valid_ether_addr(addr))
			goto out;
		netdev_info(ndev, "%s: block %pM\n", __func__, addr);
		ret = sprd_set_blacklist(priv, vif, SUBCMD_ADD, 1, addr);
	} else if (!strncasecmp(command, CMD_BLACKLIST_DISABLE,
				strlen(CMD_BLACKLIST_DISABLE))) {
		skip = strlen(CMD_BLACKLIST_DISABLE) + 1;
		iface_str2mac(command + skip, addr);
		if (!is_valid_ether_addr(addr))
			goto out;
		netdev_info(ndev, "%s: unblock %pM\n", __func__, addr);
		ret = sprd_set_blacklist(priv, vif, SUBCMD_DEL, 1, addr);
	} else if (!strncasecmp(command, CMD_ADD_WHITELIST,
				strlen(CMD_ADD_WHITELIST))) {
		skip = strlen(CMD_ADD_WHITELIST) + 1;
		iface_str2mac(command + skip, addr);
		if (!is_valid_ether_addr(addr))
			goto out;
		netdev_info(ndev, "%s: add whitelist %pM\n", __func__, addr);
		ret = sprd_set_whitelist(priv, vif, SUBCMD_ADD, 1, addr);
	} else if (!strncasecmp(command, CMD_DEL_WHITELIST,
				strlen(CMD_DEL_WHITELIST))) {
		skip = strlen(CMD_DEL_WHITELIST) + 1;
		iface_str2mac(command + skip, addr);
		if (!is_valid_ether_addr(addr))
			goto out;
		netdev_info(ndev, "%s: delete whitelist %pM\n", __func__, addr);
		ret = sprd_set_whitelist(priv, vif, SUBCMD_DEL, 1, addr);
	} else if (!strncasecmp(command, CMD_ENABLE_WHITELIST,
				strlen(CMD_ENABLE_WHITELIST))) {
		skip = strlen(CMD_ENABLE_WHITELIST) + 1;
		counter = command[skip];
		if (counter < 0 || counter > 10) {
			netdev_err(ndev, "%s: enable whitelist counter is invalid: %d\n",
				   __func__, counter);
			goto out;
		}
		netdev_info(ndev, "%s: enable whitelist counter : %d\n",
			    __func__, counter);
		if (!counter) {
			ret = sprd_set_whitelist(priv, vif,
						 SUBCMD_ENABLE, 0, NULL);
			goto out;
		}
		if (priv_cmd.total_len < skip + counter * (MAC_ADDR_STR_LEN + 1))
			goto out;

		mac_addr = kmalloc(ETH_ALEN * counter, GFP_KERNEL);
		if (!mac_addr) {
			ret = -ENOMEM;
			goto out;
		}
		mac_list = mac_addr;

		tmp = command + skip + 1;
		for (index = 0; index < counter; index++) {
			iface_str2mac(tmp, mac_addr);
			if (!is_valid_ether_addr(mac_addr)) {
				kfree(mac_addr);
				goto out;
			}
			netdev_info(ndev, "%s: enable whitelist %pM\n",
				    __func__, mac_addr);
			mac_addr += ETH_ALEN;
			tmp += 18;
		}
		ret = sprd_set_whitelist(priv, vif,
					 SUBCMD_ENABLE, counter, mac_list);
		kfree(mac_list);
	} else if (!strncasecmp(command, CMD_DISABLE_WHITELIST,
				strlen(CMD_DISABLE_WHITELIST))) {
		skip = strlen(CMD_DISABLE_WHITELIST) + 1;
		counter = command[skip];
		if (counter < 0 || counter > 10) {
			netdev_err(ndev, "%s: disable whitelist counter is invalid: %d\n",
				   __func__, counter);
			goto out;
		}
		netdev_info(ndev, "%s: disable whitelist counter : %d\n",
			    __func__, counter);
		if (!counter) {
			ret = sprd_set_whitelist(priv, vif,
						 SUBCMD_DISABLE, 0, NULL);
			goto out;
		}
		if (priv_cmd.total_len < skip + counter * (MAC_ADDR_STR_LEN + 1))
			goto out;

		mac_addr = kmalloc(ETH_ALEN * counter, GFP_KERNEL);
		if (!mac_addr) {
			ret = -ENOMEM;
			goto out;
		}
		mac_list = mac_addr;

		tmp = command + skip + 1;
		for (index = 0; index < counter; index++) {
			iface_str2mac(tmp, mac_addr);
			if (!is_valid_ether_addr(mac_addr)) {
				kfree(mac_addr);
				goto out;
			}
			netdev_info(ndev, "%s: disable whitelist %pM\n",
				    __func__, mac_addr);
			mac_addr += ETH_ALEN;
			tmp += 18;
		}
		ret = sprd_set_whitelist(priv, vif,
					 SUBCMD_DISABLE, counter, mac_list);
		kfree(mac_list);
	} else if (!strncasecmp(command, CMD_11V_GET_CFG,
				strlen(CMD_11V_GET_CFG))) {
		/* deflaut CP support all featrue */
		if (priv_cmd.total_len < (strlen(CMD_11V_GET_CFG) + 4)) {
			ret = -ENOMEM;
			goto out;
		}
		memset(command, 0, priv_cmd.total_len);
		if (priv->fw_std & SPRD_STD_11V)
			feat = priv->wnm_ft_support;

		sprintf(command, "%s %d", CMD_11V_GET_CFG, feat);
		netdev_info(ndev, "%s: get 11v feat\n", __func__);
		if (copy_to_user(priv_cmd.buf, command, priv_cmd.total_len)) {
			netdev_err(ndev, "%s: get 11v copy failed\n", __func__);
			ret = -EFAULT;
			goto out;
		}
	} else if (!strncasecmp(command, CMD_11V_SET_CFG,
				strlen(CMD_11V_SET_CFG))) {
		skip = strlen(CMD_11V_SET_CFG) + 1;
		status = command[skip];

		netdev_info(ndev, "%s: 11v cfg %d\n", __func__, status);
		sprd_set_11v_feature_support(priv, vif, status);
	} else if (!strncasecmp(command, CMD_11V_WNM_SLEEP,
				strlen(CMD_11V_WNM_SLEEP))) {
		skip = strlen(CMD_11V_WNM_SLEEP) + 1;

		status = command[skip];
		if (status)
			interval = command[skip + 1];

		netdev_info(ndev, "%s: 11v sleep, status %d, interval %d\n",
			    __func__, status, interval);
		sprd_set_11v_sleep_mode(priv, vif, status, interval);
	} else if (!strncasecmp(command, CMD_SET_COUNTRY,
				strlen(CMD_SET_COUNTRY))) {
		skip = strlen(CMD_SET_COUNTRY) + 1;
		country = command + skip;

		if (!country || strlen(country) != SPRD_COUNTRY_CODE_LEN) {
			netdev_err(ndev, "%s: invalid country code\n",
				   __func__);
			ret = -EINVAL;
			goto out;
		}
		netdev_info(ndev, "%s country code:%c%c\n", __func__,
			    toupper(country[0]), toupper(country[1]));
		ret = regulatory_hint(priv->wiphy, country);
	} else if (!strncasecmp(command, CMD_SET_MAX_CLIENTS,
				strlen(CMD_SET_MAX_CLIENTS))) {
		skip = strlen(CMD_SET_MAX_CLIENTS) + 1;
		ret = kstrtou32(command + skip, 10, &n_clients);
		if (ret < 0) {
			ret = -EINVAL;
			goto out;
		}
		ret = sprd_set_max_clients_allowed(priv, vif, n_clients);
	} else if (!strncasecmp(command, CMD_BT_COEX_MODE,
				strlen(CMD_BT_COEX_MODE))) {
		netdev_info(ndev, "%s recieved command BTCOEXMODE", __func__);
		/* To pass vts test, for details, please see Bug 1881011*/
		ret = 0;
	} else if (!strncasecmp(command, CMD_BT_COEX_SCAN,
				strlen(CMD_BT_COEX_SCAN))) {
		netdev_info(ndev, "%s recieved command BTCOEXSCAN", __func__);
		/* To pass vts test, for details, please see Bug 1881011*/
		ret = 0;
	} else {
		netdev_err(ndev, "%s command not support\n", __func__);
		ret = -ENOTSUPP;
	}
out:
	kfree(command);
	return ret;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static int iface_set_power_save(struct net_device *ndev, void __user *data)
#else
static int iface_set_power_save(struct net_device *ndev, struct ifreq *ifr)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;
	struct android_wifi_priv_cmd priv_cmd;
	char *command = NULL;
	int ret = 0, skip, value;

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	if (!data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, data, sizeof(priv_cmd)))
		return -EFAULT;
#else
	if (!ifr->ifr_data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, ifr->ifr_data, sizeof(priv_cmd)))
		return -EFAULT;
#endif

	/* add length check to avoid invalid NULL ptr */
	if (priv_cmd.total_len <= 0) {
		netdev_err(ndev, "%s: priv cmd total len is invalid\n",
			   __func__);
		return -EINVAL;
	}

	command = kmalloc(priv_cmd.total_len, GFP_KERNEL);
	if (!command)
		return -ENOMEM;
	if (copy_from_user(command, priv_cmd.buf, priv_cmd.total_len)) {
		ret = -EFAULT;
		goto out;
	}

	if (!strncasecmp(command, CMD_SETSUSPENDMODE,
			 strlen(CMD_SETSUSPENDMODE))) {
		skip = strlen(CMD_SETSUSPENDMODE) + 1;
		if (priv_cmd.total_len <= skip)
			goto out;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: set suspend mode,value : %d\n",
			    __func__, value);

		priv->is_screen_off = value;
		ret = sprd_power_save(priv, vif, SPRD_SCREEN_ON_OFF, value);
	} else if (!strncasecmp(command, CMD_SET_FCC_CHANNEL,
				strlen(CMD_SET_FCC_CHANNEL))) {
		skip = strlen(CMD_SET_FCC_CHANNEL) + 1;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: set fcc channel,value : %d\n",
			    __func__, value);
		ret = sprd_power_save(priv, vif, SPRD_SET_FCC_CHANNEL, value);
	} else if (!strncasecmp(command, CMD_SET_SAR,
				strlen(CMD_SET_SAR))) {
		skip = strlen(CMD_SET_SAR) + 1;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: set sar,value : %d\n",
			    __func__, value);
		ret = sprd_set_sar(priv, vif, SPRD_SET_SAR_ABSOLUTE, value);
	} else if (!strncasecmp(command, CMD_REDUCE_TX_POWER,
				strlen(CMD_REDUCE_TX_POWER))) {
		skip = strlen(CMD_REDUCE_TX_POWER) + 1;
		ret = kstrtoint(command + skip, 0, &value);
		if (ret)
			goto out;
		netdev_info(ndev, "%s: reduce tx power,value : %d\n",
			    __func__, value);
		ret = sprd_power_save(priv, vif, SPRD_SET_TX_POWER, value);
	} else {
		netdev_err(ndev, "%s command not support\n", __func__);
		ret = -ENOTSUPP;
	}
out:
	kfree(command);
	return ret;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static int iface_set_p2p_mac(struct net_device *ndev, void __user *data)
#else
static int iface_set_p2p_mac(struct net_device *ndev, struct ifreq *ifr)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;
	struct android_wifi_priv_cmd priv_cmd;
	char *command = NULL;
	int ret = 0;
	struct sprd_vif *tmp1, *tmp2;
	u8 addr[ETH_ALEN] = { 0 };
	#define P2P_MAC_SKIP_LEN 11

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	if (!data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, data, sizeof(priv_cmd)))
		return -EFAULT;
#else
	if (!ifr->ifr_data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, ifr->ifr_data, sizeof(priv_cmd)))
		return -EFAULT;
#endif

	/* add length check to avoid invalid NULL ptr */
	if (priv_cmd.total_len < P2P_MAC_SKIP_LEN + ETH_ALEN) {
		netdev_err(ndev, "%s: priv cmd total len is invalid\n",
			   __func__);
		return -EINVAL;
	}

	command = kmalloc(priv_cmd.total_len, GFP_KERNEL);
	if (!command)
		return -ENOMEM;
	if (copy_from_user(command, priv_cmd.buf, priv_cmd.total_len)) {
		ret = -EFAULT;
		goto out;
	}

	memcpy(addr, command + P2P_MAC_SKIP_LEN, ETH_ALEN);
	netdev_info(ndev, "p2p dev random addr is %pM\n", addr);
	if (is_multicast_ether_addr(addr)) {
		netdev_err(ndev, "%s invalid addr\n", __func__);
		ret = -EINVAL;
		goto out;
	} else if (is_zero_ether_addr(addr)) {
		netdev_info(ndev, "restore to vif addr if addr is zero\n");
		memcpy(addr, vif->mac, ETH_ALEN);
	}

	spin_lock_bh(&priv->list_lock);
	list_for_each_entry_safe_reverse(tmp1, tmp2, &priv->vif_list,
					 vif_node) {
		if (tmp1->mode == SPRD_MODE_P2P_DEVICE) {
			netdev_info(ndev,
				    "get p2p device, set addr for wdev\n");
			memcpy(tmp1->wdev.address, addr, ETH_ALEN);
			break;
		}
	}
	spin_unlock_bh(&priv->list_lock);

	if (!tmp1) {
		netdev_err(ndev, "%s Can not find p2p device\n", __func__);
		ret = -EFAULT;
		goto out;
	}

	ret = sprd_set_random_mac(tmp1->priv, tmp1,
				  SPRD_CONNECT_RANDOM_ADDR, addr);
	if (ret) {
		netdev_err(ndev, "%s set p2p mac cmd error\n", __func__);
		ret = -EFAULT;
		goto out;
	}

out:
	kfree(command);
	return ret;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static int iface_set_ndev_mac(struct net_device *ndev, void __user *data)
#else
static int iface_set_ndev_mac(struct net_device *ndev, struct ifreq *ifr)
#endif
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_hif *hif = &vif->priv->hif;
	struct android_wifi_priv_cmd priv_cmd;
	char *command = NULL;
	int ret = 0;
	u8 addr[ETH_ALEN] = { 0 };

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	if (!data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, data, sizeof(priv_cmd)))
		return -EFAULT;
#else
	if (!ifr->ifr_data)
		return -EINVAL;
	if (copy_from_user(&priv_cmd, ifr->ifr_data, sizeof(priv_cmd)))
		return -EFAULT;
#endif

	/* add length check to avoid invalid NULL ptr */
	if (priv_cmd.total_len < ETH_ALEN) {
		netdev_info(ndev, "%s: priv cmd total len is invalid\n",
			    __func__);
		return -EINVAL;
	}


	command = kmalloc(priv_cmd.total_len, GFP_KERNEL);
	if (!command)
		return -ENOMEM;
	if (copy_from_user(command, priv_cmd.buf, priv_cmd.total_len)) {
		ret = -EFAULT;
		goto out;
	}

	memcpy(addr, command, ETH_ALEN);
	netdev_info(ndev, "Device addr of '%s' is %pM\n", ndev->name, addr);
	if (is_multicast_ether_addr(addr) || is_zero_ether_addr(addr)) {
		netdev_err(ndev, "%s invalid addr\n", __func__);
		ret = -EINVAL;
		goto out;
	}

	eth_hw_addr_set(ndev, addr);
	ether_addr_copy(ndev->perm_addr, ndev->dev_addr);
	ether_addr_copy(vif->wdev.address, addr);
	ether_addr_copy(vif->priv->default_mac, addr);
	ether_addr_copy(vif->mac, addr);

	/* iface_register_netdev has generated an invalid address, and sent to
	 * cp2 by CMD_OPEN command, so it is necessary to update a
	 * correct
	 * netdevice address to cp2
	 */
	if (atomic_read(&hif->power_cnt) != 0) {
		netdev_info(ndev, "set nedv mac to cp2: %pM\n", addr);
		ret = sprd_set_random_mac(vif->priv, vif,
					  SPRD_CONNECT_RANDOM_ADDR,
					  addr);
		if (ret) {
			netdev_err(ndev, "%s set ndev mac error\n", __func__);
			ret = -EFAULT;
			goto out;
		}
	}

out:
	kfree(command);
	return ret;
}

#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
static int iface_ioctl(struct net_device *ndev, struct ifreq *req, void __user *data,  int cmd)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;

	switch (cmd) {
	case SPRDWLIOCTL:
	case SPRDWLSETCOUNTRY:
		return iface_priv_cmd(ndev, data);
	case SPRDWLSETMIRACAST:
		netdev_err(ndev, "for vts test %d\n", cmd);
		return sprd_set_miracast(priv, ndev, data);
	case SPRDWLSETFCC:
	case SPRDWLSETSUSPEND:
		return iface_set_power_save(ndev, data);
	case SPRDWLVOWIFI:
		return sprd_set_vowifi(priv, ndev, data);
	case SPRDWLSETP2PMAC:
		return iface_set_p2p_mac(ndev, data);
	case SPRDWLSETNDEVMAC:
		return iface_set_ndev_mac(ndev, data);
	default:
		netdev_err(ndev, "Unsupported IOCTL %d\n", cmd);
		return -ENOTSUPP;
	}
	return 0;
}
#else
static int iface_ioctl(struct net_device *ndev, struct ifreq *req, int cmd)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;

	switch (cmd) {
	case SPRDWLIOCTL:
	case SPRDWLSETCOUNTRY:
		return iface_priv_cmd(ndev, req);
	case SPRDWLSETMIRACAST:
		netdev_err(ndev, "for vts test %d\n", cmd);
		return sprd_set_miracast(priv, ndev, req);
	case SPRDWLSETFCC:
	case SPRDWLSETSUSPEND:
		return iface_set_power_save(ndev, req);
	case SPRDWLVOWIFI:
		return sprd_set_vowifi(priv, ndev, req);
	case SPRDWLSETP2PMAC:
		return iface_set_p2p_mac(ndev, req);
	case SPRDWLSETNDEVMAC:
		return iface_set_ndev_mac(ndev, req);
	case SPRDWLSNIFFER:
		return sprd_set_sniffer(priv, ndev, req);
	default:
		netdev_err(ndev, "Unsupported IOCTL %d\n", cmd);
		return -ENOTSUPP;
	}

	return 0;
}
#endif

static int iface_set_mac(struct net_device *dev, void *addr)
{
	struct sprd_vif *vif = netdev_priv(dev);
	struct sockaddr *sa = (struct sockaddr *)addr;
	struct sprd_hif *hif = &vif->priv->hif;
	int ret;

	if (!dev) {
		netdev_err(dev, "Invalid net device\n");
		return -EINVAL;
	}

	netdev_info(dev, "%s() receive mac: %pM\n", __func__, sa->sa_data);
	if (is_multicast_ether_addr(sa->sa_data)) {
		netdev_err(dev, "invalid, it is multicast addr: %pM\n",
			   sa->sa_data);
		return -EINVAL;
	}

	if (vif->wdev.iftype == NL80211_IFTYPE_STATION) {
		if (!is_zero_ether_addr(sa->sa_data)) {
			vif->has_rand_mac = true;
			memcpy(vif->random_mac, sa->sa_data, ETH_ALEN);
			eth_hw_addr_set(dev, sa->sa_data);
			if (atomic_read(&hif->power_cnt) != 0) {
				netdev_info(dev, "set random mac to cp2 : %pM\n", vif->random_mac);
				ret = sprd_set_random_mac(vif->priv, vif,
						  SPRD_CONNECT_RANDOM_ADDR,
						  vif->random_mac);
				if (ret) {
					netdev_err(dev, "%s set station random mac error\n", __func__);
					return -EFAULT;
				}
			}
		} else {
			vif->has_rand_mac = false;
			netdev_info(dev,
				    "need clear random mac for sta/softap\n");
			memset(vif->random_mac, 0, ETH_ALEN);
			eth_hw_addr_set(dev, vif->mac);
		}
	}

	if (vif->wdev.iftype == NL80211_IFTYPE_P2P_GO ||
	    vif->wdev.iftype == NL80211_IFTYPE_P2P_CLIENT) {
		if (!is_zero_ether_addr(sa->sa_data)) {
			netdev_info(dev, "%s vif-> mac : %pM\n", __func__,
				    vif->mac);
			if (ether_addr_equal(vif->mac, sa->sa_data)) {
				netdev_info(dev,
					    "equal to vif mac, no need set to cp\n");
				memset(vif->random_mac, 0, ETH_ALEN);
				eth_hw_addr_set(dev, vif->mac);
				vif->has_rand_mac = false;
				return 0;
			}

			netdev_info(dev, "set go/gc random mac addr\n");
			eth_hw_addr_set(dev, sa->sa_data);
			vif->has_rand_mac = true;
			memcpy(vif->random_mac, sa->sa_data, ETH_ALEN);

			ret = sprd_set_random_mac(vif->priv, vif,
						  SPRD_CONNECT_RANDOM_ADDR,
						  sa->sa_data);
			if (ret) {
				netdev_err(dev, "%s set p2p mac error\n",
					   __func__);
				return -EFAULT;
			}
		} else {
			netdev_info(dev, "%s clear mac for go/gc mode\n",
				    __func__);
			vif->has_rand_mac = false;
			memset(vif->random_mac, 0, ETH_ALEN);
			eth_hw_addr_set(dev, vif->mac);
		}
	}

	/* return success to pass vts test */
	return 0;
}

static bool iface_mac_addr_changed(struct net_device *ndev)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct netdev_hw_addr *ha;
	u8 mc_count, index;
	u8 *mac_addr;
	bool found;

	mc_count = netdev_mc_count(ndev);

	if (mc_count != vif->mc_filter->mac_num)
		return true;

	mac_addr = vif->mc_filter->mac_addr;
	netdev_for_each_mc_addr(ha, ndev) {
		found = false;
		for (index = 0; index < vif->mc_filter->mac_num; index++) {
			if (!memcmp(ha->addr, mac_addr, ETH_ALEN)) {
				found = true;
				break;
			}
			mac_addr += ETH_ALEN;
		}

		if (!found)
			return true;
	}
	return false;
}

static void iface_set_multicast(struct net_device *ndev)
{
	struct sprd_vif *vif = netdev_priv(ndev);
	struct sprd_priv *priv = vif->priv;
	struct sprd_work *work;
	struct netdev_hw_addr *ha;
	u8 mc_count;
	u8 *mac_addr;

	mc_count = netdev_mc_count(ndev);
	netdev_info(ndev, "%s multicast address num: %d\n", __func__, mc_count);
	if (mc_count > priv->max_mc_mac_addrs)
		return;

	vif->mc_filter->mc_change = false;
	if ((ndev->flags & IFF_MULTICAST) && (iface_mac_addr_changed(ndev))) {
		mac_addr = vif->mc_filter->mac_addr;
		netdev_for_each_mc_addr(ha, ndev) {
			netdev_info(ndev, "%s set mac: %pM\n", __func__,
				    ha->addr);
			if ((ha->addr[0] != 0x33 || ha->addr[1] != 0x33) &&
			    (ha->addr[0] != 0x01 || ha->addr[1] != 0x00 ||
			     ha->addr[2] != 0x5e || ha->addr[3] > 0x7f)) {
				netdev_info(ndev, "%s invalid addr\n",
					    __func__);
				return;
			}
			ether_addr_copy(mac_addr, ha->addr);
			mac_addr += ETH_ALEN;
		}
		vif->mc_filter->mac_num = mc_count;
		vif->mc_filter->mc_change = true;
	} else if (!(ndev->flags & IFF_MULTICAST) && vif->mc_filter->mac_num) {
		vif->mc_filter->mac_num = 0;
		vif->mc_filter->mc_change = true;
	}

	work = sprd_alloc_work(0);
	if (!work) {
		netdev_err(ndev, "%s out of memory\n", __func__);
		return;
	}
	work->vif = vif;
	work->id = SPRD_WORK_MC_FILTER;
	vif->mc_filter->subtype = SPRD_RX_MODE_MULTICAST;
	sprd_queue_work(vif->priv, work);
}

static struct net_device_ops sprd_netdev_ops = {
	.ndo_open = iface_open,
	.ndo_stop = iface_close,
	.ndo_start_xmit = iface_start_xmit,
	.ndo_get_stats = iface_get_stats,
	.ndo_tx_timeout = iface_tx_timeout,
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	.ndo_siocdevprivate = iface_ioctl,
#else
	.ndo_do_ioctl = iface_ioctl,
#endif
	.ndo_set_mac_address = iface_set_mac,
};

static int iface_inetaddr_event(struct notifier_block *this,
				unsigned long event, void *ptr)
{
	struct net_device *ndev;
	struct sprd_vif *vif;
	struct in_ifaddr *ifa = (struct in_ifaddr *)ptr;

	if (!ifa || !(ifa->ifa_dev->dev))
		return NOTIFY_DONE;

	if (ifa->ifa_dev->dev->netdev_ops != &sprd_netdev_ops)
		return NOTIFY_DONE;

	ndev = ifa->ifa_dev->dev;
	vif = netdev_priv(ndev);

	if (vif->wdev.iftype == NL80211_IFTYPE_STATION ||
	    vif->wdev.iftype == NL80211_IFTYPE_P2P_CLIENT) {
		netdev_info(ndev, "inetaddr event %ld\n", event);
		if (event == NETDEV_UP)
			sprd_notify_ip(vif->priv, vif, SPRD_IPV4,
				       (u8 *)&ifa->ifa_address);

		if (event == NETDEV_DOWN) {
			if (vif->priv->hif.hw_type != SPRD_HW_SC2355_PCIE)
				sprd_fc_add_share_credit(vif->priv, vif);

			sprd_qos_reset_wmmac_parameters(vif->priv);
			sprd_qos_reset_wmmac_ts_info(vif->priv);
			sprd_qos_init_default_map(vif->priv);
		}
	}

	return NOTIFY_DONE;
}

static struct notifier_block iface_inetaddr_cb = {
	.notifier_call = iface_inetaddr_event,
};

static int iface_inetaddr6_event(struct notifier_block *this,
				 unsigned long event, void *ptr)
{
	struct net_device *ndev;
	struct sprd_vif *vif;
	struct inet6_ifaddr *inet6_ifa = (struct inet6_ifaddr *)ptr;
	struct sprd_work *work;
	u8 *ipv6_addr;

	if (!inet6_ifa || !(inet6_ifa->idev->dev))
		return NOTIFY_DONE;

	if (inet6_ifa->idev->dev->netdev_ops != &sprd_netdev_ops)
		return NOTIFY_DONE;

	ndev = inet6_ifa->idev->dev;
	vif = netdev_priv(ndev);

	if (vif->wdev.iftype == NL80211_IFTYPE_STATION ||
	    vif->wdev.iftype == NL80211_IFTYPE_P2P_CLIENT) {
		if (event == NETDEV_UP) {
			work = sprd_alloc_work(SPRD_IPV6_ADDR_LEN);
			if (!work) {
				netdev_err(ndev, "%s out of memory\n",
					   __func__);
				return NOTIFY_DONE;
			}
			work->vif = vif;
			work->id = SPRD_WORK_NOTIFY_IP;
			ipv6_addr = (u8 *)work->data;
			memcpy(ipv6_addr, (u8 *)&inet6_ifa->addr,
			       SPRD_IPV6_ADDR_LEN);
			sprd_queue_work(vif->priv, work);
		}
	}
	return NOTIFY_DONE;
}

static struct notifier_block iface_inet6addr_cb = {
	.notifier_call = iface_inetaddr6_event,
};

static int iface_notify_init(struct sprd_priv *priv)
{
	int ret = 0;

	/*
	 * MU300 (from kanoqwq/mu300-linux): the WCN reset notifier last, and every step undone when a later one
	 * fails. The vendor registered the notifier first and returned on a failure with it (and the misc device,
	 * and the inetaddr notifier) still registered: the probe then freed priv, and the next WCN reset called
	 * iface_host_reset() on it. A reset before misc_register() also queued the FW_ERROR uevent for a misc
	 * device that had no this_device yet, and a failed misc_register() went on as if it had worked.
	 */
	ret = misc_register(&wlan_misc_device);
	if (ret) {
		pr_err("wlan misc dev register fail\n");
		return ret;
	}

	ret = register_inetaddr_notifier(&iface_inetaddr_cb);
	if (ret) {
		pr_err("%s failed to register inetaddr notifier(%d)!\n",
		       __func__, ret);
		goto err_misc;
	}

	if (priv->fw_capa & SPRD_CAPA_NS_OFFLOAD) {
		pr_info("\tIPV6 NS Offload supported\n");
		ret = register_inet6addr_notifier(&iface_inet6addr_cb);
		if (ret) {
			pr_err
			    ("%s failed to register inet6addr notifier(%d)!\n",
			     __func__, ret);
			goto err_inet;
		}
	}

	ret = atomic_notifier_chain_register(&wcn_reset_notifier_list,
					     &iface_host_reset_cb);
	if (!ret)
		return 0;

	if (priv->fw_capa & SPRD_CAPA_NS_OFFLOAD)
		unregister_inet6addr_notifier(&iface_inet6addr_cb);
err_inet:
	unregister_inetaddr_notifier(&iface_inetaddr_cb);
err_misc:
	misc_deregister(&wlan_misc_device);
	return ret;
}

static void iface_notify_deinit(struct sprd_priv *priv)
{
	/* MU300: no notifier call after this, then no uevent work left, before the misc device goes */
	atomic_notifier_chain_unregister(&wcn_reset_notifier_list,
					 &iface_host_reset_cb);
#ifndef DRV_RESET_SELF
	cancel_work_sync(&iface_fw_error_work);
#endif
	misc_deregister(&wlan_misc_device);
	unregister_inetaddr_notifier(&iface_inetaddr_cb);
	if (priv->fw_capa & SPRD_CAPA_NS_OFFLOAD)
		unregister_inet6addr_notifier(&iface_inet6addr_cb);
}

static void iface_init_vif(struct sprd_priv *priv, struct sprd_vif *vif,
			   const char *name)
{
	WARN_ON(strlen(name) >= sizeof(vif->name));

	strcpy(vif->name, name);
	vif->priv = priv;
	vif->sm_state = SPRD_DISCONNECTED;
	mutex_init(&vif->survey_lock);
	INIT_LIST_HEAD(&vif->survey_info_list);
	INIT_LIST_HEAD(&vif->scan_head_ptr);
}

static void iface_deinit_vif(struct sprd_vif *vif)
{
	int cnt = 0;
	unsigned long timeout = jiffies + msecs_to_jiffies(1000);

	sprd_report_scan_done(vif, true);
	sprd_report_sched_scan_done(vif, true);
	/* clear all the work in vif which is going to be removed */
	sprd_cancel_work(vif->priv, vif);

	if (vif->ref > 0) {
		do {
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
			usleep_range_state(2000, 2500, TASK_UNINTERRUPTIBLE);
#else
			usleep_range(2000, 2500);
#endif
			cnt++;
			if (time_after(jiffies, timeout)) {
				netdev_err(vif->ndev, "%s timeout cnt %d\n",
					   __func__, cnt);
				break;
			}
		} while (vif->ref > 0);
		netdev_dbg(vif->ndev, "cnt %d\n", cnt);
	}
	mutex_destroy(&vif->survey_lock);
}

static struct sprd_vif *iface_register_wdev(struct sprd_priv *priv,
					    const char *name,
					    enum nl80211_iftype type, u8 *addr)
{
	struct sprd_vif *vif;
	struct wireless_dev *wdev;

	vif = kzalloc(sizeof(*vif), GFP_KERNEL);
	if (!vif)
		return ERR_PTR(-ENOMEM);

	/* initialize vif stuff */
	iface_init_vif(priv, vif, name);

	/* initialize wdev stuff */
	wdev = &vif->wdev;
	wdev->wiphy = priv->wiphy;
	wdev->iftype = type;

	iface_set_mac_addr(vif, addr, wdev->address);
	pr_info("iface '%s'(%pM) type %d added\n", name, wdev->address, type);

	return vif;
}

static void iface_unregister_wdev(struct sprd_vif *vif)
{
	pr_info("iface '%s' deleted\n", vif->name);

	cfg80211_unregister_wdev(&vif->wdev);
	/* cfg80211_unregister_wdev use list_del_rcu to delete wdev,
	 * so we can not free vif immediately, must wait until an
	 * RCU grace period has elapsed.
	 */
	synchronize_rcu();
	iface_deinit_vif(vif);
	kfree(vif);
}

static struct sprd_vif *iface_register_netdev(struct sprd_priv *priv,
					      const char *name,
					      enum nl80211_iftype type,
					      u8 *addr)
{
	struct net_device *ndev;
	struct wireless_dev *wdev;
	struct sprd_vif *vif;
	int ret;

	ndev = alloc_netdev(sizeof(*vif), name, NET_NAME_UNKNOWN, ether_setup);
	if (!ndev) {
		pr_err("%s failed to alloc net_device!\n", __func__);
		return ERR_PTR(-ENOMEM);
	}

	/* initialize vif stuff */
	vif = netdev_priv(ndev);
	vif->ndev = ndev;
	iface_init_vif(priv, vif, name);

	/* initialize wdev stuff */
	wdev = &vif->wdev;
	wdev->netdev = ndev;
	wdev->wiphy = priv->wiphy;
	wdev->iftype = type;

	/* initialize ndev stuff */
	ndev->ieee80211_ptr = wdev;
	if (priv->fw_capa & SPRD_CAPA_MC_FILTER) {
		pr_info("\tMulticast Filter supported\n");
		vif->mc_filter =
		    kzalloc(sizeof(struct sprd_mc_filter) +
			    priv->max_mc_mac_addrs * ETH_ALEN, GFP_KERNEL);
		if (!vif->mc_filter) {
			ret = -ENOMEM;
			goto err;
		}

		sprd_netdev_ops.ndo_set_rx_mode = iface_set_multicast;
	}
	ndev->netdev_ops = &sprd_netdev_ops;
	ndev->priv_destructor = free_netdev;
	ndev->needed_headroom = sizeof(struct sprd_data_hdr) + NET_IP_ALIGN +
	    SPRD_SKB_HEAD_RESERV_LEN + sprd_hif_reserve_len(&priv->hif);
	ndev->watchdog_timeo = 2 * HZ;
	ndev->features |= priv->hif.feature;
	SET_NETDEV_DEV(ndev, wlan_misc_device.this_device);

	{
		u8 mac[ETH_ALEN];

		ether_addr_copy(mac, ndev->dev_addr);
		iface_set_mac_addr(vif, addr, mac);
		eth_hw_addr_set(ndev, mac);
	}
	memcpy(vif->mac, ndev->dev_addr, ETH_ALEN);

	/* register new Ethernet interface */
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	ret = cfg80211_register_netdevice(ndev);
#else
	ret = register_netdevice(ndev);
#endif
	if (ret) {
		netdev_err(ndev, "failed to regitster netdev(%d)!\n", ret);
		goto err;
	}

	pr_info("iface '%s'(%pM) type %d added\n",
		ndev->name, ndev->dev_addr, type);
	return vif;
err:
	iface_deinit_vif(vif);
	free_netdev(ndev);
	return ERR_PTR(ret);
}

static void iface_unregister_netdev(struct sprd_vif *vif)
{
	pr_info("iface '%s' deleted\n", vif->ndev->name);

	if (vif->priv->fw_capa & SPRD_CAPA_MC_FILTER)
		kfree(vif->mc_filter);
	iface_deinit_vif(vif);
#if (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 15, 0))
	cfg80211_unregister_netdevice(vif->ndev);
#else
	unregister_netdevice(vif->ndev);
#endif
}

struct wireless_dev *sprd_add_iface(struct sprd_priv *priv, const char *name,
				    enum nl80211_iftype type, u8 *addr)
{
	struct sprd_vif *vif;

	if (type == NL80211_IFTYPE_P2P_DEVICE)
		vif = iface_register_wdev(priv, name, type, addr);
	else
		vif = iface_register_netdev(priv, name, type, addr);

	if (IS_ERR(vif)) {
		pr_err("failed to add iface '%s'\n", name);
		return (void *)vif;
	}

	spin_lock_bh(&priv->list_lock);
	list_add_tail(&vif->vif_node, &priv->vif_list);
	spin_unlock_bh(&priv->list_lock);

	return &vif->wdev;
}

int sprd_del_iface(struct sprd_priv *priv, struct sprd_vif *vif)
{
	if (!vif->ndev)
		iface_unregister_wdev(vif);
	else
		iface_unregister_netdev(vif);

	return 0;
}

static void iface_del_all_ifaces(struct sprd_priv *priv)
{
	struct sprd_vif *vif, *tmp;

next_intf:
	spin_lock_bh(&priv->list_lock);
	list_for_each_entry_safe_reverse(vif, tmp, &priv->vif_list, vif_node) {
		list_del(&vif->vif_node);
		spin_unlock_bh(&priv->list_lock);
		rtnl_lock();
		sprd_del_iface(priv, vif);
		rtnl_unlock();
		goto next_intf;
	}

	spin_unlock_bh(&priv->list_lock);
}

static int iface_core_init(struct device *dev, struct sprd_priv *priv)
{
	struct wiphy *wiphy = priv->wiphy;
	struct wireless_dev *wdev;
	int ret;

	sprd_tcp_ack_init(priv);
	sprd_setup_wiphy(wiphy, priv);
	sprd_vendor_init(priv, wiphy);
	/*
	 * MU300: the phy's permanent address (/sys/class/ieee80211/phy0/macaddress) was all zeros. OpenWrt's wifi
	 * scripts derive the AP's address from it, and an address that differs from the one the firmware uses breaks
	 * the WPA handshake. The factory address from LK is what wlan0 gets as well (iface_set_mac_addr()); without
	 * one the phy keeps no address, as before, and wlan0 its random one.
	 * (After a community contribution, the u30air charge2 build.)
	 */
	if (!is_valid_ether_addr(priv->default_mac))
		iface_bootargs_mac(priv->default_mac);
	if (is_valid_ether_addr(priv->default_mac))
		ether_addr_copy(wiphy->perm_addr, priv->default_mac);
	set_wiphy_dev(wiphy, dev);
	ret = wiphy_register(wiphy);
	if (ret) {
		wiphy_err(wiphy, "failed to regitster wiphy(%d)!\n", ret);
		goto out;
	}
#ifdef CONFIG_PM
	/*
	 * MU300: WoWLAN "any" on by default. Without a WoWLAN configuration cfg80211's wiphy_suspend() closes every
	 * interface (cfg80211_leave_all), and the WCN PCI function, which suspends asynchronously, has usually taken
	 * the bus down by then: CMD_CLOSE and CMD_DISCONNECT were dropped ("fail because bus done"), the firmware kept
	 * them open, and the next close and open of Wi-Fi after the resume hit a firmware assert (mchn.c line 137)
	 * that only a reboot cleared (FINDINGS 37). With a configuration the interfaces stay as they are; the
	 * firmware is told about the sleep through the PCIe channel's power_notify, as before. "iw phy phy0 wowlan
	 * disable" brings the old behaviour back. wiphy_unregister() frees it.
	 */
	if (!wiphy->wowlan_config) {
		struct cfg80211_wowlan *wowlan = kzalloc(sizeof(*wowlan), GFP_KERNEL);

		if (wowlan) {
			wowlan->any = true;
			wiphy->wowlan_config = wowlan;
		}
	}
#endif

	rtnl_lock();
	wdev = sprd_add_iface(priv, "wlan%d", NL80211_IFTYPE_STATION, NULL);
	rtnl_unlock();
	if (IS_ERR(wdev)) {
		wiphy_unregister(wiphy);
		ret = -ENXIO;
		goto out;
	}

	sprd_init_npi();

	sprd_fcc_init(priv);

	sprd_qos_enable(priv, 1);

	sprd_debug_init(&priv->debug);
out:
	return ret;
}

static int iface_core_deinit(struct sprd_priv *priv)
{
	sprd_debug_deinit(&priv->debug);
	sprd_qos_enable(priv, 0);
	sprd_deinit_npi();
#ifdef DRV_RESET_SELF
	sprd_cancel_reset_work(priv);
#endif
	iface_del_all_ifaces(priv);
	sprd_vendor_deinit(priv, priv->wiphy);
	wiphy_unregister(priv->wiphy);
	sprd_tcp_ack_deinit(priv);

	return 0;
}

int sprd_iface_probe(struct platform_device *pdev,
		     struct sprd_hif_ops *hif_ops,
		     struct sprd_chip_ops *chip_ops)
{
	struct sprd_priv *priv;
	struct sprd_hif *hif;
	int ret;

	pr_info("Spreadtrum WLAN Driver (Ver. %s, %s)\n",
		SPRD_DRIVER_VERSION, utsname()->release);

	priv = sprd_core_create(chip_ops);
	if (!priv) {
		pr_err("%s core create fail\n", __func__);
		return -ENXIO;
	}

	iface_set_priv(priv);
	platform_set_drvdata(pdev, priv);
	hif = &priv->hif;
	mutex_init(&hif->power_lock);
	hif->priv = priv;
	hif->pdev = pdev;
	hif->ops = hif_ops;

	ret = sprd_hif_init(hif);
	if (ret) {
		pr_err("%s hif init failed: %d\n", __func__, ret);
		sprd_core_free(priv);
		return ret;
	}

	pr_info("Power on WCN (%d time)\n", atomic_read(&hif->power_cnt));
	ret = sprd_iface_set_power(hif, true);
	if (ret) {
		sprd_hif_deinit(hif);
		sprd_core_free(priv);
		return ret;
	}

	ret = iface_core_init(&pdev->dev, priv);
	if (ret) {
		pr_err("%s core init failed: %d\n", __func__, ret);
		/* MU300: power off while hif still exists (sprd_core_free frees priv, and hif with it) */
		sprd_iface_set_power(hif, false);
		sprd_hif_deinit(hif);
		sprd_core_free(priv);
		return ret;
	}

	ret = iface_notify_init(priv);
	if (ret) {
		pr_err("%s notify init failed: %d\n", __func__, ret);
		iface_core_deinit(priv);
		sprd_iface_set_power(hif, false);
		sprd_hif_deinit(hif);
		sprd_core_free(priv);
		return ret;
	}

	/* Power off chipset in order to save power */
	pr_info("Power off WCN (%d time)\n", atomic_read(&hif->power_cnt));
	sprd_iface_set_power(hif, false);

	/* MU300: the recovery watchdog (see iface_wd_tick) */
	iface_wd_start(priv);

	return ret;
}
EXPORT_SYMBOL(sprd_iface_probe);

int sprd_iface_remove(struct platform_device *pdev)
{
	struct sprd_priv *priv = platform_get_drvdata(pdev);
	struct sprd_hif *hif = &priv->hif;
	int ret;

	pr_info("%s\n wlan driver remove.", __func__);
	iface_wd_stop();	/* MU300: before hif goes */

	/*
	 * MU300 (issue #146): the vendor returned here when the power-on failed, with everything still registered;
	 * a platform remove cannot refuse (the device is unbound and the module freed either way), so the
	 * teardown goes on without the chip. The power-on only fails from a count of 0, i.e. with no interface
	 * open, so the close commands it would have carried are not needed and there is no power-off to balance.
	 */
	pr_info("Power on WCN (%d time)\n", atomic_read(&hif->power_cnt));
	ret = sprd_iface_set_power(hif, true);
	if (ret)
		pr_err("%s: WCN power-on failed (%d), removing without the chip\n", __func__, ret);

	iface_notify_deinit(priv);
	iface_core_deinit(priv);
	/* MU300: power off while hif still exists (sprd_core_free frees priv, and hif and its lock with it) */
	pr_info("Power off WCN (%d time)\n", atomic_read(&hif->power_cnt));
	if (!ret)
		sprd_iface_set_power(hif, false);
	sprd_hif_deinit(hif);
	sprd_core_free(priv);
	iface_set_priv(NULL);

	return 0;
}
EXPORT_SYMBOL(sprd_iface_remove);

MODULE_DESCRIPTION("Spreadtrum Wireless LAN Common Code");
MODULE_AUTHOR("Spreadtrum WCN Division");
MODULE_LICENSE("GPL");
