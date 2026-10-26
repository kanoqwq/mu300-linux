#!/bin/sh
set -e
cd "$(dirname "$0")"
gcc -O2 -ffreestanding -nostdlib -fno-pic -fno-pie -no-pie -mgeneral-regs-only -mstrict-align \
  -Wl,-Ttext=0x80080040 -Wl,-e,_entry -Wl,--build-id=none -o pmic-reset.elf entry.S pmic-reset.c
objcopy -O binary pmic-reset.elf pmic-reset.bin
ls -la pmic-reset.bin
