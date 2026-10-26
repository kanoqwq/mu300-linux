# OpenWrt with the MU300 Control Panel (`openwrt-luci`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A third installable system, `/openwrt-luci` (OpenWrt 25.12.5 with kanoqwq's LuCI control panel, complete in English/Turkish/Chinese), built from this repository, released as `mu300-openwrt-luci-rootfs.tar.gz` - and the fork's fixes to the shared scripts ported onto all three systems.

**Architecture:** `openwrt/build-rootfs.sh` builds plain OpenWrt as today and, with `MU300_SYSTEM=openwrt-luci`, layers `openwrt/luci-overlay/` and `openwrt/luci-app-mu300/` on top (Aurora pinned by SHA256, catalogs compiled by `tools/po2lmo.py`). Every script that knows system names learns one rule: `openwrt` or `openwrt-*` is OpenWrt-like. The fork's changes are ported row by row from the classification table of the spec (K-ids), never merged.

**Tech Stack:** POSIX sh (dash, bash, busybox ash, Android mksh), procd/netifd/uci (OpenWrt 25.12), LuCI JS views + rpcd, PowerShell 5.1/7, Python 3 `unittest` (standard library), Node.js for the JS smoke tests (skipped without it), Docker arm64 for the image.

**Spec:** `docs/superpowers/specs/2026-10-05-openwrt-luci-system-design.md` (decisions D1-D14, rows K1-K83)

## Global Constraints

- System name `openwrt-luci`, directory `/openwrt-luci`, asset `mu300-openwrt-luci-rootfs.tar.gz`. Kind rule: `openwrt` or `openwrt-*` is OpenWrt-like (`case $os in openwrt|openwrt-*)`). Never a glob over the disk where a system is chosen to boot.
- Fork source: `kanoqwq/clean-tf-7.2` (head `35a1c55`), read with `git show kanoqwq/clean-tf-7.2:<path>` and `git diff 1a69a41 kanoqwq/clean-tf-7.2 -- <path>`. In zsh, write `"${R}:path"`, not `$R:path` (`:r` is a modifier). Port onto our file; never `git merge`, `git cherry-pick` or `git checkout kanoqwq/... -- path` (except the verbatim snapshot in Task 9). Each port commit names the K-id(s) and the fork commit in its body.
- The safety timer in `boot/init` stays `sleep 300`. No image ships an empty root password. `COUNTRY` default stays `TR`.
- Slot code (`linux_slot`, `restore_slot_a`, `boot_b` names) is not touched: the Magisk branch owns it (D14).
- Shared scripts and the app's sources contain no CJK characters except `tools/i18n.sh`, `i18n/*.tsv` and `openwrt/luci-app-mu300/po/zh_Hans/` (enforced from Task 12 on). Comments and logs in English.
- The app has no `msgid_plural` and no `msgctxt` (`tools/po2lmo.py` refuses them).
- Every new `t '...'`/`T '...'` message gets a line in `i18n/tr.tsv` and `i18n/zh.tsv`; `python3 tools/check-i18n.py` prints nothing missing. `install.ps1` stays ASCII with no double quotes in device commands.
- Scripts parse under dash, bash and busybox ash (`tests/test_static.py`); device programs are executable (git mode 100755).
- A row with a gate is ported behind its measurement: if the gate fails, revert the port commit, record the numbers in `docs/FINDINGS.md`, and change the row's class to `c` in the spec in the same commit.
- Commit messages: one sentence in the repository's style (no `feat:` prefix), ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01DqKKtBQZCfxYnHuBCuDqqn`.
- Nothing is released from this branch: no `make-release --publish`, no `gh release`, no push to `main`.
- Devices (see Task 41/42 for the full campaign): F50 `root@192.168.77.1` / `ubuntu@192.168.77.1`, U30 Air `.78.1`, password `ubuntu`, `tools/mu300-ssh.py` with `MU300_HOST`/`MU300_PASS`; F50 power cycle `uhubctl -l 0-1.3 -p 3 -a cycle`; serial `tools/mu300-serial.py` when USB networking is gone; Windows host `ssh kaan@192.168.2.20`. macOS does not bring up a new USB interface while the screen is locked: test macOS host behaviour only with a MAC the Mac already knows.

## Review Focus

- `openwrt-luci.old` (a kept rollback copy) next to `openwrt-luci`: init must never boot it, `mu300-update clean` must remove it. Pinned in Task 1 (`test_never_boots_a_kept_copy`) and Task 2 (`test_clean_knows_the_third_system`).
- `mu300-update` running on `openwrt-luci` must report `running now openwrt-luci`, not `openwrt` (`/etc/openwrt_release` exists on both). Pinned in Task 2 (`test_running_os_by_directory`).
- A Turkish browser on any panel page, including error toasts from the backend: no Chinese, no English fallback for a string that has a Turkish translation. Pinned in Task 12 (rules) and checked by hand in Task 41.
- A browser in a language the app does not have (German): English, never Chinese. Pinned in Task 13 (`test_unknown_language_is_english`).
- The early DHCP lease on a device with a custom LAN address: the host must end up on the real subnet within two minutes. Pinned in Task 25 (`test_preinit_uses_the_configured_lan`) and measured in Task 41.
- `mobile-data` on Ubuntu with an IPv6-capable SIM still runs `v6_up` (K63 rejected). Pinned in Task 21 (`test_systemd_path_keeps_v6_up`).

---

## Phase 1 - the third system's name

### Task 1: `boot/init` boots `/openwrt-luci`

**Files:**
- Modify: `boot/init` (`pick_root`, around line 335)
- Test: `tests/test_boot_init.py` (new class `PickRoot`)

**Interfaces:**
- Produces: `pick_root` prints `/disk/<name>` for the first of `$(cat /disk/.mu300/boot-os) ubuntu openwrt openwrt-luci` that is a directory with an init; the legacy root-level Ubuntu as before. Wrapped in marker lines `# --- pick-root begin` / `# --- pick-root end` so tests can cut it out.

- [ ] **Step 1: Write the failing tests**

```python
class PickRoot(ShellTest):
    def functions(self):
        m = re.search(r'# --- pick-root begin\n(.*?)# --- pick-root end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no pick-root block')
        return m.group(1).replace('/disk', str(self.tmp / 'disk'))

    def system(self, name, init='sbin/init'):
        p = self.tmp / 'disk' / name / init
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#!/bin/sh\n'); p.chmod(0o755)

    def pick(self, shell):
        return self.sh(shell, self.functions() + '\npick_root').stdout.strip()

    def test_only_the_third_system(self):
        self.system('openwrt-luci')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), f'{self.tmp}/disk/openwrt-luci')

    def test_boot_os_chooses_it(self):
        self.system('ubuntu', 'lib/systemd/systemd'); self.system('openwrt'); self.system('openwrt-luci')
        (self.tmp / 'disk/.mu300').mkdir(); (self.tmp / 'disk/.mu300/boot-os').write_text('openwrt-luci\n')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), f'{self.tmp}/disk/openwrt-luci')

    def test_never_boots_a_kept_copy(self):
        self.system('openwrt-luci.old')
        (self.tmp / 'disk/.mu300').mkdir(); (self.tmp / 'disk/.mu300/boot-os').write_text('openwrt-luci.old\n')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), '')
```

