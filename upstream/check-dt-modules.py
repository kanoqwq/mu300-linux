#!/usr/bin/env python3
"""No module of the set may bind by itself to a node of the vendor device tree (build-modules.sh, after the build).

    python3 check-dt-modules.py MODULES_ALIAS dt-compatibles.txt [allowed module...]

Ubuntu's udev loads every module whose "of:" alias matches a node of the device tree at boot. The vendor tree these
boards boot with names hundreds of nodes (dt-compatibles.txt: every compatible string of the F50's and the U30
Air's /proc/device-tree, 2026-10-16), and a mainline driver that matched one of them would start driving a part of
the SoC next to (or instead of) the vendor driver - the expansion of all-modules.py must not bring one in. The
modules that are meant to bind (the vendor ones, the U30 Air's charger...) are named on the command line.
"""
import fnmatch
import re
import sys


def main():
    alias_file, compat_file = sys.argv[1], sys.argv[2]
    allowed = set(a.replace('-', '_') for a in sys.argv[3:])
    compats = [l.strip() for l in open(compat_file) if l.strip() and not l.startswith('#')]
    bad = {}
    for line in open(alias_file):
        m = re.match(r'alias (of:\S+) (\S+)', line)
        if not m:
            continue
        pat, mod = m.group(1), m.group(2).replace('-', '_')
        # only the aliases of a compatible string: "of:N*T*C<compatible>" (an alias of a node name, such as the
        # GRLIB drivers' "of:NGAISLER_APBPS2T(null)...", needs a node of that name, and this tree has none)
        if not pat.startswith('of:N*T*C'):
            continue
        # "...C*" at the end also matches a later compatible of the node
        cpat = re.sub(r'C\*$', '', pat[len('of:N*T*C'):])
        for comp in compats:
            if fnmatch.fnmatchcase(comp, cpat) and mod not in allowed:
                bad.setdefault(mod, set()).add(comp)
    if bad:
        for mod, comps in sorted(bad.items()):
            print('%s binds to %s' % (mod, ' '.join(sorted(comps))), file=sys.stderr)
        sys.exit('check-dt-modules: %d module(s) would load by themselves on the boards' % len(bad))
    print('check-dt-modules: no module binds to the vendor device tree by itself (%d compatibles)' % len(compats))


if __name__ == '__main__':
    main()
