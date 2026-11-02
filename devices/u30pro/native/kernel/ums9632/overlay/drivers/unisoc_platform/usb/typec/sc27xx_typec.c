// SPDX-License-Identifier: GPL-2.0
/*
 * Driver for Spreadtrum SC27XX USB Type-C
 *
 * Copyright (C) 2020 Unisoc Inc.
 */

#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/usb/typec.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/extcon.h>
#include <linux/kernel.h>
#include <linux/random.h>
#include <linux/nvmem-consumer.h>
#include <linux/slab.h>
#include <linux/extcon-provider.h>
#include <linux/usb/sprd_typec.h>
#include <linux/usb/sprd_tcpm.h>

/* registers definitions for controller REGS_TYPEC */
#define SC27XX_EN			0x00
#define SC27XX_MODE			0x04
#define SC27XX_INT_EN			0x0c
#define SC27XX_INT_CLR			0x10
#define SC27XX_INT_RAW			0x14
#define SC27XX_INT_MASK			0x18
#define SC27XX_STATUS			0x1c
#define SC27XX_TCCDE_CNT		0x20
#define SC27XX_TPDDE_CNT		0x24
#define SC27XX_RTRIM			0x3c
#define SC27XX_SW_CFG			0x54
#define SC27XX_TYPEC_DBG1		0x60
#define SC27XX_TYPEC_DBG2		0x64

#ifdef CONFIG_ZTE_MBB_USB_FEATURE
/* SC27XX_TYPEC_EN */
#define SC27XX_TYPEC_TOGGLE_SLEEP      BIT(1)

 
#define SC2721_TRY_SRC_ROLE                    BIT(2)
#define SC2721_TRY_SNK_ROLE                    BIT(3)

/* SC27XX_TYPEC TRY ROLE */
#define SC27XX_TRY_SRC_ROLE            BIT(7)
#define SC27XX_TRY_SNK_ROLE            BIT(8)
#define SC27XX_TRY_CONUT_MASK          GENMASK(11, 8)
#define SC27XX_TRY_CONUT(x)            (((x) << 8) & GENMASK(11, 8))

#define SC27XX_DRPTRY_CNT                      0x34
#define SC27XX_DRPTRY_MIN_VALUE                0x95f
#define SC27XX_DRPTRY_DEFAULT_VALUE            0xe0f
#endif

/* SC2730_DBG1 */
#define SC2730_CC_MASK			GENMASK(7, 0)
#define SC2730_CC_1_DETECT		BIT(0)
#define SC2730_CC_2_DETECT		BIT(4)

/* SC2721_STATUS */
#define SC2721_CC_MASK			GENMASK(6, 0)
#define SC2721_CC_1_DETECT		BIT(5)
#define SC2721_CC_2_DETECT		BIT(7)

/* SC27XX_TYPEC_EN */
#define SC27XX_TYPEC_USB20_ONLY		BIT(4)
#define SC27XX_TYPEC_TOGGLE_SLEEP	BIT(1)
#define SC27XX_TYPEC_EN			BIT(0)
#define SC27XX_ERR_STATUS_EN		BIT(2)

/* SC27XX_TYPEC MODE */
#define SC27XX_MODE_SNK			0
#define SC27XX_MODE_SRC			1
#define SC27XX_MODE_DRP			2
#define SC27XX_MODE_MASK		3

/* SC27XX_INT_EN */
#define SC27XX_ATTACH_INT_EN		BIT(0)
#define SC27XX_DETACH_INT_EN		BIT(1)
#define SC27XX_ERR_IN_INT_EN		BIT(2)
#define SC27XX_ERR_OUT_INT_EN		BIT(3)
#define SC27XX_RP_CHANGE_INT_EN		BIT(7)
#define SC27XX_LAST_ATTACH_INT_EN	BIT(13)

/* SC27XX_INT_CLR */
#define SC27XX_ATTACH_INT_CLR		BIT(0)
#define SC27XX_DETACH_INT_CLR		BIT(1)
#define SC27XX_LAST_ATTACH_INT_CLR	BIT(13)

/* SC27XX_INT_MASK */
#define SC27XX_ATTACH_INT		BIT(0)
#define SC27XX_DETACH_INT		BIT(1)
#define SC27XX_ERR_IN_INT		BIT(2)
#define SC27XX_ERR_OUT_INT		BIT(3)
#define SC27XX_RP_CHANGE_INT		BIT(7)
#define SC27XX_LAST_ATTACH_INT		BIT(13)

#define SC27XX_STATE_MASK		GENMASK(4, 0)
#define SC27XX_EVENT_MASK		(GENMASK(9, 0) | BIT(13))

#define SC27XX_DE_ATTACH_MASK		GENMASK(1, 0)

#define SC2730_EFUSE_CC1_SHIFT		5
#define SC2730_EFUSE_CC2_SHIFT		0
#define SC2721_EFUSE_CC1_SHIFT		11
#define SC2721_EFUSE_CC2_SHIFT		6
#define UMP9620_EFUSE_CC1_SHIFT		1
#define UMP9620_EFUSE_CC2_SHIFT		11
#define UMP9651_EFUSE_CC1_SHIFT		11
#define UMP9651_EFUSE_CC2_SHIFT		6
#define UMP9360_EFUSE_CC1_SHIFT		11
#define UMP9360_EFUSE_CC2_SHIFT		6

#define SC27XX_CC1_MASK(n)		GENMASK((n) + 4, (n))
#define SC27XX_CC2_MASK(n)		GENMASK((n) + 4, (n))
#define SC27XX_CC_SHIFT(n)		(n)

/* sc2721 registers definitions for controller REGS_TYPEC */
#define SC2721_EN			0x00
#define SC2721_CLR			0x04
#define SC2721_MODE			0x08

/* SC2721_INT_EN */
#define SC2721_ATTACH_INT_EN		BIT(5)
#define SC2721_DETACH_INT_EN		BIT(6)

#define SC2721_STATE_MASK		GENMASK(3, 0)
#define SC2721_EVENT_MASK		GENMASK(6, 0)

/* modify sc2730 tcc debunce */
//#define SC27XX_TCC_DEBOUNCE_CNT		0xc7f
#define SC27XX_TCC_DEBOUNCE_CNT		0xd1f    /*105ms*/
#define SC27XX_TPD_DEBOUNCE_CNT		0x1df    /*15ms*/

/* modify sc27xx tdrp */
#define SC2730_TDRP_CNT		0x48
#define SC27XX_MIN_TDRP_CNT		0x63f
#define SC27XX_MIN_TDRP_MS		50
//#define SC27XX_TDRP_RANGE		0x640
#define SC27XX_TDRP_RANGE		0x500
#define SC27XX_TDRP_TIMER		32

/* sc2730 registers definitions for controller REGS_TYPEC */
#define SC2730_TYPEC_PD_CFG		0x08
/* SC2730_TYPEC_PD_CFG */
#define SC27XX_VCONN_LDO_EN		BIT(13)
#define SC27XX_VCONN_LDO_RDY		BIT(12)
#define SC27XX_PD_NO_CHK_DETATACH_MASK	BIT(9)
#define SC27XX_PD_NO_CHK_DETATACH_SHIFT	9

/* pmic name string */
#define SC2721				0x01
#define SC2730				0x02
#define UMP9620				0x03
#define UMP9651				0x05
#define UMP9360				0x06

#define NEED_BY_PASS_VS		0x03

#define PMIC_ID_LOW			0x2000
#define PMIC_ID_HIGH			0x2004

/* CC DETECT */
#define SC2730_CC1_CHECK		BIT(5)
#define SC2721_CC1_CHECK		BIT(4)

/*CC RP RD*/
#define SC27XX_CC1_SW_SWITCH(x)		(((x) << 12) & GENMASK(13, 12))
#define SC27XX_CC1_SW_SWITCH_MASK	GENMASK(13, 12)
#define SC27XX_CC2_SW_SWITCH(x)		(((x) << 14) & GENMASK(15, 14))
#define SC27XX_CC2_SW_SWITCH_MASK	GENMASK(15, 14)


/*CC RP LEVEL*/
#define SC27XX_TYPEC_RP_LEVEL(x)	(((x) << 2) & GENMASK(3, 2))


/* Started by AICoder, pid:ja60fmaab17cc151433a085220ca1927daf7f120 */
int zte_aw35615_is_valid(void)
{
	/* add judge aw35615 id valid*/
	struct device_node *np;
	const char *cmd_line;
	int ret = 0;

	np = of_find_node_by_path("/chosen");
	if (!np) {
		pr_err("Fail to find chosen node.\n");
		return false;
	}

	ret = of_property_read_string(np, "bootargs", &cmd_line);
	if (ret < 0) {
		pr_err("Fail to find cmdline bootargs property.\n");
		of_node_put(np);
		return false;
	}

	of_node_put(np);
	if (strstr(cmd_line, "aw35615_deviceid = 0x91")) {
		pr_err("aw35615 cc logic property found.\n");
		return true;
	} else {
		pr_err("no aw35615 cc logic property found.\n");
		return false;
	}
}
EXPORT_SYMBOL(zte_aw35615_is_valid);
/* Ended by AICoder, pid:ja60fmaab17cc151433a085220ca1927daf7f120 */

