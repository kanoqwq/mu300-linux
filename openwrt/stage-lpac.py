#!/usr/bin/env python3
"""Install/verify the patched lpac in an owned prepared root; no card access."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess

TOP = Path(__file__).resolve().parents[1]
STATE = runpy.run_path(str(TOP/'openwrt/lpac-state.py'))
RECEIPT = 'usr/share/unisoc-modem/lpac.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect(root):
    root = Path(root)
    receipt = json.loads((root/RECEIPT).read_text())
    if receipt['source_fingerprint'] != STATE['fingerprint']():
        raise ValueError('Prepared root has stale lpac inputs; run dependency installation')
    if digest(root/'usr/lib/lpac') != receipt['binary_sha256']:
        raise ValueError('Prepared root lpac binary differs from installation receipt')
    packages = (root/'lib/apk/db/installed').read_text()
    if not any('P:lpac\n' in item and '\nV:2.3.0-r1001\n' in '\n'+item+'\n'
               for item in packages.split('\n\n')):
        raise ValueError('Prepared root lacks the patched lpac package')
    return receipt


def audit(root):
    root = Path(root)
    receipt = inspect(root)
    app = next(TOP/'openwrt'/name for name in ('luci-app-t551m', 'luci-app-mu300')
               if (TOP/'openwrt'/name).is_dir())
    files = {'root/usr/libexec/unisoc-modem/euicc': 'usr/libexec/unisoc-modem/euicc',
             'root/usr/libexec/unisoc-modem/euicc-screen': 'usr/libexec/unisoc-modem/euicc-screen',
             'root/usr/libexec/unisoc-modem/euicc-progress.py': 'usr/libexec/unisoc-modem/euicc-progress.py',
             'root/usr/share/licenses/unisoc-euicc/e5-esim-MIT': 'usr/share/licenses/unisoc-euicc/e5-esim-MIT',
             'root/usr/libexec/rpcd/unisoc-euicc': 'usr/libexec/rpcd/unisoc-euicc',
             'root/usr/share/rpcd/acl.d/unisoc-euicc.json': 'usr/share/rpcd/acl.d/unisoc-euicc.json',
             'root/usr/share/luci/menu.d/unisoc-euicc.json': 'usr/share/luci/menu.d/unisoc-euicc.json',
             'htdocs/luci-static/resources/view/mu300/euicc.js': 'www/luci-static/resources/view/mu300/euicc.js',
             'htdocs/luci-static/resources/mu300/vendor/jsQR.js': 'www/luci-static/resources/mu300/vendor/jsQR.js',
             'htdocs/luci-static/resources/mu300/vendor/jsQR.LICENSE': 'www/luci-static/resources/mu300/vendor/jsQR.LICENSE'}
    for source, target in files.items():
        if (app/source).read_bytes().replace(b'\r\n', b'\n') != (root/target).read_bytes().replace(b'\r\n', b'\n'):
            raise ValueError('Prepared eUICC source differs: '+target)
    for name in ('gsma-rsp2-root-ci1.pem', 'README.md'):
        relative = Path('usr/share/unisoc-euicc/ca')/name
        if (app/'root'/relative).read_bytes() != (root/relative).read_bytes():
            raise ValueError('Prepared eUICC trust store differs: '+str(relative))
    for target in ('usr/libexec/unisoc-modem/euicc', 'usr/libexec/rpcd/unisoc-euicc'):
        if not (root/target).stat().st_mode & 0o111:
            raise ValueError('eUICC helper is not executable: '+target)
    return receipt


def install(root, artifacts, image=None):
    root, artifacts = Path(root).resolve(), Path(artifacts).resolve()
    info = STATE['inspect'](artifacts)
    if not root.is_dir() or root == Path('/'):
        raise ValueError('Select a prepared target root')
    if image:
        prefix = ['docker', 'run', '--rm', '--platform', 'linux/arm64',
                  '-v', str(root)+':/target', '-v', str(artifacts)+':/lpac:ro',
                  '--entrypoint', '/bin/sh', image, '-ec']
        subprocess.run(prefix + ['apk --root /target --no-scripts --no-network add --force-reinstall '
                       '--allow-untrusted /lpac/'+info['file']], check=True)
        output = subprocess.check_output(prefix + ['chroot /target /bin/sh -c '
            "'LPAC_APDU=stdio LPAC_HTTP=stdio /usr/lib/lpac driver list'"], text=True)
    else:
        subprocess.run(['apk', '--root', str(root), '--no-scripts', '--no-network',
                        'add', '--force-reinstall', '--allow-untrusted', str(artifacts/info['file'])], check=True)
        # Run only the inventory, never chip/profile commands at build time.
        output = subprocess.check_output(['chroot', str(root), '/bin/sh', '-c',
            'LPAC_APDU=stdio LPAC_HTTP=stdio /usr/lib/lpac driver list'], text=True)
    drivers = json.loads(output)
    data = drivers.get('payload', {})
    if drivers.get('type') != 'driver' or not all('stdio' in data.get(k, []) for k in ('LPAC_APDU', 'LPAC_HTTP')):
        raise ValueError('lpac stdio transports missing: '+output)
    receipt = {**info, 'binary_sha256': digest(root/'usr/lib/lpac')}
    target = root/RECEIPT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, indent=2)+'\n')
    return inspect(root)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--artifacts', type=Path)
    p.add_argument('--image')
    a = p.parse_args()
    print(json.dumps(install(a.root, a.artifacts, a.image) if a.artifacts else audit(a.root)))
