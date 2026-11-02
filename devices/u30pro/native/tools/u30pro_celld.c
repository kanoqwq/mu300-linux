/* Native single-owner AT manager (the cellular data sessions), ubus object "u30pro.cell". CP secure loading remains a separate service.
 * IPv4 only. CID1 is the internet context; optional additional contexts use CIDs 2-8 (data on sipa_eth<cid-1>).
 * No raw-AT RPC, RF reset, NV or firmware writes.
 *   u30pro-celld --run <netifd interface> /dev/stty_nr0 <APN>     the daemon
 *   u30pro-celld --ctl status|refresh|reconnect|disconnect|reload  the same as "ubus call u30pro.cell <method>"
 * Settings: /etc/config/u30pro_modem, section "dial" (apn, auth none|pap|chap, username, password, pdp ipv4|ipv4v6) for the internet
 * context and sections "pdn" (enabled, cid 2-8, apn, auth, username, password, pdp; netifd interface cell_<section>) for the others;
 * read at start and by the method reload.  A link that comes up or goes down is handed to netifd (network.interface.<name> up/down);
 * its protocol handler u30cell asks this daemon (method status) for the address and the DNS servers.  Event "u30pro.cell.state"
 * {"state", "connected"} when the state changes.
 * Built with -DCELL_TEST (tests/test_cellular_native.py) the settings come from files in $U30_CELL_TEST_DIR (profile, ctx/<name>),
 * the status is written to status.json there, the control commands come through control.sock and links are reported to the hook
 * $U30_CELL_TEST_NOTIFY. */
#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/ioctl.h>
#include <arpa/inet.h>
#include <dirent.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <termios.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <ctype.h>
#include <limits.h>
#ifndef CELL_TEST
#include <libubox/uloop.h>
#include <libubox/blobmsg.h>
#include <libubus.h>
#include <uci.h>
#endif
#define ROOT "/run/u30pro-cellular"
#ifdef CELL_TEST
#define INTERVAL 600
#define BACKOFF 700
#define QUERY_TIMEOUT 650
#define DATA_TIMEOUT 1000
#define REG_STALL_MS 2500
#define RADIO_GAP_MS 6000
#define ENABLE_TIMEOUT 1500
#define ENABLE_GAP_MS 5000
#else
#define INTERVAL 10000
#define BACKOFF 5000
#define QUERY_TIMEOUT 4000
#define DATA_TIMEOUT 20000
/* not registered for this long: restart the protocol stack (AT+SFUN=5, then the radio-on sequence); at most once per RADIO_GAP_MS */
#define REG_STALL_MS 180000
#define RADIO_GAP_MS 600000
/* early in a boot the CP can take tens of seconds to answer a radio-on request (AT+CFUN=1 seen: about 20 s). A repeat sent while the first
   was pending crossed the replies, so a radio-on request waits long and a failed one is not repeated within ENABLE_GAP_MS */
#define ENABLE_TIMEOUT 90000
#define ENABLE_GAP_MS 600000
#endif
static volatile sig_atomic_t quitting;
static char root[PATH_MAX]=ROOT,iface[33],port[128],apn[64],sockpath[PATH_MAX];
#ifdef CELL_TEST
static char notify[PATH_MAX]="/usr/sbin/u30pro-cellular-notify";
#endif

static int fd=-1,listener=-1,lockfd=-1,wanted=1,reconnecting,stopping;
static long next_cycle,deadline,stop_deadline;
static unsigned requests,timeouts,errors,recoveries,urcs,generation,cycles,radio_resets;
static long unreg_since,last_radio_reset,last_enable;
static int regstat=-1,csq=-1,rsrq=-1,rsrp=-1,context=-1,cfun=-1,linked,have_reply,step;
static char configured_apn[64],configured_pdp[16];
/* optional dial profile (<root>/profile, root only): apn=, auth=none|pap|chap, user=, pass=, pdp=ipv4|ipv4v6 */
static char auth[8]="none",pdp[8]="ipv4v6",puser[48],ppass[48];static int have_profile;
static char sim[32]="unknown",operator_name[64]="",ip[16]="",dns1[16]="",dns2[16]="",actual_apn[64]="",last_error[128]="",phase[32]="starting",command[160]="",rxline[2048],cesq_raw[192]="";
static size_t rxlen;
static char notified_ip[16],notified_dns1[16],notified_dns2[16];
static int notify_dirty=1;
static int dropping_line;
/* additional contexts: one file per context in <root>/ctx (root only), the profile keys plus cid= (2-8) and iface= (the netifd
   interface); the stock RIL puts CID n on sipa_eth<n-1>. They are handled after the internet context is up, one command at a time
   like everything else, and a failure of one of them never touches the internet context. */
