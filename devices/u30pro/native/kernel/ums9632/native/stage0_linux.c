/* SPDX-License-Identifier: MIT
 * U30Pro early init. All block devices remain unmounted. No slot/AVB writes.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/fs.h>
#ifdef U30PRO_STAGE0_RESET_PROBE
#include <ctype.h>
#include <sys/reboot.h>
#include <time.h>
#endif

static void message(const char *fmt, ...) {
    char text[2048];
    va_list args;
    va_start(args, fmt);
    vsnprintf(text, sizeof(text), fmt, args);
    va_end(args);
    dprintf(2, "u30pro: %s\n", text);
    int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (fd >= 0) { dprintf(fd, "<6>u30pro: %s\n", text); close(fd); }
    fd = open("/run/u30pro-early.log", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd >= 0) { dprintf(fd, "%s\n", text); close(fd); }
}

static bool makedirs(const char *path) {
    char copy[PATH_MAX];
    if (strlen(path) >= sizeof(copy)) return false;
    strcpy(copy, path);
    for (char *p = copy + 1; *p; p++) {
        if (*p == '/') {
            *p = 0;
            if (mkdir(copy, 0755) && errno != EEXIST) return false;
            *p = '/';
        }
    }
    return mkdir(copy, 0755) == 0 || errno == EEXIST;
}

static bool mountfs(const char *type, const char *target, const char *options) {
    if (!makedirs(target)) { message("mkdir %s: %s", target, strerror(errno)); return false; }
    if (mount(type, target, type, MS_NOSUID, options) && errno != EBUSY) {
        message("mount %s: %s", target, strerror(errno)); return false;
    }
    return true;
}

static bool relative_name(const char *name) {
    if (!*name || *name == '/' || strstr(name, "..")) return false;
    for (const char *p = name; *p; p++)
        if (*p != '/' && *p != '_' && *p != '-' && *p != '.' &&
            !(*p >= '0' && *p <= '9') && !(*p >= 'A' && *p <= 'Z') && !(*p >= 'a' && *p <= 'z'))
            return false;
    return true;
}

/* Refresh partition aliases; devtmpfs owns live node creation on the native kernel. */
static void populate_nodes(const char *kind) {
    char path[PATH_MAX];
    snprintf(path, sizeof(path), "/sys/dev/%s", kind);
    DIR *dir = opendir(path);
    if (!dir) return;
    struct dirent *entry;
    while ((entry = readdir(dir))) {
        unsigned int maj = 0, min = 0;
        if (sscanf(entry->d_name, "%u:%u", &maj, &min) != 2) continue;
        snprintf(path, sizeof(path), "/sys/dev/%s/%s/uevent", kind, entry->d_name);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        char line[1024], devname[512] = "", partname[512] = "";
        while (fgets(line, sizeof(line), f)) {
            line[strcspn(line, "\r\n")] = 0;
            if (!strncmp(line, "DEVNAME=", 8)) snprintf(devname, sizeof(devname), "%s", line + 8);
            if (!strncmp(line, "PARTNAME=", 9)) snprintf(partname, sizeof(partname), "%s", line + 9);
        }
        fclose(f);
        if (!relative_name(devname)) continue;
        snprintf(path, sizeof(path), "/dev/%s", devname);
        char parent[PATH_MAX]; strcpy(parent, path);
        *strrchr(parent, '/') = 0; makedirs(parent);
        mode_t mode = !strcmp(kind, "block") ? S_IFBLK : S_IFCHR;
        if (mknod(path, mode | 0600, makedev(maj, min)) && errno != EEXIST)
            message("mknod %s: %s", devname, strerror(errno));
        if (!strcmp(kind, "block") && relative_name(partname) && !strchr(partname, '/')) {
            makedirs("/dev/block/by-name");
            char link[PATH_MAX]; snprintf(link, sizeof(link), "/dev/block/by-name/%s", partname);
            symlink(path, link);
        }
    }
    closedir(dir);
}

static void console(void) {
    const char *paths[] = {"/dev/ttyS1", "/dev/ttyAMA0", "/dev/console"};
    for (unsigned i = 0; i < sizeof(paths) / sizeof(paths[0]); i++) {
        int fd = open(paths[i], O_RDWR | O_NOCTTY);
        if (fd < 0) continue;
        for (int j = 0; j < 3; j++) dup2(fd, j);
        if (fd > 2) close(fd);
        return;
    }
}

