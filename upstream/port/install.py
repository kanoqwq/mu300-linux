#!/usr/bin/env python3
"""Copy the MU300 driver ports (drivers ported from the Unisoc 5.4 tree) into a mainline tree and hook up
Makefile/Kconfig entries. Idempotent. usage: install.py <kernel tree>"""
import os, re, shutil, sys
tree = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
from tcpm_role_recovery import apply as discard_rejected_tcpm_role_trial
discard_rejected_tcpm_role_trial(tree, enable=False)
for root, _, files in os.walk(here):
    for f in files:
        if f in ('install.py',) or root == here:
            continue
        rel = os.path.relpath(os.path.join(root, f), here)
        dst = os.path.join(tree, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst) or open(os.path.join(root, f), 'rb').read() != open(dst, 'rb').read():
            shutil.copyfile(os.path.join(root, f), dst)

def append_once(path, marker, text):
    p = os.path.join(tree, path)
    s = open(p).read()
    if marker not in s:
        open(p, 'w').write(s.rstrip('\n') + '\n' + text)

append_once('drivers/ufs/host/Makefile', 'ufs-sprd-qogirn6pro.o',
            'obj-$(CONFIG_SCSI_UFS_SPRD_QOGIRN6PRO) += ufs-sprd-qogirn6pro.o\n')
append_once('drivers/ufs/host/Kconfig', 'config SCSI_UFS_SPRD_QOGIRN6PRO',
            '\nconfig SCSI_UFS_SPRD_QOGIRN6PRO\n'
            '\ttristate "Unisoc UMS9620/QogirN6Pro UFS controller"\n'
            '\tdepends on SCSI_UFSHCD_PLATFORM && OF && ARCH_SPRD\n'
            '\tdepends on COMMON_CLK && RESET_CONTROLLER && REGULATOR && NVMEM\n'
            '\tselect MFD_SYSCON\n'
            '\thelp\n\t  Original U30 Air UFS PHY and power sequence on mainline.\n')

append_once('drivers/misc/Makefile', 'zte-v50-sim.o',
            'obj-$(CONFIG_ZTE_V50_SIM) += zte-v50-sim.o\n')
append_once('drivers/misc/Kconfig', 'config ZTE_V50_SIM',
            '\nconfig ZTE_V50_SIM\n\ttristate "ZTE V50 physical SIM multiplexer"\n'
            '\tdepends on GPIOLIB && OF\n\thelp\n'
            '\t  V50-only sim_gpio_ctl binding; preserves bootloader routing.\n')

append_once('drivers/clk/sprd/Makefile', 'reset.o', 'clk-sprd-y\t+= reset.o\n')
append_once('drivers/usb/typec/tcpm/Makefile', 'sc27xx-ump9620.o',
            'obj-$(CONFIG_TYPEC_SC27XX_UMP9620) += sc27xx-ump9620.o\n')
append_once('drivers/usb/typec/tcpm/Kconfig', 'config TYPEC_SC27XX_UMP9620',
            '\nconfig TYPEC_SC27XX_UMP9620\n'
            '\ttristate "UMP9620 Type-C and PD controller"\n'
            '\tdepends on TYPEC_TCPM && OF && MFD_SC27XX_PMIC\n'
            '\tdepends on REGULATOR && POWER_SUPPLY && USB_ROLE_SWITCH && NVMEM\n'
            '\thelp\n\t  Autonomous Type-C and USB-PD with independent data/power roles.\n')
append_once('drivers/clk/sprd/Makefile', 'ums9620-clk.o', 'obj-$(CONFIG_SPRD_UMS9620_CLK)\t\t+= ums9620-clk.o\n')
kc = os.path.join(tree, 'drivers/clk/sprd/Kconfig')
s = open(kc).read()
if 'SPRD_UMS9620_CLK' not in s:
    i = s.rindex('endif')
    s = s[:i] + ('config SPRD_UMS9620_CLK\n\ttristate "Support for the Unisoc UMS9620 clocks"\n'
                 '\tdepends on (ARM64 && SPRD_COMMON_CLK) || COMPILE_TEST\n\tdefault ARM64\n'
                 '\thelp\n\t  Support for the global clock controller on UMS9620 devices (ported from the Unisoc 5.4 kernel).\n\n') + s[i:]
    open(kc, 'w').write(s)
