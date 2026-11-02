/* SPDX-License-Identifier: GPL-2.0-or-later */

#ifndef __LINUX_USB_SPRD_PD_ADO_H
#define __LINUX_USB_SPRD_PD_ADO_H

/* ADO : Alert Data Object */
#define SPRD_USB_PD_ADO_TYPE_SHIFT			24
#define SPRD_USB_PD_ADO_TYPE_MASK			0xff
#define SPRD_USB_PD_ADO_FIXED_BATT_SHIFT		20
#define SPRD_USB_PD_ADO_FIXED_BATT_MASK			0xf
#define SPRD_USB_PD_ADO_HOT_SWAP_BATT_SHIFT		16
#define SPRD_USB_PD_ADO_HOT_SWAP_BATT_MASK		0xf

#define SPRD_USB_PD_ADO_TYPE_BATT_STATUS_CHANGE		BIT(1)
#define SPRD_USB_PD_ADO_TYPE_OCP			BIT(2)
#define SPRD_USB_PD_ADO_TYPE_OTP			BIT(3)
#define SPRD_USB_PD_ADO_TYPE_OP_COND_CHANGE		BIT(4)
#define SPRD_USB_PD_ADO_TYPE_SRC_INPUT_CHANGE		BIT(5)
#define SPRD_USB_PD_ADO_TYPE_OVP			BIT(6)

static inline unsigned int sprd_usb_pd_ado_type(u32 ado)
{
	return (ado >> SPRD_USB_PD_ADO_TYPE_SHIFT) & SPRD_USB_PD_ADO_TYPE_MASK;
}

static inline unsigned int sprd_usb_pd_ado_fixed_batt(u32 ado)
{
	return (ado >> SPRD_USB_PD_ADO_FIXED_BATT_SHIFT) & SPRD_USB_PD_ADO_FIXED_BATT_MASK;
}

static inline unsigned int sprd_usb_pd_ado_hot_swap_batt(u32 ado)
{
	return (ado >> SPRD_USB_PD_ADO_HOT_SWAP_BATT_SHIFT) & SPRD_USB_PD_ADO_HOT_SWAP_BATT_MASK;
}
#endif /* __LINUX_USB_PD_ADO_H */
