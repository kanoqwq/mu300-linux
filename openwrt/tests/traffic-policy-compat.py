"""Differential tests against the committed pre-migration Python implementation.

Python runs only on the test host. Candidate code runs in native ucode inside
an isolated OpenWrt container; no device or network interface is exposed.
"""
import argparse
from contextlib import ExitStack
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import types
from unittest.mock import patch
from traffic_policy_test_support import git_options


def baseline(repo, app, reference):
    source = f'openwrt/{app}/root/usr/libexec/unisoc-modem/traffic-policy'
    git = git_options(repo)
    revision = subprocess.check_output([*git, 'rev-parse', reference+'^{commit}'], text=True).strip()
    raw = subprocess.check_output([*git, 'show', f'{revision}:{source}'])
    if not raw.startswith(b'#!/usr/bin/python3'):
        raise RuntimeError('Select the reviewed pre-migration baseline; HEAD is no longer Python')
    module = types.ModuleType('policy_baseline')
    module.__file__ = str(repo / source)
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    return module, revision, hashlib.sha256(raw).hexdigest()


def evaluate(p, c):
    state = copy.deepcopy(c.get('state', {}))
    writes, events = [], []
    output = {}
    try:
        if c['op'] == 'config':
            # config(None) means read-from-disk in the old implementation.
            with patch.object(p, 'read', return_value=c['value']):
                return {'value': p.config()}
        if c['op'] == 'due':
            return {'value': p.due(state, c['schedule'], c['now']), 'state': state}
        if c['op'] == 'next_run':
            return {'value': p.next_run(c['now'], c['clock'])}
        if c['op'] == 'exceeded':
            return {'value': p.exceeded(c['settings'], c['snapshot'])}
        current = copy.deepcopy(state)
        network = copy.deepcopy(c.get('network', {'up': True, 'autostart': True, 'pending': False}))

        def save(path, value):
            nonlocal current
            if c.get('write_fail'):
                raise RuntimeError('storage_failed')
            if path.name == 'policy-status.json':
                output['status'] = copy.deepcopy(value)
            else:
                current = copy.deepcopy(value)
                writes.append(copy.deepcopy(value))

        def bus(iface, method):
            events.append([iface, method])
            if c.get('bus_fail') == method:
                raise RuntimeError('bus_failed')
            if method == 'down': network.update(up=False, autostart=False)
            if method == 'up': network.update(up=True, autostart=True)
            return dict(network)

        def restart(slot):
            events.append(['restart', slot])
            if c.get('restart') == 'error':
                raise RuntimeError('restart_failed')
            return c.get('restart', 'done')

        class BootPath:
            def __init__(self, path): self.path = path
            def read_text(self):
                assert self.path == '/proc/sys/kernel/random/boot_id'
                return c.get('boot', 'one')
            def exists(self):
                assert self.path == '/run/unisoc-data-sim-switch'
                return c.get('switching', False)

        with ExitStack() as patches:
            for name, value in {'write': save, 'bus': bus, 'restart': restart,
                                'snapshot': lambda slot: copy.deepcopy(c.get('snapshot', {})),
                                'active_slot': lambda: c.get('slot', '0'),
                                'uci': lambda key, default='': c.get('uci', {}).get(key, default),
                                'config': lambda: copy.deepcopy(c['settings']),
                                'read': lambda path, default=None: copy.deepcopy(current),
                                'Path': BootPath}.items():
                patches.enter_context(patch.object(p, name, value))
            patches.enter_context(patch.object(p.time, 'time', return_value=c['now']))
            if c['op'] == 'reconcile':
                value = p.reconcile(state, c['settings'], c.get('slot', '0'), c.get('iface', 'wan'),
                                    network, c.get('boot', 'one'))
                return dict(value=value, state=state, writes=writes, events=events, network=network)
            p.tick(c.get('force', False))
        return dict(state=current, writes=writes, events=events, status=output.get('status'), network=network)
    except Exception as error:
        return dict(error=str(error), writes=writes, events=events)


