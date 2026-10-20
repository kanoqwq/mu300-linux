# Linux on the ZTE F50 / MU300 and U30 Air

Local builds and packaging: [workspace and release workflow](docs/LOCAL-BUILD.zh-CN.md).

Maintainer notes: [stock behavior and new-device adaptation checklist](docs/NEW-DEVICE-STOCK-ADAPTATION.zh-CN.md),
[U30 Air charge/power/recovery evidence](docs/U30AIR-STOCK-CHARGE-20261016.zh-CN.md).

Windows desktop installer: extract the complete release package and run root `OpenWrt-Installer.exe`. Device selection, persistent recovery and the two-stage Android reset flow share the existing backend. Python/CLI and Magisk entries remain available. See the bilingual [user guide](installer/GUIDE.md), [build instructions](installer/README.md), and [verification/screenshots](installer/VERIFICATION.md). Multiple ADB devices can be selected in the GUI or interactive CLI, or explicitly bound with `--serial SERIAL`.


U30 Air mainline updates now include both charger variants, corrected fuel-gauge persistence,
Type-C current detection, OTG boost control and native dual-SIM boot/live switching.
See the [U30 Air port and verification record](docs/U30AIR-UPSTREAM-20261015.zh-CN.md).

V50/MU3351 desktop-installer support and the new LED driver are documented in
[the V50 adaptation record](docs/V50-MU3351-20261014.zh-CN.md), including what has
been checked on hardware and what remains unverified.

