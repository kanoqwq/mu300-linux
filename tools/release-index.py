#!/usr/bin/env python3
"""Compatibility entry into the pinned shared OpenWrt Tools submodule."""
from pathlib import Path
import runpy
import sys

_SHARED = Path(__file__).resolve().parents[1] / 'installer'
_SOURCE = _SHARED / 'release-index.py'
if not _SOURCE.is_file():
    raise RuntimeError('Initialize the installer submodule / 请先执行 git submodule update --init --recursive')
sys.path.insert(0,str(_SHARED))
_exports = runpy.run_path(str(_SOURCE),run_name='__main__' if __name__=='__main__' else 'openwrt_tools_entry')
globals().update({key:value for key,value in _exports.items() if not key.startswith('__')})
