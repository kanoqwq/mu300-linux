'use strict';
/* Wallpaper of the screen (rpcd object u30pro-wallpaper; the screen side is tools/ui/wall.h).
 *   get {}        what is installed and the look settings
 *   image {}      the picture (a live clip's first frame) as base64 for the web pages: the 480x480 web copy when there is one
 *   set {dim, glass, blur, tint, live}   the look: wallpaper dimming 0-90, glass transparency 0-100, glass blur 0-100, glass colour 0-3,
 *                 where a clip plays 0-2 (u30pro_native_control.display wp_dim, glass, glass_blur, glass_tint, wp_live)
 *   install {}    take the files the web page uploaded (/tmp/u30pro-wallpaper.jpg, and for a clip /tmp/u30pro-wallpaper.live; besides them
 *                 the web copies /tmp/u30pro-wallpaper.web.jpg and .web.live at 480x480, which the screen does not use)
 *   remove {}     back to the plain background
 * The browser makes the files (the page cuts the user's picture or video to 240x240); here they are only checked and moved to the data
 * partition, which keeps them across firmware updates.  The screen program looks at the files once a second.  The web copies are sharp on
 * the overview page's picture of the screen (about 200 CSS pixels, twice that on a high-density display); the overview plays the clip
 * from web.live, or from wallpaper.live when it was set before the web copies existed, through cgi-download. */
import { readfile, stat, unlink, rename, mkdir, open } from 'fs';
import { cursor } from 'uci';
import { connect } from 'ubus';

const DIR = '/etc/u30pro/wallpaper', JPG = DIR + '/wallpaper.jpg', LIVE = DIR + '/wallpaper.live';
const WEB_JPG = DIR + '/web.jpg', WEB_LIVE = DIR + '/web.live';
const UP_JPG = '/tmp/u30pro-wallpaper.jpg', UP_LIVE = '/tmp/u30pro-wallpaper.live';
const UP_WEB_JPG = '/tmp/u30pro-wallpaper.web.jpg', UP_WEB_LIVE = '/tmp/u30pro-wallpaper.web.live';
const UPLOADS = [ UP_JPG, UP_LIVE, UP_WEB_JPG, UP_WEB_LIVE ];
const MAX_JPG = 4 * 1024 * 1024, MAX_LIVE = 96 * 1024 * 1024;
const SIZE = 240, WEB = 480;
const KEYS = { dim: [ 'wp_dim', 0, 90, 30 ], glass: [ 'glass', 0, 100, 50 ], blur: [ 'glass_blur', 0, 100, 50 ], tint: [ 'glass_tint', 0, 3, 0 ],
	live: [ 'wp_live', 0, 2, 2 ] };

let uci = cursor();
function out(v) { printf('%J\n', v); }
function fail(s) { out({ error: s }); exit(1); }
function input() { return json(readfile('/dev/stdin') || '{}') || {}; }
function panel_reload() { let u = connect(); if (u) { u.call('u30pro.panel', 'reload', {}); u.disconnect(); } }

function u16(s, i) { return ord(s, i) | ord(s, i + 1) << 8; }
function u32(s, i) { return ord(s, i) | ord(s, i + 1) << 8 | ord(s, i + 2) << 16 | ord(s, i + 3) * 16777216; }

/* the header of a clip of size x size frames and its frame table, walked to the end of the file: {fps, frames} or a reason */
function live_info(path, size) {
	let st = stat(path), f = open(path, 'r');
	if (!st || !f) return 'cannot read the clip';
	if (st.size > MAX_LIVE) { f.close(); return 'the clip is too large'; }
	let h = f.read(16);
	if (length(h) != 16 || substr(h, 0, 4) != 'U3LW' || ord(h, 4) != 1) { f.close(); return 'not a live wallpaper clip'; }
	let fps = ord(h, 5), n = u32(h, 12);
	if (fps < 1 || fps > 30 || u16(h, 6) != size || u16(h, 8) != size || n < 1 || n > 1800) { f.close(); return 'unsupported clip format'; }
	let pos = 16;
	for (let i = 0; i < n; i++) {
		f.seek(pos);
		let l = f.read(4);
		if (length(l) != 4) { f.close(); return 'the clip is cut short'; }
		let len = u32(l, 0);
		if (len < 4 || pos + 4 + len > st.size) { f.close(); return 'the clip is cut short'; }
		f.seek(pos + 4);
		if (substr(f.read(2), 0, 2) != '\xff\xd8') { f.close(); return 'a frame of the clip is not a JPEG'; }
		pos += 4 + len;
	}
	f.close();
	return { fps, frames: n, bytes: st.size };
}

