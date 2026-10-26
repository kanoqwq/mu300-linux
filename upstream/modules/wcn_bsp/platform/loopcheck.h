#ifndef _LOOPCHECK_H_
#define _LOOPCHECK_H_

void start_loopcheck(void);
void stop_loopcheck(void);
int loopcheck_init(void);
int loopcheck_deinit(void);
void complete_kernel_loopcheck(void);
void complete_kernel_atcmd(void);
int loopcheck_status(void);

#endif
