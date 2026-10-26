# Magisk Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Magisk zip per (system, kernel), built by CI for every release, that installs Linux from Android on the F50 or the U30 Air with no computer: the boot image and the vendor files are made on the device from the device's own data, the device stays bootable at every step, and Android may run from slot a or slot b.

**Architecture:** The boot image layout lives in one place on the device: `mu300-update` (sourced with `MU300_LIB=1`) gets `bootimg_from_stock` next to its existing `write_boot`. `tools/android-boot-image.sh` makes what only the device can make (misc blocks, the Android subset, the device ramdisk segment via busybox `cpio` + `magiskboot compress=lz4_legacy`). `android/magisk/installer/mu300-install.sh` runs the whole installation as a child of Magisk's busybox, deciding from what is on the device and an optional `mu300-install.conf`, and drives the unchanged `tools/android-install.sh` with Android's own shell. Before that, init and the userspace learn which slot Linux is on (`etc/mu300-linux-slot`, `/run/mu300/linux-slot`). `tools/make-magisk-zips.sh` packs eight zips from a release's published assets; `.github/workflows/magisk.yml` runs it on every published release.

**Tech Stack:** POSIX sh (dash, bash, busybox ash, Android mksh + toybox, Magisk's busybox ash in standalone mode), Python 3 `unittest` (standard library plus `lz4`), Info-ZIP `zip`/`unzip`, GitHub Actions + `gh`.

**Spec:** `docs/superpowers/specs/2026-10-05-magisk-installer-design.md`

## Global Constraints

- No file from any device in a zip or a release asset. `tools/make-magisk-zips.sh` audits every zip against an exact allow-list of entry names; a zip with anything else fails the build.
- Android's own boot partition (`boot_<android slot>`), `vbmeta`, the GPT and `userdata` are never written. `misc` is written last, only after `MU300-INSTALL-OK` and a verified boot image, and is read back.
- An ext4 on the card whose label is not `mu300sd` is never formatted; `mmcblk0` is never the card (the existing `sd_check`/`sd_prepare` stay as they are). The Magisk path formats a card only with `MU300_SD_ERASE=yes`.
- The safety timer in `boot/init` stays `sleep 300`.
- `customize.sh` never runs `set -e`, `set -u` or `exit`: the installation is a child process.
- `tools/android-install.sh` and `tools/android-mount-mu300root.sh` run with Android's shell and toolbox: `env -u ASH_STANDALONE PATH=/system/bin:/system/xbin:/vendor/bin /system/bin/sh ...` (Magisk's standalone busybox has an ext2-only `mke2fs` and a `losetup` without `-S`).
- Android's `mksh` has 32-bit arithmetic: byte counts are computed in the installer (Magisk's busybox ash, 64-bit), and handed to `android-install.sh` in sectors as `install.sh` does.
- Every user-facing message of the installer goes through `t '...'` and gets a line in `i18n/tr.tsv` and `i18n/zh.tsv`; `python3 tools/check-i18n.py` prints nothing missing. (`customize.sh`'s three fallback lines stay English: they run before the language is known.)
- Scripts parse under dash, bash and busybox ash (`tests/test_static.py`); comments are plain sentences that say why, as in the surrounding files.
- Unit tests: `python3 -m unittest discover -s tests` passes on a machine with dash, bash, busybox, lz4 (the command or the Python module), zip and unzip.
- Commit messages follow the repository's style (one sentence, no `feat:` prefix) and end with the `Co-Authored-By:` and `Claude-Session:` lines of the implementing session.
- Nothing is released from this branch: no `tools/make-release.sh --publish`, no `gh release`. Task 11's workflow is exercised with `workflow_dispatch` only after merge, on a prerelease.

## Review Focus

- Linux on slot a with a rootfs whose `mu300-next-boot` predates this work: it must find no block files and write nothing, never the slot-a block (which then means "Linux, successful"). Pinned in Task 2 (`test_legacy_names_only_for_slot_b`) and Task 3 (`test_slot_a_never_falls_back_to_legacy_names`).
- `ASH_STANDALONE` leaking into `android-install.sh` (busybox `mke2fs` would make ext2). Pinned in Task 8 (`test_android_side_runs_with_android_tools`).
- `misc` changing between the plan and the arming (a second installer, an OTA): the arming must refuse. Pinned in Task 8 (`test_misc_changed_meanwhile_is_not_armed`).
- A conf value like `$(reboot)` or `` `id` `` must never run. Pinned in Task 7 (`test_conf_is_never_executed`).
- AVB vbmeta at an offset that is not page aligned in Android's image. Pinned in Task 4 (`test_unaligned_vbmeta`).
- A refusal at any step leaves both boot partitions, `misc` and the eMMC file byte for byte unchanged and removes the staging directory. Pinned in Task 8 (`test_refusals_write_nothing`).

---

### Task 1: The boot image says which slot it is for

**Files:**
- Modify: `boot/build-boot-image.py` (`bootloader_control`, `main`)
- Test: `tests/test_boot_image.py` (new class `SlotBlocks`)

**Interfaces:**
- Produces: in every device ramdisk segment `etc/mu300-linux-slot` (`a` or `b`) and four blocks: `etc/misc-bc-slot-a.bin` (Android on a), `etc/misc-bc-slot-b-trial.bin` (Linux trial on b), `etc/misc-bc-slot-b.bin` (Android on b), `etc/misc-bc-slot-a-trial.bin` (Linux trial on a). The manifest gains `misc_slot_b_hex`, `misc_slot_a_trial_hex`, `linux_slot`. `--linux-slot a|b`, default `b`. Tasks 2, 5 rely on these names and bytes.

- [ ] **Step 1: Write the failing test** — add to `tests/test_boot_image.py`:

```python
import struct
import zlib


def fake_stock(path, size=4 << 20, vbmeta_offset=1 << 20, vbmeta=b'V' * 2304):
    """An Android boot image header v4 with an AVB footer, as far as build-boot-image.py and mu300-update read it."""
    img = bytearray(size)
    img[0:8] = b'ANDROID!'
    struct.pack_into('<I', img, 40, 4)
    img[4096:8192] = b'k' * 4096
    img[vbmeta_offset:vbmeta_offset + len(vbmeta)] = vbmeta
    footer = bytearray(64)
    footer[0:4] = b'AVBf'
    struct.pack_into('>IIQQQ', footer, 4, 1, 0, vbmeta_offset, vbmeta_offset, len(vbmeta))
    img[-64:] = footer
    path.write_bytes(bytes(img))


# the slot-a block of the F50 test board: AOSP bootloader_control, nothing device-specific in it
LIVE_A = bytes.fromhex('5f61000042434142010200009f001e000000000000000000000000000be17146')


def fake_misc(path, live=LIVE_A):
    path.write_bytes(bytes(0x800) + live + bytes(4096 - 0x800 - 32))


def with_slots(live, suffix, a, b):
    x = bytearray(live)
    x[0:4] = suffix
    x[12] = a
    x[14] = b
    x[28:32] = struct.pack('<I', zlib.crc32(bytes(x[:28])))
    return bytes(x)


# Move GenericRamdisk's setUp, tearDown and build into a base class `Fixtures(unittest.TestCase)` without tests, and
# derive both GenericRamdisk and SlotBlocks from it (deriving from GenericRamdisk would run its tests twice).
@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class SlotBlocks(Fixtures):
    def image(self, *extra):
        fake_stock(self.tmp / 'stock.img')
        fake_misc(self.tmp / 'misc.bin')
        (self.tmp / 'Image').write_bytes(b'\x7fkernel' * 100)
        out = self.tmp / 'boot.img'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--stock-boot', str(self.tmp / 'stock.img'),
                            '--misc-head', str(self.tmp / 'misc.bin'), '--kernel', str(self.tmp / 'Image'),
                            '--modules', str(self.mods), '--busybox', str(self.tmp / 'busybox'),
                            '--logdw', str(self.tmp / 'logdw'), '--device', 'f50',
                            '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'),
                            '--out', str(out), *extra], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        img = out.read_bytes()
        ksz, rsz = struct.unpack_from('<II', img, 8)
        roff = 4096 + (ksz + 4095) // 4096 * 4096
        return cpio_files(unlz4_legacy(img[roff:roff + rsz]))

    def test_all_four_blocks(self):
        files = self.image()
        want = {'etc/misc-bc-slot-a.bin': with_slots(LIVE_A, b'_a\0\0', 0x9f, 0x1e),
                'etc/misc-bc-slot-b-trial.bin': with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f),
                'etc/misc-bc-slot-b.bin': with_slots(LIVE_A, b'_b\0\0', 0x1e, 0x9f),
                'etc/misc-bc-slot-a-trial.bin': with_slots(LIVE_A, b'_a\0\0', 0x2f, 0x9e)}
        for name, data in want.items():
            self.assertEqual(files[name][1], data, name)

    def test_slot_default_and_choice(self):
        self.assertEqual(self.image()['etc/mu300-linux-slot'][1], b'b\n')
        self.assertEqual(self.image('--linux-slot', 'a')['etc/mu300-linux-slot'][1], b'a\n')

    def test_generic_has_no_slot(self):
        r, _ = self.build('--linux-slot', 'a')
        self.assertNotEqual(r.returncode, 0)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd tests && python3 -m unittest test_boot_image.SlotBlocks -v`
Expected: FAIL (`KeyError: 'etc/misc-bc-slot-b.bin'`, and `--linux-slot` unknown).

- [ ] **Step 3: Four blocks in `bootloader_control`** — replace its body after the CRC check:

```python
    # slot_info byte: priority (4 bits) | tries_remaining (3 bits) | successful_boot (1 bit)
    # Unisoc LK treats tries==1 && !successful as an already failed boot, so a one-shot trial needs tries=2 (LK
    # decrements to 1; a failed boot then rolls back to Android's slot). Linux goes on the slot Android is not on,
    # and Android may be on either (after an OTA it is often b): one pair of blocks for each case.
    android_a = with_slots(b'_a\0\0', 0x9f, 0x1e)        # a: prio 15, successful
    linux_b_trial = with_slots(b'_b\0\0', 0x9e, 0x2f)    # a: prio 14 successful, b: prio 15 tries 2
    android_b = with_slots(b'_b\0\0', 0x1e, 0x9f)        # b: prio 15, successful
    linux_a_trial = with_slots(b'_a\0\0', 0x2f, 0x9e)    # b: prio 14 successful, a: prio 15 tries 2
    return android_a, linux_b_trial, android_b, linux_a_trial
```

- [ ] **Step 4: `--linux-slot` and the files** — add the argument next to `--device`:

```python
    ap.add_argument('--linux-slot', choices=['a', 'b'],
                    help='the slot this image goes to (default b; a when Android runs from slot b): written to '
                         '/etc/mu300-linux-slot, which init uses when the kernel command line does not say')
```

in the `if a.generic_ramdisk:` checks next to the `--device` refusal add
`if a.linux_slot: ap.error('--linux-slot does not go into a generic ramdisk')`, and replace the two
`files['etc/misc-bc-...']` lines with:

```python
    android_a_bc, linux_b_bc, android_b_bc, linux_a_bc = bootloader_control(a.misc_head.read_bytes())
    linux_slot = a.linux_slot or 'b'
    files['etc/misc-bc-slot-a.bin'] = (android_a_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-b-trial.bin'] = (linux_b_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-b.bin'] = (android_b_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-a-trial.bin'] = (linux_a_bc, stat.S_IFREG | 0o644)
    files['etc/mu300-linux-slot'] = (linux_slot.encode() + b'\n', stat.S_IFREG | 0o644)
```

At the end, `slot_b_bc` becomes `linux_b_bc` in the `.misc-slot-b-trial.bin` side file (install.sh pushes it), and
the manifest gets:

```python
        'misc_slot_b_trial_hex': linux_b_bc.hex(),
        'misc_slot_a_hex': android_a_bc.hex(),
        'misc_slot_b_hex': android_b_bc.hex(),
        'misc_slot_a_trial_hex': linux_a_bc.hex(),
        'linux_slot': linux_slot,
```

- [ ] **Step 5: Run the tests** — `cd tests && python3 -m unittest test_boot_image -v` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add boot/build-boot-image.py tests/test_boot_image.py
git commit -m "build-boot-image: every image carries the misc blocks for Linux on either slot and says which slot it is for"
```

---

### Task 2: `boot/init` on either slot

**Files:**
- Modify: `boot/init` (new block between `# --- slot begin` / `# --- slot end`; `find_partitions`, `write_misc_bc`, every `restore_slot_a`, the `/run/mu300` publishing, the vendor daemons' command line)
- Test: `tests/test_boot_init.py` (new class `Slot`), `tests/test_static.py` (`Rules`)

**Interfaces:**
- Consumes: `etc/mu300-linux-slot`, the four `etc/misc-bc-slot-*.bin` (Task 1).
- Produces: `LINUX_SLOT`/`ANDROID_SLOT` in init; `/run/mu300/linux-slot`, `/run/mu300/misc-bc-android.bin`, `/run/mu300/misc-bc-linux-trial.bin`; the legacy `/run/mu300/misc-bc-slot-a.bin` and `misc-bc-slot-b-trial.bin` only when Linux is on b. The persistent log goes to `boot_$LINUX_SLOT`. Task 3 reads these.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_boot_init.py`:

```python
class Slot(ShellTest):
    """The slot block of init: which slot Linux booted from, which block restores Android, what userspace gets."""

    def block(self):
        m = re.search(r'# --- slot begin\n(.*?)# --- slot end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no slot block')
        return m.group(1)

    def run_slot(self, shell, cmdline=None, bootargs=None, image=None, call='linux_slot_detect; publish_slot'):
        etc, run = self.tmp / 'etc', self.tmp / 'run'
        shutil.rmtree(run, ignore_errors=True)
        etc.mkdir(exist_ok=True)
        for n in ('slot-a', 'slot-b-trial', 'slot-b', 'slot-a-trial'):
            (etc / f'misc-bc-{n}.bin').write_text(n)
        (etc / 'mu300-linux-slot').unlink(missing_ok=True)
        if image:
            (etc / 'mu300-linux-slot').write_text(image + '\n')
        srcs = []
        for name, text in (('cmdline', cmdline), ('bootargs', bootargs)):
            if text is not None:
                (self.tmp / name).write_bytes(text.encode() + b'\0')
                srcs.append(str(self.tmp / name))
        code = (f'log() {{ echo "$*" >> "{self.tmp}/log"; }}\n'
                f'write_misc_bc() {{ echo "write $1" >> "{self.tmp}/log"; }}\n' + self.block() + f'\n{call}\n'
                'echo "linux=$LINUX_SLOT android=$ANDROID_SLOT"')
        (self.tmp / 'log').write_text('')
        r = self.sh(shell, code, MU300_CMDLINE_SRC=' '.join(srcs) or f'{self.tmp}/none', MU300_ETC=etc, MU300_RUN=run)
        self.assertEqual(r.stderr, '')
        return r.stdout.strip(), (self.tmp / 'log').read_text(), run

    def test_cmdline_names_the_slot(self):
        for shell in self.each_shell():
            out, log, run = self.run_slot(shell, cmdline='console=x androidboot.slot_suffix=_a loglevel=5')
            self.assertEqual(out, 'linux=a android=b')
            self.assertIn('stage=linux-slot slot=a source=', log)
            self.assertEqual((run / 'mu300' / 'linux-slot').read_text(), 'a\n')
            self.assertEqual((run / 'mu300' / 'misc-bc-android.bin').read_text(), 'slot-b')
            self.assertEqual((run / 'mu300' / 'misc-bc-linux-trial.bin').read_text(), 'slot-a-trial')

    def test_bootargs_when_the_kernel_replaced_the_cmdline(self):
        for shell in self.each_shell():
            out, _, _ = self.run_slot(shell, cmdline='loglevel=5', bootargs='androidboot.slot_suffix=_b')
            self.assertEqual(out, 'linux=b android=a')

    def test_image_then_default(self):
        for shell in self.each_shell():
            self.assertEqual(self.run_slot(shell, image='a')[0], 'linux=a android=b')
            out, log, _ = self.run_slot(shell)
            self.assertEqual(out, 'linux=b android=a')
            self.assertIn('source=default', log)

    def test_mismatch_is_logged_and_the_booted_slot_wins(self):
        for shell in self.each_shell():
            out, log, _ = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b', image='a')
            self.assertEqual(out, 'linux=b android=a')
            self.assertIn('stage=linux-slot-MISMATCH booted=b image=a', log)

    def test_restore_writes_androids_block(self):
        for shell in self.each_shell():
            _, log, _ = self.run_slot(shell, cmdline='androidboot.slot_suffix=_a', call='linux_slot_detect; restore_android')
            self.assertIn(f'write {self.tmp}/etc/misc-bc-slot-b.bin', log)
            _, log, _ = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b', call='linux_slot_detect; restore_android')
            self.assertIn(f'write {self.tmp}/etc/misc-bc-slot-a.bin', log)

    def test_legacy_names_only_for_slot_b(self):
        # an older mu300-next-boot writes misc-bc-slot-a.bin for "android": with Linux on a that block would boot
        # Linux, marked successful, for ever - so with Linux on a the old names must not exist at all
        for shell in self.each_shell():
            _, _, run = self.run_slot(shell, cmdline='androidboot.slot_suffix=_b')
            self.assertEqual((run / 'mu300' / 'misc-bc-slot-a.bin').read_text(), 'slot-a')
            self.assertEqual((run / 'mu300' / 'misc-bc-slot-b-trial.bin').read_text(), 'slot-b-trial')
            _, _, run = self.run_slot(shell, cmdline='androidboot.slot_suffix=_a')
            self.assertFalse((run / 'mu300' / 'misc-bc-slot-a.bin').exists())
            self.assertFalse((run / 'mu300' / 'misc-bc-slot-b-trial.bin').exists())
```

(add `import shutil` at the top) and to class `Rules` in `tests/test_static.py`:

```python
    def test_init_restores_androids_slot(self):
        # every restore goes through restore_android, so Linux on slot a returns to Android on b
        init = (TOP / 'boot' / 'init').read_text()
        self.assertNotIn('restore_slot_a', init)
        self.assertNotIn('slot_suffix=_b/androidboot.slot_suffix=_a', init)
        self.assertIn('sleep 300', init)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `cd tests && python3 -m unittest test_boot_init.Slot test_static.Rules.test_init_restores_androids_slot -v`
Expected: FAIL (`boot/init has no slot block`).

- [ ] **Step 3: The slot block** — insert in `boot/init` right before the comment `# misc and boot_b: where they are is known only once the eMMC is there`:

```sh
# Which slot this Linux runs from, and so which one is Android's (after an OTA Android often runs from b, and the
# Magisk installer then puts Linux on a). LK names the slot it booted on the command line; the mainline kernel
# replaces that line, so the bootargs LK put in the device tree come next, as for the vendor daemons below. Then
# the slot the image was built for (etc/mu300-linux-slot, from the installer), and b: every image before this one
# was built for b.
# --- slot begin
linux_slot_detect() {
    LINUX_SLOT=; _src=default
    for _f in ${MU300_CMDLINE_SRC:-/proc/cmdline /proc/device-tree/chosen/bootargs}; do
        [ -r "$_f" ] || continue
        LINUX_SLOT=$(tr '\000' ' ' < "$_f" | sed -n 's/.*androidboot\.slot_suffix=_\([ab]\).*/\1/p')
        [ -n "$LINUX_SLOT" ] && { _src=$_f; break; }
    done
    _img=$(cat "${MU300_ETC:-/etc}/mu300-linux-slot" 2>/dev/null)
    case $_img in a|b) ;; *) _img= ;; esac
    if [ -z "$LINUX_SLOT" ]; then
        LINUX_SLOT=${_img:-b}
        [ -n "$_img" ] && _src=image
    fi
    [ -z "$_img" ] || [ "$_img" = "$LINUX_SLOT" ] || log "stage=linux-slot-MISMATCH booted=$LINUX_SLOT image=$_img"
    if [ "$LINUX_SLOT" = a ]; then ANDROID_SLOT=b; else ANDROID_SLOT=a; fi
    log "stage=linux-slot slot=$LINUX_SLOT source=$_src"
}
restore_android() { write_misc_bc "${MU300_ETC:-/etc}/misc-bc-slot-$ANDROID_SLOT.bin" misc-restored; }
# what userspace needs to choose the next boot (mu300-next-boot): the slot and the two blocks of this pair. The old
# names, which an older mu300-next-boot reads, only where they mean what it thinks: Linux on b. With Linux on a its
# "android" would write the slot-a block - Linux, marked successful - and without the files it fails instead.
publish_slot() {
    _r=${MU300_RUN:-/run}/mu300 _e=${MU300_ETC:-/etc}
    mkdir -p "$_r"
    echo "$LINUX_SLOT" > "$_r/linux-slot"
    cp "$_e/misc-bc-slot-$ANDROID_SLOT.bin" "$_r/misc-bc-android.bin"
    cp "$_e/misc-bc-slot-$LINUX_SLOT-trial.bin" "$_r/misc-bc-linux-trial.bin"
    if [ "$LINUX_SLOT" = b ]; then cp "$_e/misc-bc-slot-a.bin" "$_e/misc-bc-slot-b-trial.bin" "$_r/"; fi
}
# --- slot end
linux_slot_detect
```

- [ ] **Step 4: Use it** — in `boot/init`:
  * in `find_partitions`, `grep -qx PARTNAME=boot_b` becomes `grep -qx "PARTNAME=boot_$LINUX_SLOT"` (the variable `bootb` keeps its name: it is the Linux slot's partition), and the comment above it says "misc and the Linux slot's boot partition";
  * `write_misc_bc` gets as its first line `[ -s "$1" ] || { log "stage=misc-block-missing $1"; return 1; }` (an image from before Task 1 has no `misc-bc-slot-b.bin`);
  * delete `restore_slot_a() { ... }` and replace every call `restore_slot_a` with `restore_android`;
  * `mkdir -p /run/mu300 && cp /etc/misc-bc-slot-a.bin /etc/misc-bc-slot-b-trial.bin /run/mu300/` becomes `publish_slot`;
  * the vendor daemons' command line (`sed 's/androidboot.slot_suffix=_b/androidboot.slot_suffix=_a/'`) becomes
    `sed "s/androidboot.slot_suffix=_$LINUX_SLOT/androidboot.slot_suffix=_$ANDROID_SLOT/"`, and its comment "LK boots
    us from slot b; the modem images Android uses (and keeps updated) are the _a copies" becomes "the modem images
    Android uses (and keeps updated) are the ones of Android's slot".
  * the comments that say "slot a"/"boot_b" for these things say "Android's slot"/"the Linux slot's boot partition".

Check with `grep -n 'slot_a\|boot_b\|_a\b' boot/init` that nothing slot-specific is left except inside the slot block.

- [ ] **Step 5: Run the tests** — `cd tests && python3 -m unittest test_boot_init test_static -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add boot/init tests/test_boot_init.py tests/test_static.py
git commit -m "init: Linux may run from slot a with Android on b; the booted slot comes from LK, the image says which it was built for"
```

---

### Task 3: The running system and the tools on either slot

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-next-boot`, `rootfs/overlay/opt/mu300/bin/mu300-update` (`linux_slot`, `boot_update`, `rollback_boot`), `rootfs/overlay/opt/mu300/bin/early-recorder`, `rootfs/overlay/opt/mu300/bin/android-vendor-start`, `tools/collect-logs.sh`, `android/magisk/mu300-linux-switch/switch.sh`, `android/magisk/README.md`
- Test: `tests/test_device_scripts.py` (new class `NextBoot`), `tests/test_update.py` (`test_linux_slot`), `tests/test_magisk_switch.py` (new)

**Interfaces:**
- Consumes: `/run/mu300/linux-slot`, `misc-bc-android.bin`, `misc-bc-linux-trial.bin` (Task 2).
- Produces: `linux_slot` in `mu300-update` (prints `a` or `b`, `b` without the file; honours `MU300_SYSROOT`). `mu300-next-boot` honours `MU300_RUN` (default `/run/mu300`) and `MU300_CONF` (default `/etc/mu300/default-boot`) for tests. `switch.sh` arms the slot opposite Android and honours `MU300_BY_NAME`, `MU300_SLOT_SUFFIX`, `MU300_BUSYBOX` for tests.

- [ ] **Step 1: Write the failing tests** — `tests/test_device_scripts.py`:

```python
class NextBoot(ShellTest):
    """mu300-next-boot (bash) re-arms the Linux slot with tries N+1 in the slot byte of whichever slot Linux is on."""

    def setUp(self):
        super().setUp()
        import importlib.util
        spec = importlib.util.spec_from_file_location('bbi', TOP / 'boot' / 'build-boot-image.py')
        self.bbi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bbi)
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()
        self.misc = self.tmp / 'misc'
        live = bytes.fromhex('5f61000042434142010200009f001e000000000000000000000000000be17146')
        self.misc.write_bytes(bytes(0x800) + live + bytes(2016))
        self.blocks = self.bbi.bootloader_control(self.misc.read_bytes())   # android_a, linux_b, android_b, linux_a
        (self.run_dir / 'misc-dev').write_text(str(self.misc))
        (self.tmp / 'default-boot').write_text('linux\n')

    def nb(self, *args):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        return subprocess.run(['bash', str(BIN / 'mu300-next-boot'), *args], capture_output=True, text=True,
                              env=self.env(MU300_RUN=self.run_dir, MU300_CONF=self.tmp / 'default-boot'))

    def bc(self):
        return self.misc.read_bytes()[0x800:0x820]

    def test_slot_a(self):
        android_a, linux_b, android_b, linux_a = self.blocks
        (self.run_dir / 'linux-slot').write_text('a\n')
        (self.run_dir / 'misc-bc-android.bin').write_bytes(android_b)
        (self.run_dir / 'misc-bc-linux-trial.bin').write_bytes(linux_a)
        r = self.nb('--rearm')
        self.assertEqual(r.returncode, 0, r.stderr)
        # 5 attempts by default: tries 6 in slot a's byte (12), the rest as in the trial block, CRC fixed
        want = bytearray(linux_a)
        want[12] = 0x0f | (6 << 4)
        want[28:32] = struct.pack('<I', zlib.crc32(bytes(want[:28])))
        self.assertEqual(self.bc(), bytes(want))
        self.nb('android')
        self.assertEqual(self.bc(), android_b)

    def test_slot_b_as_before(self):
        android_a, linux_b, _, _ = self.blocks
        (self.run_dir / 'misc-bc-slot-a.bin').write_bytes(android_a)          # an older initramfs: old names only
        (self.run_dir / 'misc-bc-slot-b-trial.bin').write_bytes(linux_b)
        self.assertEqual(self.nb('--rearm').returncode, 0)
        self.assertEqual(self.bc()[14], 0x6f)
        self.nb('android')
        self.assertEqual(self.bc(), android_a)

    def test_slot_a_never_falls_back_to_legacy_names(self):
        android_a, linux_b, _, _ = self.blocks
        (self.run_dir / 'linux-slot').write_text('a\n')
        (self.run_dir / 'misc-bc-slot-a.bin').write_bytes(android_a)
        (self.run_dir / 'misc-bc-slot-b-trial.bin').write_bytes(linux_b)
        before = self.misc.read_bytes()
        r = self.nb('android')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.misc.read_bytes(), before)
