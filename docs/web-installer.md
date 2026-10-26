# The web installer: how it is built, how it was tested, and what is still open

`site/web-installer/` (https://kaandikec.com/mu300-linux/web-installer/, in en/tr/zh) installs, manages and removes
MU300 Linux from Chrome or Edge over USB. It was run from start to finish on an F50 on 2026-10-13 (below); the
measurements behind its design and what that run found are FINDINGS 72. This file says what runs where, how to test
it without a device and with one, and what the owner has to set up for the one part that is built but switched off
(the release mirror).

## What runs where

```
 browser (static page, no server)                    device, rooted Android
 ----------------------------------                  ---------------------------------------------
 app.js       the screen, strings.js (en/tr/zh)
 flow.js      what fits, what is refused, answers
 github.js    release list from api.github.com
 mirror.js    a release zip from the mirror (config.js), in parts
 zip.js       reads a picked zip's manifest
 device.js    AndroidDevice / LinuxDevice   --adb--> /data/local/tmp/mu300-web/android-web.sh   (pushed by the page)
 adb/*        adb over WebUSB: auth, shell v2, sync      |  under su, Magisk's busybox, ASH_STANDALONE=1
 privacy.js   masks every line a device prints           v
                                                     /data/adb/mu300-web/   (root only, mode 700)
                                                        dl/<zip>     what the device downloads itself
                                                        zips/<zip>   checked against the release's sha256
                                                        x/<zip>/mu300/install.sh   the zip's own installer
                                                        answers.conf   MU300_ANSWERS (root's, mode 600)
                                                        out/         the copies of a backup
                                                        job/{log,rc,pid,more}   the run, in a session of its own
```

* **Where root writes, and how much.** `/data/local/tmp/mu300-web` is the adb user's: the page pushes its files
  there, and whoever can write there could put a link where a file is expected. Root creates, writes, chmods and
  deletes nothing in it. A pushed file is moved (a rename follows no link) into `/data/adb/mu300-web` and judged
  there: a regular file, of a bounded size, with the checksum asked for. A download, the copies of a backup, the
  answers, the logs and the job's state are in root's directory only; the page reads a backup's copies as base64
  from `android-web.sh cat NAME` (the adb user cannot read that directory), and removes what it pushed itself,
  without root. `/data` is Android's own, so everything root writes there has an end set before it starts: a
  download is refused when it would leave less than 512 MiB free (the reserve the Magisk installer insists on),
  cannot grow past the size the release names (the downloader's file size limit, and a watch) and ends after an
  hour; a zip larger than 300 MiB, or whose installer files say they unpack to more than 64 MiB, is refused; a
  job's log stops at 8 MiB while the job goes on. The device scripts themselves are run from the adb user's
  directory, checked by the page against the site's checksums before they are pushed: the adb user is the one who
  asks for root, so that is its own power and no more.

* **How the release gets onto the device.** The user chooses the system, the kernel and the release (the newest, or
  a newer prerelease when there is one); the page names the Magisk zip of that choice and its size and gets it there
  one of three ways, in this order:
  1. *The device downloads it* (`android-web.sh net` to ask whether it can, `run fetch NAME SIZE` to do it): busybox
     `wget` from this project's release, over the device's own connection, with progress, continued where it broke
     (`-c`), watched for a redirect to a host that is not GitHub's, and accepted only with the release's size and
     SHA-256. The default whenever the device reaches GitHub.
  2. *The browser downloads it* from the release mirror (`mirror.js`), checks it, and pushes it over adb: for a
     device that does not reach GitHub. See "The release mirror".
  3. *The user downloads it* and picks the file: the last way.

  The SHA-256 is the `digest` GitHub's API gives for the release's own asset, read by the browser; the device
  computes it over the file after root has moved it to `/data/adb/mu300-web`. That check carries all three ways:
  a file's integrity is by that pinned digest, never by the transport it came over (the device's downloader cannot
  verify a certificate, FINDINGS 72.2).
* **A locked bootloader is not an obstacle.** A device with a patched TrustOS starts the Linux boot image with the
  bootloader locked. The page shows "locked" in the warning colour with a question ("Did you patch TrustOS?"),
  beside the device's facts and again beside the plan, and blocks nothing; "unlocked" and "not reported" are as
  before. It never unlocks, patches, or links to a guide for either.
* **Install** is the Magisk zip's installer (`android/magisk/installer/mu300-install.sh`, in every zip as
  `mu300/install.sh`), started the way Magisk's `customize.sh` starts it, with `MU300_ANSWERS` (the wizard's choices)
  and `MU300_PROGRESS=1` (`MU300-STEP n/9 name`, `MU300-PLAN key=value`). The page runs it twice per zip: once with
  `MU300_DRY_RUN=1` to show the device's own plan, then for real after the user typed INSTALL. Two systems are two
  zips, one after the other, and only the page can send the second: the first run is started with `more`, so that a
  page that finds the job after the first one was closed says that the second system is missing. Nothing about
  installing is reimplemented in JavaScript.
