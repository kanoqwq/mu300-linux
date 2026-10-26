# An update keeps everything: the test on a device

What docs/FINDINGS.md 85 measured in containers (and `tests/update-experiment` on the images of a real release
build), to be run on the **F50 with Ubuntu** and the **U30 Air with OpenWrt and the control panel** in one go,
before the release that brings it is promoted. Run on both devices on 2026-10-16 (FINDINGS 85, "On the devices"; part 7 on a third F50 with OpenWrt, the same day). Every step says what to
expect; what differs goes into FINDINGS 85 with the device, the release and the kernel.

Two updates are measured on each device: **the first one**, from the newest published release (v2026.10.27: no
carry block, no Update page, no release key) to a release of this branch - the old updater hands over to the new
one, and the old image's manifest comes from the new image - and **a second one**, between two releases of this
branch, where the old system has its own manifest; on the U30 Air that one goes through the panel's
System > Update page. The way back is `mu300-update rollback` (and `rollback-boot`), or the old release's Magisk
zip; take the backup of `install.sh --check` first if a device matters.

## 0. Before: two test releases, the mirror, the old state

**Two releases of the branch**, built and never published, with tags newer than every release and test tag the
devices ever had, the boot image included (`mu300-update status`: `"boot"`; the U30 Air ran test tags up to
v2026.10.31 for FINDINGS 86) - an older tag is refused as a downgrade, which is right. Say `T1=v2026.11.29`,
`T2=v2026.11.30`:

```sh
for t in $T1 $T2; do
    MU300_KERNEL_OUT=<the 5.4 out/> MU300_RELEASE_KEY_FILE=~/.config/mu300-linux/release.sec \
    MU300_OPENSSL=/opt/homebrew/opt/openssl@3/bin/openssl sh tools/make-release.sh $t        # no --publish
    sh tools/make-magisk-zips.sh release/$t release/$t $t
done
```

Each audit must pass (every image has `opt/mu300/bin/mu300-carry`, its own manifest and the manifests of the older
releases), and each release is signed (`SHA256SUMS.sig`): the images of the branch carry the project's key, so the
second update refuses an unsigned release. The mainline kernels must be built at the branch's commit (or their
`upstream/mu300-mainline.config` audit fails); the release dir the PR's agent built (`release/<tag>`, a three-field test tag after v2026.10.27) with
v2026.10.27's mainline kernels is fine for containers, not for this.

**The mirror** on the computer, laid out as in FINDINGS 86 so that the page's *Check* works too. The devices reach
it over USB (F50 `192.168.77.x`, U30 Air `192.168.78.x`; `PC` below is the computer's address on each device's
network):

```sh
R=dikeckaan/mu300-linux
mkdir -p mirror/api/releases mirror/download
for t in $T1 $T2; do cp -R release/$t mirror/download/$t; done
gh release download v2026.10.27 -R $R -D mirror/download/v2026.10.27 -p SHA256SUMS -p 'mu300-*'
# "latest" is T2 (the page offers it); the list has T2 on top (the asset sizes are v2026.10.27's: the page's
# download figure is a few percent off)
gh api "repos/$R/releases/tags/v2026.10.27" | jq --arg t $T2 '.tag_name = $t | .name = "MU300 Linux " + $t' > mirror/api/releases/latest
gh api "repos/$R/releases?per_page=10" | jq --slurpfile l mirror/api/releases/latest '$l + .' > mirror/api/releases/index.html
python3 -m http.server 8000 -d mirror &
```

**The old state.** Both devices on v2026.10.27 with every system they have: `mu300-update version`. A device on a
test tag gets there with v2026.10.27's Magisk zip for its system and kernel (that installer predates the downgrade
rule, so it takes an older release; it keeps only the old list of places, so plant after it).

**The experiment's script** on each device, in a directory beside the systems that no update touches:

```sh
# F50
scp tests/update-experiment/experiment.py ubuntu@192.168.77.1:/tmp/
ssh ubuntu@192.168.77.1 'sudo mkdir -p /mnt/mu300-disk/update-test && sudo mv /tmp/experiment.py /mnt/mu300-disk/update-test/'
# U30 Air, as root. OpenWrt has no Python: apk add python3 (the data goes through the VPN in the owner's region, so
# the VPN must be up). python3 is then a package the user added: it is installed again at each first boot.
ssh root@192.168.78.1 'mkdir -p /mnt/mu300-disk/update-test'
scp tests/update-experiment/experiment.py root@192.168.78.1:/mnt/mu300-disk/update-test/
ssh root@192.168.78.1 'apk update && apk add python3'
```

## 1. Plant (both devices)

On each device as root (`sudo -i` on the F50), `T=/mnt/mu300-disk/update-test`:

```sh
mu300-update version | tee $T/version.before
# F50: real packages, so that the reinstall at the first boot has something to do
apt-get install -y hello tree
# the user state, some sixty kinds of it, written down with every attribute. --live leaves the login alone (no
# password is changed, no package is faked)
python3 $T/experiment.py plant / ubuntu $T/sys.json --live              # F50
python3 $T/experiment.py plant / openwrt $T/sys.json --live             # U30 Air (openwrt-luci is of kind openwrt)
# what an update of one system must not touch
find /mnt/mu300-disk/extra -type f -exec sha256sum {} + | sort -k2 > $T/extra.before
df -m /mnt/mu300-disk | tee $T/df.before
du -sm / --exclude=/proc --exclude=/sys --exclude=/mnt 2>/dev/null | tee $T/du.before     # (busybox: du -sm /)
```

## 2. The first update: v2026.10.27 to T1, from a terminal (both devices)

```sh
time MU300_RELEASE=$T1 MU300_RELEASE_URL=http://PC:8000/download/$T1 mu300-update apply 2>&1 | tee $T/apply1.log
```

