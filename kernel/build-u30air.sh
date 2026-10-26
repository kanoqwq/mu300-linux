#!/bin/bash
# The ZTE U30 Air modules: the MU300 kernel config (/src/out-linux/.config, from build-linux.sh) plus
# u30air.fragment, built in /src/out-u30air. What goes out is only what differs from the MU300 set - modules the
# F50 does not have, and same-named ones built differently (sprd-charger-manager from the SQC charger manager) -
# into /work/out/modules-u30air/, for the boot ramdisk and the root filesystems to load on a U30 Air only.
set -e
cd /src/zte-u30air
BASE=/src/out-linux
OUT=/src/out-u30air
mkdir -p $OUT
M="make O=$OUT ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang LD=ld.lld -j10"
cp $BASE/.config $OUT/.config
KCONFIG_CONFIG=$OUT/.config ./scripts/kconfig/merge_config.sh -m -O $OUT $OUT/.config /work/u30air.fragment > /work/u30air-merge.log 2>&1
$M olddefconfig >/dev/null
python3 - <<'P'
import re
def load(f):
    d={}
    for l in open(f):
        m=re.match(r'(CONFIG_\w+)=(.*)',l) or re.match(r'# (CONFIG_\w+) is not set',l)
        if m: d[m.group(1)]=m.group(2) if m.lastindex==2 else 'n'
    return d
want, got = load('/work/u30air.fragment'), load('/src/out-u30air/.config')
bad=[f'{k}: want {v} got {got.get(k,"absent")}' for k,v in want.items() if got.get(k,'absent')!=v]
print('u30air fragment rejected:', len(bad)); [print('  '+b) for b in bad]
P
# no module of an earlier build may linger (one the fragment no longer builds would be taken for a U30 Air module)
find $OUT -name '*.ko' -delete
$M Image modules > /work/build-u30air.log 2>&1 || status=$?
grep -E "error|Error" /work/build-u30air.log | tail -20 || true
[ -z "${status:-}" ] || { echo "U30 Air kernel build failed (make exit $status)"; exit "$status"; }
# the Image must be the MU300 one: a fragment option that changes built-in code shows up as a different set of
# symbols vmlinux exports to modules (I2C_SMBUS did: of_i2c_setup_smbus_alert)
cmp -s <(awk '$3=="vmlinux"' $BASE/Module.symvers | sort) <(awk '$3=="vmlinux"' $OUT/Module.symvers | sort) \
    && echo "vmlinux exports: identical" || { echo "vmlinux exports differ between the two builds" >&2; exit 1; }
rm -rf /work/out/modules-u30air && mkdir -p /work/out/modules-u30air
for ko in $(cd $OUT && find . -name '*.ko'); do
    n=$(basename $ko)
    [ $n = kheaders.ko ] && continue   # the kernel headers and config as a module: differs, never loaded
    base=$(cd $BASE && find . -name "$n" | head -1)
    # the same module from both builds is not the same file (build id, symbol table order): compare what it
    # runs - code, data and module information
    if [ -n "$base" ]; then
        same=1
        for s in .text .rodata .data .modinfo __ksymtab_strings; do
            llvm-objcopy -O binary --only-section=$s $OUT/$ko /tmp/new.s 2>/dev/null || : > /tmp/new.s
            llvm-objcopy -O binary --only-section=$s $BASE/$base /tmp/old.s 2>/dev/null || : > /tmp/old.s
            cmp -s /tmp/new.s /tmp/old.s || { same=0; break; }
        done
        [ $same = 1 ] && continue
    fi
    llvm-strip --strip-debug -o /work/out/modules-u30air/$n $OUT/$ko
done
ls /work/out/modules-u30air | tr '\n' ' '; echo
echo "$(ls /work/out/modules-u30air | wc -l) U30 Air modules"
