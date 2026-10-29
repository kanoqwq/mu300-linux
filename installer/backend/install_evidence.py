"""Bounded read-only evidence for removing an installation without its old journal.

Only fixed, small files are read from an idle ext4 partition. During an explicit
uninstall check, a verified installation may replay its journal using Android's
e2fsck. No mount, general filesystem repair or partition-table write is used.
This proves a known installation, not the historical size of Android userdata.
"""
from . import storage as storage_io
import hashlib
import re
import stat
import struct
from contextlib import ExitStack

from .boot_image import inspect_linux_boot
from .state import RecoveryRequired, DeviceLock
from .uninstall_boot import archives, entries


class Ext4Evidence:
    """Minimal ext4 reader for regular marker files and their directories only."""
    def __init__(self, read, capacity, *, pending_journal=False):
        self.read = read
        self.capacity = capacity
        self.remaining = 4 << 20
        self.cache = {}
        self.super = sb = self.raw(1024, 1024)
        if sb[56:58] != b'\x53\xef':
            raise ValueError('OpenWrt partition is not ext4')
        log = struct.unpack_from('<I', sb, 24)[0]
        incompat = struct.unpack_from('<I', sb, 96)[0]
        state = struct.unpack_from('<H', sb, 58)[0]
        self.needs_recovery = bool(incompat & 4)
        if log > 2 or incompat & ~(0x2 | 4 | 0x40 | 0x80 | 0x200 | 0x2000):
            raise ValueError('Unsupported ext4 layout / ext4 布局不受支持: incompat=0x%x, block_shift=%d' % (incompat, log))
        if state != 1:
            raise ValueError('OpenWrt ext4 reports filesystem errors; journal-only recovery is insufficient / OpenWrt ext4 记录了文件系统错误，不能只靠日志回放修复')
        if self.needs_recovery and not pending_journal:
            raise ValueError('OpenWrt ext4 journal needs recovery / OpenWrt ext4 日志需要回放')
        if self.needs_recovery and (not struct.unpack_from('<I',sb,92)[0] & 4 or
                not struct.unpack_from('<I',sb,224)[0]):
            raise ValueError('Missing internal ext4 journal / 缺少 ext4 内部日志')
        self.block_size = 1024 << log
        self.blocks = struct.unpack_from('<I', sb, 4)[0]
        if incompat & 0x80:
            self.blocks |= struct.unpack_from('<I', sb, 336)[0] << 32
        self.inodes, = struct.unpack_from('<I', sb, 0)
        self.per_group, = struct.unpack_from('<I', sb, 40)
        self.inode_size, = struct.unpack_from('<H', sb, 88)
        self.desc_size = struct.unpack_from('<H', sb, 254)[0] if incompat & 0x80 else 32
        if (not self.blocks or self.blocks*self.block_size > capacity or not self.inodes or
                not self.per_group or not 128 <= self.inode_size <= self.block_size or
                self.inode_size & (self.inode_size-1) or self.desc_size not in (32,64) or
                sb[104:120] == bytes(16)):
            raise ValueError('Invalid OpenWrt filesystem geometry or identity')
        self.filesystem_limit = self.blocks*self.block_size

    def raw(self, offset, size):
        if offset < 0 or size <= 0 or offset+size > getattr(self,'filesystem_limit',self.capacity):
            raise ValueError('Filesystem read exceeds the OpenWrt partition')
        key = (offset,size)
        if key not in self.cache:
            self.remaining -= size
            if self.remaining < 0:
                raise ValueError('Installation evidence exceeds the metadata read limit')
            data = self.read(offset,size)
            if len(data) != size:
                raise ValueError('Incomplete OpenWrt metadata read')
            self.cache[key] = data
        return self.cache[key]

    def block(self, number):
        if not 0 < number < self.blocks:
            raise ValueError('Invalid OpenWrt metadata block')
        return self.raw(number*self.block_size,self.block_size)

    def inode(self, number):
        if not 1 <= number <= self.inodes:
            raise ValueError('Invalid OpenWrt inode')
        group, index = divmod(number-1,self.per_group)
        offset = (2 if self.block_size == 1024 else 1)*self.block_size+group*self.desc_size
        desc = self.raw(offset,self.desc_size)
        table = struct.unpack_from('<I',desc,8)[0]
        if self.desc_size == 64:
            table |= struct.unpack_from('<I',desc,40)[0] << 32
        if not 0 < table < self.blocks:
            raise ValueError('Invalid OpenWrt inode table')
        return self.raw(table*self.block_size+index*self.inode_size,self.inode_size)

    def contents(self, inode, kind, limit):
        mode = struct.unpack_from('<H',inode,0)[0]
        if stat.S_IFMT(mode) != kind or not struct.unpack_from('<H',inode,26)[0]:
            raise ValueError('Installation evidence must use regular files and directories, without symlinks / 安装标记路径必须是普通目录和文件，不能是符号链接')
        size = struct.unpack_from('<I',inode,4)[0] | struct.unpack_from('<I',inode,108)[0] << 32
        if not 0 < size <= limit:
            raise ValueError('Installation evidence file is empty or too large')
        count = (size+self.block_size-1)//self.block_size
        pointers = {}
        flags = struct.unpack_from('<I',inode,32)[0]
        if flags & (0x4 | 0x800 | 0x100000 | 0x10000000):
            raise ValueError('Unsupported OpenWrt evidence inode flags')
        if not flags & 0x80000:  # old files may use direct block pointers
            if count > 12:
                raise ValueError('Unsupported indirect evidence file')
            pointers = dict(enumerate(struct.unpack_from('<12I',inode,40)[:count]))
        else:
            visited = set()
            def extents(raw, expected=None):
                magic, used, maximum, depth = struct.unpack_from('<HHHH',raw,0)
                if (magic != 0xf30a or not 0 < used <= maximum <= (len(raw)-12)//12 or
                        depth > 4 or expected is not None and depth != expected):
                    raise ValueError('Invalid OpenWrt extent tree')
                for at in range(12,12+used*12,12):
                    logical, = struct.unpack_from('<I',raw,at)
                    if depth:
                        low, high = struct.unpack_from('<IH',raw,at+4)
                        block = low | high << 32
                        if block in visited:
                            raise ValueError('Repeated OpenWrt extent node')
                        visited.add(block)
                        extents(self.block(block),depth-1)
                    else:
                        length, high, low = struct.unpack_from('<HHI',raw,at+4)
                        if not 0 < length <= 32768:
                            raise ValueError('Unwritten OpenWrt evidence extent')
                        physical = low | high << 32
                        if physical <= 0 or physical+length > self.blocks:
                            raise ValueError('OpenWrt extent exceeds the filesystem')
                        for n in range(logical,min(logical+length,count)):
                            if n in pointers:
                                raise ValueError('Overlapping OpenWrt extents')
                            pointers[n] = physical+n-logical
            extents(inode[40:100])
        if set(pointers) != set(range(count)):
            raise ValueError('Installation evidence contains sparse or missing blocks')
        return b''.join(self.block(pointers[n]) for n in range(count))[:size]

    def file(self, path):
        node = self.inode(2)
        for component in path.split('/'):
            content = self.contents(node,stat.S_IFDIR,256 << 10)
            found = []
            at = 0
            while at < len(content):
                if len(content)-at < 8:
                    raise ValueError('Truncated OpenWrt directory')
                number, length, namelen = struct.unpack_from('<IHB',content,at)
                if (length < 8 or length % 4 or length > self.block_size-at%self.block_size or
                        at+length > len(content) or namelen > length-8):
                    raise ValueError('Invalid OpenWrt directory entry')
                if number and content[at+8:at+8+namelen] == component.encode():
                    found.append(number)
                at += length
            if len(found) != 1:
                raise ValueError('Missing or ambiguous installed-system marker / 安装标记缺失或存在重名: '+path)
            node = self.inode(found[0])
        return self.contents(node,stat.S_IFREG,4096)


