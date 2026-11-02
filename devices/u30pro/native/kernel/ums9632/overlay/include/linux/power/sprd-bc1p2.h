/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Copyright (C) 2022 Unisoc Co., Ltd.
 * Chen Yongzhi <yongzhi.chen@unisoc.com>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 */
#ifndef __LINUX_SPRD_SPRD_BC1P2_INCLUDED
#define __LINUX_SPRD_SPRD_BC1P2_INCLUDED

#include <linux/delay.h>
#include <linux/kthread.h>
#include <linux/regmap.h>
#include <linux/usb/phy.h>
#include <uapi/linux/sched/types.h>
#include <uapi/linux/usb/charger.h>

#define SC2720_CHARGE_STATUS		0xe14
#define SC2720_CHG_DET_FGU_CTRL		0xe18
#define SC2721_CHARGE_STATUS		0xec8
#define SC2721_CHG_DET_FGU_CTRL		0xecc
#define SC2730_CHARGE_STATUS		0x1b9c
#define SC2730_CHG_DET_FGU_CTRL		0x1ba0
#define SC2731_CHARGE_STATUS		0xedc
#define SC2731_CHG_DET_FGU_CTRL		0xed8
#define UMP9620_CHARGE_STATUS		0x239c
#define UMP9620_CHG_DET_FGU_CTRL	0x23a0
#define UMP9620_CHG_BC1P2_CTRL2		0x243c
#define UMP9651_CHARGE_STATUS		0x2104
#define UMP9651_CHG_DET_FGU_CTRL	0x2108
#define UMP9651_CHG_BC1P2_CTRL2		0x2194
#define UMP518_CHG_BC1P2_CTRL2		0x1d24
#define UMP9360_CHARGE_STATUS		0x22c4
#define UMP9360_CHG_DET_FGU_CTRL	0x22c8
#define UMP9360_CHG_TOP			0x23a4
#define UMP9360_CHG_BC1P2_CTRL2		0x23ac
#define UMP9360_MSI_CORE_DEBUG2		0x2480
#define UMP9360_MSI_CORE_DEBUG3		0x2484
#define UMP9360_MSI_RTC_DEBUG0		0x2494

#define CHARGER_DETECT_DONE		BIT(0)

#define BIT_CHG_DET_DONE		BIT(11)
#define BIT_SDP_INT			BIT(7)
#define BIT_DCP_INT			BIT(6)
#define BIT_CDP_INT			BIT(5)
#define BIT_CHGR_INT			BIT(2)
#define UMP9360_CHG_DET_DONE		BIT(5)
#define UMP9360_SDP_INT			BIT(4)
#define UMP9360_DCP_INT			BIT(3)
#define UMP9360_CDP_INT			BIT(2)

#define SC27XX_CHG_INT_DELAY_MASK	GENMASK(11, 9)
#define SC27XX_CHG_INT_DELAY_OFFSET	9
#define UMP96XX_CHG_INT_DELAY_MASK	GENMASK(11, 8)
#define UMP96XX_CHG_INT_DELAY_OFFSET	8
#define UMP9360_CHG_INT_DELAY_MASK	GENMASK(11, 8)
#define UMP9360_CHG_INT_DELAY_OFFSET	8

#define UMP9360_CHG_REDET_DELAY_MASK	GENMASK(5, 2)
#define UMP9360_CHG_REDET_DELAY_OFFSET	2
#define UMP9360_CHG_BC1P2_EN_MASK	GENMASK(3, 3)
#define UMP9360_CHG_BC1P2_ENABLE	0x8
#define UMP9360_CHG_BC1P2_DISABLE	0
#define UMP9360_CHG_BC1P2_ENABLE_DELAY	2
#define SPRD_CHG_REDET_DELAY_MASK	GENMASK(7, 4)
#define SPRD_CHG_REDET_DELAY_OFFSET	4
#define SPRD_CHG_DET_EB_MASK		GENMASK(0, 0)
#define SPRD_CHG_BC1P2_REDET_ENABLE	1
#define SPRD_CHG_BC1P2_REDET_DISABLE	0

