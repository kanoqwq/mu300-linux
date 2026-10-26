# Driving the installers from a program

`install.sh`, `install.ps1` and the two uninstallers are made for a person at a terminal. They can also be run by a
program - a CI job, the graphical installer in `gui/`, a web installer - without changing what they do: the
program supplies the answers, and reads what happens from a progress stream. Nothing of the install logic is
duplicated outside the scripts.

Two things, independent of each other:

* **answers mode**: nothing is read from the terminal; every question is answered by a variable;
* **the progress stream**: one line of JSON per event, next to the text for people.

The code is `tools/answers.sh` (for `install.sh`, `uninstall.sh`) and `tools/answers.ps1` (for `install.ps1`,
`uninstall.ps1`). Tests: `tests/test_answers.py` and `tests/answers.Tests.ps1`, against the fake adb of
`tests/fakeadb.py`.

## Answers mode

| | macOS / Linux | Windows |
|---|---|---|
| answers from a file | `./install.sh --answers FILE` or `MU300_ANSWERS=FILE` | `.\install.ps1 -AnswersFile FILE` or `$env:MU300_ANSWERS` |
| answers from the environment | `MU300_NONINTERACTIVE=1 ./install.sh` | `.\install.ps1 -NonInteractive` or `$env:MU300_NONINTERACTIVE = '1'` |

The rules:

* **Each question has one variable.** It is the last argument of the question's `ask` / `Ask` in the script.
* **A question whose variable is not set stops the run** with an error that names the variable. Nothing is defaulted
  silently, and nothing is read from standard input in its place.
* **A variable set to nothing takes the question's default.** No default is the destructive answer: the words
  `INSTALL`, `ERASE`, `overwrite`, `internal` and `UNINSTALL` have to be given.
* **A question that comes back stops the run.** Some questions repeat until they like the answer (the kernel, the
  size when making room); the same variable would answer for ever, so the second time is an error: `the answer in
  MU300_ANS_KERNEL was not accepted: 5.4`.
* **`@stdin` as a value** puts the question on the progress stream (`"state":"waiting"`) and reads one line from
  standard input. A program uses it to decide while the installer runs: the graphical installer sets
  `MU300_ANS_CONFIRM=@stdin`, shows the installer's own summary, and sends `INSTALL` only then.
* Answers typed in the installer's other languages count as they do at the terminal (`evet`, `是`).

The file is `NAME=value` lines; the value is taken as it is to the end of the line (no quotes, no expansion), `#`
starts a comment. Only `MU300_*` and `ANDROID_SERIAL` are accepted; any other name is an error. The variables of a
file are exported to the programs the installer starts (adb reads `ANDROID_SERIAL`), except the secrets.

### Secrets

The passwords are secrets: `MU300_ANS_PASSWORD`, `MU300_ANS_CURRENT_PASSWORD` and
`MU300_ANS_CURRENT_PASSWORD_UBUNTU`, `_OPENWRT`, `_OPENWRT_LUCI`. The scripts keep the list in one place (`ANSWERS_SECRET` in
`tools/answers.sh`, `$script:MuSecrets` in `tools/answers.ps1`), and for every name in it:

* the value is never printed: the transcript shows `******`, an event carries `"secret":"1"` and no answer, and an
  error message about it shows `******`;
* it is not handed to the programs the installer starts. From a file it is never exported; given in the
  environment it is taken out of the environment before anything is started, so adb, curl and python do not
  inherit it. It reaches one command only, on its standard input: the one that hashes it. The device gets the hash;
  a current password goes to the device in a file of the user's alone, which `tools/android-pwcheck.sh` reads on its
  standard input and the same command removes, and only its one-word verdict comes back;
* a line of the file that is not `NAME=value` is reported by its number, not by its text.