function jpeg_ok(path) {
	let st = stat(path), f = open(path, 'r');
	if (!st || !f) return false;
	let h = f.read(3);
	f.close();
	return st.size > 3 && st.size <= MAX_JPG && h == '\xff\xd8\xff';
}

function settings() {
	let r = uci.get_all('u30pro_native_control', 'display') ?? {}, s = {};
	for (let k in KEYS) {
		let v = r[KEYS[k][0]];
		s[k] = v != null && match(v, /^[0-9]+$/) && +v >= KEYS[k][1] && +v <= KEYS[k][2] ? +v : KEYS[k][3];
	}
	return s;
}

function get() {
	let j = stat(JPG), l = stat(LIVE), r = { picture: !!j, live: false, settings: settings() };
	if (j) { r.picture_bytes = j.size; r.mtime = j.mtime; r.web = !!stat(WEB_JPG); }
	if (l) {
		let i = live_info(LIVE, SIZE);
		if (type(i) == 'object') { r.live = true; r.fps = i.fps; r.frames = i.frames; r.live_bytes = i.bytes; if (l.mtime > (r.mtime ?? 0)) r.mtime = l.mtime; r.web_live = !!stat(WEB_LIVE); }
	}
	return r;
}

/* copy onto the data partition (another file system than /tmp) and swap it in */
function place(src, dst) {
	let tmp = dst + '.new';
	if (system([ 'cp', src, tmp ]) != 0) { unlink(tmp); return false; }
	if (!rename(tmp, dst)) { unlink(tmp); return false; }
	return true;
}

let method = ARGV[0];
if (method == 'get') out(get());
else if (method == 'image') {
	let path = stat(WEB_JPG) ? WEB_JPG : JPG, st = stat(path), data = st && st.size <= MAX_JPG ? readfile(path) : null;
	out(data ? { mtime: st.mtime, type: 'image/jpeg', data: b64enc(data) } : {});
}
else if (method == 'set') {
	let req = input();
	uci.set('u30pro_native_control', 'display', 'display');
	for (let k in KEYS) {
		if (req[k] == null) continue;
		let v = int(req[k]);
		if (v != +req[k] || v < KEYS[k][1] || v > KEYS[k][2]) fail(sprintf('%s must be %d to %d', k, KEYS[k][1], KEYS[k][2]));
		uci.set('u30pro_native_control', 'display', KEYS[k][0], '' + v);
	}
	if (!uci.commit('u30pro_native_control')) fail('Cannot save the settings');
	panel_reload();
	out({ accepted: true, settings: settings() });
}
else if (method == 'install') {
	let drop = () => { for (let f in UPLOADS) unlink(f); };
	if (!jpeg_ok(UP_JPG)) { drop(); fail('The uploaded picture is not a JPEG of at most 4 MB'); }
	let clip = stat(UP_LIVE) ? live_info(UP_LIVE, SIZE) : null;
	if (type(clip) == 'string') { drop(); fail('The uploaded clip was refused: ' + clip); }
	/* the web copies are optional (a page of an older firmware sends none); one that does not check out is left out */
	let web = stat(UP_WEB_JPG) && jpeg_ok(UP_WEB_JPG), web_clip = clip && stat(UP_WEB_LIVE) ? live_info(UP_WEB_LIVE, WEB) : null;
	mkdir('/etc/u30pro', 0755);
	mkdir(DIR, 0755);
	if (!stat(DIR)) { drop(); fail('Cannot prepare wallpaper storage'); }
	/* the web copies go first, so the overview never pairs the new picture with an old copy; then the clip before its picture: the
	 * screen prefers the clip, so it never shows the new picture with the old clip */
	if (!(web && place(UP_WEB_JPG, WEB_JPG))) unlink(WEB_JPG);
	if (!(type(web_clip) == 'object' && place(UP_WEB_LIVE, WEB_LIVE))) unlink(WEB_LIVE);
	if (clip && !place(UP_LIVE, LIVE)) { drop(); fail('Cannot store the clip'); }
	if (!clip) unlink(LIVE);
	if (!place(UP_JPG, JPG)) { drop(); fail('Cannot store the picture'); }
	drop();
	system('sync');
	panel_reload();
	out({ accepted: true, wallpaper: get() });
}
else if (method == 'remove') {
	unlink(LIVE);
	unlink(JPG);
	unlink(WEB_LIVE);
	unlink(WEB_JPG);
	panel_reload();
	out({ accepted: true, wallpaper: get() });
}
else fail('Unknown wallpaper command');
