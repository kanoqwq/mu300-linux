"""The window's messages in the installers' languages. They live where the installers' own do: i18n/<lang>.tsv, one
"English<TAB>translation" line per message, {1}.. for the arguments (i18n/README.md). A message without a line stays
English. tools/check-i18n.py reads the messages out of this package: the first argument of _() and N_(), which has
to be a plain string for that.
"""
import locale
import os
import sys

from . import TOP

LANGUAGES = (('en', 'English'), ('tr', 'Türkçe'), ('zh', '中文'))
_table = {}
_lang = 'en'


def load(lang, top=TOP):
    """{message: translation} of i18n/<lang>.tsv; empty for English or a file that is not there."""
    table = {}
    if lang == 'en':
        return table
    try:
        with open(os.path.join(top, 'i18n', lang + '.tsv'), encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\n').rstrip('\r')
                if not line or line.startswith('#') or '\t' not in line:
                    continue
                k, v = line.split('\t', 1)
                table[k] = v
    except OSError:
        pass
    return table


def set_language(lang, top=TOP):
    global _table, _lang
    if lang not in [code for code, _name in LANGUAGES]:
        lang = 'en'
    _lang = lang
    _table = load(lang, top)
    return lang


def language():
    return _lang


def fill(text, args):
    """{1}..{9} replaced by the arguments, each once and in one pass: an argument that holds "{2}" stays as it is."""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '{' and text[i + 2:i + 3] == '}' and text[i + 1:i + 2].isdigit() and text[i + 1] != '0':
            n = int(text[i + 1])
            if n <= len(args):
                out.append(str(args[n - 1]))
                i += 3
                continue
        out.append(c)
        i += 1
    return ''.join(out)


def _(message, *args):
    """The message in the chosen language, with its arguments."""
    return fill(_table.get(message, message), args)


def N_(message):
    """Marks a message that is translated later, where it is shown (tables of labels)."""
    return message


def os_language(environ=None, getlocale=None):
    """en, tr or zh from what the system is set to: MU300_LANG, then the locale of the environment, then the
    system's own."""
    environ = os.environ if environ is None else environ
    names = [environ.get('MU300_LANG', '')]
    for var in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG'):
        names.append(environ.get(var, ''))
    try:
        if getlocale is None:
            if sys.platform == 'win32':
                import ctypes
                lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
                names.append(locale.windows_locale.get(lcid, ''))
            elif sys.platform == 'darwin':
                import subprocess
                r = subprocess.run(['defaults', 'read', '-g', 'AppleLanguages'], capture_output=True, text=True, timeout=5)
                for part in r.stdout.replace('(', ' ').replace(')', ' ').replace('"', ' ').replace(',', ' ').split():
                    names.append(part)
                    break
            names.append((locale.getlocale()[0] or ''))
        else:
            names.append(getlocale() or '')
    except Exception:       # whatever the system says or does not say: English
        pass
    for name in names:
        code = name.replace('-', '_').split('_')[0].split('.')[0].lower()
        if code in ('en', 'tr', 'zh'):
            return code
    return 'en'
