# Ubuntu 24.04 LTS arm64 root filesystem for the MU300 (exported with `docker export`, not run as a container).
# BASE swaps the distribution: any arm64 Debian-family image with the same package names works, for example
#   docker build --build-arg BASE=debian:13 -t mu300-debian:13 rootfs/
#   docker build --build-arg BASE=kalilinux/kali-rolling -t mu300-kali rootfs/
ARG BASE=ubuntu:24.04
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd systemd-sysv systemd-timesyncd systemd-resolved udev dbus kmod \
    iproute2 iputils-ping net-tools nftables dnsmasq-base ethtool \
    openssh-server busybox-static \
    wpasupplicant hostapd iw wireless-regdb rfkill bluez \
    i2c-tools gpiod jq wireguard-tools \
    ca-certificates openssl alsa-utils curl wget nano vim-tiny less htop sudo bash-completion \
    libcap2-bin locales tzdata e2fsprogs dosfstools file procps psmisc lsof strace usbutils pciutils \
    docker.io docker-compose-v2 \
 && rm -rf /var/lib/apt/lists/*
RUN sed -i 's/^# *\(en_US.UTF-8\|tr_TR.UTF-8\)/\1/' /etc/locale.gen && locale-gen \
 && ln -sf /usr/share/zoneinfo/Europe/Istanbul /etc/localtime && echo Europe/Istanbul > /etc/timezone \
 && echo mu300 > /etc/hostname \
 && (id ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu) \
 && usermod -aG sudo,docker ubuntu && echo 'ubuntu:ubuntu' | chpasswd \
 && printf 'LABEL=mu300root / ext4 defaults,noatime 0 1\n' > /etc/fstab
# Docker (docker.io, containerd, runc, the compose plugin; etc/docker/daemon.json in the overlay): started by its socket on
# the first docker command, not at boot - dockerd and containerd take some 165 MB of RAM that a dongle without
# containers should keep ("sudo systemctl enable docker" starts it at boot, for containers that restart by themselves).
# Left out (dpkg path-exclude, so upgrades do not bring them back): containerd's stress tool, its own CLI (ctr) and the
# docker trust plugin (signed images, Notary v1) - 55 MB that "docker" and "docker compose" do not use.
RUN systemctl disable docker.service containerd.service \
 && systemctl is-enabled docker.socket \
 && printf 'path-exclude=/usr/bin/containerd-stress\npath-exclude=/usr/bin/ctr\npath-exclude=/usr/libexec/docker/cli-plugins/docker-trust\n' \
    > /etc/dpkg/dpkg.cfg.d/mu300-docker \
 && rm -f /usr/bin/containerd-stress /usr/bin/ctr /usr/libexec/docker/cli-plugins/docker-trust
