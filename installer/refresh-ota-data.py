"""Refresh uncompiled OTA payload data in an otherwise verified native build.

Never rewrites compiled backend/UI inputs or skips a source mismatch. The exact
old native file hashes and every non-data source must still match. Plain OTA
Python/shell payload files live outside the signed macOS application bundle.
"""
import argparse
import hashlib
import json
from pathlib import Path
from package_gui import sources, validate_artifact, digest

HERE = Path(__file__).resolve().parent
DATA = {'../openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/'+n:n
        for n in ('engine.py', 'boot.py', 'rpc.py', 'proxy.sh')}


def refresh(artifact, platform):
    artifact = Path(artifact)
    metadata = json.loads((artifact/'build.json').read_text())
    expected = sources()
    old_source = metadata['source']
    changed = {n for n in set(old_source)|set(expected) if old_source.get(n) != expected.get(n)}
    if changed-set(DATA):
        raise ValueError('Compiled/non-data inputs changed; native rebuild required: '+', '.join(sorted(changed-set(DATA))))
    validate_artifact(artifact, platform, old_source)
    if not changed: return metadata
    before = dict(metadata['files']); updates = {}
    for name in sorted(changed):
        filename = DATA[name]
        candidates = [p for p in before if p.endswith('/_internal/'+filename)]
        if len(candidates) != 1: raise ValueError('Uncompiled native payload is not unique: '+filename)
        relative = candidates[0]
        if '.app/' in relative: raise ValueError('Refusing to edit a signed application bundle')
        target = artifact/relative
        content = (HERE/name).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(target.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != old_source[name]:
            raise ValueError('Native payload differs from original source: '+name)
        if hashlib.sha256(content).hexdigest() != expected[name]: raise ValueError('Unexpected payload source')
        target.write_bytes(content)
        updates[relative] = digest(target)
    metadata['files'].update(updates)
    for name, old in before.items():
        if name not in updates and digest(artifact/name) != old:
            raise ValueError('Non-data native artifact changed: '+name)
    metadata['source'] = expected
    metadata.setdefault('data_refreshes', []).append(dict(sources=sorted(changed), files=updates,
        procedure_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    (artifact/'build.json').write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
    validate_artifact(artifact, platform, expected)
    return metadata


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact', required=True, type=Path)
    p.add_argument('--platform', required=True, choices=('windows-x64', 'macos-arm64'))
    a = p.parse_args(); result = refresh(a.artifact, a.platform)
    print(json.dumps(dict(verified=True, files=len(result['files']), refreshes=result.get('data_refreshes', []))))
