/*
 * This file is part of wcn platform
 */

#ifndef __SYSFS_H__
#define __SYSFS_H__

#include <misc/wcn_bus.h>

#define WCN_UEVENT_SOURCE	"SOURCE=wcnmarlin"
#define WCN_UEVENT_FW_ERRO	"EVENT=FW_ERROR"
#define WCN_UEVENT_REASON	"REASON="

#define WCN_SYSFS_LOGLEVEL_SET_BIT   BIT(0)
#define WCN_ASSERT_ONLY_DUMP         0
#define WCN_ASSERT_ONLY_RESET        1
#define WCN_ASSERT_BOTH_RESET_DUMP   2

int notify_at_cmd_finish(void *buf, unsigned char len);
void wcn_notify_fw_error(enum wcn_source_type type, char *buf);
int wcn_sysfs_get_reset_prop(void);
void wcn_firmware_init_wq(struct work_struct *work);
void wcn_firmware_init(void);
int init_wcn_sysfs(void);
void exit_wcn_sysfs(void);
void wcn_send_atcmd_lock(void);
void wcn_send_atcmd_unlock(void);
char *__wcn_get_sw_ver(void);

#endif

