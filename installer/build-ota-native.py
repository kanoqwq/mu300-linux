"""Rebuild the tiny freestanding Linux ARM64 decoder; no kernel/rootfs builds."""
import hashlib
import json
from pathlib import Path
import subprocess

folder = Path(__file__).resolve().parent/'ota-native'
source = folder/'lz4raw.c'
output = folder/'lz4raw-aarch64.so'
subprocess.run(['aarch64-linux-gnu-gcc', '-O2', '-fPIC', '-fno-builtin', '-fno-stack-protector',
                '-nostdlib', '-shared', '-Wl,--build-id=none', '-Wl,-z,noexecstack',
                '-o', str(output), str(source)], check=True)
subprocess.run(['aarch64-linux-gnu-strip', '--strip-unneeded', str(output)], check=True)
header = output.read_bytes()
assert header[:5] == b'\x7fELF\x02' and int.from_bytes(header[18:20], 'little') == 183
dynamic = subprocess.check_output(['aarch64-linux-gnu-readelf', '-d', str(output)], text=True)
assert '(NEEDED)' not in dynamic
(folder/'build.json').write_text(json.dumps(dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    binary_sha256=hashlib.sha256(header).hexdigest(), bytes=len(header), abi='aarch64-linux-freestanding'), indent=2)+'\n')
print(len(header), 'bytes; no libc dependency')
