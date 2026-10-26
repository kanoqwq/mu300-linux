"""SMS forwarding of the control panel (openwrt-luci): mu300-sms-forward queues the messages mu300-sms pools and sends
them on to a webhook, a Telegram chat, an e-mail address (msmtp) and another phone; unisoc-modem/forward is the panel's
adapter for its settings. The HTTP requests go to the ucode helper on stdin: here `ucode` is a stub that records what
came on stdin and its arguments, and answers with what the test chose. mu300-sms and msmtp are stubs too.

What matters most: forwarding is off until it is turned on; the filters; the queue is bounded and on flash, the
attempts and the log in RAM; a failure is retried with a growing wait, a refusal that cannot change is not; the
secrets (token, password, webhook URL and headers) are never in a command line, the log or a reply of the adapter."""
import json
import os
import stat
import subprocess
import time
import unittest

from helpers import TOP, ShellTest
from test_mu300dash_security import JSONFILTER

FWD = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-sms-forward'
HTTP_UC = TOP / 'openwrt' / 'luci-overlay' / 'opt' / 'mu300' / 'lib' / 'sms-forward-http.uc'
HTTP_SH = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'sms-forward-http.sh'
ADAPTER = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'forward'
DASH = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'rpcd' / 'mu300dash'

TOKEN = '123456789:AAHsecretTokenValue_0123456789'
PASSWORD = 'hunter2-mail-pass'
HOOK_URL = 'http://192.0.2.10:8099/hook?key=urlsecret'

# the stub ucode: the request (stdin) into $STUBLOG/http.N, its arguments into $STUBLOG/argv.log; it answers with the
# first line of $STUBLOG/answers (consumed), else "status 200". Like the helper, it adds the reply's body to a 2xx
# only when MU300_FWD_ANSWER asks for it ($STUBLOG/answers.env says, per request, whether it did): the bytes of
# $STUBLOG/answers.reply.N, else answers.reply - all of them, the cut to the first bytes is then the forwarder's
UCODE = r'''
import os, sys
log = os.environ['STUBLOG']
n = len([f for f in os.listdir(log) if f.startswith('http.')])
open(os.path.join(log, 'http.%d' % n), 'wb').write(sys.stdin.buffer.read())
open(os.path.join(log, 'argv.log'), 'a').write(' '.join(sys.argv) + '\n')
a = os.path.join(log, 'answers')
lines = open(a).read().splitlines() if os.path.exists(a) else []
line = lines[0] if lines else 'status 200'
if lines:
    open(a, 'w').write(''.join(l + '\n' for l in lines[1:]))
ask = os.environ.get('MU300_FWD_ANSWER')
open(a + '.env', 'a').write((ask or '-') + '\n')
sys.stdout.buffer.write(line.encode() + b'\n')
if ask and line.startswith('status 2'):
    for f in (a + '.reply.%d' % n, a + '.reply'):
        if os.path.exists(f):
            sys.stdout.buffer.write(open(f, 'rb').read())
            break
'''


def pool_msg(pool, mid, sender, text, direction='mt', scts='2026-10-10 12:00:00'):
    d = pool / 'msg'
    d.mkdir(parents=True, exist_ok=True)
    who = 'from' if direction == 'mt' else 'to'
    f = d / f'{mid:06d}'
    f.write_text(f'id: {mid:06d}\ndir: {direction}\nstatus: unread\n{who}: {sender}\nscts: {scts}\nreceived: 1\n'
                 f'sim_index: 1\ncoding: gsm\nfingerprint: fp{mid}\nlength: 1\n\n{text}\n', encoding='utf-8')
    return f


