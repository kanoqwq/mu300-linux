'use strict';
'require baseclass';

/* The building blocks of the U30 Pro pages, in the design language of the U30 Pro theme (luci-theme-u30pro: its overview page):
 *   card    a section card: an icon and a title on the first row, buttons on its right, the body, a note under it
 *   tiles   a grid of value tiles: an icon and a label, a large value with its unit, one line under it (or a meter)
 *   kv      a list of label / value rows, the label in the second colour
 *   chip    a small rounded status mark (ok, warn, bad, accent or plain)
 *   meter   a thin bar 0..100 %
 *   sw      a switch (a check box underneath)
 *   seg     a segmented control for two to four choices
 * The look is in kit.css (loaded once by the first page that uses the kit); its colours are the theme's --u-* tokens, with plain
 * fallbacks when another theme is active.  Icons: Lucide 0.469.0 (https://lucide.dev, ISC licence; the same set as the theme). */
var ICONS = {
	'battery': '<rect width="16" height="10" x="2" y="7" rx="2" ry="2" /><line x1="22" x2="22" y1="11" y2="13" />',
	'battery-charging': '<path d="M15 7h1a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-2" /><path d="M6 7H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h1" /><path d="m11 7-3 5h4l-3 5" /><line x1="22" x2="22" y1="11" y2="13" />',
	'zap': '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z" />',
	'plug': '<path d="M12 22v-5" /><path d="M9 8V2" /><path d="M15 8V2" /><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />',
	'thermometer': '<path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z" />',
	'gauge': '<path d="m12 14 4-4" /><path d="M3.34 19a10 10 0 1 1 17.32 0" />',
	'activity': '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />',
	'monitor': '<rect width="20" height="14" x="2" y="3" rx="2" /><line x1="8" x2="16" y1="21" y2="21" /><line x1="12" x2="12" y1="17" y2="21" />',
	'smartphone': '<rect width="14" height="20" x="5" y="2" rx="2" ry="2" /><path d="M12 18h.01" />',
	'lock': '<rect width="18" height="11" x="3" y="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />',
	'lock-open': '<rect width="18" height="11" x="3" y="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 9.9-1" />',
	'hand': '<path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2" /><path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2" /><path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8" /><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />',
	'rotate-cw': '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" />',
	'keyboard': '<path d="M10 8h.01" /><path d="M12 12h.01" /><path d="M14 8h.01" /><path d="M16 12h.01" /><path d="M18 8h.01" /><path d="M6 8h.01" /><path d="M7 16h10" /><path d="M8 12h.01" /><rect width="20" height="16" x="2" y="4" rx="2" />',
	'cpu': '<rect width="16" height="16" x="4" y="4" rx="2" /><rect width="6" height="6" x="9" y="9" rx="1" /><path d="M15 2v2" /><path d="M15 20v2" /><path d="M2 15h2" /><path d="M2 9h2" /><path d="M20 15h2" /><path d="M20 9h2" /><path d="M9 2v2" /><path d="M9 20v2" />',
	'hard-drive': '<line x1="22" x2="2" y1="12" y2="12" /><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" /><line x1="6" x2="6.01" y1="16" y2="16" /><line x1="10" x2="10.01" y1="16" y2="16" />',
	'database': '<ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5V19A9 3 0 0 0 21 19V5" /><path d="M3 12A9 3 0 0 0 21 12" />',
	'package': '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z" /><path d="M12 22V12" /><path d="m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7" /><path d="m7.5 4.27 9 5.15" />',
	'shield': '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />',
	'shield-check': '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" /><path d="m9 12 2 2 4-4" />',
	'wifi': '<path d="M12 20h.01" /><path d="M2 8.82a15 15 0 0 1 20 0" /><path d="M5 12.859a10 10 0 0 1 14 0" /><path d="M8.5 16.429a5 5 0 0 1 7 0" />',
	'radio-tower': '<path d="M4.9 16.1C1 12.2 1 5.8 4.9 1.9" /><path d="M7.8 4.7a6.14 6.14 0 0 0-.8 7.5" /><circle cx="12" cy="9" r="2" /><path d="M16.2 4.8c2 2 2.26 5.11.8 7.47" /><path d="M19.1 1.9a9.96 9.96 0 0 1 0 14.1" /><path d="M9.5 18h5" /><path d="m8 22 4-11 4 11" />',
	'signal': '<path d="M2 20h.01" /><path d="M7 20v-4" /><path d="M12 20v-8" /><path d="M17 20V8" /><path d="M22 4v16" />',
	'antenna': '<path d="M2 12 7 2" /><path d="m7 12 5-10" /><path d="m12 12 5-10" /><path d="m17 12 5-10" /><path d="M4.5 7h15" /><path d="M12 16v6" />',
	'message-square': '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />',
	'globe': '<circle cx="12" cy="12" r="10" /><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /><path d="M2 12h20" />',
	'image': '<rect width="18" height="18" x="3" y="3" rx="2" ry="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />',
	'upload': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" x2="12" y1="3" y2="15" />',
	'download': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" x2="12" y1="15" y2="3" />',
	'refresh-cw': '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" />',
	'power': '<path d="M12 2v10" /><path d="M18.4 6.6a9 9 0 1 1-12.77.04" />',
	'save': '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" /><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7" /><path d="M7 3v4a1 1 0 0 0 1 1h7" />',
	'sliders-horizontal': '<line x1="21" x2="14" y1="4" y2="4" /><line x1="10" x2="3" y1="4" y2="4" /><line x1="21" x2="12" y1="12" y2="12" /><line x1="8" x2="3" y1="12" y2="12" /><line x1="21" x2="16" y1="20" y2="20" /><line x1="12" x2="3" y1="20" y2="20" /><line x1="14" x2="14" y1="2" y2="6" /><line x1="8" x2="8" y1="10" y2="14" /><line x1="16" x2="16" y1="18" y2="22" />',
	'timer': '<line x1="10" x2="14" y1="2" y2="2" /><line x1="12" x2="15" y1="14" y2="11" /><circle cx="12" cy="14" r="8" />',
	'clock': '<circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />',
	'info': '<circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" />',
	'triangle-alert': '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" /><path d="M12 9v4" /><path d="M12 17h.01" />',
	'circle-check': '<circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" />',
	'layers': '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z" /><path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12" /><path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17" />',
	'memory-stick': '<path d="M6 19v-3" /><path d="M10 19v-3" /><path d="M14 19v-3" /><path d="M18 19v-3" /><path d="M8 11V9" /><path d="M16 11V9" /><path d="M12 11V9" /><path d="M2 15h20" /><path d="M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.1a2 2 0 0 0 0 3.837V17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5.1a2 2 0 0 0 0-3.837Z" />',
	'microchip': '<path d="M18 12h2" /><path d="M18 16h2" /><path d="M18 20h2" /><path d="M18 4h2" /><path d="M18 8h2" /><path d="M4 12h2" /><path d="M4 16h2" /><path d="M4 20h2" /><path d="M4 4h2" /><path d="M4 8h2" /><path d="M8 2a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2h-1.5c-.276 0-.494.227-.562.495a2 2 0 0 1-3.876 0C9.994 2.227 9.776 2 9.5 2z" />',
	'search': '<circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />',
	'terminal': '<polyline points="4 17 10 11 4 5" /><line x1="12" x2="20" y1="19" y2="19" />',
	'list': '<path d="M3 12h.01" /><path d="M3 18h.01" /><path d="M3 6h.01" /><path d="M8 12h13" /><path d="M8 18h13" /><path d="M8 6h13" />',
	'file-text': '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /><path d="M10 9H8" /><path d="M16 13H8" /><path d="M16 17H8" />',
	'flame': '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" />',
	'usb': '<circle cx="10" cy="7" r="1" /><circle cx="4" cy="20" r="1" /><path d="M4.7 19.3 19 5" /><path d="m21 3-3 1 2 2Z" /><path d="M9.26 7.68 5 12l2 5" /><path d="m10 14 5 2 3.5-3.5" /><path d="m18 12 1-1 1 1-1 1Z" />',
	'router': '<rect width="20" height="8" x="2" y="14" rx="2" /><path d="M6.01 18H6" /><path d="M10.01 18H10" /><path d="M15 10v4" /><path d="M17.84 7.17a4 4 0 0 0-5.66 0" /><path d="M20.66 4.34a8 8 0 0 0-11.31 0" />',
	'network': '<rect x="16" y="16" width="6" height="6" rx="1" /><rect x="2" y="16" width="6" height="6" rx="1" /><rect x="9" y="2" width="6" height="6" rx="1" /><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3" /><path d="M12 12V8" />',
	'arrow-down-up': '<path d="m3 16 4 4 4-4" /><path d="M7 20V4" /><path d="m21 8-4-4-4 4" /><path d="M17 4v16" />',
	'calendar': '<path d="M8 2v4" /><path d="M16 2v4" /><rect width="18" height="18" x="3" y="4" rx="2" /><path d="M3 10h18" />',
	'sun': '<circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" />',
	'eye': '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" /><circle cx="12" cy="12" r="3" />',
	'hash': '<line x1="4" x2="20" y1="9" y2="9" /><line x1="4" x2="20" y1="15" y2="15" /><line x1="10" x2="8" y1="3" y2="21" /><line x1="16" x2="14" y1="3" y2="21" />',
	'send': '<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" /><path d="m21.854 2.147-10.94 10.939" />',
	'trash-2': '<path d="M3 6h18" /><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" /><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" /><line x1="10" x2="10" y1="11" y2="17" /><line x1="14" x2="14" y1="11" y2="17" />',
	'play': '<polygon points="6 3 20 12 6 21 6 3" />',
	'square': '<rect width="18" height="18" x="3" y="3" rx="2" />'
};

