#!/usr/bin/env python3
"""Reproducible build entry; no flashing or device operations."""
from pathlib import Path
import argparse,subprocess,os,datetime,json,hashlib
ap=argparse.ArgumentParser();ap.add_argument('--profile',choices=['hardware','hardware-singlecpu','hardware-stock-mm','hardware-native-buses','hardware-native-i2c','hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag','hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag','hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem','hardware-early-ramcon','hardware-timed-reset','qemu','qemu-kunit','qemu-watchdog-test'],default='hardware');ap.add_argument('--out',required=True);ap.add_argument('--jobs',type=int,default=os.cpu_count());ap.add_argument('--reset-stage',type=int,choices=range(1,11),default=1);ap.add_argument('--configure-only',action='store_true');ap.add_argument('--extra-config',action='append',default=[],help='additional kconfig fragment (path)');ap.add_argument('--require',action='append',default=[],help='CONFIG_X=y that must survive olddefconfig');a=ap.parse_args()
PORT=Path(__file__).resolve().parent;BASE=Path(os.environ['U30PRO_WORKSPACE']).resolve();TREE=Path(os.environ['U30PRO_KERNEL_SOURCE']).resolve();OUT=Path(os.environ['U30PRO_KERNEL_OBJECTS']).resolve();assert TREE.is_relative_to(BASE) and OUT.is_relative_to(BASE) and OUT!=BASE and OUT!=TREE and a.out==OUT.name;assert 1<=a.jobs<=256
subprocess.run(['python3',str(PORT/'apply_overlay.py')],check=True)
env=dict(os.environ,ARCH='arm64',LLVM='1',KBUILD_BUILD_USER='u30pro',KBUILD_BUILD_HOST='builder',KBUILD_BUILD_VERSION='1',KBUILD_BUILD_TIMESTAMP='2026-10-17 00:00:00 +0000',SOURCE_DATE_EPOCH=str(int(datetime.datetime(2026,10,17,tzinfo=datetime.timezone.utc).timestamp())))
OUT.mkdir(exist_ok=True)
# USERLDFLAGS=-nostdlib: CONFIG_CC_CAN_LINK(_STATIC) is decided by trying to link an aarch64 program, so it would follow whether the build host
# happens to have an aarch64 libc (2026-10-25 libc6-dev-arm64-cross changed the configuration and, with IKCONFIG, the Image); no user
# programs are built, so the kernel is the same either way.
# RUSTC: Kconfig records the version of a rustc found in PATH (CONFIG_RUSTC_VERSION and friends; the build host has Rust for zwrt-datad),
# which also changes the Image through IKCONFIG; this kernel has no Rust code, so it never sees one
NORUST='RUSTC=u30pro-no-rustc'
make=['make','ARCH=arm64','LLVM=1','USERLDFLAGS=-nostdlib',NORUST,'O='+str(OUT)]
# Resolve from a clean baseline every time; do not inherit hidden options from an older profile.
CONFIG_DIR=OUT.with_name(OUT.name+'-config')
CONFIG_DIR.mkdir(exist_ok=True)
cmake=['make','ARCH=arm64','LLVM=1','USERLDFLAGS=-nostdlib',NORUST,'O='+str(CONFIG_DIR)]
subprocess.run(cmake+['allnoconfig'],cwd=TREE,env=env,check=True)
fragments=[PORT/'minimal.config']+([PORT/'qemu.config'] if a.profile in ['qemu','qemu-kunit','qemu-watchdog-test'] else [])+([PORT/'kunit.config'] if a.profile=='qemu-kunit' else [])+([PORT/'watchdog-test.config'] if a.profile=='qemu-watchdog-test' else [])
if a.profile=='hardware-singlecpu':fragments.append(PORT/'singlecpu.config')
if a.profile in ['hardware-stock-mm','hardware-native-buses','hardware-native-i2c','hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag','hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag','hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem','hardware-early-ramcon','hardware-timed-reset']:fragments.append(PORT/'stock-mm.config')
if a.profile=='hardware-native-buses':fragments.append(PORT/'native-buses.config')
if a.profile in ['hardware-native-i2c','hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag','hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag','hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:fragments.append(PORT/'native-i2c.config')
if a.profile in ['hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag']:fragments.append(PORT/'native-fwdiag.config')
if a.profile in ['hardware-native-kexec-fwdiag','hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag','hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:fragments.append(PORT/'kexec-base.config')
if a.profile in ['hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag']:fragments.append(PORT/'charger-link-diagnostic.config')
if a.profile in ['hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:fragments.append(PORT/'native-charger.config')
if a.profile in ['hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:fragments.append(PORT/'native-adc.config')
if a.profile in ['hardware-native-kexec-fgu','hardware-native-kexec-modem']:fragments.append(PORT/'native-fgu.config')
if a.profile=='hardware-native-kexec-modem':fragments.append(PORT/'native-modem.config')
fragments+=[Path(x) if Path(x).is_absolute() else PORT/x for x in a.extra_config]
if a.profile=='hardware-native-kexec-i2cdiag':fragments.append(PORT/'i2c-userspace-diagnostic.config')
if a.profile=='hardware-early-ramcon':fragments.append(PORT/'early-ramcon.config')
if a.profile=='hardware-timed-reset':
 fragment=CONFIG_DIR/'timed-reset-selected.config';fragment.write_text((PORT/'timed-reset.config').read_text().replace('CONFIG_U30PRO_TIMED_RESET_STAGE=1','CONFIG_U30PRO_TIMED_RESET_STAGE='+str(a.reset_stage)));fragments.append(fragment)
subprocess.run([str(TREE/'scripts/kconfig/merge_config.sh'),'-m','-O',str(CONFIG_DIR),str(CONFIG_DIR/'.config'),*map(str,fragments)],cwd=TREE,env=env,check=True)
subprocess.run(cmake+['olddefconfig'],cwd=TREE,env=env,check=True)
config_bytes=(CONFIG_DIR/'.config').read_bytes()
if not (OUT/'.config').exists() or (OUT/'.config').read_bytes()!=config_bytes:(OUT/'.config').write_bytes(config_bytes)
subprocess.run(make+['olddefconfig'],cwd=TREE,env=env,check=True)
required=['CONFIG_U30PRO_SDHCI_R11P3_BOOTSTRAP=y','CONFIG_EFI_PARTITION=y','CONFIG_ARCH_SPRD=y','CONFIG_SPRD_UMS9632_CLK=y','CONFIG_PINCTRL_SPRD_UMS9632=y','CONFIG_UNISOC_USB_MUSB=y','CONFIG_UNISOC_USB_HSPHY=y','CONFIG_MFD_SC27XX_PMIC=y','CONFIG_SPI_SPRD_ADI=y','CONFIG_REGULATOR_UMP9650=y','CONFIG_REGULATOR_UMP9651=y','CONFIG_GPIO_PMIC_EIC_SPRD=y','CONFIG_NVMEM_SPRD_EFUSE=y','CONFIG_NVMEM_SPRD_SHADOW_EFUSE=y','CONFIG_UNISOC_USB_SC27XX_TYPEC=y','CONFIG_SPRD_WATCHDOG=y','CONFIG_U30PRO_UMP9650_WATCHDOG=y','CONFIG_UNISOC_BC1P2=y','CONFIG_UNISOC_PD_TCPM=y','CONFIG_UNISOC_SC27XX_PD=y']
cfg=(OUT/'.config').read_text().splitlines()
cmdline='ignore_loglevel maxcpus=1' if a.profile=='hardware-singlecpu' else ('ignore_loglevel keep_bootcon' if a.profile=='hardware-early-ramcon' else 'ignore_loglevel')
if a.profile in ['hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag']:cmdline='ignore_loglevel fw_devlink=permissive'
if a.profile=='hardware-native-kexec-modem':cmdline='ignore_loglevel u30pro_cpufreq=1 u30pro_ase=1 u30pro_scufreq=1 sdhci_sprd.write_protect=0'
required += ['CONFIG_MISC_FILESYSTEMS=y','CONFIG_PSTORE=y','CONFIG_PSTORE_RAM=y','CONFIG_PSTORE_CONSOLE=y','CONFIG_CMDLINE="'+cmdline+'"','CONFIG_CMDLINE_EXTEND=y','CONFIG_ARM_PSCI_FW=y','CONFIG_ARM_GIC_V3=y','CONFIG_ARM_ARCH_TIMER=y','CONFIG_SERIAL_EARLYCON=y','CONFIG_SERIAL_SPRD=y','CONFIG_SERIAL_SPRD_CONSOLE=y']
if a.profile in ['hardware-stock-mm','hardware-native-buses','hardware-native-i2c','hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag','hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag','hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem','hardware-early-ramcon','hardware-timed-reset']:
 required += ['CONFIG_ARM64_VA_BITS_39=y','CONFIG_ARM64_VA_BITS=39','CONFIG_ARM64_PA_BITS_48=y','CONFIG_ARM64_PA_BITS=48','CONFIG_PGTABLE_LEVELS=3']
 assert 'CONFIG_ARM64_LPA2=y' not in cfg
if a.profile=='hardware-native-buses':required+=['CONFIG_I2C_SPRD=y','CONFIG_DMADEVICES=y','CONFIG_DMA_ENGINE=y','CONFIG_DMA_VIRTUAL_CHANNELS=y','CONFIG_SPRD_DMA=y']
if a.profile in ['hardware-native-i2c','hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag','hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag','hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:
 required.append('CONFIG_I2C_SPRD=y')
 assert '# CONFIG_DMADEVICES is not set' in cfg
if a.profile=='hardware-early-ramcon':required.append('CONFIG_U30PRO_EARLY_RAMCON=y')
else:assert 'CONFIG_U30PRO_EARLY_RAMCON=y' not in cfg
if a.profile=='hardware-timed-reset':required+=['CONFIG_U30PRO_TIMED_RESET=y','CONFIG_U30PRO_TIMED_RESET_STAGE='+str(a.reset_stage),'CONFIG_U30PRO_TIMED_RESET_SECONDS=41']
else:assert 'CONFIG_U30PRO_TIMED_RESET=y' not in cfg
if a.profile in ['hardware-native-kexec-fwdiag','hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag','hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:
 required+=['CONFIG_SUSPEND=y','CONFIG_PM_SLEEP_SMP=y','CONFIG_KEXEC=y','CONFIG_KEXEC_CORE=y','CONFIG_PROC_KCORE=y','CONFIG_VMCORE_INFO=y','CONFIG_U30PRO_KEXEC_BASE=y','CONFIG_WATCHDOG_NOWAYOUT=y']
 assert 'CONFIG_KVM=y' not in cfg and 'CONFIG_KEXEC_FILE=y' not in cfg and 'CONFIG_CRASH_DUMP=y' not in cfg
if a.profile in ['hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag']:required.append('CONFIG_U30PRO_CHARGER_LINK_DIAGNOSTIC=y')
else:assert 'CONFIG_U30PRO_CHARGER_LINK_DIAGNOSTIC=y' not in cfg
if a.profile=='hardware-native-kexec-i2cdiag':
 required.append('CONFIG_I2C_CHARDEV=y')
if a.profile in ['hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:
 required += ['CONFIG_CHARGER_U30PRO_SGM41513=y','CONFIG_I2C_CHARDEV=y','CONFIG_POWER_SUPPLY=y','CONFIG_REGULATOR=y']
else:assert 'CONFIG_CHARGER_U30PRO_SGM41513=y' not in cfg
if a.profile in ['hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem']:
 required += ['CONFIG_U30PRO_UMP965X_ADC=y','CONFIG_IIO=y','CONFIG_HWSPINLOCK=y','CONFIG_HWSPINLOCK_SPRD=y']
 assert 'CONFIG_SC27XX_ADC=y' not in cfg
else:assert 'CONFIG_U30PRO_UMP965X_ADC=y' not in cfg
if a.profile in ['hardware-native-kexec-fgu','hardware-native-kexec-modem']:required += ['CONFIG_BATTERY_U30PRO_UMP965X=y']
else:assert 'CONFIG_BATTERY_U30PRO_UMP965X=y' not in cfg
if a.profile=='hardware-native-kexec-modem':required += ['CONFIG_UNISOC_MODEM=y','CONFIG_UNISOC_MAILBOX=y','CONFIG_UNISOC_MAILBOX_R2=y','CONFIG_UNISOC_MODEM_POWER_MANAGER=y','CONFIG_UNISOC_MODEM_LOADER=y','CONFIG_UNISOC_SIPC=y','CONFIG_UNISOC_SIPC_SPIPE=y','CONFIG_UNISOC_SIPC_SPOOL=y','CONFIG_UNISOC_SIPC_VIRT_BUS=y','CONFIG_UNISOC_TRUSTY=y','CONFIG_UNISOC_TRUSTY_VIRTIO=y','CONFIG_UNISOC_TRUSTY_VIRTIO_IPC=y','CONFIG_UNISOC_SIPA_V3=y','CONFIG_UNISOC_SIPA_ETH=y','CONFIG_UNISOC_SIPA_DELEGATE=y']
else:assert 'CONFIG_UNISOC_MODEM=y' not in cfg
required+=a.require
for setting in required:assert setting in cfg,'Required option dropped: '+setting
assert 'CONFIG_UNISOC_PD_TCPM_FAKE=y' not in cfg and 'CONFIG_UNISOC_PD_TCPM_FAKE=m' not in cfg
if a.profile in ['qemu-kunit','qemu-watchdog-test']:assert 'CONFIG_U30PRO_SDHCI_TRIAL_KUNIT_TEST=y' in cfg
if a.profile=='qemu-kunit':assert 'CONFIG_REGMAP_MMIO_SETCLR_KUNIT_TEST=y' in cfg
elif a.profile=='qemu-watchdog-test':assert 'CONFIG_U30PRO_WATCHDOG_TEST=y' in cfg
else:assert 'CONFIG_KUNIT=y' not in cfg and 'CONFIG_KUNIT=m' not in cfg
if a.configure_only:print('configured only: '+str(OUT/'.config'));raise SystemExit(0)
subprocess.run(make+['-j'+str(a.jobs),'Image','modules'],cwd=TREE,env=env,check=True)
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
report={'version':(OUT/'include/config/kernel.release').read_text().strip(),'profile':a.profile,'diagnostic_prevents_normal_boot':a.profile=='hardware-timed-reset','diagnostic_dependency_relaxation':a.profile in ['hardware-native-i2c-fwdiag','hardware-native-kexec-fwdiag'],'reset_stage':a.reset_stage if a.profile=='hardware-timed-reset' else None,'timed_reset_selector_kind':'volatile-readonly-u32pair-ms' if a.profile=='hardware-timed-reset' else None,'config_sha256':h(OUT/'.config'),'image_sha256':h(OUT/'arch/arm64/boot/Image'),'image_bytes':(OUT/'arch/arm64/boot/Image').stat().st_size,'diagnostic_charger_link_only':a.profile in ['hardware-native-kexec-linkdiag','hardware-native-kexec-i2cdiag'],'native_SGM41513_driver':a.profile in ['hardware-native-kexec-charger','hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem'],'native_UMP965x_ADC_driver':a.profile in ['hardware-native-kexec-adc','hardware-native-kexec-fgu','hardware-native-kexec-modem'],'native_modem_drivers':a.profile=='hardware-native-kexec-modem','native_UMP965x_FGU_driver':a.profile in ['hardware-native-kexec-fgu','hardware-native-kexec-modem'],'source_overlay':{p.relative_to(PORT/'overlay').as_posix():h(p) for p in sorted((PORT/'overlay').rglob('*')) if p.is_file()},'build_identity':{k:env[k] for k in ['KBUILD_BUILD_USER','KBUILD_BUILD_HOST','KBUILD_BUILD_VERSION','KBUILD_BUILD_TIMESTAMP','SOURCE_DATE_EPOCH']},'device_boot_verified':False}
(OUT/'u30pro-build.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='source_overlay'}))