```

(imports at the top of the file as needed: `shutil`, `struct`, `subprocess`, `zlib`, `BIN`, `TOP` from helpers.)

`tests/test_update.py`, in class `Update`:

```python
    def test_linux_slot(self):
        f = self.root / 'run/mu300/linux-slot'
        for shell in self.each_shell():
            f.unlink(missing_ok=True)
            self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), 'b')
            for v, want in (('a\n', 'a'), ('b\n', 'b'), ('x\n', 'b')):
                f.write_text(v)
                self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), want, v)
```

`tests/test_magisk_switch.py` (new): runs `switch.sh --dry-run` with `MU300_BY_NAME` pointing at a directory of files
(`misc` built with `fake_misc` from `test_boot_image`, `boot_a`/`boot_b` with different first 64 KiB, both starting
with `ANDROID!`), `MU300_BUSYBOX=$(which busybox)` (skip without busybox), `MU300_SLOT_SUFFIX=_a` and `_b`, a stub
`id` printing `0`, and asserts that the printed `misc  new:` line equals `linux_b_trial` resp. `linux_a_trial` from
`bootloader_control`, and that with identical boot images it refuses.

- [ ] **Step 2: Run them to make sure they fail**

Run: `cd tests && python3 -m unittest test_device_scripts.NextBoot test_update.Update.test_linux_slot test_magisk_switch -v`
Expected: FAIL (`MU300_RUN` ignored: the script reads `/run/mu300`; `linux_slot: not found`).

- [ ] **Step 3: `mu300-next-boot`** — replace the `CONF=`/`BC_A=`/`BC_B=`/`MISC=` lines:

```bash
CONF=${MU300_CONF:-/etc/mu300/default-boot}
RUN=${MU300_RUN:-/run/mu300}
# The initramfs says which slot Linux runs from (Android may be on either). The blocks it publishes are the pair of
# that slot; an initramfs from before that publishes only the old names, and only ever for Linux on b.
LINUX_SLOT=$(cat "$RUN/linux-slot" 2>/dev/null || echo b)
case $LINUX_SLOT in a) BYTE=12 ;; b) BYTE=14 ;; *) echo "unknown Linux slot '$LINUX_SLOT'" >&2; exit 1 ;; esac
BC_A=$RUN/misc-bc-android.bin
BC_B=$RUN/misc-bc-linux-trial.bin
if [ "$LINUX_SLOT" = b ]; then
    [ -s "$BC_A" ] || BC_A=$RUN/misc-bc-slot-a.bin
    [ -s "$BC_B" ] || BC_B=$RUN/misc-bc-slot-b-trial.bin
fi
MISC=$(cat "$RUN/misc-dev" 2>/dev/null || readlink -f /dev/block/by-name/misc)
```

`make_bc_b` puts the slot byte at `BYTE`:

```bash
    body="${b:0:$((BYTE * 2))}$(printf '%02x' $(( 0x0f | ($1 << 4) )))${b:$((BYTE * 2 + 2)):$((54 - BYTE * 2))}"
```

`write_bc` first checks `[ -s "$1" ] || { echo "no boot control block $1 (initramfs too old for this slot?)" >&2; exit 1; }`.
Messages "slot b armed" → "slot $LINUX_SLOT armed", "next boot: android (slot a)" → "next boot: android"; the
comment over `make_bc_b` says "byte 12 (slot a) or 14 (slot b)".

- [ ] **Step 4: `mu300-update`** — next to `part_dev`:

```sh
# the slot Linux runs from (the initramfs publishes it; Android may be on either). Without the file: b, as always.
linux_slot() { _s=$(cat "${MU300_SYSROOT:-}/run/mu300/linux-slot" 2>/dev/null); case $_s in a|b) echo "$_s" ;; *) echo b ;; esac; }
```

and in `boot_update` and `rollback_boot` `part_dev boot_b` becomes `part_dev "boot_$(linux_slot)"`; the messages
"no boot_b partition", "boot_b does not hold", "boot_b has no AVB footer" use `boot_$(linux_slot)`.

- [ ] **Step 5: `early-recorder`, `android-vendor-start`, `collect-logs.sh`, `switch.sh`**

`early-recorder`, before `BOOTB=`:

```sh
S=$(cat /run/mu300/linux-slot 2>/dev/null); case $S in a|b) ;; *) S=b ;; esac
```
and `boot_b` → `boot_$S` in both lookups (`readlink` and the `PARTNAME=` grep); the header comment says "the Linux slot's boot partition".

`android-vendor-start`, replacing the `sed 's/..._b/..._a/'` line:

```sh
L=$(cat /run/mu300/linux-slot 2>/dev/null); case $L in a) AS=b ;; *) L=b; AS=a ;; esac
tr -d '\000' < $src | sed "s/androidboot.slot_suffix=_$L/androidboot.slot_suffix=_$AS/" > /run/mu300-cmdline.android
```

`tools/collect-logs.sh`: the persistent log is in the slot Android is not on:

```sh
slot=$(adb shell "su -c 'getprop ro.boot.slot_suffix'" </dev/null | tr -d '\r')
case $slot in _b) LS=a ;; *) LS=b ;; esac
```
and `boot_b` in the `dd` line becomes `boot_$LS`.

`android/magisk/mu300-linux-switch/switch.sh`: take kanoqwq's 35a1c55 version of the slot logic (`git show
35a1c55:android/magisk/mu300-linux-switch/switch.sh`): `by_name=${MU300_BY_NAME:-/dev/block/by-name}`,
`slot=${MU300_SLOT_SUFFIX:-$(getprop ro.boot.slot_suffix)}`, the `case $slot in _a) ... _b) ...` that sets
`android_boot`, `linux_boot`, `linux_slot`, `a_arm`, `b_arm`, the awk `suffix` variable, the identical-image refusal
and the status lines. Add `MU300_BUSYBOX` to the front of the busybox search list. Do not take the Chinese strings
(this module's text is English, as now) nor `--no-reboot` (the installer of Task 8 arms `misc` itself).
`android/magisk/README.md`: "slot b" → "the slot Android is not on" where it describes what is written, and "It
refuses ... when Android is not running from slot a" goes.

- [ ] **Step 6: Run the tests** — `cd tests && python3 -m unittest test_device_scripts test_update test_magisk_switch test_static -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin tools/collect-logs.sh android/magisk tests
git commit -m "next-boot, update, the recorder, the vendor chroot, collect-logs and the switch follow the slot Linux runs from"
```

---

### Task 4: `mu300-update` builds a boot image from Android's own

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-update` (new `part_size`, `bootimg_from_stock`, `kernel_modules_into_systems`; `boot_update` uses the latter)
- Test: `tests/test_update.py` (new class `FromStock`)

**Interfaces:**
- Produces (sourced with `MU300_LIB=1`):
  * `part_size DEV_OR_FILE` → bytes (`blockdev --getsize64`, else the file size).
  * `bootimg_from_stock STOCK KERNEL RAMDISK DIR` → `DIR/new` (header page, kernel, ramdisk, page-aligned), `DIR/vbmeta`, `DIR/newfooter`: exactly what `write_boot DEV PSIZE DIR/new DIR/vbmeta DIR/newfooter` takes. Status 1 with a message on stderr when STOCK is not header v4 with an `AVBf` footer, or the result reaches `PERSIST_LOG`.
  * `kernel_modules_into_systems BUNDLE_DIR` → copies `BUNDLE_DIR/modules/*.ko` into every system under `$DISK` (Ubuntu: `lib/modules/<release>/extra` plus `modules.builtin*` and an empty `modules.order`; OpenWrt: `lib/modules/<release>`), runs `depmod` for Ubuntu unless `MU300_NO_DEPMOD=1`. Status 1 on a bad `kernel.release`.

- [ ] **Step 1: Write the failing tests** — `tests/test_update.py`:

```python
import subprocess
import sys
from test_boot_image import HAVE_LZ4, fake_stock, fake_misc, BOOT
from helpers import TOP


# Update's setUp, device() and up() move into a base class UpdateBase(ShellTest) without tests; Update and FromStock
# both derive from it (deriving from Update would run its tests twice).
@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class FromStock(UpdateBase):
    """bootimg_from_stock gives the bytes boot/build-boot-image.py gives, from the same stock image, kernel and
    ramdisk: the header page of Android's image with the new sizes, loglevel=5 and no signature, its vbmeta, its
    footer with the new sizes."""

    def python_image(self, stock):
        mods = self.tmp / 'mods'; mods.mkdir(exist_ok=True)
        for n in (BOOT / 'module-order.txt').read_text().split():
            (mods / n).write_bytes(b'\x7fELF ' + n.encode())
        for f in ('busybox', 'logdw'):
            (self.tmp / f).write_bytes(b'\x7fELF ' + f.encode())
        fake_misc(self.tmp / 'misc.bin')
        (self.tmp / 'Image').write_bytes(b'\x7fkernel' * 12345)
        out = self.tmp / 'py.img'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--stock-boot', str(stock),
                            '--misc-head', str(self.tmp / 'misc.bin'), '--kernel', str(self.tmp / 'Image'),
                            '--modules', str(mods), '--busybox', str(self.tmp / 'busybox'), '--logdw', str(self.tmp / 'logdw'),
                            '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'), '--out', str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        img = out.read_bytes()
        ksz, rsz = struct.unpack_from('<II', img, 8)
        roff = 4096 + (ksz + 4095) // 4096 * 4096
        (self.tmp / 'ramdisk').write_bytes(img[roff:roff + rsz])
        osz, _, vbs = struct.unpack_from('>QQQ', img, len(img) - 64 + 12)
        return img[:osz + vbs], img[-64:]

    def check(self, **stock_args):
        stock = self.tmp / 'stock.img'
        fake_stock(stock, **stock_args)
        head, footer = self.python_image(stock)
        d = self.tmp / 'out'
        for shell in self.each_shell():
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            r = self.up(shell, f'bootimg_from_stock "{stock}" "{self.tmp}/Image" "{self.tmp}/ramdisk" "{d}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((d / 'new').read_bytes() + (d / 'vbmeta').read_bytes(), head, shell)
            self.assertEqual((d / 'newfooter').read_bytes(), footer, shell)

    def test_same_bytes_as_the_python_builder(self):
        self.check()

    def test_unaligned_vbmeta(self):
        self.check(vbmeta_offset=(1 << 20) + 100)

    def test_refuses_what_is_not_a_v4_image(self):
        bad = self.tmp / 'bad.img'
        bad.write_bytes(bytes(1 << 20))
        (self.tmp / 'k').write_bytes(b'k'); (self.tmp / 'r').write_bytes(b'r')
        for shell in self.each_shell():
            r = self.up(shell, f'bootimg_from_stock "{bad}" "{self.tmp}/k" "{self.tmp}/r" "{self.tmp}"')
            self.assertNotEqual(r.returncode, 0)

    def test_modules_into_every_system(self):
        b = self.tmp / 'bundle'
        (b / 'modules').mkdir(parents=True)
        (b / 'modules' / 'a.ko').write_bytes(b'a')
        (b / 'kernel.release').write_text('6.18.55-mu300\n')
        (b / 'modules.builtin').write_text('kernel/x.ko\n')
        (self.disk / 'openwrt').mkdir()
        for shell in self.each_shell():
            r = self.up(shell, f'kernel_modules_into_systems "{b}"', MU300_NO_DEPMOD=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/extra/a.ko').read_bytes(), b'a')
            self.assertTrue((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/modules.builtin').exists())
            self.assertEqual((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/modules.order').read_bytes(), b'')
            self.assertEqual((self.disk / 'openwrt/lib/modules/6.18.55-mu300/a.ko').read_bytes(), b'a')
        (b / 'kernel.release').write_text('../evil\n')
        for shell in self.each_shell():
            self.assertNotEqual(self.up(shell, f'kernel_modules_into_systems "{b}"', MU300_NO_DEPMOD=1).returncode, 0)
```

