subdir-ccflags-y := -include $(src)/kinclude/mu300_compat.h -I $(srctree) \
	-I $(src)/kinclude \
	-I $(src)/include \
	-I $(src)/boot \
	-I $(src)/platform \
	-I $(src)/pcie \
	-I $(src)/platform/rf \
	-I $(src)/platform/gnss \
	-I $(src)/sleep \
	-I $(src)/sipc

# compile WCN_PCIE or not
# MU300: out-of-tree build against the mainline kernel; Marlin3 (SC2355) on the pcie-sprd RC
BUILD_WCN_PCIE := true
CONFIG_UNISOC_WCN_BSP := m
ccflags-y += -DCONFIG_WCN_BOOT=1 -DCONFIG_WCN_SLP=1

ifeq ($(BUILD_WCN_PCIE),true)
ccflags-y += -DBUILD_WCN_PCIE
endif

ifeq ($(TARGET_BUILD_VARIANT),user)
ccflags-y += -DFLAG_WCN_USER
endif

#WCN MODULE
obj-$(CONFIG_UNISOC_WCN_BSP) += wcn_bsp.o
wcn_bsp-y += wcn_bus.o
wcn_bsp-y += sprd_wcn.o
wcn_bsp-y += mu300_stubs.o

#PLATFORM FILE
wcn_bsp-y += platform/wcn_parn_parser.o
wcn_bsp-y += platform/wcn_procfs.o
wcn_bsp-y += platform/wcn_txrx.o
#wcn_bsp-y += platform/rdc_debug.o
wcn_bsp-y += platform/wcn_log.o
wcn_bsp-y += platform/wcn_misc.o
wcn_bsp-y += platform/bufring.o
wcn_bsp-y += platform/wcn_op.o
wcn_bsp-y += platform/sysfs.o
wcn_bsp-y += platform/reset.o
wcn_bsp-y += platform/reset_test.o
wcn_bsp-y += platform/loopcheck.o
wcn_bsp-y += platform/rf/rf.o
wcn_bsp-y += platform/wcn_dump_integrate.o
wcn_bsp-y += platform/wcn_boot.o
wcn_bsp-y += platform/gnss/gnss.o
wcn_bsp-y += platform/wcn_dump.o
wcn_bsp-y += platform/gnss_dump.o
wcn_bsp-y += platform/wcn_gnss_dump.o
wcn_bsp-y += platform/wcn_swd_dap.o
wcn_bsp-y += platform/mem_pd_mgr.o
wcn_bsp-y += platform/wcn_debugfs.o
wcn_bsp-y += platform/wcn_ca_trusty.o
wcn_bsp-y += platform/wcn_pm_qos.o
wcn_bsp-y += sleep/sdio_int.o
wcn_bsp-y += sleep/slp_mgr.o
wcn_bsp-y += sleep/slp_sdio.o
wcn_bsp-y += sleep/slp_test.o

#BOOT FILE
wcn_bsp-y += boot/wcn_integrate.o
wcn_bsp-y += boot/wcn_integrate_dev.o
wcn_bsp-y += boot/wcn_integrate_boot.o
wcn_bsp-y += boot/wcn_debug_bus.o

#SIPC FILE
wcn_bsp-y += sipc/wcn_sipc.o

#PCIE FILE
ifeq ($(BUILD_WCN_PCIE),true)
#wcn_bsp-y += platform/pcie_boot.o
wcn_bsp-y += pcie/pcie.o
wcn_bsp-y += pcie/edma_engine.o
wcn_bsp-y += pcie/pcie_dbg.o
wcn_bsp-y += pcie/ioctl.o
wcn_bsp-y += pcie/mchn.o
wcn_bsp-y += pcie/edma_util.o
wcn_bsp-y += pcie/pcie_attach_bus.o
wcn_bsp-y += pcie/pcie_pm.o
endif

#SDIO FILE
wcn_bsp-y += sdio/sdiohal_main.o
wcn_bsp-y += sdio/sdiohal_common.o
wcn_bsp-y += sdio/sdiohal_tx.o
wcn_bsp-y += sdio/sdiohal_rx.o
wcn_bsp-y += sdio/sdiohal_if.o
#wcn_bsp-$(DEBUG_FS) += sdio/sdiohal_ctl.o
