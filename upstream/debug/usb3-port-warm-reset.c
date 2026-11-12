// SPDX-License-Identifier: GPL-2.0-only
/* Manual single-port SS link diagnostic, never part of normal startup. */
#include <errno.h>
#include <fcntl.h>
#include <linux/usbdevice_fs.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int control(int fd, unsigned char type, unsigned char request,
                   unsigned short value, unsigned short index,
                   unsigned short length, void *data)
{
 struct usbdevfs_ctrltransfer t = { .bRequestType=type, .bRequest=request,
  .wValue=value, .wIndex=index, .wLength=length, .timeout=1000, .data=data };
 return ioctl(fd, USBDEVFS_CONTROL, &t);
}
static unsigned short le16(const unsigned char *p) { return p[0] | p[1]<<8; }
int main(int argc, char **argv)
{
 unsigned char descriptor[18], status[4];
 int fd, ret; char *end;
 if (argc!=4 || strcmp(argv[3], "--apply")) {
  fprintf(stderr,"usage: %s /dev/bus/usb/BBB/DDD port --apply\n",argv[0]); return 2;
 }
 long port=strtol(argv[2],&end,10);
 if (*end || port<1 || port>255) return 2;
 fd=open(argv[1], O_RDWR|O_CLOEXEC);
 if (fd<0) { perror("open"); return 1; }
 ret=control(fd,0x80,6,0x100,0,sizeof(descriptor),descriptor);
 /* This diagnostic only addresses Linux's USB3 root hub, not a dock. */
 if (ret!=18 || descriptor[4]!=9 || le16(descriptor+8)!=0x1d6b ||
     le16(descriptor+10)!=3 || le16(descriptor+2)<0x300) {
  fprintf(stderr,"not a Linux SuperSpeed root hub\n"); close(fd); return 1;
 }
 ret=control(fd,0xa3,0,0,port,sizeof(status),status);
 if (ret!=4) { perror("get port status"); close(fd); return 1; }
 printf("before status=%04x change=%04x\n",le16(status),le16(status+2));
 /* Require disconnected RxDetect. Never reset an active SS peripheral. */
 if ((le16(status)&1) || (le16(status)&0x1e0)!=0xa0) {
  fprintf(stderr,"port is connected or not in RxDetect\n"); close(fd); return 1;
 }
 ret=control(fd,0x23,3,28,port,0,NULL); /* SET_FEATURE BH_PORT_RESET */
 if (ret<0) { perror("warm port reset"); close(fd); return 1; }
 usleep(100000);
 ret=control(fd,0xa3,0,0,port,sizeof(status),status);
 if (ret!=4) { perror("get reset status"); close(fd); return 1; }
 printf("after status=%04x change=%04x\n",le16(status),le16(status+2));
 close(fd); return 0;
}
