# Glossary

Every word in plain language first, then what it means for this device. Grouped by topic.

## The device and its insides

**SoC (system on a chip):** The one chip that holds the processor, graphics, modem and more, like the brain and heart in one. *Here:* the Unisoc T760.

**Unisoc T760 / UMS9620:** Two names for the same chip, made by Unisoc. *Here:* both the F50 and the U30 Air use it on the board `ums9620_2h10_feimao`. The U30 Pro uses a different chip (UMS9632) and is not supported.

**Modem:** The part that talks to the mobile network, like a phone without a screen. *Here:* it needs a few Android programs (`modem_control` and friends) that Linux runs in a sandbox.

**eMMC:** The built-in storage chip, like a soldered-in memory card. *Here:* about 58 GiB on the 64 GB device; Linux uses the part no partition owns.

**Partition:** A fenced-off area of the storage with a name, like rooms in a house. *Here:* Android has many (boot, system, userdata, …); Linux does not get one, it lives in the unfenced space behind them.

**Partition table (GPT):** The map that says where each partition starts and ends. *Here:* the installer never changes it, except when you choose to shrink `userdata` on the 32 GB variant.

**userdata:** Android's partition for apps, settings and files. *Here:* it is encrypted as a whole, so Linux cannot keep its files inside it; on the 32 GB variant it fills the disk.

**misc:** A tiny partition where the bootloader keeps notes, such as which slot to start and how many tries are left. *Here:* the installer writes 32 bytes of it to tell the bootloader to try Linux.

**OpenCL / GPU:** The GPU is the graphics processor; OpenCL lets programs use it for number crunching. *Here:* the Mali-G57 runs OpenCL 3.0 without a screen.

**KVM:** A Linux feature for running virtual machines fast. *Here:* `/dev/kvm` exists on all four kernels.

**zram:** Compressed swap kept in RAM, so more fits in memory. *Here:* `mu300-zram` adds lz4 zram of half the RAM, on by default; `sudo mu300-zram off` turns it off and `/etc/mu300/zram.conf` sets size, compressor and swappiness.

**OTG / USB host:** Normally the device is the "guest" on your computer's USB; in host mode it is the "boss" and you can plug things into it. *Here:* the U30 Air can be a USB host on the mainline kernels (`sudo mu300-usb host`); the F50's port is its power supply, so no.

## Starting up

**Bootloader (LK):** The first small program that runs at power-on and decides what to start. *Here:* Unisoc's LK picks the slot from the notes in `misc`.

**Slot (A/B):** The device has two copies of the boot partitions, A and B, so an update can be tried on one while the other stays safe. *Here:* Android uses one slot; Linux's boot image goes into the other.

**Boot image:** A package holding the kernel and a small starter program (initramfs) that the bootloader loads. *Here:* the Linux boot image is built from your device's own Android boot image plus this project's kernel.

**Kernel:** The core of an operating system, which talks to the hardware. *Here:* you choose 5.4, 6.18, 7.2 or 7.3 ([Kernels](Kernels)).

**Vendor kernel vs. mainline kernel:** A vendor kernel is the chip maker's own modified Linux; mainline is the official Linux from kernel.org. *Here:* 5.4 is the vendor kernel, 6.18, 7.2 and 7.3 are mainline with this project's patches.

**LTS (long-term support):** A version that gets fixes for years. *Here:* Ubuntu 24.04/26.04 LTS and Linux 6.18 LTS.

**initramfs:** A tiny temporary system inside the boot image that prepares everything before the real system starts. *Here:* it loads the drivers, finds the Linux filesystem (SD card or free eMMC space) and counts failed boots.

**Trial boot:** Starting something on probation: if it does not report back as healthy, it does not count as good. *Here:* each Linux boot must be confirmed by `mu300-boot-ok` about 30 s after the USB network and SSH are up.

**Fallback:** Going back to the safe option automatically. *Here:* after N unconfirmed Linux boots in a row (1-6, default 5) the device starts Android.

**Rollback:** Undoing an update. *Here:* `mu300-update rollback` (system) and `rollback-boot` (kernel and boot image).

## Android side

**Root:** Full administrator rights on Android. *Here:* required before installing; getting it is not part of this project.

**Unlock:** Allowing the bootloader to start software it was not shipped with. *Here:* the device must already boot a modified Android boot image.

**Magisk:** A popular tool that gives Android root and can install "modules". *Here:* the installer adds a module whose Action button (or `su -c mu300-linux`) starts Linux; releases also ship Magisk zips that install Linux from the device.

**adb:** Android Debug Bridge, a computer program that talks to an Android device over USB. *Here:* the installer uses it; Linux offers it too, on by default and for every computer, with a root shell and no password (`sudo mu300-usb adb auth keys` lets in only computers whose key you authorised, `sudo mu300-usb adb off` turns it off).

**USB debugging:** The Android setting that lets `adb` connect. *Here:* must be on.

## Linux and systems

**Distribution:** A complete Linux system: kernel plus programs plus a way to install more. *Here:* Ubuntu and OpenWrt are offered.

**Ubuntu:** A widely used general-purpose Linux distribution, with `apt` for software. *Here:* 24.04 LTS (default) or 26.04 LTS (needs a mainline kernel).

**OpenWrt:** A Linux distribution made for routers: small and network-focused. *Here:* 25.12, plain or with the MU300 control panel.

**LuCI:** OpenWrt's web interface. *Here:* at http://192.168.77.1 (U30 Air: .78.1).

