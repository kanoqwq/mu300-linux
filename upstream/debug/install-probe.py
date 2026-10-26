#!/usr/bin/env python3
"""Insert mu300_probe_reset() calls at boot stages (idempotent). usage: install-probe.py <kernel tree> <stage>"""
import re, sys, shutil, os
tree, stage = sys.argv[1], int(sys.argv[2])
here = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(here, 'mu300-probe.c')).read().replace('#define MU300_RESET_STAGE 0', f'#define MU300_RESET_STAGE {stage}')
dst = os.path.join(tree, 'arch/arm64/kernel/mu300-probe.c')
if not os.path.exists(dst) or open(dst).read() != src:
    open(dst, 'w').write(src)
mk = os.path.join(tree, 'arch/arm64/kernel/Makefile')
m = open(mk).read()
if 'mu300-probe.o' not in m:
    open(mk, 'w').write(m + '\nobj-y += mu300-probe.o\n')
proto = 'void mu300_probe_reset(int stage, bool early);\n'
points = {
    'arch/arm64/kernel/setup.c': [('\tearly_ioremap_init();\n', 1, 'true'), ('\tpaging_init();\n', 2, 'true'),
                                  ('\tsetup_machine_fdt(__fdt_pointer);\n', 11, 'true'), ('\tparse_early_param();\n', 12, 'true'),
                                  ('\tlocal_daif_restore(DAIF_PROCCTX_NOIRQ);\n', 13, 'true'), ('\tarm64_memblock_init();\n', 14, 'true')],
    'init/main.c': [('\tmm_core_init();\n', 3, 'false'), ('\tinit_IRQ();\n', 4, 'false'), ('\ttime_init();\n', 5, 'false'),
                    ('\tconsole_init();\n', 6, 'false'), ('\trest_init();\n', 7, 'false'),
                    ('\tdo_basic_setup();\n', 9, 'false')],
}
for f, pts in points.items():
    p = os.path.join(tree, f); s = open(p).read()
    if 'mu300_probe_reset(14' not in s and f.endswith('setup.c') or 'mu300_probe_reset' not in s:
        # prototype after the last #include
        idx = [m.end() for m in re.finditer(r'^#include [^\n]*\n', s, re.M)][-1]
        s = s[:idx] + proto + s[idx:]
        for anchor, n, early in pts:
            assert s.count(anchor) >= 1, (f, anchor)
            s = s.replace(anchor, anchor + f'\tmu300_probe_reset({n}, {early});\n', 1)
        # stage 8: kernel_init entry (before do_basic_setup in kernel_init_freeable), stage 10: before running /init
        if f == 'init/main.c':
            s = s.replace('\tdo_basic_setup();\n\tmu300_probe_reset(9, false);\n',
                          '\tmu300_probe_reset(8, false);\n\tdo_basic_setup();\n\tmu300_probe_reset(9, false);\n', 1)
            a = '\tif (ramdisk_execute_command) {\n'
            s = s.replace(a, '\tmu300_probe_reset(10, false);\n' + a, 1)
        open(p, 'w').write(s)
print('probe stage', stage)
