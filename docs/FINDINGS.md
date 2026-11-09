# Findings: running Linux on the ZTE F50 / MU300 (Unisoc T760)

Everything below was verified on a ZTE F50 (hardware MU300, firmware `MU300_ZYV1.0.0B09`,
Android 13, stock kernel `5.4.254-android12-9-g9c6342244991`) during September 2026.
Each item lists the symptom, the root cause and the fix, so it can be reused for other
UMS9620 devices (for example the ZTE U30 Air).

## Hardware and firmware facts

| Item | Value |
|---|---|
| SoC | Unisoc T760 (UMS9620, "qogirn6pro"), board `ums9620_2h10_feimao` |
| RAM | 2 GiB (about 1.4 GiB visible to Linux, the rest is reserved for modem/TEE) |
| Storage | eMMC, about 58.25 GiB (122159104 sectors) |
| PMIC | UMP9620 (+ UMP9621), charger IC bq2560x/sgm41513, fuel gauge sc27xx-fgu |
| Wi-Fi/BT | SC2355 "Marlin3" on PCIe, firmware `MARLIN3_20A_RLS2_W24.45.4` |
| USB | DWC3 (`25100000.dwc3`) + MUSB (`musb-hdrc.1.auto`), Type-C on the PMIC |
| Bootloader | Unisoc LK ("sprdlk"), Trusty TEE, A/B slots |
| Boot images | boot and vendor_boot are Android header v4, page 4096, LZ4 legacy ramdisks |

## Boot chain and safe testing

### 1. Slot b one-shot trial (never touch boot_a)
* Linux is written to **boot_b** only, and the 32-byte AOSP `bootloader_control` block at
  offset `0x800` of `misc` is set so that slot b has the highest priority.
* **Unisoc LK treats `tries_remaining == 1 && successful_boot == 0` as an already failed boot**
  (`ANDROID: slot 1 booted fail, rolling back spl and reboot into normal`). A one-shot trial therefore
  needs `tries_remaining = 2`: LK decrements it to 1 and boots slot b; if that boot fails, the next boot
  rolls back to slot a.
* Linux `init` writes the original slot-a block back to `misc` as one of its first steps, so any later
  reboot returns to Android.
* The `uboot_log` partition contains LK's ring log. It is the best source for "which slot was chosen" and
  "why did it reset" (`rst_mode`, `charge first poweron reset`, watchdog flags).

### 2. Boot ramdisk must be LZ4 legacy
* Symptom: `RAMDISK: lz4 image found at block 0`, `RAMDISK: incomplete write (-28 != 8388608)`, then
  `VFS: Unable to mount root fs on unknown-block(1,0)`.
* Cause: vendor_boot's ramdisk is LZ4 legacy; a gzip boot ramdisk appended to it is not unpacked as an
  initramfs by this 5.4 kernel, which then falls back to the legacy `/dev/ram0` image path.
* Fix: compress the boot ramdisk with `lz4 -l`. Also add a `dev/console` node to the cpio.

### 3. Logs without a serial console
* The stock cmdline has `loglevel=1`, so nothing reaches `console-ramoops`.
* pstore only survives a warm reset. Most failures on this device end in a power cut, so init also
  persists its stage list and `dmesg` into unused space inside **boot_b at 48 MiB (8 MiB)**; it is read
  back from Android.
* A USB CDC-ACM function (`acm.GS0`) next to ECM gives a login console on the host
  (`/dev/cu.usbmodem*` on macOS) that works even when networking does not.

## Hardware bring-up with the vendor modules

### 4. USB gadget dependency chain
The UDC only appears when the whole chain probes, in this order:
`extcon-usb-gpio` (the PHY node references `extcon-gpio`) → PHYs → `sc27xx_adc` → `sprd_battery_info`
→ `sprd-charger-manager` → `sc27xx_fuel_gauge` → `bq2560x-charger` (provides the `otg-vbus` regulator used as
`vbus-supply` by DWC3 and MUSB) → `dwc3-sprd` / `musb_sprd`.
* **`sc27xx_adc` must be loaded before the fuel gauge.** The vendor `sc27xx-fgu` driver returns a hard error
  (not `-EPROBE_DEFER`) when its IIO channel is missing and is never probed again.
* The exact working order of the 86 modules is in `boot/module-order.txt`.

### 5. The ~290 s power cut (PM co-processor watchdog)
* Symptom: Linux runs normally, then the device loses power about 290 s after boot. LK shows
  `charge first poweron reset`, not a watchdog reset.
* Cause: the PM co-processor (CM4, `pm_sys`) runs its own watchdog. `sprd_pmic_wdt` disarms it by sending
  `watchdog rstoff` over an SIPC sbuf channel, but that channel only becomes ready after Android's
  `modem_control` reloads `pm_sys` (and the modem) through Trusty (`kernelbootcp` TA).
* Fix: run Android's own `/vendor/bin/modem_control` in a chroot with Android's bionic linker and
  libraries. Requirements that were each discovered by failure:
  1. Copy `/dev/__properties__` from a running Android so property reads work.
  2. Create `/dev/block/by-name` links and apply Android's node ownership (`ueventd.rc` plus `chown`/`chmod`
     from vendor `init*.rc`), because `modem_control` drops to uid `system` (1000).
  3. Bind-mount a copy of `/proc/cmdline` with `androidboot.slot_suffix=_a` so it loads the `_a` modem images
     (LK passes `_b` when booting the trial slot).
  4. **Exec the binary directly.** `sprd_modem_loader` rejects every ioctl/write unless `current->comm` is
     exactly `modem_control` (`drivers/unisoc_platform/modem_loader`); running it as
     `linker64 /vendor/bin/modem_control` makes the task name `linker64`.
  5. Provide a sink for liblog (`tools/logdw`, listens on `/dev/socket/logdw`), otherwise the daemon's logs vanish.
* Result: `kbc_verify_all_avb2() ret = 0`, `SEC_KBC_START_CP() ret = 0`,
  `sprd-pmic-wdt: sbuf ready for pmic wdt init!`, `Modem Alive`, and no more power cut.

### 6. Load average of about 12 is not CPU usage
Vendor kernel threads (`sprd-rotation/N`, `sipa-*`, `slog`) wait in `D` state; Linux counts them in the load
average. `top` shows about 98 % idle.

## Custom kernel