#define MAXX 7
struct xctx {char name[16],apn[64],auth[8],pdp[8],user[48],pass[48],iface[33],ip[16],dns1[16],dns2[16],tip[16],tdns1[16],tdns2[16],cfg_apn[64],cfg_pdp[16],error[128];int cid,linked,redo,dirty;unsigned errors,generation;long next_try;};
static struct xctx xs[MAXX];static int nx,xi=-1,xcid,xreload;   /* xi: the context being worked on in this pass; xcid: the CID of the command */
static int xret[MAXX],nxret;   /* CIDs of contexts removed by a reload, taken down at the next pass */
static int act[16];            /* CGACT state per CID from the last query */
static const char *xdev(const struct xctx *x){static char b[24];snprintf(b,sizeof b,"sipa_eth%d",x->cid-1);return b;}
enum {IDLE,CPIN,CFUN,ENABLE,REG,COPS,CSQ,CESQ,ACT,CFG,PDP,DEACT,APN,DIAL,DISCONNECT,AUTHSET,RADIOOFF,RADIOON,TSXTMP,XACT,XCFG,XDEACT,XAPN,XAUTH,XDIAL,XPDP,XDOWN};
static int tsx_then;           /* the radio-on step (ENABLE or RADIOON) that follows AT+SPTSXTMP */
static long now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000L+t.tv_nsec/1000000;}
#ifdef CELL_TEST
static void on_signal(int n){(void)n;quitting=1;}
#endif
static int valid_name(const char *s,size_t max,int dots){size_t n=strlen(s);if(!n||n>max)return 0;for(;*s;s++)if(!isalnum((unsigned char)*s)&&*s!='_'&&*s!='-'&&!(dots&&*s=='.'))return 0;return 1;}
static int profile_text_ok(const char *s,size_t max){size_t n=strlen(s);if(n>max)return 0;for(;*s;s++)if((unsigned char)*s<32||(unsigned char)*s>126||*s=='"'||*s=='\\')return 0;return 1;}
#ifndef CELL_TEST
/* ---- settings in UCI */
static void xclear(struct xctx *x);
static int integer(const char *s,int *v);
static struct uci_context *uci_ctx;
/* u30pro_modem read afresh (the settings may have changed since the last read); NULL when it cannot be read */
static struct uci_package *uci_modem(void){
 struct uci_package *pkg=NULL;struct uci_element *e;
 if(!uci_ctx&&!(uci_ctx=uci_alloc_context()))return NULL;
 uci_foreach_element(&uci_ctx->root,e)if(!strcmp(e->name,"u30pro_modem")){uci_unload(uci_ctx,uci_to_package(e));break;}
 return uci_load(uci_ctx,"u30pro_modem",&pkg)?NULL:pkg;
}
static const char *opt(struct uci_section *sec,const char *name){const char *v=uci_lookup_option_string(uci_ctx,sec,name);return v?v:NULL;}
/* the profile keys (the same rules as the profile file of the tests); returns 0 when one is invalid */
static int take_profile(struct uci_section *sec,char a[8],char d[8],char u[48],char pw[48],char ap[64]){
 const char *v;int bad=0;
 if((v=opt(sec,"auth"))){if(!strcmp(v,"none")||!strcmp(v,"pap")||!strcmp(v,"chap"))snprintf(a,8,"%s",v);else bad=1;}
 if((v=opt(sec,"pdp"))){if(!strcmp(v,"ipv4")||!strcmp(v,"ipv4v6"))snprintf(d,8,"%s",v);else bad=1;}
 if((v=opt(sec,"apn"))&&*v){if(valid_name(v,63,1))snprintf(ap,64,"%s",v);else bad=1;}
 if((v=opt(sec,"username"))){if(profile_text_ok(v,40))snprintf(u,48,"%s",v);else bad=1;}
 if((v=opt(sec,"password"))){if(profile_text_ok(v,40))snprintf(pw,48,"%s",v);else bad=1;}
 return !bad&&!(strcmp(a,"none")&&!*u);
}
/* returns 1 when valid settings were loaded, 0 when there are none, -1 when they are invalid (previous settings stay) */
static int load_profile(void){
 struct uci_package *pkg=uci_modem();struct uci_section *sec=pkg?uci_lookup_section(uci_ctx,pkg,"dial"):NULL;
 char a[8]="none",d[8]="ipv4v6",u[48]="",pw[48]="",ap[64]="";
 if(!sec||strcmp(sec->type,"dial"))return 0;
 if(!take_profile(sec,a,d,u,pw,ap))return -1;
 snprintf(auth,sizeof auth,"%s",a);snprintf(pdp,sizeof pdp,"%s",d);snprintf(puser,sizeof puser,"%s",u);snprintf(ppass,sizeof ppass,"%s",pw);if(*ap)snprintf(apn,sizeof apn,"%s",ap);have_profile=1;return 1;
}
#else
/* returns 1 when a valid profile was loaded, 0 when there is none, -1 when it is invalid (previous settings stay) */
static int load_profile(void){
 char path[PATH_MAX];if(snprintf(path,sizeof path,"%s/profile",root)>=(int)sizeof path)return 0;FILE *f=fopen(path,"r");if(!f)return 0;
 char a[8]="none",d[8]="ipv4v6",u[48]="",pw[48]="",ap[64]="",line[160];int bad=0;
 while(fgets(line,sizeof line,f)){line[strcspn(line,"\r\n")]=0;char *eq=strchr(line,'=');if(!eq)continue;*eq=0;const char *k=line,*v=eq+1;
  if(!strcmp(k,"auth")){if(!strcmp(v,"none")||!strcmp(v,"pap")||!strcmp(v,"chap"))snprintf(a,sizeof a,"%s",v);else bad=1;}
  else if(!strcmp(k,"pdp")){if(!strcmp(v,"ipv4")||!strcmp(v,"ipv4v6"))snprintf(d,sizeof d,"%s",v);else bad=1;}
  else if(!strcmp(k,"apn")){if(valid_name(v,63,1))snprintf(ap,sizeof ap,"%s",v);else bad=1;}
  else if(!strcmp(k,"user")){if(profile_text_ok(v,40))snprintf(u,sizeof u,"%s",v);else bad=1;}
  else if(!strcmp(k,"pass")){if(profile_text_ok(v,40))snprintf(pw,sizeof pw,"%s",v);else bad=1;}}
 fclose(f);if(bad||(strcmp(a,"none")&&!*u))return -1;
 snprintf(auth,sizeof auth,"%s",a);snprintf(pdp,sizeof pdp,"%s",d);snprintf(puser,sizeof puser,"%s",u);snprintf(ppass,sizeof ppass,"%s",pw);if(*ap)snprintf(apn,sizeof apn,"%s",ap);have_profile=1;return 1;
}
static void xclear(struct xctx *x);
static int integer(const char *s,int *v);
#endif
#ifdef CELL_TEST
/* one context file; returns 0 when it is invalid (the context is then left out) */
static int load_context(const char *path,const char *name,struct xctx *x){
 FILE *f=fopen(path,"r");if(!f)return 0;memset(x,0,sizeof *x);snprintf(x->name,sizeof x->name,"%.15s",name);strcpy(x->auth,"none");strcpy(x->pdp,"ipv4");
 char line[160];int bad=0;
 while(fgets(line,sizeof line,f)){line[strcspn(line,"\r\n")]=0;char *eq=strchr(line,'=');if(!eq)continue;*eq=0;const char *k=line,*v=eq+1;int n;
  if(!strcmp(k,"cid")){if(integer(v,&n)&&n>=2&&n<=8)x->cid=n;else bad=1;}
  else if(!strcmp(k,"apn")){if(valid_name(v,63,1))snprintf(x->apn,sizeof x->apn,"%s",v);else bad=1;}
  else if(!strcmp(k,"iface")){if(valid_name(v,32,0))snprintf(x->iface,sizeof x->iface,"%s",v);else bad=1;}
  else if(!strcmp(k,"auth")){if(!strcmp(v,"none")||!strcmp(v,"pap")||!strcmp(v,"chap"))snprintf(x->auth,sizeof x->auth,"%s",v);else bad=1;}
  else if(!strcmp(k,"pdp")){if(!strcmp(v,"ipv4")||!strcmp(v,"ipv4v6"))snprintf(x->pdp,sizeof x->pdp,"%s",v);else bad=1;}
  else if(!strcmp(k,"user")){if(profile_text_ok(v,40))snprintf(x->user,sizeof x->user,"%s",v);else bad=1;}
  else if(!strcmp(k,"pass")){if(profile_text_ok(v,40))snprintf(x->pass,sizeof x->pass,"%s",v);else bad=1;}}
 fclose(f);return !bad&&x->cid&&*x->apn&&*x->iface&&!(strcmp(x->auth,"none")&&!*x->user);
}
#else
/* one "pdn" section; returns 0 when it is invalid or switched off (the context is then left out) */
static int load_context(struct uci_section *sec,struct xctx *x){
 const char *v;int n;
 memset(x,0,sizeof *x);strcpy(x->auth,"none");strcpy(x->pdp,"ipv4");
 if(!valid_name(sec->e.name,15,0)||((v=opt(sec,"enabled"))&&!strcmp(v,"0")))return 0;
 snprintf(x->name,sizeof x->name,"%.15s",sec->e.name);snprintf(x->iface,sizeof x->iface,"cell_%.27s",sec->e.name);
 if(!(v=opt(sec,"cid"))||!integer(v,&n)||n<2||n>8)return 0;
 x->cid=n;
 return take_profile(sec,x->auth,x->pdp,x->user,x->pass,x->apn)&&*x->apn;
}
#endif
/* (re)reads the additional contexts. Unchanged contexts keep their link; changed ones are redialled; removed ones go down. */
static void load_contexts(void){
 struct xctx n[MAXX],t;int k=0;
#ifdef CELL_TEST
 char dir[PATH_MAX],path[PATH_MAX];
 if(snprintf(dir,sizeof dir,"%s/ctx",root)>=(int)sizeof dir)return;DIR *d=opendir(dir);
 if(d){struct dirent *e;while((e=readdir(d))&&k<MAXX){
  if(!valid_name(e->d_name,15,0)||snprintf(path,sizeof path,"%s/%s",dir,e->d_name)>=(int)sizeof path||!load_context(path,e->d_name,&t))continue;
  int dup=0;for(int i=0;i<k;i++)if(n[i].cid==t.cid||!strcmp(n[i].iface,t.iface))dup=1;if(!dup)n[k++]=t;}
  closedir(d);}
#else
 struct uci_package *pkg=uci_modem();struct uci_element *e;
 if(pkg)uci_foreach_element(&pkg->sections,e){
  struct uci_section *sec=uci_to_section(e);
  if(k>=MAXX||strcmp(sec->type,"pdn")||!load_context(sec,&t))continue;
  int dup=0;for(int i=0;i<k;i++)if(n[i].cid==t.cid||!strcmp(n[i].iface,t.iface))dup=1;if(!dup)n[k++]=t;}
#endif
 for(int i=1;i<k;i++)for(int j=i;j>0&&n[j].cid<n[j-1].cid;j--){t=n[j];n[j]=n[j-1];n[j-1]=t;}
 for(int i=0;i<nx;i++){struct xctx *o=&xs[i];int j;
  for(j=0;j<k;j++)if(!strcmp(n[j].name,o->name)&&n[j].cid==o->cid&&!strcmp(n[j].iface,o->iface))break;
  if(j<k){struct xctx *c=&n[j];c->linked=o->linked;c->generation=o->generation;strcpy(c->ip,o->ip);strcpy(c->dns1,o->dns1);strcpy(c->dns2,o->dns2);
   c->redo=strcmp(c->apn,o->apn)||strcmp(c->pdp,o->pdp)||strcmp(c->auth,o->auth)||strcmp(c->user,o->user)||strcmp(c->pass,o->pass);}
  else {xclear(o);if(nxret<MAXX)xret[nxret++]=o->cid;}}
 memcpy(xs,n,sizeof n);nx=k;
}
#ifdef CELL_TEST
static void quoted(FILE *f,const char *s){fputc('"',f);for(const unsigned char *p=(const unsigned char *)s;*p;p++){if(*p=='"'||*p=='\\'){fputc('\\',f);fputc(*p,f);}else if(*p<32)fprintf(f,"\\u%04x",*p);else fputc(*p,f);}fputc('"',f);}
static void status(FILE *f){
 fprintf(f,"{\"state\":");quoted(f,phase);fprintf(f,",\"desired_up\":%s,\"connected\":%s,\"internet_status\":\"not-tested\",\"interface\":",wanted?"true":"false",linked?"true":"false");quoted(f,iface);
 fprintf(f,",\"device\":\"sipa_eth0\",\"port\":");quoted(f,port);fprintf(f,",\"apn\":");quoted(f,apn);fprintf(f,",\"auth\":");quoted(f,auth);fprintf(f,",\"pdp\":");quoted(f,pdp);fprintf(f,",\"sim\":");quoted(f,sim);fprintf(f,",\"operator\":");quoted(f,!strcmp(operator_name,"45412")?"CMHK":operator_name);
 fprintf(f,",\"registration\":%d,\"signal_csq\":%d,\"lte_rsrq_raw\":%d,\"lte_rsrp_raw\":%d",regstat,csq,rsrq,rsrp);
 if(rsrp>=1&&rsrp<=96)fprintf(f,",\"lte_rsrp_lower_dbm\":%d,\"lte_rsrp_upper_dbm\":%d",rsrp-141,rsrp-140);
 if(rsrq>=1&&rsrq<=33)fprintf(f,",\"lte_rsrq_lower_db\":%.1f,\"lte_rsrq_upper_db\":%.1f",-20+rsrq*.5,-19.5+rsrq*.5);
 fprintf(f,",\"cesq_raw\":");quoted(f,cesq_raw);fprintf(f,",\"address\":");quoted(f,ip);fprintf(f,",\"dns\":[");if(*dns1)quoted(f,dns1);if(*dns2&&strcmp(dns1,dns2)){fputc(',',f);quoted(f,dns2);}fprintf(f,"],\"command\":");quoted(f,command);fprintf(f,",\"last_error\":");quoted(f,last_error);
 fprintf(f,",\"contexts\":[");
 for(int i=0;i<nx;i++){const struct xctx *x=&xs[i];if(i)fputc(',',f);
  fprintf(f,"{\"name\":");quoted(f,x->name);fprintf(f,",\"cid\":%d,\"apn\":",x->cid);quoted(f,x->apn);fprintf(f,",\"pdp\":");quoted(f,x->pdp);fprintf(f,",\"auth\":");quoted(f,x->auth);
  fprintf(f,",\"interface\":");quoted(f,x->iface);fprintf(f,",\"device\":");quoted(f,xdev(x));fprintf(f,",\"connected\":%s,\"address\":",x->linked?"true":"false");quoted(f,x->ip);
  fprintf(f,",\"dns\":[");if(*x->dns1)quoted(f,x->dns1);if(*x->dns2&&strcmp(x->dns1,x->dns2)){fputc(',',f);quoted(f,x->dns2);}fprintf(f,"],\"last_error\":");quoted(f,x->error);fprintf(f,",\"generation\":%u}",x->generation);}
 fputc(']',f);
 fprintf(f,",\"requests\":%u,\"timeouts\":%u,\"errors\":%u,\"recoveries\":%u,\"urcs\":%u,\"generation\":%u,\"cycles\":%u,\"radio_resets\":%u,\"pid\":%ld,\"ipv6_supported\":false}\n",requests,timeouts,errors,recoveries,urcs,generation,cycles,radio_resets,(long)getpid());
}
#endif
#ifdef CELL_TEST
static void save_status(void){char tmp[PATH_MAX],dst[PATH_MAX];if(snprintf(tmp,sizeof tmp,"%s/status.tmp",root)>=(int)sizeof tmp||snprintf(dst,sizeof dst,"%s/status.json",root)>=(int)sizeof dst)return;FILE *f=fopen(tmp,"w");if(!f)return;status(f);if(fclose(f)==0)rename(tmp,dst);}
static void setphase(const char *s){snprintf(phase,sizeof phase,"%s",s);save_status();}
#else
static struct ubus_context *ctx;
static struct blob_buf bb;
/* the status as a ubus reply: the same fields as the JSON of status() */
static void status_blob(struct blob_buf *b){
 blob_buf_init(b,0);
 blobmsg_add_string(b,"state",phase);blobmsg_add_u8(b,"desired_up",wanted);blobmsg_add_u8(b,"connected",linked);blobmsg_add_string(b,"internet_status","not-tested");
 blobmsg_add_string(b,"interface",iface);blobmsg_add_string(b,"device","sipa_eth0");blobmsg_add_string(b,"port",port);blobmsg_add_string(b,"apn",apn);
 blobmsg_add_string(b,"auth",auth);blobmsg_add_string(b,"pdp",pdp);blobmsg_add_string(b,"sim",sim);blobmsg_add_string(b,"operator",!strcmp(operator_name,"45412")?"CMHK":operator_name);
 blobmsg_add_u32(b,"registration",(uint32_t)regstat);blobmsg_add_u32(b,"signal_csq",(uint32_t)csq);blobmsg_add_u32(b,"lte_rsrq_raw",(uint32_t)rsrq);blobmsg_add_u32(b,"lte_rsrp_raw",(uint32_t)rsrp);
 if(rsrp>=1&&rsrp<=96){blobmsg_add_u32(b,"lte_rsrp_lower_dbm",(uint32_t)(rsrp-141));blobmsg_add_u32(b,"lte_rsrp_upper_dbm",(uint32_t)(rsrp-140));}
 if(rsrq>=1&&rsrq<=33){blobmsg_add_double(b,"lte_rsrq_lower_db",-20+rsrq*.5);blobmsg_add_double(b,"lte_rsrq_upper_db",-19.5+rsrq*.5);}
 blobmsg_add_string(b,"cesq_raw",cesq_raw);blobmsg_add_string(b,"address",ip);
 void *a=blobmsg_open_array(b,"dns");if(*dns1)blobmsg_add_string(b,NULL,dns1);if(*dns2&&strcmp(dns1,dns2))blobmsg_add_string(b,NULL,dns2);blobmsg_close_array(b,a);
 blobmsg_add_string(b,"command",command);blobmsg_add_string(b,"last_error",last_error);
 void *cs=blobmsg_open_array(b,"contexts");
 for(int i=0;i<nx;i++){const struct xctx *x=&xs[i];void *t=blobmsg_open_table(b,NULL);
  blobmsg_add_string(b,"name",x->name);blobmsg_add_u32(b,"cid",(uint32_t)x->cid);blobmsg_add_string(b,"apn",x->apn);blobmsg_add_string(b,"pdp",x->pdp);blobmsg_add_string(b,"auth",x->auth);
  blobmsg_add_string(b,"interface",x->iface);blobmsg_add_string(b,"device",xdev(x));blobmsg_add_u8(b,"connected",x->linked);blobmsg_add_string(b,"address",x->ip);
  void *d=blobmsg_open_array(b,"dns");if(*x->dns1)blobmsg_add_string(b,NULL,x->dns1);if(*x->dns2&&strcmp(x->dns1,x->dns2))blobmsg_add_string(b,NULL,x->dns2);blobmsg_close_array(b,d);
  blobmsg_add_string(b,"last_error",x->error);blobmsg_add_u32(b,"generation",x->generation);blobmsg_close_table(b,t);}
 blobmsg_close_array(b,cs);
 blobmsg_add_u32(b,"requests",requests);blobmsg_add_u32(b,"timeouts",timeouts);blobmsg_add_u32(b,"errors",errors);blobmsg_add_u32(b,"recoveries",recoveries);blobmsg_add_u32(b,"urcs",urcs);
 blobmsg_add_u32(b,"generation",generation);blobmsg_add_u32(b,"cycles",cycles);blobmsg_add_u32(b,"radio_resets",radio_resets);blobmsg_add_u32(b,"pid",(uint32_t)getpid());blobmsg_add_u8(b,"ipv6_supported",0);
}
static void save_status(void){}
/* the state changes are announced (event u30pro.cell.state); the status itself is the method status */
static void setphase(const char *s){
 static struct blob_buf e;static int last_linked=-1;
 if(!strcmp(phase,s)&&last_linked==linked)return;
 snprintf(phase,sizeof phase,"%s",s);last_linked=linked;
 if(!ctx)return;
 blob_buf_init(&e,0);blobmsg_add_string(&e,"state",phase);blobmsg_add_u8(&e,"connected",linked);ubus_send_event(ctx,"u30pro.cell.state",e.head);
}
#endif
static pid_t notify_pid;
static void hook_failed(void){notify_dirty=1;for(int i=0;i<nx;i++)xs[i].dirty=1;}
#ifndef CELL_TEST
/* netifd: a link that comes up is set up again (down, up: what ifup does), its protocol handler u30cell fetches address and DNS from
   the status; a link that goes away is taken down.  The internet context is the interface given to --run, the others cell_<name>. */