append_once('drivers/pmdomain/Makefile', 'sprd/', 'obj-y\t\t\t\t\t+= sprd/\n')
kp = os.path.join(tree, 'drivers/pmdomain/Kconfig')
k = open(kp).read()
if 'SPRD_UMS9620_IPA_PD' not in k:
    i = k.rindex('endmenu')
    k = k[:i] + ('config SPRD_UMS9620_IPA_PD\n\tbool "Unisoc UMS9620 IPA subsystem power domain"\n'
                 '\tdepends on ARCH_SPRD && PM\n\tselect PM_GENERIC_DOMAINS\n\tselect MFD_SYSCON\n\tdefault y\n\n') + k[i:]
    open(kp, 'w').write(k)

# UMP9620 PMIC: MFD match data (IRQ base 0x80, 11 IRQs) and the ported regulator driver
mfd = os.path.join(tree, 'drivers/mfd/sprd-sc27xx-spi.c')
m = open(mfd).read()
def once(marker, old, new):
    """replace old by new unless marker is there already: every edit on its own, so a tree a failed run left
    half-edited is finished rather than skipped"""
    global m
    if marker not in m:
        m = m.replace(old, new, 1)
# The UMP9620's BC1.2 result (CHARGE_STATUS, Unisoc's sprd-bc1p2.h) is at 0x239c, with the SC27xx bits (DONE 11, SDP 7,
# DCP 6, CDP 5). The first version of this edit pointed it at the SC2730's 0x1b9c, where detection never finished on
# the U30 Air: a tree prepared with that is corrected.
UMP9620_DATA = ('/* UMP9620 BC1.2 status (CHARGE_STATUS) */\n#define SPRD_UMP9620_CHG_DET\t\t0x239c\n\n'
                'static const struct sprd_pmic_data ump9620_data = {\n\t.irq_base = 0x80,\n\t.num_irqs = 11,\n'
                '\t.charger_det = SPRD_UMP9620_CHG_DET,\n};\n')
m = m.replace('static const struct sprd_pmic_data ump9620_data = {\n\t.irq_base = 0x80,\n\t.num_irqs = 11,\n'
              '\t.charger_det = SPRD_SC2730_CHG_DET,\n};\n', UMP9620_DATA)
once('ump9620_data = {', 'static const struct sprd_pmic_data sc2731_data = {',
     UMP9620_DATA + '\nstatic const struct sprd_pmic_data sc2731_data = {')
if 'enum sprd_pmic_type' not in m:
    # up to 6.x: the match data is the pmic data, and the DT's sub-nodes are populated as they are
    once('"sprd,ump9620"', '\t{ .compatible = "sprd,sc2731", .data = &sc2731_data },\n',
         '\t{ .compatible = "sprd,sc2731", .data = &sc2731_data },\n\t{ .compatible = "sprd,ump9620", .data = &ump9620_data },\n')
    once('.name = "ump9620"', '\t{ .name = "sc2731", .driver_data = (unsigned long)&sc2731_data },\n',
         '\t{ .name = "sc2731", .driver_data = (unsigned long)&sc2731_data },\n\t{ .name = "ump9620", .driver_data = (unsigned long)&ump9620_data },\n')
