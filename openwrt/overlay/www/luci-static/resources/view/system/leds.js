'use strict';
'require view';
'require uci';
'require rpc';
'require form';
'require fs';

/* Keep LuCI's normal LED actions; add controls for the two physical F50
 * lamps, whose channels are driven by led-status rather than kernel triggers.
 * Based on luci-mod-system's 25.12 LED view. */
const callLeds = rpc.declare({
	object: 'luci',
	method: 'getLEDs',
	expect: { '': {} }
});

/* This device page is copied into the rootfs independently of the MU300 LuCI
 * plugin, so its small catalog must not depend on the plugin being installed.
 * Upstream trigger plugins still use LuCI's own _() translations. */
const labels = {
	'LED Configuration': ['LED 配置', 'LED yapılandırması'],
	'Configure physical status lamps and optional LED trigger actions.': ['配置物理状态灯与可选的 LED 触发动作。', 'Fiziksel durum ışıklarını ve isteğe bağlı LED tetikleyicilerini yapılandırın.'],
	'Physical status lamps': ['物理状态灯', 'Fiziksel durum ışıkları'],
    'Battery lamp': ['电池灯', 'Pil ışığı'],
    'Battery lamp brightness': ['电池灯亮度', 'Pil ışığı parlaklığı'],
    'LED timeout (seconds)': ['状态灯自动熄灭（秒）', 'LED kapanma süresi (saniye)'],
    'While charging lamps stay on. Otherwise 0 keeps them on; press the power key to show them again.': ['充电时状态灯保持点亮；未充电时 0 表示常亮，熄灭后短按电源键可再次点亮。', 'Şarjda ışıklar açık kalır. Diğer durumlarda 0 sürekli açık tutar; yeniden göstermek için güç tuşuna basın.'],
    'U30 Air: network blue on 4G, white on 5G, red without data. Wi-Fi blue on 2.4 GHz, white on 5 GHz. These lamps support on/off only.': ['U30 Air：网络灯 4G 蓝色、5G 白色、无数据红色；Wi-Fi 灯 2.4 GHz 蓝色、5 GHz 白色。这两组灯仅支持开关。', 'U30 Air: 4G mavi, 5G beyaz, veri yoksa kırmızı. Wi-Fi: 2.4 GHz mavi, 5 GHz beyaz. Bu ışıklar yalnızca açılıp kapatılabilir.'],
    'Below 50%: blue; from 50%: white. Breathing while charging; steady white when full.': ['低于 50% 蓝色，50% 起白色；充电时对应颜色呼吸，充满后白色常亮。', '%50 altında mavi, %50 ve üzerinde beyaz. Şarjda aynı renk yumuşakça parlar; dolunca sabit beyaz yanar.'],
	'Signal lamp (LTE / 5G / no service)': ['信号灯（4G / 5G / 无信号）', 'Sinyal ışığı (4G / 5G / servis yok)'],
	'Signal lamp brightness': ['信号灯亮度', 'Sinyal ışığı parlaklığı'],
	'Wi-Fi hotspot lamp': ['Wi-Fi 热点灯', 'Wi-Fi erişim noktası ışığı'],
	'Wi-Fi lamp brightness': ['Wi-Fi 灯亮度', 'Wi-Fi ışığı parlaklığı'],
	'NET1 lamp (SIM 2)': ['NET1 灯（SIM2）', 'NET1 ışığı (SIM 2)'],
	'NET2 lamp (SIM 1)': ['NET2 灯（SIM1）', 'NET2 ışığı (SIM 1)'],
	'NET1 brightness': ['NET1 亮度', 'NET1 parlaklığı'],
	'NET2 brightness': ['NET2 亮度', 'NET2 parlaklığı'],
	'NET1 shows SIM 2; NET2 shows SIM 1. Wi-Fi: 5 GHz white, 2.4 GHz blue.': ['NET1 显示 SIM2，NET2 显示 SIM1；Wi-Fi：5 GHz 白色、2.4 GHz 蓝色。', 'NET1 SIM 2 durumunu, NET2 SIM 1 durumunu gösterir. Wi-Fi: 5 GHz beyaz, 2.4 GHz mavi.'],
	'0–255; 0 is dark. LTE stays blue, 5G white, and no service red.': ['0–255；0 为熄灭。4G 仍为蓝色、5G 为白色、无信号为红色。', '0–255; 0 ışığı kapatır. 4G mavi, 5G beyaz, servis yoksa kırmızı kalır.'],
	'0 is dark. Hardware maximum: ': ['0 为熄灭。硬件最大值：', '0 ışığı kapatır. Donanım üst sınırı: '],
	'Add LED action': ['添加 LED 动作', 'LED eylemi ekle'],
	'Name': ['名称', 'Ad'],
	'LED Name': ['LED 名称', 'LED adı'],
	'Trigger': ['触发器', 'Tetikleyici'],
	'Interval': ['间隔', 'Aralık'],
	'milliseconds': ['毫秒', 'milisaniye']
};

