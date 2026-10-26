#undef TRACE_SYSTEM
#define TRACE_SYSTEM smccall

#if !defined(_TRACE_SMCCALL_H) || defined(TRACE_HEADER_MULTI_READ)
#define _TRACE_SMCCALL_H

#include <linux/tracepoint.h>

DECLARE_EVENT_CLASS(smccall,
	TP_PROTO(ulong arg0, ulong arg1, ulong arg2, ulong arg3),
	TP_ARGS(arg0, arg1, arg2, arg3),

	TP_STRUCT__entry(
		__field(ulong, arg0)
		__field(ulong, arg1)
		__field(ulong, arg2)
		__field(ulong, arg3)
	),

	TP_fast_assign(
		__entry->arg0 = arg0;
		__entry->arg1 = arg1;
		__entry->arg2 = arg2;
		__entry->arg3 = arg3;
		),

	TP_printk("%#lx, %#lx, %#lx, %#lx",
		 __entry->arg0,
		 __entry->arg1,
		 __entry->arg2,
		 __entry->arg3
		 )
);

DEFINE_EVENT(smccall, smc_entry,
	TP_PROTO(ulong arg0, ulong arg1, ulong arg2, ulong arg3),
	TP_ARGS(arg0, arg1, arg2, arg3)
);

DEFINE_EVENT(smccall, smc_exit,
	TP_PROTO(ulong arg0, ulong arg1, ulong arg2, ulong arg3),
	TP_ARGS(arg0, arg1, arg2, arg3)
);

#endif /* _TRACE_SMCCALL_H */

/* This part must be outside protection */
#include <trace/define_trace.h>
