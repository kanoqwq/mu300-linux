/* SMS PDU codec and message handling for the U30 Pro baseband (AT+CMGF=0 PDU mode).
 *   encode_submit(number, text, ref)  -> { ok, parts: [{ len, pdu }], alphabet }   (len = TPDU octets for AT+CMGS)
 *   decode_pdu(hex)                   -> { type, address, ts, text, ... }
 *   parse_cmgl(lines)                 -> [{ index, stat, pdu }]
 *   merge(list)                       -> messages with concatenated parts joined
 * The device never reads or writes the subscriber number (no +CNUM here); the PDUs only carry the
 * correspondent's address. */
import * as m from '/usr/share/u30pro/modem.uc';

/* GSM 03.38 default alphabet as Unicode code points, index = septet value */
const GSM7 = [
	0x40, 0xA3, 0x24, 0xA5, 0xE8, 0xE9, 0xF9, 0xEC, 0xF2, 0xC7, 0x0A, 0xD8, 0xF8, 0x0D, 0xC5, 0xE5,
	0x394, 0x5F, 0x3A6, 0x393, 0x39B, 0x3A9, 0x3A0, 0x3A8, 0x3A3, 0x398, 0x39E, 0x1B, 0xC6, 0xE6, 0xDF, 0xC9,
	0x20, 0x21, 0x22, 0x23, 0xA4, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F,
	0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E, 0x3F,
	0xA1, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x4B, 0x4C, 0x4D, 0x4E, 0x4F,
	0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0xC4, 0xD6, 0xD1, 0xDC, 0xA7,
	0xBF, 0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F,
	0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0xE4, 0xF6, 0xF1, 0xFC, 0xE0
];
/* escape table (ESC + septet) */
const GSM7_EXT = { '10': 0x0C, '20': 0x5E, '40': 0x7B, '41': 0x7D, '47': 0x5C, '60': 0x5B, '61': 0x7E, '62': 0x5D, '64': 0x7C, '101': 0x20AC };

let gsm7_rev = {};
let gsm7_ext_rev = {};
for (let i = 0; i < length(GSM7); i++) if (i != 0x1B) gsm7_rev[GSM7[i]] = i;
for (let k in keys(GSM7_EXT)) gsm7_ext_rev[GSM7_EXT[k]] = int(k);

function hex2(n) { return sprintf('%02X', n & 0xFF); }

/* UTF-8 text -> array of code points (null on invalid input) */
function utf8_to_cps(s) {
	let r = [], i = 0, n = length(s);
	while (i < n) {
		let b = ord(s, i), c, extra;
		if (b < 0x80) { c = b; extra = 0; }
		else if (b >= 0xC2 && b < 0xE0) { c = b & 0x1F; extra = 1; }
		else if (b >= 0xE0 && b < 0xF0) { c = b & 0x0F; extra = 2; }
		else if (b >= 0xF0 && b < 0xF5) { c = b & 0x07; extra = 3; }
		else return null;
		if (extra && i + extra >= n) return null;
		for (let k = 1; k <= extra; k++) {
			let cb = ord(s, i + k);
			if (cb == null || (cb & 0xC0) != 0x80) return null;
			c = (c << 6) | (cb & 0x3F);
		}
		push(r, c);
		i += extra + 1;
	}
	return r;
}

function cp_to_utf8(c) {
	if (c < 0x80) return chr(c);
	if (c < 0x800) return chr(0xC0 | (c >> 6)) + chr(0x80 | (c & 0x3F));
	if (c < 0x10000) return chr(0xE0 | (c >> 12)) + chr(0x80 | ((c >> 6) & 0x3F)) + chr(0x80 | (c & 0x3F));
	return chr(0xF0 | (c >> 18)) + chr(0x80 | ((c >> 12) & 0x3F)) + chr(0x80 | ((c >> 6) & 0x3F)) + chr(0x80 | (c & 0x3F));
}

function cps_to_utf8(a) {
	let s = '';
	for (let c in a) s += cp_to_utf8(c);
	return s;
}

