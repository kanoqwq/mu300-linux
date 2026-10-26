# FAQ

### What is this, in plain words?
It puts a real Linux system (Ubuntu or OpenWrt) on the ZTE F50 / MU300 or ZTE U30 Air 5G hotspot, next to Android. The
hotspot becomes a small Linux computer and router with its own 5G modem. See [Home](Home).

### Will I lose Android?
No. Android stays on the device. On the usual 64 GB device Android, its data and the partition table are never
modified. Only on the 32 GB variant can you *choose* to shrink Android's data partition, which erases Android's data.
See [Going Back to Android](Going-Back-to-Android).

### Which devices work?
The ZTE F50 / MU300 and the ZTE U30 Air (same board, with a battery). The U30 **Pro** uses a different chip (UMS9632)
and is not supported.

### Do I need to root the device?
Yes. The device must already be rooted, and able to boot a modified boot image: an unlocked bootloader, or a locked
one with a patched TrustOS. The bootloader does not have to be unlocked; on a locked one the installer only warns
and asks whether TrustOS is patched. How to root, unlock or patch a device is not part of this project, it gives no
guide for it, and it is done at your own risk.

### Does it work from Windows?
Yes: `install.ps1` (PowerShell) or `install.cmd` (cmd.exe) on Windows 10/11. macOS and Linux use `install.sh`. You need
`adb` on every system.

### Can I install without a computer?
Yes, if Magisk 26 or newer is on the device: install the Magisk zip of a release from the Magisk app. See
[Installation](Installation#installing-from-android-with-a-magisk-zip-no-computer).

### Ubuntu or OpenWrt?
Ubuntu for a general Linux computer, OpenWrt for a lean router, OpenWrt with the control panel for a router with a web
dashboard for the modem. You can install both. See [Choosing a System](Choosing-a-System).

### Which kernel?
5.4 if unsure (the longest tested); 6.18, 7.2 or 7.3 for Ubuntu 26.04 or WireGuard, 6.18 or 7.2 for U30 Air USB host mode (not yet tried on 7.3). You can change it
later. See [Kernels](Kernels).

### How do I connect to it?
Plug it into a computer with USB and run `ssh ubuntu@192.168.77.1` (Ubuntu) or `ssh root@192.168.77.1` (OpenWrt). On a
U30 Air the address is `192.168.78.1`. OpenWrt also has a web page at the same address. Or join its Wi-Fi hotspot.

### What is the default password?
From a computer you choose it during installation (at least 6 characters). A Magisk zip uses the images' own:
Ubuntu's user `ubuntu` has the password `ubuntu`, OpenWrt's `root` has none until you set one (`passwd`, or LuCI's
System > Administration). An update keeps the password the system has: the computer installer asks for it and checks
it (or sets a new one), a Magisk zip keeps it unless a `mu300-install.conf` in the zip says otherwise.

### Does 5G work?
Yes: 5G NSA and LTE, shared with Wi-Fi and USB clients, reconnecting by itself after modem resets. See
[Mobile Data and APN](Mobile-Data-and-APN).

### Can it join my home Wi-Fi instead of using the SIM?
Yes, with `wifi-client`, as long as the network is WPA2 or WPA2/WPA3 mixed (not WPA3-only). The hotspot is off while it
does. See [Wi-Fi Hotspot and Client](Wi-Fi-Hotspot-and-Client).

### Can it run a VPN for everything connected to it?
Yes, with the optional module (WireGuard, OpenVPN, VLESS/VMess/Trojan/Shadowsocks, Clash/mihomo, Xray/sing-box JSON),
with a kill switch. See [VPN](VPN).

### Is there a web page on Ubuntu too?
Optionally, from v2026.10.20: `sudo mu300-extra install panel` adds Cockpit (Ubuntu's web console) with an MU300 page
for the modem, SMS, VPN, power, Wi-Fi and updates, at `https://192.168.77.1:9090`, on the LAN only. See
[Control Panel (LuCI)](Control-Panel-(LuCI)#a-web-panel-on-ubuntu-cockpit).

### Can I use `adb shell` with Linux, like with Android?
Yes. From the release after v2026.10.23 it is on by default and lets every computer in, with a root shell, so the web
installer and other tools work without setting anything up. See [Installation](Installation#adb-to-linux).

### Can anyone with a USB cable get into my device, then?
With the default, yes: adb has no password, so whoever plugs a cable into the device gets a root shell, also after
you set passwords. Only the cable: nothing is reachable over Wi-Fi or the mobile network. To close it, run one of
these on the device; both stay across reboots and updates:

```sh
sudo mu300-usb adb auth keys    # only computers whose key you authorised (sudo mu300-usb adb key adbkey.pub)
sudo mu300-usb adb off          # no adb at all
```

A device where you turned adb off, or set it up with a key, before this release stays as it was.

### Can I make calls or play sound?
Voice calls are not supported. The board has no speaker or microphone; USB sound cards remain supported by the kernel.

### Can I plug in a screen?
No. HDMI over USB-C does not work (the USB-C power-delivery chip never answers). It is a headless machine.

### How much memory and storage do I get?
About 1.4 GB of RAM (the modem firmware reserves most of the rest of the 2 GB) and about 32 GB of storage on the 64 GB
device. On the F50 an SD card can hold Linux instead.

### Can I run Docker, VMs, other distributions?
The kernels include KVM (`/dev/kvm`) and container support. Debian, Kali, Arch Linux ARM and ImmortalWrt can be built
from this repository; see [docs/DISTROS.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/DISTROS.md).

### What happens if Linux breaks?
After a number of failed starts in a row (5 by default, 1-6 at your choice), the device goes back to Android by itself.
From Android, `su -c mu300-linux` starts Linux again.

### How do I update?
`sudo mu300-update check`, then `sudo mu300-update apply`, then reboot. It never updates by itself. From v2026.10.20
the packages you installed yourself come back after the update. See [Updating](Updating).

### How do I remove it?
Go back to Android and run `./uninstall.sh` (or `uninstall.ps1` / `uninstall.cmd`). See
[Going Back to Android](Going-Back-to-Android#uninstalling).

### Does the release contain Android or ZTE files?
No. The images contain no proprietary files; the installer copies the Wi-Fi firmware and the Android modem and GPU
userspace from your own device.

### Can I use OpenWrt's sysupgrade?
**Never.** It would overwrite the device's storage. Use `mu300-update`.

### Is this safe?
It is designed to touch as little as possible and to fall back to Android, but it is an experimental hobby project with
no warranty. A power cut or a mistake can still brick a device. Back up first with `tools/backup-device.sh`.

### Where can I get help?
Read [Troubleshooting](Troubleshooting), then open an issue at https://github.com/dikeckaan/mu300-linux/issues with the
`--check` output or the logs from `tools/collect-logs.sh`. Never post your IMEI.
