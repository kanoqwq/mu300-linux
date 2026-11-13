/* SPDX-License-Identifier: GPL-2.0 */
/* This driver binds only battery-policy boards. Override USB data, never the
 * TCPM power contract, CC termination, current budget or charger regulator.
 * One lock serializes user policy and Type-C events; no second role writer.
 */
static int sc27xx_apply_data_role(struct sc27xx_pd *pd, enum usb_role negotiated,
				enum usb_role *applied)
{
	enum usb_role selected;
	int ret;

	mutex_lock(&pd->role_lock);
	selected = pd->data_override && !READ_ONCE(pd->stopping) ?
		pd->data_override : negotiated;
	ret = usb_role_switch_set_role(pd->role_sw, selected);
	if (!ret) {
		pd->negotiated_role = negotiated;
		*applied = selected;
	}
	mutex_unlock(&pd->role_lock);
	return ret;
}

static int sc27xx_set_data_override(struct sc27xx_pd *pd, enum usb_role mode)
{
	int ret;

	mutex_lock(&pd->role_lock);
	if (READ_ONCE(pd->stopping)) {
		ret = -ESHUTDOWN;
		goto out;
	}
	ret = usb_role_switch_set_role(pd->role_sw,
		mode == USB_ROLE_NONE ? pd->negotiated_role : mode);
	if (!ret)
		pd->data_override = mode;
out:
	mutex_unlock(&pd->role_lock);
	return ret;
}

static ssize_t usb_role_override_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct sc27xx_pd *pd = dev_get_drvdata(dev);
	enum usb_role mode;

	mutex_lock(&pd->role_lock);
	mode = pd->data_override;
	mutex_unlock(&pd->role_lock);
	return sysfs_emit(buf, "%s\n", mode == USB_ROLE_NONE ? "auto" : usb_role_string(mode));
}

static ssize_t usb_role_override_store(struct device *dev,
				      struct device_attribute *attr,
				      const char *buf, size_t count)
{
	struct sc27xx_pd *pd = dev_get_drvdata(dev);
	enum usb_role mode;
	int ret;

	if (sysfs_streq(buf, "auto"))
		mode = USB_ROLE_NONE;
	else if (sysfs_streq(buf, "host"))
		mode = USB_ROLE_HOST;
	else if (sysfs_streq(buf, "device"))
		mode = USB_ROLE_DEVICE;
	else
		return -EINVAL;
	ret = sc27xx_set_data_override(pd, mode);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(usb_role_override);

static ssize_t usb_role_negotiated_show(struct device *dev,
				       struct device_attribute *attr, char *buf)
{
	struct sc27xx_pd *pd = dev_get_drvdata(dev);
	enum usb_role role;

	mutex_lock(&pd->role_lock);
	role = pd->negotiated_role;
	mutex_unlock(&pd->role_lock);
	return sysfs_emit(buf, "%s\n", usb_role_string(role));
}
static DEVICE_ATTR_RO(usb_role_negotiated);

static struct attribute *sc27xx_role_attrs[] = {
	&dev_attr_usb_role_override.attr,
	&dev_attr_usb_role_negotiated.attr,
	NULL,
};
ATTRIBUTE_GROUPS(sc27xx_role);
