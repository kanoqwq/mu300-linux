# Running other distributions

The boot path does not care which distribution it starts. The initramfs mounts the Linux filesystem (ext4 on an SD
card labelled `mu300sd`, else the one in the free eMMC area), reads `/.mu300/boot-os`, and `switch_root`s into the directory named there. So a system is just a directory
with a root filesystem in it, and `mu300-os <name>` picks the one to boot next.

What a distribution has to bring:

* **arm64 (aarch64) userspace.**
* **It must work on the kernel you run.** With ZTE's vendor 5.4 this is the limit in practice: the newest
  userspaces have started to depend on newer kernels. Ubuntu 26.04's `tar` resolves every path with `openat2`
  (Linux 5.6) and fails on 5.4, which is why Ubuntu 26.04 is offered only with the mainline kernels (6.18, 7.2, 7.3).
* **Nothing else.** Drivers, firmware and the Android vendor chroot come from this repository's overlay, which is
  copied into every image.

## Ready to build here

| Distribution | Build | Notes |
|---|---|---|
| Ubuntu 24.04 LTS | `rootfs/` (default) | the tested default, systemd 255 |
| Ubuntu 26.04 LTS | `docker build --build-arg BASE=ubuntu:26.04 -t mu300-ubuntu:26.04 rootfs/` then `rootfs/assemble.sh` | released; needs kernel 6.18, 7.2 or 7.3 |
| OpenWrt 25.12 | `openwrt/build-rootfs.sh` | router use, LuCI, ~140 MiB RAM |
| OpenWrt 25.12 with the MU300 panel | `MU300_SYSTEM=openwrt-luci openwrt/build-rootfs.sh` | released as `openwrt-luci`, see [BUILD.md](BUILD.md) |
| ImmortalWrt 25.12 | `MU300_FLAVOUR=immortalwrt openwrt/build-rootfs.sh` | OpenWrt fork with more drivers and LuCI apps |
| Arch Linux ARM | `arch/build-rootfs.sh` | rolling, 258 MiB image / 922 MiB installed; built, not yet booted on hardware |
| Debian 13 | `docker build --build-arg BASE=debian:13 -t mu300-debian:13 rootfs/` then `rootfs/assemble.sh` | same package names as Ubuntu |
| Kali Linux | `docker build --build-arg BASE=kalilinux/kali-rolling -t mu300-kali rootfs/` then `rootfs/assemble.sh` | see below |

The Arch build removes the `linux-aarch64` kernel and `linux-firmware` packages that the generic Arch ARM image
ships (about 1.5 GB): this device boots the vendor kernel from the boot image and takes its firmware from Android.
`pacman`'s Landlock sandbox is disabled for the build only, because it cannot work inside the build container.

## Kali Linux, honestly

Kali is Debian, so it installs and runs exactly like the Debian image, with its tools available through `apt`. What
it cannot do on this device:

* **No monitor mode or packet injection.** The Wi-Fi chip is a Unisoc SC2355 with a vendor driver that offers
  station and access-point mode only. `airmon-ng` has nothing to switch, so the classic Wi-Fi attacks are out.
* **No external Wi-Fi adapter on the F50**: its USB-C port runs as a *gadget* (the device pretends to be a network
  card for your computer) and is its power supply. The U30 Air has host mode on the mainline kernels
  (`mu300-usb host`), and those kernels carry MediaTek, Ralink and Realtek USB Wi-Fi drivers; an adapter has not
  been tried here.
* **No screen.** Everything is over SSH; Kali's desktop tooling is pointless on this hardware.

What is left is still useful: a pocket-sized box with its own 5G modem that can run `nmap`, `sqlmap`, `metasploit`,
proxies and scanners over the mobile connection or the Wi-Fi it is connected to.

## Survey of the popular distributions (measured 2026-09-18)