(`shutil` imported at the top. `Update.setUp` makes `disk/ubuntu/etc`; `lib/modules` is created by the function.)

- [ ] **Step 2: Run them to make sure they fail**

Run: `cd tests && python3 -m unittest test_update.FromStock -v`
Expected: FAIL (`bootimg_from_stock: not found`).

- [ ] **Step 3: The functions** — in `mu300-update`, after `write_boot`:

```sh
part_size() { blockdev --getsize64 "$1" 2>/dev/null || fsize "$1"; }   # part_size DEV: bytes (a file in tests)

# A boot image for the Linux slot made from Android's own (STOCK, a partition or a file): the Magisk installer has
# no image of ours to start from, only Android's. Its header page with this kernel's and ramdisk's sizes, the
# command line loglevel=5 and no signature; its vbmeta and its AVB footer with the new sizes. The same bytes
# boot/build-boot-image.py makes on a computer (tests/test_update.py compares them). Writes DIR/new, DIR/vbmeta and
# DIR/newfooter, which write_boot takes.
bootimg_from_stock() {  # bootimg_from_stock STOCK KERNEL RAMDISK DIR
    _ps=$(part_size "$1") || return 1
    dd if="$1" of="$4/hdr" bs=4096 count=1 2>/dev/null
    dd if="$1" of="$4/footer" bs=64 skip=$((_ps / 64 - 1)) count=1 2>/dev/null
    [ "$(head -c 8 "$4/hdr")" = ANDROID! ] && [ "$(u32 "$4/hdr" 40)" = 4 ] ||
        { echo "boot: $1 does not hold a boot image header v4" >&2; return 1; }
    [ "$(head -c 4 "$4/footer")" = AVBf ] || { echo "boot: $1 has no AVB footer" >&2; return 1; }
    _vo=$(be64 "$4/footer" 20); _vs=$(be64 "$4/footer" 28)
    # vbmeta need not start on a page: read the pages that hold it and cut it out
    dd if="$1" bs=4096 skip=$((_vo / 4096)) count=$(( (_vo % 4096 + _vs + 4095) / 4096 )) 2>/dev/null |
        tail -c +$((_vo % 4096 + 1)) | head -c "$_vs" > "$4/vbmeta"
    [ "$(fsize "$4/vbmeta")" = "$_vs" ] || { echo "boot: cannot read the vbmeta of $1" >&2; return 1; }
    cp "$4/hdr" "$4/new"
    bytes "$(fsize "$2")" 4 le | poke "$4/new" 8
    bytes "$(fsize "$3")" 4 le | poke "$4/new" 12
    { printf 'loglevel=5'; head -c 1526 /dev/zero; } | poke "$4/new" 44
    bytes 0 4 le | poke "$4/new" 1580
    cat "$2" >> "$4/new"; pad_page "$4/new"
    cat "$3" >> "$4/new"; pad_page "$4/new"
    _os=$(fsize "$4/new")
    [ $((_os + _vs)) -le "$PERSIST_LOG" ] ||
        { echo "boot: the new image ($_os bytes) would reach the persistent log area" >&2; return 1; }
    cp "$4/footer" "$4/newfooter"
    { bytes "$_os" 8 be; bytes "$_os" 8 be; bytes "$_vs" 8 be; } | poke "$4/newfooter" 12
    rm -f "$4/hdr" "$4/footer"
}

# A kernel the systems were not built with (a mainline bundle) brings its modules along: they go into every system
# on the Linux filesystem, next to the ones of the other kernel, which stay for a switch back. Ubuntu keeps them under
# extra/ and indexes them (depmod; MU300_NO_DEPMOD=1 leaves that to its next boot, as the Magisk installer must: the
# depmod Android has is busybox's); OpenWrt keeps them flat and loads by path.
kernel_modules_into_systems() {  # kernel_modules_into_systems BUNDLE_DIR (unpacked)
    _krel=$(cat "$1/kernel.release" 2>/dev/null)
    case $_krel in ''|*/*|.*) echo "boot: bad kernel release '$_krel' in the bundle" >&2; return 1 ;; esac
    for _os in $(installed_systems); do
        case $_os in ubuntu) _d=$DISK/$_os/lib/modules/$_krel/extra ;; *) _d=$DISK/$_os/lib/modules/$_krel ;; esac
        mkdir -p "$_d" && cp "$1"/modules/*.ko "$_d/" || { echo "boot: installing the modules into $_os failed" >&2; return 1; }
        if [ "$_os" = ubuntu ]; then
            for _f in modules.builtin modules.builtin.modinfo; do [ ! -f "$1/$_f" ] || cp "$1/$_f" "$DISK/$_os/lib/modules/$_krel/"; done
            [ -e "$DISK/$_os/lib/modules/$_krel/modules.order" ] || : > "$DISK/$_os/lib/modules/$_krel/modules.order"
            if [ -z "${MU300_NO_DEPMOD:-}" ] && command -v depmod >/dev/null; then depmod -b "$DISK/$_os" "$_krel" 2>/dev/null || true; fi
        fi
    done
    printf 'boot: %s modules for %s installed into: %s\n' "$(ls "$1/modules" | wc -l | tr -d ' ')" "$_krel" "$(installed_systems)"
}
```

- [ ] **Step 4: `boot_update` uses `kernel_modules_into_systems`** — in `boot_update`, the block from `krel=$(cat "$tmp/kernel.release")` to the `printf 'boot: %s modules ...'` line becomes `kernel_modules_into_systems "$tmp" || return 1` (the `untar` of `./kernel.release ./modules` and of `modules.builtin*` above it stays). The comment above the block moves to the function (done in Step 3).

- [ ] **Step 5: Run the tests** — `cd tests && python3 -m unittest test_update -v` → all PASS (the existing tests too: `boot_update`'s behaviour did not change).

- [ ] **Step 6: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin/mu300-update tests/test_update.py
git commit -m "mu300-update: a boot image from Android's own header and AVB data, and the kernel modules step as a function of its own"
```

---

### Task 5: What only the device can make — `tools/android-boot-image.sh`

**Files:**
- Create: `tools/android-boot-image.sh`, `android-vendor/subset-files.txt`, `android-vendor/gpu-files.txt`
- Test: `tests/test_android_boot_image.py` (new)

**Interfaces:**
- Consumes: `mu300-update` sourced first (`bootimg_from_stock`, `fsize`); `MAGISKBOOT` (path of `magiskboot`); `MU300_ANDROID_ROOT` (default `/`) and `MU300_FW_DIRS` (default `/odm/firmware /vendor/firmware /vendor/etc`) for tests.
- Produces (sourced):
  * `crc32_hex HEX`, `bc_block LIVE a|b A_META B_META`, `bc_valid LIVE`, `bc_files LIVE DIR` (writes the four files of Task 1 with the same bytes),
  * `collect_firmware DIR` (prints the missing names and fails), `collect_subset LIST DIR`, `collect_gpu LIST DIR` (all or nothing),
  * `vendor_overlay OS FWDIR SUBSET GPUDIR_OR_EMPTY OUT.tar.gz` (the layout of `tools/vendor-overlay.py` without kernel modules),
  * `device_segment DIR OUT.lz4`, `linux_boot_image STOCK BUNDLE_DIR DEVSEG.lz4 OUTDIR` (→ `OUTDIR/new`, `vbmeta`, `newfooter`; ramdisk = device segment then the bundle's `ramdisk-generic.lz4`).

- [ ] **Step 1: The file lists** — `android-vendor/subset-files.txt` holds, one per line, exactly the paths of the second `tar -cf -` in `android-vendor/extract-subset.sh` (relative to `/`, in that order), with a header comment:

```
# The Android files modem_control needs, relative to /, as android-vendor/extract-subset.sh takes them over adb.
# The Magisk installer copies them from the device it runs on (tools/android-boot-image.sh); tests check that both
# lists are the same.
apex/com.android.runtime/bin/linker64
apex/com.android.runtime/lib64/bionic
system/lib64/libcutils.so
...
vendor/etc/ueventd.rc
dev/__properties__
```

`android-vendor/gpu-files.txt`: the GPU closure as `extract-gpu-subset.sh` pulls it on the F50 test board (38 paths:
the 24 under `apex/com.android.vndk.v33/lib64/`, the 11 under `system/lib64/`, `vendor/lib64/egl/libGLES_mali.so`,
`vendor/lib64/hw/vulkan.ums9620.so`, `vendor/lib64/libOpenCL.so`; read them with
`find work/android-gpu-subset \( -type f -o -type l \) | sed 's|^work/android-gpu-subset/||' | sort` on a machine
that has run the installer, or from the spec). File names only: nothing proprietary.

- [ ] **Step 2: Write the failing tests** — `tests/test_android_boot_image.py`:

```python
"""tools/android-boot-image.sh: the parts of the Linux boot image only the device can make - its misc blocks, its
Android files, the device ramdisk segment - checked against boot/build-boot-image.py on the same inputs."""
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import unittest

from helpers import BIN, TOP, ShellTest
from test_boot_image import HAVE_LZ4, LIVE_A, cpio_files, fake_misc, fake_stock, unlz4_legacy

LIB = TOP / 'tools' / 'android-boot-image.sh'
spec = importlib.util.spec_from_file_location('bbi', TOP / 'boot' / 'build-boot-image.py')
bbi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bbi)

# magiskboot compress=lz4_legacy IN OUT and decompress IN OUT, done by the builder's own lz4_legacy and the tests'
# unlz4_legacy (the lz4 command or module)
MAGISKBOOT = f'''#!/bin/sh
case $1 in
    compress=lz4_legacy) f='m.lz4_legacy' ;;
    decompress) f='unlz4_legacy' ;;
    *) exit 1 ;;
esac
exec "{sys.executable}" -c "import importlib.util,sys; sys.path.insert(0,'{TOP}/tests'); from test_boot_image import unlz4_legacy; s=importlib.util.spec_from_file_location('b','{TOP}/boot/build-boot-image.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); open(sys.argv[2],'wb').write($f(open(sys.argv[1],'rb').read()))" "$2" "$3"
'''


def cpio_all(data):
    """every newc archive of a byte string (as the kernel reads concatenated segments), later files win"""
    files, i = {}, 0
    while i < len(data):
        if data[i:i + 6] != b'070701':
            i += 1
            continue
        part = cpio_files(data[i:])
        files.update(part)
        i = data.index(b'TRAILER!!!', i) + 10
    return files


def subset_paths():
    return [l for l in (TOP / 'android-vendor' / 'subset-files.txt').read_text().split() if not l.startswith('#')]


def fake_android_root(root):
    """a / with every file of the subset list (dirs get one file inside) and the firmware"""
    for p in subset_paths():
        f = root / p
        if p.endswith(('bionic', '__properties__')):
            f.mkdir(parents=True, exist_ok=True)
            (f / 'u:object_r:x:s0' if '__properties__' in p else f / 'libc.so').write_bytes(b'data ' + p.encode())
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b'data ' + p.encode())
    os.chmod(root / 'vendor/bin/modem_control', 0o755)
    fw = root / 'vendor/firmware'
    fw.mkdir(parents=True, exist_ok=True)
    for n in ('wcnmodem.bin', 'gnssmodem.bin', 'wifi_board_config.ini', 'wifi_board_config_ab.ini',
              'bt_configure_pskey.ini', 'bt_configure_rf.ini'):
        (fw / n).write_bytes(b'fw ' + n.encode())


class Lists(unittest.TestCase):
    def test_subset_list_is_extract_subsets(self):
        sh = (TOP / 'android-vendor' / 'extract-subset.sh').read_text()
        m = re.search(r'tar -cf - (apex/.*?) \| tar -xf -', sh, re.S)
        self.assertEqual(m.group(1).replace('\\\n', ' ').split(), subset_paths())


