"""OTA correctness/security tests. Real filesystem mutations run only in temp roots."""
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import importlib.util
from unittest.mock import patch as mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.ota_engine import FORMAT, Engine, Patch, canonical, descriptor, protected, validate_manifest
from backend.ota_package import analyze, Release


def file(data=b'old', mode=0o644):
    return dict(kind='file', mode=mode, uid=0, gid=0, size=len(data),
                sha256=hashlib.sha256(data).hexdigest(), xattrs={})


def manifest(ops=None):
    return dict(format=FORMAT, family='e5', profile='e5', base_version='e5-r1', target_version='e5-r2',
                kernel_release='6.18.54-e5-test', operations=ops or [dict(path='usr/lib/test', before=file(), after=file(b'new'))])


def package(path, m=None, blobs=None, extra=None):
    m = m or manifest()
    blobs = blobs or {hashlib.sha256(b'new').hexdigest(): b'new'}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', canonical(m))
        for h, data in blobs.items():
            z.writestr('blobs/' + h, data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


def mu_release(root, version, files=None, boot=b'boot'):
    (root / 'desktop').mkdir(parents=True)
    (root / 'payload').mkdir()
    files = files or {'usr/lib/test': b'old'}
    with tarfile.open(root / 'payload/mu300-openwrt.tar.gz', 'w:gz') as tar:
        for name in ('usr', 'usr/lib', 'etc', 'etc/config'):
            t = tarfile.TarInfo(name); t.type=tarfile.DIRTYPE; t.mode=0o755
            tar.addfile(t)
        for name, data in files.items():
            t = tarfile.TarInfo(name);t.size=len(data);t.mode=0o644
            tar.addfile(t, io.BytesIO(data))
    (root / 'payload/boot-linux.img').write_bytes(boot)
    names = ('payload/boot-linux.img', 'payload/mu300-openwrt.tar.gz')
    checks = {n:hashlib.sha256((root / n).read_bytes()).hexdigest() for n in names}
    (root / 'desktop/manifest.json').write_text(json.dumps(dict(magisk=checks, desktop={}, magisk_layout={
        'boot-linux.img':names[0], 'mu300-openwrt.tar.gz':names[1]})))
    (root / 'desktop/release.json').write_text(json.dumps(dict(image_version=version, profile='mu300',
        kernel_release='7.2.8', openwrt='25.12.5')))


class FormatTests(unittest.TestCase):
    def test_protected_state(self):
        for path in ('etc/config/wireless','etc/shadow','lib/firmware/modem.bin','etc/e5/release.json',
                     'var/traffic.db','root/file','lib/apk/db/installed'):
            self.assertTrue(protected(path), path)
        self.assertFalse(protected('usr/libexec/unisoc-modem/action'))
        self.assertFalse(protected('etc/init.d/e5-modemd'))
        self.assertFalse(protected('www/luci-static/resources/view/mu300/locks.js'))
        self.assertFalse(protected('lib/modules/current/wifi.ko'))
        self.assertFalse(protected('usr/lib/unisoc-ota/engine.py'))

    def test_path_traversal(self):
        for name in ('../etc/shadow','/etc/shadow','usr//lib/x','usr/./a','usr/a/../x','usr\\lib\\x','usr/x\0y'):
            m=manifest();m['operations'][0]['path']=name
            with self.assertRaises(ValueError):validate_manifest(m)

    def test_duplicate_operation(self):
        m=manifest();m['operations']*=2
        with self.assertRaises(ValueError):validate_manifest(m)

    def test_regular_file_to_symlink_transition(self):
        m=manifest();m['operations'][0]['after']=dict(kind='symlink',mode=0o777,uid=0,gid=0,target='/tmp',xattrs={})
        self.assertEqual(validate_manifest(m), m)

    def test_directory_removal(self):
        m=manifest();m['operations'][0].update(before=dict(kind='dir',mode=0o755,uid=0,gid=0,xattrs={}),after=None)
        with self.assertRaises(ValueError):validate_manifest(m)

    def test_extra_archive_program_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=package(Path(d)/'p.ota',extra={'install.sh':b'rm -rf /'})
            with self.assertRaises(ValueError):Patch(p)

    def test_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p=package(Path(d)/'p.ota',blobs={file(b'new')['sha256']:b'bad'})
            with self.assertRaises(ValueError):Patch(p)

    def test_trailing_duplicate_zip_entry(self):
        with tempfile.TemporaryDirectory() as d:
            p=package(Path(d)/'p.ota')
            with zipfile.ZipFile(p,'a') as z:z.writestr('manifest.json',canonical(manifest()))
            with self.assertRaises(ValueError):Patch(p)

    def test_partial_kernel_update_rejected(self):
        m=manifest();m['boot']={'sha256':'a'*64}
        with self.assertRaises(ValueError):validate_manifest(m)


class GenerationTests(unittest.TestCase):
    def test_boot_changes_do_not_block_file_patch_export(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            mu_release(d/'a','mu300-r1',boot=b'old boot')
            mu_release(d/'b','mu300-r2',{'usr/lib/test':b'new'},boot=b'new boot')
            result=analyze(d/'a',d/'b',d/'patch.ota')
            self.assertTrue(result['can_export'],result['blockers'])
            with Patch(d/'patch.ota') as patch:
                self.assertEqual(patch.manifest['operations'][0]['path'],'usr/lib/test')

    def test_kernel_version_labels_do_not_block_file_patch_export(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            mu_release(d/'a','mu300-r1')
            mu_release(d/'b','mu300-r2',{'usr/lib/test':b'new'},boot=b'another boot')
            meta=d/'b/desktop/release.json'
            values=json.loads(meta.read_text());values['kernel_release']='6.18.54'
            meta.write_text(json.dumps(values))
            result=analyze(d/'a',d/'b',d/'patch.ota')
            self.assertTrue(result['can_export'],result['blockers'])

    def test_first_engine_install_is_included(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            mu_release(d/'a','mu300-r1')
            mu_release(d/'b','mu300-r2',{'usr/lib/test':b'new','usr/lib/unisoc-ota/engine.py':b'engine'})
            result=analyze(d/'a',d/'b')
            self.assertTrue(result['can_export'])
            self.assertIn('usr/lib/unisoc-ota/engine.py', [o['path'] for o in result['manifest']['operations']])

    @unittest.skipUnless(os.name=='posix','Linux release staging permissions')
    def test_stage_built_in_plugin_and_preserve_state(self):
        import runpy
        repo=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'etc/unisoc-ota').mkdir(parents=True)
            (root/'etc/unisoc-ota/current.json').write_text('preserve existing state')
            runpy.run_path(str(repo/'installer/stage-ota.py'))['stage'](root)
            self.assertEqual((root/'etc/unisoc-ota/current.json').read_text(),'preserve existing state')
            source=repo/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
            self.assertEqual((root/'usr/lib/unisoc-ota/engine.py').read_bytes(),source.read_bytes().replace(b'\r\n',b'\n'))
            self.assertTrue((root/'etc/rc.d/S06unisoc-ota').is_symlink())
            self.assertTrue((root/'etc/init.d/unisoc-ota').stat().st_mode & stat.S_IXUSR)
            acl=json.loads((root/'usr/share/rpcd/acl.d/unisoc-ota.json').read_text())
            self.assertNotIn('exec',acl['luci-app-unisoc-ota']['write']['ubus'])

    def test_web_assets_and_core_runtime_are_updatable(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            mu_release(d/'a','mu300-r1',{'www/style.css':b'old'})
            mu_release(d/'b','mu300-r2',{'www/style.css':b'new'})
            result=analyze(d/'a',d/'b')
            self.assertTrue(result['can_export'])
            self.assertEqual(result['manifest']['operations'][0]['path'],'www/style.css')
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            mu_release(d/'a','mu300-r1',{'bin/busybox':b'old'})
            mu_release(d/'b','mu300-r2',{'bin/busybox':b'new'})
            result=analyze(d/'a',d/'b')
            self.assertTrue(result['can_export'])
    def test_native_tool_refresh_verifies_owned_files(self):
        installer=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('ota_builder_test',installer/'build-ota.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);artifact=d/'artifact';(artifact/'tools/backend').mkdir(parents=True)
            (artifact/'tools/backend/openwrt-backend.exe').write_bytes(b'backend')
            (artifact/'OpenWrt-Installer.exe').write_bytes(b'gui')
            output=d/'tool'
            argv=['build-ota.py','--artifact',str(artifact),'--output',str(output)]
            with mock.object(builder,'native_platform',return_value='windows-x64'),mock.object(builder,'validate_artifact'),mock.object(builder,'sources',return_value={'fixture':'hash'}):
                with mock.object(sys,'argv',argv):builder.main()
                m=json.loads((output/'diff-tool.json').read_text())
                self.assertNotIn('diff-tool.json',m['files'])
                with mock.object(sys,'argv',argv+['--refresh']):builder.main()
                m=json.loads((output/'diff-tool.json').read_text())
                self.assertNotIn('diff-tool.json',m['files'])
                (output/'personal.txt').write_text('keep me')
                with mock.object(sys,'argv',argv+['--refresh']):
                    with self.assertRaises(ValueError):builder.main()
                self.assertEqual((output/'personal.txt').read_text(),'keep me')

    def test_package_changes_create_a_merge_operation(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            for n in (1,2):
                db=f'P:libc\nV:{n}\nA:aarch64\nD:\n\n'.encode()
                mu_release(d/str(n),'mu300-r'+str(n),{'usr/lib/test':str(n).encode(),'lib/apk/db/installed':db})
            result=analyze(d/'1',d/'2')
            self.assertTrue(result['can_export'])
            op=next(o for o in result['manifest']['operations'] if o['path']=='lib/apk/db/installed')
            self.assertEqual(op['merge'],'apk-db')

    def test_generate_tar_release_preserves_configs_deduplicates(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);a=d/'old';b=d/'new'
            mu_release(a,'mu300-r1', {'usr/lib/test':b'old','etc/config/wireless':b'old'})
            mu_release(b,'mu300-r2', {'usr/lib/test':b'new','usr/lib/alias':b'new','etc/config/wireless':b'new'})
            p=d/'p.ota';result=analyze(a,b,p)
            self.assertTrue(result['can_export'])
            self.assertEqual(result['files'],2)
            self.assertEqual(result['payload_bytes'],3)
            self.assertIn('etc/config/wireless',result['manifest']['preserved'])
            with Patch(p) as patch:self.assertEqual(len(patch.archive.namelist()),2)

    def test_zip_inputs_supported(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);archives=[]
            for i in (1,2):
                root=d/str(i);mu_release(root,'mu300-r'+str(i),{'usr/lib/test':str(i).encode()})
                z=d/(str(i)+'.zip');archives.append(z)
                with zipfile.ZipFile(z,'w') as out:
                    for p in root.rglob('*'):
                        if p.is_file():out.write(p,'release/'+p.relative_to(root).as_posix())
            self.assertTrue(analyze(*archives)['can_export'])

    def test_corrupt_full_release_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);mu_release(d/'a','mu300-r1');(d/'a/payload/boot-linux.img').write_bytes(b'bad')
            with self.assertRaises(ValueError):
                with Release(d/'a'):pass


@unittest.skipUnless(sys.platform == 'linux', 'Device engine requires Linux permissions, xattrs and durable rename semantics')
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'usr/lib').mkdir(parents=True)
        (self.root/'usr/lib/test').write_bytes(b'old')
        (self.root/'usr/lib/test').chmod(0o644)
        (self.root/'etc/e5').mkdir(parents=True)
        (self.root/'etc/e5/release.json').write_text(json.dumps(dict(profile='e5',image_version='e5-r1',kernel_release='6.18.54-e5-test')))
        self.e=Engine(self.root)
        self.m=manifest();self.m['operations'][0]['before']=descriptor(self.root/'usr/lib/test')
        self.m['operations'][0]['after'].update(uid=os.getuid(),gid=os.getgid())
        self.p=package(self.root/'p.ota',self.m)

    def test_apply_preserves_plugins_and_user_data(self):
        (self.root/'usr/lib/extra-plugin').write_bytes(b'keep')
        (self.root/'etc/config').mkdir();(self.root/'etc/config/network').write_bytes(b'user')
        with Patch(self.p) as patch:result=self.e.apply(patch)
        self.assertEqual(result['status'],'complete')
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'new')
        self.assertEqual((self.root/'usr/lib/extra-plugin').read_bytes(),b'keep')
        self.assertEqual((self.root/'etc/config/network').read_bytes(),b'user')
        self.assertEqual(self.e.identity()['version'],'e5-r2')

    def test_different_kernel_label_applies_without_relabeling_device_kernel(self):
        self.m['kernel_release']='7.2.8-different'
        package(self.p,self.m)
        with Patch(self.p) as patch:
            self.assertTrue(self.e.inspect(patch)['applicable'])
            self.assertEqual(self.e.apply(patch)['status'],'complete')
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'new')
        self.assertEqual(self.e.identity()['kernel_release'],'6.18.54-e5-test')
        self.assertEqual(self.e.identity()['version'],'e5-r2')

    def test_local_system_hotfix_is_backed_up_and_updated(self):
        (self.root/'usr/lib/test').write_bytes(b'custom')
        with Patch(self.p) as patch:
            self.assertTrue(self.e.inspect(patch)['applicable'])
            self.e.apply(patch)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'new')
        data=json.loads((self.e.state/'journal.json').read_text())
        self.assertEqual((self.e.state/data['backup']/'0').read_bytes(),b'custom')

    def test_wrong_device_base(self):
        self.m['profile']='m8';package(self.p,self.m)
        with Patch(self.p) as patch:
            with self.assertRaises(ValueError):self.e.inspect(patch)

    def test_local_package_version_does_not_block_unrelated_update(self):
        self.m['base_packages']={'libc':{'V':'1','A':'aarch64','D':''}}
        package(self.p,self.m)
        with Patch(self.p) as patch:
            self.assertEqual(self.e.inspect(patch)['package_conflicts'],[])
            self.e.apply(patch)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'new')

    def test_manual_rollback(self):
        with Patch(self.p) as patch:self.e.apply(patch)
        engine=Path(__file__).resolve().parents[2]/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
        result=subprocess.run([sys.executable,str(engine),'rollback','--root',str(self.root)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'old')
        self.assertEqual(self.e.identity()['version'],'e5-r1')

    def test_rollback_conflict_does_not_clobber_local_changes(self):
        with Patch(self.p) as patch:self.e.apply(patch)
        (self.root/'usr/lib/test').write_bytes(b'local edit')
        engine=Path(__file__).resolve().parents[2]/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/engine.py'
        result=subprocess.run([sys.executable,str(engine),'rollback','--root',str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'local edit')

    def test_rpc_upload_lock_and_arbitrary_paths(self):
        import importlib.util
        import fcntl
        directory=Path(__file__).resolve().parents[2]/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota'
        sys.path.insert(0,str(directory));self.addCleanup(lambda:sys.path.remove(str(directory)))
        spec=importlib.util.spec_from_file_location('ota_test_rpc',directory/'rpc.py')
        rpc=importlib.util.module_from_spec(spec);spec.loader.exec_module(rpc)
        environment=mock.dict(os.environ,{'UNISOC_OTA_RAM':'/'})
        environment.start();self.addCleanup(environment.stop)
        self.e.state.mkdir(parents=True)
        rpc.STATE=self.e.state;rpc.UPLOAD=self.e.state/'upload.ota';rpc.JOB=self.e.state/'job.json';rpc.Engine=lambda:self.e
        rpc.UPLOAD.write_bytes(self.p.read_bytes())
        with Patch(self.p) as patch:review_id=patch.id
        with self.assertRaises(ValueError):rpc.dispatch('start','/tmp/other.ota')
        with (self.e.state/'job.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):rpc.dispatch('start',review_id=review_id)
            self.assertTrue(rpc.UPLOAD.exists())
        with mock.object(rpc.subprocess,'Popen') as launch:
            self.assertEqual(rpc.dispatch('start',review_id=review_id)['status'],'running')
            self.assertTrue(launch.call_args.kwargs['start_new_session'])
            self.assertTrue(launch.call_args.kwargs['pass_fds'])
        self.assertFalse(rpc.UPLOAD.exists())
        other=copy.deepcopy(self.m);other['target_version']='e5-r3'
        package(rpc.UPLOAD,other)
        with mock.object(rpc.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'reviewed'):
                rpc.dispatch('start',review_id=review_id)
            launch.assert_not_called()
        self.assertTrue(rpc.UPLOAD.exists())

    def test_ram_rpc_reboot_uses_isolated_busybox(self):
        directory=Path(__file__).resolve().parents[2]/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota'
        sys.path.insert(0,str(directory));self.addCleanup(lambda:sys.path.remove(str(directory)))
        spec=importlib.util.spec_from_file_location('ota_reboot_rpc',directory/'rpc.py')
        rpc=importlib.util.module_from_spec(spec);spec.loader.exec_module(rpc)
        rpc.STATE=self.e.state
        with mock.dict(os.environ,{'UNISOC_OTA_RAM':'/'}), mock.object(rpc.subprocess,'Popen') as launch:
            self.assertEqual(rpc.dispatch('reboot')['status'],'rebooting')
            self.assertEqual(launch.call_args.args[0],['/ota/busybox','reboot'])

    def test_recovery_cleans_partial_temp_file(self):
        with Patch(self.p) as patch:
            def failure(*args):
                (self.root/'usr/lib/.test.unisoc-ota-new').write_bytes(b'partial')
                raise RuntimeError('write failed')
            original=self.e.install_descriptor
            count=0
            def once(*args,**kwargs):
                nonlocal count
                count+=1
                return failure(*args) if count==1 else original(*args,**kwargs)
            self.e.install_descriptor=once
            with self.assertRaises(RuntimeError):self.e.apply(patch)
        self.assertFalse((self.root/'usr/lib/.test.unisoc-ota-new').exists())
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'old')

    def test_deleted_file_can_be_restored(self):
        self.m['operations'][0]['after']=None;package(self.p,self.m,blobs={})
        # No blobs are expected for a deletion-only hotfix.
        with zipfile.ZipFile(self.p,'w') as z:z.writestr('manifest.json',canonical(self.m))
        with Patch(self.p) as patch:self.e.apply(patch)
        self.assertFalse((self.root/'usr/lib/test').exists())
        data=json.loads((self.e.state/'journal.json').read_text());data['status']='rolling_back'
        from backend.ota_engine import atomic_json
        atomic_json(self.e.state/'journal.json',data)
        with self.e.lock():self.e.recover()
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'old')

    def test_symlink_parent_refused(self):
        (self.root/'usr/alias').symlink_to('lib')
        self.m['operations'][0]['path']='usr/alias/test';package(self.p,self.m)
        with Patch(self.p) as patch:
            with self.assertRaises(ValueError):self.e.inspect(patch)

    def test_apply_failure_rolls_back(self):
        def fail(*args):raise RuntimeError('simulated service failure')
        with Patch(self.p) as patch:
            with self.assertRaises(RuntimeError):self.e.apply(patch,fail)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'old')
        self.assertEqual(self.e.identity()['version'],'e5-r1')
        self.assertEqual(self.e.status()['transaction']['status'],'rolled_back')

    def test_interrupted_write_recovers_on_new_process(self):
        with Patch(self.p) as patch:
            original=self.e.install_descriptor
            def powercut(*args,**kwargs):
                original(*args,**kwargs)
                os._exit(77)
            pid=os.fork()
            if pid==0:
                self.e.install_descriptor=powercut
                self.e.apply(patch)
                os._exit(0)
            _,s=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(s),77)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'new')
        with self.e.lock():self.e.recover()
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'old')
        self.assertEqual(self.e.identity()['version'],'e5-r1')

    def test_duplicate_apply_rejected(self):
        with Patch(self.p) as patch:
            self.e.apply(patch)
            with self.assertRaises(ValueError):self.e.apply(patch)

    def test_no_space_no_writes(self):
        class Disk:free=0
        with mock('backend.ota_engine.shutil.disk_usage',return_value=Disk()),Patch(self.p) as patch:
            with self.assertRaises(ValueError):self.e.apply(patch)
        self.assertEqual((self.root/'usr/lib/test').read_bytes(),b'old')

    def test_new_directories_removed_on_rollback(self):
        directory=dict(kind='dir',mode=0o755,uid=os.getuid(),gid=os.getgid(),xattrs={})
        self.m['operations'].insert(0,dict(path='usr/new',before=None,after=directory))
        self.m['operations'].append(dict(path='usr/new/x',before=None,after=file(b'new')))
        for o in self.m['operations']:
            if o['after']:o['after'].update(uid=os.getuid(),gid=os.getgid())
        package(self.p,self.m)
        with Patch(self.p) as patch:
            with self.assertRaises(RuntimeError):
                self.e.apply(patch,lambda a,b,c: (_ for _ in ()).throw(RuntimeError('failure')) if a==3 else None)
        self.assertFalse((self.root/'usr/new').exists())


if __name__=='__main__':unittest.main()