static int netifd_call(const char *ifc,const char *method){
 uint32_t id;char path[64];
 if(!ctx||snprintf(path,sizeof path,"network.interface.%s",ifc)>=(int)sizeof path||ubus_lookup_id(ctx,path,&id))return -1;
 blob_buf_init(&bb,0);return ubus_invoke(ctx,id,method,bb.head,NULL,NULL,3000);
}
static void run_notify(const char *ifc,int up,const char *a,const char *b,const char *c,const char *dev){
 (void)a;(void)b;(void)c;
 if(dev){char cmd[64];snprintf(cmd,sizeof cmd,"ip link set dev %s up",dev);if(up&&system(cmd)){hook_failed();return;}}
 if(up)netifd_call(ifc,"down");
 if(netifd_call(ifc,up?"up":"down")){snprintf(last_error,sizeof last_error,"netifd interface %.32s %s failed",ifc,up?"up":"down");hook_failed();}
}
#else
/* the internet context passes no device (the hook's old five arguments); additional contexts pass theirs */
static void run_notify(const char *ifc,int up,const char *a,const char *b,const char *c,const char *dev){
 /* one hook at a time, in order: a slow "down" (ifdown) finishing after the following "up" left the link dead */
 if(notify_pid>0){int st=0,n=0;pid_t w;while((w=waitpid(notify_pid,&st,WNOHANG))==0&&n<200){usleep(100000);n++;}if(w==0){kill(notify_pid,SIGKILL);waitpid(notify_pid,&st,0);}if(!WIFEXITED(st)||WEXITSTATUS(st))hook_failed();notify_pid=0;}
 pid_t p=fork();notify_pid=p>0?p:0;if(p==0){if(dev)execl(notify,notify,ifc,up?"up":"down",up?a:"",up?b:"",up?c:"",dev,NULL);else execl(notify,notify,ifc,up?"up":"down",up?a:"",up?b:"",up?c:"",NULL);_exit(127);}if(p<0)snprintf(last_error,sizeof last_error,"notify-fork-failed");
}
#endif
static void publish(int up){run_notify(iface,up,ip,dns1,dns2,NULL);}
static void clear_link(void){if(linked){linked=0;publish(0);}ip[0]=dns1[0]=dns2[0]=0;notify_dirty=1;}
static void xclear(struct xctx *x){if(x->linked){x->linked=0;run_notify(x->iface,0,"","","",xdev(x));}x->ip[0]=x->dns1[0]=x->dns2[0]=0;x->dirty=1;}
static void xclear_all(void){for(int i=0;i<nx;i++)xclear(&xs[i]);}
static struct xctx *xfind(int cid){for(int i=0;i<nx;i++)if(xs[i].cid==cid)return &xs[i];return NULL;}
struct token {char s[160];int quoted;};
static int csv(const char *s,struct token *t,int max){
 int n=0;while(*s&&n<max){while(*s==' ')s++;size_t k=0;t[n].quoted=*s=='"';if(t[n].quoted)s++;while(*s&&!(t[n].quoted?*s=='"':*s==',')){if(k+1<sizeof t[n].s)t[n].s[k++]=*s;s++;}t[n].s[k]=0;if(t[n].quoted&&*s=='"')s++;while(*s&&*s!=',')s++;n++;if(*s==',')s++;else break;}return n;
}
static int integer(const char *s,int *v){char *e;long n=strtol(s,&e,10);if(!*s||*e||n<0||n>1000)return 0;*v=(int)n;return 1;}
static int ipv4(const char *s,char out[16],int extended){
 char b[160];if(strlen(s)>=sizeof b||!*s||*s=='.'||s[strlen(s)-1]=='.'||strstr(s,".."))return 0;strcpy(b,s);unsigned octets[8];int n=0;char *save=NULL;
 for(char *p=strtok_r(b,".",&save);p;p=strtok_r(NULL,".",&save)){int v;if(n>=8||!integer(p,&v)||v>255)return 0;octets[n++]=(unsigned)v;}
 if(n!=4&&!(extended&&n==8))return 0;snprintf(out,16,"%u.%u.%u.%u",octets[0],octets[1],octets[2],octets[3]);struct in_addr a;return inet_pton(AF_INET,out,&a)==1&&a.s_addr!=0;
}
static void parse_line(const char *s){
 struct token t[16];int n,v;
 if(!strncmp(s,"+CPIN:",6)){s+=6;while(*s==' ')s++;snprintf(sim,sizeof sim,"%.31s",s);if(step==CPIN)have_reply=1;}
 else if(!strncmp(s,"+CFUN:",6)){if(integer(s+6+strspn(s+6," "),&v)){cfun=v;if(step==CFUN)have_reply=1;}}
 else if(!strncmp(s,"+CEREG:",7)){
  n=csv(s+7,t,16);int a,b;if(n&&integer(t[0].s,&a)){regstat=a;if(n>1&&!t[1].quoted&&integer(t[1].s,&b))regstat=b;if(step==REG)have_reply=1;else urcs++;if(regstat!=1&&regstat!=5){clear_link();xclear_all();setphase("registering");}else unreg_since=0;}
 }
 else if(!strncmp(s,"+COPS:",6)){n=csv(s+6,t,16);if(n>=3){snprintf(operator_name,sizeof operator_name,"%.63s",t[2].s);if(step==COPS)have_reply=1;}}
 else if(!strncmp(s,"+CSQ:",5)){n=csv(s+5,t,16);if(n>=2&&integer(t[0].s,&v)){csq=v;if(step==CSQ)have_reply=1;}}
 else if(!strncmp(s,"+CESQ:",6)){n=csv(s+6,t,16);if(n>=6){if(integer(t[4].s,&v))rsrq=v;if(integer(t[5].s,&v))rsrp=v;snprintf(cesq_raw,sizeof cesq_raw,"%.191s",s+6);if(step==CESQ)have_reply=1;}}
 else if(!strncmp(s,"+CGACT:",7)){n=csv(s+7,t,16);int cid;if(n>=2&&integer(t[0].s,&cid)&&integer(t[1].s,&v)){if(cid<16)act[cid]=v;if(cid==1){context=v;if(step==ACT)have_reply=1;}if(step==XACT)have_reply=1;}}
 else if(step==CFG&&!strncmp(s,"+CGDCONT:",9)){
  n=csv(s+9,t,16);int cid;if(n>=3&&integer(t[0].s,&cid)&&cid==1){snprintf(configured_apn,sizeof configured_apn,"%.63s",t[2].s);snprintf(configured_pdp,sizeof configured_pdp,"%.15s",t[1].s);have_reply=1;}
 }
 else if(step==XCFG&&!strncmp(s,"+CGDCONT:",9)){
  n=csv(s+9,t,16);int cid;struct xctx *x;if(n>=3&&integer(t[0].s,&cid)&&(x=xfind(cid))){snprintf(x->cfg_apn,sizeof x->cfg_apn,"%.63s",t[2].s);snprintf(x->cfg_pdp,sizeof x->cfg_pdp,"%.15s",t[1].s);}have_reply=1;
 }
 else if(step==XPDP&&!strncmp(s,"+CGCONTRDP:",11)){
  /* an additional context needs an address; the DNS servers are optional (an MMS APN may have none) */
  n=csv(s+11,t,16);int cid;char a[16],b[16]="",c[16]="";struct xctx *x=xi>=0&&xi<nx?&xs[xi]:NULL;
  if(x&&n>=4&&integer(t[0].s,&cid)&&cid==x->cid&&ipv4(t[3].s,a,1)){if(n<6||!ipv4(t[5].s,b,0))b[0]=0;if(n<7||!ipv4(t[6].s,c,0))strcpy(c,b);strcpy(x->tip,a);strcpy(x->tdns1,b);strcpy(x->tdns2,c);have_reply=1;}
 }
 else if(step==PDP&&!strncmp(s,"+CGCONTRDP:",11)){
  n=csv(s+11,t,16);int cid;char a[16],b[16],c[16];if(n>=7&&integer(t[0].s,&cid)&&cid==1&&ipv4(t[3].s,a,1)&&ipv4(t[5].s,b,0)){
   if(!ipv4(t[6].s,c,0))strcpy(c,b);snprintf(actual_apn,sizeof actual_apn,"%.63s",t[2].s);
   /* Publish only after this query's terminal success. */
   strcpy(ip,a);strcpy(dns1,b);strcpy(dns2,c);if(step==PDP)have_reply=1;
  }
 }
 else if(!strncmp(s,"+CGEV:",6)&&(strstr(s,"DEACT")||strstr(s,"DETACH"))){
  /* per CID: "PDN DEACT <cid>"; "DEACT <p_cid>,<cid>,<type>" (a dedicated bearer; the context stays) is ignored; the old
     "DEACT <type>,<addr>[,<cid>]" without a CID is taken as CID1; a detach takes everything. Another owner's CID (IMS) is not ours. */
  const char *p=strstr(s,"PDN DEACT");int cid=-1;urcs++;
  if(strstr(s,"DETACH"))cid=0;
  else if(p){n=csv(p+9,t,16);if(!n||!integer(t[0].s,&cid))cid=-1;}
  else {n=csv(strstr(s,"DEACT")+5,t,16);if(n>=2&&!t[0].quoted&&!t[1].quoted&&integer(t[0].s,&v)&&integer(t[1].s,&v))cid=-1;else if(!n||!integer(t[n-1].s,&cid))cid=1;}
  if(cid==0||cid==1){context=0;clear_link();next_cycle=now_ms();setphase("data-unavailable");}
  if(cid==0)xclear_all();else {struct xctx *x=xfind(cid);if(x){xclear(x);x->next_try=0;}}
 }
}
static int open_port(void){
 fd=open(port,O_RDWR|O_NOCTTY|O_NONBLOCK|O_CLOEXEC);if(fd<0)return -1;
 if(flock(fd,LOCK_EX|LOCK_NB)){close(fd);fd=-1;return -1;}
 struct termios t;if(tcgetattr(fd,&t)){close(fd);fd=-1;return -1;}cfmakeraw(&t);t.c_cflag|=CLOCAL|CREAD;t.c_cflag&=~CRTSCTS;if(tcsetattr(fd,TCSANOW,&t)){close(fd);fd=-1;return -1;}return 0;
}
static void fail_cycle(const char *reason){
 snprintf(last_error,sizeof last_error,"%.48s (%.72s)",reason,command);errors++;clear_link();xclear_all();xi=-1;step=IDLE;command[0]=0;unsigned power=errors>4?4:errors;next_cycle=now_ms()+BACKOFF*(1U<<power);setphase(wanted?"data-unavailable":"disconnected");
}
static void send_at(int id,const char *s){
 /* A successful empty CGACT list means CID1 has not been defined yet. */
 if(id==ACT)context=0;
 if(fd<0&&open_port()){fail_cycle("at-port-unavailable");return;}
 size_t len=strlen(s);char b[164];if(len+2>=sizeof b){fail_cycle("command-length");return;}memcpy(b,s,len);b[len++]='\r';size_t pos=0;
 while(pos<len){ssize_t n=write(fd,b+pos,len-pos);if(n>0)pos+=(size_t)n;else if(n<0&&(errno==EAGAIN||errno==EINTR)){struct pollfd p={fd,POLLOUT,0};if(poll(&p,1,100)<=0){fail_cycle("at-write-timeout");return;}}else {fail_cycle("at-write-failed");return;}}
 step=id;have_reply=0;requests++;snprintf(command,sizeof command,"%s",s);if(id==ENABLE||id==RADIOON)last_enable=now_ms();
 deadline=now_ms()+((id==ENABLE||id==RADIOON)?ENABLE_TIMEOUT:(id==DIAL||id==DEACT||id==DISCONNECT||id==RADIOOFF||id==XDIAL||id==XDEACT||id==XDOWN)?DATA_TIMEOUT:QUERY_TIMEOUT);save_status();
}
/* The radio goes on the way the stock RIL does it (libimpl-ril.so processRadioPowerOn): on a TSX-crystal board (crystal=7 on the
   command line) the crystal temperature first (AT+SPTSXTMP=<m°C>, getAndSendTemperature), then the protocol stack (AT+SFUN=4), which
   turns the radio on by itself.  With AT+CFUN=1 alone about every third boot left the radio dead; with this sequence none did
   (2026-10-23, 7 of 7).  The temperature comes from the PMIC sensor (modem_tsen, as stock) or, without that driver, the board zone. */