def inspect_installation(adapter, device, root, *, boot_backup=None, recovery_store=None):
    """Return fresh, device-bound evidence. Caller has already validated both GPTs."""
    try:
        adb = adapter.adb
        storage = device['storage']; disk = storage_io.disk_node(storage['NODE'])
        node = storage_io.part_node(disk,root['n']); capacity = (root['end']-root['start']+1)*512
        if (not re.fullmatch(r'(?:mmcblk\d+p|sd[a-z]+)[1-9][0-9]*',node) or storage.get('ROOT') != node or
                storage.get('ROOT_START') != root['start'] or storage.get('ROOT_SIZE')*512 != capacity):
            raise ValueError('Kernel OpenWrt node differs from verified GPT; reboot Android / 内核分区节点与 GPT 不符，请重启 Android')
        # Android must not have the target mounted or mapped. Only metadata in
        # this partition is read; neither userdata nor an unlisted gap is scanned.
        idle = adb.shell('set -e; n='+node+'; h=$(ls -A /sys/class/block/$n/holders); '
                         'd=$(cat /sys/class/block/$n/dev); '
                         'm=$(awk -v n="$d" \'$3==n {print "BUSY"}\' /proc/self/mountinfo); '
                         'if [ -z "$h$m" ]; then echo IDLE; else echo BUSY; fi',root=True)
        if idle.strip() != 'IDLE':
            raise ValueError('OpenWrt partition is mounted or mapped / OpenWrt 分区仍被挂载或映射，请先释放占用')
        slot = device['slot'].removeprefix('_'); other = 'b' if slot == 'a' else 'a'
        read_boot = adb.read
        if boot_backup is None:
            boot_size = int(adb.shell('blockdev --getsize64 /dev/block/by-name/boot_'+other,root=True).strip())
        else:
            # An interrupted, verified inactive-boot write can replace the live
            # signature. Recovery has checked that write; use its checksummed
            # original boot backup for the installation proof, never an old GPT.
            boot_size = len(boot_backup)
            def read_boot(command):
                match = re.fullmatch(r'dd if=/dev/block/by-name/boot_[ab] bs=(\d+)(?: skip=(\d+))? count=(\d+) 2>/dev/null',command)
                if not match:
                    raise ValueError('Unsupported backup metadata read')
                bs, skip, count = int(match[1]),int(match[2] or 0),int(match[3])
                return boot_backup[bs*skip:bs*(skip+count)]
        boot = inspect_linux_boot(read_boot,boot_size,adapter.kind,other)
        start = 4096+((boot['kernel_bytes']+4095)&~4095)
        size = boot['ramdisk_bytes']; count = (size+4095)//4096
        ram = read_boot(f'dd if=/dev/block/by-name/boot_{other} bs=4096 skip={start//4096} count={count} 2>/dev/null')
        if len(ram) != count*4096:
            raise ValueError('Incomplete installed ramdisk')
        files = {}
        wanted = {'init','etc/mu300-desktop-root-mode','etc/mu300-desktop-identity','etc/mu300-desktop-prepare'}
        for archive in archives(ram[:size]):
            for name, content, mode in entries(archive):
                if name in wanted:
                    if stat.S_IFMT(mode) != stat.S_IFREG or len(content) > 256 << 10:
                        raise ValueError('Unsupported installed boot evidence')
                    files[name] = content
        if adapter.kind == 'e5':
            if b'/etc/e5/part-root' not in files.get('init',b''):
                raise ValueError('Installed E5 boot does not recognize internal partitions / 已安装的 E5 启动镜像不支持内置分区')
            paths = {'etc/e5/part-root':None,'etc/openwrt_release':None}
        else:
            # install-part boots keep this mode even after a successful install;
            # completed root markers below distinguish them from preparation.
            if (files.get('etc/mu300-desktop-root-mode',b'').strip() not in (b'part',b'install-part') or
                    'etc/mu300-desktop-prepare' in files or
                    b'/etc/mu300-desktop-storage.sh' not in files.get('init',b'')):
                raise ValueError('Installed MU300 boot has no completed internal-partition route / 已安装的 MU300 启动镜像未确认完成内置分区安装')
            paths = {'openwrt/etc/mu300/desktop-mode':b'part', '.mu300/boot-os':None,'openwrt/etc/openwrt_release':None}
        def read(offset,length):
            begin = offset//512; end = (offset+length+511)//512
            raw = adb.read(f'dd if=/dev/block/{node} bs=512 skip={begin} count={end-begin} 2>/dev/null')
            if len(raw) != (end-begin)*512:
                raise ValueError('Incomplete OpenWrt partition read')
            return raw[offset%512:offset%512+length]
        fs = Ext4Evidence(read,capacity,pending_journal=recovery_store is not None)
        for path, expected in paths.items():
            value = fs.file(path).strip()
            if expected is not None and value != expected:
                raise ValueError('Installed marker differs / 安装标记内容不符: '+path)
            if path.endswith('openwrt_release') and not re.search(rb"(?m)^DISTRIB_ID=['\"]OpenWrt['\"]$",value):
                raise ValueError('OpenWrt distribution identity is missing / 缺少 OpenWrt 系统身份信息')
            if path.endswith('part-root') and value not in (('e5root /dev/'+disk).encode(),('e5root /dev/block/'+disk).encode()):
                raise ValueError('E5 partition marker names a different disk / E5 安装标记指向另一块磁盘')
            if path == '.mu300/boot-os' and value not in (b'android',b'openwrt'):
                raise ValueError('MU300 boot selector is unsupported / MU300 启动选择标记不受支持')
            paths[path] = hashlib.sha256(value).hexdigest()
        if fs.needs_recovery:
            # Existing stable markers, Linux boot and GPT ownership were checked
            # above. Never replay a journal merely because a partition is named
            # *root. A second full read is mandatory after recovery.
            with ExitStack() as locks:
                for identity in sorted({device['serial'],device['cid']}):
                    locks.enter_context(DeviceLock(recovery_store.base,identity))
                layout = adapter.read_gpt(storage)
                matching = [p for p in layout['parts'] if p['n'] == root['n']]
                if len(matching) != 1 or any(matching[0].get(k) != root.get(k) for k in ('start','end','name')):
                    raise ValueError('Partition geometry changed before journal recovery / 日志回放前分区边界发生变化')
                current = read(1024,1024)
                if current != fs.super:
                    raise ValueError('OpenWrt metadata changed before journal recovery / 日志回放前 OpenWrt 元数据发生变化')
                notice = getattr(adapter,'notice',None)
                if callable(notice):
                    notice({'code':'uninstall_journal_recovery'})
                label = 'mu300root' if adapter.kind == 'mu300' else 'e5root'
                script = ('set -eu; n='+node+'; '
                          '[ "$(readlink -f /dev/block/by-name/'+label+' 2>/dev/null || true)" = /dev/block/$n ]')
                script += ('; [ -z "$(ls -A /sys/class/block/$n/holders)" ]; '
                           'd=$(cat /sys/class/block/$n/dev); '
                           '! awk -v n="$d" \'$3==n {found=1} END {exit !found}\' /proc/self/mountinfo; '
                           '[ "$(blockdev --getsize64 /dev/block/$n)" = '+str(capacity)+' ]; '
                           '[ -x /system/bin/e2fsck ]; '
                           'rc=0; /system/bin/e2fsck -p -E journal_only /dev/block/$n </dev/null || rc=$?; '
                           '[ "$rc" = 0 ] || [ "$rc" = 1 ]; sync')
                try:
                    adb.shell(script,root=True,timeout=120)
                except (ConnectionError,OSError,TimeoutError) as error:
                    raise RecoveryRequired('OpenWrt journal recovery did not complete; no partition layout was changed / '
                                           'OpenWrt 日志回放未完成，分区布局未修改: '+str(error)) from error
                after = read(1024,1024)
                if after[104:120] != fs.super[104:120]:
                    raise ValueError('Filesystem identity changed during journal recovery / 日志回放期间文件系统身份变化')
                return inspect_installation(adapter,device,root,boot_backup=boot_backup)
        return {'version':1,'boot_header_sha256':boot['header_sha256'],
                'ramdisk_sha256':hashlib.sha256(ram[:size]).hexdigest(),
                'superblock_sha256':hashlib.sha256(fs.super).hexdigest(),'markers':paths}
    except (ValueError,KeyError,TypeError,struct.error) as error:
        raise RecoveryRequired('Cannot verify the installed OpenWrt partition without original backups / '
                               '没有原备份，且无法核实设备上的 OpenWrt 安装；已停止: '+str(error)) from error
