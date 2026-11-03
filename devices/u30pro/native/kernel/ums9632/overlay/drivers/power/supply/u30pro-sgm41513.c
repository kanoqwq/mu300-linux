// SPDX-License-Identifier: GPL-2.0-only
/* Native U30 Pro master SGM41513. Identify the real fixed 0x1a device behind
 * the original ti,bq2560x_chg DT node. Preserve firmware charging settings;
 * report measured status and programmed limits through power_supply.
 * OTG operations use the real hardware bit and reject sourcing onto live VBUS.
 * Charging policy (source tables, JEITA, bypass) is in sgm_policy() below; it only
 * changes input/charge current, lowers the charge voltage in the hot band and restores
 * the firmware value afterwards.  It does not invent battery measurements.
 */
#include <linux/bitops.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/power_supply.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/machine.h>
#include <linux/regulator/of_regulator.h>
#include <linux/workqueue.h>
#include <linux/slab.h>
#include <linux/usb/phy.h>
#include "u30pro-sgm41513-values.h"

#define SGM_CONTROL 0x01
#define SGM_TIMER 0x05
#define SGM_STATUS 0x08
#define SGM_VBUS_STATUS 0x0a
#define SGM_ID 0x0b
#define SGM_OTG BIT(5)
#define SGM_WD_RESET BIT(6)

struct sgm_jeita {
 int t_up, t_rec;	/* 0.1 degC (stock value minus the 1000 offset) */
 u32 ichg, cv;		/* uA, uV */
};

struct u30_sgm {
 struct device *dev;
 struct i2c_client *hw;
 struct mutex lock;
 struct power_supply *psy;
 struct power_supply_desc psy_desc;
 struct regulator_desc vbus_desc;
 struct delayed_work poll;
 struct usb_phy *usb_phy;
 struct notifier_block usb_nb;
 struct work_struct source_work;
 bool stopping;
 int legacy_id_result;
 int last_error;
 u8 last_status;
 unsigned long data_writes;
 unsigned long watchdog_pings;
 unsigned long poll_errors;
 /* charging policy */
 bool policy_on;
 u8 boot_vreg;			/* REG04 as left by the firmware: upper bound for the charge voltage */
 u32 tbl_ichg[4], tbl_iin[4];	/* 0 SDP, 1 DCP, 2 CDP, 3 other */
 struct sgm_jeita jeita[8];
 int jeita_n, jeita_idx;
 int behaviour;			/* POWER_SUPPLY_CHARGE_BEHAVIOUR_* */
 u32 user_iin, user_ichg;	/* uA, 0 = automatic */
 int pol_type, pol_temp, pol_pd_ma;
 u32 pol_iin, pol_ichg, pol_cv;
 bool pol_charge;
};

/* All callers hold lock; no read of clear-on-read REG09/REG0E here. */
static int sgm_read(struct u30_sgm *s, u8 reg)
{
 return i2c_smbus_read_byte_data(s->hw, reg);
}
static int sgm_write_control(struct u30_sgm *s, u8 value)
{
 int ret = i2c_smbus_write_byte_data(s->hw, SGM_CONTROL, value);
 if (!ret) s->data_writes++;
 return ret;
}

static int sgm_ichg_code(u32 ua)
{
 int code, best = 0;
 /* codes above 60 decode to more than 3 A (the shared helper clamps them): never select those */
 for (code = 0; code <= 60; code++)
  if (sgm41513_ichg_ua(code) <= ua)
   best = code;
 return best;
}

static int sgm_cv_code(u32 uv)
{
 int code = (int)(uv / 1000 - 3856) / 32;
 if (code < 0) code = 0;
 if (code > 24) code = 24;
 if (code == 15) code = 14;	/* code 15 is the 4.35 V special value */
 return code;
}