static int tsx_temp(char *out,size_t n){
#ifdef CELL_TEST
 const char *t=getenv("U30_CELL_TEST_TSX");if(!t||!*t)return 0;snprintf(out,n,"%s",t);return 1;
#else
 char b[2048];FILE *f=fopen("/proc/cmdline","r");if(!f)return 0;size_t k=fread(b,1,sizeof b-1,f);fclose(f);b[k]=0;
 for(char *p=b;*p;p++)if(*p=='\n'||*p=='\t')*p=' ';   /* the command line of this device has embedded newlines */
 if(!strstr(b," crystal=7 ")&&strncmp(b,"crystal=7 ",10))return 0;
 long v=LONG_MIN;char path[96],line[64];
 if((f=fopen("/sys/bus/platform/drivers/ump96xx-tsensor/modem_tsen","r"))){if(fgets(line,sizeof line,f))v=strtol(line,NULL,10);fclose(f);}
 for(int i=0;v==LONG_MIN&&i<64;i++){
  snprintf(path,sizeof path,"/sys/class/thermal/thermal_zone%d/type",i);if(!(f=fopen(path,"r")))continue;int board=fgets(line,sizeof line,f)&&!strcmp(line,"board-thmzone\n");fclose(f);if(!board)continue;
  snprintf(path,sizeof path,"/sys/class/thermal/thermal_zone%d/temp",i);if((f=fopen(path,"r"))){if(fgets(line,sizeof line,f))v=strtol(line,NULL,10);fclose(f);}}
 if(v<-40000||v>125000)return 0;
 snprintf(out,n,"%ld",v);return 1;
#endif
}
static void radio_on(int id){
 char t[24],cmd[40];
 if(tsx_temp(t,sizeof t)){tsx_then=id;snprintf(cmd,sizeof cmd,"AT+SPTSXTMP=%s",t);send_at(TSXTMP,cmd);}
 else send_at(id,"AT+SFUN=4");
}
static const char *pdp_at(void){return !strcmp(pdp,"ipv4")?"IP":"IPV4V6";}
static void start_apn(void){char cmd[160];snprintf(cmd,sizeof cmd,"AT+CGDCONT=1,\"%s\",\"%s\"",pdp_at(),apn);send_at(APN,cmd);}
/* The pass over the additional contexts: AT+CGACT? and AT+CGDCONT?, then per context (in CID order) a check of an active one
   (AT+CGCONTRDP) or a dial (CGDCONT, CGAUTH, CGDATA, CGCONTRDP). It runs after each successful check of the internet context,
   and after a disconnect to take them down. */
