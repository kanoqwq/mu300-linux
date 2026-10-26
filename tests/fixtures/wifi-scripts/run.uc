'use strict';
// OpenWrt's wifi-scripts without a radio: what mac80211.sh setup() does before hostapd.setup() (validate, "auto" is
// channel 0), then the hostapd configuration it writes. wifi.common is a copy whose wiphy_info() reads $FAKE_WIPHY
// (test_wifi_channels.py makes it); ubus is a stand-in that reports the country $FAKE_CC.
import * as hostapd from 'wifi.hostapd';
import { validate } from 'wifi.validate';
import * as fs from 'fs';

global.ubus = {
	call: function(obj, method) {
		if (obj == 'network.wireless' && method == 'status')
			return { radio0: { config: { country: getenv('FAKE_CC') } } };
		if (obj == 'hostapd' && method == 'config_set')
			return { pid: 1 };
		return null;
	},
	list: function() { return [ 'hostapd' ]; }
};
global.radio = 'radio0';

let data = json(fs.readfile(getenv('FAKE_DATA')));
validate('device', data.config);
if (data.config.channel == 'auto')
	data.config.channel = 0;
data.config.channel = +data.config.channel;
for (let k, v in data.interfaces)
	validate('iface', v.config);
hostapd.setup(data);
print(fs.readfile(`/var/run/hostapd-${data.phy}.conf`));
