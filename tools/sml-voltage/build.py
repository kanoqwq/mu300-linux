#!/usr/bin/env python3
"""Cache the read-only SML ABI checker with the existing static ARM64 compiler."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

TOP = Path(__file__).resolve().parents[2]


def build():
    source = Path(__file__).with_name('sml-voltage-check.c')
    output = TOP/'out/sml-voltage/sml-voltage-check'
    metadata = output.with_suffix('.build.json')
    compiler = os.environ.get('SML_VOLTAGE_CC', 'aarch64-linux-gnu-gcc')
    inputs = {'source':hashlib.sha256(source.read_bytes()).hexdigest(),
              'builder':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'compiler':compiler}
    if output.is_file() and metadata.is_file():
        try:
            saved = json.loads(metadata.read_text())
        except (OSError, ValueError):
            saved = {}  # interrupted cache metadata is not a release input
        if saved.get('inputs') == inputs and saved.get('binary') == hashlib.sha256(output.read_bytes()).hexdigest():
            return output
    output.parent.mkdir(parents=True, exist_ok=True)
    def linux(path):
        if os.name == 'nt':
            return subprocess.check_output(['wsl','-d','Ubuntu-22.04','--exec','wslpath','-a','-u',str(path)],text=True).strip()
        return str(path)
    temporary = output.with_suffix('.tmp')
    args = [compiler,'-std=c11','-Os','-Wall','-Wextra','-Werror','-static','-s',
            linux(source),'-o',linux(temporary)]
    if os.name == 'nt':
        args = ['wsl','-d','Ubuntu-22.04','--exec',*args]
    subprocess.run(args, check=True)
    data = temporary.read_bytes()
    if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20],'little') != 183:
        raise ValueError('SML checker is not an AArch64 ELF executable')
    temporary.replace(output)
    output.chmod(0o755)
    metadata.write_text(json.dumps({'inputs':inputs,'binary':hashlib.sha256(data).hexdigest()})+'\n')
    return output


if __name__ == '__main__':
    print(build())
