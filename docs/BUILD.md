# Building everything yourself

`./install.sh` (prebuilt) needs none of this. Follow this page only if you want to compile the kernel and the root
filesystems instead of downloading them, or if you maintain the project. See [`../README.md`](../README.md) first.

## What the boot looks like

```
LK (slot b, tries=2) ─► custom 5.4 kernel + vendor_boot DTB
   └─► initramfs /init (boot/init)
         ├─ load 85 modules in a fixed order (boot/module-order.txt)
         ├─ misc: restore slot a, unless the rootfs says default-boot=linux
         ├─ bind USB gadget: NCM, else ECM (usb0 up immediately) + ACM console; MU300_USBNET="rndis ncm" adds RNDIS first
         ├─ losetup -o 27762098176 /dev/mmcblk0 → ext4 "mu300root" (free space after userdata)
         └─ switch_root → systemd
               ├─ mu300-vendor   : Android modem_control in a chroot (disarms PM watchdog, boots modem)
               ├─ mu300-lan      : br-lan (usb0 + wlan0) 192.168.77.1 + dnsmasq DHCP/DNS
               ├─ mu300-wifi     : pcie-sprd, wcn_bsp, sprd_wlan_combo
               ├─ mu300-mobile-data : AT on /dev/stty_nr1, sipa_eth0, nftables NAT
               ├─ mu300-bluetooth : sprdbt_tty, mu300-bt-init (PSKey/RF), btattach → bluetoothd
               └─ ssh.socket, telnetd, serial-getty@ttyGS0
```

Read [`FINDINGS.md`](FINDINGS.md) for the reasoning behind each step (LK slot rules, LZ4 ramdisk,
USB dependency chain, the PM watchdog, the `modem_control` process-name check, macOS ECM link state, …).

## Build steps

### Clean Linux 6.18 / 7.2 + OpenWrt TF Magisk packages

The TF package is deliberately self-contained: it builds against the clean upstream branch, writes the OpenWrt
rootfs to a card labelled `mu300sd`, writes Linux only to the slot opposite the running Android slot, and arms
that slot without rebooting from inside Magisk. The installer formats the full selected TF partition as ext4;
there is no 32 GiB limit. It does not repartition the card.
If no valid TF filesystem is present at boot, the initramfs falls back to the existing internal `mu300root` rootfs.
The TF rootfs includes the standalone `openwrt/luci-app-mu300` package by default. The package remains a separate
LuCI application and can be built for other Unisoc OpenWrt systems; `MU300_LUCI_PLUGIN_SRC` can point to a different
source checkout when testing a newer plugin version.
The rootfs also includes the upstream Aurora LuCI theme (pinned APK and SHA256 in `openwrt/build-rootfs.sh`) and
selects it by default. Bootstrap remains installed and can be selected later in LuCI. A fresh TF installation gets
this default; an update that preserves `/etc/config` keeps the user's existing theme preference.

```sh
docker build -t mu300-mainline-build upstream
docker volume create mu300-mainline
docker run --rm -e KV=7.2.8 -e OUTDIR=out-7.2 \
  -v mu300-mainline:/src -v "$PWD/upstream":/work mu300-mainline-build bash /work/build.sh
docker run --rm -e KV=7.2.8 -e OUTDIR=out-7.2 \
  -v mu300-mainline:/src -v "$PWD/upstream":/work mu300-mainline-build bash /work/build-modules.sh
MU300_KERNEL=7.2 MU300_INPUTS=/path/to/private-build-inputs \
  tools/build-openwrt-tf-magisk.sh mu300-linux-openwrt-tf-7.2.zip
# Build 6.18 with upstream/build.sh and build-modules.sh (their defaults), then:
MU300_KERNEL=6.18 MU300_INPUTS=/path/to/private-build-inputs \
  tools/build-openwrt-tf-magisk.sh mu300-linux-openwrt-tf-6.18.zip
```

The private input directory contains the stock `dumps/boot_a.img`, `dumps/misc-head.bin`, Android-derived vendor
files and the existing 5.4 device modules. They are consumed during the build but are not committed. During Magisk
installation, proprietary modem/Wi-Fi firmware is copied from the running Android system into the TF rootfs.

### 1. Kernel
One step, from the pinned public sources (kernel tree, realme Wi-Fi/Bluetooth/Mali modules) with all patches applied:
```sh
kernel/build-all.sh    # -> out/Image, out/modules/*.ko, out/modules.builtin*  (about 10 minutes on Apple silicon)
```
Maintainers publish the prebuilt images with `tools/make-release.sh TAG --publish` (it refuses to publish if an image
contains firmware, Android files, host keys or local settings). The manual steps behind `build-all.sh`:
```sh
git clone https://github.com/dikeckaan/zte-ums9620-kernel-5.4.254   # or the Enceka U30 Air repo
docker build -t mu300-kbuild kernel/
docker volume create mu300-kernel
docker run --rm -v mu300-kernel:/src -v "$PWD/../zte-ums9620-kernel-5.4.254":/tree mu300-kbuild cp -a /tree /src/zte-u30air
cp kernel/f50-stock-B09.config kernel/device.config
docker run --rm -v mu300-kernel:/src -v "$PWD/kernel":/work mu300-kbuild bash /work/build-linux.sh
```
`build-linux.sh` merges `mu300-linux.fragment` into the stock config, switches to ThinLTO and builds `Image` + modules
into `/src/out-linux`. Copy `Image`, `modules.builtin*` and all `*.ko` (flattened, `llvm-strip --strip-debug`) to `out/`.

Wi-Fi driver: extract `kernel_modules/kernel5.4/wcn/wlan/wlan_combo` from the realme C51/C53 AndroidT kernel source into
the volume as `/src/ext-wlan_combo`, then run `kernel/build-wlan.sh` (applies the `patches/wlan_combo-*.patch` files).