class DeviceSide(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('magiskboot', MAGISKBOOT)

    def lib(self, shell, code, **env):
        pre = f'MU300_LIB=1 . "{BIN}/mu300-update"; . "{LIB}"; '
        return self.sh(shell, pre + code, MAGISKBOOT=self.stubs / 'magiskboot', **env)

    def test_blocks_equal_the_python_builders(self):
        want = dict(zip(('slot-a', 'slot-b-trial', 'slot-b', 'slot-a-trial'),
                        bbi.bootloader_control(bytes(0x800) + LIVE_A)))
        for shell in self.each_shell():
            d = self.tmp / 'etc'
            shutil.rmtree(d, ignore_errors=True); d.mkdir()
            r = self.lib(shell, f'bc_valid {LIVE_A.hex()} && bc_files {LIVE_A.hex()} "{d}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            for n, data in want.items():
                self.assertEqual((d / f'misc-bc-{n}.bin').read_bytes(), data, (shell, n))

    def test_invalid_live_block(self):
        bad = bytearray(LIVE_A); bad[20] ^= 1
        for shell in self.each_shell():
            self.assertNotEqual(self.lib(shell, f'bc_valid {bytes(bad).hex()}').returncode, 0)
            self.assertNotEqual(self.lib(shell, 'bc_valid 00').returncode, 0)

    def test_subset_and_firmware_from_the_device(self):
        root = self.tmp / 'root'
        fake_android_root(root)
        for shell in self.each_shell():
            s, fw = self.tmp / 'subset', self.tmp / 'fw'
            r = self.lib(shell, f'collect_subset "{TOP}/android-vendor/subset-files.txt" "{s}" && collect_firmware "{fw}"',
                         MU300_ANDROID_ROOT=root, MU300_FW_DIRS=f'{root}/odm/firmware {root}/vendor/firmware')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((s / 'vendor/bin/modem_control').is_file())
            self.assertEqual(os.readlink(s / 'system/bin/linker64'), '/apex/com.android.runtime/bin/linker64')
            self.assertEqual((s / 'linkerconfig/ld.config.txt').read_bytes(), b'')
            self.assertEqual(sorted(os.listdir(fw)), sorted(os.listdir(root / 'vendor/firmware')))
            shutil.rmtree(fw)
            (root / 'vendor/firmware/gnssmodem.bin').rename(root / 'gnss.bak')
            r = self.lib(shell, f'collect_firmware "{fw}"', MU300_FW_DIRS=f'{root}/vendor/firmware')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('gnssmodem.bin', r.stdout)
            (root / 'gnss.bak').rename(root / 'vendor/firmware/gnssmodem.bin')
            shutil.rmtree(s); shutil.rmtree(fw, ignore_errors=True)

    def test_vendor_overlay_layout(self):
        # the layout of tools/vendor-overlay.py: firmware, the subset with its property area moved out of dev/
        root = self.tmp / 'root'
        fake_android_root(root)
        for shell in self.each_shell():
            for os_name, fwdir in (('ubuntu', 'usr/lib/firmware'), ('openwrt', 'lib/firmware')):
                out = self.tmp / f'v-{os_name}.tar.gz'
                r = self.lib(shell, f'collect_subset "{TOP}/android-vendor/subset-files.txt" "{self.tmp}/s" && '
                                    f'collect_firmware "{self.tmp}/fw" && vendor_overlay {os_name} "{self.tmp}/fw" "{self.tmp}/s" "" "{out}"',
                             MU300_ANDROID_ROOT=root, MU300_FW_DIRS=f'{root}/vendor/firmware')
                self.assertEqual(r.returncode, 0, r.stderr)
                names = subprocess.run(['tar', '-tzf', str(out)], capture_output=True, text=True).stdout.split()
                names = [n.lstrip('./') for n in names]
                self.assertIn(f'{fwdir}/wcnmodem.bin', names)
                self.assertIn('opt/mu300/android/vendor/bin/modem_control', names)
                self.assertTrue(any(n.startswith('opt/mu300/android/dev-properties/') for n in names))
                self.assertFalse([n for n in names if n.startswith('opt/mu300/android/dev/')])
                # no entry for a directory the system already has: Ubuntu's lib is a link to usr/lib
                self.assertFalse([n for n in names if n.rstrip('/') in ('lib', 'usr', 'usr/lib', 'opt', 'opt/mu300')])
                shutil.rmtree(self.tmp / 's'); shutil.rmtree(self.tmp / 'fw')

    @unittest.skipIf(not HAVE_LZ4, 'no lz4')
    def test_whole_image_holds_what_the_python_builder_puts_in(self):
        ...  # see Step 3
```

`test_whole_image_holds_what_the_python_builder_puts_in` builds, from one set of inputs (the fake root above, a
`fake_stock`, `fake_misc`, fake modules as in `test_boot_image.GenericRamdisk`):
1. with Python: a generic segment (`--generic-ramdisk`) into `bundle/ramdisk-generic.lz4`, `bundle/Image`; then
   `build-boot-image.py --stock-boot --misc-head --kernel bundle/Image --append-ramdisk bundle/ramdisk-generic.lz4
   --device f50 --linux-slot b --android-subset <collected subset> ...`;
2. with the library: `etc/mu300-device` (`f50`), `etc/mu300-linux-slot` (`b`), `bc_files`, the collected subset as
   `android/`, `device_segment`, `linux_boot_image`;
and asserts that `cpio_all(unlz4_legacy(ramdisk))` of both images agree on every regular file and symlink (names,
contents, the executable bit), ignoring directory entries, and that the header page, the vbmeta and the footer
fields (`original_image_size`, `vbmeta_size`) are consistent with `bootimg_from_stock`'s output.

- [ ] **Step 3: Run them to make sure they fail**

Run: `cd tests && python3 -m unittest test_android_boot_image -v`
Expected: FAIL (`android-boot-image.sh` missing; `subset-files.txt` missing).

- [ ] **Step 4: Write `tools/android-boot-image.sh`**

```sh
# Device side of the Magisk installer: what only the device can make for its Linux boot image and its systems - the
# misc blocks, the Android files modem_control needs, the firmware, the device ramdisk segment - and the image
# itself, built from Android's own boot image the way mu300-update rebuilds ours. Sourced after
# rootfs/overlay/opt/mu300/bin/mu300-update (MU300_LIB=1), whose bootimg_from_stock and byte helpers it uses.
# Writes only into the directories it is given. The files it collects are proprietary: they stay on this device.
LZ4_MAGIC=02214c18
FIRMWARE='wcnmodem.bin gnssmodem.bin wifi_board_config.ini wifi_board_config_ab.ini bt_configure_pskey.ini bt_configure_rf.ini'

hex_to_bytes() {  # hex_to_bytes HEX: the bytes (octal escapes: dash's printf has no \x)
    _h=$1
    while [ -n "$_h" ]; do
        _p=${_h%"${_h#??}"}; _h=${_h#??}
        printf "\\$(printf %o "0x$_p")"
    done
}
# CRC-32 (zlib) of the bytes of HEX, little endian as the block stores it: gzip ends every stream with exactly that
# value, which saves an implementation (mu300-next-boot does the same)
crc32_hex() { hex_to_bytes "$1" | gzip -c | tail -c 8 | head -c 4 | od -An -tx1 -v | tr -d ' \n'; }

# bc_block LIVE a|b A_META B_META: the live bootloader_control (64 hex digits) with that slot suffix, slot a's and
# slot b's first byte (priority | tries << 4 | successful << 7) and a new CRC - boot/build-boot-image.py's with_slots
bc_block() {
    case $2 in a) _s=5f610000 ;; b) _s=5f620000 ;; *) return 1 ;; esac
    _b="$_s$(printf %s "$1" | cut -c9-24)$3$(printf %s "$1" | cut -c27-28)$4$(printf %s "$1" | cut -c31-56)"
    printf '%s%s' "$_b" "$(crc32_hex "$_b")"
}
bc_valid() {  # bc_valid LIVE: 64 hex digits, magic BCAB, a CRC that matches
    [ ${#1} = 64 ] && [ "$(printf %s "$1" | cut -c9-16)" = 42434142 ] &&
        [ "$(crc32_hex "$(printf %s "$1" | cut -c1-56)")" = "$(printf %s "$1" | cut -c57-64)" ]
}
bc_files() {  # bc_files LIVE DIR: the four blocks of Task 1, byte for byte
    _l=$1 _d=$2
    for _x in 'slot-a a 9f 1e' 'slot-b-trial b 9e 2f' 'slot-b b 1e 9f' 'slot-a-trial a 2f 9e'; do
        set -- $_x
        hex_to_bytes "$(bc_block "$_l" "$2" "$3" "$4")" > "$_d/misc-bc-$1.bin" || return 1
        [ "$(fsize "$_d/misc-bc-$1.bin")" = 32 ] || return 1
    done
}

collect_firmware() {  # collect_firmware DIR: the Wi-Fi/Bluetooth/GNSS firmware of this device; prints what is missing
    mkdir -p "$1"; _miss=
    for _f in $FIRMWARE; do
        _ok=
        for _d in ${MU300_FW_DIRS:-/odm/firmware /vendor/firmware /vendor/etc}; do
            if [ -s "$_d/$_f" ] && cp "$_d/$_f" "$1/$_f"; then _ok=1; break; fi
        done
        [ -n "$_ok" ] || _miss="$_miss $_f"
    done
    [ -z "$_miss" ] || { echo "$_miss"; return 1; }
}
# collect_subset LIST DIR: the files of LIST (android-vendor/subset-files.txt) from this device's /, links followed
# as extract-subset.sh follows them over adb, and the two things it adds. A file this firmware lacks is skipped; the
# two the modem cannot start without are checked.
collect_subset() {
    rm -rf "$2"; mkdir -p "$2"
    (cd "${MU300_ANDROID_ROOT:-/}" && tar -chf - $(grep -v '^#' "$1") 2>/dev/null) | tar -xf - -C "$2" 2>/dev/null
    mkdir -p "$2/system/bin" "$2/linkerconfig"
    ln -sfn /apex/com.android.runtime/bin/linker64 "$2/system/bin/linker64"
    : > "$2/linkerconfig/ld.config.txt"
    [ -s "$2/vendor/bin/modem_control" ] && [ -d "$2/dev/__properties__" ]
}
# collect_gpu LIST DIR: the Mali userspace and its library closure, all of it or nothing (a partial closure only
# fails later, inside OpenCL)
collect_gpu() {
    rm -rf "$2"; mkdir -p "$2"
    for _p in $(grep -v '^#' "$1"); do
        [ -e "${MU300_ANDROID_ROOT:-}/$_p" ] || { rm -rf "$2"; return 1; }
    done
    (cd "${MU300_ANDROID_ROOT:-/}" && tar -chf - $(grep -v '^#' "$1")) | tar -xf - -C "$2" || { rm -rf "$2"; return 1; }
}
# vendor_overlay OS FW SUBSET GPU OUT: what tools/vendor-overlay.py makes on a computer, for android-install.sh to
# unpack over the system: the firmware, the subset under opt/mu300/android with its property area moved out of
# dev/ (a devtmpfs is mounted there), the GPU files where the subset has none. Only leaf directories are named, so
# no entry replaces a directory the system has (Ubuntu's lib is a link to usr/lib).
vendor_overlay() {
    _o=$5.d; rm -rf "$_o"
    case $1 in ubuntu) _fw=usr/lib/firmware ;; openwrt) _fw=lib/firmware ;; *) return 1 ;; esac
    mkdir -p "$_o/$_fw" "$_o/opt/mu300/android" || return 1
    cp "$2"/* "$_o/$_fw/" && cp -a "$3"/. "$_o/opt/mu300/android/" || return 1
    mv "$_o/opt/mu300/android/dev/__properties__" "$_o/opt/mu300/android/dev-properties" &&
        rm -rf "$_o/opt/mu300/android/dev" || return 1
    if [ -n "$4" ]; then
        (cd "$4" && find . ! -type d | sed 's|^\./||') | while read -r _f; do
            [ -e "$_o/opt/mu300/android/$_f" ] || [ -L "$_o/opt/mu300/android/$_f" ] && continue
            mkdir -p "$_o/opt/mu300/android/$(dirname "$_f")" && cp -a "$4/$_f" "$_o/opt/mu300/android/$_f"
        done
    fi
    tar -czf "$5" -C "$_o" "$_fw" opt/mu300/android && rm -rf "$_o"
}

# device_segment DIR OUT: DIR as a newc cpio owned by root, compressed to LZ4 legacy by magiskboot - the format the
# 5.4 kernel takes for every segment (gzip makes it panic) and that no shell tool on the device writes
device_segment() {
    (cd "$1" && find . -mindepth 1 | sed 's|^\./||' | LC_ALL=C sort | cpio -o -H newc -R 0:0 2>/dev/null) > "$2.cpio" || return 1
    "${MAGISKBOOT:-magiskboot}" compress=lz4_legacy "$2.cpio" "$2" >/dev/null 2>&1 || return 1
    rm -f "$2.cpio"
    [ "$(od -An -tx1 -N4 "$2" | tr -d ' \n')" = "$LZ4_MAGIC" ]
}
# linux_boot_image STOCK BUNDLE DEVSEG OUT: the device segment first, then the bundle's generic one (init, busybox,
# modules); nothing in a generic segment has the name of a device-segment file, and mu300-update wants an LZ4 frame
# first. Then the header and AVB data of Android's own image around it.
linux_boot_image() {
    [ "$(od -An -tx1 -N4 "$2/ramdisk-generic.lz4" | tr -d ' \n')" = "$LZ4_MAGIC" ] || return 1
    cat "$3" "$2/ramdisk-generic.lz4" > "$4/ramdisk" || return 1
    bootimg_from_stock "$1" "$2/Image" "$4/ramdisk" "$4"
}
```

Finish `test_whole_image_holds_what_the_python_builder_puts_in` as described in Step 2.

- [ ] **Step 5: Run the tests** — `cd tests && python3 -m unittest test_android_boot_image -v` → PASS under every shell. (`cpio` on macOS is bsdcpio; it takes `-o -H newc -R 0:0` too.)

- [ ] **Step 6: Commit**

```bash
git add tools/android-boot-image.sh android-vendor/subset-files.txt android-vendor/gpu-files.txt tests/test_android_boot_image.py
git commit -m "android-boot-image: the misc blocks, the Android files and the device ramdisk segment, made on the device itself"
```

---

### Task 6: The free eMMC region as functions of `storage.sh`

**Files:**
- Modify: `tools/storage.sh` (new `region_probe`, `region_find_existing`, `region_dirty`), `install.sh` (uses them)
- Test: `tests/test_installer.py` (new class `Region`)

**Interfaces:**
- Produces: `region_probe` → `last_end disk` (sectors), `OFF SIZE` (bytes), status 1 when the device gave no answer; `region_find_existing` → `existing=yes|no`, and `OFF SIZE` of an existing `mu300root` (also at the old fixed offset 27762098176); `region_dirty` → `DIRTY` (0-16 samples with data). Same `su_do` contract as the other functions: a command string run on the device, its output back. Task 7 calls them with an `su_do` that runs on the device itself.

- [ ] **Step 1: Write the failing test** — `tests/test_installer.py`:

```python
class Region(ShellTest):
    """storage.sh's region functions, with su_do running the device commands against a fake sysfs and eMMC file."""

    def fake(self, ext4_at=None):
        sysfs = self.tmp / 'sys/block/mmcblk0'
        (sysfs / 'mmcblk0p1').mkdir(parents=True, exist_ok=True)
        (sysfs / 'mmcblk0p1/start').write_text('2048\n')
        (sysfs / 'mmcblk0p1/size').write_text(f'{4 << 21}\n')          # ends at 8 GiB + 1 MiB
        (sysfs / 'size').write_text(f'{16 << 21}\n')                   # a 16 GiB eMMC
        emmc = self.tmp / 'mmcblk0'
        with open(emmc, 'wb') as f:
            f.truncate(16 << 30)
            if ext4_at is not None:
                f.seek(ext4_at + 1024 + 4); f.write(struct.pack('<I', 1000))
                f.seek(ext4_at + 1080); f.write(b'\x53\xef')
                f.seek(ext4_at + 1144); f.write(b'mu300root')
        return emmc

    def run_region(self, shell, call):
        su = (f'su_do() {{ sh -c "$(printf "%s" "$1" | sed -e "s|/sys/block|{self.tmp}/sys/block|g" '
              f'-e "s|/dev/block/mmcblk0|{self.tmp}/mmcblk0|g")"; }}\n')
        return self.sh(shell, su + f'. "{TOP}/tools/storage.sh"\n{call}\necho "rc=$? OFF=$OFF SIZE=$SIZE existing=${{existing:-}} DIRTY=${{DIRTY:-}}"')

    def test_probe(self):
        self.fake()
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe').stdout
            start = ((2048 + (4 << 21)) // 4096 + 1) * 4096
            end = (((16 << 21) - 34) // 4096 - 1) * 4096
            self.assertIn(f'rc=0 OFF={start * 512} SIZE={(end - start) * 512}', out)

    def test_existing_and_dirty(self):
        start = ((2048 + (4 << 21)) // 4096 + 1) * 4096 * 512
        emmc = self.fake(ext4_at=start)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing').stdout
            self.assertIn(f'OFF={start} SIZE={1000 * 4096} existing=yes', out)
        self.fake()
        with open(emmc, 'r+b') as f:
            f.seek(start + (64 << 20)); f.write(b'data' * 1024)
        for shell in self.each_shell():
            out = self.run_region(shell, 'region_probe; region_find_existing; region_dirty').stdout
            self.assertIn('existing=no DIRTY=1', out)
```

(The 16 GiB file is sparse; `struct` imported at the top.)

- [ ] **Step 2: Run it to make sure it fails** — `cd tests && python3 -m unittest test_installer.Region -v` → FAIL (`region_probe: not found`).

- [ ] **Step 3: Move the code** — into `tools/storage.sh` (and its header list of functions):

```sh
# The free eMMC region: from the first 2 MiB boundary after the last partition to the last one before the backup
# GPT. Byte counts are computed here, never in the device's shell (Android's mksh has 32-bit arithmetic).
region_probe() {
    set -- $(su_do 'e=0; for p in /sys/block/mmcblk0/mmcblk0p*; do x=$(( $(cat $p/start) + $(cat $p/size) )); [ $x -gt $e ] && e=$x; done; echo $e $(cat /sys/block/mmcblk0/size)')
    [ $# -eq 2 ] || return 1
    last_end=$1; disk=$2
    start=$(( (last_end / 4096 + 1) * 4096 ))
    end=$(( ((disk - 34) / 4096 - 1) * 4096 ))
    OFF=$((start * 512)); SIZE=$(( (end - start) * 512 ))
}
# an existing installation defines the region (it may have been created with a slightly different size, or at the
# fixed offset of the first releases)
region_find_existing() {
    existing=no
    for cand in $OFF 27762098176; do
        m=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1080)) count=2 2>/dev/null | od -An -tx1" | tr -d ' ')
        l=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1144)) count=16 2>/dev/null" | LC_ALL=C tr -d '\000')
        if [ "$m" = 53ef ] && [ "$l" = mu300root ]; then
            blocks=$(su_do "dd if=/dev/block/mmcblk0 bs=1 skip=$((cand + 1028)) count=4 2>/dev/null | od -An -tu4" | tr -d ' ')
            OFF=$cand; SIZE=$((blocks * 4096)); existing=yes; return 0
        fi
    done
}
# unpartitioned space should be unused: 16 samples of 1 MiB across the region, counting those with data. Empty is
# 0x00 or 0xFF (an eMMC reads back what its erase leaves, and on some F50s that is 0xFF).
region_dirty() {
    step=$(( SIZE / 1048576 / 16 ))
    probe=""; i=0
    while [ $i -lt 16 ]; do probe="$probe $(( OFF / 1048576 + i * step ))"; i=$((i + 1)); done
    DIRTY=$(su_do "n=0; for s in $probe; do c=\$(dd if=/dev/block/mmcblk0 bs=1048576 skip=\$s count=1 2>/dev/null | tr -d \"\\000\\377\" | wc -c); [ \$c -gt 0 ] && n=\$((n + 1)); done; echo \$n")
}
```

and in `install.sh` replace lines 241-246 (`set -- $(su_do 'e=0; ...` to `OFF=...; SIZE=...`) with
`region_probe || die "$(t 'could not read the partition table from the device (is su granted? try again)')"`, the
`for cand in ...` loop (281-288) with `region_find_existing`, and the sampling (301-305, the `step=` to
`DIRTY=$(su_do ...)` lines) with `region_dirty` followed by the unchanged `echo "$(t 'data check: ...')"`. The
messages stay where they are; check with `python3 tools/check-i18n.py` that no key changed.

- [ ] **Step 4: Run the tests** — `cd tests && python3 -m unittest test_installer -v && python3 ../tools/check-i18n.py` → PASS, nothing missing.

- [ ] **Step 5: Commit**

```bash
git add tools/storage.sh install.sh tests/test_installer.py
git commit -m "storage.sh: the free eMMC region, an existing installation in it and the data check as functions, for the Magisk installer too"
```

---

### Task 7: The Magisk installer, part 1 — what is on the device and what to do

**Files:**
- Create: `android/magisk/installer/mu300-install.sh`
- Create: `tests/fakedevice.py` (the fake device shared by Tasks 7, 8, 10)
- Test: `tests/test_magisk_installer.py` (new)

**Interfaces:**
- Consumes: `tools/storage.sh` (`sd_probe`, `sd_existing`, Task 6's region functions), `tools/i18n.sh` (`t`), the zip's `mu300/manifest`.
- Produces, with `MU300_LIB=1` (functions only):
  * `android_lang`, `conf_find`, `conf_load FILE`, `conf_check` — sets the `MU300_*` answers,
  * `detect_device` → `DEVICE`; `slot_setup` → `ANDROID_SLOT LINUX_SLOT BOOT_ANDROID BOOT_LINUX MISC LIVE_BC`,
  * `manifest_load FILE` → `TAG SYSTEM OS UBUNTU KERNEL KERNEL_ASSET ROOTFS_ASSET SHA256_KERNEL SHA256_ROOTFS`,
  * `payload_unpack` → `$T/mu300-$OS.tar.gz`, `$W/kernel/` (unpacked bundle) `KB=$W/kernel`,
  * `plan_storage`, `inspect_target`, `plan_choices` → the variables `install.sh` writes into `mu300-install.env` (`SD_MODE SD_DEV OFF SIZE INT_OFF INT_SIZE INTERNAL_EXISTS FORMAT UPDATE WIPE_LEGACY OSES BOOT_OS DEFAULT_LINUX BOOT_ATTEMPTS IMPORT_HOTSPOT KERNEL GPU`),
  * `plan_print`, `write_example`.
- Exit codes of the script: 0 installed, 3 dry run, 1 refused or failed.
- Test hooks (all default to the real device): `MU300_DIR`, `MU300_TMP`, `MU300_BY_NAME`, `MU300_SDCARD`, `MU300_DEVICE_SH` (the shell `su_do` uses), `MU300_ANDROID_SH`, `MU300_ANDROID_PATH`, `MAGISKBIN`, `MAGISKBOOT`, `MU300_UBB`.

- [ ] **Step 1: The fake device** — `tests/fakedevice.py`:

```python
"""A fake F50 for the Magisk installer: block devices are files, sysfs a directory tree, Android's tools stubs.
The device shell (MU300_DEVICE_SH) rewrites the absolute device paths in the commands storage.sh sends, the way
tests/test_installer.py's Region does; android-install.sh and the mount helper are replaced by MU300_ANDROID_SH,
which records what it was given and acts on a directory standing in for the Linux filesystem."""
import os
import shutil
import struct
import sys
from pathlib import Path

from helpers import TOP
from test_boot_image import LIVE_A, fake_misc, fake_stock, with_slots

ANDROID_SH = r'''#!/bin/sh
# stand-in for /system/bin/sh running android-install.sh or android-mount-mu300root.sh
log=$FAKE/android-sh.log
echo "ASH_STANDALONE=${ASH_STANDALONE:-} PATH=$PATH $*" >> "$log"
case $1 in
    -c) shift; exec "$FAKE/devsh" -c "$@" ;;
    */android-install.sh)
        cp "$MU300_TMP/mu300-install.env" "$FAKE/install.env"
        [ "${FAKE_INSTALL_FAILS:-0}" = 1 ] && { echo "[device] failing as asked"; exit 1; }
        . "$MU300_TMP/mu300-install.env"
        for os in $OSES; do mkdir -p "$FAKE/fs/$os/etc"; done
        echo MU300-INSTALL-OK ;;
    */android-mount-mu300root.sh)
        if [ "$2" = -u ]; then rm -f "$3"; echo UNMOUNTED; else ln -s "$FAKE/fs" "$2"; echo "MOUNTED fake on $2"; fi ;;