#define SPRD_CHG_VBUS_DEBOUNCE		250
#define SPRD_CHG_INT_DELAY_MS		64
#define SPRD_CHG_BC1P2_DETECT_TIME	680
#define SPRD_CHG_DET_POLL_DELAY		20
#define SPRD_CHG_VBUS_POLL_COUNT	13
#define SPRD_CHG_DET_DELAY_STEP_MS	64
#define SPRD_CHG_REDET_DELAY_MS_MAX	960
#define SPRD_CHG_REDET_DELAY_MS_MIN	256
#define SPRD_CHG_REDET_DELAY_MS		960

#define SPRD_CHG_MAX_REDETECT_COUNT	5
#define SPRD_CHG_DETECT_DELAY		(msecs_to_jiffies(500))
#define SPRD_CHG_WAIT_SET_DMDP		(msecs_to_jiffies(500))

#define SPRD_ERROR_NO_ERROR		0
#define SPRD_ERROR_REGMAP_UPDATE	1
#define SPRD_ERROR_REGMAP_READ		2
#define SPRD_ERROR_CHARGER_INIT		3
#define SPRD_ERROR_CHARGER_DETDONE	4
#define DEFAULT_CUR_MIN	66

#define PMIC_SC2720			1
#define PMIC_SC2730			2
#define PMIC_UMP9620			3
#define PMIC_UMP9651			4
#define PMIC_UMP9360			5

enum sprd_chg_detect_state {
	CHG_STATE_INITIALIZE = 0,
	CHG_STATE_UNDETECT,
	CHG_STATE_DETECT,
	CHG_STATE_DETECTED,
	CHG_STATE_RETRY_DETECT,
	CHG_STATE_RETRY_DETECTED,
};

struct sprd_bc1p2_data {
	u32 charge_status;
	u32 chg_det_fgu_ctrl;
	u32 chg_bc1p2_ctrl2;
	u32 chg_int_delay_mask;
	u32 chg_int_delay_offset;
	u32 id;
};

struct sprd_bc1p2_priv {
	struct device *dev;
	const struct sprd_bc1p2_data *data;
	struct regmap *regmap;
	struct notifier_block	vbus_nb;
	struct usb_phy *phy;
	struct kthread_worker bc1p2_kworker;
	struct kthread_delayed_work usb_change_kwork;
	struct kthread_delayed_work bc1p2_kwork;
	struct task_struct *bc1p2_thread;
	struct completion dpdm_high_resistance;
	struct mutex bc1p2_lock;
	spinlock_t vbus_event_lock;
	int chg_detect_poll_count;
	int chg_detect_all_count;
	int chg_redet_poll_count;
	int retry_chg_detect_count;
	unsigned int flags;
	enum usb_charger_type type;
	enum sprd_chg_detect_state detect_state;
	bool is_bc1p2_parallel;
	bool vbus_events;
	bool rework;
	bool shutdown;
	bool is_dpdm_high_resistance;
	bool redetect_enable;
};

#if IS_ENABLED(CONFIG_UNISOC_BC1P2)
extern int usb_add_bc1p2_init(struct usb_phy *x, bool is_bc1p2_parallel);

extern enum usb_charger_type sprd_bc1p2_charger_detect(struct usb_phy *x);
extern enum usb_charger_type sprd_bc1p2_retry_detect(struct usb_phy *x, int times,
						     bool is_need_vbus_check);

#else
static inline int usb_add_bc1p2_init(struct usb_phy *x, bool is_bc1p2_parallel)
{
	return 0;
}

static inline enum usb_charger_type sprd_bc1p2_charger_detect(struct usb_phy *x)
{
	return UNKNOWN_TYPE;
}

static inline enum usb_charger_type sprd_bc1p2_retry_detect(struct usb_phy *x, int times,
							    bool is_need_vbus_check)
{
	return UNKNOWN_TYPE;
}

#endif

#endif
