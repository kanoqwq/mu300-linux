"""Actual receive producers: publication ownership, partial failure and retry."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

TOP=Path(__file__).resolve().parents[1]
BASE=Path(os.environ.get('SIPA_TEST_SOURCE',TOP/'upstream/modules/sprd_modem/sipa_v3'))


def function(text,name):
    m=re.search(r'(?m)^(?:static )?[\w *]+\b'+name+r'\([^;]*?\)\s*\{',text)
    if not m: raise AssertionError(name)
    end,depth=m.end(),1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[m.start():end]


@unittest.skipUnless(os.name=='posix','POSIX C fixture')
class ReceiveBuffers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='sipa-buffers-')
        cls.addClassCleanup(cls.temp.cleanup)
        src=Path(cls.temp.name)/'receive.c';cls.exe=src.with_suffix('')
        text=(BASE/'sipa_skb_recv.c').read_text()
        bodies='\n'.join(function(text,n) for n in ('sipa_put_recv_array_node','sipa_build_rx_skb','sipa_prepare_free_node',
            'sipa_get_recv_array_node','sipa_fill_free_fifo_node','sipa_fill_free_node_by_alloc_skb','sipa_fill_remaining_node','sipa_recv_skb'))
        src.write_text((TOP/'tests/fixtures/sipa_receive_buffers.c').read_text().replace('/* PRODUCTION_FUNCTIONS */',bodies))
        result=subprocess.run(['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined',
                               '-fno-sanitize-recover=all',str(src),'-o',str(cls.exe)],capture_output=True,text=True)
        if result.returncode: raise AssertionError(result.stderr)

    def scenario(self,producer,fault,after=0):
        result=subprocess.run([str(self.exe),str(producer),str(fault),str(after)],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_success_and_batch_boundary(self):
        for producer in range(4):
            with self.subTest(producer=producer):self.scenario(producer,0)

    def test_heap_allocation_and_mapping_failures_are_retryable(self):
        for fault in (1,2):
            for after in (0,2):
                with self.subTest(fault=fault,after=after):self.scenario(0,fault,after)

    def test_descriptor_failure_before_and_after_batch_publication(self):
        for producer,after in ((0,0),(0,32),(1,0),(1,2),(2,0),(2,2)):
            with self.subTest(producer=producer,after=after):self.scenario(producer,3,after)

    def test_page_skb_failure_keeps_reference_and_partial_count_balanced(self):
        for producer in (1,2):
            for after in (0,2):
                with self.subTest(producer=producer,after=after):self.scenario(producer,4,after)

    def test_full_ownership_ring_does_not_publish_untracked_buffers(self):
        for producer in range(4):
            with self.subTest(producer=producer):self.scenario(producer,5)

    def test_initial_build_failure_keeps_dma_address_with_actual_buffer(self):
        for after in (0,2):
            with self.subTest(after=after):self.scenario(3,4,after)

    def test_initial_descriptor_failure_publishes_only_valid_prefix(self):
        for after in (0,2):
            with self.subTest(after=after):self.scenario(3,3,after)

    def test_receive_error_reclaims_cpu_ownership_before_validation(self):
        for producer in (4,5):
            for fault in (0,3):
                with self.subTest(producer=producer,fault=fault):self.scenario(producer,fault)
        text=(BASE/'sipa_skb_recv.c').read_text()
        body=text[text.index('struct sk_buff *sipa_recv_skb('):]
        validation=body.index('if (!sipa_check_recv_node_value(')
        self.assertLess(body.index('dma_unmap_single('),validation)
        self.assertLess(body.index('dma_sync_single_range_for_cpu('),validation)
        self.assertNotIn('dma_unmap_single(',body[body.index('recv_err:'):])
        self.assertIn('DMA_BIDIRECTIONAL',function(text,'sipa_alloc_mem'))
        self.assertNotIn('DMA_TO_DEVICE',text)


if __name__=='__main__':unittest.main(verbosity=2)
