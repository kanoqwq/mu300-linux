#!/usr/bin/env python3
"""Wrap a zero-offset Linux Image for an uncertain legacy load address, offline."""
from pathlib import Path
import argparse,struct,hashlib,json,subprocess,tempfile,shutil
ap=argparse.ArgumentParser();ap.add_argument('--image',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--text-offset',type=lambda x:int(x,0),default=0x80000);ap.add_argument('--journal-test',action='store_true');ap.add_argument('--stock-control',action='store_true');a=ap.parse_args()
source=Path(__file__).resolve().parent/'native/align_image.S'
b=a.image.read_bytes();assert b[56:60]==b'ARMd';text,mem,flags=struct.unpack_from('<QQQ',b,8)
assert text==0 and len(b)%8==0 and len(b)>4096 and mem>=len(b) and flags&1==0
assert 0<=a.text_offset<0x200000 and a.text_offset%4096==0
arena=(4096+0x200000+mem+4095)&~4095
# The original B Image reserves 37,224,448 bytes: stay strictly inside that size.
if a.stock_control:
 assert hashlib.sha256(b).hexdigest()=='fc9bd8f2c77ec889cacb100a8723bb5714027fe5d97e03bbf44df117bb601435'
 assert len(b)==36600320 and mem==37224448 and flags==0xa
 # Only this fixed stock control may enlarge the outer arena. With the
 # observed 0x80080000 load, even worst-case alignment/BSS ends below
 # 0x82800000, well before the firmware FDT at 0x86000000.
 assert arena<=0x2800000 and 4096+len(b)<64*1024*1024-4096
else:assert arena<=37224448,'Payload no longer fits the observed stock kernel arena'
assert not a.out.exists() and not a.out.with_suffix('.manifest.json').exists()
a.out.parent.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='u30pro-align-') as t:
 t=Path(t);(t/'payload.bin').write_bytes(b)
 args=['clang','--target=aarch64-none-elf','-c','-x','assembler-with-cpp',str(source),'-o',str(t/'wrapper.o'),'-DOUTER_TEXT_OFFSET='+str(a.text_offset),'-DARENA_SIZE='+str(arena),'-DIMAGE_FLAGS='+str(flags),'-DPAYLOAD_MEMORY_SIZE='+str(mem),'-DPAYLOAD_FILE_SIZE='+str(len(b))]
 if a.journal_test:args+=['-DU30PRO_JOURNAL_FDT=0x48600000','-DU30PRO_JOURNAL_SLOT=0x46ff8000','-DU30PRO_JOURNAL_ADDR=0x46fff000','-DU30PRO_JOURNAL_SIG=0']
 subprocess.run(args,cwd=t,check=True)
 subprocess.run(['ld.lld','-Ttext=0','-e','_start','--oformat=binary',str(t/'wrapper.o'),'-o',str(t/'wrapped.bin')],check=True)
 wrapped=(t/'wrapped.bin').read_bytes();assert len(wrapped)==4096+len(b) and wrapped[4096:]==b
 assert wrapped[56:60]==b'ARMd' and struct.unpack_from('<QQQ',wrapped,8)==(a.text_offset,arena,flags)
 a.out.write_bytes(wrapped)
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
m={'scope':'Offline raw Image relocation wrapper; not a flash package','input_sha256':h(a.image),'output_sha256':h(a.out),'asm_sha256':h(source),'payload_bytes':len(b),'payload_memory_bytes':mem,'arena_bytes':arena,'prefix_bytes':4096,'outer_text_offset':a.text_offset,'destination_formula':'ALIGN_UP(actual_load_address + 4096, 2 MiB)','copy':'backwards memmove; PoC clean + I-cache invalidation','preserve':'FDT x0; x1-x3 zero; no DTB/slot/device writes','entry_state':'EL1/EL2 MMU off or firmware identity map; full cache clean/invalidate then SCTLR M/C/I cleared for raw non-EFI Linux','test_only':a.journal_test,'early_journal':{'bytes':128,'exact_fdt_gate':hex(0x48600000 if a.journal_test else 0x86000000),'empty_slot_header_required':True,'address':hex(0x46fff000 if a.journal_test else 0xfffaff00),'stage_commit':'stage xor final word must equal uint64 max'},'physical_load_address_assumed':False,'physical_boot_verified':False}
a.out.with_suffix('.manifest.json').write_text(json.dumps(m,indent=2)+'\n');print(json.dumps(m))
