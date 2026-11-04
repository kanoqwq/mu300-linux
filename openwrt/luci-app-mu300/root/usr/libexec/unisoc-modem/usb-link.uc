#!/usr/bin/ucode
// Negotiated data links only. Root-hub/UDC maximum speed is not a cable speed.
import { readfile, realpath, glob } from 'fs';

let role = ARGV[0], role_file = ARGV[1], gadgets = ARGV[2], bus = ARGV[3];
let udcs = ARGV[4], roles = ARGV[5];
function text(path) { return trim(readfile(path) ?? ''); }
function parent(path) { return replace(path, /\/[^/]+$/, ''); }
function name(path) { return replace(path, /^.*\//, ''); }
function result(state, links) { print(sprintf('%J\n', { state, links: links ?? [] })); }

let bound = [];
for (let gadget in glob(gadgets + '/*')) {
    let id = text(gadget + '/UDC');
    if (length(id) && !match(id, /[/\s]/) && index(bound, id) < 0) push(bound, id);
}
let controllers = glob(udcs + '/*');
let udc = length(bound) == 1 ? udcs + '/' + bound[0] :
          length(bound) == 0 && length(controllers) == 1 ? controllers[0] : null;

if (role == 'device') {
    if (!udc) { result('unavailable'); exit(0); }
    let state = text(udc + '/state');
    if (index(['not-attached', 'not attached'], state) >= 0) {
        result('disconnected'); exit(0);
    }
    if (index(['attached', 'powered', 'default', 'addressed', 'configured', 'suspended'], state) < 0) {
        result('unavailable'); exit(0);
    }
    let speed = text(udc + '/current_speed');
    let rates = { 'low-speed': 1.5, 'full-speed': 12, 'high-speed': 480, 'super-speed': 5000 };
    // The UDC enum cannot distinguish the different SuperSpeedPlus lane rates.
    if (rates[speed] || speed == 'super-speed-plus')
        result('connected', [{ mbps: rates[speed], speed }]);
    else result('unavailable');
    exit(0);
}
if (role != 'host') { result('unavailable'); exit(0); }

// Identify this dual-role controller before looking at external peers. Never
// report a peripheral on an unrelated USB host (or a root hub's capability).
let controller = null, rp = realpath(role_file);
if (rp && match(rp, /\/usb_role\/[^/]+\/role$/)) controller = parent(parent(parent(rp)));
// E5-family DWC3 exports its policy directly on the platform controller.
if (rp && match(rp, /\/usb_role_policy$/)) controller = parent(rp);
if (!controller && udc) {
    let up = realpath(udc);
    if (up && match(up, /\/udc\/[^/]+$/)) controller = parent(parent(up));
}
if (!controller) {
    let switches = glob(roles + '/*');
    if (length(switches) == 1) {
        let sp = realpath(switches[0]);
        if (sp && match(sp, /\/usb_role\/[^/]+$/)) controller = parent(parent(sp));
    }
}
if (!controller) { result('unavailable'); exit(0); }
let roots = [];
for (let root in glob(bus + '/usb*')) {
    if (!match(name(root), /^usb[0-9]+$/)) continue;
    let path = realpath(root);
    if (path && index(path, controller + '/') == 0) push(roots, path);
}
if (!length(roots)) { result('unavailable'); exit(0); }
let links = [], seen = [], peers = 0, unknown = false;
for (let entry in glob(bus + '/*')) {
    if (!match(name(entry), /^[0-9]+-[0-9]+$/)) continue;
    let path = realpath(entry);
    if (!path || index(roots, parent(path)) < 0) continue;
    peers++;
    let rate = text(entry + '/speed');
    if (!match(rate, /^[0-9]+(\.[0-9]+)?$/) || +rate <= 0 || +rate > 100000) {
        unknown = true; continue;
    }
    if (index(seen, +rate) >= 0) continue;
    push(seen, +rate); push(links, { mbps: +rate });
}
// A mixed USB2/USB3 hub may expose both actual upstream links. Downstream
// children and interfaces are intentionally excluded from this port's rate.
if (length(links)) result('connected', links);
else result(peers || unknown ? 'unavailable' : 'disconnected');
