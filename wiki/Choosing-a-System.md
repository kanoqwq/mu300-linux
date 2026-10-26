# Choosing a System

**In one sentence:** pick **Ubuntu** if you want a general-purpose Linux computer, **OpenWrt** if you want a router,
and **OpenWrt with the control panel** if you want a router with a friendly web page for the 5G modem. You can
install two of them and switch.

**The simple version:** Ubuntu is a full workshop with every tool; OpenWrt is a small, tidy router with a web page;
the control-panel OpenWrt is the same router with an extra dashboard for the SIM card and the radio.

## Side by side

| | Ubuntu | OpenWrt | OpenWrt with the MU300 control panel |
|---|---|---|---|
| Installer name | `ubuntu` | `openwrt` | `openwrt-luci` |
| Version | 24.04 LTS (default) or 26.04 LTS | 25.12 | 25.12 |
| RAM in use (installer's figure) | ~500 MiB | ~140 MiB | not stated by the installer |
| Installed size (measured) | ~580 MiB | ~320 MiB | - |
| Install software with | `apt` | `apk` | `apk` |
| Web interface | optional: Cockpit with an MU300 page (`mu300-extra install panel`, from v2026.10.20); otherwise SSH and `mu300-toolkit` | LuCI | LuCI + the MU300 panel (with a VPN section and a terminal in the browser) |
| Log in as | `ubuntu` (with `sudo`) | `root` | `root` |
| Good for | servers, containers, scripts, development, learning Linux | a lean router | a router you manage from the browser: signal, band locks, SMS, AT terminal, power |
| Magisk zip | yes | yes | yes (since v2026.10.18) |

All three get mobile data, the Wi-Fi hotspot and client, USB networking, SSH, SMS (`sms`), the toolkit and the
optional [VPN](VPN) module.

## The installer's own words

```
==> What should be installed?
  1) Ubuntu LTS: 24.04 or 26.04, asked next (full distribution, apt, ~500 MiB RAM in use)
  2) OpenWrt 25.12.6 (router, LuCI web UI, ~140 MiB RAM in use)
  3) both (switch later with: mu300-os ubuntu|openwrt)

==> Which OpenWrt?
  1) OpenWrt 25.12.6: the standard LuCI web interface
  2) OpenWrt 25.12.6 with the MU300 control panel: dashboard, cellular locks, SMS, AT terminal, USB modes (by kanoqwq)

==> Which Ubuntu?
  1) 24.04 LTS  the longest tested, supported until 2029
  2) 26.04 LTS  the newest (systemd 259, newer packages), supported until 2031; kernel 6.18, 7.2 or 7.3
```

`MU300_OPENWRT=plain|luci` answers the OpenWrt question without asking.

## Space needed

The installer asks for room for the system plus a spare copy kept by updates: about **800 MiB** for OpenWrt alone,
**1.6 GiB** for Ubuntu alone and **2.4 GiB** for both. On the usual 64 GB device (about 32 GiB free) any choice fits.

## Ubuntu 24.04 or 26.04?

* **24.04 LTS** is the default and the longest tested. It works with every kernel.
* **26.04 LTS** is the newest, supported until 2031. Its programs use system calls the 5.4 kernel does not have (`tar`, for one, cannot
  unpack folders there), so it **needs kernel 6.18, 7.2 or 7.3**. The installer will not let you combine it with 5.4.

An installed Ubuntu moves to the other release with `sudo MU300_UBUNTU=26.04 mu300-update apply` (or `24.04`),
keeping settings and data.

## Two systems, one device

Both systems live side by side on the Linux disk as directories; the one named in `/.mu300/boot-os` starts. Switch:

```sh
sudo mu300-os ubuntu
sudo mu300-os openwrt
sudo mu300-os openwrt-luci   # the one with the control panel
```

Then reboot. Only one runs at a time.

## Other distributions

Debian, Kali, Arch Linux ARM and ImmortalWrt can be built from this repository, but are not released or offered by
the installer. What works, what does not (no monitor mode for Kali, for instance) and how to install a system you
built yourself: [docs/DISTROS.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/DISTROS.md).

## Never on this device

**Never use OpenWrt's `sysupgrade` or flash OpenWrt firmware images.** They are written for ordinary routers and
would overwrite the device's storage. A normal `apk upgrade` is fine, except the `kernel` and `kmod-*` packages.
Updates go through [`mu300-update`](Updating).

Next: [Kernels](Kernels).
