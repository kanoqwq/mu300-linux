'use strict';
'require baseclass';
'require fs';
'require ui';

/* MU300: WPA3 (SAE) on the hotspot is disabled on this Wi-Fi chip (the U30 Air's firmware SAE offload, mu300-linux
 * docs/U30AIR-SAE.md, FINDINGS 80). openwrt-wifi-wpa2-only switched a hotspot that had it to WPA2-PSK at boot (or
 * switched it off, without a usable password) and left /etc/mu300/wpa3-disabled; this says so on Status > Overview,
 * and on the control panel's home page (which loads this module), until it is dismissed. Nothing is shown without
 * the file. It carries its own English, Turkish and Chinese: the plain OpenWrt image has no catalog of the panel's.
 * (Chinese as \u escapes: the repository keeps CJK text in the catalogs only.) */

var NOTICE = '/etc/mu300/wpa3-disabled';

var TEXT = {
	en: {
		title: 'Wi-Fi security notice',
		migrated: 'WPA3 (SAE) on the hotspot is disabled for security: with it, a client could join without knowing the password (FINDINGS 80). The hotspot was switched to WPA2-PSK (CCMP) with the same password: %s. Devices that joined with WPA3 connect again with the same password.',
		down: 'WPA3 (SAE) on the hotspot is disabled for security (FINDINGS 80), and switching it to WPA2 failed: %s is off, not open. Set a WPA2 password (8-63 characters) under Network > Wireless and enable it.',
		dismiss: 'Dismiss'
	},
	tr: {
		title: 'Wi-Fi güvenlik bildirimi',
		migrated: 'Erişim noktasında WPA3 (SAE) güvenlik nedeniyle kapatıldı: onunla bir istemci parolayı bilmeden bağlanabilirdi (FINDINGS 80). Erişim noktası aynı parolayla WPA2-PSK\'ye (CCMP) geçirildi: %s. WPA3 ile bağlanan cihazlar aynı parolayla yeniden bağlanır.',
		down: 'Erişim noktasında WPA3 (SAE) güvenlik nedeniyle kapatıldı (FINDINGS 80) ve WPA2\'ye geçiş başarısız oldu: %s kapalı, şifresiz açık değil. Ağ > Kablosuz altında bir WPA2 parolası (8-63 karakter) girin ve etkinleştirin.',
		dismiss: 'Kapat'
	},
	zh: {
		title: 'Wi-Fi \u5b89\u5168\u63d0\u793a',
		migrated: '\u51fa\u4e8e\u5b89\u5168\u539f\u56e0\uff0c\u70ed\u70b9\u4e0a\u7684 WPA3\uff08SAE\uff09\u5df2\u88ab\u7981\u7528\uff1a\u542f\u7528\u5b83\u65f6\uff0c\u5ba2\u6237\u7aef\u53ef\u80fd\u65e0\u9700\u77e5\u9053\u5bc6\u7801\u5373\u53ef\u63a5\u5165\uff08FINDINGS 80\uff09\u3002\u70ed\u70b9\u5df2\u5207\u6362\u4e3a WPA2-PSK\uff08CCMP\uff09\uff0c\u5bc6\u7801\u4e0d\u53d8\uff1a%s\u3002\u4e4b\u524d\u901a\u8fc7 WPA3 \u8fde\u63a5\u7684\u8bbe\u5907\u53ef\u7528\u540c\u4e00\u5bc6\u7801\u91cd\u65b0\u8fde\u63a5\u3002',
		down: '\u51fa\u4e8e\u5b89\u5168\u539f\u56e0\uff0c\u70ed\u70b9\u4e0a\u7684 WPA3\uff08SAE\uff09\u5df2\u88ab\u7981\u7528\uff08FINDINGS 80\uff09\uff0c\u4f46\u5207\u6362\u5230 WPA2 \u5931\u8d25\uff1a%s \u5df2\u5173\u95ed\uff0c\u800c\u4e0d\u662f\u5f00\u653e\u3002\u8bf7\u5728\u201c\u7f51\u7edc > \u65e0\u7ebf\u201d\u4e2d\u8bbe\u7f6e WPA2 \u5bc6\u7801\uff088-63 \u4e2a\u5b57\u7b26\uff09\u5e76\u542f\u7528\u5b83\u3002',
		dismiss: '\u77e5\u9053\u4e86'
	}
};

function lang() {
	var l = String((typeof document !== 'undefined' && document.documentElement && document.documentElement.getAttribute('lang')) || 'en').toLowerCase();
	return l.indexOf('zh') === 0 ? 'zh' : l.indexOf('tr') === 0 ? 'tr' : 'en';
}

function text(key) {
	var t = TEXT[lang()];
	return t[key] != null ? t[key] : TEXT.en[key];
}

/* the notice file: state=migrated|down, time=, migrated=SECTIONS, down=SECTIONS (no password in it) */
function parse(content) {
	var o = {};
	String(content || '').split('\n').forEach(function(line) {
		var i = line.indexOf('=');
		if (i > 0) o[line.substring(0, i)] = line.substring(i + 1);
	});
	return (o.state === 'migrated' || o.state === 'down') ? o : null;
}

return baseclass.extend({
	title: text('title'),

	load: function() {
		return L.resolveDefault(fs.read(NOTICE), null).then(parse);
	},

	render: function(n) {
		if (!n)
			return null;
		var box = E('div', { 'class': 'alert-message ' + (n.state === 'down' ? 'error' : 'warning'), 'id': 'mu300-wpa2-notice' });
		var msg = n.state === 'down' ? text('down').replace('%s', n.down || '-') : text('migrated').replace('%s', n.migrated || '-');
		box.appendChild(E('p', {}, [ msg + (n.time ? ' (' + n.time + ')' : '') ]));
		box.appendChild(E('button', {
			'class': 'btn cbi-button',
			'click': ui.createHandlerFn(this, function() {
				return L.resolveDefault(fs.remove(NOTICE), null).then(function() {
					box.style.display = 'none';
					/* on Status > Overview the section around it (title and all) goes too */
					var sec = box.parentNode && box.parentNode.parentNode;
					if (sec && sec.classList && sec.classList.contains('cbi-section'))
						sec.style.display = 'none';
				});
			})
		}, [ text('dismiss') ]));
		return box;
	}
});