/* code points -> GSM7 septets, or null when a character is not in the alphabet */
function cps_to_gsm7(cps) {
	let r = [];
	for (let c in cps) {
		if (gsm7_rev[c] != null) push(r, gsm7_rev[c]);
		else if (gsm7_ext_rev[c] != null) { push(r, 0x1B); push(r, gsm7_ext_rev[c]); }
		else return null;
	}
	return r;
}

/* septets -> code points; bad escapes become spaces */
function gsm7_to_cps(sept) {
	let r = [];
	for (let i = 0; i < length(sept); i++) {
		let s = sept[i];
		if (s == 0x1B && i + 1 < length(sept)) {
			let e = GSM7_EXT[sept[++i] + ''];
			push(r, e != null ? e : 0x20);
		} else push(r, GSM7[s & 0x7F]);
	}
	return r;
}

/* pack septets into bytes starting at bit offset `bits` (UDH + fill bits) */
function pack7(sept, bits) {
	let total = bits + length(sept) * 7;
	let bytes = [];
	for (let i = 0; i < int((total + 7) / 8); i++) push(bytes, 0);
	for (let i = 0; i < length(sept); i++) {
		let pos = bits + i * 7, o = pos >> 3, sh = pos & 7;
		bytes[o] = (bytes[o] | (sept[i] << sh)) & 0xFF;
		if (sh > 1) bytes[o + 1] = (bytes[o + 1] | (sept[i] >> (8 - sh))) & 0xFF;
	}
	return bytes;
}

function unpack7(bytes, bits, count) {
	let r = [];
	for (let i = 0; i < count; i++) {
		let pos = bits + i * 7, o = pos >> 3, sh = pos & 7;
		let v = (bytes[o] || 0) >> sh;
		if (sh > 1) v |= (bytes[o + 1] || 0) << (8 - sh);
		push(r, v & 0x7F);
	}
	return r;
}

function bytes_hex(b) {
	let s = '';
	for (let x in b) s += hex2(x);
	return s;
}

function hex_bytes(h) {
	if (type(h) != 'string' || length(h) % 2 || !match(h, /^[0-9A-Fa-f]*$/)) return null;
	let r = [];
	for (let i = 0; i < length(h); i += 2) push(r, hex(substr(h, i, 2)));
	return r;
}

/* UTF-16BE octets from code points */
function cps_to_ucs2(cps) {
	let r = [];
	for (let c in cps) {
		if (c >= 0x10000) {
			c -= 0x10000;
			let hi = 0xD800 | (c >> 10), lo = 0xDC00 | (c & 0x3FF);
			push(r, hi >> 8); push(r, hi & 0xFF); push(r, lo >> 8); push(r, lo & 0xFF);
		} else { push(r, c >> 8); push(r, c & 0xFF); }
	}
	return r;
}

function ucs2_to_cps(bytes) {
	let r = [];
	for (let i = 0; i + 1 < length(bytes); i += 2) {
		let u = (bytes[i] << 8) | bytes[i + 1];
		if (u >= 0xD800 && u < 0xDC00 && i + 3 < length(bytes)) {
			let lo = (bytes[i + 2] << 8) | bytes[i + 3];
			if (lo >= 0xDC00 && lo < 0xE000) { push(r, 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00)); i += 2; continue; }
		}
		push(r, u);
	}
	return r;
}

/* ---------------------------------------------------------------- encoding */

/* destination address field: length in digits, type, swapped BCD */
function encode_address(num) {
	let intl = substr(num, 0, 1) == '+';
	let d = intl ? substr(num, 1) : num;
	let out = hex2(length(d)) + (intl ? '91' : '81');
	if (length(d) % 2) d += 'F';
	for (let i = 0; i < length(d); i += 2) out += substr(d, i + 1, 1) + substr(d, i, 1);
	return out;
}

/* one SUBMIT TPDU (without the SMSC byte); udh = byte array or null */
function build_submit(num, dcs, udl, ud, udh) {
	return (udh ? '41' : '01') + '00' + encode_address(num) + '00' + hex2(dcs) + hex2(udl) + ud;
}

