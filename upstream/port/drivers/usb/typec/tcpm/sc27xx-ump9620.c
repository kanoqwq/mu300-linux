// SPDX-License-Identifier: GPL-2.0
/*
 * UMP9620 Type-C/PD controller, using mainline TCPM policy.
 * Register/PHY programming follows Spreadtrum's ZTE U30 Air GPL driver.
 * USB data role and VBUS power role are deliberately independent: a powered
 * dock must be able to charge the battery while DWC3 remains the USB host.
 */
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/extcon.h>
#include <linux/interrupt.h>
#include <linux/iopoll.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/nvmem-consumer.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/platform_device.h>
#include <linux/power_supply.h>
#include <linux/power/ump9620-battery.h>
#include <linux/property.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>
#include <linux/usb/pd.h>
#include <linux/usb/role.h>
#include <linux/usb/tcpm.h>
#include <linux/workqueue.h>

#include "sc27xx-ump9620-regs.h"

struct sc27xx_pd {
	struct device *dev;
	struct regmap *regmap, *aon_apb;
	const struct sc27xx_pd_variant_data *var_data;
	struct tcpc_dev tcpc;
	struct tcpm_port *port;
	struct usb_role_switch *role_sw;
	struct regulator *vbus;
	struct power_supply *charger;
	struct mutex lock;
	struct delayed_work cc_work;
	struct notifier_block vbus_nb;
	struct extcon_dev *vbus_extcon;
	struct fwnode_handle *connector;
	u32 base, typec_base, rc_cal, delta_cal, ref_cal;
	int irq, cc_irq;
	enum typec_role power_role;
	bool attached, pd_capable;
	bool vbus_on, tx_pending, tx_sop, stopping, irqs_enabled;
};

#include "sc27xx-ump9620-calibration.h"

static struct sc27xx_pd *to_pd(struct tcpc_dev *tcpc)
{
	return container_of(tcpc, struct sc27xx_pd, tcpc);
}

static int pd_update(struct sc27xx_pd *pd, u32 reg, u32 mask, u32 value)
{
	return regmap_update_bits(pd->regmap, pd->base + reg, mask, value);
}

static int cc_update(struct sc27xx_pd *pd, u32 reg, u32 mask, u32 value)
{
	return regmap_update_bits(pd->regmap, pd->typec_base + reg, mask, value);
}

static int sc27xx_get_vbus(struct tcpc_dev *tcpc)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	u32 status;
	int ret = regmap_read(pd->regmap, pd->typec_base + SC27XX_TYPEC_DBG1, &status);

	return ret ? ret : !!(status & SC27XX_TYPEC_VBUS_OK);
}

static int sc27xx_get_cc(struct tcpc_dev *tcpc, enum typec_cc_status *cc1,
			 enum typec_cc_status *cc2)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	enum typec_cc_status active = TYPEC_CC_OPEN;
	u32 status, state;
	int ret = regmap_read(pd->regmap, pd->typec_base + SC27XX_TYPEC_STATUS, &status);

	if (ret)
		return ret;
	state = status & SC27XX_STATE_MASK;
	if (state == SC27XX_ATTACHED_SNK) {
		switch (SC27XX_TYPEC_VBUS_CL(status)) {
		case 0: active = TYPEC_CC_RP_DEF; break;
		case 1: active = TYPEC_CC_RP_1_5; break;
		case 2: active = TYPEC_CC_RP_3_0; break;
		}
	} else if (state == SC27XX_ATTACHED_SRC || state == SC27XX_POWERED_CABLE) {
		active = TYPEC_CC_RD;
	}
	*cc1 = *cc2 = TYPEC_CC_OPEN;
	if (status & SC27XX_TYPEC_FINAL_SWITCH) {
		*cc1 = active;
		if (state == SC27XX_POWERED_CABLE)
			*cc2 = TYPEC_CC_RA;
	} else {
		*cc2 = active;
		if (state == SC27XX_POWERED_CABLE)
			*cc1 = TYPEC_CC_RA;
	}
	return 0;
}