esac
'''
DEVSH = r'''#!/bin/sh
# the device's shell for su_do: absolute device paths point into the fake device
[ "$1" = -c ] && shift
exec sh -c "$(printf '%s' "$1" | sed -e "s|/sys/|$FAKE/sys/|g" -e "s|/dev/block/|$FAKE/dev/block/|g")"
'''
GETPROP = r'''#!/bin/sh
sed -n "s/^$1=//p" "$FAKE/props"
'''
SM = r'''#!/bin/sh
case $1 in list-volumes) cat "$FAKE/volumes" ;; unmount) : ;; esac
'''
UBB = r'''#!/bin/sh
# the bundled busybox: only mkpasswd is used
[ "$1" = mkpasswd ] || exit 1
while [ $# -gt 0 ]; do case $1 in -S) salt=$2; shift ;; esac; shift; done
read -r pw
printf '$6$%s$%s\n' "$salt" "$(printf '%s' "$pw" | od -An -tx1 | tr -d ' \n' | cut -c1-20)"
'''


class FakeDevice:
    def __init__(self, tmp, stubs, model='ZTE MU300', slot='_a', emmc_gib=16, region=True, card=None):
        self.root = tmp / 'fake'
        r = self.root
        for d in ('dev/block/by-name', 'sys/block/mmcblk0/mmcblk0p1', 'sdcard', 'tmp', 'fs', 'magisk', 'android'):
            (r / d).mkdir(parents=True, exist_ok=True)
        by = r / 'dev/block/by-name'
        fake_stock(by / 'boot_a')
        shutil.copy(by / 'boot_a', by / 'boot_b')
        # what misc holds while Android runs from that slot (a valid block: magic and CRC)
        fake_misc(by / 'misc', LIVE_A if slot == '_a' else with_slots(LIVE_A, b'_b\0\0', 0x1e, 0x9f))
        (r / 'dev/block/mmcblk0').write_bytes(b'')
        # partitions end at 8 GiB; a 16 GiB eMMC leaves a ~8 GiB region, a 9 GiB one too little
        sysb = r / 'sys/block/mmcblk0'
        (sysb / 'mmcblk0p1/start').write_text('2048\n')
        (sysb / 'mmcblk0p1/size').write_text(f'{(8 << 21) if region else (emmc_gib << 21) - 4096}\n')
        (sysb / 'size').write_text(f'{emmc_gib << 21}\n')
        if card is not None:
            c = r / 'sys/block/mmcblk1'
            (c / 'device').mkdir(parents=True)
            (c / 'device/type').write_text('SD\n')
            (c / 'size').write_text(f'{32 << 21}\n')
            (r / 'dev/block/mmcblk1').write_bytes(card)
        (r / 'props').write_text(f'ro.product.model={model}\nro.product.device=mu300\nro.boot.slot_suffix={slot}\n'
                                 'persist.sys.locale=en-US\n')
        (r / 'volumes').write_text('private mounted null\n')
        for name, body in (('android-sh', ANDROID_SH), ('devsh', DEVSH), ('ubb', UBB)):
            (r / name).write_text(body); (r / name).chmod(0o755)
        for name, body in (('getprop', GETPROP), ('sm', SM)):
            (stubs / name).write_text(body); (stubs / name).chmod(0o755)

    def env(self, mu300_dir, magiskboot):
        r = self.root
        return {'FAKE': r, 'MU300_DIR': mu300_dir, 'MU300_TMP': r / 'tmp', 'MU300_BY_NAME': r / 'dev/block/by-name',
                'MU300_SDCARD': r / 'sdcard', 'MU300_DEVICE_SH': r / 'devsh', 'MU300_ANDROID_SH': r / 'android-sh',
                'MU300_ANDROID_PATH': os.environ['PATH'], 'MU300_ANDROID_ROOT': r / 'android',
                'MU300_FW_DIRS': f'{r}/android/vendor/firmware', 'MAGISKBIN': r / 'magisk',
                'MAGISKBOOT': magiskboot, 'MU300_UBB': r / 'ubb', 'MU300_SKIP_ROOT_CHECK': 1,
                # as under Magisk: what the installer starts on Android's side must not inherit it
                'ASH_STANDALONE': 1}
```

`tests/test_magisk_installer.py` builds a fake zip directory (`mu300/` laid out as Task 10 lays it out: copy the
scripts, `i18n/`, the two lists, a `manifest` and a `payload/` with a tiny kernel bundle tarball - `Image`,
`ramdisk-generic.lz4` built with `build-boot-image.py --generic-ramdisk`, `modules/`, `kernel.release`, `devices`
(`f50 u30air`), `features` (`sdcard`) - and a tiny rootfs tarball), a fake Android `/` with `fake_android_root`
from Task 5, a `magisk/busybox` that is a symlink to the host's `busybox` (skip the class without it), and runs
`busybox sh mu300/install.sh` with `FakeDevice.env(...)`. The zip itself is a real zip (`zipfile`) holding
`payload/*` so `payload_unpack` runs `unzip -p` for real.

- [ ] **Step 2: Write the failing tests** — `tests/test_magisk_installer.py`, first part:

```python
class Conf(ShellTest):
    def load(self, shell, text):
        f = self.tmp / 'mu300-install.conf'
        f.write_bytes(text.encode())
        return self.sh(shell, f'MU300_LIB=1 MU300_DIR="{INSTALLER_DIR}" . "{INSTALLER}"; conf_load "{f}"; '
                              'echo "S=$MU300_STORAGE E=$MU300_SD_ERASE B=$MU300_BOOT P=$MU300_PASSWORD"')

    def test_values_quotes_crlf_comments(self):
        for shell in self.each_shell():
            r = self.load(shell, '# a comment\r\nMU300_STORAGE=sd\r\nMU300_SD_ERASE="yes"\n  MU300_BOOT = android\n'
                                 "MU300_PASSWORD='p a$s'\n")
            self.assertIn("S=sd E=yes B=android P=p a$s", r.stdout)

    def test_unknown_keys_are_reported_not_set(self):
        for shell in self.each_shell():
            r = self.load(shell, 'PATH=/nowhere\nMU300_STORAGE=internal\n')
            self.assertIn('S=internal', r.stdout)
            self.assertIn('PATH', r.stdout + r.stderr)   # reported
            self.assertNotEqual(r.returncode, 127)       # PATH was not replaced

    def test_conf_is_never_executed(self):
        for shell in self.each_shell():
            r = self.load(shell, f'MU300_BOOT=$(touch {self.tmp}/ran)\nMU300_STORAGE=`touch {self.tmp}/ran2`\n')
            self.assertFalse((self.tmp / 'ran').exists())
            self.assertFalse((self.tmp / 'ran2').exists())

    def test_check_refuses_bad_values(self):
        for shell in self.each_shell():
            for bad in ('MU300_STORAGE=usb', 'MU300_BOOT_ATTEMPTS=9', 'MU300_PASSWORD=short', 'MU300_MODE=maybe'):
                f = self.tmp / 'c.conf'; f.write_text(bad + '\n')
                r = self.sh(shell, f'MU300_LIB=1 MU300_DIR="{INSTALLER_DIR}" . "{INSTALLER}"; conf_load "{f}"; conf_check')
                self.assertNotEqual(r.returncode, 0, bad)


class Plan(InstallerCase):          # InstallerCase: the fake zip + FakeDevice, run(), in this file
    def test_defaults_internal_region(self):
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('internal', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_card_with_mu300sd_is_the_default(self):
        self.device(card=fake_ext4_bytes('mu300sd'))
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3)
        self.assertIn('/mmcblk1', r.stdout)

    def test_card_is_never_erased_without_consent(self):
        self.device(card=bytes(1 << 20), region=False)
        r = self.run_installer(conf='MU300_STORAGE=sd\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn('MU300_SD_ERASE=yes', r.stdout)
        self.assertTrue(self.nothing_written())

    def test_foreign_ext4_card_is_refused_even_with_consent(self):
        self.device(card=fake_ext4_bytes('photos'))
        r = self.run_installer(conf='MU300_STORAGE=sd\nMU300_SD_ERASE=yes\n')
        self.assertEqual(r.returncode, 1)

    def test_no_room_no_card(self):
        self.device(region=False)
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn('MU300_STORAGE=sd', r.stdout)      # the way out is named
        self.assertTrue(self.nothing_written())

    def test_unknown_model(self):
        self.device(model='Pixel 7')
        self.assertEqual(self.run_installer().returncode, 1)
        self.assertEqual(self.run_installer(conf='MU300_DEVICE=f50\nMU300_DRY_RUN=1\n').returncode, 3)

    def test_android_on_slot_b_puts_linux_on_a(self):
        self.device(slot='_b')
        r = self.run_installer(conf='MU300_DRY_RUN=1\n')
        self.assertEqual(r.returncode, 3)
        self.assertIn('boot_a', r.stdout)

    def test_corrupt_payload(self):
        self.corrupt_payload()
        self.assertEqual(self.run_installer().returncode, 1)
        self.assertTrue(self.nothing_written())

    def test_ubuntu_2604_and_a_54_zip(self):
        self.existing_filesystem(ubuntu='26.04')
        self.zip(kernel='5.4', system='openwrt')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn('26.04', r.stdout)

    def test_example_conf_written(self):
        self.run_installer(conf='MU300_DRY_RUN=1\n')
        ex = (self.fake.root / 'sdcard/mu300-install.conf.example').read_text()
        for k in ('MU300_STORAGE', 'MU300_SD_ERASE', 'MU300_BOOT', 'MU300_PASSWORD', 'MU300_DRY_RUN'):
            self.assertIn(k, ex)
```

(`nothing_written()` compares sha256 of `boot_a`, `boot_b`, `misc` with the values taken before the run, size and
`st_mtime_ns` of `mmcblk0` and `mmcblk1` (they may be large sparse files), and checks that `$MU300_TMP/mu300-magisk`
is gone. `INSTALLER_DIR` is a directory laid out like the zip's `mu300/`, made once per module by `stage_mu300()`,
which copies the files exactly as Task 10's `stage` does; `INSTALLER` is its `install.sh`.)

- [ ] **Step 3: Run them to make sure they fail** — `cd tests && python3 -m unittest test_magisk_installer -v` → FAIL (installer missing).

- [ ] **Step 4: Write `android/magisk/installer/mu300-install.sh`, part 1**

```sh
#!/system/bin/sh
# MU300 Linux from a Magisk zip: the whole installation, on the device, with no computer. customize.sh runs this as
# a child of Magisk's busybox (nothing here can change Magisk's own shell) and turns its exit status into the
# result: 0 installed, 3 dry run (nothing written), anything else refused or failed - the messages say what was and
# was not written.
#
# Everything device-specific - the header and AVB data of Android's boot image, the misc block, the firmware and the
# Android files - is read from this device here; the zip carries none of it. Nothing is written to the eMMC or the
# card before the systems are installed, and misc is armed last.
#
# MU300_LIB=1 defines the functions and runs nothing (tests/, which also set MU300_DIR to a directory laid out like
# the zip's mu300/). The MU300_* paths below point at a fake device there.
set -eu
D=${MU300_DIR:-$(cd "$(dirname "$0")" && pwd)}
# mu300-update first: it defines a say and a die of its own, which the ones below replace. MU300_LIB=1 while it is
# read makes it define its functions only; the caller's value comes back afterwards.
_mu300_lib=${MU300_LIB:-}
MU300_LIB=1
. "$D/mu300-update"
. "$D/android-boot-image.sh"
MU300_LIB=$_mu300_lib
TOP=$D                                   # i18n.sh reads $TOP/i18n
BY=${MU300_BY_NAME:-/dev/block/by-name}
SDCARD=${MU300_SDCARD:-/sdcard}
T=${MU300_TMP:-/data/local/tmp}
W=$T/mu300-magisk
MAGISKBIN=${MAGISKBIN:-/data/adb/magisk}
MAGISKBOOT=${MAGISKBOOT:-$MAGISKBIN/magiskboot}
UBB=${MU300_UBB:-$D/busybox}             # the release's static busybox: its mkpasswd
DEVSH=${MU300_DEVICE_SH:-/system/bin/sh}
ANDROID_SH=${MU300_ANDROID_SH:-/system/bin/sh}
APATH=${MU300_ANDROID_PATH:-/system/bin:/system/xbin:/vendor/bin}
NEED_OPENWRT=$((800 * 1024 * 1024)); NEED_UBUNTU=$((1600 * 1024 * 1024)); NEED_BOTH=$((2400 * 1024 * 1024))
MU300_LANG=${MU300_LANG:-}
. "$D/i18n.sh"
. "$D/storage.sh"

say() { echo "- $*"; }
warn() { echo "! $*"; }
die() { echo "! $*"; exit 1; }
gib() { awk -v b="$1" 'BEGIN { printf "%.1f GiB", b / 1073741824 }'; }
# Android's own shell and toolbox, never Magisk's standalone busybox: its mke2fs makes ext2 only and its losetup has
# no -S. su -c sh gives install.sh exactly this environment.
asw() { env -u ASH_STANDALONE PATH="$APATH" "$@"; }
su_do() { asw "$DEVSH" -c "$1"; }            # storage.sh's way to run a command on the device: here it is local

android_lang() {  # the language of the Android locale: tr, zh or en
    _l=
    for _p in persist.sys.locale persist.sys.locales persist.sys.language ro.product.locale ro.product.locale.language; do
        _l=$(getprop "$_p" 2>/dev/null) && [ -n "$_l" ] && break
    done
    case ${_l%%,*} in tr|tr-*|tr_*) echo tr ;; zh|zh-*|zh_*) echo zh ;; *) echo en ;; esac
}

# ---- answers: mu300-install.conf, read line by line, never sourced (it is a text file anyone with storage access
# can write, and this runs as root). Only these keys are taken.
CONF_KEYS='MU300_STORAGE MU300_SD_ERASE MU300_REGION_OVERWRITE MU300_MODE MU300_BOOT_OS MU300_BOOT MU300_BOOT_ATTEMPTS MU300_HOTSPOT MU300_GPU MU300_PASSWORD MU300_DEVICE MU300_LANG MU300_DRY_RUN'
CONF_FILE=; CONF_SET=
conf_find() { for _f in "$SDCARD/mu300-install.conf" "$SDCARD/Download/mu300-install.conf"; do [ -f "$_f" ] && { echo "$_f"; return 0; }; done; return 0; }
conf_load() {
    [ -n "$1" ] && [ -f "$1" ] || return 0
    CONF_FILE=$1
    while IFS= read -r _line || [ -n "$_line" ]; do
        _line=$(printf '%s' "$_line" | tr -d '\r' | sed 's/^[[:space:]]*//')
        case $_line in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
        _k=$(printf '%s' "${_line%%=*}" | tr -d ' \t'); _v=${_line#*=}
        _v=$(printf '%s' "$_v" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        case $_v in \"*\") _v=${_v#\"}; _v=${_v%\"} ;; \'*\') _v=${_v#\'}; _v=${_v%\'} ;; esac
        case " $CONF_KEYS " in
            *" $_k "*) eval "$_k=\$_v"; CONF_SET="$CONF_SET $_k" ;;    # $_k is one of CONF_KEYS: the eval is safe
            *) warn "$(t 'mu300-install.conf: {1} is not a setting of this installer; ignored' "$_k")" ;;
        esac
    done < "$1"
}
conf_check() {  # every answer has a value this installer knows, or it stops before anything happens
    _bad() { die "$(t 'mu300-install.conf: {1}={2} is not valid' "$1" "$2")"; }
    case ${MU300_STORAGE:-} in ''|internal|sd) ;; *) _bad MU300_STORAGE "$MU300_STORAGE" ;; esac
    case ${MU300_MODE:-} in ''|update|wipe) ;; *) _bad MU300_MODE "$MU300_MODE" ;; esac
    case ${MU300_BOOT_OS:-} in ''|ubuntu|openwrt) ;; *) _bad MU300_BOOT_OS "$MU300_BOOT_OS" ;; esac
    case ${MU300_BOOT:-} in ''|linux|android) ;; *) _bad MU300_BOOT "$MU300_BOOT" ;; esac
    case ${MU300_BOOT_ATTEMPTS:-} in ''|[1-6]) ;; *) _bad MU300_BOOT_ATTEMPTS "$MU300_BOOT_ATTEMPTS" ;; esac
    for _k in MU300_SD_ERASE MU300_REGION_OVERWRITE; do
        eval "_v=\${$_k:-}"; case $_v in ''|yes) ;; *) _bad "$_k" "$_v" ;; esac
    done
    for _k in MU300_HOTSPOT MU300_GPU; do
        eval "_v=\${$_k:-}"; case $_v in ''|yes|no) ;; *) _bad "$_k" "$_v" ;; esac
    done
    case ${MU300_DEVICE:-} in ''|f50|u30air) ;; *) _bad MU300_DEVICE "$MU300_DEVICE" ;; esac
    case ${MU300_LANG:-} in ''|en|tr|zh) ;; *) _bad MU300_LANG "$MU300_LANG" ;; esac
    case ${MU300_DRY_RUN:-} in ''|1) ;; *) _bad MU300_DRY_RUN "$MU300_DRY_RUN" ;; esac
    if [ -n "${MU300_PASSWORD:-}" ] && [ ${#MU300_PASSWORD} -lt 6 ]; then
        die "$(t 'mu300-install.conf: MU300_PASSWORD must have at least 6 characters')"
    fi
}
src_of() { case " $CONF_SET " in *" $1 "*) t 'mu300-install.conf' ;; *) t 'default' ;; esac; }   # where a value came from

# ---- the device, its slots and the zip
env_check() {
    [ -n "${MU300_SKIP_ROOT_CHECK:-}" ] || [ "$(id -u)" = 0 ] || die "$(t 'this installer needs root')"
    [ -x "$MAGISKBOOT" ] || die "$(t "Magisk's magiskboot was not found; install this zip with Magisk 26 or newer")"
    chmod 755 "$UBB" 2>/dev/null || true
}
detect_device() {
    _m="$(getprop ro.product.model) / $(getprop ro.product.device)"
    say "$(t 'device: {1}' "$_m")"
    case $_m in
        *U30Air*|*U30_Air*|*"U30 Air"*) DEVICE=u30air ;;
        *MU300*|*F50*|*mu300*) DEVICE=f50 ;;
        *) DEVICE= ;;
    esac
    [ -z "${MU300_DEVICE:-}" ] || DEVICE=$MU300_DEVICE
    [ -n "$DEVICE" ] || die "$(t 'This does not look like a ZTE F50/MU300 or U30 Air. If it is one, set MU300_DEVICE=f50 or u30air in mu300-install.conf.')"
}
hex_at() { dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | od -An -tx1 -v | tr -d ' \n'; }
slot_setup() {
    case $(getprop ro.boot.slot_suffix) in
        _a) ANDROID_SLOT=a; LINUX_SLOT=b ;;
        _b) ANDROID_SLOT=b; LINUX_SLOT=a ;;
        *) die "$(t 'cannot tell which slot Android runs from')" ;;
    esac
    MISC=$BY/misc; BOOT_ANDROID=$BY/boot_$ANDROID_SLOT; BOOT_LINUX=$BY/boot_$LINUX_SLOT
    [ -e "$MISC" ] && [ -e "$BOOT_ANDROID" ] && [ -e "$BOOT_LINUX" ] || die "$(t 'this is not an A/B device with misc, boot_a and boot_b')"
    # never the partition Android booted from, whatever the links say
    [ "$(readlink -f "$BOOT_ANDROID")" != "$(readlink -f "$BOOT_LINUX")" ] || die "$(t 'boot_a and boot_b are the same partition')"
    LIVE_BC=$(hex_at "$MISC" 2048 32)
    bc_valid "$LIVE_BC" || die "$(t 'misc holds no valid boot control block; nothing was changed')"
    say "$(t 'Android runs from slot {1}; Linux goes to slot {2} (boot_{2})' "$ANDROID_SLOT" "$LINUX_SLOT")"
}
manifest_load() {  # the zip's manifest, read like the conf file: only these keys
    while IFS='=' read -r _k _v; do
        case $_k in TAG|SYSTEM|OS|UBUNTU|KERNEL|KERNEL_ASSET|ROOTFS_ASSET|SHA256_KERNEL|SHA256_ROOTFS) eval "$_k=\$_v" ;; esac
    done < "$1"
    [ -n "${OS:-}" ] && [ -n "${KERNEL:-}" ] && [ -n "${SHA256_ROOTFS:-}" ] || die "$(t 'the zip is incomplete (no manifest)')"
    OSES=$OS
}
payload_unpack() {  # the two payload files out of the zip, checked against the manifest before anything uses them
    _need=$(( $(unzip -l "$ZIPFILE" "payload/*" | awk 'END { print $1 }') + 512 * 1048576 ))
    _free=$(( $(df -k "$T" | awk 'NR == 2 { print $4 }') * 1024 ))
    [ "$_free" -ge "$_need" ] || die "$(t 'not enough free space in {1}: {2} needed, {3} free' "$T" "$(gib "$_need")" "$(gib "$_free")")"
    rm -rf "$W"; mkdir -p "$W/kernel"
    say "$(t 'Unpacking {1} and {2}' "$ROOTFS_ASSET" "$KERNEL_ASSET")"
    unzip -p "$ZIPFILE" "payload/$ROOTFS_ASSET" > "$T/mu300-$OS.tar.gz" &&
        unzip -p "$ZIPFILE" "payload/$KERNEL_ASSET" > "$W/kernel.tar.gz" || die "$(t 'could not unpack the zip')"
    [ "$(sha256sum "$T/mu300-$OS.tar.gz" | cut -d' ' -f1)" = "$SHA256_ROOTFS" ] &&
        [ "$(sha256sum "$W/kernel.tar.gz" | cut -d' ' -f1)" = "$SHA256_KERNEL" ] ||
        die "$(t 'the zip is damaged (checksum mismatch); download it again')"
    tar -xzf "$W/kernel.tar.gz" -C "$W/kernel" && rm -f "$W/kernel.tar.gz" || die "$(t 'could not unpack the kernel bundle')"
    KB=$W/kernel
    grep -qw "$DEVICE" "$KB/devices" 2>/dev/null || die "$(t 'this kernel does not support the {1}' "$DEVICE")"
}
cleanup() {  # every exit: the staging directory holds this device's proprietary files
    [ -n "${MOUNTED:-}" ] && umount_target "$MOUNTED" >/dev/null 2>&1
    rm -rf "$W" "$T/mu300-${OS:-none}.tar.gz" "$T/mu300-vendor-${OS:-none}.tar.gz" "$T/mu300-install.env" 2>/dev/null
    rm -rf "$T/mu300root-magisk" 2>/dev/null
}

# ---- what to do
mount_target() {  # mount_target DIR: the Linux filesystem of the plan, with Android's mount helper
    cp "$D/android-mount-mu300root.sh" "$T/" || return 1
    if [ "$SD_MODE" = 1 ]; then
        asw MU300_SD_DEV="$SD_DEV" "$ANDROID_SH" "$T/android-mount-mu300root.sh" "$1"
    else
        asw MU300_OFF="$INT_OFF" MU300_SIZE="$INT_SIZE" "$ANDROID_SH" "$T/android-mount-mu300root.sh" "$1"
    fi | grep -q '^MOUNTED' && MOUNTED=$1
}
umount_target() { asw "$ANDROID_SH" "$T/android-mount-mu300root.sh" -u "$1" >/dev/null; MOUNTED=; }
need_for() { case $1 in openwrt) echo $NEED_OPENWRT ;; ubuntu) echo $NEED_UBUNTU ;; *) echo $NEED_BOTH ;; esac; }
plan_storage() {
    region_probe || die "$(t 'could not read the partition table')"
    region_find_existing
    INT_OFF=$OFF; INT_SIZE=$SIZE; int_existing=$existing
    sd_probe; sd_ex=no
    [ -n "$SD_DEV" ] && sd_ex=$(sd_existing)
    case ${MU300_STORAGE:-} in
        sd) [ -n "$SD_DEV" ] || die "$(t 'MU300_STORAGE=sd, but there is no usable SD card in the device')"; SD_MODE=1 ;;
        internal) SD_MODE=0 ;;
        *)  # an installation where it is (the card first, as init looks there first), else inside when it fits
            if [ "$sd_ex" = yes ]; then SD_MODE=1
            elif [ "$int_existing" = yes ] || [ "$INT_SIZE" -ge "$(need_for "$OS")" ]; then SD_MODE=0
            elif [ -n "$SD_DEV" ]; then
                die "$(t 'There is too little free space inside for Linux. The SD card ({1}, {2}) can hold it: put MU300_STORAGE=sd and MU300_SD_ERASE=yes into /sdcard/mu300-install.conf (everything on the card is erased).' "$SD_DEV" "$(gib "$SD_BYTES")")"
            else
                die "$(t 'There is too little free space inside for Linux and no SD card. Insert a card (MU300_STORAGE=sd, MU300_SD_ERASE=yes in /sdcard/mu300-install.conf), or make room with the installer for computers.')"
            fi ;;
    esac
    if [ "$SD_MODE" = 1 ]; then
        [ "$sd_ex" != foreign ] || die "$(t 'the SD card ({1}) holds another Linux (ext4) filesystem, and the installer never formats one that is not its own (mu300sd).' "$SD_DEV")"
        existing=$([ "$sd_ex" = yes ] && echo yes || echo no); TARGET_SIZE=$SD_BYTES
        if [ "$existing" = no ] && [ "${MU300_SD_ERASE:-}" != yes ]; then
            die "$(t 'Installing to the SD card ({1}, {2}) erases everything on it. To allow that, put MU300_SD_ERASE=yes into /sdcard/mu300-install.conf.' "$SD_DEV" "$(gib "$SD_BYTES")")"
        fi
    else
        existing=$int_existing; TARGET_SIZE=$INT_SIZE; DIRTY=0
        [ "$INT_SIZE" -ge $((700 * 1024 * 1024)) ] || die "$(t 'There is too little free space inside for Linux and no SD card. Insert a card (MU300_STORAGE=sd, MU300_SD_ERASE=yes in /sdcard/mu300-install.conf), or make room with the installer for computers.')"
        if [ "$existing" = no ]; then
            region_dirty
            [ "$DIRTY" = 0 ] || [ "${MU300_REGION_OVERWRITE:-}" = yes ] ||
                die "$(t 'The free space behind the partitions is not empty ({1} of 16 samples hold data); it may be used by this firmware. To use it anyway, put MU300_REGION_OVERWRITE=yes into /sdcard/mu300-install.conf.' "$DIRTY")"
        fi
    fi
    INTERNAL_EXISTS=0; [ "$int_existing" = yes ] && INTERNAL_EXISTS=1
    FORMAT=0; UPDATE=0
    if [ "$existing" = yes ]; then
        case ${MU300_MODE:-update} in update) UPDATE=1 ;; wipe) FORMAT=1 ;; esac
    else
        FORMAT=1
    fi
}
inspect_target() {  # which systems the existing filesystem holds, and its Ubuntu release
    HAVE_SYSTEMS=; HAVE_UBUNTU=
    [ "$existing" = yes ] && [ "$FORMAT" = 0 ] || return 0
    mount_target "$T/mu300root-magisk" || die "$(t 'could not mount the existing Linux filesystem')"
    for _os in ubuntu openwrt; do [ -d "$T/mu300root-magisk/$_os" ] && HAVE_SYSTEMS="$HAVE_SYSTEMS $_os"; done
    HAVE_UBUNTU=$(sed -n 's/^VERSION_ID="\(.*\)"/\1/p' "$T/mu300root-magisk/ubuntu/usr/lib/os-release" 2>/dev/null | head -n1)
    umount_target "$T/mu300root-magisk"
}
plan_choices() {
    _after=$(printf '%s\n' $HAVE_SYSTEMS "$OS" | sort -u | tr '\n' ' ')
    case $_after in *ubuntu*openwrt*|*openwrt*ubuntu*) _need=$NEED_BOTH ;; *) _need=$(need_for "$OS") ;; esac
    [ "$TARGET_SIZE" -ge "$_need" ] || die "$(t 'that choice needs about {1} MiB and this device has {2} MiB of free space' "$((_need / 1048576))" "$((TARGET_SIZE / 1048576))")"
    if [ "$KERNEL" = 5.4 ] && case " $HAVE_SYSTEMS " in *" ubuntu "*) [ "$HAVE_UBUNTU" = 26.04 ] ;; *) false ;; esac; then
        die "$(t 'Ubuntu 26.04 is installed, and it needs a mainline kernel (6.18 or 7.2): use a zip with one of those kernels, or MU300_MODE=wipe.')"
    fi
    [ "$SD_MODE" = 0 ] || grep -qx sdcard "$KB/features" 2>/dev/null || die "$(t 'this kernel cannot read the SD card; use a newer zip')"
    BOOT_OS=${MU300_BOOT_OS:-$OS}
    case " $_after " in *" $BOOT_OS "*) ;; *) die "$(t 'MU300_BOOT_OS={1}, but {1} is not installed' "$BOOT_OS")" ;; esac
    DEFAULT_LINUX=1; [ "${MU300_BOOT:-linux}" = android ] && DEFAULT_LINUX=0
    BOOT_ATTEMPTS=${MU300_BOOT_ATTEMPTS:-5}
    IMPORT_HOTSPOT=1; [ "${MU300_HOTSPOT:-yes}" = no ] && IMPORT_HOTSPOT=0
    GPU=${MU300_GPU:-yes}
    WIPE_LEGACY=0; [ "$OS" = ubuntu ] && [ "$FORMAT" = 0 ] && WIPE_LEGACY=1
}
plan_print() {
    echo
    say "$(t 'Plan')"
    echo "  $(t 'system:         {1} (zip {2}, kernel {3})' "$OS${UBUNTU:+ $UBUNTU}" "$TAG" "$KERNEL")"
    if [ "$SD_MODE" = 1 ]; then
        echo "  $(t 'storage:        SD card {1}, {2} ({3})' "$SD_DEV" "$(gib "$TARGET_SIZE")" "$(src_of MU300_STORAGE)")"
    else
        echo "  $(t 'storage:        internal, offset {1}, {2} ({3})' "$INT_OFF" "$(gib "$TARGET_SIZE")" "$(src_of MU300_STORAGE)")"
    fi
    echo "  $(t 'filesystem:     {1}' "$([ "$FORMAT" = 1 ] && t 'CREATE new ext4 (erases what is there)' || t 'keep: settings and data of {1} are kept' "$OS")")"
    [ -z "$HAVE_SYSTEMS" ] || echo "  $(t 'already there:  {1}' "$HAVE_SYSTEMS")"
    echo "  $(t 'boots:          {1} ({2})' "$BOOT_OS" "$(src_of MU300_BOOT_OS)")"
    echo "  $(t 'default boot:   {1}' "$([ "$DEFAULT_LINUX" = 1 ] && t 'Linux (Android after {1} failed boots in a row)' "$BOOT_ATTEMPTS" || t 'Android, Linux on demand')")"
    echo "  $(t 'writes:         {1}, boot_{2}, 32 bytes of misc (boot_{3}, the partition table and userdata are not touched)' "$([ "$SD_MODE" = 1 ] && echo "$SD_DEV" || t 'the Linux region')" "$LINUX_SLOT" "$ANDROID_SLOT")"
}
write_example() {  # every setting, what it does and the value this run uses: nobody has to type it from a README
    {
        echo "# MU300 Linux Magisk installer settings. Copy to $SDCARD/mu300-install.conf, edit, install the zip again."
        echo "# Values of the last run ($TAG, $(date '+%Y-%m-%d %H:%M')) are shown; a line starting with # is not used."
        echo "#MU300_STORAGE=$([ "${SD_MODE:-0}" = 1 ] && echo sd || echo internal)        # internal or sd"
        echo "#MU300_SD_ERASE=yes            # allow erasing the SD card for Linux"
        echo "#MU300_REGION_OVERWRITE=yes    # use internal free space that holds data"
        echo "#MU300_MODE=update             # update (keep settings and data) or wipe"
        echo "#MU300_BOOT_OS=${BOOT_OS:-$OS}           # ubuntu or openwrt: which system boots"
        echo "#MU300_BOOT=linux              # linux (default boot, Android after failed boots) or android"
        echo "#MU300_BOOT_ATTEMPTS=${BOOT_ATTEMPTS:-5}          # 1-6 failed boots in a row before Android"
        echo "#MU300_HOTSPOT=yes             # copy Android's hotspot name and password"
        echo "#MU300_GPU=yes                 # Mali GPU (OpenCL) files"
        echo "#MU300_PASSWORD=               # 6+ characters; empty: generated"
        echo "#MU300_DEVICE=${DEVICE:-f50}              # f50 or u30air, only when the model is not recognised"
        echo "#MU300_LANG=${MU300_LANG:-en}                # en, tr or zh"
        echo "#MU300_DRY_RUN=1               # only show what would be done"
    } > "$SDCARD/mu300-install.conf.example" 2>/dev/null || true
}
```

and at the end of the file the entry point (Task 8 adds the build and install calls after `write_example`):

```sh
main() {
    conf_load "$(conf_find)"
    conf_check
    [ -n "$MU300_LANG" ] || MU300_LANG=$(android_lang)
    echo "MU300 Linux $(sed -n 's/^TAG=//p' "$D/manifest")"
    [ -z "$CONF_FILE" ] || say "$(t 'settings from {1}:{2}' "$CONF_FILE" "$CONF_SET")"
    env_check
    manifest_load "$D/manifest"
    detect_device
    slot_setup
    trap cleanup EXIT
    payload_unpack
    plan_storage
    inspect_target
    plan_choices
    plan_print
    write_example
    if [ "${MU300_DRY_RUN:-}" = 1 ]; then say "$(t 'Dry run: nothing was written.')"; exit 3; fi
}
[ -z "${MU300_LIB:-}" ] || return 0
main "$@"
```

- [ ] **Step 5: Translations** — add a `tr` and a `zh` line for every new `t '...'` message (the keys as written; the
words `MU300_STORAGE=sd`, `MU300_SD_ERASE=yes` etc. and paths stay as they are). Add
`'android/magisk/installer/mu300-install.sh'` to `SOURCES` in `tools/check-i18n.py`. Run `python3 tools/check-i18n.py`
→ nothing missing.

- [ ] **Step 6: Run the tests** — `cd tests && python3 -m unittest test_magisk_installer test_i18n test_static -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add android/magisk/installer tests/fakedevice.py tests/test_magisk_installer.py tools/check-i18n.py i18n
git commit -m "magisk installer: finds the device, its slots and where Linux goes, from what is there and mu300-install.conf; a dry run writes nothing"
```

---

### Task 8: The Magisk installer, part 2 — build, install, write, arm

**Files:**
- Modify: `android/magisk/installer/mu300-install.sh` (new functions, `main` continues)
- Test: `tests/test_magisk_installer.py` (new class `Install`)

**Interfaces:**
- Consumes: Task 4 (`bootimg_from_stock`, `write_boot`, `part_size`, `kernel_modules_into_systems`), Task 5 (everything), Task 7 (the plan variables).
- Produces: `random_chars ALPHABET N`, `password_hash PASSWORD`, `build_vendor`, `build_boot`, `write_env FILE`, `install_systems`, `install_boot`, `arm_linux`, `report`.

- [ ] **Step 1: Write the failing tests**

```python
class Install(InstallerCase):
    def test_full_install_internal(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual(sha(by / 'boot_a'), self.before['boot_a'])            # Android's partition untouched
        img = (by / 'boot_b').read_bytes()
        self.assertEqual(img[:8], b'ANDROID!')
        files = cpio_all(unlz4_legacy(ramdisk_of(img)))
        self.assertEqual(files['etc/mu300-linux-slot'][1], b'b\n')
        self.assertEqual(files['etc/mu300-device'][1], b'f50\n')
        self.assertIn('android/vendor/bin/modem_control', files)
        self.assertIn('init', files)                                           # from the bundle's generic segment
        bc = (by / 'misc').read_bytes()[0x800:0x820]
        self.assertEqual(bc, files['etc/misc-bc-slot-b-trial.bin'][1])         # armed: Linux trial on b
        env = (self.fake.root / 'install.env').read_text()
        for line in ('FORMAT=1', 'OSES="openwrt"', 'SD_MODE=0', 'DEFAULT_LINUX=1', 'BOOT_ATTEMPTS=5', 'KERNEL=6.18'):
            self.assertIn(line, env)
        self.assertRegex(env, r"PWHASH='\$6\$[./0-9A-Za-z]{16}\$")
        pw = (self.fake.root / 'sdcard/mu300-linux-password.txt').read_text()
        self.assertRegex(pw, r'[a-km-zA-HJ-NP-Z2-9]{12}')
        self.assertFalse((self.fake.root / 'tmp/mu300-magisk').exists())      # proprietary staging gone

    def test_slot_b_android(self):
        self.device(slot='_b')
        self.assertEqual(self.run_installer().returncode, 0)
        by = self.fake.root / 'dev/block/by-name'
        self.assertEqual(sha(by / 'boot_b'), self.before['boot_b'])            # Android's partition untouched
        files = cpio_all(unlz4_legacy(ramdisk_of((by / 'boot_a').read_bytes())))
        self.assertEqual(files['etc/mu300-linux-slot'][1], b'a\n')
        self.assertEqual((by / 'misc').read_bytes()[0x800:0x820], files['etc/misc-bc-slot-a-trial.bin'][1])

    def test_android_side_runs_with_android_tools(self):
        self.run_installer()
        for line in (self.fake.root / 'android-sh.log').read_text().splitlines():
            self.assertTrue(line.startswith('ASH_STANDALONE= '), line)

    def test_install_failure_leaves_boot_and_misc(self):
        r = self.run_installer(extra_env={'FAKE_INSTALL_FAILS': '1'})
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.nothing_written(except_fs=True))

    def test_misc_changed_meanwhile_is_not_armed(self):
        # a stub android-install.sh that changes misc while "installing" (an OTA, another installer)
        self.fake_install_hook('printf X | dd of="$FAKE/dev/block/by-name/misc" bs=1 seek=2060 conv=notrunc 2>/dev/null')
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertNotEqual((self.fake.root / 'dev/block/by-name/misc').read_bytes()[0x800:0x802], b'_b')

    def test_refusals_write_nothing(self):
        for setup in (lambda: self.device(region=False), lambda: self.corrupt_payload(),
                      lambda: self.device(model='Pixel 7'), lambda: self.no_magiskboot()):
            self.reset(); setup()
            self.assertEqual(self.run_installer().returncode, 1)
            self.assertTrue(self.nothing_written())

    def test_password_from_conf_is_used_and_removed(self):
        r = self.run_installer(conf='MU300_PASSWORD=correct horse\n')
        self.assertEqual(r.returncode, 0)
        conf = (self.fake.root / 'sdcard/mu300-install.conf').read_text()
        self.assertNotIn('correct horse', conf)

    def test_mainline_modules_into_every_system(self):
        self.existing_filesystem(systems=('ubuntu',))
        self.assertEqual(self.run_installer().returncode, 0)          # an OpenWrt 6.18 zip added to it
        krel = self.kernel_release
        self.assertTrue((self.fake.root / f'fs/ubuntu/lib/modules/{krel}/extra').is_dir())
        self.assertTrue((self.fake.root / f'fs/openwrt/lib/modules/{krel}').is_dir())


class Password(ShellTest):
    @unittest.skipIf(not busybox_has('mkpasswd'), "this busybox has no mkpasswd")
    def test_hash_is_sha512crypt(self):
        # the bundled busybox's mkpasswd and tools/sha512crypt.py agree
        import importlib.util
        spec = importlib.util.spec_from_file_location('s', TOP / 'tools' / 'sha512crypt.py')
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        for shell in self.each_shell():
            r = self.sh(shell, f'MU300_LIB=1 MU300_UBB="busybox" . "{INSTALLER}"; password_hash "s3cret pw"')
            h = r.stdout.strip()
            salt = h.split('$')[2]
            self.assertEqual(h, m.sha512_crypt('s3cret pw', salt))

    def test_random_chars(self):
        for shell in self.each_shell():
            seen = set()
            for _ in range(20):
                r = self.sh(shell, f'MU300_LIB=1 . "{INSTALLER}"; random_chars "$PW_ALPHABET" 12')
                self.assertRegex(r.stdout.strip(), r'^[a-km-zA-HJ-NP-Z2-9]{12}$')
                seen.add(r.stdout)
            self.assertEqual(len(seen), 20)
```

(`sha`, `ramdisk_of`, `cpio_all`, `busybox_has`, `InstallerCase.reset/device/corrupt_payload/no_magiskboot/
existing_filesystem/fake_install_hook` are helpers in this file; `fake_install_hook` adds a line to the fake
`android-sh` before it prints `MU300-INSTALL-OK`.)

- [ ] **Step 2: Run them to make sure they fail** — `cd tests && python3 -m unittest test_magisk_installer.Install test_magisk_installer.Password -v` → FAIL (exit 3 never reached / functions missing).

- [ ] **Step 3: The functions** — add to `mu300-install.sh` before `main`:

```sh
# ---- the password: never empty, never the image's. 12 characters without look-alikes, unless the conf gives one.
PW_ALPHABET=abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789
CRYPT_ITOA=./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz
random_chars() {  # random_chars ALPHABET N: bytes past the last whole multiple of the alphabet are skipped (no bias)
    head -c 256 /dev/urandom | od -An -tu1 -v | awk -v a="$1" -v n="$2" '
        BEGIN { l = length(a); lim = int(256 / l) * l }
        { for (i = 1; i <= NF && got < n; i++) if ($i < lim) { s = s substr(a, $i % l + 1, 1); got++ } }
        END { if (got < n) exit 1; print s }'
}
password_hash() {  # password_hash PASSWORD: SHA-512 crypt, password on stdin, as install.sh's sha512crypt.py makes it
    _salt=$(random_chars "$CRYPT_ITOA" 16) || return 1
    _h=$(printf '%s\n' "$1" | "$UBB" mkpasswd -m sha512 -S "$_salt" -P 0) || return 1
    case $_h in "\$6\$$_salt\$"?*) printf '%s' "$_h" ;; *) return 1 ;; esac
}

# ---- build everything in $W before anything is written
build_vendor() {
    _miss=$(collect_firmware "$W/fw") || die "$(t 'these firmware files could not be read from the device:{1}' "$_miss")
$(t 'Wi-Fi and Bluetooth need them; without them the system installs and boots but has no hotspot.')"
    collect_subset "$D/subset-files.txt" "$W/subset" || die "$(t 'could not read the Android files the modem needs from this device')"
    _gpu=
    if [ "$GPU" = yes ]; then
        if collect_gpu "$D/gpu-files.txt" "$W/gpu"; then _gpu=$W/gpu
        else warn "$(t 'the Mali GPU files are incomplete on this device; installing without them')"; fi
    fi
    vendor_overlay "$OS" "$W/fw" "$W/subset" "$_gpu" "$T/mu300-vendor-$OS.tar.gz" || die "$(t 'could not pack the vendor files')"
}
build_boot() {
    mkdir -p "$W/ramdisk/etc" "$W/boot"
    echo "$DEVICE" > "$W/ramdisk/etc/mu300-device"
    echo "$LINUX_SLOT" > "$W/ramdisk/etc/mu300-linux-slot"
    bc_files "$LIVE_BC" "$W/ramdisk/etc" || die "$(t 'could not build the boot control blocks')"
    cp -a "$W/subset" "$W/ramdisk/android"
    # Linux on slot a needs an init that knows it (Tasks 1-3); the init comes from the bundle's generic segment
    if [ "$LINUX_SLOT" = a ]; then
        "$MAGISKBOOT" decompress "$KB/ramdisk-generic.lz4" "$W/generic.cpio" >/dev/null 2>&1 &&
            grep -q 'mu300-linux-slot' "$W/generic.cpio" ||
            die "$(t 'Android runs from slot b, and the kernel of this zip cannot boot Linux from slot a yet; use a newer zip')"
        rm -f "$W/generic.cpio"
    fi
    device_segment "$W/ramdisk" "$W/device.lz4" || die "$(t 'could not build the boot ramdisk (magiskboot)')"
    linux_boot_image "$BOOT_ANDROID" "$KB" "$W/device.lz4" "$W/boot" || die "$(t 'could not build the boot image')"
    say "$(t 'boot image for slot {1}: kernel {2}, {3} bytes' "$LINUX_SLOT" "$(cat "$KB/kernel.release" 2>/dev/null || echo 5.4)" "$(( $(fsize "$W/boot/new") + $(fsize "$W/boot/vbmeta") ))")"
}
build_password() {
    PW=${MU300_PASSWORD:-}
    [ -n "$PW" ] || PW=$(random_chars "$PW_ALPHABET" 12) || die "$(t 'could not generate a password')"
    PWHASH=$(password_hash "$PW") || die "$(t 'could not hash the password')"
}

# ---- install: the systems, then the boot image, then misc
write_env() {  # the file android-install.sh reads; the same fields install.sh writes
    printf 'OFF=%s\nSIZE=%s\nOFF_S=%s\nSIZE_S=%s\nFORMAT=%s\nOSES="%s"\nWIPE_LEGACY=%s\nUPDATE=%s\nBOOT_OS=%s\nDEFAULT_LINUX=%s\nBOOT_ATTEMPTS=%s\nIMPORT_HOTSPOT=%s\nKERNEL=%s\nSD_MODE=%s\nSD_DEV=%s\nINTERNAL_EXISTS=%s\nPWHASH='"'"'%s'"'"'\n' \
      "$INT_OFF" "$INT_SIZE" "$((INT_OFF / 512))" "$((INT_SIZE / 512))" "$FORMAT" "$OSES" "$WIPE_LEGACY" "$UPDATE" \
      "$BOOT_OS" "$DEFAULT_LINUX" "$BOOT_ATTEMPTS" "$IMPORT_HOTSPOT" "$KERNEL" "$SD_MODE" "${SD_DEV:-}" "$INTERNAL_EXISTS" "$PWHASH" > "$1"
}
install_systems() {
    say "$(t 'Installing {1} (this takes a few minutes)' "$OS")"
    cp "$D/android-install.sh" "$D/android-mount-mu300root.sh" "$T/" || die "$(t 'could not copy the installer files')"
    write_env "$T/mu300-install.env"
    asw "$ANDROID_SH" "$T/android-install.sh" 2>&1 | tee "$W/device-install.log"
    grep -q MU300-INSTALL-OK "$W/device-install.log" || die "$(t 'installation on the device failed; boot_{1} and misc were not changed' "$LINUX_SLOT")"
    if [ -s "$KB/kernel.release" ]; then        # a mainline bundle: its modules into every system on the filesystem
        mount_target "$T/mu300root-magisk" || die "$(t 'could not mount the Linux filesystem for the kernel modules')"
        DISK=$T/mu300root-magisk MU300_NO_DEPMOD=1 kernel_modules_into_systems "$KB" ||
            { umount_target "$T/mu300root-magisk"; die "$(t 'could not install the kernel modules')"; }
        sync; umount_target "$T/mu300root-magisk"
    fi
}
install_boot() {
    say "$(t 'Writing and verifying boot_{1}' "$LINUX_SLOT")"
    write_boot "$BOOT_LINUX" "$(part_size "$BOOT_LINUX")" "$W/boot/new" "$W/boot/vbmeta" "$W/boot/newfooter" ||
        die "$(t 'boot_{1} did not verify after writing; misc was not changed, so Android keeps booting' "$LINUX_SLOT")"
}
arm_linux() {  # the Linux slot's trial block, built from the misc block read at the start; misc must still hold that
    [ "$(hex_at "$MISC" 2048 32)" = "$LIVE_BC" ] || die "$(t 'misc changed during the installation; Linux was not armed. Install the zip again.')"
    dd if="$W/ramdisk/etc/misc-bc-slot-$LINUX_SLOT-trial.bin" of="$MISC" bs=1 seek=2048 conv=notrunc 2>/dev/null; sync
    [ "$(hex_at "$MISC" 2048 32)" = "$(od -An -tx1 -v "$W/ramdisk/etc/misc-bc-slot-$LINUX_SLOT-trial.bin" | tr -d ' \n')" ] ||
        die "$(t 'misc did not verify after writing; reboot normally, Android is not affected')"
}
report() {
    _ip=192.168.77.1; [ "$DEVICE" = u30air ] && _ip=192.168.78.1
    _users=$([ "$OS" = ubuntu ] && echo ubuntu || echo root)
    ( umask 077; printf 'MU300 Linux %s, %s\nuser: %s\npassword: %s\nDelete this file after the first login.\n' \
        "$TAG" "$(date '+%Y-%m-%d %H:%M')" "$_users" "$PW" > "$SDCARD/mu300-linux-password.txt" ) 2>/dev/null || true
    if [ -n "$CONF_FILE" ] && grep -q '^[[:space:]]*MU300_PASSWORD=' "$CONF_FILE"; then
        sed -i 's/^[[:space:]]*MU300_PASSWORD=.*/# MU300_PASSWORD was used by the installer and removed/' "$CONF_FILE" 2>/dev/null || true
    fi
    echo
    say "$(t 'Done. Reboot to start {1}.' "$BOOT_OS")"
    echo "  $(t 'password for {1}: {2}   (also in {3}; delete that file after the first login)' "$_users" "$PW" "$SDCARD/mu300-linux-password.txt")"
    echo "  $(t 'USB network: {1}   SSH: {2}' "$_ip" "$_users@$_ip")"
    echo "  $(t 'switch systems: mu300-os ubuntu|openwrt   back to Android: mu300-next-boot android')"
    echo "  $(t 'If Linux does not start, the device returns to Android by itself.')"
}
```

and `main` continues after the dry-run line:

```sh
    build_vendor
    build_boot
    build_password
    install_systems
    install_boot
    arm_linux
    report
```

- [ ] **Step 4: Translations** — `tr`/`zh` lines for the new messages; `python3 tools/check-i18n.py` → nothing missing.

- [ ] **Step 5: Run the tests** — `cd tests && python3 -m unittest test_magisk_installer -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add android/magisk/installer tests/test_magisk_installer.py i18n
git commit -m "magisk installer: builds the boot image and the vendor files on the device, installs, verifies boot_<slot> and arms misc last"
```

---

### Task 9: The zip's Magisk side — `customize.sh` and the module

**Files:**
- Create: `android/magisk/installer/customize.sh`, `android/magisk/installer/update-binary`, `android/magisk/installer/updater-script`, `android/magisk/installer/module.prop.in`
- Modify: `tests/test_static.py` (new scripts in `shell_scripts()`, `test_customize_leaves_magisks_shell_alone`)
- Test: `tests/test_magisk_zip.py` (class `Customize`)

**Interfaces:**
- Consumes: Magisk's installer environment (`$ZIPFILE`, `$MODPATH`, `$TMPDIR`, `$BOOTMODE`, `$MAGISK_VER_CODE`, `$MAGISKBIN`, `ui_print`, `abort`, `set_perm`, `set_perm_recursive`).
- Produces: the zip's top level as Task 10 packs it.

- [ ] **Step 1: Write the failing tests** — `tests/test_magisk_zip.py`:

```python
"""The zip: customize.sh in a fake Magisk installer environment, and tools/make-magisk-zips.sh on a fake release
(and, in the Magisk workflow, on the real one: MU300_MAGISK_ZIPS=<dir of zips>)."""

MAGISK_ENV = r'''
ui_print() { echo "$1"; }
abort() { echo "ABORT $1"; rm -rf "$MODPATH"; exit 1; }
set_perm() { chmod "$4" "$1"; }
set_perm_recursive() { chmod -R u+rwX "$1"; }
BOOTMODE=true; MAGISK_VER_CODE=30700
'''


class Customize(ShellTest):
    def zip_with(self, install_sh):
        ...  # a zip holding mu300/install.sh (the given body), module.prop, action.sh, switch.sh, system/bin/mu300-linux

    def run_customize(self, shell, install_sh, bootmode='true'):
        z = self.zip_with(install_sh)
        mod = self.tmp / 'modpath'; mod.mkdir(exist_ok=True)
        code = (MAGISK_ENV + f'BOOTMODE={bootmode}; ZIPFILE="{z}"; MODPATH="{mod}"; TMPDIR="{self.tmp}/t"; '
                f'MAGISKBIN="{self.tmp}/magisk"; mkdir -p "$TMPDIR"; set +e +u; '
                f'. "{TOP}/android/magisk/installer/customize.sh"; echo "after opts=$-"')
        return self.sh(shell, code), mod

    def test_success_installs_the_switch(self):
        for shell in self.each_shell():
            r, mod = self.run_customize(shell, 'echo installing; exit 0')
            self.assertIn('installing', r.stdout)
            self.assertTrue((mod / 'switch.sh').exists() and (mod / 'module.prop').exists())
            self.assertNotIn('e', r.stdout.split('after opts=')[1].split()[0])   # Magisk's options untouched

    def test_failure_and_dry_run_abort(self):
        for shell in self.each_shell():
            for rc, word in ((1, 'not installed'), (3, 'Dry run')):
                r, mod = self.run_customize(shell, f'exit {rc}')
                self.assertIn('ABORT', r.stdout)
                self.assertIn(word, r.stdout)
                self.assertFalse(mod.exists())

    def test_recovery_is_refused(self):
        for shell in self.each_shell():
            r, _ = self.run_customize(shell, 'echo ran; exit 0', bootmode='false')
            self.assertIn('ABORT', r.stdout)
            self.assertNotIn('ran', r.stdout)
```

(`$MAGISKBIN/busybox` in the test is a symlink to the host's `busybox`; skip without it.)
In `tests/test_static.py`:

```python
    def test_customize_leaves_magisks_shell_alone(self):
        # Magisk sources customize.sh: errexit or nounset there would end Magisk's own installer before its cleanup
        c = (TOP / 'android' / 'magisk' / 'installer' / 'customize.sh').read_text()
        code = '\n'.join(l for l in c.splitlines() if not l.lstrip().startswith('#'))
        self.assertNotRegex(code, r'\bset\s+-[a-z]*[eu]')
        self.assertNotRegex(code, r'(^|\s)exit\b')
        self.assertIn('SKIPUNZIP=1', code)

    def test_installer_never_writes_androids_boot_partition(self):
        s = (TOP / 'android' / 'magisk' / 'installer' / 'mu300-install.sh').read_text()
        self.assertNotRegex(s, r'of="?\$BOOT_ANDROID')
        self.assertNotRegex(s, r'write_boot "?\$BOOT_ANDROID')
```

and add `android/magisk/installer/*.sh`, `android/magisk/installer/update-binary` and
`android/magisk/mu300-linux-switch/*.sh` to the candidates in `shell_scripts()`.

- [ ] **Step 2: Run them to make sure they fail** — `cd tests && python3 -m unittest test_magisk_zip.Customize test_static -v` → FAIL (customize.sh missing).

- [ ] **Step 3: Write the files**

`android/magisk/installer/customize.sh`:

```sh
# MU300 Linux installer zip. Magisk sources this file into its own installer shell, so it changes none of that
# shell's options and never exits: the installation runs as a child process (mu300/install.sh) under Magisk's
# busybox, and its exit status decides - 0 installed, 3 dry run, anything else not installed. Only after success
# does this zip become the "MU300 Linux" switch module (Action button, su -c mu300-linux). The three messages here
# stay English: they are shown before the installer knows the language.
SKIPUNZIP=1
if ! $BOOTMODE; then
    abort "! Install this zip in the Magisk app or with: magisk --install-module <zip> (not from recovery)"
fi
[ "${MAGISK_VER_CODE:-0}" -ge 26000 ] || abort "! This zip needs Magisk 26.0 or newer"
mu300_dir=$TMPDIR/mu300
rm -rf "$mu300_dir"
unzip -o -q "$ZIPFILE" 'mu300/*' -d "$TMPDIR" >&2 || abort "! The zip could not be unpacked"
MU300_DIR=$mu300_dir ZIPFILE=$ZIPFILE MAGISKBIN=${MAGISKBIN:-/data/adb/magisk} \
    "${MAGISKBIN:-/data/adb/magisk}/busybox" sh "$mu300_dir/install.sh"
mu300_rc=$?
rm -rf "$mu300_dir"
case $mu300_rc in
    0) ;;
    3) abort "- Dry run: nothing was written, the module was not installed" ;;
    *) abort "! MU300 Linux was not installed; the messages above say why and what was not changed" ;;
esac
unzip -o -q "$ZIPFILE" module.prop action.sh switch.sh 'system/*' -d "$MODPATH" >&2 || abort "! The switch module could not be installed"
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/switch.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/system/bin/mu300-linux" 0 0 0755
unset mu300_dir mu300_rc
```

`android/magisk/installer/update-binary`: Magisk's standard module installer, as Magisk ships it in
`assets/module_installer.sh` (verified against v30.7):

```sh
#!/sbin/sh

#################
# Initialization
#################

umask 022

# echo before loading util_functions
ui_print() { echo "$1"; }

require_new_magisk() {
  ui_print "*******************************"
  ui_print " Please install Magisk v20.4+! "
  ui_print "*******************************"
  exit 1
}

#########################
# Load util_functions.sh
#########################

OUTFD=$2
ZIPFILE=$3

mount /data 2>/dev/null

[ -f /data/adb/magisk/util_functions.sh ] || require_new_magisk
. /data/adb/magisk/util_functions.sh
[ $MAGISK_VER_CODE -lt 20400 ] && require_new_magisk

install_module
exit 0
```

`android/magisk/installer/updater-script`: the single line `#MAGISK`.

`android/magisk/installer/module.prop.in` (Task 10 fills `@TAG@`, `@CODE@`, `@SYSTEM@`, `@KERNEL@`):

```
id=mu300_linux_switch
name=MU300 Linux
version=@TAG@
versionCode=@CODE@
author=dikeckaan
description=Installed @SYSTEM@ with kernel @KERNEL@ (@TAG@). Action: boot Linux now. Arms the one-shot boot of the Linux slot in misc (32 bytes) and reboots; a Linux that fails to boot falls back to Android by itself.
```

- [ ] **Step 4: Run the tests** — `cd tests && python3 -m unittest test_magisk_zip.Customize test_static -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add android/magisk/installer tests/test_magisk_zip.py tests/test_static.py
git commit -m "magisk installer: customize.sh runs the installation as a child and becomes the switch module only after it succeeded"
```

---

### Task 10: `tools/make-magisk-zips.sh`

**Files:**
- Create: `tools/make-magisk-zips.sh`
- Test: `tests/test_magisk_zip.py` (class `Builder`, and class `ReleaseZips` for `MU300_MAGISK_ZIPS`)

**Interfaces:**
- Consumes: a release directory with `SHA256SUMS`, `mu300-kernel.tar.gz`, `mu300-kernel-6.18.tar.gz`, `mu300-kernel-7.2.tar.gz`, `mu300-openwrt-rootfs.tar.gz`, `mu300-ubuntu-rootfs.tar.gz`, `mu300-ubuntu-26.04-rootfs.tar.gz`, `mu300-update`.
- Produces: `OUT/mu300-magisk-<tag>-<system>-k<kernel>.zip` for system in `openwrt ubuntu-24.04 ubuntu-26.04`, kernel in `5.4 6.18 7.2`, without `ubuntu-26.04-k5.4`; `OUT/SHA256SUMS-magisk`. Exit 1 on a checksum mismatch or an audit failure.

- [ ] **Step 1: Write the failing tests**

```python
ALLOWED = {'META-INF/com/google/android/update-binary', 'META-INF/com/google/android/updater-script',
           'module.prop', 'customize.sh', 'action.sh', 'switch.sh', 'system/bin/mu300-linux',
           'mu300/install.sh', 'mu300/android-boot-image.sh', 'mu300/mu300-update', 'mu300/android-install.sh',
           'mu300/android-mount-mu300root.sh', 'mu300/storage.sh', 'mu300/i18n.sh', 'mu300/i18n/tr.tsv',
           'mu300/i18n/zh.tsv', 'mu300/subset-files.txt', 'mu300/gpu-files.txt', 'mu300/busybox', 'mu300/manifest'}


class Builder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-zips-'))
        self.rel = self.tmp / 'v2026.10.06'
        fake_release(self.rel)            # tiny tarballs with the right names; mu300-kernel.tar.gz has ./busybox

    def build(self):
        return subprocess.run(['sh', str(TOP / 'tools/make-magisk-zips.sh'), str(self.rel), str(self.tmp / 'out')],
                              capture_output=True, text=True)

    def test_eight_zips(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        names = sorted(p.name for p in (self.tmp / 'out').glob('*.zip'))
        self.assertEqual(len(names), 8)
        self.assertNotIn('mu300-magisk-v2026.10.06-ubuntu-26.04-k5.4.zip', names)
        self.assertIn('mu300-magisk-v2026.10.06-openwrt-k6.18.zip', names)
        sums = (self.tmp / 'out/SHA256SUMS-magisk').read_text().split('\n')
        self.assertEqual(len([s for s in sums if s]), 8)

    def test_layout_manifest_and_stored_payload(self):
        self.build()
        z = zipfile.ZipFile(self.tmp / 'out/mu300-magisk-v2026.10.06-ubuntu-24.04-k7.2.zip')
        names = set(z.namelist())
        self.assertEqual(names - ALLOWED, {'payload/mu300-kernel-7.2.tar.gz', 'payload/mu300-ubuntu-rootfs.tar.gz'})
        for i in z.infolist():
            if i.filename.startswith('payload/'):
                self.assertEqual(i.compress_type, zipfile.ZIP_STORED)
        m = dict(l.split('=', 1) for l in z.read('mu300/manifest').decode().split('\n') if '=' in l)
        self.assertEqual((m['OS'], m['UBUNTU'], m['KERNEL']), ('ubuntu', '24.04', '7.2'))
        self.assertEqual(m['SHA256_ROOTFS'], hashlib.sha256(z.read('payload/mu300-ubuntu-rootfs.tar.gz')).hexdigest())
        self.assertIn('versionCode=20261006', z.read('module.prop').decode())

    def test_checksum_mismatch_stops(self):
        with open(self.rel / 'mu300-openwrt-rootfs.tar.gz', 'ab') as f:
            f.write(b'x')
        self.assertNotEqual(self.build().returncode, 0)


@unittest.skipUnless(os.environ.get('MU300_MAGISK_ZIPS'), 'MU300_MAGISK_ZIPS not set (the Magisk workflow sets it)')
class ReleaseZips(unittest.TestCase):
    """the real zips of a release: the allow-list, the manifest against the payload, and a dry run of each zip's own
    installer on the fake device that must print the plan and write nothing"""
    ...
```

- [ ] **Step 2: Run them to make sure they fail** — `cd tests && python3 -m unittest test_magisk_zip.Builder -v` → FAIL.

- [ ] **Step 3: Write `tools/make-magisk-zips.sh`**

```sh
#!/bin/sh
# Build the Magisk installer zips of a release, one per system and kernel (design:
# docs/superpowers/specs/2026-10-05-magisk-installer-design.md):
#   tools/make-magisk-zips.sh RELEASE_DIR OUT_DIR [TAG]
# RELEASE_DIR holds the release's assets exactly as published; every one must match its SHA256SUMS. The zips contain
# the installer, the switch module, the 5.4 bundle's static busybox and the two assets of their combination - no file
# from any device: the installer reads those on the device it runs on. Every zip is checked against an exact list
# of names before it counts.
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
R=$(cd "${1:?usage: tools/make-magisk-zips.sh RELEASE_DIR OUT_DIR [TAG]}" && pwd)
mkdir -p "${2:?usage: tools/make-magisk-zips.sh RELEASE_DIR OUT_DIR [TAG]}"; O=$(cd "$2" && pwd)
TAG=${3:-$(basename "$R")}
sha() { (sha256sum "$1" 2>/dev/null || shasum -a 256 "$1") | cut -d' ' -f1; }
for f in mu300-kernel.tar.gz mu300-kernel-6.18.tar.gz mu300-kernel-7.2.tar.gz mu300-openwrt-rootfs.tar.gz \
         mu300-ubuntu-rootfs.tar.gz mu300-ubuntu-26.04-rootfs.tar.gz; do
    want=$(awk -v f="$f" '$2 == f || $2 == "*" f {print $1}' "$R/SHA256SUMS")
    [ -n "$want" ] && [ -f "$R/$f" ] && [ "$(sha "$R/$f")" = "$want" ] ||
        { echo "$f is missing from $R or does not match its SHA256SUMS" >&2; exit 1; }
done
# Magisk wants an integer that grows: the date in the tag (v2026.10.06 -> 20261006)
code=$(printf '%s' "$TAG" | tr -cd 0-9 | cut -c1-9); [ -n "$code" ] || code=1
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
tar -xzOf "$R/mu300-kernel.tar.gz" ./busybox > "$W/busybox"
[ -s "$W/busybox" ] || { echo "mu300-kernel.tar.gz has no busybox" >&2; exit 1; }

# what every zip holds besides its payload, by name (the audit below compares against this)
COMMON='META-INF/com/google/android/update-binary META-INF/com/google/android/updater-script module.prop customize.sh
action.sh switch.sh system/bin/mu300-linux mu300/install.sh mu300/android-boot-image.sh mu300/mu300-update
mu300/android-install.sh mu300/android-mount-mu300root.sh mu300/storage.sh mu300/i18n.sh mu300/i18n/tr.tsv
mu300/i18n/zh.tsv mu300/subset-files.txt mu300/gpu-files.txt mu300/busybox mu300/manifest'
stage() {  # stage DIR SYSTEM KERNEL ROOTFS_ASSET KERNEL_ASSET OS UBUNTU
    S=$1
    mkdir -p "$S/META-INF/com/google/android" "$S/system/bin" "$S/mu300/i18n" "$S/payload"
    I=$TOP/android/magisk/installer; M=$TOP/android/magisk/mu300-linux-switch
    cp "$I/update-binary" "$I/updater-script" "$S/META-INF/com/google/android/"
    sed -e "s/@TAG@/$TAG/" -e "s/@CODE@/$code/" -e "s/@SYSTEM@/$2/" -e "s/@KERNEL@/$3/" "$I/module.prop.in" > "$S/module.prop"
    cp "$I/customize.sh" "$M/action.sh" "$M/switch.sh" "$S/"
    cp "$M/system/bin/mu300-linux" "$S/system/bin/"
    cp "$I/mu300-install.sh" "$S/mu300/install.sh"
    cp "$TOP/tools/android-boot-image.sh" "$TOP/tools/android-install.sh" "$TOP/tools/android-mount-mu300root.sh" \
       "$TOP/tools/storage.sh" "$TOP/tools/i18n.sh" "$S/mu300/"
    # the checkout's (in CI the tag's, so the release's own): the installer needs bootimg_from_stock, which an older
    # release's mu300-update does not have when zips are built locally from one
    cp "$TOP/rootfs/overlay/opt/mu300/bin/mu300-update" "$S/mu300/mu300-update"
    cp "$TOP/i18n/tr.tsv" "$TOP/i18n/zh.tsv" "$S/mu300/i18n/"
    cp "$TOP/android-vendor/subset-files.txt" "$TOP/android-vendor/gpu-files.txt" "$S/mu300/"
    cp "$W/busybox" "$S/mu300/busybox"
    cp "$R/$4" "$R/$5" "$S/payload/"
    printf 'TAG=%s\nSYSTEM=%s\nOS=%s\nUBUNTU=%s\nKERNEL=%s\nKERNEL_ASSET=%s\nROOTFS_ASSET=%s\nSHA256_KERNEL=%s\nSHA256_ROOTFS=%s\n' \
        "$TAG" "$2" "$6" "$7" "$3" "$5" "$4" "$(sha "$R/$5")" "$(sha "$R/$4")" > "$S/mu300/manifest"
}
audit() {  # audit ZIP ROOTFS_ASSET KERNEL_ASSET: exactly the names it should hold, the payload stored
    printf '%s\n' $COMMON "payload/$2" "payload/$3" | LC_ALL=C sort > "$W/want"
    unzip -Z1 "$1" | LC_ALL=C sort > "$W/have"
    diff "$W/have" "$W/want" >&2 || { echo "$1 does not hold exactly the expected files (< extra, > missing)" >&2; return 1; }
    [ "$(unzip -Zv "$1" 'payload/*' | grep -c 'compression method: *none')" = 2 ] || { echo "$1: the payload is compressed" >&2; return 1; }
}
: > "$O/SHA256SUMS-magisk"
for s in openwrt:mu300-openwrt-rootfs.tar.gz:openwrt: \
         ubuntu-24.04:mu300-ubuntu-rootfs.tar.gz:ubuntu:24.04 \
         ubuntu-26.04:mu300-ubuntu-26.04-rootfs.tar.gz:ubuntu:26.04; do
    name=${s%%:*}; rest=${s#*:}; rootfs=${rest%%:*}; rest=${rest#*:}; os=${rest%%:*}; ubuntu=${rest#*:}
    for k in 5.4:mu300-kernel.tar.gz 6.18:mu300-kernel-6.18.tar.gz 7.2:mu300-kernel-7.2.tar.gz; do
        kv=${k%%:*}; kasset=${k#*:}
        # Ubuntu 26.04's programs need system calls 5.4 does not have (install.sh refuses the pair too)
        [ "$ubuntu" = 26.04 ] && [ "$kv" = 5.4 ] && continue
        zip=mu300-magisk-$TAG-$name-k$kv.zip
        S=$W/$name-$kv
        stage "$S" "$name" "$kv" "$rootfs" "$kasset" "$os" "$ubuntu"
        rm -f "$O/$zip"
        (cd "$S" && find . -type f ! -path './payload/*' | sed 's|^\./||' | LC_ALL=C sort | zip -X -q -9 "$O/$zip" -@ &&
            zip -X -q -0 "$O/$zip" "payload/$kasset" "payload/$rootfs")
        audit "$O/$zip" "$rootfs" "$kasset"
        rm -rf "$S"
        printf '%s  %s\n' "$(sha "$O/$zip")" "$zip" >> "$O/SHA256SUMS-magisk"
        echo "$zip: $(wc -c < "$O/$zip" | tr -d ' ') bytes"
    done
done
```

Write `fake_release` (in `tests/test_magisk_zip.py`) and `ReleaseZips` (for each zip in `MU300_MAGISK_ZIPS`: the
allow-list, the manifest's hashes against `payload/`, then the dry run: unpack `mu300/`, build a `FakeDevice`, a
`magiskboot` stub as in Task 5, run `busybox sh mu300/install.sh` with `MU300_DRY_RUN=1` in the fake `sdcard`'s conf
and `ZIPFILE` = the zip: exit 3, `Plan` printed, nothing written).

- [ ] **Step 4: Run the tests** — `cd tests && python3 -m unittest test_magisk_zip -v` → PASS (`ReleaseZips` skipped).

- [ ] **Step 5: Try it on a local release** — with a release directory that has every asset (e.g. `release/<newest>/`
of the main checkout copied into a scratch directory together with its `mu300-update`):

```sh
sh tools/make-magisk-zips.sh /path/to/release/v2026.10.08 /tmp/mu300-zips
cd tests && MU300_MAGISK_ZIPS=/tmp/mu300-zips python3 -m unittest test_magisk_zip.ReleaseZips -v
```
Expected: eight zips between ~70 and ~170 MB, `ReleaseZips` PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/make-magisk-zips.sh tests/test_magisk_zip.py
git commit -m "make-magisk-zips: eight zips per release, one per system and kernel, from the published assets and checked name by name"
```

---

### Task 11: CI

**Files:**
- Create: `.github/workflows/magisk.yml`
- Modify: `.github/workflows/tests.yml` (install `zip`, `unzip`, `cpio`), `.github/workflows/installer.yml` (paths and the syntax step)

**Interfaces:**
- Consumes: Task 10's script and `ReleaseZips`.
- Produces: on `release: published` and on `workflow_dispatch` (input `tag`), the zips and `SHA256SUMS-magisk` attached to that release.

- [ ] **Step 1: `.github/workflows/magisk.yml`**

```yaml
name: Magisk installers

# The Magisk zips of a release (tools/make-magisk-zips.sh): built from the release's own published assets as soon as
# it is published (a prerelease too, which is how a release is tried first), tested, and attached to it. For an
# existing release: Actions > Magisk installers > Run workflow, with its tag. The zips are checked out from the tag,
# so the installer always matches the init and mu300-update of the release it installs.
on:
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      tag:
        description: 'Release tag, e.g. v2026.10.06'
        required: true

permissions:
  contents: write

concurrency:
  group: magisk-${{ github.event.release.tag_name || github.event.inputs.tag }}
  cancel-in-progress: false

jobs:
  zips:
    runs-on: ubuntu-latest
    env:
      TAG: ${{ github.event.release.tag_name || github.event.inputs.tag }}
      GH_TOKEN: ${{ github.token }}
    steps:
      - name: Tag
        run: echo "$TAG" | grep -Eq '^v[0-9][0-9A-Za-z.-]*$' || { echo "not a release tag: $TAG"; exit 1; }
      - uses: actions/checkout@v4
        with:
          ref: ${{ env.TAG }}
      - name: Tools
        run: |
          sudo apt-get install -y -qq zip unzip cpio dash busybox lz4 >/dev/null
          python3 -m pip install -q lz4 || python3 -m pip install -q --break-system-packages lz4
      - name: Release assets
        run: |
          mkdir -p "release/$TAG"
          gh release download "$TAG" -R "$GITHUB_REPOSITORY" -D "release/$TAG" \
            -p SHA256SUMS -p 'mu300-*.tar.gz' -p mu300-update
          (cd "release/$TAG" && sha256sum -c SHA256SUMS)
      - name: Build
        run: sh tools/make-magisk-zips.sh "release/$TAG" out "$TAG"
      - name: Test the zips
        run: cd tests && MU300_MAGISK_ZIPS="$GITHUB_WORKSPACE/out" python3 -m unittest test_magisk_zip -v
      - name: Attach to the release
        run: gh release upload "$TAG" -R "$GITHUB_REPOSITORY" --clobber out/*.zip out/SHA256SUMS-magisk
```

- [ ] **Step 2: `tests.yml`** — the Linux step installs `dash busybox lz4 zip unzip cpio`; macOS has zip, unzip and
cpio already.

- [ ] **Step 3: `installer.yml`** — add `'android/magisk/**'` to both `paths` lists and
`android/magisk/installer/mu300-install.sh android/magisk/installer/customize.sh tools/android-boot-image.sh tools/make-magisk-zips.sh`
to the `for f in ...; do sh -n "$f"; done` list.

- [ ] **Step 4: Check the YAML** — `python3 -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]" .github/workflows/*.yml`
(PyYAML, if installed; otherwise `actionlint` if available). Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows
git commit -m "ci: every published release gets its Magisk zips, built from its own assets, tested and attached"
```

(After the merge, the maintainer runs the workflow once with `workflow_dispatch` on the newest prerelease and checks
the attached zips with Task 13 before any user is pointed at them.)

---

### Task 12: Documentation

**Files:**
- Modify: `README.md` (new section "Installing from Android with a Magisk zip" before "Starting Linux from Android without a computer"; that section points at it), `android/magisk/README.md` (the installer zip and the switch module are one module now), `tests/README.md` (the new test files)

- [ ] **Step 1: README section** — the download story of the spec ("What the user gets", points 1-6), the table of
zips, the `mu300-install.conf` table (keys, values, defaults), "Both systems: two zips", where the password is,
what is written (the slot Android is not on, 32 bytes of misc, the region or the card), how to get back to Android
(`mu300-next-boot android`, or failing boots), and that shrinking `userdata` needs the computer installer.

- [ ] **Step 2: `android/magisk/README.md`** — the release zips install Linux and stay as this module; `build.sh` keeps
building the switch-only module for computer installs; the slot wording from Task 3.

- [ ] **Step 3: `tests/README.md`** — rows for `test_android_boot_image.py`, `test_magisk_installer.py`,
`test_magisk_zip.py`, `test_magisk_switch.py`, `fakedevice.py`, and the new classes in the existing files.

- [ ] **Step 4: Commit**

```bash
git add README.md android/magisk/README.md tests/README.md
git commit -m "README: installing from Android with a Magisk zip, which zip to take, and the settings file"
```

---

### Task 13: On the devices

**Files:** none changed unless something fails (then: a fix with its own test, and a `docs/FINDINGS.md` entry).

Preconditions: both test boards on Android, slot a, Magisk 30.7 (see the test-device notes: F50 adb `324950664950`,
U30 Air `323960377386`). Zips built locally with Task 10 from the newest release plus this branch's scripts (the
scripts in a zip come from the checkout; the payload from the release). The release must be one whose bundles carry
Task 1-3's init: build a local release with `tools/make-release.sh <tag>` (no `--publish`) from this branch first.

- [ ] **Step 1: Keys** — `adb -s <serial> shell getevent -lp` on both boards; record in `docs/FINDINGS.md` which keys
exist (settles the volume-key question in the spec).

- [ ] **Step 2: F50, internal, update path** — `adb push mu300-magisk-<tag>-openwrt-k6.18.zip /sdcard/Download/`,
install it in the Magisk app (scrcpy), default answers. Expected output: plan with `storage: internal`, `keep`,
`boot_b`, then `Done`. Reboot. OpenWrt boots (`ssh root@192.168.77.1` with the password from the output);
`cat /run/mu300/linux-slot` → `b`; `mu300-update boot` reports "already up to date" or updates cleanly;
`mu300-next-boot android`, reboot, Action button in Magisk → Linux again.

- [ ] **Step 3: F50, SD card** — without a conf: the Ubuntu 24.04 k5.4 zip with `MU300_STORAGE=sd` only → refused,
`misc` unchanged (`dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 | od -An -tx1` before and after). With
`MU300_SD_ERASE=yes` too, via `adb shell su -c 'magisk --install-module /sdcard/Download/<zip>'` → installs; boots
from the card (`cat /run/mu300-root-dev` → `/dev/mmcblk1p1`).

- [ ] **Step 4: U30 Air, two systems** — Ubuntu 26.04 k7.2 (internal), then OpenWrt k7.2. `mu300-os openwrt` and back.
The device file says `u30air`; USB network on `192.168.78.1`.

- [ ] **Step 5: Failure injection (F50)** — a zip with one byte of `payload/` flipped (`printf x | dd of=<zip> bs=1
seek=<offset inside payload> conv=notrunc`): refused, nothing written. `MU300_DRY_RUN=1`: exit 3, nothing written.
A card with an ext4 labelled `photos`: refused. `mv /data/adb/magisk/magiskboot{,.off}`: refused (move it back).

- [ ] **Step 6: Reboot loop** — OpenWrt on 5.4 installed by the zip: 20 reboots in a row, every one reaches
`mu300-boot-ok` (the pre-release check from the 2026-09-28 incident).

- [ ] **Step 7: Android on slot b** — only on a board whose slot b holds a complete Android (after an OTA), only after
`sh tools/backup-full.sh`, with the serial console attached. Install a zip: the plan says `boot_a`; Linux boots;
`cat /run/mu300/linux-slot` → `a`; `mu300-next-boot android` returns to Android on b. If no such board is available,
write in the PR description that the slot-b path is covered by unit tests only.

- [ ] **Step 8: Record** — a `docs/FINDINGS.md` section "Magisk installer on the devices" with what was run and seen
(times for each step on each board, the zip sizes), and commit:

```bash
git add docs/FINDINGS.md
git commit -m "FINDINGS: the Magisk installer on the F50 and the U30 Air, internal and SD, both systems"
```
