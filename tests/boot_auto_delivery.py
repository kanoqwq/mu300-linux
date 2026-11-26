"""Real diff generation/application and ordinary config migration on tiny fixtures."""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

TOP=next(p for p in Path(__file__).resolve().parents if (p/'rootfs/overlay').is_dir())
sys.path.insert(0,str(TOP/'installer'))
from backend.ota_engine import Engine,Patch
from backend.ota_package import analyze

def put(root,files):
    for name,data in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        p.chmod(0o755 if data.startswith(b'#!') else 0o644)

def archive(path,files):
    with tarfile.open(path,'w:gz') as tar:
        parents=sorted({str(p) for n in files for p in Path(n).parents if str(p)!='.'},key=lambda n:(n.count('/'),n))
        for name in parents:
            t=tarfile.TarInfo(name);t.type=tarfile.DIRTYPE;t.mode=0o755;tar.addfile(t)
        for name,data in files.items():
            t=tarfile.TarInfo(name);t.size=len(data);t.mode=0o755 if data.startswith(b'#!') else 0o644
            tar.addfile(t,io.BytesIO(data))

def release(path,profile,version,files,e5):
    meta=dict(profile=profile,image_version=version,kernel_release='6.18.54-fixture' if e5 else '6.12.94-fixture',openwrt='25.12.5')
    if e5:
        folder=path/'files';folder.mkdir(parents=True)
        tree=path/'tiny-tree';put(tree,files)
        image=path/'tiny.ext4'
        subprocess.run(['mkfs.ext4','-q','-F','-d',str(tree),str(image),'4096'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        with image.open('rb') as src,gzip.open(folder/'openwrt.ext4.gz','wb') as dst:shutil.copyfileobj(src,dst)
        (folder/'boot.img').write_bytes(b'unchanged fixture boot')
        (folder/'RELEASE.json').write_text(json.dumps(meta));(folder/'device-profile.json').write_text(json.dumps({'id':profile}))
        names=['openwrt.ext4.gz','boot.img','RELEASE.json','device-profile.json']
        (folder/'SHA256SUMS').write_text(''.join(hashlib.sha256((folder/n).read_bytes()).hexdigest()+'  files/'+n+'\n' for n in names))
    else:
        (path/'desktop').mkdir(parents=True);(path/'payload').mkdir()
        archive(path/'payload/mu300-openwrt.tar.gz',files)
        (path/'payload/boot-linux.img').write_bytes(b'unchanged fixture boot')
        names=['payload/mu300-openwrt.tar.gz','payload/boot-linux.img']
        checks={n:hashlib.sha256((path/n).read_bytes()).hexdigest() for n in names}
        (path/'desktop/release.json').write_text(json.dumps(meta))
        (path/'desktop/manifest.json').write_text(json.dumps({'magisk':checks,'desktop':{},'magisk_layout':{
            'mu300-openwrt.tar.gz':names[0],'boot-linux.img':names[1]}}))

def sources_for(repo):
    e5=(repo/'rootfs/overlay/opt/e5/e5-next-boot').exists()
    profile='e5' if e5 else 'u30pro' if (repo/'devices/u30pro/profile.json').exists() else 'mu300'
    adapter='opt/e5/e5-next-boot' if e5 else 'opt/mu300/bin/mu300-next-boot'
    service='etc/init.d/e5-boot-ok' if e5 else 'etc/init.d/mu300-post'
    sources={adapter:'rootfs/overlay/'+adapter,service:'openwrt/overlay/'+service,
        'usr/libexec/unisoc-boot-default':'openwrt/overlay/usr/libexec/unisoc-boot-default',
        'usr/share/ucode/unisoc/boot-default.uc':'openwrt/overlay/usr/share/ucode/unisoc/boot-default.uc'}
    if profile=='u30pro':sources[service]='devices/u30pro/overlay/etc/init.d/mu300-post'
    return e5,profile,adapter,service,sources

def check(repo,baseline=None):
    e5,profile,adapter,service,sources=sources_for(repo)
    code={name:(repo/path).read_bytes() for name,path in sources.items()}
    old={}
    for name,path in sources.items():
        if baseline is not None:
            saved=baseline[repo.name]['files'].get(path)
            if saved is not None:old[name]=base64.b64decode(saved)
            continue
        p=subprocess.run(['git','-c','safe.directory='+str(repo),'-C',str(repo),'show','HEAD:'+path],capture_output=True)
        if p.returncode==0:old[name]=p.stdout
    assert adapter in old and service in old,'Require the real pre-feature boot adapter/startup baseline'
    conf='etc/e5linux/default-boot' if e5 else 'etc/mu300/default-boot'
    user={conf:b'android\n','etc/config/wireless':b'user Wi-Fi settings','etc/config/unisoc_modem':b'user RF settings'}
    if e5:user['etc/e5-infoscreen/wallpaper.png']=b'user wallpaper'
    else:user['root/my-plugin/config']=b'user plugin data'
    with tempfile.TemporaryDirectory(prefix='boot-auto-delivery-') as d:
        d=Path(d);live=d/'live';put(live,{**old,**user})
        identity=live/('etc/e5/release.json' if e5 else 'etc/mu300/release.json');identity.parent.mkdir(parents=True,exist_ok=True)
        identity.write_text(json.dumps(dict(profile=profile,image_version='auto-0',kernel_release='6.18.54-fixture' if e5 else '6.12.94-fixture')))
        versions=[old,code,{name:data+b'\n' for name,data in code.items()}]
        for i,files in enumerate(versions):release(d/str(i),profile,'auto-'+str(i),files,e5)
        for i in [1,2]:
            output=d/(str(i)+'.ota');result=analyze(d/str(i-1),d/str(i),output)
            assert result['can_export'],result['blockers']
            with Patch(output) as patch:assert Engine(live).apply(patch)['status']=='complete'
            for name,data in versions[i].items():assert (live/name).read_bytes()==data,name
            for name,data in user.items():assert (live/name).read_bytes()==data,name
        engine=repo/'installer/runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
        p=subprocess.run([sys.executable,str(engine),'rollback','--root',str(live)],capture_output=True,text=True)
        assert p.returncode==0,p.stdout+p.stderr
        for name,data in code.items():assert (live/name).read_bytes()==data,name
        for name,data in user.items():assert (live/name).read_bytes()==data,name
        # Ordinary-update preservation: use each family's production migration.
        for i in [1,2]:
            incoming=d/'ordinary';put(incoming,versions[i])
            if e5:
                script=(repo/'openwrt/device-stage-part-update.sh').read_text()
                fragment=script[script.index('settings=\n'):script.index('# END image-owned-settings-filter')]
                fragment=fragment.replace('/.e5-update/settings.tar',str(d/'settings.tar'))
                p=subprocess.run(['sh','-ec',fragment],cwd=live,env=dict(os.environ,E5_NV_POLICY='preserve'),capture_output=True,text=True)
                assert p.returncode==0,p.stdout+p.stderr
                subprocess.run(['tar','-xpf',str(d/'settings.tar'),'-C',str(incoming)],check=True)
                for name,data in user.items():assert (incoming/name).read_bytes()==data,name
                for name,data in versions[i].items():assert (incoming/name).read_bytes()==data,name
                shutil.rmtree(incoming)
            else:
                disk=d/'disk';disk.mkdir(exist_ok=True);oldroot=disk/'openwrt'
                if not oldroot.exists():shutil.copytree(live,oldroot)
                stage=disk/'.mu300-update';stage.mkdir(exist_ok=True)
                archive(stage/'mu300-openwrt-rootfs.tar.gz',dict(versions[i],**{'sbin/init':b'#!/bin/sh\n'}))
                script=repo/'rootfs/overlay/opt/mu300/bin/mu300-update'
                command='. '+str(script)+'; sync() { :; }; is_root() { false; }; apply_one openwrt boot-auto'
                p=subprocess.run(['sh','-c',command],env=dict(os.environ,MU300_LIB='1',MU300_DISK=str(disk)),capture_output=True,text=True)
                assert p.returncode==0,p.stdout+p.stderr
                for name,data in user.items():assert (oldroot/name).read_bytes()==data,name
                for name,data in versions[i].items():assert (oldroot/name).read_bytes()==data,name
                shutil.rmtree(disk/'openwrt.old');shutil.rmtree(incoming)
    print(repo.name+': first/follow-up diff, rollback and ordinary configuration migration passed',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--repo',action='append',type=Path)
    parser.add_argument('--baseline',type=Path,help='Explicit native-Git baseline export for Windows worktrees')
    parser.add_argument('--export-baseline',type=Path,help='Export HEAD fixtures with native Git; no test execution')
    args=parser.parse_args()
    if args.export_baseline:
        result={}
        for repo in args.repo or [TOP]:
            files={}
            for path in sources_for(repo)[4].values():
                p=subprocess.run(['git','-C',str(repo),'show','HEAD:'+path],capture_output=True)
                if not p.returncode:files[path]=base64.b64encode(p.stdout).decode()
            result[repo.name]={'head':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),'files':files}
        args.export_baseline.write_text(json.dumps(result))
        sys.exit(0)
    baseline=json.loads(args.baseline.read_text()) if args.baseline else None
    for repo in args.repo or [TOP]:check(repo,baseline)
