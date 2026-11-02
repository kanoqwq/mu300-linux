/* First-boot defaults of the Wi-Fi in /etc/config/wireless (run from /etc/uci-defaults at every boot of this RAM root; it only adds what is
 * missing).  Settings of earlier images are taken over: u30pro_native_control.wifi (the hotspot, written by the old Wi-Fi page) and
 * u30pro_native_control.relay (the Wi-Fi relay); both sections are removed afterwards.  Without them the hotspot starts as before: open,
 * "U30Pro-Openwrt", 5 GHz channel 36 with Wi-Fi 6 at 80 MHz (2.4 GHz channel 1 when the command line asks for u30pro.wifi=ap), off when
 * the command line has no u30pro.wifi.
 * Also moves the old private pieces to the stock ones: the relay uplink wwan is a wireless network (no device of its own), and dnsmasq takes
 * its upstream servers from netifd's resolver file. */
import { readfile, writefile, stat } from 'fs';
import { cursor } from 'uci';

/* the radio is found by its phy: the SDIO card address in its device path (mmc2:<rca>) and the driver's MAC address change from boot to
 * boot, so a path or macaddr match (what the stock detection writes) would miss it after the next boot */
const SDIO = 'platform/soc/soc:ap-ahb/22220000.sdio/';
/* libuci cannot add sections to a configuration file that does not exist */
if (!stat('/etc/config/wireless')) writefile('/etc/config/wireless', '');
let u = cursor();
let cmdline = readfile('/proc/cmdline') || '';
let changed = {};

function set(conf, sec, opt, val) {
	if (val == null || val === '') return;
	u.set(conf, sec, opt, '' + val);
	changed[conf] = true;
}

if (u.get('wireless', 'radio0') == null) {
	let w = u.get_all('u30pro_native_control', 'wifi') || {};
	let band = w.band ?? (match(cmdline, /(^|\s)u30pro\.wifi=ap5(\s|$)/) ? '5g' : '2g');
	let five = band == '5g';
	let mode = w.mode ?? (five ? 'ax' : 'n'), width = w.width ?? (five ? '80' : '20');
	let htmode = ({ n: 'HT', ac: 'VHT', ax: 'HE' })[mode] ?? 'HT';
	if (htmode == 'HT' && width == '80') width = '40';
	let enabled = w.enabled != null ? w.enabled == '1' : !!match(cmdline, /(^|\s)u30pro\.wifi=ap(5)?(\s|$)/);

	u.set('wireless', 'radio0', 'wifi-device');
	set('wireless', 'radio0', 'type', 'mac80211');
	set('wireless', 'radio0', 'phy', 'phy0');
	set('wireless', 'radio0', 'band', band);
	set('wireless', 'radio0', 'channel', w.channel ?? (five ? '36' : '1'));
	set('wireless', 'radio0', 'htmode', htmode + width);
	set('wireless', 'radio0', 'country', 'HK');
	set('wireless', 'radio0', 'cell_density', '0');
	/* no 20/40 MHz coexistence scan on 5 GHz: after a boot it left the AP on the secondary channel (40 instead of 36) */
	if (five) set('wireless', 'radio0', 'noscan', '1');
	if (w.txpower != null && w.txpower != 'auto') set('wireless', 'radio0', 'txpower', w.txpower);
	if (w.beacon != null && w.beacon != '100') set('wireless', 'radio0', 'beacon_int', w.beacon);
	if (w.sgi == '0') for (let o in ['short_gi_20', 'short_gi_40', 'short_gi_80']) set('wireless', 'radio0', o, '0');
	if (w.ldpc == '0') set('wireless', 'radio0', 'ldpc', '0');
	if (w.stbc == '0') { set('wireless', 'radio0', 'tx_stbc', '0'); set('wireless', 'radio0', 'rx_stbc', '0'); }

	let enc = ({ open: 'none', wpa2: 'psk2+ccmp', mixed: 'sae-mixed', wpa3: 'sae' })[w.security ?? 'open'] ?? 'none';
	u.set('wireless', 'default_radio0', 'wifi-iface');
	set('wireless', 'default_radio0', 'device', 'radio0');
	set('wireless', 'default_radio0', 'network', 'lan');
	set('wireless', 'default_radio0', 'mode', 'ap');
	set('wireless', 'default_radio0', 'ifname', 'wlan0');
	set('wireless', 'default_radio0', 'ssid', w.ssid ?? 'U30Pro-Openwrt');
	set('wireless', 'default_radio0', 'encryption', enc);
	if (enc != 'none') {
		set('wireless', 'default_radio0', 'key', w.password);
		if (w.pmf != null) set('wireless', 'default_radio0', 'ieee80211w', w.pmf);
	}
	if (w.hidden == '1') set('wireless', 'default_radio0', 'hidden', '1');
	if (w.isolate == '1') set('wireless', 'default_radio0', 'isolate', '1');
	if (w.uapsd == '0') set('wireless', 'default_radio0', 'uapsd', '0');
	if (w.dtim != null && w.dtim != '2') set('wireless', 'default_radio0', 'dtim_period', w.dtim);
	if (w.max_clients != null) set('wireless', 'default_radio0', 'maxassoc', w.max_clients);
	if (!enabled) set('wireless', 'default_radio0', 'disabled', '1');
	changed.wireless = true;
}

