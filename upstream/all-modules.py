#!/usr/bin/env python3
"""Turn every tristate option that can be a module on this board into =m (build.sh, after the fragment).

    python3 all-modules.py KERNEL_SRC O_DIR

Not allmodconfig: what the fragment built in stays built in (checked), bools are not touched except the few in
all-modules.config that open a family of drivers, and newly visible debug/test bools are switched off again. The
expansion is a fixpoint: a tristate whose dependencies are now met becomes =m, until nothing changes.

Left out (all-modules.exclude, regular expressions on the option name): test, debug and benchmark code, parts that
only work wired up on a board (ASoC codecs, display panels and bridges), and the drivers that would bind to the
vendor device tree these boards boot with (check-dt-modules.py finds them after the build). PCI drivers are left
out too: the only PCIe link is the soldered Marlin3 Wi-Fi/BT chip, so the expansion runs with PCI off and only the
options that made it there are taken over.

The report ($O/all-modules.report) lists the new modules and every bool that came on with them.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# debug and test switches that must stay off whatever came on with a driver (the ones the boot depends on staying
# off: kprobes and the tracing they pull in broke 2 of 36 boots of 7.2, FINDINGS 31p)
NEW_BOOL_OFF = re.compile(r'DEBUG|(^|_)TESTS?($|_)|KUNIT|TORTURE|SELFTEST|VERBOSE|TRACE|TRACING|DUMP|INJECT|'
                          r'WERROR|COMPILE_TEST|SAMPLE|BENCH')
FORBIDDEN_Y = ['KPROBES', 'FTRACE', 'FUNCTION_TRACER', 'KPROBE_EVENTS', 'BPF_EVENTS', 'PERF_EVENTS', 'KASAN',
               'UBSAN', 'KCSAN', 'KFENCE', 'LOCKDEP', 'PROVE_LOCKING', 'DEBUG_KMEMLEAK', 'COMPILE_TEST', 'STAGING',
               'RANDOMIZE_BASE', 'MODULE_SIG_FORCE', 'DEBUG_INFO_REDUCED',
               # the DMA path of every device with an "iommus" property in the vendor tree, and the vendor's
               # shared-dma-pool regions turned into CMA: SoC GPU/display drivers select these
               'IOMMU_SUPPORT', 'IOMMU_API', 'IOMMU_DMA', 'OF_IOMMU', 'CMA', 'DMA_CMA']


def read(path):
    vals = {}
    with open(path) as f:
        lines = f.readlines()
    for line in lines:
        m = re.match(r'CONFIG_(\w+)=(.*)', line)
        if m:
            vals[m.group(1)] = m.group(2)
        else:
            m = re.match(r'# CONFIG_(\w+) is not set', line)
            if m:
                vals[m.group(1)] = 'n'
    return vals


def write_set(path, values):
    """Set CONFIG_<k>=<v> for each item (replacing the line that names it), as scripts/config would."""
    keys = set(values)
    out = []
    with open(path) as f:
        lines = f.readlines()
    for l in lines:
        m = re.match(r'(?:# )?CONFIG_(\w+)[= ]', l)
        if m and m.group(1) in keys:
            continue
        out.append(l)
    out += ['CONFIG_%s=%s\n' % (k, v) if v != 'n' else '# CONFIG_%s is not set\n' % k for k, v in sorted(values.items())]
    with open(path, 'w') as f:
        f.writelines(out)


def olddefconfig(src, o, cfg):
    subprocess.run(['make', '-s', '-C', src, 'O=' + o, 'ARCH=arm64', 'KCONFIG_CONFIG=' + cfg, 'olddefconfig'],
                   check=True, stdout=subprocess.DEVNULL)


def tristates(src):
    """Every option declared tristate in some Kconfig file of the tree, with the directory of that file."""
    found = {}
    head = re.compile(r'^\s*(?:menu)?config\s+(\w+)')
    typ = re.compile(r'^\s*(tristate|def_tristate|bool|def_bool|int|hex|string)\b')
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('Documentation', 'tools', 'samples')]
        for f in files:
            if not f.startswith('Kconfig'):
                continue
            cur = None
            with open(os.path.join(root, f), errors='replace') as kf:
                klines = kf.readlines()
            for line in klines:
                m = head.match(line)
                if m:
                    cur = m.group(1)
                    continue
                m = typ.match(line)
                if m and cur:
                    if m.group(1) in ('tristate', 'def_tristate'):
                        found.setdefault(cur, os.path.relpath(root, src))
                    cur = None
    return found


def rules():
    """all-modules.exclude: 'dir PATH' (options declared under PATH), 'keep REGEX' (taken even so), REGEX."""
    dirs, keep, pats = [], [], []
    with open(os.path.join(HERE, 'all-modules.exclude')) as f:
        rule_lines = f.readlines()
    for line in rule_lines:
        line = line.split('#', 1)[0].strip()
        if line.startswith('dir '):
            dirs.append(line[4:].strip().rstrip('/') + '/')
        elif line.startswith('keep '):
            keep.append(re.compile(line[5:].strip()))
        elif line:
            pats.append(re.compile(line))
    return dirs, keep, pats


def wanted(sym, where, dirs, keep, pats):
    if any(p.search(sym) for p in keep):
        return True
    return not (any((where + '/').startswith(d) for d in dirs) or any(p.search(sym) for p in pats))


def fixpoint(src, o, cfg, cands, fam):
    """Set every candidate that is off to m, olddefconfig, again until the set of modules stops growing. The
    switches of all-modules.config go in every round: one that only becomes visible with a new driver (a bool
    under it) would otherwise be dropped before it counts."""
    for _ in range(40):
        cur = read(cfg)
        todo = {k: 'm' for k in cands if cur.get(k, 'n') == 'n'}
        before = {k for k, v in cur.items() if v == 'm'}
        write_set(cfg, dict(todo, **fam))
        olddefconfig(src, o, cfg)
        after = {k for k, v in read(cfg).items() if v == 'm'}
        if after <= before:
            return
    sys.exit('all-modules: no fixpoint after 40 rounds')


def main():
    src, o = sys.argv[1], sys.argv[2]
    cfg = os.path.join(o, '.config')
    base = read(cfg)
    base_y = {k for k, v in base.items() if v == 'y'}
    # the bools that open a family of drivers
    fam = {}
    with open(os.path.join(HERE, 'all-modules.config')) as f:
        fam_lines = f.readlines()
    for line in fam_lines:
        m = re.match(r'CONFIG_(\w+)=(.*)', line)
        if m:
            fam[m.group(1)] = m.group(2)
    write_set(cfg, fam)
    olddefconfig(src, o, cfg)
    dirs, keep, pats = rules()
    tri = tristates(src)
    cands = sorted(k for k, where in tri.items() if wanted(k, where, dirs, keep, pats))
    # the expansion with PCI off: what survives there does not need a PCI card
    tmp = os.path.join(o, 'all-modules.nopci')
    shutil.copy(cfg, tmp)
    write_set(tmp, {'PCI': 'n'})
    olddefconfig(src, o, tmp)
    fixpoint(src, o, tmp, cands, dict(fam, PCI='n'))
    nopci = {k for k, v in read(tmp).items() if v == 'm' and k in cands}
    os.remove(tmp)
    fixpoint(src, o, cfg, sorted(nopci), fam)
    # bools that came on with the new drivers and look like debugging or tests go off again
    now = read(cfg)
    off = {k: 'n' for k, v in now.items() if v == 'y' and k not in base_y and k not in fam and NEW_BOOL_OFF.search(k)}
    if off:
        write_set(cfg, off)
        olddefconfig(src, o, cfg)
    final = read(cfg)
    lost = sorted(k for k in base_y if final.get(k) != 'y')
    if lost:
        sys.exit('all-modules: built-in options changed: ' + ' '.join('%s=%s' % (k, final.get(k, 'n')) for k in lost))
    bad = [k for k in FORBIDDEN_Y if final.get(k) == 'y' and base.get(k) != 'y']
    bad += [k for k, v in fam.items() if final.get(k, 'n') != v and v == 'n']
    if bad:
        sys.exit('all-modules: turned on what must stay off: ' + ' '.join(bad))
    new_m = sorted(k for k, v in final.items() if v == 'm' and base.get(k) != 'm')
    new_y = sorted(k for k, v in final.items() if v == 'y' and k not in base_y)
    with open(os.path.join(o, 'all-modules.report'), 'w') as f:
        f.write('# new =m (%d)\n' % len(new_m))
        f.writelines('CONFIG_%s=m\n' % k for k in new_m)
        f.write('# new =y (%d): family switches and what came on with the new drivers\n' % len(new_y))
        f.writelines('CONFIG_%s=y\n' % k for k in new_y)
        f.write('# switched off again (%d)\n' % len(off))
        f.writelines('# CONFIG_%s is not set\n' % k for k in sorted(off))
    print('all-modules: %d options newly =m, %d bools newly =y, %d debug/test bools kept off; built-ins unchanged'
          % (len(new_m), len(new_y), len(off)))


if __name__ == '__main__':
    main()