static int sgm_update_reg(struct u30_sgm *s, u8 reg, u8 mask, u8 value)
{
 int cur = sgm_read(s, reg), ret;
 if (cur < 0) return cur;
 if ((cur & mask) == value) return 0;
 ret = i2c_smbus_write_byte_data(s->hw, reg, (cur & ~mask) | value);
 if (!ret) s->data_writes++;
 return ret;
}

static void sgm_load_tables(struct u30_sgm *s)
{
 static const char * const names[4] = {
  "charge-sdp-current-microamp", "charge-dcp-current-microamp",
  "charge-cdp-current-microamp", "charge-unknown-current-microamp" };
 struct device_node *bat = of_find_compatible_node(NULL, NULL, "simple-battery");
 u32 pair[2], row[4];
 int i, n;
 if (!bat) return;
 for (i = 0; i < 4; i++) {
  if (of_property_read_u32_array(bat, names[i], pair, 2)) goto out;
  s->tbl_ichg[i] = pair[0]; s->tbl_iin[i] = pair[1];
 }
 n = of_property_count_u32_elems(bat, "dcp-jeita-temp-table");
 if (n < 8 || n % 4 || n / 4 > ARRAY_SIZE(s->jeita)) goto out;
 for (i = 0; i < n / 4; i++) {
  if (of_property_read_u32_index(bat, "dcp-jeita-temp-table", i * 4 + 0, &row[0]) ||
      of_property_read_u32_index(bat, "dcp-jeita-temp-table", i * 4 + 1, &row[1]) ||
      of_property_read_u32_index(bat, "dcp-jeita-temp-table", i * 4 + 2, &row[2]) ||
      of_property_read_u32_index(bat, "dcp-jeita-temp-table", i * 4 + 3, &row[3]))
   goto out;
  s->jeita[i].t_up = (int)row[0] - 1000;
  s->jeita[i].t_rec = (int)row[1] - 1000;
  s->jeita[i].ichg = row[2];
  s->jeita[i].cv = row[3];
 }
 s->jeita_n = n / 4;
 s->policy_on = true;
out:
 of_node_put(bat);
}

/* index of the JEITA band for a temperature: band i covers [t_up[i-1], t_up[i]); i == n means too hot */
static int sgm_band(struct u30_sgm *s, int t)
{
 int i;
 for (i = 0; i < s->jeita_n; i++)
  if (t < s->jeita[i].t_up) return i;
 return s->jeita_n;
}

/* SGM41513 PN0's PSEL indication is not a BC1.2 DCP/CDP result. The stock
 * Unisoc PHY/PMIC already owns D+/D- detection and notifies its final type. */
static int sgm_source_type(struct u30_sgm *s)
{
 if (READ_ONCE(s->usb_phy->chg_state) != USB_CHARGER_PRESENT) return 3;
 switch (READ_ONCE(s->usb_phy->chg_type)) {
 case SDP_TYPE: return 0;
 case DCP_TYPE: return 1;
 case CDP_TYPE: return 2;
 default: return 3;
 }
}

static int sgm_usb_type(struct u30_sgm *s)
{
 switch (sgm_source_type(s)) {
 case 0: return POWER_SUPPLY_USB_TYPE_SDP;
 case 1: return POWER_SUPPLY_USB_TYPE_DCP;
 case 2: return POWER_SUPPLY_USB_TYPE_CDP;
 default: return POWER_SUPPLY_USB_TYPE_UNKNOWN;
 }
}