(`boot-os` naming `*.old` is refused because `pick_root` now rejects names with a dot; see Step 3.)

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -k PickRoot` → FAIL (no pick-root block).

- [ ] **Step 3: Implement** — replace `pick_root` with:

```sh
# --- pick-root begin
# The ext4 area holds one or more systems: /ubuntu, /openwrt, /openwrt-luci (selected by /.mu300/boot-os), or a
# single Ubuntu directly in its root (first installs). Explicit names only: a kept copy (openwrt.old, ubuntu.broken)
# must never be what boots, so a boot-os with a dot in it is ignored.
has_init() { [ -x "$1/lib/systemd/systemd" ] || [ -x "$1/sbin/init" ]; }
pick_root() {
    want=$(cat /disk/.mu300/boot-os 2>/dev/null)
    case $want in *.*|*/*) want= ;; esac
    for os in $want ubuntu openwrt openwrt-luci; do
        [ -d "/disk/$os" ] && has_init "/disk/$os" && { echo "/disk/$os"; return; }
        # legacy layout: Ubuntu in the filesystem root
        [ "$os" = ubuntu ] && [ -x /disk/lib/systemd/systemd ] && { echo /disk; return; }
    done
}
# --- pick-root end
```

- [ ] **Step 4: Run** the tests → PASS; `python3 -m unittest discover -s tests` → no new failures.
- [ ] **Step 5: Commit** — `git commit -m "init: /openwrt-luci boots like the other two, and a kept copy never does"`

---

### Task 2: `mu300-update` by kind

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-update` (`running_os`, `installed_systems`, `mounted_disk`, `keep_list`, `vendor_list`, `apply`'s name check, `clean`, usage text)
- Test: `tests/test_update.py`

**Interfaces:**
- Produces: `SYSTEMS="ubuntu openwrt openwrt-luci"` (one variable, used by every loop); `os_kind NAME` prints `ubuntu` or `openwrt` (status 1 for anything else); `running_os` prints the name of the directory under `$DISK` that is `/` (`[ / -ef "$DISK/$n" ]`), falling back to the old `/etc/openwrt_release` test. `MU300_ROOT` (default `/`) stands in for `/` in tests.

- [ ] **Step 1: Failing tests** in class `Update`:

```python
    def test_os_kind(self):
        for shell in self.each_shell():
            for name, kind in [('ubuntu', 'ubuntu'), ('openwrt', 'openwrt'), ('openwrt-luci', 'openwrt')]:
                self.assertEqual(self.up(shell, f'os_kind {name}').stdout.strip(), kind)
            self.assertEqual(self.up(shell, 'os_kind arch').returncode, 1)

    def test_installed_systems_three(self):
        for name in ('openwrt', 'openwrt-luci', 'openwrt-luci.old'):
            (self.disk / name).mkdir()
        for shell in self.each_shell():
            self.assertEqual(self.up(shell, 'installed_systems').stdout.split(), ['ubuntu', 'openwrt', 'openwrt-luci'])

    def test_lists_by_kind(self):
        for shell in self.each_shell():
            self.assertEqual(self.up(shell, 'keep_list openwrt-luci').stdout, self.up(shell, 'keep_list openwrt').stdout)
            self.assertEqual(self.up(shell, 'vendor_list openwrt-luci').stdout, self.up(shell, 'vendor_list openwrt').stdout)
            self.assertEqual(self.up(shell, 'rootfs_asset openwrt-luci').stdout.strip(), 'mu300-openwrt-luci-rootfs.tar.gz')

    def test_running_os_by_directory(self):
        (self.disk / 'openwrt-luci' / 'etc').mkdir(parents=True)
        (self.disk / 'openwrt-luci' / 'etc' / 'openwrt_release').write_text('x')
        for shell in self.each_shell():
            r = self.up(shell, 'running_os', MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.stdout.strip(), 'openwrt-luci')

    def test_clean_knows_the_third_system(self):
        (self.disk / 'openwrt-luci.old').mkdir()
        for shell in self.each_shell():
            self.up(shell, 'STAGE=$MU300_DISK/.stage; df() { :; }; clean')
            self.assertFalse((self.disk / 'openwrt-luci.old').exists())
            (self.disk / 'openwrt-luci.old').mkdir()
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -k test_update` → the five FAIL.
- [ ] **Step 3: Implement.**
  - after `VERSION_FILE`/`DISK` definitions: `SYSTEMS="ubuntu openwrt openwrt-luci"` and
    ```sh
    # a system's kind decides which files are its settings and how its accounts look (spec D2)
    os_kind() { case $1 in ubuntu) echo ubuntu ;; openwrt|openwrt-*) echo openwrt ;; *) return 1 ;; esac; }
    ```
  - `keep_list`/`vendor_list`: `case $(os_kind "$1") in`.
  - `running_os`:
    ```sh
    # the directory this system booted from; /etc/openwrt_release alone cannot tell the two OpenWrt systems apart
    running_os() {
        for n in $SYSTEMS; do [ -d "$DISK/$n" ] && [ "${MU300_ROOT:-/}" -ef "$DISK/$n" ] && { echo "$n"; return; }; done
        [ -e /etc/openwrt_release ] && echo openwrt || echo ubuntu
    }
    ```
  - `installed_systems`, `clean`: `for os in $SYSTEMS`. `mounted_disk`: true when any of `$SYSTEMS` is a directory.
  - `apply`: `case $os in ubuntu|openwrt|openwrt-luci) ;; *) die ...`. The `if [ "$os" = ubuntu ]` branches stay (the else branch is the OpenWrt path).
  - usage line and `-h` text: `apply [ubuntu|openwrt|openwrt-luci ...]`.
- [ ] **Step 4: Run** the whole suite → PASS.
- [ ] **Step 5: Commit** — `git commit -m "mu300-update: openwrt-luci is updated, rolled back and cleaned like OpenWrt, and knows when it is the one running"`

---

### Task 3: `mu300-os` with three systems (test only)

**Files:** Test: `tests/test_device_scripts.py` (new class `Os`)

- [ ] **Step 1: Tests** — a fake disk at `$tmp/disk` mounted as far as `mu300-os` knows (stub `mountpoint` that succeeds for `/mnt/mu300-disk`; run with `MU300_DISK` if the script has no such variable - it does not: add `DISK=${MU300_DISK:-/mnt/mu300-disk}` to the `mountpoint -q` branch so the test can point it elsewhere). Cases: `mu300-os` lists `openwrt-luci: installed`; `mu300-os openwrt-luci` writes `boot-os` and carries `default-boot`; `mu300-os openwrt-luci.old` fails.
- [ ] **Step 2: Run** → the listing passes once `MU300_DISK` exists; `openwrt-luci.old` is excluded by the existing `*.old` case.
- [ ] **Step 3: Commit** — `git commit -m "mu300-os: tested with a third system, and the disk can be a test directory"`

---

### Task 4: `tools/android-install.sh` by kind

**Files:**
- Modify: `tools/android-install.sh` (header comment, legacy wipe `case`, `keep=`, enabled-service copy, password `sed`, final `ls -d`)
- Test: `tests/test_android_install.py`

**Interfaces:**
- Consumes: `OSES` may contain `openwrt-luci`; `/data/local/tmp/mu300-openwrt-luci.tar.gz` and `mu300-vendor-openwrt-luci.tar.gz`.
- Produces: the same handling as `openwrt` (mksh-compatible `case`, no new arithmetic).

- [ ] **Step 1: Failing tests** — reuse the existing harness that runs the script with a fake `$T` (read the file's current helpers first); add `test_installs_openwrt_luci` (`OSES=openwrt-luci`: the tarball unpacks to `$M/openwrt-luci`, `root`'s hash in `etc/shadow` is `PWHASH`, `boot-os` written) and `test_update_keeps_openwrt_luci_config` (`UPDATE=1`, an old `etc/config/network` survives), and `test_legacy_wipe_keeps_openwrt_luci` (`WIPE_LEGACY=1` with a root-level Ubuntu and an `openwrt-luci` dir: the dir stays).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — `case "${e##*/}" in lost+found|.mu300|ubuntu|openwrt|openwrt-*) ;;`; every `case $os in ... openwrt)` becomes `openwrt|openwrt-*)`; the final line lists `$M/ubuntu $M/openwrt $M/openwrt-luci`; header: `OSES="ubuntu openwrt openwrt-luci"`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "android-install: openwrt-luci is installed, updated and given its password like OpenWrt"`

---

### Task 5: `vendor-overlay.py`, `reset-password.sh`, the uninstallers

**Files:**
- Modify: `tools/vendor-overlay.py:135` (`--os` choices), `tools/reset-password.sh` (usage, `case`, `systems`, `user`)
- Test: `tests/test_static.py` (`test_every_system_list_has_openwrt_luci`), `tests/test_installer.py` or a new small test for `reset-password.sh` if its functions can be sourced (read it; if not, a static check)

- [ ] **Step 1: Test** — a static rule: in each of `boot/init`, `mu300-update`, `mu300-os`, `tools/android-install.sh`, `tools/reset-password.sh`, `tools/vendor-overlay.py`, `install.sh`, `install.ps1`, `tools/make-release.sh`, the string `openwrt-luci` (or the kind pattern `openwrt-*` / `openwrt|openwrt-`) occurs. And: `uninstall.sh`/`uninstall.ps1` contain neither `ubuntu)` nor `openwrt)` case arms (they remove the whole filesystem; a test that fails if someone later adds a per-system list without the third name).
- [ ] **Step 2: Implement** — `vendor-overlay.py`: `choices=['ubuntu', 'openwrt', 'openwrt-luci']`, and map `openwrt-luci` to the OpenWrt layout where the script branches on `args.os` (read it: firmware path `lib/firmware` vs `usr/lib/firmware`). `reset-password.sh`: `[ubuntu|openwrt|openwrt-luci|all]`, `both` kept as an alias of `all`, `all` = every one of the three that exists, `user=root` for `openwrt|openwrt-*`.
- [ ] **Step 3: Run** → the rule passes for the files done so far; it stays red for `install.sh`, `install.ps1`, `make-release.sh` until Tasks 6, 7, 11 (mark those three with `self.skipTest` lines that Task 11 removes, so every commit is green).
- [ ] **Step 4: Commit** — `git commit -m "vendor-overlay, reset-password: the third system; the uninstallers stay system-agnostic"`

---

### Task 6: `install.sh` asks "Which OpenWrt?"

**Files:**
- Modify: `install.sh` (choices block ~line 335-360, local build ~line 551, summary/final hints), `i18n/tr.tsv`, `i18n/zh.tsv`
- Test: `tests/test_installer.py`

**Interfaces:**
- Produces: after the system choice, when `OSES` contains `openwrt`: the question below; answer `2` replaces `openwrt` in `OSES` with `openwrt-luci`. `MU300_OPENWRT=plain|luci` answers it without asking (like the other `MU300_*` presets the script has; read how `MU300_STORAGE` is handled and do the same). The boot question offers the names in `OSES`. Local build: `MU300_SYSTEM=$os sh openwrt/build-rootfs.sh mu300-$os-rootfs.tar.gz`, `reuse $os`.

