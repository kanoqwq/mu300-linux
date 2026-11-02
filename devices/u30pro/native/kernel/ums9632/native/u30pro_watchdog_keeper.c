/* SPDX-License-Identifier: MIT
 * Supervise the second native watchdog while procd retains at least one.
 * Fixed device identities, sysfs dev_t, GETSUPPORT and PID1 FD ownership checks.
 * No watchdog shutdown, magic-close write, arbitrary device path or raw registers.
 */
#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/utsname.h>
#include <sys/sysmacros.h>
#include <linux/watchdog.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <signal.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <ctype.h>
#ifndef U30PRO_NATIVE_RELEASE
#define U30PRO_NATIVE_RELEASE "6.12.94-u30pro-p1"
#endif
#define ROOT "/run/u30pro-native-watchdogs"
#define PERIOD_NS 1000000000L
static volatile sig_atomic_t stop_requested;
struct device { int id, fd, pid1, timeout; dev_t dev; unsigned long pings; };
static struct device devices[2]={{.id=-1,.fd=-1},{.id=-1,.fd=-1}};
static const char *identities[2]={"Spreadtrum Watchdog Timer","U30 Pro UMP9650 watchdog"};
static void sig(int n) { (void)n;stop_requested=1; }
static long uptime(void) { struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec; }
static int read_text(const char *path,char *out,size_t n) {
 int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return -1;
 ssize_t k=read(fd,out,n-1);int err=errno;close(fd);errno=err;
 if(k<0)return -1;out[k]=0;out[strcspn(out,"\r\n")]=0;return 0;
}
static int kind(const char *id) {
 for(int i=0;i<2;i++)if(!strcmp(id,identities[i]))return i;
 return -1;
}
static int scan(void) {
 int seen[2]={0,0};
 for(int id=0;id<8;id++) {
  char p[160],text[128];
  snprintf(p,sizeof p,"/sys/class/watchdog/watchdog%d/identity",id);
  if(read_text(p,text,sizeof text))continue;
  int k=kind(text);if(k<0)continue;
  if(seen[k]++){errno=EEXIST;return -1;}
  unsigned maj,min;char extra;
  snprintf(p,sizeof p,"/sys/class/watchdog/watchdog%d/dev",id);
  if(read_text(p,text,sizeof text)||sscanf(text,"%u:%u%c",&maj,&min,&extra)!=2){errno=EINVAL;return -1;}
  devices[k].id=id;devices[k].dev=makedev(maj,min);
 }
 return seen[0]&&seen[1]?1:0;
}
static int pid1_owns(const struct device *d) {
 DIR *dir=opendir("/proc/1/fd");if(!dir)return 0;
 struct dirent *e;int found=0;
 while((e=readdir(dir))) {
  if(!isdigit((unsigned char)e->d_name[0]))continue;
  char p[320];struct stat st;
  if(snprintf(p,sizeof p,"/proc/1/fd/%s",e->d_name)>=(int)sizeof p)continue;
  if(!stat(p,&st)&&S_ISCHR(st.st_mode)&&
     (st.st_rdev==d->dev||(d->id==0&&st.st_rdev==makedev(10,130)))) {found=1;break;}
 }
 closedir(dir);return found;
}
static void status(const char *state,const char *error,int err) {
 int fd=open(ROOT ".tmp",O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0600);
 if(fd<0)return;
 FILE *f=fdopen(fd,"w");if(!f){close(fd);return;}
 fprintf(f,"{\"state\":\"%s\",\"pid\":%ld,\"uptime_s\":%ld,\"error\":\"%s\",\"errno\":%d,\"devices\":[",state,(long)getpid(),uptime(),error,err);
 for(int k=0;k<2;k++) {
  struct device *d=&devices[k];
  fprintf(f,"%s{\"kind\":\"%s\",\"id\":%d,\"pid1_owner\":%s,\"keeper_owner\":%s,\"timeout\":%d,\"pings\":%lu}",k?",":"",k?"pmic":"ap",d->id,d->pid1?"true":"false",d->fd>=0?"true":"false",d->timeout,d->pings);
 }
 fputs("]}\n",f);if(!fclose(f))rename(ROOT ".tmp",ROOT ".json");
}
static void release(void) {
 for(int k=0;k<2;k++)if(devices[k].fd>=0){close(devices[k].fd);devices[k].fd=-1;}
}
static int take_secondary(void) {
 for(int k=0;k<2;k++) {
  struct device *d=&devices[k];char path[96];struct stat st;struct watchdog_info info;
  if(d->pid1) {
   char text[32];snprintf(path,sizeof path,"/sys/class/watchdog/watchdog%d/timeout",d->id);
   if(!read_text(path,text,sizeof text))d->timeout=atoi(text);
   continue;
  }
  snprintf(path,sizeof path,"/dev/watchdog%d",d->id);
  d->fd=open(path,O_WRONLY|O_CLOEXEC|O_NOFOLLOW);
  if(d->fd<0)return -1;
  if(fstat(d->fd,&st)||!S_ISCHR(st.st_mode)||st.st_rdev!=d->dev){errno=ENODEV;return -1;}
  memset(&info,0,sizeof info);
  if(ioctl(d->fd,WDIOC_GETSUPPORT,&info)||!memchr(info.identity,0,sizeof info.identity)||strcmp((char*)info.identity,identities[k])){errno=ENODEV;return -1;}
  if(ioctl(d->fd,WDIOC_GETTIMEOUT,&d->timeout)||d->timeout<3){errno=EINVAL;return -1;}
  if(ioctl(d->fd,WDIOC_KEEPALIVE,0))return -1;
  d->pings++;
 }
 return 0;
}
int main(int argc,char **argv) {
 struct utsname u;int lock=-1,code=1;long end;
 if(argc!=2||strcmp(argv[1],"--run"))return 2;
 if(geteuid()!=0||uname(&u)||strcmp(u.release,U30PRO_NATIVE_RELEASE)) {fputs("native watchdog keeper: wrong kernel or uid\n",stderr);return 2;}
 umask(077);
 lock=open(ROOT ".lock",O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
 if(lock<0||flock(lock,LOCK_EX|LOCK_NB)){fputs("native watchdog keeper: another owner or lock failure\n",stderr);if(lock>=0)close(lock);return 2;}
 signal(SIGTERM,sig);signal(SIGINT,sig);signal(SIGPIPE,SIG_IGN);
 end=uptime()+45;
 while(!stop_requested&&uptime()<end) {
  int s=scan();if(s<0){status("failed","ambiguous_or_invalid_sysfs",errno);goto done;}
  if(s==1) {
   int count=0;for(int k=0;k<2;k++){devices[k].pid1=pid1_owns(&devices[k]);count+=devices[k].pid1;}
   if(count)break;
  }
  status("waiting","watchdogs_or_procd_not_ready",0);sleep(1);
 }
 if(stop_requested){code=0;status("stopped","signal",0);goto done;}
 if(uptime()>=end){status("failed","watchdogs_or_procd_missing",ETIMEDOUT);goto done;}
 if(take_secondary()){status("failed","secondary_open_or_identity",errno);goto done;}
 while(!stop_requested) {
  int pid1_count=0;
  for(int k=0;k<2;k++) {
   struct device *d=&devices[k];
   if(d->pid1) {
    if(!pid1_owns(d)){status("failed","procd_owner_lost",0);goto done;}
    pid1_count++;
   } else {
    if(ioctl(d->fd,WDIOC_KEEPALIVE,0)){status("failed","watchdog_ping_failed",errno);goto done;}
    d->pings++;
   }
  }
  if(!pid1_count){status("failed","procd_not_supervising",0);goto done;}
  status("running","",0);
  struct timespec delay={1,0};while(nanosleep(&delay,&delay)&&errno==EINTR&&!stop_requested){}
 }
 code=0;release();status("stopped","signal",0);
done:
 release();close(lock);return code;
}