static int sc27xx_set_cc(struct tcpc_dev *tcpc, enum typec_cc_status cc)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	u32 mode, rp = 1;

	/* The autonomous controller owns detach/open, as in the vendor driver. */
	if (cc == TYPEC_CC_OPEN)
		return 0;
	if (cc == TYPEC_CC_RD)
		mode = 0;
	else {
		mode = 1;
		if (cc == TYPEC_CC_RP_1_5)
			rp = 2;
		else if (cc == TYPEC_CC_RP_3_0)
			rp = 3;
	}
	if (pd->attached && ((cc == TYPEC_CC_RD && pd->power_role == TYPEC_SOURCE) ||
	    (cc != TYPEC_CC_RD && pd->power_role == TYPEC_SINK))) {
		int ret = regmap_write_bits(pd->regmap, pd->typec_base + SC27XX_TYPEC_WC_REG,
			SC27XX_TYPEC_PD_PR_SWAP, SC27XX_TYPEC_PD_PR_SWAP);
		if (ret)
			return ret;
	}
	return cc_update(pd, SC27XX_TYPEC_MODE, GENMASK(3, 0), mode | (rp << 2));
}

static int sc27xx_start_toggling(struct tcpc_dev *tcpc, enum typec_port_type port,
				 enum typec_cc_status cc)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	u32 mode = port == TYPEC_PORT_DRP ? 2 : port == TYPEC_PORT_SNK ? 0 : 1;
	int ret;

	/* Hardware DRP, no userspace polling or periodic role-flip task. */
	ret = cc_update(pd, SC27XX_TYPEC_MODE, GENMASK(3, 0), mode | BIT(2));
	/* A cable already attached before probe may not generate another edge. */
	if (!ret && !READ_ONCE(pd->stopping))
		mod_delayed_work(system_power_efficient_wq, &pd->cc_work, msecs_to_jiffies(8));
	return ret;
}

static int sc27xx_set_polarity(struct tcpc_dev *tcpc, enum typec_cc_polarity polarity)
{
	/* The autonomous Type-C block selects the active CC pin. The similarly
	 * named PHY_CFG1 bits are calibration switches, not a polarity mux. */
	return 0;
}

static int sc27xx_set_vconn(struct tcpc_dev *tcpc, bool on)
{
	return cc_update(to_pd(tcpc), SC27XX_TYPEC_PD_CFG,
		SC27XX_TYPEC_VCCON_LDO_EN, on ? SC27XX_TYPEC_VCCON_LDO_EN : 0);
}

static int sc27xx_set_vbus(struct tcpc_dev *tcpc, bool on, bool charge)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	int ret = 0;

	if (on && charge)
		return -EINVAL;
	mutex_lock(&pd->lock);
	if (pd->vbus_on != on) {
		ret = on ? regulator_enable(pd->vbus) : regulator_disable(pd->vbus);
		if (!ret)
			pd->vbus_on = on;
	}
	mutex_unlock(&pd->lock);
	/* The charger driver retains the user's charge limit when boost is off. */
	return ret;
}

static int sc27xx_set_current_limit(struct tcpc_dev *tcpc, u32 ma, u32 mv)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	union power_supply_propval value;
	bool pd_current = ma && READ_ONCE(pd->pd_capable);
	int ret;

	/* Only 5 V is advertised; do not request unsupported higher PD voltages. */
	if (mv > 5000)
		return -EINVAL;
	value.intval = pd_current ? POWER_SUPPLY_USB_TYPE_PD : POWER_SUPPLY_USB_TYPE_UNKNOWN;
	ret = power_supply_set_property(pd->charger, POWER_SUPPLY_PROP_USB_TYPE, &value);
	/* Without a PD contract the charger owns BC1.2/Rp arbitration. TCPM's
	 * default USB budget is not the BC1.2 result: writing it here races the
	 * completed DCP/CDP notification and can replace 2 A with 500 mA.
	 * UNKNOWN above returns ownership to that existing event-driven worker.
	 */
	if (ret || !pd_current)
		return ret;
	value.intval = min(ma, 2000U) * 1000;
	return power_supply_set_property(pd->charger, POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT, &value);
}

