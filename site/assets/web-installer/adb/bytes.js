// Byte helpers shared by the adb client. Everything is Uint8Array; nothing here touches the DOM.
const enc = new TextEncoder();
const dec = new TextDecoder("utf-8", { fatal: false });

export const utf8 = (s) => enc.encode(s);
export const text = (b) => dec.decode(b);

export function concat(...parts) {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export function u32le(v) {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setUint32(0, v >>> 0, true);
  return b;
}

export function readU32le(b, off = 0) {
  return new DataView(b.buffer, b.byteOffset, b.byteLength).getUint32(off, true);
}

export function hex(b) {
  let s = "";
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, "0");
  return s;
}

export function base64(b) {
  let s = "";
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return btoa(s);
}

export function base64urlToBytes(s) {
  const t = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  const out = new Uint8Array(t.length);
  for (let i = 0; i < t.length; i++) out[i] = t.charCodeAt(i);
  return out;
}

export function bytesToBigInt(b) {  // big endian
  const h = hex(b);
  return h ? BigInt("0x" + h) : 0n;
}

export function bigIntToBytes(v, length, littleEndian = false) {  // fixed length; throws when v does not fit
  const out = new Uint8Array(length);
  for (let i = 0; i < length; i++) {
    out[littleEndian ? i : length - 1 - i] = Number(v & 0xffn);
    v >>= 8n;
  }
  if (v !== 0n) throw new RangeError("value does not fit");
  return out;
}

// Splits a stream of text chunks into lines (LF, with a CR before it dropped: su may run a command on a pty, and
// the tty layer then turns every LF into CRLF). feed() returns the complete lines; flush() the rest.
export class LineSplitter {
  constructor() { this.rest = ""; this.dec = new TextDecoder("utf-8", { fatal: false }); }
  feed(chunk) {
    this.rest += typeof chunk === "string" ? chunk : this.dec.decode(chunk, { stream: true });
    const lines = this.rest.split("\n");
    this.rest = lines.pop();
    return lines.map((l) => l.replace(/\r+$/, ""));
  }
  flush() {
    const r = this.rest.replace(/\r+$/, "");
    this.rest = "";
    return r ? [r] : [];
  }
}
