#!/usr/bin/env python3
"""Pull the Android vendor runtime needed by modem_control from a rooted device (adb + su) and build the chroot
subset used by the initramfs (/android) and the root filesystem (/opt/mu300/android).

    python3 android-vendor/extract_subset.py [OUTDIR]

These files are proprietary (ZTE/Unisoc/Google) and are NOT part of this repository. Same output as the older
extract-subset.sh; written in Python so it also runs on Windows.

Windows cannot store every name the device sends: the Android property area is a directory of files called
u:object_r:<context>:s0, ':' may not appear in a file name there, and the extraction used to die on the first of
them with "OSError: [Errno 22] Invalid argument". The archive from the device is therefore also kept as
OUTDIR/windows-source.tar.gz, and while that file exists it is the copy the packing tools use
(tools/vendor-overlay.py and boot/build-boot-image.py read it instead of the directory), so a Windows install
gets exactly what a Linux host would have extracted - including the file modes, which the file system there
cannot hold either (the chroot's modem_control has to stay executable).

The directory is built beside its final place and renamed when it is complete: the installer skips this step when
OUTDIR exists, so an interrupted run must not leave a directory behind that later runs then use as it is.
"""
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# what to ask the device for (dereferencing symlinks), and what to keep of it
PULL = [
    '/apex/com.android.runtime', '/system/lib64', '/vendor/bin/modem_control', '/vendor/bin/cp_diskserver',
    '/vendor/bin/refnotify', '/vendor/lib64/lib_crypto.so', '/vendor/bin/sh', '/vendor/bin/toybox_vendor',
    '/vendor/bin/getprop', '/vendor/lib64/libkernelbootcp.trusty.so', '/vendor/etc', '/dev/__properties__',
]
KEEP_PREFIXES = [
    'apex/com.android.runtime/bin/linker64', 'apex/com.android.runtime/lib64/bionic/',
    'vendor/bin/modem_control', 'vendor/bin/cp_diskserver', 'vendor/bin/refnotify', 'vendor/bin/sh',
    'vendor/bin/toybox_vendor', 'vendor/bin/getprop', 'vendor/lib64/libkernelbootcp.trusty.so',
    'vendor/lib64/lib_crypto.so', 'dev/__properties__/',
]
KEEP_EXACT = {
    'vendor/etc/modem_cp_info.xml', 'vendor/etc/modem_sp_info.xml', 'vendor/etc/modem_ch_info.xml',
    'vendor/etc/cp_dump_info.xml', 'vendor/etc/ueventd.rc', 'dev/__properties__',
    # refnotify asks for this RF/Wi-Fi coexistence table; the F50 firmware does not ship it (stock Android logs
    # the same error), so it is copied only when a device happens to have it
    'vendor/etc/wcn_to_mipi.xml',
}
KEEP_EXACT |= {'system/lib64/' + n for n in (
    'libcutils.so', 'libexpat.so', 'liblog.so', 'libhardware_legacy.so', 'libc++.so', 'libbase.so', 'libbinder.so',
    'libbinder_ndk.so', 'libhidlbase.so', 'libutils.so', 'android.system.suspend-V1-ndk.so', 'libtrusty.so',
    'libandroid_runtime_lazy.so', 'libvndksupport.so', 'libz.so', 'libcrypto.so', 'libselinux.so', 'libpcre2.so',
    'libpackagelistparser.so', 'libprocessgroup.so', 'libcgrouprc.so')}

# the archive from the device, kept when this host cannot store everything it contains (see the docstring)
SIDECAR = 'windows-source.tar.gz'
LINKER = '/apex/com.android.runtime/bin/linker64'
# a path the Windows API refuses: these characters, a trailing dot or space, and the DOS device names
BAD_CHARS = set('<>:"|?*')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
# 3.12 asks for permission to keep modes and owners as the archive has them (as tar -xpf does)
EXTRACT_KW = {'filter': 'fully_trusted'} if sys.version_info >= (3, 12) else {}


def wanted(name):
    n = name.lstrip('./')
    return n in KEEP_EXACT or any(n.startswith(p) for p in KEEP_PREFIXES)


def host_can_store(name):
    """Windows is the only platform whose file system cannot hold some of the names from the device"""
    if os.name != 'nt':
        return True
    for part in name.split('/'):
        if not part or part in ('.', '..'):
            continue
        if any(c in BAD_CHARS or ord(c) < 32 for c in part):
            return False
        if part[-1] in ' .' or part.split('.')[0].upper() in RESERVED:
            return False
    return True


def extra_members():
    """the entries this script adds itself (extract-subset.sh does the same): the chroot looks the linker up
    as /system/bin/linker64 and bionic wants a (empty) linker config. As tar members, parents first, so an
    unpacker never has to guess at them - and they are what the sidecar needs whenever it is written."""
    out = []
    for d in ('system/bin', 'linkerconfig'):
        m = tarfile.TarInfo(d)
        m.type, m.mode = tarfile.DIRTYPE, 0o755
        out.append(m)
    link = tarfile.TarInfo('system/bin/linker64')
    link.type, link.linkname, link.mode = tarfile.SYMTYPE, LINKER, 0o777
    out.append(link)
    conf = tarfile.TarInfo('linkerconfig/ld.config.txt')
    conf.size, conf.mode = 0, 0o644
    out.append(conf)
    for m in out:
        m.uid, m.gid, m.uname, m.gname = 0, 0, 'root', 'root'
    return out


