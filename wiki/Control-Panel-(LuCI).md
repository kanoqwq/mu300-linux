# Control Panel (LuCI)

**In one sentence:** on OpenWrt you manage the device from a web page; the `openwrt-luci` system adds the **MU300
control panel**, a dashboard for the 5G modem, SIM, SMS and battery.

**The simple version:** LuCI is the router settings page you may know from home routers. The MU300 panel is an extra
set of pages made for this device: how strong the signal is, which bands it uses, your text messages, the battery.

## Open it

In a browser on a computer or phone connected to the device (USB or its Wi-Fi):

* F50 / MU300: **http://192.168.77.1**
* U30 Air: **http://192.168.78.1**

Log in as `root` with the password you chose at install.

## Plain OpenWrt vs. the control panel

| | OpenWrt (`openwrt`) | OpenWrt with the panel (`openwrt-luci`) |
|---|---|---|
| LuCI (network, wireless, firewall, packages) | yes | yes |
| MU300 pages (dashboard, Cellular, VPN, Power, CPU, Languages, Packages, Terminal) | - | yes |
| Theme | LuCI's default | Aurora (Bootstrap stays installed) |
| IPv6 from the carrier | prefix extension | relayed (router advertisements and NAT66) |
| Magisk zip | yes | yes (since v2026.10.18) |

Pick it at install ("Which OpenWrt?" -> 2, or `MU300_OPENWRT=luci`), or switch an installed device with
`sudo mu300-os openwrt-luci` and reboot (the system must be installed).

## The panel's pages

* **Status dashboard:** live radio readings (signal, bands, cells, temperatures), the operator's name, the mobile
  data state and the current download and upload speed, every second; the Wi-Fi clients with their speeds, connected
  time, name and address; the carrier's DNS servers and the number of connections; on the U30
  Air also the battery: level, whether it charges, and the power in watts. While the modem is busy the page keeps its
  last readings and says how old they are. The home page also has **Switch to
  Android** and **Lock Linux** (see [Going Back to Android](Going-Back-to-Android)).
* **Cellular > Network locks:** network mode, band, cell and EN-DC locks that persist across reboots and are replayed
  at boot. **Reset all to automatic** clears them. Locks live in the modem, so they also apply in Android.