else:
    # 7.x: the match data is a PMIC type, and each type brings a list of MFD cells. The vendor DT's sub-nodes are
    # in no list, so UMP9620 gets no cells and its sub-nodes populated as before.
    once('PMIC_TYPE_UMP9620,', '\tPMIC_TYPE_SC2731,\n', '\tPMIC_TYPE_SC2731,\n\tPMIC_TYPE_UMP9620,\n')
    once('case PMIC_TYPE_UMP9620:', '\tdefault:\n\t\tdev_err(&spi->dev, "Invalid device ID\\n");',
         '\tcase PMIC_TYPE_UMP9620:\n\t\t/* MU300: no cells; the DT\'s own sub-nodes are populated below */\n'
         '\t\tpdata = &ump9620_data;\n\t\tcells = NULL;\n\t\tnum_cells = 0;\n\t\tbreak;\n'
         '\tdefault:\n\t\tdev_err(&spi->dev, "Invalid device ID\\n");')
    once('if (pmic_type == PMIC_TYPE_UMP9620) {',
         '\t\tdev_err(&spi->dev, "Failed to populate sub-devices %d\\n", ret);\n\t\treturn ret;\n\t}\n',
         '\t\tdev_err(&spi->dev, "Failed to populate sub-devices %d\\n", ret);\n\t\treturn ret;\n\t}\n\n'
         '\tif (pmic_type == PMIC_TYPE_UMP9620) {\n\t\tret = devm_of_platform_populate(&spi->dev);\n'
         '\t\tif (ret)\n\t\t\treturn ret;\n\t}\n')
    once('"sprd,ump9620"', '\t{ .compatible = "sprd,sc2731", .data = (void *)PMIC_TYPE_SC2731 },\n',
         '\t{ .compatible = "sprd,sc2731", .data = (void *)PMIC_TYPE_SC2731 },\n'
         '\t{ .compatible = "sprd,ump9620", .data = (void *)PMIC_TYPE_UMP9620 },\n')
    once('.name = "ump9620"', '\t{ .name = "sc2731", .driver_data = PMIC_TYPE_SC2731 },\n',
         '\t{ .name = "sc2731", .driver_data = PMIC_TYPE_SC2731 },\n\t{ .name = "ump9620", .driver_data = PMIC_TYPE_UMP9620 },\n')
    once('linux/of_platform.h', '#include <linux/module.h>', '#include <linux/module.h>\n#include <linux/of_platform.h>')
open(mfd, 'w').write(m)
append_once('drivers/regulator/Makefile', 'ump9620-regulator.o', 'obj-$(CONFIG_REGULATOR_UMP9620) += ump9620-regulator.o\n')
rk = os.path.join(tree, 'drivers/regulator/Kconfig')
r = open(rk).read()
if 'REGULATOR_UMP9620' not in r:
    i = r.rindex('endif')
    r = r[:i] + ('config REGULATOR_UMP9620\n\ttristate "Unisoc UMP9620 PMIC regulators"\n\tdepends on MFD_SC27XX_PMIC || COMPILE_TEST\n'
                 '\thelp\n\t  Regulators of the UMP9620 PMIC (ported from the Unisoc 5.4 kernel).\n\n') + r[i:]
    open(rk, 'w').write(r)

append_once('drivers/usb/phy/Makefile', 'phy-sprd-ums9620-ssphy.o', 'obj-$(CONFIG_USB_SPRD_UMS9620_SSPHY) += phy-sprd-ums9620-ssphy.o\n')
pk = os.path.join(tree, 'drivers/usb/phy/Kconfig')
k = open(pk).read()
if 'USB_SPRD_UMS9620_SSPHY' not in k:
    i = k.rindex('endmenu')
    k = k[:i] + ('config USB_SPRD_UMS9620_SSPHY\n\ttristate "Unisoc UMS9620 USB 3.1 PHY"\n\tdepends on ARCH_SPRD\n\tselect USB_PHY\n\tselect MFD_SYSCON\n\n') + k[i:]
    open(pk, 'w').write(k)