function encode_submit(number, text, ref) {
	if (type(number) != 'string' || !match(number, /^\+?[0-9]{3,20}$/)) return { ok: false, error: 'Invalid number format (digits only, optional leading +)' };
	if (type(text) != 'string' || !length(text)) return { ok: false, error: 'Message text is empty' };
	let cps = utf8_to_cps(text);
	if (!cps) return { ok: false, error: 'Message text is not valid UTF-8' };
	if (length(cps) > 670) return { ok: false, error: 'Message is too long (maximum 670 characters)' };
	let sept = cps_to_gsm7(cps);
	let parts = [];
	ref = (ref ?? (time() & 0xFF)) & 0xFF;
	if (sept) {
		if (length(sept) <= 160) {
			let ud = bytes_hex(pack7(sept, 0));
			push(parts, { pdu: build_submit(number, 0, length(sept), ud, null) });
		} else {
			/* concatenated: 153 septets per part, never splitting an escape pair */
			let chunks = [], cur = [];
			for (let i = 0; i < length(sept); i++) {
				let need = sept[i] == 0x1B ? 2 : 1;
				if (length(cur) + need > 153) { push(chunks, cur); cur = []; }
				push(cur, sept[i]);
				if (need == 2) push(cur, sept[++i]);
			}
			if (length(cur)) push(chunks, cur);
			for (let i = 0; i < length(chunks); i++) {
				let udh = [0x05, 0x00, 0x03, ref, length(chunks), i + 1];
				/* UDH is 6 octets = 48 bits; one fill bit aligns the first septet */
				let ud = bytes_hex(udh) + bytes_hex(slice(pack7(chunks[i], 49), 6));
				push(parts, { pdu: build_submit(number, 0, 7 + length(chunks[i]), ud, udh) });
			}
		}
	} else {
		let u = cps_to_ucs2(cps);
		if (length(u) <= 140) {
			push(parts, { pdu: build_submit(number, 8, length(u), bytes_hex(u), null) });
		} else {
			/* 134 octets per part (67 UCS-2 units), keep surrogate pairs together */
			let chunks = [], cur = [];
			for (let i = 0; i < length(u); i += 2) {
				let hi = (u[i] & 0xFC) == 0xD8;
				let need = hi ? 4 : 2;
				if (length(cur) + need > 134) { push(chunks, cur); cur = []; }
				push(cur, u[i]); push(cur, u[i + 1]);
				if (hi) { push(cur, u[i + 2]); push(cur, u[i + 3]); i += 2; }
			}
			if (length(cur)) push(chunks, cur);
			for (let i = 0; i < length(chunks); i++) {
				let udh = [0x05, 0x00, 0x03, ref, length(chunks), i + 1];
				push(parts, { pdu: build_submit(number, 8, 6 + length(chunks[i]), bytes_hex(udh) + bytes_hex(chunks[i]), udh) });
			}
		}
	}
	/* AT+CMGS length counts the TPDU only; the "00" SMSC prefix (use the modem's default) is sent but not counted */
	for (let p in parts) { p.len = length(p.pdu) / 2; p.pdu = '00' + p.pdu; }
	return { ok: true, parts: parts, alphabet: sept ? 'gsm7' : 'ucs2' };
}

/* ---------------------------------------------------------------- decoding */

function decode_bcd(bytes) {
	let s = '';
	for (let b in bytes) {
		let lo = b & 0x0F, hi = b >> 4;
		s += lo < 10 ? chr(48 + lo) : (lo == 10 ? '*' : (lo == 11 ? '#' : ''));
		s += hi < 10 ? chr(48 + hi) : (hi == 10 ? '*' : (hi == 11 ? '#' : ''));
	}
	return s;
}

/* address field at bytes[pos]; returns [address, new pos] */
function read_address(bytes, pos) {
	let digits = bytes[pos], typ = bytes[pos + 1];
	let n = int((digits + 1) / 2);
	let raw = slice(bytes, pos + 2, pos + 2 + n);
	let addr;
	if ((typ & 0x70) == 0x50) {
		/* alphanumeric sender: GSM7-packed, digits counts semi-octets */
		let sept = unpack7(raw, 0, int(digits * 4 / 7));
		addr = cps_to_utf8(gsm7_to_cps(sept));
	} else {
		addr = decode_bcd(raw);
		if (length(addr) > digits) addr = substr(addr, 0, digits);
		if ((typ & 0x70) == 0x10) addr = '+' + addr;
	}
	return [addr, pos + 2 + n];
}

