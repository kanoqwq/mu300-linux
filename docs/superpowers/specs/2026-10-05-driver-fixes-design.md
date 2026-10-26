# Driver and kernel-module fixes

Date: 2026-10-05. Status: approved in conversation (the fourth piece of work taken from `kanoqwq/mu300-linux`,
after the SD card, the Magisk module and the fork's OpenWrt); decisions below were taken without the user, who
asked for them to be taken and written down.

## Inputs

* The overnight U30 Air test of 2026-10-05 (Ubuntu 26.04, kernels 7.2.8, 6.18.55, 7.2.9; Pixel 5 as a Wi-Fi
  client): seven defects, D1-D7, plus one driver limitation (D8).
* The fork's driver and module changes: `git diff 1a69a41 kanoqwq/clean-tf-7.2 -- upstream/modules kernel/patches
  upstream/patches upstream/port boot/init`, and its FINDINGS entries (31f addendum, 33i, 33j).

## Rules

* One item per fix: evidence, change, test, and an accept/reject criterion decided before the change is measured.
* A kernel or module change is measured on a device, before and after, on the kernel it is about; where the code is
  shared (6.18 and 7.2 build the same `upstream/modules`) both kernels are built and the device runs both.
* Shell scripts get a failing test first (`tests/`, every shell).
* The 5.4 vendor kernel carries the same vendor drivers in its own tree. Where it has the same bug, it gets the same
  fix as a `kernel/patches/*.patch`, checked to apply and to compile; whether it is measured on a device depends on
  a 5.4 device being free (none was on the night of the work).
* Diagnostic code (module parameters that count or time packets) is not taken: it is useful while looking for a
  cause and stays in the fork's history for that.

## The items

### 1. sipa_dele: an "already active" power domain taken for a failure (D1)

Evidence: the 6.18.55 panic of 01:35 (ramoops): 855 lines `sipa_dele get pd fail ret = 1`, then a soft lockup in
`cp_dele_on_commad` (`dele-4-5`) and the softlockup panic. `pm_runtime_get_sync()` returns 1 when the device is
active already; the vendor loop treats any non-zero value as failure, drops the reference and tries again every
millisecond, for ever. The same code is in 6.18/7.2 (`upstream/modules`) and in the 5.4 tree. A running 7.2.9 boot
logged 32 ENABLE requests in 24 minutes, so the window is not only at shutdown.

Change: the fork's version (taken). `pm_runtime_resume_and_get()` (0 or a negative error, reference dropped on
error), a failure is answered with `SMSG_VAL_DELE_REQ_FAIL` instead of a busy loop, and a repeated ENABLE while
the reference is held does not take a second one; DISABLE only drops a reference it holds. 5.4: the same, as a
kernel patch.

Test: 20 reboot cycles on 6.18 (where it was seen) with the fixed module: no panic, no `get pd fail`, mobile data
up on every boot; a few cycles on 7.2. Accept if 0 panics and 0 `get pd fail` in 20; any soft lockup in
`cp_dele_on_commad` rejects it.

### 2. sipc: the debug devices created once per SIPC device (D2)

Evidence: 12 call traces per boot on every kernel: `sysfs: cannot create duplicate filename '/class/smem'`, and
`smsg`, `sbuf`, `sblock`. `sprd_ipc_probe()` calls `smem/smsg/sbuf/sblock_init_debug()` at the end of every probe
(four SIPC devices); each creates a class of a fixed name and a single global device. The first probe succeeds,
the next three fail (and leak a chrdev region and a cdev each). 5.4 has `CONFIG_DEBUG_FS=y` and the same calls.

Change: each `*_init_debug()` returns at once when its device exists already. 5.4: the same, as a kernel patch.

Test: boot both kernels; accept if `cannot create duplicate filename` = 0 and the four `/dev/sipc_*` debug nodes
still exist once; the modem still registers (mobile data up).

### 3. Wi-Fi RX: a checksum the firmware got wrong becomes a "hw csum failure" (D3)

Evidence: one `br-lan: hw csum failure` per boot on every kernel, when a client joins. Captured on 7.2.9 this
time: an IPv4 DHCP DISCOVER from the Mac, `ip_summed=2` (CHECKSUM_COMPLETE), software checksum good. The driver
hands every non-IPv6 frame up as CHECKSUM_COMPLETE with the firmware's 16-bit sum, and verifies IPv6 itself,
dropping the frame when the sum does not match. So a wrong firmware sum costs a stack dump for IPv4 and a silently
dropped frame for IPv6 (IPv6 fragments - next header 44, which the sum cannot cover - always fail and are dropped).

Change: verify, never trust and never drop. For IPv4 and IPv6 TCP, UDP and ICMPv6 the driver checks the firmware's
sum against the pseudo-header (lengths from the IP header, every header bounds-checked against the frame); a match
is CHECKSUM_UNNECESSARY, anything else CHECKSUM_NONE, and the stack verifies and drops it if it really is bad.

The fork sets CHECKSUM_NONE for every frame (software checksums everything). Rejected in favour of this unless the
measurement says otherwise: it gives up the firmware's work on every good frame to avoid one bad one.

Test: client joins (the Pixel leaving and joining 10 times, plus the Mac), `hw csum failure` count; Pixel -> device
TCP throughput before and after (200 MiB, 3 runs each); IPv6 from a Wi-Fi client (ping6 and an IPv6 TCP
connection to the device). Accept if 0 csum failures, throughput not lower by more than the run-to-run spread,
IPv6 working.

### 4. Wi-Fi: the fork's WLAN changes

| change | decision |
|---|---|
| `pcie.c`, `rx.c`: drop `to free list empty` and `out of time` (608 and 215 lines in 24 min of a normal 7.2.9 boot) | take: they report the normal idle state as a problem |
| `pcie.c`: restore `mchn_ops` in `pcie_post_init()` (the error path sets it to NULL, a later power-on passes `&NULL[chn]`) | take, and fix the same error path's unwind (it deinits the failed channel and never channel 0); 5.4 gets the fork's patch with the same fix |
| `tx.c`: ARP and DNS from an AP on the data queue instead of one command each (the fork saw the command channel wedge and CP2 assert under an ARP burst) | take with a check: ARP burst from the device to the Pixel before and after, DNS from the Pixel; accept if both work and nothing asserts |
| `reorder.c/h`: the loss-recovery timer is not pushed forward by every out-of-order frame; timeout 100 -> 20 ms | needs measurement: Pixel -> device throughput and ping spread with and without; taken only if neither gets worse |
| `rx.c`, `reorder.c`: latency and BA probes (module parameters) | rejected: diagnostics |
| `rx.c`: CHECKSUM_NONE for everything | rejected, see item 3 |

### 5. USB NCM: short frames sent at once (fork, `0008-usb-ncm-flush-small-frames.patch`)

Evidence (fork, Windows): ping over USB 4.42 ms -> 1.02 ms, 321/342 Mbit/s both ways. Kernel image change (both
mainline kernels). Test on the Mac: 100 pings at 100 ms over USB, and TCP throughput both ways, before and after.
Accept if the ping mean falls and throughput stays within 10 %.

### 6. `mobile-data status` as a user hangs (D4)

`mu300-at` loops on `mkdir /run/mu300-at/lock`; when a stale lock's pid file cannot be removed (not root) the
`continue` skips the wait counter, so it never ends. Change: a client that cannot write the daemon's directory
says so and exits (`needs root`), and a stale lock that cannot be removed counts as busy. TDD.

### 7. Connections reset for 5-10 s after mobile data comes up (D5)

Investigated on the device first (what the VPN does in that window). The least that is done: `mu300-update`'s
GitHub requests retry for a short while, so `mu300-update check` right after boot does not report "could not reach
GitHub" for a transient reset. Root cause in the VPN start, if found, fixed there.

### 8. br-lan's IPv6 link-local changes between boots (D6)

`lan-start` creates `br-lan`, adds `usb0` and sets it up while udev may not yet have given the bridge its
persistent MAC: the bridge then has `usb0`'s MAC when IPv6 makes the link-local, and udev's MAC comes too late.
Change: wait for udev to process the new bridge (`udevadm settle` with a timeout) before adding ports. Test: link-
local and MAC over 5+ boots; accept if constant.

### 9. `mu300-device` and `mu300-led` not on the PATH (D7)

`rootfs-fixups` (which adds the `/usr/local/bin` links on installed systems) has a shorter list than the image
build. One list, with `mu300-device`; a test that the three lists (Ubuntu image, OpenWrt image, fixups) agree.

### 11. The USB identity of two devices restored from one backup (added during the work)

A device restored from another's backup has its `androidboot.serialno`, so its gadget serial number and MACs (33b)
are the other's, and a computer with both gives one of them no network. The identity takes the eMMC serial too
(`androidboot.emmcid`; not the CID in /sys, which init reads before the eMMC is there). Decided for every device, since a clone cannot tell it is one:
each host sees a new adapter once after the update (written in FINDINGS 33j). Test: shell tests of the derivation;
on F50 #1 and the U30 Air the host gets its network on the new adapter.

### Decisions taken during the work

* Item 1: answering a failed ENABLE with REQ_FAIL is kept; what the CP does with it is unknown (FINDINGS 33k).
* Item 3: after review, IPv4 options, IPv6 extension headers and a short UDP length go to the stack.
* Item 4: the post-init unwind takes the failed channel too (and a channel without a pool deinits safely). The
  reorder change is rejected on review (FINDINGS 33k), without the measurement.
* Item 5: taken (ping 4.98 -> 1.46 ms, throughput within 10 %, also both ways at once).

### 10. Not taken now

* `boot/init` USB changes of the fork (single-configuration RNDIS, early DHCP in the initramfs): for Windows,
  which is not at hand to measure; entangled with the fork's slot handling and its 90 s rescue timer (rejected:
  the 300 s timer stays). Recorded as pending.
* `upstream/port/install.py` SD host changes: done by the SD card work.
* D8 (`iw station dump` without signal and rate): the firmware does not report them through this driver; recorded.
