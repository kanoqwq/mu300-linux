'use strict';
'require form';
'require view';
'require mu300.common as M';

return view.extend({
	render: function() {
		var m = new form.Map('unisoc_modem', _('Adapter settings'),
			_('Configure how this plugin connects to the current Unisoc OpenWrt platform. Changes do not require editing the dashboard, AT, locks or SMS pages.'));
		var s = m.section(form.NamedSection, 'main', 'core', _('Platform adapter'));
		s.addremove = false;

		var o = s.option(form.ListValue, 'at_backend', _('AT backend'));
		o.value('auto', _('Auto-detect'));
		o.value('mu300', 'mu300-at');
		o.value('atinout', _('atinout + serial port'));
		o.value('custom', _('Custom adapter'));

		o = s.option(form.Value, 'at_port', _('AT serial port'));
		o.placeholder = '/dev/stty_nr1';
		o.depends('at_backend', 'atinout');

		o = s.option(form.Value, 'at_command', _('Custom AT adapter'));
		o.placeholder = '/usr/libexec/my-platform/at';
		o.description = _('The executable receives a timeout in seconds and the complete AT command, in that order. It must share the serial lock with the platform dialer.');
		o.depends('at_backend', 'custom');

		o = s.option(form.Value, 'sms_command', _('SMS adapter'));
		o.placeholder = '/usr/bin/mu300-sms';
		o.description = _('Implement the list, show, send, delete and sync subcommands; leave blank to find mu300-sms automatically.');

		o = s.option(form.Value, 'sms_pool', _('SMS pool directory'));
		o.placeholder = '/etc/mu300/sms-pool';

		o = s.option(form.Value, 'data_interface', _('Cellular logical interface'));
		o.placeholder = 'wan';
		o = s.option(form.Value, 'data_interface_v6', _('Cellular IPv6 interface'));
		o.placeholder = 'wan6';
		o = s.option(form.Value, 'data_device', _('Cellular network device'));
		o.placeholder = _('Leave blank to detect from netifd');
		o = s.option(form.Value, 'lan_device', _('LAN bridge'));
		o.placeholder = 'br-lan';
		o = s.option(form.Value, 'wifi_device', _('Wi-Fi device'));
		o.placeholder = 'wlan0';
		o = s.option(form.Value, 'usb_device', _('USB device'));
		o.placeholder = 'usb0';

		o = s.option(form.Value, 'replay_timeout', _('Maximum wait for AT readiness (seconds)'));
		o.datatype = 'uinteger';
		o.placeholder = '90';
		o = s.option(form.Value, 'home_refresh_interval', _('Home dashboard refresh interval (seconds)'));
		o.datatype = 'range(0.5,60)';
		o.placeholder = '1.5';
		o.default = '1.5';
		o.rmempty = false;
		o.description = _('Controls only the home dashboard refresh rate; 0.5–60 seconds. Reopen Home after saving to apply.');
		o = s.option(form.Value, 'state_dir', _('Persistent state directory'));
		o.placeholder = '/etc/unisoc-modem/lock-state.d';

		return m.render();
	}
});