**UCI:** OpenWrt's settings system (`uci set …; uci commit`). *Here:* updates keep your UCI configuration.

**rootfs / filesystem:** The tree of folders and files a system lives in. *Here:* each system is a folder on the Linux disk (`/ubuntu`, `/openwrt`, `/openwrt-luci`).

**ext4:** The standard Linux way of organising files on a disk. *Here:* the Linux disk is ext4, labelled `mu300root` (internal) or `mu300sd` (SD card).

**SSH:** A secure way to type commands on another computer over the network. *Here:* `ssh ubuntu@192.168.77.1`.

**Serial console:** A plain text connection that works even when the network does not. *Here:* over the same USB cable, `screen /dev/cu.usbmodem* 115200` on macOS.

## Networking

**Hotspot / access point:** A Wi-Fi network the device creates for others to join. *Here:* 5 GHz or 2.4 GHz, one at a time.

**Wi-Fi client / station:** The device joining someone else's Wi-Fi, like a phone does. *Here:* `wifi-client`; the radio is either hotspot or client, decided at boot.

**WPA2 / WPA3 / SAE:** Wi-Fi password security standards; SAE is the handshake WPA3 uses. *Here:* the driver has no SAE, so WPA3-only networks cannot be joined (WPA2 and mixed mode work), and the hotspot is WPA2-PSK only: WPA3 on it is disabled for security ([U30AIR-SAE.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/U30AIR-SAE.md)).

**NCM / ECM / RNDIS:** Ways for a USB device to appear as a network card to a computer. *Here:* NCM is the default, ECM the fallback, RNDIS selectable.

**LTE:** 4G mobile data.

**5G NSA / EN-DC:** "Non-standalone" 5G, which uses a 4G connection as an anchor and adds 5G on top; EN-DC is the name of that combination. *Here:* mobile data runs on 5G NSA or LTE; the panel can lock EN-DC.

**Band lock:** Telling the modem to use only certain frequency bands or cells. *Here:* in the panel's Network locks; stored in the modem, so it also applies in Android until reset.

**APN:** The name of the "door" into your carrier's data network. *Here:* leave it empty to use what the SIM defines.

**PDP context:** The data session the modem opens with the carrier, using the APN. *Here:* `MU300_PDP_TYPE` sets its type on Ubuntu.

**AT commands:** Short text commands for talking to a modem (they start with `AT`). *Here:* the panel has a guarded AT terminal; all tools share one channel through `mu300-atd`.

**SMS / PDU:** Text messages; PDU is the raw encoded form a modem uses. *Here:* `sms` uses PDU mode because text mode garbles some senders.

**USSD:** The short codes like `*101#` that ask the carrier for your balance. *Here:* `sudo mu300-ussd '*101#'`.

**DHCP:** How devices get an address automatically when they join a network. *Here:* the device hands out addresses to its clients.

**DNS:** The internet's phone book, turning names into addresses.

**IPv6:** The newer kind of internet address.

**RA (router advertisement):** An IPv6 message in which a router tells devices "here is the network and how to reach the internet". *Here:* the control-panel OpenWrt relays the carrier's RAs to the LAN.

**NAT / NAT66:** Sharing one public address among many devices; NAT66 is the IPv6 version. *Here:* how clients share the mobile connection.

**TTL:** A counter in every packet that drops by one at each router. *Here:* `mu300-ttl set 64` fixes it for mobile data. Check your operator's terms first.

## VPN

**VPN:** A protected tunnel to another server that your traffic goes through. *Here:* the optional `mu300-vpn` module.

**Kill switch:** A rule that blocks all traffic when the VPN is down, so nothing leaks. *Here:* on by default in `mu300-vpn`.

**WireGuard:** A modern, fast VPN built into the Linux kernel. *Here:* available on the mainline kernels.

**OpenVPN:** An older, widely used VPN program. *Here:* install the system's OpenVPN package to use `.ovpn` profiles.

**Xray / sing-box / mihomo / dae:** Proxy engines for protocols such as VLESS, VMess, Trojan and Shadowsocks, for Clash-style configs, and (dae) an eBPF proxy that runs inside the kernel. *Here:* the VPN module picks the engine that speaks your profile; from v2.0.0 each engine is an extra (`vpn-xray`, `vpn-singbox`, `vpn-mihomo`, `vpn-dae`), and dae needs kernel 6.18, 7.2 or 7.3 of v2026.10.20 or newer.

**BTF / eBPF:** eBPF lets programs run safely inside the kernel; BTF is the type information such programs need to find their way around. *Here:* the mainline kernels carry BTF from v2026.10.20 so that dae can run.

**DFS channel:** A 5 GHz Wi-Fi channel (52-144) that radar also uses; an access point there must first listen for radar. *Here:* the Wi-Fi driver cannot, so the hotspot never uses one (a DFS channel you pick is replaced by 36 or 149).

**Cockpit:** Ubuntu's web console (terminal, logs, services, networking). *Here:* the optional `panel` extra on Ubuntu, with an MU300 page for the modem, SMS, VPN and power.

**Subscription (VPN):** A URL your VPN provider gives you that yields a list of profiles. *Here:* `mu300-vpn subscription add`, refreshed by hand or on a schedule; also in the panel's VPN menu.

**Tailscale:** A mesh VPN for reaching your own devices. *Here:* its traffic goes through the active tunnel by default.
