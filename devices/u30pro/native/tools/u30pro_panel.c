/* SPDX-License-Identifier: MIT
 * Small native KMS status display. No Android compositor, GPU, or storage writes.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <poll.h>
#include <time.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <drm/drm_fourcc.h>

static int fd=-1;
static unsigned width,height,pitch[2],fb[2],handles[2],crtc,connector;
static uint32_t *pixels[2];
static struct drm_mode_modeinfo mode;
static volatile sig_atomic_t running=1;
static int keyfd=-1,page,asleep;
static int touchfd=-1,touch_slot,touch_down,touch_started,touch_x,touch_y,touch_start_x,touch_start_y,touch_cancelled;
static long touch_at;
static unsigned touch_count;
static int touch_peak,touch_fired,touch_syn_dropped,touch_monotonic;
static unsigned gesture_count;
static const char *last_gesture="none";
static unsigned key_count,last_key;
static long pressed_at[KEY_MAX+1];
static int brightness=1024;
static long now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000+t.tv_nsec/1000000;}
static void backlight(int off){FILE *f=fopen("/sys/class/backlight/sprd_backlight/brightness","w");if(f){fprintf(f,"%d\n",off?0:brightness);fclose(f);}}
static void panel_state(void);
static void framebuffer_unblank(void){FILE *f=fopen("/sys/class/graphics/fb0/blank","w");if(f){fprintf(f,"0\n");fclose(f);}}
static int screen_command_read(void){
 const char *path="/run/u30pro-panel-screen-command";FILE *f=fopen(path,"r");if(!f)return 0;
 char command[8]={0};if(!fgets(command,sizeof command,f)){fclose(f);unlink(path);return 0;}fclose(f);
 int target;if(!strcmp(command,"off\n")||!strcmp(command,"off"))target=1;
 else if(!strcmp(command,"on\n")||!strcmp(command,"on"))target=0;
 else{unlink(path);fprintf(stderr,"invalid screen power command\n");return 0;}
 unlink(path);
 if(!target){refresh_brightness();framebuffer_unblank();}
 if(!backlight(target)){fprintf(stderr,"backlight unavailable; screen power command not applied\n");return 0;}
 asleep=target;last_gesture=target?"remote-sleep":"remote-wake";panel_state();return 1;
}
static void keys_open(void){
 char name[256]={0};keyfd=open("/dev/input/event0",O_RDONLY|O_NONBLOCK|O_CLOEXEC);
 if(keyfd>=0&&(ioctl(keyfd,EVIOCGNAME(sizeof name),name)<0||strcmp(name,"gpio-keys"))){close(keyfd);keyfd=-1;}
 if(keyfd>=0){FILE *f=fopen("/sys/class/backlight/sprd_backlight/brightness","r");int n;if(f){if(fscanf(f,"%d",&n)==1&&n>0&&n<=4095)brightness=n;fclose(f);}printf("KEYS device=gpio-keys power=116 down=114 up=115 home=102\n");}
 else fprintf(stderr,"gpio-keys input unavailable; display continues\n");
}
static void panel_state(void){FILE *f=fopen("/run/u30pro-panel-state.new","w");if(!f)return;fprintf(f,"{\"page\":%d,\"screen_on\":%s,\"key_count\":%u,\"last_key\":%u,\"input_ready\":%s,\"rotation\":180,\"round_safe\":true,\"touch_ready\":%s,\"touch_count\":%u,\"touch_raw_x\":%d,\"touch_raw_y\":%d,\"gesture_count\":%u,\"last_gesture\":\"%s\"}\n",page,asleep?"false":"true",key_count,last_key,keyfd>=0?"true":"false",touchfd>=0?"true":"false",touch_count,touch_x,touch_y,gesture_count,last_gesture);fclose(f);rename("/run/u30pro-panel-state.new","/run/u30pro-panel-state.json");}
/* GESTURE_CLASSIFIER_BEGIN: also compiled by the host boundary tests. */
enum gesture { G_NONE, G_TAP, G_LEFT, G_RIGHT, G_HOLD };
static enum gesture gesture_classify(int dx,int dy,int peak,long ms,int cancelled){
 if(cancelled||ms<20)return G_NONE;
 if(ms<=1200&&abs(dx)>=48&&abs(dx)*2>=abs(dy)*3)return dx<0?G_LEFT:G_RIGHT;
 if(peak>18)return G_NONE;
 if(ms>=800)return G_HOLD;
 return G_TAP;
}
/* GESTURE_CLASSIFIER_END */
static int touch_apply(enum gesture action){
 if(action==G_NONE)return 0;
 if(asleep){asleep=0;backlight(0);last_gesture="wake";}
 else if(action==G_HOLD){page=0;last_gesture="hold-home";}
 else{page=(page+(action==G_RIGHT?2:1))%3;last_gesture=action==G_LEFT?"swipe-left":action==G_RIGHT?"swipe-right":"tap";}
 gesture_count++;touch_fired=1;
 printf("GESTURE action=%s page=%d\n",last_gesture,page);fflush(stdout);panel_state();return 1;
}
static int touch_hold(void){
 if(!touch_down||!touch_started||touch_fired)return 0;
 long ms=now_ms()-touch_at;
 return gesture_classify(0,0,touch_peak,ms,touch_cancelled)==G_HOLD?touch_apply(G_HOLD):0;
}
static void touch_open(void){
 if(touchfd>=0)return;
 for(int i=0;i<32;i++){
  char path[64],name[256]={0};snprintf(path,sizeof path,"/dev/input/event%d",i);
  int t=open(path,O_RDONLY|O_NONBLOCK|O_CLOEXEC);if(t<0)continue;
  struct input_absinfo x,y;
  if(ioctl(t,EVIOCGNAME(sizeof name),name)>=0&&!strcmp(name,"tlsc6x_touch")&&ioctl(t,EVIOCGABS(ABS_MT_POSITION_X),&x)>=0&&ioctl(t,EVIOCGABS(ABS_MT_POSITION_Y),&y)>=0&&x.minimum==0&&x.maximum==239&&y.minimum==0&&y.maximum==239){
   int clk=CLOCK_MONOTONIC;touch_monotonic=ioctl(t,EVIOCSCLOCKID,&clk)==0;
   touchfd=t;printf("TOUCH device=%s name=%s range=0..239 tap=next-page swipe=page hold=home rotation=180\n",path,name);fflush(stdout);panel_state();return;
  }
  close(t);
 }
}
static int touch_read(void){
 int changed=0;struct input_event e;
 while(touchfd>=0&&read(touchfd,&e,sizeof e)==(ssize_t)sizeof e){
  if(e.type==EV_SYN&&e.code==SYN_DROPPED){touch_down=touch_started=0;touch_syn_dropped=1;touch_cancelled=1;continue;}
  if(touch_syn_dropped){if(e.type==EV_SYN&&e.code==SYN_REPORT)touch_syn_dropped=0;continue;}
  if(e.type==EV_ABS){
   if(e.code==ABS_MT_SLOT)touch_slot=e.value;
   if(e.code==ABS_MT_TRACKING_ID){if(touch_slot==0)touch_down=e.value>=0;else if(e.value>=0)touch_cancelled=1;}
   if(touch_slot==0&&e.code==ABS_MT_POSITION_X)touch_x=e.value;
   if(touch_slot==0&&e.code==ABS_MT_POSITION_Y)touch_y=e.value;
  }
  if(e.type!=EV_SYN||e.code!=SYN_REPORT)continue;
  long event_ms=touch_monotonic?e.time.tv_sec*1000+e.time.tv_usec/1000:now_ms();
  if(touch_down&&!touch_started){touch_started=1;touch_at=event_ms;touch_start_x=touch_x;touch_start_y=touch_y;touch_peak=touch_fired=0;}
  if(touch_started){int mx=abs(touch_x-touch_start_x),my=abs(touch_y-touch_start_y);if(mx>touch_peak)touch_peak=mx;if(my>touch_peak)touch_peak=my;}
  if(!touch_down&&touch_started){
   long duration=event_ms-touch_at;touch_started=0;touch_count++;
   /* Coordinates follow the LCD scanout; undo its physical 180 degree rotation. */
   int dx=touch_start_x-touch_x,dy=touch_start_y-touch_y;
   if(!touch_fired)changed|=touch_apply(gesture_classify(dx,dy,touch_peak,duration,touch_cancelled));
   printf("TOUCH release raw_start=%d,%d raw_end=%d,%d logical_delta=%d,%d peak=%d duration_ms=%ld action=%s\n",touch_start_x,touch_start_y,touch_x,touch_y,dx,dy,touch_peak,duration,touch_fired?last_gesture:"ignored");fflush(stdout);panel_state();
  }
  if(!touch_down)touch_cancelled=0;
 }
 return changed;
}
static int keys_read(void){
 int changed=0;struct input_event e;
 while(keyfd>=0&&read(keyfd,&e,sizeof e)==(ssize_t)sizeof e){
  if(e.type==EV_SYN&&e.code==SYN_DROPPED){memset(pressed_at,0,sizeof pressed_at);continue;}
  if(e.type!=EV_KEY||e.code>KEY_MAX)continue;
  if(e.value==1){pressed_at[e.code]=now_ms();continue;}
  if(e.value!=0||!pressed_at[e.code])continue;
  long duration=now_ms()-pressed_at[e.code];pressed_at[e.code]=0;
  if(e.code!=KEY_POWER&&e.code!=KEY_HOME&&e.code!=KEY_VOLUMEUP&&e.code!=KEY_VOLUMEDOWN)continue;
  key_count++;last_key=e.code;printf("KEY code=%u value=release duration_ms=%ld\n",e.code,duration);fflush(stdout);
  /* Short presses only; never erase configuration or reboot on a key. */
  if(duration>1800)continue;
  if(e.code==KEY_POWER){asleep=!asleep;backlight(asleep);}
  else{if(asleep){asleep=0;backlight(0);}page=(page+(e.code==KEY_VOLUMEDOWN?2:1))%3;}
  changed=1;panel_state();
 }
 return changed;
}
static void die(const char *s){perror(s);exit(1);}
static void xio(unsigned long req,void *arg,const char *name){if(ioctl(fd,req,arg)<0)die(name);}
static uint64_t ptr(void *p){return (uint64_t)(uintptr_t)p;}
static void finish(int sig){(void)sig;running=0;}
static void setup(int allocate){
 fd=open("/dev/dri/card0",O_RDWR|O_CLOEXEC);if(fd<0)die("open DRM");
 struct drm_mode_card_res r={0};xio(DRM_IOCTL_MODE_GETRESOURCES,&r,"resources count");
 if(r.count_crtcs>16||r.count_connectors>16||r.count_encoders>16||r.count_fbs>64||!r.count_crtcs||!r.count_connectors){errno=ENODEV;die("resources bounds");}
 uint32_t crtcs[16]={0},cons[16]={0},enc[16]={0},fbs[64]={0};r.crtc_id_ptr=ptr(crtcs);r.connector_id_ptr=ptr(cons);r.encoder_id_ptr=ptr(enc);r.fb_id_ptr=ptr(fbs);xio(DRM_IOCTL_MODE_GETRESOURCES,&r,"resources");
 struct drm_mode_get_connector c={0};
 for(unsigned i=0;i<r.count_connectors;i++){
  memset(&c,0,sizeof c);c.connector_id=cons[i];xio(DRM_IOCTL_MODE_GETCONNECTOR,&c,"connector count");
  if(c.connection!=1||!c.count_modes)continue;
  if(c.count_modes>64||c.count_encoders>16||c.count_props>128){errno=EOVERFLOW;die("connector bounds");}
  struct drm_mode_modeinfo modes[64];uint32_t encoders[16],props[128];uint64_t values[128];
  c.modes_ptr=ptr(modes);c.encoders_ptr=ptr(encoders);c.props_ptr=ptr(props);c.prop_values_ptr=ptr(values);xio(DRM_IOCTL_MODE_GETCONNECTOR,&c,"connector");
  for(unsigned j=0;j<c.count_modes;j++)if(modes[j].hdisplay==240&&modes[j].vdisplay==240){mode=modes[j];connector=c.connector_id;break;}
  if(!connector)continue;
  uint32_t encoder=c.encoder_id?c.encoder_id:encoders[0];struct drm_mode_get_encoder e={.encoder_id=encoder};xio(DRM_IOCTL_MODE_GETENCODER,&e,"encoder");
  for(unsigned j=0;j<r.count_crtcs;j++)if(e.possible_crtcs&(1u<<j)){crtc=crtcs[j];break;}
  break;
 }
 if(!connector||!crtc){errno=ENODEV;die("240x240 connected mode / CRTC");}
 width=mode.hdisplay;height=mode.vdisplay;
 printf("KMS connector=%u crtc=%u mode=%s %ux%u clock=%u\n",connector,crtc,mode.name,width,height,mode.clock);fflush(stdout);
 if(!allocate)return;
 if(ioctl(fd,DRM_IOCTL_SET_MASTER,0)<0)die("DRM master");
 for(int i=0;i<2;i++){
  struct drm_mode_create_dumb d={.width=width,.height=height,.bpp=32};xio(DRM_IOCTL_MODE_CREATE_DUMB,&d,"create dumb");
  if(d.pitch<width*4||d.size<(uint64_t)d.pitch*height||d.size>4194304){errno=EOVERFLOW;die("dumb bounds");}
  pitch[i]=d.pitch;handles[i]=d.handle;
  struct drm_mode_map_dumb m={.handle=d.handle};xio(DRM_IOCTL_MODE_MAP_DUMB,&m,"map offset");
  pixels[i]=mmap(NULL,(size_t)d.size,PROT_READ|PROT_WRITE,MAP_SHARED,fd,(off_t)m.offset);if(pixels[i]==MAP_FAILED)die("mmap dumb");
  struct drm_mode_fb_cmd2 a={.width=width,.height=height,.pixel_format=DRM_FORMAT_XRGB8888};a.handles[0]=d.handle;a.pitches[0]=d.pitch;xio(DRM_IOCTL_MODE_ADDFB2,&a,"add framebuffer");fb[i]=a.fb_id;
 }
}
/* The LCD is physically mounted upside down; leave the vendor DRM/DT intact. */
static void pixel(int b,int x,int y,uint32_t color){if(x>=0&&y>=0&&(unsigned)x<width&&(unsigned)y<height)pixels[b][(unsigned)y*(pitch[b]/4)+(unsigned)x]=color;}
static void fill(int b,uint32_t color){for(unsigned y=0;y<height;y++)for(unsigned x=0;x<width;x++)pixel(b,(int)x,(int)y,color);}
static void rect(int b,int x,int y,int w,int h,uint32_t color){for(int j=0;j<h;j++)for(int i=0;i<w;i++)pixel(b,x+i,y+j,color);}
static const char letters[]="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .:%-/+?";
static const unsigned char glyphs[][5]={
 {126,17,17,17,126},{127,73,73,73,54},{62,65,65,65,34},{127,65,65,34,28},
 {127,73,73,73,65},{127,9,9,9,1},{62,65,73,73,122},{127,8,8,8,127},
 {0,65,127,65,0},{32,64,65,63,1},{127,8,20,34,65},{127,64,64,64,64},
 {127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},{127,9,9,9,6},
 {62,65,81,33,94},{127,9,25,41,70},{70,73,73,73,49},{1,1,127,1,1},
 {63,64,64,64,63},{31,32,64,32,31},{63,64,56,64,63},{99,20,8,20,99},
 {3,4,120,4,3},{97,81,73,69,67},
 {62,81,73,69,62},{0,66,127,64,0},{66,97,81,73,70},{33,65,69,75,49},
 {24,20,18,127,16},{39,69,69,69,57},{60,74,73,73,48},{1,113,9,5,3},
 {54,73,73,73,54},{6,73,73,41,30},
 {0,0,0,0,0},{0,96,96,0,0},{0,54,54,0,0},{99,19,8,100,99},
 {8,8,8,8,8},{32,16,8,4,2},{8,8,62,8,8},{2,1,81,9,6}
};
static void text(int b,int x,int y,const char *s,int scale,uint32_t color){
 for(int k=0;*s&&k<40;s++,k++){
  char ch=(char)toupper((unsigned char)*s);const char *p=strchr(letters,ch);size_t index=p?(size_t)(p-letters):sizeof glyphs/sizeof *glyphs-1;
  for(int i=0;i<5;i++)for(int j=0;j<7;j++)if(glyphs[index][i]&(1u<<j))rect(b,x+i*scale,y+j*scale,scale,scale,color);
  x+=6*scale;
 }
}
static void pattern(int b){uint32_t colors[]={0xff0000,0x00ff00,0x0000ff,0xffffff,0xffff00,0x00ffff};fill(b,0x101820);for(int i=0;i<6;i++)rect(b,i*40,45,40,110,colors[i]);text(b,18,13,"U30PRO TEST",3,0xffffff);text(b,24,180,"RED GREEN BLUE",2,0xffffff);text(b,30,211,"240 X 240",2,0x00ffff);}
/* Keep every text rectangle inside an inset circle, including its top/bottom.
 * Integer geometry avoids libm and keeps long IP/operator strings legible. */