/* Started by AICoder, pid:s2676k919d35b9514c510b3d206c84181406fca6 */
#ifdef ZTE_FEATURE_PV_AR
int c_to_c = 0;
int get_c_to_c(void)
{
	pr_info("sc27xx cc orientation is = %d", c_to_c);
	return c_to_c;
}
EXPORT_SYMBOL(get_c_to_c);
#endif

#define SC27XX_CC_SW_STATUS_EN		BIT(5)
#define SC27XX_CC_SW_STATUS(x)		(((x) << 0) & GENMASK(4, 0))
#define SC27XX_CC_SW_STATUS_MASK	GENMASK(4, 0)

#define SC27XX_TST_CHGDET 0x650
#define SC2721_TST_CHGDET 0x55c
#define SC27XX_TYPEC_VBUS_CL(x)		(((x) & GENMASK(7, 6)) >> 6)


enum typec_rp_level {
	RP_DISABLED = 0,
	RP_80UA,
	RP_180UA,
	RP_330UA,
};

enum typec_rp_rd_state {
	SC27XX_TYPEC_HW = 0,
	SC27XX_TYPEC_NC,
	SC27XX_TYPEC_RD,
	SC27XX_TYPEC_RP,
};

static const char *const typec_rp_rd_value[] = {
	[SC27XX_TYPEC_HW] = "hw",
	[SC27XX_TYPEC_NC] = "not connect",
	[SC27XX_TYPEC_RD] = "rd",
	[SC27XX_TYPEC_RP] = "rp",
};

static const char *const typec_rp_level_value[] = {
	[RP_DISABLED] = "disabled",
	[RP_80UA] = "80UA",
	[RP_180UA] = "180UA",
	[RP_330UA] = "330UA",
};

static const char *typec_rp_rd_string(enum typec_rp_rd_state state)
{
	if (state >= ARRAY_SIZE(typec_rp_rd_value))
		return "unknown";

	return typec_rp_rd_value[state];
}

static const char *typec_rp_level_string(enum typec_rp_level state)
{
	if (state >= ARRAY_SIZE(typec_rp_level_value))
		return "unknown";

	return typec_rp_level_value[state];
}

enum sc27xx_typec_connection_state {
	SC27XX_DETACHED_SNK,
	SC27XX_ATTACHWAIT_SNK,
	SC27XX_ATTACHED_SNK,
	SC27XX_DETACHED_SRC,
	SC27XX_ATTACHWAIT_SRC,
	SC27XX_ATTACHED_SRC,
	SC27XX_POWERED_CABLE,
	SC27XX_AUDIO_CABLE,
	SC27XX_DEBUG_CABLE,
	SC27XX_TOGGLE_SLEEP,
	SC27XX_ERR_RECOV,
	SC27XX_DISABLED,
	SC27XX_TRY_SNK,
	SC27XX_TRY_WAIT_SRC,
	SC27XX_TRY_SRC,
	SC27XX_TRY_WAIT_SNK,
	SC27XX_UNSUPOORT_ACC,
	SC27XX_ORIENTED_DEBUG,
};

struct sprd_typec_variant_data {
	u8 pmic_name;
	u32 efuse_cc1_shift;
	u32 efuse_cc2_shift;
	u32 int_en;
	u32 int_clr;
	u32 mode;
	u32 attach_en;
	u32 detach_en;
	u32 err_in_en;
	u32 err_out_en;
	u32 rp_change_en;
	u32 last_attach_en;
	u32 state_mask;
	u32 event_mask;
};

static const struct sprd_typec_variant_data sc2730_data = {
	.pmic_name = SC2730,
	.efuse_cc1_shift = SC2730_EFUSE_CC1_SHIFT,
	.efuse_cc2_shift = SC2730_EFUSE_CC2_SHIFT,
	.int_en = SC27XX_INT_EN,
	.int_clr = SC27XX_INT_CLR,
	.mode = SC27XX_MODE,
	.attach_en = SC27XX_ATTACH_INT_EN,
	.detach_en = SC27XX_DETACH_INT_EN,
	.state_mask = SC27XX_STATE_MASK,
	.event_mask = SC27XX_EVENT_MASK,
};

static const struct sprd_typec_variant_data sc2721_data = {
	.pmic_name = SC2721,
	.efuse_cc1_shift = SC2721_EFUSE_CC1_SHIFT,
	.efuse_cc2_shift = SC2721_EFUSE_CC2_SHIFT,
	.int_en = SC2721_EN,
	.int_clr = SC2721_CLR,
	.mode = SC2721_MODE,
	.attach_en = SC2721_ATTACH_INT_EN,
	.detach_en = SC2721_DETACH_INT_EN,
	.state_mask = SC2721_STATE_MASK,
	.event_mask = SC2721_EVENT_MASK,
};

static const struct sprd_typec_variant_data ump9620_data = {
	.pmic_name = UMP9620,
	.efuse_cc1_shift = UMP9620_EFUSE_CC1_SHIFT,
	.efuse_cc2_shift = UMP9620_EFUSE_CC2_SHIFT,
	.int_en = SC27XX_INT_EN,
	.int_clr = SC27XX_INT_CLR,
	.mode = SC27XX_MODE,
	.attach_en = SC27XX_ATTACH_INT_EN,
	.detach_en = SC27XX_DETACH_INT_EN,
	.state_mask = SC27XX_STATE_MASK,
	.event_mask = SC27XX_EVENT_MASK,
};

static const struct sprd_typec_variant_data ump9651_data = {
	.pmic_name = UMP9651,
	.efuse_cc1_shift = UMP9651_EFUSE_CC1_SHIFT,
	.efuse_cc2_shift = UMP9651_EFUSE_CC2_SHIFT,
	.int_en = SC27XX_INT_EN,
	.int_clr = SC27XX_INT_CLR,
	.mode = SC27XX_MODE,
	.attach_en = SC27XX_ATTACH_INT_EN,
	.detach_en = SC27XX_DETACH_INT_EN,
	.last_attach_en = SC27XX_LAST_ATTACH_INT_EN,
	.err_in_en = SC27XX_ERR_IN_INT_EN,
	.err_out_en = SC27XX_ERR_OUT_INT_EN,
	.rp_change_en = SC27XX_RP_CHANGE_INT_EN,
	.state_mask = SC27XX_STATE_MASK,
	.event_mask = SC27XX_EVENT_MASK,
};

static const struct sprd_typec_variant_data ump9360_data = {
	.pmic_name = UMP9360,
	.efuse_cc1_shift = UMP9360_EFUSE_CC1_SHIFT,
	.efuse_cc2_shift = UMP9360_EFUSE_CC2_SHIFT,
	.int_en = SC27XX_INT_EN,
	.int_clr = SC27XX_INT_CLR,
	.mode = SC27XX_MODE,
	.attach_en = SC27XX_ATTACH_INT_EN,
	.detach_en = SC27XX_DETACH_INT_EN,
	.last_attach_en = SC27XX_LAST_ATTACH_INT_EN,
	.state_mask = SC27XX_STATE_MASK,
	.event_mask = SC27XX_EVENT_MASK,
};

struct sc27xx_typec {
	struct device *dev;
	struct regmap *regmap;
	u32 base;
	int irq;
	int mode;
	#ifdef CONFIG_ZTE_MBB_USB_FEATURE
	int prefer_role;
	#endif
	struct extcon_dev *edev;
	struct extcon_dev *vbus_dev;
	struct notifier_block vbus_nb;
	struct wakeup_source *wake_lock;

	bool vbus_connect_value;
	bool vbus_only_connect;
	bool vbus_events;
	bool usb20_only;
	bool partner_connected;
	bool soft_typec_out;
	bool power_mode_cable;

	u8 dr_mode;
	u8 pr_mode;
	u8 pd_swap_evt;
	u16 tst_chgdet;
	spinlock_t lock;
	spinlock_t vbus_lock;
	enum sc27xx_typec_connection_state state;
	enum sc27xx_typec_connection_state pre_state;
	enum sprd_typec_cc_polarity cc_polarity;
	struct typec_port *port;
	struct typec_partner *partner;
	struct typec_capability typec_cap;
	const struct sprd_typec_variant_data *var_data;
	/* delayed work for handling dr swap */
	struct delayed_work drswap_work;
	/* delayed work for handling vbus_only */
	struct delayed_work vbus_only_work;
	/* delayed work for 2721 emark drp */
	struct delayed_work typec_drp_handle_work;
	struct delayed_work err_clr_work;
	bool use_pdhub_c2c;
};
#if IS_ENABLED(CONFIG_UNISOC_PD_TCPM)
struct sprd_typec_device_ops sc27xx_typec_ops;
#endif
struct sc27xx_typec *typec_sc;
/* during dr_swap, begin --> 1  end --> 0  */
static atomic_t pd_dr_swap_event;
/* interrupt, attach --> 1  detach --> 0  */
static atomic_t sc27xx_attach_state;

