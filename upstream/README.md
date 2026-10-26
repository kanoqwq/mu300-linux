# Mainline (LTS) kernel on the MU300

Mainline **Linux 6.18.58** (the current longterm series) runs Ubuntu 24.04 and OpenWrt 25.12 on the ZTE F50 /
MU300 (Unisoc UMS9620): all 8 CPUs (4×A55, 4×A76), eMMC, USB NCM + ACM, the 5 GHz hotspot, Bluetooth, the modem
with mobile data (downlink and uplink), SMS, the VPN, the Mali GPU, thermal/cpufreq, the PMIC watchdog and
reboot. `docs/FINDINGS.md` 31-31f has what it took and what is still open.

On a device: `sudo mu300-update kernel 6.18` (or `7.2`, the stable release, or `7.3`, the newest mainline release), and `sudo mu300-update kernel
5.4` back; the installer offers all three. Every kernel's modules stay installed.

Reference: Unisoc's UMS9620 DT series (LKML, 2023-12-15, "arm64: dts: sprd: Add support for Unisoc's UMS9620", not
merged) describes the same GIC/UART/timer layout; this device is derived from their ums9620-2h10 reference board.

## How it boots
* **Device tree:** the stock vendor DTB in `vendor_boot` is used unchanged. Mainline ignores the vendor-only nodes and
  uses the standard ones (cpus, psci, GIC, timer, memory, reserved-memory, ramoops, ADI). A minimal custom DTB
  (`dts/ums9620-mu300.dts`, installed with `mkvendorboot.py`) was *rejected*: after LK's dtbo merge and fixups the kernel
  hung in `setup_machine_fdt`.
* **Load address:** LK always copies the kernel to 0x80080000 (old text_offset); `wrap-image.py` prepends a branch stub
  so the Image runs from 0x80200000 (2 MiB aligned).
* **Reset:** PSCI SYSTEM_RESET never returns on this firmware. `patches/0001-spi-sprd-adi-add-UMS9620-restart.patch`
  adds the UMS9620/UMP9620 variant to `spi-sprd-adi` (also matching the vendor compatible `sprd,qogirn6pro-adi`) and
  resets through the PMIC software reset, registered above the PSCI handler.
* **Logs:** with a working reset the console survives in ramoops and Android shows it as
  `/sys/fs/pstore/console-ramoops-0` (`tools/collect-logs.sh`).

