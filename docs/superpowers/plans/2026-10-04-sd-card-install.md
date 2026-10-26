# SD Card Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the installers put the Linux filesystem on the SD card (ext4 `mu300sd`) and let the boot image start from it, falling back to the internal region or Android when the card is gone.

**Architecture:** The mainline kernel gets its SD host back (polling card detect). `boot/init` looks for `mu300sd` on any non-eMMC mmc block device before the internal region. The installers ask where the filesystem goes and hand `SD_MODE`/`SD_DEV` to `tools/android-install.sh`; the storage logic that both shell installers and the uninstaller share lives in one sourced file, `tools/storage.sh`, so it can be tested with stubs the way `tools/linux-mode.sh` is.

**Tech Stack:** POSIX sh (dash, bash, busybox ash, Android mksh), PowerShell 5.1/7, Python 3 `unittest` (standard library only), Docker for the kernel build.

**Spec:** `docs/superpowers/specs/2026-10-04-sd-card-install-design.md`

## Global Constraints

- Label on the card: `mu300sd`. Label of the internal region: `mu300root`. Never the same label on both.
- The safety timer in `boot/init` stays `sleep 300`.
- A boot with no card and no `/.mu300/root-on-sd` marker never waits for a card; the wait is at most 8 seconds.
- A card installation writes to the eMMC only `boot_b` and 32 bytes of `misc` (plus the marker file inside an existing internal `mu300root`).
- An ext4 filesystem on the card whose label is not `mu300sd` is never formatted.
- Minimum card size: 700 MiB.
- `install.ps1` stays ASCII and its device commands contain no double quotes (`tests/test_static.py` enforces both).
- Every new `t '...'` / `T '...'` message gets a line in `i18n/tr.tsv` and `i18n/zh.tsv`; `python3 tools/check-i18n.py` must print nothing missing.
- Android's `mksh` has 32-bit arithmetic: no byte counts in `$(( ))` on the device; use 512-byte sectors.
- Scripts must parse under dash, bash and busybox ash; comment density and wording follow the surrounding files (plain sentences that say why).
- Commit messages follow the repository's style (a sentence, no `feat:` prefix) and end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Nothing is released from this branch: no `make-release --publish`, no `gh release`.

## Review Focus

- A card that Android has adopted as internal storage (`private:` volume, encrypted): expected to be refused with a clear message, not formatted. Pinned in Task 3 (`test_adopted_card_is_refused`).
- A card with a partition table but a first partition under 700 MiB (a small boot partition in front): expected "too small", not a fall-through to the whole device. Pinned in Task 4 (`test_small_first_partition_is_too_small`).
- The card enumerates as `mmcblk2` (the eMMC boot areas or another host take `mmcblk1`): the scan must still find it. Pinned in Task 2 (`test_finds_card_under_another_number`).
- `mu300sd` present but with no system inside (an interrupted install): init must fall back to the internal region instead of the standalone path. Pinned in Task 2 (`test_empty_card_falls_back`, static rule).
- `MU300_STORAGE=sd` with no card: an error before anything is asked or written. Pinned in Task 4 (`test_forced_sd_without_card_dies`).

---

### Task 1: The SD host under mainline

**Files:**
- Modify: `upstream/port/install.py:109-120` and its `expect` entry at `:178`
- Modify: `upstream/make-bundle.sh` (write a `features` file)
- Test: `tests/test_static.py` (class `Rules`)

**Interfaces:**
- Produces: a kernel in which `sdhci-sprd` probes the removable host; the kernel bundle contains a text file `features` with the word `sdcard` on a line of its own. Task 7 reads it.

- [ ] **Step 1: Write the failing test** — append to class `Rules` in `tests/test_static.py`:

```python
    def test_mainline_keeps_the_sd_host(self):
        # the SD card can hold the Linux filesystem: the port must not limit sdhci-sprd to the eMMC again
        port = (TOP / 'upstream' / 'port' / 'install.py').read_text()
        self.assertIn('MU300: CD GPIO deferred', port)
        self.assertNotIn("return -ENODEV;\\n' + t[j:]", port)
        self.assertIn('sdcard', (TOP / 'upstream' / 'make-bundle.sh').read_text())
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 -m unittest tests.test_static.Rules.test_mainline_keeps_the_sd_host -v` (from the repository root with `cd tests` semantics the suite already uses: `python3 -m unittest discover -s tests -k test_mainline_keeps_the_sd_host`)
Expected: FAIL (`'MU300: CD GPIO deferred' not found`).

- [ ] **Step 3: Replace the eMMC-only edit in `upstream/port/install.py`** — the block that starts at the comment `# sdhci-sprd: the SD card controller is not populated` up to (not including) the `# UMS9620 has the r11p3 controller` comment becomes:

```python
# sdhci-sprd: the SD card can hold the Linux filesystem, so both hosts are probed. An earlier version of this
# port let only the non-removable eMMC through (the SD host filled the log on a board without a card); take that
# edit out of a kernel tree that was prepared with it.
sp = os.path.join(tree, 'drivers/mmc/host/sdhci-sprd.c')
t = open(sp).read()
old = ('\t/* MU300: only the non-removable eMMC is used */\n'
       '\tif (!of_property_read_bool(pdev->dev.of_node, "non-removable"))\n\t\treturn -ENODEV;\n')
t = t.replace(old, '')
# The stock device tree gives the slot a card-detect GPIO on the vendor's EIC, which has no mainline driver: the
# supplier stays deferred and the host with it, for ever. Only for that host, poll for the card instead.
parse_old = '\tret = mmc_of_parse(host->mmc);\n\tif (ret)\n\t\treturn ret;\n'
parse_new = '''\tret = mmc_of_parse(host->mmc);
\tif (ret == -EPROBE_DEFER &&
\t    of_property_read_bool(pdev->dev.of_node, "cd-gpios") &&
\t    of_property_match_string(pdev->dev.of_node, "sprd,name", "sdio_sd") >= 0) {
\t\tdev_warn(&pdev->dev, "MU300: CD GPIO deferred, polling the card slot\\n");
\t\thost->mmc->caps |= MMC_CAP_NEEDS_POLL;
\t\tret = 0;
\t}
\tif (ret)
\t\treturn ret;
'''
if 'MU300: CD GPIO deferred' not in t:
    if parse_old not in t:
        sys.exit('port: sdhci-sprd mmc_of_parse anchor changed')
    t = t.replace(parse_old, parse_new, 1)
```

and in the `expect` list the `sdhci-sprd.c` entry becomes:

```python
    ('drivers/mmc/host/sdhci-sprd.c', ['DLL_PHASE_INTERNAL\t0x2 /* MU300 r11p3 */', 'MU300: CD GPIO deferred']),
```

Check that `install.py` already imports `sys` (it calls `sys.exit` elsewhere); if not, add `import sys` to its import line.

- [ ] **Step 4: `upstream/make-bundle.sh` writes the feature list** — next to where it writes `kernel.release` into the bundle directory, add:

```sh
# what this kernel can do that an older bundle could not; mu300-update reads it before it replaces a kernel
printf 'sdcard\n' > "$B/features"
```

(`$B` stands for the variable that script uses for the bundle directory: read the file and use its name.)

- [ ] **Step 5: Run the test** — `python3 -m unittest discover -s tests -k test_mainline_keeps_the_sd_host` → PASS.

- [ ] **Step 6: Build 6.18 and its modules**

```sh
docker build -t mu300-mainline-build upstream/
docker run --rm -v mu300-mainline:/src -v "$PWD/upstream":/work mu300-mainline-build bash /work/build.sh
docker run --rm -v mu300-mainline:/src -v "$PWD/upstream":/work mu300-mainline-build bash /work/build-modules.sh
upstream/make-bundle.sh work/mu300-kernel-6.18-sd.tar.gz
```
Expected: `build.sh` ends with the `ls -la` of `upstream/out`; no "port:" error.

- [ ] **Step 7: Try it on the F50 (OpenWrt, `root@192.168.77.1`, password `ubuntu`)**

