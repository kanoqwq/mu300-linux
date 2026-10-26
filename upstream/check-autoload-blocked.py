#!/usr/bin/env python3
"""The on-demand guard against a real module set (make-bundle.sh; tests/test_all_modules.py with a sample).

    python3 check-autoload-blocked.py BUILD_OUT LIST GUARD

BUILD_OUT holds what build-modules.sh keeps of depmod's work on the kernel's own modules (modules.dep,
modules.alias). Checked:
  * every module that depends on a blocked one is blocked too (otherwise a request for it would load the blocked one
    as a dependency);
  * the guard (rootfs/overlay/opt/mu300/bin/mu300-modprobe-guard, run with sh, over module files that carry the
    aliases of modules.alias in their .modinfo strings) refuses every alias of every blocked module and every spelling of its name, and never calls modprobe
    for them;
  * it hands on the aliases and names of the modules the systems load on demand every day (nft, wireguard, overlay,
    usb-storage, vfat, tun, bridge, veth, br_netfilter, xt_*, vhost_net, l2tp...) to modprobe unchanged.
"""
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile

MUST_PASS = ['nf_tables', 'nft_chain_nat', 'nft_ct', 'nft_masq', 'wireguard', 'overlay', 'usb_storage', 'vfat', 'tun',
             'bridge', 'veth', 'br_netfilter', 'xt_nat', 'xt_conntrack', 'xt_MASQUERADE', 'xt_addrtype',
             'vhost_net', 'l2tp_core', 'l2tp_ppp', 'l2tp_ip', 'macvtap', 'kvm', 'can_dev',
             'gs_usb', 'hfsplus', 'ntfs3', 'exfat', 'btrfs', 'fuse', 'iptable_nat', 'ip6_tables', 'xfrm_user', 'esp4',
             'sch_cake', 'nls_cp437', 'cdc_mbim', 'qmi_wwan', 'btusb', 'snd_aloop', 'dm_crypt', 'loop', 'zram']


def norm(s):
    return s.replace('-', '_')


def load(out):
    dep, aliases = {}, {}
    for line in (out / 'modules.dep').read_text().splitlines():
        k, v = line.split(':', 1)
        name = lambda p: norm(os.path.basename(p).split('.ko')[0])
        dep[name(k)] = {name(x) for x in v.split()}
    for line in (out / 'modules.alias').read_text().splitlines():
        if line.startswith('alias '):
            _, a, m = line.split(None, 2)
            aliases.setdefault(norm(m.strip()), []).append(a)
    return dep, aliases


def check(out, listfile, guard, shell=('sh',)):
    out = pathlib.Path(out)
    dep, aliases = load(out)
    blocked = {l.split('#')[0].strip() for l in pathlib.Path(listfile).read_text().splitlines()} - {''}
    errors = []
    for m, ds in sorted(dep.items()):
        if m not in blocked and ds & blocked:
            errors.append('%s depends on %s but is not in the list' % (m, ' '.join(sorted(ds & blocked))))
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        moddir = d / 'modules'
        moddir.mkdir()
        # each module file carries its aliases as a real one does: NUL-separated strings of its .modinfo
        for m in dep:
            strings = ['license=GPL', 'name=' + m] + ['alias=' + a for a in aliases.get(m, [])]
            (moddir / (m + '.ko')).write_bytes(b'\x7fELF\0' + '\0'.join(strings).encode() + b'\0')
        # Ubuntu has depmod's modules.dep next to the modules (the guard takes in their dependants from it)
        (moddir / 'modules.dep').write_text((out / 'modules.dep').read_text())
        lines = ''.join('%s\t%s\n' % (m, a) for m, al in aliases.items() for a in al)
        (d / 'aliases').write_text(lines)
        fake = d / 'modinfo'
        fake.write_text('#!/bin/sh\nm=${1##*/}; m=${m%%.ko*}\nawk -F"\\t" -v m="$m" \'$1 == m { print "alias:\\t\\t" $2 }\' '
                        '"%s"\n' % (d / 'aliases'))
        fake.chmod(0o755)
        log = d / 'modprobe.log'
        mp = d / 'modprobe'
        mp.write_text('#!/bin/sh\necho "$@" >> "%s"\n' % log)
        mp.chmod(0o755)
        env = dict(os.environ, MU300_AUTOLOAD_BLOCKED=str(listfile), MU300_MODPROBE=str(mp), MU300_MODINFO=str(fake),
                   MU300_MODDIR=str(moddir), MU300_KREL='test', MU300_GUARD_CACHE=str(d / 'cache'),
                   MU300_KMSG=str(d / 'kmsg'), MU300_AUTOLOAD_ALLOW=str(d / 'allow'))
        # the guard takes one request per run, as the kernel calls it; a batch keeps this quick
        def run(reqs):
            sh = ' '.join(shlex.quote(x) for x in shell)
            script = ''.join('%s %s -q -- %s && echo PASS %s || echo REFUSED %s\n'
                             % (sh, shlex.quote(str(guard)), shlex.quote(r), shlex.quote(r), shlex.quote(r))
                             for r in reqs)
            res = subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True)
            return dict(l.split(' ', 1)[::-1] for l in res.stdout.splitlines() if l.startswith(('PASS ', 'REFUSED ')))
        want_refused = []
        for m in sorted(blocked & set(dep)):
            want_refused += [m, m.replace('_', '-')] + aliases.get(m, [])
        got = run(want_refused)
        errors += ['not refused: %s' % r for r in want_refused if got.get(r) != 'REFUSED']
        if log.exists():
            errors.append('modprobe was called for a blocked request: ' + log.read_text().splitlines()[0])
        log.unlink(missing_ok=True)
        want_pass = []
        for m in MUST_PASS:
            if m in dep:
                want_pass += [m] + aliases.get(m, [])[:20]
        got = run(want_pass)
        errors += ['refused: %s' % r for r in want_pass if got.get(r) != 'PASS']
        called = log.read_text().splitlines() if log.exists() else []
        if len(called) != len(want_pass):
            errors.append('modprobe called %d times for %d allowed requests' % (len(called), len(want_pass)))
    if errors:
        sys.exit('check-autoload-blocked: ' + '\n  '.join(errors[:40]))
    print('check-autoload-blocked: %d blocked modules (%d requests) refused, %d allowed requests handed on' %
          (len(blocked & set(dep)), len(want_refused), len(want_pass)))


if __name__ == '__main__':
    check(*sys.argv[1:4])
