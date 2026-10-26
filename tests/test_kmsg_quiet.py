"""The kernel lines that repeated all day on an idle F50 are debug lines now, and OpenWrt's log buffer is 1 MiB
(FINDINGS 191).

* the IPA (sipa_eth, sipa_rm, sipa core, sipa_dele), WCN loopcheck and Wi-Fi TX path lines print at debug level in
  the 5.4 patches and in the mainline tree (upstream/modules), and the errors next to them stay errors;
* -EINPROGRESS from sipa_nic_tx() (the IPA resuming) is not an error and not "don't have this flow ctrl err type";
* the 5.4 build applies the kernel patch (KPATCHES) and the Wi-Fi patch (the wlan_combo-*.patch glob);
* 90-mu300 raises logd's buffer from a stock size (none, 64, 128 KiB) to 1024 and leaves a size the user set."""
import re
import unittest

from helpers import TOP, ShellTest

PATCHES = TOP / 'kernel/patches'
SIPA54 = PATCHES / 'sipa-wcn-logspam-quiet.patch'
WLAN54 = PATCHES / 'wlan_combo-txrx-logs-quiet.patch'
MODEM = TOP / 'upstream/modules/sprd_modem'
WCN = TOP / 'upstream/modules/wcn_bsp'
WLAN = TOP / 'upstream/modules/sprd_wlan_combo'
UCI_DEFAULTS = TOP / 'openwrt/overlay/etc/uci-defaults/90-mu300'

DEBUG = re.compile(r'\b(pr_debug|pr_debug_ratelimited|dev_dbg|netdev_dbg|WCN_DBG)\s*$')
# (file in the vendor 5.4 tree, the same file in upstream/modules or None, the format the line prints)
SIPA_LINES = [
    ('drivers/net/sprd/sipa_v3/sipa_eth.c', MODEM / 'sipa_v3/sipa_eth.c', '"wakeup queue on dev %s\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_eth.c', MODEM / 'sipa_v3/sipa_eth.c', '"dev %s SIPA LEAVE FLOWCTRL\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_eth.c', MODEM / 'sipa_v3/sipa_eth.c', '"stop queue on dev %s\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_usb.c', MODEM / 'sipa_v3/sipa_usb.c', '"SIPA LEAVE FLOWCTRL\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_rm_res.c', MODEM / 'sipa_v3/sipa_rm_res.c', '"%s state changed %d->%d\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_rm_res.c', MODEM / 'sipa_v3/sipa_rm_res.c', '"EXIT with %d\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_rm_res.c', MODEM / 'sipa_v3/sipa_rm_res.c', '"calling driver CB\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_rm_res.c', MODEM / 'sipa_v3/sipa_rm_res.c', '"driver CB returned with %d\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_core.c', MODEM / 'sipa_v3/sipa_core.c', '"sipa resume hrtimer start\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_core.c', MODEM / 'sipa_v3/sipa_core.c',
     '"sipa ready to suspend and change to single\\n"'),
    ('drivers/net/sprd/sipa_v3/sipa_core.c', MODEM / 'sipa_v3/sipa_core.c', '"sipa prepare suspend finished\\n"'),
    ('drivers/net/sprd/sipa_delegate/sipa_dele_cmn.c', MODEM / 'sipa_delegate/sipa_dele_cmn.c', '"smsg_recv, smsg_cnt='),
    ('drivers/net/sprd/sipa_delegate/sipa_dele_cmn.c', MODEM / 'sipa_delegate/sipa_dele_cmn.c', '"smsg_send, dst='),
    ('drivers/net/sprd/sipa_delegate/sipa_dele_cp_in_ap.c', MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c',
     '"prod_id:%d, on_cmd, flag = %d\\n"'),
    ('drivers/net/sprd/sipa_delegate/sipa_dele_cp_in_ap.c', MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c',
     '"sipa_dele get pd success ret = %d\\n"'),
    ('drivers/net/sprd/sipa_delegate/sipa_dele_cp_in_ap.c', MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c',
     '"sipa pd_eb_flag = %d, pd_get_flag = %d\\n"'),
    ('drivers/unisoc_platform/sprdwcn/platform/wcn_txrx.c', WCN / 'platform/wcn_txrx.c', '"%s, chn:%d\\n"'),
    ('drivers/unisoc_platform/sprdwcn/pcie/edma_engine.c', WCN / 'pcie/edma_engine.c', '"edma-tp:%d/%d (byte/us)\\n"'),
    ('drivers/unisoc_platform/sprdwcn/platform/loopcheck.c', WCN / 'platform/loopcheck.c', '"tx:%s in %s\\n"'),
    ('drivers/unisoc_platform/sprdwcn/platform/loopcheck.c', WCN / 'platform/loopcheck.c', '"tx:%s\\n"'),
    ('drivers/unisoc_platform/sprdwcn/platform/wcn_procfs.c', WCN / 'platform/wcn_procfs.c', '"rx:%s\\n"'),
]
WLAN_LINES = [
    ('sc2355/pcie.c', '"%s, total free num:%lu; total tx_num:%lu\\n"'),
    ('sc2355/pcie.c', '"%s, free_num: %d, to_free_list num: %d\\n"'),
    ('sc2355/pcie.c', '"tx_cmd_pop num: %d,cmd_poped=%d, cmd_send=%d\\n"'),
    ('sc2355/pcie.c', '"%s,%d,debug: %d\\n"'),
    ('sc2355/pcie.c', '"%s, free_num=%d, data_num=%d\\n"'),
    ('sc2355/pcie.c', '"%s,%d free_num=%d, data_num=%d\\n"'),
    ('sc2355/pcie.c', '"%s: to free list empty\\n"'),
    ('sc2355/cmdevt.c', '"ctx_id %d recv rsp[%s]\\n"'),
    ('sc2355/tx.c', '"%s,AP mode, multicast bssid:'),
    ('common/cfg80211.c', '"%s\\n", __func__);\n\n\treturn cfg80211_set_beacon_ies'),
]