```sh
python3 tools/mu300-scp.py work/mu300-kernel-6.18-sd.tar.gz root@192.168.77.1:/tmp/   # MU300_HOST/MU300_PASS as for mu300-ssh.py
MU300_HOST=root@192.168.77.1 MU300_PASS=ubuntu python3 tools/mu300-ssh.py 'MU300_KERNEL_BUNDLE=/tmp/mu300-kernel-6.18-sd.tar.gz mu300-update boot && reboot'
```
After the reboot:
```sh
MU300_HOST=root@192.168.77.1 MU300_PASS=ubuntu python3 tools/mu300-ssh.py 'ls /sys/class/mmc_host; cat /sys/block/mmcblk1/device/type /sys/block/mmcblk1/size; dmesg | grep -ciE "mmc1|sdio_sd"; dmesg | grep -iE "mmc1" | tail -5'
```
Expected: `mmc0 mmc1`, `SD`, a size, and a handful of `mmc1` lines (not hundreds).
Write test: `dd if=/dev/urandom of=/tmp/r bs=1M count=64; dd if=/tmp/r of=/dev/mmcblk1 bs=1M seek=8 conv=fsync; dd if=/dev/mmcblk1 bs=1M skip=8 count=64 | cmp - /tmp/r && echo SD-RW-OK; dmesg | grep -ciE "crc|timeout.*mmc1"` → `SD-RW-OK`, `0`. (The card may be wiped: the user said so on 2026-10-04.)

- [ ] **Step 8: The log without a card** — ask the user to pull the card, reboot, then: `dmesg | grep -c mmc1` after five minutes of uptime. Expected: under 20 lines. If it grows by the minute, add to `parse_new` (before `ret = 0;`) `host->mmc->caps2 |= MMC_CAP2_NO_SDIO | MMC_CAP2_NO_MMC;` (the poll then tries one card type instead of three), rebuild and measure again; record the numbers in `docs/FINDINGS.md` under a new heading "SD host under mainline".

- [ ] **Step 9: 5.4** — `mu300-update kernel 5.4`, reboot, `ls /sys/class/mmc_host; ls /dev/mmcblk1*`. Record the result in the same FINDINGS section. If there is no `mmcblk1` under 5.4, Task 4's installer check "card installations need a mainline kernel" becomes active (the code for it is in Task 4 either way).

- [ ] **Step 10: Commit**

```sh
git add upstream/port/install.py upstream/make-bundle.sh tests/test_static.py docs/FINDINGS.md
git commit -m "mainline: the SD host is probed again, with a polled card detect"
```

---

### Task 2: `boot/init` starts from the card

**Files:**
- Modify: `boot/init:286-336` (between `find_partitions` and the `rootloop` mount)
- Test: `tests/test_boot_init.py` (new), `tests/test_static.py`, `tests/README.md` (one table row)

**Interfaces:**
- Produces in `boot/init`, between the marker lines `# --- sd-root begin` and `# --- sd-root end`, three functions with no side effects beyond what is stated:
  - `ext4_label DEV` — prints the ext4 volume label of DEV, or nothing when DEV is not ext4.
  - `find_sd_root` — prints the first device matching `${MU300_SD_GLOB:-/dev/mmcblk[1-9]p1 /dev/mmcblk[1-9]}` whose label is `mu300sd`; status 1 when none.
  - `wait_sd_root SECONDS` — calls `find_sd_root` once a second (running `mdev -s` between tries) for at most SECONDS; prints the device or returns 1.
- Produces at run time: `/run/mu300-root-dev` containing the device that `/disk` was mounted from (the card device, or `mmcblk0@<offset>`).

- [ ] **Step 1: Write the failing tests** — `tests/test_boot_init.py`:

```python
"""boot/init: where the Linux filesystem is looked for. The card functions are cut out of init between their
markers and run against files that stand in for block devices."""
import re
import unittest

from helpers import TOP, ShellTest

INIT = (TOP / 'boot' / 'init').read_text()


def fake_ext4(path, label, magic=b'\x53\xef'):
    data = bytearray(4096)
    data[1080:1082] = magic
    data[1144:1144 + len(label)] = label.encode()
    path.write_bytes(bytes(data))


class SdRoot(ShellTest):
    def functions(self):
        m = re.search(r'# --- sd-root begin\n(.*?)# --- sd-root end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no sd-root block')
        return m.group(1)

    def run_fn(self, shell, call, glob):
        code = 'log() { :; }; mdev() { :; }\n' + self.functions() + f'\nMU300_SD_GLOB="{glob}"\n{call}\necho "rc=$?"'
        return self.sh(shell, code).stdout

    def test_label(self):
        fake_ext4(self.tmp / 'a', 'mu300sd')
        fake_ext4(self.tmp / 'b', 'mu300sd', magic=b'\x00\x00')
        for shell in self.each_shell():
            self.assertIn('mu300sd\nrc=0', self.run_fn(shell, f'ext4_label {self.tmp}/a', ''))
            self.assertEqual('rc=1', self.run_fn(shell, f'ext4_label {self.tmp}/b', '').strip())

    def test_partition_before_whole_card(self):
        fake_ext4(self.tmp / 'mmcblk1', 'mu300sd')
        fake_ext4(self.tmp / 'mmcblk1p1', 'mu300sd')
        for shell in self.each_shell():
            out = self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1 {self.tmp}/mmcblk[1-9]')
            self.assertIn(f'{self.tmp}/mmcblk1p1\nrc=0', out)

    def test_finds_card_under_another_number(self):
        fake_ext4(self.tmp / 'mmcblk2p1', 'mu300sd')
        for shell in self.each_shell():
            out = self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1 {self.tmp}/mmcblk[1-9]')
            self.assertIn('mmcblk2p1\nrc=0', out)

    def test_foreign_and_missing(self):
        fake_ext4(self.tmp / 'mmcblk1p1', 'photos')
        for shell in self.each_shell():
            self.assertEqual('rc=1', self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1').strip())
            self.assertEqual('rc=1', self.run_fn(shell, 'find_sd_root', f'{self.tmp}/none[1-9]').strip())

    def test_wait_gives_up(self):
        for shell in self.each_shell():
            out = self.run_fn(shell, 'sleep() { :; }; wait_sd_root 3', f'{self.tmp}/none[1-9]')
            self.assertEqual('rc=1', out.strip())


class Rules(unittest.TestCase):
    def test_timer_stays_at_300(self):
        self.assertIn('(sleep 300\n', INIT)

    def test_card_before_internal_and_wait_is_conditional(self):
        body = INIT[INIT.index('# --- sd-root end'):]
        self.assertLess(body.index('find_sd_root'), body.index('find_root_offset)'))
        # the wait must sit behind the two conditions, never on the plain path
        self.assertRegex(body, r'root-on-sd')
        self.assertEqual(body.count('wait_sd_root 8'), 1)

    def test_empty_card_falls_back(self):
        # a card with the label but no system inside must not end in standalone mode while an internal system exists
        self.assertIn('stage=sd-root-empty', INIT)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them** — `python3 -m unittest discover -s tests -p 'test_boot_init.py'` → FAIL (`boot/init has no sd-root block`).

- [ ] **Step 3: Add the functions to `boot/init`** — directly above the line `ROOT_OFFSET=27762098176`:

```sh
# The SD card as the home of the Linux filesystem: ext4 with the label mu300sd, on the card's first partition or
# on the whole card. Its own label, not mu300root - a device can hold both, and they must never be confused.
# --- sd-root begin
ext4_label() {  # ext4_label DEV: the volume label, nothing (and status 1) when DEV is not ext4
    [ "$(dd if="$1" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')" = 53ef ] || return 1
    dd if="$1" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000'; echo
}
find_sd_root() {  # the first card device that holds mu300sd; the eMMC is mmcblk0 and never looked at
    for d in ${MU300_SD_GLOB:-/dev/mmcblk[1-9]p1 /dev/mmcblk[1-9]}; do
        [ -e "$d" ] || continue
        [ "$(ext4_label "$d")" = mu300sd ] && { echo "$d"; return 0; }
    done
    return 1
}
wait_sd_root() {  # wait_sd_root SECONDS: a card that the polled host has not found yet
    n=0
    while [ "$n" -lt "$1" ]; do
        find_sd_root && return 0
        sleep 1; mdev -s; n=$((n + 1))
    done
    return 1
}
# --- sd-root end
```

- [ ] **Step 4: Use them** — replace the block from `if found=$(find_root_offset); then` through the `if [ -n "$rootloop" ] && losetup ... && rootdir=$(pick_root) && [ -n "$rootdir" ]; then` line (keeping `has_init`, `pick_root` and their comments where they are, above the new code) with:

```sh
mkdir -p /disk /newroot
mount_internal() {  # the mu300root region on the eMMC; busybox mount sets up a loop only for regular files, so
    # attach it explicitly and verify its offset (the loop options would be handed to ext4: EINVAL)
    if found=$(find_root_offset); then
        [ "$found" = "$ROOT_OFFSET" ] || log "stage=root-offset found=$found (default $ROOT_OFFSET)"
        ROOT_OFFSET=$found
    else
        log "stage=root-offset not-found (trying the default $ROOT_OFFSET)"
    fi
    rootloop=$(losetup -f 2>/run/rootmount.err)
    [ -n "$rootloop" ] && losetup -o $ROOT_OFFSET "$rootloop" /dev/mmcblk0 2>>/run/rootmount.err \
       && [ "$(cat /sys/block/${rootloop#/dev/}/loop/offset)" = "$ROOT_OFFSET" ] \
       && [ "$(dd if="$rootloop" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' ')" = 53ef ] \
       && mount -t ext4 -o noatime "$rootloop" /disk 2>>/run/rootmount.err \
       && echo "mmcblk0@$ROOT_OFFSET" > /run/mu300-root-dev
}
mount_sd() {  # mount_sd DEV: the card, a block device of its own - no loop
    mount -t ext4 -o noatime "$1" /disk 2>>/run/rootmount.err || return 1
    if [ -z "$(pick_root)" ]; then
        # the label without a system inside: an installation that was interrupted. Not a reason to give up on
        # an internal system.
        log "stage=sd-root-empty dev=$1"; umount /disk; return 1
    fi
    echo "$1" > /run/mu300-root-dev
    log "stage=sd-root dev=$1"
}
root_mounted=0
if sd=$(find_sd_root) && mount_sd "$sd"; then
    root_mounted=1
