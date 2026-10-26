# Device Manager

**In one sentence:** with the device running MU300 Linux and plugged into a computer, open the
[web installer](https://kaandikec.com/mu300-linux/web-installer/) in Chrome or Edge, click **Connect**, and you can
change the device's Wi-Fi, mobile data, VPN, SMS, LAN and system settings from the browser.

**The simple version:** the page is a remote control that talks to the device over the USB cable. The device keeps
the buttons' rules itself: it checks everything the page asks for, and it never hands a password back.

> **Not tested on a device yet.** The manager is new (FINDINGS 110): its device side and the page are covered by
> tests, but nobody has run it on an F50 or a U30 Air so far. If something looks wrong, save the log (the page can)
> and [tell us](https://github.com/dikeckaan/mu300-linux/issues). LuCI (OpenWrt) and the Cockpit panel (Ubuntu) keep
> working as before.

## What you need

* A device that runs MU300 Linux from a release with the manager. On an older release the page shows a small view
  (updates, kernel, restart) and says to update: **Update now** there, restart, connect again.
* adb on, which it is by default ([Installation](Installation): "adb to Linux").
* Chrome or Edge on a computer (WebUSB). No adb server running on the computer (`adb kill-server`).

## What is in it

| Tab | What you can do |
|---|---|
| Overview | system, release, kernel, uptime, signal and operator, data used this cycle, battery (U30 Air), temperatures, storage |
| Wi-Fi | the hotspot's name, password, band and channel, hide the name, on/off, the connected devices; join another Wi-Fi network as the internet connection |
| Mobile data | on/off, APN with user name and password, IP type, which SIM, band and cell locks (OpenWrt with the control panel) |
| VPN | install the VPN module; profiles (from a share link, a pasted config, a file or an https address), use, rename, remove; on/off, kill switch, IPv6, subscriptions, engines, the exit address, the log |
| SMS | read, send and delete messages; the forwarding summary (OpenWrt with the control panel) |
| USB and LAN | the LAN address, the U30 Air's USB host mode, whom adb lets in |
| System | device name, time zone, the login password, SSH, restart, what starts next, updates, kernel, extras, logs, a diagnostics file |

## Good to know

* **Passwords go one way.** The hotspot's, the APN's and the login password, VPN links and subscription addresses are
  sent to the device and never shown again; a password field starts empty and means "unchanged". Tick **show** to see
  what you are typing.
* **The page tells you before it cuts its own connection.** Making the U30 Air's USB port a host, changing whom adb
  lets in, and a restart end the connection; the page says how to get back first. Changing the LAN address, the
  hotspot or mobile data does not touch the USB connection.
* **Long work runs on the device.** An update, a kernel switch, installing an extra or the VPN module, joining a Wi-Fi
  network: closing the page or pulling the cable does not stop it. Connect again and the page shows where it is.
* **adb "Keys only"** adds this browser's key first, so the page keeps working. If you later clear the site's data in
  the browser, the key is gone: run `sudo mu300-usb adb auth open` over SSH to get back in.
* **WPA3** is not offered: it is disabled on this Wi-Fi chip for security, the hotspot is WPA2-PSK only. A hotspot
  still set to WPA3 is switched to WPA2 with its password when you save ([Wi-Fi Hotspot and Client](Wi-Fi-Hotspot-and-Client)).

For programs: the device side is `mu300-api`, documented in
[docs/ADB-INTERFACE.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/ADB-INTERFACE.md).
