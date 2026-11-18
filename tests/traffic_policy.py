"""Offline policy tests: real state files, fake netifd and modem operations."""
import datetime as dt
from contextlib import nullcontext
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'openwrt').is_dir())
APP = 'luci-app-t551m' if (ROOT / 'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
SOURCE = ROOT / 'openwrt' / APP / 'root/usr/libexec/unisoc-modem/traffic-policy'
loader = importlib.machinery.SourceFileLoader('traffic_policy', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)


class Policy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(p, 'BASE', self.root / 'disk').start()
        patch.object(p, 'RUN', self.root / 'ram').start()
        p.BASE.mkdir(); p.RUN.mkdir()
        self.network = {'up': True, 'autostart': True, 'pending': False}
        self.events = []
        self.cfg = {'unisoc_modem.main.sim_slots': '2'}
        self.active = '0'
        patch.object(p, 'active_slot', lambda: self.active).start()
        patch.object(p, 'uci', lambda key, default='': self.cfg.get(key, default)).start()
        patch.object(p, 'bus', self.bus).start()
        self.restart = patch.object(p, 'restart', return_value='done').start()
        self.spawn = patch.object(p, 'spawn').start()
        self.settings = p.config({})
        self.settings['slots']['0'] = {'enabled': True, 'limit_gb': 2}
        self.snapshot(0, 2000000000)
        self.snapshot(1, 0)

    def bus(self, iface, method):
        self.events.append((iface, method))
        if method == 'down': self.network.update(up=False, autostart=False)
        if method == 'up': self.network.update(up=True, autostart=True)
        return dict(self.network) if method == 'status' else {}

    def snapshot(self, slot, used, **kw):
        p.write(p.RUN / ('view-sim%d.json' % slot), dict(clock_ok=True, cycle_used=used, **kw))

    def reconcile(self, state, boot='one'):
        return p.reconcile(state, self.settings, self.active, 'wan', dict(self.network), boot)

    def test_default_off_and_validation(self):
        self.assertFalse(p.config({})['schedule']['enabled'])
        for c in p.config({})['slots'].values(): self.assertFalse(c['enabled'])
        for bad in (None, [], {'enabled': 1, 'limit_gb': 1}, {'enabled': True, 'limit_gb': 0},
                    {'enabled': True, 'limit_gb': float('nan')}, {'enabled': False, 'limit_gb': -1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                p.config({'slots': {'0': bad}})
        for clock in ('24:00', '7:00', '07:60', '07:00;reboot', None):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                p.config({'schedule': {'enabled': True, 'time': clock}})

    def test_threshold_and_no_repeated_down(self):
        state = {}
        self.snapshot(0, 1999999999)
        self.assertFalse(self.reconcile(state))
        self.snapshot(0, 2000000000)
        self.assertTrue(self.reconcile(state))
        self.assertEqual(self.events, [('wan', 'down')])
        self.assertTrue(self.reconcile(state))
        self.assertEqual(len(self.events), 1)
        self.assertEqual(p.read(p.BASE / 'policy-state.json')['stopped']['iface'], 'wan')

    def test_cycle_reset_resumes_owned_wan(self):
        state = {}; self.reconcile(state)
        self.snapshot(0, 0)
        self.assertFalse(self.reconcile(state))
        self.assertEqual(self.events, [('wan', 'down'), ('wan', 'up')])
        self.assertNotIn('stopped', state)

    def test_disabling_protection_resumes(self):
        state = {}; self.reconcile(state)
        self.settings['slots']['0']['enabled'] = False
        self.reconcile(state)
        self.assertEqual(self.events[-1], ('wan', 'up'))

    def test_inactive_sim_never_blocks_wan(self):
        self.active = '1'; state = {}
        self.assertFalse(self.reconcile(state))
        self.assertEqual(self.events, [])

    def test_switch_from_blocked_to_unlimited(self):
        state = {}; self.reconcile(state)
        self.active = '1'; self.reconcile(state)
        self.assertEqual(self.events[-1], ('wan', 'up'))

    def test_manual_down_is_never_owned_or_resumed(self):
        self.network.update(up=False, autostart=False)
        state = {}; self.reconcile(state)
        self.settings['slots']['0']['enabled'] = False
        self.reconcile(state)
        self.assertEqual(self.events, [])

    def test_explicit_persistent_disable_wins(self):
        for key, value in (('disabled', '1'), ('auto', '0')):
            with self.subTest(key=key):
                self.cfg = {}; self.network.update(up=True, autostart=True)
                state = {}; self.settings['slots']['0']['enabled'] = True; self.reconcile(state)
                self.cfg['network.wan.' + key] = value
                self.settings['slots']['0']['enabled'] = False; self.events.clear()
                self.reconcile(state)
                self.assertEqual(self.events, [])

    def test_clock_or_missing_snapshot_does_not_release(self):
        state = {}; self.reconcile(state)
        p.write(p.RUN / 'view-sim0.json', {'clock_ok': False})
        self.assertTrue(self.reconcile(state))
        self.assertNotIn(('wan', 'up'), self.events)

    def test_reboot_does_not_restore_stale_wan_ownership(self):
        state = {}; self.reconcile(state)
        self.settings['slots']['0']['enabled'] = False
        self.events.clear(); self.reconcile(state, boot='two')
        self.assertEqual(self.events, [])

    def test_failed_persistence_cannot_stop_network(self):
        with patch.object(p, 'write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.reconcile({})
        self.assertEqual(self.events, [])

    def test_crash_before_down_retries_from_ownership_record(self):
        with patch.object(p, 'bus', side_effect=RuntimeError('bus closed')):
            with self.assertRaises(RuntimeError): self.reconcile({})
        state = p.read(p.BASE / 'policy-state.json')
        self.reconcile(state)
        self.assertEqual(self.events, [('wan', 'down')])

    def test_policy_paths_for_separate_and_combined_ledgers(self):
        (p.RUN / 'view-sim0.json').unlink(); (p.RUN / 'view-sim1.json').unlink()
        p.write(p.RUN / 'view.json', {'cycle_used': 123})
        p.write(p.RUN / 'sim1/view.json', {'cycle_used': 456})
        self.assertEqual(p.snapshot('0')['cycle_used'], 123)
        self.assertEqual(p.snapshot('1')['cycle_used'], 456)

    def api(self, op, args):
        with patch('sys.stdin', io.StringIO(json.dumps(args))): return p.main([op])

    def test_partial_save_does_not_overwrite_other_sim_or_schedule(self):
        p.write(p.BASE / 'policy.json', self.settings)
        self.api('set', {'slot': '1', 'config': {'quota': {'enabled': True, 'limit_gb': 8.5}}})
        self.api('set', {'slot': '0', 'config': {'schedule': {'enabled': True, 'time': '16:45'}}})
        saved = p.config()
        self.assertEqual(saved['slots']['0'], self.settings['slots']['0'])
        self.assertEqual(saved['slots']['1']['limit_gb'], 8.5)
        self.assertEqual(saved['schedule'], {'enabled': True, 'time': '16:45'})
        self.assertEqual(self.api('get', {'slot': '1'})['slot'], 1)

    def test_invalid_scope_and_confirmation_have_no_side_effects(self):
        for op, args in [('set', {'slot': 'all', 'config': {}}), ('get', {'slot': 2}),
                         ('run', {'confirm': False}), ('run', {'confirm': 'true'})]:
            with self.subTest(op=op), self.assertRaises(ValueError): self.api(op, args)
        self.cfg['unisoc_modem.main.sim_slots'] = '1'
        with self.assertRaises(ValueError): self.api('set', {'slot': 1, 'config': {}})
        self.spawn.assert_not_called()
        self.assertFalse((p.BASE / 'policy.json').exists())

    def test_tick_and_manual_run_skip_quota_and_manual_wan_off(self):
        p.write(p.BASE / 'policy.json', self.settings)
        p.tick(True)
        self.restart.assert_not_called()
        self.assertEqual(p.read(p.BASE / 'policy-state.json')['last']['result'], 'quota_blocked')
        self.settings['slots']['0']['enabled'] = False
        p.write(p.BASE / 'policy.json', self.settings)
        (p.BASE / 'policy-state.json').unlink()  # A manually off WAN with no policy ownership.
        p.tick(True)
        self.restart.assert_not_called()
        self.assertEqual(p.read(p.BASE / 'policy-state.json')['last']['result'], 'wan_disabled')

    def test_restart_result_and_failure_are_recorded(self):
        self.settings['slots']['0']['enabled'] = False
        p.write(p.BASE / 'policy.json', self.settings)
        p.tick(True)
        self.restart.assert_called_once_with('0')
        self.assertEqual(p.read(p.BASE / 'policy-state.json')['last']['result'], 'done')
        self.restart.side_effect = RuntimeError('no response')
        p.tick(True)
        self.assertEqual(p.read(p.BASE / 'policy-state.json')['last']['detail'], 'no response')

    def test_restart_adapter_keeps_slot_owner_and_propagates_failure(self):
        # Exercise the actual adapter rather than our tick's modem stub.
        adapter = importlib.util.module_from_spec(spec)
        loader.exec_module(adapter)
        scope = self.root/'sim-scope.sh'; scope.write_text('fixture')
        with patch.object(adapter, 'LIB', self.root), patch.object(adapter, 'command', return_value='') as command:
            with patch.dict(os.environ, {'MU300_AT_DIR': 'wrong-slot', 'MU300_SCOPED_SLOT': '0'}):
                self.assertEqual(adapter.restart('1'), 'done')
            env = command.call_args.kwargs['env']
            self.assertEqual(env['MU300_SIM_SLOT'], '1')
            self.assertNotIn('MU300_AT_DIR', env)
            self.assertNotIn('MU300_SCOPED_SLOT', env)
            self.assertIn('sim_scope', command.call_args.args[0][2])
            scope.unlink()
            command.return_value = '{"ok":1,"reconnect":"deferred"}'
            self.assertEqual(adapter.restart('0'), 'radio_off')
            self.assertEqual(command.call_args.args[0], [str(self.root/'lock'), 'restart'])
            command.return_value = '{"ok":0,"reconnect":"failed","reconnect_error":"no final reply"}'
            with self.assertRaisesRegex(RuntimeError, 'no final reply'): adapter.restart('0')

    def test_e5_serialized_entry_reuses_band_restart_without_saving_settings(self):
        control = SOURCE.with_name('lock-control')
        if not control.exists():
            return  # MU300/Pro exercise the shell entry in test_lock_jobs.py.
        sl = importlib.machinery.SourceFileLoader('policy_e5_locks', str(control))
        spec = importlib.util.spec_from_loader(sl.name, sl)
        locks = importlib.util.module_from_spec(spec)
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            sl.exec_module(locks)
        with patch.object(locks, 'exclusive', return_value=nullcontext()), patch.object(locks, 'reconnect', return_value={'reconnect':'done'}) as reconnect:
            self.assertEqual(locks.main(['restart']), {'ok':1, 'reconnect':'done'})
            reconnect.assert_called_once_with(bands=True)


class Schedule(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get('TZ')
        os.environ['TZ'] = 'Asia/Shanghai'; time.tzset()
        self.addCleanup(self.restore)
        self.now = time.mktime((2026, 11, 18, 6, 59, 30, -1, -1, -1))
        self.schedule = {'enabled': True, 'time': '07:00'}

    def restore(self):
        if self.old is None: os.environ.pop('TZ', None)
        else: os.environ['TZ'] = self.old
        time.tzset()

    def test_due_once_and_clock_backwards(self):
        state = {}
        self.assertFalse(p.due(state, self.schedule, self.now))
        self.assertTrue(p.due(state, self.schedule, self.now + 30))
        self.assertFalse(p.due(state, self.schedule, self.now + 40))
        state['next_run'] = self.now + 30
        self.assertFalse(p.due(state, self.schedule, self.now + 31))
        self.assertTrue(p.due(state, self.schedule, self.now + 86430))

    def test_missed_unsynced_and_disabled(self):
        state = {}; p.due(state, self.schedule, self.now)
        self.assertFalse(p.due(state, self.schedule, self.now + 3600))
        self.assertEqual(state['last']['result'], 'missed')
        self.assertFalse(p.due({}, self.schedule, 500))
        self.assertFalse(p.due({}, {'enabled': False, 'time': '07:00'}, self.now + 30))

    def test_new_schedule_does_not_replay_past_due(self):
        state = {}; self.assertFalse(p.due(state, self.schedule, self.now + 60))
        self.assertGreater(state['next_run'], self.now + 86400)
        self.assertFalse(p.due(state, {'enabled': True, 'time': '08:00'}, self.now))
        self.assertEqual(time.localtime(state['next_run']).tm_hour, 8)

    def test_dst_and_calendar_rollover(self):
        os.environ['TZ'] = 'America/New_York'; time.tzset()
        now = time.mktime((2026, 10, 31, 8, 0, 0, -1, -1, -1))
        then = p.next_run(now, '08:00')
        self.assertEqual(then - now, 25 * 3600)
        self.assertEqual(time.localtime(then).tm_hour, 8)
        os.environ['TZ'] = 'Asia/Shanghai'; time.tzset()
        now = time.mktime((2026, 12, 31, 23, 59, 0, -1, -1, -1))
        self.assertEqual(dt.date.fromtimestamp(p.next_run(now, '00:00')).isoformat(), '2027-01-01')


class PublicRPC(unittest.TestCase):
    def test_browser_payload_scope_and_errors_through_actual_shell_rpc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bin = root/'bin';bin.mkdir()
            (root/'disk').mkdir();(root/'ram').mkdir()
            def stub(name, body):
                path = bin/name;path.write_text(body);path.chmod(0o755)
            (bin/'python3').symlink_to(sys.executable)
            stub('uci', '#!/bin/sh\ncase "$3" in *.sim_slots) echo 2;; *.data_interface) echo testwan;; *) exit 1;; esac\n')
            stub('ubus', '#!/bin/sh\nprintf \'{"autostart":false,"up":false}\\n\'\n')
            stub('jsonfilter', '#!' + sys.executable + '\nimport sys,json\ni=json.load(sys.stdin);v=i.get(sys.argv[-1].removeprefix("@."), "");print(v if isinstance(v,str) else json.dumps(v))\n')
            pool = root/'pool.sh';pool.write_text('mu300_sms_pool() { echo /tmp/test-sms-pool; }\n')
            env = dict(os.environ, PATH=str(bin) + ':' + os.environ['PATH'], MU300_DASH_BIN=str(SOURCE.parent),
                       MU300_SMS_POOL_LIB=str(pool), MU300_TRAFFIC_DIR=str(root/'disk'),
                       MU300_TRAFFIC_RUN=str(root/'ram'), MU300_DASH_DIR=str(root/'dash'))
            rpc = SOURCE.parent.parent/'rpcd/mu300dash'
            def call(method, data, invalid=False):
                r = subprocess.run(['sh', str(rpc), 'call', method], input=json.dumps(dict(data, ubus_rpc_session='fixture')),
                                   text=True, capture_output=True, env=env, timeout=5)
                if not invalid: self.assertEqual(r.returncode, 0, r.stderr)
                return json.loads(r.stdout)
            first = call('traffic_policy_get', {'slot': '1'})
            self.assertEqual(first['slot'], 1)
            self.assertFalse(first['quota']['enabled'])
            self.assertEqual(call('traffic_policy_run', {'confirm': False})['error'], 'confirmation_required')
            payload = {'slot':'1', 'config': {'quota': {'enabled':False, 'limit_gb':12}}}
            self.assertEqual(call('traffic_policy_set', {'payload':json.dumps(payload)})['ok'], 1)
            self.assertEqual(call('traffic_policy_get', {'slot':'1'})['quota']['limit_gb'], 12)
            self.assertEqual(call('traffic_policy_get', {'slot':'0'})['quota']['limit_gb'], 0)
            self.assertEqual(call('traffic_policy_set', {'payload':'{"slot":"all","config":{}}'})['error'], 'invalid_slot')
            self.assertFalse(call('traffic_policy_get', {'slot':'all'}, invalid=True)['ok'])
            # Let the private worker finish before its temporary files disappear.
            for _ in range(100):
                if (root/'ram/policy-status.json').exists(): break
                time.sleep(.01)
            self.assertTrue((root/'ram/policy-status.json').exists())


if __name__ == '__main__': unittest.main(verbosity=2)