static int sc27xx_set_roles(struct tcpc_dev *tcpc, bool attached,
			  enum typec_role power, enum typec_data_role data)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	enum usb_role usb = !attached ? USB_ROLE_DEVICE :
		data == TYPEC_HOST ? USB_ROLE_HOST : USB_ROLE_DEVICE;
	int ret;

	ret = pd_update(pd, SC27XX_PD_CFG0, SC27XX_PD_SRC_SINK_MODE,
		power == TYPEC_SOURCE ? SC27XX_PD_SRC_SINK_MODE : 0);
	if (ret)
		return ret;
	/* A PD data-role swap must never implicitly enable the charger's boost. */
	ret = usb_role_switch_set_role(pd->role_sw, usb);
	if (!ret)
		dev_info(pd->dev, "%s: USB %s, power %s\n", attached ? "attached" : "detached",
			 usb_role_string(usb), power == TYPEC_SOURCE ? "source" : "sink");
	if (!ret) {
		pd->attached = attached;
		pd->power_role = power;
		if (!attached)
			pd->pd_capable = false;
	}
	return ret;
}

static int sc27xx_set_rx(struct tcpc_dev *tcpc, bool on)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	int ret;

	ret = pd_update(pd, SC27XX_PD_CFG1, SC27XX_PD_EN_SOP_RX | SC27XX_PD_RX_AUTO_GOOD_CRC,
		on ? SC27XX_PD_EN_SOP_RX | SC27XX_PD_RX_AUTO_GOOD_CRC : 0);
	if (ret)
		return ret;
	return pd_update(pd, SC27XX_PD_CFG0, SC27XX_PD_CTL_EN | SC27XX_PD_EN_SOP,
		on ? SC27XX_PD_CTL_EN | SC27XX_PD_EN_SOP : 0);
}

static int sc27xx_transmit(struct tcpc_dev *tcpc, enum tcpm_transmit_type type,
			const struct pd_message *msg, unsigned int rev)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	int ret, i, count = msg ? pd_header_cnt_le(msg->header) : 0;
	u32 payload;

	if (type != TCPC_TX_SOP && type != TCPC_TX_HARD_RESET)
		return -EOPNOTSUPP;
	mutex_lock(&pd->lock);
	ret = pd_update(pd, SC27XX_PD_CFG1, SC27XX_PD_RETRY_MASK,
		SC27XX_PD_RETRY(rev >= PD_REV30 ? 1 : 3));
	if (ret)
		goto out;
	/* Flush/reset bits are strobes: repeated transactions must write them. */
	ret = regmap_write_bits(pd->regmap, pd->base + SC27XX_PD_CTRL,
		SC27XX_PD_TX_FLASH, SC27XX_PD_TX_FLASH);
	if (ret)
		goto out;
	if (type == TCPC_TX_HARD_RESET) {
		ret = regmap_write_bits(pd->regmap, pd->base + SC27XX_PD_CTRL,
			SC27XX_PD_HARD_RESET, SC27XX_PD_HARD_RESET);
		goto pending;
	}
	ret = regmap_write(pd->regmap, pd->base + SC27XX_PD_HEAD_CFG, le16_to_cpu(msg->header));
	for (i = 0; !ret && i < count; i++) {
		payload = le32_to_cpu(msg->payload[i]);
		ret = regmap_write(pd->regmap, pd->base + SC27XX_PD_TX_BUF, payload & 0xffff);
		if (!ret)
			ret = regmap_write(pd->regmap, pd->base + SC27XX_PD_TX_BUF, payload >> 16);
	}
	if (!ret)
		ret = regmap_write_bits(pd->regmap, pd->base + SC27XX_PD_CTRL,
			SC27XX_PD_TX_START, SC27XX_PD_TX_START);