def printk_calls(text, fmt):
    """the printk-style macro in front of every occurrence of FMT in TEXT: pr_info("..., dev_info(dev, "..., a call
    split over two lines (pr_info\\n    ("...) too"""
    out = []
    for m in re.finditer(re.escape(fmt), text):
        mm = re.search(r'([A-Za-z_]\w*)\s*\(\s*(?:[A-Za-z_][\w>.-]*\s*,\s*)?$',
                       text[max(0, m.start() - 200):m.start()])
        if mm:
            out.append(mm.group(1))
    return out


def patch_sides(patch, path):
    """(removed, added) text of PATH's hunks in PATCH: '-' lines with the context, and '+' lines with it"""
    old, new, cur = [], [], None
    for line in patch.splitlines():
        if line.startswith('+++ '):
            cur = line[6:].split('\t')[0] == path
        elif line.startswith('--- ') or line.startswith('diff '):
            continue
        elif cur and line[:1] in ' -+':
            if line[:1] != '+':
                old.append(line[1:])
            if line[:1] != '-':
                new.append(line[1:])
    return '\n'.join(old), '\n'.join(new)


class KernelLines(unittest.TestCase):
    def assert_debug(self, text, fmt, where):
        calls = printk_calls(text, fmt)
        self.assertTrue(calls, f'{fmt} not found in {where}')
        for c in calls:
            self.assertTrue(DEBUG.match(c), f'{fmt} printed with {c} in {where}')

    def test_sipa_and_wcn_lines_are_debug_in_both_kernels(self):
        patch = SIPA54.read_text()
        for path54, mainline, fmt in SIPA_LINES:
            old, new = patch_sides(patch, path54)
            self.assertTrue(printk_calls(old, fmt), f'{fmt}: no hunk for it in {SIPA54.name}')
            self.assert_debug(new, fmt, f'{SIPA54.name} ({path54})')
            self.assert_debug(mainline.read_text(), fmt, str(mainline.relative_to(TOP)))

    def test_wifi_tx_path_lines_are_debug_in_both_kernels(self):
        patch = WLAN54.read_text()
        for path, fmt in WLAN_LINES:
            old, new = patch_sides(patch, path)
            self.assertTrue(printk_calls(old, fmt), f'{fmt}: no hunk for it in {WLAN54.name}')
            for c in printk_calls(new, fmt):
                self.assertTrue(DEBUG.match(c), f'{fmt} printed with {c} in {WLAN54.name}')
            for c in printk_calls((WLAN / path).read_text(), fmt):
                self.assertTrue(DEBUG.match(c), f'{fmt} printed with {c} in sprd_wlan_combo/{path}')

    def test_einprogress_is_not_an_error_but_other_failures_are(self):
        for text in (patch_sides(SIPA54.read_text(), 'drivers/net/sprd/sipa_v3/sipa_eth.c')[1],
                     (MODEM / 'sipa_v3/sipa_eth.c').read_text()):
            i = text.index('if (ret == -EINPROGRESS)')
            block = text[i:i + 200]
            self.assertRegex(block, r'if \(ret == -EINPROGRESS\)\s*pr_debug\("fail to send, ret %d\\n", ret\);\s*'
                                    r'else\s*pr_err_ratelimited\("fail to send, ret %d\\n", ret\);')
        for text in (patch_sides(SIPA54.read_text(), 'drivers/net/sprd/sipa_v3/sipa_nic.c')[1],
                     (MODEM / 'sipa_v3/sipa_nic.c').read_text()):
            self.assertRegex(text, r'case -EINPROGRESS:\n(\s*/\*.*\*/\n)?\s*break;\n\s*default:\n\s*dev_warn\(ipa->dev, '
                                   r'"don\'t have this flow ctrl err type')

    def test_the_errors_beside_them_stay_errors(self):
        keep = [(MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c', '"sipa_dele get pd fail ret = %d, ENABLE answered with FAIL\\n"', 'pr_err'),
                (MODEM / 'sipa_delegate/sipa_dele_cmn.c', '"smsg send fail %d prod_id = %d\\n"', 'pr_err'),
                (MODEM / 'sipa_v3/sipa_rm_res.c', '"driver CB returned error %d\\n"', 'pr_err'),
                (MODEM / 'sipa_v3/sipa_eth.c', '"called when %s is down\\n"', 'pr_err')]
        for path, fmt, level in keep:
            self.assertEqual(set(printk_calls(path.read_text(), fmt)), {level}, (path.name, fmt))
        # the patches only lower levels: no line of theirs prints at error level that did not before
        for patch in (SIPA54, WLAN54):
            for line in patch.read_text().splitlines():
                if line.startswith('+') and not line.startswith('+++'):
                    self.assertNotRegex(line, r'\b(pr_err|dev_err|netdev_err|WCN_ERR|pr_warn|dev_warn)\(', line)

    def test_the_build_applies_both(self):
        kp = re.search(r'^KPATCHES="([^"]*)"', (TOP / 'kernel/build-all.sh').read_text(), re.M).group(1).split()
        self.assertIn(SIPA54.stem, kp)
        # after the earlier sipa_dele patches it was made on top of
        for before in ('sipa-delegate-einprogress', 'sipa-delegate-pm-runtime', 'logspam-quiet'):
            self.assertLess(kp.index(before), kp.index(SIPA54.stem))
        self.assertRegex(WLAN54.name, r'^wlan_combo-.*\.patch$')


class LogdBuffer(ShellTest):
    def block(self):
        lines = [ln for ln in UCI_DEFAULTS.read_text().splitlines() if 'log_size' in ln and not ln.startswith('#')]
        self.assertEqual(len(lines), 1, lines)
        return lines[0]

    def test_stock_sizes_become_1024_and_a_chosen_one_stays(self):
        # uci: "get" answers $SIZE (nothing for an unset option: exit 1), "set" is recorded
        self.stub('uci', 'case "$*" in *" get "*|"get "*) [ -n "$SIZE" ] || exit 1; echo "$SIZE" ;;\n'
                         '*set*) echo "$*" >> "$STUBLOG/set" ;; esac')
        for shell in self.each_shell():
            for size, want in (('', '1024'), ('64', '1024'), ('128', '1024'), ('256', None), ('512', None),
                               ('1024', None), ('16', None)):
                (self.tmp / 'set').write_text('')
                r = self.sh(shell, self.block(), SIZE=size)
                self.assertEqual(r.returncode, 0, r.stderr)
                got = (self.tmp / 'set').read_text().strip()
                if want:
                    self.assertEqual(got, f"-q set system.@system[0].log_size={want}", size)
                else:
                    self.assertEqual(got, '', size)

    def test_set_before_the_commit_of_system(self):
        text = UCI_DEFAULTS.read_text()
        self.assertLess(text.index(self.block()), text.index('uci commit system'))


if __name__ == '__main__':
    unittest.main()