static int sc27xx_typec_reset_rp_rd(void);
static int sc27xx_typec_set_rp_level(enum sprd_typec_cc_status cc);
static void sc27xx_typec_powered_cable_connect_state(struct sc27xx_typec *sc);
static void sc27xx_typec_powered_cable_disconnect_state(struct sc27xx_typec *sc);
static void sc27xx_typec_powered_cable_clear_state(struct sc27xx_typec *sc);

/* set pd_dr_swap_event */
void sc27xx_set_dr_swap_executing(int event)
{
	atomic_set(&pd_dr_swap_event, event);
}

/* get pd_dr_swap_event */
int sc27xx_get_dr_swap_executing(void)
{
	return atomic_read(&pd_dr_swap_event);
}
EXPORT_SYMBOL_GPL(sc27xx_get_dr_swap_executing);

/* set sc27xx_attach_state */
void sc27xx_set_current_status_detach_or_attach(int event)
{
	atomic_set(&sc27xx_attach_state, event);
}

/* get sc27xx_attach_state */
int sc27xx_get_current_status_detach_or_attach(void)
{
	return atomic_read(&sc27xx_attach_state);
}
EXPORT_SYMBOL_GPL(sc27xx_get_current_status_detach_or_attach);

static const char * const sprd_typec_cc_polarity_roles[] = {
	[SPRD_TYPEC_POLARITY_CC1] = "cc_1",
	[SPRD_TYPEC_POLARITY_CC2] = "cc_2",
};

static void typec_set_cc_polarity_role(struct sc27xx_typec *sc,
				enum sprd_typec_cc_polarity polarity)
{
	if (sc->cc_polarity == polarity)
		return;

	sc->cc_polarity = polarity;
	sysfs_notify(&sc->dev->kobj, NULL, "typec_cc_polarity_role");
	kobject_uevent(&sc->dev->kobj, KOBJ_CHANGE);
}

void sc27xx_typec_notify_sink_ready_state(void)
{
	struct sc27xx_typec *sc = typec_sc;
	char *envp[2] = { "sink_ready_state", NULL };

	kobject_uevent_env(&sc->dev->kobj, KOBJ_CHANGE, envp);
}

static void sc27xx_typec_notify_sink_default_state(void)
{
	struct sc27xx_typec *sc = typec_sc;
	char *envp[2] = { "sink_default_state", NULL };

	kobject_uevent_env(&sc->dev->kobj, KOBJ_CHANGE, envp);
}


static int sc27xx_typec_set_cc_polarity_role(struct sc27xx_typec *sc)
{
	enum sprd_typec_cc_polarity cc_polarity;
	u32 val;
	int ret;

	ret = regmap_read(sc->regmap, sc->base + SC27XX_STATUS, &val);
	if (ret < 0) {
		dev_err(sc->dev, "failed to read STATUS register.\n");
		return ret;
	}

	if (sc->var_data->pmic_name == SC2721)
		val &= SC2721_CC1_CHECK;
	else
		val &= SC2730_CC1_CHECK;

	if (val)
		cc_polarity = SPRD_TYPEC_POLARITY_CC1;
	else
		cc_polarity = SPRD_TYPEC_POLARITY_CC2;

	typec_set_cc_polarity_role(sc, cc_polarity);

	return ret;
}

static int sc27xx_connect_set_status_use_pdhubc2c(struct sc27xx_typec *sc, u32 status)
{

	spin_lock(&sc->lock);
	if (sc->partner_connected) {
		spin_unlock(&sc->lock);
		dev_info(sc->dev, "typec has already connected!\n");
		return 0;
	}

	switch (sc->state) {
	case SC27XX_ATTACHED_SNK:
	case SC27XX_DEBUG_CABLE:
		sc->pre_state = SC27XX_ATTACHED_SNK;
		sc->pr_mode = EXTCON_SINK;
		sc->dr_mode = EXTCON_USB;
		spin_unlock(&sc->lock);

		sc27xx_set_current_status_detach_or_attach(1);
		extcon_set_state_sync(sc->edev, EXTCON_SINK, true);
		extcon_set_state_sync(sc->edev, EXTCON_USB, true);
		dev_info(sc->dev, "%s sink connect !\n", __func__);
		break;
	case SC27XX_ATTACHED_SRC:
		sc->pre_state = SC27XX_ATTACHED_SRC;
		sc->pr_mode = EXTCON_SOURCE;
		sc->dr_mode = EXTCON_USB_HOST;
		spin_unlock(&sc->lock);

		sc27xx_set_current_status_detach_or_attach(1);
		extcon_set_state_sync(sc->edev, EXTCON_SOURCE, true);
		extcon_set_state_sync(sc->edev, EXTCON_USB_HOST, true);
		dev_info(sc->dev, "%s source connect!\n", __func__);
		break;
	case SC27XX_AUDIO_CABLE:
		sc->pre_state = SC27XX_AUDIO_CABLE;
		spin_unlock(&sc->lock);

		extcon_set_state_sync(sc->edev, EXTCON_JACK_HEADPHONE, true);
		break;
	default:
		spin_unlock(&sc->lock);
		/* The pmic may report both attach and detach states, which
		 * is abnormal phenomenon.
		 */
		dev_info(sc->dev, "%s workaround error status: %d state",
				__func__, sc->state);
		return 0;
	}

	spin_lock(&sc->lock);
	sc->partner_connected = true;
	spin_unlock(&sc->lock);

	sc27xx_typec_set_cc_polarity_role(sc);

	return 0;
}

static int sc27xx_connect_set_status_no_pdhubc2c(struct sc27xx_typec *sc, u32 status)
{
	enum typec_data_role data_role = TYPEC_DEVICE;
	enum typec_role power_role = TYPEC_SOURCE;
	enum typec_role vconn_role = TYPEC_SOURCE;
	struct typec_partner_desc desc;

	if (sc->partner)
		return 0;

	switch (sc->state) {
	case SC27XX_ATTACHED_SNK:
	case SC27XX_DEBUG_CABLE:
		power_role = TYPEC_SINK;
		data_role = TYPEC_DEVICE;
		vconn_role = TYPEC_SINK;
		break;
	case SC27XX_ATTACHED_SRC:
	case SC27XX_AUDIO_CABLE:
		power_role = TYPEC_SOURCE;
		data_role = TYPEC_HOST;
		vconn_role = TYPEC_SOURCE;
		break;
	case SC27XX_POWERED_CABLE:
		/* The 2721 pmic may report powered_cable when connected emark c2c
		 * this is workaround
		 */
		sc27xx_typec_powered_cable_connect_state(sc);
		dev_info(sc->dev, "workaround power cable: %d state", sc->state);
		return 0;
	default:
		/* The pmic may report both attach and detach states, which
		 * is abnormal phenomenon.
		 */
		dev_info(sc->dev, "workaround error status: %d state", sc->state);
		return 0;
	}

	desc.usb_pd = 0;
	desc.identity = NULL;
	if (sc->state == SC27XX_AUDIO_CABLE)
		desc.accessory = TYPEC_ACCESSORY_AUDIO;
	else
		desc.accessory = TYPEC_ACCESSORY_NONE;

	sc->partner = typec_register_partner(sc->port, &desc);
	if (!sc->partner)
		return -ENODEV;

	if (sc->state != SC27XX_AUDIO_CABLE) {
		typec_set_pwr_opmode(sc->port, TYPEC_PWR_MODE_USB);
		typec_set_pwr_role(sc->port, power_role);
		typec_set_data_role(sc->port, data_role);
		typec_set_vconn_role(sc->port, vconn_role);
	}

	switch (sc->state) {
	case SC27XX_ATTACHED_SNK:
	case SC27XX_DEBUG_CABLE:
		sc->pre_state = SC27XX_ATTACHED_SNK;
		extcon_set_state_sync(sc->edev, EXTCON_USB, true);
		break;
	case SC27XX_ATTACHED_SRC:
		sc->pre_state = SC27XX_ATTACHED_SRC;
		extcon_set_state_sync(sc->edev, EXTCON_USB_HOST, true);
		break;
	case SC27XX_AUDIO_CABLE:
		sc->pre_state = SC27XX_AUDIO_CABLE;
		extcon_set_state_sync(sc->edev, EXTCON_JACK_HEADPHONE, true);
		break;
	default:
		break;
	}

	spin_lock(&sc->lock);
	sc->partner_connected = true;
	spin_unlock(&sc->lock);
	sc27xx_typec_set_cc_polarity_role(sc);

	return 0;
}


