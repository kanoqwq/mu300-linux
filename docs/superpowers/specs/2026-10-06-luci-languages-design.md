# LuCI languages: a language switch, many catalogs, the lang extra (design)

Date: 2026-10-06. Branch `luci-languages`.

## Problem

The user (translated): "I could not see a language switch in the LuCI screen - add it so I can pull it with an update,
and translate into MANY languages: Turkish, English, Chinese come by default, the other languages downloadable." Then:
"the languages should also be installable from the web interface, and the main language must always be English."

What the device showed (F50 #1, v2026.10.10): the running plain `openwrt` system has no LuCI catalog at all
(`luci.languages` empty), so System -> System -> Language and Style offers only "auto" and "English" - there is
nothing to switch to. `openwrt-luci` has tr and zh-cn for luci-base and the firewall, but not for the package
manager, and starts in `auto`.

## Decisions (the user was not available; each with the alternative rejected)

1. **The switch is LuCI's own.** System -> System -> Language and Style lists `en` plus every key of
   `uci luci.languages`. Both images register tr and zh_cn, so the dropdown has a choice everywhere; the lang extra
   adds more. Rejected: a second switch in our panel's header (two places for one setting; the web page of D8
   links to LuCI's).
2. **Default languages in both images:** English, Turkish, Simplified Chinese. `build-rootfs.sh` installs
   `luci-i18n-<app>-tr` and `-zh-cn` for every LuCI component the image carries that has one in the feed (base,
   firewall, package-manager: found by name from the installed `luci-app-*`, so a new app is not forgotten), from
   the OpenWrt 25.12.5 feed through `apk add`, which checks each package against the feed's signed index (the
   same trust as every other package of the image; recorded in `etc/mu300/packages.txt`). Our panel's tr and
   zh_Hans catalogs go into openwrt-luci only (plain OpenWrt has no panel). Rejected: pinning the i18n packages by
   hash in the repository - the feed moves (luci-i18n 26.275 vs luci-base 26.180 today) and nothing else from the
   feed is pinned.
