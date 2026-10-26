#!/usr/bin/env python3
"""The translations of the Ubuntu web panel's MU300 page (rootfs/panel: Cockpit), English the source.

    python3 tools/cockpit-i18n.py check            list the problems; exit 1 if there is any
    python3 tools/cockpit-i18n.py update           add the missing msgids (empty msgstr) to each po/<lang>.po, drop
                                                   the stale ones, in order of first use
    python3 tools/cockpit-i18n.py build OUTDIR     po.<lang>.js for Cockpit (cockpit.locale) into OUTDIR

Messages: every _('...') in cockpit/mu300/mu300.js, one single-quoted literal each (JS escapes \\' and \\\\). The
catalogs are po/tr.po and po/zh_CN.po, one line per msgid and msgstr; Cockpit names the languages as these files do
(po.tr.js, po.zh_CN.js: the files of its own packages). Placeholders are Cockpit's $0, $1 ...: a translation has the
same ones.
"""
import json
import re
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parent.parent
PANEL = TOP / 'rootfs' / 'panel'
JS = PANEL / 'cockpit' / 'mu300' / 'mu300.js'
LANGS = ('tr', 'zh_CN')
CALL = re.compile(r"""\b_\(\s*'((?:[^'\\\n]|\\.)*)'\s*\)""")
ANY_CALL = re.compile(r'\b_\(')
PLACEHOLDER = re.compile(r'\$\d')


def unescape(s):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), s)


def extract(js=JS):
    src = js.read_text(encoding='utf-8')
    out, problems = [], []
    for m in CALL.finditer(src):
        s = unescape(m.group(1))
        if s not in out:
            out.append(s)
    # a _( that is not one plain literal is a message nobody can translate
    if len(ANY_CALL.findall(src)) != len(CALL.findall(src)):
        problems.append(f'dynamic: {js.relative_to(TOP)}: a _() call without one single-quoted literal')
    return out, problems


def po_quote(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def po_unquote(s):
    s = s.strip()
    assert s.startswith('"') and s.endswith('"'), s
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), s[1:-1])


def read_po(path):
    """{msgid: msgstr} (the header, msgid "", left out)"""
    out, msgid = {}, None
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('msgid '):
            msgid = po_unquote(line[6:])
        elif line.startswith('msgstr ') and msgid is not None:
            if msgid:
                out[msgid] = po_unquote(line[7:])
            msgid = None
    return out


def header(lang):
    return (f'# The MU300 page of Cockpit (rootfs/panel), {lang}. tools/cockpit-i18n.py update / check / build.\n'
            'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
            f'"Language: {lang}\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n')


def po_path(lang):
    return PANEL / 'po' / f'{lang}.po'


def check():
    msgs, problems = extract()
    for lang in LANGS:
        p = po_path(lang)
        if not p.exists():
            problems.append(f'missing: {p.relative_to(TOP)} does not exist')
            continue
        cat = read_po(p)
        for m in msgs:
            if not cat.get(m):
                problems.append(f'missing: {p.relative_to(TOP)}: {m!r}')
            elif PLACEHOLDER.findall(cat[m]) != PLACEHOLDER.findall(m) and \
                    sorted(PLACEHOLDER.findall(cat[m])) != sorted(PLACEHOLDER.findall(m)):
                problems.append(f'placeholder: {p.relative_to(TOP)}: {m!r}')
        for m in cat:
            if m not in msgs:
                problems.append(f'stale: {p.relative_to(TOP)}: {m!r}')
    return problems


def update():
    msgs, _ = extract()
    for lang in LANGS:
        p = po_path(lang)
        cat = read_po(p) if p.exists() else {}
        body = ''.join(f'\nmsgid {po_quote(m)}\nmsgstr {po_quote(cat.get(m, ""))}\n' for m in msgs)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(header(lang) + body, encoding='utf-8')


def build(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    msgs, _ = extract()
    for lang in LANGS:
        cat = read_po(po_path(lang))
        data = {'': {'plural-forms': '__PLURAL__', 'language': lang, 'language-direction': 'ltr'}}
        for m in msgs:
            if cat.get(m):
                data[m] = [None, cat[m]]
        text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False)
        text = text.replace('"__PLURAL__"', 'function(n) { return n != 1 ? 1 : 0; }')
        (out / f'po.{lang}.js').write_text(f'cockpit.locale({text});\n', encoding='utf-8')


def main(argv):
    if argv[:1] == ['check']:
        problems = check()
        for p in problems:
            print(p)
        return 1 if problems else 0
    if argv[:1] == ['update']:
        update()
        return 0
    if argv[:1] == ['build'] and len(argv) == 2:
        build(argv[1])
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