#ifndef U30PRO_NATIVE_RELEASE
#define U30PRO_NATIVE_RELEASE "6.12.94-u30pro-p1"
#endif
static bool native_release(void) {
    struct utsname u;
    return uname(&u)==0 && !strcmp(u.release,U30PRO_NATIVE_RELEASE);
}
static int protect_storage(void) {
    DIR *d=opendir("/sys/class/block");
    if(!d)return -1;
    struct dirent *e;unsigned seen=0,verified=0,errors=0;
    while((e=readdir(d))) {
        if(strncmp(e->d_name,"mmcblk",6)||!relative_name(e->d_name)||strchr(e->d_name,'/'))continue;
        char path[PATH_MAX];struct stat st;int ro=1,got=0;
        snprintf(path,sizeof path,"/dev/%s",e->d_name);
        int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
        seen++;
        if(fd<0){errors++;continue;}
        if(!fstat(fd,&st)&&S_ISBLK(st.st_mode)&&
           !ioctl(fd,BLKROSET,&ro)&&!ioctl(fd,BLKROGET,&got)&&got==1)verified++;
        else {errors++;message("read-only protection failed for %s: %s",e->d_name,strerror(errno));}
        close(fd);
    }
    closedir(d);
    FILE *f=fopen("/run/u30pro-native-storage.json","w");
    if(f){fprintf(f,"{\"mmc_block_nodes\":%u,\"read_only_verified\":%u,\"errors\":%u}\n",seen,verified,errors);fclose(f);}
    message("native storage: exposed=%u readonly=%u errors=%u (no block mounts)",seen,verified,errors);
    return errors?-1:0;
}
static void halt_trial(const char *why) {
    message("native trial halted: %s; watchdog fallback remains armed",why);
    for(;;)pause();
}
#ifdef U30PRO_STAGE0_RESET_PROBE
static void probe_text(const char *path,char *text,size_t size,bool empty_ok) {
    FILE *f=fopen(path,"r");
    if(!f)halt_trial("USB probe text open failed");
    text[0]=0;
    if(!fgets(text,size,f)&&(!empty_ok||ferror(f)))halt_trial("USB probe text read failed");
    if(!feof(f)&&strlen(text)==size-1)halt_trial("USB probe text too long");
    fclose(f);
    size_t n=strlen(text);while(n&&isspace((unsigned char)text[n-1]))text[--n]=0;
}
static unsigned usb_probe_state(void) {
    DIR *d=opendir("/sys/class/udc");
    if(!d)halt_trial("USB probe class unreadable");
    struct dirent *e;unsigned count=0;char name[256]={0};
    errno=0;
    while((e=readdir(d))) {
        if(!strcmp(e->d_name,".")||!strcmp(e->d_name,".."))continue;
        if(!relative_name(e->d_name)||strchr(e->d_name,'/'))halt_trial("USB probe invalid UDC name");
        if(++count>1)halt_trial("USB probe multiple UDCs");
        snprintf(name,sizeof(name),"%s",e->d_name);
    }
    if(errno)halt_trial("USB probe class iteration failed");
    closedir(d);
    if(!count)return 0;
    struct stat st;
    if(stat("/sys/kernel/config/usb_gadget/u30pro",&st)) {
        if(errno==ENOENT)return 1;
        halt_trial("USB probe gadget stat failed");
    }
    if(!S_ISDIR(st.st_mode))halt_trial("USB probe gadget not directory");
    char binding[256],state[64],path[PATH_MAX];
    probe_text("/sys/kernel/config/usb_gadget/u30pro/UDC",binding,sizeof(binding),true);
    if(!binding[0])return 2;
    if(strcmp(binding,name))halt_trial("USB probe unexpected UDC binding");
    snprintf(path,sizeof(path),"/sys/class/udc/%s/state",name);
    probe_text(path,state,sizeof(state),false);
    return !strcmp(state,"configured")?4:3;
}
static unsigned usb_bus_probe_state(unsigned usb) {
    const char *dev="/sys/bus/platform/devices/64a00000.usb";
    struct stat st;char path[PATH_MAX],waiting[16];
    if(stat(dev,&st)) {
        if(errno==ENOENT)return 0;
        halt_trial("USB bus probe device stat failed");
    }
    if(!S_ISDIR(st.st_mode))halt_trial("USB bus probe device not directory");
    snprintf(path,sizeof(path),"%s/driver",dev);
    if(!lstat(path,&st)) {
        if(!S_ISLNK(st.st_mode))halt_trial("USB bus probe invalid driver link");
        return usb?4:3;
    }
    if(errno!=ENOENT)halt_trial("USB bus probe driver stat failed");
    snprintf(path,sizeof(path),"%s/waiting_for_supplier",dev);
    probe_text(path,waiting,sizeof(waiting),false);
    if(strcmp(waiting,"0")&&strcmp(waiting,"1"))halt_trial("USB bus probe invalid supplier flag");
    unsigned code=!strcmp(waiting,"1")?1:2;
    if(!mountfs("debugfs","/sys/kernel/debug",""))halt_trial("USB bus probe debugfs mount");
    FILE *f=fopen("/sys/kernel/debug/devices_deferred","r");
    if(!f)halt_trial("USB bus probe deferred list unreadable");
    char line[2048];unsigned found=0;
    while(fgets(line,sizeof(line),f)) {
        if(!strchr(line,'\n')&&!feof(f))halt_trial("USB bus probe deferred line too long");
        if(strncmp(line,"64a00000.usb\t",13))continue;
        if(++found>1)halt_trial("USB bus probe duplicate deferred entry");
        message("USB deferred reason: %.1800s",line);
        if(strstr(line,"222a0000.i2c"))code=5;
        else if(strstr(line,"charger")||strstr(line,"006b"))code=6;
        else if(strstr(line,"usb-phy")||strstr(line,"usb2-phy"))code=7;
        else if(strstr(line,"typec"))code=8;
        else if(strstr(line,"clock")||strstr(line,"clk"))code=9;
    }
    if(ferror(f))halt_trial("USB bus probe deferred read failed");
    fclose(f);
    return code;
}
/* Read real device objects only: no I2C transfers, bind or register writes.
 * Flags: 1 platform device, 2 driver link, 4 adapter6, 8 DT charger client.
 * Existing adapter/client nodes must belong to the expected live DT node.
 */
