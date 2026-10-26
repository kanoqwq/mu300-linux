# OpenWrt with the MU300 control panel as a third system (`openwrt-luci`)

Date: 2026-10-05. Status: decided without the user (asleep, work approved in advance); every decision below names
the alternatives that were turned down, so a review can overturn one without redoing the rest.

## Why

`kanoqwq/mu300-linux`, branch `clean-tf-7.2` (head `35a1c55`, 19 commits on top of our `1a69a41`), is a complete
OpenWrt for the F50 and U30 Air built around a LuCI application of its own (`luci-app-mu300`: a dashboard,
cellular locks, SMS, an AT terminal, adapter settings, USB device management), the Aurora theme, and a set of
changes to the scripts every one of our systems shares (mobile data, the AT daemons, USB, LEDs, Wi-Fi, SIPA). The
user asked for two things:

1. "Integrate kano's rootfs too, if we can rebuild it from source - offer it as an OPTION."
2. "Give the fixes to all rootfses, of course."

And, earlier: the fork's translations must be complete (no Chinese left in the Turkish or English UI), and the old
branch `luci-app-mu300` is outdated - the cleaned app (PR #22, `luci-app-mu300-standalone`) in its newer state
inside `clean-tf-7.2` is the source.

This is the third of the four pieces of work taken from that fork (the SD card install is on `sd-card-install`,
the Magisk installer on `magisk-installer`, the driver fixes come last).

## What the user gets

* A third system next to `/ubuntu` and `/openwrt` on the Linux disk: **`/openwrt-luci`**, "OpenWrt with the MU300
  control panel". Plain OpenWrt stays as it is; the panel is an option, not a replacement.
* The installers (`install.sh`, `install.ps1`) ask "Which OpenWrt?" whenever OpenWrt is chosen, the way they ask
  "Which Ubuntu?" today: 1) OpenWrt 25.12.5, 2) OpenWrt 25.12.5 with the MU300 control panel.
* A release asset of its own, `mu300-openwrt-luci-rootfs.tar.gz`, built by our scripts from our tree.
* `mu300-os openwrt-luci`, `mu300-update apply openwrt-luci`, rollback, clean, reset-password, uninstall: all of
  them know it.
* The panel in English, Turkish and Chinese - complete, with English as the source language, so a browser in any
  other language gets English, never Chinese.
* The fork's fixes to the shared scripts on all three systems (Ubuntu, OpenWrt, OpenWrt with the panel), ported
  one by one onto our current code, each with its test; the ones that are not fixes for us, or that would undo
  something we fixed, are left out with the reason written down (table below).

## Decisions