class Forwarder(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'sms-forward.conf'
        self.state = self.tmp / 'state'
        self.run_dir = self.tmp / 'run'
        self.pool = self.tmp / 'pool'
        (self.stubs / 'ucode.py').write_text(UCODE)
        self.stub('ucode', f'exec python3 "{self.stubs}/ucode.py" "$@"')
        # mu300-sms: the arguments and stdin of a send, and "sent (reference 7)"
        self.stub('mu300-sms', 'printf "%s\\n" "$*" >> "$STUBLOG/sms-argv.log"; cat > "$STUBLOG/sms-stdin.$$"; '
                               '[ -f "$STUBLOG/sms-fail" ] && { cat "$STUBLOG/sms-fail" >&2; exit 1; }; echo "sent (reference 7)"')
        self.stub('hostname', 'echo mu300')

    def fresh(self):
        for p in (self.state, self.run_dir):
            subprocess.run(['rm', '-rf', str(p)])
        for f in self.tmp.iterdir():
            if f.name.startswith(('http.', 'argv.log', 'answers', 'sms-')):
                f.unlink()

    def write_conf(self, **kv):
        lines = []
        for k, v in kv.items():
            for one in (v if isinstance(v, list) else [v]):
                lines.append(f'{k}={one}')
        self.conf.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        os.chmod(self.conf, 0o600)

    def fwd(self, shell, *args, **env):
        e = dict(MU300_FWD_CONF=self.conf, MU300_FWD_STATE=self.state, MU300_FWD_RUN=self.run_dir,
                 MU300_SMS_POOL=self.pool, MU300_SMS_BIN=self.stubs / 'mu300-sms', MU300_FWD_HTTP=HTTP_UC,
                 MU300_FWD_NO_WORKER=1, MU300_FWD_BACKOFF=1)
        e.update(env)
        return self.script(shell, FWD, *args, **e)

    def queue(self):
        q = self.state / 'queue'
        return [l.split('\t') for l in q.read_text().splitlines()] if q.exists() else []

    def log(self):
        p = self.run_dir / 'log'
        return [l.split('\t') for l in p.read_text().splitlines()] if p.exists() else []

    def requests(self):
        out = []
        for f in sorted(self.tmp.glob('http.*'), key=lambda p: int(p.suffix[1:])):
            head, _, body = f.read_bytes().decode('utf-8').partition('\n\n')
            lines = head.split('\n')
            out.append((lines[0], dict(l.split(': ', 1) for l in lines[1:] if l), body))
        return out

    def answers(self, *lines):
        (self.tmp / 'answers').write_text(''.join(l + '\n' for l in lines))

    # ------------------------------------------------------------------ enqueue and filters
    def test_off_unless_turned_on(self):
        pool_msg(self.pool, 1, '+905551112233', 'hello')
        for shell in self.each_shell():
            self.fresh()
            self.conf.unlink(missing_ok=True)
            r = self.fwd(shell, 'enqueue', '1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.queue(), [])
            self.write_conf(enabled=0, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1')
            self.assertEqual(self.queue(), [])
            # on, but no target that is on and complete
            self.write_conf(enabled=1, webhook=0, webhook_url=HOOK_URL, telegram=1, telegram_token=TOKEN)
            self.fwd(shell, 'enqueue', '1')
            self.assertEqual(self.queue(), [])
            self.assertFalse(self.state.exists() and any(self.state.iterdir()))

    def test_enqueue_one_entry_per_target_and_received_only(self):
        f1 = pool_msg(self.pool, 1, '+905551112233', 'hello')
        pool_msg(self.pool, 2, '+905551112233', 'sent by me', direction='mo')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, telegram=1, telegram_token=TOKEN,
                            telegram_chat='42', sms=1, sms_number='+905550000000')
            r = self.fwd(shell, 'enqueue', '1', '2', '000001', 'x;rm', '99')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.queue(), [['webhook', str(f1)], ['telegram', str(f1)], ['sms', str(f1)]])
            # the queue is root's only, on flash; the RAM directory too
            self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(self.run_dir.stat().st_mode), 0o700)

    def test_filters(self):
        msgs = {1: ('+90 555 111 22 33', 'Your code is 1234'), 2: ('ADANA BLD', 'Kargonuz yolda'),
                3: ('BANK', 'your CODE: 99'), 4: ('+905559999999', 'code 1'), 5: ('BANKX', 'nothing')}
        for mid, (s, t) in msgs.items():
            pool_msg(self.pool, mid, s, t)
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, allow=['+905551112233', 'bank*', 'ADANA BLD'],
                            deny=['bankx'], keyword=['code'])
            self.fwd(shell, 'enqueue', *map(str, msgs))
            queued = sorted(int(f.rsplit('/', 1)[1]) for _, f in self.queue())
            self.assertEqual(queued, [1, 3])
            why = {int(l[1]): l[4] for l in self.log() if l[3] == 'skipped'}
            self.assertEqual(why, {2: 'no keyword', 4: 'sender not allowed', 5: 'sender denied'})
            # nothing of the sender or the text is in the log
            text = (self.run_dir / 'log').read_text()
            for s, t in msgs.values():
                self.assertNotIn(t, text)
                self.assertNotIn(s.replace(' ', ''), text.replace(' ', ''))

    def test_the_queue_is_bounded(self):
        for mid in (1, 2, 3):
            pool_msg(self.pool, mid, '+905551112233', f'm{mid}')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1', '2', MU300_FWD_QUEUE_MAX=2)
            self.fwd(shell, 'enqueue', '3', MU300_FWD_QUEUE_MAX=2)
            self.assertEqual([f.rsplit('/', 1)[1] for _, f in self.queue()], ['000002', '000003'])
            self.assertIn(['1', 'webhook', 'dropped', 'queue full'], [l[1:] for l in self.log()])

    # ------------------------------------------------------------------ delivery
    def test_webhook_json_and_form(self):
        pool_msg(self.pool, 7, '+905551112233', 'Merhaba "dünya" \\ {x}\nikinci satır')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL,
                            webhook_header=['Authorization: Bearer hdrsecret', 'X-Device: u30'],
                            template='{text}\\n-- {sender} @ {time} on {device} {nope} \\\\o/')
            self.fwd(shell, 'enqueue', '7')
            r = self.fwd(shell, 'run')
            self.assertEqual(r.returncode, 0, r.stderr)
            [(url, headers, body)] = self.requests()
            self.assertEqual(url, HOOK_URL)
            self.assertEqual(headers, {'Content-Type': 'application/json; charset=utf-8',
                                       'Authorization': 'Bearer hdrsecret', 'X-Device': 'u30'})
            doc = json.loads(body)
            dev = doc['device']
            self.assertEqual(doc, {'sender': '+905551112233', 'time': '2026-10-10 12:00:00',
                                   'text': 'Merhaba "dünya" \\ {x}\nikinci satır', 'device': dev,
                                   'message': f'Merhaba "dünya" \\ {{x}}\nikinci satır\n-- +905551112233 @ '
                                              f'2026-10-10 12:00:00 on {dev} {{nope}} \\o/'})
            self.assertTrue(dev)
            self.assertEqual(self.queue(), [])
            self.assertEqual([l[1:4] for l in self.log()], [['7', 'webhook', 'sent']])
            # form: the same fields, URL-encoded
            self.fresh()
            for f in self.tmp.glob('http.*'):
                f.unlink()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_format='form', template='{sender}: {text}')
            self.fwd(shell, 'enqueue', '7')
            self.fwd(shell, 'run')
            [(_, headers, body)] = self.requests()
            self.assertEqual(headers['Content-Type'], 'application/x-www-form-urlencoded')
            from urllib.parse import parse_qs
            q = {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}
            self.assertEqual(q['text'], 'Merhaba "dünya" \\ {x}\nikinci satır')
            self.assertEqual(q['message'], '+905551112233: ' + q['text'])
            self.assertEqual(set(q), {'sender', 'time', 'text', 'device', 'message'})

    # ------------------------------------------------------------------ a body of the user's own (issue #171)
    # a text and a sender that would break a body, or run something, if either were pasted in or evaluated
    HOSTILE_TEXT = ('Merhaba "dünya" \\ {x} {sender} {message}\nikinci satır\tsekme \x01\x7f € '
                    '$(touch "$STUBLOG/pwned") `touch "$STUBLOG/pwned"` %s \\n ","admin":true,"x":"')
    HOSTILE_SENDER = 'A"B\\C&d=e {text}'

    def deliver_one(self, shell, mid=8, **conf):
        self.fresh()
        self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, template='{sender}: {text}', **conf)
        self.fwd(shell, 'enqueue', str(mid))
        r = self.fwd(shell, 'run')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout + r.stderr, '')
        self.assertFalse((self.tmp / 'pwned').exists())
        return self.requests()

    def test_custom_json_body_values_are_json_escaped(self):
        pool_msg(self.pool, 8, self.HOSTILE_SENDER, self.HOSTILE_TEXT)
        # as stored: \n a newline, \\ a backslash - so the body below has a line break and the JSON text "a\\b"
        stored = ('{"msg_type":"text",\\n "content":{"text":"{message}"},"from":"{sender}","at":"{time}",'
                  '"dev":"{device}","raw":"{text}","twice":"{sender}{sender}","keep":"{other} {text",'
                  '"esc":"a\\\\\\\\b","sh":"$(touch \\\\"$STUBLOG/pwned\\\\")"}')
        for shell in self.each_shell():
            [(url, headers, body)] = self.deliver_one(shell, webhook_body=stored)
            self.assertEqual(url, HOOK_URL)
            self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
            self.assertIn('{"msg_type":"text",\n "content"', body)
            doc = json.loads(body)          # still one valid document, whatever the message held
            self.assertEqual(set(doc), {'msg_type', 'content', 'from', 'at', 'dev', 'raw', 'twice', 'keep', 'esc', 'sh'})
            self.assertEqual(doc['raw'], self.HOSTILE_TEXT)
            self.assertEqual(doc['from'], self.HOSTILE_SENDER)
            self.assertEqual(doc['twice'], self.HOSTILE_SENDER * 2)
            # one pass: the {sender} and {message} inside the text, and the {text} inside the sender, stay as they are
            self.assertEqual(doc['content']['text'], f'{self.HOSTILE_SENDER}: {self.HOSTILE_TEXT}')
            self.assertEqual(doc['at'], '2026-10-10 12:00:00')
            self.assertTrue(doc['dev'])
            self.assertEqual(doc['keep'], '{other} {text')
            self.assertEqual(doc['esc'], 'a\\b')
            self.assertEqual(doc['sh'], '$(touch "$STUBLOG/pwned")')
            self.assertEqual([l[1:4] for l in self.log()], [['8', 'webhook', 'sent']])
            # nothing of the message or the body in the log or a command line
            for f in ('argv.log',):
                self.assertNotIn('Merhaba', (self.tmp / f).read_text())
            self.assertNotIn('Merhaba', (self.run_dir / 'log').read_text())

    def test_the_pages_body_presets_are_what_the_services_take(self):
        """Issue #259: the placeholder was Feishu's body, which WeCom refuses (errcode 40008) behind an HTTP 200. The
        page's body presets: WeCom/DingTalk and Feishu/Lark, each a JSON body the forwarder fills into one valid
        document of that service's shape, with the answer check that service needs (one of the check presets)."""
        import re
        view = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/forward.js').read_text()
        bodies = dict(re.findall(r"(\w+): \[ '(\{[^']*\})', '[^']*' \]", view))
        checks = dict(re.findall(r"(\w+): \[ '\{[^']*\}', '([^']*)' \]", view))
        self.assertEqual(set(bodies), {'wecom', 'feishu'})
        presets = re.findall(r"E\('option', \{ 'value': '([^']*=[^']*)' \}", view)
        self.assertEqual(checks, {'wecom': 'errcode=0', 'feishu': 'code=0'})
        self.assertTrue(set(checks.values()) <= set(presets))
        self.assertIn("E('option', { 'value': 'wecom' }, [ 'WeCom, DingTalk' ])", view)
        self.assertIn("E('option', { 'value': 'feishu' }, [ 'Feishu, Lark' ])", view)
        self.assertNotIn('\'{"msg_type":"text","content":{"text":"{message}"}}\')', view)   # not the placeholder
        self.assertIn("format.value = 'json';", view)
        self.assertIn('if (!expect.value.trim()) expect.value = b[1];', view)   # a check of the user's is kept
        pool_msg(self.pool, 8, self.HOSTILE_SENDER, self.HOSTILE_TEXT)
        message = f'{self.HOSTILE_SENDER}: {self.HOSTILE_TEXT}'
        want = {'wecom': {'msgtype': 'text', 'text': {'content': message}},
                'feishu': {'msg_type': 'text', 'content': {'text': message}}}
        for shell in self.each_shell():
            for name, body in bodies.items():
                with self.subTest(preset=name):
                    [(_url, headers, sent)] = self.deliver_one(shell, webhook_body=body, webhook_format='json')
                    self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
                    self.assertEqual(json.loads(sent), want[name])

    def test_custom_form_and_text_bodies(self):
        from urllib.parse import parse_qs
        pool_msg(self.pool, 8, self.HOSTILE_SENDER, self.HOSTILE_TEXT)
        for shell in self.each_shell():
            # form: every value percent-encoded, so a & or = in a value cannot add a field
            [(_, headers, body)] = self.deliver_one(shell, webhook_format='form',
                                                     webhook_body='title={sender}&body={message}&device_key=abc&t={time}')
            self.assertEqual(headers['Content-Type'], 'application/x-www-form-urlencoded')
            q = parse_qs(body, keep_blank_values=True, strict_parsing=True)
            self.assertEqual(q, {'title': [self.HOSTILE_SENDER], 'device_key': ['abc'], 't': ['2026-10-10 12:00:00'],
                                 'body': [f'{self.HOSTILE_SENDER}: {self.HOSTILE_TEXT}']})
            # text: the values as they are
            [(_, headers, body)] = self.deliver_one(shell, webhook_format='text', webhook_body='[{device}] {sender}\\n{text}')
            self.assertEqual(headers['Content-Type'], 'text/plain; charset=utf-8')
            dev = body[1:body.index(']')]
            self.assertEqual(body, f'[{dev}] {self.HOSTILE_SENDER}\n{self.HOSTILE_TEXT}')
            # text with no body of its own: the message (the template filled in)
            [(_, headers, body)] = self.deliver_one(shell, webhook_format='text')
            self.assertEqual(headers['Content-Type'], 'text/plain; charset=utf-8')
            self.assertEqual(body, f'{self.HOSTILE_SENDER}: {self.HOSTILE_TEXT}')

    def test_an_empty_custom_body_is_the_standard_body(self):
        pool_msg(self.pool, 8, '+905551112233', 'hello')
        for shell in self.each_shell():
            [(_, _, plain)] = self.deliver_one(shell)
            [(_, _, empty)] = self.deliver_one(shell, webhook_body='')
            self.assertEqual(plain, empty)
            doc = json.loads(empty)
            self.assertEqual(set(doc), {'sender', 'time', 'text', 'device', 'message'})
            self.assertEqual((doc['sender'], doc['text'], doc['message']), ('+905551112233', 'hello', '+905551112233: hello'))
            [(_, _, form)] = self.deliver_one(shell, webhook_format='form', webhook_body='')
            self.assertTrue(form.startswith('sender=%2B905551112233&time='), form)

    def test_send_test_uses_the_custom_body(self):
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=0, webhook=1, webhook_url=HOOK_URL, webhook_body='{"text":"{message}","who":"{sender}"}')
            r = self.fwd(shell, 'test')
            self.assertEqual(r.returncode, 0, r.stderr)
            [(_, _, body)] = self.requests()
            doc = json.loads(body)
            self.assertEqual(doc['who'], 'MU300')
            self.assertIn('Test message from the control panel', doc['text'])
            self.assertEqual([l[1:4] for l in self.log()], [['test', 'webhook', 'sent']])

    def test_a_body_too_large_is_not_sent_and_not_retried(self):
        pool_msg(self.pool, 8, '+905551112233', 'x' * 400)
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_body='{"a":"{text}{text}{text}"}')
            self.fwd(shell, 'enqueue', '8')
            self.fwd(shell, 'run', MU300_FWD_BODY_MAX=1000)
            self.assertEqual(self.requests(), [])
            self.assertEqual(self.queue(), [])
            self.assertEqual(self.log()[-1][1:], ['8', 'webhook', 'failed', 'the webhook body is too large'])
            # under the limit it goes
            self.fwd(shell, 'enqueue', '8')
            self.fwd(shell, 'run', MU300_FWD_BODY_MAX=2000)
            [(_, _, body)] = self.requests()
            self.assertEqual(json.loads(body), {'a': 'x' * 1200})

    def test_the_body_template_is_never_evaluated(self):
        fwd = FWD.read_text()
        self.assertNotIn('eval', fwd.replace('never passes through a shell, eval or awk -v', '')
                         .replace('passes through a shell, eval or awk -v', ''))
        # the template and the values reach awk in its environment, never as program text or -v
        start = fwd.index('\nfill() {')
        body = fwd[start:fwd.index('\n}\n', start)]
        self.assertIn('T=$1 M=$2 F=$3 D=$4 X=$5 V=$6 G=${7:-} N=$# awk', body)
        self.assertNotIn('awk -v', body)
        self.assertNotIn('"$1"', body)

    # ------------------------------------------------------------------ the answer check (webhook_expect) and the log
    # all that ever leaves answer_verdict: three verdicts, and with "differs" at most a number or true, false, null
    SAFE_VERDICT = r'answer (ok|none|differs( (true|false|null|-?[0-9]{1,16}))?)'

    def verdict(self, shell, reply, key='errcode', want='0'):
        """answer_verdict on what the helper writes: the status line, then REPLY (bytes); the verdict line."""
        r = subprocess.run(shell + ['-c', f'. "{self.tmp}/lib.sh"; answer_verdict "$1" "$2"', 'sh', key, want],
                           input=b'status 200\n' + reply, capture_output=True, env=self.env(), timeout=60)
        self.assertEqual((r.returncode, r.stderr), (0, b''))
        lines = r.stdout.split(b'\n')
        # the status line, one verdict line of a fixed shape, and nothing else, whatever the reply was
        self.assertEqual((lines[0], lines[2:]), (b'status 200', [b'']), reply[:60])
        self.assertRegex(lines[1].decode('ascii'), '^' + self.SAFE_VERDICT + '$')
        return lines[1].decode('ascii')[len('answer '):]

    def test_answers_that_pass(self):
        self.lib_from_script()
        deep = '{"a":' * 300 + '1' + '}' * 300
        cases = [
            ('errcode', '0', '{"errcode":0,"errmsg":"ok"}'),                       # DingTalk, WeCom
            ('errcode', '0', '{"errmsg" : "ok" ,\r\n\t"errcode" : 0 }'),
            ('errcode', '0', '{"errcode":"0"}'),                                   # a string by its content
            ('code', '0', '{"code":0,"msg":"success","data":{}}'),                 # Feishu, Lark
            ('StatusCode', '0', '{"StatusCode":0,"StatusMessage":"success","code":0,"data":{},"msg":"success"}'),
            ('code', '200', '{"code":200,"message":"success","timestamp":1791000000}'),   # Bark
            ('ok', 'true', '{"ok":true,"result":{"message_id":5,"ok":false,"text":"\\"ok\\":false"}}'),
            ('errcode', '0', '﻿ \n{"errcode":0}'),
            ('errcode', '0', '{"errcode":0}\n{"errcode":1}'),                      # only the first object is the answer
            ('errcode', '0', '{\n  "errmsg": "line\nbreak \\\\",\n  "errcode": 0\n}\n'),
            ('errcode', '0', '{"a":"\\\\","b":"\\\\\\"","errcode":0}'),
            ('errcode', '0', '{"errcode":0,"pad":"' + 'x' * 100000 + '"}'),        # cut after the key: still a verdict
            ('errcode', '0', '{"deep":' + deep + ',"errcode":0}'),
            ('errcode', '0', '{"errcode":0,"errcode":0}'),
            ('errcode', '0', '{"errcode":{"errcode":1},"errcode":0}'),             # an object under the key is not a value
            ('ok', 'true', '{"ok":"true"}'),
            ('status', 'success', '{"status":"success"}'),
            ('errcode', '-1', '{"errcode":-1}'),
        ]
        for shell in self.each_shell():
            for key, want, reply in cases:
                with self.subTest(reply=reply[:50]):
                    self.assertEqual(self.verdict(shell, reply.encode('utf-8'), key, want), 'ok')

    def test_answers_that_fail(self):
        self.lib_from_script()
        cases = [
            ('errcode', '0', '{"errcode":310000,"errmsg":"keywords not in content"}', 'differs 310000'),
            ('code', '0', '{"code":19024,"msg":"Key Words Not Found"}', 'differs 19024'),
            ('ok', 'true', '{"ok":false,"error":"invalid_auth"}', 'differs false'),
            ('errcode', '0', '{"errcode":null}', 'differs null'),
            ('errcode', '0', '{"errcode":-5}', 'differs -5'),
            ('errcode', '0', '{"errcode":"42"}', 'differs 42'),
            ('errcode', '0', '{"errcode":1234567890123456}', 'differs 1234567890123456'),
            ('errcode', '0', '{"errcode":0,"errcode":1}', 'differs 1'),             # there twice: both must be right
            ('errcode', '0', '{"errcode":1,"errcode":0}', 'differs 1'),
            # another value that is no whole number, true, false or null: that it differs, and nothing of it
            ('errcode', '0', '{"errcode":12345678901234567}', 'differs'),
            ('errcode', '0', '{"errcode":0.0}', 'differs'),                        # text, not arithmetic
            ('errcode', '0', '{"errcode":1e3}', 'differs'),
            ('errcode', '0', '{"errcode":""}', 'differs'),
            ('errcode', '0', '{"errcode":"0 "}', 'differs'),
            ('errcode', '0', '{"errcode":"secret"}', 'differs'),
            ('errcode', '0', '{"errcode":"true"}', 'differs'),
            ('errcode', '0', '{"errcode":"-"}', 'differs'),
            ('errcode', '0', '{"errcode":True}', 'differs'),
            ('errcode', '0', '{"errcode":"' + 'A' * 100 + '"}', 'differs'),
            ('errcode', '0', '{"errcode":"$(touch pwned)`touch pwned`;touch pwned|\'\\"\\\\\n<b>&é"}', 'differs'),
            # no scalar under that key at the top: one verdict for all of it
            ('errcode', '0', '{}', 'none'),
            ('errcode', '0', '{"errcode":{"errcode":0}}', 'none'),
            ('errcode', '0', '{"errcode":[0]}', 'none'),
            ('errcode', '0', '{"data":{"errcode":0},"list":[{"errcode":0}]}', 'none'),
            ('errcode', '0', '{"x":"errcode","y":"\\"errcode\\":0","z":"errcode:0"}', 'none'),
            ('errcode', '0', '{"ERRCODE":0,"errcode ":0," errcode":0,"err\\u0063ode":0}', 'none'),
            ('errcode', '0', '{"deep":' + '{"a":' * 300 + '{"errcode":0}' + '}' * 300 + '}', 'none'),
            ('errcode', '0', '{"deep":' + '[' * 100000, 'none'),
            ('errcode', '0', '{"pad":"' + 'x' * 1000000 + '","errcode":0}', 'none'),      # after the cut: not read
            ('errcode', '0', '{"errcode":31', 'none'),                               # a value the cut may have shortened
            ('errcode', '0', '{"errcode":"0', 'none'),
            ('errcode', '0', '{"errcode"', 'none'),
            ('errcode', '0', '{"errcode":', 'none'),
            ('errcode', '0', '', 'none'),
            ('errcode', '0', ' \r\n\t\n', 'none'),
            ('errcode', '0', 'ok', 'none'),
            ('errcode', '0', '0', 'none'),
            ('errcode', '0', '"errcode":0', 'none'),
            ('errcode', '0', '[{"errcode":0}]', 'none'),
            ('errcode', '0', '<html><body>{"errcode":0}</body></html>', 'none'),
            ('errcode', '0', 'x' * 1000000, 'none'),
            # the rule is text: nothing in it is a pattern or a program
            ('.*', '.*', '{"abc":"x"}', 'none'),
            ('a', '.', '{"a":"x"}', 'differs'),
            ('a', '0" || "1', '{"a":1}', 'differs 1'),
            ('a"} END{system("touch pwned")} #', '0', '{"a":1}', 'none'),
        ]
        for shell in self.each_shell():
            for key, want, reply, verdict in cases:
                with self.subTest(reply=reply[:50]):
                    self.assertEqual(self.verdict(shell, reply.encode('utf-8'), key, want), verdict)
            # bytes that are no text: one verdict line of the fixed shape (verdict() checks that), never "ok"
            for raw in (bytes(range(256)) * 40, b'{' + bytes(range(256)) * 40, b'\x00' * 5000,
                        b'{"errcode":"\xff\xfe\x00\x01\x1b[2J"}', b'{"errcode":1,"x":"\x00","errcode":0}'):
                with self.subTest(raw=raw[:20]):
                    self.assertNotEqual(self.verdict(shell, raw), 'ok')
            self.assertFalse(list(self.tmp.rglob('pwned')))
            self.assertFalse((TOP / 'tests' / 'pwned').exists())

    def test_the_form_of_a_rule(self):
        self.lib_from_script()
        good = ['errcode=0', 'code=200', 'ok=true', 'StatusCode=0', '_k=-1', 'a_b1=success', 'k' * 32 + '=' + '9' * 16,
                'k=-' + '9' * 16, 'k=' + 'a' * 16]
        bad = ['', 'errcode', 'errcode=', '=0', 'a=b=c', 'err code=0', 'errcode=0 ', 'errcode=$(id)', 'errcode=`id`',
               'errcode=0;id', 'a.b=0', 'é=0', 'errcode=é', 'errcode="0"', 'errcode=0\nx', 'k' * 33 + '=0',
               'data.errcode=0', '*=0', 'errcode=*', 'a-b=0', '1k=0', 'v=1.0', 'v=+1', 'v=0x10', 'k=True', 'k=a1',
               'k=-', 'k=--1', 'k=1-', 'k=' + '9' * 17, 'k=-' + '9' * 17, 'k=' + 'a' * 17]
        for shell in self.each_shell():
            for rule, want in [(r, 0) for r in good] + [(r, 1) for r in bad]:
                r = subprocess.run(shell + ['-c', f'. "{self.tmp}/lib.sh"; expect_ok "$1"', 'sh', rule],
                                   capture_output=True, env=self.env())
                self.assertEqual((r.returncode, r.stdout + r.stderr), (want, b''), rule)

    def reply(self, text, n=None):
        """What the stub helper writes after its status line when it is asked for the answer (request N, or every)."""
        (self.tmp / ('answers.reply' + ('' if n is None else f'.{n}'))).write_bytes(
            text if isinstance(text, bytes) else text.encode('utf-8'))

    def asked(self):
        """Per request: what MU300_FWD_ANSWER was for the helper ('-': not set, it passes on the status alone)."""
        return (self.tmp / 'answers.env').read_text().split()

    def test_a_service_error_in_a_200_is_retried_until_the_answer_is_right(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_expect='errcode=0')
            self.fwd(shell, 'enqueue', '1')
            self.reply('{"errcode":130101,"errmsg":"send too fast"}', 0)
            self.reply('{"errcode":0,"errmsg":"ok"}', 1)
            r = self.fwd(shell, 'run')
            self.assertEqual((r.returncode, r.stdout + r.stderr), (0, ''))
            self.assertEqual([l[2:] for l in self.log()], [
                ['webhook', 'retry', 'HTTP 200, the service answered errcode=130101, attempt 1, next in 1 s'],
                ['webhook', 'sent', 'HTTP 200, errcode=0']])
            self.assertEqual(len(self.requests()), 2)
            self.assertEqual(self.asked(), ['4096', '4096'])
            self.assertEqual(self.queue(), [])
            # and given up like any other failure
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_expect='errcode=0')
            self.fwd(shell, 'enqueue', '1')
            self.reply('{"errcode":310000,"errmsg":"sign not match"}')
            self.fwd(shell, 'run', MU300_FWD_ATTEMPTS=2)
            self.assertEqual([l[3:] for l in self.log()], [
                ['retry', 'HTTP 200, the service answered errcode=310000, attempt 1, next in 1 s'],
                ['failed', 'HTTP 200, the service answered errcode=310000, gave up after 2 attempts']])
            self.assertEqual(self.queue(), [])

    def test_an_answer_the_rule_does_not_fit_fails_and_is_not_sent_again(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            # one message for all of them: the log says no more about an answer than that the value is not there
            detail = 'HTTP 200, no errcode in the answer'
            for reply in ('{"code":0,"msg":"success"}', '', 'ok', '{"errcode":{"a":1}}', '{"errcode":[0]}',
                          '{"pad":"' + 'x' * 5000 + '","errcode":0}'):
                self.fresh()
                self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_expect='errcode=0')
                self.fwd(shell, 'enqueue', '1')
                self.reply(reply)
                self.fwd(shell, 'run')
                self.assertEqual([l[3:] for l in self.log()], [['failed', detail]])
                self.assertEqual((len(self.requests()), self.queue()), (1, []))

    def test_without_a_rule_any_2xx_is_a_delivery_and_the_answer_is_not_asked_for(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            for conf in ({}, {'webhook_expect': ''}):
                self.fresh()
                self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, telegram=1, telegram_token=TOKEN,
                                telegram_chat='42', **conf)
                self.fwd(shell, 'enqueue', '1')
                self.reply('{"errcode":310000,"ok":false}')
                self.fwd(shell, 'run')
                self.assertEqual([l[2:] for l in self.log()], [['webhook', 'sent', 'HTTP 200'], ['telegram', 'sent', 'HTTP 200']])
                self.assertEqual(self.asked(), ['-', '-'])
            # with a rule: the webhook's answer only, never Telegram's
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, telegram=1, telegram_token=TOKEN,
                            telegram_chat='42', webhook_expect='errcode=0')
            self.fwd(shell, 'enqueue', '1')
            self.reply('{"errcode":0}')
            self.fwd(shell, 'run')
            self.assertEqual([l[2:] for l in self.log()], [['webhook', 'sent', 'HTTP 200, errcode=0'], ['telegram', 'sent', 'HTTP 200']])
            self.assertEqual(self.asked(), ['4096', '-'])

    def test_the_http_status_still_decides_first(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_expect='errcode=0')
            self.fwd(shell, 'enqueue', '1')
            self.answers('status 500', 'error timeout', 'status 401')
            self.reply('{"errcode":0}')
            self.fwd(shell, 'run')
            self.assertEqual([l[3:] for l in self.log()], [['retry', 'HTTP 500, attempt 1, next in 1 s'],
                                                           ['retry', 'timeout, attempt 2, next in 2 s'], ['failed', 'HTTP 401']])

    def test_send_test_applies_the_rule(self):
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=0, webhook=1, webhook_url=HOOK_URL, webhook_expect='code=0')
            self.reply('{"code":19024,"msg":"Key Words Not Found"}')
            r = self.fwd(shell, 'test')
            self.assertEqual(r.returncode, 1)
            self.reply('{"code":0,"msg":"success","data":{}}')
            r = self.fwd(shell, 'test')
            self.assertEqual(r.returncode, 0)
            self.reply('{"StatusCode":0}')
            r = self.fwd(shell, 'test')
            self.assertEqual(r.returncode, 1)
            self.assertEqual([l[1:] for l in self.log()], [
                ['test', 'webhook', 'failed', 'HTTP 200, the service answered code=19024'],
                ['test', 'webhook', 'sent', 'HTTP 200, code=0'],
                ['test', 'webhook', 'failed', 'HTTP 200, no code in the answer']])

    def test_nothing_of_an_answer_is_kept_but_the_verdict(self):
        """Whatever the service answers, the log gets the verdict - with a number or true, false, null when that is
        what the service put under the key, never a text - and no file anywhere gets the answer."""
        pool_msg(self.pool, 1, '+905551112233', 'x')
        mark = 'answermark'
        replies = ['{"errcode":7,"errmsg":"%s $(touch pwned) `touch pwned`","token":"%s"}' % (mark, mark),
                   '{"errcode":"%s"}' % (mark * 10 + '\t\n\x1b[31m";touch pwned;"'),
                   '{"errcode":"%s"}' % mark, '{"errcode":"hunter2"}', '{"errcode":"0123456789abcdef"}',
                   '{"errmsg":"%s"}' % mark, mark, '{"errcode":0,"secret":"%s"}' % mark]
        other, none = 'HTTP 200, the service answered another errcode', 'HTTP 200, no errcode in the answer'
        details = ['HTTP 200, the service answered errcode=7', other, other, other, other, none, none, 'HTTP 200, errcode=0']
        for shell in self.each_shell():
            for reply, detail in zip(replies, details):
                self.fresh()
                self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_expect='errcode=0')
                self.fwd(shell, 'enqueue', '1')
                self.reply(reply)
                r = self.fwd(shell, 'run', MU300_FWD_ATTEMPTS=1)
                self.assertEqual((r.returncode, r.stdout + r.stderr), (0, ''))
                self.assertEqual(self.log()[-1][4].split(', gave up')[0], detail)
                kept = b''
                for f in list(self.run_dir.rglob('*')) + list(self.state.rglob('*')) + [self.conf]:
                    if f.is_file():
                        kept += f.read_bytes()
                for text in (mark, 'hunter2', '0123456789abcdef'):
                    self.assertNotIn(text.encode(), kept)
                self.assertNotIn(b'pwned', kept)
                self.assertNotIn(b'\x1b', kept)
                self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()), ['log', 'retry'])
            self.assertFalse(list(self.tmp.rglob('pwned')))
            self.assertFalse((TOP / 'tests' / 'pwned').exists())

    def test_a_rule_that_is_not_key_value_sends_nothing(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            for rule in ('errcode=$(touch pwned)', 'errcode', 'a=b=c', 'errcode=0;touch pwned', '`touch pwned`=0',
                         'a-b=0', 'errcode=1.0', 'errcode=Secret', 'errcode=' + '1' * 17):
                self.fresh()
                self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL, webhook_expect=rule)
                self.fwd(shell, 'enqueue', '1')
                self.reply('{"errcode":0}')
                r = self.fwd(shell, 'run')
                self.assertEqual((r.returncode, r.stdout + r.stderr), (0, ''))
                self.assertEqual([l[3:] for l in self.log()], [['failed', 'the answer check is not KEY=VALUE']])
                self.assertEqual((self.requests(), self.queue()), ([], []))
            self.assertFalse(list(self.tmp.rglob('pwned')))
            self.assertFalse((TOP / 'tests' / 'pwned').exists())

    def test_the_gate_of_the_panels_test_button(self):
        for shell in self.each_shell():
            self.fresh()
            self.assertEqual(self.fwd(shell, 'test-gate').returncode, 0)
            for _ in range(3):
                r = self.fwd(shell, 'test-gate')
                self.assertEqual((r.returncode, r.stdout + r.stderr), (1, ''))
            # open again once the gap is over; a note that is no time, or one from the future, does not keep it shut
            time.sleep(1.2)
            self.assertEqual(self.fwd(shell, 'test-gate', MU300_FWD_TEST_GAP=1).returncode, 0)
            self.assertEqual(self.fwd(shell, 'test-gate', MU300_FWD_TEST_GAP=1).returncode, 1)
            for note in ('x;id\n', '', str(int(time.time()) + 86400) + '\n'):
                (self.run_dir / 'test.at').write_text(note)
                self.assertEqual(self.fwd(shell, 'test-gate').returncode, 0, note)
                self.assertEqual(self.fwd(shell, 'test-gate').returncode, 1, note)
            self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()), ['test.at'])
            # the command line's own test is not gated
            self.write_conf(enabled=0, webhook=1, webhook_url=HOOK_URL)
            self.assertEqual(self.fwd(shell, 'test').returncode, 0)
            self.assertEqual(self.fwd(shell, 'test').returncode, 0)

    def test_log_clear_empties_the_log_and_follows_no_link(self):
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=0, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'test')
            self.assertEqual(len(self.log()), 1)
            r = self.fwd(shell, 'log-clear')
            self.assertEqual((r.returncode, r.stdout + r.stderr), (0, ''))
            self.assertFalse((self.run_dir / 'log').exists())
            self.assertEqual(self.fwd(shell, 'log').stdout, '')
            # cleared twice, and with nothing there at all (not even the directory)
            self.assertEqual(self.fwd(shell, 'log-clear').returncode, 0)
            self.fresh()
            self.assertEqual(self.fwd(shell, 'log-clear').returncode, 0)
            # a link at the log's name goes; what it points to is not emptied
            victim = self.tmp / 'victim'
            victim.write_text('keep me\n')
            for name in ('log', 'log.part'):
                (self.run_dir / name).symlink_to(victim)
            self.assertEqual(self.fwd(shell, 'log-clear').returncode, 0)
            self.assertEqual(victim.read_text(), 'keep me\n')
            self.assertEqual(list(self.run_dir.iterdir()), [])
            # the next delivery starts a new log, a file of root's only
            self.fwd(shell, 'test')
            self.assertEqual(len(self.log()), 1)
            self.assertFalse((self.run_dir / 'log').is_symlink())
            self.assertEqual(stat.S_IMODE((self.run_dir / 'log').stat().st_mode), 0o600)
            self.assertEqual(victim.read_text(), 'keep me\n')
            # the queue and the retry state are not the log's to clear
            pool_msg(self.pool, 1, '+905551112233', 'x')
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1')
            before = self.queue()
            self.fwd(shell, 'log-clear')
            self.assertEqual((self.queue(), len(before)), (before, 1))

    def test_telegram_token_never_in_argv_or_log(self):
        pool_msg(self.pool, 3, 'ADANA BLD', 'Kargonuz yolda ğüş & co')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, telegram=1, telegram_token=TOKEN, telegram_chat='-100123', template='{text}')
            self.fwd(shell, 'enqueue', '3')
            self.fwd(shell, 'run')
            [(url, headers, body)] = self.requests()
            self.assertEqual(url, f'https://api.telegram.org/bot{TOKEN}/sendMessage')
            self.assertEqual(headers['Content-Type'], 'application/x-www-form-urlencoded')
            from urllib.parse import parse_qs
            q = {k: v[0] for k, v in parse_qs(body).items()}
            self.assertEqual((q['chat_id'], q['text']), ('-100123', 'Kargonuz yolda ğüş & co'))
            argv = (self.tmp / 'argv.log').read_text()
            self.assertNotIn(TOKEN, argv)
            self.assertNotIn(TOKEN.split(':')[1], (self.run_dir / 'log').read_text())

    def test_a_failure_is_retried_with_a_growing_wait_then_given_up(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1')
            self.answers('status 500', 'error connect', 'status 429')
            t0 = time.monotonic()
            r = self.fwd(shell, 'run', MU300_FWD_ATTEMPTS=3)
            self.assertEqual(r.returncode, 0, r.stderr)
            # 1 s, then 2 s between the three attempts; the worker's clock counts whole seconds (date +%s), so the
            # waits it really makes can be up to a second shorter each: only that it did wait is timed here
            self.assertGreaterEqual(time.monotonic() - t0, 1)
            got = [l[3:] for l in self.log()]
            self.assertEqual(got, [['retry', 'HTTP 500, attempt 1, next in 1 s'], ['retry', 'connect, attempt 2, next in 2 s'],
                                   ['failed', 'HTTP 429, gave up after 3 attempts']])
            self.assertEqual(self.queue(), [])
            self.assertEqual((self.run_dir / 'retry').read_text(), '')

    def test_a_retry_then_success(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1')
            self.answers('error timeout')
            self.fwd(shell, 'run')
            self.assertEqual([l[3] for l in self.log()], ['retry', 'sent'])
            self.assertEqual(len(self.requests()), 2)

    def test_a_refusal_is_not_retried(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1')
            self.answers('status 401')
            self.fwd(shell, 'run')
            self.assertEqual([l[3:] for l in self.log()], [['failed', 'HTTP 401']])
            self.assertEqual(len(self.requests()), 1)

    def test_a_message_gone_from_the_pool_is_dropped(self):
        f = pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1')
            f.rename(f.with_name('gone'))
            try:
                self.fwd(shell, 'run')
            finally:
                f.with_name('gone').rename(f)
            self.assertEqual([l[3:] for l in self.log()], [['failed', 'message no longer in the pool']])
            self.assertEqual(self.requests(), [])

    def test_a_worker_turned_off_stops_and_clear_empties_the_queue(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'enqueue', '1')
            self.write_conf(enabled=0, webhook=1, webhook_url=HOOK_URL)
            self.fwd(shell, 'run')
            self.assertEqual(self.requests(), [])
            self.assertEqual(len(self.queue()), 1)
            self.fwd(shell, 'clear')
            self.assertEqual(self.queue(), [])
            self.assertEqual([l[3:] for l in self.log()], [['dropped', 'forwarding turned off']])

    def test_sms_target_one_part_and_never_back_to_the_sender(self):
        long_gsm = 'a' * 200
        long_ucs = 'ş' * 80
        pool_msg(self.pool, 1, '+905551112233', long_gsm)
        pool_msg(self.pool, 2, '+905551112233', long_ucs)
        pool_msg(self.pool, 3, '+905550000000', 'loop?')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, sms=1, sms_number='+905550000000', template='{text}')
            self.fwd(shell, 'enqueue', '1', '2', '3')
            self.fwd(shell, 'run')
            sent = sorted((p.read_text(encoding='utf-8') for p in self.tmp.glob('sms-stdin.*')), key=len)
            # the number on stdin's first line, the text after it: nothing of either in an argument (ps shows those)
            self.assertEqual(sorted(sent), sorted(['+905550000000\n' + 'a' * 157 + '...', '+905550000000\n' + 'ş' * 67 + '...']))
            self.assertEqual((self.tmp / 'sms-argv.log').read_text().splitlines(), ['send --stdin'] * 2)
            res = {l[1]: l[3:] for l in self.log()}
            self.assertEqual(res['3'], ['failed', 'the sender is the forwarding number'])
            self.assertEqual((res['1'][0], res['2'][0]), ('sent', 'sent'))

    def test_a_runtime_directory_of_someone_else_is_refused(self):
        # a link where the RAM directory should be (made by someone else, pointing anywhere): nothing is written
        other = self.tmp / 'elsewhere'
        other.mkdir()
        for shell in self.each_shell():
            link = self.tmp / 'linked-run'
            link.unlink(missing_ok=True)
            link.symlink_to(other)
            self.write_conf(enabled=1, webhook=1, webhook_url='https://hook.example.com/x')
            r = self.fwd(shell, 'test', MU300_FWD_RUN=link)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not a directory of our own', r.stderr)
            self.assertEqual(list(other.iterdir()), [])

    def test_ubuntu_sms_target_on_stdin_only(self):
        # Ubuntu: no mu300-sms, the sms command's send --stdin; neither the number nor the text in its arguments
        pool_msg(self.pool, 1, '+905551112233', 'Kod 4321')
        self.stub('sms', 'printf "%s\\n" "$*" >> "$STUBLOG/ubsms-argv.log"; cat > "$STUBLOG/ubsms-stdin"; echo "sent (reference 3)"')
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'ubsms-argv.log').unlink(missing_ok=True)
            self.write_conf(enabled=1, sms=1, sms_number='+905550000000', template='{text}')
            self.fwd(shell, 'enqueue', '1', MU300_SMS_BIN=self.tmp / 'no-mu300-sms')
            self.fwd(shell, 'run', MU300_SMS_BIN=self.tmp / 'no-mu300-sms')
            self.assertEqual((self.tmp / 'ubsms-argv.log').read_text().splitlines(), ['send --stdin'])
            self.assertEqual((self.tmp / 'ubsms-stdin').read_text(encoding='utf-8'), '+905550000000\nKod 4321')
            self.assertEqual(self.log()[-1][3], 'sent')

    def test_curl_helper_keeps_secrets_out_of_its_arguments(self):
        # Ubuntu's HTTP client: curl gets the URL and the headers in its configuration on stdin, the body in a file
        self.stub('curl', 'printf "%s\\n" "$*" >> "$STUBLOG/curl-argv.log"; cat > "$STUBLOG/curl-conf"; '
                          'for a in "$@"; do :; done; f=$(sed -n \'s/^data-binary = "@\\(.*\\)"$/\\1/p\' "$STUBLOG/curl-conf"); '
                          'cp "$f" "$STUBLOG/curl-body"; ls -ld "$(dirname "$f")" > "$STUBLOG/curl-dir"; printf 200')
        req = 'https://api.example.com/bot123:SECRETtok/send?x=1\nAuthorization: Bearer hdrSECRET\nContent-Type: application/json\n\n{"t":"msg"}'
        for shell in self.each_shell():
            r = subprocess.run(shell + [str(HTTP_SH)], input=req, capture_output=True, text=True,
                               env=self.env(MU300_FWD_TMP=self.tmp), timeout=30)
            self.assertEqual(r.stdout, 'status 200\n', r.stderr)
            argv = (self.tmp / 'curl-argv.log').read_text()
            self.assertEqual(argv.splitlines()[-1], '-K -')
            for secret in ('SECRETtok', 'hdrSECRET', 'msg'):
                self.assertNotIn(secret, argv)
            conf = (self.tmp / 'curl-conf').read_text()
            self.assertIn('url = "https://api.example.com/bot123:SECRETtok/send?x=1"', conf)
            self.assertIn('header = "Authorization: Bearer hdrSECRET"', conf)
            self.assertEqual((self.tmp / 'curl-body').read_text(), '{"t":"msg"}')
            self.assertTrue((self.tmp / 'curl-dir').read_text().startswith('drwx------'))
            # the request's directory is gone afterwards
            self.assertEqual([d for d in self.tmp.iterdir() if d.name.startswith('http.')], [])

    def test_sms_target_retries_a_modem_refusal(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            (self.tmp / 'sms-fail').write_text('mu300-sms: the modem did not take the message: +CMS ERROR: 28\n')
            self.write_conf(enabled=1, sms=1, sms_number='+905550000000')
            self.fwd(shell, 'enqueue', '1')
            self.fwd(shell, 'run', MU300_FWD_ATTEMPTS=2)
            self.assertEqual([l[3] for l in self.log()], ['retry', 'failed'])
            self.assertIn('+CMS ERROR: 28', self.log()[0][4])
            (self.tmp / 'sms-fail').unlink()

    def test_email_through_msmtp_password_in_a_private_file(self):
        pool_msg(self.pool, 1, 'ADANA BLD', 'Kargonuz yolda')
        # msmtp: its arguments, the mode of its config file and that file, the message
        self.stub('msmtp', 'echo "$*" >> "$STUBLOG/msmtp-argv"; c=$2; ls -l "$c" | cut -c1-10 > "$STUBLOG/msmtp-mode"; '
                           'cp "$c" "$STUBLOG/msmtp-conf"; cat > "$STUBLOG/msmtp-msg"')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, email=1, email_host='smtp.example.com', email_port=465, email_tls='tls',
                            email_user='me@example.com', email_password=PASSWORD, email_from='me@example.com',
                            email_to='you@example.com', template='{sender}: {text}')
            self.fwd(shell, 'enqueue', '1')
            self.fwd(shell, 'run')
            self.assertEqual([l[3] for l in self.log()], ['sent'])
            self.assertNotIn(PASSWORD, (self.tmp / 'msmtp-argv').read_text())
            self.assertEqual((self.tmp / 'msmtp-mode').read_text().strip(), '-rw-------')
            conf = (self.tmp / 'msmtp-conf').read_text()
            self.assertIn(f'password {PASSWORD}\n', conf)
            self.assertIn('tls on\ntls_starttls off\n', conf)
            self.assertIn('host smtp.example.com\nport 465\n', conf)
            msg = (self.tmp / 'msmtp-msg').read_text()
            self.assertIn('To: you@example.com\nSubject: SMS from ADANA BLD\n', msg)
            self.assertTrue(msg.endswith('\n\nADANA BLD: Kargonuz yolda\n'))
            self.assertEqual(list(self.run_dir.glob('msmtp.*')), [])

    def test_send_test_to_every_target_that_is_on(self):
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=0, webhook=1, webhook_url=HOOK_URL, sms=1, sms_number='+905550000000')
            self.answers('status 503')
            r = self.fwd(shell, 'test')
            self.assertEqual(r.returncode, 1)
            self.assertEqual([l[1:4] for l in self.log()], [['test', 'webhook', 'failed'], ['test', 'sms', 'sent']])
            self.assertEqual(self.queue(), [])
            self.write_conf(enabled=1)
            r = self.fwd(shell, 'test')
            self.assertEqual(self.log()[-1][1:], ['test', '-', 'failed', 'no target is on'])

    def test_kick_with_nothing_queued_writes_nothing(self):
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            r = self.fwd(shell, 'kick', MU300_FWD_NO_WORKER=0)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(self.state.exists())
            r = self.fwd(shell, 'status')
            self.assertEqual(r.stdout.splitlines()[:2], ['queued 0', 'running 0'])

    def test_kick_starts_one_worker_that_delivers_in_the_background(self):
        pool_msg(self.pool, 1, '+905551112233', 'x')
        for shell in self.each_shell():
            self.fresh()
            self.write_conf(enabled=1, webhook=1, webhook_url=HOOK_URL)
            r = self.fwd(shell, 'enqueue', '1', MU300_FWD_NO_WORKER=0)
            self.assertEqual(r.returncode, 0, r.stderr)
            for _ in range(100):
                if self.queue() == [] and not (self.run_dir / 'worker.lock').exists():
                    break
                time.sleep(0.1)
            self.assertEqual([l[3] for l in self.log()], ['sent'])

    def test_one_sms_cut(self):
        # 160 plain characters, or 70 UCS-2 units (an emoji takes two), "..." where it was cut
        self.lib_from_script()
        for shell in self.each_shell():
            for text, want in (('a' * 160, 'a' * 160), ('a' * 161, 'a' * 157 + '...'), ('[' * 10, '[' * 10),
                               ('x' * 70 + '{', 'x' * 67 + '...'), ('😀' * 40, '😀' * 33 + '...'),
                               ('ğ' * 70, 'ğ' * 70), ('ğ' * 71, 'ğ' * 67 + '...')):
                r = subprocess.run(shell + ['-c', f'. "{self.tmp}/lib.sh"; one_sms "$1"', 'sh', text],
                                   capture_output=True, env=self.env())
                self.assertEqual(r.stdout.decode(), want, (shell, text[:5]))

    def lib_from_script(self):
        """The functions of mu300-sms-forward, without its command dispatch, as $tmp/lib.sh."""
        src = FWD.read_text(encoding='utf-8')
        (self.tmp / 'lib.sh').write_text(src[:src.index('\ncase ${1:-} in')] + '\n', encoding='utf-8')

    def test_render_leaves_unknown_braces_and_reads_no_escapes_in_values(self):
        self.lib_from_script()
        for shell in self.each_shell():
            r = subprocess.run(shell + ['-c', f'. "{self.tmp}/lib.sh"; c_template="$1"; render "$2" "$3" "$4" "$5"', 'sh',
                                        '<{sender}|{time}|{text}|{device}|{other}|{text\\n>', '\\n&$(x)', 't', '%s\\t', 'd'],
                               capture_output=True, env=self.env())
            self.assertEqual(r.stdout.decode(), '<\\n&$(x)|t|%s\\t|d|{other}|{text\n>')

    def test_the_http_helper_takes_its_request_on_stdin_only(self):
        src = HTTP_UC.read_text()
        self.assertIn("stdin.read('all')", src)
        self.assertNotIn('ARGV', src)
        self.assertIn('verify: true', src)
        self.assertIn('if (!cl.connect())', src)
        # the reply's body: only when the caller asks in the environment, only of a 2xx, only its first bytes
        self.assertIn("getenv('MU300_FWD_ANSWER')", src)
        self.assertIn('if (st.status >= 200 && st.status < 300 && keep > 0) {', src)
        self.assertIn('answer += substr(d, 0, keep - length(answer));', src)
        fwd = FWD.read_text()
        self.assertIn('ucode "$HTTP"', fwd)
        self.assertNotRegex(fwd, r'ucode[^\n]*\$c_')


