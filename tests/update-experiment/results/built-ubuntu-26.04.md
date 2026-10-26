| item | mu300-update apply | installer, Magisk zip | apply --from the zip |
|---|---|---|---|
| files with odd names (space, newline, leading dash, UTF-8, backslash, glob characters, 250 characters, quotes, $(...), tab) | kept | kept | kept |
| modes and owners: 0000, setuid 4755, a sticky 1777 directory, a 0700 directory of uid 1000, a file of an id no account has (4242:4343) | kept | kept | kept |
| extended attributes: user.*, a file capability (cap_net_bind_service), a POSIX ACL on a directory, trusted.overlay.opaque on a directory (a container layer) | kept | kept | kept |
| three names of one file (hard links), two of them in one directory | kept | kept | kept |
| a sparse file (256 MiB long, one block of data) | kept | kept | kept |
| symbolic links: relative, absolute, dangling, to a directory; one with its own date | kept | kept | kept |
| a FIFO and a character device node | kept | kept | kept |
| a socket file (a program makes it again when it starts) | by design | by design | by design |
| modification times: a file (with nanoseconds) and its directory, from 2001 | kept | kept | kept |
| 2000 small files in 20 directories | kept | kept | kept |
| read-only files (a git object, mode 0444) | kept | kept | kept |
| a swap file (/swapfile, 8 MiB, mode 0600) | kept | kept | kept |
| /tmp and /var/tmp | by design | by design | by design |
| the mount point of another filesystem (/mnt/usb): the directory | kept | kept | kept |
| what is on that other filesystem (it stays there) | by design | - | by design |
| /etc/mu300: hotspot and VPN settings | kept | kept | kept |
| /etc/hosts with a line added (an image file, modified) | kept | kept | kept |
| one of the project's own programs, edited (/opt/mu300/bin/mu300-wifi): the new release's must win, the edited one must not be thrown away | kept | kept | kept |
| /etc/netplan/60-user.yaml | kept | kept | kept |
| /etc/wireguard/wg0.conf (0600, in a 0700 directory) | kept | kept | kept |
| a hand-written systemd unit, enabled | kept | kept | kept |
| a drop-in for an image unit (/etc/systemd/system/ssh.service.d/override.conf) | kept | kept | kept |
| a masked unit (a link to /dev/null in /etc/systemd/system) | kept | kept | kept |
| an image service the user disabled (mu300-telnetd) | kept | kept | kept |
| an apt source and its key (/etc/apt/sources.list.d, /etc/apt/keyrings) | kept | kept | kept |
| a user's crontab (/var/spool/cron/crontabs/ubuntu, 0600) | kept | kept | kept |
| /etc/cron.d/backup | kept | kept | kept |
| a service's data directory (/var/lib/docker: a layer, a volume, a database file) | kept | kept | kept |
| /var/www/html/index.html | kept | kept | kept |
| software in /opt (/opt/myapp, a program and its data) | kept | kept | kept |
| /etc/ssh/sshd_config with a line changed | kept | kept | kept |
| /etc/ssh/sshd_config.d/10-user.conf | kept | kept | kept |
| SSH host keys | kept | kept | kept |
| ~/.ssh/authorized_keys (0600, in a 0700 directory) | kept | kept | kept |
| shell history (~/.bash_history of ubuntu and of root) | kept | kept | kept |
| ~/.bashrc with a line added (an image file, modified) | kept | kept | kept |
| /etc/sysctl.d/99-user.conf | kept | kept | kept |
| /etc/login.defs with a line added (an image file, modified) | kept | kept | kept |
| /etc/update-motd.d/10-help-text deleted (an image file the user removed) | kept | kept | kept |
| /etc/issue.net made a directory (the image has a file) | kept | kept | kept |
| /etc/resolv.conf made a file (the image has a symbolic link) | kept | kept | kept |
| /etc/skel made a symbolic link (the image has a directory) | kept | kept | kept |
| /var/log/myapp/app.log | kept | kept | kept |
| /var/log/wtmp and dpkg.log with entries (image files that grew) | kept | kept | kept |
| the journal (/var/log/journal/<machine-id>/system.journal) | kept | kept | kept |
| /etc/machine-id (written at the first boot; the image has an empty file) | kept | kept | kept |
| /usr/local/bin/mytool and /usr/local/lib | kept | kept | kept |
| /usr/lib/systemd/system/ssh.service edited in place (an image file outside /etc: the release's must win, the edit must not be thrown away) | held: set aside in /etc/mu300/update-kept/v2026.10.27/user/usr/lib/systemd/syste; in the report | held: set aside in /etc/mu300/update-kept/v2026.10.27/user/usr/lib/systemd/syste; in the report | held: set aside in /etc/mu300/update-kept/v2026.10.27/user/usr/lib/systemd/syste; in the report |
| a file added under /usr (/usr/share/myfonts/x.ttf) | kept | kept | kept |
| /etc/fstab with a line added | kept | kept | kept |
| /etc/hostname | kept | kept | kept |
| /var/lib/bluetooth (paired devices) | kept | kept | kept |
| /etc/sudoers.d/90-kaan (0440) | kept | kept | kept |
| /etc/environment modified, /etc/profile.d/my.sh, /etc/rc.local | kept | kept | kept |
| /etc/systemd/network/50-user.network and /etc/systemd/resolved.conf.d/dns.conf | kept | kept | kept |
| a private key (/etc/ssl/private/my.key, 0640) and a CA of the user (/usr/local/share/ca-certificates) | kept | kept | kept |
| /etc/docker/daemon.json | kept | kept | kept |
| root's files (/root/.config/app/settings, /root/notes.txt) | kept | kept | kept |
| /srv/files/document.pdf | kept | kept | kept |
| data of the user's own service (/var/lib/myapp/db.sqlite, owned by a system account the user added) | kept | kept | kept |
| ubuntu's password changed | kept | kept | kept |
| a user added on the device (kaan, uid 1001) with a password and a home directory | kept | kept | kept |
| a system account added on the device (myapp, uid 777: its data is owned by it) | kept | kept | kept |
| /var/cache (downloaded packages) | by design | by design | by design |
| a package installed on the device: its program and files (/usr/bin/userpkg, /usr/share/userpkg): installed again at the first boot | reinstall | reinstall | reinstall |
| that package's configuration, changed by the user (/etc/userpkg/userpkg.conf) | kept | kept | kept |
| that package's data (/var/lib/userpkg/state.db) | kept | kept | kept |

mu300-update apply: 4 by design, 1 held, 61 kept, 1 reinstall
installer, Magisk zip: 3 by design, 1 held, 61 kept, 1 not planted, 1 reinstall
apply --from the zip: 4 by design, 1 held, 61 kept, 1 reinstall