| # | decision | rejected alternatives |
|---|---|---|
| D1 | The system is called `openwrt-luci`; its directory is `/openwrt-luci`, its asset `mu300-openwrt-luci-rootfs.tar.gz`. | `kano` / `openwrt-kano` (a person's name in a path, and the panel is now ours to maintain too); `openwrt-tf` (the fork's name; TF is the card, which has nothing to do with this system); replacing `/openwrt` (the user asked for an option). |
| D2 | Its kind is OpenWrt: every script that treats systems by kind uses one rule, *a name that is `openwrt` or starts with `openwrt-` is OpenWrt-like*. The running system is found as the directory under the disk that is the same as `/` (`[ / -ef "$DISK/$n" ]`, the test `mu300-update` already uses as `is_root`), not from a marker file. | A marker file `/etc/mu300/system` (lives in `etc/mu300`, which updates carry over from the old system, so it would be overwritten by the old copy); hard-coding the third name everywhere (the Magisk installer and any later variant would need the same edits again). |
| D3 | Built by `openwrt/build-rootfs.sh` with `MU300_SYSTEM=openwrt-luci` (default `openwrt`): the same base tarball, packages and overlay as plain OpenWrt, then `openwrt/luci-overlay/` and `openwrt/luci-app-mu300/` on top. `MU300_SYSTEM=openwrt-luci` with `MU300_FLAVOUR=immortalwrt` is refused. | A separate build script (two copies of 200 lines that drift); building the app as an `.apk` with the OpenWrt SDK (a 1+ GiB SDK download per build for a package of text files; the app has nothing to compile); the ImmortalWrt combination (never tested by anyone). |
| D4 | The LuCI app is imported from `kanoqwq/clean-tf-7.2:openwrt/luci-app-mu300` as a snapshot commit that records the source commit, then changed in our tree. The compiled `lmo/*.lmo` files are not imported: `tools/po2lmo.py` (new, standard-library Python) builds them from `po/` during the image build. | Committing the binaries (they go stale the moment a `.po` changes - the fork's own test needed LuCI's `po2lmo` to notice); compiling LuCI's `po2lmo.c` in the build container (network fetch of LuCI sources and a C toolchain at build time, and the unit tests on Windows/macOS could not run it). `po2lmo.py` is checked byte for byte against the two `.lmo` files the real `po2lmo` produced in the fork (test fixtures). |
| D5 | Aurora stays a pinned upstream binary: `luci-theme-aurora-1.4.0-r20260920.apk`, URL and SHA256 in `build-rootfs.sh` (the fork's pin), overridable with `MU300_LUCI_THEME_APK` for offline builds. Only `openwrt-luci` gets it, and it becomes the default theme there; Bootstrap stays installed. | Building Aurora from source (its CSS is a Vite/Tailwind build: a Node toolchain plus OpenWrt packaging in our build for a theme; revisit if upstream stops publishing releases); vendoring the `.apk` into git; Aurora on plain OpenWrt too (plain stays plain). |
| D6 | The app's translations become standard LuCI: English `msgid`s in the JS (`_('...')`), `po/tr/mu300.po` and `po/zh_Hans/mu300.po`, compiled to `.lmo`. The fork's `DASH_I18N` dictionary (Chinese keys, substring replacement over whole rendered strings) is used once, by a conversion script, to produce those catalogs, and is then deleted. | Keeping `DASH_I18N` and filling its gaps (substring replacement is the cause of the "已添加到 LAN" -> "已..." bug and of every sentence assembled from fragments; it makes Chinese the fallback for every other browser language; no tooling can extract its messages); keeping both systems side by side. |
| D7 | Shared scripts (`rootfs/overlay`, `openwrt/overlay`, `boot/`) carry English only: comments and log lines. Logs are not translated (they are for bug reports, like `dmesg`). Anything the panel shows to a user comes from the catalogs. A test fails on any CJK character outside `i18n/*.tsv` and `openwrt/luci-app-mu300/po/zh_Hans/`. | Translating log lines (three languages in one log makes bug reports harder to read and to grep); allowing Chinese comments (half of the readers cannot review them). |
| D8 | Everything taken from the fork is **ported**: rewritten onto our current file, with our test, in a commit of our own that names the fork commit it comes from. Nothing is merged or cherry-picked. | `git merge kanoqwq/clean-tf-7.2` or cherry-picks: the fork reverts or replaces several of our fixes (the 300 s rescue timer, the macOS USB re-enumeration, the Wi-Fi retry, IPv6 on Ubuntu's data path, our SD card layout, `COUNTRY=TR`), and a merge would bring every one of those regressions in silently. |
| D9 | The fork's IPv6 design (RA relay + NAT66 + `ndp-learn` + an event monitor that owns the bearer's v6 state) becomes `option ipv6 'relay'` of `mu300cell`, set by `openwrt-luci`'s first boot together with `pdptype IPV4V6`. Plain OpenWrt keeps `ipv6 'extend'` (our current RFC 7278 `extendprefix` design, `pdptype IP`) until relay mode has been measured on the Turkish SIMs; the bug fixes inside `mu300cell` that are independent of the design go to both. | Switching all OpenWrt systems to relay now (measured only on China Telecom IoT; FINDINGS 13f is why our default is IPv4-only); leaving relay out (it is what makes IPv6 work on carriers that never fill in `+CGCONTRDP`'s v6 address, which our design cannot handle). |
| D10 | The fork's second LED controller `led-status` is not taken as a program; what it knows (the F50's RGB LED: blue on 4G, white on 5G, red without data, a boot chase; the F50's Wi-Fi lamp on `keyboard-backlight`; a switch for each lamp) goes into `mu300-led`, after the lamps have been checked on the F50 (`mu300-led test`). | Two programs writing the same LEDs (they would fight: `mu300-led` keeps its own state and timeout); taking the fork's colour map without the check (our `mu300-led` says the F50's green channel "stays out"; the fork says it is physically white - only the board can say). |
| D11 | The SMS pool (`mu300-sms`, `mu300-smsd`) is `openwrt-luci` only, in `openwrt/luci-overlay`, with its pool in `/etc/mu300/sms-pool`. Our `sms` command stays the SMS tool of every system. | The pool on every system (two SMS stacks deleting from one SIM); the fork's pool path `/etc/mu300/sms`, which is `sms`'s state directory already. |
| D12 | The USB mode the panel's Device page chooses is a file of the system, `etc/mu300/usb-net` (`ncm`, `ecm` or `rndis`), read by `boot/init` after it has picked the root; when it differs from what the gadget was bound with, init rebinds once before `switch_root`. The kernel command line `MU300_USBNET` still wins. | The fork's order (mount the root before creating the gadget, so the policy is read first): it moves the gadget behind the card scan, up to 8 s later on a device with a card install, for a setting few people change; the fork's path `/disk/openwrt/etc/unisoc-modem/usb-boot.conf` (one system's private path read by init). |
| D13 | Root password: the installers set it, as for plain OpenWrt; an image never ships an empty one. The rescue timer stays `sleep 300`. | The fork's 90 s timer and empty root password (both turned down by the user). |
| D14 | Slot awareness (Android on slot b, Linux on slot a) belongs to the Magisk installer branch, which already plans it (its spec, "Opposite slot"). This branch does not touch the slot code. | Porting it here too (two branches editing the same lines of `boot/init`, `mu300-next-boot` and `mu300-update`). |

## Architecture

