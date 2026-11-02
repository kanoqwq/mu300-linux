"""Native screen power menu and physical-key input tests, without hardware."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
NATIVE = TOP / 'devices/u30pro/native/tools'


@unittest.skipUnless(os.name == 'posix', 'host C compiler and POSIX input pipe')
class PowerMenu(unittest.TestCase):
    def compile_run(self, source):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = source.replace('TEST_RUN', str(base))
            (base/'test.c').write_text(source)
            compiled = subprocess.run(['gcc', '-std=gnu11', '-O1', '-Wno-unused-function',
                '-I', str(NATIVE), str(base/'test.c'), '-lm', '-o', str(base/'test')],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(base/'test')], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if os.environ.get('U30PRO_SCREEN_ARTIFACTS'):
                destination = Path(os.environ['U30PRO_SCREEN_ARTIFACTS'])
                destination.mkdir(parents=True, exist_ok=True)
                for shot in base.glob('*.ppm'):
                    shutil.copyfile(shot, destination/shot.name)
            return result.stdout

    def test_real_ui_locked_dark_touch_cancel_confirmation_and_short_power(self):
        source = r'''#define UI_HOST
#define UI_RUN "TEST_RUN"
#define main simulator_main
#include "u30pro_ui.c"
#undef main
#include <assert.h>
static void reset(void) {
 power_menu=power_choice=asleep=0;notice[0]=0;
 locked=1;lock_soft=0;pin_mode=PIN_LOCKSCR;
 g_ms=vclock=10000;last_act=g_ms;D.feed_ok=1;
 memset(&tc,0,sizeof tc);press_kill();
}
static void tap(int x,int y) {
 compose();ui_touch(T_DOWN,x,y,now_ms());vclock+=100;
 ui_touch(T_UP,x,y,now_ms());
}
int main(void) {
 ui_init_canvases();reset();
 ui_key(116,100);assert(asleep && !power_menu);
 ui_key(116,100);assert(!asleep && !power_menu);
 ui_key(116,POWER_HOLD_MS);assert(power_menu && locked && pin_mode==PIN_LOCKSCR);
 tap(120,94);assert(power_choice==1 && locked);
 tap(120,178);assert(power_menu && !power_choice && locked);
 tap(120,196);assert(!power_menu && locked && pin_mode==PIN_LOCKSCR);
 reset();asleep=1;ui_key(116,POWER_HOLD_MS);
 assert(!asleep && power_menu && locked);
 ui_key(102,100);assert(power_menu && locked);
 ui_key(102,5000);assert(power_menu && locked);
 ui_key(116,100);assert(!power_menu && !asleep && locked);
 reset();power_menu_open();compose();
 ui_touch(T_DOWN,120,94,now_ms());vclock+=100;
 ui_touch(T_MOVE,155,150,now_ms());ui_touch(T_UP,155,150,now_ms());
 assert(power_menu && !power_choice && locked);
 do_action(H_POWER_CONFIRM);assert(!notice[0]);
 do_action(H_POWER_OFF);do_action(H_POWER_CONFIRM);
 assert(!power_menu && !power_choice && notice[0] && locked);
 reset();do_action(H_REBOOT);assert(power_menu);
 do_action(H_POWER_RESTART);assert(power_choice==2);
 do_action(H_POWER_CONFIRM);assert(!power_menu && notice[0]);
 reset();snprintf(notice,sizeof notice,"firmware update");
 ui_key(116,POWER_HOLD_MS);assert(!power_menu && locked);
 reset();power_menu_open();compose();
 assert(gx_has_all(FB22,"电源") && gx_has_all(FN16,"确认关闭设备？确认重启设备？关机重启返回取消"));
 // Every interactive choice fits inside the display's round safe window.
 for(int i=0;i<nhits;i++)assert(hits[i].x>=40 && hits[i].x+hits[i].w<=200 && hits[i].y>=60 && hits[i].y+hits[i].h<=216);
 shot_write("TEST_RUN","power-menu");
 do_action(H_POWER_OFF);compose();shot_write("TEST_RUN","power-confirm");
 reset();g_reg=1;bg_fill(&scr,0,0,0);page_control(&scr);g_reg=0;
 shot_write("TEST_RUN","control");
 return 0;
}
'''
        output = self.compile_run(source)
        self.assertEqual(output.count('RUN /usr/sbin/u30pro-panel-control poweroff'), 1)
        self.assertEqual(output.count('RUN /usr/sbin/u30pro-panel-control reboot'), 1)

    def test_real_gpio_key_loop_reports_hold_once_and_ignores_release(self):
        platform = (NATIVE/'ui/platform.h').read_text()
        body = platform[platform.index('static void keys_read(void)'):platform.index('/* ------------------------------------------------------------------ touch */')]
        source = r'''#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <linux/input.h>
#define POWER_HOLD_MS 1500
static int keyfd,tx,calls,code;
static long clock_ms=100,duration;
static long now_ms(void){return clock_ms;}
static void ui_key(int c,long d){calls++;code=c;duration=d;}
BODY
static void event(int type,int key,int value){
 struct input_event e={.type=type,.code=key,.value=value};
 assert(write(tx,&e,sizeof e)==sizeof e);keys_read();
}
int main(void){
 int pipefd[2];assert(!pipe2(pipefd,O_NONBLOCK));keyfd=pipefd[0];tx=pipefd[1];
 event(EV_KEY,KEY_POWER,1);clock_ms+=1499;keys_read();assert(!calls);
 clock_ms++;keys_read();assert(calls==1 && code==KEY_POWER && duration==1500);
 clock_ms+=1000;keys_read();event(EV_KEY,KEY_POWER,2);assert(calls==1);
 event(EV_KEY,KEY_POWER,0);assert(calls==1);
 event(EV_KEY,KEY_POWER,1);clock_ms+=100;event(EV_KEY,KEY_POWER,0);
 assert(calls==2 && duration==100);
 event(EV_KEY,KEY_POWER,1);clock_ms+=100;event(EV_SYN,SYN_DROPPED,0);
 clock_ms+=2000;keys_read();event(EV_KEY,KEY_POWER,0);assert(calls==2);
 event(EV_KEY,KEY_HOME,1);clock_ms+=2000;keys_read();assert(calls==2);
 event(EV_KEY,KEY_HOME,0);assert(calls==3 && code==KEY_HOME && duration==2000);
 return 0;
}
'''.replace('BODY', body)
        self.compile_run(source)

    def test_backend_power_actions_use_native_system_commands_only(self):
        source = (TOP/'devices/u30pro/overlay/usr/sbin/u30pro-panel-control').read_text()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ('poweroff', 'reboot'):
                program=base/name
                program.write_text('#!/bin/sh\nprintf "%s\\n" "'+name+'"\n')
                program.chmod(0o755)
            helper=base/'panel-control'
            helper.write_text(source.replace('exec poweroff', 'exec '+str(base/'poweroff'))
                             .replace('exec reboot', 'exec '+str(base/'reboot')))
            for name in ('poweroff','reboot'):
                result=subprocess.run(['sh',str(helper),name],text=True,capture_output=True,check=True)
                self.assertEqual(result.stdout.strip(),name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
