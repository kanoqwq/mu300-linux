"""Check the shipped backend, ext4 parser and combined desktop/web export."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_ota import mu_release
from backend.ota_engine import Patch


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime',type=Path)
    p.add_argument('--e5-release',type=Path)
    p.add_argument('--native',action='store_true',help='Also launch the Windows GUI and exported updater for 30 seconds each')
    a=p.parse_args()
    exe=a.runtime/'tools/backend/openwrt-backend.exe'
    repo=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='ota-frozen-',dir=repo/'work') as directory:
        d=Path(directory)
        mu_release(d/'old','mu300-r1')
        mu_release(d/'new','mu300-r2',{'usr/lib/test':b'new'})
        requests=[dict(protocol=1,id=1,command='ota_analyze',params={'old':str(d/'old'),'new':str(d/'new')}),
                  dict(protocol=1,id=2,command='ota_export',params={'output':str(d/'patch.zip')})]
        if a.e5_release:
            requests.append(dict(protocol=1,id=3,command='ota_analyze',params={'old':str(a.e5_release),'new':str(a.e5_release)}))
        result=subprocess.run([str(exe),'--package-root',str(a.runtime),'--data-dir',str(d/'state')],
                              input=''.join(json.dumps(r)+'\n' for r in requests),encoding='utf-8',
                              capture_output=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert result.returncode==0,result.stderr
        replies={v['id']:v['data'] for line in result.stdout.splitlines() if (v:=json.loads(line)).get('type')=='response'}
        assert replies[1]['ok'] and replies[1]['result']['can_export'],replies
        assert replies[2]['ok'],replies
        with zipfile.ZipFile(d/'patch.zip') as z:
            names=z.namelist()
            assert names.count('update.ota')==1
            assert 'OpenWrt-Updater.exe' in names and 'OpenWrt-Updater.py' in names
            assert not any(n.endswith(('boot.img','boot-linux.img','rootfs.tar.gz','openwrt.ext4.gz')) for n in names)
            with Patch(io.BytesIO(z.read('update.ota'))) as patch:
                assert patch.manifest['target_version']=='mu300-r2'
        if a.e5_release:
            assert replies[3]['ok'],replies[3]
            assert replies[3]['result']['manifest']['family']=='e5'
        if a.native:
            from ota_native_smoke import check_launch
            check_launch(a.runtime/'OpenWrt-Diff-Studio.exe')
            with zipfile.ZipFile(d/'patch.zip') as z:
                z.extractall(d/'desktop')
            check_launch(d/'desktop/OpenWrt-Updater.exe','ota.html')
        print(json.dumps({'ok':True,'combined_zip_bytes':(d/'patch.zip').stat().st_size,
                          'raw_hotfix_bytes':len(zipfile.ZipFile(d/'patch.zip').read('update.ota')),
                          'full_e5_parser':bool(a.e5_release)}))


if __name__=='__main__':main()