3. **The base language is always English.** A fresh install starts with `luci.main.lang=en` in both images (not
   `auto`, which shows a Turkish browser Turkish). 90-mu300 (both images) sets it once and writes the marker
   `luci.mu300.lang=1`; because /etc/config survives an update, a language the user picked stays. A system from
   before the marker that still says `auto` (or nothing) gets `en` once on its first boot after the update: `auto`
   was never chosen there (it was 91-mu300-luci's default, and plain OpenWrt's luci-base default) and, with the
   catalogs now present, would otherwise switch the UI language under the user. 91-mu300-luci sets `en` instead of
   `auto`. The installers never touch LuCI's language (their own language is the installer's, MU300_LANG is the
   Android locale). Rejected: forcing `en` on every boot (takes the user's choice away).
4. **Our panel in many languages.** Catalogs `po/<lang>/mu300.po` for de, fr, es, it, pt_BR, ru, uk, pl, nl, ar, fa,
   ja, ko, hi, id, vi, az, kk, el, cs, ro, hu, sv and also zh_Hant, bg, da, fi, sk, he (29 + tr + zh_Hans = 31,
   with English 32), translated by AI from the English msgids (placeholders, HTML, commands, AT commands, band
   names and device names unchanged). The README says they are machine/AI translations and asks for corrections.
   `tools/luci-i18n.py` checks every `po/*/mu300.po` (tr and zh_Hans must exist); `update` maintains all of them.
   CJK is allowed in every catalog (ja, zh_Hant), still nowhere else.
5. **Language codes, one table.** `openwrt/luci-languages.tsv`: po directory, LuCI code (the lmo name: `pt-br`,
   `zh-cn`, `zh-tw`, otherwise the po directory), and the name shown (LuCI's own `Deutsch (German)` form; for az,
   kk and id, which LuCI has no catalog for, ours). The uci key is the LuCI code with `-` -> `_` (LuCI's rule).
   The checker fails for a po directory without a row.
6. **One `lang` extra, not one per language.** `mu300-extra-lang.tar.gz` holds every language of the OpenWrt
   25.12.5 feed's luci-i18n-base (40 besides tr and zh-cn) plus az, kk, id (ours only): LuCI's catalogs of base,
   firewall and package-manager, and our panel's catalogs. Size: a few MB (measured in the PR). Rejected: one
   asset per language (40+ assets per release, 40+ SHA256SUMS lines, a per-language catalog in mu300-update; the
   whole set is smaller than one Ubuntu package update and fits an offline file copy). Layout: `./name`
   (`lang`), `./release`, `./components` (the feed packages and versions it was built from), `./languages`
   (`code<TAB>name`, the uci key and display name), `./i18n/<component>.<luci-code>.lmo`.
7. **How the extra is built.** `tools/make-extra.sh lang` runs the OpenWrt 25.12.5 base image (the one
   build-rootfs imports), `apk add`s the i18n packages (signed index), copies the lmo files out, and compiles our
   catalogs with `tools/po2lmo.py`. Rejected: unpacking the .apk files on the host (apk v3's ADB format; apk's own
   check is the verification we want).
8. **How it is installed on the device.** Not with apk: the extra lives on the Linux partition (`extra/lang`) like
   the vpn extra, shared by every OpenWrt system there. `mu300-extra link` (run at every boot by mu300-post)
   symlinks its lmo files into `/usr/lib/lua/luci/i18n` and registers the enabled languages in `luci.languages`;
   a language whose catalogs are all gone is unregistered, and if it was the selected one, LuCI goes back to `en`.
   A real file (a user's own luci-i18n package) is never replaced. Which languages are enabled is per system:
   `/etc/mu300/languages` (kept by updates); without the file, all of them. Rejected: `apk add` of the packages on
   the device (changes the apk database, has to be redone after every system update, and needs the apk files).
   Ubuntu: `mu300-extra list` shows `lang` as "for OpenWrt (LuCI) only"; `install lang` refuses there.
9. **Commands.** `mu300-extra install lang` (download or `MU300_EXTRA_FILE`, then all languages enabled),
   `mu300-extra remove lang` (unregistered, links gone, extra deleted), and
   `mu300-extra lang [list | enable CODE... | enable all | disable CODE... | disable all]`.
10. **The web page (openwrt-luci).** System -> Languages (`admin/system/mu300-languages`, ACL luci-app-mu300): the
    languages LuCI has now, the ones the extra offers with their own name and English name, enabled or not, and
    Enable / Disable per language, "Enable all", "Remove the language pack". Without the extra: "Download from the
    release" and "Upload the file" (`mu300-extra-lang.tar.gz`, LuCI's file upload to the fixed path
    `/tmp/mu300-extra-lang.tar.gz`; the ACL grants upload of that one path). Backend: mu300dash methods
    `lang_get` and `lang_set {op, codes}`, through a new adapter `/usr/libexec/unisoc-modem/languages` that calls
    `mu300-extra`. Every code is checked against `[a-z]{2,3}(_[a-z]{2})?` and then against the extra's own list;
    `op` is a fixed set; `source` is `release` or `file` (never a path). Install runs detached (a download can take
    longer than rpcd's 30 s) and `lang_get` reports its progress from a job file. After a change the page reloads
    the language list; LuCI reads uci per request, so the dropdown shows new languages without a reboot. An
    uploaded tarball is listed before unpacking: only the expected names, no links, no `..`.
11. **Updates.** mu300-update keeps installed extras current already (`extras_to_fetch`); the lang extra goes
    through the same path. `extra_installed`/`extra_unpack` accept an extra with `i18n/` instead of `bin/`.

## Testing

Unit (each shell, busybox in docker): link/unlink/registration with a fake `uci`, enable/disable, Ubuntu refusal,
unpack validation of a lang tarball, the adapter and mu300dash's allow-lists, 90-mu300's language default (fresh,
update with `auto`, update with a chosen language), the checker over every catalog, po2lmo of every catalog.
Build: both images, the extra. Device (F50 #1, openwrt-luci from a local release dir): the dropdown has en/tr/zh,
starts in English, tr and zh render LuCI's menus and the panel; the extra from a file through the web page; de, ru,
ja appear and render; disable/remove takes them away.
