#ifndef __WCN_GLB_H__
#define __WCN_GLB_H__

#include <misc/marlin_platform.h>

#include "bufring.h"
#include "loopcheck.h"
#include "mdbg_type.h"
//#include "rdc_debug.h"
#include "reset.h"
#include "sysfs.h"
#include "wcn_dbg.h"
#include "wcn_parn_parser.h"
#include "wcn_txrx.h"
#include "wcn_log.h"
#include "wcn_misc.h"
#include "sprd_wcn.h"
#include "wcn_dump.h"
#include "wcn_glb_reg.h"
#include "wcn_dump_integrate.h"
#include "wcn_integrate.h"
#include "wcn_integrate_boot.h"
#include "wcn_integrate_dev.h"

/* log buf size */
#define M3E_MDBG_RX_RING_SIZE		(64*1024)
#define M3L_MDBG_RX_RING_SIZE		(96 * 1024)
#define M3_MDBG_RX_RING_SIZE		(96 * 1024)
#define MDBG_RX_RING_SIZE	(128 * 1024)

#endif