elif mount_internal; then
    root_mounted=1
    # A card installation on a device that also has an internal one leaves this marker in the internal one: the
    # card is what should boot, and the polled SD host may simply not have found it yet. Only then is it worth
    # waiting; a device without a card installation never waits.
    if [ -e /disk/.mu300/root-on-sd ]; then
        if sd=$(wait_sd_root 8); then
            umount /disk && losetup -d "$rootloop" 2>/dev/null
            mount_sd "$sd" || { log "stage=sd-root-failed dev=$sd"; mount_internal || root_mounted=0; }
        else
            log "stage=sd-root-missing (marker present, booting the internal system)"
        fi
    fi
else
    # nothing internal: the card is the only place a system can be, so give it its 8 seconds
    log "stage=sd-wait (no internal system)"
    sd=$(wait_sd_root 8) && mount_sd "$sd" && root_mounted=1
fi
if [ "$root_mounted" = 1 ] && rootdir=$(pick_root) && [ -n "$rootdir" ]; then
```

`wait_sd_root 8` appears twice here but the static rule counts one: fold the two calls into one helper line so the constant is written once — above `root_mounted=0` add `sd_wait() { wait_sd_root 8; }` and use `sd=$(sd_wait)` at both places.

- [ ] **Step 5: Run the tests** — `python3 -m unittest discover -s tests -p 'test_boot_init.py'` and `python3 -m unittest discover -s tests -p 'test_static.py'` → PASS (init still parses under every shell; `find_partitions` still follows `load_vendor_modules`).

- [ ] **Step 6: Add the row to `tests/README.md`**

```
| `test_boot_init.py` | `boot/init`: the card is looked for before the internal region, under any `mmcblkN`; a foreign or empty card falls back; the 300 s timer and the conditional wait |
```

- [ ] **Step 7: Commit**

```sh
git add boot/init tests/test_boot_init.py tests/README.md
git commit -m "init: the Linux filesystem may be on the SD card (label mu300sd), the internal region is the fallback"
```

---

### Task 3: Device side — `android-install.sh` and the mount helper

**Files:**
- Modify: `tools/android-install.sh` (header comment, the block from `# --- the region must not overlap` to the `trap` line, the per-system loop)
- Modify: `tools/android-mount-mu300root.sh`
- Test: `tests/test_android_install.py` (new), `tests/README.md` (one row)

**Interfaces:**
- Consumes: env file keys `SD_MODE=0|1`, `SD_DEV=/dev/block/mmcblkNpM` (Task 4/5 write them).
- Produces: `android-mount-mu300root.sh` with `MU300_SD_DEV=<dev>` set mounts that device (label must be `mu300sd`) instead of the eMMC loop; `-u MOUNTPOINT` works for both. Prints `MOUNTED <dev> on <mp>` as today.
- Produces in `android-install.sh`, between `# --- sd begin` / `# --- sd end`: `sd_release DEV` (status 1 with a message when Android keeps the card) and `sd_prepare DEV FORMAT` (status 1 with a message for a foreign ext4 or a missing `mu300sd`).

- [ ] **Step 1: Write the failing tests** — `tests/test_android_install.py`:

```python
"""tools/android-install.sh: the SD card branch, cut out between its markers and run with stubs for Android's
tools (sm, mke2fs, umount) and files standing in for block devices."""
import re

from helpers import TOP, ShellTest
from test_boot_init import fake_ext4

SRC = (TOP / 'tools' / 'android-install.sh').read_text()


class SdCard(ShellTest):
    def setUp(self):
        super().setUp()
        m = re.search(r'# --- sd begin\n(.*?)# --- sd end', SRC, re.S)
        self.assertIsNotNone(m, 'android-install.sh has no sd block')
        self.fn = m.group(1)
        self.dev = self.tmp / 'mmcblk1p1'
        self.stub('mke2fs', 'echo "mke2fs $*" >> "$STUBLOG/calls"')
        self.stub('umount', 'echo "umount $*" >> "$STUBLOG/calls"')
        (self.tmp / 'mounts').write_text('')

    def run_fn(self, shell, call, volumes='', still_mounted=False):
        (self.tmp / 'volumes').write_text(volumes)
        after = volumes if still_mounted else volumes.replace(' mounted ', ' unmounted ')
        (self.tmp / 'volumes.after').write_text(after)
        self.stub('sm', 'case "$1" in list-volumes) cat "$STUBLOG/volumes" ;; '
                        'unmount) echo "sm unmount $2" >> "$STUBLOG/calls"; cp "$STUBLOG/volumes.after" "$STUBLOG/volumes" ;; esac')
        code = ('say() { echo "[device] $*"; }\n' + f'MU300_MOUNTS="{self.tmp}/mounts"\n' + self.fn
                + f'\n{call}\necho "rc=$?"')
        r = self.sh(shell, code)
        calls = (self.tmp / 'calls').read_text() if (self.tmp / 'calls').exists() else ''
        (self.tmp / 'calls').unlink(missing_ok=True)
        return r.stdout, calls

    def test_blank_card_is_formatted(self):
        self.dev.write_bytes(bytes(4096))
        for shell in self.each_shell():
            out, calls = self.run_fn(shell, f'sd_prepare {self.dev} 1')
            self.assertIn('rc=0', out)
            self.assertIn(f'mke2fs -t ext4 -L mu300sd -F {self.dev}', calls)

    def test_foreign_ext4_is_refused(self):
        fake_ext4(self.dev, 'photos')
        for shell in self.each_shell():
            out, calls = self.run_fn(shell, f'sd_prepare {self.dev} 1')
            self.assertIn('rc=1', out)
            self.assertIn('photos', out)
            self.assertNotIn('mke2fs', calls)

    def test_existing_installation_is_kept_or_replaced(self):
        fake_ext4(self.dev, 'mu300sd')
        for shell in self.each_shell():
            out, calls = self.run_fn(shell, f'sd_prepare {self.dev} 0')
            self.assertIn('rc=0', out); self.assertNotIn('mke2fs', calls)
            out, calls = self.run_fn(shell, f'sd_prepare {self.dev} 1')
            self.assertIn('rc=0', out); self.assertIn('mke2fs', calls)

    def test_keep_without_installation_fails(self):
        self.dev.write_bytes(bytes(4096))
        for shell in self.each_shell():
            out, _ = self.run_fn(shell, f'sd_prepare {self.dev} 0')
            self.assertIn('rc=1', out)

    def test_android_releases_the_card(self):
        for shell in self.each_shell():
            out, calls = self.run_fn(shell, f'sd_release {self.dev}', volumes='public:179,1 mounted ABCD-1234\n')
            self.assertIn('rc=0', out)
            self.assertIn('sm unmount public:179,1', calls)

    def test_card_android_keeps_is_refused(self):
        for shell in self.each_shell():
            out, _ = self.run_fn(shell, f'sd_release {self.dev}', volumes='public:179,1 mounted ABCD-1234\n',
                                 still_mounted=True)
            self.assertIn('rc=1', out)

    def test_adopted_card_is_refused(self):
        # a card adopted as internal storage is encrypted and part of Android's data: never ours to take
        for shell in self.each_shell():
            out, calls = self.run_fn(shell, f'sd_release {self.dev}', volumes='private:179,3 mounted 1234-uuid\n')
            self.assertIn('rc=1', out)
            self.assertNotIn('sm unmount', calls)
```

