#!/usr/bin/env python3
"""Making room on an encrypted Android by a reset, and the installation that resumes afterwards (EXPERIMENTAL; the
installers' make-room path, docs/FINDINGS.md 112). Ported from kano's desktop installer (kanoqwq/mu300-linux, branch
clean-tf-7.2, desktop/installer.py and desktop/boot/part-prepare.sh, f6b0668d and 0e19fe32): a transaction saved on
the computer before anything is written, Android's own recovery formatting the smaller userdata, and a second run that
continues only when the device, its slot, its partition geometry, a token written to the device and the installer's
tools are still the ones the transaction was made with.

install.sh and install.ps1 call this file; the graphical installer and tests/ import it.

    resume.py token                              a new token (32 hex digits)
    resume.py marker TOKEN OUT                   the 4096-byte block written at the start of the prepared region
    resume.py bcb OUT                            the 2048-byte bootloader message that has recovery wipe userdata
    resume.py encrypted state=S type=T source=DEV magic=HEX   yes / no / unknown: is Android's data encrypted
    resume.py identity TOP [MANIFEST]            the payload identity of this installer (and of a package's manifest)
    resume.py new FILE KEY=VALUE...              a new transaction (state prepared-request), written atomically
    resume.py set FILE KEY=VALUE...              change fields of a transaction (state=prepared, state=installed)
    resume.py get FILE KEY...                    KEY=VALUE lines, for a shell
    resume.py find DIR [SERIAL CID]              the newest transaction in DIR/*/transaction.json still waiting for
                                                 this device (any device without SERIAL and CID); nothing when none
    resume.py check FILE KEY=VALUE...            RESUME-OK, or RESUME-REFUSED: <why> and status 1

The facts `check` takes, as the installers read them from the device: serial, slot (the slot Android runs from),
cid, node, start, size, disk (userdata's node, first sector and sectors, and the eMMC's sectors, from sysfs),
gpt_first, gpt_end, usable_end (resize-last-partition.py --show of the table read back: both copies and every CRC
valid), region_start, region_end (the free region by the installers' own arithmetic), marker (a file: the first
4096 bytes of that region), bcb (a file: the first 2048 bytes of misc), identity (resume.py identity of the installer
that runs now).
"""
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

FORMAT = 1
MARKER = b'MU300-PREPARED-V1 '
BCB_COMMAND = b'boot-recovery'
BCB_RECOVERY = b'recovery\n--wipe_data\n--reason=mu300-make-room\n'
# the files of this project that decide what a resumed installation writes where: the partition table's reader, the
# region's arithmetic, what formats and fills the region on the device, and what boots it. kano binds his
# transaction to his boot image, rootfs and filesystem tools in the same way (resume_payload_id): a repaired prompt
# may change, these may not.
TOOLS = ('tools/resize-last-partition.py', 'tools/storage.sh', 'tools/android-install.sh',
         'tools/android-mount-mu300root.sh', 'boot/init')
INTS = ('start', 'size', 'disk', 'target', 'need', 'gpt_first', 'gpt_end', 'usable_end', 'region_start', 'region_end')
WAITING = ('prepared-request', 'prepared')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def token():
    return secrets.token_hex(16)


def marker(tok):
    """The block at the start of the prepared region: outside every partition, so the reset that formats userdata
    leaves it, and the resumed run finds it there."""
    if len(tok) != 32 or any(c not in '0123456789abcdef' for c in tok):
        raise ValueError('not a token')
    return (MARKER + tok.encode() + b'\n').ljust(4096, b'\0')


def bcb():
    """AOSP's bootloader_message, as kano's part_prepare writes it and as he says the vendor flashing tool's
    erase-userdata does: 2048 bytes, "boot-recovery" at 0 and the recovery's arguments at 64, the rest zero. It is
    the first 2 KiB of misc; the A/B boot control block at 2048 is not part of it."""
    out = bytearray(2048)
    out[:len(BCB_COMMAND)] = BCB_COMMAND
    out[64:64 + len(BCB_RECOVERY)] = BCB_RECOVERY
    return bytes(out)


def bcb_pending(data):
    """Whether misc still asks recovery for this project's reset: it has not run yet."""
    return data[:len(BCB_COMMAND) + 1] == BCB_COMMAND + b'\0' and BCB_RECOVERY in data[64:64 + 768]


def encrypted(state, type_, source, magic):
    """kano's rule (installer.py, encrypted): FBE can have a perfectly readable filesystem superblock, so a signature
    alone never says "not encrypted". True for an encrypted state or type, or /data on a device-mapper node; False only
    for "unencrypted" with a plain F2FS or ext4 superblock on userdata; anything else is unknown (ValueError)."""
    if state == 'encrypted' or type_ in ('file', 'block') or '/dm-' in source or '/mapper/' in source:
        return True
    if state == 'unencrypted' and magic in ('1020f5f2', '53ef'):
        return False
    raise ValueError("Android's encryption state is unknown")


def identity(top, manifest=None):
    """The payload identity: the checksums of TOOLS, and of a package's two payload files when it has a manifest
    (tools/package.sh). Canonical JSON, hashed, as kano's resume_payload_id."""
    top = Path(top)
    files = {}
    for name in TOOLS:
        p = top / name
        files[name] = sha256_file(p) if p.is_file() else 'missing'
    package = {}
    if manifest:
        m = {}
        for line in Path(manifest).read_text(encoding='utf-8', errors='replace').splitlines():
            k, sep, v = line.partition('=')
            if sep:
                m[k] = v.strip()
        package = {m.get('KERNEL_ASSET', ''): m.get('SHA256_KERNEL', ''), m.get('ROOTFS_ASSET', ''): m.get('SHA256_ROOTFS', '')}
    canonical = {'format': FORMAT, 'layout': 'mu300-free-region', 'files': files, 'package': package}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load(path):
    with open(path, encoding='utf-8') as f:
        r = json.load(f)
    if not isinstance(r, dict) or r.get('format') != FORMAT:
        raise ValueError('not a transaction of this installer')
    return r


