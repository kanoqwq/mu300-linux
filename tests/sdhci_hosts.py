#!/usr/bin/env python3
"""Patch a scratch copy of a prepared SDHCI source, compile its probe guard."""
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
apply=runpy.run_path(str(root/'upstream/port/sdhci_hosts.py'))['apply']
with tempfile.TemporaryDirectory(prefix='mu300-sdhci-hosts-') as tmp:
    tree=Path(tmp); p=tree/'drivers/mmc/host/sdhci-sprd.c';p.parent.mkdir(parents=True)
    shutil.copyfile(Path(sys.argv[1])/'drivers/mmc/host/sdhci-sprd.c',p)
    apply(tree); first=p.read_bytes(); apply(tree); assert p.read_bytes()==first
    s=p.read_text();a=s.index('\t/* MU300: WLAN uses PCIe');b=s.index('\thost = sdhci_pltfm_init(',a)
    guard=s[a:b]
    fixture=r'''
#include <assert.h>
#include <string.h>
#include <errno.h>
struct platform_device { struct { void *of_node; } dev; };
static const char *machine,*name;
static int of_machine_is_compatible(const char *s) { return !strcmp(machine,s); }
static int of_property_match_string(void *n,const char *p,const char *v) {
    (void)n; (void)p; return name && !strcmp(name,v) ? 0 : -1;
}
static int probe(struct platform_device *pdev) {
''' + guard + r'''
    return 1;
}
int main(void) {
    struct platform_device p={0};
    const char *machines[]={"sprd,ums9620","sprd,ums9632","other"};
    const char *names[]={"sdio_wifi","sdio_sd","emmc","sdio_wlan",NULL};
    for(unsigned i=0;i<3;i++) for(unsigned j=0;j<5;j++) {
        machine=machines[i];name=names[j];
        assert(probe(&p)==(i==0 && j==0 ? -ENODEV : 1));
    }
}
'''
    c=tree/'probe.c';c.write_text(fixture);exe=tree/'probe'
    subprocess.run(['cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(c),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS SDHCI spare host: 15 platform/controller cases; idempotent on prepared source')
