# Building everything yourself

`./install.sh` (prebuilt) needs none of this. Follow this page only if you want to compile the kernel and the root
filesystems instead of downloading them, or if you maintain the project. See [`../README.md`](../README.md) first.

## What the boot looks like

```
LK (slot b, tries=2) ─► custom 5.4 kernel + vendor_boot DTB
   └─► initramfs /init (boot/init)
         ├─ load 85 modules in a fixed order (boot/module-order.txt)
         ├─ misc: restore slot a, unless the rootfs says default-boot=linux
         ├─ bind USB gadget: NCM, else ECM (usb0 up immediately) + ACM console; MU300_USBNET="rndis" binds RNDIS as the one configuration with the console, and replaces the other network function (rndis wins in either order of "rndis ncm"; a panel choice of RNDIS asks for "rndis ncm ecm", so a kernel without f_rndis still gets NCM)
         ├─ an SD card with ext4 "mu300sd", else losetup at the first 2 MiB boundary after the last partition
         │  (27762098176 on the tested device) → ext4 "mu300root" (free space after userdata)
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

### 1. Kernel
One step, from the pinned public sources (kernel tree, realme Wi-Fi/Bluetooth/Mali modules) with all patches applied:
```sh
kernel/build-all.sh    # -> out/Image, out/modules/*.ko, out/modules.builtin*  (about 10 minutes on Apple silicon)
```
Maintainers publish the prebuilt images with `tools/make-release.sh TAG --publish` (it refuses to publish if an image
contains firmware, Android files, host keys or local settings).

**Release names.** A new release is tagged `vYYYY.MM.DD.N`: the real day (UTC) it is built, and `N` = 1, 2, 3, ... for
the releases of that day (no leading zero, at most 99). `tools/make-release.sh --next-tag` prints the next one (today's
date, one more than the highest `N` of today's releases on GitHub and of today's tags); `make-release.sh` takes no
other form for a new release and refuses a tag whose day is not today unless `--any-date` is given (a retry, a build
past midnight). The releases up to v2026.10.27 are `vYYYY.MM.DD`, where the "day" was a counter that ran ahead of the
calendar. Every four-field tag is newer than every three-field one, whatever the dates; four-field tags compare by the
date, then by `N`, as numbers (so `.10` comes after `.9`). docs/FINDINGS.md 125 has the rule, the one table every
comparer is tested against, and what the updaters and installers released before it do with the new names.

**Releasing from GitHub Actions.** The same release, built on a GitHub arm64 runner: Actions > Release > Run workflow,
with the ref `main` (the job refuses any other ref, and a tag or release that already exists), the tag left empty (the
workflow takes `make-release.sh --next-tag`; a tag given must be `vYYYY.MM.DD.N` of today), and prerelease left on. It builds the 5.4 kernel, the mainline 6.18, 7.2 and 7.3 kernels and their modules (about an hour the first time;
about five minutes while the kernel outputs are still in the Actions cache, which they are until `kernel/`, `upstream/`
or the newest kernel.org version changes), runs `tools/make-release.sh --publish --prerelease` and dispatches the Magisk
zips. Then try the prerelease on a device (`tools/smoke.py`, the smoke test: [RELEASING.md](RELEASING.md)), edit its notes, and promote it with
`gh release edit TAG --prerelease=false --latest`: only then do the installers and `mu300-update` offer it. A release the
workflow made has no `tools/gpu/cltest` (it is built locally against Android's libraries and is not in the repository;
`make-release.sh` includes it only when it is there), so `mu300-toolkit`'s GPU stress item is absent on such a release;
a release made by hand on a machine with the binary has it. A hand-run `tools/make-release.sh TAG --publish` still works
as before and does not start the workflow, which only runs when asked for.

The manual steps behind `build-all.sh`:
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

Kernel patches: apply the `kernel/patches/*.patch` files that `KPATCHES` in `kernel/build-all.sh` lists, in that order
(`patch -p1`), to the kernel tree before building, and `echo -gb50db5b6224c > .scmversion` so the release string does
not get a `-dirty` suffix. `xfrm-sysctl-fragment-per-netns.patch` is the one that is not optional on a system that
creates network namespaces (Docker, sandboxes, `unshare -n`): without it the vendor kernel panics in their teardown
(FINDINGS §44).

GPU: copy `kernel_modules/kernel5.4/gpu/natt/mali` from the realme tree (master branch) to `/src/ext-mali` and run
`kernel/build-mali.sh`; pull the userspace with `android-vendor/extract-gpu-subset.sh` and build `tools/gpu/cltest` with
`tools/gpu/build.sh <dir with libc.so libdl.so libOpenCL.so>`.

Bluetooth: build `sprdbt_tty.ko` from the same realme tree (`wcn/bluetooth/driver/tty-pcie`, `BSP_BOARD_UNISOC_WCN_SOCKET=pcie`)
and the vendor-init tool: `docker run --rm -v "$PWD/tools/bt-init":/w mu300-kbuild gcc -O2 -static -o /w/mu300-bt-init /w/mu300-bt-init.c`.

### 2. Android vendor subset (from your device)
```sh
android-vendor/extract-subset.sh android-subset
python3 android-vendor/gen-ueventd-perms.py android-subset/vendor/etc/ueventd.rc <vendor init *.rc> > android-vendor/ueventd-perms.sh
# the F50's inputs are kept in android-vendor/ueventd/: regenerate after changing the generator with
# python3 android-vendor/gen-ueventd-perms.py android-vendor/ueventd/ueventd.rc android-vendor/ueventd/init-perms.rc > android-vendor/ueventd-perms.sh
# (and copy it to rootfs/overlay/opt/mu300/bin/ueventd-perms.sh; tests/test_vendor_ids.py checks both)
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
# the VPN is not part of the image: it is the module of github.com/dikeckaan/mu300-linux-vpn (built there with
# tools/make-module.sh); on the device: sudo mu300-extra install vpn [--from mu300-linux-vpn.tar.gz]
# adbd (adb over USB to Linux, on by default: mu300-usb adb): static aarch64, a couple of minutes; without it the
# image has no adb
sh tools/adbd/build.sh tools/adbd/adbd
# mu300-carry (static aarch64): with it in the image, an update TO this image carries everything of the user's
# over and the image gets the manifest of its own files; without it such an update keeps only the list of places
# an update kept before the first release with a four-field tag (docs/FINDINGS.md 85)
docker run --rm -v "$PWD/tools/carry":/w mu300-kbuild gcc -O2 -static -o /w/mu300-carry /w/mu300-carry.c
docker run --rm -v "$PWD/rootfs":/w -v "$PWD/out/modules":/kmods:ro -v "$PWD/out":/kout:ro \
  -v "$PWD/firmware":/firmware:ro -v "$PWD/android-subset":/android-subset:ro -v "$PWD/tools/logdw/logdw":/logdw:ro \
  -v "$PWD/tools/bt-init/mu300-bt-init":/bt-init:ro -v "$PWD/tools/adbd/adbd":/adbd:ro \
  -v "$PWD/tools/carry/mu300-carry":/carry:ro \
  mu300-ubuntu:24.04 bash /w/assemble.sh
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
Hotspot settings live in `/etc/mu300/hotspot.conf` (`SSID=`, `PSK=`, `BAND=5|2.4`, `CHANNEL=auto|n`, `COUNTRY=`); the installers
copy the current Android hotspot into it while they install (their hotspot question, `MU300_HOTSPOT=no` in the Magisk
zip turns it off), otherwise a random password is generated and shown at login.

From Android, `boot/android-boot-linux.sh boot-linux-slotb.img` boots the image already on `boot_b` again without reflashing.
If Linux ever fails before `mu300-boot-ok` runs, LK sees `tries_remaining=1` on the next boot and falls back to Android.

## OpenWrt with the MU300 control panel (`openwrt-luci`)

`openwrt/build-rootfs.sh` builds all OpenWrt-like systems; `MU300_SYSTEM` picks one (default `openwrt`):
```sh
MU300_SYSTEM=openwrt-luci openwrt/build-rootfs.sh mu300-openwrt-luci-rootfs.tar.gz
```
It is the plain OpenWrt build plus `openwrt/luci-overlay/` (SMS pool, the dashboard's AT channels, IPv6 relay mode,
first-boot defaults), the app in `openwrt/luci-app-mu300/` and the Aurora theme. It is built on OpenWrt only:
`MU300_SYSTEM=openwrt-luci` with `MU300_FLAVOUR=immortalwrt` is refused.

* `MU300_LUCI_THEME_APK` - a local copy of the pinned Aurora `.apk` (`luci-theme-aurora-1.4.0-r20260920.apk`) for
  offline builds; without it the build downloads the file. The SHA256 it is checked against is pinned in the script and
  cannot be overridden.
* `tools/po2lmo.py IN.po OUT.lmo` compiles the app's `po/*/mu300.po` into the `.lmo` catalogs LuCI loads
  (standard-library Python, byte for byte what LuCI's own `po2lmo` writes; `tests/fixtures/po2lmo` holds the
  reference files). The app has no plural messages and no `msgctxt`; the tool refuses both.
* `tools/luci-i18n.py check|extract|update` keeps `po/tr` and `po/zh_Hans` in step with the messages in the app's
  JavaScript, menu, ACL and backend (missing, stale, placeholder and stray CJK problems). `python3 tools/check-i18n.py`
  does the same for the installers' `i18n/*.tsv`.

`tools/make-release.sh` builds the `mu300-openwrt-luci-rootfs.tar.gz` asset next to the other images and audits it
like them. The asset boots from `/openwrt-luci` on the Linux disk.
