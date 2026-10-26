# LuCI languages: implementation plan

Spec: `docs/superpowers/specs/2026-10-06-luci-languages-design.md`. Execution: tasks 1-6 inline (they share
`mu300-extra`/`mu300-update` and the panel), task 7 by parallel subagents (one per group of languages, from the final
English msgids), review by one independent subagent.

1. **Language table + checker.** `openwrt/luci-languages.tsv`; `tools/luci-i18n.py`: languages = every `po/*/`,
   tr/zh_Hans required, CJK allowed in every catalog, every po dir must have a table row; `update` over all.
   Tests in `tests/test_luci_i18n.py` (a third catalog is checked; a missing row fails; CJK in po/ja allowed).
2. **Images.** `openwrt/build-rootfs.sh`: luci-i18n-<component>-{tr,zh-cn} for base + each installed luci-app with a
   feed translation, in both images; the panel catalogs for openwrt-luci only, named by the table.
   `90-mu300`: `luci.main.lang=en` once (marker `luci.mu300.lang`), `auto`/unset of an older install -> en once;
   `91-mu300-luci`: `en`. Tests (fake uci over a config dir) under each shell.
3. **mu300-update/mu300-extra core.** `EXTRAS="vpn lang"`, `extra_desc lang`, `extra_installed` and `extra_unpack`
   accept `i18n/*.lmo` + `languages` for lang (validated listing before unpack), `mu300-extra`:
   `link` handles lang (symlinks, uci registration from `/etc/mu300/languages`, unregister gone ones, selected
   language gone -> en), `install lang` (enable all), `remove lang`, `lang list|enable|disable`, Ubuntu refusal and
   list note. Tests in `tests/test_extra.py` with a fake `uci`.
4. **make-extra lang + make-release.** `tools/make-extra.sh lang OUT TAG` (docker, mu300-openwrt-base image,
   apk add, po2lmo); make-release builds/audits/uploads `mu300-extra-lang.tar.gz`, notes row.
5. **Web page.** adapter `unisoc-modem/languages`, mu300dash `lang_get`/`lang_set`, ACL (methods + cgi-io upload of
   the fixed path), menu `admin/system/mu300-languages`, view `mu300/languages.js`. Tests in
   `tests/test_mu300dash_security.py` style (allow-lists, JSON) and the view loading test.
6. **Docs.** README "Languages", luci-app README, tests/README rows.
7. **Translations.** 29 new catalogs by subagents after task 5 fixed the msgids; tr/zh_Hans for the new strings;
   `luci-i18n.py check` clean; po2lmo builds all.
8. **Verify.** full test suite (+ busybox docker), check-i18n, both images and the extra built locally, device test
   on F50 #1, review, PR, CI.
