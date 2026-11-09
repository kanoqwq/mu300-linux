"""The DFS patch must apply to stock and SAE-patched wifi-scripts."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PATCH=Path(__file__).resolve().parents[1]/'openwrt/patches/wifi-scripts-no-dfs.patch'
SOURCE="""import {
	append, append_raw, append_vars, dump_config, flush_config, set_default,
	wiphy_info, wiphy_band
} from 'wifi.common';
import { validate } from 'wifi.validate';
function device_capabilities(config) {
	let phy = config.phy;
	phy = wiphy_info(phy);
	let band = wiphy_band(phy, config.band);

	phy_capabilities.ht_capa = band.ht_capa ?? 0;
CIPHER}

function generate(config) {
	if (!config)
		die('unknown phy');

	set_device_defaults(config);

	device_log_append(config);
}
"""

@unittest.skipUnless(shutil.which('patch'),'requires GNU patch')
class PatchLayouts(unittest.TestCase):
    def test_stock_and_sae_generators(self):
        for cipher in ['', '\tphy_features.cipher_gcmp256 = WLAN_CIPHER_SUITE_GCMP_256 in (phy.cipher_suites ?? []);\n']:
            with self.subTest(cipher_in_hostapd=bool(cipher)),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);p=root/'usr/share/ucode/wifi/hostapd.uc';p.parent.mkdir(parents=True)
                p.write_text(SOURCE.replace('CIPHER',cipher))
                result=subprocess.run(['patch','--batch','--fuzz=0','-p1','-d',tmp,'-i',str(PATCH)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                text=p.read_text()
                self.assertEqual(text.count('function device_no_dfs(config)'),1)
                self.assertEqual(text.count('device_no_dfs(config);'),1)
                self.assertEqual(text.count('function device_chanlist_band(config)'),1)
                self.assertEqual(text.count('device_chanlist_band(config);'),1)
                self.assertLess(text.index('device_chanlist_band(config);'),text.index('device_no_dfs(config);'))
                self.assertIn('config.channel = 36;',text)
                self.assertNotRegex(text,r'config\.htmode\s*=(?!=)')