#ifdef CONFIG_ZTE_MBB_USB_FEATURE
static int sc27xx_typec_try_mode(struct sc27xx_typec *sc)
{
	int ret;
	u32 val, try_count_value, drp_try_cnt;

	if (sc->var_data->pmic_name == SC2721) {

		ret = regmap_read(sc->regmap, sc->base + sc->var_data->mode, &val);
		if (ret)
			return ret;

		val &= ~(SC2721_TRY_SRC_ROLE|SC2721_TRY_SNK_ROLE);
		switch (sc->prefer_role) {
		case TYPEC_SINK:
		val |= SC2721_TRY_SNK_ROLE;
			break;
		case TYPEC_SOURCE:
			val |= SC2721_TRY_SRC_ROLE;
			break;
		case TYPEC_NO_PREFERRED_ROLE:
		default:
			break;
		}
		ret = regmap_write(sc->regmap, sc->base + sc->var_data->mode, val);
		if (ret)
			return ret;
	} else {
		ret = regmap_read(sc->regmap, sc->base + SC27XX_EN, &val);
		if (ret)
			return ret;

		val &= ~(SC27XX_TRY_SRC_ROLE|SC27XX_TRY_SNK_ROLE);
		try_count_value = 0;
		drp_try_cnt = SC27XX_DRPTRY_DEFAULT_VALUE;
		switch (sc->prefer_role) {
		case TYPEC_SINK:
			val |= SC27XX_TRY_SNK_ROLE;
			drp_try_cnt = SC27XX_DRPTRY_MIN_VALUE;
			try_count_value = 1;
			break;
		case TYPEC_SOURCE:
			val |= SC27XX_TRY_SRC_ROLE;
			drp_try_cnt = SC27XX_DRPTRY_MIN_VALUE;
			try_count_value = 1;
			break;
		case TYPEC_NO_PREFERRED_ROLE:
		default:
			break;
		}

		pr_info("val 0x%x 0x%x 0x%x\n", val, drp_try_cnt, try_count_value);
		ret = regmap_write(sc->regmap, sc->base + SC27XX_EN, val);
		if (ret)
			return ret;

		ret = regmap_update_bits(sc->regmap, sc->base + sc->var_data->mode,
                               SC27XX_TRY_CONUT_MASK,
                               SC27XX_TRY_CONUT(try_count_value));
		if (ret)
			return ret;
	}

	return ret;
}
#endif

static int sc27xx_typec_connect(struct sc27xx_typec *sc, u32 status)
{
	int ret;

	if (sc->use_pdhub_c2c)
		ret = sc27xx_connect_set_status_use_pdhubc2c(sc, status);
	else
		ret = sc27xx_connect_set_status_no_pdhubc2c(sc, status);

	return ret;
}
static void sc27xx_disconnect_set_status_use_pdhubc2c(struct sc27xx_typec *sc)
{
	u8 pr_mode, dr_mode;

	sc27xx_typec_notify_sink_default_state();
	spin_lock(&sc->lock);
	sc->partner_connected = false;
	sc->pd_swap_evt = TYPEC_NO_SWAP;
	switch (sc->pre_state) {
	case SC27XX_ATTACHED_SNK:
	case SC27XX_DEBUG_CABLE:
	case SC27XX_ATTACHED_SRC:
		pr_mode = sc->pr_mode;
		dr_mode = sc->dr_mode;
		sc->pr_mode = EXTCON_NONE;
		sc->dr_mode = EXTCON_NONE;
		break;
	default:
		break;
	}
	spin_unlock(&sc->lock);

	switch (sc->pre_state) {
	case SC27XX_ATTACHED_SNK:
	case SC27XX_DEBUG_CABLE:
	case SC27XX_ATTACHED_SRC:
		dev_info(sc->dev, "%s dr_mode:%d  pr_mode:%d!\n",
				__func__, dr_mode, pr_mode);
		sc27xx_set_current_status_detach_or_attach(0);

		if (pr_mode == EXTCON_SOURCE)
			extcon_set_state_sync(sc->edev, EXTCON_SOURCE, false);
		else if (pr_mode == EXTCON_SINK)
			extcon_set_state_sync(sc->edev, EXTCON_SINK, false);

		if (dr_mode == EXTCON_USB_HOST)
			extcon_set_state_sync(sc->edev, EXTCON_USB_HOST, false);
		else if (dr_mode == EXTCON_USB)
			extcon_set_state_sync(sc->edev, EXTCON_USB, false);

		break;
	case SC27XX_AUDIO_CABLE:
		extcon_set_state_sync(sc->edev, EXTCON_JACK_HEADPHONE, false);
		break;
	default:
		break;
	}
	sc->pre_state = SC27XX_DETACHED_SNK;

	/*default rp level*/
	sc27xx_typec_set_rp_level(SPRD_TYPEC_CC_RP_DEF);

	/*reset rp rd*/
	sc27xx_typec_reset_rp_rd();

}

static void sc27xx_disconnect_set_status_no_pdhubc2c(struct sc27xx_typec *sc)
{
	typec_unregister_partner(sc->partner);
	sc->partner = NULL;
	typec_set_pwr_opmode(sc->port, TYPEC_PWR_MODE_USB);
	typec_set_pwr_role(sc->port, TYPEC_SINK);
	typec_set_data_role(sc->port, TYPEC_DEVICE);
	typec_set_vconn_role(sc->port, TYPEC_SINK);

	sc27xx_typec_powered_cable_disconnect_state(sc);
	spin_lock(&sc->lock);
	sc->partner_connected = false;
	spin_unlock(&sc->lock);

	switch (sc->pre_state) {
	case SC27XX_ATTACHED_SNK:
	case SC27XX_DEBUG_CABLE:
		extcon_set_state_sync(sc->edev, EXTCON_USB, false);
		break;
	case SC27XX_ATTACHED_SRC:
		extcon_set_state_sync(sc->edev, EXTCON_USB_HOST, false);
		break;
	case SC27XX_AUDIO_CABLE:
		extcon_set_state_sync(sc->edev, EXTCON_JACK_HEADPHONE, false);
		break;
	default:
		break;
	}
	sc->pre_state = SC27XX_DETACHED_SNK;
}

static void sc27xx_typec_disconnect(struct sc27xx_typec *sc, u32 status)
{
	#ifdef ZTE_FEATURE_PV_AR
	c_to_c = 0;
	#endif
	#ifdef CONFIG_ZTE_MBB_USB_FEATURE
	/*try sink*/
	sc27xx_typec_try_mode(sc);
	#endif
	if (sc->use_pdhub_c2c)
		sc27xx_disconnect_set_status_use_pdhubc2c(sc);
	else
		sc27xx_disconnect_set_status_no_pdhubc2c(sc);
}

static int sc27xx_typec_random_tdrp(struct sc27xx_typec *sc)
{
	int ret = 0;
	u32 rand;

	/* workaround: the two devices are connected through CC cables and restarted
	 * at the same time. there is no interruption in insertion and removal. Set
	 * the toggle period 50~90 ms of two mobile phones at random.
	 */
	get_random_bytes(&rand, 4);
	rand &= 0xfff;
	rand = SC27XX_MIN_TDRP_CNT + rand % SC27XX_TDRP_RANGE;

	if (sc->var_data->pmic_name != SC2721) {
		ret = regmap_write(sc->regmap, sc->base + SC2730_TDRP_CNT, rand);
		dev_info(sc->dev, "SC27XX_MIN_TERR_CNT = %d: %d ms\n", rand,
				(rand - SC27XX_MIN_TDRP_CNT) / SC27XX_TDRP_TIMER +
				SC27XX_MIN_TDRP_MS);
	}

	return ret;
}

void sc27xx_typec_err_recover_state_en(void)
{
	struct sc27xx_typec *sc = typec_sc;
	int ret;

	dev_info(sc->dev, "%s enter line %d\n", __func__, __LINE__);

	ret = regmap_update_bits(sc->regmap, sc->base + SC27XX_EN,
			SC27XX_ERR_STATUS_EN,
			SC27XX_ERR_STATUS_EN);

	queue_delayed_work(system_unbound_wq, &sc->err_clr_work, msecs_to_jiffies(50));

}

static void sc27xx_typec_err_recover_state_disen(void)
{
	struct sc27xx_typec *sc = typec_sc;
	int ret;

	dev_info(sc->dev, "%s enter line %d\n", __func__, __LINE__);

	ret = regmap_update_bits(sc->regmap, sc->base + SC27XX_EN,
			SC27XX_ERR_STATUS_EN,
			0);
}

static void sc27xx_typec_err_clr_work(struct work_struct *work)
{
	sc27xx_typec_err_recover_state_disen();
}