[![Latest Release](https://img.shields.io/github/v/release/dikeckaan/mu300-linux?logo=github)](https://github.com/dikeckaan/mu300-linux/releases/latest)
[![Total Downloads](https://img.shields.io/github/downloads/dikeckaan/mu300-linux/total?color=blue&logo=github)](https://github.com/dikeckaan/mu300-linux/releases)
[![Stars](https://img.shields.io/github/stars/dikeckaan/mu300-linux?logo=github)](https://github.com/dikeckaan/mu300-linux/stargazers)
[![Forks](https://img.shields.io/github/forks/dikeckaan/mu300-linux?logo=github)](https://github.com/dikeckaan/mu300-linux/network/members)
[![Issues](https://img.shields.io/github/issues/dikeckaan/mu300-linux?logo=github)](https://github.com/dikeckaan/mu300-linux/issues)
[![License](https://img.shields.io/badge/license-MIT%20%2F%20GPL--2.0-blue)](LICENSE)

The ZTE F50 is a pocket 5G router. This project turns it into a small Linux computer: **Ubuntu 24.04 LTS** or
**OpenWrt**, with SSH, Wi-Fi, Bluetooth and its 5G modem working. Android stays on the device, and you can go back
to it at any time.

Think of it as a Raspberry Pi that already has a 5G modem, a Wi-Fi access point and 32 GB of storage inside.

OpenWrt TF ZIPs can also be extracted and used with the [desktop installer](desktop/README.md):
TF deployment, userdata images, independent internal partitions and SSH updates.
The original Magisk TF installation remains unchanged.
Desktop independent-partition installation checks GPT free ranges first. If the
chosen size fits, it adds `mu300root` there and preserves Android userdata,
including encrypted userdata. Only insufficient contiguous space requires a
userdata shrink/reset. This desktop path writes a new GPT entry; the legacy
raw-region installation described below leaves GPT unchanged on 64 GB devices.

The **ZTE U30 Air** is supported too: the same board and chip with a battery. The installer recognises which one it
is talking to; see [Supported devices](#supported-devices).

> **Türkçe:** ZTE F50 / MU300'ü küçük bir Linux bilgisayarına çevirir: Ubuntu 24.04 veya OpenWrt; SSH, Wi-Fi,
> Bluetooth ve 5G modem çalışır. Android cihazda kalır, istediğiniz an geri dönersiniz. Kurulum: önce
> `./install.sh --check` ile cihazınıza bakın, sonra `./install.sh` ile kurun; `./uninstall.sh` ile kaldırın.
> ZTE U30 Air de desteklenir (aynı kart, pilli); kurulum programı cihazı kendisi tanır.

---

## Supported devices

| | ZTE F50 / MU300 | ZTE U30 Air |
|---|---|---|
| Board, chip | `ums9620_2h10_feimao`, Unisoc T760 (UMS9620) | the same |
| Power | USB only | battery (4050 mAh), charger and fuel gauge |
| LEDs used by Linux | the blue LED: mobile data | as in ZTE's firmware: battery (white: Linux is up), network (blue: 4G, white: 5G, red: no service), Wi-Fi (white: 2.4 GHz, blue: 5 GHz) |
| Heat alarm | the LED flashes red and blue | the battery LED flashes red, white, blue in turn: the SoC at 85 °C or the battery at 50 °C, until they cool down (`thermal-guard`) |
| USB network | `192.168.77.1` | `192.168.78.1` (so both can be plugged into one computer) |
| Tested | everything below | 5.4, 6.18 and 7.2: USB, Wi-Fi hotspot, Bluetooth, mobile data, VPN, LEDs |
| Battery | - | level, voltage, current, temperature and charging state in `mu300-toolkit` and `/sys/class/power_supply` on every kernel (mainline: `sc27xx-fgu`) |
| USB host (OTG) | - (its USB port is its power supply) | `sudo mu300-usb host` with an OTG adapter: flash drives (FAT, exFAT), keyboards and mice, USB modems and Ethernet adapters, with 5 V from the battery; `sudo mu300-usb device` back to the computer (the default at every boot). Mainline kernels; HDMI through USB-C adapters does not work |
| Buttons | power: held 3 s shuts down | power: a short press wakes the LEDs (they go dark after 60 s), held 3 s shuts down; double-press the Wi-Fi key within 500 ms to switch 2.4 / 5 GHz (a single press only wakes the LEDs), held 3 s turns it off or on |
| NFC | - | a phone held to the device joins the hotspot, as with ZTE's firmware: `sudo mu300-nfc` shows the tag, `wifi` writes the hotspot's name and password (again at every hotspot start), `url https://...` or `text ...` anything else, `clear` empties it, `on`/`off` is the NFC switch of ZTE's web interface (kept in the tag) |

Both run the same kernel, the same systems and the same releases; what differs is a handful of drivers for the U30
Air's charger and LEDs, which its boot image loads in place of the F50's ([`kernel/u30air.fragment`](kernel/u30air.fragment)).
Nothing about the device has to be chosen by hand: the installer reads it from Android, and the boot image, the
LEDs and the default address follow. The U30 **Pro** is a different chip (UMS9632) and is not supported.

## What you get

* **A real Linux system**, not an app or a container: Ubuntu 24.04 LTS with systemd and `apt`, or OpenWrt with its
  LuCI web interface.
* **Internet over 5G/LTE**, shared with everything connected to the device.
* **A Wi-Fi hotspot** (5 GHz or 2.4 GHz) and **USB networking**: plug it into a computer and it shows up as a network adapter.
* **SSH access** at `192.168.77.1` (U30 Air: `192.168.78.1`), plus a USB serial console.
* **Bluetooth** and the **Mali GPU** (OpenCL; no screen output).
* **`mu300-toolkit`**, a menu like `raspi-config`: temperatures, CPU and RAM use, network speeds, performance
  profiles, stress tests, VPN and services.
* **Android stays installed.** One command switches back.

## What it costs you

* **Android and Linux share the device.** Only one runs at a time; a reboot switches between them.
* **No screen output.** HDMI over USB-C does not work (the power-delivery chip never answers), so this is a headless
  machine you use over SSH or the web interface.
* **No sound.** The speaker and microphone path does not work yet.
* **1.4 GB of RAM.** The modem firmware permanently reserves the rest.

## Before you start

Your device must already be **rooted and unlocked** (it must already boot a modified Android boot image), and you
need `adb` on your computer. Getting to that point is not part of this project.

> **Warning.** Installing writes to the `boot_b` and `misc` partitions. If something goes badly wrong you may need a
> recovery tool (SPD/BROM) to revive the device. On the usual 64 GB device Android, its data and the partition table
> are never modified. The **32 GB variant** is the exception: it has no free space at all, and the installer offers to
> make some by shrinking `userdata` — that rewrites the partition table and erases everything in Android, it is
> experimental, and it asks first. Take a full backup with `tools/backup-device.sh` before going that way.
> There is always some risk. Nothing here is endorsed by ZTE or Unisoc.

**What protects you:**
* Linux is installed into empty, unused space on the internal storage; Android's partitions are not touched (except on the 32 GB variant, where you can choose to shrink `userdata` to create that space).
* Linux starts as a "trial boot". If it fails to start, the bootloader returns to Android by itself.
* `./uninstall.sh` puts everything back.

**You need:**
* A ZTE F50 / MU300 or U30 Air, rooted, connected by USB, with USB debugging enabled.
* A computer with `adb`:
  * **macOS or Linux:** also Python 3, `lz4` and `curl` (usually already installed).
  * **Windows 10/11:** PowerShell. The installer installs Python 3 (for your user, with winget or from
    python.org) and its `lz4` module itself when they are missing. Use `install.ps1` / `uninstall.ps1` below,
    or `install.cmd` / `uninstall.cmd` from cmd.exe (no execution-policy change needed).
* About 15 minutes.

## Install

**Step 0 — back up what cannot be replaced (recommended).**

```sh
tools/backup-device.sh          # macOS / Linux, device in rooted Android
```

This copies your unit's own data — `prodnv`, the modem calibration and IMEI partitions, the bootloader chain — to
your computer and verifies every dump against the device. Installing never touches these, but if they are ever lost
no firmware download can bring them back, and the modem stays broken. Keep the folder somewhere safe and out of any
repository: it contains your IMEI.

**Step 1 — check your device.** This only reads; it writes nothing.

```sh
./install.sh --check          # macOS / Linux
.\install.ps1 -Check          # Windows (PowerShell)
install.cmd -Check            # Windows (cmd)
```

It reports the storage size, where Android's partitions end and how much free space follows them. On the 64 GB
device that is about 32 GiB.

If it reports none at all, you have the **32 GB variant**, where `userdata` fills the disk. The installer can make
room by shrinking it — that erases everything in Android and rewrites the partition table, so it is experimental
and it asks first. Back the device up with `tools/backup-device.sh` before saying yes. If the numbers look like
neither case, stop and open an issue with what `--check` printed; they identify the variant.

**Step 2 — install.**

```sh
./install.sh                  # macOS / Linux
.\install.ps1                 # Windows (PowerShell)
install.cmd                   # Windows (cmd)
```

![The installer: language, checks, systems and Ubuntu release](docs/images/installer/installer-1-start.png)

It asks a few questions (Ubuntu, OpenWrt or both; which one boots; a password), downloads the ready-made images,
copies the Wi-Fi and modem files from your own device, shows exactly what it is about to write, and waits for you to
type `INSTALL`. Then it reboots into Linux.

The installer speaks **English, Türkçe and 中文**: it asks at the start (English is the default; `MU300_LANG=tr`
or `.\install.ps1 -Lang zh` skips the question). Adding a language is one file: see `i18n/README.md`. Before anything else it brings your copy of the project up to date
with GitHub - a `git clone` is fast-forwarded, a downloaded zip gets the files that changed - and restarts itself if
there was anything new; without GitHub it simply continues (`MU300_NO_SELF_UPDATE=1` / `-NoSelfUpdate` skips it).

With Ubuntu it asks for the release: **24.04 LTS** (the default, the longest tested) or **26.04 LTS (beta)** - the
newest, with systemd 259; tested on the device for a shorter time. An installed Ubuntu moves
to the other release with `sudo MU300_UBUNTU=26.04 mu300-update apply` (or `24.04`), keeping settings and data.

It also asks for the **kernel**:

| choice | kernel | |
|---|---|---|
| 1 | 5.4 | Unisoc's vendor kernel (Android 12 base): the longest tested, everything this project supports |
| 2 | 6.18 | mainline Linux, the current long-term (LTS) release: newer drivers and security fixes, the same functions (hotspot, mobile data, SMS, Bluetooth, VPN, GPU); no USB-C video output yet |
| 3 | latest stable (7.2 for now) | the newest mainline release: the newest drivers, the same functions as 6.18; tested less than 6.18 |

It can be changed later on the device with `sudo mu300-update kernel 5.4`, `... kernel 6.18` or `... kernel 7.2`.

![The kernel question, in Turkish](docs/images/installer/installer-2-kernel-tr.png)

Before it writes anything it shows what it is about to do and waits for `INSTALL`:

![The summary before installing, and the end of the install](docs/images/installer/installer-3-summary.png)

If Linux is already installed it asks whether to **update** or **wipe**:

* **update** — reinstalls the systems but keeps your settings and data: `/etc/mu300` (hotspot, VPN, toolkit), user
  accounts and home directories, `/usr/local`, SSH host keys, OpenWrt's UCI config, and the services you enabled
  yourself.
* **wipe** — erases the Linux filesystem and installs from scratch.

If the device is running Linux rather than Android when you start, the installer notices and offers to reboot it
into Android for you over SSH.

**Step 3 — log in.** Wait about a minute, then on the computer it is plugged into:

```sh
ssh ubuntu@192.168.77.1        # the password you chose during the install
```

For OpenWrt use `ssh root@192.168.77.1`, or open `http://192.168.77.1` in a browser for LuCI. On a U30 Air the
address is `192.168.78.1` instead.

The Wi-Fi network the device broadcasts is its hotspot; unless you chose otherwise it uses the name and password
copied from Android.

## Everyday use

`sudo mu300-toolkit` opens a menu for everything below. The direct commands:

| I want to… | Command |
|---|---|
| Watch temperatures, CPU, RAM, network speed | `mu300-toolkit top` |
| Make it faster, or cooler and quieter | `sudo mu300-toolkit profile performance` (also `eco`, `balanced`) |
| Test stability under load | `sudo mu300-toolkit stress all 10` |
| Check the mobile connection | `sudo mobile-data status` |
| Set the APN | Ubuntu: `/etc/mu300/mobile-data.conf` (`MU300_APN`, `MU300_PDP_TYPE`). OpenWrt: LuCI → Network → Interfaces → wan, or `uci set network.wan.apn='…'; uci commit network; ifup wan`. Leave it empty to keep the context the SIM defines, which is what most carriers expect |
| Change the Wi-Fi name or password | edit `/etc/mu300/hotspot.conf`, then `sudo systemctl restart mu300-hotspot` |
| Connect the device to someone else's Wi-Fi | `sudo mu300-toolkit` → Network → Wi-Fi → "Join a network", or `sudo wifi-client scan` then `sudo wifi-client connect "NAME" "PASSWORD"` |
| Update to the newest release | `sudo mu300-update check` then `sudo mu300-update apply` |
| Fixed TTL for mobile data (so the operator cannot tell hotspot traffic from the device's own) | `sudo mu300-ttl set 64` (`sudo mu300-ttl off` goes back to the default), or `mu300-toolkit` -> Network -> TTL |
| Switch between OpenWrt and Ubuntu | `sudo mu300-os openwrt` / `sudo mu300-os ubuntu` |
| Failed boots in a row before it falls back to Android (1-6, default 5) | `sudo mu300-next-boot attempts N` |
| Go back to Android | `sudo mu300-next-boot android`, then `sudo reboot` |
| Return to Linux from Android | `su -c mu300-linux` on the device (see below), or `boot/android-boot-linux.sh boot-linux-slotb.img` from a computer |
| Send all traffic through a VPN | see below |

### Starting Linux from Android without a computer

`android/magisk/build.sh` builds a small Magisk module. Once it is installed, the Magisk app gets an **Action**
button that reboots the device into Linux, and `su -c mu300-linux` does the same from a terminal
(`su -c 'mu300-linux status'` just shows which system is on which slot). It writes only the same 32 bytes of
`misc` that the installer does. See [android/magisk/README.md](android/magisk/README.md).

### VPN

The device can send its own traffic **and** everything from connected clients through a VLESS server. The default
engine is [Xray](https://github.com/XTLS/Xray-core) behind [hev-socks5-tunnel](https://github.com/heiher/hev-socks5-tunnel)
on a kernel TUN; `ENGINE=sing-box` in the config switches back to sing-box. Links that ask for `allowInsecure`
work: Xray 26 dropped that option, so the server's certificate is fetched once, pinned, and re-fetched by itself
when the server renews it. The kill switch (`KILL_SWITCH=1`) only works with `ENGINE=sing-box` for now; an
existing configuration that has it on stays on sing-box after an update.

```sh
sudo cp /etc/mu300/vpn.conf.example /etc/mu300/vpn.conf
sudo nano /etc/mu300/vpn.conf         # paste your vless:// link into VLESS_URI, set ENABLE=1
sudo systemctl enable --now mu300-vpn
mu300-vpn status
```

### Updating

The device can update itself from a published release, without a computer:

```sh
sudo mu300-update check           # installed version vs newest release
sudo mu300-update apply           # system, kernel and boot image: download, unpack, switch; then reboot
sudo mu300-update rollback        # back to the previous system
sudo mu300-update rollback-boot   # back to the previous kernel and boot image
```

`mu300-toolkit` offers the same under System -> Software update. Your settings, users, `/usr/local` and the vendor
files (Wi-Fi firmware, Android modem userspace) are carried over, and the previous version is kept as `<os>.old` for
a rollback until you run `mu300-update clean`. The new filesystem is unpacked beside the old one and only swapped in
at the end, so an interrupted download cannot leave a half-updated system.

The kernel and the boot image are updated too. The boot image keeps your device's own part (its Android files and
the stock header) as it is and gets the release's kernel and the generic part of its ramdisk, so no computer and
nothing from Android are needed. The previous image is kept for `rollback-boot`, and a kernel that does not start
sends the device back to Android on its own after the usual number of failed boots.

`mu300-update apply` downloads and checks every file first and changes nothing before all of them are there; from
v2026.09.28 on it also switches to the updater of the release it installs before it starts.

**Updating from v2026.09.27 or older?** Fetch the new updater first, then update; do both right after a reboot
(older boot images can lose mobile data after a few quiet minutes, see docs/FINDINGS.md 32):

```sh
sudo curl -fL https://github.com/dikeckaan/mu300-linux/releases/latest/download/mu300-update -o /opt/mu300/bin/mu300-update
sudo mu300-update apply
```

On OpenWrt, as root: `wget -O /opt/mu300/bin/mu300-update https://github.com/dikeckaan/mu300-linux/releases/latest/download/mu300-update`
and then `mu300-update apply`. Reboot afterwards to start the new system and kernel.

**Back in Android after an update?** With the older boot images a single crash or reset of Linux (the update could
cause one, see above) makes the device fall back to Android and stay there. Start Linux again with the Linux button
of the Magisk module or `su -c mu300-linux`, then update as above. If Linux does not start any more, the boot image
did not survive: run the installer again from a computer and choose `update` - it writes a fresh boot image and
keeps your settings and data.

## Uninstall

With the device back in Android:

```sh
./uninstall.sh                # macOS / Linux
.\uninstall.ps1               # Windows (PowerShell)
uninstall.cmd                 # Windows (cmd)
```

It makes Android the boot system again, restores the second boot partition and erases the Linux filesystem. Your
Android data is left alone, unless you ask it to give the space back (see below). You choose how thorough the erase is:

* **secure** (default) — overwrites the whole Linux region and then verifies it is empty, so your files are really
  gone. Takes a few minutes.
* **quick** — only erases the filesystem headers; the files stay readable on the flash until the space is reused.
* **keep** — leaves the Linux filesystem alone; it simply never boots again.

If you shrank `userdata` to make room on the 32 GB variant, it then offers to grow it back over the freed space.
That is off by default and asks twice, because on the 64 GB device the same answer would hand Android the free
area it has always had — and like the shrink, it erases Android's data again.

Like the installer, it offers to reboot the device from Linux into Android first.

## What works and what does not

| | |
|---|---|
| Ubuntu 24.04 LTS / OpenWrt 25.12 | ✅ boots, no failed services |
| Mobile data (5G NSA / LTE) | ✅ shared with Wi-Fi and USB clients; reconnects by itself after modem resets |
| Wi-Fi access point | ✅ 5 GHz (802.11ac) or 2.4 GHz, one at a time |
| USB network + serial console | ✅ `192.168.77.1` (U30 Air `192.168.78.1`), `screen /dev/cu.usbmodem* 115200` |
| SSH, telnet | ✅ |
| Bluetooth | ✅ BlueZ, scanning works |
| GPU (Mali-G57) | ✅ OpenCL 3.0, headless |
| Storage | ✅ about 32 GB in the unused area of the internal eMMC on the 64 GB device; on the 32 GB one you choose the split with Android |
| RAM | ✅ 1.4 GB usable (the modem firmware keeps the rest) |
| Temperature control, status LEDs, SIM tray | ✅ |
| Back to Android, automatic rollback | ✅ |
| Screen output (HDMI over USB-C) | ✗ the USB-C power chip never answers, so no display |
| Sound | 🚧 the card comes up (`sprdphone-sc2730`, 19 PCM devices), the audio DSP loads and answers, and calls connect — but no audio moves: every scene takes one buffer and stops. Android does not get further on this board either ([FINDINGS 24](docs/FINDINGS.md)) |
| Mainline kernel (6.18) | 🚧 experimental, see [`upstream/`](upstream/) |

## How it works, in short

The device has two Android boot slots, A and B. Android lives on slot A and is left alone. The installer puts a
custom Linux kernel into slot B and marks it as a one-time trial. At boot, a small startup program loads the
device's drivers, finds the Linux filesystem in the unused part of the internal storage and starts Ubuntu or OpenWrt
from it. If Linux ever fails to start, the bootloader falls back to Android by itself. Three small Android programs
keep running inside Linux in a sandbox, because the modem needs them.

The kernel is built from ZTE's published (GPL) source. The reasoning behind each step is in
[`docs/FINDINGS.md`](docs/FINDINGS.md), and the full build is in [`docs/BUILD.md`](docs/BUILD.md).

## Common problems

**The device does not come back after installing.** Wait two minutes. If there is still nothing, unplug and replug
it: the bootloader will have returned to Android on its own. Collect logs with `tools/collect-logs.sh`.

**The computer sees the device but gets no address (macOS).** macOS does not set up a network interface it has
never seen while the screen is locked. Each device has its own USB MAC address, so the first time one is plugged in
(or after an update that brought these addresses) unlock the Mac, and the interface appears. With both an F50 and a
U30 Air plugged in, they are `192.168.77.1` and `192.168.78.1`; two of the same kind need `/etc/mu300/lan.conf` to
tell them apart.

**No internet.** Check that the SIM has a data plan, then run `sudo mobile-data status`. A missing plan looks like a
connection that keeps dropping.

**Websites think you are in another country.** The device has no GPS, so sites guess from the IP address; mobile
operators and VPN servers often look like a different city.

**I forgot the password, and Linux boots by default.** You do not need to log in to get out:

1. **Back to Android:** unplug the device about ten seconds after it powers on, then plug it in again. The
   bootloader sees an unfinished boot and falls back to Android by itself (`mu300-boot-ok` only confirms a boot
   ~30 s after the system is up, so an interrupted boot never counts as successful).
2. **Set a new password** from your computer, without booting Linux:
   ```sh
   tools/reset-password.sh            # ubuntu, openwrt or both
   ```
   It mounts the Linux filesystem from Android and rewrites the password hash, keeping all your data.
3. Start Linux again with `boot/android-boot-linux.sh work/boot-linux-slotb.img`, or reboot if Linux is the default.

Re-running `./install.sh` and choosing **update** also sets a new password and keeps your data.

**Downloads are slow.** GitHub's release CDN throttles single connections in some regions (0.2 MB/s on a
180 Mbit/s line here). The installer already downloads in 8 parallel chunks, which measured 8x faster; set
`MU300_FETCH_JOBS` to change that, or point `MU300_RELEASE_URL` at your own mirror.

**Never use OpenWrt's `sysupgrade` or flash OpenWrt firmware images here.** They are written for ordinary computers
and would overwrite the device's storage. A normal `apk upgrade` is fine, except `kernel` and `kmod-*` packages.

## For developers

* [`docs/BUILD.md`](docs/BUILD.md) — build the kernel and images yourself (`kernel/build-all.sh` does it in one step).
* [`docs/FINDINGS.md`](docs/FINDINGS.md) — everything learned about this hardware and why each workaround exists.
* [`docs/DISTROS.md`](docs/DISTROS.md) — running other distributions (ImmortalWrt, Arch Linux ARM, Debian, Kali)
  and what the 5.4 kernel rules out.
* [`upstream/`](upstream/) — the mainline 6.18 kernel port.
* [Releases](https://github.com/dikeckaan/mu300-linux/releases) — prebuilt images. They contain **no proprietary
  files**; the installer takes those from your own device.

| Path | Contents |
|---|---|
| `install.sh`, `uninstall.sh` | installer and remover for macOS/Linux |
| `install.ps1`, `uninstall.ps1` | the same for Windows (PowerShell); `install.cmd`, `uninstall.cmd` launch them from cmd.exe |
| `kernel/` | kernel build environment, config, patches |
| `boot/` | initramfs `init`, boot image builder, slot handling |
| `rootfs/` | Ubuntu image: `Dockerfile`, `assemble.sh`, services and scripts in `overlay/` |
| `openwrt/` | OpenWrt and ImmortalWrt image build |
| `arch/` | Arch Linux ARM image build |
| `android-vendor/` | scripts that copy the needed Android files from *your* device |
| `tools/` | helper programs, release tooling, backup, SSH/serial/log helpers |

The kernel source used here is mirrored at
[`dikeckaan/zte-ums9620-kernel-5.4.254`](https://github.com/dikeckaan/zte-ums9620-kernel-5.4.254).

## Credits and licenses

* Kernel source: ZTE's GPL release for the U30 Air (mirrored by Enceka) and the Unisoc drivers in it — GPL-2.0.
* Wi-Fi, Bluetooth and GPU drivers: realme C51/C53 AndroidT kernel release — GPL-2.0; the patches in
  `kernel/patches` are GPL-2.0.
* Scripts, tools and documentation in this repository: MIT (see `LICENSE`).
* Stock firmware, Android vendor components and bootloaders belong to their owners and are not distributed here,
  with one exception: [`stock/`](stock/) holds the stock `trustos` (TEE) image for firmware `ZYV1.0.0B09`, as a
  last-resort repair for devices whose own TEE is damaged; all rights to it remain with ZTE/Unisoc. Read
  [`stock/README.md`](stock/README.md) before touching it — it can make a non-booting device worse.
