// U30 Air charge policy. The charger driver owns registers, VBUS and OTG.
import * as fs from 'fs';
import * as uci from 'uci';

const ROOT = getenv('MU300_CHARGE_SYS') || '/sys/class/power_supply';
const STATE = getenv('MU300_CHARGE_STATE') || '/run/mu300-charge.json';
const BOARD = getenv('MU300_CHARGE_DEVICE') || trim(fs.readfile('/run/mu300/device') || '');
const cfg = uci.cursor(getenv('MU300_CHARGE_CONFIG') || '/etc/config');
const PKG = 'mu300-charge';
const NATIVE = BOARD == 'u30pro';
const BAT = ROOT + (NATIVE ? '/u30pro-battery' : '/sc27xx-fgu');
const CHG = ROOT + (NATIVE ? '/u30pro-charger-master' : '/bq256xx-charger');
let saved = false;

function read(path) { return trim(fs.readfile(path) || ''); }
function integer(v, low, high) {
    if (type(v) != 'int' && type(v) != 'string') die('charge_invalid');
    if (!match('' + v, /^[0-9]{1,3}$/) || int(v) < low || int(v) > high) die('charge_invalid');
    return int(v);
}
function settings() {
    return { enabled: integer(cfg.get(PKG, 'main', 'enabled') || '0', 0, 1),
        start: integer(cfg.get(PKG, 'main', 'start') || '70', 20, 95),
        stop: integer(cfg.get(PKG, 'main', 'stop') || '80', 50, 100),
        power_on: integer(cfg.get(PKG, 'main', 'power_on') ?? '0', 0, 1),
        paused: integer(cfg.get(PKG, 'main', 'paused') || '0', 0, 1),
        full_once: integer(cfg.get(PKG, 'main', 'full_once') || '0', 0, 1) };
}
function store(values) {
    for (let key, value in values)
        if (!cfg.set(PKG, 'main', key, '' + value)) die('charge_save_failed');
    if (!cfg.commit(PKG)) die('charge_save_failed');
    saved = true;
}
function previous() {
    try { return json(fs.readfile(STATE)) || {}; } catch (e) { return {}; }
}
function supported() { return BOARD in ['u30air','u30pro']; }
function available() {
    return supported() && read(BAT + '/present') == '1' &&
        !!match(read(BAT + '/capacity'), /^(100|[0-9]{1,2})$/) &&
        !!(fs.stat(CHG + (NATIVE ? '/charge_behaviour' : '/charge_type'))?.mode & 128);
}
function snapshot(c) {
    let old = previous(), valid = available();
    let cap = valid ? int(read(BAT + '/capacity')) : null;
    let current = valid ? read(BAT + '/current_now') : '';
    return { ok: true, supported: supported(), available: valid, capacity: cap,
        current_ma: match(current, /^-?[0-9]+$/) ? int(current) / 1000 : null,
        status: valid ? read(CHG + '/status') : '', online: valid && read(CHG + '/online') == '1',
        enabled: !!c.enabled, start: c.start, stop: c.stop, power_on: !!c.power_on, full_once: !!c.full_once,
        paused: !!c.paused, stopped: !!old.stopped, reason: old.reason || '', pending: !!old.pending };
}
function remember(state) {
    const temp = STATE + '.new';
    let text = sprintf('%J\n', state);
    if (fs.writefile(temp, text) != length(text) || !fs.rename(temp, STATE)) {
        fs.unlink(temp); die('charge_state_failed');
    }
}
function apply(c) {
    if (!available()) die('charge_unavailable');
    let s = snapshot(c), old = previous();
    if (c.full_once && !c.paused && s.capacity == 100 && s.status == 'Full') {
        store({full_once: 0}); c.full_once = 0;
    }
    let reason = c.paused ? 'manual' : '';
    if (!reason && c.enabled && !c.full_once &&
        (s.capacity >= c.stop || (s.capacity > c.start && old.reason == 'limit' && old.stopped))) reason = 'limit';
    let stopped = length(reason) > 0;
    let owned = !!(c.enabled || c.paused || c.full_once);
    // Never enable battery charging while the port is sourcing OTG power.
    // Leave the previous pause owned until the next sink/charger event.
    let source = !!match(read(CHG + '/usb_type'), /\[ACA\]/);
    const TYPEC = getenv('MU300_CHARGE_TYPEC') || '/sys/class/typec';
    for (let path in fs.glob(TYPEC + '/port*/power_role') || [])
        if (match(read(path), /\[source\]/) || read(path) == 'source') source = true;
    if (!stopped && source && old.stopped) {
        remember({owned: true, stopped: true, reason: old.reason, pending: true});
        return snapshot(c);
    }
    // Default-disabled policy does not take ownership of the charger.
    if (stopped || owned || old.owned) {
        if (old.stopped !== stopped || !old.owned || old.pending) {
            let text = NATIVE ? (stopped ? 'inhibit-charge' : 'auto') : (stopped ? 'N/A' : 'Fast');
            if (!stopped && source) {
                remember({owned, stopped: false, reason: '', pending: false});
                return snapshot(c);
            }
            if (fs.writefile(CHG + (NATIVE ? '/charge_behaviour' : '/charge_type'), text) != length(text)) die('charge_apply_failed');
        }
    }
    remember({owned, stopped, reason, pending: false});
    return snapshot(c);
}

try {
    let action = ARGV[0] || 'get';
    let c = settings();
    if (c.start >= c.stop) die('charge_threshold_order');
    if (action == 'get') print(sprintf('%J\n', snapshot(c)));
    else {
        if (!available()) die('charge_unavailable');
        if (action == 'configure') {
            let data = json(fs.readfile('/dev/stdin') || '{}');
            let next = {enabled: integer(data?.enabled, 0, 1), start: integer(data?.start, 20, 95),
                stop: integer(data?.stop, 50, 100), power_on: c.power_on, paused: 0, full_once: 0};
            if (data?.power_on != null && data.power_on != '')
                next.power_on = integer(data.power_on, 0, 1);
            if (next.start >= next.stop) die('charge_threshold_order');
            store(next); c = next;
        } else if (action == 'pause' || action == 'resume') {
            store({paused: action == 'pause' ? 1 : 0, full_once: 0});
            c.paused = action == 'pause' ? 1 : 0; c.full_once = 0;
        } else if (action == 'full_once') {
            store({paused: 0, full_once: 1}); c.paused = 0; c.full_once = 1;
        } else if (action != 'apply') die('charge_invalid');
        print(sprintf('%J\n', apply(c)));
    }
} catch (error) {
    let message = '' + (error?.message || error);
    let code = match(message, /charge_[a-z_]+/)?.[0] || 'charge_unavailable';
    print(sprintf('%J\n', {ok: false, saved, error: code}));
}