static irqreturn_t sc27xx_typec_interrupt(int irq, void *data)
{
	struct sc27xx_typec *sc = data;
	u32 event, rp_status;
	int ret;
	#ifdef ZTE_FEATURE_PV_AR
	u32 val;
	#endif
	dev_info(sc->dev, "%s enter line %d\n", __func__, __LINE__);
	ret = regmap_read(sc->regmap, sc->base + SC27XX_INT_MASK, &event);
	if (ret)
		return ret;

	event &= sc->var_data->event_mask;

	ret = regmap_read(sc->regmap, sc->base + SC27XX_STATUS, &sc->state);
	if (ret)
		goto clear_ints;
#ifdef ZTE_FEATURE_PV_AR
	ret = regmap_read(sc->regmap, sc->base + SC27XX_STATUS, &val);
	if (ret < 0) {
		dev_err(sc->dev, "failed to read STATUS register.\n");
		return ret;
	}

	if (sc->var_data->pmic_name == SC2721)
		val &= SC2721_CC1_CHECK;
	else
		val &= SC2730_CC1_CHECK;

	if (val)
		c_to_c = 1;
	else
		c_to_c = 2;
	pr_info("%s:  c_to_c = %d\n", __func__, c_to_c);
#endif

	rp_status = sc->state;
	sc->state &= sc->var_data->state_mask;

	if (sc->var_data->pmic_name != SC2721) {
		if (event & SC27XX_ERR_IN_INT) {
			sc27xx_typec_err_recover_state_disen();
			spin_lock(&sc->lock);
			if (sc->partner_connected) {
				spin_unlock(&sc->lock);
				sc27xx_typec_disconnect(sc, sc->state);
			} else {
				spin_unlock(&sc->lock);
			}
		}

		if (event & SC27XX_RP_CHANGE_INT) {
			dev_info(sc->dev, "rp change %lu\n", SC27XX_TYPEC_VBUS_CL(rp_status));
			extcon_set_state_sync(sc->edev, EXTCON_RP_VALUE_CHANGE, true);
			extcon_set_state(sc->edev, EXTCON_RP_VALUE_CHANGE, false);
		}
	}

	if (sc->var_data->pmic_name == UMP9651 || sc->var_data->pmic_name == UMP9360) {
		/**
		 * UMP9651 adds a new bit SC27XX_LAST_ATTACH_INT,
		 * last_int=0 : detach int is last
		 * last_int=1 : attach int is last
		 */
		if ((event & SC27XX_LAST_ATTACH_INT) && (event & SC27XX_ATTACH_INT)) {
			ret = sc27xx_typec_connect(sc, sc->state);
			if (ret)
				dev_warn(sc->dev, "failed to register partner\n");
		} else if (!(event & SC27XX_LAST_ATTACH_INT) && (event & SC27XX_DETACH_INT)) {
			sc27xx_typec_disconnect(sc, sc->state);
			ret = sc27xx_typec_random_tdrp(sc);
			if (ret)
				dev_warn(sc->dev, "failed to random tdrp\n");
		}
	} else {
		if (event & SC27XX_ATTACH_INT) {
			ret = sc27xx_typec_connect(sc, sc->state);
			if (ret)
				dev_warn(sc->dev, "failed to register partner\n");
		} else if (event & SC27XX_DETACH_INT) {
			sc27xx_typec_disconnect(sc, sc->state);
			ret = sc27xx_typec_random_tdrp(sc);
			if (ret)
				dev_warn(sc->dev, "failed to random tdrp\n");
		}
	}

clear_ints:
	regmap_write(sc->regmap, sc->base + sc->var_data->int_clr, event);

	dev_info(sc->dev, "now works as DRP and is in %d state, event %d\n",
		sc->state, event);
	return IRQ_HANDLED;
}

static void sc27xx_typec_en_interface(struct sc27xx_typec *sc, bool en)
{
	dev_info(sc->dev, "%s enter en : %d\n", __func__, en);

	if (sc->var_data->pmic_name != SC2721)
		return;

	if (en)
		regmap_update_bits(sc->regmap, sc->base + SC27XX_EN,
				SC27XX_TYPEC_EN,
				SC27XX_TYPEC_EN);
	else
		regmap_update_bits(sc->regmap, sc->base + SC27XX_EN,
				SC27XX_TYPEC_EN,
				0);
}

static void sc27xx_typec_set_mode(struct sc27xx_typec *sc, u32 mode)
{
	dev_info(sc->dev, "%s enter mode : %d\n", __func__, mode);
	if (sc->var_data->pmic_name != SC2721)
		return;

	regmap_update_bits(sc->regmap, sc->base + sc->var_data->mode,
			  SC27XX_MODE_MASK,
			  mode);
}

static void sc27xx_typec_powered_cable_connect_state(struct sc27xx_typec *sc)
{
	dev_info(sc->dev, "powered_cable_connect_state enter");
	if (sc->var_data->pmic_name != SC2721)
		return;

	spin_lock(&sc->lock);
	sc->power_mode_cable = true;
	spin_unlock(&sc->lock);

	sc27xx_typec_en_interface(sc, false);
	sc27xx_typec_set_mode(sc, SC27XX_MODE_SNK);
	sc27xx_typec_en_interface(sc, true);
	/* outtime need to set drp */
	queue_delayed_work(system_unbound_wq, &sc->typec_drp_handle_work, msecs_to_jiffies(3000));
}

static void sc27xx_typec_powered_cable_disconnect_state(struct sc27xx_typec *sc)
{
	bool cable_value = false;

	dev_info(sc->dev, "powered_cable_disconnect_state enter");
	if (sc->var_data->pmic_name != SC2721)
		return;

	spin_lock(&sc->lock);
	cable_value = sc->power_mode_cable;
	sc->power_mode_cable = false;
	spin_unlock(&sc->lock);

	if (!cable_value)
		sc27xx_typec_set_mode(sc, SC27XX_MODE_DRP);
}

static void sc27xx_typec_powered_cable_clear_state(struct sc27xx_typec *sc)
{
	dev_info(sc->dev, "powered_cable_clear_state enter");

	spin_lock(&sc->lock);
	sc->power_mode_cable = false;
	spin_unlock(&sc->lock);
	sc27xx_typec_set_mode(sc, SC27XX_MODE_DRP);
}

static void sc27xx_typec_drp_handle_work(struct work_struct *work)
{
	struct sc27xx_typec *sc = typec_sc;

	dev_info(sc->dev, "drp_handle_work enter\n");
	if (sc->var_data->pmic_name != SC2721)
		return;

	/* vbus is on don't set drp */
	if (extcon_get_state(sc->vbus_dev, EXTCON_USB) == true)
		return;

	sc27xx_typec_powered_cable_clear_state(sc);
}

static int sc27xx_vbus_ok_bypass(struct sc27xx_typec *sc)
{
	int ret;
	u32 val;

	if (sc->var_data->pmic_name  > NEED_BY_PASS_VS)
		return 0;

	dev_info(sc->dev, "%s %d", __func__, __LINE__);
	/*set clock acceleration */
	ret = regmap_read(sc->regmap, sc->tst_chgdet, &val);
	if (ret)
		return ret;

	val |= 0x1;
	ret = regmap_write(sc->regmap, sc->tst_chgdet, val);

	return ret;
}

static int sc27xx_vbus_ok_bypass_dis(struct sc27xx_typec *sc)
{
	int ret;
	u32 val;

	if (sc->var_data->pmic_name  > NEED_BY_PASS_VS)
		return 0;

	dev_info(sc->dev, "%s %d", __func__, __LINE__);
	/* clear clock acceleration */
	ret = regmap_read(sc->regmap, sc->tst_chgdet, &val);
	if (ret)
		return ret;

	val &= ~0x1;
	ret = regmap_write(sc->regmap, sc->tst_chgdet, val);

	return ret;
}

static int sc27xx_typec_enable(struct sc27xx_typec *sc)
{
	int ret;
	u32 val;

	/* Set typec mode */
	ret = regmap_read(sc->regmap, sc->base + sc->var_data->mode, &val);
	if (ret)
		return ret;

	val &= ~SC27XX_MODE_MASK;
	switch (sc->mode) {
	case TYPEC_PORT_DFP:
		val |= SC27XX_MODE_SRC;
		break;
	case TYPEC_PORT_UFP:
		val |= SC27XX_MODE_SNK;
		break;
	case TYPEC_PORT_DRP:
		/*
		 * U30 Pro: the port starts sink only, so a dual role host (a USB4 PC port) can never make the router
		 * power it.  User space opts in to dual role through /sys/class/typec/port0/port_type.
		 */
		val |= SC27XX_MODE_SNK;
		break;
	default:
		return -EINVAL;
	}

	ret = regmap_write(sc->regmap, sc->base + sc->var_data->mode, val);
	if (ret)
		return ret;

	/* typec USB20 only flag, only work in snk mode */
	if (!sc->use_pdhub_c2c) {
		if (sc->typec_cap.data == TYPEC_PORT_UFP && sc->usb20_only) {
			ret = regmap_update_bits(sc->regmap, sc->base + SC27XX_EN,
						SC27XX_TYPEC_USB20_ONLY,
						SC27XX_TYPEC_USB20_ONLY);
			if (ret)
				return ret;
		}
	}
	#ifdef CONFIG_ZTE_MBB_USB_FEATURE
	ret = sc27xx_typec_try_mode(sc);
	if (ret)
		return ret;
	#endif
	/* modify sc2730 tcc debounce to 100ms while PD signal occur at 150ms
	 * and effect tccde reginize.Reason is hardware signal and clk not
	 * accurate.
	 */
	if (sc->var_data->pmic_name != SC2730) {
		ret = regmap_write(sc->regmap, sc->base + SC27XX_TCCDE_CNT,
				SC27XX_TCC_DEBOUNCE_CNT);
		if (ret)
			return ret;
	}

	ret = regmap_update_bits(sc->regmap, sc->base + SC27XX_EN,
		SC27XX_TYPEC_TOGGLE_SLEEP, 0);
	if (ret)
		return ret;

	if (sc->var_data->pmic_name == SC2721)
		sc->tst_chgdet = SC2721_TST_CHGDET;
	else
		sc->tst_chgdet = SC27XX_TST_CHGDET;

	/* Enable typec interrupt and enable typec */
	ret = regmap_read(sc->regmap, sc->base + sc->var_data->int_en, &val);
	if (ret)
		return ret;

	val |= sc->var_data->attach_en | sc->var_data->detach_en | sc->var_data->last_attach_en;

	if (sc->var_data->pmic_name != SC2721)
		val |=  sc->var_data->err_in_en | sc->var_data->err_out_en |
			sc->var_data->rp_change_en;


	return regmap_write(sc->regmap, sc->base + sc->var_data->int_en, val);
}

