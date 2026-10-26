# Releasing: the smoke test after a release

How a release is named, built and published is in [BUILD.md](BUILD.md) ("Release names", "Releasing from GitHub
Actions"). This page is what comes after: trying the prerelease on real boards before it is promoted with
`gh release edit TAG --prerelease=false --latest`. The long, hand-run test of what an update keeps is
[update-device-test.md](update-device-test.md) (FINDINGS 85); the smoke test here is the short one for every release,
and `tools/smoke.py` runs it (FINDINGS 156).

## What `tools/smoke.py` does

```sh
cp tools/smoke.example.json tools/smoke.json      # once; git ignores tools/smoke.json
$EDITOR tools/smoke.json                          # your boards (below)
tools/smoke.py --dry-run v2026.10.16.1            # the plan: every command, nothing connected
tools/smoke.py v2026.10.16.1 --report smoke-v2026.10.16.1.md
tools/smoke.py --board u30-openwrt v2026.10.16.1  # one board only (--board again for more)
tools/smoke.py release/v2026.10.16.1              # a local release directory; the tag is its name (or --tag)
```

Python 3.8 or newer, standard library only; `ssh`, `ssh-keyscan` and `scp` on the computer, and `adb` for the boards
with an adb serial or the Magisk path. For each board of the config, in this order:

1. **Identity, for all boards before any is touched.** The SSH host key (from `ssh-keyscan`) must have the
   fingerprint of the config; only that key goes into a known_hosts file of the run's own, and every later `ssh` and
   `scp` uses it with `StrictHostKeyChecking=yes` (no other known_hosts file is read or written). Then, over that
   connection, `/proc/sys/kernel/hostname` must be the config's `hostname` (when given), `mu300-device --json` the
   config's `device` and running `system`, and the `adb_serial` (when given) must be in `adb devices` as `device`. A
   board in Android (`"system": "android"`) must be in `adb devices` under its `android_serial` and must not have
   `/opt/mu300` (it really is Android). Anything else stops the whole run with exit status 2 and a `REFUSED` line per
   problem: nothing has been run on any board.
2. **Before.** One fixed script over `ssh` (as root: `sudo -n` when the user is not root) prints the release
   (`/etc/mu300/image-version`), `uname -r`, the boot image's tag (`/mnt/mu300-disk/boot/installed.tag`), the default
   boot (`/etc/mu300/default-boot`), the `mu300-*` units (`systemctl list-units`, Ubuntu) or `/etc/init.d/mu300-*`
   enabled/running (OpenWrt), the AP interfaces (`iw dev`), the `sipa_eth*` interfaces with an IPv4 address (mobile
   data), six fields of `mu300-vpn status --json` (enabled, running, kill switch setting, profile id, engine, tunnel
   up; nothing else of it is kept) and `sha256sum` of every file under `/etc/mu300/*vpn*`, computed on the device. The
   harness keeps the hashes by file name and never reads, prints or stores a file's contents; the report does not
   show the hashes either. A board whose default boot is not `linux` is not updated (its reboot would go to Android).
3. **The update**, the board's `update`:
   - `online`: `env MU300_RELEASE=TAG /opt/mu300/bin/mu300-update apply`
   - `mirror`: the same with `MU300_RELEASE_URL=<mirror_url>/TAG` - a server (or a `file://` directory on the device,
     which needs `curl` there) with the release's files and `SHA256SUMS`, laid out like
     `https://github.com/dikeckaan/mu300-linux/releases/download/TAG/`; update-device-test.md section 0 makes one
   - `from`: `mu300-update apply --from <from_path>`, a directory on the device with a release's files and their
     `SHA256SUMS`. With `--release-dir DIR` the harness first makes `from_path` and copies every file of DIR into it
     with `scp` (as the ssh user: the directory must be one that user may write); put only what the board needs into
     DIR, or the whole release
   - `magisk`: the release's Magisk zip `mu300-magisk-TAG-<magisk_zip>.zip` from `--release-dir`. A board in Linux is
     sent to Android first (`mu300-next-boot android`, reboot); then `adb push` to `/data/local/tmp/`,
     `su -c 'magisk --install-module ...'` (the way android/magisk/README.md installs a module from a computer), the
     zip removed, `adb reboot`, and Linux starts. Only for a board with `"allow_android": true`
   
   `apply_args` adds `--unsigned`, `--downgrade`, `--no-rollback`, `--no-reinstall` or the systems to update. A failed
   update is a FAIL with mu300-update's last line, and the board is not rebooted.
4. **Reboot and after.** The reboot script checks the default boot again and reboots only into Linux. The harness waits
   for `ssh` to go away (at most `timeouts.down`) and to come back (at most `timeouts.boot`), checks the identity
   again (the same host key, hostname, device, system), waits `timeouts.settle` seconds for the services, reads the
   state again and compares.

## The checks

One line per check and board: `- PASS|FAIL|SKIP <board> <check>: <detail>`, then a count. Exit status 0 when nothing
failed, 1 when a check failed, 2 for a config or usage error or a refused identity. After a FAIL that ends a board's
run, the checks that were not reached are SKIP "not reached".

| check | PASS when |
|---|---|
| `identity` | the board is the one in the config (always PASS here: a mismatch stops the run before) |
| `update` | the update command (or the zip's install) exited 0 |
| `reboot` | ssh went away and came back within the timeouts (says when the host key changed) |
| `identity-after` | the same host key, hostname, device and system after the reboot |
| `release` | `/etc/mu300/image-version` is the tag |
| `boot-image` | the boot image's tag is the tag (SKIP on the Magisk path, which writes the boot image itself) |
| `kernel` | `uname -r` starts with `expect_kernel` (or the zip's kernel); without either, the same major.minor as before |
| `services` | no unit or procd service is `failed`, and none that ran before has stopped |
| `hotspot` | an AP interface is up when one was before (SKIP when it was down before) |
| `mobile-data` | a `sipa_eth*` interface has IPv4 when one had before (SKIP when it was down before) |
| `vpn-state` | the six fields are as before (SKIP without the VPN module) |
| `vpn-files` | every file under `/etc/mu300/*vpn*` has the hash it had; else the names of the changed, gone and new files |

A board that starts in Android has no "before": `kernel` comes from the zip's name, `hotspot` down is a FAIL,
`mobile-data` down a SKIP (no SIM, perhaps), and the VPN checks are SKIP.

## The config

`tools/smoke.json` (or `--config FILE`): JSON, because Python 3.9 has no TOML reader. Keys that start with `_` are
comments. Every value is checked against a strict pattern before anything runs, and a value that does not fit stops
the run (exit 2): no value of the file reaches a shell unchecked.

| field | meaning |
|---|---|
| `name` | the board's name in the report and for `--board`: letters, digits, `_`, `-` |
| `device` | `f50`, `u30air` or `v50`: what `mu300-device` must say |
| `system` | `ubuntu`, `openwrt`, `openwrt-luci` (what runs now), or `android` for a board in Android (Magisk path only) |
| `ssh.host`, `ssh.port`, `ssh.user` | where to log in (port 22 and `root` by default) |
| `ssh.fingerprint` | required: `SHA256:...` of the board's host key, as `ssh-keygen -lf` or `ssh-keyscan HOST \| ssh-keygen -lf -` prints it |
| `ssh.hostname` | optional: what `/proc/sys/kernel/hostname` must be |
| `sudo` | run the device side through `sudo -n` (default: when the user is not root; Ubuntu's `ubuntu` needs sudo without a password for this) |
| `adb_serial` | optional: the board's serial in Linux (`MU300LINUX-...`, docs/ADB-INTERFACE.md); it must be connected |
| `android_serial` | the board's serial in Android (the bare number): the Magisk path |
| `allow_android` | `true` lets the harness send the board to Android (the Magisk path). Default `false`; a U30 Air, or any board, is never sent there without it |
| `update` | `online` (default), `mirror`, `from` or `magisk` |
| `mirror_url` | `mirror`: `http(s)://host[:port]/path` or `file:///path`; the tag is added |
| `from_path` | `from`: an absolute directory on the device |
| `magisk_zip` | `magisk`: the zip's system and kernel, e.g. `openwrt-luci-k6.18`, `ubuntu-24.04-k7.2`, `openwrt-k7.3`; it must install the system the board runs |
| `apply_args` | a list of `--unsigned`, `--downgrade`, `--no-rollback`, `--no-reinstall`, `ubuntu`, `openwrt`, `openwrt-luci` |
| `expect_kernel` | optional: a prefix of `uname -r` after the update, e.g. `6.18` or `7.2.10` |

`timeouts` (seconds): `command` 120 (each short command), `apply` 3600, `down` 300, `boot` 900, `settle` 60, `push`
900 (scp and adb push), `install` 1800 (the zip), `poll` 10 (between tries while waiting).

## What it does not do

These stay manual after a release:

- **Build and publish**: `make-release.sh`, the Release workflow, the Magisk workflow, the release notes, the
  promotion (`gh release edit`); the release audit runs in `make-release.sh`.
- **Getting a board into a known state** before the run (the previous release installed, the VPN configured, a SIM
  in), and putting it back afterwards (`mu300-update rollback`, `rollback-boot`, an older Magisk zip).
- **A fresh installation** with `install.sh`, `install.ps1`, the graphical or the web installer, and a Magisk zip on
  a device without Linux (whose SSH host key is not known before).
- **The panels**: LuCI's System > Update page, Cockpit, the web installer's device manager.
- **What it only sees from outside**: that clients can join the hotspot and reach the internet through it, that the
  VPN carries traffic, SMS, calls, the LEDs and buttons, the second SIM, roaming, battery and charging.
- **The kernel change** (`mu300-update kernel`) and the rollback paths.
- **Windows and macOS installers** and their CI.

If a board does not come back (`reboot` FAIL), it may have fallen back to Android after its failed boots
(`mu300-next-boot attempts`); that is the device's own safety net, not something the harness did.
