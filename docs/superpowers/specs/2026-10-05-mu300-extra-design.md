# mu300-extra: optional components downloaded on demand (design)

Date: 2026-10-05. Branch `mu300-extra` (on top of driver-fixes, PR #28 stack).

## Problem

The user (translated): "the VPN tool should not come embedded in the system; it should be downloadable with
mu300-extra". Today every image carries Xray-core (34 MB), hev-socks5-tunnel (0.4 MB) and sing-box (86 MB) in
`/opt/mu300/bin`, about 120 MB per system whether the VPN is used or not. Second, smaller problem: `mu300-ussd`
(and `sms`) are not on the PATH of OpenWrt (and Ubuntu): only `/etc/profile.d` adds `/opt/mu300/bin`,
which a non-login shell (`ssh root@... mu300-ussd`) and sudo never see.

## Decisions

1. **What an extra is.** A release asset `mu300-extra-<name>.tar.gz` built by `tools/make-extra.sh`, listed in the
   release's `SHA256SUMS` like every other asset. Layout: `./name`, `./release` (the tag), `./components` (what is
   inside, with versions), `./bin/<commands>`. One extra exists: **vpn** = xray + hev-socks5-tunnel + sing-box. It
   is not split: mu300-vpn falls back from one engine to the other and the kill switch needs sing-box, so a VPN
   with only one of them is a half-working VPN. Only arm64 exists (both devices), so the name carries no arch.
2. **Where it comes from.** Only from a release, verified against that release's SHA256SUMS with mu300-update's
   own `fetch_verified` - never from a third-party URL at run time. The engines' upstream downloads stay pinned by
   hash in `tools/fetch-xray.sh` and `tools/fetch-sing-box.sh`, run at release build time. Test and offline hooks:
   `MU300_EXTRA_FILE=<tarball>` (a local file, in the style of `MU300_KERNEL_BUNDLE`), `MU300_RELEASE_URL=<base>`
   (another server with the same files and SHA256SUMS, as `install.sh` already has), `MU300_RELEASE=<tag>`.
   Which release: `MU300_RELEASE`, else the release of the running system (`/etc/mu300/image-version`), else (a
   `dev` image, or a release from before extras) the newest one.
3. **Where it goes.** `/mnt/mu300-disk/extra/<name>/` - on the Linux partition, next to `ubuntu/` and `openwrt/`,
   not inside a system. Both systems see the same copy (installed once, ~120 MB once), and nothing that replaces a
   system (mu300-update, the installer's "update", rollback) touches it; only a wipe does. Installation unpacks to
   `.<name>.new` and renames, so an interrupted install never leaves half an extra.
4. **PATH.** One list of the system's commands, `/opt/mu300/lib/path-commands`, read by `rootfs/assemble.sh`,
   `openwrt/build-rootfs.sh` and at boot by `mu300-extra link` (replaces rootfs-fixups' copy of the list;
   OpenWrt's `mu300-post` runs it too, so installed OpenWrt systems get new links without an update). It adds
   `mu300-ussd`, `sms`, `mu300-extra`. `mu300-extra link` also links the commands of installed extras
   (`xray`, `sing-box`, `hev-socks5-tunnel`) into the PATH directory (Ubuntu `/usr/local/bin`, OpenWrt `/usr/bin`)
   and removes links into the extras that no longer resolve. mu300-vpn itself does not use the PATH: it looks in
   `/mnt/mu300-disk/extra/vpn/bin` first and then `/opt/mu300/bin` (images from before this change).
5. **mu300-update.** Installed extras are part of the update: `apply` downloads `mu300-extra-<name>.tar.gz` of the
   same release for every installed extra whose release differs, in the download phase (nothing is changed when a
   download fails), and installs them after the systems. `check` lists them.
6. **Existing installations with the VPN in use.** A system whose `/etc/mu300/vpn.conf` says `ENABLE=1` and that has
   no vpn extra gets one with the update - downloaded with the rest, before anything changes. If installing it fails
   after the switch, the engines are copied from the previous system (`<os>.old/opt/mu300/bin`) instead
   ("adopt"). A release without the asset (older) still has the engines in its images: nothing to do.
   Last line of defence, for every other path (an old installer, an updater from before self-update, a manual
   copy): `mu300-vpn run` with `ENABLE=1` and no engine adopts them from `<os>.old`, else runs
   `mu300-extra install vpn`, before the kill switch goes on. A VPN that was configured keeps working.
7. **Missing engine message.** `mu300-vpn run|gen` without an engine: `the VPN engines are not installed: run
   mu300-extra install vpn`; `status` shows the same line. The toolkit's "Turn on" installs the extra first.
8. **Installers.** `install.sh` / `install.ps1` ask "Install the VPN extra (Xray, sing-box; about N MB)?",
   default **no** (the point is that a system without VPN does not carry it). Yes: the extra comes from the same
   release download (verified with the same SHA256SUMS), is pushed and `android-install.sh` unpacks it into
   `extra/vpn` - offline, nothing on the device downloads. In update mode a system whose vpn.conf has `ENABLE=1` and
   no extra gets the engines adopted from the system being replaced (also offline). `--build` builds the extra
   with `tools/make-extra.sh`.
9. **Images.** `rootfs/assemble.sh` and `openwrt/build-rootfs.sh` no longer take xray/hev/sing-box;
   `make-release.sh` builds `mu300-extra-vpn.tar.gz`, puts it into SHA256SUMS and the upload, and its audit fails
   if an image still carries an engine. Saved: about 120 MB per installed system, ~48 MB per compressed image asset
   (measured in FINDINGS / the report).

## Commands

```
mu300-extra list              the extras a release offers, and which are installed
mu300-extra status            installed extras: release, size, commands
mu300-extra install NAME      download (release of this system), verify, install, link
mu300-extra remove NAME [--force]   refuses the vpn extra while ENABLE=1 unless --force (review fix)
mu300-extra link              put the system's and the extras' commands on the PATH (run at boot)
mu300-extra adopt NAME        take the engines from the system an update replaced (<os>.old); used by mu300-vpn
```

## Not done

- No per-arch assets (one arch exists).
- No extra other than vpn yet; the catalog is one line per extra in mu300-update (`EXTRAS`).
- The installer does not detect an installed extra on the device before asking; mu300-update brings an extra up to
  the system's release on the next `apply`.
