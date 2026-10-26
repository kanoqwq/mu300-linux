/* SPDX-License-Identifier: GPL-2.0 */
/* MU300: build the 5.4 vendor WCN drivers against a 6.18 kernel */
#ifndef _MU300_COMPAT_H
#define _MU300_COMPAT_H

#include <linux/version.h>
#include <linux/vmalloc.h>
#include <linux/proc_fs.h>
#include <linux/device.h>
#include <linux/of_platform.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_qos.h>
#include <linux/timer.h>
#include <linux/slab.h>

#define PDE_DATA(inode) pde_data(inode)

/* 5.4 had create+add / remove+destroy; 6.18 only exports register/unregister */
#define wakeup_source_create(name) wakeup_source_register(NULL, name)
#define wakeup_source_add(ws) do { } while (0)
#define wakeup_source_remove(ws) do { } while (0)
#define wakeup_source_destroy(ws) wakeup_source_unregister(ws)

/* class_create() lost its owner argument */
#define class_create(owner, name) class_create(name)

/* vm_map_ram() lost its pgprot argument */
#define vm_map_ram(pages, count, node, prot) vm_map_ram(pages, count, node)

/*
 * set_fs() is gone: kernel_read()/kernel_write() take kernel buffers directly. Callers that still pass kernel
 * buffers to vfs_read()/vfs_write() are converted in the sources.
 */
#define kzfree(p) kfree_sensitive(p)
#define del_timer(t) timer_delete(t)
#define del_timer_sync(t) timer_delete_sync(t)
#define from_timer(var, callback_timer, timer_fieldname) timer_container_of(var, callback_timer, timer_fieldname)

/* the CPU/DMA latency class is now its own API */
#define PM_QOS_CPU_DMA_LATENCY 0
#define PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE PM_QOS_CPU_LATENCY_DEFAULT_VALUE
#define pm_qos_add_request(req, class, value) cpu_latency_qos_add_request(req, value)
#define pm_qos_update_request(req, value) cpu_latency_qos_update_request(req, value)
#define pm_qos_remove_request(req) cpu_latency_qos_remove_request(req)

/* y2038: struct timespec is gone from the kernel */
#include <linux/timekeeping.h>
#define timespec timespec64
#define getnstimeofday(ts) ktime_get_real_ts64(ts)
#define timespec_to_ns(ts) timespec64_to_ns(ts)

/* sched_setscheduler() is no longer exported to modules: RT threads use sched_set_fifo() */
#include <linux/sched.h>
#define sched_setscheduler(p, policy, param) ({ (void)(param); sched_set_fifo(p); 0; })
#define sched_setattr(p, attr) ({ (void)(attr); sched_set_fifo(p); 0; })

#include <linux/etherdevice.h>
#define random_ether_addr(addr) eth_random_addr(addr)

/* Android-only nl80211 extension; WAPI is never offered by hostapd/wpa_supplicant here */
#define NL80211_WAPI_VERSION_1 (1 << 7)

#include <linux/panic_notifier.h>
#include <linux/string.h>
#include <linux/io.h>
#include <linux/hrtimer.h>
#include <linux/netdevice.h>
#include <net/netdev_rx_queue.h>
#define strlcpy(dst, src, size) strscpy(dst, src, size)
#define ioremap_nocache(addr, size) ioremap(addr, size)
#define devm_ioremap_nocache(dev, addr, size) devm_ioremap(dev, addr, size)
/* hrtimer_init() is gone; callers set ->function afterwards */
static inline enum hrtimer_restart mu300_hrtimer_nop(struct hrtimer *t) { return HRTIMER_NORESTART; }
#define hrtimer_init(timer, clock, mode) hrtimer_setup(timer, mu300_hrtimer_nop, clock, mode)
/* netif_napi_add() lost its weight argument */
#define netif_napi_add(dev, napi, poll, weight) netif_napi_add_weight(dev, napi, poll, weight)

/* Android-only virtio device id used by the Trusty IPC firmware resource table */
#ifndef VIRTIO_ID_TRUSTY_IPC
#define VIRTIO_ID_TRUSTY_IPC 13
#endif

typedef int mm_segment_t;
#define get_fs() 0
#define set_fs(x) ((void)(x))
#define KERNEL_DS 0
#define USER_DS 0

/*
 * 7.x: strncpy() is gone. Same semantics - copy up to n bytes, pad the rest with zeros, no guaranteed NUL - for
 * the vendor code that relies on them.
 */
#if LINUX_VERSION_CODE >= KERNEL_VERSION(7, 0, 0)
#include <linux/string.h>
static inline char *mu300_strncpy(char *dst, const char *src, size_t n)
{
	size_t i = 0;

	for (; i < n && src[i]; i++)
		dst[i] = src[i];
	for (; i < n; i++)
		dst[i] = '\0';
	return dst;
}
#define strncpy(dst, src, n) mu300_strncpy(dst, src, n)
#endif

/* 7.x: hex2bin() and friends moved to their own header */
#if __has_include(<linux/hex.h>)
#include <linux/hex.h>
#endif

/* 7.x: alarm_start()/alarm_restart() became alarm_start_timer(alarm, expires, relative) */
#if LINUX_VERSION_CODE >= KERNEL_VERSION(7, 0, 0)
#include <linux/alarmtimer.h>
#define alarm_start(a, t) alarm_start_timer((a), (t), false)
#define alarm_restart(a) alarm_start_timer((a), (a)->node.expires, false)
#endif

#endif