static const u32 sc27xx_typec_cable[] = {
	EXTCON_USB,
	EXTCON_USB_HOST,
	EXTCON_SOURCE,
	EXTCON_SINK,
	EXTCON_RP_VALUE_CHANGE,
	EXTCON_JACK_HEADPHONE,
	EXTCON_NONE,
};

static int sc27xx_typec_get_cc1_efuse(struct sc27xx_typec *sc)
{
	struct nvmem_cell *cell;
	u32 calib_data = 0;
	void *buf;
	size_t len;

	cell = nvmem_cell_get(sc->dev, "typec_cc1_cal");
	if (IS_ERR(cell))
		return PTR_ERR(cell);

	buf = nvmem_cell_read(cell, &len);
	nvmem_cell_put(cell);

	if (IS_ERR(buf))
		return PTR_ERR(buf);

	memcpy(&calib_data, buf, min(len, sizeof(u32)));
	calib_data = (calib_data & SC27XX_CC1_MASK(sc->var_data->efuse_cc1_shift))
			>> SC27XX_CC_SHIFT(sc->var_data->efuse_cc1_shift);
	kfree(buf);

	return calib_data;
}

static int sc27xx_typec_get_cc2_efuse(struct sc27xx_typec *sc)
{
	struct nvmem_cell *cell;
	u32 calib_data = 0;
	void *buf;
	size_t len = 0;

	cell = nvmem_cell_get(sc->dev, "typec_cc2_cal");
	if (IS_ERR(cell))
		return PTR_ERR(cell);

	buf = nvmem_cell_read(cell, &len);
	nvmem_cell_put(cell);

	if (IS_ERR(buf))
		return PTR_ERR(buf);

	memcpy(&calib_data, buf, min(len, sizeof(u32)));
	calib_data = (calib_data & SC27XX_CC2_MASK(sc->var_data->efuse_cc2_shift))
			>> SC27XX_CC_SHIFT(sc->var_data->efuse_cc2_shift);
	kfree(buf);

	return calib_data;
}

static int typec_set_rtrim(struct sc27xx_typec *sc)
{
	int calib_cc1;
	int calib_cc2;
	u32 rtrim;

	calib_cc1 = sc27xx_typec_get_cc1_efuse(sc);
	if (calib_cc1 < 0)
		return calib_cc1;
	calib_cc2 = sc27xx_typec_get_cc2_efuse(sc);
	if (calib_cc2 < 0)
		return calib_cc2;

	rtrim = calib_cc1 | calib_cc2<<5;

	return regmap_write(sc->regmap, sc->base + SC27XX_RTRIM, rtrim);
}

static ssize_t
typec_cc_polarity_role_show(struct device *dev, struct device_attribute *attr,
		char *buf)
{
	struct sc27xx_typec *sc = dev_get_drvdata(dev);

	return snprintf(buf, 5, "%s\n", sprd_typec_cc_polarity_roles[sc->cc_polarity]);
}
static DEVICE_ATTR_RO(typec_cc_polarity_role);

static struct attribute *sc27xx_typec_attrs[] = {
	&dev_attr_typec_cc_polarity_role.attr,
	NULL,
};
ATTRIBUTE_GROUPS(sc27xx_typec);

int sc27xx_typec_pr_swap_no_chk_detach(bool on)
{
	struct sc27xx_typec *sc = typec_sc;
	int ret = 0;

	dev_info(sc->dev, "%s, on = %d\n", __func__, on);
	ret = regmap_update_bits(sc->regmap,
				sc->base + SC2730_TYPEC_PD_CFG,
				SC27XX_PD_NO_CHK_DETATACH_MASK,
				on << SC27XX_PD_NO_CHK_DETATACH_SHIFT);

	if (ret)
		dev_err(sc->dev, "%s, faile to set TYPEC_PD_CFG[0x%2x], ret=%d\n",
			__func__, SC2730_TYPEC_PD_CFG, ret);

	return ret;
}

/*
 * if pd pr/dr swap ok,send extcon event by set api.
 */
int sc27xx_typec_set_pd_swap_event(u8 pd_swap_flag)
{
	struct sc27xx_typec *sc = typec_sc;

	dev_info(sc->dev, "%s pd_swap_flag:%d!\n", __func__, pd_swap_flag);

	spin_lock(&sc->lock);
	if (!sc->partner_connected) {
		spin_unlock(&sc->lock);
		dev_info(sc->dev, "typec has disconnected!\n");
		return 0;
	}

	switch (pd_swap_flag) {
	case TYPEC_SOURCE_TO_SINK:
		sc->pr_mode = EXTCON_SINK;
		spin_unlock(&sc->lock);
		extcon_set_state_sync(sc->edev, EXTCON_SOURCE, false);
		extcon_set_state_sync(sc->edev, EXTCON_SINK, true);
		break;
	case TYPEC_SINK_TO_SOURCE:
		sc->pr_mode = EXTCON_SOURCE;
		spin_unlock(&sc->lock);
		extcon_set_state_sync(sc->edev, EXTCON_SINK, false);
		extcon_set_state_sync(sc->edev, EXTCON_SOURCE, true);
		break;
	case TYPEC_HOST_TO_DEVICE:
	case TYPEC_DEVICE_TO_HOST:
		if (pd_swap_flag != sc->pd_swap_evt) {
			sc->pd_swap_evt = pd_swap_flag;
			spin_unlock(&sc->lock);
			/* delay one jiffies for scheduling purpose */
			schedule_delayed_work(&sc->drswap_work, msecs_to_jiffies(5));
		} else {
			spin_unlock(&sc->lock);
			dev_err(sc->dev, "%s igore pd_swap_flag:%d!\n",
						__func__, pd_swap_flag);
		}
		break;
	default:
		spin_unlock(&sc->lock);
		return -EINVAL;
	}

	return 0;
}

static int sc27xx_typec_set_rp_level_value(enum typec_rp_level value)
{
	struct sc27xx_typec *sc = typec_sc;
	int ret;

	dev_info(sc->dev, "rp level: %s", typec_rp_level_string(value));
	ret =  regmap_update_bits(sc->regmap, sc->base + SC27XX_MODE,
				SC27XX_TYPEC_RP_LEVEL(3), SC27XX_TYPEC_RP_LEVEL(value));
	if (ret < 0) {
		dev_err(sc->dev, "failed to set typec rp level, ret = %d\n", ret);
		return ret;
	}

	return 0;
}

static int sc27xx_typec_set_rp_rd_value(enum typec_rp_rd_state value)
{
	struct sc27xx_typec *sc = typec_sc;
	int ret;

	dev_info(sc->dev, "set %s %s",
				sprd_typec_cc_polarity_roles[sc->cc_polarity],
				typec_rp_rd_string(value));
	if (sc->cc_polarity == SPRD_TYPEC_POLARITY_CC1) {
		ret = regmap_update_bits(sc->regmap,
					sc->base + SC27XX_SW_CFG,
					SC27XX_CC1_SW_SWITCH_MASK,
					SC27XX_CC1_SW_SWITCH(value));
	} else {
		ret = regmap_update_bits(sc->regmap,
					sc->base + SC27XX_SW_CFG,
					SC27XX_CC2_SW_SWITCH_MASK,
					SC27XX_CC2_SW_SWITCH(value));
	}

	if (ret < 0) {
		dev_err(sc->dev, "failed to set typec rp rd, ret = %d\n", ret);
		return ret;
	}

	return 0;
}

static int sc27xx_typec_set_rp_level(enum sprd_typec_cc_status cc)
{
	struct sc27xx_typec *sc = typec_sc;
	int ret;

	dev_info(sc->dev, "%s cc:%d!\n", __func__, cc);

	switch (cc) {
	case SPRD_TYPEC_CC_RP_DEF:
		ret = sc27xx_typec_set_rp_level_value(RP_330UA);
		break;
	case SPRD_TYPEC_CC_RP_1_5:
		ret = sc27xx_typec_set_rp_level_value(RP_180UA);
		break;
	case SPRD_TYPEC_CC_RP_3_0:
		ret = sc27xx_typec_set_rp_level_value(RP_330UA);
		break;
	default:
		/*rp level default */
		ret = sc27xx_typec_set_rp_level_value(RP_330UA);
		break;
	}

	if (ret < 0)
		return ret;

	return 0;
}

