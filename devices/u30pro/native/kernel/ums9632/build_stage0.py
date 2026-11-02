"""Build revised PID1 into a fresh tool directory; preserve historical tools."""
from pathlib import Path
import argparse, subprocess, hashlib, json, shutil

P=Path(__file__).resolve().parents[2]
W=P/'build/kernel-port-20261017'
ap=argparse.ArgumentParser()
ap.add_argument('--out',type=Path,required=True)
ap.add_argument('--zig',type=Path,required=True)
ap.add_argument('--reset-probe',action='store_true')
a=ap.parse_args()
a.out.mkdir(exist_ok=False)
source=Path(__file__).parent/'native/stage0_linux.c'
binary=a.out/'u30pro-native-stage0'
cmd=[str(a.zig),'cc','-target','aarch64-linux-musl','-std=c11','-Wall','-Wextra','-Werror','-O2','-static','-s',str(source),'-o',str(binary)]
if a.reset_probe:cmd.insert(2,'-DU30PRO_STAGE0_RESET_PROBE')
r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
(a.out/'compile-stage0.log').write_bytes(r.stdout)
r.check_returncode()
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
d=binary.read_bytes()
assert d[:4]==b'\x7fELF' and int.from_bytes(d[18:20],'little')==183
meta={'source_sha256':h(source),'binary_sha256':h(binary),'bytes':len(d),'command':cmd,'PID1_kernel_arguments':'logged as data, never helper commands or procd arguments','PID1_reset_probe':a.reset_probe}
(a.out/'stage0-manifest.json').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
shutil.copyfile(source,a.out/'stage0_linux.c')
# Keeper is unchanged and copied with its original complete manifest.
old=W/'native-tools-p10'
keeper=json.loads((old/'manifest.json').read_text(encoding='utf-8'))
assert h(old/'u30pro-watchdog-keeper')==keeper['binary_sha256']
assert h(Path(__file__).parent/'native/u30pro_watchdog_keeper.c')==keeper['source_sha256']
for name in ['u30pro-watchdog-keeper','manifest.json']:
 shutil.copyfile(old/name,a.out/name)
print(json.dumps(meta,indent=2))
