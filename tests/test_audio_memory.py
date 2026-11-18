"""Execute the early-reservation code against FDT/memblock fixtures only."""
import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

APPLY=runpy.run_path(str(TOP/'upstream/port/audio_memory.py'))['apply']


class AudioMemory(unittest.TestCase):
    def test_early_hook_is_idempotent_and_rejects_an_unknown_lifecycle(self):
        text='''void __init early_init_fdt_scan_reserved_mem(void)
{
    fdt_scan_reserved_mem();
    for (;;) {
\tmemblock_reserve(base, size);
    }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'drivers/of/fdt.c';path.parent.mkdir(parents=True)
            path.write_text(text);APPLY(root);first=path.read_text();APPLY(root)
            self.assertEqual(first,path.read_text())
            self.assertEqual(first.count('#include "ums9620-audio-memory.h"'),1)
            self.assertEqual(first.count('ums9620_reserve_audio_memory();'),1)
            self.assertGreater(first.index('ums9620_reserve_audio_memory();'),first.index('memblock_reserve(base, size);'))
            path.write_text(text.replace('memblock_reserve(base, size);','unknown_reservation_api();'))
            with self.assertRaises(ValueError):APPLY(root)
            self.assertNotIn('ums9620_reserve_audio_memory',path.read_text())

    @unittest.skipUnless(shutil.which('cc'),'native C compiler required')
    def test_real_reservation_function_bounds_board_isolation_and_existing_reservations(self):
        header=TOP/'upstream/port/drivers/of/ums9620-audio-memory.h'
        code='\n'.join(line for line in header.read_text().splitlines() if not line.startswith('#include'))
        prelude=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
typedef uint32_t __be32;
#define __init
#define SZ_1M (1ULL << 20)
#define SZ_16M (16ULL << 20)
#define PAGE_SIZE 4096
#define ALIGN(n,a) (((n)+(a)-1)&~((u64)(a)-1))
#define IS_ALIGNED(n,a) (!((n)&((a)-1)))
#define BIT_ULL(n) (1ULL<<(n))
#define fdt32_to_cpu(n) __builtin_bswap32(n)
#define pr_info(...) do {} while (0)
#define pr_warn(...) do { warnings++; } while (0)
#define pr_err(...) do { errors++; } while (0)
static const void *initial_boot_params=(void *)1;
static int other_soc, no_node, ddr_len, cmd_len, warnings, errors, fail_reserve;
static __be32 ddr[2],cmd[2];
static u64 ram_start,ram_end,reserved_start,reserved_end;
static int fdt_node_check_compatible(const void *p,int n,const char *s) {
 assert(p==initial_boot_params && n==0 && !strcmp(s,"sprd,ums9620"));return other_soc;
}
static int fdt_path_offset(const void *p,const char *s) {
 assert(p==initial_boot_params && !strcmp(s,"/audio-mem-mgr"));return no_node?-1:7;
}
static const void *fdt_getprop(const void *p,int n,const char *s,int *len) {
 assert(p==initial_boot_params && n==7);
 if (!strcmp(s,"sprd,ddr32-dma")) {*len=ddr_len;return ddr_len<0?0:ddr;}
 assert(!strcmp(s,"sprd,cmdaddr"));*len=cmd_len;return cmd_len<0?0:cmd;
}
static int memblock_is_region_memory(u64 base,u64 size) {
 return base>=ram_start && base+size<=ram_end;
}
static int memblock_reserve(u64 base,u64 size) {
 if (fail_reserve)return -1;
 if (!reserved_end) {reserved_start=base;reserved_end=base+size;}
 else {assert(base==reserved_start);if(base+size>reserved_end)reserved_end=base+size;}
 return 0;
}
static void pair(__be32 *p,uint32_t a,uint32_t b) {p[0]=__builtin_bswap32(a);p[1]=__builtin_bswap32(b);}
static void reset(void) {
 other_soc=no_node=warnings=errors=fail_reserve=0;ddr_len=cmd_len=8;
 pair(ddr,0xaf700000,0x200000);pair(cmd,0xaf980000,0x400);
 ram_start=0x80000000;ram_end=0xc0000000;reserved_start=reserved_end=0;
}
'''
        cases=r'''
int main(void) {
 reset();ums9620_reserve_audio_memory();assert(reserved_start==0xaf700000 && reserved_end==0xb0000000);
 assert(!warnings && !errors);
 /* F50 with an existing vendor_boot reservation retains the same 9 MiB. */
 ums9620_reserve_audio_memory();assert(reserved_end-reserved_start==9*SZ_1M);
 /* U30 Air UFS and V50 use their live DT, not storage or firmware labels. */
 reset();pair(ddr,0x97000000,0x200000);pair(cmd,0x97280000,0x400);
 ums9620_reserve_audio_memory();assert(reserved_start==0x97000000 && reserved_end==0x97900000);
 reset();other_soc=1;ums9620_reserve_audio_memory();assert(!reserved_end && !warnings);
 reset();no_node=1;ums9620_reserve_audio_memory();assert(!reserved_end && !warnings);
 reset();ddr_len=-1;ums9620_reserve_audio_memory();assert(!reserved_end && warnings==1);
 reset();cmd_len=4;ums9620_reserve_audio_memory();assert(!reserved_end && warnings==1);
 reset();pair(cmd,0xaf700000,0x400);ums9620_reserve_audio_memory();assert(!reserved_end && warnings==1);
 reset();pair(cmd,0xaf980000,0);ums9620_reserve_audio_memory();assert(!reserved_end && warnings==1);
 reset();pair(ddr,0xffff0000,0x1000);pair(cmd,0xffff2000,0x1000);
 ums9620_reserve_audio_memory();assert(!reserved_end && warnings==1);
 reset();ram_end=0xa0000000;ums9620_reserve_audio_memory();assert(!reserved_end && warnings==1);
 reset();fail_reserve=1;ums9620_reserve_audio_memory();assert(!reserved_end && errors==1);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'audio.c';binary=root/'audio'
            source.write_text(prelude+code+cases)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary)],check=True,capture_output=True)


if __name__=='__main__':unittest.main()
