#!/usr/bin/env python3
"""Build/cache the selective-loader recognizer with the existing ARM64 toolchain."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess

TOP = Path(__file__).resolve().parents[2]


def build():
    source = Path(__file__).with_name('modem-loader-patch.c')
    output = TOP / 'out/modem-loader/modem-loader-patch'
    stamp = output.with_suffix('.build.json')
    compiler = os.environ.get('MODEM_LOADER_CC', 'aarch64-linux-gnu-gcc')
    inputs = {'source': hashlib.sha256(source.read_bytes()).hexdigest(),
              'builder': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'compiler': compiler}
    artifact = runpy.run_path(str(TOP / 'tools/atd-io/artifact.py'))
    if output.is_file() and stamp.is_file():
        try:
            saved = json.loads(stamp.read_text())
            if saved == {'inputs': inputs, 'artifact': artifact['inspect'](output, source)}:
                return output
        except (OSError, ValueError):
            pass
    output.parent.mkdir(parents=True, exist_ok=True)

    def linux(path):
        if os.name == 'nt':
            return subprocess.check_output(['wsl', '-d', 'Ubuntu-22.04', '--exec',
                                            'wslpath', '-a', '-u', str(path)], text=True).strip()
        return str(path)

    temporary = output.with_suffix('.tmp')
    args = [compiler, '-std=c11', '-Os', '-Wall', '-Wextra', '-Werror', '-static', '-s',
            linux(source), '-o', linux(temporary)]
    if os.name == 'nt':
        args = ['wsl', '-d', 'Ubuntu-22.04', '--exec', *args]
    subprocess.run(args, check=True)
    info = artifact['inspect'](temporary, source)
    temporary.replace(output)
    output.chmod(0o755)
    stamp.write_text(json.dumps({'inputs': inputs, 'artifact': info}) + '\n')
    return output


if __name__ == '__main__':
    print(build())