## Build and test
```sh
docker build -t mu300-mainline-build upstream/
docker volume create mu300-mainline   # build.sh fetches linux-$KV (default 6.18.58) into it, signatures checked (keys/)
docker run --rm -v mu300-mainline:/src -v "$PWD/upstream":/work mu300-mainline-build bash /work/build.sh
docker run --rm -v mu300-mainline:/src -v "$PWD/upstream":/work mu300-mainline-build bash /work/build-modules.sh
upstream/make-bundle.sh mu300-kernel-6.18.tar.gz        # Image for LK, generic ramdisk segment, modules
# on the device: MU300_KERNEL_BUNDLE=/path/mu300-kernel-6.18.tar.gz mu300-update boot
```
The build stops on a kernel tarball whose signatures do not check out (`verify-kernel-src.sh`: kernel.org's signed
`sha256sums.asc` and the releaser's `tar.sign`, against the keys pinned in `keys/`, FINDINGS 161) and on anything
that drifts silently: a patch that neither applies nor is applied, a config option
Kconfig does not take (unless listed in `config-ignored.txt`), a port edit whose anchor moved, or a module whose
vermagic does not match the kernel. `tools/make-release.sh` packages the same bundle as `mu300-kernel-6.18.tar.gz`.

The config is allnoconfig plus `mu300-mainline.config`, and then `all-modules.py` (FINDINGS 163): every other
tristate option the boards can use becomes a module - USB devices of every class, filesystems, netfilter, protocols,
crypto, block, media, iio, hwmon - while what the fragment built in stays built in (checked), bools change only where
`all-modules.config` opens a family, and `all-modules.exclude` keeps out test/debug code, PCI cards, and the drivers
of chips only a board's device tree wires up. `check-dt-modules.py` then fails the build if a module would bind to a
node of the vendor device tree (`dt-compatibles.txt`) by itself. `MU300_ALL_MODULES=0` builds the fragment alone.
On the devices `mu300-modprobe-guard` (the kernel's module helper from boot on; FINDINGS 163) keeps the modules of
`/opt/mu300/lib/autoload-blocked` (rare protocols, filesystems, line disciplines) from being loaded on demand;
root loads them with `modprobe NAME`.
In the fragment, what the board and the boot need is built in, along with
KVM (`/dev/kvm`; the CPUs start at EL2); what only matters once used - netfilter extras, tunnels, IPsec, tc,
filesystems, USB modems/adapters/Wi-Fi sticks, device mapper - is a module. `build-modules.sh` installs the
kernel's own modules (stripped) flat into `out/modules` next to the vendor ones, and the bundle ships them all;
`mu300-update` indexes them on Ubuntu (depmod) and OpenWrt loads them by name (FINDINGS 31o). Both kernels carry
BTF (`/sys/kernel/btf/vmlinux`, made by `pahole` from the build image) and the BPF JIT, tcx/clsact, cgroup and
kprobe hooks that eBPF programs such as the dae transparent proxy need (FINDINGS 31p); the DWARF it is made from
stays in `vmlinux`, and `build-modules.sh` strips it from the vendor modules.

The bring-up path of old (hand-built image, `init-bringup`, `boot/flash-trial.sh`) still works for experiments:
```sh
python3 upstream/wrap-image.py upstream/out/Image upstream/out/Image.lk
boot/flash-trial.sh boot-mainline.img      # slot b only, falls back to Android
```

## Debugging without a console
* `stub/pmic-reset.c`: bare-metal PMIC reset used as the kernel entry — proved that LK reaches our code and that the
  PMIC reset works (30 s cycle instead of the ~320 s PM power cut).
* `debug/install-probe.py` (`MU300_PROBE_STAGE=N` for `build.sh`): resets at a chosen boot stage; the cycle time tells
  whether the stage was reached. This located the custom-DTB hang in `setup_machine_fdt`.
* `MU300_CMDLINE_EXTRA="..."` for `build.sh`: kernel arguments appended to the forced `CONFIG_CMDLINE` of a test build
  (the bootloader's command line is not read), e.g. `sdhci_sprd.fail_hs400es=1` (FINDINGS 160). Never in a release.

## Newer kernels (7.x)

The whole port - kernel and all its out-of-tree modules - also builds against **7.2.12**, the newest stable release
(2026-10-15), from the same sources as 6.18, and runs on the device: four boots out of four (2026-09-27) with the
Wi-Fi access point, mobile data (18.7 MB/s), Bluetooth, the Mali GPU, cpufreq and the modem's AT channel working,
and no warning or oops in the log. (7.2 is a stable, not a longterm release: 6.18 stays the base, 7.x is how the
next jump is kept small.) One source serves both:

* `port/install.py` recognises the layout of what it edits (`sprd-sc27xx-spi.c` was reworked in 7.x), and every
  edit is checked afterwards, so a drifted anchor stops the build instead of being skipped.
* The modules get the missing pieces from `modules/wcn_bsp/kinclude`: `mu300_compat.h` (7.x-only shims:
  `strncpy`, alarmtimer, `linux/hex.h`) and `linux/of_gpio.h`, a shim kbuild only finds when the kernel has no
  such header. API changes that need more than a shim are under `LINUX_VERSION_CODE` (the cfg80211 ops taking a
  `wireless_dev`, `dma_fence_signal()` returning nothing).
* `.github/workflows/mainline.yml` builds the port every week against the current longterm, the newest stable and
  the newest mainline release (and the newest of each series the releases ship), so the next break shows up as a red
  run rather than at the next jump.

Releases carry it as `mu300-kernel-7.2.tar.gz`: `sudo mu300-update kernel 7.2` on the device, or choice 3 in the
installer. It is built next to 6.18: `OUTDIR=out-7.2 KV=7.2.12` for `build.sh` and `build-modules.sh`, and
`MU300_UPSTREAM_OUT=upstream/out-7.2` for `make-bundle.sh`; `tools/make-release.sh` packs them all.

**7.3** (mainline, 2026-10-18) is the fourth kernel, from the release after v2026.10.19.2 on: `mu300-kernel-7.3.tar.gz`,
`sudo mu300-update kernel 7.3`, choice 4 in the installer; `OUTDIR=out-7.3 KV=7.3` (its kernel release is `7.3.0`).
Every patch applies unchanged; what 7.3 changed is in FINDINGS 206: `PROC_SYSCTL` folded into `SYSCTL`
(`config-ignored.txt`), the cfg80211 cookie of remain-on-channel and management frames handed to the driver by
value, `dma_capable()` taking DMA attributes, and the IIO consumer API in a symbol namespace (used by the audio
extra).

## Remaining mainline work (as of 2026-09-26)

- The forced command line still carries `clk_ignore_unused pd_ignore_unused regulator_ignore_unused
  fw_devlink=permissive`. `regulator_ignore_unused` keeps the SIM's supply on and `fw_devlink=permissive` the USB
  gadget alive, so both stay; `pd_ignore_unused` changes nothing; `clk_ignore_unused` keeps 315 unused clocks on and
  is the one worth measuring (FINDINGS 31h).
- The early crashes and the missing OpenWrt downlink were one bug, the delegate's (FINDINGS 31f), fixed; one
  Ubuntu boot that died early before the crash capture existed (31d) is unexplained, and has not recurred in the
  reboot loops since (Ubuntu 6/6, OpenWrt 4/4).
- Power-off works through the PMIC (FINDINGS 31h); on a powered USB port the battery-less F50 starts again.

## Earlier status notes
Clocks, pinctrl, power domains, USB 3.1 gadget, eMMC, PCIe, thermal, cpufreq, watchdog, LEDs and Wi-Fi/BT are
working (see the status below). What is still missing:

- ~~**Modem**~~: works since 2026-09-19 - "Modem Alive", AT, SIM, 5G NSA registration and a data context on
  `sipa_eth0`. See "Modem on 6.18" below for what was wrong (shared memory mapped write-back).
- **PM co-processor**: without Android's `modem_control` the board powers off after ~290 s, so the vendor chroot is
  still required.
- ~~**GPU**~~: works since 2026-09-19 (DDK r40p0 ported to 6.18, OpenCL verified on the device).
- **Bluetooth**: built as an out-of-tree module, untested on 6.18.

## Modem on 6.18: working (2026-09-19)

The modem comes up on the mainline kernel: "Modem Alive", AT, SIM, radio, **registration on 5G NSA** and a data
context with an address on `sipa_eth0`. Measured on the device:

```
AT         -> OK                                   AT+CFUN?  -> +CFUN: 1
AT+CPIN?   -> +CPIN: READY                         AT+CSQ    -> +CSQ: 37,20
AT+CEREG?  -> +CEREG: 2,1,"C300","00E07F53",13     AT+COPS?  -> +COPS: 0,2,"28602",13
mobile data up on sipa_eth0                        sipa_eth0  UP  10.x.x.x/8
```

### What was wrong: the shared memory was mapped write-back

The regions the modem and the AP share are reserved **inside** System RAM (no `no-map`), and the vendor 5.4 driver
maps them with `vm_map_ram(pages, count, -1, pgprot_noncached(PAGE_KERNEL))`. Newer kernels dropped the `prot`
argument from `vm_map_ram()`, so the port had switched to `memremap(..., MEMREMAP_WC | MEMREMAP_WB)`, which falls
back to **write-back** whenever write-combine is not available - and it never is for a linear-mapped region.

The modem does not snoop the AP's caches, so everything the AP wrote stayed invisible to it. The symptom was
subtle rather than fatal: the CP started, asked for its NV data, and the NV server logged `fail SIZE` on the
second packet, wrote three chunks and stalled; `modem_control` then gave up with `wait modem alive timeout` and
`g_modem_state = 8`. `ioremap_wc()` is not an answer either - the kernel refuses it for addresses inside System
RAM (`WARNING at arch/arm64/mm/ioremap.c:27`), which is exactly what happened when it was tried.

`vmap(pages, count, VM_MAP, prot)` still takes a pgprot, so `smem.c` now builds the page array like the vendor
driver and maps the regions non-cached again, keeping `memremap()` only for regions that have no `struct page`
(real `no-map`) and for the ones the vendor code wants cached.

Before and after, from the same logs:

| | write-back (before) | non-cached (after) |
|---|---|---|
| `fail SIZE` | 1 | 0 |
| NV packets served (`writeData`) | 3 | runs to completion |
| "Modem Alive" | never | yes |

### Bring-up

`tools/mu300-modem-ml-load` loads 25 modules (no `sipa-dele`, see its comment). `cp_diskserver` is started first,
`modem_control` about 8 s later. "Modem Alive" arrives within a minute, and `mu300-atd` then serves AT normally -
15 commands in a row with no reopen needed.

### GPU

`mali_kbase` (Arm DDK r40p0, the same source the 5.4 image uses) builds against 6.18 after a port of its kernel
interfaces: `time_t`, `hrtimer_init()` -> `hrtimer_setup()`, `del_timer*()` -> `timer_delete*()`, the shrinker
allocation API, `vma->vm_flags` accessors, `get_user_pages()`/`pin_user_pages()` losing their `vmas` argument,
`dev_pm_opp_set_regulators()` returning a token, `ioremap_nocache()`, `DEFINE_SEMAPHORE()` taking a count,
`no_llseek`, `FMODE_UNSIGNED_OFFSET`, `get_file_rcu()`, devfreq's `scaling_min_freq`/`scaling_max_freq`, and the
page-migration hooks, which the folio conversion replaced and which are compiled out (migration is an
optimisation the driver runs without).

Two of them were more than renames:

* The DDK carries its own copy of the kernel's top-down address search, which walked the VMA **rbtree** - gone
  since 6.1. `vm_unmapped_area()` is not exported to modules, so the search now walks downwards with
  `find_vma()` and keeps the DDK's extra alignment rules (shader code must not touch a 4 GB boundary).
* Its `Kbuild` adds the power model (`ipa/`) to `mali_kbase-y` and a later line **reassigns** that variable with
  `:=`, dropping the objects again. Nobody noticed because the vendor kernel sets
  `CONFIG_SPRD_GPU_COOLING_DEVICE` and never uses `ipa/`; mainline does, and the module then fails to link. The
  include was moved to the end of the file.

The kernel config gained `CONFIG_SYNC_FILE` (the DDK's fence helpers) and the devfreq options the driver expects.

Measured on the device: the driver probes, `/dev/mali0` appears and the Android OpenCL userspace runs through the
vendor chroot - `Mali-G57 r0p1, OpenCL 3.0 v1.r40p0`, 1464 MiB, and `cltest` adds 4 194 304 floats on the GPU with
**0 errors**.

The address search was worth one more round: the first version returned `gap_end - length`, but
`align_and_check()` has already subtracted the length, so every allocation landed one buffer too low. The driver
loaded and OpenCL ran, and every single result was wrong - a reminder that "it runs" is not "it works".

Still open: audio is untouched.

## Status (2026-09-17): OpenWrt runs on 6.18.52

The installed OpenWrt 25.12.6 boots on the mainline kernel through the normal `boot/init`
(multi-OS switch_root): SSH, LuCI, `br-lan` over the USB 3.1 gadget, fw4/nftables, zram.

Fixes needed on top of the port:

- `sdhci-sprd`: UMS9620 has the r11p3 controller; the vendor driver programs DLL phase `0x2`
  (mainline `0x3`). With `0x3` reads work in HS400ES but every write fails with data CRC errors.
- `sdhci-sprd`: the eMMC and the SD card slot (`sprd,name` `sdio_sd`) are probed, `sdio_wifi` is not (Wi-Fi is on
  PCIe). The slot's card detect is line 35 of the EIC sync controller, which mainline's `gpio-eic-sprd` mapped as 24
  lines (three of its six banks): `patches/0014` maps them all, so the slot is detected by interrupt and an empty
  slot is left alone (issue #181: polled, it was initialised once a second and filled the log with "Got command
  interrupt ... even though no command operation was in progress" register dumps).
- eMMC bring-up: some units fail HS400ES at a cold boot (`mmc_select_hs400es failed, error -84` on every attempt,
  issue #226). `patches/0021` drops a failed timing from the host's caps, so mmc_rescan's next attempt takes HS200,
  then HS/DDR52, then HS, and the board boots (FINDINGS 160).
- UMP9620 PMIC watchdog, armed by LK for 300 s, is disabled by `ump9620-pmic-wdt-off`.
- Userspace config: cgroups/namespaces/seccomp, bridge, nftables, IPv6, zram.
- Thermal: vendor `sprd_thermal_r5p0` (19 on-die zones) with calibration from the UMS9620 eFuse; the eFuse
  provider is built read-only.
- cpufreq: vendor `sprd_sip_svc` + `sprd-cpufreq-v2`; ATF does the DVFS, the kernel asks through SIP SMC calls
  (3 policies: 4 little / 3 mid / 1 big, schedutil). PSCI cpuidle (WFI, core sleep, cluster power-down);
  `modules/ums9620_lpintc` makes the interrupts the kernel enabled wake sources of the cluster power-down, which TF-A
  left at its own list without USB, IPA and PCIe (docs/FINDINGS.md 218).
  Idle temperature dropped from ~54 C to ~48 C.
- The stock DT has trip points only on the vendor virtual zone (`soc-thmzone`: passive 70/85 C, critical 110 C,
  cooling maps to the three clusters), whose sensor driver mainline lacks: `modules/mu300_thermal` brings it back
  (the hottest of the eleven zones the DT names), so the kernel's `step_wise` throttles at 85 C. `extra-modules`
  loads it; `/opt/mu300/bin/thermal-guard` then only watches (and keeps its 105 C power-off), and caps cpufreq
  itself only while the zone is absent (FINDINGS 48).

- Watchdog: the UMP9620 PMIC watchdog that LK arms is taken over (`ump9620-pmic-wdt`), 60 s, pinged by the
  core until procd/systemd opens it; a hard hang resets the board back to Android.
- LEDs: UMP9620 RGB status LEDs (vendor `sc27xx-bltc`).
- PCIe: vendor `pcie-sprd` ported to the 6.18 DWC host API. `num-vectors` from the DT is honoured, the
  Marlin3 driver needs a contiguous block of 32 MSIs.
- Wi-Fi/BT (SC2355 Marlin3): `modules/` holds the vendor `wcn_bsp`, `sprd_wlan_combo` (SC2355 PCIe only) and
  `sprdbt_tty` ported to 6.18 as out-of-tree modules (`build-modules.sh`; `tools/port54.py` does the mechanical
  5.4 API changes, `wcn_bsp/kinclude/mu300_compat.h` the rest). On OpenWrt the chip boots its firmware and the
  5 GHz VHT80 AP beacons. The factory Wi-Fi MAC is read from `androidboot.wifimac` in `/chosen/bootargs`.
  The WCN modules cannot be unloaded and reloaded (same as on 5.4).

Not yet on mainline: modem (sipc/sipa/modem loader), GPU, audio; Bluetooth is built but untested.

### Kernel configuration and module audit

Both mainline bundles use `mu300-mainline.config`, including built-in KVM, container support,
virtio/9p, multicast/MPLS/MPTCP, traffic control, NFS/SMB, encrypted filesystems, LVM/RAID,
USB/IP and UVC webcams. Optional drivers are modules where possible. CBC and XTS are built
in because filesystem encryption requires them. Kernel tracing options associated with the
boot failures in FINDINGS 31p remain disabled.

`make-bundle.sh` checks the configuration embedded in the actual Image against the requested
fragment and requires every module recorded by `build-modules.sh`. Each bundle includes
`kernel.config`, `modules.in-tree` and `modules.list` for inspection. On arm64 KVM is built
in (`CONFIG_KVM=y`), so there is no `kvm.ko`; check `/dev/kvm` and the boot log on the device.

An audit of the published v2026.10.18 (7.2.9) and v2026.10.25 (7.2.10) bundles found KVM
built in to both, and all 376 older modules present in the latter, plus `mu300_thermal.ko`.
The expanded set adds capabilities to that baseline; it does not repair a missing KVM module.
