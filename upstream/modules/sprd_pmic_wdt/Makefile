# PM co-processor watchdog: without it the device power-cuts about 290 s after boot (docs/FINDINGS.md 5).
# It talks to pm_sys over SIPC, so it needs the modem stack's headers and its exported symbols.
obj-m += sprd_pmic_wdt.o
ccflags-y += -I$(src)/../sprd_modem/kinclude -I$(src)/../wcn_bsp/kinclude
