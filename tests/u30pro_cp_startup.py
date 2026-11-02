"""Compile and exercise the production loader probe without any device access."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.name == 'posix', 'POSIX C compiler')
class Startup(unittest.TestCase):
    def test_partial_auxiliary_boot_does_not_skip_baseband_loading(self):
        source = TOP/'devices/u30pro/native/modem-native-re/src/main.c'
        harness = r'''
#include <fcntl.h>
#include <unistd.h>
#include <assert.h>
#include <stdarg.h>
#include <errno.h>
#include <string.h>
static int probe_open(const char *, int, ...);
static int probe_close(int);
#define open probe_open
#define close probe_close
#define main loader_main
#include "SOURCE"
#undef main
#undef open
#undef close
static int mask, opened, closed;
static int probe_open(const char *name, int flags, ...) {
    const char *paths[] = {"/dev/sctl_pm", "/dev/sctl_ch", "/dev/spipe_nr2"};
    assert((flags & O_NONBLOCK) && (flags & O_CLOEXEC));
    for (int i = 0; i < 3; ++i) if (!strcmp(paths[i], name)) {
        if (!(mask & (1 << i))) { errno=ENODEV; return -1; }
        ++opened; return 100+i;
    }
    assert(0); return -1;
}
static int probe_close(int fd) { assert(fd>=100 && fd<=102); ++closed; return 0; }
void mc_log(int level, const char *format, ...) { (void)level; (void)format; }
int main(void) {
    for (mask = 0; mask < 8; ++mask) {
        opened=closed=0;
        assert(cp_already_booted() == (mask == 7));
        assert(opened == closed);
    }
    return 0;
}
'''.replace('SOURCE',str(source))
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'probe.c'; exe=Path(folder)/'probe'
            src.write_text(harness)
            subprocess.run(['cc','-std=gnu11','-O2','-ffunction-sections','-fdata-sections',str(src),'-Wl,--gc-sections','-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)

if __name__=='__main__': unittest.main()