* **Backup, uninstall, status, "start Linux now"** are `tools/android-web.sh` (uninstall in `uninstall.sh`'s order,
  with `tools/storage.sh` and the bc functions of `tools/android-boot-image.sh`; starting Linux is the Magisk
  module's `switch.sh`).
* **Managing Linux** is `LinuxDevice` in `device.js`: one adapter around "a command over adb shell, an answer back".
  It asks `mu300-device --json` first (format 1, `docs/ADB-INTERFACE.md`) and reads what that does not carry with
  commands every image has. The actions are `mu300-update check|apply|kernel`, `mu300-os`, `mu300-next-boot`,
  `reboot`. "Restart into Android" is `mu300-next-boot android`, which makes Android the default boot until
  `mu300-next-boot linux` (the page's "Make Linux the default again").
* **Connecting.** The first time, Chrome's chooser (`requestDevice`); afterwards the page offers the devices the
  browser already allowed (`getDevices`) as "Connect to ...", and connects by itself when such a device comes back
  after a restart the page asked for. In Android and in Linux the same unit is two USB devices: each is chosen once.
* **Not in the page, on purpose:** shrinking `userdata` on the 32 GB variant and growing it back (the page shows the
  figures and points to `install.sh` / `uninstall.sh`), rooting, unlocking, restoring single partitions from a
  backup (its README.txt has the two commands). The VPN module is still a file the user picks.

The device scripts the page pushes are this repository's (`tools/`, `android/magisk/`), copied next to the page with
their checksums by `site/tools/stage.sh`, which `.github/workflows/pages.yml` runs. Nothing else is built.

## The release mirror

A page cannot read a release's files (FINDINGS 72.2). `raw.githubusercontent.com` does answer a page, for files of a
repository of at most 100 MB each. So the mirror is another repository whose branch `<tag>` holds every Magisk zip
of that release in parts of at most 45 MiB and a `manifest.json` (`tools/make-release-mirror.sh`); the page puts a
zip together from its parts, checks every part and the whole against the release's own checksum, and pushes it over
adb. It is on (since 2026-10-13). What it consists of:

1. The public repository `dikeckaan/mu300-linux-mirror`.
2. In `dikeckaan/mu300-linux`: the **variable** `MIRROR_REPO` (`dikeckaan/mu300-linux-mirror`) and the **secret**
   `MIRROR_DEPLOY_KEY`, the private half of a deploy key of the mirror repository with write access (it can push
   there and nowhere else; there is no token). The last step of `.github/workflows/magisk.yml` publishes every
   release there over ssh with that key alone, to a host whose keys are the ones GitHub publishes
   (`api.github.com/meta`); it keeps the branches of the newest release and the newest prerelease, deletes older
   ones (`git ls-remote --heads`, `git push --delete`), and does nothing without the two. A release is one push of
   about 900 MB. For a release that exists: run the workflow with its tag.
3. `site/assets/web-installer/config.js`: `MIRROR_BASE`.
4. `https://raw.githubusercontent.com` in `connect-src` of the Content-Security-Policy of the three
   `site/*/web-installer/index.html`. `tests/test_web_installer.py` holds 3 and 4 together.

The order of the three ways stays: the device's own download when it reaches GitHub, the mirror when it does not,
a picked file last (and whenever the mirror has no copy of the release, which is the case until the workflow has
run for it). Limits to know: a repository should stay under 1 GB and one push under 2 GB;
`raw.githubusercontent.com` is not meant as a download service and GitHub may throttle it.

## Testing without a device

```sh
node --test tests/web/                       # the adb client, the logic, the device classes, the mirror (Node 20+)
python3 -m unittest discover -s tests -p 'test_web_installer.py'
python3 -m unittest discover -s tests -p 'test_android_web.py'      # needs busybox; runs the page's code end to end
```

* A real adbd, over TCP (the page's adb client, not its USB transport):
  `docker run --rm -d -p 127.0.0.1:15555:5555 ubuntu:26.04 sh -c 'apt-get update && apt-get install -y adbd && exec adbd'`,
  then `MU300_ADBD_TCP=127.0.0.1:15555 node --test tests/web/interop.test.mjs`. Never point it at a device.
* The page itself: `sh site/tools/stage.sh /tmp/mu300-site && python3 -m http.server -d /tmp/mu300-site 8300`, then
  `http://localhost:8300/web-installer/?demo=android` (also `installed`, `small` - a device without internet -,
  `linux`): the whole wizard with made-up answers and no USB device. `?dev` lets a zip that is not a release's file
  through (its checksum is then the file's own): for zips built with `tools/make-magisk-zips.sh` before a release
  exists; only a picked file is used then.

## Testing with a device, without a person

Both were used for the run of 2026-10-13. Stop the adb server first (`adb kill-server`): one program at a time can
hold the adb interface, and that includes a second browser window with the page.

* **The page's modules from Node**: the `usb` package's `WebUSB` object is a WebUSB implementation, and `adb/usb.js`
  takes its `USBDevice` as it is.

  ```js
  // npm install usb   (outside the repository; not a dependency of anything here)
  import { WebUSB } from "usb";
  import { AdbConnection } from "./site/assets/web-installer/adb/connection.js";
  import { generateKey } from "./site/assets/web-installer/adb/key.js";
  import { findAdbInterface, openUsbTransport } from "./site/assets/web-installer/adb/usb.js";
  import { AndroidDevice } from "./site/assets/web-installer/device.js";
  // pick the device by its serial number: other devices with adb may be on the same computer
  const device = (await new WebUSB({ allowAllDevices: true }).getDevices()).find((d) => d.serialNumber === SERIAL && findAdbInterface(d));
  const conn = new AdbConnection(await openUsbTransport(device), { key: await generateKey("node") });
  await conn.connect("node");
  const dev = new AndroidDevice(conn);          // then dev.root(), dev.stage(...), dev.status(true), ... as app.js does
  ```
* **The real page in Chrome** (Puppeteer, headless, a throwaway profile). Chrome's device chooser cannot be driven
  by a script: the DevTools protocol's `DeviceAccess` domain (Puppeteer's `waitForDevicePrompt`) raised no event for
  `navigator.usb.requestDevice` in Chrome 155, headful or headless, and a managed-policy grant
  (`WebUsbAllowDevicesForUrls`) is system-wide on macOS. What works: the permission a profile already holds. It is
  in `Default/Preferences` of the profile, under `profile.content_settings.exceptions.usb_chooser_data`:
  `{"http://localhost:8300,*": {"setting": {"chosen-objects": [{"name": ..., "vendor-id": ..., "product-id": ...,
  "serial-number": ...}]}}}`, one object per device (the unit in Android and the unit in Linux are two). With that
  written into a throwaway profile, `navigator.usb.getDevices()` returns the device and the page's own "Connect to
  ..." button connects with no chooser. Chrome's folder picker (the backup) is browser UI as well: with
  `window.showDirectoryPicker` removed before the page loads, the page uses its own fallback and keeps the backup in
  the page.
* **A device without an uplink** (the test F50's Android had none: its SIM registered but got no data bearer, and
  Android on an F50 has no Wi-Fi client). To measure the device's own download all the same, name lookups were
  pointed at the test computer (a bind mount over `/system/etc/hosts` in root's mount namespace, gone with a
  restart) where a TCP relay read only the server name of each TLS hello and connected to that host: TLS stayed end
  to end between busybox `wget` and GitHub. That measures the downloader, not a carrier.

## What was run on an F50 on 2026-10-13

F50 (64 GB, Android 13 `F50_FLYMODEM_ZYV1.0.0B09`, Magisk 30.6, bootloader unlocked), release v2026.10.25, a Mac
with Chrome 155. Every row at the Node level (N), in Chrome (C), or both; details and figures in FINDINGS 72. After the
device script's last changes (root's own directory, the bounds) the install from a pushed zip with the connection
dropped during "systems", Linux, the way back, the quick uninstall, the same install through the page in Chrome with
the tab closed, and a two-system install by the device's own download were run once more, with the same results.

| What | How | Result |
|---|---|---|
| Connect to Android with a key the device never saw | N, C | No AUTH packet at all: this Android's adbd lets every key in. Root granted by Magisk (Shell was allowed before) |
| Connect while an adb server runs / another browser window is connected | N, C | "Another program is using the device's adb interface", at once |
| Model, masked serial, slot, root, bootloader (unlocked), storage figures | N, C | As `./install.sh --check` gives them |
| Release list; the beta question | N, C | Asked while v2026.10.25 was a prerelease; not asked after it became the release |
| GitHub's API unreachable | C | "The release list could not be loaded. GitHub could not be reached." with Try again |
| A wrong zip (N, C), an older release's zip, a truncated zip picked (N) | N, C | Refused in the page, with its system and kernel named |
| A zip with one byte changed | N | Passes the page's look, refused by the device: `sha-mismatch`, nothing kept |
| Backup to the computer | N, C | 10 files, 78 MiB in 14 s; `boot_b.img` equal to `sha256sum /dev/block/by-name/boot_b`; no NV partition in it |
| Device download: probe, download, progress and speed | N, C | 41 MiB in about 4 s, 112 MiB in about 8 s (about 15 MiB/s, through the relay); checked on the device |
| Device download: connection cut, cancelled, wrong checksum, no connection | N, C | Continued where it broke; partial kept and continued; `sha-mismatch`; "no connection" and the file picker offered |
| Browser download from a mirror: a part cut short, then whole | C | "broke off before the file was complete", Try again, then pushed and planned (stand-in server) |
| Browser download from the real mirror (2026-10-15, v2026.10.27, Ubuntu 24.04 on 7.2, a device with no internet) | N | `net` said no-network, the mirror was proposed; 3 parts, 114 MiB in 32 s, checked, pushed and checked on the device in 7 s; plan status 3 |
| Plan, then INSTALL: OpenWrt with the panel on 7.2, a password, "only this browser" | N | 9 steps in 40 s; root's password set, adb in Linux takes the page's key only |
| The tab closed / the client ended during the installation | N, C | The job finished without a watcher; a new connection found it running or done and replayed its log |
| A second system added (Ubuntu 24.04), kept filesystem | N | Plan `format=0 update=1`, installed |
| Two systems in one go, fresh filesystem | C | Two downloads and two runs in 92 s |
| Restart into Linux; the page reconnects | C | By itself, 35 to 45 s later |
| Linux: status, update check, logs, kernel 7.2 -> 6.18 and restart, boot system | N, C (kernel: N) | Worked; logs with no MAC or identity number left |
| Restart into Android, Start Linux now, Make Linux the default again | N, C | Worked; "Restart into Android" leaves Android as the default boot |
| Uninstall (quick) | N, C | 7 steps in 5 s; `boot_b` a copy of `boot_a` |

Not run: Windows, Linux as the host, a U30 Air, an SD card as the target, KernelSU/APatch, an Android that is not
rooted (the page's "not rooted" and "not granted" texts: the second was produced by taking Shell's grant away for
100 s, the first is reasoned from the code), the device's download over a carrier's network, the real mirror host
from a browser (it was run from Node, 2026-10-15), the VPN module, the secure erase.

## The protocol, for the next device or platform

For a Windows computer, a Linux computer or a U30 Air: the same steps by hand. Every step lists what must appear.
**Stop at the first step that does not match and save the log** (the page's "Save the log"); nothing before step 9
writes to the device's storage.

| # | Do | Must appear | If not |
|---|---|---|---|
| 0 | `adb kill-server` in a terminal; no other window with the page. Open the page. | The "Connect the device" panel; no error in DevTools' console. | Note the console text. |
| 1 | Click **Connect**. | Chrome's chooser lists the device ("F50" in Android). | The page says why (`no-interface`: USB debugging off). On Windows: FINDINGS 72.3. |
| 2 | Pick the device, **Connect**. | On an F50: straight on to step 3 (its Android asks nothing). On a device with a screen: "Allow this browser on the device"; allow it there. | "Another program is using the device": an adb server, or a second window. |
| 3 | Wait. | Model, masked serial (click "show": it must be `adb devices`' serial), Android version, slot. **Root: asking**, then **granted (Magisk)**. Bootloader: unlocked / locked / not reported. | "not granted": allow Shell in Magisk (scrcpy on an F50), wait a few seconds, "Try again". |
| 4 | Read the overview. | The storage bar; "This device has no Linux yet" (or the installed systems). Open the log: only masked serials, no IMEI. | Compare the figures with `./install.sh --check`. |
| 5 | **Install Linux**, step Storage. | The verdict `./install.sh --check` gives. | - |
| 6 | Choices: a system, a kernel, defaults. | The release's tag; the beta question only when a newer prerelease exists; "What will be downloaded" with the file's name and size. | - |
| 7 | Files. | "The device can reach GitHub (mobile data)" and the device's own download chosen; or "no internet connection" and the file picker. Try a wrong zip once in the picker: refused with its system and kernel named. | - |
| 8 | Backup: choose a folder. | `mu300-backup-<date>/` with `boot_b.img`, `uboot_b.img`, `sml_b.img`, `teecfg_b.img`, `trustos_b.img`, `vbmeta_b.img`, `misc-head.bin`, `gpt-*.bin`, `partitions.txt`, `README.txt`. `shasum -a 256 boot_b.img` equals `adb shell su -c 'sha256sum /dev/block/by-name/boot_b'` (in another terminal: `adb kill-server` again before going on). | - |
| 9 | Next (Install step). | The download with its progress and speed (note them), then the device's plan in a table. **Nothing is written yet.** | A failure names its reason and offers "Get the files another way". |
| 10 | Type INSTALL, click Install, and when the list shows "Collecting the Wi-Fi and modem files", **close the tab**. Open the page again, **Connect to ...**. | The page shows the running (or finished) job with its log from the start. | If the job died with the tab: "interrupted". Nothing is armed: note it, run again. |
| 11 | Let it finish. | All nine steps done, "Linux is installed". In the log: `MU300-INSTALL-OK`, "Writing and verifying boot_b". | "It did not finish": the device still boots Android unless "Arming" was done. |
| 12 | **Restart into Linux.** | The device restarts; about 40 s later it is there as a new USB device. | Falls back to Android after 5 tries: collect logs (`tools/collect-logs.sh`). |
| 13 | **Connect** (the chooser once more: in Linux it is another device, "MU300 Linux"). | "Manage MU300 Linux". From then on the page reconnects by itself after a restart. | "Allow this browser": adb there takes authorised keys only; run the command the page shows over SSH. |
| 14 | Check for updates; Read the logs. | `mu300-update check`'s text; logs with MAC addresses masked. | - |
| 15 | **Restart into Android**, **Uninstall Linux**, quick, type UNINSTALL. | Seven steps, "Linux is removed". | - |

How to abort safely at any point: close the tab or click Cancel/Disconnect. Before step 9 nothing is on the device
but files in `/data/local/tmp/mu300-web` and `/data/adb/mu300-web` (`clean all` removes them; so does
`adb shell su -c 'rm -rf /data/adb/mu300-web /data/local/tmp/mu300-web'`). After "Install", do not unplug an F50 (it
has no battery); a closed tab is fine. If the page misbehaves in any way, the device side can always be finished or
inspected from a terminal: `adb shell su -c 'sh /data/local/tmp/mu300-web/android-web.sh status deep'` and
`... follow`.

## The device manager

A device in our Linux whose release has `mu300-api` (FINDINGS 110) gets the manager instead of the small manage view:

```
 browser                                               device, MU300 Linux (adb shell is root)
 manager.js   Manager (reads, actions, the job)   --adb--> /opt/mu300/bin/mu300-api METHOD   (request: JSON on stdin)
              renderManager (tabs, forms)                    lib/json.awk, the CLIs of /opt/mu300/bin, mu300-vpn,
 device.js    LinuxDevice.hello() / .api()                   on openwrt-luci the panel's rpcd backend for locks/SMS
```

`device.js` sends only methods of its own fixed list (`API_METHODS`, held to `mu300-api --methods` by
`tests/web/manager.test.mjs`) and that the device named in `hello`; the log gets the method and how it ended, never
the request. `?demo=linux` shows the manager with made-up answers, `?demo=linux-old` the fallback of a release without
mu300-api. Testing without a device: `python3 tests/test_api.py` (the device side, every shell) and
`node --test tests/web/manager.test.mjs` (the page's side against the real mu300-api).

## The device manager on a device

Run on 2026-10-16 on a U30 Air (openwrt-luci) and an F50 (Ubuntu) with the page's modules from Node over WebUSB:
FINDINGS 110 has what each row showed and what was fixed. On OpenWrt row 14 ends the page's connection (the gadget
is re-enumerated on every LAN ifup) and the page reconnects.

Run it for each system (Ubuntu, openwrt-luci; plain OpenWrt for the parts it has) on an F50 and on a
U30 Air, with a release that has mu300-api, from Chrome on a computer. Keep a terminal with SSH (or the serial
console) to the device for the checks in the third column, and save the page's log at the end of each run. Stop at
the first row that does not match.

| # | Do | Must appear / check on the device | Notes |
|---|---|---|---|
| 1 | Connect (the device in Linux). | "Device manager" with the system's name; in the log, one line per action naming the method and how it ended (`# mu300-api wifi.set: ok`), never what was sent. | An older release: "This release has no device manager yet" and the small view. |
| 2 | Overview. | System, release, kernel, uptime, memory, Linux storage, hottest sensor; operator/technology/registration/RSRP; mobile data state and address; data used; the hotspot; LAN address. U30 Air: battery. | Compare with `mu300-device --json`, `mu300-traffic`, `ip -4 addr show sipa_eth0`. |
| 3 | Wi-Fi: change the SSID to `Test "1" $(x)` and the password; save. | A confirmation that names the restart; a phone sees the new name and joins with the new password. Ubuntu: `sudo cat /etc/mu300/hotspot.conf`; OpenWrt: `uci show wireless` (key set, encryption psk2). The password is nowhere in the page's log. | Then 2.4 GHz with channel 11, then 5 GHz back: `iw dev` shows the channel. |
| 4 | Wi-Fi: hide the name; save. | The phone no longer lists it; joining by typing the name works. Ubuntu: `ignore_broadcast_ssid=1` in `/run/mu300-hostapd.conf`. | Unhide. |
| 5 | Turn the hotspot off, then on. | Off survives a restart (Ubuntu: `systemctl is-enabled mu300-hotspot` = disabled; OpenWrt: `wireless.radio0.disabled=1`). | |
| 6 | Wi-Fi client: Look for networks; join a WPA2 network; leave. | The job box follows `wifi-client connect`; the hotspot goes off while joined and comes back on leave; `wifi-client status`. | Close the tab during the join: reconnect shows the job. |
| 7 | Mobile data: off, then on. | The confirmation says the device goes offline; `ip addr show sipa_eth0` loses and regains the address; the page stays connected. | |
| 8 | APN: set `internet` with a user and password, PAP; save. Then remove the credentials. | Ubuntu: `/etc/mu300/mobile-data.conf` (0600) has the lines, mobile data restarts and comes back. OpenWrt: `uci show network.wan`. The password never in the page or its log. | A real private APN if one is at hand. |
| 9 | SIM: pick the other slot (U30 Air: two SIMs). | `mu300-sim status`; after a restart the other card. | F50: internal/external. |
| 10 | Locks (openwrt-luci): LTE bands `1,3`, then everything automatic. | LuCI's lock page shows the same; mobile data back within a minute. | Ubuntu: the "part of the control panel" note. |
| 11 | VPN without the module: Install the VPN module. | The job follows `mu300-extra install vpn`; then `mu300-vpn status`. | Needs the device online. |
| 12 | VPN: add a share link; rename; use; turn on; check the exit address; kill switch on, then off; IPv6 auto; remove. | `mu300-vpn profile list`; the exit address is the server's; with the kill switch on and the VPN off, a client has no internet. The link is nowhere in the page's log. | A subscription URL: add, refresh, remove. |
| 13 | SMS: send one to a phone you have; read the list; delete it. | The phone receives it; `sms list` (Ubuntu) or LuCI's SMS page (openwrt-luci). | Forwarding (openwrt-luci): the read-only summary matches LuCI. |
| 14 | USB and LAN: set `192.168.90.1`. | The page stays connected; the computer gets a 192.168.90.x address; SSH to 192.168.90.1. Set the default back. | |
| 15 | adb: Keys only. | The connection drops and comes back by itself; `mu300-usb adb status` says keys, one key. Then Open to every computer. | Clear the site's data in Chrome while in keys mode: the page is locked out; `sudo mu300-usb adb auth open` over SSH. |
| 16 | U30 Air: Make the port a host. | The page loses the device; a flash drive works; a restart brings the USB connection back. | |
| 17 | System: device name, time zone; login password (then log in over SSH with it); SSH off and on. | `hostname`, `date`, `ssh` refused while off. | OpenWrt without zoneinfo: a time zone is refused as "not available". |
| 18 | Check for updates; Update now (a release newer than the device's), close the tab half way, reconnect. | The job box shows the update from its start and its end; after a restart the new release. | Update and kernel jobs have no Cancel. |
| 19 | Kernel 7.2, then back. | `uname -r` after the restart. | |
| 20 | Extras: install `panel` (Ubuntu) or `lang` (OpenWrt), remove it. | `mu300-extra status`. | |
| 21 | Read the logs; Save a diagnostics file. | The file has no SSID, no MAC in full, no phone number, no password. | Attach it to the run's notes. |
| 22 | Make Android the default; restart. | Android starts; from Android, Start Linux again. | |
