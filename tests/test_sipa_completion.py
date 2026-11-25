"""Compile the real MU300 completion queue with DMA/ownership fault injection."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
BASE = TOP / 'upstream/modules/sprd_modem/sipa_v3'


def function(text, name):
    match = re.search(r'^static [\w *]+\b' + name + r'\([^;]*?\)\s*\{', text, re.M)
    if not match:
        raise AssertionError(name)
    end, depth = match.end(), 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


@unittest.skipUnless(os.name == 'posix', 'POSIX C fixture')
class Completion(unittest.TestCase):
    def test_reclamation_errors_credits_partial_batches_and_concurrency(self):
        text = (BASE / 'sipa_skb_send.c').read_text()
        body = '\n'.join(function(text, name) for name in (
            'sipa_do_free_skb', 'sipa_sender_can_notify', 'sipa_free_sent_items'))
        with tempfile.TemporaryDirectory(prefix='mu300-completion-') as directory:
            source = Path(directory) / 'completion.c'
            executable = source.with_suffix('')
            fixture = (TOP / 'tests/fixtures/sipa_completion.c').read_text()
            source.write_text(fixture.replace('/* PRODUCTION_FUNCTIONS */', body))
            subprocess.run([
                'clang', '-std=gnu11', '-O1', '-g', '-pthread',
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                str(source), '-o', str(executable)], check=True)
            for scenario in range(12):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(executable), str(scenario)], check=True, timeout=15)


if __name__ == '__main__':
    unittest.main(verbosity=2)