class Adapter(ShellTest):
    """unisoc-modem/forward: get never returns a secret, set checks and writes a root-only file."""

    def setUp(self):
        super().setUp()
        (self.stubs / 'jsonfilter.py').write_text(JSONFILTER)
        self.stub('jsonfilter', f'exec python3 "{self.stubs}/jsonfilter.py" "$@"')
        self.stub('uci', 'exit 1')
        self.conf = self.tmp / 'etc' / 'sms-forward.conf'
        # the forwarder: its status, a log, and what it was asked
        self.stub('fwd', 'echo "$*" >> "$STUBLOG/fwd.log"; case $1 in '
                         'status) printf "queued 2\\nrunning 1\\nemail ${FWD_EMAIL:-0}\\n" ;; '
                         'log-clear) [ -z "${FWD_CLEAR_FAIL:-}" ] || exit 1 ;; '
                         'test-gate) [ -z "${FWD_GATE_SHUT:-}" ] || exit 1 ;; '
                         'log) printf "1791000000\\t12\\twebhook\\tsent\\tHTTP 200\\n1791000001\\ttest\\ttelegram\\tfailed\\tHTTP 4\\"01\\n" ;; '
                         'esac')

    def adapter(self, shell, *args, stdin=None, **env):
        return self.script(shell, ADAPTER, *args, stdin=stdin, MU300_FWD_CONF=self.conf,
                           MU300_FWD_BIN=self.stubs / 'fwd', **env)

    def set(self, shell, **fields):
        r = self.adapter(shell, 'set', stdin=json.dumps(fields, ensure_ascii=False))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def get(self, shell, **env):
        r = self.adapter(shell, 'get', **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, json.loads(r.stdout)

    FULL = dict(enabled='1', template='{text}\n-- {sender}', allow='+905551112233\nBANK*', deny='SPAM', keywords='code, kod',
                webhook='1', webhook_url=HOOK_URL, webhook_format='json',
                webhook_headers='Authorization: Bearer hdrsecret\nX-Device: u30',
                telegram='1', telegram_token=TOKEN, telegram_chat='@mychannel',
                email='0', email_host='smtp.example.com', email_port='587', email_tls='starttls',
                email_user='me@example.com', email_password=PASSWORD, email_from='me@example.com',
                email_to='you@example.com', sms='1', sms_number='+905550000000')

    def test_set_writes_a_private_file_and_get_masks_every_secret(self):
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            self.assertEqual(self.set(shell, **self.FULL), {'ok': 1})
            self.assertEqual(stat.S_IMODE(self.conf.stat().st_mode), 0o600)
            lines = self.conf.read_text().splitlines()
            for want in ('enabled=1', 'template={text}\\n-- {sender}', 'allow=+905551112233', 'allow=BANK*', 'deny=SPAM',
                         'keyword=code', 'keyword=kod', f'webhook_url={HOOK_URL}',
                         'webhook_header=Authorization: Bearer hdrsecret', 'webhook_header=X-Device: u30',
                         f'telegram_token={TOKEN}', 'telegram_chat=@mychannel', f'email_password={PASSWORD}',
                         'sms_number=+905550000000'):
                self.assertIn(want, lines)
            raw, st = self.get(shell)
            for secret in (TOKEN.split(':')[1], PASSWORD, 'urlsecret', 'hdrsecret', '/hook'):
                self.assertNotIn(secret, raw)
            self.assertEqual((st['webhook_url_set'], st['webhook_url_hint']), (1, 'http://192.0.2.10:8099/...'))
            self.assertEqual((st['webhook_headers_set'], st['webhook_header_names']), (1, 'Authorization, X-Device'))
            self.assertEqual((st['telegram_token_set'], st['telegram_token_hint']), (1, '123456789:...'))
            self.assertEqual(st['email_password_set'], 1)
            self.assertEqual((st['template'], st['allow'], st['keywords']),
                             ('{text}\n-- {sender}', '+905551112233\nBANK*', 'code\nkod'))
            self.assertEqual((st['queued'], st['running'], st['email_available']), (2, 1, 0))
            self.assertEqual(st['log'][0], {'time': 1791000001, 'id': 'test', 'target': 'telegram', 'result': 'failed',
                                            'detail': 'HTTP 4"01'})

    def test_check_mode_checks_everything_and_writes_nothing(self):
        # mu300-backup's check of a restore (FINDINGS 155): the same rules, no file written
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            r = self.adapter(shell, 'set', stdin=json.dumps(self.FULL), MU300_FWD_CHECK='1')
            self.assertEqual(json.loads(r.stdout), {'ok': 1, 'checked': 1})
            self.assertFalse(self.conf.exists())
            r = self.adapter(shell, 'set', stdin=json.dumps(dict(self.FULL, webhook_url='ftp://x')), MU300_FWD_CHECK='1')
            self.assertEqual(json.loads(r.stdout)['ok'], 0)
            self.assertFalse(self.conf.exists())

    def test_an_empty_secret_keeps_the_saved_one_and_clear_removes_it(self):
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            self.set(shell, **self.FULL)
            kept = dict(self.FULL, webhook_url='', webhook_headers='', telegram_token='', email_password='')
            self.assertEqual(self.set(shell, **kept), {'ok': 1})
            text = self.conf.read_text()
            for secret in (f'webhook_url={HOOK_URL}', 'webhook_header=Authorization: Bearer hdrsecret',
                           f'telegram_token={TOKEN}', f'email_password={PASSWORD}'):
                self.assertIn(secret, text)
            cleared = dict(kept, webhook='0', telegram='0', clear='webhook_url webhook_headers telegram_token email_password')
            self.assertEqual(self.set(shell, **cleared), {'ok': 1})
            text = self.conf.read_text()
            for secret in ('urlsecret', 'hdrsecret', TOKEN, PASSWORD):
                self.assertNotIn(secret, text)

    BODY = '{"msg_type": "text",\n "content": {"text": "{message}"},\n "path": "C:\\\\x", "q": "\\"{sender}\\""}'

    def test_the_custom_body_is_saved_shown_again_and_sent(self):
        """The page's text -> the file's one line -> the page again, and through mu300-sms-forward to the request."""
        (self.stubs / 'ucode.py').write_text(UCODE)
        self.stub('ucode', f'exec python3 "{self.stubs}/ucode.py" "$@"')
        pool = self.tmp / 'pool'
        pool_msg(pool, 3, 'BANK "A"', 'kod: 123\\456\nsatır')
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            for f in self.tmp.glob('http.*'):
                f.unlink()
            self.assertEqual(self.set(shell, **dict(self.FULL, allow='', deny='', keywords='', telegram='0', sms='0',
                                                    webhook_body=self.BODY)), {'ok': 1})
            lines = self.conf.read_text().splitlines()
            [stored] = [l for l in lines if l.startswith('webhook_body=')]
            self.assertEqual(stored, 'webhook_body=' + self.BODY.replace('\\', '\\\\').replace('\n', '\\n'))
            raw, st = self.get(shell)
            self.assertEqual(st['webhook_body'], self.BODY)
            for secret in ('urlsecret', 'hdrsecret', '/hook'):      # the body comes back, the secrets still do not
                self.assertNotIn(secret, raw)
            env = self.env(MU300_FWD_CONF=self.conf, MU300_FWD_STATE=self.tmp / 'state', MU300_FWD_RUN=self.tmp / 'run',
                           MU300_SMS_POOL=pool, MU300_FWD_HTTP=HTTP_UC, MU300_FWD_NO_WORKER=1)
            for cmd in ('enqueue 3', 'run'):
                r = subprocess.run(shell + [str(FWD)] + cmd.split(), capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual((r.returncode, r.stdout + r.stderr), (0, ''))
            [req] = sorted(self.tmp.glob('http.*'))
            head, _, body = req.read_bytes().decode('utf-8').partition('\n\n')
            self.assertEqual(json.loads(body), {'msg_type': 'text', 'path': 'C:\\x', 'q': '"BANK "A""',
                                                'content': {'text': 'kod: 123\\456\nsatır\n-- BANK "A"'}})
            # saved again with the field empty: the standard body is back (a body is not a secret that is kept)
            self.assertEqual(self.set(shell, **dict(self.FULL, webhook_url='', webhook_body='')), {'ok': 1})
            self.assertIn('webhook_body=', self.conf.read_text().splitlines())
            self.assertEqual(self.get(shell)[1]['webhook_body'], '')
            # a page from before the field (it does not send it): the same as empty
            self.set(shell, **dict(self.FULL, webhook_body=self.BODY))
            self.assertEqual(self.set(shell, **self.FULL), {'ok': 1})
            self.assertEqual(self.get(shell)[1]['webhook_body'], '')

    def test_a_backslash_in_a_template_is_stored_doubled(self):
        # busybox awk (the device's) wrote one backslash where gsub's replacement asked for two: a template's \n
        # (two characters) then came back as a line break. The old code fails this only where awk is busybox
        tpl = '{text} \\n C:\\x \\\\ "q"'
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            self.assertEqual(self.set(shell, **dict(self.FULL, template=tpl, webhook_body=tpl)), {'ok': 1})
            lines = self.conf.read_text().splitlines()
            self.assertIn('template={text} \\\\n C:\\\\x \\\\\\\\ "q"', lines)
            self.assertIn('webhook_body={text} \\\\n C:\\\\x \\\\\\\\ "q"', lines)
            st = self.get(shell)[1]
            self.assertEqual((st['template'], st['webhook_body']), (tpl, tpl))

    def test_the_text_format_and_the_body_limit(self):
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            self.assertEqual(self.set(shell, **dict(self.FULL, webhook_format='text', webhook_body='é' * 1000)), {'ok': 1})
            self.assertIn('webhook_format=text', self.conf.read_text().splitlines())
            self.assertEqual(self.get(shell)[1]['webhook_format'], 'text')
            before = self.conf.read_text()
            err = 'The webhook body is too long or contains a control character'
            for body in ('x' * 2001, 'é' * 1001, 'a\x01b', 'a\rb', 'a\x7fb'):      # the limit is bytes
                r = self.set(shell, **dict(self.FULL, webhook_body=body))
                self.assertEqual((r['ok'], r['error']), (0, err), body[:10])
                self.assertEqual(self.conf.read_text(), before)

    def test_the_answer_check_is_saved_shown_again_and_checked(self):
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            for rule in ('errcode=0', 'ok=true', 'StatusCode=0', '_a1=-15', 'k' * 32 + '=' + '9' * 16, 'k=' + 'v' * 16):
                self.assertEqual(self.set(shell, **dict(self.FULL, webhook_expect=rule)), {'ok': 1})
                self.assertIn('webhook_expect=' + rule, self.conf.read_text().splitlines())
                raw, st = self.get(shell)
                self.assertEqual(st['webhook_expect'], rule)
                for secret in ('urlsecret', 'hdrsecret', '/hook'):
                    self.assertNotIn(secret, raw)
            before = self.conf.read_text()
            err = ('The answer check must be key=value, like errcode=0 or ok=true (the value a whole number or lowercase '
                   'letters, 16 at most)')
            for rule in ('errcode', 'errcode=', '=0', 'a=b=c', 'err code=0', 'errcode=0;id', 'errcode=$(id)',
                         'errcode=`id`', 'errcode="0"', 'é=0', 'errcode=é', 'data.errcode=0', 'k' * 33 + '=0',
                         'k=' + 'v' * 17, 'k=' + '9' * 17, 'errcode=0\nwebhook_url=http://198.51.100.1/', 'errcode=0\n',
                         'errcode=*', 'a-b=0', '1k=0', 'v=1.0', 'v=+1', 'k=True', 'k=a1', 'k=-'):
                r = self.set(shell, **dict(self.FULL, webhook_expect=rule))
                self.assertEqual((r['ok'], r['error']), (0, err), rule)
                self.assertEqual(self.conf.read_text(), before)
            # empty, or a page from before the field (it does not send it): any 2xx counts again
            for fields in (dict(self.FULL, webhook_expect=''), self.FULL):
                self.set(shell, **dict(self.FULL, webhook_expect='errcode=0'))
                self.assertEqual(self.set(shell, **fields), {'ok': 1})
                self.assertIn('webhook_expect=', self.conf.read_text().splitlines())
                self.assertEqual(self.get(shell)[1]['webhook_expect'], '')

    def test_logclear(self):
        for shell in self.each_shell():
            (self.tmp / 'fwd.log').unlink(missing_ok=True)
            r = self.adapter(shell, 'logclear')
            self.assertEqual(json.loads(r.stdout), {'ok': 1})
            self.assertEqual((self.tmp / 'fwd.log').read_text().splitlines(), ['log-clear'])
            r = self.adapter(shell, 'logclear', '/etc/passwd', FWD_CLEAR_FAIL=1)
            self.assertEqual(json.loads(r.stdout), {'ok': 0, 'error': 'The delivery log could not be cleared'})
            self.assertEqual((self.tmp / 'fwd.log').read_text().splitlines(), ['log-clear', 'log-clear'])

    def test_turning_off_empties_the_queue(self):
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            (self.tmp / 'fwd.log').unlink(missing_ok=True)
            self.set(shell, **self.FULL)
            self.set(shell, **dict(self.FULL, enabled='0'))
            self.assertIn('clear', (self.tmp / 'fwd.log').read_text().splitlines())

    def test_refusals(self):
        bad = [
            ('enabled', 'yes', 'Invalid forwarding setting'),
            ('template', 'a\x01b', 'The message template is too long or contains a control character'),
            ('template', 'x' * 1001, 'The message template is too long or contains a control character'),
            ('allow', '\n'.join(str(i) for i in range(51)), 'Invalid filter list (at most 50 lines)'),
            ('keywords', 'k' * 65, 'Invalid filter list (at most 50 lines)'),
            ('webhook_url', 'ftp://x/y', 'The webhook address must be an http:// or https:// URL'),
            ('webhook_url', 'http://x y', 'The webhook address must be an http:// or https:// URL'),
            ('webhook_url', 'http://x/\ny', 'The webhook address must be an http:// or https:// URL'),
            ('webhook_headers', 'Host: evil', 'Invalid header'),
            ('webhook_headers', 'content-length: 1', 'Invalid header'),
            ('webhook_headers', 'Bad Name: x', 'Invalid header'),
            ('webhook_format', 'xml', 'Invalid forwarding setting'),
            ('telegram_token', 'nope', 'Invalid Telegram bot token'),
            ('telegram_chat', 'chat;id', 'Invalid Telegram chat (a number, or @channel)'),
            ('email_port', '70000', 'Invalid e-mail setting'),
            ('email_to', 'not an address', 'Invalid e-mail setting'),
            ('email_password', 'pa\nss', 'Invalid e-mail setting'),
            ('sms_number', '+90abc', 'The number may only contain digits (and a leading +)'),
            ('clear', 'all;rm', 'Invalid forwarding setting'),
        ]
        for shell in self.each_shell():
            self.conf.parent.mkdir(exist_ok=True)
            self.conf.unlink(missing_ok=True)
            for key, value, err in bad:
                with self.subTest(key=key, value=value[:20]):
                    r = self.set(shell, **dict(self.FULL, **{key: value}))
                    self.assertEqual(r['ok'], 0)
                    self.assertTrue(r['error'].startswith(err), r)
                    self.assertFalse(self.conf.exists())
            for fields, err in ((dict(self.FULL, webhook_url=''), 'The webhook needs an address'),
                                (dict(self.FULL, telegram_chat=''), 'Telegram needs a bot token and a chat'),
                                (dict(self.FULL, email='1'), 'E-mail needs msmtp, which this image does not have'),
                                (dict(self.FULL, sms_number=''), 'SMS forwarding needs a phone number')):
                r = self.set(shell, **fields)
                self.assertEqual((r['ok'], r['error']), (0, err))
            # with msmtp, a mail target needs its addresses
            r = self.adapter(shell, 'set', stdin=json.dumps(dict(self.FULL, email='1', email_to='')), FWD_EMAIL=1)
            self.assertEqual(json.loads(r.stdout)['error'], 'E-mail needs a server, a sender and a recipient')

    def test_test_runs_detached(self):
        self.stub('setsid', 'echo "setsid $*" >> "$STUBLOG/fwd.log"')
        for shell in self.each_shell():
            (self.tmp / 'fwd.log').unlink(missing_ok=True)
            r = self.adapter(shell, 'test')
            self.assertEqual(json.loads(r.stdout), {'ok': 1, 'started': 1})
            # the gate is asked first, then the test starts in the background
            for _ in range(50):
                if 'setsid' in (self.tmp / 'fwd.log').read_text():
                    break
                time.sleep(0.05)
            log = (self.tmp / 'fwd.log').read_text().splitlines()
            self.assertEqual((log[0], log[-1].split()[0], log[-1].split()[-1], len(log)), ('test-gate', 'setsid', 'test', 2))

    def test_a_test_too_soon_after_another_is_refused(self):
        self.stub('setsid', 'echo "setsid $*" >> "$STUBLOG/fwd.log"')
        for shell in self.each_shell():
            (self.tmp / 'fwd.log').unlink(missing_ok=True)
            r = self.adapter(shell, 'test', FWD_GATE_SHUT=1)
            self.assertEqual(json.loads(r.stdout), {'ok': 0, 'error': 'A test was started a moment ago; wait a few seconds'})
            time.sleep(0.3)
            self.assertEqual((self.tmp / 'fwd.log').read_text().splitlines(), ['test-gate'])

    def test_unknown_operation(self):
        for shell in self.each_shell():
            r = self.adapter(shell, 'rm')
            self.assertEqual(json.loads(r.stdout), {'ok': 0, 'error': 'Unknown forwarding operation'})


class Wiring(unittest.TestCase):
    def test_page_menu_acl_and_rpc(self):
        app = TOP / 'openwrt' / 'luci-app-mu300'
        menu = json.loads((app / 'root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
        self.assertEqual(menu['admin/modem/sms-forward']['action'], {'type': 'view', 'path': 'mu300/forward'})
        cellular = [k for _, k in sorted((v['order'], k) for k, v in menu.items() if k.startswith('admin/modem/'))]
        self.assertEqual(cellular[cellular.index('admin/modem/sms') + 1], 'admin/modem/sms-forward')
        acl = json.loads((app / 'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        for m in ('forward_get', 'forward_set', 'forward_test', 'forward_log_clear'):
            self.assertIn(m, acl['write']['ubus']['mu300dash'])
            self.assertNotIn(m, acl['read']['ubus']['mu300dash'])
        common = (app / 'htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn('callFwdGet: callFwdGet, callFwdSet: callFwdSet, callFwdTest: callFwdTest, '
                      'callFwdLogClear: callFwdLogClear', common)
        # the custom body, then the answer check (issue #171): declared to rpcd, sent by the page as the last
        # parameters in that order (an older cached page still lines up)
        self.assertIn("'sms_number', 'webhook_body', 'webhook_expect' ]", common)
        self.assertIn('"webhook_body": "String"', DASH.read_text())
        self.assertIn('"webhook_expect": "String"', DASH.read_text())
        self.assertIn('"forward_log_clear": { }', DASH.read_text())
        view = (app / 'htdocs/luci-static/resources/view/mu300/forward.js').read_text()
        self.assertIn("this.area('webhook_body', st.webhook_body,", view)
        self.assertIn("this.val('sms_number').trim(), this.val('webhook_body'),\n"
                      "\t\t\tthis.val('webhook_expect').trim())", view)
        self.assertIn("this.text('webhook_expect', st.webhook_expect,", view)
        # a preset fills the field in and is nothing else: every one is a rule the adapter takes
        import re
        presets = re.findall(r"E\('option', \{ 'value': '([^']*=[^']*)' \}", view)
        self.assertEqual(presets, ['errcode=0', 'code=0', 'code=200', 'ok=true'])
        self.assertIn('M.callFwdLogClear()', view)
        self.assertIn("'value': 'text'", view)
        self.assertTrue(os.access(ADAPTER, os.X_OK))
        self.assertTrue(os.access(FWD, os.X_OK))
        build = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('$R/opt/mu300/bin/mu300-sms-forward', build)

    def test_the_view_never_shows_a_saved_secret(self):
        view = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/forward.js').read_text()
        # the secret fields start empty; only their *_set flags and hints are read
        for k in ('webhook_url', 'webhook_headers', 'telegram_token', 'email_password'):
            self.assertNotIn(f'st.{k})', view)
            self.assertNotIn(f'st.{k},', view)
        self.assertIn("this.secret('telegram_token', st.telegram_token_set", view)
        # the cards' ids never clash with the fields' ('mud-fwd-' + field name): a card named like its checkbox
        # made the page read the card instead of the box
        import re
        cards = re.findall(r"E\('section', \{ 'class': 'mud-card', 'id': '([^']+)'", view)
        self.assertTrue(cards and all(c.startswith('mud-fwd-card-') for c in cards), cards)
        self.assertNotIn("'id': 'mud-fwd-telegram'", view)


if __name__ == '__main__':
    unittest.main()
