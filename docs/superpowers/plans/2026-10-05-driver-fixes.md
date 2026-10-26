# Driver fixes: plan

Spec: `docs/superpowers/specs/2026-10-05-driver-fixes-design.md`. Branch `driver-fixes`.

## Build and install (all module items)

* A private copy of the build volume (`mu300-mainline-drivers`: linux-6.18.55, out-6.18.55, linux-7.2.9,
  out-7.2.9) so the kernel tree patched here is nobody else's; `/src/mod-build` on a scratch bind mount.
* `upstream/build.sh` (only for the NCM patch), `upstream/build-modules.sh`; 6.18.55 into `upstream/out`, 7.2.9 with
  `OUTDIR=out-7.2 KV=7.2.9`; bundles with `upstream/make-bundle.sh work/<name>.tar.gz <5.4 bundle>`.
* Install: `MU300_RELEASE=v2026.09.30 MU300_KERNEL_BUNDLE=/tmp/<bundle> mu300-update kernel 6.18|7.2`.
* Counters per boot from `dmesg` (`/tmp/kcount.sh` on the device): `get pd fail`, duplicate-filename, Call trace,
  hw csum failure, ARP/DNS command-path lines, the two idle messages, Oops/BUG/WARNING, new pstore files.

## Order

1. Baseline on the U30 Air (7.2.9, before): counters of a running boot, USB ping and throughput from the Mac, Wi-Fi
   throughput and ping from the Pixel, ARP burst, IPv6 from the Pixel.
2. Scripts, TDD: item 9 (PATH lists), item 6 (mu300-at), item 8 (lan-start); item 7 after looking at the device.
3. Modules: items 1, 2, 3, 4 (without the reorder change) in one build per kernel; install 6.18 on the U30 Air,
   20 reboot cycles (item 1) with counters; Wi-Fi measurements (items 3, 4).
4. Reorder change (item 4) as its own module build: Pixel throughput and ping, with and without.
5. NCM patch (item 5): kernel image, USB ping/throughput before and after.
6. 7.2.9 with everything taken: install, a few cycles, measurements; the U30 Air stays on it.
7. 5.4 patches for items 1, 2 and the PCIe post-init: apply and compile check.
8. Independent review of each kernel/module diff; FINDINGS; report in `.superpowers/DRIVER-REPORT.md`.
9. Added during the work (from the coordinator): the USB gadget's serial number and MACs unique per physical device
   when two devices share `androidboot.serialno` (one restored from the other's backup): boot/init, TDD, measured on
   F50 #1 and the U30 Air (spec item 11).

## End states

* U30 Air: Linux, 7.2.9 with the fixes, default boot linux.
* F50 #1: booting from its SD card (if it was reachable: it was not at the start, 192.168.77.1 silent).
* Pixel: back on its home network.