/* Called with s->lock held, from the 5 s poll and after any user change. */
static void sgm_policy(struct u30_sgm *s)
{
 struct power_supply *bat, *pd;
 union power_supply_propval v;
 int status, control, type, band, temp = INT_MIN;
 u32 iin, ichg, cv;
 bool charge;

 if (!s->policy_on) return;
 status = sgm_read(s, SGM_STATUS);
 control = sgm_read(s, SGM_CONTROL);
 if (status < 0 || control < 0) return;
 if ((control & SGM_OTG) || !(status & BIT(2))) return;	/* OTG source mode, or no valid input */

 type = sgm_source_type(s);
 iin = s->tbl_iin[type];
 ichg = s->tbl_ichg[type];

 s->pol_pd_ma = 0;
 pd = power_supply_get_by_name("sprd-tcpm-source-psy-sc27xx-pd");
 if (pd) {
  int online = 0, volt = 0, cur = 0;
  if (!power_supply_get_property(pd, POWER_SUPPLY_PROP_ONLINE, &v)) online = v.intval;
  if (!power_supply_get_property(pd, POWER_SUPPLY_PROP_VOLTAGE_NOW, &v)) volt = v.intval;
  if (!power_supply_get_property(pd, POWER_SUPPLY_PROP_CURRENT_MAX, &v)) cur = v.intval;
  power_supply_put(pd);
  if (online && volt > 0 && volt <= 6000000 && cur > 0) {
   s->pol_pd_ma = cur / 1000;
   if ((u32)cur > iin) iin = min_t(u32, cur, 3200000);
   if (ichg < 3000000) ichg = 3000000;
  }
 }
 if (s->user_iin && s->user_iin < iin) iin = s->user_iin;
 if (s->user_ichg && s->user_ichg < ichg) ichg = s->user_ichg;
 if (iin < 100000) iin = 100000;
 if (iin > 3200000) iin = 3200000;
 if (ichg > 3000000) ichg = 3000000;

 /* JEITA */
 cv = sgm41513_cv_uv(s->boot_vreg);
 bat = power_supply_get_by_name("u30pro-battery");
 if (bat) {
  if (!power_supply_get_property(bat, POWER_SUPPLY_PROP_TEMP, &v)) temp = v.intval;
  power_supply_put(bat);
 }
 if (temp == INT_MIN) {
  if (ichg > 1000000) ichg = 1000000;	/* no temperature: stay gentle */
 } else {
  band = sgm_band(s, temp);
  if (s->jeita_idx >= 0 && band != s->jeita_idx) {
   /* leaving a band towards a less restrictive one needs a 3 degC margin */
   u32 cur_i = s->jeita_idx < s->jeita_n ? s->jeita[s->jeita_idx].ichg : 0;
   u32 new_i = band < s->jeita_n ? s->jeita[band].ichg : 0;
   if (new_i > cur_i) {
    int lo = band > 0 ? s->jeita[band - 1].t_up : INT_MIN + 30;
    int hi = band < s->jeita_n ? s->jeita[band].t_up : INT_MAX - 30;
    if (temp < lo + 30 || temp > hi - 30) band = s->jeita_idx;
   }
  }
  s->jeita_idx = band;
  if (band >= s->jeita_n) {
   ichg = 0;
  } else {
   if (s->jeita[band].ichg < ichg) ichg = s->jeita[band].ichg;
   if (s->jeita[band].cv < cv) cv = s->jeita[band].cv;
  }
 }
 charge = ichg > 0 && s->behaviour != POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE;

 sgm_update_reg(s, 0x00, 0x1f, (iin / 1000 - 100) / 100);
 if (ichg > 0) sgm_update_reg(s, 0x02, 0x3f, sgm_ichg_code(ichg));
 sgm_update_reg(s, 0x04, 0xf8, sgm_cv_code(cv) << 3);
 sgm_update_reg(s, 0x01, BIT(4), charge ? BIT(4) : 0);
 s->pol_type = type; s->pol_temp = temp == INT_MIN ? -9999 : temp;
 s->pol_iin = iin; s->pol_ichg = ichg; s->pol_cv = cv; s->pol_charge = charge;
}

static void sgm_source_work(struct work_struct *work)
{
 struct u30_sgm *s = container_of(work, struct u30_sgm, source_work);

 if (READ_ONCE(s->stopping)) return;
 mutex_lock(&s->lock);
 if (!s->stopping) sgm_policy(s);
 mutex_unlock(&s->lock);
 power_supply_changed(s->psy);
}

