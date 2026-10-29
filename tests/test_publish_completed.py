"""Interrupted batches publish completed archives without rebuilding firmware."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

TOP=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('publish_build',TOP/'tools/build-release.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)


class Publish(unittest.TestCase):
    def fixture(self, root):
        workspace=root/'workspace';workspace.mkdir()
        pending=root/'release/.building-old';pending.mkdir(parents=True)
        tree=b'a'*40+b'\n'
        record={'profile':'mu300','kernel_release':'7.2.8','package_name':'mu300-fixture',
                'source_commit':'b'*40,'source_fingerprint':hashlib.sha256(tree).hexdigest(),
                'private_version':'fixture','source_dirty':False}
        archive=pending/'mu300-fixture.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('desktop/release.json',json.dumps(record))
        checksum=build.digest(archive)
        (pending/(archive.name+'.sha256')).write_text(checksum+'  '+archive.name+'\n')
        state={'task':'old','state':'failed','pending_output':str(pending),'workspace':str(workspace),
               'steps':[{'stage':'mu300-7.2-package','state':'complete'},
                        {'stage':'mu300-6.18-package','state':'failed'}]}
        path=root/'out/build-logs/old/status.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps(state))
        return workspace,pending,archive,state,path,tree

    def run_fixture(self, fault=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);workspace,pending,archive,state,path,tree=self.fixture(root)
            if fault=='step':state['steps'][0]['state']='failed';path.write_text(json.dumps(state))
            if fault=='checksum':(pending/(archive.name+'.sha256')).write_text('bad  '+archive.name)
            if fault=='extra':(pending/'unverified').write_text('keep')
            with patch.object(build,'TOP',root),patch.object(build,'input_check') as check, \
                    patch.object(build,'verify_archive') as verify, \
                    patch.object(build.subprocess,'check_output',return_value=tree):
                task=build.Task(workspace,'publish')
                if fault:
                    with self.assertRaises(ValueError):
                        build.publish_completed(task,{}, {'mu300-7.2':{}},pending)
                    self.assertTrue(archive.exists())
                    self.assertEqual(state['state'],'failed')
                else:
                    build.publish_completed(task,{}, {'mu300-7.2':{}},pending)
                    published=root/'release'/task.id
                    result=json.loads((published/'BUILD-RUN.json').read_text())
                    self.assertEqual(result['state'],'verified')
                    self.assertEqual(result['selected_profiles'],['mu300-7.2'])
                    self.assertEqual(result['source']['source_commit'],'b'*40)
                    self.assertEqual(result['assets'][0]['file'],archive.name)
                    self.assertFalse(pending.exists())
                    check.assert_called_once();verify.assert_called_once()
                    self.assertEqual(json.loads(path.read_text())['state'],'failed')

    def test_publish_success_keeps_original_source_and_failed_batch_log(self):self.run_fixture()
    def test_failed_step_not_published(self):self.run_fixture('step')
    def test_corrupt_archive_not_published(self):self.run_fixture('checksum')
    def test_unknown_files_not_published(self):self.run_fixture('extra')


if __name__=='__main__':unittest.main()