# DWC3: vendor DT compatibles -> mainline of-simple glue (clocks, resets, power domain) and DWC3 core
for path, anchor, line in [
    ('drivers/usb/dwc3/dwc3-of-simple.c', '\t{ .compatible = "sprd,sc9860-dwc3" },\n', '\t{ .compatible = "sprd,qogirn6pro-dwc3" },\n'),
    ('drivers/usb/dwc3/core.c', '\t{\n\t\t.compatible = "synopsys,dwc3"\n\t},\n', '\t{\n\t\t.compatible = "snps,sprd-dwc3"\n\t},\n'),
]:
    fp = os.path.join(tree, path); t = open(fp).read()
    if line not in t:
        assert anchor in t, (path, anchor)
        t = t.replace(anchor, anchor + line, 1)
        open(fp, 'w').write(t)

append_once('drivers/watchdog/Makefile', 'ump9620-pmic-wdt.o', 'obj-$(CONFIG_MFD_SC27XX_PMIC) += ump9620-pmic-wdt.o\n')

# sdhci-sprd: this branch supports a root filesystem on the removable TF card,
# so keep both the non-removable eMMC and removable SD controller available.
# Also undo the old eMMC-only injection in an already prepared kernel tree.
sp = os.path.join(tree, 'drivers/mmc/host/sdhci-sprd.c')
t = open(sp).read()
marker = 'MU300: only the non-removable eMMC'
old = '\t/* ' + marker + ' is used */\n\tif (!of_property_read_bool(pdev->dev.of_node, "non-removable"))\n\t\treturn -ENODEV;\n'
t = t.replace(old, '')
# PR #184 maps all EIC banks so the stock card-detect GPIO uses interrupts.
# A genuine GPIO deferral still has a TF-only fallback: re-run the full parse
# without cd-gpios, rather than skipping bus modes/power sequence after it.
parse_old = '\tret = mmc_of_parse(host->mmc);\n\tif (ret)\n\t\treturn ret;\n'
parse_new = '''\tret = mmc_of_parse(host->mmc);
\tif (ret == -EPROBE_DEFER &&
\t    of_property_match_string(pdev->dev.of_node, "sprd,name", "sdio_sd") >= 0) {
\t\tstruct property *cd = of_find_property(pdev->dev.of_node, "cd-gpios", NULL);
\t\tif (cd && !of_remove_property(pdev->dev.of_node, cd)) {
\t\t\tdev_warn(&pdev->dev, "MU300: CD GPIO deferred, polling removable TF slot\\n");
\t\t\tret = mmc_of_parse(host->mmc);
\t\t\thost->mmc->caps |= MMC_CAP_NEEDS_POLL;
\t\t}
\t}
\tif (ret)
\t\treturn dev_err_probe(&pdev->dev, ret, "MU300: mmc_of_parse failed\\n");
'''
# Migrate the earlier cached adaptation, which only masked the deferral.
masked = '''\tret = mmc_of_parse(host->mmc);
\tif (ret == -EPROBE_DEFER &&
\t    of_property_read_bool(pdev->dev.of_node, "cd-gpios") &&
\t    of_property_match_string(pdev->dev.of_node, "sprd,name", "sdio_sd") >= 0) {
\t\tdev_warn(&pdev->dev, "MU300: CD GPIO deferred, polling removable TF slot\\n");
\t\thost->mmc->caps |= MMC_CAP_NEEDS_POLL;
\t\tret = 0;
\t}
\tif (ret)
\t\treturn dev_err_probe(&pdev->dev, ret, "MU300: mmc_of_parse failed\\n");
'''
t = t.replace(masked, parse_old)
if 'of_remove_property(pdev->dev.of_node, cd)' not in t:
    if parse_old not in t:
        sys.exit('port: sdhci-sprd mmc_of_parse anchor changed')
    t = t.replace(parse_old, parse_new, 1)
