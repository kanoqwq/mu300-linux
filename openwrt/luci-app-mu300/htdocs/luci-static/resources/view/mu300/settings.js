'use strict';
'require form';
'require uci';
'require mu300.refresh as R';
'require view';
'require mu300.common as M';
'require mu300.product-form as P';

return view.extend({
	load: function() { return Promise.all([uci.load('unisoc_modem'), P.load()]); },
	render: function(data) {
		M.localizeMenu();
		var t = M.translate;
		var m = new form.Map('unisoc_modem', t('适配设置'),
			t('这里定义插件与当前紫光 OpenWrt 的边界。修改后无需改动看板、AT、锁定或短信页面。'));
		var s = m.section(form.NamedSection, 'main', 'core', t('平台适配'));
		s.addremove = false;
		if (!(data && data[1] && data[1].supported === false)) {
			var product = s.option(form.DummyValue, '_product_form', P.title());
			product.renderWidget = function() { return P.render(data && data[1], {embedded:true}); };
			product.write = function() {};
			product.remove = function() {};
		}

		var o = s.option(form.ListValue, 'at_backend', t('AT 后端'));
		o.value('auto', t('自动检测'));
		o.value('mu300', 'mu300-at');
		o.value('atinout', t('atinout + 串口'));
		o.value('custom', t('自定义适配器'));

		o = s.option(form.ListValue, 'sim_slots', t('SIM 卡槽数量'));
		o.value('1', t('单卡')); o.value('2', t('双卡')); o.default = '1'; o.rmempty = false;
		o.description = t('仅在硬件与 AT 适配器支持独立双卡寻址时开启双卡；查看卡槽不会切换上网卡。');
		o.description += ' ' + t('启用双卡后请重启设备，以建立独立的第二卡短信接收通道。');
		o = s.option(form.Value, 'data_sim_command', t('上网卡切换适配器'));
		o.depends('sim_slots', '2'); o.placeholder = '/opt/mu300/bin/mu300-data-sim';
		['0', '1'].forEach(function(slot) {
			var opt=s.option(form.Value, 'sim'+slot+'_device', t('分卡网卡映射')+' · SIM '+(Number(slot)+1));
			opt.depends('sim_slots','2'); opt.datatype='and(string,minlength(1),maxlength(15))';
			opt.description=t('留空使用平台默认映射，不影响已有流量账本。');
		});

		o = s.option(form.Value, 'at_port', t('AT 串口'));
		o.placeholder = '/dev/stty_nr1';
		o.depends('at_backend', 'atinout');

		o = s.option(form.Value, 'at_command', t('自定义 AT 适配器'));
		o.placeholder = '/usr/libexec/my-platform/at';
		o.description = t('可执行文件依次接收超时秒数和完整 AT 命令。它必须与平台拨号程序共享串口锁。');
		o.depends('at_backend', 'custom');

		o = s.option(form.Value, 'sms_command', t('短信适配器'));
		o.placeholder = '/usr/bin/mu300-sms';
		o.description = t('实现 list、show、send、delete、sync 子命令；留空时自动查找 mu300-sms。');

		o = s.option(form.Value, 'sms_pool', t('短信池目录'));
		o.placeholder = '/etc/mu300/sms';
		o.description = t('原生收件、面板和转发共用；更改后重启生效，已有短信不自动搬移。');

		o = s.option(form.Value, 'data_interface', t('蜂窝逻辑接口'));
		o.placeholder = 'wan';
		o = s.option(form.Value, 'data_interface_v6', t('蜂窝 IPv6 接口'));
		o.placeholder = 'wan6';
		o = s.option(form.Value, 'data_device', t('蜂窝网卡'));
		o.placeholder = t('留空则从 netifd 自动获取');
		o = s.option(form.Value, 'lan_device', t('LAN 网桥'));
		o.placeholder = 'br-lan';
		o = s.option(form.Value, 'wifi_device', t('Wi-Fi 网卡'));
		o.placeholder = 'wlan0';
		o = s.option(form.Value, 'wifi_iface', t('热点 AP 配置节'));
		o.placeholder = 'ap0';
		o.datatype = 'uciname';
		o.description = t('留空时匹配 Wi-Fi 网卡；只有一个 AP 时自动选择。多个 AP 无法唯一匹配时必须指定无线配置节名称。');
		o = s.option(form.Value, 'usb_device', t('USB 网卡'));
		o.placeholder = 'usb0';

		o = s.option(form.Value, 'replay_timeout', t('等待 AT 就绪上限（秒）'));
		o.datatype = 'uinteger';
		o.placeholder = '90';
		o = s.option(form.Value, 'home_refresh_interval', t('主页刷新间隔（秒）'));
		o.datatype = 'range(2,60)';
		o.placeholder = '2';
		o.default = '2';
		o.rmempty = false;
		o.cfgvalue = function(section) { return String(R.seconds(uci.get('unisoc_modem', section, 'home_refresh_interval'))); };
		o.description = t('主页状态每 2–60 秒刷新，网速独立每秒刷新；旧设置不足 2 秒时按 2 秒处理。保存后重新进入主页生效。');
		o = s.option(form.Value, 'state_dir', t('持久化状态目录'));
		o.placeholder = '/etc/unisoc-modem/lock-state.d';

		return m.render();
	}
});
