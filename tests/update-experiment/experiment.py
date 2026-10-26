#!/usr/bin/env python3
"""What an update keeps, measured on real release images (docs/FINDINGS.md 85).

  experiment.py plant ROOT KIND OUT.json [--no-mount] [--live]
                                              put the user state below into the installed system ROOT (KIND: ubuntu
                                              or openwrt) and write down what was planted, with every attribute
                                              (--no-mount: nothing is mounted at the planted mount point; --live: on
                                              a device that is to be used afterwards - no password is changed, no
                                              package is made up in the package manager's records, and nothing the
                                              device needs is replaced: the hotspot's and the VPN's settings, the
                                              SSH host keys and login settings, root's crontab and the cron service,
                                              the panel's saved locks get a line of the user's or are written down
                                              as they are)
  experiment.py verify ROOT OUT.json [--old-version TAG]
                                              the same paths in the updated system ROOT: one line per item,
                                              "id<TAB>status<TAB>what<TAB>detail"
  experiment.py table [--short] TITLE=FILE.tsv ...   the lines of several runs side by side, as a Markdown table

Statuses: kept (there, the same in every attribute that was written down), changed (there, something differs: the
detail says what), held (not in its place, but saved under /etc/mu300/update-kept/, where the update report points),
reinstall (a file of a package the first boot installs again), by design (/tmp, caches, what is under a mount
point: never carried), lost.

Runs as root on a filesystem with extended attributes, in the container run.sh starts; nothing here is specific to
one release.
"""
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys

KEPT_DIR = 'etc/mu300/update-kept'


def lstat_info(path):
    """every attribute of PATH an update could change, or None when it is not there"""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    info ={'type': stat.S_IFMT(st.st_mode), 'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid,
            'mtime': st.st_mtime_ns, 'nlink': st.st_nlink, 'ino': st.st_ino}
    if stat.S_ISREG(st.st_mode):
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                h.update(block)
        info.update(size=st.st_size, sha256=h.hexdigest(), blocks=st.st_blocks)
    elif stat.S_ISLNK(st.st_mode):
        info['target'] = os.readlink(path)
    elif stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode):
        info['rdev'] = st.st_rdev
    try:
        info['xattrs'] = {n: os.getxattr(path, n, follow_symlinks=False).hex()
                          for n in sorted(os.listxattr(path, follow_symlinks=False))}
    except OSError:
        info['xattrs'] = {}
    return info


