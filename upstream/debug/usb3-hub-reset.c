// SPDX-License-Identifier: GPL-2.0-only
/* Manual diagnostic: default is read-only; --reset resets only a verified hub. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <linux/usbdevice_fs.h>
int main(int argc,char **argv) {
 unsigned char d[18];struct stat st;int fd,reset,ret;
 if(argc!=5 || (strcmp(argv[1],"--probe") && strcmp(argv[1],"--reset"))) {
  fprintf(stderr,"usage: %s --probe|--reset /dev/bus/usb/BBB/DDD VID PID\n",argv[0]);return 2;
 }
 reset=!strcmp(argv[1],"--reset");
 fd=open(argv[2],reset?O_RDWR:O_RDONLY);
 if(fd<0){perror("open USB device");return 1;}
 if(fstat(fd,&st)||!S_ISCHR(st.st_mode)||major(st.st_rdev)!=189){fprintf(stderr,"not a USB device node\n");close(fd);return 1;}
 if(read(fd,d,sizeof(d))!=sizeof(d)||d[0]!=18||d[1]!=1||d[4]!=9 ||
    (unsigned long)(d[8]|d[9]<<8)!=strtoul(argv[3],0,16)||
    (unsigned long)(d[10]|d[11]<<8)!=strtoul(argv[4],0,16)) {
  fprintf(stderr,"hub descriptor does not match requested target\n");close(fd);return 1;
 }
 printf("verified USB hub %04x:%04x\n",d[8]|d[9]<<8,d[10]|d[11]<<8);fflush(stdout);
 ret=reset?ioctl(fd,USBDEVFS_RESET,0):0;
 if(ret)perror("USBDEVFS_RESET");
 else puts(reset?"USB reset completed":"probe only, no reset");
 close(fd);return ret?1:0;
}
