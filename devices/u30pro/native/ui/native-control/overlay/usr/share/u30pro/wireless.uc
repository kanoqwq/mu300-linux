/* The Wi-Fi of the U30 Pro in the standard OpenWrt way: /etc/config/wireless, applied by netifd (wifi-scripts) with wpad.
 *   radio0          the AIC8800 radio
 *   default_radio0  the hotspot: mode ap, ifname wlan0, network lan
 *   a mode sta interface (normally "wwan", ifname wlan1, network wwan): the Wi-Fi relay, the router as a client of another network
 * State comes from ubus (network.wireless, hostapd.<ifname>, iwinfo); a change is committed and applied with "network reload".
 * Used by the screen feed, the device page and the home key. */
import { readfile } from 'fs';
import { cursor } from 'uci';
import { connect } from 'ubus';

const RADIO = 'radio0';

function ap_section(u) {
	let name = null;
	u.foreach('wireless', 'wifi-iface', (s) => { if (!name && s.mode == 'ap') name = s['.name']; });
	return name;
}

function sta_section(u) {
	let name = null;
	u.foreach('wireless', 'wifi-iface', (s) => { if (!name && s.mode == 'sta') name = s['.name']; });
	return name;
}

/* Each function below closes the ubus connection it opens: a connection stays open until disconnect(), also when nothing refers to
 * it any more (only a gc() run would close it), and the long-running screen feed calls them every few seconds. */

/* netifd's view of radio0: up, and the interface name of each section */
function radio_status(bus) {
	let st = bus.call('network.wireless', 'status', {});
	let r = st ? st[RADIO] : null, ifnames = {};
	for (let i in (r?.interfaces ?? [])) ifnames[i.section] = i.ifname;
	return { up: r?.up === true, disabled: r?.disabled === true, ifnames: ifnames };
}

function apply() {
	let bus = connect();
	if (!bus) return false;
	bus.call('network', 'reload', {});
	let ok = !bus.error();
	bus.disconnect();
	return ok;
}

/* the hotspot: settings, and while it runs its channel and clients (with names and addresses from the DHCP leases) */
function hotspot(with_clients) {
	let u = cursor(), sec = ap_section(u);
	let w = { configured: sec != null, section: sec, enabled: false, active: false, ssid: '', key: '', encryption: 'none',
		band: u.get('wireless', RADIO, 'band') == '2g' ? '2.4 GHz' : '5 GHz', channel: int(u.get('wireless', RADIO, 'channel') || 0) || 0,
		ifname: 'wlan0', clients: [] };
	if (!sec) return w;
	w.ssid = u.get('wireless', sec, 'ssid') || '';
	w.encryption = u.get('wireless', sec, 'encryption') || 'none';
	w.key = w.encryption == 'none' ? '' : (u.get('wireless', sec, 'key') || '');
	w.enabled = u.get('wireless', sec, 'disabled') != '1' && u.get('wireless', RADIO, 'disabled') != '1';
	let bus = connect();
	if (!bus) return w;
	let rs = radio_status(bus);
	if (rs.ifnames[sec]) w.ifname = rs.ifnames[sec];
	let info = bus.call('iwinfo', 'info', { device: w.ifname });
	w.active = rs.up && info != null && info.mode == 'Master';
	let cl = w.active && with_clients ? (bus.call('hostapd.' + w.ifname, 'get_clients', {})?.clients ?? {}) : {};
	bus.disconnect();
	if (w.active) {
		if (info.channel) w.channel = info.channel;   /* the channel really in use */
		if (info.frequency) w.band = info.frequency < 3000 ? '2.4 GHz' : '5 GHz';
	}
	if (w.active && with_clients) {
		let leases = {};
		for (let f in ['/tmp/dhcp.leases']) {
			for (let l in split(readfile(f) || '', '\n')) {
				let p = split(trim(l), ' ');
				if (length(p) >= 4) leases[lc(p[1])] = { ip: p[2], name: p[3] == '*' ? '' : p[3] };
			}
		}
		for (let mac in cl) {
			let c = cl[mac], lease = leases[lc(mac)] || {};
			push(w.clients, { mac: lc(mac), name: lease.name || sprintf('Device %s', substr(lc(mac), 12)), ip: lease.ip || '',
				rssi: c.signal ?? 0 });
		}
	}
	return w;
}

/* the relay: state 0 off, 1 searching or connecting, 2 connected with an address */
function relay() {
	let u = cursor(), sec = sta_section(u);
	let r = { configured: sec != null && length(u.get('wireless', sec, 'ssid') || '') > 0, section: sec, enabled: false, cfg: '', ssid: '',
		state: 0, network: sec ? (u.get('wireless', sec, 'network') || 'wwan') : 'wwan' };
	if (!sec) return r;
	r.cfg = u.get('wireless', sec, 'ssid') || '';
	r.enabled = u.get('wireless', sec, 'disabled') != '1';
	if (!r.enabled) return r;
	let bus = connect();
	if (!bus) return r;
	let rs = radio_status(bus), ifname = rs.ifnames[sec], info = null, n = null;
	if (rs.up && ifname) {
		info = bus.call('iwinfo', 'info', { device: ifname });
		n = bus.call('network.interface.' + (type(r.network) == 'array' ? r.network[0] : r.network), 'status', {});
	}
	bus.disconnect();
	if (!rs.up || !ifname) return r;
	r.state = 1;
	if (info && info.ssid && info.bssid && info.bssid != '00:00:00:00:00:00') r.ssid = info.ssid;
	if (r.ssid != '' && n && n.up && length(n['ipv4-address'] ?? [])) r.state = 2;
	return r;
}

function set_disabled(sec, disabled) {
	let u = cursor();
	if (!sec) return false;
	if (disabled) u.set('wireless', sec, 'disabled', '1');
	else u.delete('wireless', sec, 'disabled');
	if (!disabled && u.get('wireless', RADIO, 'disabled') == '1') u.delete('wireless', RADIO, 'disabled');
	return u.commit('wireless') === true && apply();
}

/* the hotspot on or off (the screen's switch, the home key); returns the new state or null */
function hotspot_toggle() {
	let h = hotspot(false);
	if (!h.configured) return null;
	let on = !(h.enabled || h.active);
	return set_disabled(h.section, !on) ? on : null;
}

function relay_enable(on) {
	let r = relay();
	if (!r.configured) return null;
	return set_disabled(r.section, !on) ? on : null;
}

export { RADIO, hotspot, relay, hotspot_toggle, relay_enable, apply };