class Plant:
    """The planted state. Every method adds one item: (id, what it is, paths to compare, how it is judged)."""

    def __init__(self, root, kind, mounts=True, live=False):
        self.root, self.kind, self.items, self.mounts, self.live = root, kind, [], mounts, live

    def p(self, rel):
        return os.path.join(self.root, rel)

    def item(self, id_, what, paths, judge='same', **more):
        self.items.append(dict(id=id_, what=what, paths=paths, judge=judge, **more))

    def write(self, rel, data=b'user data\n', mode=0o644, uid=0, gid=0, mtime=None):
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.lexists(path) and not os.path.isfile(path):
            os.remove(path)
        with open(path, 'wb') as f:
            f.write(data if isinstance(data, bytes) else data.encode())
        os.chown(path, uid, gid)
        os.chmod(path, mode)
        if mtime:
            os.utime(path, ns=(mtime, mtime))
        return rel

    def mkdir(self, rel, mode=0o755, uid=0, gid=0):
        os.makedirs(self.p(rel), exist_ok=True)
        os.chown(self.p(rel), uid, gid)
        os.chmod(self.p(rel), mode)
        return rel

    def modify(self, rel, extra=b'\n# changed by the user\n'):
        """an image file the user edited: the same file, with a line added"""
        with open(self.p(rel), 'ab') as f:
            f.write(extra)
        return rel

    def mine(self, rel, data, mode=0o644, uid=0, gid=0, comment=b'\n# the user\'s line\n'):
        """REL with the user's content; with --live a file the device already has only gets a comment line added
        (its settings stay what they were: the device is used afterwards)"""
        if self.live and os.path.isfile(self.p(rel)):
            return self.modify(rel, comment)
        return self.write(rel, data, mode, uid, gid)

    def have(self, *rels):
        """the first of RELS the image has"""
        for r in rels:
            if os.path.lexists(self.p(r)):
                return r
        return None

    # ---- both systems
    def common(self, home):
        """HOME: a directory that is the user's (/root on OpenWrt, /srv on Ubuntu)"""
        odd = home + '/odd'
        self.mkdir(odd)
        names = ['with space.txt', '-leading-dash', 'new\nline', 'uni\u00e7\u00f6de-\u6f22\u5b57', 'back\\slash', 'glob*?[x]',
                 'trailing ', 'quote\'s"', 'x' * 250, '.hidden', '$(echo no)', 'tab\there']
        self.item('names', 'files with odd names (space, newline, leading dash, UTF-8, backslash, glob characters, '
                           '250 characters, quotes, $(...), tab)',
                  [self.write(f'{odd}/{n}', f'name {i}\n') for i, n in enumerate(names)])
        perms = home + '/perms'
        self.mkdir(perms)
        self.item('modes', 'modes and owners: 0000, setuid 4755, a sticky 1777 directory, a 0700 directory of uid 1000, '
                           'a file of an id no account has (4242:4343)',
                  [self.write(perms + '/none', mode=0o000), self.write(perms + '/suid', mode=0o4755),
                   self.mkdir(perms + '/sticky', 0o1777), self.mkdir(perms + '/private', 0o700, 1000, 1000),
                   self.write(perms + '/private/secret', mode=0o600, uid=1000, gid=1000),
                   self.write(perms + '/noowner', uid=4242, gid=4343)])
        xa = home + '/xattr'
        self.mkdir(xa)
        self.write(xa + '/user-attr')
        os.setxattr(self.p(xa + '/user-attr'), 'user.mu300.comment', b'kept?')
        self.write(xa + '/capability', b'#!/bin/sh\n', 0o755)
        # cap_net_bind_service=ep, as setcap writes it (VFS_CAP_REVISION_2)
        os.setxattr(self.p(xa + '/capability'), 'security.capability',
                    bytes.fromhex('0100000200040000000000000000000000000000'))
        self.mkdir(xa + '/acl-dir')
        # user::rwx, user:1000:rwx, group::r-x, mask::rwx, other::r-x
        acl = bytes.fromhex('02000000' '01000700ffffffff' '02000700e8030000' '04000500ffffffff' '10000700ffffffff'
                            '20000500ffffffff')
        os.setxattr(self.p(xa + '/acl-dir'), 'system.posix_acl_access', acl)
        self.mkdir(xa + '/opaque-dir')
        os.setxattr(self.p(xa + '/opaque-dir'), 'trusted.overlay.opaque', b'y')
        self.item('xattr', 'extended attributes: user.*, a file capability (cap_net_bind_service), a POSIX ACL on a '
                           'directory, trusted.overlay.opaque on a directory (a container layer)',
                  [xa + '/user-attr', xa + '/capability', xa + '/acl-dir', xa + '/opaque-dir'])
        hl = home + '/links'
        self.mkdir(hl)
        self.write(hl + '/a', 'one inode\n')
        os.link(self.p(hl + '/a'), self.p(hl + '/b'))
        self.mkdir(home + '/links2')
        os.link(self.p(hl + '/a'), self.p(home + '/links2/c'))
        self.item('hardlink', 'three names of one file (hard links), two of them in one directory',
                  [hl + '/a', hl + '/b', home + '/links2/c'], group=True)
        sp = self.write(home + '/sparse.img', b'')
        with open(self.p(sp), 'r+b') as f:
            f.truncate(256 << 20)
            f.seek(100 << 20)
            f.write(b'data in the middle')
        self.item('sparse', 'a sparse file (256 MiB long, one block of data)', [sp], sparse=True)
        sl = home + '/symlinks'
        self.mkdir(sl)
        for name, target in (('relative', '../links/a'), ('absolute', '/etc/hostname'), ('dangling', 'nowhere/at/all'),
                             ('to-dir', '.')):
            os.symlink(target, self.p(f'{sl}/{name}'))
        os.utime(self.p(sl + '/relative'), ns=(10 ** 18, 10 ** 18), follow_symlinks=False)
        self.item('symlink', 'symbolic links: relative, absolute, dangling, to a directory; one with its own date',
                  [f'{sl}/{n}' for n in ('relative', 'absolute', 'dangling', 'to-dir')])
        sp = home + '/special'
        self.mkdir(sp)
        os.mkfifo(self.p(sp + '/fifo'), 0o640)
        os.mknod(self.p(sp + '/null'), 0o666 | stat.S_IFCHR, os.makedev(1, 3))
        self.item('special', 'a FIFO and a character device node', [sp + '/fifo', sp + '/null'])
        s = socket.socket(socket.AF_UNIX)
        cwd = os.getcwd()
        os.chdir(self.p(sp))
        s.bind('socket')
        os.chdir(cwd)
        s.close()
        self.item('socket', 'a socket file (a program makes it again when it starts)', [sp + '/socket'], judge='design')
        t = self.write(home + '/dated/file', mtime=981173106123456789)
        os.utime(self.p(home + '/dated'), ns=(981173106000000000, 981173106000000000))
        self.item('times', 'modification times: a file (with nanoseconds) and its directory, from 2001',
                  [t, home + '/dated'])
        many = home + '/many'
        self.item('many', '2000 small files in 20 directories',
                  [self.write(f'{many}/{d:02d}/{i:03d}', f'{d} {i}\n') for d in range(20) for i in range(100)][::97])
        self.mkdir(home + '/repo/.git/objects/ab')
        self.item('readonly', 'read-only files (a git object, mode 0444)',
                  [self.write(home + '/repo/.git/objects/ab/cdef', b'\x78\x01blob', 0o444)])
        self.write('swapfile', os.urandom(4096) * 2048, 0o600)
        self.item('swapfile', 'a swap file (/swapfile, 8 MiB, mode 0600)', ['swapfile'])
        self.write('tmp/scratch', 'x')
        self.write('var/tmp/scratch' if self.kind == 'ubuntu' else 'tmp/scratch2', 'x')
        self.item('tmp', '/tmp and /var/tmp', ['tmp/scratch'], judge='design')
        # a mount point of another filesystem, with something on it
        self.mkdir('mnt/usb')
        self.item('mountpoint', 'the mount point of another filesystem (/mnt/usb): the directory', ['mnt/usb'],
                  judge='mountpoint')
        # (from Android nothing is mounted inside the system: there the mount point is a plain directory)
        if self.mounts and subprocess.run(['mount', '-t', 'tmpfs', 'none', self.p('mnt/usb')]).returncode == 0:
            self.write('mnt/usb/on-the-stick', 'x')
            self.item('mounted', 'what is on that other filesystem (it stays there)', ['mnt/usb/on-the-stick'],
                      judge='design')
        self.item('mu300conf', '/etc/mu300: hotspot and VPN settings',
                  [self.mine('etc/mu300/hotspot.conf', 'SSID=mine\nPSK=secret12\n', 0o600),
                   self.mine('etc/mu300/vpn.conf', 'ENABLE=0\n', 0o600)])
        self.item('hosts', '/etc/hosts with a line added (an image file, modified)', [self.modify('etc/hosts')])
        self.item('project-edit', 'one of the project\'s own programs, edited (/opt/mu300/bin/mu300-wifi): the new '
                                  'release\'s must win, the edited one must not be thrown away',
                  [self.modify('opt/mu300/bin/mu300-wifi', b'\n# local hack\n')], judge='project')

    # ---- Ubuntu
    def ubuntu(self):
        U = 1000
        self.item('netplan', '/etc/netplan/60-user.yaml', [self.write('etc/netplan/60-user.yaml', 'network: {}\n', 0o600)])
        self.mkdir('etc/wireguard', 0o700)
        self.item('wireguard', '/etc/wireguard/wg0.conf (0600, in a 0700 directory)',
                  [self.write('etc/wireguard/wg0.conf', '[Interface]\nPrivateKey = x\n', 0o600)])
        unit = self.write('etc/systemd/system/myapp.service', '[Service]\nExecStart=/opt/myapp/bin/run\n')
        self.mkdir('etc/systemd/system/multi-user.target.wants')
        os.symlink('/etc/systemd/system/myapp.service', self.p('etc/systemd/system/multi-user.target.wants/myapp.service'))
        self.item('unit', 'a hand-written systemd unit, enabled',
                  [unit, 'etc/systemd/system/multi-user.target.wants/myapp.service'])
        self.item('unit-dropin', 'a drop-in for an image unit (/etc/systemd/system/ssh.service.d/override.conf)',
                  [self.write('etc/systemd/system/ssh.service.d/override.conf', '[Service]\nRestart=always\n')])
        os.symlink('/dev/null', self.p('etc/systemd/system/apt-daily.service'))
        self.item('unit-masked', 'a masked unit (a link to /dev/null in /etc/systemd/system)',
                  ['etc/systemd/system/apt-daily.service'])
        wants = 'etc/systemd/system/multi-user.target.wants/mu300-telnetd.service'
        if os.path.lexists(self.p(wants)):
            os.remove(self.p(wants))
            self.item('unit-disabled', 'an image service the user disabled (mu300-telnetd)', [wants], judge='absent')
        self.item('apt-source', 'an apt source and its key (/etc/apt/sources.list.d, /etc/apt/keyrings)',
                  [self.write('etc/apt/sources.list.d/user.list',
                              'deb [signed-by=/etc/apt/keyrings/user.gpg] http://example.invalid/ stable main\n'),
                   self.write('etc/apt/keyrings/user.gpg', b'\x99\x01')])
        self.mkdir('var/spool/cron/crontabs', 0o1730, 0, 102)
        self.item('crontab', "a user's crontab (/var/spool/cron/crontabs/ubuntu, 0600)",
                  [self.write('var/spool/cron/crontabs/ubuntu', '* * * * * true\n', 0o600, U, 102)])
        self.item('cron.d', '/etc/cron.d/backup', [self.write('etc/cron.d/backup', '0 3 * * * root true\n')])
        d = 'var/lib/docker'
        self.mkdir(d + '/overlay2/abc/diff/etc', 0o755)
        self.mkdir(d, 0o710)
        os.setxattr(self.p(d + '/overlay2/abc/diff/etc'), 'trusted.overlay.opaque', b'y')
        self.item('docker', "a service's data directory (/var/lib/docker: a layer, a volume, a database file)",
                  [d, self.write(d + '/overlay2/abc/diff/etc/passwd', 'root:x:0:0\n'), d + '/overlay2/abc/diff/etc',
                   self.write(d + '/volumes/db/_data/ibdata1', os.urandom(1 << 20), 0o640, 999, 999),
                   self.write(d + '/network/files/local-kv.db', b'\x00' * 65536, 0o600)])
        self.item('www', '/var/www/html/index.html', [self.write('var/www/html/index.html', '<h1>mine</h1>\n', 0o644, 33, 33)])
        self.item('opt', "software in /opt (/opt/myapp, a program and its data)",
                  [self.write('opt/myapp/bin/run', '#!/bin/sh\n', 0o755), self.write('opt/myapp/data/state.db', os.urandom(4096))])
        self.item('sshd_config', '/etc/ssh/sshd_config with a line changed', [self.modify('etc/ssh/sshd_config', b'\nPermitRootLogin no\n')])
        # (--live: a setting that changes nothing, and the device's own host keys written down as they are)
        self.item('sshd_config.d', '/etc/ssh/sshd_config.d/10-user.conf',
                  [self.write('etc/ssh/sshd_config.d/10-user.conf', 'LoginGraceTime 2m\n' if self.live else 'PasswordAuthentication no\n')])
        keys = ['etc/ssh/ssh_host_ed25519_key', 'etc/ssh/ssh_host_ed25519_key.pub']
        if not (self.live and all(os.path.isfile(self.p(k)) for k in keys)):
            keys = [self.write(keys[0], 'PRIVATE\n', 0o600), self.write(keys[1], 'ssh-ed25519 AAAA\n')]
        self.item('hostkeys', 'SSH host keys', keys)
        self.mkdir('home/ubuntu/.ssh', 0o700, U, U)
        self.item('authorized_keys', '~/.ssh/authorized_keys (0600, in a 0700 directory)',
                  [self.write('home/ubuntu/.ssh/authorized_keys', 'ssh-ed25519 AAAA me\n', 0o600, U, U), 'home/ubuntu/.ssh'])
        self.item('history', 'shell history (~/.bash_history of ubuntu and of root)',
                  [self.write('home/ubuntu/.bash_history', 'ls\n', 0o600, U, U), self.write('root/.bash_history', 'ls\n', 0o600)])
        self.item('bashrc', '~/.bashrc with a line added (an image file, modified)', [self.modify('home/ubuntu/.bashrc')])
        self.item('sysctl.d', '/etc/sysctl.d/99-user.conf', [self.write('etc/sysctl.d/99-user.conf', 'net.ipv4.ip_forward=1\n')])
        f = self.have('etc/sysctl.conf', 'etc/login.defs')
        self.item('etc-modified', f'/{f} with a line added (an image file, modified)', [self.modify(f)])
        f = self.have('etc/update-motd.d/10-help-text', 'etc/legal')
        os.remove(self.p(f))
        self.item('etc-deleted', f'/{f} deleted (an image file the user removed)', [f], judge='absent')
        f = 'etc/issue.net'
        os.remove(self.p(f))
        self.mkdir(f)
        self.write(f + '/note', 'a directory where the image has a file\n')
        self.item('type-file-dir', '/etc/issue.net made a directory (the image has a file)', [f, f + '/note'])
        f = 'etc/resolv.conf'
        os.remove(self.p(f))
        self.write(f, 'nameserver 9.9.9.9\n')
        self.item('type-link-file', '/etc/resolv.conf made a file (the image has a symbolic link)', [f])
        f = self.have('etc/skel', 'etc/update-motd.d')
        shutil.rmtree(self.p(f))
        os.symlink('/srv/motd', self.p(f))
        self.item('type-dir-link', f'/{f} made a symbolic link (the image has a directory)', [f])
        self.item('log-user', '/var/log/myapp/app.log', [self.write('var/log/myapp/app.log', 'line\n' * 100, 0o640, U, 4)])
        self.item('log-image', '/var/log/wtmp and dpkg.log with entries (image files that grew)',
                  [self.modify('var/log/wtmp', b'\x07' * 384), self.modify('var/log/dpkg.log', b'2026-10-01 status installed x\n')])
        self.mkdir('var/log/journal/0123456789abcdef0123456789abcdef', 0o2755, 0, 999)
        self.item('journal', 'the journal (/var/log/journal/<machine-id>/system.journal)',
                  [self.write('var/log/journal/0123456789abcdef0123456789abcdef/system.journal', os.urandom(8192), 0o640, 0, 999)])
        self.item('machine-id', '/etc/machine-id (written at the first boot; the image has an empty file)',
                  [self.write('etc/machine-id', '0123456789abcdef0123456789abcdef\n', 0o444)])
        self.item('usr-local', '/usr/local/bin/mytool and /usr/local/lib',
                  [self.write('usr/local/bin/mytool', '#!/bin/sh\n', 0o755), self.write('usr/local/lib/mylib/data', 'x')])
        f = 'usr/lib/systemd/system/ssh.service'
        self.item('usr-modified', f'/{f} edited in place (an image file outside /etc: the release\'s must win, the edit '
                                  'must not be thrown away)', [self.modify(f, b'\n# edited\n')], judge='project')
        self.item('usr-created', 'a file added under /usr (/usr/share/myfonts/x.ttf)', [self.write('usr/share/myfonts/x.ttf', os.urandom(2048))])
        self.item('fstab', '/etc/fstab with a line added', [self.modify('etc/fstab', b'LABEL=data /mnt/data ext4 noauto 0 0\n')])
        self.item('hostname', '/etc/hostname', [self.write('etc/hostname', 'myrouter\n')])
        self.item('bluetooth', '/var/lib/bluetooth (paired devices)',
                  [self.write('var/lib/bluetooth/AA:BB:CC:DD:EE:FF/11:22:33:44:55:66/info', '[LinkKey]\nKey=x\n', 0o600)])
        self.item('sudoers.d', '/etc/sudoers.d/90-kaan (0440)', [self.write('etc/sudoers.d/90-kaan', 'kaan ALL=(ALL) NOPASSWD: ALL\n', 0o440)])
        self.item('environment', '/etc/environment modified, /etc/profile.d/my.sh, /etc/rc.local',
                  [self.modify('etc/environment', b'EDITOR=vi\n'), self.write('etc/profile.d/my.sh', 'export X=1\n'),
                   self.write('etc/rc.local', '#!/bin/sh\nexit 0\n', 0o755)])
        self.item('networkd', '/etc/systemd/network/50-user.network and /etc/systemd/resolved.conf.d/dns.conf',
                  [self.write('etc/systemd/network/50-user.network', '[Match]\nName=eth9\n'),
                   self.write('etc/systemd/resolved.conf.d/dns.conf', '[Resolve]\nDNS=9.9.9.9\n')])
        self.item('ssl', 'a private key (/etc/ssl/private/my.key, 0640) and a CA of the user (/usr/local/share/ca-certificates)',
                  [self.write('etc/ssl/private/my.key', 'KEY\n', 0o640, 0, 0), self.write('usr/local/share/ca-certificates/my.crt', 'CERT\n')])
        self.item('docker-conf', '/etc/docker/daemon.json', [self.write('etc/docker/daemon.json', '{}\n')])
        self.item('root-home', "root's files (/root/.config/app/settings, /root/notes.txt)",
                  [self.write('root/.config/app/settings', 'a=1\n'), self.write('root/notes.txt', 'notes\n')])
        self.item('srv', '/srv/files/document.pdf', [self.write('srv/files/document.pdf', os.urandom(100000))])
        self.item('var-lib-own', "data of the user's own service (/var/lib/myapp/db.sqlite, owned by a system account "
                                 'the user added)',
                  [self.write('var/lib/myapp/db.sqlite', os.urandom(8192), 0o600, 777, 777)])
        # accounts: a user added on the device, a system account of the user's service, a password changed
        with open(self.p('etc/passwd'), 'a') as f:
            f.write('kaan:x:1001:1001:Kaan:/home/kaan:/bin/bash\nmyapp:x:777:777::/var/lib/myapp:/usr/sbin/nologin\n')
        with open(self.p('etc/group'), 'a') as f:
            f.write('kaan:x:1001:\nmyapp:x:777:\n')
        with open(self.p('etc/shadow'), 'a') as f:
            f.write('kaan:$6$salt$kaanhash:20000:0:99999:7:::\nmyapp:!:20000::::::\n')
        if not self.live:
            lines = open(self.p('etc/shadow')).read().splitlines()
            open(self.p('etc/shadow'), 'w').write(
                '\n'.join('ubuntu:$6$mine$changedhash:20000:0:99999:7:::' if l.startswith('ubuntu:') else l for l in lines) + '\n')
            self.item('account-password', "ubuntu's password changed", [], accounts={'shadow': ['ubuntu:$6$mine$changedhash:']})
        self.mkdir('home/kaan', 0o750, 1001, 1001)
        self.item('account-user', 'a user added on the device (kaan, uid 1001) with a password and a home directory',
                  ['home/kaan', self.write('home/kaan/file', 'x', 0o600, 1001, 1001)],
                  accounts={'passwd': ['kaan:x:1001:1001:'], 'shadow': ['kaan:$6$salt$kaanhash:'], 'group': ['kaan:x:1001:']})
        self.item('account-system', 'a system account added on the device (myapp, uid 777: its data is owned by it)',
                  [], accounts={'passwd': ['myapp:x:777:777:'], 'group': ['myapp:x:777:']})
        self.write('var/cache/apt/archives/x.deb', b'x' * 1000)
        self.item('cache', '/var/cache (downloaded packages)', ['var/cache/apt/archives/x.deb'], judge='design')
        if self.live:
            return
        # a package installed on the device: dpkg's records, its program, a changed conffile, its data
        with open(self.p('var/lib/dpkg/status'), 'a') as f:
            f.write('\nPackage: userpkg\nStatus: install ok installed\nPriority: optional\nSection: utils\n'
                    'Installed-Size: 10\nMaintainer: x <x@example.invalid>\nArchitecture: arm64\nVersion: 1.0\n'
                    'Conffiles:\n /etc/userpkg/userpkg.conf d41d8cd98f00b204e9800998ecf8427e\nDescription: a package '
                    'the user installed\n')
        self.write('var/lib/dpkg/info/userpkg.list', '/.\n/etc\n/etc/userpkg\n/etc/userpkg/userpkg.conf\n/usr\n/usr/bin\n'
                                                     '/usr/bin/userpkg\n/usr/share/userpkg\n/usr/share/userpkg/data\n')
        self.write('var/lib/dpkg/info/userpkg.conffiles', '/etc/userpkg/userpkg.conf\n')
        self.write('var/lib/dpkg/info/userpkg.md5sums', 'd41d8cd98f00b204e9800998ecf8427e  usr/bin/userpkg\n')
        self.item('pkg-files', "a package installed on the device: its program and files (/usr/bin/userpkg, "
                               '/usr/share/userpkg): installed again at the first boot',
                  [self.write('usr/bin/userpkg', b'', 0o755), self.write('usr/share/userpkg/data', 'pkg\n')],
                  judge='package', package='userpkg')
        self.item('pkg-conf', "that package's configuration, changed by the user (/etc/userpkg/userpkg.conf)",
                  [self.write('etc/userpkg/userpkg.conf', 'option=changed\n')])
        self.item('pkg-data', "that package's data (/var/lib/userpkg/state.db)",
                  [self.write('var/lib/userpkg/state.db', os.urandom(4096), 0o600)])

    # ---- OpenWrt
    def openwrt(self):
        self.item('uci-modified', '/etc/config/network changed', [self.modify('etc/config/network', b"\nconfig interface 'user'\n\toption proto 'none'\n")])
        self.item('uci-created', '/etc/config/custom (a UCI file of the user)', [self.write('etc/config/custom', "config x 'y'\n")])
        self.item('www-created', '/www/mysite/index.html', [self.write('www/mysite/index.html', '<h1>mine</h1>\n')])
        self.item('www-modified', "/www/index.html edited (an image file outside /etc: the release's must win, the "
                                  'edit must not be thrown away)', [self.modify('www/index.html', b'<!-- mine -->\n')], judge='project')
        self.mkdir('etc/crontabs')
        self.item('crontab', '/etc/crontabs/root', [self.mine('etc/crontabs/root', '0 4 * * * reboot\n', 0o600, comment=b'# the user\'s line\n')])
        self.item('hotplug', '/etc/hotplug.d/iface/99-user', [self.write('etc/hotplug.d/iface/99-user', '#!/bin/sh\n', 0o755)])
        self.item('sysctl.conf', '/etc/sysctl.conf with a line added (an image file, modified)', [self.modify('etc/sysctl.conf', b'net.ipv4.ip_forward=1\n')])
        self.item('sysctl.d', '/etc/sysctl.d/99-user.conf', [self.write('etc/sysctl.d/99-user.conf', 'vm.swappiness=10\n')])
        self.item('usr-share', '/usr/share/myapp/data (added under /usr/share)', [self.write('usr/share/myapp/data', os.urandom(2048))])
        init = self.write('etc/init.d/myservice', '#!/bin/sh /etc/rc.common\nSTART=99\n', 0o755)
        os.symlink('../init.d/myservice', self.p('etc/rc.d/S99myservice'))
        self.item('init.d', "an init script of the user's own, enabled (/etc/init.d/myservice, /etc/rc.d/S99myservice)",
                  [init, 'etc/rc.d/S99myservice'])
        hk = 'etc/dropbear/dropbear_ed25519_host_key'
        if not (self.live and os.path.isfile(self.p(hk))):
            hk = self.write(hk, os.urandom(83), 0o600)
        self.item('dropbear', 'SSH host key and authorized_keys (/etc/dropbear)',
                  [hk, self.mine('etc/dropbear/authorized_keys', 'ssh-ed25519 AAAA me\n', 0o600, comment=b'# the user\'s line\n')])
        self.item('root-home', "root's files (/root/.ash_history, /root/notes.txt, /root/.ssh/id_ed25519)",
                  [self.write('root/.ash_history', 'ls\n', 0o600), self.write('root/notes.txt', 'notes\n'),
                   self.write('root/.ssh/id_ed25519', 'KEY\n', 0o600)])
        self.item('rc.local', '/etc/rc.local changed', [self.modify('etc/rc.local', b'\n# mine\n')])
        self.item('firewall.user', '/etc/firewall.user and /etc/nftables.d/10-user.nft',
                  [self.write('etc/firewall.user', '# rules\n'), self.write('etc/nftables.d/10-user.nft', '# nft\n')])
        self.item('dnsmasq', '/etc/dnsmasq.conf with a line added, /etc/ethers',
                  [self.modify('etc/dnsmasq.conf', b'address=/my.lan/192.168.77.9\n'), self.write('etc/ethers', 'aa:bb:cc:dd:ee:ff host\n')])
        self.item('openvpn', '/etc/openvpn/client.ovpn (0600)', [self.write('etc/openvpn/client.ovpn', 'client\n', 0o600)])
        self.item('opt', 'software in /opt (/opt/user/tool)', [self.write('opt/user/tool', '#!/bin/sh\n', 0o755)])
        self.item('uci-defaults', '/etc/uci-defaults/99-user, /etc/profile.d/x.sh',
                  [self.write('etc/uci-defaults/99-user', 'exit 0\n'), self.write('etc/profile.d/x.sh', 'export X=1\n')])
        self.item('sysupgrade.conf', '/etc/sysupgrade.conf with a line added', [self.modify('etc/sysupgrade.conf', b'/etc/myapp/\n')])
        # (--live: the locks the device has are written down as they are; a lock of the test's own would be applied
        # at the next boot)
        locks = 'etc/unisoc-modem/lock-state.d'
        saved = sorted(os.listdir(self.p(locks))) if self.live and os.path.isdir(self.p(locks)) else []
        if saved:
            self.item('unisoc', "the panel's saved locks (/etc/unisoc-modem/lock-state.d)", [locks + '/' + n for n in saved])
        elif not self.live:
            self.item('unisoc', "the panel's saved locks (/etc/unisoc-modem/lock-state.d)", [self.write(locks + '/lte_band', '3,7\n')])
        f = 'etc/banner'
        os.remove(self.p(f))
        self.item('etc-deleted', '/etc/banner deleted (an image file the user removed)', [f], judge='absent')
        f = self.have('etc/banner.failsafe', 'etc/shells')
        os.remove(self.p(f))
        self.mkdir(f)
        self.write(f + '/note', 'a directory where the image has a file\n')
        self.item('type-file-dir', f'/{f} made a directory (the image has a file)', [f, f + '/note'])
        os.remove(self.p('etc/TZ'))
        self.write('etc/TZ', '<+03>-3\n')
        self.item('type-link-file', '/etc/TZ made a file (the image has a symbolic link)', ['etc/TZ'])
        self.item('lua', '/usr/lib/lua/user.lua (added in a directory of packages)', [self.write('usr/lib/lua/user.lua', 'return 1\n')])
        self.item('apk-feeds', '/etc/apk/repositories.d/customfeeds.list changed, a key added to /etc/apk/keys',
                  [self.write('etc/apk/repositories.d/customfeeds.list', 'https://example.invalid/packages.adb\n'),
                   self.write('etc/apk/keys/user.pem', 'KEY\n')])
        self.item('modules', 'a kernel module built by hand (/lib/modules/6.99.0/hand.ko) and firmware (/lib/firmware/user.bin)',
                  [self.write('lib/modules/6.99.0/hand.ko', os.urandom(4096)), self.write('lib/firmware/user.bin', os.urandom(512))])
        self.item('cert', '/etc/ssl/certs/user.crt', [self.write('etc/ssl/certs/user.crt', 'CERT\n')])
        # (--live: no image service is disabled: cron runs the device's own jobs, sysctl its forwarding)
        link = None if self.live else self.have('etc/rc.d/S50cron', 'etc/rc.d/S11sysctl')
        if link:
            os.remove(self.p(link))
            self.item('service-disabled', f'an image service the user disabled (/{link} removed)', [link], judge='absent')
        # accounts
        with open(self.p('etc/passwd'), 'a') as f:
            f.write('kaan:x:1001:1001:Kaan:/home/kaan:/bin/ash\n')
        with open(self.p('etc/group'), 'a') as f:
            f.write('kaan:x:1001:\n')
        lines = open(self.p('etc/shadow')).read().splitlines()
        open(self.p('etc/shadow'), 'w').write(
            '\n'.join('root:$6$mine$changedhash:20000:0:99999:7:::' if l.startswith('root:') and not self.live else l for l in lines)
            + '\nkaan:$6$salt$kaanhash:20000:0:99999:7:::\n')
        self.mkdir('home/kaan', 0o750, 1001, 1001)
        self.item('account-user', 'a user added on the device (kaan, uid 1001) with a password and a home directory',
                  ['home/kaan', self.write('home/kaan/file', 'x', 0o600, 1001, 1001)],
                  accounts={'passwd': ['kaan:x:1001:1001:'], 'shadow': ['kaan:$6$salt$kaanhash:'], 'group': ['kaan:x:1001:']})
        if self.live:
            return
        self.item('account-password', "root's password changed", [], accounts={'shadow': ['root:$6$mine$changedhash:']})
        # a package installed on the device: apk's records, its program, its UCI file, another settings file, its data
        with open(self.p('etc/apk/world'), 'a') as f:
            f.write('userpkg\n')
        with open(self.p('lib/apk/db/installed'), 'a') as f:
            f.write('\nC:Q1AAAAAAAAAAAAAAAAAAAAAAAAAAA=\nP:userpkg\nV:1.0-r1\nA:aarch64_cortex-a76\nS:10\nI:10\nT:a package the '
                    'user installed\nF:etc/config\nR:userpkg\nF:etc/userpkg\nR:settings.yaml\nF:etc/init.d\nR:userpkg\n'
                    'a:0:0:755\nF:usr/bin\nR:userpkg\na:0:0:755\n')
        self.write('lib/apk/packages/userpkg.list', '/etc/config/userpkg\n/etc/userpkg/settings.yaml\n/etc/init.d/userpkg\n/usr/bin/userpkg\n')
        self.write('lib/apk/packages/userpkg.conffiles', '/etc/config/userpkg\n/etc/userpkg/settings.yaml\n')
        os.symlink('../init.d/userpkg', self.p('etc/rc.d/S95userpkg'))
        self.item('pkg-files', "a package installed on the device: its program and init script (/usr/bin/userpkg, "
                               '/etc/init.d/userpkg): installed again at the first boot',
                  [self.write('usr/bin/userpkg', '#!/bin/sh\n', 0o755), self.write('etc/init.d/userpkg', '#!/bin/sh /etc/rc.common\n', 0o755)],
                  judge='package', package='userpkg')
        self.item('pkg-uci', "that package's UCI settings (/etc/config/userpkg): set aside until the package is back, "
                             'so that they cannot act without it', [self.write('etc/config/userpkg', "config main\n\toption enabled '1'\n")],
                  judge='orphan')
        self.item('pkg-conf', "that package's other settings, changed by the user (/etc/userpkg/settings.yaml)",
                  [self.write('etc/userpkg/settings.yaml', 'changed: true\n')])
        self.item('pkg-data', "that package's data (/etc/userpkg/cache/rules.db, /usr/share/userpkg-data/geo.dat: made "
                              'at run time, in no package)',
                  [self.write('etc/userpkg/cache/rules.db', os.urandom(4096)), self.write('usr/share/userpkg-data/geo.dat', os.urandom(4096))])


