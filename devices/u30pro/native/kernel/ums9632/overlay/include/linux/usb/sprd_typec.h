/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * sprd_tcpm.h - Unisoc platform header
 *
 * Copyright 2022 Unisoc(Shanghai) Technologies Co.Ltd
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * version 2 as published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 */

#ifndef __SPRD_TYPEC_H__
#define __SPRD_TYPEC_H__

enum sc27xx_typec_pd_swap {
	TYPEC_NO_SWAP,
	TYPEC_SOURCE_TO_SINK,
	TYPEC_SINK_TO_SOURCE,
	TYPEC_HOST_TO_DEVICE,
	TYPEC_DEVICE_TO_HOST,
};


#define	EXTCON_SINK         3
#define	EXTCON_SOURCE       4
#define EXTCON_RP_VALUE_CHANGE		13

#if IS_ENABLED(CONFIG_UNISOC_USB_SC27XX_TYPEC)
extern int sc27xx_get_dr_swap_executing(void);
extern int sc27xx_get_current_status_detach_or_attach(void);
#else
static inline int sc27xx_get_dr_swap_executing(void)
{
	return 0;
}
static inline int sc27xx_get_current_status_detach_or_attach(void)
{
	return 0;
}
#endif /* IS_ENABLED(CONFIG_UNISOC_USB_SC27XX_TYPEC) */
#endif /* __LINUX_USB_SPRD_TYPEC_H */
