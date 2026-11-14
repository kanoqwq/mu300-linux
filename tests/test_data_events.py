"""Event-driven wakeups must preserve local watchdog recovery decisions."""
import json
import os
import signal
import subprocess
import time
import unittest
from pathlib import Path
from helpers import TOP, ShellTest, ucode
from test_modemd import Base, has_modules, wait_for, UBUS, UBUSD
import test_cellular_mobility as original

LIB=TOP/'rootfs/overlay/opt/mu300/lib/data-events.sh'

class DataEventFilter(ShellTest):
    def test_only_unsolicited_channel_can_trigger_a_modem_refresh(self):
        for shell in self.each_shell():
            for slot in [0,1]:
                for nr in range(8):
                    for line in ['+CGEV: NW PDN DEACT 1','+CEREG: 1,"123",7','NO CARRIER','+CMTI: "SM",2']:
                        event=json.dumps({'mu300.modem.urc':{'channel':f'stty_nr{nr}','line':line}},separators=(',',':'))
                        r=self.sh(shell,'. '+str(LIB)+'\nwatch_event_decode "$EVENT"; printf "%s" "$watch_event"',MU300_SIM_SLOT=slot,EVENT=event)
                        self.assertEqual(r.returncode,0,r.stderr)
                        self.assertEqual(r.stdout,'modem' if nr==0 and not line.startswith('+CMTI') else '')

    def test_only_selected_wan_but_sim_transaction_wakes_to_reresolve_slot(self):
        for shell in self.each_shell():
            for event,want in [({'network.interface':{'interface':'wan2'}},'network'),({'network.interface':{'interface':'wan'}},''),({'mu300.sim-state':{'slot':1}},'sim'),({'ubus.object.remove':{'path':'mu300.modem'}},'gone')]:
                r=self.sh(shell,'. '+str(LIB)+'\nwatch_event_decode "$EVENT"; printf "%s" "$watch_event"',MU300_WAN='wan2',EVENT=json.dumps(event,separators=(',',':')))
                self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout,want)

