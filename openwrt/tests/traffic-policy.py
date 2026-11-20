"""Run the production ucode policy and accounting loop in an isolated OpenWrt image."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--image', default='mu300-openwrt-ra-native:25.12.5')
    parser.add_argument('--section', choices=('all','unit','worker','faults','stress'), default='all')
    args = parser.parse_args()
    repo = args.repository.resolve()
    app = 'luci-app-t551m' if (repo / 'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
    lib = repo / 'openwrt' / app / 'root/usr/libexec/unisoc-modem'
    tests = Path(__file__).resolve().parent
    common = ['docker', 'run', '--rm', '--network', 'none',
              '--mount', f'type=bind,src={lib},dst=/policy,readonly',
              '--mount', f'type=bind,src={lib.parent}/rpcd/mu300dash,dst=/rpc/mu300dash,readonly',
              '--mount', f'type=bind,src={tests},dst=/tests,readonly']
    if args.section in ('all','unit'):
        for zone in ('CST-8', 'EST5EDT,M3.2.0,M11.1.0'):
            subprocess.run([*common, '-e', f'TZ={zone}', '--entrypoint', 'ucode', args.image,
                            '-L', '/policy/*.uc', '/tests/traffic-policy.uc'], check=True)
    for section in ('worker','faults'):
        if args.section in ('all',section):
            subprocess.run([*common, '-e', 'TRAFFIC_TEST_LIB=/policy', '--entrypoint', '/bin/sh',
                            args.image, f'/tests/traffic-policy-{section}.sh'], check=True)
    if args.section in ('all','stress'):
        subprocess.run([*common, '-e', 'TZ=CST-8', '--entrypoint', 'ucode', args.image,
                        '-L', '/policy/*.uc', '/tests/traffic-policy-stress.uc'], check=True)


if __name__ == '__main__':
    main()