function read_scts(bytes, pos) {
	let d = [];
	for (let i = 0; i < 7; i++) { let b = bytes[pos + i] || 0; push(d, (b & 0x0F) * 10 + (b >> 4)); }
	let tzb = bytes[pos + 6] || 0;
	let q = ((tzb & 0x07) * 10) + (tzb >> 4);
	let neg = (tzb & 0x08) != 0;
	let mins = q * 15;
	return sprintf('20%02d-%02d-%02d %02d:%02d:%02d %s%02d:%02d', d[0], d[1], d[2], d[3], d[4], d[5], (neg && mins) ? '-' : '+', int(mins / 60), mins % 60);
}

function decode_ud(bytes, pos, udl, dcs, has_udh) {
	let alphabet;                          /* 0 gsm7, 1 8-bit, 2 ucs2 */
	let grp = dcs >> 4;
	if (grp < 4) alphabet = (dcs >> 2) & 3;
	else if (grp == 0xE) alphabet = 2;
	else if (grp == 0xF) alphabet = (dcs & 4) ? 1 : 0;
	else if (grp >= 0xC) alphabet = 0;
	else alphabet = 0;
	if (alphabet == 3) alphabet = 0;
	let udh = null, udh_len = 0;
	if (has_udh && pos < length(bytes)) {
		udh_len = bytes[pos] + 1;
		udh = {};
		let p = pos + 1, end = pos + udh_len;
		while (p + 1 < end) {
			let iei = bytes[p], iel = bytes[p + 1];
			if (iei == 0 && iel == 3) udh.concat = { ref: bytes[p + 2], total: bytes[p + 3], seq: bytes[p + 4] };
			else if (iei == 8 && iel == 4) udh.concat = { ref: (bytes[p + 2] << 8) | bytes[p + 3], total: bytes[p + 4], seq: bytes[p + 5] };
			p += 2 + iel;
		}
	}
	let text;
	if (alphabet == 0) {
		let hdr_sept = udh_len ? int((udh_len * 8 + 6) / 7) : 0;
		let count = udl - hdr_sept;
		if (count < 0) count = 0;
		text = cps_to_utf8(gsm7_to_cps(unpack7(slice(bytes, pos), hdr_sept * 7, count)));
	} else if (alphabet == 2) {
		text = cps_to_utf8(ucs2_to_cps(slice(bytes, pos + udh_len, pos + udl)));
	} else {
		/* 8-bit data: show printable ASCII only */
		text = '';
		for (let b in slice(bytes, pos + udh_len, pos + udl)) text += (b >= 0x20 && b < 0x7F) ? chr(b) : '.';
	}
	return { text: text, alphabet: ['gsm7', '8bit', 'ucs2'][alphabet], concat: udh ? udh.concat : null };
}

function decode_pdu(h) {
	let bytes = hex_bytes(h);
	if (!bytes || length(bytes) < 8) return { type: 'invalid', error: 'Cannot parse PDU' };
	let pos = 0;
	let sca_len = bytes[pos++];
	let sca = '';
	if (sca_len) {
		let typ = bytes[pos];
		sca = decode_bcd(slice(bytes, pos + 1, pos + sca_len));
		if ((typ & 0x70) == 0x10) sca = '+' + sca;
		pos += sca_len;
	}
	let fo = bytes[pos++];
	let mti = fo & 3;
	let msg = { type: ['deliver', 'submit', 'status-report', 'reserved'][mti], smsc: sca };
	if (mti == 0) {
		let a = read_address(bytes, pos); msg.address = a[0]; pos = a[1];
		pos++;                                       /* PID */
		let dcs = bytes[pos++];
		msg.ts = read_scts(bytes, pos); pos += 7;
		let udl = bytes[pos++];
		let u = decode_ud(bytes, pos, udl, dcs, (fo & 0x40) != 0);
		msg.text = u.text; msg.alphabet = u.alphabet; msg.concat = u.concat;
	} else if (mti == 1) {
		pos++;                                       /* MR */
		let a = read_address(bytes, pos); msg.address = a[0]; pos = a[1];
		pos++;                                       /* PID */
		let dcs = bytes[pos++];
		let vpf = (fo >> 3) & 3;
		pos += vpf == 2 ? 1 : (vpf ? 7 : 0);
		let udl = bytes[pos++];
		let u = decode_ud(bytes, pos, udl, dcs, (fo & 0x40) != 0);
		msg.text = u.text; msg.alphabet = u.alphabet; msg.concat = u.concat;
	} else if (mti == 2) {
		pos++;                                       /* MR */
		let a = read_address(bytes, pos); msg.address = a[0]; pos = a[1];
		msg.ts = read_scts(bytes, pos); pos += 7;
		msg.text = '(delivery report)';
	}
	return msg;
}

