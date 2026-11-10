"""Offline call-event replay. No device, modem, real call or recipient."""
import importlib.machinery
import importlib.util
import io
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from unittest.mock import Mock

TOP = Path(__file__).resolve().parents[2]
PLUGIN = next(p for p in (TOP / 'openwrt').glob('luci-app-*') if (p / 'root/usr/libexec/unisoc-modem/call-notify').exists())
SOURCE = PLUGIN / 'root/usr/libexec/unisoc-modem/call-notify'
loader = importlib.machinery.SourceFileLoader('call_notify', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class CallNotifications(unittest.TestCase):
    def test_empty_successful_rpc_reply_is_not_an_action_failure(self):
        with patch.object(module.subprocess, 'check_output', return_value=b''):
            self.assertEqual(module.call('u30pro.panel', 'incoming', {'calls': []}), {})
            self.assertEqual(module.call('u30pro.panel', 'incoming_result', {'id': 'a' * 32, 'ok': True}), {})
        with patch.object(module.subprocess, 'check_output', return_value=b'broken'):
            with self.assertRaises(ValueError):
                module.call('mu300.modem', 'status')

    def setUp(self):
        self.alerts, self.views = [], []
        def send(item):
            self.alerts.append(dict(item))
            return True
        self.state = module.Calls(send, lambda x: self.views.append(x), lambda: {})

    def row(self, slot=0, index=1, state='ringing-in', number='+12345', direction='incoming'):
        return dict(key=f'{slot}:{index}', card=slot, index=index, state=state,
                    number=number, direction=direction)

    def test_repeated_hints_and_sip_format_deduplicate_both_slots(self):
        rows = module.parse_clcc('+CLCC: 1,1,4,0,0,"sip:+12345@example.test;user=phone",145\nOK', 0)
        self.state.update(rows, (0,))
        for _ in range(100):
            self.state.update([self.row()], (0,))
        self.state.update([self.row(slot=1)], (1,))
        self.assertEqual(len(self.alerts), 2)
        self.assertNotEqual(self.alerts[0]['id'], self.alerts[1]['id'])
        self.assertEqual(self.alerts[0]['number'], '+12345')
        self.assertEqual(len(self.views[-1]), 2)

    def test_ended_and_reused_clcc_index_is_a_new_call(self):
        self.state.update([self.row()])
        first = self.alerts[-1]['id']
        self.state.update([])
        self.assertEqual(self.views[-1], [])
        self.state.update([self.row()])
        self.assertNotEqual(first, self.alerts[-1]['id'])

    def test_other_slot_query_does_not_erase_or_realert_existing_call(self):
        self.state.update([self.row(slot=1)], (1,))
        self.state.update([], (0,))
        self.assertEqual(len(self.state.items), 1)
        self.state.update([self.row(slot=1, state='active')], (1,))
        self.assertEqual(self.views[-1], [])
        self.assertEqual(len(self.alerts), 1)

    def test_unknown_number_update_and_daemon_restart_keep_lifetime(self):
        self.state.update([self.row(number='')])
        identifier = self.alerts[-1]['id']
        restored = module.Calls(lambda x: self.fail('duplicate alert'), lambda x: None,
                                lambda: {}, json.loads(json.dumps(self.state.items)))
        restored.update([self.row(number='+12345')])
        self.assertEqual(restored.items['0:1']['id'], identifier)
        self.assertEqual(restored.items['0:1']['number'], '+12345')

    def test_never_alert_outgoing_active_held_data_or_fax(self):
        for state in ('active', 'held', 'dialing', 'ringing-out'):
            self.state.update([self.row(state=state)])
        self.state.update([self.row(direction='outgoing')])
        self.assertEqual(self.alerts, [])
        for mode in (1, 2, 6, 9):
            self.assertEqual(module.parse_clcc(f'+CLCC: 1,1,4,{mode},0,"12345",129\nOK', 0), [])

    def test_malformed_snapshot_cannot_be_a_false_empty_list(self):
        for data in ('+CLCC: 1,1', '+CLCC: 1,2,4,0,0', '+CLCC: -1,1,4,0,0',
                     '+CLCC: 1,1,9,0,0', '+CLCC: 1,1,4,0,0,"broken',
                     '+CLCC: 1,1,4,0,0\n+CLCC: 1,1,4,0,0'):
            with self.subTest(data=data), self.assertRaises((ValueError, module.csv.Error)):
                module.parse_clcc(data, 0)

    def test_uri_normalization_rejects_control_and_private_domains(self):
        valid = {'sip:+8613800000000@ims.example;user=phone': '+8613800000000',
                 '"name" <sips:12345@ims.example>': '12345',
                 'tel:%2B44-1234;phone-context=ims': '+441234',
                 '(123) 456-789': '123456789', 'sip:anonymous@ims.example': '',
                 'sip:user:password@host': '', 'sip:123%0aINJECT@host': ''}
        for raw, expected in valid.items():
            self.assertEqual(module.caller_number(raw), expected)

    def test_physical_sources_follow_existing_product_specific_mapping(self):
        for model, cards, expected in [('f50pro', [4, 3], ['外置卡1', '外置卡2']),
                                       ('u30pro', [0, 3], ['内置卡1', '外置卡1']),
                                       ('v50', [3, 1], ['外置卡1', '内置卡2'])]:
            for slot in (0, 1):
                labels = module.slot_labels(slot, dict(model=model, supported=True, logical_cards=cards))
                self.assertEqual(labels['zh'], expected[slot])
                self.assertTrue(labels['en'] and labels['tr'])
        self.assertEqual(module.slot_labels(1, {'supported': False})['zh'], 'SIM 2')

    def test_subscribed_pretty_json_frames_and_nr0_filter(self):
        event = {'mu300.modem.urc': {'channel': 'stty_nr0', 'line': 'RING'}}
        frames = json.dumps(event, indent=2) + '\n' + json.dumps(event) + '\n'
        self.assertEqual(list(module.event_objects(io.StringIO(frames))), [event, event])
        for line in ('OK', '+CMT: 10', '+CMTI: "SM",1'):
            self.assertIsNone(module.HINT.match(line))
        for channel in ('stty_nr0','stty_nr1','stty_nr3','stty_nr6'):
            self.assertEqual(module.voice_hint({'mu300.modem.urc':dict(channel=channel,line='RING')}),channel=='stty_nr0')
        self.assertFalse(module.voice_hint({'mu300.modem.urc':dict(channel='stty_nr0',line='RING\nERROR')}))

    def test_commands_use_correct_owner_and_explicit_sim_never_nr0(self):
        for native in (False, True):
            for slot, nr in ((0, 2 if native else 1), (1, 5 if native else 4)):
                invocations = []
                def rpc(obj, method, data=None):
                    if method == 'status':
                        return dict(bridge='connected', brokers=[dict(dev=f'/dev/stty_nr{n}', dir=f'/run/owner{n}', ready=True) for n in (0, 1, 2, 4, 5)])
                    self.fail('AT must use the existing admission-waiting client, not nonblocking RPC')
                def client(args, **kwargs):
                    invocations.append((args, kwargs))
                    return '\nOK\n'
                with patch.object(module, 'call', rpc), patch.object(module.subprocess, 'check_output', client):
                    self.assertEqual(module.ModemOwner(native).snapshot(slot), [])
                self.assertEqual(len(invocations), 1)
                args, options = invocations[0]
                self.assertEqual(args, [module.AT_CLIENT, '-t', '8', f'AT+SPACTCARD={slot};+CLCC'])
                self.assertEqual(options['env']['MU300_AT_DIR'], f'/run/owner{nr}')
                self.assertEqual(options['env']['MU300_AT_DEV'], f'/dev/stty_nr{nr}')
                self.assertEqual(options['env']['MU300_AT_DAEMON_ONLY'], '1')
                self.assertEqual(options['env']['MU300_AT_LOCK_WAIT'], '8')
                self.assertEqual(options['stdin'], module.subprocess.DEVNULL)
                self.assertTrue(options['start_new_session'])

    def test_hangup_acceptance_waits_for_real_end_without_a_second_chup(self):
        rows = [self.row()]
        self.state.update(rows)
        identifier = self.alerts[-1]['id']
        owner = module.ModemOwner(True)
        results = []
        hangups = module.Hangups(owner, self.state, lambda *v: results.append(v), lambda: 0)
        with patch.object(owner, 'snapshot', side_effect=[[], rows, rows]), \
             patch.object(owner, 'command', return_value='OK') as command:
            hangups.request(identifier)
            self.assertEqual(results, [])
            self.assertIn(identifier, hangups.pending)
            self.assertEqual(self.state.items['0:1']['id'], identifier)
            hangups.request(identifier)
            command.assert_called_once_with(0, '+CHUP')
        self.state.update([])
        hangups.settle()
        self.assertEqual(results, [(identifier, True)])
        self.assertIsNone(hangups.timeout())

    def test_hangup_timeout_is_one_confirmation_not_repeated_hangup(self):
        rows = [self.row()]
        self.state.update(rows)
        identifier = self.alerts[-1]['id']
        owner = Mock()
        owner.confirmed_snapshot.return_value = rows
        results, now = [], [0]
        hangups = module.Hangups(owner, self.state, lambda *v: results.append(v), lambda: now[0])
        hangups.request(identifier)
        self.assertEqual(results, [])
        now[0] = module.AT_TIMEOUT - 1
        hangups.expire()
        self.assertEqual(results, [])
        now[0] += 1
        hangups.expire()
        self.assertEqual(results, [(identifier, False)])
        hangups.expire()
        self.assertEqual(len(results), 1)
        owner.command.assert_called_once_with(0, '+CHUP')

    def test_accepted_hangup_does_not_fail_on_one_readback_error(self):
        rows = [self.row(slot=1)]
        self.state.update(rows)
        identifier = self.alerts[-1]['id']
        owner = Mock()
        owner.confirmed_snapshot.side_effect = [rows, RuntimeError('busy')]
        results = []
        hangups = module.Hangups(owner, self.state, lambda *v: results.append(v))
        hangups.request(identifier)
        self.assertEqual(results, [])
        self.state.update([], (1,))
        hangups.settle()
        self.assertEqual(results, [(identifier, True)])
        owner.command.assert_called_once_with(1, '+CHUP')

    def test_hangup_never_targets_replaced_answered_or_ambiguous_calls(self):
        for state in ('active', 'ambiguous', 'replaced', 'ended'):
            with self.subTest(state=state):
                rows = [self.row()]
                calls = module.Calls(lambda _: True, lambda _: None, lambda: {})
                calls.update(rows)
                identifier = calls.items['0:1']['id']
                owner = Mock()
                owner.confirmed_snapshot.return_value = (
                    [self.row(state='active')] if state == 'active' else
                    rows + [self.row(index=2)] if state == 'ambiguous' else
                    [self.row(number='54321')] if state == 'replaced' else [])
                hangups = module.Hangups(owner, calls, lambda *_: None)
                if state in ('active', 'ambiguous'):
                    with self.assertRaises(RuntimeError): hangups.request(identifier)
                else:
                    hangups.request(identifier)
                owner.command.assert_not_called()

    def test_hangup_error_requires_readback_and_never_resubmits(self):
        for ended in (False, True):
            with self.subTest(ended=ended):
                calls = module.Calls(lambda _: True, lambda _: None, lambda: {})
                rows = [self.row()]
                calls.update(rows)
                identifier = calls.items['0:1']['id']
                owner, results = Mock(), []
                owner.confirmed_snapshot.side_effect = [rows, [] if ended else rows]
                owner.command.side_effect = RuntimeError('NO CARRIER or rejected')
                hangups = module.Hangups(owner, calls, lambda *v: results.append(v))
                if ended:
                    hangups.request(identifier)
                    self.assertEqual(results, [(identifier, True)])
                else:
                    with self.assertRaises(RuntimeError): hangups.request(identifier)
                    self.assertEqual(results, [])
                owner.command.assert_called_once_with(0, '+CHUP')

    def test_queue_rejection_is_not_marked_delivered(self):
        state = module.Calls(lambda x: False, lambda x: None, lambda: {})
        state.update([self.row()])
        self.assertFalse(state.items['0:1']['notified'])

    def test_dual_ring_order_does_not_flip_during_per_slot_refresh(self):
        self.state.update([self.row(slot=1)], (1,))
        self.state.update([self.row()], (0,))
        self.assertEqual([c['card'] for c in self.views[-1]], [0, 1])
        count = len(self.views)
        for _ in range(5):
            self.state.update([self.row()], (0,))
            self.state.update([self.row(slot=1)], (1,))
        self.assertEqual(len(self.views), count)

    def test_national_number_does_not_realert_but_different_caller_does(self):
        self.state.update([self.row(number='13800000000')])
        self.state.update([self.row(number='+8613800000000')])
        self.state.update([self.row(number='8613800000000')])
        self.assertEqual(len(self.alerts), 1)
        self.state.update([self.row(number='13900000000')])
        self.assertEqual(len(self.alerts), 2)

    def test_transient_empty_snapshot_confirms_once_on_same_event(self):
        owner = module.ModemOwner(True)
        rows = [self.row()]
        with patch.object(owner, 'snapshot', side_effect=[[], rows]) as query:
            self.assertEqual(owner.confirmed_snapshot(0, {'0:1': self.row()}), rows)
            self.assertEqual(query.call_count, 2)
        with patch.object(owner, 'snapshot', side_effect=[[], RuntimeError('busy')]):
            with self.assertRaises(RuntimeError):
                owner.confirmed_snapshot(0, {'0:1': self.row()})

    def test_lifetime_is_persisted_before_delivery_worker_can_run(self):
        saved = []
        def persist(items):
            saved.append(json.loads(json.dumps(items)))
        def deliver(item):
            self.assertEqual(saved[-1][item['key']]['id'], item['id'])
            self.assertFalse(saved[-1][item['key']]['notified'])
            return True
        state = module.Calls(deliver, lambda x: None, lambda: {}, persist=persist)
        state.update([self.row()])
        self.assertTrue(saved[-1]['0:1']['notified'])

    def test_default_off_and_per_slot_configuration_do_not_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(module.notification_enabled(0, root))
            (root/'config.json').write_text(json.dumps(dict(enabled=1)))
            self.assertFalse(module.notification_enabled(0, root))
            (root/'config.json').write_text(json.dumps(dict(enabled=1,call_forward_enabled=1)))
            self.assertTrue(module.notification_enabled(1, root))
            (root/'mode').write_text('per_sim')
            (root/'config.sim0.json').write_text(json.dumps(dict(enabled=1,call_forward_enabled=0)))
            (root/'config.sim1.json').write_text(json.dumps(dict(enabled=1,call_forward_enabled=1)))
            self.assertFalse(module.notification_enabled(0, root))
            self.assertTrue(module.notification_enabled(1, root))

    def test_e5_native_number_is_normalized_before_forward_and_presentation(self):
        self.state.update([dict(key='/org/freedesktop/ModemManager1/Call/12', card=1,
            direction='incoming',state='ringing-in',number='sips:%2B4412345@ims.example;user=phone')])
        self.assertEqual(self.alerts[-1]['number'], '+4412345')
        self.assertEqual(self.views[-1][0]['number'], '+4412345')


if __name__ == '__main__':
    unittest.main()