Expected, in order: `continuing with the mu300-update of T1` (v2026.10.27's updater hands over); for each system
`carrying your files over`, `no list of the files of ...` must **not** appear (the manifests of v2026.10.27 are in
T1's image), the report (`==== ubuntu: v2026.10.27 -> T1 ...`, `Everything you added to the system was carried
over: N files, M MB`), the lists of what was set aside (the edited `/usr/lib/systemd/system/ssh.service` and
`/www/index.html`, and nothing unexpected); `boot: updated to T1`. Write down the seconds between `carrying your
files over` and `switching over` for each system (the scan and the hashing of the old system, not measured on a
device yet). The free-space check before the download counts no copy of your files on this update (v2026.10.27 has
no manifest of its own: the carry block counts after the unpack); on the second it does.

Before the reboot:

```sh
# the copy kept for a rollback is the old system to the last attribute: no line but "kept"
python3 $T/experiment.py verify /mnt/mu300-disk/<os>.old $T/sys.json | cut -f1,2,4 | grep -v '	kept	'
python3 $T/experiment.py verify /mnt/mu300-disk/<os> $T/sys.json > $T/after1.tsv
cut -f2 $T/after1.tsv | sort | uniq -c        # no "lost"; "changed" and "held" only where the report says so
reboot
```

After the reboot (U30 Air: python3 is back once `mu300-user-packages status` says so; until then, run the verify
of the new system from the old one before the reboot, as above):

```sh
T=/mnt/mu300-disk/update-test
mu300-update version; mu300-update report | head -60          # the login showed the note once; now it is gone
python3 $T/experiment.py verify / $T/sys.json | cut -f1,2,4 | grep -v '	kept	'
mu300-user-packages status                                     # F50: hello and tree; U30 Air: python3
# F50
systemctl --failed; journalctl -b -p err --no-pager | tail -30; mobile-data status; ip -br a
# U30 Air
uci show network | head; logread | grep -i -e error -e fail | tail; cat /etc/crontabs/root
ls -l /etc/rc.d/S99myservice; /etc/init.d/myservice enabled && echo enabled
# both: the extras are as they were (the VPN module may have been brought to its latest release: that is apply's job)
find /mnt/mu300-disk/extra -type f -exec sha256sum {} + | sort -k2 | diff - $T/extra.before
```

## 3. The second update: T1 to T2

**F50, `apply --from` the Magisk zip, offline** (mobile data off, no other route out):

```sh
scp release/$T2/mu300-magisk-$T2-ubuntu-24.04-k<kernel>.zip ubuntu@192.168.77.1:/tmp/x.zip
sudo mu300-update inspect /tmp/x.zip        # "signature":"valid","fits":1,"tag":"T2"
sudo mu300-update apply --from /tmp/x.zip 2>&1 | tee $T/apply2.log
```

Expected: `signed with the project's release key`, no download, the carry and report as in part 2 with `T1 ->
T2`; the files planted are all `kept` again (python3 `verify` as in part 2). The free-space line, if it comes, now
counts the copy of your files.

**U30 Air, the page** (`http://192.168.78.1/cgi-bin/luci/admin/system/mu300-update`, System > Update), online from
the mirror. Point the device at it (root's own file, mode 600):

```sh
ssh root@192.168.78.1 "printf 'MU300_API_URL=http://PC:8000/api\nMU300_DOWNLOAD_URL=http://PC:8000/download\n' > /etc/mu300/update-source; chmod 600 /etc/mu300/update-source"
```

* **Installed** shows openwrt-luci T1, the free space, and **Report of the last update** (part 2's): open while it is
  new, closed after `mu300-update report` in ssh.
* *Check for updates*: T2 is offered, with the download and **Free space needed** - `mu300-update releases`'
  `need_kb`: about 2 GB plus the copy of what was planted (write both down, and `du -sm` of what was planted).
* *Update to T2*, confirm. The log shows part 2's lines (`continuing with the mu300-update of T2`, `signed with the
  project's release key`, `carrying your files over`, the report, `boot: updated`). Close the page during the
  download and open it again: the task is there.
* *Restart now*. After the restart: **Installed** is T2, and **Report of the last update** is open, its top
  `==== openwrt-luci: T1 -> T2, ... ====` with part 2's update below it. A long one ends with `(the whole report:
  mu300-update report)`.
* Over ssh, part 2's checks; and the same once more **from a file** on the page: after part 4's rollback to T1,
  *Update from a file* with `mu300-magisk-T2-openwrt-luci-k<kernel>.zip` - the page shows its SHA-256 (the one in
  `SHA256SUMS-magisk`), T2, the system, the kernel, the signature valid; *Update from this file*, confirm, restart.

## 4. The way back (both)

```sh
mu300-update rollback && mu300-update rollback-boot && reboot        # U30 Air: or the page's buttons
# after the reboot: the system exactly as it was when it was updated
python3 $T/experiment.py verify / $T/sys.json | cut -f2 | sort | uniq -c       # all "kept"
```

## 5. An older release is never taken by itself (both)

With T2 installed:

```sh
MU300_RELEASE=$T1 MU300_RELEASE_URL=http://PC:8000/download/$T1 mu300-update apply
#   expected: "<os> is T2 and T1 is older: going back to an older release needs --downgrade, as the older
#   release's updater may not carry over everything you added to the system", "nothing was changed", nothing
#   downloaded. The same with MU300_DOWNGRADE=1 in front (that word is the Magisk zip's setting, not mu300-update's).
MU300_RELEASE=$T1 MU300_RELEASE_URL=http://PC:8000/download/$T1 mu300-update apply --downgrade
#   through, by T1's updater - which carries everything over (T1 has the carry block): the verify is all "kept"
```

Never `--downgrade` to v2026.10.27 on a device that matters: its updater keeps only the list of places. On the
page: upload T1's zip with T2 installed - the page asks for the downgrade tick, and without it the adapter refuses
("This file is older than what is installed: that needs confirmation").

## 6. F50: no room, and `--no-rollback`

```sh
df -m /mnt/mu300-disk
fallocate -l <the free space less 2.5 GB>M /srv/big.img         # a file of the user's that does not fit twice
MU300_RELEASE=$T2 MU300_RELEASE_URL=http://PC:8000/download/$T2 MU300_FORCE=1 mu300-update apply ubuntu
#   expected, before any download: "not enough free space on the Linux partition: N MB needed, M MB free (...)",
#   naming clean and --no-rollback; ls /mnt/mu300-disk: no ubuntu.new. (Should the estimate let it through, the
#   carry block stops after the unpack: "not enough room to carry your files over beside the previous system",
#   "nothing was changed".)
MU300_RELEASE=$T2 MU300_RELEASE_URL=http://PC:8000/download/$T2 MU300_FORCE=1 mu300-update apply --no-rollback ubuntu
#   expected: through; "The previous version is not kept (--no-rollback)"; /mnt/mu300-disk/ubuntu.broken is the
#   old system until the next apply or clean; rollback says "no previous version kept"
reboot      # then: ls -l /srv/big.img, part 2's verify, and "sudo mu300-update clean" removes ubuntu.broken
```

## 7. F50: the Magisk zip from Android, and the computer installer (if there is time)

* From Android (`sudo mu300-next-boot android && sudo reboot`), T1 installed:

  ```sh
  adb push release/$T2/mu300-magisk-$T2-ubuntu-24.04-k<kernel>.zip /sdcard/Download/
  adb shell 'echo MU300_DRY_RUN=1 > /sdcard/mu300-install.conf'
  adb shell su -c "magisk --install-module /sdcard/Download/mu300-magisk-$T2-ubuntu-24.04-k<kernel>.zip"
  #   the plan: "filesystem: keep: nothing is erased", "update: ubuntu T1 -> T2: everything you added to it or
  #   changed in it is kept ...", "not touched: <the other systems> and the extras ..."
  adb shell rm /sdcard/mu300-install.conf
  adb shell su -c "magisk --install-module /sdcard/Download/mu300-magisk-$T2-ubuntu-24.04-k<kernel>.zip" | tee zip.log
  adb reboot
  ```

  After the reboot into Linux: part 2's verify, the old password still logs in, `mu300-update report`. T1's zip over
  T2 is refused (`The installed ubuntu is T2, and this zip is T1, an older release`) unless `MU300_DOWNGRADE=yes` is
  in `/data/adb/mu300-install.conf`.
* The computer installer: `MU300_RELEASE_URL=http://127.0.0.1:8000/download/$T2 ./install.sh`, answer **update**:
  `[device] carrying everything of yours in the previous ubuntu over`, the report. No `<os>.old` on this path.
  An older release's package is refused on the device before anything is written (`the installed ubuntu is T2, and
  this installation is T1, an older release`); `MU300_DOWNGRADE=yes` in front of `install.sh` allows it.
* In answers mode set `ANDROID_SERIAL` to the board in Android and leave `MU300_ANS_REBOOT_ANDROID` unset: a run
  started while Android is still coming up stops at that question instead of waiting (FINDINGS 85, part 7).

## 8. Afterwards, and what to bring back

```sh
ssh root@192.168.78.1 'rm -f /etc/mu300/update-source'      # the U30 Air takes GitHub again
```

Each device back on the release the owner wants (the published one, or T2), `mu300-update clean` once the kept
copies are not needed. Bring back: `$T/*.log`, the `*.tsv` of each verify, `/etc/mu300/update-report` of each
system, the seconds of the carry step, `df -m` before and after, the "Free space needed" of the page and of
`apply` against the real size of what was planted, and every line of the reports this page did not expect: lines
under `Back, though you had deleted them` or `Deleted again` that nobody deleted, and files under `Yours is in use
where the new release has another version` that nobody touched (files the system rewrites at run time are
expected for `/etc/config/*` on OpenWrt; anything else is to be looked at).
