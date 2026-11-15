#!/usr/bin/env python3
"""Exercise production NDP routing in a private kernel network namespace.

Uses an existing OpenWrt target root and Docker image, never a device or host
network. Run through tools/build-release.py run, as with ra-renew-netns.py.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

TOP = Path(__file__).resolve().parents[1]
SCENARIO = r'''
import os
from pathlib import Path
import subprocess

ip = '/target/sbin/ip'
def run(*args):
    return subprocess.check_output([ip, *args], text=True)

case = Path('/case')
(case/'bin').mkdir()
(case/'config').write_text('fixture\n')
for name, code in {
    'uci': 'case "$*" in *main.data_sim) echo "$CASE_SIM";; *main.sim_slots) echo 2;; *main.sim?_device) :;; *) echo relay;; esac',
    'logger': 'exit 0',
}.items():
    path = case/'bin'/name
    path.write_text('#!/bin/sh\n'+code+'\n')
    path.chmod(0o755)

for slot in (0, 1):
    bearer = 'sipa_eth'+str(slot*8)
    for device in (bearer, 'br-lan'):
        run('link', 'add', device, 'type', 'dummy')
        run('link', 'set', device, 'up')
    run('-6', 'addr', 'add', 'fe80::2/64', 'dev', bearer, 'nodad')
    run('-6', 'addr', 'add', '2001:db8:55::1/64', 'dev', bearer, 'nodad')
    run('-6', 'addr', 'add', '2001:db8:66::1/64', 'dev', bearer, 'nodad')
    run('-6', 'route', 'add', 'default', 'via', 'fe80::1', 'dev', bearer)
    # Reproduce the old preference without fabricating ip command replies.
    run('-6', 'route', 'add', '2001:db8:55::/64', 'dev', 'br-lan', 'metric', '1024')
    assert 'dev '+bearer in run('-6', 'route', 'get', '2001:db8:55::99')
    print('CONTROL slot', slot, ': the WAN on-link route wins at the old metric', flush=True)
    run('-6', 'route', 'del', '2001:db8:55::/64', 'dev', 'br-lan', 'metric', '1024')

    env = dict(os.environ, CASE_SIM=str(slot),
        PATH='/case/bin:/target/sbin:/target/usr/sbin:'+os.environ['PATH'],
        MU300_NDP_INTERFACE_LIB='/repo/rootfs/overlay/opt/mu300/lib/ndp-interface.sh',
        MU300_NDP_SIM_ENV='/repo/rootfs/overlay/opt/mu300/bin/mu300-sim-env',
        MU300_NDP_RUN='/case', MU300_NDP_CONFIG='/case/config')
    def learn():
        subprocess.run(['/bin/sh', '/repo/openwrt/overlay/opt/mu300/bin/ndp-learn', '--once'],
                       env=env, check=True)
    learn()
    for address in ('2001:db8:55::99', '2001:db8:66::88'):
        selected = run('-6', 'route', 'get', address)
        assert 'dev br-lan' in selected and 'metric 128' in selected, selected
    # The selected route is the shared /64, not a learned per-host pin.
    assert 'local 2001:db8:55::1 dev lo' in run('-6', 'route', 'get', '2001:db8:55::1')
    assert 'via fe80::1 dev '+bearer in run('-6', 'route', 'get', '2001:db8:99::42')
    assert '2001:db8:55::/64 proto kernel' in run('-6', 'route', 'show', 'dev', bearer)
    learn()
    assert 'dev br-lan' in run('-6', 'route', 'get', '2001:db8:55::99')
    # Point-to-point/RA setups without an on-link bearer route still work.
    run('-6', 'route', 'del', '2001:db8:55::/64', 'dev', bearer)
    learn()
    assert 'dev br-lan' in run('-6', 'route', 'get', '2001:db8:55::99')
    print('PASS slot', slot, ': unknown clients route to LAN; local WAN and upstream gateway preserved', flush=True)
    for device in ('br-lan', bearer):
        run('link', 'del', device)
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if root == Path('/') or not (root/'etc/openwrt_release').is_file():
        raise ValueError('Use an existing prepared OpenWrt root')
    parent = os.environ.get('TMPDIR')
    with tempfile.TemporaryDirectory(prefix='ipv6-inbound-netns-', dir=parent) as directory:
        path = Path(directory)
        (path/'scenario.py').write_text(SCENARIO)
        subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
            '--network', 'none', '--cap-add', 'NET_ADMIN', '--cap-add', 'NET_RAW',
            '--sysctl', 'net.ipv6.conf.all.disable_ipv6=0',
            '--sysctl', 'net.ipv6.conf.default.disable_ipv6=0',
            '--sysctl', 'net.ipv6.conf.all.forwarding=1',
            '-v', str(root)+':/target:ro', '-v', str(TOP)+':/repo:ro',
            '-v', str(path)+':/case', '-e', 'PYTHONHOME=/target/usr',
            '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-e', 'LD_LIBRARY_PATH=/target/usr/lib:/target/lib',
            '--entrypoint', '/target/usr/bin/python3', args.image,
            '-B', '/case/scenario.py'], check=True, timeout=60)

if __name__ == '__main__':
    main()
