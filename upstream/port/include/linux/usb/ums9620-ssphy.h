/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_USB_UMS9620_SSPHY_H
#define _LINUX_USB_UMS9620_SSPHY_H
#include <linux/kconfig.h>
#include <linux/types.h>
struct usb_phy;
#if IS_REACHABLE(CONFIG_USB_SPRD_UMS9620_SSPHY)
bool ums9620_ssphy_orientation_changed(struct usb_phy *phy);
bool ums9620_ssphy_host_resume_needed(struct usb_phy *phy);
#else
static inline bool ums9620_ssphy_orientation_changed(struct usb_phy *phy)
{
	return false;
}
static inline bool ums9620_ssphy_host_resume_needed(struct usb_phy *phy)
{
	return false;
}
#endif
#endif
