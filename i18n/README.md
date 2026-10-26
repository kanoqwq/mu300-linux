# Installer translations

`install.sh` (with the `tools/*.sh` it sources), `install.ps1` (with `tools/*.ps1`) and the graphical installer
(`gui/`) show their messages through `t` / `T` / `_()`, which look them up here: `<lang>.tsv` has one line per message,

    English message with {1}<TAB>translation with {1}

where `{1}`..`{6}` are the arguments, in any order the language needs. Lines starting with `#` are comments. A
message without a line stays in English, so an incomplete translation never breaks the installer.

To add a language:

1. `python3 tools/check-i18n.py --keys` prints every message; translate them into `<lang>.tsv` (UTF-8, a real tab
   between the two columns). Keep commands, paths and the words to type (`INSTALL`, `ERASE`, `overwrite`,
   `update`/`wipe`) as they are.
2. Add the language to `choose_language` in `tools/i18n.sh` and to the menu in `install.ps1` (write its name there
   with `[char]` code points: the script has to stay ASCII for Windows PowerShell 5.1), and its words for yes/no
   to `normalize_answer` / `NormalizeAnswer`.
3. `python3 tools/check-i18n.py` must report nothing missing and no placeholder mismatches.

When a message changes in a script, its old line here turns up as "stale" and the new one as "missing".

The graphical installer's messages are in the same files: the first argument of `_('...')` and `N_('...')` in
`gui/mu300gui/*.py` (`N_` marks a label of a table that is translated where it is shown). `tools/check-i18n.py` reads
them with Python's own parser. A new language also goes into `LANGUAGES` in `gui/mu300gui/i18n.py`. The words a user
types stay as they are there too (`ERASE`, `overwrite`, `internal`); `INSTALL` and `UNINSTALL` are buttons in the
window.