static int fits_round(int x,int y,int w,int h){
 int xs[]={2*x-239,2*(x+w-1)-239},ys[]={2*y-239,2*(y+h-1)-239};
 for(int i=0;i<2;i++)for(int j=0;j<2;j++)if(xs[i]*xs[i]+ys[j]*ys[j]>216*216)return 0;
 return 1;
}
static void centered(int b,int y,const char *s,uint32_t color){
 char clipped[41];snprintf(clipped,sizeof clipped,"%.40s",s);
 int n=(int)strlen(clipped);if(!n)return;
 int scale=2,w=(n*6-1)*scale,x=(240-w)/2;
 if(!fits_round(x,y,w,7*scale)){scale=1;w=n*6-1;x=(240-w)/2;}
 while(n&&!fits_round(x,y,w,7*scale)){clipped[--n]=0;w=(n*6-1)*scale;x=(240-w)/2;}
 if(n)text(b,x,y,clipped,scale,color);
}
static void status(int b){
 fill(b,0x101820);
 const char *titles[]={"U30PRO","NETWORK","BUTTONS"};centered(b,26,titles[page],0x3be7d2);
 FILE *f=fopen("/run/u30pro-screen.txt","r");char lines[9][128]={{0}};int count=0;
 if(f){while(count<9&&fgets(lines[count],sizeof lines[count],f)){lines[count][strcspn(lines[count],"\r\n")]=0;count++;}fclose(f);}
 if(!count){centered(b,105,"STARTING...",0xffffff);return;}
 if(page==0){int indexes[]={0,1,2,3,5,8};for(int i=0;i<6;i++)centered(b,57+i*24,lines[indexes[i]],i?0xb6cbd1:0xffffff);}
 if(page==1){int indexes[]={3,4,5,-1,6,7};for(int i=0;i<6;i++)centered(b,57+i*24,indexes[i]<0?"192.168.25.1":lines[indexes[i]],0xb6cbd1);}
 if(page==2){centered(b,57,"POWER: SCREEN",0xb6cbd1);centered(b,81,"SWIPE: PAGE",0xb6cbd1);centered(b,105,"HOLD 0.8S: HOME",0xb6cbd1);centered(b,129,"TAP: NEXT PAGE",0xb6cbd1);char s[40];snprintf(s,sizeof s,"TOUCH %u",touch_count);centered(b,153,s,0x3be7d2);centered(b,177,last_gesture,0x3be7d2);}
 char footer[16];snprintf(footer,sizeof footer,"%d/3",page+1);centered(b,209,footer,0x3be7d2);
}
static void present(int b,int first){
 if(first){struct drm_mode_crtc c={.set_connectors_ptr=ptr(&connector),.count_connectors=1,.crtc_id=crtc,.fb_id=fb[b],.mode_valid=1};c.mode=mode;xio(DRM_IOCTL_MODE_SETCRTC,&c,"set CRTC");}
 else{struct drm_mode_crtc_page_flip flip={.crtc_id=crtc,.fb_id=fb[b]};xio(DRM_IOCTL_MODE_PAGE_FLIP,&flip,"page flip");}
}
/* Export the upright view.  The 180 degree panel orientation is applied by the u30pro-nv3002c kernel driver, so the
 * framebuffer is already upright; touch coordinates still follow the physical panel and are negated below. */