pending:
	if (!ret) {
		pd->tx_sop = type == TCPC_TX_SOP;
		pd->tx_pending = true;
	}
out:
	mutex_unlock(&pd->lock);
	return ret;
}

static void sc27xx_complete_tx(struct sc27xx_pd *pd, enum tcpm_transmit_status status)
{
	/* The same packet may produce TX_OK and an explicit GoodCRC reception. */
	if (pd->tx_pending) {
		/* With automatic GoodCRC reception, TX_OK can be the only ACK
		 * notification. Match the stock first-GoodCRC Type-C handoff here
		 * for either IRQ path. A hard reset is not an acknowledged SOP.
		 */
		if (status == TCPC_TX_SUCCESS && pd->tx_sop &&
		    cc_update(pd, SC27XX_TYPEC_PD_CFG, SC27XX_TYPEC_PD_CONSTRACT,
			      SC27XX_TYPEC_PD_CONSTRACT)) {
			dev_warn_ratelimited(pd->dev, "PD communication handoff failed\n");
			status = TCPC_TX_FAILED;
		}
		pd->tx_pending = false;
		tcpm_pd_transmit_complete(pd->port, status);
	}
}

static int sc27xx_receive(struct sc27xx_pd *pd)
{
	struct pd_message msg = {};
	u32 word, low, high, rx_status;
	int ret, count, i;

	/* Match the vendor IRQ path: PKG_RV can precede readable FIFO data.
	 * Do not pop stale/empty words and hand fictitious PDOs to TCPM.
	 * Ready frames do not sleep; only an empty receive FIFO waits, bounded
	 * by the stock 50 us / 2 ms hardware readiness interval/timeout.
	 */
	ret = regmap_read_poll_timeout(pd->regmap, pd->base + SC27XX_PD_STS1,
				       rx_status, (rx_status & ~SC27XX_PD_RX_EMPTY),
				       SC27XX_PD_POLL_RAW_STATUS, SC27XX_PD_RDY_TIMEOUT);
	if (ret)
		return ret;
	ret = regmap_read(pd->regmap, pd->base + SC27XX_PD_RX_BUF, &word);
	if (ret)
		return ret;
	/* The stock receive routine also samples status after popping header. */
	ret = regmap_read(pd->regmap, pd->base + SC27XX_PD_STS1, &rx_status);
	if (ret)
		return ret;
	msg.header = cpu_to_le16(word);
	count = pd_header_cnt_le(msg.header);
	for (i = 0; i < count; i++) {
		ret = regmap_read(pd->regmap, pd->base + SC27XX_PD_RX_BUF, &low);
		if (ret)
			return ret;
		ret = regmap_read(pd->regmap, pd->base + SC27XX_PD_RX_BUF, &high);
		if (ret)
			return ret;
		msg.payload[i] = cpu_to_le32((low & 0xffff) | (high << 16));
	}
	pd->pd_capable = true;
	if (!(le16_to_cpu(msg.header) & PD_HEADER_EXT_HDR) && count &&
	    pd_header_type_le(msg.header) == PD_DATA_SOURCE_CAP &&
	    (pdo_type(le32_to_cpu(msg.payload[0])) != PDO_TYPE_FIXED ||
	     pdo_fixed_voltage(le32_to_cpu(msg.payload[0])) != 5000))
		dev_warn_ratelimited(pd->dev,
			"PD invalid source capabilities: header %#x, FIFO %#x, first PDO %#x\n",
			le16_to_cpu(msg.header), rx_status, le32_to_cpu(msg.payload[0]));
	if (!count && pd_header_type_le(msg.header) == PD_CTRL_GOOD_CRC) {
		sc27xx_complete_tx(pd, TCPC_TX_SUCCESS);
	} else {
		tcpm_pd_receive(pd->port, &msg, TCPC_TX_SOP);
	}
	return 0;
}