/* earlier settings that name the radio by its path: by its phy; radios the stock detection added for a path of another boot go away */
if (index(u.get('wireless', 'radio0', 'path') ?? '', SDIO) == 0) {
	u.delete('wireless', 'radio0', 'path');
	set('wireless', 'radio0', 'phy', 'phy0');
}
let stale = [];
u.foreach('wireless', 'wifi-device', (s) => { if (s['.name'] != 'radio0' && index(s.path ?? '', SDIO) == 0) push(stale, s['.name']); });
u.foreach('wireless', 'wifi-iface', (s) => { if (index(stale, s.device) >= 0) push(stale, s['.name']); });
for (let n in stale) { u.delete('wireless', n); changed.wireless = true; }

/* the machine's own Wi-Fi MAC address (prodnv, copied for the driver by rcS) as the base of the interfaces' addresses: the hotspot uses it
 * unless the relay takes it (LuCI offers it as "Machine MAC" for an interface) */
let machine = lc(trim(readfile('/lib/firmware/wifimac.txt') || ''));
if (match(machine, /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/) && u.get('wireless', 'radio0') == 'wifi-device' && u.get('wireless', 'radio0', 'macaddr_base') != machine)
	set('wireless', 'radio0', 'macaddr_base', machine);

/* the relay: one client interface on the same radio (the driver runs it next to the hotspot, on its own channel) */
let r = u.get_all('u30pro_native_control', 'relay');
if (r && length(r.ssid ?? '') && u.get('wireless', 'wwan') == null) {
	u.set('wireless', 'wwan', 'wifi-iface');
	set('wireless', 'wwan', 'device', 'radio0');
	set('wireless', 'wwan', 'network', 'wwan');
	set('wireless', 'wwan', 'mode', 'sta');
	set('wireless', 'wwan', 'ifname', 'wlan1');
	set('wireless', 'wwan', 'ssid', r.ssid);
	/* WPA3 (SAE) or WPA2-PSK, the access point decides, as before */
	if (length(r.password ?? '')) { set('wireless', 'wwan', 'encryption', 'sae-mixed'); set('wireless', 'wwan', 'key', r.password); }
	else set('wireless', 'wwan', 'encryption', 'none');
	if (r.enabled != '1') set('wireless', 'wwan', 'disabled', '1');
	if (r.prefer == 'cellular') set('network', 'wwan', 'metric', '200');
}
for (let s in ['wifi', 'relay']) {
	if (u.get('u30pro_native_control', s) != null) { u.delete('u30pro_native_control', s); changed.u30pro_native_control = true; }
}

/* wwan gets its device from the wireless interface and comes up with it */
if (u.get('network', 'wwan') == 'interface') {
	if (u.get('network', 'wwan', 'device') == 'wlan1') { u.delete('network', 'wwan', 'device'); changed.network = true; }
	if (u.get('network', 'wwan', 'auto') == '0') { u.delete('network', 'wwan', 'auto'); changed.network = true; }
}

/* dnsmasq: the stock resolver file of netifd (DNS servers of every uplink that is up) instead of the old cellular-only file */
u.foreach('dhcp', 'dnsmasq', (s) => {
	if (s.resolvfile == '/run/u30pro-cellular-resolv.conf') { u.delete('dhcp', s['.name'], 'resolvfile'); changed.dhcp = true; }
});

for (let conf in changed) u.commit(conf);