static void xstart(void){if(!nx&&!nxret)return;memset(act,0,sizeof act);xi=-1;xcid=0;send_at(XACT,"AT+CGACT?");}
/* the internet context went away, is being redialled or taken down: leave it to the main cycle and come back after it */
static int xabort(void){return reconnecting||(wanted&&!linked)||(!wanted&&strcmp(phase,"disconnected"));}
static void xfail(struct xctx *x,const char *why){
 snprintf(x->error,sizeof x->error,"%.48s (%.72s)",why,command);x->errors++;xclear(x);unsigned p=x->errors>6?6:x->errors;x->next_try=now_ms()+BACKOFF*(1L<<p);
}
static const char *xpdp_at(const struct xctx *x){return !strcmp(x->pdp,"ipv4")?"IP":"IPV4V6";}
static void xsend(int id,struct xctx *x,const char *fmt){char cmd[160];snprintf(cmd,sizeof cmd,fmt,x->cid);xcid=x->cid;send_at(id,cmd);}
static void xapn(struct xctx *x){char cmd[160];x->redo=0;xcid=x->cid;snprintf(cmd,sizeof cmd,"AT+CGDCONT=%d,\"%s\",\"%s\"",x->cid,xpdp_at(x),x->apn);send_at(XAPN,cmd);}
static void xnext(void){
 if(xabort()){xi=-1;return;}
 while(xi<0&&nxret){int cid=xret[--nxret];if(act[cid]==1){xcid=cid;char cmd[32];snprintf(cmd,sizeof cmd,"AT+CGACT=0,%d",cid);send_at(XDOWN,cmd);return;}}
 while(++xi<nx){
  struct xctx *x=&xs[xi];int a=act[x->cid]==1;
  if(!wanted){if(a){xsend(XDOWN,x,"AT+CGACT=0,%d");return;}xclear(x);continue;}
  if(x->next_try>now_ms())continue;
  /* CGCONTRDP reports the network-expanded APN, so the configured one comes from CGDCONT? */
  if(a&&(x->redo||strcasecmp(x->cfg_apn,x->apn)||(*x->cfg_pdp&&strcasecmp(x->cfg_pdp,xpdp_at(x))))){xclear(x);xsend(XDEACT,x,"AT+CGACT=0,%d");return;}
  if(a){xsend(XPDP,x,"AT+CGCONTRDP=%d");return;}
  xclear(x);xapn(x);return;
 }
 xi=-1;
}
static void xdone(int old,int ok){
 struct xctx *x=xi>=0&&xi<nx?&xs[xi]:NULL;
 if(xabort()){command[0]=0;xi=-1;return;}
 if(old==XACT||old==XCFG){command[0]=0;if(!ok){xi=-1;return;}if(old==XACT){for(int i=0;i<nx;i++)xs[i].cfg_apn[0]=xs[i].cfg_pdp[0]=0;send_at(XCFG,"AT+CGDCONT?");}else {xi=-1;xnext();}return;}
 /* a failed take-down (already inactive) still ends it */
 if(old==XDOWN){command[0]=0;if(xcid>0&&xcid<16)act[xcid]=0;if(x)xclear(x);xnext();return;}
 if(!x){command[0]=0;xi=-1;return;}
 if(!ok||(old==XPDP&&!have_reply)){xfail(x,ok?"missing-response":"modem-command-error");command[0]=0;xnext();return;}
 command[0]=0;
 switch(old){
 case XDEACT:act[x->cid]=0;xapn(x);break;
 case XAPN:{char cmd[160];if(!strcmp(x->auth,"none"))snprintf(cmd,sizeof cmd,"AT+CGAUTH=%d,0",x->cid);else snprintf(cmd,sizeof cmd,"AT+CGAUTH=%d,%d,\"%s\",\"%s\"",x->cid,!strcmp(x->auth,"pap")?1:2,x->user,x->pass);send_at(XAUTH,cmd);}break;
 case XAUTH:xsend(XDIAL,x,"AT+CGDATA=\"M-ETHER\",%d");break;
 case XDIAL:act[x->cid]=1;xsend(XPDP,x,"AT+CGCONTRDP=%d");break;
 case XPDP:
  if(!x->linked){x->linked=1;x->generation++;}
  if(x->dirty||strcmp(x->ip,x->tip)||strcmp(x->dns1,x->tdns1)||strcmp(x->dns2,x->tdns2)){strcpy(x->ip,x->tip);strcpy(x->dns1,x->tdns1);strcpy(x->dns2,x->tdns2);run_notify(x->iface,1,x->ip,x->dns1,x->dns2,xdev(x));x->dirty=0;}
  x->error[0]=0;x->errors=0;xnext();break;
 default:xi=-1;break;
 }
}
/* a command of the pass got no answer: that context backs off, the rest waits for the next pass */
static void xtimeout(void){struct xctx *x=xi>=0&&xi<nx?&xs[xi]:NULL;if(x&&step!=XACT&&step!=XCFG&&step!=XDOWN)xfail(x,"at-timeout");step=IDLE;command[0]=0;xi=-1;save_status();}
static void completed(int ok){
 int old=step;step=IDLE;
 if(old>=XACT){xdone(old,ok);if(step==IDLE)save_status();return;}
 /* the temperature is advisory: the stack is opened whatever the answer (the stock RIL does the same) */
 if(old==TSXTMP){command[0]=0;send_at(tsx_then,"AT+SFUN=4");return;}
 /* a CP that refuses AT+SFUN=4 gets the standard request once */
 if(!ok&&(old==ENABLE||old==RADIOON)&&!strcmp(command,"AT+SFUN=4")){send_at(old,"AT+CFUN=1");return;}
 /* the failed command stays set until fail_cycle has put it into last_error */
 if(!ok||(old!=ACT&&old!=ENABLE&&old!=DEACT&&old!=APN&&old!=AUTHSET&&old!=DIAL&&old!=DISCONNECT&&old!=RADIOOFF&&old!=RADIOON&&!have_reply)){
  if(old==COPS){send_at(CSQ,"AT+CSQ");return;}
  if(old==CSQ){send_at(CESQ,"AT+CESQ");return;}
  if(old==CESQ){send_at(ACT,"AT+CGACT?");return;}
  if(old==DISCONNECT){command[0]=0;clear_link();next_cycle=LONG_MAX;setphase("disconnected");xstart();return;}
  fail_cycle(ok?"missing-response":"modem-command-error");return;
 }
 command[0]=0;
 if(!wanted&&old!=DISCONNECT){clear_link();send_at(DISCONNECT,"AT+CGACT=0,1");return;}
 if(reconnecting&&old!=DEACT){clear_link();setphase("reconnecting");send_at(DEACT,"AT+CGACT=0,1");return;}
 switch(old){
 case CPIN:
  if(strcmp(sim,"READY")){clear_link();next_cycle=now_ms()+INTERVAL;setphase("waiting-sim");}
  else send_at(CFUN,"AT+CFUN?");break;
 /* a radio seen on clears the gap, so a radio lost later (a CP reset) gets one prompt retry; last_error keeps the failure */
 case CFUN:
  if(cfun==1){last_enable=0;send_at(REG,"AT+CEREG?");}
  else if(!last_enable||now_ms()-last_enable>=ENABLE_GAP_MS)radio_on(ENABLE);
  else {clear_link();next_cycle=now_ms()+INTERVAL;setphase("data-unavailable");}
  break;
 case ENABLE:send_at(REG,"AT+CEREG?");break;
 case REG:send_at(COPS,"AT+COPS?");break;
 case COPS:send_at(CSQ,"AT+CSQ");break;
 case CSQ:send_at(CESQ,"AT+CESQ");break;
 case CESQ:
  if(regstat!=1&&regstat!=5){
   long t=now_ms();clear_link();if(!unreg_since)unreg_since=t;
   /* a modem that stays unregistered (e.g. "emergency only") often needs the radio cycled; cap the rate */
   if(wanted&&t-unreg_since>=REG_STALL_MS&&(!last_radio_reset||t-last_radio_reset>=RADIO_GAP_MS)){last_radio_reset=t;radio_resets++;setphase("radio-reset");send_at(RADIOOFF,"AT+SFUN=5");break;}
   next_cycle=t+INTERVAL;setphase("registering");}
  else {unreg_since=0;send_at(ACT,"AT+CGACT?");}break;
 case RADIOOFF:radio_on(RADIOON);break;
 case RADIOON:unreg_since=now_ms();next_cycle=now_ms()+INTERVAL;setphase("registering");break;
 case ACT:if(context==1)send_at(CFG,"AT+CGDCONT?");else {clear_link();setphase("dialing");start_apn();}break;
 case CFG:
  /* CGCONTRDP reports the network-expanded APN, not the configured APN. */
  if(strcasecmp(configured_apn,apn)||(*configured_pdp&&strcasecmp(configured_pdp,pdp_at()))){clear_link();setphase("reconfiguring");send_at(DEACT,"AT+CGACT=0,1");}else send_at(PDP,"AT+CGCONTRDP=1");break;
 case PDP:
  if(!linked){linked=1;generation++;recoveries++;}if(notify_dirty||strcmp(notified_ip,ip)||strcmp(notified_dns1,dns1)||strcmp(notified_dns2,dns2)){publish(1);strcpy(notified_ip,ip);strcpy(notified_dns1,dns1);strcpy(notified_dns2,dns2);notify_dirty=0;}last_error[0]=0;errors=0;cycles++;next_cycle=now_ms()+INTERVAL;setphase("connected");xstart();break;
 case DEACT:context=0;reconnecting=0;start_apn();break;
 case APN:
  if(have_profile){char cmd[160];if(!strcmp(auth,"none"))snprintf(cmd,sizeof cmd,"AT+CGAUTH=1,0");else snprintf(cmd,sizeof cmd,"AT+CGAUTH=1,%d,\"%s\",\"%s\"",!strcmp(auth,"pap")?1:2,puser,ppass);send_at(AUTHSET,cmd);}
  else send_at(DIAL,"AT+CGDATA=\"M-ETHER\",1");break;
 case AUTHSET:send_at(DIAL,"AT+CGDATA=\"M-ETHER\",1");break;
 case DIAL:context=1;send_at(PDP,"AT+CGCONTRDP=1");break;
 case DISCONNECT:context=0;clear_link();next_cycle=LONG_MAX;setphase("disconnected");xstart();break;
 default:break;
 }
}
static void receive_serial(void){
 char b[512];ssize_t count=read(fd,b,sizeof b);if(count==0)return;if(count<0){if(errno!=EAGAIN&&errno!=EINTR){close(fd);fd=-1;fail_cycle("at-read-failed");}return;}
 for(ssize_t i=0;i<count;i++){
  unsigned char c=(unsigned char)b[i];if(c=='\r'||c=='\n'){
   if(!rxlen){dropping_line=0;continue;}rxline[rxlen]=0;
   if(!dropping_line){if(!strcmp(rxline,"OK")||!strcmp(rxline,"CONNECT")){if(step!=IDLE)completed(1);}else if(!strcmp(rxline,"ERROR")||!strncmp(rxline,"+CME ERROR:",11)||!strcmp(rxline,"NO CARRIER")){if(step!=IDLE)completed(0);else {clear_link();next_cycle=now_ms();setphase("data-unavailable");}}else parse_line(rxline);}
   rxlen=0;dropping_line=0;
  }else if(c>=32||c=='\t'){if(rxlen+1<sizeof rxline)rxline[rxlen++]=(char)c;else dropping_line=1;}
 }
}
/* a control command; NULL when it was accepted, otherwise the error */
static const char *control(const char *b){
 if(!strcmp(b,"refresh")){if(wanted&&step==IDLE)next_cycle=now_ms();return NULL;}
 if(!strcmp(b,"reconnect")){wanted=1;reconnecting=1;clear_link();next_cycle=now_ms();setphase("reconnecting");return NULL;}
 /* the additional contexts are reread when the daemon is idle; the internet context is redialled only when its profile changed
    (or it is not up) */
 if(!strcmp(b,"reload")){char o[256],n2[256];snprintf(o,sizeof o,"%s|%s|%s|%s|%s",apn,auth,pdp,puser,ppass);int r=load_profile();xreload=1;
  if(r<0){snprintf(last_error,sizeof last_error,"invalid-profile");return "invalid-profile";}
  snprintf(n2,sizeof n2,"%s|%s|%s|%s|%s",apn,auth,pdp,puser,ppass);if(strcmp(o,n2)||!wanted||!linked){wanted=1;reconnecting=1;clear_link();next_cycle=now_ms();setphase("reconnecting");}return NULL;}
 if(!strcmp(b,"disconnect")){wanted=0;reconnecting=0;clear_link();xclear_all();next_cycle=now_ms();setphase("disconnecting");return NULL;}
 return "unsupported-command";
}
#ifdef CELL_TEST
static void accept_control(void){
 int c=accept4(listener,NULL,NULL,SOCK_CLOEXEC);if(c<0)return;struct timeval t={1,0};setsockopt(c,SOL_SOCKET,SO_RCVTIMEO,&t,sizeof t);setsockopt(c,SOL_SOCKET,SO_SNDTIMEO,&t,sizeof t);char b[64];ssize_t n=recv(c,b,sizeof b-1,0);FILE *f=fdopen(c,"w");if(!f){close(c);return;}
 if(n<=0){fclose(f);return;}b[n]=0;b[strcspn(b,"\r\n")]=0;
 if(!strcmp(b,"status"))status(f);
 else {const char *e=control(b);if(!e)fprintf(f,"{\"accepted\":true}\n");else if(!strcmp(e,"unsupported-command"))fprintf(f,"{\"error\":\"unsupported-command\"}\n");else fprintf(f,"{\"accepted\":false,\"error\":\"%s\"}\n",e);}
 fclose(f);
}
static int ctl(const char *s){
 if(strcmp(s,"status")&&strcmp(s,"refresh")&&strcmp(s,"reconnect")&&strcmp(s,"disconnect")&&strcmp(s,"reload"))return 2;
 int c=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);if(c<0)return 1;struct sockaddr_un a={.sun_family=AF_UNIX};if(strlen(sockpath)>=sizeof a.sun_path)return 2;strcpy(a.sun_path,sockpath);struct timeval t={2,0};setsockopt(c,SOL_SOCKET,SO_RCVTIMEO,&t,sizeof t);if(connect(c,(struct sockaddr *)&a,sizeof a)){close(c);return 1;}
 if(send(c,s,strlen(s),MSG_NOSIGNAL)!=(ssize_t)strlen(s)){close(c);return 1;}shutdown(c,SHUT_WR);char b[1024];ssize_t n;while((n=read(c,b,sizeof b))>0)fwrite(b,1,(size_t)n,stdout);close(c);return n<0?1:0;
}
#else
static int m_status(struct ubus_context *c,struct ubus_object *o,struct ubus_request_data *req,const char *method,struct blob_attr *msg){
 (void)o;(void)method;(void)msg;status_blob(&bb);ubus_send_reply(c,req,bb.head);return 0;}