static irqreturn_t sc27xx_pd_irq(int irq, void *data)
{
	struct sc27xx_pd *pd = data;
	u32 flags;
	int ret = 0;

	mutex_lock(&pd->lock);
	ret = regmap_read(pd->regmap, pd->base + SC27XX_INT_FLG, &flags);
	if (ret)
		goto out;
	regmap_write(pd->regmap, pd->base + SC27XX_INT_CLR, flags & SC27XX_INT_CLR_MASK);
	if (flags & SC27XX_PD_HARD_RST_FLAG) {
		pd->tx_pending = false;
		regmap_write_bits(pd->regmap, pd->base + SC27XX_PD_CTRL,
			SC27XX_PD_RX_FLASH | SC27XX_PD_RX_ID_CLR,
			SC27XX_PD_RX_FLASH | SC27XX_PD_RX_ID_CLR);
		tcpm_pd_hard_reset(pd->port);
	}
	if (flags & SC27XX_PD_PKG_RV_FLAG) {
		/* The FIFO holds one received frame plus controller trailer words.
		 * They are not another PD header. Match the vendor receive/flush
		 * sequence instead of passing those words to TCPM as messages. */
		ret = sc27xx_receive(pd);
		if (!ret)
			ret = regmap_write_bits(pd->regmap, pd->base + SC27XX_PD_CTRL,
				SC27XX_PD_RX_FLASH, SC27XX_PD_RX_FLASH);
	}
	if (flags & (SC27XX_PD_TX_ERROR_FLAG | SC27XX_PD_TX_COLLSION_FLAG))
		sc27xx_complete_tx(pd, TCPC_TX_FAILED);
	else if (flags & SC27XX_PD_TX_OK_FLAG)
		sc27xx_complete_tx(pd, TCPC_TX_SUCCESS);
	if (ret || (flags & SC27XX_PD_RX_FIFO_OVERFLOW_FLAG)) {
		regmap_write_bits(pd->regmap, pd->base + SC27XX_PD_CTRL,
			SC27XX_PD_RX_FLASH, SC27XX_PD_RX_FLASH);
		dev_warn_ratelimited(pd->dev, "PD receive error %d, flags %#x\n", ret, flags);
	}
out:
	mutex_unlock(&pd->lock);
	return IRQ_HANDLED;
}

static void sc27xx_cc_work(struct work_struct *work)
{
	struct sc27xx_pd *pd = container_of(to_delayed_work(work), struct sc27xx_pd, cc_work);

	if (!pd->stopping && pd->port) {
		tcpm_cc_change(pd->port);
		tcpm_vbus_change(pd->port);
	}
}

static irqreturn_t sc27xx_cc_irq(int irq, void *data)
{
	struct sc27xx_pd *pd = data;
	u32 event;

	if (regmap_read(pd->regmap, pd->typec_base + 0x14, &event))
		return IRQ_NONE;
	regmap_write(pd->regmap, pd->typec_base + 0x10, event & GENMASK(9, 0));
	mod_delayed_work(system_power_efficient_wq, &pd->cc_work, msecs_to_jiffies(8));
	return IRQ_HANDLED;
}

static int sc27xx_vbus_event(struct notifier_block *nb, unsigned long event, void *data)
{
	struct sc27xx_pd *pd = container_of(nb, struct sc27xx_pd, vbus_nb);

	if (!READ_ONCE(pd->stopping) && READ_ONCE(pd->port))
		mod_delayed_work(system_power_efficient_wq, &pd->cc_work, msecs_to_jiffies(8));
	return NOTIFY_OK;
}

