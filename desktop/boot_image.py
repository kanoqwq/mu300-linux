"""Append a desktop segment to a MU300 boot without changing its kernel or Magisk input."""
import hashlib
import importlib.util
from pathlib import Path
import stat
import struct
import zlib

PERSIST=48<<20


def boot_control(live, android_slot):
    if len(live)!=32 or live[4:8]!=b'BCAB' or live[8]!=1 or live[9]&7!=2 or zlib.crc32(live[:28])!=struct.unpack_from('<I',live,28)[0]:
        raise ValueError('invalid live A/B boot control')
    if android_slot not in ('a','b'): raise ValueError('unknown Android slot')
    result=bytearray(live)
    linux='b' if android_slot=='a' else 'a'
    result[:4]=('_'+linux).encode()+b'\0\0'
    result[12],result[14]=(0x9e,0x2f) if android_slot=='a' else (0x2f,0x9e)
    result[28:]=struct.pack('<I',zlib.crc32(result[:28]))
    return bytes(result)


def desktop_init(original):
    if '# U30PRO-MU300 storage bootstrap' in original:
        return original.encode()
    # The stock TF init and its filesystem scan remain byte-for-byte unchanged.
    start=original.index('# A TF-only image must never fall through')
    end=original.index('mkdir -p /newroot',start)
    normal=original[start:end]
    addon='''# Desktop boot only: native MU300 container layout and explicit target.
. /etc/mu300-desktop-storage.sh
DESKTOP_MODE=$(cat /etc/mu300-desktop-root-mode)
if [ "$DESKTOP_MODE" = legacy ]; then
'''+normal.replace('root_target=$(cat /etc/mu300-root-target 2>/dev/null)','root_target=auto')+'''
else
root_target=sd
sd_root= rootloop= internal_root= root_mounted=0
mkdir -p /disk
case "$DESKTOP_MODE" in prepare|install-part) setup_usb_gadget; DESKTOP_USB_READY=1 ;; esac
desktop_storage_start || { restore_android; reboot -f; while :; do sleep 10; done; }
desktop_mount_root && root_mounted=1
[ "$root_mounted" != 1 ] || read_usb_boot_policy
[ "${DESKTOP_USB_READY:-}" = 1 ] || setup_usb_gadget
fi
'''
    return (original[:start]+addon+original[end:]).encode()


def build(base, assets, mode, live, android_slot, prepare=None, identity=None):
    if mode not in ('sd','part','data-image','prepare','install-part','legacy'): raise ValueError('unsupported desktop target')
    if base[:8]!=b'ANDROID!' or len(base)!=(64<<20) or base[-64:-60]!=b'AVBf' or struct.unpack_from('<I',base,40)[0]!=4: raise ValueError('64 MiB boot v4 with AVB footer required')
    assets=Path(assets)
    spec=importlib.util.spec_from_file_location('mu300_boot_builder',assets/'build-boot-image.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    files={'init':(desktop_init((assets/'original-init').read_text(encoding='utf-8')),stat.S_IFREG|0o755),
           'etc/mu300-desktop-root-mode':((mode+'\n').encode(),stat.S_IFREG|0o644),
           'etc/mu300-desktop-storage.sh':((assets/'storage.sh').read_bytes(),stat.S_IFREG|0o644)}
    if (assets/'native-u30pro').is_file():
        files['etc/mu300-android-slot']=((android_slot+'\n').encode(),stat.S_IFREG|0o644)
    files['etc/mu300-internal-ext4.sh']=((assets/'internal-ext4.sh').read_bytes(),stat.S_IFREG|0o644)
    files['etc/mu300-storage.sh']=((assets/'storage-lib.sh').read_bytes(),stat.S_IFREG|0o644)
    for name in ('part-prepare.sh','part-progress.sh','install-part.sh','ext4-geometry.sh'):
        files['etc/'+name]=((assets/name).read_bytes(),stat.S_IFREG|0o644)
    files['usr/share/mu300-gpt']=((assets/'mu300-gpt').read_bytes(),stat.S_IFREG|0o755)
    repair=assets/'repair'
    expected=__import__('json').loads((repair/'manifest.json').read_text())['sha256']
    dirs={'etc','usr','usr/bin','usr/share','usr/lib','usr/lib/ucode','lib','repair'}
    for name,want in expected.items():
        data=(repair/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=want: raise ValueError('repair tool checksum: '+name)
        files['repair/'+name]=(data,stat.S_IFREG|0o755)
        files[name]=(data,stat.S_IFREG|0o755)
        for entry in (name,'repair/'+name):
            parts=entry.split('/')
            dirs.update('/'.join(parts[:i]) for i in range(1,len(parts)))
    files['repair/lib/libc.so']=(b'ld-musl-aarch64.so.1',stat.S_IFLNK|0o777)
    files['lib/libc.so']=(b'ld-musl-aarch64.so.1',stat.S_IFLNK|0o777)
    if prepare: files['etc/mu300-desktop-prepare']=(prepare.encode()+b'\n',stat.S_IFREG|0o600)
    if identity: files['etc/mu300-desktop-identity']=(identity.encode()+b'\n',stat.S_IFREG|0o600)
    padded=bytes(2048)+live
    aa,lb,ab,la=builder.bootloader_control(padded)
    for n,data in [('a',aa),('b',ab),('a-trial',la),('b-trial',lb)]:
        files['etc/misc-bc-slot-'+n+'.bin']=(data,stat.S_IFREG|0o644)
    ks,rs=struct.unpack_from('<II',base,8)
    offset=4096+((ks+4095)&~4095)
    if offset+rs>len(base)-64: raise ValueError('invalid boot ramdisk bounds')
    ram=base[offset:offset+rs]+builder.lz4_legacy(builder.cpio_archive(dirs,files))
    hdr=bytearray(base[:4096]);struct.pack_into('<I',hdr,12,len(ram))
    body=bytes(hdr)+base[4096:offset]+ram
    body=body.ljust((len(body)+4095)&~4095,b'\0');original_size=len(body)
    avb_offset,avb_size=struct.unpack_from('>QQ',base,len(base)-44)
    if avb_offset+avb_size>len(base)-64:raise ValueError('invalid AVB bounds')
    body+=base[avb_offset:avb_offset+avb_size]
    if len(body)>PERSIST:raise ValueError('desktop ramdisk would overlap persistent log; nothing flashed')
    body=body.ljust(len(base)-64,b'\0')
    footer=bytearray(base[-64:]);struct.pack_into('>QQQ',footer,12,original_size,original_size,avb_size)
    return body+bytes(footer)