def cases(group, p, seed):
    rng = random.Random(seed)
    rows = []
    if group == 'rules':
        for value in (None, [], {}, {'slots': None}, {'slots': []}, {'schedule': None},
                      {'slots': {'0': None}}, {'schedule': {'enabled': True, 'time': '24:00'}}):
            rows.append(dict(op='config', value=value))
        for _ in range(120):
            cfg = p.config({})
            for slot in ('0', '1'):
                cfg['slots'][slot] = dict(enabled=rng.choice((True, False)), limit_gb=rng.choice((.001, 1, 2, 8.5, 100000)))
            cfg['schedule'] = dict(enabled=rng.choice((True, False)), time=f'{rng.randrange(24):02}:{rng.randrange(60):02}')
            rows.append(dict(op='config', value=cfg))
        for _ in range(650):
            cfg = p.config({})
            cfg['slots']['0'] = dict(enabled=rng.choice((True, False)), limit_gb=2)
            slot = rng.choice(('0', '1'))
            snapshot = rng.choice(({}, {'clock_ok': False}, {'clock_ok': True, 'cycle_used': 1999999999},
                                   {'clock_ok': True, 'cycle_used': 2000000000}, {'clock_ok': True, 'cycle_used': 2000000001}))
            state = rng.choice(({}, {'stopped': {'boot': 'one', 'iface': 'wan'}},
                                {'stopped': {'boot': 'old', 'iface': 'wan'}}))
            network = dict(up=rng.choice((True, False)), autostart=rng.choice((True, False)), pending=False)
            uci = rng.choice(({}, {'network.wan.disabled': '1'}, {'network.wan.auto': '0'}))
            rows.append(dict(op='reconcile', settings=cfg, state=state, snapshot=snapshot,
                             slot=slot, network=network, uci=uci, now=1794960000))
        for _ in range(180):
            cfg = p.config({}); cfg['slots']['0'] = dict(enabled=rng.choice((True, False)), limit_gb=2)
            rows.append(dict(op='tick', settings=cfg, state=rng.choice(({}, {'last': {'result': 'running'}})),
                             now=1794960000, force=rng.choice((True, False)), switching=rng.choice((True, False)),
                             snapshot={'clock_ok': True, 'cycle_used': rng.choice((0, 2e9))},
                             write_fail=rng.choice((False, False, True)), restart=rng.choice(('done', 'radio_off', 'error'))))
    else:
        for month, day in ((3, 8), (11, 1), (11, 18), (12, 31)):
            for hour, minute, second in ((0, 0, 0), (1, 30, 0), (2, 30, 0), (6, 59, 59), (7, 0, 0), (23, 59, 59)):
                now = int(time.mktime((2026, month, day, hour, minute, second, -1, -1, -1)))
                for clock in ('00:00', '01:30', '02:30', '07:00', '23:59'):
                    rows.append(dict(op='next_run', now=now, clock=clock))
                    schedule = dict(enabled=True, time=clock)
                    at = p.next_run(now, clock)
                    for delta in (-1, 0, 1, 120, 121, 3600, 86400):
                        state = dict(next_run=at, schedule_time=clock)
                        rows.append(dict(op='due', now=at+delta, state=state, schedule=schedule))
        for now in (0, 500, 1704067199, 1704067200, 1794960000):
            rows.append(dict(op='due', now=now, state={}, schedule=dict(enabled=True, time='07:00')))
            rows.append(dict(op='due', now=now, state={}, schedule=dict(enabled=False, time='07:00')))
    return rows


def main():
    if sys.argv[1:] == ['--oracle']:
        request = json.load(sys.stdin)
        os.environ['TZ'] = request['zone']; time.tzset()
        module = types.ModuleType('policy_baseline')
        module.__file__ = '/legacy/traffic-policy'
        exec(compile(request['source'], module.__file__, 'exec'), module.__dict__)
        print(json.dumps([evaluate(module, c) for c in request['cases']]))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--group', choices=('rules', 'clock'), required=True)
    parser.add_argument('--seed', type=int, default=20261120)
    parser.add_argument('--image', default='mu300-openwrt-ra-native:25.12.5')
    parser.add_argument('--baseline', default='HEAD', help='Reviewed pre-migration Git revision (tests only)')
    args = parser.parse_args()
    repo = args.repository.resolve()
    app = 'luci-app-t551m' if (repo/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
    p, revision, digest = baseline(repo, app, args.baseline)
    old_source = subprocess.check_output([*git_options(repo), 'show',
                                         f'{revision}:openwrt/{app}/root/usr/libexec/unisoc-modem/traffic-policy'], text=True)
    tests = Path(__file__).resolve().parent
    lib = repo/'openwrt'/app/'root/usr/libexec/unisoc-modem'
    total = 0
    zones = ('CST-8',) if args.group == 'rules' else ('CST-8', 'UTC0', 'EST5EDT,M3.2.0,M11.1.0')
    for zone in zones:
        os.environ['TZ'] = zone; time.tzset()
        inputs = cases(args.group, p, args.seed)
        # The deployed Python runtime and ucode both use musl. A host-glibc
        # mktime oracle disagrees at nonexistent DST wall times; keep libc equal.
        oracle = ['docker', 'run', '--rm', '-i', '--network', 'none',
                  '--mount', f'type=bind,src={tests},dst=/tests,readonly',
                  '--entrypoint', 'python3', 'python:3-alpine',
                  '/tests/traffic-policy-compat.py', '--oracle']
        old = subprocess.run(oracle, input=json.dumps(dict(source=old_source, cases=inputs, zone=zone)),
                             text=True, capture_output=True, check=True)
        expected = json.loads(old.stdout)
        command = ['docker', 'run', '--rm', '-i', '--network', 'none', '-e', f'TZ={zone}',
                   '--mount', f'type=bind,src={lib},dst=/policy,readonly',
                   '--mount', f'type=bind,src={tests},dst=/tests,readonly',
                   '--entrypoint', 'ucode', args.image, '-L', '/policy/*.uc', '/tests/traffic-policy-compat.uc']
        proc = subprocess.run(command, input=json.dumps(inputs), text=True, capture_output=True, check=True)
        actual = json.loads(proc.stdout)
        for index, (old, new) in enumerate(zip(expected, actual, strict=True)):
            if old != new:
                raise AssertionError(json.dumps(dict(repository=str(repo), zone=zone, index=index,
                                                     case=inputs[index], old=old, new=new), indent=2))
        total += len(inputs)
    print(json.dumps(dict(result='PASS', group=args.group, cases=total, repository=str(repo),
                          baseline_revision=revision, baseline_sha256=digest)))


if __name__ == '__main__':
    main()