Kernel patches: apply `kernel/patches/bluetooth-marlin3-link-policy.patch`, `of-reserved-mem-skip.patch` and `regdb-wens-certificate.patch` to the kernel tree
before building, and `echo -gb50db5b6224c > .scmversion` so the release string does not get a `-dirty` suffix.

GPU: copy `kernel_modules/kernel5.4/gpu/natt/mali` from the realme tree (master branch) to `/src/ext-mali` and run
`kernel/build-mali.sh`; pull the userspace with `android-vendor/extract-gpu-subset.sh` and build `tools/gpu/cltest` with
`tools/gpu/build.sh <dir with libc.so libdl.so libOpenCL.so>`.

Bluetooth: build `sprdbt_tty.ko` from the same realme tree (`wcn/bluetooth/driver/tty-pcie`, `BSP_BOARD_UNISOC_WCN_SOCKET=pcie`)
and the vendor-init tool: `docker run --rm -v "$PWD/tools/bt-init":/w mu300-kbuild gcc -O2 -static -o /w/mu300-bt-init /w/mu300-bt-init.c`.

### 2. Android vendor subset (from your device)
```sh
android-vendor/extract-subset.sh android-subset
python3 android-vendor/gen-ueventd-perms.py android-subset/vendor/etc/ueventd.rc <vendor init *.rc> > android-vendor/ueventd-perms.sh
```

### 3. Boot image
```sh
docker build -t mu300-ubuntu:24.04 rootfs/
docker run --rm mu300-ubuntu:24.04 cat /bin/busybox > busybox && chmod +x busybox   # static, has mdev/losetup/switch_root/telnetd
docker run --rm -v "$PWD/tools/logdw":/w mu300-kbuild gcc -O2 -static -o /w/logdw /w/logdw.c
python3 boot/build-boot-image.py --stock-boot dumps/boot_a.img --misc-head dumps/misc-head.bin \
  --kernel out/Image --modules out/modules --busybox busybox --logdw tools/logdw/logdw \
  --ueventd-perms android-vendor/ueventd-perms.sh --android-subset android-subset --out boot-linux-slotb.img
```

### 4. Root filesystem on the free eMMC region
1. Check on *your* device that the space after `userdata` is really unallocated (compare the last partition end with
   the GPT's last usable LBA) and adjust `ROOT_OFFSET` in `boot/init` and `tools/android-mount-mu300root.sh`.
2. On Android (root), create the filesystem through a bounded loop device and verify offset/size first.
3. Assemble and deploy:
```sh
cid=$(docker create mu300-ubuntu:24.04); docker export $cid > rootfs/base.tar; docker rm $cid
tools/fetch-xray.sh       # optional: xray + hev-socks5-tunnel, mu300-vpn's default engine (pinned, sha256-checked)
tools/fetch-sing-box.sh   # optional: the sing-box engine for mu300-vpn (pinned release, sha256-checked)
python3 tools/build-release.py run --workspace /home/kano/openwrt-build --label loader -- python3 tools/modem-loader/build.py
docker run --rm -v "$PWD/rootfs":/w -v "$PWD/out/modules":/kmods:ro -v "$PWD/out":/kout:ro \
  -v "$PWD/out/modem-loader/modem-loader-patch":/modem-loader-patch:ro \
  -v "$PWD/firmware":/firmware:ro -v "$PWD/android-subset":/android-subset:ro -v "$PWD/tools/logdw/logdw":/logdw:ro \
  -v "$PWD/tools/bt-init/mu300-bt-init":/bt-init:ro -v "$PWD/sing-box":/sing-box:ro -v "$PWD/xray":/xray:ro -v "$PWD/hev-socks5-tunnel":/hev-socks5-tunnel:ro mu300-ubuntu:24.04 bash /w/assemble.sh
```
Push `mu300-ubuntu-24.04-rootfs.tar.gz` to the device and extract it with `tools/android-mount-mu300root.sh`.
`firmware/` holds `wcnmodem.bin`, `gnssmodem.bin` and `wifi_board_config*.ini` from the device's `/odm/firmware`, plus
`bt_configure_pskey.ini` and `bt_configure_rf.ini` from `/vendor/etc`.

### 5. Boot Linux
```sh
boot/flash-trial.sh boot-linux-slotb.img
```
After about 50 s: `ssh ubuntu@192.168.77.1` (password `ubuntu`, **change it**), `telnet 192.168.77.1`, or
`screen /dev/cu.usbmodem* 115200`. `sudo /opt/mu300/bin/mobile-data status|up [APN]|down|sim-reset` controls the modem (`mu300-mobile-data-watch` reconnects automatically unless you ran `down`). `sudo reboot` returns to Android. If a trial fails, collect logs from Android with
`tools/collect-logs.sh`.

Make Linux the default (inside Linux):
```sh
sudo mu300-next-boot linux     # every successful boot re-arms slot b (mu300-boot-ok.service)
sudo mu300-next-boot android   # next reboot goes to Android and stays there
sudo mu300-next-boot status
```
Hotspot settings live in `/etc/mu300/hotspot.conf` (`SSID=`, `PSK=`, `BAND=5|2.4`, `CHANNEL=auto|n`, `COUNTRY=`); `tools/android-import-hotspot.sh`
copies the current Android hotspot into it before the first boot, otherwise a random password is generated and shown at login.

From Android, `boot/android-boot-linux.sh boot-linux-slotb.img` boots the image already on `boot_b` again without reflashing.
If Linux ever fails before `mu300-boot-ok` runs, LK sees `tries_remaining=1` on the next boot and falls back to Android.
