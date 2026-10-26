| item | mu300-update apply | installer, Magisk zip |
|---|---|---|
| files with odd names (space, newline, leading dash, UTF-8, backslash, glob characters, 250 characters, quotes, $(...), tab) | changed: time: the fraction of the second | kept |
| modes and owners: 0000, setuid 4755, a sticky 1777 directory, a 0700 directory of uid 1000, a file of an id no account has (4242:4343) | changed: time: the fraction of the second | kept |
| extended attributes: user.*, a file capability (cap_net_bind_service), a POSIX ACL on a directory, trusted.overlay.opaque on a directory (a container layer) | changed: time: the fraction of the second; extended attributes: user.mu300.comment gone; extended attributes: security.capability gone (and 2 more) | changed: extended attributes: user.mu300.comment gone; extended attributes: security.capability gone; extended attributes: system.posix_acl_access gone (and 1 more) |
| three names of one file (hard links), two of them in one directory | changed: time: the fraction of the second | changed: a copy of its own, no longer one file with the others |
| a sparse file (256 MiB long, one block of data) | changed: time: the fraction of the second; no longer sparse: 256 MiB on disk | changed: no longer sparse: 256 MiB on disk |
| symbolic links: relative, absolute, dangling, to a directory; one with its own date | changed: time | kept |
| a FIFO and a character device node | changed: time: the fraction of the second | kept |
| a socket file (a program makes it again when it starts) | kept | kept |
| modification times: a file (with nanoseconds) and its directory, from 2001 | changed: time: the fraction of the second | kept |
| 2000 small files in 20 directories | changed: time: the fraction of the second | kept |
| read-only files (a git object, mode 0444) | changed: time: the fraction of the second | kept |
| a swap file (/swapfile, 8 MiB, mode 0600) | lost: /swapfile is gone | lost: /swapfile is gone |
| /tmp and /var/tmp | by design | by design |
| the mount point of another filesystem (/mnt/usb): the directory | lost: /mnt/usb is gone | lost: /mnt/usb is gone |
| what is on that other filesystem (it stays there) | by design | - |
| /etc/mu300: hotspot and VPN settings | changed: time: the fraction of the second | kept |
| /etc/hosts with a line added (an image file, modified) | lost: /etc/hosts: the image's content is there | lost: /etc/hosts: the image's content is there |
| one of the project's own programs, edited (/opt/mu300/bin/mu300-wifi): the new release's must win, the edited one must not be thrown away | lost: /opt/mu300/bin/mu300-wifi is gone | lost: /opt/mu300/bin/mu300-wifi is gone |
| /etc/config/network changed | changed: time: the fraction of the second | kept |
| /etc/config/custom (a UCI file of the user) | changed: time: the fraction of the second | kept |
| /www/mysite/index.html | lost: /www/mysite/index.html is gone | lost: /www/mysite/index.html is gone |
| /www/index.html edited (an image file outside /etc: the release's must win, the edit must not be thrown away) | lost: /www/index.html: the image's content is there | lost: /www/index.html: the image's content is there |
| /etc/crontabs/root | lost: /etc/crontabs/root is gone | lost: /etc/crontabs/root is gone |
| /etc/hotplug.d/iface/99-user | lost: /etc/hotplug.d/iface/99-user is gone | lost: /etc/hotplug.d/iface/99-user is gone |
| /etc/sysctl.conf with a line added (an image file, modified) | lost: /etc/sysctl.conf: the image's content is there | lost: /etc/sysctl.conf: the image's content is there |
| /etc/sysctl.d/99-user.conf | lost: /etc/sysctl.d/99-user.conf is gone | lost: /etc/sysctl.d/99-user.conf is gone |
| /usr/share/myapp/data (added under /usr/share) | lost: /usr/share/myapp/data is gone | lost: /usr/share/myapp/data is gone |
| an init script of the user's own, enabled (/etc/init.d/myservice, /etc/rc.d/S99myservice) | lost: /etc/init.d/myservice is gone; /etc/rc.d/S99myservice is gone | lost: /etc/init.d/myservice is gone; /etc/rc.d/S99myservice is gone |
| SSH host key and authorized_keys (/etc/dropbear) | changed: time: the fraction of the second | kept |
| root's files (/root/.ash_history, /root/notes.txt, /root/.ssh/id_ed25519) | changed: time: the fraction of the second | kept |
| /etc/rc.local changed | changed: time: the fraction of the second | kept |
| /etc/firewall.user and /etc/nftables.d/10-user.nft | lost: /etc/firewall.user is gone; /etc/nftables.d/10-user.nft is gone | lost: /etc/firewall.user is gone; /etc/nftables.d/10-user.nft is gone |
| /etc/dnsmasq.conf with a line added, /etc/ethers | lost: /etc/dnsmasq.conf: the image's content is there; /etc/ethers: the image's content is there | lost: /etc/dnsmasq.conf: the image's content is there; /etc/ethers: the image's content is there |
| /etc/openvpn/client.ovpn (0600) | lost: /etc/openvpn/client.ovpn is gone | lost: /etc/openvpn/client.ovpn is gone |
| software in /opt (/opt/user/tool) | lost: /opt/user/tool is gone | lost: /opt/user/tool is gone |
| /etc/uci-defaults/99-user, /etc/profile.d/x.sh | lost: /etc/uci-defaults/99-user is gone; /etc/profile.d/x.sh is gone | lost: /etc/uci-defaults/99-user is gone; /etc/profile.d/x.sh is gone |
| /etc/sysupgrade.conf with a line added | lost: /etc/sysupgrade.conf: the image's content is there | lost: /etc/sysupgrade.conf: the image's content is there |
| the panel's saved locks (/etc/unisoc-modem/lock-state.d) | changed: time: the fraction of the second | kept |
| /etc/banner deleted (an image file the user removed) | changed: /etc/banner is back | changed: /etc/banner is back |
| /etc/banner.failsafe made a directory (the image has a file) | lost: /etc/banner.failsafe: another kind of file is there; /etc/banner.failsafe/note is gone | lost: /etc/banner.failsafe: another kind of file is there; /etc/banner.failsafe/note is gone |
| /etc/TZ made a file (the image has a symbolic link) | lost: /etc/TZ: another kind of file is there | lost: /etc/TZ: another kind of file is there |
| /usr/lib/lua/user.lua (added in a directory of packages) | lost: /usr/lib/lua/user.lua is gone | lost: /usr/lib/lua/user.lua is gone |
| /etc/apk/repositories.d/customfeeds.list changed, a key added to /etc/apk/keys | lost: /etc/apk/repositories.d/customfeeds.list: the image's content is there; /etc/apk/keys/user.pem is gone | lost: /etc/apk/repositories.d/customfeeds.list: the image's content is there; /etc/apk/keys/user.pem is gone |
| a kernel module built by hand (/lib/modules/6.99.0/hand.ko) and firmware (/lib/firmware/user.bin) | changed: time: the fraction of the second | lost: /lib/modules/6.99.0/hand.ko is gone; /lib/firmware/user.bin is gone |
| /etc/ssl/certs/user.crt | lost: /etc/ssl/certs/user.crt is gone | lost: /etc/ssl/certs/user.crt is gone |
| an image service the user disabled (/etc/rc.d/S50cron removed) | changed: /etc/rc.d/S50cron is back | changed: /etc/rc.d/S50cron is back |
| a user added on the device (kaan, uid 1001) with a password and a home directory | lost: /home/kaan is gone; /home/kaan/file is gone | lost: /home/kaan is gone; /home/kaan/file is gone |
| root's password changed | kept | kept |
| a package installed on the device: its program and init script (/usr/bin/userpkg, /etc/init.d/userpkg): installed again at the first boot | reinstall | reinstall |
| that package's UCI settings (/etc/config/userpkg): set aside until the package is back, so that they cannot act without it | held: set aside in /etc/mu300/orphaned-config/etc/config | held: set aside in /etc/mu300/orphaned-config/etc/config |
| that package's other settings, changed by the user (/etc/userpkg/settings.yaml) | lost: /etc/userpkg/settings.yaml is gone | lost: /etc/userpkg/settings.yaml is gone |
| that package's data (/etc/userpkg/cache/rules.db, /usr/share/userpkg-data/geo.dat: made at run time, in no package) | lost: /etc/userpkg/cache/rules.db is gone; /usr/share/userpkg-data/geo.dat is gone | lost: /etc/userpkg/cache/rules.db is gone; /usr/share/userpkg-data/geo.dat is gone |

mu300-update apply: 2 by design, 20 changed, 1 held, 2 kept, 26 lost, 1 reinstall
installer, Magisk zip: 1 by design, 5 changed, 1 held, 16 kept, 27 lost, 1 not planted, 1 reinstall
