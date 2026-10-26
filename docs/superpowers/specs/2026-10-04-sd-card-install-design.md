# SD card as the home of the Linux filesystem

Date: 2026-10-04. Status: design approved in conversation, awaiting review of this document.

## Why

The Linux systems live in an ext4 filesystem (`mu300root`) placed in the unpartitioned eMMC space behind
`userdata`. Devices with a small eMMC have no such space, and the only way to make some is to shrink `userdata`,
which rewrites the GPT and erases Android's data. The SD card slot is a second place to put the filesystem that
needs neither, and pulling the card is itself a way back to what was there before.

The idea and the core of the boot-side change come from `kanoqwq/mu300-linux`, branch `clean-tf-7.2` (which
contains `oswaldxdd`'s two TF commits). That branch covers `boot/init` and `tools/android-install.sh` only; the
installers, the uninstallers, the helper tools, the translations and the tests are written here.

This is the first of four pieces of work taken from that fork. The others get their own documents: the Magisk
installer module, the fork's OpenWrt as a third installable system, and its driver fixes.

## What the user gets

* `install.sh` and `install.ps1` ask where the Linux filesystem goes when a usable card is in the slot.
* A device with too little free eMMC space installs to the card instead of being offered the repartition.
* An installation on the card writes nothing to the eMMC except `boot_b` and the 32 bytes of `misc`.
* Pulling the card: the device boots the internal installation if there is one, Android otherwise.
* `mu300-update`, `mu300-os`, `mu300-next-boot`, `uninstall`, `reset-password` work the same on both.

## The filesystem on the card

ext4 with the label `mu300sd`, on the card's first partition when it has one and on the whole card otherwise.
Inside, the layout is exactly that of `mu300root`: `/ubuntu`, `/openwrt`, `/.mu300/`.

A label of its own, not `mu300root`: a device can hold both, and two filesystems with one label make
`LABEL=` mounts pick either.

## Kernel: the SD host under mainline (first step, on its own)

Read from the F50 test board on 2026-10-04 (OpenWrt, 6.18.54): only `mmc0` exists. `upstream/port/install.py`
makes `sdhci-sprd` probe nothing but the non-removable eMMC, because the SD host "is not populated on the MU300
and floods the log". The board does have the slot and a card in it, so that edit is what keeps the card away.

The fork removes the edit and, because the card-detect GPIO of the stock device tree stays deferred under
mainline (its EIC supplier has no driver there), lets the `sdio_sd` host fall back to polling
(`MMC_CAP_NEEDS_POLL`) instead of deferring forever. That is taken, with two things checked on the board before
anything else is built on it:

* with a card: `mmcblk1` appears, in time for init's scan, and reads and writes hold up (a full write and read
  back of a few hundred MiB, no CRC errors in the log);
* without a card: the log stays quiet. The flood is why the edit exists; if polling brings it back, the host's
  messages are rate-limited or the poll is made silent, and that is part of this step.

Both 6.18 and 7.2 get it (one source). The 5.4 vendor kernel is checked the same way and is expected to have the
host already; if it does not, a card installation is offered only together with a mainline kernel and the
installer says so.

A card installation therefore needs a boot image from a release that has this change. `mu300-update` refuses to
install an older kernel bundle onto a device that booted from the card.

## Boot (`boot/init`)

After the modules are loaded and before the internal region is looked for:

1. Scan the block devices that are not the eMMC (`/dev/mmcblk[1-9]p1`, then `/dev/mmcblk[1-9]`) for ext4 magic and
   the label `mu300sd`. A hit is mounted on `/disk` directly, with no loop device.
2. No hit: find and mount the internal region as today.
3. Waiting for a card that has not enumerated yet happens only when it can matter, for at most 8 seconds, polling
   once a second:
   * no internal installation was found, or
   * the internal filesystem has the marker `/.mu300/root-on-sd` (the installer writes it when a card installation
     is made on a device that also has an internal one).
   If the card appears, the internal filesystem is unmounted and the card is used. A boot with no card and no
   marker never waits. (The fork waits on every boot that has no card.)
4. Everything after the mount is unchanged: `pick_root`, the boot counter, `mount --move /disk` to
   `/mnt/mu300-disk`.

Each decision gets a `log "stage=sd-..."` line, so `tools/collect-logs.sh` shows which filesystem was booted.

Not taken from the fork: the safety timer stays at 300 seconds (the fork has 90), and the USB changes that share
its diff (early DHCP, single-configuration RNDIS, the boot policy file) belong to the driver-fixes work.

## Installers (`install.sh`, `install.ps1`)

After the eMMC free space is measured:

* A card counts when Android shows a block device other than `mmcblk0` whose `device/type` is `SD` and whose size
  is at least 700 MiB. The target is its first partition, or the whole device without one.