static int sc27xx_typec_reset_rp_rd(void)
{
	struct sc27xx_typec *sc = typec_sc;
	int ret;

	ret = regmap_update_bits(sc->regmap,
				sc->base + SC27XX_SW_CFG,
				SC27XX_CC1_SW_SWITCH_MASK,
				SC27XX_CC1_SW_SWITCH(SC27XX_TYPEC_NC));
	ret = regmap_update_bits(sc->regmap,
				sc->base + SC27XX_SW_CFG,
				SC27XX_CC2_SW_SWITCH_MASK,
				SC27XX_CC2_SW_SWITCH(SC27XX_TYPEC_NC));

	msleep(20);

	ret = regmap_update_bits(sc->regmap,
				sc->base + SC27XX_SW_CFG,
				SC27XX_CC1_SW_SWITCH_MASK,
				SC27XX_CC1_SW_SWITCH(SC27XX_TYPEC_HW));
	ret = regmap_update_bits(sc->regmap,
				sc->base + SC27XX_SW_CFG,
				SC27XX_CC2_SW_SWITCH_MASK,
				SC27XX_CC2_SW_SWITCH(SC27XX_TYPEC_HW));
	return 0;
}

int sc27xx_typec_set_rp_rd(enum sprd_typec_cc_status cc)
{
	int ret;

	switch (cc) {
	case SPRD_TYPEC_CC_RD:
		/* force cc connected by rd */
		ret = sc27xx_typec_set_rp_rd_value(SC27XX_TYPEC_RD);
		break;
	case SPRD_TYPEC_CC_RP_DEF:
	case SPRD_TYPEC_CC_RP_1_5:
	case SPRD_TYPEC_CC_RP_3_0:
		/* force cc connected by rp */
		ret = sc27xx_typec_set_rp_rd_value(SC27XX_TYPEC_RP);
		break;
	case SPRD_TYPEC_CC_OPEN:
	default:
		/* cc_switch is controlled by HW */
		ret = sc27xx_typec_set_rp_rd_value(SC27XX_TYPEC_HW);
		break;
	}

	if (ret < 0)
		return ret;

	return 0;
}

#if IS_ENABLED(CONFIG_UNISOC_PD_TCPM)
static int sc27xx_typec_set_port_type(enum typec_port_type type)
{
	struct sc27xx_typec *sc = typec_sc;
	u32 mode;

	if (!sc)
		return -ENODEV;
	if (type == TYPEC_PORT_DRP && sc->mode != TYPEC_PORT_DRP)
		return -EINVAL;

	switch (type) {
	case TYPEC_PORT_SRC:
		mode = SC27XX_MODE_SRC;
		break;
	case TYPEC_PORT_SNK:
		mode = SC27XX_MODE_SNK;
		break;
	default:
		mode = SC27XX_MODE_DRP;
		break;
	}
	dev_info(sc->dev, "port type %d, hw mode %u\n", type, mode);
	return regmap_update_bits(sc->regmap, sc->base + sc->var_data->mode, SC27XX_MODE_MASK, mode);
}

static int sc27xx_typec_register_ops(void)
{
	if (!typec_sc->use_pdhub_c2c)
		return 0;

	sc27xx_typec_ops.name = "sc27xx-typec";
	sc27xx_typec_ops.typec_set_pd_swap_event = sc27xx_typec_set_pd_swap_event;
	sc27xx_typec_ops.set_typec_rp_level = sc27xx_typec_set_rp_level;
	sc27xx_typec_ops.set_typec_rp_rd = sc27xx_typec_set_rp_rd;
	sc27xx_typec_ops.typec_pr_swap_no_chk_detach = sc27xx_typec_pr_swap_no_chk_detach;
	sc27xx_typec_ops.set_typec_err_recovery_enter = sc27xx_typec_err_recover_state_en;
	sc27xx_typec_ops.typec_notify_sink_ready_state  = sc27xx_typec_notify_sink_ready_state;
	sc27xx_typec_ops.set_typec_port_type = sc27xx_typec_set_port_type;

	sprd_tcpm_typec_device_ops_register(&sc27xx_typec_ops);

	return 0;
}
#else
static int sc27xx_typec_register_ops(void)
{
	return 0;
}
#endif

static void sc27xx_typec_extcon_drswap_work(struct work_struct *work)
{
	struct sc27xx_typec *sc = typec_sc;

	dev_info(sc->dev, "%s pd_swap_evt:%d!\n", __func__, sc->pd_swap_evt);

	spin_lock(&sc->lock);
	switch (sc->pd_swap_evt) {
	case TYPEC_HOST_TO_DEVICE:
		sc->dr_mode = EXTCON_USB;
		spin_unlock(&sc->lock);

		sc27xx_set_dr_swap_executing(1);
		extcon_set_state_sync(sc->edev, EXTCON_USB_HOST, false);
		if (sc27xx_get_current_status_detach_or_attach())
			extcon_set_state_sync(sc->edev, EXTCON_USB, true);
		sc27xx_set_dr_swap_executing(0);
		break;
	case TYPEC_DEVICE_TO_HOST:
		sc->dr_mode = EXTCON_USB_HOST;
		spin_unlock(&sc->lock);

		sc27xx_set_dr_swap_executing(1);
		extcon_set_state_sync(sc->edev, EXTCON_USB, false);
		if (sc27xx_get_current_status_detach_or_attach())
			extcon_set_state_sync(sc->edev, EXTCON_USB_HOST, true);
		sc27xx_set_dr_swap_executing(0);
		break;
	default:
		spin_unlock(&sc->lock);
		return;
	}
}

static int sc27xx_typec_get_vbus_notify(struct notifier_block *nb,
				unsigned long event, void *data)
{
	struct sc27xx_typec *sc = container_of(nb,
				  struct sc27xx_typec, vbus_nb);
	int ret;

	spin_lock(&sc->vbus_lock);
	sc->vbus_events = true;
	sc->vbus_connect_value = event;
	spin_unlock(&sc->vbus_lock);

	dev_info(sc->dev, "vbus_connect_value:%d\n", sc->vbus_connect_value);

	spin_lock(&sc->lock);
	if (sc->partner_connected) {
		spin_unlock(&sc->lock);
		return 0;
	}
	spin_unlock(&sc->lock);

	if (sc->vbus_connect_value)
		ret = sc27xx_vbus_ok_bypass_dis(sc);
	else
		ret = sc27xx_vbus_ok_bypass(sc);

	if (!event && sc->var_data->pmic_name == SC2721) {
		/*vbus off need to set drp*/
		cancel_delayed_work_sync(&sc->typec_drp_handle_work);
		sc27xx_typec_powered_cable_clear_state(sc);
	}
	queue_delayed_work(system_unbound_wq, &sc->vbus_only_work, 0);

	return 0;
}

static void sc27xx_typec_handle_vbus(struct sc27xx_typec *sc)
{
	u32 timeout = 25;

	dev_info(sc->dev, "%s event %d", __func__, sc->vbus_connect_value);
	if (sc->vbus_connect_value) {
		__pm_stay_awake(sc->wake_lock);
		do {
			/* typec has attach,not vbusonly, notify charging when analog headphone
			 *  have vbus connection
			 */
			if (sc->partner_connected && sc->pre_state != SC27XX_AUDIO_CABLE) {
				dev_info(sc->dev, "typec connect, not vbusonly\n");
				goto out;
			}
			msleep(20);
			if (!sc->vbus_connect_value) {
				dev_info(sc->dev, "vbusonly plug out during 500ms\n");
				goto out;
			}

		} while (--timeout > 0);
		/* vbus attach and 500ms no typec attch,
		 * attach cable recognize vbusonly cable.
		 */
		sc->vbus_only_connect = true;
		dev_info(sc->dev, "vbus_only_connect\n");
		extcon_set_state_sync(sc->edev, EXTCON_SINK, true);
		extcon_set_state_sync(sc->edev, EXTCON_USB, true);
out:
		__pm_relax(sc->wake_lock);
		return;
	}

	if (sc->vbus_only_connect) {
		dev_info(sc->dev, "vbus_only_disconnect\n");
		extcon_set_state_sync(sc->edev, EXTCON_SINK, false);
		extcon_set_state_sync(sc->edev, EXTCON_USB, false);
	}
	sc->vbus_only_connect = false;
}

static void sc27xx_typec_vbus_only_work(struct work_struct *work)
{
	struct sc27xx_typec *sc = typec_sc;

	spin_lock(&sc->vbus_lock);
	while (sc->vbus_events) {
		sc->vbus_events = false;
		spin_unlock(&sc->vbus_lock);
		sc27xx_typec_handle_vbus(sc);
		spin_lock(&sc->vbus_lock);
	}

	spin_unlock(&sc->vbus_lock);
}

