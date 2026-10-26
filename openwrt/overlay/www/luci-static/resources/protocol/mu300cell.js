'use strict';
'require form';
'require network';
'require rpc';

/* LuCI needs a handler per protocol or the interface page shows "unsupported protocol" and offers nothing to
   edit - which is what the modem interface looked like, even though /lib/netifd/proto/mu300cell.sh has read an
   apn option all along. The options here are exactly the ones that protocol handler understands. */

/* Whether this device has the internal SIM (slot 1) at all is mu300-sim's to say (slot1_supported: F50 boot selection and the positively gated U30 Air/OpenWrt backend). The control panel's local snapshot carries its answer as "sim" (mu300dash sysinfo: uci and files, no AT
   command). Without the panel, or when the call fails, nothing is known and both sources are offered as before. */
var callSysinfo = rpc.declare({ object: 'mu300dash', method: 'sysinfo', expect: { '': {} }, nobatch: true });

function simSupport() {
	return L.resolveDefault(callSysinfo(), null).then(function(info) {
		var sim = info && info.sim;
		return (sim && (sim.slot1_supported === 0 || sim.slot1_supported === 1)) ? sim : null;
	});
}

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

		o = s.taboption('general', form.ListValue, 'sim_slot', _('SIM source'),
			_('Applies after reboot. The internal SIM is available only on devices fitted with one.'));
		o.value('0', _('External SIM (slot 0)'));
		o.value('1', _('Internal SIM (slot 1)'));
		o.default = '0';
		/* A device without slot 1: mu300-sim refuses the selection and every reader uses slot 0, so the page must
		   not offer the internal SIM (issue #194: it was chosen on a U30 Air and the WAN silently stayed on the
		   external card). Where the configuration already asks for it, the entry stays, named for what it is: the
		   page then shows what is saved and why it has no effect, and choosing the external SIM puts it right. */
		o.load = function(section_id) {
			var self = this;
			var cur = form.ListValue.prototype.load.apply(this, arguments);
			return Promise.all([ simSupport(), cur ]).then(function(r) {
				/* the ZTE V50 (FINDINGS 204): each slot is one of the modem's two SIM interfaces, with a choice of
				   cards behind it (Cellular > SIM) */
				if (r[0] && r[0].v50) {
					self.keylist = [];
					self.vallist = [];
					self.value('0', _('SIM1: built-in SIM 1 or the external SIM'));
					self.value('1', _('SIM2: built-in SIM 2 or 3'));
					self.description = _('Applies after reboot. Which card is behind each SIM is chosen in Cellular > SIM.');
				}
				else if (r[0] && !r[0].slot1_supported) {
					self.keylist = [];
					self.vallist = [];
					self.value('0', _('External SIM (slot 0)'));
					if (r[1] == '1')
						self.value('1', _('Internal SIM (slot 1): not supported on this device'));
					self.description = _('The internal SIM (slot 1) is not supported on this device: the external SIM is used.');
				}
				return r[1];
			});
		};

		o = s.taboption('general', form.Value, 'apn_internal', _('Internal SIM APN'),
			_('Use the APN supplied by the internal SIM provider. China Mobile was tested with cmnet.'));
		o.depends('sim_slot', '1');
		o.retain = true;
		o.placeholder = 'cmnet';

		o = s.taboption('general', form.ListValue, 'pdptype_internal', _('Internal SIM PDP type'));
		o.value('IPV4V6', _('IPv4 and IPv6'));
		o.value('IP', _('IPv4 only'));
		o.default = 'IPV4V6';
		o.depends('sim_slot', '1');
		o.retain = true;

		/* data roaming, one setting per card (mobile-data, docs/FINDINGS.md 120): unset is allowed, as every release
		   before it did; the dial and its watchdog read it, so a change applies within half a minute */
		o = s.taboption('general', form.Flag, 'roaming_internal', _('Internal SIM data roaming'),
			_('Off: no mobile data while the SIM is registered on a roaming network (SMS and calls are not affected). Some SIMs report roaming at home (an MVNO, national roaming): those need it on.'));
		o.default = o.enabled;
		o.depends('sim_slot', '1');
		o.retain = true;

		o = s.taboption('general', form.Value, 'apn', _('APN'),
			_('Leave empty unless the carrier needs a specific one. Empty means the modem keeps the context the SIM already defines, which is what most SIMs expect and what this device has been using.'));
		o.placeholder = _('whatever the SIM defines');
		o.depends('sim_slot', '0');
		o.retain = true;

		o = s.taboption('general', form.ListValue, 'pdptype', _('PDP type'),
			_('IPv4 only is what Android asks this modem for, and what has been measured working here. Ask for both only if the carrier requires it.'));
		o.value('IP', _('IPv4 only (default)'));
		o.value('IPV4V6', _('IPv4 and IPv6'));
		o.default = 'IP';
		o.depends('sim_slot', '0');
		o.retain = true;

		/* data roaming, one setting per card (mobile-data, docs/FINDINGS.md 120): unset is allowed, as every release
		   before it did; the dial and its watchdog read it, so a change applies within half a minute */
		o = s.taboption('general', form.Flag, 'roaming', _('Data roaming'),
			_('Off: no mobile data while the SIM is registered on a roaming network (SMS and calls are not affected). Some SIMs report roaming at home (an MVNO, national roaming): those need it on.'));
		o.default = o.enabled;
		o.depends('sim_slot', '0');
		o.retain = true;

		/* an APN that asks for credentials (a private APN, some MVNOs): mobile-data's define_context sends them */
		o = s.taboption('general', form.Value, 'username', _('APN user name'),
			_('Only for an APN that asks for one. Needs an APN above.'));
		o.depends('sim_slot', '0');
		o.retain = true;
		o.validate = function(section_id, value) {
			return (value.length <= 64 && /^[ -~]*$/.test(value) && !/["\\]/.test(value)) ? true :
				_('At most 64 printable ASCII characters, without quotes or backslashes.');
		};

		o = s.taboption('general', form.Value, 'password', _('APN password'));
		o.password = true;
		o.depends('sim_slot', '0');
		o.retain = true;
		o.validate = function(section_id, value) {
			return (value.length <= 64 && /^[ -~]*$/.test(value) && !/["\\]/.test(value)) ? true :
				_('At most 64 printable ASCII characters, without quotes or backslashes.');
		};

		o = s.taboption('general', form.ListValue, 'auth', _('APN authentication'),
			_('How the user name and password are presented. Automatic lets the network choose between PAP and CHAP.'));
		o.value('', _('Automatic (PAP or CHAP when a user name is set)'));
		o.value('pap', 'PAP');
		o.value('chap', 'CHAP');
		o.value('both', _('PAP or CHAP'));
		o.value('none', _('None'));
		o.depends('sim_slot', '0');
		o.retain = true;

		o = s.taboption('general', form.Flag, 'peerdns', _('Use DNS servers advertised by peer'));
		o.default = o.enabled;

		o = s.taboption('general', form.DynamicList, 'dns', _('Use custom DNS servers'));
		o.depends('peerdns', '0');
		o.datatype = 'ipaddr';
	}
});
