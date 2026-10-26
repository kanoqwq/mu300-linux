| item | mu300-update apply | installer, Magisk zip |
|---|---|---|
| files with odd names (space, newline, leading dash, UTF-8, backslash, glob characters, 250 characters, quotes, $(...), tab) | kept | kept |
| modes and owners: 0000, setuid 4755, a sticky 1777 directory, a 0700 directory of uid 1000, a file of an id no account has (4242:4343) | kept | kept |
| extended attributes: user.*, a file capability (cap_net_bind_service), a POSIX ACL on a directory, trusted.overlay.opaque on a directory (a container layer) | kept | kept |
| three names of one file (hard links), two of them in one directory | kept | kept |
| a sparse file (256 MiB long, one block of data) | kept | kept |
| symbolic links: relative, absolute, dangling, to a directory; one with its own date | kept | kept |
| a FIFO and a character device node | kept | kept |
| a socket file (a program makes it again when it starts) | by design | by design |
| modification times: a file (with nanoseconds) and its directory, from 2001 | kept | kept |
| 2000 small files in 20 directories | kept | kept |
| read-only files (a git object, mode 0444) | kept | kept |
| a swap file (/swapfile, 8 MiB, mode 0600) | kept | kept |
| /tmp and /var/tmp | by design | by design |
| the mount point of another filesystem (/mnt/usb): the directory | kept | kept |
| what is on that other filesystem (it stays there) | by design | - |
| /etc/mu300: hotspot and VPN settings | kept | kept |
| /etc/hosts with a line added (an image file, modified) | kept | kept |
| one of the project's own programs, edited (/opt/mu300/bin/mu300-wifi): the new release's must win, the edited one must not be thrown away | kept | kept |
| /etc/config/network changed | kept | kept |
| /etc/config/custom (a UCI file of the user) | kept | kept |
| /www/mysite/index.html | kept | kept |
| /www/index.html edited (an image file outside /etc: the release's must win, the edit must not be thrown away) | held: set aside in /etc/mu300/update-kept/v2026.10.23/user/www; in the report | held: set aside in /etc/mu300/update-kept/v2026.10.23/user/www; in the report |
| /etc/crontabs/root | kept | kept |
| /etc/hotplug.d/iface/99-user | kept | kept |
| /etc/sysctl.conf with a line added (an image file, modified) | kept | kept |
| /etc/sysctl.d/99-user.conf | kept | kept |
| /usr/share/myapp/data (added under /usr/share) | kept | kept |
| an init script of the user's own, enabled (/etc/init.d/myservice, /etc/rc.d/S99myservice) | kept | kept |
| SSH host key and authorized_keys (/etc/dropbear) | kept | kept |
| root's files (/root/.ash_history, /root/notes.txt, /root/.ssh/id_ed25519) | kept | kept |
| /etc/rc.local changed | kept | kept |
| /etc/firewall.user and /etc/nftables.d/10-user.nft | kept | kept |
| /etc/dnsmasq.conf with a line added, /etc/ethers | kept | kept |
| /etc/openvpn/client.ovpn (0600) | kept | kept |
| software in /opt (/opt/user/tool) | kept | kept |
| /etc/uci-defaults/99-user, /etc/profile.d/x.sh | kept | kept |
| /etc/sysupgrade.conf with a line added | kept | kept |
| the panel's saved locks (/etc/unisoc-modem/lock-state.d) | kept | kept |
| /etc/banner deleted (an image file the user removed) | kept | kept |
| /etc/banner.failsafe made a directory (the image has a file) | kept | kept |
| /etc/TZ made a file (the image has a symbolic link) | kept | kept |
| /usr/lib/lua/user.lua (added in a directory of packages) | kept | kept |
| /etc/apk/repositories.d/customfeeds.list changed, a key added to /etc/apk/keys | kept | kept |
| a kernel module built by hand (/lib/modules/6.99.0/hand.ko) and firmware (/lib/firmware/user.bin) | kept | kept |
| /etc/ssl/certs/user.crt | kept | kept |
| an image service the user disabled (/etc/rc.d/S50cron removed) | kept | kept |
| a user added on the device (kaan, uid 1001) with a password and a home directory | kept | kept |
| root's password changed | kept | kept |
| a package installed on the device: its program and init script (/usr/bin/userpkg, /etc/init.d/userpkg): installed again at the first boot | reinstall | reinstall |
| that package's UCI settings (/etc/config/userpkg): set aside until the package is back, so that they cannot act without it | held: set aside in /etc/mu300/orphaned-config/etc/config; in the report | held: set aside in /etc/mu300/orphaned-config/etc/config; in the report |
| that package's other settings, changed by the user (/etc/userpkg/settings.yaml) | kept | kept |
| that package's data (/etc/userpkg/cache/rules.db, /usr/share/userpkg-data/geo.dat: made at run time, in no package) | kept | kept |

mu300-update apply: 3 by design, 2 held, 46 kept, 1 reinstall
installer, Magisk zip: 2 by design, 2 held, 46 kept, 1 not planted, 1 reinstall
