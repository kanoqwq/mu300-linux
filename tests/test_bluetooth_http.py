"""Actual uhttpd/rpcd/ucode event delivery, without hardware or host ports.

Use the existing OpenWrt test image. If it lacks ucode-mod-socket, explicitly
select that matching module using UNISOC_BT_TEST_SOCKET_MODULE.
"""
import json
import os
from pathlib import Path
import socketserver
import subprocess
import tempfile
import threading
import time

TOP = Path(__file__).resolve().parents[1]
IMAGE = 'mu300-openwrt-base:25.12.5'


def run(*args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def main():
    requests = []
    class Feed(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                request = json.loads(self.rfile.readline())
                requests.append(request)
                time.sleep(.1)
                seq = len(requests)
                reply = dict(ok=True, sequence=seq, event_id='fixture-boot',
                             events=[dict(seq=seq, type='status', data={})])
                self.wfile.write((json.dumps(reply)+'\n').encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    with tempfile.TemporaryDirectory(prefix='bluetooth-http-') as folder:
        fixture = Path(folder)
        script = TOP/'openwrt/overlay/usr/lib/unisoc-bluetooth/events.uc'
        # Exercise the same lease lifecycle without waiting 30 seconds per case.
        (fixture/'events.uc').write_text(script.read_text().replace('AUTH_LEASE = 30000', 'AUTH_LEASE = 1500'))
        cgi = fixture/'cgi-bin'; cgi.mkdir()
        (cgi/'probe').write_text("#!/bin/sh\nprintf 'Content-Type: text/plain\\r\\n\\r\\nprobe-ok\\n'\n")
        (cgi/'probe').chmod(0o755)
        legacy = TOP/'openwrt/overlay/www/cgi-bin/unisoc-bluetooth-events'
        (cgi/'unisoc-bluetooth-events').write_bytes(legacy.read_bytes().replace(b'\r\n', b'\n'))
        (cgi/'unisoc-bluetooth-events').chmod(0o755)
        server = Server(str(fixture/'control.sock'), Feed)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        command = ['docker', 'run', '-d', '--pull=never', '--network=none', '--platform=linux/arm64',
                   '--mount', f'type=bind,src={fixture},dst=/fixture,readonly']
        module = os.environ.get('UNISOC_BT_TEST_SOCKET_MODULE')
        if module:
            assert Path(module).is_file(), module
            command += ['--mount', f'type=bind,src={module},dst=/usr/lib/ucode/socket.so,readonly']
        startup = '''set -eu
mkdir -p /var/run/ubus
ubusd >/tmp/ubus.log 2>&1 &
while [ ! -S /var/run/ubus/ubus.sock ]; do sleep 1; done
rpcd >/tmp/rpcd.log 2>&1 &
UNISOC_BT_EVENTS_SOCKET=/fixture/control.sock ucode /fixture/events.uc >/tmp/bridge.log 2>&1 &
uhttpd -f -p 127.0.0.1:18080 -h /fixture -u /ubus -n 3 -N 100 -t 10 -T 5 >/tmp/http.log 2>&1 &
exec sleep 90
'''
        created = run(*command, '--entrypoint', '/bin/sh', IMAGE, '-c', startup)
        assert created.returncode == 0, created.stderr
        container = created.stdout.strip(); streams = []
        def execute(*args): return run('docker', 'exec', container, *args)
        def http(path, sid=None):
            argv = ['wget', '-q', '-T', '2', '-O', '-']
            if sid: argv += ['--header', 'Authorization: Bearer '+sid]
            return execute(*argv, 'http://127.0.0.1:18080'+path)
        def session():
            reply = execute('ubus', 'call', 'session', 'create', '{"timeout":60}')
            assert reply.returncode == 0, reply.stderr
            sid = json.loads(reply.stdout)['ubus_rpc_session']
            grant = dict(ubus_rpc_session=sid, scope='ubus',
                         objects=[['unisoc-bluetooth-events', ':subscribe']])
            assert execute('ubus', 'call', 'session', 'grant', json.dumps(grant)).returncode == 0
            return sid
        try:
            for _ in range(40):
                if execute('ubus', 'list', 'unisoc-bluetooth-events').stdout.strip(): break
                time.sleep(.1)
            else:
                raise AssertionError(execute('cat', '/tmp/bridge.log').stdout)
            sid = session()
            denied = json.loads(http('/ubus/subscribe/unisoc-bluetooth-events').stdout)
            assert denied['code'] == -13, denied
            for index in range(8):
                argv = ['docker', 'exec', container, 'wget', '-q', '-T', '5', '-O', '/tmp/stream'+str(index),
                        '--header', 'Authorization: Bearer '+sid,
                        'http://127.0.0.1:18080/ubus/subscribe/unisoc-bluetooth-events']
                streams.append(subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(.4)
            for _ in range(4):
                probe = http('/cgi-bin/probe')
                assert probe.returncode == 0 and 'probe-ok' in probe.stdout, probe
                assert execute('ubus', '-t', '2', 'list', 'session').stdout.strip() == 'session'
            assert any(r.get('action') == 'events' for r in requests), requests
            assert execute('sh', '-c', "grep -l '^data:' /tmp/stream* | wc -l").stdout.strip() == '8'
            print('PASS eight native event streams with max_requests=3; CGI and rpcd/session remain responsive', flush=True)
            assert execute('ubus', 'call', 'session', 'destroy', json.dumps({'ubus_rpc_session': sid})).returncode == 0
            for child in streams: child.wait(timeout=4)
            assert json.loads(http('/ubus/subscribe/unisoc-bluetooth-events', sid).stdout)['code'] == -13
            print('PASS server ends subscription lease; revoked sessions cannot reconnect', flush=True)
            time.sleep(.3); count=len(requests); time.sleep(.3)
            assert len(requests)==count,'Event feed kept polling without subscribers'
            legacy_reply = http('/cgi-bin/unisoc-bluetooth-events')
            assert legacy_reply.returncode != 0
            assert http('/cgi-bin/probe').returncode == 0
            print('PASS cached legacy endpoint terminates promptly without holding CGI slots', flush=True)
        finally:
            for child in streams:
                if child.poll() is None: child.terminate()
                child.wait(timeout=3)
            logs = execute('cat', '/tmp/bridge.log').stdout
            if logs: print(logs)
            assert run('docker', 'rm', '-f', container).returncode == 0
            server.shutdown(); server.server_close(); thread.join(2)


if __name__ == '__main__':
    main()