Two things decide whether a distribution can run on this device's 5.4 kernel, and both are readable from its own
binaries: the minimum kernel its glibc was compiled for (the ELF ABI note), and whether its userspace already uses
syscalls 5.4 does not have — `openat2` (5.6) in `tar` is the one that bit us with Ubuntu 26.04.

| Distribution (arm64) | glibc needs | `tar` uses `openat2` | Verdict |
|---|---|---|---|
| Ubuntu 22.04 / 24.04 LTS | 3.7 | no | ✅ works (24.04 is the tested default) |
| Debian 12 / 13 / sid | 3.7 | no | ✅ works, built here |
| Kali rolling | 3.7 | no | ✅ works, built here (see the Kali section) |
| Arch Linux ARM | — | no | ✅ works, built here |
| OpenWrt / ImmortalWrt 25.12 | musl | no | ✅ works, built here |
| Alpine 3.22 | musl | no | ✅ userspace fine — but OpenRC, so the services need porting |
| Fedora 42 / 43 | 3.7 | no | ✅ userspace fine, not built yet |
| Rocky 9, AlmaLinux 10, Oracle Linux 9 | 3.7 | no | ✅ userspace fine, not built yet |
| Amazon Linux 2023 | 3.7 | n/a | ✅ userspace fine, not built yet |
| Gentoo stage3 arm64 | 3.7 | no | ✅ userspace fine; OpenRC unless the systemd stage3 is used |
| Void Linux | — | — | ✅ rootfs tarball exists; runit, so the services need porting |
| Raspberry Pi OS arm64 | Debian | no | ✅ Debian underneath; take the rootfs out of its image |
| openSUSE Tumbleweed | 4.3 | **yes** | ⚠️ works only after replacing `tar` (same trap as Ubuntu 26.04) |
| Ubuntu 26.04+ | 3.7 | **yes** | ⚠️ same on 5.4; fine on 6.18, 7.2 and 7.3, where it is released |
| Arch (official image), Void (official image), Clear Linux | — | — | ❌ no arm64 container image (Arch: use Arch Linux ARM) |

No glibc on this list demands a kernel newer than 5.4, so the kernel is rarely the blocker; what breaks is individual
programs reaching for new syscalls, and those can be swapped out one by one.

### The part that is actual work

The MU300 services — modem bring-up, the Android vendor chroot, Wi-Fi, the LAN bridge, the hotspot, the toolkit —
exist as **systemd units** (used by Ubuntu, Debian, Kali, Arch, Fedora and friends) and as **procd init scripts**
for OpenWrt/ImmortalWrt. A distribution with a different init (Alpine's OpenRC, Void's runit) boots fine, but
somebody has to write those ~15 service definitions again before the modem and Wi-Fi come up by themselves.

## What does not fit

* **Anything that needs its own kernel** (Fedora IoT, postmarketOS images, Home Assistant OS): they ship kernels
  built for other devices, and this one only boots this project's kernels (vendor 5.4, mainline 6.18, 7.2 and 7.3) from
  the boot image.
* **On 5.4, distributions whose userspace requires a newer kernel**: Ubuntu 26.04 and later, and eventually any
  rolling distribution; use 6.18, 7.2 or 7.3 for those. Arch works today; a future `glibc` or `systemd` may end that, which is why it is the "fun" option
  and not the default.
* **x86 anything.**

## Installing a distribution you built

The installer knows `ubuntu`, `openwrt` and `openwrt-luci` by name.
 For anything else, unpack the tarball next to them and select
it (from Android, with the Linux filesystem mounted as in `tools/android-mount-mu300root.sh`):

```sh
mkdir -p /data/local/tmp/mu300root/arch
tar -xzpf mu300-arch-rootfs.tar.gz -C /data/local/tmp/mu300root/arch
echo arch > /data/local/tmp/mu300root/.mu300/boot-os
```

From a running Linux on the device, the same thing under `/mnt/mu300-disk/`, then `mu300-os arch` and reboot.
If it does not boot, the bootloader falls back to Android by itself, and `mu300-os ubuntu` brings the old system
back.