- [ ] **Step 1: Failing tests** — drive the choice block the way the existing installer tests do (read `tests/test_installer.py`; if the choice block cannot be cut out, put it into a function `choose_systems` in `install.sh` first, as the SD work did with `tools/storage.sh`): `3` then `2` → `OSES="ubuntu openwrt-luci"`; `2` then `1` → `OSES=openwrt`; `MU300_OPENWRT=luci` with `2` → `openwrt-luci` without the question; boot answer `openwrt-luci` accepted, `openwrt` refused when it is not in `OSES`.
- [ ] **Step 2: Implement** — messages (each also in both `.tsv` files, Turkish and Chinese written out):
  - `Which OpenWrt?`
  - `1) OpenWrt {1}: the standard LuCI web interface`
  - `2) OpenWrt {1} with the MU300 control panel: dashboard, cellular locks, SMS, AT terminal, USB modes (by kanoqwq)`
  - `Which one should boot ({1})` (replaces `Which one should boot (ubuntu/openwrt)`; `{1}` = the names joined by `/`)
  - `switch systems: mu300-os {1}   back to Android: mu300-next-boot android` (replaces the fixed text)
  Remove the replaced messages from both `.tsv` files.
- [ ] **Step 3: Run** the tests and `python3 tools/check-i18n.py` → PASS / nothing missing.
- [ ] **Step 4: Commit** — `git commit -m "install.sh: OpenWrt with the MU300 control panel as a choice next to plain OpenWrt"`

---

### Task 7: `install.ps1` the same

**Files:** Modify: `install.ps1` (lines ~560-590, the build and summary parts), Test: `tests/installer.Tests.ps1`

- [ ] **Step 1: Pester tests** for the new function (`Choose-OpenWrt` or the existing pattern: read the file) mirroring Task 6's cases.
- [ ] **Step 2: Implement** with the same messages through `T` (ASCII-only source; the Turkish/Chinese text lives in the `.tsv`).
- [ ] **Step 3: Run** `pwsh -File tests/installer.Tests.ps1` (if `pwsh` is not installed locally, CI runs it; `tests/test_static.py` still checks ASCII and quotes) and `python3 tools/check-i18n.py`.
- [ ] **Step 4: Commit** — `git commit -m "install.ps1: OpenWrt with the MU300 control panel, as in install.sh"`

---

## Phase 2 - building the system

### Task 8: `tools/po2lmo.py`

**Files:**
- Create: `tools/po2lmo.py`, `tests/test_po2lmo.py`, `tests/fixtures/po2lmo/{mu300.tr.po,mu300.tr.lmo,mu300.en.po,mu300.en.lmo}`

