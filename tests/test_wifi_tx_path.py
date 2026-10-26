"""The SC2355 Wi-Fi TX path of the mainline kernels (upstream/modules/sprd_wlan_combo, wcn_bsp): four defects of the
vendor's code that kanoqwq found and measured on an F50 (clean-tf-7.2 149c9d2a), fixed here in our own words
(FINDINGS 103). These read the source: the modules are built by the release (and were built for 6.18.56 and 7.2.10
without a new warning), and the behaviour needs a device (the checklist in the section)."""
import unittest

from helpers import TOP

WLAN = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo' / 'sc2355'
WCN = TOP / 'upstream' / 'modules' / 'wcn_bsp'


def body(src, sig):
    b = src[src.index(sig):]
    return b[:b.index('\n}\n')]


class WifiTxPath(unittest.TestCase):
    def test_the_descriptor_fill_keeps_the_caller_s_skb(self):
        # skb_realloc_headroom() + kfree_skb() replaced only the local pointer: every caller went on with a freed skb
        for f, fn in (('pcie.c', 'int sc2355_pcie_hif_fill_msdu_dscr('), ('sdio.c', 'int sc2355_hif_fill_msdu_dscr(')):
            with self.subTest(file=f):
                b = body((WLAN / f).read_text(), fn)
                self.assertNotIn('skb_realloc_headroom(skb', b)
                self.assertNotIn('kfree_skb(', b)
                self.assertIn('skb_header_cloned(skb)', b)
                self.assertIn('if (skb_cow_head(skb, DSCR_LEN + offset + hif->hif_offset + MSG_PTR_LEN + dscr_rsvd)) {', b)
                # the Ethernet header is read again after the data may have moved, before anything uses it
                self.assertLess(b.index('skb_cow_head'), b.index('ethhdr = (struct ethhdr *)skb->data;\t/*'))
                self.assertLess(b.index('ethhdr = (struct ethhdr *)skb->data;\t/*'), b.index('memcpy(hif->skb_da'))

    def test_the_group_tid_is_set_in_the_descriptor_not_the_pcie_prefix(self):
        b = body((WLAN / 'cmdevt.c').read_text(), 'int sprd_xmit_data2cmd_wq(')
        pick = b.index('dscr = (struct tx_msdu_dscr *)(skb->data +\n\t\t(hif->hw_type == SPRD_HW_SC2355_PCIE ? MSDU_DSCR_RSVD : 0));')
        self.assertLess(pick, b.index('skb_push(skb,FLAG_SIZE);'))
        self.assertLess(pick, b.index('dscr->buffer_info.msdu_tid = prio_1;'))
        self.assertEqual(b.count('dscr = '), 1)
        self.assertNotIn('pr_info(', b)

    def test_ap_ipv6_multicast_takes_the_data_queue_on_pcie(self):
        tx = (WLAN / 'tx.c').read_text()
        b = body(tx, 'static int tx_mc_pkt(')
        # before anything sends it by command; SDIO keeps the vendor's path
        self.assertLess(b.index('if (hif->hw_type == SPRD_HW_SC2355_PCIE)\n\t\treturn 1;'), b.index('sprd_xmit_data2cmd_wq('))
        # 1 means "not handled": the caller goes on to tx_filter_ip_pkt, which still sends DHCPv6/DNS/VoWiFi by command
        f = body(tx, 'int sprd_tx_filter_packet(')
        self.assertLess(f.index('!tx_mc_pkt(skb, ndev)'), f.index('return tx_filter_ip_pkt(skb, ndev);'))
        ip = body(tx, 'static int tx_filter_ip_pkt(')
        self.assertIn('is_data2cmd = (is_ipv4_dhcp || is_ipv6_dhcp || is_vowifi2cmd ||', ip)

    def test_a_full_edma_pending_ring_is_backpressure(self):
        edma = body((WCN / 'pcie' / 'edma_engine.c').read_text(), 'static int edma_pending_q_buffer(')
        self.assertIn('return -EAGAIN;', edma)
        self.assertNotIn('return ERROR;', edma)
        pcie = (WLAN / 'pcie.c').read_text()
        # both TX data channels get the completion callback that wakes the TX thread
        self.assertEqual(pcie.count('NULL, sc2355_pcie_tx_data_complete, NULL),'), 2)
        done = body(pcie, 'static int sc2355_pcie_tx_data_complete(')
        self.assertIn('atomic_xchg(&tx_mgmt->pcie_tx_waiting, 0)', done)
        self.assertIn('sc2355_tx_up(tx_mgmt);', done)
        push = body(pcie, 'static int sc2355_pcie_tx_data_push(')
        # armed before the submission (the completion can come first), disarmed unless it was refused
        self.assertLess(push.index('atomic_set(&tx_mgmt->pcie_tx_waiting, 1);'), push.index('sc2355_pcie_push_link('))
        self.assertIn('if (ret != -EAGAIN)\n\t\tatomic_set(&tx_mgmt->pcie_tx_waiting, 0);', push)
        lst = body(pcie, 'int sc2355_pcie_hif_tx_list(')
        self.assertIn('ret = sc2355_pcie_tx_data_push(hif,', lst)
        self.assertIn('if (ret != -EAGAIN)\t/* MU300: a full pending ring is not a failure */\n\t\t\t\t\thif->pushfail_count++;', lst)
        tx = (WLAN / 'tx.c').read_text()
        h = body(tx, 'static int tx_handle_to_send_list(')
        # a refusal returns before the failure count (more than 50 flushed the queue)
        self.assertLess(h.index('if (ret == -EAGAIN)\n\t\t\t\treturn ret;'), h.index('failcount++'))
        self.assertIn('atomic_set(&tx_mgmt->pcie_tx_waiting, 0);', body(tx, 'int sc2355_tx_init('))
        self.assertIn('\tatomic_t pcie_tx_waiting;\n', (WLAN / 'tx.h').read_text())

    def test_backpressure_is_not_logged_as_an_error(self):
        # #339: the pair "edma_pending_q_buffer(10) full" / "push link fail: -11" was a third of the kernel log
        src = (WCN / 'pcie' / 'edma_engine.c').read_text()
        edma = body(src, 'static int edma_pending_q_buffer(')
        self.assertNotIn('pr_warn_ratelimited(', edma)
        self.assertIn('static DEFINE_RATELIMIT_STATE(full_rs, 60 * HZ, 1);', edma)
        self.assertLess(edma.index('if (__ratelimit(&full_rs))'), edma.index('return -EAGAIN;'))
        self.assertIn('#include <linux/ratelimit.h>', src)
        link = body((WLAN / 'pcie.c').read_text(), 'int sc2355_pcie_push_link(')
        self.assertIn('if (ret && ret != -EAGAIN) {', link)
        self.assertIn('pr_err_ratelimited("%s: push link fail: %d, chn: %d!\\n"', link)

    def test_a_failed_direct_push_does_not_leave_the_channel_refusing(self):
        # #339: status is set before the direct push and only a completion's flush clears it; a push that went
        # nowhere brings no completion, so with an empty ring status is cleared at once. Nothing queued is popped
        # here: a queued submission's caller was told OK, and dropping one whose push fails would leak it.
        src = (WCN / 'pcie' / 'edma_engine.c').read_text()
        a = body(src, 'int edma_push_link_async(')
        self.assertLess(a.index('pending_q.status = 1;'), a.index('ret = edma_push_link(chn, head, tail, num);\n\t/*'))
        self.assertIn('\tif (ret) {\n\t\tspin_lock_irqsave(q->lock.irq_spinlock_p, q->lock.flag);\n'
                      '\t\tif (edma_pending_q_num(chn) <= 0)\n\t\t\tq->status = 0;\n'
                      '\t\tspin_unlock_irqrestore(q->lock.irq_spinlock_p, q->lock.flag);\n\t}', a)
        self.assertNotIn('edma_pending_q_flush(', a)
        self.assertNotIn('while (', a)
        self.assertLess(src.index('static int edma_pending_q_num(int chn);'), src.index('int edma_push_link_async('))

