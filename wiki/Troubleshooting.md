# Troubleshooting

**The simple version:** most problems end the same safe way: the device goes back to Android, and you try again. Below
are the known problems, what causes them and what to do. If yours is not here, collect logs and
[open an issue](https://github.com/dikeckaan/mu300-linux/issues).

## Before anything else

* Wait **two minutes** after power-on; the first boot is slow.
* Use a **good USB cable** plugged straight into the computer.
* Make sure you use the **right address**: `192.168.77.1` (F50) or `192.168.78.1` (U30 Air).
* The USB serial console works even when networking does not: `screen /dev/cu.usbmodem* 115200` on macOS.
* After a fallback to Android, collect logs from a computer with `tools/collect-logs.sh`.

## The device does not come back after installing

Wait two minutes. If there is still nothing, unplug and replug it. Every boot that does not finish counts, and after 5
of them in a row (or the number you chose) the device starts Android. Then run `tools/collect-logs.sh` and open an
issue with the result.

## Back in Android right after installing, and the bootloader is locked

The installer warned "The bootloader is locked. Did you patch TrustOS?". A locked bootloader checks the boot image
it starts: without a patched TrustOS it rejects the Linux image (on the serial console the bootloader says
`verify_ret:0x3`, issue #134), counts that as a failed boot, and after the failed boots the device starts Android
again. Nothing is broken, and nothing of Android was changed. Linux needs a device that boots a modified boot
image: an unlocked bootloader, or a locked one with a patched TrustOS. This project does not root, unlock or patch
devices and gives no guide for it.

## Back in Android without asking, on kernel 6.18

About one boot in 20 on one board hangs in its first seconds, and the device then falls back to Android (this can take
around 20 minutes). Start Linux again with `su -c mu300-linux` or the Magisk module's Action button. If it keeps
happening, try 7.2, 7.3 or 5.4: `sudo mu300-update kernel 7.2`.
[FINDINGS 31m](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md#31m-hard-hangs-in-the-first-seconds-under-618-open)

## Back in Android after an update

With older boot images a single crash or reset of Linux sent the device back to Android. Start Linux again with
`su -c mu300-linux`, then update (see [Updating](Updating#coming-from-a-very-old-release)). If Linux does not start any
more, run `./install.sh` from a computer and choose **update**: it writes a fresh boot image and keeps your data.

## The computer sees the device but gets no address (macOS)

macOS does not set up a network interface it has never seen while the screen is locked. Each device has its own USB
MAC address, so the first time one is plugged in (or after an update that brought new addresses) **unlock the Mac** and
the interface appears. With an F50 and a U30 Air plugged in together, they are `192.168.77.1` and `192.168.78.1`; two of
the same kind need `/etc/mu300/lan.conf` to tell them apart.

## No internet

Check that the SIM has a **data plan**, then run `sudo mobile-data status`. A missing plan looks like a connection that
keeps dropping. Leave the APN empty unless your carrier needs one ([Mobile Data and APN](Mobile-Data-and-APN)).

## OpenWrt on kernel 5.4: no address, no web page, nothing joins the hotspot

Releases before v2026.10.13: the firewall's boot ruleset had no rules for the LAN, and on 5.4 the reload that should have
added them always failed. **Fixed from v2026.10.13 on**; update. On an affected release, log in over the USB
serial console (`ttyGS0`, a root shell) and run:

```sh
nft delete flowtable inet fw4 ft; fw4 reload
```

[FINDINGS 38c](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## The installer prints nothing for minutes and the device gets hot

A read past the end of the internal storage never returned on devices whose partition table ends at the end of the disk
(the 32 GB variant, and an F50 with no gap behind `userdata`). **Fixed from v2026.10.13 on.** Reboot the device
to end the stuck reads, then install with the current installer (it updates itself from GitHub when it starts).
[FINDINGS 38a](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## `--check` reports no free space

You probably have the **32 GB variant**. Use an SD card (F50), or let the installer shrink `userdata` (experimental,
erases Android's data). See [Installation](Installation#the-32-gb-variant). If the numbers look like neither a 64 GB nor
a 32 GB device, open an issue with the `--check` output.

## `--check` says the free space is "not empty"

Something has written into the unpartitioned area. Installing would overwrite it, and the installer makes you type
`overwrite`. If you are not sure what is there, stop and ask in an issue.

## I forgot the password

You do not need to log in to get out (unless Linux is [locked](Going-Back-to-Android#locked-linux)):

1. **Back to Android:** unplug the device about ten seconds after it powers on, then plug it in again, as many times in
   a row as your failed-boot count (5 by default). The device then falls back to Android.
2. **Set a new password** from your computer, without booting Linux:
   ```sh
   tools/reset-password.sh            # ubuntu, openwrt or both
   ```
   It mounts the Linux filesystem from Android and rewrites the password, keeping all your data.
3. Start Linux again with `su -c mu300-linux` (or `boot/android-boot-linux.sh work/boot-linux-slotb.img`), or reboot if
   Linux is the default.

Re-running `./install.sh` and choosing **update** also keeps your data and sets the password you type.

## The hotspot went from WPA3 to WPA2, or does not come up after choosing WPA3

WPA3 (SAE) on the hotspot is disabled on this Wi-Fi chip, on every device: the U30 Air's firmware WPA3 could let a
client in without the password ([U30AIR-SAE.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/U30AIR-SAE.md)).

* A hotspot that was WPA3 was switched to **WPA2 with the same password** at boot; LuCI shows a notice. Join again
  with the same password. The old settings are in `/etc/mu300/wireless.before-wpa2`.
* The notice says the hotspot is **off**: it had no password WPA2 can use (8-63 characters). Set one in LuCI →
  Network → Wireless, choose WPA2-PSK and enable the network (over USB or SSH if the hotspot was your way in).
* WPA3 or WPA2/WPA3 mixed chosen in LuCI: that access point is not started (`logread | grep wifi-scripts`). Choose
  WPA2-PSK.

## Joining a Wi-Fi network fails

* WPA3-only networks cannot be joined (the driver has no SAE). Switch the router to WPA2/WPA3 mixed mode.
* While the hotspot is on, `wifi-client connect` saves the network and asks for a reboot.
See [Wi-Fi Hotspot and Client](Wi-Fi-Hotspot-and-Client).

## Kernel warnings are not in `journalctl -k`

On Ubuntu they are in `journalctl -t kernel` (warnings and errors only, from the start of each boot). Everything else is
in `dmesg`. Repeating vendor messages are filtered out by `/etc/mu300/kmsg-ignore`.

## Websites think I am in another country

The device has no GPS; websites guess from the IP address, and mobile operators and VPN servers often look like another
city.

## Downloads are slow

GitHub's CDN throttles single connections in some regions. The installer already downloads in 8 parallel chunks; set
`MU300_FETCH_JOBS` to change that, or `MU300_RELEASE_URL` to use your own mirror.

## "could not arm the Linux slot for a trial" during an update

Harmless when updating from v2026.10.14 or older: the older `mu300-next-boot` does not know `--trial`. The fallback to
Android still works.

## The U30 Air battery drains while plugged in

The mainline kernels (6.18, 7.2) of v2026.10.11 and older leave charging off. Update (charging on mainline comes from
v2026.10.12 on), or use kernel 5.4.

## The hotspot does not start on a 5 GHz channel between 52 and 144

hostapd stops with `start_dfs_cac() failed` / `AP-DISABLED` (releases up to v2026.10.18). Those are DFS channels, and
the Wi-Fi driver cannot detect radar. **Fixed from v2026.10.20 on:** such a channel is replaced by 36 (or 149) with
a line in the log. On an older release choose 36-48 or 149-165, or `auto` on Ubuntu.
[FINDINGS 42](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## The control panel went blank after an AT command got stuck

On `openwrt-luci` up to v2026.10.18 one stuck AT command could blank every page, even the battery tile, for minutes,
also after a reboot. The panel's lock wait counted whole seconds instead of tenths (OpenWrt's `sleep` takes no
fractions), and some pages read the modem inside the request. **Fixed from v2026.10.20 on**; there the pages keep
their last readings and say how old they are. On an older release wait it out, or restart `rpcd` and `mu300-atd`.
[FINDINGS 40](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## Sending an SMS fails with `SMS FAILED: CODE:600`, or the panel's operations log says `sms_send - killed`

Up to v2026.10.21, on some networks (China Broadnet, issue #176) the modem's IMS stack refused every submission
(`+SPIMSREASON: 1,512,"SMS FAILED: CODE:600 REASON:Busy Everywhere"` in the URC log) while incoming messages
arrived; and on `openwrt-luci` a send that took longer than 30 s was killed together with the panel's request, leaving
`sms_send - killed` in the operations log and no word on whether the message went out. **Fixed from v2026.10.22 on**:
a send is one AT transaction (the channel is held from the setup to the submission, so no other client gets in
between) and the SMSC the modem reports is written back to it unchanged before each one, in `mu300-sms` and the panel
on OpenWrt and in `sms` on Ubuntu; the panel starts the send in the background and follows it, also after a reload.
On an older release write the SMSC back yourself (`mu300-at 'AT+CSCA?'`, then `mu300-at 'AT+CSCA="<the value it
showed>",<the type>'`, with `sudo` on Ubuntu) and send from the shell while the panel is idle (`mu300-sms send NUMBER
TEXT` on OpenWrt, `sudo sms send NUMBER TEXT` on Ubuntu).
[FINDINGS 46](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## The device restarts on kernel 5.4 when a container stops

Up to v2026.10.20, on 5.4, when two network namespaces were alive at once and one went away (Docker containers, a
sandbox, `unshare -n`), the kernel could panic while cleaning it up (`xfrm_sysctl_fini` in the trace; issue #103); the
device restarts, and after a few of those it falls back to Android. **Fixed from v2026.10.21 on**; update. On an older
release keep to one container at a time, or use 6.18 or 7.2, which never had the bug.
[FINDINGS 44](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## The device crashes when the Wi-Fi driver is unloaded (6.18, 7.2)

Up to v2026.10.20, `rmmod sprd_wlan_combo` on 6.18 or 7.2 panicked within a second (`__run_timers`: the driver left a
timer running in freed memory; issue #146). **Fixed from v2026.10.21 on.** On an older release do not unload the
module; restart the device instead. [FINDINGS 45](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## The log is full of `sprd-wlan` errors every few seconds

Lines such as `sc2355_dump_survey, idx 0`, `bssid not found, multicast?` or `data len is 251, skb need 576` at error
level, every six seconds or so in `dmesg` and `logread` (releases up to v2026.10.20). They describe the driver's
routine work, not faults (issue #140). **From v2026.10.21 on** they are debug lines, shown only when you ask for them:
`echo 'module sprd_wlan_combo +p' > /sys/kernel/debug/dynamic_debug/control`. The real errors next to them are still
logged.

## The log is full of `mmc1` register dumps on the F50 (6.18, 7.2)

`mmc1: Got command interrupt 0x00000001 even though no command operation was in progress`, followed by a 16-line
`SDHCI REGISTER DUMP`, every few seconds to minutes in `dmesg -l err` on an F50 with nothing in the SD slot, on 6.18
and 7.2 up to v2026.10.21 (issue #181). Mainline's EIC driver mapped only three of the controller's six banks, so the
slot's card-detect line could not be found and the slot was polled instead: every second a card that is not there was
initialised, and the host's late answer to a command nothing replied to was logged with the dump. **Fixed from
v2026.10.22 on**: the six banks are mapped, the slot has real card detect (an interrupt), and an empty slot is left
alone until a card goes in. The dumps are noise, not a fault; on an older release put a card in the slot (with a card
there are none) or use 5.4, which never had them. The U30 Air has no slot.
[FINDINGS 31j](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md)

## The U30 Air charges slowly on a strong USB-C port

The mainline kernels of v2026.10.19 and older took 500 mA from every port. From v2026.10.20 on they read the current
the port offers (1.5 or 3 A) and charge at up to 2 A. On 5.4 the vendor stack decides.

## The U30 Air stays dark after starting from a charger

A boot that started because a charger was plugged in stays a **charging boot** (LED blinking, hotspot and modem down)
until you press the **Wi-Fi key**.

## Things that are not bugs

* **No screen output** over USB-C and **no sound**: the hardware path is not there ([Hardware Notes](Hardware-Notes)).
* **A load average around 12 on 5.4:** vendor kernel threads waiting in D state are counted; the CPU is mostly idle.
* **About 1.4 GB of RAM:** the modem firmware reserves the rest.

## Never

* Never use OpenWrt's `sysupgrade` or flash OpenWrt firmware images.
* Never post your backup folder or logs containing your IMEI publicly.