# UMS9620 has the r11p3 controller: the vendor driver programs DLL phase 0x2 (mainline 0x3,
# which gives data CRC errors on HS400ES writes while reads work)
t = t.replace('#define  SDHCI_SPRD_DLL_PHASE_INTERNAL\t0x3', '#define  SDHCI_SPRD_DLL_PHASE_INTERNAL\t0x2 /* MU300 r11p3 */')
open(sp, 'w').write(t)
# Shared durability policy; migrate cached keep-awake trees back to runtime PM.
from emmc import apply as apply_emmc_policy
apply_emmc_policy(tree)
# thermal: UMS9620 on-die sensors (vendor sprd_thermal_r5p0), calibration from eFuse
append_once('drivers/thermal/Makefile', 'sprd_thermal_r5p0.o', 'obj-$(CONFIG_SPRD_THERMAL_R5P0) += sprd_thermal_r5p0.o\n')
append_once('drivers/thermal/Kconfig', 'SPRD_THERMAL_R5P0', '''
config SPRD_THERMAL_R5P0
	tristate "Unisoc UMS9620 thermal sensors (r5p0)"
	depends on ARCH_SPRD || COMPILE_TEST
	depends on HAS_IOMEM && NVMEM && THERMAL_OF
''')

# sprd-efuse: add the UMS9620 (qogirn6pro) variant and make the provider strictly read-only.
# Writing blows eFuses permanently; nothing on this port needs it.
ep = os.path.join(tree, 'drivers/nvmem/sprd-efuse.c')
t = open(ep).read()
if 'qogirn6pro' not in t:
    t = t.replace('static const struct of_device_id sprd_efuse_of_match[] = {\n',
                  'static const struct sprd_efuse_variant_data qogirn6pro_data = {\n\t.blk_nums = 51,\n\t.blk_offset = 53,\n\t.blk_double = true,\n};\n\nstatic const struct of_device_id sprd_efuse_of_match[] = {\n\t{ .compatible = "sprd,qogirn6pro-efuse", .data = &qogirn6pro_data },\n', 1)
    t = t.replace('econfig.read_only = false;', 'econfig.read_only = true;\t/* MU300: never blow eFuses */')
    t = t.replace('\teconfig.reg_write = sprd_efuse_write;\n', '')
    t = t.replace('static int sprd_efuse_write(', 'static int __maybe_unused sprd_efuse_write(')
    open(ep, 'w').write(t)
assert 'reg_write' not in open(ep).read()
# cpufreq: UMS9620 apcpu DVFS is done by ATF through Unisoc SIP calls (vendor sprd_sip_svc + sprd-cpufreq-v2)
append_once('drivers/firmware/Makefile', 'sprd_sip_svc.o', 'obj-$(CONFIG_SPRD_SIP_SVC) += sprd_sip_svc.o\n')
append_once('drivers/firmware/Kconfig', 'SPRD_SIP_SVC', '''
config SPRD_SIP_SVC
	bool "Unisoc SIP services (DVFS through ATF)"
	depends on ARM64 && HAVE_ARM_SMCCC
''')
append_once('drivers/cpufreq/Makefile', 'sprd-cpufreq-v2-driver.o', 'obj-$(CONFIG_ARM_SPRD_CPUFREQ_V2) += sprd-cpufreq-v2-driver.o\n')
append_once('drivers/cpufreq/Kconfig.arm', 'ARM_SPRD_CPUFREQ_V2', '''
config ARM_SPRD_CPUFREQ_V2
	tristate "Unisoc UMS9620 cpufreq (v2, ATF DVFS)"
	depends on SPRD_SIP_SVC && NVMEM
	select PM_OPP
''')
# PCIe RC for the Marlin3 (SC2355) Wi-Fi/BT chip: vendor pcie-sprd glue ported to the 6.18 DWC host API
cp = os.path.join(tree, 'drivers/cpufreq/Kconfig.arm')
ct = open(cp).read()
ct = ct.replace('bool "Unisoc UMS9620 cpufreq (v2, ATF DVFS)"',
                'tristate "Unisoc UMS9620 cpufreq (v2, ATF DVFS)"')
