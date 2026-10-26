/*
 * Copyright (C) 2015 Spreadtrum Communications Inc.
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

#ifndef __MTTY_H
#define __MTTY_H
//#include <soc/sprd/wcn_bus.h>
#include <misc/mchn.h>

enum mtty_log_level {
	MTTY_LOG_LEVEL_NONE,
	MTTY_LOG_LEVEL_VER,
};
struct mtty_init_data {
    char *name;
};

#define BT_TX_CHANNEL0    1
#define BT_TX_CHANNEL1    4
#define BT_RX_CHANNEL     2
#define BT_TX_INOUT    1
#define BT_RX_INOUT     0
#define BT_TX_POOL_SIZE0   64  // the max buffer is 64
#define BT_TX_POOL_SIZE1   64
#define BT_RX_POOL_SIZE   1
#define BT_SDIO_HEAD_LEN   0
#define BT_RX_MAX_NUM 4
#define BT_RX_DMA_SIZE 2048

#endif
