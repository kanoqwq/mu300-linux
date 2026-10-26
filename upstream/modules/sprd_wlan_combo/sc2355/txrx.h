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

#ifndef __TXRX_H__
#define __TXRX_H__

#include "rx.h"
#include "tx.h"

#define SPRD_SEND_DATA_OFFSET	(0)

void sc2355_tcp_ack_filter_rx(struct sprd_priv *priv, unsigned char *buf,
			      unsigned int plen);
/* return val: 0 for not fileter, 1 for fileter */
int sc2355_tcp_ack_filter_send(struct sprd_priv *priv, struct sprd_msg *msg,
			       unsigned char *buf, unsigned int plen);
void sc2355_tcp_ack_move_msg(struct sprd_priv *priv, struct sprd_msg *msg);

#endif