var loaded = false;
function stylesheet() {
	if (loaded || typeof document === 'undefined' || !window.L)
		return;
	loaded = true;
	var v = (L.env && L.env.resource_version) ? '?v=' + L.env.resource_version : '';
	document.head.appendChild(E('link', { 'rel': 'stylesheet', 'href': L.resource('u30pro/kit.css') + v }));
}

function nodes(x) {
	return (x === null || x === undefined || x === '') ? [] : (Array.isArray(x) ? x : [ x ]);
}

return baseclass.extend({
	__init__: function() {
		stylesheet();
	},

	icon: function(name, cls) {
		var s = E('span', { 'class': 'k-ic' + (cls ? ' ' + cls : ''), 'aria-hidden': 'true' });
		s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
			(ICONS[name] || '') + '</svg>';
		return s;
	},

	/* the page: its title, a description, then the cards */
	page: function(title, descr, children) {
		return E('div', { 'class': 'cbi-map k-page' }, [ E('h2', {}, title), descr ? E('div', { 'class': 'cbi-map-descr' }, descr) : '' ].concat(nodes(children)));
	},

	/* o: {title, icon, body, help, actions: [buttons], cls} */
	card: function(o) {
		var head = o.title ? E('div', { 'class': 'k-card-h' }, [ o.icon ? this.icon(o.icon) : '', E('h3', {}, o.title),
			o.actions ? E('div', { 'class': 'k-card-act' }, nodes(o.actions)) : '' ]) : '';
		return E('div', { 'class': 'cbi-section k-card' + (o.cls ? ' ' + o.cls : '') }, [ head ].concat(nodes(o.body),
			o.help ? [ E('div', { 'class': 'k-note' }, o.help) ] : []));
	},

	/* one tile. o: {icon, label, value, unit, meta, metaIcon, tone, meter, meterTone, chip, wide, click} */
	tile: function(o) {
		var val = E('div', { 'class': 'k-val' + (o.tone ? ' ' + o.tone : '') }, [ o.value === null || o.value === undefined || o.value === '' ? '—' : String(o.value),
			o.unit && o.value !== null && o.value !== undefined && o.value !== '' ? E('small', {}, o.unit) : '' ]);
		var meta = o.meter !== undefined && o.meter !== null ? this.meter(o.meter, o.meterTone) :
			E('div', { 'class': 'k-meta' }, [ o.metaIcon ? this.icon(o.metaIcon) : '', E('span', {}, nodes(o.meta).length ? nodes(o.meta) : ' ') ]);
		var t = E('div', { 'class': 'k-tile' + (o.wide ? ' wide' : '') + (o.click ? ' tap' : '') }, [
			E('div', { 'class': 'k-tile-h' }, [ o.icon ? this.icon(o.icon) : '', E('span', { 'class': 'k-tile-l' }, o.label), o.chip || '' ]),
			val, meta ]);
		if (o.click) {
			t.setAttribute('role', 'button');
			t.setAttribute('tabindex', '0');
			t.addEventListener('click', o.click);
		}
		return t;
	},

	/* the grid follows the number of tiles: up to four in a row, two rows of two (or three) where the page is narrower */
	tiles: function(list, cls) {
		var self = this, items = list.filter(Boolean), n = Math.min(items.length, 6);
		return E('div', { 'class': 'k-tiles n' + n + (cls ? ' ' + cls : ''), 'style': '--k-n:' + (n === 6 ? 3 : n) }, items.map(function(o) { return o.nodeType ? o : self.tile(o); }));
	},

	/* rows: [ [label, value], ... ]; a value may be an element; a row with a third item true spans the value across */
	kv: function(rows) {
		return E('div', { 'class': 'k-kv' }, rows.filter(Boolean).map(function(r) {
			return E('div', { 'class': 'k-kv-r' }, [ E('div', { 'class': 'k-kv-l' }, r[0]), E('div', { 'class': 'k-kv-v' }, nodes(r[1]).length ? nodes(r[1]) : '—') ]);
		}));
	},

	chip: function(text, tone, icon) {
		return E('span', { 'class': 'k-chip' + (tone ? ' ' + tone : '') }, [ icon ? this.icon(icon) : (tone ? E('i') : ''), text ]);
	},

	meter: function(pct, tone) {
		var p = typeof pct === 'number' && isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0;
		return E('div', { 'class': 'k-meter' }, E('i', { 'class': tone || '', 'style': 'width:' + p.toFixed(1) + '%' }));
	},

	/* a switch: {el, input}; the input is a check box (checked, disabled, change events) */
	sw: function(checked) {
		var input = E('input', { 'type': 'checkbox' });
		input.checked = !!checked;
		return { el: E('label', { 'class': 'k-sw' }, [ input, E('i') ]), input: input };
	},

	/* a segmented control over choices [ [value, label], ... ]: {el, value(), set(v), onchange} */
	seg: function(choices, value) {
		var self = { value: String(value), onchange: null }, btns = [];
		var el = E('div', { 'class': 'k-seg', 'role': 'radiogroup' });
		choices.forEach(function(c) {
			var b = E('button', { 'type': 'button', 'role': 'radio', 'data-v': String(c[0]) }, c[1]);
			b.addEventListener('click', function(ev) {
				ev.preventDefault();
				if (b.disabled) return;
				self.set(String(c[0]));
				if (self.onchange) self.onchange(self.value);
			});
			btns.push(b);
			el.appendChild(b);
		});
		self.el = el;
		self.set = function(v) {
			self.value = String(v);
			btns.forEach(function(b) {
				var on = b.getAttribute('data-v') === self.value;
				b.className = on ? 'on' : '';
				b.setAttribute('aria-checked', on ? 'true' : 'false');
			});
		};
		self.disable = function(d) { btns.forEach(function(b) { b.disabled = !!d; }); };
		self.set(value);
		return self;
	},

	/* the row of buttons at the end of a card */
	actions: function(children) {
		return E('div', { 'class': 'k-actions' }, nodes(children));
	},

	/* a temperature, a level or a quality as a tone: thresholds [warn, bad] going up (or down when the second is smaller) */
	tone: function(v, warn, bad) {
		if (typeof v !== 'number' || !isFinite(v))
			return '';
		if (bad > warn)
			return v >= bad ? 'bad' : (v >= warn ? 'warn' : 'ok');
		return v <= bad ? 'bad' : (v <= warn ? 'warn' : 'ok');
	}
});