```
disk (mu300root on the eMMC or mu300sd on the card)
  /.mu300/boot-os          ubuntu | openwrt | openwrt-luci
  /ubuntu                  rootfs/overlay + Ubuntu
  /openwrt                 rootfs/overlay/opt/mu300 + openwrt/overlay + OpenWrt 25.12.5
  /openwrt-luci            the same as /openwrt + openwrt/luci-overlay + openwrt/luci-app-mu300 + Aurora
```

Build (`openwrt/build-rootfs.sh`, inside the arm64 OpenWrt container):

1. base tarball (checked against the release's `sha256sums`), `apk add` of the common packages - as today;
2. `rootfs/overlay/opt/mu300` -> `/opt/mu300`, `openwrt/overlay` -> `/` - as today;
3. only `openwrt-luci`: `apk add` of `luci-i18n-base-tr luci-i18n-base-zh-cn luci-i18n-firewall-tr
   luci-i18n-firewall-zh-cn` (LuCI itself in the panel's languages), the pinned Aurora `.apk`,
   `openwrt/luci-overlay` -> `/`, the app's `root/` -> `/` and `htdocs/` -> `/www/`, its `po/` compiled by
   `tools/po2lmo.py` into `/usr/lib/lua/luci/i18n/`, `luci.languages.tr` and `luci.languages.zh_cn` registered,
   the app's services enabled;
4. the package list (`apk list --installed`) written to `/etc/mu300/packages.txt` (reproducibility evidence, see
   below); `image-version`; the tarball named after the system.

`openwrt/luci-overlay/` holds what only the panel's system has: `uci-defaults/91-mu300-luci` (Aurora default,
`ipv6 relay`, `pdptype IPV4V6`, LED switches, as first-install defaults behind the marker `luci.mu300.defaults`, which
an update keeps with `/etc/config`; only the NDP relay deletes run on every boot that runs it), `init.d/mu300-smsd`, `init.d/mu300-atd-dash` (the dashboard's AT
channels nr6/nr7), `init.d/mu300-ndp`, `opt/mu300/bin/{mu300-sms,mu300-smsd,ndp-learn}`,
`lib/netifd/proto/mu300cell-v6.sh`, `www/.../view/system/leds.js`.

Boot: unchanged except two points. `pick_root` tries `$want ubuntu openwrt openwrt-luci` (explicit names, never a
glob: `*.old`, `*.new` and `*.broken` must never boot), and init reads `etc/mu300/usb-net` of the chosen root (D12).
`mu300-os` already treats any directory with `/etc` and an init as a system (verified: `is_system`), so it lists and
switches to `openwrt-luci` as it is.

Interfaces for the other branches (Magisk installer, later variants): **a system is a name; its asset is
`mu300-<name>-rootfs.tar.gz`; its kind is the D2 rule.** `MU300_BOOT_OS=openwrt-luci` is all the Magisk installer
needs to add a zip for it.

## The fork's changes, one by one

Classes: **a** = ported to all systems it applies to (the "systems" column); **b** = `openwrt-luci` only;
**c** = not taken (reason); **x** = belongs to another piece of work (named). "Gate" = taken only if the named
on-device measurement passes; a failed gate turns the row into **c** with the numbers recorded in FINDINGS.
Fork commit references are short SHAs on `kanoqwq/clean-tf-7.2`.

### boot/init

| id | change | class | systems | notes |
|---|---|---|---|---|
| K1 | rescue timer 300 s -> 90 s | c | - | user decision: 300 s stays |
| K2 | slot detection from misc/cmdline, `restore_android`, persist log in `boot_<linux slot>` | x | - | Magisk branch (its "Opposite slot" tasks) |
| K3 | the fork's own SD root scan (`mu300sd`) and mount-before-gadget order | c | - | superseded by our SD card work on this branch (294081a, 26b6890) |
| K4 | `is_mu300root` through a loop device at the offset instead of `dd skip` | c | - | measured (FINDINGS 35): busybox `dd` seeks; the probe at the region offset takes 0.01-0.04 s on the F50 and under 0.08 s on the U30 Air, nothing to win |
| K5 | early DHCP: `udhcpd` on the gadget's netdev right after binding, `killall udhcpd` before `switch_root` | a | all | lease 120 s, not 3600 (a host must not keep an early lease when the system's LAN differs from the default subnet) |
| K6 | RNDIS as a single configuration with ACM, `bcdDevice 0x0302` | a | all | gate: Windows 10/11 host gets a network adapter (only with `MU300_USBNET=rndis`) - not measured (no Windows host, FINDINGS 35); kept: only an explicit RNDIS choice uses it |
| K7 | USB mode policy read before the gadget is created | b | openwrt-luci | reshaped (D12): `etc/mu300/usb-net`, after `pick_root`, rebind only on difference |
| K8 | one-shot "applied" marker `/run/unisoc-usb-net-applied` | b | openwrt-luci | with K7, renamed `/run/mu300/usb-net-applied` |

### openwrt/overlay (both OpenWrt systems unless "openwrt-luci")