static bool probe_directory(const char *path) {
    struct stat st;
    if(stat(path,&st)) {
        if(errno==ENOENT)return false;
        halt_trial("I2C probe directory stat failed");
    }
    if(!S_ISDIR(st.st_mode))halt_trial("I2C probe expected directory");
    return true;
}
static void probe_same_node(const char *actual,const char *expected) {
    struct stat a,b;
    if(stat(actual,&a)||stat(expected,&b)||!S_ISDIR(a.st_mode)||
       !S_ISDIR(b.st_mode)||a.st_dev!=b.st_dev||a.st_ino!=b.st_ino)
        halt_trial("I2C probe unexpected DT provenance");
}
static unsigned i2c_probe_state(void) {
    const char *dev="/sys/bus/platform/devices/222a0000.i2c";
    struct stat st;char path[PATH_MAX];unsigned flags=0;
    if(probe_directory(dev)) {
        flags|=1;snprintf(path,sizeof(path),"%s/driver",dev);
        if(!lstat(path,&st)) {
            if(!S_ISLNK(st.st_mode))halt_trial("I2C probe invalid driver link");
            flags|=2;
        } else if(errno!=ENOENT)halt_trial("I2C probe driver stat failed");
    }
    if(probe_directory("/sys/bus/i2c/devices/i2c-6")) {
        probe_same_node("/sys/bus/i2c/devices/i2c-6/of_node",
                        "/sys/bus/platform/devices/222a0000.i2c/of_node");
        flags|=4;
    }
    if(probe_directory("/sys/bus/i2c/devices/6-006b")) {
        probe_same_node("/sys/bus/i2c/devices/6-006b/of_node",
                        "/sys/bus/platform/devices/222a0000.i2c/of_node/charger@6b");
        flags|=8;
    }
    return flags;
}
static bool probe_driver_link(const char *path) {
    struct stat st;
    if(lstat(path,&st)) {
        if(errno==ENOENT)return false;
        halt_trial("device probe driver link stat");
    }
    if(!S_ISLNK(st.st_mode)||stat(path,&st)||!S_ISDIR(st.st_mode))
        halt_trial("device probe invalid driver link");
    return true;
}
static unsigned usb_link_probe_state(unsigned usb,unsigned i2c) {
    const char *dev="/sys/bus/platform/devices/64a00000.usb";
    const char *link="/sys/bus/platform/devices/64a00000.usb/supplier:i2c:6-006b";
    char sync[8],status[64],waiting[8];
    if(!probe_directory(dev))return 0;
    if(i2c!=15)return 9;
    if(probe_driver_link("/sys/bus/platform/devices/64a00000.usb/driver"))
        return usb?2:1;
    probe_text("/sys/bus/platform/devices/64a00000.usb/waiting_for_supplier",waiting,sizeof(waiting),false);
    if(strcmp(waiting,"0")&&strcmp(waiting,"1"))halt_trial("USB link invalid supplier flag");
    bool linked=probe_directory(link);
    if(linked) {
        probe_same_node("/sys/bus/platform/devices/64a00000.usb/supplier:i2c:6-006b/supplier",
                        "/sys/bus/i2c/devices/6-006b");
        probe_same_node("/sys/bus/platform/devices/64a00000.usb/supplier:i2c:6-006b/consumer",dev);
        probe_text("/sys/bus/platform/devices/64a00000.usb/supplier:i2c:6-006b/sync_state_only",sync,sizeof(sync),false);
        probe_text("/sys/bus/platform/devices/64a00000.usb/supplier:i2c:6-006b/status",status,sizeof(status),false);
        if(strcmp(sync,"0")&&strcmp(sync,"1"))halt_trial("USB link invalid sync-only flag");
        if(strcmp(status,"not tracked")&&strcmp(status,"dormant")&&strcmp(status,"available")&&
           strcmp(status,"consumer probing")&&strcmp(status,"active")&&strcmp(status,"supplier unbinding"))
            halt_trial("USB link invalid status");
        message("USB charger device link: waiting=%s sync_only=%s status=%s",waiting,sync,status);
    }
    if(!strcmp(waiting,"1"))return linked?4:3;
    if(!linked)return 5;
    if(!strcmp(sync,"1"))return 8;
    return !strcmp(status,"dormant")?6:7;
}
static bool extcon_usb_probe(const char *dev) {
    char path[PATH_MAX],state[1024];snprintf(path,sizeof(path),"%s/extcon",dev);
    DIR *d=opendir(path);unsigned found=0;bool usb=false;
    if(!d) {
        if(errno==ENOENT)return false;
        halt_trial("extcon probe opendir");
    }
    for(;;) {
        errno=0;struct dirent *e=readdir(d);
        if(!e) { if(errno)halt_trial("extcon probe readdir");break; }
        if(strncmp(e->d_name,"extcon",6)||!e->d_name[6])continue;
        bool valid=true;for(char *p=e->d_name+6;*p;p++)if(!isdigit((unsigned char)*p))valid=false;
        if(!valid)continue;
        if(++found>1)halt_trial("extcon probe multiple children");
        snprintf(path,sizeof(path),"%s/extcon/%s/state",dev,e->d_name);
        probe_text(path,state,sizeof(state),false);
        unsigned lines=0;char *save=NULL;
        for(char *p=strtok_r(state,"\n",&save);p;p=strtok_r(NULL,"\n",&save)) {
            if(strncmp(p,"USB=",4))continue;
            if(++lines>1||(strcmp(p,"USB=0")&&strcmp(p,"USB=1")))halt_trial("extcon probe invalid USB state");
            usb=!strcmp(p,"USB=1");
        }
        if(lines!=1)halt_trial("extcon probe missing USB capability");
    }
    closedir(d);return usb;
}
static unsigned usb_phy_extcon_probe_state(void) {
    unsigned flags=0;
    if(probe_driver_link("/sys/bus/platform/devices/64300000.hsphy/driver"))flags|=1;
    if(probe_driver_link("/sys/bus/platform/devices/64600000.spi:pmic@1:typec@380/driver"))flags|=2;
    if(probe_driver_link("/sys/bus/platform/devices/extcon-gpio/driver"))flags|=4;
    if(extcon_usb_probe("/sys/bus/platform/devices/extcon-gpio"))flags|=8;
    if(extcon_usb_probe("/sys/bus/platform/devices/64600000.spi:pmic@1:typec@380"))flags|=16;
    return flags;
}
/* The class path is only accepted when its DT node is the expected provider. */
static unsigned shadow_nvmem_probe_state(void) {
    unsigned flags=0;
    if(probe_driver_link("/sys/bus/platform/devices/65007800.efuse/driver"))flags|=1;
    if(probe_directory("/sys/bus/nvmem/devices/sprd-cache-efuse0")) {
        probe_same_node("/sys/bus/nvmem/devices/sprd-cache-efuse0/of_node",
                        "/sys/bus/platform/devices/65007800.efuse/of_node");
        flags|=2;
    }
    if(probe_driver_link("/sys/bus/platform/devices/65007a80.efuse/driver"))flags|=4;
    if(probe_directory("/sys/bus/nvmem/devices/ump9651-cache-efuse")) {
        probe_same_node("/sys/bus/nvmem/devices/ump9651-cache-efuse/of_node",
                        "/sys/bus/platform/devices/65007a80.efuse/of_node");
        flags|=8;
    }
    return flags;
}
static unsigned typec_deferred_probe_state(void) {
    const char *dev="/sys/bus/platform/devices/64600000.spi:pmic@1:typec@380";
    const char *prefix="64600000.spi:pmic@1:typec@380\t";
    char waiting[8],line[4096];unsigned found=0,code;
    if(!probe_directory(dev))return 0;
    if(probe_driver_link("/sys/bus/platform/devices/64600000.spi:pmic@1:typec@380/driver"))return 1;
    probe_text("/sys/bus/platform/devices/64600000.spi:pmic@1:typec@380/waiting_for_supplier",waiting,sizeof(waiting),false);
    if(strcmp(waiting,"0")&&strcmp(waiting,"1"))halt_trial("TypeC invalid supplier flag");
    code=!strcmp(waiting,"1")?3:2;
    if(!mountfs("debugfs","/sys/kernel/debug",""))halt_trial("TypeC debugfs mount");
    FILE *f=fopen("/sys/kernel/debug/devices_deferred","r");
    if(!f)halt_trial("TypeC deferred list open");
    while(fgets(line,sizeof(line),f)) {
        if(!strchr(line,'\n')&&!feof(f))halt_trial("TypeC deferred line too long");
        if(strncmp(line,prefix,strlen(prefix)))continue;
        if(++found>1)halt_trial("TypeC duplicate deferred entry");
        const char *reason=line+strlen(prefix);
        message("TypeC deferred reason: %.3800s",reason);
        if(strstr(reason,"efuse")||strstr(reason,"calib"))code=4;
        else if(strstr(reason,"extcon")||strstr(reason,"gpio"))code=5;
        else if(strstr(reason,"irq")||strstr(reason,"interrupt"))code=6;
        else if(strstr(reason,"nvmem"))code=7;
        else if(strstr(reason,"regulator")||strstr(reason,"power-controller")||strstr(reason,"LDO_"))code=8;
        else if(strstr(reason,"typec"))code=9;
    }
    if(ferror(f))halt_trial("TypeC deferred read error");
    fclose(f);return code;
}
/* Diagnostic builds only. Configuration lives in the immutable initramfs;
 * no storage, watchdog, or slot writes. A positive standard reboot is the
 * evidence, never an inference from absent RAM logs.
 */
