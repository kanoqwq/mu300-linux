"""Real shell/FIFO monitor lifetime with private IPv6 tables; no network writes."""
import os
import shlex
import signal
import subprocess
import time
import unittest
from helpers import TOP, ShellTest
from test_modemd import wait_for
from test_ipv6_prefix_routes import ROWS

LIB=TOP/'rootfs/overlay/opt/mu300/lib/ndp-events.sh'
SCRIPT=TOP/'openwrt/overlay/opt/mu300/bin/ndp-learn'

class NdpEvents(ShellTest):
    def setUp(self):
        super().setUp();self.procs=[]
        self.mapper=self.tmp/'mapper-default.sh';self.mapper.write_text('mu300_sim_env() { MU300_SIM_DEVICE=sipa_eth0; }\n')
        self.inet6=self.tmp/'inet6';self.inet6.write_text(ROWS['a'])
        self.routes=self.tmp/'routes';self.routes.write_text('')
        self.stub('uci','echo relay');self.stub('logger',':')
        self.stub('ra-deprecate','echo "ra $*" >> "$STUBLOG/calls"')
        self.stub('ip','''case "$*" in
    "-o -6 monitor label address route neigh")
        [ "${MONITOR_FAIL:-0}" = 0 ] || exit 1
        echo $$ > "$STUBLOG/monitor.pid"
        exec python3 "$STUBLOG/emitter.py" ;;
    *) echo "$*" >> "$STUBLOG/calls"
       case "$*" in *'route replace'*)
           if [ -f "$STUBLOG/during-replace" ]; then
               cp "$STUBLOG/during-replace" "$MU300_IF_INET6"
               rm "$STUBLOG/during-replace"
           fi ;;
       esac ;;
esac
exit 0''')
        (self.tmp/'emitter.py').write_text('''import os,sys,time
from pathlib import Path
root=Path(os.environ['STUBLOG']); offset=0
while True:
    lines=(root/'events').read_text().splitlines()
    for line in lines[offset:]: print(line,flush=True)
    offset=len(lines);time.sleep(.02)
''')
        (self.tmp/'events').touch();(self.tmp/'calls').touch()

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:p.terminate()
            p.wait(5)
            if p.stdout:p.stdout.close()
            if p.stderr:p.stderr.close()
        super().tearDown()

    def start(self,fail=0,**options):
        source=SCRIPT.read_text()
        self.daemon=self.tmp/'ndp-learn';self.daemon.write_text(source)
        defaults=dict(MU300_NDP_INTERFACE_LIB=LIB.with_name('ndp-interface.sh'),MU300_NDP_SIM_ENV=self.mapper,MU300_NDP_RUN=self.tmp/'run',MU300_NDP_CONFIG=self.tmp/'config',MU300_NDP_EVENTS_LIB=LIB,MU300_IF_INET6=self.inet6,MU300_IPV6_ROUTE=self.routes,MONITOR_FAIL=fail)
        defaults.update(options)
        env=self.env(**defaults)
        p=subprocess.Popen(shlex.split(os.environ.get('MU300_TEST_NDP_SHELL','bash'))+[str(self.daemon)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.procs.append(p)
        self.assertTrue(wait_for(lambda:'route replace' in (self.tmp/'calls').read_text()))
        return p

    def send(self,line):
        with (self.tmp/'events').open('a') as f:f.write(line+'\n')

    def count(self):return (self.tmp/'calls').read_text().count('route replace')

    def test_snapshot_ignores_traffic_counters_and_foreign_interfaces(self):
        route='20010db8000000000000000000000000 40 00000000000000000000000000000000 00 00000000000000000000000000000000 00000400 {refs} {uses} 00000001 br-lan\n'
        for shell in self.each_shell():
            self.routes.write_text(route.format(refs='00000001',uses='00000002'))
            second=self.tmp/'route2';second.write_text(route.format(refs='000000ff',uses='00000fff'))
            code='. '+str(LIB)+'\nIF_INET6="$INET"; ndp_snapshot; first=$NDP_SNAPSHOT\ncp "$SECOND" "$MU300_IPV6_ROUTE"; ndp_snapshot; [ "$NDP_SNAPSHOT" = "$first" ]\n'
            r=self.sh(shell,code,INET=self.inet6,SECOND=second,MU300_IPV6_ROUTE=self.routes)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_neighbour_updates_immediately_but_our_route_notifications_do_not_loop(self):
        p=self.start();self.assertTrue(wait_for(lambda:(self.tmp/'monitor.pid').exists()))
        initial=self.count()
        self.send('[ROUTE] 2001:db8::/64 dev br-lan metric 128')
        self.send('[ROUTE] default via fe80::1 dev other0')
        time.sleep(.4);self.assertEqual(self.count(),initial)
        self.send('[NEIGH] 2001:db8::22 dev br-lan lladdr aa:bb:cc:dd:ee:ff REACHABLE')
        self.assertTrue(wait_for(lambda:self.count()>initial,timeout=3))
        p.send_signal(signal.SIGTERM);self.assertEqual(p.wait(5),0)
        pid=(self.tmp/'monitor.pid').read_text().strip()
        self.assertFalse(os.path.exists('/proc/'+pid))

    def test_address_change_and_removed_route_trigger_existing_route_functions(self):
        self.start();initial=self.count()
        self.inet6.write_text(ROWS['a']+ROWS['b'])
        self.send('[ADDR] 5: sipa_eth0 inet6 2408:1:2:4::1/64 scope global')
        self.assertTrue(wait_for(lambda:self.count()>=initial+2,timeout=3))
        initial=self.count()
        self.routes.write_text('20010db8000000000000000000000000 40 0 00 0 00000400 0 0 1 br-lan\n')
        self.send('[ROUTE] Deleted 2001:db8::/64 dev br-lan')
        self.assertTrue(wait_for(lambda:self.count()>initial,timeout=3))

    def test_monitor_failure_is_an_error_without_a_polling_backend(self):
        p=self.start(fail=1)
        self.assertNotEqual(p.wait(5),0)
        self.assertLessEqual(self.count(),1)

    def test_address_arriving_during_our_route_writes_is_not_absorbed(self):
        (self.tmp/'during-replace').write_text(ROWS['a']+ROWS['b'])
        self.start()
        self.assertTrue(wait_for(lambda:'route replace 2408:1:2:4::/64' in (self.tmp/'calls').read_text(),timeout=3))

    def test_running_daemon_tracks_completed_data_sim_change(self):
        self.inet6.write_text(ROWS['a']+ROWS['b'].replace('sipa_eth0','sipa_eth8'))
        selected=self.tmp/'selected-iface';selected.write_text('sipa_eth0\n')
        config=self.tmp/'config';config.write_text('initial\n')
        mapper=self.tmp/'mapper.sh';mapper.write_text('mu300_sim_env() { IFS= read -r MU300_SIM_DEVICE < "$STUBLOG/selected-iface"; }\n')
        runtime=self.tmp/'runtime';runtime.mkdir()
        self.start(MU300_NDP_INTERFACE_LIB=LIB.with_name('ndp-interface.sh'),MU300_NDP_SIM_ENV=mapper,MU300_NDP_CONFIG=config,MU300_NDP_RUN=runtime)
        selected.write_text('sipa_eth8\n');config.write_text('SIM2\n')
        self.send('[ADDR] 6: sipa_eth8 inet6 2408:1:2:4::1/64 scope global')
        self.assertTrue(wait_for(lambda:'route replace 2408:1:2:4::/64' in (self.tmp/'calls').read_text(),timeout=3))
        self.assertIn('route del 2001:db8:0:0::/64 dev br-lan',(self.tmp/'calls').read_text())

    def test_event_burst_cannot_accelerate_three_withdrawal_rounds(self):
        source=SCRIPT.read_text()
        functions='IFACE=sipa_eth0\n'+source[source.index('bearer_prefixes() {'):source.index('\ncase ${1:-} in')]
        code=functions+'\n. '+str(LIB)+'''
ndp_events=1; NDP_WITHDRAW_AT=0
ndp_clock() { NDP_NOW=$TEST_NOW; }
last_prefixes='2001:db8:0:0::/64'
for TEST_NOW in 100 100 101 109 110 119 120; do
    prefixes='2408:1:2:4::/64'; withdraw_old; last_prefixes=$prefixes
done
'''
        for shell in self.each_shell():
            (self.tmp/'calls').write_text('')
            r=self.sh(shell,code,MU300_RA_DEPRECATE=self.stubs/'ra-deprecate')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((self.tmp/'calls').read_text().count('ra br-lan'),3)

    @unittest.skipUnless(os.environ.get('MU300_TEST_REAL_NETLINK')=='1','explicit read-only netlink test required')
    def test_actual_iproute2_accepts_the_monitor_subscription(self):
        p=subprocess.Popen(['ip','-o','-6','monitor','label','address','route','neigh'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            time.sleep(.2)
            if p.poll() is not None:self.fail(p.stderr.read().decode())
        finally:
            if p.poll() is None:p.terminate()
            p.wait(5);p.stdout.close();p.stderr.close()
