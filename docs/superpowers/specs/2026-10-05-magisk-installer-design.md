# Installing Linux from Android with a Magisk zip

Date: 2026-10-05. Status: design written by the architect while the user was away; the user approved the work and
its order beforehand and asked for the decisions to be made and recorded, not asked about. Every decision below
names the alternatives that were rejected and why.

## Why

Today Linux is installed from a computer: `install.sh` (or `install.ps1`) pulls the device's own boot image, `misc`
head and vendor files over adb, builds `boot_b` on the computer with `boot/build-boot-image.py`, and drives
`tools/android-install.sh` on the device. Many owners of these routers have no computer at hand when they want to
try Linux, but they do have a rooted Android with Magisk on the device itself (reached through scrcpy, a web panel
such as UFI-TOOLS, or a phone running adb). The user asked for:

* "Magisk flashable zips should be produced for all combinations by CI; whoever wants downloads the one they need."
* an optimised install mode for the Magisk module (kanoqwq's fork has a first one),
* and, as always, a device that stays bootable: no step may lock the owner out of Android.

kanoqwq's deployer (`kanoqwq/clean-tf-7.2`, `android/magisk/mu300-openwrt-tf/`, and 35a1c55) shows that the idea
works, but it ships one prebuilt boot image made from kanoqwq's own device: his stock `boot_a`, his `misc`, and
53 MB of his device's Android libraries. That image is wrong for any other firmware and redistributes proprietary
files. It also sets an empty root password and shortens the rescue timer to 90 s. This design keeps what is good
in it (Android on slot b with Linux on slot a, `/system/bin/mke2fs` for big cards, leaving Magisk's shell options
alone, messages in the Android locale's language) and builds everything device-specific on the device.

## What the user gets

On every release page, next to the existing assets, eight zips:

| zip | system | kernel | size (v2026.10.08 assets) |
|---|---|---|---|
| `mu300-magisk-<tag>-openwrt-k5.4.zip` | OpenWrt | 5.4 (vendor) | ~91 MB |
| `mu300-magisk-<tag>-openwrt-k6.18.zip` | OpenWrt | 6.18 LTS | ~73 MB |
| `mu300-magisk-<tag>-openwrt-k7.2.zip` | OpenWrt | 7.2 | ~73 MB |
| `mu300-magisk-<tag>-ubuntu-24.04-k5.4.zip` | Ubuntu 24.04 | 5.4 | ~167 MB |
| `mu300-magisk-<tag>-ubuntu-24.04-k6.18.zip` | Ubuntu 24.04 | 6.18 | ~149 MB |
| `mu300-magisk-<tag>-ubuntu-24.04-k7.2.zip` | Ubuntu 24.04 | 7.2 | ~150 MB |
| `mu300-magisk-<tag>-ubuntu-26.04-k6.18.zip` | Ubuntu 26.04 | 6.18 | ~161 MB |
| `mu300-magisk-<tag>-ubuntu-26.04-k7.2.zip` | Ubuntu 26.04 | 7.2 | ~162 MB |

(Ubuntu 26.04 has no 5.4 zip: its programs need system calls 5.4 does not have, the same rule as `install.sh`.)
Plus `SHA256SUMS-magisk`.

The download story, as the README will tell it:

1. Pick the system and the kernel you want. **The same zip works on the F50 and on the U30 Air, and installs to the
   internal storage or to the SD card**: the installer recognises the device and finds the place (see "Choices").
2. Put the zip on the device and install it in the Magisk app (Modules → Install from storage), or run
   `su -c magisk --install-module /sdcard/Download/<zip>`.
3. Read the output: it shows what it found, what it did, and the password it generated. The password is also in
   `/data/adb/mu300-linux-password.txt` (`su -c cat /data/adb/mu300-linux-password.txt`).
4. Reboot. Linux starts; if it does not, the device returns to Android by itself.
5. Both systems: install the OpenWrt zip and the Ubuntu zip one after the other. The second one adds its system next
   to the first and becomes the one that boots; `mu300-os` switches.
6. To choose anything the defaults do not cover (SD card instead of internal storage, erasing a card, a password of
   your own, Android as the default boot), put a `mu300-install.conf` in `/sdcard` (see "Choices"); erasing a card
   and a password of your own count only from `/data/adb/mu300-install.conf`, which the installer tells you how to
   make. `MU300_DRY_RUN=1` makes the zip only report what it would do.

After the install the zip stays as the Magisk module "MU300 Linux" (the existing switch module, same id): its
Action button and `su -c mu300-linux` start Linux from Android later.

## Architecture

```
zip (built by CI from the release's own assets; nothing device-specific inside)
├── META-INF/com/google/android/{update-binary,updater-script}   Magisk's standard module installer
├── module.prop, customize.sh                                   the switch module that stays installed
├── action.sh, switch.sh, system/bin/mu300-linux                 (android/magisk/mu300-linux-switch)
├── mu300/                       used once during the install, never copied into the module
│   ├── install.sh               android/magisk/installer/mu300-install.sh: the whole installation
│   ├── android-boot-image.sh    tools/android-boot-image.sh: device segment, misc blocks, vendor files
│   ├── mu300-update             the updater of the tag the zip is built from, sourced (MU300_LIB=1) for its boot image functions
│   ├── android-install.sh, android-mount-mu300root.sh, storage.sh, i18n.sh, i18n/*.tsv
│   ├── subset-files.txt, gpu-files.txt     which Android files to take from this device
│   ├── busybox                  the 5.4 bundle's static busybox (Ubuntu): mkpasswd for the password hash
│   └── manifest                 tag, system, kernel, payload names and sha256
└── payload/                     stored (not deflated: already gzip)
    ├── mu300-kernel[-6.18|-7.2].tar.gz
    └── mu300-<system>-rootfs.tar.gz
```

`customize.sh` is sourced by Magisk. It sets `SKIPUNZIP=1`, refuses recovery mode, unpacks `mu300/` and the switch
module's files into Magisk's `$TMPDIR` in one go, runs `mu300/install.sh` **as a child process** under Magisk's
busybox, and turns a non-zero exit status into `abort`. Only after success does it copy the module's files from
`$TMPDIR` into `$MODPATH` - never from the zip again: a zip on `/sdcard` can be replaced while the installation runs,
and `system/*` runs as root. It never runs `set -e` or `set -u` in Magisk's shell.

> Decision: the installation runs in a child shell. Rejected: running it inside `customize.sh` with `set -eu` and
> restoring Magisk's options afterwards (kanoqwq 35a1c55), because an error under `errexit` in a sourced file ends
> Magisk's own installer before its cleanup, and every early `exit` would have to remember the restore.

The child (`mu300-install.sh`) does, in this order, and writes nothing to the eMMC before step 8:

1. **Environment.** Magisk's busybox and `magiskboot` (`/data/adb/magisk`), Magisk ≥ 26.0, `BOOTMODE` (Android
   running), root. Language from the Android locale (tr, zh, else en) unless `MU300_LANG` is set.
2. **Answers.** `mu300-install.conf`, read (never sourced) with an allow-list of keys; what may destroy data or
   set the password is taken only from a file only root can change (see "Choices").
3. **Device.** F50 or U30 Air from `ro.product.model` (as `install.sh`); anything else is refused unless
   `MU300_DEVICE` names it. The kernel bundle must list the device in `./devices`.
4. **Slots.** Android's slot from `ro.boot.slot_suffix`; Linux goes to the other one. `misc` must hold a valid
   `bootloader_control` (magic and CRC); `boot_<linux slot>` must exist and must not be the partition Android booted.
5. **Payload.** A work directory of this run only, `mktemp -d /data/adb/mu300-magisk.XXXXXX` (mode 700; see
   "Safety"), and free space checked there; the two payload files are copied out of the zip into it, and the copies
   are checked against the manifest's sha256. Only those copies are used.
6. **Plan.** Storage, format/update, boot system, default boot, attempts, hotspot, GPU, password source (or kept). Printed in
   full, with the source of every value (default or the conf file). `MU300_DRY_RUN=1` stops here (exit 3; the
   module is not installed).
7. **Build, all in the work directory.** Vendor files from this device (firmware, the modem_control
   subset, the GPU closure), the vendor overlay tarballs `android-install.sh` already accepts, the four `misc`
   blocks, the device ramdisk segment, the boot image (`bootimg_from_stock`), the password hash. Then the password
   is shown and written to its file, before anything is installed: `android-install.sh` puts the hash into the
   systems, so a run that fails or is killed after that must not leave a Linux whose password nobody has seen. When
   the file cannot be written the installer stops here, having written nothing.
8. **Install the systems.** `tools/android-install.sh` with the same `mu300-install.env` `install.sh` writes, run
   by Android's own `/system/bin/sh` and toybox (see "Safety"), from the work directory (`MU300_DEVICE_WORK`). Then the kernel bundle's modules go into every
   system on the Linux filesystem (`mu300-update`'s function).
9. **Boot image.** `write_boot` (from `mu300-update`) writes it to `boot_<linux slot>` and reads it back (sha256 of
   the image and of the AVB footer). On a mismatch it stops: `misc` is untouched and Android keeps booting.
10. **Arm.** The Linux slot's trial block goes into `misc` (32 bytes) and is read back. When the write fails or the
    read-back differs, the block `misc` held at the start (kept in the work directory since step 5) is written back
    and read back: then Android boots as before and the installer says so; if even that does not verify, it says
    plainly that `misc` holds unknown bytes, keeps the old block in `/data/adb/mu300-misc-bc-before.bin` and prints
    the `dd` command that writes it back, with the advice not to reboot first.
11. **Report.** Password, addresses, how to switch and how to get back to Android; the settings this run used once
    and removed (see "Choices"). Nothing in it can fail the run: Linux is armed by then. The work directory is deleted
    (it held this device's proprietary files), but never a filesystem still mounted in it (see "Safety"). The
    reboot is the user's.

## The boot image on the device

The boot image `install.sh` writes is: the header page of Android's own boot image (header v4) with new kernel and
ramdisk sizes, `cmdline=loglevel=5` and no signature; the kernel; the ramdisk; Android's vbmeta; zeros; Android's
AVB footer with the new sizes (`boot/build-boot-image.py`). The ramdisk is concatenated LZ4 legacy segments: the
device segment (this device's Android subset, its `misc` blocks, `etc/mu300-device`, modules), then, for a mainline
kernel, the bundle's generic segment. `mu300-update` already rebuilds this image on the device (`boot_update`):
header and AVB tail from `boot_b`, the device segment kept, the release's generic segment appended.

On the device without a computer the only new pieces are the device segment (it contains proprietary files and
can only be made there) and taking the header and AVB tail from Android's boot partition instead of `boot_b`.

**Evidence** (scratch experiments, 2026-10-05, Magisk v30.7's own arm64 `magiskboot` and `busybox` run in an arm64
container, on a copy of the F50 test board's `boot_a` dump and of the installer-built `boot-linux-slotb.img`):

* `magiskboot unpack` reads the stock image (`HEADER_VER [4]`, `RAMDISK_FMT [lz4_legacy]`, `VBMETA`), so it
  understands the format. On our Linux image it reports `RAMDISK_FMT [lz4_lg]` and splits off a `kernel_dtb`
  (the wrapped mainline Image carries its DTB): it treats the concatenated segments as one stream and would
  repack them as a single `lz4_lg` frame (with a size trailer) - the later segments would be decompressed into one
  66 MB cpio whose second and third archives its cpio editor never sees. Unpack/repack is the wrong tool here.
* `magiskboot compress=lz4_legacy` of a cpio made by Magisk's `busybox cpio -o -H newc` (the 51 MB Android subset
  plus `etc/mu300-device`: 53.5 MB → 4.7 MB) gives a frame with the legacy magic `02 21 4c 18` that the host's
  `lz4 -d` decodes back byte for byte.
* A 40-line prototype of the header/AVB assembly in POSIX sh, run under Magisk's busybox ash, given the stock dump,
  the kernel and the ramdisk taken out of `boot-linux-slotb.img`, reproduced that image's first `original_size +
  vbmeta_size` bytes and its 64-byte footer **byte for byte**.
* The release's 5.4 bundle `busybox` (Ubuntu 1.36.1, static arm64) has `mkpasswd -m sha512 -S SALT -P 0`; its output
  equals `tools/sha512crypt.py` for the same password and salt. Magisk's busybox has no `mkpasswd`/`cryptpw`.
* The plan's `bootimg_from_stock` (Task 4) and `bc_files` (Task 5), as written there, run under dash, bash and
  Magisk's busybox ash in standalone mode: the image head and footer match `boot-linux-slotb.img` byte for byte, and
  the slot-a and slot-b-trial blocks match the ones `build-boot-image.py` computed for that image.

> Decision: assemble the image in POSIX sh by extending `mu300-update` (new `bootimg_from_stock`, the existing
> `bytes`/`poke`/`pad_page`/`write_boot`), sourced by the installer with `MU300_LIB=1`; build the device segment with
> busybox `cpio -o -H newc` and compress it with `magiskboot compress=lz4_legacy`. One implementation of the image
> layout on the device, the one that already updates every installed device.
>
> Rejected: (a) `magiskboot unpack/repack` - shown above to merge our segments into one `lz4_lg` stream;
> (b-only) an LZ4 compressor in sh - not feasible; an uncompressed cpio segment would avoid compression, but the 5.4
> kernel's behaviour with a raw segment behind or before LZ4 legacy ones is unproven (it already panics on gzip)
> and 53 MB uncompressed would not fit under the 48 MiB persistent-log limit; (c) a static helper binary from CI -
> a second implementation of the image layout to keep in step with `build-boot-image.py` and `mu300-update`,
> when `magiskboot` is guaranteed present under Magisk.

The segment order is **device segment first, then the kernel bundle's generic segment** (for 5.4 too: the 5.4 bundle
has carried `ramdisk-generic.lz4` with init, busybox, logdw, the 5.4 and U30 Air modules since on-device updates
exist). Nothing in a generic segment has the name of a device-segment file, so the order only matters for
`mu300-update`, which wants an LZ4 frame first (it checks the magic) and keeps the whole ramdisk as the device
segment when `boot_b` was written by someone else: exactly what it does after `install.sh` today.

The device segment holds:

| path | from |
|---|---|
| `etc/mu300-device` | `f50` or `u30air` |
| `etc/mu300-linux-slot` | `a` or `b` (new, see "Opposite slot") |
| `etc/misc-bc-slot-a.bin`, `etc/misc-bc-slot-b-trial.bin`, `etc/misc-bc-slot-b.bin`, `etc/misc-bc-slot-a-trial.bin` | the live `misc` block with the slot fields set and the CRC recomputed (gzip's CRC-32, as `mu300-next-boot` does) |
| `android/...` | the files of `android-vendor/subset-files.txt` copied from this device's `/` (`tar -h`, as `extract-subset.sh` does over adb), plus `system/bin/linker64` and an empty `linkerconfig/ld.config.txt` |

Compared with the image `install.sh` builds, the differences are: no init with the region offset written in
(init finds the region itself since v2026.09.29), the 5.4 modules are absent from a mainline image (the generic
segment of a later `mu300-update kernel 5.4` brings them), and the generic segment's empty `etc/mu300-trial-guard`.
None changes what boots.

The installer also checks the image before it writes: header v4 and `AVBf` in Android's image, LZ4 magic of both
segments, the image plus vbmeta under 48 MiB.

## Zip and asset layout

| what | size (MB, from the v2026.10.08 assets in `release/`) |
|---|---|
| `mu300-kernel.tar.gz` (5.4) | 26.9 |
| `mu300-kernel-6.18.tar.gz` / `-7.2` | 9.4 / 9.6 |
| `mu300-openwrt-rootfs.tar.gz` | 61.6 |
| `mu300-ubuntu-rootfs.tar.gz` (24.04) / `-26.04-` | 137.7 / 150.0 |
| installer, switch module, busybox (in every zip) | ~2.2 |

The combinations the user named are device (2) × system (OpenWrt, Ubuntu 24.04, Ubuntu 26.04, both) × kernel (3) ×
storage (2): 2 × 5 × 3 × 2 = 60, less the eight with 26.04 on 5.4, so 52 zips, about 7.5 GB per release, each
rootfs copied up to 24 times.

> Decision: one zip per (system, kernel): 8 zips, about 1.03 GB per release. The device and the storage do not
> change a single byte of the payload, so they are decided on the device (device detected; storage by rule or
> `mu300-install.conf`). "Both systems" is two installs, the second adding its system to the existing filesystem.
>
> Rejected: zips per full combination (52, 7.5 GB, identical payloads under different names); zips that also cover
> "both" (13 zips, ~2.1 GB, the two rootfs together in five more 200+ MB zips, for something two installs already
> do and that installing a second system later needs anyway); a small zip that downloads the assets on the device -
> the Magisk screen would sit for many minutes (GitHub's CDN gives a single connection 0.2 MB/s in some regions,
> `install.sh` measured it; busybox `wget` has no parallel ranges), Magisk's busybox `wget` does not verify TLS
> certificates, and an install would depend on mobile data working in Android at that moment. Kept as a possible
> addition: the payload check is already by sha256 from the manifest, so a fetching variant could reuse it.

Limits: GitHub allows 2 GiB per release asset (largest zip ~170 MB) and has no limit on the total size of a release.
The Magisk app copies a zip chosen from storage into its cache before it runs it, and the installer unpacks the
payload into its work directory under `/data/adb`: the installer requires free space on `/data` of the payload size plus 512 MiB and says
so before it starts. Zip entries under `payload/` are stored, not deflated, so `unzip -p` streams them at disk speed.

`module.prop`: `id=mu300_linux_switch` (it updates an installed switch module in place), `version=<tag>`,
`versionCode` from the tag (`v2026.10.06` → `20261006`).

## Choices: no questions on the screen

Magisk's install screen shows output and cannot take input. The common answer is a volume-key chooser
(`getevent`), and it does not fit these devices: they are headless; `gpio-keys` declares `KEY_VOLUMEUP`/`DOWN`
(docs/FINDINGS.md, reboot section) but as far as this project knows the cases have no volume buttons - the button
handler (`mu300-buttons`) knows only power (116) and the U30 Air's Wi-Fi key (138); the power key also sends Android
to sleep. A chooser that times out to a default is the defaults with a delay, and many owners install through
`magisk --install-module` from a web panel or adb, where no one can press anything.

> Decision: safe defaults, decided from what is on the device, and an optional answer file for everything else,
> with the same names and values as the `MU300_*` answers `install.sh` already takes. Destructive choices (erasing
> an SD card, using an internal region that holds data, wiping an installation) happen only when the file says so.
> `MU300_DRY_RUN=1` prints the plan and writes nothing. Rejected: volume keys (above); a choice encoded in the zip's
> file name (the Magisk app copies the zip under its own name, so `$ZIPFILE` does not keep it); two-pass flashing
> where the first pass always only reports (every install would take two flashes).

The answers are `KEY=VALUE` lines (`#` comments, optional quotes, CRLF tolerated) in a file named
`mu300-install.conf`. It is read line by line and only the keys below are taken; anything else is reported and
ignored, a value is only ever assigned (never evaluated), and every value must be exactly one of those listed. A
value is checked when its line is read, before it is assigned (`MU300_LANG`, for one, is part of a file name as soon
as it is set); a value that is not one of those stops the installer before anything happens.

The installer runs as root, and `/sdcard` can be written by every app with storage access. So there are two kinds of
file:

* **trusted**: `/data/adb/mu300-install.conf`, when root owns it and neither group nor others can write it (only root
  can create files in `/data/adb`), and a `mu300-install.conf` inside the flashed zip's `mu300/` (it is exactly as
  trusted as the scripts next to it, which run as root anyway);
* **untrusted**: `/sdcard/mu300-install.conf` or `/sdcard/Download/mu300-install.conf` (first found), and a
  `/data/adb/mu300-install.conf` that someone other than root owns or can write.

An untrusted file may only choose what destroys nothing and reveals nothing. A key of the "trusted only" kind in it
is reported and ignored, and the installer prints the one command that turns the file into a trusted one:
`su -c 'cp /sdcard/mu300-install.conf /data/adb/mu300-install.conf && chmod 600 /data/adb/mu300-install.conf'`.
Untrusted files are read first, trusted ones after them, so a trusted value wins. The plan names the file every
value came from. A trusted `MU300_MODE=wipe`, `MU300_SD_ERASE=yes` or `MU300_REGION_OVERWRITE=yes` counts for one
install: after a successful run that used it, its line in `/data/adb/mu300-install.conf` becomes a comment, the way a
used `MU300_PASSWORD` does (a new file renamed over the old one, mode 600, a comment saying the installer used and
removed it), and the report names it. Left standing, it would apply to every later flash: the second of two zips
would wipe the first system, every update the home directories, and a new card would be erased. One that a run did
not use (an erase on an update) stays, and a conf inside the zip is never edited. A standing one is still possible
for a file someone else wrote earlier, so it counts only for storage the same trust chose: an erase of the card only with
`MU300_STORAGE=sd` from a trusted file, or for a card that already holds `mu300sd` when no `MU300_STORAGE` is
given; a region overwrite only with a trusted `MU300_STORAGE=internal` or without any `MU300_STORAGE`. Otherwise
an untrusted `MU300_STORAGE=sd` would aim a standing erase at whatever card is in the slot.

> Decision: two trust levels, with erasing, wiping, the region overwrite, the password, its file and the model
> override only from a file only root can write. Rejected: honouring `/sdcard` for everything (any app with storage
> access could have the next flash erase a card, wipe an installation or set the Linux root password); a typed or
> volume-key confirmation (no input under Magisk; volume keys above); two-pass flashing (every install twice). The
> price: a user without a terminal who wants an erase types one `su` command, in a terminal app or `adb shell`.

| key | values | where | default |
|---|---|---|---|
| `MU300_STORAGE` | `internal`, `sd` | any | where an installation already is (card first, as init does); else internal when the free eMMC region is big enough; else none (refused, with the reason) |
| `MU300_SD_ERASE` | `yes` | trusted only | not set: a card is never formatted - not a new one, and not a `mu300sd` card with `MU300_MODE=wipe` |
| `MU300_REGION_OVERWRITE` | `yes` | trusted only | not set: an internal region whose samples hold data is not used |
| `MU300_MODE` | `update`, `wipe` | `update` any, `wipe` trusted only | `update` when a filesystem exists (settings and data kept) |
| `MU300_BOOT_OS` | `ubuntu`, `openwrt` | any | the system of this zip |
| `MU300_BOOT` | `linux`, `android` | any | `linux` (Linux is the default boot, Android after failed boots) |
| `MU300_BOOT_ATTEMPTS` | `1`-`6` | any | `5` |
| `MU300_HOTSPOT` | `yes`, `no` | any | `yes` (Android's hotspot name and password) |
| `MU300_GPU` | `yes`, `no` | any | `yes` (skipped with a message when this device lacks a file of the closure) |
| `MU300_PASSWORD` | 6+ characters | trusted only | generated for a new system; an update keeps the existing password |
| `MU300_PASSWORD_RESET` | `yes` | trusted only | not set: an update keeps the existing password; `yes` generates a new one |
| `MU300_PASSWORD_FILE` | `sdcard` | trusted only | not set: the password file is `/data/adb/mu300-linux-password.txt` |
| `MU300_DEVICE` | `f50`, `u30air` | trusted only | detected; needed only for a model name the installer does not know |
| `MU300_LANG` | `en`, `tr`, `zh` | any | the Android locale |
| `MU300_DRY_RUN` | `1` | any | not set |

Never offered here: shrinking `userdata` to make room (the 32 GB variant). That rewrites the GPT and erases
Android's data; it stays with the computer installer, where a typed `ERASE` and a backup step come first. Without
room inside, the message names the way out this device has: the card in the slot (`MU300_STORAGE=sd`,
`MU300_SD_ERASE=yes` in the trusted file), a bigger card when the one there is too small, or the computer
installer; a card with another Linux filesystem is never offered for erasing.

Every run, a refused one too (with what was known by then), writes `/sdcard/mu300-install.conf.example` with every
key, its meaning, the value this run used and which keys need the trusted file, so the file never has to be typed
from documentation. Each explanation is a comment line of its own above its key: removing the `#` of a key gives a
line the parser takes as it is (it does not cut a value at `#`, which a password may contain). Any app can write `/sdcard`, so the file is never opened by its name: the text goes into a new
file with a random name, created exclusively, which is then renamed over `mu300-install.conf.example` - a rename
replaces whatever is at that name, a link someone put there included, instead of writing through it. (An exclusive
open of the name itself is not enough: the shells' `set -C` opens an existing non-regular file, such as a link to a
block device, without `O_EXCL`.)

## Passwords

Never empty, never the image's (`ubuntu`/`ubuntu`, OpenWrt's empty root). An update (keep) leaves the accounts and
their passwords as the device has them, as `mu300-update` does: the installer makes a password only for a system that
has no accounts of its own yet (a new filesystem, a wipe, a system added beside another, or one whose accounts are
still the image's: `.mu300-accounts-from-image`), or when a trusted file asks for one (`MU300_PASSWORD`, or
`MU300_PASSWORD_RESET=yes` for a generated one).

> Decision: on an update the installer passes no `PWHASH`, writes no password file (an existing one stays as it is)
> and says "unchanged" where it would show the password; `android-install.sh` carries the accounts over with
> `mu300-update`'s `merge_accounts` (a copy: it runs on Android and cannot source the new system's script; a test
> keeps the two the same): the old entry of every account both systems have, the users and groups added on the
> device. With a `PWHASH` (install.sh's update, or a reset) the merge runs too and the hash replaces only the
> `ubuntu`/`root` entry. Kept is only a real crypt hash of the system's own: an empty, locked or missing entry, a
> hash equal to the image's or (SHA-crypt, checked with the bundled `mkpasswd`) of the images' "ubuntu", an
> unreadable shadow or accounts still marked as the image's get a generated password instead. `android-install.sh`
> fails closed as well: without a `PWHASH` it installs a system only when its account ends up with a real hash that
> is not the image's, otherwise the old system stays, the install stops and the boot image is never armed. Rejected: a new password on every update (seen on the U30 Air: the user's own password was
> replaced by a generated one, and SSH with it stopped working after an update that was meant to change nothing).

> Decision: the installer generates 12 characters from `/dev/urandom` (alphabet without look-alikes: no 0/O, 1/l/I;
> rejection sampling, so no bias) unless `MU300_PASSWORD` gives one of 6+ characters. It is hashed on the device to
> SHA-512 crypt with the bundled static busybox's `mkpasswd` (password on stdin, 16-character random salt) and
> passed as `PWHASH`, exactly what `install.sh` passes, so `android-install.sh` and the images do not change. The
> password is written to `/data/adb/mu300-linux-password.txt` (mode 600, root's: the user may have no other way to
> read the output later, and `su -c cat` reads it) and shown in the Magisk output before anything is installed, and
> shown again at the end, between quotes (a password of the conf file may hold blanks); the report says to delete
> that file after the first login. Only `MU300_PASSWORD_FILE=sdcard` in a trusted file writes it to
> `/sdcard/mu300-linux-password.txt` instead. `MU300_PASSWORD` and `MU300_PASSWORD_RESET` are taken only from a
> trusted file, and such a line there is replaced by a comment once it has been used.
>
> Rejected: writing it to `/sdcard` by default (every app with storage access could read the root password of a
> device on the LAN; the file was meant to be deleted, and often would not be); an empty or fixed password (kanoqwq's empty root password); a forced change on first login (the account
> would be open with a known password on the LAN and Wi-Fi until someone logs in); storing the plaintext in the root
> filesystem for a first-boot service to set (plaintext at rest, and a failing service leaves the known password);
> computing SHA-512 crypt in awk (busybox awk has no 64-bit integers); a CI-built hashing binary (the release's
> busybox already does it and is already published).

## Safety

The guarantees of `install.sh`/`android-install.sh`, kept, and how:

* **Android stays the fallback.** Android's own boot partition (`boot_<android slot>`), `vbmeta`, the GPT and
  `userdata` are never written. The installer resolves both `by-name` links and refuses when the Linux slot's
  partition is the one Android booted from. Before step 8 nothing on the eMMC or card is written.
* **No foreign ext4 is formatted; `mmcblk0` is never the card.** The card is chosen by `storage.sh`'s `sd_probe`
  (type `SD`, not `mmcblk0`) and prepared by `android-install.sh`'s existing `sd_check`/`sd_release`/`sd_prepare`,
  unchanged. On top of that the Magisk path formats a card only with `MU300_SD_ERASE=yes` from a trusted file,
  whatever the reason for the format (a new card, or `MU300_MODE=wipe` on a `mu300sd` one).
* **Android's tools for the device side.** Magisk runs module scripts with `ASH_STANDALONE=1`: every command name is
  a busybox applet. Busybox `mke2fs` makes ext2 only and busybox `losetup` has no `-S` (kanoqwq hit the first). So
  `android-install.sh` and `android-mount-mu300root.sh` run as `env -u ASH_STANDALONE PATH=/system/bin:/system/xbin:/vendor/bin /system/bin/sh ...`,
  the environment `su -c sh` gives them under `install.sh`.
* **The boot image is verified after writing** (`write_boot`: sha256 of image + vbmeta, sha256 of the footer).
* **`misc` is armed last**, only after the systems installed (`MU300-INSTALL-OK`), the image verified, and the
  armed block read back. A failure at any step before leaves `misc` as it was: Android boots, and the Linux slot
  is only ever booted when `misc` says so.
* **The block is computed from the live one** (magic and CRC checked first), as the switch module does; the four
  blocks in the image come from the same read.
* **The rescue timer stays 300 s**; default-boot failures fall back after `BOOT_ATTEMPTS` (5).
* **What root checks is what root uses.** `/data/local/tmp` belongs to the shell user and `/sdcard` to every app
  with storage access: a file there can be replaced between the check and the use, and the installer would run or
  install the replacement as root. Everything the installer checks and then uses - the payload copies, the copies
  of `android-install.sh` and the mount helper, `mu300-install.env`, the vendor tarballs, the mount points - lives
  in one directory made for this run with `mktemp -d` under `/data/adb` (mode 700). `android-install.sh` takes its
  directory from `MU300_DEVICE_WORK` (default `/data/local/tmp`, so the computer installer, which pushes there over adb,
  is unchanged). The Magisk path reads and writes nothing in `/data/local/tmp`.

  > Decision: a fresh root-only directory per run. Rejected: a fixed `/data/local/tmp/mu300-magisk` (the shell user
  > can swap the scripts or the tarballs after the sha256 check, and a fixed name meets a previous run's leftovers);
  > re-checking files just before use (still a window, and the scripts would be checked by what they replace).
* **No filesystem mounted in the work directory is ever deleted.** The inspection mount (read-only, `ro,noload`,
  so a dry run does not even replay the journal) and `android-install.sh`'s mount points live in the work directory.
  Leaving, the installer removes its entries one by one: a mount point only ever with `rmdir`, anything else only
  when `/proc/mounts` lists nothing at or below it (an unreadable mount table counts as mounted). A failed unmount
  leaves the filesystem and its mount point in place and says so. A run killed while a filesystem was mounted (no
  exit trap) leaves its own directory, which no later run uses or removes - each has a new name.

  > Rejected: `rm -rf` of the work directory after an unmount (an unmount that fails, or a mount left by a killed
  > run at a fixed path, and the installed systems and `/home` go with it).
* **Proprietary files never leave the device.** The work directory is deleted on every exit path (`trap`), apart
  from a filesystem still mounted in it; the CI audit (below) fails a zip that contains anything outside the
  allow-list.
* **The manifest is checked before any path is made from it.** Every field must be exactly a value a release
  has: the system, its Ubuntu release and its rootfs asset one of the three combinations, the kernel and its asset
  one of the three, the checksums 64 lowercase hex digits, the tag a `v` tag without `/`, `..` or blanks. A
  manifest with anything else is refused before the payload is touched.
* **The payload is checked** against the manifest's sha256 before use; the manifest itself is inside the zip the
  user downloaded over HTTPS from the release, and `SHA256SUMS-magisk` lets them check the zip.

## Opposite slot: Android on slot b, Linux on slot a

An Android OTA (or a user) can leave Android running from slot b. Today everything assumes Android on a and Linux on
b: `install.sh`, `boot/init` (restores the slot-a block, persists its log in `boot_b`, rewrites the vendor
daemons' slot suffix from `_b` to `_a`), `build-boot-image.py` (two blocks), `mu300-next-boot`, `mu300-update`
(`part_dev boot_b`), `early-recorder`, `android-vendor-start`, `tools/collect-logs.sh` and the switch module.
kanoqwq's 35a1c55 makes these slot-aware. This is its own piece of work (Tasks 1-3 of the plan), done before the
installer, which needs it to support both slots:

* **The image says which slot it was built for**: `etc/mu300-linux-slot` (`a`/`b`) in the device segment, written
  by `build-boot-image.py --linux-slot` (default `b`) and by the Magisk installer. All four blocks are in every
  image.
* **init** takes the slot LK actually booted from `androidboot.slot_suffix` (`/proc/cmdline`, else the DT bootargs,
  the same fallback init already has), else `etc/mu300-linux-slot`, else `b`, and logs the source and any mismatch.
  It restores Android's slot (`restore_android`), persists its log in `boot_<linux slot>`, rewrites the vendor
  daemons' suffix to Android's slot, and publishes `/run/mu300/linux-slot`, `misc-bc-android.bin` and
  `misc-bc-linux-trial.bin`.
* **Old names stay only where they are right.** With Linux on b, init also writes the old
  `misc-bc-slot-a.bin`/`misc-bc-slot-b-trial.bin` for an older `mu300-next-boot`. With Linux on a it does not: an
  older `mu300-next-boot android` would write the slot-a block, which there means "boot Linux, successful" - a
  device that never returns to Android by itself. Without the files the old tool fails and writes nothing.
* **Userspace** (`mu300-next-boot`, `mu300-update`, `early-recorder`, `android-vendor-start`) reads
  `/run/mu300/linux-slot` (missing: `b`). `collect-logs.sh` reads the persistent log from the slot Android is not on.
  `switch.sh` arms the slot opposite Android, refusing when both boot partitions hold the same image.
* **Out of scope here:** `install.sh`, `install.ps1`, `uninstall.*`, `boot/flash-trial.sh` keep requiring Android on
  slot a; their messages already say so. A Magisk install with Android on b is the supported way.

Taken from kanoqwq: the four-block layout and their byte values, the cmdline rewrite in both directions, the
`mu300-next-boot` byte offset per slot. Not taken: reading the slot from `misc` before the cmdline (the block's
suffix is ours - we write it - so it says what we armed, not what LK booted), and the 90 s timer.

## CI

> Decision: a new workflow, `.github/workflows/magisk.yml`, on `release: published` (fires for prereleases too,
> which is how releases are tested first) and on `workflow_dispatch` with a tag. It checks out **the tag** (the
> installer must match the release's init and `mu300-update`), downloads the release's assets with `gh release
> download`, verifies them against `SHA256SUMS`, runs `tools/make-magisk-zips.sh`, tests the result, and uploads the
> zips and `SHA256SUMS-magisk` to the same release with `gh release upload --clobber` (`contents: write`).
> Rejected: building the zips inside `tools/make-release.sh` on the maintainer's machine (the user asked for CI;
> CI builds them from exactly the published bytes); adding the zips to `SHA256SUMS` (that file is what
> `install.sh` and `mu300-update` trust; rewriting it after publication is the stale-checksum problem of
> v2026.09.28 again).

`tools/make-magisk-zips.sh RELEASE_DIR OUT_DIR` can be run locally too. It refuses assets that fail `SHA256SUMS`,
writes each zip from a staging directory with a fixed file order, stores `payload/` uncompressed, and audits every
zip against an exact allow-list of entry names (anything else - a `boot_a.img`, a `misc` dump, firmware, an
`android/` tree - fails the build).

Tests in CI:

* `tests.yml` (every push/PR, Ubuntu and macOS, every shell) runs the new unit tests below; they use tiny fake
  releases, so no download.
* `installer.yml` adds the installer scripts to its shell syntax check and `check-i18n.py` gains them as sources.
* `magisk.yml` runs, after building, `tests/test_magisk_zip.py` against the real zips (`MU300_MAGISK_ZIPS=out`): the
  allow-list, the manifest's hashes against the payload, and a dry run of each zip's own `mu300/install.sh` against
  a fake device (stub `getprop`, `sm`, `magiskboot`, block devices as files) that must print the plan and write
  nothing.

## Testing

Unit tests (Python `unittest`, standard library plus `lz4`, every shell available, stubs as in `tests/helpers.py`):

* `test_boot_image.py`: `--linux-slot a|b` writes `etc/mu300-linux-slot` and all four blocks, with kanoqwq's bytes.
* `test_boot_init.py`: the slot functions cut out of init: cmdline, DT bootargs, file, default; mismatch logged;
  `restore_android` writes the block of Android's slot; legacy names only for slot b.
* `test_update.py`: `bootimg_from_stock` gives the same bytes as `build-boot-image.py` for the same stock image,
  kernel and ramdisk (the experiment above, made permanent); `kernel_modules_into_systems` lays out modules as
  `boot_update` does; `linux_slot`/`part_dev` follow `/run/mu300/linux-slot`.
* `test_device_scripts.py`: `mu300-next-boot` (bash) arms slot a with byte 12 when Linux is on a.
* `test_android_boot_image.py` (new): `bc_block` equals `build-boot-image.py`'s blocks for the same `misc` head;
  `collect_subset`/`collect_firmware` against a fake `/`; the device segment decodes to the expected files; a whole
  image built from a fake stock image, misc and bundle holds, merged over its segments, the same files as the image
  `build-boot-image.py` builds from the same inputs.
* `test_magisk_installer.py` (new): the conf parser (quotes, CRLF, unknown keys, a `$(...)` value is not run),
  the storage/mode/compatibility decisions as a table, password generation and its hash against
  `tools/sha512crypt.py`, and a full run against a fake device: the Linux slot's fake partition holds the new image,
  `misc` holds the trial block, Android's partition and the eMMC file are unchanged, and the staging directory is
  gone; every refusal path leaves `misc` and the boot partitions untouched.
* `test_magisk_zip.py` (new): the builder on a fake release (names, allow-list, stored payload, manifest, no 26.04
  on 5.4), and `customize.sh` in a fake Magisk environment (child exit status → `abort`; module files only after
  success; Magisk's shell options unchanged).
* `test_static.py`: the new scripts parse under every shell; `customize.sh` contains no `set -e`/`set -u`; nothing
  in the installer writes to `boot_$ANDROID_SLOT`.

On the devices (Task 14; the F50 test board and the U30 Air, both rooted with Magisk 30.7, Android on slot a):

1. F50, internal region with an existing installation: OpenWrt k6.18 zip from the Magisk app (scrcpy), default
   answers → update path; reboot; OpenWrt boots; the generated password works over SSH; `mu300-update boot` from
   this state; the module's Action button after `mu300-next-boot android`.
2. F50, SD card: without the conf the zip refuses (nothing written; `misc` unchanged); with `MU300_STORAGE=sd`,
   `MU300_SD_ERASE=yes`, Ubuntu 24.04 k5.4 via `magisk --install-module` over adb; boots from the card.
3. U30 Air: Ubuntu 26.04 k7.2 to internal storage, then the OpenWrt k7.2 zip as the second system; `mu300-os`
   switches both ways.
4. Failure injection on the F50: a zip with a corrupted payload (refused before writing); `MU300_DRY_RUN=1`
   (nothing written); a foreign ext4 card (refused); `magiskboot` renamed away (refused).
5. Reboot loop of OpenWrt on 5.4 installed this way (the pre-release check from the update incident).
6. Android on slot b: only on a board whose slot b holds a complete Android (after an OTA) and only with a full
   backup (`tools/backup-full.sh`) and the serial console attached. If no such board is available, the slot-b path
   ships tested by unit tests only and the release notes say so.

`getevent -lp` on both boards records which keys exist (it settles the volume-key question for good).

## What can go wrong

| case | behaviour |
|---|---|
| no Magisk `magiskboot`/busybox (KernelSU, APatch, very old Magisk) | refused before anything is read or written |
| flashed from recovery | refused (`BOOTMODE` false): the installer needs the running Android (`sm`, `getprop`, vendor files) |
| unknown model name | refused unless `MU300_DEVICE` is set |
| corrupt or truncated zip | payload sha256 mismatch: refused before writing |
| `/data` too full | refused before unpacking, with the space needed |
| no room inside, no card | refused; message names the SD card and the computer installer's repartition |
| card with FAT/exFAT data | refused without `MU300_SD_ERASE=yes` |
| foreign ext4 on the card | refused (also with `MU300_SD_ERASE=yes`: `sd_prepare` never formats it) |
| card adopted by Android, or Android keeps it mounted | refused by `sd_release`, nothing written |
| internal region not empty | refused without `MU300_REGION_OVERWRITE=yes` |
| Ubuntu 26.04 on the filesystem and a 5.4 zip | refused (26.04 cannot run on 5.4) |
| SD install with a bundle without `sdcard` in `features` | refused (all current bundles have it) |
| this device's firmware or modem subset incomplete | refused with the missing names (the system would boot without Wi-Fi or modem) |
| GPU closure incomplete | GPU skipped with a message, install continues |
| `android-install.sh` fails | stops; boot partition and `misc` unchanged; the Linux filesystem may hold a half-installed `<os>.new` that the next run removes |
| boot image read-back mismatch | stops; `misc` unchanged, Android boots; the Linux slot holds a broken image that is never booted until a later install |
| `misc` write fails or reads back wrong | the block it held at the start is written back and read back: Android boots as before. If that fails too: says so, keeps the block in `/data/adb/mu300-misc-bc-before.bin` and prints the command that writes it back |
| power lost during the install | before `misc` is armed: Android boots. After: the armed trial boots the new Linux |
| Linux does not boot | LK's tries and the 300 s timer return to Android; the module's Action or a new flash retries |
| Android OTA later | the OTA writes the inactive slot - the Linux one - so Linux disappears and Android switches slot; installing the zip again puts Linux on the other slot (needs the opposite-slot work) |
| old `mu300-next-boot` with Linux on slot a (a downgraded rootfs) | finds no legacy block files and writes nothing |
| the conf file holds a password | used, then the line is replaced by a comment |
| update of a system with accounts of its own | accounts and passwords kept (no `PWHASH`, the password file not touched); a trusted `MU300_PASSWORD` or `MU300_PASSWORD_RESET=yes` sets a new one |
| the trusted conf holds `MU300_MODE=wipe`, `MU300_SD_ERASE=yes` or `MU300_REGION_OVERWRITE=yes` | once a successful run used it, the line is replaced by a comment; one the run did not use stays |
| the module is removed in Magisk | only the switch goes; Linux stays and boots as configured |

## Out of scope

* Shrinking `userdata` from the Magisk installer (stays with the computer installer).
* Uninstalling Linux from Android (removing the module only removes the switch; `uninstall.sh` stays).
* A zip that downloads its payload on the device (possible later, see the decision above).
* The third system (OpenWrt with kanoqwq's LuCI app): it becomes three more zips (`openwrt-luci-k*`) once it is a
  release asset; the manifest and the builder take the system list from one table.
* Making `install.sh`/`install.ps1`/`uninstall.*`/`flash-trial.sh` work with Android on slot b.
* Moving `install.sh` onto the device-side boot image builder (possible later: it would leave one builder).
* KernelSU and APatch.