def save(path, record):
    """Atomically: a transaction is never half written (kano: transaction())."""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def typed(pairs):
    out = {}
    for kv in pairs:
        k, sep, v = kv.partition('=')
        if not sep or not k:
            raise ValueError('not KEY=VALUE: ' + kv)
        out[k] = (int(v) if v.strip() else -1) if k in INTS else v
    return out


def new(path, fields):
    r = {'format': FORMAT, 'state': 'prepared-request', 'created': time.strftime('%Y-%m-%dT%H:%M:%S')}
    r.update(fields)
    for k in ('serial', 'slot', 'cid', 'node', 'start', 'size', 'disk', 'target', 'need', 'usable_end', 'region_start',
              'region_end', 'token', 'identity'):
        if k not in r or r[k] in ('', None):
            raise ValueError('a transaction needs ' + k)
    marker(r['token'])
    save(path, r)
    return r


def find(directory, serial='', cid=''):
    """The newest transaction still waiting, for this device when SERIAL and CID are given."""
    best = None
    for p in Path(directory).glob('*/transaction.json'):
        try:
            r = load(p)
        except (OSError, ValueError):
            continue
        if r.get('state') not in WAITING:
            continue
        if serial and (r.get('serial') != serial or r.get('cid') != cid):
            continue
        key = (p.stat().st_mtime, str(p))
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


def check(r, facts):
    """Raise ValueError with the reason when this device is not the one, or not in the state, the transaction
    prepared (kano's validate_resume and validate_resume_package, and this project's own: the table read back with
    both copies and every CRC valid, the region by the installers' arithmetic, the reset having run)."""
    f = dict(facts)
    if r.get('state') not in WAITING + ('installed',):
        raise ValueError('the transaction is %s, not one to resume' % r.get('state'))
    if r.get('state') == 'prepared-request':
        raise ValueError('the preparation did not finish (the transaction was saved, the device may be unchanged); '
                         'run the installer without --resume')
    if 'bcb' in f and bcb_pending(Path(f['bcb']).read_bytes()):
        raise ValueError("Android's recovery has not reset the device yet (misc still asks for it): reboot the device "
                         '(adb reboot), set Android up again, and run the installer once more')
    for k in ('serial', 'slot', 'cid', 'node', 'start', 'disk'):
        if str(f.get(k)) != str(r.get(k)):
            raise ValueError('the device or its slot differs from the saved transaction (%s)' % k)
    if int(f.get('size', -1)) != r['target']:
        raise ValueError('the prepared partition geometry differs (userdata is %s sectors, the transaction made it %s)'
                         % (f.get('size'), r['target']))
    want = {'gpt_first': r['start'], 'gpt_end': r['start'] + r['target'] - 1, 'usable_end': r['usable_end'],
            'region_start': r['region_start'], 'region_end': r['region_end']}
    for k, v in want.items():
        if int(f.get(k, -1)) != v:
            raise ValueError('the prepared partition geometry differs (%s)' % k)
    if r['region_end'] - r['region_start'] != r['need']:
        raise ValueError('the transaction does not add up (its region is not the size it reserved)')
    if r.get('state') != 'installed':
        got = Path(f['marker']).read_bytes()[:4096] if f.get('marker') else b''
        if got != marker(r['token']):
            raise ValueError('preparation token mismatch; refusing to overwrite the region')
    if f.get('identity') != r['identity']:
        raise ValueError("this installer's tools or the package differ from the ones that prepared the installation")


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == 'token':
            print(token())
        elif cmd == 'marker':
            Path(args[1]).write_bytes(marker(args[0]))
        elif cmd == 'bcb':
            Path(args[0]).write_bytes(bcb())
        elif cmd == 'encrypted':
            # KEY=VALUE (state= type= source= magic=): an empty value is never a missing argument (Windows
            # PowerShell 5.1 drops empty arguments on the way to a program)
            f = typed(args)
            try:
                print('yes' if encrypted(f.get('state', ''), f.get('type', ''), f.get('source', ''), f.get('magic', '')) else 'no')
            except ValueError:
                print('unknown')
        elif cmd == 'identity':
            print(identity(args[0], args[1] if len(args) > 1 and args[1] else None))
        elif cmd == 'new':
            new(args[0], typed(args[1:]))
        elif cmd == 'set':
            r = load(args[0])
            r.update(typed(args[1:]))
            r['changed'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            save(args[0], r)
        elif cmd == 'get':
            r = load(args[0])
            for k in args[1:]:
                print('%s=%s' % (k, r.get(k, '')))
        elif cmd == 'find':
            p = find(args[0], *(args[1:3] if len(args) >= 3 else ()))
            if p:
                print(p)
        elif cmd == 'check':
            try:
                check(load(args[0]), typed(args[1:]))
            except (ValueError, KeyError, OSError) as e:
                print('RESUME-REFUSED: %s' % (e.args[0] if e.args else e))
                return 1
            print('RESUME-OK')
        else:
            print('unknown command ' + cmd, file=sys.stderr)
            return 2
    except (ValueError, IndexError, OSError) as e:
        print('resume.py: %s' % e, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
