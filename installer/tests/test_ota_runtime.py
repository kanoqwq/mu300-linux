"""Execute the generated bootstrap with no device utilities in PATH."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.ota_runtime import bootstrap


@unittest.skipUnless(os.name == 'posix', 'Execute POSIX bootstrap shell')
class BootstrapTests(unittest.TestCase):
    def test_first_and_repeated_setup_without_mountpoint_or_device_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            home = base/('unisoc-ota-'+'a'*64); home.mkdir()
            mounts = base/'mounts'; mounts.write_text('')
            log = base/'commands'
            # This stand-in deliberately implements only the packaged applets;
            # absent applets, including mountpoint, fail instead of succeeding.
            bb = home/'busybox'
            # Ignore the generated target PYTHONHOME only in this host-side stub.
            bb.write_text('#!'+sys.executable+' -E\n'+r'''
import hashlib,json,os,pathlib,shutil,sys,zipfile
app,*args=sys.argv[1:]
with open(os.environ['TEST_LOG'],'a') as f: f.write(json.dumps([app,*args])+'\n')
if app=='mkdir':
 for p in args[1:]: pathlib.Path(p).mkdir(parents=True,exist_ok=True)
elif app=='unzip':
 with zipfile.ZipFile(args[1]) as z:
  for n in args[2:-2]: z.extract(n,args[-1])
elif app=='cat': sys.stdout.write(sys.stdin.read())
elif app=='sha256sum':
 for line in pathlib.Path(args[1]).read_text().splitlines():
  h,n=line.split(); assert hashlib.sha256(pathlib.Path(n).read_bytes()).hexdigest()==h
elif app=='cp': shutil.copyfile(*args)
elif app=='chmod': os.chmod(args[1],int(args[0],8))
elif app=='mv': os.replace(*args)
elif app=='rm': shutil.rmtree(args[-1])
elif app=='mount':
 with open(os.environ['TEST_MOUNTS'],'a') as f: f.write('source '+args[-1]+' tmpfs rw 0 0\n')
elif app=='chroot': print(json.dumps({'command':args[-1]}))
else: raise SystemExit('unsupported applet: '+app)
''')
            bb.chmod(0o700)
            content = b'portable interpreter fixture'
            h = hashlib.sha256(content).hexdigest()
            runtime = {'usr/bin/python3':dict(kind='file',mode=0o755,sha256=h)}
            script = bootstrap(runtime, {}).decode()
            # Only redirect generated fixed paths to the owned temporary root.
            script = script.replace('/tmp/unisoc-ota-', str(base)+'/unisoc-ota-')
            script = script.replace('/proc/mounts', str(mounts))
            program = home/'bootstrap.sh'; program.write_text(script)
            archive = base/'patch.ota'
            with zipfile.ZipFile(archive, 'w') as z: z.writestr('blobs/'+h, content)
            env = dict(os.environ, PATH='/no-device-tools', TEST_LOG=str(log), TEST_MOUNTS=str(mounts))
            for _ in range(2):
                result = subprocess.run(['/bin/sh',str(program),str(archive),'status',str(home)],
                                        env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(result.stderr,'')
                self.assertEqual(json.loads(result.stdout)['command'],'status')
            commands=[json.loads(l) for l in log.read_text().splitlines()]
            self.assertEqual(sum(c[0]=='mount' for c in commands),7)
            self.assertFalse(any(c[0]=='mountpoint' for c in commands))
            self.assertEqual((home/'root/usr/bin/python3').read_bytes(),content)
