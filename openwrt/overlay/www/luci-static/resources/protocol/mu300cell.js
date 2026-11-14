'use strict';
'require form';
'require network';

/* LuCI needs a handler per protocol or the interface page shows "unsupported protocol" and offers nothing to
   edit - which is what the modem interface looked like, even though /lib/netifd/proto/mu300cell.sh has read an
   apn option all along. The options here are exactly the ones that protocol handler understands. */

return network.registerProtocol('mu300cell', {
	getI18n: function() {
		return _('MU300 cellular');
	},

	getIfname: function() {
		return this._ubus('l3_device') || 'sipa_eth0';
	},

	getOpkgPackage: function() {
		return null;
	},

	/* the modem is reached over AT commands, not by claiming a network device, so there is nothing for the
	   user to pick in the device list */
	isFloating: function() {
		return true;
	},

	isVirtual: function() {
		return true;
	},

	getDevices: function() {
		return null;
	},

	containsDevice: function(ifname) {
		return (network.getIfnameOf(ifname) == this.getIfname());
	},

	renderFormOptions: function(s) {
		var o;

		o = s.taboption('general', form.Value, 'apn', _('APN'),
			_('Leave empty unless the carrier needs a specific one. Empty means the modem keeps the context the SIM already defines, which is what most SIMs expect and what this device has been using.'));
		o.placeholder = _('whatever the SIM defines');

		o = s.taboption('general', form.ListValue, 'pdptype', _('PDP type'),
			_('IPv4 only is what Android asks this modem for, and what has been measured working here. Ask for both only if the carrier requires it.'));
		o.value('IP', _('IPv4 only (default)'));
		o.value('IPV4V6', _('IPv4 and IPv6'));
		o.value('IPV6', _('IPv6'));
		o.default = 'IP';

		o = s.taboption('general', form.Flag, 'peerdns', _('Use DNS servers advertised by peer'));
		o.default = o.enabled;

		o = s.taboption('general', form.DynamicList, 'dns', _('Use custom DNS servers'));
		o.depends('peerdns', '0');
		o.datatype = 'ipaddr';
	}
});
