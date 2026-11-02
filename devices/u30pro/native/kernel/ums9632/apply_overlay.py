#!/usr/bin/env python3
"""Apply reviewed U30Pro source overlay to the isolated upstream tree."""
from pathlib import Path
import os
import shutil,json,hashlib
PROJECT=Path(__file__).resolve().parents[2]
PORT=PROJECT/'kernel/ums9632';BASE=Path(os.environ['U30PRO_WORKSPACE']).resolve();TREE=Path(os.environ['U30PRO_KERNEL_SOURCE']).resolve()
assert TREE.resolve().is_relative_to(BASE.resolve()) and (TREE/'Makefile').is_file()
for p in sorted((PORT/'overlay').rglob('*')):
 if p.is_file():
  dest=TREE/p.relative_to(PORT/'overlay');dest.parent.mkdir(parents=True,exist_ok=True)
  if not dest.exists() or dest.read_bytes()!=p.read_bytes():shutil.copyfile(p,dest)
adds={'drivers/mmc/host/Kconfig':'source "drivers/mmc/host/Kconfig.u30pro"','drivers/base/regmap/Kconfig':'source "drivers/base/regmap/Kconfig.u30pro"','drivers/base/regmap/Makefile':'obj-$(CONFIG_REGMAP_MMIO_SETCLR_KUNIT_TEST) += regmap-mmio-setclr-test.o','drivers/watchdog/Kconfig':'source "drivers/watchdog/Kconfig.u30pro"','drivers/watchdog/Makefile':'obj-$(CONFIG_U30PRO_UMP9650_WATCHDOG) += u30pro-ump9650-wdt.o\nobj-$(CONFIG_U30PRO_WATCHDOG_TEST) += u30pro-watchdog-test.o','drivers/nvmem/Kconfig':'source "drivers/nvmem/Kconfig.u30pro"','drivers/nvmem/Makefile':'obj-$(CONFIG_NVMEM_SPRD_SHADOW_EFUSE) += sprd-shadow-efuse.o','drivers/regulator/Kconfig':'source "drivers/regulator/Kconfig.u30pro"','drivers/regulator/Makefile':'obj-$(CONFIG_REGULATOR_UMP9650) += ump9650-regulator.o\nobj-$(CONFIG_REGULATOR_UMP9651) += ump9651-regulator.o','drivers/Kconfig':'source "drivers/unisoc_platform/Kconfig"','drivers/Makefile':'obj-$(CONFIG_U30PRO_BSP) += unisoc_platform/','drivers/clk/Kconfig':'source "drivers/clk/sprd-u30pro/Kconfig"','drivers/clk/Makefile':'obj-$(CONFIG_U30PRO_VENDOR_CLK) += sprd-u30pro/','drivers/pinctrl/Kconfig':'source "drivers/pinctrl/sprd-u30pro/Kconfig"','drivers/pinctrl/Makefile':'obj-$(CONFIG_PINCTRL_SPRD_UMS9632) += sprd-u30pro/'}
adds.update({'drivers/power/supply/Kconfig':'source "drivers/power/supply/Kconfig.u30pro"','drivers/power/supply/Makefile':'obj-$(CONFIG_CHARGER_U30PRO_SGM41513) += u30pro-sgm41513.o'})
adds.update({'drivers/iio/adc/Kconfig':'source "drivers/iio/adc/Kconfig.u30pro"','drivers/iio/adc/Makefile':'obj-$(CONFIG_U30PRO_UMP965X_ADC) += u30pro-ump965x-adc.o'})
for n,line in adds.items():
 p=TREE/n;s=p.read_text()
 if line not in s:p.write_text(s+'\n'+line+'\n')
for n,line in {'drivers/power/supply/Makefile':'obj-$(CONFIG_BATTERY_U30PRO_UMP965X) += u30pro-ump965x-fgu.o','arch/arm64/Kconfig':'source "arch/arm64/Kconfig.u30pro"','arch/arm64/kernel/Makefile':'obj-$(CONFIG_U30PRO_EARLY_RAMCON) += u30pro-early-ramcon.o'}.items():
 p=TREE/n;s=p.read_text()
 if line not in s:p.write_text(s+'\n'+line+'\n')
p=TREE/'arch/arm64/kernel/Makefile';s=p.read_text();line='obj-$(CONFIG_U30PRO_TIMED_RESET) += u30pro-timed-reset.o'
if line not in s:p.write_text(s+'\n'+line+'\n')
def edit_once(name,old,new,ignore=''):
 p=TREE/name;s=p.read_text()
 if new not in s:
  # A later diagnostic hook can be nested inside this exact earlier block.
  # Ignore only that explicitly named line when verifying prior application.
  if ignore and new in s.replace(ignore,''):return
  assert s.count(old)==1,(name,old)
  p.write_text(s.replace(old,new))
for n in ['arch/arm64/kernel/setup.c','init/main.c']:
 edit_once(n,'#include <linux/init.h>','#include <linux/init.h>\n#include <linux/u30pro_early_ramcon.h>')