**The answers file holds the password in plain text.** `install.sh` and `uninstall.sh` refuse a file that is a
link, that another user owns, or that its group or anyone else can read or write: create it with `umask 077` (or
`chmod 600`) before the password goes in. On Windows there is no such check: keep the file in your own profile
folder, not in a folder other users can read. Delete it when the run ends - the graphical installer does, whatever
way the run ends. Prefer the file to the environment for the password: other processes of the same user can read a
process's environment while it starts.

`install.ps1 -Answers FILE` is older and different: one answer per line, in the order of the questions. It stays as
it is; it never gets the beta question, which would shift its lines.

### The variables of `install.sh` / `install.ps1`

A question is asked only where the installer asks it by hand too.

| variable | values | the question |
|---|---|---|
| `MU300_LANG` | `en`, `tr`, `zh` | the language (`-Lang`) |
| `ANDROID_SERIAL` | a serial of `adb devices` | which device, with more than one attached, or one that is no F50/U30 Air (or `MU300_ANS_DEVICE`: its number in the list) |
| `MU300_ANS_REBOOT_ANDROID` | `yes`, `no` | the device runs Linux: reboot it into Android over SSH (needs a terminal for the device's password) |
| `MU300_ANS_UNKNOWN_MODEL` | `yes`, `no` | not an F50 or U30 Air: continue anyway |
| `MU300_STORAGE` | `internal`, `sd` | with an SD card: where the Linux filesystem goes |
| `MU300_ANS_INTERNAL_OVER_CARD` | `internal` | internal storage although the card holds an installation |
| `MU300_ANS_MAKE_ROOM` | `yes`, `no` | no free space: shrink `userdata` (erases Android's data) |
| `MU300_ANS_LINUX_GIB` | a number, `4` or `2.5` | then: how many GiB for Linux |
| `MU300_ANS_ERASE_ANDROID` | `ERASE` | then: the word that allows it |
| `MU300_ANS_REBOOT` | `yes`, `no` | then: reboot (into the reset by Android's recovery; the installation resumes on the next run) |
| `MU300_ANS_OVERWRITE` | `overwrite` | the free space holds data: use it anyway |
| `MU300_ANS_BETA` | `yes`, `no` | a prerelease newer than the newest release: install it. `MU300_RELEASE=<tag>` (`-Release`) names the release and asks nothing |
| `MU300_ANS_SYSTEMS` | `1`, `2`, `3` | Ubuntu, OpenWrt, both |
| `MU300_OPENWRT` | `plain`, `luci` | which OpenWrt |
| `MU300_ANS_BOOT_OS` | a chosen system | with two systems: the one that boots |
| `MU300_ANS_UBUNTU` | `24.04`, `26.04` | which Ubuntu |
| `MU300_ANS_DEFAULT_LINUX` | `yes`, `no` | boot Linux by default |
| `MU300_ANS_BOOT_ATTEMPTS` | `1`..`6` | failed boots before Android |
| `MU300_ANS_HOTSPOT` | `yes`, `no` | copy Android's hotspot name and password |
| `MU300_ANS_GPU` | `yes`, `no` | the Mali GPU userspace |
| `MU300_ANS_VPN` | `yes`, `no` | the VPN module |
| `MU300_ANS_ADB` | `yes`, `no` | adb to Linux is on for every computer: let in this computer's key only |
| `MU300_ANS_PANEL` | `yes`, `no` | the web panel for Ubuntu |
| `MU300_ANS_KERNEL` | `5.4`, `6.18`, `7.2`, `7.3` | the kernel |
| `MU300_ANS_EXISTING` | `update`, `wipe` | Linux is installed already |
| `MU300_ANS_ERASE_SD` | `ERASE` | a new filesystem on the SD card erases it |
| `MU300_ANS_KEEP_PASSWORD` | `yes`, `no` | an update of systems with a login password of their own: keep it (the current one is then asked and checked on the device) or set a new one |
| `MU300_ANS_CURRENT_PASSWORD` | the current password, empty for none | with keep: checked against the installed system's hash; one try in answers mode, and a wrong one stops the run before anything is written. `MU300_ANS_CURRENT_PASSWORD_UBUNTU`, `MU300_ANS_CURRENT_PASSWORD_OPENWRT`, `MU300_ANS_CURRENT_PASSWORD_OPENWRT_LUCI` answer for one system and win over it |
| `MU300_ANS_PASSWORD` | 6 characters or more | the password of `ubuntu` (Ubuntu) and `root` (OpenWrt); not asked when an update keeps the password of every system it installs |
| `MU300_ANS_CONFIRM` | `INSTALL` | the summary's question |

The variables that were there before work as they did (`MU300_WORK`, `MU300_RELEASE_URL`, `MU300_SYNC_BOOTCHAIN`,
`MU300_VPN_URL`, `MU300_VPN_MODULE`, `MU300_FETCH_JOBS`, `MU300_NO_SELF_UPDATE`); `install.ps1` now reads
`MU300_LANG`, `MU300_RELEASE`, `MU300_RELEASE_URL` and `MU300_WORK` where its parameters are not given.

**The Magisk zip on the device** asks nothing (nothing can be typed while Magisk installs it). Its answers are a
`mu300-install.conf` (README, "Choosing something else"); for the password of an update only one inside the zip
counts - at the top of the zip, or `MU300_ANSWERS` of a caller such as `tools/android-web.sh`: `MU300_KEEP_PASSWORD`
(`yes`, `no`), `MU300_CURRENT_PASSWORD`, `MU300_PASSWORD`, `MU300_PASSWORD_RESET`. Without them an update keeps the
password unchecked. The plan line `MU300-PLAN password=kept` is followed by `password_checked=1` when the current one
was checked.

**An unpacked Magisk zip** (`MU300_PACKAGE=DIR`, `--package DIR` / `-Package DIR`, or the zip's own `computer/`
with its `.mu300-package`; `tools/package.sh`): the system, the Ubuntu, the kernel and the release are the zip's, so
`MU300_ANS_SYSTEMS`, `MU300_OPENWRT`, `MU300_ANS_BOOT_OS`, `MU300_ANS_UBUNTU`, `MU300_ANS_KERNEL`, `MU300_ANS_BETA` and
`MU300_ANS_PANEL` are never asked (and `MU300_RELEASE`, `MU300_RELEASE_URL` are not used): a program sets only the
others. The `download` step checks the two files of the zip instead (`verify` events, `ok` or `mismatch`), and a
manifest that is not one of a release's stops the run before the device is asked anything. The graphical installer
started from such a folder offers that system and kernel only.

**A prepared installation** (making room by a reset, EXPERIMENTAL; `tools/resume.sh`, `tools/resume.py`):
`MU300_BACKUP_DIR` (default `~/mu300-backups`) is where the transaction and the backup go; `MU300_RESUME=0`
(`--no-resume` / `-NoResume`) ignores a waiting transaction, `MU300_RESUME=1` (`--resume` / `-Resume`) stops when
there is none for the device, `MU300_TRANSACTION=FILE` (`--transaction` / `-Transaction`) names one. A resumed run
asks no `MU300_STORAGE` and no `MU300_ANS_OVERWRITE` (the region is the prepared one); a transaction that does not
match stops the run before anything is written, with the reason in the error event.

### The variables of `uninstall.sh` / `uninstall.ps1`

| variable | values | the question |
|---|---|---|
| `ANDROID_SERIAL`, `MU300_ANS_DEVICE`, `MU300_ANS_REBOOT_ANDROID` | as above | |
| `MU300_ANS_WIPE` | `secure`, `quick`, `keep` | the Linux filesystem on the eMMC |
| `MU300_ANS_SD_WIPE` | `erase`, `keep` | the Linux filesystem on the SD card |
| `MU300_ANS_CONFIRM` | `UNINSTALL` | the summary's question |
| `MU300_ANS_GROW` | `yes`, `no` | `uninstall.sh` only: give the space behind `userdata` back to it (erases Android's data) |
| `MU300_ANS_ERASE_ANDROID` | `ERASE` | then: the word that allows it |

## The progress stream

`MU300_PROGRESS=1` writes the events to standard output, between the lines for people. `install.sh` and
`uninstall.sh` can write them to another descriptor instead: `MU300_PROGRESS_FD=3 ./install.sh ... 3>events`.

Each event is one line:

    @@mu300 {"event":"step","id":"download","percent":"20","title":"Downloading release v2026.10.23"}

Every value is a string. Text is UTF-8 (`install.ps1` writes characters outside ASCII as `\uXXXX`). A line end or a
tab inside a value becomes a space. **The last event of a run is `done` or `error`**, whatever ends it.

| event | fields | meaning |
|---|---|---|
| `hello` | `script`, `version` (`1`), `mode` (`check`, `backup`, `prebuilt`, `build`, `uninstall`) | the first event |
| `step` | `id`, `percent`, `title` | a heading of the run. `id` and `percent` are empty for a heading that is no stage of its own |
| `info` | `key`, `value` | a fact, see below |
| `question` | `id` (the variable), `text`, `default`, `answer` | a question and what it was answered with. With `"state":"waiting"` instead of `answer`: it waits for a line on standard input. `"secret":"1"`: the password, never with its answer |
| `download` | `file`, `bytes`, `total`, `state` (`start`, `running`, `done`, `failed`) | once a second while a file comes in; speed and time left are for the reader to work out. `install.ps1` says `running` only for the large files it fetches in parts |
| `verify` | `file`, `result` (`ok`, `cached`, `mismatch`) | the sha256 of a downloaded file against the release's `SHA256SUMS`; `cached`: a file of an earlier run that still matches |
| `error` | `message` | the run stops |
| `done` | `result` (`ok`), after an installation `boot_os`, `systems`, `ip`, `release`, `kernel` | the run is complete |

The stages of an installation, in order: `preflight` 2, `storage` 6, `choices` 10, `pull` 15, `download` 20,
`vendor` 60, `bootimage` 68, `confirm` 72, `copy` 75, `device` 80, `boot` 92, `switch` 96, `done` 100
(`repartition` 50 on the make-room path, which ends the run). Those of an uninstallation: `preflight`, `find`,
`choices`, `confirm`, `misc`, `boot`, `erase`, `sd`, `userdata`, `switch`, `done`.

### Facts (`info`)

| key | value |
|---|---|
| `device.model` | `ro.product.model / ro.product.device` |
| `device.kind` | `f50`, `u30air`, `unknown` |
| `android.build` | Android's build (`ro.build.display.id`) |
| `bootloader.locked` | `ro.boot.flash.locked`: `0` unlocked, `1` locked, empty when the device does not say |
| `bootloader.device_state` | `ro.boot.vbmeta.device_state`: `locked`, `unlocked`, or empty |
| `bootloader.state` | what the installer makes of the two: `locked`, `unlocked`, `unknown`. Locked is a warning in the text and never a refusal: a locked bootloader with a patched TrustOS boots Linux |
| `bootloader.verified` | `ro.boot.verifiedbootstate`: `green`, `yellow`, `orange`, `red` |
| `root` | `yes`, `no`: whether `su` works |
| `root.su` | what `su -v` says, for example `27.0:MAGISKSU` |
| `slot.android`, `slot.linux` | `a`, `b` |
| `work` | the work directory on this computer |
| `bootchain` | the boot chain line of the plan |
| `emmc.sectors`, `emmc.last_end` | the eMMC's size, and where its last partition ends, in 512-byte sectors |
| `partitions` | `name:first sector:sectors` of every partition, separated by spaces |
| `region.offset`, `region.size` | the free region (or the installation in it), in bytes |
| `need.openwrt`, `need.ubuntu`, `need.both` | what each choice of systems needs, in bytes |
| `region.existing` | `yes` when a `mu300root` filesystem is there |
| `region.dirty` | how many of 16 samples of an empty region hold data |
| `sd.device`, `sd.bytes`, `sd.state` | the SD card, and `yes` (`mu300sd`), `foreign` (another ext4) or `no` |
| `storage` | `internal`, `sd`: where this run installs |
| `verdict`, `verdict.code` | the result line of the check, and `tested`, `both`, `one`, `openwrt`, `existing`, `sd`, `dirty`, `sd-only` |
| `makeroom.first`, `.end`, `.usable_end`, `.min_android`, `.min_linux`, `.max_linux` | where `userdata` fills the disk: its sectors, and the limits of a split, in sectors |
| `release` | the release this run installs |
| `password.<system>` | an update that keeps the password: the device's verdict on the current one, `MATCH`, `NOMATCH`, `LOCKED`, `NOUSER` or `UNVERIFIABLE` (never the password or its hash) |
| `package`, `package.system`, `package.kernel`, `package.release` | an unpacked Magisk zip this run installs from: its folder, and what it holds |
| `makeroom.crypto`, `.crypto_state`, `.crypto_type` | the make-room path: whether Android's data is encrypted (`yes`, `no`, `unknown`: kano's rule), and `ro.crypto.state` / `ro.crypto.type` |
| `resume.transaction`, `resume.check`, `resume.backup` | a prepared installation: its transaction, what its checks said (`RESUME-OK` or `RESUME-REFUSED: why`), the backup made before it |
| `backup.boot`, `backup.misc`, `backup.sha256` | `--backup`: the two files and the boot image's hash |

The facts before `root` need no root, and they are sent before the check for it: a device without root still says
what it is and how its bootloader stands.

## The read-only modes

| | macOS / Linux | Windows | |
|---|---|---|---|
| look at the device | `./install.sh --check` | `.\install.ps1 -Check` | writes nothing; with the stream, the facts above |
| back up | `./install.sh --backup` | `.\install.ps1 -Backup` | copies Android's boot image (`boot_a`, or `boot_b` when Android runs from b) and the first block of `misc` into `work/dumps`, and compares the image with the device's. Nothing else is read: the NV and calibration partitions are never copied by the installers |
| which adb | `./install.sh --adb-path` | `.\install.ps1 -AdbPath` | prints the adb the installer would use (PATH, then platform-tools next to the project and in the usual places); exit 1 without one |
| which release | `python3 tools/release_pick.py --json < releases.json` | the same | the newest release and the beta, if any, from GitHub's `GET /repos/<repo>/releases` |

## Examples

A check, as JSON lines only:

```sh
MU300_NONINTERACTIVE=1 MU300_LANG=en MU300_PROGRESS_FD=3 ./install.sh --check 3>&1 >/dev/null
```

An installation without a terminal (it erases the Linux region; read the summary of a run by hand first):

```sh
umask 077
cat > answers.txt <<'EOF'
MU300_LANG=en
MU300_ANS_SYSTEMS=1
MU300_ANS_UBUNTU=24.04
MU300_ANS_DEFAULT_LINUX=yes
MU300_ANS_BOOT_ATTEMPTS=5
MU300_ANS_HOTSPOT=yes
MU300_ANS_GPU=yes
MU300_ANS_VPN=no
MU300_ANS_ADB=no
MU300_ANS_PANEL=no
MU300_ANS_KERNEL=6.18
MU300_ANS_PASSWORD=choose-your-own
MU300_ANS_CONFIRM=INSTALL
EOF
./install.sh --answers answers.txt; rm -f answers.txt
```

On a device with something the file does not answer - an SD card, an installation that is already there, data in
the free space, a beta - the run stops and names the variable; nothing has been written by then.