/* "+CMGL: 3,1,,24" followed by the PDU line */
function parse_cmgl(lines) {
	let r = [];
	for (let i = 0; i < length(lines); i++) {
		let mm = match(lines[i], /^\+CMGL: *([0-9]+),([0-9]+)/);
		if (!mm) continue;
		let pdu = lines[i + 1];
		if (type(pdu) == 'string' && match(pdu, /^[0-9A-Fa-f]+$/)) { push(r, { index: int(mm[1]), stat: int(mm[2]), pdu: pdu }); i++; }
	}
	return r;
}

const STAT = ['Unread', 'Read', 'Unsent', 'Sent', 'All'];

/* decode every PDU, then join concatenated parts that belong together */
function merge(raw) {
	let out = [], groups = {};
	for (let r in raw) {
		let d = decode_pdu(r.pdu);
		d.index = r.index; d.stat = r.stat; d.stat_text = STAT[r.stat] || '';
		if (d.concat && d.concat.total > 1) {
			let key = (d.address || '') + '/' + d.concat.ref + '/' + d.concat.total + '/' + d.type;
			let g = groups[key];
			if (!g) {
				g = groups[key] = { type: d.type, address: d.address, ts: d.ts, stat: d.stat, stat_text: d.stat_text, alphabet: d.alphabet,
					parts: {}, indexes: [], total: d.concat.total };
				push(out, g);
			}
			g.parts[d.concat.seq] = d.text;
			push(g.indexes, d.index);
			if (d.ts && d.ts < g.ts) g.ts = d.ts;
		} else {
			d.indexes = [d.index];
			push(out, d);
		}
	}
	for (let g in out) {
		if (g.parts) {
			let t = '', miss = 0;
			for (let i = 1; i <= g.total; i++) { if (g.parts[i] != null) t += g.parts[i]; else { t += '…'; miss++; } }
			g.text = t; g.complete = miss == 0; g.index = g.indexes[0];
			delete g.parts;
		} else g.complete = true;
		delete g.concat;
	}
	return out;
}

/* ---------------------------------------------------------------- modem operations */

/* newest first */
function list(storage) {
	let r = m.at('AT+CMGF=0', 4000);
	if (!r.ok) return { ok: false, error: sprintf('Cannot switch to PDU mode: %s', r.status || r.error) };
	let cap = null;
	if (storage) {
		if (!match(storage, /^(SM|ME|MT)$/)) return { ok: false, error: 'Invalid storage' };
		let s = m.at('AT+CPMS="' + storage + '","' + storage + '","' + storage + '"', 8000);
		if (!s.ok) return { ok: false, error: sprintf('Cannot select SMS storage %s: %s', storage, s.status) };
	}
	let c = m.at('AT+CPMS?', 4000);
	if (c.ok) for (let l in c.lines) {
		let mm = match(l, /^\+CPMS: *"([A-Z_]+)",([0-9]+),([0-9]+)/);
		if (mm) cap = { storage: mm[1], used: int(mm[2]), total: int(mm[3]) };
	}
	let g = m.at('AT+CMGL=4', 30000);
	if (!g.ok) return { ok: false, error: sprintf('Failed to read SMS: %s', g.status || g.error), capacity: cap };
	let msgs = merge(parse_cmgl(g.lines));
	sort(msgs, (a, b) => (a.ts < b.ts) ? 1 : (a.ts > b.ts ? -1 : 0));
	return { ok: true, messages: msgs, capacity: cap };
}