def make_extras(tree):
    """the same entries on disk; returns the ones the host would not take (the sidecar offers them anyway)"""
    failed = []
    for m in extra_members():
        target = tree / m.name
        try:
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.issym():
                target.parent.mkdir(parents=True, exist_ok=True)
                if not os.path.lexists(target):
                    # without developer mode (or as a service) Windows refuses this, and then the sidecar
                    # is the only place that carries the symlink the chroot needs
                    os.symlink(m.linkname, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'')
        except OSError as e:
            failed.append(m)
            print(f'  {m.name}: {e}', file=sys.stderr)
    return failed


def write_sidecar(path, tar, members, extras):
    """the whole subset in one file, with the names, modes and links the host file system cannot hold"""
    with tarfile.open(path, 'w:gz') as side:
        for m in extras:
            if m.isdir():
                side.addfile(m)
        for m in members:
            side.addfile(m, tar.extractfile(m) if m.isreg() else None)
        for m in extras:
            if not m.isdir():
                side.addfile(m, io.BytesIO(b'') if m.isreg() else None)


def rmtree(path):
    """remove a tree shutil.rmtree would stop at: the modes the device sends (0444 for the property area)
    become the read-only attribute on Windows, and that blocks deleting the file"""
    def clear(func, p, *_):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    kw = {'onexc': clear} if sys.version_info >= (3, 12) else {'onerror': clear}
    shutil.rmtree(path, **kw)


def make_writable(tree):
    """clear that attribute again: the work directory has to stay deletable, and the modes that reach the
    device come from the sidecar anyway"""
    for root, dirs, files in os.walk(tree):
        for n in dirs + files:
            try:
                os.chmod(os.path.join(root, n), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'android-subset').resolve()
    with tempfile.TemporaryDirectory() as tmp:
        blob = Path(tmp) / 'vendor.tar'
        # Build the tar on the device and pull it as a file. Streaming it through `adb exec-out "su -c ..."`
        # is what the comment here used to claim was binary-clean, and on some devices it is not: su gives
        # the command a pty whose ONLCR turns every LF into CRLF, and the archive arrives corrupt (issue #2).
        devtar = '/data/local/tmp/mu300-subset.tar'
        subprocess.run(['adb', 'shell', f"su -c 'tar -chf {devtar} " + ' '.join(PULL) + " 2>/dev/null'"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        p = subprocess.run(['adb', 'pull', devtar, str(blob)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['adb', 'shell', f"su -c 'rm -f {devtar}'"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        if p.returncode != 0 or not blob.exists() or blob.stat().st_size < 1_000_000:
            sys.exit('pulling the vendor files failed (is the device in rooted Android?)')
        # Everything is checked before the output directory is touched: a half-done run used to leave an
        # empty directory behind, and the callers' "skip if it exists" guard then skipped it for ever.
        with tarfile.open(blob) as tar:
            members = [m for m in tar.getmembers() if wanted(m.name)]
            if not members:
                sys.exit('the archive from the device contains none of the expected files')
            for m in members:
                m.name = m.name.lstrip('./')
            keep, escaped, taken = [], [], set()
            for m in members:
                # names that differ only in case collide on Windows, and a path there stops at ~260 characters
                if host_can_store(m.name) and m.name.lower() not in taken and len(str(out / m.name)) < 240:
                    taken.add(m.name.lower())
                    keep.append(m)
                else:
                    escaped.append(m)
            part = out.with_name(out.name + '.part')
            rmtree(part)
            part.mkdir(parents=True)
            for m in list(keep):
                try:
                    tar.extract(m, part, **EXTRACT_KW)
                except (OSError, tarfile.TarError) as e:
                    keep.remove(m)
                    escaped.append(m)
                    print(f'  {m.name}: {e}', file=sys.stderr)
            extra_failed = make_extras(part)
            extras = extra_members()
            if escaped or extra_failed:
                # one file with the whole subset in it, not only the escaped members: the tools take
                # everything from one place while it exists, so nothing can be taken from the wrong one
                write_sidecar(part / SIDECAR, tar, members, extras)
            if os.name == 'nt':
                make_writable(part)
            if out.is_dir():
                rmtree(out)
            elif os.path.lexists(out):
                os.unlink(out)
            part.rename(out)
    # what the tree holds (the property area is only in the sidecar, so say where it is)
    size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file() and not f.is_symlink())
    note = ''
    if (out / SIDECAR).is_file():
        note = f' (+{len(members) + len(extras)} entries for the device, in {SIDECAR})'
    print(f'{out}: {size // 1024 // 1024} MiB{note}')


if __name__ == '__main__':
    main()
