# Review: kanoqwq/mu300-linux, branch `clean-tf-7.2` (2026-10-11)

The fork is MIT, like this repository. The review covers 31 commits after merge-base `1a69a41`, up to `23361ce`: 248 files, about 25k lines. Every change below is compared with our `main` at `7a044d5` (PR #122).

## Classes

- (a) Already in main, the same or better.
- (b) A bug fix we lack.
- (c) A feature we lack.
- (d) Local to the fork: its TF/SD standalone layout, its branding, its installer paths, or diagnostics.
- (e) Unsafe or wrong.

## Summary

- **Much of the fork's runtime and driver work is our own work going round in a circle.** f092671 and 23361ce say "backport upstream", and upstream is this repository. Before them we had already ported its early fixes: ndp-learn (cd4fb28, 89b376d), the sipa-dele ordering, the vendor-start speedups, the copy_missing update fix (f7df326), the early-replay hook and the USB management tests. In several places our version is newer: WoWLAN, PCIe WAKE#, RX checksums that are verified rather than dropped, and ndp-learn without neighbour pins.
- **Ported.** Three driver fixes, two runtime fixes and one panel fix, each commit credited `Based on kanoqwq/mu300-linux@<sha>`:
  - PR #123 `kano-driver-fixes`:
    - TX credit exhaustion is logged at debug level and ratelimited, not as an error (3386928).
    - The WCN reset notifier is registered last, and a failed init unwinds (23361ce).
    - The RX refill producers are stopped before the ring is torn down (23361ce).
  - PR #128 `kano-runtime`:
    - mu300-at and mu300-atd share one request framing rule, the lock wait counts seconds, and the direct path needs a final code (25f8d0c, tightened; see "Security").
    - OpenWrt reopens Wi-Fi once per WCN reset (23361ce).
  - PR #129 `kano-panel`: the home page's Wi-Fi client rows, DNS and connection count (373e191, 5c8c2f9).
- **Deferred, needs a decision.**
  - The CPU voltage offset (the cpufreq driver, see below).
  - `mu300_thermal`, the stock virtual thermal zone with kernel trips; it must be coordinated with our `thermal-guard`.
  - The BTF/eBPF config, which belongs to the F50-B kernel/BTF work.
  - The own-number (MSISDN) readout (0d93695).
  - The verified lock reset (23361ce), which checks the writes and reads them back before the saved locks are deleted. It is the most useful follow-up in the panel area.
  - Independent signal and Wi-Fi lamps (ee10835), which go to the F50 LED roadmap item.
  - Dual-SIM (25f8d0c); compare with contributor PRs #72 and #73 and do not take the fork's.
- **Rejected.**
  - The reorder timeout cut to 20 ms; their own measurements show no gain.
  - The always-CHECKSUM_NONE RX; ours is better.
  - The ICMP latency probe and the TX/EDMA pressure counters, which are diagnostics.
  - The watchdog policy changes (`recovery_reboot` off by default).
  - The neighbour `/128` pins in ndp-learn, a route hijack by spoofed NA (e).
  - The whole TF standalone layout and branding: Aurora theme, r4 zip checksums, the `unisoc-modem` paths, the "slot" installer.

## Security

1. **Proprietary files in the distributed TF zip.** `tools/build-openwrt-tf-magisk.sh` (8b7abb4, later 373e191 and 3386928) packs the builder's own device data into the zip it publishes:
   - in the boot image (`--android-subset`, `--stock-boot dumps/boot_a.img`, `--modules $IN/out/modules`): `$IN/android-subset` (Android `/system` and `/vendor` libraries and binaries, `modem_control`, and the `/dev/__properties__` snapshot, see 2), the builder's stock `boot_a.img` and the vendor modules;
   - in the rootfs through `openwrt/build-rootfs.sh`: `$IN/firmware`, `mu300-keys`.

   This is how the "simui" build (3386928) shipped proprietary Android files and a device property dump. **Nothing of this is ported.** Our release zips stay free of vendor files: each device copies its own at install time.
2. **`tools/mu300-vendor-from-device.sh` (8b7abb4, 35a1c55)** tars a subset of `/system`, `/vendor` and `/apex` plus **`/dev/__properties__`** into the rootfs on the device itself.
   - On a device that is legitimate; our installer does the equivalent.
   - The danger is a build host that runs it and then packages the result, which is item 1.
   - The property area carries the device's serial, IMEI-derived properties and build fingerprints.
   - Not ported, and our `.gitignore` and release checks must keep `android-subset/` and `dev-properties` out of any artefact.
3. **mu300-at parser differential** (25f8d0c, and an earlier pass of our own port).
   - The client accepted `-t` 1-999 while the daemon parsed 0-99. A request such as `120 /run/mu300-at/answer.N AT...` then fell back to "the whole line is the command" and went to the modem.
   - CR and LF framing differed between the paths.
   - Fixed in PR #128: one `one_request` function in both scripts, a strict daemon parse, and the answer file restricted to `$DIR/answer.<digits>`. A CR is allowed only in `AT+CMGS=<n><CR><hex><Ctrl-Z>`.
4. **ndp-learn neighbour pins** (cd4fb28, 89b376d, 23361ce). A `/128` route goes to br-lan for every global address in br-lan's neighbour table. Any LAN client can spoof an NA and pin another client's address, or an internet host's, to the LAN. Our port leaves the pins out, and the fork still has them (e).
5. **Third-party binaries in the image.** The Aurora theme and config APKs (5e301d4, 373e191) are downloaded from GitHub releases, pinned by sha256 and installed with `apk add --allow-untrusted`. Pinning makes this reproducible, but it adds code we do not review into the root web UI (d, not ported).
6. **SMTP secrets in the forwarder.** The `sms-forward` UCI holds `smtp_password`. Their tests use dummies only, and no real credentials are in the diff. Our `forward` already never returns secrets to the page.

A grep over the diff found no real IMEIs, keys or passwords. The only binaries are two `.lmo` translation files.

## Classification

| commit | area | class | decision | notes |
|---|---|---|---|---|
| 8b7abb4 | TF standalone deployment for 7.2: `mu300-openwrt-tf` Magisk, `build-openwrt-tf-magisk.sh`, `mu300-vendor-from-device.sh`, boot/init SD root, Dockerfile, install.py | d / e | not ported | Their own deployment flavour. The packaging bundles proprietary inputs (Security 1, 2). The SD root path is superseded by our SD support (mmc_disks by type, sd_candidates). |
| d7347ca | boot/init: poll the SD host before falling back to the eMMC; losetup probe; **safety timer 300 s → 90 s** | a / d | not ported | Our boot/init already polls the SD host by card type, which is more robust. busybox dd `skip` seeks, so the losetup "probe speedup" gains nothing. The 90 s fallback timer is their choice; a slow rootfs would bounce to Android. |
| cd4fb28 | fast F50 boot, modem, Wi-Fi LED, SMS platform: android-vendor-start, sipa-dele ordering, wifi-start insmod, mobile-data, mu300-sms/smsd, led-status, mu300cell.sh, preinit early USB, pcie post-init retry | a | none | Ported earlier: vendor-start without fork storms, sipa-dele loaded by mobile-data, early USB, pcie post-init retry. Our SMS pool (PR #112) replaces their mu300-sms. Their led-status goes to the F50 LED work. |
| 6404c10 | pre-radio hook for standalone lock replay | a | none | `tests/test_early_replay_hook.py` is in main. |
| a366a89 | the standalone LuCI app bundled in the TF rootfs (their `unisoc-modem` app) | a / d | none | Our luci-app-mu300 grew out of the same app and has since diverged. It has 30+ languages, rpcd security tests, power, ttl and languages pages. |
| 1f5b82a | TF USB handoff, `mu300cell-v6.sh` | a / d | none | `openwrt/luci-overlay/lib/netifd/proto/mu300cell-v6.sh` is in main (with the +CGEV handling). The TF handoff in boot/init is TF-only. |
| de8381b | TF Magisk password-neutral | d | none | TF installer. |
| 7e0acce | **wifi: AP ARP and DNS off the firmware command queue** (tx.c) | a | none | Identical code in main, including the later move of the DNS check before the DHCP log. |
| 45222e6 | sipa_dele_cp_in_ap resume_and_get; drop the "to free list empty" log; NCM small-frame flush; fw4 sipa offload; console sysctl | a | none | Main is better: runtime-PM enable before start, the 5.4 patch, NCM flush with U30 Air numbers. |
| 45222e6 | reorder.c: no re-arm while pending, **timeout 100 → 20 ms**, BA counters | e / d | rejected | No evidence it helps; their own FINDINGS 33i say the RTT did not improve. 20 ms is far below mac80211's 100 ms and releases gaps too early. |
| 45222e6 | rx.c ICMP latency probe | d | rejected | Debug only. |
| d27296c | slot-aware "system switch" WebUI plugin for KernelSU/Magisk WebUI | d | not ported | Drives their A/B "slot" installer (35a1c55). We have no slot concept, and switch.sh is our own. Shell command strings are built in JS: review it before any port. |
| 5e301d4 | Aurora theme bundled; dual-kernel TF audit; make-release | d | not ported | Branding and third-party APKs (Security 5). |
| 89b376d | ndp-learn keeps the IPv6 client routes; rx.c checksum to CHECKSUM_NONE | a / e | none | ndp-learn ported earlier without the neighbour pins (Security 4). Our verified `rx_l4_csum_ok` beats always-NONE. |
| 040e44c | docs: rebuilt WLAN modules | d | none | — |
| 265f4de | build: reject stale WLAN modules (source newer than .ko) in Magisk packages | c (small) | not ported | Sensible for local builds. Our CI builds modules from scratch, so the check guards nothing there. Worth a line in `make-bundle.sh` if we ever package hand-built modules. |
| 4e70d02 / 22a05c9 / efa034d | USB device management page, gadget modes, deferred role switch, exec bits | a | none | Main has `device-usb`, `hotplug.d/*/90-unisoc-usb-host`, `lib/mu300/usb-host.sh`, the boot/init usb-net mode (D12) and `tests/test_usb_management.py`. |
| ee10835 | independent signal and Wi-Fi lamps; LuCI `leds.js` override | c | deferred | Goes to the F50 LEDs roadmap item. Their `leds.js` replaces a core LuCI view (d); take only the lamp semantics. |
| 35a1c55 | installer hardening, A/B slot boot, dashboard settings, locale | d | not ported | Their slot layout (`mu300-next-boot` slots, `build-boot-image.py` slot args). The installer parts we have are covered by our own installer tests. |
| 373e191 | TF install worker, staging, payload manifest; traffic and SMS-forward pages; SMS capacity; container kernel config | a / d | partly ported | Traffic counter: main has it (PR #113). SMS forward: main (PR #112), with secrets never returned. Container config: in main (59e17d5, 765da29). TF tools are d. **Ported: station rows, leases, conntrack in dashboard-info (PR #129).** |
| 0d93695 | own number (CNUM / IMS SPPAURI) outside the identity cache | c | ported | **Ported (branch `panel-own-number`, FINDINGS §49):** CNUM on full rounds only, the IMS identity as the fallback, a number found kept in RAM for the boot, never in the fast tier, never on flash, masked in the panel like the IMSI. Device test on the U30 Air pending. |
| f092671 | "backport upstream" Wi-Fi and installer fixes: wcn power serialise, mbuf deinit, armlog, region bounds, RNDIS handoff | a | none | These are our own fixes (bde23d0, 486856c, 1f6e825, ...), identical in main. |
| 50a7fcd | dashboard staged refresh (`refresh.js`), SMS layout from "E5", `dashboard-rates` | a / c | not ported | We have live rates (PR #113, `mu300rates.uc`) and our own refresh tiers (fast and full in `cell`). The staged visual refresh is cosmetic; revisit with a panel redesign. |
| 85d179c / d882579 / 9fac259 | dashboard backport notes, lock-refresh indicator placement | d | none | Their layout. |
| db4e5d2 | "verified controls": lock jobs (`lock-apply.sh`, `lock-jobs.sh`), hotspot client view, traffic clear, USB LAN management | a / c | not ported | Lock reset: main (PR #99). Traffic clear: main (`traffic set clear yes`). USB LAN: main. The asynchronous lock job queue is a larger refactor of `lock`; ours applies synchronously under the rpcd budget. Hotspot clients: the home page shows them (fixed in PR #129). |
| 25f8d0c | dual-SIM controls (`mu300-data-sim`, `mu300-dual-radio`, `sim-scope.sh`), CPU page, mu300-at client boundary | c / a | partly ported | Dual-SIM: wait for contributor PRs #72 (F50) and #73 (U30). CPU profiles: main (PR #111). **Ported: the mu300-at fixes, tightened (PR #128).** |
| 3386928 | **CPU voltage offset** (cpufreq-v2 driver, `cpu-voltage-platform`, S08 module load), forwarding profiles, Wi-Fi log flood, `mu300-update` vendor copy | c / b / a | partly ported | Voltage: deferred, see below. **Ported: flow-control logs to debug, ratelimited (PR #123).** TX/EDMA counters (d). The `mu300-update` copy_missing is in main. Making cpufreq `=m` delays all DVFS until S08. |
| 5c8c2f9 | kernel capabilities (BTF, eBPF, ftrace, kprobes, perf), `mu300_thermal` module, CPU thermal page, thermal-guard | c | deferred | The BTF/eBPF config belongs to the F50-B kernel/BTF work. `mu300_thermal` is clean GPL, but it switches on kernel passive trips (70 and 85 °C) and a 110 °C critical trip next to our `thermal-guard`, so it needs coordination and a device test. NETFILTER_XT_NAT and IPVTAP `=m` are harmless additions for the BTF/config PR. |
| 23361ce | "verified lock reset", ra-deprecate, ndp-learn, wcn-reopen, driver recovery (iface.c, rx.c, mm.c, mchn.c, procfs) | a / b / d | partly ported | ra-deprecate, ndp-learn withdrawal and driver recovery are ours (#100, #104, #106). **Their "verified lock reset" is better than our `lock reset` (c, recommended follow-up).** It checks every AT write. It reads SPTESTMODE, EN-DC, bands and cells back before it commits. It deletes the saved locks only after a verified reset, and a `reset_pending` tombstone finishes an interrupted cleanup at the next replay or apply. Ours deletes the saved settings whether or not the writes took. Not ported here: it sits on their asynchronous lock-jobs refactor (db4e5d2) and needs a device test. **Ported: notifier registered last with unwind, RX refill stop before deinit (PR #123), and one Wi-Fi reopen per reset (PR #128).** `recovery_reboot` default-off is rejected (policy). |

### Driver and kernel hunks in detail

| sha | area | class | decision | notes |
|---|---|---|---|---|
| cd4fb28 | wlan pcie.c post_init: restore mchn_ops (+5.4 patch) | a | none | In main, with an extra chn..0 unwind. |
| 7e0acce | tx.c AP ARP/DNS bypass the command queue | a | none | Identical. |
| 45222e6 | sipa_dele_cp_in_ap.c resume_and_get | a | none | Main also does runtime-PM enable, plus the 5.4 patch. |
| 45222e6 | reorder.c/h timer, 20 ms timeout | e / d | rejected | See above. |
| 89b376d | rx.c CHECKSUM_NONE always | a (ours better) | rejected | Ours: CHECKSUM_UNNECESSARY when verified, never a drop. |
| f092671 | wcn-power-serialise, mbuf deinit NULL, armlog off, 5.4 patches | a | none | Ours. |
| 3386928 | `fc_test_send_num` and `_fc_ no credit` pr_err → debug ratelimited | **b** | **ported (PR #123)** | Fires on every TX pass under load. |
| 3386928 | atomic pressure counters, module params, 5.4 diagnostic patches | d | rejected | Diagnostics. |
| 3386928 | cpufreq-v2 voltage offset, install.py tristate + `EXPORT_SYMBOL_GPL(topology_set_freq_scale)`, `ARM_SPRD_CPUFREQ_V2=m` | c | deferred | See below. |
| 5c8c2f9 | `upstream/modules/mu300_thermal` | c | deferred | See above. |
| 23361ce | cfg80211 cond_resched, ratelimited refill logs, refill back-off, `wcn_mu300_recover`, mdbg lock deadline, assert-path mutex leak, FLUSH_BUFFER | a | none | Ours (#100, #104). |
| 23361ce | iface.c `iface_notify_init`: reset notifier registered last, full unwind | **b** | **ported (PR #123)** | A failed init left the notifier on freed priv, and `misc_register` errors were ignored. |
| 23361ce | rx.c/rx.h `stopping` flag, teardown order in `sc2355_rx_deinit` | **b** | **ported (PR #123)** | Producer and timer race against a freed `rx_mgmt`. |
| 23361ce | watchdog `recovery_reboot` default false, no requeue after give-up | d | rejected | Policy. Ours keeps rebooting by default and keeps reporting after a give-up. |
| 373e191 / 3386928 / 5c8c2f9 | build-modules.sh x86 cross-compile, BTF checks, manifests, `check-container-support.py`, KV 6.18.54 | d / c | not ported | Their KV is older than ours. Hand the BTF manifest idea to the BTF work. |

### Kernel config changes

- **373e191** (KVM, VHOST, VSOCK, IKCONFIG, BINFMT_MISC, PSI, cgroup controllers, CFS_BANDWIDTH, BLK_DEV_THROTTLING, BRIDGE_NETFILTER, xt matches, CONNTRACK_MARK and ZONES, IP_VS, DUMMY, MACVTAP, IPVLAN, VXLAN, FUSE): **(a)**, all in main already.
- **3386928** `ARM_SPRD_CPUFREQ_V2 y → m`: **(c) deferred**. It is only needed for a voltage module parameter. It delays DVFS to userspace and needs a mainline export patch.
- **5c8c2f9** DEBUG_INFO_BTF, BTF_MODULES, KALLSYMS_ALL, FTRACE and the function and graph tracers, DYNAMIC_FTRACE, FTRACE_SYSCALLS, KPROBES and KPROBE_EVENTS, UPROBES and UPROBE_EVENTS, PERF_EVENTS, ARM_PMU, BPF_JIT, BPF_EVENTS, BPF_STREAM_PARSER, BPF_UNPRIV_DEFAULT_OFF: **(c)**, owned by the BTF work (`mu300-mainline-btf`).
- **5c8c2f9** NETFILTER_XT_NAT and IPVTAP: **(c) minor**, harmless `=m` additions; fold them into the same config PR. The sch, cls, act, IFB and GENEVE options are already in main.

## The CPU voltage-offset driver (`upstream/port/drivers/cpufreq/sprd-cpufreq-v2-driver.c`, 3386928)

Read this before starting our deferred voltage feature.

- **License.** The file keeps Unisoc's `SPDX-License-Identifier: GPL-2.0`. The fork's ~76 added lines are GPL-2.0 with it, so they can go into our port tree.
- **Interface.** A module parameter `voltage_offset_uv`, an array of 3 ints with perms 0444. It is set only at load (modprobe options, or the cmdline when built in), and nothing can change it at runtime. The driver adds read-only cpufreq attributes:
  - `scaling_voltage_table`: kHz/µV pairs through the firmware's PAIR_GET;
  - `scaling_voltage_offset`;
  - `scaling_voltage_domain`.
- **How the offset is applied.**
  - In `sprd_cpufreq_probe()`, `sprd_voltage_flag()` folds the offsets into the `flag` word of the one-time `dvfs_init(flag)` call. That call is the SIP SMC DVFS_INIT (`0x8200060b`) into the SML/ATF firmware.
  - Byte 0 keeps `androidboot.dvfs_set` (debug magic 0x5A), and the vendor's upper bits are cleared.
  - Bytes 1, 2 and 3 are domains 0, 1 and 2. In each, bit 7 is the sign (negative is undervolt) and bits 0-6 are whole mV (`abs(uv)/1000`).
  - The firmware turns that into `ceil(mV*1000/3125)` selector steps and applies them to **both the CPU and the SRAM voltage tables**. PAIR_GET then reports selector × 3125 µV.
  - Linux never touches the PMIC, SML, NV or DTBO itself; the firmware's own DVFS sets the regulators.
- **Clamping.** The offset must lie in −50000…+25000 µV and be a multiple of 3125 µV.
  - A wrong array count gives -EINVAL, and a value out of range or not aligned gives -ERANGE. Either one fails the probe, so the device runs without cpufreq.
  - A non-zero offset on anything but `sprd,ums9620` gives -EOPNOTSUPP.
  - A second probe gives -EBUSY (`firmware_initialized`), and `suppress_bind_attrs` blocks unbind.
- **Default.** All zero. The upper flag bytes are then 0, the same as stock, except that the vendor bootarg's upper bits are dropped.
- **What prevents an undervolt crash.** In the kernel, nothing but the −50 mV floor. The rest is in the fork's userspace (`cpu-voltage-platform`, `S08mu300-cpu-driver`):
  - an SML partition SHA-256 allowlist (one F50 firmware), with non-zero refused on unknown firmware;
  - an "armed" marker written before a non-zero load and cleared on clean shutdown. If it is still there at the next boot, the config is renamed `.rejected` and zero is loaded;
  - the Android trial/fallback guard as the last resort.
- **Risks for us.**
  1. The SRAM rail moves with the CPU rail.
  2. The marker logic works only because the module loads after rootfs (S08). With our built-in `=y` driver, a cmdline offset would apply in early boot with no marker.
  3. `=m` delays all DVFS to S08 and needs `EXPORT_SYMBOL_GPL(topology_set_freq_scale)` in mainline.
  4. The fork verified only +3125 µV on one F50, and back to 0. No negative value was tested, and there was no stability or power-loss test.
- **Recommendation.**
  - Reuse the encoding and the read-only attributes.
  - Keep DVFS built in and apply the offset through a separate post-rootfs path, or accept `=m` knowingly.
  - Keep the SML-hash allowlist and the armed-marker rollback.
  - Start with positive offsets or −3125 µV only, on an idle test device.
  - Per "bootability over lockdown", the rollback must never become a boot gate: a rejected profile boots at zero offset, and nothing refuses to boot.

## What needs a device test

- **PR #123 (drivers)**, on 6.18 and 7.2:
  - boot, AP up and a client joined;
  - 60 s of sustained upload with no `fc_test_send_num`/`_fc_ no credit` lines;
  - 5× `wifi down/up` plus a network restart;
  - optionally an `rmmod`/`modprobe` of sprd_wlan_combo;
  - `echo rebootwcn > /proc/mdbg/at_cmd` recovery.
- **PR #128 (runtime)**:
  - SMS send from the panel and from `mu300-sms send` (the CMGS exception);
  - the panel AT terminal;
  - `mobile-data status`, `ifup wan`, and a lock apply and reset;
  - the direct path with mu300-atd stopped;
  - a WCN reset that gives exactly one reopen.
- **PR #129 (panel)**: the client rows with rate, time, name and IP; the DNS list; the connection count under mainline.