/* USB PHY uses an atomic notifier. Never do I2C, lock a mutex or run a new
 * charger detection from it; defer policy to normal process context. */
static int sgm_source_notify(struct notifier_block *nb, unsigned long event, void *data)
{
 struct u30_sgm *s = container_of(nb, struct u30_sgm, usb_nb);

 (void)event;
 if (data != s->usb_phy || READ_ONCE(s->stopping)) return NOTIFY_DONE;
 schedule_work(&s->source_work);
 return NOTIFY_OK;
}

static void sgm_unregister_source(void *data)
{
 struct u30_sgm *s = data;

 usb_unregister_notifier(s->usb_phy, &s->usb_nb);
}

static int sgm_set_property(struct power_supply *psy, enum power_supply_property property,
                            const union power_supply_propval *value)
{
 struct u30_sgm *s = power_supply_get_drvdata(psy);
 int ret = 0;
 mutex_lock(&s->lock);
 switch (property) {
 case POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR:
  if (value->intval != POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO &&
      value->intval != POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE) { ret = -EINVAL; break; }
  s->behaviour = value->intval;
  break;
 case POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT:
  if (value->intval < 0 || value->intval > 3200000) { ret = -EINVAL; break; }
  s->user_iin = value->intval;		/* 0 = automatic */
  break;
 case POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT:
  if (value->intval < 0 || value->intval > 3000000) { ret = -EINVAL; break; }
  s->user_ichg = value->intval;		/* 0 = automatic */
  break;
 default: ret = -EINVAL;
 }
 if (!ret) sgm_policy(s);
 mutex_unlock(&s->lock);
 return ret;
}

static int sgm_property_is_writeable(struct power_supply *psy, enum power_supply_property property)
{
 return property == POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR ||
        property == POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT ||
        property == POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT;
}

static enum power_supply_property sgm_properties[] = {
 POWER_SUPPLY_PROP_MANUFACTURER, POWER_SUPPLY_PROP_MODEL_NAME,
 POWER_SUPPLY_PROP_ONLINE, POWER_SUPPLY_PROP_STATUS,
 POWER_SUPPLY_PROP_CHARGE_TYPE, POWER_SUPPLY_PROP_USB_TYPE,
 POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,
 POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT,
 POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE,
 POWER_SUPPLY_PROP_PRECHARGE_CURRENT, POWER_SUPPLY_PROP_CHARGE_TERM_CURRENT,
 POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR,
};

static int sgm_get_property(struct power_supply *psy,
                           enum power_supply_property property,
                           union power_supply_propval *value)
{
 struct u30_sgm *s = power_supply_get_drvdata(psy);
 int reg, ret = 0, control;