- [ ] **Step 2: Run** — `python3 -m unittest discover -s tests -p 'test_android_install.py'` → FAIL (no sd block).

- [ ] **Step 3: Implement in `tools/android-install.sh`.** Header comment: add after the `OFF_S SIZE_S` line

```sh
#   SD_MODE=0|1 SD_DEV with SD_MODE=1 the filesystem (label mu300sd) is the SD card block device SD_DEV instead of
#                      that region; OFF/SIZE are then not used
```

and change the `FORMAT` line to `#   FORMAT=0|1         create the ext4 filesystem (mu300root in the region, mu300sd on the card)`.

After `say() { echo "[device] $*"; }` insert:

```sh
# --- sd begin
sd_label() {  # the ext4 label of a device, empty when it is not ext4
    [ "$(dd if="$1" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')" = 53ef ] || return 0
    dd if="$1" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000'
}
sd_release() {  # sd_release DEV: make Android let go of the card; nothing has been written when this fails
    vols=$(sm list-volumes 2>/dev/null)
    # adopted as internal storage: encrypted, and part of Android's data
    case "$vols" in *private:*mounted*) case "$vols" in *"private mounted"*) ;; *)
        say "the SD card is adopted as Android internal storage; format it as portable storage first"; return 1 ;; esac ;; esac
    for v in $(echo "$vols" | sed -n 's/^\(public:[^ ]*\) mounted.*/\1/p'); do
        say "asking Android to unmount $v"
        sm unmount "$v" 2>/dev/null || true
    done
    if sm list-volumes 2>/dev/null | grep -q '^public:[^ ]* mounted'; then
        say "Android keeps the SD card mounted; eject it under Settings > Storage and run the installer again"; return 1
    fi
    for m in $(grep -o "^${1%p[0-9]*}[^ ]*" "${MU300_MOUNTS:-/proc/mounts}" 2>/dev/null); do
        umount "$m" 2>/dev/null || umount -f "$m" 2>/dev/null || true
    done
}
sd_prepare() {  # sd_prepare DEV FORMAT: create mu300sd, or check that it is there
    label=$(sd_label "$1")
    if [ "$2" = 1 ]; then
        [ -z "$label" ] || [ "$label" = mu300sd ] || { say "refusing to format: foreign ext4 ($label) on the SD card"; return 1; }
        say "creating ext4 mu300sd on $1"
        mke2fs -t ext4 -L mu300sd -F "$1" >/dev/null
    elif [ "$label" != mu300sd ]; then
        say "no mu300sd filesystem on $1 (run with FORMAT=1)"; return 1
    fi
}
# --- sd end
```

