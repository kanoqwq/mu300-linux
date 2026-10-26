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

#ifndef __SDIO_H__
#define __SDIO_H__

#include <linux/spinlock.h>
#include <linux/types.h>
#include <linux/wait.h>
#include <linux/workqueue.h>
#include <misc/wcn_bus.h>
#include <linux/pm_qos.h>

#include "common/hif.h"

#include "sc2355_intf.h"

#define SDIO_RX_CMD_PORT	22
#define SDIO_RX_PKT_LOG_PORT	23
/*use port 24 because fifo_len = 8*/
#define SDIO_RX_DATA_PORT	24
#define SDIO_TX_CMD_PORT	8
/*use port 10 because fifo_len = 8*/
#define SDIO_TX_DATA_PORT	10



#define DISABLE_PD_THRESHOLD (25 * 0x100000)  //200Mbit/s  or 25Mbyte/s
#define SET_UCLAMP_THRESHOLD (25 * 0x100000)  //200Mbit/s  or 25Mbyte/s

struct throughput_sta {
	unsigned long tx_bytes;
	unsigned long last_time;
	unsigned long rx_bytes;
	unsigned long rx_last_time;
	unsigned long throughput_rx;
	unsigned long throughput_tx;
	bool disable_pd_flag;
	bool uclamp_set_flag;
	struct  pm_qos_request pm_qos_request_idle;
};

struct sc2355_sdiohal_puh {
	unsigned int pad:6;
	unsigned int check_sum:1;
	unsigned int len:16;
	unsigned int eof:1;
	unsigned int subtype:4;
	unsigned int type:4;
};/* 32bits public header */
extern struct throughput_sta throughput_static;
#endif /* __SDIO_H__ */