 if (property == POWER_SUPPLY_PROP_MANUFACTURER) {
  value->strval = "SG Micro"; return 0;
 }
 if (property == POWER_SUPPLY_PROP_MODEL_NAME) {
  value->strval = "SGM41513"; return 0;
 }
 mutex_lock(&s->lock);
 switch (property) {
 case POWER_SUPPLY_PROP_ONLINE:
 case POWER_SUPPLY_PROP_STATUS:
 case POWER_SUPPLY_PROP_CHARGE_TYPE:
 case POWER_SUPPLY_PROP_USB_TYPE:
  reg = sgm_read(s, SGM_STATUS);
  if (reg < 0) { ret = reg; break; }
  control = sgm_read(s, SGM_CONTROL);
  if (control < 0) { ret = control; break; }
  if (property == POWER_SUPPLY_PROP_ONLINE)
   value->intval = !!(reg & BIT(2)) && !(control & SGM_OTG);
  else if (property == POWER_SUPPLY_PROP_USB_TYPE)
   value->intval = (reg & BIT(2)) && !(control & SGM_OTG) ?
       sgm_usb_type(s) : POWER_SUPPLY_USB_TYPE_UNKNOWN;
  else if (!(reg & BIT(2)) || control & SGM_OTG)
   value->intval = property == POWER_SUPPLY_PROP_STATUS ?
       POWER_SUPPLY_STATUS_NOT_CHARGING : POWER_SUPPLY_CHARGE_TYPE_NONE;
  else if (property == POWER_SUPPLY_PROP_STATUS) {
   static const int status[] = {POWER_SUPPLY_STATUS_NOT_CHARGING,
       POWER_SUPPLY_STATUS_CHARGING, POWER_SUPPLY_STATUS_CHARGING,
       POWER_SUPPLY_STATUS_FULL};
   value->intval = status[(reg >> 3) & 3];
  } else {
   static const int type[] = {POWER_SUPPLY_CHARGE_TYPE_NONE,
       POWER_SUPPLY_CHARGE_TYPE_TRICKLE, POWER_SUPPLY_CHARGE_TYPE_FAST,
       POWER_SUPPLY_CHARGE_TYPE_NONE};
   value->intval = type[(reg >> 3) & 3];
  }
  break;
 case POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT:
  reg = sgm_read(s, 0x00);
  if (reg < 0) ret = reg; else value->intval = sgm41513_iin_ua(reg);
  break;
 case POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT:
  reg = sgm_read(s, 0x02);
  if (reg < 0) ret = reg; else value->intval = sgm41513_ichg_ua(reg);
  break;
 case POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE:
  reg = sgm_read(s, 0x04);
  if (reg < 0) ret = reg; else value->intval = sgm41513_cv_uv(reg);
  break;
 case POWER_SUPPLY_PROP_CHARGE_BEHAVIOUR:
  value->intval = s->behaviour;
  break;
 case POWER_SUPPLY_PROP_PRECHARGE_CURRENT:
 case POWER_SUPPLY_PROP_CHARGE_TERM_CURRENT:
  reg = sgm_read(s, 0x03);
  if (reg < 0) ret = reg;
  else value->intval = sgm41513_small_current_ua(
       property == POWER_SUPPLY_PROP_PRECHARGE_CURRENT ? reg >> 4 : reg);
  break;
 default: ret = -EINVAL; break;
 }
 s->last_error = ret;
 mutex_unlock(&s->lock);
 return ret;
}

static int sgm_vbus_is_enabled(struct regulator_dev *rdev)
{
 struct u30_sgm *s = rdev_get_drvdata(rdev);
 int ret;
 mutex_lock(&s->lock);ret = sgm_read(s, SGM_CONTROL);mutex_unlock(&s->lock);
 return ret < 0 ? ret : !!(ret & SGM_OTG);
}
static int sgm_vbus_enable(struct regulator_dev *rdev)
{
 struct u30_sgm *s = rdev_get_drvdata(rdev);
 int control, status, ret = 0;
 mutex_lock(&s->lock);
 control = sgm_read(s, SGM_CONTROL);
 if (control < 0) { ret = control; goto out; }
 if (control & SGM_OTG) goto out;
 status = sgm_read(s, SGM_VBUS_STATUS);
 if (status < 0) { ret = status; goto out; }
 if (status & BIT(7)) { ret = -EBUSY; goto out; }
 ret = sgm_write_control(s, (control & ~SGM_WD_RESET) | SGM_OTG);
out:
 s->last_error = ret;mutex_unlock(&s->lock);return ret;
}
static int sgm_vbus_disable(struct regulator_dev *rdev)
{
 struct u30_sgm *s = rdev_get_drvdata(rdev);
 int control, ret = 0;
 mutex_lock(&s->lock);
 control = sgm_read(s, SGM_CONTROL);
 if (control < 0) ret = control;
 else if (control & SGM_OTG)
  ret = sgm_write_control(s, control & ~(SGM_OTG | SGM_WD_RESET));
 s->last_error = ret;mutex_unlock(&s->lock);return ret;
}
static int sgm_vbus_voltage_sel(struct regulator_dev *rdev)
{
 struct u30_sgm *s = rdev_get_drvdata(rdev);
 int value;
 mutex_lock(&s->lock);value = sgm_read(s, 0x06);mutex_unlock(&s->lock);
 return value < 0 ? value : (value >> 4) & 3;
}
static const unsigned int sgm_boost_uv[] = {4850000,5000000,5150000,5300000};
static const struct regulator_ops sgm_vbus_ops = {
 .enable = sgm_vbus_enable, .disable = sgm_vbus_disable,
 .is_enabled = sgm_vbus_is_enabled,
 .get_voltage_sel = sgm_vbus_voltage_sel,
 .list_voltage = regulator_list_voltage_table,
};

