"""Interrupted publication must use a committed source fingerprint, not status flags."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('release_runner', Path(__file__).parents[1]/'build-release.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class Publication(unittest.TestCase):
    def publish(self, *, dirty=True, changed_source=False, corrupt_archive=False):
        with tempfile.TemporaryDirectory(prefix='mu300-publication-test-') as temporary:
            top=Path(temporary)
            workspace=top/'workspace'
            source=top/'release/.building-old'
            source.mkdir(parents=True)
            tree=b'0123456789abcdef\n'
            record=dict(kernel_release='7.2.8',package_name='mu300-fixture',
                        source_dirty=dirty,source_commit='a'*40,
                        source_fingerprint=hashlib.sha256(tree).hexdigest(),private_version='fixture')
            if changed_source:record['source_fingerprint']='b'*64
            archive=source/'mu300-fixture.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('desktop/release.json',json.dumps(record))
            checksum='c'*64 if corrupt_archive else runner.digest(archive)
            archive.with_name(archive.name+'.sha256').write_text(checksum+'  '+archive.name+'\n')
            runner.write_json(top/'out/build-logs/old/status.json',dict(
                state='failed',task='old',pending_output=str(source),workspace=str(workspace),
                steps=[dict(stage='mu300-7.2-package',state='complete')]))
            class Task:
                id='completed'
                data={}
                def save(self,**fields):self.data.update(fields)
            task=Task()
            task.workspace=workspace
            with patch.object(runner,'TOP',top), patch.object(runner.subprocess,'check_output',return_value=tree), \
                 patch.object(runner,'verify_archive') as verify, patch.object(runner,'input_check') as inputs:
                runner.publish_completed(task,{}, {'mu300-7.2':{}},source)
                verify.assert_called_once_with(archive, require_diff=True, require_android=True)
                inputs.assert_called_once()
                published=json.loads((top/'release/completed/BUILD-RUN.json').read_text())
                self.assertEqual(published['source']['source_dirty'],dirty)
                self.assertEqual(task.data['state'],'complete')

    def test_generated_submodule_status_does_not_reject_exact_commit(self):
        self.publish(dirty=True)

    def test_clean_committed_archive_still_publishes(self):
        self.publish(dirty=False)

    def test_source_edits_are_rejected_even_when_status_claims_clean(self):
        with self.assertRaisesRegex(ValueError,'fingerprint differs'):
            self.publish(dirty=False,changed_source=True)

    def test_dirty_status_cannot_bypass_source_fingerprint(self):
        with self.assertRaisesRegex(ValueError,'fingerprint differs'):
            self.publish(dirty=True,changed_source=True)

    def test_corrupted_archive_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'checksum differs'):
            self.publish(corrupt_archive=True)


if __name__=='__main__':unittest.main()