**Interfaces:**
- Produces: `python3 tools/po2lmo.py IN.po OUT.lmo`; importable `compile_po(text) -> bytes`. Exit 1 with a message on `msgctxt`, `msgid_plural`, or a malformed entry. An entry whose `msgstr` is empty or equal to its `msgid` is left out (as LuCI's `po2lmo` does - which is why an English catalog over English msgids is empty and not written).

- [ ] **Step 1: Fixtures** — `R=kanoqwq/clean-tf-7.2; for l in tr en; do git show "${R}:openwrt/luci-app-mu300/po/$l/mu300.po" > tests/fixtures/po2lmo/mu300.$l.po; git show "${R}:openwrt/luci-app-mu300/lmo/mu300.$l.lmo" > tests/fixtures/po2lmo/mu300.$l.lmo; done` (made by LuCI's real `po2lmo` in the fork; Chinese `msgid`s - allowed under `tests/fixtures/`, add that path to the CJK allowlist in Task 12).
- [ ] **Step 2: Failing test**

```python
"""tools/po2lmo.py against catalogs that LuCI's own po2lmo produced (tests/fixtures/po2lmo, from kanoqwq's fork)."""
import subprocess, sys, unittest
from helpers import TOP

FIX = TOP / 'tests' / 'fixtures' / 'po2lmo'
sys.path.insert(0, str(TOP / 'tools'))
import po2lmo  # noqa: E402


class Po2Lmo(unittest.TestCase):
    def test_byte_for_byte(self):
        for lang in ('tr', 'en'):
            want = (FIX / f'mu300.{lang}.lmo').read_bytes()
            self.assertEqual(po2lmo.compile_po((FIX / f'mu300.{lang}.po').read_text(encoding='utf-8')), want, lang)

    def test_identity_and_empty_are_left_out(self):
        out = po2lmo.compile_po('msgid "A"\nmsgstr "A"\n\nmsgid "B"\nmsgstr ""\n')
        self.assertEqual(out, b'\0\0\0\0')   # no values, no index, index offset 0

    def test_refuses_plural_and_context(self):
        for bad in ('msgctxt "x"\nmsgid "a"\nmsgstr "b"\n', 'msgid "a"\nmsgid_plural "as"\nmsgstr[0] "b"\n'):
            with self.assertRaises(ValueError):
                po2lmo.compile_po(bad)

    def test_cli(self):
        r = subprocess.run([sys.executable, str(TOP / 'tools/po2lmo.py'), str(FIX / 'mu300.tr.po'), '/dev/stdout'],
                           capture_output=True)
        self.assertEqual(r.stdout, (FIX / 'mu300.tr.lmo').read_bytes())
```

(Check `test_identity_and_empty_are_left_out`'s expected bytes against what LuCI's `po2lmo` writes for an empty catalog - read `luci-base/src/po2lmo.c` of the LuCI release in OpenWrt 25.12; if it writes nothing at all for an empty catalog, expect `b''` and make the build skip empty catalogs.)

- [ ] **Step 3: Implement** — from `luci-base/src/po2lmo.c` and `template_lmo.c` (read them on github.com/openwrt/luci at the `openwrt-25.12` branch; do not guess): parse `msgid`/`msgstr` with C-style escapes and continued lines; `key_id = sfh_hash(msgid)`, `val_id = sfh_hash(msgstr)`, skip when equal or `msgstr` empty; values written in order, each padded with NUL to a multiple of 4; then the index of `(key_id, val_id, offset, length)` as big-endian u32, sorted by `key_id`; then the big-endian u32 offset of the index. `sfh_hash` is Paul Hsieh's SuperFastHash with the initial hash = length, reading 16-bit little-endian words, the tail bytes as *signed* chars, all arithmetic modulo 2^32.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests -k Po2Lmo` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "po2lmo.py: LuCI catalogs built from their .po in Python, byte for byte as LuCI's po2lmo"`

---

### Task 9: The app, as kanoqwq left it (K70)

**Files:** Create: `openwrt/luci-app-mu300/**` (from the fork, without `lmo/`)

- [ ] **Step 1: Snapshot** — the one verbatim copy in this plan:
```sh
git archive kanoqwq/clean-tf-7.2 openwrt/luci-app-mu300 | tar -x -C . --exclude 'openwrt/luci-app-mu300/lmo'
git add openwrt/luci-app-mu300
```
Check the modes: `git ls-files -s openwrt/luci-app-mu300 | grep -E 'rpcd|unisoc-modem/|init.d|hotplug'` all `100755`.
- [ ] **Step 2: Note the origin** at the top of `openwrt/luci-app-mu300/README.md`: "Imported from kanoqwq/mu300-linux, branch clean-tf-7.2, commit 35a1c55 (PR #22 and later). Changed here since: see git log." Remove the `lmo` install line from the app's `Makefile` (catalogs are built by the image build; the `Makefile` stays for people who build the package with an SDK, and gets a `po2lmo` step in Task 10).
- [ ] **Step 3: `tests/test_static.py` still passes** (the app's shell scripts parse under the three shells; if one uses a bash-only construct, record it and fix it in this commit - it runs under busybox ash on the device).
- [ ] **Step 4: Commit** — body names the source commit:
```
luci-app-mu300: kanoqwq's control panel as of clean-tf-7.2 35a1c55, unchanged but for the compiled catalogs
```

---

### Task 10: `build-rootfs.sh` builds `openwrt-luci`

**Files:**
- Modify: `openwrt/build-rootfs.sh`
- Create: `openwrt/luci-overlay/etc/uci-defaults/91-mu300-luci` (Aurora default and language `auto` for now; Tasks 34-38 add to it)
- Test: `tests/test_static.py` (`test_openwrt_luci_build_wiring`)

**Interfaces:**
- Consumes: `MU300_SYSTEM=openwrt|openwrt-luci` (default `openwrt`), `MU300_LUCI_THEME_APK` (optional local copy).
- Produces: `OUT` defaults to `mu300-$MU300_SYSTEM-$VER-rootfs.tar.gz`; in an `openwrt-luci` image: the app's files, `/usr/lib/lua/luci/i18n/mu300.tr.lmo` and `mu300.<zh name>.lmo`, Aurora, `luci-i18n-base-tr`, `luci-i18n-base-zh-cn`, `luci-i18n-firewall-tr`, `luci-i18n-firewall-zh-cn`; in both: `/etc/mu300/packages.txt`.

- [ ] **Step 1: Static test** — `build-rootfs.sh` contains `MU300_SYSTEM`, the Aurora SHA256 `05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e`, `po2lmo.py`, `luci-overlay`, refuses `immortalwrt` with `openwrt-luci`; `91-mu300-luci` is executable and parses.
- [ ] **Step 2: Implement** (port of the fork's `build-rootfs.sh` changes, reshaped):
  - top: `SYSTEM=${MU300_SYSTEM:-openwrt}`; `case $SYSTEM in openwrt) ;; openwrt-luci) [ "$FLAVOUR" = openwrt ] || die ;; *) die ;; esac`.
  - only for `openwrt-luci`, on the host before Docker: the Aurora fetch-and-verify block from the fork (`git show kanoqwq/clean-tf-7.2:openwrt/build-rootfs.sh`, lines with `THEME_APK`/`THEME_SHA`), unchanged; and the catalogs: `for l in tr zh_Hans; do python3 tools/po2lmo.py openwrt/luci-app-mu300/po/$l/mu300.po "$CAT/mu300.$l.lmo"; done` into a temporary `$CAT` mounted as `/in/catalogs`.
  - in the container, after the common `apk add`: when `/in/luci-plugin` is mounted, `apk add luci-i18n-base-tr luci-i18n-base-zh-cn luci-i18n-firewall-tr luci-i18n-firewall-zh-cn` and `apk add --allow-untrusted /in/luci-theme-aurora.apk`.
  - after `cp -a /in/overlay/. $R/`: `[ -d /in/luci-overlay ] && cp -a /in/luci-overlay/. $R/`; the app (`root/` -> `$R/`, `htdocs/` -> `$R/www/`, the `chmod` of the fork); the catalogs: find LuCI's own name for Chinese with `ls $R/usr/lib/lua/luci/i18n/base.*.lmo` (expect `base.zh-cn.lmo`) and copy `mu300.zh_Hans.lmo` to that name (`mu300.zh-cn.lmo`), `mu300.tr.lmo` as is; fail the build when no `base.zh*.lmo` exists. `uci -c $R/etc/config set luci.languages.tr='Türkçe'` and `luci.languages.zh_cn='中文 (Chinese)'` if `luci-i18n-base-zh-cn` did not register them (it normally does; check with `uci -c $R/etc/config show luci.languages`).
  - the Aurora checks of the fork (`main.css` present; `mediaurlbase` points at aurora after first boot - the default is set by `91-mu300-luci`, so check the uci-defaults file instead of `/etc/config/luci`).
  - enable the app's `unisoc-modem-ui` like the other services.
  - both systems: `apk list --installed | sort > $R/etc/mu300/packages.txt`.
  - output name: `OUT=${1:-mu300-$SYSTEM-$VER-rootfs.tar.gz}`.
- [ ] **Step 3: Build both** (needs the kernel outputs; on the maintainer machine):
```sh
MU300_INPUTS=$PWD/work sh openwrt/build-rootfs.sh /tmp/plain.tar.gz
MU300_SYSTEM=openwrt-luci MU300_INPUTS=$PWD/work sh openwrt/build-rootfs.sh /tmp/luci.tar.gz
tar -tzf openwrt/luci.tar.gz | grep -E 'mu300dash|mu300\.(tr|zh-cn)\.lmo|aurora/main.css|91-mu300-luci' | wc -l   # 5
tar -tzf openwrt/plain.tar.gz | grep -cE 'mu300dash|aurora'                                                    # 0
```
- [ ] **Step 4: Commit** — `git commit -m "build-rootfs: MU300_SYSTEM=openwrt-luci builds OpenWrt with the MU300 control panel, Aurora pinned, catalogs from po"`

---

### Task 11: The release asset

**Files:** Modify: `tools/make-release.sh`; Test: `tests/test_static.py` (drop the `skipTest` of Task 5 for this file)

- [ ] **Step 1: Implement** — after the plain OpenWrt build:
```sh
echo "==> OpenWrt with the MU300 control panel"
MU300_SYSTEM=openwrt-luci MU300_INPUTS="$IN" MU300_VERSION="$TAG" sh "$TOP/openwrt/build-rootfs.sh" mu300-openwrt-luci-release.tar.gz >/dev/null
mv "$TOP/openwrt/mu300-openwrt-luci-release.tar.gz" "$D/mu300-openwrt-luci-rootfs.tar.gz"
```
  add `mu300-openwrt-luci-rootfs` to the audit loop; after it, the panel check (the app's menu JSON, `home.js`, `mu300dash`, `mu300.tr.lmo`, the Chinese `.lmo`, `aurora/main.css` must be in the luci asset; `mu300dash` and `aurora` must not be in the plain one); print the diff of `etc/mu300/packages.txt` against the previous release's asset when `$MU300_PREV_RELEASE` (a directory) is given; a notes row:
  `| mu300-openwrt-luci-rootfs.tar.gz | OpenWrt 25.12.5 with the MU300 control panel (luci-app-mu300 by kanoqwq, Aurora theme by eamonxg) |`, and Aurora in the "Corresponding source" sentence with its release URL.
- [ ] **Step 2: Run** `sh -n tools/make-release.sh`; the static rules → PASS.
- [ ] **Step 3: Commit** — `git commit -m "make-release: mu300-openwrt-luci-rootfs.tar.gz, audited like the others and checked for the panel"`

---

## Phase 3 - translations

### Task 12: The catalog checker (red first)

**Files:** Create: `tools/luci-i18n.py`, `tests/test_luci_i18n.py`; Modify: `.github/workflows/tests.yml` (a `actions/setup-node` step before the unit tests, Node 22)

**Interfaces:**
- Produces: `python3 tools/luci-i18n.py check` (exit 1 and a list of problems), `python3 tools/luci-i18n.py extract` (prints every message, one per line, for writing catalogs), `python3 tools/luci-i18n.py update` (adds missing `msgid`s with empty `msgstr` to both `.po`, removes stale ones, keeps order by first use).
- Message sources: `_('...')`/`_("...")` in `htdocs/**/*.js` (string literals only; JS escapes decoded); `"title"` values in `root/usr/share/luci/menu.d/*.json`; `"description"` in `root/usr/share/rpcd/acl.d/*.json`; backend messages - the literal after `"error":"` / `"message":"` in `printf` lines of `root/usr/libexec/rpcd/mu300dash` and `root/usr/libexec/unisoc-modem/*`, plus a `# i18n: ...` comment line form for messages built elsewhere.
- Allowed non-literal `_()` arguments (anything else is an error): `_(res.error)`, `_(res.message)`, `_(carrier.name)` - listed in `DYNAMIC_OK` in the tool.
- CJK scope: every tracked file under `openwrt/`, `rootfs/overlay/`, `boot/`, `tools/`, plus `install.sh`, `uninstall.sh`; allowed: `tools/i18n.sh`, `i18n/*.tsv`, `openwrt/luci-app-mu300/po/zh_Hans/*`, `tests/fixtures/**`. CJK = U+2E80-U+9FFF, U+F900-U+FAFF, U+FF00-U+FFEF.

- [ ] **Step 1: Tests**

```python
"""The control panel's catalogs: complete in Turkish and Chinese, no Chinese outside them (spec, Translations)."""
import subprocess, sys, unittest
from helpers import TOP

TOOL = [sys.executable, str(TOP / 'tools' / 'luci-i18n.py')]


class Catalogs(unittest.TestCase):
    def test_check_is_clean(self):
        r = subprocess.run(TOOL + ['check'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_checker_catches_each_rule(self):
        # each rule against a temporary copy of the app (tool option --root DIR): a CJK literal in a view, a
        # _() message missing from tr, a placeholder mismatch, a stale msgid, a dynamic _(x)
        ...
```

  Write `test_checker_catches_each_rule` fully: copy `openwrt/luci-app-mu300` into `self.tmp`, apply one defect at a time, run `check --root`, assert exit 1 and the rule's name in the output, restore.
- [ ] **Step 2: Run** → `test_check_is_clean` FAILS with the fork's state (hundreds of CJK lines, no `po/zh_Hans`) - that is the work of Tasks 13-16. Mark it `@unittest.expectedFailure` in this commit; Task 16 removes the decorator.
- [ ] **Step 3: Commit** — `git commit -m "luci-i18n.py: the control panel's catalogs checked - no Chinese outside zh_Hans, every message in tr and zh_Hans"`

---

### Task 13: `common.js`, the menu and the ACL in English with catalogs

**Files:** Modify: `openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js`, `root/usr/share/luci/menu.d/luci-app-mu300.json`, `root/usr/share/rpcd/acl.d/luci-app-mu300.json`; Create: `po/zh_Hans/mu300.po`; Modify: `po/tr/mu300.po`; Delete: `po/en/` (English is the source); Test: `tests/test_luci_i18n.py` (class `Languages`, the fork's Node harness rewritten)

- [ ] **Step 1: The conversion script** (scratchpad, not committed): load `DASH_I18N` from `common.js` with Node (`new Function` as the fork's test does), and for every Chinese literal `'X'` in the file: if `X` is a key, replace the literal with `_('EN')` (EN = column 0) and record `EN -> (tr, zh=X)`; print every Chinese literal that is not a key, and every `M.translate(` / `translate(` call whose argument is not a single literal - those are the fragments to rewrite by hand.
- [ ] **Step 2: Rewrite by hand** what the script printed: concatenations become one message with `%s`/`%d` and `.format()` (LuCI's `String.prototype.format`); `translate(x)` with a variable becomes `_()` at the place the literal is defined. Carrier names: a table `{ '46000': 'China Mobile', ... }` of English names, shown through `_(name)` (allowed dynamic site), with the names in both catalogs. Delete `DASH_I18N`, `DASH_KEYS`, `DASH_PATTERN`, `translate` and `uiLanguage` once nothing uses them (views still calling `M.translate` are converted in Tasks 14-15; keep a one-line `translate(s) { return s; }` shim until Task 15 deletes it).
- [ ] **Step 3: Menu and ACL** — English titles (`Status dashboard`, `Cellular`, `Network locks`, `SMS`, `AT terminal`, `Adapter settings`, `Device management` - take the fork's `po/en` texts), Turkish from the fork's `po/tr`, Chinese = the fork's original titles.
- [ ] **Step 4: Catalogs** — `python3 tools/luci-i18n.py update`, then fill `msgstr`s from the recorded pairs; the hand-written messages get Turkish and Chinese written out (Turkish: native wording, as in `i18n/tr.tsv`; Chinese: from the fork's original sentence where one existed).
- [ ] **Step 5: Tests** — port the fork's `run_js` harness with a stub `_` that looks up a dict loaded from the `.po` of the chosen language (`tr`, `zh_Hans`, or none):
  - `test_three_languages` (the fork's `链路与流量 · 无应答` case, now `_('Link & traffic · No response')`),
  - `test_carrier_names_follow_locale` (the fork's cases),
  - `test_unknown_language_is_english` (`de`: every rendered string equals its `msgid`).
- [ ] **Step 6: Run** the class → PASS (the catalog check still expected-fails for the other files).
- [ ] **Step 7: Commit** — `git commit -m "luci-app-mu300: common.js, the menu and the ACL speak English with Turkish and Chinese catalogs"`

---

### Task 14: `home.js` and `locks.js`

**Files:** Modify: `htdocs/luci-static/resources/view/mu300/home.js`, `locks.js`, both `.po`

- [ ] **Step 1:** Run the Task 13 script over each file; convert literals; rewrite fragments by hand (`locks.js` builds the band list and the "apply at startup is on/off" sentence from parts - the fork's `'开机自动应用已'` key is one of them: it becomes two whole messages, `Apply at startup is on` / `Apply at startup is off`).
- [ ] **Step 2:** `luci-i18n.py update`, fill translations, `luci-i18n.py check 2>&1 | grep -E 'home.js|locks.js'` → nothing.
- [ ] **Step 3:** The fork's JS smoke tests that touch these views still pass (if any; otherwise add one that loads each view module with stubbed `view`, `rpc`, `ui`, `dom` and checks it returns an object - a parse check under Node is the minimum).
- [ ] **Step 4: Commit** — `git commit -m "luci-app-mu300: the dashboard and the lock page in English with catalogs"`

---

### Task 15: `sms.js`, `at.js`, `settings.js`, `device.js`

Same steps as Task 14 for the four files; the "已添加到 LAN" case of the partial-match bug lives in `device.js` (`Added to LAN` as one message). Remove the `translate` shim from `common.js` when nothing calls it. Commit: `git commit -m "luci-app-mu300: SMS, AT terminal, settings and device pages in English with catalogs"`

---

### Task 16: Backend messages

**Files:** Modify: `root/usr/libexec/rpcd/mu300dash`, `root/usr/libexec/unisoc-modem/{action,cell,lock,device-usb,dashboard-info,at,boot-replay}`, the views that show `res.error`; both `.po`; Test: `tests/test_luci_i18n.py` (drop `expectedFailure`)

- [ ] **Step 1:** Every Chinese backend message becomes English (the ten in `mu300dash`: `Applying network locks: no live signal right now`, `Applying network locks: the modem is restarting (about half a minute), try again shortly`, `The AT channel is busy; the command was not sent, try again`, `mu300-sms is not available (the SMS service is not installed or not running)`, `Read failed`, `The number may only contain digits (and a leading +)`, `The message is empty`, `The message is too long (over 480 characters)`; the comment on line 308 in English). A message that carried a value inside the sentence (`printf '{"ok":0,"error":"%s"}' "$text"`) sends the fixed sentence in `error` and the value in `detail`; the UI shows `_(res.error)` followed by `res.detail` when present.
- [ ] **Step 2:** The English-only backend errors (`live signal read failed`, `unknown op`, `denied: this command wedges the AT port until a reboot`, `must be an AT command`, `unknown lock kind`, `bad id`, `dashboard-info failed`, `unknown USB setting`, `no such method`, and the adapters' messages the check lists) get sentence case and catalog entries.
- [ ] **Step 3:** Every view that displays `res.error`/`res.message` uses `_(...)` on it (grep `\.error` in `htdocs`).
- [ ] **Step 4: Run** `python3 tools/luci-i18n.py check` → exit 0; remove `@unittest.expectedFailure`; the whole suite → PASS.
- [ ] **Step 5: Commit** — `git commit -m "luci-app-mu300: backend messages in English, shown through the catalogs; the panel has no Chinese outside zh_Hans"`

---

## Phase 4 - the fork's fixes for every system (class a)

Each task: read the fork's version (`git diff 1a69a41 kanoqwq/clean-tf-7.2 -- <file>`), port the named hunks onto our file, write the test first, translate any Chinese comment/log into English, and name the K-ids and the fork commit in the commit body.

### Task 17: `mu300-at` (K48, K49, K50)

**Files:** Modify: `rootfs/overlay/opt/mu300/bin/mu300-at`; Test: `tests/test_device_scripts.py` (class `At`)

- [ ] **Step 1: Tests** with a fake daemon directory (`MU300_AT_DIR=$tmp/at`, a FIFO `cmd` created with `os.mkfifo`, a Python thread that reads one line and writes the answer file):
  - `test_answer_needs_a_final_result_code`: answer file `"\r\n"` → exit 1, stderr `modem returned no final response`; answer `+CSQ: 20,99\r\nOK\r\n` → exit 0 and the text.
  - `test_no_daemon_does_not_hang`: a FIFO with no reader → exits within `T + 2` s with `no answer from the daemon` and the lock directory gone (the old write-only open blocked for ever).
  - `test_fast_round_trip`: 20 commands against the fake daemon take under 3 s when `/opt/mu300/bin/busybox` is a stub that implements `sleep` with Python's `time.sleep` (point the script at it with `MU300_BUSYBOX`, a new variable defaulting to `/opt/mu300/bin/busybox`).
- [ ] **Step 2: Port** the fork's three hunks (`exec 9<> "$DIR/cmd"`, the `grep -aEq` final-code check, the `zz` sleep with `MU300_BUSYBOX`).
- [ ] **Step 3: Run** → PASS under dash, bash, busybox ash.
- [ ] **Step 4: Commit** — `git commit -m "mu300-at: the command FIFO opened read-write, an answer counts only with a final result code, 10 ms polling"`

### Task 18: `mu300-atd` (K51, K52)

**Files:** Modify: `rootfs/overlay/opt/mu300/bin/mu300-atd`; Test: `tests/test_device_scripts.py`
- [ ] Tests: cut the URC loop's channel list out (or run the script with a stub tty pair if the existing tests do that - read them); `MU300_AT_URC_CHANNELS=` (set, empty) opens no URC channel; unset opens nr0. Drain: after a reply ending in `OK`, the next command is sent without the 1 s wait (time it with a stub `now`); after a timeout, the slow drain runs.
- [ ] Port; run; commit — `git commit -m "mu300-atd: an empty URC channel list means none, and no drain wait after a clean reply"`

### Task 19: A second AT daemon for mobile data (K16, K17, K21)

**Files:**
- Modify: `openwrt/overlay/etc/init.d/mu300-atd`, `rootfs/overlay/opt/mu300/bin/mobile-data` (`at()`)
- Create: `rootfs/overlay/etc/systemd/system/mu300-atd2.service` (and its `WantedBy` link the way `mu300-atd.service` is enabled - read `rootfs/assemble.sh`)
- Test: `tests/test_device_scripts.py`, `tests/test_static.py`

- [ ] **Step 1: Tests** — static: `init.d/mu300-atd` has `START=19`, instances `atd` (nr1) and `atd2` (nr2, `MU300_AT_URC_CHANNELS=` empty, `MU300_AT_DIR=/run/mu300-at2`), no nr3-nr5 instances (K18 rejected); `mu300-atd2.service` sets the same environment. Behaviour: `mobile-data at` with `/run/mu300-at2/cmd` present uses it, else `/run/mu300-at` (stub `mu300-at` records `MU300_AT_DIR`).
- [ ] **Step 2: Port** the fork's `wait_and_exec` and the two instances; the plugin marker line (K21) exactly as the fork has it (`[ ! -x /usr/libexec/unisoc-modem/lock ] || : > /run/unisoc-modem-early-hook-pending`), with a comment that it does nothing without the panel. Do not port nr3-nr7 here (nr6/nr7 are Task 36). `mobile-data`'s `at()` gets the fork's nr2 preference block (English comment).
- [ ] **Step 3: Run**; then on the F50 (OpenWrt and Ubuntu): `ls /run/mu300-at2/cmd`, `mobile-data status`, `logread | grep -c "never appeared"` = 0.
- [ ] **Step 4: Commit** — `git commit -m "mu300-atd: started before the network (S19), and a second daemon on nr2 serves mobile data alone"`

### Task 20: Boot marks and the vendor start (K40, K41, K42)

**Files:** Create: `rootfs/overlay/opt/mu300/bin/bootmark`; Modify: `android-vendor-start`; Test: `tests/test_device_scripts.py`
- [ ] `bootmark`: English comments; appends `t=<uptime> <words>` to `${MU300_TIMELINE:-/run/mu300/boot-timeline}` (`-r` resets); never fails. Test: two calls → two lines, `-r` empties; unwritable path → exit 0.
- [ ] `android-vendor-start`: the fork's `mark` calls, the logdw start without `sleep 1`, the fork's fork-free partition loop (test it: a fake `/sys/class/block/mmcblk0p*/uevent` tree through `MU300_SYSROOT` if the script has one; if not, cut the loop into a function `link_partitions SYSDIR DEVDIR` and test that).
- [ ] Commit — `git commit -m "android-vendor-start: no idle second for logdw, partition links without 365 forks, boot marks in /run"`

### Task 21: `mobile-data` registration, address, LED, logs (K56, K59, K60, K61, K62)

**Files:** Modify: `rootfs/overlay/opt/mu300/bin/mobile-data`; Test: `tests/test_device_scripts.py` (class `MobileData`)

**Interfaces:**
- Produces: `wait_registered` (URC log `${MU300_URC_LOG:-/run/mu300-at/urc/stty_nr0.log}`, budget `MU300_REGISTER_WAIT`, query fallback every 5 s), `fetch_addr SECONDS` (sets `rdp`), `radio_say` writing `/run/mu300/radio.log`, `led_up` from `AT+CEREG?`.
- [ ] **Step 1: Tests** — source-able functions: run `mobile-data` with `MU300_LIB=1` support (add the same "functions only" guard `mu300-update` has, right before the final `case`), stub `at` as a shell function that answers from a table:
  - `test_registered_from_urc`: URC log gets `+CEREG: 1` appended after the start → returns 0 without any `AT+CEREG?` sent in the first 5 s.
  - `test_old_urc_does_not_count`: the log already ends with `+CEREG: 1` before the call and nothing new arrives → falls back to the query.
  - `test_fetch_addr_bounded`: `+CGCONTRDP` answers `0.0.0.0` → returns 1 within budget + 2 s.
  - `test_led_from_cereg`: `+CEREG: 2,1,"..","..",13` → `mu300-led data 5g`; `,7` → `data on` (stub `mu300-led` records).
  - `test_systemd_path_keeps_v6_up`: grep-level: `up_locked` still calls `v6_up` when `iid` is set and `v6_off` otherwise (K63 rejected).
- [ ] **Step 2: Port** `wait_registered`, `is_registered`, `fetch_addr` + the one idempotent `CGACT=1` reassert, the CEREG `led_up` (with `mu300-led`, not `led-status` - Task 32 adds the F50 colours), the background COPS log, `radio_say` with English messages. Keep `v6_up`/`v6_off`.
- [ ] **Step 3: Run** → PASS. On both devices (Ubuntu and OpenWrt): `mobile-data down; time mobile-data up` three times; record the times before/after in the commit body.
- [ ] **Step 4: Commit** — `git commit -m "mobile-data: registration from the modem's own announcements, a bounded address wait, 4G/5G from CEREG"`

### Task 22: `radio_on` (K57, K58, K66, K20, K65)

**Files:** Modify: `rootfs/overlay/opt/mu300/bin/mobile-data`, `openwrt/overlay/etc/init.d/mu300-atd` (`radio-warmup` instance); Test: `tests/test_device_scripts.py`, `tests/test_early_replay_hook.py` (ported from the fork)
- [ ] Tests (stub `at`/`mu300-at`): SMMSWAP is sent once per boot (marker `/run/mu300-ril-handshake`); no SFUN when CFUN? gets no answer; after SFUN=4, `CFUN: 0` twice then `CFUN: 1` → success without SFUN=2; two concurrent `radio_on` → the second waits for the lock; the lock-replay hook runs only when `/usr/libexec/unisoc-modem/lock` exists (`MU300_PLUGIN_LOCK` override for the test) and is called after SMMSWAP and before SFUN=4 (the fork's `test_early_replay_hook.py` cases, paths adjusted).
- [ ] Port the fork's `radio_on_locked`/`radio_on`, `radio-on` subcommand, `AT+CAVIMS=1` (K58), the warm-up instance (K20). All logs English.
- [ ] **Gate (K57, K58)**: Task 41/42 boots. Until they pass, this commit stays on the branch but the plan does not move on to Task 41's sign-off.
- [ ] Commit — `git commit -m "mobile-data: the radio comes up after the modem's first word and the RIL handshake, without an early power cycle"`

### Task 23: `sipa-dele` loaded at the right moment (K45, K46, K47)

**Files:** Modify: `extra-modules`, `sipa-dele-start`, `mobile-data`; Test: `tests/test_device_scripts.py`
- [ ] Tests: `extra-modules` never starts `sipa-dele-start` (stub records); `sipa-dele-start` exits 1 after its wait without calling `insmod` (stub); `mobile-data up` calls it after registration and before `AT+CGACT=1`, and stops when it fails.
- [ ] Port (for all systems, not only OpenWrt as in the fork). On both devices, Ubuntu and OpenWrt: 10 `mobile-data down; mobile-data up` cycles, `dmesg | grep -ci "cfi\|sipa_dele.*panic"` = 0, `lsmod | grep sipa_dele`.
- [ ] Commit — `git commit -m "sipa-dele is loaded by mobile-data between registration and the PDP context, never in the background"`

### Task 24: `mu300-vendor` earlier (K22, K23)

**Files:** Modify: `openwrt/overlay/etc/init.d/mu300-vendor`; Test: `tests/test_static.py`
- [ ] Static test: `START=09` (with the leading zero, the fork's comment on why), no `sleep 5` in the `modem_control` command.
- [ ] **Gate (K23)**: on each device 10 cold boots (F50: `uhubctl` cycle; U30 Air: `reboot`), each time `mu300-at 'AT+CEREG?'` registered within 60 s of `uptime` and `logread | grep -c "modem alive timeout"` = 0. Record the 10 registration times.
- [ ] Commit — `git commit -m "mu300-vendor: the modem is released at S09 and without a fixed five-second wait"`

### Task 25: Early DHCP for the USB host (K5, K9, K11, K13, K25, K26, K27)

**Files:**
- Modify: `boot/init` (`setup_usb_gadget` end; before `switch_root`: `killall udhcpd`), `openwrt/overlay/etc/hotplug.d/iface/10-mu300-usb`, `openwrt/overlay/etc/uci-defaults/90-mu300`, `openwrt/overlay/etc/init.d/mu300-post` (`usb1`)
- Create: `openwrt/overlay/lib/preinit/06_mu300_early_usb`
- Test: `tests/test_boot_init.py`, `tests/test_device_scripts.py`, `tests/test_static.py`

**Interfaces:**
- Produces: init serves `$NET.200` on the gadget netdev with a 120 s lease (`/run/udhcpd-usb0.conf`, `/run/mu300-usb-host-mac`); preinit restarts it with the system's LAN address (`uci -q get network.lan.ipaddr`, else `mu300-lan-ip`) and lease 3600, pid in `/run/mu300-early-udhcpd.pid`; `10-mu300-usb` on `lan` ifup kills it, removes the bridge address from `usb0`/`rndis0` if they kept it, reattaches `rndis0`, **and still does our macOS re-enumeration** (K10 rejected until Task 26's gate).
- [ ] **Step 1: Tests**:
  - init (static): the udhcpd block uses `$NET` and `lease 120`; `killall udhcpd` comes before the `switch_root` line; `sleep 300` unchanged.
  - `test_preinit_uses_the_configured_lan`: run `06_mu300_early_usb`'s function with stubs (`uci` printing `10.1.2.1`, `ifconfig`, `udhcpd` recording its config) → config has `start 10.1.2.200`, `router 10.1.2.1`.
  - `10-mu300-usb`: with stubs for `ip`, `kill`, the pid file → kill called, `ip addr del` only for a port that has the bridge address, the re-enumeration subshell still started (stamp file written).
  - `90-mu300`: the `mu300_usb` host entry (`broadcast 1`, the MAC from `/run/mu300-usb-host-mac`), the `earlyusb` zone added once (run twice → one zone), `rndis0` only when `/sys/class/net/rndis0` exists, `bridge_empty 1` - run against a uci stub that records the batch.
- [ ] **Step 2: Port** the fork's hunks with the two fixes named above (subnet from the system, short initramfs lease).
- [ ] **Step 3: On the F50** (OpenWrt): macOS host (screen unlocked, known MAC) and the Windows host (`ssh kaan@192.168.2.20`, `ipconfig`): time from power-on to a lease, 5 boots each; then set `uci set network.lan.ipaddr=192.168.90.1; uci commit` and reboot: the host is on `192.168.90.x` within 2 minutes. Ubuntu on the F50: the host gets its lease (init's early server) and then `lan-start`'s dnsmasq (renew works).
- [ ] **Step 4: Commit** — `git commit -m "USB: the host gets its lease from the initramfs and keeps it into the system, on the system's own subnet"`

### Task 26: One fast rebind instead of the lease check (K12, K67; K10 decided here)

**Files:** Modify: `rootfs/overlay/opt/mu300/bin/mu300-usb-reset`, `openwrt/overlay/etc/init.d/mu300-post`, (if the gate passes) `10-mu300-usb`; Test: `tests/test_device_scripts.py`
- [ ] Tests: `mu300-usb-reset` exits 0 without touching `UDC` when the role file says `host` (`MU300_SYSROOT`-style path variable for `/sys/class/usb_role/.../role`, add it); `--fast-run` writes `""` then the UDC name, waits ~100 ms (stub busybox `usleep` records), runs the LAN handoff.
- [ ] Port the role check, the configfs mount, `--fast-run` (`--ready` too). In `mu300-post`: the fork's `usb-ready` one-shot **in addition to** `--if-no-lease` for now.
- [ ] **Gate (K12)**: macOS, Windows 11, a Linux host: 5 boots each, lease without replug, `ping -c 3 <lan ip>` works. If all pass: remove `--if-no-lease` from `mu300-post` and the 20 s re-enumeration from `10-mu300-usb` (K10 becomes taken: change its row in the spec to `a` with the numbers in FINDINGS). If any host fails: remove the `usb-ready` instance again (one behaviour for every host), keep `--if-no-lease` and the re-enumeration, and K12 becomes `c`; the role check and the configfs mount of K67 stay (they fix `mu300-usb-reset` itself).
- [ ] Commit — `git commit -m "mu300-usb-reset: never in host mode, and one 100 ms rebind once the LAN is up"` (+ the gate result in the body)

### Task 27: RNDIS as one configuration (K6)

**Files:** Modify: `boot/init` (`setup_usb_gadget`); Test: `tests/test_boot_init.py`
- [ ] Cut `setup_usb_gadget` into markers if not already; run it against a fake configfs directory tree (`g=$tmp/gadget`, stub `ln`/`ifconfig`/`ip`): `MU300_USBNET=rndis` → one configuration `c.1` with `rndis.rn0` and `acm.GS0`, `bcdDevice 0x0302`; `MU300_USBNET='ncm ecm'` → unchanged (`0x0301`, `ncm.usb0`).
- [ ] Port.
- [ ] **Gate**: Windows 10 or 11 host (`ssh kaan@192.168.2.20`): boot with `MU300_USBNET=rndis` (kernel command line through `boot/build-boot-image.py`'s cmdline option - read it), `Get-NetAdapter` shows an RNDIS adapter with a lease; Linux host: `ip link` shows it. If Windows shows only a COM port: revert, K6 → `c`.
- [ ] Commit — `git commit -m "init: RNDIS in a single configuration with the console, which Windows binds as a composite device"`

### Task 28: The region probe (K4, gated first)

- [ ] **Measure first** on both devices (from Linux, the probe as init runs it): `time sh -c 'dd if=/dev/mmcblk0 bs=1 skip=27762099256 count=2 2>/dev/null | od -An -tx1'` with busybox. If under 0.5 s: K4 → `c` ("busybox dd seeks"), record it, no code change.
- [ ] Otherwise port the fork's `is_mu300root` (loop device at the offset), test with a fake image file in `tests/test_boot_init.py` (`losetup` stub mapping to the file), commit — `git commit -m "init: the region probe seeks through a loop device instead of reading up to it"`

### Task 29: Wi-Fi (K15, K55)

**Files:** Modify: `openwrt/overlay/etc/init.d/mu300-hw`, `rootfs/overlay/opt/mu300/bin/wifi-start`; Test: `tests/test_device_scripts.py`
- [ ] `wifi-start`: `modprobe` first; when it fails, `insmod $K/$m.ko` or `$K/extra/$m.ko`; neither → exit 1 `missing $m.ko`. Test with stubs.
- [ ] `mu300-hw`: `wifi down; wifi up` once after the country is live (fork hunk); the 45 s retry in `mu300-post` stays (K14).
- [ ] On the F50 OpenWrt: 10 reboots, `ubus call hostapd.wlan0 get_status | grep -c ENABLED` each time; `logread | grep -c "AP not up, retrying"` recorded.
- [ ] Commit — `git commit -m "Wi-Fi: the AP is restarted once the regulatory domain is live, and the modules load without modules.dep"`

### Task 30: Downlink through fw4's flowtable (K28, K29, K74)

**Files:** Create: `openwrt/patches/fw4-sipa-offload.patch` (the fork's); Modify: `openwrt/build-rootfs.sh`, `openwrt/overlay/etc/uci-defaults/90-mu300`; Test: `tests/test_static.py`
- [ ] Port: the patch, applied with `patch --batch --fuzz=0` (build fails when it no longer applies), the fork's check that `mu300cell.sh` exists (and `mu300cell-v6.sh` for `openwrt-luci`), `flow_offloading 1`, `flow_offloading_hw 0`.
- [ ] On the F50 OpenWrt: `nft list flowtables | grep -c sipa_eth0` ≥ 1; download speed to a Wi-Fi client (`curl -o /dev/null` of a 100 MB file from a client) before/after, recorded.
- [ ] Commit — `git commit -m "OpenWrt: cellular downlink in fw4's software flowtable (sipa_eth0 patched in), the build stops if the patch no longer applies"`

### Task 31: `mu300cell` bug fixes (K32, K33)

**Files:** Modify: `openwrt/overlay/lib/netifd/proto/mu300cell.sh`; Test: `tests/test_device_scripts.py` (class `Mu300cell`, netifd functions stubbed: `proto_init_update` etc. record their arguments)
- [ ] Tests: setup sends `proto_init_update "$ifname" 1 1` (external); installs the v4 address with `ip -4 addr replace` and removes other global v4 addresses; v6 flush on setup; teardown kills the command, runs `mobile-data down`, flushes v4 and v6 of `sipa_eth0`; **`sleep 20` after an attach failure is still there** (K34 rejected); with `ipv6` unset or `extend`, the existing `extendprefix` dynamic interface is still added when `IID6` is set.
- [ ] Port the hunks that are not relay-specific (no `mu300cell-v6.sh` call, no renew handler - Task 34).
- [ ] On the F50 OpenWrt: `ifdown wan; ifup wan` five times, `logread | grep -c "reconnecting"` over 10 minutes afterwards = 0 (the 60 s redial loop).
- [ ] Commit — `git commit -m "mu300cell: the bearer's addresses are the protocol's own, so netifd never deletes them and the watchdog stops redialling"`

### Task 32: LEDs (K68, K38)

**Files:** Modify: `rootfs/overlay/opt/mu300/bin/mu300-led`, `mobile-data` (the LED calls), `openwrt/overlay/etc/init.d/mu300-post`, `mu300-hw`; Create: `openwrt/overlay/opt/mu300/bin/mu300-led-events`; Test: `tests/test_device_scripts.py` (class `Led`)
- [ ] **Gate first (D10)**: on the F50, `mu300-led test` and, one channel at a time, `echo 255 > /sys/class/leds/sc27xx:green/brightness` (then 0): note the colour; `keyboard-backlight`: does a lamp light, which one. Write the result into `docs/FINDINGS.md`. If green is not white or the Wi-Fi lamp is not on `keyboard-backlight`, port only the states that the board confirms.
- [ ] Then `mu300-led`: F50 `data 5g` = white (green channel) alone, `data on` = blue alone, `data error`/`data off` = red (fork's "offline red"), `boot` = the chase (a background loop with a pid file under `/run/mu300/led`, stopped by any other state - the same pattern as the siren), `wifi on|off` on the F50's Wi-Fi lamp; `/etc/mu300/led.conf`: `LED_SIGNAL=0|1`, `LED_WIFI=0|1` (both default 1). U30 Air unchanged. Tests through `MU300_SYSROOT` as the existing `Led` tests do.
- [ ] `mu300-led-events`: the fork's `ubus listen` loop calling `mu300-led data ...`/`wifi ...`; started from `mu300-post` with respawn; `mu300-hw` starts `mu300-led boot`.
- [ ] On the F50 both systems: boot (chase), data up (blue/white by RAT), `mobile-data down` (red), hotspot down/up (Wi-Fi lamp).
- [ ] Commit — `git commit -m "mu300-led: the F50's lamp shows 4G blue, 5G white, no data red and a chase while booting, and its Wi-Fi lamp"`

### Task 33: Quiet console (K24)

**Files:** Create: `openwrt/overlay/etc/sysctl.d/99-mu300-console.conf`, `rootfs/overlay/etc/sysctl.d/99-mu300-console.conf` (same content, English comment); Test: `tests/test_static.py`
- [ ] Commit — `git commit -m "printk level 1 on every system: the UART console no longer prints the WLAN log synchronously"`

---

## Phase 5 - only in `openwrt-luci` (class b)

### Task 34: Relay IPv6 (K30, K35, K36, K37, K64)

**Files:**
- Modify: `openwrt/overlay/lib/netifd/proto/mu300cell.sh` (`proto_config_add_string ipv6`; when `relay`: `renew_handler`, the `proto_run_command` of `mu300cell-v6.sh`, `accept_ra` cycle; `extend` or unset: today's path), `rootfs/overlay/opt/mu300/bin/mobile-data` (K64: `accept_ra 2` for IPV4V6 on the netifd path only, under `MU300_IPV6=relay`)
- Create: `openwrt/luci-overlay/lib/netifd/proto/mu300cell-v6.sh`, `openwrt/luci-overlay/opt/mu300/bin/ndp-learn`, `openwrt/luci-overlay/etc/init.d/mu300-ndp`
- Modify: `openwrt/luci-overlay/etc/uci-defaults/91-mu300-luci`
- Test: `tests/test_device_scripts.py` (`Mu300cell` relay cases), `tests/test_static.py`

- [ ] Tests: relay mode starts the monitor with the fork's arguments; `mu300cell-v6.sh "" dump` and `dump` exit at once (netifd probe); `ndp-learn`'s prefix parse from a fake `/proc/net/if_inet6` (make the path a variable); `mu300-ndp` does nothing unless `uci get network.wan.ipv6` is `relay`; `91-mu300-luci` sets `network.wan.ipv6=relay`, `pdptype=IPV4V6` and the fork's dhcp/firewall/ULA settings, idempotently.
- [ ] Port the fork's files (English comments) into `luci-overlay`.
- [ ] On the F50 `openwrt-luci` with the Turkish SIM: `ip -6 addr show sipa_eth0 scope global`, a Wi-Fi client's `curl -6 https://ifconfig.co`, 10 minutes of `ping -6`; record in FINDINGS whether the carrier fills `+CGCONTRDP`'s v6 address (this decides the later move of relay to plain OpenWrt, spec D9).
- [ ] Commit — `git commit -m "openwrt-luci: IPv6 by RA relay with NAT66, kanoqwq's design, as a mode of mu300cell"`

### Task 35: SMS pool (K69)

**Files:** Create: `openwrt/luci-overlay/opt/mu300/bin/{mu300-sms,mu300-smsd}`, `openwrt/luci-overlay/etc/init.d/mu300-smsd`; Modify: `openwrt/build-rootfs.sh` (enable `mu300-smsd`, link `mu300-sms` into `/usr/bin` - only for `openwrt-luci`); the app's `mu300dash` if it names a pool path; Test: port the fork's SMS pool tests if any (`git grep -l mu300-sms kanoqwq/clean-tf-7.2 -- tests`), else new ones: `mu300-sms list` on a fake pool, `sync` with a stub `mu300-at` returning two `+CMGL` PDUs (take PDUs from our `sms` tests), `smsd` single-instance lock.
- [ ] Pool path `/etc/mu300/sms-pool` (D11), English messages (the one Chinese line translated).
- [ ] On the F50 `openwrt-luci`: send an SMS to the SIM from a phone, it appears on the panel's SMS page within 10 s; send one from the panel; our `sms list` still works on the same SIM.
- [ ] Commit — `git commit -m "openwrt-luci: the SMS pool behind the panel's SMS page, kept apart from the sms command's state"`

### Task 36: The dashboard's AT channels (K19)

**Files:** Create: `openwrt/luci-overlay/etc/init.d/mu300-atd-dash` (nr6, nr7: `MU300_AT_DIR=/run/mu300-at6|7`, `MU300_AT_URC_CHANNELS=`), enabled in the build; Test: static.
- [ ] On the F50 `openwrt-luci`: the dashboard refreshes every few seconds while `mobile-data up` runs, without `mu300-at: busy` in `logread`.
- [ ] Commit — `git commit -m "openwrt-luci: two AT channels of its own for the dashboard"`

### Task 37: USB mode from the panel (K7, K8)

**Files:** Modify: `boot/init` (after `pick_root`: read `$rootdir/etc/mu300/usb-net`), `openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/device-usb` (writes `/etc/mu300/usb-net`, reads `/run/mu300/usb-net-applied`), its hotplug scripts if they name the old paths; Test: `tests/test_boot_init.py`, `tests/test_usb_management.py` (ported from the fork, paths adjusted)
- [ ] init: `usb_net_policy ROOTDIR` (between `# --- usb-net begin/end` markers) prints the functions for `ncm|ecm|rndis` (`ncm ecm`, `ecm ncm`, `rndis`) or nothing; when it prints something, `MU300_USBNET` is not set on the command line, and it differs from what was bound: unbind, rebuild the functions, bind again, write `/run/mu300/usb-net-applied`. Tests: each value, garbage, `MU300_USBNET` set → nothing.
- [ ] Port the fork's `test_usb_management.py` cases that are about the plugin's own logic (role switch, host mode, NIC reattach); drop the ones about the fork's init order (`test_tf_early_policy_precedes_gadget_enumeration`), replace with the D12 behaviour.
- [ ] On the F50 `openwrt-luci`: choose ECM on the Device page, reboot: `cat /sys/kernel/config/usb_gadget/linux/configs/c.1/*/` shows ecm; the Mac gets a lease; back to NCM.
- [ ] Commit — `git commit -m "openwrt-luci: the USB mode chosen on the Device page is applied by init on the next boot"`

### Task 38: LuCI LED page and Aurora default (K39, K31, K73)

**Files:** Create: `openwrt/luci-overlay/www/luci-static/resources/view/system/leds.js` (the fork's, writing `LED_SIGNAL`/`LED_WIFI` in `/etc/mu300/led.conf` through `fs.write` and calling `mu300-led refresh` through `fs.exec`; ACL entries for both in the app's `acl.d`); Modify: `91-mu300-luci` (Aurora as `luci.main.mediaurlbase`, `system.mu300_leds` not needed any more - the file is the store); `mu300-led refresh` (re-apply the remembered states with the switches); the strings of `leds.js` through `_()` and the catalogs.
- [ ] On the F50 `openwrt-luci`: switch the signal lamp off on the page → the lamp goes dark, data still up; on → back.
- [ ] Commit — `git commit -m "openwrt-luci: the LED page switches the signal and Wi-Fi lamps, and Aurora is the default theme"`

### Task 39: The fork's plugin tests (K81)

**Files:** Create: `tests/test_unisoc_plugin.py` (from `git show kanoqwq/clean-tf-7.2:tests/test_unisoc_plugin.py`, paths and the `MU300_*` variables adjusted to our tree); `tests/README.md` rows for every new test file of this plan.
- [ ] Run the whole suite under the three shells → PASS. Commit — `git commit -m "tests: kanoqwq's plugin tests, on our paths"`

---

## Phase 6 - documentation and the devices

### Task 40: README, BUILD, tests/README

- [ ] README: the third system in the "what you can install" part (what the panel does, screenshots only if the user adds them later, credit to kanoqwq and eamonxg), the asset, `mu300-os openwrt-luci`. `docs/BUILD.md`: `MU300_SYSTEM`, `MU300_LUCI_THEME_APK`, `tools/po2lmo.py`, `tools/luci-i18n.py`. Commit — `git commit -m "README: OpenWrt with the MU300 control panel, the third system"`

### Task 41: F50 campaign

Install from a local release directory built with `tools/make-release.sh vtest` (not published; `MU300_RELEASE_URL=http://<mac>:8000` serving `release/vtest`, as the installer supports):
- [ ] `./install.sh` → "both", Ubuntu 24.04, "Which OpenWrt?" 2 → `ubuntu` + `openwrt-luci` installed; then a second run with OpenWrt 1 adds `/openwrt` (three systems).
- [ ] `mu300-os` lists three; switch to each, reboot, `cat /etc/os-release`, `mu300-update check` says the right "running now".
- [ ] `openwrt-luci`: every page in English, Turkish, Chinese (LuCI → System → Language), German browser → English; no CJK on the Turkish pages (`curl` the rendered translation JSON `/cgi-bin/luci/admin/translations/tr` is not enough - click through, screenshot each page into `work/`); trigger each backend error once (busy AT channel: run `mobile-data up` while sending a command; an over-long SMS; a bad number).
- [ ] Gates of Tasks 22, 24, 25, 26, 27, 29, 32: their commands.
- [ ] `mu300-update apply openwrt-luci` from `vtest` to `vtest2` (rebuild with one visible change), settings kept (`etc/config/network`, the panel's lock state), `rollback`, `clean`.
- [ ] 20 reboots of `openwrt-luci` with Linux as the default boot: no hang, data up each time (`mu300-at 'AT+CEREG?'`).
- [ ] `tools/reset-password.sh openwrt-luci` from Android; `uninstall.sh` removes everything (last step).

### Task 42: U30 Air campaign

The same as Task 41 on the U30 Air (192.168.78.1; battery: no `uhubctl` cycle, use `reboot`), plus: the early DHCP serves `192.168.78.200` (not .77), `usb1` joins br-lan if present, the U30 Air's LEDs unchanged by Task 32.

### Task 43: Record and close

- [ ] `docs/FINDINGS.md`: one section "kanoqwq's fixes, measured" with every gate's numbers (rows that turned into `c` included), citing the fork commits.
- [ ] The spec's table matches what was done (classes updated by the gates); the count line recomputed: `grep -E '^\| K[0-9]+ ' docs/superpowers/specs/2026-10-05-openwrt-luci-system-design.md | awk -F'|' '{gsub(/ /,"",$4); print $4}' | sort | uniq -c`.
- [ ] `python3 -m unittest discover -s tests` (all shells), `python3 tools/check-i18n.py`, `python3 tools/luci-i18n.py check` → clean.
- [ ] Commit — `git commit -m "FINDINGS: kanoqwq's fixes on our code, measured on the F50 and the U30 Air"`