static void reset_probe(unsigned point) {
    int fd=open("/etc/u30pro/pid1-reset-probe",O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    struct stat st;char buf[33]={0},extra;unsigned stage=0,delay=0;
    if(fd<0)halt_trial("missing PID1 probe configuration");
    if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size<=0||st.st_size>32)
        halt_trial("invalid PID1 probe configuration type/size");
    ssize_t n=read(fd,buf,sizeof(buf)-1);close(fd);
    if(n!=st.st_size||sscanf(buf,"%u %u %c",&stage,&delay,&extra)!=2||
       stage<1||stage>12||delay<100||delay>5000)
        halt_trial("invalid PID1 probe parameters");
    if(stage!=point&&!(stage>=5&&point==4))return;
    if(geteuid()||!native_release()||(point<=3&&getpid()!=1)||(point==4&&getpid()==1))
        halt_trial("PID1 probe identity/release");
    if(point==4) {
        char comm[32]={0};FILE *f=fopen("/proc/1/comm","r");
        if(!f||!fgets(comm,sizeof(comm),f))halt_trial("rcS probe PID1 unreadable");
        fclose(f);
        if(strcmp(comm,"procd\n"))halt_trial("rcS probe requires procd PID1");
        message("rcS reset probe: PID1=procd; helper_pid=%d",getpid());
        if(stage>=6) {
            message("USB diagnostic settling wait: 10000ms");
            struct timespec settle={.tv_sec=10,.tv_nsec=0};
            while(nanosleep(&settle,&settle))if(errno!=EINTR)halt_trial("USB settling nanosleep");
        }
        unsigned usb=usb_probe_state();
        unsigned bus=usb_bus_probe_state(usb);
        message("USB reset report: state=%u mode=%u",usb,stage);
        message("USB bus report: state=%u mode=%u",bus,stage);
        unsigned i2c=i2c_probe_state();
        message("I2C object report: flags=%u mode=%u",i2c,stage);
        unsigned link=usb_link_probe_state(usb,i2c);
        unsigned phy=usb_phy_extcon_probe_state();
        message("USB link report: state=%u mode=%u",link,stage);
        message("USB PHY extcon report: flags=%u mode=%u",phy,stage);
        unsigned typec=typec_deferred_probe_state();
        unsigned nvmem=shadow_nvmem_probe_state();
        message("TypeC deferred report: state=%u mode=%u",typec,stage);
        message("Shadow NVMEM report: flags=%u mode=%u",nvmem,stage);
        if(stage>=5) {
            if(delay!=100)halt_trial("USB probe report requires base delay 100ms");
            unsigned report=stage==12?nvmem:(stage==11?typec:(stage==10?phy:(stage==9?link:(stage==8?i2c:(stage==7?bus:usb)))));
            delay+=1000*(report+1);
        }
    }
    message("PID1 reset probe: stage=%u delay_ms=%u",stage,delay);
    struct timespec wait={.tv_sec=delay/1000,.tv_nsec=(delay%1000)*1000000L};
    while(nanosleep(&wait,&wait))if(errno!=EINTR)halt_trial("PID1 probe nanosleep");
    reboot(RB_AUTOBOOT);
    halt_trial("PID1 probe reboot returned");
}
#else
static inline void reset_probe(unsigned point) { (void)point; }
#endif
int main(int argc,char **argv) {
    /* Linux may append unhandled bare boot parameters to init's argv.
     * They are data for PID1, never helper commands or procd arguments.
     */
    if(getpid()!=1) {
#ifdef U30PRO_STAGE0_RESET_PROBE
        if(argc==2 && !strcmp(argv[1],"--reset-probe-rcS")) {
            if(geteuid()||!native_release())return 2;
            reset_probe(4);return 0;
        }
#endif
        if(argc==2 && !strcmp(argv[1],"--populate-nodes")) {
            if(geteuid()||!native_release())return 2;
            populate_nodes("char");populate_nodes("block");return 0;
        }
        if(argc==2 && !strcmp(argv[1],"--protect-storage")) {
            if(geteuid()||!native_release())return 2;
            return protect_storage()?1:0;
        }
        fputs("native stage0: PID1 or exact guarded helper command required\n",stderr);
        return 2;
    }
    reset_probe(1);
    setenv("PATH","/usr/sbin:/usr/bin:/sbin:/bin",1);setenv("HOME","/root",1);setenv("TERM","linux",0);umask(022);
    if(!mountfs("proc","/proc","")||!mountfs("sysfs","/sys","")||
       !mountfs("devtmpfs","/dev","mode=0755")||!mountfs("tmpfs","/run","mode=0755,size=16m")||
       !mountfs("tmpfs","/tmp","mode=1777,size=256m"))halt_trial("required filesystem");
    mknod("/dev/console",S_IFCHR|0600,makedev(5,1));mknod("/dev/null",S_IFCHR|0666,makedev(1,3));
    makedirs("/dev/pts");if(!mountfs("devpts","/dev/pts","mode=0620,ptmxmode=0666"))halt_trial("devpts");
    unlink("/dev/ptmx");symlink("pts/ptmx","/dev/ptmx");
    populate_nodes("char");populate_nodes("block");console();
    if(!native_release())halt_trial("kernel release mismatch");
    for(int i=1;i<argc;i++)message("PID1 kernel argument ignored: %.512s",argv[i]);
    message("native stage0 %s; built-in drivers, no Android module autoload",U30PRO_NATIVE_RELEASE);
    reset_probe(2);
    if(protect_storage())halt_trial("storage read-only check");
    reset_probe(3);
    makedirs("/var/run");makedirs("/var/lock");
    execl("/sbin/procd","/sbin/procd","-S",(char*)NULL);
    halt_trial("exec procd failed");
}