### 7. Matching source
* The ZTE U30 Air kernel (`github.com/Enceka/android_kernel_zte_ums9620_mifi_u30air`, "downloaded from ZTE
  opensource") is exactly 5.4.254, covers 137 of the 150 F50 vendor modules and all but one derived config
  symbol of the F50 stock config.
* Missing from that tree: camera/display/GPU/touch modules (not used on this device) and the Wi-Fi driver
  `sprd_wlan_combo`, which is taken from the realme C51/C53 AndroidT kernel_modules drop.
* An older Unisoc 5.4.147 tree lacks the whole qogirn6pro USB stack and is not usable.

### 8. Build notes
* Full LTO needs more than 8 GiB in the linker step; ThinLTO keeps CFI and links fine.
* Stock config + `kernel/mu300-linux.fragment` adds devtmpfs, fhandle, autofs, SysV IPC, namespaces, nftables,
  btrfs/xfs/squashfs, NFS/CIFS, USB serial/modem/audio host drivers, crypto user API, CDC-ACM gadget, and removes
  `STATIC_USERMODEHELPER` and forced module signatures.
* All vendor modules must be rebuilt from the same tree (symbol CRCs change).

## Root filesystem on free eMMC space

### 9. Unused space after userdata
* `userdata` is 20 GiB and ends at sector 54218752; the GPT's last usable LBA is 122155007 and the backup GPT
  is at the very end. About **32.4 GiB between them belongs to no partition** and read back as zeros.
* The rootfs is an ext4 filesystem (`mu300root`) at byte offset `27762098176` (sector 54222848), 32.39 GiB,
  accessed through a loop device with an offset. **The GPT, userdata and all Android partitions are unchanged.**
* `userdata` uses metadata encryption (`dm-default-key`, `inlinecrypt`), so it cannot be shrunk or shared.

### 9b. There is no second home for the rootfs
Asked for often, because a smaller eMMC variant leaves less free space behind `userdata`. Measured on the device:

* **An image file inside `userdata` cannot work.** Metadata encryption covers the whole partition, not just file
  contents: `mmcblk0p75` reads as ciphertext from Linux - 4079 of the first 4096 bytes are non-zero, no ext4 magic
  at `0x438` (`2d81`), no f2fs magic at `0x400` (`58931da1`). The same `dd` on our own region returns `53ef` and
  the label `mu300root`, so this is the partition and not the reader. The key lives in keymaster and `metadata`
  and Android unwraps it at boot, so a file created from Android is unreadable from Linux whatever we do to it.
* **`blackbox` (500 MiB) and `fulldumpdb` (2 GiB) are not spare.** They look unused, but sampling shows
  `fulldumpdb` non-zero in 16 of 16 samples (it starts with a `note` record) and `blackbox` in 1 of 16: the
  firmware writes crash dumps there.
* So the gap after `userdata` is all there is. The installer no longer demands 4 GiB of it: the installed systems
  measure ~320 MiB (OpenWrt) and ~580 MiB (Ubuntu) and an update keeps the previous one as `<os>.old`, so it asks
  for 800 MiB, 1.6 GiB or 2.4 GiB depending on the choice, and prints the eMMC size and the end of the last
  partition, which identifies the variant when it still does not fit.

* **On the 32 GB variant the space can be made, by shrinking `userdata` - EXPERIMENTAL.** Reported in issue #2
  on a unit whose eMMC is 29.12 GiB (61079552 sectors) with partitions ending at sector 40095744 and `userdata`
  filling the rest, so `--check` computed a negative free size and refused. Shrinking `userdata`'s GPT entry
  left 10 GiB behind it and the installer then ran unmodified.
  * Why only `userdata`: it is the **last** partition, so its end moves and nothing else does. Every other
    partition keeps its offset and the AVB descriptors and the boot chain stay valid. That is the entire
    safety argument, and it does not transfer to any other partition on these devices.
  * `install.sh` offers it when there is no room, asks how many GiB to give Linux and keeps at least 4 GiB for
    Android and 800 MiB for Linux, and requires ERASE to be typed. `uninstall.sh` offers to grow it back, off
    by default because on the 64 GB variant that would hand the factory gap to Android.
    `tools/resize-last-partition.py` does the edit: it validates both GPT copies and every CRC first, refuses
    if the named partition is not the last one, and re-reads what it produced before handing it over. The
    backup GPT is written before the primary, so a power cut between the two leaves the old table and a device
    that still boots.
  * **It rewrites the partition table and erases everything in Android.** Take a full backup first
    (`tools/backup-device.sh`), and treat the path as experimental: it has been done by hand on one device.

### 10. Mounting gotchas
* busybox `mount -o loop,offset=` only creates a loop for regular files; for a block device the options go to
  ext4 and fail with `EINVAL`. Use `losetup -o` and verify `/sys/block/loopN/loop/offset`.
* Android's `losetup -f` can return a loop index whose node does not exist yet; allocate, wait for the node and
  refuse to attach the same offset twice (two loops mounting one ext4 corrupted it once).
* Toybox `od` on 64 MiB is extremely slow; never interrupt a safety check, an interrupted pipeline "passed" once.

## Ubuntu on kernel 5.4

The root filesystem moved from Ubuntu 26.04 to 24.04 LTS: 26.04's userland starts to depend on syscalls newer than
5.4 (below), 24.04 is supported until 2029 and runs on 5.4 without workarounds.

### 11. systemd 259 works on 5.4
5.4 is systemd's minimum baseline; the system boots to `running` with the `old-kernel` taint.

### 12. USB Ethernet must be up before the host activates ECM
* Symptom: macOS shows the "MU300 Linux USB Ethernet" interface as `inactive` forever, no DHCP, although on the
  device `usb0` is UP with carrier.
* Cause: `usb0` was only brought up by a systemd unit a few seconds after the UDC was bound. f_ecm reports the link
  in its first CONNECT notification and macOS' `AppleUserECM` does not pick up a later "connected" notification.
* Fix: `ifconfig usb0 up` immediately after binding the UDC in the initramfs.

### 13. Userland needing newer syscalls
* Ubuntu 26.04's GNU `tar` (1.35+dfsg-4ubuntu0.4) fails with `Cannot stat: Function not implemented` for every
  path, creating and extracting: it resolves paths with `openat2` (Linux 5.6) and has no fallback. 24.04's tar does
  not use `openat2`.
* `ssh.service` is socket-activated (24.04 and 26.04); enable `ssh.socket`, not only `ssh.service`.
* Extracting an archive that contains a `lib/` directory over Ubuntu replaces the `/lib -> usr/lib` symlink with a
  directory (systemd then disappears). Always ship files under `usr/lib/...`.
* `docker export` leaves `/etc/hostname` empty.

### 13b. One reader at a time on the modem's AT tty, and reopen it after a modem restart
`/dev/stty_nr*` are SIPC channels, not real serial ports, and the data goes to **one** reader. While a process
holds the channel, a second one that opens the device reads nothing - it is not broken, it is simply not the owner.

Both of the things this section has claimed over time are true, of different situations, and telling the two apart
is the whole problem:

* A descriptor that was open **across a modem restart** (`AT+SFUN=4`, or a CP crash) refers to a channel that no
  longer exists, so it reads nothing - and because it still occupies the device, every other opener is shut out
  too, which is what made it look permanent. Closing it and opening the device again fixes *that* case
  immediately. Measured: with the daemon holding a stale descriptor, all six channels are silent; with the daemon
  stopped, all six answer `OK` at once.
* **Closing the descriptor while the modem is running does the opposite: the channel never answers again.**
  Measured on 5.4, with `AT+CGACT?` answering normally minutes earlier: `mu300-atd` was killed and started again -
  one close, one open, nothing else - and from then on `/dev/stty_nr1` accepted writes and returned nothing, for
  the remaining forty minutes of that boot. Everything tried against it failed: opening it raw with no daemon at
  all, opening `nr0`-`nr5` together, draining `nr0`, and `/etc/init.d/mu300-vendor restart` - `modem_control`
  re-attaches without reloading the modem (the kernel logs `modem_control has get lock 0`, and no boot follows).
  Only a reboot brings it back. The modem is fine throughout: `nr0` keeps delivering `+SIND` and `+ECIND`, and
  debugfs `modem` still reports `run_state: 1`.

A reopen is therefore the only cure for one case and the cause of the other, so the daemon needs evidence before
it decides, and `nr0` is that evidence: the modem's unsolicited output never stops while it is running, so a `nr0`
that is still producing lines means the modem is alive and this descriptor is fine - whatever else is wrong,
closing it can only make things worse.

Three rules follow, and `mu300-atd` implements all three:

* **Exactly one owner.** The daemon holds the tty and serves one command at a time through a pair of fifos in
  `/run/mu300-at`; `mu300-at "AT+CSQ"` asks it, and `mobile-data` uses it automatically when it is running. Nothing
  else may open the device - including `stty -F`, which is an open and a close of its own.
* **Drain `nr0`.** It is read continuously into a capped log under `/run/mu300-at/urc/`, the way Android's RIL
  holds all six channels open. It is not a cure for a silent channel - that was tried - but registration and PDP
  changes are announced there and nowhere else, and `stty_nr0.log` is what the reopen rule below is built on.
  `nr2`-`nr5` are left alone by default (`MU300_AT_URC_CHANNELS` takes them): a drainer owns its channel as
  completely as the daemon owns `nr1`, and measured, those four say nothing at all.
* **Reopen only when the modem is really gone.** Five unanswered commands, at most one reopen every five minutes,
  and only when `stty_nr0.log` has been quiet for 240 s as well. The earlier rule - reopen after two or three
  unanswered commands - traded a working modem for a dead one on any device busy enough to miss a few replies.

"Exactly one owner" has to be enforced, not assumed. On the mainline kernel the modem kept going quiet a few
minutes after every boot, and it was neither the channel nor the modem: **a second `mu300-atd` had been started**
(procd respawning it, a service started twice). Two daemons read the same `/dev/stty_nr1` and each gets part of
every reply, and the newcomer removes and recreates the command fifo under the one already running, so every
command returns empty. What follows looks exactly like a lost network - the watchdog sees no context, takes the
interface down, netifd tears `wan` down and rebuilds it for ever - while the modem is untouched: stopping every
daemon and starting a single one answered `+CSQ: 43,24` with `+CGACT:1,1` still active. `mu300-atd` now takes
`/run/mu300-at/lock` before it opens the tty or creates the fifo; a second instance waits there instead of
exiting, so a spare is always ready to take over if the owner is killed.

The lock alone was not enough, because `mobile-data` could still walk past it. Its `at()` asks the daemon when
`/run/mu300-at/cmd` exists and opens `/dev/stty_nr1` itself when it does not - and "no fifo" is not the same as
"no daemon". Remove `/run/mu300-at` under a running `mu300-atd` (a stale lock cleaned up by hand, a `tmpfiles`
sweep) and the daemon keeps its descriptor while the fifo is gone, so every `mobile-data` call takes the direct
path and becomes a second reader on a channel that hands each line to exactly one of them. Found on the device:
`mu300-at` reporting "the daemon is not reading commands" while `/proc/*/fd` showed `mu300-atd` *and*
`mobile-data watch` both holding `/dev/stty_nr1`. `mobile-data` now checks `/run/mu300-at/lock/pid` before it
opens anything: a live `mu300-atd` behind that pid means "refuse and say so", and otherwise it takes the same
lock for the length of the command. `tty_setup` obeys the same check, since `stty -F` is an open and a close.

Also make sure only one bring-up runs at a time: `mobile-data up` from the service and from the watchdog used to
run concurrently, and the two of them take the channel lock away from each other for every single AT command, so
neither finishes. `mobile-data` now holds `/run/mu300-mobile-data-up.lock` while it works, `down()` leaves the
context alone while a bring-up owns the channel, and only one watchdog runs.

Verified on 5.4 after these changes: a full `down`/`up` cycle followed by AT queries, and 12 queries over two
minutes with the watchdog polling at the same time - all answered, no reopen needed.

A lock for this has to avoid one trap: **ash and dash run an `EXIT` trap when a subshell exits**, and the daemon
runs a subshell per command (`reply=$(collect ...)`). A `trap 'rm -rf $LOCK' EXIT` therefore deleted the lock a
second after taking it, and the next daemon walked straight in - the very failure the lock was meant to stop, now
happening every few seconds. Clean up on `INT`/`TERM` only, and only when the pid in the lock is still ours.

### 13b-2. The modem stops answering on Linux - and it is not the mainline port (open)
**Measured on both kernels and on stock Android, in that order, and the answer is not what it looked like.** The
modem stops talking to the AP partway through every Linux boot and never comes back without a reboot:

| System | First AT reply | Then | `sipa_eth0` |
|---|---|---|---|
| mainline 6.18.52 | 67 s | silent 22 s later | address, `rx=0` |
| vendor 5.4.254 | 60 s | silent 17 s later | address, `rx=0` |
| stock Android 13 | - | keeps working | address, **`rx=104`** |

So this is **not a regression in the mainline port**: the vendor kernel the device shipped with fails the same way
on the same day, and Android on the same hardware, SIM and carrier passes traffic. What is missing is on our side
of userspace, and it is missing on both kernels. Android runs a full modem stack (RIL, `phoneserver`/`atcmdsrv`,
`slogmodem`, the IMS bridge); we run `modem_control`, `cp_diskserver` and `refnotify` and nothing else - and
`refnotify` cannot even open `/dev/stime_ch` (ENODEV, the time-sync channel), on both kernels. The 5.4 module list
also loads `sipa_usb`, `sprd_pamu3` and `sfp_core`, none of which the mainline build has, though 5.4 shows `rx=0`
with them, so they are not sufficient by themselves.

The rest of this section is what was measured while the failure was still thought to be mainline's. Typical run: `+CSQ: 44,26` at 67 s, nothing at 89 s. What dies is the CP's side of SIPC - the outbox
(CP -> AP) mailbox interrupt stops counting, the modem's own log stops at the same moment, and after the mailbox
fix (13e) the AP's messages still go out and are simply never answered. Restarting the vendor daemons does not
recover it; `modem_control` reloading the modem does not either.

The timing is not fixed: measured deaths 24 s to 60 s after the first successful command (53 s, 87 s, 90 s, 91 s,
109 s of uptime), and not a fixed number of commands either - ten in a fast loop, four at one command every ten
seconds.

Ruled out by measurement, so that nobody spends another evening on them:

* **Our own boot recorder.** It did write 8 MiB to the eMMC every five seconds and that is worth fixing on its own
  (§ below), but with it disabled the channel still died.
* **A cached alias of the modem's shared memory.** The vendor device tree marks these reservations without
  `no-map` (only `rebootescrow` has it), so the 5.4 kernel maps them exactly the same way.
* **The modem power manager.** `sprd_mpm_init_resource_ops()` is never called in the 5.4 tree either, so the NULL
  request/release callbacks are normal for this SoC.
* **The data attach.** With `wan` disabled and no `AT+CGDATA` at all, the channel dies just the same.
* **Two daemons on the channel** (real bug, fixed in 13b) and **the modem log ring filling** (real, 257 KB were
  sitting unread, drained now) - neither stops the failure.
* **A full software mailbox queue** (real bug, fixed in 13e) - the AP can send again, and the modem still goes
  quiet.

Also ruled out by the same differential: **our own services**. With `mu300-atd` and `mobile-data` killed and a
single shell holding the tty, the channel still went silent (132 s instead of ~90 s).

The next step is therefore not a kernel one: find what the CP expects from the AP that Android provides and we do
not - the time-sync channel `refnotify` cannot open is the most concrete lead, followed by running more of the
vendor modem userspace in the chroot the way `modem_control` already is.

### 13f. The data call completes and still nothing arrives - fixed: the delegate has to come after the modem
**Fixed.** The downlink works: DNS resolves through the carrier's own resolvers and a TCP handshake to
1.1.1.1:443 completes, with `rx_packets` and the IPA receive interrupt climbing for the first time. What it
took was loading `sipa-dele.ko` after the modem is up instead of with the rest of the modem stack. The
measurements that led there are kept below, because most of them are about what the *wrong* answers look like.

Before and after, on the same device and SIM, one boot apart:

| | before | after |
|---|---|---|
| `sipa_eth0` rx_packets | 0, always | climbs with every request |
| irq 64 `sprd,multi-sipa-0` | 0 | 67 and rising |
| `SIPA_RM_RES_PROD_CP` | released, ref 0 | granted, ref 1 |
| `nic`: `rc` / `nrt` | 0 / 1 | 4 / 0 |
| channel 5-120 | no trace of it | `send open msg` at 50.9 s, `receive open msg` and `success` at 60.8 s |
| `sipa_dele` conversation | silent | `type=5 flag=1` -> `CONS_WWAN_DL 0->2` -> `type=6 flag=1`, cycling |

The modem answers the channel open the moment it is up - `smsg_ch_open(dst, chan, -1)` waits, and 60.8 s is
exactly when `modem_control` has the CP talking. So the old early load did not fail because the call was made
too early in itself; it failed because the CP was *reset* underneath it afterwards, which is the path where
`smsg_recv` errors out and `conn_thread` exits for good.

Two things that are not this bug, and cost time looking like it:

* **A stopped VPN leaves its policy routing behind.** `/etc/init.d/mu300-vpn stop` removes neither the
  `sbtun` rules (prefs 9000-9010, table 2022) nor sing-box itself, so every packet still goes into a tunnel
  with nothing at the other end and every test reads `Operation not permitted`. That is an EPERM from routing,
  not from the modem, and it made a working data path look dead.
* **A resolv.conf left behind by Docker.** Images built before the build fix carry the container's
  `nameserver 192.168.65.7` as a regular file rather than the symlink to `/tmp/resolv.conf`, so every name
  lookup fails while addresses work fine.

What is left is not ours: with the tunnel down, DNS and 1.1.1.1:443 are reachable over the bearer and other
destinations time out, which is the carrier restricting where this SIM may go.

### 13f-1. How it looked while it was open
Measured step by step on 5.4 with the release userspace, so none of it is a mainline or a script regression:

* `AT+CFUN?` is **0 at boot** - the radio is off until `mobile-data` turns it on. Any experiment that stubs
  `mobile-data` out is testing a device with no radio, which is why "the AT channel survives when nothing
  attaches" proved less than it looked.
* With the radio on, the bring-up is textbook: `+CEREG: 2,1` (registered), `AT+CGACT=1,1` returns `^ORIG: 1,2 OK`,
  `AT+CGACT?` reports `+CGACT:1,1`, `AT+CGCONTRDP=1` hands back an address, a netmask and both DNS servers, and
  `AT+CGDATA="M-ETHER",1` answers `^ORIG: 1,2` and then `CONNECT`.
* Configure `sipa_eth0` with exactly that address and route, and **`rx_packets` stays at 0**. Not one downlink
  packet, on either kernel, while stock Android on the same device, SIM and carrier shows `rx=104`.
* `cid 11` is the IMS context (`ims.MNC002.MCC286.GPRS`), not a second internet bearer, and no other `sipa_ethN`
  receives anything either.

**`AT+CGDATA` needs a long timeout.** It answers `^ORIG` first and `CONNECT` ten to twenty seconds later. The
eight-second budget `mobile-data` used meant the reply landed after we had given up, so the next command read
*that* instead of its own answer - which is most of what "the AT channel dies after the attach" really was.
Raised to 45 s.

What is left is the IPA receive path itself. Two differences from Android are recorded but neither is proven:
its RIL defines the context as `AT+CGDCONT=<cid>,"IP",<apn>,"",0,0,0,0,1` (IPv4 only, with the vendor's extra
parameters) where we ask for `IPV4V6`; and its `SIPA_RM_RES_CONS_WWAN_DL` is granted while ours is never
requested - though in `sipa_nic.c` that consumer belongs to PCIe-source nics, and this modem is on-chip.

**The Android reference, taken on the same device with data working** (`sipa_eth0 rx=419 tx=1440`), so that the
Linux dump has something to be compared against rather than reasoned about. Mount debugfs first - Android does
not (`mount -t debugfs debugfs /sys/kernel/debug`):

```
suspend_stage = 0x0 rc = 80 sc = 79 pf = 1          # and 0x3f / 80 / 80 / 0 a second later: it sleeps when idle
mode_state = 0x0 is_bypass = 0x1
open = 0 src_mask = 0x7070 netid = 0 fcs = 0 rm_flow_ctrl = 0 rr = 0 rw = 1 release_in_progress = 1 nrt = 0 rip = 0
DEVICE: sipa_eth0, src_id 6, netid 0, state UP      # rx_errors = 81, tx_errors = 81
SIPA_RM_RES_PROD_IPA      ref_count = 1 state = 2 (granted)
SIPA_RM_RES_PROD_CP       ref_count = 1 state = 2 (granted)
SIPA_RM_RES_CONS_WWAN_UL  ref_count = 1 state = 2 (granted)
SIPA_RM_RES_CONS_WWAN_DL  ref_count = 0 state = 0 (released)   # never granted, even with data flowing
sipa_dummy0: all 419 packets on CPU0, last_irq_trigger set, last_read_empty 0
```

**The configuration is identical to ours.** Same interface name, same `src_id 6`, same `netid 0`, same
`src_mask 0x7070`, same `is_bypass = 0x1`. So the netid/src_id mapping is not the problem, bypass mode is not the
problem, and `CONS_WWAN_DL` being released is not the problem either - Android passes downlink with it released.
What differs is only the history: `rc = 80` against our `rc = 0`, `nrt = 0` against our `nrt = 1`. Ours has never
been woken, because nothing has ever transmitted through it in the boot that was dumped. **The comparison that
matters has therefore still not been made**: the Linux dump has to be taken while packets are actually being
pushed at `sipa_eth0`, and then read field by field against the block above.

**The downlink is the modem's decision, and it asks first.** This is the part the Android log settles, and it
reframes everything above. With data flowing, `dmesg` on Android is a conversation on SIPC channel 120
(`SMSG_CH_COMM_SIPA`, dst 5 = `SIPC_ID_PSCP`), repeating every few seconds:

```
sipa_dele: smsg_recv, smsg_cnt=295, dst=5, chan=120, type=5, flag=0x1   <- the modem asks
sipa_dele: prod_id:4, on_cmd, flag = 1
sipa_dele: sipa_dele get pd success ret = 0
sipa_rm:   SIPA_RM_RES_CONS_WWAN_DL state changed 0->2                  <- the AP grants
sipa_dele: smsg_send, dst=5 chan=120 type=6 flag=1                      <- and acks
...three seconds later...
sipa_dele: smsg_recv, ... type=5, flag=0x2                              <- the modem lets it go
sipa_rm:   SIPA_RM_RES_CONS_WWAN_DL state changed 2->0
```

So `CONS_WWAN_DL` is not released on a working system, as a single sample suggested - it cycles, granted for as
long as the modem has something to deliver. And nothing arrives until the AP answers that request. **A
`sipa_dele` that is not holding up its end is therefore a complete explanation of our symptom**: the modem is
never given permission, so it never sends, so `sipa_receiver_notify_cb` is never called and `rx_packets` stays
at zero while everything else looks correct.

Checking it costs one command, because both halves of the exchange are `pr_info`:

    dmesg | grep -E 'sipa_dele|sipa_rm'

Nothing at all is the interesting answer. Two ways that happens, both visible:

* `sipa_delegator failed to open dst 5 channel 120` - `smsg_ch_open` refused.
* No message of any kind, including that one. `conn_thread` calls `smsg_ch_open(dst, chan, -1)`, and the `-1`
  waits for ever by design ("the channel open may hang, we call it in the thread context"). If the modem side
  never opens channel 120, the kthread simply sits there and says nothing.

Worth knowing while reading the probe: `dts parsing failed` and `get resource failed for remote-base!` are
**not** the failure. `sipa_dele_plat_drv_probe` logs them and carries on, and Android prints them too.

**The delegate gets exactly one attempt, and ours has been taking it too early** (not yet confirmed on the
device, but it follows from the source and from our boot order). `conn_thread`'s first act is
`smsg_ch_open(dst, chan, -1)`, and with the modem not up that ends one of two ways: it waits for ever for an
OPEN that never comes, or `smsg_recv` returns an error, `smsg_ch_open` frees the channel, and the thread logs
`sipa_delegator failed to open dst 5 channel 120` and **returns**. The reopen handling further down
(`case SMSG_TYPE_OPEN: smsg_open_ack(...)`) only ever runs *after* that first open succeeds, so a modem restart
is survivable and a modem that was never there is not. There is no second chance either: `sipa_dele` is
`[permanent]` in `/proc/modules`, so it cannot be removed and inserted again.

Our order made that likely. The initramfs loaded `sipa-dele.ko` with the rest of the modem stack at about
twenty seconds, while `modem_control` does not have the CP talking until about sixty - measured on this device,
`sprd-sbuf: channel 5-*` and `sbuf ready for pmic wdt init` both land at 60.8 s. Android loads it the other way
round. `sipa-dele-start` now waits for one of those two lines before inserting the module, and
`boot/module-order.txt` and `upstream/module-order.txt` no longer carry it. Nothing regresses if the theory is
wrong: `sipa_core` creates every consumer resource itself, and the delegator only adds `PROD_CP` and the
`CONS_WWAN_UL -> PROD_CP` edge, so `sipa_eth0` still opens exactly as before.

Note that this needs a **rebuilt boot image** to take effect - an image that still loads the delegate early
leaves nothing for `sipa-dele-start` to do, by design.

**Do not test this with ping.** On this network ICMP does not come back even over a link that works: on stock
Android, with data flowing, `ping -I sipa_eth0 8.8.8.8` reports 100 % loss, and that form binds to the device,
so it is the cellular path rather than a VPN swallowing the echo. A TCP connect on the same interface completes
and `rx_packets` climbs. The signal to read is that counter, not whether a connect succeeded - the connect may
be carried by a VPN, and on Android it was (`ip route get 1.1.1.1` goes to `tun0`), but the VPN's own packets
still leave and arrive over `sipa_eth0`, which is the only WAN this device has. `mu300-ipa-dump` does it this
way; several earlier "nothing arrives" measurements here were ping-based and proved less than they looked.

Two smaller differences from the same reference, both cheap to copy and neither yet tested:

* Android keeps **IPv6 switched off on that interface** (`/proc/sys/net/ipv6/conf/sipa_eth0/disable_ipv6` is 1);
  ours carries a link-local address. This fits its RIL defining the context as `"IP"` rather than our `"IPV4V6"`.
* Its `rx_errors` and `tx_errors` are equal and non-zero (81 each) on a link that works, so those counters are
  not by themselves a sign of trouble.

**Read the IPA state before theorising - `/sys/kernel/debug/sipa/` answers most of it**, and two of its fields
are easy to misread:

* `nic`: `suspend_stage`, `rc` (resumes), `sc` (suspends), `is_bypass`, then one line per allocated nic. A fresh
  boot that has sent nothing shows `suspend_stage = 0x3f rc = 0` and that is **correct, not a fault**: the driver
  sets `suspend_stage = SIPA_SUSPEND_MASK` in probe (`sipa_core.c`) and the hardware is woken lazily, by the
  first transmit, through `sipa_nic_rm_res_request()`. Likewise `open = 0` on a nic line means `NIC_OPEN`, since
  that enum starts at 0 (`sipa_priv.h`) - an open nic, not a closed one. Any conclusion drawn from these two has
  to come from a dump taken *while traffic is being pushed at `sipa_eth0`*.
* `rm_res` prints the whole producer/consumer graph with its states, `fifo_cfg` the seventeen common FIFOs,
  `flow_ctrl` the per-FIFO enter/exit counts, and `sipa_eth/sipa_eth0/stats` the driver's own packet counters -
  which are the ones to trust, since a route pointing at the interface is not the same as packets reaching it.

### 13g. The same dead downlink, a different cause: the PDP context, not the IPA
The downlink stopped again, and every counter read exactly like 13f's "before" column: `rx_packets` 0 since
boot, `SIPA_RM_RES_PROD_CP` released with ref 0, `suspend_stage = 0x3f`, `nic`'s `nrt = 1`. That invites the
13f diagnosis a second time. It was wrong. `AT+CGACT?` answered:

    +CGACT:1,0
    +CGACT:11,1

The internet context was gone while the IMS context stayed up, and `sipa_eth0` still carried the address from
the previous call - so `ip addr` and every check built on it kept passing. `mobile-data up` brought cid 1 back,
the interface took a new address, and `rx_packets` moved on the first DNS query. Nothing about the IPA, the
delegate or the kernel was involved.

**An idle link reads as a broken one.** SIPA runtime-suspends a few seconds after the last packet. Suspended,
`suspend_stage` is `0x3f` (`SIPA_SUSPEND_MASK`, the value `sipa_probe` starts from), every resource is
Released and `alloc_skb_cnt` is 0, because the receive buffers are posted on resume and handed back on
suspend. That is the normal idle state, not a fault: `docs/reference/android-ipa-state.txt` section 15 shows
Android cycling through it every few seconds. In `sipa/nic`, `rc` and `sc` count resumes and suspends - awake
is `rc == sc + 1`, and `rc == sc` only means nothing has been sent lately. **None of these counters mean
anything unless traffic is actually leaving while you read them.** Drive the link first, then look.

Two things make "traffic is leaving" much harder to establish than it sounds, and both produced false
positives here before the real cause turned up:

* **sing-box's tun answers ICMP itself.** Its `ip rule` set sends locally generated packets into table 2022
  as well (pref 9001 `lookup 2022 suppress_prefixlength 0`, and the tunnel routes are `0.0.0.0/1` and
  `128.0.0.0/2`, which a prefix-length-0 suppression does not catch), so binding the source with
  `ping -I <cellular address>` does not escape it either. Every ping then succeeds in well under a
  millisecond while `sipa_eth0`'s `tx_packets` never moves. A sub-millisecond reply from a public address is
  the tell. Stop sing-box before believing any ping, and check `tx_packets` alongside it.
* **The kill-switch turns the next attempt into a different lie.** With sing-box stopped but the firewall up,
  the same ping returns `sendto: Operation not permitted` - an EPERM from `table inet mu300_vpn`, which reads
  like the modem refusing traffic. Both have to be down, or the test has to be a hole punched in
  `accept_to_wan` and taken straight back out.

The carrier answers DNS but drops ICMP to 8.8.8.8, so a failed ping is not evidence either way on this SIM.
Resolve a name against the address `AT+CGCONTRDP` hands back and watch `rx_packets`; that is the cheap test
that does not lie.

**A cosmetic write could take the data call down with it.** `mobile-data`'s `up()` ends by putting the blue
LED on. The PMIC rejected that write once (`echo: write error: Invalid argument`), and under `set -euo
pipefail` the `&&` list failing ended the function one line before it reported success - after the context was
already up. So the call was live and the caller saw a failure. The LED writes go through a `led()` helper now
that cannot fail. Worth remembering for any other cosmetic side effect in a path that matters.

Still open: `mobile-data watch` was running and its check does look at `AT+CGACT?`, so it should have caught
the dropped context and reconnected. It did not, and this round did not establish why.

### 13h. Ubuntu never started the delegate at all
The fix in 13f moved `sipa-dele.ko` out of the module list into `extra-modules`, which starts `sipa-dele-start` when
it finds `$M/sipa-dele.ko` with `M=/lib/modules/$(uname -r)`. OpenWrt keeps its modules flat in that directory; the
Ubuntu image keeps them under `extra/`. The test was false on Ubuntu, nothing was started and nothing was logged
(not even the log file the redirection would have created), and every Ubuntu image since then came up with an
address on `sipa_eth0`, a finished `mobile-data` and no downlink. What made it look like working on the test unit
was a VPN still pointed at an HTTP proxy on the computer at the other end of the USB cable: `wget` succeeded with
`sipa_eth0` at rx 0 **and tx 0**. Both scripts now look in both places. Measured on Ubuntu afterwards: the delegate
loads at boot, `rx_packets` climbs, and the Xray tunnel reaches its server over the bearer.

### 13e. The mailbox stops sending after one slow delivery (mainline)
On the mainline kernel the modem went quiet about ninety seconds into every boot - `+CSQ: 44,26` at 67 s, nothing
at 89 s - and stayed quiet until a reboot. It was neither the modem nor the channel: writing an AT command left
the mailbox registers untouched (`/dev/mbox`: INBOX `msg_low` identical before and after), so **the AP was not
sending anything at all**. `mbox-deliver-th` was asleep in `sprd_mbox_deliver_thread`, and the inbox interrupt had
fired **zero** times since boot.

`sprd_mbox_send_data()` queues a message in a software fifo when `phy_ops->send()` fails, and only the inbox
interrupt - raised when a delivery completes or a channel blocks - wakes the thread that drains it. But
`check_mbox_chan_state()` also fails with `-ETIMEDOUT` when the remote core is merely slow to take the previous
message, and that raises no interrupt at all. One such timeout leaves the fifo non-empty for ever, and from then
on every message takes the "fifo is not empty, queue it" path: the AP never speaks to the modem again.

The fix is in the driver, not in the timeout: queueing now wakes the deliver thread itself, and the thread retries
what is still queued (1 ms apart) instead of waiting for an interrupt that may never come.

### 13c. Reading this tty needs `read -t`, and only bash or busybox ash have it
Two ways of timing out a read do **not** work here, and both fail silently:

* **dash has no `read -t`.** It is `/bin/sh` on Ubuntu and Debian, so a `#!/bin/sh` script using `read -t` fails on
  every read with "Illegal option", spins through its timeout and returns an empty reply. The symptom is a daemon
  that answers nothing while burning exactly one timeout of CPU per command (measured: 17.9 s for three 6 s
  commands).
* **The termios timer is ignored.** `stty min 0 time 5` changes nothing: the driver blocks in `sbuf_read` until the
  modem says something, possibly for ever. Visible as `wchan = sbuf_read` with zero CPU time.

`read -t` in bash and in busybox ash uses `poll()`, which this driver does implement, so both work. `mu300-atd` and
`mu300-at` re-exec themselves under bash (or busybox ash) when the shell running them has no `-t`.

Apply `stty` to the already-open descriptor (`stty raw -echo <&3`), never `stty -F /dev/stty_nr1`: the `-F` form is
an extra open and close of the device, which takes the channel away from whoever owns it (13b).

### 13d. Memory shared with the modem must not be mapped write-back (mainline)
The modem and the AP share regions that are reserved **inside** System RAM (no `no-map` in the device tree), and
the modem does not snoop the AP's caches. The vendor 5.4 driver maps them with
`vm_map_ram(pages, count, -1, pgprot_noncached(PAGE_KERNEL))`. Newer kernels removed the `prot` argument from
`vm_map_ram()`, and a port that reaches for `memremap(..., MEMREMAP_WC | MEMREMAP_WB)` instead gets **write-back**,
because write-combine is never available for a linear-mapped region.

The modem then starts, asks for its NV data and never finishes: the NV server reads one good packet and logs
`fail SIZE` on the next, writes three chunks, stalls, and `modem_control` gives up with `wait modem alive timeout`
(`g_modem_state = 8`). `ioremap_wc()` cannot fix it either - the kernel refuses ioremap for System RAM addresses
(`WARNING at arch/arm64/mm/ioremap.c:27`) and the code silently falls back to the cached mapping.

`vmap(pages, count, VM_MAP, prot)` still takes a pgprot, so building the page array and mapping non-cached, the way
the vendor driver did, brings the modem up: `fail SIZE` goes to 0 and "Modem Alive" arrives.

## Wi-Fi (SC2355 / Marlin3)

### 14. Bring-up
* Modules: `pcie-sprd-misc`, `pcie-sprd`, `wcn_bsp`, `sprd_wlan_combo`.
* Firmware and board config come from Android's `/odm/firmware`: `wcnmodem.bin`, `gnssmodem.bin`,
  `wifi_board_config.ini`, `wifi_board_config_ab.ini`. Place them in `/usr/lib/firmware`.
* The realme `wlan_combo` driver falls back to `wifi_board_config_hulk.ini` for unknown projects; the firmware then
  asserts with `CMD_DOWNLOAD_INI / LOAD_INI_DATA_FAILED` and the chip stays in "card dump" state, so `wlan0` cannot
  be brought up (`RTNETLINK answers: No such device`). `kernel/patches/wlan_combo-default-board-config.patch` fixes it.
* `rmmod wcn_bsp` crashes the kernel; reboot instead of reloading the Wi-Fi stack.
* The driver logs `API version not match` for a few command IDs (the realme driver is slightly older than the ZTE
  firmware) and a `WARNING` in `sc2355_free_cmd_buf` (spin_unlock_bh in IRQ context).
* Verified after the fix: the driver parses `wifi_board_config.ini`, `wlan0` comes up, `iw dev wlan0 scan` lists
  nearby networks and `hostapd` (nl80211, WPA2) reaches `AP-ENABLED`. The MAC address is randomized on each load.
* A crash right after installing a module can leave a 0-byte `.ko` on ext4; run `sync` after installing files.

## Mobile data without Android RIL

### 14b. Wi-Fi station mode: decided at boot, and one way only
The SC2355 can be an access point or a client of somebody else's network, but the switch only goes one way:

* The driver creates `wlan0` in **station** mode. hostapd turns it into an access point, and after that nothing
  turns it back. `iw dev wlan0 set type managed` is refused with `Invalid argument` even with the interface down
  and out of the bridge, although `iw phy phy0 info` lists `managed` among the supported modes.
* Deleting and recreating the interface **breaks the radio until the next reboot**: the driver powers the WCN
  chip down and up through an SDIO path (`WCN BASEstart_marlin SDIO card dump`), which is not how Marlin3 is
  attached on this board, and the result is `sprd-wlan: failed to power on WCN!` on every later open. A second
  interface does not help either - with the first one still present the firmware answers scans with
  `sc2355_scan_timeout`.

So the mode belongs to the boot: `mu300-wifi-client.service` runs before `mu300-hotspot.service`, joins the saved
network while `wlan0` is still a station, and holds `/run/mu300-wifi-client.active`, which the hotspot unit refuses
to start on (`ConditionPathExists=!`). Going back to the hotspot needs no reboot, because that is the direction
hostapd can do by itself.

**WPA3/SAE does not work**: wpa_supplicant negotiates SAE correctly but every association is rejected with
`status_code=1`, so Wi-Fi 7 / WPA3-only networks (a "MLO" SSID, for instance) cannot be joined. WPA2 works;
measured on the device: 18 networks scanned, joined, DHCP address, and the Wi-Fi default route (metric 50) taking
precedence over mobile data (metric 100).

### 14c. The Wi-Fi driver can panic the kernel while it is still starting (mainline)
Two boots in a row died at about 29 s with `Unable to handle kernel paging request at 00000000000030b0`,
`pc : sc2355_pcie_tx_cmd_pop_list+0x2c [sprd_wlan_combo]`, reached from `dw_handle_msi_irq` - "Fatal exception in
interrupt", so the kernel stops and LK falls back to Android on the next boot. The captured trace is in
`/sys/fs/pstore/dmesg-ramoops-*`, which Android can read after the failed boot.

The chip reports finished commands with an MSI, and the bus callbacks are registered around `tx_init()` and
`tx_deinit()` - which allocates `hif->tx_mgmt` and sets it back to NULL. An interrupt inside that window walks
`&tx_mgmt->tx_list_cmd.cmd_to_free`, which is offset `0x30b0` from NULL. Both PCIe pop callbacks now return the
buffers to the bus and leave when there is no tx context yet.

### 15. Radio, registration and the data bearer
* AT channels: `/dev/stty_nr0` carries unsolicited results (URCs); `/dev/stty_nr1` is a clean command channel.
* After `modem_control` boots the modem the radio is off (`+CFUN: 0`). Android's RIL (`libimpl-ril.so`) uses the Unisoc
  commands `AT+SFUN=2` (SIM on) and `AT+SFUN=4` (protocol stack on); afterwards `+CFUN: 1` and the modem registers
  (`+CEREG: 2,1,...,13` = E-UTRA-NR dual connectivity, i.e. 5G NSA).
* The network activates the default EPS bearer (CID 1, IPv4v6) by itself. `AT+CGCONTRDP=1` returns the address as
  `a.b.c.d.m.m.m.m` plus DNS servers. `AT+CGDATA="M-ETHER",1` answers `CONNECT` and binds the bearer to
  `sipa_eth0` (`sipa_eth<cid-1>`, raw IP, `NOARP`). Assign the address as /32 and route `default dev sipa_eth0`.
* Measured on a Turkcell 5G NSA SIM: about 9.3 MB/s download and 1.3 MB/s upload. `rootfs/.../mobile-data` implements
  up/down/status/sim-reset and NAT (`nftables masquerade` + MSS clamping) so USB/Wi-Fi clients can share the link.
* AT responses can arrive after a short read timeout and then show up as answers to the next command. Drain the channel
  before sending and read until the final result code (`OK`, `ERROR`, `+CME ERROR`, `CONNECT`).
* Hot-swapping the SIM leaves it busy (`+CME ERROR: 14`) and registration stays at emergency-only (`+CEREG: 2,8`);
  reboot after changing the SIM.
* A SIM without an active data plan still registers and gets an address; TCP handshakes may even succeed, but no data
  flows. Check the plan before debugging the data path.

### 16. Rootfs details found while testing data
* `docker export` leaves an empty `/etc/resolv.conf`: `resolvectl query` works but glibc programs cannot resolve names.
  Link it to `../run/systemd/resolve/stub-resolv.conf`.
* busybox/toybox tar drop xattrs, so `ping` loses `cap_net_raw`; `mu300-fixups.service` restores it.
* Android's uid `system` (1000) is also Ubuntu's first user, so modem device nodes show up as owned by `ubuntu`.

### 26b. The tunnel needs sing-box's gvisor stack on this kernel
Everything through the VPN failed while the VPN itself looked healthy: it connected to its server, resolved
names through the tunnel, and `sbtun` counted the packets going in. Applications got `Connection refused` from
`nc` and `Failed to send request: Operation not permitted` from `uclient-fetch`, and sing-box logged one line
per connection - `router: pre-match[0] => sniff` - and nothing after it. No outbound was ever selected.

The cause is the tun inbound's default **system** stack, which this 5.4 vendor kernel cannot support. It says so
at startup, in a warning that is easy to read as cosmetic:

    inbound/tun[tun-in]: enable offload: set udp offload: TUNSETOFFLOAD: invalid argument

`"stack": "gvisor"` carries its own TCP/IP and does not ask the kernel for any of it. With that one field,
`apk update` goes from "8 unavailable, 145 distinct packages" to **"OK: 11168 distinct packages available"**,
with the kill switch active the whole time.

Worth remembering while chasing something like this: DNS kept working throughout, because it is hijacked and
answered inside sing-box rather than carried as a TCP connection. A tunnel that resolves names but carries no
traffic points at the stack, not at routing or the firewall - both of which were searched first, and neither of
which had a rule with a non-zero counter.

### 26c. SMS needs PDU mode, and Turkish needs more than that
Text mode (`AT+CMGF=1`) looks like the easy way and is unusable here. Whenever the sender is a name rather than
a number - which is most of what a SIM in a router receives - this modem returns the address mangled:

    +CMGL: 1,"REC READ","144414+4140224P444",,"26/09/17,17:18:47+12"

The PDU for the same message says type-of-address `0xD0`, alphanumeric, and the packed septets spell
`ADANA BLD`. Changing `AT+CSCS` does not help: `IRA`, `GSM` and `HEX` all return the same broken string, HEX
simply in hex. Text mode also cannot tell you that three stored messages are one long message.

So `sms` works in PDU mode and decodes the frames itself, in awk, because busybox awk is the only interpreter
on the device - no python, no lua, no perl. It does have `and()`, `or()`, `lshift()` and `rshift()`, and
`printf "%c"` writes a raw byte for anything under 256, which is enough to emit UTF-8 a byte at a time.

**Turkish arrives as ordinary GSM 7-bit with a header saying to read it against a different table.** The user
data header carries information element `0x25` (national language locking shift) with value `0x01`, and without
honouring it the text comes out the right shape with the wrong letters - `hastası` as `hastasì`, `bağışta` as
`baøìæta`, `Aladağ` as `Aladaø`. Eight positions move (`0x04 0x07 0x0B 0x0C 0x1C 0x1D 0x40 0x60`), and they are
exactly the Turkish ones. The header has to be walked element by element for this: the concatenation element is
not always first, and the language element never is.

Sending picks the encoding from the text - GSM 7-bit while every character fits it, UCS-2 otherwise, which is
what makes `ığşçöü ÇĞİÖŞÜ` survive. Verified by sending to the SIM's own number and reading it back intact.

### 26e. The Xray engine, and a connectivity test that could only say no
`mu300-vpn` now defaults to `ENGINE=xray`: Xray-core does VLESS, hev-socks5-tunnel owns a plain kernel TUN (`xtun`)
and hands each flow to Xray's SOCKS port on loopback. Neither installs routes, so the script does, with sing-box's
rule prefs and table (9000-9010, table 2022): Xray's own sockets carry mark `0x2d0` and go to `main`, as do the LAN
and the server's address; everything else goes to `xtun`. The device's own DNS - dnsmasq's upstream, which is the
carrier resolver and does not answer from the far end of a tunnel - is DNAT'd to `REMOTE_DNS`. Every exit path
removes all of it, because routing left behind by a dead engine is what made sing-box's crashes look like a dead
modem.

The kill switch does **not** carry over, even though Xray's sockets carry the same mark: before the tunnel exists
the server's name is resolved through dnsmasq and its certificate fetched with openssl, both unmarked, and the kill
switch drops both - the engine never comes up and the device is left with nothing. sing-box does its bootstrap
lookup on its own marked socket, which is why it never had this problem. So an unset `ENGINE` picks sing-box when
`KILL_SWITCH=1` (what an updated device with an old `vpn.conf` has), and `ENGINE=xray` with the kill switch on
runs without it and says so.

Measured on OpenWrt: the device's exit IP is the server's, `apk add` works through the tunnel, and a Wi-Fi client's
flows are forwarded into `xtun`. The Ubuntu side uses the same script but has not been run on the device.

**allowInsecure is gone in Xray 26.** Its replacement, `pinnedPeerCertSha256`, accepts exactly one leaf
certificate. For a link that asks for allowInsecure the script manages the pin: fetched once with openssl when
there is none and stored as `TLS_PIN_SHA256` in `vpn.conf`, left alone on later starts, and fetched again only when
Xray reports `peer cert is unrecognized (against pinnedPeerCertSha256)` - a renewal - after which the service
restarts itself. Xray reports that failure at **info** level, which a `warning` log level hides completely; the
script runs Xray at info with the access log off and passes only warnings and worse to the system log. `xray tls
ping` is no substitute for openssl here: it tries without SNI first, and a server that ignores that attempt keeps it
waiting past any sensible timeout. (The server this was tested against presents a Let's Encrypt certificate that
expired in December 2025 - which is why its link asks for allowInsecure. The pin skips the expiry check.)

**busybox `nc` on this OpenWrt has no `-w`.** `nc -w 6 HOST PORT` prints the usage text and exits 1, so a
reachability test built on its exit status reports every destination as closed. That produced a confident and
wrong "this SIM reaches nothing but the carrier's DNS" - the same bearer carried the VPN minutes later. Test TCP
with `wget -T` or `curl -m`, check the tool's own exit status rather than a pipe's, and run the test once against
something that must succeed before believing a failure. To test the bearer while a tunnel runs, route a single
address around it: `ip rule add pref 8999 to <addr> lookup main`, test, delete the rule.

## Default boot

### 17. Linux as default without losing the Android fallback
* LK decrements `tries_remaining` before booting a slot and rolls back when it finds `tries == 1 && !successful`.
  The one-shot trial arms slot b with `tries = 2`.
* Default-Linux mode never sets `successful_boot`. Instead:
  1. init skips the slot-a restore when `/etc/mu300/default-boot` in the rootfs says `linux` (slot b is left at `tries = 1`);
  2. `mu300-boot-ok.service` runs 30 s after `multi-user.target` and writes the `tries = 2` block again.
* A boot that never reaches `mu300-boot-ok` therefore leaves `tries = 1`, and the next boot rolls back to Android.
* Verified: `mu300-next-boot linux` + reboot returned to Linux and re-armed slot b; `mu300-next-boot android` + reboot
  booted slot a.

**One interrupted boot was enough to lose Linux.** Re-arming with `tries = 2` gives Linux exactly one boot that
may fail. `mu300-boot-ok` runs about a minute after power-on, so pulling the plug once during that minute - or
a power cut, or a crash - sent the next boot to Android. The initramfs has its own counter meant to allow five
such boots, but LK always got there first, so it never mattered. (`uboot_log` shows it plainly: `bootable slot 1
... tries_remaining: 1, successful_boot: 0`, `check rollback slot 1 tries: 1`, `Booting slot_a`.)

Now the user picks N, 1-6, at install (default 5; stored in `.mu300/boot-attempts` on the Linux disk, changed
later with `mu300-next-boot attempts N`), and slot b is re-armed with `tries = N + 1`. LK decrements on every boot
and rolls back at 1, so N boots in a row that never reach boot-ok are allowed and the next one is Android. The
field is three bits, hence the upper limit. The initramfs counter now reads the same N and fires on the same
boot, as a backstop.

* The block is rebuilt at run time from the initramfs' `tries = 2` block, because `mu300-update` does not replace
  the boot image and an older one carries only that block. Byte 14 is slot b's `prio | tries << 4 |
  successful << 7`; the CRC-32 over the first 28 bytes comes from `gzip`, whose stream trailer is the same
  CRC-32. Before writing anything the script rebuilds `tries = 2` and requires it to match the initramfs' block
  byte for byte, and falls back to that block otherwise.
* An older boot image still has the initramfs counter fixed at five, which sends a device to Android after four
  failed boots whatever N says.
* Measured with N = 5 on OpenWrt, with boot-ok suppressed for two boots: LK booted slot b from `tries = 6`, then
  from 5, then from 4 - two unconfirmed boots in a row stayed in Linux - and the next good boot re-armed 6. Not
  run to exhaustion; the rollback at `tries = 1` is the LK behaviour above.

**Boot-ok used to wait for every service.** It was ordered `After=multi-user.target`, so one service that never
finished - `mobile-data` stuck on a busy AT channel in an older Ubuntu image - kept the boot unconfirmed however
usable it was, and the next reboot counted it as failed. It now follows only the USB network and SSH: a boot you
can log in to is a good one. Measured on Ubuntu: SSH at 47 s, boot-ok at 79 s, independent of the rest.

`mu300-os` also carries `default-boot` to the system it switches to. The initramfs reads it from the system it
boots, so switching from one where Linux is the default to one where it was never set made the first reboot
there go to Android.

## Parity with Android

### 18. Services and drivers Android runs that the minimal port lacked
* `cp_diskserver` persists modem NV (`nr_fixnv*`, `nr_runtimenv*`); on first start it immediately wrote pending
  "dirty" NV data, so without it modem NV changes are lost. `refnotify` handles modem reference-clock requests.
  Both run from the same chroot; `srtd` needs Android's binder radio HAL and is skipped.
* Drivers that load cleanly after the modem is up: `sprd_soc_thm`, `thermal-generic-adc`, `sprd_cpu_cooling` (binds
  cpufreq and CPU hotplug cooling to `soc-thmzone` with trips at 70/85/110 °C), `leds-sc27xx-bltc` (RGB status LED),
  `zte_card_holder_det`, `sc27xx-vibra`, `sprd_cp_dvfs`, `sprd_ddr_dvfs`. `zte_sar` loads but the aw9610x SAR sensor
  is not populated on this board (`-201`).
* Android disables audio entirely (`ro.audioserver.disabled=true`, no sound cards) although the device tree has an
  enabled sound card (`unisoc,vbc-v4-codec-sc2730`), the UMP9620 codec and an AW883xx smart amplifier that answers on I2C.
* Android's hotspot (`WifiConfigStoreSoftAp.xml`) lives on the metadata-encrypted `/data`, so Linux cannot read it; it
  has to be copied while Android runs. No factory default Wi-Fi credential is stored in a readable partition.
* RAM: Android uses about 960 MiB of the 1.4 GiB; Ubuntu with all services about 480 MiB, of which ~100 MiB is
  unreclaimable vendor-driver slab. journald is capped (`RuntimeMaxUse=16M`).

### 19. systemd ordering pitfall
* A unit `Before=ssh.socket` that keeps default dependencies is ordered after `basic.target`, while `ssh.socket` is
  before `sockets.target` (before `basic.target`). systemd silently drops `ssh.socket` from the boot transaction and SSH
  never starts. Units that must run before sockets need `DefaultDependencies=no`.

### 20. Diagnosing early power cuts
* The initramfs log loop ends at `switch_root` and journald flushes only after local filesystems are up, so a power cut in
  the first seconds of systemd leaves no log. `mu300-early-recorder.service` keeps writing `dmesg` to the boot_b log area
  for the first five minutes.

### 20b. Telling a userspace reboot from a power cut, and finding who asked for it
`/sys/fs/pstore/console-ramoops-0` survives into the next boot and separates the two cases in one line. A power cut or a
watchdog leaves the log ending mid-sentence; a deliberate reboot ends with the kernel's own

    [   46.468782]c0 [    T1] reboot: Restarting system with command 'shell'

`[T1]` is the task that made the call, and on OpenWrt that is procd: busybox `reboot` does not call the syscall itself,
it hands the request to init, so *every* userspace reboot on this image shows up as pid 1 no matter who started it. The
pstore line therefore says "userspace asked", never who. Three things together do say who, and all three write to
`/mnt/mu300-disk/.mu300/` so they survive the reboot they are recording:

* **not** a wrapper on `/sbin/reboot`. That was tried and it deadlocks the device: on OpenWrt `reboot` hands the
  request to procd, and procd runs `/sbin/reboot` itself on the way down - straight back into the wrapper, which
  execs busybox, which tells procd again. Nothing reboots, `reboot` simply returns and the machine keeps
  running, and the only sign is the audit file gaining an entry whose caller is `pid=1 /sbin/procd`. Hours were
  lost to this twice over: once wondering why the device would not reboot, and once reading the two entries an
  earlier operator's `reboot` had left as evidence of something deliberate. If a wrapper is wanted anyway, it
  must call `reboot(2)` directly (`busybox reboot -f`) rather than exec the applet that talks to init,
* a line at the top of each `/etc/rc.button/*` handler - OpenWrt's `reset` handler reboots on a *short* press
  (`SEEN < 1`), so a bouncing key is a plausible cause and worth ruling in or out explicitly,
* a kprobe on the syscall for anything that bypasses `/sbin/reboot`, which needs no module:

      echo 'p:mu300reboot __arm64_sys_reboot' > /sys/kernel/debug/tracing/kprobe_events
      echo 1 > /sys/kernel/debug/tracing/events/kprobes/mu300reboot/enable
      cat /sys/kernel/debug/tracing/trace_pipe >> /mnt/mu300-disk/.mu300/reboot-trace.log &

  `trace_pipe` is worth the reader process: the trace buffer itself does not survive the reboot, and procd sleeps a
  second before it calls the syscall, which is long enough for the line to reach the disk.

`gpio-keys` on this board exposes `KEY_VOLUMEDOWN`, `KEY_VOLUMEUP` and `KEY_POWER` (`B: KEY=1c000000000000 0`) and no
`KEY_RESTART`, so `/etc/rc.button/reset` cannot fire here; `/etc/rc.button/power` runs `poweroff`, which the kernel
would log as "Power down" rather than "Restarting system".

## LAN, Wi-Fi bands and regulatory

### 21. One LAN for USB and Wi-Fi
* `br-lan` (192.168.77.1/24) bridges `usb0` and `wlan0`; one dnsmasq serves both.
* cfg80211 refuses to bridge `wlan0` ("Device does not allow enslaving to a bridge") because `IFF_DONT_BRIDGE` stays set:
  the SC2355 driver marks the interface as AP but returns an error from `change_virtual_intf` when tearing down the
  previous firmware mode fails, so cfg80211 skips clearing the flag. `kernel/patches/wlan_combo-allow-bridging-ap.patch`.
* After moving modules, delete old copies: `depmod` indexes every subdirectory and `modprobe` loaded stale drivers from
  an `extra.old/` directory for several boots.

### 22. Regulatory database
* This 5.4 kernel only has the `sforshee` regdb certificate; current `wireless-regdb` is signed by `wens`, so
  `iw reg reload` fails with `-ENODATA`, the domain stays `00` and 5 GHz is `NO-IR`. Android's own `regulatory.db` is
  signed by a different certificate and is rejected too. `kernel/patches/regdb-wens-certificate.patch` adds mainline's
  `wens.hex`. cfg80211 tries to load the database before the rootfs is mounted, so userspace runs `iw reg reload` first.

### 23. Only one AP; 5 GHz AP needs the DS Parameter Set element
* `iw list`: `#{ managed, AP } <= 1` — only one AP interface, so 2.4 and 5 GHz cannot be served simultaneously.
* Symptom: with hostapd on channel 36 the firmware answered `CMD_START_AP` with `SPRD_CMD_STATUS_NOT_SUPPORT_ERROR`
  (HT/VHT), or accepted it and never sent beacons (non-HT), for every country, channel, rate set and HT/VHT/PMF setting
  tried. Android's SoftAP on the same firmware runs on 5180 MHz with 80 MHz.
* Stock (ZTE) and realme `sc2355_start_ap` are identical, so the command payload was captured on Android with a kprobe
  on `sc2355_send_cmd_recv_rsp` (`msg->data` at +24, command id at data-11). Android's beacon contains a DS Parameter Set
  element (`03 01 24`) on 5 GHz — Unisoc's hostapd adds it — while upstream hostapd only adds it on 2.4 GHz. The
  firmware takes the AP channel from that element.
* `kernel/patches/wlan_combo-5ghz-ap-ds-params.patch` inserts the element when hostapd omits it. Verified: 802.11a/n/ac
  AP on channel 36 at 80 MHz (seen by a client, disappears when hostapd stops). The firmware offers 5 GHz AP only on
  36-48 and 149-165 (its ACS channel list); `hotspot-start` uses HT40/VHT80 there, and `hotspot-verify` still falls back
  to 2.4 GHz if the firmware refuses.
* The wiphy rate table lists HT MCS rates as legacy bitrates, so hostapd advertises odd "extended rates" (some with the
  basic-rate bit); Android does the same and the firmware ignores them.
* hostapd's 20/40 MHz coexistence scan (`HT_SCAN`) completes on this driver; the log line after it can lag because
  hostapd's stdout is block-buffered.
* The stock driver prints the SoftAP passphrase to the kernel log (`vendor_softap_convert_para`); Android `dmesg`
  captures contain it.

### 26. Modem resets and mobile data recovery
* The modem firmware occasionally resets: it sends an smsg (type 12, "channel 0 not opened" on the AP side),
  `sipa_delegate: modem_reset`, and `modem_control` stops and reloads the modem through Trusty. It comes back with
  `+CFUN: 0`, no registration and no PDP context, while `sipa_eth0` keeps its stale address, so clients lose internet.
  Android's RIL reconnects silently.
* `mobile-data watch` (`mu300-mobile-data-watch.service`) checks `AT+CGACT?` and the interface address every 30 s and
  runs `up` again after two failed checks; `mobile-data down` sets `/run/mu300-mobile-data-down` so a manual disconnect
  is respected. Verified by switching the radio off: data returned without intervention.

### 26d. USSD, and the AT lock that made the channel look dead
Three separate traps, and the first one hid the other two for an evening. `mu300-ussd` handles all three.

* **The AT lock meant two things at once.** `/run/mu300-at/lock` was both "`mu300-atd` holds the channel", which
  lasts for the life of the system, and "one client at a time", which lasts for one command. Whichever ran second
  won: a client removed the daemon's directory as it exited, after which the lock behaved as a plain client mutex
  and everything worked — so the collision stayed invisible. Restart the daemon and it takes the directory back,
  and from that moment every client waits out its full 90 s and prints `mu300-at: busy` at a completely idle
  modem. Ownership is now `/run/mu300-at/owner`; `lock` is the client mutex alone.
  * The failure looks exactly like a wedged SIPC channel, which sends you after the modem instead of the lock.
    What tells them apart: `cat /run/mu300-at/owner/pid` against `ps`, and whether `+CSQ` is still ticking in
    `urc/stty_nr0.log` — a wedged channel goes quiet, a blocked lock does not.
  * Do not diagnose this by polling `mu300-at` in a loop. Each call takes the client lock, so the loop queues up
    behind the command already waiting for its answer and genuinely jams what was only blocked.
* **A USSD code has to be sent as hex.** `AT+CUSD=1,"*101#",15` answers `+CME ERROR: 3`, and still does after
  `AT+CSCS="GSM"` — so this is not the character set behaving as documented. `AT+CUSD=1,"2A31303123",15` is
  accepted whatever `AT+CSCS` says.
* **The answer arrives on a different channel than the command.** The command gets a bare `OK` on `/dev/stty_nr1`;
  the `+CUSD` turns up seconds later on nr0, the unsolicited channel. Anything waiting for it on nr1 waits for
  ever. `mu300-atd`'s drainer logs nr0 to `/run/mu300-at/urc/stty_nr0.log`, and that log is what to read — waiting
  on a file also costs the modem nothing, which is the point after the trap above.
* `drain()` in `mu300-atd` now appends what it reads to `urc/stty_nr1.log` instead of discarding it, so a late
  answer on the command channel is recoverable too. `+CMTI` (a new SMS) lands there the same way.
* The third `+CUSD` field is a **CBS** coding scheme, not an SMS one: 15 is the GSM alphabet, 72 is UCS2, which is
  what a Turkish menu comes back as. The body is hex-dumped one byte per character, not packed septets.
  `awk -v mode=ussd -v hex=... -v dcs=... -f sms-pdu.awk` decodes both.
* Verified end to end: `mu300-ussd '*101#'` returns the SIM's own number, and a `dcs=72` body decodes with its
  Turkish characters intact.

### 27. Where the missing ~550 MiB of RAM goes
* `Memory: 1430144K/2097084K available ... 617788K reserved`. The device tree reserves 464 MiB for the modem firmware
  (`cp-modem@88000000`, needed for 4G/5G), 24 MiB for Trusty (`tos-mem`), 8 MiB SIPC shared memory, 3 MiB DDR training
  data and a few small areas; the kernel image (~35 MiB) and the page tables for 2 GiB (~32 MiB) make up the rest.
  `cma_share` (48 MiB) is still usable for movable pages.
* Not needed on Linux: `logobuffer` (9 MiB, there is no display) and `sysdump-uboot` (16 MiB, bootloader crash dumps).
  `kernel/patches/of-reserved-mem-skip.patch` adds `CONFIG_OF_RESERVED_MEM_SKIP` (the boot image command line is not
  passed on by LK, so a cmdline option alone would not work); MemTotal grew from 1447 to 1473 MiB.
* `mu300-zram.service` adds lz4 zram swap of half the RAM (swappiness 100).


### 28. Mali-G57 GPU (OpenCL) without Android
* The stock `mali_kbase.ko` (DDK r40p0) does not load: 166 of its 335 imported symbol CRCs differ from this kernel.
  realme's `kernel_modules` master branch has the same DDK (`gpu/natt/mali`, r40p0-01eac0, UK 11.36, platform
  `qogirn6pro`); an older checkout of the tree has r34p0 (UK 11.31), which Android's r40p0 userspace would not accept.
  `kernel/build-mali.sh` builds it; it probes `23140000.gpu` as "arch 9.0.9 r0p1" and creates `/dev/mali0`.
* Userspace is Android's `libGLES_mali.so` (it is also `libOpenCL.so` and the Vulkan ICD), a bionic library with a
  37-library closure (VNDK apex, bionic, gralloc/mapper HIDL stubs). It runs in the existing vendor chroot with
  `LD_LIBRARY_PATH` covering vendor, egl, VNDK and bionic; `/dev/ion` is enough for OpenCL buffers.
* Test programs are built for bionic with plain clang (`--target=aarch64-linux-android29`, linked against the device's
  `libc.so`/`libdl.so`/`libOpenCL.so`) and a small `_start` that calls `__libc_init` (`tools/gpu/`), so no NDK is needed.
  `android-gpu-run /system/bin/cltest`: "OpenCL 3.0 v1.r40p0-01eac0", device "Mali-G57 r0p1", 4M-element kernel
  verified correct.
* There is no display, so GLES/Vulkan are only usable off-screen. The driver adds about 45 MiB of memory use.

## OpenWrt

### 29. OpenWrt 25.12 next to Ubuntu
* Layout: the ext4 area holds `/openwrt` (and `/ubuntu`, or Ubuntu directly in the root on first installs);
  `/.mu300/boot-os` selects the system and `init` starts `/lib/systemd/systemd` or procd's `/sbin/init`. The disk stays
  mounted at `/mnt/mu300-disk`; `mu300-os ubuntu|openwrt` switches. `openwrt/build-rootfs.sh` builds the rootfs from the
  official armsr/armv8 tarball (checksum-verified) with apk, our kernel modules flat in `/lib/modules/<release>`
  (ubox kmodloader), the vendor chroot and procd services (`mu300-vendor`, `mu300-hw`, `mu300-post`).
* Cellular WAN is a netifd protocol (`proto mu300cell`, option `apn`), so LuCI/fw4 handle routing, DNS and NAT;
  `mobile-data watch` calls `ifup wan` after modem resets.
* Pitfalls found on the device:
  * procd mounts `/dev` as a 512 KiB tmpfs: copying the 85 MiB Android property area gives empty files and
    `modem_control` never boots the modem (power cut at ~290 s). The property area is now bind-mounted (also on Ubuntu,
    saving the RAM).
  * procd preloads `/lib/libsetlbf.so` into services; the chroot runners unset `LD_PRELOAD` or the bionic linker fails.
  * `ujail` drops capability 38 (CAP_PERFMON), unknown to 5.4, so jailed services crash-loop; `procd-ujail` is removed.
  * OpenWrt's busybox lacks `od`, `timeout`, `losetup`, `telnetd`; the static busybox provides them, plus a tiny
    `mountpoint` script (neither busybox has it).
  * GNU `stty` fails on the modem tty ("unable to perform all requested operations"); it is non-fatal now.
  * `wifi`/netifd wireless handlers are in `wifi-scripts` (with `iwinfo`, `wireless-regdb`), not pulled in by `wpad`.
  * macOS keeps the ECM link inactive after netifd reconfigures `usb0`; a hotplug hook re-enumerates the gadget on every
    LAN ifup.
  * The kernel had no nftables sets (`CONFIG_NF_TABLES_SET`), so fw4's ruleset (`ct state vmap {...}`) was rejected as a
    whole and there was no NAT; the fragment now enables sets, objref, flow offload, redirect, quota and friends.
  * OpenWrt's `regulatory.db` is unsigned; this kernel wants the signed one (Debian's `wireless-regdb`).
  * cfg80211 requests `regulatory.db` at 1.6 s, before the rootfs exists; the direct load fails and the request sits in the
    sysfs firmware fallback for 60 s (no userspace helper answers it, neither on OpenWrt nor with systemd-udevd), and
    `iw reg reload` returns ENOENT meanwhile. `regdb-load` answers the pending requests through `/sys/class/firmware`,
    then reloads; the country is applied before netifd starts hostapd.
  * Attended sysupgrade ("Check online for firmware upgrades") and `sysupgrade` would flash a whole-disk armsr image
    (own GPT, kernel 6.12) over the eMMC and brick the device. The packages are removed and `/sbin/sysupgrade` only allows
    configuration backups.
  * `wifi-scripts` generates an open "OpenWrt" network on first boot; `openwrt-wifi-config` replaces it once (marker
    `/etc/mu300/wifi-configured`) with the imported SSID/WPA2 settings.
* Verified after reboots: 5 GHz AP (channel 36), cellular WAN, a USB client's traffic leaves with the modem's public IP;
  memory use about 140 MiB.
* The early recorder runs from preinit, so a failed OpenWrt boot leaves dmesg, `ps`, `logread` and the Android logcat in
  boot_b for `tools/collect-logs.sh`.


### 30. Installer notes
* Android's `/system/bin/sh` (mksh) does 32-bit arithmetic: byte offsets of the Linux region (27 GiB) overflow, so the
  device script works in sectors. mksh also lets a failing EXIT trap replace the exit status; the installer checks for an
  explicit success line instead.
* `adb shell`/`exec-out` read stdin and swallow answers piped into a script; every call uses `</dev/null`.
* Android restarts once shortly after booting back from a Linux fallback; start the installer when Android has settled.
* A first-generation install (Ubuntu directly in the filesystem root) is replaced by `/ubuntu`; `init` still boots the
  old layout if no `/ubuntu` exists.


## Bluetooth

### 25. SC2355 Bluetooth on BlueZ
* Transport: `sprdbt_tty` (realme `wcn/bluetooth/driver/tty-pcie`, built with `BSP_BOARD_UNISOC_WCN_SOCKET=pcie`) exposes
  an H4 tty `/dev/ttyBT0` over the WCN PCIe link and registers a `bluetooth` rfkill that powers the BT function.
  `btattach -B /dev/ttyBT0 -P h4` creates `hci0`.
* Vendor init (Android `libbt-sprd_suite`, Marlin3): before the stack starts, send `0xFCA0` with the 176-byte PSKey block
  from `bt_configure_pskey.ini` (BD address at bytes 20..25, little endian), `0xFCA2` with the 252-byte RF block from
  `bt_configure_rf.ini`, then `0xFCA1 00 00 01` (dual mode, enable). `tools/bt-init/mu300-bt-init.c` does this. Without the
  PSKey upload the controller reports a fixed placeholder address.
* The firmware advertises Hold Mode and Park State in its LMP features, but `Write Default Link Policy Settings` returns
  `Invalid HCI Command Parameters` for any value that includes them (0x0000/0x0001/0x0004/0x0005 accepted, 0x0007 rejected).
  The 5.4 kernel sets every advertised mode, so the init sequence aborts and `hciconfig hci0 up` fails with `EINVAL`.
  `kernel/patches/bluetooth-marlin3-link-policy.patch` requests only Role Switch and Sniff (Bluetooth is built in, so this
  needs the kernel rebuild).
* Android keeps the real BT address in `/data/vendor/bluetooth/btmac.txt` on encrypted `/data`; Linux uses
  `BDADDR=` from `/etc/mu300/bluetooth.conf` or a stable locally administered address derived from `machine-id`.
* Verified: `bluetoothd` powers the adapter and LE/BR-EDR scanning lists nearby devices.
* Rebuilding with a modified tree appends `-dirty` to the kernel release and breaks module loading; `.scmversion` with
  `-gb50db5b6224c` in the source tree keeps the release string stable.

## Mainline 6.18 as a daily kernel

Everything here was measured on the test device (64 GB F50, Vodafone TR, 5G NSA) with 6.18.54 and the
upstream/ modules, running the release's Ubuntu and OpenWrt images.

### 31. Ubuntu on 6.18, and what it takes
Ubuntu had never been booted on the mainline kernel. With the kernel bundle from `upstream/make-bundle.sh`,
installed by `mu300-update` (modules into every system, the boot image built on the device), it boots in under
45 s and runs the hotspot, USB NCM, Bluetooth, the modem with SMS, the VPN and mobile data - the downlink
included, which had never been shown on 6.18: the delegate's handshake completes and a 20 MB download goes
through.

Speed at one spot, alternating kernels within minutes (signal -99 to -107 dBm, so downloads say little):
upload is about twice as fast on 6.18 (0.29-0.34 MB/s against 5.4's 0.167 MB/s). 5.4 never exceeded exactly
166 650 B/s in any upload, with or without the VPN - a ceiling that constant is the device's, not the network's.

### 31a. Wi-Fi RX: sync_for_device stopped invalidating (hang under load)
A client uploading over Wi-Fi hung the board within 40 s. The Marlin3 driver waits for the device to finish an
RX buffer by reading a flag in it and "refreshed" the buffer between retries with `dma_sync_single_for_device()`.
On arm64 that call invalidated `DMA_FROM_DEVICE` buffers until 5.19 and cleans them since, so the CPU kept
reading a stale line: every check failed (`hw still writing`, 369 times in the hung boot's log), the checksum
taken from the same stale descriptor was wrong (`hw csum failure`), and each failure went to the 115200 baud
console at loglevel 8 from the RX path. `dma_sync_single_for_cpu()` is the call for reading what the device
wrote. After the fix: 3 x 15 MB uploads and a 30 MB download over Wi-Fi, no error. The IPA driver syncs the right
way round already.

Consequence for 5.4: IPv6 TCP and UDP from Wi-Fi clients get through on 6.18 (SSH and DNS over IPv6 measured),
but not on 5.4, whose Wi-Fi driver carries `wlan_combo-rx-software-checksum.patch` - the only difference in that
path. The patch's "hw csum failure" may well have had this stale read as its cause too.

### 31b. The modem units' ordering cycle (both kernels)
`cp_diskserver` was ordered before `mu300-vendor`, which runs before `sysinit.target`, while its default
dependencies put it after `sysinit.target`: a cycle, which systemd broke by dropping `cp_diskserver` and
`refnotify` from the boot transaction (on 5.4 it happened to break it elsewhere). On 6.18 it showed because the
modem's nodes appear about 18 s in, after the units' `ConditionPathExists=/dev/modem` had been evaluated.
`cp_diskserver` has no default dependencies now, the conditions look for the driver
(`/sys/module/sprd_modem_loader`), and `mu300-vendor` waits for the node.

### 31c. A context that comes back with a new address (both kernels)
After the radio or the network dropped the bearer, the LTE modem attaches again by itself and the default context
is active again - with a new address, while `sipa_eth0` keeps the old one: it sends and never receives. The
watchdog only asked whether the context was active and the interface had an address. It compares the address
(`AT+CGPADDR`) now. `AT+CFUN=4` from scratch: radio back on, new address noticed, internet back after 72 s.

### 31d. Evidence of a failed boot
Two boots in about twenty (one Ubuntu, one OpenWrt) died shortly after `switch_root` - the OpenWrt one between 11
and 21 s by the early recorder - and left nothing. Three gaps, all closed:
* The 6.18 config had no lockup detectors, so `softlockup_panic`/`hardlockup_panic` from `boot/init` had nothing
  behind them. Soft and (buddy) hard lockup detectors now panic; hung tasks are only reported.
* `mu300-early-recorder` was never enabled in the Ubuntu image; it is, and writes every 2 s for the first 90 s.
* pstore works: `sysrq-c` -> panic -> Linux again a minute later, with the panic in
  `/var/lib/systemd/pstore/dmesg-ramoops-0`. `systemd-pstore` moves the records out of `/sys/fs/pstore` at boot,
  which is why that directory looked empty after the failures.
Also: the Wi-Fi driver's per-frame traces pushed a boot's messages out of the ring buffer within a minute (now
`pr_debug`, and `log_buf_len=4M`), and systemd's 10 min reboot watchdog was rejected by the UMP9620 PMIC watchdog
(maximum 300 s), so a hang during reboot was not caught (`RebootWatchdogSec=2min`).

### 31e. sipa-set-rps: a thread that never started
The vendor comments the thread's wake-up out ("zsw changed", to keep `rps_cpus` fixed) but still creates it, so it
sat in its pre-start state - uninterruptible - for good: one more on the load average, and on 6.18 a hung-task
report every two minutes. It is not created any more.

### 31f. The delegate on OpenWrt: -EINPROGRESS taken for a failure
On OpenWrt the downlink died on some boots and the board crashed on others, and the recorder and pstore (31d)
caught both crashes in the delegate's connection thread `dele-4-5`: a call through NULL (`pc : 0x0`,
`lr : conn_thread+0x188 [sipa_dele]`) and a soft lockup, 22 s in `console_flush_all`. The probe log explains
them:

    sipa_rm: driver CB returned with -115
    sipa_rm: SIPA_RM_RES_PROD_CP state changed 1->3
    sipa_rm: SIPA_RM_RES_PROD_CP does not exist
    sipa_delegate soc:ipa-apb:sipa-dele: cp_delegator_init failed: -22

OpenWrt brings the WAN up before the delegate is loaded (at ~80 s), so `CONS_WWAN_UL` is already granted when
`sipa_delegator_start()` makes it depend on `PROD_CP`. That starts requesting the producer and returns
`-EINPROGRESS` - which the vendor code took for a failure: it deleted `PROD_CP` again, `cp_delegator_init()`
ignored that result, failed on the next dependency ("does not exist"), and the failed probe freed the delegator
under the connection thread it had already started. On Ubuntu the delegate is loaded before there is traffic,
the call returns 0, and none of this happens. Fixed in the module: `-EINPROGRESS` is success, the result of
`sipa_delegator_start()` is checked, the Wi-Fi offload dependencies (unused here) are warnings, and the delegator
is no longer devm-allocated. The 5.4 kernel has the same driver in its own tree and the same crash on OpenWrt ("Kernel
panic - not syncing: CFI failure (target: 0x0)" in `dele-4-5`, caught while testing v2026.09.29); it gets the same
fix as `kernel/patches/sipa-delegate-einprogress.patch`.

On 2026-10-02 a separate delegate bug caused a 6.18.54 trial to panic after 4792 seconds and fall back to Android.
The CP sent ENABLE while the IPA device was already runtime-active. `pm_runtime_get_sync()` returned 1 (success), but
`cp_dele_on_commad()` treated every nonzero result as failure and retried forever with `mdelay(1)` and a printk on
each iteration. The `dele-4-5` thread then hit a 22-second soft lockup in `console_flush_all`. The handler now uses
`pm_runtime_resume_and_get()` (0 on success, negative on failure), acknowledges errors without spinning, and avoids
taking a second reference for a repeated ENABLE. The matching 6.18.54 and 7.2.8 `sipa-dele.ko` modules were rebuilt.

### 31g. Three warnings on every boot, three vendor bugs
6.18 printed three `WARNING:`s on every boot; each one is a bug in the vendor drivers:
* `kernel/softirq.c:429 __local_bh_enable_ip` from `sc2355_free_cmd_buf`: the Wi-Fi command list's `complock` is
  taken with `spin_lock_bh` in the PCIe tx-complete interrupt, and the same lock is taken with only bottom halves off
  in process context - that interrupt arriving on the CPU holding it spins forever. Both places now use
  `spin_lock_irqsave`.
* `tty_port_link_device` from `mtty_probe` (`sprdbt_tty`): the tty driver is allocated with one line and a second,
  never used port is linked at index 1 - past the end of `driver->ports[]`. 5.4 has no check there and writes it;
  6.18 refuses with the warning. The second port is no longer linked.
* `dev_addr_check`, "sipa_eth0: Incorrect netdev->dev_addr": `sipa_eth`, `seth` and `sipa_usb` wrote their random
  MAC straight into `netdev->dev_addr`; `eth_hw_addr_random()` sets it through `dev_addr_set()`.

A look at `dmesg` itself (Ubuntu's journal misses the first seconds of the kernel log) found two more:
* `device_create_file` / `sysfs_create_file_ns`, "Attribute base_addr: read permission without 'show'", four times
  per boot: `sipx`, `sblock`, `sbuf`, `smem`, `smsg` and the mailbox each created a `base_addr` attribute with
  neither show nor store, on an embedded `platform_device` that is never registered - it could never be read. Gone.
* `dev_addr_check` for `sipa_dummy0`: the same direct `dev_addr` write as above; now `eth_hw_addr_set()`.

And the idle load average of 2.0 was two kernel threads parked in D state for good: `slog-0-0` polls every 2 s for
the modem log to be switched on with an uninterruptible `msleep()`, and the Wi-Fi `SC2355_TX_THREAD` waits for work
with `wait_for_completion()`. They now sleep interruptibly and in `TASK_IDLE`; the idle load is about 0.4.

7.x adds one of its own: a workqueue has to say whether it is per-CPU or unbound (`WQ_PERCPU`, which 6.18 has
too, or `WQ_UNBOUND`), and `trusty` and six Mali queues said neither ("trusty-nop-wq is using neither WQ_PERCPU or
WQ_UNBOUND"). They say `WQ_PERCPU` now - what the kernel picked for them anyway.

The Wi-Fi lock and the Bluetooth port come from the same vendor sources as the 5.4 modules, so the 5.4 build
patches them as well (`kernel/patches/wlan_combo-tx-complock-irqsave.patch`, `sprdbt-tty-one-port.patch`); there
the second port really was written past the end of `ports[]`, since 5.4 does not check.

The 6.18 bundle also carries `modules.builtin` and `modules.builtin.modinfo` now: without them depmod warned and
`modprobe` of a built-in driver failed. Three boots after the fixes: no warnings, Wi-Fi AP, mobile data, Bluetooth
(24 devices in a scan) up.

### 31h. Power-off, and the command line's crutches
6.18 had only the PMIC restart (patch 0001): `poweroff` fell through to PSCI `SYSTEM_OFF`. The same patch now
registers a power-off handler that does what the vendor's `sc27xx-poweroff` does for the UMP9620 - clear
`LDO_XTL_EN` and `SLP_LDO_PD_EN` in `SLP_CTRL` (0x2248), then write 1 to `PWR_PD_HW` (0x2020). It cuts the power:
ramoops lives in RAM, and after `reboot` the next boot finds the old console there ("reboot: Restarting system"),
after `poweroff` it finds nothing. The F50 has no battery, so on a powered USB port the PMIC switches it on again
about a minute later; unplugged, it stays off.

The command line still carries four bring-up flags. Measured on the running system, what each one keeps:
* `clk_ignore_unused`: 315 of 551 clocks are on with no driver holding them (bus, config, DSP, camera, thermal,
  PWM clocks ...). This is where power could be saved - and where the risk is, because the modem, the Wi-Fi
  firmware and the vendor modules touch hardware behind some of them without the clock API. Not changed yet.
* `pd_ignore_unused`: nothing - the only generic power domain (`sipa-sys`) has active devices (USB, IPA).
* `regulator_ignore_unused`: one regulator, `LDO_VDDSIM0` - the SIM's supply, which the modem uses and no Linux
  driver claims. Without the flag the kernel would switch the SIM off. Required.
* `fw_devlink=permissive`: required. Without it (and `pd_ignore_unused`) the board booted but the USB gadget
  never came up - it waits for suppliers in the vendor device tree that no driver provides - and since Linux
  itself was fine, the boot counted as good and the board stayed in a Linux without USB until it was forced back
  to Android.

### 31i. A Wi-Fi card that is late at boot panicked the board
Rebooting OpenWrt on 5.4 again and again (testing v2026.09.29), one boot ended in "WCN BOOT: error: Waiting for
PCIe scan card timeout", `sprd_pcie_remove`, "Unable to handle kernel paging request at virtual address
ffffffffffffffe8" in `__wake_up_locked` from `complete()`, and a panic - back to Android. The SC2355 does not always
show up on the PCIe bus within the 5 s the vendor driver waits; the timeout then removes the half-probed card, and
`sprd_pcie_remove()` completes `remove_done`, which only `sprd_pcie_remove_card()` initialises - a completion that
was never set up. It is initialised before every scan now, `remove` checks for a probe that never set its data, and
a timed-out scan is tried once more before Wi-Fi and Bluetooth are given up. Both copies of the driver: the 5.4
tree (`kernel/patches/wcn-pcie-scan-timeout.patch`) and `upstream/modules/wcn_bsp`.

## Updating on the device

### 32. Old kernels, an idle IPA, and an update that ended in Android
A report: a device on the vendor 5.4 kernel, updated with `mu300-update`, only ever started Android afterwards.
Reproduced on the test board with an older installer boot image (kernel #11 of 20 September) and the systems of
v2026.09.21. The boot image of v2026.09.27 (kernel #13) does not have the problem below: its downlink works after
four and a half idle minutes, with the same failed power-off messages in the log. Which of the published boot
images have it was not established, so the update protects all of them:

* After about two minutes without mobile traffic the IPA tries to power off and fails
  (`Polling check power off reg timed out`, `sipa power off maybe fail`). From then on the downlink is dead
  until the next boot: `rx_packets` of `sipa_eth0` stops, TLS handshakes time out, and neither the data
  watchdog (the address is still right) nor a reconnect of mobile data brings it back.
* The old `mu300-update` downloaded one system, unpacked it for a few minutes - exactly the quiet time the IPA
  needs - and then started the next download. `curl` had no timeout and hung there, and twice the board reset
  without a trace (no pstore, no persistent log) right when the next network transfer started: once between
  the two systems, once at the start of the boot image step. A reset while boot_b is being written leaves slot b
  unbootable, and LK then falls back to Android on every boot - the reported symptom.

A reset does not have to hit the boot_b write to end in Android, though. With the boot scheme of the older boot
images slot b is armed for one boot (`tries_remaining 2`, see 1); a crash leaves it at 1, and LK's log on the next
start reads `check rollback slot 1 tries: 1` - `Have not get right slot` - Android, for good, with boot_b intact.
`su -c mu300-linux` in Android (or the Magisk module's button) arms slot b again and Linux starts as before; the
newer boot images count failed boots instead (5 in a row before Android).

`mu300-update` now:
* downloads everything the update needs (the systems and the kernel bundle) first, checks every file against
  the release's `SHA256SUMS`, and only then changes anything; the unpacking and the boot_b write need no network;
* keeps the IPA from going idle while it runs: a ping every 3 s (to 1.1.1.1 and to 223.5.5.5, which is reachable
  from mainland China). With it, a 26 MB download after four otherwise quiet minutes on kernel #11 ran at 5 MB/s;
  without it the download after the same pause never started;
* cuts off stalled transfers (`--connect-timeout`, `--speed-limit`) and resumes partial files, so a dead downlink
  is an error message ("reboot, then run mu300-update apply right away") instead of a hang;
* stops the data watchdog while it installs, so no redial happens in the middle of the boot_b write.

The full old-user path (installer boot_b with kernel #11, both systems reinstalled, boot image written) then took
40 s, and the board started the new kernel.

### 32a. The offset in init, replaced by every boot image update
A user installed 6.18 with the installer and got "MU300 standalone Linux ... automatically reboots to Android
after 300 seconds": the root filesystem was not found. `boot/init` finds the Linux region at `ROOT_OFFSET`, a number
in the file - our test board's region (27762098176). The installer writes the device's own offset into that
line, but that init lives in the device's ramdisk segment, and the generic segment behind it - what
`mu300-update` puts there for every kernel and boot image update since v2026.09.27, and what the installer adds for
a mainline kernel - brings its own init, which replaces the file, with the default. On every device whose region
is somewhere else, each such boot image went looking at the test board's offset, dropped into standalone mode and
went back to Android: the boot image did not have to be broken for "the update always ends in Android", which is
very likely what the report of section 32 was too. The test board never showed it: its region is the default.

init now finds the region itself, as the installer places it - the first 2 MiB boundary after the last partition
(from sysfs) - and takes the first candidate that holds the mu300root filesystem (ext4 magic and label), the
number in the file only as the last one. Tested with a generic segment whose default was wrong on purpose:
"stage=root-offset found=27762098176 (default 12884901888)", and the board booted. A device left in Android by
this needs one reinstall from a computer (the installer's `update` keeps settings and data); from v2026.09.29 on
every generic segment carries the new init.

Found on the way: the account merge rewrote `/etc/passwd` and friends in place (a reader at first boot could see a
half-written file: `Failed to resolve user 'messagebus'`); it now writes a copy and renames it, and
`mu300-accounts` runs before tmpfiles, sysusers and D-Bus. `rollback` removed `<os>.broken` even when that was the
system still running (only rm's `--preserve-root` stopped it), and `apply`, `rollback` and `clean` could remove
`<os>.old` while it was the running system (an apply or rollback without the reboot in between); all of them now
check whether a directory is the running root (`[ / -ef dir ]`) first.

## ZTE U30 Air

### 33. The same board with a battery

The U30 Air (`ro.product.device` U30Air, firmware `U30Air_SSV1.0.0B14`) is `ums9620_2h10_feimao` like the F50: same
SoC, same 64 GB eMMC and partition layout (partitions end at sector 54218752, so the Linux region is at the same
offset), and its stock kernel is the very source this project builds (Enceka's tree is named after it). Its
`/proc/config.gz` differs from the F50's in 19 symbols, all about the battery and its surroundings: ZTE's "SQC"
charger stack (`sqc_charger`, `sqc_bq2560x`, `sqc_netlink`, `charger_policy_service`, `zte_power_supply`,
`zte_misc`), `SC27XX_PD`, GPIO and LDO LEDs, a SAR sensor (`SAR_PARA` bougain instead of anthurium) and an NFC tag.
Two are built in on the U30 Air (`I2C_CHARDEV`, `I2C_SMBUS`); everything else is a module or a bool that only
changes modules.

The first boot with the F50 modules (one-shot trial) reached switch_root and ran, but the host never saw USB:
`sprd-charger-manager` probed with -517 for good, because the U30 Air's device tree describes the SQC charger
manager, and the F50 build of that module is `charger-manager.c` while `VENDOR_SQC_CHARGER` swaps in
`charger-manager-sqc-comm.c`. The USB PHY and dwc3 wait on the charger/Type-C side, so no gadget.

`kernel/build-u30air.sh` builds the F50 config plus `kernel/u30air.fragment` in a second build directory and keeps
what differs. The Image has to stay the F50 one, so the check is the set of symbols vmlinux exports: `I2C_SMBUS=m`
changed it (the i2c core gains `of_i2c_setup_smbus_alert`), and no U30 Air driver needs it, so it is left out;
`I2C_CHARDEV` is a module. Comparing whole files marks nearly every module as different (build id, symbol table
order), so the comparison is on `.text`, `.rodata`, `.data`, `.modinfo` and `__ksymtab_strings`; that leaves 15
modules. The load order (`boot/module-order-u30air.txt`) is the F50's with the charger block replaced, checked
against every module's `depends=` (the fuel gauge needs the charger manager, which needs the SQC modules and
`charger_policy_service`).

init picks the set: `/etc/mu300-device` (written by the installer into the device segment of the ramdisk, which an
update's generic segment does not replace), else the device tree (`/charger_policy_service` exists only on the U30
Air). The set is used only when `linux-modules/u30air/kernel.release` matches `uname -r`, since a mainline kernel's
generic segment brings its own modules and order but leaves these in place.

With them: 93/93 modules, USB network, the hotspot, Bluetooth, mobile data, `/sys/class/power_supply/battery`
(capacity, status) and the charger's `usb`/`ac` online flags. The battery LED is the charger's own; the others are
`gpio-leds` (`pwr_green`, `net_blue`/`net_red`/`net_green`/`net_white`, `wifi_blue`/`wifi_white`), driven by
`mu300-led`. No `LEDS_TRIGGER_NETDEV` in this kernel, so the Wi-Fi LED follows the hotspot service, not traffic.

### 33a. misc and boot_b were looked up before the eMMC existed

`sdhci-sprd` is one of the vendor modules, so the eMMC appears only once they are loaded (about 10 s in). init looked
for misc and boot_b before that: 20 s of polling for nothing, then `misc-NOT-FOUND` - slot a was never restored by
init, and there was no persistent log (`persist-target dev=none`). It happened on the F50 as well; the trial boots
there were rescued by the bootloader's own counting. The lookup now runs after the modules (misc and boot_b at
10.7 s on both devices), and the reboot timer, which started earlier, reads boot_b from `/run/bootb`.

### 33b. Two devices on one computer

Every device had `192.168.77.1` and the same USB MAC addresses (`02:50:00:00:77:0x`). The subnet now goes with the
kind of device (F50 `.77`, U30 Air `.78`; `mu300-lan-ip`, `lan.conf` still overrides it) and the gadget MACs are
`02:50:<md5 of the serial>:<subnet>:0x`. `mu300-vpn` always keeps the device's own LAN out of the tunnel: a
`vpn.conf` copied from an F50 said `LAN_CIDRS=192.168.77.0/24`, which on a `.78` device would have sent every reply
to its USB and Wi-Fi clients into the tunnel.

### 33c. Mainline on the U30 Air: the USB PHY waited for a Type-C driver that does not exist

The first 6.18 boot on the U30 Air came up completely - systemd, mobile data, the VPN, the hotspot - with no USB at
all: no network, no serial console. Its persistent log said `25310000.ssphy: cannot add phy` and `dwc3: failed to
initialize core`. After LK's dtbo merge the PHY's `extcon` on the F50 is `/extcon-gpio` (`linux,extcon-usb-gpio`,
which mainline has), on the U30 Air `typec@380` - the PMIC's Type-C block, `sprd,sc27xx-typec`, which has no driver
under mainline, so `usb_add_phy_dev()` deferred for ever. Both boards have the VBUS GPIO node: the ssphy driver now
points the property at it when it names the Type-C block. Found with the device's own log, since there was no USB to
look through: init persists it into boot_b, and it was read from Android afterwards (`tools/collect-logs.sh`).

A boot without USB is also a boot you cannot end: it counted as good, so only failing five boots by hand brought
the device back. `build-boot-image.py --trial-guard SECONDS` is for such experiments: init leaves a timer running
past switch_root (from a copy of busybox in `/run`, reached through its working directory, since switch_root deletes
the ramdisk and moves `/run`) that reboots unless `/run/stay` exists by then; with a one-shot trial that is back in
Android. A reading of the PMIC registers through debugfs (`regmap/spi4.0/registers`) during these tests locked up a
CPU for 23 s and panicked the board - the ADI bus does not take a full register sweep.

Two more things that only show with two devices on one computer: every gadget had the USB serial number
`MU300LINUX`, and macOS gave the second device a serial port but no network interface; it is now
`MU300LINUX-<serial number>`. And under mainline `/proc/cmdline` is the kernel's forced command line, without
`androidboot.serialno`: init reads the bootloader's from `/chosen/bootargs` in the device tree.

Under 6.18 and 7.2 the U30 Air has USB, mobile data, the VPN, the hotspot, Bluetooth and its LEDs
(`CONFIG_LEDS_GPIO`). The battery is not reported: its charger and fuel gauge (the SQC stack and `sc27xx-fgu` on
the UMP9620) have no mainline drivers; the charger IC keeps charging on its own defaults. Kernel bundles now name
the devices they run on (`./devices`): mu300-update and the installers do not put a bundle from before this onto a
U30 Air.

### 33d. The U30 Air's battery under mainline

Under 6.18 the PMIC's efuse, ADC and fuel gauge (`sprd,ump9620-efuse/-adc/-fgu`) appeared as platform devices with
no driver: mainline's sc27xx drivers know the SC2731/SC2730 family only. Patches 0004-0006 add UMP9620 from the
values in Unisoc's 5.4 drivers:

- efuse: 64 blocks, read directly from a window at 0x40 once the controller's RTC clock is on and ungated
- ADC: its own scale table and ratios, a battery-voltage detection graph for scale 1, calibration from two efuse
  words per graph (bits 15:4), and a vote for its 26 MHz clock in an AON register (`sprd_adc_pm_reg`) around each
  conversion
- fuel gauge: enable bits at 0x2008/0x2010, the 4200 mV calibration in bits 15:7. `bat-temp` is the NTC's voltage
  on this board, not a temperature (71.2 "degrees" at first): the battery node's `voltage-temp-table` converts it.
  The charger IC has no driver, so the status comes from the battery current. Mainline read a discharge current
  as ~2 billion: `u32 cur - 8192` wraps below zero; the vendor driver casts to s64 first.

The first status fallback asked for the capacity, whose calibration asks for the status: a stack overflow in the
first second of every boot. Five of them in a row put the device back into Android by itself - the fallback did
its job - and the panic was in pstore.

The charger (SGM41511, `ti,bq2560x_chg` on I2C) still has no driver under mainline: it charges on its power-on
defaults and reports nothing; I2C is not even enabled there. Under 5.4 the vendor SQC stack drives it.

### 33e. USB host on the U30 Air, and a trial guard that outlived its trial

Mainline had the gadget side of the dwc3 only: no host stack, no `/sys/bus/usb` at all. With dual-role dwc3, xHCI,
mass storage, HID and the USB network/serial drivers it is a host as well, and it still starts as a gadget (the
dwc3 node has `usb-role-switch` and no default mode: peripheral), so the USB network to a computer comes up as
before. Two things the vendor stack does elsewhere:

- The role. Under 5.4 the PMIC's Type-C block decides; it has no mainline driver, so the role is chosen from
  userspace: dwc3 opens its role switch to it in 7.2, patch `6.18/0007` does it on UMS9620 in 6.18 (a patch 7.2
  does not need goes into `patches/<version>/`). dwc3 tells the PHY about the role through `otg_set_vbus()`, which
  the PHY did not provide: it has an otg structure now (host eye pattern, A-type ID, D+/D- pull-downs).
- The 5 V. `vbus-supply` is the charger's `otg-vbus` regulator, and the charger (SGM41511, part 0010 in REG0B, on
  the I2C controller at 22a0000) has no driver under mainline. `mu300-usb` sets its OTG_CONFIG bit (REG01 bit 5)
  and turns its watchdog off (REG05 5:4), which would otherwise return it to its defaults 40 s after the write,
  boost included. It refuses host mode while REG08 reports power good (a computer or charger on VBUS), and turns
  the boost off at boot: the chip runs on the battery and keeps its registers across a reboot.

A Logitech receiver (HID) and a flash drive (FAT, mounted and read) worked through a USB-C adapter under 6.18 and
7.2. The drive read at ~3 MB/s with or without the PHY's host settings; it is an old stick, not yet compared
elsewhere. HDMI through the same adapter needs DisplayPort alternate mode over USB-C, which this project has on
neither kernel.

Two restarts during these tests, both about 600 s after boot, were not the USB: `mu300-update` keeps the device
segment of whatever image is in boot_b, and boot_b held a `--trial-guard 600` experiment - so every boot restarted
after ten minutes. init now honours a guard only in a trial boot (Linux not the default), and says so.

### 33f. The U30 Air's LEDs: three kinds of wiring, and lights that never went out

Under mainline the network and Wi-Fi lights stayed lit whatever Linux did. Lighting each LED the kernel knew, one
at a time, while someone watched the device, showed where they are:

| light | colour | wired to |
|---|---|---|
| battery | white, red, blue | the PMIC's RGB LED (`sc27xx-bltc`): its *green* channel is white |
| network | blue | GPIO 117 (`net_blue`) |
| network | white | LDO VDDCAMA0 |
| network | red | the PMIC's keypad backlight sink (`keyboard-backlight`) |
| Wi-Fi | white | LDO VDDCAMA1 |
| Wi-Fi | blue | LDO VDDCAMA2 |

The device tree's six other GPIO LEDs (`pwr_green`, `net_red`, `net_green`, `net_white`, `wifi_blue`,
`wifi_white`) light nothing; they come from a ZTE board file shared with other products, and Android never writes
them either. The lights that never went out were the LDO ones: ZTE's `zte_ldo_leds` switches the camera supplies
VDDCAMA0-2 (3.3 V) through `vddcamaN_status`, mainline had no consumer for them, and `regulator_ignore_unused`
kept the bootloader's "on". `leds-zte-ldo` (upstream/port) makes them `zte-ldo0..2` and starts them dark, and
`leds-sc27xx-kpled` (after Unisoc's keypad driver, current mode) brings the red back under its Android name. What
each colour means was read from Android: sampling `/sys/class/leds` with the radio off shows the network LED
cycling red / white / off, and `vddcama0` lit on 5G. mu300-led now follows it (blue on 4G, white on 5G, red
without service); `mobile-data` reads the access technology (`AT+COPS?`) again on every watchdog round, since it
changes under a live connection.

The PMIC's LED also carries the heat alarm (`thermal-guard`, now running on 5.4 too, where it only watches): one
colour at a time, since the white outshines red and blue when they are mixed.

### 33g. The U30 Air's NFC tag

The device tree's `st,st21nfc` at `i2c@2260000` 0x08 is a leftover: nothing answers there. The tag is a Fudan FM11NT08
dual-interface EEPROM at 0x57 on the same bus (I2C bus 2), which answers only while GPIO 190 (ZTE's
`ntag-reset-gpio`, driven by its `fm11tag` module under 5.4) is low; GPIO 127 is its field-detect interrupt. The
memory is NTAG-like: UID and lock bytes, the capability container `e1 10 6d 00` (872 bytes of NDEF), then the NDEF
TLV from 0x10. ZTE's `Fm11ntagService` writes a WSC Wi-Fi record (WPA2-PSK, AES) with the hotspot's name and
password; `mu300-nfc wifi` writes the same bytes (compared page by page against ZTE's: nothing to write).

A phone got nothing from the tag at first, under Linux and under Android alike, although the field-detect interrupt
counted every tap. ZTE's web interface had NFC off (`settings global webserver_nfc_switch_status=0`, the factory
state of this unit); switching it on there once made the tag answer phones from then on, under Linux too, so the
switch is kept in the chip, presumably in its configuration block at 0x3b0 (which also holds the I2C address).
The data area was unchanged by it: reading all 1 KiB with the switch on and off, the only difference is bit 5 of
byte 0x3bf (0x20 set: off), in the configuration block at 0x3b0 that also holds the I2C address (0x57 at 0x3b3).
`mu300-nfc on|off` changes that bit alone. With it on, URLs and text written by `mu300-nfc` reached a phone; a
Wi-Fi record joins Android phones, while iOS reads URL records by itself but does nothing with a WSC record.

### 33i. USB ping is not the Wi-Fi latency baseline

On the 6.18 trial boot, Windows 11's `UsbNcm.sys` on the high-speed (480 Mb/s) gadget link pinged
`192.168.77.1` at 3–7 ms (20 packets, mean 4 ms); the device pinged Windows at
0.924–6.362 ms (20 packets, mean 4.586 ms). The same device pinging its own
`192.168.77.1` was 0.062–0.437 ms (mean 0.280 ms), with no loss in any series.
The gadget NCM transmit aggregation timeout in the 6.18 source is 300 microseconds,
so it cannot by itself explain a consistent 4–5 ms. The extra time lies around
the USB transfer and host path, not the bridge or Linux IP response: simultaneous
`tcpdump` on `usb0` measured just 0.130–0.176 ms from request ingress to reply
egress while Windows reported 3–6 ms RTT. This does **not** establish a common
IP-stack delay that would also explain the phone's 10–15 ms Wi-Fi RTT.

CPU0 deep idle is, however, a demonstrated contributor to USB RTT. At 100 ms
intervals, disabling the 20 ms `cluster-pd` cpuidle state on all CPUs reduced
Windows USB RTT from 4.375 to 2.325 ms (40 packets each); restoring it returned
RTT to 4.475 ms. Disabling that state only on CPU0, where dwc3 and WCN IRQs
actually execute, yielded 2.625 ms; disabling CPU0's shallower `core-sleep-lit`
as well yielded 2.325 ms. At the normal one-second ping interval, CPU0
`cluster-pd` disabled yielded 1–4 ms, mean 2 ms (12 packets). All cpuidle
settings were restored after the tests. Whether this also improves Wi-Fi RTT
needs a phone-side comparison; a previous CPU0-only Wi-Fi test showed no clear
benefit. Permanently disabling deep idle trades standby power for latency and
is not yet a chosen fix.

Removing two misleading SC2355 idle-path log messages (`to free list empty` and
`out of time` between normal TX completions) eliminated those messages in the
live 6.18 driver, but the user's phone ping did not noticeably improve. This is
log-overhead cleanup, not a demonstrated Wi-Fi latency fix. The 6.18 driver
must not be hot-unloaded while hostapd is active: an earlier `wifi down` returned
before hostapd exited, and unloading it then caused a hostapd cfg80211 use-after-free
and a panic. Replace the module file and reboot instead.

A second controlled check disabled `wlan0` RPS temporarily (`04` → `00`), keeping
the hotspot up. The phone reported no meaningful RTT change, so RPS was restored
to `04`; there is no reason to persist that setting. During this check, 20 ICMP
request/reply pairs captured on `wlan0` spent only 0.15–0.33 ms between Linux RX
and Linux TX. As on USB, most measured RTT is outside the Linux IP response path.
At the time, `wlan0` had zero link errors/drops and CPU was mostly idle. The
two media have not been shown to share a single larger bottleneck: CPU0 deep
idle explains about 2 ms of the USB RTT, but the phone did not show a comparable
Wi-Fi gain when it was disabled. Do not attribute the remaining Wi-Fi 10–15 ms
to RPS, flow offload, or the Linux bridge without new evidence.

### 33j. USB NCM short-frame latency, fixed in the 6.18 trial

The gadget NCM function delayed an underfilled NTB for 300 microseconds and
completed it from a soft hrtimer callback. Although the timer value is short,
the actual short-frame path added several milliseconds on this device. The
`0008-usb-ncm-flush-small-frames.patch` trial packages frames of at most 256
bytes immediately, leaving the ordinary aggregation window for larger frames.
It changes neither USB enumeration nor the early DHCP/host-lease recovery path.

On the same Windows USB NCM link, 100 ICMP pings at 100 ms intervals went from
mean 4.42 ms (40-packet pre-change baseline, 2–7 ms) to mean 1.02 ms (100
packets, 0–3 ms), with no loss. The device's cpuidle settings were both back
to their defaults (`state1/disable=0`, `state2/disable=0`) for the patched
measurement. At the standard one-second interval, 20 pings were all at most
3 ms and most were under 1 ms. `iperf3` on the USB link transferred 321 Mbit/s
host-to-device and 342 Mbit/s device-to-host using two streams, with no TCP
retransmits reported in the reverse test. USB came back automatically after
the kernel-only trial boot; no manual replug was needed. The user's Android
default-boot choice and five-failure fallback were restored after validation.

### 33h. A trial guard that outlived its experiment, again

The U30 Air restarted about every ten minutes after coming back from Android (`su -c mu300-linux`). init said
`stage=trial-guard 600s`: the device segment of boot_b still held the guard of an old `--trial-guard 600`
experiment, and `mu300-update` keeps that segment. 33e made init honour a guard only in a trial boot, but a boot
from Android with mu300-linux is exactly that. The generic ramdisk segment, which every update appends behind the
device segment, now carries an empty `etc/mu300-trial-guard` (a later file replaces an earlier one), and an
experiment's guard goes into a segment of its own behind the generic one.

## 2026-10-13: Mobility renewal gaps in the OpenWrt protocol

The existing event monitor was only started for dual-stack contexts. IPv4-only
connections therefore missed in-place address/DNS renewal, while the watchdog
redialled when CGPADDR differed from the kernel address. Both families now use
the same event owner; the watchdog requests renew for an active context and
does not confuse failed AT reads with detach or radio-off. ACT/DETACH/PCO hints
are coalesced without dropping hints during cooldown. Persistent explicit
inactivity still requests netifd recovery after bounded confirmation.

Replacement addresses are installed before old ones are withdrawn; failed
updates retain the previous snapshot for retry. External IPv6 reporting and
finite RA lifetimes remain intact. There is no ModemManager registration
timeout here, so the E5/T551M 45-second change was not copied.

20 focused checks passed, including dash/bash/BusyBox renewal tests; adjacent
IPv6 regressions passed with dash/bash (35 combined checks). No MU300 device
was deployed or tested for this change. See
[implementation and validation](CELLULAR-MOBILITY-20261013.zh-CN.md).

## 2026-10-13：对齐 T551M 发现的 Wi-Fi 接入缺陷

MU300 的 AP/GO RX 同样把网络字节序 h_proto 与主机字节序协议常量直接
比较，导致 IP 组播进入额外复制转发分支。已修正主线共用源码，并补齐
5.4 构建补丁；保留单播 W2W、非 IP、桥接与 IPv6。

PCIe/SDIO 的过期客户端丢弃和组播 LUT 查找日志收敛，上层 TX 错误限速，
队列、credit 和 skb/message 释放路径不变。现有 WCN 事件恢复保留，补上
down 后复查 disabled，避免恢复期间用户关闭 Wi-Fi 仍被重新打开。

34 项 RX/TX/WCN/蜂窝相关测试通过，全部 5.4 Wi-Fi 补丁从原始厂商源码
实际应用成功。未部署 MU300 实机，不能把 T551M 的现场结果当作 MU300
验证。详见 [Wi-Fi 对齐记录](WIFI-FORWARDING-20261013.zh-CN.md)。

## 2026-10-14：公共 eMMC 策略与默认启动提示

旧 V50/AW9523B 门槛改为公共非可移除 eMMC 策略，禁用 runtime/system sleep、
内外 CLK_AUTO，并按 E5 既有路径以刷缓存实现 FUA 持久化。TF/SDIO 保留原策略。
6.18.54 与 7.2.8 的最终 Image/modules 编译通过；新完整策略尚未刷入实机。

看板按保存的启动意图提示临时 OpenWrt，用户确认后设置默认，不立即重启。
启动控制写入加入校验、失败回退和 rearm 互斥。V50 已热更新并正确显示默认 Linux。

V50 当前 p76 的 EXT4 8 GiB 与分区长度一致，旧 #17 宿主 on/active、错误计数 0。
旧 inode 不一致属于元数据版本不一致，不能把 fsck、短时稳定或 runtime PM 关联
当作唯一底层根因。M8 的已确认铃声损坏原因也不能混作 inode 故障证据。
详见 [两仓库报告](DUAL-SIM-BOOT-EMMC-20261014.zh-CN.md)。

后续按用户要求撤销公共 eMMC 运行时常驻和 CLK_AUTO 禁用，恢复原有运行时省电。
系统休眠保护改为专用回调，FUA 兼容保留；构建缓存一并迁移。实机仍运行旧 #17，
本次未刷内核或切换运行时 sysfs 策略。

### 2026-10-14：F50 / V50 共用固件的机型隔离

旧 `install.sh` 会把 V50 当成未识别设备，用户确认后写入 f50 标记；
initramfs 又优先相信该标记。现与桌面安装器对齐 V50/MU3351/ums9620 校验，
启动和运行时均以本机设备树为准。跨机恢复配置在 AT 服务启动前纠正卡槽及映射，
同机型自定义设置与短信/流量/APN 数据保留。网页灯控仍按实际 LED 节点隔离。
V50 更新要求包明确支持，主线包和共用 TF 包检查内置 AW9523B 支持。
详见 [V50 记录](V50-MU3351-20261014.zh-CN.md#f50--v50-共用固件的机型隔离)。
本轮仅修改代码与回归验证，未重新编译、打包或刷机。

## 2026-10-14：六项上游修复的公共适配

按 #199 / #184 / #217 / #190 / #211 / #228 移植权限、TF 检测、升级锁、温控保护、转发结果及内核/打包检查。保留机型边界和现有双卡后端；E5 无相同升级保活链路，因此不引入 MU300 更新器。E5/T551M 原厂虚拟温区同类提前返回问题也纳入公共修正，M8 同步。正常导入树不重复改权限；不预装 Docker，不增加常驻 AT/温控轮询。详见 [范围与验证](UPSTREAM-BACKPORT-20261014.zh-CN.md)。尚未统一构建安装包或部署实机。


## 2026-10-14：eMMC DMA/MMIO 排序与 E5、V50 实机验证

公共 SDHCI 适配器改用有序 MMIO 访问器，修补 ADMA 描述符提交与完成读取之间的排序缺口；保留寄存器特殊处理、HS400 和运行时省电。E5 的修复前块记录存在另一 inode 表的完整内容，E5/V50 旧驱动普通文件测试均曾触发 ADMA 错误，不能仅按断电未刷盘解释。

E5 完成 896 MiB 写入及等量直接读回、3584 次元数据更新、重启后 224 MiB 校验和两遍离线只读 fsck。V50 按用户要求重装现有 8 GiB OpenWrt 分区、重新提取本机固件，完整系统两轮 256 MiB 写读、1024 次元数据更新、重启后 64 MiB 校验通过。两机保持 HS400/200 MHz，错误计数为 0、运行时休眠启用；不把有界测试宣布为所有硬件永久可靠。V50 曾有本次部署漏同步模块导致的 BTF 不匹配，以及旧根目录脱落导致基础程序缺失，已在报告中分别记录。详见 [证据、范围及限制](EMMC-DMA-ORDERING-20261014.zh-CN.md)。未统一构建发行包，T551M/M8 未实机部署本次内核。


2026-10-14 补齐排序修复覆盖：E5/T551M/M8 的三个机型内核入口及公共 Docker/本机交叉编译、MU300/F50/V50/U30 Air 两条主线版本均接入公共适配器；另补入两仓库旧 5.4 补丁链，不带入主线专属 PM/FUA 改动。发布 CI 增加实际访问器 C 编译执行检查。实际 6.18、7.2、原厂 5.4 参考驱动测试通过；尚未逐个重编和升级全部机型，详见同一调查报告的构建覆盖表。

## 2026-11-09：切换频段后残留 ACS 信道列表

参考 [上游 PR #432](https://github.com/dikeckaan/mu300-linux/pull/432)，维护分支也缺少
按当前频段过滤 hostapd chanlist 的步骤：初次设置的 5 GHz 非 DFS 列表会在 LuCI
切换至 2.4 GHz／自动信道后保留，使 ACS 找不到可用信道。现将过滤加入两条
内核发行线共用的 wifi-scripts-no-dfs.patch，并在 rootfs 构建中检查调用已接入。
使用所选频段的实际 wiphy 频率过滤，保留合法项和范围；全部无效则去掉临时
chanlist，让 ACS 在该频段正常选择。原 UCI 配置不覆写；原来的 DFS 回落至
36、频宽保留和法规检查逻辑不变，没有照搬上游不同的 DFS／带宽策略。

另拒绝畸形、多端点或倒序范围。使用真实 OpenWrt 25.12.5 feed 的 wifi-scripts
及假 wiphy 离线生成配置，20 项信道回归通过，涵盖 5g→2g、2g→5g、混合
列表／范围、合法列表、HT40 和原有 VHT80／DFS 行为；stock／SAE 两种补丁
布局兼容测试通过，构建 shell 语法检查通过。结果属于离线验证，未改实机、
未重新编译内核或打发布包。任务日志：
out/build-logs/20261109-045642-wifi-chanlist-regression-90884/command.log。

同日按用户要求，LuCI 共用频率控件屏蔽不支持的 VHT160／HE160／EHT160
及 80+80 选项，保留 20／40／80 与 N／AC／AX。limit-wifi-widths 接入两条
内核共用的构建、插件安装、uci-defaults 和现有 UI 服务，不引入轮询。
不修改 UCI；旧宽度作为隐藏禁用占位保留并在保存时提示改选，避免自动
清空或降档。4 项真实 LuCI 控件、幂等与失败不写入离线回归通过，日志
out/build-logs/20261109-051406-wifi-width-choices-4119/command.log。