class WifiCommandNotSent(unittest.TestCase):
    """FINDINGS 106 (after kanoqwq's clean-tf-7.2 0e19fe32): a command the driver does not send is an error, with
    nothing in the caller's reply buffer; get_station's reply starts zeroed. The 5.4 driver gets the same change as
    kernel/patches/wlan_combo-unsent-cmd-error.patch."""

    def test_unsent_commands_return_an_error_and_no_reply(self):
        b = body((WLAN / 'cmdevt.c').read_text(), 'int sc2355_send_cmd_recv_rsp(')
        self.assertIn('if (!ret && cmdevt_lock_cmd(cmd, hif))\n\t\tret = -EIO;', b)
        self.assertNotIn('if (ret || cmdevt_lock_cmd(cmd, hif))', b)
        # both blocked windows: the lock released, the reply length 0, -EBUSY, before the common exit
        for marker in ('need block cmd after close', 'need block cmd while change iface'):
            with self.subTest(window=marker):
                w = b[b.index(marker):]
                w = w[:w.index('goto out;')]
                self.assertIn('cmdevt_unlock_cmd(cmd, hif);', w)
                self.assertIn('*rlen = 0;', w)
                self.assertIn('ret = -EBUSY;', w)
        # nothing between the checks and the send can fall through with ret still 0 and nothing sent
        self.assertLess(b.index('ret = -EBUSY;'), b.index('ret = cmdevt_send_cmd(priv, msg);'))

    def test_get_station_reply_is_zeroed(self):
        b = body((WLAN / 'cmdevt.c').read_text(), 'int sc2355_get_station(')
        self.assertIn('struct cmd_get_station get_sta = {};', b)

    def test_the_vendor_kernel_gets_the_same_change(self):
        p = (TOP / 'kernel' / 'patches' / 'wlan_combo-unsent-cmd-error.patch').read_text()
        self.assertIn('--- a/sc2355/cmdevt.c\n+++ b/sc2355/cmdevt.c\n', p)
        for s in ('+\tif (!ret && cmdevt_lock_cmd(cmd, hif))\n+\t\tret = -EIO;', '+\t\t\tret = -EBUSY;',
                  '+\tstruct cmd_get_station get_sta = {};'):
            self.assertIn(s, p)
        self.assertEqual(p.count('+\t\t\tret = -EBUSY;'), 2)


if __name__ == '__main__':
    unittest.main()
