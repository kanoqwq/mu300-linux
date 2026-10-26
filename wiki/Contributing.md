# Contributing

**In one sentence:** reports, translations, tests and fixes are all welcome; open an issue or a pull request on
[GitHub](https://github.com/dikeckaan/mu300-linux).

This is a hobby project run in spare time. Clear reports and small, focused pull requests help the most.

## Reporting a problem

1. Read [Troubleshooting](Troubleshooting) first.
2. Say which device (F50 / MU300 or U30 Air), which release, which system (Ubuntu 24.04/26.04, OpenWrt, OpenWrt with the
   panel) and which kernel (5.4, 6.18, 7.2, 7.3).
3. Attach what helps:
   * installer problems: the output of `./install.sh --check` (it identifies the storage variant);
   * a device that fell back to Android: the folder from `tools/collect-logs.sh` (bootloader log, pstore and the init
     log kept in the Linux boot partition);
   * on a running system: `sudo mobile-data status`, `sudo wifi-client status`, `journalctl -t kernel` (Ubuntu).
4. **Remove personal data before posting:** IMEI, phone numbers, Wi-Fi names and passwords, VPN links. Never post the
   folder from `tools/backup-device.sh`.

Open it at https://github.com/dikeckaan/mu300-linux/issues. Reports about the VPN module belong to
https://github.com/dikeckaan/mu300-linux-vpn/issues.

## Running the tests

```sh
cd tests && python3 -m unittest        # all unit tests; standard library only (lz4 for the boot image tests)
python3 tools/check-i18n.py            # the installers' translations
python3 tools/luci-i18n.py check       # the control panel's translations
```

CI runs `tests.yml` on Ubuntu, macOS and Windows, `installer.yml` (translations, script syntax), `mainline.yml` (the
kernel port built every week against the newest longterm and stable kernels) and `magisk.yml` (the Magisk zips of each
release).

## Translating the installer

The installers (`install.sh`, `install.ps1`) speak English, Turkish and Chinese. Messages live in `i18n/<lang>.tsv`, one
line per message: the English text, a tab, the translation. A missing line simply stays in English.

To add a language (full steps in [i18n/README.md](https://github.com/dikeckaan/mu300-linux/blob/main/i18n/README.md)):

1. `python3 tools/check-i18n.py --keys` prints every message; translate them into `i18n/<lang>.tsv`. Keep commands, paths
   and the words to type (`INSTALL`, `ERASE`, `overwrite`, `update`/`wipe`) as they are.
2. Add the language to `choose_language` in `tools/i18n.sh` and to the menu in `install.ps1` (that script must stay
   ASCII), and its words for yes/no to `normalize_answer` / `NormalizeAnswer`.
3. `python3 tools/check-i18n.py` must report nothing missing and no placeholder mismatches.

## Translating the control panel

The panel's translations other than Turkish and Chinese are machine (AI) translations. Fix them in
`openwrt/luci-app-mu300/po/<language>/mu300.po` and open a pull request. `python3 tools/luci-i18n.py check` must stay
clean, and a new language also needs a row in `openwrt/luci-languages.tsv`.

## Code

* Read [docs/FINDINGS.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/FINDINGS.md) for why things are the
  way they are before changing them; many odd-looking steps exist because something broke without them.
* Build instructions: [Building from Source](Building-from-Source) and
  [docs/BUILD.md](https://github.com/dikeckaan/mu300-linux/blob/main/docs/BUILD.md).
* Keep images free of proprietary files: firmware and Android files come from the user's own device at install time.
* Say in the pull request what you tested, and on which device and kernel.

## This wiki and the website

* The wiki is kept as Markdown in the [`wiki/` folder](https://github.com/dikeckaan/mu300-linux/tree/main/wiki) of the
  main repository. Change it there with a pull request; the maintainer copies it to the GitHub wiki.
* The website (https://kaandikec.com/mu300-linux/) is the `site/` folder, plain HTML and CSS, deployed by GitHub
  Actions.
* When a fact changes (a command, a version, a limit), update the README, the wiki and the website together.

## Licences

Contributions to scripts, tools and documentation are under MIT; kernel patches under GPL-2.0. See
[LICENSE](https://github.com/dikeckaan/mu300-linux/blob/main/LICENSE).