open(cp, 'w').write(ct)
# The vendor driver updates scheduler frequency invariance after DVFS. Mainline
# has no export for modular cpufreq drivers; retain the call, export GPL-only.
ap = os.path.join(tree, 'drivers/base/arch_topology.c')
at = open(ap).read()
if 'EXPORT_SYMBOL_GPL(topology_set_freq_scale)' not in at:
    start = at.index('void topology_set_freq_scale(')
    end = at.index('\n}', start) + 2
    at = at[:end] + '\nEXPORT_SYMBOL_GPL(topology_set_freq_scale);' + at[end:]
    open(ap, 'w').write(at)
append_once('drivers/pci/controller/dwc/Makefile', 'pcie-sprd.o', 'obj-$(CONFIG_PCIE_SPRD) += pcie-sprd-misc.o pcie-sprd.o\n')
append_once('drivers/pci/controller/dwc/Kconfig', 'config PCIE_SPRD', '''
config PCIE_SPRD
	bool "Unisoc UMS9620 PCIe host (Marlin3)"
	depends on OF && PCI_MSI
	select PCIE_DW_HOST
	select MFD_SYSCON
''')
# PMIC RTC (refnotify on the modem side needs /dev/rtc0): same registers as SC2731
rp = os.path.join(tree, 'drivers/rtc/rtc-sc27xx.c')
t = open(rp).read()
if 'ump96xx-rtc' not in t:
    t = t.replace('\t{ .compatible = "sprd,sc2731-rtc", },\n', '\t{ .compatible = "sprd,sc2731-rtc", },\n\t{ .compatible = "sprd,ump96xx-rtc", },\n', 1)
    open(rp, 'w').write(t)
# The ZTE U30 Air's charger (SGM41511, a bq25601): its vendor DT compatible in bq256xx's OF and I2C tables; the
# driver's handling of that node (no IRQ, no input current in the DT, the chip as Android left it) is patch 0009. The
# I2C table is an edit rather than part of the patch: 6.18 writes its entries positionally, 7.x with .name/.driver_data.
bp = os.path.join(tree, 'drivers/power/supply/bq256xx_charger.c')
t = open(bp).read()
if '"ti,bq2560x_chg"' not in t:
    t = t.replace('\t{ .compatible = "ti,bq25601", .data = &bq256xx_chip_info_tbl[BQ25601] },\n',
                  '\t{ .compatible = "ti,bq25601", .data = &bq256xx_chip_info_tbl[BQ25601] },\n'
                  '\t{ .compatible = "ti,bq2560x_chg", .data = &bq256xx_chip_info_tbl[BQ25601] },\n', 1)
if '"bq2560x_chg"' not in t:
    for line in ('\t{ "bq25601", (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] },\n',
                 '\t{ .name = "bq25601", .driver_data = (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] },\n'):
        # the I2C table comes first; the ACPI table has the same positional line further down
        i = t.find(line, t.index('bq256xx_i2c_ids[]'))
        if i >= 0 and i < t.index('MODULE_DEVICE_TABLE(i2c'):
            t = t[:i + len(line)] + line.replace('"bq25601"', '"bq2560x_chg"') + t[i + len(line):]
            break
