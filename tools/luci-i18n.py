#!/usr/bin/env python3
"""The control panel's (openwrt/luci-app-mu300) translation catalogs, po/<lang>/mu300.po (tr and zh_Hans required).

    python3 tools/luci-i18n.py check   [--root DIR]   list the problems; exit 1 if there is any
    python3 tools/luci-i18n.py extract [--root DIR]   print every message once, in order of first use
    python3 tools/luci-i18n.py update  [--root DIR]   add the missing msgids (empty msgstr) to every .po file,
                                                       drop the stale ones, order the entries by first use

--root DIR checks another copy of the app (the tests use it); the shared scripts are then not scanned for CJK.

Messages are English and come from:
  * htdocs/**/*.js: _('...') / _("...") with one string literal argument (JS escapes decoded). Any other argument
    is an error, except the sites in DYNAMIC_OK, whose values are messages extracted from elsewhere.
  * root/usr/share/luci/menu.d/*.json: every "title"; root/usr/share/rpcd/acl.d/*.json: every "description".
  * the backend, root/usr/libexec/rpcd/mu300dash, mu300vpn and root/usr/libexec/unisoc-modem/*, in three forms:
      - a literal JSON value in the code, "error" or "message", plain or escaped inside a "..." string:
        printf '{"ok":0,"error":"Text"}\\n'   print "{\\"error\\":\\"Text\\"}"
        Any other value of such a key (%s, a variable) outside the body of a reply helper is an error, unless the
        code line is right after '# i18n: Text' line(s) naming the message(s) it can carry.
      - a literal argument of a reply helper: refuse 'Text' [DETAIL], reply_obj JSON 'Text' (mu300dash),
        fail OP 'Text' [DETAIL] (action), error 'Text' (device-usb), cli_fail 'Text', reply_val KEY 'Text'
        (mu300vpn); single or double quotes, no $ or `
        inside double quotes. A helper called with a non-literal message is an error, except in BACKEND_DYNAMIC_OK.
      - a comment line '# i18n: Text', for a message the code builds or passes through a variable.
  * N_() is an error: the app has no plural messages (tools/po2lmo.py refuses msgid_plural).

The problems `check` prints, one per line as RULE: PATH:LINE: WHAT (spec, Translations):
  cjk          a CJK character outside the catalogs (po/) and the installers' i18n data
  missing      a message without an entry, or with an empty msgstr, in any po/<lang>/mu300.po (or no po/tr,
               po/zh_Hans: the images need both)
  language     a po/<lang> directory without a row in openwrt/luci-languages.tsv (its LuCI code and name)
  placeholder  a msgstr whose %s/%d/%% placeholders are not its msgid's, in the same order
  stale        a msgid that no source uses
  dynamic      a _(), a reply helper or an "error"/"message" key with a non-literal message, not allowed above
  plural       an N_() call
  js           a JavaScript file the scanner cannot read (its messages are then not extracted)
  po           a .po that LuCI's po2lmo (tools/po2lmo.py) would not compile
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import po2lmo  # noqa: E402

APP = TOP / 'openwrt' / 'luci-app-mu300'
REQUIRED = ('tr', 'zh_Hans')
# po directory -> (LuCI code, name): the build, the lang extra and this check read the same table
TABLE = TOP / 'openwrt' / 'luci-languages.tsv'


def table():
    rows = {}
    for line in TABLE.read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#'):
            d, code, name = line.split('\t')
            rows[d] = (code, name)
    return rows


def langs(root):
    """every catalog directory of the app (po/<lang>/), the required ones included even when missing"""
    have = {p.name for p in (root / 'po').iterdir() if p.is_dir()} if (root / 'po').is_dir() else set()
    return sorted(have | set(REQUIRED))
DOMAIN = 'mu300'

# _() arguments that are not literals: backend replies and carrier names, extracted from the backend and common.js
DYNAMIC_OK = {'res.error', 'res.message', 'carrier.name'}
# reply helpers of the backend: name -> position of the message among its arguments (1 = first)
HELPERS = {'refuse': 1, 'reply_obj': 2, 'fail': 2, 'error': 1, 'cli_fail': 1, 'reply_val': 2}
# helper calls whose message is a variable on purpose: (file name, helper, argument)
BACKEND_DYNAMIC_OK = {('mu300dash', 'refuse', '"$2"'),   # reply_obj passing its MESSAGE on
                      ('mu300vpn', 'refuse', '"$1"'),    # cli_fail and reply_val passing theirs on
                      ('mu300vpn', 'refuse', '"$2"')}

# CJK: only these files may contain it (repository paths; with --root, only po/ of that copy)
CJK = re.compile('[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef]')
CJK_SCOPE = ['openwrt', 'rootfs/overlay', 'boot', 'tools', 'install.sh', 'uninstall.sh']
CJK_ALLOWED = re.compile(r'^(tools/i18n\.sh|i18n/[^/]+\.tsv|openwrt/luci-app-mu300/po/.*|openwrt/luci-languages\.tsv|tests/fixtures/.*|rootfs/overlay/opt/mu300/lib/cpu-voltage-warning\.txt|rootfs/overlay/opt/mu300/lib/image-manifests/[^/]+\.gz)$')   # (compressed data: no text to look at)

PLACEHOLDER = re.compile(r'%(?:%|[-+#0]*\d*(?:\.\d+)?[a-zA-Z])')


# ---------------------------------------------------------------------------------------------------- JavaScript

JS_ESC = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', 'v': '\v', '0': '\0'}
REGEX_AFTER = set('(,=:[!&|?{};+-*%<>~^')
REGEX_AFTER_WORDS = {'return', 'typeof', 'case', 'do', 'else', 'in', 'of', 'new', 'delete', 'void', 'throw',
                     'yield', 'await', 'instanceof'}


def _js_string(src, i):
    """(decoded value, index after) of the string literal whose opening quote is at src[i]."""
    q, out, i = src[i], [], i + 1
    while i < len(src):
        c = src[i]
        if c == q:
            return ''.join(out), i + 1
        if c == '\n':
            raise ValueError('unterminated string')
        if c != '\\':
            out.append(c)
            i += 1
            continue
        e = src[i + 1]
        if e in JS_ESC and not (e == '0' and src[i + 2:i + 3].isdigit()):
            out.append(JS_ESC[e])
            i += 2
        elif e == 'x':
            out.append(chr(int(src[i + 2:i + 4], 16)))
            i += 4
        elif e == 'u' and src[i + 2] == '{':
            j = src.index('}', i)
            out.append(chr(int(src[i + 3:j], 16)))
            i = j + 1
        elif e == 'u':
            out.append(chr(int(src[i + 2:i + 6], 16)))
            i += 6
        elif e == '\r' and src[i + 2:i + 3] == '\n':
            i += 3
        elif e in '\n\r\u2028\u2029':
            i += 2
        else:
            out.append(e)
            i += 2
    raise ValueError('unterminated string')


def _js_skip(src, i):
    """The index of the next character at or after i that is not white space or inside a comment."""
    while i < len(src):
        if src[i].isspace():
            i += 1
        elif src.startswith('//', i):
            j = src.find('\n', i)
            i = len(src) if j < 0 else j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            i = len(src) if j < 0 else j + 2
        else:
            break
    return i


def _js_close(src, i):
    """The index after the ')' that closes the '(' at src[i] (strings and nesting respected)."""
    depth = 0
    while i < len(src):
        c = src[i]
        if c in '\'"':
            i = _js_string(src, i)[1]
            continue
        if c == '`':
            j = i + 1
            while j < len(src) and src[j] != '`':
                j += 2 if src[j] == '\\' else 1
            i = j + 1
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(src)


N_PLURAL = object()      # js_calls' message for an N_() call


def js_calls(src):
    """[(offset, message or None, argument text)] for every _( ... ) and N_( ... ) call outside comments, strings
    and regexes. message is the decoded literal when the argument of _() is exactly one '...' or "..." string,
    N_PLURAL for N_(), else None."""
    calls, stack, i, last = [], [], 0, None       # last: the previous token's last character, or a keyword
    n = len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
        elif src.startswith('//', i) or src.startswith('/*', i):
            i = _js_skip(src, i)
        elif c in '\'"':
            i, last = _js_string(src, i)[1], '"'
        elif c == '`' or (c == '}' and stack and stack[-1] == '`'):
            if c == '}':
                stack.pop()
            i += 1
            while i < n and src[i] != '`':      # template text, until its end or a ${
                if src[i] == '\\':
                    i += 2
                elif src.startswith('${', i):
                    stack.append('`')
                    i += 2
                    break
                else:
                    i += 1
            else:
                i, last = i + 1, '"'
                continue
            last = '{'
        elif c == '/':
            if last is None or last in REGEX_AFTER or last in REGEX_AFTER_WORDS:
                i, cls = i + 1, False           # a regular expression literal
                while i < n and (src[i] != '/' or cls):
                    if src[i] == '\\':
                        i += 1
                    elif src[i] == '[':
                        cls = True
                    elif src[i] == ']':
                        cls = False
                    i += 1
                i += 1
                while i < n and (src[i].isalnum()):
                    i += 1
                last = '"'
            else:
                i, last = i + 1, '/'
        elif c.isalpha() or c in '_$':
            j = i
            while j < n and (src[j].isalnum() or src[j] in '_$'):
                j += 1
            word = src[i:j]
            if word in ('_', 'N_') and last != '.':
                k = _js_skip(src, j)
                if k < n and src[k] == '(':
                    a = _js_skip(src, k + 1)
                    msg = None
                    if word == 'N_':
                        msg = N_PLURAL
                    elif a < n and src[a] in '\'"':
                        value, b = _js_string(src, a)
                        if src[_js_skip(src, b):_js_skip(src, b) + 1] == ')':
                            msg = value
                    end = _js_close(src, k)
                    calls.append((i, msg, ' '.join(src[k + 1:end - 1].split())))
            i, last = j, word
        else:
            if c == '{':
                stack.append('{')
            elif c == '}' and stack:
                stack.pop()
            i, last = i + 1, c
    return calls


# ------------------------------------------------------------------------------------------------------- sources

class Messages:
    """Messages in order of first use, with where each was first seen; and the problems found while extracting."""

    def __init__(self):
        self.where, self.problems = {}, []

    def add(self, msg, path, line):
        self.where.setdefault(msg, (path, line))

    def problem(self, rule, path, line, what):
        self.problems.append((rule, path, line, what))


def _line(text, offset):
    return text.count('\n', 0, offset) + 1


def _json_values(m, key, path, text):
    for x in re.finditer(r'"%s"\s*:\s*("(?:[^"\\]|\\.)*")' % key, text):
        m.add(json.loads(x.group(1)), path, _line(text, x.start()))


# a shell word: '...', "..." or unquoted characters, glued together
SH_WORD = re.compile(r'''(?:'[^']*'|"(?:[^"\\]|\\.)*"|[^\s;&|()<>'"]+)+''')
SH_HELPER = re.compile(r'(?:^|[;&|{(]|\bthen|\belse|\bdo|\))\s*(%s)[ \t]+' % '|'.join(HELPERS))
SH_I18N = re.compile(r'^\s*# i18n: (.+?)\s*$')
SH_DEF = re.compile(r'^(\s*)(\w+)\s*\(\)\s*\{')
# an "error" or "message" key in JSON the code writes, plain ("error":) or escaped inside "..." (\"error\":)
SH_KEY = re.compile(r'(\\?)"(?:error|message)\\?"\s*:\s*')
SH_PLAIN_VALUE = re.compile(r'"((?:[^"\\]|\\.)*)"')
SH_ESCAPED_VALUE = re.compile(r'\\"((?:[^"\\]|\\[^"])*)\\"')
SH_CONST = re.compile(r'(?:null|true|false|-?\d)')
CONVERSION = re.compile(r'%(?!%)')


def _sh_literal(word):
    """The value of a shell word that is one literal quoted string, else None."""
    if len(word) >= 2 and word[0] == word[-1] == "'" and "'" not in word[1:-1]:
        return word[1:-1]
    if len(word) >= 2 and word[0] == word[-1] == '"' and not re.search(r'[$`]|(?<!\\)"', word[1:-1]):
        return re.sub(r'\\([$`"\\])', r'\1', word[1:-1])
    return None


def _json_keys(line):
    """[(value or None, text)] for each "error"/"message" key on a shell line: value is the literal string the
    key gets, None when it gets anything else (a %s, a variable, ...); text is what follows the key."""
    out = []
    for x in SH_KEY.finditer(line):
        rest = line[x.end():]
        v = (SH_ESCAPED_VALUE if x.group(1) else SH_PLAIN_VALUE).match(rest)
        if v and not CONVERSION.search(v.group(1)):
            value = v.group(1).replace('\\"', '"') if x.group(1) else v.group(1)
            out.append((json.loads('"%s"' % value), rest))
        elif not SH_CONST.match(rest):
            out.append((None, rest[:30]))
    return out


def _backend(m, path, rel, name):
    lines = path.read_text(encoding='utf-8', errors='replace').split('\n')
    no, helper_end, covered = 0, None, False      # helper_end: the closing line of the helper being read
    while no < len(lines):
        start, line = no + 1, lines[no]
        while line.endswith('\\') and no + 1 < len(lines):     # continued lines are one command
            no += 1
            line = line[:-1] + lines[no]
        no += 1
        c = SH_I18N.match(line)
        if c:
            m.add(c.group(1), rel, start)
            covered = True
            continue
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        d = SH_DEF.match(line)
        in_helper = helper_end is not None
        if d and d.group(2) in HELPERS:
            in_helper = True
            if not line.rstrip().endswith('}'):
                helper_end = re.compile(r'^%s\}\s*$' % re.escape(d.group(1)))
        elif helper_end is not None and helper_end.match(line):
            helper_end = None
        for value, text in _json_keys(line):
            if value is not None:
                m.add(value, rel, start)
            elif not in_helper and not covered:
                m.problem('dynamic', rel, start, f'a non-literal error/message ({text.strip()}) without a '
                          '"# i18n:" line above it')
        covered = False
        for x in SH_HELPER.finditer(line):
            helper = x.group(1)
            words = SH_WORD.findall(line, x.end())
            if len(words) < HELPERS[helper]:
                continue
            word = words[HELPERS[helper] - 1]
            value = _sh_literal(word)
            if value is not None:
                m.add(value, rel, start)
            elif (name, helper, word) not in BACKEND_DYNAMIC_OK:
                m.problem('dynamic', rel, start, f'{helper} with a non-literal message {word}')


def extract(root, base=None):
    """The messages of the app at root; paths are shown relative to base (default: root)."""
    m = Messages()

    def rel(p):
        return p.relative_to(base or root).as_posix()

    for p in sorted(root.glob('root/usr/share/luci/menu.d/*.json')):
        _json_values(m, 'title', rel(p), p.read_text(encoding='utf-8'))
    for p in sorted(root.glob('root/usr/share/rpcd/acl.d/*.json')):
        _json_values(m, 'description', rel(p), p.read_text(encoding='utf-8'))
    for p in sorted(root.glob('htdocs/**/*.js')):
        src = p.read_text(encoding='utf-8')
        try:
            calls = js_calls(src)
        except (ValueError, IndexError) as e:
            m.problem('js', rel(p), 1, f'cannot read the JavaScript: {e}')
            continue
        for off, msg, arg in calls:
            if msg is N_PLURAL:
                m.problem('plural', rel(p), _line(src, off), f'N_({arg}): this app has no plural messages')
            elif msg is not None:
                m.add(msg, rel(p), _line(src, off))
            elif arg not in DYNAMIC_OK:
                m.problem('dynamic', rel(p), _line(src, off), f'_({arg}) is not one string literal')
    backend = [root / 'root/usr/libexec/rpcd/mu300dash', root / 'root/usr/libexec/rpcd/mu300vpn'] + \
        sorted(root.glob('root/usr/libexec/unisoc-modem/*'))
    for p in backend:
        if p.is_file():
            _backend(m, p, rel(p), p.name)
    return m


# -------------------------------------------------------------------------------------------------------- .po

def po_escape(s):
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def read_po(path):
    """(header lines, [entry]) of a .po; entry = {msgid, msgstr, line, comments, raw_msgstr}. Strings are
    decoded as LuCI's po2lmo decodes them (only \\" and \\\\). ValueError if po2lmo would refuse the file."""
    text = path.read_text(encoding='utf-8')
    po2lmo.compile_po(text)
    entries, cur, comments, field = [], None, [], None
    for no, line in enumerate(text.split('\n'), 1):
        s = line.strip()
        if s.startswith('#') or not s:
            if s:
                comments.append(line)
            continue
        if s.startswith('msgid '):
            cur = {'msgid': '', 'msgstr': '', 'line': no, 'comments': comments, 'raw_msgstr': []}
            comments, field = [], 'msgid'
            entries.append(cur)
        elif s.startswith('msgstr '):
            field = 'msgstr'
        body = s.split(' ', 1)[1].lstrip() if not s.startswith('"') else s
        cur[field] += po2lmo._string(body.encode('utf-8'), no).decode('utf-8')
        if field == 'msgstr':
            cur['raw_msgstr'].append(body)
    header = []
    if entries and entries[0]['msgid'] == '':
        h = entries.pop(0)
        header = h['comments'] + ['msgid ""'] + ['msgstr ' + h['raw_msgstr'][0]] + h['raw_msgstr'][1:]
    return header, entries


