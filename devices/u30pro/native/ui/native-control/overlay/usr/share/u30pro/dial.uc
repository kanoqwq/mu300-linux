/* Dial settings of the built-in baseband: /etc/config/u30pro_modem, section "dial" (the internet context) and sections "pdn" (additional
 * APNs).  u30pro-celld reads them itself, at start and on its method reload (ubus call u30pro.cell reload); the credentials are never
 * returned by any rpc method. */
import { cursor } from 'uci';
import { connect } from 'ubus';

function text_ok(s, max) {
	return type(s) == 'string' && length(s) <= max && !match(s, /[^ -~]/) && index(s, '"') < 0 && index(s, '\\') < 0;
}

/* returns an error string or null */
function validate(c) {
	if (type(c.apn) != 'string' || !match(c.apn, /^[A-Za-z0-9_.-]{1,63}$/)) return 'The APN may contain only letters, digits, dots, underscores and hyphens (up to 63 characters)';
	if (c.auth != 'none' && c.auth != 'pap' && c.auth != 'chap') return 'Authentication must be none / PAP / CHAP';
	if (c.pdp != 'ipv4' && c.pdp != 'ipv4v6') return 'IP type must be IPv4 or IPv4/IPv6';
	if (!text_ok(c.username || '', 40)) return 'The username contains disallowed characters (no quotes or backslashes, up to 40 characters)';
	if (!text_ok(c.password || '', 40)) return 'The password contains disallowed characters (no quotes or backslashes, up to 40 characters)';
	if (c.auth != 'none' && !length(c.username)) return 'A username is required when using PAP/CHAP';
	return null;
}

function load() {
	let u = cursor();
	return {
		apn: u.get('u30pro_modem', 'dial', 'apn') || 'cmhk',
		auth: u.get('u30pro_modem', 'dial', 'auth') || 'none',
		username: u.get('u30pro_modem', 'dial', 'username') || '',
		password: u.get('u30pro_modem', 'dial', 'password') || '',
		pdp: u.get('u30pro_modem', 'dial', 'pdp') || 'ipv4v6'
	};
}

function save(c) {
	let u = cursor();
	if (!u.get('u30pro_modem', 'dial')) u.set('u30pro_modem', 'dial', 'dial');
	for (let k in ['apn', 'auth', 'username', 'password', 'pdp']) u.set('u30pro_modem', 'dial', k, c[k] ?? '');
	/* libuci cannot commit under the qemu-user test harness (I/O error); the offline tests set this variable and read the saved changes back */
	if (getenv('U30PRO_TEST_NO_COMMIT')) return u.save('u30pro_modem') === true;
	return u.commit('u30pro_modem') === true;
}

/* a method of u30pro-celld (status, reload, reconnect, ...) */
function celld(cmd) {
	let bus = connect(null, 10);
	let r = bus ? bus.call('u30pro.cell', cmd, {}) : null;
	return type(r) == 'object' ? r : { error: bus ? (bus.error() || 'celld not running') : 'cannot connect to ubus' };
}

/* Additional APNs (multiple PDN): sections "pdn" of /etc/config/u30pro_modem, one per context (CID 2-8, data on sipa_eth<cid-1>).
 * u30pro-celld dials each enabled one; it is the netifd interface cell_<name> (protocol u30cell, no default route, its DNS servers not
 * used by the resolver) in the firewall zone wan.  The internet context (section "dial", CID 1) is not affected by any of them. */
const PDN_MAX = 7;

function pdn_validate(list) {
	if (type(list) != 'array') return 'Invalid list of additional APNs';
	if (length(list) > PDN_MAX) return 'At most 7 additional APNs';
	let names = {}, cids = {};
	for (let p in list) {
		if (type(p) != 'object') return 'Invalid list of additional APNs';
		if (type(p.name) != 'string' || !match(p.name, /^[a-z0-9_]{1,10}$/))
			return 'The name of an additional APN may contain only lower-case letters, digits and underscores (up to 10 characters)';
		if (names[p.name]) return sprintf('Additional APN %s: %s', p.name, 'The name is used twice');
		names[p.name] = true;
		if (type(p.cid) != 'int' || p.cid < 2 || p.cid > 8) return sprintf('Additional APN %s: %s', p.name, 'The context number must be 2 to 8');
		if (cids[p.cid]) return sprintf('Additional APN %s: %s', p.name, 'The context number is used twice');
		cids[p.cid] = true;
		let e = validate(p);
		if (e) return sprintf('Additional APN %s: %s', p.name, e);
	}
	return null;
}

