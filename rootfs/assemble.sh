#!/bin/bash
# Assemble the MU300 rootfs tarball from the exported Ubuntu image + overlay + kernel modules
set -e
KREL=5.4.254-gb50db5b6224c
R=/build/rootfs
# the Ubuntu release of /w/base.tar (24.04 or 26.04): it names the tarball
# (etc/os-release is a link, and docker export names its members without ./)
UBUNTU=$(tar -xOf /w/base.tar usr/lib/os-release 2>/dev/null | sed -n 's/^VERSION_ID="\(.*\)"/\1/p')
[ -n "$UBUNTU" ] || UBUNTU=24.04

# Say which mount is missing instead of failing later on a bare "cp: can't stat". This runs inside the
# container, so every path here is a -v from the docker run in docs/BUILD.md.
miss=
[ -f /w/base.tar ] || miss="$miss\n  /w/base.tar        -v \$PWD/rootfs:/w              (docker run --rm mu300-ubuntu:24.04 tar -c ... > rootfs/base.tar)"
[ -d /w/overlay ] || miss="$miss\n  /w/overlay         -v \$PWD/rootfs:/w              (part of the checkout)"
ls /kmods/*.ko >/dev/null 2>&1 || miss="$miss\n  /kmods/*.ko        -v \$PWD/out/modules:/kmods:ro  (kernel/build-linux.sh output)"
[ -f /kout/modules.builtin ] || miss="$miss\n  /kout/modules.*    -v \$PWD/out:/kout:ro           (kernel/build-linux.sh output)"
[ -f /logdw ] || miss="$miss\n  /logdw             -v \$PWD/tools/logdw/logdw:/logdw:ro"
if [ -n "$miss" ]; then
    printf 'assemble.sh: missing build inputs:%b\n' "$miss" >&2
    echo "see docs/BUILD.md for the full docker run line" >&2
    exit 1
fi
# Optional inputs decide whether the image can use the modem, Wi-Fi and the GPU at all. Missing ones used to
# pass silently and produce an image that boots and does nothing useful, so say what went in.
for o in /firmware /android-subset /android-gpu-subset /bt-init /keys /adbd /carry /cltest; do
    [ -e "$o" ] && echo "assemble.sh: + $o" || echo "assemble.sh: - $o (not mounted; the image will be built without it)"
done

rm -rf $R && mkdir -p $R
tar -xf /w/base.tar -C $R
cp -a /w/overlay/. $R/
# the Android vendor daemons' own system account (android-run, ueventd-perms.sh): Android's system user is uid 1000,
# which here is ubuntu's. mu300-update carries the image's system accounts into an updated system, so updated
# systems have it too (android-vendor-start makes it on one that does not).
grep -q '^mu300vnd:' $R/etc/passwd || useradd --root $R --system --user-group --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin --comment 'MU300 Android vendor daemons' mu300vnd
# /lib is a symlink to usr/lib on Ubuntu; always write through usr/lib
mkdir -p $R/usr/lib/modules/$KREL/extra
cp /kmods/*.ko $R/usr/lib/modules/$KREL/extra/
cp /kout/modules.builtin /kout/modules.builtin.modinfo $R/usr/lib/modules/$KREL/
depmod -b $R $KREL
# per-device identity is created on first boot
# exported from a docker container: without this systemd-detect-virt says "docker" and skips timesyncd, pstore, random-seed
rm -f $R/.dockerenv
rm -f $R/etc/ssh/ssh_host_* ; : > $R/etc/machine-id; rm -f $R/var/lib/dbus/machine-id
# docker manages /etc/hostname, so the exported file is empty
echo mu300 > $R/etc/hostname
# which release this filesystem came from, so mu300-update can tell whether a newer one exists
mkdir -p $R/etc/mu300 && printf '%s\n' "${MU300_VERSION:-dev}" > $R/etc/mu300/image-version
# the packages the image asks for (apt-mark showmanual): an update installs the others again, the ones added on the
# device (mu300-update, upk_record; read with its own function)
(MU300_LIB=1; . $R/opt/mu300/bin/mu300-update; upk_manual $R ubuntu) > $R/etc/mu300/image-packages
[ -s $R/etc/mu300/image-packages ] || { echo "assemble.sh: no package list for etc/mu300/image-packages" >&2; exit 1; }
ln -sfn ../run/systemd/resolve/stub-resolv.conf $R/etc/resolv.conf
# vendor firmware for Wi-Fi (wcnmodem.bin, wifi_board_config*.ini) is copied from the device's /odm/firmware
if [ -d /firmware ]; then mkdir -p $R/usr/lib/firmware && cp /firmware/* $R/usr/lib/firmware/; fi
# the Android vendor subset (modem_control + libs + properties) comes from android-vendor/extract-subset.sh
if [ -d /android-subset ]; then mkdir -p $R/opt/mu300/android && cp -a /android-subset/. $R/opt/mu300/android/ && mv $R/opt/mu300/android/dev/__properties__ $R/opt/mu300/android/dev-properties && rmdir $R/opt/mu300/android/dev; fi
cp /logdw $R/opt/mu300/bin/logdw
# tools/bt-init build (static arm64); Bluetooth also needs bt_configure_pskey.ini/bt_configure_rf.ini from Android /vendor/etc in /firmware
# optional Mali GPU userspace (android-vendor/extract-gpu-subset.sh) and OpenCL test (tools/gpu)
# cp -an FROM/. TO/ needs GNU cp (busybox cp -n skips an existing directory without looking into it); this runs in the build containers only
if [ -d /android-gpu-subset ]; then cp -an /android-gpu-subset/. $R/opt/mu300/android/; fi
if [ -e /cltest ]; then install -D -m755 /cltest $R/opt/mu300/android/system/bin/cltest; fi
if [ -e /bt-init ]; then cp /bt-init $R/opt/mu300/bin/mu300-bt-init; fi
# tools/keys build (static arm64): the buttons (mu300-buttons)
if [ -e /keys ]; then install -m755 /keys $R/opt/mu300/bin/mu300-keys; fi
# tools/adbd build (static arm64, musl): adb over USB, on unless mu300-usb adb off
if [ -e /adbd ]; then install -m755 /adbd $R/opt/mu300/bin/adbd; fi
# tools/carry build (static arm64): an update carries everything of the user's over with it (mu300-update, the carry
# block; without it an update of this image keeps only the list of places, as before the first release with a four-field tag)
if [ -e /carry ]; then install -m755 /carry $R/opt/mu300/bin/mu300-carry; fi
# the manifests of the releases before images carried their own: this system's only (the overlay has OpenWrt's too)
rm -f $R/opt/mu300/lib/image-manifests/openwrt*.gz
# the VPN engines (Xray, hev-socks5-tunnel, sing-box) are not part of the image: they are the vpn extra
# (tools/make-extra.sh, installed with mu300-extra install vpn)
# accounts still the image's (ubuntu/ubuntu) until an installer or mu300-update puts the device's own in place
: > $R/etc/.mu300-accounts-from-image
for u in mu300-accounts.service:sysinit.target mu300-modprobe-guard.service:sysinit.target mu300-early-recorder.service:sysinit.target mu300-vendor.service:sysinit.target mu300-lan.service:multi-user.target mu300-ssh-hostkeys.service:sysinit.target mu300-telnetd.service:multi-user.target serial-getty@ttyGS0.service:getty.target ssh.socket:sockets.target mu300-wifi.service:multi-user.target mu300-wifi-client.service:multi-user.target mu300-mobile-data.service:multi-user.target mu300-mobile-data-watch.service:multi-user.target mu300-fixups.service:sysinit.target mu300-zram.service:swap.target mu300-boot-ok.service:multi-user.target mu300-cp_diskserver.service:multi-user.target mu300-refnotify.service:multi-user.target mu300-extra-modules.service:multi-user.target mu300-hotspot.service:multi-user.target mu300-bluetooth.service:multi-user.target mu300-thermal-guard.service:multi-user.target mu300-firewall.service:sysinit.target mu300-kmsg.service:sysinit.target mu300-toolkit.service:multi-user.target mu300-cpu.service:multi-user.target mu300-cpu-voltage.service:multi-user.target mu300-cpu-voltage-good.timer:timers.target mu300-atd.service:multi-user.target mu300-atd2.service:multi-user.target mu300-modem-log.service:multi-user.target mu300-buttons.service:multi-user.target mu300-power.service:multi-user.target mu300-traffic.service:multi-user.target mu300-user-packages.service:multi-user.target mu300-adbd.service:multi-user.target mu300-update-notice.timer:timers.target mu300-wifi-scan-mode.service:multi-user.target mu300-lock-replay.service:multi-user.target mu300-sms-forward.service:multi-user.target systemd-networkd.service:multi-user.target; do
  svc=${u%%:*}; tgt=${u##*:}
  mkdir -p $R/etc/systemd/system/$tgt.wants
  src=/etc/systemd/system/$svc; [ -e $R$src ] || src=/usr/lib/systemd/system/$svc
  case $svc in serial-getty@*) src=/usr/lib/systemd/system/serial-getty@.service;; esac
  ln -sfn $src $R/etc/systemd/system/$tgt.wants/$svc
done
# the commands people are told to run must be on PATH, including sudo's secure_path, which does not contain
# /opt/mu300/bin - without these links every "sudo mu300-os ..." in the README is a "command not found". One list
# for both images and for mu300-extra link, which adds them at boot on systems installed before a command had one.
for c in $(cat $R/opt/mu300/lib/path-commands); do ln -sfn /opt/mu300/bin/$c $R/usr/local/bin/$c; done
# no graphical/serial login noise on a headless dongle; keep ttyS1 console for debugging
ln -sfn /dev/null $R/etc/systemd/system/getty@tty1.service
# The manifest of the image: every file it holds, with type, mode, owner, size and sha256 (tools/image-manifest.py has
# the format and makes the same lines from the tarball; make-release.sh holds the two against each other). The next
# update reads it to tell the image's files from the user's. Last, when nothing in $R changes any more.
if [ -x $R/opt/mu300/bin/mu300-carry ]; then
    $R/opt/mu300/bin/mu300-carry manifest $R "ubuntu-$UBUNTU" "${MU300_VERSION:-dev}" | gzip -9n > /tmp/image-manifest.gz
    [ "$(gzip -dc /tmp/image-manifest.gz | wc -l)" -gt 1000 ] || { echo "assemble.sh: the image manifest is not right" >&2; exit 1; }
    install -m644 /tmp/image-manifest.gz $R/etc/mu300/image-manifest.gz
fi
cd $R && tar --numeric-owner -czf /w/mu300-ubuntu-$UBUNTU-rootfs.tar.gz .
ls -la /w/mu300-ubuntu-$UBUNTU-rootfs.tar.gz; du -sh $R