(The adopted-card test line `private:179,3 mounted 1234-uuid` must make `sd_release` return 1 without calling `sm unmount`, and Android's own `private mounted null` line for built-in storage must not: adjust the `case` to exactly that — match a line that starts with `private:` and has `mounted`.) Simplest correct form, replacing the nested `case`:

```sh
    echo "$vols" | grep -q '^private:[^ ]* mounted' && {
        say "the SD card is adopted as Android internal storage; format it as portable storage first"; return 1; }
```

Then wrap the existing region code: the line `# --- the region must not overlap any partition` through the `trap 'sync; sh $T/android-mount-mu300root.sh -u $M ...' EXIT` line goes into the `else` of

```sh
if [ "${SD_MODE:-0}" = 1 ]; then
    [ -b "${SD_DEV:?SD_DEV is not set}" ] || { say "no block device $SD_DEV (is the SD card inserted?)"; exit 1; }
    sd_release "$SD_DEV" || exit 1
    sd_prepare "$SD_DEV" "$FORMAT" || exit 1
    MU300_SD_DEV=$SD_DEV sh $T/android-mount-mu300root.sh $M
    trap 'sync; sh $T/android-mount-mu300root.sh -u $M >/dev/null 2>&1; true' EXIT
else
    ...existing code, unchanged...
fi
```

In the per-system loop, after `R=$M/$os`:

```sh
    # Ubuntu remounts / by label; on the card that label is mu300sd
    if [ "${SD_MODE:-0}" = 1 ] && [ -f $R/etc/fstab ]; then sed -i 's|^LABEL=mu300root |LABEL=mu300sd |' $R/etc/fstab; fi
```

Before the final `rm -f $T/mu300-install.env`:

```sh
# A device with an internal installation as well: tell its init that the card is what should boot (it then waits
# for the card instead of taking the internal system at once). Best effort - the card boots without it whenever
# the SD host is quick enough.
if [ "${SD_MODE:-0}" = 1 ] && [ -n "${OFF:-}" ] && [ "${INTERNAL_EXISTS:-0}" = 1 ]; then
    I=$T/mu300root-internal
    if MU300_OFF=$OFF MU300_SIZE=$SIZE sh $T/android-mount-mu300root.sh $I >/dev/null 2>&1; then
        mkdir -p $I/.mu300 && : > $I/.mu300/root-on-sd
        sync; sh $T/android-mount-mu300root.sh -u $I >/dev/null 2>&1 || true
        say "marked the internal installation: the SD card boots first"
    fi
fi
```

and document `INTERNAL_EXISTS=0|1` in the header comment (`with SD_MODE=1: an internal mu300root exists at OFF/SIZE and gets the root-on-sd marker`).

- [ ] **Step 4: `tools/android-mount-mu300root.sh`** — after the `-u` branch add:

```sh
# the SD card: a block device of its own, no loop. Same checks as for the region, for the card's label.
if [ -n "${MU300_SD_DEV:-}" ]; then
    grep -q "^$MU300_SD_DEV " /proc/mounts && { echo "ALREADY-MOUNTED $MU300_SD_DEV"; exit 1; }
    magic=$(dd if="$MU300_SD_DEV" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')
    [ "$magic" = 53ef ] || { echo "NO-EXT4 magic=$magic"; exit 1; }
    label=$(dd if="$MU300_SD_DEV" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000')
    [ "$label" = mu300sd ] || { echo "NOT-MU300SD label=$label"; exit 1; }
    mkdir -p "$1"
    mount -t ext4 -o noatime "$MU300_SD_DEV" "$1"
    echo "MOUNTED $MU300_SD_DEV on $1"
    exit 0
fi
```

and make the `-u` branch not `losetup -d` a card: `umount "$2"; case "$L" in */loop*) losetup -d "$L" ;; esac; echo UNMOUNTED; exit 0`. Update the usage comment at the top with the `MU300_SD_DEV` form.

- [ ] **Step 5: Run** — `python3 -m unittest discover -s tests -p 'test_android_install.py'` and `-p 'test_static.py'` → PASS. Add the README row:

```
| `test_android_install.py` | `tools/android-install.sh` on the SD card: a blank card is formatted, a foreign ext4 and an adopted or busy card are refused |
```

- [ ] **Step 6: Commit**

```sh
git add tools/android-install.sh tools/android-mount-mu300root.sh tests/test_android_install.py tests/README.md
git commit -m "android-install: the filesystem on the SD card (SD_MODE), never over a foreign ext4 or a card Android keeps"
```

---

### Task 4: `install.sh` asks where the filesystem goes

**Files:**
- Create: `tools/storage.sh`
- Modify: `install.sh` (source it next to `tools/i18n.sh`; the block from `# ---- free eMMC region` to the `--check` exit; the summary; the env file), `i18n/tr.tsv`, `i18n/zh.tsv`
- Test: `tests/test_installer.py` (new class `Storage`), `tests/test_static.py` (add `tools/storage.sh` to the list of scripts that must parse)

**Interfaces:**
- Consumes: `su_do`, `ask`, `t`, `die`, `gib` from `install.sh`; `SIZE` (free internal bytes).
- Produces (`tools/storage.sh`, sourced):
  - `sd_probe` — sets `SD_DEV` (Android path, e.g. `/dev/block/mmcblk1p1`) and `SD_BYTES`, or both empty when there is no SD card of type `SD`; sets `SD_SMALL=1` when a card is there but under 700 MiB.
  - `sd_existing` — prints `yes` when `SD_DEV` holds `mu300sd`, else `no`.
  - `choose_storage` — sets `SD_MODE` to 0 or 1 from `MU300_STORAGE`, the card and `SIZE`; dies for `MU300_STORAGE=sd` without a usable card.
- Produces in the env file: `SD_MODE`, `SD_DEV`, `INTERNAL_EXISTS`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_installer.py`:

```python
class Storage(ShellTest):
    """tools/storage.sh: internal region or SD card."""
    GIB = 1024 ** 3

    def run_choose(self, shell, card, internal=30 * GIB, answer='', forced=''):
        # card: None, or (device, sectors, has_partition, type)
        (self.tmp / 'sd').write_text('' if card is None else '%s %s %s\n' % (
            card[0] + ('p1' if card[2] else ''), card[1], card[3]))
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; '
                'say() { :; }; die() { echo "DIE $*"; exit 1; }; '
                'gib() { echo "$1"; }; '
                'ask() { printf "ASKED[%s] " "$3"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
                f'su_do() {{ cat "{self.tmp}/sd"; }}; SIZE={internal}; '
                f'{"MU300_STORAGE=" + forced + "; " if forced else "unset MU300_STORAGE; "}'
                '. "$TOP/tools/storage.sh"; sd_probe; choose_storage; '
                'echo "mode=$SD_MODE dev=${SD_DEV:-none}"')
        return self.sh(shell, code, stdin=answer + '\n').stdout

    def test_no_card_never_asks(self):
        for shell in self.each_shell():
            out = self.run_choose(shell, None)
            self.assertNotIn('ASKED', out)
            self.assertIn('mode=0 dev=none', out)

    def test_card_asks_with_internal_as_default(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            out = self.run_choose(shell, card)
            self.assertIn('ASKED[internal]', out)
            self.assertIn('mode=0', out)
            out = self.run_choose(shell, card, answer='sd')
            self.assertIn('mode=1 dev=/dev/block/mmcblk1p1', out)

    def test_small_internal_space_makes_the_card_the_default(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, False, 'SD')
        for shell in self.each_shell():
            out = self.run_choose(shell, card, internal=100 * 1024 ** 2)
            self.assertIn('ASKED[sd]', out)
            self.assertIn('mode=1 dev=/dev/block/mmcblk1', out)

    def test_forced(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            self.assertIn('mode=1', self.run_choose(shell, card, forced='sd'))
            out = self.run_choose(shell, card, forced='internal')
            self.assertNotIn('ASKED', out); self.assertIn('mode=0', out)

    def test_forced_sd_without_card_dies(self):
        for shell in self.each_shell():
            self.assertIn('DIE', self.run_choose(shell, None, forced='sd'))

    def test_small_first_partition_is_too_small(self):
        # 64 MiB first partition in front of a big card: too small, not a reason to take the whole device
        card = ('/dev/block/mmcblk1', 64 * 2048, True, 'SD')
        for shell in self.each_shell():
            out = self.run_choose(shell, card)
            self.assertNotIn('ASKED', out)
            self.assertIn('mode=0', out)

    def test_not_an_sd_card(self):
        for shell in self.each_shell():
            self.assertIn('mode=0 dev=none', self.run_choose(shell, ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'MMC')))

    def test_invalid_answer(self):
        card = ('/dev/block/mmcblk1', 62 * 2 ** 21, True, 'SD')
        for shell in self.each_shell():
            self.assertIn('DIE', self.run_choose(shell, card, answer='usb'))
```

- [ ] **Step 2: Run** — `python3 -m unittest discover -s tests -p 'test_installer.py'` → FAIL (`tools/storage.sh` not found).

- [ ] **Step 3: Create `tools/storage.sh`**

```sh
# Where the Linux filesystem goes: the free eMMC region or the SD card. Sourced by install.sh and uninstall.sh
# (su_do, ask, t, die and gib are theirs).
#   sd_probe         SD_DEV / SD_BYTES of the card in the slot (empty without one), SD_SMALL=1 when it is too small
#   sd_existing      yes when the card already holds a mu300sd filesystem
#   choose_storage   SD_MODE=0|1; MU300_STORAGE=internal|sd answers without asking

SD_MIN=$((700 * 1024 * 1024))

# One line from the device: "<block device> <512-byte sectors> <type>" for the first mmc disk that is not the
# eMMC - its first partition when it has one, the whole card otherwise. The type keeps a second eMMC or an SDIO
# function out.
sd_probe() {
    SD_DEV=; SD_BYTES=0; SD_SMALL=0
    set -- $(su_do 'for b in /sys/block/mmcblk[1-9]; do [ -e $b/device/type ] || continue; n=${b##*/}; if [ -e $b/${n}p1 ]; then echo /dev/block/${n}p1 $(cat $b/${n}p1/size) $(cat $b/device/type); else echo /dev/block/$n $(cat $b/size) $(cat $b/device/type); fi; break; done')
    [ $# -eq 3 ] && [ "$3" = SD ] || return 0
    if [ $(( $2 / 2048 )) -lt $(( SD_MIN / 1048576 )) ]; then SD_SMALL=1; return 0; fi
    SD_DEV=$1; SD_BYTES=$(( $2 * 512 ))
}

sd_existing() {
    m=$(su_do "dd if=$SD_DEV bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1" | tr -d ' ')
    l=$(su_do "dd if=$SD_DEV bs=1 skip=1144 count=16 2>/dev/null" | tr -d '\000')
    if [ "$m" = 53ef ] && [ "$l" = mu300sd ]; then echo yes; else echo no; fi
}

choose_storage() {
    SD_MODE=0
    case ${MU300_STORAGE:-} in
        internal) return 0 ;;
        sd) [ -n "$SD_DEV" ] || die "$(t 'MU300_STORAGE=sd, but there is no usable SD card in the device')"
            SD_MODE=1; return 0 ;;
        '') ;;
        *) die "$(t 'MU300_STORAGE must be internal or sd')" ;;
    esac
    [ -n "$SD_DEV" ] || return 0
    # too little room inside: the card is the way that needs no repartitioning
    _d=internal; [ "$SIZE" -lt "$SD_MIN" ] && _d=sd
    ask _st "$(t 'Where should the Linux filesystem go: internal storage or the SD card ({1}, {2})? (internal/sd)' "$SD_DEV" "$(gib "$SD_BYTES")")" $_d
    case $_st in
        internal) ;;
        sd) SD_MODE=1 ;;
        *) die "$(t 'invalid choice')" ;;
    esac
}
```

- [ ] **Step 4: Run** — the `Storage` tests → PASS. Add `TOP / 'tools' / 'storage.sh'` to the script list at `tests/test_static.py:25`.

- [ ] **Step 5: Wire it into `install.sh`.**

(a) After `. "$TOP/tools/i18n.sh"`: `. "$TOP/tools/storage.sh"`.

(b) After the `echo "$(t 'eMMC: {1} ...` line:

```sh
# the SD card as the other place for it (boot/init looks there first)
sd_probe
[ "$SD_SMALL" = 1 ] && echo "$(t 'SD card present but smaller than 700 MiB; not used')"
if [ $CHECK_ONLY = 1 ]; then
    SD_MODE=0
    [ -n "$SD_DEV" ] && echo "$(t 'SD card: {1}, {2}, existing mu300sd filesystem: {3}' "$SD_DEV" "$(gib "$SD_BYTES")" "$(t "$(sd_existing)")")"
else
    choose_storage
fi
```

(c) Change `if [ $SIZE -lt $((700 * 1024 * 1024)) ]; then offer_repartition` to `if [ $SD_MODE = 0 ] && [ $SIZE -lt $((700 * 1024 * 1024)) ]; then`.

(d) The internal detection loop stays as it is but records what it found for the marker: after the loop add `INTERNAL_EXISTS=0; [ $existing = yes ] && INTERNAL_EXISTS=1`. Then:

```sh
if [ $SD_MODE = 1 ]; then
    # from here on SIZE and existing describe the card; OFF and INT_SIZE keep the internal region for the marker
    INT_SIZE=$SIZE; SIZE=$SD_BYTES; existing=$(sd_existing); DIRTY=0
    echo "$(t 'Linux filesystem: SD card {1}, {2}, existing mu300sd filesystem: {3}' "$SD_DEV" "$(gib $SIZE)" "$(t "$existing")")"
fi
```

and guard the dirty probe with `if [ $SD_MODE = 0 ] && [ $existing = no ]; then`. In the verdict chain add, after the `existing = yes` branch:

```sh
elif [ $SD_MODE = 1 ]; then
    verdict=$(t 'OK: the SD card will be formatted; everything on it is erased')
```

(e) Where `FORMAT` is decided for a fresh filesystem (find the line that sets `FORMAT=1` for `existing = no`), add for the card:

```sh
if [ $SD_MODE = 1 ] && [ $FORMAT = 1 ]; then
    ask erase "$(t 'Everything on the SD card ({1}, {2}) will be erased. Type ERASE to continue' "$SD_DEV" "$(gib $SIZE)")" no
    [ "$erase" = ERASE ] || die "$(t 'cancelled')"
fi
```

(f) Boot image: `sed "s/^ROOT_OFFSET=[0-9]*/ROOT_OFFSET=$OFF/"` stays (the internal offset is still the right fallback).

(g) Summary: replace the `filesystem:` and `writes:` lines with

```sh
echo "  $(t 'filesystem:     {1}' "$([ $FORMAT = 0 ] && t 'keep existing' || { [ $SD_MODE = 1 ] && t 'CREATE new ext4 (erases the SD card)' || t 'CREATE new ext4 (erases the Linux region)'; })")"
```
```sh
if [ $SD_MODE = 1 ]; then
    echo "  $(t 'writes:         SD card {1}, boot_b, 32 bytes of misc (the eMMC region, boot_a, GPT and userdata are not touched)' "$SD_DEV")"
else
    echo "  $(t 'writes:         Linux region at offset {1}, boot_b, 32 bytes of misc (boot_a, GPT and userdata are not touched)' "$OFF")"
fi
```

(h) Env file: add `SD_MODE=%s\nSD_DEV=%s\nINTERNAL_EXISTS=%s\n` with `"$SD_MODE" "$SD_DEV" "$INTERNAL_EXISTS"`, and when `SD_MODE=1` pass the internal size, not the card's, as `SIZE`/`SIZE_S` (`${INT_SIZE:-$SIZE}`), because the marker mount uses them.

(i) A card installation with the 5.4 kernel: only if Task 1 Step 9 found no `mmcblk1` under 5.4, add after the kernel choice

```sh
[ $SD_MODE = 1 ] && [ "$KERNEL" = 5.4 ] && die "$(t 'the 5.4 kernel cannot read the SD card; choose 6.18 or 7.2 for an SD card installation')"
```

- [ ] **Step 6: Translations** — add every new message (the eleven strings above) to `i18n/tr.tsv` and `i18n/zh.tsv`, e.g. in `tr.tsv`:

```
Where should the Linux filesystem go: internal storage or the SD card ({1}, {2})? (internal/sd)	Linux dosya sistemi nereye kurulsun: dahili depolama mı, SD kart mı ({1}, {2})? (internal/sd)
Everything on the SD card ({1}, {2}) will be erased. Type ERASE to continue	SD karttaki ({1}, {2}) her şey silinecek. Devam etmek için ERASE yazın
OK: the SD card will be formatted; everything on it is erased	TAMAM: SD kart biçimlendirilecek; üzerindeki her şey silinir
CREATE new ext4 (erases the SD card)	Yeni ext4 OLUŞTUR (SD kartı siler)
```

Run `python3 tools/check-i18n.py` → nothing missing, no placeholder mismatch, in both languages.

- [ ] **Step 7: Run everything** — `python3 -m unittest discover -s tests` → PASS.

- [ ] **Step 8: Commit**

```sh
git add tools/storage.sh install.sh i18n tests
git commit -m "install.sh: the Linux filesystem on the SD card when one is in the slot (MU300_STORAGE=internal|sd)"
```

---

### Task 5: `install.ps1`

**Files:**
- Modify: `install.ps1` (after the `Gib` function at `:427`; the 700 MB check at `:437`; the region detection at `:442-451`; summary at `:707-709`; env lines at `:722`)
- Test: `tests/installer.Tests.ps1`

**Interfaces:**
- Produces: `function ChooseStorage([string]$SdDev, [int64]$SdBytes, [int64]$InternalBytes, [string]$Forced, [string]$Answer)` returning `'internal'` or `'sd'`, throwing on an invalid answer or `sd` without a card. Pure: the caller does the asking and passes the answer (empty = default).

- [ ] **Step 1: Failing checks** — in `tests/installer.Tests.ps1` add `'ChooseStorage'` to `$want` and append before the summary:

```powershell
# ---- ChooseStorage: internal region or SD card -------------------------------------------------------------------
Check 'no card'            (ChooseStorage '' 0 30GB '' '') 'internal'
Check 'card, default'      (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' '') 'internal'
Check 'card, answer sd'    (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' 'sd') 'sd'
Check 'small internal'     (ChooseStorage '/dev/block/mmcblk1p1' 62GB 100MB '' '') 'sd'
Check 'forced internal'    (ChooseStorage '/dev/block/mmcblk1p1' 62GB 100MB 'internal' '') 'internal'
Check 'forced sd'          (ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB 'sd' '') 'sd'
$threw = $false; try { ChooseStorage '' 0 30GB 'sd' '' | Out-Null } catch { $threw = $true }
Check 'forced sd, no card' $threw $true
$threw = $false; try { ChooseStorage '/dev/block/mmcblk1p1' 62GB 30GB '' 'usb' | Out-Null } catch { $threw = $true }
Check 'invalid answer'     $threw $true
```

