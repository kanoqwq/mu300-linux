#ifndef __WCN_INTEGRATE_BOOT_H__
#define __WCN_INTEGRATE_BOOT_H__

#include <misc/wcn_integrate_platform.h>

int start_integrate_wcn(u32 subsys);
int stop_integrate_wcn(u32 subsys);
int start_integrate_wcn_truely(u32 subsys);
int stop_integrate_wcn_truely(u32 subsys);
int wcn_proc_native_start(void *arg);
int wcn_proc_native_stop(void *arg);
void wcn_boot_init(void);
void wcn_power_wq(struct work_struct *pwork);
void wcn_device_poweroff(void);
int wcn_reset_mdbg_notifier_init(void);
int wcn_reset_mdbg_notifier_deinit(void);
struct reg_wcn_aon_ahb_reserved2 {
	u32 priority : 2;
	u32 reserved : 30;
};
#define BTWF_SYS_ABNORMAL 0x0deadbad
#define GNSS_SYS_ABNORMAL 0x1deadbad

#ifndef BTWF_SW_DEEP_SLEEP_MAGIC
#define BTWF_SW_DEEP_SLEEP_MAGIC (0x504C5344)
#endif

#define SIPC_SBUF_CHN_MULTIPLEX_WAIT_US 25000
#endif