function send(number, text) {
	let enc = encode_submit(number, text);
	if (!enc.ok) return enc;
	let r = m.at('AT+CMGF=0', 4000);
	if (!r.ok) return { ok: false, error: 'Cannot switch to PDU mode' };
	let sent = 0, last = null;
	for (let p in enc.parts) {
		last = m.sms_send(p.len, p.pdu, 60000);
		if (!last.ok) return { ok: false, error: sprintf('Send failed (part %s/%s): %s', sent + 1, length(enc.parts), last.status || last.error), sent: sent, parts: length(enc.parts), lines: last.lines };
		sent++;
	}
	return { ok: true, parts: sent, alphabet: enc.alphabet, lines: last ? last.lines : [] };
}

function remove(indexes) {
	if (type(indexes) != 'array' || !length(indexes) || length(indexes) > 40) return { ok: false, error: 'Invalid index' };
	let done = 0;
	for (let i in indexes) {
		if (type(i) == 'string' && match(i, /^[0-9]+$/)) i = int(i);
		if (type(i) != 'int' || i < 0 || i > 4096) return { ok: false, error: 'Invalid index', deleted: done };
		let r = m.at('AT+CMGD=' + i, 8000);
		if (!r.ok) return { ok: false, error: sprintf('Delete failed: %s', r.status || r.error), deleted: done };
		done++;
	}
	return { ok: true, deleted: done };
}

/* ---------------------------------------------------------------- USSD */

function ussd_decode(text, dcs) {
	if (type(text) != 'string') return '';
	if (type(dcs) == 'int' && ((dcs & 0xF0) == 0x70 || (dcs & 0x0C) == 0x08) && match(text, /^[0-9A-Fa-f]+$/) && length(text) % 4 == 0) {
		let b = hex_bytes(text);
		if (b) return cps_to_utf8(ucs2_to_cps(b));
	}
	return text;
}

/* pull a "+CUSD: n,"text",dcs" line out of the URC lines newer than `since` */
function ussd_parse(urc, since) {
	let found = null;
	for (let u in urc) {
		if (u.t < since) continue;
		let mm = match(u.line, /^\+CUSD: *([0-9]),"(.*)"(,([0-9]+))?$/);
		if (mm) found = { n: int(mm[1]), text: ussd_decode(mm[2], mm[4] != null ? int(mm[4]) : null), raw: mm[2] };
		else if ((mm = match(u.line, /^\+CUSD: *([0-9])$/))) found = { n: int(mm[1]), text: '' };
	}
	return found;
}

const USSD_N = ['No further action needed', 'Reply needed', 'Network ended the session', 'Another client responded', 'Operation not supported', 'Network timeout'];

function ussd(code, cancel) {
	if (cancel) { let r = m.at('AT+CUSD=2', 8000); return { ok: r.ok, status: r.status }; }
	if (type(code) != 'string' || !match(code, /^[0-9*#+]{1,40}$/)) return { ok: false, error: 'Invalid USSD code (only digits, * # + allowed)' };
	let t0 = time();
	let r = m.at('AT+CUSD=1,"' + code + '",15', 20000);
	if (!r.ok) return { ok: false, error: sprintf('USSD request rejected: %s', r.status || r.error) };
	/* the answer arrives as an unsolicited +CUSD line, possibly some seconds later */
	let ans = null;
	for (let i = 0; i < 30 && !ans; i++) {
		ans = ussd_parse(m.urc_log().urc || [], t0);
		if (!ans) system('sleep 1');
	}
	if (!ans) return { ok: false, error: 'Timed out waiting for the network reply (30 seconds)' };
	return { ok: true, n: ans.n, state: USSD_N[ans.n] || '', text: ans.text };
}

export { utf8_to_cps, cps_to_utf8, cps_to_gsm7, gsm7_to_cps, pack7, unpack7, bytes_hex, hex_bytes, encode_submit, decode_pdu, parse_cmgl, merge, list, send, remove, ussd_decode, ussd_parse, ussd };