open(bp, 'w').write(t)
# Every edit above is a text substitution, and one whose anchor drifted in a new kernel release changes nothing
# without saying so. Check the result rather than trusting the substitutions.
expect = [
    ('drivers/power/supply/bq256xx_charger.c', ['\"ti,bq2560x_chg\"', '\"bq2560x_chg\"']),
    ('drivers/mfd/sprd-sc27xx-spi.c', ['SPRD_UMP9620_CHG_DET\t\t0x239c', 'ump9620_data = {', '"sprd,ump9620"', '.name = "ump9620"']
     + (['case PMIC_TYPE_UMP9620:', 'if (pmic_type == PMIC_TYPE_UMP9620) {', 'linux/of_platform.h']
        if 'enum sprd_pmic_type' in open(os.path.join(tree, 'drivers/mfd/sprd-sc27xx-spi.c')).read() else [])),
    ('drivers/mmc/host/sdhci-sprd.c', ['DLL_PHASE_INTERNAL\t0x2 /* MU300 r11p3 */',
                                      'MU300: CD GPIO deferred']),
    ('drivers/nvmem/sprd-efuse.c', ['"sprd,qogirn6pro-efuse"', 'econfig.read_only = true;']),
    ('drivers/rtc/rtc-sc27xx.c', ['"sprd,ump96xx-rtc"']),
    ('drivers/usb/dwc3/dwc3-of-simple.c', ['"sprd,qogirn6pro-dwc3"']),
    ('drivers/usb/dwc3/core.c', ['"snps,sprd-dwc3"']),
]
missing = [(p, w) for p, ws in expect for w in ws if w not in open(os.path.join(tree, p)).read()]
if missing:
    sys.exit('port: edits did not apply (anchor changed in this kernel?): ' + '; '.join(f'{p}: {w}' for p, w in missing))
print('port installed')

# UMS9620's two legacy usb-phy phandles reference the same combo PHY. Its
# role-dependent TCA state must be prepared after stopping the old link and
# before starting xHCI/gadget, not after xHCI has begun link training.
core_path = os.path.join(tree, 'drivers/usb/dwc3/core.c')
core = open(core_path).read()
header = '#include <linux/usb/ums9620-ssphy.h>\n'
if header not in core:
    anchor = '#include "core.h"\n'
    if core.count(anchor) != 1:
        sys.exit('port: unexpected DWC3 header location')
    core = core.replace(anchor, header + anchor)
resume_decls = '/* ums9620 role-resume forward declarations */'
if resume_decls in core:
    previous_decls = re.compile(re.escape(resume_decls) +
        r'\n(?:(?:static )?(?:void|int) dwc3_core_(?:exit|init|init_for_resume)'
        r'\(struct dwc3 \*dwc\);\n){2}\n')
    core, count = previous_decls.subn('', core)
    if count != 1:
        sys.exit('port: unexpected existing DWC3 lifecycle declarations')
if resume_decls not in core:
    declarations = []
    for name in ('dwc3_core_exit', 'dwc3_core_init_for_resume'):
        match = re.search(r'^((?:static )?(?:void|int) ' + name +
                          r'\(struct dwc3 \*dwc\))\n\{', core, re.M)
        if not match:
            sys.exit('port: unexpected DWC3 core lifecycle signature: ' + name)
        declarations.append(match.group(1) + ';')
    role_entry = 'static void __dwc3_set_mode(struct work_struct *work)'
    if core.count(role_entry) != 1:
        sys.exit('port: unexpected DWC3 role worker')
    core = core.replace(role_entry, resume_decls + '\n' +
                        '\n'.join(declarations) + '\n\n' + role_entry)