def po_path(root, lang):
    return root / 'po' / lang / f'{DOMAIN}.po'


def check(root, scan_shared):
    """[(rule, path, line, what)]. scan_shared: the repository's app and shared scripts, paths shown from the top
    of the repository; else only the app at root, paths shown from root."""
    base = TOP if scan_shared else root
    m = extract(root, base)
    problems = list(m.problems)

    def rel(p):
        return p.relative_to(base).as_posix()

    # 1. CJK
    if scan_shared:
        out = subprocess.run(['git', '-C', str(TOP), 'ls-files', '-z', '--'] + CJK_SCOPE,
                             capture_output=True, check=True).stdout.decode('utf-8')
        files = [(TOP / f, f, CJK_ALLOWED.match(f)) for f in out.split('\0') if f]
    else:
        files = [(p, rel(p), rel(p).startswith('po/')) for p in sorted(root.rglob('*'))]
    for p, f, allowed in files:
        if allowed or not p.is_file():
            continue
        for no, line in enumerate(p.read_bytes().decode('utf-8', errors='replace').split('\n'), 1):
            if CJK.search(line):
                problems.append(('cjk', f, no, line.strip()[:100]))

    # 2-4. the catalogs
    rows = table()
    for lang in langs(root):
        p = po_path(root, lang)
        if lang not in rows:
            problems.append(('language', rel(p), 0, f'po/{lang} has no row in {TABLE.relative_to(TOP).as_posix()}'))
        if not p.is_file():
            problems.append(('missing', rel(p), 0, 'no such catalog'))
            continue
        try:
            _, entries = read_po(p)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            problems.append(('po', rel(p), 0, str(e)))
            continue
        have = {e['msgid']: e for e in entries}
        for msg, (src, line) in m.where.items():
            e = have.get(msg)
            if e is None:
                problems.append(('missing', rel(p), 0, f'"{po_escape(msg)}" (used at {src}:{line})'))
            elif not e['msgstr']:
                problems.append(('missing', rel(p), e['line'], f'"{po_escape(msg)}" has an empty msgstr'))
            elif PLACEHOLDER.findall(e['msgstr']) != PLACEHOLDER.findall(msg):
                problems.append(('placeholder', rel(p), e['line'],
                                 f'"{po_escape(msg)}" {PLACEHOLDER.findall(msg)} but the msgstr has '
                                 f'{PLACEHOLDER.findall(e["msgstr"])}'))
        for e in entries:
            if e['msgid'] not in m.where:
                problems.append(('stale', rel(p), e['line'], f'"{po_escape(e["msgid"])}" is used nowhere'))
    return problems


