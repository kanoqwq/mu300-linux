#!/usr/bin/env python3
"""Which release the installers take, from GitHub's list of releases (GET /repos/<repo>/releases).

  tools/release_pick.py [FILE]          prints LATEST=<tag> and, only when there is one, BETA=<tag>
  tools/release_pick.py --json [FILE]   the same as one JSON object, with the files and sizes of both

FILE is the API's answer (default: standard input). LATEST is the newest full release: not a draft, not a
prerelease. BETA is the newest prerelease, and only when it was published after LATEST (and, both being release
tags, is newer by its tag: docs/FINDINGS.md 125) - an old prerelease that a full release has overtaken is never
offered. Anything that is not such a list (an empty answer, the error object of a rate-limited or failed request,
HTML of a proxy) gives no line at all, and the caller goes on as without a beta.
install.sh, install.ps1 and the graphical installer all decide here, so they cannot disagree.
"""
import json
import re
import sys

# The release tags (docs/FINDINGS.md 125): vYYYY.MM.DD up to v2026.10.27, where the "day" was a counter, and from then
# on vYYYY.MM.DD.N, the real (UTC) day the release was built and N = 1..99 for the releases of that day. Every
# four-field tag is newer than every three-field one; four-field tags compare by the date, then by N, as numbers. The
# same rule as tools/releases.sh (sh), the web installer's version.js (JavaScript) and install.ps1 (PowerShell), held
# to one table: tests/fixtures/release-tags.tsv.
_OLD_TAG = re.compile(r'v([0-9]{4})\.([0-9]{2})\.([0-9]{2})', re.ASCII)
_NEW_TAG = re.compile(r'v([0-9]{4})\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])\.([1-9][0-9]?)', re.ASCII)


def tag_key(tag):
    """(era, YYYYMMDD, N) in the order of the releases; None for what is no release tag."""
    if not isinstance(tag, str):
        return None
    m = _OLD_TAG.fullmatch(tag)
    if m:
        return (1, int(''.join(m.groups())), 0)
    m = _NEW_TAG.fullmatch(tag)
    if m:
        return (2, int(''.join(m.groups()[:3])), int(m.group(4)))
    return None


def tag_ok(tag):
    return tag_key(tag) is not None


def newer(a, b):
    """Release a is newer than release b; False when either is no release tag."""
    ka, kb = tag_key(a), tag_key(b)
    return ka is not None and kb is not None and ka > kb


def _when(rel):
    """What a release is ordered by: when it was published (ISO 8601 in UTC, so text order is time order)."""
    return str(rel.get('published_at') or rel.get('created_at') or '')


def _usable(rel):
    return isinstance(rel, dict) and isinstance(rel.get('tag_name'), str) and rel['tag_name'] and not rel.get('draft')


def _files(rel):
    out = {}
    for a in rel.get('assets') or []:
        if isinstance(a, dict) and isinstance(a.get('name'), str):
            try:
                out[a['name']] = int(a.get('size') or 0)
            except (TypeError, ValueError):
                out[a['name']] = 0
    return out


def pick(releases):
    """{'latest': tag or None, 'beta': tag or None, 'files': {tag: {name: bytes}}} from the parsed API answer."""
    res = {'latest': None, 'beta': None, 'files': {}}
    if not isinstance(releases, list):
        return res
    usable = [r for r in releases if _usable(r)]
    full = [r for r in usable if not r.get('prerelease')]
    if not full:
        return res
    latest = max(full, key=_when)
    res['latest'] = latest['tag_name']
    res['files'][latest['tag_name']] = _files(latest)
    # published after it, and - when both are release tags - also newer by its tag (as mu300-update decides)
    pre = [r for r in usable if r.get('prerelease') and _when(r) > _when(latest) and r['tag_name'] != latest['tag_name']
           and not (tag_ok(r['tag_name']) and tag_ok(latest['tag_name']) and not newer(r['tag_name'], latest['tag_name']))]
    if pre:
        beta = max(pre, key=_when)
        res['beta'] = beta['tag_name']
        res['files'][beta['tag_name']] = _files(beta)
    return res


def pick_text(text):
    """pick() of the API's answer as text; anything that does not parse is no release at all."""
    try:
        return pick(json.loads(text))
    except (ValueError, TypeError):
        return pick(None)


def plain(tag):
    """A tag goes into file names and URLs: one that is not plain is not passed on."""
    return bool(tag) and all(c.isascii() and (c.isalnum() or c in '._-+') for c in tag)


def main(argv):
    as_json = '--json' in argv
    paths = [a for a in argv if not a.startswith('--')]
    if paths:
        with open(paths[0], 'rb') as f:
            raw = f.read()
    else:
        raw = sys.stdin.buffer.read()
    res = pick_text(raw.decode('utf-8', 'replace'))
    for key in ('latest', 'beta'):
        if not plain(res[key]):
            res['files'].pop(res[key], None)
            res[key] = None
    if not res['latest']:
        res['beta'] = None
        res['files'] = {}
    if as_json:
        print(json.dumps(res, sort_keys=True))
        return 0
    for key in ('latest', 'beta'):
        if res[key]:
            print('%s=%s' % (key.upper(), res[key]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
