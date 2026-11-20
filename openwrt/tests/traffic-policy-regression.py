"""Six distinct offline migration rounds, with per-command logs and source receipts.

Run through tools/build-release.py run. This is a test orchestrator, not a
release worker; it never assembles packages or connects to hardware.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def fingerprint(repo):
    app = 'luci-app-t551m' if (repo/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
    root = repo/'openwrt'/app/'root/usr/libexec'
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root/'unisoc-modem/traffic', root/'unisoc-modem/traffic-policy',
                         root/'unisoc-modem/traffic-policy.uc', root/'rpcd/mu300dash')}


def main():
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[2]
    parser.add_argument('--repositories', nargs='+', type=Path, default=[root])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--baseline', action='append', nargs=2, default=[], metavar=('REPO_NAME','REVISION'),
                        help='Reviewed pre-migration revision for a repository; defaults to its HEAD')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        raise SystemExit('Use the WSL root test runner for actual OTA ownership tests')
    repos = [p.resolve() for p in args.repositories]
    baselines = dict(args.baseline)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    output = args.output or root/'out/build-logs'/f'{stamp}-traffic-policy-six-rounds'
    output.mkdir(parents=True, exist_ok=False)
    tests = Path(__file__).resolve().parent
    stages = [
        ('1-legacy-rules', [('traffic-policy-compat.py',['--group','rules']),
                            ('traffic-policy.py',['--section','unit'])]),
        ('2-clock-and-dst', [('traffic-policy-compat.py',['--group','clock'])]),
        ('3-native-rpc-events', [('traffic-policy.py',['--section','worker'])]),
        ('4-native-concurrency-faults', [('traffic-policy.py',['--section','faults'])]),
        ('5-ota-and-rollback', [('traffic-policy-delivery.py',[])]),
        ('6-endurance', [('traffic-policy.py',['--section','stress'])]),
    ]
    report = dict(state='running', offline_only=True, repositories=[str(p) for p in repos],
                  sources={str(p):fingerprint(p) for p in repos}, rounds=[])

    def save():
        path = output/'results.json'
        temp = path.with_suffix('.new')
        temp.write_text(json.dumps(report,indent=2)+'\n'); temp.replace(path)

    save()
    print(f'Six-round results: {output}',flush=True)
    try:
        for stage, commands in stages:
            row = dict(round=stage,state='running',commands=[])
            report['rounds'].append(row); save()
            for repo in repos:
                for script, extra in commands:
                    log = output/f'{stage}-{repo.name}-{Path(script).stem}.log'
                    command = [sys.executable,str(tests/script),*extra]
                    if script != 'traffic-policy-delivery.py':
                        command += ['--repository',str(repo)]
                        if script == 'traffic-policy-compat.py':
                            command += ['--baseline',baselines.get(repo.name,'HEAD')]
                    else:
                        command[1] = str(repo/'openwrt/tests'/script)
                    print(f'{stage} {repo.name}: {script} {" ".join(extra)}',flush=True)
                    started = time.monotonic()
                    with log.open('w') as stream:
                        result = subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,
                                                env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',
                                                         TRAFFIC_TEST_BASELINE=baselines.get(repo.name,'HEAD')))
                    row['commands'].append(dict(repository=str(repo),command=command,log=str(log),
                                                seconds=round(time.monotonic()-started,2),returncode=result.returncode))
                    save()
                    if result.returncode:
                        row['state']='failed'
                        raise RuntimeError(f'{stage} failed; inspect {log}')
            row['state']='complete'; save()
            print(f'PASS round {stage}',flush=True)
        for repo in repos:
            if fingerprint(repo) != report['sources'][str(repo)]:
                raise RuntimeError(f'Source changed while tests were running: {repo}')
        report['state']='complete'; save()
        print('PASS all six rounds; production sources unchanged during validation',flush=True)
    except BaseException as error:
        report.update(state='failed',error=str(error)); save()
        raise


if __name__ == '__main__':
    main()
