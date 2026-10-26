"""A kernel or module that fails to compile fails kernel/build-all.sh.

build-linux.sh and build-u30air.sh filter make's output through grep and tail; in a pipeline the status was tail's,
so a failed sipa_eth.c still reported success and the previous build's .ko were collected as new. make's status now
decides, and the filtered lines are still printed."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

SCRIPTS = ['kernel/build-linux.sh', 'kernel/build-u30air.sh']


class MakeStatus(unittest.TestCase):
    def test_no_pipeline_hides_make(self):
        for s in SCRIPTS:
            text = (TOP / s).read_text()
            self.assertNotRegex(text, r'\$M Image modules[^\n|]*\| *(grep|tail)', s)
            self.assertIn('$M Image modules > ', text, s)

    def test_failed_make_exits_nonzero(self):
        # the three lines of build-linux.sh, run with a make that fails
        lines = (TOP / 'kernel/build-linux.sh').read_text().splitlines()
        i = next(n for n, l in enumerate(lines) if l.startswith('$M Image modules'))
        with tempfile.TemporaryDirectory() as d:
            body = '\n'.join(lines[i:i + 3]).replace('/work/', d + '/')
            for make, want in (('echo "foo.c:1: error: x"; exit 2', 2), ('echo ok', 0)):
                fake = Path(d) / 'make'
                fake.write_text(make + '\n')
                r = subprocess.run(['bash', '-ec', f'M="sh {fake}"\n{body}\necho done'],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, want, r.stdout + r.stderr)
                if want:
                    self.assertIn('error: x', r.stdout)
                    self.assertNotIn('done', r.stdout)


if __name__ == '__main__':
    unittest.main()