static int sc27xx_init(struct tcpc_dev *tcpc)
{
	struct sc27xx_pd *pd = to_pd(tcpc);
	int ret;

	ret = sc27xx_pd_set_aon_clock(pd, true);
	if (ret)
		return ret;
	ret = sc27xx_pd_clk_cfg(pd);
	if (ret)
		return ret;
	ret = sc27xx_pd_delta_cal(pd);
	if (ret)
		return ret;
	ret = sc27xx_pd_rc_ref_cal(pd);
	if (ret)
		return ret;
	ret = sc27xx_pd_select_clk_mode(pd, SC27XX_PD_CLK_D6M_A6M);
	if (ret)
		return ret;
	ret = cc_update(pd, SC27XX_TYPEC_PD_CFG, SC27XX_TYPEC_PD_SUPPORT,
		SC27XX_TYPEC_PD_SUPPORT);
	if (ret)
		return ret;
	ret = regmap_write(pd->regmap, pd->typec_base + 0x20, 0xc7f);
	if (ret)
		return ret;
	ret = regmap_update_bits(pd->regmap, 0x234c, BIT(4), BIT(4));
	if (ret)
		return ret;
	ret = pd_update(pd, SC27XX_PD_CFG0, SC27XX_PD_BIST_MODE_EN, SC27XX_PD_BIST_MODE_EN);
	if (ret)
		return ret;
	regmap_write(pd->regmap, pd->base + SC27XX_INT_CLR, SC27XX_INT_CLR_MASK);
	ret = regmap_write(pd->regmap, pd->base + SC27XX_INT_EN, SC27XX_INT_EN_MASK);
	if (ret)
		return ret;
	return cc_update(pd, 0x0c, BIT(0) | BIT(1), BIT(0) | BIT(1));
}

static void sc27xx_cleanup(void *data)
{
	struct sc27xx_pd *pd = data;

	if (pd->stopping)
		return;
	WRITE_ONCE(pd->stopping, true);
	if (pd->vbus_extcon) {
		devm_extcon_unregister_notifier(pd->dev, pd->vbus_extcon,
			EXTCON_USB, &pd->vbus_nb);
		pd->vbus_extcon = NULL;
	}
	if (pd->irqs_enabled) {
		disable_irq(pd->irq);
		disable_irq(pd->cc_irq);
		pd->irqs_enabled = false;
	}
	cancel_delayed_work_sync(&pd->cc_work);
	if (pd->port) {
		tcpm_unregister_port(pd->port);
		pd->port = NULL;
	}
	if (pd->vbus_on) {
		regulator_disable(pd->vbus);
		pd->vbus_on = false;
	}
	sc27xx_pd_disable_clk(pd);
}

static void sc27xx_put_role(void *data)
{
	usb_role_switch_put(data);
}

static void sc27xx_put_charger(void *data)
{
	power_supply_put(data);
}

static void sc27xx_put_connector(void *data)
{
	fwnode_remove_software_node(data);
}

static int sc27xx_cc_cal(struct sc27xx_pd *pd, struct device_node *node)
{
	const char *names[] = { "typec_cc1_cal", "typec_cc2_cal" };
	const unsigned int shifts[] = { 1, 11 };
	struct nvmem_cell *cell;
	u32 trim = 0, raw;
	void *bytes;
	size_t length;
	int i;

	for (i = 0; i < ARRAY_SIZE(names); i++) {
		cell = of_nvmem_cell_get(node, names[i]);
		if (IS_ERR(cell))
			return PTR_ERR(cell);
		bytes = nvmem_cell_read(cell, &length);
		nvmem_cell_put(cell);
		if (IS_ERR(bytes))
			return PTR_ERR(bytes);
		if (length < 2) {
			kfree(bytes);
			return -EINVAL;
		}
		raw = ((u8 *)bytes)[0] | ((u8 *)bytes)[1] << 8;
		kfree(bytes);
		trim |= ((raw >> shifts[i]) & 31) << (i * 5);
	}
	return regmap_write(pd->regmap, pd->typec_base + 0x3c, trim);
}

