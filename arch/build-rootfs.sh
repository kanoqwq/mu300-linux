#!/bin/sh
# Build an Arch Linux ARM root filesystem for the MU300 (runs on the host; needs Docker with arm64 support).
#   arch/build-rootfs.sh [OUT.tar.gz]
# Inputs are the same optional ones the other builds use (see openwrt/build-rootfs.sh):
#   out/modules/*.ko  out/modules.builtin*  firmware/  android-subset/  android-gpu-subset/
#   tools/logdw/logdw  tools/bt-init/mu300-bt-init  tools/gpu/cltest  busybox
# (the VPN is the module of github.com/dikeckaan/mu300-linux-vpn, engines included: mu300-extra install vpn)
#
# Arch Linux ARM ships a generic aarch64 root filesystem and a rolling package set, so the userspace is far newer
# than the 5.4 kernel this device runs. That works because nothing here needs new kernel features, but it also
# means an update can break things: this is the "fun" option, not the one to put in a router that must stay up.
set -eu
KREL=5.4.254-gb50db5b6224c
TOP=$(cd "$(dirname "$0")/.." && pwd)
IN=${MU300_INPUTS:-$TOP}
OUT=${1:-mu300-arch-rootfs.tar.gz}
BASE=ArchLinuxARM-aarch64-latest.tar.gz
# Arch Linux ARM serves over plain HTTP only; the packages themselves are signed and pacman verifies them
URL=http://os.archlinuxarm.org/os/$BASE

cd "$TOP"
mkdir -p arch
[ -f "arch/$BASE" ] || curl -fL --progress-bar -o "arch/$BASE" "$URL"
docker import --platform linux/arm64 "arch/$BASE" mu300-arch-base:latest >/dev/null

opt() { [ -e "$IN/$1" ] && echo "-v $IN/$1:/in/$2:ro" || true; }
# shellcheck disable=SC2046
docker run --rm --platform linux/arm64 \
  -v "$TOP/rootfs/overlay/opt/mu300":/in/opt-mu300:ro -v "$TOP/rootfs/overlay":/in/overlay:ro \
  $(opt out/modules modules) $(opt out/modules.builtin modules.builtin) \
  $(opt out/modules.builtin.modinfo modules.builtin.modinfo) \
  $(opt firmware firmware) $(opt android-subset android-subset) $(opt android-gpu-subset android-gpu-subset) \
  $(opt tools/logdw/logdw logdw) $(opt tools/bt-init/mu300-bt-init bt-init) $(opt tools/gpu/cltest cltest) \
  $(opt busybox busybox) -v "$TOP/arch":/out \
  -e KREL=$KREL -e OUT="$(basename "$OUT")" -e MU300_VERSION="${MU300_VERSION:-dev}" \
  mu300-arch-base:latest /bin/bash -eu -c '
export LANG=C
# pacman 7 sandboxes downloads with Landlock and drops to the "alpm" user; neither works inside this container
grep -q "^DisableSandbox" /etc/pacman.conf || sed -i "/^\[options\]/a DisableSandbox" /etc/pacman.conf
grep -q "^DownloadUser" /etc/pacman.conf && sed -i "s/^DownloadUser/#DownloadUser/" /etc/pacman.conf
# the image ships an empty keyring; without it every package install fails signature verification
pacman-key --init >/dev/null 2>&1
pacman-key --populate archlinuxarm >/dev/null 2>&1
pacman -Sy --noconfirm archlinux-keyring archlinuxarm-keyring >/dev/null 2>&1 || true
pacman -Syu --noconfirm --needed \
    systemd systemd-sysvcompat dbus kmod iproute2 iputils net-tools nftables dnsmasq ethtool \
    openssh wpa_supplicant hostapd iw wireless-regdb bluez bluez-utils rfkill \
    ca-certificates curl wget nano less htop sudo bash-completion \
    e2fsprogs dosfstools file procps-ng psmisc lsof usbutils pciutils python jq wireguard-tools >/dev/null