function label(key) {
	let lang = (L.env && L.env.lang) || document.documentElement.lang || navigator.language || 'en';
	if (lang === 'auto') lang = document.documentElement.lang || navigator.language || 'en';
	lang = String(lang).toLowerCase().replace('_', '-');
	const entry = labels[key];
	if (entry && lang.indexOf('zh') === 0) return entry[0];
	if (entry && lang.indexOf('tr') === 0) return entry[1];
	return _(key);
}

return view.extend({
	load() {
		return Promise.all([
			callLeds(),
			L.resolveDefault(fs.list('/www' + L.resource('view/system/led-trigger')), [])
		]).then(function([leds, plugins]) {
			const tasks = [];

			for (let p of plugins) {
				const m = p.name.match(/^(.+)\.js$/);
				if (p.type != 'file' || m == null)
					continue;

				tasks.push(L.require('view.system.led-trigger.' + m[1]).then(L.bind(function(name) {
					return L.resolveDefault(L.require('view.system.led-trigger.' + name)).then(function(form) {
						return { name: name, form: form };
					});
				}, this, m[1])));
			}

			return Promise.all(tasks).then(function(plugins) {
				return [leds, plugins];
			});
		});
	},

	render([leds, plugins]) {
		let m, s, o;
		const awRgb = !!(leds.aw_led && leds.red && leds.green && leds.blue);
		const v50 = !!leds['v50:net1:white'];
		const u30air = !!(leds['zte-ldo0'] && leds['zte-ldo1'] && leds['zte-ldo2'] && leds.net_blue);
		const f50 = !v50 && !u30air && !!(leds['sc27xx:blue'] && leds['keyboard-backlight']);
		let wifiMax = leds['keyboard-backlight'] && Number(leds['keyboard-backlight'].max_brightness);
		if (!Number.isInteger(wifiMax) || wifiMax < 1 || wifiMax > 255) wifiMax = 255;
		const triggers = [];
		for (let k in leds)
			for (let t of leds[k].triggers)
				triggers.push(t);

		m = new form.Map('system',
			label('LED Configuration'),
			label('Configure physical status lamps and optional LED trigger actions.'));

		/* One RGB signal lamp and one separate Wi-Fi lamp. A single sysfs LED
		 * action cannot disable all RGB channels, while led-status would also
		 * overwrite its brightness at the next state change. */
		s = m.section(form.NamedSection, 'mu300_leds', 'mu300_leds', label('Physical status lamps'));
		s.anonymous = true;
		s.addremove = false;
		if (u30air) s.description = label('U30 Air: network blue on 4G, white on 5G, red without data. Wi-Fi blue on 2.4 GHz, white on 5 GHz. These lamps support on/off only.');
		if (v50) s.description = label('NET1 shows SIM 2; NET2 shows SIM 1. Wi-Fi: 5 GHz white, 2.4 GHz blue.');
		o = s.option(form.Flag, 'signal', label(v50 ? 'NET1 lamp (SIM 2)' : 'Signal lamp (LTE / 5G / no service)'));
		o.default = '1';
		o.rmempty = false;
		if (!u30air) {
		o = s.option(form.Value, 'signal_brightness', label(v50 ? 'NET1 brightness' : 'Signal lamp brightness'),
			label('0–255; 0 is dark. LTE stays blue, 5G white, and no service red.'));
		o.datatype = 'range(0,255)';
		o.default = '255';
		o.rmempty = false;
		}

		if (v50) {
			o = s.option(form.Flag, 'signal2', label('NET2 lamp (SIM 1)'));
			o.default = '1'; o.rmempty = false;
			o = s.option(form.Value, 'signal2_brightness', label('NET2 brightness'));
			o.datatype = 'range(0,255)'; o.default = '255'; o.rmempty = false;
			wifiMax = 255; // normalized brightness, scaled to each physical channel
		}
		if (!awRgb || v50) {
		o = s.option(form.Flag, 'wifi', label('Wi-Fi hotspot lamp'));
		o.default = '1';
		o.rmempty = false;
		if (!u30air) {
		o = s.option(form.Value, 'wifi_brightness', label('Wi-Fi lamp brightness'),
			label('0 is dark. Hardware maximum: ') + wifiMax);
		o.datatype = 'range(0,' + wifiMax + ')';
		o.default = String(Math.min(f50 ? 55 : 127, wifiMax));
		o.rmempty = false;
		}

		}

        if (u30air) {
            o = s.option(form.Flag, 'battery', label('Battery lamp'), label('Below 50%: blue; from 50%: white. Breathing while charging; steady white when full.'));
            o.default = '1'; o.rmempty = false;
            o = s.option(form.Value, 'battery_brightness', label('Battery lamp brightness'));
            o.datatype = 'range(0,255)'; o.default = '255'; o.rmempty = false;
            o = s.option(form.Value, 'timeout', label('LED timeout (seconds)'), label('While charging lamps stay on. Otherwise 0 keeps them on; press the power key to show them again.'));
            o.datatype = 'range(0,86400)'; o.default = '60'; o.rmempty = false;
        }

		s = m.section(form.GridSection, 'led', '');
		s.anonymous = true;
		s.addremove = true;
		s.sortable = true;
		s.addbtntitle = label('Add LED action');
		s.nodescriptions = true;

		s.option(form.Value, 'name', label('Name'));
		o = s.option(form.ListValue, 'sysfs', label('LED Name'));
		Object.keys(leds).sort().forEach(function(name) {
			if (u30air && /^(sc27xx:|zte-ldo|net_|wifi_|pwr_green$|keyboard-backlight$)/.test(name)) return;
			o.value(name);
		});
		o = s.option(form.ListValue, 'trigger', label('Trigger'));
		for (let plugin of plugins) {
			if (plugin.form.kernel == false) {
				o.value(plugin.name, plugin.form.trigger);
			} else if (triggers.indexOf(plugin.name) >= 0) {
				o.value(plugin.name, plugin.form.trigger);
			}
		}
		o.onchange = function(ev, section, value) {
			const nes = this.map.findElement('id', 'cbid.system.%s.trigger'.format(section)).nextElementSibling;
			for (let plugin of plugins) {
				if (plugin.name === value && nes)
					nes.innerText = plugin.form.description || '';
			}
		};
		o.load = function(section_id) {
			const trigger = uci.get('system', section_id, 'trigger');
			for (let plugin of plugins) {
				if (plugin.name === trigger)
					this.description = plugin.form.description || ' ';
			}
			return trigger;
		};
		s.addModalOptions = function(s) {
			for (let plugin of plugins)
				plugin.form.addFormOptions(s);

			const opts = s.getOption();
			const removeIfNoneActive = function(original_remove_fn, section_id) {
				let isAnyActive = false;
				for (let optname in opts) {
					if (opts[optname].ucioption != this.ucioption)
						continue;
					if (!opts[optname].isActive(section_id))
						continue;
					isAnyActive = true;
					break;
				}
				if (!isAnyActive)
					original_remove_fn.call(this, section_id);
			};

			for (let optname in opts) {
				if (!opts[optname].ucioption || optname == opts[optname].ucioption)
					continue;
				opts[optname].remove = removeIfNoneActive.bind(opts[optname], opts[optname].remove);
			}
		};
		o = s.option(form.Value, 'interval', label('Interval'), label('milliseconds'));
		o.placeholder = '50';

		return m.render();
	}
});
