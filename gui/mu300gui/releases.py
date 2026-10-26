"""The newest release and the beta, if there is one - decided by tools/release_pick.py, the file install.sh and
install.ps1 decide with, so the window offers exactly what they would. No token is needed; when GitHub does not
answer (offline, its rate limit) there is simply nothing to offer and the installer looks the release up itself.
"""
import importlib.util
import os
import urllib.error
import urllib.request

from . import REPO, TOP

_pick = None


def picker(top=TOP):
    global _pick
    if _pick is None:
        spec = importlib.util.spec_from_file_location('release_pick', os.path.join(top, 'tools', 'release_pick.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _pick = mod
    return _pick


def decide(text, top=TOP):
    """{'latest', 'beta', 'files'} from the API's answer as text; tags that are not plain are dropped as the command
    line of release_pick.py drops them."""
    mod = picker(top)
    res = mod.pick_text(text)
    for key in ('latest', 'beta'):
        if res[key] and not mod.plain(res[key]):
            res['files'].pop(res[key], None)
            res[key] = None
    if not res['latest']:
        res['beta'] = None
        res['files'] = {}
    return res


def fetch(repo=REPO, timeout=15, opener=None, top=TOP):
    """The decision for the releases GitHub lists now; latest None when the list did not come."""
    url = 'https://api.github.com/repos/%s/releases?per_page=15' % repo
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'mu300-linux-gui'})
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            text = r.read().decode('utf-8', 'replace')
    except (OSError, urllib.error.URLError, ValueError):
        text = ''
    return decide(text, top)