edit_once('fs/pstore/ram.c','#include <linux/module.h>','#include <linux/module.h>\n#include <linux/u30pro_early_ramcon.h>')
edit_once('arch/arm64/include/asm/fixmap.h','\tFIX_EARLYCON_MEM_BASE,\n','\tFIX_EARLYCON_MEM_BASE,\n#ifdef CONFIG_U30PRO_EARLY_RAMCON\n\tFIX_U30PRO_RAMCON,\n#endif\n')
edit_once('arch/arm64/kernel/setup.c','\tjump_label_init();\n\tparse_early_param();','\tjump_label_init();\n\tu30pro_early_ramcon_init();\n\tu30pro_early_ramcon_checkpoint("before-early-params");\n\tparse_early_param();\n\tu30pro_early_ramcon_checkpoint("after-early-params");',ignore='\tu30pro_reset_checkpoint(6);\n')
edit_once('init/main.c','\tearly_irq_init();','\tu30pro_early_ramcon_checkpoint("before-irq-init");\n\tearly_irq_init();')
edit_once('init/main.c','\tinit_IRQ();','\tinit_IRQ();\n\tu30pro_early_ramcon_checkpoint("after-irq-init");')
edit_once('init/main.c','\ttime_init();','\tu30pro_early_ramcon_checkpoint("before-time-init");\n\ttime_init();\n\tu30pro_early_ramcon_checkpoint("after-time-init");')
edit_once('init/main.c','\tsmp_init();','\tu30pro_early_ramcon_checkpoint("before-smp");\n\tsmp_init();\n\tu30pro_early_ramcon_checkpoint("after-smp");')
edit_once('fs/pstore/ram.c','\terr = ramoops_init_prz("console", dev, cxt, &cxt->cprz, &paddr,','\tu30pro_early_ramcon_handoff(paddr);\n\terr = ramoops_init_prz("console", dev, cxt, &cxt->cprz, &paddr,')
# Positive boot-stage evidence when reset-path RAM retention is unverified.
# Normal profiles leave the option disabled, eliminating all these hooks.
for n in ['arch/arm64/kernel/setup.c','init/main.c']:
 edit_once(n,'#include <linux/u30pro_early_ramcon.h>','#include <linux/u30pro_early_ramcon.h>\n#include <linux/u30pro_timed_reset.h>')
for anchor,stage in [('SYM_CODE_START(primary_entry)',1),('\tmov\tx20, x0\n',2),('SYM_FUNC_START_LOCAL(__primary_switched)',4)]:
 hook='\n#if defined(CONFIG_U30PRO_TIMED_RESET) && CONFIG_U30PRO_TIMED_RESET_STAGE == '+str(stage)+'\n\tb\tu30pro_timed_reset\n#endif\n'
 edit_once('arch/arm64/kernel/head.S',anchor,anchor+hook)
anchor='\tbl\t__cpu_setup\t\t\t// initialise processor\n\tb\t__primary_switch'
edit_once('arch/arm64/kernel/head.S',anchor,'\tbl\t__cpu_setup\t\t\t// initialise processor\n#if defined(CONFIG_U30PRO_TIMED_RESET) && CONFIG_U30PRO_TIMED_RESET_STAGE == 3\n\tb\tu30pro_timed_reset\n#endif\n\tb\t__primary_switch')
edit_once('init/main.c','\tset_task_stack_end_magic(&init_task);','\tu30pro_reset_checkpoint(5);\n\tset_task_stack_end_magic(&init_task);')
edit_once('arch/arm64/kernel/setup.c','\tu30pro_early_ramcon_init();','\tu30pro_reset_checkpoint(6);\n\tu30pro_early_ramcon_init();')
edit_once('init/main.c','\tu30pro_early_ramcon_checkpoint("before-irq-init");','\tu30pro_reset_checkpoint(7);\n\tu30pro_early_ramcon_checkpoint("before-irq-init");')
edit_once('init/main.c','\tu30pro_early_ramcon_checkpoint("before-smp");','\tu30pro_reset_checkpoint(8);\n\tu30pro_early_ramcon_checkpoint("before-smp");')
edit_once('init/main.c','\tu30pro_early_ramcon_checkpoint("after-smp");','\tu30pro_early_ramcon_checkpoint("after-smp");\n\tu30pro_reset_checkpoint(9);')
edit_once('init/main.c','\tdo_basic_setup();','\tdo_basic_setup();\n\tu30pro_reset_checkpoint(10);')
# Blacklisted initcalls return before the before-hook. An after-hook means
# only that the function returned, not that its device probe succeeded.
edit_once('init/main.c','\tdo_trace_initcall_start(fn);','\tu30pro_reset_initcall((unsigned long)fn, false);\n\tdo_trace_initcall_start(fn);')
edit_once('init/main.c','\tdo_trace_initcall_finish(fn, ret);','\tdo_trace_initcall_finish(fn, ret);\n\tu30pro_reset_initcall((unsigned long)fn, true);')
# Generic OF already implements CMDLINE_EXTEND; arm64 6.12 lacks its choice.
# Expose that existing behavior without replacing the firmware command line.
p=TREE/'arch/arm64/Kconfig';s=p.read_text()
if '\nconfig CMDLINE_EXTEND\n' not in s:
 anchor='config CMDLINE_FORCE\n'
 assert s.count(anchor)==1
 extra='config CMDLINE_EXTEND\n\tbool "Append built-in arguments to the bootloader command line"\n\thelp\n\t  Retain bootloader arguments and append CONFIG_CMDLINE using the\n\t  existing generic OF command-line extension implementation.\n\n'
 p.write_text(s.replace(anchor,extra+anchor))
print('PORT_OVERLAY_APPLIED',TREE)
