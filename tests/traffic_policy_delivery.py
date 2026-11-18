"""Apply first introduction and a subsequent real OTA, preserving user ledgers."""
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
import unittest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'installer').is_dir())
APP = 'luci-app-t551m' if (ROOT/'openwrt/luci-app-t551m').exists() else 'luci-app-mu300'
SOURCE = ROOT / 'openwrt' / APP
sys.path.insert(0, str(ROOT/'installer/tests'))
from test_ota import mu_release, analyze, Engine, Patch

FILES = {
    'usr/libexec/unisoc-modem/traffic': 'root/usr/libexec/unisoc-modem/traffic',
    'usr/libexec/unisoc-modem/traffic-policy': 'root/usr/libexec/unisoc-modem/traffic-policy',
    'usr/libexec/rpcd/mu300dash': 'root/usr/libexec/rpcd/mu300dash',
    'www/luci-static/resources/mu300/traffic-controls.js': 'htdocs/luci-static/resources/mu300/traffic-controls.js',
    'www/luci-static/resources/view/mu300/traffic.js': 'htdocs/luci-static/resources/view/mu300/traffic.js',
    'www/luci-static/resources/mu300/common.js': 'htdocs/luci-static/resources/mu300/common.js',
    'usr/share/rpcd/acl.d/luci-app-mu300.json': 'root/usr/share/rpcd/acl.d/luci-app-mu300.json',
    'usr/share/luci/menu.d/luci-app-mu300.json': 'root/usr/share/luci/menu.d/luci-app-mu300.json',
    'etc/init.d/unisoc-traffic': 'root/etc/init.d/unisoc-traffic',
}
name = 'lock-control' if APP == 'luci-app-t551m' else 'lock'
FILES['usr/libexec/unisoc-modem/' + name] = 'root/usr/libexec/unisoc-modem/' + name


def release(root, version, files):
    mu_release(root, version, files)
    archive = root/'payload/mu300-openwrt.tar.gz'
    parents = set()
    for name in files:
        parents.update(str(p) for p in Path(name).parents if str(p) != '.')
    with tarfile.open(archive, 'w:gz') as tar:
        for name in sorted(parents, key=lambda s: (s.count('/'), s)):
            item = tarfile.TarInfo(name);item.type = tarfile.DIRTYPE;item.mode = 0o755;tar.addfile(item)
        for name, data in files.items():
            item = tarfile.TarInfo(name);item.mode = 0o755 if name.startswith(('usr/libexec/', 'etc/init.d/')) else 0o644
            item.size = len(data);tar.addfile(item, io.BytesIO(data))
    manifest = root/'desktop/manifest.json'; meta = json.loads(manifest.read_text())
    meta['magisk']['payload/mu300-openwrt.tar.gz'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(meta))


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'actual OTA ownership requires POSIX root')
class Delivery(unittest.TestCase):
    def test_first_and_subsequent_updates_keep_records_and_settings(self):
        current = {target: (SOURCE/rel).read_bytes() for target, rel in FILES.items()}
        previous = {}
        for target, rel in FILES.items():
            r = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:openwrt/' + APP + '/' + rel], capture_output=True)
            if r.returncode == 0: previous[target] = r.stdout
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); live = base/'live'
            for name, content in previous.items():
                path = live/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(content)
                if name.startswith(('usr/libexec/', 'etc/init.d/')): path.chmod(0o755)
            user = {'etc/config/wireless': b'user wifi settings',
                    'etc/unisoc-modem/traffic/state.json': b'user accounting and billing cycle',
                    'etc/unisoc-modem/traffic/policy.json': b'user quota and schedule',
                    'etc/unisoc-modem/traffic/sim1/state.json': b'SIM 2 user records'}
            for name, data in user.items():
                path = live/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(data)
            meta = live/'etc/mu300/release.json';meta.parent.mkdir(parents=True, exist_ok=True)
            meta.write_text(json.dumps(dict(profile='mu300', image_version='mu300-r1', kernel_release='7.2.8')))
            for number, files in ((1, previous), (2, current), (3, dict(current, **{'usr/libexec/unisoc-modem/traffic-policy': current['usr/libexec/unisoc-modem/traffic-policy'] + b'\n# subsequent update\n'}))):
                release(base/str(number), 'mu300-r' + str(number), files)
            for start in (1, 2):
                output = base/('patch%d.ota' % start)
                result = analyze(base/str(start), base/str(start+1), output)
                self.assertTrue(result['can_export'], result['blockers'])
                with Patch(output) as patch: Engine(live).apply(patch)
                self.assertEqual((live/'usr/libexec/unisoc-modem/traffic-policy').read_bytes(),
                                 current['usr/libexec/unisoc-modem/traffic-policy'] + (b'\n# subsequent update\n' if start == 2 else b''))
                self.assertTrue((live/'usr/libexec/unisoc-modem/traffic-policy').stat().st_mode & 0o111)
                self.assertEqual((live/'www/luci-static/resources/mu300/traffic-controls.js').read_bytes(), current['www/luci-static/resources/mu300/traffic-controls.js'])
                for name, content in user.items(): self.assertEqual((live/name).read_bytes(), content)

    def test_tracked_full_and_ordinary_update_delivery(self):
        self.assertIn('/etc/unisoc-modem/traffic/', (SOURCE/'root/lib/upgrade/keep.d/unisoc-traffic').read_text())
        self.assertIn('/etc/unisoc-modem/traffic/', (SOURCE/'Makefile').read_text())
        self.assertIn('/usr/libexec/unisoc-modem/*', (SOURCE/'Makefile').read_text())
        acl = json.loads((SOURCE/'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        self.assertIn('traffic_policy_get', acl['read']['ubus']['mu300dash'])
        for method in ('traffic_policy_set', 'traffic_policy_run'):
            self.assertNotIn(method, acl['read']['ubus']['mu300dash'])
        self.assertIn('*', acl['write']['ubus']['mu300dash'])
        for name in ('make-release.sh', 'build-openwrt-tf-magisk.sh'):
            path = ROOT/'tools'/name
            if path.exists():
                text = path.read_text()
                if './usr/libexec/unisoc-modem/traffic-core.uc' in text:
                    self.assertIn('./usr/libexec/unisoc-modem/traffic-policy', text)
                    self.assertIn('./www/luci-static/resources/mu300/traffic-controls.js', text)


if __name__ == '__main__': unittest.main(verbosity=2)