def accounts(root):
    """{'u': {uid: name}, 'g': {gid: name}} of the system at ROOT"""
    out = {}
    for key, name in (('u', 'passwd'), ('g', 'group')):
        out[key] = {}
        try:
            for line in open(os.path.join(root, 'etc', name), errors='replace'):
                f = line.split(':')
                if len(f) > 2 and f[2].isdigit():
                    out[key].setdefault(f[2], f[0])
        except OSError:
            pass
    return out


def plant(root, kind, out, mounts=True, live=False):
    pl = Plant(root, kind, mounts, live)
    pl.common('srv' if kind == 'ubuntu' else 'root')
    getattr(pl, kind)()
    for it in pl.items:
        it['before'] = {p: lstat_info(pl.p(p)) for p in it['paths']}
    with open(out, 'w') as f:
        json.dump({'kind': kind, 'items': pl.items, 'accounts': accounts(root)}, f)
    print(f'planted {len(pl.items)} items ({sum(len(i["paths"]) for i in pl.items)} paths) in {root}')


def differs(a, b, item):
    """what of the attributes written down before (A) is not the same now (B): (gone, changed) - GONE when what the
    user had there is not there any more (another type, other content), CHANGED for an attribute of it"""
    if a['type'] != b['type']:
        return ['another kind of file is there'], []
    gone, out = [], []
    if a.get('sha256') != b.get('sha256'):
        gone.append("the image's content is there")
    if a.get('target') != b.get('target'):
        gone.append('the link points elsewhere')
    if a.get('rdev') != b.get('rdev'):
        out.append('device number')
    if a['mode'] != b['mode']:
        out.append(f'mode {a["mode"]:04o} -> {b["mode"]:04o}')
    if (a['uid'], a['gid']) != (b['uid'], b['gid']):
        # the same account under the number the new system gives it (Ubuntu 24.04 and 26.04 differ) is the same owner
        was = (OLD_NAMES.get('u', {}).get(str(a['uid'])), OLD_NAMES.get('g', {}).get(str(a['gid'])))
        now = (NEW_NAMES.get('u', {}).get(str(b['uid'])), NEW_NAMES.get('g', {}).get(str(b['gid'])))
        same = all(w == n if w else x == y for w, n, x, y in zip(was, now, (a['uid'], a['gid']), (b['uid'], b['gid'])))
        if not same:
            out.append(f'owner {a["uid"]}:{a["gid"]} -> {b["uid"]}:{b["gid"]}')
    if a['mtime'] != b['mtime']:
        out.append('time: the fraction of the second' if a['mtime'] // 10 ** 9 == b['mtime'] // 10 ** 9 else 'time')
    if a['xattrs'] != b['xattrs']:
        missing = sorted(set(a['xattrs']) - set(b['xattrs']))
        out.append('extended attributes' + (f': {", ".join(missing)} gone' if missing else ''))
    if item.get('sparse') and b.get('blocks', 0) > 4 * a.get('blocks', 0) + 64:
        out.append(f'no longer sparse: {b["blocks"] * 512 >> 20} MiB on disk')
    return gone, out


def find_held(root, rel):
    """REL as the update set it aside: /etc/mu300/update-kept/<tag>/<what>/REL"""
    base = os.path.join(root, KEPT_DIR)
    if not os.path.isdir(base):
        return None
    for tag in sorted(os.listdir(base)):
        d = os.path.join(base, tag)
        for sub in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            p = os.path.join(d, sub, rel)
            if os.path.lexists(p):
                return os.path.relpath(p, root)
    return None


def judge_path(root, it, rel):
    """(status, notes) of one planted path"""
    kind = it['judge']
    before, now = it['before'][rel], lstat_info(os.path.join(root, rel))
    if kind == 'absent':
        # back again: nothing of the user's is gone, but it is not as they left it
        return ('kept', []) if now is None else ('changed', [f'/{rel} is back'])
    if kind == 'design':
        return ('by design' if now is None else 'kept'), []
    if kind == 'package':
        wanted = os.path.join(root, 'etc/mu300/user-packages/wanted')
        listed = os.path.exists(wanted) and it['package'] in open(wanted).read().split()
        return ('kept' if now else 'reinstall' if listed else 'lost'), []
    held = find_held(root, rel)
    if kind == 'orphan' and now is None and os.path.lexists(os.path.join(root, 'etc/mu300/orphaned-config', rel)):
        held = 'etc/mu300/orphaned-config/' + rel
    if now is not None:
        gone, changed = differs(before, now, it)
        if kind == 'mountpoint':
            # written down while the other filesystem was mounted: those were its attributes, not the directory's
            changed = []
        if it.get('group'):
            first = lstat_info(os.path.join(root, it['paths'][0]))
            if first and now['ino'] != first['ino']:
                changed.append('a copy of its own, no longer one file with the others')
        if not gone:
            return ('changed' if changed else 'kept'), changed
    # not in its place as the user had it: is the user's version set aside?
    if held:
        h = lstat_info(os.path.join(root, held))
        hgone, _ = differs(before, h, it)
        if not hgone:
            return 'held', ['set aside in /' + os.path.dirname(held)[:60]]
    if now is None:
        return 'lost', [f'/{rel} is gone']
    return 'lost', [f'/{rel}: ' + ', '.join(gone)]


def judge(root, it, report):
    """(status, detail) of one item in the updated system: the worst of its paths"""
    statuses, notes = [], []
    for field, wants in (it.get('accounts') or {}).items():
        try:
            text = open(os.path.join(root, 'etc', field)).read()
        except OSError:
            text = ''
        for w in wants:
            if not any(line.startswith(w) for line in text.splitlines()):
                statuses.append('lost')
                notes.append(f'/etc/{field} has no entry {w.split(":")[0]} as it was')
    silent = []
    for rel in it['paths']:
        s, n = judge_path(root, it, rel)
        statuses.append(s)
        notes += n
        # what the update said about it: a change the report does not name is a silent one
        if s in ('changed', 'held') and report is not None and ('/' + rel) not in report:
            silent.append(rel)
    status = next((s for s in ('lost', 'changed', 'held', 'reinstall', 'by design') if s in statuses), 'kept')
    uniq = list(dict.fromkeys(notes))
    detail = '; '.join(uniq[:3]) + (f' (and {len(uniq) - 3} more)' if len(uniq) > 3 else '')
    if report is not None and status in ('changed', 'held'):
        detail += '; in the report' if not silent else f'; NOT in the report: /{silent[0]}'
    return status, detail
OLD_NAMES, NEW_NAMES = {}, {}


def verify(root, spec):
    data = json.load(open(spec))
    OLD_NAMES.update(data.get('accounts', {}))
    NEW_NAMES.update(accounts(root))
    report = None
    rp = os.path.join(root, 'etc/mu300/update-report')
    if os.path.exists(rp):
        report = open(rp, errors='replace').read()
    for it in data['items']:
        status, detail = judge(root, it, report)
        print('\t'.join((it['id'], status, it['what'], detail.replace('\n', '\\n').replace('\t', '\\t'))))


def brief(status, detail):
    """a cell of the short table: the status, and in a few words why"""
    if status in ('kept', 'reinstall', 'by design', '-'):
        return {'reinstall': 'installed again at the first boot', 'by design': 'not carried, by design'}.get(status, status)
    why = []
    for key, word in (('is gone', 'gone'), ("the image's content is there", "the image's file instead"),
                      ('another kind of file is there', "the image's kind of file instead"), ('the link points elsewhere', "the image's link instead"),
                      ('is back', 'back'), ('has no entry', 'the account is gone'), ('fraction of the second', 'time to the second only'),
                      ('extended attributes', 'extended attributes gone'), ('no longer sparse', 'no longer sparse'),
                      ('a copy of its own', 'hard link broken'), ('owner ', 'owner'), ('mode ', 'mode'),
                      ('orphaned-config', 'in /etc/mu300/orphaned-config'), ('update-kept', 'in /etc/mu300/update-kept')):
        if key in detail and word not in why:
            why.append(word)
    if 'time' in detail and 'time to the second only' not in why and '; time' in '; ' + detail.replace(': time', '; time'):
        why.append('time')
    if status in ('changed', 'held') and 'in the report' in detail:
        why.append('NOT in the report' if 'NOT in the report' in detail else 'in the report')
    return ('set aside' if status == 'held' else status) + (': ' + ', '.join(why) if why else '')


def table(args):
    short = '--short' in args
    args = [a for a in args if a != '--short']
    cols, rows, order = [], {}, []
    for a in args:
        title, path = a.split('=', 1)
        cols.append(title)
        for line in open(path).read().splitlines():
            id_, status, what, detail = (line.split('\t') + ['', '', ''])[:4]
            if id_ not in rows:
                rows[id_] = {'what': what}
                order.append(id_)
            rows[id_][title] = (status, detail)
    print('| item | ' + ' | '.join(cols) + ' |')
    print('|---|' + '---|' * len(cols))
    for id_ in order:
        r = rows[id_]
        cells = []
        for c in cols:
            status, detail = r.get(c, ('-', ''))
            cells.append(brief(status, detail) if short else status + (f': {detail}' if detail else ''))
        print(f'| {r["what"]} | ' + ' | '.join(x.replace('|', '\\|') for x in cells) + ' |')
    print()
    for c in cols:
        n = {}
        for id_ in order:
            s = rows[id_].get(c, ('not planted', ''))[0]
            n[s] = n.get(s, 0) + 1
        print(f'{c}: ' + ', '.join(f'{v} {k}' for k, v in sorted(n.items())))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'plant':
        plant(sys.argv[2], sys.argv[3], sys.argv[4], '--no-mount' not in sys.argv[5:], '--live' in sys.argv[5:])
    elif cmd == 'verify':
        verify(sys.argv[2], sys.argv[3])
    elif cmd == 'table':
        table(sys.argv[2:])
    else:
        sys.exit(__doc__)
