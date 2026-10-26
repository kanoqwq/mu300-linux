#ifndef __WCN_DUMP_INTEGRATE_H__
#define __WCN_DUMP_INTEGRATE_H__

int mdbg_snap_shoot_iram(void *buf);
void mdbg_dump_mem_integ(enum wcn_source_type type);
int dump_arm_reg_integ(void);
u32 mdbg_check_wifi_ip_status(void);
u32 mdbg_check_bt_poweron(void);
u32 mdbg_check_gnss_poweron(void);
u32 mdbg_check_wcn_sys_exit_sleep(void);
u32 mdbg_check_btwf_sys_exit_sleep(void);

#endif
