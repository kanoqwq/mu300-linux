# SPDX-License-Identifier: GPL-2.0
#
# for USB OTG silicon based on Mentor Graphics INVENTRA designs
#

# define_trace.h needs to know how to find our header
CFLAGS_musb_trace.o	:= -I$(src)

obj-$(CONFIG_UNISOC_USB_MUSB_HDRC) += musb_hdrc.o

musb_hdrc-y := musb_core.o musb_trace.o

musb_hdrc-$(CONFIG_UNISOC_USB_MUSB_HOST)$(CONFIG_UNISOC_USB_MUSB_DUAL_ROLE) += musb_virthub.o musb_host.o
musb_hdrc-$(CONFIG_UNISOC_USB_MUSB_GADGET)$(CONFIG_UNISOC_USB_MUSB_DUAL_ROLE) += musb_gadget_ep0.o musb_gadget.o
musb_hdrc-$(CONFIG_DEBUG_FS)			+= musb_debugfs.o

# Hardware Glue Layer

obj-$(CONFIG_UNISOC_USB_MUSB)			+= musb_sprd.o

# the kconfig must guarantee that only one of the
# possible I/O schemes will be enabled at a time ...
# PIO only, or DMA (several potential schemes).
# though PIO is always there to back up DMA, and for ep0

musb_hdrc-$(CONFIG_UNISOC_USB_DMA)		+= sprd_musbhsdma.o