static void sgm_poll(struct work_struct *work)
{
 struct u30_sgm *s = container_of(to_delayed_work(work), struct u30_sgm, poll);
 int status, timer, control, ret = 0;
 bool changed = false;
 if (READ_ONCE(s->stopping)) return;
 mutex_lock(&s->lock);
 status = sgm_read(s, SGM_STATUS);timer = sgm_read(s, SGM_TIMER);
 if (status < 0 || timer < 0) ret = status < 0 ? status : timer;
 else {
  changed = status != s->last_status;s->last_status = status;
  /* Service an already-armed IC watchdog. Do not change its timeout,
   * charge enable, voltage/current limits, safety timer, or NTC policy. */
  if (timer & GENMASK(5,4)) {
   control = sgm_read(s, SGM_CONTROL);
   ret = control < 0 ? control : sgm_write_control(s, control | SGM_WD_RESET);
   if (!ret) s->watchdog_pings++;
  }
 }
 if (!ret) sgm_policy(s);
 s->last_error = ret;
 if (ret) s->poll_errors++;
 mutex_unlock(&s->lock);
 if (changed) power_supply_changed(s->psy);
 if (ret) dev_warn_ratelimited(s->dev, "status/watchdog I2C error %d\n", ret);
 if (!READ_ONCE(s->stopping)) schedule_delayed_work(&s->poll, 5 * HZ);
}
static void sgm_cancel_poll(void *data)
{
 struct u30_sgm *s = data;
 WRITE_ONCE(s->stopping, true);cancel_delayed_work_sync(&s->poll);
 cancel_work_sync(&s->source_work);
}

static ssize_t register_snapshot_show(struct device *dev,
                                     struct device_attribute *attr, char *buf)
{
 static const u8 regs[] = {0,1,2,3,4,5,6,7,8,10,11};
 struct u30_sgm *s = dev_get_drvdata(dev);
 int len = 0, value, i;
 mutex_lock(&s->lock);
 for (i = 0; i < ARRAY_SIZE(regs); i++) {
  value = sgm_read(s, regs[i]);
  if (value < 0) { len = value; break; }
  len += sysfs_emit_at(buf, len, "%02x=%02x\n", regs[i], value);
 }
 mutex_unlock(&s->lock);return len;
}
static ssize_t native_state_show(struct device *dev,
                                struct device_attribute *attr, char *buf)
{
 struct u30_sgm *s = dev_get_drvdata(dev);
 int len;
 mutex_lock(&s->lock);
 len = sysfs_emit(buf, "hw_address=%02x legacy_id_result=%d data_writes=%lu watchdog_pings=%lu poll_errors=%lu last_error=%d "
                  "policy=%d type=%d temp_dC=%d band=%d pd_mA=%d iin_uA=%u ichg_uA=%u cv_uV=%u charge=%d behaviour=%d user_iin=%u user_ichg=%u phy_present=%d phy_type=%d\n",
                  s->hw->addr, s->legacy_id_result, s->data_writes,
                  s->watchdog_pings, s->poll_errors, s->last_error,
                  s->policy_on, s->pol_type, s->pol_temp, s->jeita_idx, s->pol_pd_ma,
                  s->pol_iin, s->pol_ichg, s->pol_cv, s->pol_charge, s->behaviour,
                  s->user_iin, s->user_ichg,
                  READ_ONCE(s->usb_phy->chg_state), READ_ONCE(s->usb_phy->chg_type));
 mutex_unlock(&s->lock);return len;
}
static DEVICE_ATTR(register_snapshot, 0400, register_snapshot_show, NULL);
static DEVICE_ATTR(native_state, 0400, native_state_show, NULL);
static struct attribute *sgm_attrs[] = {
 &dev_attr_register_snapshot.attr, &dev_attr_native_state.attr, NULL,
};
ATTRIBUTE_GROUPS(sgm);

