"""Native tests of the production charger event wait using pipes, never evdev."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name=='posix' and shutil.which('gcc'), 'native POSIX gcc required')
class ChargerKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='charge-keys-')
        cls.path=Path(cls.temp.name)
        cls.addClassCleanup(cls.temp.cleanup)
        source=cls.path/'test.c'
        source.write_text(r'''
#include <sys/socket.h>
static ssize_t charger_recvfrom(int,void *,size_t,int,struct sockaddr *,socklen_t *);
#define recvfrom charger_recvfrom
#define main keys_program
#include "mu300-keys.c"
#undef main
#undef recvfrom
#include <signal.h>
#include <sys/wait.h>
/* Supply sender metadata for local datagram fixtures; no real netlink events. */
static ssize_t charger_recvfrom(int fd,void *buf,size_t size,int flags,struct sockaddr *sender,socklen_t *length){
    ssize_t n=recvfrom(fd,buf,size,flags,NULL,NULL);
    if(n>0&&((char *)buf)[0]=='L'){errno=ENOBUFS;return -1;}
    if(n>0){struct sockaddr_nl *s=(struct sockaddr_nl *)sender;s->nl_family=AF_NETLINK;s->nl_pid=((char *)buf)[0]=='U'?123:0;*length=sizeof(*s);}
    return n;
}
static const char *status_path;
static void refresh(void){
    FILE *f=fopen(status_path,"r");char state[32];
    if(!f||!fgets(state,sizeof(state),f))_exit(9);
    fclose(f);fputs(state,stdout);fflush(stdout);
}
static void value(const char *path,const char *text){
    FILE *f=fopen(path,"w");if(!f)_exit(9);fputs(text,f);fclose(f);
}
static void key(int fd, int code, int value) {
    struct input_event e={.type=EV_KEY,.code=code,.value=value};
    if(write(fd,&e,sizeof(e))!=sizeof(e)) _exit(9);
}
int main(int argc, char **argv) {
    (void)argc;
    char event[]="change@/devices/test\0SUBSYSTEM=power_supply\0ONLINE=0\0";
    if(!charge_event(event,sizeof(event)) || charge_event("SUBSYSTEM=power_supply",22) ||
       charge_event("SUBSYSTEM=usb\0",14)) return 9;
    int p[2];if(pipe(p)) return 9;
    int supplies[2];if(socketpair(AF_UNIX,SOCK_DGRAM,0,supplies))return 9;
    int events=!strcmp(argv[2],"events")||!strcmp(argv[2],"lost");
    if(events)status_path=argv[3];
    pid_t child=fork();if(child<0) return 9;
    if(!child) {
        usleep(20000);
        if(events){
            char usb[]="K@/devices/test\0SUBSYSTEM=usb\0";
            char forged[]="U@/devices/test\0SUBSYSTEM=power_supply\0";
            char supply[]="K@/devices/test\0SUBSYSTEM=power_supply\0";
            value(argv[3],"Full\n");
            send(supplies[1],usb,sizeof(usb),0);usleep(30000);
            send(supplies[1],forged,sizeof(forged),0);usleep(30000);
            if(!strcmp(argv[2],"lost"))supply[0]='L';
            send(supplies[1],supply,sizeof(supply),0);usleep(30000);
            supply[0]='K';value(argv[3],"Discharging\n");value(argv[1],"0\n");
            send(supplies[1],supply,sizeof(supply),0);
        } else if(!strcmp(argv[2],"short")) {
            key(p[1],KEY_POWER,1);usleep(20000);key(p[1],KEY_POWER,0);
            usleep(140000);key(p[1],KEY_POWER,1);
        } else if(!strcmp(argv[2],"other")) {
            key(p[1],KEY_HELP,1);usleep(180000);key(p[1],KEY_POWER,1);
        } else if(!strcmp(argv[2],"power")) key(p[1],KEY_POWER,1);
        sleep(3);_exit(0);
    }
    struct pollfd fd={.fd=p[0],.events=POLLIN};
    int result=charge_wait(&fd,1,events?supplies[0]:-1,argv[1],80,events?refresh:NULL);
    kill(child,SIGTERM);waitpid(child,NULL,0);close(p[0]);close(p[1]);
    return result;
}
''')
        cls.binary=cls.path/'test'
        subprocess.run(['gcc','-Wall','-Wextra','-Werror','-O2','-I',str(TOP/'tools/keys'),
                        str(source),'-o',str(cls.binary)],check=True,capture_output=True)

    def wait(self,value,mode):
        path=self.path/'online';path.write_text(value)
        import time
        start=time.monotonic()
        r=subprocess.run([str(self.binary),str(path),mode],timeout=2)
        return r.returncode,time.monotonic()-start

    def test_long_power_starts(self):
        code,elapsed=self.wait('1\n','power')
        self.assertEqual(code,0);self.assertLess(elapsed,1)

    def test_short_power_and_wifi_key_do_not_start(self):
        for mode in ('short','other'):
            code,elapsed=self.wait('1\n',mode)
            self.assertEqual(code,0);self.assertGreater(elapsed,.23)

    def test_unplug_debounced_and_invalid_supply_fails_open(self):
        code,elapsed=self.wait('0\n','idle')
        self.assertEqual(code,3);self.assertGreaterEqual(elapsed,.49)
        self.assertEqual(self.wait('invalid\n','idle')[0],1)

    def test_full_supply_event_refreshes_without_polling_or_non_kernel_events(self):
        for mode in ('events', 'lost'):
            online=self.path/'online';online.write_text('1\n')
            status=self.path/'status';status.write_text('Charging\n')
            r=subprocess.run([str(self.binary),str(online),mode,str(status)],
                             capture_output=True,text=True,timeout=2)
            self.assertEqual(r.returncode,3,r.stderr)
            self.assertEqual(r.stdout.splitlines(),['Charging','Full','Discharging'])