same_role = '\tif (desired_dr_role == dwc->current_dr_role)\n\t\tgoto out;'
if same_role in core:
    core = core.replace(same_role, '''\t/* A reattached cable can have a new orientation with the same data role. */
\tif (desired_dr_role == dwc->current_dr_role &&
\t    !(of_device_is_compatible(dwc->dev->of_node, "snps,sprd-dwc3") &&
\t      dwc->usb2_phy && dwc->usb2_phy == dwc->usb3_phy &&
\t      ums9620_ssphy_orientation_changed(dwc->usb2_phy)))
\t\tgoto out;''', 1)
marker = '/* ums9620-prepare-phy-before-role: no VBUS power-policy change. */'
anchor = '\tspin_lock_irqsave(&dwc->lock, flags);\n\n\tdwc3_set_prtcap(dwc, desired_dr_role, false);'
prepare = '''\t/* ums9620-prepare-phy-before-role: no VBUS power-policy change. */
\tif (of_device_is_compatible(dwc->dev->of_node, "snps,sprd-dwc3") &&
\t    dwc->usb2_phy && dwc->usb2_phy == dwc->usb3_phy &&
\t    desired_dr_role != DWC3_GCTL_PRTCAP_OTG) {
\t\tbool host_resume = desired_dr_role == DWC3_GCTL_PRTCAP_HOST &&
\t\t\tdwc->current_dr_role != DWC3_GCTL_PRTCAP_HOST &&
\t\t\tums9620_ssphy_host_resume_needed(dwc->usb2_phy);
\t\tbool host_resumed = false;

\t\t/* Both old controllers have exited. Match the stock glue ordering:
\t\t * host PHY intent, then full PHY/core resume, then xHCI. A PHY-only
\t\t * reset would also reset the core without restoring its registers.
\t\t */
\t\tif (host_resume && dwc->current_dr_role)
\t\t\tdwc3_core_exit(dwc);
\t\tret = otg_set_vbus(dwc->usb2_phy->otg,
\t\t\tdesired_dr_role == DWC3_GCTL_PRTCAP_HOST);
\t\tif (!ret && host_resume) {
\t\t\tret = dwc3_core_init_for_resume(dwc);
\t\t\thost_resumed = !ret;
\t\t\t/* phy_init restored controller-sync mode. Finish the mux
\t\t\t * handoff now, not in the generic callback after xHCI starts.
\t\t\t */
\t\t\tif (!ret)
\t\t\t\tret = otg_set_vbus(dwc->usb2_phy->otg, true);
\t\t}
\t\tif (ret) {
\t\t\tif (host_resumed)
\t\t\t\tdwc3_core_exit(dwc);
\t\t\tdev_err(dwc->dev, "cannot prepare combo PHY role: %d\\n", ret);
\t\t\t/* The old controller has exited; allow a later explicit retry. */
\t\t\tdwc->current_dr_role = 0;
\t\t\tgoto out;
\t\t}
\t}

'''
if marker in core:
    begin = core.index('\t' + marker)
    end = core.find(anchor, begin)
    if end < 0 or core[begin:end].count(marker) != 1:
        sys.exit('port: unexpected existing UMS9620 role preparation block')
    core = core[:begin] + prepare + core[end:]
else:
    if core.count(anchor) != 1:
        sys.exit('port: unexpected DWC3 role transition; USB3 preparation not applied')
    core = core.replace(anchor, prepare + anchor)
open(core_path, 'w').write(core)

# LEDs of the ZTE U30 Air: its white ones on PMIC LDOs (leds-zte-ldo.c), and its red on the PMIC's keypad backlight
# sink (leds-sc27xx-kpled.c)
LED_DRIVERS = (
    ('LEDS_AW9523B', 'leds-aw9523b', 'AW9523B LED groups (ZTE V50)',
     'LEDS_CLASS && I2C && OF', 'The V50/MU3351 RGB controller with its original Android DT binding.'),
    ('LEDS_ZTE_LDO', 'leds-zte-ldo', 'LEDs on PMIC LDOs (ZTE U30 Air)', 'LEDS_CLASS && REGULATOR && OF',
     "The ZTE U30 Air's white LEDs, powered by three PMIC LDOs."),
    ('LEDS_SC27XX_KPLED', 'leds-sc27xx-kpled', 'Spreadtrum/Unisoc PMIC keypad backlight',
     'LEDS_CLASS && MFD_SC27XX_PMIC && OF', "The PMIC's keypad backlight sink: the U30 Air's red network LED."),
)
kl = os.path.join(tree, 'drivers/leds/Kconfig')
kls = open(kl).read()
for sym, obj, title, deps, text in LED_DRIVERS:
    append_once('drivers/leds/Makefile', obj + '.o', f'obj-$(CONFIG_{sym})\t\t+= {obj}.o\n')
    if f'config {sym}\n' not in kls:
        i = kls.rindex('endif # NEW_LEDS')
        kls = kls[:i] + f'config {sym}\n\ttristate "{title}"\n\tdepends on {deps}\n\thelp\n\t  {text}\n\n' + kls[i:]
open(kl, 'w').write(kls)