* **Cellular > SMS:** read, send and delete messages (see [SMS](SMS) for the one rule to remember).
* **Cellular > SMS forwarding:** pass incoming messages on to a webhook, a Telegram chat or another phone (see
  [SMS](SMS#sms-forwarding)).
* **Cellular > Data usage:** mobile data per day and per billing cycle, with a reset day and a monthly cap
  (`mu300-traffic`, see [Mobile Data and APN](Mobile-Data-and-APN#data-usage)).
* **Cellular > AT terminal:** guarded AT commands over the same channel the system uses. A command that scans, changes
  the radio, the APN or the SIM, or writes a setting that persists into Android, is explained first and sent only
  after a click; a network scan runs in the background and the page follows it.
* **VPN** (a menu of its own, with the [VPN](VPN) module v2): **Overview** (state, active profile, engine, tunnel,
  uptime, live rates, the exit IP behind a button, Start/Stop/Restart, the kill switch and IPv6 behind a
  confirmation), **Profiles** (add from a share link, pasted text, a file or a URL; test the latency; rename, show and
  edit the config, delete), **Subscriptions** (a URL that yields profiles, refreshed by hand or every 6 to 168 hours),
  **Engines** (Xray, sing-box, mihomo, dae: install, update, remove; online or from an uploaded tarball checked against
  the module's release), **Settings** (kill switch, IPv6, LAN ranges that bypass the tunnel, Tailscale, DNS, the
  per-profile options) and **Logs**. Without the module every page offers to install it.
* **Cellular > TTL:** a fixed TTL for mobile data (`mu300-ttl`).
* **Cellular > Device management:** USB role (device or host), the USB network mode (NCM, ECM or RNDIS, applied at the
  next boot), and host-mode adapters that can join the LAN bridge.
* **Cellular > Adapter settings:** how the panel reaches the modem (AT backend, serial port, custom adapter).
* **System > Update:** update the device from the browser: what is installed, the newest release on GitHub with its
  download size, an update that keeps running when the page is closed, an update from an uploaded release file for a
  device without internet, going back to the previous version, the kernel choice (see
  [Updating](Updating#from-the-browser-openwrt-with-the-control-panel)). The home page says when a newer release is
  out.
* **System > Backup of settings:** your settings as one file (with a passphrase it is encrypted) and back: the
  hotspot, mobile data and the APN, the network locks, the VPN profiles and settings, SMS forwarding, the LAN address,
  host name, time zone, SSH keys and, if you ask, the login password. **Show what would change** checks the file
  first; **Restore** applies only what differs (see [Updating](Updating#backing-up-your-settings)).
* **System > Languages:** interface language, and the lang extra.
* **System > Power:** the active power profile and why (in your language), battery, power source and the charger's
  input current, the idle settings of the three profiles, the saver threshold and the charge limit (0-100 %, U30 Air).
* **System > CPU:** the CPU profile: saving, balanced (the default) or performance (`mu300-cpu`). It never touches
  the voltage, and the thermal protection stays in charge in every profile. Below it, read only, the kernel's thermal
  zones: temperatures, trip points and the cooling devices bound to them with their current state (on the mainline
  kernels the stock `soc-thmzone`, 70/85 °C passive and 110 °C critical, throttles the three clusters at 85 °C).
* **System > Packages after an update:** the packages you had installed yourself and what the update did with them
  (see [Updating](Updating#packages-you-installed-yourself)).
* **System > Terminal:** a root shell in the browser ([ttyd](https://github.com/tsl0922/ttyd)). It listens on the LAN
  address only (port 7681; the cellular side never reaches it) and opens a shell only for a one-time token the page
  gets for your logged-in panel session, so there is no second password to guess. It closes after 15 minutes without
  activity (`uci set ttyd.mu300.idle=<seconds>`; 0 never). A panel opened over https opens it in a tab of its own.
  **Terminal settings** is ttyd's own page; `enable 0` there removes the terminal.

Since v2026.10.20 one stuck AT command no longer takes the panel down: every modem read has a deadline, the pages
send their calls one at a time and back off after failures, and the Device page keeps a log of the panel's recent
modem operations (names and timings only). On OpenWrt an AT command takes about 0.13 s instead of a full second.

## A web panel on Ubuntu (Cockpit)

Ubuntu has no LuCI, but it can have [Cockpit](https://cockpit-project.org/), Ubuntu's own web console (terminal, logs,
services, networking, storage, accounts), with an **MU300** page whose tabs follow the OpenWrt panel's pages:
Overview (signal, mobile data, device, data usage, hotspot, VPN, live rates, reconnect / restart), Mobile network
(APN, data roaming, SIM choice and dual SIM, radio per SIM, VoLTE/VoNR, TTL, USSD), SMS, AT terminal, Data usage
(billing cycle, monthly cap, history), Wi-Fi (the hotspot's settings and clients, joining a network), VPN (on/off,
kill switch, exit address, profiles, subscriptions, engines, IPv6, log), Power and CPU (power profiles, charge limit,
LEDs, zram, CPU profile, the experimental CPU voltage offset), System (host name, time zone, password, SSH, LAN
address, USB and adb, boot, Docker, logs), Updates (check and install, kernel, mirror, extras, your packages) and
Backup (the settings as one file, and back). It uses `mu300-api`, the same backend as the web installer's device
manager. Network, band and cell locks and SMS forwarding stay features of the OpenWrt panel.

```sh
sudo mu300-extra install panel                                           # Cockpit from Ubuntu's archive (about 12 MB)
sudo mu300-extra install panel --from mu300-extra-panel-offline.tar.gz   # offline: the release's file, Cockpit inside
sudo mu300-extra remove panel
```

The installer asks about it too (default no). Then open `https://192.168.77.1:9090` (U30 Air: `.78.1`), accept
Cockpit's own certificate and log in as `ubuntu`, then turn on administrative access (the "Limited access" button at the top): the MU300 page
needs it for nearly everything.
Cockpit listens on the LAN only (USB and the hotspot), the firewall closes it to the cellular side anyway, root cannot
log in, and a session ends after 15 minutes idle. In English, Turkish and Chinese.

## Languages

Both OpenWrt systems speak **English, Turkish and Simplified Chinese** out of the box. The interface starts in
English: switch under System -> System -> Language and Style, or on `openwrt-luci` under System -> Languages. An update
keeps your choice.

The **lang extra** (about 2 MB) adds LuCI in 40 more languages and the panel in 29 of them:

```sh
mu300-extra install lang                                               # from the release of this system
MU300_EXTRA_FILE=/tmp/mu300-extra-lang.tar.gz mu300-extra install lang  # from a file, offline
mu300-extra lang                                                       # which languages are offered
mu300-extra remove lang
```

The panel's translations other than Turkish and Chinese are machine (AI) translations; corrections are welcome (see
[Contributing](Contributing)).

## Credits

The panel is the work of kanoqwq ([kanoqwq/mu300-linux](https://github.com/kanoqwq/mu300-linux), branch
`clean-tf-7.2`), ported here with the translations rewritten as standard LuCI catalogs. The theme is
[Aurora](https://github.com/eamonxg/luci-theme-aurora) by eamonxg. The app's own notes:
[openwrt/luci-app-mu300/README.md](https://github.com/dikeckaan/mu300-linux/blob/main/openwrt/luci-app-mu300/README.md).

## OpenWrt reminders

* Never `sysupgrade` or flash OpenWrt firmware images.
* `apk upgrade` is fine, except the `kernel` and `kmod-*` packages.
* On kernel 5.4, a firewall change saved in LuCI may need `nft delete flowtable inet fw4 ft; fw4 reload` (or a reboot)
  on older releases; v2026.10.17 fixed `fw4 reload`/`restart` with flow offloading on 5.4.
