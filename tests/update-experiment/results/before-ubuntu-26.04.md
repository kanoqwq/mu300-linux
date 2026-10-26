| item | mu300-update apply | installer, Magisk zip |
|---|---|---|
| files with odd names (space, newline, leading dash, UTF-8, backslash, glob characters, 250 characters, quotes, $(...), tab) | kept | kept |
| modes and owners: 0000, setuid 4755, a sticky 1777 directory, a 0700 directory of uid 1000, a file of an id no account has (4242:4343) | kept | kept |
| extended attributes: user.*, a file capability (cap_net_bind_service), a POSIX ACL on a directory, trusted.overlay.opaque on a directory (a container layer) | kept | changed: extended attributes: user.mu300.comment gone; extended attributes: security.capability gone; extended attributes: system.posix_acl_access gone (and 1 more) |
| three names of one file (hard links), two of them in one directory | kept | changed: a copy of its own, no longer one file with the others |
| a sparse file (256 MiB long, one block of data) | kept | changed: no longer sparse: 256 MiB on disk |
| symbolic links: relative, absolute, dangling, to a directory; one with its own date | kept | kept |
| a FIFO and a character device node | kept | kept |
| a socket file (a program makes it again when it starts) | kept | kept |
| modification times: a file (with nanoseconds) and its directory, from 2001 | kept | kept |
| 2000 small files in 20 directories | kept | kept |
| read-only files (a git object, mode 0444) | kept | kept |
| a swap file (/swapfile, 8 MiB, mode 0600) | lost: /swapfile is gone | lost: /swapfile is gone |
| /tmp and /var/tmp | by design | by design |
| the mount point of another filesystem (/mnt/usb): the directory | lost: /mnt/usb is gone | lost: /mnt/usb is gone |
| what is on that other filesystem (it stays there) | by design | - |
| /etc/mu300: hotspot and VPN settings | kept | kept |
| /etc/hosts with a line added (an image file, modified) | lost: /etc/hosts: the image's content is there | lost: /etc/hosts: the image's content is there |
| one of the project's own programs, edited (/opt/mu300/bin/mu300-wifi): the new release's must win, the edited one must not be thrown away | lost: /opt/mu300/bin/mu300-wifi is gone | lost: /opt/mu300/bin/mu300-wifi is gone |
| /etc/netplan/60-user.yaml | lost: /etc/netplan/60-user.yaml is gone | lost: /etc/netplan/60-user.yaml is gone |
| /etc/wireguard/wg0.conf (0600, in a 0700 directory) | lost: /etc/wireguard/wg0.conf is gone | lost: /etc/wireguard/wg0.conf is gone |
| a hand-written systemd unit, enabled | lost: /etc/systemd/system/myapp.service is gone; /etc/systemd/system/multi-user.target.wants/myapp.service is gone | lost: /etc/systemd/system/myapp.service is gone; /etc/systemd/system/multi-user.target.wants/myapp.service is gone |
| a drop-in for an image unit (/etc/systemd/system/ssh.service.d/override.conf) | lost: /etc/systemd/system/ssh.service.d/override.conf is gone | lost: /etc/systemd/system/ssh.service.d/override.conf is gone |
| a masked unit (a link to /dev/null in /etc/systemd/system) | lost: /etc/systemd/system/apt-daily.service is gone | lost: /etc/systemd/system/apt-daily.service is gone |
| an image service the user disabled (mu300-telnetd) | changed: /etc/systemd/system/multi-user.target.wants/mu300-telnetd.service is back | changed: /etc/systemd/system/multi-user.target.wants/mu300-telnetd.service is back |
| an apt source and its key (/etc/apt/sources.list.d, /etc/apt/keyrings) | kept | kept |
| a user's crontab (/var/spool/cron/crontabs/ubuntu, 0600) | lost: /var/spool/cron/crontabs/ubuntu is gone | lost: /var/spool/cron/crontabs/ubuntu is gone |
| /etc/cron.d/backup | lost: /etc/cron.d/backup is gone | lost: /etc/cron.d/backup is gone |
| a service's data directory (/var/lib/docker: a layer, a volume, a database file) | lost: /var/lib/docker is gone; /var/lib/docker/overlay2/abc/diff/etc/passwd is gone; /var/lib/docker/overlay2/abc/diff/etc is gone (and 2 more) | lost: /var/lib/docker is gone; /var/lib/docker/overlay2/abc/diff/etc/passwd is gone; /var/lib/docker/overlay2/abc/diff/etc is gone (and 2 more) |
| /var/www/html/index.html | lost: /var/www/html/index.html is gone | lost: /var/www/html/index.html is gone |
| software in /opt (/opt/myapp, a program and its data) | lost: /opt/myapp/bin/run is gone; /opt/myapp/data/state.db is gone | lost: /opt/myapp/bin/run is gone; /opt/myapp/data/state.db is gone |
| /etc/ssh/sshd_config with a line changed | kept | kept |
| /etc/ssh/sshd_config.d/10-user.conf | kept | kept |
| SSH host keys | kept | kept |
| ~/.ssh/authorized_keys (0600, in a 0700 directory) | kept | kept |
| shell history (~/.bash_history of ubuntu and of root) | kept | kept |
| ~/.bashrc with a line added (an image file, modified) | kept | kept |
| /etc/sysctl.d/99-user.conf | lost: /etc/sysctl.d/99-user.conf is gone | lost: /etc/sysctl.d/99-user.conf is gone |
| /etc/login.defs with a line added (an image file, modified) | lost: /etc/login.defs: the image's content is there | lost: /etc/login.defs: the image's content is there |
| /etc/update-motd.d/10-help-text deleted (an image file the user removed) | changed: /etc/update-motd.d/10-help-text is back | changed: /etc/update-motd.d/10-help-text is back |
| /etc/issue.net made a directory (the image has a file) | lost: /etc/issue.net: another kind of file is there; /etc/issue.net/note is gone | lost: /etc/issue.net: another kind of file is there; /etc/issue.net/note is gone |
| /etc/resolv.conf made a file (the image has a symbolic link) | lost: /etc/resolv.conf: another kind of file is there | lost: /etc/resolv.conf: another kind of file is there |
| /etc/skel made a symbolic link (the image has a directory) | lost: /etc/skel: another kind of file is there | lost: /etc/skel: another kind of file is there |
| /var/log/myapp/app.log | lost: /var/log/myapp/app.log is gone | lost: /var/log/myapp/app.log is gone |
| /var/log/wtmp and dpkg.log with entries (image files that grew) | lost: /var/log/wtmp: the image's content is there; /var/log/dpkg.log: the image's content is there | lost: /var/log/wtmp: the image's content is there; /var/log/dpkg.log: the image's content is there |
| the journal (/var/log/journal/<machine-id>/system.journal) | lost: /var/log/journal/0123456789abcdef0123456789abcdef/system.journal is gone | lost: /var/log/journal/0123456789abcdef0123456789abcdef/system.journal is gone |
| /etc/machine-id (written at the first boot; the image has an empty file) | lost: /etc/machine-id: the image's content is there | lost: /etc/machine-id: the image's content is there |
| /usr/local/bin/mytool and /usr/local/lib | kept | kept |
| /usr/lib/systemd/system/ssh.service edited in place (an image file outside /etc: the release's must win, the edit must not be thrown away) | lost: /usr/lib/systemd/system/ssh.service: the image's content is there | lost: /usr/lib/systemd/system/ssh.service: the image's content is there |
| a file added under /usr (/usr/share/myfonts/x.ttf) | lost: /usr/share/myfonts/x.ttf is gone | lost: /usr/share/myfonts/x.ttf is gone |
| /etc/fstab with a line added | kept | kept |
| /etc/hostname | kept | kept |
| /var/lib/bluetooth (paired devices) | kept | kept |
| /etc/sudoers.d/90-kaan (0440) | lost: /etc/sudoers.d/90-kaan is gone | lost: /etc/sudoers.d/90-kaan is gone |
| /etc/environment modified, /etc/profile.d/my.sh, /etc/rc.local | lost: /etc/environment: the image's content is there; /etc/profile.d/my.sh is gone; /etc/rc.local is gone | lost: /etc/environment: the image's content is there; /etc/profile.d/my.sh is gone; /etc/rc.local is gone |
| /etc/systemd/network/50-user.network and /etc/systemd/resolved.conf.d/dns.conf | lost: /etc/systemd/network/50-user.network is gone; /etc/systemd/resolved.conf.d/dns.conf is gone | lost: /etc/systemd/network/50-user.network is gone; /etc/systemd/resolved.conf.d/dns.conf is gone |
| a private key (/etc/ssl/private/my.key, 0640) and a CA of the user (/usr/local/share/ca-certificates) | lost: /etc/ssl/private/my.key is gone | lost: /etc/ssl/private/my.key is gone |
| /etc/docker/daemon.json | lost: /etc/docker/daemon.json is gone | lost: /etc/docker/daemon.json is gone |
| root's files (/root/.config/app/settings, /root/notes.txt) | kept | kept |
| /srv/files/document.pdf | kept | kept |
| data of the user's own service (/var/lib/myapp/db.sqlite, owned by a system account the user added) | lost: /var/lib/myapp/db.sqlite is gone | lost: /var/lib/myapp/db.sqlite is gone |
| ubuntu's password changed | kept | kept |
| a user added on the device (kaan, uid 1001) with a password and a home directory | kept | kept |
| a system account added on the device (myapp, uid 777: its data is owned by it) | lost: /etc/passwd has no entry myapp as it was; /etc/group has no entry myapp as it was | lost: /etc/passwd has no entry myapp as it was; /etc/group has no entry myapp as it was |
| /var/cache (downloaded packages) | by design | by design |
| a package installed on the device: its program and files (/usr/bin/userpkg, /usr/share/userpkg): installed again at the first boot | reinstall | reinstall |
| that package's configuration, changed by the user (/etc/userpkg/userpkg.conf) | lost: /etc/userpkg/userpkg.conf is gone | lost: /etc/userpkg/userpkg.conf is gone |
| that package's data (/var/lib/userpkg/state.db) | lost: /var/lib/userpkg/state.db is gone | lost: /var/lib/userpkg/state.db is gone |

mu300-update apply: 3 by design, 2 changed, 27 kept, 34 lost, 1 reinstall
installer, Magisk zip: 2 by design, 5 changed, 24 kept, 34 lost, 1 not planted, 1 reinstall