| id | change | class | systems | notes |
|---|---|---|---|---|
| K9 | `10-mu300-usb`: stop the early `udhcpd` on `lan` ifup, delete the LAN address from bridge ports that kept it, reattach `rndis0` | a | OpenWrt | with K5 |
| K10 | `10-mu300-usb`: our macOS re-enumeration on LAN up removed | c | - | our fix for macOS' inactive link (FINDINGS); removed only if K12 passes its gate on macOS |
| K11 | preinit `06_mu300_early_usb`: restart the early DHCP server in the real root | a | OpenWrt | fixed: the fork hard-codes 192.168.77.x, which is the F50's subnet, wrong on the U30 Air and with a custom LAN address; takes `uci get network.lan.ipaddr` or `mu300-lan-ip` |
| K12 | `mu300-post`: one 100 ms gadget rebind (`mu300-usb-reset --fast-run`) once br-lan/dnsmasq are up, instead of `--if-no-lease 25` | c | - | gate not passed: shown on macOS only (9/9 boots, ping 24-27 s after reboot); no Windows 11 or Linux USB host could be measured (FINDINGS 35). The `usb-ready` instance is not started; the role check and configfs mount of K67 stay |
| K13 | `mu300-post`: `usb1` into br-lan | a | OpenWrt | U30 Air's second function on some kernels |
| K14 | `mu300-post`: the 45 s Wi-Fi retry removed | c | - | kept as a backstop: it acts only when the AP is not up; K15 makes it rare |
| K15 | `mu300-hw`: `wifi down; wifi up` once the regulatory domain is live | a | OpenWrt | readiness-driven; fixes an AP stuck in AP-DISABLED from the world domain |
| K16 | `init.d/mu300-atd`: START 91 -> 19, wait for the tty inside the procd command | a | OpenWrt | at 91 the dial ran before the daemon and opened the tty directly: two readers on one SIPC channel |
| K17 | `init.d/mu300-atd`: second daemon on `nr2` for mobile-data | a | all | Ubuntu: `mu300-atd2.service`; `mobile-data` prefers `/run/mu300-at2`, falls back to `/run/mu300-at` |
| K18 | `init.d/mu300-atd`: daemons on nr3-nr5 (slot 1) | c | - | no measured fault they fix; every channel held open is one more reader that can wedge |
| K19 | `init.d/mu300-atd`: daemons on nr6/nr7 (dashboard pool) | b | openwrt-luci | in `luci-overlay/etc/init.d/mu300-atd-dash` |
| K20 | `init.d/mu300-atd`: `radio-warmup` instance (`mobile-data radio-on` once nr1 is ready) | a | OpenWrt | Ubuntu: `mu300-mobile-data.service` already starts early; it gets `radio-on` as `ExecStartPre` only if measured faster |
| K21 | `init.d/mu300-atd`: plugin early-hook marker | b | openwrt-luci | line guarded by `[ -x /usr/libexec/unisoc-modem/lock ]` |
| K22 | `mu300-vendor`: START 11 -> 09 | a | OpenWrt | 1.4 s earlier CP release (fork's boot timeline) |
| K23 | `mu300-vendor`: no `sleep 5` before `android-vendor-start` | a | OpenWrt | gate: 10 cold boots per device register on the network every time ("wait modem alive timeout" never appears) - passed on soft reboots, F50 #1 9 of 10 (the tenth the known 6.18 hard hang, FINDINGS 31m) and U30 Air 11 of 11; cold boots not run (F50 #1 cannot be power-cycled remotely, the U30 Air has a battery) (FINDINGS 35): registered at 30.1-31.4 s, no timeout |
| K24 | `sysctl.d/99-mu300-console.conf`: `kernel.printk = 1` | a | all | Ubuntu: `rootfs/overlay/etc/sysctl.d/`; the UART console no longer prints the WLAN log synchronously |
| K25 | `uci-defaults`: dnsmasq host entry for the USB host with `broadcast` | a | OpenWrt | with K5 |
| K26 | `uci-defaults`: `earlyusb` firewall zone for `usb0` before br-lan exists | a | OpenWrt | input only, forwarding rejected |
| K27 | `uci-defaults`: `rndis0` in br-lan only when it exists; `bridge_empty 1` | a | OpenWrt | |
| K28 | `uci-defaults`: software flow offloading on, hardware off | a | OpenWrt | with K29 |
| K29 | `patches/fw4-sipa-offload.patch`: `sipa_eth0` in fw4's flowtable | a | OpenWrt | the build fails when the patch stops applying (`--fuzz=0`) |
| K30 | `uci-defaults`: `pdptype IPV4V6`, RA/DHCPv6 relay (the fork's NDP relay left off), `masq6`, `ip6assign 60` (the fork's fixed ULA left out) | b | openwrt-luci | D9; in `91-mu300-luci` with `ipv6 'relay'`. RA and DHCPv6 relay as the fork; NDP relay is off on lan and wan (any earlier `ndp`/`ndproxy_routing` is deleted): odhcpd's NDP relay pins every br-lan neighbour entry as a /128 to br-lan (`ndproxy_routing`, default on), and a LAN host makes such an entry for any address, even off-prefix, with one spoofed neighbour solicitation (route hijack, Task 34 review I1). NAT66 rewrites every client source to the router's address, so the carrier never needs NDP for a client. The device phase checks relay works without it; if it does not, the fallback is `ndp 'relay'` with `ndproxy_routing '0'`. No fixed ULA: OpenWrt keeps the random RFC 4193 prefix of its own first boot |
| K31 | `uci-defaults`: `system.mu300_leds` section | b | openwrt-luci | `leds.js` (K39) |
| K32 | `mu300cell`: every update address-external, the script installs/removes the v4 address and default route itself | a | OpenWrt | fixes the 60 s redial loop (netifd deleting the address it had installed) |
| K33 | `mu300cell`: flush v6 addresses/routes on setup and v4+v6 on teardown | a | OpenWrt | carriers with infinite RA lifetimes stack prefixes otherwise |
| K34 | `mu300cell`: `sleep 20` after an attach failure removed | c | - | netifd re-runs setup immediately; the sleep is the only back-off between AT dial attempts |
| K35 | `mu300cell`: `renew` handler (SIGUSR1 to the monitor) | b | openwrt-luci | relay mode only (the monitor exists only there) |
| K36 | `mu300cell-v6.sh` event monitor (netlink + `+CGEV`) | b | openwrt-luci | relay mode |
| K37 | `ndp-learn` + `init.d/mu300-ndp` | b | openwrt-luci | relay mode; the init script does nothing unless `network.wan.ipv6` is `relay`. Only the bearer's /64 is routed to br-lan; the fork's per-neighbour /128 pins are rejected (a LAN client's spoofed neighbour advertisement pinned any address, the router's own or an internet host's, to br-lan). The br-lan /64 has metric 128, below the carrier RA's metric-256 route for the same /64 on sipa_eth0, so NAT66 replies reach the clients (review I2; a metric-1024 route from an earlier ndp-learn is removed). With NDP relay off (K30) nothing makes a /128 from the LAN either; the device phase checks that a client's stable SLAAC address answers `curl -6` with no /128 present, and that a spoofed NS leaves no off-prefix /128 |
| K38 | `mu300-led-events` (`ubus listen`: WAN and hostapd state -> LEDs) | x | - | f50-leds-fixes (FINDINGS 34 there): the F50 lamp states follow `mobile-data`/`mu300-led wifi sync`; an event loop of the fork waits for that branch |
| K39 | `www/.../view/system/leds.js`: LuCI LED page with the two lamp switches | x | - | waits for f50-leds-fixes: the lamp switches need its `mu300-led` (follow-up) |

### rootfs/overlay/opt/mu300/bin (shared by all three)

| id | change | class | systems | notes |
|---|---|---|---|---|
| K40 | `android-vendor-start`: no `sleep 1` after starting `logdw` | a | all | the socket shows up in the chroot through the rbind of /dev |
| K41 | `android-vendor-start`: `/dev/block` links without forks (0.64 s measured) | a | all | |
| K42 | `bootmark` + marks in vendor/atd/mobile-data | a | all | reworked: English, `/run/mu300/boot-timeline` (tmpfs; the fork wrote `/etc` on every boot) |
| K43 | `boot-milestones` (DNS probe of `www.qq.com`, Chinese comments) | c | - | a probe of one site on every boot; K42 covers the measurement |
| K44 | slot awareness in `android-vendor-start`, `early-recorder`, `mu300-next-boot`, `mu300-update`, `tools/collect-logs.sh`, `boot/build-boot-image.py` | x | - | Magisk branch |
| K45 | `extra-modules`: never load `sipa-dele` in the background | a | all | the fork did it on OpenWrt only; the race is the same on Ubuntu |
| K46 | `sipa-dele-start`: refuse to load after the timeout instead of loading anyway | a | all | |
| K47 | `mobile-data`: load `sipa-dele` synchronously after registration, before `CGACT` | a | all | reproducible CFI panic in the delegate otherwise (fork 45222e6) |
| K48 | `mu300-at`: open the command FIFO read-write | a | all | a write-only open blocked for ever when procd replaced the daemon between lookup and open, holding the global lock |
| K49 | `mu300-at`: success only with a final result code in the answer | a | all | an empty/partial answer after a daemon timeout counted as success |
| K50 | `mu300-at`: 10 ms answer poll with the static busybox's fractional sleep | a | all | `sleep 0.1` failed on the image's busybox and fell to `sleep 1`: 1 s per command (30 commands in 30.6 s) |
| K51 | `mu300-atd`: `${MU300_AT_URC_CHANNELS-0}` (empty means none) | a | all | with `:-` every secondary daemon also read nr0 and the URC stream was split |
| K52 | `mu300-atd`: skip the 1 s drain after a clean final result code | a | all | the slow drain stays after a timeout |
| K53 | `openwrt-wifi-config`: country default `CN` | c | - | `TR` stays; the country comes from `hotspot.conf` |
| K54 | `openwrt-wifi-config`: `noscan 1` | c | - | forces 40 MHz on 2.4 GHz regardless of neighbours (20/40 coexistence) |
| K55 | `wifi-start`: `insmod` the WLAN modules when `modprobe` cannot | a | all | `modprobe` first, `insmod` from `$K` or `$K/extra` as the fallback |
| K56 | `mobile-data`: 4G/5G from `AT+CEREG?` instead of `AT+COPS?` | a | all | COPS takes ~2 s on weak service, on every watchdog round |
| K57 | `mobile-data`: `radio_on` waits for nr0's first URC, does the RIL handshake `AT+SMMSWAP=0` once, waits for `CFUN: 1` after `SFUN=4` instead of power-cycling at once; a lock against two radio state machines | a | all | gate: both devices, 10 cold boots each, registered every time; time to `CFUN: 1` recorded - passed on soft reboots, F50 #1 9 of 10 (the tenth the known 6.18 hard hang, FINDINGS 31m) and U30 Air 11 of 11; cold boots not run (F50 #1 cannot be power-cycled remotely, the U30 Air has a battery) (FINDINGS 35): SMMSWAP OK 20/20, one SFUN=4 per boot, registered every time |
| K58 | `mobile-data`: `AT+CAVIMS=1` (IMS bearer for SMS) | a | all | gate: SMS send and receive with the Turkish SIM on both devices still work - not measured (both lines without credit: the same CMS error with CAVIMS 0 and 1); kept |
| K59 | `mobile-data`: registration from nr0's `+CEREG` URCs, query every 5 s as the fallback | a | all | replaces the 2 s poll loop |
| K60 | `mobile-data`: bounded address fetch with one idempotent `CGACT=1` reassert; no `CGACT?` query first | a | all | fork measured "CGACT up, no address for 40 s" on NR |
| K61 | `mobile-data`: operator name logged in the background after success | a | all | |
| K62 | `mobile-data`: `radio_say` decision log (Chinese) | a | all | English, `/run/mu300/radio.log` |
| K63 | `mobile-data`: `v6_up`/`v6_off` removed from the systemd path | c | - | Ubuntu's IPv6 (our `v6_up`) would be gone |
| K64 | `mobile-data`: `accept_ra 2` for IPV4V6 without an interface identifier | b | openwrt-luci | relay mode; the netifd path only |
| K65 | `mobile-data`: early lock replay hook of the plugin | b | openwrt-luci | inert without `/usr/libexec/unisoc-modem/lock` |
| K66 | `mobile-data radio-on` subcommand | a | all | used by K20 |
| K67 | `mu300-usb-reset`: no rebind in host role, mount configfs when init unmounted it, `--fast-run` | a | all | with K12; `--fast-run` and `--ready` stay in `mu300-usb-reset` for manual use, with no boot-time caller since K12 went to c |
| K68 | `led-status` (F50 RGB states, boot chase, Wi-Fi lamp, per-lamp switches) | x | - | f50-leds-fixes: blue 4G, white (green channel) 5G, red without service, Wi-Fi on `keyboard-backlight`, measured with an observer there; boot chase and switches are a follow-up |
| K69 | `mu300-sms` + `mu300-smsd` (SMS pool) | b | openwrt-luci | D11 |

### The LuCI app, build, tools, tests

| id | change | class | systems | notes |
|---|---|---|---|---|
| K70 | `luci-app-mu300` (views, `common.js`, rpcd `mu300dash`, `unisoc-modem` adapters, ACL, menu, `unisoc_modem` config, init/hotplug) | b | openwrt-luci | D4, D6 |
| K71 | committed `lmo/*.lmo` | c | - | D4: built from `po/` |
| K72 | `DASH_I18N` substring translation | c | - | D6 |
| K73 | Aurora theme `.apk`, pinned, default | b | openwrt-luci | D5 |
| K74 | `build-rootfs.sh`: fail when `mu300cell.sh` is missing | a | OpenWrt | (and `mu300cell-v6.sh` for `openwrt-luci`) |
| K75 | `build-rootfs.sh`: `MU300_MAINLINE_OUT` | x | - | driver job (where the mainline modules come from) |
| K76 | `install.sh`/`make-release.sh`: the plugin inside plain OpenWrt | c | - | the plugin is its own system (D1, D3) |
| K77 | `tools/android-install.sh` (fork's SD and `mke2fs`) | c | - | superseded: ported in 4dcf098 and our SD work |
| K78 | `android/magisk/*`, `tools/build-openwrt-tf-magisk.sh`, `tools/mu300-vendor-from-device.sh`, WebUI plugin, `.gitignore` line | x | - | Magisk branch |
| K79 | empty root password in the fork's Magisk path | c | - | D13 |
| K80 | `upstream/modules/sprd_modem/sipa_delegate`, `upstream/modules/sprd_wlan_combo/sc2355/{pcie,reorder,rx,tx}`, `kernel/patches/wlan_combo-pcie-post-init-retry.patch`, `upstream/patches/0008-usb-ncm-flush-small-frames.patch`, `upstream/make-bundle.sh` WLAN staleness check, `upstream/Dockerfile` cross gcc, `upstream/build*.sh`, `upstream/port/install.py`, `.gitattributes` patch rule | x | - | driver job (separate, later) |
| K81 | `tests/test_unisoc_plugin.py`, `test_usb_management.py`, `test_early_replay_hook.py` | b | openwrt-luci | ported to our paths and names |
| K82 | `tests/test_mu300_i18n.py` | c | - | replaced by `tests/test_luci_i18n.py` (D6, D7) |
| K83 | `tests/test_tf_*.py` | x | - | SD (superseded) and Magisk |

Count: **a 41, b 15, c 18, x 9** (83 rows, after the device gates; K-ids are unique). The fork's docs (`FINDINGS`, `BUILD`,
`KERNEL-PATCH-AUDIT`) are not copied: where one of our measurements confirms a fork finding, the FINDINGS entry
cites the fork commit.

## Translations

Three places, three mechanisms:

* **The panel** (views, `common.js`, menu, ACL title): LuCI `_()` with English `msgid`s; `po/tr/mu300.po` and
  `po/zh_Hans/mu300.po`; `.lmo` built by `tools/po2lmo.py`. Sentences that the fork assembled from fragments
  (`'· ' + n + ' 条'`) become one message with a placeholder (`_('· %d messages').format(n)`). Carrier names
  (`中国联通` -> `China Unicom`) are data, kept as a table in `common.js` keyed by PLMN with English names, and
  translated through `_()` like any other message.
* **Backend messages** (`mu300dash`, `unisoc-modem/*`): English only, and every message the backend can send is a
  fixed string (no interpolated values inside the sentence; values go into separate JSON fields). The UI shows
  `_(res.error)`. `tools/luci-i18n.py` extracts these strings from `printf '{..."error":"..."...}'` patterns, so they
  are in the catalogs although the JS never names them.
* **Installers**: `i18n/tr.tsv` and `i18n/zh.tsv` as today (`tools/check-i18n.py`); every new `t '...'` gets both.

Unknown browser languages get English (the `msgid`). Chinese browsers get Chinese, because the app now has a
complete `zh_Hans` catalog and `luci-i18n-base-zh-cn` is installed - the fork registered only Turkish because its
Chinese source made a Chinese UI the accidental default; that reason is gone.

`tools/luci-i18n.py check` (and `tests/test_luci_i18n.py`, which runs it) fails when:

1. any file of the app outside `po/zh_Hans/`, or any shared script (`rootfs/overlay`, `openwrt/overlay`,
   `openwrt/luci-overlay`, `boot/`, `tools/` except `i18n` data), contains a CJK character;
2. a `_('...')` message, a menu `title`, or a backend message is missing from `po/tr` or `po/zh_Hans`, or has an
   empty `msgstr`;
3. a translation's `%s`/`%d`/`%%` placeholders differ from its `msgid`'s;
4. a `.po` has a `msgid` that no source uses any more (stale);
5. a `_()` call has a non-literal argument anywhere except the documented `_(res.error)` / `_(name)` sites, which
   are listed in the tool (so a new dynamic call cannot slip past the extraction).

The measured state of the fork, for comparison: 351 dictionary entries covered 258 of 259 Chinese UI lines; not
covered were ~9 Chinese backend errors, ~15 English-only backend errors, the partial-match bug, Chinese menu
titles, and 9 Chinese log lines in `mobile-data`. Each of those is a case the checks above catch.

## Build reproducibility

What is pinned, and how:

| input | pin |
|---|---|
| OpenWrt 25.12.5 armsr rootfs tarball | SHA256 from the release's `sha256sums` (as today) |
| packages (`apk add`) | not pinnable on the release feed; the installed list goes into `/etc/mu300/packages.txt` and the release audit prints its diff to the previous release |
| Aurora `.apk` | URL + SHA256 in `build-rootfs.sh` (D5) |
| the app, overlays, catalogs | this repository; `.lmo` built at build time |
| signed `regulatory.db` | Ubuntu 26.04's `wireless-regdb` (as today) |

"CI-buildable": the build needs Docker with arm64 and the kernel modules from `kernel/build-all.sh`, exactly like
plain OpenWrt, so the image itself is built by `tools/make-release.sh` on the maintainer's machine as today. CI
(`tests.yml`) runs everything that does not need the image: the unit tests (now with Node, for the JS tests),
`po2lmo.py` against its fixtures, the catalog checks, and `sh -n` of the new scripts.

## Releases, installers, update

* `tools/make-release.sh`: builds `mu300-openwrt-luci-rootfs.tar.gz` after the plain one; the audit runs over it
  too, plus two checks: the app's files (menu, views, rpcd, lmo for `tr` and the Chinese language name LuCI uses)
  and Aurora are in it, and none of them are in `mu300-openwrt-rootfs.tar.gz`; the notes table gets a row.
* `install.sh`, `install.ps1`: "Which OpenWrt?" after "What should be installed?" when OpenWrt is part of the
  choice; `OSES` then holds `openwrt-luci` instead of `openwrt`; the boot question offers the chosen names; the
  local build passes `MU300_SYSTEM`; `vendor-overlay.py --os` takes the kind. The menu numbers stay as they are
  (scripts and tests that answer `3` keep working). A second run with the other OpenWrt adds it next to the
  first (the device side only replaces the systems in `OSES`).
* `tools/android-install.sh`: settings carried over and the password set by kind; the legacy wipe keeps
  `openwrt-*` directories.
* `mu300-update`: `installed_systems`, `mounted_disk`, `clean` and the name check in `apply` take
  `openwrt-luci`; `keep_list`/`vendor_list` by kind; `running_os` by `-ef`. `rootfs_asset` already yields
  `mu300-openwrt-luci-rootfs.tar.gz`. An old `mu300-update` on a device with `openwrt-luci` ignores the directory
  (it lists systems by name) and replaces itself with the release's before doing anything (existing self-update).
* `tools/reset-password.sh`: `[ubuntu|openwrt|openwrt-luci|all]` (`both` stays as an alias of `all`).
* `uninstall.sh`/`uninstall.ps1`: they remove the whole filesystem and never name systems - checked by a test, no
  change expected.
* README: the third system, what the panel is, where it comes from (kanoqwq), the asset.

Interaction with the SD card work on this branch: none beyond the name lists; `/openwrt-luci` lives on whichever
filesystem init mounted. With the Magisk branch: `MU300_BOOT_OS=openwrt-luci` and the asset name (above).

## Testing

Unit tests (`tests/`, standard library; JS tests skip without `node`):

* `test_boot_init.py`: `pick_root` with only `/openwrt-luci`, with `boot-os=openwrt-luci`, never `openwrt-luci.old`;
  `usb-net` read (K7) with each value and with `MU300_USBNET` set.
* `test_update.py`: `installed_systems` with three, `rootfs_asset openwrt-luci`, `keep_list`/`vendor_list` by kind,
  `running_os` by `-ef` (a bind mount stand-in: a symlink is not enough, so the test uses the same directory).
* `test_android_install.py`: `OSES=openwrt-luci` installs, keeps `etc/config` on update, sets root's hash.
* `test_installer.py` / `installer.Tests.ps1`: "Which OpenWrt?" answers map to the names; boot question.
* `test_po2lmo.py`: byte equality with the fork's two `.lmo` fixtures; plural and context entries.
* `test_luci_i18n.py`: the five rules above, plus the fork's language smoke tests rewritten for `_()`.
* `test_device_scripts.py`: `mu300-at` (FIFO read-write, final result code), `mu300-atd` URC list, `mobile-data`
  `wait_registered`/`fetch_addr` with a fake AT daemon, `mu300-led` new F50 states, `sipa-dele-start` refusal.
* `test_static.py`: the new scripts parse under dash/bash/busybox ash and are executable; fw4 patch present;
  `openwrt-luci` named in every system list (one test that greps the files of D2's list).
* `test_unisoc_plugin.py`, `test_usb_management.py`, `test_early_replay_hook.py`: ported from the fork.

On the devices (F50 and U30 Air, both on OpenWrt and Ubuntu where the row says "all"): the gates in the table, and
an end-to-end run - install `openwrt-luci` next to the existing systems, boot it, use every page of the panel in
the three languages, switch between all three systems with `mu300-os`, `mu300-update apply` from a local release
directory, rollback, 20 reboots without a hang. The plan lists each one with its command and expected result.

## What can go wrong

* **A ported fix behaves differently on our code than on the fork's.** Each is ported with a test, and the risky
  ones (K6, K12, K23, K57, K58, K68) have gates. A failed gate is written down and the row becomes **c**.
* **Early DHCP on a device whose LAN is not the default subnet** (lan.conf, uci). The host gets an initramfs lease
  on the default subnet. Mitigation: 120 s lease in the initramfs; the preinit restart uses the system's real LAN
  address; the final dnsmasq answers on that. Tested with a custom LAN address on the F50.
* **The `_()` conversion changes behaviour of the panel** (a sentence that was assembled differently, a key that
  matched part of a string). The conversion is done page by page with the fork's JS smoke tests kept running, and
  every page is clicked through in three languages on the device.
* **LuCI's file name for Chinese catalogs** (`zh-cn` vs `zh_Hans`) differs from what we generate: the panel shows
  English in a Chinese browser. The build checks the name against `luci-i18n-base-zh-cn`'s own `.lmo` in the
  image instead of assuming it.
* **Aurora disappears upstream or changes under the same name.** The SHA256 stops the build; `MU300_LUCI_THEME_APK`
  takes a saved copy; Bootstrap is installed, so a release without Aurora would still work.
* **Two OpenWrt systems on one disk share nothing at run time** but both write `/.mu300/boot-os` through `mu300-os`
  and carry over `default-boot`: covered by the existing `mu300-os` logic, tested with three systems.
* **The Magisk branch lands first and edits the same lines** (`mu300-update`, `android-install.sh`). The changes
  here are small and by name; the later branch rebases.

## Out of scope

* The driver and kernel fixes (K80): their own piece of work, later.
* Slot awareness (K2, K44): the Magisk installer branch.
* Relay IPv6 on plain OpenWrt and Ubuntu: after it has been measured on the Turkish carriers (D9).
* ImmortalWrt with the panel (D3).
* Publishing a release from this branch.