function pdn_load() {
	let u = cursor(), r = [];
	u.foreach('u30pro_modem', 'pdn', function(s) {
		let cid = +(s.cid ?? 0);
		push(r, { name: s['.name'], enabled: s.enabled != '0', cid: type(cid) == 'int' ? cid : 0, apn: s.apn || '', auth: s.auth || 'none',
			username: s.username || '', password: s.password || '', pdp: s.pdp || 'ipv4' });
	});
	return sort(r, (a, b) => a.cid - b.cid);
}

function pdn_save(list) {
	let u = cursor();
	let old = [];
	u.foreach('u30pro_modem', 'pdn', function(s) { push(old, s['.name']); });
	for (let n in old) u.delete('u30pro_modem', n);
	for (let p in list) {
		u.set('u30pro_modem', p.name, 'pdn');
		u.set('u30pro_modem', p.name, 'enabled', p.enabled ? '1' : '0');
		u.set('u30pro_modem', p.name, 'cid', '' + p.cid);
		for (let k in ['apn', 'auth', 'username', 'password', 'pdp']) u.set('u30pro_modem', p.name, k, p[k] ?? '');
	}
	if (getenv('U30PRO_TEST_NO_COMMIT')) return u.save('u30pro_modem') === true;
	return u.commit('u30pro_modem') === true;
}

/* netifd interfaces cell_<name> and their place in the firewall zone wan; returns true when the configuration changed */
function sync_network(list) {
	let u = cursor(), want = {}, net = false, fw = false;
	for (let p in list) if (p.enabled) want['cell_' + p.name] = p;
	let stale = [];
	u.foreach('network', 'interface', function(s) {
		if (index(s['.name'], 'cell_') == 0 && s.proto == 'u30cell' && !want[s['.name']]) push(stale, s['.name']);
	});
	for (let n in stale) { u.delete('network', n); net = true; }
	for (let n, p in want) {
		let dev = 'sipa_eth' + (p.cid - 1);
		if (u.get('network', n) != 'interface') {
			/* a new interface: no default route, the carrier's DNS servers of this APN stay out of the resolver; both can be changed */
			u.set('network', n, 'interface');
			u.set('network', n, 'defaultroute', '0');
			u.set('network', n, 'peerdns', '0');
			u.set('network', n, 'metric', '' + (200 + p.cid));
			net = true;
		}
		for (let k, v in { proto: 'u30cell', device: dev, extra: '1' })
			if (u.get('network', n, k) != v) { u.set('network', n, k, v); net = true; }
	}
	if (u.get('firewall', 'wan') == 'zone') {
		let nets = u.get('firewall', 'wan', 'network') || [];
		if (type(nets) == 'string') nets = split(nets, /[ \t]+/);
		let keep = filter(nets, (n) => index(n, 'cell_') != 0 || want[n]);
		for (let n in sort(keys(want))) if (index(keep, n) < 0) push(keep, n);
		if (join(' ', keep) != join(' ', nets)) { u.set('firewall', 'wan', 'network', keep); fw = true; }
	}
	if (getenv('U30PRO_TEST_NO_COMMIT')) return net || fw;
	if (net) u.commit('network');
	if (fw) u.commit('firewall');
	if (net || fw) system('/sbin/reload_config >/dev/null 2>&1');
	return net || fw;
}

/* start-up helper: the interfaces of the stored additional APNs; an invalid set is left out entirely */
function sync_network_from_uci() {
	let list = pdn_load();
	if (pdn_validate(list)) list = [];
	return sync_network(list);
}

export { validate, load, save, celld, pdn_validate, pdn_load, pdn_save, sync_network, sync_network_from_uci };
