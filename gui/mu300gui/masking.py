"""What may not leave the computer in a log, a copied text or a diagnostics report: the serial numbers of devices,
IMEI, IMSI and ICCID, passwords and their hashes, Wi-Fi passphrases, adb keys, and the name of the user's home
directory. Every line is masked when it comes in, so nothing unmasked is ever kept, shown or saved.

A mask says what was there, not what it was: <serial>, <imei>, <15 digits>, <password>.
"""
import os
import re

# 15 digits standing alone: an IMEI or an IMSI (a byte count or a sector number of this project never has 15; a
# sha256 is hex, and digits inside a longer word are not "standing alone")
_FIFTEEN = re.compile(r'(?<![0-9A-Za-z])\d{15}(?![0-9A-Za-z])')
# an ICCID: 19 or 20 digits starting with 89 (an F at the end on some cards)
_ICCID = re.compile(r'(?<![0-9A-Za-z])89\d{16,18}[0-9Ff]?(?![0-9A-Za-z])')
# IMEI and software version together (16 digits), and the 14 digits of an IMEI without its check digit after a name
_NAMED_ID = re.compile(r'(?i)\b(imei(?:sv)?|imsi|iccid|meid|serial(?:\s*(?:number|no))?|serialno|ro\.serialno|ro\.boot\.serialno)\b'
                       r'(\s*[\]:=]*\s*\[?\s*)(?=[0-9A-Za-z._-]*\d)([0-9A-Za-z][0-9A-Za-z._-]{5,})')
# name=value and name: value of anything that is a secret by its name
_NAMED_SECRET = re.compile(r'(?i)\b((?:MU300_ANS_PASSWORD|MU300_ANS_CURRENT_PASSWORD(?:_[A-Z_]+)?|MU300_PASSWORD|[A-Za-z_]*pass(?:word|phrase|wd)?|psk|wpa_passphrase|'
                           r'secret|token|private[_-]?key|PWHASH)\s*[=:]\s*)(\'[^\']*\'|"[^"]*"|\S+)')
# a crypt(3) hash ($6$salt$hash, $y$..., $1$...), wherever it stands
_CRYPT = re.compile(r'\$(?:1|2[aby]?|5|6|y|7)\$[./A-Za-z0-9$,=+-]{8,}')
# an adb public key: Android's RSA key as base64, 700 characters starting QAAAA
_ADB_KEY = re.compile(r'QAAAA[A-Za-z0-9+/]{100,}={0,2}')
# a MAC address (the hotspot's BSSID names the unit)
_MAC = re.compile(r'(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])')
# -s SERIAL of an adb command line, and ANDROID_SERIAL=...
_ADB_S = re.compile(r'(\badb(?:\.exe)?\s+-s\s+|ANDROID_SERIAL=)(\S+)')


class Masker:
    """mask(text) with the secrets this run knows by value (the serial numbers of the attached devices, the
    password that was typed) and the patterns above."""

    def __init__(self, home=None):
        self._values = {}       # value -> what to show instead
        home = os.path.expanduser('~') if home is None else home
        self._home = home if home and len(home) > 3 else ''

    def add(self, value, shown):
        """A value that is a secret from now on, wherever it turns up (too short a value would mask half the log)."""
        value = (value or '').strip()
        if len(value) >= 4:
            self._values[value] = shown

    def add_serial(self, serial):
        self.add(serial, '<serial>')
        if serial and serial.upper().startswith('MU300LINUX-'):
            self.add(serial[len('MU300LINUX-'):], '<serial>')

    def mask(self, text):
        if not text:
            return text
        # the longest first: a serial that contains another must not be cut in two
        for value in sorted(self._values, key=len, reverse=True):
            if value in text:
                text = text.replace(value, self._values[value])
        text = _NAMED_SECRET.sub(lambda m: m.group(1) + '<hidden>', text)
        text = _CRYPT.sub('<password hash>', text)
        text = _ADB_KEY.sub('<adb key>', text)
        text = _ADB_S.sub(lambda m: m.group(1) + '<serial>', text)
        text = _NAMED_ID.sub(lambda m: m.group(1) + m.group(2) + '<' + m.group(1).lower().split()[0].replace('ro.', '') + '>', text)
        text = _ICCID.sub('<iccid>', text)
        text = _FIFTEEN.sub('<15 digits>', text)
        text = _MAC.sub('<mac>', text)
        if self._home and self._home in text:
            text = text.replace(self._home, '~')
        return text


def short_serial(serial):
    """A serial number as it is shown by default: enough to tell two devices apart, not the number itself."""
    if not serial:
        return ''
    prefix = ''
    if serial.upper().startswith('MU300LINUX-'):
        prefix, serial = serial[:len('MU300LINUX-')], serial[len('MU300LINUX-'):]
    if len(serial) <= 4:
        return prefix + '•' * len(serial)
    return prefix + '•' * (len(serial) - 3) + serial[-3:]
