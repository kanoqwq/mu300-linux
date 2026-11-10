# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Enceka <enceka@yeah.net>
# Extracted from XiaoMengXinX/luci-app-e5-esim@6fb6b318; see licenses/e5-esim-MIT.
import re

class WriteProgress:
    def __init__(self, notify):
        self.notify = notify
        self.active = False
        self.total = 0
        self.written = 0
        self.percent = -1

    def accept(self, apdu, reply):
        if not self.active:
            return
        raw = bytes.fromhex(apdu)
        if len(raw) < 5 or raw[1] != 0xe2:
            return
        response = re.search(r',\s*"?([0-9a-fA-F]+)', reply)
        if not response:
            return
        status = response.group(1)[-4:].upper()
        if not status.startswith(('61', '9')):
            return
        payload = raw[5:5 + raw[4]]
        if not self.total:
            # BF36's BER length covers the complete bound profile package.
            if not payload.startswith(b'\xbf\x36') or len(payload) < 3:
                return
            length = payload[2]
            header = 3
            if length & 0x80:
                count = length & 0x7f
                if not 1 <= count <= 4 or len(payload) < 3 + count:
                    return
                header += count
                length = int.from_bytes(payload[3:header], 'big')
            self.total = header + length
        self.written += len(payload)
        percent = min(100, self.written * 100 // self.total)
        if percent != self.percent:
            self.percent = percent
            self.notify('es10b_load_bound_profile_package:progress',
                        {'written': self.written, 'total': self.total, 'percent': percent})