- [ ] **Step 2: Run** — `pwsh -NoProfile -File tests/installer.Tests.ps1` (on the Windows test machine: `powershell -NoProfile -ExecutionPolicy Bypass -File tests\installer.Tests.ps1`) → FAIL.

- [ ] **Step 3: Implement** — after `function Gib`:

```powershell
# Where the Linux filesystem goes. The caller asks; an empty answer is the default: internal storage, the card
# when there is too little room inside (the way that needs no repartitioning).
function ChooseStorage([string]$SdDev, [int64]$SdBytes, [int64]$InternalBytes, [string]$Forced, [string]$Answer) {
    if ($Forced -eq 'internal') { return 'internal' }
    if ($Forced -eq 'sd') { if (-not $SdDev) { throw 'MU300_STORAGE=sd, but there is no usable SD card in the device' }; return 'sd' }
    if ($Forced) { throw 'MU300_STORAGE must be internal or sd' }
    if (-not $SdDev) { return 'internal' }
    if (-not $Answer) { if ($InternalBytes -lt 700MB) { return 'sd' } else { return 'internal' } }
    if ($Answer -eq 'internal' -or $Answer -eq 'sd') { return $Answer }
    throw 'invalid choice'
}
```

and at the call site, after the `eMMC:` line (device command in single quotes only, as the static test demands):

```powershell
$SD_DEV = ''; [int64]$SD_BYTES = 0
$sd = (SuDo 'for b in /sys/block/mmcblk[1-9]; do [ -e $b/device/type ] || continue; n=${b##*/}; if [ -e $b/${n}p1 ]; then echo /dev/block/${n}p1 $(cat $b/${n}p1/size) $(cat $b/device/type); else echo /dev/block/$n $(cat $b/size) $(cat $b/device/type); fi; break; done').Trim() -split '\s+'
if ($sd.Count -eq 3 -and $sd[2] -eq 'SD') {
    if ([int64]$sd[1] * 512 -ge 700MB) { $SD_DEV = $sd[0]; $SD_BYTES = [int64]$sd[1] * 512 }
    else { Write-Host (T 'SD card present but smaller than 700 MiB; not used') }
}
$SD_MODE = 0
if (-not $Check) {
    $ans = ''
    if ($SD_DEV -and -not $env:MU300_STORAGE) {
        $def = $(if ($SIZE -lt 700MB) { 'sd' } else { 'internal' })
        $ans = Ask (T 'Where should the Linux filesystem go: internal storage or the SD card ({1}, {2})? (internal/sd)' $SD_DEV (Gib $SD_BYTES)) $def
    }
    try { if ((ChooseStorage $SD_DEV $SD_BYTES $SIZE ([string]$env:MU300_STORAGE) $ans) -eq 'sd') { $SD_MODE = 1 } }
    catch { Die (T $_.Exception.Message) }
} elseif ($SD_DEV) {
    Write-Host (T 'SD card: {1}, {2}, existing mu300sd filesystem: {3}' $SD_DEV (Gib $SD_BYTES) (T (SdExisting)))
}
```

with

```powershell
function SdExisting {
    $m = (SuDo "dd if=$SD_DEV bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1") -replace '\s', ''
    $l = (SuDo "dd if=$SD_DEV bs=1 skip=1144 count=16 2>/dev/null") -replace '\0', ''
    if ($m -eq '53ef' -and $l.Trim() -eq 'mu300sd') { 'yes' } else { 'no' }
}
```

Then mirror Task 4 Step 5 (c)–(h) one for one: `if ($SD_MODE -eq 0 -and $SIZE -lt 700MB) { ... Die ... }`; `$INTERNAL_EXISTS`; `$INT_SIZE = $SIZE; $SIZE = $SD_BYTES; $existing = SdExisting`; skip the dirty probe in SD mode; the `ERASE` confirmation; the two summary lines; `"SD_MODE=$SD_MODE", "SD_DEV=$SD_DEV", "INTERNAL_EXISTS=$INTERNAL_EXISTS"` in `$lines`, with `SIZE`/`SIZE_S` from `$INT_SIZE` in SD mode. The strings are the same English keys as in `install.sh`, so no new translation lines.

- [ ] **Step 4: Run** — the PowerShell tests, `python3 -m unittest discover -s tests -p 'test_static.py'` (ASCII only, no double quotes in device commands) and `python3 tools/check-i18n.py` → PASS.

- [ ] **Step 5: Commit**

```sh
git add install.ps1 tests/installer.Tests.ps1
git commit -m "install.ps1: the SD card as the place for the Linux filesystem, as in install.sh"
```

---

### Task 6: Uninstallers and the helper tools

**Files:**
- Modify: `uninstall.sh:33-75,108-140`, `uninstall.ps1:150-165` and its erase section, `tools/reset-password.sh:28-47`, `tools/android-import-hotspot.sh`
- Test: `tests/test_static.py` (rule), device run in Task 8

**Interfaces:**
- Consumes: `sd_probe`, `sd_existing` from `tools/storage.sh`; `MU300_SD_DEV` of `android-mount-mu300root.sh`.

- [ ] **Step 1: Failing rule** — in `tests/test_static.py` class `Rules`:

```python
    def test_every_tool_that_mounts_the_filesystem_knows_the_card(self):
        for f in ('uninstall.sh', 'uninstall.ps1', 'tools/reset-password.sh', 'tools/android-import-hotspot.sh'):
            self.assertIn('mu300sd', (TOP / f).read_text(), f)
```

Run → FAIL.

- [ ] **Step 2: `uninstall.sh`** — source `tools/storage.sh` after its helpers are defined (it has `su_do`, `ask`, `die`; give it `t() { printf '%s' "$1"; }` and `gib() { awk -v b="$1" 'BEGIN { printf "%.1f GiB", b / 1073741824 }'; }` if it lacks them). After the `mu300root` search:

```sh
sd_probe; SD_HAS=no
[ -n "$SD_DEV" ] && SD_HAS=$(sd_existing)
[ "$SD_HAS" = yes ] && echo "Linux filesystem on the SD card: $SD_DEV, $(gib "$SD_BYTES")"
```

In "What should be removed?":

```sh
sdwipe=keep
if [ "$SD_HAS" = yes ]; then
    echo "  The SD card holds a Linux installation too:"
    echo "  erase   remove the filesystem from the card (its first 64 MiB are overwritten)"
    echo "  keep    leave the card as it is (it does not boot once boot_b is restored)"
    ask sdwipe "Linux filesystem on the SD card: erase / keep" erase
    case $sdwipe in erase|keep) ;; *) die "invalid choice" ;; esac
fi
```

a summary line `echo "  SD card:  $([ $sdwipe = erase ] && echo "filesystem erased ($SD_DEV)" || echo "$([ "$SD_HAS" = yes ] && echo kept || echo "no installation")")"`, and after the internal erase:

```sh
if [ $sdwipe = erase ]; then
    say "Erasing the Linux filesystem on the SD card"
    su_do "for m in \$(grep -o '^${SD_DEV%p[0-9]*}[^ ]*' /proc/mounts); do umount \$m 2>/dev/null; done; dd if=/dev/zero of=$SD_DEV bs=1048576 count=64 conv=notrunc 2>/dev/null; sync"
    [ "$(sd_existing)" = no ] || die "the SD card still shows a mu300sd filesystem"
fi
# an internal installation that stays must not wait for a card that no longer boots
if [ -n "$OFF" ] && [ $wipe = keep ]; then
    adb push "$TOP/tools/android-mount-mu300root.sh" $T/ >/dev/null
    su_do "MU300_OFF=$OFF MU300_SIZE=$SIZE sh $T/android-mount-mu300root.sh $T/mu300root >/dev/null && rm -f $T/mu300root/.mu300/root-on-sd; sync; sh $T/android-mount-mu300root.sh -u $T/mu300root >/dev/null" >/dev/null 2>&1 || true
fi
```

