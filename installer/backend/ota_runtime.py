"""Build a self-contained RAM updater from the target release, deduplicated by SHA.

The same runtime is used by LuCI and desktop bootstrap. An old device needs only
SSH, a shell and the packaged static BusyBox; no installed Python/OTA dependency.
"""
import hashlib
import os
from pathlib import Path
import shlex
import sys


def support_sources():
    if getattr(sys, 'frozen', False):
        folder = Path(sys._MEIPASS)
    elif Path(__file__).with_name('boot.py').is_file():
        folder = Path(__file__).parent
    else:
        folder = Path(__file__).resolve().parents[2]/'openwrt/luci-app-unisoc-ota/root/usr/lib/unisoc-ota'
    result = {name:(folder/('ota_engine.py' if name == 'engine.py' and not (folder/name).is_file() else name)).read_bytes().replace(b'\r\n', b'\n') for name in ('engine.py', 'boot.py', 'rpc.py', 'proxy.sh')}
    native = folder/'lz4raw-aarch64.so'
    if not native.is_file(): native = Path(__file__).resolve().parents[1]/'ota-native/lz4raw-aarch64.so'
    metadata = native.with_name('build.json')
    if metadata.is_file():
        import json
        expected = json.loads(metadata.read_text())
        if hashlib.sha256(native.read_bytes()).hexdigest() != expected['binary_sha256']:
            raise ValueError('Native OTA decoder fingerprint mismatch')
        source = native.with_name('lz4raw.c')
        if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() != expected['source_sha256']:
            raise ValueError('Rebuild the changed native OTA decoder')
    result['lz4raw-aarch64.so'] = native.read_bytes()
    return result


def build(release):
    runtime = {}; extra = {}
    # Keep Python's complete stdlib, extension modules, musl and shared-library
    # dependencies isolated from the live system. Content reused in file deltas
    # is the very same blob, never a second rootfs/runtime archive.
    for name, desc in release.entries.items():
        if (name.startswith(('usr/lib/python3', 'usr/bin/python3')) or
                name.startswith(('lib/', 'usr/lib/')) and '/' not in name.split('/', 2 if name.startswith('usr/') else 1)[-1]
                and ('.so' in name or name.endswith('/libc.so'))):
            if desc['kind'] != 'dir': runtime[name] = desc
    # Symlink targets in e.g. usr/bin/python3 or a versioned library must be in
    # the runtime even when a future release changes the conventional name.
    import posixpath
    for _ in range(8):
        added = False
        for name, desc in list(runtime.items()):
            if desc['kind'] != 'symlink': continue
            target = desc['target']
            target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(name), target))
            if target not in runtime and target in release.entries:
                runtime[target] = release.entries[target]; added = True
        if not added: break
    for name, content in support_sources().items():
        h = hashlib.sha256(content).hexdigest(); extra[h] = content
        runtime['ota/'+name] = dict(kind='file', mode=0o644, uid=0, gid=0, xattrs={}, size=len(content), sha256=h)
    busybox = next((n for n in ('files/busybox', 'payload/busybox', 'busybox') if release.exists(n)), None)
    bb = None
    if busybox:
        with release.open(busybox) as stream: content = stream.read(16 << 20)
        h = hashlib.sha256(content).hexdigest(); extra[h] = content
        bb = dict(sha256=h, size=len(content))
        runtime['ota/busybox'] = dict(kind='file', mode=0o700, uid=0, gid=0, xattrs={}, **bb)
    return runtime, bb, extra


def bootstrap(runtime, busybox):
    """Generated commands refer only to validated paths and content hashes."""
    lines = ['#!/bin/sh', 'set -eu', 'umask 077', 'patch=$1', 'command=$2', 'home=$3',
             'id=${home#/tmp/unisoc-ota-}',
             'case "$id" in *[!0-9a-f]*|\'\') exit 2 ;; esac',
             '[ "$home" != "$id" ] && [ ${#id} -eq 64 ] && [ ! -L "$home" ]',
             'bb="$home/busybox"', 'root="$home/root"', 'mkdir -p "$root" "$home/content"']
    hashes = sorted({d['sha256'] for d in runtime.values() if d['kind'] == 'file'})
    lines += ['"$bb" unzip -oq "$patch" '+' '.join('blobs/'+h for h in hashes)+' -d "$home/content"',
              '"$bb" cat > "$home/runtime.sha256" <<\'OTA_RUNTIME_HASHES\'']
    lines.extend(h+'  content/blobs/'+h for h in hashes)
    lines += ['OTA_RUNTIME_HASHES', '(cd "$home"; "$bb" sha256sum -c runtime.sha256 >/dev/null)']
    for name, desc in sorted(runtime.items()):
        path = '"$root"/'+shlex.quote(name)
        lines.append('"$bb" mkdir -p '+ '"$root"/'+shlex.quote(str(Path(name).parent).replace('\\', '/')))
        if desc['kind'] == 'file':
            lines += ['"$bb" cp "$home/content/blobs/'+desc['sha256']+'" '+path+'.new',
                      '"$bb" chmod '+oct(desc['mode'])[2:]+' '+path+'.new',
                      '"$bb" mv '+path+'.new '+path]
        else:
            lines.append('"$bb" ln -snf '+shlex.quote(desc['target'])+' '+path)
    lines += ['"$bb" rm -rf "$home/content"',
              '"$bb" mkdir -p "$root/target" "$root/dev" "$root/proc" "$root/sys" "$root/target/dev" "$root/target/run" "$root/target/sys"',
              '"$bb" mountpoint -q "$root/target" || "$bb" mount --bind / "$root/target"',
              '"$bb" mountpoint -q "$root/dev" || "$bb" mount --bind /dev "$root/dev"',
              '"$bb" mountpoint -q "$root/target/dev" || "$bb" mount --bind /dev "$root/target/dev"',
              '"$bb" mountpoint -q "$root/target/run" || "$bb" mount --bind /run "$root/target/run"',
              '"$bb" mountpoint -q "$root/sys" || "$bb" mount --bind /sys "$root/sys"',
              '"$bb" mountpoint -q "$root/target/sys" || "$bb" mount --bind /sys "$root/target/sys"',
              '"$bb" mountpoint -q "$root/proc" || "$bb" mount -t proc proc "$root/proc"',
              'if [ "$command" = start ]; then printf "%s\\n" "$root" > /etc/unisoc-ota/active-runtime; fi',
              'export UNISOC_OTA_ROOT=/target UNISOC_OTA_RAM=/ UNISOC_OTA_LIVE=1 PYTHONHOME=/usr',
              'case "$command" in',
              ' inspect) exec "$bb" chroot "$root" /usr/bin/python3 /ota/engine.py inspect "/target$patch" --root /target ;;',
              ' start) exec "$bb" chroot "$root" /usr/bin/python3 /ota/rpc.py start "/target$patch" ;;',
              ' status) exec "$bb" chroot "$root" /usr/bin/python3 /ota/rpc.py status ;;',
              ' *) exit 2 ;;', 'esac']
    return ('\n'.join(lines)+'\n').encode()