def update(root):
    m = extract(root)
    for lang in langs(root):
        p = po_path(root, lang)
        if p.is_file():
            header, entries = read_po(p)
        else:
            header = ['msgid ""', 'msgstr ""', '"Content-Type: text/plain; charset=UTF-8\\n"',
                      f'"Language: {lang}\\n"']
            entries = []
        have = {e['msgid']: e for e in entries}
        out = header[:]
        for msg in m.where:
            e = have.get(msg)
            out += [''] + (e['comments'] if e else []) + [f'msgid "{po_escape(msg)}"']
            raw = e['raw_msgstr'] if e else ['""']
            out += ['msgstr ' + raw[0]] + raw[1:]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('\n'.join(out) + '\n', encoding='utf-8')
    return m.problems


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--root', type=Path, help='the app directory (default: openwrt/luci-app-mu300)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('check', 'extract', 'update'):
        sub.add_parser(name, parents=[common])
    a = ap.parse_args(argv)
    root = (a.root or APP).resolve()

    if a.cmd == 'extract':
        for msg in extract(root).where:
            print(msg.replace('\\', '\\\\').replace('\n', '\\n'))
        return 0
    if a.cmd == 'update':
        problems = update(root)
    else:
        problems = check(root, scan_shared=a.root is None)
    for rule, path, line, what in problems:
        print(f'{rule}: {path}:{line}: {what}')
    if problems and a.cmd == 'check':
        print(f'{len(problems)} problem(s)', file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