static void capture(int b){FILE *f=fopen("/run/u30pro-screen.new","wb");if(!f)return;fprintf(f,"P6\n%u %u\n255\n",width,height);for(unsigned y=0;y<height;y++)for(unsigned x=0;x<width;x++){uint32_t c=pixels[b][y*(pitch[b]/4)+x];unsigned char rgb[]={(unsigned char)(c>>16),(unsigned char)(c>>8),(unsigned char)c};fwrite(rgb,1,3,f);}fclose(f);rename("/run/u30pro-screen.new","/run/u30pro-screen.ppm");}
int main(int argc,char **argv){
 if(argc!=2){fprintf(stderr,"Usage: u30pro-panel probe|test|run\n");return 2;}
 int probe=!strcmp(argv[1],"probe"),test=!strcmp(argv[1],"test");if(!probe&&!test&&strcmp(argv[1],"run"))return 2;
 umask(077);unlink("/run/u30pro-panel-screen-command");setup(!probe);if(probe){close(fd);return 0;}signal(SIGTERM,finish);signal(SIGINT,finish);
 if(!test){keys_open();touch_open();}panel_state();int b=0,first=1;long next=0,retry_touch=0;
 while(running){
  int changed=keys_read();changed|=touch_read();changed|=touch_hold();changed|=screen_command_read();long now=now_ms();
  if(!test&&touchfd<0&&now>=retry_touch){touch_open();retry_touch=now+2000;}
  if(!asleep&&(first||changed||now>=next)){if(test)pattern(b);else status(b);present(b,first);capture(b);panel_state();if(first)printf("FRAME mode=%s fb=%u; refresh=2s\n",test?"test":"status",fb[b]);fflush(stdout);first=0;b^=1;next=now+2000;}
  if(test)break;struct pollfd p[]={{.fd=keyfd,.events=POLLIN},{.fd=touchfd,.events=POLLIN}};poll(p,2,50);
 }
 if(keyfd>=0)close(keyfd);if(touchfd>=0)close(touchfd);backlight(0);close(fd);return 0;
}
