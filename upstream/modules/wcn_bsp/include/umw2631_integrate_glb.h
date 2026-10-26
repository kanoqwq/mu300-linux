#ifndef _UMW2631_INTEG_GLB_H_
#define _UMW2631_INTEG_GLB_H_

#include "wcn_integrate_base_glb.h"

/* ap cp sync flag */
#define UMW2631_MARLIN_CP_INIT_READY_MAGIC	(0xf0f0f0ff)
#define UMW2631_MARLIN_CP2_INITIALIZE_CAIL_WAITING (0xf0f0f0f1)
#define UMW2631_MARLIN_CP2_INITIALIZE_CAIL_DATA_DONE (0xf0f0f0f2)


/* AP regs start and end */
#define UMW2631_WCN_DUMP_AP_REGS_END 7

/* CP2 regs start and end */
#define UMW2631_WCN_DUMP_CP2_REGS_START (UMW2631_WCN_DUMP_AP_REGS_END + 1)

#endif
