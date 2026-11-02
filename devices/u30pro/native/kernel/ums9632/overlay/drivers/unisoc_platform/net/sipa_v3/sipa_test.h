/* SPDX-License-Identifier: GPL-2.0 */
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

#ifndef _SIPA_TEST_H
#define _SIPA_TEST_H

#include <soc/unisoc/sipa.h>
#include "sipa_priv.h"

#define SIPA_LTP_CASE_1	(1)
#define SIPA_LTP_CASE_2	(2)

int sipa_test_init(void);
void sipa_test_exit(void);

#endif
