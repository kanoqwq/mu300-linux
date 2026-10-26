# Docker

Containers on the MU300: Docker 29 with compose, on Ubuntu out of the box and on OpenWrt one command away. The same text
is in the [README](https://github.com/dikeckaan/mu300-linux#docker); the details are FINDINGS 130.

## Ubuntu (24.04 and 26.04): installed

Ubuntu's own packages: `docker.io`, `containerd`, `runc` and the compose plugin (`docker-compose-v2`). The user `ubuntu`
is in the `docker` group.

```sh
docker run --rm hello-world             # the first docker command starts the engine
docker compose up -d                    # in a directory with a compose.yaml
sudo systemctl enable docker            # start it at every boot (containers with a restart policy come back)
sudo systemctl disable docker           # back to the default: started by its socket on first use
```

The engine does not run until something uses it (`docker.socket`): with containerd it takes some 165 MB of RAM.

**The `docker` group is as good as root** (a container can mount the whole system): keep only trusted accounts in it.
`sudo gpasswd -d ubuntu docker` takes `ubuntu` out; `docker` then needs `sudo`.

## OpenWrt and OpenWrt with the control panel: ready to install

The settings and the firewall zone are in the image; the programs are not (190 MB, which an update would need twice in
the smallest region OpenWrt is installed in):

```sh
apk update && apk add dockerd docker docker-compose
apk add luci-app-dockerman                         # control panel system: LuCI > Docker
/etc/init.d/dockerd start                          # "enable" to start it at every boot
```

They are installed again after each update (internet needed). On an 800 MiB region, remove them before an update
(`apk del dockerd docker docker-compose`) and add them again after it: the images and containers stay.

## Where the data is

`/mnt/mu300-disk/docker/<system>` - on the Linux partition next to the systems, not inside them. Updates and rollbacks do
not touch it: nothing is copied, nothing is lost. Each system (ubuntu, openwrt, openwrt-luci) has its own. Storage driver
`overlay2`. Settings: `/etc/docker/daemon.json`.

## Networking and the firewall

Docker uses its **nftables** backend: its own tables (`ip docker-bridges`), no iptables rules, and it never changes the
device's firewall. Containers reach the internet; published ports are reachable from the hotspot and USB clients, not
from the mobile network. Containers cannot open anything on the device itself (SSH, the web panels, LuCI, ubus); their
DNS still works. On OpenWrt the firewall zone `docker` holds every Docker network (172.16.0.0/12): it reaches the
internet, not the LAN, and one Docker network does not reach another; add a forwarding `wan -> docker` to publish to the
mobile network. Works on kernels 5.4, 6.18, 7.2 and 7.3.