* With a card present the installer asks: internal storage or SD card. The default is internal, and SD when the
  internal space is under 700 MiB; in that case the repartition offer is not made unless the user picks internal.
* `MU300_STORAGE=internal|sd` answers the question without asking (same convention as the other `MU300_*`
  answers); `sd` without a usable card is an error.
* An existing `mu300sd` filesystem is reported and can be kept or replaced, like `mu300root` today.
* The size checks for the chosen systems use the card's size.
* A card without `mu300sd` is formatted only after the user types the confirmation word; the question names the
  device and its size and says that everything on the card is erased.
* The summary before `INSTALL` names the card as the target and lists the writes: the card, `boot_b`, 32 bytes of
  `misc`.
* `--check` reports the card and whether it holds an installation; it still writes nothing.

Both installers pass `SD_MODE` and `SD_DEV` in `mu300-install.env`. Every new sentence gets its Turkish and
Chinese line in `i18n/` (`tools/check-i18n.py` already fails on a missing one).

## Device side (`tools/android-install.sh`, `tools/android-mount-mu300root.sh`)

`SD_MODE=1`:

* The block device must exist. Android's own mount of the card is released through `sm unmount`; mounts that remain
  are unmounted; if the card is still mounted the script stops before writing anything.
* `FORMAT=1`: refuse an ext4 filesystem whose label is not `mu300sd`; anything else is formatted
  (`mke2fs -t ext4 -L mu300sd -F`, as for the internal region).
* `FORMAT=0`: the filesystem must be ext4 `mu300sd`.
* The systems are installed with the code that exists; only the mount differs.
* Ubuntu's `/etc/fstab` root line is written as `LABEL=mu300sd`.
* If an internal `mu300root` exists too, it is mounted briefly and gets `/.mu300/root-on-sd`.

`android-mount-mu300root.sh` learns `MU300_SD_DEV=<device>`: mount that block device (after the same magic and
label check, for `mu300sd`) instead of the loop over the eMMC region. `reset-password.sh` and
`android-import-hotspot.sh` look for a card installation first and use it through that.

## Uninstallers (`uninstall.sh`, `uninstall.ps1`)

They look for `mu300sd` on the card as well as `mu300root` on the eMMC and report both. For the card the choices
are keep or erase (quick: the first 64 MiB, as for the internal region). The marker `root-on-sd` is removed from an
internal filesystem that is kept. Slot and `boot_b` handling is unchanged.

## `mu300-update` and the running systems

They work on `/mnt/mu300-disk`, which init moves into place whichever device it came from, and `boot_b` is on the
eMMC either way; no change is expected. Ubuntu's `etc/fstab` is in the list of files an update keeps, so the
`LABEL=mu300sd` line survives. This is checked on the device, not assumed.

## What can go wrong

| case | behaviour |
|---|---|
| card pulled, internal installation present | boots the internal one (after the 8 s wait if the marker is there) |
| card pulled, nothing internal | standalone mode, then Android after the 300 s timer; LK's tries also count down |
| card fails while Linux runs | the running system loses its root; the next boot is the case above |
| foreign ext4 on the card | never formatted; the installer says so and stops |
| Android refuses to release the card | the installer stops before writing anything |
| card with data in FAT/exFAT | formatted only after the typed confirmation |
| old boot image (before this change) on a card-only device | cannot find the card; lands in Android. The installer always writes the boot image, so this needs a manual downgrade |

## Tests

Unit tests, in the existing files and style (stubs, a fake `/`, every shell):

* `test_static.py`: init scans the card before the internal region; the safety timer is still 300; the wait is
  conditional.
* `test_installer.py`: with a fake `adb`, the storage question appears only with a usable card; the default flips
  when the internal space is too small; `MU300_STORAGE` is honoured; the env file carries `SD_MODE`/`SD_DEV`.
* `test_device_scripts.py`: `android-install.sh` in SD mode against stub tools: refuses a foreign ext4, refuses a
  card Android keeps mounted, rewrites the fstab label, writes the marker.
* `installer.Tests.ps1`: the PowerShell side of the storage choice.
* `tools/check-i18n.py` passes.

On the F50 test board, with a card:

1. Fresh install to the card, OpenWrt and Ubuntu; both boot; `mu300-os` switches.
2. Card pulled: internal installation boots; with no internal installation the device returns to Android.
3. `mu300-update` from the card installation, including from the previous release's state.
4. `uninstall` with the erase choice.
5. Reboot loop of OpenWrt on 5.4, as before every release.

Whether the U30 Air has a card slot at all is read from the device (`/sys/class/mmc_host`); without one the
question never appears there and nothing else changes.

## Out of scope

The Magisk installer module, the third system, the fork's USB and driver changes, partitioning the card
(a card is used as it is: first partition or whole device).
