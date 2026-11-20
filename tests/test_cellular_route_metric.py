"""Real netifd/kernel route selection, using isolated dummy bearers and AT stubs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

TOP = Path(__file__).resolve().parents[1]
PROTO = TOP/'openwrt/overlay/lib/netifd/proto'
IMAGE = os.environ.get('MU300_ROUTE_TEST_IMAGE')


@unittest.skipUnless(IMAGE and shutil.which('docker'), 'explicit OpenWrt test image required')
class RouteMetrics(unittest.TestCase):
    def test_real_setup_reload_bearer_report_and_uplink_failover(self):
        with tempfile.TemporaryDirectory(prefix='cell-route-metric-') as d:
            root=Path(d)
            def write(name, text):
                p=root/name; p.write_text(text); p.chmod(0o755)
            write('mu300cell.sh', (PROTO/'mu300cell.sh').read_text())
            write('mobile-data', '''#!/bin/sh
echo "$*" >> /tmp/mobile-calls
[ "$1" = up ] || exit 0
printf '%s\\n' IP=192.0.2.2 IFACE=sipa_test PREFIX=24 DNS1=192.0.2.53 PDP_TYPE=IP
''')
            write('sim-env', '''mu300_sim_env() {
MU300_SIM_SLOT=0; MU300_SIM_DEVICE=sipa_test; MU300_SIM_URC=/tmp/test.urc
}
''')
            write('monitor.sh', '''#!/bin/sh
case "${1:-}-${2:-}" in *-dump|dump-*) exit 0;; esac
exec sleep 300
''')
            monitor=(PROTO/'mu300cell-v6.sh').read_text()
            functions=monitor[monitor.index('current_v6() {'):monitor.index('\nread_bearer() {')]
            write('report.sh', '''#!/bin/sh
. /lib/functions.sh
. /lib/netifd/netifd-proto.sh
config=wan; ifname=sipa_test; ip4=192.0.2.3; prefix4=24; peerdns=0
'''+functions+'\nreport\n')
            write('run.sh', r'''#!/bin/sh
set -eu
mkdir -p /tmp/metric-config /run /var/run/ubus
cat >/tmp/metric-config/network <<'EOF'
config interface 'wan'
 option proto 'mu300cell'
 option device 'sipa_test'
 option metric '200'
 option pdptype 'IP'
config interface 'wwan'
 option proto 'static'
 option device 'wifi_test'
 option ipaddr '198.51.100.2'
 option netmask '255.255.255.0'
 option gateway '198.51.100.1'
 option metric '40'
EOF
ip link add sipa_test type dummy
ip link add wifi_test type dummy
ip link set sipa_test up
ip link set wifi_test up
ubusd >/tmp/ubusd.log 2>&1 &
netifd -S -c /tmp/metric-config >/tmp/netifd.log 2>&1 &
wait_up() {
 for i in $(seq 1 20); do
  [ "$(ubus call network.interface.wan status 2>/dev/null | jsonfilter -e @.up 2>/dev/null)" != true ] || return 0
  sleep 1
 done
 cat /tmp/netifd.log; return 1
}
reload() {
 uci -c /tmp/metric-config commit network
 ubus call network reload
 sleep 1
}
route_is() {
 ip -4 route get 203.0.113.9 | grep -q "dev $1"
}
wait_up
sleep 1
ip -4 route show default
ip -4 route show default dev sipa_test | grep -q 'metric 200'
route_is wifi_test
echo PASS_WIFI_PREFERRED
uci -c /tmp/metric-config set network.wwan.metric=300
reload
route_is sipa_test
echo PASS_WIFI_METRIC_RELOAD
before=$(wc -l </tmp/mobile-calls)
uci -c /tmp/metric-config set network.wan.metric=400
reload
route_is wifi_test
ip -4 route show default dev sipa_test | grep -q 'metric 400'
[ "$(wc -l </tmp/mobile-calls)" = "$before" ]
echo PASS_CELL_METRIC_RELOAD_WITHOUT_REDIAL
ip -4 addr replace 192.0.2.3/24 dev sipa_test
/fixture/report.sh
sleep 1
route_is wifi_test
ip -4 route show default dev sipa_test | grep -q 'metric 400'
echo PASS_EVENT_REPORT_PRESERVES_PRIORITY
ubus call network.interface.wwan down
sleep 1
route_is sipa_test
echo PASS_FALLBACK_TO_CELL
uci -c /tmp/metric-config set network.wan.metric=0
reload
ip -4 route show default dev sipa_test | grep -q default
if ip -4 route show default dev sipa_test | grep -q 'metric '; then exit 1; fi
echo PASS_EXPLICIT_ZERO
uci -c /tmp/metric-config set network.wan.defaultroute=0
reload
[ -z "$(ip -4 route show default dev sipa_test)" ]
echo PASS_DEFAULTROUTE_DISABLED
uci -c /tmp/metric-config set network.wan.defaultroute=1
uci -c /tmp/metric-config set network.wan.ip4table=100
reload
ip -4 route show table 100 default dev sipa_test | grep -q default
[ -z "$(ip -4 route show default dev sipa_test)" ]
echo PASS_CONFIGURED_TABLE
ubus call network.interface.wan down
sleep 1
[ -z "$(ip -4 route show table 100 default dev sipa_test)" ]
echo PASS_TEARDOWN
''')
            name='pro-route-test-'+uuid.uuid4().hex[:10]
            args=['docker','run','--rm','--name',name,'--network','none','--cap-add','NET_ADMIN',
                  '--platform',os.environ.get('MU300_ROUTE_TEST_PLATFORM','linux/arm64'),
                  '-v',str(root)+':/fixture:ro',
                  '-v',str(root/'mu300cell.sh')+':/lib/netifd/proto/mu300cell.sh:ro',
                  '-v',str(root/'monitor.sh')+':/lib/netifd/proto/mu300cell-v6.sh:ro',
                  '-v',str(root/'mobile-data')+':/opt/mu300/bin/mobile-data:ro',
                  '-v',str(root/'sim-env')+':/opt/mu300/bin/mu300-sim-env:ro',
                  '--entrypoint','/bin/sh',IMAGE,'/fixture/run.sh']
            try:
                result=subprocess.run(args,capture_output=True,text=True,timeout=60)
            finally:
                subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stdout.count('PASS_'),9,result.stdout)
            print(result.stdout,end='')


if __name__=='__main__':
    unittest.main()
