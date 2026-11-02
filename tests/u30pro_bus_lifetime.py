"""Run production snapshots against real ubusd under a low FD limit.

All interfaces/files are private fixtures, no modem, hardware or network access.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from native_dashboard_fixture import Snapshot

TOP = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get('U30PRO_BUS_TEST_IMAGE', 'mu300-openwrt-base:25.12.5')


@unittest.skipUnless(os.name == 'posix' and shutil.which('docker'), 'local OpenWrt ARM64 test image')
class Lifetime(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='u30pro-bus-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.snap = Snapshot(self.root)
        for name, value in (('rpc', self.snap.responses), ('stations', self.snap.stations),
                            ('addresses', self.snap.addresses)):
            (self.root/(name+'.json')).write_text(json.dumps(value))

    def run_program(self, code, real_bus=True):
        module = self.root/'modules'
        if real_bus:
            source = (module/'dash.uc').read_text().replace("from './bus.uc'", "from 'ubus'")
            (module/'dash.uc').write_text(source)
        program = module/'lifetime.uc'
        program.write_text("import * as dash from './dash.uc';\n"+code)
        args = ['docker', 'run', '--rm', '--platform', 'linux/arm64', '--network', 'none',
                '--cpus', '1', '--read-only', '--tmpfs', '/tmp',
                '-v', str(TOP)+':'+str(TOP)+':ro', '-v', str(self.root)+':'+str(self.root),
                '-e', 'MU300_DASH_SYSROOT='+str(self.root),
                '-e', 'MU300_DASH_CONFIG='+str(self.snap.config),
                '-e', 'DASH_FIXTURE='+str(self.root), '--entrypoint', '/bin/sh', IMAGE,
                '-ec', 'mkdir -p /var/run; /sbin/ubusd & sleep 1; ulimit -n 64; exec /usr/bin/ucode "$1"',
                'lifetime', str(program)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return json.loads(result.stdout)

    def test_repeated_owned_snapshots_and_collects_do_not_leak_real_sockets(self):
        result = self.run_program('''
import * as fs from 'fs';
// The first connect initializes uloop's one epoll and wakeup pipe.
dash.info('0'); dash.status('0',true);
let baseline=length(fs.lsdir('/proc/self/fd'));
for(let i=0;i<128;i++) {
    dash.info('0'); dash.status('0',true);
    if(length(fs.lsdir('/proc/self/fd'))>baseline+1)
        die(sprintf('snapshot leaked an FD at %d: %J',i,map(fs.lsdir('/proc/self/fd'),n=>[n,fs.readlink('/proc/self/fd/'+n)])));
}
print(sprintf('%J\\n',{iterations:128,baseline,after:length(fs.lsdir('/proc/self/fd'))}));
''')
        self.assertLessEqual(result['after'], result['baseline']+1)

    def test_persistent_screen_connection_is_borrowed_and_stays_usable(self):
        result = self.run_program('''
import * as fs from 'fs';
import { connect } from 'ubus';
import { cursor } from 'uci';
const bus=connect(null,2), config=cursor(getenv('MU300_DASH_CONFIG'));
if(!bus)die('test bus unavailable');
let baseline=length(fs.lsdir('/proc/self/fd'));
for(let i=0;i<128;i++) {
    dash.info('0',bus,config); dash.status('0',true,bus,config);
    if(length(fs.lsdir('/proc/self/fd'))>baseline+1)die('borrowed connection leaked an FD');
}
if(type(bus.list())!='array')die('snapshot closed its caller connection');
let after=length(fs.lsdir('/proc/self/fd')); bus.disconnect();
print(sprintf('%J\\n',{iterations:128,baseline,after}));
''')
        self.assertLessEqual(result['after'], result['baseline']+1)

    def test_exception_releases_owned_but_never_closes_borrowed_connection(self):
        self.snap.responses['fixture.throw'] = True
        (self.root/'rpc.json').write_text(json.dumps(self.snap.responses))
        result = self.run_program('''
import * as bus from './bus.uc';
try{dash.info('0');}catch(e){}
let owned=bus.counts(), shared=bus.connect();
try{dash.info('0',shared);}catch(e){}
let borrowed=bus.counts();shared.disconnect();
print(sprintf('%J\\n',{owned,borrowed,final:bus.counts()}));
''', real_bus=False)
        self.assertEqual(result['owned'], {'opened':1, 'closed':1, 'live':0})
        self.assertEqual(result['borrowed'], {'opened':2, 'closed':1, 'live':1})
        self.assertEqual(result['final']['live'], 0)

    def test_negative_control_detects_the_original_missing_disconnect(self):
        module = self.root/'modules/dash.uc'
        module.write_text(module.read_text().replace('if(owned)bus?.disconnect();', '/* deliberately reproduce the leak */'))
        with self.assertRaisesRegex(AssertionError, 'FD leak detected'):
            self.run_program('''
import * as fs from 'fs';
dash.info('0'); let baseline=length(fs.lsdir('/proc/self/fd'));
for(let i=0;i<128;i++) {
    dash.info('0');
    if(length(fs.lsdir('/proc/self/fd'))>baseline+1)die('FD leak detected');
}
print('{}');
''')


if __name__ == '__main__':
    unittest.main()