@unittest.skipUnless(ucode() and has_modules(ucode()) and os.access(UBUS,os.X_OK) and os.access(UBUSD,os.X_OK),'real ucode/ubus required')
class DataEventBus(Base):
    def setUp(self):
        super().setUp()
        self.sock=self.tmp/'bus';self.busd=subprocess.Popen([UBUSD,'-s',str(self.sock)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertTrue(wait_for(self.sock.exists));self.ubus_sock=self.sock
        self.modem=self.start();self.ready()
        self.assertTrue(wait_for(lambda:self.bus_call('list','mu300.modem').returncode==0))
        stubs=self.tmp/'stubs';stubs.mkdir()
        stub=stubs/'ubus';stub.write_text('#!/bin/sh\nexec '+UBUS+' -s "$MU300_UBUS_SOCKET" "$@"\n');stub.chmod(0o755)
        self.event_env=dict(self.env,PATH=str(stubs)+':'+os.environ['PATH'],MU300_UBUS_SOCKET=str(self.sock))
        self.result=self.tmp/'result';self.started=self.tmp/'started'

    def tearDown(self):
        super().tearDown();self.busd.terminate();self.busd.wait(5)

    def bus_call(self,*args):
        return subprocess.run([UBUS,'-s',str(self.sock),*args],capture_output=True,text=True,timeout=5)

    def receiver(self,interval=30,fails=0):
        # Bash is required by mobile-data. OpenWrt's receiver helper is portable
        # too, so this test uses its actual ash with the same errexit/nounset.
        code='set -eu\n. '+str(LIB)+'''\ntrap watch_events_stop EXIT
trap 'exit 0' TERM INT
watch_events_start || exit 9
printf '%s' "$watch_event_pid" > "$STARTED"
watch_clock; printf '%s' "$WATCH_NOW" > "$RESULT.start"
watch_next
printf '%s' "$WATCH_NOW" > "$RESULT.end"
printf '%s|%s' "$watch_event" "$watch_events" > "$RESULT"
'''
        env=dict(self.event_env,MU300_WATCH_INTERVAL=str(interval),fails=str(fails),STARTED=str(self.started),RESULT=str(self.result))
        p=subprocess.Popen(['/bin/sh','-c',code],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);self.procs.append(p)
        self.assertTrue(wait_for(self.started.exists));time.sleep(.1)
        return p

    def send(self,name,data):
        r=self.bus_call('send',name,json.dumps(data));self.assertEqual(r.returncode,0,r.stderr)

    def finish(self,p,want):
        self.assertEqual(p.wait(5),0,p.stderr.read());self.assertEqual(self.result.read_text(),want)

    def test_wan_and_modem_events_wake_without_waiting_for_safety_poll(self):
        p=self.receiver();self.send('mu300.modem.urc',{'channel':'stty_nr0','line':'+CGEV: NW PDN DEACT 1'})
        self.finish(p,'modem|1')

    def test_other_slot_and_solicited_replies_do_not_trigger_refresh(self):
        p=self.receiver()
        for nr in [1,2,3,4,5,6,7]:self.send('mu300.modem.urc',{'channel':f'stty_nr{nr}','line':'+CEREG: 1'})
        self.send('network.interface',{'interface':'other'})
        time.sleep(.2);self.assertIsNone(p.poll())
        self.send('network.interface',{'interface':'wan'});self.finish(p,'network|1')

    def test_unrelated_event_stream_does_not_extend_original_safety_deadline(self):
        p=self.receiver(interval=2)
        for _ in range(6):self.send('network.interface',{'interface':'other'});time.sleep(.2)
        self.finish(p,'|1')
        # Measure inside the receiver; CLI/process startup under QEMU is not
        # part of the event wait and must not be charged to its deadline.
        elapsed=int(Path(str(self.result)+'.end').read_text())-int(Path(str(self.result)+'.start').read_text())
        self.assertIn(elapsed,(2,3))

    def test_detach_confirmation_interval_is_not_shortened_by_burst(self):
        start=time.monotonic();p=self.receiver(interval=2,fails=1)
        self.send('mu300.modem.urc',{'channel':'stty_nr0','line':'NO CARRIER'})
        time.sleep(.2);self.assertIsNone(p.poll())
        self.finish(p,'modem|1');self.assertGreater(time.monotonic()-start,.7)

    def test_listener_death_stops_with_an_error(self):
        p=self.receiver();pid=int(self.started.read_text());os.kill(pid,signal.SIGTERM)
        self.assertNotEqual(p.wait(5),0,p.stderr.read())

    def test_native_daemon_removal_stops_with_an_error(self):
        p=self.receiver();self.modem.terminate();self.modem.wait(5);self.assertNotEqual(p.wait(5),0,p.stderr.read())

    def whole_watchdog(self,down=False):
        root=self.tmp/'watch-run';(root/'mu300').mkdir(parents=True)
        if down:(root/'mu300-mobile-data-down').touch()
        source=original.MOBILE
        functions=source[source.index('watch_context_state() {'):source.index('\n# Radio quality')]
        functions=functions.replace('/run/',str(root)+'/').replace('watch_events_start || return 1;','watch_events_start || return 1; printf "%s" "$watch_event_pid" > "$STARTED";')
        script='set -euo pipefail\n'+original.PDP_SOURCE+functions+'''
CID=1; DEV=/dev/null; IFACE=sipa_eth0; DEVICE=f50; MU300_NETIFD=1; MU300_SIM_SLOT=0
modem_stack() { return 0; }
tty_setup() { :; }
at_owner_alive() { return 1; }
mu300_sim_env() { MU300_SIM_SLOT=1; MU300_SIM_DEVICE=sipa_eth8; }
led_up() { :; }
radio_on() { echo forbidden-radio >> "$CALLS"; }
at() {
    printf 'slot=%s %s\n' "$MU300_SIM_SLOT" "$1" >> "$CALLS"
    case "$1" in *CGACT*) echo '+CGACT: 1,1';; *CGCONTRDP*) echo '+CGCONTRDP: 1,6,internet,10.0.0.2.255.0.0.0,,1.1.1.1';; esac
}
watch
'''
        stubs=self.tmp/'stubs'
        for name,body in [('ip',"echo '1: sipa_eth8 inet 10.0.0.1/8 scope global sipa_eth8'"),('jsonfilter','echo false')]:
            p=stubs/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
        # Keep the real ubus transport. Add only a harmless fake netifd method
        # response so the complete watchdog can exercise in-place renew.
        bus=stubs/'ubus';bus.write_text('#!/bin/sh\ncase "$*" in\n "call network.interface.wan status") echo "{}";;\n "call network.interface.wan renew") echo renew >> "$CALLS";;\n *) exec '+UBUS+' -s "$MU300_UBUS_SOCKET" "$@";;\nesac\n');bus.chmod(0o755)
        calls=self.tmp/'calls';calls.touch()
        env=dict(self.event_env,MU300_DATA_EVENTS_LIB=str(LIB),MU300_WATCH_INTERVAL='1',STARTED=str(self.started),CALLS=str(calls))
        p=subprocess.Popen(['/bin/bash','-c',script],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);self.procs.append(p)
        self.assertTrue(wait_for(self.started.exists));time.sleep(.15)
        return p,calls

    @unittest.skipUnless(os.path.exists('/bin/bash'),'mobile-data requires bash')
    def test_whole_watchdog_reresolves_active_sim_and_renews_without_redial(self):
        p,calls=self.whole_watchdog()
        self.send('network.interface',{'interface':'wan'})
        self.assertTrue(wait_for(lambda:'renew' in calls.read_text(),timeout=3),calls.read_text())
        self.assertIn('slot=1 AT+CGACT?',calls.read_text())
        self.assertNotIn('slot=0',calls.read_text());self.assertNotIn('forbidden-radio',calls.read_text())
        pid=int(self.started.read_text());p.terminate();self.assertEqual(p.wait(5),143)
        self.assertFalse(os.path.exists('/proc/'+str(pid)))

    @unittest.skipUnless(os.path.exists('/bin/bash'),'mobile-data requires bash')
    def test_whole_watchdog_respects_explicit_user_data_off(self):
        p,calls=self.whole_watchdog(down=True)
        self.send('network.interface',{'interface':'wan'})
        time.sleep(1.2)
        status=p.poll()
        self.assertIsNone(status,p.stderr.read() if status is not None else '')
        self.assertEqual(calls.read_text(),'')

    @unittest.skipUnless(os.path.exists('/bin/bash'),'mobile-data requires bash')
    def test_cleanup_does_not_remove_a_replaced_lock(self):
        p,calls=self.whole_watchdog(down=True)
        pidfile=self.tmp/'watch-run/mu300-mobile-data-watch.lock/pid'
        pidfile.write_text('999999\n')
        p.terminate();self.assertEqual(p.wait(5),143)
        self.assertEqual(pidfile.read_text(),'999999\n')
