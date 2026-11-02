#!/usr/bin/env python3
"""Build the on-device screen UI (tools/u30pro_ui.c + tools/ui/*.h) as the aarch64-musl binary u30pro-panel, linked against the image's
libubus, libubox and libuci (the ubus object u30pro.panel, the settings in UCI: tools/ui/bus.h; tools/ubus_sysroot.py).
usage: build_ui.py <new-out-dir>

The binary keeps the old program's name and command line (probe | test | run) so the existing service, packager and
control-center code pick it up unchanged; pass the output directory's u30pro-panel to package_native_control_root.py
with --panel-binary.  Regenerate tools/ui/font.h with tools/gen_ui_font.py after changing any UI text."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from build_native_panel import cached_zig
from build_static import SYSROOT, verify_dynamic_aarch64

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / 'tools' / 'u30pro_ui.c'


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1]).resolve()
    if out.exists():
        raise SystemExit(f'output already exists; choose a new directory: {out}')
    zig = cached_zig()
    if not (SYSROOT / 'lib' / 'libubus.so').is_file():
        subprocess.run([sys.executable, str(REPO / 'tools' / 'ubus_sysroot.py'), str(SYSROOT)], check=True)
    out.mkdir(parents=True)
    binary = out / 'u30pro-panel'
    cmd = [str(zig), 'cc', '-target', 'aarch64-linux-musl', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O2',
           '-dynamic', '-I', str(SYSROOT / 'include'), '-L', str(SYSROOT / 'lib'), '-s', str(SRC), '-o', str(binary),
           '-lubus', '-lubox', '-luci', '-lm']
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    (out / 'compile.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        sys.stderr.write((out / 'compile.log').read_text(errors='replace'))
        raise SystemExit('build failed')
    data = binary.read_bytes()
    elf = verify_dynamic_aarch64(data)
    sources = [SRC] + sorted((REPO / 'tools' / 'ui').glob('*.h'))
    # the root carries this manifest (etc/u30pro/native-build.json): the source path relative to the repository, not the build host's
    man = {'source': SRC.relative_to(REPO).as_posix(), 'source_sha256': hashlib.sha256(SRC.read_bytes()).hexdigest(),
           'headers_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources[1:]},
           'binary': 'u30pro-panel', 'binary_sha256': hashlib.sha256(data).hexdigest(), 'binary_bytes': len(data),
           'libc': 'musl', 'target': 'aarch64-linux-musl', 'linkage': 'dynamic', 'needed': elf['needed'], 'elf': elf, 'ui': 'rich'}
    (out / 'manifest.json').write_text(json.dumps(man, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(binary), 'sha256': man['binary_sha256'], 'bytes': len(data)}))


if __name__ == '__main__':
    main()