# the generic Arch ARM image carries its own kernel and the full linux-firmware set (about 1.5 GB); this device
# boots the vendor 5.4 kernel from the boot image and gets its firmware from the Android side
pacman -Rns --noconfirm linux-aarch64 linux-firmware linux-firmware-whence 2>/dev/null >/dev/null || true
pacman -Scc --noconfirm >/dev/null 2>&1 || true
rm -rf /var/cache/pacman/pkg/* /usr/share/doc /usr/share/man /usr/share/info /boot/* 2>/dev/null || true

R=/build/root; mkdir -p $R
for e in /*; do
    case "$e" in /proc|/sys|/dev|/build|/in|/out|/tmp) continue ;; esac
    cp -a "$e" $R/
done
mkdir -p $R/proc $R/sys $R/dev $R/tmp $R/run $R/opt

# the MU300 scripts, systemd units and configuration (same overlay as the Ubuntu image)
cp -a /in/opt-mu300 $R/opt/mu300
cp -a /in/overlay/etc $R/
mkdir -p $R/usr/lib/modules/$KREL/extra
[ -d /in/modules ] && cp /in/modules/*.ko $R/usr/lib/modules/$KREL/extra/
for f in modules.builtin modules.builtin.modinfo; do [ -e /in/$f ] && cp /in/$f $R/usr/lib/modules/$KREL/; done
depmod -b $R $KREL 2>/dev/null || true
[ -d /in/firmware ] && { mkdir -p $R/usr/lib/firmware; cp -a /in/firmware/. $R/usr/lib/firmware/; }
if [ -d /in/android-subset ]; then
    mkdir -p $R/opt/mu300/android && cp -a /in/android-subset/. $R/opt/mu300/android/
    mv $R/opt/mu300/android/dev/__properties__ $R/opt/mu300/android/dev-properties && rmdir $R/opt/mu300/android/dev
fi
# cp -an FROM/. TO/ needs GNU cp (busybox cp -n skips an existing directory without looking into it); this runs in the build containers only
[ -d /in/android-gpu-subset ] && cp -an /in/android-gpu-subset/. $R/opt/mu300/android/
[ -e /in/cltest ] && { mkdir -p $R/opt/mu300/android/system/bin; install -m755 /in/cltest $R/opt/mu300/android/system/bin/cltest; }
[ -e /in/logdw ] && install -m755 /in/logdw $R/opt/mu300/bin/logdw
[ -e /in/bt-init ] && install -m755 /in/bt-init $R/opt/mu300/bin/mu300-bt-init
[ -e /in/busybox ] && { install -m755 /in/busybox $R/opt/mu300/bin/busybox; mkdir -p $R/opt/mu300/busybox-bin; }
# on PATH for sudo too (secure_path has no /opt/mu300/bin)
for c in mu300-toolkit mu300-next-boot mu300-os mu300-update mobile-data mu300-at wifi-client; do ln -sfn /opt/mu300/bin/$c $R/usr/local/bin/$c; done

# identity and defaults
echo mu300 > $R/etc/hostname
mkdir -p $R/etc/mu300 && printf "%s\n" "${MU300_VERSION}" > $R/etc/mu300/image-version
printf "LABEL=mu300root / ext4 defaults,noatime 0 1\n" > $R/etc/fstab
ln -sfn /usr/share/zoneinfo/Europe/Istanbul $R/etc/localtime
sed -i "s/^#en_US.UTF-8/en_US.UTF-8/" $R/etc/locale.gen 2>/dev/null || true
echo "LANG=en_US.UTF-8" > $R/etc/locale.conf
rm -f $R/etc/ssh/ssh_host_*; : > $R/etc/machine-id
ln -sfn /run/systemd/resolve/stub-resolv.conf $R/etc/resolv.conf
# Arch Linux ARM ships an "alarm" user; the installer sets the password of root and of this one
id -u ubuntu >/dev/null 2>&1 || true
chroot $R /usr/bin/useradd -m -s /bin/bash -G wheel ubuntu 2>/dev/null || true
echo "%wheel ALL=(ALL:ALL) ALL" > $R/etc/sudoers.d/wheel; chmod 440 $R/etc/sudoers.d/wheel
# the Android vendor daemons own system account (android-run): uid 1000 is alarm here
chroot $R /usr/bin/useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/bin/nologin \
    --comment "MU300 Android vendor daemons" mu300vnd 2>/dev/null || true

# services: same set the Ubuntu image enables
for u in mu300-vendor.service:sysinit.target mu300-lan.service:multi-user.target mu300-ssh-hostkeys.service:sysinit.target \
         serial-getty@ttyGS0.service:getty.target sshd.service:multi-user.target mu300-wifi.service:multi-user.target mu300-wifi-client.service:multi-user.target \
         mu300-mobile-data.service:multi-user.target mu300-mobile-data-watch.service:multi-user.target \
         mu300-fixups.service:sysinit.target mu300-zram.service:swap.target mu300-boot-ok.service:multi-user.target \
         mu300-cp_diskserver.service:multi-user.target mu300-refnotify.service:multi-user.target \
         mu300-extra-modules.service:multi-user.target mu300-hotspot.service:multi-user.target \
         mu300-bluetooth.service:multi-user.target mu300-thermal-guard.service:multi-user.target \
         mu300-firewall.service:sysinit.target mu300-kmsg.service:sysinit.target mu300-toolkit.service:multi-user.target \
         mu300-atd.service:multi-user.target mu300-atd2.service:multi-user.target \
         systemd-networkd.service:multi-user.target systemd-resolved.service:multi-user.target; do
    svc=${u%%:*}; tgt=${u##*:}
    mkdir -p $R/etc/systemd/system/$tgt.wants
    src=/etc/systemd/system/$svc; [ -e $R$src ] || src=/usr/lib/systemd/system/$svc
    case $svc in serial-getty@*) src=/usr/lib/systemd/system/serial-getty@.service ;; esac
    ln -sfn $src $R/etc/systemd/system/$tgt.wants/$svc
done
ln -sfn /dev/null $R/etc/systemd/system/getty@tty1.service

cd $R && tar --numeric-owner -czf /out/$OUT .
ls -la /out/$OUT; du -sh $R
'
echo "built $TOP/arch/$(basename "$OUT")"
