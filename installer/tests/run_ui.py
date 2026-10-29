"""Run all production-bundle browser checks with an owned, temporary server."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
CHECKS=('ui_smoke','help_smoke','refresh_smoke','method_smoke','ordering_smoke','diagnostics_smoke','device_reboot_smoke','update_smoke','support_smoke','wireless_smoke','uninstall_smoke','mtp_smoke','online_shrink_smoke')


def main():
    if not (ROOT/'ui/dist/index.html').is_file():
        raise SystemExit('Build the production frontend first: npm run build in installer/ui')
    # Never attach tests to, or terminate, somebody else's running application.
    with socket.socket() as check:
        check.bind(('127.0.0.1',1420))
    results=[]
    with tempfile.TemporaryFile() as log:
        server=subprocess.Popen([sys.executable,'-m','http.server','1420','--bind','127.0.0.1',
                                 '--directory',str(ROOT/'ui/dist')],stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+15
            while True:
                if server.poll() is not None:raise RuntimeError('Test server exited')
                try:
                    with urlopen('http://127.0.0.1:1420',timeout=1) as response:
                        if response.status==200:break
                except OSError:
                    if time.monotonic()>deadline:raise RuntimeError('Test server did not start')
                    time.sleep(.1)
            for name in CHECKS:
                result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tests'/(name+'.py'))],
                                      capture_output=True,timeout=180)
                row={'test':name,'exit':result.returncode,'output':result.stdout.decode('utf-8'),
                     'error':result.stderr.decode('utf-8')}
                results.append(row)
                print(name+(': PASS' if result.returncode==0 else ': FAIL'),flush=True)
                if result.returncode:print(row['error'],flush=True)
        finally:
            server.terminate()
            server.wait(timeout=10)
    report=ROOT/'verification/ui-regression.json'
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return 1 if any(row['exit'] for row in results) else 0


if __name__=='__main__':raise SystemExit(main())
