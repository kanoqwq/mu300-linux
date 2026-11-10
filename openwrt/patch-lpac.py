#!/usr/bin/env python3
"""Apply the upstream stdio boolean fix to the pinned lpac 2.3.0 source."""
from pathlib import Path
import sys


def patch(root, verify=False):
    file = Path(root)/'driver/apdu/stdio.c'
    text = file.read_text()
    for expr in ('json_request("connect", NULL, 0)', 'json_request("logic_channel_open", aid, aid_len)',
                 'json_request("transmit", tx, tx_len)'):
        old = 'if (' + expr + ') {'
        new = 'if (!' + expr + ') {'
        if verify:
            if old in text or text.count(new) != 1:
                raise ValueError('lpac stdio patch was not preserved: ' + expr)
            continue
        if text.count(old) != 1 or new in text:
            raise ValueError('Unexpected lpac stdio source: ' + expr)
        text = text.replace(old, new, 1)
    old = '''    free(*data);
    if (data) {
        *data = NULL;
    }'''
    new = '''    if (data) {
        free(*data);
        *data = NULL;
    }'''
    if verify:
        if old in text or text.count(new) != 1:
            raise ValueError('lpac stdio cleanup patch was not preserved')
        return
    if text.count(old) != 1:
        raise ValueError('Unexpected lpac stdio response cleanup')
    text = text.replace(old, new, 1)
    file.write_text(text)


if __name__ == '__main__':
    patch(sys.argv[-1], '--verify' in sys.argv[1:])