static int m_control(struct ubus_context *c,struct ubus_object *o,struct ubus_request_data *req,const char *method,struct blob_attr *msg){
 (void)o;(void)msg;const char *e=control(method);blob_buf_init(&bb,0);blobmsg_add_u8(&bb,"accepted",!e);if(e)blobmsg_add_string(&bb,"error",e);ubus_send_reply(c,req,bb.head);return 0;}
static const struct ubus_method methods[]={UBUS_METHOD_NOARG("status",m_status),UBUS_METHOD_NOARG("refresh",m_control),UBUS_METHOD_NOARG("reconnect",m_control),
 UBUS_METHOD_NOARG("disconnect",m_control),UBUS_METHOD_NOARG("reload",m_control)};
static struct ubus_object_type object_type=UBUS_OBJECT_TYPE("u30pro.cell",methods);
static struct ubus_object object={.name="u30pro.cell",.type=&object_type,.methods=methods,.n_methods=ARRAY_SIZE(methods)};
/* the command-line form of the methods */
static int ctl(const char *s){
 if(strcmp(s,"status")&&strcmp(s,"refresh")&&strcmp(s,"reconnect")&&strcmp(s,"disconnect")&&strcmp(s,"reload"))return 2;
 execl("/bin/ubus","ubus","-t","5","call","u30pro.cell",s,(char *)NULL);return 1;
}
#endif
/* one pass of the state machine; 1 when the daemon has finished stopping */
static int tick(void){
 long now=now_ms();int child_status;pid_t w;while((w=waitpid(-1,&child_status,WNOHANG))>0){if(w==notify_pid)notify_pid=0;if(!WIFEXITED(child_status)||WEXITSTATUS(child_status))hook_failed();}
 if(quitting&&!stopping){stopping=1;wanted=0;clear_link();xclear_all();stop_deadline=now+3000;}
 if(stopping&&(now>=stop_deadline||(step==IDLE&&!strcmp(phase,"disconnected"))))return 1;
 if(step!=IDLE&&now>=deadline){timeouts++;if(step>=XACT)xtimeout();else if(step==TSXTMP){step=IDLE;command[0]=0;send_at(tsx_then,"AT+SFUN=4");}else fail_cycle("at-timeout");}
 if(xreload&&step==IDLE){xreload=0;load_contexts();save_status();if((wanted&&linked&&!reconnecting)||(!wanted&&!strcmp(phase,"disconnected")))xstart();}
 if(step==IDLE&&now>=next_cycle){
  if(!wanted){send_at(DISCONNECT,"AT+CGACT=0,1");next_cycle=LONG_MAX;}
  else if(reconnecting){clear_link();setphase("reconnecting");send_at(DEACT,"AT+CGACT=0,1");}
  else {setphase(linked?"connected":"checking");send_at(CPIN,"AT+CPIN?");}
 }
 return 0;
}
/* the port while stopping (and in the tests): poll it and run the state machine every 100 ms */
static int poll_once(void){
 struct pollfd p[2]={{listener,POLLIN,0},{fd,POLLIN,0}};int q=poll(p,2,100);if(q<0&&errno!=EINTR)return -1;
#ifdef CELL_TEST
 if(p[0].revents&POLLIN)accept_control();
#endif
 if(p[1].revents&POLLIN)receive_serial();if(p[1].revents&(POLLERR|POLLHUP|POLLNVAL)){if(fd>=0)close(fd);fd=-1;fail_cycle("at-port-disconnected");}
 return 0;
}
#ifndef CELL_TEST
static struct uloop_fd serial_fd;
static void serial_event(struct uloop_fd *u,unsigned int events){
 (void)events;
 if(u->eof||u->error){uloop_fd_delete(u);u->fd=-1;if(fd>=0)close(fd);fd=-1;fail_cycle("at-port-disconnected");return;}
 receive_serial();
}
/* the port is opened and closed by the state machine; keep uloop watching the one that is open */
static void watch_port(void){
 if(serial_fd.fd==fd&&(fd<0||serial_fd.registered))return;
 if(serial_fd.registered)uloop_fd_delete(&serial_fd);
 serial_fd.fd=fd;serial_fd.eof=serial_fd.error=false;
 if(fd>=0)uloop_fd_add(&serial_fd,ULOOP_READ);
}
static void tick_timer(struct uloop_timeout *t){if(tick()){uloop_end();return;}watch_port();uloop_timeout_set(t,100);}
#endif
int main(int argc,char **argv){
 umask(077);
#ifdef CELL_TEST
 const char *r=getenv("U30_CELL_TEST_DIR"),*hook=getenv("U30_CELL_TEST_NOTIFY");if(r&&strlen(r)<sizeof root-30)strcpy(root,r);if(hook&&strlen(hook)<sizeof notify)strcpy(notify,hook);
#endif
 if(snprintf(sockpath,sizeof sockpath,"%s/control.sock",root)>=(int)sizeof sockpath)return 2;
 if(argc==3&&!strcmp(argv[1],"--ctl"))return ctl(argv[2]);
 if(argc!=5||strcmp(argv[1],"--run")||!valid_name(argv[2],32,0)||!valid_name(argv[4],63,1))return 2;
#ifndef CELL_TEST
 if(strcmp(argv[3],"/dev/stty_nr0")||geteuid()!=0)return 2;
#endif
 if(strlen(argv[3])>=sizeof port)return 2;strcpy(iface,argv[2]);strcpy(port,argv[3]);strcpy(apn,argv[4]);if(mkdir(root,0700)&&errno!=EEXIST){perror("mkdir");return 1;}chmod(root,0700);
#ifdef CELL_TEST
 if(load_profile()<0){fprintf(stderr,"invalid dial profile\n");return 2;}
#else
 /* invalid settings: the command line APN, as without settings (the web interface does not store invalid ones) */
 if(load_profile()<0){fprintf(stderr,"u30pro-celld: invalid dial settings in u30pro_modem.dial, using the APN %s\n",apn);snprintf(last_error,sizeof last_error,"invalid-profile");}
#endif
 load_contexts();
 char lockpath[PATH_MAX];if(snprintf(lockpath,sizeof lockpath,"%s/owner.lock",root)>=(int)sizeof lockpath)return 2;lockfd=open(lockpath,O_CREAT|O_RDWR|O_CLOEXEC,0600);if(lockfd<0||flock(lockfd,LOCK_EX|LOCK_NB)){fprintf(stderr,"AT owner already running\n");return 1;}
 signal(SIGPIPE,SIG_IGN);
#ifdef CELL_TEST
 listener=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);struct sockaddr_un addr={.sun_family=AF_UNIX};if(listener<0||strlen(sockpath)>=sizeof addr.sun_path)return 1;strcpy(addr.sun_path,sockpath);unlink(sockpath);if(bind(listener,(struct sockaddr *)&addr,sizeof addr)||listen(listener,8)){perror("control socket");return 1;}
 signal(SIGTERM,on_signal);signal(SIGINT,on_signal);setphase("starting");next_cycle=now_ms();
 while(1){if(tick())break;if(poll_once())break;}
 clear_link();xclear_all();setphase("stopped");if(fd>=0)close(fd);close(listener);unlink(sockpath);close(lockfd);return 0;
#else
 uloop_init();
 if(!(ctx=ubus_connect(NULL))){fprintf(stderr,"u30pro-celld: cannot connect to ubus\n");return 1;}
 ubus_add_uloop(ctx);
 if(ubus_add_object(ctx,&object)){fprintf(stderr,"u30pro-celld: cannot register u30pro.cell\n");return 1;}
 setphase("starting");next_cycle=now_ms();
 serial_fd.cb=serial_event;serial_fd.fd=-1;
 struct uloop_timeout t={.cb=tick_timer};uloop_timeout_set(&t,0);
 uloop_run();
 /* SIGTERM (procd stops the service): take the links down and wait up to 3 s for the disconnect, then go */
 if(serial_fd.registered)uloop_fd_delete(&serial_fd);
 quitting=1;while(!tick()&&!poll_once());
 clear_link();xclear_all();setphase("stopped");if(fd>=0)close(fd);ubus_free(ctx);uloop_done();close(lockfd);return 0;
#endif
}
