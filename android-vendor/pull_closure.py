"""Pull an Android binary and its shared-library closure from the device, preserving paths.
Search order approximates the vendor linker namespace: vendor, VNDK apex, system, bionic."""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get('MU300_CLOSURE_ROOT', 'rootfs'))
SEARCH = ['/vendor/lib64', '/vendor/lib64/hw', '/vendor/lib64/egl', '/odm/lib64', '/apex/com.android.vndk.v33/lib64',
          '/system/lib64', '/apex/com.android.runtime/lib64/bionic', '/apex/com.android.runtime/lib64',
          '/apex/com.android.i18n/lib64', '/system_ext/lib64']
DEV = '/data/local/tmp/mu300-pull.bin'
# A transfer that never finishes used to leave the installer waiting for ever - the only way out was Ctrl-C,
# and the traceback then looked like a crash in this script (issue: pull_closure.py KeyboardInterrupt). Every
# call now has a deadline and is retried, so a stalled or rebooting device costs a few seconds, not the run.
TIMEOUT = 120
TRIES = 30
TMP = Path(tempfile.mkdtemp(prefix='mu300-pull-'))
WORK = TMP / 'file.bin'


def run(args, timeout=TIMEOUT, **kw):
    """subprocess.run with a deadline: None when it did not finish in time"""
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        print(f'  adb did not answer within {timeout}s, retrying', file=sys.stderr, flush=True)
        return None


def adb(cmd):
    """run one command as root on the device and return its output (b'' when the command failed)"""
    for _ in range(TRIES):
        r = run(['adb', 'exec-out', f"su -c '{cmd}; echo __RC$?'"], capture_output=True)
        if r is None:
            continue
        out = r.stdout
        i = out.rfind(b'__RC')
        if r.returncode == 0 and i >= 0:
            # text can come back CRLF-ified when su runs on a pty; callers want plain LF
            return out[:i].replace(b'\r\n', b'\n') if out[i + 4:].strip() == b'0' else b''
        time.sleep(5)
    raise SystemExit('adb unavailable (device gone, or su stopped working?)')


def adb_shell(cmd):
    """no output wanted; the device may take its time, but not for ever"""
    run(['adb', 'shell', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def locate(libs):
    """the device path of each library, asked for in one call for the whole batch: checking one library in
    one directory per adb call - up to ten calls per library - is both slow and the thing that hung"""
    if not libs:
        return {}
    script = '; '.join('for d in ' + ' '.join(SEARCH) + f'; do [ -e $d/{lib} ] && {{ echo $d/{lib}; break; }}; done'
                       for lib in libs)
    found = {}
    for line in adb(script).decode(errors='replace').split():
        d, _, name = line.rpartition('/')
        if d and name not in found:
            found[name] = line
    return found


# A binary read must not go through `adb exec-out "su -c ..."`: on some devices su gives the command a pty
# whose ONLCR rewrites every LF as CRLF, and the file arrives inflated and unparsable (issue #2).
def adb_pull_file(path):
    adb_shell(f"su -c 'cat {path} > {DEV}'")
    if WORK.exists():
        WORK.unlink()
    r = run(['adb', 'pull', DEV, str(WORK)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    adb_shell(f"su -c 'rm -f {DEV}'")
    if r is None or r.returncode != 0 or not WORK.is_file():
        return b''
    return WORK.read_bytes()


def pull(path):
    dst = ROOT / path.lstrip('/')
    # a file an interrupted run left behind is not a file that was pulled: only a non-empty one counts
    if dst.is_file() and dst.stat().st_size:
        return dst
    real = adb(f'readlink -f {path}').decode().strip() or path
    data = adb_pull_file(real)
    if not data or (path.endswith('.so') and not data.startswith(b'\x7fELF')):
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return dst


def needed(p):
    """the DT_NEEDED names of an ELF file; nothing to follow when it is not one this can read"""
    try:
        b = p.read_bytes()
        if b[:4] != b'\x7fELF':
            return []
        shoff, = struct.unpack_from('<Q', b, 0x28)
        phoff, = struct.unpack_from('<Q', b, 0x20)
        phnum, = struct.unpack_from('<H', b, 0x38)
        dyn = None
        loads = []
        for i in range(phnum):
            t, f, off, va, pa, fs, ms, al = struct.unpack_from('<IIQQQQQQ', b, phoff + i * 56)
            if t == 2:
                dyn = (off, fs)
            if t == 1:
                loads.append((va, off, fs))
        if not dyn:
            return []
        ents = [struct.unpack_from('<qQ', b, dyn[0] + j) for j in range(0, dyn[1], 16)]
        strtab = [v for t, v in ents if t == 5][0]
        stroff = [o + strtab - va for va, o, fs in loads if va <= strtab < va + fs][0]
        out = []
        for t, v in ents:
            if t == 1:
                e = b.index(b'\0', stroff + v)
                out.append(b[stroff + v:e].decode())
        return out
    except (struct.error, IndexError, ValueError, OSError, UnicodeDecodeError):
        return []


def main():
    roots = list(dict.fromkeys(sys.argv[1:]))
    if not roots:
        sys.exit('usage: pull_closure.py <binary or library> [...]  (MU300_CLOSURE_ROOT sets the output)')
    todo, seen, missing = list(roots), set(), []
    while todo:
        path = todo.pop(0)
        if path in seen:
            continue
        seen.add(path)
        print(f'  {path}', flush=True)
        p = pull(path)
        if not p:
            missing.append(path)
            continue
        wanted = []
        for lib in needed(p):
            # anything already pulled, in any of the directories the vendor linker would look in, needs no
            # question: that keeps a second run (the installer repeats this step) down to no device calls
            if any((ROOT / f'{d}/{lib}'.lstrip('/')).is_file() for d in SEARCH):
                continue
            if any(f'{d}/{lib}' in seen for d in SEARCH):
                continue
            wanted.append(lib)
        found = locate(wanted)
        for lib in wanted:
            hit = found.get(lib)
            if hit:
                todo.append(hit)
            else:
                missing.append(lib)
    print('pulled', len(seen) - len(missing))
    print('missing', sorted(set(missing)))
    # a requested file that is not there means the device does not have what was asked for: say so loudly,
    # the caller decides what to do about it
    gone = [r for r in roots if not (ROOT / r.lstrip('/')).is_file()]
    if gone:
        print('could not pull ' + ' '.join(gone), file=sys.stderr)
    return 1 if gone else 0


if __name__ == '__main__':
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
