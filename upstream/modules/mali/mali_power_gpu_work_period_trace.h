/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
/*
 *
 * (C) COPYRIGHT 2020-2021 ARM Limited. All rights reserved.
 *
 * This program is free software and is provided to you under the terms of the
 * GNU General Public License version 2 as published by the Free Software
 * Foundation, and any use by you of this program is subject to the terms
 * of such GNU license.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, you can access it online at
 * http://www.gnu.org/licenses/gpl-2.0.html.
 *
 */

#ifndef _TRACE_POWER_GPU_WORK_PERIOD_MALI
#define _TRACE_POWER_GPU_WORK_PERIOD_MALI
#endif

#undef TRACE_SYSTEM
#define TRACE_SYSTEM power
#undef TRACE_INCLUDE_FILE
#define TRACE_INCLUDE_FILE mali_power_gpu_work_period_trace
#undef TRACE_INCLUDE_PATH
#define TRACE_INCLUDE_PATH .

#if !defined(_TRACE_POWER_GPU_WORK_PERIOD_H) || defined(TRACE_HEADER_MULTI_READ)
#define _TRACE_POWER_GPU_WORK_PERIOD_H

#include <linux/tracepoint.h>

TRACE_EVENT(gpu_work_period,

	TP_PROTO(unsigned int gpu_id, unsigned int uid, unsigned long start_time_ns,
		unsigned long end_time_ns, unsigned long total_active_duration_ns),

	TP_ARGS(gpu_id, uid, start_time_ns, end_time_ns, total_active_duration_ns),

	TP_STRUCT__entry(
		__field(u32, gpu_id)
		__field(u32, uid)
		__field(u64, start_time_ns)
		__field(u64, end_time_ns)
		__field(u64, total_active_duration_ns)
	),

	TP_fast_assign(
		__entry->gpu_id                                = gpu_id;
		__entry->uid                                   = uid;
		__entry->start_time_ns                         = start_time_ns;
		__entry->end_time_ns                           = end_time_ns;
		__entry->total_active_duration_ns              = total_active_duration_ns;
	),

	TP_printk("gpu_id = %u uid = %u start_time_ns = %llu end_time_ns = %llu "
		"total_active_duration_ns = %llu",
		(unsigned int)__entry->gpu_id,
		(unsigned int)__entry->uid,
		(unsigned long)__entry->start_time_ns,
		(unsigned long)__entry->end_time_ns,
		(unsigned long)__entry->total_active_duration_ns)
);

#endif /* _TRACE_POWER_GPU_WORK_PERIOD_H */
/* This part must be outside protection */
#include <trace/define_trace.h>
