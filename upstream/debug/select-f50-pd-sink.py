#!/usr/bin/env python3
"""Explicit trial profile for kernels which force their built-in command line."""
import argparse
import json
from pathlib import Path

KEY = 'sc27xx_ump9620.batteryless_sink='


def builtin_cmdline(config):
    lines = config.splitlines()
    values = [line.removeprefix('CONFIG_CMDLINE=') for line in lines
              if line.startswith('CONFIG_CMDLINE=')]
    if len(values) != 1:
        raise ValueError('expected one built-in kernel command line')
    return json.loads(values[0])


def validate_profile(config, enabled):
    selected = [v for v in builtin_cmdline(config).split() if v.startswith(KEY)]
    if enabled:
        if selected != [KEY+'1'] or 'CONFIG_CMDLINE_FORCE=y' not in config.splitlines():
            raise ValueError('fixed-input trial must be enabled in the forced kernel command line')
    elif selected:
        raise ValueError('fixed-input kernel requires an explicit trial image selection')


def enable_profile(config):
    cmdline = builtin_cmdline(config)
    selected = [v for v in cmdline.split() if v.startswith(KEY)]
    if selected and selected != [KEY+'1']:
        raise ValueError('conflicting fixed-input kernel command line')
    if not selected:
        cmdline = cmdline.rstrip()+' '+KEY+'1'
    if len(cmdline.encode()) >= 2048:
        raise ValueError('trial exceeds kernel command line capacity')
    updated = '\n'.join('CONFIG_CMDLINE='+json.dumps(cmdline) if line.startswith('CONFIG_CMDLINE=')
                        else line for line in config.splitlines())+'\n'
    validate_profile(updated, True)
    return updated


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('config', type=Path)
    p.add_argument('--verify', action='store_true')
    args = p.parse_args()
    if args.verify:
        validate_profile(args.config.read_text(), True)
    else:
        args.config.write_text(enable_profile(args.config.read_text()))


if __name__ == '__main__':
    main()