static int sgm_probe(struct i2c_client *client)
{
 struct device *dev = &client->dev;
 struct power_supply_config psy_cfg = { };
 struct regulator_config reg_cfg = { };
 struct regulator_init_data *init;
 struct regulator_dev *vbus;
 struct device_node *node;
 struct u30_sgm *s;
 int id, again, ret;

 if (!of_machine_is_compatible("sprd,ums9632") || client->addr != 0x6b ||
     of_alias_get_id(client->adapter->dev.of_node, "i2c") != 6)
  return -ENODEV;
 if (!i2c_check_functionality(client->adapter, I2C_FUNC_SMBUS_BYTE_DATA))
  return -EOPNOTSUPP;
 s = devm_kzalloc(dev, sizeof(*s), GFP_KERNEL);
 if (!s) return -ENOMEM;
 s->dev = dev;mutex_init(&s->lock);INIT_DELAYED_WORK(&s->poll, sgm_poll);
 INIT_WORK(&s->source_work, sgm_source_work);
 s->usb_phy = devm_usb_get_phy(dev, USB_PHY_TYPE_USB2);
 if (IS_ERR(s->usb_phy)) {
  ret = PTR_ERR(s->usb_phy);
  return dev_err_probe(dev, ret == -ENODEV ? -EPROBE_DEFER : ret,
                       "USB2 BC1.2 provider is not ready\n");
 }
 /* Fixed legacy DT ID read is also an observable negative-address control.
  * It does not select the eventual hardware backend or write any data. */
 s->legacy_id_result = i2c_smbus_read_byte_data(client, SGM_ID);
 s->hw = devm_i2c_new_dummy_device(dev, client->adapter, 0x1a);
 if (IS_ERR(s->hw)) return dev_err_probe(dev, PTR_ERR(s->hw), "cannot reserve real SGM address\n");
 id = sgm_read(s, SGM_ID);again = sgm_read(s, SGM_ID);
 if (id < 0 || again < 0) return dev_err_probe(dev, id < 0 ? id : again, "cannot read real SGM ID\n");
 if ((id & 0x7c) || (id & 0x7f) != (again & 0x7f))
  return dev_err_probe(dev, -ENODEV, "unexpected SGM part/revision\n");
 ret = sgm_read(s, SGM_STATUS);if (ret < 0) return ret;s->last_status = ret;
 i2c_set_clientdata(client, s);

 s->psy_desc.name = "u30pro-charger-master";
 s->psy_desc.type = POWER_SUPPLY_TYPE_USB;
 s->psy_desc.usb_types = BIT(POWER_SUPPLY_USB_TYPE_UNKNOWN) |
                       BIT(POWER_SUPPLY_USB_TYPE_SDP) | BIT(POWER_SUPPLY_USB_TYPE_DCP) |
                       BIT(POWER_SUPPLY_USB_TYPE_CDP);
 s->psy_desc.properties = sgm_properties;
 s->psy_desc.num_properties = ARRAY_SIZE(sgm_properties);
 s->psy_desc.get_property = sgm_get_property;
 s->psy_desc.set_property = sgm_set_property;
 s->psy_desc.property_is_writeable = sgm_property_is_writeable;
 s->psy_desc.charge_behaviours = BIT(POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO) |
                                 BIT(POWER_SUPPLY_CHARGE_BEHAVIOUR_INHIBIT_CHARGE);
 s->behaviour = POWER_SUPPLY_CHARGE_BEHAVIOUR_AUTO;
 s->jeita_idx = -1;
 ret = sgm_read(s, 0x04);
 if (ret < 0) return ret;
 s->boot_vreg = ret;		/* the firmware's charge voltage is the ceiling */
 sgm_load_tables(s);
 psy_cfg.drv_data = s;psy_cfg.of_node = dev->of_node;
 s->psy = devm_power_supply_register(dev, &s->psy_desc, &psy_cfg);
 if (IS_ERR(s->psy)) return dev_err_probe(dev, PTR_ERR(s->psy), "power_supply registration failed\n");

 node = of_get_child_by_name(dev->of_node, "otg-vbus");
 if (!node) return dev_err_probe(dev, -EINVAL, "missing original OTG regulator child\n");
 s->vbus_desc.name = "u30pro-sgm41513-otg";
 s->vbus_desc.owner = THIS_MODULE;s->vbus_desc.type = REGULATOR_VOLTAGE;
 s->vbus_desc.ops = &sgm_vbus_ops;s->vbus_desc.n_voltages = ARRAY_SIZE(sgm_boost_uv);
 s->vbus_desc.volt_table = sgm_boost_uv;
 init = of_get_regulator_init_data(dev, node, &s->vbus_desc);
 if (!init) { of_node_put(node);return -EINVAL; }
 init->constraints.valid_ops_mask |= REGULATOR_CHANGE_STATUS;
 reg_cfg.dev = dev;reg_cfg.driver_data = s;reg_cfg.of_node = node;reg_cfg.init_data = init;
 vbus = devm_regulator_register(dev, &s->vbus_desc, &reg_cfg);
 of_node_put(node);
 if (IS_ERR(vbus)) return dev_err_probe(dev, PTR_ERR(vbus), "real OTG regulator registration failed\n");
 ret = devm_add_action_or_reset(dev, sgm_cancel_poll, s);
 if (ret) return ret;
 s->usb_nb.notifier_call = sgm_source_notify;
 ret = usb_register_notifier(s->usb_phy, &s->usb_nb);
 if (ret) return ret;
 /* LIFO devres: unregister first, cancel both workers before psy/PHY release. */
 ret = devm_add_action_or_reset(dev, sgm_unregister_source, s);
 if (ret) return ret;
 schedule_work(&s->source_work);
 schedule_delayed_work(&s->poll, 5 * HZ);
 dev_info(dev, "native SGM41513 hw=1a id=%02x legacy6b=%d; charge policy %s (boot VREG %02x)\n",
          id, s->legacy_id_result, s->policy_on ? "active" : "OFF (no battery tables)", s->boot_vreg);
 return 0;
}
static void sgm_shutdown(struct i2c_client *client)
{
 struct u30_sgm *s = i2c_get_clientdata(client);
 if (s) sgm_cancel_poll(s);
}
static const struct of_device_id sgm_match[] = {
 { .compatible = "ti,bq2560x_chg" }, { }
};
MODULE_DEVICE_TABLE(of, sgm_match);
static struct i2c_driver sgm_driver = {
 .driver = { .name = "u30pro-sgm41513", .of_match_table = sgm_match,
             .dev_groups = sgm_groups },
 .probe = sgm_probe, .shutdown = sgm_shutdown,
};
module_i2c_driver(sgm_driver);
MODULE_DESCRIPTION("U30 Pro native master SGM41513 power supply and hardware OTG regulator");
MODULE_LICENSE("GPL");