static int sc27xx_typec_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *node = pdev->dev.of_node;
	struct sc27xx_typec *sc;
	#ifdef  CONFIG_ZTE_MBB_USB_FEATURE
	const char *typec_str;
	#endif
	const struct sprd_typec_variant_data *pdata;
	u32 pmic_id_low = 0;
	u32 pmic_id_high = 0;
	int mode, ret;
	
	pdata = of_device_get_match_data(dev);
	if (!pdata) {
		dev_err(&pdev->dev, "No matching driver data found\n");
		return -EINVAL;
	}

	sc = devm_kzalloc(&pdev->dev, sizeof(*sc), GFP_KERNEL);
	if (!sc)
		return -ENOMEM;

	sc->edev = devm_extcon_dev_allocate(&pdev->dev, sc27xx_typec_cable);
	if (IS_ERR(sc->edev)) {
		dev_err(&pdev->dev, "failed to allocate extcon device\n");
		return PTR_ERR(sc->edev);
	}

	ret = devm_extcon_dev_register(&pdev->dev, sc->edev);
	if (ret < 0) {
		dev_err(&pdev->dev, "can't register extcon device: %d\n", ret);
		return ret;
	}

	if (zte_aw35615_is_valid()) {
		dev_info(&pdev->dev, "AW35615 controller selected by bootloader\n");
		return -ENODEV;
	}

	sc->dev = &pdev->dev;
	sc->irq = platform_get_irq(pdev, 0);
	if (sc->irq < 0) {
		dev_err(sc->dev, "failed to get typec interrupt.\n");
		return sc->irq;
	}

	sc->regmap = dev_get_regmap(pdev->dev.parent, NULL);
	if (!sc->regmap) {
		dev_err(sc->dev, "failed to get regmap.\n");
		return -ENODEV;
	}

	ret = of_property_read_u32(node, "reg", &sc->base);
	if (ret) {
		dev_err(dev, "failed to get reg offset!\n");
		return ret;
	}

	regmap_read(sc->regmap, PMIC_ID_LOW, &pmic_id_low);
	regmap_read(sc->regmap, PMIC_ID_HIGH, &pmic_id_high);

	if ((pmic_id_high & 0xffff) == 0x9360 && (pmic_id_low & 0xffff) == 0xb000)
		sc->base = 0x300;

	dev_info(sc->dev, "typec base addr: 0x%x\n", sc->base);

	ret = of_property_read_u32(node, "sprd,mode", &mode);
	if (ret) {
		dev_err(dev, "failed to get typec port mode type\n");
		return ret;
	}

	sc->use_pdhub_c2c = of_property_read_bool(node, "use_pdhub_c2c");

	if (mode < TYPEC_PORT_DFP || mode > TYPEC_PORT_DRP
	    || mode == TYPEC_PORT_UFP) {
		mode = TYPEC_PORT_UFP;
		sc->usb20_only = true;
		dev_info(dev, "usb 2.0 only is enabled\n");
	}

	sc->var_data = pdata;
	sc->mode = mode;
	#ifdef CONFIG_ZTE_MBB_USB_FEATURE
	sc->prefer_role = TYPEC_NO_PREFERRED_ROLE;
	if (sc->mode == TYPEC_PORT_DRP) {
		ret = of_property_read_string(node, "try-power-role", &typec_str);
		if (ret == 0) {
			ret = typec_find_power_role(typec_str);
			if (ret < 0) {
				dev_err(dev, "try-power-role null\n");
				return ret;
			}
			sc->prefer_role = ret;
		}
	}
	#endif
	sc->power_mode_cable = false;
	if (!sc->use_pdhub_c2c) {
		sc->typec_cap.type = mode;
		sc->typec_cap.data = TYPEC_PORT_DRD;
		//sc->typec_cap.dr_set = sc27xx_typec_dr_set;
		//sc->typec_cap.pr_set = sc27xx_typec_pr_set;
		//sc->typec_cap.vconn_set = sc27xx_typec_vconn_set;
		sc->typec_cap.revision = USB_TYPEC_REV_1_2;
		sc->typec_cap.prefer_role = TYPEC_NO_PREFERRED_ROLE;
		sc->port = typec_register_port(&pdev->dev, &sc->typec_cap);
		if (IS_ERR(sc->port)) {
			dev_err(sc->dev, "failed to register port!\n");
			return PTR_ERR(sc->port);
		}
	}

	typec_sc = sc;
	spin_lock_init(&sc->lock);
	spin_lock_init(&sc->vbus_lock);
	sc27xx_set_dr_swap_executing(0);
	sc27xx_set_current_status_detach_or_attach(0);

	ret = sysfs_create_groups(&sc->dev->kobj, sc27xx_typec_groups);
	if (ret < 0)
		dev_err(sc->dev, "failed to create cc_polarity %d\n", ret);

	ret = typec_set_rtrim(sc);
	if (ret < 0) {
		dev_err(sc->dev, "failed to set typec rtrim %d\n", ret);
		goto error;
	}

	ret = devm_request_threaded_irq(sc->dev, sc->irq, NULL,
					sc27xx_typec_interrupt,
					IRQF_ONESHOT, /* System sleep is disabled or rejected before device PM. */
					dev_name(sc->dev), sc);
	if (ret) {
		dev_err(sc->dev, "failed to request irq %d\n", ret);
		goto error;
	}

	/* typec get vbus extcon notify */
	if (of_property_read_bool(pdev->dev.of_node, "extcon")) {
		sc->vbus_dev = extcon_get_edev_by_phandle(&pdev->dev, 0);
		if (IS_ERR(sc->vbus_dev)) {
			ret = PTR_ERR(sc->vbus_dev);
			dev_err(sc->dev, "typec failed to get vbus extcon.\n");
			goto error;
		}
		sc->vbus_nb.notifier_call = sc27xx_typec_get_vbus_notify;
		ret = extcon_register_notifier(sc->vbus_dev, EXTCON_USB,
						&sc->vbus_nb);
		if (ret) {
			dev_err(sc->dev, "failed to register vbus extcon.\n");
			goto error;
		}
		dev_info(sc->dev, "typec vbus notify on.\n");
	} else {
		dev_info(sc->dev, "typec vbus notify off.\n");
	}

	sc->pd_swap_evt = TYPEC_NO_SWAP;
	INIT_DELAYED_WORK(&sc->drswap_work, sc27xx_typec_extcon_drswap_work);
	INIT_DELAYED_WORK(&sc->vbus_only_work, sc27xx_typec_vbus_only_work);
	INIT_DELAYED_WORK(&sc->typec_drp_handle_work, sc27xx_typec_drp_handle_work);
	INIT_DELAYED_WORK(&sc->err_clr_work, sc27xx_typec_err_clr_work);
	if (extcon_get_state(sc->vbus_dev, EXTCON_USB) == true)
		sc27xx_typec_get_vbus_notify(&sc->vbus_nb, true, sc->vbus_dev);

	sc27xx_typec_register_ops();

	sc->wake_lock = wakeup_source_register(sc->dev, "sc27xx_typec");
	if (!sc->wake_lock) {
		dev_err(dev, "fail to register wakeup lock.\n");
		goto error;
	}

	sc27xx_typec_set_rp_rd_value(SC27XX_TYPEC_HW);
	ret = sc27xx_typec_enable(sc);
	if (ret)
		goto error;

	if (of_property_read_bool(pdev->dev.of_node, "extcon"))
		sc27xx_vbus_ok_bypass(sc);

	platform_set_drvdata(pdev, sc);
	sc27xx_typec_set_rp_level(SPRD_TYPEC_CC_RP_DEF);
	dev_info(sc->dev, "sc27xx_typec_probe success\n");

	return 0;

error:
	if (!sc->use_pdhub_c2c)
		typec_unregister_port(sc->port);
	return ret;
}

static void sc27xx_typec_remove(struct platform_device *pdev)
{
	struct sc27xx_typec *sc = platform_get_drvdata(pdev);

	sysfs_remove_groups(&sc->dev->kobj, sc27xx_typec_groups);
	sc27xx_typec_disconnect(sc, 0);
	if (!sc->use_pdhub_c2c)
		typec_unregister_port(sc->port);

}

static const struct of_device_id typec_sprd_match[] = {
	{.compatible = "sprd,sc2730-typec", .data = &sc2730_data},
	{.compatible = "sprd,sc2721-typec", .data = &sc2721_data},
	{.compatible = "sprd,ump96xx-typec", .data = &ump9620_data},
	{.compatible = "sprd,ump9651-typec", .data = &ump9651_data},
	{.compatible = "sprd,ump9360-typec", .data = &ump9360_data},
	{},
};
MODULE_DEVICE_TABLE(of, typec_sprd_match);

static struct platform_driver sc27xx_typec_driver = {
	.probe = sc27xx_typec_probe,
	.remove = sc27xx_typec_remove,
	.driver = {
		.name = "sc27xx-typec",
		.of_match_table = typec_sprd_match,
	},
};
module_platform_driver(sc27xx_typec_driver);
MODULE_LICENSE("GPL");
