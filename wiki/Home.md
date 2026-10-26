# MU300 Linux wiki

**In one sentence:** this project puts a real Linux system (Ubuntu or OpenWrt) on the ZTE F50 / MU300 and the ZTE
U30 Air pocket 5G hotspots, next to Android, so the little box becomes a Linux router and server with its own 5G
modem.

![Android and Linux side by side on one device](https://kaandikec.com/mu300-linux/assets/img/two-systems.svg)

## The simple version

Your hotspot is a tiny computer. Out of the box it runs Android, hidden behind a web page, and does one job: share
mobile internet. This project adds a second system, Linux, in a part of the storage Android does not use. When the
device starts, it starts Linux. If Linux ever fails to start a few times in a row, the device quietly goes back to
Android on its own. Android is never removed, and one command brings it back for good.

Think of it as a Raspberry Pi that already has a 5G modem, a Wi-Fi access point and 32 GB of storage inside.

## Where to start

| If you want to… | Read |
|---|---|
| understand what you need and install it | [Installation](Installation) |
| pick between Ubuntu, OpenWrt and OpenWrt with the control panel | [Choosing a System](Choosing-a-System) |
| pick a kernel (5.4, 6.18, 7.2 or 7.3) | [Kernels](Kernels) |
| get mobile data working, set an APN | [Mobile Data and APN](Mobile-Data-and-APN) |
| change the hotspot, or join another Wi-Fi | [Wi-Fi Hotspot and Client](Wi-Fi-Hotspot-and-Client) |
| read and send text messages | [SMS](SMS) |
| route everything through a VPN | [VPN](VPN) |
| use the web control panel | [Control Panel (LuCI)](Control-Panel-(LuCI)) |
| update, or roll an update back | [Updating](Updating) |
| go back to Android, or remove Linux | [Going Back to Android](Going-Back-to-Android) |
| fix something | [Troubleshooting](Troubleshooting) and [FAQ](FAQ) |
| look up a word | [Glossary](Glossary) |
| know how the hardware behaves | [Hardware Notes](Hardware-Notes) |
| build it yourself or help out | [Building from Source](Building-from-Source), [Contributing](Contributing) |

There is also an illustrated website with the same information for beginners, in English, Turkish and Chinese:
**https://kaandikec.com/mu300-linux/**

## Supported devices

| | ZTE F50 / MU300 | ZTE U30 Air |
|---|---|---|
| Chip | Unisoc T760 (UMS9620), board `ums9620_2h10_feimao` | the same |
| Power | USB only | battery (4050 mAh) |
| USB network address | `192.168.77.1` | `192.168.78.1` |
| SD card | yes: Linux can live on the card | no card slot |

The installer recognises which device it is talking to. The **U30 Pro** is a different chip (UMS9632) and is
**not** supported. More in [Hardware Notes](Hardware-Notes).

## What you get, and what you lose (spoiler: nothing)

**You get:** Ubuntu 24.04 or 26.04 LTS, or OpenWrt 25.12 (plain or with a control panel for the modem); three
kernels to choose from; 5G/LTE internet shared over Wi-Fi and USB; SSH, and `adb` over USB; Bluetooth;
SMS and USSD; an optional VPN module; a web panel on Ubuntu too (Cockpit with an MU300 page, optional); a
`raspi-config`-like menu called `mu300-toolkit`; automatic fallback to Android; updates on the device.

**Follow the project:** releases and news are announced on the Telegram channel, [t.me/mu300project](https://t.me/mu300project).

## New in v2026.10.25

The release of 2026-10-25 (a prerelease first, promoted once a board has run it; v2026.10.24 stayed a prerelease:
its updater could stop with "another mu300-update is running", and its OpenWrt came up without adb). What it
changes, and where to read on:

* **adb to Linux is on by default, for every computer.** Plug the cable in and `adb shell` is a root shell, no key
  needed; the graphical and web installers rely on it. **Anyone who can plug a cable into the device gets that
  shell**, also after you set passwords. A device that never touched adb becomes open with this update; one where
  you ran `mu300-usb adb off`, or turned adb on with a key, stays as it was. To close it: `mu300-usb adb auth keys`
  (only authorised keys) or `mu300-usb adb off`. [Installation](Installation#adb-to-linux), [FAQ](FAQ)
* **A web installer:** [kaandikec.com/mu300-linux/web-installer](https://kaandikec.com/mu300-linux/web-installer/)
  installs from Chrome or Edge over USB, with nothing to install on the computer: you choose the system and the
  kernel, and the device downloads the release itself. Run end to end on an F50 from Chrome on macOS; Windows, a
  Linux computer and the U30 Air are not tested with it yet. [Installation](Installation)
* **SMS forwarding: a service's own error counts.** "The answer must contain" (`errcode=0`, `code=0`, `ok=true`...)
  makes an HTTP 200 with an error in it a failed delivery, and the delivery log has a Clear button (issue #171).
  [SMS](SMS)
* **Long SMS** from one sender under a reused reference number are no longer mixed or left in parts. [SMS](SMS)
* **An APN with a user name and a password** (`AT+CGPCO`), on OpenWrt (`network.wan.username`/`password`/`auth`) and
  on Ubuntu (`mobile-data.conf`). [Mobile Data and APN](Mobile-Data-and-APN)
* **The panel's saved band and cell locks survive an update** (they were dropped; the modem kept the lock and the
  panel showed none).
* **Windows: the installer can make room** on a unit whose `userdata` fills the eMMC, as `install.sh` does, and says
  the figures when it refuses (issue #32). [Installation](Installation)
* **U30 Air, firmware variant ABSSV:** the mainline kernels look for the charger at a second address (an SGM41513
  at 0x1a) when nothing answers at the first (issue #102; not confirmed on such a unit yet). [Kernels](Kernels)
* **`mu300-update`** no longer stops with "another mu300-update is running" when the release brings a newer
  updater. [Updating](Updating)
* **Kernels:** 5.4 unchanged; 6.18.56 and 7.2.10 rebuilt with the charger change. [Kernels](Kernels)

## New in v2026.10.23

The release of 2026-10-23 (a prerelease first, promoted once a board has run it). What it changes, and where to read
on:

* **The modem on mainline after a Magisk install** (issues #127, #170): Android files the installer imported could
  end up readable by root only, and the modem's processor was then never loaded. The installers write them with the
  right modes now, and every boot repairs an import that is already there. [Troubleshooting](Troubleshooting)
* **A thermal zone on 6.18 and 7.2:** the board's temperature is a kernel thermal zone (`mu300_thermal`), and
  `thermal-guard` hands over to it. [Kernels](Kernels)
* **SMS arrive at once on OpenWrt:** the modem delivers a message as it comes in (`+CMT`) instead of waiting for the
  next poll, and forwarding to a webhook can have a body of your own (issue #171). [SMS](SMS)
* **The panel** shows the SIM's own number, shows a carrier name the modem sends as hex as text (issue #174), hides
  a SIM slot the board does not have, and a band or cell lock reset is checked after it is made.
* **The VPN page says why a config was refused** instead of "profile not added", and its check works. The engines'
  own fixes are in VPN module v2.0.1 (`mu300-extra install vpn` again): sing-box, mihomo and dae find their server
  on carriers that only let DNS through to their own resolvers, mihomo configs with GEOSITE/GEOIP rules start (the
  geodata comes with the extra), dae stays up on 6.18 and 7.2, and switching to a profile that cannot run leaves the
  working tunnel alone. [VPN](VPN)
* **Installers:** a stalled write to the SD card or the internal storage is noticed and reported, and the boot image
  is read back after it is written. [Installation](Installation)
* **Kernels:** 5.4 rebuilt with `NFT_QUEUE`; 6.18.56 and 7.2.10 with the thermal module. [Kernels](Kernels)

## New in v2026.10.22

The release of 2026-10-22 (a prerelease first, promoted once a board has run it). Fixes again. What it changes, and
where to read on:

* **Sending an SMS no longer fails with `SMS FAILED: CODE:600`** on networks that refused every submission (China
  Broadnet; issue #176): a send is now one AT transaction, with nothing else getting at the modem between the setup
  and the submission, and the SMSC is written back to the modem unchanged before each one; on OpenWrt (`mu300-sms`
  and the panel) and on Ubuntu (`sms`). [SMS](SMS), [Troubleshooting](Troubleshooting)
* **The panel's SMS page keeps its send result:** a slow send used to be killed with the request after 30 s
  (`sms_send - killed` in the operations log). The send now runs in the background and the page follows it, also
  after a reload. [Troubleshooting](Troubleshooting)
* **No more `mmc1` register dumps on the F50** on 6.18 and 7.2: the empty SD slot has real card detect now (the EIC
  controller's six banks are mapped), so nothing polls it any more (issue #181). [Troubleshooting](Troubleshooting), [Kernels](Kernels)
* **Magisk zips: known passwords.** A system the zip installs gets the images' own: Ubuntu's user `ubuntu` has the
  password `ubuntu`, OpenWrt's `root` has none until you set one; `MU300_PASSWORD` still sets one of your own. No
  random passwords any more, and the password note holds no secret (issue #167). [Installation](Installation#installing-from-android-with-a-magisk-zip-no-computer)
* **No VPN section without the VPN module:** LuCI's VPN menu, the Cockpit page's VPN tab and the toolkit's VPN item
  are there only once `mu300-extra install vpn` has run. [VPN](VPN)
* **Kernels:** 5.4 unchanged; 6.18.56 and 7.2.10 rebuilt with the card-detect fix. [Kernels](Kernels)

## New in v2026.10.21

The release of 2026-10-21 (a prerelease first, promoted once a board has run it). Mostly fixes. What it changes, and
where to read on:

* **F50: the internal SIM can be selected** instead of the card in the slot: `mu300-sim select internal` (or the
  **SIM source** field of the wan interface in LuCI), with that card's own APN. It takes effect at the next boot, the
  external card stays the default, and nothing is written to the modem or the SIM. [docs/SIM-SELECTION.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/SIM-SELECTION.md)
* **Kernel 5.4 no longer panics when a network namespace goes away** (Docker containers, sandboxes; issue #103). [Troubleshooting](Troubleshooting)
* **The Wi-Fi driver can be unloaded** on 6.18 and 7.2: `rmmod sprd_wlan_combo` used to crash the device (issue #146). [Troubleshooting](Troubleshooting)
* **Quieter logs:** the Wi-Fi driver's routine lines (`sc2355_dump_survey`, `bssid not found`, `data len is`...) are
  no longer errors in `dmesg` and `logread`, on every kernel (issue #140). [Troubleshooting](Troubleshooting)
* **`mu300-update` runs one at a time:** a second run started meanwhile is refused and names the first one's pid. [Updating](Updating)
* **Kernels:** 5.4 rebuilt with the two fixes above; 6.18.56 and 7.2.10 with rebuilt modules. [Kernels](Kernels)

## New in v2026.10.20

The release of 2026-10-20 (a prerelease first, promoted once a board has run it). What it adds, and where to read on:

* **Updates keep the packages you installed** (`apk add`, `apt install`): they come back at the first boot, with their
  settings and their services' state; the panel shows it under System > Packages after an update. [Updating](Updating)
* **The control panel gets a VPN section** (Overview, Profiles, Subscriptions, Engines, Settings, Logs), **System >
  Terminal** (a root shell in the browser, LAN only), Wi-Fi clients with their speeds on the home page, the Power
  page's reasons in your language, and it no longer freezes when an AT command gets stuck: on OpenWrt an AT command
  now takes 0.13 s instead of a second. [Control Panel (LuCI)](Control-Panel-(LuCI))
* **A web panel for Ubuntu:** Cockpit with an MU300 page, as the `panel` extra. [Control Panel (LuCI)](Control-Panel-(LuCI)#a-web-panel-on-ubuntu-cockpit)
* **VPN module v2.0.0:** a core plus engine extras (`vpn-xray`, `vpn-singbox`, `vpn-mihomo`, `vpn-dae`), subscriptions,
  and an IPv6 guard so nothing leaks beside a tunnel that carries no IPv6. [VPN](VPN)
* **Kernels 6.18.56 and 7.2.10**, with BTF and the eBPF hooks dae needs. [Kernels](Kernels)
* **U30 Air:** a charge limit of 0-100 % (`mu300-power charge-limit N`), and faster charging on Type-C ports that offer
  1.5 or 3 A (mainline kernels). [Hardware Notes](Hardware-Notes)
* **The hotspot never lands on a DFS channel** any more (it used to fail to start there). [Wi-Fi Hotspot and Client](Wi-Fi-Hotspot-and-Client)
* **adb to Linux over USB**, on by default and open to every computer: anyone with a cable gets a root shell without a password. Close it with `sudo mu300-usb adb auth keys` (only authorised keys) or `sudo mu300-usb adb off`. [Installation](Installation#adb-to-linux)
* **The installers copy the boot chain** of Android's slot to the Linux slot after an Android update (issue #86). [Installation](Installation#the-boot-chain-of-the-linux-slot)
* **Hardening:** the Android modem daemons run under their own account (`mu300vnd`) and nothing in `/dev` belongs to
  the login user any more; `sms delete` sends only a message index to the modem. [Hardware Notes](Hardware-Notes), [SMS](SMS)

**You lose:** nothing that was there. Android on these boxes has no screen, no speaker and no HDMI either; all Linux
takes is your last excuse for not having a 5G Linux server in your pocket. Installing it costs you nothing; not
trying it costs you the server.

* **Android stays.** Only one of the two runs at a time, and a reboot switches between them.
* **The hardware stays what it is,** under either system: no screen output (HDMI over USB-C does not work), no
  speaker or microphone (voice calls are not supported), about 1.4 GB
  of the 2 GB RAM usable, because the modem keeps the rest.

## A hobby project, at your own risk

This is an experimental hobby project. It is not a product, it has no support line and it comes with **no
warranty of any kind**. Rooting and unlocking the device are **not** part of this project and are done at your own
risk. Writing to a phone-class device can leave it unusable; keep a backup and do not install on a device you cannot
afford to lose. Nothing here is endorsed by ZTE, Unisoc or any operator. Read the
[full notice in the README](https://github.com/dikeckaan/mu300-linux#a-hobby-project-at-your-own-risk).

## Links

* Source code and README: https://github.com/dikeckaan/mu300-linux
* Releases (prebuilt images): https://github.com/dikeckaan/mu300-linux/releases
* VPN module: https://github.com/dikeckaan/mu300-linux-vpn
* Deep technical notes: [docs/FINDINGS.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)
* Issues: https://github.com/dikeckaan/mu300-linux/issues