static int sc27xx_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct sc27xx_pd *pd;
	struct device_node *typec, *usb, *gpio;
	struct extcon_dev *extcon;
	int ret;
	/* Existing board power path is 5 V only. Do not import vendor PPS PDOs. */
	u32 src[] = { PDO_FIXED(5000, 500, PDO_FIXED_DUAL_ROLE | PDO_FIXED_DATA_SWAP | PDO_FIXED_USB_COMM) };
	u32 snk[] = { PDO_FIXED(5000, 2000, PDO_FIXED_DUAL_ROLE | PDO_FIXED_DATA_SWAP | PDO_FIXED_USB_COMM) };
	const struct property_entry properties[] = {
		/* This driver is gated to battery-policy boards below. Let TCPM
		 * use battery-backed recovery, rather than treating a powered dock
		 * as the system's only power source. PDO limits/roles stay unchanged.
		 */
		PROPERTY_ENTRY_BOOL("self-powered"),
		PROPERTY_ENTRY_STRING("power-role", "dual"),
		PROPERTY_ENTRY_STRING("data-role", "dual"),
		PROPERTY_ENTRY_STRING("try-power-role", "sink"),
		PROPERTY_ENTRY_U32_ARRAY("source-pdos", src),
		PROPERTY_ENTRY_U32_ARRAY("sink-pdos", snk),
		PROPERTY_ENTRY_U32("op-sink-microwatt", 10000000), {}
	};

	/* Batteryless F50/V50 must retain their stock USB power path. */
	if (!ump9620_has_battery_policy())
		return -ENODEV;

	typec = of_get_compatible_child(dev->parent->of_node, "sprd,sc27xx-typec");
	if (!typec)
		return -ENODEV;
	/* This path needs a real battery board with the PD-hub connector. */
	if (!of_property_read_bool(typec, "use_pdhub_c2c")) {
		of_node_put(typec);
		return -ENODEV;
	}
	pd = devm_kzalloc(dev, sizeof(*pd), GFP_KERNEL);
	if (!pd) {
		of_node_put(typec);
		return -ENOMEM;
	}
	pd->dev = dev;
	pd->var_data = &ump9620_data;
	pd->regmap = dev_get_regmap(dev->parent, NULL);
	mutex_init(&pd->lock);
	INIT_DELAYED_WORK(&pd->cc_work, sc27xx_cc_work);
	ret = of_property_read_u32_index(dev->of_node, "reg", 0, &pd->base);
	if (!ret)
		ret = of_property_read_u32(typec, "reg", &pd->typec_base);
	if (!ret)
		ret = sc27xx_cc_cal(pd, typec);
	pd->cc_irq = of_irq_get(typec, 0);
	of_node_put(typec);
	if (ret)
		return ret;
	if (pd->cc_irq < 0)
		return pd->cc_irq;
	pd->irq = platform_get_irq(pdev, 0);
	if (pd->irq < 0)
		return pd->irq;
	pd->aon_apb = syscon_regmap_lookup_by_phandle(dev->of_node, "sprd,syscon-aon-apb");
	if (IS_ERR(pd->aon_apb))
		return PTR_ERR(pd->aon_apb);
	pd->vbus = devm_regulator_get(dev, "vbus");
	if (IS_ERR(pd->vbus))
		return dev_err_probe(dev, PTR_ERR(pd->vbus), "OTG power regulator\n");
	pd->charger = power_supply_get_by_name("bq256xx-charger");
	if (!pd->charger)
		return -EPROBE_DEFER;
	ret = devm_add_action_or_reset(dev, sc27xx_put_charger, pd->charger);
	if (ret)
		return ret;
	usb = of_find_compatible_node(NULL, NULL, "snps,sprd-dwc3");
	if (!usb)
		return -ENODEV;
	pd->role_sw = usb_role_switch_find_by_fwnode(of_fwnode_handle(usb));
	of_node_put(usb);
	if (IS_ERR_OR_NULL(pd->role_sw))
		return pd->role_sw ? PTR_ERR(pd->role_sw) : -EPROBE_DEFER;
	ret = devm_add_action_or_reset(dev, sc27xx_put_role, pd->role_sw);
	if (ret)
		return ret;
	ret = sc27xx_pd_cal(pd);
	if (ret)
		return dev_err_probe(dev, ret, "PD efuse calibration\n");
	/* A software-node parent must itself be a software node, not an OF node. */
	pd->connector = fwnode_create_software_node(properties, NULL);
	if (IS_ERR(pd->connector))
		return PTR_ERR(pd->connector);
	ret = devm_add_action_or_reset(dev, sc27xx_put_connector, pd->connector);
	if (ret)
		return ret;
	pd->tcpc = (struct tcpc_dev) {
		.fwnode = pd->connector, .init = sc27xx_init,
		.get_vbus = sc27xx_get_vbus, .get_cc = sc27xx_get_cc,
		.set_cc = sc27xx_set_cc, .start_toggling = sc27xx_start_toggling,
		.set_polarity = sc27xx_set_polarity, .set_vconn = sc27xx_set_vconn,
		.set_vbus = sc27xx_set_vbus, .set_current_limit = sc27xx_set_current_limit,
		.set_roles = sc27xx_set_roles, .set_pd_rx = sc27xx_set_rx,
		.pd_transmit = sc27xx_transmit,
	};
	ret = devm_request_threaded_irq(dev, pd->irq, NULL, sc27xx_pd_irq,
		IRQF_ONESHOT | IRQF_NO_AUTOEN, "ump9620-pd", pd);
	if (ret)
		goto put_node;
	ret = devm_request_threaded_irq(dev, pd->cc_irq, NULL, sc27xx_cc_irq,
		IRQF_ONESHOT | IRQF_NO_AUTOEN, "ump9620-cc", pd);
	if (ret)
		goto put_node;
	/* VBUS may change without another CC attach during a PD power-role swap. */
	gpio = of_find_compatible_node(NULL, NULL, "linux,extcon-usb-gpio");
	if (gpio) {
		extcon = extcon_find_edev_by_node(gpio);
		of_node_put(gpio);
		if (!IS_ERR(extcon)) {
			pd->vbus_nb.notifier_call = sc27xx_vbus_event;
			ret = devm_extcon_register_notifier(dev, extcon, EXTCON_USB, &pd->vbus_nb);
			if (ret)
				return ret;
			pd->vbus_extcon = extcon;
		}
	}
	/* Register TCPM last: its workers allocate devres after probe returns.
	 * Unregister in .remove before devres teardown, never as a devm action. */
	pd->port = tcpm_register_port(dev, &pd->tcpc);
	if (IS_ERR(pd->port)) {
		ret = PTR_ERR(pd->port);
		pd->port = NULL;
		sc27xx_cleanup(pd);
		return ret;
	}
	platform_set_drvdata(pdev, pd);
	enable_irq(pd->irq);
	enable_irq(pd->cc_irq);
	pd->irqs_enabled = true;
	mod_delayed_work(system_power_efficient_wq, &pd->cc_work, 0);
	dev_info(dev, "Type-C/PD active: automatic USB roles, 5 V sink/source\n");
	return 0;
put_node:
	return ret;
}

static void sc27xx_shutdown(struct platform_device *pdev)
{
	sc27xx_cleanup(platform_get_drvdata(pdev));
}

static const struct of_device_id sc27xx_ids[] = {
	{ .compatible = "sprd,ump9620-pd" }, {}
};
MODULE_DEVICE_TABLE(of, sc27xx_ids);
static struct platform_driver sc27xx_driver = {
	.probe = sc27xx_probe, .remove = sc27xx_shutdown,
	.shutdown = sc27xx_shutdown,
	.driver = { .name = "ump9620-tcpm", .of_match_table = sc27xx_ids },
};
module_platform_driver(sc27xx_driver);
MODULE_DESCRIPTION("UMP9620 Type-C/PD mainline TCPM controller");
MODULE_LICENSE("GPL");