- [ ] **Step 3: `uninstall.ps1`** — the same three pieces in PowerShell (probe with the single-quoted device command from Task 5, `Ask` for erase/keep, the `dd` of 64 MiB and the check; no double quotes inside device commands).

- [ ] **Step 4: `tools/reset-password.sh`** — before the eMMC search:

```sh
# an installation on the SD card is the one that boots, so it is the one whose password is reset
. "$TOP/tools/storage.sh"; sd_probe
MOUNTENV=
if [ -n "$SD_DEV" ] && [ "$(sd_existing)" = yes ]; then MOUNTENV="MU300_SD_DEV=$SD_DEV"; OFF=sd; fi
```

skip the eMMC search when `MOUNTENV` is set, and mount with `su_do "${MOUNTENV:-MU300_OFF=$OFF MU300_SIZE=$SIZE} sh $T/android-mount-mu300root.sh $T/mu300root"`. (Give the script `t`/`gib` shims as in Step 2 if needed.) `tools/android-import-hotspot.sh`: accept `MU300_SD_DEV` from its caller's environment and pass it through to the mount helper; add a comment line naming `mu300sd`.

- [ ] **Step 5: Run** — `python3 -m unittest discover -s tests` → PASS.

- [ ] **Step 6: Commit**

```sh
git add uninstall.sh uninstall.ps1 tools/reset-password.sh tools/android-import-hotspot.sh tests/test_static.py
git commit -m "uninstall, reset-password, import-hotspot: an installation on the SD card is found and handled too"
```

---

### Task 7: `mu300-update` keeps a card installation bootable

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-update` (`bundle_runs_here` at `:100`)
- Test: `tests/test_update.py`

**Interfaces:**
- Consumes: `features` in the kernel bundle (Task 1); `/run/mu300-root-dev` (Task 2).
- Produces: `root_on_sd` (status 0 when `${MU300_ROOT_DEV_FILE:-/run/mu300-root-dev}` names a device other than `mmcblk0@...`), `bundle_has_sd DIR` (status 0 when `DIR/features` has the line `sdcard`).

- [ ] **Step 1: Failing tests** — in `tests/test_update.py`, following how that file sources the script (`MU300_LIB=1`), add:

```python
    def test_card_installation_needs_an_sd_capable_kernel(self):
        bundle = self.tmp / 'bundle'; bundle.mkdir()
        rootdev = self.tmp / 'root-dev'
        for shell in self.each_shell():
            def run(dev, features):
                rootdev.write_text(dev + '\n')
                (bundle / 'features').write_text(features)
                code = (f'MU300_LIB=1 . "{UPDATE}"; MU300_ROOT_DEV_FILE="{rootdev}"; '
                        f'if root_on_sd && ! bundle_has_sd "{bundle}"; then echo REFUSE; else echo OK; fi')
                return self.sh(shell, code).stdout.strip()
            self.assertEqual(run('/dev/mmcblk1p1', ''), 'REFUSE')
            self.assertEqual(run('/dev/mmcblk1p1', 'sdcard\n'), 'OK')
            self.assertEqual(run('mmcblk0@27762098176', ''), 'OK')
```

(`UPDATE` is the path constant that file already uses for the script; use its name.) Run → FAIL.

- [ ] **Step 2: Implement** — above `bundle_runs_here`:

```sh
# A system on the SD card boots only with a kernel that has the SD host (the bundle says so in "features").
# Installing an older bundle there would leave a device that cannot find its own root filesystem.
root_on_sd() { d=$(cat "${MU300_ROOT_DEV_FILE:-/run/mu300-root-dev}" 2>/dev/null); [ -n "$d" ] && case $d in mmcblk0@*) return 1 ;; esac; }
bundle_has_sd() { grep -qx sdcard "$1/features" 2>/dev/null; }
```

and in `boot_update`, right after the existing `bundle_runs_here` check:

```sh
    if root_on_sd && ! bundle_has_sd "$B"; then
        die "this kernel bundle cannot read the SD card, and this system runs from it; nothing was changed"
    fi
```

(`$B` = the variable `boot_update` uses for the unpacked bundle directory.) The 5.4 bundle: if Task 1 Step 9 found the SD host under 5.4, `tools/make-release.sh` writes the same `features` file into `mu300-kernel.tar.gz`; if not, it does not, and the refusal above is the correct behaviour.

- [ ] **Step 3: Run** — `python3 -m unittest discover -s tests -p 'test_update.py'` → PASS.

- [ ] **Step 4: Commit**

```sh
git add rootfs/overlay/opt/mu300/bin/mu300-update tests/test_update.py tools/make-release.sh
git commit -m "mu300-update: a system on the SD card never gets a kernel that cannot read the card"
```

---

### Task 8: On the device, and the documentation

**Files:**
- Modify: `README.md` (installation section, the "what it writes" list), `docs/FINDINGS.md` (the section from Task 1), `android/magisk/README.md` (no change expected; read it for statements about "the region")

Device: F50 test board, adb serial `324950664950`, card inserted and expendable. Build mode, so the branch's own files are used: `./install.sh --build` needs `work/` and the kernel outputs as for any local build; with `MU300_STORAGE=sd`.

- [ ] **Step 1: Fresh install to the card** — from Android: `MU300_STORAGE=sd ./install.sh --build`, choose both systems, kernel 6.18. Expected on the summary: `writes: SD card /dev/block/mmcblk1p1, boot_b, 32 bytes of misc`. After the reboot: `cat /run/mu300-root-dev` → `/dev/mmcblk1p1`; `grep mu300-disk /proc/mounts` → the card; mobile data and the hotspot up.
- [ ] **Step 2: Both systems** — `mu300-os ubuntu`, reboot, `findmnt /` shows the card; `mu300-os openwrt`, reboot.
- [ ] **Step 3: Marker and fallback** — the internal installation exists on this board, so `/.mu300/root-on-sd` must be in it (check from Android with the mount helper). Ask the user to pull the card; reboot: the internal system boots after the 8 s wait, `tools/collect-logs.sh` shows `stage=sd-root-missing`. Card back in, reboot: the card system boots.
- [ ] **Step 4: Update** — on the card system: `MU300_KERNEL_BUNDLE=<an old 6.18 bundle without features> mu300-update boot` → refused with the Task 7 message; with the new bundle → installs, reboots, still on the card.
- [ ] **Step 5: Reboot loop** — 20 reboots of OpenWrt on the card (`for i in $(seq 20)`: reboot, wait for SSH, record `cat /run/mu300-root-dev` and `dmesg | grep -ciE "mmc1.*(error|timeout|crc)"`). Expected: 20 times the card, 0 errors. A boot that lands on the internal system means the polled host was slower than init's scan: record it and raise it with the user before changing any constant.
- [ ] **Step 6: Uninstall** — `./uninstall.sh`, erase the card, keep the internal system; `./install.sh --check` afterwards reports the card without `mu300sd` and the marker gone.
- [ ] **Step 7: `README.md`** — in the installation section, a paragraph:

```markdown
**On the SD card.** With a card of at least 700 MiB in the slot the installer asks whether the Linux filesystem
goes there instead of into the free eMMC space (`MU300_STORAGE=sd` answers it). The card is formatted (ext4,
label `mu300sd`); on the eMMC only `boot_b` and 32 bytes of `misc` are written, so a device with a small eMMC
needs no repartitioning. Without the card the device starts the internal installation if there is one, Android
otherwise. It needs kernel 6.18 or 7.2.
```

(the last sentence only if Task 1 Step 9 says so), and the measured numbers into `docs/FINDINGS.md`.
- [ ] **Step 8: Full test run and commit**

```sh
python3 -m unittest discover -s tests && python3 tools/check-i18n.py
git add README.md docs/FINDINGS.md
git commit -m "README, FINDINGS: installing to the SD card, and what was measured"
```

The U30 Air: when that board is connected, `adb shell su -c 'ls /sys/class/mmc_host; ls /sys/block'` from Android tells whether it has the slot; without one `sd_probe` finds nothing and no question appears. Recorded in FINDINGS either way.
